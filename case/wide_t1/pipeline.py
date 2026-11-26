#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wide_t1 —— 宽基 T+1 双轨日报 · 框架适配层

原实现
======
宿主机 crontab ``30 8 * * 1-5`` 跑 ``bash bin/wide_ens/t1_daily.sh``，串四步：

    0. bin/wide_ens/trade_gate.py            交易日闸门（休市直接 exit 0）
    1. update_data.update_raw()              5 宽基日线增量
    2. research/chan_zs_t1/make_today_report.py
                                             today_dual → md + json
    2b. research/chan_zs_t1/dual_ledger.py   台账 settle / record / md
    3. research/chan_zs_t1/push_feishu.py    飞书卡片

迁移后的五阶段
==============
    fetch     交易日闸门 + DataHub 取五宽基日线/利差/跨市场因子 → data/gate.json
    check     产物校验（框架内置 artifacts）
    render    today_dual 算中枢/AC/波动档 → out/report.md + data/today_dual.json
    emit      5 宽基 × 3 指标（wd.t1.signal / ac_gate / conviction）
    dispatch  台账 settle+record + 推送（**默认关**，WT1_PUSH=1 才发）

关键口径
========
* 交易日闸门语义保留：休市 → 不计算、只写一张「休市跳过」说明，**不抛错**
  （抛错会每天给休市日刷一条告警噪音）。
* 预测分必须能算出来。迁移时踩过一个坑：``xm_incr_test.build_X`` 无条件读
  ``cross_market_factors.csv``，文件缺席时 today_dual 静默回退到 panel 最后一行
  （= 5 周前的旧预测）。所以 fetch 阶段**必须**把该文件造出来（DataHub GLOBAL.*）。
  这里加了一道自检：render 出来若 ``pred_std`` 为空，判本次预测不可信 → 报警 + 不写指标。

⚠️ 跨天保留状态
    <WS>/.cache/chan_zs_t1/panel_<指数>.csv   walk-forward 历史预测面板（必需）
    <WS>/.cache/chan_zs_t1/dual_ledger.db     双轨台账（必需，累年结算历史）
    <WS>/.cache/wide_ens/                     统一选模候选曲线（本任务不写，wide_ens_weekly 写）
  详见 README「跨天保留状态」。
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

KEY = "wide_t1"
SHARED = P.lib_dir()

NAME2CODE = {"沪深300": "000300.SH", "中证500": "000905.SH", "中证1000": "000852.SH",
             "科创50": "000688.SH", "创业板": "399006.SZ", "创业板指": "399006.SZ"}
ST = {"above": "中枢外·上", "below": "中枢外·下", "inside": "中枢内", "none": "无中枢"}


