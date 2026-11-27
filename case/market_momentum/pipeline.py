# -*- coding: utf-8 -*-
"""market_momentum 的框架适配：fetch → check → render → emit → dispatch

    fetch     经 DataHub 取宽基指数日线 + 申万一级行业日线，落 data/
    check     框架通用校验（data/ 有产物、out/latest.json 有指标）
    render    算动量排名/动量分/拐头，出 out/report.md + out/momentum.png
    emit      5 只宽基 × 3 个指标 → out/latest.json（再由 publish 落 DataHub）
    dispatch  任务自己的推送（**默认关**，只在显式开启时发）

调度
----
task.json 的 sched 是每天 20:35。原 crontab 是 ``35 20 * * 1-5`` + ``--qq``，
参数化的推送已折进 dispatch 阶段（optional: true），失败不影响报告与指标。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data import INDICES as _IND  # noqa: E402
SECS = list(_IND)


def _state(ctx, name, obj=None):
    """阶段间传值：落 data/stage_<name>.json，runner 只保证钩子顺序调用。"""
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


def fetch(ctx):
    """取数。非交易日由 DataHub 交易日历回退到最近交易日。"""
    import data
    date_arg = os.environ.get("MM_DATE", "") or ""
    got = data.fetch_all(date_arg, use_cache=(os.environ.get("MM_NO_CACHE") != "1"))
    idx_rows = (got.get("indices") or {}).get("rows") or []
    sw = got.get("sw") or {}
    ctx.log("交易日 %s（请求 %s，%s）缓存=%s"
            % (got.get("trade_date"), got.get("requested"),
               "交易日" if got.get("is_open") else "非交易日已回退", got.get("_cache")))
    ctx.log("指数日线 %d 行；申万一级 %s"
            % (len(idx_rows), "ok %d 行" % len(sw.get("rows") or []) if sw.get("ok")
               else "失败（%s）" % sw.get("reason")))
    if not idx_rows:
        raise RuntimeError("指数日线 0 行，无法计算动量")
    _state(ctx, "fetch", {"trade_date": got.get("trade_date"),
                          "requested": got.get("requested"),
                          "is_open": got.get("is_open"),
                          "note": got.get("note") or "",
                          "n_idx": len(idx_rows),
                          "sw_ok": bool(sw.get("ok")),
                          "sw_reason": (sw.get("reason") or "")})
    return 0


def render(ctx):
    """算动量 + 出报告与图。"""
    import analyze
    import data
    import render as R

    date_arg = os.environ.get("MM_DATE", "") or ""
    got = data.fetch_all(date_arg, use_cache=True)     # fetch 阶段已落盘，这里命缓存
    idx_rows = (got.get("indices") or {}).get("rows") or []
    sw_rows = (got.get("sw") or {}).get("rows") if (got.get("sw") or {}).get("ok") else None
    td = got.get("trade_date") or ""
    as_of = "%s-%s-%s" % (td[:4], td[4:6], td[6:]) if len(td) == 8 else td

    per = analyze.compute(idx_rows, SECS)
    sw = analyze.sw_ranking(sw_rows) if sw_rows else {"error": "取数阶段申万一级失败"}
    env = analyze.market_env(idx_rows)

    note = ""
    if not (got.get("is_open") is not False):
        note = "%s 非交易日，已回退到最近交易日 %s" % (got.get("requested"), as_of)

    text = R.build_text(per, SECS, sw, env, as_of, note=note)
    os.makedirs(ctx.out_dir, exist_ok=True)
    rp = os.path.join(ctx.out_dir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(text)
    ctx.log("报告：%s（%d 字）" % (os.path.relpath(rp, ctx.dir), len(text)))

    png = R.build_chart(per, SECS, as_of, os.path.join(ctx.out_dir, "momentum.png"))
    ctx.log("图：%s" % os.path.relpath(png, ctx.dir))

    _state(ctx, "render", {"as_of": as_of, "per": per, "sw": sw,
                           "env": env, "note": note})
    if not sw_rows:
        ctx.warn("行业动量缺块", ["申万一级取数失败，报告第③栏标注未生成（不影响宽基指标）"], "warn")
    return 0


def emit(ctx):
    """5 只宽基 × 3 指标。算不出来的写 missing，不补零。"""
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 阶段拿不到 render 结果")
    as_of = st.get("as_of") or ""
    per = st.get("per") or {}
    points = []
    for code, name in SECS:
        v = per.get(code) or {}
        obs = as_of
        points.append({"key": "momentum.rank", "sec": code, "obs_date": obs,
                       "name": "%s 动量排名" % name,
                       "value": v.get("rank"), "dec": 0, "unit": "",
                       "quality": "ok" if v.get("rank") is not None else "missing"})
        points.append({"key": "momentum.score", "sec": code, "obs_date": obs,
                       "name": "%s 动量分" % name,
                       "value": (round(v["score"], 1) if v.get("score") is not None else None),
                       "dec": 1, "unit": "",
                       "quality": "ok" if v.get("score") is not None else "missing"})
        tv = v.get("turn")
        points.append({"key": "momentum.turn", "sec": code, "obs_date": obs,
                       "name": "%s 拐头信号" % name,
                       "value": tv if tv is not None else None, "dec": 0,
                       "quality": "ok" if tv is not None else "missing",
                       "meta_json": {"mom_pct": v.get("mom"),
                                     "prev_score": v.get("prev_score"),
                                     "mom_days": 20}})
    n_ok = sum(1 for p in points if p.get("quality") == "ok")
    ctx.log("指标 %d 条（ok %d / missing %d）" % (len(points), n_ok, len(points) - n_ok))
    return points


def dispatch(ctx):
    """推送（optional）。默认不发；只有 MM_PUSH=1 时才真推。

    为什么默认关：迁移阶段要求「双跑但不动线上通道」，验证一律不真发消息。

    走的是框架统一的报警出口 ``_lib/alert.send``：它只认环境变量
    ``CASE_ALERT_WEBHOOK``（各 IM 的机器人地址都收同一格式），没配就只落盘
    out/alerts.json。刻意**不引用**框架的推送凭据模块 —— runner 会扫源码，
    只要出现那个模块名就要求配齐凭据，缺配置时直接让整个任务启动失败；
    本任务的推送是可选项，不该拖住报告与指标。
    """
    if os.environ.get("MM_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过：未设 MM_PUSH=1（迁移期默认不推送）")
        return 0
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("dispatch 阶段拿不到 render 结果")
    rp = os.path.join(ctx.out_dir, "report.md")
    if not os.path.isfile(rp):
        raise RuntimeError("dispatch 阶段找不到 out/report.md")
    body = open(rp, encoding="utf-8").read()
    import alert
    rec = alert.send(ctx.key, "A股宽基动量日报 · %s" % (st.get("as_of") or ""),
                     body.splitlines()[:40], level="info")
    ctx.log("推送记录：%s（webhook=%s）" % (rec.get("ts"), rec.get("webhook") or "未配置"))
    return 0
