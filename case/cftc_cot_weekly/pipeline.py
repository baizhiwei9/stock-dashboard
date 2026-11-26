#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cftc_cot_weekly —— CFTC COT 极值持仓周报 · 框架适配层

原实现
======
宿主机 crontab ``0 9 * * 6 --qq``（周六 09:00）跑 ``bin/cftc_cot_weekly.py``：

    1. ``_refresh_cftc_zips()``          重下 CFTC 年度 zip（直连 cftc.gov）
    2. ``cftc_qr_robust.load_panel()``   解 zip + 对齐 akshare 价格 → 周频面板
    3. COT104 = 管理基金净持仓 104 周分位；≥0.90 做多 / ≤0.10 做空
    4. ``make_chart()``                  4 品种 × COT 走势 + 分位带（matplotlib）
    5. 推飞书卡片 + 可选 QQ

迁移后
======
    fetch     DataHub ``get_cftc_cot()``（采集器已含年度包下载/缓存/解包/价格对齐）
              → data/cot_panel.csv，**不再直连 cftc.gov**
    render    COT104 分位 + episode 判定 + out/report.md + out/cot.png
    emit      4 品种 × 3 指标（cot.index / cot.weeks_left / cot.direction）
    dispatch  推送（默认关，COTC_PUSH=1 才发）

口径保持不变（这是被回测验证过的部分，不动）
=============================================
    COT104   = mm_net 在**过去 104 周（不含未来）**的经验分位，0~1
    ≥ 0.90   → 做多；≤ 0.10 → 做空；其余 → 观望
    持有窗口 = 4~8 周（历史极值停留：中位 4 周、P75 6 周）
    剩余周数 = max(0, 8 − 已持续周数)

品种
====
任务描述写「crude/gold/copper/…」，原脚本 ``NAMES`` 里实际是
**copper / gold / silver / corn**（WTI 在 DataHub 有，但原报告不含它）。
本 case 按原脚本的四个品种出，另把 crude 一并取回来放在 data/ 里备用
（README 说明），但**不进指标、不进报告**，避免凭空扩大面板。

sec 用 DataHub tech catalog 里登记过的期货代码（AU.SHF 等）；
COT 是境外品种（COMEX/CBOT），国内对应主力合约见 ``SEC_MAP``。
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

SHARED = os.path.join(os.path.dirname(HERE), "_shared")
# 复用宽基那套 wsenv（DataHub 入口 + 路径口径），不另立规则
WSHARED = os.path.join(SHARED, "wide_ens_lib")

KEY = "cftc_cot_weekly"
MARKETS = ["copper", "gold", "silver", "corn"]     # 原报告的四个（顺序即报告顺序）
EXTRA = ["crude"]                                  # 取回来备用，不进报告/指标

# COT 品种 → 国内期货 sec（tech catalog 里已登记的代码）
# ⚠️ 玉米(C)/原油(SC) 在 catalog 里**没有登记**（只有 BC.INE/I.DCE/J.DCE/JM.DCE/
#    SF.ZCE/SM.ZCE），写不存在的 sec 会变成无归属指标 → 这两个品种 sec 留空
#    （market 级指标），README「指标 sec 归属」一节有说明。
SEC_MAP = {"copper": "CU.SHF", "gold": "AU.SHF", "silver": "AG.SHF",
           "corn": "", "crude": ""}
NAME_CN = {"copper": "COMEX铜", "gold": "COMEX黄金", "silver": "COMEX白银",
           "corn": "CBOT玉米", "crude": "WTI原油"}

COT_LB = 104
HI, LO = 0.90, 0.10
WIN_MIN, WIN_MAX = 4, 8
HIST_AVG_BP = {"copper": 429, "gold": 158, "silver": 450, "corn": 570}


def _q(s):
    return "'%s'" % str(s).replace("'", "'\"'\"'")


