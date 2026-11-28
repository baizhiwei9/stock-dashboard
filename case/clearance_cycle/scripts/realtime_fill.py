#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中实时补当日 bar：日线源拿不到「今天」时，用 DataHub 实时快照兜底。

14:50 跑的时候，日线源（中证指数官网、新浪期货日线）经常还没有当日 bar：
MA40/MA80、被动化率、现货基差都会停留在 T-1。这里用 DataHub 的统一实时入口
（``get_daily_with_realtime`` / ``get_realtime_futures``）补一根**当日** bar。

安全边界（宁可少补，不可造数）：
  · 只在「北京时间的今天是交易日」且「序列末根不是今天」时补；
  · 实时快照自己的报价日期必须等于今天 —— 期货快照会返回已到期合约的旧价，
    不校验就会把 10 月的价格当成今天的；
  · DataHub 不可用 / 超时 / 无快照 / 非交易时段，一律只记日志后原样返回，
    绝不阻断 fetch，也不会补一根来源不明的 bar。

每个函数返回 meta（是否补、来源、时点），调用方落进 JSON 便于排障。
"""
import signal
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
REALTIME_TIMEOUT = 40          # 单次实时接口的硬超时（秒）


# ── DataHub 路径（任务由 scheduler 直拉，sys.path 里没有 case/_lib）──────────
def _ensure_hub():
    try:
        from hub_root import ensure_hub_on_path
    except ImportError:
        import os
        d = os.path.dirname(os.path.abspath(__file__))
        while not os.path.isfile(os.path.join(d, "_lib", "hub_root.py")):
            parent = os.path.dirname(d)
            if parent == d:
                raise
            d = parent
        sys.path.insert(0, os.path.join(d, "_lib"))
        from hub_root import ensure_hub_on_path
    return ensure_hub_on_path(__file__)


# ── 日期 / 超时 ──────────────────────────────────────────────────────────────
def today_cn():
    """北京时间今天（YYYY-MM-DD），不用宿主机本地时区。"""
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _norm_date(v):
    """20261128 / 2026-11-28 / '20261128 00:00:00' → 2026-11-28。"""
    s = str(v).strip()[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _is_trading_day(d_iso):
    """交易日判断走 DataHub 日历；拿不到日历就**不补**（不能把节假日补成假 bar）。"""
    try:
        _ensure_hub()
        from data_hub.collectors.stocks.index_daily import _is_trading_day
        return bool(_is_trading_day(d_iso.replace("-", "")))
    except Exception as e:  # noqa: BLE001
        print(f"[realtime_fill] 交易日判断失败，本次不补: {e}", flush=True)
        return False


def _call_with_timeout(fn, seconds=REALTIME_TIMEOUT):
    """给实时接口套硬超时。主线程用 SIGALRM；非主线程退化为直接调用。"""
    if threading.current_thread() is not threading.main_thread():
        return fn()

    def _handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"实时接口超过 {seconds}s")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# ── 指数 ─────────────────────────────────────────────────────────────────────
_AMOUNT_CACHE = None


def _spot_amounts():
    """{6位指数代码: 成交额(亿元)}，来自新浪指数快照（成交额单位：元）。"""
    global _AMOUNT_CACHE
    if _AMOUNT_CACHE is not None:
        return _AMOUNT_CACHE
    out = {}
    try:
        _ensure_hub()
        from data_hub.collectors.misc.option_board import get_index_spot_sina
        df = _call_with_timeout(get_index_spot_sina, 30)
        if df is not None and len(df) and "代码" in df.columns:
            for _, r in df.iterrows():
                code = str(r.get("代码", "")).strip()
                amt = _num(r.get("成交额"))
                if code and amt and amt > 0:
                    out[code[-6:]] = round(amt / 1e8, 1)
    except Exception as e:  # noqa: BLE001
        print(f"[realtime_fill] 指数成交额快照失败（不影响补价）: {e}", flush=True)
    _AMOUNT_CACHE = out
    return out


def fetch_index_today(codes, lookback=10):
    """补指数当日实时 bar。

    Args:
        codes: {"hs300": "000300.SH", ...}（key 任意，值是 tushare 代码）
        lookback: 日线回溯自然日数（只为拿到昨收算涨跌幅）

    Returns:
        {key: {date, close, chg_pct, amount_yi, source, as_of}}
        只包含「确认是今天」的；没补的 key 不出现。
    """
    today = today_cn()
    if not _is_trading_day(today):
        print(f"[realtime_fill] {today} 非交易日，不补当日 bar", flush=True)
        return {}
    try:
        _ensure_hub()
        from data_hub.collectors.stocks.index_daily import get_daily_with_realtime
        frames = _call_with_timeout(
            lambda: get_daily_with_realtime(
                ts_code_list=list(codes.values()), lookback=lookback),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[realtime_fill] 指数实时补数失败，沿用日线: {e}", flush=True)
        return {}
    if frames is None:
        return {}
    if not isinstance(frames, dict):
        frames = {list(codes.values())[0]: frames}

    amounts = _spot_amounts()
    out = {}
    for key, code in codes.items():
        df = frames.get(code)
        if df is None or not len(df):
            continue
        meta = getattr(df, "attrs", {}).get("realtime") or {}
        if not meta.get("is_realtime"):
            print(f"[realtime_fill] {key} 未补: {meta.get('reason') or '无实时快照'}", flush=True)
            continue
        row = df.iloc[-1]
        d = _norm_date(row.get("trade_date", row.get("date")))
        if d != today:
            print(f"[realtime_fill] {key} 实时 bar 日期 {d} != {today}，丢弃", flush=True)
            continue
        price = _num(row.get("close"))
        if not price or price <= 0:
            continue
        prev = _num(df.iloc[-2].get("close")) if len(df) > 1 else None
        chg = _num((meta.get("injected") or {}).get("change_pct"))
        if chg is None and prev:
            chg = (price / prev - 1) * 100
        out[key] = {
            "date": today,
            "close": round(price, 3),
            "chg_pct": round(chg, 2) if chg is not None else None,
            "amount_yi": amounts.get(code.split(".")[0]),
            "source": (meta.get("injected") or {}).get("source"),
            "as_of": meta.get("as_of"),
        }
        print(f"[realtime_fill] {key} 补入 {today} close={price:.2f} "
              f"chg={out[key]['chg_pct']} 源={out[key]['source']}", flush=True)
    return out


def append_bar(df, bar, columns, date_col="date"):
    """把 bar 追加到 df（同一天已存在则覆盖），列对齐 columns。"""
    import pandas as pd

    row = {c: bar.get(c) for c in columns}
    row[date_col] = bar["date"]
    df = df.copy()
    df[date_col] = df[date_col].astype(str)
    if len(df) and str(df[date_col].iloc[-1]) == bar["date"]:
        idx = df.index[-1]
        for c in columns:
            if bar.get(c) is not None:
                df.at[idx, c] = bar.get(c)
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    return df.sort_values(date_col).reset_index(drop=True)


def fetch_index_history(ts_code, lookback):
    """csindex 整体不可用时的第二数据源：DataHub 指数日线 + 当日实时。

    返回列与 csindex_hist 一致的 DataFrame（date/close/amount_yi/chg_pct）；
    日线源没有成交额，amount_yi 列留空（下游按「成交额缺失」处理）。
    """
    try:
        _ensure_hub()
        from data_hub.collectors.stocks.index_daily import get_daily_with_realtime
        df = _call_with_timeout(
            lambda: get_daily_with_realtime(ts_code=ts_code, lookback=lookback),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[realtime_fill] {ts_code} 日线兜底失败: {e}", flush=True)
        return None
    if df is None or not len(df):
        return None
    import pandas as pd

    out = pd.DataFrame({
        "date": [_norm_date(x) for x in df["trade_date" if "trade_date" in df.columns else "date"]],
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "amount_yi": float("nan"),
        "chg_pct": float("nan"),
    })
    close = out["close"]
    out["chg_pct"] = (close / close.shift(1) - 1) * 100
    meta = getattr(df, "attrs", {}).get("realtime") or {}
    print(f"[realtime_fill] {ts_code} 改用 DataHub 日线 {len(out)} 行 "
          f"（实时补={bool(meta.get('is_realtime'))}）", flush=True)
    return out.sort_values("date").reset_index(drop=True)


# ── 期货 ─────────────────────────────────────────────────────────────────────
def _fut_key(sym):
    """get_realtime_futures 会把末尾 0 去掉（IF0=主力连续），按同一规则取 key。"""
    return sym[:-1] if sym.endswith("0") else sym


def fill_futures_today(futs):
    """给 {合约: 日线df(date/close/volume)} 补当日实时行（就地修改）。

    返回 {合约: meta}；没补的合约不出现。快照报价日期不是今天的不补。
    """
    today = today_cn()
    need = [s for s, df in futs.items()
            if df is not None and len(df) and str(df["date"].iloc[-1])[:10] < today]
    if not need:
        return {}
    if not _is_trading_day(today):
        print(f"[realtime_fill] {today} 非交易日，期货不补当日 bar", flush=True)
        return {}
    try:
        _ensure_hub()
        from data_hub.collectors.misc.option_chain_realtime import get_realtime_futures
        quotes = _call_with_timeout(lambda: get_realtime_futures(need))
    except Exception as e:  # noqa: BLE001
        print(f"[realtime_fill] 期货实时补数失败，沿用日线: {e}", flush=True)
        return {}
    if not quotes:
        return {}

    import pandas as pd

    out = {}
    for sym in need:
        q = quotes.get(_fut_key(sym)) or quotes.get(sym)
        if not q:
            continue
        qd = _norm_date(q.get("quote_date"))
        if qd != today:
            print(f"[realtime_fill] {sym} 快照日期 {qd} != {today}，丢弃", flush=True)
            continue
        last = _num(q.get("last"))
        if not last or last <= 0:
            continue
        df = futs[sym]
        row = {"date": today, "close": last, "volume": _num(q.get("volume")) or 0}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        futs[sym] = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
        out[sym] = {"date": today, "close": last, "source": q.get("source"),
                    "quote_time": q.get("quote_time")}
        print(f"[realtime_fill] {sym} 补入 {today} close={last} "
              f"@{q.get('quote_time')}", flush=True)
    return out


def futures_daily_from_hub(sym, lookback_days=420):
    """akshare 合约日线失败时的第二数据源；返回 date/close/volume 或 None。"""
    try:
        _ensure_hub()
        from data_hub.collectors.futures import get_futures_contract_daily
        df = _call_with_timeout(
            lambda: get_futures_contract_daily(contract=sym, lookback=lookback_days),
            60,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[realtime_fill] {sym} DataHub 合约日线失败: {e}", flush=True)
        return None
    if df is None or not len(df):
        return None
    import pandas as pd

    out = pd.DataFrame({
        "date": [_norm_date(x) for x in df["date"]],
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    })
    return out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
