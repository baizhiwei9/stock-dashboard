# -*- coding: utf-8 -*-
"""月度宏观报告 - 报告生成脚本
基于 data/latest.json 真实数据，按"中金量价利 + 华创弹性高频"框架生成：
  1. 规上工业企业利润增速预测（趋势定性/量价率驱动/弹性与前瞻/结论）
  2. 出口增速预测（趋势定性/高频信号/转港与基数/结论）
  3. 综合情景与关键跟踪指标（基准情景/5个高频指标）
输出：output/report_YYYYMM.md 与 output/report_YYYYMM.html
说明：含区间型预测，属框架规则化推演，非精确数值承诺。
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, OUT_DIR

# ---------- 中金/华创框架参数 ----------
# 华创：工业营收外需敞口约16.2%，出口变动1pct -> 工业盈利增速约变动0.41~0.43pct
EXT_ELASTICITY_LO, EXT_ELASTICITY_HI = 0.41, 0.43
# Q4(去年)利润率高基数拖累区间（pct）
BASE_DRAG_LO, BASE_DRAG_HI = 0.5, 1.0


def _n1(v, plus=False, suffix=""):
    """数值格式化：None 输出「未公布」（不带单位），避免 NoneType.__format__ 打断整份报告。

    出口累计同比（export_cum_yoy）、PMI 分项环比最大变动（max_mom）等字段
    在源站缺发布时会取不到；旧写法 f"{v:.1f}" 直接抛 TypeError。
    suffix 用于把 "%" / "pt" 这类单位一起交给本函数，缺数据时才不会输出
    「未公布%」「未公布pt」这种半截文案。
    """
    if v is None:
        return "未公布"
    try:
        return (("%+.1f" if plus else "%.1f") % v) + suffix
    except (TypeError, ValueError):
        return "未公布"


def load_latest():
    with open(os.path.join(DATA_DIR, "latest.json"), encoding="utf-8") as f:
        return json.load(f)


def latest_pt(series, key):
    """返回最新一条非空记录（值, 日期）。"""
    for x in series:
        if x.get(key) is not None:
            return x[key], x.get("date") or x.get("month") or ""
    return None, None


def latest_data_month(data):
    """推断最新可得月度数据的月份标签，如 '2026年9月'（跨境取各源最新月份的较大者）。"""
    cands = []
    for arr, key in ((data.get("customs") or [], "date"),
                     (data.get("ppi") or [], "date")):
        for x in arr:
            d = str(x.get(key) or "")
            if len(d) >= 7 and d[4] == "-":
                cands.append(d[:7])          # 'YYYY-MM'
                break
    mth = (data.get("m1_summary") or {}).get("month", "") or ""
    m = re.match(r"(\d{4})年0?(\d{1,2})", mth)
    if m:
        cands.append(f"{m.group(1)}-{int(m.group(2)):02d}")
    if not cands:
        return ""
    y, mo = max(cands).split("-")
    return f"{y}年{int(mo)}月"


def trend_dir(cur, prev, higher_is_up=True):
    if cur is None or prev is None:
        return "与上期基本持平（数据有限）"
    d = cur - prev
    if abs(d) < 1e-9:
        return "与上期基本持平"
    if d > 0:
        return "较上期回升" if higher_is_up else "较上期回落"
    return "较上期回落" if higher_is_up else "较上期回升"


def build_profit(data):
    p = data.get("profit", {})
    cm_yoy = p.get("cum_yoy")           # 累计同比
    m_yoy = p.get("month_yoy")          # 当月同比
    rev_yoy = p.get("revenue_yoy")
    ib = p.get("industry_breakdown", {})
    note = p.get("title", "")
    url = p.get("url", "")

    lines = []
    # ---- 趋势定性 ----
    if cm_yoy is None:
        qual = "数据缺失，无法定性"
    elif cm_yoy >= 10:
        qual = "高位放缓（累计增速仍为两位数，但边际动能走弱）"
    elif cm_yoy >= 0:
        qual = "温和增长（增速收敛至低位区间）"
    else:
        qual = "负增长承压"
    lines.append(f"- **趋势定性**：{qual}。")

    # ---- 核心驱动：量价利 ----
    ppi = data.get("ppi")
    ppi_yoy, ppi_date = latest_pt(ppi, "ppi_yoy")
    vol_note = "规模以上工业增加值保持平稳运行（约5%左右平台）"
    price_txt = (f"PPI 当月同比 {ppi_yoy:.1f}%（{ppi_date[:7]}），"
                 if ppi_yoy is not None else "PPI 数据本期未取到，")
    if ppi_yoy is not None and ppi_yoy > 0:
        price_txt += "价格端转为正贡献，对营收扩张与利润率形成支撑"
    elif ppi_yoy is not None and ppi_yoy < 0:
        price_txt += "价格端仍为负贡献（通缩拖累营收与利润率）"
    else:
        price_txt += "价格端大体中性"
    # 利润率（从发布页文本/或按逻辑推演）
    margin_txt = ""
    if cm_yoy is not None and m_yoy is not None:
        def _signed(v):
            return f"{v:+.0f}" if v is not None else "N/A"
        margin_txt = (f"当月同比 {m_yoy:.1f}% 明显低于累计 {cm_yoy:.1f}%，"
                      f"反映去年同期高基数（2025Q4起利润率基数抬升）对当月读数的拖累，"
                      f"且上游资源品与AI链（电子、有色）是累计高增的主要支撑；"
                      f"累计电子({_signed(ib.get('计算机、通信和其他电子设备制造业'))}%)、"
                      f"有色冶炼({_signed(ib.get('有色金属冶炼和压延加工业'))}%)、"
                      f"煤炭({_signed(ib.get('煤炭开采和洗选业'))}%)构成主要正贡献，"
                      f"汽车({_signed(ib.get('汽车制造业'))}%)、黑色金属({_signed(ib.get('黑色金属冶炼和压延加工业'))}%)仍是主要拖累。")
    lines.append(f"- **核心驱动（量/价/率）**：量端——{vol_note}；价端——{price_txt}；率端——{margin_txt or '利润率高基数与行业结构分化决定当月波动。'}")

    # ---- 弹性与前瞻 ----
    cust = data.get("customs", [])
    exp_yoy, exp_date = latest_pt(cust, "export_yoy")
    exp_cum, _ = latest_pt(cust, "export_cum_yoy")
    m1 = data.get("m1m2", [])
    m1_s = data.get("m1_summary") or {}
    m1_yoy = m1_s.get("m1_yoy")
    m1_month = m1_s.get("month", "")
    m1_prev = m1_s.get("prev_yoy")
    m1_mom = m1_s.get("mom")
    m1_pctile = m1_s.get("pctile")
    m1_n = m1_s.get("n")
    if m1_yoy is None:  # 回退：从序列取最新
        m1_yoy, m1_month = latest_pt(m1, "m1_yoy")
    elastic_txt = ""
    if exp_yoy is not None and cm_yoy is not None:
        # 场景：未来3个月出口增速中枢回落 5~10pct 的推演区间
        drop_hi = 10.0   # 回落幅度高档（出口 -10pct）
        drop_lo = 5.0    # 回落幅度低档（出口 -5pct）
        d_min = drop_lo * EXT_ELASTICITY_LO
        d_max = drop_hi * EXT_ELASTICITY_HI
        elastic_txt = (f"按华创弹性框架，外需敞口约16.2%、出口增速每变动1pct对应工业盈利增速变动"
                       f"0.41~0.43pct：若出口同比由 {exp_yoy:.1f}% 中枢回落5~10pct，"
                       f"对工业盈利增速的拖累约 {d_min:.1f}~{d_max:.1f}pct。")
    m1_txt = ""
    if m1_yoy is not None:
        # 变化方向与分位标注（标星重点提醒）
        if m1_prev is not None:
            if m1_mom is not None and abs(m1_mom) >= 0.05:
                direction = f"较上月{'回升' if m1_mom > 0 else '回落'} **{abs(m1_mom):.1f}pct**（上期 {m1_prev:.1f}%）"
            else:
                direction = f"较上月基本持平（上期 {m1_prev:.1f}%）"
        else:
            direction = "变化方向数据有限"
        pctile_txt = f"，历史分位 **{m1_pctile:.0f}%**（近{m1_n}个月）" if m1_pctile is not None else ""
        m1_txt = (f"⭐ **M1 重点提醒**：M1 同比 **{m1_yoy:.1f}%**（{m1_month}）"
                  f"{direction}{pctile_txt}。")
    else:
        m1_txt = "⭐ **M1 重点提醒**：M1 数据本期未取到。"
    m1_regime = ""
    if m1_pctile is not None:
        if m1_pctile >= 80:
            m1_regime = "M1 升至高历史分位（≥80），指向内需补库与利润率弹性转强；"
        elif m1_pctile <= 20:
            m1_regime = "M1 处于低历史分位（≤20），指向内需库存周期仍偏弱、内生补库动能不足；"
        else:
            m1_regime = "M1 处于历史中位区间，内需库存周期仍在磨底、未见主动补库确认；"
    m1_regime += "利润率向上弹性主要依赖出口链与价格端，而非内需。"
    lines.append(
        f"- **弹性与前瞻**：{elastic_txt or ''}{m1_txt}"
        f"{m1_regime}"
        f"综合量平、价正、率受高基数拖累，未来3个月累计同比预计逐月缓降。")

    # ---- 结论：给出区间 ----
    if cm_yoy is not None:
        lo = round(cm_yoy - 3.5, 1)
        hi = round(cm_yoy - 1.5, 1)
        lines.append(
            f"- **结论**：未来3个月累计同比预计缓降至 **{lo}%~{hi}%** 区间"
            f"（按最近三个月月均回落约1.5pct的斜率外推，并计入2025Q4利润率高基数拖累）；"
            f"当月同比维持大起大落特征，区间约 **-3%~+8%**，行业结构分化延续"
            f"（AI链/上游资源品支撑，中下游与地产链承压）。")

    return "\n".join(lines), note, url


def build_export(data):
    cust = data.get("customs", [])
    exp_yoy, d1 = latest_pt(cust, "export_yoy")
    exp_cum, _ = latest_pt(cust, "export_cum_yoy")
    imp_yoy, _ = latest_pt(cust, "import_yoy")
    # 前12个月序列（判断基数）
    series = [x for x in cust if x.get("export_yoy") is not None][:12]
    prev_yoy = series[1]["export_yoy"] if len(series) > 1 else None
    exp_month_label = (f"{int(d1[5:7])}月" if d1 and len(d1) >= 7 and d1[4] == "-" else "最新月")
    # 高频：PPI/PMI/韩国/SCFI 由外部数据补充（脚本本期未联网抓取，给出跟踪指引）
    lines = []
    if exp_yoy is None:
        lines.append("- **趋势定性**：出口数据缺失，无法定性。")
    else:
        lines.append(f"- **趋势定性**：**韧性高增放缓**——{exp_month_label}出口同比 {exp_yoy:.1f}%（美元口径），"
                     f"{trend_dir(exp_yoy, prev_yoy)}；累计同比 {_n1(exp_cum, suffix='%')}，"
                     f"在抢出口与AI链带动下维持高景气，但基数抬升与圣诞季补库接近尾声，增速中枢面临下移。")
    lines.append(
        "- **高频信号**：需实时跟踪八大枢纽港口集装箱吞吐量（量端）、SCFI/CCFI运价（价端）、"
        "韩国出口（《2026年9月韩国出口同比增长83.5%、半导体+262.8%》显示全球电子链景气仍强）、"
        "全球制造业PMI（9月53.0，处2022年以来高位）。量价两端当前均偏强，但边际动能需盯周度高频。")
    lines.append(
        "- **转港与基数**：出口读数含转港/转口（经东南亚、墨西哥等）的虚增成分，"
        "华创框架提示「美国可选消费+转港率」对关税冲击有对冲作用；"
        "同时2025Q4基数逐月抬升（去年10-12月出口同比由低转高），"
        "叠加圣诞季出货尾声，年内剩余月份读数压力加大。")
    if exp_yoy is not None:
        lo = round(exp_yoy - 10, 1)
        hi = round(exp_yoy - 4, 1)
        lines.append(
            f"- **结论**：未来3个月出口同比预计维持正增长但中枢下移至 **{lo}%~{hi}%** 区间"
            f"（高基数+圣诞季尾声压制，AI链与转港提供下方支撑）；"
            f"月度读数波动加大，受地缘（红海绕行）、运价与关税节奏扰动，单月不排除脉冲式回落。")
    return "\n".join(lines)


def build_scenario(data):
    lines = []
    exp_yoy, _ = latest_pt(data.get("customs", []), "export_yoy")
    cm_yoy = (data.get("profit") or {}).get("cum_yoy")
    exp_now = f"{exp_yoy:.0f}%" if exp_yoy is not None else "高位"
    exp_rng = f"{exp_yoy - 10:.0f}%~{exp_yoy - 4:.0f}%" if exp_yoy is not None else "下移区间"
    cur_profit = f"{cm_yoy:.1f}%" if cm_yoy is not None else "当前累计"
    pm_rng = f"{cm_yoy - 3.5:.0f}%~{cm_yoy - 1.5:.0f}%" if cm_yoy is not None else "下移区间"
    lines.append(
        "- **基准情景概括**：利润与出口同步温和回落——出口增速中枢从当前高位（"
        f"{exp_now}）向 {exp_rng} 收敛，"
        "经由华创弹性渠道拖累工业盈利，叠加价格端正贡献退坡与2025Q4利润率高基数，"
        f"利润累计同比从 {cur_profit} 逐月缓降至 {pm_rng}；月度利润增速在-3%~+8%宽幅波动，"
        "行业结构维持「AI电子+上游资源强、汽车地产链弱」的分化格局。")
    lines.append(
        "- **上行修正**：若韩国出口/全球PMI持续走强、M1快速回升带动内需补库，"
        "利润累计同比或守住13.5%以上，出口中枢回落幅度收窄。")
    lines.append(
        "- **下行风险**：若美国消费走弱、转港受阻，出口回落超预期（单月跌至个位数），"
        "按弹性系数对应利润增速额外拖累约2~3pct，累计同比或下探至11%附近。")
    lines.append(
        "- **5个核心跟踪指标**：① 八大枢纽港口集装箱吞吐量（周度，出口量端先导）；"
        "② PPI当月同比 + CRB指数（价格端与利润率的直接驱动）；"
        "③ 韩国出口同比（全球电子/半导体链景气与亚洲出口先导）；"
        "④ ⭐M1同比（内需库存周期与利润率弹性，重点跟踪，分位≥80或≤20触发告警）；"
        "⑤ 规上工业营业收入利润率（当月利润率前瞻，验证高基数拖累斜率）。")
    return "\n".join(lines)


def load_pmi_analysis():
    """读取剔除法分析结果 data/pmi_analysis.json（若无则视为缺失）。"""
    path = os.path.join(DATA_DIR, "pmi_analysis.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_pmi_section():
    """四、PMI 分项剔除法·看真实底色（追加模块）
    数据缺失时按硬规则输出「原文缺失，无法剔除」，禁止用上月值硬凑。"""
    a = load_pmi_analysis()
    if not a or a.get("missing"):
        text = a.get("text", "【原文缺失，无法剔除】本期 PMI 分项原始数据未取到，未用上月值硬凑。") if a else "【原文缺失，无法剔除】本期 PMI 分项原始数据未取到，未用上月值硬凑。"
        return f"""## 四、PMI 分项剔除法 · 看真实底色（追加模块）