def _py(ctx, code: str, timeout: int, tag: str) -> int:
    env = {"PYTHONPATH": os.pathsep.join([WSHARED, os.path.join(WSHARED, "bin")]),
           "CASE_DATA_DIR": ctx.data_dir, "CASE_OUT_DIR": ctx.out_dir,
           "TZ": os.environ.get("TZ", "Asia/Shanghai")}
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
    """DataHub get_cftc_cot → data/cot_panel.csv"""
    rc = _py(ctx, (
        "import sys, os, json\n"
        "sys.path.insert(0, %r)\n"
        "from datahub_fetch import hub\n"
        "from data_hub.collectors.futures import cftc_cot as cc\n"
        "markets = %r\n"
        "df = cc.get_cftc_cot(markets=markets, start_year=2019)\n"
        "if df is None or len(df) == 0:\n"
        "    raise SystemExit('CFTC COT 返回 0 行')\n"
        "p = os.path.join(os.environ['CASE_DATA_DIR'], 'cot_panel.csv')\n"
        "df.to_csv(p, index=False)\n"
        "g = df.groupby('market').agg(n=('week', 'size'),\n"
        "                             first=('week', 'min'), last=('week', 'max'))\n"
        "out = {k: {'n': int(v['n']), 'first': str(v['first'])[:10],\n"
        "           'last': str(v['last'])[:10]}\n"
        "       for k, v in g.to_dict('index').items()}\n"
        "json.dump({'markets': out, 'rows': len(df)},\n"
        "          open(os.path.join(os.environ['CASE_DATA_DIR'], 'stage_fetch.json'),\n"
        "               'w'), ensure_ascii=False)\n"
        "print('COT 面板 %%d 行：%%s' %% (len(df),\n"
        "      ', '.join('%%s=%%d' %% (k, v['n']) for k, v in out.items())))\n"
    ) % (WSHARED, MARKETS + EXTRA), timeout=1800, tag="fetch")
    st = _state(ctx, "fetch") or {}
    if not st:
        raise RuntimeError("fetch 没产出 stage_fetch.json（rc=%s）" % rc)
    got = st.get("markets") or {}
    lack = [m for m in MARKETS if m not in got]
    if lack:
        ctx.warn("COT 品种缺数", ["DataHub 返回里缺：%s" % ",".join(lack)], "error")
    else:
        ctx.log("COT 面板 %s 行 · 4 品种齐全（最新周到 %s）"
                % (st.get("rows"),
                   max((v.get("last") or "") for v in got.values())))
    return 0


