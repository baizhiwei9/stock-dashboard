#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合脚本:
Part 1: 液冷概念股周涨幅排名 (用东财文章确认的完整成分股清单)
Part 2: 全市场2000只Top50周涨幅 -> 概念反推 (新浪概念 + 液冷手工标签)
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

# ===================== 液冷概念股完整清单 =====================
# 来源: 东方财富液冷散热产业链文章 + 搜索结果
LIQUID_COOLING_STOCKS = {
    # 液冷系统方案商
    'sz002837': '英维克', 'bj872208': '曙光数创', 'sz301018': '申菱环境',
    'sz300990': '同飞股份', 'sz300499': '高澜股份',
    # 核心零部件
    'sz002600': '领益智造', 'sh603270': '金帝股份', 'sz002536': '飞龙股份',
    'sz002050': '三花智控', 'sz002011': '盾安环境', 'sz301521': '思泉新材',
    'sz688668': '鼎通科技', 'sz301128': '强瑞技术',
    # 冷却液/材料
    'sh600160': '巨化股份', 'sh688181': '八亿时空',
    # 液冷服务器整机
    'sz000977': '浪潮信息', 'sh603019': '中科曙光', 'sh601138': '工业富联',
    # 其他搜索确认的液冷概念股
    'sz300017': '网宿科技', 'sz000887': '中鼎股份', 'sh603516': '淳中科技',
    'sz002975': '博杰股份', 'sh603269': '海鸥股份', 'sz000811': '冰轮环境',
    'sz000530': '冰山冷热', 'sz002639': '雪人股份', 'sh603912': '佳力图',
    'sz300249': '依米康', 'sh600510': '黑牡丹', 'sz000063': '中兴通讯',
    'sz002909': '集泰股份', 'sz301176': '鸿富瀚', 'sz300602': '飞荣达',
    'sh688146': '精研科技', 'sz300831': '科创新源', 'sz002418': '康盛股份',
    'sz002328': '新朋股份', 'sz002837': '英维克',
}

# 去重
LIQUID_COOLING_STOCKS = {k: v for k, v in sorted(LIQUID_COOLING_STOCKS.items(), key=lambda x: x[0])}

# ===================== 腾讯API =====================
def fetch_tencent_kline(symbol, count=12):
    """腾讯API拉取日K线(前复权)"""
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

def compute_weekly(symbol, name):
    """计算个股周涨幅"""
    df = fetch_tencent_kline(symbol, 12)
    if df is None or len(df) < 6:
        return None
    close = df['close'].values
    weekly = (close[-1] / close[-6] - 1) * 100
    biweekly = (close[-1] / close[-11] - 1) * 100 if len(close) >= 11 else 0
    daily = (close[-1] / close[-2] - 1) * 100 if len(close) >= 2 else 0
    return {
        'symbol': symbol, 'code': symbol[2:], 'name': name,
        'daily': round(daily, 2), 'weekly': round(weekly, 2),
        'biweekly': round(biweekly, 2), 'last_date': df.iloc[-1]['date'],
        'close': round(close[-1], 2),
    }

# ===================== 新浪API =====================
def get_sina_stocks(num=100, page=1, sort='nmc', asc=0):
    """新浪API获取A股列表"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {'page': page, 'num': num, 'node': 'hs_a', 'sort': sort, 'asc': asc}
    try:
        resp = SESSION.get(url, params=params, timeout=12)
        return json.loads(resp.text)
    except:
        return []

def get_sina_concepts():
    """获取新浪全部概念板块"""
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
            parts = m.group(2).split(',')
            if len(parts) >= 2:
                concepts[m.group(1)] = parts[1]
        return concepts
    except:
        return {}

def get_concept_stocks(node):
    """获取概念板块成分股代码"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {'page': 1, 'num': 100, 'node': node, 'sort': 'changepercent', 'asc': 0}
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = json.loads(resp.text)
        return [s.get('code', '') for s in data]
    except:
        return []

def build_concept_map(concepts):
    """构建 stock_code -> [concept_names]"""
    stock_map = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(get_concept_stocks, node): cname for node, cname in concepts.items()}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 30 == 0:
                print(f"  概念映射: {done}/{len(concepts)}", file=sys.stderr)
            cname = futures[f]
            try:
                codes = f.result()
                for c in codes:
                    stock_map.setdefault(c, []).append(cname)
            except:
                pass
            time.sleep(0.03)
    return stock_map

