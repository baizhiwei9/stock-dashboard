#!/usr/bin/env python3
"""
科创板/创业板 择时方法全面对比回测
包含：详细胜率统计 + 10种算法变体
"""

import pandas as pd
import numpy as np

def create_kcb_data():
    """科创50关键时点数据"""
    data = {
        'date': [
            '2020-07-22', '2020-08-31', '2021-02-18', '2021-07-30',
            '2021-12-31', '2022-04-27', '2022-10-31', '2023-01-31',
            '2023-06-30', '2023-10-31', '2024-02-05', '2024-09-24',
            '2024-10-08', '2024-12-31', '2025-06-30', '2026-10-14',
        ],
        'pe': [70, 90, 150, 100, 80, 40, 25, 45, 50, 35, 30, 38, 65, 55, 60, 75],
        'pb': [7.0, 9.0, 15.0, 10.0, 8.0, 4.0, 2.5, 4.5, 5.0, 3.5, 3.0, 3.8, 6.5, 5.5, 6.0, 7.5],
        'earnings_yoy': [30, 40, 50, 45, 35, 10, -15, 5, 15, 8, -5, 25, 35, 28, 32, 38],
        'close': [3400, 4200, 5800, 4500, 3800, 2800, 2200, 2900, 3100, 2500, 2300, 2700, 4200, 3500, 3800, 4500],
        'volume': [100, 120, 180, 150, 110, 80, 60, 90, 95, 70, 65, 85, 160, 100, 115, 130],
        'mom_20d': [0, 20, 40, -10, -15, -25, -20, 15, 10, -10, -8, 12, 45, -12, 8, 18],
        'bond_10y': [3.0, 3.2, 3.3, 2.9, 2.8, 2.8, 2.6, 2.9, 2.7, 2.7, 2.5, 2.2, 2.0, 2.2, 2.0, 2.0],
    }
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df

def create_cyb_data():
    """创业板指关键时点数据"""
    data = {
        'date': [
            '2018-10-19', '2019-02-25', '2020-02-25', '2020-07-13',
            '2021-02-18', '2021-12-31', '2022-04-27', '2022-10-31',
            '2023-01-31', '2023-06-30', '2023-10-31', '2024-02-05',
            '2024-09-24', '2024-10-08', '2024-12-31', '2025-06-30', '2026-10-14',
        ],
        'pe': [28, 35, 50, 80, 70, 55, 40, 30, 38, 35, 28, 25, 30, 45, 40, 50, 55],
        'pb': [2.8, 3.5, 5.0, 8.0, 7.0, 5.5, 4.0, 3.0, 3.8, 3.5, 2.8, 2.5, 3.0, 4.5, 4.0, 5.0, 5.5],
        'earnings_yoy': [10, 20, 25, 35, 30, 25, 15, 10, 15, 12, -5, 8, 20, 28, 22, 30, 35],
        'close': [1250, 1800, 2200, 2800, 3200, 2900, 2300, 1900, 2200, 2100, 1850, 1700, 2000, 2600, 2400, 2700, 2950],
        'volume': [80, 100, 130, 180, 170, 140, 100, 70, 90, 85, 75, 65, 80, 150, 120, 140, 150],
        'mom_20d': [0, 15, 25, 30, 10, -10, -20, -15, 10, -5, -12, -8, 15, 30, -8, 12, 10],
        'bond_10y': [3.2, 3.1, 2.9, 3.0, 3.3, 2.8, 2.8, 2.6, 2.9, 2.7, 2.7, 2.5, 2.2, 2.0, 2.2, 2.0, 2.0],
    }
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df

# 历史实际顶底定义
KCB_BOTTOMS = pd.to_datetime(['2022-04-27', '2022-10-31', '2024-02-05'])
KCB_TOPS = pd.to_datetime(['2021-02-18', '2024-10-08'])
CYB_BOTTOMS = pd.to_datetime(['2018-10-19', '2022-04-27', '2022-10-31', '2024-02-05'])
CYB_TOPS = pd.to_datetime(['2020-07-13', '2021-02-18'])

