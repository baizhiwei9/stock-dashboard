#!/usr/bin/env python3
"""
RMT风控 vs TDA风控 对比回测 (持仓标的: 沪深300指数)
====================================================
- 回测区间: 2022-01-04 ~ 2026-08-19
- 信号时序: T日收盘生成信号 -> T+1日按信号持仓
- 基准: 满仓买入持有沪深300

RMT风控(19 ETF收益矩阵, 60日窗口):
  gap_1 < 0.10  -> 简并共振警报 -> 减仓10% (仓位90%)
  ΔS > +0.03    -> 危机预警     -> 减仓30% (仓位70%)
  ΔS < -0.10    -> 恐慌底       -> 满仓100%抄底
  其他          -> 正常         -> 满仓100%

TDA风控(22申万行业指数价格, 60日窗口, UMAP+ripser):
  脆弱性 vulnerability + 5状态分类:
  bull:        position = max(0.50, 1 - vuln/100*0.5)
  bear:        position = max(0.15, 1 - vuln/100*0.9)
  oscillation: position = max(0.30, 1 - vuln/100*0.7)
"""

import os, sys, json, time, datetime
import numpy as np
import pandas as pd
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# ===== 路径 =====
WORK = os.path.dirname(os.path.abspath(__file__))
TDA_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0'
RMT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fe30462daffeCUhLPMsmZ1FHa7'
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)
sys.path.insert(0, TDA_DIR)

from tda_core import run_tda_analysis_safe, classify_market_state_v2

# ===== 常量 =====
WINDOW = 60
START = '2022-01-04'
END = '2026-08-19'

# ===== 数据加载 =====

def load_hs300():
    df = pd.read_pickle(os.path.join(WORK, 'hs300_daily.pkl'))
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    close = df['close'].astype(float)
    ret = close.pct_change().dropna()
    return close, ret

def load_rmt_pool():
    """RMT资产池: 19 ETF 日收益"""
    try:
        ret = pd.read_pickle(os.path.join(RMT_DIR, 'etf_returns_with_kc50.pkl'))
    except Exception:
        ret = pd.read_pickle(os.path.join(RMT_DIR, 'etf_returns.pkl'))
    ret = ret.sort_index()
    ret.index = pd.to_datetime(ret.index)
    return ret

