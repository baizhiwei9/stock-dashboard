# -*- coding: utf-8 -*-
"""dispatch 阶段：推送摘要

规则（用户设定）：
- MA40 下穿 MA80（出清中）→ 发送
- MA40 上穿 MA80（出清完成）→ 静默，不发送

推送通道未接入时只打印，不报错；dispatch 在 task.json 里是 optional，失败不影响落库。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("CASE_OUT_DIR") or os.path.join(HERE, "out")
SRC_DATA = os.path.join(HERE, "output", "data")


def _src_data():
    """取数产物优先读契约目录 data/，回退脚本原生目录 output/data/。"""
    for p in (os.path.join(HERE, "data"), SRC_DATA):
        if os.path.exists(os.path.join(p, "cycle.json")):
            return p
    return SRC_DATA


def load(name):
    with open(os.path.join(_src_data(), name), encoding="utf-8") as f:
        return json.load(f)


def main():
    c = load("cycle.json")
    ma = c["ma"]

    if not ma["in_clearing"]:
        print(f"出清已完成（{ma['since']} 上穿），按规则静默，不发送报告。")
        return

    md_path = os.path.join(OUT, "report.md")
    if not os.path.exists(md_path):
        print("未找到 out/report.md，跳过推送。")
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
    dry = "--dry" in sys.argv
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


if __name__ == "__main__":
    main()
