#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股周度资金流向与微盘择时联合简报
整合国金周度资金流监控 + 微盘择时模型，生成联合周报

Usage (positional args):
    python3 weekly_combined_report.py <result_mode> [end_date] [push_feishu] [output_dir]

Params order: result_mode end_date push_feishu output_dir
    result_mode: display_only | no_reply | notify
    end_date: YYYY-MM-DD or "last_friday" (default)
    push_feishu: true | false (default true)
    output_dir: path (default: /Coze/Drive/两融季节同比/reports)

产物:
    - 联合周报 Markdown → output_dir/A股联合周报_YYYYMMDD.md
    - 微盘择时 JSON → micro_timing_cache/
    - 有信号切换时：邮件 + 飞书提醒
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()


import os
import sys
import json
import time
import smtplib
import subprocess
import traceback
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 路径配置
# ============================================================
SCRIPT_DIR = Path(_cp("/Coze/Drive/两融季节同比/codeact/scripts"))
PROJECT_DIR = Path(_cp("/Coze/Drive/两融季节同比"))
CACHE_DIR = PROJECT_DIR / "codeact" / "output" / "micro_timing_cache"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "reports"
FEISHU_PUSH_SCRIPT = SCRIPT_DIR / "feishu_push.py"

# 邮件配置
EMAIL_TO = "410119229@qq.com"
EMAIL_FROM = "moergen@coze.email"
SMTP_HOST = "smtp.coze.email"
SMTP_PORT = 465

# 飞书配置
FEISHU_CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"

# 确保 scripts 在 sys.path 中（用于 import micro_cap_timing 的纯函数）
sys.path.insert(0, str(SCRIPT_DIR))


# ============================================================
# 参数解析 (位置参数协议)
# ============================================================
def parse_args():
    """位置参数: result_mode end_date push_feishu output_dir"""
    args = sys.argv[1:]
    result_mode = args[0] if len(args) > 0 else "display_only"
    end_date = args[1] if len(args) > 1 else "last_friday"
    push_feishu = args[2] if len(args) > 2 else "true"
    output_dir = args[3] if len(args) > 3 else None
    return result_mode, end_date, push_feishu.lower() == "true", output_dir


# ============================================================
# 日期工具
# ============================================================
def get_last_friday():
    """获取上一个周五的日期"""
    today = datetime.now().date()
    offset = (today.weekday() - 4) % 7
    if offset == 0:
        offset = 7  # 如果今天是周五，取上周五
    return today - timedelta(days=offset)


def get_week_range(friday_date):
    """根据周五日期计算周一~周五的范围"""
    friday = friday_date if isinstance(friday_date, datetime) else datetime.combine(friday_date, datetime.min.time())
    monday = friday.date() - timedelta(days=friday.date().weekday())
    return monday, friday.date()


def format_date_yyyymmdd(d):
    """日期格式化为 YYYYMMDD"""
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    return d.strftime("%Y%m%d")


def format_date_display(d):
    """日期格式化为 MM月DD日"""
    if isinstance(d, str):
        return d
    return d.strftime("%m月%d日")


def get_iso_week_label(d):
    """获取 ISO 周标签，如 2026年第42周"""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    elif isinstance(d, datetime):
        d = d.date()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}年第{iso_week}周"


