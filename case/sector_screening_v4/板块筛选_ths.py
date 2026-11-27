#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块筛选 - 同花顺数据源版本
由于东方财富API临时不可用，改用同花顺数据源
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()

import os
os.environ['TQDM_DISABLE'] = '1'

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import random
import warnings
warnings.filterwarnings('ignore')

def get_kline(symbol, board_type='industry'):
    """获取板块K线数据"""
    try:
        if board_type == 'industry':
            df = ak.stock_board_industry_index_ths(symbol=symbol, start_date='20250101', end_date='20260703')
        else:
            df = ak.stock_board_concept_index_ths(symbol=symbol, start_date='20250101', end_date='20260703')
        
        if df is None or len(df) < 80:
            return None
        
        df = df.sort_values('日期').reset_index(drop=True)
        df['收盘价'] = df['收盘价'].astype(float)
        return df
    except Exception as e:
        return None

def calc_ma(prices, period):
    """计算移动均线"""
    if len(prices) < period:
        return None
    return np.mean(prices[-period:])

def check_ma_conditions(df):
    """检查均线条件"""
    closes = df['收盘价'].values
    
    # 计算MA40和MA80
    if len(closes) < 81:
        return False, None
    
    ma40 = calc_ma(closes, 40)
    ma40_prev = calc_ma(closes[:-1], 40)
    ma80 = calc_ma(closes, 80)
    ma80_prev = calc_ma(closes[:-1], 80)
    
    if ma40 is None or ma80 is None or ma40_prev is None or ma80_prev is None:
        return False, None
    
    current_price = closes[-1]
    
    # 条件：收盘>MA40, MA40向上, MA80向上
    condition1 = current_price > ma40
    condition2 = ma40 > ma40_prev
    condition3 = ma80 > ma80_prev
    
    # 近5日涨幅
    if len(closes) >= 6:
        pct_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
    else:
        pct_5d = 0
    
    condition4 = pct_5d > 2  # 近5日涨幅>2%
    
    passed = condition1 and condition2 and condition3 and condition4
    
    info = {
        'price': current_price,
        'ma40': ma40,
        'ma80': ma80,
        'ma40_up': condition2,
        'ma80_up': condition3,
        'pct_5d': pct_5d,
    }
    
    return passed, info

def get_fund_flow_dict():
    """获取所有板块的当日资金流"""
    flow_dict = {}
    try:
        # 行业资金流
        df_ind = ak.stock_fund_flow_industry()
        for _, row in df_ind.iterrows():
            name = row['行业']
            flow_dict[name] = {
                'net_flow': row['净额'],  # 亿元
                'pct_change': row['行业-涨跌幅'],
            }
    except Exception as e:
        print(f"行业资金流获取失败: {e}")
    
    try:
        # 概念资金流
        df_con = ak.stock_fund_flow_concept()
        for _, row in df_con.iterrows():
            name = row['行业']  # 列名也是"行业"
            flow_dict[name] = {
                'net_flow': row['净额'],  # 亿元
                'pct_change': row['行业-涨跌幅'],
            }
    except Exception as e:
        print(f"概念资金流获取失败: {e}")
    
    return flow_dict

def main():
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"=== 板块筛选 (同花顺数据源) {today} ===")
    print()
    
    # 1. 获取板块列表
    print("[1] 获取板块列表...")
    industry_df = ak.stock_board_industry_name_ths()
    concept_df = ak.stock_board_concept_name_ths()
    
    industry_list = industry_df['name'].tolist()
    concept_list = concept_df['name'].tolist()
    
    print(f"  行业: {len(industry_list)}, 概念: {len(concept_list)}")
    print(f"  总计: {len(industry_list) + len(concept_list)} 个板块")
    print()
    
    # 2. 获取资金流数据
    print("[2] 获取当日资金流数据...")
    flow_dict = get_fund_flow_dict()
    print(f"  获取到 {len(flow_dict)} 个板块的资金流数据")
    print()
    
    # 3. 逐个筛选
    print("[3] 开始筛选...")
    passed_boards = []
    failed_count = 0
    
    all_boards = [('industry', name) for name in industry_list] + [('concept', name) for name in concept_list]
    total = len(all_boards)
    
    for i, (board_type, name) in enumerate(all_boards):
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{total} | 通过: {len(passed_boards)}")
        
        df = get_kline(name, board_type)
        if df is None:
            failed_count += 1
            time.sleep(random.uniform(0.1, 0.3))
            continue
        
        passed, info = check_ma_conditions(df)
        
        if passed:
            # 获取资金流信息
            flow_info = flow_dict.get(name, {})
            net_flow = flow_info.get('net_flow', 0)
            pct_change = flow_info.get('pct_change', 0)
            
            passed_boards.append({
                'name': name,
                'type': '行业' if board_type == 'industry' else '概念',
                'price': info['price'],
                'ma40': info['ma40'],
                'ma80': info['ma80'],
                'pct_5d': info['pct_5d'],
                'net_flow': net_flow,
                'pct_change': pct_change,
            })
        
        time.sleep(random.uniform(0.1, 0.3))
    
    print(f"  完成: {total}/{total} | 通过: {len(passed_boards)} | 失败: {failed_count}")
    print()
    
    # 4. 按资金净流入排序
    print("[4] 生成报告...")
    passed_boards.sort(key=lambda x: x.get('net_flow', 0), reverse=True)
    
    # 5. 输出结果
    report_lines = []
    report_lines.append(f"# 📊 板块筛选日报 {today}")
    report_lines.append("")
    report_lines.append(f"> 数据源：同花顺 | 筛选条件：收盘>MA40 且 MA40向上 且 MA80向上 且 近5日涨幅>2%")
    report_lines.append("")
    report_lines.append(f"**共筛选 {total} 个板块，通过 {len(passed_boards)} 个**")
    report_lines.append("")
    
    if passed_boards:
        report_lines.append("## ✅ 通过筛选的板块")
        report_lines.append("")
        report_lines.append("| 序号 | 板块 | 类型 | 最新价 | MA40 | MA80 | 5日涨幅 | 当日净额(亿) | 当日涨幅 |")
        report_lines.append("|------|------|------|--------|------|------|---------|------------|----------|")
        
        for i, board in enumerate(passed_boards, 1):
            report_lines.append(
                f"| {i} | {board['name']} | {board['type']} | {board['price']:.2f} | "
                f"{board['ma40']:.2f} | {board['ma80']:.2f} | "
                f"{board['pct_5d']:.2f}% | {board['net_flow']:.2f} | "
                f"{board['pct_change']:.2f}% |"
            )
    else:
        report_lines.append("## ❌ 今日无满足条件的板块")
        report_lines.append("")
        report_lines.append("当前市场环境下，没有板块同时满足所有均线条件。")
    
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("*注：由于东方财富API临时不可用，今日改用同花顺数据源。资金流为当日数据而非3日累计。*")
    
    report = '\n'.join(report_lines)
    print(report)
    
    # 保存文件
    filename = _cp(f'/app/data/所有对话/主对话/板块筛选_ths_{today}.md')
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存: {filename}")
    
    return report

if __name__ == '__main__':
    main()
