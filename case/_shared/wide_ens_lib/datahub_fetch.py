#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""datahub_fetch —— 宽基三任务的取数层（只在 DataHub 里取，不自建 HTTP）

搬过来的三个任务的原始依赖是宿主机 ``.cache/weekly_forecast/raw/`` 下的一批 csv：

    idx_hs300.csv / idx_zz500.csv / idx_zz1000.csv / idx_kc50.csv / idx_cyb.csv
        五只宽基日线（open/high/low/close/vol/amount）
    spread_factors.csv
        期限利差 / 中国信用利差 / 美国信用利差 / 中美利差 / 期权隐含波动率
    cross_market_factors.csv, china_us_factors.csv, opt_feat.csv, orth_feats.csv
        跨市场与期权因子（历史模型训练用）

本模块负责前两类：

1. **指数日线**（必须有，缠论/AC/动量的唯一输入）
   ``DataHub EDB 数据集 CN.MARKET.INDEX.DAILY``
   → 统一 Tushare 通道（代理/直连降级、全局串行限流都在 DataHub 里），
     字段 ts_code/trade_date/open/high/low/close/vol/amount，正好是 csv 需要的列。

2. **利差因子**（有则用，缺的写 NaN 并在报告里标注）
   映射表见 ``SPREAD_MAP``；DataHub 有的指标直接算，没有的（中债中短票 AAA 3Y、
   期权隐含波动率 qvix/iv/skew）**留空不编造** —— 模型侧本来就把 NaN 填 0，
   编一个假值进去会污染特征，比缺列更糟。

⚠️ 为什么不在任务侧直连 akshare / tushare
   2026-11-26 口径：取数一律走 DataHub，任务侧不开线程池、不抓外网。
   本模块串行、单次调用，失败按「单块跳过 + 报告标注」处理，不并发、不重试风暴。