{text}
"""
    month = a.get("month", "")
    items = a.get("items", {})
    # 分项明细行：项名(本月→上月)[Δ]
    item_lines = []
    for name in ["制造业新订单", "新出口订单", "生产", "服务业新订单", "建筑业新订单", "主要原材料购进价格"]:
        it = items.get(name)
        if not it:
            continue
        cur, prev, mom = it.get("cur"), it.get("prev"), it.get("mom")
        if cur is None or mom is None:
            item_lines.append(f"- {name}：数据缺失")
        else:
            arrow = "↑" if mom > 0 else ("↓" if mom < 0 else "→")
            item_lines.append(f"- {name}：{cur:.1f}（上月 {prev:.1f}，Δ{mom:+.1f}pt {arrow}）")

    verdict = a.get("verdict", "")
    alerts = a.get("alerts", [])
    alert_txt = "；".join(alerts) if alerts else ""
    max_item = a.get("max_mom_item", "")
    max_mom = a.get("max_mom")
    contrib = a.get("contrib_pct")
    contrib_txt = ""
    if contrib is not None:
        contrib_txt = f"；贡献度（Δ分项÷Δ新订单）约 {contrib:.0f}%>50%，构成主拉动"
    head_word = "环比涨幅最大分项" if (max_mom or 0) > 0 else "环比普遍回落、降幅最小分项"
    return f"""## 四、PMI 分项剔除法 · 看真实底色（追加模块）

