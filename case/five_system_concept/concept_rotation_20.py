#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
20概念板块综合轮动评价系统
6个手工定义新概念(液冷/算力/光模块/服务器/人工智能/半导体) + 14个新浪概念
统一规则: 周涨幅 + MA40趋势强度 + 上涨比例 -> 综合评分 -> 强弱评价
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

# ===================== 6个手工定义新概念 =====================
MANUAL_CONCEPTS = {
    '液冷概念': {
        '002837': '英维克', '603019': '中科曙光', '300499': '高澜股份',
        '300017': '网宿科技', '301018': '申菱环境', '000887': '中鼎股份',
        '688668': '鼎通科技', '301128': '强瑞技术', '603516': '淳中科技',
        '002975': '博杰股份', '603269': '海鸥股份', '000811': '冰轮环境',
        '000530': '冰山冷热', '002639': '雪人股份', '603912': '佳力图',
        '300249': '依米康', '600510': '黑牡丹', '000977': '浪潮信息',
        '601138': '工业富联', '000063': '中兴通讯', '002909': '集泰股份',
        '301176': '鸿富瀚', '300602': '飞荣达', '688146': '精研科技',
        '300831': '科创新源', '002418': '康盛股份', '002328': '新朋股份',
        '002600': '领益智造', '603270': '金帝股份', '002536': '飞龙股份',
        '002050': '三花智控', '002011': '盾安环境', '301521': '思泉新材',
        '600160': '巨化股份', '688181': '八亿时空', '300990': '同飞股份',
    },
    'AI算力': {
        '000977': '浪潮信息', '601138': '工业富联', '603019': '中科曙光',
        '000938': '紫光股份', '000063': '中兴通讯', '688256': '寒武纪',
        '300017': '网宿科技', '300454': '深信服', '603881': '数据港',
        '688345': '优刻得', '688041': '海光信息', '600845': '宝信软件',
    },
    '光模块/CPO': {
        '300308': '中际旭创', '300502': '新易盛', '300394': '天孚通信',
        '002281': '光迅科技', '600487': '亨通光电', '600522': '中天科技',
        '002792': '通宇通讯', '300570': '太辰光', '002463': '沪电股份',
        '300353': '东土科技',
    },
    '服务器/IDC': {
        '000977': '浪潮信息', '601138': '工业富联', '603019': '中科曙光',
        '000938': '紫光股份', '603881': '数据港', '600845': '宝信软件',
        '300017': '网宿科技', '300383': '光环新网', '688345': '优刻得',
        '000063': '中兴通讯',
    },
    '人工智能': {
        '002230': '科大讯飞', '300418': '昆仑万维', '300364': '中文在线',
        '002415': '海康威视', '002236': '大华股份', '688111': '海天瑞声',
    },
    '半导体/芯片': {
        '688981': '中芯国际', '688256': '寒武纪', '603501': '韦尔股份',
        '300474': '景嘉微', '002049': '紫光国微', '603986': '兆易创新',
        '002371': '北方华创', '688012': '中微公司', '002916': '深南电路',
        '688041': '海光信息',
    },
}

# ===================== 14个新浪概念 =====================
SINA_CONCEPTS = {
    'gn_hwgn': '华为概念', 'gn_5Ggn': '5G概念', 'gn_gf': '光伏',
    'gn_xny': '新能源', 'gn_gfjg': '国防军工', 'gn_gcrj': '国产软件',
    'gn_cxy': '创新药', 'gn_cdz': '充电桩', 'gn_znjq': '智能机器',
    'gn_wxdh': '卫星导航', 'gn_zjtx': '专精特新', 'gn_yjys': '业绩预升',
    'gn_dtjj': '低碳经济', 'gn_qcdz': '汽车电子',
}

MAX_STOCKS_PER_CONCEPT = 15  # 每概念最多取15只

# ===================== 腾讯API =====================
def get_prefix(code):
    if code.startswith('6') or code.startswith('5') or code.startswith('11'):
        return 'sh'
    elif code.startswith('8') or code.startswith('4'):
        return 'bj'
    else:
        return 'sz'