# ===================== 主流程 =====================
def main():
    print("=" * 115, file=sys.stderr)
    print("综合分析: 液冷概念股排名 + 全市场Top50概念反推", file=sys.stderr)
    print("=" * 115, file=sys.stderr)

    # ========== Part 1: 液冷概念股周涨幅排名 ==========
    print("\n[Part 1] 液冷概念股周涨幅排名...", file=sys.stderr)
    lc_codes = list(set(LIQUID_COOLING_STOCKS.keys()))
    print(f"  共 {len(lc_codes)} 只液冷概念股", file=sys.stderr)

    lc_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(compute_weekly, sym, LIQUID_COOLING_STOCKS[sym]): sym for sym in lc_codes}
        for f in as_completed(futures):
            r = f.result()
            if r:
                lc_results.append(r)

    lc_results.sort(key=lambda x: x['weekly'], reverse=True)
    lc_dates = [r['last_date'] for r in lc_results if r.get('last_date')]
    lc_date = max(lc_dates) if lc_dates else 'N/A'

    # 液冷概念整体统计
    lc_weeklys = [r['weekly'] for r in lc_results]
    lc_avg = np.mean(lc_weeklys) if lc_weeklys else 0
    lc_median = np.median(lc_weeklys) if lc_weeklys else 0
    lc_positive = sum(1 for w in lc_weeklys if w > 0)

    print("\n" + "=" * 115)
    print(f"[Part 1] 液冷概念股周涨幅排名 (数据截止: {lc_date})")
    print(f"  整体: 平均周涨幅 {lc_avg:.2f}%, 中位数 {lc_median:.2f}%, 上涨 {lc_positive}/{len(lc_results)}只")
    print("=" * 115)
    print(f"{'排名':<5} {'代码':<8} {'名称':<12} {'周涨幅%':<9} {'两周%':<8} {'昨日%':<8} {'最新价':<9} {'细分赛道'}")
    print("-" * 115)

    # 细分赛道标签
    sector_tags = {
        'sz002837': '系统方案商', 'bj872208': '系统方案商', 'sz301018': '系统方案商',
        'sz300990': '系统方案商', 'sz300499': '系统方案商',
        'sz002600': '零部件', 'sh603270': '零部件', 'sz002536': '零部件',
        'sz002050': '零部件', 'sz002011': '零部件', 'sz301521': '零部件',
        'sz688668': '零部件', 'sz301128': '零部件',
        'sh600160': '冷却液/材料', 'sh688181': '冷却液/材料',
        'sz000977': '服务器整机', 'sh603019': '服务器整机', 'sh601138': '服务器整机',
        'sz300017': '其他', 'sz000887': '其他', 'sh603516': '其他',
        'sz002975': '其他', 'sh603269': '其他', 'sz000811': '温控设备',
        'sz000530': '温控设备', 'sz002639': '温控设备', 'sh603912': '温控设备',
        'sz300249': '温控设备', 'sh600510': '其他', 'sz000063': '通信设备',
        'sz002909': '其他', 'sz301176': '其他', 'sz300602': '其他',
        'sh688146': '其他', 'sz300831': '其他', 'sz002418': '其他', 'sz002328': '其他',
    }

    for i, r in enumerate(lc_results):
        tag = sector_tags.get(r['symbol'], '')
        print(f"{i+1:<5} {r['code']:<8} {r['name']:<12} {r['weekly']:<9.2f} {r['biweekly']:<8.2f} {r['daily']:<8.2f} {r['close']:<9.2f} {tag}")

    # ========== Part 2: 全市场Top50周涨幅 -> 概念反推 ==========
    print(f"\n[Part 2] 全市场2000只Top50周涨幅 -> 概念反推...", file=sys.stderr)

    # Step 1: 获取2000只股票
    print("  [1] 获取全市场股票(20页x100=2000, 按市值)...", file=sys.stderr)
    all_stocks = []
    for page in range(1, 21):
        stocks = get_sina_stocks(num=100, page=page, sort='nmc', asc=0)
        if not stocks:
            break
        all_stocks.extend(stocks)
    print(f"  获取 {len(all_stocks)} 只", file=sys.stderr)

    # Step 2: 拉K线算周涨幅
    print(f"  [2] 并发拉取K线算周涨幅...", file=sys.stderr)
    stock_results = []
    fail = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for s in all_stocks:
            sym = s.get('symbol', '')
            name = s.get('name', '')
            if sym:
                futures[executor.submit(compute_weekly, sym, name)] = sym
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 100 == 0:
                print(f"  K线进度: {done}/{len(all_stocks)} (成功{len(stock_results)} 失败{fail})", file=sys.stderr)
            r = f.result()
            if r and r.get('weekly') is not None:
                stock_results.append(r)
            else:
                fail += 1

    stock_results.sort(key=lambda x: x['weekly'], reverse=True)
    top50 = stock_results[:50]
    dates = [r.get('last_date') for r in stock_results if r.get('last_date')]
    latest_date = max(dates) if dates else 'N/A'
    print(f"  成功: {len(stock_results)}/{len(all_stocks)}, 数据截止: {latest_date}", file=sys.stderr)

    # Step 3: 概念映射
    print("  [3] 建立stock->concept映射...", file=sys.stderr)
    concepts = get_sina_concepts()
    print(f"  新浪概念: {len(concepts)} 个", file=sys.stderr)
    sina_map = build_concept_map(concepts)
    print(f"  新浪映射: {len(sina_map)} 只股票", file=sys.stderr)

    # 添加液冷手工标签
    lc_codes_set = set(v['code'] for v in [{'code': s[2:]} for s in LIQUID_COOLING_STOCKS])
    for code in lc_codes_set:
        sina_map.setdefault(code, []).append('液冷概念')

    print(f"  最终映射: {len(sina_map)} 只股票(含液冷标签)", file=sys.stderr)

    # Step 4: 输出Top50
    print("  [4] 输出结果...", file=sys.stderr)

    print("\n" + "=" * 120)
    print(f"[Part 2] 全市场周涨幅Top50个股 (数据截止: {latest_date})")
    print("=" * 120)
    print(f"{'排名':<5} {'代码':<8} {'名称':<12} {'周涨幅%':<9} {'两周%':<8} {'昨日%':<8} {'所属概念'}")
    print("-" * 120)

    concept_stats = {}
    for i, s in enumerate(top50):
        code = s['code']
        concepts_of = sina_map.get(code, [])
        cstr = ', '.join(concepts_of[:5]) if concepts_of else '无标签'
        if len(concepts_of) > 5:
            cstr += f'...({len(concepts_of)})'

        # 标记液冷
        is_lc = '液冷概念' in concepts_of
        marker = ' ★液冷' if is_lc else ''

        print(f"{i+1:<5} {code:<8} {s['name']:<12} {s['weekly']:<9.2f} {s['biweekly']:<8.2f} {s['daily']:<8.2f} {cstr}{marker}")

        for c in concepts_of:
            if c not in concept_stats:
                concept_stats[c] = {'count': 0, 'stocks': [], 'weekly_sum': 0}
            concept_stats[c]['count'] += 1
            concept_stats[c]['stocks'].append(f"{s['name']}({s['weekly']:.1f}%)")
            concept_stats[c]['weekly_sum'] += s['weekly']

    # 概念聚合
    print(f"\n{'='*120}")
    print("概念板块聚合排名 (Top50中出现>=3只个股的概念)")
    print("=" * 120)
    print(f"{'排名':<5} {'概念名称':<18} {'个股数':<6} {'平均周涨幅%':<12} {'代表个股'}")
    print("-" * 120)

    sorted_c = sorted(concept_stats.items(), key=lambda x: (x[1]['count'], x[1]['weekly_sum']/max(x[1]['count'],1)), reverse=True)
    rank = 0
    kw_list = ['液冷','散热','算力','服务器','光模块','CPO','铜缆','光通信','交换机','芯片','通信','AI','人工智能','云计算','ICT','光电','电子','软件','半导体','华为','5G','军工','银行']
    for cname, stats in sorted_c:
        if stats['count'] < 3:
            continue
        rank += 1
        avg_w = stats['weekly_sum'] / stats['count']
        rep = ', '.join(stats['stocks'][:3])
        if len(stats['stocks']) > 3:
            rep += f' ...等{len(stats["stocks"])}只'
        marker = " ★" if any(k in cname for k in kw_list) else ""
        print(f"{rank:<5} {cname:<18} {stats['count']:<6} {avg_w:<12.2f} {rep}{marker}")

    # 液冷概念定位
    print(f"\n{'='*120}")
    print("液冷/算力/科技相关概念在全部概念中的位置(出现>=2次):")
    print("-" * 120)
    found = False
    rank2 = 0
    for cname, stats in sorted_c:
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

    print(f"\n{'='*120}")
    print("说明: ★=液冷/算力/科技相关  周涨幅=近5交易日  两周=近10交易日")
    print("=" * 120)

if __name__ == '__main__':
    main()
