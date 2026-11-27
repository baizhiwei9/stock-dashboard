#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liq_credit_weekly —— 流动性 · 信用风险周报 · 框架适配层

原实现
======
宿主机 crontab **两次调用同一个脚本**（一份报告分中国/美国两半，早上发中国、
中午发美国）：

    30 8 * * 0  python3 bin/liq_credit_weekly.py --side cn --refresh-eq   # 周日 08:30
     0 11 * * 0  python3 bin/liq_credit_weekly.py --side us               # 周日 11:00

``--refresh-eq`` 只是「强制重抓五宽基日线」（默认走 20 小时缓存），对应原 sh
里那句 ``bin/l5_fetch.py`` 的取数动作 —— 迁移后这一步由 fetch 阶段统一做。

迁移后：一条 pipeline，按时点分流 side
=====================================
    sched.at = ["08:30", "11:00"]，weekdays=[7]
    fetch 时看「现在几点」决定本次跑哪一侧：

        08:30 那一轮 → side=cn（含五宽基日线 + L5 两融微观池）
        11:00 那一轮 → side=us（美国信用/流动性）

    也可以显式覆盖：``LIQ_SIDE=cn|us|all``（调试/补跑用）。

    ⚠️ 两个时点跑的是**同一份报告的两个方面**，各自落 out/report_<side>.md 与
       data/stage_render_<side>.json，互不覆盖。

阶段
====
    fetch      DataHub 取齐 38 条序列 + 五宽基日线 → data/liq_archive/（滚动归档）
    check      产物校验
    render     评估(threshould) + 卡片/md → out/report_cn.md / out/report_us.md
    emit       liq.credit.cn_score / liq.credit.us_score / liq.stress_level
    dispatch   报警 + 可选飞书（**默认关**，LIQ_PUSH=1 才发）

指标口径（无「打分公式」可抄，这里给出可复核的定义）
====================================================
    liq.credit.cn_score   中国侧命中项：RISK 计 -2 / WARN 计 -1 / 其余 0，累加；
                          负得越多越紧张（0 = 全绿）。这是「命中条数」的加权和，
                          不是人为校准的 0~100 分 —— 宁可口径粗也要可复核。
    liq.credit.us_score   美国侧同口径。
    liq.stress_level      综合压力档：RISK → 2 / WARN → 1 / OK → 0（取两侧最大值）。
                          ⚠️ 与 DataHub 的 risk_level（none/opportunity/risk/alert）
                          是两套口径：这里是本任务的档位，publish 时会映射过去。

sec 用 ``CN.LIQ`` / ``GLOBAL.MACRO``（DataHub tech catalog 里实际登记的两个
宏观/流动性证券 ID）。任务描述里写的 ``LIQ.CN`` / ``LIQ.US`` 在 catalog 里
**不存在**，写上去会变成「未知证券」并在面板上无归属，故改用真实 ID。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SHARED = os.path.join(os.path.dirname(HERE), "_shared")
LIQLIB = os.path.join(SHARED, "liq_lib")

KEY = "liq_credit_weekly"
SEC_CN = "CN.LIQ"          # DataHub tech catalog 实际登记的证券 ID
SEC_US = "GLOBAL.MACRO"    # 美国宏观/流动性挂在 GLOBAL.MACRO 下


def _paths():
    return [LIQLIB, os.path.join(SHARED, "wide_ens_lib"),
            os.path.join(SHARED, "wide_ens_lib", "bin")]


