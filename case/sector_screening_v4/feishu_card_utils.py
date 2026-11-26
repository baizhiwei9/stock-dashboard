# -*- coding: utf-8 -*-
"""
飞书交互式卡片工具模块
为所有市场监控报告提供统一的卡片构建工具

设计原则:
- 顶部标题栏：报告名称 + 日期
- 核心指标区：大号数字+颜色标签展示关键结论
- 信号标签：🟢看多 / 🟡中性 / 🔴看空 / ⚠️风险
- 趋势箭头：↑↓↗↘→
- 详细数据：lark_md 表格展示
- 底部备注：数据来源、更新时间
"""

import json
from datetime import datetime


# ========== 颜色模板映射 ==========
TEMPLATE_COLORS = {
    'green': 'green',      # 看多/积极
    'red': 'red',          # 看空/风险
    'yellow': 'yellow',    # 中性/预警
    'blue': 'blue',        # 信息/数据
    'purple': 'purple',    # 特殊信号
    'grey': 'grey',        # 观望/等待
    'turquoise': 'turquoise',  # 流动/宽松
    'orange': 'orange',    # 偏空/偏高
    'carmine': 'carmine',  # 极端风险
}


def _escape_md(text):
    """安全处理文本，避免破坏markdown格式"""
    if text is None:
        return 'N/A'
    return str(text)


def _warn_stock_name(name, ytd_pct):
    """年内跌幅超50%的股票名称前加⚠️警告标记
    
    Args:
        name: 股票名称
        ytd_pct: 年内涨幅百分比（负数表示跌幅）
    
    Returns:
        str: 带警告标记的股票名称
    """
    if ytd_pct is not None and isinstance(ytd_pct, (int, float)) and ytd_pct < -50:
        return f'⚠️ {name}'
    return name


# ========== 卡片基础构建函数 ==========

def build_card_header(title, subtitle=None, template='blue'):
    """构建卡片头部"""
    header = {
        "tag": "header",
        "template": template,
        "title": {
            "tag": "plain_text",
            "content": title
        }
    }
    return header


def build_section_header(text, tag='bold'):
    """构建区块标题"""
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**{text}**"
        }
    }


def build_hr():
    """构建分割线"""
    return {"tag": "hr"}


def build_note(text, tag=None):
    """构建底部备注
    
    Args:
        text: 备注文本
        tag: 可选标签（warning/info等），用于样式区分，兼容旧调用
    """
    # 根据tag调整显示样式
    if tag == "warning":
        prefix = "⚠️ "
    elif tag == "info":
        prefix = "ℹ️ "
    else:
        prefix = ""
    
    return {
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": prefix + text
            }
        ]
    }


def build_collapsible(title, content_lines, default_expanded=False):
    """构建指标说明区域（飞书interactive card不支持collapse组件，改用markdown+灰字呈现）
    
    适用于：指标说明、使用方法等说明性文字（每行是一个独立条目）
    效果：灰色文字 + 每行前加 • 项目符号
    
    title: 区域标题
    content_lines: list of strings，每行一条说明
    default_expanded: 预留参数，实际直接展开展示
    """
    # 使用markdown列表形式，灰字呈现说明内容
    content_parts = [f"<font color='grey'>📖 **{title}**</font>"]
    for line in content_lines:
        content_parts.append(f"<font color='grey'>• {line}</font>")
    
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "\n".join(content_parts)
        }
    }


def build_module_block(title, content_lines):
    """构建模块内容区块（标题 + 正文，保留完整markdown格式）
    
    适用于：数据表格、详细分析等需要正常渲染markdown的内容
    效果：加粗标题 + 正常颜色 + 完整markdown支持（表格、列表等）
    
    title: 模块标题
    content_lines: list of strings，每行内容（支持markdown表格、列表等）
    """
    content = f"**{title}**\n\n" + "\n".join(content_lines)
    return build_markdown(content)


def build_markdown(content):
    """构建markdown文本块"""
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": content
        }
    }


# ========== 核心指标展示 ==========

def build_metric_row(metrics):
    """构建指标行（多列并排，每列含标签+数值+状态）
    
    metrics: list of dict with keys: label, value, status(可选), trend(可选箭头)
    """
    columns = []
    for m in metrics:
        value_str = _escape_md(m.get('value', '-'))
        label_str = _escape_md(m.get('label', ''))
        status_str = m.get('status', '')
        trend = m.get('trend', '')
        
        content_parts = []
        if label_str:
            content_parts.append(f"<font color='grey'>{label_str}</font>")
        content_parts.append(f"**{trend} {value_str}**")
        if status_str:
            content_parts.append(status_str)
        
        columns.append({
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "vertical_align": "top",
            "elements": [
                {
                    "tag": "markdown",
                    "content": "\n".join(content_parts),
                    "text_align": "center"
                }
            ]
        })
    
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "columns": columns
    }


def build_key_value_table(rows, title=None):
    """构建键值对表格（用lark_md实现）
    
    rows: list of (key, value) tuples or list of dict with key/value
    """
    lines = []
    if title:
        lines.append(f"**{title}**")
        lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    for row in rows:
        if isinstance(row, (list, tuple)):
            k, v = row[0], row[1]
        else:
            k, v = row.get('key', ''), row.get('value', '')
        lines.append(f"| {k} | {v} |")
    return build_markdown("\n".join(lines))


def build_data_table(headers, rows, title=None):
    """构建通用数据表格
    
    headers: list of header strings
    rows: list of lists (每行数据)
    title: 可选表格标题
    """
    lines = []
    if title:
        lines.append(f"**{title}**")
        lines.append("")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["------"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_escape_md(c) for c in row) + " |")
    return build_markdown("\n".join(lines))


# ========== 信号标签组件 ==========

def build_signal_tags(tags, title=None):
    """构建信号标签行
    
    tags: list of dict with: text, color (green/red/yellow/blue)
    """
    tag_strs = []
    color_map = {
        'green': 'green',
        'red': 'red', 
        'yellow': 'yellow',
        'blue': 'blue',
        'orange': 'orange',
    }
    for t in tags:
        color = color_map.get(t.get('color', 'blue'), 'blue')
        tag_strs.append(f"<font color='{color}'>**{t['text']}**</font>")
    
    content = ""
    if title:
        content = f"**{title}**: "
    content += "  ".join(tag_strs)
    return build_markdown(content)


# ========== 完整卡片封装 ==========

def wrap_card(header, elements):
    """将头部和元素封装为完整卡片JSON"""
    return {
        "config": {
            "wide_screen_mode": True
        },
        "header": header,
        "elements": elements
    }


def card_to_json(card):
    """将卡片转为JSON字符串（用于lark-cli推送）"""
    return json.dumps(card, ensure_ascii=False)


# ========== 各报告专用卡片构建函数 ==========

# ---------- 1. 结构因子日报 ----------
def build_structural_factor_card(date_str, indices_data):
    """结构因子日报卡片（v2.0：新增赔率模式）

    indices_data: list of dict with: name, abs_factor, abs_status, rel_factor,
                   rel_status, overall, close, price_percentile, odds_label
    """
    # 判断整体信号（赔率信号优先级最高）
    odds_labels = [d.get('odds_label', '') for d in indices_data]
    has_bottom_odds = any('底部赔率' in l for l in odds_labels)
    has_top_odds = any('顶部赔率' in l for l in odds_labels)
    overalls = [d.get('overall', '') for d in indices_data]

    if has_bottom_odds:
        template = 'green'
        main_signal = '🎯 底部赔率信号'
    elif has_top_odds:
        template = 'red'
        main_signal = '⚠️ 顶部赔率风险'
    elif any('胜率' in o for o in overalls):
        template = 'green'
        main_signal = '🟢 胜率模式'
    elif any('空头挤出中' in o for o in overalls):
        template = 'yellow'
        main_signal = '🟡 空头挤出中'
    else:
        template = 'grey'
        main_signal = '⚪ 观望'

    # 极端信号
    extreme_signals = []
    for d in indices_data:
        abs_v = d.get('abs_factor', 0)
        if abs(abs_v) > 2:
            extreme_signals.append(f"{d['name']} AbsFactor={abs_v:+.2f}σ")
        rel_v = d.get('rel_factor', 0)
        if abs(rel_v) > 2:
            extreme_signals.append(f"{d['name']} RelFactor={rel_v:+.2f}σ")

    header = build_card_header(f"📊 结构因子日报 | {date_str}", template=template)

    elements = []

    # 核心指标：整体信号
    elements.append(build_markdown(f"**综合信号**: {main_signal}"))
    elements.append(build_markdown("<font color='grey'>💡 RelFactor: <0=资金流入, >0=资金流出/挤出</font>"))
    elements.append(build_hr())

    # 赔率信号突出展示（如有）
    bottom_odds_list = [d for d in indices_data if d.get('odds_label') == '🎯底部赔率关注']
    top_odds_list = [d for d in indices_data if d.get('odds_label') == '⚠️顶部赔率风险']

    if bottom_odds_list or top_odds_list:
        odds_lines = []
        if bottom_odds_list:
            for d in bottom_odds_list:
                odds_lines.append(
                    f"🎯 **{d['name']}** 底部赔率关注\n"
                    f"　价格分位: {d.get('price_percentile', 0):.1f}% | "
                    f"Abs={d.get('abs_factor', 0):+.2f}σ | "
                    f"Rel={d.get('rel_factor', 0):+.2f}σ"
                )
        if top_odds_list:
            for d in top_odds_list:
                odds_lines.append(
                    f"⚠️ **{d['name']}** 顶部赔率风险\n"
                    f"　价格分位: {d.get('price_percentile', 0):.1f}% | "
                    f"Abs={d.get('abs_factor', 0):+.2f}σ | "
                    f"Rel={d.get('rel_factor', 0):+.2f}σ"
                )
        elements.append(build_markdown("**🎯 赔率信号**\n\n" + "\n\n".join(odds_lines)))
        elements.append(build_hr())

    # 各指数概览（含价格分位、赔率信号）
    table_rows = []
    for d in indices_data:
        pct = d.get('price_percentile', 50)
        odds_lbl = d.get('odds_label', '')
        # 价格分位颜色标记
        if pct < 25:
            pct_str = f"<font color='green'>**{pct:.1f}%**</font>"
        elif pct > 75:
            pct_str = f"<font color='red'>**{pct:.1f}%**</font>"
        else:
            pct_str = f"{pct:.1f}%"

        table_rows.append([
            d.get('name', ''),
            f"{d.get('abs_factor', 0):+.2f}σ",
            d.get('abs_status', ''),
            f"{d.get('rel_factor', 0):+.2f}σ",
            d.get('rel_status', ''),
            pct_str,
            odds_lbl or '-',
        ])

    elements.append(build_data_table(
        ['指数', 'AbsFactor', '状态', 'RelFactor', '状态', '价格分位', '赔率信号'],
        table_rows,
        title="指数结构因子明细"
    ))

    # 极端信号提醒
    if extreme_signals:
        elements.append(build_hr())
        elements.append(build_markdown("⚠️ **极端信号提醒**:\n" + "\n".join(f"- {s}" for s in extreme_signals)))

    # 指标说明（v2.0 更新）
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**AbsFactor（绝对因子）**：衡量多空力量绝对值，单位为σ（标准差）。>2σ极端多头、>1σ多头、-1~1σ震荡、<-1σ空头、<-2σ极端空头",
        "**RelFactor（相对因子）**：衡量资金流动方向。正值=资金挤出/流出，负值=资金流入",
        "**价格分位**：当前收盘价在40日窗口内的百分位排名(0-100)，<25%为低位、>75%为高位",
        "**胜率模式**：AbsFactor>0且资金未挤出，适合追涨顺势操作",
        "**底部赔率🎯**：价格分位<25% + 资金流入(Rel<0) + 空头格局(Abs<0)，跌多了有资金接，关注反转",
        "**顶部赔率⚠️**：价格分位>75% + 资金流出(Rel>0) + 多头格局(Abs>0)，涨多了资金在撤，注意风险",
        "**使用建议**：赔率信号优先级高于胜率模式；多指数共振时可靠性高；出现极端信号(>±2σ)时重点关注",
    ]))

    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))

    return wrap_card(header, elements)