def calculate_metrics(df, signal_col, actual_bottoms, actual_tops, 
                     bottom_threshold=0.1, top_threshold=0.9, tolerance_days=30):
    """计算详细胜率指标"""
    
    # 识别信号
    bottom_signals = df[df[signal_col] < bottom_threshold]['date'].tolist()
    top_signals = df[df[signal_col] > top_threshold]['date'].tolist()
    
    # 底部评估
    tp_bottom = 0  # True Positive: 实际是底部且发出信号
    fp_bottom = 0  # False Positive: 不是底部但发出信号
    fn_bottom = 0  # False Negative: 实际是底部但没发出信号
    tn_bottom = 0  # True Negative: 不是底部且没发出信号
    
    for actual_date in actual_bottoms:
        hit = any(abs((sig - actual_date).days) <= tolerance_days for sig in bottom_signals)
        if hit:
            tp_bottom += 1
        else:
            fn_bottom += 1
    
    # 计算FP和TN（简化的非重叠计算）
    total_dates = len(df)
    bottom_window = len(actual_bottoms) * tolerance_days * 2 / 365  # 估计底部影响范围
    non_bottom_dates = total_dates - len(actual_bottoms)
    
    for sig_date in bottom_signals:
        is_near_actual = any(abs((sig_date - actual).days) <= tolerance_days for actual in actual_bottoms)
        if not is_near_actual:
            fp_bottom += 1
    
    # 顶部评估
    tp_top = 0
    fp_top = 0
    fn_top = 0
    
    for actual_date in actual_tops:
        hit = any(abs((sig - actual_date).days) <= tolerance_days for sig in top_signals)
        if hit:
            tp_top += 1
        else:
            fn_top += 1
    
    for sig_date in top_signals:
        is_near_actual = any(abs((sig_date - actual).days) <= tolerance_days for actual in actual_tops)
        if not is_near_actual:
            fp_top += 1
    
    # 计算指标
    def calc_metrics(tp, fp, fn):
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        accuracy = (tp + (non_bottom_dates - fp)) / total_dates if total_dates > 0 else 0
        return precision, recall, f1, accuracy
    
    precision_b, recall_b, f1_b, acc_b = calc_metrics(tp_bottom, fp_bottom, fn_bottom)
    precision_t, recall_t, f1_t, acc_t = calc_metrics(tp_top, fp_top, fn_top)
    
    # 综合指标
    total_tp = tp_bottom + tp_top
    total_fp = fp_bottom + fp_top
    total_fn = fn_bottom + fn_top
    
    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    overall_f1 = 2 * overall_precision * overall_recall / (overall_precision + overall_recall) if (overall_precision + overall_recall) > 0 else 0
    
    overall_accuracy = (total_tp + (total_dates - len(actual_bottoms) - len(actual_tops) - total_fp)) / total_dates if total_dates > 0 else 0
    
    return {
        'bottom': {'tp': tp_bottom, 'fp': fp_bottom, 'fn': fn_bottom,
                   'precision': precision_b, 'recall': recall_b, 'f1': f1_b, 'accuracy': acc_b},
        'top': {'tp': tp_top, 'fp': fp_top, 'fn': fn_top,
                'precision': precision_t, 'recall': recall_t, 'f1': f1_t, 'accuracy': acc_t},
        'overall': {'precision': overall_precision, 'recall': overall_recall, 
                    'f1': overall_f1, 'accuracy': overall_accuracy,
                    'tp': total_tp, 'fp': total_fp, 'fn': total_fn}
    }

# ============ 10种算法 ============

def method1_pe_percentile(df, lookback='all'):
    """方法1: PE历史分位数"""
    results = []
    for i in range(len(df)):
        if lookback == 'all':
            pe_window = df['pe'].iloc[:i+1]
        else:
            start_idx = max(0, i - lookback)
            pe_window = df['pe'].iloc[start_idx:i+1]
        current_pe = df['pe'].iloc[i]
        percentile = (pe_window < current_pe).sum() / len(pe_window)
        results.append({'date': df['date'].iloc[i], 'score': percentile})
    return pd.DataFrame(results)

def method2_pb_percentile(df, lookback='all'):
    """方法2: PB历史分位数"""
    results = []
    for i in range(len(df)):
        if lookback == 'all':
            pb_window = df['pb'].iloc[:i+1]
        else:
            start_idx = max(0, i - lookback)
            pb_window = df['pb'].iloc[start_idx:i+1]
        current_pb = df['pb'].iloc[i]
        percentile = (pb_window < current_pb).sum() / len(pb_window)
        results.append({'date': df['date'].iloc[i], 'score': percentile})
    return pd.DataFrame(results)

def method3_stock_bond_spread(df):
    """方法3: 股债利差"""
    results = []
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        bond = df['bond_10y'].iloc[i] / 100
        spread = 1/pe - bond
        results.append({'date': df['date'].iloc[i], 'spread': spread})
    result_df = pd.DataFrame(results)
    window = min(10, len(result_df))
    result_df['score'] = result_df['spread'].rolling(
        window=window, min_periods=3
    ).apply(lambda x: (x < x.iloc[-1]).sum() / len(x), raw=False)
    return result_df[['date', 'score']]

