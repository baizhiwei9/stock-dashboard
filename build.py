#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stock Dashboard 构建器

从 DataHub 的 tech 指标仓读取全部指标 → 生成按指数分栏的静态看板。

设计约束
════════════════════════════════════════════════════════════════
· 只读不写：不触发任何计算，不 import 任何任务的算法脚本
· 取不到值 → 显示 NA，绝不用 0 或上期值顶替
· 按指数分栏：每栏从上到下 = 缠论 → RMT → DFA → 趋势/偏多
· 流动性/风控/期权单独一栏（tech 库暂未覆盖的域显示 NA）
· 纯静态单文件 HTML，零外部 CDN，内联 SVG 图表

用法
════════════════════════════════════════════════════════════════
    python3 build.py
    python3 build.py --out index.html --open-date 2026-11-23
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

DATAHUB_ROOT = os.environ.get("DATA_HUB_PATH") or "/opt/coze/.fuse_drive/dataHub_三级体系"
if DATAHUB_ROOT not in sys.path:
    sys.path.insert(0, DATAHUB_ROOT)

from data_hub.tech import (                                   # noqa: E402
    FUTURES, fut_groups, fut_snapshot, opt_asof, opt_snapshot,
    INDEX_ORDER, SECURITIES, pair_of, read_latest, read_series, security_name, stats,
)

HERE = os.path.dirname(os.path.abspath(__file__))

# 每栏要展示的指标分组
CHANLUN_KEYS = ["chanlun.bi_dir", "chanlun.bi_count", "chanlun.zs_count",
                "chanlun.zs_zg", "chanlun.zs_zd", "chanlun.signal",
                "chanlun.signal_date", "chanlun.macd_bar", "chanlun.bull_score"]
RMT_KEYS = ["rmt.ar5", "rmt.l1_l2", "rmt.l1_sum", "rmt.outlier_energy", "rmt.rc"]
DFA_KEYS = ["dfa.hurst_short", "dfa.hurst_long", "dfa.hurst_diff",
            "dfa.hurst_vol", "dfa.signal"]
TREND_KEYS = ["trend.ma20_slope", "trend.ma60_dist", "trend.pct_20d", "trend.vol_20d"]

# 宏观 / 流动性 / 两融（全市场口径，security 用虚拟标的）
MACRO_CN_KEYS = ["macro.m2_yoy", "macro.m1m2_scissor", "macro.gdp_yoy",
                 "macro.cpi_yoy", "macro.ppi_yoy", "macro.tsf_inc", "macro.tsf_stock"]
MACRO_GL_KEYS = ["macro.dxy", "macro.cnh", "macro.usdcny"]
LIQ_KEYS = ["liquidity.dr001", "liquidity.dr007", "liquidity.shibor_3m",
            "liquidity.omo_net", "liquidity.gc001", "liquidity.cn10y",
            "liquidity.cn2y", "liquidity.spread_10y2y", "liquidity.spread_cn_us"]
MARGIN_KEYS = ["margin.rzye", "margin.rqye", "margin.rzrqye", "margin.maint_ratio"]

MACRO_SEC = "CN.MACRO"
GLOBAL_SEC = "GLOBAL.MACRO"
LIQ_SEC = "CN.LIQ"
MARGIN_SEC = "CN.MARGIN"

SIGNAL_CN = {"B1": "一买", "B2": "二买", "B3": "三买",
             "S1": "一卖", "S2": "二卖", "S3": "三卖", "none": "无"}
DFA_CN = {"golden": "金叉", "dead": "死叉", "ordered": "有序",
          "disordered": "退相", "critical": "临界"}
BI_CN = {"up": "向上笔", "down": "向下笔", "unknown": "未知"}


# ════════════════════════════════════════════════════════════════
# 数据装载
# ════════════════════════════════════════════════════════════════
def load_all(asof: Optional[str] = None) -> Tuple[Dict, Dict, Optional[str]]:
    """返回 (latest, series, obs_date)。

    latest  = {(metric_key, security_id): row}
    series  = {(metric_key, security_id): [rows]}
    """
    all_keys = CHANLUN_KEYS + RMT_KEYS + DFA_KEYS + TREND_KEYS
    latest = read_latest(all_keys, INDEX_ORDER, asof=asof)

    obs = None
    dates = [r["obs_date"] for r in latest.values() if r.get("obs_date")]
    if dates:
        obs = max(dates)

    # 宏观 / 流动性 / 两融：全市场口径
    ext_latest = read_latest(MACRO_CN_KEYS, [MACRO_SEC], asof=asof)
    ext_latest.update(read_latest(MACRO_GL_KEYS, [GLOBAL_SEC], asof=asof))
    ext_latest.update(read_latest(LIQ_KEYS, [LIQ_SEC], asof=asof))
    ext_latest.update(read_latest(MARGIN_KEYS, [MARGIN_SEC], asof=asof))
    latest.update(ext_latest)

    series: Dict[Tuple[str, str], List[Dict]] = {}
    for sid in INDEX_ORDER:
        for k in ("chanlun.bull_score", "rmt.ar5", "rmt.outlier_energy",
                  "dfa.hurst_short", "dfa.hurst_long", "trend.pct_20d"):
            series[(k, sid)] = read_series(k, sid)

    # 宏观/流动性时序
    for k in ("macro.m2_yoy", "macro.m1m2_scissor", "macro.tsf_inc"):
        series[(k, MACRO_SEC)] = read_series(k, MACRO_SEC)
    for k in ("macro.dxy", "macro.cnh"):
        series[(k, GLOBAL_SEC)] = read_series(k, GLOBAL_SEC)
    for k in ("liquidity.dr001", "liquidity.dr007", "liquidity.spread_cn_us"):
        series[(k, LIQ_SEC)] = read_series(k, LIQ_SEC)
    series[("margin.rzrqye", MARGIN_SEC)] = read_series("margin.rzrqye", MARGIN_SEC)
    return latest, series, obs


def g(latest: Dict, key: str, sid: str) -> Optional[Dict]:
    return latest.get((key, sid))


def v(latest: Dict, key: str, sid: str):
    """取数值；缺失返回 None。"""
    r = g(latest, key, sid)
    if not r or r.get("quality") != "ok":
        return None
    return r.get("value_num")


def t(latest: Dict, key: str, sid: str) -> Optional[str]:
    r = g(latest, key, sid)
    if not r or r.get("quality") != "ok":
        return None
    return r.get("value_text")


# ════════════════════════════════════════════════════════════════
# 渲染小工具
# ════════════════════════════════════════════════════════════════
NA = '<span class="na">NA</span>'


def num(vv, dec=2, unit="", cls=""):
    if vv is None:
        return NA
    s = ("%." + str(dec) + "f") % vv
    c = (' class="%s"' % cls) if cls else ""
    return '<span%s>%s%s</span>' % (c, s, unit)


def signed(vv, dec=2, unit=""):
    if vv is None:
        return NA
    cls = "up" if vv > 0 else ("down" if vv < 0 else "flat")
    s = ("%+." + str(dec) + "f") % vv
    return '<span class="%s">%s%s</span>' % (cls, s, unit)


_OPT_SNAP: Optional[Dict] = None


def _opt_snap() -> Dict:
    """期权快照整个 build 只加载一次；失败就当空（展示 NA）。"""
    global _OPT_SNAP
    if _OPT_SNAP is None:
        try:
            _OPT_SNAP = opt_snapshot()
        except Exception:                                    # noqa: BLE001
            _OPT_SNAP = {}
    return _OPT_SNAP


def kvh(label_html, val_html):
    """label 原样当 HTML 用（可带 <span class="sm"> 副标题）。"""
    return '<div class="kv"><span class="k">%s</span><span class="v">%s</span></div>' % (
        label_html, val_html)


def kv(label, val_html):
    return kvh(html.escape(label), val_html)


def score_bar(score: Optional[float]) -> str:
    if score is None:
        return '<div class="bar-wrap">%s</div>' % NA
    p = max(0.0, min(100.0, float(score)))
    if p >= 60:
        col, lab = "#ff4d5e", "偏多"
    elif p <= 40:
        col, lab = "#22c55e", "偏空"
    else:
        col, lab = "#e0a33e", "中性"
    return ('<div class="bar-wrap"><div class="bar"><i style="width:%.1f%%;background:%s"></i>'
            '<u style="left:50%%"></u></div><div class="bar-lab">%s %.0f/100</div></div>'
            % (p, col, lab, p))


def chart_block(cid: str, series_rows: List[Dict], field: str = "value_num") -> str:
    """生成一个图容器 + 数据脚本。点数不足 3 显示提示。"""
    pts = [(r["obs_date"], r.get(field)) for r in (series_rows or [])
           if r.get("quality") == "ok" and r.get(field) is not None]
    if len(pts) < 3:
        n = len(pts)
        return ('<div class="chart-empty">历史数据积累中（已有 %d 个交易日）</div>' % n)
    return '<div class="chart" id="%s"></div>' % cid


def chart_payload(cid: str, series_rows: List[Dict], name: str, color: str,
                  unit: str = "", dec: int = 2, extra: Optional[List[Dict]] = None,
                  axis: Optional[Dict] = None) -> str:
    pts = [(r["obs_date"], r.get("value_num")) for r in (series_rows or [])
           if r.get("quality") == "ok" and r.get("value_num") is not None]
    if len(pts) < 3:
        return ""
    labels = [p[0] for p in pts]
    sers = [{"name": name, "color": color, "values": [p[1] for p in pts]}]
    if extra:
        sers += extra
    cfg = {"labels": labels, "series": sers, "height": 168, "unit": unit,
           "aria": name, "vfmt": None}
    if axis:
        cfg.update(axis)
    return ('<script>drawChart(document.getElementById(%s),%s);</script>'
            % (json.dumps(cid), json.dumps(cfg, ensure_ascii=False)))


