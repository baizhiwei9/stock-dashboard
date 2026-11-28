#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双分位告警状态机：QVIX 分位 × 股指基差分位

用户口径（2026-11-28）：
  · QVIX 250 日分位 ≥ 70 且 基差 250 日分位 ≥ 70（两者同步走高）
      → 对冲需求与恐慌同时升温，发邮件提醒
  · QVIX 分位 < 70 且 基差分位 < 70（两者同步回落）
      → 出清，发邮件提醒
  · 一高一低（分化）→ 不动，保持上一状态

分位方向：基差用「现货-期货」年化率（正=贴水），分位越高＝贴水越深＝基差越大，
与 QVIX 同向，所以「双高」＝风险、「双低」＝出清。

被动化率 7% 提醒线已并入本邮件正文，不单独发信（2026-11-28 用户指令）。

边沿触发：只在状态切换时发邮件，避免每天重复轰炸。首跑只建基线不发信。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "output", "data")

QV_TH = 70.0
BASIS_TH = 70.0
STATE_FILE = "alert_state.json"

LABEL = {
    "alert": "双高·对冲需求升温",
    "clear": "双低·出清",
    "mixed": "分化·待确认",
    "unknown": "数据不足",
}


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _load_optional(name, default=None):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def _prev_state():
    p = os.path.join(DATA, STATE_FILE)
    if not os.path.exists(p):
        return None, None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("state"), d.get("since")
    except (OSError, ValueError):
        return None, None


def classify(qvix_pct, basis_pct):
    if qvix_pct is None or basis_pct is None:
        return "unknown"
    if qvix_pct >= QV_TH and basis_pct >= BASIS_TH:
        return "alert"
    if qvix_pct < QV_TH and basis_pct < BASIS_TH:
        return "clear"
    return "mixed"


def build():
    """读 cycle.json（基差/被动化率）与 emotion.json（QVIX 分位），判状态"""
    cycle = _load("cycle.json")
    em = _load_optional("emotion.json", {}) or {}
    qv = ((em.get("metrics") or {}).get("qvix") or {})
    qvix_pct = qv.get("rank_250")
    basis = (cycle.get("basis") or {}).get("IF") or {}
    basis_pct = basis.get("pct_rank_250d")

    raw = classify(qvix_pct, basis_pct)
    prev, since = _prev_state()

    if raw == "unknown":
        state = prev or "unknown"
        changed = False
    elif raw == "mixed":
        state = prev if prev in ("alert", "clear") else "mixed"
        changed = False
    else:
        state = raw
        changed = bool(prev is not None and prev != raw and prev in ("alert", "clear", "mixed"))
        if prev is None:
            changed = False  # 首跑建基线，不发信

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if changed or not since:
        since = now

    out = {
        "generated_at": now,
        "as_of": cycle.get("as_of"),
        "qvix_pct": qvix_pct,
        "qvix_value": qv.get("value"),
        "qvix_date": qv.get("date"),
        "basis_pct": basis_pct,
        "basis_annual": basis.get("annual_pct"),
        "basis_contract": basis.get("contract"),
        "basis_high5": basis.get("high5"),
        "basis_drop_pp": basis.get("drop_pp"),
        "basis_hist_days": basis.get("hist_days"),
        "threshold": {"qvix": QV_TH, "basis": BASIS_TH},
        "raw": raw,
        "state": state,
        "prev_state": prev,
        "state_label": LABEL.get(state, state),
        "changed": changed,
        "since": since,
    }
    _save("alert.json", out)
    _save(STATE_FILE, {"state": state, "since": since, "updated": now})
    return out


def compose_mail(alert, cycle, emotion):
    """生成邮件标题与正文（纯文本，邮件客户端友好）"""
    p = (cycle or {}).get("passive") or {}
    em = emotion or {}
    as_of = alert.get("as_of") or ""
    qv = alert.get("qvix_pct")
    bp = alert.get("basis_pct")
    qvv = alert.get("qvix_value")
    ba = alert.get("basis_annual")
    bc = alert.get("basis_contract") or "IF"

    if alert["state"] == "alert":
        subject = f"【出清周期·风险】QVIX分位{qv:.0f}% + 基差分位{bp:.0f}%，双高"
        head = "波动率与基差同步走高，对冲需求升温。"
    else:
        subject = f"【出清周期·出清】QVIX分位{qv:.0f}% + 基差分位{bp:.0f}%，双双回落"
        head = "波动率与基差同步回落到 70% 分位以下，出清信号成立。"

    # 14:50 跑：日线未收，盘中价算出来的 MA / 分位收盘后可能有差异，必须在正文里标明。
    # 显式用北京时间判断，避免宿主机 TZ 不是 Asia/Shanghai 时误判。
    _now = datetime.now(ZoneInfo("Asia/Shanghai"))
    intraday = "（盘中数据，非收盘价，收盘后可能微调）" if _now.hour * 60 + _now.minute < 15 * 60 else ""

    # 被动化率不再单独发信，7% 提醒线并入本邮件正文。
    # 分子缺交易所时读数偏低，不能拿它判 7% —— 宁可写「不做判定」，也不给错信号。
    pv = p.get("value_pct")
    pv_txt = f"{pv}%（土狗口径 {p.get('value_pct_dog')}%）"
    if p.get("reliable") is False:
        _miss = "/".join(p.get("missing_exchanges") or []) or "部分交易所"
        pv_txt += f"  ← 数据不全（缺 {_miss}），读数偏低，本次不做 7% 判定"
    elif pv is not None and pv >= 7.0:
        pv_txt += "  ← 已过 7% 提醒线"

    lines = [
        f"{as_of}  {head}{intraday}",
        "",
        f"QVIX（300ETF期权隐波）  {qvv}   250日分位 {qv}%",
        f"{bc} 年化基差           {ba}%  250日分位 {bp}%（{alert.get('basis_hist_days')}个交易日样本）",
        f"距5日高点回落           {alert.get('basis_drop_pp')}pp（5日高 {alert.get('basis_high5')}%）",
        "",
        f"被动化率                {pv_txt}",
        f"情绪温度                {em.get('heat')} / {em.get('stage')}",
        "",
        f"状态：{alert['state_label']}（自 {alert['since']}）",
        "规则：QVIX 与基差分位同时 ≥70% 发风险提醒；同时 <70% 发出清提醒；一高一低不动。",
        "只在状态切换时发信，同状态不重复。被动化率只在本邮件内展示，不单独发信。",
    ]
    return subject, "\n".join(lines)


def main():
    cycle = _load("cycle.json")
    emotion = _load_optional("emotion.json", {})
    alert = build()
    print(json.dumps({k: alert[k] for k in ["state", "raw", "prev_state", "changed",
                                            "qvix_pct", "basis_pct", "since"]},
                     ensure_ascii=False, indent=2))
    if alert["changed"]:
        subject, body = compose_mail(alert, cycle, emotion)
        print("\n--- 邮件 ---")
        print(subject)
        print(body)
    return alert


if __name__ == "__main__":
    main()
