# -*- coding: utf-8 -*-
"""请求 34 定稿 v2：缠论日线（结构版 struct，MACD 20/40/5）遍历候选板块（行业/主题/概念），
按「最近一次买点当前是否仍然有效」过滤，输出当前机会名单。最新收盘日由 raw/_daily_snap.read_daily 动态给出。

关键修正（v2 → v3）：
  B3 三买失效判定必须对照「该买点真正突破的那个中枢」的上沿 ZG：
  取【买点价下方、起点不晚于买点、ZG 最高的那个中枢】（价格刚向上离开的那个中枢），
  买点后任一交易日收盘 ≤ 该 ZG 即「跌回中枢」，信号失效。
  B1/B2：买点后任一交易日收盘 < 该买点锚定低点（B1 用自身低点，B2 用其所依托的一买低点）即失效。
  典型校验：半导体 09-11 三买突破的中枢 ZG=1431.1（非早期 1098.3），10 月起收盘跌至
  1201.78 ≤ 1431.1 → 三买失效，从当前机会中剔除（与用户判断一致）。

档位：
  T1 近40日买点·仍有效（当前仍成立的机会）
  T2 近40日无买点，仅有更早买点
  T3 无近40日买点，但当前收盘 > 最近中枢上沿（突破中枢）
  T4 近40日买点已被打掉（陈旧信号，不得计为当前机会）
  T5 无
"""
import os, sys, json
import numpy as np, pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"; OUT = f"{BASE}/output"
CHAN = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, CHAN)
sys.path.insert(0, RAW)
import chan_fractal as cf, chan_core as cc
from _daily_snap import read_daily

WINDOW = 40
TYPE_W = {"B3": 3, "B2": 2, "B1": 1}

# ---------------- 数据与板块映射 ----------------
tm = pd.read_csv(f"{RAW}/theme_member_all.csv", dtype=str)
tm2 = pd.read_csv(f"{RAW}/theme_member_ai.csv", dtype=str)
tmall = pd.concat([tm[["board_name", "con_code"]], tm2[["board_name", "con_code"]]], ignore_index=True)
A = lambda cs: {c for c in cs if isinstance(c, str) and c.split(".")[-1] in ("SZ", "SH", "BJ")}
con_members = {bn: A(set(g.con_code)) for bn, g in tmall.groupby("board_name")}
sb = pd.read_csv(f"{RAW}/stock_basic.csv", dtype=str)
ind_members = {k: set(g.ts_code) for k, g in sb.groupby("industry")}
_src = open(f"{RAW}/final_pick_fullA.py", encoding="utf-8").read()
_seg = _src[_src.index("THEME_MAP = {"):_src.index("# ---------- 数据 ----------")]
_ns = {}; exec(_seg, _ns); THEME_MAP = _ns["THEME_MAP"]
theme_members = {th: set().union(*[con_members.get(c, set()) for c in cs]) for th, cs in THEME_MAP.items()}

cols = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg"]
# 区间表 + 最新单日快照（自动跟随最新交易日，不再硬编码日期）
d, AS_OF = read_daily(RAW, usecols=cols)
d["trade_date"] = d.trade_date.astype(int)
d = d.drop_duplicates(["ts_code", "trade_date"])
dates = sorted(d.trade_date.unique())
AS_OF_FMT = f"{AS_OF[:4]}-{AS_OF[4:6]}-{AS_OF[6:]}" if len(AS_OF) == 8 else str(dates[-1])
d["op_r"] = d.open / d.pre_close - 1; d["hi_r"] = d.high / d.pre_close - 1
d["lo_r"] = d.low / d.pre_close - 1; d["cl_r"] = d.pct_chg / 100
def _lim(c):
    s = c.split(".")[-1]
    return 0.30 if s == "BJ" else (0.20 if c[:3] in ("300", "301", "688", "689") else 0.10)
