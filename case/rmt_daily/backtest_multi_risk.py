#!/usr/bin/env python3
"""
多风控统一对比回测：沪深300 上各任务风控策略
================================================
统一约定（与 RMT vs TDA 对比一致）：
- 标的: 沪深300指数, 基准=满仓买入持有
- 区间: 2022-01-04 ~ 2026-08-19
- 信号T日生成 -> T+1日持仓生效, 无交易成本
- 收益序列复用 backtest_rmt_vs_tda.pkl 中 hs300_ret 保证同口径

风控列表:
  RMT     : %random_matrix_theory (已有结果, 直接复用 pkl 的 pos/ret)
  TDA     : %topological_data_analysis (已有结果, 复用)
  HLM     : Hodge Laplacian 任务 MA40 趋势过滤 -> 沪深300<MA40 空仓, 否则满仓
  CHAOS   : 分析力学混沌风控 -> Hurst/LLE/Tor/MA20 三层判定 DANGER(0) / WARNING(40%) / CLEAR(100%)
"""
import os, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)

CHAOS_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_00be4a7d3fferyCWjQeV0xmIKY'
sys.path.insert(0, CHAOS_DIR)

from hamiltonian_chaos_system import (
    rolling_hurst, rolling_lle, rolling_toroidality, RiskManager
)

START = '2022-01-04'
END = '2026-08-19'

# ===== 1. 载入公共数据 =====
base = pd.read_pickle(os.path.join(WORK, 'backtest_rmt_vs_tda.pkl'))  # 索引 2022-01-05~2026-08-19
hs300 = pd.read_pickle(os.path.join(WORK, 'hs300_daily.pkl'))
hs300['date'] = pd.to_datetime(hs300['date'])
hs300 = hs300.set_index('date').sort_index()
close = hs300['close']

# 对齐: pkl 的每个交易日 -> 用截至 T-1 的信息判定 T 日仓位
dates = base.index
close_hist = close[close.index < dates[0]]  # 历史数据 (提前量, 保证 MA40/特征有窗口)
close_all = pd.concat([close_hist, close[dates[0]:]])

# ===== 2. HLM MA40 趋势过滤 =====
# 规则: 沪深300 收盘 >= MA40 -> 满仓100%; < MA40 -> 空仓0% (T-1 收盘判定, T 日生效)
ma40 = close_all.rolling(40).mean()
hlm_pos = pd.Series(1.0, index=dates)
for d in dates:
    prev = close_all.loc[:d].iloc[:-1]  # 截至 T-1 (含) 的信息... 简化: d 当日是 T, 需用 T-1 收盘
    break
# 逐日: 用 T-1 的 close/ma40
pos_hlm = []
for i, d in enumerate(dates):
    if i == 0:
        pos_hlm.append(1.0)  # 首日无 T-1 数据, 视为满仓(与RMT/TDA首日处理一致: 首日收益0)
        continue
    prev_close = close_all.loc[:dates[i-1]].iloc[-1]
    prev_ma = ma40.loc[dates[i-1]]
    pos_hlm.append(1.0 if prev_close >= prev_ma else 0.0)
hlm_pos = pd.Series(pos_hlm, index=dates)

# ===== 3. 分析力学混沌风控 =====
# 全序列一次计算特征 (滚动窗口内部实现)
print('计算混沌特征 (Hurst/LLE/Tor, 全序列)...')
log_ret = np.log(close_all / close_all.shift(1))
print('  rolling Hurst...')
hurst_s = rolling_hurst(log_ret.dropna(), 100)
print('  rolling LLE...')
lle_s = rolling_lle(log_ret.dropna(), 100)
detrended = close_all - close_all.rolling(20).mean()
print('  rolling Tor...')
tor_s = rolling_toroidality(detrended.dropna(), 60)
ma20 = close_all.rolling(20).mean()

feat = pd.DataFrame({
    'close': close_all,
    'hurst': hurst_s.reindex(close_all.index),
    'lle': lle_s.reindex(close_all.index),
    'tor': tor_s.reindex(close_all.index),
    'ma20': ma20,
})
rm = RiskManager()

def risk_level(row):
    close_v = row['close']
    ma = row['ma20']
    if pd.isna(ma) or ma <= 0:
        return 'CLEAR'
    above = close_v > ma
    dev = (close_v - ma) / ma
    return rm.check_market_risk(row['lle'], row['tor'], row['hurst'],
                                above_ma20=above, dev_ma20=dev, below_ma20_days=0)

