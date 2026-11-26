# -*- coding: utf-8 -*-
"""期权风险释放监控 —— 精简版飞书交互式卡片（V2：红黄灯置顶）

承载方式（用户 2026-10-22 要求）：
  报告只以【交互式卡片】推送，不再推送文件消息；卡片精简，主要风险优先置前。

精简口径：
  1) 卡片最上方先给「🔴 红灯 → 🟡 黄灯」信号总览，逐条列 标的 + 维度 + 原因，红灯在前；
  2) 标的一览只保留 名称 / 现价 · 隐波 / 波动率灯 / 风险灯 四列，不展开八字段明细；
  3) 未来 5 个交易日波动率预测压缩为 标的 / t+1 / t+5 / 预判 四列；
  4) 缺数据一律写「待更新」，不插值；日期从各数据源实际最新日期动态推导，不硬编码。

数据源：data/final.json（judge.py 输出）+ data/vol_forecast.json + data/us_*.json
推送凭据：复用 skills/user/momentum-risk-control/feishu_config.py（只引用路径，不复制密钥）
"""
import json
import os
import re
import sys

# 原指向旧沙箱 workspace + ses_... 会话目录（均已空/不存在）。
# data/ 与 feishu_config.py 都在本 case 目录下，改相对定位。
BASE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(BASE, "data")
CFG_DIR = BASE          # feishu_config.py 在本 case 目录
sys.path.insert(0, CFG_DIR)

RED = "<font color='red'>%s</font>"
ORANGE = "<font color='orange'>%s</font>"
GREY = "<font color='grey'>%s</font>"
GREEN = "<font color='green'>%s</font>"

A_ORDER = ["300ETF", "500ETF", "KC50", "CYB", "ZZ1000"]
US_ORDER = [("SPX", "标普500"), ("NDX", "纳斯达克100"), ("RUT", "罗素2000")]

A_NAMES = {"300ETF": "沪深300ETF", "500ETF": "中证500ETF", "KC50": "科创50ETF",
           "CYB": "创业板ETF", "ZZ1000": "中证1000(MO)"}
U_NAMES = {"SPX": "标普500", "NDX": "纳斯达克100", "RUT": "罗素2000"}


def b(s):
    return "**%s**" % s


def red(s):
    return RED % s


def org(s):
    return ORANGE % s


def grey(s):
    return GREY % s


def green(s):
    return GREEN % s


def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def col(weight, content):
    return {"tag": "column", "width": "weighted", "weight": int(weight),
            "vertical_align": "top", "elements": [div(content)]}


def cols(items, mode="none"):
    """按 (权重, 内容) 列表生成 column_set 对齐表格。

    飞书分栏的列宽只支持 weighted + 整数权重（1~5），不支持百分比；
    flex_mode 取 none —— 窄屏按比例压缩、各列仍并排对齐。
    （原用 flex_mode=stretch：官方语义是把每列改成整行、上下堆叠，
     正是此前“散成一列”的根因。）
    """
    return {"tag": "column_set", "flex_mode": mode, "horizontal_spacing": "small",
            "columns": [col(w, c) for w, c in items]}


# ---------------- 日期与数字 ----------------

def iso8(s):
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return s


def iso_from_mdy(s):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", str(s))
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
    return str(s)


def iso_from_dash(s):
    return str(s)[:10]


def fmt_spot(x, wide=None):
    """点位自适应：>=100 用两位小数，否则四位；wide 可强制"""
    if not isinstance(x, (int, float)):
        return "待更新"
    d = wide if wide is not None else (2 if abs(x) >= 100 else 4)
    return ("%%.%df" % d) % x


def fmt_pct(x):
    if not isinstance(x, (int, float)):
        return "待更新"
    return "%.2f%%" % x


def last_field(path, field):
    try:
        j = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    if isinstance(j, list):
        for row in reversed(j):
            if isinstance(row, dict) and row.get(field) not in (None, ""):
                return row[field]
    if isinstance(j, dict):
        return j.get(field)
    return None


def _status_line():
    """卡片首行的数据时点声明（data/data_status.json，由主链路在计算后写入）。"""
    try:
        st = json.load(open(os.path.join(D, "data_status.json"), encoding="utf-8"))
    except Exception:
        return None
    return st.get("line")


