# -*- coding: utf-8 -*-
"""概念热度监控指标计算（机器人链 R1-R4 + 特斯拉线 T1-T3）

为什么需要它：26 因子（估值/成长/盈利/流动/波动/预期/机构/反转与动量）体系里，
机器人相关概念在 26 个因子上的最大绝对暴露只有 0.48（近乎零暴露），
主题概念的热度、事件驱动、产业链订单预期无法被这套截面因子刻画，
所以必须补一套「概念层」监控指标。

四个维度（全部真实可算，带数据截止日期）：
  注意力  ：同花顺概念板块热榜 hot 的 5 日热度均值的自身分位 + 近 60 交易日上榜天数占比
  价格强度：板块指数相对中证全指(000985.CSI) 的 5/20/60 日超额，20 日超额取 250 日分位
  量能拥挤：板块指数 turnover_rate / vol 的 250 日分位；成分股成交额占样本空间比的分位
  情绪强度：板块成分股近 5 日涨停家数(涨幅≥9.8%) 在 260 日分布中的分位

输出：raw/theme_monitor.csv、output/theme_monitor.csv
数据源：ths_daily(板块指数) / index_daily(基准) / ths_hot(热榜) / daily(成分股行情)
"""
import os
import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
os.makedirs(OUT, exist_ok=True)

AS_OF = "20261027"          # 概念板块指数(ths_daily)与热榜(ths_hot)最新可得交易日
HOT_DAYS = 60               # 热榜覆盖的最近交易日数


def pctile_of_last(s):
    """序列末值在自身历史中的分位（0-100）。"""
    s = pd.Series(s).dropna()
    if len(s) < 5 or s.max() == s.min():
        return np.nan
    return float((s <= s.iloc[-1]).mean() * 100)


boards = pd.read_csv(os.path.join(RAW, "theme_board_daily.csv"),
                     dtype={"trade_date": str, "board_key": str})
bench = pd.read_csv(os.path.join(RAW, "theme_bench_daily.csv"), dtype={"trade_date": str})
hot = pd.read_csv(os.path.join(RAW, "theme_hot_daily.csv"), dtype={"trade_date": str, "ts_code": str})
meta = pd.read_csv(os.path.join(RAW, "theme_boards_meta.csv"), dtype={"board": str})
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
uni = set(xs["ts_code"])

# 成分股行情：daily_260d（到 20261023）+ 增量 theme_daily_ext（20261024 起），对齐到 AS_OF
daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"ts_code": str, "trade_date": str},
                    usecols=["ts_code", "trade_date", "pct_chg", "amount"])
ext_p = os.path.join(RAW, "theme_daily_ext.csv")
if os.path.exists(ext_p):
    ext = pd.read_csv(ext_p, dtype={"ts_code": str, "trade_date": str,
                                    "pct_chg": float, "amount": float})
    daily = pd.concat([daily, ext], ignore_index=True)
daily = daily[daily["trade_date"] <= AS_OF].drop_duplicates(["ts_code", "trade_date"])
calendar = sorted(daily["trade_date"].unique())

# 基准：必须升序再算 shift，否则最近 20/60 日会因倒序而全为 NaN
bclose = bench.sort_values("trade_date").set_index("trade_date")["close"]
EX = {n: (bclose / bclose.shift(n) - 1) * 100 for n in (5, 20, 60)}

universe_amt = daily.groupby("trade_date")["amount"].sum()
big_up = (daily["pct_chg"] >= 9.8).groupby(daily["trade_date"]).sum()

hot_days = sorted(hot["trade_date"].unique())[-HOT_DAYS:]

