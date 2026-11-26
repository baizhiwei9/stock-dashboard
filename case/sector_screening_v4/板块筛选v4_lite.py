#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于v2筛选结果，补充龙头股信息
只针对已知通过筛选的板块找龙头股，大幅减少API调用
"""
import requests, json, time, random, sys
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import pkgutil
    if not hasattr(pkgutil, 'ImpImporter'):
        sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})
except:
    pass

TODAY = "2026-07-02"
OUTPUT = f"/app/data/所有对话/主对话/板块筛选v4_{TODAY}.md"

LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/"
}

_session = None
def new_session():
    global _session
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500,502,503,504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount('http://', adapter)
    s.mount('https://', adapter)
    _session = s

def req(url, params, retries=4):
    for i in range(retries):
        try:
            time.sleep(0.6 + random.uniform(0.1, 0.4))
            r = _session.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
            return data
        except Exception as e:
            print(f"    请求失败({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(3 + i * 2)
                new_session()
            else:
                return None

new_session()

# v2通过的板块（今早10:51结果）
QUALIFIED = [
    ("BK1012", "PVDF概念", "行业"),
    ("BK1424", "氟化工", "概念"),
    ("BK1326", "半导体设备", "概念"),
    ("BK0690", "氟化工概念", "行业"),
    ("BK0998", "机器视觉", "行业"),
    ("BK1108", "科创板做市股", "行业"),
    ("BK0949", "氦气概念", "行业"),
    ("BK1408", "机器人", "概念"),
    ("BK1184", "人形机器人", "行业"),
    ("BK1327", "分立器件", "概念"),
    ("BK0946", "EDA概念", "行业"),
    ("BK1145", "机器人执行器", "行业"),
    ("BK1330", "模拟芯片设计", "概念"),
    ("BK1119", "PLC概念", "行业"),
    ("BK1422", "无机盐", "概念"),
    ("BK0901", "3D摄像头", "行业"),
    ("BK1438", "合成树脂", "概念"),
    ("BK0458", "仪器仪表", "概念"),
    ("BK1319", "硅料硅片", "概念"),
]

MARKED = [
    ("BK1335", "面板", "概念"),
    ("BK1443", "橡胶助剂", "概念"),
    ("BK1395", "机床工具", "概念"),
    ("BK1334", "光学元件", "概念"),
]

def get_top_stock(sector_code, sector_name):
    """找板块内近5日涨幅最高的股票"""
    print(f"  查找 {sector_name}({sector_code}) 龙头股...")
    
    # 获取成分股
    p = {"pn": 1, "pz": 30, "po": 1, "np": 1,
         "ut": "bd1d9ddb04089700cf9c27f6f7426281",
         "fltt": 2, "invt": 2, "fid": "f3",
         "fs": f"b:{sector_code}",
         "fields": "f12,f14,f3"}
    d = req(LIST_URL, p)
    
    if not d or not d.get("data", {}).get("diff"):
        print(f"    ✗ 获取成分股失败")
        return "—"
    
    stocks = d["data"]["diff"]
    print(f"    成分股: {len(stocks)}只，取涨幅前15计算5日涨幅")
    
    # 按今日涨幅排序取前15
    def safe_f3(x):
        v = x.get("f3", 0)
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0
    
    sorted_stocks = sorted(
        stocks,
        key=lambda x: safe_f3(x),
        reverse=True
    )[:15]
    
    best_name = "—"
    best_gain = -999
    
    for s in sorted_stocks:
        code = s.get("f12", "")
        name = s.get("f14", "未知")
        if not code:
            continue
        
        prefix = "1" if code.startswith(("6", "9")) else "0"
        secid = f"{prefix}.{code}"
        
        # 获取10日K线算5日涨幅
        kl = req(KLINE_URL, {
            "secid": secid, "klt": 101, "fqt": 1, "lmt": 10,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        })
        
        kl_data = kl.get("data") if kl else None
        if kl_data and kl_data.get("klines"):
            kls = kl_data["klines"]
            if len(kls) >= 5:
                closes = [float(k.split(',')[2]) for k in kls]
                if closes[-5] > 0:
                    g5 = (closes[-1] - closes[-5]) / closes[-5] * 100
                    if g5 > best_gain:
                        best_gain = g5
                        best_name = name
    
    print(f"    ✓ 龙头: {best_name} (+{best_gain:.1f}%)")
    return best_name

def main():
    print(f"=== 补充龙头股信息 {TODAY} ===")
    print(f"基于v2筛选结果 ({len(QUALIFIED)}个通过 + {len(MARKED)}个✖)")
    
    # 找龙头股
    qualified_with_top = []
    for code, name, btype in QUALIFIED:
        top = get_top_stock(code, name)
        qualified_with_top.append({"name": name, "type": btype, "top": top})
        time.sleep(0.3)
    
    marked_with_top = []
    for code, name, btype in MARKED:
        top = get_top_stock(code, name)
        marked_with_top.append({"name": name, "type": btype, "top": top})
        time.sleep(0.3)
    
    # 生成报告
    md = f"""# A股板块筛选日报

**{TODAY}** | 数据源: 东方财富
**筛选条件**: 收盘>40MA & MA40↑ & MA80↑ & 近3日主力净流入>0 & 近5日涨幅>2%

---

## ✅ 符合条件板块（按近3日主力净流入排序）

| 板块 | 类型 | 龙头股 |
|:---:|:---:|:---:|
"""
    for b in qualified_with_top:
        md += f"| {b['name']} | {b['type']} | **{b['top']}** |\n"
    
    if marked_with_top:
        md += f"""
---

## ✖ 散户卖主力买（主力净流入>0 但总资金净流出）

| 板块 | 类型 | 龙头股 |
|:---:|:---:|:---:|
"""
        for b in marked_with_top:
            md += f"| {b['name']} | {b['type']} | **{b['top']}** |\n"
    
    md += f"\n---\n*{datetime.now().strftime('%H:%M')} 自动生成*\n"
    
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(md)
    
    print(f"\n✅ 报告已保存: {OUTPUT}")
    print(f"通过: {len(qualified_with_top)}, ✖: {len(marked_with_top)}")
    
    # 打印结果预览
    print("\n=== 结果预览 ===")
    print(md)

if __name__ == "__main__":
    main()
