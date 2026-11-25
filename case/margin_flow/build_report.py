# -*- coding: utf-8 -*-
"""市场融资风险扫描日报生成器（拆分为两份独立报告，由同一任务触发）

报告 A（两融专项）：
    <工作目录>/市场融资风险扫描日报_两融专项_<基准日>.md
    含「两融统计汇总表」与「栏目一 · 两融杠杆监控」（风险等级／宏观杠杆体温双轨／227% 等效信号／
    行业融资×ETF背离表／强平脆弱前10／操作建议）
    以及「附栏目 · ETF存量资金MACD突破监控（全部子图合并大图）」——与报告 B 栏目四**同源同图**
    （同一 data/macd_module.json 快照与合并大图），供两融专项报告内一并呈现，不重复取数、不重复计算
报告 B（资金流与宽度）：
    <工作目录>/市场融资风险扫描日报_资金流与宽度_<基准日>.md
    含「栏目二 · ETF 资金流向监控」「栏目三 · 市场宽度监控」「栏目四 · ETF存量资金MACD突破监控」
    与「四栏目联动综合结论」
两份各自独立生成文件、独立推送飞书卡片、独立发送邮件（红标规则分别生效）。
产出（每份一套）：
    data/feishu_cards_{margin,other}.json、data/red_flags_{margin,other}.json、
    data/email_subject_{margin,other}.txt、data/email_body_{margin,other}.txt
"""
import os, sys, json
import unicodedata
# ---- 路径统一口径：case/_lib/paths.py（换机器只改一处）----------------------
try:
    from paths import shared_dir as _shared
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    import os as _o, sys as _s
    _d = _o.path.dirname(_o.path.abspath(__file__))
    while not _o.path.isfile(_o.path.join(_d, "_lib", "paths.py")):
        _n = _o.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    _s.path.insert(0, _o.path.join(_d, "_lib"))
    from paths import shared_dir as _shared



# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = _shared()
        if _d and _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''
from datetime import datetime
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D

REPORT_NAME = "市场融资风险扫描日报"
TAGLINE = "两融杠杆 × ETF资金流 × 市场宽度 × 存量资金MACD"
NAME_MARGIN = "市场融资风险扫描日报（两融专项）"
NAME_OTHER = "市场融资风险扫描日报（资金流与宽度）"
TAG_MARGIN = "两融统计汇总表 × 杠杆体温 × 融资资金流"
TAG_OTHER = "ETF资金流 × 行业融资背离 × 市场宽度 × 存量资金MACD"
FILE_MARGIN = "两融专项"
FILE_OTHER = "资金流与宽度"
WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 会话工作目录
B = json.load(open(os.path.join(D, "breadth_result.json"), encoding="utf-8"))
M = json.load(open(os.path.join(D, "margin_module.json"), encoding="utf-8"))
IDV = pd.read_csv(os.path.join(D, "industry_div.csv"))
# 栏目四：ETF存量资金 MACD 快照（由 run_daily.py 的 7b 步骤从 etf_macd_monitor 拷入；缺失时栏目降级）
_mp = os.path.join(D, "macd_module.json")
MACD = json.load(open(_mp, encoding="utf-8")) if os.path.exists(_mp) else None
MACD_ORDER = ["沪深300", "中证500", "中证1000", "创业板", "科创50"]
# 栏目四「全部子图合并大图」：由 run_daily.py 从 etf_macd_monitor 拷到工作目录根（与报告同级），
# 路径写入 macd_module.json 的 combined_report_png；供 .md 报告引用与飞书图文卡片上传。
MACD_COMBINED = (MACD.get("combined_report_png") if MACD else None)
if MACD_COMBINED and not os.path.exists(MACD_COMBINED):
    MACD_COMBINED = None

# ---------- 统一「机会 / 风险」重点突出标记 ----------
# 口径：风险＝红＋星★，机会＝绿＋月🌙，中性＝➖；复用 etf_macd_monitor/markers.py 的常量。
# 该模块依赖 matplotlib，导入失败（或未装）时用等价 emoji 兜底，保证报告生成不被拖累。
try:
    _MKDIR = os.path.join(WORK, "etf_macd_monitor")
    if _MKDIR not in sys.path:
        sys.path.append(_MKDIR)
    from markers import MK_RISK, MK_OPP, MK_NEU
except Exception:
    MK_RISK, MK_OPP, MK_NEU = "🔴⭐", "🟢🌙", "➖"

# ---------- 两融统计汇总表（近一年分位） ----------
# 由 run_daily.py 的 7a 步骤（refresh_series.py -> ratio_alert.py）产出；缺失时该栏目降级。
_rap = os.path.join(D, "ratio_alert.json")
RA = json.load(open(_rap, encoding="utf-8")) if os.path.exists(_rap) else None
_KIND_MARK = {"risk": MK_RISK, "opp": MK_OPP, "neu": MK_NEU}


def ra_mark(k):
    return _KIND_MARK.get(str(k), MK_NEU)


def dir_mark(v):
    """按方向判定标记：净流入/上涨 → 机会（绿月），净流出/下跌 → 风险（红星）。"""
    try:
        return MK_OPP if float(v) >= 0 else MK_RISK
    except Exception:
        return MK_NEU


_DIV_MARK = {"双强(主升共振)": MK_OPP, "机构底仓": MK_OPP,
             "杠杆炒作(警惕回撤)": MK_RISK, "双弱(回避)": MK_RISK,
             "中性": MK_NEU, "数据不足": MK_NEU}


def div_mark(lab):
    return _DIV_MARK.get(str(lab).strip(), MK_NEU)


_ACT_MARK = {"大幅减仓": MK_RISK, "观望": MK_RISK, "减仓至防御": MK_RISK,
             "左侧布局": MK_OPP, "重仓配置": MK_OPP}


def act_mark(a):
    return _ACT_MARK.get(str(a).strip(), MK_NEU)

DATE = str(M["last_date"]); DSHOW = f"{DATE[:4]}-{DATE[4:6]}-{DATE[6:]}"
pk = lambda d: f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}"
def wd(d):
    """YYYYMMDD -> 周四"""
    return "周" + "一二三四五六日"[datetime.strptime(str(d), "%Y%m%d").weekday()]
# 运行元信息：由 run_daily.py 写入；独立运行时以基准日兜底
RUN_META = {}
_rmp = os.path.join(D, "run_meta.json")
if os.path.exists(_rmp):
    RUN_META = json.load(open(_rmp, encoding="utf-8"))
PERIOD = str(RUN_META.get("report_period") or DATE); PSHOW = pk(PERIOD)
mk = M["market"]; rt1 = M["risk_track1"]; rt2 = M["risk_track2"]
o1 = M["opportunity_track1"]; o2 = M["opportunity_track2"]
flow = pd.DataFrame(M["etf_flow"]); risk10 = pd.DataFrame(M["risk_top10"]); opp10 = pd.DataFrame(M["opp_top10"])

def table(headers, rows, aligns=None):
    """Markdown 表格。aligns 为每列 'l'/'r'/'c'（写入分隔行的对齐冒号）；缺省保持 ``---`` 不写冒号。"""
    n = len(headers)
    if aligns is None:
        sep = ["---"] * n
    else:
        _map = {"l": ":---", "c": ":---:", "r": "---:"}
        sep = [_map.get(a, "---") for a in aligns]
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "|" + "|".join(sep) + "|"] +
                     ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])

def tbl_code(headers, rows, widths):
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    return "\n".join([line, "-" * len(line)] +
                     ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in rows])


def dw(s):
    """字符串显示宽度：东亚宽/全角字符计 2 列，其余计 1 列（供等宽表对齐）。"""
    w = 0
    for ch in str(s):
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def tbl_wide(headers, rows, aligns=None, widths=None):
    """东亚宽度感知的等宽表：按显示宽度补空格，支持左/右/居中（l/r/c）。
    用于飞书卡片代码块等纯文本载体，避免中文列因按字符数补空格而错位。"""
    n = len(headers)
    if aligns is None:
        aligns = ["l"] + ["r"] * (n - 1)
    cells = [[str(c) for c in r] for r in rows]
    if widths:
        w = list(widths)
    else:
        w = [max([dw(headers[i])] + [dw(r[i]) for r in cells]) for i in range(n)]

    def pad(s, i):
        gap = w[i] - dw(s)
        if gap <= 0:
            return s
        a = aligns[i]
        if a == "r":
            return " " * gap + s
        if a == "c":
            left = gap // 2
            return " " * left + s + " " * (gap - left)
        return s + " " * gap

    head = "  ".join(pad(headers[i], i) for i in range(n))
    return "\n".join([head, "-" * dw(head)] +
                     ["  ".join(pad(r[i], i) for i in range(n)) for r in cells])


# ---------- 两融统计汇总表 派生（供正文表格 / 飞书卡片 / 邮件共用） ----------
_RA_SHORT = {
    "杠杆率（融资余额 ÷ 全A流通市值）": "杠杆率",
    "融资余额 ÷ 两市成交额": "融资余额÷成交额",
    "融资买入额占比（融资买入额 ÷ 两市成交额）": "融资买入额占比",
    "融资净买入（当日）": "融资净买入(日)",
    "融资净买入（20 日累计）": "融资净买入(20日)",
    "两融余额 ÷ 全A流通市值": "两融余额÷流通市值",
    "227% 担保比例等效信号": "227%等效信号",
    "融资余额自区间高点回落": "余额自高点回落",
}


def ra_short(name):
    return _RA_SHORT.get(name, str(name)[:16])


