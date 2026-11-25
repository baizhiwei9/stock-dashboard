#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CME FedWatch 概率变化提醒（挂在中美期权风控任务里每日执行）

为什么放在这里
--------------
美股风控（中美期权风险释放汇总）本来每个交易日都会跑一遍、也已经有邮件通道，
把「降息/加息概率变了」的判断挂进来，不用再单独养一条定时任务。

判定口径（用户 2026-11-25 确认）
--------------------------------
- 数据源：DataHub EDB `US.FEDWATCH.NEXT.*`（采集器 quikstrike:fedwatch，与 CME
  FedWatch 工具页同源），不在这里二次联网；
- 比较对象：**同一场 FOMC 会议**的上一次记录（state/rate_prob_state.json）；
  会议换场（比如 12-09 会开完、基线切到下一次会议）只重设基线，不告警；
- 触发条件：降息 / 不变 / 加息三档中任一档概率变动 **≥ 5 个百分点**，或隐含目标
  利率中值变动 ≥ 5bp；低于阈值视为四舍五入噪声，不打扰；
- 幂等：同一个数据日（EDB 最新观测日）最多发一封，重复跑不会重复轰炸。

用法
----
    python3 rate_prob_alert.py            # 只判断 + 打印（dry-run，不发邮件）
    python3 rate_prob_alert.py --send     # 真发（邮件走 email_alert 的主/备双通道）
    python3 rate_prob_alert.py --status   # 看当前基线

退出码：0=无变化/未达阈值；2=触发并已发送（或 dry-run 下本应发送）；1=执行错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
STATE_DIR = os.path.join(HERE, "state")
STATE = os.path.join(STATE_DIR, "rate_prob_state.json")