# ════════════════════════════════════════════════════════════════
# 单个指数卡片
# ════════════════════════════════════════════════════════════════
def render_index_card(sid: str, latest: Dict, series: Dict, obs: Optional[str]) -> str:
    name = security_name(sid)
    code = sid
    score = v(latest, "chanlun.bull_score", sid)
    bi_dir = t(latest, "chanlun.bi_dir", sid)
    sig = t(latest, "chanlun.signal", sid)
    dfa_sig = t(latest, "dfa.signal", sid)

    # 卡片头部状态徽标
    badges = []
    if score is not None:
        if score >= 60:
            badges.append('<b class="bd up">缠论偏多 %.0f</b>' % score)
        elif score <= 40:
            badges.append('<b class="bd down">缠论偏空 %.0f</b>' % score)
        else:
            badges.append('<b class="bd mid">缠论中性 %.0f</b>' % score)
    else:
        badges.append('<b class="bd na">缠论 NA</b>')
    if dfa_sig:
        m = {"golden": "up", "dead": "down", "critical": "mid"}.get(dfa_sig, "mid")
        badges.append('<b class="bd %s">Hurst %s</b>' % (m, DFA_CN.get(dfa_sig, dfa_sig)))

    # ── 缠论区 ──
    chan = ['<div class="blk">',
            '<h4>缠论结构 <em>日线 · 上一交易日</em></h4>',
            '<div class="kvs">']
    chan.append(kv("当前笔", '<span class="%s">%s</span>' % (
        "up" if bi_dir == "up" else ("down" if bi_dir == "down" else "flat"),
        BI_CN.get(bi_dir, "NA")) if bi_dir else NA))
    chan.append(kv("笔数量", num(v(latest, "chanlun.bi_count", sid), 0, " 笔")))
    chan.append(kv("中枢数量", num(v(latest, "chanlun.zs_count", sid), 0, " 个")))
    zg, zd = v(latest, "chanlun.zs_zg", sid), v(latest, "chanlun.zs_zd", sid)
    if zg is not None and zd is not None:
        chan.append(kv("最近中枢", '<span class="mono">%.1f ~ %.1f</span>' % (zd, zg)))
    else:
        chan.append(kv("最近中枢", NA))
    if sig and sig != "none":
        sdate = t(latest, "chanlun.signal_date", sid) or ""
        chan.append(kv("最近买卖点", '<b class="%s">%s %s</b> <em class="sm">%s</em>' % (
            "up" if sig.startswith("B") else "down",
            SIGNAL_CN.get(sig, sig), sig, sdate)))
    else:
        chan.append(kv("最近买卖点", '<span class="flat">近60日无</span>' if sig == "none" else NA))
    chan.append(kv("MACD柱", num(v(latest, "chanlun.macd_bar", sid), 3,
                                 cls="up" if (v(latest, "chanlun.macd_bar", sid) or 0) > 0 else "down")))
    chan.append('</div>')
    chan.append(score_bar(score))
    chan.append(chart_block("c_%s" % sid.replace(".", "_"), series.get(("chanlun.bull_score", sid))))
    chan.append('</div>')

    # ── RMT 区 ──
    rmt = ['<div class="blk">',
           '<h4>RMT 随机矩阵 <em>60 日滚动窗</em></h4>',
           '<div class="kvs">']
    rmt.append(kv("AR5 吸收比", num(v(latest, "rmt.ar5", sid), 4)))
    rmt.append(kv("λ1/λ2", num(v(latest, "rmt.l1_l2", sid), 2, " 倍")))
    rmt.append(kv("λ1/Σλ", num(v(latest, "rmt.l1_sum", sid), 4)))
    rmt.append(kv("离群能量", num(v(latest, "rmt.outlier_energy", sid), 4)))
    rmt.append(kv("RC 压缩比", num(v(latest, "rmt.rc", sid), 2, " 倍")))
    rmt.append('</div>')
    rmt.append(chart_block("r_%s" % sid.replace(".", "_"), series.get(("rmt.ar5", sid))))
    rmt.append('</div>')

    # ── DFA 区 ──
    dfa = ['<div class="blk">',
           '<h4>DFA / Hurst <em>趋势记忆性</em></h4>',
           '<div class="kvs">']
    dfa.append(kv("短周期 H", num(v(latest, "dfa.hurst_short", sid), 4)))
    dfa.append(kv("长周期 H", num(v(latest, "dfa.hurst_long", sid), 4)))
    dh = v(latest, "dfa.hurst_diff", sid)
    dfa.append(kv("短−长", signed(dh, 4)))
    dfa.append(kv("波动率 H", num(v(latest, "dfa.hurst_vol", sid), 4)))
    if dfa_sig:
        m = {"golden": "up", "dead": "down"}.get(dfa_sig, "mid")
        dfa.append(kv("信号", '<b class="%s">%s</b>' % (m, DFA_CN.get(dfa_sig, dfa_sig))))
    else:
        dfa.append(kv("信号", NA))
    dfa.append('</div>')
    dfa.append(chart_block("d_%s" % sid.replace(".", "_"), series.get(("dfa.hurst_short", sid))))
    dfa.append('</div>')

    # ── 趋势 / 偏多区 ──
    tr = ['<div class="blk">',
          '<h4>趋势 / 偏多 <em>动量与期权定价</em></h4>',
          '<div class="kvs">']
    tr.append(kv("MA20 斜率", signed(v(latest, "trend.ma20_slope", sid), 3, "%")))
    tr.append(kv("距 MA60", signed(v(latest, "trend.ma60_dist", sid), 2, "%")))
    tr.append(kv("20 日涨跌", signed(v(latest, "trend.pct_20d", sid), 2, "%")))
    tr.append(kv("20 日波动", num(v(latest, "trend.vol_20d", sid), 2, "%")))
    _o = _opt_snap().get(sid)
    if _o and _o.get("atm_iv") is not None:
        _pk = _o.get("atm_iv_pk")
        tr.append(kv("期权 平值IV",
                     "%.1f%%" % _o["atm_iv"]
                     + ("" if _pk is None else ' <span class="sm">分位%.0f%%</span>' % _pk)))
    else:
        tr.append(kv("期权 平值IV", NA))
    if _o and _o.get("iv_skew") is not None:
        _sv = _o["iv_skew"]
        _sc = "down" if _sv > 0 else ("up" if _sv < 0 else "flat")
        tr.append(kv("期权 25D偏度", '<span class="%s">%+.1f%%</span>' % (_sc, _sv)))
    else:
        tr.append(kv("期权 25D偏度", NA))
    if _o and _o.get("pcr_oi") is not None:
        tr.append(kv("期权 PCR / VIX", "%.2f / %s" % (
            _o["pcr_oi"],
            "%.1f" % _o["vix"] if _o.get("vix") is not None else NA)))
    else:
        tr.append(kv("期权 PCR / VIX", NA))
    tr.append(kv("两融/资金流", NA))
    tr.append('</div>')
    tr.append(chart_block("t_%s" % sid.replace(".", "_"), series.get(("trend.pct_20d", sid))))
    tr.append('</div>')

    _pr = pair_of(sid)
    _pt = []
    if _pr.get("etf"):
        _pt.append("ETF %s" % _pr["etf"])
    if _pr.get("opt"):
        _pt.append("期权 %s" % "/".join(_pr["opt"]))
    _pair_html = ('<span class="sm pair">%s</span>' % html.escape(" · ".join(_pt))
                  if _pt else "")
    return ('<section class="idx-card" id="i_%s">'
            '<header><div class="ttl"><h3>%s</h3><code>%s</code>%s</div>'
            '<div class="bds">%s</div></header>%s</section>'
            % (sid.replace(".", "_"), html.escape(name), code, _pair_html,
               "".join(badges), "".join(chan + rmt + dfa + tr)))


# ════════════════════════════════════════════════════════════════
# 侧栏：流动性与风控
# ════════════════════════════════════════════════════════════════
def _domain_cov(items) -> List[Tuple[str, str, int, int, Optional[str]]]:
    """(中文名, 域, 已定义数, 已入库数, 最新日期)"""
    from data_hub.tech import registry as _reg
    from data_hub.tech import store as _store
    try:
        conn = _store.connect()
    except Exception:                                         # noqa: BLE001
        return [(nm, d, 0, 0, None) for nm, d in items]
    out = []
    try:
        for nm, d in items:
            keys = [k for k, vv in _reg.REGISTRY.items() if vv.domain == d]
            if not keys:
                out.append((nm, d, 0, 0, None))
                continue
            q = ",".join("?" * len(keys))
            r = conn.execute(
                "SELECT COUNT(DISTINCT metric_key), MAX(obs_date) FROM metric_points "
                "WHERE metric_key IN (%s) AND quality='ok'" % q, keys).fetchone()
            out.append((nm, d, len(keys), r[0] or 0, r[1]))
    finally:
        conn.close()
    return out


def render_side_panel(latest: Dict, st: Optional[Dict] = None,
                      obs: Optional[str] = None) -> str:
    # ── 关键指标快照：有就显示真值，没有就 NA ──
    dr1 = v(latest, "liquidity.dr001", LIQ_SEC)
    dr7 = v(latest, "liquidity.dr007", LIQ_SEC)
    dr_txt = ("%s / %s" % (num(dr1, 3, "%"), num(dr7, 3, "%"))
              if (dr1 is not None or dr7 is not None) else NA)
    snap = [
        ("美元指数 DXY", num(v(latest, "macro.dxy", GLOBAL_SEC), 2)),
        ("离岸人民币 CNH", num(v(latest, "macro.cnh", GLOBAL_SEC), 4)),
        ("DR001 / DR007", dr_txt),
        ("央行 OMO 净投放", num(v(latest, "liquidity.omo_net", LIQ_SEC), 0, " 亿")),
        ("两融余额（两市）", num(v(latest, "margin.rzrqye", MARGIN_SEC), 0, " 亿")),
        ("中美 10Y 利差", num(v(latest, "liquidity.spread_cn_us", LIQ_SEC), 1, " bp")),
        ("北向资金", NA),
        ("VIX / 中国波指", NA),
        ("50ETF 期权 PCR", NA),
        ("股指期货基差", NA),
        ("市场宽度（涨跌家数）", NA),
    ]
    lis = "".join(kv(k, val) for k, val in snap)

    # ── 接入进度：查库统计，不写死 ──
    items = [("缠论结构", "chanlun"), ("RMT 随机矩阵", "rmt"),
             ("DFA / Hurst", "dfa"), ("趋势 / 偏多", "trend"),
             ("宏观", "macro"), ("流动性", "liquidity"),
             ("两融 / 杠杆", "margin"), ("期权 IV / 偏度", "option"),
             ("市场宽度", "breadth"), ("北向资金", "watch")]
    cov = _domain_cov(items)
    prows = []
    n_done = 0
    for nm, d, n_def, n_in, latest_d in cov:
        if n_in > 0:
            n_done += 1
            tag = '<span class="ok">✓ %d/%d</span>' % (n_in, n_def)
        elif n_def > 0:
            tag = '<span class="todo">待跑</span>'
        else:
            tag = '<span class="todo">待接入</span>'
        prows.append('<div class="kv"><span class="k">%s <code>%s.*</code></span>'
                     '<span class="v">%s</span></div>' % (nm, d, tag))
    prog = ('<section class="side-card">'
            '<h3>数据接入进度 <em>%d / %d 组</em></h3>'
            '<div class="kvs">%s</div>'
            '<p class="note">✓ 后是「已入库指标数 / 契约已定义数」。'
            '只显示已按契约写入 tech 指标仓的数据，未接入一律 '
            '<span class="na">NA</span>，不拿别处的旧值顶替。</p>'
            '</section>' % (n_done, len(cov), "".join(prows)))

    ov = ""
    if st:
        ov = ('<section class="side-card">'
              '<h3>指标仓概览</h3>'
              '<div class="kvs">%s</div>'
              '<p class="note">活库 <code>%s</code><br>'
              '云盘快照 <code>output/tech_metrics.db.gz</code></p>'
              '</section>'
              % ("".join([
                  kv("指标点位数", "%s" % st.get("total_points")),
                  kv("指标数", "%s" % st.get("n_metrics")),
                  kv("证券数", "%s" % st.get("n_securities")),
                  kv("日期范围", "%s ~ %s" % (st.get("date_min") or "—",
                                             st.get("date_max") or "—")),
              ]), html.escape(str(st.get("db_path") or ""))))

    return ('<section class="side-card">'
            '<h3>关键指标快照 <em>%s</em></h3>'
            '<div class="kvs">%s</div>'
            '<p class="note">全部来自 tech 指标仓最新一期。'
            '北向资金 / VIX / PCR / 基差 / 市场宽度 尚未按契约接入，'
            '保持 <span class="na">NA</span>。</p>'
            '</section>' % (obs or "—", lis)) + prog + ov