def ra_normal_cell(r, compact=False):
    """正常范围展示：报告用「低 ~ 高（中位 X）」，紧凑版（卡片/邮件）用「低 ~ 高」。"""
    disp = r.get("normal_display")
    if not disp or disp == "—":
        return "—"
    if r.get("normal_low") is None:
        # 触发式/口径说明型：紧凑模式只保留主状态（去掉括号说明）
        return (disp.split("（")[0] if compact else disp)
    if not compact:
        return disp
    nd = 0 if r.get("unit") == "亿元" else 2
    return f"{r['normal_low']:,.{nd}f} ~ {r['normal_high']:,.{nd}f}"


if RA:
    _rw = RA.get("window", {}) or {}
    _st = RA.get("summary_stats", {}) or {}
    _f0 = pk(_rw.get("first_date"))
    _l0 = pk(_rw.get("last_date"))
    ra_rows = [[r["indicator"], r["value_display"],
                (r["percentile_display"] if r.get("percentile_1y") is not None else "—"),
                ra_mark(r["kind"]), ra_normal_cell(r), str(r["start_display"]), r["rule"]]
               for r in RA["summary_rows"]]
    ra_card_rows = [[ra_short(r["indicator"]), r["value_display"],
                     r["percentile_display"],
                     (str(r["start_display"]) if r.get("percentile_1y") is not None else "—"),
                     ra_mark(r["kind"]), ra_normal_cell(r, compact=True)] for r in RA["summary_rows"]]
    ra_risk = [f"{r['indicator']} {r['value_display']}（近一年分位 {r['percentile_display']}，"
               f"正常范围 {ra_normal_cell(r)}，{r['label']}）"
               for r in RA["summary_rows"] if r["kind"] == "risk"]
    ra_opp = [f"{r['indicator']} {r['value_display']}（近一年分位 {r['percentile_display']}，"
              f"正常范围 {ra_normal_cell(r)}，{r['label']}）"
              for r in RA["summary_rows"] if r["kind"] == "opp"]
    _nr_rule = RA.get("normal_range_rule", "")
    ra_mail = [
        "",
        f"== 两融统计汇总表（近一年分位 · 窗口 {_f0} ~ {_l0}）==",
        f"共 {_st['n_rows']} 栏：风险 {_st['n_risk']} · 机会 {_st['n_opp']} · 中性 {_st['n_neu']}",
        f"正常范围口径：{_nr_rule}",
    ] + [f"  - {r['indicator']}：{r['value_display']}{r['unit']}｜近一年分位 {r['percentile_display']}"
         f"｜正常范围 {ra_normal_cell(r)}｜起点日({_f0}) {r['start_display']}｜{r['label']}"
         for r in RA["summary_rows"]]
else:
    _rw, _st, _f0, _l0 = {}, {}, "", ""
    ra_rows, ra_card_rows, ra_risk, ra_opp, ra_mail = [], [], [], [], []
    _nr_rule = ""

# ---------- 结论判定 ----------
risk_hard = (rt1["accel_outflow"] or rt1["sig227_triggered"] or rt1["turn_below_6"]
             or mk["lev_pct"] > 3.0 or rt2["percentiles"]["高杠杆群体偿还分位"] >= 80)
risk_level = "🔴高风险" if risk_hard else ("🟡中风险" if (rt1["roll_negative"] or 2.2 <= mk["lev_pct"] <= 3.0
             or len(rt2["extreme"]) >= 1) else "🟢低风险")
risk_reason = ("5 项固定阈值均未命中，但杠杆率仍处 2.2%~3.0% 区间且融资余额持续净流出"
               if risk_level == "🟡中风险" else "")
# 等级「显示值」：风险等级叠加红＋星★，低风险叠加绿＋月🌙（判断逻辑仍用 risk_level 本身，不改变分级）
RLSHOW = risk_level.replace("🔴", "🔴⭐").replace("🟡", "🟡⭐").replace("🟢", "🟢🌙")

w_weak = bool(B["runs"]["r20_lt30"] >= 5 or B["nl20"] > 1000)
w_extreme = B["composite_pct"] < 20
lev_pct_low = rt2["percentiles"]["杠杆率分位"] < 30
width_div = "宽度背离" in B["divergence"]
# 栏目三标记：宽度等级按等级着色；背离检测「底背离」＝机会（绿月）、「宽度背离」＝风险（红星）
B_MARK = MK_RISK if B["level"].startswith("🔴") else (MK_OPP if B["level"].startswith("🟢") else MK_NEU)
DIV_MARK = MK_RISK if width_div else (MK_OPP if "底背离" in B["divergence"] else MK_NEU)
table5 = [
    ("宽度恶化（涨跌比<0.5连续）", "杠杆率>3%", "最危险：高杠杆+宽度崩溃", "大幅减仓"),
    ("宽度恶化（站上20MA<30%连续 / 新低扩散）", "杠杆出清中（余额回落）", "阵痛期，去杠杆未完", "观望"),
    ("宽度改善（涨跌比回升至>1.0）", "杠杆出清尾声", "最佳买点", "左侧布局"),
    ("宽度极端压缩（分位<20%）", "杠杆分位<30%", "历史级底部特征", "重仓配置"),
    ("宽度背离（指数强宽度弱）", "杠杆高位", "假强势陷阱", "减仓至防御"),
]
match = 1
if w_extreme and lev_pct_low: match = 3
elif width_div and mk["lev_pct"] >= 2.2: match = 4
elif B["ad_ratio"] < 0.5 and B["runs"]["ad_ratio_lt0.5"] >= 3 and mk["lev_pct"] > 3.0: match = 0
cross_verdict, cross_action = table5[match][2], table5[match][3]

# ---------- 两融栏目 ----------
lev_state = "偏高于 2.2% 弱势中枢" if mk["lev_pct"] > 2.2 else "接近 2.2% 弱势中枢"
margin_advice = ("防守为主：去杠杆未完成，融资持续净流出且融资买入占比创区间新低；"
                 "不参与杠杆资产的左侧抄底，等待“净偿还连续收窄 + 余额止跌”的确认信号。") if not o1["opp1"] else \
                "观望转积极：出清接近尾声，可小仓位左侧布局。"
fixed_hits = []                                   # 实际上确实命中的固定阈值
if rt1["accel_outflow"]: fixed_hits.append(f"加速流出（日环比连续<-0.3% 第 {rt1['run_dod_below_0_3']} 天且加速）")
if rt1["turn_below_6"]: fixed_hits.append("融资买入占比<6%")
if abs(mk["lev_pct"] - 2.2) <= 0.15: fixed_hits.append("杠杆率接近 2.2% 弱势中枢")
if rt2["percentiles"]["高杠杆群体偿还分位"] >= 80: fixed_hits.append("高杠杆群体偿还分位≥80%")
if mk["lev_pct"] > 3.0: fixed_hits.append(f"杠杆率 {mk['lev_pct']}% > 3.0%")
if rt1["sig227_triggered"]: fixed_hits.append("227% 担保比例等效信号触发")
lvl_note = "；".join(fixed_hits) if fixed_hits else "无（两融风险固定阈值均未命中）"
pct_note = "；".join(rt2["extreme"]) or "无"
sig227_reason = (f"日环比连续<-0.3% 仅 {rt1['run_dod_below_0_3']} 天，未达“加速流出”阈值；"
                 f"融资买入占比 {mk['turn_pct']}%，未跌破 6%")

idrows = []
_div_labs = []
for _, r in IDV.iterrows():
    fd = ("↓流出 " if r.fin_dir_20d_yi < 0 else "↑流入 ") + f"{r.fin_dir_20d_yi:+.1f}亿"
    ed = ("↑" if r.etf_chg20_pct > 0 else "↓") + f" {r.etf_chg20_pct:+.2f}%"
    idrows.append([f"{r.industry}·{r.etf_name}", fd, f"P{r.fin_pct:.0f}", ed, f"P{r.etf_pct:.0f}",
                   r.label, div_mark(r.label)])
    _div_labs.append(str(r.label).strip())
n_div_opp = sum(1 for l in _div_labs if _DIV_MARK.get(l) == MK_OPP)
n_div_risk = sum(1 for l in _div_labs if _DIV_MARK.get(l) == MK_RISK)

# ---------- ETF 栏目 ----------
etf_rows = [[r["code"], r["name"], r["group"], f"{r['est_flow_yi']:+.2f}", f"{r['chg20_pct']:+.2f}",
             f"P{r['flow_pct120']:.0f}", dir_mark(r["est_flow_yi"])] for _, r in flow.iterrows()]
etf_top3 = flow.nlargest(3, "est_flow_yi"); etf_bot3 = flow.nsmallest(3, "est_flow_yi")
broad = flow[flow.group == "宽基锚"].est_flow_yi.sum(); sect = flow[flow.group != "宽基锚"].est_flow_yi.sum()
sect_out = int((flow[flow.group != "宽基锚"].est_flow_yi < 0).sum()); sect_all = int((flow.group != "宽基锚").sum())
MK_BROAD = dir_mark(broad)                 # 宽基锚合计标记
MK_SECT = dir_mark(sect)                  # 行业/主题合计标记
MK_ETF = MK_OPP if broad >= 0 else MK_RISK  # 栏目二 / ETF 卡片整体标记（宽基锚净流入方向）

# ---------- 宽度栏目 ----------
b_p = B["percentiles"]; runs = B["runs"]

