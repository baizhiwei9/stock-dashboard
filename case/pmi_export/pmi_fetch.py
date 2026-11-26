# -*- coding: utf-8 -*-
"""PMI 自动爬取模块（国家统计局官方发布页）

数据源：国家统计局 zxfb 发布页（制造业+非制造业 PMI 运行情况）
  - 最新发布页：从列表页 https://www.stats.gov.cn/sj/zxfb/ 自动定位
  - 历史补全：维护 data/pmi_pages.json 记录已知发布页 URL；
    首次初始化时预置上一年同期发布页，使新订单序列 >= 24 个月，可算 MA12 同比。

输出：data/pmi_latest.json
  - data_month   : 数据归属月份（YYYY-MM）
  - latest       : 当月各关键分项指数
  - series       : 按月序列 {month: field: value}（用于 MA12 同比等历史计算）
  - missing      : 正文/表格中未取到的字段清单（报告层按硬规则标【原文缺失】）
  - source_urls  : 本次用到的发布页 URL 清单
  - generated_at : 生成时间

硬规则：原文缺失绝不编造，缺字段进 missing 由报告层标【原文缺失】。
"""
import datetime
import io
import json
import os
import re
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, ensure_dirs

ensure_dirs()

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
LIST_URL = "https://www.stats.gov.cn/sj/zxfb/"
PAGES_FILE = os.path.join(DATA_DIR, "pmi_pages.json")
OUT_FILE = os.path.join(DATA_DIR, "pmi_latest.json")

# 首次初始化时预置的上一年同期发布页（补历史序列用；运行中自动追加新页）
SEED_PAGES = [
    "https://www.stats.gov.cn/sj/zxfb/202509/t20250930_1961438.html",  # 2025年9月发布页
]

# 表0：制造业构成指数；表1：制造业其他指标；表2：非制造业
TABLE0_COLS = ["month", "pmi", "production", "new_orders", "raw_material_inventory",
               "employees", "supplier_delivery"]
TABLE1_COLS = ["month", "new_export_orders", "imports", "purchase_qty",
               "main_rm_purchase_price", "ex_factory_price", "finished_goods_inventory",
               "orders_on_hand", "production_expectation"]
TABLE2_COLS = ["month", "business_activity", "n_new_orders", "input_price",
               "selling_price", "n_employees", "business_expectation"]


def _get(url, retries=3):
    """带重试的 GET（网络抖动重试 3 次，指数退避）。"""
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=H, timeout=25)
            r.encoding = "utf-8"
            if r.ok and len(r.text) > 5000:
                return r.text
            last = f"HTTP {r.status_code}, len={len(r.text)}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(2 ** i)
    raise RuntimeError(f"抓取失败 {url}: {last}")


def find_latest_pmi_url():
    """从统计局 zxfb 列表页定位最新「中国采购经理指数运行情况」发布页 URL。"""
    html = _get(LIST_URL)
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>\s*([^<]*采购经理指数运行情况[^<]*)</a>', html)
    if not links:
        raise RuntimeError("列表页未找到采购经理指数发布链接")
    href, title = links[0]
    if not href.startswith("http"):
        href = "https://www.stats.gov.cn/sj/zxfb/" + href.lstrip("./")
    return href, title.strip()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_page(url):
    """解析单张统计局 PMI 发布页：返回 {month: {field: value}} + 正文提取字段。"""
    html = _get(url)
    tables = pd.read_html(io.StringIO(html))
    if len(tables) < 3:
        raise RuntimeError(f"{url} 表格不足3张: {len(tables)}")
    series = {}

    def _fill(t, cols):
        # 数据行从第2行开始：第0行"单位：%"、第1行表头
        for _, row in t.iloc[2:].iterrows():
            vals = list(row)
            m = str(vals[0]).strip()
            if not re.match(r"^\d{4}年\d{1,2}月$", m):
                continue
            mm = re.sub(r"年", "-", m).replace("月", "")  # 2026年9月 -> 2026-9
            y, mo = mm.split("-")
            key = f"{y}-{int(mo):02d}"
            d = series.setdefault(key, {})
            for ci, col in enumerate(cols[1:], start=1):
                if ci < len(vals):
                    v = _num(re.sub(r"[^\d.\-]", "", str(vals[ci])))
                    if v is not None:
                        d[col] = v

    t0, t1, t2 = tables[0], tables[1], tables[2]
    if "PMI" not in " ".join(map(str, t0.iloc[1, :2])):
        raise RuntimeError(f"{url} 表0结构异常（非制造业构成表）")
    _fill(t0, TABLE0_COLS)
    _fill(t1, TABLE1_COLS)
    _fill(t2, TABLE2_COLS)

    # 正文提取（去标签后；各指数间可能含空格）
    plain = re.sub(r"<[^>]+>", " ", html)
    plain = re.sub(r"\s+", "", plain)
    extra = {}
    for key, pat in [
        ("service_new_orders", r"服务业新订单指数为([\d.]+)%"),
        ("construction_new_orders", r"建筑业新订单指数为([\d.]+)%"),
        ("basic_rm_new_orders", r"基础原材料[^\d]{0,10}新订单指数为([\d.]+)%"),
    ]:
        m = re.search(pat, plain)
        if m:
            v = _num(m.group(1))
            if v is not None:
                extra[key] = v
    return series, extra


