#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将军四区间日报 · 区间切换邮件提醒入口

把「判定当前区间 → 读取上次区间 → 对比 → 切换则发邮件 → 写回状态」串成一次调用。

规则：
  - 仅当区间发生切换（由 X 区间 → Y 区间）时生成并发送提醒邮件；
  - 未切换、或首次记录（此前无区间）时静默，不发信；
  - 无论是否切换，正常运行时都把最新区间写回 zone_state.json。

CLI:
  python zone_notify.py --date 2026-10-21 --zone 1 --note "贴水扩大、缩量降波"
  python zone_notify.py --date 2026-10-22 --zone 2 --note "贴水收窄、次级扩散" --evidence "2026-10-22 收盘" --dry-run
"""
import argparse
import datetime
import sys

import email_push
import zone_alert as za

DISCLAIMER = ("本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，"
              "不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。")


def build_subject(result, date):
    return f"【区间切换提醒】将军四区间：{result['prev_label']} → {result['label']}（数据日 {date}）"


def build_text_body(result, date, note, evidence):
    lines = [
        "将军四区间 · 区间切换提醒",
        "",
        f"切换方向：{result['prev_label']}（上次判定 {result['prev_date']}） → {result['label']}",
        f"本次判定：{result['label']}（区间{result['zone']}）",
        f"数据日：{date}",
        f"证据时点：{evidence}",
        "",
        "判定依据：",
        note or "（未提供，请在日报正文核对）",
        "",
        f"生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def build_html_body(result, date, note, evidence):
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    return f"""<div style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;line-height:1.7;color:#1f2329">
  <h2 style="margin:0 0 12px">将军四区间 · 区间切换提醒</h2>
  <p style="margin:6px 0"><b>切换方向：</b>{esc(result['prev_label'])}（上次判定 {esc(result['prev_date'])}）
     &nbsp;→&nbsp; <b style="color:#c0392b">{esc(result['label'])}</b></p>
  <p style="margin:6px 0"><b>本次判定：</b>{esc(result['label'])}（区间{esc(result['zone'])}）</p>
  <p style="margin:6px 0"><b>数据日：</b>{esc(date)} &nbsp;&nbsp; <b>证据时点：</b>{esc(evidence)}</p>
  <p style="margin:14px 0 6px"><b>判定依据：</b></p>
  <blockquote style="margin:0;padding:8px 12px;border-left:3px solid #ddd;color:#4e5969">
     {esc(note) if note else '（未提供，请在日报正文核对）'}</blockquote>
  <p style="margin:14px 0 0;color:#86909c;font-size:12px">生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
  <hr style="border:none;border-top:1px solid #eee;margin:12px 0">
  <p style="color:#86909c;font-size:12px">{esc(DISCLAIMER)}</p>
</div>"""


def run(date, zone, note="", evidence="", dry_run=False, retries=1):
    zone = za.normalize_zone(zone)
    if zone is None:
        print(f"[区间提醒] 无法识别的区间：{zone}（应为 1/2/3/4 或区间名）")
        return {"status": "invalid_zone", "sent": False}

    result = za.check_transition(zone)
    print("[区间提醒] " + za.describe(result))

    sent = False
    status = "unchanged"
    if result["changed"]:
        subject = build_subject(result, date)
        text_body = build_text_body(result, date, note, evidence or date)
        html_body = build_html_body(result, date, note, evidence or date)
        if dry_run:
            status = "changed_dry_run"
            print("[区间提醒][dry-run] 命中区间切换，将发送以下邮件（未实际发送、未写回状态）：")
            print("-" * 60)
            print("主题：" + subject)
            print(text_body)
            print("-" * 60)
        else:
            sent = email_push.send_email(subject, text_body, html_body, retries=retries)
            status = "changed_sent" if sent else "changed_send_failed"
            print(f"[区间提醒] 切换提醒邮件{'已发送' if sent else '发送失败'}")
    else:
        print("[区间提醒] 区间未切换，静默（不发信）")

    if dry_run:
        print("[区间提醒][dry-run] 未写回状态文件")
    else:
        za.record(zone, date, note=note)
        print(f"[区间提醒] 状态已写回 zone_state.json（{date} -> {result['label']}）")

    return {"status": status, "sent": sent, "changed": result["changed"], "result": result}


def main():
    ap = argparse.ArgumentParser(description="将军四区间 · 区间切换邮件提醒")
    ap.add_argument("--date", required=True, help="本次判定的数据日期（最近交易日）")
    ap.add_argument("--zone", required=True, help="本次判定区间：1/2/3/4 或区间名")
    ap.add_argument("--note", default="", help="判定依据摘要（贴水/量能/情绪等）")
    ap.add_argument("--evidence", default="", help="证据时点（如 '2026-10-21 收盘'），缺省用数据日")
    ap.add_argument("--dry-run", action="store_true", help="不实际发送、不写回状态，仅打印将要发送的内容")
    args = ap.parse_args()

    out = run(args.date, args.zone, args.note, args.evidence, dry_run=args.dry_run)
    if out["status"] == "invalid_zone":
        sys.exit(2)
    if out["status"] == "changed_send_failed":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()