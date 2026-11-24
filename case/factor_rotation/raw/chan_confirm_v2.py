# -*- coding: utf-8 -*-
"""请求 35：按用户口径复核「一买是否该成立」——在 v3 买点基础上叠加『向上确认』过滤。

用户口径（原话）：一买至少得涨起来；至少 10 日线上穿 20 日线，或至少连续 3 根涨，
或向上的分型（向上笔）已成立。

判定（对每个板块的等权合成日线，结构版 struct / MACD 20,40,5）：
  C1 = 最新 MA10 > MA20（并记录最近一次上穿的日期与距离）
  C2 = 末端连续上涨 ≥ 3 根（收盘价逐日递增）
  C3 = 买点之后已出现新的『顶』端点（向上笔成型）
  C4(辅) = 买点后最高收盘 > 买点当日最高价（价格确实上攻过）
  已确认 = C1 | C2 | C3

档位（新）：
  A 当前机会    = 近40日买点 且 仍有效 且 已确认
  B 底部信号待确认 = 近40日买点 且 仍有效 但 未确认
  C 中枢突破    = 无近40日买点，但当前收盘 > 最近中枢上沿
  D 买点已失效  = 近40日买点已被打掉
  E 无
"""
import os, sys, json
import numpy as np, pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"; OUT = f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}
exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, theme_members = ns["synth"], ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc, WINDOW, TYPE_W = ns["cf"], ns["cc"], ns["WINDOW"], ns["TYPE_W"]
eval_board, buys_detail, judge = ns["eval_board"], ns["buys_detail"], ns["judge"]

def confirm(dfi, buyinfo, base="struct"):
    """在结构的最后一个买点上评估向上确认。"""
    df2, _ = cf.normalize_input(dfi, name="board")
    close = df2["close"].values; high = df2["high"].values; n = len(close)
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    c1 = bool(np.isfinite(ma10[-1]) and np.isfinite(ma20[-1]) and ma10[-1] > ma20[-1])
    # 最近一次上穿（MA10 从下穿上 MA20）
    cross_ago = None
    for t in range(1, n):
        if np.isfinite(ma10[t - 1]) and np.isfinite(ma20[t - 1]) and np.isfinite(ma10[t]) and np.isfinite(ma20[t]):
            if ma10[t - 1] <= ma20[t - 1] and ma10[t] > ma20[t]:
                cross_ago = n - 1 - t
    # 末端连续上涨
    k = 0
    for t in range(n - 1, 0, -1):
        if close[t] > close[t - 1]: k += 1
        else: break
    c2 = k >= 3
    # 买点后是否出现新顶端点（向上笔成型）；或价格上攻
    c3 = c4 = False
    if buyinfo is not None:
        br = buyinfo["row"]
        ends = cf.get_endpoints(df2, base)
        c3 = any(e[1] == "top" and e[0] > br for e in ends)
        if br + 1 < n:
            c4 = bool(np.max(close[br + 1:]) > high[br])
    gap = (ma10[-1] / ma20[-1] - 1) * 100 if (np.isfinite(ma10[-1]) and np.isfinite(ma20[-1])) else None
    r5 = (close[-1] / close[-6] - 1) * 100 if n > 6 else None
    dd20 = (close[-1] / np.max(close[-21:]) - 1) * 100 if n > 21 else None
    return dict(MA10=round(float(ma10[-1]), 2), MA20=round(float(ma20[-1]), 2),
                均线多头=gap is not None and gap > 0, MA10MA20差pct=(round(gap, 2) if gap is not None else None),
                上次上穿距今天=(int(cross_ago) if cross_ago is not None else None),
                末端连涨根数=int(k), 近5日pct=(round(r5, 2) if r5 is not None else None),
                距20日高点pct=(round(dd20, 2) if dd20 is not None else None),
                C1=bool(c1), C2=bool(c2), C3=bool(c3), C4=bool(c4),
                已确认=bool(c1 or c2 or c3))