# ── render ────────────────────────────────────────────────────────────────
_RENDER = '''\
import sys, os, json
import numpy as np
import pandas as pd
sys.path.insert(0, @@WSHARED@@)
from datahub_fetch import hub
from data_hub.collectors.futures import cftc_cot as cc

DD = os.environ['CASE_DATA_DIR']
OD = os.environ['CASE_OUT_DIR']
MARKETS = @@MARKETS@@
COT_LB, HI, LO = @@LB@@, @@HI@@, @@LO@@
WMIN, WMAX = @@WMIN@@, @@WMAX@@
HIST_AVG_BP = @@AVG@@
NAME_CN = @@NAMES@@

df = pd.read_csv(os.path.join(DD, 'cot_panel.csv'))
df['week'] = pd.to_datetime(df['week'], errors='coerce')

def episode(s):
    """当前极值 episode：方向、已持续周数、起始周"""
    c = s['cot'].dropna()
    d = s['dir'].reindex(c.index).fillna(0).astype(int)
    last = int(d.iloc[-1])
    i = len(d) - 1
    while i > 0 and int(d.iloc[i - 1]) == last:
        i -= 1
    return {'dir': last, 'held': len(d) - i, 'start': str(c.index[i].date())}

rows = []
for m in MARKETS:
    sub = df[df['market'] == m].sort_values('week').set_index('week')
    if not len(sub):
        continue
    p = sub.copy()
    s = p['mm_net'].astype(float)
    mn = s.rolling(COT_LB, min_periods=30).min()
    mx = s.rolling(COT_LB, min_periods=30).max()
    p['cot'] = (s - mn) / (mx - mn).replace(0, np.nan)
    p['dir'] = np.where(p['cot'] >= HI, 1, np.where(p['cot'] <= LO, -1, 0))
    info = episode(p)
    cot = float(p['cot'].dropna().iloc[-1])
    close = float(p['close'].dropna().iloc[-1]) if 'close' in p else None
    # 已跑多少 bp：episode 起点收盘 → 现在
    seg = p.loc[p['cot'].dropna().index[0]:] if False else p
    start = pd.Timestamp(info['start'])
    seg = p.loc[start:]
    pnl = None
    try:
        c0 = float(seg['close'].dropna().iloc[0]); c1 = float(seg['close'].dropna().iloc[-1])
        pnl = (c1 / c0 - 1) * 1e4 * info['dir']
    except Exception:
        pnl = None
    left = max(0, WMAX - info['held']) if info['dir'] != 0 else None
    rows.append({'market': m, 'name': NAME_CN.get(m, m), 'week': str(p.index[-1].date()),
                 'cot': round(cot, 4), 'dir': info['dir'], 'held': info['held'],
                 'start': info['start'], 'weeks_left': left,
                 'close': close, 'pnl_bp': (round(pnl, 0) if pnl is not None else None),
                 'avg_bp': HIST_AVG_BP.get(m), 'n': int(len(p))})

VERDICT = {1: ('做多', '#c0392b'), -1: ('做空', '#1e8449'), 0: ('观望', '#7f8c8d')}
md = ['# CFTC COT 极值持仓周报 · %s' % (rows[0]['week'] if rows else ''), '',
      '**COT104** = 管理基金净持仓在过去 104 周的经验分位（不含未来）。'
      '≥0.90 做多 / ≤0.10 做空 / 其余观望；目标持有 4~8 周。', '',
      '| 品种 | COT104 | 动作 | 已持续 | 起始周 | 剩余窗口 | 期间累计 | 历史均值 |',
      '|---|---|---|---|---|---|---|---|']
for r in rows:
    act = VERDICT[r['dir']][0]
    held_txt = ('%d 周' % r['held']) if r['dir'] != 0 else '—'
    start_txt = r['start'] if r['dir'] != 0 else '—'
    md.append('| %s | %.3f | **%s** | %s | %s | %s | %s | %s |' % (
        r['name'], r['cot'], act, held_txt, start_txt,
        ('还剩 %d 周' % r['weeks_left']) if r['weeks_left'] is not None else '—',
        (('%+.0fbp' % r['pnl_bp']) if (r['dir'] != 0 and r['pnl_bp'] is not None)
         else '—'),
        ('%dbp' % r['avg_bp']) if r['avg_bp'] else '—'))
md += ['', '## 口径', '', '| 项 | 说明 |', '|---|---|',
       '| 样本 | 2019 年起周频（COT 报告日对齐到周五收盘） |',
       '| 分位 | 104 周滚动经验分位，`min_periods=30`，无未来函数 |',
       '| 持有窗口 | 4~8 周（历史极值停留中位 4 周、P75 6 周） |',
       '| 数据源 | DataHub `get_cftc_cot()`（采集器自带年度包与价格对齐） |', '']
open(os.path.join(OD, 'report.md'), 'w', encoding='utf-8').write('\\n'.join(md))

# 图：4 品种 COT 走势（近 120 周）+ 极值带
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for cand in ('Noto Sans CJK SC', 'Noto Sans CJK JP', 'WenQuanYi Zen Hei',
                 'DejaVu Sans'):
        if any(cand in f.name for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [cand]
            break
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    for ax, m in zip(axes.ravel(), MARKETS):
        sub = df[df['market'] == m].sort_values('week').set_index('week')
        s = sub['mm_net'].astype(float)
        mn = s.rolling(COT_LB, min_periods=30).min()
        mx = s.rolling(COT_LB, min_periods=30).max()
        cot = ((s - mn) / (mx - mn).replace(0, np.nan)).dropna().iloc[-120:]
        x = np.arange(len(cot))
        ax.axhspan(HI, 1.02, color='#c0392b', alpha=0.10)
        ax.axhspan(-0.02, LO, color='#1e8449', alpha=0.10)
        ax.plot(x, cot.values, color='#2c3e50', lw=1.8)
        ax.scatter([x[-1]], [cot.iloc[-1]], s=90, color='#c0392b', zorder=5)
        ax.set_ylim(-0.05, 1.05)
        ax.set_title(NAME_CN.get(m, m) + ' COT104', fontsize=11)
        ax.tick_params(labelsize=7)
    fig.suptitle('CFTC COT 极值持仓（近 120 周）', fontsize=13)
    fig.tight_layout()
    fig.savefig(os.path.join(OD, 'cot.png'), dpi=90)
    plt.close(fig)
    png_ok = True
except Exception as e:
    print('出图失败：%r' % (e,))
    png_ok = False

json.dump({'as_of': (rows[0]['week'] if rows else ''), 'rows': rows,
           'png': png_ok},
          open(os.path.join(DD, 'stage_render.json'), 'w'), ensure_ascii=False)
print('COT 周报：%d 个品种，图 %s' % (len(rows), '已出' if png_ok else '失败'))
for r in rows:
    print('  %-10s COT%.3f %s%s' % (
        r['name'], r['cot'], VERDICT[r['dir']][0],
        (' 已%d周 剩%s' % (r['held'], r['weeks_left'])) if r['dir'] else ''))
'''

