# -*- coding: utf-8 -*-
"""合并修正口径后的两份结果：缠论买点（含笔结构与确认状态）+ SMC 买点。"""
import os
import json

import pandas as pd
import numpy as np

WORK = os.path.dirname(os.path.abspath(__file__))
S = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
NAME = S.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()

# ---------- 缠论：最新有效买点 + 笔结构 ----------
chan_rows = []
for p in sorted(__import__("glob").glob(os.path.join(WORK, "chan_out", "*.json"))):
    code = os.path.basename(p)[:-5]
    r = json.load(open(p, encoding="utf-8"))
    d = pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % code), parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    ops = [o for o in r["交易机会"] if o["类型"] in ("B1", "B2", "B3")]
    if len(ops) == 0:
        chan_rows.append({"ts_code": code, "name": NAME.get(code, code), "缠论有买点": False})
        continue
    ops = pd.DataFrame(ops).drop_duplicates(["行号", "类型"]).sort_values("行号")
    pens = r["结构"]["笔端点"]
    pens = pd.DataFrame(pens)
    last = ops.iloc[-1]
    i = int(last["行号"])
    # 买点所在笔端点 & 之后是否已成向上笔
    ep = pens[pens.行号 == i]
    nxt = pens[pens.行号 > i]
    prev = pens[pens.行号 < i].tail(1)
    up_confirmed = len(nxt) > 0 and nxt.iloc[0]["类型"] == "顶"
    up_end = nxt.iloc[0] if up_confirmed else None
    sells = [o for o in r["交易机会"] if o["类型"] in ("S1", "S2", "S3") and int(o["行号"]) > i]
    chan_rows.append({
        "ts_code": code, "name": NAME.get(code, code), "缠论有买点": True,
        "缠论买点": last["类型"], "缠论日期": str(last["时间"])[:10], "缠论价格": last["价格"],
        "缠论距末日K线": n - 1 - i,
        "是否笔端点": bool(len(ep) and ep.iloc[0]["类型"] == "底"),
        "所在笔方向": "向下（顶→底）" if len(prev) and prev.iloc[0]["类型"] == "顶" else "—",
        "前一顶点日期": str(prev.iloc[0]["时间"])[:10] if len(prev) else "—",
        "前一顶点价": prev.iloc[0]["价格"] if len(prev) else None,
        "买点即笔底": bool(len(ep) and ep.iloc[0]["类型"] == "底"),
        "笔底价": ep.iloc[0]["价格"] if len(ep) else None,
        "向上笔已确认": up_confirmed,
        "向上笔确认日": str(up_end["时间"])[:10] if up_confirmed else "待确认",
        "确认后涨幅": (up_end["价格"] / ep.iloc[0]["价格"] - 1) if up_confirmed else np.nan,
        "此后是否出现卖点": bool(len(sells)),
        "买点总数": len(ops),
    })
chan = pd.DataFrame(chan_rows)

# 最近 30 根内、且未被卖点破坏 → 有效
_sell_after = chan["此后是否出现卖点"].fillna(False).astype(bool)   # 修正：object 列取反会变成整数，需先转 bool
chan["缠论有效"] = chan["缠论有买点"].fillna(False).astype(bool) & \
                   (chan["缠论距末日K线"].fillna(999) <= 30) & (~_sell_after)
chan.to_csv(os.path.join(WORK, "chan_live_v2.csv"), index=False, encoding="utf-8-sig")

# ---------- SMC：最新有效买点 ----------
smc = pd.read_csv(os.path.join(WORK, "smc_valid_live.csv"), dtype={"ts_code": str})
smc_latest = smc.sort_values("买点日期").groupby("ts_code").tail(1).set_index("ts_code")
smc_all = smc.groupby("ts_code").apply(
    lambda g: ";".join("%s %s" % (r.类型, r.买点日期) for _, r in g.iterrows()), include_groups=False).to_dict()

# ---------- 合并 ----------
rows = []
for c in sorted(NAME):
    ch = chan[chan.ts_code == c].iloc[0] if (chan.ts_code == c).any() else None
    sm = smc_latest.loc[c] if c in smc_latest.index else None
    hit = ("缠论+SMC" if (ch is not None and ch.缠论有效 and sm is not None)
           else ("缠论" if (ch is not None and ch.缠论有效) else ("SMC" if sm is not None else "未命中")))
    rows.append({
        "代码": c, "名称": NAME[c], "命中": hit,
        "缠论买点": ch.缠论买点 if (ch is not None and ch.缠论有效) else "—",
        "缠论日期": ch.缠论日期 if (ch is not None and ch.缠论有效) else "—",
        "缠论价": ch.缠论价格 if (ch is not None and ch.缠论有效) else None,
        "缠论笔结构": ("向下笔终点(笔底 %.2f)→向上笔%s" % (
            ch.笔底价, "已确认(%s 顶)", ) if False else
            ("向下笔终点(笔底 %.2f)，向上笔已确认(%s)" % (ch.笔底价, ch.向上笔确认日)
             if ch.向上笔已确认 else "向下笔终点(笔底 %.2f)，向上笔待确认" % ch.笔底价))
        if (ch is not None and ch.缠论有效) else "—",
        "缠论确认后涨幅": ch.确认后涨幅 if (ch is not None and ch.缠论有效) else None,
        "SMC类型": sm.类型 if sm is not None else "—",
        "SMC区间": "%.2f~%.2f" % (sm.下沿ZD, sm.上沿ZG) if sm is not None else "—",
        "SMC跌到价": sm["跌到价(触发日最低)"] if sm is not None else None,
        "SMC买点价": sm["买点价(收盘)"] if sm is not None else None,
        "SMC首次触碰日": sm.首次触碰日 if sm is not None else "—",
        "SMC距末日K线": sm.距末日K线 if sm is not None else None,
        "SMC全部买点": smc_all.get(c, "—"),
        "末日收盘": (ch.缠论价格 if False else None) or (
            ch.价格 if False else None),
    })
m = pd.DataFrame(rows)
last_close = {c: float(pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % c)).close.iloc[-1]) for c in NAME}
m["末日收盘"] = m.代码.map(last_close)
m = m.drop(columns=["末日收盘"]).assign(末日收盘=m.代码.map(last_close))
m.to_csv(os.path.join(WORK, "buypoint_merged_v2.csv"), index=False, encoding="utf-8-sig")
pd.set_option("display.width", 340)
print("命中统计:", m.命中.value_counts().to_dict())
print("\n有效缠论买点 %d 只；其中向上笔已确认 %d 只" % (
    int((chan.缠论有效).sum()), int(chan[chan.缠论有效].向上笔已确认.sum())))
print("\n逐只清单:")
print(m[["代码", "名称", "命中", "缠论买点", "缠论日期", "缠论笔结构", "SMC类型", "SMC区间", "SMC跌到价",
         "SMC买点价", "SMC首次触碰日"]].to_string(index=False))
print("\n未命中: %s" % "、".join(m[m.命中 == "未命中"].名称))