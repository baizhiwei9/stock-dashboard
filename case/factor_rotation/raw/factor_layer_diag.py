# -*- coding: utf-8 -*-
"""因子层单因子诊断（纯因子口径，不含任何短期/中期板块动量）。

口径：
  1) 打分池 = 全 A 可交易池（obs>=120 & 最近交易日有成交 & 非 ST），与 final_board_v2 一致。
  2) 对 26 个已计算个股因子，direction 调整后（值×direction，统一"大=好"）
     在池内做分位（pct rank, ascending, 0~1）。
  3) 概念层得分 = 成分股（在池内且有该因子值）的分位等权平均；
     概念层名次 = 在 133 个概念里按得分降序排名。
  4) 定位 AI视频 / 电机 在每个单因子下的概念层名次与覆盖度。

输出：output/factor_layer_diag.csv（26 行，每个因子一行）
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

FACTORS = [
    ("BP", +1), ("单季EP", +1), ("单季SP", +1), ("EPTTM", +1), ("SPTTM", +1),
    ("EPTTM一年分位点", +1), ("股息率", +1),
    ("一个月反转", -1), ("三个月反转", -1), ("一年动量", +1),
    ("单季净利同比增速", +1), ("单季营收同比增速", +1),
    ("单季ROE", +1), ("DELTAROE", +1),
    ("非流动性冲击", -1), ("一个月换手", -1), ("三个月换手", -1),
    ("一个月波动", -1), ("三个月波动", -1), ("特异度", -1),
    ("预期EPTTM", +1), ("预期PEG", +1), ("预期净利润环比", +1),
    ("三个月机构覆盖", +1), ("公募重仓", +1), ("机构持仓", +1),
]

TARGETS = ["电机", "AI视频"]

# ---------- 池 ----------
daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "amount"], dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "amount"], dtype={"ts_code": str, "trade_date": str})
daily = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
daily["amount"] = pd.to_numeric(daily["amount"], errors="coerce")
dates = sorted(daily["trade_date"].unique())
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
ST = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in amt.columns})
obs = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").notna().sum()
UPTO = len(dates) - 1
tr = (obs >= 120) & amt.iloc[UPTO].notna() & (amt.iloc[UPTO] > 0) & ~ST.reindex(amt.columns).fillna(False)
pool = set(tr[tr].index)
print(f"[pool] 全 A 可交易 {len(pool)}；截面日 {dates[UPTO]}")

# ---------- 截面 ----------
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str}).set_index("ts_code")
xs = xs[[c for c, _ in FACTORS]]

# ---------- 概念成分 ----------
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
# 与 final_board_v2 口径对齐：成分在日频面板中 >=5 只才纳入概念层
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in amt.columns]) >= 5}
N_CON = len(con_members)
print(f"[concept] {N_CON} 个（>=5 只成分）")

rows = []
for f, d in FACTORS:
    v = pd.to_numeric(xs[f], errors="coerce") * d
    v = v.reindex(sorted(pool))
    pct = v.rank(pct=True, ascending=True, method="average")
    rec = {"factor": f, "direction": d, "n_pool": int(pct.notna().sum())}
    # 概念层得分
    scores, covs = {}, {}
    for bc, mem in con_members.items():
        s = pct.reindex(mem).dropna()
        scores[bc] = s.mean() if len(s) else np.nan
        covs[bc] = len(s)
    ser = pd.Series(scores)
    rk = ser.rank(ascending=False, method="min")
    rec["n_con_ok"] = int(ser.notna().sum())
    for t in TARGETS:
        hit = [bc for bc, nm in con_names.items() if nm == t]
        if hit:
            bc = hit[0]
            rec[f"rk_{t}"] = int(rk[bc]) if pd.notna(rk.get(bc)) else np.nan
            rec[f"score_{t}"] = round(float(scores[bc]), 4) if pd.notna(scores[bc]) else np.nan
            rec[f"cov_{t}"] = covs[bc]
            rec[f"nmem_{t}"] = len(con_members[bc])
    top = ser.dropna().sort_values(ascending=False, kind="mergesort").head(3)
    rec["top3"] = "、".join(f"{con_names.get(bc, bc)}({v:.3f})" for bc, v in top.items())
    rows.append(rec)

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT, "factor_layer_diag.csv"), index=False)

pd.set_option("display.width", 200, "display.max_columns", 50)
print("\n===== 单因子：目标概念在概念层（133）的名次 =====")
print(df[["factor", "direction", "rk_AI视频", "cov_AI视频", "rk_电机", "cov_电机", "n_con_ok"]].to_string(index=False))
print("\n===== 各因子概念层 Top3 =====")
for _, r in df.iterrows():
    print(f"  {r['factor']:>10s} d{r['direction']:+d}  {r['top3']}")
print("\n[done] -> output/factor_layer_diag.csv")