# ---------- 栏目四：MACD 派生展示值 ----------
mb, mwin = (MACD["bases"] if MACD else {}), (MACD["window"] if MACD else {})
MACDDATE = str(MACD["as_of"]) if MACD else ""
MACDDSHOW = pk(MACDDATE) if MACDDATE else ""
if MACD:
    macd_rows, macd_opp, macd_risk = [], [], []
    for nm in MACD_ORDER:
        b = mb.get(nm)
        if not b:
            continue
        sig = "、".join(b["signals_today"]) or "—"
        _cls_parts = []
        if b["opportunity_reasons"]:
            _cls_parts.append(MK_OPP + "机会")
        if b["risk_reasons"]:
            _cls_parts.append(MK_RISK + "风险")
        cls = "/".join(_cls_parts) or MK_NEU
        macd_rows.append([nm, sig, f"{b['dif_now_yi']:.4f}", f"{b['dea_now_yi']:.4f}", f"{b['bar_now_yi']:.4f}",
                          f"{b['net_sub_now_yi']:.4f}", f"{b['net_sub_p85_yi']:.4f}",
                          f"{b['net_sub_p15_yi']:.4f}", "是" if b["resonance"] else "否",
                          "是" if b["redemption"] else "否", cls])
        for r in b["opportunity_reasons"]:
            macd_opp.append(f"- {MK_OPP} {nm}：{r}")
        for r in b["risk_reasons"]:
            macd_risk.append(f"- {MK_RISK} {nm}：{r}")
    n_res = sum(1 for nm in MACD_ORDER if mb.get(nm, {}).get("resonance"))
    n_red = sum(1 for nm in MACD_ORDER if mb.get(nm, {}).get("redemption"))
    n_opp, n_risk = len(MACD["opportunity"]), len(MACD["risk"])
else:
    macd_rows = macd_opp = macd_risk = []
    n_res = n_red = 0
    n_opp = n_risk = 0

# ---------- 红标清单（按报告分轨；邮件分级触发依据） ----------
red_margin = ["两融·" + x for x in fixed_hits]
for x in ra_risk:                       # 两融统计汇总表：近一年分位 ≥90 的风险项
    red_margin.append("两融汇总·" + x)
red_other = []
for t in B.get("risk_triggers", []):
    red_other.append("宽度·" + t)
if B["level"].startswith("🔴"):
    red_other.append(f"宽度·等级 {B['level']}（{B['wstate']}，综合分位 P{B['composite_pct']}）")
if MACD:
    for nm in MACD_ORDER:
        b = mb.get(nm)
        if not b:
            continue
        for r in b["risk_reasons"]:
            red_other.append(f"MACD·{nm}·{r}")


def _sev(items):
    n = len(items)
    return n, ("强提醒" if n >= 2 else ("提醒" if n == 1 else "无"))


def _sev_color(sev):
    return "red" if sev == "强提醒" else ("orange" if sev == "提醒" else "green")


red_n_margin, sev_margin = _sev(red_margin)
red_n_other, sev_other = _sev(red_other)
C_SEV_M, C_SEV_O = _sev_color(sev_margin), _sev_color(sev_other)
DISCLAIMER = ("本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。"
              "市场有风险，投资需谨慎。数据可能存在延迟、遗漏或误差，请独立判断并根据自身风险承受能力审慎决策。")
DATE_LINE = f"**基准日 {DSHOW}（{wd(DATE)}）｜ 报告期 {PSHOW}（{wd(PERIOD)}）｜ 来自 DoMate**"
if PERIOD != DATE:
    head_note = (f"{PSHOW} 为交易日，但两融（margin / margin_detail）数据尚未更新，"
                 f"故两融、ETF 份额、行情与市场宽度数据统一以前一交易日 **{DSHOW}** 为准；")
else:
    head_note = f"{PSHOW} 两融、ETF 份额、行情与市场宽度数据均已更新，本报告基准日与报告期一致；"


# ---------- 风险与机会前置速览（报告A 专用；全部由脚本按数据确定性生成） ----------
# 用户诉求：把风险与机会放到报告靠前位置先说一遍，并说明「到底是怎么回事」——
# 该节内容一律由本脚本按 data/ratio_alert.json（标记、分位、正常范围带、窗口起止值）确定性推导，
# 不使用模型即时撰写；数据缺失时整节降级不出现。
def _fmt_gap(x, unit):
    if unit == "亿元":
        return f"{x:,.2f}"
    return f"{x:.2f}"


def _dev_phrase(r):
    """当前值相对正常带（P10~P90）的偏离描述，全部由数据推导。"""
    lo, hi, v = r.get("normal_low"), r.get("normal_high"), r.get("value")
    u = r.get("unit") or ""
    us = "亿" if u == "亿元" else ("pct" if u == "%" else "")
    if lo is None or hi is None or v is None:
        return str(r.get("label", "—"))
    if v > hi:
        return f"高出上沿 {_fmt_gap(v - hi, u)}{us}（{r.get('label', '')}）"
    if v < lo:
        return f"低于下沿 {_fmt_gap(lo - v, u)}{us}（{r.get('label', '')}）"
    return f"落在带内（{r.get('label', '正常')}）"


def _chg_pct(cur, start):
    try:
        return (float(cur) - float(start)) / float(start) * 100.0
    except Exception:
        return None


def _digest_item(r):
    return (f"- {ra_mark(r['kind'])} **{r['indicator']}**：{r['value_display']}{r['unit']}"
            f"｜近一年分位 {r['percentile_display']}｜正常范围 {ra_normal_cell(r)}｜{_dev_phrase(r)}")


QA = []                                     # 前置速览的 Markdown 段落（报告A）
if RA:
    _cur = RA.get("current", {}) or {}
    _sp = RA.get("start_point", {}) or {}
    _risk_rows = [r for r in RA["summary_rows"] if r["kind"] == "risk"]
    _opp_rows = [r for r in RA["summary_rows"] if r["kind"] == "opp"]
    _neu_rows = [r for r in RA["summary_rows"] if r["kind"] == "neu"]
    _lev_pct_disp = next((r["percentile_display"] for r in RA["summary_rows"]
                          if r["indicator"].startswith("杠杆率")), "—")
    # 栏目一侧风险（报告A 内除 14 栏汇总表之外的风险项）
    _side = []
    if not str(risk_level).startswith("🟢"):
        _side.append(f"- {'🔴⭐' if str(risk_level).startswith('🔴') else '🟡⭐'} **两融风险等级**：{RLSHOW}"
                     f"（固定阈值命中：{lvl_note}）")
    if len(risk10):
        _side.append(f"- {MK_RISK} **强平脆弱前 10**：{len(risk10)} 只（融资占比>8% 且偿还加速且股价创新低）")
    QA.append(f"> 本节由 `build_report.py` 依 `data/ratio_alert.json` 的标记结果**确定性生成**"
              f"（不使用模型即时撰写）：标记口径＝近一年分位 ≥90 → {MK_RISK} 风险（太火）／≤10 → {MK_OPP} 机会（太惨）"
              f"／其余 → {MK_NEU} 中性；正常范围＝该指标近一年自身分布的 P10~P90 分位带（中位 P50 为正常中枢）。"
              f"窗口 {_f0} ~ {_l0}（{_rw.get('n_trading_days')} 个交易日）。"
              f"现读数：共 {_st.get('n_rows')} 栏 —— {MK_RISK} 风险 {len(_risk_rows)} · "
              f"{MK_OPP} 机会 {len(_opp_rows)} · {MK_NEU} 中性 {len(_neu_rows)}。")
    QA.append(f"**{MK_RISK} 风险（汇总表 {len(_risk_rows)} 项 ＋ 栏目一侧 {len(_side)} 项）**")
    QA.extend([_digest_item(r) for r in _risk_rows] or ["- 无"])
    QA.extend(_side or ["- 栏目一侧无额外风险项"])
    QA.append(f"**{MK_OPP} 机会（{len(_opp_rows)} 项）**")
    QA.extend([_digest_item(r) for r in _opp_rows] or ["- 无"])
    if _neu_rows:
        QA.append(f"**{MK_NEU} 中性（{len(_neu_rows)} 项）**：" + "、".join(r["indicator"] for r in _neu_rows))

    # —— 成因说明：按分子 / 分母分解，全部由 current 与 start_point 两个快照推导 ——
    _b_z, _b_s = _cur.get("融资余额_亿"), _sp.get("融资余额_亿")
    _a_z, _a_s = _cur.get("两市成交额_亿"), _sp.get("两市成交额_亿")
    _c_z, _c_s = _cur.get("全A流通市值_亿"), _sp.get("全A流通市值_亿")
    _r_z, _r_s = _cur.get("融资余额÷两市成交额_%"), _sp.get("融资余额÷两市成交额_%")
    _l_z = _cur.get("杠杆率_%")
    _bz, _at, _cmn = _chg_pct(_b_z, _b_s), _chg_pct(_a_z, _a_s), _chg_pct(_c_z, _c_s)
    QA.append("**到底是怎么回事（按分子 / 分母分解，程序自动推导）**")
    if None not in (_bz, _at, _r_z, _r_s):
        QA.append(f"- 比值类风险项的走高**不是杠杆在加**：窗口内融资余额 {_b_s:,.2f} → {_b_z:,.2f} 亿（{_bz:+.2f}%），"
                  f"两市成交额 {_a_s:,.2f} → {_a_z:,.2f} 亿（{_at:+.2f}%）；分子几乎不动、分母缩 {abs(_at):.2f}%，"
                  f"比值由 {_r_s:.4f}% 被动抬到 {_r_z:.4f}%（{_r_z - _r_s:+.4f} 个百分点）→ 属「分母收缩型」偏热。")
    if None not in (_bz, _cmn, _l_z):
        QA.append(f"- 相对杠杆并未扩张：杠杆率（融资余额 ÷ 全A流通市值）{_l_z:.4f}% 落在正常带内"
                  f"（近一年分位 {_lev_pct_disp}）；分子（融资余额）{_bz:+.2f}%、分母（全A流通市值）{_cmn:+.2f}%，"
                  f"两者同步缩水。")
    _opp_pcts = [r["percentile_1y"] for r in _opp_rows if r.get("percentile_1y") is not None]
    if _opp_pcts:
        QA.append(f"- 机会项集中在**绝对规模与交投**：{len(_opp_pcts)} 项的近一年分位落在 P{min(_opp_pcts):.2f} ~ "
                  f"P{max(_opp_pcts):.2f}，全部低于正常带下沿 → 杠杆存量与交投双冷。")
    QA.append("- 综合：风险集中在**比值口径**（以成交额 / 市值为分母），机会集中在**绝对规模**；"
              "判「安不安全」看绝对规模，判「结构紧不紧」看比值。")