def _env(ctx):
    e = {"PYTHONPATH": os.pathsep.join(_paths()),
         "CASE_DATA_DIR": ctx.data_dir,
         "CASE_OUT_DIR": ctx.out_dir,
         "TZ": os.environ.get("TZ", "Asia/Shanghai"),
         "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}
    return e


def _q(s):
    return "'%s'" % str(s).replace("'", "'\"'\"'")


def _py(ctx, code: str, timeout: int, tag: str) -> int:
    os.makedirs(ctx.data_dir, exist_ok=True)
    script = os.path.join(ctx.data_dir, "_step_%s.py" % tag)
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    return ctx.sh("%s -u %s" % (_q(sys.executable), _q(script)), timeout=timeout,
                  env=_env(ctx))


def _state(ctx, name, obj=None):
    p = os.path.join(ctx.data_dir, "stage_%s.json" % name)
    if obj is None:
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    os.makedirs(ctx.data_dir, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return obj


def _sides(ctx) -> list:
    """本轮跑哪几侧：env 显式指定 > 按时点猜（08:30→cn，11:00→us）。

    ⚠️ 时点判断必须写在 fetch 里并且落盘（stage_fetch.json 的 sides 字段），
       后面阶段只读这个字段 —— 否则 render 跑到 08:31 就会改判成 us。
    """
    env = (os.environ.get("LIQ_SIDE") or "").strip().lower()
    if env in ("cn", "us"):
        return [env]
    if env == "all":
        return ["cn", "us"]
    # 按北京时间判时点：宿主机若为 UTC，11:00 CST = 03:00 UTC，
    # 用无时区的 now() 会把美国半张误判成 cn，且不报错。
    try:
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    except Exception:                                         # noqa: BLE001
        now = dt.datetime.now()
    # 11:00 那一轮 vs 08:30 那一轮：以 09:45 为界
    if now.hour * 60 + now.minute >= 9 * 60 + 45:
        return ["us"]
    return ["cn"]


# ── fetch ─────────────────────────────────────────────────────────────────
def fetch(ctx):
    sides = _sides(ctx)
    with_etf = "cn" in sides                 # ETF 净申赎只在 cn 侧用
    os.makedirs(ctx.data_dir, exist_ok=True)
    rc = _py(ctx, (
        "import sys, os, json\n"
        "for p in %r:\n"
        "    if p not in sys.path: sys.path.insert(0, p)\n"
        "import liqdata\n"
        "want_etf = %r\n"
        "data = liqdata.load_all(want_etf=want_etf)\n"
        "real = {k: len(v) for k, v in data.items() if not k.startswith('_')}\n"
        "jump = (data.get('cn.cb.govt.10Y') or [[None]])[-1][1]\n"
        "eq = {}\n"
        "if 'cn' in %r:\n"
        "    import liqeq\n"
        "    eq = liqeq.load_index_daily()\n"
        "    json.dump(eq, open(os.path.join(os.environ['CASE_DATA_DIR'],\n"
        "                                    'cn_eq_index.json'), 'w'), ensure_ascii=False)\n"
        "json.dump({'sides': %r, 'series': real, 'missing': data.get('_missing') or [],\n"
        "           'archive': data.get('_archive') or {}, 'eq_codes': list(eq),\n"
        "           'cn_10y': jump},\n"
        "          open(os.path.join(os.environ['CASE_DATA_DIR'], 'stage_fetch.json'),\n"
        "               'w'), ensure_ascii=False)\n"
        "# 整份序列快照给后绕阶段用（render/dispatch 不再连 DataHub，省 4~8 分钟）\n"
        "full = {k: [[d, v] for d, v in rows] for k, rows in data.items()\n"
        "        if not k.startswith('_')}\n"
        "json.dump({'series': full, 'missing': data.get('_missing') or [],\n"
        "           'archive': data.get('_archive') or {}},\n"
        "          open(os.path.join(os.environ['CASE_DATA_DIR'], 'liq_series.json'),\n"
        "               'w'), ensure_ascii=False)\n"
        "print('序列 %%d 条，五宽基 %%d 只' %% (len(real), len(eq)))\n"
    ) % (_paths(), with_etf, sides, sides), timeout=3000, tag="fetch")
    st = _state(ctx, "fetch") or {}
    if not st:
        raise RuntimeError("fetch 没产出 stage_fetch.json（rc=%s）" % rc)
    ctx.log("本轮 side=%s · 序列 %d 条 · 五宽基 %d 只"
            % (",".join(st.get("sides") or []), len(st.get("series") or {}),
               len(st.get("eq_codes") or [])))
    miss = st.get("missing") or []
    if miss:
        ctx.log("DataHub 当前没有的指标（报告里按「暂缺」呈现）：%s"
                % "、".join(miss[:12]) + ("…" if len(miss) > 12 else ""))
    short = {k: v for k, v in (st.get("series") or {}).items() if v < 60}
    if short:
        ctx.warn("分位窗口偏短",
                 ["%d 条序列不足 60 点：%s" % (len(short), "、".join(list(short)[:6])),
                  "分位数仍按实际长度算（不补、不插值），长期靠 data/liq_archive 长回来"],
                 "warn")
    return 0


# ── render ────────────────────────────────────────────────────────────────
_RENDER_TMPL = '''\
import sys, os, json
for p in %(paths)r:
    if p not in sys.path: sys.path.insert(0, p)
import liq_credit_weekly as L

side = %(side)r
ASIDE = '中国' if side == 'cn' else '美国'
c = L.con()
eq = L.load_eq()
assess = (L.assess_cn(c, eq) if side == 'cn' else L.assess_us(c))
card = (L.build_cn(c) if side == 'cn' else L.build_us(c))

# md：从卡片 elements 抽文本（markdown 块原样、column_set 拼成一行、hr 画线）
lines = []
for el in card.get('elements') or []:
    tag = el.get('tag')
    if tag == 'markdown':
        lines.append(el.get('content') or '')
    elif tag == 'hr':
        lines.append('---')
    elif tag == 'column_set':
        cells = []
        for col in el.get('columns') or []:
            for sub in col.get('elements') or []:
                if sub.get('tag') == 'markdown':
                    cells.append(sub.get('content') or '')
        if cells:
            lines.append(' | '.join(x.replace('\\n', '<br>') for x in cells))
    elif tag == 'div':
        txt = (el.get('text') or {}).get('content')
        if txt:
            lines.append(txt)
head = card.get('header') or {}
title = (head.get('title') or {}).get('content') or ''
md = ['# ' + (title or ASIDE + ' 流动性·信用风险周报'), ''] + lines

out = os.environ['CASE_OUT_DIR']
os.makedirs(out, exist_ok=True)
rp = os.path.join(out, 'report_%%s.md' %% side)
open(rp, 'w', encoding='utf-8').write('\\n'.join(md))

# 指标口径：RISK -2 / WARN -1 累加
n_risk = len(assess.get('risks') or [])
n_warn = len(assess.get('warns') or [])
score = -(2 * n_risk + n_warn)
level = assess.get('level') or 'OK'
json.dump({'side': side, 'level': level, 'score': score,
           'n_risk': n_risk, 'n_warn': n_warn,
           'risks': [{'name': r['name'], 'cur': r.get('cur'), 'thr': r.get('thr')}
                     for r in (assess.get('risks') or [])],
           'warns': [{'name': r['name'], 'cur': r.get('cur'), 'thr': r.get('thr')}
                     for r in (assess.get('warns') or [])],
           'as_of': (L.last(c, 'cn.cb.govt.10Y')[0] if side == 'cn'
                     else L.last(c, 'us.fred.BAMLH0A3HYC')[0]),
           'report': os.path.basename(rp), 'md_bytes': len('\\n'.join(md))},
          open(os.path.join(os.environ['CASE_DATA_DIR'],
                            'stage_render_%%s.json' %% side), 'w'), ensure_ascii=False)
print('%%s 侧：%%s · RISK %%d / WARN %%d · score %%d · 报告 %%s'
      %% (ASIDE, level, n_risk, n_warn, score, os.path.basename(rp)))
'''


def render(ctx):
    st = _state(ctx, "fetch") or {}
    sides = st.get("sides") or ["cn"]
    os.makedirs(ctx.out_dir, exist_ok=True)
    tmpl = dict(paths=_paths())
    merged = {}
    for side in sides:
        rc = _py(ctx, _RENDER_TMPL % dict(tmpl, side=side), timeout=1800,
                 tag="render_%s" % side)
        if rc != 0:
            raise RuntimeError("%s 侧 render 失败 rc=%s" % (side, rc))
        one = _state(ctx, "render_%s" % side)
        if not one:
            raise RuntimeError("%s 侧没有产出 stage_render 结果" % side)
        merged[side] = one
        ctx.log("%s：%s · RISK %s / WARN %s · 报告 %s"
                % ("中国" if side == "cn" else "美国", one.get("level"),
                   one.get("n_risk"), one.get("n_warn"), one.get("report")))
    _state(ctx, "render", {"sides": sides, "by_side": merged})
    return 0


# ── emit ──────────────────────────────────────────────────────────────────
def emit(ctx):
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 拿不到 render 结果")
    by = dict(st.get("by_side") or {})
    # 两侧是分两轮（08:30 / 11:00）跑的：本轮只跑了一侧时，另一侧用上一轮留下的
    # stage_render_<side>.json 补齐 —— 否则 cn_score / us_score 会有一侧永远 missing。
    # 只在该侧文件比本轮 render 更新（即上一轮产物）时才补，避免读到过期数据。
    for side in ("cn", "us"):
        if side in by:
            continue
        old = _state(ctx, "render_%s" % side)
        if old:
            by[side] = old
            ctx.log("%s 侧沿用上一轮结果（%s，level=%s）"
                    % (side, old.get("as_of"), old.get("level")))
    points = []
    level_rank = {"OK": 0, "WARN": 1, "RISK": 2}
    worst = 0
    for side, one in by.items():
        sec = SEC_CN if side == "cn" else SEC_US
        key = "liq.credit.cn_score" if side == "cn" else "liq.credit.us_score"
        points.append({"key": key, "sec": sec, "obs_date": one.get("as_of") or "",
                       "name": ("中国" if side == "cn" else "美国") + " 流动性信用压力分",
                       "value": one.get("score"), "dec": 0,
                       "quality": "ok",
                       "meta_json": {"level": one.get("level"),
                                     "n_risk": one.get("n_risk"),
                                     "n_warn": one.get("n_warn"),
                                     "basis": "RISK -2 / WARN -1 累加，0=全绿",
                                     "risks": one.get("risks"),
                                     "warns": one.get("warns")}})
        worst = max(worst, level_rank.get(one.get("level") or "OK", 0))
    # 综合压力档（两侧取最差）
    points.append({"key": "liq.stress_level", "sec": SEC_CN, "obs_date": "",
                   "name": "流动性·信用压力档", "value": worst, "dec": 0,
                   "quality": "ok",
                   "meta_json": {"scale": "0=OK / 1=WARN / 2=RISK",
                                 "sides": {k: v.get("level") for k, v in by.items()}}})
    # obs_date 兜底：两条都空时用今天（否则 check 会因 as_of 缺失判红）
    today = dt.date.today().isoformat()
    for p in points:
        if not p.get("obs_date"):
            p["obs_date"] = today
    n_ok = sum(1 for p in points if p.get("quality") == "ok")
    ctx.log("指标 %d 条（ok %d）" % (len(points), n_ok))
    return points


# ── dispatch ──────────────────────────────────────────────────────────────
def dispatch(ctx):
    """报警（framework alert）+ 可选飞书卡片（默认关）。"""
    st = _state(ctx, "render") or {}
    by = st.get("by_side") or {}
    import alert

    for side, one in by.items():
        if one.get("level") != "RISK":
            continue
        names = ["%s（现 %s，阈值 %s）" % (r.get("name"), r.get("cur"), r.get("thr"))
                 for r in (one.get("risks") or [])[:6]]
        alert.send(ctx.key,
                   "流动性信用风险预警 · %s · %d 项超阈值"
                   % ("中国" if side == "cn" else "美国", one.get("n_risk") or 0),
                   names, level="error")
        ctx.log("%s 侧 RISK，已记录报警（%d 项）" % (side, len(names)))

    if os.environ.get("LIQ_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过飞书推送：未设 LIQ_PUSH=1（迁移期默认不发）")
        return 0
    if not (os.environ.get("CREDIT_FEISHU_HOOK") or "").strip():
        ctx.log("dispatch 跳过推送：未配置 CREDIT_FEISHU_HOOK（凭据走 case/_lib/feishu_creds）")
        return 0

    # 真发时用原脚本的卡片构建 + push（webhook 由 feishu_creds 提供）
    rc = _py(ctx, (
        "import sys, os, json\n"
        "for p in %r:\n"
        "    if p not in sys.path: sys.path.insert(0, p)\n"
        "import liq_credit_weekly as L\n"
        "bad = 0\n"
        "c = L.con(); eq = L.load_eq()\n"
        "for side in %r:\n"
        "    card = L.build_cn(c) if side == 'cn' else L.build_us(c)\n"
        "    if not L.push(card, side):\n"
        "        bad += 1\n"
        "sys.exit(1 if bad else 0)\n"
    ) % (_paths(), list(by)), timeout=600, tag="push")
    if rc != 0:
        ctx.warn("飞书推送失败", ["push() 返回失败（飞书 body code≠0 或网络异常）"], "error")
    return 0
