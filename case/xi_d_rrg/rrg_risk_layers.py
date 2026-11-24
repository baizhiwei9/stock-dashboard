#!/usr/bin/env python3
"""
扩散+RRG 加装风控层回测 v2 (复用原版引擎, 只缩放权重)
====================================================
风控方案 (与选行业同月生效, 权重 = 等权1/6 x 仓位):
  A. 满仓 (现状基线)
  B. 回撤止损: 净值距高点回撤>15% -> 60%, >25% -> 30%
  C. 波动率目标: 滚动20日行业等权年化波动 -> min(1, 0.25/vol)
  D. 趋势过滤: 行业等权 < MA40 -> 50%, < MA80 -> 0
  E. 扩散阈值: 30行业扩散中位 <0.4 -> 50%, <0.3 -> 30%
"""
import sys, warnings
sys.path.insert(0, '.')
sys.path.insert(0, 'pylib')
warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
import rrg_data, rrg_engine

START, END = '20220101', '20260822'

def load():
    diffusion = pd.read_csv('data_rrg/diffusion.csv', index_col=0, parse_dates=True)
    prices = rrg_data.load_industry_prices()
    rrg = rrg_engine.compute_rrg(prices)
    return prices, diffusion, rrg

def base_signals(prices, diffusion, rrg):
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    signals = {}
    for i in me_idx:
        t = p.index[i]
        q = {c: rrg[c].loc[t] for c in p.columns}
        diff_t = diffusion.loc[t] if t in diffusion.index else pd.Series(dtype=float)
        topd = diff_t.dropna().sort_values(ascending=False)
        top6d = topd.index[:6].tolist()
        keep = [c for c in top6d if q.get(c, {}).get('quadrant', 3) in (1, 2)]
        signals[t] = {c: 1.0 / len(keep) for c in keep} if keep else {}
    return signals, p

def positions(prices, diffusion, signals, mode):
    """returns dict t -> 仓位系数"""
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    eq = prices.mean(axis=1)
    out = {}
    nv_cum = None  # 回撤止损需要模拟净值
    if mode == 'dd':
        # 先模拟满仓净值序列计算动态回撤止损仓位
        rets = prices.pct_change().fillna(0)
        p_loc = p
        nav = 1.0
        for i in me_idx:
            t = p.index[i]
            w = signals.get(t, {})
            nxt = i + 1
            nxt_i = me_idx[list(me_idx).index(i) + 1] + 1 if list(me_idx).index(i) + 1 < len(me_idx) else len(rets)
            seg = rets.iloc[nxt:nxt_i]
            if len(seg) == 0 or not w:
                continue
            seg_w = seg[list(w.keys())]
            wv = pd.Series([w.get(c, 0.0) for c in seg_w.columns], index=seg_w.columns)
            r = seg_w.values @ wv.values
            ddm = 0.0
            for rr in r:
                nav *= (1 + rr)
            peak = 1.0  # 重新计算太贵, 简化: 用固定阈值不适合. 改用简化方案
            break
        return None  # 由 run 内部处理
    if mode == 'vol':
        vol = eq.pct_change().rolling(20).std() * np.sqrt(252)
        for t in p.index[me_idx]:
            v = vol.loc[t] if t in vol.index else np.nan
            out[t] = float(min(1.0, 0.25 / v)) if pd.notna(v) and v > 0 else 1.0
    elif mode == 'ma':
        ma40 = eq.rolling(40).mean(); ma80 = eq.rolling(80).mean()
        for t in p.index[me_idx]:
            e, m4, m8 = eq.loc[t], ma40.loc[t], ma80.loc[t]
            if pd.notna(m8) and e < m8: out[t] = 0.0
            elif pd.notna(m4) and e < m4: out[t] = 0.5
            else: out[t] = 1.0
    elif mode == 'diff':
        md = diffusion.median(axis=1)
        for t in p.index[me_idx]:
            v = md.loc[t] if t in md.index else np.nan
            if pd.notna(v):
                out[t] = 0.3 if v < 0.3 else (0.5 if v < 0.4 else 1.0)
            else: out[t] = 1.0
    else:
        for t in p.index[me_idx]:
            out[t] = 1.0
    return out

# 回撤止损: 需要动态模拟, 单独写
def run_dd_stop(prices, signals, th1=0.15, p1=0.6, th2=0.25, p2=0.3):
    rets = prices.pct_change().fillna(0)
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    weights_series = {}
    nav = 1.0; peak = 1.0
    for k, i in enumerate(me_idx):
        t = p.index[i]
        w = signals.get(t, {})
        ddm = (peak - nav) / peak if peak > 0 else 0
        pos = p2 if ddm > th2 else (p1 if ddm > th1 else 1.0)
        scaled = {c: v * pos for c, v in w.items()}
        weights_series[t] = scaled
        nxt = i + 1
        end = me_idx[k + 1] + 1 if k + 1 < len(me_idx) else len(rets)
        seg = rets.iloc[nxt:end]
        if len(seg) == 0: continue
        sw = seg[list(scaled.keys())] if scaled else pd.DataFrame(0, index=seg.index, columns=rets.columns)
        wv = pd.Series([scaled.get(c, 0.0) for c in sw.columns], index=sw.columns)
        r = sw.values @ wv.values
        for rr in r:
            nav *= (1 + rr); peak = max(peak, nav)
    r, nav = rrg_engine.portfolio_returns(prices, weights_series)
    return r, nav

def run(prices, signals, pos_map):
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    weights_series = {t: {c: v * pos_map.get(t, 1.0) for c, v in w.items()} for t, w in signals.items()}
    r, nav = rrg_engine.portfolio_returns(prices, weights_series)
    return r, nav

def stats(r, nav, label):
    s = rrg_engine.perf_stats(r, nav)
    print(f'{label:<14}{s["total_ret"]:>10.2%}{s["ann_ret"]:>10.2%}{s["maxdd"]:>11.2%}{s["sharpe"]:>8.3f}')

if __name__ == '__main__':
    prices, diffusion, rrg = load()
    signals, p = base_signals(prices, diffusion, rrg)
    print(f'=== 扩散+RRG 风控层对比 | {START} ~ {END} ===')
    print(f'{"方案":<14}{"累计":>10}{"年化":>10}{"最大回撤":>11}{"夏普":>8}')
    modes = [('A 满仓(现状)', ''), ('B 回撤止损', 'dd'), ('C 波动率目标', 'vol'),
             ('D 趋势过滤MA', 'ma'), ('E 扩散阈值', 'diff')]
    results = {}
    for name, mode in modes:
        if mode == 'dd':
            r, nav = run_dd_stop(prices, signals)
        else:
            pm = positions(prices, diffusion, signals, mode)
            r, nav = run(prices, signals, pm)
        results[name] = (r, nav)
        stats(r, nav, name)
    print('\n--- 2026年至今分段 ---')
    for name, (r, nav) in results.items():
        nv = nav[(nav.index >= pd.Timestamp('20260101')) & (nav.index <= pd.Timestamp('20260822'))]
        if len(nv) > 10:
            tot = float(nv.iloc[-1] / nv.iloc[0] - 1)
            mdd = float((nv / nv.cummax() - 1).min())
            print(f'{name:<14}{tot:>9.2%}  回撤 {mdd:>8.2%}')