# ════════════════════════════════════════════════════════════════
# 主渲染
# ════════════════════════════════════════════════════════════════
CSS = """
*{box-sizing:border-box}
:root{
  --bg:#0b0e14; --card:#121821; --card2:#0f141c; --line:#1f2836;
  --tx:#e6ecf5; --tx2:#8d99ab; --tx3:#6b7889;
  --up:#ff4d5e; --down:#22c55e; --mid:#e0a33e; --blue:#4a9eff;
}
html,body{margin:0;padding:0;background:var(--bg);color:var(--tx);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--blue);text-decoration:none}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:1560px;margin:0 auto;padding:20px 18px 60px}

/* ── 页头 ── */
header.top{display:flex;flex-wrap:wrap;align-items:flex-end;gap:16px;
  padding:18px 22px;margin-bottom:18px;background:linear-gradient(135deg,#141c28,#0d1219);
  border:1px solid var(--line);border-radius:12px}
header.top h1{margin:0;font-size:22px;letter-spacing:.5px;font-weight:650}
header.top h1 small{display:block;font-size:11.5px;color:var(--tx3);
  font-weight:400;letter-spacing:1.5px;margin-top:3px}
.top-meta{margin-left:auto;display:flex;gap:26px;flex-wrap:wrap}
.top-meta div{text-align:right}
.top-meta b{display:block;font-size:19px;font-family:ui-monospace,monospace}
.top-meta span{font-size:11px;color:var(--tx3);letter-spacing:.5px}

/* ── 导航 ── */
nav.jump{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
nav.jump a{padding:5px 13px;background:var(--card);border:1px solid var(--line);
  border-radius:20px;font-size:12.5px;color:var(--tx2)}
nav.jump a:hover{border-color:var(--blue);color:var(--blue)}

/* ── 主栅格 ── */
.layout{display:grid;grid-template-columns:1fr 336px;gap:16px;align-items:start}
@media(max-width:1240px){.layout{grid-template-columns:1fr}}
#side{position:sticky;top:14px;max-height:calc(100vh - 28px);overflow-y:auto;
  scrollbar-width:thin;scrollbar-color:#26313f transparent}
#side::-webkit-scrollbar{width:5px}
#side::-webkit-scrollbar-thumb{background:#26313f;border-radius:3px}
@media(max-width:1240px){#side{position:static;max-height:none}}
.grid-idx{display:grid;grid-template-columns:1fr;gap:16px}

/* ── 指数卡片 ── */
.idx-card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;scroll-margin-top:16px}
.idx-card>header{display:flex;align-items:center;gap:14px;padding:13px 18px;
  background:linear-gradient(90deg,#161f2c,#121821);border-bottom:1px solid var(--line)}
.idx-card .ttl{display:flex;align-items:baseline;gap:10px}
.idx-card h3{margin:0;font-size:16.5px;font-weight:650}
.idx-card code{font-size:11.5px;color:var(--tx3)}
.idx-card .bds{margin-left:auto;display:flex;gap:7px;flex-wrap:wrap}
.bd{font-size:11.5px;font-weight:500;padding:3px 10px;border-radius:12px;
  border:1px solid var(--line);background:#0e131a;color:var(--tx2)}
.bd.up{border-color:rgba(255,77,94,.4);color:var(--up);background:rgba(255,77,94,.08)}
.bd.down{border-color:rgba(34,197,94,.4);color:var(--down);background:rgba(34,197,94,.08)}
.bd.mid{border-color:rgba(224,163,62,.4);color:var(--mid);background:rgba(224,163,62,.08)}
.bd.na{color:var(--tx3)}

/* 卡片内部四块：宽屏 2×2，窄屏 1 列 */
.idx-card{display:block}
.idx-card>.blk,.idx-card>section{display:inline-block}
.idx-card{position:relative}
.idx-card{--x:0}
.idx-body{display:grid;grid-template-columns:1fr 1fr;gap:0}
@media(max-width:1000px){.idx-body{grid-template-columns:1fr}}
.blk{padding:14px 18px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}
.blk:nth-child(2n){border-right:none}
.blk:nth-last-child(-n+2){border-bottom:none}
@media(max-width:1000px){.blk{border-right:none}}
.blk h4{margin:0 0 11px;font-size:13px;font-weight:600;color:var(--tx);
  display:flex;align-items:baseline;gap:8px}
.blk h4 em{font-style:normal;font-size:10.5px;color:var(--tx3);font-weight:400}

/* 键值对 */
.kvs{display:grid;grid-template-columns:1fr 1fr;gap:5px 14px}
@media(max-width:640px){.kvs{grid-template-columns:1fr}}
.kv{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  padding:3px 0;border-bottom:1px dotted #1a2230}
.kv .k{font-size:12px;color:var(--tx3);white-space:nowrap}
.kv .v{font-size:12.5px;font-family:ui-monospace,monospace;text-align:right}
.kv .v em{font-style:normal}
.sm{font-size:10.5px;color:var(--tx3)}

/* 评分条 */
.bar-wrap{margin:12px 0 4px}
.bar{position:relative;height:7px;background:#1a2230;border-radius:4px;overflow:hidden}
.bar i{position:absolute;left:0;top:0;height:100%;border-radius:4px}
.bar u{position:absolute;top:-2px;width:1px;height:11px;background:#3a4658}
.bar-lab{font-size:11px;color:var(--tx3);margin-top:5px;text-align:right}

/* 图表 */
.chart{position:relative;margin-top:10px}
.chart svg{width:100%;height:auto;display:block}
.chart .tip{position:absolute;pointer-events:none;opacity:0;transition:opacity .12s;
  background:#0a0e14;border:1px solid var(--line);border-radius:7px;padding:7px 9px;
  font-size:11px;white-space:nowrap;z-index:5;box-shadow:0 6px 18px rgba(0,0,0,.5)}
.chart .tip.on{opacity:1}
.chart .tip .d{color:var(--tx3);margin-bottom:4px;font-family:ui-monospace,monospace}
.chart .tip .r{display:flex;justify-content:space-between;gap:14px;
  font-family:ui-monospace,monospace}
.chart-empty{margin-top:10px;padding:16px;text-align:center;font-size:11.5px;
  color:var(--tx3);background:var(--card2);border:1px dashed #1e2734;border-radius:8px}

/* 状态色 */
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--tx2)}
.na{color:#4d5866;font-style:italic}
.ok{color:var(--down)} .todo{color:#7a6a3a}
.mid{color:var(--mid)}

/* ── 管理入口与面板 ── */
.adm-btn{position:absolute;top:14px;right:16px;padding:6px 13px;font-size:12px;
  background:rgba(74,158,255,.1);color:var(--blue);border:1px solid rgba(74,158,255,.35);
  border-radius:8px;cursor:pointer;font-family:inherit;letter-spacing:.3px}
.adm-btn:hover{background:rgba(74,158,255,.2)}
header.top{position:relative}
.adm-mask{position:fixed;inset:0;background:rgba(5,8,12,.78);z-index:9000;
  display:none;align-items:center;justify-content:center;padding:20px;
  backdrop-filter:blur(3px)}
.adm-box{width:100%;max-width:440px;max-height:82vh;overflow-y:auto;
  background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:20px 22px;box-shadow:0 18px 60px rgba(0,0,0,.6)}
.adm-hd{display:flex;align-items:center;margin-bottom:14px}
.adm-hd b{font-size:15px}
.adm-x{margin-left:auto;background:none;border:0;color:var(--tx3);font-size:22px;
  line-height:1;cursor:pointer;padding:0 4px;font-family:inherit}
.adm-x:hover{color:var(--tx)}
.adm-tip{margin:0 0 12px;font-size:12.5px;color:var(--tx2)}
#admPw{width:100%;padding:9px 12px;background:var(--card2);border:1px solid var(--line);
  border-radius:8px;color:var(--tx);font-size:14px;font-family:inherit;
  letter-spacing:3px;outline:none}
#admPw:focus{border-color:var(--blue)}
.adm-err{min-height:18px;font-size:12px;color:var(--up);margin:6px 0 4px}
.adm-primary{width:100%;padding:9px;background:rgba(74,158,255,.16);color:var(--blue);
  border:1px solid rgba(74,158,255,.4);border-radius:8px;font-size:13.5px;
  cursor:pointer;font-family:inherit}
.adm-primary:hover{background:rgba(74,158,255,.26)}
.adm-list{display:flex;flex-direction:column;gap:2px;margin:4px 0 14px}
.adm-row{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:8px;
  cursor:pointer;font-size:13px}
.adm-row:hover{background:var(--card2)}
.adm-row input{width:15px;height:15px;accent-color:#4a9eff;cursor:pointer;flex:none}
.adm-row b{font-weight:600}
.adm-row span{font-size:11.5px;color:var(--tx3);margin-left:auto;text-align:right}
.adm-row.adm-l2{padding-left:34px;font-size:12.5px}
.adm-row.adm-l2 b{font-weight:400;color:var(--tx2)}
.adm-acts{display:flex;gap:8px;flex-wrap:wrap}
.adm-acts button{flex:1;min-width:88px;padding:8px;background:var(--card2);
  color:var(--tx2);border:1px solid var(--line);border-radius:8px;font-size:12.5px;
  cursor:pointer;font-family:inherit}
.adm-acts button:hover{color:var(--tx);border-color:var(--tx3)}
.adm-acts button.adm-primary{flex:1.4}
.adm-note{margin:14px 0 0;font-size:11.5px;color:var(--tx3);line-height:1.65}
.adm-note code{color:var(--mid)}

/* ── 四大分区 ── */
.sec-head{display:flex;align-items:center;gap:14px;margin:30px 0 4px;
  padding:0 2px;border-bottom:1px solid var(--line);padding-bottom:10px}
.sec-head:first-child{margin-top:0}
.sec-num{font-size:22px;font-weight:800;color:var(--line);letter-spacing:-1px;
  font-variant-numeric:tabular-nums;line-height:1}
.sec-head h2{margin:0;font-size:17px;font-weight:700;letter-spacing:.2px}
.sec-head p{margin:3px 0 0;font-size:12px;color:var(--tx3)}
.sec-obs{margin-left:auto;font-size:12px;color:var(--tx3);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.sec-grid{display:grid;grid-template-columns:1fr;gap:16px;margin-top:14px}
nav.jump .sub{font-size:12px;padding:3px 10px;background:transparent;
  color:var(--tx3);border-color:transparent}
nav.jump .sub:hover{background:var(--card);color:var(--blue)}
nav.jump .sub::before{content:"· "}
.jump-sep{width:1px;background:var(--line);margin:4px 2px}

/* ── 模块管理表 ── */
.mod-tab{width:100%;border-collapse:collapse;font-size:12.5px}
.mod-tab th{text-align:left;font-weight:600;color:var(--tx3);font-size:11.5px;
  padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.mod-tab td{padding:8px 10px;border-bottom:1px solid rgba(31,40,54,.55);
  vertical-align:top}
.mod-tab tr:last-child td{border-bottom:0}
.mod-tab td:first-child code{color:var(--mid);font-size:12px}
.mod-tab td code{font-size:11.5px;color:var(--tx2)}
.note{font-size:11.5px;color:var(--tx3);line-height:1.65;margin:10px 0 0}

/* 侧栏 */
.side-card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:15px 17px;margin-bottom:16px}
.side-card h3{margin:0 0 12px;font-size:14px;font-weight:600;
  display:flex;align-items:baseline;gap:8px}
.side-card h3 em{font-style:normal;font-size:10.5px;color:var(--tx3);font-weight:400}
.side-card .kvs{grid-template-columns:1fr}

footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);
  font-size:11.5px;color:var(--tx3);line-height:1.8}
footer code{color:var(--tx2)}
.chart.na-box{display:flex;align-items:center;justify-content:center;min-height:88px;
  color:var(--dim);font-size:12px;border:1px dashed var(--line);border-radius:8px}
table.mod-tab th.nw,table.mod-tab td.nw{white-space:nowrap}
.sm.pair{color:var(--tx3);font-size:11px;letter-spacing:.2px}
table.mod-tab.fut{font-size:12px}
table.mod-tab.fut th{font-size:11px;font-weight:500}
table.mod-tab.fut td{padding:5px 8px;vertical-align:middle}
table.mod-tab.fut td:first-child{white-space:nowrap}
table.mod-tab.fut td:nth-child(2){font-variant-numeric:tabular-nums}
.adm-sec-h{margin:18px 0 8px;font-size:13px;font-weight:650;color:var(--tx2);
  border-top:1px solid var(--line);padding-top:14px}
.adm-sec-h em{font-style:normal;font-weight:400;color:var(--tx3);font-size:11.5px;margin-left:8px}
.adm-spec{margin-top:12px}
.adm-spec summary{cursor:pointer;font-size:12.5px;color:var(--blue);padding:4px 0}
.adm-spec .mod-tab.spec{font-size:11.5px}
.adm-spec .mod-tab.spec td{padding:4px 8px;vertical-align:top}
.adm-spec .mod-tab.spec td:first-child{width:110px;white-space:nowrap}
#regBox .mod-tab.spec{font-size:11.5px}
#regBox button{background:var(--card2);border:1px solid var(--line);color:var(--tx2);
  border-radius:6px;padding:3px 9px;font-size:11.5px;cursor:pointer}
#regBox button:hover{border-color:var(--blue);color:var(--blue)}
"""