def load_tda_pool():
    """TDA资产池: 22 申万行业指数收盘价"""
    cache_dir = os.path.join(TDA_DIR, 'state', 'sw_cache')
    prices = {}
    for f in sorted(os.listdir(cache_dir)):
        if not f.startswith('sw_') or not f.endswith('.json'):
            continue
        with open(os.path.join(cache_dir, f), 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        df = pd.DataFrame(data)
        df['trade_date'] = df['trade_date'].astype(str)
        s = df.set_index(pd.to_datetime(df['trade_date']))['close'].astype(float)
        s = s[~s.index.duplicated(keep='last')].sort_index()
        name = f.split('_')[1].split('.')[0]
        prices[name] = s
    pdf = pd.DataFrame(prices).sort_index()
    return pdf

# ===== RMT 指标计算 (对应 rmt_daily_report_v5.py compute_rmt_v4) =====

def compute_rmt_metrics(ret_window):
    """输入: DataFrame(60日 x 19 ETF 收益) -> gap_1, entropy, ks_d"""
    window = ret_window.copy()
    C = window.corr().values
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    C = (C + C.T) / 2
    eigs = np.sort(np.linalg.eigvalsh(C))[::-1]
    eigs = np.maximum(eigs, 1e-12)
    N = len(window.columns)
    q = N / len(window)
    lambda_plus = (1 + np.sqrt(q))**2
    lambda_minus = max(0, (1 - np.sqrt(q))**2)
    gap_1 = (eigs[0] - eigs[1]) / eigs[0]
    p = eigs / eigs.sum()
    mask = p > 1e-12
    entropy = -np.sum(p[mask] * np.log(p[mask]))
    bulk = eigs[1:]
    spacings = -np.diff(bulk)
    spacings = np.maximum(spacings, 1e-12)
    if spacings.mean() > 1e-10:
        normalized = spacings / spacings.mean()
        ks_d = stats.kstest(normalized, lambda s: 1 - np.exp(-np.pi * s**2 / 4)).statistic
    else:
        ks_d = 0.0
    return gap_1, entropy, ks_d

def rmt_position(gap_1, entropy, entropy_hist):
    """RMT状态机 -> 仓位"""
    if len(entropy_hist) >= 4:
        prev_entropy = entropy_hist[-4]
        delta_s = entropy - prev_entropy
    else:
        delta_s = 0.0
    if gap_1 < 0.10:
        status = '简并共振警报'
        pos = 0.90
    elif delta_s > 0.03:
        status = '危机预警'
        pos = 0.70
    elif delta_s < -0.10:
        status = '恐慌底'
        pos = 1.00
    else:
        status = '正常'
        pos = 1.00
    return pos, status, delta_s

# ===== TDA 指标计算 =====

def tda_position(price_window):
    """TDA分析 -> 仓位 (对应 run_daily_22f.py v3 逻辑)"""
    tda_result = run_tda_analysis_safe(price_window)
    features = tda_result['features']
    market = tda_result['market']
    rotation = classify_market_state_v2(features, price_window)
    vuln = market['vulnerability']
    if 'bull' in rotation['state']:
        pos = max(0.50, 1.0 - vuln / 100.0 * 0.5)
    elif 'bear' in rotation['state']:
        pos = max(0.15, 1.0 - vuln / 100.0 * 0.9)
    else:
        pos = max(0.30, 1.0 - vuln / 100.0 * 0.7)
    return pos, rotation, market

# ===== 回测主循环 =====

def run_backtest():
    print('=' * 70)
    print(f'RMT风控 vs TDA风控 对比回测 | {START} ~ {END} | 标的: 沪深300')
    print('=' * 70)

    close, hs_ret = load_hs300()
    rmt_pool = load_rmt_pool()
    tda_pool = load_tda_pool()
    print(f'沪深300: {close.index.min().date()} ~ {close.index.max().date()} ({len(close)}天)')
    print(f'RMT资产池: {rmt_pool.index.min().date()} ~ {rmt_pool.index.max().date()} ({rmt_pool.shape[0]}天 x {rmt_pool.shape[1]}标的)')
    print(f'TDA资产池: {tda_pool.index.min().date()} ~ {tda_pool.index.max().date()} ({tda_pool.shape[0]}天 x {tda_pool.shape[1]}行业)')

    # 主交易日历: 沪深300
    trade_dates = hs_ret.index
    # 数据加载从2021-06开始(用于预热60日窗口+熵历史), 但回测起点=START(2022-01-04)
    mask = (hs_ret.index >= pd.Timestamp('2021-06-01')) & (hs_ret.index <= pd.Timestamp(END))
    dates = hs_ret.index[mask]
    dates = dates[dates >= pd.Timestamp(START)]

    # 对齐资产池到主日历 (向前填充)
    rmt_pool_aligned = rmt_pool.reindex(trade_dates).ffill()
    tda_pool_aligned = tda_pool.reindex(trade_dates).ffill()

    results = []  # 每日记录
    entropy_hist = []  # RMT熵历史

    tic = time.time()
    for i, d in enumerate(dates):
        dt = pd.Timestamp(d)
        # 需要至少 WINDOW+1 历史
        idx_pos = trade_dates.get_loc(dt) if dt in trade_dates else -1
        if idx_pos < WINDOW + 1:
            continue
        window_dates = trade_dates[idx_pos - WINDOW + 1: idx_pos + 1]  # 前60日(含T)

        # ----- RMT信号 -----
        rmt_win = rmt_pool_aligned.loc[window_dates].dropna(axis=1)
        if rmt_win.shape[1] < 5 or len(rmt_win) < 30:
            rmt_pos, rmt_status, delta_s = 1.0, '正常(数据不足)', 0.0
            gap_1, entropy = 1.0, 1.0
        else:
            gap_1, entropy, _ = compute_rmt_metrics(rmt_win)
            rmt_pos, rmt_status, delta_s = rmt_position(gap_1, entropy, entropy_hist)
        entropy_hist.append(entropy)

        # ----- TDA信号 -----
        tda_win = tda_pool_aligned.loc[window_dates]
        tda_win = tda_win.dropna(axis=1)
        if tda_win.shape[1] < 8 or len(tda_win) < 30:
            tda_pos, tda_rot, tda_mkt = 1.0, {'state': 'oscillation', 'state_cn': '均衡配置'}, {'vulnerability': 30.0, 'state': '震荡偏强'}
        else:
            tda_pos, tda_rot, tda_mkt = tda_position(tda_win)

        results.append({
            'date': dt,
            'hs300_ret': hs_ret[dt],
            'rmt_pos': rmt_pos, 'rmt_status': rmt_status, 'rmt_delta_s': delta_s, 'rmt_entropy': entropy, 'rmt_gap1': gap_1,
            'tda_pos': tda_pos, 'tda_state': tda_rot['state'], 'tda_vuln': tda_mkt['vulnerability'],
        })

        if (i + 1) % 200 == 0:
            elapsed = time.time() - tic
            print(f'  已处理 {i+1}/{len(dates)} 交易日 ({elapsed:.0f}s)')

    print(f'回测完成: {len(results)} 个交易日, 耗时 {time.time()-tic:.0f}s')

    df = pd.DataFrame(results).set_index('date')

    # T+1 生效: 组合收益 = 前一日信号仓位 x 当日收益
    df['rmt_return'] = df['rmt_pos'].shift(1) * df['hs300_ret']
    df['tda_return'] = df['tda_pos'].shift(1) * df['hs300_ret']
    # 基准
    df['bench_return'] = df['hs300_ret']

    # 从第二个信号后开始计算 (第一个信号在T+1才生效)
    df = df.iloc[1:]
    df = df.dropna(subset=['rmt_return', 'tda_return'])

    df['rmt_cum'] = (1 + df['rmt_return']).cumprod()
    df['tda_cum'] = (1 + df['tda_return']).cumprod()
    df['bench_cum'] = (1 + df['bench_return']).cumprod()

    # 回撤
    for col in ['rmt_cum', 'tda_cum', 'bench_cum']:
        df[f'{col}_dd'] = df[col] / df[col].cummax() - 1

    df.to_pickle(os.path.join(WORK, 'backtest_rmt_vs_tda.pkl'))
    print('\n结果已保存: backtest_rmt_vs_tda.pkl')
    return df

# ===== 指标计算 =====

def calc_metrics(returns, cum, name):
    ret = returns.dropna()
    n = len(ret)
    years = n / 252
    total = cum.iloc[-1] - 1 if len(cum) > 0 else 0
    annual = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252 - 0.015) / vol if vol > 0 else 0  # 无风险利率1.5%
    dd = (cum / cum.cummax() - 1).min()
    calmar = annual / abs(dd) if dd != 0 else 0
    win = (ret > 0).mean()
    avg_pos = None
    return {
        'name': name, 'total': total, 'annual': annual, 'vol': vol,
        'sharpe': sharpe, 'max_dd': dd, 'calmar': calmar, 'win_rate': win, 'n_days': n,
    }

