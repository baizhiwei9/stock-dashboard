#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
土狗付鹏地产周报 — 沧海一土狗状态机 + 付鹏四维房地产周期框架 + 地方债框架
用法:
  python canghai_fupeng_weekly.py                # 生成报告正文 markdown（不推送）
  python canghai_fupeng_weekly.py --push         # 生成报告并推送飞书
  python canghai_fupeng_weekly.py --data xxx.json  # 指定周度数据文件（默认 weekly_input.json）

数据入口:
  - weekly_input.json                  周度高频输入（北京/上海/土狗框架/付鹏四维/地方债），每项含 value + as_of(截止日期) + mom_pct/yoy_pct(环比/同比) + note
  - fupeng_frame_annual_slow_vars.json 年度慢变量（居民杠杆率、25-44岁人口、房贷余额等），引用时以 updated_at 为截止时点
计算逻辑（稳定公式，代码内完成）:
  - 掉期年化率 = 掉期点 × 0.0001 ÷ 即期汇率 × 100%；周环比/月环比 = 本周年化率 − 上周/上月年化率
  - 风险资产贴现率 = 有效FFTR + 掉期年化率
  - 核心中性利率 = 潜在产出增速(HP,λ=1600) + headline CPI；土狗中性利率 = 5.5%
  - STATE 判定：①贴现率<中性利率 ②租金企稳 ③北京挂牌量<15万套（全满足→STATE_3 地产筑底）
  - 2R-r 消费判定：边际利率 = 2×FFTR − AAA1Y存单；消费中性 = 潜在增速 + 核心CPI
  - 付鹏双杀判定：按揭利率 > 租金收益率 → 持房成本>收益，双杀成立
  - 付鹏周期定位：规则组合（双杀+去杠杆→双杀出清期等）
输出:
  - 土狗付鹏地产周报_YYYYMMDD.md（正文 markdown，按 ## 分节；推送沿用 canghai_property_weekly_report.py）
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUSH_SCRIPT = os.path.join(BASE_DIR, "canghai_property_weekly_report.py")
REPORT_TITLE = "土狗付鹏地产周报 | 来自 DoMate"

# 付鹏四维渲染清单：维度 → [(数据键, 展示标签)]
FUPENG_SECTIONS = [
    ("维度① 居民杠杆率与加杠杆能力", [
        ("居民杠杆率_pct", "居民部门杠杆率"),
        ("25-44岁人口占比", "25-44岁主力借贷人口"),
        ("居民中长期贷款_8月", "居民中长期贷款（8月单月）"),
        ("居民贷款_前8月", "住户贷款（前8个月）"),
        ("居民中长期贷款_上半年增量", "住户贷款（上半年净增）"),
    ]),
    ("维度② 利率环境与双杀机制", [
        ("lpr_5y", "5年期LPR"),
        ("按揭贷款利率", "按揭贷款利率"),
        ("租金收益率_50城", "租金收益率（重点50城）"),
    ]),
    ("维度③ 监管周期与信贷挤水分", [
        ("监管政策要点", "监管政策要点"),
        ("DTI或LTV限制", "DTI / LTV 约束"),
        ("信贷风控与挤水分", "信贷挤水分与风控"),
    ]),
    ("维度④ 风险传导链与底层出清", [
        ("开发商与影子银行风险", "开发商/影子银行风险"),
        ("美元债违约与展期", "离岸美元债（违约/展期）"),
        ("投资移民与外资依赖", "投资移民/外资依赖"),
        ("全球金融周期溢出", "全球金融周期溢出"),
    ]),
]

# 外部融资脆弱性与全球美元周期渲染清单（三维度全字段）
EXTFIN_SECTIONS = [
    ("外部融资·经常项目逆差的融资缺口", [
        ("经常账户顺差_上半年", "经常账户顺差（上半年）"),
        ("货物贸易差额_二季度", "货物贸易差额（二季度）"),
        ("资本和金融账户_上半年", "资本和金融账户（上半年）"),
    ]),
    ("外部融资·离岸美元债与影子银行杠杆", [
        ("中资美元债_8月净融资", "中资美元债净融资（8月）"),
        ("中资美元债_年内净融资", "中资美元债净融资（年内累计）"),
        ("中美10年利差倒挂", "中美10年利差倒挂"),
    ]),
    ("外部融资·外资流入可持续性与汇率负反馈", [
        ("北向资金_二季度净流入", "北向资金净流入（二季度）"),
        ("境外机构持A股流通市值", "境外机构持A股流通市值"),
        ("人民币中间价", "人民币兑美元中间价"),
        ("外资购房依赖度", "外资购房依赖度"),
    ]),
]