JS_ADMIN = r"""
(function(){
  var PW = "__PW__";
  var KEY = "sd_vis_v1";
  var btn = document.getElementById("admBtn");
  var mask = document.getElementById("admMask");
  var gate = document.getElementById("admGate");
  var panel = document.getElementById("admPanel");
  var pw = document.getElementById("admPw");
  var err = document.getElementById("admErr");
  if (!btn || !mask) return;

  function load(){ try { return JSON.parse(localStorage.getItem(KEY) || "{}"); }
                   catch(e){ return {}; } }
  function save(o){ try { localStorage.setItem(KEY, JSON.stringify(o)); } catch(e){} }

  function apply(){
    var o = load();
    var els = document.querySelectorAll("[data-sec]");
    for (var i=0;i<els.length;i++){
      var el = els[i], k = el.getAttribute("data-sec");
      var def = el.getAttribute("data-def") === "0" ? false : true;
      var on = Object.prototype.hasOwnProperty.call(o, k) ? !!o[k] : def;
      el.style.display = on ? "" : "none";
    }
    var ctl = document.querySelectorAll("[data-ctl]");
    for (var j=0;j<ctl.length;j++){
      var c = ctl[j], kk = c.getAttribute("data-ctl");
      var el2 = document.querySelector('[data-sec="'+kk+'"]');
      var d2 = el2 && el2.getAttribute("data-def") === "0" ? false : true;
      c.checked = Object.prototype.hasOwnProperty.call(o, kk) ? !!o[kk] : d2;
    }
  }

  function open(){ mask.style.display = "flex";
    if (sessionStorage.getItem("sd_adm") === "1"){ gate.style.display="none";
      panel.style.display="block"; }
    else { gate.style.display="block"; panel.style.display="none";
      setTimeout(function(){ pw.focus(); }, 60); } }
  function close(){ mask.style.display = "none"; err.textContent=""; pw.value=""; }

  btn.onclick = open;
  document.getElementById("admClose").onclick = close;
  mask.onclick = function(e){ if (e.target === mask) close(); };

  document.getElementById("admGo").onclick = function(){
    if (pw.value === PW){
      sessionStorage.setItem("sd_adm","1");
      gate.style.display="none"; panel.style.display="block";
      err.textContent=""; pw.value="";
      apply();
    } else { err.textContent = "密码不对"; pw.value=""; pw.focus(); }
  };
  pw.onkeydown = function(e){ if (e.key === "Enter") document.getElementById("admGo").click(); };

  var acts = panel.querySelectorAll("[data-act]");
  for (var i=0;i<acts.length;i++){
    acts[i].onclick = function(){
      var a = this.getAttribute("data-act");
      if (a === "all"){ var o={}; var e1=document.querySelectorAll("[data-sec]");
        for(var x=0;x<e1.length;x++){ o[e1[x].getAttribute("data-sec")]=true; }
        save(o); apply(); }
      else if (a === "none"){ var o2={}; var e2=document.querySelectorAll("[data-sec]");
        for(var y=0;y<e2.length;y++){ o2[e2[y].getAttribute("data-sec")]=false; }
        save(o2); apply(); }
      else if (a === "reset"){ try{ localStorage.removeItem(KEY); }catch(e){}
        apply(); }
      else if (a === "exit"){ close(); }
    };
  }

  // 勾选联动：改一个就存一个，立即生效
  var ctls = document.querySelectorAll("[data-ctl]");
  for (var ii=0; ii<ctls.length; ii++){
    ctls[ii].addEventListener("change", function(){
      var o = load();
      o[this.getAttribute("data-ctl")] = this.checked;
      save(o); apply();
    });
  }

  // ── 外部模块注册：审批与管理 ──
  var regBox = document.getElementById("regBox");
  function esc(t){ return String(t==null?"":t).replace(/[&<>"]/g, function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }

  function renderReg(d){
    var ws = d.workers || [];
    var pend = [], appr = [];
    for (var i=0;i<ws.length;i++){
      if (ws[i].status === "pending") pend.push(ws[i]);
      else if (ws[i].status === "approved") appr.push(ws[i]);
    }
    var h = '<p class="adm-tip">待批准 <b class="'+(pend.length?"mid":"")+'">'+pend.length
          + '</b> · 已批准 <b>'+appr.length+'</b></p>';
    function tab(list, acts){
      if (!list.length) return "";
      var x = '<table class="mod-tab spec"><thead><tr><th>app_id</th><th>model</th>'
            + '<th>大类</th><th>调度</th><th>操作</th></tr></thead><tbody>';
      for (var i=0;i<list.length;i++){
        var w = list[i], b = "";
        for (var j=0;j<acts.length;j++){
          b += '<button data-rv="'+acts[j][0]+'" data-id="'+esc(w.app_id)+'">'
             + acts[j][1] + '</button> ';
        }
        x += '<tr><td><code>'+esc(w.app_id)+'</code></td>'
           + '<td>'+esc(w.title||w.key)+'<br><span class="sm">'+esc(w.key)+'</span></td>'
           + '<td>'+esc(w.tab||"—")+'</td>'
           + '<td>'+esc((w.sched&&w.sched.at)||"—")+'</td>'
           + '<td class="nw">'+b+'</td></tr>';
      }
      return x + '</tbody></table>';
    }
    h += tab(pend, [["approve","批准"],["reject","拒绝"]]);
    h += tab(appr, [["delete","注销"]]);
    if (!ws.length) h = '<p class="adm-note">还没有外部模块注册。按下面的规范提交即可。</p>';
    regBox.innerHTML = h;
    var bs = regBox.querySelectorAll("[data-rv]");
    for (var k=0;k<bs.length;k++){
      bs[k].onclick = function(){
        var id = this.getAttribute("data-id"), act = this.getAttribute("data-rv");
        var btn = this;
        btn.disabled = true; btn.textContent = "处理中…";
        fetch("/api/worker/"+id+"/"+act, {method:"POST", cache:"no-store",
          headers:{"X-SD-PW":PW,"Content-Type":"application/json"}, body:"{}"})
          .then(function(r){ return r.json(); })
          .then(function(d){
            if (d && d.error){ alert("操作失败：" + d.error); }
            loadReg();
          })
          .catch(function(e){ alert("请求失败：" + e); loadReg(); });
      };
    }
  }

  function loadReg(){
    if (!regBox) return;
    fetch("/api/registry", {cache:"no-store", headers:{"X-SD-PW":PW}})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (!d || d.error) throw new Error("x");
        renderReg(d);
      })
      .catch(function(){
        regBox.innerHTML = '<p class="adm-note">当前是静态页，读不到调度器。'
          + '审批请打开控制台 <code>http://&lt;主机&gt;:__PORT__/</code>'
          + '（同源才能调 API），静态页只负责展示。</p>';
      });
  }

  apply();
  loadReg();
})();
"""

