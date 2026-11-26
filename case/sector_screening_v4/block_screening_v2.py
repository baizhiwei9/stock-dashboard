#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股概念板块+行业板块指数综合筛选v2
筛选条件：
1. 收盘价 > 40日均线
2. 40日均线向上（今日40MA > 昨日40MA）
3. 80日均线向上（今日80MA > 昨日80MA）
4. 近3日主力资金净流入 > 0
5. 近5日涨幅 > 2%
"""

import sys
import time
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Python 3.13 akshare兼容处理
sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()

# ============ API配置 ============
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://quote.eastmoney.com/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Connection': 'keep-alive'
}

# ============ Session配置 ============
def create_session():
    """创建带重试机制的Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

session = create_session()

# ============ 重试机制 ============
def safe_request(url, params, max_retries=5, base_delay=0.5):
    """带重试的请求"""
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + (attempt * 0.3)  # 递增延迟
                time.sleep(delay)
                session = create_session()  # 重建Session
            else:
                print(f"请求最终失败({url}): {e}")
                return None
    return None

# ============ 数据获取 ============
def get_block_list(block_type):
    """获取板块列表
    block_type: 2=概念板块, 3=行业板块
    """
    params = {
        'pn': 1,
        'pz': 500,  # 尽量多获取
        'po': 1,
        'np': 1,
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': 2,
        'invt': 2,
        'fid': 'f3',
        'fs': f'm:90+t:{block_type}',
        'fields': 'f12,f14,f3,f62'
    }
    
    result = safe_request(CLIST_URL, params)
    if result and result.get('data') and result['data'].get('diff'):
        items = result['data']['diff']
        blocks = []
        for item in items:
            blocks.append({
                'code': item.get('f12', ''),
                'name': item.get('f14', ''),
                'change_pct': item.get('f3', 0),
                'main_net': item.get('f62', 0)
            })
        return blocks
    return []

def get_kline_data(secid):
    """获取K线数据用于计算均线"""
    params = {
        'secid': secid,
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': 101,  # 日K
        'fqt': 1,    # 前复权
        'lmt': 120   # 120日足够计算80日均线
    }
    
    result = safe_request(KLINE_URL, params)
    if result and result.get('data') and result['data'].get('klines'):
        klines = result['data']['klines']
        return klines
    return []

def get_fflow_data(secid):
    """获取资金流数据（近5日）"""
    params = {
        'secid': secid,
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fields1': 'f1,f2,f3,f7,f8',
        'fields2': 'f51,f52,f53,f54,f55',
        'klt': 101,
        'lmt': 5
    }
    
    result = safe_request(FFLOW_URL, params)
    if result and result.get('data') and result['data'].get('klines'):
        return result['data']['klines']
    return []

# ============ 数据处理 ============
def calc_ma(klines, period):
    """计算均线"""
    if len(klines) < period:
        return None, None
    
    closes = []
    for kline in klines:
        parts = kline.split(',')
        closes.append(float(parts[2]))  # 收盘价
    
    ma_values = []
    for i in range(period - 1, len(closes)):
        ma = sum(closes[i - period + 1:i + 1]) / period
        ma_values.append((closes[i], ma))  # (收盘价, 均线值)
    
    if len(ma_values) >= 2:
        # 返回最近两个MA值
        return ma_values[-1], ma_values[-2]  # (今日, 昨日)
    return None, None

def calc_gain_5d(klines):
    """计算近5日涨幅"""
    if len(klines) < 6:
        return None
    
    # 取今日收盘和5日前收盘
    today_close = float(klines[-1].split(',')[2])
    five_day_ago_close = float(klines[-6].split(',')[2])
    
    if five_day_ago_close == 0:
        return None
    
    return (today_close - five_day_ago_close) / five_day_ago_close * 100

def parse_fflow(klines):
    """解析资金流数据
    返回: (近3日主力净流入总和, 近5日主力净流入总和, 近5日总净流入)
    
    格式: 日期,主力净流入,小单净流入,中单净流入,大单净流入
    """
    if not klines or len(klines) < 3:
        return None, None, None
    
    main_net_3d = 0
    main_net_5d = 0
    total_net_5d = 0
    
    # 近5日
    for i, kline in enumerate(klines[-5:] if len(klines) >= 5 else klines):
        parts = kline.split(',')
        main_net = float(parts[1])  # 主力净流入
        total_net = float(parts[1]) + float(parts[2]) + float(parts[3]) + float(parts[4])  # 总净流入
        main_net_5d += main_net
        total_net_5d += total_net
        
        if i >= len(klines) - 3:  # 近3日
            main_net_3d += main_net
    
    return main_net_3d, main_net_5d, total_net_5d

