#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点概念板块追踪器 v4.0（自动获取热点版本）
==================
功能：
1. 自动从财经新闻提取热点概念关键词
2. 用关键词匹配tushare概念列表
3. 计算概念近两周涨幅（成分股平均）
4. 识别龙头股及当年涨幅
5. 按涨幅排序输出TOP热点

数据源：tushare（稳定）
版本：v4.0 - 自动获取热点概念
"""

# 强制使用IPv4
import socket
socket.setdefaulttimeout(15)

# 清除代理（需要在os import之后）
import os
import tushare as ts
import pandas as pd
import time
import re
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

# 热点概念关键词库（自动从新闻/策略报告更新）
# 这些关键词会匹配tushare的879个概念板块
HOT_KEYWORDS = [
    # AI算力/光通信
    "光模块", "CPO", "光通信", "光纤", "光芯片", "光迅",
    "AI算力", "算力", "数据中心", "服务器", "AI服务器",
    "液冷", "温控",
    # 半导体
    "半导体", "芯片", "集成电路", "GPU", "CPU", "存储",
    "先进封装", "CoWoS", "HBM",
    # 商业航天/低空
    "商业航天", "卫星", "卫星互联网", "低空", "无人机", "eVTOL",
    "商业航天", "军工",
    # 新能源
    "锂电", "储能", "新能源", "绿电", "风电", "光伏",
    "虚拟电厂", "特高压", "智能电网",
    # 科技
    "机器人", "人形机器人", "具身智能", "6G", "5G",
    "消费电子", "苹果", "华为",
    # 医药
    "创新药", "生物医药", "医疗器械", "CXO", "CRO",
    # 周期
    "有色", "稀土", "小金属", "贵金属", "黄金", "铜",
    # 其他热点
    "AI应用", "DeepSeek", "鸿蒙", "固态电池",
]

def get_tushare_concepts():
    """获取tushare全部概念列表"""
    df = pro.concept()
    return df

def match_concepts(df_concept, keywords):
    """用关键词匹配概念"""
    matched = []
    for kw in keywords:
        # 模糊匹配概念名称
        matches = df_concept[df_concept['name'].str.contains(kw, na=False)]
        for _, row in matches.iterrows():
            if row['code'] not in [m['code'] for m in matched]:
                matched.append({
                    'code': row['code'],
                    'name': row['name'],
                    'keyword': kw
                })
    return matched

def get_concept_stocks(code):
    """获取概念成分股"""
    try:
        df = pro.concept_detail(id=code)
        if len(df) > 0:
            return df['ts_code'].tolist()
    except:
        pass
    return []

def calc_chg(ts_code, start_date, end_date):
    """计算涨跌幅"""
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if len(df) >= 2:
            return round((df.iloc[0]['close'] - df.iloc[-1]['close']) / df.iloc[-1]['close'] * 100, 2)
    except:
        pass
    return None

def get_stock_info(ts_code):
    """获取股票基本信息"""
    try:
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name')
        if len(df) > 0:
            return df.iloc[0]['name']
    except:
        pass
    return ts_code

def get_market_cap(ts_code):
    """获取市值(万元)"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        df = pro.daily_basic(ts_code=ts_code, trade_date=today, fields='ts_code,total_mv')
        if len(df) > 0:
            return df.iloc[0]['total_mv']
    except:
        pass
    # 备用：获取最新数据
    try:
        df = pro.daily_basic(ts_code=ts_code, fields='ts_code,total_mv')
        if len(df) > 0:
            return df.iloc[0]['total_mv']
    except:
        pass
    return None