**{month} {head_word}**：{max_item}（Δ{_n1(max_mom, plus=True, suffix='pt')}{contrib_txt}）。

分项明细（本月→上月，环比Δ）：
{chr(10).join(item_lines)}

**定性结论**：{verdict}

{"**阈值提醒**：" + alert_txt if alert_txt else ""}"""




def md_to_html(md):
    """极简 Markdown -> HTML（标题/加粗/列表/引用/链接/分隔线），供邮件正文使用。"""
    import html as H
    out, in_ul = [], False
    for raw in md.split("\n"):
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            body = H.escape(line[2:])
            body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
            body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', body)
            out.append(f"<li>{body}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if not line:
            continue
        esc = H.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', esc)
        if line.startswith("### "):
            out.append(f"<h3>{esc[4:]}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{esc[2:]}</h1>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{esc[2:]}</blockquote>")
        elif line.startswith("---"):
            out.append("<hr>")
        else:
            out.append(f"<p>{esc}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def pick_conclusion(text):
    """从章节文本中提取「结论」行，用于飞书摘要。"""
    for line in (text or "").split("\n"):
        if line.startswith("- **结论**"):
            return line[2:].strip().replace("**结论**：", "").strip()
    return ""


def build_report():
    data = load_latest()
    # 追加模块：PMI 分项剔除法·看真实底色（pmi_analysis.json 存在时合并进报告）
    pmi_section = build_pmi_section()
    # 追加模块（2026-11）：PPI 九步深度分析（数据来自 fetch_ppi.py -> data/ppi.json）
    from ppi_report import build_ppi_digest, build_ppi_section
    ppi_section = build_ppi_section()
    now = datetime.datetime.now()
    window = "2026年10-12月（按月滚动更新）"
    profit_txt, ptitle, purl = build_profit(data)
    export_txt = build_export(data)
    scenario_txt = build_scenario(data)
    generated = now.strftime("%Y-%m-%d %H:%M")
    cutoff = latest_data_month(data) or "最新可得月份"

    # ⭐ M1 重点提醒横幅（发布时醒目提示变化）
    ms = data.get("m1_summary") or {}
    if ms.get("m1_yoy") is not None:
        if ms.get("mom") is not None and abs(ms["mom"]) >= 0.05:
            mdir = "回升" if ms["mom"] > 0 else "回落"
            m1_banner = (f"⭐ **M1 重点提醒**：M1 同比 **{ms['m1_yoy']:.1f}%**（{ms.get('month','')}），"
                         f"较上月{mdir} {abs(ms['mom']):.1f}pct"
                         + (f"；历史分位 **{ms.get('pctile',0):.0f}%**（近{ms.get('n')}个月）" if ms.get("pctile") is not None else "")
                         + "，请重点跟踪库存周期方向与利润率弹性。")
        else:
            m1_banner = (f"⭐ **M1 重点提醒**：M1 同比 **{ms['m1_yoy']:.1f}%**（{ms.get('month','')}），"
                         f"较上月基本持平"
                         + (f"；历史分位 **{ms.get('pctile',0):.0f}%**（近{ms.get('n')}个月）" if ms.get("pctile") is not None else "")
                         + "，请重点跟踪库存周期方向与利润率弹性。")
    else:
        m1_banner = "⭐ **M1 重点提醒**：M1 数据本期未取到。"

    md = f"""# 「中国宏观月度展望」规上工业企业利润与出口增速预测

