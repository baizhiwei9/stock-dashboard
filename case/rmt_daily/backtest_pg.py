#!/usr/bin/env python3
"""
物理几何分析风控 (PG) 回测：沪深300
======================================
忠实复刻线上「物理-几何市场分析」任务 (ses_gffe5fde225ad5ffePQAojl8RuNMiuQ) 的风控：
- 截面: 20标的 (5宽基指数+15行业ETF) 收益矩阵 rm (20 x 125日窗口, 线上逻辑)
- 物理引擎: Ising磁化 M / 耦合J / 关联长度xi / 温度T / 微观稳定MSF / RMT特征值
- 几何引擎: PCA流形 / Betti数 / Ricci曲率 / Morse形态 / Higuchi分形维D / Hurst H
- 融合状态机: classify_state -> opp_score / risk_score -> 牛/震荡/熊概率
- 仓位映射(假设, 可调): 仓位 = clip(1 - 0.8 * bear_prob, 0.2, 1.0)
  bear_prob = risk/(opp+risk), 即看跌概率越高仓位越低
统一约定: T-1日信号 -> T日生效, 与 RMT/TDA 回测口径一致
"""
import os, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)

# ========== 复刻线上 helpers (纯numpy, 来自 daily_physics_geo.py) ==========

def ising_M(rm):
    if rm is None or rm.shape[0] < 3: return None, None
    signs = np.sign(rm); signs[signs == 0] = 1
    M_s = signs.mean(axis=0)
    return float(M_s[-1]), M_s

def coupling_J(rm):
    if rm is None or rm.shape[0] < 3: return None
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    ev = np.linalg.eigvalsh(corr); ev = ev[ev > 0]
    if len(ev) == 0: return 0.5
    return float(np.log(1 + ev[-1] / max(ev.mean(), 0.01)))

def corr_len(rm):
    if rm is None or rm.shape[0] < 3: return None
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    ev = np.linalg.eigvalsh(corr); ev = ev[ev > 0]
    if len(ev) < 2: return 0
    diff = ev[-1] - ev.mean()
    return 100.0 if diff < 1e-6 else float(1.0 / np.sqrt(diff))

def msf_calc(rm):
    if rm is None or rm.shape[0] < 3 or rm.shape[1] < 4: return None, None
    signs = np.sign(rm); signs[signs == 0] = 1
    s1, s2 = signs[:, -2], signs[:, -1]
    inter = np.sum((s1 == 1) & (s2 == 1)) + np.sum((s1 == -1) & (s2 == -1))
    m = float(inter / len(s1))
    ms = []
    for i in range(2, min(8, signs.shape[1])):
        a, b = signs[:, -i-1], signs[:, -i]
        mi = np.sum((a == 1) & (b == 1)) + np.sum((a == -1) & (b == -1))
        ms.append(mi / len(a))
    mp = float(np.mean(ms[:3])) if ms else m
    return m, m - mp

def rmt_calc(rm):
    if rm is None or rm.shape[0] < 3: return None, None, None
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    ev = np.linalg.eigvalsh(corr); ev = ev[ev > 0]
    N, T = rm.shape
    if T <= N: return None, None, None
    lp = (1 + np.sqrt(N / T)) ** 2
    lm = float(ev[-1]) if len(ev) > 0 else 0
    return lm, float(lp), float(lm / max(lp, 0.01))

def pca_manifold(rm):
    if rm is None or rm.shape[0] < 3: return '未知', 0, 0
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    ev = np.sort(np.linalg.eigvalsh(corr))[::-1]; ev = ev[ev > 0]
    if len(ev) < 2: return '未知', 0, 0
    t = ev.sum(); p1 = ev[0] / t; p2 = ev[1] / t
    if p1 > 0.5 and p2 < 0.1: return '丝带', round(float(p1), 3), round(float(p2), 3)
    if p1 > 0.4: return '球状', round(float(p1), 3), round(float(p2), 3)
    if p1 < 0.25 and p2 > 0.15: return '散点', round(float(p1), 3), round(float(p2), 3)
    return '椭球', round(float(p1), 3), round(float(p2), 3)

def betti(rm, th=0.5):
    if rm is None or rm.shape[0] < 3: return 0, 0
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    n = corr.shape[0]; vis = [False] * n; comp = 0
    for i in range(n):
        if not vis[i]:
            comp += 1; st = [i]
            while st:
                nd = st.pop()
                if vis[nd]: continue
                vis[nd] = True
                for j in range(n):
                    if not vis[j] and abs(corr[nd, j]) > th: st.append(j)
    tr = 0
    for i in range(min(n, 15)):
        for j in range(i + 1, min(n, 15)):
            for k in range(j + 1, min(n, 15)):
                if abs(corr[i, j]) > th and abs(corr[j, k]) > th and abs(corr[i, k]) > th: tr += 1
    return comp, max(0, tr - n + comp) if tr > 0 else 0

def ricci(rm):
    if rm is None or rm.shape[0] < 3: return None
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    return round(float(corr[np.triu_indices(corr.shape[0], k=1)].mean()), 3)