def daily_metrics(df):
    out = {}
    out['RMT风控'] = calc_metrics(df['rmt_return'], df['rmt_cum'], 'RMT风控')
    out['TDA风控'] = calc_metrics(df['tda_return'], df['tda_cum'], 'TDA风控')
    out['基准(满仓)'] = calc_metrics(df['bench_return'], df['bench_cum'], '基准(满仓)')
    # 仓位统计
    out['RMT风控']['avg_pos'] = df['rmt_pos'].mean()
    out['TDA风控']['avg_pos'] = df['tda_pos'].mean()
    out['基准(满仓)']['avg_pos'] = 1.0
    return out

if __name__ == '__main__':
    df = run_backtest()
    metrics = daily_metrics(df)
    print('\n' + '=' * 70)
    print('回测指标对比')
    print('=' * 70)
    print(f"{'指标':<12}{'RMT风控':>14}{'TDA风控':>14}{'基准(满仓)':>14}")
    rows = [
        ('总收益', 'total', '{:.2%}'),
        ('年化收益', 'annual', '{:.2%}'),
        ('年化波动', 'vol', '{:.2%}'),
        ('夏普(1.5%rf)', 'sharpe', '{:.2f}'),
        ('最大回撤', 'max_dd', '{:.2%}'),
        ('卡玛比率', 'calmar', '{:.2f}'),
        ('日胜率', 'win_rate', '{:.2%}'),
        ('平均仓位', 'avg_pos', '{:.2%}'),
        ('交易日数', 'n_days', '{:.0f}'),
    ]
    for label, key, fmt in rows:
        vals = ' '.join(fmt.format(metrics[k][key]) for k in ['RMT风控', 'TDA风控', '基准(满仓)'])
        print(f'{label:<12}{vals}')

    # 年度收益
    print('\n年度收益对比:')
    print(f"{'年份':<8}{'RMT风控':>14}{'TDA风控':>14}{'基准(满仓)':>14}")
    for yr in sorted(set(df.index.year)):
        sub = df[df.index.year == yr]
        rmt_yr = (1 + sub['rmt_return']).prod() - 1
        tda_yr = (1 + sub['tda_return']).prod() - 1
        bench_yr = (1 + sub['bench_return']).prod() - 1
        print(f'{yr:<8}{rmt_yr:>14.2%}{tda_yr:>14.2%}{bench_yr:>14.2%}')

    # 信号分布
    print('\nRMT信号分布:')
    print(df['rmt_status'].value_counts().to_string())
    print('\nTDA状态分布:')
    print(df['tda_state'].value_counts().to_string())
    print('\nTDA脆弱性: min={:.1f} max={:.1f} avg={:.1f}'.format(
        df['tda_vuln'].min(), df['tda_vuln'].max(), df['tda_vuln'].mean()))
    print('\nRMT仓位: min={:.0%} max={:.0%} avg={:.2%}'.format(
        df['rmt_pos'].min(), df['rmt_pos'].max(), df['rmt_pos'].mean()))
    print('TDA仓位: min={:.0%} max={:.0%} avg={:.2%}'.format(
        df['tda_pos'].min(), df['tda_pos'].max(), df['tda_pos'].mean()))