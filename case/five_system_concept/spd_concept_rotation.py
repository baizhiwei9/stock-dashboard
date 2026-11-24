#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPD (Spectral Property Decomposition) 概念轮动系统

方法核心:
  收益率 -> 相关矩阵 -> 特征分解 -> 谱属性分析:
    1. 因子显著性: 特征值占比识别主导因子
    2. 参与率(Participation Ratio): 每个因子涉及的概念数
    3. 因子载荷(Factor Loading): 概念在显著因子上的暴露
    4. 谱角(Spectral Angle): 概念向量与市场因子的夹角
    5. 谱密度(Spectral Density): 显著因子能量集中度
  -> 结合MA40趋势过滤 -> 输出机会/风险信号
"""
import pandas as pd
import numpy as np
from numpy.linalg import eigh
import sys

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

MA_PERIOD = 40
MOM_PERIOD = 20
FACTOR_THRESHOLD = 0.10  # 因子显著性阈值: 方差占比>10%为主因子
SPECTRAL_ANGLE_THRESHOLD = 0.5  # 谱角阈值(rad): >此值为偏离市场

def load_data():
    returns = pd.read_pickle(f'{OUT_DIR}/concept_returns.pkl')
    close = pd.read_pickle(f'{OUT_DIR}/concept_close.pkl')
    return returns, close

def spectral_decomposition(corr_matrix):
    """特征分解, 返回(特征值降序, 特征向量列对应)"""
    C = (corr_matrix + corr_matrix.T) / 2
    evals, evecs = eigh(C)
    idx = np.argsort(evals)[::-1]
    return evals[idx], evecs[:, idx]

def participation_ratio(eigenvector):
    """参与率: 衡量因子涉及的概念数 (IPR的倒数)
    PR = 1 / sum(v_i^4), PR越大表示因子越分散(系统性), PR越小表示越集中
    """
    v2 = eigenvector ** 2
    ipr = np.sum(v2 ** 2)  # Inverse Participation Ratio
    pr = 1.0 / ipr if ipr > 0 else 0
    return float(pr)

def compute_factor_loadings(eigenvalues, eigenvectors, n_concepts):
    """计算每个概念在各显著因子上的载荷"""
    # 方差占比
    var_pct = eigenvalues / eigenvalues.sum()
    # 显著因子: 方差占比 > 阈值
    significant = var_pct > FACTOR_THRESHOLD

    loadings = {}
    for i in range(n_concepts):
        loadings[i] = {
            'factor_0': float(eigenvectors[i, 0]),  # 市场因子
            'factor_0_sq': float(eigenvectors[i, 0] ** 2),
        }
        if significant[1]:
            loadings[i]['factor_1'] = float(eigenvectors[i, 1])
            loadings[i]['factor_1_sq'] = float(eigenvectors[i, 1] ** 2)
        else:
            loadings[i]['factor_1'] = 0.0
            loadings[i]['factor_1_sq'] = 0.0

    return loadings, var_pct, significant

def spectral_angle_to_market(concept_loading, market_factor):
    """计算概念向量与市场因子的谱角
    theta = arccos( <v_concept, v_market> / (||v_concept|| * ||v_market||) )
    """
    dot = abs(np.dot(concept_loading, market_factor))
    norm_c = np.linalg.norm(concept_loading)
    norm_m = np.linalg.norm(market_factor)
    if norm_c == 0 or norm_m == 0:
        return 0.0
    cos_a = np.clip(dot / (norm_c * norm_m), -1, 1)
    return float(np.arccos(cos_a))

def compute_spectral_density(eigenvalues, n_significant):
    """谱密度: 显著因子能量集中度"""
    if len(eigenvalues) == 0:
        return 0.0
    total = eigenvalues.sum()
    if total == 0:
        return 0.0
    sig_energy = eigenvalues[:n_significant].sum()
    return float(sig_energy / total)

def compute_ma(close, period=40):
    return close.rolling(window=period, min_periods=1).mean()

def compute_momentum(close, period=20):
    return close.pct_change(periods=period) * 100

def main():
    print("=" * 110, file=sys.stderr)
    print("SPD (Spectral Property Decomposition) 概念轮动系统 (37概念版)", file=sys.stderr)
    print("=" * 110, file=sys.stderr)

    returns, close = load_data()
    concept_names = list(returns.columns)
    N = len(concept_names)
    T = len(returns)

    print(f"数据: {T}交易日 x {N}概念, {returns.index[0].strftime('%Y-%m-%d')}~{returns.index[-1].strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"参数: MA{MA_PERIOD}, 动量{MOM_PERIOD}日, 因子阈值{FACTOR_THRESHOLD*100}%, 谱角阈值{SPECTRAL_ANGLE_THRESHOLD}", file=sys.stderr)

    # ===================== Step 1: 谱分解 =====================
    print(f"\n[1/4] 谱分解...", file=sys.stderr)
    corr = returns.corr().fillna(0).values
    eigenvalues, eigenvectors = spectral_decomposition(corr)

    var_pct = eigenvalues / eigenvalues.sum()
    significant_factors = var_pct > FACTOR_THRESHOLD
    n_sig = significant_factors.sum()

    print(f"  最大特征值: {eigenvalues[0]:.4f} ({var_pct[0]*100:.1f}%方差)", file=sys.stderr)
    print(f"  显著因子数(>10%方差): {n_sig}", file=sys.stderr)
    for i in range(min(5, N)):
        pr = participation_ratio(eigenvectors[:, i])
        sig = 'Y' if significant_factors[i] else 'N'
        print(f"  因子{i}: eval={eigenvalues[i]:.4f}, 方差={var_pct[i]*100:.1f}%, 参与率={pr:.1f}/{N}, 显著={sig}", file=sys.stderr)

    spectral_density = compute_spectral_density(eigenvalues, max(1, n_sig))

    # 市场因子(第一主因子)
    market_factor = eigenvectors[:, 0]
    market_pr = participation_ratio(market_factor)

    print(f"  谱密度: {spectral_density:.4f}", file=sys.stderr)
    print(f"  市场因子参与率: {market_pr:.1f}/{N} ({'系统性主导' if market_pr > N*0.5 else '集中驱动' if market_pr < N*0.2 else '中等分散'})", file=sys.stderr)

    # ===================== Step 2: 因子载荷+谱角 =====================
    print(f"\n[2/4] 因子载荷+谱角分析...", file=sys.stderr)
    loadings, _, _ = compute_factor_loadings(eigenvalues, eigenvectors, N)

    concept_spectral = {}
    k_factors = max(2, n_sig + 1)
    for i, name in enumerate(concept_names):
        # 谱角: 概念在因子空间中的载荷向量与纯市场方向的夹角
        # concept_vec = [loading_0, loading_1, ...] (概念i在各因子上的载荷)
        # market_dir = [1, 0, 0, ...] (纯市场方向)
        concept_vec = eigenvectors[i, :k_factors]
        # 谱角 = arccos(|v_0| / ||v||), 即市场因子载荷占总载荷的比例
        norm_v = np.linalg.norm(concept_vec)
        if norm_v > 0:
            cos_a = np.clip(abs(concept_vec[0]) / norm_v, -1, 1)
            angle = float(np.arccos(cos_a))
        else:
            angle = 0.0

        # 市场因子载荷
        m_loading = loadings[i]['factor_0_sq']
        # 第二因子载荷
        s_loading = loadings[i].get('factor_1_sq', 0)

        concept_spectral[name] = {
            'market_loading': m_loading,
            'second_loading': s_loading,
            'spectral_angle': angle,
            'is_market_driven': angle < SPECTRAL_ANGLE_THRESHOLD,
            'is_idiosyncratic': angle > SPECTRAL_ANGLE_THRESHOLD,
        }

    # ===================== Step 3: 趋势+动量+综合信号 =====================
    print(f"[3/4] 生成综合信号...", file=sys.stderr)
    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]

    # 滚动谱密度历史(检测相变)
    spd_hist = []
    window = min(30, T)
    for t in range(window, T + 1):
        rw = returns.iloc[t - window:t]
        cw = rw.corr().fillna(0).values
        ew, _ = spectral_decomposition(cw)
        vw = ew / ew.sum()
        sw = compute_spectral_density(ew, max(1, (vw > FACTOR_THRESHOLD).sum()))
        spd_hist.append(sw)

    spd_latest = spd_hist[-1] if spd_hist else spectral_density
    spd_avg5 = np.mean(spd_hist[-5:]) if len(spd_hist) >= 5 else spd_latest
    spd_trend = 'rising' if len(spd_hist) >= 3 and spd_hist[-1] > spd_hist[-3] else 'falling'

    concept_signals = []
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0

        cs = concept_spectral[name]
        ml = cs['market_loading']
        sl = cs['second_loading']
        angle = cs['spectral_angle']
        market_driven = cs['is_market_driven']

        # 信号评分
        # 趋势分
        trend_score = 2 if above_ma else -2
        # 动量分
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        # 谱角分: 与市场对齐加分(趋势同步), 偏离减分(可能逆势)
        if angle < 0.3: angle_score = 2  # 高度对齐市场
        elif angle < SPECTRAL_ANGLE_THRESHOLD: angle_score = 1
        elif angle < 0.8: angle_score = 0
        else: angle_score = -1  # 严重偏离
        # 市场因子载荷分: 高载荷=跟随市场趋势, 低载荷=独立行情
        if ml > 0.15: loading_score = 1  # 强市场暴露
        elif ml > 0.05: loading_score = 0
        else: loading_score = -1  # 低市场暴露
        # 谱密度分: 高密度=强趋势, 低密度=分散
        if spd_latest > 0.6: density_score = 1
        elif spd_latest > 0.4: density_score = 0
        else: density_score = -1

        total_score = trend_score + mom_score + angle_score + loading_score + density_score

        if total_score >= 4: eval_label = '机会'
        elif total_score >= 2: eval_label = '偏多'
        elif total_score >= 0: eval_label = '中性'
        elif total_score >= -2: eval_label = '偏空'
        else: eval_label = '风险'

        concept_signals.append({
            'name': name, 'total_score': total_score, 'eval': eval_label,
            'close': round(c_close, 3), 'ma40': round(c_ma, 3),
            'above_ma40': above_ma, 'ma40_gap': round(ma_gap, 2),
            'mom20': round(c_mom, 2),
            'market_loading': round(ml, 4), 'second_loading': round(sl, 4),
            'spectral_angle': round(angle, 4),
            'market_driven': market_driven,
            'trend_score': trend_score, 'mom_score': mom_score,
            'angle_score': angle_score, 'loading_score': loading_score,
            'density_score': density_score,
        })

    concept_signals.sort(key=lambda x: x['total_score'], reverse=True)

    # ===================== Step 4: 输出 =====================
    print(f"[4/4] 输出结果...", file=sys.stderr)

    print("\n" + "=" * 130)
    print(f"SPD谱属性分解概念轮动分析 (数据截止: {returns.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 130)

    print(f"\n--- SPD系统级状态 ---")
    print(f"  最大特征值: {eigenvalues[0]:.4f} ({var_pct[0]*100:.1f}%方差)")
    print(f"  显著因子数(>10%方差): {n_sig}")
    print(f"  谱密度: {spd_latest:.4f} (5日均值: {spd_avg5:.4f}, 趋势: {spd_trend})")
    print(f"  市场因子参与率: {market_pr:.1f}/{N} ({'系统性主导' if market_pr > N*0.5 else '集中驱动' if market_pr < N*0.2 else '中等分散'})")
    print(f"  评价: {'强趋势主导' if spd_latest > 0.6 else '中等分散' if spd_latest > 0.4 else '高度分散无主线'}")

    # 因子分布
    print(f"\n{'='*130}")
    print("因子分布 (前5个):")
    print("-" * 130)
    print(f"  {'因子':<6} {'特征值':<10} {'方差占比':<8} {'参与率':<8} {'类型':<10} {'柱状图'}")
    for i in range(min(5, N)):
        pr = participation_ratio(eigenvectors[:, i])
        ftype = '市场因子' if i == 0 else ('行业因子' if significant_factors[i] else '噪声')
        bar_len = min(40, int(var_pct[i] * 40))
        bar = '█' * bar_len + '░' * (40 - bar_len)
        print(f"  {i:<6} {eigenvalues[i]:<10.4f} {var_pct[i]*100:<8.1f}% {pr:<8.1f} {ftype:<10} {bar}")

    # 概念信号表
    print(f"\n{'='*130}")
    print("概念板块 SPD 信号表 (按综合评分降序)")
    print("=" * 130)
    print(f"{'排名':<4} {'概念':<14} {'评分':<5} {'评价':<5} {'MA40':<4} {'偏离%':<7} {'20日动量':<8} {'市场载荷':<7} {'次因子':<7} {'谱角':<6} {'趋':<3} {'动':<3} {'角':<3} {'载':<3} {'密'}")
    print("-" * 130)

    markers = {'机会': '★', '偏多': '▲', '中性': '─', '偏空': '▼', '风险': '✕'}

    for i, s in enumerate(concept_signals):
        m = markers.get(s['eval'], '')
        ma_s = 'Y' if s['above_ma40'] else 'N'
        print(f"{i+1:<4} {s['name']:<14} {s['total_score']:<+5} {s['eval']:<5}{m} "
              f"{ma_s:<4} {s['ma40_gap']:<7.2f} {s['mom20']:<8.2f} "
              f"{s['market_loading']:<7.4f} {s['second_loading']:<7.4f} {s['spectral_angle']:<6.4f} "
              f"{s['trend_score']:<+3} {s['mom_score']:<+3} {s['angle_score']:<+3} {s['loading_score']:<+3} {s['density_score']:<+3}")

    # 强弱分布
    print(f"\n{'='*130}")
    print("概念板块强弱分布:")
    for label in ['机会', '偏多', '中性', '偏空', '风险']:
        names = [s['name'] for s in concept_signals if s['eval'] == label]
        if names:
            print(f"  {label} ({len(names)}个): {', '.join(names)}")

    # 市场对齐分析
    print(f"\n{'='*130}")
    print("谱角分析 (市场对齐 vs 独立行情):")
    print("-" * 130)
    aligned = [s for s in concept_signals if s['market_driven']]
    independent = [s for s in concept_signals if not s['market_driven']]
    print(f"  市场对齐({len(aligned)}个):")
    for s in aligned[:10]:
        print(f"    {s['name']:<14} 谱角={s['spectral_angle']:.4f}  动量={s['mom20']:+.2f}%  评分={s['total_score']:+d}")
    print(f"  独立行情({len(independent)}个):")
    for s in independent[:10]:
        print(f"    {s['name']:<14} 谱角={s['spectral_angle']:.4f}  动量={s['mom20']:+.2f}%  评分={s['total_score']:+d}")

    # 液冷专题
    yeleng = [s for s in concept_signals if s['name'] == '液冷概念']
    if yeleng:
        s = yeleng[0]
        print(f"\n{'='*130}")
        print(f"液冷概念 SPD 专题:")
        print(f"-" * 130)
        print(f"  评价: {s['eval']} (评分{s['total_score']:+d})")
        print(f"  MA40: {'线上' if s['above_ma40'] else '线下'} (偏离{s['ma40_gap']:.2f}%)")
        print(f"  20日动量: {s['mom20']:+.2f}%")
        print(f"  市场因子载荷: {s['market_loading']:.4f} ({'强暴露' if s['market_loading'] > 0.15 else '中等' if s['market_loading'] > 0.05 else '低暴露'})")
        print(f"  谱角: {s['spectral_angle']:.4f} ({'市场对齐' if s['market_driven'] else '独立行情'})")

    # 机会与风险总结
    print(f"\n{'='*130}")
    print("SPD机会与风险总结:")
    print("-" * 130)
    opps = [s for s in concept_signals if s['eval'] in ['机会', '偏多']]
    risks = [s for s in concept_signals if s['eval'] in ['风险', '偏空']]
    neutrals = [s for s in concept_signals if s['eval'] == '中性']

    print(f"\n  谱密度: {spd_latest:.4f} ({'强趋势' if spd_latest > 0.6 else '中等' if spd_latest > 0.4 else '分散'})")
    print(f"\n  机会区({len(opps)}个):")
    for s in opps:
        reasons = []
        if s['above_ma40']: reasons.append(f"MA40线上({s['ma40_gap']:.1f}%)")
        if s['mom20'] > 0: reasons.append(f"动量+{s['mom20']:.1f}%")
        if s['market_driven']: reasons.append("市场对齐")
        if s['market_loading'] > 0.15: reasons.append("强市场暴露")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")

    print(f"\n  风险区({len(risks)}个):")
    for s in risks:
        reasons = []
        if not s['above_ma40']: reasons.append(f"MA40线下({s['ma40_gap']:.1f}%)")
        if s['mom20'] < 0: reasons.append(f"动量{s['mom20']:.1f}%")
        if not s['market_driven']: reasons.append("独立行情(偏离市场)")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")

    print(f"\n  中性区({len(neutrals)}个): {', '.join(s['name'] for s in neutrals)}")

    print(f"\n{'='*130}")
    print("评分: 趋势(MA40±2) + 动量(±1~±2) + 谱角(对齐+2/偏离-1) + 因子载荷(强+1/弱-1) + 谱密度(强+1/弱-1)")
    print("评价: 机会(≥4) | 偏多(2~3) | 中性(0~1) | 偏空(-1~-2) | 风险(≤-3)")
    print("=" * 130)

if __name__ == '__main__':
    main()