def data_dates():
    """从各数据源推导实际口径日期"""
    d = {}
    d["etf"] = iso_from_dash(last_field(os.path.join(D, "us_etf_SPY.json"), "Date"))
    d["vix"] = iso_from_mdy(last_field(os.path.join(D, "us_VIX.json"), "DATE"))
    d["skew"] = iso_from_mdy(last_field(os.path.join(D, "us_SKEW.json"), "DATE"))
    d["vxn"] = iso_from_mdy(last_field(os.path.join(D, "us_VXN.json"), "DATE"))
    d["rvx"] = iso_from_mdy(last_field(os.path.join(D, "us_RVX.json"), "DATE"))
    ts = last_field(os.path.join(D, "us_chain_SPX.json"), "timestamp")
    d["chain"] = str(ts)[:16] if ts else None
    return d


# ---------------- 判灯分档 ----------------

def light_of(entry):
    return (entry or {}).get("light") or "🟡观察"


def escalate_rank(entry):
    """风险分档：3=红灯，2=需盯（红灯条件或 Lv1/Lv2 命中），1=仅基线未满"""
    e = entry or {}
    if "🔴" in light_of(e) or e.get("red_light"):
        return 3
    if (e.get("nr") or 0) >= 1 or (e.get("n1") or 0) >= 1 or (e.get("n2") or 0) >= 1:
        return 2
    return 1


def light_disp(entry, rank=None):
    """风险释放灯（列头已标明维度，仅留色标以适配窄列）"""
    lv = light_of(entry)
    rk = escalate_rank(entry) if rank is None else rank
    if "🔴" in lv:
        return red(b("🔴"))
    if rk >= 2:
        return org(b("🟡"))
    return b("🟢")


def nm(entry, fallback):
    v = (entry or {}).get("vals") or {}
    return v.get("label") or (entry or {}).get("label") or fallback


def vol_disp(entry):
    """期权波动率预警灯（独立一维，由 judge.py 判级写入 entry['vol']）"""
    lv = ((entry or {}).get("vol") or {}).get("level") or "🟢"
    if lv == "🔴":
        return red(b("🔴"))
    if lv == "🟡":
        return org(b("🟡"))
    return green(b("🟢"))


# ---------------- 信号总览（红灯 → 黄灯，置顶） ----------------

def _why_short(w):
    """压缩原因文本，便于一行呈现"""
    if not w:
        return ""
    s = str(w)
    s = s.replace("隐历差 IV−HV20", "隐历差").replace("隐历差 IV-HV20", "隐历差")
    s = s.replace("（绝对水位）", "").replace("的绝对水位", "")
    s = s.replace("（近月/次月隐波近端倒挂）", " 近端倒挂")
    m = re.search(r"R\s+([\d.]+)", s)
    if m:
        s = re.sub(r"R\s+[\d.]+", "R %.2f" % float(m.group(1)), s, count=1)
    s = re.sub(r"\s*≥\s*\+?[\d.]+(?:pct)?", "", s)   # 去掉阈值（pct 或纯数字）
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _vdetail(vs):
    return {d["key"]: (d.get("why") or "") for d in ((vs or {}).get("detail") or [])}