DH = os.environ.get("DATAHUB_ROOT") or os.path.join(WS, "projects", "datahub-multi-tier")
for _p in (DH, os.path.join(WS, "pylibs"), os.path.join(WS, "py_deps")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

THRESHOLD_PP = 5.0     # 百分点
THRESHOLD_MID_BP = 5.0  # 隐含中值利率，bp


def load_edb():
    """从 DataHub EDB 取 FedWatch 标量 + 矩阵（矩阵用于邮件里的区间表）。"""
    from data_hub import edb
    out = {}
    for k, sid in (("hike", "US.FEDWATCH.NEXT.HIKE_PROB"),
                   ("hold", "US.FEDWATCH.NEXT.HOLD_PROB"),
                   ("ease", "US.FEDWATCH.NEXT.EASE_PROB"),
                   ("target_mid", "US.FEDWATCH.NEXT.TARGET_MID"),
                   ("days", "US.FEDWATCH.NEXT.DAYS")):
        r = edb.get_series_result(sid)
        out[k] = {
            "value": r.get("latest_value") if r.get("ok") else None,
            "obs": r.get("latest_date"),
            "ok": bool(r.get("ok")),
            "error": r.get("error"),
        }
    m = {}
    try:
        from data_hub.edb.fetchers import cme_fedwatch
        m = cme_fedwatch.latest_matrix() or {}
    except Exception:                                          # noqa: BLE001
        m = {}
    out["meeting"] = m.get("meeting_date") or ""
    out["asof_ct"] = m.get("asof_ct") or ""
    out["rows"] = m.get("rows") or []
    return out


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(obj):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def diff(cur, prev):
    """返回 [(档位, 当前, 上次, 变动pp)]，只含有有效数值的档位。"""
    rows = []
    for k, label in (("ease", "降息"), ("hold", "不变"), ("hike", "加息")):
        a = (cur.get(k) or {}).get("value")
        b = (prev.get(k) or {}).get("value")
        if a is None or b is None:
            continue
        rows.append((label, float(a), float(b), float(a) - float(b)))
    return rows


def build_mail(cur, prev, changes, mid_chg):
    meet = cur.get("meeting") or "—"
    days = (cur.get("days") or {}).get("value")
    lines = [
        "CME FedWatch 加息/降息概率发生变化（阈值 %.0f 个百分点）" % THRESHOLD_PP,
        "",
        "会议：%s（距今 %s 天）" % (meet, ("%.0f" % days) if days is not None else "—"),
        "数据时点（CT）：%s" % (cur.get("asof_ct") or "—"),
        "",
        "档位      当前      上次      变动",
    ]
    for label, a, b, d in changes:
        lines.append("%-6s %7.1f%% %8.1f%% %+8.1fpp" % (label, a, b, d))
    if mid_chg is not None:
        lines.append("隐含目标中值变动：%+.1fbp" % mid_chg)
    lines += ["", "各目标区间概率（现在 / 1天前 / 1周前 / 1月前）："]
    for r in cur.get("rows") or []:
        if max(r.get("now") or 0, r.get("d1") or 0, r.get("w1") or 0, r.get("m1") or 0) <= 0:
            continue
        lines.append("  %s%s：%.1f%% / %.1f%% / %.1f%% / %.1f%%" % (
            r.get("range"), "（当前）" if r.get("current") else "",
            r.get("now") or 0, r.get("d1") or 0, r.get("w1") or 0, r.get("m1") or 0))
    lines += ["", "口径：CME FedWatch（30 天联邦基金期货定价），数据来自 DataHub EDB "
                  "quikstrike:fedwatch；低于阈值的变动不提醒。",
              "来自 DoMate · 中美期权风险释放汇总"]
    subject = "【利率概率】FedWatch %s 会议：%s" % (
        meet, "、".join("%s%+.1fpp" % (c[0], c[3]) for c in changes))
    return subject, "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真发邮件（默认 dry-run）")
    ap.add_argument("--status", action="store_true", help="只打印当前基线")
    a = ap.parse_args(argv)

    st = load_state()
    if a.status:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    cur = load_edb()
    if not (cur.get("hike") or {}).get("ok"):
        print("[rate_prob] 取数失败：%s" % ((cur.get("hike") or {}).get("error") or "unknown"))
        return 1
    obs = (cur.get("hike") or {}).get("obs") or dt.date.today().isoformat()

    prev = st.get("current") or {}
    prev_meet = st.get("meeting") or ""
    meet = cur.get("meeting") or ""

    # 首次记录：只建基线，不提醒。dry-run 不写任何状态，避免预演把当天真发送幂等掉。
    if not prev:
        if a.send:
            save_state({"meeting": meet, "obs": obs, "current": {k: cur.get(k) for k in
                        ("ease", "hold", "hike", "target_mid", "days")},
                        "last_alert_obs": st.get("last_alert_obs")})
            print("[rate_prob] 初次记录会议（%s），已建基线，不提醒" % (meet or "—"))
        else:
            print("[rate_prob] dry-run：初次记录会议（%s），未写基线" % (meet or "—"))
        return 0

    # 换场：两场会议的概率不可直接相减，但仍要提醒——把「上一场最终定价」与
    # 「新会议初始定价」并列发出，避免用户以为页面停更（2026-11-25 校验意见）。
    if meet and prev_meet and meet != prev_meet:
        if st.get("last_alert_obs") == obs:
            print("[rate_prob] %s 已提醒过（换场），跳过（幂等）" % obs)
            return 0
        days = (cur.get("days") or {}).get("value")
        lines = ["CME FedWatch 会议切换：%s → %s" % (prev_meet, meet), "",
                 "上一场会议的最终定价（%s）：" % prev_meet]
        for _k, _lb in (("ease", "降息"), ("hold", "不变"), ("hike", "加息")):
            _v = (prev.get(_k) or {}).get("value")
            if _v is not None:
                lines.append("  %s：%.1f%%" % (_lb, float(_v)))
        lines += ["", "新会议的初始定价（%s，距今 %s 天）：" % (
            meet, ("%.0f" % days) if days is not None else "—")]
        for _k, _lb in (("ease", "降息"), ("hold", "不变"), ("hike", "加息")):
            _v = (cur.get(_k) or {}).get("value")
            if _v is not None:
                lines.append("  %s：%.1f%%" % (_lb, float(_v)))
        lines += ["", "各目标区间概率（现在 / 1天前 / 1周前 / 1月前）："]
        for r in cur.get("rows") or []:
            if max(r.get("now") or 0, r.get("d1") or 0, r.get("w1") or 0, r.get("m1") or 0) <= 0:
                continue
            lines.append("  %s%s：%.1f%% / %.1f%% / %.1f%% / %.1f%%" % (
                r.get("range"), "（当前）" if r.get("current") else "",
                r.get("now") or 0, r.get("d1") or 0, r.get("w1") or 0, r.get("m1") or 0))
        lines += ["", "说明：会议已换场，两场会议的概率不可直接相减，本封只做口径接力；"
                      "同一场会议内任一档变动 ≥ %.0f 个百分点另发变化提醒。" % THRESHOLD_PP,
                  "口径：CME FedWatch（30 天联邦基金期货定价），数据来自 DataHub EDB "
                  "quikstrike:fedwatch。",
                  "来自 DoMate · 中美期权风险释放汇总"]
        subject = "【利率概率】FedWatch 会议切换：%s → %s" % (prev_meet, meet)
        body = "\n".join(lines)
        print(body)
        ok = True
        if a.send:
            try:
                import email_alert
                ok = email_alert.send_email(subject, body)
            except Exception as exc:                           # noqa: BLE001
                print("[rate_prob] 邮件发送异常：%s" % exc)
                ok = False
        if ok:
            if a.send:
                save_state({"meeting": meet, "obs": obs,
                            "current": {k: cur.get(k) for k in
                                        ("ease", "hold", "hike", "target_mid", "days")},
                            "last_alert_obs": obs})
                print("[rate_prob] 换场提醒已发送（%s）" % obs)
            else:
                print("[rate_prob] 换场提醒（dry-run 未发，不写基线，%s）" % obs)
            return 2
        print("[rate_prob] 换场邮件发送失败，基线不推进，下次继续尝试")
        return 1

    changes = diff(cur, prev)
    mid_now = (cur.get("target_mid") or {}).get("value")
    mid_prev = (prev.get("target_mid") or {}).get("value")
    mid_chg = None
    if mid_now is not None and mid_prev is not None:
        mid_chg = (float(mid_now) - float(mid_prev)) * 100.0  # % → bp

    hit = [c for c in changes if abs(c[3]) >= THRESHOLD_PP]
    mid_hit = mid_chg is not None and abs(mid_chg) >= THRESHOLD_MID_BP
    if not hit and not mid_hit:
        if a.send:
            save_state({"meeting": meet, "obs": obs,
                        "current": {k: cur.get(k) for k in ("ease", "hold", "hike", "target_mid", "days")},
                        "last_alert_obs": st.get("last_alert_obs")})
        print("[rate_prob] 概率变动未达阈值（%s）" % (
            "、".join("%s%+.1fpp" % (c[0], c[3]) for c in changes) or "无有效对比"))
        return 0

    if st.get("last_alert_obs") == obs:
        print("[rate_prob] %s 已提醒过，跳过（幂等）" % obs)
        return 0

    subject, body = build_mail(cur, prev, hit or changes, mid_chg)
    print(body)
    ok = True
    if a.send:
        try:
            import email_alert
            ok = email_alert.send_email(subject, body)
        except Exception as exc:                               # noqa: BLE001
            print("[rate_prob] 邮件发送异常：%s" % exc)
            ok = False
    if ok:
        if a.send:
            save_state({"meeting": meet, "obs": obs,
                        "current": {k: cur.get(k) for k in ("ease", "hold", "hike", "target_mid", "days")},
                        "last_alert_obs": obs})
            print("[rate_prob] 已触发并发送（%s）" % obs)
        else:
            print("[rate_prob] 已触发（dry-run 未发，不写基线，%s）" % obs)
        return 2
    print("[rate_prob] 邮件发送失败，基线不推进，下次继续尝试")
    return 1


if __name__ == "__main__":
    sys.exit(main())