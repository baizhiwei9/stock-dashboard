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
    FUTURES, REGISTRY, count_alerts, fut_groups, fut_snapshot,
    list_risk_points, opt_asof, opt_snapshot,
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

BOND_KEYS = ["bond.govt_3m", "bond.govt_6m", "bond.govt_1y", "bond.govt_3y",
             "bond.govt_5y", "bond.govt_7y", "bond.govt_10y", "bond.govt_30y",
             "bond.spread_10y1y", "bond.spread_30y10y", "bond.curve_shape",
             "bond.govt_10y_pct", "bond.corp_aaa_3y", "bond.corp_spread_3y",
             "bond.cd_aaa_1y", "bond.cd_spread_1y", "bond.mmf_7d_median",
             "bond.mmf_7d_p25", "bond.mmf_7d_p75", "bond.wealth_gap"]

# 期权 / 波动率（各标的）
OPTION_KEYS = ["option.atm_iv", "option.iv_skew", "option.pcr_oi",
               "option.pcr_vol", "option.vix"]
VOL_KEYS = ["vol.atm_iv", "vol.iv_pctile", "vol.direction",
            "vol.alert", "vol.surface_state", "vol.surface_risk_count"]
BOTTOM_KEYS = ["bottom.position", "bottom.triggered", "bottom.prediction"]

# 风险 / 结构（分口径落不同标的）
RISK_ALLA_KEYS = ["risk.score_all_a", "risk.struct_score", "risk.volume_score",
                  "risk.extreme_score", "risk.grade", "risk.cdi",
                  "risk.abs_factor", "struct.cfc", "struct.cfc_signal"]
RISK_IDX_KEYS = ["risk.concentration", "risk.up50", "midline.up50"]
RISK_HS3_KEYS = ["risk.release_composite", "risk.release_vix",
                 "risk.release_pcr", "risk.release_skew"]
RISK_VIRT_KEYS = ["risk.daily_composite", "risk.gsratio_cross",
                  "style.drift_z", "style.drift_state", "style.drift_advice",
                  "midline.gex_alert", "midline.kurt_alert"]

# 股债性价比（ERP）：沪深300 口径
ERP_KEYS = ["stock.erp_300", "stock.ey_300", "stock.pe_300", "stock.erp_300_pct"]

ALLA_SEC = "881001.WI"        # 万得全 A：全市场口径
RISK_IDX_SEC = "000852.SH"    # 中证1000：多数结构指标落这个标的
RISK_HS3_SEC = "000300.SH"    # 沪深300：风险释放走这个标的
ERP_SEC = "000300.SH"         # 沪深300：股债性价比口径

BOND_SEC = "CN.BOND"

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
    ext_latest.update(read_latest(BOND_KEYS, [BOND_SEC], asof=asof))
    latest.update(ext_latest)

    # 期权 / 波动率 / 顶底：按标的 + 虚拟标的（空串）
    _o = read_latest(OPTION_KEYS + VOL_KEYS, OPT_IDS, asof=asof)
    _o.update(read_latest(BOTTOM_KEYS + ["vol.surface_risk_count"], [""], asof=asof))
    latest.update(_o)

    # 风险 / 结构 / 风格 / 中线
    latest.update(read_latest(RISK_ALLA_KEYS, [ALLA_SEC], asof=asof))
    latest.update(read_latest(RISK_IDX_KEYS, [RISK_IDX_SEC], asof=asof))
    latest.update(read_latest(RISK_HS3_KEYS, [RISK_HS3_SEC], asof=asof))
    latest.update(read_latest(RISK_VIRT_KEYS, [""], asof=asof))

    # 股债性价比（ERP）：沪深300 口径，卡片挂在股票栏
    latest.update(read_latest(ERP_KEYS, [ERP_SEC], asof=asof))

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
    for k in ("bond.govt_10y", "bond.spread_10y1y", "bond.corp_spread_3y",
              "bond.cd_aaa_1y"):
        series[(k, BOND_SEC)] = read_series(k, BOND_SEC)

    # 期权 / 风险 图表时序
    for k, s in (("option.vix", "000300.SH"), ("option.iv_skew", "000300.SH"),
                 ("option.pcr_oi", "000300.SH"), ("vol.iv_pctile", "000300.SH"),
                 ("risk.score_all_a", ALLA_SEC), ("risk.cdi", ALLA_SEC),
                 ("risk.struct_score", ALLA_SEC),
                 ("risk.release_composite", RISK_HS3_SEC),
                 ("stock.erp_300", ERP_SEC)):
        try:
            series[(k, s)] = read_series(k, s)
        except Exception:                                 # noqa: BLE001
            series[(k, s)] = []
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
# 「本来就没有这一项」用短横，跟「该有但没取到」的 NA 区分开
DASH = '<span class="dash">—</span>'

# ── 风险标签：任务写数据时自己打的 risk_level，这里只读 ──
_RISK: Dict = {}          # (metric_key, security_id) -> {"level","note"}
_RISK_BY_SEC: Dict = {}   # security_id -> 该标的最高等级


def load_risk(days: int = 7) -> None:
    """build 开始时加载一次风险标签，供卡片/指标打标与排序用。"""
    global _RISK, _RISK_BY_SEC
    try:
        rows = list_risk_points(days=days, limit=1000)
    except Exception:                                        # noqa: BLE001
        rows = []
    rank = {"alert": 3, "risk": 2, "opportunity": 1}
    _RISK = {}
    _RISK_BY_SEC = {}
    for r in rows:
        k = (r.get("metric_key") or "", r.get("security_id") or "")
        lv = r.get("risk_level") or "none"
        # 同一指标多天，保留等级最高的
        if k not in _RISK or rank.get(lv, 0) > rank.get(_RISK[k]["level"], 0):
            _RISK[k] = {"level": lv, "note": r.get("risk_note") or "",
                        "obs": r.get("obs_date") or ""}
        sid = k[1]
        if sid:
            if sid not in _RISK_BY_SEC or rank.get(lv, 0) > rank.get(_RISK_BY_SEC[sid], 0):
                _RISK_BY_SEC[sid] = lv


def risk_of(key: str, sid: str = "") -> Optional[str]:
    r = _RISK.get((key, sid))
    return r["level"] if r else None


def rbadge(key: str, sid: str = "") -> str:
    """指标级风险标记：alert/risk 红 ★，opportunity 绿 ☾，无标签空串。"""
    lv = risk_of(key, sid)
    if lv in ("alert", "risk"):
        return '<b class="bd up" title="%s">★ %s</b>' % (
            html.escape(_RISK[(key, sid)]["note"]), "严重" if lv == "alert" else "风险")
    if lv == "opportunity":
        return '<b class="bd down" title="%s">☾ 机会</b>' % html.escape(
            _RISK[(key, sid)]["note"])
    return ""


def sec_risk(sid: str) -> str:
    return _RISK_BY_SEC.get(sid, "none")


def sec_risk_rank(sid: str) -> int:
    return {"alert": 3, "risk": 2, "opportunity": 1}.get(sec_risk(sid), 0)


