# -*- coding: utf-8 -*-
"""触发清单 + 命中/虚警明细 + AUC 粗略评估
目标：为 rot_adj、n_rs_lt100、n_lead 选阈值，量化命中/虚警。
"""
import pandas as pd
import numpy as np

df = pd.read_csv("style_signal_backtest_v2.csv")
df["week"] = df["week"].astype(int)

# 合并修正后的 rot_adj（从 factor_weekly_raw 重算）
F = pd.read_csv("factor_weekly_raw.csv", dtype={"week_end": np.int64})
WEEKS = sorted(F["week_end"].unique())
piv = F.pivot(index="factor", columns="week_end", values="weekly_long_short_return").sort_index(axis=1)
piv = piv[WEEKS]
prev_quad = None
rot_rows = []
for i, we in enumerate(WEEKS):
    window = WEEKS[max(0, i - 7): i + 1]
    recent = piv[window]
    valid = recent.dropna(how="all").index.tolist()
    recent = recent.loc[valid]
    if recent.shape[1] < 4 or len(valid) < 3:
        prev_quad = None
        rot_rows.append({"week": int(we), "rot_adj": np.nan})
        continue
    rs_raw = recent.sum(axis=1)
    rs = 100 + (rs_raw - rs_raw.mean()) / (rs_raw.std() + 1e-12) * 12
    k = min(3, recent.shape[1])
    if recent.shape[1] > k:
        mom_raw = recent.iloc[:, -k:].sum(axis=1) - recent.iloc[:, :-k].sum(axis=1)
    else:
        mom_raw = recent.iloc[:, -1]
    mom = (mom_raw - mom_raw.mean()) / (mom_raw.std() + 1e-12) * 12
    def quad(r, m):
        return "lead" if (r > 100 and m > 0) else ("impr" if (r < 100 and m > 0) else ("weak" if (r > 100 and m < 0) else "lag"))
    cur = {f: quad(rs[f], mom[f]) for f in valid}
    if prev_quad is not None:
        common = [f for f in valid if f in prev_quad]
        n_rot_adj = sum(1 for f in common if cur[f] != prev_quad[f]) if len(common) >= 3 else np.nan
    else:
        n_rot_adj = np.nan
    prev_quad = cur
    rot_rows.append({"week": int(we), "rot_adj": n_rot_adj})
rot = pd.DataFrame(rot_rows)
df = df.merge(rot, on="week", how="left")

# 未来收益：fwd = we 之后 5 个交易日（即下周实际）；fwd1/fwd2 对应当前周后第1/2个截面周的mkt_ret
df["fwd"] = df["fwd1"].fillna(df["mkt_ret"].shift(-1))

# 事件窗口标记（真实下跌周）
ev_down = {20260703, 20260710, 20260717, 20260918, 20260924, 20260930, 20261009}
df["is_down_week"] = df["week"].isin(ev_down).astype(int)
# "提前命中"：在当前周触发信号，未来1-2周出现下跌周
df["down_next2"] = (df["is_down_week"].shift(-1).fillna(0).astype(int) | df["is_down_week"].shift(-2).fillna(0).astype(int)).astype(int)

print("=" * 96)
print("候选阈值：触发周明细（含是否提前命中未来1-2周下跌）")
print("=" * 96)

def show(c, ge, thr):
    trig = (df[c] >= thr) if ge else (df[c] <= thr)
    sub = df[trig & df[c].notna()].copy()
    print(f"\n### {c} {'>=' if ge else '<='} {thr}：共 {len(sub)} 次触发")
    for _, r in sub.iterrows():
        mark = " <==" if r["down_next2"] else ""
        print(f"  {int(r['week'])} mkt={r['mkt_ret']*100:+.2f}% fwd1={r['fwd1']*100 if pd.notna(r['fwd1']) else float('nan'):+.2f}% "
              f"fwd2={r['fwd2']*100 if pd.notna(r['fwd2']) else float('nan'):+.2f}% {c}={r[c]}{mark}")

show("rot_adj", True, 10)
show("n_rs_lt100", True, 12)
show("n_lead", False, 2)

print("\n" + "=" * 96)
print("命中率概览（触发→未来1-2周是否出现下跌周）")
print("=" * 96)
for c, ge, thr in [("rot_adj", True, 8), ("rot_adj", True, 10), ("rot_adj", True, 12),
                   ("n_rs_lt100", True, 12), ("n_lead", False, 2), ("n_lead", False, 3)]:
    trig = (df[c] >= thr) if ge else (df[c] <= thr)
    sub = df[trig & df[c].notna()]
    if len(sub) == 0:
        continue
    hit = int(sub["down_next2"].sum())
    print(f"{c} {('>=' if ge else '<=')} {thr:2d}: 触发{len(sub):2d}次, 未来1-2周有下跌 {hit:2d}次 ({hit/len(sub)*100:.0f}%), "
          f"未来1-2周无下跌(虚警) {len(sub)-hit:2d}次")
    # 全样本下跌周总数
print("全样本下跌周数（mkt_ret<-1.5%）：", int((df['mkt_ret'] < -0.015).sum()), "/", int(df['mkt_ret'].notna().sum()))

print("\n" + "=" * 96)
print("mkt_ret 分布（用于定义'下跌周'阈值参考）")
print("=" * 96)
print(df[["week", "mkt_ret"]].dropna().sort_values("mkt_ret").head(12).to_string(index=False))