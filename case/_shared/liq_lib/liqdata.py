#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liqdata —— 流动性/信用周报的取数层（DataHub 唯一来源 + 本地滚动归档）

为什么不能简单「把 market.db 换成 DataHub」
==========================================
原实现读宿主机的 ``data/market.db``（采集器 ``bin/marketdb_fetch.py`` 自己写）：

    cn.repo.R007 / cn.cb.govt.10Y / us.fred.BAMLH0A0HYM2 … 共 268 条长表

这套报告的分位数逻辑要 **近 252 个交易日**（``pct()`` 里 ``v[-252:]``、
``sp_pct()`` 里 ``w = v[-252:] if len(v) >= 252 else v``）。而 DataHub EDB 的
保留策略（大佬 2026-11-19 定：各模块 60 天）意味着：

    · 今天第一次跑 → 能拿到全历史（785 行 US HY OAS），落库只留 60 天
    · 60 天后再跑 → 库里只剩 60 个点，分位窗口塌成 60 日

分位窗口从 252 塌到 60，会让「≥95% 分位」这类阈值彻底失去含义（60 天里
总有 3 天在 95% 以上）。这是个必须正面处理的差异，不能假装没发生。

处理办法（两件事，都很轻）
==========================
1. **取数只走 DataHub**：``edb.get_series`` / ``edb.get_dataset_result`` /
   ``get_etf_net_flow``，一行业务代码都不直连 akshare / tushare / FRED。
2. **任务侧留一份滚动归档** ``data/liq_archive/<series>.jsonl``：
   每次 fetch 把当天取到的新点 append 进去（同日期覆盖）。这样窗口随运行次数
   逐渐长回来，60 天后重新回到 200+ 日。归档是本任务私有的**跨天保留状态**，
   属 data/ 但被 check 排除（见 task.json artifacts），README 有清单。

⚠️ 归档不是「第二数据源」：它只存 DataHub 给过的值，从不自己抓、不插值、不补零。
   DataHub 当天没给的点，归档里也没有。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.dirname(HERE)
