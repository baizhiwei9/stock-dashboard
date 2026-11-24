#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行业 ETF 板块轮动信号 -- 20日动量排名 + MA40趋势过滤
规则来源: RMT系统(Top5动量) + Hodge系统(MA40趋势过滤)
"""
import akshare as ak
import pandas as pd
import numpy as np
import warnings
import sys

warnings.filterwarnings('ignore')

# 18只行业ETF (RMT系统使用)
INDUSTRY_ETFS = {
    '159928': '消费',
    '512010': '医药',
    '512800': '银行',
    '512880': '证券',
    '512200': '房地产',
    '512660': '军工',
    '515030': '新能源车',
    '515790': '光伏',
    '512480': '半导体',
    '515880': '通信',
    '512720': '计算机',
    '515170': '食品饮料',
    '512400': '有色金属',
    '512980': '传媒',
    '515220': '煤炭',
    '516950': '基建',
    '512580': '环保',
    '512690': '白酒',
}

# 补充更多行业ETF，确保覆盖液冷服务器相关板块
EXTRA_ETFS = {
    '515050': '5GETF',
    '512760': '电子50',
    '515070': '人工智能',
    '515790': '光伏',  # dup
    '562500': '机器人',
    '588000': '科创50',
    '515080': '中药',
    '159825': '养殖',
}

def fetch_etf_data(code, days=120):
    """用akshare拉取ETF日线数据"""
    try:
        df = ak.fund_etf_hist_em(symbol=code, period='daily', adjust='qfq')
        if df is None or len(df) == 0:
            return None
        df = df.tail(days).copy()
        df['收盘'] = pd.to_numeric(df['收盘'], errors='coerce')
        df = df.dropna(subset=['收盘'])
        return df
    except Exception as e:
        print(f"  [WARN] {code} 拉取失败: {e}", file=sys.stderr)
        return None

def compute_signals(df):
    """计算动量和趋势信号"""
    close = df['收盘'].values
    n = len(close)
    if n < 41:
        return None
    
    latest = close[-1]
    
    # 20日动量 (RMT核心信号)
    mom20 = (latest / close[-21] - 1) * 100 if n >= 21 else np.nan
    
    # 5日动量
    mom5 = (latest / close[-6] - 1) * 100 if n >= 6 else np.nan
    
    # 10日动量
    mom10 = (latest / close[-11] - 1) * 100 if n >= 11 else np.nan
    
    # MA40趋势过滤 (Hodge系统核心)
    ma40 = np.mean(close[-40:]) if n >= 40 else np.nan
    above_ma40 = latest > ma40 if not np.isnan(ma40) else False
    ma40_gap = (latest / ma40 - 1) * 100 if not np.isnan(ma40) else np.nan
    
    # MA20
    ma20 = np.mean(close[-20:]) if n >= 20 else np.nan
    above_ma20 = latest > ma20 if not np.isnan(ma20) else False
    
    # 近20日波动率
    rets = np.diff(np.log(close[-21:])) if n >= 21 else np.array([])
    vol20 = np.std(rets) * np.sqrt(252) * 100 if len(rets) > 0 else np.nan
    
    # 60日动量
    mom60 = (latest / close[-61] - 1) * 100 if n >= 61 else np.nan
    
    return {
        'latest': latest,
        'mom5': round(mom5, 2),
        'mom10': round(mom10, 2),
        'mom20': round(mom20, 2),
        'mom60': round(mom60, 2),
        'above_ma40': above_ma40,
        'above_ma20': above_ma20,
        'ma40_gap': round(ma40_gap, 2) if not np.isnan(ma40_gap) else None,
        'vol20': round(vol20, 2) if not np.isnan(vol20) else None,
    }

def main():
    print("=" * 90)
    print("行业ETF板块轮动信号 -- RMT(20日动量Top5) + Hodge(MA40趋势过滤)")
    print(f"数据拉取时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)
    
    all_etfs = {**INDUSTRY_ETFS}
    # 去重
    seen_codes = set(all_etfs.keys())
    for k, v in EXTRA_ETFS.items():
        if k not in seen_codes:
            all_etfs[k] = v
    
    results = []
    for code, name in all_etfs.items():
        print(f"  拉取 {code} {name}...", file=sys.stderr)
        df = fetch_etf_data(code, days=120)
        if df is None:
            print(f"  [SKIP] {code} {name} 无数据", file=sys.stderr)
            continue
        sig = compute_signals(df)
        if sig is None:
            print(f"  [SKIP] {code} {name} 数据不足", file=sys.stderr)
            continue
        sig['code'] = code
        sig['name'] = name
        results.append(sig)
    
    if not results:
        print("ERROR: 没有获取到任何数据!", file=sys.stderr)
        return
    
    df_res = pd.DataFrame(results)
    
    # --- RMT规则: 20日动量Top5 ---
    df_sorted = df_res.sort_values('mom20', ascending=False).reset_index(drop=True)
    
    print("\n" + "-" * 90)
    print(">>> RMT系统选股: 20日动量Top5 <<<")
    print("-" * 90)
    print(f"{'排名':<5} {'代码':<8} {'板块':<10} {'20日涨幅%':<12} {'10日涨幅%':<12} {'5日涨幅%':<12} {'MA40线上':<8}")
    print("-" * 90)
    for i, row in df_sorted.head(5).iterrows():
        ma40_str = "是" if row['above_ma40'] else "否"
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<10} {row['mom20']:<12.2f} {row['mom10']:<12.2f} {row['mom5']:<12.2f} {ma40_str:<8}")
    
    # --- Hodge规则: MA40线上 + 动量排序 ---
    df_above = df_res[df_res['above_ma40'] == True].sort_values('mom20', ascending=False).reset_index(drop=True)
    
    print("\n" + "-" * 90)
    print(">>> Hodge系统: MA40趋势过滤(线上) + 动量排名 <<<")
    print("-" * 90)
    print(f"{'排名':<5} {'代码':<8} {'板块':<10} {'20日涨幅%':<12} {'MA40偏离%':<12} {'60日涨幅%':<12} {'波动率%':<10}")
    print("-" * 90)
    for i, row in df_above.iterrows():
        ma40_gap = row['ma40_gap'] if row['ma40_gap'] is not None else 0
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<10} {row['mom20']:<12.2f} {ma40_gap:<12.2f} {row['mom60']:<12.2f} {row['vol20']:<10.2f}")
    
    # --- 全部板块完整排名 ---
    print("\n" + "-" * 90)
    print(">>> 全部行业ETF完整排名 (按20日动量) <<<")
    print("-" * 90)
    print(f"{'排名':<5} {'代码':<8} {'板块':<10} {'5日%':<10} {'10日%':<10} {'20日%':<10} {'60日%':<10} {'MA40':<6} {'MA20':<6} {'偏离%':<8} {'波动率%':<8}")
    print("-" * 90)
    for i, row in df_sorted.iterrows():
        ma40_str = "Y" if row['above_ma40'] else "N"
        ma20_str = "Y" if row['above_ma20'] else "N"
        ma40_gap = row['ma40_gap'] if row['ma40_gap'] is not None else 0
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<10} {row['mom5']:<10.2f} {row['mom10']:<10.2f} {row['mom20']:<10.2f} {row['mom60']:<10.2f} {ma40_str:<6} {ma20_str:<6} {ma40_gap:<8.2f} {row['vol20']:<8.2f}")
    
    print("\n" + "=" * 90)
    print("说明:")
    print("  - RMT系统: 平时满仓Top5动量(20日), 极端状态触发干预(delta_s阈值)")
    print("  - Hodge系统: MA40趋势过滤(线上做多)+Hodge谱角距离变点检测")
    print("  - 动量 = (今日收盘 / N日前收盘 - 1) * 100")
    print("  - MA40线上 = 收盘价 > 40日均线 (趋势做多信号)")
    print("=" * 90)

if __name__ == '__main__':
    main()