# T-1 特征 -> T 日仓位 (DANGER=0, WARNING=0.4, CLEAR=1.0)
pos_chaos = []
risk_series = []
print('逐日风控判定...')
for i, d in enumerate(dates):
    if i == 0:
        pos_chaos.append(1.0)
        risk_series.append('CLEAR')
        continue
    prev_row = feat.loc[dates[i-1]]
    r = risk_level(prev_row)
    risk_series.append(r)
    pos_chaos.append({'DANGER': 0.0, 'WARNING': 0.4, 'CLEAR': 1.0}[r])
chaos_pos = pd.Series(pos_chaos, index=dates)
chaos_risk = pd.Series(risk_series, index=dates)

# ===== 4. 收益计算 =====
hs300_ret = base['hs300_ret']
bench_ret = base['bench_return']

# 复用已有 RMT/TDA 收益
rmt_ret = base['rmt_return']
tda_ret = base['tda_return']

# HLM / CHAOS: 当日收益 = 当日生效仓位(T-1信号) * 当日沪深300收益
# 注: hlm_pos[T] 由 T-1 收盘判定 => hlm_ret[T] = hlm_pos[T] * hs300_ret[T]
def cum_from_ret(ret):
    return (1 + ret).cumprod()

hlm_ret = hlm_pos * hs300_ret
chaos_ret = chaos_pos * hs300_ret

out = pd.DataFrame({
    'hs300_ret': hs300_ret,
    'bench_ret': bench_ret,
    'rmt_ret': rmt_ret,
    'tda_ret': tda_ret,
    'hlm_ret': hlm_ret,
    'chaos_ret': chaos_ret,
    'rmt_pos': base['rmt_pos'],
    'tda_pos': base['tda_pos'],
    'hlm_pos': hlm_pos,
    'chaos_pos': chaos_pos,
    'chaos_risk': chaos_risk,
    'rmt_cum': base['rmt_cum'],
    'tda_cum': base['tda_cum'],
    'hlm_cum': cum_from_ret(hlm_ret),
    'chaos_cum': cum_from_ret(chaos_ret),
    'bench_cum': base['bench_cum'],
})
for col in ['rmt_cum_dd', 'tda_cum_dd', 'hlm_cum_dd', 'chaos_cum_dd', 'bench_cum_dd']:
    key = col.replace('_cum_dd', '_cum')
    out[col] = out[key] / out[key].cummax() - 1

out.to_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))

# ===== 5. 汇总指标 =====
def metrics(ret, cum, rf=0.015):
    n = len(ret)
    years = n / 252
    total = cum.iloc[-1] - 1
    annual = (1 + total) ** (1 / years) - 1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252 - rf) / vol if vol > 0 else 0
    mdd = (cum / cum.cummax() - 1).min()
    calmar = annual / abs(mdd) if mdd != 0 else 0
    win = (ret > 0).mean()
    return dict(total=total, annual=annual, vol=vol, sharpe=sharpe, mdd=mdd,
                calmar=calmar, win=win, avg_pos=np.nan)

summary = {}
for name in ['rmt', 'tda', 'hlm', 'chaos', 'bench']:
    m = metrics(out[f'{name}_ret'], out[f'{name}_cum'])
    m['avg_pos'] = out[f'{name}_pos'].mean() if name != 'bench' else 1.0
    m['name'] = {'rmt': 'RMT风控', 'tda': 'TDA风控', 'hlm': 'HLM(MAA40趋势)', 'chaos': '分析力学混沌', 'bench': '基准'}[name]
    summary[name] = m

print(f"\n{'策略':<14} {'总收益':>8} {'年化':>8} {'波动':>7} {'夏普':>6} {'回撤':>8} {'卡玛':>6} {'仓位':>6}")
for name, m in summary.items():
    print(f"{m['name']:<14} {m['total']*100:>7.2f}% {m['annual']*100:>7.2f}% {m['vol']*100:>6.2f}% "
          f"{m['sharpe']:>6.2f} {m['mdd']*100:>7.2f}% {m['calmar']:>6.2f} {m['avg_pos']*100:>5.1f}%")

# 年度收益
print("\n年度收益 (%):")
for name in ['rmt', 'tda', 'hlm', 'chaos', 'bench']:
    yr = out[f'{name}_ret'].groupby(out.index.year).apply(lambda x: (1+x).prod()-1)
    print(f"  {name}: " + ' | '.join(f"{y}:{v*100:+.1f}" for y, v in yr.items()))

# 混沌风控风险分布
print("\n混沌风控信号分布:")
print(out['chaos_risk'].value_counts().to_string())
print("HLM 空仓天数:", int((out['hlm_pos'] == 0).sum()), f"({(out['hlm_pos']==0).mean()*100:.1f}%)")
print("\n结果已保存: backtest_multi_risk.pkl")