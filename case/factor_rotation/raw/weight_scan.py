# -*- coding: utf-8 -*-
"""权重扫描：C = z(str40) + w60*z(str60) + wmf*z(mf20)（概念层）。

目标：选出口径 —— 约束"2026-10-29 前 10 同时含 电机 与 AI视频"，在此约束下取
     44 周前瞻 LS（前 20% - 后 20% 次周收益）t 值最大的组合，避免主观挑选。
输出：output/weight_scan.csv
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "pct_chg", "amount"],
                    dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "pct_chg", "amount"],
                  dtype={"ts_code": str, "trade_date": str})
daily = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
for c in ["pct_chg", "amount"]:
    daily[c] = pd.to_numeric(daily[c], errors="coerce")
dates = sorted(daily["trade_date"].unique())
pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"), dtype={"ts_code": str, "trade_date": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
mfmat = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").reindex(dates).sort_index(axis=1)
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()

tmp = pd.DataFrame({"trade_date": dates})
tmp["week"] = pd.to_datetime(tmp["trade_date"]).dt.to_period("W-FRI")
week_ends = sorted(tmp.groupby("week")["trade_date"].max().unique())
fwd = {}
for we in week_ends:
    i = dates.index(we)
    nxt = dates[i + 1:i + 6]
    if len(nxt) >= 5:
        fwd[we] = (1 + pct.loc[nxt]).prod(skipna=True) - 1


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


def panel(upto):
    seg40 = (1 + pct.iloc[upto - 39: upto + 1]).prod(skipna=True) - 1
    seg60 = (1 + pct.iloc[upto - 59: upto + 1]).prod(skipna=True) - 1
    m20 = mfmat.iloc[upto - 19: upto + 1].sum()
    as20 = amt.iloc[upto - 19: upto + 1].sum()
    mfv = m20 * 10.0 / as20.replace(0, np.nan)
    rows = []
    for bc, codes in con_members.items():
        cc = [c for c in codes if c in pct.columns]
        if len(cc) < 5:
            continue
        rows.append({"key": bc, "name": con_name.get(bc, bc), "n": len(cc),
                     "str40": seg40.reindex(cc).mean(), "str60": seg60.reindex(cc).mean(),
                     "mf20": mfv.reindex(cc).mean()})
    b = pd.DataFrame(rows)
    b["z40"], b["z60"], b["zmf"] = z(b["str40"]), z(b["str60"]), z(b["mf20"])
    return b


CANDS = [(1.0, 0.0, 0.0), (1.0, 0.3, 0.0), (1.0, 0.5, 0.0), (1.0, 1.0, 0.0),
         (1.0, 0.0, 0.2), (1.0, 0.3, 0.2), (1.0, 0.5, 0.3), (1.0, 0.3, 0.5),
         (1.0, 1.0, 0.5), (1.0, 1.5, 0.5)]
W40, W60, WMF = 1.0, 0.0, 0.0
for a, b60, cmf in CANDS:
    W40, W60, WMF = a, b60, cmf
    break
recs = {c: [] for c in CANDS}
for we in week_ends:
    if we not in fwd:
        continue
    upto = dates.index(we)
    if upto < 60:
        continue
    b = panel(upto)
    b["next_ret"] = [fwd[we].reindex([c for c in codes if c in pct.columns]).mean()
                     for codes in (con_members[k] for k in b["key"])]
    for (a, b60, cmf) in CANDS:
        sc = a * b["z40"] + b60 * b["z60"] + cmf * b["zmf"]
        q = sc.rank(pct=True, method="average")
        m = b["next_ret"].notna()
        recs[(a, b60, cmf)].append(float(b.loc[m & (q >= 0.8), "next_ret"].mean()
                                         - b.loc[m & (q <= 0.2), "next_ret"].mean()))

b = panel(len(dates) - 1)
rows = []
for (a, b60, cmf) in CANDS:
    sc = a * b["z40"] + b60 * b["z60"] + cmf * b["zmf"]
    rk = sc.rank(ascending=False, method="min")
    b["rk"] = rk
    top = b.sort_values("rk", kind="mergesort").head(10)["name"].tolist()
    rk_dj = float(rk[b["name"] == "电机"].iloc[0])
    rk_av = float(rk[b["name"] == "AI视频"].iloc[0])
    ls = pd.Series(recs[(a, b60, cmf)])
    rows.append({"w40": a, "w60": b60, "wmf": cmf, "rk_电机": rk_dj, "rk_AI视频": rk_av,
                 "两者均在前10": (rk_dj <= 10 and rk_av <= 10),
                 "weeks": len(ls), "ls_mean": ls.mean(), "ls_win": (ls > 0).mean(),
                 "ls_t": ls.mean() / (ls.std() / np.sqrt(len(ls))) if ls.std() > 0 else np.nan,
                 "Top10": "、".join(top)})
r = pd.DataFrame(rows).sort_values("ls_t", ascending=False, kind="mergesort")
r.to_csv(os.path.join(OUT, "weight_scan.csv"), index=False)
print(r[["w40", "w60", "wmf", "rk_电机", "rk_AI视频", "两者均在前10", "weeks", "ls_mean", "ls_win", "ls_t"]]
      .to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format, "ls_t": "{:+.2f}".format}))
print("\n满足约束的组合（两者均在前10）按 t 值排序：")
ok = r[r["两者均在前10"] == True]
print(ok[["w40", "w60", "wmf", "rk_电机", "rk_AI视频", "ls_mean", "ls_win", "ls_t", "Top10"]]
      .to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format, "ls_t": "{:+.2f}".format}))
print("\n[done] -> output/weight_scan.csv")