# ============ 筛选逻辑 ============
def analyze_block(code, name, block_type):
    """分析单个板块"""
    secid = f"90.{code}"
    block_type_name = "概念" if block_type == 2 else "行业"
    
    # 获取K线数据
    klines = get_kline_data(secid)
    if not klines or len(klines) < 80:
        return None
    
    # 计算均线
    ma40_today, ma40_yesterday = calc_ma(klines, 40)
    ma80_today, ma80_yesterday = calc_ma(klines, 80)
    
    if ma40_today is None or ma80_today is None:
        return None
    
    close_today = float(klines[-1].split(',')[2])
    ma40_val = ma40_today[1]
    ma80_val = ma80_today[1]
    
    # 计算近5日涨幅
    gain_5d = calc_gain_5d(klines)
    if gain_5d is None:
        return None
    
    # 获取资金流数据
    fflow_klines = get_fflow_data(secid)
    main_net_3d, main_net_5d, total_net_5d = parse_fflow(fflow_klines)
    
    if main_net_3d is None:
        return None
    
    return {
        'code': code,
        'name': name,
        'type': block_type_name,
        'close': close_today,
        'ma40': ma40_val,
        'ma80': ma80_val,
        'ma40_up': ma40_today[1] > ma40_yesterday[1] if ma40_yesterday else False,
        'ma80_up': ma80_today[1] > ma80_yesterday[1] if ma80_yesterday else False,
        'close_above_ma40': close_today > ma40_val,
        'main_net_3d': main_net_3d,
        'main_net_5d': main_net_5d,
        'total_net_5d': total_net_5d,
        'gain_5d': gain_5d
    }

def check_pass_conditions(analyze_result):
    """检查是否通过所有筛选条件"""
    if not analyze_result:
        return False, []
    
    conditions = []
    passed = True
    
    # 条件1: 收盘价 > 40日均线
    if not analyze_result['close_above_ma40']:
        passed = False
        conditions.append("收盘价≤40MA")
    
    # 条件2: 40日均线向上
    if not analyze_result['ma40_up']:
        passed = False
        conditions.append("40MA向下")
    
    # 条件3: 80日均线向上
    if not analyze_result['ma80_up']:
        passed = False
        conditions.append("80MA向下")
    
    # 条件4: 近3日主力资金净流入 > 0
    if analyze_result['main_net_3d'] <= 0:
        passed = False
        conditions.append("近3日主力净流出")
    
    # 条件5: 近5日涨幅 > 2%
    if analyze_result['gain_5d'] <= 2:
        passed = False
        conditions.append(f"近5日涨幅{analyze_result['gain_5d']:.2f}%≤2%")
    
    return passed, conditions

def check_special_mark(analyze_result):
    """检查是否需要特殊标注✖
    条件：未通过筛选 且 主力资金净流入>0 且 总资金净流入<0
    """
    if not analyze_result:
        return False
    
    if analyze_result['main_net_5d'] > 0 and analyze_result['total_net_5d'] < 0:
        return True
    return False

def format_net_flow(value):
    """格式化资金流，单位万元"""
    if value is None:
        return "N/A"
    if abs(value) >= 100000000:
        return f"{value/100000000:.2f}亿"
    elif abs(value) >= 10000:
        return f"{value/10000:.2f}亿"
    return f"{value:.2f}万"