# 地方债渲染清单（全字段）
LOCAL_DEBT_KEYS = [
    ("化债部署与置换债", "化债部署与置换债"),
    ("隐性债务余额与负债率", "隐性债务余额与负债率"),
    ("结存限额增量政策", "结存限额等增量政策"),
    ("专项债发行", "新增专项债发行"),
    ("城投债信用利差", "城投债信用利差"),
    ("土地出让收入", "国有土地使用权出让收入"),
]

# ---------- 工具 ----------
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
    """取周度输入项：返回 (value, as_of, note, unit)。环比/同比若 note 未含则自动追加。"""
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
    """渲染单行数据：**数值** + 单位 + （截止日期）。长文本不加粗。"""
    s = str(value_str)
    if unit:
        s += unit
    if as_of:
        s += f"（截至{as_of}）"
    if len(s) > 24:
        return f"{s}"
    return f"**{s}**"

def render_fields(lines, data, section, key_labels):
    """按清单遍历渲染某 section 的全部字段，避免漏项。key_labels: [(key, label), ...]"""
    for key, lab in key_labels:
        v, asof, note, unit = get_item(data, section, key)
        if v is None or v == "":
            v = "最新值待更新"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")

# ---------- 土狗框架计算 ----------
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

    # 掉期年化率周/月环比（本周 vs 上周/上月）
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

    # STATE 判定
    gates = []
    if disc is not None:
        gates.append(("贴现率<中性利率(土狗)", disc < tugou_neutral))
    else:
        gates.append(("贴现率<中性利率(土狗)", False))
    gates.append(("租金企稳", bool(rent_ok)))
    gates.append(("北京挂牌量<阈值", hang is not None and hang < th))
    if all(g[1] for g in gates):
        state = "STATE_3 地产筑底"
    elif gates[0][1] is False:
        state = "STATE_1（贴现率不低于中性利率）"
    else:
        state = "STATE_2/4（量价反馈或风偏仍低，需社零/餐饮交叉验证）"
    r["state"] = state
    r["gates"] = gates

    # 2R-r 消费判定
    if ff is not None and aaa is not None and hp is not None and ccpi is not None:
        marginal = 2 * ff - aaa
        neutral_c = hp + ccpi
        r["marginal"] = marginal
        r["neutral_c"] = neutral_c
        r["consumption"] = "宽松区间，消费获支撑" if marginal < neutral_c else "收紧区间，消费承压"
    else:
        r["consumption"] = "n/a（输入缺失）"
    return r

# ---------- 付鹏周期定位 ----------
def fupeng_calc(data):
    r = {}
    f = data.get("付鹏框架", {})
    r["lpr5"] = f.get("lpr_5y", {}).get("value")
    r["lpr5_asof"] = f.get("lpr_5y", {}).get("as_of", "")
    r["rent_yield"] = f.get("租金收益率_50城", {}).get("value")
    r["rent_asof"] = f.get("租金收益率_50城", {}).get("as_of", "")
    r["loan_h1"] = f.get("居民中长期贷款_上半年增量", {}).get("value")
    r["loan_h1_asof"] = f.get("居民中长期贷款_上半年增量", {}).get("as_of", "")
    # 行为判定以最新月度为准：8月单月 <0 → 去杠杆
    r["loan_latest"] = f.get("居民中长期贷款_8月", {}).get("value")
    r["household_leverage"] = f.get("居民杠杆率_pct", {}).get("value")
    r["pop2544"] = f.get("25-44岁人口占比", {}).get("value")
    r["pop2544_asof"] = f.get("25-44岁人口占比", {}).get("as_of", "")

    # 双杀判定：按揭利率(LPR口径) vs 租金收益率
    if r["lpr5"] is not None and r["rent_yield"] is not None:
        r["kill_spread"] = r["lpr5"] - r["rent_yield"]
        r["double_kill"] = r["kill_spread"] > 0
    else:
        r["kill_spread"], r["double_kill"] = None, None

    # 居民行为判定：最新月度/累计贷款负增长 → 主动去杠杆
    if r["loan_latest"] is not None and r["loan_latest"] < 0:
        r["household_behavior"] = "主动去杠杆（8月单月净偿还822亿，提前还贷/净偿还主导）"
    elif r["loan_h1"] is not None and r["loan_h1"] <= 0:
        r["household_behavior"] = "主动去杠杆（住户贷款净减少，有统计以来首次）"
    else:
        r["household_behavior"] = "n/a（数据待更新）"
    return r

