#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块筛选 - 东方财富API
筛选条件：
1. 收盘价 > 40日均线
2. 40日均线向上（今日MA > 昨日MA）
3. 80日均线向上（今日MA > 昨日MA）
4. 近3日主力资金净流入 > 0
5. 近5日涨幅 > 2%
6. 按近3日主力资金净流入降序，取前30
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

import requests
import json
import time
import random
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ========== 配置 ==========
TODAY = "2026-07-02"
OUTPUT_FILE = _cp(f"/Coze/Drive/蓁宝/所有对话/主对话/板块筛选_东财_{TODAY}.md")

# 东财API
LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/"
}

# ========== 稳健的HTTP会话 ==========
def create_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

_session = create_session()

def robust_request(url, params, max_retries=3):
    """带重试和指数退避的请求"""
    global _session
    for attempt in range(max_retries):
        try:
            time.sleep(0.8 + random.uniform(0.2, 0.5))
            response = _session.get(url, params=params, headers=HEADERS, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 2
                print(f"  请求失败 (尝试 {attempt+1}/{max_retries}): {e}, 等待 {wait}s")
                time.sleep(wait)
                # 重建Session
                _session = create_session()
            else:
                print(f"  请求最终失败: {e}")
                return None

def refresh_session():
    """重建全局会话"""
    global _session
    _session = create_session()

# ========== 1. 获取板块列表 ==========
def get_block_list(block_type):
    """获取板块列表 (t:2=概念, t:3=行业)"""
    all_blocks = []
    page = 1
    while True:
        params = {
            "pn": page,
            "pz": 100,
            "po": 1,  # 按涨幅降序
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": f"m:90+t:{block_type}",
            "fields": "f12,f14,f3,f8,f20,f9"  # 代码,名称,涨跌幅,成交量,市值,换手
        }
        data = robust_request(LIST_URL, params)
        if not data or data.get("data", {}).get("diff") is None:
            break
        
        blocks = data["data"]["diff"]
        all_blocks.extend(blocks)
        
        if len(blocks) < 100:
            break
        page += 1
        print(f"  {block_type}板块 第{page}页获取 {len(blocks)} 条")
    
    return all_blocks

# ========== 2. 获取K线数据 ==========
def get_kline_data(secid):
    """获取K线数据，用于计算均线"""
    params = {
        "secid": secid,
        "klt": 101,  # 日K
        "fqt": 1,    # 前复权
        "lmt": 120,  # 120日
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    }
    return robust_request(KLINE_URL, params)

def calc_ma(klines, period):
    """计算均线"""
    closes = [float(k.split(',')[2]) for k in klines]
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

# ========== 3. 获取资金流数据 ==========
def get_flow_data(secid):
    """获取资金流数据，近5日"""
    params = {
        "secid": secid,
        "klt": 101,
        "lmt": 5,
        "fields1": "f1,f2,f3,f7,f8",
        "fields2": "f51,f52,f53,f54,f55"  # 日期,主力,小单,中单,大单
    }
    return robust_request(FLOW_URL, params)

def parse_flow_data(data):
    """解析资金流数据
    返回: (近5日主力净流入总额, 近3日主力净流入总额, 近5日总净流入)
    """
    if not data or not data.get("data", {}).get("klines"):
        return None, None, None
    
    klines = data["data"]["klines"]
    if len(klines) < 3:
        return None, None, None
    
    main_5d = 0
    main_3d = 0
    total_5d = 0
    
    for i, kline in enumerate(klines):
        parts = kline.split(',')
        if len(parts) < 5:
            continue
        # 主力净流入, 小单, 中单, 大单
        main = float(parts[1])
        small = float(parts[2])
        mid = float(parts[3])
        large = float(parts[4])
        
        day_total = main + small + mid + large
        
        if i < 5:  # 近5日
            main_5d += main
            total_5d += day_total
        
        if i < 3:  # 近3日
            main_3d += main
    
    return main_5d, main_3d, total_5d

# ========== 主筛选逻辑 ==========
def main():
    print(f"=== A股板块筛选 {TODAY} ===")
    print("=" * 60)
    
    # Step 1: 获取板块列表
    print("\n[Step 1] 获取板块列表...")
    concept_blocks = get_block_list(2)
    industry_blocks = get_block_list(3)
    
    all_blocks = concept_blocks + industry_blocks
    print(f"概念板块: {len(concept_blocks)} 个, 行业板块: {len(industry_blocks)} 个, 总计: {len(all_blocks)} 个")
    
    # Step 2: 预筛 - 排除今日涨跌幅 < -5%
    print("\n[Step 2] 预筛排除跌幅超5%的板块...")
    filtered = [b for b in all_blocks if b.get("f3", -100) >= -5]
    print(f"预筛后: {len(filtered)} 个板块")
    
    # Step 3: 分批处理，每50个重建Session
    print("\n[Step 3] 检查均线条件...")
    batch_size = 50
    qualified_blocks = []
    mark_blocks = []  # 额外标注的板块
    
    for idx, block in enumerate(filtered):
        if idx > 0 and idx % batch_size == 0:
            print(f"  已处理 {idx}/{len(filtered)}，重建Session...")
            refresh_session()
        
        code = block.get("f12", "")
        name = block.get("f14", "未知")
        change_pct = block.get("f3", 0)
        block_type = "概念" if "t:2" in str(block.get("fs", "")) else "行业"
        
        secid = f"90.{code}"
        
        # 获取K线
        kline_data = get_kline_data(secid)
        if kline_data is None or not kline_data.get("data") or not kline_data.get("data", {}).get("klines"):
            continue
        
        klines = kline_data["data"]["klines"]
        if len(klines) < 80:
            continue
        
        # 计算均线
        closes = [float(k.split(',')[2]) for k in klines]
        cur_close = closes[-1]
        
        ma40_today = sum(closes[-40:]) / 40
        ma40_yesterday = sum(closes[-41:-1]) / 40
        ma80_today = sum(closes[-80:]) / 80
        ma80_yesterday = sum(closes[-81:-1]) / 80
        
        # 计算近5日涨幅
        if len(closes) >= 5:
            gain_5d = (closes[-1] - closes[-5]) / closes[-5] * 100
        else:
            gain_5d = 0
        
        # 检查均线条件
        ma_pass = (cur_close > ma40_today and 
                   ma40_today > ma40_yesterday and 
                   ma80_today > ma80_yesterday)
        
        if not ma_pass:
            # 额外标注检查：主力净流入 > 0（近5日）且 总资金净流出 < 0
            flow_data = get_flow_data(secid)
            main_5d, _, total_5d = parse_flow_data(flow_data)
            
            if main_5d is not None and main_5d > 0 and total_5d < 0:
                mark_blocks.append({
                    "name": name,
                    "code": code,
                    "type": block_type,
                    "close": cur_close,
                    "ma40": ma40_today,
                    "ma80": ma80_today,
                    "change_pct": change_pct,
                    "gain_5d": gain_5d,
                    "main_5d": main_5d,
                    "total_5d": total_5d,
                    "fail_reason": f"收盘价{cur_close:.2f}{'≤' if cur_close <= ma40_today else ''}MA40;"
                                  f"MA40{'平' if abs(ma40_today-ma40_yesterday)<0.001 else ('↑' if ma40_today>ma40_yesterday else '↓')};"
                                  f"MA80{'平' if abs(ma80_today-ma80_yesterday)<0.001 else ('↑' if ma80_today>ma80_yesterday else '↓')}"
                })
            continue
        
        # 获取资金流
        flow_data = get_flow_data(secid)
        main_5d, main_3d, total_5d = parse_flow_data(flow_data)
        
        if main_3d is None:
            continue
        
        # 完整条件检查
        cond1 = cur_close > ma40_today
        cond2 = ma40_today > ma40_yesterday
        cond3 = ma80_today > ma80_yesterday
        cond4 = main_3d > 0
        cond5 = gain_5d > 2
        
        if cond1 and cond2 and cond3 and cond4 and cond5:
            qualified_blocks.append({
                "name": name,
                "code": code,
                "type": block_type,
                "close": cur_close,
                "ma40": ma40_today,
                "ma80": ma80_today,
                "change_pct": change_pct,
                "gain_5d": gain_5d,
                "main_3d": main_3d,
                "main_5d": main_5d,
                "total_5d": total_5d
            })
        
        if idx % 20 == 0:
            print(f"  进度: {idx}/{len(filtered)}...")
    
    # Step 4: 排序并取前30
    print("\n[Step 4] 排序取前30...")
    qualified_blocks.sort(key=lambda x: x.get("main_3d", 0), reverse=True)
    top30 = qualified_blocks[:30]
    
    print(f"通过筛选板块: {len(qualified_blocks)} 个, 取前30: {len(top30)} 个")
    print(f"额外标注板块: {len(mark_blocks)} 个")
    
    # Step 5: 生成Markdown
    print("\n[Step 5] 生成Markdown...")
    
    md_content = f"""# A股板块筛选报告

**筛选日期**: {TODAY}  
**数据来源**: 东方财富  
**筛选条件**: 收盘价>40日均线 & MA40向上 & MA80向上 & 近3日主力净流入>0 & 近5日涨幅>2%

---

## ✅ 符合条件板块 TOP30

按近3日主力资金净流入降序排列：

| 排名 | 板块名称 | 代码 | 类型 | 收盘价 | 40MA | 80MA | 近3日主力净流入(万) | 近5日涨幅 |
|:---:|:--------:|:----:|:----:|-------:|-----:|-----:|-------------------:|----------:|
"""
    
    for i, b in enumerate(top30, 1):
        main_3d_str = f"{b['main_3d']/10000:.2f}亿" if abs(b['main_3d']) >= 10000 else f"{b['main_3d']:.2f}万"
        md_content += f"| {i} | {b['name']} | {b['code']} | {b['type']} | {b['close']:.2f} | {b['ma40']:.2f} | {b['ma80']:.2f} | {main_3d_str} | {b['gain_5d']:.2f}% |\n"
    
    # 额外标注表格
    if mark_blocks:
        md_content += f"""

---

## ✖ 未通过筛选但资金面特殊板块

满足条件：**近5日主力净流入>0 且 总资金（含散户）净流出<0**

| 板块名称 | 代码 | 类型 | 收盘价 | 40MA | 80MA | 近5日主力净流入(万) | 近5日总净流入(万) | 近5日涨幅 | 未通过原因 |
|:--------:|:----:|:----:|-------:|-----:|-----:|-------------------:|------------------:|----------:|:-----------|
"""
        for b in mark_blocks:
            main_5d_str = f"{b['main_5d']/10000:.2f}亿" if abs(b['main_5d']) >= 10000 else f"{b['main_5d']:.2f}万"
            total_5d_str = f"{b['total_5d']/10000:.2f}亿" if abs(b['total_5d']) >= 10000 else f"{b['total_5d']:.2f}万"
            md_content += f"| {b['name']} | {b['code']} | {b['type']} | {b['close']:.2f} | {b['ma40']:.2f} | {b['ma80']:.2f} | {main_5d_str} | {total_5d_str} | {b['gain_5d']:.2f}% | {b['fail_reason']} |\n"
    
    # 统计摘要
    md_content += f"""

---

## 📊 统计摘要

- **概念板块总数**: {len(concept_blocks)} 个
- **行业板块总数**: {len(industry_blocks)} 个
- **预筛后板块数**: {len(filtered)} 个（排除今日跌幅>5%）
- **符合全部条件**: {len(qualified_blocks)} 个
- **额外标注板块**: {len(mark_blocks)} 个
- **输出TOP**: {len(top30)} 个

---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    
    # 保存
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    print(f"\n✅ 报告已保存: {OUTPUT_FILE}")
    print(f"符合条件: {len(top30)} 个板块")
    print(f"额外标注: {len(mark_blocks)} 个板块")

if __name__ == "__main__":
    main()
