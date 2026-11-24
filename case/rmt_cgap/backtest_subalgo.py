#!/usr/bin/env python3
"""
物理几何分析风控 (PG) 子算法独立回测：沪深300
===============================================
将线上「物理-几何市场分析」任务 (ses_gffe5fde225ad5ffePQAojl8RuNMiuQ) 的每个子算法组件
分别作为独立风控回测：
- 物理侧: Ising磁化 M / 耦合 J / 关联长度 xi / 温度 T / 微观稳定 MSF / RMT谱(excess_ratio)
- 几何侧: PCA流形 / Betti数 / Ricci曲率 / Morse形态 / Higuchi分形维 D / Hurst H
每个子算法独立映射为三档仓位(100% / 75% / 40%)：
- 看涨档: 该特征落入系统打分规则中的 opp_score+ 区间 -> 满仓
- 看跌档: 该特征落入系统打分规则中的 risk_score+ 区间 -> 40%
- 中性档: 其余 -> 75%
阈值全部取自 classify_state 打分规则 (backtest_pg.py L153-230)。
统一口径: T-1日信号 -> T日生效, 与 RMT/TDA/HLM/混沌 回测一致。
输出: backtest_subalgo.pkl (子算法仓位 + 逐日特征长表, 供报告组装)
"""
import os, sys, warnings, pickle
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)

# ========== 复刻线上 helpers (与 backtest_pg.py 完全一致) ==========

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

# ========== 子算法三档仓位映射 (阈值取自 classify_state 打分规则) ==========
# 看涨条件 -> 100%, 看跌条件 -> 40%, 其余(含None) -> 75%

def tier_pos(bull_cond, bear_cond, feat):
    if feat is None:
        return 0.75
    if bull_cond:
        return 1.0
    if bear_cond:
        return 0.40
    return 0.75

SUBALGOS = {
    # 物理侧
    'pg_M_magnet':        lambda f: tier_pos(f['M'] is not None and f['M'] > 0.3,
                                             f['M'] is not None and f['M'] <= -0.3, f['M']),
    'pg_J_coupling':      lambda f: tier_pos(f['J'] is not None and f['J'] < 0.8,
                                             f['J'] is not None and f['J'] > 1.5, f['J']),
    'pg_xi_corrlen':      lambda f: tier_pos(f['xi'] is not None and f['xi'] < 5,
                                             f['xi'] is not None and f['xi'] > 10, f['xi']),
    'pg_T_temperature':   lambda f: tier_pos(f['T'] is not None and f['T'] < 20,
                                             f['T'] is not None and f['T'] > 25, f['T']),
    'pg_MSF_stability':   lambda f: tier_pos(f['MSF'] is not None and f['MSF'] > 0.85,
                                             f['MSF'] is not None and f['MSF'] < 0.7, f['MSF']),
    'pg_RMT_spectrum':    lambda f: tier_pos(f['excess'] is not None and f['excess'] < 1.5,
                                             f['excess'] is not None and f['excess'] > 3, f['excess']),
    # 几何侧
    'pg_PCA_manifold':    lambda f: tier_pos(f['manifold'] in ('丝带', '球状'),
                                             f['manifold'] == '散点', f['manifold']),
    'pg_Betti_holes':     lambda f: tier_pos(f['betti_1'] is not None and f['betti_1'] > 6,
                                             f['betti_1'] is not None and f['betti_1'] < 2, f['betti_1']),
    'pg_Ricci_curv':      lambda f: tier_pos(f['ricci'] is not None and f['ricci'] > 0.3,
                                             f['ricci'] is not None and f['ricci'] < 0.1, f['ricci']),
    'pg_Morse_shape':     lambda f: tier_pos(f['morse'] in ('上升坡面', '山谷'),
                                             f['morse'] in ('山顶', '下降坡面'), f['morse']),
    'pg_D_fractal':       lambda f: tier_pos(f['D'] is not None and f['D'] < 1.3,
                                             f['D'] is not None and f['D'] > 1.7, f['D']),
    'pg_H_hurst':         lambda f: tier_pos(f['H'] is not None and f['H'] > 0.6,
                                             f['H'] is not None and f['H'] < 0.4, f['H']),
}

WINDOW = 125

# ========== 载入数据 ==========
panel = pd.read_pickle(os.path.join(WORK, 'pg_universe.pkl'))  # date x 20
rets_panel = panel.pct_change().dropna(how='all')

base = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))
dates = base.index

cross_ret = rets_panel.values.T  # 20 x T
col_dates = rets_panel.index
date_pos = {d: i for i, d in enumerate(col_dates)}

print('回测逐日计算物理几何指标 + 子算法仓位...')
feat_rows = []
pos_series = {k: [] for k in SUBALGOS}
pos_series['pg_fusion'] = []
for i, d in enumerate(dates):
    if i == 0 or True:
        pass
    prev_idx = date_pos[dates[i-1]] if i > 0 else None
    if prev_idx is None:
        for k in SUBALGOS: pos_series[k].append(0.75)
        pos_series['pg_fusion'].append(1.0)
        feat_rows.append({'M': None, 'J': None, 'xi': None, 'T': None, 'MSF': None,
                          'excess': None, 'manifold': '未知', 'betti_1': None,
                          'ricci': None, 'morse': '未知', 'D': None, 'H': None})
        continue
    start = prev_idx - WINDOW + 1
    if start < 0: start = 0
    rm = cross_ret[:, start:prev_idx+1]
    if rm.shape[1] < 30:
        for k in SUBALGOS: pos_series[k].append(0.75)
        pos_series['pg_fusion'].append(1.0)
        feat_rows.append({'M': None, 'J': None, 'xi': None, 'T': None, 'MSF': None,
                          'excess': None, 'manifold': '未知', 'betti_1': None,
                          'ricci': None, 'morse': '未知', 'D': None, 'H': None})
        continue
    # 物理
    M, Ms = ising_M(rm); J = coupling_J(rm); xi = corr_len(rm)
    m, mc = msf_calc(rm); lm, lp, ex = rmt_calc(rm)
    hs_rets = rets_panel['HS300'].iloc[:prev_idx+1].dropna().values
    T_val = float(np.std(hs_rets[-20:]) * np.sqrt(252) * 100) if len(hs_rets) >= 20 else 20.0
    # 几何
    mf, p1, p2 = pca_manifold(rm); b0, b1 = betti(rm); rc = ricci(rm)
    mo, mv = morse(hs_rets)
    D = higuchi(hs_rets[-120:] if len(hs_rets) >= 120 else hs_rets)
    H = hurst(hs_rets)
    feats = {'M': M, 'J': J, 'xi': xi, 'T': round(T_val, 2), 'MSF': m,
             'excess': ex, 'manifold': mf, 'betti_1': b1, 'ricci': rc,
             'morse': mo, 'D': D, 'H': H}
    for k, fn in SUBALGOS.items():
        pos_series[k].append(fn(feats))
    physics = {'M': M, 'J': J, 'T': round(T_val, 2), 'xi': xi, 'MSF': m,
               'MSF_change': mc, 'lambda_max': lm, 'excess_ratio': ex}
    geometry = {'manifold': mf, 'betti_1': b1, 'ricci': rc, 'morse': mo, 'D': D, 'H': H}
    fusion = classify_state(physics, geometry)
    bear = fusion['risk_score'] / max(fusion['opp_score'] + fusion['risk_score'], 1)
    pos_series['pg_fusion'].append(float(np.clip(1 - 0.8 * bear, 0.2, 1.0)))
    fr = dict(feats); fr['state'] = fusion['state']
    feat_rows.append(fr)

sub_df = pd.DataFrame({k: pd.Series(v, index=dates) for k, v in pos_series.items()})
feat_df = pd.DataFrame(feat_rows, index=dates)

# ========== 校验: 融合仓位与线上 v1 一致 ==========
diff = (sub_df['pg_fusion'] - base['pg_pos']).abs().max()
print(f'pg_fusion 与 v1 pg_pos 最大偏差: {diff:.6f}')
assert diff < 1e-9, '融合仓位与 v1 不一致, 请检查复刻逻辑'

out = {'sub_pos': sub_df, 'features': feat_df}
with open(os.path.join(WORK, 'backtest_subalgo.pkl'), 'wb') as fh:
    pickle.dump(out, fh)
print('已保存 backtest_subalgo.pkl:')
print('  子算法仓位列:', list(sub_df.columns))
print('  特征长表:', feat_df.shape, feat_df.index[0], '->', feat_df.index[-1])
print('  平均仓位:')
print((sub_df.mean() * 100).round(1).to_string())