def signal_items(fin):
    """汇总两维信号：波动率预警 + 风险释放判灯
    返回 (reds, yels)：reds 为逐条 (维度, 标的, 原因)；yels 为按维度分组的列表"""
    A = fin.get("ashare") or {}
    U = fin.get("us") or {}
    VS = fin.get("vol_summary") or {}
    vA = VS.get("ashare") or {}
    vU = VS.get("us") or {}
    dA, dU = _vdetail(vA), _vdetail(vU)

    reds = []
    vol_yel, rel_yel = [], []

    # 波动率红灯
    for k in (vA.get("red") or []):
        reds.append(("波动率", A_NAMES.get(k, k), _why_short(dA.get(k))))
    for k in (vU.get("red") or []):
        reds.append(("波动率", U_NAMES.get(k, k), _why_short(dU.get(k))))

    # 风险释放红灯
    for k in A_ORDER:
        if "🔴" in light_of(A.get(k)):
            reds.append(("风险释放", A_NAMES.get(k, k), "近端倒挂 R≥1.00 连续触发"))
    for k, disp in US_ORDER:
        if "🔴" in light_of(U.get(k)):
            reds.append(("风险释放", disp, "红灯"))

    # 偏度分位报警（近月 25Δ 偏度一年分位 ≥ 阈值；美股 CBOE SKEW）
    for _mk, _nm, _sk, _pct, _p90 in skew_alarm_items():
        reds.append(("偏度分位", _nm, "一年分位 %.1f%% ≥ %g%%（偏度 %.2fpt）"
                      % (_pct or 0, SKEW_THR, _sk or 0)))

    # 波动率的波动率（VVIX）分位报警：与偏度分位同级，独立一维，逐标的独立判定。
    # 仅保留美股 CBOE VVIX（标普500，前瞻口径）；A股 5 标的 VVIX 已按用户口径停用。
    _vv = SKEW.get("vvix") or {}
    if _vv.get("alarm"):
        reds.append(("VVIX分位", _vv.get("label") or "标普500 VVIX",
                      "一年分位 %.1f%% ≥ %g%%（VVIX %.2f）"
                      % (_vv.get("pct") or 0, SKEW_THR, _vv.get("vvix") or 0)))

    # GEX / Gamma Flip 报警（净GEX<0 且 dist 一年分位 ≤ 阈值）
    for _nm, _gw, _fl, _dist, _pct, _thr in gex_alarm_items():
        reds.append(("GEX/Flip", _nm, "净GEX %.4f<0 ｜ dist %.2f%% ｜ 一年分位 %.1f%% ≤ %g%%"
                      % (_gw or 0, _dist or 0, _pct or 0, _thr)))

    # 波动率黄灯（逐条带原因）
    for k in (vA.get("yellow") or []):
        vol_yel.append((A_NAMES.get(k, k), _why_short(((A.get(k) or {}).get("vol") or {}).get("why"))))
    for k in (vU.get("yellow") or []):
        vol_yel.append((U_NAMES.get(k, k), _why_short(((U.get(k) or {}).get("vol") or {}).get("why"))))

    # 风险释放需盯（分档命中、未升红灯）
    for k in A_ORDER:
        if escalate_rank(A.get(k)) == 2:
            rel_yel.append(A_NAMES.get(k, k))
    for k, disp in US_ORDER:
        if escalate_rank(U.get(k)) == 2:
            rel_yel.append(disp)

    yels = []
    if vol_yel:
        yels.append(("波动率", vol_yel))
    if rel_yel:
        yels.append(("风险释放需盯", [(n, "") for n in rel_yel]))
    return reds, yels


def _yel_count(yels):
    return sum(len(items) for _, items in yels)


def signal_block(fin):
    """信号总览：红灯在前、黄灯随后，统一为对齐的表格（灯 / 标的 / 维度 · 起因）"""
    reds, yels = signal_items(fin)
    n_yel = _yel_count(yels)
    els = [div(b("📋 信号总览（红灯优先）") + grey("　🔴 %d · 🟡 %d" % (len(reds), n_yel))),
           cols([(1, b("灯")), (3, b("标的")), (5, b("维度 · 起因"))])]
    if reds:
        for dim, name, why in reds:
            tail = (" · " + why) if why else ""
            els.append(cols([(1, red("🔴")), (3, red(b(name))), (5, dim + tail)]))
    else:
        els.append(div(grey("本日无红灯")))
    if yels:
        for dim, items in yels:
            for n, w in items:
                tail = (" · " + w) if w else ""
                els.append(cols([(1, org("🟡")), (3, org(b(n))), (5, dim + tail)]))
    else:
        els.append(div(grey("本日无黄灯")))
    return els


# ---------------- 标的一览（精简表） ----------------

def levels_block(fin):
    A = fin.get("ashare") or {}
    U = fin.get("us") or {}
    head = [(3, b("标的")), (3, b("现价 / 隐波")), (2, b("波动率灯")), (2, b("风险灯"))]
    els = [div(b("A股五轨 ｜ 现价 / 隐波 / 波动率灯 / 风险灯")), cols(head)]
    for k in A_ORDER:
        e = A.get(k) or {}
        v = e.get("vals") or {}
        spot = fmt_spot(v.get("spot"))
        vix = fmt_pct(v.get("vix"))
        if k == "ZZ1000":
            vix = "MO IV " + vix
        els.append(cols([(3, b(A_NAMES.get(k) or nm(e, k))), (3, b(spot) + "\n" + vix),
                         (2, vol_disp(e)), (2, light_disp(e))]))

    els.append({"tag": "hr"})
    els.append(div(b("美股三轨 ｜ 现价 / 隐波 / 波动率灯 / 风险灯")))
    els.append(cols(head))
    for k, disp in US_ORDER:
        e = U.get(k)
        if not e:
            els.append(cols([(2, b(disp)), (5, grey("待更新"))]))
            continue
        v = e.get("vals") or {}
        etf, el = v.get("etf"), v.get("etf_last")
        amp = v.get("idx") or v.get("idx_name") or "隐波"
        al = v.get("idx_last")
        point = "%s %s\n%s %s" % (etf, b(fmt_spot(el, wide=2)), amp, b(fmt_spot(al, wide=2)))
        els.append(cols([(3, b(disp)), (3, point),
                         (2, vol_disp(e)), (2, light_disp(e))]))
    return els


