#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RMT随机矩阵概念轮动系统
原系统逻辑(行业ETF版 +100.59%回测):
  收益率→相关矩阵→RMT去噪(Marchenko-Pastur分布过滤噪声特征值)→
  保留显著相关结构→20日动量Top5选概念→delta_s极端状态触发减仓

改造版: 将20个概念板块当作行业, 套用同一套分析框架
输出: RMT去噪后的相关结构 + Top5动量概念 + 极端状态检测 + 每个概念的机会/风险评价
"""
import pandas as pd
import numpy as np
from numpy.linalg import eigh
import pickle
import sys

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

# ===================== 参数 =====================
MOM_PERIOD = 20        # 动量周期
MA_PERIOD = 40         # 趋势均线周期
TOP_K = 5             # 动量Top K选概念
DELTA_S_THRESHOLD = 0.5  # delta_s极端状态阈值: 超过此值触发减仓
RMT_WINDOW = 60       # RMT分析窗口(用全部可用数据)

def load_data():
    returns = pd.read_pickle(f'{OUT_DIR}/concept_returns.pkl')
    close = pd.read_pickle(f'{OUT_DIR}/concept_close.pkl')
    return returns, close

def marchenko_pastur_bounds(N, T, sigma=1.0):
    """
    Marchenko-Pastur分布的上下界
    N: 变量数(概念数)
    T: 样本数(交易日数)
    Q = T/N (必须 > 1)
    sigma: 方差(归一化后=1)
    """
    Q = T / N
    if Q <= 1:
        Q = 1.001  # 避免除零
    lambda_minus = sigma**2 * (1 - 1/np.sqrt(Q))**2
    lambda_plus = sigma**2 * (1 + 1/np.sqrt(Q))**2
    return lambda_minus, lambda_plus

def rmt_denoise(corr_matrix, N, T):
    """
    RMT去噪:
    1. 对相关矩阵做特征分解
    2. 识别落在MP分布区间内的噪声特征值
    3. 将噪声特征值替换为0, 重构去噪后的相关矩阵

    返回: 去噪后相关矩阵, 噪声特征值数量, 显著特征值列表
    """
    # 确保对称
    C = corr_matrix.copy()
    C = (C + C.T) / 2

    # 特征分解
    eigenvalues, eigenvectors = eigh(C)
    idx = np.argsort(eigenvalues)[::-1]  # 降序
    eigenvalues = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]

    # MP分布上下界
    lambda_minus, lambda_plus = marchenko_pastur_bounds(N, T)

    # 区分噪声特征值和显著特征值
    noise_mask = (eigenvalues >= lambda_minus) & (eigenvalues <= lambda_plus)
    signal_mask = ~noise_mask

    n_noise = noise_mask.sum()
    n_signal = signal_mask.sum()

    # 去噪: 将噪声特征值设为0, 重构矩阵
    denoised_evals = eigenvalues.copy()
    denoised_evals[noise_mask] = 0.0

    # 重构去噪后的相关矩阵
    C_denoised = eigenvectors @ np.diag(denoised_evals) @ eigenvectors.T

    # 归一化对角线为1
    d = np.sqrt(np.diag(C_denoised))
    d[d == 0] = 1
    C_denoised = C_denoised / np.outer(d, d)
    np.fill_diagonal(C_denoised, 1.0)

    return C_denoised, eigenvalues, n_noise, n_signal, lambda_minus, lambda_plus

def compute_delta_s(returns_window, N, T):
    """
    计算delta_s: 衡量相关矩阵偏离MP分布的程度
    delta_s = 信号特征值能量 / 总能量
    delta_s高 = 市场存在显著共移动(系统性风险高)
    delta_s低 = 市场噪声主导(无明确趋势)
    """
    corr = returns_window.corr().fillna(0).values
    C = (corr + corr.T) / 2

    eigenvalues, _ = eigh(C)
    eigenvalues = np.sort(eigenvalues)[::-1]

    lambda_minus, lambda_plus = marchenko_pastur_bounds(N, T)

    total_energy = np.sum(eigenvalues**2)
    if total_energy == 0:
        return 0.0

    # 信号能量: MP区间外特征值的能量
    signal_energy = np.sum(eigenvalues[(eigenvalues > lambda_plus) | (eigenvalues < lambda_minus)]**2)

    delta_s = signal_energy / total_energy
    return float(delta_s)

def compute_entropy(eigenvalues):
    """
    计算特征值熵: 衡量相关结构的分散程度
    高熵 = 分散(各概念独立运动)
    低熵 = 集中(少数主因子驱动)
    """
    evals = eigenvalues[eigenvalues > 0]
    if len(evals) == 0:
        return 0.0
    evals = evals / evals.sum()
    entropy = -np.sum(evals * np.log(evals + 1e-15))
    max_entropy = np.log(len(evals))
    if max_entropy > 0:
        entropy = entropy / max_entropy
    return float(entropy)

def compute_ma(close, period=40):
    return close.rolling(window=period, min_periods=1).mean()

def compute_momentum(close, period=20):
    return close.pct_change(periods=period) * 100

def main():
    print("=" * 110, file=sys.stderr)
    print("RMT随机矩阵概念轮动系统 (20概念版)", file=sys.stderr)
    print("=" * 110, file=sys.stderr)

    returns, close = load_data()
    concept_names = list(returns.columns)
    n_concepts = len(concept_names)
    n_days = len(returns)

    T = RMT_WINDOW if n_days >= RMT_WINDOW else n_days
    N = n_concepts

    print(f"数据: {n_days}个交易日 x {n_concepts}个概念", file=sys.stderr)
    print(f"日期范围: {returns.index[0].strftime('%Y-%m-%d')} ~ {returns.index[-1].strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"RMT窗口: {T}日, N={N}概念, Q=T/N={T/N:.2f}", file=sys.stderr)
    print(f"参数: 动量{MOM_PERIOD}日, MA{MA_PERIOD}, Top{TOP_K}, delta_s阈值={DELTA_S_THRESHOLD}", file=sys.stderr)

    # ===================== Step 1: RMT去噪 =====================
    print(f"\n[1/5] RMT去噪分析...", file=sys.stderr)

    returns_window = returns.iloc[-T:]
    corr_raw = returns_window.corr().fillna(0).values
    corr_raw = (corr_raw + corr_raw.T) / 2

    # 原始特征分解
    raw_evals, raw_evecs = eigh(corr_raw)
    raw_evals = np.sort(raw_evals)[::-1]

    # MP分布上下界
    lambda_minus, lambda_plus = marchenko_pastur_bounds(N, T)
    Q = T / N

    print(f"  Marchenko-Pastur: Q={Q:.2f}, lambda_minus={lambda_minus:.4f}, lambda_plus={lambda_plus:.4f}", file=sys.stderr)
    print(f"  原始特征值: max={raw_evals[0]:.4f}, min={raw_evals[-1]:.4f}, mean={raw_evals.mean():.4f}", file=sys.stderr)

    # RMT去噪
    corr_denoised, denoised_evals, n_noise, n_signal, _, _ = rmt_denoise(corr_raw, N, T)

    # 去噪后的显著特征值
    signal_evals = raw_evals[(raw_evals > lambda_plus) | (raw_evals < lambda_minus)]

    print(f"  噪声特征值: {n_noise}个 (落在MP区间内)", file=sys.stderr)
    print(f"  显著特征值: {n_signal}个 (落在MP区间外)", file=sys.stderr)
    print(f"  显著特征值列表: {np.round(signal_evals, 4)}", file=sys.stderr)
    print(f"  噪声占比: {n_noise/N*100:.1f}%", file=sys.stderr)

    # 最大特征值分析(市场因子)
    max_eigenvalue = raw_evals[0]
    max_eigenvalue_ratio = max_eigenvalue / N
    market_factor_strength = max_eigenvalue_ratio

    # delta_s
    delta_s = compute_delta_s(returns_window, N, T)

    # 特征值熵
    entropy = compute_entropy(raw_evals)

    # Gap_1
    gap_1 = raw_evals[0] / raw_evals[1] if raw_evals[1] > 0 else float('inf')

    # delta_s极端状态判定
    extreme_state = delta_s > DELTA_S_THRESHOLD

    print(f"  delta_s={delta_s:.4f} ({'极端状态!' if extreme_state else '正常状态'})", file=sys.stderr)
    print(f"  特征值熵={entropy:.4f} ({'分散' if entropy > 0.7 else '集中' if entropy < 0.4 else '中等'})", file=sys.stderr)
    print(f"  Gap_1={gap_1:.4f} (最大/次大特征值比)", file=sys.stderr)
    print(f"  市场因子强度={market_factor_strength:.4f} ({max_eigenvalue_ratio*100:.1f}%方差)", file=sys.stderr)

    # ===================== Step 2: 去噪后相关结构分析 =====================
    print(f"\n[2/5] 去噪后相关结构分析...", file=sys.stderr)

    avg_corr_raw = (corr_raw.sum() - np.trace(corr_raw)) / (N * (N - 1))
    avg_corr_denoised = (corr_denoised.sum() - np.trace(corr_denoised)) / (N * (N - 1))

    concept_avg_corr = {}
    for i, name in enumerate(concept_names):
        row = corr_denoised[i, :].copy()
        row[i] = 0
        concept_avg_corr[name] = float(row.mean())

    # 聚类: |corr| > 0.3 则同簇
    cluster_adj = (np.abs(corr_denoised) > 0.3).astype(int)
    np.fill_diagonal(cluster_adj, 0)

    visited = [False] * N
    clusters = []
    for i in range(N):
        if not visited[i]:
            stack = [i]
            cluster = []
            while stack:
                node = stack.pop()
                if visited[node]:
                    continue
                visited[node] = True
                cluster.append(node)
                for j in range(N):
                    if cluster_adj[node, j] and not visited[j]:
                        stack.append(j)
            if len(cluster) > 1:
                clusters.append(cluster)

    print(f"  原始平均相关: {avg_corr_raw:.4f}", file=sys.stderr)
    print(f"  去噪后平均相关: {avg_corr_denoised:.4f}", file=sys.stderr)
    print(f"  去噪后相关簇: {len(clusters)}个", file=sys.stderr)
    for i, cluster in enumerate(clusters):
        names = [concept_names[j] for j in cluster]
        print(f"    簇{i+1}({len(cluster)}个): {', '.join(names)}", file=sys.stderr)

    # ===================== Step 3: 动量Top5选概念 =====================
    print(f"\n[3/5] {MOM_PERIOD}日动量Top{TOP_K}选概念...", file=sys.stderr)

    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)

    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]

    mom_ranked = latest_mom.sort_values(ascending=False)

    top5 = mom_ranked.head(TOP_K)
    bottom5 = mom_ranked.tail(TOP_K)

    print(f"  Top{TOP_K}动量概念:", file=sys.stderr)
    for name, val in top5.items():
        above = 'Y' if latest_close[name] > latest_ma[name] else 'N'
        print(f"    {name:<14} 动量={val:+.2f}%  MA40{'线上' if above=='Y' else '线下'}", file=sys.stderr)

    print(f"  Bottom{TOP_K}动量概念:", file=sys.stderr)
    for name, val in bottom5.items():
        above = 'Y' if latest_close[name] > latest_ma[name] else 'N'
        print(f"    {name:<14} 动量={val:+.2f}%  MA40{'线上' if above=='Y' else '线下'}", file=sys.stderr)

    # ===================== Step 4: 综合信号 =====================
    print(f"\n[4/5] 生成综合RMT信号...", file=sys.stderr)

    # delta_s历史(滚动计算)
    delta_s_history = []
    ds_window = min(30, n_days)
    for t in range(max(T, ds_window), n_days + 1):
        window = returns.iloc[t - T:t] if t >= T else returns.iloc[:t]
        ds = compute_delta_s(window, N, len(window))
        delta_s_history.append({
            'date': returns.index[t - 1],
            'delta_s': ds,
        })

    ds_latest = delta_s_history[-1]['delta_s'] if delta_s_history else delta_s
    ds_avg5 = np.mean([d['delta_s'] for d in delta_s_history[-5:]]) if len(delta_s_history) >= 5 else ds_latest
    ds_trend = 'rising' if len(delta_s_history) >= 3 and delta_s_history[-1]['delta_s'] > delta_s_history[-3]['delta_s'] else 'falling'

    # RMT模式判定
    if extreme_state:
        rmt_mode = 'extreme'
        mode_desc = '极端共移动: 市场系统性风险高, 宜减仓/防御'
    elif delta_s > DELTA_S_THRESHOLD * 0.7:
        rmt_mode = 'elevated'
        mode_desc = '系统性风险偏高: 概念间相关性增强, 趋势可能接近尾声'
    elif delta_s > DELTA_S_THRESHOLD * 0.4:
        rmt_mode = 'normal'
        mode_desc = '正常分化: 有明确热点, 动量策略有效'
    else:
        rmt_mode = 'dispersed'
        mode_desc = '高度分散: 无明确主线, 动量策略效果有限'

    # 逐概念RMT信号
    concept_signals = []
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0
        avg_c = concept_avg_corr.get(name, 0)

        is_top = name in top5.index
        is_bottom = name in bottom5.index
        mom_rank = mom_ranked.index.get_loc(name) + 1

        # 信号评分
        trend_score = 2 if above_ma else -2
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        top_score = 2 if is_top else (1 if mom_rank <= TOP_K + 3 else 0)
        bottom_score = -2 if is_bottom else 0
        if rmt_mode == 'extreme': ds_score = -1
        elif rmt_mode == 'elevated': ds_score = 0
        elif rmt_mode == 'normal': ds_score = 1
        else: ds_score = 0
        if avg_c < 0.1: corr_score = 1
        elif avg_c < 0.3: corr_score = 0
        else: corr_score = -1

        total_score = trend_score + mom_score + top_score + bottom_score + ds_score + corr_score

        if total_score >= 4: eval_label = '机会'
        elif total_score >= 2: eval_label = '偏多'
        elif total_score >= 0: eval_label = '中性'
        elif total_score >= -2: eval_label = '偏空'
        else: eval_label = '风险'

        # 极端状态下, 即使评分高也降级
        if extreme_state and eval_label in ['机会', '偏多']:
            eval_label = '中性'

        concept_signals.append({
            'name': name,
            'close': round(c_close, 3),
            'ma40': round(c_ma, 3),
            'above_ma40': above_ma,
            'ma40_gap': round(ma_gap, 2),
            'mom20': round(c_mom, 2),
            'mom_rank': mom_rank,
            'is_top5': is_top,
            'is_bottom5': is_bottom,
            'avg_corr_denoised': round(avg_c, 4),
            'trend_score': trend_score,
            'mom_score': mom_score,
            'top_score': top_score,
            'bottom_score': bottom_score,
            'ds_score': ds_score,
            'corr_score': corr_score,
            'total_score': total_score,
            'eval': eval_label,
        })

    concept_signals.sort(key=lambda x: x['total_score'], reverse=True)

    # ===================== Step 5: 输出 =====================
    print(f"\n[5/5] 输出结果...", file=sys.stderr)

    print("\n" + "=" * 130)
    print(f"RMT随机矩阵概念轮动分析 (数据截止: {returns.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 130)

    # 系统级状态
    print(f"\n--- RMT系统级状态 ---")
    print(f"  Marchenko-Pastur: Q={Q:.2f}, lambda_minus={lambda_minus:.4f}, lambda_plus={lambda_plus:.4f}")
    print(f"  最大特征值: {max_eigenvalue:.4f} (占{max_eigenvalue_ratio*100:.1f}%方差)")
    print(f"  Gap_1: {gap_1:.4f}")
    print(f"  特征值熵: {entropy:.4f} ({'分散' if entropy > 0.7 else '集中' if entropy < 0.4 else '中等'})")
    print(f"  噪声特征值: {n_noise}个 ({n_noise/N*100:.1f}%), 显著特征值: {n_signal}个")
    print(f"  去噪前后平均相关: {avg_corr_raw:.4f} -> {avg_corr_denoised:.4f}")
    print(f"  delta_s: {ds_latest:.4f} (5日均值: {ds_avg5:.4f}, 趋势: {ds_trend})")
    print(f"  RMT模式: {rmt_mode}")
    print(f"  模式含义: {mode_desc}")
    if extreme_state:
        print(f"  *** 极端状态! delta_s > {DELTA_S_THRESHOLD}, 市场系统性风险高 ***")

    # 特征值分布
    print(f"\n{'='*130}")
    print("特征值分布 (原始 vs MP分布区间)")
    print("-" * 130)
    print(f"  MP分布区间: [{lambda_minus:.4f}, {lambda_plus:.4f}]")
    print(f"  {'排名':<5} {'特征值':<10} {'类型':<8} {'方差占比':<8} {'柱状图'}")
    for i, ev in enumerate(raw_evals):
        ev_type = '噪声' if lambda_minus <= ev <= lambda_plus else '显著'
        var_pct = ev / N * 100
        bar_len = min(40, int(ev * 20))
        bar = '█' * bar_len + '░' * (40 - bar_len)
        marker = ' ★' if ev_type == '显著' else ''
        print(f"  {i+1:<5} {ev:<10.4f} {ev_type:<8} {var_pct:<8.1f}% {bar}{marker}")

    # 概念信号表
    print(f"\n{'='*130}")
    print(f"概念板块 RMT 信号表 (按综合评分降序)")
    print("=" * 130)
    print(f"{'排名':<5} {'概念':<14} {'评分':<5} {'评价':<5} {'MA40':<5} {'偏离%':<7} {'20日动量%':<9} {'动量排名':<7} {'Top5':<4} {'去噪相关':<8} {'趋势':<4} {'动量':<4} {'Top':<4} {'Bot':<4} {'DS':<4} {'相关'}")
    print("-" * 130)

    eval_markers = {'机会': '★', '偏多': '▲', '中性': '─', '偏空': '▼', '风险': '✕'}

    for i, s in enumerate(concept_signals):
        marker = eval_markers.get(s['eval'], '')
        ma_s = 'Y' if s['above_ma40'] else 'N'
        top_s = 'Y' if s['is_top5'] else '-'
        bot_s = 'Y' if s['is_bottom5'] else '-'
        print(f"{i+1:<5} {s['name']:<14} {s['total_score']:<+5} {s['eval']:<5}{marker} "
              f"{ma_s:<5} {s['ma40_gap']:<7.2f} {s['mom20']:<9.2f} "
              f"{s['mom_rank']:<7} {top_s:<4} {s['avg_corr_denoised']:<8.4f} "
              f"{s['trend_score']:<+4} {s['mom_score']:<+4} {s['top_score']:<+4} {s['bottom_score']:<+4} {s['ds_score']:<+4} {s['corr_score']:<+4}")

    # 强弱分布
    print(f"\n{'='*130}")
    print("概念板块强弱分布:")
    for label in ['机会', '偏多', '中性', '偏空', '风险']:
        names = [s['name'] for s in concept_signals if s['eval'] == label]
        if names:
            print(f"  {label} ({len(names)}个): {', '.join(names)}")

    # Top5选概念明细
    print(f"\n{'='*130}")
    print(f"RMT动量Top{TOP_K}选概念 (核心持仓池):")
    print("-" * 130)
    sig_map = {s['name']: s for s in concept_signals}
    for name, val in top5.items():
        c_above = latest_close[name] > latest_ma[name]
        avg_c = concept_avg_corr.get(name, 0)
        si = sig_map[name]
        print(f"  {name:<14} 动量={val:+.2f}%  MA40{'线上' if c_above else '线下'}  "
              f"去噪相关={avg_c:.4f}  评分={si['total_score']:+d}  评价={si['eval']}")

    print(f"\nRMT动量Bottom{TOP_K} (风险观察池):")
    print("-" * 130)
    for name, val in bottom5.items():
        c_above = latest_close[name] > latest_ma[name]
        avg_c = concept_avg_corr.get(name, 0)
        si = sig_map[name]
        print(f"  {name:<14} 动量={val:+.2f}%  MA40{'线上' if c_above else '线下'}  "
              f"去噪相关={avg_c:.4f}  评分={si['total_score']:+d}  评价={si['eval']}")

    # delta_s历史
    if len(delta_s_history) >= 10:
        print(f"\n{'='*130}")
        print("delta_s 历史 (最近10个窗口, 检测极端状态):")
        print("-" * 130)
        threshold_bar = int(DELTA_S_THRESHOLD * 50)
        for s in delta_s_history[-10:]:
            date_str = s['date'].strftime('%Y-%m-%d')
            bar_len = min(50, int(s['delta_s'] * 50))
            bar = '█' * bar_len + '░' * (50 - bar_len)
            spike = ' ★极端' if s['delta_s'] > DELTA_S_THRESHOLD else ''
            print(f"  {date_str}  delta_s={s['delta_s']:.4f}  {bar}{spike}")
        print(f"  {'':>10}  阈值={DELTA_S_THRESHOLD:.2f}  {'─' * threshold_bar}<-阈值线")

    # 去噪后相关矩阵热力图(文字版)
    print(f"\n{'='*130}")
    print("去噪后相关矩阵 (文字热力图, 只显示 |corr| > 0.15 的对):")
    print("-" * 130)
    significant_pairs = []
    for i in range(N):
        for j in range(i+1, N):
            c = corr_denoised[i, j]
            if abs(c) > 0.15:
                significant_pairs.append((concept_names[i], concept_names[j], c))
    significant_pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    for a, b, c in significant_pairs[:25]:
        direction = '正' if c > 0 else '负'
        bar_len = min(30, int(abs(c) * 30))
        bar = '█' * bar_len
        print(f"  {a:<14} <-> {b:<14}  {c:+.4f}  ({direction}) {bar}")

    # 机会与风险总结
    print(f"\n{'='*130}")
    print("RMT机会与风险总结:")
    print("-" * 130)

    opportunities = [s for s in concept_signals if s['eval'] in ['机会', '偏多']]
    risks = [s for s in concept_signals if s['eval'] in ['风险', '偏空']]
    neutrals = [s for s in concept_signals if s['eval'] == '中性']

    print(f"\n  系统状态: {rmt_mode} ({mode_desc})")
    if extreme_state:
        print(f"  [!] 极端状态: delta_s={ds_latest:.4f} > 阈值{DELTA_S_THRESHOLD}, 建议整体减仓至防御状态")
    else:
        print(f"  delta_s={ds_latest:.4f}, 未触发极端状态, 动量策略正常生效")

    print(f"\n  机会区({len(opportunities)}个):")
    for s in opportunities:
        reasons = []
        if s['above_ma40']: reasons.append(f"MA40线上(偏离{s['ma40_gap']:.1f}%)")
        if s['is_top5']: reasons.append(f"动量Top5(第{s['mom_rank']}名)")
        if s['mom20'] > 0: reasons.append(f"动量+{s['mom20']:.1f}%")
        if s['avg_corr_denoised'] < 0.1: reasons.append("低相关(独立行情)")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")

    print(f"\n  风险区({len(risks)}个):")
    for s in risks:
        reasons = []
        if not s['above_ma40']: reasons.append(f"MA40线下(偏离{s['ma40_gap']:.1f}%)")
        if s['is_bottom5']: reasons.append(f"动量Bottom5(第{s['mom_rank']}名)")
        if s['mom20'] < 0: reasons.append(f"动量{s['mom20']:.1f}%")
        if s['avg_corr_denoised'] > 0.3: reasons.append("高相关(跟随系统性风险)")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")

    print(f"\n  中性区({len(neutrals)}个): {', '.join(s['name'] for s in neutrals)}")

    print(f"\n{'='*130}")
    print("评分规则: 趋势(MA40线上+2/线下-2) + 动量(>5%:+2/>0%:+1/<-5%:-2/<0%:-1)")
    print("         + Top5(+2/Top8+1) + Bottom5(-2) + delta_s模式(正常+1/极端-1) + 相关性(低+1/高-1)")
    print("评价: 机会(>=4) | 偏多(2~3) | 中性(0~1) | 偏空(-1~-2) | 风险(<=-3)")
    print(f"极端状态(delta_s>{DELTA_S_THRESHOLD}): 机会/偏多降级为中性")
    print("=" * 130)

if __name__ == '__main__':
    main()
