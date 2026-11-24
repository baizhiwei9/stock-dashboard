#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Approach B: 全市场Top50周涨幅个股 -> 反推概念板块
1. 用新浪API拿全市场股票按今日涨幅排序
2. 取前300只用腾讯API拉K线算周涨幅
3. 按周涨幅排序取Top50
4. 用新浪概念板块API建立 stock->concept 反向映射
5. 聚合: 哪些概念拥有最多的Top50个股
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

# ===================== 新浪API: 全市场股票列表 =====================
def get_sina_stock_list(page=1, num=100, sort='changepercent', asc=0, node='hs_a'):
    """新浪API获取A股列表(按涨幅排序)"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {
        'page': page,
        'num': num,
        'node': node,
        'sort': sort,
        'asc': asc,
    }
    try:
        resp = SESSION.get(url, params=params, timeout=12)
        return json.loads(resp.text)
    except:
        return []

def get_all_stocks_sorted():
    """获取全市场按今日涨幅降序排列的股票(前300)"""
    all_stocks = []
    for page in range(1, 5):  # 4页 x 100 = 400只
        stocks = get_sina_stock_list(page=page, num=100, sort='changepercent', asc=0)
        if not stocks:
            break
        all_stocks.extend(stocks)
    return all_stocks[:300]

# ===================== 腾讯API: 个股K线 =====================
def fetch_tencent_kline(code, count=12):
    """腾讯API拉取个股日K线"""
    if code.startswith('6') or code.startswith('5') or code.startswith('11'):
        prefix = 'sh'
    elif code.startswith('8') or code.startswith('4'):
        prefix = 'bj'
    else:
        prefix = 'sz'
    full_code = f"{prefix}{code}"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f"{full_code},day,,,{count},qfq"}
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = resp.json()
        kline = data.get('data', {}).get(full_code, {})
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
    code = stock.get('code', '')
    name = stock.get('name', '')
    daily_chg = float(stock.get('changepercent', 0) or 0)
    df = fetch_tencent_kline(code, 12)
    if df is None or len(df) < 6:
        return {'code': code, 'name': name, 'daily': round(daily_chg, 2), 'weekly': None}
    close = df['close'].values
    weekly = (close[-1] / close[-6] - 1) * 100
    biweekly = (close[-1] / close[-11] - 1) * 100 if len(close) >= 11 else 0
    return {
        'code': code,
        'name': name,
        'daily': round(daily_chg, 2),
        'weekly': round(weekly, 2),
        'biweekly': round(biweekly, 2),
        'last_date': df.iloc[-1]['date'],
    }

# ===================== 新浪概念板块: 建立反向映射 =====================
def get_sina_concepts():
    """获取新浪全部概念板块列表"""
    url = "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
    try:
        resp = SESSION.get(url, timeout=12)
        resp.encoding = 'gbk'
        text = resp.text
        # 解析 var S_Finance_bankuai_class = {...}
        match = re.search(r'=\s*(\{.*\})', text)
        if not match:
            return {}
        raw = match.group(1)
        # 解析 "gn_code":"gn_code,概念名,数量,..."
        concepts = {}
        for m in re.finditer(r'"(gn_[^"]+)":"([^"]+)"', raw):
            key = m.group(1)
            parts = m.group(2).split(',')
            if len(parts) >= 2:
                concepts[key] = parts[1]  # 概念名
        return concepts
    except Exception as e:
        print(f"  [ERR] 概念列表获取失败: {e}", file=sys.stderr)
        return {}

def get_concept_stocks_sina(node, page=1, num=100):
    """获取某概念板块成分股"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {
        'page': page,
        'num': num,
        'node': node,
        'sort': 'changepercent',
        'asc': 0,
    }
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
    done = 0

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {}
        for node, cname in concepts.items():
            futures[executor.submit(get_concept_stocks_sina, node)] = (node, cname)

        for f in as_completed(futures):
            done += 1
            if done % 30 == 0:
                print(f"  概念映射进度: {done}/{total}", file=sys.stderr)
            node, cname = futures[f]
            try:
                stocks = f.result()
                for scode in stocks:
                    if scode:
                        if scode not in stock_map:
                            stock_map[scode] = []
                        stock_map[scode].append(cname)
            except:
                pass
            time.sleep(0.05)

    return stock_map

