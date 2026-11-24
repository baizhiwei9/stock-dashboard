#!/usr/bin/env python3
"""
SPD流形 vs 扩散+RRG —— 2026年至今同期对比回测
============================================
SPD策略 (用户现有实盘逻辑):
  22申万行业因子, 60日窗口: SPD Fisher-z测地漂移 x 动量(s+1)^2 x discount, Top5集中持仓
  调仓: 每5个交易日重算一次 (与实盘 run_daily 一致)
扩散+RRG策略 (strategy_repro 复现):
  30申万一级行业, 月末调仓: 扩散最高6行业 -> 剔除RRG三、四象限
区间: 20260101 ~ 20260822 (今年至今)
"""
import os, sys, json, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'pylib'))

from data_fetcher_22f import FACTOR_22_MAP, FACTOR_NAMES, fetch_all_sw_prices, get_trade_calendar, fetch_benchmark_prices
from spd_core import compute_spd_weights, compute_spd_drift_scores
from tda_core import compute_factor_sharpe

START, END = '20260101', '20260822'
TOP_N = 5
REBAL = 5          # SPD调仓频率: 每5个交易日
WINDOW = 60
REF_OFFSET = 60


# ---------- 1. SPD 回测 (纯股票端, 用用户实盘同款逻辑) ----------
def backtest_spd():
    cal = get_trade_calendar()
    if START not in cal:
        print('交易日历缺 START'); return None
    start_idx = cal.index(START)
    data_start_idx = max(0, start_idx - (WINDOW + REF_OFFSET + 10))
    data_start = cal[data_start_idx]
    price_df = fetch_all_sw_prices(data_start, END)
    if price_df.empty or len(price_df) < WINDOW + 60:
        print('SPD 价格数据不足'); return None
    print(f'  SPD 价格矩阵: {price_df.shape} ({price_df.index[0]} ~ {price_df.index[-1]})')

    dates = price_df.index.tolist()
    bt_dates = [d for d in dates if START <= d <= END]
    rets = price_df.pct_change().fillna(0)

    nav = 1.0; peak = 1.0; mdd = 0.0
    nav_series = []
    holdings = {}
    last_rebal_idx = -999
    for i, d in enumerate(dates):
        if d not in bt_dates:
            continue
        cur_idx = i
        # 每 REBAL 个交易日重算一次仓位 (信号T日收盘 -> T+1生效)
        if cur_idx - last_rebal_idx >= REBAL and cur_idx >= WINDOW:
            window_slice = slice(cur_idx - WINDOW + 1, cur_idx + 1)
            price_window = price_df.iloc[window_slice]
            sharpe = compute_factor_sharpe(price_window, window=min(WINDOW, len(price_window)))
            weights, total_drift = compute_spd_weights(
                price_window, price_df, cur_idx, sharpe,
                top_n=TOP_N, momentum_discount=0.5, window=WINDOW, ref_offset=REF_OFFSET)
            holdings = weights
            last_rebal_idx = cur_idx
        # 用昨日持仓计算今日收益 (信号T日生成, T+1生效)
        if holdings:
            h = [c for c in holdings if c in rets.columns]
            if h:
                r = sum(holdings[c] * rets.iloc[i][c] for c in h)
            else:
                r = 0.0
        else:
            r = 0.0
        nav *= (1 + r)
        peak = max(peak, nav)
        mdd = max(mdd, (peak - nav) / peak)
        nav_series.append((d, nav, r))

    df = pd.DataFrame(nav_series, columns=['date', 'nav', 'ret'])
    days = len(df)
    ann = (df['nav'].iloc[-1]) ** (252 / days) - 1 if days > 0 else 0
    sharpe = df['ret'].mean() / df['ret'].std() * np.sqrt(252) if df['ret'].std() > 0 else 0
    bench = get_benchmark_ret(cal, price_df.index[0])
    return {
        'nav_df': df, 'ann': ann, 'mdd': mdd, 'sharpe': sharpe,
        'total': df['nav'].iloc[-1] - 1, 'days': days,
    }


