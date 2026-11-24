# -*- coding: utf-8 -*-
"""概念内代表股选股（机器人链 R1-R4 + 特斯拉线 T1-T3）

为什么要它：概念监控只看板块层面"火不火"不够，还要落到"哪几只股票"——
但用户明确要求面向用户呈现股票名称，不要发代码，因此：
    raw/theme_picks.csv    内部用（含 ts_code，供交叉核对）
    output/theme_picks.csv 面向用户（只含股票名称，无任何代码）

选股口径（可复现）：
  1) 样本 = 概念成分股 ∩ 样本空间（沪深300∪中证500∪中证1000∪中证A500 并集）
  2) 剔除 ST/退市/次新（名称含 ST、退、N）
  3) 近 5 日涨幅、近 20 日涨幅（按日 pct_chg 复利累计）、近 5 日日均成交额
  4) 流动性门槛：近 5 日日均成交额 ≥ 概念内中位数；按近 20 日涨幅降序取前 5 只（领涨股）
  5) 数据截止：成分股行情最新交易日（AS_OF）
输出：raw/theme_picks.csv（含代码）、output/theme_picks.csv（仅名称）
"""
import os, sys
import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
sys.path.insert(0, RAW)
from ts_session import api  # noqa: E402

TOPN = 5
THEMES = {
    "人形机器人": "ths_member_R1_人形机器人.csv",
    "机器人概念": "ths_member_R2_机器人概念.csv",
    "减速器": "ths_member_R3_减速器.csv",
    "机器视觉": "ths_member_R4_机器视觉.csv",
    "特斯拉概念": "ths_member_T1_特斯拉概念.csv",
    "一体化压铸": "ths_member_T2_一体化压铸.csv",
    "无人驾驶": "ths_member_T3_无人驾驶.csv",
}

# ---------- 1. 行情 ----------
daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"ts_code": str, "trade_date": str},
                    usecols=["ts_code", "trade_date", "pct_chg", "amount"])
ext_p = os.path.join(RAW, "theme_daily_ext.csv")
if os.path.exists(ext_p):
    ext = pd.read_csv(ext_p, dtype={"ts_code": str, "trade_date": str})[["ts_code", "trade_date", "pct_chg", "amount"]]
    daily = pd.concat([daily, ext], ignore_index=True)
daily = daily.drop_duplicates(["ts_code", "trade_date"]).sort_values(["ts_code", "trade_date"])
AS_OF = str(daily["trade_date"].max())

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
uni = set(xs["ts_code"])
mv = dict(zip(xs["ts_code"], pd.to_numeric(xs["总市值"], errors="coerce") / 1e4))  # 万元→亿元

# ---------- 2. 股票名称 ----------
basic_p = os.path.join(RAW, "stock_basic.csv")
if os.path.exists(basic_p):
    basic = pd.read_csv(basic_p, dtype={"ts_code": str, "symbol": str})
else:
    basic = api("stock_basic", exchange="", list_status="L", fields="ts_code,symbol,name,industry,market")
    basic.to_csv(basic_p, index=False)
    print(f"[stock_basic] 已缓存 {basic.shape}")
NAME = dict(zip(basic["ts_code"], basic["name"]))
IND = dict(zip(basic["ts_code"], basic.get("industry", pd.Series(dtype=object))))

# ---------- 3. 指标 ----------
g = daily.groupby("ts_code")
m5 = g["pct_chg"].apply(lambda s: s.tail(5))
m20 = g["pct_chg"].apply(lambda s: s.tail(20))
ret5 = m5.groupby(level=0).apply(lambda s: (np.prod(1 + s / 100.0) - 1) * 100)
ret20 = m20.groupby(level=0).apply(lambda s: (np.prod(1 + s / 100.0) - 1) * 100)
amt5 = g["amount"].apply(lambda s: s.tail(5).mean() / 1e4)      # 千元→亿元
day_ret = g["pct_chg"].last()
lim5 = m5.groupby(level=0).apply(lambda s: int((s >= 9.8).sum()))

met = pd.DataFrame({"ret5": ret5, "ret20": ret20, "amt5亿": amt5, "最新日涨跌幅": day_ret, "近5日涨停": lim5})
met.index.name = "ts_code"
met = met.reset_index()

rows = []
for theme, fn in THEMES.items():
    codes = set(pd.read_csv(os.path.join(RAW, fn), dtype={"con_code": str})["con_code"]) & uni
    d = met[met["ts_code"].isin(codes)].copy()
    d["股票名称"] = d["ts_code"].map(NAME)
    d = d[d["股票名称"].notna()]
    d = d[~d["股票名称"].str.contains("ST|退|^N", regex=True, na=False)]
    d["总市值亿"] = d["ts_code"].map(mv)
    # 流动性门槛：近 5 日日均成交额不低于概念内中位数（避免选出无成交的标的）
    amt_med = d["amt5亿"].median()
    d = d[d["amt5亿"] >= amt_med]
    d["排名_涨幅"] = d["ret20"].rank(ascending=False, method="min")
    d["排名_成交额"] = d["amt5亿"].rank(ascending=False, method="min")
    d["综合分"] = d["排名_涨幅"]          # 主排序＝近 20 日涨幅（领涨属性）
    d = d.sort_values("ret20", ascending=False).head(TOPN).reset_index(drop=True)
    d["概念"] = theme
    d["概念内可用个股数"] = len(codes)
    rows.append(d)
    print(f"[{theme}] 概念内可用 {len(codes)} 只（流动性门槛后 {len(d)}+）→ 选出 {len(d)} 只: "
          + "、".join(d["股票名称"].tolist()), flush=True)

picks = pd.concat(rows, ignore_index=True)
picks["数据截止"] = AS_OF
cols = ["概念", "股票名称", "ts_code", "最新日涨跌幅", "ret5", "ret20", "近5日涨停",
        "amt5亿", "总市值亿", "排名_涨幅", "排名_成交额", "综合分", "概念内可用个股数", "数据截止"]
picks["最新日涨跌幅"] = picks["最新日涨跌幅"].round(2)
picks["ret5"] = picks["ret5"].round(2)
picks["ret20"] = picks["ret20"].round(2)
picks["amt5亿"] = picks["amt5亿"].round(3)
picks["总市值亿"] = picks["总市值亿"].round(1)
internal = picks[cols].rename(columns={"ret5": "近5日涨幅%", "ret20": "近20日涨幅%", "amt5亿": "近5日日均成交额亿"})
internal.to_csv(os.path.join(RAW, "theme_picks.csv"), index=False)

user_cols = ["概念", "股票名称", "最新日涨跌幅", "近5日涨幅%", "近20日涨幅%", "近5日涨停",
             "近5日日均成交额亿", "总市值亿", "数据截止"]
internal[user_cols].to_csv(os.path.join(OUT, "theme_picks.csv"), index=False)
print(f"\n[OK] 数据截止 {AS_OF}；raw/theme_picks.csv（含代码，内部用）、output/theme_picks.csv（仅名称，面向用户）")
print("输出列：", user_cols)