rk = pd.read_csv(f"{OUT}/nonneg_layer_rank.csv")
rows = []
for _, row in rk.iterrows():
    lv, name, rank, score = row["层级"], row["名称"], int(row["名次"]), float(row["得分"])
    mem = (con_members.get(name, set()) if lv == "概念"
           else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set())))
    dfi, nm = synth(mem)
    if dfi is None:
        rows.append({"层级": lv, "名称": name, "因子名次": rank, "状态": "成分不足"}); continue
    r = eval_board(dfi)
    rec = r["recent"]
    # 复算买点行号以评估确认
    df2, _ = cf.normalize_input(dfi, name="board")
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values; low = df2["low"].values; n = len(close)
    buys = buys_detail(ends, m["hist"].values, close, cc.MIN_BI_GAP)
    recent_cand = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
    buy_r = recent_cand[-1] if recent_cand else None
    cfm = confirm(dfi, buy_r)
    if rec and rec["valid"]:
        tier = "A" if cfm["已确认"] else "B"
    elif rec:
        tier = "D"
    elif r["brk"]:
        tier = "C"
    else:
        tier = "E"
    d0 = dict(层级=lv, 名称=name, 因子名次=rank, 因子得分=score, 成分池内数=nm, 状态="ok",
              新档位=tier, 旧档位=(1 if (rec and rec["valid"]) else (4 if rec else (3 if r["brk"] else (2 if r["latest"] else 5)))),
              笔数=r["n_bi"], 中枢数=r["n_zs"], 最近中枢上沿ZG=r["zg_last"], 最新收盘=r["last"],
              当前突破中枢=r["brk"], 突破距今天=r["brk_ago"])
    if buy_r is not None:
        j = judge(buy_r, close, low, n, df2["time"].dt.strftime("%Y-%m-%d").values)
        d0.update({"近40日买点类型": j["kind"], "近40日买点日期": j["date"], "近40日买点距今天": j["ago"],
                   "近40日买点价位": j["px"], "近40日买点关键位": j["ref"], "近40日买点仍有效": j["valid"],
                   "近40日买点失效原因": j["inv"]})
    d0.update(cfm)
    rows.append(d0)
out = pd.DataFrame(rows)
out.to_csv(f"{OUT}/chan_confirm_v2.csv", index=False, encoding="utf-8-sig")
ok = out[out["状态"] == "ok"].copy().fillna({"C1": False, "C2": False, "C3": False, "C4": False, "已确认": False})
print("[out]", f"{OUT}/chan_confirm_v2.csv", out.shape)
print("\n=== 新分档计数（合成等权日线，数据截至 2026-10-29 收盘）===")
for lv in ["行业", "主题", "概念"]:
    s = ok[ok["层级"] == lv]
    print(f"{lv}: n={len(s)} | A当前机会={int((s['新档位']=='A').sum())} B底部信号待确认={int((s['新档位']=='B').sum())} "
          f"C中枢突破={int((s['新档位']=='C').sum())} D买点已失效={int((s['新档位']=='D').sum())} E无={int((s['新档位']=='E').sum())}")
def dump(df, title, cols_):
    print(f"\n=== {title} ===")
    if df.empty: print("(空)"); return
    print(df[cols_].to_string(index=False))
A = ok[ok["新档位"] == "A"].sort_values(["层级", "近40日买点距今天"])
dump(A, "A 类「近40日买点·仍有效·已向上确认」全部",
     ["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点距今天", "最新收盘",
      "MA10MA20差pct", "末端连涨根数", "C1", "C2", "C3"])
B = ok[ok["新档位"] == "B"].sort_values(["层级", "近40日买点距今天"])
dump(B, "B 类「近40日买点·仍有效·未向上确认」（原 T1，观感问题来源）",
     ["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点距今天", "近40日买点价位",
      "近40日买点关键位", "最新收盘", "MA10MA20差pct", "末端连涨根数", "近5日pct", "C1", "C2", "C3", "C4"])
C = ok[ok["新档位"] == "C"].sort_values(["层级", "突破距今天"])
dump(C, "C 类「无近40日买点·当前站上最近中枢上沿」", ["层级", "名称", "因子名次", "最近中枢上沿ZG", "最新收盘", "突破距今天"])
# 被点名条目
print("\n=== 被点名板块逐条 ===")
sel = ok[ok["名称"].isin(["国产航母", "培育钻石", "电机", "银行", "半导体", "航空", "中船系", "煤炭开采", "铁路", "路桥"])]
dump(sel.sort_values(["层级", "名称"]),
     "点名条目", ["层级", "名称", "因子名次", "新档位", "近40日买点类型", "近40日买点日期", "近40日买点距今天",
                  "最新收盘", "MA10MA20差pct", "上次上穿距今天", "末端连涨根数", "近5日pct", "C1", "C2", "C3", "C4"])
json.dump({"as_of": "2026-10-29", "window": WINDOW,
           "counting": {lv: {k: int((ok[(ok["层级"] == lv)]["新档位"] == k).sum()) for k in ["A", "B", "C", "D", "E"]} for lv in ["行业", "主题", "概念"]},
           "A": A[["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "近40日买点距今天", "最新收盘", "MA10MA20差pct", "末端连涨根数"]].to_dict("records"),
           "B": B[["层级", "名称", "因子名次", "近40日买点类型", "近40日买点日期", "最新收盘"]].to_dict("records")},
          open(f"{OUT}/chan_confirm_v2_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n[out] chan_confirm_v2_summary.json")
