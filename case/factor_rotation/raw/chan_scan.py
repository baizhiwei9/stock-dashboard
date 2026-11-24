# -*- coding: utf-8 -*-
"""
用缠论（分型版 chan-fenxing-ban）在日线上遍历请求32 的三层名单
（行业105 / 主题16 / 概念133，共254个板块），判定 一买/二买/三买 或 突破中枢。

板块无官方指数日线（TH系列不可用），故按成分股等权合成日线 OHLCV 序列。
等权合成：index_close[t] = index_close[t-1]*(1+mean(成员 pct_chg/100))，
open/high/low 用成员 (open/pre_close-1) 等比例等权平均后套到前一日指数收盘。
"""
import os, sys, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "raw")
OUT = os.path.join(BASE, "output")
CHAN = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, CHAN)
import chan_fractal as cf
import chan_core as cc

WINDOW = 40          # "近期"窗口（交易日）≈ 2 个月
SHORT = "SH"  # noqa

# ---------------- 1. 板块 -> 成员(A股) ----------------
def a_share(codes):
    return {c for c in codes if isinstance(c, str) and c.split(".")[-1] in ("SZ", "SH", "BJ")}

tm = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str)
tm2 = pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)
tmall = pd.concat([tm[["board_name", "con_code"]], tm2[["board_name", "con_code"]]], ignore_index=True)

con_members = {}
for bn, g in tmall.groupby("board_name"):
    con_members[bn] = a_share(set(g.con_code))

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
ind_members = {}
for ind, g in sb.groupby("industry"):
    ind_members[ind] = set(g.ts_code)

# THEME_MAP
src = open(os.path.join(RAW, "final_pick_fullA.py"), encoding="utf-8").read()
seg = src[src.index("THEME_MAP = {"):src.index("# ---------- 数据 ----------")]
ns = {}
exec(seg, ns)
THEME_MAP = ns["THEME_MAP"]

theme_members = {}
for th, conceps in THEME_MAP.items():
    s = set()
    for cn in conceps:
        s |= con_members.get(cn, set())
    theme_members[th] = s

# ---------------- 2. 日线数据（含最新一日） ----------------
cols = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg"]
d = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), usecols=cols)
d2 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"), usecols=cols)
d = pd.concat([d, d2], ignore_index=True)
d["trade_date"] = d["trade_date"].astype(int)
d = d.drop_duplicates(["ts_code", "trade_date"]).sort_values(["trade_date", "ts_code"])
dates = sorted(d.trade_date.unique())
d["op_r"] = d["open"] / d["pre_close"] - 1.0
d["hi_r"] = d["high"] / d["pre_close"] - 1.0
d["lo_r"] = d["low"] / d["pre_close"] - 1.0
d["cl_r"] = d["pct_chg"] / 100.0

# 涨跌停幅度逐股上限（新股首日无涨跌幅，需按板中性化，否则次新股板块等权指数爆炸）
def _lim(code):
    suf = code.split(".")[-1]
    pre = code[:3]
    if suf == "BJ":
        return 0.30
    if pre in ("300", "301", "688", "689"):
        return 0.20
    return 0.10

lim_map = {c: _lim(c) for c in d["ts_code"].unique()}
for _r in ["op_r", "hi_r", "lo_r", "cl_r"]:
    d[_r] = np.clip(d[_r], d["ts_code"].map(lambda c: -lim_map[c]),
                    d["ts_code"].map(lambda c: lim_map[c]))

def piv(r):
    return d.pivot_table(index="trade_date", columns="ts_code", values=r)

P = {r: piv(r) for r in ["op_r", "hi_r", "lo_r", "cl_r"]}
P = {k: v.reindex(dates) for k, v in P.items()}
print("[data] dates", dates[0], "..", dates[-1], "n=", len(dates), "codes", d.ts_code.nunique())

def synth_index(members):
    """等权合成板块日线 OHLCV。返回 DataFrame(time/open/high/low/close) 或 None。"""
    mcols = [c for c in members if c in P["cl_r"].columns]
    if len(mcols) < 5:
        return None, len(mcols)
    mr = {k: P[k][mcols].mean(axis=1, skipna=True).values for k in ["op_r", "hi_r", "lo_r", "cl_r"]}
    n = len(dates)
    close = np.empty(n); op = np.empty(n); hi = np.empty(n); lo = np.empty(n)
    close[0] = 1000.0
    op[0] = hi[0] = lo[0] = 1000.0
    for t in range(1, n):
        rc = mr["cl_r"][t]
        rc = 0.0 if not np.isfinite(rc) else rc
        close[t] = close[t - 1] * (1 + rc)
        def g(x):
            v = mr[x][t]
            return 0.0 if not np.isfinite(v) else v
        op[t] = close[t - 1] * (1 + g("op_r"))
        hi[t] = close[t - 1] * (1 + g("hi_r"))
        lo[t] = close[t - 1] * (1 + g("lo_r"))
    dfi = pd.DataFrame({"time": pd.to_datetime([str(x) for x in dates], format="%Y%m%d"),
                        "open": op, "high": hi, "low": lo, "close": close})
    # 保证 high>=max(open,close), low<=min(open,close)
    dfi["high"] = dfi[["high", "open", "close"]].max(axis=1)
    dfi["low"] = dfi[["low", "open", "close"]].min(axis=1)
    return dfi, len(mcols)