# ---------------- 未来 5 日波动率预测（压缩块） ----------------

VF = json.load(open(os.path.join(D, "vol_forecast.json"), encoding="utf-8"))
VT = VF.get("targets") or {}
# 与 judge.py VOL_RULES["A"]["abs_red"] 同一份阈值
ABS_RED = {"KC50": (42.0, "VIX"), "CYB": (34.0, "VIX"), "ZZ1000": (28.0, "ATM-IV")}


def fc_target(k):
    return VT.get(k) or {}


# ---------------- 期权偏度分位（近月25Δ 认沽IV − 认购IV ｜ 美股 CBOE SKEW） ----------------

def _load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


SKEW = _load_json(os.path.join(D, "skew_pct_summary.json")) or {}
SKEW_THR = float(SKEW.get("thr", 90) or 90)


def skew_alarm_items():
    """偏度分位越阈（报警）列表：[(市场, 名称, 偏度, 分位, P90), ...]"""
    out = []
    for k in A_ORDER:
        v = (SKEW.get("cn") or {}).get(k)
        if v and v.get("alarm"):
            out.append(("A股", A_NAMES.get(k, k), v.get("skew"), v.get("pct"), v.get("p90")))
    u = SKEW.get("us")
    if u and u.get("alarm"):
        out.append(("美股", "标普500", u.get("skew"), u.get("pct"), u.get("p90")))
    return out


# A股 5 标的 VVIX（回望口径）已按用户口径停用：卡片不再输出该区块与对应报警项。


def skew_block():
    """偏度分位 / VVIX 区块：各标的 偏度(或 VVIX) / 一年分位 / P90，越阈标红"""
    els = [div(b("📐 期权偏度分位 & VVIX（近月25Δ认沽IV − 认购IV ｜ 美股 CBOE SKEW / VVIX）")
               + grey("　阈值 ≥%g%% 分位" % SKEW_THR))]
    if not SKEW:
        els.append(div(grey("待更新")))
        return els

    def _cell(pct, al, txt):
        c = red if al else (org if isinstance(pct, (int, float)) and pct >= SKEW_THR - 10 else green)
        return c(b(txt))

    els.append(cols([(3, b("标的")), (3, b("偏度")), (3, b("一年分位")), (3, b("P90"))]))
    for k in A_ORDER:
        v = (SKEW.get("cn") or {}).get(k)
        if not v:
            els.append(cols([(3, b(A_NAMES.get(k, k))), (9, grey("待更新"))]))
            continue
        els.append(cols([(3, b(A_NAMES.get(k, k))),
                         (3, b("%.2fpt" % (v.get("skew") or 0))),
                         (3, _cell(v.get("pct"), v.get("alarm"), "%s%%" % v.get("pct"))),
                         (3, grey("P90 %.2f" % (v.get("p90") or 0)))]))
    u = SKEW.get("us") or {}
    if u:
        els.append({"tag": "hr"})
        els.append(cols([(3, b("标普500(SKEW)")),
                         (3, b("%.2f" % (u.get("skew") or 0))),
                         (3, _cell(u.get("pct"), u.get("alarm"), "%s%%" % u.get("pct"))),
                         (3, grey("P90 %.2f" % (u.get("p90") or 0)))]))
        els.append(div(grey("A股：近月(剩余>20天) 25Δ认沽IV − 25Δ认购IV，delta 按各合约自身IV插值；"
                            "美股：CBOE SKEW 指数（数据日 %s）。" % (u.get("date") or "待更新"))))
    vv = SKEW.get("vvix") or {}
    els.append({"tag": "hr"})
    if vv:
        els.append(cols([(3, b("标普500(VVIX)")),
                         (3, b("%.2f" % (vv.get("vvix") or 0))),
                         (3, _cell(vv.get("pct"), vv.get("alarm"), "%s%%" % vv.get("pct"))),
                         (3, grey("P90 %.2f" % (vv.get("p90") or 0)))]))
        els.append(div(grey("VVIX=波动率的波动率（VIX 期权隐含波动率，标普500口径）；"
                            "纳指/罗素2000 无 VVIX 等价口径（数据日 %s）。"
                            % (vv.get("date") or "待更新"))))
    else:
        els.append(cols([(3, b("标普500(VVIX)")), (9, grey("待更新"))]))

    # A股 5 标的 VVIX（回望口径）已按用户口径停用，本区块不再输出。
    return els


