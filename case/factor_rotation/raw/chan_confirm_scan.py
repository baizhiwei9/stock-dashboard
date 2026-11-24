# -*- coding: utf-8 -*-
"""请求 35：缠论买点 + 「向上确认」过滤。
确认条件（满足任一即视为已向上确认）：
  A 均线：最新收盘时 MA10 > MA20
  B 连续：末端连续收阳 >= 3 根
  C 结构：买点行之后出现顶端点（底分型确认 + 向上笔成型）
当前机会 = 近40日买点仍有效 且 已向上确认。
"""
import os, sys, json
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
src = open(f"{BASE}/raw/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, theme_members = ns["synth"], ns["con_members"], ns["ind_members"], ns["theme_members"]
eval_board, OUT, cf, cc = ns["eval_board"], ns["OUT"], ns["cf"], ns["cc"]
buys_detail, W = ns["buys_detail"], ns["WINDOW"]

def buy_info(dfi):
    df2, _ = cf.normalize_input(dfi, name="b")
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values.astype(float); high = df2["high"].values.astype(float)
    n = len(close)
    buys = buys_detail(ends, m["hist"].values, close, cc.MIN_BI_GAP)
    recent = [b for b in buys if (n - 1 - b["row"]) <= W]
    br = recent[-1]["row"] if recent else None
    s = pd.Series(close)
    ma10 = s.rolling(10).mean().values; ma20 = s.rolling(20).mean().values
    A = bool(np.isfinite(ma10[-1]) and np.isfinite(ma20[-1]) and ma10[-1] > ma20[-1])
    k = 0
    for t in range(n - 1, 0, -1):
        if close[t] > close[t - 1]: k += 1
        else: break
    B = k >= 3
    C = bool(br is not None and any(tp == "top" and row > br for (row, tp, px) in ends))
    E = bool(br is not None and br + 1 < n and close[br + 1:].max() > high[br])
    return dict(A=A, B=B, C=C, E=E, ok=(A or B or C), row=br,
                ma10=round(float(ma10[-1]), 2), ma20=round(float(ma20[-1]), 2), up_k=int(k))

rk = pd.read_csv(f"{OUT}/nonneg_layer_rank.csv")
rows = []
for _, row in rk.iterrows():
    lv, name, rank = row["层级"], row["名称"], int(row["名次"])
    mem = (con_members.get(name, set()) if lv == "概念"
           else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set())))
    dfi, nm = synth(mem)
    if dfi is None:
        rows.append(dict(层级=lv, 名称=name, 因子名次=rank, 状态="成分不足")); continue
    r = eval_board(dfi); rec = r["recent"]; c = buy_info(dfi)
    tier = 1 if (rec and rec["valid"]) else (4 if rec else (3 if r["brk"] else (2 if r["latest"] else 5)))
    rows.append(dict(层级=lv, 名称=name, 因子名次=rank, 状态="ok", 旧档位=tier,
                     MA10=c["ma10"], MA20=c["ma20"], 连涨=c["up_k"], A均线=c["A"], B连涨=c["B"],
                     C结构=c["C"], 价上攻=c["E"], 已确认=c["ok"],
                     买点类型=(rec["kind"] if rec else None), 买点日期=(rec["date"] if rec else None),
                     买点距今=(rec["ago"] if rec else None), 买点仍有效=(rec["valid"] if rec else None),
                     最新收盘=r["last"], 当前突破中枢=r["brk"],
                     新档位=(1 if (rec and rec["valid"] and c["ok"]) else ("1x" if (rec and rec["valid"]) else tier))))
out = pd.DataFrame(rows)
out.to_csv(f"{OUT}/chan_confirm_scan.csv", index=False, encoding="utf-8-sig")
ok = out[out["状态"] == "ok"]
print("A均线通过:", int(ok["A均线"].sum()), "| B连续>=3:", int(ok["B连涨"].sum()), "| C结构通过:", int(ok["C结构"].sum()), "| 合计已确认:", int(ok["已确认"].sum()), "/", len(ok))
