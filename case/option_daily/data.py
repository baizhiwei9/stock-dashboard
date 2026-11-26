# -*- coding: utf-8 -*-
"""option_daily 取数层 —— 期权链只经 DataHub

与原 ``bin/option_daily.py`` 的关系
---------------------------------
原脚本是个 2914 行的「汇总器」：抓飞书群消息（别的应用发的日报）→ 正则解析 SKEW/GEX/OI
表 → 拼装日报 → 发飞书卡片 / QQ / 邮件。其中：

  · 抓群消息、读本地 ``option_chain.db``、发飞书/QQ/邮件  → **私有基础设施，不搬**；
  · 期权链取数（原走 akshare ``option_finance_board`` + 本地库降级）→ 改走 DataHub；
  · 期权计算（原走 ``options_utility``）→ 改走 ``data_hub.core.option_metrics``
    （DataHub 自己的唯一实现，内部再委托 options_utility）。

所以本 case 的口径是「用 DataHub 的期权链，算 DataHub 口径的四个指标」，
不是「把群消息汇总搬过来」。挪不动的那部分在 PR 里说明，不做假复刻。

标的
----
``000300.SH`` ← 华泰柏瑞沪深300ETF（510300）期权，DataHub 侧键名 ``300ETF``。
这是原脚本里指标最全、也是面板 OPT_IDS 的第一位。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_DIR = (os.environ.get("CASE_DATA_DIR")
            or os.path.join(HERE, "data"))

# 任务对外只报这一个标的（task.json 的 metrics[].sec 全是 000300.SH）
TARGET = "300ETF"
SEC = "000300.SH"
MULT = 10000.0        # 沪深300ETF 期权合约乘数：10000 份/张
EXCHANGE = "SSE"      # 510300 在上交所
OPT_PREFIX = "OP510300"  # opt_basic.opt_code 前缀（与 DataHub TARGETS 一致）
SPOT_FALLBACK_CODE = "510300.SH"
MIN_DTE = 3           # 与 DataHub / 原脚本一致：剔当月临到期


def _hub():
    """导入前先把 DataHub 根放到 sys.path[0]。

    ⚠️ 顺序很重要：宿主机上可能装了 ``data_hub_hook.pth``，解释器一启动就把
    **另一份** data_hub（例如 workspace/optstack 下那份）塞进 sys.path 末尾。
    只要在那之前 import 过 data_hub，``sys.modules`` 就锁死了，后面再改
    sys.path 也不会换源 —— 表现就是「data_hub.api 不存在」。所以本模块
    在 import 阶段就先把口径固定下来，所有取数都走同一份。
    """
    _bootstrap()
    import data_hub
    return data_hub


_BOOT_DONE = []


def _bootstrap():
    if _BOOT_DONE:
        return _BOOT_DONE[0]
    import paths  # case/_lib/paths.py
    root = paths.ensure_hub_on_path()
    _BOOT_DONE.append(root or "")
    return root or ""


# 模块导入即固定 DataHub 口径，避免别处先 import 拿到别的副本
try:
    _bootstrap()
except Exception:                                                # noqa: BLE001
    pass          # 框架/手工环境差异，留给调用点报错


def _read_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def resolve_trade_date(ds=""):
    ds = (ds or datetime.date.today().strftime("%Y%m%d")).replace("-", "")
    try:
        from data_hub.core.trade_calendar import resolve
        r, ok, note = resolve(ds)
        return r, ok, note
    except Exception:                                            # noqa: BLE001
        return ds, True, ""


def fetch_chain(trade_date, use_cache=True):
    """取当日期权链 + 标的现价（只经 DataHub 的期权 API）。

    ⚠️ 为什么不用 ``options.get_option_chain('300ETF')``
    ------------------------------------------------
    那个入口在 Tier1（TuShare opt_basic+opt_daily）偶发空响应时**静默降级**到
    Tier2 的 AKShare 看板链，而看板链**没有持仓量（oi 恒为 0）且只有当月一个
    到期月**。实测同一交易日两种结果都出现过（94 行带 OI / 38 行 OI 全 0）。
    PCR 与 GEX 都依赖 OI，用降级链会算出「持仓 PCR = 0」和「GEX 无 Flip」这种
    假结论 —— 比报错更危险。

    所以这里显式走两个原始接口再自己 merge（仍是 DataHub 的 api 层，不新增
    取数通道），拿到 Tier1 的完整链（含 oi / 多个到期月）；拿不到就明确失败。

    Returns dict:
        {"ok": True, "trade_date":..., "spot":..., "rows":[...], "months":[...]}
        {"ok": False, "reason":...}
    """
    cache = os.path.join(DATA_DIR, "chain_%s.json" % trade_date)
    if use_cache:
        got = _read_json(cache)
        if isinstance(got, dict) and got.get("trade_date") == trade_date and got.get("rows"):
            got["_cache"] = "hit"
            return got

    dh = _hub()
    try:
        from data_hub.api import options as opt_api
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "reason": "data_hub.api.options 不可用：%s" % str(e)[:160]}

    basic = _opt_basic(opt_api)
    if basic is None or not len(basic):
        return {"ok": False, "reason": "opt_basic 取不到（%s，重试与缓存都失败）" % EXCHANGE}

    try:
        daily = opt_api.get_option_daily_raw(trade_date=trade_date, exchange=EXCHANGE)
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "reason": "opt_daily 取数异常：%s: %s"
                                       % (type(e).__name__, str(e)[:160])}
    if daily is None or not len(daily):
        # 当日结算数据还没出（盘后 16:00 前跑就会这样）→ 明确失败，不拿看板链顶替
        return {"ok": False,
                "reason": "%s 的 opt_daily 为空（当日结算数据尚未落地）" % trade_date}

    import pandas as pd
    b = basic.copy()
    if "opt_code" not in b.columns:
        return {"ok": False, "reason": "opt_basic 缺 opt_code 列，无法归属标的"}
    b = b[b["opt_code"].astype(str).str.startswith(OPT_PREFIX)]
    b = b[b["maturity_date"].astype(str) >= trade_date]
    if not len(b):
        return {"ok": False, "reason": "opt_basic 里 %s 前缀无未到期合约" % OPT_PREFIX}

    m = b.merge(daily, on="ts_code", how="inner")
    if not len(m):
        return {"ok": False, "reason": "opt_basic × opt_daily 合并后 0 行（当日无成交/无结算）"}

    td = datetime.datetime.strptime(trade_date, "%Y%m%d")
    rows = []
    for r in m.to_dict("records"):
        try:
            cp = str(r.get("call_put") or "").strip().upper()
            if cp not in ("C", "P"):
                continue
            mat = str(r.get("maturity_date") or "")[:8]
            k = float(r.get("exercise_price") or 0)
            close = float(r.get("close") or 0)
            if k <= 0 or not mat.isdigit():
                continue
            dte = max((datetime.datetime.strptime(mat, "%Y%m%d") - td).days, 0)
        except (TypeError, ValueError):
            continue
        rows.append({"ts_code": str(r.get("ts_code") or ""), "call_put": cp,
                     "exercise_price": k, "maturity_date": mat, "dte": dte,
                     "close": close,
                     "vol": float(r.get("vol") or 0),
                     "oi": float(r.get("oi") or 0)})
    if not rows:
        return {"ok": False, "reason": "标准化后 0 条有效合约"}

    # 现价：DataHub 的标的报价（ETF 走 akshare/东财，失败则退到指数）
    spot, spot_src = _spot(opt_api, trade_date)
    if not spot or spot <= 0:
        return {"ok": False, "reason": "标的现价取不到（ATM/偏度/GEX 都要用 S）"}

    months = sorted({r["maturity_date"] for r in rows})
    payload = {"ok": True, "trade_date": trade_date, "target": TARGET, "sec": SEC,
               "spot": round(spot, 4), "spot_src": spot_src,
               "mult": MULT, "min_dte": MIN_DTE, "exchange": EXCHANGE,
               "n_rows": len(rows), "months": months, "rows": rows,
               "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _write_json(cache, payload)
    payload["_cache"] = "miss"
    return payload


def _opt_basic(opt_api, retries=3):
    """合约主表（opt_basic）。**与交易日无关**，12000 行、接口易超时。

    所以：本地缓存 7 天 → 命中就不用再拉；未命中重试 3 次（间隔递增）。
    合约主表变化很慢（新挂牌按季/按月加），7 天缓存不影响「当日有哪些合约」的
    判断——真正决定当日有效合约的是 opt_daily 的成交/持仓，那部分是每天取的。
    """
    cache = os.path.join(DATA_DIR, "opt_basic_%s.json" % EXCHANGE)
    got = _read_json(cache)
    if isinstance(got, dict) and got.get("rows"):
        try:
            age = (datetime.datetime.now()
                   - datetime.datetime.strptime(str(got.get("fetched_at"))[:19],
                                                "%Y-%m-%d %H:%M:%S")).days
        except (TypeError, ValueError):
            age = 999
        if age <= 7:
            import pandas as pd
            return pd.DataFrame(got["rows"])
    import time as _t
    import pandas as pd
    last = ""
    for i in range(max(1, retries)):
        try:
            b = opt_api.get_option_basic_raw(exchange=EXCHANGE)
        except Exception as e:                                   # noqa: BLE001
            b = None
            last = "%s: %s" % (type(e).__name__, str(e)[:100])
        if b is not None and len(b):
            _write_json(cache, {"exchange": EXCHANGE,
                                "fetched_at": datetime.datetime.now().strftime(
                                    "%Y-%m-%d %H:%M:%S"),
                                "rows": b.to_dict("records")})
            return b
        if i < retries - 1:
            _t.sleep(2 * (i + 1))
    return None


def _spot(opt_api, trade_date):
    """标的现价：先按 DataHub 的标的键取实时/收盘价，退到 ETF 收盘，再退指数。"""
    try:
        q = opt_api.get_option_underlying_quote(TARGET)
        if isinstance(q, dict):
            for k in ("price", "latest", "last", "close"):
                try:
                    v = float(q.get(k) or 0)
                except (TypeError, ValueError):
                    v = 0.0
                if v > 0:
                    return v, "underlying_quote.%s" % k
    except Exception:                                            # noqa: BLE001
        pass
    # 退一步：ETF 日线收盘（DataHub EDB 指数/ETF 日线口径）
    try:
        res = _hub().edb.get_dataset_result(
            "CN.MARKET.INDEX.DAILY", codes=[SPOT_FALLBACK_CODE],
            start_date=trade_date, end_date=trade_date)
        if res.get("ok") and res["data"] is not None and len(res["data"]):
            return float(res["data"].iloc[-1]["close"]), "edb:%s" % SPOT_FALLBACK_CODE
    except Exception:                                            # noqa: BLE001
        pass
    return 0.0, ""
