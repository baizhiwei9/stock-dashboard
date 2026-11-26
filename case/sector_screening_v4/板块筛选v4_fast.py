#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块筛选v4 - 并发加速版
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
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Python 3.13 compat
try:
    import pkgutil
    if not hasattr(pkgutil, 'ImpImporter'):
        sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})
except:
    pass

TODAY = datetime.now().strftime("%Y-%m-%d")
OUTPUT = _cp(f"/Coze/Drive/蓁宝/所有对话/主对话/板块筛选v4_{TODAY}.md")

LIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/"
}

_local = threading.local()
def get_session():
    if not hasattr(_local, 's'):
        s = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[500,502,503,504])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount('http://', adapter)
        s.mount('https://', adapter)
        _local.s = s
    return _local.s

def req(url, params, retries=2):
    s = get_session()
    for i in range(retries):
        try:
            r = s.get(url, params=params, headers=HEADERS, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < retries - 1:
                time.sleep(0.5 + i)
            else:
                return None

# ========== 板块列表 ==========
def get_blocks(btype):
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

# ========== 单板块筛选 ==========
def check_block(args):
    block, btype = args
    code = block.get("f12", "")
    name = block.get("f14", "未知")
    secid = f"90.{code}"

    # 并行获取K线和资金流
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(lambda: req(KLINE_URL, {
            "secid": secid, "klt": 101, "fqt": 1, "lmt": 120,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        }))
        f2 = ex.submit(lambda: req(FLOW_URL, {
            "secid": secid, "klt": 101, "lmt": 5,
            "fields1": "f1,f2,f3,f7,f8",
            "fields2": "f51,f52,f53,f54,f55"
        }))
        kl = f1.result()
        fl = f2.result()

    # K线解析
    if kl is None or not kl.get("data") or not kl.get("data", {}).get("klines"):
        return None
    klines = kl["data"]["klines"]
    if len(klines) < 80:
        return None

    closes = [float(k.split(',')[2]) for k in klines]
    cur = closes[-1]
    ma40 = sum(closes[-40:]) / 40
    ma40_prev = sum(closes[-41:-1]) / 40
    ma80 = sum(closes[-80:]) / 80
    ma80_prev = sum(closes[-81:-1]) / 80
    g5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0

    ma_ok = cur > ma40 and ma40 > ma40_prev and ma80 > ma80_prev

    # 资金流解析
    m3 = m5 = t5 = None
    if fl and fl.get("data", {}).get("klines"):
        fklines = fl["data"]["klines"]
        if len(fklines) >= 3:
            m3 = m5 = t5 = 0
            for i, k in enumerate(fklines):
                p = k.split(',')
                if len(p) < 5:
                    continue
                main, sm, mid, lg = float(p[1]), float(p[2]), float(p[3]), float(p[4])
                m5 += main
                t5 += main + sm + mid + lg
                if i < 3:
                    m3 += main

    # ✖标记：主力流入但总资金流出
    is_marked = False
    if not ma_ok and m5 is not None and m5 > 0 and t5 is not None and t5 < 0:
        is_marked = True

    if not ma_ok and not is_marked:
        return None

    # 取龙头股
    top = "—"
    try:
        p = {"pn": 1, "pz": 20, "po": 1, "np": 1,
             "ut": "bd1d9ddb04089700cf9c27f6f7426281",
             "fltt": 2, "invt": 2, "fid": "f3",
             "fs": f"b:{code}",
             "fields": "f12,f14,f3,f109"}
        d = req(LIST_URL, p)
        if d and d.get("data", {}).get("diff"):
            stocks = d["data"]["diff"]
            valid = [s for s in stocks if s.get("f109") not in (None, "-", "") and s.get("f109") is not None]
            if valid:
                best = max(valid, key=lambda x: float(x.get("f109") or 0))
                top = best.get("f14", "—")
    except:
        pass

    return {
        "name": name, "type": btype, "top": top,
        "m3": m3 or 0, "g5": g5, "marked": is_marked
    }

# ========== 主流程 ==========
def main():
    print(f"=== 板块筛选v4 (加速版) {TODAY} ===")
    t0 = time.time()

    # 获取板块列表
    print("[1] 获取板块列表...")
    concept_list = get_blocks(2)
    industry_list = get_blocks(3)
    print(f"  概念: {len(concept_list)}, 行业: {len(industry_list)}")

    all_blocks = [(b, "概念") for b in concept_list] + [(b, "行业") for b in industry_list]

    def safe_float(v, default=-100):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    filtered = [(b, t) for b, t in all_blocks if safe_float(b.get("f3")) >= -5]
    print(f"[2] 预筛后: {len(filtered)} 个板块")

    # 并发筛选
    print("[3] 并发筛选中...")
    qualified = []
    marked = []
    done = 0
    lock = threading.Lock()

    def progress():
        nonlocal done
        with lock:
            done += 1
            if done % 100 == 0:
                print(f"  进度: {done}/{len(filtered)}")

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(check_block, item): item for item in filtered}
        for future in as_completed(futures):
            progress()
            try:
                result = future.result()
                if result:
                    if result["marked"]:
                        marked.append(result)
                    else:
                        # 还要满足: 近3日主力净流入>0 且 近5日涨幅>2%
                        if result["m3"] > 0 and result["g5"] > 2:
                            qualified.append(result)
            except Exception as e:
                pass

    # 按近3日主力净流入降序
    qualified.sort(key=lambda x: x["m3"], reverse=True)
    qualified = qualified[:30]

    print(f"[4] 通过筛选: {len(qualified)} 个 | ✖标记: {len(marked)} 个")

    # 生成报告
    lines = [
        f"# A股板块筛选日报 - {TODAY}",
        "",
        f"**筛选条件**: 收盘价>MA40 & MA40向上 & MA80向上 & 近3日主力净流入>0 & 近5日涨幅>2%",
        f"**筛选范围**: 概念+行业共 {len(all_blocks)} 个板块，预筛后 {len(filtered)} 个",
        f"**通过数量**: {len(qualified)} 个（取前30）",
        "",
        "## 达标板块（按近3日主力净流入排序）",
        "",
        "| # | 板块 | 类型 | 龙头股 | 近5日涨幅 |",
        "|:-:|:---|:---:|:---|---:|",
    ]

    for i, b in enumerate(qualified, 1):
        lines.append(f"| {i} | {b['name']} | {b['type']} | {b['top']} | {b['g5']:.2f}% |")

    if marked:
        lines += [
            "",
            "## ✖ 资金背离板块（主力流入但总资金流出）",
            "",
            "| 板块 | 类型 | 龙头股 |",
            "|:---|:---:|:---|",
        ]
        for b in sorted(marked, key=lambda x: x["m3"], reverse=True)[:20]:
            lines.append(f"| {b['name']} | {b['type']} | {b['top']} |")

    lines.append("")
    report = "\n".join(lines)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(report)

    elapsed = time.time() - t0
    print(f"[5] 完成! 用时: {elapsed:.1f}秒")
    print(f"    输出: {OUTPUT}")

    # 飞书推送：交互式卡片 + 三级降级机制
    push_success = False
    push_error = ""

    # 1. 优先推送交互式卡片
    try:
        import sys as _sys
        _sys.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
        from feishu_card_utils import build_sector_screening_card
        from feishu_push import push_card_to_feishu
        from datetime import datetime as _dt

        # 构建板块数据
        sectors_data = []
        for b in qualified[:20]:
            sectors_data.append({
                'name': b['name'],
                'change_pct': b['g5'],
                'hot_score': f"{b['m3']:.1f}亿",
                'stocks_count': b.get('type', ''),
            })

        today_str = _dt.now().strftime('%Y-%m-%d')
        filters_info = {
            '筛选条件': '收盘价>MA40 & MA40向上 & MA80向上 & 近3日主力净流入>0 & 近5日涨幅>2%',
            '筛选范围': f"概念+行业共{len(all_blocks)}个板块",
            '预筛后': f"{len(filtered)}个板块",
        }

        card = build_sector_screening_card(
            today_str,
            sectors_data,
            total_count=len(qualified),
            filters_info=filters_info,
        )
        push_success = push_card_to_feishu(card_dict=card)
        if push_success:
            print("✅ 板块筛选卡片推送成功")
        else:
            push_error = "卡片推送返回失败"
    except Exception as e:
        push_error = f"卡片推送异常: {e}"
        print(f"⚠️ {push_error}")
        import traceback; traceback.print_exc()

    # 2. 降级1：卡片失败则推纯文本MD
    if not push_success and os.path.exists(OUTPUT):
        try:
            import sys as _sys2
            _sys2.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
            from feishu_push import push_to_feishu
            push_success = push_to_feishu(file_path=OUTPUT)
            if push_success:
                print("⚠️ 已降级为纯文本推送")
        except Exception as e:
            print(f"❌ 纯文本推送也失败: {e}")

    # 3. 降级2：都失败则至少通知主人
    if not push_success:
        try:
            import sys as _sys3
            _sys3.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
            from feishu_push import push_to_feishu
            err_msg = f"[主人](at://owner) ⚠️ 板块筛选日报推送失败\n错误: {push_error or '未知原因'}\n本地文件: {OUTPUT}"
            push_to_feishu(text=err_msg)
            print("❌ 已发送失败通知")
        except Exception as e2:
            print(f"❌❌ 连失败通知都发不出去: {e2}")

if __name__ == "__main__":
    main()