JS_CORE = r"""
(function(){
  "use strict";
  var NS="http://www.w3.org/2000/svg";
  function el(tag,attrs){var e=document.createElementNS(NS,tag);
    for(var k in attrs){e.setAttribute(k,attrs[k]);}return e;}
  function fmt(v,dec){if(v===null||v===undefined||isNaN(v))return "—";
    dec=(dec===undefined?2:dec);return Number(v).toFixed(dec);}
  function tickText(v,r){if(Math.abs(v)>=1000)return String(Math.round(v));
    var dec=r>=20?0:r>=3?1:r>=0.15?2:3;return v.toFixed(dec);}

  window.drawChart=function(host,cfg){
    if(!host) return;
    host.innerHTML="";
    var labels=cfg.labels||[];
    var series=(cfg.series||[]).filter(function(s){
      return s.values&&s.values.some(function(v){return v!==null&&v!==undefined;});});
    if(!labels.length||!series.length){host.innerHTML='<p class="note">无数据</p>';return;}
    var W=800,H=cfg.height||170;
    var P={t:12,r:10,b:22,l:44};
    var iw=W-P.l-P.r, ih=H-P.t-P.b;
    var all=[];
    series.forEach(function(s){s.values.forEach(function(v){
      if(v!==null&&v!==undefined&&!isNaN(v))all.push(v);});});
    if(!all.length){host.innerHTML='<p class="note">无数据</p>';return;}
    var lo=Math.min.apply(null,all),hi=Math.max.apply(null,all);
    if(cfg.min!==undefined)lo=Math.min(lo,cfg.min);
    if(cfg.max!==undefined)hi=Math.max(hi,cfg.max);
    if(hi===lo){hi=lo+1;lo=lo-1;}
    if(!(cfg.min!==undefined&&cfg.max!==undefined)){
      var padV=(hi-lo)*0.12;lo-=padV;hi+=padV;
    }
    function X(i){return P.l+(labels.length===1?iw/2:iw*i/(labels.length-1));}
    function Y(v){return P.t+ih*(1-(v-lo)/(hi-lo));}
    var svg=el("svg",{viewBox:"0 0 "+W+" "+H,role:"img","aria-label":cfg.aria||"走势图"});
    for(var t=0;t<=4;t++){
      var val=lo+(hi-lo)*t/4,y=Y(val);
      svg.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:y,y2:y,stroke:"#1a2230","stroke-width":1}));
      var tx=el("text",{x:P.l-6,y:y+3.5,"text-anchor":"end",fill:"#6b7889",
        "font-size":10,"font-family":"ui-monospace,monospace"});
      tx.textContent=cfg.yfmt?cfg.yfmt(val):tickText(val,hi-lo);
      svg.appendChild(tx);
    }
    if(lo<0&&hi>0){
      svg.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:Y(0),y2:Y(0),
        stroke:"#3a4658","stroke-width":1,"stroke-dasharray":"3 3"}));
    }
    var step=Math.max(1,Math.ceil(labels.length/5));
    for(var i=0;i<labels.length;i+=step){
      var xt=el("text",{x:X(i),y:H-6,"text-anchor":i===0?"start":
        (i>=labels.length-step?"end":"middle"),fill:"#6b7889","font-size":10,
        "font-family":"ui-monospace,monospace"});
      xt.textContent=String(labels[i]).slice(5);
      svg.appendChild(xt);
    }
    series.forEach(function(s){
      var d="",open=false;
      s.values.forEach(function(v,i){
        if(v===null||v===undefined||isNaN(v)){open=false;return;}
        d+=(open?"L":"M")+X(i).toFixed(1)+" "+Y(v).toFixed(1)+" ";open=true;});
      if(d){svg.appendChild(el("path",{d:d,fill:"none",stroke:s.color,
        "stroke-width":1.7,"stroke-linejoin":"round","stroke-linecap":"round"}));}
    });
    var hov=el("g",{opacity:"0"});
    var vline=el("line",{y1:P.t,y2:P.t+ih,stroke:"#5b6b82","stroke-width":1,
      "stroke-dasharray":"3 3"});
    hov.appendChild(vline);
    var dots=series.map(function(s){
      var c=el("circle",{r:3,fill:s.color,stroke:"#0b0e14","stroke-width":1.2});
      hov.appendChild(c);return c;});
    svg.appendChild(hov);
    var hit=el("rect",{x:P.l,y:P.t,width:iw,height:ih,fill:"transparent",
      style:"cursor:crosshair"});
    svg.appendChild(hit);
    var tip=document.createElement("div");tip.className="tip";
    function move(ev){
      var box=svg.getBoundingClientRect();
      var px=(ev.clientX-box.left)/box.width*W;
      var i=Math.round((px-P.l)/(iw||1)*(labels.length-1));
      i=Math.max(0,Math.min(labels.length-1,i));
      hov.setAttribute("opacity","1");
      vline.setAttribute("x1",X(i));vline.setAttribute("x2",X(i));
      var rows="";
      series.forEach(function(s,j){
        var v=s.values[i];
        var has=(v!==null&&v!==undefined&&!isNaN(v));
        dots[j].setAttribute("opacity",has?"1":"0");
        if(has){dots[j].setAttribute("cx",X(i));dots[j].setAttribute("cy",Y(v));}
        rows+='<div class="r"><span style="color:'+s.color+'">'+s.name+'</span><span>'+
          (has?(cfg.vfmt?cfg.vfmt(v):fmt(v)):"—")+(cfg.unit||"")+'</span></div>';
      });
      tip.innerHTML='<div class="d">'+labels[i]+'</div>'+rows;
      tip.classList.add("on");
      var bw=box.width,tw=tip.offsetWidth;
      var left=(X(i)/W)*bw+12;
      if(left+tw>bw-8){left=(X(i)/W)*bw-tw-12;}
      tip.style.left=Math.max(0,left)+"px";
      tip.style.top=Math.max(0,(Y(0)/H)*box.height-10)+"px";
    }
    hit.addEventListener("mousemove",move);
    hit.addEventListener("mouseleave",function(){
      hov.setAttribute("opacity","0");tip.classList.remove("on");});
    hit.addEventListener("touchstart",function(e){if(e.touches[0])move(e.touches[0]);},{passive:true});
    hit.addEventListener("touchmove",function(e){if(e.touches[0])move(e.touches[0]);},{passive:true});
    host.appendChild(svg);host.appendChild(tip);
  };
})();
"""



# ════════════════════════════════════════════════════════════════
# 分区渲染：期货 / 宏观 / 流动性 / 模块管理
# ════════════════════════════════════════════════════════════════
def section_head(num_txt: str, title: str, sub: str, obs: Optional[str],
                 enabled: bool = True) -> str:
    return ('<div class="sec-head"><span class="sec-num">%s</span>'
            '<div><h2>%s</h2><p>%s</p></div>'
            '<span class="sec-obs">%s</span></div>'
            % (num_txt, html.escape(title), sub, obs or "—"))


def _fpx(v) -> str:
    """期货价格按量级选精度。"""
    if v is None:
        return NA
    a = abs(v)
    if a >= 10000:
        return "%.0f" % v
    if a >= 1000:
        return "%.1f" % v
    return "%.2f" % v


def _fwan(v) -> str:
    if v is None:
        return NA
    return "%.1f万" % (v / 10000.0) if abs(v) >= 10000 else "%.0f" % v


def render_futures_section() -> str:
    """期货栏 —— 品种主连为主，配期限结构（主力−次主力）与基差。

    key 一律是品种主连（AU.SHF）；具体合约 AU2412 / AU0 都是它的别名。
    """
    try:
        snap = fut_snapshot()
        groups = fut_groups()
    except Exception:                                    # noqa: BLE001
        snap, groups = {}, {}

    if not snap:
        return ('<section class="idx-card sec" id="futures">'
                '<header><h3>期货品种 <em>主连 / 期限 / 基差</em></h3>'
                '<div class="obs">取数失败</div></header>'
                '<div class="idx-body"><div class="blk"><div class="kvs">%s</div>'
                '<p class="note">期货日线取数失败（Tushare fut_daily 未返回）。'
                '按「没有数据就不写」的规则这里显示 %s，不用旧值顶替。</p>'
                '</div></div></section>' % (kv("品种快照", NA), NA))

    obs = next(iter(snap.values())).get("date") or "—"
    blocks = []
    for g, keys in groups.items():
        ks = [k for k in keys if k in snap]
        if not ks:
            continue
        rows = []
        for k in ks:
            v = snap[k]
            chg = v.get("cont_chg")
            if chg is None:
                chg_s = NA
            else:
                cls = "up" if chg > 0 else ("down" if chg < 0 else "flat")
                chg_s = '<span class="%s">%+.2f%%</span>' % (cls, chg)
            main = (v.get("main_code") or "").split(".")[0] or NA
            sp = v.get("spread")
            if sp is None:
                sp_s = NA
            else:
                cls = "up" if sp > 0 else ("down" if sp < 0 else "flat")
                sp_s = '<span class="%s">%s</span>' % (cls, _fpx(sp))
            b = v.get("basis")
            if b is None:
                b_s = NA
            else:
                bp = v.get("basis_pct")
                cls = "up" if b > 0 else ("down" if b < 0 else "flat")
                b_s = ('<span class="%s">%s</span>' % (cls, _fpx(b))
                       + ("" if bp is None else ' <span class="sm">(%+.2f%%)</span>' % bp))
            rows.append(
                '<tr><td><b>%s</b> <span class="sm">%s</span></td>'
                '<td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                % (html.escape(v["name"]), k, _fpx(v.get("cont_close")), chg_s,
                   main, sp_s, b_s, _fwan(v.get("cont_oi"))))
        blocks.append(
            '<div class="blk"><h4>%s <span class="sm">%d 个品种</span></h4>'
            '<table class="mod-tab fut"><thead><tr>'
            '<th>品种</th><th>主连</th><th>涨跌</th><th>主力合约</th>'
            '<th>期限</th><th>基差</th><th>持仓</th></tr></thead>'
            '<tbody>%s</tbody></table></div>'
            % (html.escape(g), len(ks), "".join(rows)))

    note = ('<p class="note">口径：<b>主连</b>=品种连续合约收盘；'
            '<b>主力合约</b>=当日持仓量最大的合约；'
            '<b>期限</b>=主力−次主力价差（正=近强远弱 backwardation，负=contango）；'
            '<b>基差</b>=现货指数−主力期货，只有股指期货有现货，商品基差显示 %s。'
            'key 一律用品种主连（<code>AU.SHF</code>），'
            '<code>AU2412</code> / <code>AU0</code> / <code>au</code> 都是它的别名。</p>' % NA)

    return ('<section class="idx-card sec" id="futures">'
            '<header><h3>期货品种 <em>主连 / 期限 / 基差</em></h3>'
            '<div class="obs">%s</div></header>'
            '<div class="idx-body">%s%s</div></section>'
            % (html.escape(obs), "".join(blocks), note))


