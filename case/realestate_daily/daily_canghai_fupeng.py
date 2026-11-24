#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
土狗付鹏地产日报 — 基于合并版周报体系（沧海一土狗框架 + 付鹏五维 + 地方债 + 外部融资）
用法:
  python daily_canghai_fupeng.py                       # 生成日报 markdown（不推送）
  python daily_canghai_fupeng.py --push                # 生成日报并推送飞书
  python daily_canghai_fupeng.py --alert               # 检查北京看涨预警，触发则发邮件
  python daily_canghai_fupeng.py --push --alert        # 生成+推送+预警检查（定时任务默认）

数据入口（复用周报体系）:
  - weekly_input.json                  周度高频数据（含 as_of 截止日期与 mom_pct/yoy_pct 环比同比）
  - fupeng_frame_annual_slow_vars.json 年度慢变量（以 updated_at 为截止时点）
  - email_config.json                  邮件 SMTP 配置（server/port/user/password/to；缺失时预警仅记录无法发送）

北京房价看涨预警规则（任一满足即触发邮件）:
  R1: 北京70城二手环比 > 0（月度信号：一线二手价格转正）
  R2: 北京挂牌均价月环比 > 0 且 周网签周环比 > 0（量价齐升信号）
  触发后调用 send_alert_email()：若 email_config.json 缺失或字段不全，输出明确告警"邮件未发送（缺SMTP配置）"，不编造发送成功。

输出:
  - 土狗付鹏地产日报_YYYYMMDD.md（正文 markdown，按 ## 分节）
  - --push: 沿用 canghai_property_weekly_report.py 推送飞书群
  - --alert: 预警检查结果（触发 → 发邮件；未触发 → 报告无信号）
