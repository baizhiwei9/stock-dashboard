# -*- coding: utf-8 -*-
"""比例化阈值分析：rot_adj/n_common 与 n_rs_lt100/n_factor，保证跨因子数可比
并评估组合触发（两个信号同时达标）的命中/虚警。
"""
import pandas as pd
import numpy as np

df = pd.read_csv("style_signal_backtest_v2.csv")
df["week"] = df["week"].astype(int)

F = pd.read_csv("factor_weekly_raw.csv", dtype={"week_end": np.int64})
WEEKS = sorted(F["week_end"].unique())
piv = F.pivot(index="factor", columns="week_end", values="weekly_long_short_return").sort_index(axis=1)
piv = piv[WEEKS]

prev_quad = None
rows = []
for i, we in enumerate(WEEKS):
    window = WEEKS[max(0, i - 7): i + 1]
    recent = piv[window]
    valid = recent.dropna(how="all").index.tolist()
    recent = recent.loc[valid]
    if recent.shape[1] < 4 or len(valid) < 3:
        prev_quad = None
        rows.append({"week": int(we), "rot_adj": np.nan, "rot_rate": np.nan, "rs_wide_rate": np.nan})
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
    n_rot_adj = np.nan; n_common = len(valid)
    if prev_quad is not None:
        common = [f for f in valid if f in prev_quad]
        if len(common) >= 3:
            n_common = len(common)
            n_rot_adj = sum(1 for f in common if cur[f] != prev_quad[f])
    prev_quad = cur
    rows.append({"week": int(we), "rot_adj": n_rot_adj,
                 "rot_rate": (n_rot_adj / n_common) if pd.notna(n_rot_adj) else np.nan,
                 "rs_wide_rate": float((rs < 100).mean())})
rot = pd.DataFrame(rows)
df = df.merge(rot, on="week", how="left")

ev_down = {20260703, 20260710, 20260717, 20260918, 20260924, 20260930, 20261009}
df["is_down_week"] = df["week"].isin(ev_down).astype(int)
df["down_next2"] = (df["is_down_week"].shift(-1).fillna(0).astype(int) | df["is_down_week"].shift(-2).fillna(0).astype(int)).astype(int)

print("=" * 96)
print("比例化信号时序（rot_rate=旋转因子占比, rs_wide_rate=弱势因子占比）")
print("=" * 96)
keep = ["week", "mkt_ret", "n_factor", "rot_adj", "rot_rate", "rs_wide_rate", "n_rs_lt100", "is_down_week"]
print(df[keep].to_string(index=False))

print("\n" + "=" * 96)
print("比例阈值扫描（触发→未来1-2周下跌的命中/虚警）")
print("=" * 96)
for c, ge, thrs in [("rot_rate", True, [0.35, 0.4, 0.45, 0.5, 0.55]),
                    ("rs_wide_rate", True, [0.5, 0.55, 0.6, 0.65])]:
    for thr in thrs:
        trig = (df[c] >= thr) if ge else (df[c] <= thr)
        sub = df[trig & df[c].notna()]
        if len(sub) == 0:
            continue
        hit = int(sub["down_next2"].sum())
        t = sub["mkt_ret"].mean()
        # 事件前 5 周（起点前1-5周）命中情况
        preA = df[(df['week'] >= 20260529) & (df['week'] <= 20260703)]
        preB = df[(df['week'] >= 20260814) & (df['week'] <= 20260918)]
        hitA = int(((preA[c] >= thr) if ge else (preA[c] <= thr)).sum())
        hitB = int(((preB[c] >= thr) if ge else (preB[c] <= thr)).sum())
        print(f"{c} {('>=' if ge else '<=')} {thr:.2f}: 触发{len(sub):2d}次 命中{hit:2d} ({hit/len(sub)*100:.0f}%) 虚警{len(sub)-hit:2d} | "
              f"同期mkt均值{t*100:+.2f}% | A前6周{hitA}/6 B前6周{hitB}/6")

print("\n" + "=" * 96)
print("组合触发：rot_rate >= 0.4 且 rs_wide_rate >= 0.55")
print("=" * 96)
combo = (df["rot_rate"] >= 0.4) & (df["rs_wide_rate"] >= 0.55)
sub = df[combo & df["rot_rate"].notna()]
print(f"组合触发 {len(sub)} 次:")
for _, r in sub.iterrows():
    print(f"  {int(r['week'])} mkt={r['mkt_ret']*100 if pd.notna(r['mkt_ret']) else float('nan'):+.2f}% "
          f"rot_rate={r['rot_rate']:.2f} rs_wide_rate={r['rs_wide_rate']:.2f} "
          f"is_down={int(r['is_down_week'])} down_next2={int(r['down_next2'])}")