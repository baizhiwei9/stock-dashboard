#!/usr/bin/env python3
"""
组装 v2 全策略回测数据 + 指标表
================================
策略清单:
- 继承 v1: 基准(满仓HS300) / RMT / TDA / HLM(MA40) / 混沌 / PG融合
- HLM 趋势过滤严格度对比: MA20 / MA40 / MA60 (二值仓控, 前窗NaN=满仓, 与v1口径一致)
- PG 子算法照抄线上阈值版: 12个 (backtest_subalgo.pkl)
- PG 子算法分位数校准版: 12个 (backtest_subalgo_calib.pkl)
输出: backtest_multi_risk_v2.pkl + metrics_v2.pkl
"""
import os, warnings, pickle
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))

base = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))
dates = base.index
hs_ret = base['hs300_ret']

sub = pickle.load(open(os.path.join(WORK, 'backtest_subalgo.pkl'), 'rb'))['sub_pos']
cal = pickle.load(open(os.path.join(WORK, 'backtest_subalgo_calib.pkl'), 'rb'))['sub_pos_calib']

# ========== HLM MA 严格度 (沿用 v1 数据源与构造: hs300_daily.pkl 长历史提前量) ==========
hs300 = pd.read_pickle(os.path.join(WORK, 'hs300_daily.pkl'))
hs300['date'] = pd.to_datetime(hs300['date'])
hs300 = hs300.set_index('date').sort_index()
close_src = hs300['close']
close_hist = close_src[close_src.index < dates[0]]
close_all = pd.concat([close_hist, close_src[dates[0]:]])

def add_ma(name, win):
    ma = close_all.rolling(win).mean()
    pos_hlm = [1.0]  # 首日无 T-1 数据, 视为满仓 (与 v1/RMT/TDA 首日处理一致)
    for i in range(1, len(dates)):
        pc = close_all.loc[:dates[i - 1]].iloc[-1]
        pm = ma.loc[dates[i - 1]]
        pos_hlm.append(1.0 if pc >= pm else 0.0)
    pos = pd.Series(pos_hlm, index=dates)
    ret = pos * hs_ret
    cum = (1 + ret).cumprod()
    base[f'{name}_pos'] = pos
    base[f'{name}_ret'] = ret
    base[f'{name}_cum'] = cum
    base[f'{name}_cum_dd'] = cum / cum.cummax() - 1

add_ma('hlm_ma20', 20)
add_ma('hlm_ma40', 40)
add_ma('hlm_ma60', 60)
# 校验 MA40 与 v1 hlm 一致
assert (base['hlm_ma40_pos'] - base['hlm_pos']).abs().max() < 1e-12, 'MA40 重构与 v1 不一致'
print('HLM MA40 与 v1 hlm_pos 完全一致 ✓')

# ========== 并入 PG 子算法 (照抄版 + 校准版) ==========
for c in sub.columns:
    if c == 'pg_fusion':
        continue
    pos = sub[c]
    ret = pos * hs_ret
    cum = (1 + ret).cumprod()
    base[f'{c}_pos'] = pos
    base[f'{c}_ret'] = ret
    base[f'{c}_cum'] = cum
    base[f'{c}_cum_dd'] = cum / cum.cummax() - 1
for c in cal.columns:
    pos = cal[c]
    ret = pos * hs_ret
    cum = (1 + ret).cumprod()
    base[f'{c}_pos'] = pos
    base[f'{c}_ret'] = ret
    base[f'{c}_cum'] = cum
    base[f'{c}_cum_dd'] = cum / cum.cummax() - 1

# ========== 指标表 ==========
def metrics(ret_s, cum_s, pos_s, rf=0.015):
    n = len(ret_s); years = n / 252
    total = cum_s.iloc[-1] - 1
    annual = (1 + total) ** (1 / years) - 1
    vol = ret_s.std() * np.sqrt(252)
    sharpe = (ret_s.mean() * 252 - rf) / vol if vol > 0 else 0
    mdd = (cum_s / cum_s.cummax() - 1).min()
    calmar = annual / abs(mdd) if mdd != 0 else 0
    win = (ret_s > 0).mean()
    avg_pos = pos_s.mean()
    turnover = pos_s.diff().abs().mean()
    return pd.Series({'总收益%': total * 100, '年化%': annual * 100,
                      '年化波动%': vol * 100, '夏普': sharpe, '最大回撤%': mdd * 100,
                      '卡玛': calmar, '胜率%': win * 100,
                      '平均仓位%': avg_pos * 100, '日均换仓%': turnover * 100})

