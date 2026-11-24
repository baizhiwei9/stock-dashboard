#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Approach B (修复版): 全市场Top50周涨幅个股 -> 反推概念板块
修复点:
1. 新浪API按市值(nmc)排序而非今日涨幅(开盘前无数据)
2. 用symbol字段直接取交易所前缀, 修复北京交易所bug
3. 扩大到500只股票
"""
import requests
import pandas as pd
import numpy as np
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
})

def get_sina_stocks_by_mcap(num=100, page=1):
    """新浪API按市值降序获取A股"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {'page': page, 'num': num, 'node': 'hs_a', 'sort': 'nmc', 'asc': 0}
    try:
        resp = SESSION.get(url, params=params, timeout=12)
        return json.loads(resp.text)
    except:
        return []

def fetch_tencent_kline(symbol, count=12):
    """腾讯API拉取个股日K线 - 用新浪symbol直接做前缀"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f"{symbol},day,,,{count},qfq"}
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = resp.json()
        kline = data.get('data', {}).get(symbol, {})
        bars = kline.get('qfqday') or kline.get('day')
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=['date','open','close','high','low','volume'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        return df
    except:
        return None

def compute_stock_weekly(stock):
    """计算个股周涨幅"""
    symbol = stock.get('symbol', '')
    code = stock.get('code', '')
    name = stock.get('name', '')
    df = fetch_tencent_kline(symbol, 12)
    if df is None or len(df) < 6:
        return None
    close = df['close'].values
    weekly = (close[-1] / close[-6] - 1) * 100
    biweekly = (close[-1] / close[-11] - 1) * 100 if len(close) >= 11 else 0
    daily = (close[-1] / close[-2] - 1) * 100 if len(close) >= 2 else 0
    return {
        'symbol': symbol,
        'code': code,
        'name': name,
        'daily': round(daily, 2),
        'weekly': round(weekly, 2),
        'biweekly': round(biweekly, 2),
        'last_date': df.iloc[-1]['date'],
    }

def get_sina_concepts():
    """获取新浪全部概念板块列表"""
    url = "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
    try:
        resp = SESSION.get(url, timeout=12)
        resp.encoding = 'gbk'
        text = resp.text
        match = re.search(r'=\s*(\{.*\})', text)
        if not match:
            return {}
        raw = match.group(1)
        concepts = {}
        for m in re.finditer(r'"(gn_[^"]+)":"([^"]+)"', raw):
            key = m.group(1)
            parts = m.group(2).split(',')
            if len(parts) >= 2:
                concepts[key] = parts[1]
        return concepts
    except Exception as e:
        print(f"  [ERR] 概念列表: {e}", file=sys.stderr)
        return {}

def get_concept_stocks_sina(node):
    """获取某概念板块成分股代码列表"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {'page': 1, 'num': 100, 'node': node, 'sort': 'changepercent', 'asc': 0}
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = json.loads(resp.text)
        return [s.get('code', '') for s in data]
    except:
        return []

def build_stock_concept_map(concepts):
    """构建 stock_code -> [concept_names] 反向映射"""
    stock_map = {}
    total = len(concepts)
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(get_concept_stocks_sina, node): (node, cname) for node, cname in concepts.items()}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 30 == 0:
                print(f"  概念映射进度: {done}/{total}", file=sys.stderr)
            node, cname = futures[f]
            try:
                stocks = f.result()
                for scode in stocks:
                    if scode:
                        stock_map.setdefault(scode, []).append(cname)
            except:
                pass
            time.sleep(0.03)
    return stock_map