# ---------------- A股五轨 期权 GEX / Gamma Flip ----------------

GEX = _load_json(os.path.join(D, "gex_flip_summary.json")) or {}
GEX_THR = float(GEX.get("thr", 5) or 5)
GEX_ORDER = ["300ETF", "500ETF", "KC50", "CYB", "ZZ1000"]


def _gex_rows():
    """逐标的明细，按固定顺序输出 [(key, dict), ...]；无数据返回 []"""
    tg = GEX.get("targets") or {}
    if not tg:
        return []
    keys = [k for k in GEX_ORDER if k in tg] + [k for k in tg if k not in GEX_ORDER]
    return [(k, tg[k]) for k in keys]


def gex_alarm_items():
    """GEX/Flip 越阈（报警）标的列表：[(名称, 净GEX, Flip, dist, 一年分位, 阈值), ...]，未报警返回 []"""
    if not GEX:
        return []
    return [(t.get("label") or t.get("key"), t.get("gex_weighted"), t.get("flip_1"),
             t.get("dist"), t.get("pct"), GEX_THR)
            for _k, t in _gex_rows() if t.get("alarm")]


def gex_block():
    """GEX/Flip 区块：逐标的 净GEX / Flip 价位 / dist·一年分位，越阈标红"""
    els = [div(b("⚙️ A股五轨 期权 GEX / Gamma Flip（净GEX<0 且 dist 一年分位 ≤%g%%）" % GEX_THR))]
    rows = _gex_rows()
    if not rows:
        els.append(div(grey("待更新")))
        return els

    els.append(cols([(2, b("标的")), (2, b("净GEX(亿元)")), (2, b("Flip 价位")),
                     (2, b("dist · 一年分位"))]))
    n_alarm = 0
    for _k, t in rows:
        gw, dist, pct, fl = t.get("gex_weighted"), t.get("dist"), t.get("pct"), t.get("flip_1")
        cond_a = t.get("cond_a")
        cond_b = t.get("cond_b")
        alarm = bool(t.get("alarm"))
        if alarm:
            n_alarm += 1
        # 净GEX 格：<0 红（条件A）；
        # dist格：报警(两条件同时满足)红 / 仅分位越阈(条件B但条件A不满足)橙 / 否则绿
        gx_txt = ("%.4f" % gw) if isinstance(gw, (int, float)) else "待更新"
        dd = ("%.2f%% · %.1f%%" % (dist, pct)
              if isinstance(dist, (int, float)) and isinstance(pct, (int, float)) else "待更新")
        gx_cell = (red if cond_a else green)(b(gx_txt))
        if alarm:
            dd_cell = red(b(dd))
        elif cond_b:
            dd_cell = org(b(dd))
        else:
            dd_cell = green(b(dd))
        name = (t.get("label") or t.get("key"))
        if alarm:
            name = "● " + name
        if t.get("level") == "yellow":
            name = name + "（黄·降级）"
        els.append(cols([(2, red(b(name)) if alarm else b(name)),
                         (2, gx_cell),
                         (2, b("%.4f" % fl) if isinstance(fl, (int, float)) else grey("—")),
                         (2, dd_cell)]))
    if n_alarm:
        els.append(div(red("▲ 越阈 %d/%d 个标的：%s"
                           % (n_alarm, len(rows), "、".join(t.get("label") or k
                                                            for k, t in rows if t.get("alarm"))))))
    else:
        els.append(div(green("○ 无标的越阈（A股五轨）")))
    els.append(div(grey("口径：净GEX=Σ sign·Γ·OI·MULT·S²·0.01（Call+1/Put−1；ETF 期权乘数 10000，"
                        "中证1000 股指期权 MO 乘数 100）；DTE 取 30~180，30/90 恒定期限线性插值加权；"
                        "Flip=[0.90S,1.10S] 网格上净GEX过零点；dist=(S−flip)/S；"
                        "报警=净GEX<0 且 dist 一年分位≤%g%%（同时满足）。数据日 %s。"
                        % (GEX_THR, GEX.get("asof") or "待更新"))))
    return els