_SHARED = os.path.join(LIB, "wide_ens_lib")
for _p in (LIB, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _hub():
    from datahub_fetch import hub             # noqa: PLC0415
    return hub()


def _data_dir() -> str:
    d = (os.environ.get("CASE_DATA_DIR") or "").strip()
    if d:
        return d
    return os.path.join(os.path.dirname(LIB), "liq_credit_weekly", "data")


# ── 归档 ──────────────────────────────────────────────────────────────────
def _arch_dir() -> str:
    p = os.path.join(_data_dir(), "liq_archive")
    os.makedirs(p, exist_ok=True)
    return p


def _arch_path(key: str) -> str:
    safe = key.replace("/", "_").replace(".", "_")
    return os.path.join(_arch_dir(), "%s.jsonl" % safe)


def load_archive(key: str) -> Dict[str, float]:
    p = _arch_path(key)
    out = {}
    if not os.path.isfile(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                d, v = rec.get("d"), rec.get("v")
                if d and v is not None:
                    out[str(d)] = float(v)
    except OSError:
        return {}
    return out


def save_archive(key: str, series: Dict[str, float]) -> int:
    """整份重写（去重 + 排序），返回条数。原子替换，避免半截文件。"""
    p = _arch_path(key)
    tmp = p + ".tmp"
    items = sorted((d, v) for d, v in series.items() if v is not None)
    with open(tmp, "w", encoding="utf-8") as f:
        for d, v in items:
            f.write(json.dumps({"d": d, "v": v}, ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    return len(items)


def merged_series(key: str, incoming: Optional[pd.Series] = None,
                  min_rows: int = 0) -> List[Tuple[str, float]]:
    """归档 ∪ 本次 DataHub 值 → 升序 [(date, value)]，并回写归档。

    incoming 为 None 时只读归档（离线/调试用）。
    """
    arch = load_archive(key)
    n_new = 0
    if incoming is not None and len(incoming):
        for d, v in zip(incoming.index, incoming.to_numpy()):
            if v is None or pd.isna(v):
                continue
            ds = str(pd.Timestamp(d).date())
            if ds not in arch:
                n_new += 1
            arch[ds] = float(v)
    if n_new:
        save_archive(key, arch)
    rows = sorted(arch.items())
    if min_rows and len(rows) < min_rows:
        pass                      # 不编造；调用方按实际长度算分位
    return rows


# ── DataHub 指标映射 ──────────────────────────────────────────────────────
# key = 原 market.db 的 series_id（报告里到处用这个名字），value = DataHub 的取法。
# "edb": EDB 直接取该 code；"derive": 由同 dict 里其它 key 现算；None = 缺（不编造）
EDB_MAP: Dict[str, str] = {
    # ── 中国债券曲线 ──
    "cn.cb.govt.10Y": "RATE.CN.10Y",
    "cn.cb.govt.2Y": "RATE.CN.2Y",
    "cn.cb.govt.1Y": "RATE.CN.1Y",
    "cn.cb.govt.30Y": "RATE.CN.30Y",
    "cn.cb.ncd_aaa.1Y": "BOND.CN.NCD.AAA.1Y",
    # ── 中国流动性 ──
    "cn.lpr.1y": "LIQ.CN.LPR.1Y",
    "cn.pbc.omo.amount": "LIQ.CN.OMO.INJECT",
    "cn.pbc.omo.net": "LIQ.CN.OMO.NET",
    "cn.pbc.omo.rate": "LIQ.CN.OMO.POLICY",
    "cn.repo.FDR001": "LIQ.CN.REPO.FR001",
    "cn.repo.FDR007": "LIQ.CN.REPO.FR007",
    "cn.repo.R007": "LIQ.CN.R007",
    "cn.repo.vol_total": "LIQ.CN.REPO.VOL.R007",
    # ── 中国宏观 ──
    "cn.macro.m1_yoy": "CN.M1.YOY",
    "cn.macro.m2_yoy": "CN.M2.YOY",
    "cn.macro.m1_m2_gap": "CN.M1M2.SCISSOR",
    # ── 中国股市两融 ──
    "cn.eq.margin_balance": "CN.EQ.MARGIN.BALANCE",
    "cn.eq.margin_buy": "CN.EQ.MARGIN.BUY",
    # ── 美国信用 ──
    "us.fred.BAMLH0A0HYM2": "CREDIT.US.OAS.HY",
    "us.fred.BAMLH0A1HYBB": "CREDIT.US.OAS.BB",
    "us.fred.BAMLH0A3HYC": "CREDIT.US.OAS.CCC",
    "us.fred.BAMLC0A0CM": "CREDIT.US.OAS.IG",
    "us.fred.BAMLC0A4CBBB": "CREDIT.US.OAS.BBB",
    # ── 美国利率/流动性 ──
    "us.fred.DGS10": "RATE.US.10Y",
    "us.fred.DGS2": "RATE.US.2Y",
    "us.ust.30Y": "RATE.US.30Y",
    "us.fred.IORB": "US.IORB",
    "us.fred.SOFR": "LIQ.US.SOFR",
    "us.fred.RRPONTSYD": "LIQ.US.RRP",
    "us.fred.WRESBAL": "LIQ.US.RESERVES",
    "us.fred.WTREGEN": "LIQ.US.TGA",
    # ── 美国风险资产 ──
    "us.fred.VIXCLS": "GLOBAL.VIX",
    "us.fred.SP500": "GLOBAL.SP500",
    "us.fred.NASDAQCOM": "GLOBAL.NASDAQ100",
}

# DataHub 当前没有、且不是简单派生的（不编造，报告里标「本周期暂缺」）
MISSING_BASE = (
    "cn.cb.govt.3Y", "cn.cb.govt.0.083Y", "cn.cb.mtn_aaa.3Y",
    "cn.repo.vol_on_share", "cn.eq.new_fund_week", "cn.eq.north_flow",
    "cn.eq.holder_net_reduce", "cn.eq.amt_total", "cn.eq.margin_amt_share",
    "cn.eq.margin_net",
)


def _unit_scale(key: str) -> float:
    """market.db 的口径换算：美债/信用利差存 %，报告里 ×100 当 bp 用。

    原报告对 ``us.fred.BAMLH0A3HYC`` 等做 ``cccv = ccc * 100``，说明库里是 %。
    DataHub CREDIT.US.OAS.* 同样是 %，单位一致，这里恒等返回；
    保留这个函数是为了把「单位在哪换算」这件事写死在代码里，别散在调用点。
    """
    return 1.0


def load_all(keys: Optional[List[str]] = None, want_etf: bool = True) -> dict:
    """取齐周报需要的全部序列 → {key: [(date, value), ...]}（升序）。

    返回的 dict 还会带两个元信息键（下划线开头，调用方注意跳过）：
        _missing  DataHub 里没有、本次也没归档的 key 列表
        _archive  每个 key 的归档条数（用于报告注明「分位窗口多长」）
    """
    dh = _hub()
    keys = keys or list(EDB_MAP)
    out: Dict[str, List[Tuple[str, float]]] = {}
    missing: List[str] = []
    arc_len: Dict[str, int] = {}

    for i, key in enumerate(keys):
        code = EDB_MAP.get(key)
        if not code:
            if key not in MISSING_BASE:
                missing.append(key)
            continue
        s = None
        try:
            df = dh.edb.get_series(code)
            if df is not None and len(df):
                s = pd.Series(df["value"].to_numpy(dtype="float64"),
                              index=pd.DatetimeIndex(pd.to_datetime(df["date"])))
                s = s[~s.index.duplicated(keep="last")].sort_index() * _unit_scale(key)
        except Exception as e:                                # noqa: BLE001
            print("  [edb] %-32s 取数失败：%s" % (code, str(e)[:80]))
        rows = merged_series(key, s)
        if rows:
            out[key] = rows
            arc_len[key] = len(rows)
        else:
            missing.append(key)
        if (i + 1) % 8 == 0:
            print("  已取 %d/%d" % (i + 1, len(keys)))

    # ── 派生指标（由上面已取的序列现算） ──
    out.update(_derive(out))
    for k, v in out.items():
        if not k.startswith("_") and k not in arc_len:
            arc_len[k] = len(v)

    # ── ETF 净申赎（DataHub 采集器，单位亿元） ──
    if want_etf:
        rows = _etf_net_flow(dh)
        if rows:
            out["cn.eq.etf_net_flow"] = rows
            arc_len["cn.eq.etf_net_flow"] = len(rows)
        else:
            missing.append("cn.eq.etf_net_flow")

    out["_missing"] = sorted(set(missing))
    out["_archive"] = arc_len
    return out


def _derive(base: Dict[str, List[Tuple[str, float]]]) -> Dict[str, List[Tuple[str, float]]]:
    """派生序列：只做「两个已有序列相减/相除」，绝不自造趋势。"""
    out: Dict[str, List[Tuple[str, float]]] = {}

    def _align(a_key, b_key):
        A = dict(base.get(a_key) or [])
        B = dict(base.get(b_key) or [])
        if not A or not B:
            return None
        common = sorted(set(A) & set(B))
        if not common:
            return None
        return [(d, A[d] - B[d]) for d in common]

    # R007 - DR007（非银流动性分层，bp；库里两个都是 %，×100 换 bp）
    A = dict(base.get("cn.repo.R007") or [])
    B = dict(base.get("cn.repo.DR007") or [])
    if A and B:
        common = sorted(set(A) & set(B))
        if common:
            out["cn.repo.r_dr_spread"] = [(d, (A[d] - B[d]) * 100) for d in common]

    # 美国派生利差（bp）
    d = _align("us.fred.BAMLH0A3HYC", "us.fred.BAMLH0A1HYBB")
    if d:
        out["us.derived.ccc_bb_spread"] = [(x, v * 100) for x, v in d]
    d = _align("us.fred.BAMLH0A1HYBB", "us.fred.BAMLC0A4CBBB")
    if d:
        out["us.derived.bb_b_spread"] = [(x, v * 100) for x, v in d]
    d = _align("us.fred.BAMLH0A2HYB", "us.fred.BAMLH0A1HYBB")
    if d:
        out["us.derived.b_b_spread"] = [(x, v * 100) for x, v in d]

    # SOFR - IORB（回购脱离区间，bp）
    d = _align("us.fred.SOFR", "us.fred.IORB")
    if d:
        out["us.derived.sofr_iorb_spread"] = [(x, v * 100) for x, v in d]

    return out


def _etf_net_flow(dh) -> List[Tuple[str, float]]:
    """宽基 ETF 净申赎（亿元）。DataHub 采集器已把「份额×价格」算好，直接用。"""
    try:
        df = dh.aggregate_etf_net_flow(lookback_days=90)
    except Exception as e:                                    # noqa: BLE001
        print("  [etf] aggregate_etf_net_flow 失败：%s" % str(e)[:120])
        return []
    if df is None or len(df) == 0:
        return []
    col = None
    for c in ("net_flow_yi", "net_yi", "net_flow"):
        if c in df.columns:
            col = c
            break
    if col is None:
        print("  [etf] 返回列里没有净流入字段：%s" % list(df.columns)[:8])
        return []
    dcol = "trade_date" if "trade_date" in df.columns else df.columns[0]
    g = df.groupby(dcol)[col].sum().sort_index()
    key = "cn.eq.etf_net_flow"
    s = pd.Series(g.to_numpy(dtype="float64"),
                  index=pd.DatetimeIndex(pd.to_datetime(g.index.astype(str))))
    return merged_series(key, s)


if __name__ == "__main__":
    import time
    t0 = time.time()
    data = load_all()
    real = {k: v for k, v in data.items() if not k.startswith("_")}
    print("序列 %d 条，用时 %.1fs" % (len(real), time.time() - t0))
    for k in sorted(real):
        v = real[k]
        print("  %-32s %4d 点  %s ~ %s  末值 %s"
              % (k, len(v), v[0][0], v[-1][0], v[-1][1]))
    print("缺：", ", ".join(data["_missing"]) or "无")
