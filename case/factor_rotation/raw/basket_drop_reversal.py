# -*- coding: utf-8 -*-
"""剔除反转类因子后的加权篮子对照回测
A  现行 5 因子（三个月反转5 / EPTTM一年分位点4 / 一个月反转3 / 预期PEG2 / 公募重仓1）
B  现行篮子剔除两个反转因子后，剩余 3 因子按历史周均超额重新归一化权重
C  上一轮新篮子（预期EPTTM / 预期净利润环比 / 单季净利同比增速 / 单季EP / 单季ROE，权重=历史周均超额归一化）

口径：篮子周度超额 = Σ w_f × 因子周度超额(f, t) / Σw，其中因子周度超额取自
      raw/factor_weekly_raw.csv 的 excess_vs_benchmark（单因子前 20% 组相对全样本等权的超额）。
      与上一轮结论同口径（现行篮子复现 −0.083%/周、胜率 50.9%）。
输出：output/basket_compare.csv、output/basket_weights.csv、output/basket_weekly.csv
"""
import os
import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))

ex = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"), dtype={"week_end": str})
ex = ex.pivot_table(index="week_end", columns="factor", values="excess_vs_benchmark").sort_index()
FULL_MEAN = ex.mean()

CUR = ["三个月反转", "EPTTM一年分位点", "一个月反转", "预期PEG", "公募重仓"]
CUR_W = {f: len(CUR) - i for i, f in enumerate(CUR)}          # 5/4/3/2/1
REST = [f for f in CUR if f not in ("三个月反转", "一个月反转")]
NEW = ["预期EPTTM", "预期净利润环比", "单季净利同比增速", "单季EP", "单季ROE"]

PERIODS = [("全区间", "20250101", "20261231"), ("2025", "20250101", "20251231"),
           ("2026H1", "20260101", "20260630"), ("2026H2", "20260701", "20261231")]


def stats(x):
    x = x.dropna()
    n = len(x)
    if n < 2:
        return dict(mean=np.nan, win=np.nan, t=np.nan, n=n)
    return dict(mean=x.mean() * 100, win=(x > 0).mean() * 100,
                t=x.mean() / x.std(ddof=1) * np.sqrt(n), n=n)


def basket(weights, cols, raw_weights=False):
    """weights: dict f->w。raw_weights=True 时不做 /Σw（保留净敞口，供多空口径）。"""
    sub = ex[cols]
    wt = pd.Series({k: float(v) for k, v in dict(weights).items()})
    num = (sub.fillna(0) * wt).sum(axis=1)
    total = float(np.sum(wt.to_numpy()))
    return num if raw_weights else num / total


# ---------- 权重 ----------
m_rest = FULL_MEAN[REST]
wB_literal = m_rest / m_rest.sum()                     # 字面口径：∝历史周均超额，Σw=1（含负权重）
wB_abs = m_rest / m_rest.abs().sum()                   # 净敞口口径：∝历史周均超额，Σ|w|=1
wB_pos = m_rest[m_rest > 0] / m_rest[m_rest > 0].sum()  # 仅保留历史周均超额为正的剩余因子
wB_eq = {f: 1.0 for f in REST}
m_new = FULL_MEAN[NEW]
wC = m_new / m_new.sum()
wA = pd.Series(CUR_W)

print("=" * 78)
print("剩余因子（剔除三个月反转/一个月反转后）历史周均超额：")
for f, v in m_rest.items():
    print(f"  {f:<16} {v*100:+.4f}%/周" + ("   ← 负，无法作为正权重" if v < 0 else ""))
print("\nB 字面口径权重(∝周均超额, Σw=1):", {k: round(v, 3) for k, v in wB_literal.items()})
print("B 净敞口口径权重(∝周均超额, Σ|w|=1):", {k: round(v, 3) for k, v in wB_abs.items()})
print("B 仅正超额剩余因子权重:", {k: round(v, 3) for k, v in wB_pos.items()})

# ---------- 篮子序列 ----------
series = {
    "A 现行5因子(5/4/3/2/1)": basket(CUR_W, CUR),
    "B1 剩余3因子·字面口径(Σw=1,含负权重)": basket(wB_literal, REST),
    "B2 剩余3因子·净敞口口径(Σ|w|=1,多空)": basket(wB_abs, REST, raw_weights=True),
    "B3 仅正超额因子(EPTTM一年分位点)": basket(wB_pos, list(wB_pos.index)),
    "B4 剩余3因子·等权": basket(wB_eq, REST),
    "C 上一轮新篮子(5因子)": basket(wC, NEW),
}
Wk = pd.DataFrame(series)
Wk.to_csv(os.path.join(OUT, "basket_weekly.csv"), encoding="utf-8-sig")
pd.DataFrame({"A": wA, "B_literal": wB_literal, "B_abs": wB_abs, "B_pos": wB_pos, "C": wC}
             ).to_csv(os.path.join(OUT, "basket_weights.csv"), encoding="utf-8-sig")

rows = []
for name, s in series.items():
    acc = {"篮子": name}
    for pname, lo, hi in PERIODS:
        st = stats(s[(s.index >= lo) & (s.index <= hi)])
        acc[f"{pname}_周均%"] = round(st["mean"], 4)
        acc[f"{pname}_胜率%"] = round(st["win"], 1)
        acc[f"{pname}_t"] = round(st["t"], 2)
        acc[f"{pname}_n"] = st["n"]
    rows.append(acc)
cmp = pd.DataFrame(rows)
cmp.to_csv(os.path.join(OUT, "basket_compare.csv"), index=False, encoding="utf-8-sig")

print("\n" + "=" * 78)
print("三套篮子分区间样本外表现（周度超额口径）")
hdr = f"{'篮子':<38}" + "".join(f"{p:>22}" for p, _, _ in PERIODS)
print(hdr)
for _, r in cmp.iterrows():
    line = f"{r['篮子']:<38}"
    for p, _, _ in PERIODS:
        line += f"  {r[p+'_周均%']:>7.3f}% {r[p+'_胜率%']:>5.1f}% t{r[p+'_t']:>6.2f} n{r[p+'_n']:>3}"
    print(line)

print("\n对照：单因子全区间周均超额（前 6 / 后 4）")
print(FULL_MEAN.sort_values(ascending=False).head(6).mul(100).round(3).to_string())
print(FULL_MEAN.sort_values().head(4).mul(100).round(3).to_string())