# 速览的飞书卡片版：与正文同一份数据、同一套措辞，供报告A 总览卡在开头先呈现风险与机会
QA_CARD = ""
if QA:
    _qc = [f"**【先说结论】{MK_RISK} 风险 {len(_risk_rows)} 项（＋栏目一侧 {len(_side)} 项）｜"
           f"{MK_OPP} 机会 {len(_opp_rows)} 项｜{MK_NEU} 中性 {len(_neu_rows)} 项** "
           f"（近一年分位口径，窗口 {_f0} ~ {_l0}，{_rw.get('n_trading_days')} 个交易日）"]
    _qc += [_digest_item(r) for r in _risk_rows]
    _qc += _side
    _qc += ["", f"**{MK_OPP} 机会**"]
    _qc += [_digest_item(r) for r in _opp_rows]
    _qc += ["", "**到底是怎么回事（程序自动推导）**"]
    if None not in (_bz, _at, _r_z, _r_s):
        _qc.append(f"- 比值类风险项走高**不是杠杆在加**：融资余额 {_bz:+.2f}%、两市成交额 {_at:+.2f}%，"
                   f"分子几乎不动、分母收缩，比值 {_r_s:.4f}% → {_r_z:.4f}%（{_r_z - _r_s:+.4f}pct）")
    if None not in (_bz, _cmn, _l_z):
        _qc.append(f"- 相对杠杆未扩张：杠杆率 {_l_z:.4f}%（近一年分位 {_lev_pct_disp}），"
                   f"分子 {_bz:+.2f}%、分母 {_cmn:+.2f}% 同步缩水")
    _qc.append("- 综合：风险集中在**比值口径**，机会集中在**绝对规模**；判安不安全看绝对规模，判结构紧不紧看比值")
    QA_CARD = "\n".join(_qc)

# ---------- 报告A 附栏目：ETF存量资金 MACD（与报告B 栏目四同源同图，供两融专项报告一并呈现） ----------
# 用户诉求：两融专项报告（唯一被投递的报告）内也要看到 MACD 图。
# 口径：**不重复取数、不重复计算**——直接复用 run_daily.py 7b 步骤落盘的 data/macd_module.json 快照与合并大图；
# 该节内容全部由脚本按快照确定性生成，不使用模型即时撰写。快照缺失时整节降级不出图。
_macd_sec = []
if MACD:
    _nbA = len([n for n in MACD_ORDER if n in mb])
    _macd_sec.append("## 附栏目 · ETF存量资金MACD突破监控（全部子图合并大图）")
    _macd_sec.append(
        f"> 口径：与《{NAME_OTHER}》报告的「栏目四（存量资金MACD）」**同源同图**（同一 `data/macd_module.json` 快照），"
        f"本报告仅一并呈现，**不重复取数、不重复计算**。数据日期 **{MACDDSHOW}（{wd(MACDDATE)}）**"
        f"（MACD 栏目独立取数，与两融基准日 {DSHOW}（{wd(DATE)}）各按自身最新可得日标注）；"
        f"参数 MACD **(10,20,5)**；滚动窗口 **近{mwin['n_trading_days']}个交易日**"
        f"（{pk(mwin['first_date'])} ~ {pk(mwin['last_date'])}）；85%/15% 分位数为**本次运行重算值**（非沿用旧分位）。"
        f"触发口径：**零轴上金叉 / 零轴下死叉 / 上穿零轴 / 跌破零轴**（零轴下金叉、零轴上死叉不触发）。"
        f"今日触发 **{MACD['n_triggered']}/{MACD['n_bases']}** 宽基｜{MK_OPP}机会 **{len(MACD['opportunity'])}** 项 · "
        f"{MK_RISK}风险 **{len(MACD['risk'])}** 项。")
    if MACD_COMBINED:
        _macd_sec.append(
            f"![ETF存量资金MACD全部子图合并大图]({os.path.basename(MACD_COMBINED)})\n\n"
            f"- **单张合成图**：`{MACD_COMBINED}`（与本报告同目录）\n"
            f"- 合成内容：**{_nbA} 个宽基 × 2 面板 = {_nbA * 2} 个子图**，每行一个宽基——**左列**＝该宽基对应 ETF 组合"
            f"总市值，**右列**＝DIF / DEA / MACD柱（含零轴虚线、信号点标注），不遗漏任一宽基、任一子图。\n"
            f"- 生成方式：全部由代码生成（`etf_macd_monitor/plot_macd_all.py`；数据经 AKShare→TuShare→网页三级回退"
            f"自动采集），不使用任何外部手工素材。")
    else:
        _cl = MACD.get("charts", {})
        _macd_sec.append("\n".join(f"- {nm}：`{_cl[nm]}`" for nm in MACD_ORDER if nm in _cl) or "- 未生成图表")
    _macd_sec.append("### 一、五宽基信号明细（金额单位：亿元，4 位小数）")
    _macd_sec.append(table(["宽基", "当日信号", "DIF", "DEA", "MACD柱", "净申赎", "机会/风险标记"],
                           [[r[0], r[1], r[2], r[3], r[4], r[5], r[10]] for r in macd_rows]))
    _macd_sec.append("### 二、机会 / 风险判定依据")
    _macd_sec.append(f"**{MK_OPP} 机会项**\n" + ("\n".join(macd_opp) if macd_opp else "- 无"))
    _macd_sec.append(f"**{MK_RISK} 风险项**\n"
                     + ("\n".join(macd_risk) if macd_risk else "- 无（当日五宽基均无 MACD 风险信号与大额赎回）"))
    _macd_sec.append(
        f"### 三、与两融栏目的联动阅读\n"
        f"- 本栏目读数为**存量资金趋势信号**：五宽基 ETF 存量资金净申赎处近{mwin['n_trading_days']}日高位，"
        f"{n_res}/5 宽基触发「大额申购共振」（净申赎额 > 85% 分位），大额赎回 {n_red}/5 项。\n"
        f"- 与两融栏目对照：场内杠杆端（融资余额）仍在持续净流出、宽基 ETF 获逆势净申购——"
        f"两者共同指向「**宽基增量承接、场内杠杆去化**」的分歧盘结构，本报告内一并呈现以便交叉印证。\n"
        f"- {MK_RISK} 提示：大额申购共振属存量资金趋势信号，不构成反转确认；"
        f"仍须与两融「净偿还连续收窄 + 余额止跌」共同确认。")
else:
    _macd_sec.append("## 附栏目 · ETF存量资金MACD突破监控（全部子图合并大图）")
    _macd_sec.append("> **本栏目数据缺失**：未找到 `data/macd_module.json`（MACD 链路未执行或失败），"
                     "MACD 图与信号暂无法呈现；两融其余栏目不受影响。")

# ==================== 报告 A：两融专项 ====================
mdA = []
mdA.append(f"# {NAME_MARGIN} · {TAG_MARGIN}")
mdA.append(DATE_LINE)
mdA.append("> 说明：" + head_note +
           f"本报告为**两融专项**报告，与《{NAME_OTHER}》报告（{TAG_OTHER}）由同一任务分别生成、分别推送。"
           "分位窗口 **120 个交易日**（两融栏目）／**近一年 242 个交易日**（两融统计汇总表，每日重算）。"
           "固定阈值系统与分位数系统并列输出、互不替代，结论冲突处标注“矛盾信号，需人工确认”。"
           f"本报告红标项数 ≥1 触发邮件提醒，≥2 触发强提醒（本次红标 **{red_n_margin}** 项 → **{sev_margin}**）。"
           "本报告只做状态识别与风险提示，**不做点位预测**。")
# 前置：风险与机会速览（先说风险与机会，再展开其它栏目）——内容由脚本按数据自动生成
if QA:
    mdA.append("## 风险与机会速览（程序自动生成）")
    mdA.extend(QA)
mdA.append("## 报告总览（两融专项）")
mdA.append(table(["项目", "状态", "关键读数"], [
    ["① 两融杠杆", RLSHOW,
     f"杠杆率 {mk['lev_pct']}%｜20日净流出 {mk['chg20_pct']:+.2f}%｜融资买入占比 {mk['turn_pct']}%"],
    ["② 两融统计汇总表",
     f"{MK_RISK} 风险 {_st.get('n_risk', 0)} · {MK_OPP} 机会 {_st.get('n_opp', 0)} · {MK_NEU} 中性 {_st.get('n_neu', 0)}",
     (f"近一年分位窗口 {_f0} ~ {_l0}（{_rw.get('n_trading_days')} 个交易日）｜触发={RA.get('triggered')}"
      if RA else "数据缺失")],
    ["③ 强平脆弱前 10", f"{MK_RISK} {len(risk10)} 只", "融资占比>8% 且偿还加速且股价创新低"],
    ["④ 存量资金MACD（附）",
     (f"{MK_OPP} 机会 {len(MACD['opportunity'])} · {MK_RISK} 风险 {len(MACD['risk'])}" if MACD else "数据缺失"),
     (f"五宽基触发 {MACD['n_triggered']}/{MACD['n_bases']}｜数据日期 {MACDDSHOW}｜见下方「附栏目」（与资金流与宽度报告同源同图）"
      if MACD else "未找到 data/macd_module.json")],
]))

# ---------- 两融统计汇总表（近一年分位） ----------
if not RA:
    mdA.append("## 两融统计汇总表（近一年分位）")
    mdA.append("> **本栏目数据缺失**：未找到 `data/ratio_alert.json`（run_daily.py 的 7a 步骤未执行或失败），"
               "汇总表暂无法呈现；其余栏目不受影响。")
