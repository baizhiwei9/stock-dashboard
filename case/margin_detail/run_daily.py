#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场融资风险扫描日报 · 每日执行链路

链路（单次同步执行，按日/按文件断点续跑）：
  1) 刷新交易日历 -> data/calendar_open.json（data/cal_refresh.json 记录每日刷新时间）
  2) 全窗口刷新两融汇总 margin_summary.csv（margin API），确定
        report_period = 今日之前最近一个交易日
        base_date     = 两融数据实际最新可得日（未更新时向前回退，最多 5 个交易日）
  3) 逐日补齐 data/md、data/db 并合并到 margin_detail.csv / daily_basic.csv；
     trade_days.json 只保留“两融与行情均有数据”的交易日（避免尾部全 NaN 破坏窗口）
  4) 全窗口刷新指数（000001.SH/399106.SZ -> index_amount.csv；000300.SH/000905.SH -> index_main.csv）
  5) 全窗口刷新 19 只监控 ETF 的 fund_share（etf_share.csv）与 fund_daily（etf_price.csv），按 (ts_code,trade_date) upsert
  6) limit_counts 增量补齐；stock_basic / industry_map 7 天缓存
  7) 依次执行 margin_module.py -> breadth.py -> ind_div.py
  7a) 近一年基础序列 + 两融统计汇总表：refresh_series.py（重建/增量维护近一年三序列 long_series.csv，
      窗口 242 个完整交易日，两融汇总显式排除 BSE）-> ratio_alert.py（近一年分位 + 杠杆率阈值，
      落盘 data/ratio_alert.json，供报告「两融统计汇总表」栏目与红标清单使用）
  7b) ETF存量资金 MACD 栏目：调用 ../etf_macd_monitor/run_chain.py（数据采集→五宽基独立总市值/净申赎序列
      → MACD(10,20,5) 信号→机会/风险分类→双面板图；每日重算近100日序列与 85%/15% 分位），
      并把 data/macd_module.json 拷贝到本工程 data/ 供合并报告读取
  8) 写 data/run_meta.json（report_period / base_date / 回退说明），执行 build_report.py（统一命名日报 + 红标清单 + 邮件正文）
  9) 按基准日去重（data/last_report.json）；推送飞书；红标 ≥1 发提醒邮件、≥2 发强提醒邮件

用法：
  python run_daily.py --send                 # 定时任务用：取上一交易日数据、推送飞书 + 分级邮件
  python run_daily.py --date 20261024 --force --send
  python run_daily.py --dry-run              # 只生成，不推送/不发送/不写去重标记
开关：--send / --dry-run / --force / --no-feishu / --no-email / --skip-macd / --skip-series / --date YYYYMMDD / --skip-fetch
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import dh  # noqa: E402  DataHub/EDB 唯一取数入口（本工程不再直连数据源）
import pandas as pd  # noqa: E402

D = os.path.join(HERE, "data")
MACD_DIR = os.path.join(WORK, "etf_macd_monitor")          # 栏目四：ETF存量资金 MACD 独立工程
MACD_CHAIN = os.path.join(MACD_DIR, "run_chain.py")
REPORT_NAME = "市场融资风险扫描日报"
NAME_MARGIN = "市场融资风险扫描日报（两融专项）"
NAME_OTHER = "市场融资风险扫描日报（资金流与宽度）"
START = "20260301"
MD_KEEP = ["ts_code", "trade_date", "rzye", "rqye", "rzmre", "rzche", "rzrqye", "rqyl", "rqchl"]
DB_KEEP = ["ts_code", "trade_date", "close", "circ_mv", "total_mv", "float_share", "turnover_rate"]
ETFS = ["512480.SH", "588200.SH", "515880.SH", "159819.SZ", "512000.SH", "512800.SH", "512170.SH",
        "159992.SZ", "516270.SH", "159796.SZ", "159928.SZ", "515170.SH", "515080.SH", "563020.SH",
        "515220.SH", "512400.SH", "510300.SH", "588000.SH", "159915.SZ"]
