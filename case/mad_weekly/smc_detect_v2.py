# -*- coding: utf-8 -*-
"""修正版 SMC 判定：FVG／订单块加「新鲜度 + 是否已被完整填补 + 一次性使用」校验。

规则（修正点见 ①②③）：
  1. 结构突破 BOS：收盘价 > 前 20 根最高价的最大值（去重：同一波只留首次）。
  2. 区间候选：订单块 OB = BOS 前最后一根阴线的 [low, high]；看涨失衡 FVG = 三根K线缺口
     low[i] > high[i-2] 的 [high[i-2], low[i]]（取 BOS 前最近的一个）。
  ③ 失效判定（新增）：自区间形成到触发之前，若出现收盘价 < 区间下沿 → 区间失效（结构破坏）；
     若出现最低价 <= 区间下沿 → 缺口被完整填补，区间同样失效，不再产生买点。
  ① 新鲜度（新增）：自「区间形成后的下一根K线」起检查，只认第一次触碰；一经触碰即视为消耗，
     同一区间此后不再产生任何买点（首次触碰若不满足触发条件，该区间亦不再计买点）。
  ② 触发条件（沿用并加严）：首次触碰那根 K 线须满足 low 落在 (下沿, 上沿] 区间内（未击穿下沿）、
     收盘 > 下沿、且收阳；若该根最低价 <= 下沿则属"直接填满"，不计买点。
"""
import os
import glob
import json

import pandas as pd
import numpy as np

WORK = os.path.dirname(os.path.abspath(__file__))
s = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
NAME = s.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()

recs = []
for p in sorted(glob.glob(os.path.join(WORK, "chan_in", "*.csv"))):
    code = os.path.basename(p)[:-4]
    d = pd.read_csv(p, parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    hi, lo, cl, op = d.high.values, d.low.values, d.close.values, d.open.values
    # BOS：20 根新高，去重（间隔 > 5）
    bos = []
    for t in range(21, n):
        if cl[t] > hi[t - 20:t].max() and cl[t - 1] <= hi[t - 21:t - 1].max():
            if not bos or t - bos[-1] > 5:
                bos.append(t)
    for t in bos:
        seg = list(range(max(0, t - 5), t + 1))
        ob = None
        for i in reversed(seg):
            if cl[i] < op[i]:
                ob = ("OB", lo[i], hi[i], i)
                break
        fvg = None
        for i in reversed(list(range(max(1, t - 5), t + 1))):
            if lo[i] > hi[i - 2]:
                fvg = ("FVG", hi[i - 2], lo[i], i)
                break
        for zone in (fvg, ob):
            if zone is None:
                continue
            kind, zd, zg, zbar = zone
            start = zbar + 1          # 修正：新鲜度检查自「区间形成后的下一根」开始，而非 BOS 之后
            filled_at, first_touch, invalid_at = None, None, None
            for u in range(start, n):
                if cl[u] < zd:                       # ③ 收盘跌破下沿 → 结构破坏
                    invalid_at = u
                    break
                if lo[u] <= zd:                      # ③ 最低价触及/穿透下沿 → 缺口被完整填补
                    filled_at = u
                    break
                if lo[u] <= zg:                      # ① 首次触碰（进入区间但未填满）
                    first_touch = u
                    break
            trig = None
            if first_touch is not None and invalid_at is None and filled_at is None:
                u = first_touch
                if cl[u] > zd and cl[u] > op[u]:
                    trig = u
            recs.append({
                "ts_code": code, "name": NAME.get(code, code), "类型": kind,
                "BOS行号": t, "BOS日期": str(d.date[t].date()),
                "区间形成行号": zbar, "区间形成日": str(d.date[zbar].date()),
                "下沿ZD": round(float(zd), 4), "上沿ZG": round(float(zg), 4),
                "首次触碰行号": first_touch, "首次触碰日": str(d.date[first_touch].date()) if first_touch is not None else None,
                "被完整填补": filled_at is not None, "填补行号": filled_at,
                "填补日": str(d.date[filled_at].date()) if filled_at is not None else None,
                "收盘破下沿": invalid_at is not None, "破位行号": invalid_at,
                "修正后仍成立": trig is not None, "买点行号": trig,
                "买点日期": str(d.date[trig].date()) if trig is not None else None,
                "买点价(收盘)": float(cl[trig]) if trig is not None else None,
                "跌到价(触发日最低)": float(lo[trig]) if trig is not None else None,
                "距末日K线": (n - 1 - trig) if trig is not None else None,
                "末日收盘": float(cl[-1]),
            })
df = pd.DataFrame(recs)
df.to_csv(os.path.join(WORK, "smc_zones_v2.csv"), index=False, encoding="utf-8-sig")
print("修正后区间候选总数:", len(df), "| 仍成立(有效买点):", int(df.修正后仍成立.sum()))

old = pd.read_csv(os.path.join(WORK, "smc_buypoints.csv"), dtype={"ts_code": str})
old_live = old[old.距末日K线 <= 30]
print("上一版口径: 最近30根内买点 %d 条 / %d 只" % (len(old_live), old_live.ts_code.nunique()))
new_live = df[df.修正后仍成立 & (df.距末日K线 <= 30)]
print("修正后口径: 最近30根内买点 %d 条 / %d 只" % (len(new_live), new_live.ts_code.nunique()))
print("\n按标的对比（上一版 vs 修正后，最近30根内）:")
old_set = set(map(tuple, old_live[["ts_code", "SMC类型", "买点日期"]].values))
new_set = set(map(tuple, new_live[["ts_code", "类型", "买点日期"]].values))
for c in sorted(set(old_live.ts_code) | set(new_live.ts_code)):
    o = [x for x in old_set if x[0] == c]
    m = [x for x in new_set if x[0] == c]
    tag = "保留" if o and m else ("仅修正后" if m else "被剔除")
    print("  %-6s %-8s 上一版 %-28s 修正后 %-28s %s" % (
        c, NAME.get(c, c), (";".join("%s %s" % (x[1], x[2]) for x in o) or "—"),
        (";".join("%s %s" % (x[1], x[2]) for x in m) or "—"), tag))
pd.set_option("display.width", 320)
print("\n修正后仍有效买点明细（最近 30 根内）:")
print(new_live.sort_values("距末日K线")[["ts_code", "name", "类型", "BOS日期", "区间形成日", "下沿ZD", "上沿ZG",
                                          "首次触碰日", "跌到价(触发日最低)", "买点价(收盘)", "距末日K线"]].to_string(index=False))
print("\n被剔除的买点及其原因:")
drop = df[(~df.修正后仍成立) & (df.BOS行号 > 0)]
drop = drop[(df.被完整填补.fillna(False)) | (df.收盘破下沿.fillna(False))]
print(drop.groupby(["被完整填补", "收盘破下沿"]).size().to_string())