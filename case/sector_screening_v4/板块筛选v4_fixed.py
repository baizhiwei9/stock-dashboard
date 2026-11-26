#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块筛选v4 - 精简版
输出: 板块 | 类型 | 龙头股(近5日涨幅最高的那只)
数据源: 东方财富API
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
import requests, json, time, random, sys, os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Python 3.13 compat
try:
    import pkgutil
    if not hasattr(pkgutil, 'ImpImporter'):
        sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})
except:
    pass

TODAY = datetime.now().strftime("%Y-%m-%d")
OUTPUT = _cp(f"/app/data/所有对话/主对话/板块筛选v4_{TODAY}.md")

LIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2delay.eastmoney.com/api/qt/stock/kline/get"
FLOW_URL = "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get"

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

def req(url, params, retries=3):
    for i in range(retries):
        try:
            time.sleep(0.4 + random.uniform(0.1, 0.3))
            r = _session.get(url, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < retries - 1:
                time.sleep(2 ** i * 2)
                new_session()
            else:
                return None

new_session()

# ========== 板块列表 ==========
def get_blocks(btype):
    """t:2=概念, t:3=行业"""
    all_b = []
    page = 1
    while True:
        p = {"pn": page, "pz": 100, "po": 1, "np": 1,
             "ut": "bd1d9ddb04089700cf9c27f6f7426281",
             "fltt": 2, "invt": 2, "fid": "f3",
             "fs": f"m:90+t:{btype}",
             "fields": "f12,f14,f3,f8,f20,f9"}
        d = req(LIST_URL, p)
        if not d or not d.get("data", {}).get("diff"):
            break
        blocks = d["data"]["diff"]
        all_b.extend(blocks)
        if len(blocks) < 100:
            break
        page += 1
    return all_b

# ========== K线 ==========
def get_kline(secid, lmt=120):
    return req(KLINE_URL, {
        "secid": secid, "klt": 101, "fqt": 1, "lmt": lmt,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    })

# ========== 资金流 ==========
def get_flow(secid):
    return req(FLOW_URL, {
        "secid": secid, "klt": 101, "lmt": 5,
        "fields1": "f1,f2,f3,f7,f8",
        "fields2": "f51,f52,f53,f54,f55"
    })

def parse_flow(data):
    if not data or not data.get("data", {}).get("klines"):
        return None, None, None
    klines = data["data"]["klines"]
    if len(klines) < 3:
        return None, None, None
    m3 = m5 = t5 = 0
    for i, k in enumerate(klines):
        p = k.split(',')
        if len(p) < 5:
            continue
        main, sm, mid, lg = float(p[1]), float(p[2]), float(p[3]), float(p[4])
        m5 += main
        t5 += main + sm + mid + lg
        if i < 3:
            m3 += main
    return m3, m5, t5

# ========== 龙头股 ==========
def get_top_stock(sector_code, sector_name):
    """找板块内近5日涨幅最高的股票"""
    try:
        # 获取成分股，按5日涨幅字段排序取前20
        p = {"pn": 1, "pz": 30, "po": 1, "np": 1,
             "ut": "bd1d9ddb04089700cf9c27f6f7426281",
             "fltt": 2, "invt": 2, "fid": "f3",
             "fs": f"b:{sector_code}",
             "fields": "f12,f14,f3,f109"}
        d = req(LIST_URL, p)
        if not d or not d.get("data", {}).get("diff"):
            return "—"

        stocks = d["data"]["diff"]
        valid = [s for s in stocks if s.get("f109") not in (None, "-", "") and s.get("f109") is not None]
        if valid:
            best = max(valid, key=lambda x: float(x.get("f109") or 0))
            return best.get("f14", "—")
        return "—"
    except Exception as e:
        return "—"

# ========== 主流程 ==========
def main():
    print(f"=== 板块筛选v4 {TODAY} ===")

    # 获取板块列表
    print("[1] 获取板块列表...")
    concept_list = get_blocks(2)
    industry_list = get_blocks(3)
    print(f"  概念: {len(concept_list)}, 行业: {len(industry_list)}")

    # 带类型标记
    all_blocks = [(b, "概念") for b in concept_list] + [(b, "行业") for b in industry_list]

    # 预筛：排除今日跌幅>5%
    def safe_float(v, default=-100):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default
    filtered = [(b, t) for b, t in all_blocks if safe_float(b.get("f3")) >= -5]
    print(f"[2] 预筛后: {len(filtered)}")

    qualified = []
    marked = []
    fail_count = 0

    for idx, (block, btype) in enumerate(filtered):
        if idx > 0 and idx % 50 == 0:
            new_session()
            print(f"  进度: {idx}/{len(filtered)} | 通过: {len(qualified)} | ✖: {len(marked)}")

        code = block.get("f12", "")
        name = block.get("f14", "未知")
        secid = f"90.{code}"

        # K线
        kl = get_kline(secid)
        if kl is None or not kl.get("data") or not kl.get("data", {}).get("klines"):
            fail_count += 1
            continue
        klines = kl["data"]["klines"]
        if len(klines) < 80:
            continue

        closes = [float(k.split(',')[2]) for k in klines]
        cur = closes[-1]
        ma40 = sum(closes[-40:]) / 40
        ma40_prev = sum(closes[-41:-1]) / 40
        ma80 = sum(closes[-80:]) / 80
        ma80_prev = sum(closes[-81:-1]) / 80
        g5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0

        ma_ok = cur > ma40 and ma40 > ma40_prev and ma80 > ma80_prev

        if not ma_ok:
            # ✖检查
            fl = get_flow(secid)
            _, m5, t5 = parse_flow(fl)
            if m5 is not None and m5 > 0 and t5 < 0:
                top = get_top_stock(code, name)
                marked.append({"name": name, "type": btype, "top": top})
            continue

        # 资金流
        fl = get_flow(secid)
        m3, m5, t5 = parse_flow(fl)
        if m3 is None:
            continue

        if m3 > 0 and g5 > 2:
            top = get_top_stock(code, name)
            qualified.append({"name": name, "type": btype, "top": top, "m3": m3})

    # 排序取前30
    qualified.sort(key=lambda x: x["m3"], reverse=True)
    top30 = qualified[:30]

    print(f"\n[结果] 通过: {len(qualified)}, 取前30: {len(top30)}, ✖标注: {len(marked)}, K线失败: {fail_count}")

    # 生成报告
    md = f"""# A股板块筛选日报

**{TODAY}** | 数据源: 东方财富
**筛选条件**: 收盘>40MA & MA40↑ & MA80↑ & 近3日主力净流入>0 & 近5日涨幅>2%

---

## ✅ 符合条件板块（按近3日主力净流入排序）

| 板块 | 类型 | 龙头股 |
|:---:|:---:|:---:|
"""
    for b in top30:
        md += f"| {b['name']} | {b['type']} | **{b['top']}** |\n"

    if not top30:
        md += "| 暂无 | — | — |\n"

    if marked:
        md += f"""
---

## ✖ 散户卖主力买（主力净流入>0 但总资金净流出）

| 板块 | 类型 | 龙头股 |
|:---:|:---:|:---:|
"""
        for b in marked:
            md += f"| {b['name']} | {b['type']} | **{b['top']}** |\n"

    md += f"\n---\n*{datetime.now().strftime('%H:%M')} 自动生成*\n"

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f"✅ 报告已保存: {OUTPUT}")

    # 同时输出JSON方便后续处理
    result_json = {
        "date": TODAY,
        "qualified": [{"name": b["name"], "type": b["type"], "top": b["top"]} for b in top30],
        "marked": [{"name": b["name"], "type": b["type"], "top": b["top"]} for b in marked]
    }
    json_path = OUTPUT.replace(".md", ".json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