# ============ 主程序 ============
def main():
    print(f"开始板块筛选... {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print(f"\n获取概念板块列表...")
    blocks_concept = get_block_list(2)
    print(f"获取到 {len(blocks_concept)} 个概念板块")
    
    time.sleep(0.5)
    
    print(f"\n获取行业板块列表...")
    blocks_industry = get_block_list(3)
    print(f"获取到 {len(blocks_industry)} 个行业板块")
    
    # 标记类型并合并
    for b in blocks_concept:
        b['block_type'] = 2
    for b in blocks_industry:
        b['block_type'] = 3
    
    all_blocks = blocks_concept + blocks_industry
    
    print(f"\n总计获取 {len(all_blocks)} 个板块，开始逐一分析...")
    
    results = []
    special_mark_blocks = []
    failed_count = 0
    
    for i, block in enumerate(all_blocks):
        code = block['code']
        name = block['name']
        bt = block['block_type']
        
        if i % 20 == 0:
            print(f"进度: {i}/{len(all_blocks)}, 成功: {len(results)}, 失败: {failed_count}")
        
        try:
            result = analyze_block(code, name, bt)
            
            if result:
                passed, conditions = check_pass_conditions(result)
                result['passed'] = passed
                result['conditions'] = conditions
                
                # 检查特殊标注
                if not passed and check_special_mark(result):
                    result['special_mark'] = True
                    special_mark_blocks.append(result)
                else:
                    result['special_mark'] = False
                
                results.append(result)
            else:
                failed_count += 1
        
        except Exception as e:
            failed_count += 1
            if failed_count <= 5:
                print(f"分析失败 {code} {name}: {e}")
        
        time.sleep(0.4)  # 增加间隔
    
    print(f"\n分析完成，共 {len(results)} 个有效板块，{failed_count} 个失败")
    
    # 筛选通过条件的板块
    passed_blocks = [r for r in results if r['passed']]
    passed_blocks.sort(key=lambda x: x['main_net_3d'], reverse=True)
    top30_passed = passed_blocks[:30]
    
    # 特殊标注板块（已按近5日主力净流入降序）
    special_mark_blocks.sort(key=lambda x: x['main_net_5d'], reverse=True)
    
    # 生成Markdown报告
    today = datetime.now().strftime('%Y-%m-%d')
    
    md_content = f"""# A股概念板块+行业板块指数综合筛选
**筛选时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**筛选条件**:
1. 收盘价 > 40日均线 ✓
2. 40日均线向上（今日40MA > 昨日40MA）✓
3. 80日均线向上（今日80MA > 昨日80MA）✓
4. 近3日主力资金净流入 > 0 ✓
5. 近5日涨幅 > 2% ✓

---

## 一、通过筛选条件的板块（前30名，按近3日主力净流入降序）

| 排名 | 板块名称 | 板块代码 | 类型 | 收盘价 | 40MA | 80MA | 近3日主力净流入 | 近5日涨幅 |
|:---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|
"""
    
    if top30_passed:
        for rank, block in enumerate(top30_passed, 1):
            md_content += f"| {rank} | {block['name']} | {block['code']} | {block['type']} | {block['close']:.3f} | {block['ma40']:.3f} | {block['ma80']:.3f} | {format_net_flow(block['main_net_3d'])} | {block['gain_5d']:+.2f}% |\n"
    else:
        md_content += "| - | 暂无符合条件的板块 | - | - | - | - | - | - | - |\n"
    
    # 特殊标注板块
    md_content += f"""
---

## 二、未通过筛选但值得关注的板块 ✖

**标注说明**: 散户在卖但主力在买（近5日总资金净流出但主力净流入>0），可能是潜在关注信号。

| 排名 | 板块名称 | 板块代码 | 类型 | 收盘价 | 40MA | 80MA | 近5日主力净流入 | 近5日总净流入 | 近5日涨幅 |
|:---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|---:|
"""
    
    if special_mark_blocks:
        for rank, block in enumerate(special_mark_blocks, 1):
            md_content += f"| ✖{rank} | {block['name']} | {block['code']} | {block['type']} | {block['close']:.3f} | {block['ma40']:.3f} | {block['ma80']:.3f} | {format_net_flow(block['main_net_5d'])} | {format_net_flow(block['total_net_5d'])} | {block['gain_5d']:+.2f}% |\n"
    else:
        md_content += "| - | 暂无符合条件的板块 | - | - | - | - | - | - | - | - |\n"
    
    # 统计摘要
    concept_count = len([r for r in results if r.get('type') == '概念'])
    industry_count = len([r for r in results if r.get('type') == '行业'])
    
    md_content += f"""

---

## 三、统计摘要

- **分析板块总数**: {len(results)}
- **概念板块**: {concept_count} 个
- **行业板块**: {industry_count} 个
- **通过筛选的板块数**: {len(passed_blocks)} 个
- **特殊标注板块数**: {len(special_mark_blocks)} 个

"""
    
    # 保存文件
    output_file = f"/Coze/Drive/蓁宝/所有对话/主对话/板块筛选v2_{today}.md"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    print(f"\n筛选完成！结果已保存到: {output_file}")
    print(f"通过筛选的板块: {len(passed_blocks)} 个")
    print(f"特殊标注板块: {len(special_mark_blocks)} 个")
    
    return output_file

if __name__ == "__main__":
    main()
