# -*- coding: utf-8 -*-
"""分析 v2：相关性（rank-Pearson 替代 Spearman）+ 修正 n_rot + 阈值扫描 + 迁移方向
不依赖 scipy。
"""
import pandas as pd
import numpy as np

df = pd.read_csv("style_signal_backtest_v2.csv")
df["week"] = df["week"].astype(int)

sig_cols = ["n_lead", "n_impr", "n_weak", "n_lag", "n_mom_neg", "n_rs_lt100", "n_rot",
            "def_gro_4w", "def_rev_4w", "rev_gro_4w", "val_gro_4w"]

def rho(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 20:
        return np.nan, int(m.sum())
    ra, rb = a[m].rank(), b[m].rank()
    return float(np.corrcoef(ra, rb)[0, 1]), int(m.sum())

print("=" * 90)
print("信号 vs 区间收益 Spearman(rank-Pearson) 相关")
print("=" * 90)
for c in sig_cols:
    r0, n0 = rho(df[c], df["mkt_ret"])
    r1, n1 = rho(df[c], df["fwd1"])
    r2, n2 = rho(df[c], df["fwd2"])
    print(f"{c:12s} corr(mkt)={r0:+.3f}(n={n0}) corr(fwd1)={r1:+.3f}(n={n1}) corr(fwd2)={r2:+.3f}(n={n2})")

# ---------- 修正 n_rot 的扩容虚增：仅统计两个窗口都存在的因子 ----------
RAW_F = "factor_weekly_raw.csv"
F = pd.read_csv(RAW_F, dtype={"week_end": np.int64})
WEEKS = sorted(F["week_end"].unique())
piv = F.pivot(index="factor", columns="week_end", values="weekly_long_short_return").sort_index(axis=1)
piv = piv[WEEKS]
gmeta = F.drop_duplicates("factor").set_index("factor")["group"].to_dict()

styles = {
    "def": [f for f, g in gmeta.items() if g == "波动"],
    "rev": [f for f, g in gmeta.items() if g == "反转动量"],
    "gro": [f for f, g in gmeta.items() if g in ("成长", "盈利")],
    "val": [f for f, g in gmeta.items() if g == "估值"],
}
prev_quad = None
rot_rows = []
for i, we in enumerate(WEEKS):
    window = WEEKS[max(0, i - 7): i + 1]
    recent = piv[window]
    valid = recent.dropna(how="all").index.tolist()
    recent = recent.loc[valid]
    if recent.shape[1] < 4 or len(valid) < 3:
        prev_quad = None
        rot_rows.append({"week": int(we), "rot_adj": np.nan,
                         "lead2lag": np.nan, "lag2lead": np.nan, "to_lead": np.nan, "to_lag": np.nan})
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
        if r > 100 and m > 0: return "lead"
        if r < 100 and m > 0: return "impr"
        if r > 100 and m < 0: return "weak"
        return "lag"
    cur = {f: quad(rs[f], mom[f]) for f in valid}
    n_rot_adj = np.nan
    lead2lag = lag2lead = to_lead = to_lag = np.nan
    if prev_quad is not None:
        common = [f for f in valid if f in prev_quad]
        if len(common) >= 3:
            n_rot_adj = sum(1 for f in common if cur[f] != prev_quad[f])
            lead2lag = sum(1 for f in common if prev_quad[f] == "lead" and cur[f] == "lag")
            lag2lead = sum(1 for f in common if prev_quad[f] == "lag" and cur[f] == "lead")
            to_lead = sum(1 for f in common if cur[f] == "lead" and prev_quad[f] != "lead")
            to_lag = sum(1 for f in common if cur[f] == "lag" and prev_quad[f] != "lag")
    prev_quad = cur
    rot_rows.append({"week": int(we), "rot_adj": n_rot_adj, "lead2lag": lead2lag,
                     "lag2lead": lag2lead, "to_lead": to_lead, "to_lag": to_lag})
rot = pd.DataFrame(rot_rows)
df = df.merge(rot, on="week", how="left")
for c in ["rot_adj", "lead2lag", "lag2lead", "to_lead", "to_lag"]:
    r0, n0 = rho(df[c], df["mkt_ret"])
    r1, n1 = rho(df[c], df["fwd1"])
    r2, n2 = rho(df[c], df["fwd2"])
    print(f"{c:10s} corr(mkt)={r0:+.3f}(n={n0}) corr(fwd1)={r1:+.3f}(n={n1}) corr(fwd2)={r2:+.3f}(n={n2})")

print("\n" + "=" * 90)
print("修正后信号时序（rot_adj/lead2lag/lag2lead/to_lead/to_lag + 两段下跌标注）")
print("=" * 90)
ev = {20260703: "A起点", 20260710: "A跌2", 20260717: "A跌3", 20260918: "B起点", 20260924: "B跌2", 20260930: "B跌3", 20261009: "B跌4"}
keep = ["week", "mkt_ret", "n_factor", "n_lead", "n_weak", "n_rot", "rot_adj", "lead2lag", "lag2lead", "to_lead", "to_lag", "n_rs_lt100"]
sub = df[keep].copy()
sub["mark"] = sub["week"].map(lambda w: ev.get(int(w), ""))
print(sub[sub["week"] >= 20260501].to_string(index=False))

print("\n" + "=" * 90)
print("阈值扫描（触发后 fwd1/fwd2 平均收益；比较触发 vs 未触发；事件是否被覆盖）")
print("=" * 90)
df["fwd"] = df[["fwd1", "fwd2"]].mean(axis=1, skipna=True)
for c, ge, thrs in [
    ("rot_adj", True, [6, 8, 10, 12]),
    ("n_rs_lt100", True, [12, 14, 16, 18]),
    ("n_rot", True, [8, 10, 12, 15]),
    ("n_lead", False, [2, 3]),
    ("n_weak", True, [8]),
]:
    base = df.dropna(subset=["fwd", c])
    print(f"--- {c} (触发方向 {'>=' if ge else '<='}) ---")
    for thr in thrs:
        trig = (base[c] >= thr) if ge else (base[c] <= thr)
        t = base.loc[trig, "fwd"]
        nt = base.loc[~trig, "fwd"]
        if len(t) < 3 or len(nt) < 3:
            print(f"  thr={thr}: 触发{len(t)}周 样本不足")
            continue
        # 两段事件中"事件起点周及之前1-4周"是否被触发覆盖
        preA = df[(df['week'] >= 20260605) & (df['week'] <= 20260703)]
        preB = df[(df['week'] >= 20260821) & (df['week'] <= 20260918)]
        hitA = ((preA[c] >= thr) if ge else (preA[c] <= thr)).sum()
        hitB = ((preB[c] >= thr) if ge else (preB[c] <= thr)).sum()
        print(f"  thr={thr:3d}: 触发{len(t):2d}周 未来收益均值{t.mean()*100:+.2f}% 中位{t.median()*100:+.2f}% | "
              f"未触发{len(nt):2d}周 均值{nt.mean()*100:+.2f}% 中位{nt.median()*100:+.2f}% | "
              f"A前5周命中{hitA}/5 B前5周命中{hitB}/5")