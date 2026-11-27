#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_html.py — 从统一事实库 facts.db 生成单文件看板 index.html

数据全部来自 facts.db（由 ingest.py 从「AI股票群」已推送报告解析入库），
不重新采集行情、不做插值补值。
"""
import os, re, json, sqlite3, html, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "facts.db")
OUT = os.path.join(HERE, "index.html")
CSS = open(os.path.join(HERE, "tmpl_css.txt"), encoding="utf-8").read()
JSCORE = open(os.path.join(HERE, "tmpl_js_core.txt"), encoding="utf-8").read()

CONN = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
C = CONN.cursor()

IDX_ORDER = ["沪深300", "中证500", "中证1000", "创业板指", "科创50", "万得全A"]
IDX_COLORS = {"沪深300": "#58a6ff", "中证500": "#39d2c0", "中证1000": "#bc8cff",
              "创业板指": "#f85149", "科创50": "#d29922", "万得全A": "#8b949e"}
OPT_ORDER = ["510300", "510500", "159915", "588000"]
OPT_NAMES = {"510300": "沪深300ETF", "510500": "中证500ETF", "159915": "创业板ETF", "588000": "科创50ETF"}
OPT_COLORS = {"510300": "#58a6ff", "510500": "#39d2c0", "159915": "#f85149", "588000": "#d29922"}

CHARTS = []


# ---------------------------------------------------------------- 取数

def esc(x):
    return html.escape(str(x)) if x is not None and x != "" else ""


def last_num(dom, ent, met):
    r = C.execute("SELECT value_num FROM facts WHERE domain=? AND entity=? AND metric=? "
                  "AND value_num IS NOT NULL ORDER BY date DESC LIMIT 1", (dom, ent, met)).fetchone()
    return r[0] if r else None


def last_txt(dom, ent, met):
    r = C.execute("SELECT value_text,value_num,date FROM facts WHERE domain=? AND entity=? AND metric=? "
                  "AND (value_text IS NOT NULL AND value_text<>'') ORDER BY date DESC LIMIT 1",
                  (dom, ent, met)).fetchone()
    if r:
        return r[0]
    r = C.execute("SELECT value_num,date FROM facts WHERE domain=? AND entity=? AND metric=? "
                  "AND value_num IS NOT NULL ORDER BY date DESC LIMIT 1", (dom, ent, met)).fetchone()
    return r[0] if r else None


def last_date(dom):
    r = C.execute("SELECT MAX(date) FROM facts WHERE domain=?", (dom,)).fetchone()
    return r[0] if r and r[0] else "—"


def pick(dom, ent, cands):
    """cands=[(metric, mult)]，跨 metric 取日期最新的一条，返回 (date, value)"""
    best = None
    for m, mult in cands:
        r = C.execute("SELECT date,value_num FROM facts WHERE domain=? AND entity=? AND metric=? "
                      "AND value_num IS NOT NULL ORDER BY date DESC LIMIT 1", (dom, ent, m)).fetchone()
        if not r:
            continue
        if best is None or r[0] > best[0]:
            best = (r[0], r[1] * mult)
    return best


def ser_multi(dom, ent, cands):
    """cands=[(metric, mult)]，按日期合并成一条序列"""
    out = {}
    for m, mult in cands:
        for d, v in ser(dom, ent, m):
            out[d] = v * mult
    return sorted(out.items())


def ser(dom, ent, met):
    return C.execute("SELECT date,value_num FROM facts WHERE domain=? AND entity=? AND metric=? "
                     "AND value_num IS NOT NULL ORDER BY date", (dom, ent, met)).fetchall()


def merge(items, last_n=None):
    """[(name,color,[(date,val)])] -> (labels, series)"""
    dates = sorted(set(d for _, _, arr in items for d, _ in arr))
    if last_n:
        dates = dates[-last_n:]
    pos = {d: i for i, d in enumerate(dates)}
    out = []
    for name, color, arr in items:
        vals = [None] * len(dates)
        for d, v in arr:
            if d in pos:
                vals[pos[d]] = v
        out.append({"name": name, "color": color, "values": vals})
    return dates, out


def roll_pct(arr, window=252, min_n=20):
    """把 [(date,val)] 转成滚动分位序列 [(date,pct)]"""
    out = []
    for i, (d, v) in enumerate(arr):
        if v is None:
            out.append((d, None))
            continue
        sample = [x for _, x in arr[max(0, i - window + 1):i + 1] if x is not None]
        if len(sample) < min_n:
            out.append((d, None))
        else:
            out.append((d, round(100.0 * sum(1 for x in sample if x <= v) / len(sample), 1)))
    return out


def pct_rank(arr, window=252, min_n=20):
    vals = [v for v in arr if v is not None]
    if len(vals) < min_n:
        return None
    cur = vals[-1]
    sample = vals[-window:]
    return round(100.0 * sum(1 for x in sample if x <= cur) / len(sample), 1)


def chart(cid, aria, labels, series, height=250, vfmt=None, yfmt=None,
          legend=None, unit="", zero=False, padL=None, ymin=None, ymax=None):
    cfg = {"id": cid, "aria": aria, "labels": labels, "series": series, "height": height}
    if vfmt:
        cfg["vfmtKey"] = vfmt
    if yfmt:
        cfg["yfmtKey"] = yfmt
    if legend:
        cfg["legendId"] = legend
    if unit:
        cfg["unit"] = unit
    if zero:
        cfg["zeroBase"] = True
    if padL:
        cfg["padL"] = padL
    if ymin is not None:
        cfg["yMin"] = ymin
    if ymax is not None:
        cfg["yMax"] = ymax
    CHARTS.append(cfg)


def kpi(k, v, s="", cls=""):
    return ('<div class="kpi"><div class="k">%s</div><div class="v %s">%s</div>%s</div>'
            % (esc(k), cls, esc(v), ('<div class="s">%s</div>' % esc(s)) if s else ""))


def table(cap, head, rows, numcols=()):
    h = "".join('<th%s>%s</th>' % (' class="n"' if i in numcols else "", esc(x)) for i, x in enumerate(head))
    b = ""
    for r in rows:
        b += "<tr>" + "".join('<td%s>%s</td>' % (' class="n"' if i in numcols else "", c) for i, c in enumerate(r)) + "</tr>"
    if not rows:
        b = '<tr><td colspan="%d" style="color:var(--tx3)">暂无数据</td></tr>' % len(head)
    return ('<div class="tblwrap"><table><caption>%s</caption><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (esc(cap), h, b))


def block(title, inner, hint=""):
    return ('<div class="block"><h2>%s</h2>%s%s</div>'
            % (esc(title), ('<p class="hint">%s</p>' % esc(hint)) if hint else "", inner))


def fnum(v, dec=2, sign=False):
    if v is None:
        return "—"
    if isinstance(v, str):
        return esc(v)
    s = ("%+." if sign else "%.") + str(dec) + "f"
    return s % v


def cls_of(v):
    if v is None or isinstance(v, str):
        return ""
    if v > 0:
        return "c-red"
    if v < 0:
        return "c-green"
    return ""


def pcls(p):
    if p is None:
        return ""
    if p >= 90:
        return "c-red"
    if p >= 70:
        return "c-amber"
    if p <= 10:
        return "c-green"
    return ""


def colored(txt, cls):
    return '<span class="%s">%s</span>' % (cls, txt) if cls else txt


# ---------------------------------------------------------------- Tab1 指数风险

def build_index():
    d = last_date("index")
    kp, ch, tb = [], [], []

    score = last_num("index", "万得全A", "score")
    rate = last_txt("index", "万得全A", "rating")
    n_hi = sum(1 for n in IDX_ORDER if (last_num("index", n, "struct") or 0) >= 0.3)
    n_panic = 0
    for n in IDX_ORDER:
        t = last_txt("index", n, "up50_text") or ""
        if "超卖" in t:
            n_panic += 1
    kp.append(kpi("万得全A 综合得分", fnum(score, 3, True),
                  "%s · %s" % (rate or "—", "偏高" if (score or 0) > 0.3 else "偏低" if (score or 0) < -0.3 else "中性"),
                  "c-red" if (score or 0) > 0.3 else "c-green" if (score or 0) < -0.3 else "c-amber"))
    kp.append(kpi("结构风险偏高", "%d / %d" % (n_hi, len(IDX_ORDER)), "结构分 ≥ +0.30", "c-red" if n_hi >= 3 else "c-amber"))
    kp.append(kpi("up50 极度超卖", "%d / %d" % (n_panic, len(IDX_ORDER)), "恐慌区（<10%）", "c-red" if n_panic >= 3 else "c-amber"))
    kp.append(kpi("数据日期", d, "共 %d 份风险日报" % C.execute(
        "SELECT COUNT(DISTINCT date) FROM facts WHERE domain='index'").fetchone()[0], ""))

    # 结构风险走势
    items = [(n, IDX_COLORS[n], ser("index", n, "struct")) for n in IDX_ORDER]
    lb, ss = merge(items)
    chart("c-idx-struct", "宽基指数结构风险分走势", lb, ss, 250, "sign2", legend="l-idx-struct", zero=True)
    ch.append(block("结构风险分（各指数）",
                    '<div class="chart" id="c-idx-struct"></div><div class="legend" id="l-idx-struct"></div>',
                    "正值=结构风险偏高（抱团/集中度上升），负值=结构健康。数据来自风险预测日报①结构风险模块。"))

    items = [(n, IDX_COLORS[n], a) for n in IDX_ORDER
             for a in [ser("index", n, "cfc_pct")] if len(a) >= 5]
    lb, ss = merge(items)
    chart("c-idx-cfc", "宽基指数CFC集中度分位走势", lb, ss, 230, "f0", legend="l-idx-cfc", ymin=0, ymax=100)
    ch.append(block("CFC 集中度分位（0-100%）",
                    '<div class="chart" id="c-idx-cfc"></div><div class="legend" id="l-idx-cfc"></div>',
                    "CFC 分位越高=持仓越集中，结构风险越大。≥90% 极度集中（危险），≤10% 极度分散（机会）。中证500 的 CFC 在报告里全部标注 N/A，已从图中略去。"))

    items = [(n, IDX_COLORS[n], ser("index", n, "volume")) for n in IDX_ORDER]
    lb, ss = merge(items)
    chart("c-idx-volume", "宽基指数量能情绪风险分走势", lb, ss, 230, "sign2", legend="l-idx-volume", zero=True)
    ch.append(block("量能情绪风险分（各指数）",
                    '<div class="chart" id="c-idx-volume"></div><div class="legend" id="l-idx-volume"></div>',
                    "10 月日报仅覆盖万得全A，11 月起扩至各指数，早期曲线有断点。"))

    lb, ss = merge([("万得全A 综合得分", "#58a6ff", ser("index", "万得全A", "score"))])
    chart("c-idx-score", "万得全A综合风险得分走势", lb, ss, 200, "sign2", zero=True)
    ch.append(block("万得全A 综合风险得分（-1 ~ +1）",
                    '<div class="chart" id="c-idx-score"></div>',
                    "报告口径：正值=风险偏高，负值=风险偏低。"))

    # 最新风险分解表
    rows = []
    for n in IDX_ORDER:
        st = last_num("index", n, "struct")
        vo = last_num("index", n, "volume")
        ex = last_num("index", n, "extreme")
        cf = last_num("index", n, "cfc_pct")
        up = last_txt("index", n, "up50_text")
        rt = last_txt("index", n, "rating")
        rows.append([n,
                     colored(fnum(st, 2, True), cls_of(st)),
                     colored(fnum(vo, 2, True), cls_of(vo)),
                     colored(fnum(ex, 2, True), cls_of(ex)),
                     colored(("%.0f%%" % cf) if cf is not None else "—", pcls(cf)),
                     esc(up) if up else "—",
                     '<span class="badge">%s</span>' % esc(rt) if rt else "—"])
    tb.append(block("最新风险分解（%s）" % d,
                    table("各指数风险对比", ["指数", "结构", "量能情绪", "极端", "CFC 分位", "up50 信号", "综合评级"],
                          rows, numcols=(1, 2, 3, 4)),
                    "中证500 的结构风险在多数日报中标注 N/A（该指数 CFC/结构数据未覆盖）。"))

    # 结构因子
    sd = last_date("struct_factor")
    rows = []
    for n in IDX_ORDER:
        ab = last_num("struct_factor", n, "AbsFactor")
        rl = last_num("struct_factor", n, "RelFactor")
        if ab is None and rl is None:
            continue
        rows.append([n,
                     colored(fnum(ab, 2, True) + "σ", cls_of(ab)),
                     esc(last_txt("struct_factor", n, "Abs状态") or "—"),
                     colored(fnum(rl, 2, True) + "σ", cls_of(rl)),
                     esc(last_txt("struct_factor", n, "Rel状态") or "—"),
                     fnum(last_num("struct_factor", n, "价格分位_pct"), 1) + "%",
                     esc(last_txt("struct_factor", n, "综合信号") or "—")])
    if rows:
        key = last_txt("struct_factor", "综合", "关键信号") or ""
        tb.append(block("结构因子（AbsFactor / RelFactor）· %s" % sd,
                        table("RelFactor < 0 = 资金流入，> 0 = 资金流出/挤出", ["指数", "AbsFactor", "状态", "RelFactor", "状态", "价格分位", "综合信号"],
                              rows, numcols=(1, 3, 5)) + ('<p class="note">关键信号：%s</p>' % esc(key) if key else ""),
                        "数据来自结构因子日报，最新一份为 %s，比风险日报滞后。" % sd))

    # 风险 / 机会信号
    rd_ = last_date("signal")
    risk_rows, opp_rows = [], []
    for i in range(1, 4):
        t = last_txt("signal", "风险TOP%d" % i, "text")
        if t:
            risk_rows.append([str(i), esc(re.sub(r"^\d+\.\s*", "", t))])
    for i in range(1, 5):
        t = last_txt("signal", "机会%d" % i, "text")
        if t:
            opp_rows.append([str(i), esc(re.sub(r"^[-–]\s*", "", t))])
    if risk_rows:
        tb.append(block("核心风险信号 TOP3（%s）" % rd_,
                        table("", ["#", "信号"], risk_rows, numcols=(0,))))
    if opp_rows:
        tb.append(block("潜在机会信号（%s）" % rd_,
                        table("", ["#", "信号"], opp_rows, numcols=(0,))))

    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab2 期权

def build_option():
    kp, ch, tb = [], [], []
    d = last_date("option")
    stats = {}
    MULT = {"atm_iv": 100.0, "iv_skew": 100.0, "vix": 1.0, "vol_pcr": 1.0, "oi_pcr": 1.0, "spot_price": 1.0}
    for u in OPT_ORDER:
        stats[u] = {}
        for m in ("atm_iv", "iv_skew", "vix", "vol_pcr", "oi_pcr", "spot_price"):
            arr = [v * MULT[m] for _, v in ser("option", u, m)]
            stats[u][m] = (arr[-1] if arr else None, pct_rank(arr) if m in ("atm_iv", "iv_skew", "vix") else None)

    a = stats["510300"]
    kp.append(kpi("沪深300ETF ATM IV", "%.2f%%" % a["atm_iv"][0] if a["atm_iv"][0] is not None else "—",
                  "近252日分位 %.1f%%" % a["atm_iv"][1] if a["atm_iv"][1] is not None else "",
                  pcls(a["atm_iv"][1])))
    s5 = stats["588000"]
    kp.append(kpi("科创50ETF 偏度分位", "%.1f%%" % s5["iv_skew"][1] if s5["iv_skew"][1] is not None else "—",
                  "偏度 %.2f vol pt" % s5["iv_skew"][0] if s5["iv_skew"][0] is not None else "", pcls(s5["iv_skew"][1])))
    kp.append(kpi("VIX 分位（沪深300ETF）", "%.1f%%" % a["vix"][1] if a["vix"][1] is not None else "—",
                  "VIX %.2f" % a["vix"][0] if a["vix"][0] is not None else "", pcls(a["vix"][1])))
    kp.append(kpi("数据日期", d, "期权库 %d 个交易日" % C.execute(
        "SELECT COUNT(DISTINCT date) FROM facts WHERE domain='option'").fetchone()[0], ""))

    for mid, title, hint in (
            ("atm_iv", "ATM IV（平值隐含波动率）", "近 120 个交易日。IV 越高=期权越贵、市场越紧张。"),
            ("iv_skew", "IV 偏度（看跌-看涨）", "偏度走高=下行保护需求升温，尾部风险溢价抬升。"),
            ("vix", "VIX（CBOE 法自算）", "30 日年化隐含波动率，非 QVIX。未做插值。"),):
        mult = 100.0 if mid in ("atm_iv", "iv_skew") else 1.0
        items = [(OPT_NAMES[u], OPT_COLORS[u], [(d, v * mult) for d, v in ser("option", u, mid)]) for u in OPT_ORDER]
        lb, ss = merge(items, last_n=120)
        cid = "c-opt-" + mid
        chart(cid, title, lb, ss, 230, "f2", legend="l-" + cid)
        ch.append(block(title, '<div class="chart" id="%s"></div><div class="legend" id="l-%s"></div>' % (cid, cid), hint))

    for mid, title, hint in (
            ("atm_iv", "ATM IV 分位走势（滚动252日）", "分位 ≥90% = 期权贵、市场紧张；≤10% = 便宜、可买保护。"),
            ("iv_skew", "IV 偏度分位走势（滚动252日）", "偏度分位冲高=下行保护需求急升，往往是恐慌前兆。")):
        mult = 100.0
        items = [(OPT_NAMES[u], OPT_COLORS[u], roll_pct([(d, v * mult) for d, v in ser("option", u, mid)]))
                 for u in OPT_ORDER]
        lb, ss = merge(items, last_n=160)
        cid = "c-optp-" + mid
        chart(cid, title, lb, ss, 220, "f0", legend="l-" + cid, ymin=0, ymax=100)
        ch.append(block(title, '<div class="chart" id="%s"></div><div class="legend" id="l-%s"></div>' % (cid, cid), hint))

    rows = []
    for u in OPT_ORDER:
        s = stats[u]
        rows.append([OPT_NAMES[u] + "(" + u + ")",
                     fnum(s["spot_price"][0], 3),
                     colored(fnum(s["atm_iv"][0], 2) + "%", pcls(s["atm_iv"][1])),
                     colored(("%.1f%%" % s["atm_iv"][1]) if s["atm_iv"][1] is not None else "—", pcls(s["atm_iv"][1])),
                     colored(fnum(s["iv_skew"][0], 2) + " pt", pcls(s["iv_skew"][1])),
                     colored(("%.1f%%" % s["iv_skew"][1]) if s["iv_skew"][1] is not None else "—", pcls(s["iv_skew"][1])),
                     colored(fnum(s["vix"][0], 2), pcls(s["vix"][1])),
                     colored(("%.1f%%" % s["vix"][1]) if s["vix"][1] is not None else "—", pcls(s["vix"][1])),
                     fnum(s["vol_pcr"][0], 2), fnum(s["oi_pcr"][0], 2)])
    tb.append(block("期权指标最新快照（%s）" % d,
                    table("分位按近 252 个交易日计算；cboe_skew 字段因 510300 存在 349/451 天封顶 150.0 而弃用，统一用 iv_skew",
                          ["标的", "现价", "ATM IV", "IV 分位", "IV 偏度", "偏度分位", "VIX", "VIX 分位", "量 PCR", "仓 PCR"],
                          rows, numcols=(1, 2, 3, 4, 5, 6, 7, 8, 9))))
    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab3 美元环流

def build_usd():
    kp, ch, tb = [], [], []
    d = last_date("usd")
    rate = last_txt("usd", "综合", "评级")
    score = last_num("usd", "综合", "评分")
    res = last_num("usd", "准备金", "余额_万亿")
    onrrp = last_num("usd", "ONRRP", "余额_十亿")
    tga = (pick("usd", "TGA", [("余额_万亿", 1000.0), ("余额_十亿", 1.0)]) or (None, None))[1]
    spread = last_num("usd", "中美利差", "利差_bp")

    kp.append(kpi("美元流动性综合评级", "%s · %s/10" % (rate or "—", fnum(score, 0)),
                  "报警 %s / 警告 %s" % (fnum(last_num("usd", "综合", "报警数"), 0), fnum(last_num("usd", "综合", "警告数"), 0)),
                  "c-red" if (score or 10) <= 3 else "c-amber"))
    kp.append(kpi("准备金余额", "$%.2fT" % res if res else "—",
                  "低于 3 万亿警戒线" if res and res < 3 else "高于警戒线",
                  "c-red" if res and res < 3 else "c-green"))
    kp.append(kpi("ON RRP 缓冲垫", "$%.1fB" % onrrp if onrrp is not None else "—",
                  esc(last_txt("usd", "ONRRP", "状态") or ""), "c-red" if onrrp is not None and onrrp < 50 else ""))
    kp.append(kpi("TGA 余额", "$%.0fB" % tga if tga is not None else "—",
                  "日变动 %sB" % fnum(last_num("usd", "TGA", "日变动_十亿"), 0, True) if last_num("usd", "TGA", "日变动_十亿") is not None else "", ""))
    kp.append(kpi("中美10Y利差", "%+dbp" % spread if spread is not None else "—",
                  ("倒挂，资本外流压力" if spread < -100 else "顺挂") if spread is not None else "",
                  "c-red" if spread is not None and spread < -100 else ""))

    lb, ss = merge([("准备金（万亿$）", "#58a6ff", ser("usd", "准备金", "余额_万亿"))])
    chart("c-usd-res", "美联储准备金余额走势", lb, ss, 210, "f2", legend="l-c-usd-res")
    ch.append(block("美联储准备金余额（万亿$）", '<div class="chart" id="c-usd-res"></div><div class="legend" id="l-c-usd-res"></div>',
                    "3 万亿是市场公认的流动性警戒线，跌破后 SRF 可能被动用。"))

    lb, ss = merge([("TGA 余额（十亿$）", "#39d2c0", ser_multi("usd", "TGA", [("余额_万亿", 1000.0), ("余额_十亿", 1.0)])),
                    ("TGA 日变动（十亿$）", "#d29922", ser("usd", "TGA", "日变动_十亿"))])
    chart("c-usd-tga", "TGA账户余额与日变动", lb, ss, 230, "f1", legend="l-c-usd-tga", zero=True)
    ch.append(block("TGA 账户（财政部在美联储的余额）", '<div class="chart" id="c-usd-tga"></div><div class="legend" id="l-c-usd-tga"></div>',
                    "TGA 上升=抽走银行体系流动性；下降=释放流动性。日变动来自财政部 DTS 日度数据。"))

    items = [(t, c, ser("usd", "UST" + t, "收益率_pct")) for t, c in
             (("2Y", "#58a6ff"), ("5Y", "#39d2c0"), ("10Y", "#f85149"), ("30Y", "#bc8cff"))]
    lb, ss = merge(items)
    chart("c-usd-ust", "美债收益率曲线走势", lb, ss, 230, "f2", legend="l-c-usd-ust")
    ch.append(block("美债收益率曲线（%）", '<div class="chart" id="c-usd-ust"></div><div class="legend" id="l-c-usd-ust"></div>',
                    "10Y 上行=长端压力；2Y-10Y 利差反映衰退定价。"))

    lb, ss = merge([("中美利差（bp）", "#d29922", ser("usd", "中美利差", "利差_bp"))])
    chart("c-usd-li", "中美利差走势", lb, ss, 200, "f0", legend="l-c-usd-li", zero=True)
    ch.append(block("中美利差（bp）", '<div class="chart" id="c-usd-li"></div><div class="legend" id="l-c-usd-li"></div>',
                    "倒挂幅度越大，人民币贬值与资本外流压力越大。"))

    rows = []
    for ent, met, lab, unit, dec in (
            ("TGA", "余额_万亿", "TGA 余额", " 万亿$", 2),
            ("准备金", "余额_万亿", "准备金余额", " 万亿$", 2),
            ("ONRRP", "余额_十亿", "ON RRP", " 十亿$", 1),
            ("美联储", "总资产_万亿", "美联储总资产", " 万亿$", 2),
            ("UST2Y", "收益率_pct", "美债 2Y", "%", 2),
            ("UST10Y", "收益率_pct", "美债 10Y", "%", 2),
            ("利差2Y10Y", "利差_bp", "2Y-10Y 利差", "bp", 0),
            ("DXY", "指数", "美元指数 DXY", "", 2),
            ("中美利差", "利差_bp", "中美 10Y 利差", "bp", 0),
            ("USDCNH", "汇率", "离岸人民币 USD/CNH", "", 4),
            ("USDCNY", "汇率", "在岸人民币 USD/CNY", "", 4),
            ("SOFR", "利率_pct", "SOFR", "%", 2),
            ("IORB", "利率_pct", "IORB", "%", 2)):
        v = last_num("usd", ent, met)
        if v is None:
            continue
        rows.append([lab, ("%." + str(dec) + "f") % v + unit, esc(last_date("usd"))])
    tb.append(block("美元环流核心指标（%s）" % d,
                    table("数据来自环流日报 Q/g/P 三维度模块", ["指标", "最新", "数据日期"], rows, numcols=(1, 2))))

    pts = []
    for i in range(1, 11):
        t = last_txt("usd", "风险点%d" % i, "text")
        if t:
            pts.append("<li>%s</li>" % esc(re.sub(r"^[-–]\s*", "", t)))
    if pts:
        tb.append(block("环流日报标注的风险点",
                        '<ul style="margin:6px 0 12px;padding-left:20px;color:var(--tx2);font-size:13px;line-height:1.9">%s</ul>'
                        % "".join(pts)))
    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab4 中国流动性

def build_cn():
    kp, ch, tb = [], [], []
    d = last_date("cn_liq")
    judge = last_txt("cn_liq", "资金面", "判定")
    tension = last_num("cn_liq", "资金面", "紧张度")
    dr001 = last_num("cn_liq", "DR001", "利率_pct")
    net = last_num("cn_liq", "OMO", "净投放_亿")
    swap = last_num("swap", "USDCNY1Y", "年化掉期率_pct")
    jgb = last_num("jgb", "JGB10Y", "收益率_pct")

    kp.append(kpi("资金面判定", "%s" % (judge or "—"), "紧张度 %s/100" % fnum(tension, 1),
                  "c-red" if (tension or 0) >= 60 else "c-amber" if (tension or 0) >= 40 else "c-green"))
    kp.append(kpi("DR001", "%.4f%%" % dr001 if dr001 is not None else "—",
                  "偏离政策利率 %sbp" % fnum(last_num("cn_liq", "DR001", "偏离_bp"), 1, True) if last_num("cn_liq", "DR001", "偏离_bp") is not None else "", ""))
    kp.append(kpi("OMO 净投放", "%s亿" % fnum(net, 0, True) if net is not None else "—",
                  "逆回购余额 %s亿" % fnum(last_num("cn_liq", "OMO", "逆回购余额_亿"), 0), "c-red" if (net or 0) > 0 else "c-green"))
    kp.append(kpi("1Y 掉期点（年化）", "%.2f%%" % swap if swap is not None else "—",
                  "近1年分位 %s%%" % fnum(last_num("swap", "USDCNY1Y", "一年分位_pct"), 0), "c-red" if swap is not None and swap < -2.5 else ""))
    kp.append(kpi("JGB 10Y", "%.2f%%" % jgb if jgb is not None else "—",
                  "美日10Y利差 %.2f%%" % (last_num("jgb", "美日利差", "利差_pct") or 0), "c-red" if (jgb or 0) > 1.5 else ""))

    items = [(k, c, ser("cn_liq", k, "利率_pct")) for k, c in
             (("DR001", "#58a6ff"), ("DR007", "#39d2c0"), ("R001", "#d29922"))]
    lb, ss = merge(items)
    chart("c-cn-dr", "银行间回购利率走势", lb, ss, 220, "f3", legend="l-c-cn-dr")
    ch.append(block("银行间回购利率（%）", '<div class="chart" id="c-cn-dr"></div><div class="legend" id="l-c-cn-dr"></div>',
                    "DR001 是存款类机构隔夜，R001 是全市场隔夜。两者之差=非银分层。"))

    items = [(k, c, ser("cn_liq", k, "收益率_pct")) for k, c in
             (("CGB2Y", "#58a6ff"), ("CGB5Y", "#39d2c0"), ("CGB10Y", "#f85149"), ("CGB30Y", "#bc8cff"))]
    lb, ss = merge(items)
    chart("c-cn-cgb", "中国国债收益率曲线", lb, ss, 220, "f2", legend="l-c-cn-cgb")
    ch.append(block("中国国债收益率（%）", '<div class="chart" id="c-cn-cgb"></div><div class="legend" id="l-c-cn-cgb"></div>',
                    "1Y 在多数报告里标注 N/A（数据源缺失），故曲线从 2Y 起。长端快速下行=资金抢跑降息预期。"))

    items = [("Shibor 1W", "#58a6ff", ser("cn_liq", "Shibor1W", "利率_pct")),
             ("1Y AAA 同业存单", "#39d2c0", ser("cn_liq", "CD1Y_AAA", "利率_pct"))]
    lb, ss = merge(items)
    chart("c-cn-cd", "Shibor 1W 与 1Y AAA 同业存单", lb, ss, 210, "f3", legend="l-c-cn-cd")
    ch.append(block("Shibor 1W 与 1Y AAA 同业存单（%）", '<div class="chart" id="c-cn-cd"></div><div class="legend" id="l-c-cn-cd"></div>',
                    "存单利率是银行负债成本，处低位对债市友好。"))

    lb, ss = merge([("1Y USD/CNY 年化掉期率（%）", "#d29922", ser("swap", "USDCNY1Y", "年化掉期率_pct"))])
    chart("c-cn-swap", "1Y掉期点走势", lb, ss, 200, "f2", legend="l-c-cn-swap", zero=True)
    ch.append(block("1Y 掉期点（年化掉期率 %）", '<div class="chart" id="c-cn-swap"></div><div class="legend" id="l-c-cn-swap"></div>',
                    "贴水越深=境内美元需求越强/人民币贬值预期越浓。"))

    # Shibor 表
    rows = []
    for t in ("O/N", "1W", "2W", "1M", "3M", "6M", "1Y"):
        v = last_num("cn_liq", "Shibor" + t, "利率_pct")
        if v is None:
            continue
        rows.append([t, "%.3f" % v,
                     colored(fnum(last_num("cn_liq", "Shibor" + t, "日变动_bp"), 1, True), cls_of(last_num("cn_liq", "Shibor" + t, "日变动_bp"))),
                     colored(fnum(last_num("cn_liq", "Shibor" + t, "周变动_bp"), 1, True), cls_of(last_num("cn_liq", "Shibor" + t, "周变动_bp")))])
    tb.append(block("Shibor 利率（%）", table("日/周变动单位 BP", ["期限", "利率", "日变动", "周变动"], rows, numcols=(1, 2, 3))))

    # CGB
    rows = []
    for t in ("1Y", "2Y", "5Y", "10Y", "30Y"):
        v = last_num("cn_liq", "CGB" + t, "收益率_pct")
        if v is None:
            continue
        rows.append([t, "%.3f" % v,
                     colored(fnum(last_num("cn_liq", "CGB" + t, "日变动_bp"), 1, True), cls_of(last_num("cn_liq", "CGB" + t, "日变动_bp"))),
                     colored(fnum(last_num("cn_liq", "CGB" + t, "周变动_bp"), 1, True), cls_of(last_num("cn_liq", "CGB" + t, "周变动_bp")))])
    tb.append(block("中国国债收益率（%）", table("", ["期限", "收益率", "日变动", "周变动"], rows, numcols=(1, 2, 3))))

    # 回购分位
    rows = []
    for k in ("DR001", "DR007", "DR014", "FR001", "FR007", "FR014"):
        v = last_num("cn_liq", k, "利率_pct")
        if v is None:
            continue
        p = last_num("cn_liq", k, "两年分位_pct")
        rows.append([k, "%.3f" % v, colored(("%.1f%%" % p) if p is not None else "—", pcls(p)),
                     esc(last_txt("cn_liq", k, "状态") or "—")])
    if rows:
        tb.append(block("银行间回购利率与近两年分位",
                        table("分位越低=资金越宽松", ["品种", "利率%", "两年分位", "状态"], rows, numcols=(1, 2))))

    # 成交量 + 存单 + 降准降息 + HIBOR + JGB
    rows = []
    for k, lab in (("DR001", "DR001"), ("DR007", "DR007"), ("DR014", "DR014"),
                   ("R001", "R001"), ("R007", "R007"), ("R014", "R014")):
        v = last_num("cn_liq", "量" + k, "成交量_亿")
        if v is not None:
            rows.append([lab, "%.0f 亿" % v])
    mlf = last_num("cn_liq", "MLF", "余额_亿")
    if mlf is not None:
        rows.append(["<b>MLF 余额</b>", "<b>%.0f 亿</b>" % mlf])
    tot = last_num("cn_liq", "回购", "成交量合计_亿")
    if tot is not None:
        rows.append(["<b>主要品种合计</b>", "<b>%.0f 亿</b>" % tot])
    if rows:
        tb.append(block("银行间回购成交量", table("中国货币网 T-1 数据", ["品种", "成交量"], rows, numcols=(1,))))

    rows = []
    cd = last_num("cn_liq", "CD1Y_AAA", "利率_pct")
    if cd is not None:
        rows.append(["1Y AAA 同业存单", "%.4f%%" % cd,
                     colored(fnum(last_num("cn_liq", "CD1Y_AAA", "日变动_bp"), 1, True) + "bp", cls_of(last_num("cn_liq", "CD1Y_AAA", "日变动_bp"))),
                     "近1年分位 %.0f%%" % (last_num("cn_liq", "CD1Y_AAA", "一年分位_pct") or 0),
                     esc(last_txt("cn_liq", "CD1Y_AAA", "阶段") or "")])
    sp = last_num("cn_liq", "降准降息利差", "1Y减3M国开_bp")
    if sp is not None:
        rows.append(["降准降息预期利差（1Y-3M国开）", "%.2fbp" % sp, "—",
                     "近1年分位 %.0f%%" % (last_num("cn_liq", "降准降息利差", "一年分位_pct") or 0),
                     esc(last_txt("cn_liq", "降准降息利差", "阶段") or "")])
    if rows:
        tb.append(block("存单与降准降息预期",
                        table("利差极低位=宽松预期已被充分定价", ["指标", "最新", "日变动", "位置", "阶段"],
                              rows, numcols=(1, 2, 3))))

    rows = []
    for k in ("ON", "1M", "3M", "6M", "12M"):
        v = last_num("hibor", "HIBOR" + k, "利率_pct")
        if v is not None:
            rows.append(["HIBOR " + k, "%.2f%%" % v])
    for k in ("2Y", "5Y", "10Y", "20Y", "30Y", "40Y"):
        v = last_num("jgb", "JGB" + k, "收益率_pct")
        if v is not None:
            rows.append(["JGB " + k, "%.2f%%" % v])
    if rows:
        tb.append(block("港股 HIBOR 与日本国债 JGB",
                        table("JGB 10Y > 1.5% = 日元套息平仓风险高悬；HIBOR 警戒线 >3.0%",
                              ["期限", "利率"], rows, numcols=(1,))))
    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab5 两融杠杆

def build_margin():
    kp, ch, tb = [], [], []
    d = last_date("margin")
    bal = last_num("margin", "全市场", "融资余额_亿")
    pct = last_num("margin", "全市场", "融资占流通_pct")
    rank = last_num("margin", "全市场", "历史分位_pct")
    acc = last_num("margin", "全市场", "加速度_pct")
    sq = last_num("margin", "全市场", "强平连锁")

    kp.append(kpi("融资余额", "%s 亿" % fnum(bal, 0), "两融 %s 亿" % fnum(last_num("margin", "全市场", "两融余额_亿"), 0),
                  "c-red" if (rank or 0) >= 90 else "c-amber"))
    kp.append(kpi("融资 / 流通市值", "%.3f%%" % pct if pct is not None else "—",
                  "历史分位 %.0f%%" % (rank or 0), "c-red" if (rank or 0) >= 90 else "c-amber"))
    kp.append(kpi("融资变化加速度", "%.2f%%" % acc if acc is not None else "—",
                  "加速流出" if acc is not None and acc < -30 else "平稳", "c-green" if acc is not None and acc < -30 else ""))
    kp.append(kpi("强平连锁风险", "触发" if sq else "未触发",
                  esc(last_txt("margin", "全市场", "强平连锁") or ""), "c-red" if sq else "c-green"))
    kp.append(kpi("融资买入 / 成交额", "%s%%" % fnum(last_num("margin", "全市场", "融资买入占成交_pct"), 2),
                  "杠杆资金参与度", ""))

    lb, ss = merge([("融资余额（亿）", "#58a6ff", ser("margin", "全市场", "融资余额_亿"))])
    chart("c-mg-bal", "融资余额走势", lb, ss, 220, "f0", legend="l-c-mg-bal")
    ch.append(block("融资余额（亿元）", '<div class="chart" id="c-mg-bal"></div><div class="legend" id="l-c-mg-bal"></div>',
                    "两融数据 T+1 延迟，日期比风险日报晚一天。"))

    items = [("5日变化率（%）", "#58a6ff", ser("margin", "全市场", "变化率5日_pct")),
             ("10日变化率（%）", "#39d2c0", ser("margin", "全市场", "变化率10日_pct")),
             ("20日变化率（%）", "#d29922", ser("margin", "全市场", "变化率20日_pct"))]
    lb, ss = merge(items)
    chart("c-mg-chg", "融资余额变化率走势", lb, ss, 220, "sign1", legend="l-c-mg-chg", zero=True)
    ch.append(block("融资余额变化率（%）", '<div class="chart" id="c-mg-chg"></div><div class="legend" id="l-c-mg-chg"></div>',
                    "10 日变化率大幅正值说明前期杠杆快速抬升，随后偿还压力更大。"))

    lb, ss = merge([("加速度（%）", "#f85149", ser("margin", "全市场", "加速度_pct"))])
    chart("c-mg-acc", "融资变化加速度", lb, ss, 200, "f1", legend="l-c-mg-acc", zero=True)
    ch.append(block("融资变化加速度（%）", '<div class="chart" id="c-mg-acc"></div><div class="legend" id="l-c-mg-acc"></div>',
                    "加速度 < -0.3% 触发 227% 担保比例等效信号。"))

    rows = [
        ["融资余额", "%s 亿" % fnum(bal, 0)],
        ["两融余额", "%s 亿" % fnum(last_num("margin", "全市场", "两融余额_亿"), 0)],
        ["流通市值", "%s 万亿" % fnum(last_num("margin", "全市场", "流通市值_万亿"), 1)],
        ["两市成交额", "%s 万亿" % fnum(last_num("margin", "全市场", "两市成交_万亿"), 2)],
        ["融资 / 流通市值", "%.3f%%（历史分位 %.0f%%）" % (pct or 0, rank or 0)],
        ["融资买入 / 成交额", "%s%%" % fnum(last_num("margin", "全市场", "融资买入占成交_pct"), 2)],
        ["5日变化率", fnum(last_num("margin", "全市场", "变化率5日_pct"), 2, True) + "%"],
        ["10日变化率", fnum(last_num("margin", "全市场", "变化率10日_pct"), 2, True) + "%"],
        ["20日变化率", fnum(last_num("margin", "全市场", "变化率20日_pct"), 2, True) + "%"],
        ["加速度", fnum(acc, 3, True) + "%"],
    ]
    tb.append(block("两融全景（%s）" % d, table("数据来自风险预测日报④两融风险监控，T+1 延迟", ["指标", "数值"], rows, numcols=(1,))))

    sig = []
    for k, lab in (("强平连锁", "强平连锁风险"), ("担保比例等效信号", "227% 担保比例等效信号"),
                   ("融资出清", "融资出清"), ("超跌反弹候选", "超跌反弹候选"),
                   ("底部确认信号", "底部确认信号"), ("风格切换", "风格切换")):
        v = last_num("margin", "全市场", k)
        if v is None:
            continue
        t = last_txt("margin", "全市场", k) or ""
        sig.append(["<b>%s</b>" % lab,
                    colored("触发" if v else "未触发", "c-red" if v else "c-green"),
                    esc(re.sub(r"^\d+\.\s*", "", t))[:80]])
    if sig:
        tb.append(block("机会与风险信号", table("", ["信号", "状态", "原文"], sig, numcols=(1,))))
    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab6 择时评分

def build_score():
    kp, ch, tb = [], [], []
    td = last_date("timing")
    ld = last_date("liq_risk")
    rt = last_txt("timing", "综合", "评级")
    pos = last_txt("timing", "综合", "整体仓位")
    ls = last_num("liq_risk", "综合", "评分")
    ar = last_num("liq_risk", "吸收率因子", "AR_num")
    hb_new = last_num("liq_risk", "兴登堡", "120日新高_num")
    hb_low = last_num("liq_risk", "兴登堡", "120日新低_num")

    kp.append(kpi("三维择时评级", "%s" % (rt or "—"), "整体仓位 %s" % (pos or "—"),
                  "c-amber" if rt and "观望" in rt else ""))
    kp.append(kpi("流动性风险偏好评分", "%s/100" % fnum(ls, 0), esc(last_txt("liq_risk", "综合", "等级") or ""),
                  "c-green" if (ls or 0) >= 70 else "c-amber"))
    kp.append(kpi("吸收率因子 AR", fnum(ar, 4), esc(last_txt("liq_risk", "吸收率因子", "最终信号") or ""), ""))
    kp.append(kpi("兴登堡指标", esc(last_txt("liq_risk", "兴登堡", "触发状态") or "—"),
                  "新高 %s只 / 新低 %s只" % (fnum(hb_new, 0), fnum(hb_low, 0)),
                  "c-red" if "未触发" not in (last_txt("liq_risk", "兴登堡", "触发状态") or "未触发") else "c-green"))

    lb, ss = merge([("流动性风险偏好综合评分", "#58a6ff", ser("liq_risk", "综合", "评分"))])
    chart("c-sc-liq", "流动性风险偏好评分走势", lb, ss, 210, "f0", legend="l-c-sc-liq")
    ch.append(block("流动性风险偏好综合评分（0-100）", '<div class="chart" id="c-sc-liq"></div><div class="legend" id="l-c-sc-liq"></div>',
                    "60 分流动性 + 40 分风险偏好。数据来自「市场流动性与风险偏好」日报。"))

    items = [("AR（吸收率）", "#58a6ff", ser("liq_risk", "吸收率因子", "AR_num")),
             ("AR 5日均值", "#39d2c0", ser("liq_risk", "吸收率因子", "AR_5日均值_num"))]
    lb, ss = merge(items)
    chart("c-sc-ar", "吸收率因子走势", lb, ss, 210, "f4", legend="l-c-sc-ar")
    ch.append(block("吸收率因子 AR（Absorption Ratio）", '<div class="chart" id="c-sc-ar"></div><div class="legend" id="l-c-sc-ar"></div>',
                    "AR 上升=风险由少数因子主导，系统性风险抬升；DAR_short > DAR_long 提示短期风险。"))

    items = [("120日新高（只）", "#3fb950", ser("liq_risk", "兴登堡", "120日新高_num")),
             ("120日新低（只）", "#f85149", ser("liq_risk", "兴登堡", "120日新低_num"))]
    lb, ss = merge(items)
    chart("c-sc-hb", "兴登堡指标新高新低家数", lb, ss, 210, "f0", legend="l-c-sc-hb")
    ch.append(block("兴登堡指标（新高 / 新低家数）", '<div class="chart" id="c-sc-hb"></div><div class="legend" id="l-c-sc-hb"></div>',
                    "新高与新低同时放大且指数在 50 日均线下方=市场内部撕裂，是崩盘前兆信号。"))

    rows = []
    six = [("① ETF资金流",), ("② 市场拥挤度",), ("③ 期权情绪(PCR)",),
           ("④ 热门板块持仓",), ("⑤ 杠杆水平(两融)",), ("⑥ 量价关系",)]
    for (ent,) in six:
        rows.append([esc(ent), esc(last_txt("timing", ent, "信号") or "—"), esc(last_txt("timing", ent, "说明") or "—")])
    if rows:
        tb.append(block("三维择时 · 六维度明细（%s）" % td,
                        table("综合评级 %s ｜ 整体仓位 %s ｜ 看多 %s / 中性 %s / 看空 %s / 缺失 %s" % (
                            rt or "—", pos or "—",
                            fnum(last_num("timing", "综合", "看多数"), 0),
                            fnum(last_num("timing", "综合", "中性数"), 0),
                            fnum(last_num("timing", "综合", "看空数"), 0),
                            fnum(last_num("timing", "综合", "缺失数"), 0)),
                            ["维度", "信号", "说明"], rows)))

    rows = []
    for k in ("AR", "AR_5日均值", "DAR_long", "DAR_short", "PC1解释方差", "PC3累计方差", "最终信号"):
        v = last_txt("liq_risk", "吸收率因子", k)
        if v:
            rows.append([k, esc(v)])
    for k in ("120日新高", "120日新低", "上证指数", "50日均线", "触发状态"):
        v = last_txt("liq_risk", "兴登堡", k)
        if v:
            rows.append(["兴登堡 · " + k, esc(v)])
    if rows:
        tb.append(block("吸收率因子与兴登堡明细（%s）" % ld,
                        table("数据来自「市场流动性与风险偏好」日报", ["指标", "数值"], rows, numcols=(1,))))

    rows = []
    for k, lab in (("流动性评分", "流动性评分"), ("SHIBOR7天", "SHIBOR 7天"),
                   ("全A成交额", "全A成交额"), ("风险偏好评分", "风险偏好评分"),
                   ("成交额集中度", "成交额集中度"), ("涨停/跌停", "涨停/跌停"),
                   ("北向资金", "北向资金")):
        v = last_txt("liq_risk", "流动性" if k in ("SHIBOR7天", "全A成交额", "流动性评分") else "风险偏好", k)
        if v is not None:
            rows.append([lab, esc(v)])
    if rows:
        tb.append(block("流动性与风险偏好分项",
                        table("", ["指标", "数值"], rows, numcols=(1,))))
    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab7 债市与周期

def build_bond():
    kp, ch, tb = [], [], []
    bd = last_date("bond")
    tot = last_num("bond", "总分", "评分")
    dirn = last_txt("bond", "方向", "判断")
    cd = last_date("cycle")

    kp.append(kpi("国债期货四因子总分", fnum(tot, 1), esc(dirn or ""),
                  "c-red" if (tot or 0) >= 60 else "c-green" if (tot or 0) <= 40 else "c-amber"))
    kp.append(kpi("信贷周期", esc(last_txt("cycle", "信贷周期", "状态") or "—"),
                  "本轮起始 %s" % esc(last_txt("cycle", "信贷周期", "本轮起始") or "—"), ""))
    kp.append(kpi("库存周期", esc(last_txt("cycle", "库存周期", "状态") or "—"),
                  "本轮起始 %s" % esc(last_txt("cycle", "库存周期", "本轮起始") or "—"), ""))
    kp.append(kpi("数据覆盖", "%s%%" % fnum(last_num("bond", "数据", "覆盖率_pct"), 0),
                  "缺失：%s" % esc((last_txt("bond", "数据", "缺失项") or "—")[:36]), ""))

    lb, ss = merge([("四因子总分", "#58a6ff", ser("bond", "总分", "评分"))])
    chart("c-bd-tot", "国债期货四因子总分走势", lb, ss, 210, "f1", legend="l-c-bd-tot")
    ch.append(block("国债期货四因子总分（周频）", '<div class="chart" id="c-bd-tot"></div><div class="legend" id="l-c-bd-tot"></div>',
                    "政策 30% + 资金 30% + 情绪 20% + 股债 20%。分数越高越看多债市。"))

    items = [(k, c, ser("bond", k, "评分")) for k, c in
             (("政策信号", "#58a6ff"), ("资金信号", "#39d2c0"), ("情绪信号", "#d29922"), ("股债信号", "#bc8cff"))]
    lb, ss = merge(items)
    chart("c-bd-fac", "四因子分项评分走势", lb, ss, 210, "f1", legend="l-c-bd-fac")
    ch.append(block("四因子分项评分", '<div class="chart" id="c-bd-fac"></div><div class="legend" id="l-c-bd-fac"></div>',
                    "资金信号权重最高，与 DR007 / Shibor / 存单利率直接挂钩。"))

    rows = []
    for k in ("政策信号", "资金信号", "情绪信号", "股债信号", "总分"):
        sc = last_num("bond", k, "评分")
        if sc is None:
            continue
        rows.append(["<b>%s</b>" % k if k == "总分" else k,
                     esc(last_txt("bond", k, "权重") or ("—" if k == "总分" else "")),
                     fnum(sc, 1),
                     esc((last_txt("bond", k, "说明") or "—")[:70])])
    if rows:
        tb.append(block("四因子评分表（%s）" % bd,
                        table("方向判断：%s" % (dirn or "—"), ["因子", "权重", "评分", "说明"], rows, numcols=(2,))))

    rows = []
    for k in ("信贷周期", "库存周期"):
        rows.append([k, esc(last_txt("cycle", k, "状态") or "—"),
                     esc(last_txt("cycle", k, "本轮起始") or "—"),
                     esc(last_txt("cycle", k, "阶段起始") or "—"),
                     esc(last_txt("cycle", k, "研判") or "—")])
    if rows:
        tb.append(block("库存 / 信贷周期（%s）" % cd,
                        table("月频报告，最新一份 %s" % cd, ["周期", "当前状态", "本轮起始", "阶段起始", "研判"],
                              rows, numcols=(2, 3))))

    # 热点概念 TOP20（去个股）
    gd = last_date("concept")
    rows = C.execute(
        "SELECT entity, value_text FROM facts WHERE domain='concept' AND date=? AND metric='排名' "
        "ORDER BY CAST(value_text AS INTEGER)", (gd,)).fetchall() if gd != "—" else []
    out = []
    for name, rk in rows:
        chg = last_num("concept", name, "两周涨幅_pct")
        ratio = last_txt("concept", name, "上涨比例")
        logic = last_txt("concept", name, "热点逻辑")
        out.append([esc(rk), esc(name),
                    colored(fnum(chg, 2, True) + "%" if chg is not None else "—", cls_of(chg)),
                    esc(ratio or "—"), esc((logic or "—")[:40])])
    if out:
        tb.append(block("热点概念 TOP20（%s）" % gd,
                        table("已剔除个股明细，只保留板块聚合（按你的要求：不要个股）",
                              ["#", "概念", "两周涨幅", "上涨比例", "热点逻辑"], out, numcols=(0, 2))))
    return "".join(kp), "".join(ch), "".join(tb)


# ---------------------------------------------------------------- Tab8 自选股

def build_watch():
    inner = ('<div class="block"><h2>自选股 · 待接入</h2>'
             '<p class="note">我全盘搜了云盘（文件名 + 文件内容，关键词：自选 / 自选股 / 股票池 / watchlist / portfolio / '
             '默尔根 / 亚丽），<b>没有找到「默尔根和亚丽」的自选股报告或清单</b>。</p>'
             '<p class="note">目前云盘里跟持仓有关的只有两处，都不是你说的那份：</p>'
             '<ul style="margin:6px 0 14px;padding-left:20px;color:var(--tx2);font-size:13px;line-height:1.9">'
             '<li><code>长期计划/股票早晚报/计划配置.md</code> — 10 只港股持仓（协鑫新能源、中芯、信达、药明合联、心动、美团、百济、长飞、康方、腾讯）</li>'
             '<li><code>codeact/output/margin_detail.db</code> — 两融个股明细（不是自选股）</li>'
             '</ul>'
             '<p class="note">你把清单或文件位置给我（哪怕就是几句话：「默尔根看 A B C，亚丽看 D E」），我立刻接成第 8 个栏目：'
             '每只标的最新价、日/周涨跌、所属报告里的风险信号、以及走势图，一样是每天自动更新。</p>'
             '<p class="note">栏目骨架已经留好了，加清单只是补一段解析，不用改架构。</p></div>')
    return "", "", inner


# ---------------------------------------------------------------- 组装

def _bdays_between(d1, d2):
    """d1 到 d2 之间跨过的工作日数（不含 d1、含 d2）。用于判断数据落后几个交易日。"""
    n, d = 0, d1
    while d < d2:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def main():
    tabs = [
        ("idx", "宽基指数风险", build_index),
        ("opt", "期权 · 偏度/VIX", build_option),
        ("usd", "美元环流", build_usd),
        ("cn", "中国流动性", build_cn),
        ("margin", "两融与杠杆", build_margin),
        ("score", "择时与评分", build_score),
        ("bond", "债市与周期", build_bond),
        ("watch", "自选股", build_watch),
    ]
    nav, panels = [], []
    for i, (tid, label, fn) in enumerate(tabs):
        kp, ch, tb = fn()
        nav.append('<button role="tab" id="tab-%s" aria-controls="panel-%s" aria-selected="%s" tabindex="%s">%s</button>'
                   % (tid, tid, "true" if i == 0 else "false", "0" if i == 0 else "-1", label))
        inner = ""
        if kp:
            inner += '<div class="kpis" id="kp-%s">%s</div>' % (tid, kp)
        inner += ch + tb
        panels.append('<section class="panel" id="panel-%s" role="tabpanel" aria-labelledby="tab-%s"%s>%s</section>'
                      % (tid, tid, "" if i == 0 else " hidden", inner))

    # 元信息
    meta = C.execute("SELECT domain, COUNT(*), MIN(date), MAX(date) FROM facts GROUP BY domain").fetchall()
    total = C.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    nfile = C.execute("SELECT COUNT(*) FROM ingest_log WHERE status='ok'").fetchone()[0]
    built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    dom_cn = {"index": "指数风险", "option": "期权", "usd": "美元环流", "cn_liq": "中国流动性",
              "swap": "掉期点", "hibor": "HIBOR", "jgb": "JGB", "margin": "两融", "timing": "三维择时",
              "liq_risk": "流动性风险偏好", "bond": "国债四因子", "cycle": "库存信贷周期",
              "concept": "热点概念", "struct_factor": "结构因子", "signal": "风险/机会信号"}
    chips = "".join('<span class="badge" style="margin:0 6px 6px 0">%s %d 条 · %s~%s</span>'
                    % (esc(dom_cn.get(dm, dm)), cnt, mn, mx) for dm, cnt, mn, mx in sorted(meta, key=lambda x: -x[1]))

    # ---- 数据新鲜度（2026-11-27）----
    # 页面第一眼就得知道「这些数字是哪天的」。各域更新节奏不同（两融 T+1、国债四因子周更、
    # 库存信贷周期月更），所以按域给各自的 SLA，统一阈值会天天误报。
    meta_map = {dm: (cnt, mn, mx) for dm, cnt, mn, mx in meta}
    built_dt = datetime.datetime.now()
    ALL_DOMS = [("index", "指数风险"), ("option", "期权"), ("usd", "美元环流"),
                ("cn_liq", "中国流动性"), ("swap", "掉期点"), ("hibor", "HIBOR"),
                ("jgb", "JGB"), ("margin", "两融"), ("timing", "三维择时"),
                ("liq_risk", "流动性风险偏好"), ("bond", "国债四因子"),
                ("cycle", "库存信贷周期"), ("concept", "热点概念"),
                ("struct_factor", "结构因子"), ("signal", "风险/机会信号")]
    # 正常更新节奏（交易日）：超过标黄，超过 2 倍标红
    DOM_SLA = {"index": 1, "option": 1, "usd": 1, "cn_liq": 1, "swap": 1,
               "hibor": 1, "jgb": 1, "margin": 2, "timing": 1, "liq_risk": 1,
               "bond": 5, "cycle": 20, "concept": 5, "struct_factor": 2, "signal": 1}
    # 顶部红条只报这些页面主栏目的过期；其余域过期在状态表里看，避免天天挂一串黄条把人练脱敏
    ALERT_DOMS = {"index", "option", "usd", "cn_liq", "margin", "timing", "liq_risk"}
    st_rows, hard_alerts, worst_lag = [], [], 0
    for dm, cn in ALL_DOMS:
        cnt, mn, mx = meta_map.get(dm, (0, None, None))
        if not cnt or not mx:
            st_rows.append((cn, "—", "—", '<span class="fresh bad"><span class="dot"></span>缺失</span>'))
            hard_alerts.append("%s 完全没有数据" % cn)
            worst_lag = 99
            continue
        lag = _bdays_between(datetime.date(*map(int, mx.split("-"))), built_dt.date())
        if dm == "index":
            worst_lag = lag
        sla = DOM_SLA.get(dm, 1)
        if lag <= sla:
            cls, txt = "ok", "正常"
        elif lag <= sla * 2:
            cls, txt = "warn", "滞后 %d 日" % lag
        else:
            cls, txt = "bad", "过期 %d 日" % lag
            if dm in ALERT_DOMS:
                hard_alerts.append("%s 已落后 %d 个交易日（最新 %s）" % (cn, lag, mx))
        st_rows.append((cn, mx, str(lag),
                        '<span class="fresh %s"><span class="dot"></span>%s</span>' % (cls, txt)))

    # 顶部角标只盯核心的指数风险域
    _imx = meta_map.get("index", (0, None, None))[2]
    if not _imx:
        fresh_cls, fresh_txt = "bad", "指数风险无数据"
    elif worst_lag <= 1:
        fresh_cls, fresh_txt = "ok", "数据截至 %s" % _imx
    elif worst_lag <= 3:
        fresh_cls, fresh_txt = "warn", "数据截至 %s（落后 %d 日）" % (_imx, worst_lag)
    else:
        fresh_cls, fresh_txt = "bad", "数据截至 %s（落后 %d 日）" % (_imx, worst_lag)

    srctab = ('<table class="srctab"><thead><tr><th>数据源</th><th>最新日期</th>'
              '<th>落后(交易日)</th><th>状态</th></tr></thead><tbody>'
              + "".join('<tr><td>%s</td><td class="mono">%s</td><td class="mono">%s</td><td>%s</td></tr>'
                        % (cn, mx, lag, st) for cn, mx, lag, st in st_rows)
              + "</tbody></table>")
    alerts_html = ""
    if hard_alerts:
        alerts_html = ('<div class="alerts"><b>⚠️ 数据源异常，页面部分数字不是最新的：</b><ul>'
                       + "".join("<li>%s</li>" % esc(a) for a in hard_alerts)
                       + "</ul></div>")

    payload = json.dumps(CHARTS, ensure_ascii=False, separators=(",", ":"))
    js = JSCORE + r"""
  /* ---------- 格式化映射 ---------- */
  var VF={
    sign2:function(v){return (v>0?"+":"")+v.toFixed(2);},
    sign1:function(v){return (v>0?"+":"")+v.toFixed(1);},
    f0:function(v){return Math.round(v).toString();},
    f1:function(v){return v.toFixed(1);},
    f2:function(v){return v.toFixed(2);},
    f3:function(v){return v.toFixed(3);},
    f4:function(v){return v.toFixed(4);}
  };
  var YF={
    f0:function(v){return Math.round(v).toString();},
    f1:function(v){return v.toFixed(1);},
    f2:function(v){return v.toFixed(2);}
  };
  var CHARTS=window.__CHARTS__||[];
  CHARTS.forEach(function(cfg){
    if(cfg.vfmtKey&&VF[cfg.vfmtKey]) cfg.vfmt=VF[cfg.vfmtKey];
    if(cfg.yfmtKey&&YF[cfg.yfmtKey]) cfg.yfmt=YF[cfg.yfmtKey];
    var host=document.getElementById(cfg.id);
    if(!host) return;
    drawChart(host,cfg);
    if(cfg.legendId){
      var lg=document.getElementById(cfg.legendId);
      if(lg) lg.innerHTML=legend(cfg.series||[]);
    }
  });

  /* ---------- Tab 切换 ---------- */
  window.addEventListener('resize',function(){
  [].forEach.call(document.querySelectorAll('.tip'),function(t){
    t.classList.remove('on'); t.style.left='0px'; t.style.top='0px'; t.innerHTML='';
  });
});
var tabs=[].slice.call(document.querySelectorAll('nav.tabs button'));
  function activate(btn,focus){
    tabs.forEach(function(b){
      var on=(b===btn);
      b.setAttribute('aria-selected',on?'true':'false');
      b.setAttribute('tabindex',on?'0':'-1');
      var p=document.getElementById(b.getAttribute('aria-controls'));
      if(p) p.hidden=!on;
    });
    if(focus) btn.focus();
  }
  tabs.forEach(function(b,i){
    b.addEventListener('click',function(){ activate(b,false); });
    b.addEventListener('keydown',function(e){
      var k=e.key, j=-1;
      if(k==='ArrowRight') j=(i+1)%tabs.length;
      else if(k==='ArrowLeft') j=(i-1+tabs.length)%tabs.length;
      else if(k==='Home') j=0;
      else if(k==='End') j=tabs.length-1;
      if(j>=0){ e.preventDefault(); activate(tabs[j],true); }
    });
  });
})();
"""

    htmlout = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>股市监控画布 · 每日看板</title>
<style>__CSS__</style>
</head>
<body>
<div id="boot" role="status" aria-live="polite">
  <div class="ring" aria-hidden="true"></div>
  <div class="bt">正在加载事实库…</div>
  <div class="bs2">__TOTAL__ 条事实 · __NFILE__ 份报告 · 正在绘制 <span id="bootn">__NCHART__</span> 张图表</div>
</div>
<div class="wrap">
<header class="top">
  <h1>股市监控画布<span>AI股票群报告 · 统一事实库</span></h1>
  <div class="stamp"><span class="fresh __FRESHCLS__"><span class="dot"></span>__FRESHTXT__</span> 生成 __BUILT__</div>
</header>
<nav class="tabs" role="tablist" aria-label="看板栏目">__NAV__</nav>
__PANELS__
__ALERTS__
<footer>
  <div style="margin-bottom:8px">__CHIPS__</div>
  <details style="margin:6px 0 12px"><summary style="cursor:pointer;color:var(--tx2)">数据源状态（每个域的最新日期与落后交易日）</summary>__SRCTAB__</details>
  <div>数据来源：<code>facts.db</code> 统一事实库，共 <b>__TOTAL__</b> 条事实、解析自 <b>__NFILE__</b> 份已推送报告。</div>
  <div>口径：所有数值直接解析自报告正文与报告引用的数据库（style_drift.db 期权库），<b>未重新采集行情、未做插值补值</b>。分位按近 252 个交易日计算。</div>
  <div>日期差异：风险日报 <code>__D1__</code>；环流日报 <code>__D2__</code>；期权库 <code>__D3__</code>；两融 <code>__D4__</code>；结构因子 <code>__D5__</code>；国债四因子 <code>__D6__</code>。各源更新节奏不同，看板按各自最新一期展示。</div>
  <div>生成方式：<code>python3 ingest.py</code> → <code>python3 build_html.py</code>，纯脚本零 token。案例分析学习材料，不构成投资建议。</div>
</footer>
</div>
<script>window.__CHARTS__=__PAYLOAD__;</script>
<script>__JS__</script>
<script>
/* 图表画完才放开页面。8 秒兜底：无论 JS 出什么错，都不会把页面永久锁在遮罩后面。 */
(function(){
  var b=document.getElementById('boot');
  if(!b) return;
  var released=false;
  function done(){
    if(released) return;
    released=true;
    requestAnimationFrame(function(){
      requestAnimationFrame(function(){
        b.classList.add('done');
        setTimeout(function(){ if(b.parentNode) b.parentNode.removeChild(b); },600);
      });
    });
  }
  if(document.readyState==='complete') done();
  else window.addEventListener('load', done);
  setTimeout(done, 8000);
})();
</script>
</body>
</html>
"""
    out = (htmlout
           .replace("__CSS__", CSS)
           .replace("__JS__", js)
           .replace("__PAYLOAD__", payload)
           .replace("__NAV__", "".join(nav))
           .replace("__PANELS__", "".join(panels))
           .replace("__CHIPS__", chips)
           .replace("__TOTAL__", str(total))
           .replace("__NFILE__", str(nfile))
           .replace("__BUILT__", built)
           .replace("__FRESHCLS__", fresh_cls)
           .replace("__FRESHTXT__", fresh_txt)
           .replace("__ALERTS__", alerts_html)
           .replace("__SRCTAB__", srctab)
           .replace("__NCHART__", str(len(CHARTS)))
           .replace("__D1__", last_date("index"))
           .replace("__D2__", last_date("usd"))
           .replace("__D3__", last_date("option"))
           .replace("__D4__", last_date("margin"))
           .replace("__D5__", last_date("struct_factor"))
           .replace("__D6__", last_date("bond")))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("已生成 %s（%.1f KB，%d 张图，%d 个栏目）" % (OUT, len(out.encode()) / 1024.0, len(CHARTS), len(tabs)))


if __name__ == "__main__":
    main()
