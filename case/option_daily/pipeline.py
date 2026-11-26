# -*- coding: utf-8 -*-
"""option_daily 的框架适配：fetch → check → render → emit → dispatch

调度（一天三档，与原 crontab 一一对应）
-------------------------------------
原 crontab 三次调用：

    20 22 * * 1-5  option_daily.py                → 22:20 主报告（全量）
    50 22 * * 1-5  option_daily.py --smile-only   → 22:50 只重算微笑/偏度
    30 23 * * 1-5  option_daily.py --smile-only   → 23:30 再重算一次微笑/偏度

task.json 的 ``sched.at`` 写成 ``["22:20","22:50","23:30"]``。runner 不知道
「这次是哪个时间点触发的」，所以**按时间分流**：以当前时钟靠近哪个档位判定模式
（容差 25 分钟，覆盖 scheduler 的 15s 轮询与 6h 宽限）。

为什么 22:50 / 23:30 只跑 smile
-------------------------------
原脚本两次 ``--smile-only`` 是同一个功能重复跑，目的是「等 akshare 出当日的正式
盘后链之后再补一次微笑」：22:20 那档用的往往还是盘中/延时数据，22:50、23:30 是
拿更晚落地的链重算偏度、覆盖前一次结果。GEX 依赖持仓量（OI），当日 OI 只在
盘后结算后才稳定，22:20 之后不会变，所以不需要重算 —— 这正是原脚本只在
``--smile-only`` 里跳过 GEX 的原因。这里照搬同一分流：

    22:20  全量：IV / 偏度 / PCR / GEX Flip → 出报告 + 出图 + emit
    22:50  只重算偏度与 ATM IV，覆盖 report.md 的第①②栏与 smile 图，**不重算 GEX**
    23:30  同上（再补一次；若 22:50 已拿到当日正式链，这档结果与之一致）

实现：``fetch`` 永远取链（带当日缓存，22:50/23:30 命中 22:20 落的缓存就不再打
TuShare）；``render`` 看 mode 决定是否调用 GEX 计算。这样既保留了原语义，
又不会为了「只重算微笑」把整条链接口再拉三遍。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 三档计划点（与 task.json 的 sched.at 保持一致）
SLOTS = ("22:20", "22:50", "23:30")
FULL_SLOT = "22:20"
SLOT_TOL_MIN = 25        # 判定容差（分钟）


def _state(ctx, name, obj=None):
    p = os.path.join(ctx.data_dir, "stage_%s.json" % name)
    if obj is None:
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    os.makedirs(ctx.data_dir, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return obj


def _mode(now=None):
    """按当前时钟判定本档模式：full（22:20）/ smile（22:50、23:30）。

    显式覆盖：环境变量 ``OD_MODE=full|smile``（手工补跑 / 验证用）。
    """
    env = (os.environ.get("OD_MODE") or "").strip().lower()
    if env in ("full", "smile"):
        return env, "env:OD_MODE"
    now = now or datetime.datetime.now()
    cur = now.hour * 60 + now.minute
    best, bestd = None, 10 ** 6
    for s in SLOTS:
        h, m = s.split(":")
        d = abs(cur - (int(h) * 60 + int(m)))
        d = min(d, 24 * 60 - d)          # 跨零点
        if d < bestd:
            best, bestd = s, d
    if best is not None and bestd <= SLOT_TOL_MIN:
        return ("full" if best == FULL_SLOT else "smile"), "slot:%s(±%dmin)" % (best, bestd)
    # 落不进任何档（手动跑在白天）：按全量处理，宁可多算也不要少算
    return "full", "default(no slot matched, now=%s)" % now.strftime("%H:%M")


def fetch(ctx):
    """取链。同一交易日的缓存会被三档共用（22:50/23:30 不再打接口）。"""
    import data
    date_arg = os.environ.get("OD_DATE", "") or ""
    td, is_open, note = data.resolve_trade_date(date_arg)
    got = data.fetch_chain(td, use_cache=(os.environ.get("OD_NO_CACHE") != "1"))
    if not got.get("ok"):
        raise RuntimeError("期权链取数失败：%s" % (got.get("reason") or "未知"))
    ctx.log("交易日 %s（请求 %s，%s）缓存=%s｜链 %d 行｜现价 %.4f"
            % (td, date_arg or "today", "交易日" if is_open else "非交易日已回退",
               got.get("_cache"), got.get("n_rows") or 0, got.get("spot") or 0))
    _state(ctx, "fetch", {"trade_date": td, "requested": date_arg,
                          "is_open": is_open, "note": note,
                          "spot": got.get("spot"), "n_rows": got.get("n_rows")})
    return 0


def render(ctx):
    """按 mode 算指标、出报告、出图。"""
    import analyze
    import data
    import render as R

    mode, why = _mode()
    ctx.log("本次模式：%s（%s）" % (mode, why))
    date_arg = os.environ.get("OD_DATE", "") or ""
    td, _ok, _note = data.resolve_trade_date(date_arg)
    got = data.fetch_chain(td, use_cache=True)
    if not got.get("ok"):
        raise RuntimeError("render 阶段取不到链：%s" % (got.get("reason") or "未知"))
    rows, spot = got.get("rows") or [], float(got.get("spot") or 0)

    m = analyze.compute_metrics(rows, td, spot)
    contracts = analyze.with_iv(rows, td, spot)
    if mode == "smile":
        # 原脚本 --smile-only：只重算微笑/偏度，GEX 沿用 22:20 那档的结果
        prev = _state(ctx, "render") or {}
        prev_m = prev.get("metrics") or {}
        if prev_m.get("gex_flip") is not None:
            m["gex_flip"] = prev_m["gex_flip"]
            kept = (m.get("diagnostics") or {}).get("gex") or {}
            old = (prev_m.get("diagnostics") or {}).get("gex") or {}
            kept = dict(kept) if kept else dict(old)
            kept["reused_from"] = prev.get("as_of") or ""
            m["diagnostics"]["gex"] = kept
            ctx.log("GEX Flip 沿用上一档结果：%s" % m["gex_flip"])
        else:
            ctx.log("上一档没有 GEX Flip（本次为当日首档 smile），本栏留空")

    diag = m.get("diagnostics") or {}
    prev_for_text = None
    prev = _state(ctx, "render") or {}
    pm = prev.get("metrics") or {}
    if prev.get("as_of") == _asof(td) and pm:
        prev_for_text = pm
    text = R.build_text(m, td, spot, diag, mode=mode, prev=prev_for_text)
    os.makedirs(ctx.out_dir, exist_ok=True)
    with open(os.path.join(ctx.out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    ctx.log("报告：out/report.md（%d 字）" % len(text))

    png = R.build_smile_chart(contracts, spot, td, os.path.join(ctx.out_dir, "smile.png"))
    if png:
        ctx.log("图：out/smile.png")
    else:
        ctx.warn("微笑图未生成", ["链上有效 IV 不足 8 档（DataHub IV 反解可能失败）"], "warn")

    _state(ctx, "render", {"as_of": _asof(td), "mode": mode, "spot": spot,
                           "metrics": m, "diagnostics": diag})
    missing = [k for k in ("atm_iv", "iv_skew", "pcr_oi", "gex_flip")
               if m.get(k) is None]
    if missing:
        ctx.warn("部分指标缺失", ["缺：%s" % "、".join(missing),
                                  "原因见 out/report.md 与 diagnostics"], "warn")
    return 0


def _asof(td):
    return "%s-%s-%s" % (td[:4], td[4:6], td[6:]) if len(td) == 8 else td


def emit(ctx):
    """四条指标（sec=000300.SH）。算不出的写 missing。"""
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 阶段拿不到 render 结果")
    as_of = st.get("as_of") or ""
    m = st.get("metrics") or {}
    diag = st.get("diagnostics") or {}
    gx = diag.get("gex") or {}
    points = [
        {"key": "option.atm_iv", "sec": "000300.SH", "obs_date": as_of,
         "name": "沪深300ETF 平值IV", "value": m.get("atm_iv"), "dec": 4,
         "quality": "ok" if m.get("atm_iv") is not None else "missing"},
        {"key": "option.iv_skew", "sec": "000300.SH", "obs_date": as_of,
         "name": "沪深300ETF 25Δ偏度", "value": m.get("iv_skew"), "dec": 4,
         "quality": "ok" if m.get("iv_skew") is not None else "missing",
         "meta_json": {"expiry": diag.get("expiry"), "dte": diag.get("dte")}},
        {"key": "option.pcr_oi", "sec": "000300.SH", "obs_date": as_of,
         "name": "沪深300ETF 持仓PCR", "value": m.get("pcr_oi"), "dec": 3,
         "quality": "ok" if m.get("pcr_oi") is not None else "missing"},
        {"key": "option.gex_flip", "sec": "000300.SH", "obs_date": as_of,
         "name": "沪深300ETF Gamma Flip", "value": m.get("gex_flip"), "dec": 4,
         "quality": "ok" if m.get("gex_flip") is not None else "missing",
         "meta_json": {"net_gex_yi": gx.get("net_gex_yi"),
                       "dist_pct": gx.get("dist_pct"),
                       "reused_from": gx.get("reused_from"),
                       "reason": gx.get("reason") if m.get("gex_flip") is None else ""}},
    ]
    n_ok = sum(1 for p in points if p["quality"] == "ok")
    ctx.log("指标 %d 条（ok %d / missing %d）" % (len(points), n_ok, len(points) - n_ok))
    return points


def dispatch(ctx):
    """推送（optional）。默认不发；只有 OD_PUSH=1 时才真推。

    原脚本的推送是「卡片 + QQ 文本 + 邮件」三通道，且正文依赖群里别的应用发的
    日报（搬不过来）。这里只保留最小一档：框架统一出口 ``alert.send``，认
    ``CASE_ALERT_WEBHOOK``；没配就只落盘，不影响报告与指标。刻意不引用框架的
    推送凭据模块 —— runner 扫到那个模块名就强制要求配齐凭据，缺配置会让整个
    任务启动失败，而推送对本报是可选项。
    """
    if os.environ.get("OD_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过：未设 OD_PUSH=1（迁移期默认不推送）")
        return 0
    st = _state(ctx, "render")
    rp = os.path.join(ctx.out_dir, "report.md")
    if not os.path.isfile(rp):
        raise RuntimeError("dispatch 阶段找不到 out/report.md")
    body = open(rp, encoding="utf-8").read()
    import alert
    rec = alert.send(ctx.key, "沪深300ETF 期权日报 · %s（%s 档）"
                     % ((st or {}).get("as_of") or "", (st or {}).get("mode") or "full"),
                     body.splitlines()[:40], level="info")
    ctx.log("推送记录：%s（webhook=%s）" % (rec.get("ts"), rec.get("webhook") or "未配置"))
    return 0