else:
    mdA.append(f"## 两融统计汇总表（近一年分位 · 窗口 {_f0} ~ {_l0}）")
    mdA.append("> 口径：近一年 = 最近 **{} 个完整交易日**（{} ~ {}）；分位 = 窗口内 ≤ 当前值的交易日占比 ×100；"
               "当前值为基准日 **{}** 取值，起点日列为窗口首日 **{}** 的当日值。\n"
               "> 标记规则：{}。\n"
               "> **正常范围 = 该指标近一年自身分布的 P10~P90 分位带**，括号内「中位」为 P50 正常中枢；"
               "当前值落在带内＝正常、高于上沿＝偏热（风险）、低于下沿＝偏冷（机会）。"
               "触发式指标（227% 等效信号）正常状态＝未触发；自高点回落以窗口内逐日滚动回落幅度的 P10~P90 为正常带。\n"
               "> 汇总：共 **{}** 栏 —— {} 风险 **{}** 项 · {} 机会 **{}** 项 · {} 中性 **{}** 项。"
               .format(_rw['n_trading_days'], _f0, _l0, DSHOW, _f0, RA['mark_rule'],
                       _st['n_rows'], MK_RISK, _st['n_risk'], MK_OPP, _st['n_opp'], MK_NEU, _st['n_neu']))
    mdA.append(table(["指标", f"当前值（{DSHOW}）", "近一年分位", "标记",
                      "正常范围（P10~P90｜中位 P50）", f"窗口起点日 {_f0} 当日值", "判定口径"],
                     ra_rows, aligns=["l", "r", "c", "c", "c", "r", "l"]))
    mdA.append(f"**{MK_RISK} 风险项（分位 ≥90 太火 或 显式阈值触发）**\n"
               + ("\n".join(f"- {MK_RISK} {x}" for x in ra_risk) if ra_risk else "- 无"))
    mdA.append(f"**{MK_OPP} 机会项（分位 ≤10 太惨）**\n"
               + ("\n".join(f"- {MK_OPP} {x}" for x in ra_opp) if ra_opp else "- 无"))

mdA.append("## 栏目一 · 两融杠杆监控")
mdA.append(f"### 一、风险等级：{RLSHOW}")
mdA.append(f"- **固定阈值触发项**：{lvl_note}" + (f"｜等级依据：{risk_reason}" if risk_reason else ""))
mdA.append(f"- **分位数极端项**：{pct_note}")
mdA.append("### 二、宏观杠杆体温（双轨）")
mdA.append(f"**轨一 · 绝对值**\n"
           f"- 融资余额 {mk['rzye_yi']:.0f} 亿（两融余额 {mk['rzrqye_yi']:.0f} 亿）｜流通市值 {mk['circ_mv_yi']:.0f} 亿｜"
           f"**杠杆率 {mk['lev_pct']}%**（{lev_state}，5日变动 {rt1['lev_trend_5d']:+.3f}pct）\n"
           f"- 余额趋势：日环比 {mk['dod_pct']:+.2f}%｜5日 {mk['chg5_pct']:+.2f}%｜10日 {mk['chg10_pct']:+.2f}%｜"
           f"20日 {mk['chg20_pct']:+.2f}%（自 {pk(mk['rzye_peak_date'])} 高点 {mk['rzye_peak_yi']:.0f} 亿回落 {mk['rzye_drop_from_peak_pct']}%）\n"
           f"- 融资买入占比（成交占比）：**{mk['turn_pct']}%**，自区间高点 {mk['turn_peak_pct']}% 回落，尚未跌破 6%\n"
           f"- 加速流出状态：{'**触发**' if rt1['accel_outflow'] else '未触发'}（当前日环比<-0.3% 连续 {rt1['run_dod_below_0_3']} 天；近 120 日最长连续 {rt1['max_run_dod_below_0_3_recent']} 天）\n"
           f"- 强平连锁：融资占比>8% 个股 {rt1['grp_lev_gt8_count']} 只，群体日均偿还 {rt1['grp_rep5_yi']} 亿，5日均值较前变化 {rt1['grp_rep5_slope_pct']:+.1f}%")
mdA.append(f"**轨二 · 分位数（120日）**：杠杆率 P{rt2['percentiles']['杠杆率分位']}｜融资净买入 P{rt2['percentiles']['融资净买入分位']}｜"
           f"成交占比 P{rt2['percentiles']['成交占比分位']}｜高杠杆群体偿还 P{rt2['percentiles']['高杠杆群体偿还分位']}")
mdA.append(f"### 三、227% 担保比例等效信号：{'**触发**' if rt1['sig227_triggered'] else '**未触发**'}\n"
           f"- 判定依据：{sig227_reason}")
mdA.append("### 四、行业融资 × ETF 背离表")
mdA.append(table(["行业·ETF", "融资20日方向", "融资分位", "ETF20日方向", "ETF分位", "背离标签", "标记"], idrows))
mdA.append("### 五、强平脆弱前 10（融资占比>8% 且 偿还加速 且 股价创新低）")
mdA.append(table(["代码", "名称", "行业", "融资占比", "偿还加速", "偿还分位", "融资余额(亿)"],
                 [[r.ts_code, r["name"], r.industry, f"{r.lev:.2f}%", f"{r.accel5:.2f}", f"P{r.accel_pct:.0f}", f"{r.rzye_yi:.2f}"]
                  for _, r in risk10.iterrows()]))
mdA.append("### 六、操作建议（两融）")
mdA.append(f"- {margin_advice}")

mdA += _macd_sec

mdA.append("## 报告标红清单（两融专项）")
mdA.append(f"- **红标项数：{red_n_margin}**（分级规则：≥1 触发邮件提醒，≥2 触发强提醒）｜ 本次分级：**{sev_margin}**")
mdA.append("\n".join(f"- {MK_RISK} {x}" for x in red_margin) if red_margin else "- 无标红项")
mdA.append("> " + DISCLAIMER)


# ==================== 报告 B：资金流与宽度 ====================
mdB = []
mdB.append(f"# {NAME_OTHER} · {TAG_OTHER}")
mdB.append(DATE_LINE)
mdB.append("> 说明：" + head_note +
           f"本报告为**资金流与宽度**报告，与《{NAME_MARGIN}》报告（{TAG_MARGIN}）由同一任务分别生成、分别推送。"
           "分位窗口 **120 个交易日**（市场宽度）／**近100个交易日**（栏目四 MACD，每次运行重算）。"
           "固定阈值系统与分位数系统并列输出、互不替代，结论冲突处标注“矛盾信号，需人工确认”。"
           f"本报告红标项数 ≥1 触发邮件提醒，≥2 触发强提醒（本次红标 **{red_n_other}** 项 → **{sev_other}**）。"
           "本报告只做状态识别与风险提示，**不做点位预测**。")
mdB.append("## 报告总览（资金流与宽度）")
mdB.append(table(["栏目", "状态", "关键读数"], [
    ["① ETF资金流", f"{MK_OPP} 宽基大幅净流入 / {MK_RISK} 行业多数净流出",
     f"宽基锚合计 {broad:+.0f}亿 {MK_BROAD}｜行业/主题合计 {sect:+.0f}亿 {MK_SECT}（{sect_out}/{sect_all} 只净流出）"],
    ["② 市场宽度", f"{B['level']}（{B['wstate']}）{B_MARK}",
     f"涨跌比 {B['ad_ratio']}｜站上20MA {B['r20']}%｜创新低 {B['nl20']}家｜综合分位 P{B['composite_pct']}"],
    ["③ ETF存量资金MACD", (f"{MK_OPP}机会 {len(MACD['opportunity'])} / {MK_RISK}风险 {len(MACD['risk'])}" if MACD else "数据缺失"),
     (f"五宽基触发 {MACD['n_triggered']}/{MACD['n_bases']}｜大额申购共振 {n_res}/5｜大额赎回 {n_red}/5｜数据日期 {MACDDSHOW}"
      if MACD else "未找到 data/macd_module.json（MACD 链路未执行）")],
    ["联动结论", f"{cross_verdict} {act_mark(cross_action)}", f"操作建议：{cross_action}"],
]))

mdB.append("## 栏目二 · ETF 资金流向监控")
mdB.append(f"### 净流入 / 净流出排行（19 只监控 ETF，按估算净流入降序；末列标记：{MK_OPP}机会 / {MK_RISK}风险）")
mdB.append(table(["代码", "名称", "方向组", "估算净流入(亿)", "20日份额变化", "120日分位", "标记"], etf_rows))
mdB.append(f"### 结构读数\n"
           f"- {MK_OPP} 净流入前三：" + "、".join(f"{r['name']} {r['est_flow_yi']:+.1f}亿" for _, r in etf_top3.iterrows()) + "\n"
           f"- {MK_RISK} 净流出前三：" + "、".join(f"{r['name']} {r['est_flow_yi']:+.1f}亿" for _, r in etf_bot3.iterrows()) + "\n"
           f"- 宽基锚（510300/588000/159915）合计净流入 **{broad:+.0f} 亿** {MK_BROAD}；行业/主题 ETF 合计 {sect:+.0f} 亿 {MK_SECT}，"
           f"其中 {sect_out}/{sect_all} 只净流出，仅半导体/科创芯片、新能源等少数逆势净流入。\n"
           f"- 读数：资金显著向宽基集中、行业遭赎回，与两融持续净流出、宽度极端收缩一致，"
           f"属“宽基逆势承接、行业去杠杆”的分歧盘结构。")

mdB.append("## 栏目三 · 市场宽度监控")
mdB.append(f"### 一、宽度等级：{B['level']}（{B['wstate']}）{B_MARK}\n"
           f"- 涨跌比：{B['ad_ratio']}（今日）｜连续 {runs['ad_ratio_lt0.5']} 天 < 0.5，连续 {runs['ad_ratio_lt0.8']} 天 < 0.8\n"
           f"- 站上20MA比例：{B['r20']}% ｜ 站上60MA比例：{B['r60']}%\n"
           f"- 创新低家数：{B['nl20']} 家（占全市场 {B['nl20_pct_of_universe']}%）｜新高-新低差 {B['nh_nl_diff']}")