def load_pages():
    if os.path.exists(PAGES_FILE):
        with open(PAGES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return list(SEED_PAGES)


def save_pages(pages):
    with open(PAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)


def merge_series(pages):
    """解析所有已知发布页，按月去重合并（后出现的页覆盖同月）。返回 (series, extras, urls)。"""
    all_series, all_extra = {}, {}
    for url in pages:
        try:
            s, e = parse_page(url)
        except Exception as ex:  # noqa: BLE001
            print(f"[warn] 解析失败，跳过 {url}: {ex}")
            continue
        for k, v in s.items():
            all_series[k] = v
        if e and s:
            # 正文提取值归属该页最新月份
            page_latest = sorted(s.keys())[-1]
            all_extra[page_latest] = e
    return all_series, all_extra


def main():
    # 1) 定位最新发布页
    latest_url, title = find_latest_pmi_url()
    pages = load_pages()
    added = 0
    if latest_url not in pages:
        pages.append(latest_url)
        added += 1
    if added:
        save_pages(pages)

    # 2) 全量解析并合并序列
    series, extras = merge_series(pages)
    if not series:
        raise RuntimeError("PMI 解析结果为空，疑似页面结构变更")

    months = sorted(series.keys())
    latest_month = months[-1]
    latest = dict(series[latest_month])

    # 3) 缺失校验：关键判定字段缺则记 missing（报告层标【原文缺失】）
    latest_extra = extras.get(latest_month, {})
    required_latest = ["new_orders", "production", "new_export_orders",
                       "n_new_orders", "service_new_orders", "construction_new_orders",
                       "basic_rm_new_orders"]
    missing = []
    for f in required_latest:
        if f not in latest and f not in latest_extra:
            missing.append(f)
    latest.update(latest_extra)

    out = {
        "data_month": latest_month,
        "title": title,
        "latest": latest,
        "series": series,
        "missing": missing,
        "source_urls": list(pages),
        "n_months": len(months),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    print("== PMI 爬取摘要 ==")
    print("最新月份:", latest_month, "| 序列长度:", len(months),
          f"({months[0]} ~ {months[-1]})", "| 新增发布页:", added)
    print("制造业PMI:", latest.get("pmi"), "| 新订单:", latest.get("new_orders"),
          "| 生产:", latest.get("production"), "| 新出口订单:", latest.get("new_export_orders"))
    print("非制造业商务活动:", latest.get("business_activity"),
          "| 非制造业新订单:", latest.get("n_new_orders"))
    print("服务业新订单:", latest.get("service_new_orders"),
          "| 建筑业新订单:", latest.get("construction_new_orders"))
    print("缺失字段:", missing if missing else "无")
    print("发布页:", latest_url)
    print("输出:", OUT_FILE)


if __name__ == "__main__":
    main()