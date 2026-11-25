# -*- coding: utf-8 -*-
"""房价指数技术分析 → 并入土狗付鹏地产日报，同一封邮件发出。

分工（用户明确的口径）
----------------------
- **公有数据**：`data_hub.house.signal_report()`——京沪房价指数的状态与触发信号、图。
  data_hub 只出数据和渲染，不发信、不存状态。
- **私有数据/观点**：`realestate_daily` 自己的日报正文（土狗状态机 + 付鹏四维 + 地方债…）。
- 两者在 `pipeline.dispatch` 里拼成**一封**邮件：正文上半是日报（私有），下半是房价技术（公有）。

发信通道
--------
走**统一通知库** `data_hub.notify`（飞书、邮件两个通道一个入口）。真实 token/key/收件人
只在部署侧 `notify.json`（或 `NOTIFY_*` 环境变量），库里只有字段名与脱敏打印，本文件不含凭据。
邮件通道用 `dws mail message send --inline-attachment` 内联图（正文占位 `[inline:price_tech.png]`），
dws 不可用时库内自动退 SMTP；本任务的飞书日报仍走自己的交互卡片（统一库只发文本，发不了卡片）。

默认真发：调度器跑任务就该发（用户口径「一起发邮件」）。要演练时显式设
`RE_PRICE_TECH_SEND=0`，或调用方传 `dry_run=True`——测试一律走这两个开关，
不靠改代码，也不靠默认不发导致线上静默不报。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
STATE = os.path.join(OUT, ".price_tech_state.json")
PNG = os.path.join(OUT, "price_tech.png")

LEVEL_CN = {"bull": "转多", "bear": "转空", "warn": "超买", "watch": "超卖"}
LEVEL_ORDER = {"bear": 0, "warn": 1, "bull": 2, "watch": 3}


def collect(cities=None):
    """取公有数据（含出图）。datahub 不可用时返回 None，由调用方降级——不编数据。"""
    try:
        from data_hub import house
    except ImportError as exc:
        print("[price_tech] 无法 import data_hub.house：%s" % exc, file=sys.stderr)
        return None
    try:
        return house.signal_report(cities=cities, kind="old", chart_path=PNG)
    except Exception as exc:
        print("[price_tech] 技术分析失败：%s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        return None


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(OUT, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def dedupe(rep):
    """按 city|kind|month|code 去重，返回 (本轮新增, 新状态)。

    区间信号也在里面——所以「已经在超买区」这件事每月只提醒一次，不会天天刷。
    """
    state = _load_state()
    fresh, now = [], {}
    for s in (rep or {}).get("signals") or []:
        key = "%s|%s|%s|%s" % (s["city"], rep.get("kind", "old"), s["month"], s["code"])
        now[key] = s["month"]
        if key in state:
            continue
        fresh.append(s)
    # 状态只保留近 12 个月
    months = sorted(now.values())
    floor = ""
    if months:
        yy, mm = int(months[0][:4]), int(months[0][5:7]) - 12
        while mm <= 0:
            mm += 12
            yy -= 1
        floor = "%04d-%02d" % (yy, mm)
    keep = {k: v for k, v in state.items() if v >= floor} if floor else dict(state)
    keep.update(now)
    return fresh, keep


def render_section(rep, fresh):
    """公有节：状态表 + 本轮新增（没有新增就说没有，不拿旧信号凑数）。"""
    if not rep:
        return "### 房价指数技术分析\n\n本次未取到（data_hub.house 不可用或数据缺失）。"
    md = [rep["markdown"], ""]
    if fresh:
        rows = {}
        for s in fresh:
            rows.setdefault((s["city"], s["month"]), []).append(s)
        md.append("本轮新增 %d 条：" % len(fresh))
        for (city, month), items in sorted(
                rows.items(),
                key=lambda kv: (LEVEL_ORDER.get(kv[1][0]["level"], 9), kv[0][0])):
            md.append("- %s %s：%s" % (city, month,
                                       "；".join(i["text"] for i in items)))
    else:
        md.append("本轮新增：无（同月同信号此前已报过，或数据未更新）。")
    return "\n".join(md)


def render_text(rep, fresh):
    """公有节的**纯文本**版：给 dws 邮件正文用。

    邮件正文会被 CLI 转成 HTML，但 CLI 不解析 Markdown——表格的 `|` 会原样留成竖线，
    状态表按行写成一行一城更耐读。看板/飞书仍用 ``render_section`` 的 Markdown 版。
    """
    if not rep:
        return "本次未取到（data_hub.house 不可用或数据缺失）。"
    label = "二手住宅" if rep.get("kind", "old") == "old" else "新建商品住宅"
    lines = ["房价指数技术分析（%s，月度）" % label,
             "口径：房价指数环比连乘还原的合成价格（该城首月=100），不是元/㎡；"
             "指标 KDJ(9,3,3)、MACD(12,26,9)、MA20。", ""]
    for s in rep.get("status") or []:
        if not s.get("ok"):
            lines.append("%s：数据不足（%s）" % (s.get("city"), s.get("reason") or "无数据"))
            continue
        lines.append("%s %s｜合成价格 %.2f｜K %.1f D %.1f J %.1f｜DIF %.2f DEA %.2f｜%s" % (
            s["city"], s["month"], s["price"],
            s["kdj"]["k"], s["kdj"]["d"], s["kdj"]["j"],
            s["macd"]["dif"], s["macd"]["dea"], s.get("zone") or "—"))
    lines.append("")
    if fresh:
        lines.append("本轮新增 %d 条：" % len(fresh))
        rows = {}
        for s in fresh:
            rows.setdefault((s["city"], s["month"]), []).append(s)
        for (city, month), items in sorted(
                rows.items(),
                key=lambda kv: (LEVEL_ORDER.get(kv[1][0]["level"], 9), kv[0][0])):
            lines.append("- %s %s：%s" % (city, month,
                                         "；".join(i["text"] for i in items)))
    else:
        lines.append("本轮新增：无（同月同信号此前已报过，或数据未更新）。")
    return "\n".join(lines)


def plain_md(text, skip_first_heading=False):
    """把日报的 Markdown 去标记成邮件可读文本（CLI 只转 HTML，不解析 Markdown）。"""
    out, skipped = [], False
    for line in (text or "").splitlines():
        s = line
        if s.startswith("#"):
            if skip_first_heading and not skipped:
                skipped = True
                continue
            s = s.lstrip("#").strip()
        s = s.replace("**", "")
        out.append(s)
    return "\n".join(out)


def send_notice(subject, content, inline_png=None, dry_run=True):
    """走统一通知库发一封（含内联图）。返回 ``(ok, message)``。

    只走邮件通道：本任务的飞书日报是交互卡片，由 dispatch 里的卡片脚本负责；
    统一库的飞书通道只发文本，替代不了卡片。

    ``dry_run=True`` 时返回 ``ok=False``——让调用方知道「没真发」，别推进去重状态。
    """
    try:
        from data_hub import notify
    except ImportError as exc:
        return (False, "统一通知库不可用：%s" % exc)
    res = notify.send(subject, content, channels=("email",),
                      images=[inline_png] if inline_png else None, dry_run=dry_run)
    ch = (res.get("channels") or {}).get("email") or {"ok": False, "msg": "邮件通道无返回"}
    if dry_run:
        return (False, ch["msg"])
    return (bool(ch["ok"]), ch["msg"])


def send_state(state):
    _save_state(state)


def allow_send():
    """真发开关：**默认真发**（调度器到点就该发）；测试/演练显式设 RE_PRICE_TECH_SEND=0。"""
    return os.environ.get("RE_PRICE_TECH_SEND", "1").strip().lower() not in (
        "0", "false", "no", "off")