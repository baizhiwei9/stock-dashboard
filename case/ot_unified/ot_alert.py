#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ot_alert.py — 靠岸状态切换提醒 + 缠论图
==========================================
两件事：
1) 状态切换检测：前一交易日处于「靠岸中 / 未靠岸 / 上行偏离」，今日跨进「已靠岸」（W1 分位 <40）
   → 汇总成一封邮件发出（宽基 + 申万一级行业放同一封，不拆多封）。
2) 缠论图：引用 projects/techanly-summary 的 `charts.buypoint.draw_stock_grid`
   （缠论·分型版：K线 + 笔 + 中枢 + 买卖点），对当日已靠岸标的出两张日线图：
   第一张宽基、第二张行业；各图最多 20 个标的。

用法：
    python ot_alert.py                 # 检测 + 有切换才发（首次运行发一封初始化汇总）
    python ot_alert.py --force         # 无视切换，强制发一封当日靠岸汇总
    python ot_alert.py --dry-run       # 只打印不发邮件
    python ot_alert.py --charts-only   # 只出缠论图
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ot_core as oc

WS_ROOT = oc.WS_ROOT
TECH_WS = os.path.join(WS_ROOT, "projects", "techanly-summary")
os.environ.setdefault("TECH_WS", TECH_WS)
if TECH_WS not in sys.path:
    sys.path.insert(0, TECH_WS)

# 状态文件默认写到用户目录，不落进仓库（避免把运行态数据提交进 git）
STATE_PATH = os.environ.get(
    "OT_ALERT_STATE", os.path.join(os.path.expanduser("~"), ".ot_alert_state.json"))
OUT_DIR = os.environ.get("OT_ALERT_OUT", os.path.join(HERE, "output"))
LAND_Q = 40.0

# 邮件通道（钉钉企业邮箱 dws CLI 主通道）。
# 【P0-3 修复】原来把真实邮箱硬编码成默认值，仓库是公开的，等于把收发件人地址一起公开了。
# 现在默认留空：未配置时跳过发信并明确提示，不再回退到任何硬编码地址。
DWS = os.environ.get("DWS_BIN", os.path.expanduser("~/.local/bin/dws"))
MAIL_FROM = os.environ.get("ALERT_MAIL_FROM", "")
MAIL_TO = os.environ.get("ALERT_MAIL_TO", "")


# ============================================================================
# 状态
# ============================================================================

def tag_of(q, dm):
    if q < 40:
        return "已靠岸"
    if q < 60:
        return "靠岸中"
    if dm is not None and dm > 0:
        return "上行偏离"
    return "未靠岸"


def emoji_of(q, dm):
    if q < 40:
        return "🟢🌙"
    if q < 60:
        return "🟡●"
    if dm is not None and dm > 0:
        return "🟢↗"
    return "🔴⭐"


def collect_states(start=None, end=None, snap=None, ind=None):
    """返回 (snap, ind, rows)。rows 同时含宽基与行业，带 code/kind 供画图。
    snap/ind 可由调用方注入（日报里已经取过，避免重复取数 31 只行业）。"""
    if snap is None:
        snap = oc.build_landing_snapshot(start=start, end=end, verbose=False)
    if ind is None:
        ind = oc.build_industry_landing(start=start, end=end, verbose=False)
    rows = []
    for c in snap["codes"]:
        d = snap["detail"][c]
        rows.append({"key": c, "name": d["name"], "kind": "宽基", "code": c,
                     "q": float(d["w1_pct"]), "dir": d.get("dir_mean"),
                     "ret20": d.get("ret20"), "dd": d.get("dd_long"), "as_of": snap["as_of"]})
    for r in ind["rows"]:
        rows.append({"key": "SW" + r["code"], "name": r["name"], "kind": "行业", "code": r["code"],
                     "q": float(r["pct"]), "dir": r.get("dir_mean"),
                     "ret20": r.get("ret20"), "dd": r.get("dd"), "as_of": ind["as_of"]})
    return snap, ind, rows


def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(rows, as_of, triggered):
    state = {"as_of": as_of,
             "updated_at": date.today().isoformat(),
             "items": {r["key"]: {"name": r["name"], "kind": r["kind"],
                                  "q": round(r["q"], 1), "tag": tag_of(r["q"], r["dir"]),
                                  "triggered": bool(triggered)} for r in rows}}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    return state


def detect_switches(prev, rows):
    """前一状态为 靠岸中/未靠岸/上行偏离 → 今日 已靠岸，判为切换。首次（无 prev）不算切换。"""
    if not prev:
        return []
    items = prev.get("items", {})
    out = []
    for r in rows:
        old = items.get(r["key"])
        if not old:
            continue
        if r["q"] < LAND_Q and old.get("q", 100) >= LAND_Q:
            out.append({**r, "prev_q": old.get("q"), "prev_tag": old.get("tag")})
    return out


