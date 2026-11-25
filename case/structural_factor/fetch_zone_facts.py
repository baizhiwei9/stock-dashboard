#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将军四区间日报 · 事实包取数（单命令，输出 <=45 行紧凑文本）

职责：只取数与做确定性计算，不做区间判断。
数据源：
  - iFind（finance_api.py）：A股指数/ETF/美股日线
  - 新浪期货 hq.sinajs.cn：中金所四品种当月/次月/下季合约收盘
  - 东方财富 push2ex：涨停池/跌停池/炸板池（家数、连板高度、封板率）
缺口一律输出 NA(原因)，不用旧值顶替。

用法：python3 fetch_zone_facts.py [--date YYYY-MM-DD] [--json out.json]
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "zone_facts_state.json")
FIN = os.environ.get("FIN_COMMONS_DIR") or (
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/plugins/official"
    "/financial-investment-plugin/skills/commons"
)
# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
HUB_ROOT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier"
HUB_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps"
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

API = os.path.join(FIN, "ifind-data-service", "finance_api.py")

IDX = [
    ("000001.SH", "上证综指"), ("399001.SZ", "深证成指"), ("000300.SH", "沪深300"),
    ("000016.SH", "上证50"), ("000905.SH", "中证500"), ("000852.SH", "中证1000"),
    ("000688.SH", "科创50"), ("399006.SZ", "创业板指"),
]
FUT = [("IF", "000300.SH"), ("IH", "000016.SH"), ("IC", "000905.SH"), ("IM", "000852.SH")]
US_TECH = {
    "anchor": [("NVDA.O", "英伟达")],
    "spend": [("MSFT.O", "微软"), ("META.O", "Meta"), ("GOOGL.O", "谷歌"), ("AMZN.O", "亚马逊"), ("CRWV.O", "CoreWeave")],
    "earn": [("AMD.O", "AMD"), ("AVGO.O", "博通"), ("TSM.N", "台积电"), ("MU.O", "美光"), ("WDC.O", "西数")],
}
NVDA_LOW = 210.18  # 美股锚前低（历史判定基准，可在 zone_facts_state.json 覆盖）

gaps = []


def note_gap(item, why):
    gaps.append(f"{item}:{why}")


def finance(args, timeout=180):
    p = subprocess.run([sys.executable, API] + args, capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"status": "parse_error", "rows": [], "message": (p.stdout or p.stderr)[:200]}


def trading_days(end, back=40):
    """交易日历：DataHub 股票模块（CN.MARKET.CAL.TRADE）。"""
    start = end - dt.timedelta(days=back)
    try:
        from data_hub import edb
        cal = edb.get_dataset("CN.MARKET.CAL.TRADE",
                              start_date=start.strftime("%Y%m%d"),
                              end_date=end.strftime("%Y%m%d"))
        raw = sorted({r for r in cal["cal_date"].astype(str)}) if cal is not None and len(cal) else []
        days = [f"{r[:4]}-{r[4:6]}-{r[6:8]}" if len(r) == 8 else r for r in raw]
    except Exception as e:
        days = []
        note_gap("交易日历", f"DataHub 日历不可用: {str(e)[:80]}")
    return days


def hub_index_rows(codes, start, end):
    """DataHub 股票模块指数日线 → iFind rows 同构 [{time, close, change_ratio, amount}]。"""
    from data_hub.collectors.stocks import get_index_daily
    out = {}
    for code in codes:
        try:
            df = get_index_daily(ts_code=code,
                                 start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""))
        except Exception as e:
            print(f"  [dataHub] {code} 取数失败: {str(e)[:80]}")
            continue
        rows = []
        prev = None
        for _, r in df.sort_values("trade_date").iterrows():
            d8 = str(r["trade_date"]).replace("-", "")
            close = float(r["close"]) if pd.notna(r.get("close")) else None
            chg = (close / prev - 1) * 100 if (close is not None and prev) else None
            rows.append({"time": f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}",
                         "close": close, "change_ratio": chg,
                         "amount": float(r["amount"]) * 1000 if pd.notna(r.get("amount")) else None})
            prev = close
        out[code] = rows
    return out


