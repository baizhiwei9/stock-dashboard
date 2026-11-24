# -*- coding: utf-8 -*-
"""T3：对 114 只待检股票按「修正版 SMC 口径」逐只识别有效买点区间。

规则（沿用本会话已确立的修正口径）：
  1) BOS：收盘价 > 前 20 根最高价最大值，且前一交易日未突破；同一波只留首次（间隔 > 5 根）。
  2) 候选区间：OB = BOS 前最后一根阴线的 [low, high]；FVG = 三根K线缺口 low[i] > high[i-2] 的 [high[i-2], low[i]]。
  3) 新鲜度：自「区间形成后的下一根」起检查，只认第一次触碰；一经触碰即消耗，同一区间不再产生买点。
  4) 失效：出现收盘 < 下沿（结构破坏）或最低价 <= 下沿（缺口被完整填补）→ 区间失效。
  5) 触发：首次触碰那根须 low 落在 (下沿, 上沿]、收盘 > 下沿、且收阳。
"""
import os, glob
import pandas as pd
import numpy as np

WORK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
pool = pd.read_csv(WORK + "/chan_smc_pool.csv", dtype={"ts_code": str})
NAME = pool.drop_duplicates("ts_code").set_index("ts_code")["名称"].to_dict()

recs = []
for p in sorted(glob.glob(os.path.join(WORK, "chan_in_pool", "*.csv"))):
    code = os.path.basename(p)[:-4]
    d = pd.read_csv(p, parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    hi, lo, cl, op = d.high.values, d.low.values, d.close.values, d.open.values
    bos = []
    for t in range(21, n):
        if cl[t] > hi[t-20:t].max() and cl[t-1] <= hi[t-21:t-1].max():
            if not bos or t - bos[-1] > 5:
                bos.append(t)
    for t in bos:
        seg = list(range(max(0, t - 5), t + 1))
        ob = None
        for i in reversed(seg):
            if cl[i] < op[i]:
                ob = ("OB", lo[i], hi[i], i); break
        fvg = None
        for i in reversed(list(range(max(1, t - 5), t + 1))):
            if lo[i] > hi[i-2]:
                fvg = ("FVG", hi[i-2], lo[i], i); break
        for zone in (fvg, ob):
            if zone is None:
                continue
            kind, zd, zg, zbar = zone
            start = zbar + 1
            filled_at, first_touch, invalid_at = None, None, None
            for u in range(start, n):
                if cl[u] < zd:
                    invalid_at = u; break
                if lo[u] <= zd:
                    filled_at = u; break
                if lo[u] <= zg:
                    first_touch = u; break
            trig = None
            if first_touch is not None and invalid_at is None and filled_at is None:
                u = first_touch
                if cl[u] > zd and cl[u] > op[u]:
                    trig = u
            recs.append({
                "ts_code": code, "名称": NAME.get(code, code), "SMC类型": kind,
                "BOS日期": str(d.date[t].date()), "区间形成日": str(d.date[zbar].date()),
                "区间下沿ZD": round(float(zd), 4), "区间上沿ZG": round(float(zg), 4),
                "首次触碰日": str(d.date[first_touch].date()) if first_touch is not None else "",
                "被完整填补": filled_at is not None, "收盘破下沿": invalid_at is not None,
                "有效买点": trig is not None,
                "买点日期": str(d.date[trig].date()) if trig is not None else "",
                "跌到价(触发日最低)": float(lo[trig]) if trig is not None else None,
                "买点价(收盘)": float(cl[trig]) if trig is not None else None,
                "距末日K线": (n - 1 - trig) if trig is not None else None,
                "末日收盘": float(cl[-1]),
            })
df = pd.DataFrame(recs)
df.to_csv(WORK + "/smc_pool_zones.csv", index=False, encoding="utf-8-sig")
live = df[df["有效买点"] & (df["距末日K线"] <= 30)]
live.to_csv(WORK + "/smc_pool_live.csv", index=False, encoding="utf-8-sig")
print("区间候选 %d 条（覆盖 %d 只）；其中有效买点 %d 条（%d 只）" % (
    len(df), df.ts_code.nunique(), int(df["有效买点"].sum()), df[df["有效买点"]].ts_code.nunique()))
print("最近 30 个交易日内仍有有效买点：%d 条 / %d 只" % (len(live), live.ts_code.nunique()))
print("失效原因分布：被完整填补 %d / 收盘破下沿 %d / 首次触碰不满足触发 %d" % (
    int(df["被完整填补"].sum()), int(df["收盘破下沿"].sum()),
    int(((~df["有效买点"]) & (~df["被完整填补"]) & (~df["收盘破下沿"])).sum())))
print("按类型：", df[df["有效买点"]]["SMC类型"].value_counts().to_dict())
print("落盘 smc_pool_zones.csv / smc_pool_live.csv")