def render(ctx):
    os.makedirs(ctx.out_dir, exist_ok=True)
    # 用 token 替换而不是 % 格式化：模板里本来就有大量 %s/%.3f，用 % 会打架
    code = _RENDER
    for tok, val in (("@@WSHARED@@", repr(WSHARED)),
                     ("@@MARKETS@@", repr(MARKETS)),
                     ("@@LB@@", str(COT_LB)),
                     ("@@HI@@", repr(HI)),
                     ("@@LO@@", repr(LO)),
                     ("@@WMIN@@", str(WIN_MIN)),
                     ("@@WMAX@@", str(WIN_MAX)),
                     ("@@AVG@@", repr(HIST_AVG_BP)),
                     ("@@NAMES@@", repr(NAME_CN))):
        code = code.replace(tok, val)
    rc = _py(ctx, code, timeout=1800, tag="render")
    if rc != 0:
        raise RuntimeError("render 失败 rc=%s" % rc)
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("render 没有产出 stage_render.json")
    ctx.log("报告 → out/report.md（%d 品种，图 %s）"
            % (len(st.get("rows") or []), "已出" if st.get("png") else "失败"))
    return 0


# ── emit ──────────────────────────────────────────────────────────────────
def emit(ctx):
    st = _state(ctx, "render")
    if not st:
        raise RuntimeError("emit 拿不到 render 结果")
    as_of = st.get("as_of") or ""
    points = []
    for r in st.get("rows") or []:
        sec = SEC_MAP.get(r["market"]) or ""
        if not sec:
            # 该品种在 DataHub tech catalog 里没有对应合约（玉米/原油）→ 不写指标，
            # 只在报告里展示。硬写一个未登记的 sec 会变成无归属证券、面板上看不到。
            ctx.log("%s 无对应 sec（catalog 未登记），只进报告不进指标" % (r.get("name") or r["market"]))
            continue
        nm = r.get("name") or r["market"]
        points.append({"key": "cot.index", "sec": sec, "obs_date": as_of,
                       "name": "%s COT104 分位" % nm, "value": r.get("cot"),
                       "dec": 3, "quality": "ok" if r.get("cot") is not None else "missing",
                       "meta_json": {"window_weeks": COT_LB, "hi": HI, "lo": LO,
                                     "n_weeks": r.get("n"), "market": r["market"]}})
        points.append({"key": "cot.weeks_left", "sec": sec, "obs_date": as_of,
                       "name": "%s 剩余持有周数" % nm, "value": r.get("weeks_left"),
                       "dec": 0, "unit": "周",
                       "quality": "ok" if r.get("weeks_left") is not None else "missing",
                       "meta_json": {"held": r.get("held"), "start": r.get("start"),
                                     "window": [WIN_MIN, WIN_MAX],
                                     "pnl_bp": r.get("pnl_bp"),
                                     "avg_bp": r.get("avg_bp")}})
        # 方向是枚举值，纯数字（-1/0/1）在面板上读不出来 → 同时写 value_text，
        # task.json 里该指标标了 "text": true，看板优先显示文字。
        _dir = r.get("dir")
        _dir_txt = {1: "做多", -1: "做空", 0: "观望"}.get(_dir, "—")
        points.append({"key": "cot.direction", "sec": sec, "obs_date": as_of,
                       "name": "%s COT 方向" % nm, "value": _dir, "dec": 0,
                       "value_text": _dir_txt,
                       "quality": "ok",
                       "meta_json": {"scale": "1 做多 / -1 做空 / 0 观望",
                                     "cot": r.get("cot")}})
    ctx.log("指标 %d 条（4 品种 × 3）" % len(points))
    return points


# ── dispatch ──────────────────────────────────────────────────────────────
def dispatch(ctx):
    if os.environ.get("COTC_PUSH", "").strip() not in ("1", "true", "yes"):
        ctx.log("dispatch 跳过推送：未设 COTC_PUSH=1（迁移期默认不发）")
        return 0
    st = _state(ctx, "render") or {}
    rp = os.path.join(ctx.out_dir, "report.md")
    lines = open(rp, encoding="utf-8").read().splitlines()[:40]
    import alert
    rec = alert.send(ctx.key, "CFTC COT 周报 · %s" % (st.get("as_of") or ""),
                     lines, level="info")
    ctx.log("推送记录 %s（webhook=%s）" % (rec.get("ts"), rec.get("webhook") or "未配置"))
    return 0
