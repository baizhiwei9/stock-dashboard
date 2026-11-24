#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ot_unified_daily.py — 最优传输统一日报（靠岸 + 漂移状态 + 资金）
==============================================================
把 Max OT「崩→稳→起」靠岸判断与 OT 日报（W1 漂移状态 + 资金/行业评分 + 桑基图）
合并为单一入口，取数全部经 DataHub。

每天输出：
  1) 靠岸判断：六指数是否跌到头、大小盘分布、耦合（共振/尾部依赖）
  2) OT 漂移状态：16 只宽基/行业 ETF 的 W1 漂移 + 80 日动量趋势优先
  3) 资金：ETF 份额变化（DataHub etf_share）+ 成交额/规模 → 宽基与行业排名
  4) 图：靠岸监测图、耦合图、宽基/行业桑基图
  5) 飞书交互卡片

用法：
    python ot_unified_daily.py [--date YYYY-MM-DD] [--no-push] [--no-share]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ot_core as oc

# 绘图与飞书工具（凭据全部走环境变量，不进仓库）
import ot_render as odr

ETFS = [
    ('sh510300', '沪深300', '宽基'), ('sh510500', '中证500', '宽基'), ('sh512100', '中证1000', '宽基'),
    ('sh588000', '科创50', '宽基'), ('sh510050', '上证50', '宽基'), ('sz159915', '创业板', '宽基'),
    ('sh512010', '医药', '行业'), ('sh512480', '半导体', '行业'), ('sh515030', '新能源车', '行业'),
    ('sh515790', '光伏', '行业'), ('sh512660', '军工', '行业'), ('sh512800', '银行', '行业'),
    ('sh512000', '券商', '行业'), ('sh515050', '5G', '行业'), ('sh512690', '酒', '行业'),
    ('sz159992', '创新药', '行业'),
]
LOOKBACK = 5      # 份额/资金观察窗口
MONTH_DAYS = 22   # 近一个月交易日数（净申购 ASCII 图）
SHARE_LB = 40     # 份额回溯自然日


# ============================================================================
# 取数（全部 DataHub）
# ============================================================================

def fetch_etf_all(start, end, etfs=ETFS):
    data, last_dates, failed = {}, {}, []
    idx_all = []
    for code, name, _ in etfs:
        try:
            df = oc.get_etf_df(code, start, end)
            if len(df) > 120:
                data[name] = df
                last_dates[name] = df.index[-1]
                idx_all.append(df.index)
            else:
                failed.append(f"{name}({len(df)}行)")
        except Exception as e:
            failed.append(f"{name}({str(e)[:40]})")
    if not data:
        return {}, pd.DatetimeIndex([]), {}, failed
    dates = None
    for df in data.values():
        dates = df.index if dates is None else dates.intersection(df.index)
    for name in data:
        data[name] = data[name].loc[dates].copy()
    all_dates = idx_all[0]
    for ix in idx_all[1:]:
        all_dates = all_dates.union(ix)
    return data, dates, last_dates, failed


def fetch_shares(etfs=ETFS):
    """ETF 份额（DataHub etf_share）；失败标的记 None。"""
    out, failed = {}, []
    for code, name, _ in etfs:
        try:
            sh = oc.get_etf_share_df(code, SHARE_LB)
        except Exception:
            sh = pd.DataFrame()
        if sh is None or len(sh) < 2:
            failed.append(name)
            continue
        out[name] = sh["fund_share"]
    return out, failed


