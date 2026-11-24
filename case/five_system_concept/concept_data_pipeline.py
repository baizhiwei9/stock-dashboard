#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念级指数数据管道
拉取20个概念板块成分股日K线 -> 等权合成概念级收盘指数 -> 计算日收益率矩阵
输出: concept_returns.pkl (DataFrame: 日期×20概念 收益率)
      concept_close.pkl   (DataFrame: 日期×20概念 收盘指数)
"""
import requests
import pandas as pd
import numpy as np
import json
import re
import sys
import time
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
})

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

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
    '光模块CPO': {
        '300308': '中际旭创', '300502': '新易盛', '300394': '天孚通信',
        '002281': '光迅科技', '600487': '亨通光电', '600522': '中天科技',
        '002792': '通宇通讯', '300570': '太辰光', '002463': '沪电股份',
        '300353': '东土科技',
    },
    '服务器IDC': {
        '000977': '浪潮信息', '601138': '工业富联', '603019': '中科曙光',
        '000938': '紫光股份', '603881': '数据港', '600845': '宝信软件',
        '300017': '网宿科技', '300383': '光环新网', '688345': '优刻得',
        '000063': '中兴通讯',
    },
    '人工智能': {
        '002230': '科大讯飞', '300418': '昆仑万维', '300364': '中文在线',
        '002415': '海康威视', '002236': '大华股份', '688111': '海天瑞声',
        '300369': '绿盟科技', '300295': '三六五网', '300133': '华策影视',
        '002413': '雷柏科技',
    },
    '半导体芯片': {
        '688981': '中芯国际', '688256': '寒武纪', '603501': '韦尔股份',
        '300474': '景嘉微', '002049': '紫光国微', '603986': '兆易创新',
        '002371': '北方华创', '688012': '中微公司', '002916': '深南电路',
        '688041': '海光信息', '300666': '江丰电子', '002407': '多氟多',
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

MAX_STOCKS_PER_CONCEPT = 15

# ===================== 腾讯API =====================
def get_prefix(code):
    if code.startswith('6') or code.startswith('5') or code.startswith('11'):
        return 'sh'
    elif code.startswith('8') or code.startswith('4'):
        return 'bj'
    return 'sz'

def fetch_kline(code, count=60):
    """腾讯API拉取日K线(前复权)"""
    prefix = get_prefix(code)
    symbol = f"{prefix}{code}"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f"{symbol},day,,,{count},qfq"}
    try:
        resp = SESSION.get(url, params=params, timeout=12)
        data = resp.json()
        kline = data.get('data', {}).get(symbol, {})
        bars = kline.get('qfqday') or kline.get('day')
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=['date','open','close','high','low','volume'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        return df[['close']]
    except Exception:
        return None

# ===================== 新浪概念API =====================
def get_sina_concept_stocks(node, max_n=15):
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
    print("=" * 100, file=sys.stderr)
    print("概念级指数数据管道: 20概念 -> 成分股K线 -> 等权合成 -> 收益率矩阵", file=sys.stderr)
    print("=" * 100, file=sys.stderr)

    # Step 1: 构建20个概念成分股
    print("[1/4] 构建20个概念成分股...", file=sys.stderr)
    all_concepts = {}
    all_stock_names = {}

    for cname, stocks in MANUAL_CONCEPTS.items():
        all_concepts[cname] = dict(stocks)
        all_stock_names.update(stocks)
        print(f"  {cname}: {len(stocks)}只", file=sys.stderr)

    for node, cname in SINA_CONCEPTS.items():
        stocks = get_sina_concept_stocks(node, MAX_STOCKS_PER_CONCEPT)
        if stocks:
            all_concepts[cname] = stocks
            all_stock_names.update(stocks)
            print(f"  {cname}: {len(stocks)}只", file=sys.stderr)
        else:
            print(f"  {cname}: 获取失败!", file=sys.stderr)
        time.sleep(0.15)

    all_codes = list(all_stock_names.keys())
    print(f"\n  共 {len(all_concepts)} 个概念, {len(all_codes)} 只去重个股", file=sys.stderr)

    # Step 2: 并发拉取全部个股K线
    print(f"[2/4] 并发拉取K线({len(all_codes)}只, 60日, 4并发)...", file=sys.stderr)
    stock_close = {}  # {code: pd.Series(close, index=date)}
    fail = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_kline, code): code for code in all_codes}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  K线进度: {done}/{len(all_codes)} (成功{len(stock_close)} 失败{fail})", file=sys.stderr)
            code = futures[f]
            df = f.result()
            if df is not None and len(df) > 0:
                stock_close[code] = df['close']
            else:
                fail += 1

    print(f"  成功: {len(stock_close)}/{len(all_codes)}, 失败: {fail}", file=sys.stderr)

    # Step 3: 等权合成概念级收盘指数
    print("[3/4] 等权合成概念级收盘指数...", file=sys.stderr)
    concept_close = {}  # {concept_name: pd.Series}

    for cname, stocks in all_concepts.items():
        codes = list(stocks.keys())
        # 收集该概念所有成分股的收盘价
        stock_data = {}
        for code in codes:
            if code in stock_close:
                stock_data[code] = stock_close[code]

        if not stock_data:
            print(f"  [SKIP] {cname}: 无有效数据", file=sys.stderr)
            continue

        # 对齐到统一日期索引
        df_stocks = pd.DataFrame(stock_data)
        # 等权: 对每日有数据的股票取均值(等权)
        df_stocks = df_stocks.sort_index()
        # 归一化: 每只股票从首个有效日起始为1, 然后取截面均值
        df_norm = df_stocks.div(df_stocks.ffill().iloc[0])
        concept_idx = df_norm.mean(axis=1)  # 等权概念指数
        concept_close[cname] = concept_idx

        valid = len(stock_data)
        print(f"  {cname}: {valid}/{len(codes)}只有效, {len(concept_idx)}个交易日", file=sys.stderr)

    # 构建概念收盘价DataFrame
    df_close = pd.DataFrame(concept_close)
    df_close = df_close.sort_index()

    # 截取最近250个交易日
    df_close = df_close.tail(250)

    # Step 4: 计算日收益率矩阵
    print("[4/4] 计算日收益率矩阵...", file=sys.stderr)
    df_returns = df_close.pct_change().dropna(how='all')

    # 数据截止日
    last_date = df_close.index[-1]
    print(f"  数据截止: {last_date.strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"  概念数: {len(df_close.columns)}", file=sys.stderr)
    print(f"  交易日数: {len(df_close)}", file=sys.stderr)

    # 检查全NaN列
    nan_cols = df_returns.columns[df_returns.isna().all()].tolist()
    if nan_cols:
        print(f"  [WARN] 全NaN列: {nan_cols}", file=sys.stderr)
    else:
        print(f"  无全NaN列", file=sys.stderr)

    # 落盘
    returns_path = f'{OUT_DIR}/concept_returns.pkl'
    close_path = f'{OUT_DIR}/concept_close.pkl'
    df_returns.to_pickle(returns_path)
    df_close.to_pickle(close_path)

    # 输出摘要
    print(f"\n{'='*100}")
    print(f"概念级收益率矩阵已生成")
    print(f"  收益率: {returns_path} ({df_returns.shape[0]}日 x {df_returns.shape[1]}概念)")
    print(f"  收盘指数: {close_path} ({df_close.shape[0]}日 x {df_close.shape[1]}概念)")
    print(f"  数据截止: {last_date.strftime('%Y-%m-%d')}")
    print(f"\n  概念列表: {', '.join(df_returns.columns.tolist())}")
    print(f"\n{'='*100}")

    # 输出最近5日收益率预览
    print("\n最近5个交易日收益率预览:")
    recent = df_returns.tail(5)
    for date, row in recent.iterrows():
        print(f"\n  {date.strftime('%Y-%m-%d')}:")
        for col, val in row.sort_values(ascending=False).items():
            sign = '+' if val >= 0 else ''
            print(f"    {col:<14} {sign}{val*100:.2f}%")

    # 输出20日动量排名
    print(f"\n{'='*100}")
    print("20日动量排名 (概念级):")
    if len(df_close) >= 21:
        mom20 = (df_close.iloc[-1] / df_close.iloc[-21] - 1) * 100
        mom20 = mom20.sort_values(ascending=False)
        print(f"{'排名':<5} {'概念':<14} {'20日涨幅%':<10}")
        print("-" * 40)
        for i, (cname, val) in enumerate(mom20.items()):
            if pd.isna(val):
                continue
            print(f"{i+1:<5} {cname:<14} {val:<10.2f}")
    else:
        print("  数据不足21日, 无法计算20日动量")

    print(f"\n{'='*100}")

if __name__ == '__main__':
    main()
