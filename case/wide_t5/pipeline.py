#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wide_t5 —— 宽基 T+5 周报 · 框架适配层

原实现
======
宿主机 crontab ``0 20 * * 0``（周日 20:00）跑 ``bash bin/wide_ens/t5_weekly.sh``：

    1. bin/wide_ens/update_data.py                 5 宽基日线 + 利差因子
    2. bin/wide_ens/wd_refresh.py --h 5 -p 4       重算 wd 家族 T+5（walk-forward）
    3. bin/wide_ens/unified_live.py --family ms|enh --code X --h 5 --depth 3|5 --fast
                                                   （20 次：5 指数 × 2 深度 × 2 家族）
    4. bin/wide_ens/unified_select.py              统一选模（滚 500 日分位）
    5. bin/wide_ens/unified_signal.py              出本期信号
    6. bin/wide_ens/t5_weekly.py                   出卡片 + 推送

迁移后
======
    fetch     DataHub 取数（五宽基日线 / 利差 / 跨市场）
    render    wd_refresh(H=5) → unified_live → unified_select → unified_signal
              → out/report.md（表格来自 unified_selected.json + unified_signal.json）
    emit      wd.t5.signal / wd.t5.conviction（5 宽基）
    dispatch  推送（默认关，WT5_PUSH=1 才发）

⚠️ 依赖关系（务必知道）
=======================
本任务写 ``<WS>/.cache/wide_ens/unified_selected.json`` 与 ``unified_signal.json``，
``case/wide_t1``（日频卡）和 ``case/wide_ens_weekly``（集成周报）**都读这两份文件**。
原 crontab 是周日 15:30 集成 → 周日 20:00 T+5 → 次日 08:30 T+1，天然错开。
迁进框架后三个任务都 ``enabled: false`` 双跑，**不要手动同时触发三个**：
同时跑会有两个进程同时写同一份 json（后写覆盖先写，且 unified_select 期间读到
半成品会得到「全部观望」）。README「任务之间的依赖与互斥」一节有顺序表。

另外 ``wd_refresh`` 是**重训**（5 指数 × walk-forward，CPU 密集，实测 3~6 分钟/
进程，4 进程并行约 6~10 分钟）。它属于「必须真算」的一步：wd 家族的 csv 里
``bagged`` 就是当日 score，不重算就永远停在旧日期（踩过：日频卡用了 3 天前的信号）。
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

import task_paths as P                                       # noqa: E402

KEY = "wide_t5"
# 同一转换：wide_ens 内部 code（000300_SH）→ DataHub 证券 ID（000300.SH）
SEC = {"000300_SH": "000300.SH", "000905_SH": "000905.SH", "000852_SH": "000852.SH",
       "000688_SH": "000688.SH", "399006_SZ": "399006.SZ"}


def _q(s):
    return "'%s'" % str(s).replace("'", "'\"'\"'")


def _py(ctx, code: str, timeout: int, tag: str) -> int:
    env = P.env_for_child()
    env["PYTHONPATH"] = os.pathsep.join(P.py_paths())
    os.makedirs(ctx.data_dir, exist_ok=True)
    script = os.path.join(ctx.data_dir, "_step_%s.py" % tag)
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    return ctx.sh("%s -u %s" % (_q(sys.executable), _q(script)), timeout=timeout, env=env)


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


# ── fetch ─────────────────────────────────────────────────────────────────
def fetch(ctx):
    rc = _py(ctx, (
        "import sys, os, json\n"
        "sys.path.insert(0, %r)\n"
        "import datahub_fetch as D\n"
        "rep = D.refresh_all()\n"
        "json.dump({'idx_ok': (rep.get('idx') or {}).get('ok', 0),\n"
        "           'idx_dates': (rep.get('idx') or {}).get('dates') or {},\n"
        "           'spread_cols': len((rep.get('spread') or {}).get('cols') or []),\n"
        "           'spread_missing': (rep.get('spread') or {}).get('missing') or [],\n"
        "           'xm_cols': (rep.get('xm') or {}).get('cols', 0)},\n"
        "          open(os.path.join(os.environ['CASE_DATA_DIR'], 'stage_fetch.json'),\n"
        "               'w'), ensure_ascii=False)\n"
    ) % P.lib_dir(), timeout=1800, tag="fetch")
    st = _state(ctx, "fetch") or {}
    if not st:
        raise RuntimeError("fetch 没产出 stage_fetch.json（rc=%s）" % rc)
    ctx.log("取数：宽基 %s/5 · 利差 %s 列（缺 %d）· 跨市场 %s 列"
            % (st.get("idx_ok"), st.get("spread_cols"),
               len(st.get("spread_missing") or []), st.get("xm_cols")))
    if (st.get("idx_ok") or 0) < 5:
        ctx.warn("宽基日线不足 5 只", ["wd_refresh 只对能读到的指数重训"], "warn")
    return 0