_lm = {c: _lim(c) for c in d.ts_code.unique()}
for r in ["op_r", "hi_r", "lo_r", "cl_r"]:
    d[r] = np.clip(d[r], d.ts_code.map(lambda c: -_lm[c]), d.ts_code.map(lambda c: _lm[c]))
P = {r: d.pivot_table(index="trade_date", columns="ts_code", values=r).reindex(dates)
     for r in ["op_r", "hi_r", "lo_r", "cl_r"]}

def synth(members):
    mc = [c for c in members if c in P["cl_r"].columns]
    if len(mc) < 5:
        return None, len(mc)
    mr = {k: P[k][mc].mean(axis=1, skipna=True).values for k in ["op_r", "hi_r", "lo_r", "cl_r"]}
    n = len(dates)
    close = np.empty(n); op = np.empty(n); hi = np.empty(n); lo = np.empty(n)
    close[0] = 1000.; op[0] = hi[0] = lo[0] = 1000.
    for t in range(1, n):
        f = lambda x: (0. if not np.isfinite(mr[x][t]) else mr[x][t])
        close[t] = close[t - 1] * (1 + f("cl_r")); op[t] = close[t - 1] * (1 + f("op_r"))
        hi[t] = close[t - 1] * (1 + f("hi_r")); lo[t] = close[t - 1] * (1 + f("lo_r"))
    dfi = pd.DataFrame({"time": pd.to_datetime([str(x) for x in dates], format="%Y%m%d"),
                        "open": op, "high": hi, "low": lo, "close": close})
    dfi["high"] = dfi[["high", "open", "close"]].max(axis=1)
    dfi["low"] = dfi[["low", "open", "close"]].min(axis=1)
    return dfi, len(mc)

def buys_detail(ends, hist, close, min_seg):
    """复刻 chan_core.detect_buy_sell 的买点，并附上每个买点自身的关键位：
       B3 -> zg（该买点真正突破的那个中枢的上沿）；B1/B2 -> anchor（锚定低点所在行号）。

    B3 锚定口径修正（v2 → v3）：
       底层 detect_buy_sell 对【每个】中枢独立判定三买（回抽底 > 该中枢 ZG），
       一个处于长期上行的板块会被早已远去的低中枢反复触发三买，这些并非有效的
       当前三买。故对每个三买，锚定中枢取【买点当日收盘价下方、起点不晚于买点、
       上沿 ZG 最高的那个中枢】——即买点日收盘已向上离开的那个中枢。
       半导体即典型案例：09-11 收盘 1463.42 已站上 ZG=1431.13 的中枢（该中枢
       2025-11 起、2026-09-23 结束），而非 ZG=1098.31 的早期中枢；按 1431.13 判，
       10 月起收盘跌至 1201.78 ≤ 1431.13 → 三买失效。"""
    res = []
    bs = cc.detect_backshift(ends, hist, close, min_seg)
    b1_rows = set(e["k_idx"] for e in bs if e["signal"] == 1)
    for e in bs:
        if e["signal"] == 1:
            res.append(dict(row=e["k_idx"], kind="B1", anchor=e["k_idx"], zg=None))
    last_b1 = None
    for idx, (row, tp, px_) in enumerate(ends):
        if row in b1_rows:
            last_b1 = idx
        elif tp == "bottom" and last_b1 is not None and row not in b1_rows:
            if any(ends[k][1] == "top" for k in range(last_b1 + 1, idx)):
                if px_ > ends[last_b1][2]:
                    res.append(dict(row=row, kind="B2", anchor=ends[last_b1][0], zg=None))
                    last_b1 = None
    zss = cc.detect_zhongshu(ends, min_seg)
    zinfo = [(ends[s][0], float(zg)) for s, e_, zd, zg in zss]  # (中枢起点行号, 上沿)
    seen = set()
    for s, e_, zd, zg in zss:
        left = False
        for idx in range(e_, len(ends)):
            row, tp, px_ = ends[idx]
            if tp == "top":
                if idx > 0 and max(px_, ends[idx - 1][2]) > zg:
                    left = True
            elif left and tp == "bottom":
                if px_ > zg:
                    bclose = float(close[row])
                    cand = [z for (sr, z) in zinfo if z < bclose and sr <= row]
                    az = max(cand) if cand else float(zg)
                    if (row, "B3") not in seen:
                        seen.add((row, "B3"))
                        res.append(dict(row=row, kind="B3", anchor=row, zg=az))
                left = False
    res.sort(key=lambda x: x["row"])
    return res

