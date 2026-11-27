#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wide_ens_weekly —— 宽基集成周报（统一选模）· 框架适配层

原实现
======
宿主机 crontab ``30 15 * * 0``（周日 15:30）跑 ``bash bin/wide_ens/unified_weekly.sh``：

    1. update_data.py                取数
    2. wd_refresh.py --h 1,5 -p 4    重算 wd 家族 T+1 + T+5（walk-forward 重训）
    3. 18 次 unified_live.py         补 live 尾部（5 指数 × 2 周期 × 2 深度 × {ms,enh}）
    4. unified_select.py             四张表（T+1/T+5 × 普适/高精度）
    5. unified_signal.py             本期信号
    6. unified_card.py               出卡片（--push 才发）

迁移后
======
    fetch     DataHub 取数
    render    wd_refresh(H=1,5) → 18 次 unified_live → unified_select → unified_signal
              → out/report.md（四张表：T+1/T+5 × 普适/高精度）
    emit      wd.ens.t1_best / wd.ens.t5_best / wd.ens.acc（5 宽基）
    dispatch  卡片 + 推送（默认关，WENS_PUSH=1 才发）

⚠️ **本任务是上游**
``wide_t5`` 与 ``wide_t1`` 都读本任务写出的
``<WS>/.cache/wide_ens/unified_selected.json`` / ``unified_signal.json``。
原 crontab 顺序：周日 15:30 本任务 → 周日 20:00 wide_t5 → 次日 08:30 wide_t1。
三者 enabled=false 双跑时，若要手工验证，**按这个顺序**，不要并行
（会两个进程同时写同一份 json）。

⚠️ ``--retrain``（重训 ms/enh/train 全家族）本 case **不搬**：那是调参动作、
单次 40+ 分钟、且会覆盖候选曲线历史。需要重训时在 deployment 侧手工跑原 sh。
本任务只保证「wd 家族 + live 尾部」是最新的，这也是原 sh 的默认行为（RETRAIN=0）。
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

KEY = "wide_ens_weekly"
CODES = ["000300_SH", "000905_SH", "000852_SH", "000688_SH", "399006_SZ"]
# wide_ens 内部用下划线 code（000300_SH），DataHub 指标仓用带点 ts_code（000300.SH）。
# 混用会让指标落到一个 catalog 里不存在的证券上（面板看不到），必须显式转换。
SEC = {"000300_SH": "000300.SH", "000905_SH": "000905.SH", "000852_SH": "000852.SH",
       "000688_SH": "000688.SH", "399006_SZ": "399006.SZ"}
NM = {"000300_SH": "沪深300", "000905_SH": "中证500", "000852_SH": "中证1000",
      "000688_SH": "科创50", "399006_SZ": "创业板"}


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
    ctx.log("取数：宽基 %s/5 · 利差 %s 列 · 跨市场 %s 列"
            % (st.get("idx_ok"), st.get("spread_cols"), st.get("xm_cols")))
    return 0