def any_val(latest: Dict, pairs) -> bool:
    """pairs=[(key, sec), ...] 里任意一个有值 → True。

    板块级「有没有数据」判定用。整块都没值就整块不渲染，
    不留一排 NA（主人 2026-11-25：没数据就不显示，要好看）。
    """
    for _k, _s in pairs:
        if v(latest, _k, _s) is not None:
            return True
    return False


def num(vv, dec=2, unit="", cls=""):
    if vv is None:
        return NA
    s = ("%." + str(dec) + "f") % vv
    if s.lstrip("-").strip("0.") == "" and s.startswith("-"):
        s = s[1:]                    # -0.0 → 0.0，别让负零难看
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


def kvl(label, note, val_html):
    """长文本指标：值另起一行，不跟 label 挤在同一行。

    顶部判断、漂移建议、GEX 告警这类是整句话，用 kvh 会挤成一坨。
    """
    return ('<div class="kv kv-long"><span class="k">%s%s</span>'
            '<span class="v">%s</span></div>'
            % (html.escape(label),
               ('<span class="sm">%s</span>' % html.escape(note)) if note else "",
               val_html))


def _kv_smart(label, note, latest, key, sid, dec=0, unit=""):
    """按内容自动选布局：数值 → 并排；短文本 → 并排；长文本 → 另起一行。

    没值也没文本返回空串（主人 2026-11-25：没数据就不显示，别摆 NA）。
    """
    vv = v(latest, key, sid)
    if vv is not None:
        return kvh('%s <span class="sm">%s</span>' % (label, note) if note
                   else html.escape(label), num(vv, dec, unit))
    tt = t(latest, key, sid)
    if not tt:
        return ""
    _s = str(tt).strip()
    _h = '<span class="tx">%s</span>' % html.escape(_s)
    if len(_s) > 10:
        return kvl(label, note, _h)
    return kvh('%s <span class="sm">%s</span>' % (label, note) if note
               else html.escape(label), _h)


def risk_bar(score: Optional[float]) -> str:
    """风险综合分的进度条：分越高越危险（跟缠论评分的语义相反）。

    仅适用于 0~100 分口径（如 risk.daily_composite）；
    risk.score_all_a / struct_score / volume_score / extreme_score 是
    -1~1 的小数分，不能直接用本函数，否则永远显示「低风险」。
    """
    if score is None:
        return '<div class="bar-wrap">%s</div>' % DASH
    p = max(0.0, min(100.0, float(score)))
    if p >= 60:
        col, lab = "#ff4d5e", "高风险"
    elif p >= 40:
        col, lab = "#e0a33e", "中风险"
    else:
        col, lab = "#22c55e", "低风险"
    return ('<div class="bar-wrap"><div class="bar">'
            '<i style="width:%.1f%%;background:%s"></i><u style="left:50%%"></u>'
            '</div><div class="bar-lab">%s %.2f/100</div></div>'
            % (p, col, lab, p))


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


