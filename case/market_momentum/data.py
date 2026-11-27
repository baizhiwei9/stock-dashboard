# -*- coding: utf-8 -*-
"""market_momentum 取数层 —— 只经 DataHub，不自建 HTTP、不自开线程池

取数口径
--------
1. 宽基指数日线：DataHub EDB 数据集 ``CN.MARKET.INDEX.DAILY``
   （``amount`` 单位千元；上证综指 + 深证综指相加即两市成交额）
2. 申万一级行业日线：DataHub 统一 Tushare 通道 ``data_hub.ts_query('sw_daily')``
   （统一凭据 / 代理与直连降级 / 全局串行限流都在 DataHub 里，本模块不自建连接）

为什么不再用原 ``bin/industry_layer.py``、``scripts/sector_rotation/fetch_sector_data.py``
--------------------------------------------------------------------------------
原实现要「全市场个股日线 + 个股资金流 + 涨停池 + 同花顺板块历史」四套数据，
并且 ``fetch_sector_data`` 自己开 ``ThreadPoolExecutor(6)`` 直连 akshare。
按 2026-11-26 的口径（取数一律走 DataHub、任务侧不自开线程池），这里：
  · 个股资金流 / 涨停池 → 未搬（DataHub 当日 ``moneyflow`` 需直连 token 且缺行业维度，
    搬过来只能得到 0 行，属于「用空数据冒充无数据」，不如不做）；
  · 行业动量 → 改用申万一级 ``sw_daily``（本身就带 pct_change / amount / float_mv，
    不需要再逐只个股聚合，一次取数即可）；
  · 串行取数，失败按「单日重试 → 整段跳过并在报告里标注」处理，不并发。

缓存
----
``data/`` 下按数据日落 json，同一天重复跑直接复用（避开 22:50/23:30 那种补跑窗口
重复拉全量）。可用 ``TASK_WS`` 覆盖 workspace 根；缓存目录永远在任务自己的 data/。
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

# ---- 工作目录口径：env → 任务目录自身（不写死任何宿主机路径）-------------
# runner 拉起时给 CASE_DATA_DIR；手工跑时退到 <case>/data。
DATA_DIR = (os.environ.get("CASE_DATA_DIR")
            or os.path.join(HERE, "data"))

# 五只宽基：与 task.json 的 metrics[].sec 一一对应
INDICES = [
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000016.SH", "上证50"),
    ("399006.SZ", "创业板指"),
]
# 两市成交额用：上证综指（沪市）+ 深证综指（含全深市，不能用 399001 那只成分指数）
TURNOVER_CODES = ["000001.SH", "399106.SZ"]

SW_LEVEL1 = "SW2021"
LOOKBACK = 40          # 指数：取多少自然日（约 28 个交易日）
SW_NEED_DAYS = 22      # 申万一级：至少要覆盖的交易日数（20 日动量 + 2 天余量）


def _hub():
    """把 DataHub 挂进 sys.path 并返回模块（唯一入口，口径来自 case/_lib/paths.py）。"""
    import paths  # case/_lib/paths.py
    paths.ensure_hub_on_path()
    import data_hub
    return data_hub


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


def _resolve_trade_date(ds):
    """非交易日回退到最近交易日（DataHub 交易日历，避免周末跑出空报告）。"""
    try:
        from data_hub.core.trade_calendar import resolve
        r, ok, note = resolve(ds)
        return r, ok, note
    except Exception:                                            # noqa: BLE001
        return ds, True, ""


def fetch_indices(trade_date):
    """宽基 + 两市成交额的日线（近 LOOKBACK 自然日）。"""
    dh = _hub()
    end = trade_date
    start = (datetime.date(int(end[:4]), int(end[4:6]), int(end[6:]))
             - datetime.timedelta(days=LOOKBACK)).strftime("%Y%m%d")
    # 除宽基 + 两市成交额外，还拉 399001.SZ（深证成指）——analyze.py 的「当日涨跌」
    # 读它，不拉就永远取不到数。
    codes = [c for c, _n in INDICES] + TURNOVER_CODES + ["399001.SZ"]
    res = dh.edb.get_dataset_result("CN.MARKET.INDEX.DAILY",
                                    codes=codes, start_date=start, end_date=end)
    if not res.get("ok"):
        raise RuntimeError("指数日线取数失败：%s" % (res.get("error") or "未知原因"))
    df = res["data"]
    if df is None or len(df) == 0:
        raise RuntimeError("指数日线返回 0 行（%s~%s）" % (start, end))
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    out = {"trade_date": trade_date, "start": start,
           "rows": df.to_dict("records")}
    return out


def fetch_sw_daily(trade_date, need_days=SW_NEED_DAYS):
    """申万一级行业日线（至少覆盖最近 need_days 个交易日）。

    ⚠️ TuShare ``sw_daily`` 单次返回有行数上限（实测约 4000 行）：一次要 30 天
    全市场（含 L2/L3 约 450 个代码）只会返回**最近 9 天**，多出来的日期被静默截断。
    所以这里先整段取、再检测覆盖了几个交易日，缺口按日补取（每次只返回当天的
    全量，行数远小于上限）。缺的补不上就在报告里标注「窗口不足」，不假装有 20 天。

    返回 {"ok":True, "rows":[...], "dates":[交易日...], "note":...}
    或 {"ok":False, "reason":...}。
    """
    dh = _hub()
    end = trade_date
    start = (datetime.date(int(end[:4]), int(end[4:6]), int(end[6:]))
             - datetime.timedelta(days=int(need_days * 2.2) + 10)).strftime("%Y%m%d")

    codes = set()
    cls_err = ""
    try:
        cls = dh.ts_query("index_classify", level="L1", src=SW_LEVEL1)
    except Exception as e:                                       # noqa: BLE001
        cls = None
        cls_err = "%s: %s" % (type(e).__name__, str(e)[:120])
    if cls is not None and len(cls) and "index_code" in cls.columns:
        codes = {str(x) for x in cls["index_code"].tolist() if str(x)}
    if not codes:
        return {"ok": False, "reason": "指数分类 index_classify 取不到 L1 代码：%s"
                                       % (cls_err or "返回空")}

    def _filter(df):
        if df is None or not len(df) or "ts_code" not in df.columns:
            return []
        sub = df[df["ts_code"].astype(str).isin(codes)]
        if not len(sub):
            return []
        sub = sub.copy()
        sub["trade_date"] = sub["trade_date"].astype(str)
        return sub.to_dict("records")

    rows, notes = [], []
    first_err = ""
    try:
        rows = _filter(dh.ts_query("sw_daily", start_date=start, end_date=end))
    except Exception as e:                                       # noqa: BLE001
        first_err = "%s: %s" % (type(e).__name__, str(e)[:140])

    # 需要覆盖的交易日（用 DataHub 交易日历，不自己猜）
    want = _trade_days(start, end)[-need_days:]
    have = {r["trade_date"] for r in rows}
    missing = [d for d in want if d not in have]
    if missing:
        notes.append("整段取数缺 %d 天，按日补取" % len(missing))
        for d in missing:
            try:
                got = _filter(dh.ts_query("sw_daily", trade_date=d))
            except Exception:                                    # noqa: BLE001
                continue
            rows.extend(got)

    if not rows:
        return {"ok": False, "reason": first_err or "sw_daily 返回 0 行"}

    dates = sorted({r["trade_date"] for r in rows})
    if len(dates) < need_days:
        notes.append("窗口仅 %d/%d 个交易日" % (len(dates), need_days))
    return {"ok": True, "trade_date": trade_date, "n_codes": len(codes),
            "rows": rows, "dates": dates,
            "note": "；".join(notes)}


def _trade_days(start, end):
    """[start, end] 内的交易日列表（YYYYMMDD 升序）。日历取不到就退化成自然日，
    取数失败由调用方按「缺块」处理，不在这里抛。"""
    from data_hub.core.trade_calendar import cal_days
    out = []
    try:
        days = cal_days()
        out = [d for d in sorted(days) if start <= d <= end and days.get(d)]
    except Exception:                                            # noqa: BLE001
        out = []
    if out:
        return out
    cur = datetime.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    last = datetime.date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while cur <= last:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    return out


def fetch_all(date_str="", use_cache=True):
    """一次取齐，落 data/。date_str 为空取今天。"""
    ds = (date_str or datetime.date.today().strftime("%Y%m%d")).replace("-", "")
    td, is_open, note = _resolve_trade_date(ds)
    cache = os.path.join(DATA_DIR, "raw_%s.json" % td)
    if use_cache:
        got = _read_json(cache)
        if isinstance(got, dict) and got.get("trade_date") == td and got.get("indices"):
            got["_cache"] = "hit"
            return got

    idx = fetch_indices(td)
    sw = fetch_sw_daily(td)
    payload = {"requested": ds, "trade_date": td, "is_open": bool(is_open),
               "note": note, "indices": idx, "sw": sw,
               "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _write_json(cache, payload)
    payload["_cache"] = "miss"
    return payload