def judge(buy, close, low, n, datev):
    """该买点截至最新收盘是否仍有效。"""
    if buy is None:
        return None
    r0 = buy["row"]; ac = close[r0 + 1:]
    if buy["kind"] == "B3":
        zg = buy["zg"]
        inv = bool(ac.size and zg is not None and (ac <= zg).any())
        why = (f"买点后收盘跌回其所属中枢内(≤ZG={zg:.1f})" if inv else "")
        ref = round(float(zg), 2) if zg is not None else None
    else:
        alo = float(low[buy["anchor"]])
        inv = bool(ac.size and (ac < alo).any())
        why = (f"买点后收盘跌破买点低点({alo:.1f})" if inv else "")
        ref = round(alo, 2)
    return dict(kind=buy["kind"], date=str(datev[r0]), ago=int(n - 1 - r0),
                px=round(float(close[r0]), 2), ref=ref, valid=(not inv), inv=why)

def eval_board(dfi, base="struct"):
    df2, _ = cf.normalize_input(dfi, name="board")
    ends = cf.get_endpoints(df2, base)
    close = df2["close"].values; low = df2["low"].values; n = len(close)
    datev = df2["time"].dt.strftime("%Y-%m-%d").values
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    m = cc.macd_on(df2, 20, 40, 5)
    buys = buys_detail(ends, m["hist"].values, close, cc.MIN_BI_GAP)
    zs_last = max(zss, key=lambda z: (z[1], z[0])) if zss else None
    zg_last = float(zs_last[3]) if zs_last is not None else None
    recent_cand = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
    latest = judge(buys[-1] if buys else None, close, low, n, datev)
    recent = judge(recent_cand[-1] if recent_cand else None, close, low, n, datev)
    brk = False; brk_ago = None
    if zg_last is not None and close[-1] > zg_last:
        brk = True; i = n - 1
        while i > 0 and close[i - 1] > zg_last:
            i -= 1
        brk_ago = int(n - 1 - i)
    # 均线版对照
    ends_m = cf.get_endpoints(df2, "ma5-40")
    mm = cc.macd_on(df2, 20, 40, 5)
    bm = [b for b in buys_detail(ends_m, mm["hist"].values, close, cc.MIN_BI_GAP)
          if (n - 1 - b["row"]) <= WINDOW]
    ma_recent = None
    if bm:
        j = judge(bm[-1], close, low, n, datev)
        ma_recent = f"{j['kind']}@{j['date'][5:]}{'' if j['valid'] else '(失效)'}"
    return dict(n_bi=len(ends), n_zs=len(zss), zg_last=(round(zg_last, 2) if zg_last else None),
                last=round(float(close[-1]), 2), latest=latest, recent=recent,
                brk=brk, brk_ago=brk_ago, ma_recent=ma_recent)