def render_realestate_section() -> str:
    """地产栏 —— 本机没有地产任务落库，只留栏目，不编造。"""
    rows = [("商品房销售面积累计同比", None), ("房地产开发投资累计同比", None),
            ("70 城新房价格同比", None), ("70 城二手房价格同比", None),
            ("地产债信用利差", None), ("房企到位资金同比", None)]
    lis = "".join(kv(k, NA) for k, _ in rows)
    return ('<section class="idx-card sec" id="realestate">'
            '<header><h3>地产 <em>销售 · 投资 · 房价 · 地产债</em></h3>'
            '<div class="obs">本机无数据</div></header>'
            '<div class="idx-body"><div class="blk">'
            '<h4>地产景气与融资</h4><div class="kvs">%s</div>'
            '<p class="note">⚠️ 本机 crontab 里没有地产相关任务，'
            'EDB 也只有美国实际利率两条相关序列，没有国内地产数据。<br>'
            '按「没有数据就不写」的规则，这里只保留栏目，显示 '
            '<span class="na">NA</span>。等有任务往指标仓塞 '
            '<code>realestate.*</code> 契约数据，这块自动就有值。</p>'
            '</div></div></section>' % lis)


def load_sections(path: str) -> Dict:
    import json as _json
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {"password": "123456", "sections": []}


def sec_style(enabled: bool) -> str:
    return "" if enabled else ' style="display:none"'


def render_macro_section(latest: Dict, series: Dict, obs: Optional[str]) -> str:
    cn = [
        ("M2 同比", "macro.m2_yoy", 1, "%", "宽货币的锚"),
        ("M1-M2 剪刀差", "macro.m1m2_scissor", 1, "pp", "企业活期活化度"),
        ("GDP 同比", "macro.gdp_yoy", 1, "%", "当季不变价"),
        ("CPI 同比", "macro.cpi_yoy", 1, "%", "居民端通胀"),
        ("PPI 同比", "macro.ppi_yoy", 1, "%", "上游盈利周期"),
        ("社融增量", "macro.tsf_inc", 0, "亿", "当月新增"),
        ("社融存量", "macro.tsf_stock", 0, "亿", "信用总水位"),
    ]
    gl = [
        ("美元指数 DXY", "macro.dxy", 2, "", "走强压制新兴市场"),
        ("离岸 USD/CNH", "macro.cnh", 4, "", "上行=人民币贬值"),
        ("在岸 USD/CNY", "macro.usdcny", 4, "", "在岸即期"),
    ]
    cn_html = "".join(kvh("%s <span class=\"sm\">%s</span>" % (n, note),
                         num(v(latest, k, MACRO_SEC), d, u))
                     for n, k, d, u, note in cn)
    gl_html = "".join(kvh("%s <span class=\"sm\">%s</span>" % (n, note),
                         num(v(latest, k, GLOBAL_SEC), d, u))
                     for n, k, d, u, note in gl)

    charts = [
        ("mc_m2", ("macro.m2_yoy", MACRO_SEC), "M2同比", "#4a9eff", "%", 1),
        ("mc_sc", ("macro.m1m2_scissor", MACRO_SEC), "M1-M2剪刀差", "#e0a33e", "pp", 1),
        ("mc_tsf", ("macro.tsf_inc", MACRO_SEC), "社融增量", "#ff4d5e", "亿", 0),
        ("mc_dxy", ("macro.dxy", GLOBAL_SEC), "美元指数", "#9b7bff", "", 2),
        ("mc_cnh", ("macro.cnh", GLOBAL_SEC), "离岸人民币", "#22c55e", "", 4),
    ]
    ch_html = ""
    _live = []
    for _c in charts:
        _cid, _k, _nm = _c[0], _c[1], _c[2]
        _n = len(series.get(_k) or [])
        if _n < 3:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart na-box">数据点不足（%d 个），暂不绘图</div></div>'
                        % (_nm, _n))
        else:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart" id="%s"></div></div>' % (_nm, _cid))
            _live.append(_c)
    charts = _live

    return ('<section class="idx-card sec" id="macro">'
            '<header><h3>宏观与美元环流 <em>M2 · GDP · 社融 · 汇率</em></h3>'
            '<div class="obs">月/季频</div></header>'
            '<div class="idx-body">'
            '<div class="blk"><h4>货币与信用（全国口径）</h4><div class="kvs">%s</div></div>'
            '<div class="blk"><h4>美元与人民币</h4><div class="kvs">%s</div>'
            '<p class="note">宏观为月/季频，与日频行情不是同一条时间轴，'
            '所以数值按各自最新一期显示，日期见指标仓。</p></div>'
            '%s</div></section>' % (cn_html, gl_html, ch_html)), charts


def render_liquidity_section(latest: Dict, series: Dict, obs: Optional[str]) -> str:
    rate = [
        ("DR001", "liquidity.dr001", 3, "%", "银行间隔夜"),
        ("DR007", "liquidity.dr007", 3, "%", "7天政策观察锚"),
        ("SHIBOR 3M", "liquidity.shibor_3m", 3, "%", "中期资金成本"),
        ("GC001", "liquidity.gc001", 3, "%", "交易所逆回购"),
        ("央行 OMO 净投放", "liquidity.omo_net", 0, "亿", "正=净投放"),
    ]
    spread = [
        ("中国 10Y 国债", "liquidity.cn10y", 3, "%", ""),
        ("中国 2Y 国债", "liquidity.cn2y", 3, "%", ""),
        ("期限利差 10Y-2Y", "liquidity.spread_10y2y", 1, "bp", "走阔=曲线陡化"),
        ("中美利差 10Y", "liquidity.spread_cn_us", 1, "bp", "负=倒挂"),
    ]
    mg = [
        ("两融余额", "margin.rzrqye", 0, "亿", "两市合计"),
        ("融资余额", "margin.rzye", 0, "亿", ""),
        ("融券余额", "margin.rqye", 0, "亿", ""),
        ("维持担保比例", "margin.maint_ratio", 1, "%", "越低越近强平区"),
    ]
    rate_html = "".join(kvh("%s <span class=\"sm\">%s</span>" % (n, note),
                           num(v(latest, k, LIQ_SEC), d, u))
                        for n, k, d, u, note in rate)
    sp_html = "".join(kvh("%s <span class=\"sm\">%s</span>" % (n, note),
                         num(v(latest, k, LIQ_SEC), d, u))
                      for n, k, d, u, note in spread)
    mg_html = "".join(kvh("%s <span class=\"sm\">%s</span>" % (n, note),
                         num(v(latest, k, MARGIN_SEC), d, u))
                      for n, k, d, u, note in mg)

    charts = [
        ("lq_dr", ("liquidity.dr001", LIQ_SEC), "DR001", "#4a9eff", "%", 3),
        ("lq_us", ("liquidity.spread_cn_us", LIQ_SEC), "中美10Y利差", "#e0a33e", "bp", 1),
        ("lq_mg", ("margin.rzrqye", MARGIN_SEC), "两融余额", "#ff4d5e", "亿", 0),
    ]
    ch_html = ""
    _live = []
    for _c in charts:
        _cid, _k, _nm = _c[0], _c[1], _c[2]
        _n = len(series.get(_k) or [])
        if _n < 3:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart na-box">数据点不足（%d 个），暂不绘图</div></div>'
                        % (_nm, _n))
        else:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart" id="%s"></div></div>' % (_nm, _cid))
            _live.append(_c)
    charts = _live

    return ('<section class="idx-card sec" id="liquidity">'
            '<header><h3>流动性与杠杆 <em>资金价格 · 利差 · 两融</em></h3>'
            '<div class="obs">日频</div></header>'
            '<div class="idx-body">'
            '<div class="blk"><h4>资金价格</h4><div class="kvs">%s</div></div>'
            '<div class="blk"><h4>国债与利差</h4><div class="kvs">%s</div></div>'
            '<div class="blk"><h4>两融与杠杆（两市账户口径）</h4><div class="kvs">%s</div>'
            '<p class="note">两融用 <code>margin_account.db</code> 两市合计口径，'
            '不用只有沪市的 <code>margin_history.db</code>。</p></div>'
            '%s</div></section>' % (rate_html, sp_html, mg_html, ch_html)), charts


def render_registered_model(w: Dict, latest: Dict, series: Dict) -> tuple:
    """把外部注册的 model 渲染成一个大类下的子块。返回 (html, charts)。"""
    key = w.get("key")
    rows, charts = [], []
    dflt_sec = w.get("sec") or "CN.MACRO"
    for it in (w.get("metrics") or []):
        if isinstance(it, str):
            it = {"key": it, "sec": dflt_sec, "name": it}
        k = it.get("key")
        sc = it.get("sec") or dflt_sec
        nm = it.get("name") or k
        d = latest.get((k, sc)) or {}
        vnum = d.get("value_num") if d.get("quality") == "ok" else None
        o = d.get("obs_date") or "—"
        unit = it.get("unit") or ""
        dec = int(it.get("dec", 2))
        vs = "—" if vnum is None else (("%." + str(dec) + "f") % vnum) + unit
        rows.append(kv(nm, '<b>%s</b> <span class="sm">%s</span>' % (vs, o)))
        sv = series.get((k, sc))
        if sv and len(sv) >= 3:
            cid = "rw_%s_%d" % (key, len(charts))
            charts.append((cid, k, nm, it.get("color") or "#4a9eff", unit, dec))
    ch_html = "".join(
        '<div class="blk"><h4>%s</h4><div class="chart" id="%s"></div></div>' % (nm, cid)
        for cid, _k, nm, _c, _u, _d in charts)
    meta = ('<span class="sm">注册 Worker %s · %s</span>'
            % (html.escape(str(w.get("app_id") or "")),
               html.escape(_sched_txt(w.get("sched")))))
    head = ('<div class="blk"><h4>%s %s</h4>'
            '<p class="note">%s</p><div class="kvs">%s</div></div>'
            % (html.escape(w.get("title") or key), meta,
               html.escape(w.get("desc") or ""), "".join(rows)))
    return head + ch_html, charts


def _sched_txt(sched: Optional[Dict]) -> str:
    """{"at":"21:00","weekdays":[1..5]} → 工作日 21:00"""
    if not sched:
        return "—"
    at = str(sched.get("at") or "").strip() or "—"
    wd = sorted(int(x) for x in (sched.get("weekdays") or []) if str(x).isdigit())
    if not wd or len(wd) == 7:
        d = "每天"
    elif wd == [1, 2, 3, 4, 5]:
        d = "工作日"
    elif wd == [6, 7]:
        d = "周末"
    else:
        d = "周" + "".join("一二三四五六日"[x - 1] for x in wd if 1 <= x <= 7)
    return "%s %s" % (d, at)


def _load_workers(only_approved: bool = True) -> list:
    """读外部注册的 Worker（registry.json）。"""
    import json as _json
    fp = os.path.join(HERE, "registry.json")
    try:
        with open(fp, encoding="utf-8") as f:
            d = _json.load(f)
    except (OSError, ValueError):
        return []
    ws = d.get("workers") or []
    if only_approved:
        ws = [w for w in ws if w.get("status") == "approved"]
    return ws


