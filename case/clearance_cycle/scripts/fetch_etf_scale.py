#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基ETF规模采集：上交所 + 深交所 份额 × 最新净值 → 规模（亿元）

口径：被动化率分子 = 宽基ETF总规模（沪深300/中证500/中证1000/中证2000/A500/A50/
创业板/科创50/科创100/科创200/双创/上证50/深证100/中证800/中证100）
分母 = 沪深300自由流通市值（Wind，见 hs300_mv.json）

支持 STAT_DATE 回溯（交易所按日公布份额）。
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "output", "data")
os.makedirs(DATA, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# 宽基指数标识（按优先级匹配，先长后短）
WIDE_TAGS = [
    ("中证A500", ["A500"]),
    ("中证A50", ["A50"]),
    ("沪深300", ["沪深300", "HS300", "300ETF", "华夏300", "300价值"]),
    ("中证500", ["中证500", "500ETF", "500增强"]),
    ("中证1000", ["中证1000", "1000ETF"]),
    ("中证2000", ["中证2000", "2000ETF"]),
    ("创业板", ["创业板"]),
    ("科创50", ["科创50", "科创板50"]),
    ("科创100", ["科创100"]),
    ("科创200", ["科创200"]),
    ("科创综指", ["科创综指", "科创综合"]),
    ("双创", ["双创"]),
    ("上证50", ["上证50"]),
    ("深证100", ["深证100"]),
    ("中证800", ["中证800"]),
    ("中证100", ["中证100"]),
    ("上证180", ["上证180"]),
]

# 行业/主题/策略 ETF 排除词
EXCLUDE = [
    "半导体", "芯片", "集成电路", "医药", "医疗", "生物", "创新药", "疫苗", "器械",
    "消费", "白酒", "食品", "饮料", "家电", "汽车", "新能源", "光伏", "电池", "储能",
    "有色", "稀土", "黄金", "白银", "钢铁", "煤炭", "化工", "建材", "机械", "军工",
    "国防", "券商", "证券", "银行", "保险", "金融", "地产", "农业", "养殖", "旅游",
    "传媒", "游戏", "影视", "通信", "5G", "计算机", "软件", "云", "大数据", "人工智能",
    "AI", "机器人", "电力", "公用", "环保", "物流", "港口", "航运", "航空", "油气",
    "石油", "天然气", "电网", "基建", "央企", "国企", "红利", "低波", "价值", "成长",
    "质量", "基本面", "现金流", "ESG", "科技", "电子", "教育", "电信", "船舶", "工程",
    "纺织", "轻工", "商贸", "零售", "非银", "全指", "主题", "中概", "港股", "恒生",
    "恒指", "纳斯达克", "标普", "日经", "德国", "法国", "东南亚", "沙特", "美国",
    "QDII", "跨境", "原油", "商品", "豆粕", "能化", "国债", "政金债", "信用债", "短融",
    "货币", "现金", "白银", "稀有", "金属", "化工", "新材料", "卫星", "低空", "算力",
    "数据", "信创", "网络安全", "创新", "龙头", "50等权", "等权", "增强", "动量",
]


def _get(url, params, headers, retry=3, timeout=25):
    last = None
    for i in range(retry):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def fetch_sse(date_str=None):
    """上交所 ETF 规模（TOT_VOL 单位：万份）"""
    h = {"User-Agent": UA, "Referer": "http://www.sse.com.cn/"}
    rows = []
    for p in range(1, 20):
        params = {
            "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
            "isPagination": "true",
            "pageHelp.pageSize": "100",
            "pageHelp.pageNo": str(p),
            "pageHelp.beginPage": str(p),
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": str(p),
        }
        if date_str:
            params["STAT_DATE"] = date_str
        r = _get("http://query.sse.com.cn/commonQuery.do", params, h)
        d = r.json()["pageHelp"]["data"]
        if not d:
            break
        rows += d
    if not rows:
        return pd.DataFrame(columns=["code", "name", "vol", "date"])
    df = pd.DataFrame(rows)
    df["code"] = df["SEC_CODE"].astype(str).str.zfill(6)
    df["name"] = df["SEC_NAME"]
    df["vol"] = pd.to_numeric(df["TOT_VOL"], errors="coerce")
    df["date"] = df["STAT_DATE"]
    return df[["code", "name", "vol", "date"]]


def fetch_szse():
    """深交所 ETF 列表（dqgm 当前规模，单位：万份）"""
    h = {"User-Agent": UA, "Referer": "http://www.szse.cn/"}
    rows = []
    for p in range(1, 100):
        try:
            r = _get(
                "http://www.szse.cn/api/report/ShowReport/data",
                {"SHOWTYPE": "JSON", "CATALOGID": "1945", "TABKEY": "tab1", "PAGENO": str(p)},
                h,
                timeout=20,
            )
            j = r.json()[0]["data"]
        except Exception:  # noqa: BLE001
            break
        if not j:
            break
        rows += j
    if not rows:
        return pd.DataFrame(columns=["code", "name", "vol", "date"])
    df = pd.DataFrame(rows)

    def clean(x):
        m = re.search(r"([\d,\.]+)", str(x))
        return float(m.group(1).replace(",", "")) if m else None

    df["code"] = df["sys_key"].str.extract(r"code=(\d+)")
    df["name"] = df["kzjcurl"].str.extract(r"name=([^'&]+)")
    df["vol"] = df["dqgm"].map(clean)
    df["date"] = None
    return df[["code", "name", "vol", "date"]]


def fetch_nav():
    """场内基金最新单位净值（东财 fund.eastmoney.com 源，可用）"""
    import akshare as ak

    nav = ak.fund_etf_fund_daily_em()
    col = [c for c in nav.columns if str(c).endswith("单位净值")][0]
    nav["code"] = nav["基金代码"].astype(str).str.zfill(6)
    nav["nav"] = pd.to_numeric(nav[col], errors="coerce")
    return nav.set_index("code")["nav"].to_dict(), str(col).replace("-单位净值", "")


def classify(name):
    """宽基分类：白名单命中 + 黑名单排除"""
    n = str(name)
    for k in EXCLUDE:
        if k in n:
            return None
    for cat, tags in WIDE_TAGS:
        for t in tags:
            if t in n:
                return cat
    return None


def build(date_str=None, save=True):
    sse = fetch_sse(date_str)
    szse = fetch_szse()
    navmap, nav_date = fetch_nav()
    etf = pd.concat([sse, szse], ignore_index=True)
    etf = etf.dropna(subset=["code", "vol"])
    etf["nav"] = etf["code"].map(lambda c: navmap.get(str(c).zfill(6)))
    etf["scale"] = etf["vol"] * etf["nav"] / 1e4  # 万份 × 元 / 1e4 = 亿元
    etf["cat"] = etf["name"].map(classify)
    wide = etf.dropna(subset=["cat", "scale"])
    by_cat = wide.groupby("cat")["scale"].sum().sort_values(ascending=False)
    total_wide = float(wide["scale"].sum())
    total_all = float(etf["scale"].sum())
    out = {
        "as_of": date_str or (sse["date"].dropna().iloc[0] if len(sse) and sse["date"].notna().any() else datetime.now().strftime("%Y-%m-%d")),
        "nav_date": nav_date,
        "wide_total_yi": round(total_wide, 1),
        "all_etf_total_yi": round(total_all, 1),
        "wide_count": int(len(wide)),
        "by_cat_yi": {k: round(float(v), 1) for k, v in by_cat.items()},
        "top10": wide.nlargest(10, "scale")[["code", "name", "cat", "vol", "nav", "scale"]].round(2).to_dict("records"),
        "missing_nav": int(etf["nav"].isna().sum()),
    }
    if save:
        with open(os.path.join(DATA, "etf_scale.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        wide.to_csv(os.path.join(DATA, "wide_etf_detail.csv"), index=False, encoding="utf-8-sig")
    return out


if __name__ == "__main__":
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else None
    r = build(d)
    print(json.dumps(r, ensure_ascii=False, indent=2))