rk = pd.read_csv(f"{OUT}/nonneg_layer_rank.csv")
rows = []
for _, row in rk.iterrows():
    lv, name, rank, score = row["层级"], row["名称"], int(row["名次"]), float(row["得分"])
    mem = (con_members.get(name, set()) if lv == "概念"
           else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set())))
    dfi, nm = synth(mem)
    if dfi is None:
        rows.append({"层级": lv, "名称": name, "因子名次": rank, "因子得分": score, "成分池内数": nm, "状态": "成分不足"})
        continue
    r = eval_board(dfi)
    rec = r["recent"]
    tier = 1 if (rec and rec["valid"]) else (4 if rec else (3 if r["brk"] else (2 if r["latest"] else 5)))
    d0 = dict(层级=lv, 名称=name, 因子名次=rank, 因子得分=score, 成分池内数=nm, 状态="ok",
              档位=tier, 笔数=r["n_bi"], 中枢数=r["n_zs"], 最近中枢上沿ZG=r["zg_last"], 最新收盘=r["last"],
              当前突破中枢=r["brk"], 突破距今天=r["brk_ago"], 均线版近40日买点=r["ma_recent"])
    if rec:
        d0.update({"近40日买点类型": rec["kind"], "近40日买点日期": rec["date"], "近40日买点距今天": rec["ago"],
                   "近40日买点价位": rec["px"], "近40日买点关键位": rec["ref"],
                   "近40日买点仍有效": rec["valid"], "近40日买点失效原因": rec["inv"]})
    if r["latest"]:
        lt = r["latest"]
        d0.update({"最新买点类型": lt["kind"], "最新买点日期": lt["date"], "最新买点距今天": lt["ago"],
                   "最新买点仍有效": lt["valid"], "最新买点失效原因": lt["inv"]})
    rows.append(d0)
out = pd.DataFrame(rows)
out.to_csv(f"{OUT}/chan_final_rank.csv", index=False, encoding="utf-8-sig")
print("[out]", f"{OUT}/chan_final_rank.csv", out.shape)

ok = out[out["状态"] == "ok"].copy()
print(f"\n=== 分档计数（数据截至 {AS_OF_FMT} 收盘） ===")
for lv in ["行业", "主题", "概念"]:
    s = ok[ok["层级"] == lv]
    c = {t: int((s["档位"] == t).sum()) for t in [1, 2, 3, 4, 5]}
    print(f"{lv}: n={len(s)} | T1近40日有效买点={c[1]} T2仅更早买点={c[2]} T3突破中枢={c[3]} T4买点已失效={c[4]} T5无={c[5]}")

def dump(df, title, cols_):
    print(f"\n=== {title} ===")
    if df.empty:
        print("(空)"); return
    print(df[cols_].to_string(index=False))

t1 = ok[ok["档位"] == 1].copy()
t1["_k"] = t1.apply(lambda r: (r["近40日买点距今天"], -TYPE_W.get(r["近40日买点类型"], 0)), axis=1)
t1 = t1.sort_values(["层级", "_k"])
dump(t1, "T1「近40日买点·当前仍有效」全部",
     ["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点距今天",
      "近40日买点价位", "近40日买点关键位", "最新收盘", "均线版近40日买点"])
t3 = ok[ok["档位"] == 3].sort_values(["层级", "突破距今天"])
dump(t3, "T3「无近40日买点·当前站上最近中枢上沿」全部",
     ["层级", "名称", "因子名次", "最近中枢上沿ZG", "最新收盘", "突破距今天", "均线版近40日买点"])
t4 = ok[ok["档位"] == 4].sort_values(["层级", "近40日买点距今天"])
dump(t4, "T4「近40日买点已被打掉（陈旧信号，须剔除）」全部",
     ["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点距今天",
      "近40日买点价位", "近40日买点关键位", "最新收盘", "近40日买点失效原因"])

for lv in ["行业", "主题", "概念"]:
    s = ok[(ok["层级"] == lv) & (ok["档位"] == 1)]
    print(f"\n[{lv}] 有效买点类型分布:", s["近40日买点类型"].value_counts().to_dict())

json.dump({"as_of": AS_OF_FMT, "window": WINDOW,
           "counting": {lv: {str(k): int((ok[ok["层级"] == lv]["档位"] == k).sum()) for k in [1, 2, 3, 4, 5]}
                        for lv in ["行业", "主题", "概念"]},
           "t1": t1[["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点距今天", "最新收盘"]].to_dict("records"),
           "t3": t3[["层级", "名称", "因子名次", "最近中枢上沿ZG", "最新收盘", "突破距今天"]].to_dict("records"),
           "t4": t4[["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点失效原因"]].to_dict("records")},
          open(f"{OUT}/chan_final_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n[out] chan_final_summary.json")