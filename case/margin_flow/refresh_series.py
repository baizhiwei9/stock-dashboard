#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""近一年基础序列维护（两融汇总 / 两市成交额 / 全A流通市值）

用途：为「融资比值分位监控」提供覆盖近一年（≥242 个交易日）的三条基础序列，支持每日增量维护、
持久化落盘与完整性校验。本脚本只产出聚合序列（每日一行），不保存全市场个股明细，避免数据目录膨胀。

输出：
  data/trade_days_long.json   长窗口交易日历（含缓冲）
  data/long_series.csv        逐日序列，列见 COLUMNS
  data/long_series_meta.json  最近一次运行的校验结果（窗口、缺口、半量日、家数）

三序列口径：
  1) 两融汇总  margin(rzye/rzmre/rzche/rqye/rzrqye)  —— 同一交易日须同时含 SSE 与 SZSE（半量日不采）
  2) 两市成交额 index_daily(amount, 千元) = 上证综指(000001.SH) + 深证综指(399106.SZ)
  3) 全A流通市值 daily_basic(circ_mv, 万元) = 全部 .SH + .SZ 个股求和，家数 < MIN_STOCKS 视为不完整

完整性规则：交易日必须连续（缺失日由交易日历比对得出）；不完整日/半量日不写入序列（缺失不填充），
在 meta 中单列；分位窗口只使用完整日。

用法：
  python refresh_series.py                 # 增量维护（沿用已有序列，只补缺失日）
  python refresh_series.py --rebuild       # 全量重建
  python refresh_series.py --end 20261024  # 指定窗口终点（默认今日之前最近交易日）
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dh  # noqa: E402  DataHub/EDB 唯一取数入口（本工程不再直连数据源）
import pandas as pd  # noqa: E402

D = os.path.join(HERE, "data")
SERIES = os.path.join(D, "long_series.csv")
META = os.path.join(D, "long_series_meta.json")
EMPTY = os.path.join(D, "long_series_empty.json")
CAL_LONG = os.path.join(D, "trade_days_long.json")

WINDOW = 242          # 近一年分位窗口（交易日）
FETCH_START = "20250901"   # 抓取起点（含缓冲，保证 242 个完整日可用）
MIN_STOCKS = 3500     # 全A（SH+SZ）家数下限
COLS = ["trade_date", "rzye", "rzmre", "rzche", "rqye", "rzrqye", "amt", "circ_mv_wan",
        "n_sh", "n_sz", "n_stock", "src_margin", "src_db", "complete"]


def jload(p, default=None):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return default
    return default


def jdump(obj, p):
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pstr(d):
    d = str(d)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- 1) 交易日历（长窗口） ----------------
def load_calendar(end):
    days = sorted(dh.trade_calendar(FETCH_START, str(end)))   # 经 DataHub/EDB 取交易日历
    jdump([str(d) for d in days], CAL_LONG)
    return days


# ---------------- 2) 两融汇总（区间一次取回） ----------------
def margin_series(start, end):
    """返回 {trade_date: dict}，仅保留同日含 SSE+SZSE 的完整日。

    注意：margin 接口同时返回 BSE（北交所），本序列口径为沪深两所，
    必须显式过滤 SSE/SZSE，否则融资余额会被 BSE 抬升约 78 亿元。
    """
    ms = dh.margin_summary(start, end)                 # 经 DataHub/EDB 取两融汇总
    if ms.empty:
        return {}, []
    ms["trade_date"] = ms.trade_date.astype(int)
    ms = ms[ms.exchange_id.isin(["SSE", "SZSE"])]
    grp = ms.groupby("trade_date")["exchange_id"].apply(set)
    keep = {d for d, s in grp.items() if {"SSE", "SZSE"} <= s}
    partial = sorted(d for d in grp.index if d not in keep)
    out = {}
    for d, g in ms[ms.trade_date.isin(keep)].groupby("trade_date"):
        out[int(d)] = {k: float(g[k].sum()) for k in ("rzye", "rzmre", "rzche", "rqye", "rzrqye") if k in g.columns}
    log(f"两融汇总：{len(out)} 个完整日"
        + (f"（剔除 {len(partial)} 个半量日，最近 {pstr(partial[-1])}）" if partial else ""))
    return out, partial


