#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1 监测告警脚本
每月央行金融数据发布后运行一次（默认 15日09:00），检测两个触发条件（仅此两条）：
  ① 上升高分位：最新 M1 同比处于历史序列的 80 分位及以上（默认 pctile-up=80）-> 发邮件
  ② 下降低分位：最新 M1 同比处于历史序列的 20 分位及以下（默认 pctile-down=20）-> 发邮件
中间区间（20 < 分位 < 80）不触发、静默。任一触发支路命中 -> 发邮件通知（dws 主通道 + 163 SMTP 回退），同月幂等不重复发。

数据源：DataHub EDB 序列 CN.M1.YOY（源头 akshare macro_china_money_supply / tushare cn_m，2008年至今）。
分位定义：经验分位数 = 历史序列中小于等于当前 M1 同比的月份占比（0-100）。
用法：
  python m1_alert.py                  # 正常运行（触发才发邮件）
  python m1_alert.py --force          # 忽略同月幂等，强制重发
  python m1_alert.py --dry-run        # 只计算打印，不发送
  python m1_alert.py --pctile-up 80 --pctile-down 20 --window 240
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, ensure_dirs
import edb_bridge as edb
from pusher import send_email

STATE_FILE = os.path.join(DATA_DIR, "m1_alert_state.json")


def fetch_m1_series(max_rows=240):
    """返回降序列表: [{month:'2026-10', m1_yoy:4.4}, ...]（读 DataHub EDB）"""
    # EDB 为唯一数据入口：月频 TTL 3 天，月度任务每次都会真实刷新（EDB_UPDATE=1 打开联网）
    os.environ.setdefault("EDB_UPDATE", "1")
    rep = edb.update(["CN.M1.YOY"], force=False)
    if rep.get("failed"):
        print("[M1告警] EDB 刷新失败，改用库内已有数据：", rep.get("failed"))
    out = []
    for month, v in edb.month_series("CN.M1.YOY", limit=max_rows):
        if v is None:
            continue
        out.append({"month": month, "m1_yoy": float(v)})
    return out


