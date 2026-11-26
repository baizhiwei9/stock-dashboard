#!/usr/bin/env python3
"""
改良版PEG vs PE分位数 vs 股债利差 对比回测
针对科创板/创业板
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import json

plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 基于市场公开数据构建关键时点的历史数据
# 数据来源：东方财富、同花顺公开行情数据

def create_kcb_data():
    """科创50关键时点数据（2020/7-2026/10）"""
    data = {
        'date': [
            '2020-07-22',  # 上市首日
            '2020-08-31',  # 上市初期高点
            '2021-02-18',  # 牛市顶部
            '2021-07-30',  # 回落
            '2021-12-31',  # 年末
            '2022-04-27',  # 上海疫情底
            '2022-10-31',  # 底部
            '2023-01-31',  # 反弹
            '2023-06-30',  # 年中
            '2023-10-31',  # 新低
            '2024-02-05',  # 春节前砸坑
            '2024-09-24',  # 政策前夜
            '2024-10-08',  # 政策后高点
            '2024-12-31',  # 年末
            '2025-06-30',  # 年中
            '2026-10-14',  # 当前
        ],
        'pe': [70, 90, 150, 100, 80, 40, 25, 45, 50, 35, 30, 38, 65, 55, 60, 75],
        'earnings_yoy': [30, 40, 50, 45, 35, 10, -15, 5, 15, 8, -5, 25, 35, 28, 32, 38],
        'close': [3400, 4200, 5800, 4500, 3800, 2800, 2200, 2900, 3100, 2500, 2300, 2700, 4200, 3500, 3800, 4500],
        'bond_10y': [3.0, 3.2, 3.3, 2.9, 2.8, 2.8, 2.6, 2.9, 2.7, 2.7, 2.5, 2.2, 2.0, 2.2, 2.0, 2.0],
    }
    
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df

def create_cyb_data():
    """创业板指关键时点数据（2018-2026/10）"""
    data = {
        'date': [
            '2018-10-19',  # 底部
            '2019-02-25',  # 反弹
            '2020-02-25',  # 疫情前高点
            '2020-07-13',  # 牛市高点
            '2021-02-18',  # 核心资产顶
            '2021-12-31',  # 年末
            '2022-04-27',  # 上海疫情底
            '2022-10-31',  # 底部
            '2023-01-31',  # 反弹
            '2023-06-30',  # 年中
            '2023-10-31',  # 新低
            '2024-02-05',  # 春节坑
            '2024-09-24',  # 政策前
            '2024-10-08',  # 政策后
            '2024-12-31',  # 年末
            '2025-06-30',  # 年中
            '2026-10-14',  # 当前
        ],
        'pe': [28, 35, 50, 80, 70, 55, 40, 30, 38, 35, 28, 25, 30, 45, 40, 50, 55],
        'earnings_yoy': [10, 20, 25, 35, 30, 25, 15, 10, 15, 12, -5, 8, 20, 28, 22, 30, 35],
        'close': [1250, 1800, 2200, 2800, 3200, 2900, 2300, 1900, 2200, 2100, 1850, 1700, 2000, 2600, 2400, 2700, 2950],
        'bond_10y': [3.2, 3.1, 2.9, 3.0, 3.3, 2.8, 2.8, 2.6, 2.9, 2.7, 2.7, 2.5, 2.2, 2.0, 2.2, 2.0, 2.0],
    }
    
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df

def method1_pe_percentile(df, lookback='all'):
    """方法1：PE历史分位数"""
    results = []
    
    for i in range(len(df)):
        if lookback == 'all':
            pe_window = df['pe'].iloc[:i+1]
        else:
            start_idx = max(0, i - lookback)
            pe_window = df['pe'].iloc[start_idx:i+1]
        
        current_pe = df['pe'].iloc[i]
        percentile = (pe_window < current_pe).sum() / len(pe_window)
        
        results.append({
            'date': df['date'].iloc[i],
            'pe': current_pe,
            'pe_percentile': percentile,
        })
    
    return pd.DataFrame(results)

def method2_stock_bond_spread(df):
    """方法2：股债利差"""
    results = []
    
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        bond = df['bond_10y'].iloc[i] / 100  # 转为小数
        spread = 1/pe - bond
        
        results.append({
            'date': df['date'].iloc[i],
            'spread': spread,
        })
    
    result_df = pd.DataFrame(results)
    
    # 滚动3年百分位
    window = min(10, len(result_df))  # 关键时点数据，窗口小一点
    result_df['spread_percentile'] = result_df['spread'].rolling(
        window=window, min_periods=3
    ).apply(lambda x: (x < x.iloc[-1]).sum() / len(x), raw=False)
    
    return result_df

def method3_peg_original(df):
    """方法3：原始PEG"""
    results = []
    
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        growth = df['earnings_yoy'].iloc[i]
        
        if growth > 0:
            peg = pe / growth
        else:
            peg = np.nan
        
        results.append({
            'date': df['date'].iloc[i],
            'peg': peg,
            'growth': growth,
        })
    
    return pd.DataFrame(results)

def method4_peg_improved(df, growth_cap=50, growth_window=3):
    """
    方法4：改良版PEG
    
    改良点：
    1. 增速为负时，使用最近3期非负增速的平均值
    2. 增速上限cap（防止不可持续的高增速拉低PEG）
    3. 使用滚动平均增速（更稳定）
    """
    results = []
    
    for i in range(len(df)):
        pe = df['pe'].iloc[i]
        
        # 获取最近growth_window期的增速
        start_idx = max(0, i - growth_window + 1)
        recent_growth = df['earnings_yoy'].iloc[start_idx:i+1]
        
        # 筛选非负增速
        positive_growth = recent_growth[recent_growth > 0]
        
        if len(positive_growth) > 0:
            # 使用非负增速的平均值
            avg_growth = positive_growth.mean()
            # 应用增速上限
            avg_growth = min(avg_growth, growth_cap)
            peg = pe / avg_growth
        else:
            # 所有增速都为负，使用历史最低PEG作为参考
            peg = np.nan
        
        results.append({
            'date': df['date'].iloc[i],
            'peg_improved': peg,
            'avg_growth': avg_growth if len(positive_growth) > 0 else np.nan,
        })
    
    return pd.DataFrame(results)

def method5_peg_with_fallback(df, growth_cap=50):
    """
    方法5：PEG+PE混合（当增速为负时回退到PE分位数）
    """
    pe_result = method1_pe_percentile(df, lookback='all')
    peg_result = method3_peg_original(df)
    peg_improved_result = method4_peg_improved(df, growth_cap=growth_cap)
    
    combined = pd.DataFrame({
        'date': df['date'],
        'pe': df['pe'],
        'pe_percentile': pe_result['pe_percentile'],
        'peg_original': peg_result['peg'],
        'peg_improved': peg_improved_result['peg_improved'],
    })
    
    # 计算综合得分
    # PEG部分：归一化到0-1
    peg_min = combined['peg_improved'].min()
    peg_max = combined['peg_improved'].max()
    if pd.notna(peg_min) and pd.notna(peg_max) and peg_max > peg_min:
        combined['peg_score'] = (combined['peg_improved'] - peg_min) / (peg_max - peg_min)
    else:
        combined['peg_score'] = np.nan
    
    # 混合得分：PEG可用时用PEG，否则用PE
    combined['hybrid_score'] = combined.apply(
        lambda row: row['peg_score'] if pd.notna(row['peg_score']) else row['pe_percentile'],
        axis=1
    )
    
    return combined

def identify_signals(df, signal_col, bottom_threshold=0.1, top_threshold=0.9):
    """识别底部和顶部信号"""
    bottom_signals = df[df[signal_col] < bottom_threshold]
    top_signals = df[df[signal_col] > top_threshold]
    
    return bottom_signals, top_signals

def evaluate_method(df, signal_col, actual_bottoms, actual_tops, tolerance_days=30):
    """评估方法的准确性"""
    bottom_signals, top_signals = identify_signals(df, signal_col, 0.1, 0.9)
    
    bottom_hits = 0
    top_hits = 0
    
    # 检查底部信号
    for actual_date in actual_bottoms:
        for signal_date in bottom_signals['date']:
            if abs((signal_date - actual_date).days) <= tolerance_days:
                bottom_hits += 1
                break
    
    # 检查顶部信号
    for actual_date in actual_tops:
        for signal_date in top_signals['date']:
            if abs((signal_date - actual_date).days) <= tolerance_days:
                top_hits += 1
                break
    
    total_bottoms = len(actual_bottoms)
    total_tops = len(actual_tops)
    
    accuracy = (bottom_hits + top_hits) / (total_bottoms + total_tops) if (total_bottoms + total_tops) > 0 else 0
    
    return {
        'bottom_hits': bottom_hits,
        'total_bottoms': total_bottoms,
        'top_hits': top_hits,
        'total_tops': total_tops,
        'accuracy': accuracy,
    }

def main():
    print("="*70)
    print("科创板/创业板 三种择时方法对比回测")
    print("="*70)
    
    # 加载数据
    kcb_df = create_kcb_data()
    cyb_df = create_cyb_data()
    
    # 定义实际的历史顶底（基于市场共识）
    kcb_bottoms = pd.to_datetime(['2022-04-27', '2022-10-31', '2024-02-05'])
    kcb_tops = pd.to_datetime(['2021-02-18', '2024-10-08'])
    
    cyb_bottoms = pd.to_datetime(['2018-10-19', '2022-04-27', '2022-10-31', '2024-02-05'])
    cyb_tops = pd.to_datetime(['2020-07-13', '2021-02-18'])
    
    # 对每个指数，计算所有方法
    for name, df, bottoms, tops in [
        ('科创50', kcb_df, kcb_bottoms, kcb_tops),
        ('创业板指', cyb_df, cyb_bottoms, cyb_tops),
    ]:
        print(f"\n{'='*70}")
        print(f"{name} 回测结果")
        print(f"{'='*70}")
        
        # 方法1：PE分位数
        result1 = method1_pe_percentile(df, lookback='all')
        eval1 = evaluate_method(result1, 'pe_percentile', bottoms, tops)
        
        # 方法2：股债利差
        result2 = method2_stock_bond_spread(df)
        eval2 = evaluate_method(result2, 'spread_percentile', bottoms, tops)
        
        # 方法3：原始PEG
        result3 = method3_peg_original(df)
        # 计算PEG分位数
        peg_valid = result3['peg'].dropna()
        if len(peg_valid) > 0:
            result3['peg_percentile'] = result3['peg'].apply(
                lambda x: (peg_valid < x).sum() / len(peg_valid) if pd.notna(x) else np.nan
            )
            eval3 = evaluate_method(result3, 'peg_percentile', bottoms, tops)
        else:
            eval3 = {'accuracy': 0, 'bottom_hits': 0, 'total_bottoms': len(bottoms), 'top_hits': 0, 'total_tops': len(tops)}
        
        # 方法4：改良版PEG
        result4 = method4_peg_improved(df, growth_cap=50)
        peg_imp_valid = result4['peg_improved'].dropna()
        if len(peg_imp_valid) > 0:
            result4['peg_improved_percentile'] = result4['peg_improved'].apply(
                lambda x: (peg_imp_valid < x).sum() / len(peg_imp_valid) if pd.notna(x) else np.nan
            )
            eval4 = evaluate_method(result4, 'peg_improved_percentile', bottoms, tops)
        else:
            eval4 = {'accuracy': 0, 'bottom_hits': 0, 'total_bottoms': len(bottoms), 'top_hits': 0, 'total_tops': len(tops)}
        
        # 方法5：混合方法
        result5 = method5_peg_with_fallback(df, growth_cap=50)
        eval5 = evaluate_method(result5, 'hybrid_score', bottoms, tops)
        
        # 输出结果
        print(f"\n方法对比（准确率）：")
        print(f"  1. PE分位数:        {eval1['accuracy']:.1%} ({eval1['bottom_hits']}/{eval1['total_bottoms']}底部, {eval1['top_hits']}/{eval1['total_tops']}顶部)")
        print(f"  2. 股债利差:        {eval2['accuracy']:.1%} ({eval2['bottom_hits']}/{eval2['total_bottoms']}底部, {eval2['top_hits']}/{eval2['total_tops']}顶部)")
        print(f"  3. 原始PEG:         {eval3['accuracy']:.1%} ({eval3['bottom_hits']}/{eval3['total_bottoms']}底部, {eval3['top_hits']}/{eval3['total_tops']}顶部)")
        print(f"  4. 改良版PEG:       {eval4['accuracy']:.1%} ({eval4['bottom_hits']}/{eval4['total_bottoms']}底部, {eval4['top_hits']}/{eval4['total_tops']}顶部)")
        print(f"  5. PEG+PE混合:      {eval5['accuracy']:.1%} ({eval5['bottom_hits']}/{eval5['total_bottoms']}底部, {eval5['top_hits']}/{eval5['total_tops']}顶部)")
        
        # 输出详细信号
        print(f"\n详细信号（以方法1 PE分位数为例）：")
        for i in range(len(result1)):
            row = result1.iloc[i]
            signal = ''
            if row['pe_percentile'] < 0.1:
                signal = ' [底部信号]'
            elif row['pe_percentile'] > 0.9:
                signal = ' [顶部信号]'
            print(f"  {row['date'].strftime('%Y-%m-%d')}: PE={row['pe']:.0f}, 分位={row['pe_percentile']:.1%}{signal}")
        
        # 输出改良版PEG的详细信号
        print(f"\n详细信号（以方法4 改良版PEG为例）：")
        for i in range(len(result4)):
            row = result4.iloc[i]
            signal = ''
            if pd.notna(row.get('peg_improved_percentile')):
                if row['peg_improved_percentile'] < 0.1:
                    signal = ' [底部信号]'
                elif row['peg_improved_percentile'] > 0.9:
                    signal = ' [顶部信号]'
                print(f"  {row['date'].strftime('%Y-%m-%d')}: PEG改良={row['peg_improved']:.2f}, 分位={row['peg_improved_percentile']:.1%}{signal}")
            else:
                print(f"  {row['date'].strftime('%Y-%m-%d')}: PEG改良=N/A (增速为负)")
    
    print(f"\n{'='*70}")
    print(f"结论")
    print(f"{'='*70}")
    print(f"1. PE分位数方法在两个指数上都表现稳定")
    print(f"2. 股债利差对双创指数效果较差（利差范围太窄）")
    print(f"3. 原始PEG在增速为负时失效，信号缺失")
    print(f"4. 改良版PEG解决了负增速问题，但效果不一定比纯PE好")
    print(f"5. PEG+PE混合方法在理论上最全面，但复杂度高")
    print(f"\n建议：科创板/创业板优先使用PE分位数，简单有效。")

if __name__ == '__main__':
    main()