# ---------------- 3) 两市成交额（上证综指 + 深证综指） ----------------
def amount_series(start, end):
    amt = {}
    for code in ("000001.SH", "399106.SZ"):
        try:
            df = dh.index_daily([code], start, end)    # 经 DataHub/EDB 取指数日线
        except Exception as e:
            log(f"  index_daily {code} 失败 {str(e)[:80]}")
            continue
        if df is None or df.empty:
            continue
        df["trade_date"] = df.trade_date.astype(int)
        for d, v in zip(df.trade_date, df.amount):
            if pd.isna(v):
                continue
            if d in amt and code == "399106.SZ":
                amt[d] += float(v)
            else:
                amt.setdefault(d, float(v))
    log(f"两市成交额：{len(amt)} 天（000001.SH + 399106.SZ，单位千元）")
    return amt


# ---------------- 4) 全A流通市值（优先本地明细缓存，缺失日走接口） ----------------
def circ_from_local(d):
    p = os.path.join(D, "db", f"{d}.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, usecols=["ts_code", "circ_mv"])
    except Exception:
        return None
    mk = df.ts_code.str.split(".").str[1]
    sub = df[mk.isin(["SH", "SZ"])]
    return int((mk == "SH").sum()), int((mk == "SZ").sum()), float(sub.circ_mv.sum())


def circ_from_api(d):
    try:
        df = dh.daily_basic_day(str(d))                # 经 DataHub/EDB 取每日指标
    except Exception as e:
        log(f"  daily_basic {pstr(d)} 失败 {str(e)[:80]}")
        return None
    if df is None or df.empty or "circ_mv" not in df.columns:
        return None
    df = df[["ts_code", "circ_mv"]].dropna()
    mk = df.ts_code.str.split(".").str[1]
    sub = df[mk.isin(["SH", "SZ"])]
    return int((mk == "SH").sum()), int((mk == "SZ").sum()), float(sub.circ_mv.sum())


def circ_series(days):
    """逐日流通市值：先读本地全市场明细缓存（data/db/<date>.csv），再读本脚本的聚合缓存，
    最后才走接口；接口结果会写入聚合缓存，避免重建时重复取数。"""
    cache_p = os.path.join(D, "long_series_dbcache.csv")
    cache = {}
    if os.path.exists(cache_p):
        c = pd.read_csv(cache_p)
        c["trade_date"] = c.trade_date.astype(int)
        cache = {int(r.trade_date): (int(r.n_sh), int(r.n_sz), float(r.circ_mv_wan), str(r.src))
                 for r in c.itertuples()}
    out = {}
    todo = []
    for d in days:
        loc = circ_from_local(d)
        if loc is not None and loc[0] + loc[1] >= MIN_STOCKS:
            out[d] = loc + ("local",)
        elif d in cache and cache[d][0] + cache[d][1] >= MIN_STOCKS:
            out[d] = cache[d]
        else:
            todo.append(d)
    log(f"全A流通市值：本地明细命中 {len([1 for d in out if out[d][3] == 'local'])} 天，"
        f"聚合缓存命中 {len(out) - len([1 for d in out if out[d][3] == 'local'])} 天，需接口补取 {len(todo)} 天")
    if todo:
        def one(d):
            try:
                r = circ_from_api(d)
            except Exception as e:
                return d, None, str(e)[:80]
            return d, r, ""

        t0 = time.time(); done = 0; n_api = 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            for fu in as_completed({ex.submit(one, d): d for d in todo}):
                d, r, err = fu.result()
                done += 1
                if r is not None:
                    out[d] = r + ("api",)
                    cache[d] = out[d]
                    n_api += 1
                elif err:
                    log(f"  daily_basic {pstr(d)} 失败 {err}")
                if done % 30 == 0:
                    log(f"  daily_basic {done}/{len(todo)} elapsed={time.time()-t0:.0f}s")
        log(f"全A流通市值：接口补取成功 {n_api}/{len(todo)} 天，合计可用 {len(out)} 天")
        if cache:
            pd.DataFrame([{"trade_date": k, "n_sh": v[0], "n_sz": v[1], "circ_mv_wan": v[2], "src": v[3]}
                          for k, v in sorted(cache.items())]).to_csv(cache_p, index=False)
            log(f"聚合缓存落盘：{cache_p}（{len(cache)} 天）")
    return out


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="近一年基础序列维护（两融汇总/两市成交额/全A流通市值）")
    ap.add_argument("--end", default="", help="窗口终点 YYYYMMDD（默认今日之前最近交易日）")
    ap.add_argument("--rebuild", action="store_true", help="全量重建（忽略已有序列）")
    a = ap.parse_args()

    end = int(a.end) if a.end else int(datetime.now().strftime("%Y%m%d"))
    days_all = load_calendar(end)
    log(f"长窗口交易日历：{len(days_all)} 天 {pstr(days_all[0])}~{pstr(days_all[-1])}")

    old = pd.DataFrame()
    if os.path.exists(SERIES) and not a.rebuild:
        old = pd.read_csv(SERIES)
        if not old.empty:
            old["trade_date"] = old.trade_date.astype(int)
            log(f"沿用已有序列：{len(old)} 天 {pstr(old.trade_date.min())}~{pstr(old.trade_date.max())}")

    mrg, partial = margin_series(days_all[0], days_all[-1])
    amt = amount_series(days_all[0], days_all[-1])

    # 只对「两融完整 且 有成交额」的交易日取流通市值（避免浪费接口调用）
    need = [d for d in days_all if d in mrg and d in amt]
    circ = circ_series(need)

    rows = []
    for d in days_all:
        m = mrg.get(d)
        c = circ.get(d)
        a_ = amt.get(d)
        if not m or c is None or a_ is None:
            continue
        n_sh, n_sz, cm, src_db = c[0], c[1], c[2], c[3]
        n_stock = n_sh + n_sz
        row = {"trade_date": d, "rzye": m.get("rzye"), "rzmre": m.get("rzmre"), "rzche": m.get("rzche"),
               "rqye": m.get("rqye"), "rzrqye": m.get("rzrqye"), "amt": a_, "circ_mv_wan": cm,
               "n_sh": n_sh, "n_sz": n_sz, "n_stock": n_stock,
               "src_margin": "margin(SSE+SZSE)", "src_db": src_db,
               "complete": bool(n_stock >= MIN_STOCKS and cm > 0)}
        rows.append(row)

    new = pd.DataFrame(rows)
    if not old.empty:
        new = pd.concat([old, new], ignore_index=True)
    new = new.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    new["trade_date"] = new.trade_date.astype(int)
    new = new[COLS]
    new.to_csv(SERIES, index=False)

    # ---------------- 校验：窗口、连续性、半量日 ----------------
    comp = new[new.complete == True]            # noqa: E712
    as_of = int(comp.trade_date.max())
    win = comp[comp.trade_date <= as_of].tail(WINDOW)
    win_days = list(win.trade_date.astype(int))
    cal_in = [d for d in days_all if win_days[0] <= d <= win_days[-1]] if win_days else []
    missing = [d for d in cal_in if d not in set(win_days)]      # 交易日历有、序列无（缺失日）
    incomplete = [int(d) for d in new[new.complete == False].trade_date]   # noqa: E712

    meta = {
        "as_of": as_of,
        "window": {"n_trading_days": len(win_days), "first_date": win_days[0] if win_days else None,
                   "last_date": win_days[-1] if win_days else None,
                   "target_window": WINDOW, "definition": f"近一年 = 最近 {WINDOW} 个完整交易日"},
        "series_rows": int(len(new)),
        "complete_rows": int(len(comp)),
        "incomplete_days": incomplete,
        "window_missing_days": missing,
        "continuous": len(missing) == 0,
        "window_full": len(win_days) == WINDOW,
        "min_stocks_threshold": MIN_STOCKS,
        "min_n_stock_in_window": int(win.n_stock.min()) if len(win) else None,
        "partial_margin_days": partial[-5:] if partial else [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": {"margin": "TuShare margin (SSE+SZSE)", "amount": "TuShare index_daily 000001.SH+399106.SZ (千元)",
                   "circ_mv": "TuShare daily_basic circ_mv 全A SH+SZ 求和 (万元)"},
    }
    jdump(meta, META)
    log(f"序列落盘：{SERIES}（{len(new)} 天，完整 {len(comp)} 天）")
    log(f"近一年窗口：{meta['window']['n_trading_days']}/{WINDOW} 天 "
        f"{pstr(win_days[0]) if win_days else '-'}~{pstr(win_days[-1]) if win_days else '-'}"
        f"｜连续={meta['continuous']}｜窗口满={meta['window_full']}｜缺失日={len(missing)}｜不完整日={len(incomplete)}")
    if incomplete:
        log(f"不完整日（未写入分位窗口）：{[pstr(x) for x in incomplete[-8:]]}")
    if missing:
        log(f"!! 窗口内缺失交易日：{[pstr(x) for x in missing[:8]]}")
    if not meta["window_full"]:
        log(f"!! 近一年窗口不足 {WINDOW} 天（实得 {len(win_days)} 天），分位口径需按实际窗口标注")
    return 0 if (meta["window_full"] and meta["continuous"]) else 1


if __name__ == "__main__":
    sys.exit(main())