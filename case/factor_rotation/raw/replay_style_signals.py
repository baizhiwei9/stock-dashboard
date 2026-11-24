# -*- coding: utf-8 -*-
"""逐周 RRG 旋转 + 风格信号回放（修复版）
目的：检验"因子旋转流动"能否提前发现风格变化（以 2026-07、2026-09末-10初两段下跌为样本）。
口径：
- 因子周收益: factor_weekly_raw.csv weekly_long_short_return（we 截面、下周实现）
- 市场周收益: 与因子同口径，mkt_ret[we] = 全样本等权下周收益（对齐 factor_engine bench）
- 逐周 RRG: 对每个 we 取最近 min(8, 可用) 周窗口，按 rrg_plot.py 同式算 rs/mom
- 信号:
  * 四象限计数 n_lead/n_impr/n_weak/n_lag（可用因子）
  * 因子动量/相对强度恶化的广度 n_mom_neg、n_rs_lt100
  * 风格差分 def_gro_4w / def_rev_4w / rev_gro_4w（4周累计多空收益差）
  * 旋转强度 n_rot（象限迁移因子数）、rs 离散度 rs_std
  * 前瞻收益 fwd1/fwd2（we 之后 1/2 周市场收益，用于评估"提前"）
运行: python3 replay_style_signals.py （不依赖 pylibs，用系统 pandas）
"""
import pandas as pd
import numpy as np

RAW = "."

# ---------------- 1. 因子周收益 ----------------
F = pd.read_csv(f"{RAW}/factor_weekly_raw.csv", dtype={"week_end": np.int64})
WEEKS = sorted(F["week_end"].unique())
piv = F.pivot(index="factor", columns="week_end", values="weekly_long_short_return").sort_index(axis=1)
piv = piv[WEEKS]
gmeta = F.drop_duplicates("factor").set_index("factor")["group"].to_dict()
dmeta = F.drop_duplicates("factor").set_index("factor")["direction"].to_dict()

# ---------------- 2. 市场周收益（等权，与因子同口径） ----------------
d = pd.read_csv(f"{RAW}/daily_260d.csv",
                usecols=["ts_code", "trade_date", "pct_chg"],
                dtype={"ts_code": str, "trade_date": np.int32, "pct_chg": np.float32})
d["dt"] = pd.to_datetime(d["trade_date"].astype(str))
d["wk"] = d["dt"].dt.to_period("W-FRI")
# 每周最后交易日 = 该周 week_end（与 factor_engine weekly_last 一致）
week_last = d.groupby("wk")["trade_date"].max()
d["week_end"] = d["wk"].map(week_last)
ret = d.pivot_table(index="trade_date", columns="ts_code", values="pct_chg") / 100.0
dates = sorted(ret.index.tolist())
fwd = {}
for we in WEEKS:
    if we not in dates:
        continue
    idx = dates.index(we)
    nxt = dates[idx + 1: idx + 6]
    if not nxt:
        continue
    rr = (1.0 + ret.loc[nxt]).prod(skipna=True) - 1.0
    fwd[we] = float(rr.mean(skipna=True))
bench = pd.Series(fwd, name="mkt_ret")

# ---------------- 3. 逐周 RRG 与风格信号 ----------------
styles = {
    "def": [f for f, g in gmeta.items() if g == "波动"],          # 防御：波动类（direction=-1 已含在 LS 收益中）
    "rev": [f for f, g in gmeta.items() if g == "反转动量"],      # 反转/动量
    "gro": [f for f, g in gmeta.items() if g in ("成长", "盈利")],  # 进攻：成长+盈利
    "val": [f for f, g in gmeta.items() if g == "估值"],
}

def style_diff(we, a_list, b_list, lookback=4):
    """两个风格因子组 4 周累计 LS 收益差（a 组相对 b 组）"""
    cols = [w for w in WEEKS if w <= we][-lookback:]
    if len(cols) < 2:
        return np.nan
    a = piv.loc[[f for f in a_list if f in piv.index], cols].mean(skipna=True).sum()
    b = piv.loc[[f for f in b_list if f in piv.index], cols].mean(skipna=True).sum()
    return float(a - b)

def quadrant(rs, mom):
    if rs > 100 and mom > 0:
        return "lead"
    if rs < 100 and mom > 0:
        return "impr"
    if rs > 100 and mom < 0:
        return "weak"
    return "lag"

prev_quad = None
rows = []
for i, we in enumerate(WEEKS):
    window = WEEKS[max(0, i - 7): i + 1]  # 最近 ≤8 周（含本周），与 rrg_plot 一致
    recent = piv[window]
    # 仅保留该窗口内数据非全缺失的因子（真实反映当时可见的因子集）
    valid = recent.dropna(how="all").index.tolist()
    recent = recent.loc[valid]
    if recent.shape[1] < 4 or len(valid) < 3:
        rows.append({"week": int(we), "n_factor": len(valid)})
        continue
    rs_raw = recent.sum(axis=1)
    rs = 100 + (rs_raw - rs_raw.mean()) / (rs_raw.std() + 1e-12) * 12
    k = min(3, recent.shape[1])
    if recent.shape[1] > k:
        mom_raw = recent.iloc[:, -k:].sum(axis=1) - recent.iloc[:, :-k].sum(axis=1)
    else:
        mom_raw = recent.iloc[:, -1]
    mom = (mom_raw - mom_raw.mean()) / (mom_raw.std() + 1e-12) * 12

    qs = [quadrant(r, m) for r, m in zip(rs.values, mom.values)]
    n_lead = qs.count("lead"); n_impr = qs.count("impr")
    n_weak = qs.count("weak"); n_lag = qs.count("lag")
    n_mom_neg = int((mom < 0).sum())
    n_rs_lt100 = int((rs < 100).sum())
    # 旋转强度：相对上周迁移象限的因子数
    if prev_quad is not None:
        cur = dict(zip(valid, qs))
        n_rot = sum(1 for f in valid if cur.get(f) != prev_quad.get(f))
    else:
        n_rot = np.nan
    prev_quad = dict(zip(valid, qs))

    r = {
        "week": int(we),
        "mkt_ret": bench.get(we, np.nan),
        "fwd1": bench.get(WEEKS[i + 1], np.nan) if i + 1 < len(WEEKS) else np.nan,
        "fwd2": bench.get(WEEKS[i + 2], np.nan) if i + 2 < len(WEEKS) else np.nan,
        "n_factor": len(valid),
        "n_lead": n_lead, "n_impr": n_impr, "n_weak": n_weak, "n_lag": n_lag,
        "n_mom_neg": n_mom_neg, "n_rs_lt100": n_rs_lt100, "n_rot": n_rot,
        "rs_std": float(rs.std()), "mom_std": float(mom.std()),
        "rs_mean": float(rs.mean()), "mom_mean": float(mom.mean()),
        "def_gro_4w": style_diff(we, styles["def"], styles["gro"]),
        "def_rev_4w": style_diff(we, styles["def"], styles["rev"]),
        "rev_gro_4w": style_diff(we, styles["rev"], styles["gro"]),
        "val_gro_4w": style_diff(we, styles["val"], styles["gro"]),
        "def_1w": style_diff(we, styles["def"], styles["gro"], 1),
    }
    rows.append(r)

out = pd.DataFrame(rows)
out.to_csv(f"{RAW}/style_signal_backtest_v2.csv", index=False)
print(out.to_string(index=False))
print("\nrows:", len(out), "| mkt_ret non-nan:", int(out['mkt_ret'].notna().sum()))