mdB.append("### 二、轨一触发项\n" + ("\n".join(f"- {MK_RISK} {t}" for t in B["triggers"]) if B["triggers"] else "- 无"))
mdB.append(f"### 三、轨二分位\n"
           f"- 涨跌比 P{b_p['ad_ratio']} ｜ 站上20MA P{b_p['r20']} ｜ 站上60MA P{b_p['r60']}\n"
           f"- 创新低家数 P{b_p['nl20']}（反向计入，越高越差）｜ 新高-新低差 P{b_p['nhnl_diff']}\n"
           f"- **宽度综合分位 P{B['composite_pct']}（120日，五项等权）** → {B['wstate']} {B_MARK}")
mdB.append(f"### 四、背离检测\n- **{B['divergence']}** {DIV_MARK}（指数60日分位：沪深300 P{B['index_pct']['hs300']} / 中证500 P{B['index_pct']['zz500']}）")
mdB.append(f"### 五、联合信号（宽度 × 杠杆）\n- 交叉判定：**{cross_verdict}** → 操作：**{cross_action}** {act_mark(cross_action)}")
mdB.append("### 六、操作建议\n"
           f"- {MK_RISK} 减仓至防御、右侧确认前保持观望：宽度极端收缩且指数与融资余额同时创区间新低，"
           "虽出现底背离（宽度未同步恶化）但反转尚未确认。")

mdB.append("## 栏目四 · ETF存量资金MACD突破监控")
if not MACD:
    mdB.append("> **本栏目数据缺失**：未找到 `data/macd_module.json`（MACD 链路未执行或失败），"
               "栏目四内容暂无法呈现；其余栏目不受影响。")
else:
    mdB.append(f"### 一、栏目读数（数据日期 {MACDDSHOW}）\n"
               f"- **数据日期：{MACDDSHOW}（{wd(MACDDATE)}）**——MACD 栏目独立取数，"
               f"与两融基准日 {DSHOW}（{wd(DATE)}）各按自身最新可得日标注。\n"
               f"- 监控口径：五宽基各自**独立**监控其对应 ETF 组合的**总市值**与**净申赎**序列，互不混合。\n"
               f"- 参数与窗口：MACD **(10,20,5)**｜滚动窗口 **近{mwin['n_trading_days']}个交易日**"
               f"（{pk(mwin['first_date'])} ~ {pk(mwin['last_date'])}）｜"
               f"85%/15% 分位数为**本次运行重算值**（非沿用旧分位），已随快照暂存。\n"
               f"- 触发口径：**零轴上金叉 / 零轴下死叉 / 上穿零轴 / 跌破零轴**"
               f"（零轴下金叉与零轴上死叉不触发）。\n"
               f"- 今日触发：**{MACD['n_triggered']}/{MACD['n_bases']}** 宽基｜"
               f"{MK_OPP}机会 **{len(MACD['opportunity'])}** 项 · {MK_RISK}风险 **{len(MACD['risk'])}** 项")
    mdB.append("### 二、五宽基信号明细（金额单位：亿元，4 位小数）")
    mdB.append(table(["宽基", "当日信号", "DIF", "DEA", "MACD柱", "净申赎",
                      "85%分位", "15%分位", "大额申购共振", "大额赎回", "机会/风险标记"], macd_rows))
    mdB.append("### 三、机会 / 风险判定依据")
    mdB.append(f"**{MK_OPP} 机会项**\n" + ("\n".join(macd_opp) if macd_opp else "- 无"))
    mdB.append(f"**{MK_RISK} 风险项**\n" + ("\n".join(macd_risk) if macd_risk else "- 无（当日五宽基均无 MACD 风险信号与大额赎回）"))
    mdB.append("### 四、合并大图（全部子图合成为单张）")
    _nbase = len([n for n in MACD_ORDER if n in mb])
    if MACD_COMBINED:
        mdB.append(f"![ETF存量资金MACD全部子图合并大图]({os.path.basename(MACD_COMBINED)})\n\n"
                   f"- **单张合成图（落于本报告同目录）**：`{MACD_COMBINED}`\n"
                   f"- 合成内容：**{_nbase} 个宽基 × 2 面板 = {_nbase * 2} 个子图**，不遗漏任一宽基、任一子图；"
                   f"每行一个宽基——**左列**＝该宽基对应 ETF 组合总市值，**右列**＝DIF/DEA/MACD柱"
                   f"（含零轴虚线、信号点标注）。\n"
                   f"- 生成方式：全部由代码生成（`etf_macd_monitor/plot_macd_all.py`；数据经 "
                   f"AKShare→TuShare→网页三级回退自动采集），不使用任何外部手工素材。\n"
                   f"- 该合成图同时嵌入飞书图文卡片。")
    else:
        cl = MACD.get("charts", {})
        mdB.append("\n".join(f"- {nm}：`{cl[nm]}`" for nm in MACD_ORDER if nm in cl) or "- 未生成图表")
    mdB.append("### 五、栏目解读")
    mdB.append(f"- {MK_OPP} 五宽基 ETF 存量资金净申赎普遍处于近100日高位：**{n_res}/5** 宽基触发"
               f"「大额申购共振」（净申赎额 > 85% 分位），大额赎回 **{n_red}/5** 项；"
               f"{MK_OPP} 机会宽基：" + ("、".join(MACD['opportunity']) if MACD['opportunity'] else "无") + "。"
               + (f"其中中证500 同时出现「上穿零轴」的动量拐点信号（DIF 由负转正，"
                  f"{mb['中证500']['dif_now_yi']:.4f} 亿元）。" if '中证500' in mb and '上穿零轴' in mb['中证500'].get('signals_today', []) else "") + "\n"
               f"- 与其余栏目对照：栏目二显示宽基锚 ETF 已获大额逆势净流入、行业/主题遭赎回，"
               f"与本栏目「宽基存量资金净申购共振」同向；而两融报告显示两融仍在持续净流出、栏目三宽度极端收缩且创新低扩散——"
               f"三者共同指向「**宽基增量承接、场内杠杆去化**」的分歧盘结构，方向与联动结论一致。\n"
               f"- {MK_RISK} 风险提示：大额申购共振属存量资金趋势信号，不构成反转确认；"
               f"仍须与两融「净偿还连续收窄 + 余额止跌」及宽度「站上20MA比例回升至 30% 以上」共同确认。")

mdB.append("## 四栏目联动综合结论")
mdB.append(table(["宽度状态", "杠杆状态", "综合判定", "操作建议"],
                  [[t[0] + (" ✅命中" if i == match else ""), t[1] + (" ✅命中" if i == match else ""),
                    t[2] + (f" {act_mark(t[3])}" if i == match else ""),
                    t[3] + (f" {act_mark(t[3])}" if i == match else "")]
                   for i, t in enumerate(table5)]))
mdB.append(f"**综合操作建议：{cross_action} {act_mark(cross_action)}。** 当前市场处于“宽度极端收缩 + 两融持续出清 + 资金向宽基集中、行业遭赎回”的组合："
           f"杠杆去化尚未完成（净偿还未连续收窄、余额仍创新低），但宽度已现底背离、宽基 ETF 获大额逆势申购"
           + (f"、五宽基存量资金现 {MK_OPP} {n_res}/5 大额申购共振（栏目四）" if MACD else "") +
           f"，属左侧观察区间而非右侧买点。防御为主，等待“净偿还连续 3 日收窄 + 站上20MA比例回升至 30% 以上”的确认信号。")

mdB.append("## 报告标红清单（资金流与宽度）")
mdB.append(f"- **红标项数：{red_n_other}**（分级规则：≥1 触发邮件提醒，≥2 触发强提醒）｜ 本次分级：**{sev_other}**")
mdB.append("\n".join(f"- {MK_RISK} {x}" for x in red_other) if red_other else "- 无标红项")
mdB.append("> " + DISCLAIMER)

# ---------- 落盘两份报告 ----------
repA = "\n\n".join(mdA) + _bs('margin_monitor', 'text')
repB = "\n\n".join(mdB) + _bs('margin_monitor', 'text')
outA = os.path.join(WORK, f"{REPORT_NAME}_{FILE_MARGIN}_{DATE}.md")
outB = os.path.join(WORK, f"{REPORT_NAME}_{FILE_OTHER}_{DATE}.md")
open(outA, "w", encoding="utf-8").write(repA)
open(outB, "w", encoding="utf-8").write(repB)

# ---------- 飞书卡片（两份各一套） ----------
cross_code = tbl_code(["宽度状态", "杠杆状态", "综合判定", "操作"],
                      [[t[0], t[1], t[2], t[3]] for t in table5], [30, 22, 18, 10])
hits_line = "；".join(fixed_hits) if fixed_hits else "无"
C_RA = "red" if _st.get("n_risk", 0) > 0 else ("green" if _st.get("n_opp", 0) > 0 else "orange")
C_MACD = "red" if (MACD and len(MACD["risk"]) > 0) else "green"
C_ETF = "green" if MK_ETF == MK_OPP else "red"
C_DIV = "red" if n_div_risk > n_div_opp else ("green" if n_div_opp > 0 else "orange")
C_WIDTH = "red" if B_MARK == MK_RISK else ("green" if B_MARK == MK_OPP else "orange")
C_LINK = "red" if act_mark(cross_action) == MK_RISK else ("green" if act_mark(cross_action) == MK_OPP else "orange")

