#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Approach A: 细化ETF/概念池 + 动量+MA40规则
在原有18只行业ETF基础上，大量补充细分概念ETF(算力/液冷/光模块/数据中心等)
"""
import requests
import pandas as pd
import numpy as np
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

# 原有18只行业ETF + 大量补充细分概念ETF
ALL_ETFS = {
    # === 原有行业ETF ===
    'sh512800': '银行', 'sh512880': '证券', 'sh512200': '房地产',
    'sh512660': '军工', 'sh515030': '新能源车', 'sh515790': '光伏',
    'sh512480': '半导体', 'sh515880': '通信', 'sh512720': '计算机',
    'sh515170': '食品饮料', 'sh512400': '有色金属', 'sh512980': '传媒',
    'sh515220': '煤炭', 'sh516950': '基建', 'sh512580': '环保',
    'sh512690': '白酒', 'sz159928': '消费', 'sh512010': '医药',

    # === 细分科技/算力概念ETF ===
    'sh516510': '云计算ETF', 'sh515400': '大数据ETF', 'sh515070': '人工智能ETF',
    'sh562500': '机器人ETF', 'sh588000': '科创50', 'sh515050': '5G ETF',
    'sh512760': '芯片ETF', 'sh515000': '科技ETF', 'sh516930': '基建50ETF',
    'sh516160': '数据中心ETF',  # 数据中心/IDC
    'sh159890': '云计算50ETF', 'sh515080': '中药ETF',
    'sz159825': '养殖ETF', 'sh515710': '光伏ETF30',
    'sz159766': '旅游ETF', 'sh516970': '基建50',
    'sh513050': '中概互联ETF', 'sh513030': '德国ETF',
    'sh513100': '纳指ETF', 'sh513500': '标普500ETF',
    'sh588200': '科创芯片ETF', 'sh588060': '科创AI ETF',
    'sh561930': '算力ETF',  # 可能存在
    'sh516000': '科技传媒ETF',
    'sh515950': '光伏ETF', 'sh515850': '证券保险ETF',
    'sh515060': '通信设备ETF', 'sh515580': '通信ETF',
    'sh512970': '传媒ETF', 'sh515950': '新能源车ETF30',
    'sz159775': '电子ETF', 'sh515090': '人工智能ETF30',
    'sh563000': '芯片ETF30', 'sh516710': '软件ETF',
    'sh515760': '光伏产业ETF',
    'sh512610': '中证500ETF', 'sh510300': '沪深300ETF',
    'sh510500': '中证500', 'sh512100': '中证1000',
    'sz159915': '创业板ETF', 'sh512690': '白酒',
    'sh515650': '稀土ETF', 'sh515170': '食品饮料',
    'sh516950': '基建50', 'sh512660': '军工',
    'sh516150': '基建ETF', 'sh513050': '中概互联',
    'sh515030': '新能源车',
    # 可能的液冷/散热相关
    'sh516160': '数据中心',
    'sh588000': '科创50',
}

def fetch_tencent(code, count=120):
    """腾讯API拉取日K线(前复权)"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f"{code},day,,,{count},qfq"}
    try:
        resp = SESSION.get(url, params=params, timeout=12)
        data = resp.json()
        kline = data.get('data', {}).get(code, {})
        bars = kline.get('qfqday') or kline.get('day')
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=['date','open','close','high','low','volume'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        return df
    except:
        return None

def compute_signals(code, name, df):
    """计算动量+趋势信号"""
    if df is None or len(df) < 41:
        return None
    close = df['close'].values
    n = len(close)
    latest = close[-1]

    mom5 = (latest / close[-6] - 1) * 100 if n >= 6 else 0
    mom10 = (latest / close[-11] - 1) * 100 if n >= 11 else 0
    mom20 = (latest / close[-21] - 1) * 100 if n >= 21 else 0
    ma40 = np.mean(close[-40:]) if n >= 40 else 0
    above_ma40 = latest > ma40 if ma40 > 0 else False
    ma40_gap = (latest / ma40 - 1) * 100 if ma40 > 0 else 0

    # 标记液冷/算力相关
    kw = ['液冷','散热','算力','服务器','数据中心','IDC','光模块','通信','CPO','芯片','半导体','电子','计算机','AI','人工智能','云计算','大数据','软件','科技','5G','科创']
    is_hot_concept = any(k in name for k in kw)

    return {
        'code': code[2:],
        'name': name,
        'mom5': round(mom5, 2),
        'mom10': round(mom10, 2),
        'mom20': round(mom20, 2),
        'above_ma40': above_ma40,
        'ma40_gap': round(ma40_gap, 2),
        'is_hot': is_hot_concept,
        'last_date': df.iloc[-1]['date'],
    }

def main():
    # 去重
    seen = set()
    etfs = {}
    for k, v in ALL_ETFS.items():
        if k not in seen:
            seen.add(k)
            etfs[k] = v

    print(f"[Approach A] 细化ETF池: {len(etfs)} 只", file=sys.stderr)

    # 并发拉取K线
    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {}
        for code, name in etfs.items():
            futures[executor.submit(fetch_tencent, code, 60)] = (code, name)

        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 20 == 0:
                print(f"  进度: {done}/{len(etfs)}", file=sys.stderr)
            code, name = futures[f]
            df = f.result()
            sig = compute_signals(code, name, df)
            if sig:
                results.append(sig)

    print(f"  成功获取: {len(results)}/{len(etfs)}", file=sys.stderr)
    df_res = pd.DataFrame(results)
    df_sorted = df_res.sort_values('mom20', ascending=False).reset_index(drop=True)

    dates = df_res['last_date'].dropna().unique()
    latest_date = max(dates) if len(dates) > 0 else 'N/A'

    print("\n" + "=" * 110)
    print(f"[Approach A] 细化ETF池动量排名 (数据截止: {latest_date})")
    print("=" * 110)

    # Top20 动量
    print(f"\n{'排名':<5} {'代码':<8} {'名称':<16} {'5日%':<8} {'10日%':<8} {'20日%':<8} {'MA40':<5} {'偏离%':<7} {'标签':<6}")
    print("-" * 110)
    for i, row in df_sorted.head(20).iterrows():
        ma40_s = "Y" if row['above_ma40'] else "N"
        hot_s = "★" if row['is_hot'] else ""
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<16} {row['mom5']:<8.2f} {row['mom10']:<8.2f} {row['mom20']:<8.2f} {ma40_s:<5} {row['ma40_gap']:<7.2f} {hot_s}")

    # MA40线上 + 动量排序
    df_above = df_res[df_res['above_ma40'] == True].sort_values('mom20', ascending=False).reset_index(drop=True)
    print(f"\n{'='*110}")
    print(f"MA40线上(趋势做多) 共{len(df_above)}只:")
    print("-" * 110)
    print(f"{'排名':<5} {'代码':<8} {'名称':<16} {'20日%':<8} {'偏离%':<7} {'标签':<6}")
    print("-" * 110)
    for i, row in df_above.iterrows():
        hot_s = "★" if row['is_hot'] else ""
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<16} {row['mom20']:<8.2f} {row['ma40_gap']:<7.2f} {hot_s}")

    # 液冷/算力相关ETF定位
    print(f"\n{'='*110}")
    print("液冷/算力/通信相关ETF在全部排名中的位置:")
    print("-" * 110)
    for i, row in df_sorted.iterrows():
        if row['is_hot']:
            print(f"  排名{i+1:>3}: {row['name']:<16} 20日 {row['mom20']:>7.2f}%  10日 {row['mom10']:>7.2f}%  MA40 {'线上' if row['above_ma40'] else '线下'}")

    # 全部列表
    print(f"\n{'='*110}")
    print("全部ETF完整排名:")
    print("-" * 110)
    print(f"{'排名':<5} {'代码':<8} {'名称':<16} {'5日%':<8} {'10日%':<8} {'20日%':<8} {'MA40':<5} {'标签':<4}")
    print("-" * 110)
    for i, row in df_sorted.iterrows():
        ma40_s = "Y" if row['above_ma40'] else "N"
        hot_s = "★" if row['is_hot'] else ""
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<16} {row['mom5']:<8.2f} {row['mom10']:<8.2f} {row['mom20']:<8.2f} {ma40_s:<5} {hot_s}")

    print(f"\n{'='*110}")
    print("说明: ★ = 液冷/算力/科技相关ETF   MA40线上 = 趋势做多信号")
    print("=" * 110)

if __name__ == '__main__':
    main()
