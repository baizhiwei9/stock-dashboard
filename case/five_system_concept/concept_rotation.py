#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念板块周涨幅排名 + 龙头股筛选
1. 拉取全部概念板块日K线，计算近5交易日(一周)涨幅
2. 按周涨幅排名Top30
3. 对Top10概念，拉成分股展示涨幅领先的个股
"""
import requests
import pandas as pd
import numpy as np
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/'
})

def get_all_concepts():
    """获取全部概念板块列表"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        'fs': 'm:90+t:2',
        'fields': 'f2,f3,f4,f8,f12,f14',
        'fid': 'f3',
        'po': '1',
        'pz': '500',
        'pn': '1',
        'np': '1',
        'fltt': '2',
    }
    resp = SESSION.get(url, params=params, timeout=15)
    data = resp.json()
    boards = data.get('data', {}).get('diff', [])
    return boards

def get_kline(secid, days=12):
    """获取日K线"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        'secid': secid,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57',
        'klt': '101',
        'fqt': '1',
        'lmt': str(days),
        'end': '20261231',
    }
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = resp.json()
        klines = data.get('data', {}).get('klines', [])
        if not klines:
            return None
        rows = [k.split(',') for k in klines]
        df = pd.DataFrame(rows, columns=['date','open','close','high','low','volume','amount'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        return df
    except:
        return None

def fetch_concept_data(c):
    """获取单个概念的周涨幅"""
    code = c.get('f12', '')
    name = c.get('f14', '')
    secid = f"90.{code}"
    df = get_kline(secid, days=12)
    if df is None or len(df) < 6:
        return None
    close = df['close'].values
    latest = close[-1]
    week_ago = close[-6]
    weekly = (latest / week_ago - 1) * 100
    biweekly = (latest / close[-11] - 1) * 100 if len(close) >= 11 else 0
    daily = c.get('f3', 0) or 0
    last_date = df.iloc[-1]['date']
    return {
        'code': code,
        'name': name,
        'daily': round(float(daily), 2),
        'weekly': round(weekly, 2),
        'biweekly': round(biweekly, 2),
        'latest': round(latest, 2),
        'last_date': last_date,
    }

def get_concept_stocks(concept_code):
    """获取概念板块成分股(按今日涨幅排序)"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        'fs': f'b:{concept_code}',
        'fields': 'f2,f3,f4,f5,f6,f7,f8,f12,f14',
        'fid': 'f3',
        'po': '1',
        'pz': '30',
        'pn': '1',
        'np': '1',
        'fltt': '2',
    }
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = resp.json()
        stocks = data.get('data', {}).get('diff', [])
        return stocks
    except:
        return []

def fetch_stock_weekly(s):
    """获取个股周涨幅"""
    code = s.get('f12', '')
    name = s.get('f14', '')
    daily = s.get('f3', 0) or 0
    # 判断市场前缀
    if code.startswith('6') or code.startswith('5') or code.startswith('11'):
        secid = f"1.{code}"
    else:
        secid = f"0.{code}"
    df = get_kline(secid, days=12)
    if df is None or len(df) < 6:
        return {'code': code, 'name': name, 'daily': round(float(daily),2), 'weekly': None}
    close = df['close'].values
    weekly = (close[-1] / close[-6] - 1) * 100
    return {'code': code, 'name': name, 'daily': round(float(daily),2), 'weekly': round(weekly, 2)}

def main():
    # === Step 1: 获取全部概念板块 ===
    concepts = get_all_concepts()
    print(f"获取到 {len(concepts)} 个概念板块", file=sys.stderr)

    # === Step 2: 并发拉取K线, 计算周涨幅 ===
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_concept_data, c): c for c in concepts}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  概念K线进度: {done}/{len(concepts)}", file=sys.stderr)
            r = f.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x['weekly'], reverse=True)

    # 获取数据截止日
    dates = [r['last_date'] for r in results if r.get('last_date')]
    latest_date = max(dates) if dates else 'N/A'

    print("=" * 105)
    print(f"概念板块周涨幅排名 (近5交易日)  数据截止: {latest_date}")
    print("=" * 105)
    print(f"{'排名':<5} {'代码':<10} {'概念名称':<18} {'周涨幅%':<10} {'两周涨幅%':<12} {'今日涨幅%':<10}")
    print("-" * 105)
    for i, r in enumerate(results[:30]):
        marker = " <--" if any(kw in r['name'] for kw in ['液冷','散热','算力','服务器','AI','芯片','光模块','CPO']) else ""
        print(f"{i+1:<5} {r['code']:<10} {r['name']:<18} {r['weekly']:<10.2f} {r['biweekly']:<12.2f} {r['daily']:<10.2f}{marker}")

    # === Step 3: Top10概念成分股 ===
    print(f"\n{'='*105}")
    print("Top10 概念板块成分股 (按今日涨幅排序, 展示前8)")
    print("=" * 105)

    for r in results[:10]:
        code = r['code']
        name = r['name']
        stocks = get_concept_stocks(code)
        if not stocks:
            continue

        # 并发拉取成分股周涨幅
        stock_results = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_stock_weekly, s): s for s in stocks[:15]}
            for f in as_completed(futures):
                res = f.result()
                if res:
                    stock_results.append(res)

        stock_results.sort(key=lambda x: x.get('weekly') if x.get('weekly') is not None else -999, reverse=True)

        print(f"\n{'─'*80}")
        print(f"  [{name}]  周涨幅 {r['weekly']:.2f}%  两周 {r['biweekly']:.2f}%  今日 {r['daily']:.2f}%")
        print(f"{'─'*80}")
        print(f"  {'代码':<8} {'名称':<14} {'周涨幅%':<10} {'今日涨幅%':<10}")
        for sr in stock_results[:8]:
            wk = f"{sr['weekly']:.2f}" if sr.get('weekly') is not None else "N/A"
            print(f"  {sr['code']:<8} {sr['name']:<14} {wk:<10} {sr['daily']:<10.2f}")
        time.sleep(0.15)

    # === Step 4: 检查液冷相关概念是否在列表中 ===
    liquid_keywords = ['液冷', '散热', '服务器', '算力', '光模块', 'CPO', '铜缆', '交换机']
    print(f"\n{'='*105}")
    print("液冷/算力相关概念在全部排名中的位置:")
    print("-" * 105)
    for i, r in enumerate(results):
        for kw in liquid_keywords:
            if kw in r['name']:
                print(f"  排名{i+1:>3}: {r['name']:<18} 周涨幅 {r['weekly']:>7.2f}%  今日 {r['daily']:>7.2f}%  两周 {r['biweekly']:>7.2f}%")
                break

    print(f"\n{'='*105}")

if __name__ == '__main__':
    main()