def _n_valid(series_rows) -> int:
    """有效点位数（quality=ok 且有数值）——与 chart_payload 的判定保持一致。"""
    return sum(1 for r in (series_rows or [])
               if r.get("quality") == "ok" and r.get("value_num") is not None)


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

    # 该标的下任一指标带风险标签 → 卡片头打标（标签由任务侧写入，这里只读）
    _sr = sec_risk(sid)
    if _sr in ("alert", "risk", "opportunity"):
        _ico = "☾ 机会" if _sr == "opportunity" else ("★ 严重" if _sr == "alert" else "★ 风险")
        _cls = "down" if _sr == "opportunity" else "up"
        badges.append('<b class="bd %s">%s</b>' % (_cls, _ico))

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
    ]
    # 没取到的项直接不显示（主人 2026-11-25：没数据就别显示，别摆一排 NA）
    lis = "".join(kv(k, val) for k, val in snap if val != NA)

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
            '只显示已按契约写入 tech 指标仓的数据，未接入就不显示、'
            '不拿别处的旧值顶替。</p>'
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
            '未接入的项直接不显示，不摆 NA、不拿旧值顶替。</p>'
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
.tx{color:#c8d2e0}
.kv-long{flex-direction:column;align-items:flex-start;gap:3px}
.kv-long .k{white-space:normal}
.kv-long .v{text-align:left;line-height:1.6;font-family:inherit;font-size:12.5px;
color:#c8d2e0;white-space:normal}
.opt-tab{width:100%;font-size:12.5px}
.opt-tab th{font-weight:500;color:#7c8798;text-align:right;padding:6px 8px;border-bottom:1px solid #232b36;white-space:nowrap}
.opt-tab th:first-child,.opt-tab td.nm{text-align:left}
.opt-tab td{text-align:right;padding:6px 8px;border-bottom:1px solid #1b222b;color:#c8d2e0;white-space:nowrap}
.opt-tab td.nm{color:#e6edf6;font-weight:500}
.opt-tab tbody tr:last-child td{border-bottom:0}
.dash{color:#3d4652}
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
/* ══ 告警 / 机会（只展示，不判断）══ */
.alerts{margin:14px 0 18px;border:1px solid var(--line);border-radius:12px;
  background:var(--card);overflow:hidden}
.al-hd{display:flex;align-items:center;gap:16px;padding:10px 16px;
  border-bottom:1px solid var(--line);background:var(--card2);font-size:13px}
.al-hd .t{font-weight:700;letter-spacing:.2px}
.al-hd .t.red{color:var(--up)}
.al-hd .t.green{color:var(--down)}
.al-hd .t.mute{color:var(--tx3);font-weight:600}
.al-hd .date{margin-left:auto;color:var(--tx3);font-size:12px}
.al-list{padding:5px 0;max-height:264px;overflow:auto}
.al-item{display:flex;align-items:baseline;gap:10px;padding:6px 16px;font-size:13px;
  border-left:3px solid transparent}
.al-item.alert{border-left-color:var(--up);background:rgba(255,77,94,.06)}
.al-item.risk{border-left-color:var(--up)}
.al-item.opportunity{border-left-color:var(--down);background:rgba(34,197,94,.05)}
.al-item .lv{font-size:11px;flex:none;padding:1px 6px;border-radius:4px;background:var(--card2);color:var(--tx2)}
.al-item.alert .lv{background:rgba(255,77,94,.18);color:var(--up);font-weight:700}
.al-item.risk .lv{background:rgba(255,77,94,.12);color:var(--up)}
.al-item.opportunity .lv{background:rgba(34,197,94,.15);color:var(--down)}
.al-item .val{font-variant-numeric:tabular-nums;color:var(--tx);flex:none;font-weight:600}
.al-item .note{color:var(--tx3);font-size:12px;flex:1;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;margin-left:8px}
.al-item .ico{font-size:13px;line-height:1;flex:none}
.al-item.alert .ico{color:var(--up)}
.al-item.opportunity .ico{color:var(--down)}
.al-item .src{color:var(--tx3);font-size:11.5px;flex:none;min-width:132px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.al-item .ti{color:var(--tx);font-weight:600;flex:none}
.al-item .dt{color:var(--tx2);font-size:12px}
.al-empty{padding:12px 16px;color:var(--tx3);font-size:12.5px}

/* ══ 股债性价比（股票栏专属）══ */
.erp-card{grid-column:1/-1}
.erp-card .idx-body{padding:0}
.erp-card .kvs{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}

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
# ════════════════════════════════════════════════════════════════
# 告警 / 机会（只展示，判断在各任务侧）
# ════════════════════════════════════════════════════════════════

ALERT_DAYS = 7

# 三档标签：alert 最上（任务侧已发邮件），其次 risk，最后 opportunity
_LEVEL_META = {
    "alert":       ("★", "red",   "严重"),
    "risk":        ("★", "red",   "风险"),
    "opportunity": ("☾", "green", "机会"),
}
_LEVEL_ORDER = ("alert", "risk", "opportunity")


def render_alerts(days: int = ALERT_DAYS) -> str:
    """渲染「严重 / 风险 / 机会」条。

    数据来自 ``metric_points.risk_level`` —— 每个任务写数据时自己打的标签。
    这里**不做任何判断**：不重算阈值、不筛标的、不合并同类项，
    只按标签分色、按等级排序原样展示。

    排序固定为 alert → risk → opportunity（风险永远在最上面）。
    """
    try:
        rows = list_risk_points(days=days, limit=300)
        cnt = count_alerts(days=days)
    except Exception:                                        # noqa: BLE001
        rows, cnt = [], {}

    if not rows:
        return ""            # 没有带标签的结论 → 整块不渲染

    def _row(r: Dict) -> str:
        lv = r.get("risk_level") or "none"
        ico, _cls, lv_cn = _LEVEL_META.get(lv, ("·", "flat", lv))
        key = r.get("metric_key") or ""
        mdef = REGISTRY.get(key)
        nm = html.escape(mdef.name if mdef else key)
        sid = r.get("security_id") or ""
        sid_txt = html.escape(security_name(sid) or sid) if sid else ""
        note = html.escape(str(r.get("risk_note") or ""))
        val = r.get("value_num")
        if val is None:
            vtxt = html.escape(str(r.get("value_text") or ""))
        else:
            unit = (mdef.unit if mdef and mdef.unit else "")
            vtxt = ("%.4g" % val) + (unit if unit and unit != "text" else "")
        head = nm + (" · " + sid_txt if sid_txt else "")
        tail = ('<span class="val">%s</span>' % vtxt) if vtxt else ""
        return ('<div class="al-item %s"><span class="ico">%s</span>'
                '<span class="lv">%s</span>'
                '<span class="ti">%s</span>'
                '%s'
                '<span class="dt">%s</span>'
                '<span class="note">%s</span></div>'
                % (lv, ico, lv_cn, head, tail,
                   html.escape(str(r.get("obs_date") or "")), note))

    ordered = []
    for lv in _LEVEL_ORDER:
        ordered.extend([r for r in rows if (r.get("risk_level") or "") == lv])
    body = "".join(_row(r) for r in ordered)
    if not body:
        body = ('<div class="al-empty">近 %d 天没有任务打出风险或机会标签。'
                '（判断在各任务侧，看板只读标签）</div>' % days)

    today = datetime.now().strftime("%Y-%m-%d")
    n_today = sum(1 for r in rows if r.get("obs_date") == today)

    def _cnt(lv: str) -> str:
        n = cnt.get(lv, 0)
        ico, cls, lv_cn = _LEVEL_META[lv]
        if not n:
            return '<span class="t mute">%s %s 0</span>' % (ico, lv_cn)
        return '<span class="t %s">%s %s %d</span>' % (cls, ico, lv_cn, n)

    return ('<section class="alerts" id="alerts">'
            '<div class="al-hd">%s%s%s<span class="date">共 %d 条 · 今日 %d 条 · 每项只留最新</span></div>'
            '<div class="al-list">%s</div></section>'
            % (_cnt("alert"), _cnt("risk"), _cnt("opportunity"),
               len(rows), n_today, body))

def render_erp_card(latest: Dict, series: Dict, obs: Optional[str]):
    """股债性价比卡（股票栏专属，按用户要求不放宏观栏）。

    股债利差 = 沪深300 盈利收益率 EY − 10Y 国债收益率 BY。
    值越高 = 股票相对债券越便宜。分位越高 = 当前越偏股票便宜。

    返回 (html, chart_script)；无数据时 chart_script 为空串。
    """
    SID = "000300.SH"
    erp = v(latest, "stock.erp_300", SID)
    ey = v(latest, "stock.ey_300", SID)
    pe = v(latest, "stock.pe_300", SID)
    pct = v(latest, "stock.erp_300_pct", SID)

    head = ('<header><div class="ttl"><h3>股债性价比</h3>'
            '<code>%s</code><span class="sm pair">EY − 10Y国债</span></div>'
            '<div class="bds">%s</div></header>' % (SID, ""))
    if erp is None:
        return "", ""        # 无数据 → 整张卡不渲染

    # 分位分档：越高越偏股票便宜（绿），越低越偏债券贵（红）
    if pct is None:
        zone, zcls = "—", "flat"
    elif pct >= 80:
        zone, zcls = "股票很便宜", "down"
    elif pct >= 60:
        zone, zcls = "偏股票", "down"
    elif pct > 40:
        zone, zcls = "中性", "flat"
    elif pct > 20:
        zone, zcls = "偏债券", "up"
    else:
        zone, zcls = "股票偏贵", "up"

    kvs = []
    kvs.append(kv("股债利差",
                  '<span class="%s">%.2f</span> <span class="sm">pp</span>'
                  % (zcls, erp)))
    kvs.append(kv("盈利收益率 EY", num(ey, 2, "%")))
    kvs.append(kv("沪深300 PE", num(pe, 2, "倍")))
    kvs.append(kv("历史分位",
                  '%s <span class="sm %s">%s</span>'
                  % (num(pct, 1, "%"), zcls, zone)))

    rows = series.get(("stock.erp_300", SID)) or []
    blk = ('<div class="blk"><div class="kvs">%s</div>%s</div>'
           % ("".join(kvs),
              chart_block("t_erp_300", rows)))
    script = chart_payload("t_erp_300", rows, "股债利差", "#e0a33e",
                           unit="pp", dec=2)
    body = '<div class="idx-body">%s</div>' % blk
    return ('<section class="idx-card erp-card" data-sec="erp" data-def="1">'
            '%s%s</section>' % (head, body)), script

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
        # 整块没数据 → 整块不显示，不留「取数失败」空壳
        return ""

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
                # 商品没有现货指数，基差天然为空 → 用短横，不当成缺数据
                b_s = DASH
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
            '<b>基差</b>=现货指数−主力期货，只有股指期货有现货，商品基差显示 —。'
            'key 一律用品种主连（<code>AU.SHF</code>），'
            '<code>AU2412</code> / <code>AU0</code> / <code>au</code> 都是它的别名。</p>')

    return ('<section class="idx-card sec" id="futures">'
            '<header><h3>期货品种 <em>主连 / 期限 / 基差</em></h3>'
            '<div class="obs">%s</div></header>'
            '<div class="idx-body">%s%s</div></section>'
            % (html.escape(obs), "".join(blocks), note))


RE_CITY_SUBS = (("北京", "re_bj"), ("上海", "re_sh"))


def _re_report_sections() -> Dict[str, List[str]]:
    """把 realestate_daily 的 report.md 按「## 【城市】」切出各城市条目。"""
    path = os.path.join(HERE, "case", "realestate_daily", "out", "report.md")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    out: Dict[str, List[str]] = {}
    cur = None
    known = dict(RE_CITY_SUBS)
    for raw in lines:
        s = raw.strip()
        if s.startswith("## "):
            name = s[3:].strip()
            cur = None
            if name.startswith("【") and name.endswith("】"):
                name = name[1:-1]
                if name in known:
                    cur = name
                    out.setdefault(cur, [])
            continue
        if cur and s.startswith("- ") and len(s) > 2:
            out[cur].append(s[2:].strip())
    return {k: v for k, v in out.items() if v}


def _re_md_bold(s: str) -> str:
    """行内 **粗体** → <b>，其余按 HTML 转义；未闭合的 ** 原样保留。"""
    parts = html.escape(s).split("**")
    out = []
    for i, p in enumerate(parts):
        if i % 2 == 0:
            out.append(p)
        elif i < len(parts) - 1:
            out.append("<b>%s</b>" % p)
        else:
            out.append("**%s" % p)
    return "".join(out)


def _re_fetch_meta() -> Dict:
    """取 fetch_input.py 落盘的取数元数据（data/fetch_meta.json）。

    只用来判断本期真取了哪些字段（sources.*.ok）；基准日一律从正文的
    行内截止日推，不用 fetched_at。文件缺失不阻断渲染——没跑过自动取数
    的旧部署也得能出页面。
    """
    import json as _json
    p = os.path.join(HERE, "case", "realestate_daily", "data", "fetch_meta.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _re_month_end(y: int, m: int):
    """该月最后一天；月频数据按此对齐，避免年月差把边界日算少一个月。"""
    import calendar as _cal
    import datetime as _dt
    return _dt.date(y, m, _cal.monthrange(y, m)[1])


def _re_add_months(d, n: int):
    """日历月加法（日按月末夹紧），用于「超过 N 个月」的边界判断。"""
    import calendar as _cal
    import datetime as _dt
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return _dt.date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def _re_ref_date(cities: Dict[str, List[str]]):
    """数据基准日：取正文里最新的「截至」日期，不用 fetch_meta.fetched_at。

    抓取日只说明脚本什么时候跑过，数据可能是沿用的旧值；拿它当基准会把
    没取到数的那期也显示成「本期取数」。优先取日频截止日的最大值，没有
    日频时退回最新的月频月末，再没有才用今天，保证页面还能出。
    """
    import datetime as _dt
    import re as _re
    day_dates, mon_dates = [], []
    for items in (cities or {}).values():
        for text in items:
            m = _re.search(r"截至\s*(\d{4})-(\d{2})-(\d{2})", text)
            if m:
                try:
                    day_dates.append(_dt.date(int(m.group(1)), int(m.group(2)),
                                              int(m.group(3))))
                except ValueError:
                    pass
                continue
            m = _re.search(r"截至\s*(\d{4})-(\d{2})(?!\d)", text)
            if m:
                try:
                    mon_dates.append(_re_month_end(int(m.group(1)), int(m.group(2))))
                except ValueError:
                    pass
    if day_dates:
        return max(day_dates)
    if mon_dates:
        return max(mon_dates)
    return _dt.date.today()


_RE_STALE_STYLE = ("font-style:normal;font-size:11px;color:#b03a2e;"
                   "border:1px solid #e6b0aa;border-radius:3px;"
                   "padding:0 5px;margin-left:6px;white-space:nowrap")


def _re_stale(text: str, ref) -> str:
    """按行内「截至 YYYY-MM-DD / YYYY-MM」判断过期，返回标记 HTML。

    日频超 14 天、月频超 2 个月才算过期（月频数据 20 多天前是正常的，
    拿日频阈值套月频会把正常的月度数据全标成过期）。月频按数据月末对齐，
    否则只减年月会在边界日少算一个月；数据日晚于基准日视为异常并显式标出，
    不能当成「新」。没有截止日的行同样标出来——读者无从判断它是什么时候的数。
    「数据基准」是说明行而非数据行，跳过不标。
    """
    import datetime as _dt
    import re as _re
    if text.startswith("数据基准"):
        return ""
    m = _re.search(r"截至\s*(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return ""
        days = (ref - d).days
        if days < 0:
            # 数据日晚于基准日：不是「新」，是日期写错或穿越了，显式标出来
            return '<em style="%s">数据日异常 · 超前 %d 天</em>' % (
                _RE_STALE_STYLE, -days)
        if days <= 14:
            return ""
        return '<em style="%s">未更新 · %d 天前</em>' % (_RE_STALE_STYLE, days)
    m = _re.search(r"截至\s*(\d{4})-(\d{2})(?!\d)", text)
    if m:
        try:
            e = _re_month_end(int(m.group(1)), int(m.group(2)))
        except ValueError:
            return ""
        # 月频按「数据月末 + 2 个月」判过期，比直接减年月少一次边界误差
        if ref > _re_add_months(e, 2):
            # 已过完整月数：基准日当天若是月末就算满月，否则去掉不足一月的零头
            months = (ref.year - e.year) * 12 + (ref.month - e.month)
            if ref < _re_month_end(ref.year, ref.month):
                months -= 1
            return '<em style="%s">未更新 · %d 个月前</em>' % (
                _RE_STALE_STYLE, months)
        return ""
    return '<em style="%s">未标注截止日</em>' % _RE_STALE_STYLE


def render_realestate_section() -> str:
    """地产栏 —— 按城市分两张子卡片：北京 / 上海。

    内容取自 ``case/realestate_daily/out/report.md`` 的「## 【北京】」「## 【上海】」
    两节（每行自带数据截止日期）。自动取数只覆盖汇率/利率/CPI/70 城，网签、
    挂牌、租金仍是人工快照，所以对过期行显式标「未更新」，并在卡片顶部写明
    本期取数日——宁可明说没有，也不让旧数混在新数里冒充最新。
    子页在 sections.json 的 ``realestate.subs`` 里登记，``enabled`` 决定默认显隐。
    整块无数据 → 整块不渲染，与其它板块口径一致。
    """
    cities = _re_report_sections()
    if not cities:
        return ""
    subs: Dict[str, Dict] = {}
    for s in (load_sections(os.path.join(HERE, "sections.json")).get("sections") or []):
        if s.get("key") == "realestate":
            subs = {x.get("key"): x for x in (s.get("subs") or [])}
            break

    meta = _re_fetch_meta()
    ref = _re_ref_date(cities)
    fetched = str(meta.get("fetched_at") or "")[:10]
    sources = meta.get("sources") if isinstance(meta.get("sources"), dict) else {}
    fetched_ok = any(isinstance(v, dict) and v.get("ok")
                     and (v.get("got") or v.get("updated") or v.get("added"))
                     for v in sources.values())
    fetched_s = ("；本期自动取数 %s" % html.escape(fetched)
                 if fetched_ok and fetched else "")
    head = ('<div style="font-size:12px;color:#666;line-height:1.7;margin:0 0 10px 0">'
            '自动更新：汇率 / 利率 / CPI / 70 城房价（数据截至 %s%s）；'
            '网签、挂牌量价、租金暂无自动源，标「未更新」的行沿用人工快照，'
            '以行内截止日为准。</div>'
            % (ref.isoformat(), fetched_s))

    cards = []
    for city, key in RE_CITY_SUBS:
        items = cities.get(city) or []
        if not items:
            continue
        d = 1 if (subs.get(key) or {}).get("enabled", True) else 0
        lis = "".join('<li style="margin:0 0 10px 0">%s%s</li>'
                      % (_re_md_bold(x), _re_stale(x, ref))
                      for x in items)
        cards.append(
            '<section class="idx-card sec" id="%s" data-sec="%s" data-def="%d">'
            '<header><h3>%s <em>地产 · 城市基础数据</em></h3></header>'
            '<div class="idx-body"><div class="blk">%s'
            '<ul style="margin:0;padding-left:18px;line-height:1.75">%s</ul>'
            '</div></div></section>'
            % (key, key, d, html.escape(city), head, lis))
    return "".join(cards)


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
    if not any_val(latest, [(k, MACRO_SEC) for _n, k, _d, _u, _x in cn]
                          + [(k, GLOBAL_SEC) for _n, k, _d, _u, _x in gl]):
        return "", []        # 整块无数据 → 不渲染

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
        _n = _n_valid(series.get(_k))
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
    if not any_val(latest, [(k, LIQ_SEC) for _n, k, _d, _u, _x in rate + spread]
                          + [(k, MARGIN_SEC) for _n, k, _d, _u, _x in mg]):
        return "", []        # 整块无数据 → 不渲染

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
        _n = _n_valid(series.get(_k))
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


def render_bond_section(latest: Dict, series: Dict, obs: Optional[str]) -> tuple:
    """债券与理财：利率曲线 / 期限结构 / 信用利差 / 理财替代锚。

    数据口径：国债曲线来自中债估值（8 期限），信用债走中债企业债 AAA 与
    同业存单 AAA 曲线，理财用**货币基金 7 日年化**做类理财收益锚 ——
    银行理财不披露日频净值，这里不推算、不冒称理财业绩基准。
    """
    curve = [
        ("国债 3M", "bond.govt_3m", 3, "%", "短端资金面"),
        ("国债 6M", "bond.govt_6m", 3, "%", ""),
        ("国债 1Y", "bond.govt_1y", 3, "%", "期限利差基准"),
        ("国债 3Y", "bond.govt_3y", 3, "%", "信用利差基准"),
        ("国债 5Y", "bond.govt_5y", 3, "%", "配置主力"),
        ("国债 7Y", "bond.govt_7y", 3, "%", ""),
        ("国债 10Y", "bond.govt_10y", 3, "%", "债市定价之锚"),
        ("国债 30Y", "bond.govt_30y", 3, "%", "超长端"),
    ]
    term = [
        ("期限利差 10Y-1Y", "bond.spread_10y1y", 1, "bp", "走阔=曲线变陡"),
        ("期限利差 30Y-10Y", "bond.spread_30y10y", 1, "bp", "超长端溢价"),
    ]
    credit = [
        ("企业债 AAA 3Y", "bond.corp_aaa_3y", 3, "%", "中债估值曲线"),
        ("信用利差 AAA 3Y", "bond.corp_spread_3y", 1, "bp", "走阔=风险溢价上升"),
        ("同业存单 AAA 1Y", "bond.cd_aaa_1y", 3, "%", "银行负债成本"),
        ("存单利差 1Y", "bond.cd_spread_1y", 1, "bp", "边际负债压力"),
    ]
    wealth = [
        ("货基 7 日年化 · 中位数", "bond.mmf_7d_median", 3, "%", "类理财收益锚"),
        ("25 分位", "bond.mmf_7d_p25", 3, "%", "头部货基"),
        ("75 分位", "bond.mmf_7d_p75", 3, "%", "尾部货基"),
        ("存单 − 货基价差", "bond.wealth_gap", 1, "bp", "锁久期的收益补偿"),
    ]

    if not any_val(latest, [(k, BOND_SEC) for _n, k, _d, _u, _x
                            in curve + term + credit + wealth]):
        return "", []        # 整块无数据 → 不渲染

    curve_html = "".join(kvh('%s <span class="sm">%s</span>' % (n, note),
                             num(v(latest, k, BOND_SEC), d, u))
                         for n, k, d, u, note in curve)

    _shp = (g(latest, "bond.curve_shape", BOND_SEC) or {}).get("value_text") or "NA"
    _pct = num(v(latest, "bond.govt_10y_pct", BOND_SEC), 1, "%")
    term_html = "".join(kvh('%s <span class="sm">%s</span>' % (n, note),
                            num(v(latest, k, BOND_SEC), d, u))
                        for n, k, d, u, note in term)
    term_html += kvh('曲线形态 <span class="sm">10Y 方向 × 利差方向</span>',
                     '<b>%s</b>' % html.escape(str(_shp)))
    term_html += kvh('10Y 历史分位 <span class="sm">近 500 交易日</span>', _pct)

    credit_html = "".join(kvh('%s <span class="sm">%s</span>' % (n, note),
                              num(v(latest, k, BOND_SEC), d, u))
                          for n, k, d, u, note in credit)
    wealth_html = "".join(kvh('%s <span class="sm">%s</span>' % (n, note),
                              num(v(latest, k, BOND_SEC), d, u))
                          for n, k, d, u, note in wealth)

    charts = [
        ("bd_10y", ("bond.govt_10y", BOND_SEC), "国债 10Y", "#4a9eff", "%", 3),
        ("bd_sp", ("bond.spread_10y1y", BOND_SEC), "期限利差 10Y-1Y", "#e0a33e", "bp", 1),
        ("bd_cs", ("bond.corp_spread_3y", BOND_SEC), "信用利差 AAA 3Y", "#ff4d5e", "bp", 1),
    ]
    ch_html = ""
    _live = []
    for _c in charts:
        _cid, _k, _nm = _c[0], _c[1], _c[2]
        _n = _n_valid(series.get(_k))
        if _n < 3:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart na-box">数据点不足（%d 个），暂不绘图</div></div>'
                        % (_nm, _n))
        else:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart" id="%s"></div></div>' % (_nm, _cid))
            _live.append(_c)
    charts = _live

    return ('<section class="idx-card sec" id="bond">'
            '<header><h3>债券与理财 <em>利率曲线 · 信用利差 · 类理财收益</em></h3>'
            '<div class="obs">日频</div></header>'
            '<div class="idx-body">'
            '<div class="blk"><h4>国债收益率曲线（中债估值）</h4>'
            '<div class="kvs">%s</div></div>'
            '<div class="blk"><h4>期限结构与形态</h4><div class="kvs">%s</div></div>'
            '<div class="blk"><h4>信用债与信用利差</h4><div class="kvs">%s</div>'
            '<p class="note">信用利差 = 同期限信用债 − 同期限国债。走阔通常对应'
            '信用风险溢价上升、风险偏好下降。</p></div>'
            '<div class="blk"><h4>理财替代锚（货币基金口径）</h4>'
            '<div class="kvs">%s</div>'
            '<p class="note">银行理财不披露日频净值，公开接口没有统一的理财收益率序列。'
            '这里用<b>货币基金 7 日年化</b>做「类理财收益」的可得锚，'
            '只陈述数据本身，<b>不是银行理财业绩基准</b>，两者口径不同不可直接比较。</p></div>'
            '%s</div></section>' % (curve_html, term_html, credit_html,
                                    wealth_html, ch_html)), charts


# ════════════════════════════════════════════════════════════════
# 期权 / 风险 两个 Tab
#
# 主人 2026-11-25：标签除了股票/期货/地产/宏观/流动性，还可以往上扩。
# 这里扩「期权」和「风险」两块，数据全部来自指标仓 option.* / vol.* /
# bottom.* / risk.* / struct.* / style.* / midline.*，
# 页面只读不判断，风险等级一律用指标自带的标签（rbadge）。
# ════════════════════════════════════════════════════════════════

# 期权标的顺序：先股指后 ETF，跟期权任务自己的口径一致
OPT_IDS = ["000300.SH", "000905.SH", "000852.SH", "399006.SZ",
           "000688.SH", "932000.CSI"]

# 波动率曲面状态机的中文名（任务侧给的是 safe/medium/high）
_VOL_ST = {"safe": "安全", "medium": "关注", "high": "风险"}


def _tv(latest: Dict, key: str, sid: str, dec: int = 0, unit: str = "") -> str:
    """有数值就显示数值，纯文本就显示文本，都没有才显示短横。

    评级、状态、建议这类指标只落 text 不落 value_num，
    不能一律当 NA 处理（主人 2026-11-25：没数据才不显示，有文本就得显示）。
    """
    vv = v(latest, key, sid)
    if vv is not None:
        return num(vv, dec, unit)
    tt = t(latest, key, sid)
    if tt:
        return '<span class="tx">%s</span>' % html.escape(str(tt))
    return DASH


def render_option_section(latest: Dict, series: Dict,
                          obs: Optional[str]) -> tuple:
    """期权栏 —— 各标的 IV / 偏度 / PCR，加波动率曲面与顶底判断。"""
    cols = [
        ("平值 IV", "option.atm_iv", 2, "%"),
        ("25D 偏度", "option.iv_skew", 2, ""),
        ("持仓 PCR", "option.pcr_oi", 2, ""),
        ("成交 PCR", "option.pcr_vol", 2, ""),
        ("VIX", "option.vix", 2, ""),
        ("IV 分位", "vol.iv_pctile", 0, "%"),
    ]
    pairs = [(k, s) for s in OPT_IDS for _n, k, _d, _u in cols]
    pairs += [("vol.surface_state", s) for s in OPT_IDS]
    pairs += [("bottom.position", ""), ("bottom.triggered", ""),
              ("bottom.prediction", ""), ("vol.surface_risk_count", "")]
    if not any_val(latest, pairs):
        return "", []        # 整块无数据 → 不渲染

    # ── 逐标的明细表 ──
    # 列过滤：所有标的都空的列直接不显示（主人 2026-11-25：没数据就不显示）
    live_cols = [c for c in cols
                 if any(v(latest, c[1], s) is not None for s in OPT_IDS)]
    # 行过滤：这一行所有列都空、曲面状态也空 → 整行不显示
    live_rows = [s for s in OPT_IDS
                 if any(v(latest, k, s) is not None for _n, k, _d, _u in live_cols)
                 or v(latest, "vol.surface_state", s) is not None]
    head = "".join("<th>%s</th>" % n for n, _k, _d, _u in live_cols)
    rows = []
    for s in live_rows:
        cells = []
        for _n, k, d, u in live_cols:
            _vv = v(latest, k, s)
            # 这个标的天然没有这项（比如中证2000 没有对应期权品种）→ 用 —，不摆 NA
            cells.append("<td>%s</td>" % (num(_vv, d, u) if _vv is not None else DASH))
        _st = v(latest, "vol.surface_state", s)
        _stt = t(latest, "vol.surface_state", s)
        _lab = _VOL_ST.get(str(_stt or "").strip().lower(), "")
        if not _lab and _st is None:
            st_s = DASH
        else:
            st_s = '<span class="tx">%s</span>' % html.escape(
                _lab or str(_stt or _st or ""))
        rows.append('<tr><td class="nm">%s%s</td>%s<td>%s</td></tr>'
                    % (html.escape(security_name(s)),
                       rbadge("vol.surface_state", s), "".join(cells), st_s))
    tab = ('<table class="mod-tab opt-tab"><thead><tr><th>标的</th>%s'
           '<th>曲面状态</th></tr></thead><tbody>%s</tbody></table>'
           % (head, "".join(rows)))

    # ── 波动率曲面汇总（没值的项直接跳过）──
    surf_html = "".join(x for x in (
        _kv_smart("风险标的数", "medium/high 标的个数",
                  latest, "vol.surface_risk_count", "", 0, " 个"),
        _kv_smart("曲面告警", "", latest, "vol.alert", "", 0, ""),
    ) if x)

    # ── 杨康平顶底 ──
    bot_html = "".join(x for x in (
        _kv_smart("仓位建议", "11 红线 + 7 顶部信号", latest, "bottom.position", ""),
        _kv_smart("底部红线触发", "越多越接近底部", latest, "bottom.triggered", "", 0, " 条"),
        _kv_smart("顶部判断", "", latest, "bottom.prediction", ""),
    ) if x)

    # ── 图表 ──
    charts = [
        ("op_vix", ("option.vix", "000300.SH"), "沪深300 VIX", "#4a9eff", "", 2),
        ("op_skew", ("option.iv_skew", "000300.SH"), "沪深300 25D偏度",
         "#e0a33e", "", 2),
        ("op_pcr", ("option.pcr_oi", "000300.SH"), "沪深300 持仓PCR",
         "#9b7bff", "", 2),
        ("op_pct", ("vol.iv_pctile", "000300.SH"), "沪深300 IV分位",
         "#22c55e", "%", 0),
    ]
    ch_html = ""
    _live = []
    for _c in charts:
        _cid, _k, _nm = _c[0], _c[1], _c[2]
        _n = _n_valid(series.get(_k))
        if _n >= 3:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart" id="%s"></div></div>' % (_nm, _cid))
            _live.append(_c)
    charts = _live

    return ('<section class="idx-card sec" id="option">'
            '<header><h3>期权 · 波动率 <em>IV · 偏度 · PCR · 波动率曲面</em></h3>'
            '<div class="obs">日频</div></header>'
            '<div class="idx-body">'
            '<div class="blk"><h4>各标的期权指标</h4>%s'
            '<p class="note">PCR &gt; 1 通常对应认沽占比高、避险情绪偏重；'
            '25D 偏度越大说明虚值认沽越贵。曲面状态由任务自己的'
            '<code>analyze_surface_risk()</code> 判定，页面只翻译不重算。'
            '表中 — = 该标的无对应期权品种（如中证2000）。</p></div>'
            '<div class="blk"><h4>波动率曲面</h4><div class="kvs">%s</div></div>'
            '<div class="blk"><h4>杨康平顶底</h4><div class="kvs">%s</div></div>'
            '%s</div></section>' % (tab, surf_html, bot_html, ch_html)), charts


def render_risk_section(latest: Dict, series: Dict,
                        obs: Optional[str]) -> tuple:
    """风险栏 —— 全 A 风险评分、结构健康、风险释放、风格漂移、中线偏好。"""
    ALLA = "881001.WI"       # 万得全 A：全市场口径
    IDX = "000852.SH"        # 中证1000：多数结构指标落这个标的
    HS3 = "000300.SH"        # 沪深300：风险释放走这个标的

    score = [
        ("全A综合风险分", "risk.score_all_a", ALLA, 1, "", "越高越该谨慎"),
        ("结构风险分", "risk.struct_score", ALLA, 1, "", "结构层面拥挤度"),
        ("量能情绪风险分", "risk.volume_score", ALLA, 1, "", "成交与情绪过热"),
        ("极端风险分", "risk.extreme_score", ALLA, 1, "", "尾部事件概率"),
        ("每日风险综合评分", "risk.daily_composite", "", 2, "分", "V5.2 日频"),
        ("风险评级", "risk.grade", ALLA, 0, "", "任务自带分级"),
    ]
    health = [
        ("CDI 结构健康度", "risk.cdi", ALLA, 2, "", "越高结构越健康"),
        ("结构因子 AbsFactor", "risk.abs_factor", ALLA, 4, "", "多空力量净额"),
        ("成交额集中度", "risk.concentration", IDX, 1, "%", "中证1000 口径"),
        ("up50 分位", "risk.up50", IDX, 1, "%", "上行空间分位"),
    ]
    release = [
        ("风险释放度", "risk.release_composite", HS3, 1, "分", "越高释放越充分"),
        ("释放 VIX", "risk.release_vix", HS3, 2, "", "沪深300 口径"),
        ("释放 PCR", "risk.release_pcr", HS3, 2, "", "沪深300 口径"),
        ("释放 skew", "risk.release_skew", HS3, 3, "", "沪深300 口径"),
    ]
    style = [
        ("风格漂移 z", "style.drift_z", "", 2, "", "z_opt 越大越偏"),
        ("漂移状态", "style.drift_state", "", 0, "", "任务自己的判定"),
        ("漂移建议", "style.drift_advice", "", 0, "", "任务自己的建议"),
    ]
    mid = [
        ("中线 up50 分位", "midline.up50", IDX, 1, "%", "≥90% 风险 / ≤10% 机会"),
        ("CFC 结构因子", "struct.cfc", ALLA, 4, "", "资金流结构"),
        ("CFC 信号", "struct.cfc_signal", ALLA, 0, "", "任务自己的信号"),
        ("中线 GEX 告警", "midline.gex_alert", "", 0, "", "Flip 只认现价 ±20% 内"),
    ]

    all_groups = score + health + release + style + mid
    if not any_val(latest, [(k, s) for _n, k, s, _d, _u, _x in all_groups]):
        # 数值全空时还要看文本类指标有没有落库
        if not any(t(latest, k, s) for _n, k, s, _d, _u, _x in all_groups):
            return "", []

    def _grp(items) -> str:
        # 没值的项直接跳过，不留 NA/DASH（主人 2026-11-25）
        return "".join(x for x in (
            _kv_smart(n, note, latest, k, s, d, u)
            for n, k, s, d, u, note in items) if x)

    score_html = _grp(score)
    health_html = _grp(health)
    release_html = _grp(release)
    style_html = _grp(style)
    mid_html = _grp(mid)

    # 每日风险综合评分单独画一条进度条，比数字直观
    _dc = v(latest, "risk.daily_composite", "")
    bar_html = ('<div class="blk"><h4>每日风险综合评分</h4>%s'
                '<p class="note">V5.2 任务自己的综合分，<b>分越高风险越大</b>；'
                '评级与穿越事件见上方告警区。</p></div>'
                % risk_bar(_dc))

    charts = [
        ("rk_all", ("risk.score_all_a", ALLA), "全A综合风险分", "#ff4d5e", "", 1),
        ("rk_cdi", ("risk.cdi", ALLA), "CDI 结构健康度", "#22c55e", "", 2),
        ("rk_rel", ("risk.release_composite", HS3), "风险释放度", "#4a9eff", "", 1),
        ("rk_str", ("risk.struct_score", ALLA), "结构风险分", "#e0a33e", "", 1),
    ]
    ch_html = ""
    _live = []
    for _c in charts:
        _cid, _k, _nm = _c[0], _c[1], _c[2]
        _n = _n_valid(series.get(_k))
        if _n >= 3:
            ch_html += ('<div class="blk"><h4>%s</h4>'
                        '<div class="chart" id="%s"></div></div>' % (_nm, _cid))
            _live.append(_c)
    charts = _live

    return ('<section class="idx-card sec" id="risk">'
            '<header><h3>风险 · 结构 <em>综合评分 · CDI · 风险释放 · 风格漂移</em></h3>'
            '<div class="obs">日频</div></header>'
            '<div class="idx-body">'
            '<div class="blk"><h4>风险评分</h4><div class="kvs">%s</div>'
            '<p class="note">四类分项都来自任务自己算的分数，'
            '页面只读不重算阈值。</p></div>'
            '%s'
            '<div class="blk"><h4>结构健康度</h4><div class="kvs">%s</div>'
            '<p class="note">CDI 越高说明结构越健康；AbsFactor 是多空力量的净额，'
            '正值偏多、负值偏空。</p></div>'
            '<div class="blk"><h4>风险释放</h4><div class="kvs">%s</div>'
            '<p class="note">释放度越高说明这波风险释放得越充分，'
            '不等于马上见底，要配合结构看。</p></div>'
            '<div class="blk"><h4>风格漂移</h4><div class="kvs">%s</div></div>'
            '<div class="blk"><h4>中线偏好与结构因子</h4><div class="kvs">%s</div></div>'
            '%s</div></section>'
            % (score_html, bar_html, health_html, release_html,
               style_html, mid_html, ch_html)), charts


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
        if sv and _n_valid(sv) >= 3:
            cid = "rw_%s_%d" % (key, len(charts))
            # series 的 key 是 (metric_key, security_id) 元组，不是单个指标名
            charts.append((cid, (k, sc), nm, it.get("color") or "#4a9eff", unit, dec))
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


def _case_workers() -> list:
    """case/*/task.json 的任务（dumate 分支）：metrics 回填后当 worker 渲染。"""
    lib = os.path.join(HERE, "case", "_lib")
    try:
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import case_registry
        return case_registry.as_workers(os.path.join(HERE, "case"))
    except Exception:                                         # noqa: BLE001
        return []


def _case_modules() -> list:
    """case 任务清单（含 metrics 还没回填的），用于模块管理页。"""
    lib = os.path.join(HERE, "case", "_lib")
    try:
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import case_registry
        return [m for m in case_registry.as_modules(os.path.join(HERE, "case"))
                if not m.get("_errors")]
    except Exception:                                         # noqa: BLE001
        return []


def _all_modules(cfg: Dict) -> list:
    """内置 + 已批准的注册 Worker + case 任务，key 唯一，内置优先。"""
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
    for w in _case_modules():
        k = w.get("key")
        if k and k not in seen:
            w = dict(w)
            w["source"] = "case"
            w["metrics"] = w.get("metrics") or []
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
    api_port = (cfg.get("api") or {}).get("port") or 8767
    console_url = "http://&lt;主机&gt;:%s/" % api_port

    def _fresh(m):
        """数据新鲜度：case 任务用自己申报的 metrics，内置/注册按 domain 反查。"""
        doms = m.get("domains") or []
        own = ([it.get("key") for it in (m.get("metrics") or []) if it.get("key")]
               if m.get("source") == "case" else None)
        if own is not None and not doms:
            doms = ["case"]
        cells = []
        for d in doms:
            keys = own if own is not None else \
                [k for k, vv in _reg.REGISTRY.items() if vv.domain == d]
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
                  "stopped": "down", "interrupted": "mid"}.get(lstat, "sm")
        st_txt = {"ok": "成功", "timeout": "超时", "fail": "失败",
                  "stopped": "已停", "interrupted": "中断"}.get(lstat, "—")
        if ms.get("queued"):
            st_cls, st_txt = "mid", "排队"
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
               _fresh(m)))

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
            '不用系统 crontab。控制台：<code>%s</code>'
            '（同源，可直接启停 / 立即跑 / 审批注册）。<br>'
            '外部模块接入：<code>POST /api/register</code> 带注册密钥提交，'
            '后台批准后自动出现在对应大类下并开始定时更新。'
            '%s</p>'
            '</div></div></section>'
            % (len(mods), len(tree), "".join(tree), "".join(rows), console_url,
               ('<br><b class="mid">有 %d 个注册申请待批准</b>' % len(pend))
               if pend else ""))


def build(out_path: str, asof: Optional[str] = None) -> str:
    latest, series, obs = load_all(asof)
    st = stats()
    # 先加载风险标签：卡片打标与排序都要用
    load_risk(ALERT_DAYS)

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

    def wrap(k: str, body: str, reg: str = "",
             obs_d: Optional[str] = None) -> str:
        """板块外壳。body 和 reg 都空 → 整块不渲染。

        主人 2026-11-25：没数据的模块就别显示，少一两个没啥，页面要好看。
        """
        inner = (body or "") + (reg or "")
        if not inner.strip():
            return ""
        return ('<div class="sec-wrap" data-sec="%s" data-def="1"%s>%s'
                '<div class="sec-grid">%s</div></div>'
                % (k, sty(k), sh(k, obs_d), inner))

    cards = []
    # 卡片自动排序：带风险标签的标的最前（alert > risk > opportunity），其余保持原序
    _sid_sorted = sorted(INDEX_ORDER, key=lambda x: -sec_risk_rank(x))
    for sid in _sid_sorted:
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
    bond_sec, bond_charts = render_bond_section(latest, series, obs)
    mod_sec = render_modules_panel(os.path.join(HERE, "modules.json"), st)

    # ── 外部注册的 model：按所属大类分组 ──
    KNOWN_TABS = ("stock", "futures", "realestate", "macro", "liquidity",
                  "bond", "option", "risk")
    reg_in = {t: [] for t in KNOWN_TABS}
    reg_new = []
    reg_charts = []
    _rws = _load_workers(True) + _case_workers()
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
    # 期权 / 风险 两个 Tab（主人 2026-11-25 批准扩的标签）
    opt_sec, opt_charts = render_option_section(latest, series, obs)
    risk_sec, risk_charts = render_risk_section(latest, series, obs)

    for cid, key, nm, color, unit, dec in bond_charts:
        scripts.append(chart_payload(cid, series.get(key), nm, color, unit=unit, dec=dec))
    for cid, key, nm, color, unit, dec in (opt_charts + risk_charts):
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

    erp_html, erp_script = render_erp_card(latest, series, obs)
    if erp_script:
        scripts.append(erp_script)

    # 导航只给「真渲染出来」的板块出链接（空板块不显示，链接也别留）
    _NAV = [("stock", "股票 · 宽基"), ("futures", "期货 · 基差"),
            ("realestate", "地产"), ("macro", "宏观"),
            ("liquidity", "流动性"), ("bond", "债券 · 理财"),
            ("option", "期权 · 波动率"), ("risk", "风险 · 结构"),
            ("modules", "模块管理")]
    _has = {
        "stock": bool((erp_html or "").strip() or cards or reg_in.get("stock")),
        "futures": bool((futures_sec or "").strip() or reg_in.get("futures")),
        "realestate": bool((re_sec or "").strip() or reg_in.get("realestate")),
        "macro": bool((macro_sec or "").strip() or reg_in.get("macro")),
        "liquidity": bool((liq_sec or "").strip() or reg_in.get("liquidity")),
        "bond": bool((bond_sec or "").strip() or reg_in.get("bond")),
        "option": bool((opt_sec or "").strip() or reg_in.get("option")),
        "risk": bool((risk_sec or "").strip() or reg_in.get("risk")),
        "modules": True,
    }
    jump = "".join('<a href="#%s">%s</a>' % (k, t) for k, t in _NAV
                   if _has.get(k))
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
    bond_obs = _obs_of([(k, BOND_SEC) for k in BOND_KEYS])
    opt_obs = _obs_of([("option.vix", s) for s in OPT_IDS]
                      + [("vol.surface_state", s) for s in OPT_IDS])
    risk_obs = _obs_of([("risk.score_all_a", "881001.WI"),
                        ("risk.daily_composite", "")])

    alerts_html = render_alerts()
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Dashboard · 股票 / 期货 / 宏观 / 流动性 / 债券理财</title>
<style>{CSS}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>Stock Dashboard<small>股票 · 期货 · 宏观 · 流动性 · 债券理财</small></h1>
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

{alerts_html}

<div class="layout">
  <div class="grid-idx">

    {wrap("stock", erp_html + "".join(cards), reg_in['stock'], obs)}

    {wrap("futures", futures_sec, reg_in['futures'], None)}

    {wrap("realestate", re_sec, reg_in['realestate'], None)}

    {wrap("macro", macro_sec, reg_in['macro'], macro_obs)}

    {wrap("liquidity", liq_sec, reg_in['liquidity'], liq_obs)}

    {wrap("bond", bond_sec, reg_in['bond'], bond_obs)}

    {wrap("option", opt_sec, reg_in['option'], opt_obs)}

    {wrap("risk", risk_sec, reg_in['risk'], risk_obs)}

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
  整块没数据的板块直接不显示，不插值、不用上期值顶替。
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
        <code>curl -X POST -H 'X-SD-KEY: {adm_key}' -H 'Content-Type: application/json' \\<br>
        &nbsp;&nbsp;-d '{{"tab":"macro","key":"my_ind","title":"我的指标","sched":{{"at":"21:30","weekdays":[1,2,3,4,5]}},"cmd":"python3 /path/x.py"}}' \\<br>
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