def money_metrics(data, shares, names, lb=LOOKBACK):
    """份额口径资金指标：5 日净申购额、份额变化率、规模、日均成交额/规模、换手率。"""
    out = {}
    for name in names:
        kl = data.get(name)
        sh = shares.get(name)
        if kl is None or sh is None or len(sh) < 2:
            out[name] = {"ok": False}
            continue
        close = kl["close"]
        # 份额按日对齐（份额通常滞后一日，用 ffill 对齐后取最近 lb+1 个有效点）
        s = sh.reindex(kl.index.union(sh.index)).ffill().reindex(kl.index)
        s = s.dropna()
        if len(s) < 2:
            out[name] = {"ok": False}
            continue
        # 末行份额跳变 >50% 多为数据源未定稿/口径异常（非份额折算，折算伴随价格折算），回退前一有效日
        share_flag = ""
        if len(s) >= 2 and s.iloc[-2] > 0 and abs(s.iloc[-1] / s.iloc[-2] - 1) > 0.5:
            s = s.iloc[:-1]
            share_flag = "末行份额异常已回退"
        if len(s) < 2:
            out[name] = {"ok": False}
            continue
        c = close.reindex(s.index).ffill()
        k = min(lb, len(s) - 1)
        d_share = float(s.iloc[-1] - s.iloc[-1 - k])
        chg = float(s.iloc[-1] / s.iloc[-1 - k] - 1) * 100
        mv_yi = float(s.iloc[-1] * c.iloc[-1]) / 1e8
        net_yi = float(d_share * c.iloc[-1]) / 1e8
        # 近一个月每日净申购额（亿）= 当日份额变化 × 当日收盘价；供 ASCII 图使用
        k_m = min(MONTH_DAYS, len(s) - 1)
        _ds = s.diff().iloc[-k_m:]
        _c = c.iloc[-k_m:]
        net_daily = (np.asarray(_ds.values, dtype=float) * np.asarray(_c.values, dtype=float)) / 1e8
        # 成交额仅留档：DataHub 各数据源 vol（手/股）与 amount（千元/万元/元）单位不一致，
# 换手率无法可靠计算，故不输出换手，避免量纲错误（份额、规模与份额变化率不受影响）。
        win = kl.index[-lb:]
        vol = kl["vol"].reindex(win).astype(float) if "vol" in kl.columns else pd.Series(np.nan, index=win)
        c_win = close.reindex(win).ffill()
        amt_day_yi = float(np.nansum(vol.values * c_win.values)) / max(len(win), 1) / 1e8
        turn = np.nan
        out[name] = {"ok": True, "net_yi": net_yi, "share_chg": chg, "mv_yi": mv_yi,
                     "amt_day_yi": amt_day_yi, "turn": turn,
                     "net_daily": net_daily, "net_dates": [str(x.date()) for x in _ds.index],
                     "net_month": float(np.nansum(net_daily)),
                     "share_date": str(s.index[-1].date())}
    return out


# ============================================================================
# 文本组装
# ============================================================================

def landing_lines(snap, coup):
    L = []
    L.append(f"【一、靠岸判断｜{snap['as_of']}】")
    L.append(f"状态：{snap['state']}（连续 {snap['state_streak']} 日）→ **{snap['verdict']}**")
    L.append(f"大小盘：大盘 {snap['big_pct']:.0f}｜中盘 {snap['mid_pct']:.0f}｜小盘 {snap['small_pct']:.0f}"
             f"（小盘-大盘 {snap['big_small_gap']:+.0f}，正值=小盘更未靠岸）")
    L.append(f"联合 W1 {snap['joint_w1']:.3f}（分位 {snap['joint_pct']:.0f}%）｜"
             f"谱共振 {coup['resonance_now']:.3f}｜平均 λ_L {coup['lamL_now']:.3f}｜平均成对 W1 {coup['w1_now']:.3f}")
    L.append("")
    for c in snap["codes"]:
        d = snap["detail"][c]
        cap = {"big": "大盘", "mid": "中盘", "small": "小盘"}[d["cap"]]
        q = d["w1_pct"]
        dm = d.get("dir_mean")
        if q < 40:
            mark, tag = "🟢🌙", "已靠岸"
        elif q < 60:
            mark, tag = "🟡●", "靠岸中"
        elif dm is not None and dm > 0:
            mark, tag = "🟢↗", "上行偏离（不是下跌风险）"
        else:
            mark, tag = "🔴⭐", "未靠岸"
        L.append(f"  {mark} {cap}·{d['name']}: W1 {d['w1']:.4f}｜分位 {q:.0f}（{tag}）｜5日ΔW1 {d['w1_chg5']:+.4f}｜"
                 f"回撤 {d['dd_long']:+.1f}%｜20日 {d['ret20']:+.2f}%｜MA40{'↑' if d['above_ma40'] else '↓'}")
    ind = snap.get("industry")
    if ind and ind.get("rows"):
        L.append(f"申万一级行业（{len(ind['rows'])} 个，{ind['as_of']}）：已靠岸 {ind['n_low']}｜靠岸中 {ind['n_mid']}"
                 f"｜上行偏离 {ind.get('n_up', 0)}｜下跌未靠岸 {ind.get('n_down', ind.get('n_high', 0))}"
                 f"｜中位分位 {ind['median_pct']:.0f}")
        top_up = [r for r in ind["rows"] if (r.get("dir_mean") or 0) > 0 and r["pct"] >= 60][:4]
        top_dn = [r for r in ind["rows"] if (r.get("dir_mean") or 0) <= 0 and r["pct"] >= 60][-4:]
        if top_up:
            L.append("  上行偏离（涨出来的，不是风险）：" + "、".join(f"{r['name']}({r['pct']:.0f})" for r in top_up))
        if top_dn:
            L.append("  下跌未靠岸：" + "、".join(f"{r['name']}({r['pct']:.0f})" for r in reversed(top_dn)))
    L.append("")
    return L


