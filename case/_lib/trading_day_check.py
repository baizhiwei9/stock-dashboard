#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股交易日检查（stock-dashboard 版）

用法（每个要「只在交易日跑」的脚本加两行）::

    from trading_day_check import check_trading_day
    check_trading_day()          # 非交易日直接 sys.exit(0)

为什么放在 case/_lib：本仓有 6 处脚本 import 它（factor_daily、five_strategy、
limit_up_morning/noon、hurst_daily、vol_monitor_daily、_shared/limit_up_predictor），
但这个模块原来只存在于另一条仓库线的任务目录里，本仓一份都没有 —— 换机器单独跑
就 `ModuleNotFoundError: No module named 'trading_day_check'`。runner 现在把
case/_lib 加进子进程 PYTHONPATH，放这里一次覆盖全部任务。

判定顺序（逐级降级，任何一级不抛崩）：
  1. chinese_calendar.is_workday —— 最准（含法定节假日调休），未装则跳过
  2. akshare 交易日历 —— 覆盖到已公布年份
  3. tushare trade_cal —— 覆盖未来年份（需要 token，取不到则跳过）
  4. 工作日兜底 —— weekday()<5，节假日未校准，会打印警告
"""
import sys
from datetime import date

_TS_CAL_CACHE = {}


def _tushare_calendar(d):
    """返回 (是否交易日, 来源) 或 None（取不到/该日不在日历里）。按年缓存。"""
    try:
        import os
        lib = os.path.dirname(os.path.abspath(__file__))
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import app_secrets
        tok = app_secrets.get("tushare.token", default="") or ""
        if not tok:
            return None
        import tushare as ts
        year = d.year
        if year not in _TS_CAL_CACHE:
            pro = ts.pro_api(tok)
            df = pro.trade_cal(exchange="SSE", start_date=f"{year}0101",
                               end_date=f"{year}1231")
            days = {}
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    days[str(row["cal_date"])] = int(row["is_open"])
            _TS_CAL_CACHE[year] = days
        key = d.strftime("%Y%m%d")
        if key in _TS_CAL_CACHE[year]:
            return bool(_TS_CAL_CACHE[year][key]), "tushare 交易日历"
    except Exception as e:                                  # noqa: BLE001
        print("⚠️ tushare 交易日历不可用（%s）" % e)
    return None


def _fallback_calendar(d):
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = set()
        for v in df["trade_date"]:
            try:
                days.add(v if isinstance(v, date) else date.fromisoformat(str(v)[:10]))
            except (TypeError, ValueError):
                continue
        if days and d <= max(days):
            return d in days, "akshare 交易日历"
    except Exception as e:                                  # noqa: BLE001
        print("⚠️ akshare 交易日历不可用（%s）" % e)
    got = _tushare_calendar(d)
    if got is not None:
        return got
    return d.weekday() < 5, "工作日（节假日未校准）"


def is_trading_day(d=None):
    """是否为 A 股交易日（排除周末 + 法定节假日）。"""
    d = d or date.today()
    try:
        from chinese_calendar import is_workday
        return is_workday(d)
    except (ImportError, NotImplementedError, ValueError) as e:   # noqa: F841
        print("⚠️ chinese_calendar 无法判断 %s，改用备用日历" % d)
    ok, src = _fallback_calendar(d)
    if src != "akshare 交易日历":
        print("⚠️ %s 交易日按%s判断：%s" % (d, src, ok))
    return ok


def check_trading_day():
    """非交易日直接退出（exit 0），不执行后面的发送/出报告逻辑。"""
    if not is_trading_day():
        print("📅 %s 非A股交易日，跳过" % date.today())
        sys.exit(0)


if __name__ == "__main__":
    d = date.today()
    print("%s → %s" % (d, "交易日" if is_trading_day(d) else "非交易日"))