# ---------- 2. CFC结构因子 ----------
def build_cfc_card(date_str, factors, data_available=True, omo_data=None):
    """CFC结构因子卡片
    
    omo_data: 央行OMO操作数据（可选）
    factors: dict with pool_name -> {cfc, cfc_ratio, weighted_skew, weighted_kurt, up50, top5z, cdi, ...}
    """
    # 判断整体状态
    sell_pools = []
    buy_pools = []
    for name, f in factors.items():
        if not f:
            continue
        cfc = f.get('cfc', 0)
        if cfc is None:
            continue
        if cfc > 1.5:
            sell_pools.append(name)
        elif cfc < -1:
            buy_pools.append(name)
    
    if sell_pools:
        template = 'red'
        main_signal = f"🔴 卖出信号: {', '.join(sell_pools)}"
    elif buy_pools:
        template = 'green'
        main_signal = f"🟢 买入信号: {', '.join(buy_pools)}"
    else:
        template = 'blue'
        main_signal = '⚖️ 全线中性'
    
    header = build_card_header(f"📊 CFC结构因子日报 | {date_str}", template=template)
    
    elements = []
    
    if not data_available or not factors:
        elements.append(build_markdown("⚠️ **数据缺失警告**：tushare日线数据未发布，因子汇总表可能为空，请稍后重试"))
        elements.append(build_hr())
        elements.append(build_note(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"))
        return wrap_card(header, elements)
    
    # 核心信号
    elements.append(build_markdown(f"**核心信号**: {main_signal}"))
    elements.append(build_hr())
    
    # 因子表（精简版）
    pool_order = ["沪深300", "中证1000", "创业板", "科创板", "全市场前2000"]
    table_rows = []
    for pool_name in pool_order:
        if pool_name not in factors or not factors[pool_name]:
            continue
        f = factors[pool_name]
        cfc = f.get('cfc')
        cfc_str = f"{cfc:.3f}" if cfc is not None else 'N/A'
        # 信号判断
        if cfc is not None:
            if cfc > 1.5:
                cfc_signal = "🔴卖出"
            elif cfc < -1:
                cfc_signal = "🟢买入"
            else:
                cfc_signal = "⚖️中性"
        else:
            cfc_signal = '-'
        
        w_skew = f.get('weighted_skew')
        w_kurt = f.get('weighted_kurt')
        skew_str = f"{w_skew:.2f}" if w_skew is not None else 'N/A'
        kurt_str = f"{w_kurt:.2f}" if w_kurt is not None else 'N/A'
        
        up50 = f.get('up50')
        up50_str = f"{up50*100:.1f}%" if up50 is not None else 'N/A'
        
        cdi = f.get('cdi')
        cdi_str = f"{cdi:.2f}" if cdi is not None else 'N/A'
        if cdi is not None:
            if cdi > 0.3:
                cdi_signal = "🔴恶化"
            elif cdi < -0.3:
                cdi_signal = "🟢改善"
            else:
                cdi_signal = "➖中性"
        else:
            cdi_signal = '-'
        
        table_rows.append([pool_name, cfc_str, cfc_signal, skew_str, kurt_str, up50_str, cdi_str, cdi_signal])
    
    elements.append(build_data_table(
        ['标的池', 'CFC', '信号', 'w_skew', 'w_kurt', 'up50', 'CDI峰度', 'CDI峰度信号'],
        table_rows,
        title="完整因子表"
    ))
    
    # 关键解读
    elements.append(build_hr())
    elements.append(build_section_header("🔍 关键信号解读"))
    
    interpret_lines = []
    
    # CDI峰度恶化报警（最醒目，放在最前面）
    cdi_det_pools = []
    for pool_name in pool_order:
        if pool_name in factors and factors[pool_name]:
            cdi = factors[pool_name].get('cdi')
            # CDI峰度 <= 3 为恶化
            if cdi is not None and cdi <= 3:
                cdi_det_pools.append((pool_name, cdi))
    if cdi_det_pools:
        det_str = "、".join([f"🔴 **{p}({v:.2f})**" for p, v in cdi_det_pools])
        interpret_lines.append(f"🚨 **CDI峰度恶化报警**: {det_str}")
        interpret_lines.append("  加仓资金高度集中，微观结构脆弱，一旦抱团瓦解普跌风险大 ⚠️")
    
    if sell_pools:
        for pool_name in sell_pools:
            f = factors[pool_name]
            w_skew = f.get('weighted_skew', 0) or 0
            w_kurt = f.get('weighted_kurt', 0) or 0
            interpret_lines.append(f"⚠️ **{pool_name}** CFC={f.get('cfc',0):.2f} → 触发卖出阈值(>1.5)")
            if abs(w_skew) > 1.0 and abs(w_kurt) > 1.0:
                interpret_lines.append(f"  w_skew={w_skew:.2f}+w_kurt={w_kurt:.2f}双高，成交极度偏向头部股+分布极端尖峰")
    else:
        interpret_lines.append("• CFC全线中性，暂无极端信号")
    
    # CDI峰度全线判断（改善池）
    cdi_neg_pools = []
    for pool_name in pool_order:
        if pool_name in factors and factors[pool_name]:
            cdi = factors[pool_name].get('cdi')
            if cdi is not None and cdi > 3:
                cdi_neg_pools.append(pool_name)
    if cdi_neg_pools:
        interpret_lines.append(f"• 🟢 CDI峰度健康池: {', '.join(cdi_neg_pools)}")
    
    elements.append(build_markdown("\n".join(interpret_lines)))
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**CFC（截面因子集中度）**：= 0.4×加权峰度 + 0.6×加权偏度。>1.5卖出信号（成交极度集中头部+极端分布），<-1买入信号（分散程度高）",
        "**w_skew（加权偏度）**：衡量收益分布对称性。正偏=上涨股主导（上涨股少但涨幅大），负偏=下跌股主导（下跌股少但跌幅大）",
        "**w_kurt（加权峰度）**：衡量收益分布尖峰程度。高=成交集中在少数头部股票（抱团行情），低=成交分散（普涨/普跌行情）",
        "**CDI风洞（峰度版微观结构指数）**：衡量个股分化程度。>0.3恶化（分化加剧，选股难度大），<-0.3改善（同涨同跌，指数行情）",
        "**up50**：涨幅前50%股票成交额占比，越高代表上涨股吸金能力越强",
        "**使用建议**：CFC+CDI风洞双信号共振时可靠性高；沪深300与中证1000信号分化时注意大小盘风格切换",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"CFC = 0.4×峰度 + 0.6×偏度 | 样本: A股前2000只 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 3. 期权监控 ----------
def build_option_monitor_card(date_str, skew_data, gex_data, basis_data, data_warnings=None, omo_data=None, risk_reversal_data=None, ust_data=None, ev_data=None):
    """期权监控卡片
    
    skew_data: dict {name: {atm_iv, iv_skew, cboe_skew, vol_pcr, direction}}
    gex_data: dict {name: {total_gex_yi, flip_point, risk}}
    basis_data: list of {product, type, dte, basis_rate, rank_252, z_score, signal}
    data_warnings: list of warning strings，数据缺失告警
    omo_data: 央行OMO操作数据（可选）
    """
    # 判断整体风险
    has_high_risk = False
    has_warning = False
    for name, d in skew_data.items():
        if d and d.get('direction', '') and ('看空' in d.get('direction', '') or '🔴' in d.get('direction', '')):
            has_high_risk = True
        if d and '🟡' in d.get('direction', ''):
            has_warning = True
    
    data_warnings = data_warnings or []
    has_data_missing = len(data_warnings) > 0

    if has_high_risk:
        template = 'red'
    elif has_warning or has_data_missing:
        template = 'yellow'
    else:
        template = 'blue'
    
    title_prefix = "⚠️ " if has_data_missing else ""
    header = build_card_header(f"{title_prefix}📊 期权综合监控 | {date_str}", template=template)
    
    elements = []

    # 数据缺失告警（放在最前面）
    if data_warnings:
        elements.append(build_note("\\n".join(data_warnings), tag="warning"))
    
    # SKEW汇总表
    skew_rows = []
    for name, d in skew_data.items():
        if d:
            skew_rows.append([
                name,
                f"{d.get('atm_iv', 0):.2f}%",
                f"{d.get('iv_skew', 0):+.2f}%",
                f"{d.get('cboe_skew', 0):.2f}",
                f"{d.get('vol_pcr', 0):.2f}",
                d.get('direction', ''),
            ])
        else:
            skew_rows.append([name, 'N/A', 'N/A', 'N/A', 'N/A', '-'])
    
    elements.append(build_data_table(
        ['标的', 'ATM IV', 'IV Skew', 'SKEW', 'Vol PCR', '方向'],
        skew_rows,
        title="📈 SKEW汇总"
    ))
    
    elements.append(build_hr())
    
    # Risk Reversal（风险逆转）汇总表
    if risk_reversal_data:
        rr_rows = []
        for name, d in risk_reversal_data.items():
            if d:
                rr_rows.append([
                    name,
                    f"{d.get('rr', 0):+.2f}%",
                    f"{d.get('call_iv', 0):.2f}%",
                    f"{d.get('put_iv', 0):.2f}%",
                    str(d.get('nearest_dte', '')),
                    d.get('direction', ''),
                ])
            else:
                rr_rows.append([name, 'N/A', 'N/A', 'N/A', '-', '-'])
        
        elements.append(build_data_table(
            ['标的', 'Risk Reversal', 'Call IV', 'Put IV', 'DTE', '方向'],
            rr_rows,
            title="🔄 8.1 Risk Reversal（风险逆转）"
        ))
        elements.append(build_note(
            "Risk Reversal = 相同到期日、相同Delta的虚值看涨IV - 虚值看跌IV。"
            "正值→看涨更贵（市场押注向上），负值→看跌更贵（押注向下）。"
            "这是真金白银押注的方向，比历史数据更真实。",
            tag="info"
        ))
        elements.append(build_hr())
    
    # EV预期收益汇总表
    if ev_data:
        ev_rows = []
        for name, d in ev_data.items():
            if d and d.get('levels'):
                lv = {l['label']: l['comb_ev'] for l in d['levels']}
                itm_key = '实值5%' if '实值5%' in lv else '实值3%'
                otm_key = '虚值5%' if '虚值5%' in lv else '虚值3%'
                spot_s = f"{d['spot']:.4f}" if d.get('spot', 0) and d['spot'] < 100 else f"{d.get('spot',0):.2f}"
                atm_ev = d.get('atm_ev', 0)
                atm_ev_str = f"{atm_ev:+.1f}"
                itm_ev_str = f"{lv.get(itm_key, 0):+.1f}"
                otm_ev_str = f"{lv.get(otm_key, 0):+.1f}"
                q_str = f"{d.get('implied_q', 0):.1f}%"
                ev_rows.append([
                    name,
                    spot_s,
                    atm_ev_str,
                    itm_ev_str,
                    otm_ev_str,
                    q_str,
                    d.get('expire', ''),
                ])
            else:
                ev_rows.append([name, 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', '-'])
        
        elements.append(build_data_table(
            ['标的', '现价', '平值EV', '实值档EV', '虚值档EV', '隐含分红', '到期'],
            ev_rows,
            title="🎯 EV预期收益"
        ))
        elements.append(build_note(
            "EV = 风险中性公允价 - 市价。正值=被低估（买入有正EV），负值=被高估。"
            "Call+Put等权重综合。基于Breeden-Litzenberger完整算法。",
            tag="info"
        ))
        elements.append(build_hr())
    
    # GEX汇总表
    gex_rows = []
    for name, d in gex_data.items():
        if d:
            fp = f"{d['flip_point']:.3f}" if d.get('flip_point') else 'N/A'
            gex_rows.append([
                name,
                f"{d.get('total_gex_yi', 0):+.2f}亿",
                fp,
                d.get('risk', ''),
            ])
        else:
            gex_rows.append([name, 'N/A', 'N/A', '-'])
    
    elements.append(build_data_table(
        ['标的', 'Net GEX', 'Flip Point', '风险'],
        gex_rows,
        title="💰 GEX汇总"
    ))
    
    elements.append(build_hr())
    
    # 升贴水汇总
    if basis_data:
        basis_rows = []
        for b in basis_data:
            rk = f"{b['rank_252']:.4f}" if b.get('rank_252') else 'N/A'
            zs = f"{b['z_score']:+.2f}" if b.get('z_score') is not None else 'N/A'
            br = f"{b['basis_rate']:+.4f}%" if b.get('basis_rate') is not None else 'N/A'
            basis_rows.append([
                b.get('product', ''),
                b.get('type', ''),
                str(b.get('dte', '')),
                br,
                rk,
                zs,
                b.get('signal', ''),
            ])
        
        elements.append(build_data_table(
            ['品种', '类型', 'DTE', '年化基差率', 'rank', 'Z', '信号'],
            basis_rows,
            title="📊 升贴水汇总"
        ))
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**ATM IV（平值隐含波动率）**：市场对未来波动的预期。IV高=预期波动大（恐慌/亢奋），IV低=预期波动小（平静/麻木）",
        "**IV Skew（波动率偏度）**：看涨与看跌期权IV差值。正偏=看涨期权更贵（看多情绪），负偏=看跌期权更贵（避险情绪）",
        "**SKEW指数**：类似CBOE SKEW，衡量尾部风险。越高代表市场越担心黑天鹅事件（左尾风险）",
        "**Risk Reversal（风险逆转）**：相同到期日、相同Delta的虚值看涨与虚值看跌IV之差。>0看涨更贵=押注向上，<0看跌更贵=押注向下。真金白银的方向押注。",
        "**Vol PCR（成交量认沽认购比）**：看跌期权成交量/看涨期权成交量。>1看跌情绪重，<1看涨情绪重",
        "**Net GEX（做市商净Gamma敞口）**：做市商持有的期权净Gamma。正Gamma→做市商反向操作（涨卖跌买）抑制波动；负Gamma→做市商同向操作（涨买跌卖）放大波动",
        "**Flip Point（翻转点位）**：净GEX=0的点位，即做市商行为模式切换的临界位置",
        "**年化基差率**：期货相对现货的年化溢价/折价。高升水（正）=看多情绪，高贴水（负）=看空情绪",
        "**EV（预期收益）**：Breeden-Litzenberger方法反推风险中性分布，计算公允价与市价差。正值代表期权被低估，买入有正期望收益；负值代表被高估。Call+Put等权重综合。",
        "**使用建议**：SKEW+PCR+IV Skew三指标共振时信号最强；GEX负且指数接近flip point时警惕波动率放大；EV与偏度方向相反时可能是定价错误机会",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare期权数据 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 4. 风格漂移监控 ----------
def build_style_drift_card(date_str, summary, data_warnings=None, omo_data=None, ust_data=None):
    """风格漂移监控卡片
    
    summary: dict with z_opt, zone, label, advice, deep_analysis, etc.
    data_warnings: list of warning strings，数据缺失告警
    omo_data: 央行OMO操作数据（可选）
    """
    z_opt = summary.get('z_opt')
    label = summary.get('label', '')
    data_warnings = data_warnings or []
    has_data_missing = len(data_warnings) > 0

    # 判断模板颜色
    if '警戒' in label or z_opt is not None and z_opt > 1:
        template = 'red'
    elif '预警' in label or z_opt is not None and z_opt > 0.5:
        template = 'yellow'
    elif has_data_missing:
        template = 'yellow'
    else:
        template = 'green'
    
    title_prefix = "⚠️ " if has_data_missing else ""
    header = build_card_header(f"{title_prefix}📈 A股风格漂移监控（期权版）| {date_str}", template=template)
    
    elements = []

    # 数据缺失告警（放在最前面）
    if data_warnings:
        elements.append(build_note("\\n".join(data_warnings), tag="warning"))
    
    # 核心指标
    z_str = f"{z_opt:+.3f}" if z_opt is not None else "N/A"
    elements.append(build_markdown(f"**z_opt = {z_str}** → {summary.get('zone', '')} **{label}**\n**操作建议**: {summary.get('advice', '')}"))
    if summary.get('detail'):
        elements.append(build_markdown(f"> {summary['detail']}"))
    
    elements.append(build_hr())
    
    # 信号分解表
    deep = summary.get('deep_analysis', {})
    iv_z = summary.get('growth_iv_z') or 0
    skew_z = summary.get('growth_skew_z') or 0
    pcr_z = summary.get('growth_pcr_z') or 0
    iv_pct = deep.get('iv_pct')
    skew_pct = deep.get('skew_pct')
    pcr_pct = deep.get('pcr_pct')
    
    signal_rows = [
        ['成长IV', f"{iv_z:+.3f}", '0.5', f"{0.5*iv_z:+.3f}", f"{iv_pct:.0f}%" if iv_pct else '-'],
        ['成长Skew', f"{skew_z:+.3f}", '0.3', f"{0.3*skew_z:+.3f}", f"{skew_pct:.0f}%" if skew_pct else '-'],
        ['成长PCR', f"{pcr_z:+.3f}", '0.2', f"{0.2*pcr_z:+.3f}", f"{pcr_pct:.0f}%" if pcr_pct else '-'],
        ['**合计**', '', '', f'**{z_str}**', ''],
    ]
    elements.append(build_data_table(
        ['维度', 'z-score', '权重', '贡献', '占比'],
        signal_rows,
        title="🔍 信号分解"
    ))
    
    elements.append(build_hr())
    
    # 深度解读
    elements.append(build_section_header("📊 深度解读"))
    deep_lines = []
    
    # 趋势方向
    t5d = deep.get('trend_5d')
    t20d = deep.get('trend_20d')
    trend_parts = []
    if t5d is not None:
        arrow = "↑" if t5d > 0.05 else ("↓" if t5d < -0.05 else "→")
        trend_parts.append(f"5日{arrow}{abs(t5d):.3f}")
    if t20d is not None:
        arrow = "↑" if t20d > 0.1 else ("↓" if t20d < -0.1 else "→")
        trend_parts.append(f"20日{arrow}{abs(t20d):.3f}")
    if trend_parts:
        deep_lines.append(f"• 趋势方向：{', '.join(trend_parts)}")
    
    # 信号持续性
    cdays = deep.get('consecutive_warning_days', 0)
    if cdays > 0:
        deep_lines.append(f"• 信号持续性：已连续**{cdays}天**处于预警/警戒区")
        if cdays >= 10:
            deep_lines.append(f"  ⚠️ 连续预警超过10天，风格漂移可能已形成趋势")
    else:
        deep_lines.append("• 信号持续性：当前不在预警区")
    
    # 主驱动力
    if iv_pct is not None:
        driver_name = "IV" if iv_pct >= max(skew_pct or 0, pcr_pct or 0) else ("Skew" if (skew_pct or 0) >= (pcr_pct or 0) else "PCR")
        deep_lines.append(f"• 主驱动力：{driver_name}（IV={iv_pct:.0f}% Skew={skew_pct:.0f}% PCR={pcr_pct:.0f}%）")
    
    # 单标的驱动
    single = deep.get('single_driver')
    diff = deep.get('single_driver_diff')
    if single:
        deep_lines.append(f"• 🔍 单标的驱动：主要由{single}驱动（IV z差异={diff:+.3f}），可仅减仓{single}")
    elif diff is not None and abs(diff) > 0.5:
        deep_lines.append(f"• 科创50与创业板IV z差异={diff:+.3f}，暂未达单标的驱动阈值(>1.0)")
    
    elements.append(build_markdown("\n".join(deep_lines)))
    
    # 央行OMO操作
    if omo_data:
        elements.append(build_hr())
        omo_lines = ["🏦 **央行OMO操作** (" + omo_data.get('date', '') + ")"]
        ops = omo_data.get('operations', [])
        if ops:
            op_strs = []
            for op in ops:
                term = "隔夜" if op.get('term') == 0 else f"{op.get('term', '?')}天"
                amt = op.get('amount', 0)
                rate = op.get('rate')
                rate_str = f" @ {rate}%" if rate else ""
                op_strs.append(f"{term}逆回购 **{amt:,}亿**{rate_str}")
            omo_lines.append("操作: " + " / ".join(op_strs))
        
        today_inj = omo_data.get('today_injection')
        today_mat = omo_data.get('today_maturity')
        if today_inj is not None and today_mat is not None:
            omo_lines.append(f"今日操作 **{today_inj:,}亿** ／ 到期 **{today_mat:,}亿**")
        
        net_inj = omo_data.get('today_net')
        if net_inj is not None:
            if net_inj > 0:
                net_tag = f"🟢 净投放 {net_inj:+,}亿"
            elif net_inj < 0:
                net_tag = f"🔴 净回笼 {abs(net_inj):,}亿"
            else:
                net_tag = "⚪ 零投放"
            omo_lines.append(net_tag)
        
        balance = omo_data.get('reverse_repo_balance')
        if balance is not None:
            omo_lines.append(f"逆回购余额: **{balance:,}亿**")
        
        elements.append(build_markdown("\n".join(omo_lines)))

    # 美债利率-流动性传导监控
    if ust_data:
        elements.append(build_hr())
        ust_lines = ["🔬 **美债利率-流动性传导监控**"]
        
        ps = ust_data.get('price_signals', {})
        spreads = ust_data.get('spread_signals', {})
        liq = ust_data.get('liquidity_signals', {})
        judgment = ust_data.get('judgment', '未知')
        reason = ust_data.get('judgment_reason', '')
        
        # 价格信号
        price_rows = []
        t30 = ps.get('30Y')
        t30_chg = ps.get('30Y_chg5d')
        if t30 is not None:
            chg_str = f"{t30_chg:+.1f}bp" if t30_chg is not None else "-"
            price_rows.append(f"30Y超长债 {t30:.2f}% ({chg_str})")
        t10 = ps.get('10Y')
        t10_chg = ps.get('10Y_chg5d')
        if t10 is not None:
            chg_str = f"{t10_chg:+.1f}bp" if t10_chg is not None else "-"
            price_rows.append(f"10Y名义 {t10:.2f}% ({chg_str})")
        dxy = ps.get('dxy')
        if dxy is not None:
            price_rows.append(f"DXY {dxy:.2f}")
        
        if price_rows:
            ust_lines.append("**📊 价格信号**: " + " | ".join(price_rows))
        
        # 利差
        spread_items = []
        s30_10 = spreads.get('30Y_10Y')
        if s30_10 is not None:
            spread_items.append(f"30Y-10Y {s30_10*100:+.0f}bp")
        s10_2 = spreads.get('10Y_2Y')
        if s10_2 is not None:
            spread_items.append(f"10Y-2Y {s10_2*100:+.0f}bp")
        bei10 = spreads.get('BEI_10Y')
        if bei10 is not None:
            spread_items.append(f"BEI10Y {bei10:.2f}%")
        if spread_items:
            ust_lines.append("**📐 利差**: " + " | ".join(spread_items))
        
        # 流动性利率
        liq_items = []
        effr = liq.get('effr')
        if effr is not None:
            liq_items.append(f"EFFR {effr:.2f}%")
        sofr = liq.get('sofr')
        if sofr is not None:
            sofr_dev = liq.get('sofr_deviation_bp', 0)
            liq_items.append(f"SOFR {sofr:.2f}%({sofr_dev:+.0f}bp)")
        sp2y_effr = liq.get('spread_2y_effr')
        if sp2y_effr is not None:
            sp2y_pct = liq.get('spread_2y_effr_pct1y')
            pct_str = f" 分位{sp2y_pct:.0f}%" if sp2y_pct is not None else ""
            liq_items.append(f"2Y-EFFR {sp2y_effr*100:+.0f}bp{pct_str}")
        if liq_items:
            ust_lines.append("**💧 流动性**: " + " | ".join(liq_items))
        
        # 传导判定
        if '疑似' in judgment:
            judge_emoji = "⚠️"
        elif '成立' in judgment and '未' not in judgment:
            judge_emoji = "🔴"
        else:
            judge_emoji = "✅"
        ust_lines.append(f"{judge_emoji} **传导判定**: {judgment}")
        if reason:
            ust_lines.append(f"   {reason}")
        
        # 下一步检查项
        next_steps = ust_data.get('next_steps', [])
        if next_steps:
            ust_lines.append("\n**🔍 需人工确认**:")
            for ns in next_steps[:3]:
                ust_lines.append(f"  {ns}")
        
        elements.append(build_markdown("\n".join(ust_lines)))

    # 指标说明
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**z_opt（综合风格漂移z值）**：基于期权数据计算的成长-价值风格偏离度。>0.5预警、>1.0警戒、>1.5极端漂移",
        "**成长IV（成长股隐含波动率z值）**：科创50+创业板ETF期权隐含波动率的偏离程度，是风格漂移的核心驱动因子（权重50%）",
        "**成长Skew（成长股波动率偏度z值）**：成长股看涨/看跌期权IV差的偏离，反映期权市场对成长方向的押注（权重30%）",
        "**成长PCR（成长股期权成交量认沽认购比z值）**：成长股期权实际成交量的多空偏向，反映真实交易情绪（权重20%）",
        "**信号持续性**：连续处于预警区的天数，>10天说明风格漂移已形成趋势而非短期扰动",
        "**单标的驱动**：主要由科创50或创业板单一指数驱动时，可针对性减仓该指数而非全面减仓成长股",
        "**使用建议**：z_opt>1时降低成长股仓位；信号由单标的驱动时精准减仓对应指数；连续预警10天以上注意风格切换",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare ETF期权数据 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 5. 成交量分位 ----------
def build_volume_percentile_card(date_str, result):
    """成交量分位卡片
    
    【指标业务含义】
    成交额分位数反映当前市场活跃度在历史中的位置，是判断市场情绪和趋势强度的核心指标：
    - 95%+ 极度放量：情绪亢奋，全民炒股，短期见顶风险极高，建议减仓止盈
    - 80-95% 放量：活跃度高，趋势延续性强，但需警惕板块分化，可顺势而为
    - 40-60% 正常：量能均衡，多空相对平衡，按正常策略操作即可
    - 20%以下 缩量：情绪低迷，交投清淡，地量之后可能见地价，但也可能继续阴跌
    交易参考：放量上涨是健康趋势，放量下跌是恐慌信号，缩量上涨需警惕假突破，缩量下跌可能是见底信号
    
    result: dict with today_amount, percentile, high_1y, low_1y, tag, top2000_fallback
    """
    tag = result.get('tag', '')
    
    # 判断颜色
    if '极度放量' in tag or '缩量预警' in tag:
        template = 'red'
    elif '放量预警' in tag or '偏高' in tag or '偏低' in tag:
        template = 'yellow'
    else:
        template = 'green'
    
    header = build_card_header(f"📊 全A成交额分位数 | {date_str}", template=template)
    
    elements = []
    
    # 核心指标
    fallback_note = " (Top2000估算)" if result.get('top2000_fallback') else ""
    metrics = [
        {"label": "今日成交额", "value": f"{result.get('today_amount', 0)}亿", "status": tag},
    ]
    if result.get('percentile') is not None:
        metrics.append({"label": "1年分位数", "value": f"{result['percentile']}%"})
    
    elements.append(build_metric_row(metrics))
    
    elements.append(build_hr())
    
    # 详细数据
    detail_rows = []
    detail_rows.append(['今日成交额', f"{result.get('today_amount', 0)}亿"])
    if result.get('percentile') is not None:
        detail_rows.append(['1年分位数', f"{result['percentile']}%"])
    if result.get('high_1y'):
        detail_rows.append(['1年最高', f"{result['high_1y']}亿"])
    if result.get('low_1y'):
        detail_rows.append(['1年最低', f"{result['low_1y']}亿"])
    detail_rows.append(['状态', result.get('tag', '')])
    if result.get('top2000_fallback'):
        detail_rows.append(['备注', 'Top2000估算值（双指数合并失败）'])
    
    elements.append(build_key_value_table(detail_rows))
    
    # 业务含义解释
    elements.append(build_hr())
    pct = result.get('percentile')
    if pct is not None:
        if pct >= 95:
            meaning = "🔴 **极度放量**：市场情绪极度亢奋，全民炒股氛围浓厚，短期见顶风险极高。建议减仓止盈，警惕高位接盘。"
        elif pct >= 80:
            meaning = "🟡 **放量**：市场活跃度高，趋势延续性较强，但板块分化可能加剧。可顺势而为，注意热点轮动节奏。"
        elif pct >= 60:
            meaning = "🟢 **偏活跃**：量能偏高水平，市场情绪积极。可正常操作，关注成交量配合的突破机会。"
        elif pct >= 40:
            meaning = "🟢 **正常**：量能处于均衡区间，多空力量相对平衡。按既定策略正常操作即可。"
        elif pct >= 20:
            meaning = "🟡 **偏低**：成交量萎缩，市场观望情绪较浓。轻仓参与，等待方向选择。"
        else:
            meaning = "🔴 **缩量预警**：交投极度清淡，市场情绪低迷。地量之后可能见地价，但也可能继续阴跌，需结合其他信号判断。"
    else:
        meaning = "分位数数据暂缺。"
    
    elements.append(build_markdown(f"**💡 业务含义解读**\n{meaning}"))
    
    # 指标说明（折叠面板）
    elements.append(build_collapsible("📖 指标说明 & 交易参考", [
        "**成交额分位数**：今日成交额在过去1年所有交易日中的排名分位，越高代表市场越活跃",
        "**95%+ 极度放量**：情绪亢奋→短期见顶风险高→建议减仓止盈",
        "**80-95% 放量**：活跃度高→趋势延续性强→顺势操作但注意分化",
        "**60-80% 偏活跃**：量能偏强→情绪积极→正常操作关注突破",
        "**40-60% 正常**：量能均衡→多空平衡→按既定策略操作",
        "**20-40% 偏低**：成交萎缩→观望情绪→轻仓参与等方向",
        "**20%以下 缩量**：情绪低迷→地量见地价OR继续阴跌→结合其他信号判断",
        "**量价配合口诀**：放量上涨健康，放量下跌恐慌，缩量上涨警惕假突破，缩量下跌可能见底",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare（上证+深证综指合并≈全A）{fallback_note} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"))
    
    return wrap_card(header, elements)


# ---------- 6. 环流日报 ----------
def build_circulation_daily_card(date_str, score, rating, risks, positives, key_metrics=None, omo_data=None, tga_data=None, ust_data=None, bs_data=None, swap_data=None, liq_data=None, alerts_data=None, fp_data=None):
    """环流日报卡片
    
    score: 综合评分 (1-10)
    rating: 评级描述
    risks: list of risk strings
    positives: list of positive strings
    key_metrics: optional dict of key metrics
    ust_data: optional dict of UST liquidity monitor data
    bs_data: optional dict of Fed balance sheet data (含国债期限结构)
    swap_data: optional dict of swap rate data (年化掉期率)
    liq_data: optional dict 央行资金面判定（扰动 vs 真紧）
    fp_data: optional dict 美元流动性指纹侦查（A-F 归因）
    alerts_data: optional AlertBook 统一告警汇总（警告=普通 / 报警=高级）
    """
    # 判断颜色
    if '紧张' in rating or score <= 3:
        template = 'red'
    elif '偏紧' in rating or score <= 5:
        template = 'yellow'
    else:
        template = 'green'

    # 有报警时标题标红提示
    _has_alert = False
    if alerts_data is not None:
        try:
            _has_alert = bool(alerts_data.counts().get('alert'))
        except Exception:
            _has_alert = False
    if _has_alert:
        template = 'red'

    header = build_card_header(f"🌊 环流日报 | {date_str}", template=template)
    
    elements = []

    # 告警汇总（警告=普通 / 报警=高级）—— 放在最醒目位置
    if alerts_data is not None:
        try:
            elements.append(build_markdown("\n".join(alerts_data.render_card_lines())))
            elements.append(build_hr())
        except Exception as _ae:
            elements.append(build_markdown(f"⚠️ 告警汇总渲染失败: {_ae}"))
    
    # 综合评级
    elements.append(build_markdown(f"## 综合评级: {rating} | **评分 {score}/10**"))
    
    elements.append(build_hr())
    
    # 风险点
    if risks:
        risk_lines = ["⚠️ **风险点**:"]
        for r in risks:
            risk_lines.append(f"- {r}")
        elements.append(build_markdown("\n".join(risk_lines)))
    
    # 积极因素
    if positives:
        pos_lines = ["✅ **积极因素**:"]
        for p in positives:
            pos_lines.append(f"- {p}")
        elements.append(build_markdown("\n".join(pos_lines)))
    
    # 关键指标
    if key_metrics:
        elements.append(build_hr())
        metric_rows = list(key_metrics.items())
        elements.append(build_key_value_table(metric_rows, title="📊 关键指标"))

    # 央行资金面判定（扰动 vs 真紧）—— 纯Python渲染
    if liq_data:
        elements.append(build_hr())
        try:
            import sys as _sys
            if (__import__('os').environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts/modules' not in _sys.path:
                _sys.path.insert(0, (__import__('os').environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts/modules')
            from pboc_liquidity_reader import render_card_lines
            elements.append(build_markdown("\n".join(render_card_lines(liq_data))))
        except Exception as _e:
            elements.append(build_markdown(f"⚠️ 央行资金面判定渲染失败: {_e}"))

    # 美元流动性指纹侦查（A-F 归因）—— 纯Python渲染
    if fp_data:
        elements.append(build_hr())
        try:
            import sys as _sys
            if (__import__('os').environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts/modules' not in _sys.path:
                _sys.path.insert(0, (__import__('os').environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts/modules')
            from usd_fingerprint import render_card_lines as _fp_cards
            elements.append(build_markdown("\n".join(_fp_cards(fp_data))))
        except Exception as _e:
            elements.append(build_markdown(f"⚠️ 指纹侦查渲染失败: {_e}"))

    # TGA 日度变动
    if tga_data:
        elements.append(build_hr())
        tga_date = tga_data.get('date', '')
        closing = tga_data.get('closing')
        net_chg = tga_data.get('net_change')
        deposits = tga_data.get('deposits')
        withdrawals = tga_data.get('withdrawals')
        debt_issues = tga_data.get('debt_issues', 0)
        debt_redemptions = tga_data.get('debt_redemptions', 0)
        debt_net = tga_data.get('debt_net', 0)
        
        tga_lines = [f"🏛️ **TGA 日度变动** ({tga_date})"]
        
        if closing is not None:
            tga_lines.append(f"TGA余额: **${closing/1e3:.0f}B**")
        
        if net_chg is not None:
            if net_chg > 0:
                net_label = f"🟢 净增加 ${net_chg/1e3:+.0f}B"
            elif net_chg < 0:
                net_label = f"🔴 净减少 ${net_chg/1e3:+.0f}B"
            else:
                net_label = "⚪ 持平"
            tga_lines.append(net_label)
        
        if deposits is not None and withdrawals is not None:
            tga_lines.append(f"存入 **${deposits/1e3:.0f}B** ／ 取出 **${withdrawals/1e3:.0f}B**")
        
        # 发债 vs 赎回
        if debt_issues or debt_redemptions:
            if debt_net > 0:
                debt_label = f"🟢 净发债 ${debt_net/1e3:.0f}B"
            elif debt_net < 0:
                debt_label = f"🔴 净赎回 ${abs(debt_net)/1e3:.0f}B"
            else:
                debt_label = "⚪ 持平"
            tga_lines.append(f"发债 ${debt_issues/1e3:.0f}B ／ 到期 ${debt_redemptions/1e3:.0f}B | {debt_label}")
        
        # 主要支出 Top 3
        top_wd = tga_data.get('top_withdrawals', [])
        wd_non_debt = [(c,a) for c,a in top_wd if 'Public Debt' not in c][:3]
        if wd_non_debt:
            tga_lines.append("\n📤 主要支出:")
            for catg, amt in wd_non_debt:
                short = catg.replace('Dept of ', '').replace('Department of ', '')
                tga_lines.append(f"  • {short}: ${amt/1e3:.1f}B")
        
        elements.append(build_markdown("\n".join(tga_lines)))

    # 美债利率-流动性传导监控
    if ust_data:
        elements.append(build_hr())
        ust_lines = ["🔬 **美债利率-流动性传导监控**"]
        
        ps = ust_data.get('price_signals', {})
        spreads = ust_data.get('spread_signals', {})
        liq = ust_data.get('liquidity_signals', {})
        judgment = ust_data.get('judgment', '未知')
        reason = ust_data.get('judgment_reason', '')
        
        # 价格信号
        price_rows = []
        t30 = ps.get('30Y')
        t30_chg = ps.get('30Y_chg5d')
        if t30 is not None:
            chg_str = f"{t30_chg:+.1f}bp" if t30_chg is not None else "-"
            price_rows.append(f"30Y超长债 {t30:.2f}% ({chg_str})")
        t10 = ps.get('10Y')
        t10_chg = ps.get('10Y_chg5d')
        if t10 is not None:
            chg_str = f"{t10_chg:+.1f}bp" if t10_chg is not None else "-"
            price_rows.append(f"10Y名义 {t10:.2f}% ({chg_str})")
        dxy = ps.get('dxy')
        if dxy is not None:
            price_rows.append(f"DXY {dxy:.2f}")
        
        if price_rows:
            ust_lines.append("**📊 价格信号**: " + " | ".join(price_rows))
        
        # 利差
        spread_items = []
        s30_10 = spreads.get('30Y_10Y')
        if s30_10 is not None:
            spread_items.append(f"30Y-10Y {s30_10*100:+.0f}bp")
        s10_2 = spreads.get('10Y_2Y')
        if s10_2 is not None:
            spread_items.append(f"10Y-2Y {s10_2*100:+.0f}bp")
        bei10 = spreads.get('BEI_10Y')
        if bei10 is not None:
            spread_items.append(f"BEI10Y {bei10:.2f}%")
        if spread_items:
            ust_lines.append("**📐 利差**: " + " | ".join(spread_items))
        
        # 流动性利率
        liq_items = []
        effr = liq.get('effr')
        if effr is not None:
            liq_items.append(f"EFFR {effr:.2f}%")
        sofr = liq.get('sofr')
        if sofr is not None:
            sofr_dev = liq.get('sofr_deviation_bp', 0)
            liq_items.append(f"SOFR {sofr:.2f}%({sofr_dev:+.0f}bp)")
        sp2y_effr = liq.get('spread_2y_effr')
        if sp2y_effr is not None:
            sp2y_pct = liq.get('spread_2y_effr_pct1y')
            pct_str = f" 分位{sp2y_pct:.0f}%" if sp2y_pct is not None else ""
            liq_items.append(f"2Y-EFFR {sp2y_effr*100:+.0f}bp{pct_str}")
        if liq_items:
            ust_lines.append("**💧 流动性**: " + " | ".join(liq_items))
        
        # 传导判定
        if '疑似' in judgment:
            judge_emoji = "⚠️"
        elif '成立' in judgment and '未' not in judgment:
            judge_emoji = "🔴"
        else:
            judge_emoji = "✅"
        ust_lines.append(f"{judge_emoji} **传导判定**: {judgment}")
        if reason:
            ust_lines.append(f"   {reason}")
        
        # 下一步检查项
        next_steps = ust_data.get('next_steps', [])
        if next_steps:
            ust_lines.append("\n**🔍 需人工确认**:")
            for ns in next_steps[:3]:
                ust_lines.append(f"  {ns}")
        
        elements.append(build_markdown("\n".join(ust_lines)))

    # 掉期点 (年化掉期率)
    if swap_data and swap_data.get('annualized_pct') is not None:
        elements.append(build_hr())
        sw = swap_data
        ann = sw['annualized_pct']
        pips = sw.get('pips_1y')
        spot = sw.get('spot')
        wk_chg = sw.get('wk_chg')
        mo_chg = sw.get('mo_chg')
        pct_1y = sw.get('pct_1y')
        source = sw.get('source', '')
        
        swap_lines = ["💱 **1Y USD/CNY 掉期（年化率）**"]
        
        # 主数据行
        sign = "+" if ann > 0 else ""
        abs_ann = abs(ann)
        if abs_ann >= 3.0:
            tag = "🔴"
        elif abs_ann >= 2.5:
            tag = "🟡"
        else:
            tag = "🟢"
        
        main_line = f"{tag} **{sign}{ann:.2f}%**"
        if wk_chg is not None:
            s = "↑" if wk_chg > 0 else "↓"
            main_line += f"  周{s}{abs(wk_chg):.2f}pp"
        if mo_chg is not None:
            s = "↑" if mo_chg > 0 else "↓"
            main_line += f"  月{s}{abs(mo_chg):.2f}pp"
        swap_lines.append(main_line)
        
        # 副信息行
        detail_parts = []
        if pips is not None:
            detail_parts.append(f"掉期点 {pips:+.0f}pips")
        if spot:
            detail_parts.append(f"即期 {spot}")
        if pct_1y is not None:
            detail_parts.append(f"近1年分位 {pct_1y:.0f}%")
        if detail_parts:
            swap_lines.append("   | ".join(detail_parts))
        
        if source:
            swap_lines.append(f"   _数据源: {source}_")
        
        elements.append(build_markdown("\n".join(swap_lines)))
        
        # 利率平价验证
        if sw.get('cip_valid'):
            elements.append(build_hr())
            cip_cd = sw.get('cip_cn_1y_cd')
            cip_us2y = sw.get('cip_us_2y')
            cip_synth = sw.get('cip_synthetic')
            cip_sp = sw.get('cip_spread')
            
            cip_lines = ["📊 **利率平价验证**"]
            
            if cip_sp is not None:
                if abs(cip_sp) > 0.3:
                    cip_tag = "⚠️偏离较大"
                elif abs(cip_sp) > 0.15:
                    cip_tag = "小幅偏离"
                else:
                    cip_tag = "基本吻合"
                cip_lines.append(f"合成利率 **{cip_synth:.2f}%** vs 2Y美债 **{cip_us2y:.2f}%**  →  利差 **{cip_sp:+.2f}%**  {cip_tag}")
            else:
                cip_lines.append("数据不足")
            
            cip_lines.append(f"   1Y存单 {cip_cd:.2f}% + 掉期贴水 {abs(ann):.2f}% = 合成美元利率")
            elements.append(build_markdown("\n".join(cip_lines)))
    
    # 美联储国债期限结构 (短长债比例)
    if bs_data and bs_data.get('treasury_short_long_ratio') is not None:
        elements.append(build_hr())
        bills = bs_data.get('treasury_bills')
        long_b = bs_data.get('treasury_long_bonds')
        sl_ratio = bs_data.get('treasury_short_long_ratio')
        slr_wk = bs_data.get('treasury_slr_wk_chg')
        bills_wk = bs_data.get('treasury_bills_wk_chg')
        long_wk = bs_data.get('treasury_long_bonds_wk_chg')
        
        bs_lines = ["🏛️ **美联储国债期限结构**"]
        
        # 短长债比例
        ratio_line = f"**短/长债比例**: {sl_ratio:.2f}%"
        if slr_wk is not None:
            sign = "↑" if slr_wk > 0 else "↓"
            ratio_line += f"  周{sign}{abs(slr_wk):.2f}pp"
        bs_lines.append(ratio_line)
        
        # 分项数据
        detail_parts = []
        if bills is not None:
            wk_str = ""
            if bills_wk is not None:
                s = "+" if bills_wk > 0 else ""
                wk_str = f"({s}{bills_wk/1e3:.1f}B)"
            detail_parts.append(f"短债 ${bills/1e3:.0f}B{wk_str}")
        if long_b is not None:
            wk_str = ""
            if long_wk is not None:
                s = "+" if long_wk > 0 else ""
                wk_str = f"({s}{long_wk/1e3:.1f}B)"
            detail_parts.append(f"长债 ${long_b/1e3:.0f}B{wk_str}")
        if detail_parts:
            bs_lines.append("   | ".join(detail_parts))
        
        bs_lines.append(f"   _H.4.1 周度更新 · 短债=Bills(≤1Y) · 长债=Notes+Bonds+TIPS(>1Y)_")
        
        elements.append(build_markdown("\n".join(bs_lines)))

    # 指标说明
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**综合评分（1-10分）**：综合衡量全球美元流动性对A股的影响程度。8-10分极宽松，6-7分偏松，4-5分中性，2-3分偏紧，0-1分紧张",
        "**风险点**：可能导致流动性收紧或市场承压的因素（美联储加息、资金外流、汇率贬值等）",
        "**积极因素**：有利于流动性宽松或市场向好的因素（降息、降准、外资流入等）",
        "**环流视角**：从全球美元环流角度分析流动性，而非单一国内视角，关注美联储政策、中美利差、汇率、外资流向等跨境因素",
        "**使用建议**：评分高时可提高仓位进攻，评分低时降低仓位防守；关注风险点和积极因素的边际变化比绝对值更重要",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare + AkShare + Fed H.4.1 + TradingEconomics | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 7. 多策略综合选股 ----------
def build_multi_strategy_card(date_str, strategies_data, market_advice=None,
                              resonance_industries=None):
    """四选股器综合选股卡片

    strategies_data: list of dict with: strategy_name, stock_count, top_stocks (list of (name, month_pct, year_pct))
    market_advice: optional market position advice
    resonance_industries: optional list of (行业, 加权次数, 股票数) — 共振行业，显示在概览后

    兼容旧版 top_stocks (list of (name, pct)) — 此时年内涨幅列显示为'-'
    """
    total_stocks = sum(s.get('stock_count', 0) for s in strategies_data)
    
    if total_stocks > 50:
        template = 'green'
    elif total_stocks > 20:
        template = 'blue'
    else:
        template = 'yellow'
    
    header = build_card_header(f"🎯 四选股器综合选股 | {date_str}", template=template)
    
    elements = []
    
    # 总体概览
    strategy_counts = []
    for s in strategies_data:
        strategy_counts.append(f"{s.get('strategy_name', '')}: **{s.get('stock_count', 0)}只**")
    elements.append(build_markdown(" | ".join(strategy_counts)))

    # 共振行业（置顶，紧跟概览）
    if resonance_industries:
        lines = ["**🏭 共振行业（加权）**"]
        for ind, w, ns in resonance_industries[:8]:
            lines.append(f"　{ind}：加权 **{w}** ｜ {ns}只")
        elements.append(build_markdown("\n".join(lines)))
    
    if market_advice:
        elements.append(build_markdown(f"\n💡 **仓位建议**: {market_advice}"))
    
    elements.append(build_hr())
    
    # 各策略Top股票
    for s in strategies_data:
        name = s.get('strategy_name', '')
        top_stocks = s.get('top_stocks', [])
        if top_stocks:
            # 兼容旧版二元组 (name, pct) 和新版三元组 (name, month_pct, year_pct)
            def _fmt_year(stk):
                if len(stk) >= 3 and stk[2] is not None:
                    return f"{stk[2]:+.2f}%"
                return '-'
            def _stock_name(stk):
                """股票名称加跌幅警告"""
                year_pct = stk[2] if len(stk) >= 3 else None
                return _warn_stock_name(stk[0], year_pct)
            stock_rows = [[i+1, _stock_name(stk), f"{stk[1]:+.2f}%", _fmt_year(stk)] for i, stk in enumerate(top_stocks[:10])]
            elements.append(build_data_table(
                ['排名', '股票名称', '月涨幅', '年内涨幅'],
                stock_rows,
                title=f"📈 {name} Top{min(10, len(top_stocks))}"
            ))
            elements.append(build_hr())
    
    # 指标说明
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**多策略选股**：融合多种选股逻辑（成长股、缠论多头、强势股、价值股等）的综合选股结果，降低单一策略的失效风险",
        "**策略含义**：各策略从不同维度筛选优质标的——成长股看业绩增速、缠论看技术形态、强势股看资金强度",
        "**月涨幅**：本月以来涨跌幅，反映短期动量强度",
        "**年内涨幅**：年初至今涨跌幅，反映中期趋势强度",
        "**多策略共振**：某只股票被多个策略同时选中时可靠性更高，建议优先关注",
        "**仓位建议**：基于当前市场环境给出的参考仓位，需结合个人风险承受能力调整",
        "**使用建议**：不要只看Top涨幅榜，建议结合行业分布、估值水平、基本面综合判断；多策略共振标的优先关注",
    ]))
    
    # 底部
    elements.append(build_note(f"数据来源: Tushare/AkShare | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 8. 成长股选股 ----------
def build_growth_stock_card(date_str, stocks, total_count=0, filters_info=None, omo_data=None):
    """成长股选股卡片
    
    stocks: list of dict with name, code, revenue_growth, profit_growth, ytd_return, pe
    total_count: total filtered stocks count
    filters_info: dict of filter conditions
    omo_data: 央行OMO操作数据（可选）
    """
    if total_count > 30:
        template = 'green'
    elif total_count > 10:
        template = 'blue'
    else:
        template = 'yellow'
    
    header = build_card_header(f"📈 成长股筛选 | {date_str}", template=template)
    
    elements = []
    
    # 总体概览
    elements.append(build_markdown(f"**筛选结果**: {total_count}只成长股满足条件"))
    
    if filters_info:
        filter_lines = []
        for k, v in filters_info.items():
            filter_lines.append(f"- {k}: {v}")
        elements.append(build_markdown("\n".join(filter_lines)))
    
    elements.append(build_hr())
    
    # Top20表格
    if stocks:
        table_rows = []
        for i, s in enumerate(stocks[:20]):
            ytd = s.get('ytd_return')
            name = _warn_stock_name(s.get('name', ''), ytd)
            table_rows.append([
                i + 1,
                name,
                s.get('code', ''),
                f"{s.get('revenue_growth', 0):.1f}%" if s.get('revenue_growth') is not None else 'N/A',
                f"{s.get('profit_growth', 0):.1f}%" if s.get('profit_growth') is not None else 'N/A',
                f"{ytd:+.2f}%" if ytd is not None else 'N/A',
                f"{s.get('pe', 0):.1f}" if s.get('pe') is not None else 'N/A',
            ])
        elements.append(build_data_table(
            ['#', '名称', '代码', '营收增速', '净利润增速', '年内涨幅', 'PE'],
            table_rows,
            title=f"🏆 Top{min(20, len(stocks))}成长股"
        ))
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**营收增速**：营业收入同比增长率，反映公司业务扩张速度。高增长是成长股的核心特征",
        "**净利润增速**：净利润同比增长率，反映盈利能力提升。营收+利润双高增长质量最佳",
        "**PE（市盈率）**：股价/每股收益，反映估值水平。成长股通常PE较高，需结合增速判断PEG（PE/增速）是否合理",
        "**年内涨幅**：年初至今涨跌幅，反映市场对该股的认可程度。涨幅过大需警惕回调风险",
        "**筛选逻辑**：通过营收增速、净利润增速、ROE等多维度筛选出高成长标的，并按综合质量排序",
        "**使用建议**：优先关注营收+利润双高增长的标的；PEG<1（PE/增速<1）估值相对合理；避开单一季度高增长不可持续的标的",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 9. 缠论多头排列 ----------
def build_chanlun_card(date_str, stocks, total_count=0, details=None, omo_data=None):
    """缠论多头排列卡片
    
    stocks: list of dict with name, code, ytd_return, industry
    total_count: total count
    details: optional extra info
    omo_data: 央行OMO操作数据（可选）
    """
    if total_count > 200:
        template = 'green'
    elif total_count > 50:
        template = 'blue'
    else:
        template = 'yellow'
    
    header = build_card_header(f"📐 缠论多头排列筛选 | {date_str}", template=template)
    
    elements = []
    
    elements.append(build_markdown(f"**筛选结果**: {total_count}只满足多头排列条件"))
    
    if details:
        detail_lines = []
        for k, v in details.items():
            detail_lines.append(f"- {k}: {v}")
        elements.append(build_markdown("\n".join(detail_lines)))
    
    elements.append(build_hr())
    
    # Top20表格
    if stocks:
        table_rows = []
        for i, s in enumerate(stocks[:20]):
            ytd = s.get('ytd_return')
            name = _warn_stock_name(s.get('name', ''), ytd)
            table_rows.append([
                i + 1,
                name,
                s.get('code', ''),
                f"{ytd:+.2f}%" if ytd is not None else 'N/A',
                s.get('industry', '-'),
            ])
        elements.append(build_data_table(
            ['#', '名称', '代码', '年内涨幅', '行业'],
            table_rows,
            title=f"🏆 Top{min(20, len(stocks))}"
        ))
    
    # 央行OMO操作
    if omo_data:
        elements.append(build_hr())
        omo_lines = ["🏦 **央行OMO操作** (" + omo_data.get('date', '') + ")"]
        ops = omo_data.get('operations', [])
        if ops:
            op_strs = []
            for op in ops:
                term = "隔夜" if op.get('term') == 0 else f"{op.get('term', '?')}天"
                amt = op.get('amount', 0)
                rate = op.get('rate')
                rate_str = f" @ {rate}%" if rate else ""
                op_strs.append(f"{term}逆回购 **{amt:,}亿**{rate_str}")
            omo_lines.append("操作: " + " / ".join(op_strs))
        
        today_inj = omo_data.get('today_injection')
        today_mat = omo_data.get('today_maturity')
        if today_inj is not None and today_mat is not None:
            omo_lines.append(f"今日操作 **{today_inj:,}亿** ／ 到期 **{today_mat:,}亿**")
        
        net_inj = omo_data.get('today_net')
        if net_inj is not None:
            if net_inj > 0:
                net_tag = f"🟢 净投放 {net_inj:+,}亿"
            elif net_inj < 0:
                net_tag = f"🔴 净回笼 {abs(net_inj):,}亿"
            else:
                net_tag = "⚪ 零投放"
            omo_lines.append(net_tag)
        
        balance = omo_data.get('reverse_repo_balance')
        if balance is not None:
            omo_lines.append(f"逆回购余额: **{balance:,}亿**")
        
        elements.append(build_markdown("\n".join(omo_lines)))

    # 指标说明
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**多头排列**：短期均线在中期均线上方，中期均线在长期均线上方，代表各周期投资者一致看多，趋势向上",
        "**缠论视角**：基于缠中说禅理论，多头排列是确认上涨趋势的重要技术形态，配合成交量放大更佳",
        "**筛选数量参考**：>200只说明普涨行情、>50只有结构行情、<30只说明市场偏弱、赚钱效应差",
        "**行业分布**：某行业扎堆出现多头排列时，可能是板块行情启动信号",
        "**使用建议**：多头排列只是趋势确认的必要条件非充分条件，需结合基本面、成交量、板块逻辑综合判断；均线粘合后发散的多头排列爆发力最强",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: 新浪财经/Tushare | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 10. 板块筛选日报 ----------
def build_sector_screening_card(date_str, sectors, total_count=0, filters_info=None, omo_data=None):
    """板块筛选日报卡片
    
    sectors: list of dict with name, change_pct, hot_score, stocks_count
    total_count: total sectors count
    filters_info: filter conditions
    omo_data: 央行OMO操作数据（可选）
    """
    if not sectors or total_count == 0:
        template = 'grey'
    elif len(sectors) > 10:
        template = 'green'
    else:
        template = 'blue'
    
    header = build_card_header(f"🏭 板块热度筛选日报 | {date_str}", template=template)
    
    elements = []
    
    elements.append(build_markdown(f"**筛选结果**: {total_count}个板块满足条件"))
    
    if filters_info:
        filter_lines = []
        for k, v in filters_info.items():
            filter_lines.append(f"- {k}: {v}")
        elements.append(build_markdown("\n".join(filter_lines)))
    
    elements.append(build_hr())
    
    # Top板块表格
    if sectors:
        table_rows = []
        for i, s in enumerate(sectors[:20]):
            table_rows.append([
                i + 1,
                s.get('name', ''),
                f"{s.get('change_pct', 0):+.2f}%" if s.get('change_pct') is not None else 'N/A',
                s.get('hot_score', '-'),
                s.get('stocks_count', '-'),
            ])
        elements.append(build_data_table(
            ['#', '板块名称', '涨跌幅', '热度分', '成分股数'],
            table_rows,
            title=f"🔥 Top{min(20, len(sectors))}热门板块"
        ))
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**热度分**：综合考虑板块涨跌幅、成交量、资金流入、涨停股数等多维度计算的热度评分，越高代表越强势",
        "**涨跌幅**：板块当日平均涨跌幅，反映板块整体表现",
        "**成分股数**：板块内包含的股票数量，成分股多的大板块持续性通常更好",
        "**筛选逻辑**：通过多因子模型从全市场板块中筛选出热度高、趋势强、有资金关注的优质板块",
        "**使用建议**：Top板块中优先选择成分股数量适中（10-50只）、热度分高且持续上涨的板块；单板块连续3天以上霸榜需警惕高潮见顶；注意板块轮动节奏，追涨前排不如潜伏即将启动的后排",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: 东方财富 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 11. 市场综合监控 ----------
def build_market_monitor_card(date_str, liquidity_score, risk_score, concentration, latest_data, vix_data=None, ar_data=None, hindenburg_data=None, iv_ma_data=None, omo_data=None, ust_data=None, margin_data=None, margin_alerts=None, data_warnings=None):
    """市场综合监控卡片
    
    liquidity_score: 流动性评分 (0-60)
    risk_score: 风险偏好评分 (0-40)
    concentration: 成交额集中度
    latest_data: dict with latest market data
    vix_data: list of VIX data dicts
    ar_data: absorption ratio data
    hindenburg_data: hindenburg omen data
    iv_ma_data: list of IV moving average data dicts
    omo_data: dict with OMO operation data
    ust_data: dict with US Treasury yield & liquidity transmission data
    data_warnings: list of warning strings，数据缺失告警
    """
    data_warnings = data_warnings or []
    has_data_missing = len(data_warnings) > 0

    total_score = liquidity_score + risk_score
    if total_score >= 80:
        overall = "🟢 良好"
        template = 'green'
    elif total_score >= 60:
        overall = "🟡 中性"
        template = 'yellow'
    elif total_score >= 40:
        overall = "🟠 偏弱"
        template = 'orange'
    else:
        overall = "🔴 极弱"
        template = 'red'

    # 数据缺失时降级为黄色
    if has_data_missing and template == 'green':
        template = 'yellow'
    
    title_prefix = "⚠️ " if has_data_missing else ""
    header = build_card_header(f"{title_prefix}💧 市场流动性与风险偏好 | {date_str}", template=template)
    
    elements = []

    # 数据缺失告警（放在最前面）
    if data_warnings:
        elements.append(build_note("\\n".join(data_warnings), tag="warning"))
    
    # 综合评分
    elements.append(build_markdown(f"## 综合评分: **{total_score}/100** {overall}"))
    elements.append(build_metric_row([
        {"label": "流动性评分", "value": f"{liquidity_score}/60"},
        {"label": "风险偏好评分", "value": f"{risk_score}/40"},
        {"label": "成交额集中度", "value": f"{concentration:.1f}%"},
    ]))
    
    elements.append(build_hr())
    
    # 流动性指标
    liq_rows = []
    if latest_data:
        liq_rows.append(['SHIBOR 7天', f"{latest_data.get('rate_7d', 0):.2f}%"])
        liq_rows.append(['全A成交额', f"{latest_data.get('volume', 0):.2f}万亿"])
        liq_rows.append(['涨停/跌停', f"{latest_data.get('up_limit', 0)}/{latest_data.get('down_limit', 0)}家"])
        liq_rows.append(['北向资金', f"{latest_data.get('north_money', 0):+.2f}亿"])
    elements.append(build_key_value_table(liq_rows, title="💧 流动性指标"))
    
    # VIX分位数
    if vix_data:
        elements.append(build_hr())
        vix_rows = []
        for v in vix_data:
            vix_rows.append([
                v.get('name', ''),
                f"{v.get('vix', 0):.2f}%",
                f"{v.get('percentile', 0):.1f}%",
                v.get('alert', ''),
            ])
        elements.append(build_data_table(
            ['指数', 'VIX', '分位', '状态'],
            vix_rows,
            title="📊 指数VIX分位数（近1年）"
        ))
        
        # 波动率预警
        alerts = [v for v in vix_data if v.get('alert_type', 'normal') != 'normal']
        if alerts:
            alert_lines = ["⚠️ **波动率预警**："]
            for a in alerts:
                if a.get('alert_type') == 'high':
                    alert_lines.append(f"- {a['name']} VIX={a['vix']:.2f}% 分位={a['percentile']:.1f}% **高波预警**")
                elif a.get('alert_type') == 'low':
                    alert_lines.append(f"- {a['name']} VIX={a['vix']:.2f}% 分位={a['percentile']:.1f}% **低波预警**")
            elements.append(build_markdown("\n".join(alert_lines)))
    
    # 吸收率因子
    if ar_data:
        elements.append(build_hr())
        ar_rows = [
            ['AR', f"{ar_data.get('ar', 0):.4f}"],
            ['AR_5日均值', f"{ar_data.get('ar_5d', 0):.4f}"],
            ['DAR_long', f"{ar_data.get('dar_long', 0):.2f}"],
            ['DAR_short', f"{ar_data.get('dar_short', 0):.2f}"],
            ['PC1解释方差', f"{ar_data.get('pc1_explained', 0):.2f}%"],
            ['PC3累计方差', f"{ar_data.get('pc3_explained', 0):.2f}%"],
            ['**最终信号**', f"**{ar_data.get('signal_desc', '')}**"],
        ]
        elements.append(build_key_value_table(ar_rows, title="📊 吸收率因子 (Absorption Ratio)"))
    
    # 兴登堡指标
    if hindenburg_data:
        elements.append(build_hr())
        hind_rows = [
            ['120日新高', f"{hindenburg_data.get('new_highs_count', 0)}只 ({hindenburg_data.get('high_pct', 0):.2f}%)"],
            ['120日新低', f"{hindenburg_data.get('new_lows_count', 0)}只 ({hindenburg_data.get('low_pct', 0):.2f}%)"],
            ['上证指数', f"{hindenburg_data.get('index_close', 0):.2f}"],
            ['50日均线', '上方 ✅' if hindenburg_data.get('index_above_50ma') else '下方 ❌'],
            ['**触发状态**', f"**{hindenburg_data.get('hindenburg_desc', '')}**"],
        ]
        elements.append(build_key_value_table(hind_rows, title="⚠️ 兴登堡指标 (Hindenburg Omen)"))
    
    # 期权IV均线监控
    if iv_ma_data:
        elements.append(build_hr())
        iv_rows = []
        for iv in iv_ma_data:
            if iv.get('data_source') == 'missing' or iv.get('current_iv') is None:
                iv_rows.append([iv.get('name', ''), '数据缺失', '-', '-', '数据缺失'])
            else:
                star_str = '★' * iv.get('stars', 0) if iv.get('stars', 0) > 0 else ''
                iv_rows.append([
                    iv.get('name', ''),
                    f"{iv.get('current_iv', 0):.2f}% {star_str}",
                    f"{iv.get('ma40', 0):.2f}%",
                    f"{iv.get('ma80', 0):.2f}%",
                    iv.get('signal', ''),
                ])
        elements.append(build_data_table(
            ['标的', '当前IV', 'MA40', 'MA80', '信号'],
            iv_rows,
            title="📈 期权IV均线监控"
        ))
        # 文字版横线可视化
        vis_lines = []
        for iv in iv_ma_data:
            if iv.get('data_source') != 'missing' and iv.get('current_iv') is not None:
                cur = iv.get('current_iv', 0)
                m40 = iv.get('ma40', 0)
                m80 = iv.get('ma80', 0)
                vals = [v for v in [cur, m40, m80] if v is not None]
                if len(vals) >= 2:
                    vmin = min(vals)
                    vmax = max(vals)
                    vrange = vmax - vmin if vmax > vmin else 1.0
                    vmin_pad = vmin - vrange * 0.15
                    vmax_pad = vmax + vrange * 0.15
                    vrange_pad = vmax_pad - vmin_pad
                    def _p(v):
                        return max(0, min(100, (v - vmin_pad) / vrange_pad * 100))
                    lines = [
                        ('IV', cur, '━', iv.get('stars', 0)),
                        ('MA40', m40, '─', 0),
                        ('MA80', m80, '─', 0),
                    ]
                    lines_sorted = sorted(lines, key=lambda x: _p(x[1]), reverse=True)
                    vis_lines.append(f"**{iv.get('name', '')}**")
                    bar_len = 30
                    for label, val, char, stars in lines_sorted:
                        star = '★' * stars if stars > 0 else ''
                        vis_lines.append(f"  {label:>4} {val:>5.1f}% │{char * bar_len}│{star}")
                    vis_lines.append("")
        if vis_lines:
            elements.append(build_markdown("均线相对位置：\n" + "\n".join(vis_lines)))
    if margin_data and margin_data.get('current'):
        elements.append(build_hr())
        mc = margin_data['current']
        mchanges = margin_data.get('changes', {})
        mpercentiles = margin_data.get('percentiles', {})
        mpoints = margin_data.get('data_points', 0)

        def _fmt_p(v):
            if v is None:
                return '--'
            emoji = '🔴' if v > 0 else '🟢' if v < 0 else '⚪'
            return f"{emoji}{v:+.2f}%"

        def _t(v):
            return f"{v/1e12:.3f}万亿" if v else '--'

        rzrq = mc.get('rzrqye', 0)
        margin_lines = ["💰 **融资融券余额**"]
        margin_lines.append(f"| 指标 | 当前值 | 日变动 | 周变动 | 月变动 |")
        margin_lines.append("|------|--------|--------|--------|--------|")
        margin_lines.append(f"| 融资余额 | {_t(mc.get('rzye'))} | {_fmt_p(mchanges.get('day_pct'))} | {_fmt_p(mchanges.get('week_pct'))} | {_fmt_p(mchanges.get('month_pct'))} |")
        margin_lines.append(f"| 融券余额 | {_t(mc.get('rqye'))} | -- | -- | -- |")
        margin_lines.append(f"| **两融总余额** | **{_t(rzrq)}** | **{_fmt_p(mchanges.get('day_pct'))}** | **{_fmt_p(mchanges.get('week_pct'))}** | **{_fmt_p(mchanges.get('month_pct'))}** |")

        pct_val = mpercentiles.get('rzrqye_percentile')
        if pct_val is not None:
            pct_status = "🟠 高位警戒（>80%）" if pct_val >= 80 else "🔵 低位区域（<20%）" if pct_val <= 20 else "🟢 正常区间"
            margin_lines.append(f"\n**历史分位: {pct_val}%** {pct_status}")
            margin_lines.append(f"_统计区间: 近{mpoints}天_")

        # 告警
        if margin_alerts:
            margin_lines.append("\n⚠️ **告警**：")
            for a in margin_alerts:
                margin_lines.append(f"- {a['title']}")

        elements.append(build_markdown("\n".join(margin_lines)))

    # 指标说明
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**综合评分（0-100分）**：= 流动性评分(60%) + 风险偏好评分(40%)。>80良好、60-80中性、40-60偏弱、<40极弱",
        "**流动性评分（0-60分）**：衡量市场资金面宽松程度，包括利率、成交额、北向资金等维度",
        "**风险偏好评分（0-40分）**：衡量投资者风险承受意愿，包括涨跌停比、VIX分位、投机情绪等维度",
        "**成交额集中度**：前N只股票成交额占全A比例，越高说明抱团越严重，市场宽度越窄",
        "**VIX分位数**：波动率指数在过去1年的分位。高分位（>80%）=恐慌期可能见底，低分位（<20%）=平静期可能见顶",
        "**吸收率因子(AR)**：衡量市场风险集中度。AR上升→风险集中→系统性风险增加；DAR（AR变化率）过大时警惕系统性事件",
        "**兴登堡指标**：技术分析见顶信号。同时出现大量新高新低+指数在50日均线上方+麦克连指标为负，说明市场内部分裂严重，见顶风险高",
        "**期权IV均线**：跟踪科创50/创业板期权隐含波动率(IV)与MA40/MA80的关系。IV>MA80=★★极度恐慌，IV>MA40=★偏强，IV<MA40=平稳；数据源优先Tushare期权IV，降级为指数已实现波动率(RV)",
        "**两融余额**：融资余额+融券余额的两市合计，衡量杠杆资金活跃度。日变动>50%或分位>80%/<20%触发邮件告警，历史分位基于本地缓存累积数据计算",
        "**使用建议**：综合评分>70可积极做多；<50降低仓位防御；AR+兴登堡+VIX高波三重预警时务必谨慎",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 12. TOP200概念汇总 ----------
def build_top200_concept_card(date_str, concepts, total_concepts=0, summary_stats=None, omo_data=None):
    """TOP200概念汇总卡片
    
    concepts: list of dict with name, avg_change, score, stock_count, leader_stock
    total_concepts: total concepts count
    summary_stats: optional summary statistics
    """
    if not concepts:
        template = 'grey'
    else:
        template = 'blue'
    
    header = build_card_header(f"🔥 TOP200热点概念汇总 | {date_str}", template=template)
    
    elements = []
    
    # 概览
    elements.append(build_markdown(f"**概念总数**: {total_concepts}个"))
    
    if summary_stats:
        stats_lines = []
        for k, v in summary_stats.items():
            stats_lines.append(f"- {k}: {v}")
        elements.append(build_markdown("\n".join(stats_lines)))
    
    elements.append(build_hr())
    
    # Top概念表格
    if concepts:
        table_rows = []
        for i, c in enumerate(concepts[:20]):
            table_rows.append([
                i + 1,
                c.get('name', ''),
                f"{c.get('avg_change', 0):+.2f}%" if c.get('avg_change') is not None else 'N/A',
                c.get('score', '-'),
                c.get('stock_count', '-'),
                c.get('leader_stock', '-'),
            ])
        elements.append(build_data_table(
            ['#', '概念名称', '平均涨幅', '综合评分', '成分股数', '龙头股'],
            table_rows,
            title=f"🏆 Top{min(20, len(concepts))}热门概念"
        ))
    
    # 央行OMO操作
    if omo_data:
        elements.append(build_hr())
        omo_lines = ["🏦 **央行OMO操作** (" + omo_data.get('date', '') + ")"]
        ops = omo_data.get('operations', [])
        if ops:
            op_strs = []
            for op in ops:
                term = "隔夜" if op.get('term') == 0 else f"{op.get('term', '?')}天"
                amt = op.get('amount', 0)
                rate = op.get('rate')
                rate_str = f" @ {rate}%" if rate else ""
                op_strs.append(f"{term}逆回购 **{amt:,}亿**{rate_str}")
            omo_lines.append("操作: " + " / ".join(op_strs))
        
        today_inj = omo_data.get('today_injection')
        today_mat = omo_data.get('today_maturity')
        if today_inj is not None and today_mat is not None:
            omo_lines.append(f"今日操作 **{today_inj:,}亿** ／ 到期 **{today_mat:,}亿**")
        
        net_inj = omo_data.get('today_net')
        if net_inj is not None:
            if net_inj > 0:
                net_tag = f"🟢 净投放 {net_inj:+,}亿"
            elif net_inj < 0:
                net_tag = f"🔴 净回笼 {abs(net_inj):,}亿"
            else:
                net_tag = "⚪ 零投放"
            omo_lines.append(net_tag)
        
        balance = omo_data.get('reverse_repo_balance')
        if balance is not None:
            omo_lines.append(f"逆回购余额: **{balance:,}亿**")
        
        elements.append(build_markdown("\n".join(omo_lines)))

    # 指标说明
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**平均涨幅**：概念板块内所有成分股的平均涨跌幅，反映概念整体强弱",
        "**综合评分**：综合考虑涨幅、成交量、资金流入、涨停股数、持续性等多维度的热度评分",
        "**成分股数**：概念板块包含的股票数量，成分股适中的概念（10-30只）弹性通常更好",
        "**龙头股**：该概念板块内近2周涨幅最高的股票，具有风向标意义",
        "**筛选范围**：基于全市场涨幅前200的股票进行概念聚类，找出强势股集中的热点方向",
        "**使用建议**：优先关注综合评分高、成分股适中、持续多天上榜的概念；首天爆发的新概念需警惕一日游；可结合板块筛选交叉验证热点方向",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(f"数据来源: Tushare | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"))
    
    return wrap_card(header, elements)


# ---------- 13. A股风险预测日报（整合版） ----------
def build_risk_forecast_card(date_str, indices, conclusion, top_risks=None, top_opps=None, style_drift=None, is_midday=False, timing_data=None, margin_data=None, data_warnings=None):
    """A股风险预测日报整合卡片（金字塔结构，结论先行）
    
    布局：
    1. 顶部：综合风险评级 + 一句话结论
    2. 核心风险信号 TOP3（红色预警区）
    3. 潜在机会信号（绿色区）
    4. 各模块明细（折叠样式）：衍生品/结构/量能情绪/极端风险
    5. 风格漂移（附加）
    6. 两融风险监控（附加）
    7. 三维择时监控（附加）
    
    Args:
        date_str: 日期字符串，如 '2026-07-10'
        indices: dict {index_name: {dim_scores, factors, rating, total_score, ...}}
        conclusion: 一句话核心结论
        top_risks: list of 风险信号 dict，按风险度降序
        top_opps: list of 机会信号 dict，按机会度升序
        style_drift: 风格漂移数据（可选）
        is_midday: 是否为午盘版
        timing_data: 三维择时监控数据（可选，收盘模式才有）
        data_warnings: list of warning strings，数据缺失告警
    """
    top_risks = top_risks or []
    top_opps = top_opps or []
    data_warnings = data_warnings or []
    has_data_missing = len(data_warnings) > 0
    
    # 万得全A作为综合视角
    wanquan = indices.get('万得全A', {})
    total_score = wanquan.get('total_score', 0)
    rating = wanquan.get('rating', '🟡 中性')
    rating_level = wanquan.get('rating_level', 2)  # 0-4
    
    # 卡片头部颜色
    if rating_level >= 4:
        template = 'red'
    elif rating_level >= 3:
        template = 'orange'
    elif rating_level >= 2:
        template = 'yellow'
    elif rating_level >= 1:
        template = 'green'
    else:
        template = 'turquoise'
    
    title_suffix = " 🌞 午盘版" if is_midday else ""
    title_prefix = "⚠️ " if has_data_missing else ""
    header = build_card_header(f"{title_prefix}📊 A股风险预测日报 | {date_str}{title_suffix}", template=template)
    
    elements = []

    # 数据缺失告警（放在最前面）
    if data_warnings:
        elements.append(build_note("\\n".join(data_warnings), tag="warning"))
        elements.append(build_hr())
    
    # 标签高亮辅助函数：优先用emoji判断，其次关键词
    def _hl(text, value_on_newline=True):
        """根据标签内容高亮：
        - 🔴/高风险/极端 → 红+⭐
        - 🟠/偏高 → 橙
        - 🟢/低/安全/健康 → 绿
        - 支持 \n 或 <br> 分隔的"数值+标签"格式，只高亮标签行
        修复：1. 优先emoji判断，避免"极低尾部风险"被误标红
              2. 支持<br>作为单元格内换行（Markdown表格不支持\n换行）
        """
        s = str(text)

        # 统一分隔符：同时支持 \n 和 <br>
        sep = None
        if value_on_newline:
            if '<br>' in s:
                sep = '<br>'
            elif '\n' in s:
                sep = '\n'

        def _judge_color(label_text):
            """判断标签的颜色等级，返回 (color, has_star) or (None, False)"""
            t = str(label_text)
            # 优先级1：emoji 直接判断
            if '🔴' in t:
                return 'red', True
            if '🟠' in t:
                return 'orange', False
            if '🟡' in t:
                return None, False
            if '🟢' in t or '✅' in t:
                return 'green', False
            # 优先级2：明确词组判断
            risk_phrases = ['高风险', '极端风险', '极度风险', 'Gamma压制', '强Gamma压制', '深度贴水', '极度超卖']
            for p in risk_phrases:
                if p in t:
                    return 'red', True
            warn_phrases = ['偏高', '偏空', '偏深', '恶化']
            for p in warn_phrases:
                if p in t:
                    return 'orange', False
            safe_phrases = ['低风险', '极低', '安全', '健康', '支撑', 'Gamma支撑', '强Gamma支撑']
            for p in safe_phrases:
                if p in t:
                    return 'green', False
            # 优先级3：单字关键词（需排除反义组合）
            if '高' in t and '低' not in t and '不高' not in t:
                return 'red', True
            if '低' in t and '高' not in t:
                return 'green', False
            return None, False

        if sep is None:
            # 整段文本判断
            color, has_star = _judge_color(s)
            if color == 'red':
                star = '⭐ ' if has_star else ''
                return f"<font color='red'>{star}{s}</font>"
            elif color == 'green':
                return f"<font color='green'>{s}</font>"
            elif color == 'orange':
                return f"<font color='orange'>{s}</font>"
            return s
        else:
            # 只有标签行高亮
            parts = s.split(sep, 1)
            value_part = parts[0]
            label_part = parts[1] if len(parts) > 1 else ''
            color, has_star = _judge_color(label_part)
            if color == 'red':
                star = '⭐ ' if has_star else ''
                return f"{value_part}{sep}<font color='red'>{star}{label_part}</font>"
            elif color == 'green':
                return f"{value_part}{sep}<font color='green'>{label_part}</font>"
            elif color == 'orange':
                return f"{value_part}{sep}<font color='orange'>{label_part}</font>"
            return s
    
    # ===== 第一块：综合评级 + 一句话结论 =====
    # 高风险/极高风险时结论加红加大
    if rating_level >= 3:
        elements.append(build_markdown(f"## <font color='red'><font size='large'>{conclusion}</font></font>"))
    else:
        elements.append(build_markdown(f"## {conclusion}"))
    
    # 四大维度概览
    dim_metrics = []
    ds = wanquan.get('dim_scores', {})

    # ---------- 衍生品维度：分项概况（不使用万得全A聚合值）----------
    deriv_option_indices = ['沪深300', '中证500', '中证1000', '创业板指', '科创50']
    deriv_count = 0
    extreme_alerts = []
    for idx_name in deriv_option_indices:
        idx = indices.get(idx_name, {})
        f = idx.get('factors', {})
        has_data = False
        # SKEW
        skew_info = f.get('SKEW', {})
        skew_val = skew_info.get('value', '')
        if skew_val and skew_val not in ('-', '—', 'N/A', '') and not skew_info.get('is_pending', False):
            has_data = True
            try:
                skew_num = float(skew_val)
                if skew_num >= 150:
                    extreme_alerts.append(f"{idx_name} SKEW极值")
            except (ValueError, TypeError):
                pass
        # GEX
        gex_info = f.get('GEX', {})
        gex_val = gex_info.get('value', '')
        if gex_val and gex_val not in ('-', '—', 'N/A', '') and not gex_info.get('is_pending', False):
            has_data = True
            try:
                gex_num = float(str(gex_val).replace('亿', '').replace('+', '').strip())
                if gex_num <= -5:  # 负且大（亿级）
                    extreme_alerts.append(f"{idx_name} GEX压制")
            except (ValueError, TypeError):
                pass
        # 基差
        basis_info = f.get('基差', {})
        basis_val = basis_info.get('value', '')
        if basis_val and basis_val not in ('-', '—', 'N/A', ''):
            has_data = True
        if has_data:
            deriv_count += 1

    if deriv_count > 0:
        if extreme_alerts:
            deriv_status = "🔴警戒"
            deriv_value = f"{deriv_count}个指数\n" + "、".join(extreme_alerts[:2])
        else:
            deriv_status = "🟡分项"
            deriv_value = f"{deriv_count}个指数有数据"
    else:
        deriv_status = "🟡分项"
        deriv_value = "待更新"

    dim_metrics.append({
        "label": "📈 衍生品",
        "value": deriv_value,
        "status": deriv_status,
    })

    # ---------- 其他三个维度：使用万得全A得分 ----------
    other_dims = [
        ('structural', '结构', '🏗️'),
        ('sentiment', '量能情绪', '💧'),
        ('extreme', '极端', '⚠️'),
    ]
    for dim_key, dim_name, icon in other_dims:
        s = ds.get(dim_key)
        if s is not None:
            if s >= 0.5:
                status = '🔴高'
            elif s >= 0.2:
                status = '🟠偏高'
            elif s >= -0.2:
                status = '🟡中'
            elif s >= -0.5:
                status = '🟢低'
            else:
                status = '✅极低'
            dim_metrics.append({
                "label": f"{icon} {dim_name}",
                "value": f"{s:+.2f}",
                "status": status,
            })
        else:
            dim_metrics.append({
                "label": f"{icon} {dim_name}",
                "value": "N/A",
                "status": "⚪缺失",
            })
    
    elements.append(build_metric_row(dim_metrics))
    elements.append(build_hr())
    
    # ===== 第二块：核心风险信号 TOP3 =====
    elements.append(build_section_header("⚠️ 核心风险信号 TOP 3"))
    
    if top_risks:
        risk_lines = []
        for i, r in enumerate(top_risks[:3]):
            idx_name = r.get('index', '')
            factor = r.get('factor', '')
            value = r.get('value', '')
            label = r.get('label', '')
            dim = r.get('dimension', '')
            risk_lines.append(
                f"⭐ **{i+1}. {idx_name} · {factor}**  \n"
                f"   数值: {value} → <font color='red'><font size='large'>**{label}**</font></font>  \n"
                f"   <font color='grey'>维度: {dim}</font>"
            )
        elements.append(build_markdown("\n\n".join(risk_lines)))
    else:
        elements.append(build_markdown("✅ **当前无显著风险信号**"))
    
    elements.append(build_hr())
    
    # ===== 第三块：潜在机会信号 =====
    elements.append(build_section_header("✅ 潜在机会信号"))
    
    if top_opps:
        opp_lines = []
        for i, o in enumerate(top_opps[:4]):
            idx_name = o.get('index', '')
            factor = o.get('factor', '')
            value = o.get('value', '')
            label = o.get('label', '')
            dim = o.get('dimension', '')
            opp_lines.append(
                f"**{idx_name} · {factor}**: {value} → <font color='green'>{label}</font>  \n"
                f"<font color='grey'>维度: {dim}</font>"
            )
        elements.append(build_markdown("\n\n".join(opp_lines)))
    else:
        elements.append(build_markdown("⚪ **暂无明确机会信号**"))
    
    elements.append(build_hr())
    
    # ===== 第四块：各指数风险对比 =====
    elements.append(build_section_header("📈 各指数风险对比"))
    
    core_indices = ['沪深300', '中证500', '中证1000', '创业板指', '科创50', '万得全A']
    idx_rows = []
    for idx_name in core_indices:
        idx = indices.get(idx_name, {})
        if not idx or not idx.get('factors'):
            continue
        rating = idx.get('rating', 'N/A')
        score = idx.get('total_score', 0)
        idx_ds = idx.get('dim_scores', {})
        
        # 判断是否为高风险行（整行标红）
        is_high_risk_row = score >= 1.0 or '🔴' in rating
        
        def _fmt(text, red=False, green=False, star=False):
            """单元格格式化：红/绿/星标"""
            prefix = '⭐ ' if star else ''
            if red:
                return f"<font color='red'>{prefix}{text}</font>"
            elif green:
                return f"<font color='green'>{prefix}{text}</font>"
            return f"{prefix}{text}" if prefix else text
        
        # 指数名称、评级、综合得分
        name_cell = _fmt(idx_name, red=is_high_risk_row)
        rating_cell = _fmt(rating, red=is_high_risk_row)
        score_red = is_high_risk_row or score >= 1.0
        score_green = score < 0 and not score_red
        score_cell = _fmt(f"{score:+.2f}", red=score_red, green=score_green)
        
        row = [name_cell, rating_cell, score_cell]
        
        # 四大维度得分
        for dim in ['derivatives', 'structural', 'sentiment', 'extreme']:
            s = idx_ds.get(dim)
            if s is not None:
                dim_text = f"{s:+.2f}"
                if s >= 0.5:
                    # 高风险维度：红色 + 星标
                    row.append(_fmt(dim_text, red=True, star=True))
                elif s < 0:
                    # 低风险（负分）：绿色
                    row.append(_fmt(dim_text, green=True))
                elif is_high_risk_row:
                    # 高风险行中其他维度也标红
                    row.append(_fmt(dim_text, red=True))
                else:
                    row.append(dim_text)
            else:
                row.append('-')
        idx_rows.append(row)
    
    if idx_rows:
        elements.append(build_data_table(
            ['指数', '评级', '综合', '衍生品', '结构', '量能', '极端'],
            idx_rows,
        ))
    
    elements.append(build_hr())
    
    # ===== 第五块：各模块明细（折叠样式） =====
    
    # --- 模块1：衍生品定价风险 ---
    deriv_indices = [i for i in core_indices if indices.get(i, {}).get('derivatives_count', 0) > 0]
    if deriv_indices:
        deriv_rows = []
        for idx_name in deriv_indices:
            idx = indices[idx_name]
            f = idx.get('factors', {})
            skew_v = f.get('SKEW', {}).get('value', '-')
            skew_label = f.get('SKEW', {}).get('label', '-')
            gex_v = f.get('GEX', {}).get('value', '-')
            gex_label = f.get('GEX', {}).get('label', '-')
            basis_v = f.get('基差', {}).get('value', '-')
            basis_label = f.get('基差', {}).get('label', '-')
            deriv_rows.append([
                idx_name,
                _hl(f"{skew_v}<br>{skew_label}"),
                _hl(f"{gex_v}<br>{gex_label}"),
                _hl(f"{basis_v}<br>{basis_label}"),
            ])
        
        deriv_content_lines = ["**📈 衍生品定价风险明细**", ""]
        # 用表格
        deriv_content_lines.append("| 指数 | SKEW | GEX | 基差 |")
        deriv_content_lines.append("|------|------|-----|------|")
        for row in deriv_rows:
            deriv_content_lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
        
        elements.append(build_module_block(
            "📈 模块一：衍生品定价风险（SKEW / GEX / 基差）",
            deriv_content_lines[2:],  # 从表格开始
        ))
        elements.append(build_hr())
    
    # --- 模块2：结构风险 ---
    struct_indices = [i for i in core_indices if indices.get(i, {}).get('structural_count', 0) > 0]
    if struct_indices:
        struct_content_lines = []
        
        # CFC/up50/top5z/CDI 表格（午盘版不显示CFC）
        # 午盘版已移除CFC因子（离散度公式与收盘版峰度+偏度口径不一致）
        cfc_indices = [i for i in struct_indices if any(k in indices[i].get('factors', {}) for k in ['CFC', 'up50', 'top5z', 'CDI'])]
        if cfc_indices:
            if is_midday:
                struct_content_lines.append("**结构四因子（午盘版：up50 / top5z / CDI聚类 / 结构因子）**")
                struct_content_lines.append("")
                struct_content_lines.append("| 指数 | up50 | top5z | CDI聚类 |")
                struct_content_lines.append("|------|------|-------|-----|")
                for idx_name in cfc_indices:
                    f = indices[idx_name].get('factors', {})
                    def _v(fk):
                        info = f.get(fk, {})
                        val = info.get('value', '-')
                        lab = info.get('label', '')
                        lab_s = str(lab)
                        for kw in ['极度集中', '极端风险', '高风险', '高风险预警', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                            if kw in lab_s:
                                return f"<font color='red'>⭐ {val}</font>"
                        for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                            if kw in lab_s:
                                return f"<font color='green'>{val}</font>"
                        return str(val)
                    struct_content_lines.append(f"| {idx_name} | {_v('up50')} | {_v('top5z')} | {_v('CDI')} |")
                struct_content_lines.append("")
                struct_content_lines.append("<font color='grey'>💡 午盘版CFC已移除（离散度公式与收盘版峰度+偏度口径不一致，无参考价值）</font>")
                struct_content_lines.append("")
            else:
                struct_content_lines.append("**CFC / up50 / top5z / CDI聚类**")
                struct_content_lines.append("")
                struct_content_lines.append("| 指数 | CFC | up50 | top5z | CDI聚类 |")
                struct_content_lines.append("|------|-----|------|-------|-----|")
                for idx_name in cfc_indices:
                    f = indices[idx_name].get('factors', {})
                    # 根据因子label给value加颜色标记（只改颜色，不改显示内容）
                    def _v(fk):
                        info = f.get(fk, {})
                        val = info.get('value', '-')
                        lab = info.get('label', '')
                        lab_s = str(lab)
                        for kw in ['极度集中', '极端风险', '高风险', '高风险预警', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                            if kw in lab_s:
                                return f"<font color='red'>⭐ {val}</font>"
                        for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                            if kw in lab_s:
                                return f"<font color='green'>{val}</font>"
                        return str(val)
                    struct_content_lines.append(f"| {idx_name} | {_v('CFC')} | {_v('up50')} | {_v('top5z')} | {_v('CDI')} |")
                struct_content_lines.append("")
            
            # ===== 📖 怎么看CFC/up50/top5z/CDI聚类（人话说明）=====
            # 午盘版跳过CFC解读
            if not is_midday:
                struct_content_lines.append("---")
                struct_content_lines.append("**📖 怎么看结构四因子**")
                struct_content_lines.append("")
                struct_content_lines.append("**CFC（横截面因子）**——抱团集中度，看赛道拥挤不拥挤")
                struct_content_lines.append("• \>1.5 → 极度抱团 ⚠️ 见顶风险，小心追高被套")
                struct_content_lines.append("• 1.0~1.5 → 偏高，趋势还在但要留意拐点")
                struct_content_lines.append("• -1.0~1.0 → 中性，正常波动")
                struct_content_lines.append("• \<-1.0 → 分散到底部 🟢 可能有机会，可以关注")
                struct_content_lines.append("• \<-1.5 → 极度分散，极端恐慌，往往是大底信号")
                struct_content_lines.append("")
                struct_content_lines.append("**up50（50日线占比）**——多少只股票在50日均线上方，看市场整体强弱")
            else:
                struct_content_lines.append("---")
                struct_content_lines.append("**📖 怎么看结构三因子（午盘版）**")
                struct_content_lines.append("")
                struct_content_lines.append("**up50（50日线占比）**——多少只股票在50日均线上方，看市场整体强弱")
            struct_content_lines.append("• \>80% → 过热 🔥 大部分股票都涨了，小心回调")
            struct_content_lines.append("• 60%~80% → 偏强，趋势向上，顺势操作")
            struct_content_lines.append("• 40%~60% → 中性，震荡市")
            struct_content_lines.append("• 20%~40% → 偏弱，大部分股票在下跌")
            struct_content_lines.append("• \&lt;20% → 超跌 💚 大部分股票跌透了，可能有反弹机会")
            struct_content_lines.append("")
            struct_content_lines.append("**top5z（前5%成交额占比）**——成交集中度，看资金是不是扎堆头部")
            struct_content_lines.append("• \>50% → 极度集中 ⚠️ 资金抱团龙头，小票失血，风险高")
            struct_content_lines.append("• 40%~50% → 偏高，龙头行情，小票难涨")
            struct_content_lines.append("• 25%~40% → 正常区间，大小盘均衡")
            struct_content_lines.append("• 15%~25% → 偏低，资金分散，普涨行情可能")
            struct_content_lines.append("• \&lt;15% → 极度分散，市场没有主线")
            struct_content_lines.append("")
            struct_content_lines.append("**CDI聚类（聚类离散度）**——板块分化程度，看市场风格是否一致")
            struct_content_lines.append("• \>2.0 → 高度分化 ⚠️ 板块撕裂，选对方向最重要")
            struct_content_lines.append("• 1.0~2.0 → 中等分化，正常结构性行情")
            struct_content_lines.append("• 0.5~1.0 → 偏低，市场风格比较一致")
            struct_content_lines.append("• \&lt;0.5 → 高度一致，同涨同跌，趋势行情")
            struct_content_lines.append("")
            if not is_midday:
                struct_content_lines.append("<font color='grey'>💡 口诀：CFC看抱团、up50看温度、top5z看集中度、CDI聚类看分化</font>")
            else:
                struct_content_lines.append("<font color='grey'>💡 口诀：up50看温度、top5z看集中度、CDI聚类看分化</font>")
            struct_content_lines.append("")
            struct_content_lines.append("⭐ = 高风险预警标记（极度/极端等级）")
            struct_content_lines.append("")
        
        # AbsFactor/RelFactor 表格 + 解读
        sf_indices = [i for i in struct_indices if any(k in indices[i].get('factors', {}) for k in ['AbsFactor', 'RelFactor'])]
        if sf_indices:
            struct_content_lines.append("**结构因子（AbsFactor / RelFactor）**")
            struct_content_lines.append("")
            struct_content_lines.append("| 指数 | AbsFactor | 多空解读 | RelFactor | 资金解读 |")
            struct_content_lines.append("|------|-----------|----------|-----------|----------|")
            
            def _parse_sigma(val_str):
                """从 '+0.61σ' 格式中解析数值"""
                if not val_str or val_str == '-' or val_str == 'N/A':
                    return None
                try:
                    return float(val_str.replace('σ', '').replace(' ', ''))
                except (ValueError, AttributeError):
                    return None
            
            def _abs_interpret(abs_val, abs_label):
                """AbsFactor 人话解读"""
                if abs_val is None:
                    return '<font color="grey">暂无</font>'
                if abs_val >= 1.5:
                    return '<font color="red">多头力量强，接近极端</font>'
                elif abs_val >= 1.0:
                    return '<font color="orange">多头趋势中</font>'
                elif abs_val >= 0.3:
                    return '<font color="green">温和偏多</font>'
                elif abs_val > -0.3:
                    return '⚖️ 多空平衡'
                elif abs_val > -1.0:
                    return '<font color="green">温和偏空</font>'
                elif abs_val > -1.5:
                    return '<font color="orange">空头趋势中</font>'
                else:
                    return '<font color="red">空头力量强，接近极端，可能反转</font>'
            
            def _rel_interpret(rel_val, rel_label):
                """RelFactor 人话解读"""
                if rel_val is None:
                    return '<font color="grey">暂无</font>'
                if rel_val >= 1.5:
                    return '<font color="red">资金大幅流出⚠️</font>'
                elif rel_val >= 0.8:
                    return '<font color="orange">资金流出中</font>'
                elif rel_val >= 0.3:
                    return '资金小幅流出'
                elif rel_val > -0.3:
                    return '⚖️ 资金平衡'
                elif rel_val > -0.8:
                    return '资金小幅流入'
                elif rel_val > -1.5:
                    return '<font color="green">资金流入中</font>'
                else:
                    return '<font color="green">资金大幅流入🔥</font>'
            
            def _sf_color(fk, info):
                """结构因子值颜色标记"""
                val = info.get('value', '-')
                lab = info.get('label', '')
                lab_s = str(lab)
                for kw in ['极度集中', '极端风险', '高风险', '高风险预警', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                    if kw in lab_s:
                        return f"<font color='red'>⭐ {val}</font>"
                for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                    if kw in lab_s:
                        return f"<font color='green'>{val}</font>"
                return str(val)
            
            for idx_name in sf_indices:
                f = indices[idx_name].get('factors', {})
                abs_info = f.get('AbsFactor', {})
                rel_info = f.get('RelFactor', {})
                abs_val = _parse_sigma(abs_info.get('value', ''))
                rel_val = _parse_sigma(rel_info.get('value', ''))
                abs_lab = abs_info.get('label', '')
                rel_lab = rel_info.get('label', '')
                
                abs_colored = _sf_color('AbsFactor', abs_info)
                rel_colored = _sf_color('RelFactor', rel_info)
                abs_interp = _abs_interpret(abs_val, abs_lab)
                rel_interp = _rel_interpret(rel_val, rel_lab)
                
                struct_content_lines.append(f"| {idx_name} | {abs_colored} | {abs_interp} | {rel_colored} | {rel_interp} |")
            
            struct_content_lines.append("")
            
            # ===== 📖 怎么看结构因子（人话说明）=====
            struct_content_lines.append("---")
            struct_content_lines.append("**📖 怎么看结构因子**")
            struct_content_lines.append("")
            struct_content_lines.append("**AbsFactor（绝对因子）**——衡量多空力量的强弱")
            struct_content_lines.append("• 正数越大 → 多头力量越强，上涨趋势越猛")
            struct_content_lines.append("• 负数越小 → 空头力量越强，下跌趋势越猛")
            struct_content_lines.append("• 接近0 → 多空拉锯，震荡行情")
            struct_content_lines.append("• 超过±1.5σ → 接近极端，物极必反，注意反转机会")
            struct_content_lines.append("")
            struct_content_lines.append("**RelFactor（相对因子）**——衡量资金的流动方向")
            struct_content_lines.append("• 负值 → 资金在流入，有人在偷偷买，可能要涨")
            struct_content_lines.append("• 正值 → 资金在流出，有人在跑路，小心下跌")
            struct_content_lines.append("• 绝对值越大 → 资金流动越猛，趋势推动力越强")
            struct_content_lines.append("")
            struct_content_lines.append("**赔率模式（v2.0）**")
            struct_content_lines.append("• 🎯 **底部赔率关注**：价格分位<25%（跌到位）+ RelFactor<0（资金流入）+ Abs<0（空头区）= 可以分批建仓")
            struct_content_lines.append("• ⚠️ **顶部赔率风险**：价格分位>75%（涨到位）+ RelFactor>0（资金流出）+ Abs>0（多头区）= 注意止盈风险")
            struct_content_lines.append("• 🔻 **空头挤出中**：Abs<0 + Rel>1（资金还在流出）= 还在杀跌，等待资金回流信号")
            struct_content_lines.append("")
            struct_content_lines.append("**胜率模式**")
            struct_content_lines.append("• 🟢 **胜率模式持有**：AbsFactor>0（多头）+ RelFactor<0.5（没在大幅流出）= 顺势持有")
            struct_content_lines.append("")
            struct_content_lines.append("**常见组合信号**")
            struct_content_lines.append("• ✅ AbsFactor>1 + RelFactor<0 → 多头+资金流入，强势上涨，持有/加仓")
            struct_content_lines.append("• ⚠️ AbsFactor>1.5 + RelFactor>0 → 极端多头+资金流出，可能见顶，注意止盈")
            struct_content_lines.append("• 🔍 AbsFactor<-1.5 + RelFactor<0 → 极端空头但资金还在流入，可能见底，关注抄底机会")
            struct_content_lines.append("• ❌ AbsFactor<-1 + RelFactor>0 → 空头+资金流出，下跌趋势中，回避")
            struct_content_lines.append("")
            struct_content_lines.append("<font color='grey'>💡 简单口诀：Abs看方向，Rel看资金；同向趋势稳，反向要变盘</font>")
        
        if struct_content_lines:
            module_title = "🏗️ 模块二：结构风险（CDI聚类 / 结构因子）" if is_midday else "🏗️ 模块二：结构风险（CFC / CDI聚类 / 结构因子）"
            elements.append(build_module_block(
                module_title,
                struct_content_lines,
            ))
            elements.append(build_hr())
    
    # --- 模块3：量能情绪风险 ---
    sent_indices = [i for i in core_indices if indices.get(i, {}).get('sentiment_count', 0) > 0]
    if sent_indices:
        sent_content_lines = []
        sent_content_lines.append("| 指数 | 成交量分位 | 成交额集中度 | 涨跌停比 |")
        sent_content_lines.append("|------|------------|--------------|----------|")
        for idx_name in sent_indices:
            f = indices[idx_name].get('factors', {})
            def _vs(fk):
                info = f.get(fk, {})
                val = info.get('value', '-')
                lab = info.get('label', '')
                lab_s = str(lab)
                for kw in ['极度集中', '极端风险', '高风险', '高风险预警', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                    if kw in lab_s:
                        return f"<font color='red'>⭐ {val}</font>"
                for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                    if kw in lab_s:
                        return f"<font color='green'>{val}</font>"
                return str(val)
            sent_content_lines.append(f"| {idx_name} | {_vs('成交量分位')} | {_vs('成交额集中度')} | {_vs('涨跌停比')} |")
        
        elements.append(build_module_block(
            "💧 模块三：量能情绪风险（成交量 / 集中度 / 涨跌停）",
            sent_content_lines,
        ))
        elements.append(build_hr())
    
    # --- 模块4：极端风险（LPPL） ---
    ext_indices = [i for i in core_indices if indices.get(i, {}).get('extreme_count', 0) > 0]
    if ext_indices:
        ext_content_lines = []
        ext_content_lines.append("| 指数 | LPPL泡沫信号 |")
        ext_content_lines.append("|------|-------------|")
        for idx_name in ext_indices:
            f = indices[idx_name].get('factors', {})
            lppl = f.get('LPPL泡沫', {}).get('label', '-')
            # 极端风险标签高亮
            lppl_hl = _hl(lppl, value_on_newline=False)
            ext_content_lines.append(f"| {idx_name} | {lppl_hl} |")
        
        elements.append(build_module_block(
            "⚠️ 模块四：极端风险（LPPL泡沫检测）",
            ext_content_lines,
        ))
        elements.append(build_hr())
    
    # --- 风格漂移（附加） ---
    if style_drift:
        drift_label = style_drift.get('label', '')
        drift_advice = style_drift.get('advice', '')
        z_opt = style_drift.get('z_opt', 0)
        zone = style_drift.get('zone', '')
        
        drift_lines = [
            f"**风格漂移综合分（Z_opt）**: {z_opt:.3f}  {zone} {drift_label}",
            f"**操作建议**: {drift_advice}",
        ]
        
        # 各标的详情
        deep = style_drift.get('deep_analysis', {})
        def _fmt_iv(av):
            try:
                v = float(av)
                return f"{v*100:.1f}%" if abs(v) <= 1.5 else f"{v:.1f}%"
            except (TypeError, ValueError):
                return str(av)
        if deep:
            drift_lines.append("")
            drift_lines.append(f"**科创50 IV水平**: {deep.get('iv_level_科创50', '-')} ({_fmt_iv(deep.get('iv_abs_科创50'))})")
            drift_lines.append(f"**创业板 IV水平**: {deep.get('iv_level_创业板', '-')} ({_fmt_iv(deep.get('iv_abs_创业板'))})")
            drift_lines.append(f"**沪深300 IV水平**: {deep.get('iv_level_沪深300', '-')} ({_fmt_iv(deep.get('iv_abs_沪深300'))})")
        
        elements.append(build_module_block(
            "🎨 风格漂移监控",
            drift_lines,
        ))
        elements.append(build_hr())
    
    # --- 两融风险监控（附加） ---
    if margin_data and margin_data.get('risk_level') != 'unknown':
        td = margin_data.get('trade_date', '')
        dt_str = f"{td[:4]}-{td[4:6]}-{td[6:]}" if len(td) == 8 else td
        risk = margin_data.get('risk_level', 'low')
        risk_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(risk, '⚪')
        
        margin_lines = []
        
        # 操作建议
        margin_lines.append(margin_data.get('advice', ''))
        margin_lines.append("")
        
        # 核心指标
        rzye = margin_data.get('_rzye_raw')
        circ = margin_data.get('total_circ_mv_yi')
        ratio = margin_data.get('margin_circ_ratio')
        chg = margin_data.get('rzye_chg', {})
        accel = margin_data.get('acceleration')
        consec = margin_data.get('consecutive_decline_days', 0)
        
        metric_parts = []
        if rzye:
            metric_parts.append(f"融资余额 {rzye / 1e8:.0f}亿")
        if circ:
            if circ > 10000:
                metric_parts.append(f"流通市值 {circ / 10000:.1f}万亿")
            else:
                metric_parts.append(f"流通市值 {circ:.0f}亿")
        if ratio is not None:
            t = margin_data.get('ratio_trend', '→')
            metric_parts.append(f"融资/流通 {ratio:.3f}% {t}")
        if metric_parts:
            margin_lines.append(" | ".join(metric_parts))
        
        # 融资变化率
        cp = []
        for n in (5, 10, 20):
            v = chg.get(n)
            if v is not None:
                arrow = "↑" if v > 0 else "↓" if v < 0 else "→"
                cp.append(f"{n}日{v * 100:+.2f}%{arrow}")
        if cp:
            margin_lines.append("")
            margin_lines.append(f"**变化率**: {' | '.join(cp)}")
        if accel is not None:
            at = '⚡加速流出' if accel < -0.003 else ('减速中' if accel > 0.003 else '平稳')
            margin_lines.append(f"**加速度**: {accel * 100:+.3f}% ({at})")
        if consec > 0:
            margin_lines.append(f"**连续净偿还**: {consec}天")
        
        # 风险监控
        chain = margin_data.get('chain_risk', False)
        stress = margin_data.get('stress_stocks', [])
        leverage_exit = margin_data.get('leverage_exit', False)
        rz_buy = margin_data.get('rz_buy_ratio')
        
        if chain:
            margin_lines.append("")
            margin_lines.append(f"🔴 **强平连锁风险** 高杠杆股集体加速偿还（{len(stress)}只）")
        if leverage_exit:
            margin_lines.append(f"🔴 **杠杆资金撤退** 融资/成交额 {rz_buy:.2f}%（骤降）")
        
        # 机会监控
        near_clear = margin_data.get('near_clearing', False)
        rebound = margin_data.get('rebound_candidates', [])
        bottom = margin_data.get('bottom_divergence', False)
        style_sw = margin_data.get('style_switch', False)
        
        if near_clear or rebound or bottom or style_sw:
            margin_lines.append("")
            opp_parts = []
            if near_clear:
                opp_parts.append("🟢出清接近尾声")
            if rebound:
                opp_parts.append(f"🟢反弹候选{len(rebound)}只")
            if bottom:
                opp_parts.append(f"🟢底部确认")
            if style_sw:
                opp_parts.append("🟢风格切换")
            margin_lines.append(" | ".join(opp_parts))
        
        # 227信号
        if margin_data.get('signal_227'):
            margin_lines.append("")
            margin_lines.append("⚠️ **227%担保比例等效信号已触发**")
        
        # 数据缺失警告
        warns = margin_data.get('data_warnings', [])
        if warns:
            margin_lines.append("")
            for w in warns:
                margin_lines.append(f"> {w}")
        
        elements.append(build_field(
            "lark_md",
            "\n".join(margin_lines),
            f"{risk_emoji} 两融风险监控 | {dt_str}",
        ))
        elements.append(build_hr())
    
    # --- 三维择时监控（附加） ---
    if timing_data and timing_data.get('data_available'):
        dims = timing_data['dimensions']
        comp = timing_data['composite']
        advice = comp.get('advice', {})
        
        # 6个维度简述
        dim_info = [
            ('etf_flow', '① ETF资金流'),
            ('crowding', '② 市场拥挤度'),
            ('pcr', '③ 期权情绪(PCR)'),
            ('sector_holding', '④ 板块持仓'),
            ('margin', '⑤ 杠杆水平'),
            ('volume_price', '⑥ 量价关系'),
        ]
        
        timing_lines = []
        # 综合评级
        rating = comp.get('rating', '⚪ 数据不足')
        timing_lines.append(f"**择时综合评级：{rating}**")
        timing_lines.append("")
        
        # 多空维度统计
        g = comp.get('green_count', 0)
        y = comp.get('yellow_count', 0)
        r = comp.get('red_count', 0)
        m = comp.get('missing_count', 0)
        stats_parts = []
        if g > 0:
            stats_parts.append(f"🟢看多 {g}")
        if y > 0:
            stats_parts.append(f"🟡中性 {y}")
        if r > 0:
            stats_parts.append(f"🔴看空 {r}")
        if m > 0:
            stats_parts.append(f"⚪缺失 {m}")
        timing_lines.append("维度分布：" + " ｜ ".join(stats_parts))
        timing_lines.append("")
        
        # 各维度信号速览
        timing_lines.append("**各维度信号**")
        timing_lines.append("")
        timing_lines.append("| 维度 | 信号 | 简述 |")
        timing_lines.append("|------|------|------|")
        for dim_key, dim_name in dim_info:
            dim = dims.get(dim_key, {})
            label = dim.get('label', '⚪ 缺失')
            summary = dim.get('summary', '')
            if len(summary) > 25:
                summary = summary[:24] + '…'
            timing_lines.append(f"| {dim_name} | {label} | {summary} |")
        timing_lines.append("")
        
        # 操作建议
        timing_lines.append("**📋 操作建议**")
        timing_lines.append("")
        pos = advice.get('position', '')
        idx = advice.get('index_advice', '')
        sec = advice.get('sector_advice', '')
        if pos:
            timing_lines.append(f"• **整体仓位**：{pos}")
        if idx:
            timing_lines.append(f"• **宽基指数**：{idx}")
        if sec:
            timing_lines.append(f"• **热门板块**：{sec}")
        
        elements.append(build_module_block(
            f"📊 三维择时监控  {rating.split()[0] if ' ' in rating else rating}",
            timing_lines,
        ))
        elements.append(build_hr())
    
    # ===== 指标说明 =====
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**综合风险评级**：基于衍生品定价(25%)+结构(30%)+量能情绪(25%)+极端风险(20%)加权计算，分数范围-1~+1，正值越高风险越大",
        "**衍生品定价风险**：SKEW（尾部风险）、GEX（Gamma敞口）、基差（期货升贴水）综合判断市场定价隐含的风险",
        "**结构风险**：" + ("up50均线占比、top5z成交集中度、CDI聚类离散度指数、AbsFactor/RelFactor结构因子（午盘版已移除CFC）" if is_midday else "CFC横截面偏度峰度、up50均线占比、top5z成交集中度、CDI聚类离散度指数、AbsFactor/RelFactor结构因子"),
        "**量能情绪风险**：成交量分位、成交额集中度、涨跌停比、吸收率因子、兴登堡指标等情绪类指标",
        "**极端风险**：LPPL对数周期幂律模型，检测市场是否处于泡沫末期，高质量拟合且临界日临近时风险极高",
        "**使用建议**：综合评级>0.5时降低仓位防御；<-0.3时可积极进攻；重点关注TOP3风险信号的边际变化；各模块出现共振时可靠性最高",
    ]))
    
    # ===== 底部 =====
    elements.append(build_hr())
    
    # 图例说明（折叠区）
    elements.append(build_collapsible("📌 图例说明", [
        "⚡ = 午盘实时估算数据（收盘后更新）",
        "🔴 / 🟠 / 🟡 / 🟢 = 风险等级（高 / 偏高 / 中性 / 低）",
        "⭐ = 高风险预警标记（极度/极端等级）",
        "CDI聚类 = 聚类离散度版CDI（衡量市场分化程度）",
        "CDI风洞 = 峰度版CDI（衡量微观结构恶化，CFC结构因子报告使用）",
    ]))
    
    # 数据来源（午盘版用实时源）
    if is_midday:
        data_source_text = "数据来源: 新浪财经实时行情 + 东财涨停池 | 午盘实时数据"
    else:
        data_source_text = "数据来源: Tushare + 交易所 + AkShare"
    
    elements.append(build_note(
        f"{data_source_text} | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))
    
    return wrap_card(header, elements)


# ---------- 14. 每日英文邮件词根学习 ----------
def build_daily_english_learning_card(date_str, email_summaries, root_groups, high_freq_words=None, myth_stories=None, myth_closing_line="", total_words=0, total_high_freq=0):
    """每日英文邮件词根学习卡片

    金字塔信息层级：头部概览 → 邮件速览 → 高频难词 → 词根学习主区 → 神话记忆法(折叠) → 底部统计

    Args:
        date_str: 日期字符串，如 '2026年07月10日'
        email_summaries: list of dict with keys: subject, from, summary
        root_groups: list of dict with keys: root, root_meaning, words
            each word: {word, structure, meaning, source_sentence, is_extension}
        high_freq_words: list of dict with keys: word, phonetic, meaning, part_of_speech,
            source_sentence, difficulty, frequency, structure (可选，不传则不展示高频难词板块)
        myth_stories: list of dict with keys: myth_source, myth_figure, myth_story,
            words (list of {word, etymology_link, memory_anchor}) （可选，不传则不展示神话记忆法板块）
        myth_closing_line: 神话记忆法串记一句话（可选）
        total_words: 总单词数（可选，不填则自动统计）
        total_high_freq: 高频难词数（可选，不填则自动统计）
    """
    # 统计数据
    email_count = len(email_summaries)
    root_count = len(root_groups)
    if total_words <= 0:
        total_words = sum(len(g.get('words', [])) for g in root_groups)
    if high_freq_words is None:
        high_freq_words = []
    if total_high_freq <= 0:
        total_high_freq = len(high_freq_words)

    # 卡片头部
    header = build_card_header(
        f"📚 今日英文邮件词根学习 | {date_str}",
        template='blue'
    )

    elements = []

    # ===== 第一块：核心指标 =====
    metric_items = [
        {"label": "📧 英文邮件", "value": f"{email_count} 封"},
    ]
    if total_high_freq > 0:
        metric_items.append({"label": "📖 高频难词", "value": f"{total_high_freq} 个"})
    metric_items.extend([
        {"label": "🌱 词根组数", "value": f"{root_count} 组"},
        {"label": "📝 学习单词", "value": f"{total_words} 个"},
    ])
    elements.append(build_metric_row(metric_items))
    elements.append(build_hr())

    # ===== 第二块：邮件速览 =====
    elements.append(build_section_header("📧 邮件速览"))

    if email_summaries:
        email_lines = []
        for i, eml in enumerate(email_summaries, 1):
            subject = eml.get('subject', '(无主题)')
            sender = eml.get('from', '未知')
            summary = eml.get('summary', '')

            # 主题行（加粗）+ 发件人（灰字）
            email_lines.append(f"**{i}. {subject}**")
            email_lines.append(f"<font color='grey'>👤 {sender}</font>")
            if summary:
                # 摘要控制长度
                s = summary.strip()
                if len(s) > 120:
                    s = s[:117] + "..."
                email_lines.append(f"💬 {s}")
            email_lines.append("")

        elements.append(build_markdown("\n".join(email_lines)))
    else:
        elements.append(build_markdown("📭 今日无英文邮件"))

    elements.append(build_hr())

    # ===== 第二块五：高频难词 =====
    if high_freq_words:
        elements.append(build_section_header("📖 高频难词"))

        # 难度标签映射
        diff_map = {
            "CET4": ("🟢", "四级"),
            "CET6": ("🟡", "六级"),
            "考研": ("🟠", "考研"),
            "雅思": ("🔴", "雅思"),
            "托福": ("🔴", "托福"),
            "GRE": ("💚", "GRE"),
        }

        # 以列表式紧凑展示：单词（音标）+ 词性 + 释义 + 难度标签
        # 分两列展示，每列最多5个词
        half = (len(high_freq_words) + 1) // 2
        left_words = high_freq_words[:half]
        right_words = high_freq_words[half:]

        def _build_word_col(words_list):
            lines = []
            for w in words_list:
                word = w.get('word', '')
                phonetic = w.get('phonetic', '')
                meaning = w.get('meaning', '')
                pos = w.get('part_of_speech', '')
                difficulty = w.get('difficulty', '')
                freq = w.get('frequency', 1)
                structure = w.get('structure', '')

                emoji, label = diff_map.get(difficulty, ("⚪", difficulty))

                freq_suffix = f" ×{freq}" if freq > 1 else ""

                # 主行：单词 + 音标 + 难度标签
                lines.append(f"**{word}** <font color='grey'>{phonetic}</font>")
                # 次行：词性 + 释义 + 频率
                lines.append(f"<font color='grey'>{pos} </font>{meaning} {emoji}{label}{freq_suffix}")
                # 词根拆解行
                if structure:
                    lines.append(f"🔤 <font color='grey'>{structure}</font>")
                lines.append("")
            return lines

        # 使用两列布局展示
        left_lines = _build_word_col(left_words)
        right_lines = _build_word_col(right_words)

        # 构建两列布局
        col_set = {
            "tag": "column_set",
            "flex_mode": "none",
            "background_style": "default",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": "\n".join(left_lines),
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": "\n".join(right_lines),
                        }
                    ],
                },
            ],
        }
        elements.append(col_set)

        # 折叠区：每个词的原句
        sentence_lines = []
        for i, w in enumerate(high_freq_words, 1):
            word = w.get('word', '')
            src_sentence = w.get('source_sentence', '')
            if src_sentence:
                s = src_sentence.strip()
                if len(s) > 100:
                    s = s[:97] + "..."
                sentence_lines.append(f"{i}. **{word}** — \"{s}\"")

        if sentence_lines:
            elements.append(build_collapsible(
                "📝 查看原句",
                sentence_lines,
                default_expanded=False
            ))

        elements.append(build_hr())

    # ===== 第三块：词根学习主展示区 =====
    elements.append(build_section_header("🌱 词根学习"))

    if root_groups:
        # 第一个词根组：完整展开（主展示区）
        first_group = root_groups[0]
        first_root = first_group.get('root', '')
        first_root_meaning = first_group.get('root_meaning', '')
        first_words = first_group.get('words', [])

        first_lines = []
        first_lines.append(f"### 词根①：**{first_root}**（{first_root_meaning}）")
        first_lines.append("")

        for j, w in enumerate(first_words, 1):
            word = w.get('word', '')
            structure = w.get('structure', '')
            meaning = w.get('meaning', '')
            is_ext = w.get('is_extension', False)
            src_sentence = w.get('source_sentence', '')

            ext_mark = " ⭐拓展词" if is_ext else ""
            first_lines.append(f"**{j}. {word}**{ext_mark}")
            first_lines.append(f"   🔤 {structure} → {meaning}")

            if src_sentence and not is_ext:
                s = src_sentence.strip()
                if len(s) > 100:
                    s = s[:97] + "..."
                first_lines.append(f"   📖 *\"{s}\"*")

            first_lines.append("")

        elements.append(build_markdown("\n".join(first_lines)))

        # 其他词根组：折叠样式展示
        for idx, group in enumerate(root_groups[1:], 2):
            root_name = group.get('root', '')
            root_meaning = group.get('root_meaning', '')
            words = group.get('words', [])

            # 构建折叠内容行
            content_lines = []
            for j, w in enumerate(words, 1):
                word = w.get('word', '')
                structure = w.get('structure', '')
                meaning = w.get('meaning', '')
                is_ext = w.get('is_extension', False)
                src_sentence = w.get('source_sentence', '')

                ext_mark = " ⭐" if is_ext else ""
                line = f"{j}. **{word}**{ext_mark} — {structure} → {meaning}"
                content_lines.append(line)

                if src_sentence and not is_ext:
                    s = src_sentence.strip()
                    if len(s) > 80:
                        s = s[:77] + "..."
                    content_lines.append(f"   📖 \"{s}\"")

            elements.append(build_hr())
            elements.append(build_collapsible(
                f"词根{idx}：{root_name}（{root_meaning}）",
                content_lines,
                default_expanded=False
            ))
    else:
        elements.append(build_markdown("📭 今日无词根学习内容"))

    # ===== 第四块：神话记忆法（折叠区） =====
    if myth_stories:
        elements.append(build_hr())
        elements.append(build_section_header("🔮 神话记忆法"))

        # 统计总词数
        total_myth_words = sum(len(s.get('words', [])) for s in myth_stories)

        # 每个神话故事作为一组
        myth_content_lines = []
        for i, story in enumerate(myth_stories, 1):
            myth_source = story.get('myth_source', '')
            myth_figure = story.get('myth_figure', '')
            myth_story = story.get('myth_story', '')
            words = story.get('words', [])

            myth_content_lines.append(f"**【{i}】{myth_figure}** — <font color='grey'>{myth_source}</font>")
            myth_content_lines.append(f"　📜 {myth_story}")

            for j, w in enumerate(words, 1):
                word = w.get('word', '')
                etymology_link = w.get('etymology_link', '')
                memory_anchor = w.get('memory_anchor', '')
                myth_content_lines.append(f"　🔗 **{word}** — <font color='grey'>{etymology_link}</font>")
                myth_content_lines.append(f"　　💡 {memory_anchor}")

            myth_content_lines.append("")

        if myth_closing_line:
            myth_content_lines.append(f"🎯 **串记**：{myth_closing_line}")

        elements.append(build_collapsible(
            f"🔮 神话记忆法（{len(myth_stories)}个故事 · {total_myth_words}个词）",
            myth_content_lines,
            default_expanded=False
        ))

    # ===== 底部统计 =====
    elements.append(build_hr())
    stats_parts = []
    if total_high_freq > 0:
        stats_parts.append(f"高频难词 {total_high_freq} 个")
    stats_parts.append(f"词根 {root_count} 组")
    stats_parts.append(f"学习单词 {total_words} 个")
    if myth_stories:
        total_myth_words = sum(len(s.get('words', [])) for s in myth_stories)
        stats_parts.append(f"神话故事 {len(myth_stories)} 个 · {total_myth_words} 词")
    elements.append(build_note(
        f"今日学习：{' | '.join(stats_parts)} | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"💪 每天进步一点点！"
    ))

    return wrap_card(header, elements)


# ---------- 15. 无英文邮件通知卡片 ----------
def build_empty_english_card(date_str):
    """无英文邮件时的通知卡片"""
    header = build_card_header(
        f"📚 今日英文邮件学习 | {date_str}",
        template='grey'
    )

    elements = [
        build_markdown("## 📭 今日无英文邮件"),
        build_markdown("休息一下吧～明天继续加油！💪"),
        build_hr(),
        build_note(
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
            f"每日英文邮件学习助手"
        ),
    ]

    return wrap_card(header, elements)


# ---------- 16. 国债期货四因子周报 ----------
def build_bond_futures_card(date_str, result):
    """国债期货四因子周报卡片

    金字塔信息层级：头部标题+核心结论 → 四因子得分概览 → 折叠区（各因子明细+LLM研判）

    Args:
        date_str: 日期字符串，如 '2026-07-11'
        result: dict with keys:
            - total_score: 总分 (0-100)
            - direction: 方向判断（看多/震荡偏多/震荡观望/看空）
            - position: 仓位建议
            - emoji: 方向emoji
            - policy_score: 政策信号评分
            - fund_score: 资金信号评分
            - sentiment_score: 情绪信号评分
            - stock_bond_score: 股债信号评分
            - policy_detail: 政策信号明细说明
            - fund_detail: 资金信号明细说明
            - sentiment_detail: 情绪信号明细说明
            - stock_bond_detail: 股债信号明细说明
            - coverage_percent: 数据覆盖率
            - missing_items: 缺失数据项列表
            - llm_analysis: LLM研判文本（可选）
    """
    total_score = result.get('total_score', 50)
    direction = result.get('direction', '中性')
    position = result.get('position', '')
    emoji = result.get('emoji', '🟡')

    # 卡片头部颜色
    if '看多' in direction:
        template = 'green'
    elif '看空' in direction:
        template = 'red'
    elif '偏多' in direction:
        template = 'turquoise'
    elif '观望' in direction:
        template = 'orange'
    else:
        template = 'yellow'

    header = build_card_header(f"📈 国债期货四因子周报 | {date_str}", template=template)

    elements = []

    # ===== 第一块：核心结论 =====
    elements.append(build_markdown(
        f"## {emoji} **{direction}**\n"
        f"**建议仓位**: {position}\n"
        f"**综合评分**: **{total_score}** / 100"
    ))
    elements.append(build_hr())

    # ===== 第二块：四因子得分概览 =====
    elements.append(build_section_header("🎯 四因子得分概览"))

    factor_metrics = [
        {"label": "📜 政策信号 (30%)", "value": f"{result.get('policy_score', 0):.1f}",
         "status": _score_status(result.get('policy_score', 50))},
        {"label": "💰 资金信号 (30%)", "value": f"{result.get('fund_score', 0):.1f}",
         "status": _score_status(result.get('fund_score', 50))},
        {"label": "📊 情绪信号 (20%)", "value": f"{result.get('sentiment_score', 0):.1f}",
         "status": _score_status(result.get('sentiment_score', 50))},
        {"label": "📉 股债信号 (20%)", "value": f"{result.get('stock_bond_score', 0):.1f}",
         "status": _score_status(result.get('stock_bond_score', 50))},
    ]
    elements.append(build_metric_row(factor_metrics))

    # 覆盖率
    coverage = result.get('coverage_percent', 0)
    elements.append(build_markdown(
        f"<font color='grey'>📊 数据覆盖率: {coverage}% "
        f"({result.get('fetched_items', 0)}/{result.get('total_items', 12)}项)</font>"
    ))
    elements.append(build_hr())

    # ===== 第三块：各因子明细（折叠区） =====

    # --- 政策信号明细 ---
    policy_detail = result.get('policy_detail', '')
    if policy_detail:
        policy_lines = [f"• {item}" for item in policy_detail.split('; ') if item]
        elements.append(build_collapsible(
            "📜 因子一：政策信号（30%）",
            policy_lines if policy_lines else ["暂无明细"],
            default_expanded=False
        ))
        elements.append(build_hr())

    # --- 资金信号明细 ---
    fund_detail = result.get('fund_detail', '')
    if fund_detail:
        fund_lines = [f"• {item}" for item in fund_detail.split('; ') if item]
        elements.append(build_collapsible(
            "💰 因子二：资金信号（30%）",
            fund_lines if fund_lines else ["暂无明细"],
            default_expanded=False
        ))
        elements.append(build_hr())

    # --- 情绪信号明细 ---
    sentiment_detail = result.get('sentiment_detail', '')
    if sentiment_detail:
        sentiment_lines = [f"• {item}" for item in sentiment_detail.split('; ') if item]
        elements.append(build_collapsible(
            "📊 因子三：情绪信号（20%）",
            sentiment_lines if sentiment_lines else ["暂无明细"],
            default_expanded=False
        ))
        elements.append(build_hr())

    # --- 股债信号明细 ---
    stock_bond_detail = result.get('stock_bond_detail', '')
    if stock_bond_detail:
        sb_lines = [f"• {item}" for item in stock_bond_detail.split('; ') if item]
        elements.append(build_collapsible(
            "📉 因子四：股债信号（20%）",
            sb_lines if sb_lines else ["暂无明细"],
            default_expanded=False
        ))
        elements.append(build_hr())

    # --- LLM研判摘要 ---
    llm_analysis = result.get('llm_analysis', '')
    if llm_analysis:
        # 取前300字符作为摘要展示在折叠区
        llm_summary = llm_analysis.strip()
        if len(llm_summary) > 500:
            llm_summary = llm_summary[:500] + "..."

        llm_lines = [llm_summary]
        elements.append(build_collapsible(
            "🔍 LLM 综合研判摘要",
            llm_lines,
            default_expanded=False
        ))
        elements.append(build_hr())

    # --- 缺失数据提示 ---
    missing_items = result.get('missing_items', [])
    if missing_items:
        elements.append(build_markdown(
            f"⚠️ **缺失数据 ({len(missing_items)}项)**: " +
            ", ".join(missing_items[:5]) +
            ("..." if len(missing_items) > 5 else "")
        ))
        elements.append(build_hr())

    # ===== 指标说明 =====
    elements.append(build_collapsible("📖 评分框架 & 使用方法", [
        "**四因子评分体系**（0-100分，越高越利多国债期货）：",
        "• **政策信号（30%）**：央行OMO操作方向、MLF操作、国债买卖等——政策宽松利多",
        "• **资金信号（30%）**：DR007/Shibor/CD利率/回购量——资金面宽松利多",
        "• **情绪信号（20%）**：TL期货持仓变化、期限利差、IRS利率——多头情绪利多",
        "• **股债信号（20%）**：股指涨跌幅、北向资金——股市走弱利多债市（跷跷板效应）",
        "**方向判断阈值**：>60看多，50-60震荡偏多，40-50震荡观望，<40看空",
        "**使用建议**：四因子中3个以上同向时可靠性高；政策+资金双因子共振时信号最强；关注数据覆盖率，覆盖率低于60%时谨慎参考",
    ]))

    # ===== 底部 =====
    elements.append(build_hr())
    elements.append(build_note(
        f"数据来源: 央行官网 + CFETS + AkShare + 新浪财经 | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))

    return wrap_card(header, elements)


def _score_status(score):
    """根据评分返回状态标签（利多/利空/中性）"""
    if score >= 60:
        return "🟢 偏多"
    elif score >= 50:
        return "🟡 中性"
    elif score >= 40:
        return "🟠 偏空"
    else:
        return "🔴 空"


# ---------- 17. 热点概念TOP20 ----------
def build_hot_concepts_top20_card(date_str, concepts, total_concepts=0, summary_stats=None):
    """热点概念TOP20卡片

    金字塔信息层级：头部标题 → 核心数据概览 → 折叠区（TOP20明细表）

    Args:
        date_str: 日期字符串
        concepts: list of dict with keys: name, avg_change, score, stock_count, leader_name, leader_change, reason
        total_concepts: 概念总数
        summary_stats: dict of summary statistics
    """
    if not concepts:
        template = 'grey'
    else:
        # 取第一个概念的涨幅判断整体热度
        top_change = concepts[0].get('avg_change', 0) if concepts else 0
        if top_change > 5:
            template = 'red'
        elif top_change > 3:
            template = 'orange'
        else:
            template = 'blue'

    header = build_card_header(f"🔥 热点概念 TOP20 | {date_str}", template=template)

    elements = []

    # ===== 第一块：核心数据概览 =====
    if summary_stats:
        metrics = []
        for k, v in summary_stats.items():
            metrics.append({"label": k, "value": str(v)})
        if metrics:
            # 保证最多4列
            elements.append(build_metric_row(metrics[:4]))
            elements.append(build_hr())

    # 领涨概念
    if concepts:
        top1 = concepts[0]
        elements.append(build_markdown(
            f"**🏆 今日领涨**: **{top1.get('name', '')}**  "
            f"涨幅 <font color='red'>**{top1.get('avg_change', 0):+.2f}%**</font>  "
            f"龙头: {top1.get('leader_name', '')}"
        ))
        if top1.get('reason'):
            elements.append(build_markdown(f"<font color='grey'>💡 {top1['reason']}</font>"))
    elements.append(build_hr())

    # ===== 第二块：TOP20概念明细表（折叠区） =====
    if concepts:
        table_rows = []
        for i, c in enumerate(concepts[:20]):
            change = c.get('avg_change', 0)
            change_str = f"{change:+.2f}%" if change is not None else 'N/A'
            leader_chg = c.get('leader_change')
            leader_str = f"{c.get('leader_name', '')} ({leader_chg:+.2f}%)" if leader_chg is not None else c.get('leader_name', '-')
            table_rows.append([
                i + 1,
                c.get('name', ''),
                change_str,
                str(c.get('stock_count', '-')),
                leader_str,
            ])

        # 因为表格较长，使用折叠区
        table_content_lines = []
        table_content_lines.append("| 排名 | 概念名称 | 平均涨幅 | 成分股 | 龙头股 |")
        table_content_lines.append("|:---:|:---|---:|:---:|:---|")
        for row in table_rows:
            table_content_lines.append(
                f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |"
            )

        elements.append(build_collapsible(
            f"📋 TOP{min(20, len(concepts))} 概念明细",
            table_content_lines[2:],  # 跳过表头行，build_collapsible自己有标题
            default_expanded=False
        ))
        elements.append(build_hr())

    # ===== 指标说明 =====
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**平均涨幅**：概念板块内所有成分股的算术平均涨跌幅，反映概念整体强弱",
        "**龙头股**：该板块今日涨幅最大的成分股，具有风向标意义",
        "**成分股数**：概念板块包含的股票数量，成分股适中（10-30只）的概念弹性通常更好",
        "**筛选逻辑**：基于全市场概念板块涨幅排序，取涨幅最高的前20个",
        "**使用建议**：优先关注持续多天上榜的概念；首天爆发的新概念需警惕一日游；结合板块成交量和龙头股持续性综合判断",
    ]))

    # ===== 底部 =====
    elements.append(build_hr())
    elements.append(build_note(
        f"数据来源: 新浪财经 / Tushare | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))

    return wrap_card(header, elements)


# ---------- 18. 库存信贷周期监控 ----------
# 周期轮转顺序：用于在卡片里标出"下一阶段叫什么"（飞书卡片必须让大佬一眼看到下一步）
INV_NEXT_CARD = {'主动补库存': '被动补库存', '被动补库存': '主动去库存',
                 '主动去库存': '被动去库存', '被动去库存': '主动补库存'}
CREDIT_NEXT_CARD = {'信贷扩张期': '政策收紧期', '政策收紧期': '信贷收缩期',
                    '信贷收缩期': '政策宽松期', '政策宽松期': '信贷扩张期'}


def build_cycle_monitor_card(date_str, credit_cycle, inventory_cycle, indicators=None, cycle_info=None, prediction=None):
    """库存信贷周期监控卡片

    金字塔信息层级：头部标题 → 周期定位结论 → 核心指标概览 → 折叠区（详细数据）

    Args:
        date_str: 日期字符串
        credit_cycle: 信贷周期阶段
        inventory_cycle: 库存周期阶段
        indicators: dict of indicator data {ind_id: {name, value, ma3, trend, status}}
        cycle_info: dict with cycle phase details
        prediction: str 周期预测文本
    """
    # 判断整体模板颜色
    if '扩张' in credit_cycle or '宽松' in credit_cycle:
        if '补库' in inventory_cycle:
            template = 'green'  # 复苏期
        else:
            template = 'turquoise'  # 宽松但去库
    elif '收缩' in credit_cycle or '收紧' in credit_cycle:
        if '去库' in inventory_cycle:
            template = 'red'  # 下行期
        else:
            template = 'orange'  # 收紧但补库（滞胀）
    else:
        template = 'yellow'

    header = build_card_header(f"coze.DeepSeek · 库存&信贷周期监控 | {date_str}", template=template)

    elements = []

    # ===== 第一块：周期定位结论 =====
    # 飞书 lark_md 不支持 markdown 表格（会原样渲染成 |管道符| 乱码），改用纯文本行
    _inv_next = INV_NEXT_CARD.get(inventory_cycle, '—')
    _cred_next = CREDIT_NEXT_CARD.get(credit_cycle, '—')
    elements.append(build_markdown(
        f"**▎📍 当前周期定位**\n\n"
        f"**🏦 信贷周期**：**{credit_cycle}**\n"
        f"**🏭 库存周期**：**{inventory_cycle}**\n\n"
        f"**➡️ 下一阶段（按轮转顺序）**\n"
        f"**🏦 信贷**：{_cred_next}\n"
        f"**🏭 库存**：{_inv_next}"
    ))

    # 周期组合解读
    combo_text = _cycle_combo_interpretation(credit_cycle, inventory_cycle)
    if combo_text:
        elements.append(build_markdown(f"<font color='grey'>💡 {combo_text}</font>"))

    elements.append(build_hr())

    # ===== 第二块：核心指标概览 =====
    elements.append(build_section_header("📊 核心指标概览"))

    if indicators:
        # 精选6个核心指标：社融、M2、PPI、工业增加值、PMI新订单、产成品库存
        core_inds = ['C001', 'C002', 'I004', 'I008', 'I006', 'I009']
        metric_items = []
        for ind_id in core_inds:
            if ind_id in indicators:
                ind = indicators[ind_id]
                val = ind.get('value')
                val_str = f"{val:.2f}" if val is not None else "N/A"
                trend = ind.get('trend', '')
                name = ind.get('name', ind_id)
                # 简化名称
                short_name = name.replace('同比', '').replace('指数', '')
                if len(short_name) > 8:
                    short_name = short_name[:8]
                metric_items.append({
                    "label": short_name,
                    "value": val_str,
                    "trend": trend,
                })

        if metric_items:
            # 分两行展示（每行3个）
            elements.append(build_metric_row(metric_items[:3]))
            if len(metric_items) > 3:
                elements.append(build_metric_row(metric_items[3:6]))
    elements.append(build_hr())

    # ===== 第三块：详细数据（折叠区） =====

    # --- 信贷周期指标 ---
    if indicators:
        credit_lines = []
        credit_ids = ['C001', 'C002', 'C003', 'C004', 'C005', 'C006', 'C007', 'C008']
        for ind_id in credit_ids:
            if ind_id in indicators:
                ind = indicators[ind_id]
                val = ind.get('value')
                val_str = f"{val:.2f}" if val is not None else "N/A"
                ma3 = ind.get('ma3')
                ma3_str = f"{ma3:.2f}" if ma3 is not None else "N/A"
                trend = ind.get('trend', '')
                status = ind.get('status', '')
                name = ind.get('name', ind_id)
                credit_lines.append(f"• **{name}**: {val_str} (3月均: {ma3_str}) {trend} <font color='grey'>{status}</font>")

        if credit_lines:
            elements.append(build_collapsible(
                "🏦 信贷周期指标明细",
                credit_lines,
                default_expanded=False
            ))
            elements.append(build_hr())

        # --- 库存周期指标 ---
        inv_lines = []
        inv_ids = ['I003', 'I004', 'I005', 'I006', 'I007', 'I008', 'I009', 'I010']
        for ind_id in inv_ids:
            if ind_id in indicators:
                ind = indicators[ind_id]
                val = ind.get('value')
                val_str = f"{val:.2f}" if val is not None else "N/A"
                ma3 = ind.get('ma3')
                ma3_str = f"{ma3:.2f}" if ma3 is not None else "N/A"
                trend = ind.get('trend', '')
                status = ind.get('status', '')
                name = ind.get('name', ind_id)
                inv_lines.append(f"• **{name}**: {val_str} (3月均: {ma3_str}) {trend} <font color='grey'>{status}</font>")

        if inv_lines:
            elements.append(build_collapsible(
                "🏭 库存周期指标明细",
                inv_lines,
                default_expanded=False
            ))
            elements.append(build_hr())

        # --- 交叉验证指标 ---
        cross_lines = []
        cross_ids = ['V001', 'V002', 'V003']
        for ind_id in cross_ids:
            if ind_id in indicators:
                ind = indicators[ind_id]
                val = ind.get('value')
                val_str = f"{val:.2f}" if val is not None else "N/A"
                name = ind.get('name', ind_id)
                meaning = ind.get('meaning', '')
                cross_lines.append(f"• **{name}**: {val_str} <font color='grey'>{meaning}</font>")

        if cross_lines:
            elements.append(build_collapsible(
                "🔍 交叉验证指标",
                cross_lines,
                default_expanded=False
            ))
            elements.append(build_hr())

    # --- 周期尾声预测 ---
    if prediction:
        pred_lines = [line for line in prediction.split('\n') if line.strip()]
        elements.append(build_collapsible(
            "🔮 周期尾声预测",
            pred_lines,
            default_expanded=False
        ))
        elements.append(build_hr())

    # ===== 指标说明 =====
    elements.append(build_collapsible("📖 周期框架 & 使用方法", [
        "**信贷周期四阶段**：政策宽松→信贷扩张→政策收紧→信贷收缩，循环往复",
        "**库存周期四阶段**：主动补库→被动补库→主动去库→被动去库，约3-4年一轮",
        "**周期组合意义**：",
        "  • 信贷扩张+主动补库 = 📈 复苏繁荣期（顺周期共振）",
        "  • 信贷扩张+被动去库 = 🌱 复苏初期（政策先行，经济待起）",
        "  • 信贷收缩+被动补库 = ⚠️ 滞胀风险（需求收缩，库存被动上升）",
        "  • 信贷收缩+主动去库 = 📉 下行衰退期（逆周期共振）",
        "**使用建议**：周期定位用于大类资产配置决策；复苏期增配权益，衰退期增配债券；关注周期尾声信号，提前布局下一轮",
    ]))

    # ===== 底部 =====
    elements.append(build_hr())
    elements.append(build_note(
        f"数据来源: 央行 + 统计局 + AkShare | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))

    return wrap_card(header, elements)


def _cycle_combo_interpretation(credit_cycle, inventory_cycle):
    """根据信贷+库存周期组合给出一句话解读"""
    if '扩张' in credit_cycle or '宽松' in credit_cycle:
        if '主动补库' in inventory_cycle:
            return "顺周期共振：信贷扩张+企业补库，经济复苏动能较强，权益资产偏多"
        elif '被动补库' in inventory_cycle:
            return "主动补库过渡：政策宽松传导至企业端，但需求端尚待确认，震荡偏多"
        elif '主动去库' in inventory_cycle:
            return "错位周期：政策已宽松但企业仍在去库，可能处于复苏初期，耐心等待"
        elif '被动去库' in inventory_cycle:
            return "复苏前夜：政策宽松+库存去化见底，需求即将回升，可左侧布局"
    elif '收缩' in credit_cycle or '收紧' in credit_cycle:
        if '主动去库' in inventory_cycle:
            return "逆周期共振：信贷收缩+企业去库，经济下行压力较大，防御为主"
        elif '被动去库' in inventory_cycle:
            return "下行后期：政策未松但库存已见底，等待政策信号，谨慎观望"
        elif '主动补库' in inventory_cycle:
            return "滞胀风险：信用收缩但企业补库，可能是需求端收缩前的最后补库"
        elif '被动补库' in inventory_cycle:
            return "需求收缩：政策收紧+被动补库，经济下行压力显现，降低风险敞口"
    return ""


# ---------- 19. A股选股器每周简报 ----------
def build_weekly_report_card(date_str, report_data):
    """A股选股器每周简报卡片

    金字塔信息层级：头部标题 → 本周筛选概览 → 行业分布TOP → 高频入选股票 → 完整报告（折叠）

    Args:
        date_str: 日期字符串，如 '2026-07-13'
        report_data: dict with keys:
            - week_range: str, 如 '2026-07-07 至 2026-07-11'
            - total_days: int, 有效天数
            - total_filtered: int, 总筛选记录数（去重前）
            - unique_stocks: int, 去重后股票数
            - avg_daily: float, 日均筛选数
            - hit_rate: float, 平均命中率%
            - industry_top: list of {name, count, percent}
            - frequent_stocks: list of {code, name, industry, appear_days}
            - continuous_stocks: list of {code, name, industry, appear_days}
            - report_path: str, 完整报告路径（可选）
    """
    week_range = report_data.get('week_range', '')
    total_filtered = report_data.get('total_filtered', 0)
    avg_daily = report_data.get('avg_daily', 0)
    hit_rate = report_data.get('hit_rate', 0)
    unique_stocks = report_data.get('unique_stocks', 0)
    total_days = report_data.get('total_days', 0)

    # 判断整体热度
    if hit_rate > 5:
        template = 'red'  # 筛选数量多，市场热度高
    elif hit_rate > 2:
        template = 'orange'
    else:
        template = 'blue'

    header = build_card_header(f"📈 A股选股器每周简报 | {date_str}", template=template)

    elements = []

    # ===== 第一块：本周筛选概览 =====
    elements.append(build_markdown(
        f"**报告周期**: {week_range}\n"
        f"**有效交易日**: {total_days} 天"
    ))

    # 核心指标
    metric_items = [
        {"label": "总筛选记录", "value": str(total_filtered),
         "status": f"<font color='grey'>条</font>"},
        {"label": "去重股票数", "value": str(unique_stocks),
         "status": f"<font color='grey'>只</font>"},
        {"label": "日均筛选", "value": f"{avg_daily:.0f}",
         "status": f"<font color='grey'>只/天</font>"},
        {"label": "平均命中率", "value": f"{hit_rate:.3f}%",
         "status": f"<font color='grey'>占全市场</font>"},
    ]
    elements.append(build_metric_row(metric_items))
    elements.append(build_hr())

    # ===== 第二块：行业分布TOP =====
    industry_top = report_data.get('industry_top', [])
    if industry_top:
        elements.append(build_section_header("🏭 行业分布 TOP"))
        
        industry_rows = []
        for i, ind in enumerate(industry_top[:8]):
            industry_rows.append([
                i + 1,
                ind.get('name', ''),
                str(ind.get('count', 0)),
                f"{ind.get('percent', 0):.1f}%",
            ])
        
        elements.append(build_data_table(
            ['排名', '行业', '股票数', '占比'],
            industry_rows,
        ))
        elements.append(build_hr())

    # ===== 第三块：高频入选股票 =====
    frequent_stocks = report_data.get('frequent_stocks', [])
    continuous_stocks = report_data.get('continuous_stocks', [])
    
    # 合并高频股
    if frequent_stocks and continuous_stocks:
        elements.append(build_section_header("🔥 高频入选股票（连续3天以上）"))
        
        freq_rows = []
        for i, s in enumerate(frequent_stocks[:10]):
            freq_rows.append([
                i + 1,
                s.get('code', ''),
                s.get('name', ''),
                s.get('industry', ''),
                str(s.get('appear_days', 0)),
            ])
        
        elements.append(build_data_table(
            ['#', '代码', '名称', '行业', '入选天数'],
            freq_rows,
        ))
        elements.append(build_hr())
    elif continuous_stocks:
        elements.append(build_section_header("🔥 连续入选股票"))
        
        cont_rows = []
        for i, s in enumerate(continuous_stocks[:10]):
            cont_rows.append([
                i + 1,
                s.get('code', ''),
                s.get('name', ''),
                s.get('industry', ''),
                str(s.get('appear_days', 0)),
            ])
        
        elements.append(build_data_table(
            ['#', '代码', '名称', '行业', '连续天数'],
            cont_rows,
        ))
        elements.append(build_hr())

    # ===== 第四块：完整报告（折叠区） =====
    report_path = report_data.get('report_path', '')
    if report_path:
        elements.append(build_collapsible(
            "📋 完整报告说明",
            [
                f"• 完整Markdown报告已生成：{report_path}",
                "• 包含：每日筛选明细、行业分布分析、市值分布分析、典型案例分析、市场趋势观察",
                "• 筛选条件：近1年涨幅前20% + 近半年涨幅前20% + 近1月涨幅前15% + 当年涨幅前100名",
                "• 排除ST/停牌/上市不满一年次新股，市值门槛50亿以上",
            ],
            default_expanded=False
        ))
        elements.append(build_hr())

    # ===== 底部 =====
    elements.append(build_note(
        f"数据来源: 恒生聚源数据库 | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))

    return wrap_card(header, elements)


# ---------- 20. A股强势股选股器 ----------
def build_astock_screener_card(date_str, stocks, total_count, sample_size, filters_info=None):
    """A股强势股选股器卡片（四重筛选）

    金字塔信息层级：头部标题 → 核心指标概览 → TOP10表格 → 筛选阈值说明（折叠）

    Args:
        date_str: 日期字符串，如 '2026-07-15'
        stocks: list of dict with keys: code, name, price, year_ret, half_ret, month_ret, ytd_ret
        total_count: 筛选出的股票总数
        sample_size: 参与筛选的样本量（有效数据股票数）
        filters_info: dict of filter thresholds, e.g. {'year_ret_p80': xx, 'half_ret_p80': xx, ...}
    """
    # 卡片颜色：筛选数量多→市场强→绿色，少→黄色
    hit_rate = (total_count / sample_size * 100) if sample_size > 0 else 0
    if hit_rate > 3:
        template = 'green'
    elif hit_rate > 1:
        template = 'blue'
    else:
        template = 'yellow'

    header = build_card_header(f"📊 A股强势股选股器 | {date_str}", template=template)

    elements = []

    # ===== 第一块：核心指标概览 =====
    metric_items = [
        {"label": "筛选结果", "value": str(total_count),
         "status": f"<font color='grey'>只</font>"},
        {"label": "样本量", "value": str(sample_size),
         "status": f"<font color='grey'>只</font>"},
        {"label": "命中率", "value": f"{hit_rate:.2f}%",
         "status": f"<font color='grey'>占样本</font>"},
    ]
    elements.append(build_metric_row(metric_items))
    elements.append(build_hr())

    # ===== 第二块：TOP10 股票表格 =====
    if stocks:
        top_n = min(10, len(stocks))
        stock_lines = []
        stock_lines.append(f"**🏆 TOP{top_n} 强势股**")
        stock_lines.append("")
        for idx, s in enumerate(stocks[:top_n]):
            month_ret = s.get('month_ret')
            year_ret = s.get('year_ret')
            ytd_ret = s.get('ytd_ret')
            price = s.get('price', 0)
            name = _warn_stock_name(s.get('name', ''), ytd_ret)
            
            def _fmt(val):
                if val is None: return 'N/A'
                color = 'red' if val >= 0 else 'green'
                sign = '+' if val >= 0 else ''
                return f"<font color='{color}'>{sign}{val:.1f}%</font>"
            
            line = f"**{idx+1}. {name}**　¥{price:.2f}　月{_fmt(month_ret)}　年{_fmt(year_ret)}　当年{_fmt(ytd_ret)}"
            stock_lines.append(line)
        
        elements.append(build_markdown("\n".join(stock_lines)))
        elements.append(build_hr())

    # ===== 第三块：筛选阈值说明（折叠区） =====
    if filters_info:
        filter_lines = []
        # 四重筛选逻辑说明
        filter_lines.append("**四重筛选条件**：")
        p80_year = filters_info.get('year_ret_p80')
        p80_half = filters_info.get('half_ret_p80')
        p85_month = filters_info.get('month_ret_p85')
        top100_min = filters_info.get('ytd_top100_min')

        if p80_year is not None:
            filter_lines.append(f"• 近1年涨幅前20%: ≥ {p80_year:.2f}%")
        if p80_half is not None:
            filter_lines.append(f"• 近半年涨幅前20%: ≥ {p80_half:.2f}%")
        if p85_month is not None:
            filter_lines.append(f"• 近1月涨幅前15%: ≥ {p85_month:.2f}%")
        if top100_min is not None:
            filter_lines.append(f"• 当年涨幅前100名: ≥ {top100_min:.2f}%")

        filter_lines.append("")
        filter_lines.append("**前置筛选**：")
        filter_lines.append("• 排除ST、退市股")
        filter_lines.append("• 排除上市不满1年次新股")
        filter_lines.append("• 市值≥50亿")

        elements.append(build_collapsible(
            "📖 筛选阈值说明",
            filter_lines,
            default_expanded=False
        ))
        elements.append(build_hr())

    # ===== 底部 =====
    elements.append(build_note(
        f"数据来源: Tushare | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))

    return wrap_card(header, elements)



# -*- coding: utf-8 -*-


def _risk_card_template(indices):
    """根据万得全A综合评级返回卡片头部配色"""
    wanquan = indices.get('万得全A', {})
    rating_level = wanquan.get('rating_level', 2)
    if rating_level >= 4:
        return 'red'
    elif rating_level >= 3:
        return 'orange'
    elif rating_level >= 2:
        return 'yellow'
    elif rating_level >= 1:
        return 'green'
    return 'turquoise'


def _risk_forecast_sections(date_str, indices, conclusion, top_risks=None, top_opps=None,
                            style_drift=None, is_midday=False, timing_data=None,
                            margin_data=None, data_warnings=None):
    """A股风险预测日报——分段渲染（2026-10-22 三维口径重构）

    与旧单卡版差异：
      1. 彻底移除衍生品定价风险（维度指标/对比表列/模块一/指标说明全部删除）；
         期权 PCR 仅在"三维择时监控"中独立呈现，不参与综合评级。
      2. 三维加权：结构 40% / 量能情绪 33.3% / 极端 26.7%。
      3. 模块三量能表新增"成交额/成交量分位"列（六指数 data_hub 三级降级补齐）。
      4. 三维择时监控无条件常显（数据不可用时空态提示）。

    返回 dict: 每个 key 是一个元素 list，供单卡/多卡组装复用。
    """
    top_risks = top_risks or []
    top_opps = top_opps or []
    data_warnings = data_warnings or []

    wanquan = indices.get('万得全A', {})
    rating_level = wanquan.get('rating_level', 2)

    S = {k: [] for k in (
        'warnings', 'summary', 'risks', 'opps', 'compare',
        'structure', 'sentiment', 'extreme', 'style', 'margin', 'timing', 'notes')
    }

    def P(key, *els):
        for e in els:
            S[key].append(e)

    # 标签高亮（沿用旧逻辑）
    def _hl(text, value_on_newline=True):
        s = str(text)
        sep = None
        if value_on_newline:
            if '<br>' in s:
                sep = '<br>'
            elif '\n' in s:
                sep = '\n'

        def _judge_color(label_text):
            t = str(label_text)
            if '🔴' in t:
                return 'red', True
            if '🟠' in t:
                return 'orange', False
            if '🟡' in t:
                return None, False
            if '🟢' in t or '✅' in t:
                return 'green', False
            for p in ['高风险', '极端风险', '极度风险', '深度贴水', '极度超卖']:
                if p in t:
                    return 'red', True
            for p in ['偏高', '偏空', '偏深', '恶化']:
                if p in t:
                    return 'orange', False
            for p in ['低风险', '极低', '安全', '健康', '支撑']:
                if p in t:
                    return 'green', False
            if '高' in t and '低' not in t and '不高' not in t:
                return 'red', True
            if '低' in t and '高' not in t:
                return 'green', False
            return None, False

        if sep is None:
            color, has_star = _judge_color(s)
            if color == 'red':
                star = '⭐ ' if has_star else ''
                return f"<font color='red'>{star}{s}</font>"
            elif color == 'green':
                return f"<font color='green'>{s}</font>"
            elif color == 'orange':
                return f"<font color='orange'>{s}</font>"
            return s
        parts = s.split(sep, 1)
        value_part = parts[0]
        label_part = parts[1] if len(parts) > 1 else ''
        color, has_star = _judge_color(label_part)
        if color == 'red':
            star = '⭐ ' if has_star else ''
            return f"{value_part}{sep}<font color='red'>{star}{label_part}</font>"
        elif color == 'green':
            return f"{value_part}{sep}<font color='green'>{label_part}</font>"
        elif color == 'orange':
            return f"{value_part}{sep}<font color='orange'>{label_part}</font>"
        return s

    core_indices = ['沪深300', '中证500', '中证1000', '创业板指', '科创50', '万得全A']

    # ===== warnings =====
    if data_warnings:
        P('warnings', build_note("\n".join(data_warnings), tag="warning"), build_hr())

    # ===== summary：结论 + 三维维度指标 =====
    if rating_level >= 3:
        P('summary', build_markdown(f"## <font color='red'><font size='large'>{conclusion}</font></font>"))
    else:
        P('summary', build_markdown(f"## {conclusion}"))

    ds = wanquan.get('dim_scores', {})
    dim_metrics = []
    for dim_key, dim_name, icon in [
        ('structural', '结构', '🏗️'),
        ('sentiment', '量能情绪', '💧'),
        ('extreme', '极端', '⚠️'),
    ]:
        s = ds.get(dim_key)
        if s is not None:
            if s >= 0.5:
                status = '🔴高'
            elif s >= 0.2:
                status = '🟠偏高'
            elif s >= -0.2:
                status = '🟡中'
            elif s >= -0.5:
                status = '🟢低'
            else:
                status = '✅极低'
            dim_metrics.append({"label": f"{icon} {dim_name}", "value": f"{s:+.2f}", "status": status})
        else:
            dim_metrics.append({"label": f"{icon} {dim_name}", "value": "N/A", "status": "⚪缺失"})
    P('summary', build_metric_row(dim_metrics), build_hr())

    # ===== risks：TOP3 =====
    P('risks', build_section_header("⚠️ 核心风险信号 TOP 3"))
    if top_risks:
        risk_lines = []
        for i, r in enumerate(top_risks[:3]):
            risk_lines.append(
                f"⭐ **{i+1}. {r.get('index','')} · {r.get('factor','')}**  \n"
                f"   数值: {r.get('value','')} → <font color='red'><font size='large'>**{r.get('label','')}**</font></font>  \n"
                f"   <font color='grey'>维度: {r.get('dimension','')}</font>"
            )
        P('risks', build_markdown("\n\n".join(risk_lines)))
    else:
        P('risks', build_markdown("✅ **当前无显著风险信号**"))
    P('risks', build_hr())

    # ===== opps：机会信号（全部展示，不再只取4条导致截断） =====
    P('opps', build_section_header("✅ 潜在机会信号"))
    if top_opps:
        opp_lines = []
        for o in top_opps:
            opp_lines.append(
                f"**{o.get('index','')} · {o.get('factor','')}**: {o.get('value','')} → <font color='green'>{o.get('label','')}</font>  \n"
                f"<font color='grey'>维度: {o.get('dimension','')}</font>"
            )
        P('opps', build_markdown("\n\n".join(opp_lines)))
    else:
        P('opps', build_markdown("⚪ **暂无明确机会信号**"))
    P('opps', build_hr())

    # ===== compare：各指数风险对比（三维，删衍生品列） =====
    P('compare', build_section_header("📈 各指数风险对比"))
    idx_rows = []
    for idx_name in core_indices:
        idx = indices.get(idx_name, {})
        if not idx or not idx.get('factors'):
            continue
        rating = idx.get('rating', 'N/A')
        score = idx.get('total_score', 0)
        idx_ds = idx.get('dim_scores', {})
        is_high_risk_row = score >= 1.0 or '🔴' in rating

        def _fmt(text, red=False, green=False, star=False):
            prefix = '⭐ ' if star else ''
            if red:
                return f"<font color='red'>{prefix}{text}</font>"
            elif green:
                return f"<font color='green'>{prefix}{text}</font>"
            return f"{prefix}{text}" if prefix else text

        row = [_fmt(idx_name, red=is_high_risk_row), _fmt(rating, red=is_high_risk_row)]
        score_red = is_high_risk_row or score >= 1.0
        score_green = score < 0 and not score_red
        row.append(_fmt(f"{score:+.2f}", red=score_red, green=score_green))
        for dim in ['structural', 'sentiment', 'extreme']:
            sval = idx_ds.get(dim)
            if sval is not None:
                dim_text = f"{sval:+.2f}"
                if sval >= 0.5:
                    row.append(_fmt(dim_text, red=True, star=True))
                elif sval < 0:
                    row.append(_fmt(dim_text, green=True))
                elif is_high_risk_row:
                    row.append(_fmt(dim_text, red=True))
                else:
                    row.append(dim_text)
            else:
                row.append('-')
        idx_rows.append(row)
    if idx_rows:
        P('compare', build_data_table(['指数', '评级', '综合', '结构', '量能', '极端'], idx_rows))
    P('compare', build_hr())

    # ===== 模块一：结构风险 =====
    struct_indices = [i for i in core_indices if indices.get(i, {}).get('structural_count', 0) > 0]
    if struct_indices:
        cl = []
        cfc_indices = [i for i in struct_indices if any(k in indices[i].get('factors', {}) for k in ['CFC', 'up50', 'top5z', 'CDI'])]
        if cfc_indices:
            if is_midday:
                cl.append("**结构三因子（午盘版：up50 / top5z / CDI聚类 / 结构因子）**")
                cl.append("")
                cl.append("| 指数 | up50 | top5z | CDI聚类 |")
                cl.append("|------|------|-------|-----|")
                for idx_name in cfc_indices:
                    f = indices[idx_name].get('factors', {})
                    def _v(fk, f=f):
                        info = f.get(fk, {})
                        val = info.get('value', '-')
                        lab_s = str(info.get('label', ''))
                        for kw in ['极度集中', '极端风险', '高风险', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                            if kw in lab_s:
                                return f"<font color='red'>⭐ {val}</font>"
                        for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                            if kw in lab_s:
                                return f"<font color='green'>{val}</font>"
                        return str(val)
                    cl.append(f"| {idx_name} | {_v('up50')} | {_v('top5z')} | {_v('CDI')} |")
                cl.append("")
                cl.append("<font color='grey'>💡 午盘版CFC已移除（离散度公式与收盘版峰度+偏度口径不一致，无参考价值）</font>")
                cl.append("")
            else:
                cl.append("**CFC / up50 / top5z / CDI聚类**")
                cl.append("")
                cl.append("| 指数 | CFC | up50 | top5z | CDI聚类 |")
                cl.append("|------|-----|------|-------|-----|")
                for idx_name in cfc_indices:
                    f = indices[idx_name].get('factors', {})
                    def _v(fk, f=f):
                        info = f.get(fk, {})
                        val = info.get('value', '-')
                        lab_s = str(info.get('label', ''))
                        for kw in ['极度集中', '极端风险', '高风险', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                            if kw in lab_s:
                                return f"<font color='red'>⭐ {val}</font>"
                        for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                            if kw in lab_s:
                                return f"<font color='green'>{val}</font>"
                        return str(val)
                    cl.append(f"| {idx_name} | {_v('CFC')} | {_v('up50')} | {_v('top5z')} | {_v('CDI')} |")
                cl.append("")
            cl.append("---")
            cl.append("**📖 怎么看结构因子**")
            cl.append("")
            if not is_midday:
                cl.append("**CFC（横截面因子）**——抱团集中度，看赛道拥挤不拥挤")
                cl.append("• \>1.5 → 极度抱团 ⚠️ 见顶风险；• \<-1.5 → 极度分散，往往是大底信号")
                cl.append("")
            cl.append("**up50（50日线占比）**——多少只股票在50日均线上方，\>80%过热，\&lt;20%超跌")
            cl.append("**top5z（前5%成交额占比）**——\>50%资金抱团龙头小票失血；\&lt;15%市场无主线")
            cl.append("**CDI聚类（聚类离散度）**——\>2.0板块撕裂；\&lt;0.5同涨同跌")
            cl.append("")
            cl.append("⭐ = 高风险预警标记（极度/极端等级）")
            cl.append("")

        sf_indices = [i for i in struct_indices if any(k in indices[i].get('factors', {}) for k in ['AbsFactor', 'RelFactor'])]
        if sf_indices:
            cl.append("**结构因子（AbsFactor / RelFactor）**")
            cl.append("")
            cl.append("| 指数 | AbsFactor | 多空解读 | RelFactor | 资金解读 |")
            cl.append("|------|-----------|----------|-----------|----------|")

            def _parse_sigma(val_str):
                if not val_str or val_str in ('-', 'N/A'):
                    return None
                try:
                    return float(val_str.replace('σ', '').replace(' ', ''))
                except (ValueError, AttributeError):
                    return None

            def _abs_interpret(abs_val):
                if abs_val is None:
                    return '<font color="grey">暂无</font>'
                if abs_val >= 1.5:
                    return '<font color="red">多头力量强，接近极端</font>'
                elif abs_val >= 1.0:
                    return '<font color="orange">多头趋势中</font>'
                elif abs_val >= 0.3:
                    return '<font color="green">温和偏多</font>'
                elif abs_val > -0.3:
                    return '⚖️ 多空平衡'
                elif abs_val > -1.0:
                    return '<font color="green">温和偏空</font>'
                elif abs_val > -1.5:
                    return '<font color="orange">空头趋势中</font>'
                return '<font color="red">空头力量强，接近极端，可能反转</font>'

            def _rel_interpret(rel_val):
                if rel_val is None:
                    return '<font color="grey">暂无</font>'
                if rel_val >= 1.5:
                    return '<font color="red">资金大幅流出⚠️</font>'
                elif rel_val >= 0.8:
                    return '<font color="orange">资金流出中</font>'
                elif rel_val >= 0.3:
                    return '资金小幅流出'
                elif rel_val > -0.3:
                    return '⚖️ 资金平衡'
                elif rel_val > -0.8:
                    return '资金小幅流入'
                elif rel_val > -1.5:
                    return '<font color="green">资金流入中</font>'
                return '<font color="green">资金大幅流入🔥</font>'

            def _sf_color(info):
                val = info.get('value', '-')
                lab_s = str(info.get('label', ''))
                for kw in ['极度集中', '极端风险', '高风险', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买']:
                    if kw in lab_s:
                        return f"<font color='red'>⭐ {val}</font>"
                for kw in ['低', '安全', '健康', '正常', '不足', '冷清']:
                    if kw in lab_s:
                        return f"<font color='green'>{val}</font>"
                return str(val)

            for idx_name in sf_indices:
                f = indices[idx_name].get('factors', {})
                abs_info = f.get('AbsFactor', {})
                rel_info = f.get('RelFactor', {})
                abs_val = _parse_sigma(abs_info.get('value', ''))
                rel_val = _parse_sigma(rel_info.get('value', ''))
                cl.append(f"| {idx_name} | {_sf_color(abs_info)} | {_abs_interpret(abs_val)} | {_sf_color(rel_info)} | {_rel_interpret(rel_val)} |")
            cl.append("")
            cl.append("---")
            cl.append("<font color='grey'>💡 Abs看方向（正多负空，±1.5σ近极端）；Rel看资金（负=流入，正=流出）。同向趋势稳，反向要变盘</font>")

        if cl:
            module_title = "🏗️ 模块一：结构风险（up50 / top5z / CDI / 结构因子）" if is_midday else "🏗️ 模块一：结构风险（CFC / CDI聚类 / 结构因子）"
            P('structure', build_module_block(module_title, cl), build_hr())

    # ===== 模块二：量能情绪（含成交额/成交量分位列） =====
    sent_indices = [i for i in core_indices if indices.get(i, {}).get('sentiment_count', 0) > 0]
    if sent_indices:
        cl = ["| 指数 | 成交额/量分位 | 成分股集中度 | 涨/跌家数 |",
              "|------|------------|--------------|----------|"]
        for idx_name in sent_indices:
            f = indices[idx_name].get('factors', {})
            vol_info = f.get('成交额分位') or f.get('成交量分位') or {}
            def _vs(info):
                val = info.get('value', '-')
                lab_s = str(info.get('label', ''))
                for kw in ['极度集中', '极端风险', '高风险', '预警', '泡沫', '过热', '极度拥挤', '严重拥挤', '见顶', '极度超买', '抱团偏强', '普跌', '恐慌']:
                    if kw in lab_s:
                        return f"<font color='red'>⭐ {val}</font>"
                for kw in ['低', '安全', '健康', '正常', '不足', '冷清', '均衡', '涨多', '分散']:
                    if kw in lab_s:
                        return f"<font color='green'>{val}</font>"
                return str(val)
            # 六大宽基=成分股上涨/下跌家数；万得全A=全市场涨停/跌停家数
            breadth = f.get('涨跌家数比') or f.get('全市场涨跌停比') or f.get('涨跌停比') or {}
            cl.append(f"| {idx_name} | {_vs(vol_info)} | {_vs(f.get('成交额集中度', {}))} | {_vs(breadth)} |")
        cl.append("")
        cl.append("<font color='grey'>注：集中度=成分股成交额前5占比；涨/跌家数为成分股口径，万得全A行为全市场上涨/下跌家数。</font>")
        P('sentiment', build_module_block("💧 模块二：量能情绪风险（成交额/量分位 / 成分股集中度 / 涨跌家数）", cl), build_hr())

    # ===== 模块三：极端风险（LPPL） =====
    ext_indices = [i for i in core_indices if indices.get(i, {}).get('extreme_count', 0) > 0]
    if ext_indices:
        cl = ["| 指数 | LPPL泡沫信号 |", "|------|-------------|"]
        for idx_name in ext_indices:
            f = indices[idx_name].get('factors', {})
            lppl = f.get('LPPL泡沫', {}).get('label', '-')
            cl.append(f"| {idx_name} | {_hl(lppl, value_on_newline=False)} |")
        P('extreme', build_module_block("⚠️ 模块三：极端风险（LPPL泡沫检测）", cl), build_hr())

    # ===== 风格漂移 =====
    if style_drift:
        drift_label = style_drift.get('label', '')
        drift_advice = style_drift.get('advice', '')
        z_opt = style_drift.get('z_opt', 0)
        zone = style_drift.get('zone', '')
        z_txt = f"{z_opt:.3f}" if isinstance(z_opt, (int, float)) else (str(z_opt) if z_opt is not None else 'N/A')
        dl = [
            f"**风格漂移综合分（Z_opt）**: {z_txt}  {zone} {drift_label}",
            f"**操作建议**: {drift_advice}",
        ]
        deep = style_drift.get('deep_analysis', {})
        def _fmt_iv(av):
            # 统一 IV 展示口径：库内存小数(0.4384)，历史可能存百分数；≤1.5 视为小数转百分数
            try:
                v = float(av)
                return f"{v*100:.1f}%" if abs(v) <= 1.5 else f"{v:.1f}%"
            except (TypeError, ValueError):
                return str(av)
        def _iv_line(label, d, key_lv, key_abs):
            lv = d.get(key_lv)
            av = d.get(key_abs)
            if lv is None or av is None:
                return f"**{label} IV水平**: ⚪ 数据不足"
            return f"**{label} IV水平**: {lv} ({_fmt_iv(av)})"
        if deep:
            dl.append("")
            dl.append(_iv_line("科创50", deep, 'iv_level_科创50', 'iv_abs_科创50'))
            dl.append(_iv_line("创业板", deep, 'iv_level_创业板', 'iv_abs_创业板'))
            dl.append(_iv_line("沪深300", deep, 'iv_level_沪深300', 'iv_abs_沪深300'))
        P('style', build_module_block("🎨 风格漂移监控", dl), build_hr())

    # ===== 两融风险监控（个股Top10已移至次日08:00两融早报，此处只留汇总） =====
    if margin_data and margin_data.get('risk_level') != 'unknown':
        td = margin_data.get('trade_date', '')
        dt_str = f"{td[:4]}-{td[4:6]}-{td[6:]}" if len(td) == 8 else td
        risk = margin_data.get('risk_level', 'low')
        risk_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(risk, '⚪')
        ml = [margin_data.get('advice', ''), ""]
        rzye = margin_data.get('_rzye_raw')
        circ = margin_data.get('total_circ_mv_yi')
        ratio = margin_data.get('margin_circ_ratio')
        chg = margin_data.get('rzye_chg', {})
        accel = margin_data.get('acceleration')
        consec = margin_data.get('consecutive_decline_days', 0)
        mp = []
        if rzye:
            mp.append(f"融资余额 {rzye / 1e8:.0f}亿")
        if circ:
            mp.append(f"流通市值 {circ / 10000:.1f}万亿" if circ > 10000 else f"流通市值 {circ:.0f}亿")
        if ratio is not None:
            mp.append(f"融资/流通 {ratio:.3f}% {margin_data.get('ratio_trend', '→')}")
        if mp:
            ml.append(" | ".join(mp))
        cp = []
        for n in (5, 10, 20):
            v = chg.get(n)
            if v is not None:
                arrow = "↑" if v > 0 else "↓" if v < 0 else "→"
                cp.append(f"{n}日{v * 100:+.2f}%{arrow}")
        if cp:
            ml.append("")
            ml.append(f"**变化率**: {' | '.join(cp)}")
        if accel is not None:
            at = '⚡加速流出' if accel < -0.003 else ('减速中' if accel > 0.003 else '平稳')
            ml.append(f"**加速度**: {accel * 100:+.3f}% ({at})")
        if consec > 0:
            ml.append(f"**连续净偿还**: {consec}天")
        if margin_data.get('chain_risk', False):
            ml.append("")
            ml.append(f"🔴 **强平连锁风险** 高杠杆股集体加速偿还（{len(margin_data.get('stress_stocks', []))}只）")
        if margin_data.get('leverage_exit', False):
            ml.append(f"🔴 **杠杆资金撤退** 融资/成交额 {margin_data.get('rz_buy_ratio', 0):.2f}%（骤降）")
        near_clear = margin_data.get('near_clearing', False)
        rebound = margin_data.get('rebound_candidates', [])
        bottom = margin_data.get('bottom_divergence', False)
        style_sw = margin_data.get('style_switch', False)
        if near_clear or rebound or bottom or style_sw:
            ml.append("")
            op = []
            if near_clear:
                op.append("🟢出清接近尾声")
            if rebound:
                op.append(f"🟢反弹候选{len(rebound)}只")
            if bottom:
                op.append("🟢底部确认")
            if style_sw:
                op.append("🟢风格切换")
            ml.append(" | ".join(op))
        if rebound:
            ml.append("<font color='grey'>个股超跌反弹Top10见次日08:00《两融风险监控早报》⑥</font>")
        if margin_data.get('signal_227'):
            ml.append("")
            ml.append("⚠️ **227%担保比例等效信号已触发**")
        for w in margin_data.get('data_warnings', []):
            ml.append("")
            ml.append(f"> {w}")
        P('margin', build_module_block(f"{risk_emoji} 两融风险监控 | {dt_str}", ml), build_hr())

    # ===== 三维择时监控（无条件常显，空态也展示栏目） =====
    if timing_data and timing_data.get('data_available'):
        dims = timing_data['dimensions']
        comp = timing_data['composite']
        advice = comp.get('advice', {})
        dim_info = [
            ('etf_flow', '① ETF资金流'),
            ('crowding', '② 市场拥挤度'),
            ('pcr', '③ 期权情绪(PCR)'),
            ('sector_holding', '④ 板块持仓'),
            ('margin', '⑤ 杠杆水平'),
            ('volume_price', '⑥ 量价关系'),
        ]
        tl = []
        rating = comp.get('rating', '⚪ 数据不足')
        tl.append(f"**择时综合评级：{rating}**")
        tl.append("")
        g = comp.get('green_count', 0)
        y = comp.get('yellow_count', 0)
        r = comp.get('red_count', 0)
        m = comp.get('missing_count', 0)
        stats = []
        if g > 0:
            stats.append(f"🟢看多 {g}")
        if y > 0:
            stats.append(f"🟡中性 {y}")
        if r > 0:
            stats.append(f"🔴看空 {r}")
        if m > 0:
            stats.append(f"⚪缺失 {m}")
        tl.append("维度分布：" + " ｜ ".join(stats))
        tl.append("")
        tl.append("**各维度信号**")
        tl.append("")
        tl.append("| 维度 | 信号 | 简述 |")
        tl.append("|------|------|------|")
        for dim_key, dim_name in dim_info:
            dim = dims.get(dim_key, {})
            label = dim.get('label', '⚪ 缺失')
            summary = dim.get('summary', '') or '—'
            if len(summary) > 25:
                summary = summary[:24] + '…'
            tl.append(f"| {dim_name} | {label} | {summary} |")
        pos = advice.get('position', '')
        idx_a = advice.get('index_advice', '')
        sec_a = advice.get('sector_advice', '')
        if pos or idx_a or sec_a:
            tl.append("")
            tl.append("**📋 操作建议**")
            tl.append("")
            if pos:
                tl.append(f"• **整体仓位**：{pos}")
            if idx_a:
                tl.append(f"• **宽基指数**：{idx_a}")
            if sec_a:
                tl.append(f"• **热门板块**：{sec_a}")
        head_rating = rating.split()[0] if ' ' in rating else rating
        P('timing', build_module_block(f"📊 三维择时监控  {head_rating}", tl), build_hr())
    else:
        tl = [
            "**择时综合评级：⚪ 数据暂不可用**",
            "",
            "<font color='grey'>三维择时（ETF资金流 / 拥挤度 / 期权PCR / 板块持仓 / 杠杆 / 量价）"
            "本时段数据缺失或尚未生成，收盘后更新。</font>",
            "<font color='grey'>注：期权 PCR 在此独立模块呈现，不参与上方综合风险评级。</font>",
        ]
        P('timing', build_module_block("📊 三维择时监控  ⚪", tl), build_hr())

    # ===== notes：指标说明（三维口径，删衍生品） =====
    P('notes', build_collapsible("📖 指标说明 & 使用方法", [
        "**综合风险评级**：基于结构(40%)+量能情绪(33.3%)+极端风险(26.7%)三维加权，分数范围-1~+1，正值越高风险越大（2026-10-22起移除衍生品维度）",
        "**结构风险**：" + ("up50均线占比、top5z成交集中度、CDI聚类离散度、AbsFactor/RelFactor结构因子（午盘版无CFC）" if is_midday else "CFC横截面偏度峰度、up50均线占比、top5z成交集中度、CDI聚类离散度、AbsFactor/RelFactor结构因子"),
        "**量能情绪风险**：六大宽基成交额/成交量分位（data_hub三级降级：AKShare→Tushare→网页）、成交额集中度、涨跌停比、吸收率、兴登堡指标等",
        "**极端风险**：LPPL对数周期幂律模型，检测市场是否处于泡沫末期，高质量拟合且临界日临近时风险极高",
        "**三维择时监控**：ETF资金流/拥挤度/期权PCR/板块持仓/杠杆/量价六维独立择时模型，与综合风险评级并行参考，期权PCR只在此呈现",
        "**使用建议**：综合评级>0.5时降低仓位防御；<-0.3时可积极进攻；重点关注TOP3风险信号的边际变化；多模块共振时可靠性最高",
    ]))
    P('notes', build_hr())
    P('notes', build_collapsible("📌 图例说明", [
        "🔴 / 🟠 / 🟡 / 🟢 = 风险等级（高 / 偏高 / 中性 / 低）",
        "⭐ = 高风险预警标记（极度/极端等级）",
        "CDI聚类 = 聚类离散度版CDI（衡量市场分化程度）",
        "成交额/量分位 = 指数自身近250日量能的相对分位，越高越放量",
    ]))

    return {'sections': S, 'template': _risk_card_template(indices)}


def _risk_footer(is_midday):
    if is_midday:
        src = "数据来源: 新浪财经实时行情 + 东财涨停池 + data_hub | 午盘实时数据"
    else:
        src = "数据来源: data_hub(AKShare/Tushare/网页) + 交易所"
    return build_note(
        f"{src} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 案例分析学习材料，不构成投资建议"
    )


def build_risk_forecast_cards(date_str, indices, conclusion, top_risks=None, top_opps=None,
                              style_drift=None, is_midday=False, timing_data=None,
                              margin_data=None, data_warnings=None):
    """A股风险预测日报——多卡片版本（防飞书单卡过长截断）。

    返回 list[card_dict]，按顺序推送，每张均带独立标题：
      卡1 总览：综合评级 + TOP风险 + 机会信号 + 各指数对比 + 指标说明
      卡2 模块明细：结构 / 量能情绪 / 极端 / 风格漂移
      卡3 附加监控：两融 + 三维择时（常显）
    若某张卡没有实质内容则自动跳过（卡1恒在）。
    """
    bundle = _risk_forecast_sections(
        date_str, indices, conclusion, top_risks=top_risks, top_opps=top_opps,
        style_drift=style_drift, is_midday=is_midday, timing_data=timing_data,
        margin_data=margin_data, data_warnings=data_warnings)
    S = bundle['sections']
    template = bundle['template']
    suffix = " 🌞 午盘版" if is_midday else ""
    prefix = "⚠️ " if (data_warnings or None) else ""

    def _header(part):
        return build_card_header(f"{prefix}📊 A股风险预测日报{suffix} | {date_str} · {part}", template=template)

    cards = []

    # 卡1 总览
    e1 = []
    for k in ('warnings', 'summary', 'risks', 'opps', 'compare', 'notes'):
        e1.extend(S[k])
    e1.append(_risk_footer(is_midday))
    cards.append(wrap_card(_header("总览"), e1))

    # 卡2 模块明细
    e2 = []
    for k in ('structure', 'sentiment', 'extreme', 'style'):
        e2.extend(S[k])
    if e2:
        e2.append(_risk_footer(is_midday))
        cards.append(wrap_card(_header("风险模块明细"), e2))

    # 卡3 两融 + 三维择时
    e3 = []
    for k in ('margin', 'timing'):
        e3.extend(S[k])
    if e3:
        e3.append(_risk_footer(is_midday))
        cards.append(wrap_card(_header("两融 & 三维择时"), e3))

    return cards



if __name__ == '__main__':
    # 测试：生成一个结构因子日报卡片
    test_data = [
        {'name': '沪深300', 'abs_factor': -1.20, 'abs_status': '🔵强空头', 'rel_factor': 0.16, 'rel_status': '🟡流出放缓', 'overall': '⚪观望', 'close': 3800},
        {'name': '中证500', 'abs_factor': -1.01, 'abs_status': '🔵强空头', 'rel_factor': -0.49, 'rel_status': '🟢流入放缓', 'overall': '⚪观望', 'close': 5500},
        {'name': '中证1000', 'abs_factor': -1.02, 'abs_status': '🔵强空头', 'rel_factor': -0.18, 'rel_status': '🟢流入放缓', 'overall': '⚪观望', 'close': 6000},
        {'name': '科创50', 'abs_factor': -1.38, 'abs_status': '🔵强空头', 'rel_factor': -0.16, 'rel_status': '🟢流入放缓', 'overall': '⚪观望', 'close': 900},
        {'name': '创业板指', 'abs_factor': -0.82, 'abs_status': '🟢温和空头', 'rel_factor': -0.35, 'rel_status': '🟢流入放缓', 'overall': '⚪观望', 'close': 2100},
    ]
    card = build_structural_factor_card('2026-07-07', test_data)
    print(json.dumps(card, ensure_ascii=False, indent=2))
