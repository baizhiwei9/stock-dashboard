#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四区间（期指贴水周期）→ 飞书卡片前段区块 + 区间切换邮件

被 daily_structural_report.py 调用，把「将军四区间」判定放到消息最前面，
结构因子指标图与结论接在其后。所有数值来自当日事实包，缺项标 NA；
取数或判定失败只标注失败原因，不阻断结构因子主流程。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import zone_regime as zr

FACTS_PATH = os.path.join(HERE, "zone_facts_latest.json")
STATE_PATH = os.path.join(HERE, "zone_state.json")

ZONE_CN = {1: "区间一", 2: "区间二", 3: "区间三", 4: "区间四"}
CONF_CN = {"high": "高", "medium": "中", "low": "低"}

ACTION = {
    1: "只守低熵（红利/银行高股息、上证50、沪深300），弹性仓维持 0，等贴水收窄、降波结束",
    2: "低熵做底仓、弹性仓维持 0；低熵走平且高熵跟随放阳线（两步确认全满足）才加弹性",
    3: "可持低熵并把弹性仓打开，升水回吐或跌破前低立即收回",
    4: "升水失败，弹性仓收回、回到低熵，等升水重新站上再谈进攻",
}


def prev_zone():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("last_zone")
    except Exception:
        return None


def fetch_facts(timeout: int = 480) -> dict:
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "fetch_zone_facts.py"), "--json", FACTS_PATH],
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "fetch_zone_facts 失败")[-400:])
    with open(FACTS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def judge_now(facts: dict | None = None) -> dict:
    facts = facts if facts is not None else fetch_facts()
    v = zr.judge(zr.extract_signals(facts), prev_zone=prev_zone())
    v["prev_zone"] = prev_zone()
    return v


def _div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def build(facts: dict | None = None) -> dict:
    """生成卡片前段要素 + 标题标记 + 一句话摘要。失败时返回降级块，不抛异常给主流程。"""
    try:
        v = judge_now(facts)
    except Exception as exc:  # 取数/判定失败：只降级标注
        msg = str(exc).splitlines()[-1][:160] if str(exc) else exc.__class__.__name__
        return {
            "ok": False,
            "elements": [
                _div(f"**将军四区间：本次判定失败（NA）**\n取数或判定异常，结构因子部分照常输出：{msg}"),
                {"tag": "hr"},
            ],
            "title": "将军四区间+结构因子日报",
            "short": "区间 NA",
            "risk": False,
            "opp": False,
            "summary": f"区间判定 NA（{msg}）",
            "verdict": None,
        }

    z = v["zone"]
    prev = v.get("prev_zone")
    switched = bool(prev and z and prev != z)
    label = v["label"]
    head = f"**将军四区间：{label}**　数据日 {v['data_date']}｜置信度 {CONF_CN.get(v['confidence'], v['confidence'])}"
    if switched:
        head += f"｜切换：{ZONE_CN.get(prev, prev)}→{ZONE_CN.get(z, z)}"

    lines = [head, f"依据：{v['reason']}", f"基差：{v['basis']}"]
    lines.append("排除｜" + "；".join(
        x for x in (
            f"非一：{v['non_1']}" if v.get("non_1") else "",
            f"非三：{v['non_3']}" if v.get("non_3") else "",
            f"非四：{v['non_4']}" if v.get("non_4") else "",
        ) if x
    ))
    lines.append(f"怎么做：{ACTION.get(z, '维持低熵防守')}")
    if v.get("hard_thresholds"):
        lines.append("**硬阈值触发：** " + "；".join(v["hard_thresholds"]))
    if v.get("na"):
        lines.append("NA：" + "；".join(v["na"]))

    # 单块承载，控制卡片元素在上限内（飞书卡片 elements 上限 30）
    elements = [_div("\n".join(lines)), {"tag": "hr"}]

    risk = z in (1, 4) or bool(v.get("hard_thresholds"))
    opp = z == 3
    short = f"{ZONE_CN.get(z, '区间')}（{label.split(' ', 1)[-1]}）" if z else "区间 NA"
    return {
        "ok": True,
        "elements": elements,
        "title": "将军四区间+结构因子日报",
        "short": short,
        "risk": risk,
        "opp": opp,
        "summary": f"{ZONE_CN.get(z, '区间')}（{v['confidence']}）",
        "verdict": v,
    }


def notify_switch(verdict: dict | None, timeout: int = 180) -> str:
    """区间相较上次切换时发提醒邮件；未切换脚本静默。返回一行状态。"""
    if not verdict or not verdict.get("zone"):
        return "[区间邮件] 跳过：无可判定区间"
    note = "；".join(x for x in (verdict.get("reason", ""), verdict.get("non_3", "")) if x)[:190]
    cmd = [
        sys.executable, os.path.join(HERE, "zone_notify.py"),
        "--date", str(verdict.get("data_date") or ""),
        "--zone", str(verdict["zone"]),
        "--note", note,
        "--evidence", f"{verdict.get('data_date')} 收盘",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        return tail[-1] if tail else f"[区间邮件] 无输出（exit={r.returncode}）"
    except Exception as exc:
        return f"[区间邮件] 失败：{str(exc)[:120]}"


if __name__ == "__main__":
    info = build()
    print(json.dumps({k: info[k] for k in ("ok", "short", "summary", "risk", "opp")}, ensure_ascii=False))
    for el in info["elements"]:
        if el.get("tag") == "div":
            print("-", el["text"]["content"])
    if "--email" in sys.argv:
        print(notify_switch(info.get("verdict")))