# ============================================================================
# 缠论图（引用 techanly-summary）
# ============================================================================

def load_ohlc(code: str, kind: str, start="2025-06-01", end=None):
    dh = oc._dh()
    end = end or date.today().isoformat()
    if kind == "宽基":
        df = dh.get_index_daily(ts_code=code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    else:
        # 直接用 DataHub 原始表：ot_core.get_sw_industry 会归一成只含 close/amount/vol，丢掉 OHLC 无法画 K 线
        df = dh.get_data("sw_index_daily", code=str(code),
                         start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise RuntimeError(f"{code} 无 OHLC 数据")
    d = df.copy()
    # get_index_daily 带 trade_date 列；ot_core.get_sw_industry 已把 trade_date 归一成 DatetimeIndex
    if "trade_date" in d.columns:
        d["dt"] = pd.to_datetime(d["trade_date"].astype(str))
    else:
        d["dt"] = pd.to_datetime(d.index)
    d = d.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    if "vol" not in d.columns:
        d["vol"] = np.nan
    return d[["dt", "open", "high", "low", "close", "vol"]].dropna(subset=["close"])


def _fix_cjk_font():
    """techanly-summary 的 charts.common 硬编码了 wqy/noto 的 .ttc 路径（本机不存在），
    import 后中文会变成方框。这里覆盖回本机实际可用的中文字体族。"""
    import matplotlib
    from matplotlib import font_manager

    fams = []
    for f in (os.path.join(HERE, "SimHei.ttf"),
              "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-VF.ttf",
              "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"):
        if os.path.exists(f):
            try:
                font_manager.fontManager.addfont(f)
                fams.append(font_manager.FontProperties(fname=f).get_name())
            except Exception:
                pass
    matplotlib.rcParams["font.sans-serif"] = fams + ["Noto Sans SC", "Droid Sans Fallback", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    return fams


def make_chan_charts(landed, out_path, title):
    """调用 techanly-summary 的 draw_stock_grid 出缠论图（最多 20 个标的）。

    【P0-3 修复】原实现把 `from charts.buypoint import draw_stock_grid` 放在
    try 之外，外部项目 techanly-summary 不在环境里时直接 ModuleNotFoundError，
    整个 ot_alert 进程中断——邮件不发、状态也不落盘。现在降级为「跳过缠论图」。
    """
    try:
        from charts.buypoint import draw_stock_grid
    except Exception as e:
        print(f"  [缠论] 跳过：未找到 techanly-summary 的 charts.buypoint（{type(e).__name__}: {e}）")
        print(f"  [缠论] 如需出图，请设置 TECH_WS 指向 techanly-summary 项目根目录")
        return None

    print(f"  [字体] {_fix_cjk_font()}")

    picks = []
    for r in landed[:20]:
        try:
            d = load_ohlc(r["code"], r["kind"])
            if len(d) < 60:
                continue
            picks.append({"name": r["name"], "code": r["code"], "df": d, "pct": r.get("ret20") or 0.0,
                          "vw_z": 0.0, "ind": r["kind"], "buys": [], "sells": []})
        except Exception as e:
            print(f"  [缠论] {r['name']} 取数失败：{e}")
    if not picks:
        return None
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        draw_stock_grid(picks, out_path, n_show=120, title=title,
                        ncol=1 if len(picks) == 1 else 2)
        return out_path if os.path.exists(out_path) else None
    except Exception as e:
        print(f"  [缠论] 画图失败：{e}")
        return None


# ============================================================================
# 邮件
# ============================================================================

def build_mail_body(switches, rows, snap, ind, generated=True):
    landed_wide = [r for r in rows if r["kind"] == "宽基" and r["q"] < LAND_Q]
    landed_ind = [r for r in rows if r["kind"] == "行业" and r["q"] < LAND_Q]
    L = []
    if generated:
        L.append(f"### 靠岸提醒（{snap['as_of']}）")
        L.append("")
        L.append("首次启用：建立状态基线，本次为当前已靠岸清单；之后仅在「靠岸中/未靠岸 → 已靠岸」切换时发信。")
    else:
        L.append(f"### 靠岸切换提醒（{snap['as_of']}）")
    L.append("")
    L.append(f"**本次切换 {len(switches)} 个**")
    for s in switches:
        L.append(f"- {emoji_of(s['q'], s['dir'])} {s['kind']}·{s['name']}：分位 {s['prev_q']:.0f}（{s['prev_tag']}）"
                 f" → **{s['q']:.0f}（已靠岸）**｜方向 {s['dir']:+.2f}｜20日 {s['ret20']:+.1f}%｜回撤 {s['dd']:+.1f}%")
    L.append("")
    L.append(f"**当前已靠岸：宽基 {len(landed_wide)} 个｜行业 {len(landed_ind)} 个**")
    L.append("")
    L.append("宽基：")
    for r in landed_wide:
        L.append(f"- {emoji_of(r['q'], r['dir'])} {r['name']}：分位 {r['q']:.0f}｜方向 {r['dir']:+.2f}"
                 f"｜20日 {r['ret20']:+.1f}%｜回撤 {r['dd']:+.1f}%")
    L.append("")
    L.append("行业（按分位升序前 15）：")
    for r in landed_ind[:15]:
        L.append(f"- {emoji_of(r['q'], r['dir'])} {r['name']}：分位 {r['q']:.0f}｜方向 {r['dir']:+.2f}"
                 f"｜20日 {r['ret20']:+.1f}%｜回撤 {r['dd']:+.1f}%")
    if len(landed_ind) > 15:
        L.append(f"- 其余 {len(landed_ind) - 15} 个：{'、'.join(r['name'] for r in landed_ind[15:])}")
    L.append("")
    L.append("口径：W1 分位 <40＝已靠岸；>60 且方向为正＝上行偏离（不是下跌风险）。"
             "靠岸是状态识别，不是买卖信号。附件两张缠论图（宽基/行业，日线）。")
    return "\n".join(L)


def send_mail(subject, body, attachments=()):
    if not MAIL_FROM or not MAIL_TO:
        print("[邮件] 未配置 ALERT_MAIL_FROM / ALERT_MAIL_TO，跳过发送"
              "（本仓库不内置任何邮箱地址，请用环境变量提供）")
        return {"skipped": "mail not configured"}
    if not os.path.exists(DWS):
        print(f"[邮件] 未找到 dws（{DWS}），跳过发送")
        return {"skipped": "dws not found"}
    cmd = [DWS, "mail", "message", "send", "--from", MAIL_FROM, "--to", MAIL_TO,
           "--subject", subject, "--content", body, "--format", "json"]
    for a in attachments:
        if a and os.path.exists(a):
            cmd += ["--attachment", a]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        print(f"[邮件] rc={proc.returncode} out={proc.stdout[:300]} err={proc.stderr[:200]}")
        return {"rc": proc.returncode, "out": proc.stdout[:500], "err": proc.stderr[:300]}
    except Exception as e:
        print(f"[邮件] 发送异常：{e}")
        return {"error": str(e)}


# ============================================================================
# 主流程
# ============================================================================

def run(send=True, force=False, charts_only=False, quiet=False, snap=None, ind=None, start=None, end=None):
    snap, ind, rows = collect_states(start=start, end=end, snap=snap, ind=ind)
    prev = load_state()
    switches = detect_switches(prev, rows)
    first_run = prev is None
    landed_wide = [r for r in rows if r["kind"] == "宽基" and r["q"] < LAND_Q]
    landed_ind = [r for r in rows if r["kind"] == "行业" and r["q"] < LAND_Q]

    print(f"[切换] {snap['as_of']}｜首次={first_run}｜切换 {len(switches)} 个｜"
          f"已靠岸：宽基 {len(landed_wide)}｜行业 {len(landed_ind)}")
    for s in switches:
        print(f"  {emoji_of(s['q'], s['dir'])} {s['kind']}·{s['name']} {s['prev_q']:.0f}→{s['q']:.0f}")

    # 缠论图
    charts = []
    if landed_wide:
        p = make_chan_charts(landed_wide, os.path.join(OUT_DIR, f"chan_wide_{snap['as_of']}.png"),
                             f"宽基已靠岸·缠论日线（{snap['as_of']}）")
        if p:
            charts.append(p)
            print(f"[图] {p}")
    if landed_ind:
        p = make_chan_charts(landed_ind, os.path.join(OUT_DIR, f"chan_ind_{snap['as_of']}.png"),
                             f"申万一级已靠岸行业·缠论日线（{snap['as_of']}）")
        if p:
            charts.append(p)
            print(f"[图] {p}")
    if charts_only:
        return {"switches": switches, "charts": charts}

    triggered = bool(switches) or first_run or force
    if triggered and send:
        body = build_mail_body(switches, rows, snap, ind, generated=first_run and not switches)
        if not switches:
            tag = "初始化" if first_run else "汇总"
            subject = (f"【靠岸提醒·{tag}】{snap['as_of']} 当前已靠岸 宽基{len(landed_wide)}/行业{len(landed_ind)}")
        else:
            subject = f"【靠岸切换提醒】{snap['as_of']} {len(switches)} 个标的转向已靠岸"
        send_mail(subject, body, charts)
    elif not triggered and not quiet:
        print("[邮件] 今日无切换，不发信")

    save_state(rows, snap["as_of"], triggered)
    return {"switches": switches, "charts": charts, "triggered": triggered}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--charts-only", action="store_true")
    args = ap.parse_args()
    run(send=not args.dry_run, force=args.force, charts_only=args.charts_only)


if __name__ == "__main__":
    main()