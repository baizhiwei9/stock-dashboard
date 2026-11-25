#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史期权成交量回填（OCC Volume Query 月度模式）
================================================
用户要求：历史数据不得靠逐日积攒，需向前回溯获取并构造「当天交易」快照。

数据源（唯一被验证可用的历史源）：
  OCC Volume Query  https://marketdata.theocc.com//volume-query
  - 支持按 underlying 回溯过去 24 个月的逐日成交量（call/put 双侧）
  - 按月批量（reportType=M）：一次返回该月全部交易日的分合约明细
  - 天然只含交易日（周末/节假日不出现在 actdate），无 OI 字段

已确认不可用（历史 OI）：
  OCC Open Interest 报表仅提供全市场汇总（Equity/Index/Other/Futures 总额）
  OCC Series Search 无历史日期维度
  NASDAQ/Yahoo/CBOE/FINRA/optionslam/stockanalysis/optionistics 均无法提供
  按 underlying 的历史 OI -> R5/R6（OI 2σ/80分位）需自首个快照起逐日积累

口径对齐：
  OCC 的 volume 为全市场「合约边数」（contract sides，每笔成交计两侧）；
  NASDAQ option-chain 的 c_Volume/p_Volume 为单边合约张数。
  直接用 OCC sides 与 NASDAQ 当期 vol 比较会造成 ~2 倍系统性偏差，
  因此按 symbol 用同一交易日（最近一期）计算校准系数 k_c/k_p：
      NASDAQ 当期 call_vol  / OCC 同交易日 call sides
  历史 OCC sides × k -> NASDAQ 口径的估算成交量，供 R1（30日均量）比较用。
  校准系数写入 _meta，口径为估算值，注释说明其本质。

合并规则：
  按日期去重合并进 options_oi_history.json；同日已存在快照（含 OI 的当期/已积累
  快照）优先保留、不覆盖；仅新增 OCC 回填的历史交易日（只含成交量字段）。

用法：
  python3 options_backfill.py [--history options_oi_history.json]
                              [--data options_data_output.json]
                              [--months 12] [--symbols TLT,TLH,IEF,XLU]
