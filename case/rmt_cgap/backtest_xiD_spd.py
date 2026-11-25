#!/usr/bin/env python3
"""
ξ关联长度 × D分形维 组合风控 vs SPD黎曼流形漂移风控
======================================================
1) ξ+D 组合: 取 v2 分位数校准版单信号仓位 (100/75/40 三档)
   - 等权平均: pos = 0.5*xi + 0.5*D
   - 保守合并: pos = min(xi, D)  (任一信号触发看跌档即降仓)
2) SPD 漂移风控 (复刻线上 spd_core.py):
   - 22申万行业 60日收益率窗口, 当前窗口 vs 60日前参考窗口
   - total_drift = ||logm(R_当前) - logm(R_参考)||_F (Log-Euclidean 全矩阵测地距离)
   - avg_drift = mean_i |atanh(rho_i,t) - atanh(rho_i,t_ref)| (Fisher-z 平均漂移)
   - 分位数校准三档仓位 (漂移高=风险高: 低档40% / 低=满仓100% / 中间75%), 与子算法同口径
统一口径: T-1 日信号 -> T 日生效, 2022-01-05 ~ 2026-08-19, 沪深300, 基准满仓
输出: backtest_xiD_spd.pkl
"""
import os, sys, json, pickle, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
WORK_SPD = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0'
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)
from scipy.linalg import logm

# ---------------- 1. ξ+D 组合 ----------------
sub = pickle.load(open(os.path.join(WORK, 'backtest_subalgo_calib.pkl'), 'rb'))['sub_pos_calib']
xi = sub['pg_cal_xi']
D = sub['pg_cal_D']
combo = pd.DataFrame(index=xi.index)
combo['xiD_avg_pos'] = 0.5 * xi + 0.5 * D
combo['xiD_min_pos'] = np.minimum(xi, D)
combo['xi_pos'] = xi
combo['D_pos'] = D

# ---------------- 2. SPD 漂移 ----------------
FACTOR_FILES = {
    '科技成长': '801080', '高端制造': '801890', '新能源': '801730', '医药健康': '801150',
    '大消费': '801120', '金融地产': '801780', '资源材料': '801050', '数字经济': '801750',
    '国防军工': '801740', '绿色低碳': '801970', '农业养殖': '801010', '低波防御': '801160',
    '红利高息': '801170', '价值周期': '801040', '动量': '801880', '反转': '801180',
    '小盘成长': '801210', '大盘蓝筹': '801790', '质量因子': '801110', '成长因子': '801760',
    '价值因子': '801030', '流动性因子': '801950',
}
CACHE = os.path.join(WORK_SPD, 'state', 'sw_cache')

def load_prices():
    series = {}
    for name, code in FACTOR_FILES.items():
        fp = os.path.join(CACHE, f'sw_{code}.json')
        if not os.path.exists(fp):
            continue
        d = json.load(open(fp))
        s = pd.Series({r['trade_date']: r['close'] for r in d})
        s.index = pd.to_datetime(s.index, format='%Y%m%d')
        series[name] = s
    df = pd.DataFrame(series).sort_index()
    return df.dropna(how='all')

def drift_and_total(price_window, price_df, cur_pos, window=60, ref_offset=60):
    """复刻 spd_core.compute_spd_drift_scores 的逐日版本"""
    r_cur = price_window.pct_change().dropna()
    ref_end = cur_pos - ref_offset
    if ref_end < window:
        r_ref = r_cur
    else:
        p_ref = price_df.iloc[max(0, ref_end - window + 1):ref_end + 1]
        r_ref = p_ref.pct_change().dropna()
    cols = r_cur.columns.intersection(r_ref.columns)  # 列对齐(兼容早期缺新品种)
    r_cur, r_ref = r_cur[cols], r_ref[cols]
    mkt_cur = r_cur.mean(axis=1); mkt_ref = r_ref.mean(axis=1)
    drift = []
    for c in cols:
        rc = np.clip(r_cur[c].corr(mkt_cur), -0.999, 0.999)
        rr = np.clip(r_ref[c].corr(mkt_ref), -0.999, 0.999)
        drift.append(abs(np.arctanh(rc) - np.arctanh(rr)))
    avg_drift = float(np.mean(drift)) if drift else 0.0
    Rc = r_cur.corr().values; Rr = r_ref.corr().values
    try:
        dmat = logm(Rc) - logm(Rr)
        total_drift = float(np.sqrt(np.sum(np.real(dmat) ** 2)))
    except Exception:
        total_drift = 0.0
    return avg_drift, total_drift

price_df = load_prices()
print('22因子价格矩阵:', price_df.shape, price_df.index[0].date(), '~', price_df.index[-1].date())

base = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk_v2.pkl'))
dates = base.index
WINDOW, REF = 60, 60

print('逐日计算 SPD 漂移...')
avg_rows, total_rows = [], []
for i, d in enumerate(dates):
    if i == 0:
        avg_rows.append(np.nan); total_rows.append(np.nan)
        continue
    prev = dates[i - 1]
    pos = price_df.index.get_indexer([prev], method=None)[0]
    if pos < WINDOW + REF:
        avg_rows.append(np.nan); total_rows.append(np.nan)
        continue
    wind = price_df.iloc[pos - WINDOW + 1:pos + 1]
    a, t = drift_and_total(wind, price_df, pos, WINDOW, REF)
    avg_rows.append(a); total_rows.append(t)

spd = pd.DataFrame({'avg_drift': avg_rows, 'total_drift': total_rows}, index=dates)

# 分位数校准三档 (漂移高=风险高 -> 看跌)
def calib_tier(s, high_good=False):
    q25, q75 = s.quantile(0.25), s.quantile(0.75)
    if high_good:
        return np.where(s > q75, 1.0, np.where(s < q25, 0.40, 0.75))
    return np.where(s < q25, 1.0, np.where(s > q75, 0.40, 0.75))

combo['spd_total_pos'] = calib_tier(spd['total_drift'].fillna(spd['total_drift'].median()))
combo['spd_avg_pos'] = calib_tier(spd['avg_drift'].fillna(spd['avg_drift'].median()))
combo['spd_total_drift'] = spd['total_drift']
combo['spd_avg_drift'] = spd['avg_drift']

# ---------------- 3. 收益与指标 ----------------
hs = base['hs300_ret']
out = combo.copy()
for c in ['xiD_avg', 'xiD_min', 'spd_total', 'spd_avg']:
    pos = combo[f'{c}_pos']
    ret = pos * hs
    cum = (1 + ret).cumprod()
    out[f'{c}_ret'] = ret
    out[f'{c}_cum'] = cum
    out[f'{c}_cum_dd'] = cum / cum.cummax() - 1

def metrics(ret_s, cum_s, pos_s, rf=0.015):
    n = len(ret_s); years = n / 252
    total = cum_s.iloc[-1] - 1
    annual = (1 + total) ** (1 / years) - 1
    vol = ret_s.std() * np.sqrt(252)
    sharpe = (ret_s.mean() * 252 - rf) / vol if vol > 0 else 0
    mdd = (cum_s / cum_s.cummax() - 1).min()
    calmar = annual / abs(mdd) if mdd != 0 else 0
    win = (ret_s > 0).mean()
    return pd.Series({'总收益%': total * 100, '年化%': annual * 100, '年化波动%': vol * 100,
                      '夏普': sharpe, '最大回撤%': mdd * 100, '卡玛': calmar,
                      '胜率%': win * 100, '平均仓位%': pos_s.mean() * 100,
                      '日均换仓%': pos_s.diff().abs().mean() * 100})

rows = {}
rows['ξ关联长度·单信号'] = metrics(base['pg_cal_xi_ret'], base['pg_cal_xi_cum'], combo['xi_pos'])
rows['D分形维·单信号'] = metrics(base['pg_cal_D_ret'], base['pg_cal_D_cum'], combo['D_pos'])
rows['ξ+D 等权平均'] = metrics(out['xiD_avg_ret'], out['xiD_avg_cum'], combo['xiD_avg_pos'])
rows['ξ+D 保守合并(min)'] = metrics(out['xiD_min_ret'], out['xiD_min_cum'], combo['xiD_min_pos'])
rows['SPD全矩阵漂移(Log-Euc)'] = metrics(out['spd_total_ret'], out['spd_total_cum'], combo['spd_total_pos'])
rows['SPD平均Fisher-z漂移'] = metrics(out['spd_avg_ret'], out['spd_avg_cum'], combo['spd_avg_pos'])
# 参照
rows['HLM(MA60)·v2最优'] = metrics(base['hlm_ma60_ret'], base['hlm_ma60_cum'], base['hlm_ma60_pos'])
rows['基准·满仓HS300'] = metrics(base['hs300_ret'], base['bench_cum'], pd.Series(1.0, index=dates))
mt = pd.DataFrame(rows).T.round(2)

with open(os.path.join(WORK, 'backtest_xiD_spd.pkl'), 'wb') as fh:
    pickle.dump(out, fh)
mt.to_pickle(os.path.join(WORK, 'metrics_xiD_spd.pkl'))
pd.set_option('display.width', 220)
print(mt.to_string())
print('\nSPD漂移可用天数: %d/%d (前120日窗口不足期 NaN)' % (combo['spd_total_drift'].notna().sum(), len(dates)))