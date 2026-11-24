# -*- coding: utf-8 -*-
"""SMC 买点判定（自定义规则，供与缠论结果并列）并合并命中清单。

规则（明确口径，非套件内既有能力）：
  1. 结构突破 BOS(多头)：收盘价 > 前 20 根（不含当日）最高价的最大值。
  2. 冲击腿区间 [t-20, t]：取其中最后一根阴线（close<open）的 [low, high] 为订单块 OB；
     取其中最后一个 3 根 K 线缺口 low[i+1] > high[i-1] 的 [high[i-1], low[i+1]] 为看涨失衡 FVG。
  3. 回踩买点：BOS 后 20 根内出现某根 K 线 low 触及区间上沿、收盘未跌破区间下沿、且为阳线（close>open）；
     且自 BOS 以来未出现收盘跌破区间下沿的破坏。
"""
import os
import glob
import numpy as np
import pandas as pd

WORK = os.path.dirname(os.path.abspath(__file__))
OUT_WINDOW = 30        # 与缠论口径一致的"最近有效窗口"（K线根数）
s = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
NAME = s.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()

rows = []
for p in sorted(glob.glob(os.path.join(WORK, "chan_in", "*.csv"))):
    code = os.path.basename(p)[:-4]
    d = pd.read_csv(p, parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    hi, lo, op, cl = d.high.values, d.low.values, d.open.values, d.close.values
    bos_events = []
    for t in range(21, n):
        if cl[t] > hi[t - 20:t].max() and cl[t - 1] <= hi[t - 21:t - 1].max():
            bos_events.append(t)
    # 去重：连续多日突破只保留首次
    ded = []
    for t in bos_events:
        if not ded or t - ded[-1] > 5:
            ded.append(t)
    for t in ded:
        seg = range(max(0, t - 20), t + 1)
        ob = None
        for i in reversed(list(seg)):
            if cl[i] < op[i]:
                ob = (lo[i], hi[i], i)
                break
        fvg = None
        for i in reversed(list(range(max(1, t - 19), t + 1))):
            if lo[i] > hi[i - 2]:
                fvg = (hi[i - 2], lo[i], i)
                break
        for zone, kind in ((ob, "OB"), (fvg, "FVG")):
            if zone is None:
                continue
            zd, zg, zbar = zone
            broken = False
            for u in range(t + 1, min(n, t + 21)):
                if cl[u] < zd:
                    broken = True
                    break
                if lo[u] <= zg and cl[u] > zd and cl[u] > op[u]:
                    rows.append({"ts_code": code, "name": NAME.get(code, code), "SMC类型": kind,
                                 "BOS行号": t, "BOS日期": str(d.date[t].date()),
                                 "买点行号": u, "买点日期": str(d.date[u].date()), "买点价格": float(cl[u]),
                                 "区间下沿ZD": round(float(zd), 4), "区间上沿ZG": round(float(zg), 4),
                                 "距末日K线": n - 1 - u, "末日收盘": float(cl[-1])})
                    break
smc = pd.DataFrame(rows)
if len(smc):
    smc = smc.sort_values(["ts_code", "买点行号"]).drop_duplicates(["ts_code", "买点行号", "SMC类型"])
smc.to_csv(os.path.join(WORK, "smc_buypoints.csv"), index=False, encoding="utf-8-sig")
print("SMC 买点合计 %d 条，涉及 %d 只" % (len(smc), smc.ts_code.nunique() if len(smc) else 0))
live = smc[smc.距末日K线 <= OUT_WINDOW] if len(smc) else smc
print("\n最近 %d 根内 SMC 买点：%d 条，涉及 %d 只" % (OUT_WINDOW, len(live), live.ts_code.nunique() if len(live) else 0))
if len(live):
    print(live.sort_values("距末日K线").to_string(index=False))

# ---- 合并命中清单 ----
chan = pd.read_csv(os.path.join(WORK, "chan_latest_buypoint.csv"), dtype={"ts_code": str})
chan_live = chan[chan.当前有效][["ts_code", "name", "最新买点类型", "买点日期", "买点价格", "距末日K线", "末日收盘"]]
chan_live = chan_live.rename(columns={"最新买点类型": "缠论买点"})
smc_live = live[["ts_code", "SMC类型", "买点日期", "买点价格", "距末日K线"]].rename(
    columns={"买点日期": "SMC买点日期", "买点价格": "SMC买点价格"})
keys = sorted(set(chan_live.ts_code) | set(smc_live.ts_code))
allname = {**NAME, **dict(zip(chan.name, chan.name))}
m = pd.DataFrame({"ts_code": keys})
m["name"] = m.ts_code.map(lambda c: NAME.get(c, c))
cl = chan_live.groupby("ts_code").agg(缠论买点=("缠论买点", lambda x: "、".join(x)),
                                      缠论日期=("买点日期", "max"), 缠论价格=("买点价格", "last"),
                                      缠论距末日K线=("距末日K线", "min"))
sl = smc_live.groupby("ts_code").agg(SMC类型=("SMC类型", lambda x: "、".join(x)),
                                     SMC日期=("SMC买点日期", "max"), SMC价格=("SMC买点价格", "last"),
                                     SMC距末日K线=("距末日K线", "min"))
m = m.merge(cl, on="ts_code", how="left").merge(sl, on="ts_code", how="left")
m["命中来源"] = np.where(m.缠论买点.notna() & m.SMC类型.notna(), "缠论+SMC",
                        np.where(m.缠论买点.notna(), "缠论", np.where(m.SMC类型.notna(), "SMC", "未命中")))
m["末日收盘"] = m.ts_code.map(lambda c: float(pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % c)).close.iloc[-1]))
m = m.sort_values(["命中来源", "ts_code"])
m.to_csv(os.path.join(WORK, "buypoint_merged.csv"), index=False, encoding="utf-8-sig")
pd.set_option("display.width", 300)
print("\n合并命中清单（全部 33 只）:")
print(m[["ts_code", "name", "命中来源", "缠论买点", "缠论日期", "SMC类型", "SMC日期", "末日收盘"]].to_string(index=False))
print("\n命中来源统计:", m.命中来源.value_counts().to_dict())