"""
import argparse
import csv
import io
import json
import sys
import time
from datetime import date, datetime, timedelta

import requests

OCC_BASE = "https://marketdata.theocc.com//volume-query"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DEFAULT_SYMBOLS = ("TLT", "TLH", "IEF", "XLU")


def _month_end_yyyymmdd(y, m):
    """该月最后一天，返回 YYYYMMDD。"""
    if m == 12:
        last = date(y, 12, 31)
    else:
        last = date(y, m + 1, 1) - timedelta(days=1)
    return last.strftime("%Y%m%d")


def fetch_month(sym, report_date, retries=2, report_type="M"):
    """拉取某标的某月的 OCC 成交量，返回 dict[(actdate, porc)] = qty。
    report_type: M=整月逐日；D=单日。"""
    params = {
        "format": "csv", "reportDate": report_date, "volumeQueryType": "O",
        "symbolType": "U", "symbol": sym, "reportType": report_type,
        "accountType": "ALL", "productKind": "ALL", "porc": "BOTH",
    }
    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.get(OCC_BASE, params=params, headers=HEADERS, timeout=60)
            if r.status_code == 200 and r.text.strip():
                rows = {}
                for rec in csv.DictReader(io.StringIO(r.text)):
                    d = rec["actdate"].strip()
                    p = rec["porc"].strip()
                    try:
                        rows[(d, p)] = rows.get((d, p), 0) + int(float(rec["quantity"]))
                    except (KeyError, ValueError):
                        continue
                if rows:
                    return rows, r.text
        except requests.RequestException as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(1.5)
    if last_err is not None:
        raise RuntimeError("OCC %s %s: %s" % (sym, report_date, last_err))
    raise RuntimeError("OCC %s %s: empty response" % (sym, report_date))


def backfill(history_path, data_path, months, symbols):
    with open(history_path, encoding="utf-8") as fp:
        hist = json.load(fp)
    with open(data_path, encoding="utf-8") as fp:
        data = json.load(fp)

    snaps = hist.setdefault("snapshots", [])
    meta = hist.setdefault("_meta", {})
    existing_dates = {s["date"] for s in snaps}

    # 当期 NASDAQ 口径（校准基准）
    nasdaq_cur = {}
    for sym in symbols:
        tot = data["symbols"].get(sym, {}).get("totals", {})
        if tot.get("call_vol") and tot.get("put_vol"):
            nasdaq_cur[sym] = {"call_vol": tot["call_vol"], "put_vol": tot["put_vol"]}

    today = date.today()
    year, month = today.year, today.month

    # 当前月用最近完整交易日报告：从今天往回最多 5 天逐个试到非空（OCC 日终数据次日可得）
    cur_month_report = None
    probe_sym = symbols[0]
    for back in range(0, 6):
        cand = (today - timedelta(days=back)).strftime("%Y%m%d")
        try:
            probe_rows, _ = fetch_month(probe_sym, cand)
        except RuntimeError:
            continue
        if probe_rows:
            cur_month_report = cand
            break
    if cur_month_report is None:
        cur_month_report = (today - timedelta(days=1)).strftime("%Y%m%d")
    print("当前月报告日:", cur_month_report)

    # 月度列表：当月 + 往前 months-1 个月
    month_list = []
    for i in range(months):
        yy, mm = year, month - i
        while mm <= 0:
            mm += 12
            yy -= 1
        month_list.append((yy, mm, cur_month_report if i == 0 else _month_end_yyyymmdd(yy, mm)))

    calib = {}          # sym -> {k_call, k_put, ref_date}
    added = []          # 新增快照日期（YYYY-MM-DD）
    skipped = []
    failed = []

    # 校准基准日：含 OI 的 NASDAQ 快照最新日期（当天 OCC 值与之对齐最合理）
    oi_dates = [s["date"] for s in snaps if s.get("symbols") and
                any("call_oi" in v for v in s["symbols"].values())]
    oi_ref_date = max(oi_dates) if oi_dates else None

    # 每个标的独立校准：用 NASDAQ 快照日期当天的 OCC 日报告（reportType=D）
    # 计算 k_call/k_put；该日期应已回填或拉取当月数据，若不在当月则由 D 报告直接提供
    calib = {}
    calib_errors = []
    if oi_ref_date:
        occ_ref = oi_ref_date.replace("-", "")
        for sym in symbols:
            try:
                daily_rows, _ = fetch_month(sym, occ_ref, report_type="D")
            except RuntimeError as exc:  # noqa: BLE001
                calib_errors.append("%s %s: %s" % (sym, occ_ref, exc))
                continue
            agg = {}
            for (d, p), q in daily_rows.items():
                mm_, dd_, yyyy_ = d.split("/")
                iso = "%s-%s-%s" % (yyyy_, mm_, dd_)
                agg.setdefault(iso, {"C": 0, "P": 0})
                agg[iso][p] += q
            ref_cp = agg.get(oi_ref_date, {})
            nd = nasdaq_cur.get(sym)
            if nd and ref_cp.get("C", 0) > 0:
                k_call = nd["call_vol"] / ref_cp["C"]
                k_put = nd["put_vol"] / ref_cp["P"] if ref_cp.get("P", 0) > 0 else k_call
                calib[sym] = {"k_call": round(k_call, 4), "k_put": round(k_put, 4),
                              "ref_date": oi_ref_date}
            else:
                calib_errors.append("%s %s: 校准数据缺失" % (sym, oi_ref_date))
            time.sleep(0.4)
    for ce in calib_errors:
        print("  ! 校准失败: %s（回填量将按原值写入）" % ce)

    for yy, mm, rep in month_list:
        ym = "%04d-%02d" % (yy, mm)
        for sym in symbols:
            try:
                rows, _ = fetch_month(sym, rep)
            except RuntimeError as exc:  # noqa: BLE001
                failed.append("%s %s: %s" % (sym, ym, exc))
                continue
            # 按交易日聚合 C/P
            day_agg = {}
            for (d, p), q in rows.items():
                # 10/08/2026 -> 2026-10-08
                mm_, dd_, yyyy_ = d.split("/")
                iso = "%s-%s-%s" % (yyyy_, mm_, dd_)
                day_agg.setdefault(iso, {"C": 0, "P": 0})
                day_agg[iso][p] += q
            for iso, cp in sorted(day_agg.items()):
                if iso in existing_dates:
                    skipped.append("%s %s (已存在)" % (iso, sym))
                    continue
                # 校准系数：优先用独立校准（NASDAQ 快照日期当日 OCC 日报告），缺失则按 1.0
                kk = calib.get(sym) or {"k_call": 1.0, "k_put": 1.0}
                call_vol = int(round(cp["C"] * kk["k_call"]))
                put_vol = int(round(cp["P"] * kk["k_put"]))
                # 检查该日是否已由其它标的写入（快照按日期共享符号表）
                cur_snap = next((s for s in snaps if s["date"] == iso), None)
                if cur_snap is None:
                    cur_snap = {"date": iso, "symbols": {}, "source": "OCC_backfill"}
                    snaps.append(cur_snap)
                    added.append("%s %s" % (iso, sym))
                elif sym not in cur_snap["symbols"]:
                    added.append("%s %s" % (iso, sym))
                cur_snap["symbols"][sym] = {
                    "call_vol": call_vol, "put_vol": put_vol,
                    "pcr_vol": round(call_vol / put_vol, 3) if put_vol else None,
                    "avg_vol_30d": None,
                }
            time.sleep(0.4)

    # 排序（按日期）
    def _dkey(s):
        return s.get("date", "")

    snaps.sort(key=_dkey)

    meta.update({
        "note": "OI 历史快照按交易日追加（自 2026-10-08 起），供 2σ/分位统计；"
                "成交量历史由 OCC Volume Query 月度接口回填（source=OCC_backfill）",
        "backfill_source": "OCC Volume Query (reportType=M, past 24 months)",
        "backfill_oi_unavailable": ("OCC Open Interest 报表仅全市场汇总、Series Search 无历史日期，"
                                    "按 underlying 的历史 OI 无免费源；R5/R6 OI 2σ/80分位"
                                    "需自首个含 OI 的快照起逐日积累"),
        "calibration": calib,
        "calibration_note": ("OCC 值为全市场 sides（每笔成交计两侧），按最近一期与 NASDAQ 当期"
                             "call_vol/put_vol 比值折算为单边合约估算值，供 R1 30 日均量同口径比较；"
                             "系数为估算性质"),
        "last_backfill_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    with open(history_path, "w", encoding="utf-8") as fp:
        json.dump(hist, fp, ensure_ascii=False, indent=2)

    print("回填完成: 快照总数=%d, 新增=%d, 跳过(已存在)=%d, 失败=%d" % (
        len(snaps), len(added), len(skipped), len(failed)))
    for a in added:
        print("  + %s" % a)
    for f in failed:
        print("  ! %s" % f)
    print("校准系数:", json.dumps(calib, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="options_oi_history.json")
    ap.add_argument("--data", default="options_data_output.json")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    args = ap.parse_args()
    backfill(args.history, args.data, args.months,
             tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip()))


if __name__ == "__main__":
    main()