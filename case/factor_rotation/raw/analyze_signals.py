# -*- coding: utf-8 -*-
"""分析 style_signal_backtest_v2.csv：两段下跌前各候选信号的提前量 + 全样本分位 + 阈值扫描
口径：mkt_ret = 因子截面 week_end 之后 5 个交易日的市场等权收益（与因子 LS 同口径）
事件周定义（mkt_ret 显著为负）：
  A(7月): 20260703(-3.7%) 20260710(-7.0%) 20260717(-2.8%)  起点 20260703
  B(9末-10初): 20260918(-1.6%) 20260924(-1.8%) 20260930(-3.4%) 20261009(-5.4%)  起点 20260918
前置窗口：起点前 1~4 周
"""
import pandas as pd
import numpy as np

df = pd.read_csv("style_signal_backtest_v2.csv")
df["week"] = df["week"].astype(int)

# ---------- 事件定义 ----------
EVENTS = [
    {"name": "A_7月下跌", "start": 20260703, "down_weeks": [20260703, 20260710, 20260717]},
    {"name": "B_9月末10月初下跌", "start": 20260918, "down_weeks": [20260918, 20260924, 20260930, 20261009]},
]
sig_cols = ["n_lead", "n_impr", "n_weak", "n_lag", "n_mom_neg", "n_rs_lt100", "n_rot",
            "def_gro_4w", "def_rev_4w", "rev_gro_4w", "val_gro_4w", "def_1w"]

print("=" * 100)
print("全样本时序（周 | mkt_ret | n_factor | n_lead | n_weak | n_rot | def_gro_4w | def_rev_4w | def_1w）")
print("=" * 100)
print(df[["week", "mkt_ret", "n_factor", "n_lead", "n_impr", "n_weak", "n_lag",
          "n_mom_neg", "n_rs_lt100", "n_rot", "def_gro_4w", "def_rev_4w", "def_1w"]].to_string(index=False))

# ---------- 事件前信号表 ----------
print("\n" + "=" * 100)
print("两段下跌：事件前 1~4 周信号值 + 全样本分位数定位")
print("=" * 100)
for ev in EVENTS:
    st = ev["start"]
    idx = df.index[df["week"] == st][0]
    rows = df.iloc[max(0, idx - 4):idx]
    print(f"\n### 事件 {ev['name']}（起点 {st}，下跌周 {ev['down_weeks']}）")
    print("前置周信号：")
    print(rows[["week", "mkt_ret", "n_factor", "n_lead", "n_impr", "n_weak", "n_lag",
                "n_mom_neg", "n_rs_lt100", "n_rot", "def_gro_4w", "def_rev_4w", "def_1w"]].to_string(index=False))
    print("下跌周实际表现（mkt_ret）：",
          df.loc[df["week"].isin(ev["down_weeks"]), ["week", "mkt_ret"]].to_dict("records"))

# ---------- 分位数 ----------
print("\n" + "=" * 100)
print("全样本分位数（含下跌周之前所有周，用于阈值定位）")
print("=" * 100)
for c in sig_cols:
    v = df[c].dropna()
    if len(v) == 0:
        continue
    q = v.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(3)
    print(f"{c:12s} n={len(v):2d} P10={q.iloc[0]:7.3f} P25={q.iloc[1]:7.3f} P50={q.iloc[2]:7.3f} "
          f"P75={q.iloc[3]:7.3f} P90={q.iloc[4]:7.3f}")

# ---------- 信号 vs 未来收益相关 ----------
print("\n" + "=" * 100)
print("信号与未来市场收益相关（同期 mkt_ret、未来1周 fwd1、未来2周 fwd2；Spearman）")
print("=" * 100)
for c in sig_cols:
    tmp = df[["week", c, "mkt_ret", "fwd1", "fwd2"]].dropna()
    if len(tmp) < 20:
        print(f"{c:12s} 样本不足 {len(tmp)}")
        continue
    r0 = tmp[c].corr(tmp["mkt_ret"], method="spearman")
    r1 = tmp[c].corr(tmp["fwd1"], method="spearman")
    r2 = tmp[c].corr(tmp["fwd2"], method="spearman")
    print(f"{c:12s} n={len(tmp):2d} corr(mkt)={r0:+.3f} corr(fwd1)={r1:+.3f} corr(fwd2)={r2:+.3f}")

# ---------- 阈值扫描：把"周信号值"二分后对未来负收益的命中 ----------
# 思路：信号达到极端侧（如 n_rot >= thr）后，未来 fwd1+fwd2 是否显著负
print("\n" + "=" * 100)
print("阈值扫描：信号触发后未来1-2周市场收益分布（事件前是否提前亮灯）")
print("=" * 100)
df["fwd"] = df[["fwd1", "fwd2"]].mean(axis=1, skipna=True)
for c in ["n_rot", "n_lead", "n_weak", "n_mom_neg", "def_gro_4w", "def_rev_4w"]:
    v = df[c].dropna()
    if len(v) < 20:
        continue
    # 对"越大越危险"的信号：n_rot/n_weak/n_mom_neg；对"越小越危险"：n_lead/def_gro_4w 特判
    if c in ("n_rot", "n_weak", "n_mom_neg"):
        pth = v.quantile(0.75)
        sign = ">="
        trig = df[c] >= pth
    else:
        pth = v.quantile(0.25)
        sign = "<="
        trig = df[c] <= pth
    t = df.loc[trig & df["fwd"].notna(), "fwd"]
    nt = df.loc[~trig & df["fwd"].notna(), "fwd"]
    if len(t) < 5 or len(nt) < 5:
        continue
    print(f"{c}: 触发 {sign}{pth:.3f} -> {len(t)}周, 未来1-2周收益 均值{t.mean()*100:+.2f}% 中位{t.median()*100:+.2f}% | 未触发{len(nt)}周 均值{nt.mean()*100:+.2f}% 中位{nt.median()*100:+.2f}%")