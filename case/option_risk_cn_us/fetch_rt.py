# -*- coding: utf-8 -*-
"""A股盘中实时快照取数（替代 EOD 取数）

背景：主链路每交易日 14:30 运行，此时 A 股尚未收盘，TuShare 的日终数据
（opt_daily 期权链、fund_daily/index_daily 收盘价、结算价）与第三方 QVIX 日线
均未生成。本模块改为从行情网页/交易所实时报价取当日盘中快照，并把结果写成
与 EOD 同构的落盘文件，使下游 compute_*/analyze/judge/skew/gex 无需改动即可复用。

用法:
    python fetch_rt.py spot      # 标的实时价 -> data/underlying_hist.json / data/spot.json
    python fetch_rt.py chains    # 期权实时链 -> data/chains/<TRADE_DATE>.json
    python fetch_rt.py futures   # IM 期指实时 -> data/fut_im_month_hist.json
    python fetch_rt.py all

环境变量:
    TRADE_DATE=YYYYMMDD   当日数据日（主链路注入）

数据源:
    · 标的实时价：新浪财经 https://hq.sinajs.cn/list=...
    · 期权/期货实时：东方财富 push2 https://push2.eastmoney.com/api/qt/clist/get
        - 沪市 ETF 期权   fs=m:10
        - 深市 ETF 期权   fs=m:12
        - 中金所 中证1000 股指期权 fs=m:221+t:2
        - 中金所 期货     fs=m:8
      东财合约代码与 TuShare ts_code 可直接拼接（+ .SH/.SZ/.CFX）作为连接键。
"""
import json
import os
import subprocess
import sys
import time

import pandas as pd

import lib_adapter as LIB  # 取数走 DataHub（用户口径 2026-11-13）

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CH = os.path.join(DATA, "chains")
os.makedirs(CH, exist_ok=True)

TRADE_DATE = os.environ.get("TRADE_DATE", "")  # YYYYMMDD
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
SINA_REF = "https://finance.sina.com.cn"

# 标的池：与 fetch_ashare2.mode_underlying 完全一致，保证 underlying_hist 结构不变
ETFS = ["510300.SH", "510500.SH", "588000.SH", "159915.SZ", "159919.SZ", "159922.SZ"]
IDX = ["000300.SH", "000905.SH", "000852.SH", "000688.SH", "399006.SZ"]
SINA_CODE = {
    "510300.SH": "sh510300", "510500.SH": "sh510500", "588000.SH": "sh588000",
    "159915.SZ": "sz159915", "159919.SZ": "sz159919", "159922.SZ": "sz159922",
    "000300.SH": "sh000300", "000905.SH": "sh000905", "000852.SH": "sh000852",
    "000688.SH": "sh000688", "399006.SZ": "sz399006",
}
KIND = {c: ("etf" if c in ETFS else "index") for c in ETFS + IDX}

CHAIN_BOARDS = [("SSE", "m:10", ".SH"), ("SZSE", "m:12", ".SZ"),
                ("CFFEX", "m:221+t:2", ".CFX")]


def curl(url, ref=None, timeout=25):
    cmd = ["curl", "-s", "-m", str(timeout), "-A", UA]
    if ref:
        cmd += ["-H", "Referer: %s" % ref]
    return subprocess.run(cmd + [url], capture_output=True).stdout