# ============================================================
# 模块1: 调用国金周度资金流 (subprocess)
# ============================================================
def run_capital_flow(end_date_str, output_dir):
    """
    调用 weekly_capital_flow.py 生成国金资金流报告。
    返回 (report_text, report_path) 或 (None, None) 如果失败。
    """
    print("\n" + "=" * 50)
    print("📊 [1/4] 运行国金周度资金流监控...")
    print("=" * 50)

    script_path = str(SCRIPT_DIR / "weekly_capital_flow.py")
    if not os.path.exists(script_path):
        print(f"  ❌ 脚本不存在: {script_path}")
        return None, None

    cmd = [
        sys.executable, script_path,
        "display_only",     # result_mode
        end_date_str,       # end_date
        "false",            # push_feishu (不单独推送，由联合脚本统一推送)
        str(output_dir),    # output_dir
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        print(f"  [stdout] (最后5行):")
        for line in result.stdout.strip().split('\n')[-5:]:
            print(f"    {line}")

        if result.returncode != 0:
            print(f"  ❌ 执行失败 (exit={result.returncode})")
            if result.stderr:
                print(f"  [stderr] {result.stderr[:300]}")
            return None, None

        # 找到生成的报告文件
        date_tag = end_date_str.replace("-", "")
        report_path = os.path.join(str(output_dir), f"国金资金流周报_{date_tag}.md")
        if os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                report_text = f.read()
            print(f"  ✅ 国金报告已读取: {report_path} ({len(report_text)}字)")
            return report_text, report_path
        else:
            # 尝试查找最新的报告文件
            import glob
            pattern = os.path.join(str(output_dir), "国金资金流周报_*.md")
            files = sorted(glob.glob(pattern))
            if files:
                latest = files[-1]
                with open(latest, 'r', encoding='utf-8') as f:
                    report_text = f.read()
                print(f"  ✅ 使用最新国金报告: {latest}")
                return report_text, latest
            print("  ❌ 未找到国金报告文件")
            return None, None

    except subprocess.TimeoutExpired:
        print("  ❌ 执行超时(600s)")
        return None, None
    except Exception as e:
        print(f"  ❌ 执行异常: {e}")
        return None, None


# ============================================================
# 模块2: 加载微盘择时数据 (cache)
# ============================================================
def load_micro_timing_data():
    """
    从 cache 加载微盘择时的合并数据。
    返回 (merged_df, breadth_df, flow_df, json_output) 或全 None。
    """
    print("\n" + "=" * 50)
    print("📊 [2/4] 加载微盘择时数据...")
    print("=" * 50)

    merged_path = CACHE_DIR / "merged_scores.parquet"
    breadth_path = CACHE_DIR / "breadth_scores.parquet"
    flow_path = CACHE_DIR / "fund_flow_scores.parquet"

    if not merged_path.exists():
        print(f"  ❌ merged_scores.parquet 不存在，需先运行 micro_cap_timing.py")
        return None, None, None, None

    merged = pd.read_parquet(merged_path)
    merged["trade_date"] = merged["trade_date"].astype(str)
    print(f"  ✅ merged_scores: {len(merged)} 条 ({merged['trade_date'].min()}~{merged['trade_date'].max()})")

    breadth = None
    if breadth_path.exists():
        breadth = pd.read_parquet(breadth_path)
        breadth["trade_date"] = breadth["trade_date"].astype(str)
        print(f"  ✅ breadth_scores: {len(breadth)} 条")

    flow = None
    if flow_path.exists():
        flow = pd.read_parquet(flow_path)
        flow["trade_date"] = flow["trade_date"].astype(str)
        print(f"  ✅ fund_flow_scores: {len(flow)} 条")

    # 查找最新的 JSON 输出
    json_output = None
    json_pattern = str(PROJECT_DIR / "微盘择时_*_data.json")
    import glob
    json_files = sorted(glob.glob(json_pattern))
    if json_files:
        with open(json_files[-1], 'r', encoding='utf-8') as f:
            json_output = json.load(f)
        print(f"  ✅ JSON数据: {json_files[-1]}")

    return merged, breadth, flow, json_output


# ============================================================
# 模块3: 信号切换检测 (import 纯函数)
# ============================================================
def detect_all_switches(merged):
    """
    检测所有类型的信号切换。
    返回 dict 包含切换检测结果。
    """
    print("\n" + "=" * 50)
    print("📊 [3/4] 检测信号切换...")
    print("=" * 50)

    results = {
        "intra_week_switches": [],
        "cross_week_switch": None,
        "switch_zones": [],
        "current_week_dates": [],
        "prev_week_dates": [],
        "has_switch": False,
    }

    try:
        from micro_cap_timing import (
            detect_intra_week_switches,
            detect_cross_week_switch,
            detect_switch_zone,
            get_current_week_dates,
        )
    except ImportError as e:
        print(f"  ⚠️ 无法 import micro_cap_timing 的切换检测函数: {e}")
        return results

    # 确定当前周和上周日期
    current_week_dates, prev_week_dates = get_current_week_dates(merged)
    results["current_week_dates"] = current_week_dates
    results["prev_week_dates"] = prev_week_dates

    if not current_week_dates:
        print("  ⚠️ 无法确定当前周日期")
        return results

    current_week_data = merged[
        merged["trade_date"].isin(current_week_dates)
    ].sort_values("trade_date").copy()

    prev_week_data = merged[
        merged["trade_date"].isin(prev_week_dates)
    ].sort_values("trade_date").copy() if prev_week_dates else pd.DataFrame()

    # 1. 周内切换
    results["intra_week_switches"] = detect_intra_week_switches(current_week_data)

    # 2. 跨周切换
    if len(prev_week_dates) > 0:
        results["cross_week_switch"] = detect_cross_week_switch(
            merged, current_week_dates, prev_week_dates
        )

    # 3. 切换区间
    results["switch_zones"] = detect_switch_zone(merged, min_days=3)

    # 判断是否有切换
    has_switch = (
        len(results["intra_week_switches"]) > 0
        or results["cross_week_switch"] is not None
    )
    results["has_switch"] = has_switch

    # 日志
    if results["cross_week_switch"]:
        sw = results["cross_week_switch"]
        print(f"  ⚡ 跨周切换: {sw['from_signal']} → {sw['to_signal']} (日期 {sw['date']})")
    if results["intra_week_switches"]:
        for sw_date, from_s, to_s in results["intra_week_switches"]:
            print(f"  ⚡ 周内切换: {sw_date} {from_s} → {to_s}")
    if not has_switch:
        print("  ✅ 本周无信号切换")

    return results


# ============================================================
# 模块4: 生成联合周报
# ============================================================
def extract_capital_flow_sections(report_text):
    """
    从国金报告中提取各部分文本，用于重组联合周报。
    返回 dict of section texts。
    """
    if not report_text:
        return {}

    sections = {}
    lines = report_text.split('\n')

    # 提取各节内容
    current_section = None
    current_lines = []

    section_markers = {
        "header_end": "---",
        "core_conclusion": "## 🎯 一、核心结论",
        "summary_table": "## 📊 二、资金主体全景表",
        "consensus_matrix": "## 📊 三、资金共识矩阵",
        "industry_ranking": "## 📈 四、行业综合净流入排序",
        "tech_section": "## 🔬 五、科技板块专项判断",
        "detail_section": "## 📋 六、各资金分项详解",
        "risk_section": "## ⚠️ 七、测算误差与风险提示",
    }

    for line in lines:
        if line.startswith("## 🎯 一、核心结论"):
            current_section = "core_conclusion"
            current_lines = []
        elif line.startswith("## 📊 二、资金主体全景表"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = "summary_table"
            current_lines = []
        elif line.startswith("## 📊 三、资金共识矩阵"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = "consensus_matrix"
            current_lines = []
        elif line.startswith("## 📈 四、行业综合净流入排序"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = "industry_ranking"
            current_lines = []
        elif line.startswith("## 🔬 五、科技板块专项判断"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = "tech_section"
            current_lines = []
        elif line.startswith("## 📋 六、各资金分项详解"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = "detail_section"
            current_lines = []
        elif line.startswith("## ⚠️ 七、"):
            if current_section:
                sections[current_section] = '\n'.join(current_lines)
            current_section = "risk_section"
            current_lines = []

        if current_section:
            current_lines.append(line)

    if current_section:
        sections[current_section] = '\n'.join(current_lines)

    return sections


def build_micro_timing_markdown(merged, breadth, flow, json_output,
                                 current_week_dates, prev_week_dates):
    """
    构建微盘择时部分的 Markdown 文本（六个部分）。
    """
    if merged is None or len(merged) == 0:
        return "⚠️ 微盘择时数据不可用\n"

    lines = []

    # 获取当前周数据
    current_week_data = merged[
        merged["trade_date"].isin(current_week_dates)
    ].sort_values("trade_date")

    prev_week_data = merged[
        merged["trade_date"].isin(prev_week_dates)
    ].sort_values("trade_date") if prev_week_dates else pd.DataFrame()

    if len(current_week_data) == 0:
        return "⚠️ 当前周无数据\n"

    # 当前信号
    last_row = current_week_data.iloc[-1]
    current_signal = last_row["signal"]
    current_score = last_row["composite_score"]

    # 计算连续天数
    all_sorted = merged.sort_values("trade_date")
    consecutive = 0
    for _, row in all_sorted.iloc[::-1].iterrows():
        if row["signal"] == current_signal:
            consecutive += 1
        else:
            break

    # ===== Part 1: 信号概览 =====
    lines.append("### 1.1 本周信号概览\n")
    for _, row in current_week_data.iterrows():
        d = row["trade_date"]
        b = f"{row['breadth_score']:.1f}" if pd.notna(row.get("breadth_score")) else "N/A"
        f = f"{row['fund_flow_score']:.1f}" if pd.notna(row.get("fund_flow_score")) else "N/A"
        c = f"{row['composite_score']:.1f}" if pd.notna(row.get("composite_score")) else "N/A"
        sig = row["signal"]
        lines.append(f"- **{d}**：宽度 {b}，资金流 {f}，综合 {c} → **{sig}**")
    lines.append(f"\n当前信号连续第 **{consecutive}** 个交易日。\n")

    # ===== Part 2: 宽度拆解 =====
    lines.append("### 1.2 宽度三维拆解\n")
    if breadth is not None and len(breadth) > 0:
        latest_date = current_week_data.iloc[-1]["trade_date"]
        latest_b = breadth[breadth["trade_date"] == latest_date]
        if len(latest_b) > 0:
            row = latest_b.iloc[0]
            ma20 = row.get("ma20_breadth", 0)
            ma20_pct = row.get("ma20_breadth_pct", 0)
            wd = row.get("weight_disp", 0)
            wd_pct = row.get("weight_disp_pct", 0)
            ind_d = row.get("ind_disp", 0)
            ind_pct = row.get("ind_disp_pct", 0)

            lines.append(f"- **MA20趋势宽度**：{ma20:.1%} 全A站上20日均线，5年分位 {ma20_pct:.0f}")
            lines.append(f"- **权重扩散**：等权-沪深300收益差 {wd:+.2%}，5年分位 {wd_pct:.0f}")
            lines.append(f"- **行业扩散**：{ind_d:.0%} 行业20日收益为正，5年分位 {ind_pct:.0f}")

            b_score = current_week_data["breadth_score"].mean()
            if b_score >= 70:
                lines.append("\n> 宽度高位，「广撒网式上涨」，微盘彩票效应兑现有利。")
            elif b_score >= 50:
                lines.append("\n> 宽度中性偏强，有扩散但非极致普涨，微盘有机会但需选择。")
            elif b_score >= 30:
                lines.append("\n> 宽度偏弱，行情集中少数权重，微盘被边缘化风险。")
            else:
                lines.append("\n> 宽度极弱，高度抱团，微盘回避。")
    lines.append("")

    # ===== Part 3: 资金流拆解 =====
    lines.append("### 1.3 资金流二维拆解\n")
    if flow is not None and len(flow) > 0:
        latest_date = current_week_data.iloc[-1]["trade_date"]
        latest_f = flow[flow["trade_date"] <= latest_date]
        if len(latest_f) > 0:
            frow = latest_f.iloc[-1]
            mi = frow.get("micro_intensity_ema", 0)
            mi_pct = frow.get("micro_intensity_ema_pct", 0)
            ri = frow.get("rel_intensity_20d_chg", 0)
            ri_pct = frow.get("rel_intensity_20d_chg_pct", 0)

            lines.append(f"- **微盘主动净买入强度**：EMA10 = {mi:+.4f}，5年分位 {mi_pct:.0f}")
            lines.append(f"- **相对强度20日变化**：偏离20MA = {ri:+.4f}，5年分位 {ri_pct:.0f}")

            if mi_pct >= 60 and ri_pct >= 60:
                lines.append("\n> 微盘有强主动买盘，相对沪深300持续吸金，最有利资金面。")
            elif mi_pct >= 40:
                lines.append("\n> 微盘买盘中性的，资金流向均衡，需关注变化方向。")
            else:
                lines.append("\n> 微盘买盘偏弱，存在赎回或流动性收缩迹象。")
    else:
        lines.append("⚠️ 资金流数据不可用。\n")
    lines.append("")

    # ===== Part 4: 综合判断 =====
    lines.append("### 1.4 综合判断\n")
    latest_score = last_row["composite_score"]
    latest_b_s = last_row["breadth_score"]
    latest_f_s = last_row.get("fund_flow_score", None)
    dist = abs(latest_score - 50)

    lines.append(f"综合得分 **{latest_score:.1f}**，距50阈值 {dist:.1f} 分。")
    if dist < 5:
        lines.append("⚠️ 得分在50附近横跳，信号不稳定，不宜重仓。")
    elif dist < 15:
        lines.append("边缘区域，有一定可信度但不够坚实。")
    else:
        direction = "多" if latest_score > 50 else "空"
        lines.append(f"稳稳站住看{direction}方向，信号可信度高。")

    # 因子共振
    if pd.notna(latest_f_s):
        b_side = "多" if latest_b_s > 50 else "空"
        f_side = "多" if latest_f_s > 50 else "空"
        if b_side == f_side:
            lines.append(f"\n宽度（{latest_b_s:.0f}）和资金流（{latest_f_s:.0f}）**同向共振**，双因子共振信号可信度高。")
        else:
            lines.append(f"\n宽度（{latest_b_s:.0f}）和资金流（{latest_f_s:.0f}）**出现背离**，信号脆弱。")
    lines.append("")

    # ===== Part 5: 情景推演 =====
    lines.append("### 1.5 下周情景推演\n")
    if latest_score > 50:
        lines.append("- **乐观**(25%)：宽度+资金流双高，继续看多微盘")
        lines.append(f"- **基准**(50%)：维持当前格局，综合得分 {max(30, latest_score-10):.0f}~{min(70, latest_score+10):.0f}")
        lines.append("- **悲观**(25%)：风格急剧切换，资金撤出微盘，得分跌破50")
    else:
        lines.append("- **乐观**(25%)：市场快速反弹，宽度扩张资金回流，翻多看微盘")
        lines.append(f"- **基准**(50%)：维持当前格局，综合得分 {max(30, latest_score-10):.0f}~{min(70, latest_score+10):.0f}")
        lines.append("- **悲观**(25%)：微盘流动性冲击，得分进一步下探至30以下")
    lines.append("")

    # ===== Part 6: 一句话结论 =====
    lines.append("### 1.6 一句话结论\n")
    if latest_score > 65:
        lines.append(f"**{latest_score:.0f}分，强烈看多微盘。** 盯宽度和资金流是否维持高位。")
    elif latest_score > 50:
        lines.append(f"**{latest_score:.0f}分，看多微盘但信号不算极端强。** 关注得分能否站稳。")
    elif latest_score > 35:
        lines.append(f"**{latest_score:.0f}分，偏中性略偏沪深300。** 关注宽度能否企稳。")
    else:
        lines.append(f"**{latest_score:.0f}分，明确看多沪深300。** 继续回避微盘。")
    lines.append("")

    return '\n'.join(lines)


def build_cross_validation_section(capital_sections, micro_md, detection_results, merged):
    """
    构建第三部分：交叉验证与综合研判
    """
    lines = []
    lines.append("## 第三部分：交叉验证与综合研判\n")

    # 获取核心数据
    if merged is not None and len(merged) > 0:
        last = merged.sort_values("trade_date").iloc[-1]
        micro_signal = last["signal"]
        micro_score = last["composite_score"]
    else:
        micro_signal = "N/A"
        micro_score = 50

    # 从国金报告中提取关键资金面信息
    capital_direction = "中性"
    capital_detail = ""
    core_text = capital_sections.get("core_conclusion", "")
    if "净购" in core_text or "融资余额增加" in core_text:
        capital_direction = "偏多"
    elif "净赎回" in core_text or "融资余额减少" in core_text:
        capital_direction = "偏空"

    # 交叉验证表
    lines.append("### 资金面 × 微盘择时 交叉验证\n")
    lines.append("| 维度 | 国金五维资金 | 微盘择时模型 | 一致性 |")
    lines.append("|------|------------|------------|--------|")

    # 总体方向
    micro_direction = "看多微盘" if micro_score > 50 else "看多沪深300"
    agree = "✅ 一致" if (capital_direction == "偏多" and micro_score > 50) or \
                        (capital_direction == "偏空" and micro_score <= 50) or \
                        (capital_direction == "中性") else "⚠️ 背离"
    lines.append(f"| 总体方向 | {capital_direction} | {micro_direction} | {agree} |")

    # 宽度 vs 资金共识
    breadth_signal = "扩散" if micro_score > 50 else "集中"
    lines.append(f"| 市场结构 | — | 宽度{breadth_signal} | 参考 |")

    # 资金流
    flow_signal = "微盘吸金" if micro_score > 55 else "微盘失血" if micro_score < 45 else "均衡"
    lines.append(f"| 边际资金 | {capital_direction} | {flow_signal} | 参考 |")

    lines.append("")

    # 共识结论
    lines.append("### 共识结论\n")
    if micro_score > 60 and capital_direction == "偏多":
        lines.append("**强共振**：五维资金面偏多 + 微盘择时强烈看多。市场处于「广撒网」格局，微盘股弹性最大。建议维持微盘持仓，关注行业轮动机会。")
    elif micro_score > 50 and capital_direction != "偏空":
        lines.append("**温和共振**：资金面中性偏多 + 微盘择时看多。市场宽度支撑微盘策略，但需关注资金面是否持续。建议持有但控制仓位。")
    elif micro_score <= 40 and capital_direction == "偏空":
        lines.append("**双杀信号**：资金面偏空 + 微盘择时看空。市场处于「抱团」格局，微盘被边缘化。建议切换到沪深300防御。")
    elif micro_score <= 50:
        lines.append("**偏空共振**：微盘择时看空，资金面未提供明确支撑。建议降低微盘仓位，观察宽度指标何时企稳。")
    else:
        lines.append("**信号混合**：两个模型信号不完全一致，建议降低仓位等待方向明确。")
    lines.append("")

    # 下周关键观察点
    lines.append("### 下周关键观察点\n")
    lines.append("1. **宽度指标**：MA20趋势宽度能否维持/扩张，行业扩散方向")
    lines.append("2. **资金流向**：ETF净申购趋势、融资余额变化方向")
    lines.append("3. **微盘资金流**：主动净买入强度是否持续，相对沪深300是否失血")
    if detection_results.get("has_switch"):
        lines.append("4. **⚡ 信号切换**：本周已出现信号切换，需密切跟踪T+1确认")
    lines.append("")

    return '\n'.join(lines)


def generate_combined_report(end_date_str, week_start, friday,
                              capital_report_text, capital_sections,
                              merged, breadth, flow, json_output,
                              detection_results):
    """
    生成完整的联合周报 Markdown。
    """
    week_label = get_iso_week_label(friday)
    today_str = datetime.now().strftime("%Y年%m月%d日")
    ws_display = format_date_display(week_start)
    we_display = format_date_display(friday)

    current_week_dates = detection_results.get("current_week_dates", [])
    prev_week_dates = detection_results.get("prev_week_dates", [])

    L = []

    # ===== 标题区 =====
    L.append(f"# A股周度资金流向与微盘择时联合简报 | {week_label}")
    L.append("")
    L.append(f"> **报告日期**: {today_str}")
    L.append(f"> **观察窗口**: {ws_display}-{we_display}")
    L.append(f"> **分析框架**: 国金五维资金跟踪 × 微盘择时量化模型（宽度+边际资金流）")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 核心结论 =====
    L.append("## 🎯 核心结论")
    L.append("")

    # 从国金报告提取核心数据
    core_text = capital_sections.get("core_conclusion", "")
    # 提取一句话结论（通常在第一个加粗文本中）
    core_one_liner = ""
    for line in core_text.split('\n'):
        if line.startswith('**') and len(line) > 10:
            core_one_liner = line.strip('*').strip()
            break

    # 微盘信号
    micro_signal = "看多微盘" if (merged is not None and len(merged) > 0 and
                                   merged.sort_values("trade_date").iloc[-1]["composite_score"] > 50) else "看多沪深300"
    micro_score = merged.sort_values("trade_date").iloc[-1]["composite_score"] if merged is not None and len(merged) > 0 else 50

    if core_one_liner:
        L.append(f"**资金面**：{core_one_liner}")
    L.append(f"**微盘择时**：综合得分 {micro_score:.0f}，{micro_signal}")

    # 交叉验证结论
    if micro_score > 50 and "净购" in core_one_liner:
        L.append(f"**交叉验证**：资金面偏多 × 微盘看多 → ✅ 共振，维持微盘策略")
    elif micro_score <= 50 and "净赎回" in core_one_liner:
        L.append(f"**交叉验证**：资金面偏空 × 微盘看空 → ⚠️ 双杀，切换防御")
    elif micro_score > 50:
        L.append(f"**交叉验证**：资金面中性 × 微盘看多 → 持有但控制仓位")
    else:
        L.append(f"**交叉验证**：信号不完全一致，降低仓位等待方向明确")

    # 切换提醒
    if detection_results.get("cross_week_switch"):
        sw = detection_results["cross_week_switch"]
        L.append(f"\n⚡ **信号切换**：本周发生跨周切换，从「{sw['from_signal']}」→「{sw['to_signal']}」")
    elif detection_results.get("intra_week_switches"):
        L.append(f"\n⚡ **信号反复**：本周出现周内信号切换，信号不稳定")

    L.append("")
    L.append("---")
    L.append("")

    # ===== 第一部分：微盘择时 =====
    L.append("## 第一部分：微盘择时量化信号")
    L.append("")
    micro_md = build_micro_timing_markdown(
        merged, breadth, flow, json_output,
        current_week_dates, prev_week_dates
    )
    L.append(micro_md)
    L.append("---")
    L.append("")

    # ===== 第二部分：国金策略资金跟踪 =====
    L.append("## 第二部分：国金策略资金跟踪")
    L.append("")

    # 按顺序放国金报告的核心部分
    for section_key in ["core_conclusion", "summary_table", "consensus_matrix",
                        "industry_ranking", "tech_section", "detail_section"]:
        section_text = capital_sections.get(section_key, "")
        if section_text:
            L.append(section_text)
            L.append("")

    L.append("---")
    L.append("")

    # ===== 第三部分：交叉验证 =====
    cross_val = build_cross_validation_section(
        capital_sections, micro_md, detection_results, merged
    )
    L.append(cross_val)

    # ===== 备注 =====
    # 国金的风险提示
    risk_text = capital_sections.get("risk_section", "")
    if risk_text:
        L.append(risk_text)
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"*联合简报由自动化脚本生成 | {today_str}*")
    L.append(f"*数据来源：国金五维资金(AKShare) + 微盘择时模型(Tushare代理)*")
    L.append(f"*微盘择时模型：综合得分 = 0.5×市场宽度得分 + 0.5×边际资金流得分（5年分位数）*")

    return '\n'.join(L)


# ============================================================
# 模块5: 邮件 & 飞书通知
# ============================================================
def markdown_to_html(md_text):
    """简易 Markdown → HTML 转换（邮件用）"""
    lines = md_text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
            continue

        if stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
            continue
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped[2:])
            html_lines.append(f"<li>{text}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped)
        html_lines.append(f"<p>{text}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "<html><body>" + "\n".join(html_lines) + "</body></html>"


def send_switch_alert_email(subject, body):
    """发送邮件通知（信号切换时）"""
    password = os.environ.get("COZE_EMAIL_PASSWORD")
    if not password:
        print("  ⚠️ COZE_EMAIL_PASSWORD 未设置，跳过邮件")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        html_body = markdown_to_html(body)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, password)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

        print(f"  ✅ 邮件发送成功: {subject}")
        return True
    except Exception as e:
        print(f"  ❌ 邮件发送失败: {e}")
        return False


def send_switch_alert_feishu(subject, body):
    """飞书卡片通知（信号切换时，邮件失败的降级方案）"""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from feishu_push import push_to_feishu

        # 构建简单卡片
        card_body = body[:2800] if len(body) > 2800 else body
        push_to_feishu(text=f"**{subject}**\n\n{card_body}", as_card=True)
        print("  ✅ 飞书卡片推送完成")
        return True
    except Exception as e:
        print(f"  ❌ 飞书推送失败: {e}")
        return False


def send_combined_report_feishu(report_path, report_text, week_label):
    """推送联合周报到飞书"""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from feishu_push import push_to_feishu

        # 推送文件附件
        push_to_feishu(file_path=report_path, as_file=True)

        # 推送摘要卡片
        # 取核心结论部分
        summary = report_text[:2500]
        header = f"【联合周报】{week_label}\n"
        push_to_feishu(text=header + summary, as_card=True)
        print("  ✅ 联合周报飞书推送完成")
        return True
    except Exception as e:
        print(f"  ⚠️ 飞书推送异常: {e}")
        return False


def handle_switch_alerts(detection_results, today_str):
    """处理信号切换告警"""
    if not detection_results.get("has_switch"):
        print("  ✅ 无切换，跳过告警")
        return

    cross = detection_results.get("cross_week_switch")
    intra = detection_results.get("intra_week_switches", [])

    # 构建邮件内容
    if cross:
        subject = f"【微盘择时信号切换提醒】{today_str} 从{cross['from_signal']}切换为{cross['to_signal']}"
        body_lines = [
            f"## 跨周信号切换\n",
            f"- **切换日期**：{cross['date']}",
            f"- **切换前**：{cross['from_signal']}（综合得分 {cross['from_score']:.1f}）",
            f"- **切换后**：{cross['to_signal']}（综合得分 {cross['to_score']:.1f}）",
            f"- **切换前连续天数**：{cross.get('prev_consecutive_days', 'N/A')} 天",
            f"- **建议**：T+1收盘执行切换",
        ]
    elif intra:
        last = intra[-1]
        subject = f"【微盘择时信号切换提醒】{today_str} 周内信号反复 {last[1]}↔{last[2]}"
        body_lines = [
            f"## 周内信号反复\n",
            f"- 本周出现 {len(intra)} 次信号切换：",
        ]
        for sw_date, from_s, to_s in intra:
            body_lines.append(f"  - {sw_date}：{from_s} → {to_s}")
        body_lines.append(f"- **建议**：信号不稳定，等待确认后再执行")
    else:
        return

    body = "\n".join(body_lines)

    # 先邮件，失败降级飞书
    email_ok = send_switch_alert_email(subject, body)
    if not email_ok:
        print("  📤 邮件失败，降级飞书...")
        send_switch_alert_feishu(subject, body)


# ============================================================
# 主函数
# ============================================================
def main():
    t_start = time.time()
    result_mode, end_date_arg, do_push, output_dir_arg = parse_args()

    print("=" * 60)
    print("A股周度资金流向与微盘择时联合简报")
    print("=" * 60)
    print(f"  result_mode: {result_mode}")
    print(f"  end_date: {end_date_arg}")
    print(f"  push_feishu: {do_push}")

    # ===== 确定日期范围 =====
    if end_date_arg and end_date_arg != "last_friday":
        end_date_str = end_date_arg
    else:
        last_friday = get_last_friday()
        end_date_str = last_friday.strftime("%Y-%m-%d")

    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    week_start = end_dt - timedelta(days=end_dt.weekday())
    friday = end_dt

    output_dir = Path(output_dir_arg) if output_dir_arg else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    week_label = get_iso_week_label(friday)
    print(f"\n📅 分析周期: {week_start} ~ {friday}")
    print(f"📁 输出目录: {output_dir}")
    print(f"📋 周标签: {week_label}")

    # ===== Step 1: 国金资金流 =====
    capital_report_text, capital_report_path = run_capital_flow(end_date_str, output_dir)
    capital_sections = extract_capital_flow_sections(capital_report_text) if capital_report_text else {}

    # ===== Step 2: 微盘择时数据 =====
    merged, breadth, flow, json_output = load_micro_timing_data()

    # ===== Step 3: 信号切换检测 =====
    detection_results = {"has_switch": False, "current_week_dates": [], "prev_week_dates": []}
    if merged is not None and len(merged) > 0:
        detection_results = detect_all_switches(merged)
    else:
        print("\n  ⚠️ 微盘数据不可用，跳过切换检测")

    # ===== Step 4: 生成联合周报 =====
    print("\n" + "=" * 50)
    print("📝 [4/4] 生成联合周报...")
    print("=" * 50)

    report_text = generate_combined_report(
        end_date_str, week_start, friday,
        capital_report_text, capital_sections,
        merged, breadth, flow, json_output,
        detection_results,
    )

    # 保存联合周报
    date_tag = format_date_yyyymmdd(friday)
    report_filename = f"A股联合周报_{date_tag}.md"
    report_path = output_dir / report_filename
    with open(str(report_path), 'w', encoding='utf-8') as f:
        f.write(report_text)
    print(f"  ✅ 联合周报已保存: {report_path} ({len(report_text)}字)")

    # ===== 信号切换告警 =====
    today_str = datetime.now().strftime("%Y-%m-%d")
    if detection_results.get("has_switch"):
        print("\n⚡ 检测到信号切换，发送告警...")
        handle_switch_alerts(detection_results, today_str)

    # ===== 飞书推送 =====
    if do_push and result_mode != "no_reply":
        print("\n📤 推送联合周报到飞书...")
        send_combined_report_feishu(str(report_path), report_text, week_label)

    # ===== 完成 =====
    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"✅ 联合周报完成 | 耗时 {elapsed:.0f}s")
    print(f"   报告: {report_path}")
    if detection_results.get("has_switch"):
        print(f"   ⚡ 本周有信号切换，已发送告警")
    else:
        print(f"   ✅ 本周无信号切换")
    print(f"{'=' * 60}")

    return str(report_path)


if __name__ == "__main__":
    try:
        result = main()
        if result:
            print(f"\n📄 报告路径: {result}")
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        traceback.print_exc()
        sys.exit(1)