rows = []
for r in meta.itertuples():
    key, code = r.key, r.board
    bd = boards[boards["board_key"] == key].sort_values("trade_date").reset_index(drop=True)
    for n in (5, 20, 60):
        bd[f"ret{n}"] = (bd["close"] / bd["close"].shift(n) - 1) * 100
        bd[f"ex{n}"] = bd[f"ret{n}"] - bd["trade_date"].map(EX[n])
    ex20_s = bd["ex20"].dropna().tail(250)
    last_board = bd[bd["trade_date"] <= AS_OF]["trade_date"].max()

    # ---- 成分股 ----
    mem = pd.read_csv(os.path.join(RAW, f"ths_member_{key}.csv"), dtype={"con_code": str})
    codes = sorted(set(mem["con_code"]) & uni)
    dd = daily[daily["ts_code"].isin(codes)]
    piv = dd.pivot_table(index="trade_date", columns="ts_code", values="pct_chg").tail(21)
    stk_ex20 = np.nan
    if len(piv) >= 21:
        cum = ((1 + piv.iloc[-20:] / 100).prod() - 1) * 100
        bench20 = (bclose.iloc[-1] / bclose.iloc[-21] - 1) * 100
        stk_ex20 = float(cum.mean() - bench20)
    lim = (dd["pct_chg"] >= 9.8).groupby(dd["trade_date"]).sum()
    lim = lim.reindex(calendar, fill_value=0)
    lim_roll5 = lim.rolling(5).sum()
    amt = dd.groupby("trade_date")["amount"].sum()
    shr = (amt / universe_amt).dropna()

    # ---- 热榜 ----
    h = hot[hot["ts_code"] == code].sort_values("trade_date")
    hmap_hot = h.set_index("trade_date")["hot"]
    hmap_rank = h.set_index("trade_date")["rank"]
    hser = pd.Series([hmap_hot.get(d, 0.0) for d in hot_days], index=hot_days)
    hser = pd.to_numeric(hser, errors="coerce").fillna(0)
    hroll5 = hser.rolling(5).mean()
    on_board = int((hser > 0).sum())
    latest_rank = int(hmap_rank.loc[hot_days[-1]]) if hot_days[-1] in hmap_rank.index else 99

    row = {
        "key": key, "board": code, "board_name": key.split("_", 1)[-1],
        "概念成分": int(r.概念成分), "样本内": int(r.样本内),
        "最新日": last_board,
        "hot_最新排名": latest_rank,
        "hot_最新热度": round(float(hmap_hot.get(hot_days[-1], 0.0)), 1),
        "hot_5日热度均值": round(float(hroll5.iloc[-1]), 1),
        "hot_5日均值分位": round(pctile_of_last(hroll5), 1),
        "hot_上榜天数": on_board,
        "hot_上榜占比": round(on_board / len(hot_days), 3),
        "ex5": round(float(bd[bd["trade_date"] == last_board]["ex5"].iloc[0]), 2),
        "ex20": round(float(bd[bd["trade_date"] == last_board]["ex20"].iloc[0]), 2),
        "ex60": round(float(bd[bd["trade_date"] == last_board]["ex60"].iloc[0]), 2),
        "ex20_分位": round(pctile_of_last(ex20_s), 1),
        "turn_pctile": round(pctile_of_last(bd[bd["trade_date"] <= AS_OF]["turnover_rate"].tail(250)), 1),
        "vol_pctile": round(pctile_of_last(bd[bd["trade_date"] <= AS_OF]["vol"].tail(250)), 1),
        "amt_share": round(float(shr.iloc[-1]) * 100, 2) if len(shr) else np.nan,
        "amt_share_分位": round(pctile_of_last(shr.tail(250)), 1),
        "stk_ex20": round(stk_ex20, 2) if stk_ex20 is not None else np.nan,
        "limit5": int(lim_roll5.iloc[-1]),
        "limit5_分位": round(pctile_of_last(lim_roll5.tail(260)), 1),
    }
    # 四维度子分（0-100）
    row["分_注意力"] = round(np.nanmean([row["hot_5日均值分位"], row["hot_上榜占比"] * 100]), 1)
    row["分_价格强度"] = row["ex20_分位"]
    row["分_量能拥挤"] = round(np.nanmean([row["turn_pctile"], row["amt_share_分位"]]), 1)
    row["分_情绪"] = row["limit5_分位"]
    subs = [row[k] for k in ["分_注意力", "分_价格强度", "分_量能拥挤", "分_情绪"] if pd.notna(row[k])]
    row["热度分"] = round(float(np.mean(subs)), 1) if subs else np.nan
    row["判定"] = ("很火" if row["热度分"] >= 80 else "温热" if row["热度分"] >= 60
                 else "一般" if row["热度分"] >= 40 else "偏冷")
    rows.append(row)

res = pd.DataFrame(rows).sort_values("热度分", ascending=False)
res.to_csv(os.path.join(RAW, "theme_monitor.csv"), index=False)
res.to_csv(os.path.join(OUT, "theme_monitor.csv"), index=False)

pd.set_option("display.width", 260)
print(f"数据截止 {AS_OF}｜热榜覆盖 {hot['trade_date'].nunique()} 个交易日"
      f"（{min(hot_days)}~{max(hot_days)}）｜样本空间 {len(uni)} 只｜成分股行情至 {calendar[-1]}")
print(res[["key", "最新日", "hot_最新排名", "hot_5日热度均值", "hot_上榜占比", "ex5", "ex20", "ex60",
           "ex20_分位", "turn_pctile", "amt_share_分位", "limit5", "limit5_分位",
           "分_注意力", "分_价格强度", "分_量能拥挤", "分_情绪", "热度分", "判定"]].to_string(index=False))