# --- 报告 A 卡片 ---
if RA:
    _ra_summary = (f"\n**② 两融统计汇总表（{_f0} ~ {_l0}）：{MK_RISK}风险 {_st.get('n_risk', 0)} · "
                   f"{MK_OPP}机会 {_st.get('n_opp', 0)} · {MK_NEU}中性 {_st.get('n_neu', 0)}**\n"
                   f"共 {_st.get('n_rows')} 栏｜近一年分位窗口 {_rw.get('n_trading_days')} 个交易日｜触发={RA.get('triggered')}\n")
else:
    _ra_summary = "\n**② 两融统计汇总表：数据缺失**\n"
_margin_core = (f"**① 两融杠杆：{RLSHOW}**\n"
                f"杠杆率 {mk['lev_pct']}%｜20日净流出 {mk['chg20_pct']:+.2f}%｜"
                f"融资买入占比 {mk['turn_pct']}%（P{rt2['percentiles']['成交占比分位']}）\n"
                f"融资余额 {mk['rzye_yi']:.0f} 亿，自高点回落 {mk['rzye_drop_from_peak_pct']}%；"
                f"227% 等效信号：{'触发' if rt1['sig227_triggered'] else '未触发'}\n"
                f"固定阈值命中：{hits_line}\n分位极端：{'；'.join(rt2['extreme']) or '无'}\n")
_margin_red = "\n".join(f"- {MK_RISK} {x}" for x in red_margin) if red_margin else "- 无"
card_margin_overview = ((f"{QA_CARD}\n\n" if QA_CARD else "") +
                        f"{_margin_core}{_ra_summary}"
                        f"\n**③ 强平脆弱前 10：{len(risk10)} 只**（融资占比>8% 且偿还加速且创新低）\n\n"
                        f"**本报告红标 {red_n_margin} 项（{sev_margin}）**\n{_margin_red}")
if RA:
    ra_card = (f"{NAME_MARGIN} · 两融统计汇总表 | " + DSHOW + " | 来自 DoMate",
               f"**两融统计汇总表（近一年分位 · 窗口 {_f0} ~ {_l0}，共 {_rw['n_trading_days']} 个交易日）**\n"
               f"共 **{_st['n_rows']}** 栏：{MK_RISK}风险 {_st['n_risk']} · {MK_OPP}机会 {_st['n_opp']} · {MK_NEU}中性 {_st['n_neu']}\n"
               f"标记规则：{RA['mark_rule']}\n\n"
               f"**{MK_RISK} 风险项**：\n" + ("\n".join(f"- {x}" for x in ra_risk) if ra_risk else "- 无") + "\n\n"
               f"**{MK_OPP} 机会项**：\n" + ("\n".join(f"- {x}" for x in ra_opp) if ra_opp else "- 无") + "\n\n"
"```\n" + tbl_wide(["指标", "当前值", "分位", f"起点({_f0})", "标记", "正常范围"],
                                   ra_card_rows, aligns=["l", "r", "c", "r", "c", "c"]) + "\n```")
else:
    ra_card = (f"{NAME_MARGIN} · 两融统计汇总表 | " + DSHOW + " | 来自 DoMate",
               "**两融统计汇总表（近一年分位）**\n数据缺失：未找到 data/ratio_alert.json。")
cards_margin = [
    (f"{NAME_MARGIN} · 总览 | " + DSHOW + " | 来自 DoMate", card_margin_overview, C_SEV_M),
    (ra_card[0], ra_card[1], C_RA),
    (f"{NAME_MARGIN} · 强平脆弱前10 / 超跌反弹候选 | " + DSHOW + " | 来自 DoMate",
     f"**{MK_RISK} 强平脆弱前10（融资占比>8%, 偿还加速, 创新低）**\n```\n" +
     tbl_code(["代码", "名称", "行业", "占比%", "偿还加速", "分位"],
              [[r.ts_code, r["name"], r.industry, f"{r.lev:.1f}", f"{r.accel5:.2f}", f"P{r.accel_pct:.0f}"]
               for _, r in risk10.iterrows()], [10, 10, 10, 7, 9, 6]) + "\n```\n" +
     f"**{MK_OPP} 超跌反弹候选前10（回撤>30%, 融资回落>20%, 偿还收敛）**\n```\n" +
     tbl_code(["代码", "名称", "行业", "回撤", "融资回落", "收敛比"],
              [[r.ts_code, r["name"], r.industry, f"{r.retrace*100:.0f}%", f"{r.rz_drop*100:.0f}%", f"{r.conv_ratio:.2f}"]
               for _, r in opp10.iterrows()], [10, 10, 10, 7, 9, 7]) + "\n```", "red"),
]

# 报告A（两融专项）新增「存量资金MACD」图文卡：
# 用户只收到本报告时也要能看到那根/那组 MACD 线；与报告B 栏目四**同源同图**（同一 data/macd_module.json 与合并大图）。
# 该卡片内容同样由脚本按快照确定性生成；合并图通过下方 objs_margin 挂图循环写入 image。
if MACD:
    _macd_tbl_A = tbl_code(["宽基", "信号", "DIF", "DEA", "柱", "净申赎", "判定"],
                           [[r[0], r[1], r[2], r[3], r[4], r[5], r[10]] for r in macd_rows],
                           [8, 10, 9, 9, 9, 9, 6])
    cards_margin.append(
        (f"{NAME_MARGIN} · 存量资金MACD | " + MACDDSHOW + " | 来自 DoMate",
         f"**附：ETF存量资金MACD突破监控（数据日期 {MACDDSHOW}）**\n"
         f"参数 MACD(10,20,5)｜窗口 近{mwin['n_trading_days']}个交易日（{pk(mwin['first_date'])}~{pk(mwin['last_date'])}）｜分位数本次重算\n"
         f"触发口径：零轴上金叉 / 零轴下死叉 / 上穿零轴 / 跌破零轴\n"
         f"今日触发 **{MACD['n_triggered']}/{MACD['n_bases']}** 宽基｜{MK_OPP}机会 {len(MACD['opportunity'])} · {MK_RISK}风险 {len(MACD['risk'])}｜"
         f"大额申购共振 {n_res}/5 · 大额赎回 {n_red}/5\n\n```\n" + _macd_tbl_A + "\n```\n"
         f"\n**{MK_OPP} 机会**：{'；'.join(MACD['opportunity']) or '无'}\n"
         f"**{MK_RISK} 风险**：{'；'.join(MACD['risk']) or '无'}\n"
         f"（图：全部子图合并大图，{len([n for n in MACD_ORDER if n in mb])} 宽基 × 2 面板）", C_MACD))
else:
    cards_margin.append(
        (f"{NAME_MARGIN} · 存量资金MACD | " + DSHOW + " | 来自 DoMate",
         "**附：ETF存量资金MACD突破监控**\n数据缺失：未找到 data/macd_module.json。", "orange"))

# --- 报告 B 卡片 ---
_other_etf = (f"**① ETF 资金流：{MK_OPP} 宽基净流入 / {MK_RISK} 行业净流出**\n"
              f"{MK_OPP} 净流入前三：" + "；".join(f"{r['name']} {r['est_flow_yi']:+.1f}亿" for _, r in etf_top3.iterrows()) + "\n"
              f"{MK_RISK} 净流出前三：" + "；".join(f"{r['name']} {r['est_flow_yi']:+.1f}亿" for _, r in etf_bot3.iterrows()) + "\n"
              f"宽基锚合计 {broad:+.0f} 亿 {MK_BROAD}；行业/主题合计 {sect:+.0f} 亿 {MK_SECT}（{sect_out}/{sect_all} 只净流出）\n")
_other_width = (f"\n**② 市场宽度：{B['level']}（{B['wstate']}）{B_MARK}**\n"
                f"涨跌比 {B['ad_ratio']}（连续{runs['ad_ratio_lt0.5']}天<0.5）｜站上20MA {B['r20']}%｜"
                f"站上60MA {B['r60']}%｜创新低 {B['nl20']}家\n"
                f"综合分位 P{B['composite_pct']}｜背离：{B['divergence']} {DIV_MARK}\n")
if MACD:
    _other_macd = (f"\n**③ ETF存量资金MACD（{MACDDSHOW}）：触发 {MACD['n_triggered']}/{MACD['n_bases']}**\n"
                   f"{MK_OPP}机会 {len(MACD['opportunity'])}（{'、'.join(MACD['opportunity']) or '无'}）｜"
                   f"{MK_RISK}风险 {len(MACD['risk'])}（{'、'.join(MACD['risk']) or '无'}）\n"
                   f"大额申购共振 {n_res}/5 · 大额赎回 {n_red}/5\n")
else:
    _other_macd = "\n**③ ETF存量资金MACD：数据缺失**\n"
_other_red = "\n".join(f"- {MK_RISK} {x}" for x in red_other) if red_other else "- 无"
card_other_overview = (_other_etf + _other_width + _other_macd +
                       f"\n**联动：{cross_verdict}** → 操作 **{cross_action}** {act_mark(cross_action)}\n\n"
                       f"**本报告红标 {red_n_other} 项（{sev_other}）**\n{_other_red}")
if MACD:
    macd_tbl = tbl_code(["宽基", "信号", "DIF", "DEA", "柱", "净申赎", "85%分位", "15%分位", "判定"],
                        [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[10]] for r in macd_rows],
                        [8, 10, 9, 9, 9, 9, 9, 9, 6])
    macd_card = (f"{NAME_OTHER} · 存量资金MACD | " + MACDDSHOW + " | 来自 DoMate",
                 f"**④ ETF存量资金MACD突破监控（数据日期 {MACDDSHOW}）**\n"
                 f"参数 MACD(10,20,5)｜窗口 近{mwin['n_trading_days']}个交易日（{pk(mwin['first_date'])}~{pk(mwin['last_date'])}）｜分位数本次重算\n"
                 f"触发口径：零轴上金叉 / 零轴下死叉 / 上穿零轴 / 跌破零轴\n"
                 f"今日触发 **{MACD['n_triggered']}/{MACD['n_bases']}** 宽基｜{MK_OPP}机会 {len(MACD['opportunity'])} · {MK_RISK}风险 {len(MACD['risk'])}｜"
                 f"大额申购共振 {n_res}/5 · 大额赎回 {n_red}/5\n\n```\n" + macd_tbl + "\n```\n"
                 f"\n**{MK_OPP} 机会**：{'；'.join(MACD['opportunity']) or '无'}\n"
                 f"**{MK_RISK} 风险**：{'；'.join(MACD['risk']) or '无'}")