def ot_lines(data, dates, names_wide, names_ind):
    L = []
    L.append("【二、OT 漂移状态（W1 漂移 + 80 日动量趋势；状态识别，非买卖信号）】")
    sig = {}
    for name in names_wide + names_ind:
        s = oc.drift_signal(data[name]["close"])
        sig[name] = s
        d = '↑' if s['dir'] > 0 else ('↓' if s['dir'] < 0 else '-')
        # 【P1-2】只描述分布状态，不再用「看多/看空」这种交易指令措辞
        if s['signal'].startswith('漂移上行'):
            mk = "▲ "
        elif s['signal'].startswith('漂移下行'):
            mk = "▼ "
        else:
            mk = "— "
        L.append(f"{mk}{name} W1 {s['w1']:.4f} 方向{d} 5日{s['ret5']:+.2f}% 20日{s['ret20']:+.2f}% "
                 f"80日{s['mom80']:+.2f}% MA40{'↑' if s['ma40'] else '↓'} → {s['signal']}")
    L.append("")
    return L, sig


NET_UP, NET_DN = 5, 3   # ASCII 净申购图：向上 5 格、向下 3 格

SHORT_NAME = {"沪深300": "沪300", "中证500": "中500", "中证1000": "千指",
              "科创50": "科创", "上证50": "上证", "创业板": "创业",
              "医药": "医药", "半导体": "半导", "新能源车": "新能", "光伏": "光伏",
              "军工": "军工", "银行": "银行", "券商": "券商", "5G": "5G",
              "酒": "酒", "创新药": "创药"}

SPARK = "▁▂▃▄▅▆▇█"   # 8 级高度：越高=当日净申购越多