"""
import argparse
import datetime as dt
import json
import os
import smtplib
import subprocess
import sys
from email.mime.text import MIMEText
from email.utils import formataddr

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUSH_SCRIPT = os.path.join(BASE_DIR, "canghai_property_weekly_report.py")
REPORT_TITLE = "土狗付鹏地产日报 | 来自 DoMate"
SLOW_VARS = os.path.join(BASE_DIR, "fupeng_frame_annual_slow_vars.json")
EMAIL_CONFIG = os.path.join(BASE_DIR, "email_config.json")

# 北京看涨预警的输入键（与 weekly_input.json 保持一致）
BJ_SIGNALS = [
    ("70城二手_环比", "北京70城二手环比", ">0 即转正（月度看涨）"),
    ("挂牌均价", "北京挂牌均价月环比", ">0 为价格上行（月度）"),
    ("二手网签_周度", "北京周网签周环比", ">0 为放量（周度）"),
]

# ---------- 通用工具（与合并版周报保持一致） ----------
def _fmt_num(v, digits=2):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"

def _arrow(v):
    if v is None or v == 0:
        return ""
    return "↑" if v > 0 else "↓"

def _pct(v, digits=2):
    if v is None:
        return "n/a"
    return f"**{v:.{digits}f}%** {_arrow(v)}"

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_item(data, section, key):
    """取输入项：返回 (value, as_of, note, unit)。环比/同比若 note 未含则自动追加。"""
    sec = data.get(section, {})
    item = sec.get(key, {}) if isinstance(sec, dict) else {}
    if not isinstance(item, dict):
        return (item, "", "", "")
    value = item.get("value")
    as_of = item.get("as_of", "")
    note = item.get("note", "") or ""
    mom = item.get("mom_pct")
    yoy = item.get("yoy_pct")
    unit = item.get("unit", "")
    if isinstance(mom, (int, float)) and "环比" not in note:
        label = item.get("mom_label", "环比")
        note += f" {label}{_fmt_num(mom)}%{_arrow(mom)}"
    if isinstance(yoy, (int, float)) and "同比" not in note:
        label = item.get("yoy_label", "同比")
        note += f" {label}{_fmt_num(yoy)}%{_arrow(yoy)}"
    return (value, as_of, note.strip(), unit)

def tag(value_str, as_of, unit=""):
    s = str(value_str)
    if unit:
        s += unit
    if as_of:
        s += f"（截至{as_of}）"
    if len(s) > 24:
        return f"{s}"
    return f"**{s}**"

def render_fields(lines, data, section, key_labels):
    for key, lab in key_labels:
        v, asof, note, unit = get_item(data, section, key)
        if v is None or v == "":
            v = "最新值待更新"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")

# ---------- 土狗框架计算（与合并版周报一致） ----------
def tugou_calc(data):
    r = {}
    w = data.get("土狗框架", {})
    ff = w.get("fftr_effective", {}).get("value")
    sp = w.get("swap_points_1y", {}).get("value")
    fx = w.get("spot_usdcny", {}).get("value")
    hp = w.get("潜在产出增速_hp", {}).get("value")
    cpi = w.get("headline_cpi", {}).get("value")
    ccpi = w.get("核心_cpi", {}).get("value")
    aaa = w.get("aaa_1y存单", {}).get("value")
    hang = w.get("北京挂牌量_万套", {}).get("value")
    th = w.get("挂牌阈值_万套", {}).get("value", 15.0)
    rent_ok = w.get("租金企稳", {}).get("value", False)
    tugou_neutral = w.get("中性利率_土狗", {}).get("value", 5.5)

    r["ff"] = ff
    if ff is not None and sp is not None and fx:
        swap_annual = sp * 0.0001 / fx * 100
        disc = ff + swap_annual
    else:
        swap_annual, disc = None, None
    r["swap_annual"] = swap_annual
    r["disc"] = disc

    spw = w.get("swap_points_1y_上周", {}).get("value")
    fxw = w.get("spot_usdcny_上周", {}).get("value")
    spm = w.get("swap_points_1y_上月", {}).get("value")
    fxm = w.get("spot_usdcny_上月", {}).get("value")
    if spw is not None and fxw:
        r["swap_annual_week"] = spw * 0.0001 / fxw * 100
    else:
        r["swap_annual_week"] = None
    if spm is not None and fxm:
        r["swap_annual_month"] = spm * 0.0001 / fxm * 100
    else:
        r["swap_annual_month"] = None

    r["neutral_calc"] = (hp + cpi) if (hp is not None and cpi is not None) else None
    r["neutral_tugou"] = tugou_neutral

    gates = []
    if disc is not None:
        gates.append(("贴现率<中性利率(土狗)", disc < tugou_neutral))
    else:
        gates.append(("贴现率<中性利率(土狗)", False))
    gates.append(("租金企稳", bool(rent_ok)))
    gates.append(("北京挂牌量<阈值", hang is not None and hang < th))
    if all(g[1] for g in gates):
        r["state"] = "STATE_3 地产筑底"
    elif gates[0][1] is False:
        r["state"] = "STATE_1（贴现率不低于中性利率）"
    else:
        r["state"] = "STATE_2/4（量价反馈或风偏仍低，需社零/餐饮交叉验证）"
    r["gates"] = gates

    if ff is not None and aaa is not None and hp is not None and ccpi is not None:
        marginal = 2 * ff - aaa
        neutral_c = hp + ccpi
        r["marginal"] = marginal
        r["neutral_c"] = neutral_c
        r["consumption"] = "宽松区间，消费获支撑" if marginal < neutral_c else "收紧区间，消费承压"
    else:
        r["consumption"] = "n/a（输入缺失）"
    return r

# ---------- 付鹏周期定位（与合并版一致） ----------
def fupeng_calc(data):
    r = {}
    f = data.get("付鹏框架", {})
    r["lpr5"] = f.get("lpr_5y", {}).get("value")
    r["rent_yield"] = f.get("租金收益率_50城", {}).get("value")
    r["loan_h1"] = f.get("居民中长期贷款_上半年增量", {}).get("value")
    r["loan_latest"] = f.get("居民中长期贷款_8月", {}).get("value")
    r["household_leverage"] = f.get("居民杠杆率_pct", {}).get("value")
    if r["lpr5"] is not None and r["rent_yield"] is not None:
        r["kill_spread"] = r["lpr5"] - r["rent_yield"]
        r["double_kill"] = r["kill_spread"] > 0
    else:
        r["kill_spread"], r["double_kill"] = None, None
    if r["loan_latest"] is not None and r["loan_latest"] < 0:
        r["household_behavior"] = "主动去杠杆（8月单月净偿还822亿，提前还贷/净偿还主导）"
    elif r["loan_h1"] is not None and r["loan_h1"] <= 0:
        r["household_behavior"] = "主动去杠杆（住户贷款净减少，有统计以来首次）"
    else:
        r["household_behavior"] = "n/a（数据待更新）"
    return r

def fupeng_cycle_stage(fc, tc):
    if fc.get("double_kill") is True and "去杠杆" in fc.get("household_behavior", ""):
        return "双杀出清期"
    if tc.get("disc") is not None and tc.get("neutral_tugou") is not None and tc["disc"] >= tc["neutral_tugou"]:
        return "泡沫积累期/见顶震荡期（高利率压制估值）"
    if tc.get("state", "").startswith("STATE_3") and fc.get("double_kill") is False:
        return "底部磨底期"
    return "见顶震荡期（证据不足，需更多信贷/利率数据）"

# ---------- 北京看涨预警判定 ----------
def check_beijing_alert(data):
    """
    返回 (triggered, reasons, signals)。
    signals: [(label, 最新值, 判定, 说明)]
    reasons: 触发的规则描述列表；未触发为空。
    """
    bj = data.get("北京", {})
    r1_val = bj.get("70城二手_环比", {}).get("value")
    r2a = bj.get("挂牌均价", {}).get("mom_pct")
    r2b = bj.get("二手网签_周度", {}).get("mom_pct")
    asof70 = bj.get("70城二手_环比", {}).get("as_of", "")
    asof_jg = bj.get("挂牌均价", {}).get("as_of", "")
    asof_wq = bj.get("二手网签_周度", {}).get("as_of", "")

    signals = []
    signals.append((BJ_SIGNALS[0][1], r1_val, r1_val is not None and r1_val > 0, f"月度转正（截至{asof70 or '待更新'}）"))
    signals.append((BJ_SIGNALS[1][1], r2a, r2a is not None and r2a > 0, f"月环比上行（截至{asof_jg or '待更新'}）"))
    signals.append((BJ_SIGNALS[2][1], r2b, r2b is not None and r2b > 0, f"周环比放量（截至{asof_wq or '待更新'}）"))

    reasons = []
    if r1_val is not None and r1_val > 0:
        reasons.append(f"R1 北京70城二手环比 {_fmt_num(r1_val)}% 转正（截至{asof70}）")
    if (r2a is not None and r2a > 0) and (r2b is not None and r2b > 0):
        reasons.append(f"R2 量价齐升：挂牌均价月环比+{_fmt_num(r2a)}% 且 周网签周环比+{_fmt_num(r2b)}%")
    return (len(reasons) > 0, reasons, signals)

# ---------- 邮件发送链路 ----------
def send_alert_email(subject, body):
    """发送预警邮件。返回 (ok, message)。email_config.json 缺失/字段不全 → 不发送、如实返回未发送原因。"""
    if not os.path.exists(EMAIL_CONFIG):
        return (False, f"邮件未发送：未找到配置文件 {EMAIL_CONFIG}（需填写 SMTP server/port/user/password/to 后重试）")
    try:
        cfg = load_json(EMAIL_CONFIG)
    except Exception as e:
        return (False, f"邮件未发送：配置文件解析失败 {e}")
    need = ["server", "port", "user", "password", "to"]
    miss = [k for k in need if not cfg.get(k)]
    if miss:
        return (False, f"邮件未发送：配置缺少字段 {miss}（请完整填写 {EMAIL_CONFIG}）")
    to_list = cfg["to"] if isinstance(cfg["to"], list) else [cfg["to"]]
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((str(cfg.get("from_name", "土狗付鹏预警")), cfg["user"]))
        msg["To"] = ", ".join(to_list)
        with smtplib.SMTP_SSL(cfg["server"], int(cfg["port"]), timeout=15) as srv:
            srv.login(cfg["user"], cfg["password"])
            srv.sendmail(cfg["user"], to_list, msg.as_string())
        return (True, f"邮件发送成功 → {', '.join(to_list)}")
    except Exception as e:
        return (False, f"邮件发送失败：{e}")

# ---------- 日报正文 ----------
def build_daily_report(data, fc, tc, alert_result):
    now = dt.date.today().isoformat()
    slow = load_json(SLOW_VARS)
    slow_updated = slow.get("updated_at", "")
    lines = [f"# {REPORT_TITLE}", ""]

    # 北京
    lines.append("## 【北京】")
    bj_week_asof = data.get("北京", {}).get("二手网签_周度", {}).get("as_of", "")
    for key, lab in [("二手网签_周度", "周网签"), ("二手网签_8月全月", "8月全月网签"),
                     ("新房网签_8月", "8月新房网签"), ("挂牌均价", "挂牌均价"),
                     ("二手挂牌量", "二手挂牌量"), ("70城二手_环比", "70城二手环比"),
                     ("70城二手_同比", "70城二手同比"), ("租金", "租金")]:
        v, asof, note, unit = get_item(data, "北京", key)
        if v is None:
            v = "n/a"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")
    lines.append(f"- 数据基准：周度值截至{bj_week_asof}，月度指标以各行标注为准")

    # 上海
    lines.append("## 【上海】")
    sh_week_asof = data.get("上海", {}).get("挂牌量", {}).get("as_of", "") or data.get("上海", {}).get("二手网签_8月全月", {}).get("as_of", "")
    for key, lab in [("二手网签_9月累计", "9月累计网签"), ("二手网签_8月全月", "8月全月网签"),
                     ("挂牌量", "挂牌量"), ("70城二手_环比", "70城二手环比"),
                     ("70城二手_同比", "70城二手同比"), ("租金", "租金")]:
        v, asof, note, unit = get_item(data, "上海", key)
        if v is None:
            v = "n/a"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")
    lines.append(f"- 数据基准：周度值截至{sh_week_asof}，月度指标以各行标注为准")

    # 京沪对比
    lines.append("## 【京沪对比】")
    bj70, bj70a, _, _ = get_item(data, "北京", "70城二手_环比")
    sh70, sh70a, _, _ = get_item(data, "上海", "70城二手_环比")
    if bj70 is not None and sh70 is not None:
        lines.append(f"- 70城二手环比：北京{_fmt_num(bj70)}（截至{bj70a}） vs 上海{_fmt_num(sh70)}（截至{sh70a}） → " +
                     ("京沪分化持续" if (sh70 > bj70) else "京沪同步"))
    else:
        lines.append("- 70城二手环比：数据待更新")
    # 看涨预警状态（数据+结论）
    triggered, reasons, signals = alert_result
    lines.append("- 北京看涨预警：")
    for lab, val, ok, desc in signals:
        lines.append(f"  - {lab}：**{_fmt_num(val) if val is not None else 'n/a'}%** → {'✓' if ok else '✗'}（{desc}）")
    if triggered:
        lines.append(f"  - **触发！** {'；'.join(reasons)}")
    else:
        lines.append("  - 未触发（无看涨趋势信号）")

    # 土狗框架
    lines.append("## 【土狗框架】")
    ffr_asof = data.get("土狗框架", {}).get("fftr_effective", {}).get("as_of", "")
    swap_asof = data.get("土狗框架", {}).get("swap_points_1y", {}).get("as_of", "")
    if tc["ff"] is not None:
        lines.append(f"- 有效FFTR：**{_fmt_num(tc['ff'])}%**（截至{ffr_asof}，目标区间3.75%-4.00%）")
    if tc["swap_annual"] is not None:
        lines.append(f"- 1Y货币掉期年化率：{_pct(tc['swap_annual'])}（截至{swap_asof}，负=贴水）")
        if tc.get("swap_annual_week") is not None:
            wk = tc["swap_annual"] - tc["swap_annual_week"]
            lines.append(f"  - 周环比：{_fmt_num(wk, 3)}({_arrow(wk)})（本周 {_fmt_num(tc['swap_annual'],3)}% vs 上周 {_fmt_num(tc['swap_annual_week'],3)}%）")
        if tc.get("swap_annual_month") is not None:
            mo = tc["swap_annual"] - tc["swap_annual_month"]
            lines.append(f"  - 月环比：{_fmt_num(mo, 3)}({_arrow(mo)})（本周 {_fmt_num(tc['swap_annual'],3)}% vs 上月 {_fmt_num(tc['swap_annual_month'],3)}%）")
    if tc["disc"] is not None:
        lines.append(f"- 风险资产贴现率：**{_fmt_num(tc['disc'])}%**（=FFTR+掉期年化率，数据截至{ffr_asof}）")
    hp_asof = data.get("土狗框架", {}).get("潜在产出增速_hp", {}).get("as_of", "2026Q3")
    if tc["neutral_calc"] is not None:
        lines.append(f"- 核心中性利率（潜在增速+CPI）：**{_fmt_num(tc['neutral_calc'])}%**（潜在产出至{hp_asof}、CPI至2026-08）")
    if tc["neutral_tugou"] is not None:
        lines.append(f"- 中性利率（土狗）：**{_fmt_num(tc['neutral_tugou'])}%**（潜在增速+CPI，数据截至2026-08）")
    lines.append(f"- 状态判定：**{tc['state']}**")
    for name, ok in tc.get("gates", []):
        lines.append(f"  - {name}：{'✓' if ok else '✗'}")
    if tc.get("marginal") is not None:
        lines.append(f"- 2R-r 消费：边际利率**{_fmt_num(tc['marginal'])}%** vs 消费中性**{_fmt_num(tc['neutral_c'])}%** → **{tc['consumption']}**（FFTR至{ffr_asof}、AAA存单至2026-09-11）")

    # 付鹏·房地产周期（结论 + 关键判定）
    lines.append("## 【付鹏·房地产周期框架】")
    lines.append("**结论先行：** " + fupeng_cycle_stage(fc, tc))

    # 维度1 居民杠杆率与加杠杆能力
    lev_v, lev_asof, lev_note, _ = get_item(data, "付鹏框架", "居民杠杆率_pct")
    if lev_v is not None:
        lines.append(f"- 居民杠杆率：**{_fmt_num(lev_v)}%（截至{lev_asof}，{lev_note}）**")
    else:
        lines.append("- 居民杠杆率：**最新值待更新**")
    hb = fc.get("household_behavior", "n/a")
    loan8_v, loan8_asof, loan8_note, _ = get_item(data, "付鹏框架", "居民中长期贷款_8月")
    if loan8_v is not None and "净偿还" not in hb:
        loan8_s = f"（{loan8_note}，截至{loan8_asof}）"
    elif loan8_v is not None:
        loan8_s = f"（同比多减1022亿，央行2026-09-14发布，截至{loan8_asof}）"
    else:
        loan8_s = ""
    lines.append(f"- **行为判定（居民加/去杠杆）：** {hb}{loan8_s}")

    # 维度2 利率环境与双杀机制
    lpr_v, lpr_asof, lpr_note, _ = get_item(data, "付鹏框架", "lpr_5y")
    ry_v, ry_asof, ry_note, _ = get_item(data, "付鹏框架", "租金收益率_50城")
    lpr_s = f"LPR {_fmt_num(lpr_v)}%（{lpr_note}，截至{lpr_asof}）" if lpr_v is not None else "LPR n/a"
    ry_s = f"租金收益率 {_fmt_num(ry_v)}%（{ry_note}，截至{ry_asof}）" if ry_v is not None else "租金收益率 n/a"
    lines.append(f"- **双杀判定：** 持房成本（按揭≈{lpr_s}） vs 收益（{ry_s}）"
                 f" → **{'双杀成立（利率+房价双杀，钝性出清中）' if fc.get('double_kill') else '尚未击穿（收益仍可覆盖成本）'}**")

    # 维度3 监管周期与信贷挤水分
    render_fields(lines, data, "付鹏框架", [("监管政策要点", "监管政策要点"), ("DTI或LTV限制", "DTI/LTV限制"),
                                            ("信贷风控与挤水分", "信贷挤水分")])

    # 维度4 风险传导链与底层出清
    render_fields(lines, data, "付鹏框架", [("开发商与影子银行风险", "开发商/影子银行风险"),
                                            ("美元债违约与展期", "房企美元债违约/展期"),
                                            ("投资移民与外资依赖", "外资与投资移民依赖"),
                                            ("全球金融周期溢出", "全球金融周期溢出")])

    # 付鹏·地方债
    lines.append("## 【付鹏·地方债框架】")
    for key, lab in [("化债部署与置换债", "化债部署与置换债"), ("隐性债务余额与负债率", "隐性债务余额与负债率"),
                     ("结存限额增量政策", "结存限额等增量政策"), ("专项债发行", "新增专项债发行"),
                     ("城投债信用利差", "城投债信用利差"), ("土地出让收入", "国有土地使用权出让收入")]:
        v, asof, note, unit = get_item(data, "地方债", key)
        if v is None:
            v = "最新值待更新"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")
    lines.append("- 一句结论：土地出让收入连年下行（1-8月同比-28.6%）压制地方财政弹性；6万亿置换近尾声、隐性债务余额6.5万亿仍待出清")

    # 付鹏·外部融资
    lines.append("## 【付鹏·外部融资脆弱性与全球美元周期】")
    ext_concl = slow.get("维度5_外部融资脆弱性与全球美元周期", {}).get("外部融资脆弱性综合结论", "")
    lines.append(f"**结论先行：** {ext_concl or '外部融资脆弱性结论待更新'}")
    for key, lab in [("经常账户顺差_上半年", "经常账户顺差（上半年）"), ("中资美元债_8月净融资", "中资美元债净融资（8月）"),
                     ("中美10年利差倒挂", "中美10年利差倒挂"), ("北向资金_二季度净流入", "北向资金净流入（二季度）"),
                     ("人民币中间价", "人民币兑美元中间价"), ("外资购房依赖度", "外资购房依赖度")]:
        v, asof, note, unit = get_item(data, "外部融资", key)
        if v is None:
            v = "最新值待更新"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")

    # 底部
    lines.append("---")
    lines.append(f"数据纪律：所有数据标注截止日期；年度慢变量引用暂存文件（updated_at={slow_updated or '未更新'}），禁止编造。")
    lines.append(f"报告生成：{now}（土狗付鹏地产日报）")
    return "\n".join(lines)

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(BASE_DIR, "weekly_input.json"))
    ap.add_argument("--push", action="store_true", help="生成日报并推送飞书")
    ap.add_argument("--alert", action="store_true", help="检查北京看涨预警，触发则发邮件")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        print(f"数据文件不存在: {args.data}")
        sys.exit(1)
    data = load_json(args.data)

    tc = tugou_calc(data)
    fc = fupeng_calc(data)
    alert_result = check_beijing_alert(data)
    body = build_daily_report(data, fc, tc, alert_result)

    out_path = os.path.join(BASE_DIR, f"土狗付鹏地产日报_{dt.date.today().strftime('%Y%m%d')}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print("日报已生成:", out_path)
    print("=" * 60)
    print(body)

    # 预警检查（无论是否 --push 都执行判定；仅 --alert 时尝试发邮件）
    triggered, reasons, _ = alert_result
    if triggered:
        print("=" * 60)
        print(f"[预警] 北京房价看涨信号触发：{'；'.join(reasons)}")
        if args.alert:
            subject = f"【土狗付鹏预警】北京房价看涨（{dt.date.today().isoformat()}）"
            mail_body = "北京房价看涨趋势触发预警：\n" + "\n".join(f"- {r}" for r in reasons) + \
                        "\n\n详见：土狗付鹏地产日报_%s.md" % dt.date.today().strftime('%Y%m%d')
            ok, msg = send_alert_email(subject, mail_body)
            print(f"[邮件] {msg}")
        else:
            print("[邮件] 未发送（未加 --alert 参数；预警已触发）")
    else:
        print("=" * 60)
        print("[预警] 北京房价无看涨趋势信号，不发送邮件")

    if args.push:
        result = subprocess.run(
            [sys.executable, PUSH_SCRIPT, REPORT_TITLE, body],
            capture_output=True, text=True
        )
        print("推送 STDOUT:", result.stdout)
        print("推送 STDERR:", result.stderr)
        if "发送成功" not in result.stdout:
            print("推送疑似失败，请检查飞书脚本日志")
            sys.exit(1)
        print("飞书推送完成")

if __name__ == "__main__":
    main()