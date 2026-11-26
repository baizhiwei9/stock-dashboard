#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点概念板块追踪器
==================
功能：
1. 计算各概念板块一周涨幅
2. 识别龙头股（市值最大）
3. 计算龙头股当年涨幅
4. 输出TOP热点概念

数据源：tushare（稳定）
概念成分股：手动维护（来自东方财富）
"""

import tushare as ts
import pandas as pd
import os
import time
from datetime import datetime, timedelta
import json

# 清除代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ.pop(k)

# Tushare Token
TUSHARE_TOKEN = 'a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# 概念成分股库（手动维护，来自东方财富）
CONCEPT_STOCKS = {
    "CPO/光模块": {
        "leader": "300308.SZ",  # 中际旭创
        "stocks": [
            "300308.SZ",  # 中际旭创 - 龙头
            "300502.SZ",  # 新易盛
            "300394.SZ",  # 天孚通信
            "002281.SZ",  # 光迅科技
            "000988.SZ",  # 华工科技
            "600487.SH",  # 亨通光电
            "600522.SH",  # 中天科技
            "603618.SH",  # 杭电股份
            "688313.SH",  # 仕佳光子
            "300620.SZ",  # 光库科技
            "300570.SZ",  # 太辰光
            "600105.SH",  # 永鼎股份
            "002491.SZ",  # 通鼎互联
            "688498.SH",  # 源杰科技
            "688048.SH",  # 长光华芯
            "301205.SZ",  # 联特科技
            "603083.SH",  # 剑桥科技
            "002384.SZ",  # 东山精密
            "601869.SH",  # 长飞光纤
        ]
    },
    "光芯片": {
        "leader": "688313.SH",  # 仕佳光子
        "stocks": [
            "688313.SH",  # 仕佳光子
            "688048.SH",  # 长光华芯
            "688498.SH",  # 源杰科技
            "002079.SZ",  # 苏州固锝
        ]
    },
    "AI算力": {
        "leader": "300308.SZ",  # 中际旭创（算力硬件龙头）
        "stocks": [
            "300308.SZ",  # 中际旭创
            "300502.SZ",  # 新易盛
            "601138.SH",  # 工业富联
            "000063.SZ",  # 中兴通讯
            "002475.SZ",  # 立讯精密
        ]
    },
    "半导体": {
        "leader": "688981.SH",  # 中芯国际
        "stocks": [
            "688981.SH",  # 中芯国际
            "002371.SZ",  # 北方华创
            "688012.SH",  # 中微公司
            "603501.SH",  # 韦尔股份
            "002049.SZ",  # 紫光国微
        ]
    },
    "存储芯片": {
        "leader": "688525.SH",  # 佰维存储
        "stocks": [
            "688525.SH",  # 佰维存储
            "688041.SH",  # 海光信息
            "603986.SH",  # 兆易创新
        ]
    },
    "人形机器人": {
        "leader": "002747.SZ",  # 埃斯顿
        "stocks": [
            "002747.SZ",  # 埃斯顿
            "688169.SH",  # 石头科技
            "300024.SZ",  # 机器人
            "002611.SZ",  # 东方精工
        ]
    },
    "低空经济": {
        "leader": "000333.SZ",  # 万丰奥威
        "stocks": [
            "000333.SZ",  # 万丰奥威
            "002085.SZ",  # 万丰奥威
            "600433.SH",  # 冠豪高新
        ]
    },
    "AI应用": {
        "leader": "002230.SZ",  # 科大讯飞
        "stocks": [
            "002230.SZ",  # 科大讯飞
            "300033.SZ",  # 同花顺
            "688787.SH",  # 海天瑞声
        ]
    },
    "创新药": {
        "leader": "688180.SH",  # 君实生物
        "stocks": [
            "688180.SH",  # 君实生物
            "688235.SH",  # 百济神州
            "603259.SH",  # 药明康德
            "300347.SZ",  # 泰格医药
        ]
    },
    "消费电子": {
        "leader": "002475.SZ",  # 立讯精密
        "stocks": [
            "002475.SZ",  # 立讯精密
            "002241.SZ",  # 歌尔股份
            "600745.SH",  # 闻泰科技
        ]
    },
}

def get_stock_name(ts_code):
    """获取股票名称"""
    try:
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name')
        if len(df) > 0:
            return df.iloc[0]['name']
    except:
        pass
    return ts_code

def calc_week_chg(ts_code):
    """计算一周涨幅"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=week_ago, end_date=today)
        if len(df) >= 2:
            latest = df.iloc[0]['close']
            oldest = df.iloc[-1]['close']
            return round((latest - oldest) / oldest * 100, 2)
    except:
        pass
    return None

