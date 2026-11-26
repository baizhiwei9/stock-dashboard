# -*- coding: utf-8 -*-
"""A股 5 标的 VVIX（波动率的波动率）—— CBOE VVIX 同源思路在「无波动率期权」条件下的等价实现

口径（本文件即口径说明，2026-10-30 定稿）：
  · 波动率指数 IV：券商 QVIX(30日) 序列（300ETF / 500ETF / 科创50 / 创业板），
    来自 data/qvix_*.json（fetch_qvix.py 经 akshare 刷新，科创50 序列自 20230605 起有效）；
    中证1000 无官方 QVIX，退化为 MO 股指期权当月平价 ATM 隐含波动率
    （data/ashare_chain_metrics.json 的 iv_near，属「期货期权替代」口径）。
  · 波动率的波动率：VVIX_t = 100 × √252 × std( ln(IV_t / IV_{t-1}), 最近 30 个交易日, ddof=1 )
    即「波动率指数对数收益的 30 交易日滚动年化标准差」，单位 = 年化波动点数，与 CBOE VVIX 同量纲。
  · 与 CBOE VVIX 的差异：CBOE 用 VIX 期权链、以与 VIX 相同的无模型方差法（方差互换原理）
    计算 VIX 未来 30 天的隐含波动率（前瞻口径）；A股不存在波动率期权，
    故以「波动率指数自身的 30 日滚动实现波动率」等价替代（回望口径）。
    二者趋势与相对高低可比，绝对水平不得与 CBOE VVIX 直接混用。

输入：data/qvix_{300etf,500etf,kcb,cyb}.json、data/ashare_chain_metrics.json
输出：data/vvix_ashare.json（逐标的 IV/VVIX 序列 + 最新截面 + 一年分位）
可用 TRADE_DATE=YYYYMMDD 截断数据日（14:30 未收盘时回落口径与主链路一致）。
"""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

WIN_VV = 30     # VVIX 滚动窗口（交易日），对齐 CBOE 30 天期限
ANN = 252       # 年化因子
WIN = 252       # 分位窗口（一年交易日）

TARGETS = [
    ("300ETF", "沪深300ETF期权(510300)", "qvix_300etf.json", "券商 QVIX(30日)"),
    ("500ETF", "中证500ETF期权(510500)", "qvix_500etf.json", "券商 QVIX(30日)"),
    ("KC50",   "科创50ETF期权(588000)",  "qvix_kcb.json",   "券商 QVIX(30日)"),
    ("CYB",    "创业板ETF期权(159915)",  "qvix_cyb.json",   "券商 QVIX(30日)"),
    ("ZZ1000", "中证1000股指期权(MO 000852)", None,          "MO 当月平价 ATM-IV 自算（期货期权替代）"),
]


def _dates():
    return os.environ.get("TRADE_DATE") or None


def load_qvix(fn):
    """QVIX: [{date(ms), open/high/low/close}] -> pd.Series(index=YYYYMMDD, close)"""
    q = pd.read_json(os.path.join(DATA, fn), orient="records")
    q["d"] = pd.to_datetime(q["date"], unit="ms").dt.strftime("%Y%m%d")
    q = q.dropna(subset=["close"]).drop_duplicates("d").sort_values("d")
    return pd.Series(q["close"].astype(float).values, index=q["d"].values)


def load_zz1000():
    """中证1000: MO 当月 ATM 隐含波动率（iv_near，小数）-> 百分比序列"""
    m = pd.read_json(os.path.join(DATA, "ashare_chain_metrics.json"), orient="records")
    m = m[m["target"] == "ZZ1000"].copy()
    m["d"] = m["date"].astype(str)
    m = m.dropna(subset=["iv_near"]).drop_duplicates("d").sort_values("d")
    return pd.Series((m["iv_near"].astype(float) * 100).values, index=m["d"].values)


def vvix(iv):
    r = np.log(iv / iv.shift(1))
    vv = r.rolling(WIN_VV).std(ddof=1) * np.sqrt(ANN) * 100.0
    return vv.dropna()


def pct_rank(arr, val):
    a = np.asarray([x for x in arr if x is not None and np.isfinite(x)], dtype=float)
    if not len(a) or val is None or not np.isfinite(val):
        return None
    return float((a <= val).sum() / len(a) * 100)


def main():
    cut = _dates()
    out = {}
    for key, label, fn, src in TARGETS:
        try:
            iv = load_qvix(fn) if fn else load_zz1000()
        except Exception as e:
            print("[warn] %-7s 读取失败: %s" % (key, str(e)[:120]))
            continue
        if cut:
            iv = iv[iv.index <= str(cut)]
        vv = vvix(iv)
        if not len(vv):
            print("[warn] %-7s 样本不足，跳过" % key)
            continue
        ser = [dict(d=d, iv=round(float(iv[d]), 4), vvix=round(float(v), 4))
               for d, v in vv.items() if d in iv.index]
        w = vv.tail(WIN)
        cur = float(vv.iloc[-1])
        pct = pct_rank(w.tolist(), cur)
        out[key] = dict(
            label=label, iv_source=src,
            window=WIN_VV, ann=ANN,
            start=ser[0]["d"], end=ser[-1]["d"], n=len(ser),
            iv=ser[-1]["iv"], vvix=round(cur, 2),
            pct=round(pct, 1) if pct is not None else None, n_win=len(w),
            p90=round(float(np.percentile(w, 90)), 2),
            med=round(float(np.median(w)), 2),
            series=ser,
        )
        print("  %-7s %s  IV=%.2f  VVIX=%.2f  一年分位=%.1f%%  (P90=%.2f, 中位=%.2f, n=%d, %s~%s)"
              % (key, ser[-1]["d"], ser[-1]["iv"], cur, pct or 0.0,
                 out[key]["p90"], out[key]["med"], len(ser), ser[0]["d"], ser[-1]["d"]))

    if not out:
        raise SystemExit("vvix_ashare: 未算出任何标的")
    p = os.path.join(DATA, "vvix_ashare.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved", p)


if __name__ == "__main__":
    main()