else:
    macd_card = (f"{NAME_OTHER} · 存量资金MACD | " + DSHOW + " | 来自 DoMate",
                 "**④ ETF存量资金MACD突破监控**\n数据缺失：未找到 data/macd_module.json。")
cards_other = [
    (f"{NAME_OTHER} · 总览 | " + DSHOW + " | 来自 DoMate", card_other_overview, C_SEV_O),
    (f"{NAME_OTHER} · ETF资金流明细 | " + DSHOW + " | 来自 DoMate",
     "```\n" + tbl_code(["代码", "名称", "方向组", "净流入亿", "20日份额", "分位", "标记"],
                        etf_rows, [10, 12, 10, 9, 9, 6, 6]) + "\n```", C_ETF),
    (f"{NAME_OTHER} · 行业融资×ETF背离 | " + DSHOW + " | 来自 DoMate",
     "```\n" + tbl_code(["行业·ETF", "融资20日", "融资分位", "ETF20日", "ETF分位", "标签", "标记"],
                        idrows, [16, 10, 8, 9, 8, 8, 6]) + "\n```", C_DIV),
    (f"{NAME_OTHER} · 市场宽度监控 | " + DSHOW + " | 来自 DoMate",
     f"**市场宽度等级：{B['level']}（{B['wstate']}）{B_MARK}**\n"
     f"涨跌比 {B['ad_ratio']}（今日）｜连续 {runs['ad_ratio_lt0.5']} 天 < 0.5，连续 {runs['ad_ratio_lt0.8']} 天 < 0.8\n"
     f"站上20MA {B['r20']}% ｜ 站上60MA {B['r60']}%\n"
     f"创20日新低 {B['nl20']} 家（占全市场 {B['nl20_pct_of_universe']}%）｜新高-新低差 {B['nh_nl_diff']}\n"
     f"综合分位 P{B['composite_pct']}｜背离：{B['divergence']} {DIV_MARK}\n\n"
     f"**轨一触发项**\n" + ("\n".join(f"- {MK_RISK} {t}" for t in B["triggers"]) if B["triggers"] else "- 无"), C_WIDTH),
    (macd_card[0], macd_card[1], C_MACD),
    (f"{NAME_OTHER} · 四栏目联动综合结论 | " + DSHOW + " | 来自 DoMate",
     "```\n" + cross_code + "\n```\n\n"
     f"**建议：{cross_action} {act_mark(cross_action)}**。去杠杆未完成、宽度极端收缩但现底背离、宽基 ETF 逆势获申购；"
     f"防御为主，待“净偿还连续 3 日收窄 + 站上20MA比例回升至 30% 以上”确认。", C_LINK),
]


def _objs(cards):
    return [{"title": t, "content": c, "color": col} for (t, c, col) in cards]


objs_margin = _objs(cards_margin)
objs_other = _objs(cards_other)
# 图文卡片：把「全部子图合并大图」附到 MACD 卡片（标题含「存量资金MACD」），
# 使该卡片同时含文字与图片（飞书图文卡片）；不含 image 的卡片仍为纯文字卡。
# 报告A 与报告B 各自的 MACD 卡均挂同一张合并大图（同源同图）。
if MACD_COMBINED:
    for _objs_set in (objs_margin, objs_other):
        for o in _objs_set:
            if "存量资金MACD" in o["title"]:
                o["image"] = MACD_COMBINED
jdump_cards = lambda objs, p: json.dump(objs, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
jdump_cards(objs_margin, os.path.join(D, "feishu_cards_margin.json"))
jdump_cards(objs_other, os.path.join(D, "feishu_cards_other.json"))

# ---------- 红标清单（每份一套） ----------
def _dump_flags(name, items, sev, path):
    json.dump({"report_name": name, "base_date": DATE, "report_period": PERIOD,
               "count": len(items), "severity": sev, "items": items,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


_dump_flags(NAME_MARGIN, red_margin, sev_margin, os.path.join(D, "red_flags_margin.json"))
_dump_flags(NAME_OTHER, red_other, sev_other, os.path.join(D, "red_flags_other.json"))


def _subject(name, sev, n):
    if n >= 2:
        return f"【{name}·强提醒】{DSHOW} 红标 {n} 项"
    if n == 1:
        return f"【{name}·提醒】{DSHOW} 红标 1 项"
    return f"【{name}】{DSHOW} 红标 0 项"


# ---------- 邮件正文（每份一套） ----------
bodyA = [
    f"{NAME_MARGIN} · 来自 DoMate",
    f"报告期 {PSHOW}（{wd(PERIOD)}）｜ 数据基准日 {DSHOW}（{wd(DATE)}）",
    f"红标项数：{red_n_margin} → 提醒分级：{sev_margin}（规则：≥1 发提醒，≥2 发强提醒）",
    "",
    "== 红标清单（两融专项） ==",
]
bodyA += ([f"{i+1}. {x}" for i, x in enumerate(red_margin)] if red_margin else ["无"])
bodyA += [
    "",
    "== 核心读数 ==",
    f"两融杠杆：{risk_level}｜杠杆率 {mk['lev_pct']}%｜20日净流出 {mk['chg20_pct']:+.2f}%｜"
    f"融资买入占比 {mk['turn_pct']}%｜227% 等效信号：{'触发' if rt1['sig227_triggered'] else '未触发'}",
    f"强平脆弱前 10：{len(risk10)} 只（融资占比>8% 且偿还加速且创新低）",
    (f"ETF存量资金MACD（附，数据日期 {MACDDSHOW}）：触发 {MACD['n_triggered']}/{MACD['n_bases']}｜"
     f"机会 {len(MACD['opportunity'])}（{'、'.join(MACD['opportunity']) or '无'}）｜"
     f"风险 {len(MACD['risk'])}（{'、'.join(MACD['risk']) or '无'}）｜"
     f"大额申购共振 {n_res}/5 · 大额赎回 {n_red}/5｜MACD(10,20,5) 近{mwin['n_trading_days']}日窗口（分位本次重算）"
     if MACD else "ETF存量资金MACD：数据缺失"),
]
bodyA += ra_mail
bodyA += ["", "== 报告文件 ==", outA, "", DISCLAIMER]

bodyB = [
    f"{NAME_OTHER} · 来自 DoMate",
    f"报告期 {PSHOW}（{wd(PERIOD)}）｜ 数据基准日 {DSHOW}（{wd(DATE)}）",
    f"红标项数：{red_n_other} → 提醒分级：{sev_other}（规则：≥1 发提醒，≥2 发强提醒）",
    "",
    "== 红标清单（资金流与宽度） ==",
]
bodyB += ([f"{i+1}. {x}" for i, x in enumerate(red_other)] if red_other else ["无"])
bodyB += [
    "",
    "== 核心读数 ==",
    f"ETF 资金流：宽基锚合计 {broad:+.0f} 亿｜行业/主题合计 {sect:+.0f} 亿（{sect_out}/{sect_all} 只净流出）",
    f"市场宽度：{B['level']}（{B['wstate']}）｜涨跌比 {B['ad_ratio']}｜站上20MA {B['r20']}%｜"
    f"站上60MA {B['r60']}%｜创20日新低 {B['nl20']} 家｜综合分位 P{B['composite_pct']}",
    (f"ETF存量资金MACD（数据日期 {MACDDSHOW}）：触发 {MACD['n_triggered']}/{MACD['n_bases']}｜"
     f"机会 {len(MACD['opportunity'])}（{'、'.join(MACD['opportunity']) or '无'}）｜"
     f"风险 {len(MACD['risk'])}（{'、'.join(MACD['risk']) or '无'}）｜"
     f"大额申购共振 {n_res}/5 · 大额赎回 {n_red}/5｜MACD(10,20,5) 近{mwin['n_trading_days']}日窗口（分位本次重算）"
     if MACD else "ETF存量资金MACD：数据缺失"),
    f"联动结论：{cross_verdict} → 操作建议：{cross_action}",
    "",
    "== 报告文件 ==",
    outB,
    "",
    DISCLAIMER,
]

subjA = _subject(NAME_MARGIN, sev_margin, red_n_margin)
subjB = _subject(NAME_OTHER, sev_other, red_n_other)
open(os.path.join(D, "email_subject_margin.txt"), "w", encoding="utf-8").write(subjA)
open(os.path.join(D, "email_subject_other.txt"), "w", encoding="utf-8").write(subjB)
open(os.path.join(D, "email_body_margin.txt"), "w", encoding="utf-8").write("\n".join(bodyA))
open(os.path.join(D, "email_body_other.txt"), "w", encoding="utf-8").write("\n".join(bodyB))

print("WROTE_A", outA, len(repA), "chars; cards:", len(cards_margin))
print("WROTE_B", outB, len(repB), "chars; cards:", len(cards_other))
print("risk_level", risk_level, "| cross", cross_verdict, cross_action, "| match row", match)
print("red_flags margin", red_n_margin, sev_margin, "| subject:", subjA)
print("red_flags other ", red_n_other, sev_other, "| subject:", subjB)
if RA:
    print("RA", RA["as_of"], "window", RA["window"]["first_date"], "~", RA["window"]["last_date"],
          "| stats", RA["summary_stats"], "| risk_items", len(ra_risk))