def _abs_verdict(k, p):
    """绝对水位口径预判：区分当前是否已在该口径触发，避免把“本未触发”写成“红灯缓解”"""
    thr, unit = ABS_RED[k]
    cur = fc_target(k).get("last_vol")
    above_now = isinstance(cur, (int, float)) and cur > thr
    above_fc = p > thr
    if above_now and above_fc:
        return red("仍 ≥%g" % thr)
    if above_now and not above_fc:
        return green("↓ 回落<%g" % thr)
    if (not above_now) and above_fc:
        return red("⚠️ 升破%g" % thr)
    return grey("未触发")


def _cell(p):
    """单元格：点预测加粗一行 + 80% 区间一行，避免窄列把数字截断"""
    return "**%.2f**\n%.1f–%.1f" % (p["point"], p["lo"], p["hi"])


def forecast_block():
    els = [div(b("📈 未来 5 个交易日波动率预测（80% 区间）"))]

    t0 = fc_target(A_ORDER[0])
    a_dates = [d["date_fmt"] for d in (t0.get("forecast") or [])]
    if a_dates:
        els.append(div(grey("A股：自 %s 起 5 个交易日 %s"
                            % (iso8(t0.get("last_date") or ""), " / ".join(a_dates)))))
    els.append(cols([(3, b("标的")), (3, b("t+1")), (3, b("t+5")), (2, b("预判"))]))
    for k in A_ORDER:
        t = fc_target(k)
        fc = t.get("forecast") or []
        if not t.get("ok") or len(fc) < 5:
            els.append(cols([(2, b(A_NAMES.get(k, k))), (5, grey("待更新"))]))
            continue
        p1, p5 = fc[0], fc[4]
        v = _abs_verdict(k, p1["point"]) if k in ABS_RED else grey("—")
        els.append(cols([(3, b(A_NAMES.get(k, k))), (3, _cell(p1)), (3, _cell(p5)), (2, v)]))

    els.append({"tag": "hr"})
    _us_start = []
    for _k, _disp in US_ORDER:
        _ld = fc_target(_k).get("last_date")
        if _ld:
            _us_start.append("%s 自 %s" % (_disp, iso8(_ld)[5:]))
    els.append(div(grey("美股：按各指数自身最近可得收盘起算（%s）"
                        % ("；".join(_us_start) if _us_start else "待更新"))))
    els.append(cols([(3, b("标的")), (3, b("t+1")), (3, b("t+5")), (2, b("vs 均值"))]))
    for k, disp in US_ORDER:
        t = fc_target(k)
        fc = t.get("forecast") or []
        if not t.get("ok") or len(fc) < 5:
            els.append(cols([(2, b(disp)), (5, grey("待更新"))]))
            continue
        p1, p5 = fc[0], fc[4]
        lm = t.get("long_mean")
        if isinstance(lm, (int, float)):
            v = red("高于") if p5["point"] > lm else green("低于")
        else:
            v = grey("—")
        els.append(cols([(3, b(disp)), (3, _cell(p1)), (3, _cell(p5)), (2, v)]))

    els.append(div(grey("方法：x=ln(隐波)，OLS 拟合 x(t+1)=c+φ·x(t)（窗口 252 日），点预测=exp(μ+φ^h(x_t−μ))，"
                        "80% 区间 z=1.2816；EWMA(λ=0.94) 实现波动率仅作参照锚。模型外推，非官方发布值；"
                        "分位/隐历差口径不可外推，预判仅覆盖绝对水位阈值。")))
    return els


# ---------------- 组装 ----------------