def fnum(x):
    """东财/新浪字段安全转数值；'-'、''、None 均返回 None。"""
    if x is None:
        return None
    try:
        v = float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def em_page(pn, fs, fields):
    """单页请求，空响应重试。"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
           "&fltt=2&invt=2&fid=f2&fs=%s&fields=%s" % (pn, fs, fields))
    for _ in range(6):
        out = curl(url)
        if out:
            try:
                d = json.loads(out)["data"]
                if d and d.get("diff"):
                    return d
            except Exception:
                pass
        time.sleep(1.2)
    return None


def em_list(fs, fields, max_pages=20):
    rows, total, pn = [], None, 1
    while pn <= max_pages:
        d = em_page(pn, fs, fields)
        if d is None:
            break
        total = d.get("total")
        rows += d["diff"]
        if total and len(rows) >= int(total):
            break
        pn += 1
        time.sleep(0.4)
    return rows, total


def load_basics():
    frames = []
    for f in ["basic_sse.json", "basic_szse.json", "basic_cffex.json", "basic_extra.json"]:
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            frames.append(pd.read_json(p, orient="records", dtype={"ts_code": str}))
    if not frames:
        return {}
    basics = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    return basics.set_index("ts_code")[["opt_code", "call_put", "exercise_price",
                                        "maturity_date", "per_unit"]].to_dict("index")


# ------------------------------------------------------------------ spot
def mode_spot():
    codes = list(SINA_CODE.values())
    raw = curl("https://hq.sinajs.cn/list=" + ",".join(codes), ref=SINA_REF)
    if not raw:
        raise SystemExit("fetch_rt spot: 新浪实时行情无响应")
    txt = raw.decode("gbk", errors="replace")
    got = {}
    for line in txt.strip().split(";"):
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().replace("var hq_str_", "")
        parts = line.split("=", 1)[1].strip().strip('"').split(",")
        if len(parts) < 6 or not parts[3]:
            continue
        cur = fnum(parts[3])
        if cur is None or cur <= 0:
            continue
        got[key] = cur
    rows, spot = [], []
    D = TRADE_DATE
    for code, sc in SINA_CODE.items():
        cur = got.get(sc)
        if cur is None:
            print("  miss", code, sc)
            continue
        rows.append(dict(code=code, kind=KIND[code], trade_date=D, close=float(cur)))
        spot.append(dict(code=code, close=float(cur), trade_date=D))
    if not rows:
        raise SystemExit("fetch_rt spot: 未取到任何标的实时价")
    p = os.path.join(DATA, "underlying_hist.json")
    uh = pd.read_json(p, orient="records") if os.path.exists(p) else pd.DataFrame(
        columns=["code", "kind", "trade_date", "close"])
    uh["trade_date"] = uh["trade_date"].astype(str)
    uh = uh[uh["trade_date"] != str(D)]
    uh = pd.concat([uh, pd.DataFrame(rows)], ignore_index=True)
    uh["trade_date"] = uh["trade_date"].astype(str)
    uh = uh.sort_values(["code", "trade_date"])
    uh.to_json(p, orient="records", force_ascii=False)
    pd.DataFrame(spot).to_json(os.path.join(DATA, "spot.json"), orient="records",
                               force_ascii=False)
    print("saved underlying_hist.json / spot.json  (盘中实时 %s, %d 个标的)"
          % (D, len(rows)))
    for r in rows:
        print("  %-11s %s %s" % (r["code"], r["kind"], r["close"]))


# ------------------------------------------------------------------ chains
def mode_chains():
    bmap = load_basics()
    if not bmap:
        raise SystemExit("fetch_rt chains: 缺少 basic_*.json 合约基础信息")
    fields = "f12,f14,f2,f5,f108"
    recs = []
    for name, fs, suf in CHAIN_BOARDS:
        rows, total = em_list(fs, fields)
        kept = 0
        for x in rows:
            ts = str(x.get("f12") or "") + suf
            if ts not in bmap:
                continue
            px = fnum(x.get("f2"))
            if px is None or px <= 0:
                continue
            recs.append(dict(ts_code=ts, trade_date=TRADE_DATE,
                             exchange=("CFFEX" if suf == ".CFX" else name),
                             close=float(px), settle=None,
                             vol=fnum(x.get("f5")), oi=fnum(x.get("f108"))))
            kept += 1
        print("  %-6s %-12s total=%s 匹配=%d" % (name, fs, total, kept))
    if not recs:
        raise SystemExit("fetch_rt chains: 未取到任何期权实时报价")
    df = pd.DataFrame(recs)
    df = df.drop_duplicates("ts_code")
    df.to_json(os.path.join(CH, str(TRADE_DATE) + ".json"), orient="records",
               force_ascii=False)
    bys = df.groupby("exchange")["ts_code"].count().to_dict()
    print("saved chains/%s.json  (%d 条实时合约; %s)" % (TRADE_DATE, len(df), bys))


# ------------------------------------------------------------------ futures
def mode_futures():
    rows, total = em_list("m:8", "f12,f14,f2,f5,f108")
    recs = []
    for x in rows:
        nm = str(x.get("f14") or "")
        if not (len(nm) == 6 and nm.startswith("IM") and nm[2:].isdigit()):
            continue
        px = fnum(x.get("f2"))
        if px is None or px <= 0:
            continue
        recs.append(dict(code=nm + ".CFX", trade_date=TRADE_DATE, close=float(px),
                         settle=float(px), vol=fnum(x.get("f5")), oi=fnum(x.get("f108"))))
    if not recs:
        raise SystemExit("fetch_rt futures: 未取到 IM 期指实时报价")
    p = os.path.join(DATA, "fut_im_month_hist.json")
    h = pd.read_json(p, orient="records") if os.path.exists(p) else pd.DataFrame(
        columns=["code", "trade_date", "close", "settle", "vol", "oi"])
    h["trade_date"] = h["trade_date"].astype(str)
    h = h[h["trade_date"] != str(TRADE_DATE)]
    h = pd.concat([h, pd.DataFrame(recs)], ignore_index=True)
    h["trade_date"] = h["trade_date"].astype(str)
    h.to_json(p, orient="records", force_ascii=False)
    print("saved fut_im_month_hist.json (盘中实时 %s, %d 个 IM 月合约)"
          % (TRADE_DATE, len(recs)))


# ------------------------------------------------------------------ DataHub 主通道
def lib_spot():
    """标的实时价走 DataHub（新浪/腾讯/东财多源链），失败返回 False 由旧通道接管。"""
    px = LIB.spot_prices(list(SINA_CODE.keys()))
    if not px:
        print("  [lib] DataHub 标的实时价为空，回落东财/新浪直连")
        return False
    D = str(TRADE_DATE)
    rows, spot = [], []
    for code in SINA_CODE:
        cur = px.get(code)
        if cur is None:
            print("  miss", code)
            continue
        rows.append(dict(code=code, kind=KIND[code], trade_date=D, close=float(cur)))
        spot.append(dict(code=code, close=float(cur), trade_date=D))
    if not rows:
        return False
    p = os.path.join(DATA, "underlying_hist.json")
    uh = pd.read_json(p, orient="records") if os.path.exists(p) else pd.DataFrame(
        columns=["code", "kind", "trade_date", "close"])
    uh["trade_date"] = uh["trade_date"].astype(str)
    uh = uh[uh["trade_date"] != D]
    uh = pd.concat([uh, pd.DataFrame(rows)], ignore_index=True)
    uh["trade_date"] = uh["trade_date"].astype(str)
    uh = uh.sort_values(["code", "trade_date"])
    uh.to_json(p, orient="records", force_ascii=False)
    pd.DataFrame(spot).to_json(os.path.join(DATA, "spot.json"), orient="records",
                               force_ascii=False)
    print("saved underlying_hist.json / spot.json  (DataHub 实时 %s, %d 个标的)"
          % (D, len(rows)))
    return True


def lib_chains():
    """当日期权链走 DataHub 实时链接口；合约基础信息由链自带字段生成。"""
    ch, ba = LIB.realtime_chains(str(TRADE_DATE))
    if ch is None or not len(ch):
        print("  [lib] DataHub 期权链为空，回落东财直连")
        return False
    ch = ch.drop_duplicates("ts_code")
    ch.to_json(os.path.join(CH, str(TRADE_DATE) + ".json"), orient="records",
               force_ascii=False)
    for ex, fn in (("SSE", "basic_sse"), ("SZSE", "basic_szse"),
                   ("CFFEX", "basic_cffex")):
        codes = set(ch.loc[ch["exchange"] == ex, "ts_code"])
        sub = ba[ba["ts_code"].isin(codes)].copy()
        if len(sub):
            sub.to_json(os.path.join(DATA, fn + ".json"), orient="records",
                        force_ascii=False)
    bys = ch.groupby("exchange")["ts_code"].count().to_dict()
    print("saved chains/%s.json  (DataHub 实时链 %d 条; %s)"
          % (TRADE_DATE, len(ch), bys))
    return True


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if not TRADE_DATE:
        raise SystemExit("fetch_rt: 需设置 TRADE_DATE=YYYYMMDD")
    if mode in ("spot", "all"):
        if not lib_spot():
            mode_spot()
    if mode in ("chains", "all"):
        if not lib_chains():
            mode_chains()
    if mode in ("futures", "all"):
        # IM 月合约：DataHub 实时期货接口只覆盖主力连续（IF0/IC0/IH0/IM0 等），
        # 不含 IM2611 这类月合约，故期货月合约仍走东财实时列表。
        mode_futures()
    print("DONE fetch_rt", mode)


if __name__ == "__main__":
    main()