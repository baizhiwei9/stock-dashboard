#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基ETF规模采集：上交所 + 深交所 份额 × 最新净值 → 规模（亿元）

口径：被动化率分子 = 宽基ETF总规模（沪深300/中证500/中证1000/中证2000/A500/A50/
创业板/科创50/科创100/科创200/双创/上证50/深证100/中证800/中证100）
分母 = 沪深300自由流通市值（Wind，见 hs300_mv.json）

支持 STAT_DATE 回溯（交易所按日公布份额）。
"""
import io
import json
import os
import subprocess
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


def _fetch_szse_strict():
    """深交所 ETF 份额（基金列表 xlsx，一次请求拿全 782 只）。

    为什么不再走 www.szse.cn 的分页 JSON：接口固定 10 条/页、共 79 页，
    连续请求会被断连，原实现把异常 break 掉，深市 ETF 静默缺失 ——
    被动化率因此偏低约 2pp，7% 提醒线会漏报。
    fund.szse.cn 的 xlsx 导出有 TLS 指纹限制，python-requests 常被 RemoteDisconnected，
    所以下载失败时退回 curl 取同一个文件（本机 curl 正常）。
    """
    url = "https://fund.szse.cn/api/report/ShowReport"
    params = {"SHOWTYPE": "xlsx", "CATALOGID": "1000_lf",
              "TABKEY": "tab1", "random": "0.07610353191740105"}
    h = {
        "Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/88.0.4324.150 Safari/537.36",
    }
    content = None
    try:
        r = requests.get(url, params=params, headers=h, timeout=60)
        if r.status_code == 200 and r.content[:2] == b"PK":
            content = r.content
        else:
            print(f"[etf_scale] 深交所 xlsx 响应异常: {r.status_code} len={len(r.content)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[etf_scale] requests 取深交所 xlsx 失败，改用 curl: {e}", flush=True)
    if content is None:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        cmd = ["curl", "-sS", "--fail", "--max-time", "90",
               "-H", f"Referer: {h['Referer']}", "-H", f"User-Agent: {h['User-Agent']}",
               f"{url}?{qs}"]
        cp = subprocess.run(cmd, capture_output=True)
        if cp.returncode != 0 or not cp.stdout.startswith(b"PK"):
            raise RuntimeError(f"深交所 ETF xlsx 下载失败: curl rc={cp.returncode} "
                               f"{cp.stderr[:200]!r}")
        content = cp.stdout

    df = pd.read_excel(io.BytesIO(content), engine="openpyxl", dtype={"基金代码": str})
    df = df[df["基金类别"].astype(str).str.strip() == "ETF"].copy()
    vol = pd.to_numeric(
        df["当前规模(份)"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    out = pd.DataFrame({
        "code": df["基金代码"].astype(str).str.zfill(6),
        "name": df["基金简称"],
        "vol": vol / 1e4,      # 份 → 万份，与上交所 TOT_VOL 同口径
        "date": None,
    }).dropna(subset=["code", "vol"])
    if len(out) < 100:
        raise RuntimeError(f"深交所 ETF 只解析到 {len(out)} 只，疑似文件不完整")
    return out


def fetch_szse():
    """深交所 ETF 份额：严格取数 + 失败降级。

    取不到返回空表、不抛异常，由 build() 决定「降级出报告」还是「真的没数据」。
    半份数据比没有更危险（读数偏低、7% 告警漏报），所以 <100 只也按失败处理。
    """
    try:
        return _fetch_szse_strict()
    except Exception as e:  # noqa: BLE001
        print(f"[etf_scale] 深交所 ETF 取数失败，本次按缺失处理: "
              f"{type(e).__name__}: {e}", flush=True)
        return pd.DataFrame(columns=["code", "name", "vol", "date"])


def fetch_nav():
    """场内基金最新单位净值：多源降级，取不到就返回空 dict（不抛）。

    2026-11 起的现实：``fund_etf_fund_daily_em`` 已在 akshare 1.20.x 移除，
    东财 push2 还会对高频调用限流（Push2RateLimitError）。原来的单源写法会让
    整个「宽基 ETF 规模」块直接崩掉。这里按「老接口 → spot 快照(IOPV/最新价)」
    逐级降级，全失败返回 ``({}, "")``——上游按「净值缺失」处理，份额数据照常保留。
    """
    import akshare as ak

    def _from_daily():
        if not hasattr(ak, "fund_etf_fund_daily_em"):
            return None
        nav = ak.fund_etf_fund_daily_em()
        col = [c for c in nav.columns if str(c).endswith("单位净值")][0]
        nav["code"] = nav["基金代码"].astype(str).str.zfill(6)
        nav["nav"] = pd.to_numeric(nav[col], errors="coerce")
        return nav.set_index("code")["nav"].to_dict(), str(col).replace("-单位净值", "")

    def _from_spot():
        if not hasattr(ak, "fund_etf_spot_em"):
            return None
        df = ak.fund_etf_spot_em()
        cols = [c for c in df.columns if "IOPV" in str(c)]
        cols = cols or [c for c in df.columns if "最新" in str(c) and "价" in str(c)]
        if not cols:
            return None
        col = cols[0]
        df = df.copy()
        df["code"] = df["代码"].astype(str).str.zfill(6)
        df["nav"] = pd.to_numeric(df[col], errors="coerce")
        return df.set_index("code")["nav"].to_dict(), "%s(近似)" % col

    for name, fn in (("fund_etf_fund_daily_em", _from_daily),
                     ("fund_etf_spot_em", _from_spot)):
        try:
            got = fn()
        except Exception as e:  # noqa: BLE001
            print(f"[etf_scale] 净值源 {name} 不可用，尝试下一个: "
                  f"{type(e).__name__}: {str(e)[:120]}", flush=True)
            continue
        if got:
            return got
    print("[etf_scale] 净值全部源不可用，本次按「净值缺失」处理"
          "（份额数据保留，规模列置空）", flush=True)
    return {}, ""


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
    try:
        sse = fetch_sse(date_str)
    except Exception as e:  # noqa: BLE001
        print(f"[etf_scale] 上交所 ETF 取数失败: {type(e).__name__}: {e}", flush=True)
        sse = pd.DataFrame(columns=["code", "name", "vol", "date"])
    szse = fetch_szse()
    print(f"[etf_scale] 上交所 {len(sse)} 行 / 深交所 {len(szse)} 行", flush=True)

    missing = [k for k, df in (("sse", sse), ("szse", szse)) if not len(df)]
    if len(missing) == 2:
        # 两边都取不到 = 真的没有数据。不能编，直接失败让 fetch 阶段告警。
        raise RuntimeError("ETF 份额两边交易所都取不到，被动化率无法计算")

    # 少一边会把被动化率算低 ~2pp，7% 提醒线因此漏报 —— 这是「错信号」，比没有报告更糟；
    # 但整条链一起停也不对，均线/基差/情绪当天照样要看。
    # 折中：照常出报告，把「读数不可用」一路传到 calc_clearing / 告警 / 报告，
    # 由它们明确标注并拒绝做 7% 判定；只有两边都缺才真的失败。
    degraded = bool(missing)
    if degraded:
        print(f"[etf_scale] ⚠️ 缺 {'/'.join(missing)} ETF 份额，被动化率偏低，"
              f"本次读数不可用于 7% 判定", flush=True)

    navmap, nav_date = fetch_nav()
    etf = pd.concat([sse, szse], ignore_index=True)
    etf = etf.dropna(subset=["code", "vol"])
    # vol 来自交易所接口、nav 来自东财，任何一侧是字符串都会让 scale 变成 object，
    # 下游 nlargest/sum 会直接报 TypeError（pandas 2.x）。统一先转数值。
    etf["vol"] = pd.to_numeric(etf["vol"], errors="coerce")
    etf["nav"] = pd.to_numeric(
        etf["code"].map(lambda c: navmap.get(str(c).zfill(6))), errors="coerce")
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
        # 降级标记：下游据此拒绝做 7% 判定，避免拿偏低读数给出错信号
        "degraded": degraded,
        "missing_exchanges": missing,
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
