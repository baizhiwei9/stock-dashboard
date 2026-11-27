#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日判断（DataHub 交易日历版）

原实现（宿主机 ``bin/trade_day.py``）自己拉 tushare ``trade_cal`` 并缓存到
``/root/.openclaw/workspace/.cache/trade_cal.json``。搬进 case/ 之后：
日历只有一个来源 —— **DataHub** ``data_hub.core.trade_calendar``，
本模块只做一层薄包装，保持 ``d8 / is_open / prev_trade / resolve`` 四个名字不变，
调用方（trade_gate / make_today_report / push_feishu）一行都不用改。

口径统一的好处：DataHub 已经处理了「非交易日回退到最近交易日」，且日历缓存
由它自己管，任务侧不再另存一份、不会两份日历打架。
"""
from __future__ import annotations

import datetime

FMT = "%Y%m%d"


def _tc():
    """import data_hub 的交易日历（唯一来源）。"""
    from wsenv import hub                # noqa: PLC0415  延迟导入，模块级 import 会拖慢冷启动
    hub()
    from data_hub.core import trade_calendar as tc   # noqa: PLC0415
    return tc


def d8(date=None) -> str:
    """统一成 YYYYMMDD 字符串。"""
    if date is None:
        return datetime.date.today().strftime(FMT)
    return str(date).strip().replace("-", "").replace("/", "")[:8]


def d10(date=None) -> str:
    s = d8(date)
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:])


def cal_days(force: bool = False):
    """{YYYYMMDD: 0/1}。日历拿不到返回空 dict（调用方按「照常跑」处理）。"""
    try:
        return _tc().cal_days(force=force) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def is_open(date=None, days=None):
    """(是否交易日, 日历 dict)。"""
    try:
        return _tc().is_open(d8(date), days=days)
    except Exception:                                        # noqa: BLE001
        return True, (days or {})


def prev_trade(date=None, days=None) -> str:
    try:
        return _tc().prev_trade(d8(date), days=days)
    except Exception:                                        # noqa: BLE001
        d = datetime.datetime.strptime(d8(date), FMT).date() - datetime.timedelta(days=1)
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)
        return d.strftime(FMT)


def resolve(date=None, days=None):
    """(数据日 YYYYMMDD, 是否交易日, 说明)。非交易日回退到最近交易日。

    与 ``data_hub.core.trade_calendar.resolve`` 完全同义，只是强制走 wsenv.hub()。
    """
    try:
        r, ok, note = _tc().resolve(d8(date), days=days)
        return r, bool(ok), note
    except Exception as e:                                   # noqa: BLE001
        return d8(date), True, "交易日历不可用（%s）" % str(e)[:80]


def note(date=None) -> str:
    try:
        return _tc().note(d8(date))
    except Exception:                                        # noqa: BLE001
        return ""


if __name__ == "__main__":
    d, ok, n = resolve()
    print("today=%s data_day=%s is_open=%s %s prev=%s"
          % (d8(), d, ok, n, prev_trade()))
