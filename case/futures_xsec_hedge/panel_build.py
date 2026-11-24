# -*- coding: utf-8 -*-
"""从 all_kline.csv 构建 panel：main(oi最大)/near(最近到期月)/next(次近月)/roll/basis/atr/ret_hold
与既有 data/norm/panel.csv 同口径（D 按品种），输出 data/full/panel_full.csv"""
import pandas as pd, numpy as np, math, sys

D_CONST = {"AP":30.0,"C":30.5,"CF":30.5,"EB":31.0,"EG":31.0,"JD":31.0,"L":31.0,
           "M":30.5,"MA":31.0,"OI":30.5,"P":31.0,"PP":31.0,"RM":30.5,"SA":31.0,
           "SR":30.5,"TA":31.0,"UR":31.0,"V":31.0,"Y":30.5}
CHEM = {"MA","TA","SA","UR","V","PP","L","EG","EB"}

def month_id(c):  # "MA2409" -> 2297
    return 2000 + int(c[-4:-2])*12 + int(c[-2:])

k = pd.read_csv("data/full/all_kline.csv", parse_dates=["date"])
k = k.sort_values(["variety","contract","date"]).reset_index(drop=True)
k["m"] = k["contract"].map(month_id)

rows = []
for v, g in k.groupby("variety"):
    g = g.sort_values("date")
    dates = np.sort(g["date"].unique())
    prev_main, prev_close = None, None
    # 每日该品种所有有数据的合约
    for d in dates:
        kd = g[g["date"] == d]
        if len(kd) < 2:          # 至少主力+近月 2 合约即可输出主连行（否则远月缺失日整行被跳过）
            continue
        kd = kd.sort_values("m")
        # main = oi 最大
        main_i = kd["oi"].idxmax()
        main_c = kd.loc[main_i, "contract"]; main_close = kd.loc[main_i, "close"]
        main_hi = kd.loc[main_i, "high"]; main_lo = kd.loc[main_i, "low"]
        main_oi = kd.loc[main_i, "oi"]
        # near = 到期月最小（若有数据）
        near = kd.iloc[0]; next_ = kd.iloc[1]
        dm = next_["m"] - near["m"]
        if dm <= 0:
            continue
        D = D_CONST[v]
        if near["close"] > 0 and next_["close"] > 0:
            roll = math.log(near["close"]/next_["close"]) * 365.0 / (dm * D)
            basis = (near["close"] - main_close) / main_close
        else:
            roll, basis = np.nan, np.nan
        near_c, near_close = near["contract"], near["close"]
        rows.append({"date": d, "variety": v, "main": main_c, "main_close": main_close,
                     "main_high": main_hi, "main_low": main_lo, "main_oi": main_oi,
                     "roll_annual": roll, "basis_rate": basis, "near_c": near_c,
                     "near_close": near_close, "prev_main": prev_main})
        prev_main, prev_close = main_c, main_close

panel = pd.DataFrame(rows)
panel = panel.sort_values(["variety","date"]).reset_index(drop=True)

# ret / ret_hold（主力日收益；ret_hold 遇主力切换用新的主力收益，跳空按原逻辑：对照既有 panel 的 ret_hold 定义）
# 既有 panel: ret = main_close 日收益；ret_hold: 主力切换日 ret_hold = ret 本身（无跳空修正？看示例：prev_main=main 时等同）
panel["ret"] = panel.groupby("variety")["main_close"].pct_change()
# 对照既有实现：ret_hold = (main_close - prev_main_close)/prev_main_close 若 prev_main 相同，否则用当日主力收益
panel["prev_close"] = panel.groupby("variety")["main_close"].shift(1)
panel["ret_hold"] = panel["ret"]
# 换月日：prev_main != main，跳空直接吃 new 主力收益（与示例 ret_hold=ret 一致时即为 pct_change）
panel.drop(columns=["prev_close"], inplace=True)

# ATR20（用 main 的 high/low/close，Wilder）
def atr20(g):
    hc = (g["main_high"] - g["main_close"]).abs()
    lc = (g["main_low"] - g["main_close"]).abs()
    hl = (g["main_high"] - g["main_low"]).abs()
    tr = pd.concat([hc, lc, hl], axis=1).max(axis=1)
    return tr.rolling(20, min_periods=20).mean()
panel["atr20"] = panel.groupby("variety", group_keys=False).apply(atr20)

panel.to_csv("data/full/panel_full.csv", index=False)
print("panel_full rows:", len(panel), "date:", panel.date.min(), "->", panel.date.max())
print("每品种覆盖:", panel.groupby("variety")["date"].agg(["min","max","count"]).to_string())
