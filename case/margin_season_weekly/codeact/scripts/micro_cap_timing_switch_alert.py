#!/usr/bin/env python3
"""
微盘择时信号切换检测 & 提醒脚本
独立包装脚本，供cron调度

功能:
- 复用 micro_cap_timing.py 的 cache (merged_scores.parquet)
- 检测三类切换: 周度内切换 / 跨周切换 / 进入切换区间(40~60连续3天)
- 有切换 → 邮件+飞书通知
- 无切换 → 打印日志跳过
- 邮件失败自动降级到飞书卡片

用法:
    python micro_cap_timing_switch_alert.py              # 正常运行
    python micro_cap_timing_switch_alert.py --force       # 强制重新计算
    python micro_cap_timing_switch_alert.py --dry-run     # 检测但不发送
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
import smtplib
import subprocess
import asyncio
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# Configuration
# ============================================================
BASE_DIR = Path(_cp("/Coze/Drive/两融季节同比"))
SCRIPT_DIR = BASE_DIR / "codeact" / "scripts"
CACHE_DIR = BASE_DIR / "codeact" / "output" / "micro_timing_cache"

# Email config
EMAIL_TO = "410119229@qq.com"
EMAIL_FROM = "moergen@coze.email"
SMTP_HOST = "smtp.coze.email"
SMTP_PORT = 465  # SSL

# Feishu fallback
FEISHU_CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"

# Import helpers from micro_cap_timing
sys.path.insert(0, str(SCRIPT_DIR))
from micro_cap_timing import (
    detect_intra_week_switches,
    detect_cross_week_switch,
    detect_switch_zone,
    get_current_week_dates,
    get_micro_structure,
    CACHE_DIR as MTC_CACHE_DIR,
)


# ============================================================
# Cache loading
# ============================================================

def load_merged_scores():
    """Load merged_scores.parquet from cache."""
    cache_path = CACHE_DIR / "merged_scores.parquet"
    if not cache_path.exists():
        return None
    df = pd.read_parquet(cache_path)
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def is_cache_fresh(merged):
    """Check if cache contains data for the latest trading day."""
    if merged is None or len(merged) == 0:
        return False

    # Get latest date in cache
    latest_cached = merged["trade_date"].max()

    # Check trade calendar for latest trading day
    trade_cal_path = CACHE_DIR / "trade_cal.parquet"
    if not trade_cal_path.exists():
        return True  # No calendar, assume fresh

    cal = pd.read_parquet(trade_cal_path)
    open_dates = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    if not open_dates:
        return True

    today_str = datetime.now().strftime("%Y%m%d")
    latest_trading = None
    for d in reversed(open_dates):
        if d <= today_str:
            latest_trading = d
            break

    if latest_trading is None:
        return True

    return latest_cached >= latest_trading


def force_recompute():
    """Run micro_cap_timing.py to recompute data."""
    script_path = SCRIPT_DIR / "micro_cap_timing.py"
    print(f"[INFO] 触发重新计算: {script_path}")
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True, text=True, timeout=1800
    )
    if result.returncode != 0:
        print(f"[ERROR] 重新计算失败:\n{result.stderr[-500:]}")
        return False
    print(f"[INFO] 重新计算完成")
    return True


# ============================================================
# Switch detection (orchestration)
# ============================================================

def run_all_detections(merged):
    """
    Run all switch detection checks.
    Returns dict of detection results.
    """
    results = {
        "intra_week_switches": [],
        "cross_week_switch": None,
        "switch_zones": [],
        "current_week_dates": [],
        "prev_week_dates": [],
        "current_week_data": None,
        "prev_week_data": None,
    }

    # Determine current and previous week dates
    current_week_dates, prev_week_dates = get_current_week_dates(merged)
    results["current_week_dates"] = current_week_dates
    results["prev_week_dates"] = prev_week_dates

    if not current_week_dates:
        print("[WARN] 无法确定当前周日期")
        return results

    # Current week data
    current_week_data = merged[
        merged["trade_date"].isin(current_week_dates)
    ].sort_values("trade_date").copy()
    results["current_week_data"] = current_week_data

    # Previous week data
    prev_week_data = merged[
        merged["trade_date"].isin(prev_week_dates)
    ].sort_values("trade_date").copy()
    results["prev_week_data"] = prev_week_data

    # 1. Intra-week switches
    results["intra_week_switches"] = detect_intra_week_switches(current_week_data)

    # 2. Cross-week switch
    if len(prev_week_dates) > 0:
        results["cross_week_switch"] = detect_cross_week_switch(
            merged, current_week_dates, prev_week_dates
        )

    # 3. Switch zone detection (40~60 for 3+ consecutive days)
    results["switch_zones"] = detect_switch_zone(merged, min_days=3)

    return results


# ============================================================
# Notification formatting
# ============================================================

def format_date(d):
    """Format date string YYYYMMDD → YYYY-MM-DD."""
    d = str(d)
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def build_email_content(detection_results):
    """Build email subject and body from detection results."""
    today = datetime.now().strftime("%Y-%m-%d")
    cross = detection_results["cross_week_switch"]
    intra = detection_results["intra_week_switches"]
    zones = detection_results["switch_zones"]
    current_week_data = detection_results["current_week_data"]

    # Determine primary switch message
    if cross:
        from_sig = cross["from_signal"]
        to_sig = cross["to_signal"]
        subject = f"【微盘择时信号切换提醒】{today} 从{from_sig}切换为{to_sig}"
    elif intra:
        last_switch = intra[-1]
        from_sig = last_switch[1]
        to_sig = last_switch[2]
        subject = f"【微盘择时信号切换提醒】{today} 周内信号反复 {from_sig}↔{to_sig}"
    elif zones:
        latest_zone = zones[-1]
        subject = f"【微盘择时信号切换提醒】{today} 进入切换区间(40~60)"
    else:
        return None, None  # Nothing to alert

    # Build markdown body
    lines = []
    lines.append("## 信号切换提醒\n")

    if cross:
        lines.append(f"- **切换类型**：跨周切换")
        lines.append(f"- **切换日期**：{format_date(cross['date'])}")
        lines.append(f"- **切换前**：{cross['from_signal']}（综合得分 {cross['from_score']:.1f}）")
        lines.append(f"- **切换后**：{cross['to_signal']}（综合得分 {cross['to_score']:.1f}）")
        flow_str = f"{cross['flow']:.1f}" if cross['flow'] is not None else "N/A"
        lines.append(f"- **触发原因**：综合得分 {cross['to_score']:.1f}（宽度 {cross['breadth']:.1f} × 资金流 {flow_str}）")
        lines.append(f"- **切换前连续天数**：{cross['prev_consecutive_days']} 天")
        lines.append(f"- **建议**：T+1收盘执行切换")
    elif intra:
        lines.append(f"- **切换类型**：周度内信号反复")
        for sw_date, from_s, to_s in intra:
            lines.append(f"  - {format_date(sw_date)}：{from_s} → {to_s}")
        if current_week_data is not None and len(current_week_data) > 0:
            latest = current_week_data.iloc[-1]
            lines.append(f"- **当前信号**：{latest['signal']}（综合得分 {latest['composite_score']:.1f}）")
            lines.append(f"- **建议**：信号不稳定，建议等待确认后再执行")
    elif zones:
        z = zones[-1]
        lines.append(f"- **切换类型**：进入切换区间（综合得分40~60连续{z['days']}天）")
        lines.append(f"- **区间**：{format_date(z['start_date'])} ~ {format_date(z['end_date'])}")
        scores_str = "、".join([f"{s['composite_score']:.1f}" for s in z['scores'][-3:]])
        lines.append(f"- **近期得分**：{scores_str}")
        lines.append(f"- **建议**：信号在50附近反复横跳，密切关注方向选择")

    # Weekly review section
    lines.append("\n## 本周信号回顾\n")
    if current_week_data is not None and len(current_week_data) > 0:
        for _, row in current_week_data.iterrows():
            d = format_date(row["trade_date"])
            b = f"{row['breadth_score']:.1f}" if pd.notna(row.get("breadth_score")) else "N/A"
            f_val = f"{row['fund_flow_score']:.1f}" if pd.notna(row.get("fund_flow_score")) else "N/A"
            c = f"{row['composite_score']:.1f}" if pd.notna(row.get("composite_score")) else "N/A"
            sig = row["signal"]
            lines.append(f"- {d}：宽度 {b} | 资金流 {f_val} | 综合 {c} → **{sig}**")
    else:
        lines.append("（本周暂无数据）")

    # Micro structure
    lines.append("\n## 微观结构\n")
    latest_date = None
    if current_week_data is not None and len(current_week_data) > 0:
        latest_date = current_week_data.iloc[-1]["trade_date"]
    micro = get_micro_structure(latest_date)
    if micro["turnover_pct"] is not None:
        lines.append(f"- 微盘平均换手率：{micro['turnover_pct']}%")
    else:
        lines.append("- 微盘平均换手率：N/A")
    if micro["mv_billion"] is not None:
        lines.append(f"- 微盘平均市值：{micro['mv_billion']} 亿元")
    else:
        lines.append("- 微盘平均市值：N/A")

    body = "\n".join(lines)
    return subject, body


def build_feishu_card(subject, body):
    """Build a feishu card dict from subject and markdown body."""
    # Truncate body for feishu card limit
    max_len = 2800
    if len(body) > max_len:
        cut = body.rfind('\n', 0, max_len)
        if cut <= 0:
            cut = max_len
        body = body[:cut] + "\n\n⚠️ 内容已截断"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": subject},
            "template": "red" if "切换" in subject else "orange"
        },
        "elements": [
            {"tag": "markdown", "content": body}
        ]
    }
    return card


# ============================================================
# Notification sending
# ============================================================

def send_email(subject, body):
    """
    Send email via SMTP SSL.
    Returns True on success, False on failure.
    """
    password = os.environ.get("COZE_EMAIL_PASSWORD")
    if not password:
        print("[WARN] 环境变量 COZE_EMAIL_PASSWORD 未设置，跳过邮件发送")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        # Convert markdown to HTML for email
        html_body = markdown_to_html(body)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, password)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

        print(f"[INFO] ✅ 邮件发送成功: {subject}")
        return True

    except Exception as e:
        print(f"[ERROR] 邮件发送失败: {e}")
        return False


def markdown_to_html(md_text):
    """Simple markdown to HTML conversion for email."""
    import re
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

        # Headers
        if stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = stripped[3:]
            html_lines.append(f"<h2>{text}</h2>")
            continue
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = stripped[4:]
            html_lines.append(f"<h3>{text}</h3>")
            continue

        # List items
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            text = stripped[2:]
            # Bold
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            html_lines.append(f"<li>{text}</li>")
            continue

        # Regular paragraph
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped)
        html_lines.append(f"<p>{text}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "<html><body>" + "\n".join(html_lines) + "</body></html>"


def send_feishu_card(subject, body):
    """Send notification via feishu card (fallback)."""
    try:
        # Import feishu_push from the same directory
        sys.path.insert(0, str(SCRIPT_DIR))
        from feishu_push import push_to_feishu, markdown_to_card

        card = build_feishu_card(subject, body)
        card_json = json.dumps(card, ensure_ascii=False)

        cmd = [
            'lark-cli', 'im', '+messages-send',
            '--chat-id', FEISHU_CHAT_ID,
            '--msg-type', 'interactive',
            '--content', card_json,
            '--as', 'bot'
        ]
        env = os.environ.copy()
        env['LARK_CLI_NO_PROXY'] = '1'

        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and ('"ok": true' in result.stdout or '"ok":true' in result.stdout):
            print("[INFO] ✅ 飞书卡片推送完成")
            return True
        else:
            print(f"[ERROR] 飞书卡片推送失败: {result.stderr[:300] or result.stdout[:300]}")
            return False

    except Exception as e:
        print(f"[ERROR] 飞书推送异常: {e}")
        return False


def send_alert(subject, body, dry_run=False):
    """
    Send alert via email, fallback to feishu if email fails.
    """
    if dry_run:
        print(f"\n[DRY-RUN] 不实际发送通知，以下为通知内容:")
        print(f"  主题: {subject}")
        print(f"  正文:\n{body}")
        return True

    # Try email first
    email_ok = send_email(subject, body)
    if not email_ok:
        print("[INFO] 邮件发送失败，降级到飞书卡片...")
        feishu_ok = send_feishu_card(subject, body)
        if not feishu_ok:
            print("[ERROR] 邮件和飞书均发送失败")
            return False
    return True


# ============================================================
# Main
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="微盘择时信号切换检测")
    parser.add_argument("--force", action="store_true", help="强制重新计算数据")
    parser.add_argument("--dry-run", action="store_true", help="检测但不发送通知")
    args = parser.parse_args()

    print("=" * 60)
    print("微盘择时信号切换检测 & 提醒")
    print("=" * 60)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  --force: {args.force}")
    print(f"  --dry-run: {args.dry_run}")

    # Step 1: Load or recompute data
    print("\n--- Step 1: 加载/更新数据 ---")
    merged = load_merged_scores()

    if args.force:
        print("[INFO] --force 模式，强制重新计算")
        if not force_recompute():
            print("[ERROR] 重新计算失败，尝试使用现有cache")
            merged = load_merged_scores()
            if merged is None:
                print("[FATAL] 无可用数据，退出")
                sys.exit(1)
        else:
            merged = load_merged_scores()
    elif merged is None:
        print("[INFO] cache不存在，触发首次计算")
        if not force_recompute():
            print("[FATAL] 首次计算失败，退出")
            sys.exit(1)
        merged = load_merged_scores()
    elif not is_cache_fresh(merged):
        print("[INFO] cache不是最新，触发重新计算")
        if not force_recompute():
            print("[WARN] 重新计算失败，使用现有cache")
        merged = load_merged_scores()
    else:
        latest = merged["trade_date"].max()
        print(f"[INFO] cache已最新 (最新日期: {latest})")

    if merged is None or len(merged) == 0:
        print("[FATAL] 无法加载merged_scores数据，退出")
        sys.exit(1)

    print(f"[INFO] 数据范围: {merged['trade_date'].min()} ~ {merged['trade_date'].max()}，共{len(merged)}条")

    # Step 2: Run detections
    print("\n--- Step 2: 信号切换检测 ---")
    results = run_all_detections(merged)

    current_week_dates = results["current_week_dates"]
    prev_week_dates = results["prev_week_dates"]
    intra_switches = results["intra_week_switches"]
    cross_switch = results["cross_week_switch"]
    switch_zones = results["switch_zones"]

    print(f"  当前周日期: {current_week_dates}")
    print(f"  上周日期: {prev_week_dates}")
    print(f"  周度内切换: {len(intra_switches)} 次")
    for sw_date, from_sig, to_sig in intra_switches:
        print(f"    {sw_date}: {from_sig} → {to_sig}")
    print(f"  跨周切换: {'是' if cross_switch else '否'}")
    if cross_switch:
        print(f"    {cross_switch['from_signal']} → {cross_switch['to_signal']} (日期: {cross_switch['date']})")
    print(f"  切换区间(40~60连续3天+): {len(switch_zones)} 个")
    for z in switch_zones[-3:]:
        print(f"    {z['start_date']}~{z['end_date']} ({z['days']}天)")

    # Step 3: Determine if alert needed
    has_switch = bool(intra_switches) or bool(cross_switch) or bool(switch_zones)

    if not has_switch:
        print("\n✅ 无切换，跳过通知")
        # Print summary for logging
        if results["current_week_data"] is not None and len(results["current_week_data"]) > 0:
            latest = results["current_week_data"].iloc[-1]
            print(f"  当前信号: {latest['signal']}，综合得分: {latest['composite_score']:.1f}")
        return

    # Step 4: Build and send alert
    print("\n--- Step 3: 构建并发送通知 ---")
    subject, body = build_email_content(results)

    if subject is None:
        print("[INFO] 无需通知的内容，跳过")
        return

    ok = send_alert(subject, body, dry_run=args.dry_run)
    if ok:
        print(f"\n✅ 通知处理完成: {subject}")
    else:
        print(f"\n❌ 通知发送失败: {subject}")

    # Step 5: Summary
    print("\n" + "=" * 60)
    print("检测完成，汇总:")
    print(f"  当前周: {current_week_dates[0] if current_week_dates else 'N/A'} ~ {current_week_dates[-1] if current_week_dates else 'N/A'}")
    print(f"  周内切换: {len(intra_switches)} 次")
    print(f"  跨周切换: {'是' if cross_switch else '否'}")
    print(f"  切换区间: {len(switch_zones)} 个")
    if results["current_week_data"] is not None and len(results["current_week_data"]) > 0:
        latest = results["current_week_data"].iloc[-1]
        print(f"  最新信号: {latest['signal']} | 综合得分: {latest['composite_score']:.1f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