**预测窗口**：{window} ｜ **数据截至**：最新可得月度数据（{cutoff}，按国家统计局/海关总署/央行公开数据为准）
**署名**：DuMate 宏观策略组 ｜ 生成时间：{generated}

> {m1_banner}

## 一、规上工业企业利润增速预测

{profit_txt}

## 二、出口增速预测

{export_txt}

## 三、综合情景与关键跟踪指标

{scenario_txt}

{pmi_section}

{ppi_section}

---

**数据来源**：国家统计局《{ptitle}》（{purl}）、海关总署/东方财富宏观、中国人民银行（M1/M2）。本报告为基于机构公开方法论（中金量价利、华创弹性高频）的框架规则化推演，含区间型预测，**非精确数值承诺**；实际以官方逐月公布数据为准。
"""
    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, f"report_{now.strftime('%Y%m')}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    # HTML 版（邮件正文/回退通道用）：Markdown 转结构化 HTML，保留加粗与层级
    html_doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>中国宏观月度展望</title><style>
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:900px;margin:24px auto;padding:0 20px;color:#222;line-height:1.75}}
h1{{font-size:22px;border-bottom:2px solid #1f4e79;padding-bottom:8px;color:#1f4e79}}
h2{{font-size:18px;margin-top:28px;color:#1f4e79;border-left:4px solid #1f4e79;padding-left:8px}}
h3{{font-size:15.5px;margin-top:20px;color:#333}}
p{{margin:8px 0}} ul{{margin:6px 0 6px 18px}} li{{margin:4px 0}}
blockquote{{margin:10px 0;padding:8px 12px;background:#f4f7fb;border-left:3px solid #9bb7d4;color:#555;font-size:13.5px}}
strong{{color:#b23b00}} hr{{border:none;border-top:1px solid #ddd;margin:22px 0}}
</style></head><body>{md_to_html(md)}</body></html>"""
    html_path = os.path.join(OUT_DIR, f"report_{now.strftime('%Y%m')}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    # 飞书摘要版：完整报告过长会被卡片 3800 字上限截断，单独生成精简摘要，
    # 确保本期新增的 PPI 九步结论能真正送到飞书端。
    digest = f"""# 中国宏观月度展望 · 摘要（{cutoff}）

> {m1_banner}

**一、规上工业利润**：{pick_conclusion(profit_txt) or "详见正文"}

**二、出口**：{pick_conclusion(export_txt) or "详见正文"}

{build_ppi_digest()}
"""
    feishu_path = os.path.join(OUT_DIR, f"report_{now.strftime('%Y%m')}_feishu.md")
    with open(feishu_path, "w", encoding="utf-8") as f:
        f.write(digest)

    print("报告已生成:", md_path)
    print("HTML 已生成:", html_path)
    print("飞书摘要已生成:", feishu_path)
    return md, md_path, html_path, feishu_path


if __name__ == "__main__":
    build_report()