# ── render ────────────────────────────────────────────────────────────────
def render(ctx):
    """wd_refresh(H=5) → unified_live×20 → unified_select → unified_signal → report.md"""
    os.makedirs(ctx.out_dir, exist_ok=True)

    # 前置自检：unified_live 依赖两个**仓外资产**，缺了会 20/20 全失败
    # （候选曲线仍能读旧 csv，报告不会中断，但尾部不会更新 —— 必须显式告警）
    _raw = os.path.join(P.cache_root(), ".cache", "weekly_forecast", "raw")
    _lack = [f for f in ("orth_feats.csv", "opt_feat.csv")
             if not os.path.isfile(os.path.join(_raw, f))]
    if _lack:
        ctx.warn("缺仓外特征文件",
                 ["%s 不在 %s" % ("、".join(_lack), _raw),
                  "unified_live 会全部失败 → 候选曲线尾部停在旧日期",
                  "补法：从原宿主机拷贝这两个 csv（行业宽度/期权 PCR），DataHub 暂无构建器"],
                 "warn")

    # 1) 重算 wd 家族 T+5（walk-forward 重训，慢）
    rc = _py(ctx, (
        "import sys, subprocess, os\n"
        "sys.path.insert(0, %r)\n"
        "p = os.path.join(%r, 'wide_ens', 'wd_refresh.py')\n"
        "r = subprocess.run([sys.executable, p, '--h', '5', '-p', '4'],\n"
        "                   capture_output=True, text=True)\n"
        "print((r.stdout or '')[-1500:])\n"
        "print((r.stderr or '')[-800:])\n"
        "sys.exit(r.returncode)\n"
    ) % (P.lib_dir(), P.lib_dir()), timeout=2400, tag="wd_refresh")
    if rc != 0:
        raise RuntimeError("wd_refresh(H=5) 失败 rc=%s" % rc)
    ctx.log("wd 家族 T+5 重算完成")

    # 2) unified_live：5 指数 × {ms,enh} × depth{3,5}，全部要刷新
    rc = _py(ctx, (
        "import sys, subprocess, os\n"
        "sys.path.insert(0, %r)\n"
        "W = os.path.join(%r, 'wide_ens')\n"
        "p = os.path.join(W, 'unified_live.py')\n"
        "codes = ['000300_SH', '000905_SH', '000852_SH', '000688_SH', '399006_SZ']\n"
        "bad = 0\n"
        "for code in codes:\n"
        "    for fam in ('ms', 'enh'):\n"
        "        for d in ('3', '5'):\n"
        "            r = subprocess.run([sys.executable, p, '--family', fam, '--code', code,\n"
        "                                '--h', '5', '--depth', d, '--fast'],\n"
        "                               capture_output=True, text=True)\n"
        "            if r.returncode != 0:\n"
        "                bad += 1\n"
        "                print('  live %%-7s %%-4s d%%s 失败：%%s' %% (code, fam, d,\n"
        "                      (r.stderr or '').strip().splitlines()[-1:] or ''))\n"
        "print('unified_live 完成，失败 %%d/20' %% bad)\n"
        "sys.exit(0)     # 失败不致命：候选曲线 csv 仍在，只是尾部少一天\n"
    ) % (P.lib_dir(), P.lib_dir()), timeout=2400, tag="live")
    if rc != 0:
        ctx.warn("unified_live 异常", ["个别组合失败，候选曲线尾部可能少一天"], "warn")
    ctx.log("live 刷新完成")

    # 3) unified_select（挑模）+ 4) unified_signal（出本期信号）
    rc = _py(ctx, (
        "import sys, subprocess, os\n"
        "sys.path.insert(0, %r)\n"
        "W = os.path.join(%r, 'wide_ens')\n"
        "for name in ('unified_select.py', 'unified_signal.py'):\n"
        "    r = subprocess.run([sys.executable, os.path.join(W, name)],\n"
        "                       capture_output=True, text=True)\n"
        "    print('--', name, 'rc=', r.returncode)\n"
        "    if r.returncode != 0:\n"
        "        print((r.stderr or '')[-1200:])\n"
        "        sys.exit(r.returncode)\n"
    ) % (P.lib_dir(), P.lib_dir()), timeout=2400, tag="select")
    if rc != 0:
        raise RuntimeError("unified_select / unified_signal 失败 rc=%s" % rc)

    # 5) 出报告（读 json，不经卡片；卡片只在 dispatch 时用）
    sd = os.path.join(P.cache_root(), ".cache", "wide_ens")
    sel_p = os.path.join(sd, "unified_selected.json")
    sig_p = os.path.join(sd, "unified_signal.json")
    if not os.path.isfile(sel_p):
        raise RuntimeError("unified_select 没有产出 %s" % sel_p)
    with open(sel_p, encoding="utf-8") as f:
        sel = json.load(f)
    sig = {}
    if os.path.isfile(sig_p):
        with open(sig_p, encoding="utf-8") as f:
            sig = json.load(f)

    NM = {"000300_SH": "沪深300", "000905_SH": "中证500", "000852_SH": "中证1000",
          "000688_SH": "科创50", "399006_SZ": "创业板"}
    ORDER = ["000300_SH", "000905_SH", "000852_SH", "000688_SH", "399006_SZ"]
    sigmap = {(r.get("name"), r.get("h"), r.get("mode")): r
              for r in sig.get("signals") or []}

    rows = []
    for code in ORDER:
        nm = NM[code]
        best = None
        for mode in ("precise", "broad"):
            r = ((sel.get("selected") or {}).get(code) or {}).get("5", {}).get(mode) or {}
            if r.get("status") not in ("ok", "below_bar"):
                continue
            key = (1 if r.get("status") == "ok" else 0, (r.get("oos") or {}).get("hit") or 0)
            if best is None or key > best[0]:
                best = (key, dict(r, mode=mode))
        if best is None:
            rows.append({"code": code, "sec": code, "name": nm, "status": "no_model",
                         "state": "无达标模型", "hit": None, "n": 0, "net": None,
                         "cand": "", "mode": "", "score": None, "triggered": False})
            continue
        r = best[1]
        sg = sigmap.get((nm, 5, r["mode"])) or {}
        oos = r.get("oos") or {}
        sc = sg.get("score")
        if sc is None:
            state = "无本期信号"
        elif r.get("status") != "ok":
            state = "观望（未达标）"
        elif not sg.get("triggered"):
            state = "未触发（观望）"
        else:
            state = "看多" if sc > 0 else "看空（回避）"
        rows.append({"code": code, "sec": code, "name": nm,
                     "status": r.get("status"), "state": state,
                     "hit": oos.get("hit"), "n": oos.get("n"), "net": oos.get("net"),
                     "cand": r.get("cand"), "mode": r.get("mode"), "score": sc,
                     "pct": sg.get("pct"), "triggered": bool(sg.get("triggered"))})

    as_of = ""
    try:
        ds = sorted({str(x.get("sig_date")) for x in rows if x.get("sig_date")})
        as_of = ds[-1] if ds else ""
    except Exception:                                        # noqa: BLE001
        pass
    if not as_of:
        as_of = (sig.get("updated") or sel.get("updated") or "")[:10]

    md = ["# 宽基 T+5 周报（统一选模）· %s" % as_of, "",
          "**周期** T+5（约一周）｜ **选模** IS(2019~2022) 挑 → OOS(2023+) 验收 "
          "｜ 门槛 T+5 ≥65%、n≥25、p≤0.10、净>0", "",
          "| 指数 | 本期状态 | 方向分 | 分位 | 选中模型 | 档位 | OOS 胜率 |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] == "no_model":
            md.append("| %s | 无达标模型 | — | — | — | — | — |" % r["name"])
            continue
        md.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["name"], r["state"],
            ("%+.3f" % r["score"]) if r["score"] is not None else "—",
            ("%.2f" % r["pct"]) if r.get("pct") is not None else "—",
            r["cand"], r["mode"],
            ("%.1f%%(n=%s)" % (r["hit"] * 100, r["n"])) if r["hit"] is not None else "—"))
    md += ["", "## 口径", "", "| 项 | 说明 |", "|---|---|",
           "| 候选家族 | ms(lgb_tri 深度3/5) / enh / train / wd(每日 bagged) |",
           "| 门槛统一 | 各家族 |score| 换成自身滚动 500 日分位，按分位定门槛（跨家族可比） |",
           "| IS/OOS | IS 2019~2022 只用于挑（Wilson 下限排序），OOS 2023 起只验收 |",
           "| T+5 去重叠 | wd-T+5 先按完整日历每 5 日取 1 再套门槛，避免重叠抬高显著性 |",
           "| 数据源 | DataHub EDB；任务侧不直连 |", ""]
    rp = os.path.join(ctx.out_dir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    ctx.log("报告 → %s（%d 指数，%d 字）"
            % (os.path.relpath(rp, ctx.dir), len(rows), len("\n".join(md))))
    _state(ctx, "render", {"as_of": as_of, "rows": rows})
    return 0


# ── emit ──────────────────────────────────────────────────────────────────
def emit(ctx):
    """wd.t5.signal / wd.t5.conviction。

    signal     +1 看多 / -1 看空 / 0 观望（未触发或未达标）
    conviction 该槽位 OOS 真实命中率 ×100（%）；未达标或无模型 → missing
    """
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 拿不到 render 结果")
    as_of = st.get("as_of") or ""
    points = []
    for r in st.get("rows") or []:
        code, nm = r.get("code"), r.get("name")
        if not code:
            continue
        trig = bool(r.get("triggered"))
        sc = r.get("score")
        if r.get("status") == "no_model" or sc is None:
            sig, q = None, "missing"
        elif trig and r.get("status") == "ok":
            sig, q = (1 if sc > 0 else -1), "ok"
        else:
            sig, q = 0, "ok"
        points.append({"key": "wd.t5.signal", "sec": SEC.get(code, code), "obs_date": as_of,
                       "name": "%s T+5 方向" % nm, "value": sig, "dec": 0,
                       "quality": q,
                       "meta_json": {"score": sc, "pct": r.get("pct"),
                                     "cand": r.get("cand"), "mode": r.get("mode"),
                                     "status": r.get("status"), "state": r.get("state")}})
        hit = r.get("hit")
        points.append({"key": "wd.t5.conviction", "sec": SEC.get(code, code), "obs_date": as_of,
                       "name": "%s T+5 信心度" % nm,
                       "value": (round(hit * 100, 1) if hit is not None else None),
                       "dec": 1, "unit": "%",
                       "quality": "ok" if hit is not None else "missing",
                       "meta_json": {"basis": "OOS 真实命中率", "n": r.get("n"),
                                     "net": r.get("net"), "cand": r.get("cand")}})
    n_ok = sum(1 for p in points if p.get("quality") == "ok")
    ctx.log("指标 %d 条（ok %d / missing %d）" % (len(points), n_ok, len(points) - n_ok))
    return points


# ── dispatch ──────────────────────────────────────────────────────────────
def dispatch(ctx):
    st = _state(ctx, "render") or {}
    if os.environ.get("WT5_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过推送：未设 WT5_PUSH=1（迁移期默认不发）")
        return 0
    rp = os.path.join(ctx.out_dir, "report.md")
    body = open(rp, encoding="utf-8").read()
    import alert
    rec = alert.send(ctx.key, "宽基 T+5 周报 · %s" % (st.get("as_of") or ""),
                     body.splitlines()[:40], level="info")
    ctx.log("推送记录 %s（webhook=%s）" % (rec.get("ts"), rec.get("webhook") or "未配置"))
    return 0