def pct(series, n):
    """最近 n 日累计涨跌（%）"""
    v = [x for x in series if x is not None]
    if len(v) < n + 1:
        return None
    return (v[-1] / v[-1 - n] - 1) * 100


def vol_rank(closes, win=20, look=252):
    """20 日年化波动率在近 look 日内的分位（%）"""
    v = [x for x in closes if x is not None]
    if len(v) < look * 0.6 or len(v) < win + 30:
        return None
    rets = [(v[i] / v[i - 1] - 1) for i in range(1, len(v))]
    vols = []
    for i in range(win, len(rets) + 1):
        w = rets[i - win:i]
        m = sum(w) / len(w)
        var = sum((x - m) ** 2 for x in w) / (len(w) - 1)
        vols.append((var ** 0.5) * (252 ** 0.5) * 100)
    if not vols:
        return None
    cur, hist = vols[-1], vols[-look:]
    return sum(1 for x in hist if x <= cur) / len(hist) * 100, cur


def fut_contract(prod, data_date, offset):
    """当月/次月/下季合约代码：offset 0/1/2"""
    y, m = data_date.year, data_date.month
    if offset == 0:
        return f"{prod}{y % 100:02d}{m:02d}"
    if offset == 1:
        m2 = m + 1
        y2 = y + (m2 > 12)
        m2 = 1 if m2 > 12 else m2
        return f"{prod}{y2 % 100:02d}{m2:02d}"
    for mm in (3, 6, 9, 12):
        if mm > m:
            return f"{prod}{y % 100:02d}{mm:02d}"
    return f"{prod}{(y + 1) % 100:02d}03"


def sina_kline(code):
    """期货合约日K：DataHub 期货模块（返回 [{"d": "YYYY-MM-DD", "c": close}] 升序）。"""
    from data_hub.collectors.futures import get_futures_contract_daily
    start = (dt.date.today() - dt.timedelta(days=90)).strftime("%Y%m%d")
    end = dt.date.today().strftime("%Y%m%d")
    try:
        df = get_futures_contract_daily(contract=str(code), start_date=start, end_date=end)
    except Exception as ex:
        note_gap(f"期货{code}日K", f"DataHub {type(ex).__name__}")
        return []
    if df is None or len(df) == 0:
        note_gap(f"期货{code}日K", "DataHub 无数据")
        return []
    dcol = "trade_date" if "trade_date" in df.columns else "date"
    bars = []
    for _, r in df.sort_values(dcol).iterrows():
        d8 = str(r[dcol]).replace("-", "")
        try:
            bars.append({"d": f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}", "c": float(r["close"])})
        except Exception:
            continue
    return bars


def em_pool(kind, date_str):
    """涨跌停池：DataHub 数据集 CN.MARKET.LIMIT.DETAIL（替代东财 push2ex 页面）。

    返回 (家数, 池明细[{"n":名称,"lbc":连板数,"hybk":行业}])，与旧结构保持同构。
    """
    from data_hub import edb
    d8 = str(date_str).replace("-", "")
    try:
        df = edb.get_dataset("CN.MARKET.LIMIT.DETAIL", trade_date=d8)
    except Exception as ex:
        note_gap(f"{kind}池", f"DataHub 涨跌停明细不可用 {type(ex).__name__}")
        return None, []
    if df is None or len(df) == 0:
        note_gap(f"{kind}池", "当日无涨跌停明细（休市或缺数据）")
        return 0, []
    flag = {"zt": "U", "dt": "D", "zb": "Z"}[kind]
    sub = df[df["limit"] == flag]
    pool = []
    for _, r in sub.iterrows():
        lbc = r.get("limit_times")
        pool.append({
            "n": r.get("name"),
            "lbc": int(lbc) if pd.notna(lbc) else 1,
            "hybk": r.get("industry"),
        })
    return int(len(sub)), pool


# QVIX 序列统一走 options_utility（期权库口径），不再直连第三方现成 QVIX 站点
OPTIONS_LIB = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/options_utility"
OPTIONS_CN_DATA = ("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/"
                   "ses_gffe5ea0b23728ffegpx6GMQLqwA5ek/data")
QVIX_TARGETS = ("300ETF", "500ETF", "创业板", "科创50")


def _iso(d):
    """YYYYMMDD -> YYYY-MM-DD，与事实包其余日期字段同口径。"""
    t = str(d).replace("-", "")
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}" if len(t) == 8 else str(d)