# ── render ────────────────────────────────────────────────────────────────
def render(ctx):
    """wd_refresh(H=1,5) → 18 次 live → select → signal → out/report.md"""
    os.makedirs(ctx.out_dir, exist_ok=True)

    _raw2 = os.path.join(P.cache_root(), ".cache", "weekly_forecast", "raw")
    _lack2 = [f for f in ("orth_feats.csv", "opt_feat.csv")
              if not os.path.isfile(os.path.join(_raw2, f))]
    if _lack2:
        ctx.warn("缺仓外特征文件",
                 ["%s 不在 %s" % ("、".join(_lack2), _raw2),
                  "unified_live 会全部失败 → 候选曲线尾部停在旧日期",
                  "补法：从原宿主机拷贝这两个 csv（行业宽度/期权 PCR），DataHub 暂无构建器"],
                 "warn")

    rc = _py(ctx, (
        "import sys, subprocess, os\n"
        "sys.path.insert(0, %r)\n"
        "p = os.path.join(%r, 'wide_ens', 'wd_refresh.py')\n"
        "r = subprocess.run([sys.executable, p, '--h', '1,5', '-p', '4'],\n"
        "                   capture_output=True, text=True)\n"
        "print((r.stdout or '')[-1200:])\n"
        "print((r.stderr or '')[-600:])\n"
        "sys.exit(r.returncode)\n"
    ) % (P.lib_dir(), P.lib_dir()), timeout=3000, tag="wd_refresh")
    if rc != 0:
        raise RuntimeError("wd_refresh(H=1,5) 失败 rc=%s" % rc)
    ctx.log("wd 家族 T+1/T+5 重算完成")

    # 18 次 live（5 指数 × 2 周期 × 2 深度 × {ms,enh}）
    rc = _py(ctx, (
        "import sys, subprocess, os\n"
        "sys.path.insert(0, %r)\n"
        "W = os.path.join(%r, 'wide_ens')\n"
        "p = os.path.join(W, 'unified_live.py')\n"
        "codes = ['000300_SH', '000905_SH', '000852_SH', '000688_SH', '399006_SZ']\n"
        "bad = 0; n = 0\n"
        "for code in codes:\n"
        "    for h in ('1', '5'):\n"
        "        for fam in ('ms', 'enh'):\n"
        "            for d in ('3', '5'):\n"
        "                n += 1\n"
        "                r = subprocess.run([sys.executable, p, '--family', fam,\n"
        "                                    '--code', code, '--h', h, '--depth', d,\n"
        "                                    '--fast'], capture_output=True, text=True)\n"
        "                if r.returncode != 0:\n"
        "                    bad += 1\n"
        "print('unified_live %%d/%%d 完成，失败 %%d' %% (n - bad, n, bad))\n"
        "sys.exit(1 if bad else 0)   # 有失败就让 rc 非零，外层 ctx.warn 才不是死代码\n"
    ) % (P.lib_dir(), P.lib_dir()), timeout=3000, tag="live")
    if rc != 0:
        ctx.warn("unified_live 异常", ["个别组合失败，候选曲线尾部可能少一天"], "warn")
    ctx.log("live 尾部补算完成")

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
    sigmap = {(r.get("name"), r.get("h"), r.get("mode")): r
              for r in sig.get("signals") or []}

    # 每个 (指数, H) 按 status 高 + OOS 命中率高挑一个最优槽位
    best = {}
    for code in CODES:
        for h in ("1", "5"):
            pick = None
            for mode in ("precise", "broad"):
                r = ((sel.get("selected") or {}).get(code) or {}).get(h, {}).get(mode) or {}
                if r.get("status") not in ("ok", "below_bar"):
                    continue
                key = (1 if r.get("status") == "ok" else 0,
                       (r.get("oos") or {}).get("hit") or 0)
                if pick is None or key > pick[0]:
                    pick = (key, dict(r, mode=mode))
            best[(code, h)] = pick[1] if pick else None

    # 覆盖率：四张表（T+1/T+5 × 两种档位）各有几个达标槽位
    cov = {}
    total = 0
    okn = 0
    for h in ("1", "5"):
        for mode in ("precise", "broad"):
            n = 0
            for code in CODES:
                r = ((sel.get("selected") or {}).get(code) or {}).get(h, {}).get(mode) or {}
                total += 1
                if r.get("status") == "ok":
                    n += 1
                    okn += 1
            cov["%s_%s" % (h, mode)] = n

    as_of = (sig.get("updated") or sel.get("updated") or "")[:10]

    md = ["# 宽基集成周报（统一选模 · 四张表）· %s" % as_of, "",
          "**四张表** = T+1/T+5 × 普适（覆盖 35~65%）/ 高精度（覆盖 3~22%）",
          "｜ 候选家族 ms / enh / train / wd，各家族分数统一换成滚动 500 日分位", "",
          "## 一、每个指数的最优槽位", "",
          "| 指数 | 周期 | 档位 | 选中模型 | 状态 | OOS 胜率 | 触发率 | 本期信号 |",
          "|---|---|---|---|---|---|---|---|"]
    for code in CODES:
        for h in ("1", "5"):
            r = best.get((code, h))
            if not r:
                md.append("| %s | T+%s | — | 无达标模型 | — | — | — | — |" % (NM[code], h))
                continue
            sg = sigmap.get((NM[code], int(h), r.get("mode"))) or {}
            sc = sg.get("score")
            st = sg.get("state") or ("看多" if (sc or 0) > 0 else
                                    ("看空" if sc is not None else "无信号"))
            oos = r.get("oos") or {}
            md.append("| %s | T+%s | %s | %s | %s | %s | %s | %s |" % (
                NM[code], h, r.get("mode"), r.get("cand"), r.get("status"),
                ("%.1f%%(n=%s)" % (oos["hit"] * 100, oos.get("n"))) if oos.get("hit") else "—",
                ("%.0f%%" % (r["oos"]["cov"] * 100)) if (r.get("oos") or {}).get("cov") else "—",
                st))

    md += ["", "## 二、四张表覆盖率（达标槽位数 / 5 个指数）", "",
           "| 周期 | 档位 | 达标数 |", "|---|---|---|"]
    for h in ("1", "5"):
        for mode in ("precise", "broad"):
            md.append("| T+%s | %s | %d/5 |" % (h, mode, cov.get("%s_%s" % (h, mode), 0)))
    md += ["", "**总达标 %d/%d 槽位**" % (okn, total), "",
           "## 口径", "", "| 项 | 说明 |", "|---|---|",
           "| 选模不后视 | IS 2019~2022 只用已实现数据挑（Wilson 下限），OOS 2023+ 只验收 |",
           "| 门槛统一 | |score| 换滚动 500 日自身分位，按分位门槛跨家族可比 |",
           "| 重叠处理 | wd-T+5 先按完整日历每 5 日取 1 再套门槛 |",
           "| 本任务为上游 | wide_t5 / wide_t1 都读本任务产出的 unified_selected/signal.json |",
           "| 数据源 | DataHub EDB；任务侧不直连 |", ""]

    rp = os.path.join(ctx.out_dir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    ctx.log("报告 → %s（%d 字，总达标 %d/%d）"
            % (os.path.relpath(rp, ctx.dir), len("\n".join(md)), okn, total))
    _state(ctx, "render", {"as_of": as_of, "cov": cov, "okn": okn, "total": total,
                           "best": {"%s|%s" % (c, h): (b or {}) for (c, h), b in best.items()},
                           "sigmap_keys": [[k[0], k[1], k[2]] for k in sigmap]})
    return 0


# ── emit ──────────────────────────────────────────────────────────────────
def emit(ctx):
    """wd.ens.t1_best / wd.ens.t5_best（最优模型名，文本）、wd.ens.acc（覆盖率 %）。

    为什么 best 用文本而不是数字：模型名（如 ``enh:lgb_tri_d3_T5``）是有信息量的
    标识，压成数字就没法看了。DataHub 的 value_text 正是为此设计。
    """
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 拿不到 render 结果")
    as_of = st.get("as_of") or ""
    points = []
    for code in CODES:
        nm = NM[code]
        for h, key in (("1", "wd.ens.t1_best"), ("5", "wd.ens.t5_best")):
            r = (st.get("best") or {}).get("%s|%s" % (code, h)) or {}
            cand = r.get("cand")
            points.append({"key": key, "sec": SEC[code], "obs_date": as_of,
                           "name": "%s %s 最优模型" % (nm, "T+1" if h == "1" else "T+5"),
                           "value_text": cand or "",
                           "quality": "ok" if cand else "missing",
                           "meta_json": {"mode": r.get("mode"),
                                         "status": r.get("status"),
                                         "oos_hit": (r.get("oos") or {}).get("hit"),
                                         "oos_n": (r.get("oos") or {}).get("n")}})
        # 覆盖率：该指数在四张表里达标了几张（0~4 → 百分比）
        n_ok = 0
        for h in ("1", "5"):
            r = (st.get("best") or {}).get("%s|%s" % (code, h)) or {}
            if r.get("status") == "ok":
                n_ok += 1
        points.append({"key": "wd.ens.acc", "sec": SEC[code], "obs_date": as_of,
                       "name": "%s 选模覆盖率" % nm,
                       "value": round(n_ok / 2.0 * 100, 1), "dec": 1, "unit": "%",
                       "quality": "ok",
                       "meta_json": {"basis": "T+1/T+5 两个周期里 status=ok 的比例",
                                     "n_ok": n_ok, "n_total": 2}})
    n_ok = sum(1 for p in points if p.get("quality") == "ok")
    ctx.log("指标 %d 条（ok %d / missing %d）" % (len(points), n_ok, len(points) - n_ok))
    return points


# ── dispatch ──────────────────────────────────────────────────────────────
def dispatch(ctx):
    st = _state(ctx, "render") or {}
    if os.environ.get("WENS_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过推送：未设 WENS_PUSH=1（迁移期默认不发）")
        return 0
    rp = os.path.join(ctx.out_dir, "report.md")
    body = open(rp, encoding="utf-8").read()
    import alert
    rec = alert.send(ctx.key, "宽基集成周报（统一选模）· %s" % (st.get("as_of") or ""),
                     body.splitlines()[:45], level="info")
    ctx.log("推送记录 %s（webhook=%s）" % (rec.get("ts"), rec.get("webhook") or "未配置"))
    return 0