def _all_modules(cfg: Dict) -> list:
    """内置 + 已批准的注册 Worker，key 唯一，内置优先。"""
    mods, seen = [], set()
    for m in (cfg.get("modules") or []):
        if m.get("key") and m["key"] not in seen:
            m = dict(m)
            m["source"] = "builtin"
            mods.append(m)
            seen.add(m["key"])
    for w in _load_workers(True):
        k = w.get("key")
        if k and k not in seen:
            w = dict(w)
            w["source"] = "registered"
            mods.append(w)
            seen.add(k)
    return mods


def render_modules_panel(cfg_path: str, st: Optional[Dict]) -> str:
    """模块管理 —— 大类 Tab / 子类 model / 每个 model 一条 task。"""
    import json as _json
    from datetime import date as _date, datetime as _dt
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _json.load(f)
    except (OSError, ValueError):
        cfg = {"modules": []}
    from data_hub.tech import registry as _reg
    try:
        from data_hub.tech import store as _store
        conn = _store.connect()
    except Exception:                                         # noqa: BLE001
        conn = None

    st = st or {}
    mstate = st.get("modules") or {}
    mods = _all_modules(cfg)

    def _fresh(doms):
        cells = []
        for d in doms:
            keys = [k for k, vv in _reg.REGISTRY.items() if vv.domain == d]
            latest = None
            if conn is not None and keys:
                q = ",".join("?" * len(keys))
                r = conn.execute("SELECT MAX(obs_date) FROM metric_points "
                                 "WHERE metric_key IN (%s) AND quality='ok'" % q,
                                 keys).fetchone()
                latest = r[0] if r else None
            if not latest:
                cells.append('<span class="na">%s 空</span>' % d)
                continue
            try:
                age = (_date.today() - _dt.strptime(latest, "%Y-%m-%d").date()).days
            except ValueError:
                age = 99
            cls = "up" if age <= 1 else ("mid" if age <= 4 else "down")
            cells.append('<span class="%s">%s %s</span>' % (cls, d, latest))
        return " · ".join(cells) if cells else '<span class="sm">—</span>'

    rows = []
    for m in mods:
        k = m.get("key")
        ms = mstate.get(k) or {}
        on = m.get("enabled", True)
        lstat = ms.get("last_status") or ""
        st_cls = {"ok": "up", "timeout": "down", "fail": "down",
                  "stopped": "down"}.get(lstat, "sm")
        st_txt = {"ok": "成功", "timeout": "超时", "fail": "失败",
                  "stopped": "已停"}.get(lstat, "—")
        dur = ("%ss" % ms["duration"]) if ms.get("duration") else "—"
        src = "注册" if m.get("source") == "registered" else "内置"
        rows.append(
            '<tr><td><code>%s</code></td><td>%s</td>'
            '<td class="nw"><span class="sm">%s</span></td>'
            '<td class="nw">%s</td>'
            '<td class="nw %s">%s</td>'
            '<td class="nw">%s</td><td class="nw">%s</td>'
            '<td class="nw %s">%s <span class="sm">%s</span></td>'
            '<td>%s</td></tr>'
            % (html.escape(k), html.escape(m.get("title") or k), src,
               html.escape(_sched_txt(m.get("sched"))),
               "up" if on else "down", "启用" if on else "停用",
               html.escape(str(ms.get("next_run") or "—")),
               html.escape(str(ms.get("last_run") or "—")),
               st_cls, st_txt, dur,
               _fresh(m.get("domains") or [])))

    # 大类 → 子类 model 树
    try:
        with open(os.path.join(HERE, "sections.json"), encoding="utf-8") as f:
            secs = (_json.load(f).get("sections") or [])
    except (OSError, ValueError):
        secs = []
    tree = []
    for s in secs:
        if s.get("key") == "modules":
            continue
        subs = s.get("subs") or []
        if subs:
            items = "".join(
                '<span class="tag">%s</span>' % html.escape(
                    x.get("title") or x.get("key") or "")
                for x in subs)
        else:
            items = '<span class="sm">（单 model）</span>'
        tag = ('<span class="sm">注册</span>'
               if s.get("source") == "registered" else "")
        tree.append('<tr><td class="nw"><b>%s</b> %s</td><td class="nw">%s</td>'
                    '<td>%s</td></tr>'
                    % (html.escape(s.get("title") or s.get("key")),
                       tag, "%d" % max(1, len(subs)), items))
    if conn is not None:
        conn.close()

    pend = [w for w in _load_workers(False) if w.get("status") == "pending"]

    return ('<section class="idx-card sec" id="modules">'
            '<header><h3>模块管理 <em>大类 Tab → 子类 model → 每个 model 一条 task</em></h3>'
            '<div class="obs">%d 条 task · %d 个大类</div></header>'
            '<div class="idx-body"><div class="blk">'
            '<h4>大类与子类 model</h4>'
            '<table class="mod-tab"><thead><tr><th class="nw">大类 Tab</th>'
            '<th class="nw">model 数</th><th>子类 model</th></tr></thead>'
            '<tbody>%s</tbody></table>'
            '</div>'
            '<div class="blk">'
            '<h4>Task 清单 <span class="sm">（Python 调度器管理，不用 crontab）</span></h4>'
            '<table class="mod-tab"><thead><tr>'
            '<th>key</th><th>名称</th><th class="nw">来源</th>'
            '<th class="nw">调度</th><th class="nw">状态</th>'
            '<th class="nw">下次跑</th><th class="nw">上次跑</th>'
            '<th class="nw">结果</th><th>数据新鲜度</th>'
            '</tr></thead><tbody>%s</tbody></table>'
            '<p class="note">调度器 <code>scheduler.py</code> 常驻管理全部 task，'
            '不用系统 crontab。控制台：<code>http://&lt;主机&gt;:8766/</code>'
            '（同源，可直接启停 / 立即跑 / 审批注册）。<br>'
            '外部模块接入：<code>POST /api/register</code> 带注册密钥提交，'
            '后台批准后自动出现在对应大类下并开始定时更新。'
            '%s</p>'
            '</div></div></section>'
            % (len(mods), len(tree), "".join(tree), "".join(rows),
               ('<br><b class="mid">有 %d 个注册申请待批准</b>' % len(pend))
               if pend else ""))