def qvix_series():
    """QVIX 序列（options_utility 口径）：返回 {名称: [(YYYY-MM-DD, qvix)]}。"""
    if OPTIONS_LIB not in sys.path:
        sys.path.insert(0, OPTIONS_LIB)
    # 期权库数据目录可被环境变量覆盖；必须在 import config.paths 之前设置
    os.environ.setdefault("OPTIONS_CN_DATA_DIR", OPTIONS_CN_DATA)
    try:
        from calc.cn.forecast import qvix_series as _lib_qvix
    except Exception as ex:
        note_gap("QVIX", f"options_utility 不可用 {type(ex).__name__}")
        return {}
    out = {}
    for name in QVIX_TARGETS:
        try:
            ser = _lib_qvix(name) or []
        except Exception as ex:
            note_gap(f"QVIX-{name}", f"读取失败 {type(ex).__name__}")
            continue
        if ser:
            out[name] = [(_iso(d), float(v)) for d, v in ser]
        else:
            note_gap(f"QVIX-{name}", "期权库无序列")
    return out


def qvix_pct(series, look=252):
    """最新值与近 look 日分位（%）"""
    v = series[-look:]
    cur = series[-1][1]
    rank = sum(1 for _, x in v if x <= cur) / len(v) * 100
    return cur, rank, series[-1][0]