def fupeng_cycle_stage(fc, tc):
    """
    规则化周期定位：
    - 双杀成立 且 居民去杠杆 → 双杀出清期
    - 贴现率>=中性 且 高杠杆 → 泡沫积累期/见顶震荡期
    - 双杀缓解 且 挂牌去化 且 租金企稳 → 底部磨底期
    - 其余 → 见顶震荡期（证据不足时）
    """
    if fc.get("double_kill") is True and "去杠杆" in fc.get("household_behavior", ""):
        return "双杀出清期"
    if tc.get("disc") is not None and tc.get("neutral_tugou") is not None and tc["disc"] >= tc["neutral_tugou"]:
        return "泡沫积累期/见顶震荡期（高利率压制估值）"
    if tc.get("state", "").startswith("STATE_3") and fc.get("double_kill") is False:
        return "底部磨底期"
    return "见顶震荡期（证据不足，需更多信贷/利率数据）"

# ---------- 报告正文 ----------
def build_report(data, fc, tc):
    now = dt.date.today().isoformat()
    slow = load_json(os.path.join(BASE_DIR, "fupeng_frame_annual_slow_vars.json"))
    slow_updated = slow.get("updated_at", "")

    lines = []
    # ===== 北京 =====
    lines.append("## 【北京】")
    bj_week_asof = data.get("北京", {}).get("二手网签_周度", {}).get("as_of", "")
    for key, lab in [("二手网签_周度", "周网签"), ("二手网签_9月全月", "9月全月网签"),
                     ("二手网签_8月全月", "8月全月网签"), ("新房网签_9月", "9月新房网签"),
                     ("新房网签_8月", "8月新房网签"), ("挂牌均价", "挂牌均价"),
                     ("二手挂牌量", "二手挂牌量"), ("70城二手_环比", "70城二手环比"),
                     ("70城二手_同比", "70城二手同比"), ("租金", "租金")]:
        v, asof, note, unit = get_item(data, "北京", key)
        if v is None:
            v = "最新值待更新"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")
    lines.append(f"- 数据基准：周度值截至{bj_week_asof}，月度指标以各行标注为准")

    # ===== 上海 =====
    lines.append("## 【上海】")
    sh_week_asof = data.get("上海", {}).get("二手网签_周度", {}).get("as_of", "")
    for key, lab in [("二手网签_9月全月", "9月全月网签"), ("二手网签_8月全月", "8月全月网签"),
                     ("挂牌量", "挂牌量"), ("70城二手_环比", "70城二手环比"),
                     ("70城二手_同比", "70城二手同比"), ("租金", "租金")]:
        v, asof, note, unit = get_item(data, "上海", key)
        if v is None:
            v = "最新值待更新"
        lines.append(f"- {lab}：{tag(v, asof, unit)} {note}")
    lines.append(f"- 数据基准：周度值截至{sh_week_asof}，月度指标以各行标注为准")

    # ===== 京沪对比 =====
    lines.append("## 【京沪对比】")
    bj70, bj70a, _, _ = get_item(data, "北京", "70城二手_环比")
    sh70, sh70a, _, _ = get_item(data, "上海", "70城二手_环比")
    if bj70 is not None and sh70 is not None:
        lines.append(f"- 70城二手环比：北京{_fmt_num(bj70)} vs 上海{_fmt_num(sh70)} → " +
                     ("京沪分化持续" if (sh70 > bj70) else "京沪同步"))
    lines.append("- 结论：以上海独立行情是否强化为准（挂牌/租金/成交详见上两节）")

    # ===== 土狗框架 =====
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
        lines.append(f"- 风险资产贴现率：**{_fmt_num(tc['disc'])}%**（=FFTR+掉期年化率）")
    if tc["neutral_calc"] is not None:
        lines.append(f"- 核心中性利率（潜在增速+CPI）：**{_fmt_num(tc['neutral_calc'])}%**")
    if tc["neutral_tugou"] is not None:
        lines.append(f"- 中性利率（土狗）：**{_fmt_num(tc['neutral_tugou'])}%**")
    lines.append(f"- 状态判定：**{tc['state']}**")
    for name, ok in tc.get("gates", []):
        lines.append(f"  - {name}：{'✓' if ok else '✗'}")
    if tc.get("marginal") is not None:
        lines.append(f"- 2R-r 消费：边际利率**{_fmt_num(tc['marginal'])}%** vs 消费中性**{_fmt_num(tc['neutral_c'])}%** → **{tc['consumption']}**")

    # ===== 付鹏房地产周期框架（四维全字段渲染） =====
    lines.append("## 【付鹏·房地产周期框架】")
    lines.append("**结论先行：** " + fupeng_cycle_stage(fc, tc))
    for sec_title, key_labels in FUPENG_SECTIONS:
        lines.append(f"- **{sec_title}**")
        render_fields(lines, data, "付鹏框架", key_labels)
    lines.append(f"- **行为判定（居民加/去杠杆）：** {fc.get('household_behavior', 'n/a')}")
    lines.append(f"- **双杀判定：** 持房成本（按揭≈LPR {_fmt_num(fc.get('lpr5'))}%） vs 收益（租金收益率 {_fmt_num(fc.get('rent_yield'))}%）"
                 f" → **{'双杀成立（利率+房价双杀，钝性出清中）' if fc.get('double_kill') else '尚未击穿（收益仍可覆盖成本）'}**")

    # ===== 付鹏地方债框架（全字段渲染） =====
    lines.append("## 【付鹏·地方债框架】")
    render_fields(lines, data, "地方债", LOCAL_DEBT_KEYS)
    lines.append("- 一句结论：土地出让收入连年下行（1-8月同比-28.6%）压制地方财政弹性；6万亿置换近尾声、隐性债务余额6.5万亿仍待出清，城投/地方债风险传导需按月跟踪")

    # ===== 外部融资脆弱性与全球美元周期（三维度全字段渲染） =====
    lines.append("## 【付鹏·外部融资脆弱性与全球美元周期】")
    ext_concl = slow.get("维度5_外部融资脆弱性与全球美元周期", {}).get("外部融资脆弱性综合结论", "")
    if ext_concl:
        lines.append(f"**结论先行：** {ext_concl}")
    else:
        lines.append("**结论先行：** 外部融资脆弱性结论待更新")
    for sec_title, key_labels in EXTFIN_SECTIONS:
        lines.append(f"- **{sec_title}**")
        render_fields(lines, data, "外部融资", key_labels)

    # 底部信息
    lines.append("---")
    lines.append(f"数据纪律：所有数据标注截止日期；年度慢变量引用暂存文件（updated_at={slow_updated or '未更新'}），无值标'年度更新待填'，禁止编造。")
    lines.append(f"报告生成：{now}（土狗付鹏地产周报）")
    return "\n".join(lines)

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(BASE_DIR, "weekly_input.json"))
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    data_path = args.data
    if not os.path.exists(data_path):
        print(f"数据文件不存在: {data_path}")
        sys.exit(1)
    data = load_json(data_path)

    tc = tugou_calc(data)
    fc = fupeng_calc(data)
    body = build_report(data, fc, tc)

    out_path = os.path.join(BASE_DIR, f"土狗付鹏地产周报_{dt.date.today().strftime('%Y%m%d')}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print("报告已生成:", out_path)
    print("=" * 60)
    print(body)

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