def build(out_path: str, asof: Optional[str] = None) -> str:
    latest, series, obs = load_all(asof)
    st = stats()

    # 顶部总览
    scores = [v(latest, "chanlun.bull_score", s) for s in INDEX_ORDER]
    scores = [x for x in scores if x is not None]
    avg_score = sum(scores) / len(scores) if scores else None
    oes = [v(latest, "rmt.outlier_energy", s) for s in INDEX_ORDER]
    oes = [x for x in oes if x is not None]
    avg_oe = sum(oes) / len(oes) if oes else None

    # ── 板块配置 ──
    SEC = load_sections(os.path.join(HERE, "sections.json"))
    SECMAP = {x["key"]: x for x in SEC.get("sections", [])}

    def en(k: str) -> bool:
        return SECMAP.get(k, {}).get("enabled", True)

    def sty(k: str) -> str:
        return sec_style(en(k))

    def sh(k: str, obs_d: Optional[str] = None) -> str:
        m = SECMAP.get(k) or {}
        return section_head(m.get("num", ""), m.get("title", k),
                            m.get("sub", ""), obs_d, en(k))

    cards = []
    for sid in INDEX_ORDER:
        body = render_index_card(sid, latest, series, obs)
        # 把 blk 们包进 .idx-body（header 之后）
        body = body.replace("</header>", '</header><div class="idx-body">', 1)
        body = body.replace("</section>", "</div></section>", 1)
        sk = "i_%s" % sid.replace(".", "_")
        sub_def = 1
        for _m in (SECMAP.get("stock", {}).get("subs") or []):
            if _m.get("key") == sk:
                sub_def = 1 if _m.get("enabled", True) else 0
        body = body.replace('id="%s"' % sk,
                            'id="%s" data-sec="%s" data-def="%d"' % (sk, sk, sub_def), 1)
        cards.append(body)

    # 图表脚本
    scripts = []
    for sid in INDEX_ORDER:
        sk = sid.replace(".", "_")
        scripts.append(chart_payload("c_%s" % sk, series.get(("chanlun.bull_score", sid)),
                                     "缠论评分", "#e0a33e", dec=0,
                                     axis={"min": 0, "max": 100}))
        extra_oe = None
        scripts.append(chart_payload("r_%s" % sk, series.get(("rmt.ar5", sid)),
                                     "AR5", "#4a9eff", dec=3))
        hs = series.get(("dfa.hurst_short", sid)) or []
        hl = series.get(("dfa.hurst_long", sid)) or []
        # 短长 H 合并成双线
        m = {}
        for r in hs:
            if r.get("quality") == "ok" and r.get("value_num") is not None:
                m.setdefault(r["obs_date"], {})["s"] = r["value_num"]
        for r in hl:
            if r.get("quality") == "ok" and r.get("value_num") is not None:
                m.setdefault(r["obs_date"], {})["l"] = r["value_num"]
        if len(m) >= 3:
            labels = sorted(m)
            cfg = {"labels": labels, "height": 168, "aria": "Hurst",
                   "series": [
                       {"name": "短H(60日)", "color": "#4a9eff",
                        "values": [m[d].get("s") for d in labels]},
                       {"name": "长H(250日)", "color": "#ff4d5e",
                        "values": [m[d].get("l") for d in labels]},
                   ]}
            scripts.append('<script>drawChart(document.getElementById(%s),%s);</script>'
                           % (json.dumps("d_%s" % sk), json.dumps(cfg, ensure_ascii=False)))
        scripts.append(chart_payload("t_%s" % sk, series.get(("trend.pct_20d", sid)),
                                     "20日涨跌", "#22c55e", unit="%", dec=2))

    # ── 分区内容 ──
    futures_sec = render_futures_section()
    re_sec = render_realestate_section()
    macro_sec, macro_charts = render_macro_section(latest, series, obs)
    liq_sec, liq_charts = render_liquidity_section(latest, series, obs)
    mod_sec = render_modules_panel(os.path.join(HERE, "modules.json"), st)

    # ── 外部注册的 model：按所属大类分组 ──
    KNOWN_TABS = ("stock", "futures", "realestate", "macro", "liquidity")
    reg_in = {t: [] for t in KNOWN_TABS}
    reg_new = []
    reg_charts = []
    _rws = _load_workers(True)
    for _w in _rws:
        _need = {}
        for _it in (_w.get("metrics") or []):
            if isinstance(_it, str):
                _it = {"key": _it, "sec": _w.get("sec") or "CN.MACRO"}
            _k2 = _it.get("key")
            _s2 = _it.get("sec") or _w.get("sec") or "CN.MACRO"
            if _k2 and (_k2, _s2) not in latest:
                _need.setdefault(_s2, []).append(_k2)
            if _k2 and (_k2, _s2) not in series:
                try:
                    series[(_k2, _s2)] = read_series(_k2, _s2)
                except Exception:                             # noqa: BLE001
                    series[(_k2, _s2)] = []
        for _s2, _ks in _need.items():
            try:
                latest.update(read_latest(_ks, [_s2]))
            except Exception:                                 # noqa: BLE001
                pass
    for _w in _rws:
        _h, _cs = render_registered_model(_w, latest, series)
        reg_charts.extend(_cs)
        _tab = _w.get("tab") or ""
        if _tab in KNOWN_TABS:
            reg_in[_tab].append(_h)
        else:
            reg_new.append((_w.get("key"), _w.get("title") or _w.get("key"),
                            _w.get("desc") or "", _h))
    reg_in = {k: "".join(v) for k, v in reg_in.items()}
    extra_secs = "".join(
        '<div class="sec-wrap" data-sec="%s" data-def="1"%s>'
        '<section class="idx-card sec" id="%s">'
        '<header><h3>%s <em>外部注册</em></h3><div class="obs">Worker</div></header>'
        '<div class="idx-body">%s</div></section></div>'
        % (html.escape(k), sty(k), html.escape(k), html.escape(t), h)
        for k, t, _d, h in reg_new)
    for cid, key, nm, color, unit, dec in reg_charts:
        scripts.append(chart_payload(cid, series.get(key), nm, color, unit=unit, dec=dec))

    for cid, key, nm, color, unit, dec in macro_charts:
        scripts.append(chart_payload(cid, series.get(key), nm, color, unit=unit, dec=dec))
    for cid, key, nm, color, unit, dec in liq_charts:
        scripts.append(chart_payload(cid, series.get(key), nm, color, unit=unit, dec=dec))

    # ── 管理面板：板块显隐清单 ──
    adm_rows = []
    for m in SEC.get("sections", []):
        adm_rows.append(
            '<label class="adm-row"><input type="checkbox" data-ctl="%s"%s>'
            '<b>%s</b><span>%s</span></label>'
            % (m["key"], " checked" if m.get("enabled", True) else "",
               html.escape(m["title"]), html.escape(m.get("sub") or "")))
        for sm in (m.get("subs") or []):
            adm_rows.append(
                '<label class="adm-row adm-l2"><input type="checkbox" data-ctl="%s"%s>'
                '<b>%s</b><span></span></label>'
                % (sm["key"], " checked" if sm.get("enabled", True) else "",
                   html.escape(sm["title"])))
    adm_html = "".join(adm_rows)
    adm_pw = html.escape(str(SEC.get("password") or "123456"))
    try:
        _mc = json.load(open(os.path.join(HERE, "modules.json"), encoding="utf-8"))
        _api = _mc.get("api") or {}
    except (OSError, ValueError):
        _api = {}
    adm_key = html.escape(str(_api.get("registry_key") or _api.get("password") or "123456"))
    adm_port = int(_api.get("port") or 8767)

    jump = ('<a href="#stock">股票 · 宽基</a>'
            '<a href="#futures">期货 · 基差</a>'
            '<a href="#realestate">地产</a>'
            '<a href="#macro">宏观</a>'
            '<a href="#liquidity">流动性</a>'
            '<a href="#modules">模块管理</a>')
    idx_jump = "".join('<a class="sub" href="#i_%s">%s</a>'
                       % (s.replace(".", "_"), security_name(s)) for s in INDEX_ORDER)

    def _obs_of(pairs):
        ds = []
        for _k, _sc in pairs:
            _d = (latest.get((_k, _sc)) or {}).get("obs_date")
            if _d:
                ds.append(_d)
        return max(ds) if ds else None

    macro_obs = _obs_of([(k, MACRO_SEC) for k in MACRO_CN_KEYS]
                        + [(k, GLOBAL_SEC) for k in MACRO_GL_KEYS])
    liq_obs = _obs_of([(k, LIQ_SEC) for k in LIQ_KEYS]
                      + [(k, MARGIN_SEC) for k in MARGIN_KEYS])

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Dashboard · 股票 / 期货 / 宏观 / 流动性</title>
<style>{CSS}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>Stock Dashboard<small>股票 · 期货 · 宏观 · 流动性</small></h1>
  <button id="admBtn" class="adm-btn" title="管理板块显示">⚙ 管理</button>
  <div class="top-meta">
    <div><b>{obs or "NA"}</b><span>数据日（上一交易日）</span></div>
    <div><b class="{'up' if (avg_score or 50) >= 50 else 'down'}">
      {("%.0f" % avg_score) if avg_score is not None else "NA"}</b><span>缠论评分均值</span></div>
    <div><b>{("%.4f" % avg_oe) if avg_oe is not None else "NA"}</b><span>平均离群能量</span></div>
    <div><b>{st['total_points']}</b><span>指标点位数</span></div>
  </div>
</header>

<nav class="jump">{jump}<span class="jump-sep"></span>{idx_jump}</nav>

<div class="layout">
  <div class="grid-idx">

    <div class="sec-wrap" data-sec="stock" data-def="1"{sty("stock")}>
      {sh("stock", obs)}
      <div class="sec-grid">
        {"".join(cards)}
        {reg_in['stock']}
      </div>
    </div>

    <div class="sec-wrap" data-sec="futures" data-def="1"{sty("futures")}>
      {sh("futures")}
      <div class="sec-grid">
        {futures_sec}
        {reg_in['futures']}
      </div>
    </div>

    <div class="sec-wrap" data-sec="realestate" data-def="1"{sty("realestate")}>
      {sh("realestate")}
      <div class="sec-grid">
        {re_sec}
        {reg_in['realestate']}
      </div>
    </div>

    <div class="sec-wrap" data-sec="macro" data-def="1"{sty("macro")}>
      {sh("macro", macro_obs)}
      <div class="sec-grid">
        {macro_sec}
        {reg_in['macro']}
      </div>
    </div>

    <div class="sec-wrap" data-sec="liquidity" data-def="1"{sty("liquidity")}>
      {sh("liquidity", liq_obs)}
      <div class="sec-grid">
        {liq_sec}
        {reg_in['liquidity']}
      </div>
    </div>

    {extra_secs}

    <div class="sec-wrap" data-sec="modules" data-def="1"{sty("modules")}>
      {sh("modules")}
      <div class="sec-grid">
        {mod_sec}
      </div>
    </div>

  </div>
  <aside id="side">
    {render_side_panel(latest, st, obs)}
  </aside>
</div>

<footer>
  数据源：DataHub · tech 指标仓（<code>{html.escape(st['db_path'])}</code>）·
  覆盖 {st['n_metrics']} 个指标 / {st['n_securities']} 个证券 ·
  日期范围 {st['date_min'] or "—"} ~ {st['date_max'] or "—"}<br>
  取不到值一律显示 <span class="na">NA</span>，不插值、不用上期值顶替。
  算法口径见 <code>data_hub/tech/CONTRACT.md</code>。<br>
  生成时间 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ·
  纯静态页面，不触发任何计算，只读库渲染。
</footer>

</div>

<div class="adm-mask" id="admMask">
  <div class="adm-box">
    <div class="adm-hd">
      <b>管理</b>
      <button id="admClose" class="adm-x" title="关闭">×</button>
    </div>

    <div id="admGate">
      <p class="adm-tip">输入管理密码</p>
      <input id="admPw" type="password" inputmode="numeric" placeholder="密码"
             autocomplete="off">
      <div class="adm-err" id="admErr"></div>
      <button id="admGo" class="adm-primary">进入</button>
    </div>

    <div id="admPanel" style="display:none">
      <p class="adm-tip">勾选要显示的板块，立即生效（记在这台浏览器）</p>
      <div class="adm-list">{adm_html}</div>
      <div class="adm-acts">
        <button data-act="all">全显示</button>
        <button data-act="none">全隐藏</button>
        <button data-act="reset">恢复默认</button>
        <button data-act="exit" class="adm-primary">完成</button>
      </div>
      <p class="adm-note">这里是前端门锁（密码 <code>{adm_pw}</code>），只防误触，不是服务端鉴权。<br>
      要让所有人生效，改 <code>sections.json</code> 后重新构建发布。</p>

      <div class="adm-sec-h">外部模块接入 <em>注册密钥 {adm_key}</em></div>
      <div id="regBox"><p class="adm-note">加载中…</p></div>
      <details class="adm-spec">
        <summary>接口规范（POST /api/register）</summary>
        <p class="adm-note">请求头 <code>X-SD-KEY: {adm_key}</code>，body 为 JSON：</p>
        <table class="mod-tab spec"><tbody>
        <tr><td><code>tab</code></td><td>挂到哪个大类 Tab，如 <code>macro</code>；写新名字就新建一个大类</td></tr>
        <tr><td><code>key</code></td><td>model 唯一键，字母开头，如 <code>my_indicator</code></td></tr>
        <tr><td><code>title</code></td><td>显示名，如「我的指标」</td></tr>
        <tr><td><code>desc</code></td><td>一句话说明</td></tr>
        <tr><td><code>metrics</code></td><td>它写进库的指标：<code>[{{"key":"macro.m2_yoy","sec":"CN.MACRO","name":"M2同比","unit":"%","dec":1}}]</code></td></tr>
        <tr><td><code>sched</code></td><td>定时：<code>{{"at":"21:30","weekdays":[1,2,3,4,5]}}</code></td></tr>
        <tr><td><code>cmd</code></td><td>到点执行的命令</td></tr>
        <tr><td><code>cwd</code></td><td>执行目录（可选）</td></tr>
        <tr><td><code>timeout</code></td><td>超时秒数（可选，默认 1800）</td></tr>
        </tbody></table>
        <p class="adm-note">curl 示例：<br>
        <code>curl -X POST -H 'X-SD-KEY: {adm_key}' -H 'Content-Type: application/json' \<br>
        &nbsp;&nbsp;-d '{{"tab":"macro","key":"my_ind","title":"我的指标","sched":{{"at":"21:30","weekdays":[1,2,3,4,5]}},"cmd":"python3 /path/x.py"}}' \<br>
        &nbsp;&nbsp;http://&lt;主机&gt;:8767/api/register</code></p>
        <p class="adm-note">提交后是 <b>pending</b>，在上面的列表点「批准」才会显示和调度。</p>
      </details>
    </div>
  </div>
</div>

<script>{JS_CORE}</script>
<script>{JS_ADMIN.replace("__PW__", adm_pw).replace("__PORT__", str(adm_port))}</script>
{"".join(scripts)}
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "index.html"))
    ap.add_argument("--asof", default=None, help="指定观测日 YYYY-MM-DD")
    a = ap.parse_args()
    p = build(a.out, a.asof)
    size = os.path.getsize(p)
    print("已生成 %s  %.1f KB" % (p, size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
