#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四系统整合: Hodge Laplacian + RMT随机矩阵 + SPD谱属性分解 + 曼宁资金流相变
概念轮动综合评价系统 (37概念版)

整合逻辑:
  1. 分别计算四套系统的评分和信号
  2. 综合评分 = 四系统评分之和 (范围 -28~+28)
  3. 投票: >=2套系统判"机会" = 强机会, 1套 = 弱机会
  4. 液冷概念验证: 至少1套系统标记为机会
"""
import pandas as pd
import numpy as np
from numpy.linalg import eigh
import sys

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

# ===================== 公共参数 =====================
MA_PERIOD = 40
MOM_PERIOD = 20
CORR_WINDOW = 30
CORR_THRESHOLD = 0.3
TOP_K_EIGEN = 5
SPECTRAL_ANGLE_THRESHOLD = 0.3
DELTA_S_THRESHOLD = 0.5
RMT_WINDOW = 60
TOP_K = 5
FACTOR_THRESHOLD = 0.10
SPD_ANGLE_THRESHOLD = 0.5
FLOW_WINDOW = 10
PHASE_WINDOW = 20

def load_data():
    returns = pd.read_pickle(f'{OUT_DIR}/concept_returns.pkl')
    close = pd.read_pickle(f'{OUT_DIR}/concept_close.pkl')
    volume = pd.read_pickle(f'{OUT_DIR}/concept_volume.pkl')
    return returns, close, volume

def compute_ma(close, period=40):
    return close.rolling(window=period, min_periods=1).mean()

def compute_momentum(close, period=20):
    return close.pct_change(periods=period) * 100

# ===================== Hodge 系统 =====================
def run_hodge(returns, close, concept_names):
    N = len(concept_names)
    n_days = len(returns)

    # 滚动Laplacian
    spectral_angles = []
    fiedler_history = []
    for t in range(CORR_WINDOW, n_days + 1):
        window = returns.iloc[t - CORR_WINDOW:t]
        corr = window.corr().fillna(0).values
        adj = (np.abs(corr) > CORR_THRESHOLD).astype(float)
        np.fill_diagonal(adj, 0)
        adj_w = corr * adj
        D = np.diag(adj_w.sum(axis=1))
        L = D - adj_w
        evals, evecs = eigh(L)
        idx = np.argsort(evals)
        evals = evals[idx]
        evecs = evecs[:, idx]

        fvec = evecs[:, 1] if len(evals) > 1 else np.zeros(N)
        fanalysis = {}
        for i, name in enumerate(concept_names):
            fanalysis[name] = {
                'fiedler_component': float(fvec[i]),
                'community': 'leading' if fvec[i] >= 0 else 'lagging',
            }
        fiedler_history.append({'analysis': fanalysis})

        if t > CORR_WINDOW:
            prev_window = returns.iloc[t - CORR_WINDOW - 1: t - 1]
            pc = prev_window.corr().fillna(0).values
            pa = (np.abs(pc) > CORR_THRESHOLD).astype(float)
            np.fill_diagonal(pa, 0)
            paw = pc * pa
            pD = np.diag(paw.sum(axis=1))
            pL = pD - paw
            _, prev_evecs = eigh(pL)
            idx2 = np.argsort(eigh(pL)[0])
            prev_evecs = prev_evecs[:, idx2]

            k = min(TOP_K_EIGEN, evecs.shape[1] - 1, prev_evecs.shape[1] - 1)
            if k >= 1:
                V_c = evecs[:, 1:k+1]
                V_p = prev_evecs[:, 1:k+1]
                P_c = V_c @ V_c.T
                P_p = V_p @ V_p.T
                overlap = np.trace(P_c @ P_p)
                cos_a = np.clip(overlap / k, -1, 1)
                spectral_angles.append(float(np.arccos(cos_a)))

    latest_angle = spectral_angles[-1] if spectral_angles else 0
    avg_angle = np.mean(spectral_angles[-5:]) if len(spectral_angles) >= 5 else latest_angle
    if avg_angle < 0.15:
        stability = 'stable'
    elif avg_angle < SPECTRAL_ANGLE_THRESHOLD:
        stability = 'shifting'
    else:
        stability = 'unstable'

    current_fiedler = fiedler_history[-1]['analysis'] if fiedler_history else {}
    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]

    signals = {}
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0

        fi = current_fiedler.get(name, {})
        community = fi.get('community', 'unknown')

        trend_score = 2 if above_ma else -2
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        fiedler_score = 1 if community == 'leading' else -1
        if stability == 'stable': struct_score = 1
        elif stability == 'shifting': struct_score = 0
        else: struct_score = -1

        total = trend_score + mom_score + fiedler_score + struct_score
        if total >= 3: ev = '机会'
        elif total >= 1: ev = '偏多'
        elif total >= -1: ev = '中性'
        elif total >= -3: ev = '偏空'
        else: ev = '风险'

        signals[name] = {
            'score': total, 'eval': ev, 'above_ma': above_ma,
            'ma_gap': round(ma_gap, 2), 'mom': round(c_mom, 2),
            'community': community, 'stability': stability,
            'latest_angle': round(latest_angle, 4),
        }
    return signals

# ===================== RMT 系统 =====================
def run_rmt(returns, close, concept_names):
    N = len(concept_names)
    T = min(RMT_WINDOW, len(returns))
    Q = T / N if N > 0 else 1
    lambda_minus = (1 - 1/np.sqrt(Q))**2
    lambda_plus = (1 + 1/np.sqrt(Q))**2

    returns_window = returns.iloc[-T:]
    corr = returns_window.corr().fillna(0).values
    corr = (corr + corr.T) / 2

    evals, evecs = eigh(corr)
    idx = np.argsort(evals)[::-1]
    evals = evals[idx]
    evecs = evecs[:, idx]

    noise_mask = (evals >= lambda_minus) & (evals <= lambda_plus)
    denoised = evals.copy()
    denoised[noise_mask] = 0.0
    corr_denoised = evecs @ np.diag(denoised) @ evecs.T
    d = np.sqrt(np.diag(corr_denoised))
    d[d == 0] = 1
    corr_denoised = corr_denoised / np.outer(d, d)
    np.fill_diagonal(corr_denoised, 1.0)

    # delta_s
    total_energy = np.sum(evals**2)
    signal_energy = np.sum(evals[~noise_mask]**2)
    delta_s = signal_energy / total_energy if total_energy > 0 else 0
    extreme_state = delta_s > DELTA_S_THRESHOLD

    # concept avg corr
    concept_avg_corr = {}
    for i, name in enumerate(concept_names):
        row = corr_denoised[i, :].copy()
        row[i] = 0
        concept_avg_corr[name] = float(row.mean())

    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]
    mom_ranked = latest_mom.sort_values(ascending=False)
    top5 = set(mom_ranked.head(TOP_K).index)
    bottom5 = set(mom_ranked.tail(TOP_K).index)

    if extreme_state:
        rmt_mode = 'extreme'
        ds_score = -1
    elif delta_s > DELTA_S_THRESHOLD * 0.7:
        rmt_mode = 'elevated'
        ds_score = 0
    elif delta_s > DELTA_S_THRESHOLD * 0.4:
        rmt_mode = 'normal'
        ds_score = 1
    else:
        rmt_mode = 'dispersed'
        ds_score = 0

    signals = {}
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0

        is_top = name in top5
        is_bottom = name in bottom5
        mom_rank = mom_ranked.index.get_loc(name) + 1
        avg_c = concept_avg_corr.get(name, 0)

        trend_score = 2 if above_ma else -2
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        top_score = 2 if is_top else (1 if mom_rank <= TOP_K + 3 else 0)
        bottom_score = -2 if is_bottom else 0
        if avg_c < 0.1: corr_score = 1
        elif avg_c < 0.3: corr_score = 0
        else: corr_score = -1

        total = trend_score + mom_score + top_score + bottom_score + ds_score + corr_score
        if total >= 4: ev = '机会'
        elif total >= 2: ev = '偏多'
        elif total >= 0: ev = '中性'
        elif total >= -2: ev = '偏空'
        else: ev = '风险'

        if extreme_state and ev in ['机会', '偏多']:
            ev = '中性'

        signals[name] = {
            'score': total, 'eval': ev, 'above_ma': above_ma,
            'ma_gap': round(ma_gap, 2), 'mom': round(c_mom, 2),
            'mom_rank': mom_rank, 'is_top5': is_top,
            'avg_corr': round(avg_c, 4), 'delta_s': round(delta_s, 4),
            'rmt_mode': rmt_mode,
        }
    return signals

# ===================== SPD 系统 =====================
def run_spd(returns, close, concept_names):
    N = len(concept_names)
    corr = returns.corr().fillna(0).values
    corr = (corr + corr.T) / 2
    evals, evecs = eigh(corr)
    idx = np.argsort(evals)[::-1]
    evals = evals[idx]
    evecs = evecs[:, idx]

    var_pct = evals / evals.sum()
    significant = var_pct > FACTOR_THRESHOLD
    n_sig = significant.sum()

    total_e = evals.sum()
    spectral_density = evals[:max(1, n_sig)].sum() / total_e if total_e > 0 else 0

    market_factor = evecs[:, 0]

    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]

    signals = {}
    k_factors = max(2, n_sig + 1)
    for i, name in enumerate(concept_names):
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0

        concept_vec = evecs[i, :k_factors]
        norm_v = np.linalg.norm(concept_vec)
        if norm_v > 0:
            cos_a = np.clip(abs(concept_vec[0]) / norm_v, -1, 1)
            angle = float(np.arccos(cos_a))
        else:
            angle = 0.0
        ml = float(concept_vec[0] ** 2)

        trend_score = 2 if above_ma else -2
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        if angle < 0.3: angle_score = 2
        elif angle < SPD_ANGLE_THRESHOLD: angle_score = 1
        elif angle < 0.8: angle_score = 0
        else: angle_score = -1
        if ml > 0.15: loading_score = 1
        elif ml > 0.05: loading_score = 0
        else: loading_score = -1
        if spectral_density > 0.6: density_score = 1
        elif spectral_density > 0.4: density_score = 0
        else: density_score = -1

        total = trend_score + mom_score + angle_score + loading_score + density_score
        if total >= 4: ev = '机会'
        elif total >= 2: ev = '偏多'
        elif total >= 0: ev = '中性'
        elif total >= -2: ev = '偏空'
        else: ev = '风险'

        signals[name] = {
            'score': total, 'eval': ev, 'above_ma': above_ma,
            'ma_gap': round(ma_gap, 2), 'mom': round(c_mom, 2),
            'spectral_angle': round(angle, 4), 'market_loading': round(ml, 4),
            'spectral_density': round(spectral_density, 4),
        }
    return signals

# ===================== 曼宁资金流 系统 =====================
def run_manning(returns, close, volume, concept_names):
    vol_aligned = volume.reindex(returns.index).ffill().fillna(0)
    ret_aligned = returns.fillna(0)

    signed_return = np.sign(ret_aligned) * np.abs(ret_aligned)
    money_flow = signed_return * vol_aligned

    mf_sum = money_flow.rolling(window=FLOW_WINDOW, min_periods=1).sum()
    vol_sum = vol_aligned.rolling(window=FLOW_WINDOW, min_periods=1).sum()
    vol_sum = vol_sum.replace(0, np.nan)
    cmf = (mf_sum / vol_sum).fillna(0)

    forward_avg = cmf.rolling(window=PHASE_WINDOW, min_periods=1).mean()
    backward_avg = forward_avg.shift(PHASE_WINDOW)
    phase = forward_avg - backward_avg

    persistence = (cmf > 0).rolling(window=PHASE_WINDOW, min_periods=1).mean()

    mf_ma = money_flow.rolling(window=FLOW_WINDOW, min_periods=1).mean()
    mf_prev = mf_ma.shift(FLOW_WINDOW)
    mfm = ((mf_ma - mf_prev) / (mf_prev.abs() + 1e-10)).replace([np.inf, -np.inf], 0).fillna(0)

    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]
    latest_cmf = cmf.iloc[-1]
    latest_phase = phase.iloc[-1]
    latest_persist = persistence.iloc[-1]
    latest_mfm = mfm.iloc[-1]

    signals = {}
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0

        c_cmf = float(latest_cmf[name]) if not np.isnan(latest_cmf[name]) else 0
        c_phase = float(latest_phase[name]) if not np.isnan(latest_phase[name]) else 0
        c_persist = float(latest_persist[name]) if not np.isnan(latest_persist[name]) else 0
        c_mfm_val = float(latest_mfm[name]) if not np.isnan(latest_mfm[name]) else 0

        trend_score = 2 if above_ma else -2
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        if c_cmf > 0.1: cmf_score = 2
        elif c_cmf > 0: cmf_score = 1
        elif c_cmf < -0.1: cmf_score = -2
        elif c_cmf < 0: cmf_score = -1
        else: cmf_score = 0
        if c_phase > 0.002: phase_score = 2
        elif c_phase > 0: phase_score = 1
        elif c_phase < -0.002: phase_score = -2
        elif c_phase < 0: phase_score = -1
        else: phase_score = 0
        if c_persist > 0.6: persist_score = 1
        elif c_persist < 0.4: persist_score = -1
        else: persist_score = 0
        if c_mfm_val > 0.5: mfm_score = 1
        elif c_mfm_val < -0.5: mfm_score = -1
        else: mfm_score = 0

        total = trend_score + mom_score + cmf_score + phase_score + persist_score + mfm_score
        if total >= 4: ev = '机会'
        elif total >= 2: ev = '偏多'
        elif total >= 0: ev = '中性'
        elif total >= -2: ev = '偏空'
        else: ev = '风险'

        signals[name] = {
            'score': total, 'eval': ev, 'above_ma': above_ma,
            'ma_gap': round(ma_gap, 2), 'mom': round(c_mom, 2),
            'cmf': round(c_cmf, 4), 'phase': round(c_phase, 4),
            'persistence': round(c_persist, 2), 'mfm': round(c_mfm_val, 4),
        }
    return signals

# ===================== 最优传输 OT 系统 =====================
def run_ot(returns, close, concept_names):
    """Wasserstein 最优传输分布漂移探测
    W1 高 = 收益分布正在偏移 = 可能进入新趋势
    W1 低 = 分布稳定 = 震荡噪声
    概念级: 双窗口 w15=0.6+w20=0.4, 滚动 Q70 阈值"""
    W1_WINDOWS = [15, 20]
    W1_WEIGHTS = [0.6, 0.4]
    OT_QUANTILE = 0.70

    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]

    signals = {}
    for name in concept_names:
        ret_series = returns[name].dropna()
        n = len(ret_series)
        vals = ret_series.values

        w1_combined = np.full(n, np.nan)
        dir_combined = np.full(n, 0.0)

        for wi, w in enumerate(W1_WINDOWS):
            w1_w = np.full(n, np.nan)
            dir_w = np.full(n, 0.0)
            for i in range(w * 2, n):
                prev = vals[i - w * 2 : i - w]
                curr = vals[i - w : i]
                mask_p = ~np.isnan(prev)
                mask_c = ~np.isnan(curr)
                if mask_p.sum() < 3 or mask_c.sum() < 3:
                    continue
                prev_s = np.sort(prev[mask_p])
                curr_s = np.sort(curr[mask_c])
                min_len = min(len(prev_s), len(curr_s))
                w1_w[i] = np.mean(np.abs(curr_s[:min_len] - prev_s[:min_len]))
                dir_w[i] = np.sign(np.mean(curr[mask_c]) - np.mean(prev[mask_p]))
            for i in range(n):
                if not np.isnan(w1_w[i]):
                    wt = W1_WEIGHTS[wi]
                    if np.isnan(w1_combined[i]):
                        w1_combined[i] = w1_w[i] * wt
                    else:
                        w1_combined[i] += w1_w[i] * wt
                    dir_combined[i] = dir_w[i]

        w1_series = pd.Series(w1_combined)
        q70 = w1_series.rolling(252, min_periods=10).quantile(OT_QUANTILE).values
        q90 = w1_series.rolling(252, min_periods=10).quantile(0.90).values

        li = n - 1
        latest_w1 = w1_combined[li] if not np.isnan(w1_combined[li]) else 0.0
        valid_w1 = w1_combined[~np.isnan(w1_combined)]
        med = float(np.nanmedian(valid_w1)) if len(valid_w1) > 0 else 0.0
        latest_q70 = q70[li] if not np.isnan(q70[li]) else med
        latest_q90 = q90[li] if not np.isnan(q90[li]) else med * 1.5
        latest_dir = dir_combined[li]

        w1_active = latest_w1 > latest_q70 if latest_q70 > 0 else False
        w1_extreme = latest_w1 > latest_q90 if latest_q90 > 0 else False

        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0

        trend_score = 2 if above_ma else -2
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1

        if w1_active and above_ma:
            w1_score = 2
        elif w1_extreme and not above_ma:
            w1_score = -2
        elif w1_active and not above_ma:
            w1_score = -1
        else:
            w1_score = 0

        dir_score = 1 if latest_dir > 0 else (-1 if latest_dir < 0 else 0)

        total = trend_score + mom_score + w1_score + dir_score
        if total >= 3: ev = '机会'
        elif total >= 1: ev = '偏多'
        elif total >= -1: ev = '中性'
        elif total >= -3: ev = '偏空'
        else: ev = '风险'

        signals[name] = {
            'score': total, 'eval': ev, 'above_ma': above_ma,
            'ma_gap': round(ma_gap, 2), 'mom': round(c_mom, 2),
            'w1': round(float(latest_w1), 6),
            'w1_q70': round(float(latest_q70), 6) if latest_q70 == latest_q70 else 0,
            'w1_active': bool(w1_active), 'w1_extreme': bool(w1_extreme),
            'w1_direction': 'up' if latest_dir > 0 else 'down' if latest_dir < 0 else 'flat',
        }
    return signals

# ===================== 整合主函数 =====================
def main():
    returns, close, volume = load_data()
    concept_names = list(returns.columns)
    N = len(concept_names)
    T = len(returns)

    print("=" * 140, file=sys.stderr)
    print("四系统整合概念轮动: Hodge + RMT + SPD + 曼宁资金流 (37概念版)", file=sys.stderr)
    print("=" * 140, file=sys.stderr)
    print(f"数据: {T}交易日 x {N}概念, {returns.index[0].strftime('%Y-%m-%d')}~{returns.index[-1].strftime('%Y-%m-%d')}", file=sys.stderr)

    # 运行四套系统
    print(f"\n[1/3] 运行四套系统...", file=sys.stderr)
    print(f"  Hodge Laplacian...", file=sys.stderr)
    hodge = run_hodge(returns, close, concept_names)
    print(f"  RMT随机矩阵...", file=sys.stderr)
    rmt = run_rmt(returns, close, concept_names)
    print(f"  SPD谱属性分解...", file=sys.stderr)
    spd = run_spd(returns, close, concept_names)
    print(f"  曼宁资金流相变...", file=sys.stderr)
    manning = run_manning(returns, close, volume, concept_names)

    # 整合
    print(f"\n[2/3] 整合四系统信号...", file=sys.stderr)

    eval_map = {'机会': 2, '偏多': 1, '中性': 0, '偏空': -1, '风险': -2}

    integrated = []
    for name in concept_names:
        h = hodge[name]
        r = rmt[name]
        s = spd[name]
        m = manning[name]

        total_score = h['score'] + r['score'] + s['score'] + m['score']

        # 投票: 多少套系统判"机会"
        opp_votes = sum(1 for x in [h, r, s, m] if x['eval'] == '机会')
        bull_votes = sum(1 for x in [h, r, s, m] if x['eval'] in ['机会', '偏多'])
        risk_votes = sum(1 for x in [h, r, s, m] if x['eval'] in ['风险', '偏空'])

        # 综合评价
        if opp_votes >= 3:
            eval_label = '强机会'
        elif opp_votes >= 2:
            eval_label = '机会'
        elif bull_votes >= 3:
            eval_label = '偏多'
        elif bull_votes >= 1 and risk_votes == 0:
            eval_label = '弱偏多'
        elif risk_votes >= 3:
            eval_label = '强风险'
        elif risk_votes >= 2:
            eval_label = '风险'
        else:
            eval_label = '中性'

        integrated.append({
            'name': name,
            'total_score': total_score,
            'eval': eval_label,
            'opp_votes': opp_votes,
            'bull_votes': bull_votes,
            'risk_votes': risk_votes,
            'hodge_score': h['score'], 'hodge_eval': h['eval'],
            'rmt_score': r['score'], 'rmt_eval': r['eval'],
            'spd_score': s['score'], 'spd_eval': s['eval'],
            'manning_score': m['score'], 'manning_eval': m['eval'],
            'above_ma': h['above_ma'],
            'ma_gap': h['ma_gap'],
            'mom': h['mom'],
            # 关键指标
            'delta_s': r.get('delta_s', 0),
            'rmt_mode': r.get('rmt_mode', ''),
            'spectral_angle': s.get('spectral_angle', 0),
            'cmf': m.get('cmf', 0),
            'phase': m.get('phase', 0),
            'persistence': m.get('persistence', 0),
        })

    integrated.sort(key=lambda x: x['total_score'], reverse=True)

    # ===================== 输出 =====================
    print(f"[3/3] 输出整合结果...", file=sys.stderr)

    print("\n" + "=" * 140)
    print(f"四系统整合概念轮动综合评价 (数据截止: {returns.index[-1].strftime('%Y-%m-%d')})")
    print(f"Hodge Laplacian + RMT随机矩阵 + SPD谱属性分解 + 曼宁资金流相变")
    print("=" * 140)

    # 系统级状态汇总
    print(f"\n--- 四系统状态汇总 ---")
    hodge_stability = hodge[concept_names[0]].get('stability', '')
    rmt_mode = rmt[concept_names[0]].get('rmt_mode', '')
    spd_density = spd[concept_names[0]].get('spectral_density', 0)
    delta_s_val = rmt[concept_names[0]].get('delta_s', 0)

    print(f"  Hodge: 结构稳定性={hodge_stability}, 谱角={hodge[concept_names[0]].get('latest_angle', 0):.4f}")
    print(f"  RMT: 模式={rmt_mode}, delta_s={delta_s_val:.4f}{' [极端状态!]' if rmt_mode == 'extreme' else ''}")
    print(f"  SPD: 谱密度={spd_density:.4f} ({'强趋势' if spd_density > 0.6 else '中等' if spd_density > 0.4 else '分散'})")
    # 曼宁系统级
    inflow_count = sum(1 for name in concept_names if manning[name].get('cmf', 0) > 0)
    outflow_count = N - inflow_count
    print(f"  曼宁: CMF净流入{inflow_count}个, 净流出{outflow_count}个")

    # 概念综合信号表
    print(f"\n{'='*140}")
    print("概念板块 四系统综合信号表 (按综合评分降序)")
    print("=" * 140)
    print(f"{'排名':<4} {'概念':<14} {'总分':<5} {'综合':<5} {'机会票':<5} {'多头票':<5} {'空头票':<5} "
          f"{'Hodge':<8} {'RMT':<8} {'SPD':<8} {'曼宁':<8} {'MA40':<4} {'偏离':<6} {'动量':<7} {'dS':<6} {'谱角':<6} {'CMF':<7} {'相变':<7} {'持续'}")
    print("-" * 140)

    markers = {'强机会': '★★', '机会': '★', '偏多': '▲', '弱偏多': '△', '中性': '─', '风险': '▼', '强风险': '✕✕'}

    for i, s in enumerate(integrated):
        m = markers.get(s['eval'], '')
        ma_s = 'Y' if s['above_ma'] else 'N'
        h_eval = f"{s['hodge_score']:+d}{s['hodge_eval']}"
        r_eval = f"{s['rmt_score']:+d}{s['rmt_eval']}"
        sp_eval = f"{s['spd_score']:+d}{s['spd_eval']}"
        mn_eval = f"{s['manning_score']:+d}{s['manning_eval']}"
        print(f"{i+1:<4} {s['name']:<14} {s['total_score']:<+5} {s['eval']:<5}{m:<3} "
              f"{s['opp_votes']:<5} {s['bull_votes']:<5} {s['risk_votes']:<5} "
              f"{h_eval:<8} {r_eval:<8} {sp_eval:<8} {mn_eval:<8} "
              f"{ma_s:<4} {s['ma_gap']:<6.1f} {s['mom']:<7.1f} "
              f"{s['delta_s']:<6.4f} {s['spectral_angle']:<6.4f} {s['cmf']:<7.4f} {s['phase']:<7.4f} {s['persistence']:.2f}")

    # 强弱分布
    print(f"\n{'='*140}")
    print("概念板块综合强弱分布:")
    for label in ['强机会', '机会', '偏多', '弱偏多', '中性', '风险', '强风险']:
        names = [s['name'] for s in integrated if s['eval'] == label]
        if names:
            print(f"  {label} ({len(names)}个): {', '.join(names)}")

    # 液冷专题
    yeleng = [s for s in integrated if s['name'] == '液冷概念']
    if yeleng:
        s = yeleng[0]
        print(f"\n{'='*140}")
        print(f"液冷概念 四系统整合专题:")
        print(f"-" * 140)
        print(f"  综合评价: {s['eval']} (总分{s['total_score']:+d})")
        print(f"  投票: 机会{s['opp_votes']}票, 多头{s['bull_votes']}票, 空头{s['risk_votes']}票")
        print(f"  MA40: {'线上' if s['above_ma'] else '线下'} (偏离{s['ma_gap']:.2f}%)")
        print(f"  20日动量: {s['mom']:+.2f}%")
        print(f"  Hodge: 评分{s['hodge_score']:+d} ({s['hodge_eval']})")
        print(f"  RMT:   评分{s['rmt_score']:+d} ({s['rmt_eval']}) [delta_s={s['delta_s']:.4f}, {s['rmt_mode']}]")
        print(f"  SPD:   评分{s['spd_score']:+d} ({s['spd_eval']}) [谱角={s['spectral_angle']:.4f}]")
        print(f"  曼宁:   评分{s['manning_score']:+d} ({s['manning_eval']}) [CMF={s['cmf']:+.4f}, 相变={s['phase']:+.4f}, 持续={s['persistence']:.2f}]")

    # 机会与风险总结
    print(f"\n{'='*140}")
    print("四系统综合机会与风险总结:")
    print("-" * 140)

    opps = [s for s in integrated if s['eval'] in ['强机会', '机会', '偏多', '弱偏多']]
    risks = [s for s in integrated if s['eval'] in ['风险', '强风险']]
    neutrals = [s for s in integrated if s['eval'] == '中性']

    print(f"\n  机会区({len(opps)}个):")
    for s in opps:
        reasons = []
        if s['above_ma']: reasons.append(f"MA40线上({s['ma_gap']:.1f}%)")
        if s['mom'] > 0: reasons.append(f"动量+{s['mom']:.1f}%")
        if s['opp_votes'] > 0: reasons.append(f"{s['opp_votes']}套系统判机会")
        if s['cmf'] > 0: reasons.append(f"CMF净流入({s['cmf']:+.4f})")
        if s['phase'] > 0: reasons.append(f"资金流入相变({s['phase']:+.4f})")
        if s['spectral_angle'] < 0.5: reasons.append(f"谱角市场对齐({s['spectral_angle']:.4f})")
        votes = f"H:{s['hodge_eval']} R:{s['rmt_eval']} S:{s['spd_eval']} M:{s['manning_eval']}"
        print(f"    {s['name']:<14} 总分{s['total_score']:+d}  {s['eval']:<5}  {' | '.join(reasons)}")
        print(f"    {'':>16} 投票: {votes}")

    print(f"\n  风险区({len(risks)}个):")
    for s in risks:
        reasons = []
        if not s['above_ma']: reasons.append(f"MA40线下({s['ma_gap']:.1f}%)")
        if s['mom'] < 0: reasons.append(f"动量{s['mom']:.1f}%")
        if s['risk_votes'] > 0: reasons.append(f"{s['risk_votes']}套系统判风险")
        if s['cmf'] < 0: reasons.append(f"CMF净流出({s['cmf']:+.4f})")
        if s['phase'] < 0: reasons.append(f"资金流出相变({s['phase']:+.4f})")
        votes = f"H:{s['hodge_eval']} R:{s['rmt_eval']} S:{s['spd_eval']} M:{s['manning_eval']}"
        print(f"    {s['name']:<14} 总分{s['total_score']:+d}  {s['eval']:<5}  {' | '.join(reasons)}")
        print(f"    {'':>16} 投票: {votes}")

    print(f"\n  中性区({len(neutrals)}个): {', '.join(s['name'] for s in neutrals)}")

    # 系统一致性分析
    print(f"\n{'='*140}")
    print("四系统一致性分析:")
    print("-" * 140)
    consistent_bull = 0
    consistent_bear = 0
    split_signals = 0
    for s in integrated:
        evals_set = {s['hodge_eval'], s['rmt_eval'], s['spd_eval'], s['manning_eval']}
        bull_set = {'机会', '偏多'}
        bear_set = {'风险', '偏空'}
        if evals_set <= bull_set:
            consistent_bull += 1
        elif evals_set <= bear_set:
            consistent_bear += 1
        else:
            split_signals += 1

    print(f"  四系统一致看多: {consistent_bull}个概念")
    print(f"  四系统一致看空: {consistent_bear}个概念")
    print(f"  四系统信号分歧: {split_signals}个概念")

    # 逐系统液冷评价
    print(f"\n{'='*140}")
    print("液冷概念逐系统验证:")
    print("-" * 140)
    yl = yeleng[0] if yeleng else None
    if yl:
        systems = [
            ('Hodge', yl['hodge_score'], yl['hodge_eval']),
            ('RMT', yl['rmt_score'], yl['rmt_eval']),
            ('SPD', yl['spd_score'], yl['spd_eval']),
            ('曼宁', yl['manning_score'], yl['manning_eval']),
        ]
        opp_count = 0
        for sys_name, score, ev in systems:
            marker = '★机会' if ev == '机会' else ''
            print(f"  {sys_name:<8} 评分{score:+d}  评价={ev:<5} {marker}")
            if ev == '机会':
                opp_count += 1
        print(f"\n  结论: 液冷概念被 {opp_count}/4 套系统识别为机会")
        if opp_count >= 1:
            print(f"  *** 验收通过: 液冷概念被至少1套系统标记为机会 ***")
        else:
            print(f"  *** 验收未通过: 液冷概念未被任何系统标记为机会 ***")

    print(f"\n{'='*140}")
    print("评分规则:")
    print("  Hodge: 趋势(±2) + 动量(±1~±2) + Fiedler(±1) + 结构(±1)  => 范围[-7,+7]")
    print("  RMT:   趋势(±2) + 动量(±1~±2) + Top5(+2/+1) + Bottom5(-2) + delta_s(±1) + 相关(±1)  => 范围[-9,+9]")
    print("  SPD:   趋势(±2) + 动量(±1~±2) + 谱角(+2~−1) + 载荷(±1) + 谱密度(±1)  => 范围[-7,+7]")
    print("  曼宁:   趋势(±2) + 动量(±1~±2) + CMF(±1~±2) + 相变(±1~±2) + 持续性(±1) + 资金流动量(±1)  => 范围[-9,+9]")
    print("  综合: 四系统评分之和  => 范围[-32,+32]")
    print("  投票: 机会票=系统判'机会'数, 多头票=系统判'机会/偏多'数, 空头票=系统判'风险/偏空'数")
    print("  评价: 强机会(≥3票机会) | 机会(≥2票) | 偏多(≥3票多头) | 弱偏多(≥1多头且0空头) | 中性 | 风险(≥2空头) | 强风险(≥3空头)")
    print("=" * 140)

if __name__ == '__main__':
    main()