def fetch_kline(code, count=50):
    """腾讯API拉取日K线(前复权)"""
    prefix = get_prefix(code)
    symbol = f"{prefix}{code}"
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

def compute_stock_metrics(code):
    """计算个股: 周涨幅/两周涨幅/MA40线上/MA20线上"""
    df = fetch_kline(code, 50)
    if df is None or len(df) < 41:
        return None
    close = df['close'].values
    n = len(close)
    latest = close[-1]
    weekly = (latest / close[-6] - 1) * 100 if n >= 6 else 0
    biweekly = (latest / close[-11] - 1) * 100 if n >= 11 else 0
    ma40 = np.mean(close[-40:]) if n >= 40 else 0
    above_ma40 = latest > ma40 if ma40 > 0 else False
    ma20 = np.mean(close[-20:]) if n >= 20 else 0
    above_ma20 = latest > ma20 if ma20 > 0 else False
    return {
        'code': code, 'weekly': round(weekly, 2), 'biweekly': round(biweekly, 2),
        'above_ma40': above_ma40, 'above_ma20': above_ma20,
        'last_date': df.iloc[-1]['date'], 'close': round(latest, 2),
    }

# ===================== 新浪概念API =====================
def get_sina_concept_stocks(node, max_n=15):
    """获取新浪概念板块成分股(按市值排序取前N只)"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {'page': 1, 'num': max_n, 'node': node, 'sort': 'nmc', 'asc': 0}
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        data = json.loads(resp.text)
        return {s.get('code', ''): s.get('name', '') for s in data if s.get('code')}
    except:
        return {}

# ===================== 主流程 =====================
def main():
    print("=" * 120, file=sys.stderr)
    print("20概念板块综合轮动评价 (周涨幅 + MA40趋势 + 上涨比例 -> 强弱评价)", file=sys.stderr)
    print("=" * 120, file=sys.stderr)

    # Step 1: 构建20个概念的成分股
    print("[1/3] 构建20个概念成分股...", file=sys.stderr)
    all_concepts = {}  # {concept_name: {code: name}}
    all_stocks = {}   # {code: name} 全局去重

    # 手工概念
    for cname, stocks in MANUAL_CONCEPTS.items():
        all_concepts[cname] = dict(stocks)
        all_stocks.update(stocks)
        print(f"  {cname}: {len(stocks)}只", file=sys.stderr)

    # 新浪概念
    for node, cname in SINA_CONCEPTS.items():
        stocks = get_sina_concept_stocks(node, MAX_STOCKS_PER_CONCEPT)
        if stocks:
            all_concepts[cname] = stocks
            all_stocks.update(stocks)
            print(f"  {cname}: {len(stocks)}只", file=sys.stderr)
        else:
            print(f"  {cname}: 获取失败!", file=sys.stderr)
        time.sleep(0.15)

    print(f"\n  共 {len(all_concepts)} 个概念, {len(all_stocks)} 只去重个股", file=sys.stderr)

    # Step 2: 拉K线算指标
    print(f"[2/3] 并发拉取K线({len(all_stocks)}只)...", file=sys.stderr)
    stock_metrics = {}
    fail = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(compute_stock_metrics, code): code for code in all_stocks}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  K线进度: {done}/{len(all_stocks)} (成功{len(stock_metrics)} 失败{fail})", file=sys.stderr)
            r = f.result()
            if r:
                stock_metrics[r['code']] = r
            else:
                fail += 1

    dates = [r['last_date'] for r in stock_metrics.values() if r.get('last_date')]
    latest_date = max(dates) if dates else 'N/A'
    print(f"  成功: {len(stock_metrics)}/{len(all_stocks)}, 数据截止: {latest_date}", file=sys.stderr)

    # Step 3: 计算概念级指标
    print("[3/3] 计算概念级指标并排名...", file=sys.stderr)
    concept_results = []
    for cname, stocks in all_concepts.items():
        codes = list(stocks.keys())
        metrics = [stock_metrics[c] for c in codes if c in stock_metrics]
        if not metrics:
            continue

        weeklys = [m['weekly'] for m in metrics]
        biweeklys = [m['biweekly'] for m in metrics]
        above_ma40 = sum(1 for m in metrics if m['above_ma40'])
        above_ma20 = sum(1 for m in metrics if m['above_ma20'])
        positive = sum(1 for w in weeklys if w > 0)

        avg_weekly = np.mean(weeklys)
        avg_biweekly = np.mean(biweeklys)
        ma40_ratio = above_ma40 / len(metrics) * 100
        ma20_ratio = above_ma20 / len(metrics) * 100
        positive_ratio = positive / len(metrics) * 100

        # 综合评分: 周涨幅40% + MA40线上比例30% + 上涨比例30%
        score = avg_weekly * 0.4 + ma40_ratio * 0.3 + positive_ratio * 0.3

        # 强弱评价
        if avg_weekly > 3 and ma40_ratio >= 60:
            eval_label = "强势"
        elif avg_weekly > 1 and ma40_ratio >= 50:
            eval_label = "偏强"
        elif avg_weekly > -1 and ma40_ratio >= 40:
            eval_label = "中性"
        elif avg_weekly > -3 and ma40_ratio >= 30:
            eval_label = "偏弱"
        else:
            eval_label = "弱势"

        # 最强/最弱个股
        sorted_stocks = sorted(metrics, key=lambda x: x['weekly'], reverse=True)
        best = sorted_stocks[0]
        worst = sorted_stocks[-1]

        concept_results.append({
            'name': cname,
            'count': len(metrics),
            'avg_weekly': round(avg_weekly, 2),
            'avg_biweekly': round(avg_biweekly, 2),
            'ma40_ratio': round(ma40_ratio, 1),
            'ma20_ratio': round(ma20_ratio, 1),
            'positive_ratio': round(positive_ratio, 1),
            'score': round(score, 2),
            'eval': eval_label,
            'best_stock': f"{all_stocks.get(best['code'], best['code'])}({best['weekly']:.1f}%)",
            'worst_stock': f"{all_stocks.get(worst['code'], worst['code'])}({worst['weekly']:.1f}%)",
        })

    # 按综合评分排序
    concept_results.sort(key=lambda x: x['score'], reverse=True)

    # 输出
    print("\n" + "=" * 130)
    print(f"20概念板块综合轮动评价 (数据截止: {latest_date})")
    print("=" * 130)
    print(f"{'排名':<5} {'概念名称':<14} {'个股':<5} {'周涨幅%':<8} {'两周%':<7} {'MA40线上%':<9} {'MA20线上%':<9} {'上涨%':<7} {'评分':<7} {'评价':<5} {'最强个股':<20} {'最弱个股'}")
    print("-" * 130)

    eval_colors = {'强势': '★', '偏强': '▲', '中性': '─', '偏弱': '▼', '弱势': '✕'}

    for i, r in enumerate(concept_results):
        marker = eval_colors.get(r['eval'], '')
        print(f"{i+1:<5} {r['name']:<14} {r['count']:<5} {r['avg_weekly']:<8.2f} {r['avg_biweekly']:<7.2f} "
              f"{r['ma40_ratio']:<9.1f} {r['ma20_ratio']:<9.1f} {r['positive_ratio']:<7.1f} "
              f"{r['score']:<7.2f} {r['eval']:<5}{marker} {r['best_stock']:<20} {r['worst_stock']}")

    # 汇总
    print(f"\n{'='*130}")
    print("概念板块强弱分布:")
    for label in ['强势', '偏强', '中性', '偏弱', '弱势']:
        names = [r['name'] for r in concept_results if r['eval'] == label]
        if names:
            print(f"  {label}: {', '.join(names)}")
    print(f"\n{'='*130}")
    print("评分规则: 综合评分 = 周涨幅×0.4 + MA40线上比例×0.3 + 上涨比例×0.3")
    print("评价标准: 强势(周>3%且MA60%+) | 偏强(周>1%且MA50%+) | 中性(周-1%~1%) | 偏弱(周-3%~-1%) | 弱势(周<-3%且MA<30%)")
    print("=" * 130)

if __name__ == '__main__':
    main()