LOG = []


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.append(line)


def jload(p, default=None):
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return default


def jdump(obj, p):
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pstr(d):
    return f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}"


# ---------------- 1) 交易日历 ----------------
def refresh_calendar(today, force=False):
    p, meta = os.path.join(D, "calendar_open.json"), os.path.join(D, "cal_refresh.json")
    ref = jload(meta, {}) or {}
    if not force and ref.get("refreshed") == today and os.path.exists(p):
        days = [int(x) for x in jload(p, [])]
        log(f"交易日历沿用当日缓存：{len(days)} 天，最新 {max(days)}")
        return days
    end = (datetime.strptime(today, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d")
    days = set(dh.trade_calendar(START, end))          # 经 DataHub/EDB 取交易日历
    days |= set(int(x) for x in (jload(p, []) or []))
    days = sorted(days)
    jdump([str(d) for d in days], p)
    jdump({"refreshed": today, "count": len(days), "max": max(days)}, meta)
    log(f"交易日历刷新：{len(days)} 天，最新 {max(days)}")
    return days


# ---------------- 2) 两融汇总 + 基准日 ----------------
def refresh_margin_summary(today):
    ms = dh.margin_summary(START, today)               # 经 DataHub/EDB 取两融汇总
    ms["trade_date"] = ms.trade_date.astype(int)
    ms.to_csv(os.path.join(D, "margin_summary_raw.csv"), index=False)     # 原始留档（含未完整交易日）
    # 完整性校验：同一交易日须同时具备 SSE 与 SZSE（BSE 可选）。
    # 上游常先发布上交所、稍后才发布深交所，若只用「该日是否存在」判断，
    # 会把仅含上交所的半量数据当成有效基准日，导致融资余额被腰斩。
    grp = ms.groupby("trade_date")["exchange_id"].apply(set)
    complete = {d for d, s in grp.items() if {"SSE", "SZSE"} <= s}
    partial = sorted(d for d in grp.index if d not in complete)
    ms = ms[ms.trade_date.isin(complete)]
    ms.to_csv(os.path.join(D, "margin_summary.csv"), index=False)
    days = sorted(set(ms.trade_date))
    log(f"两融汇总刷新：{len(ms)} 行，数据最新 {days[-1]}"
        + (f"（已剔除 {len(partial)} 个未完整交易日，最近 {pstr(partial[-1])}"
           f"，仅含{'/'.join(sorted(grp[partial[-1]]))}）" if partial else "（全部交易日交易所齐备）"))
    return days


# ---------------- 3) 逐日明细 ----------------
def _md_ok(df):
    """两融明细完整性：须同时含沪市(.SH)与深市(.SZ)标的，否则视为未完整发布。"""
    try:
        suf = set(str(c)[-2:] for c in df["ts_code"])
    except Exception:
        return False
    return {"SH", "SZ"} <= suf


def fetch_days(days, api, keep, sub, validate=None, fetch=None):
    """逐日补取明细。

    Args:
        days: 待覆盖的交易日（int）。
        api: 接口名，仅用于 ``empty_days.json`` 的键与日志前缀。
        keep: 需要保留的列。
        sub: 落盘子目录（md / db）。
        validate: 完整性校验函数（如沪深两市齐备校验）。
        fetch: 取数函数，签名 ``fetch(trade_date:str) -> DataFrame``，
            由调用方从 :mod:`dh` 传入（EDB 数据集封装），本函数不直连数据源。
    """
    if fetch is None:
        raise ValueError(f"{api}：未指定 DataHub 取数函数")
    empty = jload(os.path.join(D, "empty_days.json"), {}) or {}
    ddir = os.path.join(D, sub)
    os.makedirs(ddir, exist_ok=True)   # data/ 不入库，首跑时子目录可能不存在
    have = set(os.path.splitext(x)[0] for x in os.listdir(ddir))
    # 剔除历史遗留的「不完整缓存」（如仅含上交所的一天），避免后续永远跳过重取
    if validate:
        bad = []
        for h in list(have):
            try:
                if not validate(pd.read_csv(os.path.join(ddir, h + ".csv"), usecols=["ts_code"])):
                    bad.append(h)
            except Exception:
                bad.append(h)
        for h in bad:
            try:
                os.remove(os.path.join(ddir, h + ".csv"))
            except OSError:
                pass
            have.discard(h)
        if bad:
            log(f"{api}：剔除 {len(bad)} 天不完整缓存（缺沪/深交易所），将重新取数，最近 {pstr(sorted(bad)[-1])}")
    todo = [d for d in days if str(d) not in have and str(d) not in empty.get(api, [])]
    if not todo:
        log(f"{api}：无需补取（已有 {len(have)} 天）")
        return 0
    log(f"{api}：待补取 {len(todo)} 天 {pstr(todo[0])}~{pstr(todo[-1])}")

    def one(d):
        try:
            df = fetch(str(d))                 # 经 DataHub/EDB 数据集取数
        except Exception as e:
            log(f"  {api} {pstr(d)} 取数失败 {str(e)[:120]}")
            return d, 0
        if df is None or df.empty:
            return d, 0
        df = df[[c for c in keep if c in df.columns]]
        if validate and not validate(df):
            return d, -1                       # 数据不完整：不落盘，下次重取
        df.to_csv(os.path.join(ddir, str(d) + ".csv"), index=False)
        return d, len(df)

    ok, incom = 0, []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for fu in as_completed({ex.submit(one, d): d for d in todo}):
            d, n = fu.result()
            if n == 0:
                empty.setdefault(api, []).append(str(d))
            elif n < 0:
                incom.append(d)
            else:
                ok += 1
    jdump(empty, os.path.join(D, "empty_days.json"))
    if empty.get(api):
        log(f"{api}：空数据 {len(empty[api])} 天（已记录，不再重取）")
    if incom:
        log(f"{api}：{len(incom)} 天数据不完整（仅部分交易所），本次不落盘并将重取，最近 {pstr(sorted(incom)[-1])}")
    log(f"{api}：补取完成 {ok}/{len(todo)}")
    del api, keep
    return ok


def merge_days(days):
    for prefix, main in (("md", "margin_detail.csv"), ("db", "daily_basic.csv")):
        files = [os.path.join(D, prefix, str(d) + ".csv") for d in days
                 if os.path.exists(os.path.join(D, prefix, str(d) + ".csv"))]
        p = os.path.join(D, main)
        if os.path.exists(p):                      # 已是最新（天数与末日一致）则免重合并
            head = pd.read_csv(p, usecols=["trade_date"])
            if head.trade_date.nunique() == len(files) and int(head.trade_date.max()) == days[-1]:
                log(f"{main} 已最新（{len(files)} 天），免重合并")
                continue
        df = pd.concat([pd.read_csv(x) for x in files], ignore_index=True)
        df = df.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        df.to_csv(p, index=False)
        log(f"{main} 合并：{len(df)} 行 / {df.trade_date.nunique()} 天")


# ---------------- 4) 指数 ----------------
def refresh_index(base):
    for out, codes in (("index_amount.csv", ["000001.SH", "399106.SZ"]),
                       ("index_main.csv", ["000300.SH", "000905.SH"])):
        df = dh.index_daily(codes, START, str(base))    # 经 DataHub/EDB 取指数日线
        df.to_csv(os.path.join(D, out), index=False)
        log(f"{out} 刷新：{codes}")


# ---------------- 5) ETF ----------------
def refresh_etf(base):
    # 经 DataHub/EDB 取 ETF 份额与行情（数据集内部已做单只失败容忍与 fund_nav 兜底）
    for fetch, out in ((dh.etf_share, "etf_share.csv"), (dh.etf_price, "etf_price.csv")):
        rows = []

        def one(c):
            try:
                df = fetch([c], START, str(base))
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                log(f"  {out} {c} 失败 {str(e)[:80]}")
            return None

        with ThreadPoolExecutor(max_workers=4) as ex:
            for df in ex.map(one, ETFS):
                if df is not None:
                    rows.append(df)
        if not rows:
            log(f"{out}：本次未取到任何数据，保留原有文件")
            continue
        new = pd.concat(rows, ignore_index=True)
        new["trade_date"] = new.trade_date.astype(int)
        p = os.path.join(D, out)
        if os.path.exists(p):
            old = pd.read_csv(p)
            old["trade_date"] = old.trade_date.astype(int)
            new = pd.concat([old, new], ignore_index=True)
        new = new.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        new = new.sort_values(["ts_code", "trade_date"])
        new.to_csv(p, index=False)
        log(f"{out} upsert：{new.ts_code.nunique()} 只 / 最新 {new.trade_date.max()}")


# ---------------- 6) 其他 ----------------
def refresh_limit(days):
    out = os.path.join(D, "limit_counts.csv")
    have = set()
    if os.path.exists(out):
        have = set(str(x) for x in pd.read_csv(out).trade_date.astype(str))
    todo = [d for d in days if str(d) not in have]
    if not todo:
        log("limit_counts：无需补取")
        return

    def one(d):
        try:
            r = dh.limit_counts_day(str(d))        # 经 DataHub/EDB 取涨跌停家数
        except Exception as e:
            log(f"  limit_counts {pstr(d)} 取数失败 {str(e)[:100]}")
            return {"trade_date": str(d), "up_limit": 0, "down_limit": 0, "broken": 0}
        if r.get("empty", False):                  # 接口无该日数据：如实记 0，不补造
            log(f"  limit_counts {pstr(d)} 接口无数据")
        return {"trade_date": str(d), "up_limit": int(r["up_limit"]),
                "down_limit": int(r["down_limit"]), "broken": int(r["broken"])}

    res = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(one, todo):
            res.append(r)
    nd = pd.DataFrame(res)
    nd["trade_date"] = nd.trade_date.astype(int)
    if os.path.exists(out):
        old = pd.read_csv(out)
        old["trade_date"] = old.trade_date.astype(int)
        nd = pd.concat([old, nd], ignore_index=True)
    nd = nd.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    nd.to_csv(out, index=False)
    log(f"limit_counts 补取 {len(todo)} 天")


def refresh_static():
    """静态表：股票基础信息与申万行业映射，7 天过期后刷新（经 DataHub/EDB）。"""
    stale = lambda p: (not os.path.exists(p)) or time.time() - os.path.getmtime(p) > 7 * 86400

    p = os.path.join(D, "stock_basic.csv")
    if stale(p):
        try:
            dh.stock_basic().to_csv(p, index=False)
            log("stock_basic.csv 刷新")
        except Exception as e:
            log(f"!! stock_basic 刷新失败 {str(e)[:120]}")

    p = os.path.join(D, "industry_map.csv")
    if stale(p):
        try:
            imap = dh.industry_map()
            miss = imap.attrs.get("missing_l1") or []
            imap.to_csv(p, index=False)
            log(f"industry_map.csv 刷新：申万一级 {imap.ts_code.nunique()} 只"
                + (f"（缺 {len(miss)} 个行业）" if miss else ""))
        except Exception as e:
            log(f"!! industry_map 刷新失败 {str(e)[:120]}")


# ---------------- 7-9) 计算 / 报告 / 推送 ----------------
def run_script(name, timeout=1800):
    r = subprocess.run([sys.executable, os.path.join(HERE, name)], capture_output=True,
                       text=True, cwd=HERE, timeout=timeout)
    if r.returncode != 0:
        log(f"!! {name} 失败 rc={r.returncode}\n{(r.stderr or r.stdout)[-800:]}")
        return False
    log(f"{name} 完成")
    return True


def run_macd_chain(base, timeout=1800):
    """7b) ETF存量资金 MACD 栏目：调用独立工程 run_chain.py（每日重算近100日序列与85%/15%分位，
    并把「全部宽基的全部子图」合并为单张大图），成功后：
      ① 把 data/macd_module.json 拷入本工程 data/ 供合并报告读取；
      ② 把合并大图拷到工作目录根（与报告同目录），路径写入 macd_module.json 的 combined_report_png，
         供 .md 报告引用与飞书图文卡片上传。
    失败仅告警、不中断其余栏目。"""
    if not os.path.exists(MACD_CHAIN):
        log(f"!! MACD 链路脚本缺失：{MACD_CHAIN}，栏目四将读取已有快照或标记缺失")
        return False
    r = subprocess.run([sys.executable, MACD_CHAIN], capture_output=True, text=True,
                       cwd=MACD_DIR, timeout=timeout)
    if r.returncode != 0:
        log(f"!! MACD 链路失败 rc={r.returncode}\n{(r.stderr or r.stdout)[-800:]}")
        return False
    src = os.path.join(MACD_DIR, "data", "macd_module.json")
    if not os.path.exists(src):
        log("!! MACD 链路未产出 macd_module.json")
        return False
    mod = jload(src, {}) or {}
    # 合并大图 -> 工作目录根（与报告文件同级，文件名含基准日）
    comb = mod.get("combined_chart")
    if comb and os.path.exists(comb):
        dst = os.path.join(WORK, f"{REPORT_NAME}_合并图_{base}.png")
        shutil.copyfile(comb, dst)
        mod["combined_report_png"] = dst
        log(f"MACD 合并大图 -> {dst}（{os.path.getsize(dst)} B）")
    else:
        mod["combined_report_png"] = None
        log("!! MACD 链路未产出合并大图 combined_chart")
    jdump(mod, os.path.join(D, "macd_module.json"))
    log(f"MACD 链路完成：as_of={mod.get('as_of')} 窗口 {mod.get('window', {}).get('first_date')}~"
        f"{mod.get('window', {}).get('last_date')}（{mod.get('window', {}).get('n_trading_days')}交易日）"
        f"触发 {mod.get('n_triggered')}/{mod.get('n_bases')} 机会{len(mod.get('opportunity', []))}·风险{len(mod.get('risk', []))}")
    return True


def run_series_chain(base, timeout=1800):
    """7a) 近一年基础序列 + 两融统计汇总表：
       refresh_series.py（增量维护近一年三条基础序列：两融汇总 SSE+SZSE / 两市成交额 / 全A流通市值；
         落盘 data/long_series.csv 与 data/long_series_meta.json，返回码 1 表示「窗口不足/不连续」——
         仍会落盘已有序列，属可降级情形，不视为链路失败）
       -> ratio_alert.py（近一年 242 交易日分位 + 杠杆率绝对值阈值，落盘 data/ratio_alert.json）。
    失败仅告警、不中断其余栏目（报告「两融统计汇总表」栏目会自动降级标注数据缺失）。"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "refresh_series.py"), "--end", str(base)],
                       capture_output=True, text=True, cwd=HERE, timeout=timeout)
    if r.returncode not in (0, 1):
        log(f"!! refresh_series.py 失败 rc={r.returncode}\n{(r.stderr or r.stdout)[-800:]}")
        return False
    m = jload(os.path.join(D, "long_series_meta.json"), {}) or {}
    mwin = m.get("window", {}) or {}
    log(f"近一年序列：as_of={m.get('as_of')} 窗口 {pstr(mwin.get('first_date'))}~{pstr(mwin.get('last_date'))}"
        f"（{mwin.get('n_trading_days')}/{mwin.get('target_window')} 交易日）"
        f"｜连续={m.get('continuous')}｜窗口满={m.get('window_full')}"
        f"｜完整行 {m.get('complete_rows')}｜缺失日 {len(m.get('window_missing_days') or [])}")
    if not run_script("ratio_alert.py"):
        return False
    ra = jload(os.path.join(D, "ratio_alert.json"), {}) or {}
    st = ra.get("summary_stats", {}) or {}
    log(f"两融统计汇总表：as_of={ra.get('as_of')} 窗口 {pstr(ra.get('window', {}).get('first_date'))}~"
        f"{pstr(ra.get('window', {}).get('last_date'))}｜{st.get('n_rows')} 栏"
        f"（风险 {st.get('n_risk')}·机会 {st.get('n_opp')}·中性 {st.get('n_neu')}）｜触发={ra.get('triggered')}")
    return True


def main():
    ap = argparse.ArgumentParser(description=f"{REPORT_NAME} 每日执行链路")
    ap.add_argument("--date", default="", help="目标交易日 YYYYMMDD（默认今天）")
    ap.add_argument("--send", action="store_true", help="推送飞书并按红标分级发邮件")
    ap.add_argument("--dry-run", action="store_true", help="只生成，不推送/不发送/不写去重标记")
    ap.add_argument("--force", action="store_true", help="忽略同日去重标记，强制重跑并覆盖产物")
    ap.add_argument("--no-feishu", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--skip-macd", action="store_true", help="跳过 MACD 栏目链路（读取已有快照）")
    ap.add_argument("--skip-series", action="store_true",
                    help="跳过近一年序列与两融统计汇总表链路（读取已有 ratio_alert.json）")
    ap.add_argument("--skip-fetch", action="store_true", help="跳过取数，直接用现有数据重算报告")
    ap.add_argument("--only", default="all", choices=["all", "margin", "other"],
                    help="只推送/发送指定报告：all=两份（默认）｜margin=仅两融专项｜other=仅资金流与宽度")
    a = ap.parse_args()

    t0 = time.time()
    today = datetime.now().strftime("%Y%m%d")
    log(f"=== {REPORT_NAME} 启动：{datetime.now():%Y-%m-%d %H:%M:%S} ===")

    # 1) 日历
    open_days = refresh_calendar(today, force=a.force)
    if a.date:
        tgt = int(a.date)
        rp = tgt if tgt in open_days else max([d for d in open_days if d < tgt] or [tgt])
    else:
        rp = max([d for d in open_days if d < int(today)] or [int(today)])
    log(f"report_period = {pstr(rp)}")

    # 2) 两融汇总 + 基准日
    if a.skip_fetch:
        ms = pd.read_csv(os.path.join(D, "margin_summary.csv"))
        marg_days = sorted(set(int(x) for x in ms.trade_date))
    else:
        marg_days = refresh_margin_summary(today)
    cand = [d for d in open_days if d <= rp][::-1]
    base, fb = None, 0
    for i, d in enumerate(cand[:5]):
        if d in marg_days:
            base, fb = d, i
            break
    if base is None:
        log("!! 最近 5 个交易日均无两融数据，终止（不生成过期报告）")
        sys.exit(2)
    if fb:
        log(f"!! {pstr(rp)} 两融数据未更新，基准日回退 {fb} 步 -> {pstr(base)}")

    # 去重（按「报告 × 基准日」分别判定：两条定时任务各自只投递其一，互不阻塞）
    #   同一基准日下，两融专项（--only margin）与资金流与宽度（--only other）各由独立任务推送；
    #   先跑的任务写入其 pushed[报告名]=base_date，后跑的任务只校验自己要投的那份，不被误跳过。
    lp = os.path.join(D, "last_report.json")
    _prev0 = jload(lp, {}) or {}
    _pushed0 = _prev0.get("pushed") or {}
    _need = ([NAME_MARGIN] if a.only == "margin"
             else [NAME_OTHER] if a.only == "other"
             else [NAME_MARGIN, NAME_OTHER])
    if a.only == "all":
        _done = (_prev0.get("base_date") == str(base))
    else:
        _done = all(_pushed0.get(nm) == str(base) for nm in _need)
    if not a.force and not a.dry_run and _done:
        _lbl = "全部报告" if a.only == "all" else _need[0]
        log(f"基准日 {pstr(base)} 的{_lbl}已生成并推送过，跳过（--force 可覆盖）")
        sys.exit(0)

    # 3) 逐日明细
    if not a.skip_fetch:
        hist = [d for d in open_days if int(START) <= d <= base]
        fetch_days(hist, "margin_detail", MD_KEEP, "md", validate=_md_ok, fetch=dh.margin_detail_day)
        fetch_days(hist, "daily_basic", DB_KEEP, "db", fetch=dh.daily_basic_day)
    os.makedirs(os.path.join(D, "md"), exist_ok=True)
    os.makedirs(os.path.join(D, "db"), exist_ok=True)
    have_md = set(os.path.splitext(x)[0] for x in os.listdir(os.path.join(D, "md")))
    have_db = set(os.path.splitext(x)[0] for x in os.listdir(os.path.join(D, "db")))
    days = [d for d in open_days if int(START) <= d <= base
            and str(d) in have_md and str(d) in have_db]
    miss = [d for d in open_days if int(START) <= d <= base
            and not (str(d) in have_md and str(d) in have_db)]
    if miss:
        log(f"!! {len(miss)} 个交易日缺少明细数据，已从交易日序列剔除：{pstr(miss[-1])} 等")
    jdump([str(d) for d in days], os.path.join(D, "trade_days.json"))
    log(f"trade_days：{len(days)} 天 {pstr(days[0])}~{pstr(days[-1])}")
    if not a.skip_fetch:
        merge_days(days)
        refresh_index(base)
        refresh_etf(base)
        refresh_limit([d for d in open_days if int(START) <= d <= base])
        refresh_static()

    # 7) 计算
    for s in ("margin_module.py", "breadth.py", "ind_div.py"):
        if not run_script(s):
            sys.exit(3)

    # 7a) 近一年基础序列 + 两融统计汇总表（失败仅告警，不中断其余栏目）
    if a.skip_series:
        log("已指定 --skip-series：跳过近一年序列链路，读取已有 data/ratio_alert.json")
    else:
        run_series_chain(base)

    # 7b) MACD 栏目（独立工程，每日重算分位；失败仅告警，不中断其余栏目）
    if a.skip_macd:
        log("已指定 --skip-macd：跳过 MACD 链路，读取已有 data/macd_module.json")
    else:
        run_macd_chain(base)

    # 8) run_meta + 报告
    jdump({"report_period": str(rp), "base_date": str(base), "fallback_steps": fb,
           "target_input": a.date or today, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "note": (f"报告期 {pstr(rp)} 两融数据未更新，基准日回退 {fb} 步至 {pstr(base)}"
                    if fb else "报告期与基准日一致")},
          os.path.join(D, "run_meta.json"))
    if not run_script("build_report.py"):
        sys.exit(3)
    # 报告已拆为两份：各自一套卡片 / 红标 / 邮件 / 正文文件，独立投递
    reports = [
        {"name": NAME_MARGIN, "cards": "feishu_cards_margin.json",
         "flags": jload(os.path.join(D, "red_flags_margin.json"), {}) or {},
         "subj_file": "email_subject_margin.txt", "body_file": "email_body_margin.txt",
         "md": os.path.join(WORK, f"{REPORT_NAME}_两融专项_{base}.md")},
        {"name": NAME_OTHER, "cards": "feishu_cards_other.json",
         "flags": jload(os.path.join(D, "red_flags_other.json"), {}) or {},
         "subj_file": "email_subject_other.txt", "body_file": "email_body_other.txt",
         "md": os.path.join(WORK, f"{REPORT_NAME}_资金流与宽度_{base}.md")},
    ]
    for r_ in reports:
        log(f"报告[{r_['name']}] {os.path.basename(r_['md'])}｜"
            f"红标 {r_['flags'].get('count', 0)} 项 → {r_['flags'].get('severity', '无')}")

    # --only 过滤：all=两份；margin=仅两融专项；other=仅资金流与宽度（未选中的报告只生成、不推送/不发邮件）
    only = getattr(a, "only", "all")

    def _keep(r_):
        return (only == "all"
                or (only == "margin" and r_["name"] == NAME_MARGIN)
                or (only == "other" and r_["name"] == NAME_OTHER))

    # 9) 推送 + 邮件（两份各自独立：独立飞书卡片、独立邮件、红标规则分别生效）
    res = {}
    if a.dry_run:
        log("[dry-run] 跳过飞书推送与邮件发送")
    elif a.send:
        for r_ in reports:
            if not _keep(r_):
                log(f"报告[{r_['name']}]：按 --only={only} 跳过推送与邮件")
                continue
            nm, cnt, sev = r_["name"], r_["flags"].get("count", 0), r_["flags"].get("severity", "无")
            feishu_ok = email_ok = None
            if not a.no_feishu:
                r = subprocess.run([sys.executable, os.path.join(HERE, "push_feishu_report.py"),
                                    "--cards-file", r_["cards"]],
                                   capture_output=True, text=True, cwd=HERE, timeout=300)
                feishu_ok = r.returncode == 0 and "DONE" in r.stdout
                log(f"飞书[{nm}]：{'成功' if feishu_ok else '失败'} {(r.stderr or r.stdout)[-300:]}")
            if not a.no_email and cnt >= 1:
                subj = open(os.path.join(D, r_["subj_file"]), encoding="utf-8").read().strip()
                r = subprocess.run([sys.executable, os.path.join(HERE, "email_push.py"),
                                    "--subject", subj, "--body-file", os.path.join(D, r_["body_file"])],
                                   capture_output=True, text=True, cwd=HERE, timeout=300)
                email_ok = r.returncode == 0
                log(f"邮件[{nm}]（{sev}）：{'成功' if email_ok else '失败'} {subj}｜{(r.stderr or r.stdout)[-300:]}")
            else:
                log(f"邮件[{nm}]：红标 {cnt} 项，按规则不发邮件")
            res[nm] = {"feishu_sent": feishu_ok, "email_sent": email_ok, "red_count": cnt, "severity": sev}
    else:
        log("未指定 --send：仅生成，不推送/不发送")

    if a.dry_run:
        # dry-run 不写去重标记：避免把真实投递记录覆盖为 None，也避免下次同日误判为“已推送”而跳过
        log("[dry-run] 保留原 last_report.json，不写去重标记")
    else:
        _prev = jload(lp, {}) or {}
        _prev_map = {x.get("name"): x for x in (_prev.get("reports") or [])}
        _pushed = dict(_prev.get("pushed") or {})
        _out_reports = []
        for r_ in reports:
            if _keep(r_):
                _pushed[r_["name"]] = str(base)
                _out_reports.append({
                    "name": r_["name"], "report_md": r_["md"], "cards_file": r_["cards"],
                    "red_count": r_["flags"].get("count", 0),
                    "severity": r_["flags"].get("severity", "无"),
                    "feishu_sent": (res.get(r_["name"]) or {}).get("feishu_sent"),
                    "email_sent": (res.get(r_["name"]) or {}).get("email_sent")})
            else:
                # 未推送的报告：沿用上一次投递记录，不覆盖为 None
                _out_reports.append(_prev_map.get(r_["name"]) or {
                    "name": r_["name"], "report_md": r_["md"], "cards_file": r_["cards"],
                    "red_count": r_["flags"].get("count", 0),
                    "severity": r_["flags"].get("severity", "无"),
                    "feishu_sent": None, "email_sent": None})
        jdump({"base_date": str(base), "report_period": str(rp), "only": only,
               "pushed": _pushed,
               "reports": _out_reports,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, lp)
    open(os.path.join(D, "run_log.txt"), "a", encoding="utf-8").write("\n".join(LOG) + "\n")
    log(f"=== 完成，用时 {time.time()-t0:.0f}s ===")


if __name__ == "__main__":
    main()