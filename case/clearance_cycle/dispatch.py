# -*- coding: utf-8 -*-
"""dispatch 阶段：推送摘要

规则（用户设定）：
- MA40 下穿 MA80（出清中）→ 发送
- MA40 上穿 MA80（出清完成）→ 静默，不发送

推送通道未接入时只打印，不报错；dispatch 在 task.json 里是 optional，失败不影响落库。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("CASE_OUT_DIR") or os.path.join(HERE, "out")
SRC_DATA = os.path.join(HERE, "output", "data")


def load(name):
    with open(os.path.join(SRC_DATA, name), encoding="utf-8") as f:
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

    # 推送通道接入点：有 lark-cli / webhook 时在这里发，当前只回显
    print("=" * 60)
    print(f"待推送：出清中第 {ma['days']} 个交易日，缺口 {ma['gap_pct']:.2f}%")
    print("=" * 60)
    print(md[:1200])


if __name__ == "__main__":
    main()