def em_boards(topn=30):
    """概念板块榜：DataHub 股票模块 get_sector_flow（替代东财 clist 接口）。

    返回 [(名称, 涨幅%, 主力净流入亿, 领涨股)]，按涨幅降序取前 topn。
    """
    try:
        from data_hub.collectors.stocks import get_sector_flow
        df = get_sector_flow(sector_type="concept")
    except Exception as ex:
        note_gap("概念板块榜", f"DataHub 板块资金流不可用 {type(ex).__name__}")
        return []
    if df is None or len(df) == 0:
        note_gap("概念板块榜", "DataHub 板块资金流为空")
        return []
    out = []
    for _, r in df.iterrows():
        name = r.get("sector_name")
        if not name:
            continue
        pct = r.get("change_pct")
        flow = r.get("main_net_inflow")
        try:
            pct = float(pct)
        except Exception:
            continue
        try:
            flow = float(flow) / 1e8 if flow is not None and pd.notna(flow) else None
        except Exception:
            flow = None
        out.append((name, pct, flow, r.get("leading_stock") or "-"))
    out.sort(key=lambda x: -x[1])
    return out[:topn]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="数据日（缺省=今天之前最近 A 股交易日）")
    ap.add_argument("--json", help="事实包 JSON 落盘路径")
    a = ap.parse_args()

    today = dt.date.today()
    days = trading_days(today)
    if not days:                      # 日历不可用：按工作日推断，并在缺口里声明
        note_gap("交易日历", "iFind 日历不可用，按工作日推断")
        d0, wk = today, []
        while len(wk) < 40:
            if d0.weekday() < 5:
                wk.append(d0.isoformat())
            d0 -= dt.timedelta(days=1)
        days = sorted(wk)
    is_trading_today = today.isoformat() in days
    if a.date:
        data_date = dt.date.fromisoformat(a.date)
    elif is_trading_today and dt.datetime.now().time() >= dt.time(15, 30):
        data_date = today                 # 已收盘，当日数据可用
    else:
        before = [d for d in days if d < today.isoformat()]
        data_date = dt.date.fromisoformat(before[-1]) if before else today - dt.timedelta(days=1)
    facts = {"data_date": data_date.isoformat(), "is_trading_today": is_trading_today,
             "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": {}, "gaps": gaps}
    L = []
    L.append(f"ZONE_FACTS v1 | DATA_DATE={data_date} | TODAY_TRADING={'YES' if is_trading_today else 'NO'}")

    # ---------- A 指数与量能 ----------
    start = (data_date - dt.timedelta(days=430)).isoformat()
    by = hub_index_rows([c for c, _ in IDX] + ["399001.SZ"], start, data_date.isoformat())
    if not any(by.values()):
        note_gap("指数行情", "DataHub 指数日线全空")
    facts["source"]["index"] = "datahub get_index_daily"
    L.append("A1 指数(收盘/当日%/20日累计%/距20日高点回撤%/20日年化波动分位%)")
    idx_fact = {}
    for code, name in IDX:
        rows = sorted(by.get(code, []), key=lambda x: x.get("time") or "")
        rows = [r for r in rows if r.get("time") and r.get("time") <= data_date.isoformat()]
        if not rows:
            L.append(f"  {name} NA(无行情)")
            note_gap(f"{name}行情", "无有效数据")
            continue
        closes = [r.get("close") for r in rows]
        prev_c = closes[-2] if len(closes) > 1 else None
        last = rows[-1]
        c = last.get("close")
        chg = last.get("change_ratio")
        r20 = pct(closes, 20)
        hi20 = max([x for x in closes[-20:] if x is not None]) if any(closes[-20:]) else None
        dd = (c / hi20 - 1) * 100 if (c and hi20) else None
        vr = vol_rank(closes)
        amt = last.get("amount")
        idx_fact[code] = {"close": c, "chg": chg, "cum20": r20, "dd20": dd, "amount": amt,
                          "rows": len(rows), "last_date": last.get("time"), "prev_close": prev_c}
        L.append("  %s %.2f %s %s %s %s" % (
            name, c if c is not None else float("nan"),
            f"{chg:+.2f}" if chg is not None else "NA",
            f"{r20:+.2f}" if r20 is not None else "NA",
            f"{dd:.2f}" if dd is not None else "NA",
            f"{vr[0]:.0f}" if vr else "NA"))
    facts["index"] = idx_fact

    # 两市成交额（上证 + 深证）
    try:
        a_sse = idx_fact["000001.SH"]["amount"]
        a_szz = idx_fact["399001.SZ"]["amount"]
        tot = (a_sse or 0) + (a_szz or 0)
        prev_rows = sorted(by.get("000001.SH", []), key=lambda x: x.get("time") or "")
        prev_szz = sorted(by.get("399001.SZ", []), key=lambda x: x.get("time") or "")
        prev = (prev_rows[-2].get("amount") or 0) + (prev_szz[-2].get("amount") or 0) if len(prev_rows) > 1 and len(prev_szz) > 1 else None
        L.append("A2 两市成交 %.2f万亿 %s" % (tot / 1e12,
                 f"较前日{(tot - prev) / 1e12:+.2f}万亿({(tot / prev - 1) * 100:+.1f}%)" if prev else "前日NA"))
        facts["turnover"] = {"total_yi": tot / 1e8, "prev_yi": prev / 1e8 if prev else None}
    except Exception as e:
        L.append("A2 两市成交 NA")
        note_gap("两市成交额", type(e).__name__)

    # ---------- A3 涨跌停与炸板 ----------
    ds = data_date.strftime("%Y%m%d")
    zt_tc, zt_pool = em_pool("zt", ds)
    dt_tc, _ = em_pool("dt", ds)
    zb_tc, _ = em_pool("zb", ds)
    lbc = max([p.get("lbc") or 0 for p in zt_pool], default=None) if zt_pool else None
    top = next((p.get("n") for p in zt_pool if (p.get("lbc") or 0) == lbc), None) if zt_pool else None
    seal = (zt_tc / (zt_tc + zb_tc) * 100) if (zt_tc is not None and zb_tc is not None and (zt_tc + zb_tc)) else None
    brk = (zb_tc / (zt_tc + zb_tc) * 100) if seal is not None else None
    L.append("A3 涨停%s家 连板高度%s(%s) 炸板%s家 封板率%s 炸板率%s 跌停%s家" % (
        zt_tc if zt_tc is not None else "NA", lbc if lbc else "NA", top or "NA",
        zb_tc if zb_tc is not None else "NA",
        f"{seal:.0f}%" if seal is not None else "NA", f"{brk:.0f}%" if brk is not None else "NA",
        dt_tc if dt_tc is not None else "NA"))
    thr = []
    if zt_tc is not None and zt_tc >= 180:
        thr.append("涨停>=180")
    if lbc and lbc >= 47:
        thr.append("连板>=47")
    L.append("A4 左侧离场硬阈值: %s" % ("触发 " + "/".join(thr) if thr else "未触发"))
    facts["sentiment"] = {"zt": zt_tc, "dt": dt_tc, "zb": zb_tc, "lbc": lbc, "top_lb": top, "seal": seal}
    facts["source"]["pool"] = "datahub CN.MARKET.LIMIT.DETAIL"

    # ---------- E 题材与情绪结构（供题材线判断，替代模型联网扫描）----------
    boards = em_boards()
    if boards:
        L.append("E1 概念板块涨幅榜(涨幅%/主力净流入亿/领涨股):")
        L.append("  " + " | ".join(f"{n} {pc:+.2f}%/{mf:+.1f}/{ld}" for n, pc, mf, ld in boards))
        facts["boards"] = [{"name": n, "pct": pc, "mainflow_yi": mf, "leader": ld} for n, pc, mf, ld in boards]
        facts["source"]["boards"] = "datahub get_sector_flow(concept)"
    else:
        L.append("E1 概念板块涨幅榜 NA(接口不可用)")
    if zt_pool:
        from collections import Counter
        cnt = Counter((p.get("hybk") or "未分类") for p in zt_pool)
        L.append("E2 涨停行业分布: " + " | ".join(f"{k}{v}家" for k, v in cnt.most_common(5)))
        ladder = sorted([p for p in zt_pool if (p.get("lbc") or 0) >= 2],
                        key=lambda p: -(p.get("lbc") or 0))[:8]
        L.append("E3 连板梯队(板数/行业): " + " | ".join(
            f"{(p.get('n') or '')}{p.get('lbc')}板({p.get('hybk') or '-'})" for p in ladder) if ladder
            else "E3 连板梯队: 无≥2板")
        facts["ladder"] = [{"name": p.get("n"), "lbc": p.get("lbc"), "industry": p.get("hybk")} for p in ladder]
    else:
        L.append("E2/E3 涨停池为空，连板结构 NA")
    # ---------- B 期指基差（新浪期货日K，按数据日精确对齐）----------
    prev_state = {}
    if os.path.exists(STATE):
        try:
            prev_state = json.load(open(STATE))
        except Exception:
            prev_state = {}
    L.append("B 期指基差(期货收盘-现货收盘, 点/百分比; Δ=较前一交易日)")
    fut_fact = {}
    for prod, spot_code in FUT:
        info_s = idx_fact.get(spot_code) or {}
        spot, spot_prev = info_s.get("close"), info_s.get("prev_close")
        parts = []
        for off, label in ((0, "当月"), (1, "次月"), (2, "下季")):
            code = fut_contract(prod, data_date, off)
            bars = sina_kline(code)
            cur = next((b for b in bars if b.get("d") == data_date.isoformat()), None)
            priors = [b for b in bars if b.get("d") and b["d"] < data_date.isoformat()]
            prev = priors[-1] if priors else None
            if not cur or spot is None:
                parts.append(f"{label} NA")
                note_gap(f"{prod}{label}基差", "无当日K线" if not cur else "现货缺失")
                continue
            fc = float(cur["c"])
            b_pt, b_pct = fc - spot, (fc - spot) / spot * 100
            d_txt = ""
            if prev and spot_prev:
                d = (fc - spot) - (float(prev["c"]) - spot_prev)
                d_txt = f"({d:+.1f})"
            parts.append(f"{label} {b_pt:+.1f}点/{b_pct:+.2f}%{d_txt}")
            fut_fact[f"{prod}_{off}"] = {"contract": code, "close": fc, "basis": b_pt,
                                         "basis_pct": b_pct, "bar_date": cur.get("d")}
        L.append(f"  {prod}(vs {spot_code}) " + " | ".join(parts))
    facts["futures"] = fut_fact
    facts["source"]["futures"] = "datahub get_futures_contract_daily"
    try:
        json.dump({"data_date": data_date.isoformat(), "nvda_low": prev_state.get("nvda_low", NVDA_LOW)},
                  open(STATE, "w"), ensure_ascii=False, indent=1)
    except Exception as e:
        note_gap("事实包状态落盘", type(e).__name__)

    # ---------- C 期权氛围 ----------
    qs = qvix_series()
    if qs:
        parts, qf = [], {}
        for name in ("300ETF", "500ETF", "创业板", "科创50"):
            sr = qs.get(name)
            if not sr:
                note_gap(f"QVIX{name}", "序列缺失")
                continue
            cur, rank, qd = qvix_pct(sr)
            if not cur or cur <= 0:            # 源数据该系列为空/停更
                note_gap(f"QVIX{name}", "源数据为空(值为0)，系列停更")
                continue
            prev = sr[-2][1] if len(sr) > 1 else None
            parts.append("%s %.2f/%d%%%s" % (name, cur, round(rank),
                                             f"({cur - prev:+.2f})" if prev is not None else ""))
            qf[name] = {"value": cur, "pct_252": rank, "date": qd}
        last_qd = next(iter(qf.values()))["date"] if qf else "NA"
        L.append("C1 QVIX(数值/近252日分位/较前日Δ) 数据日%s" % last_qd)
        L.append("  " + " | ".join(parts))
        facts["qvix"] = qf
        facts["source"]["qvix"] = "options_utility qvix_series"
        if last_qd != data_date.isoformat():
            note_gap("QVIX数据日", f"{last_qd}≠{data_date}(源未更新)")
    else:
        L.append("C1 QVIX NA(optbbs 批量源不可用)")


    # ---------- D 美股锚 ----------
    syms = [s for g in US_TECH.values() for s, _ in g]
    uby = {}
    try:
        from data_hub.collectors.usa_stocks.us_stock import get_us_stock_daily
        for sym in syms:
            plain = sym.split(".")[0]
            try:
                udf = get_us_stock_daily(plain,
                                         start=(data_date - dt.timedelta(days=40)).isoformat(),
                                         end=data_date.isoformat())
            except Exception as ex:
                note_gap(f"美股{sym}", f"DataHub {type(ex).__name__}")
                continue
            if udf is None or len(udf) == 0:
                note_gap(f"美股{sym}", "DataHub 无数据")
                continue
            prev = None
            for _, r in udf.sort_values("date").iterrows():
                c = float(r["close"]) if pd.notna(r.get("close")) else None
                chg = (c / prev - 1) * 100 if (c is not None and prev) else None
                uby.setdefault(sym, []).append(
                    {"time": str(r["date"])[:10], "close": c, "change_ratio": chg})
                prev = c
    except Exception as ex:
        note_gap("美股锚", f"DataHub 美股模块不可用 {type(ex).__name__}")
    us_fact = {}
    L.append("D 美股锚(收盘/当日%):")
    for group, items in US_TECH.items():
        label = {"anchor": "锚NVDA", "spend": "花钱端", "earn": "收钱端"}[group]
        parts, chgs = [], []
        for code, name in items:
            rows = sorted(uby.get(code, []), key=lambda x: x.get("time") or "")
            if not rows:
                parts.append(f"{name} NA")
                note_gap(f"美股{name}", "无行情")
                continue
            last = rows[-1]
            c, ch = last.get("close"), last.get("change_ratio")
            parts.append(f"{name} {c:.2f}/{ch:+.2f}%" if c is not None and ch is not None else f"{name} NA")
            if ch is not None:
                chgs.append(ch)
            us_fact[code] = {"close": c, "chg": ch, "date": last.get("time")}
        avg = sum(chgs) / len(chgs) if chgs else None
        L.append(f"  {label}: " + " ".join(parts) + (f" | 均{avg:+.2f}%" if avg is not None else ""))
    nv = us_fact.get("NVDA.O") or {}
    low = prev_state.get("nvda_low", NVDA_LOW)
    if nv.get("close") is not None:
        L.append("  NVDA 前低锚 %.2f → %s" % (low, "已破" if nv["close"] < low else "未破"))
    facts["us"] = us_fact
    facts["source"]["us"] = "datahub us_stock"

    # ---------- 缺口与上次区间 ----------
    try:
        zs = json.load(open(os.path.join(HERE, "zone_state.json")))
        L.append("STATE 上次区间=%s(数据日%s)" % (zs.get("last_zone"), zs.get("last_date")))
    except Exception:
        L.append("STATE 上次区间 NA(zone_state.json 读取失败)")
    L.append("GAPS " + ("; ".join(gaps) if gaps else "无"))
    facts["gaps"] = gaps

    text = "\n".join(L)
    print(text)
    if a.json:
        json.dump(facts, open(a.json, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()