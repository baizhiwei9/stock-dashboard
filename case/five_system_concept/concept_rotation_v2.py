#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念板块周涨幅排名 + 龙头股筛选 (akshare版)
1. 获取全部概念板块, 拉取日K线计算近5交易日(一周)涨幅
2. 按周涨幅排名Top30
3. 对Top10概念, 拉成分股展示涨幅领先的个股
"""
import akshare as ak
import pandas as pd
import numpy as np
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_all_concepts():
    """获取全部概念板块列表"""
    df = ak.stock_board_concept_name_em()
    return df

def fetch_concept_hist(name):
    """获取概念板块日K线, 计算周涨幅"""
    for attempt in range(3):
        try:
            df = ak.stock_board_concept_hist_em(symbol=name, period="日", adjust="")
            if df is None or len(df) < 6:
                return None
            close = df['收盘'].values
            latest = close[-1]
            week_ago = close[-6]
            weekly = (latest / week_ago - 1) * 100
            biweekly = (latest / close[-11] - 1) * 100 if len(close) >= 11 else 0
            last_date = df.iloc[-1]['日期'] if '日期' in df.columns else ''
            return {
                'name': name,
                'weekly': round(weekly, 2),
                'biweekly': round(biweekly, 2),
                'latest': round(latest, 2),
                'last_date': str(last_date),
            }
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(0.5)
    return None

def get_concept_stocks(name):
    """获取概念板块成分股"""
    try:
        df = ak.stock_board_concept_cons_em(symbol=name)
        if df is None or len(df) == 0:
            return []
        return df
    except:
        return []

def fetch_stock_weekly(row):
    """获取个股周涨幅"""
    code = str(row.get('代码', ''))
    name = str(row.get('名称', ''))
    for attempt in range(2):
        try:
            # 判断市场
            if code.startswith('6') or code.startswith('5'):
                secid = f"1.{code}"
            else:
                secid = f"0.{code}"
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20260820", end_date="20260904", adjust="qfq")
            if df is None or len(df) < 6:
                return {'code': code, 'name': name, 'weekly': None}
            close = df['收盘'].values
            weekly = (close[-1] / close[-6] - 1) * 100
            daily = (close[-1] / close[-2] - 1) * 100 if len(close) >= 2 else 0
            return {'code': code, 'name': name, 'weekly': round(weekly, 2), 'daily': round(daily, 2)}
        except:
            time.sleep(0.3)
    return {'code': code, 'name': name, 'weekly': None}

def main():
    # === Step 1: 获取全部概念板块 ===
    print("[1/4] 获取概念板块列表...", file=sys.stderr)
    concepts_df = get_all_concepts()
    print(f"  共 {len(concepts_df)} 个概念板块", file=sys.stderr)

    # 取每日涨跌幅作为参考, 先按今日涨幅排序取前100(减少K线拉取量)
    if '涨跌幅' in concepts_df.columns:
        concepts_df['涨跌幅'] = pd.to_numeric(concepts_df['涨跌幅'], errors='coerce')
        # 取涨幅前80 + 跌幅前20(确保覆盖所有可能的热门概念)
        top_daily = concepts_df.nlargest(80, '涨跌幅')
        bot_daily = concepts_df.nsmallest(20, '涨跌幅')
        subset = pd.concat([top_daily, bot_daily]).drop_duplicates(subset=['板块名称'])
    else:
        subset = concepts_df.head(100)

    names = subset['板块名称'].tolist()
    print(f"  筛选 {len(names)} 个概念拉取K线...", file=sys.stderr)

    # === Step 2: 并发拉取K线, 计算周涨幅 ===
    print("[2/4] 拉取概念K线计算周涨幅...", file=sys.stderr)
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_concept_hist, n): n for n in names}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 20 == 0:
                print(f"  进度: {done}/{len(names)}", file=sys.stderr)
            r = f.result()
            if r:
                # 从原df拿今日涨幅
                row = subset[subset['板块名称'] == r['name']]
                daily_val = row['涨跌幅'].values[0] if '涨跌幅' in row.columns and len(row) > 0 else 0
                r['daily'] = round(float(daily_val), 2) if not pd.isna(daily_val) else 0
                results.append(r)

    results.sort(key=lambda x: x['weekly'], reverse=True)

    dates = [r['last_date'] for r in results if r.get('last_date') and r['last_date'] != 'nan']
    latest_date = max(dates) if dates else 'N/A'

    # === 输出: 概念周涨幅Top30 ===
    print("\n" + "=" * 100)
    print(f"概念板块周涨幅排名Top30 (近5交易日)  数据截止: {latest_date}")
    print("=" * 100)
    print(f"{'排名':<5} {'概念名称':<20} {'周涨幅%':<10} {'两周涨幅%':<12} {'今日涨幅%':<10}")
    print("-" * 100)
    kw_list = ['液冷','散热','服务器','算力','光模块','CPO','铜缆','交换机','光通信','芯片','AI','GPU','数据中心','IDC','ICT']
    for i, r in enumerate(results[:30]):
        marker = " ★" if any(kw in r['name'] for kw in kw_list) else ""
        print(f"{i+1:<5} {r['name']:<20} {r['weekly']:<10.2f} {r['biweekly']:<12.2f} {r['daily']:<10.2f}{marker}")

    # === Step 3: Top10概念成分股 ===
    print(f"\n{'='*100}")
    print("[3/4] Top10概念板块成分股 (按周涨幅排序, 展示前6)")
    print("=" * 100)

    for r in results[:10]:
        name = r['name']
        stocks_df = get_concept_stocks(name)
        if stocks_df is None or len(stocks_df) == 0:
            continue

        # 并发拉成分股周涨幅(最多取前20只)
        stock_list = stocks_df.head(20).to_dict('records')
        stock_results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_stock_weekly, s): s for s in stock_list}
            for f in as_completed(futures):
                res = f.result()
                if res and res.get('weekly') is not None:
                    stock_results.append(res)

        stock_results.sort(key=lambda x: x.get('weekly', -999), reverse=True)

        print(f"\n{'─'*90}")
        print(f"  [{name}]  周涨幅 {r['weekly']:.2f}%  两周 {r['biweekly']:.2f}%  今日 {r['daily']:.2f}%")
        print(f"{'─'*90}")
        print(f"  {'代码':<8} {'名称':<14} {'周涨幅%':<10} {'今日涨幅%':<10}")
        for sr in stock_results[:6]:
            print(f"  {sr['code']:<8} {sr['name']:<14} {sr['weekly']:<10.2f} {sr.get('daily',0):<10.2f}")
        time.sleep(0.3)

    # === Step 4: 液冷/算力相关概念定位 ===
    print(f"\n{'='*100}")
    print("[4/4] 液冷/算力/服务器相关概念在全部排名中的位置:")
    print("-" * 100)
    found = False
    for i, r in enumerate(results):
        for kw in kw_list:
            if kw in r['name']:
                print(f"  排名{i+1:>3}: {r['name']:<20} 周涨幅 {r['weekly']:>7.2f}%  今日 {r['daily']:>7.2f}%  两周 {r['biweekly']:>7.2f}%")
                found = True
                break
    if not found:
        print("  未在已拉取的概念中找到液冷/算力相关概念(可能名称不同或未进入今日涨跌幅前100)")

    print(f"\n{'='*100}")

if __name__ == '__main__':
    main()