# ---------------- 3. 缠论判读 ----------------
def chan_eval(dfi, base):
    df2, _ = cf.normalize_input(dfi, name="board")
    ends = cf.get_endpoints(df2, base)
    close = df2["close"].values
    n = len(close)
    if base == "struct":
        zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    else:
        zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    m = cc.macd_on(df2, 20, 40, 5)
    ev = cc.detect_buy_sell(ends, m["hist"].values, close, cc.MIN_BI_GAP)
    buys = [e for e in ev if e["sig"] == 1 and e["kind"] in ("B1", "B2", "B3")]
    buys.sort(key=lambda x: x["row"])
    latest = buys[-1] if buys else None
    latest_recent = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
    lr = latest_recent[-1] if latest_recent else None
    # 中枢
    zs_last = None
    if zss:
        zs_last = max(zss, key=lambda z: (z[1], z[0]))
    res = {
        "n_bar": n, "n_bi": len(ends), "n_zs": len(zss),
        "latest_kind": latest["kind"] if latest else None,
        "latest_date": str(df2["time"].iloc[latest["row"]].date()) if latest else None,
        "latest_ago": (n - 1 - latest["row"]) if latest else None,
        "recent_kind": lr["kind"] if lr else None,
        "recent_date": str(df2["time"].iloc[lr["row"]].date()) if lr else None,
        "recent_ago": (n - 1 - lr["row"]) if lr else None,
    }
    # 突破中枢（最新中枢上沿 ZG）
    if zs_last is not None:
        zg = float(zs_last[3])
        z_end_row = ends[zs_last[1]][0]
        res["zg"] = round(zg, 4)
        res["last_close"] = round(float(close[-1]), 4)
        if close[-1] > zg:
            i = n - 1
            while i > 0 and close[i - 1] > zg:
                i -= 1
            res["breakout"] = True
            res["breakout_ago"] = n - 1 - i
            res["breakout_date"] = str(df2["time"].iloc[i].date())
            res["breakout_after_zs"] = bool(i > z_end_row)
        else:
            res["breakout"] = False
            res["breakout_ago"] = None
            res["breakout_date"] = None
            res["breakout_after_zs"] = False
    else:
        res["zg"] = None; res["last_close"] = round(float(close[-1]), 4)
        res["breakout"] = False; res["breakout_ago"] = None
        res["breakout_date"] = None; res["breakout_after_zs"] = False
    return res

rk = pd.read_csv(os.path.join(OUT, "nonneg_layer_rank.csv"))
_rows = []
for _, row in rk.iterrows():
    lv, name, rank, score = row["层级"], row["名称"], int(row["名次"]), float(row["得分"])
    if lv == "概念":
        mem = con_members.get(name, set())
    elif lv == "行业":
        mem = ind_members.get(name, set())
    else:
        mem = theme_members.get(name, set())
    dfi, nmem = synth_index(mem)
    if dfi is None:
        _rows.append({"层级": lv, "名称": name, "因子名次": rank, "因子得分": score,
                      "成分池内数": nmem, "状态": "成分不足"})
        continue
    st = chan_eval(dfi, "struct")
    ma = chan_eval(dfi, "ma5-40")
    rec = {"层级": lv, "名称": name, "因子名次": rank, "因子得分": score, "成分池内数": nmem,
           "状态": "ok", "K线根数": st["n_bar"], "笔数": st["n_bi"], "中枢数": st["n_zs"],
           "最新买点类型": st["latest_kind"], "最新买点日期": st["latest_date"], "最新买点距今天": st["latest_ago"],
           "近40日买点类型": st["recent_kind"], "近40日买点日期": st["recent_date"], "近40日买点距今天": st["recent_ago"],
           "中枢上沿ZG": st["zg"], "最新收盘": st["last_close"],
           "突破中枢": st["breakout"], "突破距今天": st["breakout_ago"], "突破日期": st["breakout_date"],
           "突破在中枢后": st["breakout_after_zs"],
           "均线版最新买点": ma["latest_kind"], "均线版最新买点日期": ma["latest_date"],
           "均线版近40日买点": ma["recent_kind"]}
    _rows.append(rec)

out = pd.DataFrame(_rows)
out.to_csv(os.path.join(OUT, "chan_layer_scan.csv"), index=False, encoding="utf-8-sig")
print("[saved]", os.path.join(OUT, "chan_layer_scan.csv"), out.shape)
print(out[["层级", "名称", "因子名次", "最新买点类型", "最新买点日期", "突破中枢", "突破距今天"]].head(20).to_string())