# ── 小工具 ────────────────────────────────────────────────────────────────
def _py(ctx, code: str, timeout: int, tag: str = "run") -> int:
    """在共享库环境里跑一段 python。模块根全部进 PYTHONPATH，脚本用 -c。"""
    env = P.env_for_child()
    env["PYTHONPATH"] = os.pathsep.join([
        SHARED,
        os.path.join(SHARED, "wide_ens"),
        os.path.join(SHARED, "chan_zs_t1"),
        os.path.join(SHARED, "bin"),
        os.path.join(SHARED, "wide_daily"),
        os.path.join(SHARED, "research_wf"),
    ])
    script = os.path.join(ctx.data_dir, "_step_%s.py" % tag)
    os.makedirs(ctx.data_dir, exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    return ctx.sh("%s -u %s" % (_q(sys.executable), _q(script)), timeout=timeout, env=env)


def _q(s: str) -> str:
    return "'%s'" % str(s).replace("'", "'\"'\"'")


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


def _code_of(nm: str) -> str:
    return NAME2CODE.get(nm, "")


# ── fetch ─────────────────────────────────────────────────────────────────
def fetch(ctx):
    """交易日闸门 → DataHub 取数 → data/gate.json / data/fetch_report.json"""
    os.makedirs(ctx.data_dir, exist_ok=True)
    rc = _py(ctx, (
        "import sys, os, json\n"
        "sys.path.insert(0, %r)\n"
        "DD = os.environ['CASE_DATA_DIR']\n"
        "gp = os.path.join(DD, 'gate.json')\n"
        "import trade_day as TD\n"
        "d, ok, note = TD.resolve()\n"
        "print('trade_day data_day=%%s is_open=%%s %%s' %% (d, ok, note))\n"
        "if not ok:\n"
        "    json.dump({'market_open': False, 'data_day': d, 'note': note}, open(gp, 'w'))\n"
        "    sys.exit(0)\n"
        "import datahub_fetch as D\n"
        "rep = D.refresh_all()\n"
        "json.dump({'market_open': True, 'data_day': d, 'note': note,\n"
        "           'idx_ok': (rep.get('idx') or {}).get('ok', 0),\n"
        "           'idx_dates': (rep.get('idx') or {}).get('dates') or {},\n"
        "           'spread_cols': len(((rep.get('spread') or {}).get('cols') or [])),\n"
        "           'spread_missing': (rep.get('spread') or {}).get('missing') or [],\n"
        "           'xm_cols': (rep.get('xm') or {}).get('cols', 0),\n"
        "           'xm_markets': (rep.get('xm') or {}).get('tags') or []},\n"
        "          open(gp, 'w'), ensure_ascii=False)\n"
    ) % SHARED, timeout=1800, tag="fetch")

    gate = None
    try:
        with open(os.path.join(ctx.data_dir, "gate.json"), encoding="utf-8") as f:
            gate = json.load(f)
    except (OSError, ValueError):
        pass
    if gate is None:
        raise RuntimeError("fetch 没产出 gate.json（rc=%s）" % rc)
    ctx.log("数据日 %s · 开市=%s %s" % (gate.get("data_day"), gate.get("market_open"),
                                    gate.get("note") or ""))
    if gate.get("market_open"):
        ctx.log("取数：宽基 %s/5 只 · 利差 %d 列（缺 %d）· 跨市场 %d 列/%d 市场"
                % (gate.get("idx_ok"), gate.get("spread_cols"),
                   len(gate.get("spread_missing") or []),
                   gate.get("xm_cols"), len(gate.get("xm_markets") or [])))
        if (gate.get("idx_ok") or 0) < 4:
            ctx.warn("宽基日线不足", ["只拿到 %s/5 只，缠论/AC 会缺指数"
                                    % gate.get("idx_ok")], "error")
        if not gate.get("xm_cols"):
            ctx.warn("跨市场因子为空",
                     ["cross_market_factors.csv 没造出来 → today_dual 会退回旧面板"],
                     "error")
    else:
        ctx.log("今日休市 → render 只写「休市跳过」说明")
    _state(ctx, "fetch", gate)
    return 0


# ── render ────────────────────────────────────────────────────────────────
def render(ctx):
    """today_dual → out/report.md + data/today_dual.json"""
    st = _state(ctx, "fetch") or {}
    os.makedirs(ctx.out_dir, exist_ok=True)
    rp = os.path.join(ctx.out_dir, "report.md")

    if st.get("market_open") is False:
        txt = ("# 宽基 T+1 双轨预测 · %s\n\n"
               "**今日休市，不生成信号。** %s\n\n"
               "> 与原 crontab 一致：非交易日直接跳过，避免连着发同一份旧信号。\n"
               % (st.get("data_day") or "", st.get("note") or ""))
        with open(rp, "w", encoding="utf-8") as f:
            f.write(txt)
        ctx.log("休市跳过报告 → %s" % os.path.relpath(rp, ctx.dir))
        _state(ctx, "render", {"market_open": False, "rows": [], "as_of": st.get("data_day")})
        return 0

    jp = os.path.join(ctx.data_dir, "today_dual.json")
    rc = _py(ctx, (
        "import sys, os, json, subprocess\n"
        "sys.path.insert(0, %r)\n"
        "td = os.path.join(%r, 'chan_zs_t1', 'today_dual.py')\n"
        "r = subprocess.run([sys.executable, td, '--json'], capture_output=True, text=True)\n"
        "if r.stderr.strip():\n"
        "    print(r.stderr.strip()[-3000:])\n"
        "if r.returncode != 0 or not r.stdout.strip():\n"
        "    raise SystemExit('today_dual 失败 rc=%%s' %% r.returncode)\n"
        "open(%r, 'w', encoding='utf-8').write(r.stdout)\n"
        "print('today_dual ok %%d bytes' %% len(r.stdout))\n"
    ) % (SHARED, SHARED, jp), timeout=2400, tag="today_dual")
    if rc != 0:
        raise RuntimeError("today_dual 阶段失败 rc=%s" % rc)

    with open(jp, encoding="utf-8") as f:
        rep = json.load(f)

    # 自检：pred_std 全空 = 预测没算出来（退回旧面板），不能当真信号
    missing_psd = [nm for nm, r in rep.items() if r.get("pred_std") is None]
    if len(missing_psd) == len(rep) and rep:
        ctx.warn("预测分全部为空",
                 ["%d/%d 个指数的 pred_std 为空，today_dual 疑似退回旧面板"
                  % (len(missing_psd), len(rep)), "本次不写指标"], "error")

    dates = [r.get("date") for r in rep.values() if r.get("date")]
    as_of = sorted(dates)[-1] if dates else ""

    # dual_ledger --record 读的是 <WS>/.cache/chan_zs_t1/today_dual_<date>.json，
    # 原 make_today_report.py 就写在那儿；这里补写一份，保证台账能记账。
    if as_of:
        try:
            sd = os.path.join(P.cache_root(), ".cache", "chan_zs_t1")
            os.makedirs(sd, exist_ok=True)
            pct = {nm: [r.get("pct"), r.get("close")] for nm, r in rep.items()}
            with open(os.path.join(sd, "today_dual_%s.json" % as_of), "w",
                      encoding="utf-8") as f:
                json.dump({"date": as_of, "pct": pct, "rep": rep}, f,
                          ensure_ascii=False, default=float)
        except OSError as e:
            ctx.log("写台账用 json 失败：%s" % e)
    rows = []
    md = ["# 宽基 T+1 双轨预测 · %s" % as_of, "",
          "**数据截止** %s 收盘 ｜ **预测对象** 下一交易日 ｜ 缠论中枢 v3 口径" % as_of, "",
          "| 指数 | 收盘 | 中枢状态 | AC1_40 | 波动档 | ①原算法 | AC闸门 | 该状态历史命中 |",
          "|---|---|---|---|---|---|---|---|"]
    for nm, r in rep.items():
        pred = r.get("pred")
        gate = "✅ 放行" if r.get("gate") == "PASS" else ("⛔ 观望" if r.get("gate") else "—")
        cal = r.get("cal_state") or {}
        rows.append({"name": nm, "code": r.get("code") or _code_of(nm),
                     "date": r.get("date"), "close": r.get("close"),
                     "chan": r.get("chan"), "ac": r.get("ac"),
                     "vol": r.get("vol_predstd"), "pred": pred,
                     "pred_std": r.get("pred_std"), "gate": r.get("gate"),
                     "zd": r.get("zd"), "zg": r.get("zg"),
                     "cal_hit": cal.get("hit"), "cal_n": cal.get("n")})
        md.append("| %s | %.2f | %s | %s | %s | %s %s | %s | %s |" % (
            nm, r.get("close") or 0, ST.get(r.get("chan"), r.get("chan")),
            ("%+.4f" % r["ac"]) if r.get("ac") is not None else "—",
            r.get("vol_predstd") or "—",
            ("看多" if (pred or 0) > 0 else "看空"),
            ("%+.3f" % pred) if pred is not None else "NA", gate,
            ("%.1f%%(n=%d)" % (cal["hit"] * 100, cal.get("n") or 0)) if cal.get("hit") else "—"))

    md += ["", "## 口径", "", "| 项 | 说明 |", "|---|---|",
           "| 中枢 | v3：三笔重叠锁定 ZD/ZG，一笔完全离开才结束；空窗期沿用上一中枢边缘 |",
           "| AC1_40 | 近 40 交易日日收益率 lag-1 自相关；中枢外 AC ≥ +0.10 才放行 |",
           "| 波动档 | pred_std（模型 5 种子标准差）扩张分位 1/3、2/3 切点 |",
           "| 预测分 | walk-forward，750 日训练窗，5 种子平均 |",
           "| 数据源 | DataHub EDB（指数日线 / 利率 / 跨市场），任务侧不直连 |", ""]
    if st.get("spread_missing"):
        md += ["> 利差因子缺列（DataHub 未登记，模型侧按缺失填 0）：%s"
               % "、".join(st["spread_missing"]), ""]

    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    ctx.log("报告 → %s（%d 个指数，%d 字）"
            % (os.path.relpath(rp, ctx.dir), len(rows), len("\n".join(md))))
    _state(ctx, "render", {"market_open": True, "as_of": as_of, "rows": rows,
                           "pred_std_missing": missing_psd})
    return 0


# ── emit ──────────────────────────────────────────────────────────────────
def emit(ctx):
    """5 宽基 × 3 指标。"""
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 拿不到 render 结果")
    if st.get("market_open") is False:
        ctx.log("休市，无指标")
        return []
    as_of = st.get("as_of") or ""
    points = []
    for r in st.get("rows") or []:
        code, nm = r.get("code") or "", r.get("name") or ""
        if not code:
            continue
        pred, gate, chan = r.get("pred"), r.get("gate"), r.get("chan")
        # 方向：AC 放行 → 按预测分；中枢内 → 低波口径同样按预测分；
        # 其余（中枢外但闸门未放行）→ 观望 0
        if pred is None:
            sig, sig_q = None, "missing"
        elif gate == "PASS" or chan == "inside":
            sig, sig_q = (1 if pred > 0 else -1), "ok"
        else:
            sig, sig_q = 0, "ok"
        points.append({"key": "wd.t1.signal", "sec": code, "obs_date": as_of,
                       "name": "%s T+1 方向" % nm, "value": sig, "dec": 0,
                       "quality": sig_q,
                       "meta_json": {"pred": pred, "chan": chan, "gate": gate,
                                     "vol": r.get("vol")}})
        gatev = None if gate is None else (1 if gate == "PASS" else 0)
        points.append({"key": "wd.t1.ac_gate", "sec": code, "obs_date": as_of,
                       "name": "%s AC 闸门" % nm, "value": gatev, "dec": 0,
                       "quality": "ok" if gatev is not None else "missing",
                       "meta_json": {"ac1_40": r.get("ac"), "threshold": 0.10}})
        hit = r.get("cal_hit")
        points.append({"key": "wd.t1.conviction", "sec": code, "obs_date": as_of,
                       "name": "%s T+1 信心度" % nm,
                       "value": (round(hit * 100, 1) if hit is not None else None),
                       "dec": 1, "unit": "%",
                       "quality": "ok" if hit is not None else "missing",
                       "meta_json": {"basis": "cal_state.hit（该中枢状态历史命中率）",
                                     "n": r.get("cal_n")}})
    n_ok = sum(1 for p in points if p.get("quality") == "ok")
    ctx.log("指标 %d 条（ok %d / missing %d）" % (len(points), n_ok, len(points) - n_ok))
    return points


# ── dispatch ──────────────────────────────────────────────────────────────
def dispatch(ctx):
    """台账记账 + 推送（默认关）。"""
    st = _state(ctx, "render") or {}
    if st.get("market_open") is False:
        ctx.log("休市，dispatch 跳过")
        return 0

    try:
        rc = _py(ctx, (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import dual_ledger as DL\n"
            "bad = 0\n"
            "for m in ('cmd_settle', 'cmd_record'):\n"
            "    fn = getattr(DL, m, None)\n"
            "    if fn is None:\n"
            "        print('dual_ledger 缺 %%s' %% m); bad += 1; continue\n"
            "    try:\n"
            "        fn()\n"
            "    except SystemExit:\n"
            "        pass\n"
            "    except Exception as e:\n"
            "        print('ledger %%s 失败 %%r' %% (m, e)); bad += 1\n"
            "sys.exit(1 if bad else 0)\n"
        ) % SHARED, timeout=900, tag="ledger")
        ctx.log("台账 settle+record rc=%s" % rc)
        if rc != 0:
            ctx.warn("台账记账失败", ["dual_ledger settle/record 返回非 0"], "warn")
    except Exception as e:                                   # noqa: BLE001
        ctx.warn("台账记账异常", [str(e)[:300]], "warn")

    if os.environ.get("WT1_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过推送：未设 WT1_PUSH=1（迁移期默认不发）")
        return 0

    rp = os.path.join(ctx.out_dir, "report.md")
    body = open(rp, encoding="utf-8").read()
    import alert
    rec = alert.send(ctx.key, "宽基 T+1 双轨预测 · %s" % (st.get("as_of") or ""),
                     body.splitlines()[:40], level="info")
    ctx.log("推送记录 %s（webhook=%s）" % (rec.get("ts"), rec.get("webhook") or "未配置"))
    return 0