# 策略 -> (收益列表名, 仓位列名) , 收益列约定 = {name}_ret
NAME_ORDER = [
    ('基准', 'bench_ret', 'bench_pos'), ('RMT谱风控', 'rmt_ret', 'rmt_pos'),
    ('TDA拓扑风控', 'tda_ret', 'tda_pos'), ('HLM(MA40)', 'hlm_ret', 'hlm_pos'),
    ('混沌风控', 'chaos_ret', 'chaos_pos'), ('PG融合风控', 'pg_ret', 'pg_pos'),
    ('HLM(MA20)', 'hlm_ma20_ret', 'hlm_ma20_pos'),
    ('HLM(MA60)', 'hlm_ma60_ret', 'hlm_ma60_pos'),
]
PG_SUB = [
    ('M·Ising磁化', 'pg_M_magnet'), ('J·耦合强度', 'pg_J_coupling'),
    ('ξ·关联长度', 'pg_xi_corrlen'), ('T·温度波动', 'pg_T_temperature'),
    ('MSF·微观稳定', 'pg_MSF_stability'), ('RMT·谱超差', 'pg_RMT_spectrum'),
    ('PCA流形', 'pg_PCA_manifold'), ('Betti洞数', 'pg_Betti_holes'),
    ('Ricci曲率', 'pg_Ricci_curv'), ('Morse形态', 'pg_Morse_shape'),
    ('D·分形维', 'pg_D_fractal'), ('H·Hurst', 'pg_H_hurst'),
]
PG_CAL = [
    ('M·Ising磁化', 'pg_cal_M'), ('J·耦合强度', 'pg_cal_J'),
    ('ξ·关联长度', 'pg_cal_xi'), ('T·温度波动', 'pg_cal_T'),
    ('MSF·微观稳定', 'pg_cal_MSF'), ('RMT·谱超差', 'pg_cal_excess'),
    ('PCA流形', 'pg_cal_manifold'), ('Betti洞数', 'pg_cal_betti_1'),
    ('Ricci曲率', 'pg_cal_ricci'), ('Morse形态', 'pg_cal_morse'),
    ('D·分形维', 'pg_cal_D'), ('H·Hurst', 'pg_cal_H'),
]

rows = {}
for cname, rcol, pcol in NAME_ORDER:
    rows[cname] = metrics(base[rcol], base[f'{rcol[:-4]}_cum' if rcol != 'bench_ret' else 'bench_cum'], base[pcol] if pcol in base else base['hlm_pos'])
rows['基准'] = metrics(base['hs300_ret'], base['bench_cum'], pd.Series(1.0, index=dates))
rows['HLM(MA40)'] = metrics(base['hlm_ret'], base['hlm_cum'], base['hlm_pos'])
rows['PG融合风控'] = metrics(base['pg_ret'], base['pg_cum'], base['pg_pos'])
for cname, key in PG_SUB:
    rows[f'{cname}·照抄'] = metrics(base[f'{key}_ret'], base[f'{key}_cum'], base[f'{key}_pos'])
for cname, key in PG_CAL:
    rows[f'{cname}·校准'] = metrics(base[f'{key}_ret'], base[f'{key}_cum'], base[f'{key}_pos'])
# 基准富余: bench_pos 用全1
rows['基准'] = metrics(base['hs300_ret'], base['bench_cum'], pd.Series(1.0, index=dates))

mt = pd.DataFrame(rows).T
mt = mt.round(2)

with open(os.path.join(WORK, 'backtest_multi_risk_v2.pkl'), 'wb') as fh:
    pickle.dump(base, fh)
mt.to_pickle(os.path.join(WORK, 'metrics_v2.pkl'))
print('已保存 backtest_multi_risk_v2.pkl (%d 列) 与 metrics_v2.pkl (%d 行)' % (len(base.columns), len(mt)))
pd.set_option('display.width', 200)
print(mt.to_string())