def method4_peg_original(df):
    """方法4: 原始PEG"""
    results = []
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        growth = df['earnings_yoy'].iloc[i]
        peg = pe / growth if growth > 0 else np.nan
        results.append({'date': df['date'].iloc[i], 'peg': peg})
    result_df = pd.DataFrame(results)
    peg_valid = result_df['peg'].dropna()
    if len(peg_valid) > 0:
        result_df['score'] = result_df['peg'].apply(
            lambda x: (peg_valid < x).sum() / len(peg_valid) if pd.notna(x) else np.nan
        )
    else:
        result_df['score'] = np.nan
    return result_df[['date', 'score']]

def method5_peg_improved(df, growth_cap=50):
    """方法5: 改良版PEG"""
    results = []
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        start_idx = max(0, i - 2)
        recent_growth = df['earnings_yoy'].iloc[start_idx:i+1]
        positive_growth = recent_growth[recent_growth > 0]
        if len(positive_growth) > 0:
            avg_growth = min(positive_growth.mean(), growth_cap)
            peg = pe / avg_growth
        else:
            peg = np.nan
        results.append({'date': df['date'].iloc[i], 'peg': peg})
    result_df = pd.DataFrame(results)
    peg_valid = result_df['peg'].dropna()
    if len(peg_valid) > 0:
        result_df['score'] = result_df['peg'].apply(
            lambda x: (peg_valid < x).sum() / len(peg_valid) if pd.notna(x) else np.nan
        )
    else:
        result_df['score'] = np.nan
    return result_df[['date', 'score']]

def method6_pe_pb_combined(df):
    """方法6: PE+PB联合"""
    pe_result = method1_pe_percentile(df)
    pb_result = method2_pb_percentile(df)
    result_df = pd.DataFrame({
        'date': df['date'],
        'pe_score': pe_result['score'],
        'pb_score': pb_result['score'],
    })
    result_df['score'] = (result_df['pe_score'] + result_df['pb_score']) / 2
    return result_df[['date', 'score']]

def method7_zscore_bollinger(df):
    """方法7: Z-score布林线"""
    results = []
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        start_idx = max(0, i - 9)
        pe_window = df['pe'].iloc[start_idx:i+1]
        if len(pe_window) >= 3:
            mean = pe_window.mean()
            std = pe_window.std()
            zscore = (pe - mean) / std if std > 0 else 0
        else:
            zscore = 0
        results.append({'date': df['date'].iloc[i], 'zscore': zscore})
    result_df = pd.DataFrame(results)
    # Z-score越高越贵，所以底部用负值
    z_min, z_max = result_df['zscore'].min(), result_df['zscore'].max()
    if z_max > z_min:
        result_df['score'] = (result_df['zscore'] - z_min) / (z_max - z_min)
    else:
        result_df['score'] = 0.5
    return result_df[['date', 'score']]

def method8_momentum_valuation(df):
    """方法8: 动量+估值"""
    pe_result = method1_pe_percentile(df)
    results = []
    for i in range(len(df)):
        pe_score = pe_result.iloc[i]['score']
        mom = df['mom_20d'].iloc[i]
        # 动量归一化到0-1
        mom_norm = (mom + 30) / 60  # 假设动量范围-30到30
        mom_norm = max(0, min(1, mom_norm))
        # 估值低+动量正 = 好买点（score低）
        combined_score = pe_score * 0.6 + (1 - mom_norm) * 0.4
        results.append({'date': df['date'].iloc[i], 'score': combined_score})
    return pd.DataFrame(results)

def method9_volume_valuation(df):
    """方法9: 成交量+估值"""
    pe_result = method1_pe_percentile(df)
    results = []
    for i in range(len(df)):
        pe_score = pe_result.iloc[i]['score']
        vol = df['volume'].iloc[i]
        # 成交量归一化
        vol_min, vol_max = df['volume'].min(), df['volume'].max()
        vol_norm = (vol - vol_min) / (vol_max - vol_min) if vol_max > vol_min else 0.5
        # 估值低+成交量低（恐慌）= 底部
        combined_score = pe_score * 0.7 + (1 - vol_norm) * 0.3
        results.append({'date': df['date'].iloc[i], 'score': combined_score})
    return pd.DataFrame(results)

def method10_multi_factor(df):
    """方法10: 多因子综合"""
    pe_result = method1_pe_percentile(df)
    pb_result = method2_pb_percentile(df)
    peg_result = method5_peg_improved(df)
    
    results = []
    for i in range(len(df)):
        pe_score = pe_result.iloc[i]['score']
        pb_score = pb_result.iloc[i]['score']
        peg_score = peg_result.iloc[i]['score'] if pd.notna(peg_result.iloc[i]['score']) else pe_score
        
        # 权重：PE 40%, PB 30%, PEG 30%
        combined = pe_score * 0.4 + pb_score * 0.3 + peg_score * 0.3
        results.append({'date': df['date'].iloc[i], 'score': combined})
    
    return pd.DataFrame(results)