"""
from __future__ import annotations

import datetime
import os
import sys

import pandas as pd

try:
    from wsenv import WS, hub, raw_dir, INDICES      # noqa: PLC0415
except ImportError:                                   # 单独拷出去跑
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    INDICES = []

    def raw_dir():
        p = os.path.join(WS, ".cache", "weekly_forecast", "raw")
        os.makedirs(p, exist_ok=True)
        return p

    def hub():
        import data_hub
        return data_hub


# csv 文件名 → ts_code（与 INDICES 同序，但拆开是因为 csv 名不带交易所后缀写法）
IDX_CSV = {
    "idx_hs300.csv": "000300.SH",
    "idx_zz500.csv": "000905.SH",
    "idx_zz1000.csv": "000852.SH",
    "idx_kc50.csv": "000688.SH",
    "idx_cyb.csv": "399006.SZ",
}

# 利差因子：本仓能从 DataHub 直接算出的部分
#   (csv 列名, 计算方式)
SPREAD_MAP = {
    "gz_10y": ("RATE.CN.10Y", None),            # 中债 10Y 国债（%）
    "gz_1y": ("RATE.CN.1Y", None),
    "cn_us_10y": ("RATE.SPREAD.CN10Y_US10Y", None),   # bp
    "us_10y": ("RATE.US.10Y", None),
}
# 明确「DataHub 里当前没有」的列：不编造，留空 + 报告标注
SPREAD_MISSING = ("cn_cs_3y", "cn_cs_baa", "qvix300", "qvix500",
                  "iv_atm300", "skew300", "us_cs_baa10y", "us_baa10y")


def hub_():                                      # 别名：liq_lib 用 from datahub_fetch import hub
    return hub()


def _series(code: str):
    """DataHub 单指标 → pd.Series（index=DatetimeIndex）。失败返回 None。"""
    try:
        df = hub().edb.get_series(code)
    except Exception as e:                                   # noqa: BLE001
        print("  [edb] %s 取数失败：%s" % (code, str(e)[:100]))
        return None
    if df is None or len(df) == 0:
        print("  [edb] %s 空序列" % code)
        return None
    try:
        s = pd.Series(df["value"].to_numpy(dtype="float64"),
                      index=pd.DatetimeIndex(pd.to_datetime(df["date"])))
    except Exception as e:                                   # noqa: BLE001
        print("  [edb] %s 解析失败：%s" % (code, str(e)[:100]))
        return None
    return s[~s.index.duplicated(keep="last")].sort_index()


def fetch_index_csv(start="20130101", end="", force=False) -> dict:
    """五只宽基日线 → raw/idx_*.csv。

    返回 {"ok": n, "files": [...], "dates": {csv: last_date}, "skipped": [...]}
    """
    dh = hub()
    end = end or datetime.date.today().strftime("%Y%m%d")
    out = {"ok": 0, "files": [], "dates": {}, "skipped": []}
    codes = list(IDX_CSV.values())
    try:
        res = dh.edb.get_dataset_result("CN.MARKET.INDEX.DAILY",
                                        codes=codes, start_date=start, end_date=end)
    except Exception as e:                                   # noqa: BLE001
        raise RuntimeError("指数日线取数失败（DataHub）：%s" % str(e)[:200])
    if not res.get("ok"):
        raise RuntimeError("指数日线取数失败：%s" % (res.get("error") or "未知原因"))
    df = res.get("data")
    if df is None or len(df) == 0:
        raise RuntimeError("指数日线返回 0 行（%s~%s）" % (start, end))

    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    need = ["trade_date", "open", "high", "low", "close", "vol", "amount"]
    for fn, code in IDX_CSV.items():
        sub = df[df["ts_code"].astype(str) == code]
        if not len(sub):
            out["skipped"].append(fn)
            print("  [idx] %s 在 DataHub 返回里没有数据，跳过" % code)
            continue
        sub = sub.sort_values("trade_date")
        cols = [c for c in need if c in sub.columns]
        p = os.path.join(raw_dir(), fn)
        sub[cols].to_csv(p, index=False)
        out["ok"] += 1
        out["files"].append(fn)
        out["dates"][fn] = str(sub["trade_date"].iloc[-1])
        print("  [idx] %-16s %5d 行 → %s（末年 %s）"
              % (fn, len(sub), os.path.basename(p), out["dates"][fn]))
    return out


def fetch_spread_factors() -> dict:
    """利差因子 csv（能算的算，缺的留空）。返回 {"cols": [...], "missing": [...]}"""
    got = {}
    for col, (code, _fn) in SPREAD_MAP.items():
        s = _series(code)
        if s is not None and len(s):
            got[col] = s
    if "gz_10y" in got and "gz_1y" in got:
        got["ts_10y1y"] = (got["gz_10y"] - got["gz_1y"]) * 100     # bp
    if "us_baa10y" in got and "us_10y" in got:
        got["us_cs_baa10y"] = got["us_baa10y"] - got["us_10y"]

    cols = list(got)
    if not cols:
        print("  [spread] 一条都取不到，跳过（模型侧按缺失处理）")
        return {"cols": [], "missing": list(SPREAD_MISSING)}

    F = pd.DataFrame(got).sort_index()
    # 滚动 250 日分位（与原 build_spread_factors 同口径）
    for c in list(F.columns):
        if c.endswith("_10y") or c in ("ts_10y1y", "cn_cs_3y", "us_cs_baa10y"):
            F[c + "_p250"] = F[c].rolling(250, min_periods=60).rank(pct=True)
    p = os.path.join(raw_dir(), "spread_factors.csv")
    F.index.name = "date"
    F.to_csv(p)
    print("  [spread] %d 列 → %s（%s ~ %s），缺列：%s"
          % (len(F.columns), os.path.basename(p), F.index[0].date(),
             F.index[-1].date(), ",".join(SPREAD_MISSING)))
    return {"cols": list(F.columns), "missing": list(SPREAD_MISSING),
            "n": len(F), "last": str(F.index[-1].date())}


def refresh_all(what=("idx", "spread", "xm"), start="20130101") -> dict:
    """一次取齐。what 里放 'idx' / 'spread'。"""
    rep = {"idx": None, "spread": None, "xm": None}
    if "idx" in what:
        rep["idx"] = fetch_index_csv(start=start)
    if "spread" in what:
        rep["spread"] = fetch_spread_factors()
    if "xm" in what:
        rep["xm"] = fetch_cross_market()
    return rep


if __name__ == "__main__":
    import json
    print("raw_dir =", raw_dir())
    print(json.dumps(refresh_all(), ensure_ascii=False, indent=1, default=str))


# ── 跨市场因子（cross_market_factors.csv）────────────────────────────────
# 为什么必须造这个文件：``research_wf/xm_incr_test.build_X`` 在函数开头**无条件**
# ``pd.read_csv(XM)``，文件不在就抛异常 → today_dual 的 pred_today 落空 →
# 回退到 panel 里最后一行的旧预测（实测会退回 2026-10-19 的分，等于用 5 周前的
# 信号冒充今天）。所以宁可只写「DataHub 有的那几个市场」，也不能让文件缺席。
#
# ⚠️ 只写真实可取到的市场：SPX / NASDAQ100 / VIX / DXY / 布伦特 / 黄金 / LME。
#    A50 / 恒生 / 恒生科技 / 中概（原 csv 的 xm_a50_* / xm_hsi_* / xm_kweb_* 等）
#    DataHub 当前**没有**，就不写这些列 —— 缺列在该任务里是安全的（use_xm=False
#    时列根本不参与特征），编一列假数据才是错的。
XM_SERIES = {
    "spx": "GLOBAL.SP500",
    "ndx": "GLOBAL.NASDAQ100",
    "vix": "GLOBAL.VIX",
    "dxy": "GLOBAL.DXY",
    "brent": "GLOBAL.BRENT",
    "gold": "GLOBAL.GOLD.AU9999",
    "lme_cu": "GLOBAL.LME.CU",
    "lme_al": "GLOBAL.LME.AL",
}


def _mk_xm_feats(s: pd.Series, tag: str) -> pd.DataFrame:
    """按原 build_cross_market 的命名口径造特征列（ret1/mom5/mom20/gap/amp/dvol20/ma20dev/streak）。"""
    idx = s.index
    out = pd.DataFrame(index=idx)
    r = s.pct_change() * 100
    out["xm_%s_ret1" % tag] = r
    for n in (5, 20):
        out["xm_%s_mom%d" % (tag, n)] = s / s.shift(n) - 1
    out["xm_%s_dvol20" % tag] = r.rolling(20).std()
    out["xm_%s_ma20dev" % tag] = s / s.rolling(20).mean() - 1
    up = (r > 0).astype(int)
    out["xm_%s_streak" % tag] = up.groupby((up != up.shift()).cumsum()).cumcount() + 1
    return out


def fetch_cross_market() -> dict:
    """跨市场因子 csv（DataHub GLOBAL.* 超集）。返回 {"cols": n, "tags": [...]}"""
    frames = []
    tags = []
    for tag, code in XM_SERIES.items():
        s = _series(code)
        if s is None or len(s) < 30:
            print("  [xm] %s 取不到或太短，跳过" % code)
            continue
        frames.append(_mk_xm_feats(s, tag))
        tags.append(tag)
    if not frames:
        return {"cols": 0, "tags": [], "note": "DataHub 跨市场序列全不可用"}
    F = pd.concat(frames, axis=1).sort_index()
    F.index.name = "trade_date"
    p = os.path.join(raw_dir(), "cross_market_factors.csv")
    F.to_csv(p, date_format="%Y-%m-%d")
    print("  [xm] %d 列 / %d 个市场 → %s（%s ~ %s）"
          % (len(F.columns), len(tags), os.path.basename(p),
             F.index[0].date(), F.index[-1].date()))
    return {"cols": len(F.columns), "tags": tags, "n": len(F),
            "last": str(F.index[-1].date()),
            "missing_markets": ["a50", "hsi", "hstech", "kweb", "fxi", "cqqq"]}