# ---------- 2. 基准: 沪深300 同期 ----------
def get_benchmark_ret(cal, data_start):
    try:
        bench = fetch_benchmark_prices(data_start, END)
        if bench is None or len(bench) == 0:
            return 0.0
        b = bench[(bench.index >= START) & (bench.index <= END)]
        return b.iloc[-1] / b.iloc[0] - 1 if len(b) > 1 else 0.0
    except Exception:
        return 0.0


# ---------- 3. 扩散+RRG 2026同期 (从 strategy_repro 已算结果提取) ----------
def get_diffusion_rrg_2026():
    repo = os.path.join(os.path.dirname(BASE), 'ses_gffe5fd6b2558bffeYG74Ts057P2dLR', 'strategy_repro')
    if not os.path.exists(repo):
        return None
    sys.path.insert(0, repo)
    import importlib
    try:
        fe = open(os.path.join(repo, 'auto_compare_report.py'), encoding='utf-8').read()
    except Exception:
        return None
    # 直接 import 并复用其 run_rrg(), 然后截取 2026 段
    import rrg_data, rrg_engine
    diff_path = os.path.join(repo, 'data_rrg', 'diffusion.csv')
    if os.path.exists(diff_path):
        diffusion = pd.read_csv(diff_path, index_col=0, parse_dates=True)
    else:
        diffusion = rrg_data.build_diffusion()
    prices = rrg_data.load_industry_prices()
    rrg = rrg_engine.compute_rrg(prices)
    results = rrg_engine.backtest(prices, diffusion, rrg, start='20220101', end='20260822')
    out = {}
    for key in ['扩散', '扩散+RRG']:
        r, nav, sig = results[key]
        # nav.index 是日期
        nv = nav[(nav.index >= pd.Timestamp(START)) & (nav.index <= pd.Timestamp(END))]
        if len(nv) < 10:
            out[key] = None; continue
        ret = nv.pct_change().fillna(0)
        total = nv.iloc[-1] / nv.iloc[0] - 1
        days = len(nv)
        ann = (1 + total) ** (252 / days) - 1
        mdd = (nv / nv.cummax() - 1).min()
        sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
        out[key] = {'total': total, 'ann': ann, 'mdd': mdd, 'sharpe': sh, 'days': days, 'nav_df': nv}
    return out


if __name__ == '__main__':
    print(f'=== SPD流形 vs 扩散+RRG | {START} ~ {END} ===')
    spd = backtest_spd()
    if spd:
        print(f'\nSPD流形(Top{TOP_N}, 60日窗口):')
        print(f'  累计收益: {spd["total"]:.2%}  年化: {spd["ann"]:.2%}  最大回撤: {spd["mdd"]:.2%}  夏普: {spd["sharpe"]:.3f}  (T={spd["days"]})')
    dd = get_diffusion_rrg_2026()
    if dd:
        for k, v in dd.items():
            if v:
                print(f'{k}: 累计 {v["total"]:.2%}  年化 {v["ann"]:.2%}  最大回撤 {v["mdd"]:.2%}  夏普 {v["sharpe"]:.3f}  (T={v["days"]})')
    import pandas as _pd
    cal = get_trade_calendar()
    bench = get_benchmark_ret(cal, '20250901')
    if bench:
        print(f'沪深300: {bench:.2%}')
    # 保存
    out = {}
    if spd:
        out['spd'] = {k: (v.to_dict('records') if k == 'nav_df' and hasattr(v, 'to_dict') else v) for k, v in spd.items() if k != 'nav_df'}
    if dd:
        out['rrg'] = {k: {kk: (vv.to_dict('records') if kk == 'nav_df' and hasattr(vv, 'to_dict') else vv) for kk, vv in v.items() if kk != 'nav_df'} for k, v in dd.items()}
    json.dump(out, open(os.path.join(BASE, 'spd_vs_diffusion_2026.json'), 'w'), ensure_ascii=False, indent=1, default=str)