def main():
    print("=" * 110, file=sys.stderr)
    print("[Approach B 修复版] 全市场Top50周涨幅个股 -> 概念反推", file=sys.stderr)
    print("=" * 110, file=sys.stderr)

    # Step 1: 按市值获取500只股票(覆盖主流标的)
    print("[1/4] 获取全市场股票(按市值降序, 5页x100=500只)...", file=sys.stderr)
    all_stocks = []
    for page in range(1, 6):
        stocks = get_sina_stocks_by_mcap(num=100, page=page)
        if not stocks:
            break
        all_stocks.extend(stocks)
    print(f"  获取 {len(all_stocks)} 只股票", file=sys.stderr)

    # Step 2: 并发拉K线算周涨幅
    print(f"[2/4] 并发拉取K线计算周涨幅...", file=sys.stderr)
    stock_results = []
    fail_count = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(compute_stock_weekly, s): s for s in all_stocks}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  K线进度: {done}/{len(all_stocks)} (成功{len(stock_results)} 失败{fail_count})", file=sys.stderr)
            r = f.result()
            if r and r.get('weekly') is not None:
                stock_results.append(r)
            else:
                fail_count += 1

    stock_results.sort(key=lambda x: x['weekly'], reverse=True)
    top50 = stock_results[:50]

    dates = [r.get('last_date') for r in stock_results if r.get('last_date')]
    latest_date = max(dates) if dates else 'N/A'
    print(f"  成功: {len(stock_results)}/{len(all_stocks)}, 失败: {fail_count}", file=sys.stderr)
    print(f"  数据截止: {latest_date}", file=sys.stderr)

    # Step 3: 概念映射
    print("[3/4] 获取概念板块并建立stock->concept映射...", file=sys.stderr)
    concepts = get_sina_concepts()
    print(f"  共 {len(concepts)} 个概念板块", file=sys.stderr)
    stock_concept_map = build_stock_concept_map(concepts)
    print(f"  映射完成: {len(stock_concept_map)} 只股票有概念标签", file=sys.stderr)

    # Step 4: 输出
    print("[4/4] 输出结果...", file=sys.stderr)

    print("\n" + "=" * 115)
    print(f"[Approach B] 全市场周涨幅Top50个股 (数据截止: {latest_date})")
    print("=" * 115)
    print(f"{'排名':<5} {'代码':<8} {'名称':<12} {'周涨幅%':<9} {'两周%':<8} {'昨日%':<8} {'所属概念'}")
    print("-" * 115)

    concept_stats = {}
    for i, s in enumerate(top50):
        code = s['code']
        concepts_of = stock_concept_map.get(code, [])
        cstr = ', '.join(concepts_of[:4]) if concepts_of else '无标签'
        if len(concepts_of) > 4:
            cstr += f'...({len(concepts_of)})'
        print(f"{i+1:<5} {code:<8} {s['name']:<12} {s['weekly']:<9.2f} {s['biweekly']:<8.2f} {s['daily']:<8.2f} {cstr}")

        for c in concepts_of:
            if c not in concept_stats:
                concept_stats[c] = {'count': 0, 'stocks': [], 'weekly_sum': 0}
            concept_stats[c]['count'] += 1
            concept_stats[c]['stocks'].append(f"{s['name']}({s['weekly']:.1f}%)")
            concept_stats[c]['weekly_sum'] += s['weekly']

    # 概念聚合
    print(f"\n{'='*115}")
    print("概念板块聚合排名 (按Top50中出现个股数排序, 至少3只)")
    print("=" * 115)
    print(f"{'排名':<5} {'概念名称':<18} {'个股数':<6} {'平均周涨幅%':<12} {'代表个股'}")
    print("-" * 115)

    sorted_concepts = sorted(concept_stats.items(), key=lambda x: (x[1]['count'], x[1]['weekly_sum']/max(x[1]['count'],1)), reverse=True)
    kw_list = ['液冷','散热','算力','服务器','数据中心','IDC','光模块','CPO','铜缆','光通信','交换机','芯片','通信','AI','人工智能','云计算','ICT','光电','电子','软件','半导体']
    rank = 0
    for cname, stats in sorted_concepts:
        if stats['count'] < 3:
            continue
        rank += 1
        avg_w = stats['weekly_sum'] / stats['count']
        rep = ', '.join(stats['stocks'][:3])
        if len(stats['stocks']) > 3:
            rep += f' ...等{len(stats["stocks"])}只'
        marker = " ★" if any(k in cname for k in kw_list) else ""
        print(f"{rank:<5} {cname:<18} {stats['count']:<6} {avg_w:<12.2f} {rep}{marker}")

    # 液冷相关定位
    print(f"\n{'='*115}")
    print("液冷/算力/科技相关概念在全部概念中的位置(出现>=2次):")
    print("-" * 115)
    found = False
    rank2 = 0
    for cname, stats in sorted_concepts:
        if stats['count'] < 2:
            continue
        rank2 += 1
        for kw in kw_list:
            if kw in cname:
                print(f"  排名{rank2:>3}: {cname:<18} 个股数 {stats['count']:<3} 平均周涨幅 {stats['weekly_sum']/stats['count']:>7.2f}%  代表: {', '.join(stats['stocks'][:3])}")
                found = True
                break
    if not found:
        print("  未找到匹配概念")

    print(f"\n{'='*115}")
    print("说明: ★=液冷/算力/科技相关  周涨幅=近5交易日  两周=近10交易日  昨日%=上个交易日涨跌")
    print("=" * 115)

if __name__ == '__main__':
    main()
