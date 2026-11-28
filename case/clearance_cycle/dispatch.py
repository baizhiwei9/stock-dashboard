# -*- coding: utf-8 -*-
"""dispatch 阶段：双分位邮件告警 + 报告推送

两条互不干扰的规则：

1) 双分位告警（QVIX 250日分位 × IF 年化基差 250日分位）
   · 同时 ≥ 70%  → 风险邮件（波动率与对冲需求同步升温）
   · 同时 < 70%  → 出清邮件（两者双双回落）
   · 一高一低    → 不动，保持上一状态
   · 只在状态切换时发信（calc_alert 判 changed），同状态不重复
   · 不受均线状态影响：出清走完也要继续盯下一轮

2) 报告推送（均线口径）
   · MA40 下穿 MA80（出清中）→ 发送
   · MA40 上穿 MA80（出清完成）→ 静默

dispatch 在 task.json 里是 optional，任何一环失败都不影响前面阶段落库。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")
OUT = os.environ.get("CASE_OUT_DIR") or os.path.join(HERE, "out")
SRC_DATA = os.path.join(HERE, "output", "data")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def _src_data():
    """取数产物优先读契约目录 data/，回退脚本原生目录 output/data/。"""
    for p in (os.path.join(HERE, "data"), SRC_DATA):
        if os.path.exists(os.path.join(p, "cycle.json")):
            return p
    return SRC_DATA


def load(name, default=None):
    p = os.path.join(_src_data(), name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _dispatch_alert(dry):
    """双分位告警：只有状态切换才发信"""
    a = load("alert.json")
    if not a:
        print("[双分位] 未找到 alert.json，跳过（检查 render 阶段是否跑了 calc_alert.py）")
        return

    qv, bp = a.get("qvix_pct"), a.get("basis_pct")
    print(f"[双分位] QVIX {qv}% / 基差 {bp}% → {a.get('state_label')}（自 {a.get('since')}）")

    if not a.get("changed"):
        print("[双分位] 状态未切换，不发信。")
        return

    from calc_alert import compose_mail          # noqa: PLC0415
    from mailer import send_mail                 # noqa: PLC0415

    # 邮件正文要被动化率与情绪温度：cycle.json 为准，旧 clearing.json 兼底
    cyc = load("cycle.json") or load("clearing.json") or {}
    subject, body = compose_mail(a, cyc, load("emotion.json") or {})
    rec = send_mail(subject, body, tag=f"alert-{a.get('state')}", dry=dry)
    print("=" * 60)
    print(f"[双分位] {subject}")
    print(f"[双分位] 发送结果：ok={rec['ok']} via={rec['via']} {rec['detail']}")
    if rec.get("draft"):
        print(f"[双分位] 草稿：{rec['draft']}")
    print("=" * 60)


def _dispatch_report(dry):
    """报告推送：均线口径，出清完成即静默"""
    c = load("cycle.json")
    if not c:
        print("[报告] 未找到 cycle.json，跳过。")
        return
    ma = c["ma"]

    if not ma["in_clearing"]:
        print(f"[报告] 出清已完成（{ma['since']} 上穿），按规则静默，不发送报告。")
        return

    md_path = os.path.join(OUT, "report.md")
    if not os.path.exists(md_path):
        print("[报告] 未找到 out/report.md，跳过推送。")
        return

    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    title = f"出清周期监控 · {c.get('as_of', '')} · 第 {ma['days']} 个交易日"
    lines = [
        f"周期状态：出清中（{ma['since']} MA40 下穿起），第 {ma['days']} 个交易日",
        f"MA40 − MA80 缺口：{ma['gap_pct']:.2f}%（{ma.get('dif', 0):.2f} 点）",
        "详见 out/report.md（含被动化率、年化基差、情绪温度）",
    ]
    # 走框架告警通道：有 webhook 配置就外发，没有就落盘历史 + 打印（alert.send 自带这一层降级）
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
        from alert import send as alert_send                       # noqa: WPS433
        rec = alert_send("clearance_cycle", title, lines, level="warn", dry=dry, task_dir=HERE)
        print("=" * 60)
        print(f"已提交告警通道：{title}（{rec.get('webhook', '无 webhook 配置，已落盘 + 打印')}）")
        print("=" * 60)
    except Exception as exc:                                       # noqa: BLE001
        print("=" * 60)
        print(f"[告警通道不可用：{str(exc)[:80]}] 仅回显：{title}")
        print("=" * 60)
    print(md[:1200])


def main():
    dry = "--dry" in sys.argv
    _dispatch_alert(dry)
    _dispatch_report(dry)


if __name__ == "__main__":
    main()
