#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告构建: 飞书卡片格式
- 开仓/平仓信号 (增量, 状态变化时推送)
- 持仓汇总 (收盘后)
- 开盘全量报告 (每次开盘时, 全量推送当前所有持仓+可开仓配对)
- 每个品种后标注板块内动量排名 ①②③
"""

from typing import List, Dict, Optional
from datetime import datetime

from ..config.config import SYMBOL_NAME, CONTRACT_MULTIPLIER
from .risk_filter import calc_sizes, estimate_pair_cost


# 状态emoji
SIGNAL_EMOJI = {
    "ALERT_LONG": "🟡",
    "ALERT_SHORT": "🟡",
    "ENTRY_LONG": "🟢",
    "ENTRY_SHORT": "🔴",
    "CLOSE_LONG": "⬜",
    "CLOSE_SHORT": "⬜",
    "HOLD": "🔵",
    "FLAT": "⚪",
}

# 圈号数字 1-20
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _circled(rank: int) -> str:
    """返回圈号数字字符串, rank从1开始"""
    if 1 <= rank <= 20:
        return CIRCLED[rank - 1]
    return f"({rank})"


# ===== 板块内动量排名 =====

def compute_board_momentum_rank(results: List[dict]) -> Dict[str, int]:
    """
    按板块计算所有品种的5日动量排名
    返回: {symbol: rank} (rank从1开始, 1=动量最强)
    """
    # 收集每个板块的品种和动量
    board_moments = {}  # {board: [(symbol, pct), ...]}
    for r in results:
        if r is None:
            continue
        board = r.get("board", "其他")
        leg_a, leg_b = r["leg_a"], r["leg_b"]
        pct_a = r.get("momentum_pct_a", 0)
        pct_b = r.get("momentum_pct_b", 0)

        if board not in board_moments:
            board_moments[board] = {}
        # 同一品种可能在多对中出现, 取最新值
        board_moments[board][leg_a] = pct_a
        board_moments[board][leg_b] = pct_b

    # 排名
    rankings = {}
    for board, sym_pcts in board_moments.items():
        sorted_syms = sorted(sym_pcts.items(), key=lambda x: x[1], reverse=True)
        for rank, (sym, _) in enumerate(sorted_syms, 1):
            rankings[sym] = rank
    return rankings


# ===== 单行格式化 =====

def _format_pair_line(result: dict, rankings: Dict[str, int] = None,
                      lots_a: int = 0, lots_b: int = 0,
                      cost: float = 0) -> str:
    """格式化单对结果为一行文本, 品种名后加板块内动量排名圈号"""
    pair_id = result["pair_id"]
    leg_a = result["leg_a"]
    leg_b = result["leg_b"]
    name_a = SYMBOL_NAME.get(leg_a, leg_a)
    name_b = SYMBOL_NAME.get(leg_b, leg_b)
    signal = result["signal"]
    z = result["z"]
    value = result["value"]
    calc_method = result["calc_method"]
    pct = result.get("pct_rank")
    close_a = result.get("close_a", 0)
    close_b = result.get("close_b", 0)
    mom_pct_a = result.get("momentum_pct_a", 0)
    mom_pct_b = result.get("momentum_pct_b", 0)

    emoji = SIGNAL_EMOJI.get(signal, "⚪")

    # 星级: ⭐=关注(超出2σ未回归) ⭐⭐⭐⭐⭐=回归中(可开仓/已持仓)
    stars = _get_stars(result)

    # 品种名 + 动量排名圈号
    if rankings:
        rank_a = rankings.get(leg_a)
        rank_b = rankings.get(leg_b)
        name_a_str = f"{name_a}{_circled(rank_a)}" if rank_a else name_a
        name_b_str = f"{name_b}{_circled(rank_b)}" if rank_b else name_b
    else:
        name_a_str = name_a
        name_b_str = name_b

    # 价差/比价显示
    if calc_method == "spread":
        value_str = f"{value:+.0f}"
        method_label = "价差"
    else:
        value_str = f"{value:.4f}"
        method_label = "比价"

    # 分位
    pct_str = f"{pct:.0%}" if pct is not None else "N/A"

    # 动量百分比 (带方向)
    def _mom_str(pct_val):
        if pct_val > 0:
            return f"+{pct_val*100:.1f}%"
        elif pct_val < 0:
            return f"{pct_val*100:.1f}%"
        return "0%"

    line = f"{emoji} **{pair_id}**: {name_a_str} - {name_b_str}"
    if stars:
        line += f"  {stars}"
    line += f"\n    {method_label}={value_str} | z={z:+.2f} | 分位={pct_str}"
    line += f"\n    动量: {name_a}{_mom_str(mom_pct_a)} / {name_b}{_mom_str(mom_pct_b)}"

    if lots_a > 0 and lots_b > 0:
        line += f" | 手数 {leg_a}×{lots_a} {leg_b}×{lots_b}"

    if cost > 0:
        line += f" | 成本≈{cost:.0f}元"

    return line


def _calc_lots(result: dict):
    """计算等名义手数"""
    leg_a, leg_b = result["leg_a"], result["leg_b"]
    close_a = result.get("close_a", 0)
    close_b = result.get("close_b", 0)
    lots_a, lots_b = calc_sizes(
        type("P", (), {"leg_a": leg_a, "leg_b": leg_b,
                       "multiplier_a": CONTRACT_MULTIPLIER.get(leg_a, 10),
                       "multiplier_b": CONTRACT_MULTIPLIER.get(leg_b, 10)})(),
        close_a, close_b
    )
    cost = estimate_pair_cost(
        type("P", (), {"leg_a": leg_a, "leg_b": leg_b})(),
        close_a, close_b, lots_a, lots_b
    )
    return lots_a, lots_b, cost


# ===== 开仓/平仓信号报告 =====

def build_signal_report(results: List[dict],
                        state: Dict[str, int],
                        rankings: Dict[str, int] = None) -> List[tuple]:
    """
    构建信号报告 (飞书卡片格式)
    只推送有变化的信号: entry(新开仓) 和 close(平仓)
    返回: [(title, content), ...]
    """
    if not results:
        return []

    messages = []
    entry_signals = []
    close_signals = []

    for r in results:
        if r is None:
            continue
        signal = r.get("signal", "FLAT")
        if signal in ("ENTRY_LONG", "ENTRY_SHORT"):
            entry_signals.append(r)
        elif signal in ("CLOSE_LONG", "CLOSE_SHORT"):
            close_signals.append(r)

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 开仓信号
    if entry_signals:
        lines = [f"**📊 价差回归·开仓信号** ({now_ts})\n"]
        for r in entry_signals:
            leg_a, leg_b = r["leg_a"], r["leg_b"]
            lots_a, lots_b, cost = _calc_lots(r)

            lines.append(_format_pair_line(r, rankings, lots_a, lots_b, cost))

            direction = r["direction"]
            z = r["z"]
            name_a = SYMBOL_NAME.get(leg_a, leg_a)
            name_b = SYMBOL_NAME.get(leg_b, leg_b)
            if direction == 1:
                lines.append(f"    → 做多价差: 买{name_a} 卖{name_b}")
            elif direction == -1:
                lines.append(f"    → 做空价差: 卖{name_a} 买{name_b}")
            lines.append(f"    z={z:+.2f}, 预期向均值回归\n")

        content = "\n".join(lines)
        messages.append(("价差回归-开仓信号", content))

    # 平仓信号
    if close_signals:
        lines = [f"**📊 价差回归·平仓信号** ({now_ts})\n"]
        for r in close_signals:
            lines.append(_format_pair_line(r, rankings))
            lines.append(f"    → z回到中性区域, 建议平仓\n")

        content = "\n".join(lines)
        messages.append(("价差回归-平仓信号", content))

    return messages


# ===== 预警提示报告 (|z|≥2σ, 仅提示) =====

def build_alert_report(results: List[dict],
                       state: Dict[str, int],
                       rankings: Dict[str, int] = None) -> List[tuple]:
    """
    构建预警报告: |z|≥2σ 但尚未开始回归
    标记 ⭐ 表示关注中, 等回归开始(⭐⭐⭐⭐⭐)才开仓
    """
    if not results:
        return []

    alerts = [r for r in results if r and r.get("signal") in ("ALERT_LONG", "ALERT_SHORT")]
    if not alerts:
        return []

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"**📊 价差回归·预警提示 ⭐** ({now_ts})\n"]
    lines.append("以下配对已超出±2σ, 持续关注中:\n")

    for r in alerts:
        leg_a, leg_b = r["leg_a"], r["leg_b"]
        z = r["z"]
        name_a = SYMBOL_NAME.get(leg_a, leg_a)
        name_b = SYMBOL_NAME.get(leg_b, leg_b)

        lines.append(_format_pair_line(r, rankings))
        if z > 0:
            lines.append(f"    ⭐ z偏高, 等待回落 → 回归后做空价差: 卖{name_a} 买{name_b}")
        else:
            lines.append(f"    ⭐ z偏低, 等待回升 → 回归后做多价差: 买{name_a} 卖{name_b}")
        lines.append("")

    lines.append("*⭐=关注中(尚未回归) | 回归开始后发⭐⭐⭐⭐⭐开仓信号*")

    return [("价差回归-预警提示", "\n".join(lines))]


def _get_stars(result: dict) -> str:
    """根据信号返回星级显示"""
    signal = result.get("signal", "FLAT")
    if signal in ("ALERT_LONG", "ALERT_SHORT"):
        return "⭐"
    elif signal in ("ENTRY_LONG", "ENTRY_SHORT", "HOLD"):
        return "⭐⭐⭐⭐⭐"
    return ""


# ===== 持仓汇总 (收盘后) =====

def build_summary_report(results: List[dict],
                         state: Dict[str, int],
                         rankings: Dict[str, int] = None) -> Optional[tuple]:
    """
    构建收盘持仓汇总报告
    包含所有 holding + entry 的配对
    """
    if not results:
        return None

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"**📊 价差回归·持仓汇总** ({now_ts})\n"]

    active_pairs = []
    for r in results:
        if r is None:
            continue
        direction = r.get("direction", 0)
        signal = r.get("signal", "FLAT")
        alert = r.get("alert", 0)
        if direction != 0 or alert != 0 or signal in ("HOLD", "ENTRY_LONG", "ENTRY_SHORT"):
            active_pairs.append(r)

    if not active_pairs:
        lines.append("当前无活跃配对(全部空仓)")
        return ("价差回归-持仓汇总", "\n".join(lines))

    # 按板块分组
    board_groups = {}
    for r in active_pairs:
        board = r.get("board", "其他")
        if board not in board_groups:
            board_groups[board] = []
        board_groups[board].append(r)

    for board, pairs in board_groups.items():
        lines.append(f"\n**【{board}】**")
        for r in pairs:
            lots_a, lots_b, _ = _calc_lots(r)
            lines.append(_format_pair_line(r, rankings, lots_a, lots_b))

    lines.append(f"\n---")
    lines.append("*z≥2.0开仓, z≤0.5平仓 | 圈号=板块内5日动量排名(①最强)*")

    return ("价差回归-持仓汇总", "\n".join(lines))


# ===== 开盘全量报告 =====

def build_opening_report(results: List[dict],
                         state: Dict[str, int],
                         rankings: Dict[str, int] = None) -> Optional[tuple]:
    """
    开盘时全量报告: 当前所有持仓 + 所有可开仓(触发±2σ)的配对
    不管增量变化, 全量展示一遍
    """
    if not results:
        return None

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"**📊 价差回归·开盘持仓** ({now_ts})\n"]

    # 1. 当前持仓 (direction != 0, 正在做回归)
    holding_pairs = [r for r in results if r and r.get("direction", 0) != 0]
    # 2. 刚触发 entry (本轮新开仓信号)
    entry_pairs = [r for r in results if r and r.get("signal") in ("ENTRY_LONG", "ENTRY_SHORT")]
    # 3. 预警关注中 (alert!=0, |z|≥2σ, 等回归) ⭐
    alert_pairs = [r for r in results if r and r.get("direction", 0) == 0
                   and r.get("alert", 0) != 0]
    # 4. 接近阈值 (1.5σ ≤ |z| < 2σ, 观望)
    watch_pairs = [r for r in results if r and r.get("direction", 0) == 0
                   and r.get("alert", 0) == 0
                   and r.get("signal") == "FLAT" and 1.5 <= abs(r.get("z", 0)) < 2.0]

    if holding_pairs:
        lines.append("**🔵 当前持仓**")
        for r in holding_pairs:
            lots_a, lots_b, _ = _calc_lots(r)
            lines.append(_format_pair_line(r, rankings, lots_a, lots_b))
            direction = r["direction"]
            z = r["z"]
            name_a = SYMBOL_NAME.get(r["leg_a"], r["leg_a"])
            name_b = SYMBOL_NAME.get(r["leg_b"], r["leg_b"])
            if direction == 1:
                lines.append(f"    → 持有多头价差: 买{name_a} 卖{name_b}")
            elif direction == -1:
                lines.append(f"    → 持有空头价差: 卖{name_a} 买{name_b}")
            # 回归进度
            entry_z = 2.0
            exit_z = 0.5
            abs_z = abs(z)
            if abs_z >= entry_z:
                progress = "0%"
                tip = "仍深偏离, 继续持有"
            elif abs_z <= exit_z:
                progress = "100%"
                tip = "建议平仓"
            else:
                progress = f"{(entry_z - abs_z) / (entry_z - exit_z) * 100:.0f}%"
                tip = f"回归中, 距平仓线还有 {abs_z - exit_z:.1f}σ"
            lines.append(f"    回归进度: {progress} — {tip}\n")
    else:
        lines.append("**🔵 当前持仓**: 无\n")

    if entry_pairs:
        lines.append("**🟢 新开仓信号 (|z|≥2.0)**")
        for r in entry_pairs:
            lots_a, lots_b, cost = _calc_lots(r)
            lines.append(_format_pair_line(r, rankings, lots_a, lots_b, cost))
            direction = r["direction"]
            z = r["z"]
            name_a = SYMBOL_NAME.get(r["leg_a"], r["leg_a"])
            name_b = SYMBOL_NAME.get(r["leg_b"], r["leg_b"])
            if direction == 1:
                lines.append(f"    → 做多价差: 买{name_a} 卖{name_b}")
            elif direction == -1:
                lines.append(f"    → 做空价差: 卖{name_a} 买{name_b}")
            lines.append(f"    z={z:+.2f}, 预期向均值回归\n")

    if alert_pairs:
        lines.append("**🟡 关注中 ⭐ (|z|≥2σ, 等待回归)**")
        for r in alert_pairs:
            lots_a, lots_b, cost = _calc_lots(r)
            lines.append(_format_pair_line(r, rankings, lots_a, lots_b, cost))
            z = r["z"]
            alert_dir = r.get("alert", 0)
            name_a = SYMBOL_NAME.get(r["leg_a"], r["leg_a"])
            name_b = SYMBOL_NAME.get(r["leg_b"], r["leg_b"])
            if alert_dir == -1:  # z偏高, 等回落 → 做空价差
                lines.append(f"    ⭐ z偏高, 回落时做空价差: 卖{name_a} 买{name_b}")
            elif alert_dir == 1:  # z偏低, 等回升 → 做多价差
                lines.append(f"    ⭐ z偏低, 回升时做多价差: 买{name_a} 卖{name_b}")
            lines.append("")

    if watch_pairs:
        lines.append("**⚪ 接近阈值 (1.5≤|z|<2.0)**")
        for r in watch_pairs:
            lines.append(_format_pair_line(r, rankings))
        lines.append("")

    # 统计
    lines.append(f"---")
    total_entry = len(entry_pairs)
    total_alert = len(alert_pairs)
    total_hold = len(holding_pairs)
    total_watch = len(watch_pairs)
    lines.append(f"持仓{total_hold}对 | ⭐关注{total_alert}对 | 新开仓{total_entry}对 | 接近阈值{total_watch}对")
    lines.append("*⭐=关注中等回归 | ⭐⭐⭐⭐⭐=回归中可开仓/已持仓 | 圈号=动量排名*")

    return ("价差回归-开盘持仓", "\n".join(lines))


# ===== 全量扫描 (独立运行时) =====

def build_full_scan_report(results: List[dict],
                           rankings: Dict[str, int] = None) -> Optional[tuple]:
    """
    全量扫描报告(独立运行时使用)
    显示所有配对的当前状态
    """
    if not results:
        return None

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"**📊 价差回归·全量扫描** ({now_ts})\n"]

    # 按板块分组
    board_groups = {}
    for r in results:
        if r is None:
            continue
        board = r.get("board", "其他")
        if board not in board_groups:
            board_groups[board] = []
        board_groups[board].append(r)

    for board in ["农产品", "贵金属", "化工"]:
        pairs = board_groups.get(board, [])
        if not pairs:
            continue
        lines.append(f"\n**【{board}】**")
        for r in pairs:
            lots_a, lots_b, _ = _calc_lots(r)
            lines.append(_format_pair_line(r, rankings, lots_a, lots_b))

    # 统计
    entry_count = sum(1 for r in results if r and r["signal"] in ("ENTRY_LONG", "ENTRY_SHORT"))
    hold_count = sum(1 for r in results if r and r["signal"] == "HOLD")
    close_count = sum(1 for r in results if r and r["signal"] in ("CLOSE_LONG", "CLOSE_SHORT"))
    flat_count = sum(1 for r in results if r and r["signal"] == "FLAT")

    lines.append(f"\n---")
    lines.append(f"统计: 🟢开仓{entry_count} | 🔵持仓{hold_count} | ⬜平仓{close_count} | ⚪空仓{flat_count}")
    lines.append("*z≥2.0开仓, z≤0.5平仓 | 圈号=板块内5日动量排名(①最强)*")

    return ("价差回归-全量扫描", "\n".join(lines))