# ===================== 主流程 =====================
def main():
    print("=" * 100, file=sys.stderr)
    print("[Approach B] 全市场Top50周涨幅个股 -> 概念反推", file=sys.stderr)
    print("=" * 100, file=sys.stderr)

    # Step 1: 获取股票列表
    print("[1/4] 获取全市场股票列表(按今日涨幅排序)...", file=sys.stderr)
    stocks = get_all_stocks_sorted()
    print(f"  获取 {len(stocks)} 只股票", file=sys.stderr)

    # Step 2: 拉K线算周涨幅
    print(f"[2/4] 并发拉取K线计算周涨幅(前{len(stocks)}只)...", file=sys.stderr)
    stock_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(compute_stock_weekly, s): s for s in stocks}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  K线进度: {done}/{len(stocks)}", file=sys.stderr)
            r = f.result()
            if r and r.get('weekly') is not None:
                stock_results.append(r)

    stock_results.sort(key=lambda x: x['weekly'], reverse=True)
    top50 = stock_results[:50]

    dates = [r.get('last_date') for r in stock_results if r.get('last_date')]
    latest_date = max(dates) if dates else 'N/A'

    # Step 3: 获取概念板块并建立反向映射
    print("[3/4] 获取概念板块并建立stock->concept映射...", file=sys.stderr)
    concepts = get_sina_concepts()
    print(f"  共 {len(concepts)} 个概念板块", file=sys.stderr)

    stock_concept_map = build_stock_concept_map(concepts)
    print(f"  映射完成: {len(stock_concept_map)} 只股票有概念标签", file=sys.stderr)

    # Step 4: 输出结果
    print("[4/4] 输出结果...", file=sys.stderr)

    print("\n" + "=" * 110)
    print(f"[Approach B] 全市场周涨幅Top50个股 (数据截止: {latest_date})")
    print("=" * 110)
    print(f"{'排名':<5} {'代码':<8} {'名称':<12} {'周涨幅%':<9} {'两周%':<8} {'今日%':<8} {'所属概念'}")
    print("-" * 110)

    # 统计概念出现频次
    concept_stats = {}  # {concept_name: {'count': N, 'stocks': [...], 'avg_weekly': float}}

    for i, s in enumerate(top50):
        code = s['code']
        name = s['name']
        concepts_of_stock = stock_concept_map.get(code, [])

        # 展示前4个概念
        cstr = ', '.join(concepts_of_stock[:4]) if concepts_of_stock else '无标签'
        if len(concepts_of_stock) > 4:
            cstr += f'...({len(concepts_of_stock)})'

        print(f"{i+1:<5} {code:<8} {name:<12} {s['weekly']:<9.2f} {s.get('biweekly',0):<8.2f} {s['daily']:<8.2f} {cstr}")

        # 统计概念
        for c in concepts_of_stock:
            if c not in concept_stats:
                concept_stats[c] = {'count': 0, 'stocks': [], 'weekly_sum': 0}
            concept_stats[c]['count'] += 1
            concept_stats[c]['stocks'].append(f"{name}({s['weekly']:.1f}%)")
            concept_stats[c]['weekly_sum'] += s['weekly']

    # 概念聚合排名
    print(f"\n{'='*110}")
    print("概念板块聚合排名 (按Top50中出现个股数量排序)")
    print("只展示至少出现3只个股的概念")
    print("=" * 110)
    print(f"{'排名':<5} {'概念名称':<16} {'个股数':<6} {'平均周涨幅%':<12} {'代表个股'}")
    print("-" * 110)

    sorted_concepts = sorted(concept_stats.items(), key=lambda x: x[1]['count'], reverse=True)
    rank = 0
    for cname, stats in sorted_concepts:
        if stats['count'] < 3:
            continue
        rank += 1
        avg_w = stats['weekly_sum'] / stats['count']
        # 展示前3只代表个股
        rep = ', '.join(stats['stocks'][:3])
        if len(stats['stocks']) > 3:
            rep += f' ...等{len(stats["stocks"])}只'

        # 标记液冷/算力相关
        kw = ['液冷','散热','算力','服务器','数据中心','IDC','光模块','CPO','铜缆','光通信','交换机']
        marker = " ★" if any(k in cname for k in kw) else ""

        print(f"{rank:<5} {cname:<16} {stats['count']:<6} {avg_w:<12.2f} {rep}{marker}")

    # 液冷相关概念定位
    print(f"\n{'='*110}")
    print("液冷/算力相关概念在全部概念中的位置(出现>=2次):")
    print("-" * 110)
    kw_list = ['液冷','散热','算力','服务器','数据中心','IDC','光模块','CPO','铜缆','光通信','交换机','芯片','通信','AI','人工智能','云计算','ICT','光电']
    found = False
    rank2 = 0
    for cname, stats in sorted_concepts:
        if stats['count'] < 2:
            continue
        rank2 += 1
        for kw in kw_list:
            if kw in cname:
                print(f"  排名{rank2:>3}: {cname:<16} 个股数 {stats['count']:<3} 平均周涨幅 {stats['weekly_sum']/stats['count']:>7.2f}%  代表: {', '.join(stats['stocks'][:3])}")
                found = True
                break
    if not found:
        print("  未找到液冷/算力相关概念(可能在Top50中无个股入选或概念名称不匹配)")

    print(f"\n{'='*110}")
    print("说明: ★ = 液冷/算力相关概念  周涨幅=近5交易日涨幅  两周=近10交易日涨幅")
    print("=" * 110)

if __name__ == '__main__':
    main()
