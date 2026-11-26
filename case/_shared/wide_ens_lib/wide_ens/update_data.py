# -*- coding: utf-8 -*-
"""update_data —— 宽基数据更新（DataHub 版）

原来这个文件干三件事：

    1) 5 宽基日线增量          （tushare index_daily 直连）
    2) 利差/QVIX 因子          （本机 csv → build_spread_factors.py）
    3) 重训 46 维模型 / 包内预测（长任务，默认不用）

迁移后：
    1) → ``datahub_fetch.fetch_index_csv``（DataHub EDB CN.MARKET.INDEX.DAILY）
    2) → ``datahub_fetch.fetch_spread_factors``（DataHub EDB，缺列留空不编造）
       顺带 ``fetch_cross_market``（DataHub GLOBAL.*）—— 这个原来由
       research/weekly_forecast/build_cross_market.py 直连 yfinance 生成，
       而 xm_incr_test 无条件读它，缺文件 today_dual 会退回旧面板（踩过）。
    3) 不再搬：重训是「调参/回测」动作，不是日报该干的活；需要重训时在
       deployment 侧手工跑，本任务保证「有数据、有增量」即可。

对外接口保持不变：``update_raw()`` / ``update_spread()`` 返回 bool。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.dirname(HERE)                      # …/_shared/wide_ens_lib
for _p in (LIB, os.path.join(LIB, "bin")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def log(*a):
    print(*a, flush=True)


def update_raw():
    """增量拉 5 宽基日线（DataHub）。"""
    import datahub_fetch as D
    try:
        rep = D.fetch_index_csv()
    except Exception as e:                                   # noqa: BLE001
        log("[raw] 取数失败 %s" % str(e)[:300])
        return False
    if rep.get("skipped"):
        log("[raw] 缺：%s" % ",".join(rep["skipped"]))
    log("[raw] 5 宽基 → %d 个文件，末年 %s"
        % (rep.get("ok", 0), max((rep.get("dates") or {}).values(), default="-")))
    return rep.get("ok", 0) >= 4          # 至少 4 只拿到才算成功（科创50 等历史短）


def update_spread():
    """利差因子 + 跨市场因子（DataHub）。"""
    import datahub_fetch as D
    ok = True
    try:
        sp = D.fetch_spread_factors()
        log("[spread] %d 列，缺列 %s" % (len(sp.get("cols") or []),
                                       ",".join(sp.get("missing") or []) or "无"))
    except Exception as e:                                   # noqa: BLE001
        log("[spread] 失败 %s" % str(e)[:200])
        ok = False
    try:
        xm = D.fetch_cross_market()
        log("[xm] %d 列 / %d 市场" % (xm.get("cols") or 0, len(xm.get("tags") or [])))
    except Exception as e:                                   # noqa: BLE001
        log("[xm] 失败 %s" % str(e)[:200])
        ok = False
    return ok


def update_all():
    a = update_raw()
    b = update_spread()
    return bool(a and b)


if __name__ == "__main__":
    sys.exit(0 if update_all() else 1)