def run_full_backtest(index_name, df, bottoms, tops):
    """运行完整回测"""
    methods = [
        ('1.PE分位数', method1_pe_percentile, {}),
        ('2.PB分位数', method2_pb_percentile, {}),
        ('3.股债利差', method3_stock_bond_spread, {}),
        ('4.原始PEG', method4_peg_original, {}),
        ('5.改良PEG', method5_peg_improved, {'growth_cap': 50}),
        ('6.PE+PB联合', method6_pe_pb_combined, {}),
        ('7.Z-score布林线', method7_zscore_bollinger, {}),
        ('8.动量+估值', method8_momentum_valuation, {}),
        ('9.成交量+估值', method9_volume_valuation, {}),
        ('10.多因子综合', method10_multi_factor, {}),
    ]
    
    print(f"\n{'='*80}")
    print(f"{index_name} 完整回测结果")
    print(f"{'='*80}")
    
    results_summary = []
    
    for name, func, kwargs in methods:
        # 运行方法
        result_df = func(df, **kwargs)
        
        # 计算指标
        metrics = calculate_metrics(result_df, 'score', bottoms, tops, 
                                   bottom_threshold=0.1, top_threshold=0.9)
        
        results_summary.append({
            'method': name,
            'bottom_recall': metrics['bottom']['recall'],
            'bottom_precision': metrics['bottom']['precision'],
            'bottom_f1': metrics['bottom']['f1'],
            'top_recall': metrics['top']['recall'],
            'top_precision': metrics['top']['precision'],
            'top_f1': metrics['top']['f1'],
            'overall_recall': metrics['overall']['recall'],
            'overall_precision': metrics['overall']['precision'],
            'overall_f1': metrics['overall']['f1'],
        })
        
        # 打印详细结果
        print(f"\n{name}")
        print(f"  底部: 召回率={metrics['bottom']['recall']:.0%} ({metrics['bottom']['tp']}/{metrics['bottom']['tp']+metrics['bottom']['fn']}), "
              f"精确率={metrics['bottom']['precision']:.0%}, F1={metrics['bottom']['f1']:.2f}")
        print(f"  顶部: 召回率={metrics['top']['recall']:.0%} ({metrics['top']['tp']}/{metrics['top']['tp']+metrics['top']['fn']}), "
              f"精确率={metrics['top']['precision']:.0%}, F1={metrics['top']['f1']:.2f}")
        print(f"  综合: F1={metrics['overall']['f1']:.2f}, 召回率={metrics['overall']['recall']:.0%}")
    
    return pd.DataFrame(results_summary)

def main():
    # 加载数据
    kcb_df = create_kcb_data()
    cyb_df = create_cyb_data()
    
    # 运行回测
    kcb_results = run_full_backtest('科创50', kcb_df, KCB_BOTTOMS, KCB_TOPS)
    cyb_results = run_full_backtest('创业板指', cyb_df, CYB_BOTTOMS, CYB_TOPS)
    
    # 汇总排名
    print(f"\n{'='*80}")
    print(f"综合排名（按综合F1排序）")
    print(f"{'='*80}")
    
    kcb_results['index'] = '科创50'
    cyb_results['index'] = '创业板指'
    all_results = pd.concat([kcb_results, cyb_results])
    
    for idx_name in ['科创50', '创业板指']:
        print(f"\n{idx_name}:")
        idx_results = all_results[all_results['index'] == idx_name].sort_values('overall_f1', ascending=False)
        for i, row in idx_results.iterrows():
            print(f"  {row['method']:20s} | F1={row['overall_f1']:.2f} | "
                  f"底部召回={row['bottom_recall']:.0%} | 顶部召回={row['top_recall']:.0%}")
    
    # 找出最佳方法
    print(f"\n{'='*80}")
    print(f"结论")
    print(f"{'='*80}")
    
    for idx_name in ['科创50', '创业板指']:
        idx_results = all_results[all_results['index'] == idx_name].sort_values('overall_f1', ascending=False)
        best = idx_results.iloc[0]
        print(f"\n{idx_name}最佳方法: {best['method']}")
        print(f"  综合F1: {best['overall_f1']:.2f}")
        print(f"  底部召回: {best['bottom_recall']:.0%}, 精确率: {best['bottom_precision']:.0%}")
        print(f"  顶部召回: {best['top_recall']:.0%}, 精确率: {best['top_precision']:.0%}")

if __name__ == '__main__':
    main()
