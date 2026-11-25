#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓量横截面对冲 · 快速回测验证（开发期临时脚本）
规格 XSEC_COMMODITY_SPEC-1：
  因子 OI_ROC40 = ln(OI+1).diff(40)；板块内横截面z；w = z/Σ|z|；T日因子→T+1收益
验证项：日频/周频 × 成本0/2/5/8bp；IC；NW-t；多空分解；逐年；回撤
"""
import numpy as np
import pandas as pd

ROOT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f076770c9ffekXUDiMfraoGX1A"
CHEM = {"MA", "TA", "SA", "UR", "V", "PP", "L", "EG", "EB"}
END = pd.Timestamp("2026-10-13")


def build_factor(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p = p[p["main_oi"] > 0]
    p["sector"] = np.where(p["variety"].isin(CHEM), "化工", "农产品")
    p = p.sort_values(["variety", "date"]).reset_index(drop=True)
    p["oi_roc40"] = p.groupby("variety")["main_oi"].transform(lambda s: np.log(s + 1).diff(40))
    # 板块内横截面 z
    def xz(s):
        return (s - s.mean()) / s.std(ddof=1)
    p["oi_z"] = p.groupby(["date", "sector"])["oi_roc40"].transform(xz)
    p["oi_w"] = p["oi_z"] / p.groupby(["date", "sector"])["oi_z"].transform(lambda s: s.abs().sum())
    return p


def pivot_wide(p: pd.DataFrame, col: str) -> pd.DataFrame:
    w = p.pivot_table(index="date", columns="variety", values=col)
    return w.sort_index()


def nw_t(x: pd.Series, lag: int = 20) -> float:
    x = x.dropna()
    n = len(x)
    if n < 10:
        return float("nan")
    mu = x.mean()
    e = x - mu
    def acv(k):
        if k == 0:
            return float((e * e).mean())
        if k >= n:
            return 0.0
        return float((e.iloc[k:] * e.iloc[:-k].values).mean())
    v0 = acv(0)
    se2 = v0 + 2 * sum((1 - k / (lag + 1)) * acv(k) for k in range(1, lag + 1))
    if se2 <= 0 or n <= 1:
        return float("nan")
    return mu / np.sqrt(se2 / n)


def perf(port: pd.Series, years_label: str = "") -> dict:
    port = port.dropna()
    if port.empty:
        return {}
    n = len(port)
    years = n / 244.0
    ann = (1 + port).prod() ** (1 / years) - 1
    vol = port.std() * np.sqrt(244)
    sharpe = ann / vol if vol > 0 else float("nan")
    cum = (1 + port).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    return {"ann": ann, "vol": vol, "sharpe": sharpe, "mdd": dd, "n": n, "t_nw": nw_t(port)}


def run_backtest(p: pd.DataFrame, freq: str = "daily", cost_bps: float = 0.0,
                 start: pd.Timestamp = pd.Timestamp("2021-02-01")) -> dict:
    """组合收益序列：port_t = Σ w_{t-1} * r_t；成本按调仓换手×单边bps"""
    w = pivot_wide(p, "oi_w")
    r = pivot_wide(p, "ret_hold")
    dts = w.index
    # 权重序列滞后一日（T日信号 → T+1收益）
    w_lag = w.shift(1)
    combo = (w_lag * r).sum(axis=1, min_count=1)
    combo = combo[(combo.index >= start) & (combo.index <= END)]

    if freq == "weekly":
        # 每周最后交易日调仓；非调仓日按上周权重持有
        period = pd.Series(dts).dt.to_period("W-FRI")
        rebs = set(pd.Series(dts).groupby(period.values).max().values)
        hold_w = w.shift(1).copy()
        # 构造当前生效权重：调仓日更新为目标权重，其余延续
        eff_w = hold_w.copy()
        active = None
        for d in hold_w.index:
            if d in rebs:
                active = hold_w.loc[d].copy()
            if active is not None:
                eff_w.loc[d] = active
            else:
                eff_w.loc[d] = np.nan
        combo = (eff_w * r).sum(axis=1, min_count=1)
        combo = combo[(combo.index >= start) & (combo.index <= END)]
        # 换手：调仓日 |Δw|（相对上一期生效权重）；非调仓日 0
        turnover = pd.Series(0.0, index=combo.index)
        prev_eff = None
        for d in combo.index:
            if d in rebs:
                cur = eff_w.loc[d].fillna(0.0)
                base = prev_eff if prev_eff is not None else cur * 0.0
                turn = (cur - base).abs().sum()
                turnover.loc[d] = turn
                prev_eff = cur
        combo = combo - turnover * (cost_bps / 1e4)
    else:
        # 日频：每日调仓，换手 = Σ|Δw|
        w_eff = w_lag.fillna(0.0)
        turn = w_eff.diff().abs().sum(axis=1, min_count=1).fillna(0.0)
        turn = turn[(turn.index >= start) & (turn.index <= END)]
        combo = combo - turn * (cost_bps / 1e4)
    combo.name = "port"
    return {"port": combo, "perf": perf(combo)}


def ic_report(p: pd.DataFrame, start=pd.Timestamp("2021-02-01")):
    f = pivot_wide(p, "oi_roc40")
    r = pivot_wide(p, "ret_hold").shift(-1)  # T+1收益
    ic = []
    for d in f.index:
        if d < start or d > END:
            continue
        a = f.loc[d].dropna()
        b = r.loc[d].reindex(a.index).dropna()
        idx = a.index.intersection(b.index)
        if len(idx) >= 5 and b[idx].std() > 0 and a[idx].std() > 0:
            ic.append(a[idx].rank().corr(b[idx].rank()))
    s = pd.Series(ic)
    return {"ic_mean": s.mean(), "ic_t": nw_t(s, 20) if len(s) > 5 else float("nan"), "ic_n": len(s)}


def yearly(port: pd.Series) -> pd.Series:
    return (1 + port).groupby(port.index.year).prod() - 1


def main():
    panel = pd.read_csv(f"{ROOT}/data/full/panel_full.csv", parse_dates=["date"])
    p = build_factor(panel)
    print("== 因子覆盖：oi_roc40 有效比例", round(p.oi_roc40.notna().mean(), 3),
          "oi_z", round(p.oi_z.notna().mean(), 3))
    # 预热后评估起点
    warm = panel[panel.date >= "2021-01-01"].date.min()

    print("\n== Rank IC（oi_roc40 → T+1收益）")
    ic = ic_report(p)
    print(f"IC均值 = {ic['ic_mean']:+.4f}，IC t_NW = {ic['ic_t']:+.2f}，n = {ic['ic_n']}")

    print("\n== 频率 × 成本 矩阵")
    rows = []
    for freq in ("daily", "weekly"):
        for cb in (0.0, 2.0, 5.0, 8.0):
            res = run_backtest(p, freq, cb)
            pf = res["perf"]
            rows.append((freq, cb, pf))
    print(f"{'freq':7s} {'cost':>5s} {'年化':>9s} {'波动':>8s} {'Sharpe':>7s} {'MDD':>8s} {'t_NW':>6s} {'n':>5s}")
    for freq, cb, pf in rows:
        print(f"{freq:7s} {cb:5.1f} {pf['ann']*100:8.2f}% {pf['vol']*100:7.2f}% "
              f"{pf['sharpe']:7.2f} {pf['mdd']*100:7.1f}% {pf['t_nw']:6.2f} {pf['n']:5d}")

    print("\n== 多空分解（日频毛）")
    w = pivot_wide(p, "oi_w").shift(1)
    r = pivot_wide(p, "ret_hold")
    d0 = pd.Timestamp("2021-02-01")
    long_leg = (w.clip(lower=0) * r).sum(axis=1, min_count=1)
    short_leg = (w.clip(upper=0) * r).sum(axis=1, min_count=1)
    long_leg = long_leg[(long_leg.index >= d0) & (long_leg.index <= END)]
    short_leg = short_leg[(short_leg.index >= d0) & (short_leg.index <= END)]
    print(f"多头腿年化 {perf(long_leg)['ann']*100:+.2f}% ｜ 空头腿年化 {perf(short_leg)['ann']*100:+.2f}%")

    print("\n== 逐年（日频毛 / 周频毛）")
    d_res = run_backtest(p, "daily", 0.0)["port"]
    w_res = run_backtest(p, "weekly", 0.0)["port"]
    yd = yearly(d_res)
    yw = yearly(w_res)
    for y in sorted(set(yd.index) | set(yw.index)):
        print(f"{y}: 日频 {yd.get(y, float('nan'))*100:+7.2f}% ｜ 周频 {yw.get(y, float('nan'))*100:+7.2f}%")

    print("\n== 周频持仓示意（最近3个调仓日）")
    dts = w.index
    period = pd.Series(dts).dt.to_period("W-FRI")
    rebs = sorted(set(pd.Series(dts).groupby(period.values).max().values))
    for d in rebs[-3:]:
        d = pd.Timestamp(d)
        row = p[p.date == d].dropna(subset=["oi_z"]).sort_values("oi_z", ascending=False)
        top3 = row.head(3)["variety"].tolist()
        bot3 = row.tail(3)["variety"].tolist()
        print(f"{d.date()}: 多 {top3} ｜ 空 {bot3}")

    # 分组收益诊断：按每日 oi_z 三分组，看次日收益
    print("\n== 分组诊断（每日按 oi_z 分组次日收益均值）")
    start_diag = pd.Timestamp("2021-02-01")
    z = pivot_wide(p, "oi_z")
    r = pivot_wide(p, "ret_hold").shift(-1)
    g = {"上1/3": [], "中1/3": [], "下1/3": []}
    for d in z.index:
        if d < start_diag or d > END:
            continue
        a = z.loc[d].dropna()
        if len(a) < 6:
            continue
        qs = a.quantile([1/3, 2/3])
        rr = r.loc[d]
        for k, (lo, hi) in (("上1/3", (qs.iloc[1], np.inf)), ("中1/3", (qs.iloc[0], qs.iloc[1])), ("下1/3", (-np.inf, qs.iloc[0]))):
            sel = a[(a >= lo) & (a < hi)].index
            g[k] += rr.reindex(sel).dropna().tolist()
    for k, v in g.items():
        print(f"{k}: 平均次日收益 {np.mean(v)*100:+.3f}%（n={len(v)}）")

    # 板块暴露检查：化工/农产品净敞口
    print("\n== 板块净敞口（日频权重日度均值）")
    piv_z = pivot_wide(p, "oi_z")
    piv_w = pivot_wide(p, "oi_w")
    chem = [v for v in piv_w.columns if v in CHEM]
    agri = [v for v in piv_w.columns if v not in CHEM]
    net_c = (piv_w[chem].sum(axis=1)).mean()
    net_a = (piv_w[agri].sum(axis=1)).mean()
    print(f"化工净敞口均值 {net_c:+.3f} ｜ 农产品净敞口均值 {net_a:+.3f}")


if __name__ == "__main__":
    main()