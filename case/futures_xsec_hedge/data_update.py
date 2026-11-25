# -*- coding: utf-8 -*-
"""行情增量更新
===============
- incremental_fetch(): 抓取最近 ~14 个月活跃合约日线（覆盖增量+复牌）
- apply_kline_merge(): 与 data/full/all_kline.csv 按 (variety, contract, date) 合并去重
取数口径：DataHub 期货模块 data_hub.collectors.futures.get_futures_contract_daily，
          不再直连新浪 InnerFuturesNewService。
          注意 DataHub 合约日线字段为 trade_date/open/high/low/close/vol/oi，**没有 settle**，
          本文件保留 settle 列但其值恒为空（下游未使用该列）。
"""
import json
import re
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
KLINE_FILE = ROOT / "data" / "full" / "all_kline.csv"

# ---- DataHub 期货模块（取数统一入口）----
HUB_ROOT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier"
HUB_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps"
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

KLINE_COLS = ["variety", "contract", "date", "open", "high", "low", "close",
              "volume", "oi", "settle"]

UNIVERSE = ["MA", "TA", "SA", "UR", "V", "PP", "L", "EG", "EB",
            "M", "RM", "Y", "P", "OI", "SR", "CF", "C", "AP", "JD"]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
      "Referer": "https://finance.sina.com.cn"}


def active_contracts(ref: date = None, months_back: int = 14, months_fwd: int = 4):
    """最近 months_back 月至未来 months_fwd 月的全部候选合约（覆盖主力+近远月）"""
    ref = ref or date.today()
    out = []
    y, m = ref.year, ref.month
    for _ in range(months_back):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    y, m = ref.year, ref.month
    for _ in range(1, months_fwd + 1):
        m += 1
        if m == 13:
            m = 1
            y += 1
        out.append((y, m))
    return out


def fetch_contract(sym: str, code: str, datalen: int = 800, retry: int = 2):
    """单合约日线：DataHub 期货模块（data_hub.collectors.futures）。

    DataHub 返回 trade_date/open/high/low/close/vol/oi，无 settle；本函数保留 settle 列
    以维持既有 CSV schema，值恒为空（下游未使用）。
    """
    from data_hub.collectors.futures import get_futures_contract_daily

    end_d = date.today().strftime("%Y%m%d")
    # datalen 为「交易日根数」，按自然日放宽 1.6 倍覆盖节假日
    start_d = (date.today() - timedelta(days=int(datalen * 1.6))).strftime("%Y%m%d")
    for i in range(max(1, retry)):
        try:
            df = get_futures_contract_daily(contract=sym, start_date=start_d, end_date=end_d)
            if df is None or df.empty:
                raise RuntimeError("DataHub 返回空")
            d8 = df.get("trade_date", df.get("date")).astype(str).str.replace("-", "")
            out = pd.DataFrame({
                "date": pd.to_datetime(d8, format="%Y%m%d", errors="coerce"),
                "open": pd.to_numeric(df.get("open"), errors="coerce"),
                "high": pd.to_numeric(df.get("high"), errors="coerce"),
                "low": pd.to_numeric(df.get("low"), errors="coerce"),
                "close": pd.to_numeric(df.get("close"), errors="coerce"),
                "volume": pd.to_numeric(
                    df["vol"] if "vol" in df.columns else df.get("volume"), errors="coerce"),
                "oi": pd.to_numeric(df.get("oi"), errors="coerce"),
                "settle": pd.NA,
            }).dropna(subset=["date", "close"])
            if out.empty:
                raise RuntimeError("解析后为空")
            out["contract"] = sym
            out["variety"] = code
            return out[KLINE_COLS].sort_values("date").reset_index(drop=True)
        except Exception:
            if i < max(1, retry) - 1:
                time.sleep(0.8 + i)
    return None


def incremental_fetch(datalen: int = 800):
    """抓取活跃合约，返回合并后的 DataFrame（含 variety/contract/date 列）"""
    pairs = [(f"{code}{yy % 100}{mm:02d}", code) for code in UNIVERSE for (yy, mm) in active_contracts()]
    # 去重（合约名可能跨年重复？不会——contract 名含 yy）
    seen = set()
    work = []
    for sym, code in pairs:
        if sym not in seen:
            seen.add(sym)
            work.append((sym, code))
    bufs = {c: [] for c in UNIVERSE}
    n_ok = {c: 0 for c in UNIVERSE}
    with ThreadPoolExecutor(max_workers=3) as exe:  # DataHub 全局串行限流，降并发避免 IP 超限
        futs = {exe.submit(fetch_contract, sym, code, datalen): (sym, code) for sym, code in work}
        for fut in as_completed(futs):
            sym, code = futs[fut]
            df = fut.result()
            if df is None or df.empty:
                continue
            bufs[code].append(df)
            n_ok[code] += 1
    parts = []
    for c in UNIVERSE:
        if bufs[c]:
            parts.append(pd.concat(bufs[c], ignore_index=True))
        print(f"{c}: contracts={n_ok[c]}", flush=True)
    if not parts:
        raise RuntimeError("行情抓取失败：全部合约无数据")
    k = pd.concat(parts, ignore_index=True)
    k["date"] = pd.to_datetime(k["date"])
    return k


def apply_kline_merge(new_k: pd.DataFrame):
    """与 all_kline.csv 合并去重并落盘"""
    if KLINE_FILE.exists():
        old = pd.read_csv(KLINE_FILE, parse_dates=["date"])
        merged = pd.concat([old, new_k], ignore_index=True)
    else:
        merged = new_k
    merged = merged.drop_duplicates(subset=["variety", "contract", "date"], keep="last")
    merged = merged.sort_values(["variety", "contract", "date"]).reset_index(drop=True)
    # 剔除未来日期（接口偶发返回异常数据）
    merged = merged[merged["date"] <= pd.Timestamp.now().normalize()]
    merged.to_csv(KLINE_FILE, index=False)
    print(f"all_kline.csv 更新完成：{len(merged)} 行，最新日期 {merged['date'].max().date()}")
    return merged


if __name__ == "__main__":
    k = incremental_fetch()
    apply_kline_merge(k)