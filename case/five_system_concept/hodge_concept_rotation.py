#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hodge Laplacian 概念轮动系统
原系统逻辑(行业ETF版 +1778%回测):
  收益率→相关矩阵→图结构→Laplacian谱分解→Fiedler向量识别领涨/领跌→谱角距离检测变点→MA40趋势过滤

改造版: 将20个概念板块当作行业, 套用同一套分析框架
输出: 每个概念的 Hodge信号 + MA40趋势 + 结构稳定性 + 综合评价(机会/中性/风险)
"""
import pandas as pd
import numpy as np
from numpy.linalg import eigh
import pickle
import sys

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

# ===================== 参数 =====================
CORR_WINDOW = 30       # 滚动相关矩阵窗口
CORR_THRESHOLD = 0.3   # 构图阈值: |corr| > 此值则连边
MA_PERIOD = 40         # 趋势均线周期
MOM_PERIOD = 20        # 动量周期
TOP_K_EIGEN = 5       # 谱角距离使用前K个特征向量
SPECTRAL_ANGLE_THRESHOLD = 0.3  # 谱角变化阈值(弧度), 超过此值判定为结构变点

def load_data():
    """加载概念收益率和收盘指数"""
    returns = pd.read_pickle(f'{OUT_DIR}/concept_returns.pkl')
    close = pd.read_pickle(f'{OUT_DIR}/concept_close.pkl')
    return returns, close

def build_correlation_graph(returns_window):
    """
    从收益率窗口构建相关矩阵和邻接矩阵
    returns_window: DataFrame, shape=(window, n_concepts)
    """
    corr = returns_window.corr().fillna(0).values
    n = corr.shape[0]
    # 邻接矩阵: |corr| > threshold 则连边
    adj = (np.abs(corr) > CORR_THRESHOLD).astype(float)
    np.fill_diagonal(adj, 0)
    # 加权邻接: 用相关系数作为权重
    adj_weighted = corr * adj
    return corr, adj, adj_weighted

def compute_laplacian(adj_weighted):
    """
    计算加权图Laplacian矩阵
    L = D - A (标准组合Laplacian, 即0阶Hodge Laplacian)
    """
    D = np.diag(adj_weighted.sum(axis=1))
    L = D - adj_weighted
    return L

def spectral_decomposition(L):
    """
    Laplacian谱分解
    返回: 特征值(升序), 特征向量(列对应特征值)
    """
    eigenvalues, eigenvectors = eigh(L)
    # 排序(已经是升序, eigh保证)
    idx = np.argsort(eigenvalues)
    eigenvalues = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]
    return eigenvalues, eigenvectors

def fiedler_vector_analysis(eigenvalues, eigenvectors, concept_names):
    """
    Fiedler向量分析: 第二小特征值对应的特征向量
    用于识别"领涨社区"和"领跌社区"
    正值=领涨半区, 负值=领跌半区
    """
    if len(eigenvalues) < 2:
        return np.array([]), {}
    
    fiedler_val = eigenvalues[1]  # 第二小特征值(代数连通度)
    fiedler_vec = eigenvectors[:, 1]  # Fiedler向量
    
    # 分析每个概念在Fiedler向量中的位置
    analysis = {}
    for i, name in enumerate(concept_names):
        analysis[name] = {
            'fiedler_component': float(fiedler_vec[i]),
            'community': 'leading' if fiedler_vec[i] >= 0 else 'lagging',
            'magnitude': float(abs(fiedler_vec[i])),
        }
    
    return fiedler_vec, analysis

def spectral_angle_distance(eigvecs_current, eigvecs_previous):
    """
    计算谱角距离: 衡量特征子空间的变化
    θ = arccos( |<V_curr, V_prev>| / (||V_curr|| * ||V_prev||) )
    使用子空间投影矩阵的Frobenius内积
    """
    # 取前K个特征向量(排除第0个, 因为它是常数向量对应λ=0)
    k = min(TOP_K_EIGEN, eigvecs_current.shape[1] - 1, eigvecs_previous.shape[1] - 1)
    if k < 1:
        return 0.0
    
    V_curr = eigvecs_current[:, 1:k+1]  # 排除第0个
    V_prev = eigvecs_previous[:, 1:k+1]
    
    # 子空间投影矩阵
    P_curr = V_curr @ V_curr.T
    P_prev = V_prev @ V_prev.T
    
    # 谱角距离 = arccos(tr(P_curr @ P_prev) / k) (归一化)
    # 使用矩阵的Frobenius内积
    overlap = np.trace(P_curr @ P_prev)
    denom = k  # 归一化因子(投影矩阵的迹)
    if denom == 0:
        return 0.0
    
    cos_angle = np.clip(overlap / denom, -1, 1)
    angle = np.arccos(cos_angle)
    return float(angle)

def compute_ma(close, period=40):
    """计算移动平均"""
    return close.rolling(window=period, min_periods=period).mean()

def compute_momentum(close, period=20):
    """计算动量"""
    return close.pct_change(periods=period) * 100

def main():
    print("=" * 110, file=sys.stderr)
    print("Hodge Laplacian 概念轮动系统 (20概念版)", file=sys.stderr)
    print("=" * 110, file=sys.stderr)

    # 加载数据
    returns, close = load_data()
    concept_names = list(returns.columns)
    n_concepts = len(concept_names)
    n_days = len(returns)
    
    print(f"数据: {n_days}个交易日 x {n_concepts}个概念", file=sys.stderr)
    print(f"日期范围: {returns.index[0].strftime('%Y-%m-%d')} ~ {returns.index[-1].strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"参数: 相关窗口={CORR_WINDOW}日, 构图阈值={CORR_THRESHOLD}, MA{MA_PERIOD}, 动量{MOM_PERIOD}日", file=sys.stderr)

    # ===================== Step 1: 滚动Laplacian谱分析 =====================
    print(f"\n[1/4] 滚动Laplacian谱分析 ({n_days - CORR_WINDOW + 1}个窗口)...", file=sys.stderr)
    
    spectral_angles = []
    fiedler_history = []
    eigenvalue_history = []
    
    for t in range(CORR_WINDOW, n_days + 1):
        window = returns.iloc[t - CORR_WINDOW:t]
        corr, adj, adj_w = build_correlation_graph(window)
        L = compute_laplacian(adj_w)
        evals, evecs = spectral_decomposition(L)
        
        eigenvalue_history.append(evals)
        
        # Fiedler分析
        fvec, fanalysis = fiedler_vector_analysis(evals, evecs, concept_names)
        fiedler_history.append({
            'date': returns.index[t - 1],
            'fiedler_value': float(evals[1]) if len(evals) > 1 else 0,
            'fiedler_vec': fvec,
            'analysis': fanalysis,
        })
        
        # 谱角距离
        if t > CORR_WINDOW:  # 需要前一个窗口
            prev_window = returns.iloc[t - CORR_WINDOW - 1: t - 1]
            _, _, prev_adj_w = build_correlation_graph(prev_window)
            prev_L = compute_laplacian(prev_adj_w)
            _, prev_evecs = spectral_decomposition(prev_L)
            angle = spectral_angle_distance(evecs, prev_evecs)
            spectral_angles.append({
                'date': returns.index[t - 1],
                'angle': angle,
            })
    
    print(f"  谱角距离序列: {len(spectral_angles)}个", file=sys.stderr)
    print(f"  Fiedler历史: {len(fiedler_history)}个", file=sys.stderr)

    # ===================== Step 2: MA40趋势 + 动量 =====================
    print(f"[2/4] 计算MA{MA_PERIOD}趋势和{MOM_PERIOD}日动量...", file=sys.stderr)
    
    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]
    
    # ===================== Step 3: 综合信号 =====================
    print("[3/4] 生成综合信号...", file=sys.stderr)
    
    # 当前Fiedler分析
    current_fiedler = fiedler_history[-1] if fiedler_history else None
    current_fiedler_analysis = current_fiedler['analysis'] if current_fiedler else {}
    
    # 当前谱角距离
    latest_angle = spectral_angles[-1]['angle'] if spectral_angles else 0
    avg_angle = np.mean([s['angle'] for s in spectral_angles[-5:]]) if len(spectral_angles) >= 5 else latest_angle
    angle_spike = latest_angle > SPECTRAL_ANGLE_THRESHOLD
    
    # 谱角距离趋势
    if len(spectral_angles) >= 3:
        recent_angles = [s['angle'] for s in spectral_angles[-3:]]
        angle_trend = 'rising' if recent_angles[-1] > recent_angles[0] else 'falling'
    else:
        angle_trend = 'stable'
    
    # 代数连通度
    algebraic_connectivity = current_fiedler['fiedler_value'] if current_fiedler else 0
    
    # 结构稳定性评价
    if avg_angle < 0.15:
        structure_stability = 'stable'
    elif avg_angle < SPECTRAL_ANGLE_THRESHOLD:
        structure_stability = 'shifting'
    else:
        structure_stability = 'unstable'
    
    # 逐概念信号
    concept_signals = []
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0
        
        fiedler_info = current_fiedler_analysis.get(name, {})
        fiedler_comp = fiedler_info.get('fiedler_component', 0)
        community = fiedler_info.get('community', 'unknown')
        
        # 信号评分
        # 趋势分: 线上+2, 线下-2
        trend_score = 2 if above_ma else -2
        # 动量分: >5% +2, >0% +1, <-5% -2, <0% -1
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        # Fiedler分: 领涨+1, 领跌-1
        fiedler_score = 1 if community == 'leading' else -1
        # 结构分: 稳定+1, 变化0, 不稳定-1
        if structure_stability == 'stable': struct_score = 1
        elif structure_stability == 'shifting': struct_score = 0
        else: struct_score = -1
        
        total_score = trend_score + mom_score + fiedler_score + struct_score
        
        # 综合评价
        if total_score >= 3:
            eval_label = '机会'
        elif total_score >= 1:
            eval_label = '偏多'
        elif total_score >= -1:
            eval_label = '中性'
        elif total_score >= -3:
            eval_label = '偏空'
        else:
            eval_label = '风险'
        
        concept_signals.append({
            'name': name,
            'close': round(c_close, 3),
            'ma40': round(c_ma, 3),
            'above_ma40': above_ma,
            'ma40_gap': round(ma_gap, 2),
            'mom20': round(c_mom, 2),
            'fiedler': round(fiedler_comp, 4),
            'community': community,
            'trend_score': trend_score,
            'mom_score': mom_score,
            'fiedler_score': fiedler_score,
            'struct_score': struct_score,
            'total_score': total_score,
            'eval': eval_label,
        })
    
    # 按总分排序
    concept_signals.sort(key=lambda x: x['total_score'], reverse=True)

    # ===================== Step 4: 输出 =====================
    print("[4/4] 输出结果...", file=sys.stderr)
    
    print("\n" + "=" * 130)
    print(f"Hodge Laplacian 概念轮动分析 (数据截止: {returns.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 130)
    
    # 系统级状态
    print(f"\n--- 系统级状态 ---")
    print(f"  代数连通度(λ1): {algebraic_connectivity:.4f}  ({'高连通' if algebraic_connectivity > 0.5 else '中等' if algebraic_connectivity > 0.2 else '碎片化'})")
    print(f"  谱角距离(最新): {latest_angle:.4f} rad")
    print(f"  谱角距离(5日均值): {avg_angle:.4f} rad")
    print(f"  结构稳定性: {structure_stability}{' [变点预警!]' if angle_spike else ''}")
    print(f"  谱角趋势: {angle_trend}")
    
    # 概念信号表
    print(f"\n{'='*130}")
    print("概念板块 Hodge 信号表 (按综合评分降序)")
    print("=" * 130)
    print(f"{'排名':<5} {'概念':<14} {'评分':<5} {'评价':<5} {'MA40':<5} {'偏离%':<7} {'20日动量%':<9} {'Fiedler':<9} {'社区':<6} {'趋势':<4} {'动量':<4} {'Fied':<4} {'结构'}")
    print("-" * 130)
    
    eval_markers = {'机会': '★', '偏多': '▲', '中性': '─', '偏空': '▼', '风险': '✕'}
    
    for i, s in enumerate(concept_signals):
        marker = eval_markers.get(s['eval'], '')
        ma_s = 'Y' if s['above_ma40'] else 'N'
        print(f"{i+1:<5} {s['name']:<14} {s['total_score']:<+5} {s['eval']:<5}{marker} "
              f"{ma_s:<5} {s['ma40_gap']:<7.2f} {s['mom20']:<9.2f} "
              f"{s['fiedler']:<9.4f} {s['community']:<6} "
              f"{s['trend_score']:<+4} {s['mom_score']:<+4} {s['fiedler_score']:<+4} {s['struct_score']:<+4}")

    # 强弱分布
    print(f"\n{'='*130}")
    print("概念板块强弱分布:")
    for label in ['机会', '偏多', '中性', '偏空', '风险']:
        names = [s['name'] for s in concept_signals if s['eval'] == label]
        if names:
            print(f"  {label} ({len(names)}个): {', '.join(names)}")

    # Fiedler社区分析
    print(f"\n{'='*130}")
    print("Fiedler向量社区分析 (领涨/领跌半区):")
    print("-" * 130)
    
    fiedler_sorted = sorted(concept_signals, key=lambda x: x['fiedler'], reverse=True)
    print(f"  领涨半区(Fiedler>0):")
    for s in fiedler_sorted:
        if s['fiedler'] >= 0:
            print(f"    {s['name']:<14} Fiedler={s['fiedler']:+.4f}  动量={s['mom20']:+.2f}%  {'线上' if s['above_ma40'] else '线下'}")
    print(f"  领跌半区(Fiedler<0):")
    for s in fiedler_sorted:
        if s['fiedler'] < 0:
            print(f"    {s['name']:<14} Fiedler={s['fiedler']:+.4f}  动量={s['mom20']:+.2f}%  {'线上' if s['above_ma40'] else '线下'}")

    # 谱角距离历史
    if len(spectral_angles) >= 10:
        print(f"\n{'='*130}")
        print("谱角距离历史 (最近10个窗口, 检测结构变点):")
        print("-" * 130)
        for s in spectral_angles[-10:]:
            date_str = s['date'].strftime('%Y-%m-%d')
            bar = '█' * int(s['angle'] * 50) + '░' * (20 - int(s['angle'] * 50))
            spike = ' ★变点' if s['angle'] > SPECTRAL_ANGLE_THRESHOLD else ''
            print(f"  {date_str}  {s['angle']:.4f}  {bar}{spike}")

    # 机会与风险总结
    print(f"\n{'='*130}")
    print("机会与风险总结:")
    print("-" * 130)
    
    opportunities = [s for s in concept_signals if s['eval'] in ['机会', '偏多']]
    risks = [s for s in concept_signals if s['eval'] in ['风险', '偏空']]
    neutrals = [s for s in concept_signals if s['eval'] == '中性']
    
    print(f"\n  机会区({len(opportunities)}个):")
    for s in opportunities:
        reasons = []
        if s['above_ma40']: reasons.append(f"MA40线上(偏离{s['ma40_gap']:.1f}%)")
        if s['mom20'] > 0: reasons.append(f"动量+{s['mom20']:.1f}%")
        if s['community'] == 'leading': reasons.append("Fiedler领涨半区")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")
    
    print(f"\n  风险区({len(risks)}个):")
    for s in risks:
        reasons = []
        if not s['above_ma40']: reasons.append(f"MA40线下(偏离{s['ma40_gap']:.1f}%)")
        if s['mom20'] < 0: reasons.append(f"动量{s['mom20']:.1f}%")
        if s['community'] == 'lagging': reasons.append("Fiedler领跌半区")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")
    
    print(f"\n  中性区({len(neutrals)}个): {', '.join(s['name'] for s in neutrals)}")

    print(f"\n{'='*130}")
    print("评分规则: 趋势(MA40线上+2/线下-2) + 动量(>5%:+2/>0%:+1/<-5%:-2/<0%:-1)")
    print("         + Fiedler(领涨+1/领跌-1) + 结构(稳定+1/变化0/不稳定-1)")
    print("评价: 机会(≥3) | 偏多(1~2) | 中性(-1~0) | 偏空(-2~-3) | 风险(≤-4)")
    print("=" * 130)

if __name__ == '__main__':
    main()