def morse(rets):
    if rets is None or len(rets) < 30: return '未知', 0
    ma = pd.Series(rets).rolling(20).mean().dropna().values
    if len(ma) < 5: return '未知', 0
    sl = float(ma[-1] - ma[-5]); cr = float(np.mean(rets[-5:]))
    if sl > 0 and cr > 0: return '上升坡面', round(sl * 1000, 2)
    if sl > 0 and cr < 0: return '山顶', round(sl * 1000, 2)
    if sl < 0 and cr > 0: return '山谷', round(sl * 1000, 2)
    return '下降坡面', round(sl * 1000, 2)

def higuchi(s, kmax=8):
    if s is None or len(s) < 20: return 1.5
    n = len(s); L = []; x = np.arange(1, kmax + 1)
    for k in range(1, kmax + 1):
        Lk = 0
        for m in range(k):
            nm = int((n - m - 1) / k) + 1
            if nm < 2: continue
            idx = np.arange(m, min(m + nm * k, n), k)
            if len(idx) < 2: continue
            ll = np.sum(np.abs(np.diff(s[idx]))) * (n - 1) / (nm * k); Lk += ll
        L.append(Lk / k)
    L = np.array(L); v = L > 0
    if v.sum() < 3: return 1.5
    c = np.polyfit(np.log(1.0 / x[v]), np.log(L[v]), 1)
    return round(max(1.0, min(2.0, float(c[0]))), 3)

def hurst(r):
    if r is None or len(r) < 30: return 0.5
    s = np.cumsum(r - np.mean(r)); n = len(s); mk = min(n // 2, 60)
    rl, kl = [], []
    for k in range(5, mk):
        nb = n // k
        if nb < 1: continue
        rsv = []
        for i in range(nb):
            b = s[i*k:(i+1)*k]
            if len(b) < 2: continue
            a = b - np.mean(b); ca = np.cumsum(a)
            rng = np.max(ca) - np.min(ca); sd = np.std(b)
            if sd > 1e-8: rsv.append(rng / sd)
        if rsv: rl.append(np.mean(rsv)); kl.append(k)
    if len(rl) < 3: return 0.5
    c = np.polyfit(np.log(kl), np.log(rl), 1)
    return round(max(0.0, min(1.0, float(c[0]))), 3)

def classify_state(physics, geometry):
    """复刻 analysis.py classify_state (指数分支, 不含期货)"""
    M = physics.get('M', 0) or 0
    J = physics.get('J', 0.5) or 0.5
    T = physics.get('T', 20) or 20
    MSF = physics.get('MSF', 0.8) or 0.8
    MSF_chg = physics.get('MSF_change', 0) or 0
    xi = physics.get('xi', 5) or 5
    morse_v = geometry.get('morse', '')
    ricci_v = geometry.get('ricci', 0) or 0
    D = geometry.get('D', 1.5) or 1.5
    H = geometry.get('H', 0.5) or 0.5

    opp_score, risk_score = 50, 50
    if M is not None:
        if M <= -0.3:
            opp_score += 15; risk_score += 5
        elif M > 0.3:
            opp_score += 5; risk_score += 3
        if abs(M) > 0.7:
            risk_score += 15
    if J is not None and J > 1.5:
        risk_score += 10
    elif J is not None and J < 0.8:
        opp_score += 3
    if MSF is not None:
        if MSF < 0.7:
            risk_score += 12
        elif MSF > 0.85:
            opp_score += 8
    if MSF_chg is not None and MSF_chg < -0.1:
        risk_score += 10
    if xi is not None and xi > 10:
        risk_score += 8
    if '上升' in morse_v or '山谷' in morse_v:
        opp_score += 10
    if '山顶' in morse_v or '下降' in morse_v:
        risk_score += 8
    if ricci_v is not None:
        if ricci_v > 0.3:
            opp_score += 5
        elif ricci_v < 0.1:
            risk_score += 8
    if H is not None:
        if H > 0.6:
            opp_score += 8
        elif H < 0.4:
            risk_score += 5
    if D is not None:
        if D > 1.7:
            risk_score += 8
        elif D < 1.3:
            opp_score += 5

    opp_score = max(0, min(100, opp_score))
    risk_score = max(0, min(100, risk_score))

    if abs(M or 0) > 0.7 and (MSF or 0.8) < 0.75 and (xi or 5) > 8:
        state = '临界相变前夜'
    elif (MSF or 0.8) > 0.85 and (H or 0.5) > 0.6 and '上升' in (morse_v or ''):
        state = '有序趋势市'
    elif abs(M or 0) < 0.2 and (T or 20) > 25 and (MSF or 0.8) < 0.75:
        state = '无序震荡市'
    elif (M or 0) <= -0.3 and (MSF or 0.8) > 0.75 and '山谷' in (morse_v or ''):
        state = '超卖反弹'
    else:
        state = '过渡态'

    total = opp_score + risk_score
    if total > 0:
        bull_prob = round(opp_score / total * 100)
        bear_prob = round(risk_score / total * 100)
        range_prob = 100 - bull_prob - bear_prob
    else:
        bull_prob, range_prob, bear_prob = 33, 34, 33
    return {'state': state, 'opp_score': opp_score, 'risk_score': risk_score,
            'bull_prob': max(0, bull_prob), 'range_prob': max(0, range_prob),
            'bear_prob': max(0, bear_prob)}

WINDOW = 125  # 线上截面矩阵窗口 (~125日)

# ========== 载入数据 ==========
panel = pd.read_pickle(os.path.join(WORK, 'pg_universe.pkl'))  # date x 20
rets_panel = panel.pct_change().dropna(how='all')  # 每列收益

base = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))
dates = base.index  # 2022-01-05 ~ 2026-08-19