def build():
    fin = json.load(open(os.path.join(D, "final.json"), encoding="utf-8"))
    asof = iso8(fin.get("asof", ""))
    DD = data_dates()
    reds, yels = signal_items(fin)
    n_yel = _yel_count(yels)

    els = []
    # ---- -1. 数据时点声明（最前置；用户 2026-11-05 口径：显式标识盘中/自算或过期）----
    _sl = _status_line()
    if _sl:
        els.append(div(_sl))
        els.append({"tag": "hr"})
    # ---- 0. 信号总览（红黄灯置顶，红灯在前） ----
    els.extend(signal_block(fin))
    els.append({"tag": "hr"})
    # ---- 1. 标的一览（精简表） ----
    els.extend(levels_block(fin))
    els.append({"tag": "hr"})
    # ---- 1b. 期权偏度分位（近月25Δ ｜ CBOE SKEW） ----
    els.extend(skew_block())
    els.append({"tag": "hr"})
    # ---- 1c. A股五轨 期权 GEX / Gamma Flip ----
    els.extend(gex_block())
    els.append({"tag": "hr"})
    # ---- 2. 未来 5 日波动率预测（压缩） ----
    els.extend(forecast_block())

    # ---- 脚注：口径与日期动态 ----
    vol_dates = {}
    for lbl, dt in (("VIX/SKEW", DD["vix"]), ("VXN/RVX", DD["vxn"])):
        if dt:
            vol_dates.setdefault(dt, []).append(lbl)
    us_parts = []
    if DD["etf"]:
        us_parts.append("ETF %s" % DD["etf"])
    for dt in sorted(vol_dates):
        us_parts.append("隐波 %s（%s）" % (dt, "/".join(vol_dates[dt])))
    us_date_txt = "、".join(us_parts) if us_parts else "日期待更新"
    if us_parts:
        us_date_txt += "（美东收盘）"
    if DD["chain"]:
        us_date_txt += "；期权链为延迟快照（北京 %s）" % DD["chain"]

    note = ("口径：A股以各标的 252 交易日基线独立判灯，缺数据标“待更新”，不插值、不跨标比绝对值；"
            "美股期权链历史缺失，R连3日 / 持仓PCR连3日 / GEX分位标“待更新”。"
            "波动率预警为独立一维：红灯=252日分位≥90%% 或 绝对水位（科创50 VIX>42 / 创业板 VIX>34 / "
            "中证1000 ATM-IV>28）或 隐历差 IV−HV20≥+8pct；黄灯=分位≥70%%（科创50/创业板 75%%）或 "
            "隐历差≥+5pct 或 R≥1.00。偏度分位为独立一维：近月(剩余>20天) 25Δ认沽IV − 25Δ认购IV"
            "（美股为 CBOE SKEW 指数），取各标的自身近一年 252 交易日分位，分位 ≥%g%% 即报警（红）。"
            "波动率的波动率 VVIX 为独立一维：美股 CBOE VVIX（标普500，VIX 期权隐含波动率，前瞻口径），"
            "取自身近一年 252 交易日分位，分位 ≥%g%% 即报警（红）；纳指/罗素2000 无 VVIX 等价口径。"
            "A股 5 标的 VVIX（回望口径）已按用户口径停用，不再展示与报警。"
            "GEX/Flip 为独立一维：A股五轨（沪深300ETF/中证500ETF/科创50ETF/创业板ETF/中证1000 MO）逐标的判定，"
            "净GEX<0 且 Gamma Flip 距离 dist 一年分位 ≤%g%% 时该标的报警（红），同一数据日越阈标的合并出图。"
            "A股隐波=链上自算 30 日恒定期限 ATM IV（原第三方 QVIX 源已停用）；"
            "数据时点：A股 %s 盘中（当日实时自算）；美股 %s。"
            "本内容由人工智能基于公开信息生成，仅供研究，不构成投资建议。") % (SKEW_THR, SKEW_THR, GEX_THR, asof, us_date_txt)

    els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "red",
                       "title": {"tag": "plain_text",
                                 "content": "中美期权风险释放汇总｜A股五轨·美股三轨｜🔴%d · 🟡%d｜%s盘中｜%s来自 DoMate"
                                            % (len(reds), n_yel, asof, "注意风险 | " if reds else "")}},
            "elements": els,
        },
    }
    return card


def send(card):
    import feishu_config as fc
    import requests
    r = requests.post(fc.WEBHOOK_URL, json=card,
                      headers={"Content-Type": "application/json"}, timeout=30)
    d = r.json()
    print("[webhook]", json.dumps(d, ensure_ascii=False))
    if d.get("code") != 0 and d.get("StatusCode") != 0:
        raise RuntimeError("飞书推送失败: %s" % (d.get("msg") or d.get("StatusMessage")))
    return True


if __name__ == "__main__":
    card = build()
    out = os.path.join(D, "card_payload.json")
    json.dump(card, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[card] written", out)
    if "--send" in sys.argv:
        print("[send]", send(card))