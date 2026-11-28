#!/usr/bin/env python3
"""
A股交易日检查模块
非交易日调用 check_trading_day() 会直接 sys.exit(0)，跳过后续发送逻辑。

用法（每个发消息的脚本加2行）:
    from trading_day_check import check_trading_day
    check_trading_day()
"""
import sys
from datetime import date

_TS_CAL_CACHE = {}


def _tushare_calendar(d):
    """akshare 日历未覆盖该年份时，试 tushare trade_cal（交易所公布的未来年份）。

    返回 (是否交易日, 来源) 或 None（取不到/未覆盖该日）。按年缓存，避免
    fetch/check/emit 反复请求。
    """
    try:
        import os
        lib = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '_lib'))
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
        import app_secrets
        tok = app_secrets.get('tushare.token', default='') or ''
        if not tok:
            return None
        import tushare as ts
        year = d.year
        if year not in _TS_CAL_CACHE:
            pro = ts.pro_api(tok)
            df = pro.trade_cal(exchange='SSE', start_date=f'{year}0101',
                               end_date=f'{year}1231')
            days = {}
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    days[str(row['cal_date'])] = int(row['is_open'])
            _TS_CAL_CACHE[year] = days
        days = _TS_CAL_CACHE[year]
        key = d.strftime('%Y%m%d')
        if key in days:
            return bool(days[key]), "tushare 交易日历"
    except Exception as e:
        print(f"⚠️ tushare 交易日历不可用（{e}）")
    return None


def _fallback_calendar(d):
    """chinese_calendar 未覆盖该年份时的备用判断。

    依次尝试 akshare 日历（覆盖到已公布年份）、tushare trade_cal（未来年份），
    都不可用时退到工作日判断，保证超出年份范围时不会抛 NotImplementedError
    把任务带崩。
    """
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = set()
        for v in df['trade_date']:
            try:
                days.add(v if isinstance(v, date) else date.fromisoformat(str(v)[:10]))
            except (TypeError, ValueError):
                continue
        if days and d <= max(days):
            return d in days, "akshare 交易日历"
    except Exception as e:
        print(f"⚠️ akshare 交易日历不可用（{e}）")
    got = _tushare_calendar(d)
    if got is not None:
        return got
    return d.weekday() < 5, "工作日（节假日未校准）"


def is_trading_day(d=None):
    """判断是否为A股交易日（排除周末+法定节假日）"""
    d = d or date.today()
    try:
        from chinese_calendar import is_workday
        return is_workday(d)
    except (ImportError, NotImplementedError, ValueError) as e:
        print(f"⚠️ chinese_calendar 无法判断 {d}（{e}），改用备用日历")
    ok, src = _fallback_calendar(d)
    if src != "akshare 交易日历":
        print(f"⚠️ {d} 交易日按{src}判断：{ok}")
    return ok

def check_trading_day():
    """非交易日直接退出，不执行后续代码"""
    if not is_trading_day():
        today = date.today()
        print(f"📅 {today} 非A股交易日，跳过发送")
        sys.exit(0)