def net_sparkline(vals):
    """把一个月（约 22 交易日）的日度净申购额连成一条 ASCII/Unicode 折线。

    - 每个交易日只占 1 个字符位，用 ▁▂▃▄▅▆▇█ 八级高度表现高低（按该标的月内最大正值自适应）；
    - 当日为负：线断掉，直接在该位置写出金额数字（单位亿，整数）；
    - 当日无份额数据或为 0：写 `·`。
    """
    def _num(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    ns = [_num(v) for v in vals]
    mx = max([v for v in ns if v is not None and v > 0], default=0.0)
    out = []
    for v in ns:
        if v is None or v == 0:
            out.append("·")
        elif v < 0:
            out.append(f"{v:.0f}")   # 负数：断线，直接插数字
        else:
            lvl = int(round(v / mx * 7)) if mx > 0 else 0
            out.append(SPARK[max(0, min(7, lvl))])
    return "".join(out)


def ascii_net_panel(vals, up=4, cell=4, step=None):
    """近一月日度净申购 ASCII 块图：正值为向上柱（高度=金额），负值在零轴下直接写数字。

    - 柱高按该标的月度最大净申购额自适应（每格金额见标题）；
    - 负净申购当天不画向下柱，直接在零轴下一行写该日金额（整数，单位亿）；
    - 零轴行 ─ 表示当日有数据，· 表示当日无份额数据。
    """
    def _num(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    ns = [_num(v) for v in vals]
    if step is None:
        step = _nice_step(max([abs(v) for v in ns if v is not None], default=0.0), up)

    def h(v):
        if v is None or v <= 0:
            return 0
        return min(up, max(1, int(round(abs(v) / step))))

    pad = " " * cell + " "
    rows = []
    for lvl in range(up, 0, -1):
        rows.append("".join(("█" * cell + " ") if (v is not None and h(v) >= lvl) else pad for v in ns))
    rows.append("".join((("─" * cell + " ") if (v is not None and v != 0) else ("·" * cell + " ")) for v in ns))
    rows.append("".join((f"{v:>{cell}.0f} " if (v is not None and v < 0) else pad) for v in ns))
    return rows, step


TRI_POS = ["▵", "▴", "△", "▲", "🔺"]   # 级别 1（最小）→ 5（最大），正值向上
TRI_NEG = ["▿", "▾", "▽", "▼", "🔻"]   # 负值向下


def triangle_strip(items, unit="亿"):
    """一行横排三角形表示各标的净申购额。

    items: [(简称, 金额亿)]，按给定顺序从左到右排列。
    规则：绝对值五等分 → 5 级三角（▵▴△▲🔺 / ▿▾▽▼🔻），0 用 │。
    返回三行：三角形行（紧凑不空格）/ 简称行 / 数值行（带正负号与单位）。
    """
    vals = [abs(float(v)) for _, v in items]
    mx = max(vals) if vals else 0.0
    step = mx / 5 if mx > 0 else 1.0
    r1, r2, r3 = [], [], []
    for name, v in items:
        v = float(v)
        if abs(v) < 1e-9:
            ch = "│"
        else:
            lvl = min(5, max(1, int(math.ceil(abs(v) / step))))
            ch = TRI_POS[lvl - 1] if v > 0 else TRI_NEG[lvl - 1]
        r1.append(ch)
        r2.append(name)
        r3.append(f"{v:+.1f}{unit}")
    return ["".join(r1), " ".join(r2), " ".join(r3)]





def _nice_step(mx, up):
    """把每只标的的月度最大额换算成"好看"的每格金额，让柱高吃满 5 格。"""
    if mx <= 0:
        return 1.0
    raw = mx / up
    for c in (0.5, 1, 2, 5, 10, 20, 30, 50, 100, 200, 500):
        if raw <= c:
            return float(c)
    return float(int(raw // 500 + 1) * 500)


def ascii_net_chart(vals, up=NET_UP, dn=NET_DN, step=None):
    """日度净申购额 ASCII 柱图（单位亿元）：柱高=金额，向上=净申购、向下=净赎回。

    - 每格金额按该标的月度最大额自适应（返回 step），柱高吃满图幅，形状差异不会被压平；
    - 当日无份额数据（NaN）或净申购恰为 0 → 零轴行打一个「.」；
    - 空白处一律留空格，不做填充，柱子高度才看得出来。
    """
    def _num(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    ns = [_num(v) for v in vals]
    if step is None:
        step = _nice_step(max([abs(v) for v in ns if v is not None], default=0.0), up)

    def h(v, k):
        if v is None or v == 0:
            return 0
        return min(k, max(1, int(round(abs(v) / step))))

    rows = []
    for lvl in range(up, 0, -1):
        rows.append("".join("█" if (v is not None and v > 0 and h(v, up) >= lvl) else " " for v in ns))
    rows.append("".join("." if (v is None or v == 0) else "─" for v in ns))
    for lvl in range(1, dn + 1):
        rows.append("".join("█" if (v is not None and v < 0 and h(v, dn) >= lvl) else " " for v in ns))
    return rows, step


def money_lines(money, names_wide, names_ind):
    L = []
    L.append(f"【三、资金（ETF 份额口径，份额最新 {money.get('_date', '?')}）】")
    L.append("说明：份额变化 × 收盘价 ≈ 净申购额；规模=份额×收盘价（份额口径，非主力资金）。")
    L.append("")
    L.append(f"宽基 近一个月（{MONTH_DAYS} 交易日）净申购走势（每格一个交易日，旧→新）：")
    L.append("```")
    for n in names_wide:
        m = money.get(n) or {}
        if not m.get("ok"):
            continue
        _nd = m.get("net_daily")
        nd = list(_nd) if _nd is not None else []
        _m = money[n]
        L.append(f"{SHORT_NAME.get(n, n[:4]):<5} {net_sparkline(nd)}")
    L.append("```")
    L.append("▁▂▃▄▅▆▇█ 越高=当日净申购越多（满格＝该标的月内最大单日净申购）；负数处线断掉、直接写成金额（亿）；· = 当日无份额数据或为 0。")
    L.append("行业申购额按用户要求取消，不再输出（行业只看靠岸与漂移状态）。")
    return L


def verdict_lines(snap, coup, money, names_wide, names_ind):
    """把靠岸 + 漂移状态 + 资金合成一句状态结论（状态识别，不含买卖指令）。"""
    w = [(n, money[n]["net_yi"]) for n in names_wide if money.get(n, {}).get("ok")]
    inflow = [n for n, v in sorted(w, key=lambda x: x[1], reverse=True)[:2]] if w else []
    L = ["【四、综合结论】"]
    if snap["state"] == "🟩 企稳可起":
        head = "靠岸基本完成，可看右侧确认"
    elif snap["state"] == "🟨 靠岸中":
        head = "靠岸进行中，接近但未完成"
    elif snap["state"] == "ℹ️ 大盘先稳，小盘滞后":
        head = "大盘先靠岸、小盘未跌完，结构分化"
    elif snap["state"] == "🟧 底部摩擦":
        head = "底部摩擦，跌势钝化但未确认跌完"
    elif snap["state"] == "🟥 共振崩塌":
        head = "仍在共振下跌，未靠岸"
    else:
        head = snap["verdict"]
    L.append(f"靠岸：{head}；大盘分位 {snap['big_pct']:.0f} vs 小盘 {snap['small_pct']:.0f}。")
    L.append(f"六指数齐跌黏性 λ_L {coup['lamL_now']:.3f}"
             f"（{'抱团下跌未解除' if coup['lamL_now'] > 0.6 else '共振压力不高'}；越接近 1 越是一起跌）。")
    if inflow:
        L.append(f"资金：宽基净申购额（份额变化×价格）靠前为 {'、'.join(inflow)}（ETF 份额口径，非主力资金）。")
    L.append("纪律：靠岸是状态识别，不是买入信号；需等小盘 W1 分位回落至 40 以下 + 联合 W1 连降 + 不再创新低。")
    return L


# ============================================================================
# 主流程
# ============================================================================

def money_only(end, etf_start, push=True):
    """只出宽基净申购额（三角形横排）这一段，数据仍走 DataHub。"""
    print(f"=== 宽基净申购额 {end}（DataHub）===")
    wide_etfs = [e for e in ETFS if e[2] == "宽基"]
    data, dates, last_dates, failed = fetch_etf_all(etf_start, end, etfs=wide_etfs)
    if len(dates) == 0:
        raise SystemExit("[DATA-GATE] ETF 日线为空，拒绝出报告")
    shares, sh_failed = fetch_shares(etfs=wide_etfs)
    names_wide = [n for _, n, c in ETFS if c == "宽基" and n in data]
    money = {"_date": "无"}
    if shares:
        money = money_metrics(data, shares, names_wide)
        dates_ok = [v.get("share_date") for v in money.values()
                    if isinstance(v, dict) and v.get("ok")]
        money["_date"] = max(dates_ok) if dates_ok else "无"
    text = "\n".join(money_lines(money, names_wide, []))
    print(text)
    if not push:
        return text
    if not odr.WEBHOOK_URL:
        print("[飞书] 未配置 FEISHU_WEBHOOK，跳过推送")
        return text
    title = f"宽基ETF近月净申购额 | {money.get('_date', '?')} | 来自 DoMate"
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": text}}]
    card = {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": elements}
    import requests
    r = requests.post(odr.WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=20)
    print(f"[飞书] {r.json()}")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-share", action="store_true", help="跳过份额取数（调试用）")
    ap.add_argument("--no-alert", action="store_true", help="跳过靠岸切换提醒与缠论图邮件")
    ap.add_argument("--force-alert", action="store_true", help="无视切换，强制发一封靠岸汇总邮件")
    ap.add_argument("--money-only", action="store_true", help="只出宽基净申购额三角形横排这一段并推飞书")
    args = ap.parse_args()

    end = args.date or date.today().isoformat()
    start = args.start or (pd.Timestamp(end) - pd.Timedelta(days=540)).strftime("%Y-%m-%d")
    etf_start = (pd.Timestamp(end) - pd.Timedelta(days=820)).strftime("%Y-%m-%d")

    if args.money_only:
        return money_only(end, etf_start, push=not args.no_push)

    print(f"=== OT 统一日报 {end}（DataHub）===")
    snap = oc.build_landing_snapshot(start=start, end=end)
    coup = oc.build_coupling_snapshot(start=start, end=end)
    try:
        # 【P1-3 修复】复用指数层已校验过的稳态窗口 + 同一分位口径，避免两层口径分裂
        snap["industry"] = oc.build_industry_landing(
            start=start, end=end, steady=snap.get("steady"),
            pct_mode=snap.get("window", {}).get("pct_mode"))
    except Exception as e:
        snap["industry"] = None
        print(f"[行业] 取数失败：{e}")

    print("取 ETF 日线（DataHub）...")
    data, dates, last_dates, failed = fetch_etf_all(etf_start, end)
    if len(dates) == 0:
        print("[DATA-GATE] ETF 日线为空，拒绝出报告")
        return
    names_wide = [n for _, n, c in ETFS if c == "宽基" and n in data]
    names_ind = [n for _, n, c in ETFS if c == "行业" and n in data]
    print(f"  成功 {len(data)}/{len(ETFS)}  最新 {dates[-1].date()}  失败 {failed}")

    money = {}
    share_failed = []
    if not args.no_share:
        print("取 ETF 份额（DataHub etf_share）...")
        shares, share_failed = fetch_shares([(c, n, k) for c, n, k in ETFS if n in data])
        money = money_metrics(data, shares, names_wide + names_ind)
        dates_ok = [v.get("share_date") for v in money.values() if v.get("ok")]
        money["_date"] = max(dates_ok) if dates_ok else "无"
        print(f"  份额成功 {sum(1 for v in money.values() if isinstance(v, dict) and v.get('ok'))}"
              f"/{len(names_wide)+len(names_ind)}  份额日 {money['_date']}")

    sig_lines, sig = ot_lines(data, dates, names_wide, names_ind)
    L = []
    L.append(f"**最优传输 OT 统一日报 | {dates[-1].date()} | 来自 DoMate**")
    L.append("")
    if failed or share_failed:
        L.append(f"【数据完整性】ETF 日线失败：{','.join(failed) or '无'}；份额缺失：{','.join(share_failed) or '无'}")
        L.append("")
    L += landing_lines(snap, coup)
    L += sig_lines
    if money:
        L += money_lines(money, names_wide, names_ind)
    L += verdict_lines(snap, coup, money, names_wide, names_ind)
    text = "\n".join(L)

    # 图：靠岸 + 耦合 + 份额资金条形图
    raw = oc.get_index_batch(snap["codes"], start, end)
    raw_close = {c: raw[c]["close"] for c in snap["codes"]}
    import ot_landing_report as olr
    p_land, _ = olr.plot_landing(snap, raw_close)
    p_coup, _ = olr.plot_coupling(coup)
    p_badge = olr.plot_status_badges(snap, raw_close)
    p_ind = olr.plot_industry_status(snap["industry"]) if snap.get("industry") else None

    p_bars = []  # 份额条形图已下线：宽基净申购改 ASCII 月度图，行业申购额取消

    print(text)
    print(f"[图] {p_land}\n[图] {p_coup}")

    # 靠岸切换提醒 + 缠论日线图（宽基一张、行业一张），引用 techanly-summary
    p_chan = []
    if not args.no_alert:
        try:
            import ot_alert as oa
            res = oa.run(send=True, force=args.force_alert, snap=snap, ind=snap.get("industry"))
            p_chan = res.get("charts") or []
        except Exception as e:
            print(f"[提醒] 失败：{e}")

    # 飞书
    if not args.no_push:
        tok = odr.get_token()
        keys = []
        if tok:
            for p in ([p_land, p_badge] + ([p_ind] if p_ind else []) + p_bars + p_chan):
                k = odr.upload(p, tok)
                if k:
                    keys.append(k)
        title = f"最优传输OT统一日报 | {dates[-1].date()} | 靠岸：{snap['state']} | 来自 DoMate"
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": text}}]
        for k in keys:
            elements.append({"tag": "img", "img_key": k, "alt": {"tag": "plain_text", "content": "图"}})
        card = {"config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
                "elements": elements}
        import requests
        r = requests.post(odr.WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=20)
        print(f"[飞书] {r.json()}")


if __name__ == "__main__":
    main()