def parse_month(month_str):
    """'2026年08' -> (2026, 8)"""
    s = month_str.replace("年", "-").replace("月份", "").replace("月", "").strip()
    parts = s.split("-")
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def calc_metrics(series):
    """series 降序；返回最新值、环比、3月变化、经验分位数。"""
    if not series:
        return None
    latest = series[0]
    prev = series[1] if len(series) > 1 else None
    three_ago = series[3] if len(series) > 3 else None
    mom = round(latest["m1_yoy"] - prev["m1_yoy"], 2) if prev else None
    chg3 = round(latest["m1_yoy"] - three_ago["m1_yoy"], 2) if three_ago else None

    vals = [r["m1_yoy"] for r in series]
    n = len(vals)
    below = sum(1 for v in vals if v <= latest["m1_yoy"])
    pctile = round(below * 100.0 / n, 1)  # 经验分位数: 历史中小于等于当前值的占比

    return {
        "latest": latest,
        "prev": prev,
        "three_ago": three_ago,
        "mom": mom,
        "chg3": chg3,
        "pctile": pctile,
        "n": n,
    }


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    ensure_dirs()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fmt_month(y, m):
    return "%04d年%02d" % (y, m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pctile-up", type=float, default=80.0, help="上升高分位触发阈值(0-100)，默认80")
    ap.add_argument("--pctile-down", type=float, default=20.0, help="下降低分位触发阈值(0-100)，默认20")
    ap.add_argument("--window", type=int, default=240, help="分位数计算窗口(月数)，默认240")
    ap.add_argument("--force", action="store_true", help="忽略同月幂等，强制重发")
    ap.add_argument("--dry-run", action="store_true", help="只计算，不发送")
    args = ap.parse_args()

    series = fetch_m1_series(args.window)
    if not series:
        print("[M1告警] 取数失败：未取到任何 M1 数据")
        sys.exit(1)

    m = calc_metrics(series)
    latest = m["latest"]
    parsed = parse_month(latest["month"])
    month_key = fmt_month(*parsed) if parsed else str(latest["month"])

    # 仅两条触发规则：上升达高分位 / 下降达低分位
    trig_up = m["pctile"] >= args.pctile_up
    trig_down = m["pctile"] <= args.pctile_down
    triggered = trig_up or trig_down

    print("== M1 监测快照 ==")
    print("数据月:", month_key, "| M1同比:", latest["m1_yoy"], "%")
    print("环比(较上月):", ("%+.1f pct" % m["mom"]) if m["mom"] is not None else "n/a",
          "| 较3个月前:", ("%+.1f pct" % m["chg3"]) if m["chg3"] is not None else "n/a")
    print("历史分位数(窗口%d个月): %.1f%% | 上升触发>=%.0f%% / 下降触发<=%.0f%%"
          % (m["n"], m["pctile"], args.pctile_up, args.pctile_down))
    print("触发: 上升高分位=%s(>=%.0f%%) 下降低分位=%s(<=%.0f%%) -> 总判定: %s"
          % (trig_up, args.pctile_up, trig_down, args.pctile_down,
             "触发" if triggered else "未触发(静默)"))

    if args.dry_run:
        print("[M1告警] dry-run，不发送")
        return

    state = load_state()
    if not triggered:
        save_state({"last_month": month_key, "last_sent_month": state.get("last_sent_month")})
        print("[M1告警] 未触发（分位处于中间区间），静默退出")
        return

    if not args.force and state.get("last_sent_month") == month_key:
        print("[M1告警] 当月已发送过(%s)，跳过(幂等)；--force 可强制重发" % month_key)
        return

    # 组装邮件（按触发支路写明原因）
    direction_txt = ""
    interpretation = ""
    if trig_up:
        direction_txt = "⚠ M1 同比升至历史高位区间（>= %.0f 分位）" % args.pctile_up
        interpretation = ("M1 升至高分位通常预示内需补库动能与 PPI 上行弹性增强，"
                          "需结合八大枢纽港口吞吐量、韩国出口等高频指标确认需求真实性。")
    elif trig_down:
        direction_txt = "⚠ M1 同比降至历史低位区间（<= %.0f 分位）" % args.pctile_down
        interpretation = ("M1 降至低分位通常预示内需偏弱、库存周期仍在磨底，"
                          "工业利润率弹性依赖出口链与价格端而非内需。")

    body = (
        "【M1 监测告警】%s\n\n"
        "数据月份：%s\n"
        "M1 同比：**%.1f%%**（上期 %.1f%%）\n"
        "环比变化：%+.1f pct｜较3个月前：%+.1f pct\n"
        "历史分位：%.1f%%（窗口 %d 个月，2008年至今）\n"
        "触发规则：%s\n\n"
        "解读：%s\n\n"
        "数据来源：中国人民银行货币供应量统计（经 DataHub EDB 序列 CN.M1.YOY 取数：akshare macro_china_money_supply）\n"
        "生成时间：%s\n本通知为规则化告警，非投资建议。"
        % (direction_txt, month_key, latest["m1_yoy"],
           m["prev"]["m1_yoy"] if m["prev"] else float("nan"),
           m["mom"] if m["mom"] is not None else 0.0,
           m["chg3"] if m["chg3"] is not None else 0.0,
           m["pctile"], m["n"], direction_txt, interpretation,
           datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    subject = "【M1监测】%s M1同比 %.1f%%（历史分位%.0f%%）触发告警" % (
        month_key, latest["m1_yoy"], m["pctile"])

    ok = send_email(subject, body)
    if ok:
        save_state({"last_month": month_key, "last_sent_month": month_key})
        print("[M1告警] 邮件已发送 ->", subject)
    else:
        print("[M1告警] 邮件发送失败")
        sys.exit(1)


if __name__ == "__main__":
    main()