hs300_close = panel['HS300']  # HS300 昨收系列 (reits 来自 panel, 与 rets_panel 同一日历)

# 收益矩阵 slice: 截面收益序列 (20 x N), 逐日用 rolling 窗口
cross_ret = rets_panel.values.T  # 20 x T
col_dates = rets_panel.index
date_pos = {d: i for i, d in enumerate(col_dates)}

print('回测逐日计算物理几何指标...')
pos_list, state_list, opp_list, risk_list, bear_list = [], [], [], [], []
for i, d in enumerate(dates):
    if i == 0:
        pos_list.append(1.0); state_list.append('过渡态'); opp_list.append(50); risk_list.append(50); bear_list.append(0.5)
        continue
    # 截至 T-1 的窗口
    prev_idx = date_pos[dates[i-1]]
    start = prev_idx - WINDOW + 1
    if start < 0:
        start = 0
    rm = cross_ret[:, start:prev_idx+1]  # 20 x w (含T-1)
    if rm.shape[1] < 30:
        pos_list.append(1.0); state_list.append('过渡态'); opp_list.append(50); risk_list.append(50); bear_list.append(0.5)
        continue
    # 物理
    M, Ms = ising_M(rm); J = coupling_J(rm); xi = corr_len(rm)
    m, mc = msf_calc(rm); lm, lp, ex = rmt_calc(rm)
    hs_rets = rets_panel['HS300'].iloc[:prev_idx+1].dropna().values
    T_val = float(np.std(hs_rets[-20:]) * np.sqrt(252) * 100) if len(hs_rets) >= 20 else 20.0
    physics = {'M': M, 'M_prev': float(Ms[-3]) if Ms is not None and len(Ms) > 2 else 0,
               'J': J, 'T': round(T_val, 2), 'xi': xi, 'MSF': m, 'MSF_change': mc,
               'lambda_max': lm, 'excess_ratio': ex}
    # 几何 (HS300 自身)
    mf, p1, p2 = pca_manifold(rm); b0, b1 = betti(rm); rc = ricci(rm)
    mo, mv = morse(hs_rets)
    D = higuchi(hs_rets[-120:] if len(hs_rets) >= 120 else hs_rets)
    H = hurst(hs_rets)
    geometry = {'manifold': mf, 'betti_0': b0, 'betti_1': b1, 'ricci': rc,
                'morse': mo, 'D': D, 'H': H}
    # 融合
    fusion = classify_state(physics, geometry)
    bear = fusion['risk_score'] / max(fusion['opp_score'] + fusion['risk_score'], 1)
    pos = float(np.clip(1 - 0.8 * bear, 0.2, 1.0))
    pos_list.append(pos); state_list.append(fusion['state'])
    opp_list.append(fusion['opp_score']); risk_list.append(fusion['risk_score']); bear_list.append(bear)

pg_pos = pd.Series(pos_list, index=dates)
pg_state = pd.Series(state_list, index=dates)

# ========== 并入总表 ==========
hs300_ret = base['hs300_ret']
pg_ret = pg_pos * hs300_ret
pg_cum = (1 + pg_ret).cumprod()

out = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))
out['pg_ret'] = pg_ret
out['pg_pos'] = pg_pos
out['pg_state'] = pg_state
out['pg_cum'] = pg_cum
out['pg_cum_dd'] = pg_cum / pg_cum.cummax() - 1
out.to_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))

# ========== 汇总 ==========
def metrics(ret, cum, rf=0.015):
    n = len(ret); years = n / 252
    total = cum.iloc[-1] - 1; annual = (1 + total) ** (1 / years) - 1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252 - rf) / vol if vol > 0 else 0
    mdd = (cum / cum.cummax() - 1).min()
    calmar = annual / abs(mdd) if mdd != 0 else 0
    win = (ret > 0).mean()
    return total, annual, vol, sharpe, mdd, calmar, win

import time
time.sleep(0)
for name in ['rmt', 'tda', 'hlm', 'chaos', 'pg']:
    t, a, v, s, md, c, w = metrics(out[f'{name}_ret'], out[f'{name}_cum'])
    print(f'{name:6s} 总收益{t*100:7.2f}% 年化{a*100:7.2f}% 波动{v*100:6.2f}% 夏普{s:6.2f} 回撤{md*100:7.2f}% 卡玛{c:6.2f} 仓位{out[f"{name}_pos"].mean()*100:5.1f}%')
print('\nPG 状态分布:')
print(pg_state.value_counts().to_string())
print('PG 平均仓位: %.1f%%' % (pg_pos.mean() * 100))
print('已更新 backtest_multi_risk.pkl')