def calc_year_chg(ts_code):
    """计算当年涨幅"""
    try:
        year_start = f"{datetime.now().year}0105"
        today = datetime.now().strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=year_start, end_date=today)
        if len(df) >= 2:
            latest = df.iloc[0]['close']
            oldest = df.iloc[-1]['close']
            return round((latest - oldest) / oldest * 100, 2)
    except:
        pass
    return None

def get_market_cap(ts_code):
    """获取市值"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        df = pro.daily_basic(ts_code=ts_code, trade_date=today, fields='ts_code,total_mv')
        if len(df) > 0:
            return df.iloc[0]['total_mv']
    except:
        pass
    return None

def track_concepts():
    """追踪所有概念板块"""
    print("=" * 70)
    print("热点概念板块追踪")
    print("=" * 70)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    
    for concept_name, concept_data in CONCEPT_STOCKS.items():
        print(f"\n正在计算【{concept_name}】...")
        
        stocks = concept_data['stocks']
        leader_code = concept_data['leader']
        
        # 计算概念平均涨幅
        week_chgs = []
        for i, code in enumerate(stocks):
            chg = calc_week_chg(code)
            if chg is not None:
                week_chgs.append(chg)
            time.sleep(0.12)
        
        if week_chgs:
            avg_week_chg = round(sum(week_chgs) / len(week_chgs), 2)
            up_count = sum(1 for c in week_chgs if c > 0)
            
            # 计算龙头股当年涨幅
            leader_name = get_stock_name(leader_code)
            time.sleep(0.12)
            leader_year_chg = calc_year_chg(leader_code)
            time.sleep(0.12)
            leader_cap = get_market_cap(leader_code)
            time.sleep(0.12)
            
            results.append({
                'concept': concept_name,
                'week_chg': avg_week_chg,
                'stock_count': len(week_chgs),
                'up_count': up_count,
                'leader_code': leader_code,
                'leader_name': leader_name,
                'leader_year_chg': leader_year_chg,
                'leader_cap': leader_cap,
            })
            
            print(f"  一周涨幅: {avg_week_chg}% ({up_count}/{len(week_chgs)}上涨)")
            print(f"  龙头股: {leader_name} 当年涨幅: {leader_year_chg}%")
    
    # 按一周涨幅排序
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('week_chg', ascending=False)
    
    print("\n" + "=" * 70)
    print("热点概念TOP10（按一周涨幅排序）")
    print("=" * 70)
    
    top10 = results_df.head(10)
    for i, row in top10.iterrows():
        print(f"\n{row['concept']}")
        print(f"  一周涨幅: {row['week_chg']}%")
        print(f"  龙头股: {row['leader_name']}({row['leader_code']})")
        print(f"  龙头市值: {row['leader_cap']:.0f}亿" if row['leader_cap'] else "")
        print(f"  龙头当年涨幅: {row['leader_year_chg']}%")
    
    return results_df

def save_report(results_df):
    """保存报告"""
    output_dir = '热点概念追踪/outputs'
    os.makedirs(output_dir, exist_ok=True)
    
    date_str = datetime.now().strftime('%Y-%m-%d')
    
    # 保存CSV
    csv_path = f"{output_dir}/{date_str}_热点概念.csv"
    results_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n✓ 已保存: {csv_path}")
    
    # 保存Markdown报告
    md_path = f"{output_dir}/{date_str}_热点概念报告.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# A股热点概念板块周报 - {date_str}\n\n")
        f.write("## 概念涨幅TOP10\n\n")
        f.write("| 排名 | 概念 | 一周涨幅 | 上涨比例 | 龙头股 | 龙头当年涨幅 |\n")
        f.write("|:---:|:---|---:|---:|:---|---:|\n")
        
        for rank, (_, row) in enumerate(results_df.head(10).iterrows(), 1):
            up_ratio = f"{row['up_count']}/{row['stock_count']}"
            leader = f"{row['leader_name']}({row['leader_code']})"
            f.write(f"| {rank} | {row['concept']} | {row['week_chg']}% | {up_ratio} | {leader} | {row['leader_year_chg']}% |\n")
        
        f.write("\n---\n⚠️ 案例分析学习材料，不构成投资建议\n")
    
    print(f"✓ 已保存: {md_path}")
    
    return csv_path, md_path

if __name__ == '__main__':
    results = track_concepts()
    save_report(results)