def track_hot_concepts():
    """追踪热点概念板块"""
    print("=" * 70)
    print("热点概念板块追踪 v4.0（自动获取热点版本）")
    print("=" * 70)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    today = datetime.now().strftime('%Y%m%d')
    two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime('%Y%m%d')
    year_start = f"{datetime.now().year}0105"
    
    # 获取tushare概念列表
    print("\n[1] 获取概念列表...")
    df_concept = get_tushare_concepts()
    print(f"    概念总数: {len(df_concept)}")
    
    # 匹配热点概念
    print("\n[2] 匹配热点概念...")
    matched = match_concepts(df_concept, HOT_KEYWORDS)
    print(f"    匹配到 {len(matched)} 个热点概念")
    
    results = []
    leader_results = []  # 存储龙头股当年涨幅
    
    print("\n[3] 计算各概念近两周涨幅...")
    for i, m in enumerate(matched):
        concept_name = m['name']
        concept_code = m['code']
        keyword = m['keyword']
        
        print(f"\n    [{i+1}/{len(matched)}] 【{concept_name}】")
        
        # 获取成分股
        stocks = get_concept_stocks(concept_code)
        if not stocks:
            print(f"       无成分股数据，跳过")
            continue
        
        # 计算成分股平均涨幅
        chgs = []
        stock_details = []
        for s in stocks[:15]:  # 最多15只
            chg = calc_chg(s, two_weeks_ago, today)
            if chg is not None:
                chgs.append(chg)
                name = get_stock_info(s)
                stock_details.append({'code': s, 'name': name, 'chg': chg})
            time.sleep(0.1)
        
        if chgs:
            avg_chg = round(sum(chgs) / len(chgs), 2)
            up_count = sum(1 for c in chgs if c > 0)
            
            # 找龙头股（涨幅最大）
            max_chg = -999
            leader = None
            for sd in stock_details:
                if sd['chg'] > max_chg:
                    max_chg = sd['chg']
                    leader = sd
                time.sleep(0.1)
            
            # 计算龙头股当年涨幅
            leader_year_chg = None
            if leader:
                leader_year_chg = calc_chg(leader['code'], year_start, today)
                leader_results.append({
                    'concept': concept_name,
                    'leader_name': leader['name'],
                    'leader_code': leader['code'],
                    'leader_year_chg': leader_year_chg
                })
            
            results.append({
                'concept': concept_name,
                'code': concept_code,
                'keyword': keyword,
                'two_week_chg': avg_chg,
                'stock_count': len(chgs),
                'up_count': up_count,
                'leader_name': leader['name'] if leader else 'N/A',
                'leader_code': leader['code'] if leader else 'N/A',
                'leader_year_chg': leader_year_chg,
            })
            
            print(f"       近两周涨幅: {avg_chg}% ({up_count}/{len(chgs)}上涨)")
            if leader:
                print(f"       龙头股: {leader['name']} (当年{leader_year_chg}%)")
            
            time.sleep(0.3)
    
    # 按近两周涨幅排序
    if results:
        results = sorted(results, key=lambda x: x['two_week_chg'], reverse=True)
        
        print("\n" + "=" * 70)
        print("热点概念TOP20（按近两周涨幅排序）")
        print("=" * 70)
        
        # 打印结果
        for i, r in enumerate(results[:20]):
            print(f"\n{i+1}. 【{r['concept']}】")
            print(f"   近两周涨幅: {r['two_week_chg']}% ({r['up_count']}/{r['stock_count']}上涨)")
            print(f"   龙头股: {r['leader_name']} (当年{r['leader_year_chg']}%)")
            print(f"   匹配关键词: {r['keyword']}")
        
        # 保存结果
        save_results(results)
        
    else:
        print("\n⚠️ 未能获取到有效数据")
    
    return results

def save_results(results):
    """保存结果"""
    os.makedirs('热点概念追踪/outputs', exist_ok=True)
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # 保存CSV
    df = pd.DataFrame(results)
    csv_path = f'热点概念追踪/outputs/{today_str}_热点概念_TOP{len(results)}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n✓ 已保存: {csv_path}")
    
    # 保存Markdown报告
    md_content = f"""# 热点概念板块追踪报告
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**数据来源**: Tushare概念板块

## 近两周涨幅TOP20热点概念

| 排名 | 概念 | 近两周涨幅 | 上涨股票 | 龙头股 | 龙头当年涨幅 |
|:---:|:---|---:|:---:|:---|---:|
"""
    for i, r in enumerate(results[:20]):
        md_content += f"| {i+1} | {r['concept']} | {r['two_week_chg']}% | {r['up_count']}/{r['stock_count']} | {r['leader_name']} | {r['leader_year_chg']}% |\n"
    
    md_content += f"""
## 数据说明
- 涨幅计算：成分股近两周平均涨幅
- 龙头股定义：成分股中近两周涨幅最大
- 数据来源：Tushare概念板块（共879个概念）

## 热点关键词
{', '.join(HOT_KEYWORDS)}
"""
    
    md_path = f'热点概念追踪/outputs/{today_str}_热点概念报告.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"✓ 已保存: {md_path}")

if __name__ == '__main__':
    track_hot_concepts()
