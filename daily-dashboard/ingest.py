#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest.py — 把「AI股票群」全部历史报告 + 期权库，统一落进单一事实库 facts.db

设计：一张长表 facts(date, domain, entity, metric, value_num, value_text)
     - 新增栏目只需加解析器，不动表结构
     - 幂等：同 (date,domain,entity,metric) 覆盖写
     - 可追溯：每条记录带 source（来源文件名）

用法:
    python3 ingest.py            # 全量增量（跳过已入库文件）
    python3 ingest.py --rebuild  # 清库重建
"""
import os, re, sys, glob, sqlite3, datetime

# 数据源根目录（各任务产出的 md/报告都在这下面）。可用 DASH_BASE 覆盖。
import os
BASE = os.environ.get("DASH_BASE", "/Coze/Drive/蓁宝/所有对话/主对话")
HERE = os.path.dirname(os.path.abspath(__file__))

# 解析器版本：改动任何 parse_* 逻辑后必须升版，否则增量模式会跳过已入库文件
PARSER_VER = "2026-11-24a"
DB = os.path.join(HERE, "facts.db")
OPT_DB = os.path.join(BASE, "市场情绪监控/data/options_cache/style_drift.db")

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
IDX = ["沪深300", "中证500", "中证1000", "创业板指", "科创50", "万得全A"]
OPT_UND = {"510300": "沪深300ETF", "510500": "中证500ETF", "159915": "创业板ETF", "588000": "科创50ETF"}

# ---------------------------------------------------------------- 基础工具

def rd(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("−", "-").replace("–", "-")
    m = re.search(r"[-+]?\d+\.?\d*", s)
    return float(m.group()) if m else None


def clean(s):
    if s is None:
        return ""
    s = re.sub(r"[*`>]", "", str(s))
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def md_rows(seg, max_lines=90):
    """从片段里取第一段（可含引用块穿插）markdown 表格"""
    rows, started, skip = [], False, 0
    for t in seg.split("\n")[:max_lines]:
        t = t.strip()
        if t.startswith("|"):
            cells = [c.strip() for c in t.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                started, skip = True, 0
                continue
            rows.append(cells)
            started, skip = True, 0
        elif started:
            skip += 1
            if skip >= 2:
                break
    return rows


def tbl(t, anchor=None, pattern=None, max_lines=90):
    if pattern:
        m = re.search(pattern, t)
        if not m:
            return []
        return md_rows(t[m.end():], max_lines)
    if anchor:
        i = t.find(anchor)
        if i < 0:
            return []
        return md_rows(t[i + len(anchor):], max_lines)
    return []


def seg(t, start_pat, end_pat=None):
    m = re.search(start_pat, t)
    if not m:
        return ""
    rest = t[m.end():]
    if end_pat:
        m2 = re.search(end_pat, rest)
        if m2:
            rest = rest[:m2.start()]
    return rest


def d2date(s):
    """20261120 / 2026-11-20 -> 2026-11-20"""
    s = str(s)
    if re.fullmatch(r"\d{8}", s):
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return s[:10]


def date_of(path):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return m.group(1) if m else None


class Facts:
    def __init__(self):
        self.rows = []

    def add(self, date, domain, entity, metric, val, text=None):
        if not date:
            return
        n = val if isinstance(val, (int, float)) and not isinstance(val, bool) else num(val)
        if text is None and not isinstance(val, (int, float)):
            text = clean(val)
        text = clean(text) if text is not None else None
        if n is None and not text:
            return
        self.rows.append((date, domain, clean(entity), clean(metric), n, text))


# ---------------------------------------------------------------- 解析器

def parse_risk(path, date, F):
    """风险预测日报（盘后版）"""
    t = rd(path)
    if not t:
        return 0

    # 综合评级（万得全A）
    s = seg(t, r"##\s*🏆\s*综合风险评级", r"\n##\s")
    if s:
        m = re.search(r"综合得分[:：]\s*([-+]?\d+\.?\d*)", s)
        if m:
            F.add(date, "index", "万得全A", "score", m.group(1))
        m = re.search(r"评级[:：]\s*([^\n\|]+)", s)
        if m:
            F.add(date, "index", "万得全A", "rating", None, m.group(1))
        for r in tbl(s, pattern=r"\|"):
            if len(r) >= 3 and clean(r[0]) in ("结构风险", "量能情绪风险", "极端风险"):
                k = {"结构风险": "struct", "量能情绪风险": "volume", "极端风险": "extreme"}[clean(r[0])]
                F.add(date, "index", "万得全A", k, r[1])
                F.add(date, "index", "万得全A", k + "_state", None, r[2])

    # 各指数风险对比总表（表头随版本变化，动态映射）
    rows = tbl(t, anchor="各指数风险对比")
    if rows:
        head = [clean(x) for x in rows[0]]
        for r in rows[1:]:
            name = clean(r[0])
            if name not in IDX:
                continue
            d = {head[i]: (r[i] if i < len(r) else None) for i in range(min(len(head), len(r)))}
            for h, k in (("结构", "struct"), ("量能情绪", "volume"), ("极端", "extreme")):
                v = d.get(h)
                if v and "N/A" not in v and num(v) is not None:
                    F.add(date, "index", name, k, v)
            if d.get("综合评级"):
                F.add(date, "index", name, "rating", None, d["综合评级"])

    # 各模块明细表（编号会漂移，用正则锚）
    for kw, k in (("结构风险", "struct"), ("量能情绪风险", "volume"), ("极端风险", "extreme")):
        rows = tbl(t, pattern=r"###\s*[" + CIRCLED + r"]\s*" + kw)
        if not rows:
            continue
        head = [clean(x) for x in rows[0]]
        for r in rows[1:]:
            name = clean(r[0])
            if name not in IDX:
                continue
            d = {head[i]: (r[i] if i < len(r) else None) for i in range(len(head))}
            v = d.get("综合")
            if v and "N/A" not in v and num(v) is not None:
                F.add(date, "index", name, k, v)
            cfc = d.get("CFC")
            if cfc and "N/A" not in cfc:
                m = re.search(r"(\d+)%分位", cfc)
                if m:
                    F.add(date, "index", name, "cfc_pct", m.group(1))
                F.add(date, "index", name, "cfc_text", None, cfc)
            up = d.get("up50")
            if up and "N/A" not in up:
                m = re.search(r"(\d+\.?\d*)%", up)
                if m:
                    F.add(date, "index", name, "up50_pct", m.group(1))
                F.add(date, "index", name, "up50_text", None, up)

    # ④ 两融风险监控
    s = seg(t, r"###\s*[" + CIRCLED + r"]\s*两融风险监控", r"###\s*[" + CIRCLED + r"]\s*三维择时")
    if s:
        d0 = date
        m = re.search(r"两融数据日期[:：]\s*(\d{8})", s)
        if m:
            d0 = d2date(m.group(1))
        pats = [
            (r"融资余额\s*\**\s*([\d\.]+)\s*亿", "融资余额_亿"),
            (r"两融\s*\**\s*([\d\.]+)\s*亿", "两融余额_亿"),
            (r"流通市值\s*\**\s*([\d\.]+)\s*万亿", "流通市值_万亿"),
            (r"两市成交\s*\**\s*([\d\.]+)\s*万亿", "两市成交_万亿"),
            (r"融资/流通\s*\**\s*([\d\.]+)\s*%", "融资占流通_pct"),
            (r"加速度[:：]\s*\**\s*([-+]?[\d\.]+)\s*%", "加速度_pct"),
            (r"融资买入/成交额[:：]\s*([\d\.]+)\s*%", "融资买入占成交_pct"),
        ]
        for p, k in pats:
            m = re.search(p, s)
            if m:
                F.add(d0, "margin", "全市场", k, m.group(1))
        m = re.search(r"融资/流通市值[:：]\s*([\d\.]+)%[，,]\s*历史分位\s*([\d\.]+)%", s)
        if m:
            F.add(d0, "margin", "全市场", "融资占流通_pct", m.group(1))
            F.add(d0, "margin", "全市场", "历史分位_pct", m.group(2))
        m = re.search(r"融资变化率[:：]\s*5日([-+]?[\d\.]+)%.*?10日([-+]?[\d\.]+)%.*?20日([-+]?[\d\.]+)%", s, re.S)
        if m:
            F.add(d0, "margin", "全市场", "变化率5日_pct", m.group(1))
            F.add(d0, "margin", "全市场", "变化率10日_pct", m.group(2))
            F.add(d0, "margin", "全市场", "变化率20日_pct", m.group(3))
        if "强平连锁风险" in s:
            m = re.search(r"强平连锁风险\**\s*([^\n（(]*)（(\d+)只）", s)
            F.add(d0, "margin", "全市场", "强平连锁", 1 if m else None,
                  ("触发 %s只" % m.group(2)) if m else "触发")
        else:
            F.add(d0, "margin", "全市场", "强平连锁", 0, "未触发")
        if "227%担保比例等效信号已触发" in s:
            F.add(d0, "margin", "全市场", "担保比例等效信号", 1, "227%等效信号已触发")
        else:
            F.add(d0, "margin", "全市场", "担保比例等效信号", 0, "未触发")
        for r in tbl(s, pattern=r"一、风险监控"):
            pass
        # 机会监控逐条
        for kw, k in (("融资出清", "融资出清"), ("超跌反弹候选", "超跌反弹候选"),
                      ("底部确认信号", "底部确认信号"), ("风格切换", "风格切换")):
            m = re.search(r"^.*?" + kw + r".*$", s, re.M)
            if m:
                line = clean(m.group(0))
                st = "触发" if ("未触发" not in line and "无" not in line[:6]) else "未触发"
                F.add(d0, "margin", "全市场", k, 1 if st == "触发" else 0, line[:60])

    # ⑤ 三维择时监控
    s = seg(t, r"###\s*[" + CIRCLED + r"]\s*三维择时")
    if s:
        m = re.search(r"择时综合评级[:：]\s*([^\n]+)", s)
        if m:
            F.add(date, "timing", "综合", "评级", None, m.group(1))
        for kw, k in (("看多", "看多数"), ("中性", "中性数"), ("看空", "看空数"), ("数据缺失", "缺失数")):
            m = re.search(kw + r"[:：]\s*(\d+)\s*个维度", s)
            if m:
                F.add(date, "timing", "综合", k, m.group(1))
        m = re.search(r"\*\*整体仓位\*\*[:：]\s*([^\n]+)", s)
        if m:
            F.add(date, "timing", "综合", "整体仓位", None, m.group(1))
        for r in tbl(s, anchor="各维度明细"):
            if len(r) >= 3 and clean(r[0]) and not clean(r[0]).startswith("维度"):
                F.add(date, "timing", clean(r[0]), "信号", None, r[1])
                F.add(date, "timing", clean(r[0]), "说明", None, r[2])
        for r in tbl(s, anchor="ETF资金流详情"):
            if len(r) >= 7 and re.match(r"^\d{6}\.", clean(r[0])):
                code = clean(r[0])[:6]
                F.add(date, "timing", code, "ETF份额日变化_pct", r[3])
                F.add(date, "timing", code, "ETF份额周变化_pct", r[5])

    # 核心风险信号 TOP3 / 潜在机会（文本留档）
    s = seg(t, r"##\s*⚠️\s*核心风险信号", r"\n##\s")
    if s:
        for i, ln in enumerate([x for x in s.split("\n") if re.match(r"^\d+\.\s", x.strip())][:3], 1):
            F.add(date, "signal", "风险TOP%d" % i, "text", None, ln.strip())
    s = seg(t, r"##\s*✅\s*潜在机会信号", r"\n##\s")
    if s:
        for i, ln in enumerate([x for x in s.split("\n") if x.strip().startswith("-")][:4], 1):
            F.add(date, "signal", "机会%d" % i, "text", None, ln.strip())
    return 1


def parse_macro(path, date, F):
    """环流日报（美元环流 + 中国流动性 + 掉期 + HIBOR + JGB）"""
    t = rd(path)
    if not t:
        return 0

    m = re.search(r"##\s*🚨\s*告警汇总（报警\s*(\d+)\s*/\s*警告\s*(\d+)）", t)
    if m:
        F.add(date, "usd", "综合", "报警数", m.group(1))
        F.add(date, "usd", "综合", "警告数", m.group(2))

    # Q 水池
    s = seg(t, r"###\s*💧\s*Q:", r"###\s*🏛️")
    for r in tbl(s, pattern=r"\|"):
        k = clean(r[0]) if r else ""
        if "准备金余额" in k and len(r) >= 5:
            F.add(date, "usd", "准备金", "余额_万亿", r[2])
            F.add(date, "usd", "准备金", "周变化_pct", r[3])
            F.add(date, "usd", "准备金", "状态", None, r[4])
        elif "ON RRP" in k and len(r) >= 5:
            F.add(date, "usd", "ONRRP", "余额_十亿", r[2])
            F.add(date, "usd", "ONRRP", "状态", None, r[4])
        elif k == "TGA" and len(r) >= 5:
            F.add(date, "usd", "TGA", "余额_万亿", r[2])
            F.add(date, "usd", "TGA", "周变化_pct", r[3])

    # TGA 日度（DTS）
    s = seg(t, r"###\s*🏛️\s*TGA 日度变动", r"###\s*🫧")
    d0 = date
    m = re.search(r"数据日期\**:?\s*(\d{4}-\d{2}-\d{2})", s)
    if m:
        d0 = m.group(1)
    for r in tbl(s, pattern=r"\|"):
        k = clean(r[0]) if r else ""
        if k == "TGA 收盘余额" and len(r) >= 2:
            F.add(d0, "usd", "TGA", "余额_十亿", r[2])
        elif "当日净变动" in k and len(r) >= 2:
            F.add(d0, "usd", "TGA", "日变动_十亿", r[2])
    for r in tbl(s, pattern=r"发债融资 vs 债务到期"):
        k = clean(r[0]) if r else ""
        if "净发债" in k and len(r) >= 2:
            F.add(d0, "usd", "TGA", "净发债_十亿", r[2])
    for r in tbl(s, pattern=r"QT进程"):
        k = clean(r[0]) if r else ""
        if k == "总资产" and len(r) >= 3:
            F.add(d0, "usd", "美联储", "总资产_万亿", r[2])
        elif k == "美国国债" and len(r) >= 3:
            F.add(d0, "usd", "美联储", "持有国债_万亿", r[2])

    # g 含氧量
    s = seg(t, r"###\s*🫧\s*g:", r"###\s*🌡️")
    for r in tbl(s, pattern=r"\|"):
        k = clean(r[0]) if r else ""
        if k == "SOFR" and len(r) >= 2:
            F.add(date, "usd", "SOFR", "利率_pct", r[1])
        elif k == "IORB" and len(r) >= 2:
            F.add(date, "usd", "IORB", "利率_pct", r[1])
        elif "SOFR-IORB" in k and len(r) >= 2:
            F.add(date, "usd", "SOFR", "利差_bp", r[1])

    # P 水温（美债曲线）
    s = seg(t, r"###\s*🌡️\s*P:", r"###\s*📊")
    for r in tbl(s, pattern=r"\|"):
        if not r:
            continue
        k = clean(r[0]).replace("*", "")
        if k in ("2Y", "5Y", "10Y", "30Y") and len(r) >= 2:
            F.add(date, "usd", "UST" + k, "收益率_pct", r[1])
            if len(r) >= 3:
                F.add(date, "usd", "UST" + k, "日变动_bp", r[2])
    m = re.search(r"2Y-10Y利差[:：]\s*([-+]?\d+\.?\d*)bp", t)
    if m:
        F.add(date, "usd", "利差2Y10Y", "利差_bp", m.group(1))

    # 美债 2Y/10Y 深度
    for ten in ("2Y", "10Y"):
        s = seg(t, r"###\s*📊\s*美债" + ten + r"深度追踪", r"###\s*")
        m = re.search(r"当前值\**:?\s*([\d\.]+)%", s)
        if m:
            F.add(date, "usd", "UST" + ten, "收益率_pct", m.group(1))
        m = re.search(r"近两年分位\**:?\s*([\d\.]+)%", s)
        if m:
            F.add(date, "usd", "UST" + ten, "两年分位_pct", m.group(1))

    # DXY
    s = seg(t, r"###\s*💵\s*美元指数", r"###\s*🔗")
    m = re.search(r"([\d]{2,3}\.\d+)", clean(s))
    if m:
        F.add(date, "usd", "DXY", "指数", m.group(1))

    # 中美利差
    s = seg(t, r"###\s*🔗\s*中美利差", r"###\s*💱")
    m = re.search(r"([-+]?\d+)\s*bp", clean(s), re.I)
    if m:
        F.add(date, "usd", "中美利差", "利差_bp", m.group(1))

    # 人民币汇率
    s = seg(t, r"###\s*💱\s*离岸/在岸人民币汇率", r"###\s*🔬")
    cs = clean(s)
    m = re.search(r"USD/CNH\**\s*[:：]\s*([\d\.]{5,8})", cs, re.I)
    if m:
        F.add(date, "usd", "USDCNH", "汇率", m.group(1))
    m = re.search(r"USD/CNY\**\s*[:：]\s*([\d\.]{5,8})", cs, re.I)
    if m:
        F.add(date, "usd", "USDCNY", "汇率", m.group(1))

    # 综合评级
    m = re.search(r"\*\*综合评级\*\*[:：]\s*([^\|\n]+)\|\s*评分\s*([\d\.]+)/10", t)
    if m:
        F.add(date, "usd", "综合", "评级", None, m.group(1))
        F.add(date, "usd", "综合", "评分", m.group(2))
    s = seg(t, r"##\s*六、综合评级")
    if s:
        items = [x.strip() for x in s.split("\n") if x.strip().startswith("-")]
        items = [x for x in items if len(clean(x)) > 4 and not re.fullmatch(r"[-\s]+", clean(x))]
        for i, ln in enumerate(items[:10], 1):
            F.add(date, "usd", "风险点%d" % i, "text", None, ln)

    # ---------------- 中国流动性
    # Shibor
    s = seg(t, r"###\s*📈\s*Shibor利率", r"###\s*📋")
    for r in tbl(s, pattern=r"\|"):
        if not r:
            continue
        k = clean(r[0])
        if k in ("O/N", "1W", "2W", "1M", "3M", "6M", "1Y") and len(r) >= 2:
            F.add(date, "cn_liq", "Shibor" + k, "利率_pct", r[1])
            if len(r) >= 3:
                F.add(date, "cn_liq", "Shibor" + k, "日变动_bp", r[2])
            if len(r) >= 4:
                F.add(date, "cn_liq", "Shibor" + k, "周变动_bp", r[3])
    # LPR
    s = seg(t, r"###\s*📋\s*LPR利率", r"###\s*🏛️")
    for r in tbl(s, pattern=r"\|"):
        if r and clean(r[0]) in ("1Y", "5Y") and len(r) >= 2:
            F.add(date, "cn_liq", "LPR" + clean(r[0]), "利率_pct", r[1])
    # 中国国债
    s = seg(t, r"###\s*🏛️\s*中国国债收益率", r"###\s*🏦")
    for r in tbl(s, pattern=r"\|"):
        if r and clean(r[0]) in ("1Y", "2Y", "5Y", "10Y", "30Y") and len(r) >= 2:
            F.add(date, "cn_liq", "CGB" + clean(r[0]), "收益率_pct", r[1])
            if len(r) >= 3:
                F.add(date, "cn_liq", "CGB" + clean(r[0]), "日变动_bp", r[2])
            if len(r) >= 4:
                F.add(date, "cn_liq", "CGB" + clean(r[0]), "周变动_bp", r[3])
    # 央行操作
    s = seg(t, r"###\s*🏦\s*央行流动性操作", r"###\s*📊\s*银行间回购利率分位数")
    m = re.search(r"逆回购余额\**\s*\|\s*\**\s*([\d,]+)", s)
    if m:
        F.add(date, "cn_liq", "OMO", "逆回购余额_亿", m.group(1))
    m = re.search(r"今日净投放\**\s*\|\s*\**\s*([-+]?[\d,]+)", s)
    if m:
        F.add(date, "cn_liq", "OMO", "净投放_亿", m.group(1))
    m = re.search(r"判定\**:?\s*\**([^\|\n]+?)\**\s*\|\s*紧张度\s*([\d\.]+)/100", s)
    if m:
        F.add(date, "cn_liq", "资金面", "判定", None, m.group(1))
        F.add(date, "cn_liq", "资金面", "紧张度", m.group(2))
    for k in ("DR001", "R001", "DR007"):
        m = re.search(r"\|\s*" + k + r"（[^）]*）\s*\|\s*([\d\.]+)%\s*\|\s*([-+]?[\d\.]+)bp", s)
        if m:
            F.add(date, "cn_liq", k, "利率_pct", m.group(1))
            F.add(date, "cn_liq", k, "偏离_bp", m.group(2))
    m = re.search(r"非银分层\s*R001-DR001\s*\|\s*([\d\.]+)bp", s)
    if m:
        F.add(date, "cn_liq", "资金面", "非银分层_bp", m.group(1))
    # 回购利率分位数
    s = seg(t, r"###\s*📊\s*银行间回购利率分位数", r"###\s*📈\s*银行间回购成交量")
    for r in tbl(s, pattern=r"\|"):
        if not r:
            continue
        k = clean(r[0]).split(" ")[0].replace("*", "")
        if k in ("DR001", "DR007", "DR014", "FR001", "FR007", "FR014", "R001", "R007") and len(r) >= 7:
            F.add(date, "cn_liq", k, "利率_pct", r[1])
            F.add(date, "cn_liq", k, "两年分位_pct", r[6])
            F.add(date, "cn_liq", k, "状态", None, r[7] if len(r) > 7 else None)
    # 回购成交量
    s = seg(t, r"###\s*📈\s*银行间回购成交量", r"###\s*🧾")
    for r in tbl(s, pattern=r"\|"):
        if not r:
            continue
        k = clean(r[0]).split(" ")[0]
        if k in ("DR001", "DR007", "DR014", "R001", "R007", "R014") and len(r) >= 2:
            F.add(date, "cn_liq", "量" + k, "成交量_亿", r[1])
    m = re.search(r"主要品种合计估算\**:?\s*([\d,]+)\s*亿元", s)
    if m:
        F.add(date, "cn_liq", "回购", "成交量合计_亿", m.group(1))
    m = re.search(r"MLF余额\**\s*\|\s*\**\s*([\d,]+)", s)
    if m:
        F.add(date, "cn_liq", "MLF", "余额_亿", m.group(1))
    # 1Y AAA 同业存单
    s = seg(t, r"###\s*🧾\s*1Y AAA同业存单流动性", r"###\s*🎯")
    m = re.search(r"快照\**:?\s*([\d\.]+)%（日\s*([-+]?[\d\.]+)bp），近1年分位\s*(\d+)%", s)
    if m:
        F.add(date, "cn_liq", "CD1Y_AAA", "利率_pct", m.group(1))
        F.add(date, "cn_liq", "CD1Y_AAA", "日变动_bp", m.group(2))
        F.add(date, "cn_liq", "CD1Y_AAA", "一年分位_pct", m.group(3))
    m = re.search(r"所处阶段\**:?\s*([^\n（]+)", s)
    if m:
        F.add(date, "cn_liq", "CD1Y_AAA", "阶段", None, m.group(1))
    m = re.search(r"日\s*([-+]?[\d\.]+)bp\s*\|\s*周\s*([-+]?[\d\.]+)bp\s*\|\s*月\s*([-+]?[\d\.]+)bp", s)
    if m:
        F.add(date, "cn_liq", "CD1Y_AAA", "周变动_bp", m.group(2))
        F.add(date, "cn_liq", "CD1Y_AAA", "月变动_bp", m.group(3))
    # 降准降息预期利差
    s = seg(t, r"###\s*🎯\s*降准降息预期利差", r"---")
    m = re.search(r"快照\**:?\s*1Y−3M国开\s*([-+]?[\d\.]+)bp，近1年分位\s*(\d+)%", s)
    if m:
        F.add(date, "cn_liq", "降准降息利差", "1Y减3M国开_bp", m.group(1))
        F.add(date, "cn_liq", "降准降息利差", "一年分位_pct", m.group(2))
    m = re.search(r"所处阶段\**:?\s*([^\n（]+)", s)
    if m:
        F.add(date, "cn_liq", "降准降息利差", "阶段", None, m.group(1))

    # ---------------- 1Y 掉期点
    s = seg(t, r"##\s*三、1Y掉期点", r"##\s*四、")
    for r in tbl(s, pattern=r"\|"):
        if r and "USD/CNY" in clean(r[0]) and len(r) >= 5:
            F.add(date, "swap", "USDCNY1Y", "年化掉期率_pct", r[1])
            F.add(date, "swap", "USDCNY1Y", "周环比_pp", r[2])
            F.add(date, "swap", "USDCNY1Y", "月环比_pp", r[3])
            F.add(date, "swap", "USDCNY1Y", "一年分位_pct", r[4])
    s2 = seg(t, r"###\s*📈\s*近一周1Y掉期点走势", r"###\s*📊")
    for r in tbl(s2, pattern=r"\|"):
        if r and re.match(r"^\d{4}-\d{2}-\d{2}$", clean(r[0])) and len(r) >= 2:
            F.add(clean(r[0]), "swap", "USDCNY1Y", "年化掉期率_pct", r[1])

    # ---------------- 港股 HIBOR
    s = seg(t, r"##\s*四、港股流动性", r"##\s*五、")
    for r in tbl(s, pattern=r"\|"):
        if r and "HIBOR" in clean(r[0]) and len(r) >= 3:
            k = clean(r[0]).replace("HIBOR", "").strip()
            F.add(date, "hibor", "HIBOR" + k, "利率_pct", r[1])
    m = re.search(r"HIBOR期限结构[:：]\s*ON\s*([\d\.]+)%.*?1M\s*([\d\.]+)%.*?3M\s*([\d\.]+)%.*?6M\s*([\d\.]+)%.*?12M\s*([\d\.]+)%", s)
    if m:
        for k, v in zip(("ON", "1M", "3M", "6M", "12M"), m.groups()):
            F.add(date, "hibor", "HIBOR" + k, "利率_pct", v)

    # ---------------- JGB
    s = seg(t, r"##\s*五、日本国债收益率", r"##\s*六、")
    for r in tbl(s, pattern=r"\|"):
        if not r:
            continue
        k = clean(r[0]).replace("*", "")
        if k in ("2Y", "5Y", "10Y", "20Y", "30Y", "40Y") and len(r) >= 2:
            F.add(date, "jgb", "JGB" + k, "收益率_pct", r[1])
    for k, lab in (("日环比", "日变动_bp"), ("周环比", "周变动_bp"), ("月环比", "月变动_bp"), ("年同比", "年变动_bp")):
        m = re.search(r"\|\s*" + k + r"\s*\|\s*([-+]?[\d\.]+)\s*\|", s)
        if m:
            F.add(date, "jgb", "JGB10Y", lab, m.group(1))
    m = re.search(r"美日10Y利差[:：]\s*([-+]?[\d\.]+)%", s)
    if m:
        F.add(date, "jgb", "美日利差", "利差_pct", m.group(1))
    return 1


def parse_liq_risk(path, date, F):
    """市场流动性与风险偏好"""
    t = rd(path)
    if not t:
        return 0
    m = re.search(r"##\s*综合评分[:：]\s*([\d\.]+)/100\s*(\S*)", t)
    if m:
        F.add(date, "liq_risk", "综合", "评分", m.group(1))
        F.add(date, "liq_risk", "综合", "等级", None, m.group(2))
    s = seg(t, r"###\s*流动性指标", r"###\s*风险偏好指标")
    for r in tbl(s, pattern=r"\|"):
        pass
    for k in ("SHIBOR 7天", "全A成交额", "流动性评分"):
        m = re.search(r"- " + re.escape(k) + r"[:：]\s*([^\n]+)", t)
        if m:
            F.add(date, "liq_risk", "流动性", k.replace(" ", ""), m.group(1))
    s = seg(t, r"###\s*风险偏好指标", r"###\s*📊")
    for k in ("成交额集中度", "涨停/跌停", "北向资金", "风险偏好评分"):
        m = re.search(r"- " + re.escape(k) + r"[:：]\s*([^\n]+)", s)
        if m:
            F.add(date, "liq_risk", "风险偏好", k.replace(" ", ""), m.group(1))
    # 吸收率因子
    s = seg(t, r"###\s*📊\s*吸收率因子", r"###\s*⚠️")
    for r in tbl(s, pattern=r"\|"):
        if len(r) >= 2 and clean(r[0]) and clean(r[0]) != "指标":
            F.add(date, "liq_risk", "吸收率因子", clean(r[0]).replace("*", ""), None, r[1])
            n = num(r[1])
            if n is not None:
                F.add(date, "liq_risk", "吸收率因子", clean(r[0]).replace("*", "") + "_num", n)
    # 兴登堡
    s = seg(t, r"###\s*⚠️\s*兴登堡指标", r"###\s*💰")
    for r in tbl(s, pattern=r"\|"):
        if len(r) >= 2 and clean(r[0]) and clean(r[0]) != "指标":
            k = clean(r[0]).replace("*", "")
            F.add(date, "liq_risk", "兴登堡", k, None, r[1])
            m2 = re.search(r"([\d\.]+)", r[1])
            if m2:
                F.add(date, "liq_risk", "兴登堡", k + "_num", m2.group(1))
    # 两融余额（此报告口径）
    s = seg(t, r"###\s*💰\s*融资融券余额", r"###\s*📈")
    for r in tbl(s, pattern=r"\|"):
        if len(r) >= 2 and "两融总余额" in clean(r[0]):
            F.add(date, "liq_risk", "两融", "总余额_万亿", r[1])
            if len(r) >= 3:
                F.add(date, "liq_risk", "两融", "日变动_pct", r[2])
    m = re.search(r"历史分位[:：]\s*([\d\.]+)%", s)
    if m:
        F.add(date, "liq_risk", "两融", "历史分位_pct", m.group(1))
    # 期权IV均线
    s = seg(t, r"###\s*📈\s*期权IV均线监控", r"\Z")
    for r in tbl(s, pattern=r"\|"):
        if len(r) >= 5 and clean(r[0]) and clean(r[0]) != "标的" and "缺失" not in r[1]:
            F.add(date, "liq_risk", "IV均线" + clean(r[0]), "当前IV_pct", r[1])
            F.add(date, "liq_risk", "IV均线" + clean(r[0]), "信号", None, r[4])
    return 1


def parse_struct(path, date, F):
    """结构因子日报（AbsFactor / RelFactor）"""
    t = rd(path)
    if not t:
        return 0
    rows = tbl(t, pattern=r"RelFactor说明")
    if not rows:
        rows = tbl(t, pattern=r"\| 指数 \|")
    for r in rows:
        if not r:
            continue
        name = clean(r[0])
        if name not in IDX or len(r) < 8:
            continue
        F.add(date, "struct_factor", name, "AbsFactor", r[1])
        F.add(date, "struct_factor", name, "Abs状态", None, r[2])
        F.add(date, "struct_factor", name, "RelFactor", r[3])
        F.add(date, "struct_factor", name, "Rel状态", None, r[4])
        F.add(date, "struct_factor", name, "价格分位_pct", r[5])
        if clean(r[6]) not in ("-", "—", ""):
            F.add(date, "struct_factor", name, "赔率信号", None, r[6])
        F.add(date, "struct_factor", name, "综合信号", None, r[7])
    m = re.search(r"🔑 关键信号[:：]\s*\n+\s*([^\n]+)", t)
    if m:
        F.add(date, "struct_factor", "综合", "关键信号", None, m.group(1))
    return 1


def parse_bond(path, date, F):
    """国债期货四因子周报"""
    t = rd(path)
    if not t:
        return 0
    for r in tbl(t, anchor="评分总表"):
        if not r:
            continue
        k = clean(r[0])
        if k in ("政策信号", "资金信号", "情绪信号", "股债信号", "总分") and len(r) >= 4:
            F.add(date, "bond", k, "权重", None, r[1])
            F.add(date, "bond", k, "评分", r[2])
            if k != "总分" and len(r) >= 5:
                F.add(date, "bond", k, "说明", None, r[4])
    m = re.search(r"###\s*方向判断\s*\n+\s*([^\n]+)", t)
    if m:
        F.add(date, "bond", "方向", "判断", None, m.group(1))
    m = re.search(r"数据自动采集覆盖率[:：]\s*([\d\.]+)%", t)
    if m:
        F.add(date, "bond", "数据", "覆盖率_pct", m.group(1))
    s = seg(t, r"###\s*⚠️\s*缺失数据")
    miss = [x.strip("- ").strip() for x in s.split("\n") if x.strip().startswith("-")]
    if miss:
        F.add(date, "bond", "数据", "缺失项", None, " / ".join(miss))
    return 1


def parse_cycle(path, date, F):
    """库存周期 & 信贷周期月报"""
    t = rd(path)
    if not t:
        return 0
    rows = tbl(t, anchor="周期状态总览")
    for r in rows[1:]:
        if len(r) >= 3 and clean(r[0]) in ("信贷周期", "库存周期"):
            F.add(date, "cycle", clean(r[0]), "状态", None, r[1])
            F.add(date, "cycle", clean(r[0]), "本轮起始", None, r[2])
            if len(r) >= 4:
                F.add(date, "cycle", clean(r[0]), "阶段起始", None, r[3])
    s = seg(t, r"##\s*五、综合研判", r"##\s*六、")
    m = re.search(r"信贷周期分析\s*\n+([^\n#]+)", s)
    if m:
        F.add(date, "cycle", "信贷周期", "研判", None, m.group(1))
    m = re.search(r"库存周期分析\s*\n+([^\n#]+)", s)
    if m:
        F.add(date, "cycle", "库存周期", "研判", None, m.group(1))
    return 1


def parse_concept(path, date, F):
    """热点概念 TOP20（只留板块聚合，不留个股）"""
    t = rd(path)
    if not t:
        return 0
    rows = tbl(t, anchor="近两周热点概念TOP20")
    if not rows:
        rows = tbl(t, pattern=r"##\s*近两周热点概念TOP20")
    for r in rows[1:]:
        if len(r) < 5 or not re.fullmatch(r"\d+", clean(r[0])):
            continue
        name = clean(r[1])
        F.add(date, "concept", name, "排名", r[0])
        F.add(date, "concept", name, "两周涨幅_pct", r[2])
        F.add(date, "concept", name, "上涨比例", None, r[3])
        if len(r) >= 7:
            F.add(date, "concept", name, "热点逻辑", None, r[6])
    return 1


def ingest_options(conn, F):
    """期权库 daily_metrics -> facts"""
    if not os.path.exists(OPT_DB):
        return 0
    src = sqlite3.connect("file:%s?mode=ro" % OPT_DB, uri=True)
    try:
        cols = [c[1] for c in src.execute("PRAGMA table_info(daily_metrics)")]
    except Exception:
        return 0
    want = [c for c in ("atm_iv", "iv_skew", "vix", "vol_pcr", "oi_pcr", "amt_pcr", "spot_price",
                        "put_25d_iv", "call_25d_iv") if c in cols]
    if not want or "trade_date" not in cols or "underlying" not in cols:
        return 0
    q = "SELECT trade_date, underlying, %s FROM daily_metrics ORDER BY trade_date" % ",".join(want)
    n = 0
    for row in src.execute(q):
        td, und = str(row[0]), str(row[1])
        d = d2date(td)
        for i, c in enumerate(want):
            v = row[2 + i]
            if v is None:
                continue
            F.add(d, "option", und, c, v)
            n += 1
    src.close()
    return n


# ------------------------------------------------------- 指标仓直读（2026-11-27）
# 背景：任务侧早就在往 data_hub.tech 指标仓落库，dashboard 却只读 md 报告 ——
# 同一份口径两条腿各走各的，页面还会因为 md 没生成而停在几天前。
# 这里加一条直读通道，策略是「库优先、md 补历史」：
#   · 库里的值覆盖 md 同名 (date, domain, entity, metric)，因为库更新更及时、
#     标的更全（risk 域 6 个宽基都有，md 里部分标的常缺）；
#   · 库只有 2026-11-13 起的数据，更早的历史仍然由 md 解析器提供，不会丢。
# 只映射能确认同口径的指标。up50 / cfc 这类库里只有数值、没有原文分级口径的，
# 继续走 md，避免凭空造出一套阈值把页面口径改掉。

TECH_DB = os.path.expanduser("~/.local/share/cozedb/tech_metrics.db")

# security_id → facts 里的 entity 名（必须和 parse_risk 里一字不差，否则前端取不到）
SID2ENTITY = {
    "881001.WI": "万得全A",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000688.SH": "科创50",
    "399006.SZ": "创业板指",
}

# 已知脏数据：881001.WI（万得全A）的 risk.struct_score 在 2026-11-13~11-24 恒定 -0.5，
# 是 producer 取数失败时的兜底值 —— 同期 md 的真实值是 0.12~0.5，且同标的的 volume /
# extreme / score 三项库与 md 完全一致，可确认只有这一个指标坏了。此处排除不采信，
# 继续用 md 解析结果。任务侧修好 risk_daily 对万得全A 的取数后，把这条删掉即可。
TECH_EXCLUDE = {
    ("881001.WI", "risk.struct_score"),
}

# metric_key → (facts 域, facts 指标, 是否取 value_text)
TECH_MAP = {
    "risk.struct_score":  ("index", "struct",  False),
    "risk.volume_score":  ("index", "volume",  False),
    "risk.extreme_score": ("index", "extreme", False),
    "risk.score_all_a":   ("index", "score",   False),
    "risk.grade":         ("index", "rating",  True),
}


def ingest_from_tech_db(conn, F, since=None):
    """从 data_hub 指标仓直读，返回写入行数。

    since: 只取该日期之后（YYYY-MM-DD）；None 表示全量。
    库不存在或读失败时静默回退到纯 md，不中断入库。
    """
    if not os.path.exists(TECH_DB):
        print("  [tech_db] 指标仓不存在，跳过（只用 md）")
        return 0
    keys = tuple(TECH_MAP)
    sql = ("SELECT metric_key, security_id, obs_date, value_num, value_text "
           "FROM metric_points WHERE metric_key IN (%s)"
           % ",".join("?" * len(keys)))
    args = list(keys)
    if since:
        sql += " AND obs_date >= ?"
        args.append(since)
    n = 0
    try:
        src = sqlite3.connect("file:%s?mode=ro" % TECH_DB, uri=True)
        for key, sid, dt, vnum, vtext in src.execute(sql, args):
            ent = SID2ENTITY.get(sid)
            if not ent or not dt:
                continue
            if (sid, key) in TECH_EXCLUDE:
                continue
            dom, met, use_text = TECH_MAP[key]
            if use_text:
                if not vtext:
                    continue
                F.add(dt, dom, ent, met, None, vtext)
            else:
                if vnum is None:
                    continue
                F.add(dt, dom, ent, met, vnum)
            n += 1
        src.close()
    except Exception as e:
        print("  [tech_db] 直读失败，回退纯 md：%s: %s" % (type(e).__name__, e))
        return 0
    return n


# ---------------------------------------------------------------- 主流程

SOURCES = [
    ("risk", os.path.join(BASE, "市场情绪监控/outputs"), "*风险预测日报.md", parse_risk),
    ("macro", os.path.join(BASE, "环流日报/outputs"), "*环流日报.md", parse_macro),
    ("liq_risk", os.path.join(BASE, "市场情绪监控/outputs"), "*流动性风险偏好*.md", parse_liq_risk),
    ("struct_factor", os.path.join(BASE, "结构因子分析/outputs"), "*结构因子日报*.md", parse_struct),
    ("bond", os.path.join(BASE, "国债期货四因子"), "*四因子周报.md", parse_bond),
    ("cycle", os.path.join(BASE, "库存信贷周期/outputs"), "*周期监控.md", parse_cycle),
    ("concept", os.path.join(BASE, "热点概念追踪/outputs"), "*热点概念TOP20*.md", parse_concept),
]


def main():
    rebuild = "--rebuild" in sys.argv
    if rebuild:
        for suf in ("", "-wal", "-shm"):
            p = DB + suf
            if os.path.exists(p):
                os.remove(p)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS facts (
      date TEXT NOT NULL,
      domain TEXT NOT NULL,
      entity TEXT NOT NULL,
      metric TEXT NOT NULL,
      value_num REAL,
      value_text TEXT,
      source TEXT,
      ingested_at TEXT,
      PRIMARY KEY (date, domain, entity, metric)
    );
    CREATE INDEX IF NOT EXISTS ix_facts_q ON facts(domain, entity, metric, date);
    CREATE TABLE IF NOT EXISTS ingest_log (
      source TEXT, file TEXT, date TEXT, rows INTEGER, status TEXT, msg TEXT, at TEXT,
      PRIMARY KEY (source, file)
    );
    """)
    # ingest_log 版本列迁移
    if "ver" not in [r[1] for r in c.execute("PRAGMA table_info(ingest_log)")]:
        c.execute("ALTER TABLE ingest_log ADD COLUMN ver TEXT")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    done = set(r[0] for r in c.execute(
        "SELECT file FROM ingest_log WHERE status='ok' AND ver=?", (PARSER_VER,)))
    F = Facts()
    stat = {}

    for name, d, pat, fn in SOURCES:
        files = sorted(glob.glob(os.path.join(d, pat)))
        for p in files:
            base = os.path.basename(p)
            if not rebuild and p in done:
                continue
            dt = date_of(p)
            if not dt:
                continue
            before = len(F.rows)
            try:
                fn(p, dt, F)
                added = len(F.rows) - before
                c.execute("INSERT OR REPLACE INTO ingest_log(source,file,date,rows,status,msg,at,ver) "
                          "VALUES(?,?,?,?,?,?,?,?)", (name, p, dt, added, "ok", "", now, PARSER_VER))
                stat[name] = stat.get(name, 0) + 1
            except Exception as e:
                c.execute("INSERT OR REPLACE INTO ingest_log(source,file,date,rows,status,msg,at,ver) "
                          "VALUES(?,?,?,?,?,?,?,?)", (name, p, dt, 0, "err", str(e)[:200], now, PARSER_VER))
                print("  [ERR] %s: %s" % (base, e))

    # 指标仓直读：必须放在 md 解析之后 —— F.rows 是列表，写库时 INSERT OR REPLACE，
    # 后写入的同 key 行覆盖前面的，这样才能让「库优先」成立。
    n_tech = ingest_from_tech_db(conn, F)
    if n_tech:
        print("  指标仓直读：%d 行（覆盖同名 md 解析值）" % n_tech)
        stat["tech_db"] = n_tech

    # 期权库
    before = len(F.rows)
    try:
        ingest_options(conn, F)
        c.execute("INSERT OR REPLACE INTO ingest_log(source,file,date,rows,status,msg,at,ver) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  ("option", OPT_DB, "", len(F.rows) - before, "ok", "", now, PARSER_VER))
        stat["option"] = 1
    except Exception as e:
        print("  [ERR] option:", e)

    # 写库：用文件名回填 source
    src_of = {}
    for name, d, pat, fn in SOURCES:
        for p in glob.glob(os.path.join(d, pat)):
            dt = date_of(p)
            if dt:
                src_of.setdefault((name, dt), os.path.basename(p))
    DOM2SRC = {"index": "risk", "signal": "risk", "margin": "risk", "timing": "risk",
               "usd": "macro", "cn_liq": "macro", "swap": "macro", "hibor": "macro", "jgb": "macro",
               "liq_risk": "liq_risk", "struct_factor": "struct_factor",
               "bond": "bond", "cycle": "cycle", "concept": "concept", "option": "option"}
    c.executemany(
        "INSERT OR REPLACE INTO facts VALUES(?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5],
          src_of.get((DOM2SRC.get(r[1], ""), r[0]), ""), now) for r in F.rows])
    conn.commit()

    tot = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    print("入库完成：新增 %d 条，库内合计 %d 条" % (len(F.rows), tot))
    print("文件统计：", ", ".join("%s=%d" % kv for kv in sorted(stat.items())))
    print("\n各域覆盖：")
    for dom, cnt, dmin, dmax in c.execute(
            "SELECT domain, COUNT(*), MIN(date), MAX(date) FROM facts GROUP BY domain ORDER BY COUNT(*) DESC"):
        print("  %-14s %6d 条  %s ~ %s" % (dom, cnt, dmin, dmax))
    errs = c.execute("SELECT source, file, msg FROM ingest_log WHERE status='err' LIMIT 10").fetchall()
    if errs:
        print("\n解析失败：")
        for e in errs:
            print("  ", os.path.basename(e[1]), "->", e[2])
    conn.close()


if __name__ == "__main__":
    main()
