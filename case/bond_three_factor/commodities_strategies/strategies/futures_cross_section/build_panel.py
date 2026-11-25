# -*- coding: utf-8 -*-
"""构建品种-日期面板：近月/次近月/更远月合约结构、主力合约、连续收益。

合约规则（冻结口径）：
- 到期时间 mat（年）= (最后交易日 − 交易日)/365.25，最后交易日取 fut_basic.delist_date；
- 剔除 mat ≤ 0.02 年（约 7 天，进入交割月流动性枯竭）的合约；
- rk1..rk4 = 按 mat 升序的近月/次近月/第三/第四合约（合成价用结算价 settle，缺失回退 close）；
- 主力 = 当日持仓量 oi 最大的合约；
- 近月持有腿 = rk≤4 中当日持仓量最大的合约（即主力；中国商品期货近月活跃合约即主力，纯到期最近合约常近乎零成交）。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from .common import DATA, RAW, log

def px(df):
    s = df["settle"].where(df["settle"] > 0, df["close"])
    return s.where(s > 0, np.nan)

def load():
    merged_pq = os.path.join(DATA, "fut_daily_all.parquet")
    merged = os.path.join(DATA, "fut_daily_all.csv")
    if os.path.exists(merged_pq):
        d = pd.read_parquet(merged_pq)
        d["trade_date"] = d["trade_date"].astype(str)
    elif os.path.exists(merged):
        d = pd.read_csv(merged, dtype={"trade_date": str})
    else:      # 取数未完成时直接用分片文件，便于冒烟测试
        fs = sorted(p for p in os.listdir(RAW) if p.startswith("fut_daily_") and p.endswith(".csv"))
        d = pd.concat([pd.read_csv(os.path.join(RAW, f), dtype={"trade_date": str}) for f in fs],
                      ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    b = pd.read_csv(os.path.join(DATA, "fut_basic.csv"))
    b["variety"] = b["ts_code"].str.extract(r"^([A-Za-z]+)")[0].str.upper()
    b["delist"] = pd.to_datetime(b["delist_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    b["listdt"] = pd.to_datetime(b["list_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    _dm = pd.to_datetime(b["d_month"].astype(str).str.slice(0, 6), format="%Y%m", errors="coerce")
    b["expiry"] = b["delist"].fillna(_dm + pd.offsets.MonthEnd(0) - pd.Timedelta(days=10))
    d["variety"] = d["ts_code"].str.extract(r"^([A-Za-z]+)")[0].str.upper()
    d = d[d["ts_code"].str.match(r"^[A-Za-z]{1,3}\d{3,5}\.", na=False)]      # 仅真实月份合约
    d = d.merge(b[["ts_code", "expiry", "listdt", "multiplier", "name"]].drop_duplicates("ts_code"),
                on="ts_code", how="left")
    d["date"] = pd.to_datetime(d["trade_date"])
    d["mat"] = (d["expiry"] - d["date"]).dt.days / 365.25
    d["px"] = px(d)
    d = d[(d["mat"] > 0.02) & d["px"].notna()].copy()
    return d, b

def main():
    d, b = load()
    log("valid rows", len(d), "varieties", d["variety"].nunique(), "dates", d["date"].nunique())
    log("amount sample", d[["amount", "vol", "oi"]].describe().loc[["min", "50%", "max"]].to_dict())

    # 合约内收益
    d = d.sort_values(["variety", "ts_code", "date"])
    d["ret_c"] = d.groupby(["variety", "ts_code"])["px"].pct_change()

    # 每品种每日：最大成交额、总持仓
    g = d.groupby(["variety", "date"])
    d["amt_max"] = g["amount"].transform("max")
    d["oi_total"] = g["oi"].transform("sum")
    d["n_live"] = g["ts_code"].transform("size")

    # 近月排序
    d = d.sort_values(["variety", "date", "mat"])
    d["rk"] = d.groupby(["variety", "date"]).cumcount() + 1
    front = d[d["rk"] <= 4].copy()

    def _pv(val):
        t = front.pivot_table(index=["variety", "date"], columns="rk", values=val, aggfunc="first")
        t.columns = [f"{val}{int(c)}" for c in t.columns]
        return t
    idx = pd.concat([_pv(v) for v in ["settle", "close", "px", "mat", "oi", "amount"]], axis=1).reset_index()

    # 近月持有腿：近四档合约中持仓量最大者。中国商品期货近月活跃合约即主力（01/05/10 系列），
    # 纯粹"到期最近"的合约常近乎零成交。换月：日内持有仍为前一日合约（收盘换月），
    # 故用前一日合约的当日收益拼接连续序列，避免换月日收益缺失。
    fr = d[d["rk"] <= 4].copy()
    fr = fr.sort_values(["variety", "date", "oi"], ascending=[True, True, False])
    held = fr.groupby(["variety", "date"], as_index=False).first()[
        ["variety", "date", "ts_code", "px", "ret_c", "mat", "amount", "oi"]]
    held = held.rename(columns={"ts_code": "near_code", "px": "near_px", "mat": "near_mat",
                                "amount": "near_amt", "oi": "near_oi"})
    held["prev_code"] = held.groupby("variety")["near_code"].shift(1)
    rt = d[["variety", "date", "ts_code", "ret_c"]].rename(
        columns={"ts_code": "prev_code", "ret_c": "ret_prev"})
    held = held.merge(rt, on=["variety", "date", "prev_code"], how="left")
    held["ret_near"] = held["ret_prev"].fillna(held["ret_c"])
    held = held.drop(columns=["ret_c"])

    main_c = d.loc[d.groupby(["variety", "date"])["oi"].idxmax(),
                   ["variety", "date", "ts_code", "px", "ret_c", "mat"]]
    main_c = main_c.rename(columns={"ts_code": "main_code", "px": "main_px",
                                    "ret_c": "ret_main", "mat": "main_mat"})

    total = d.groupby(["variety", "date"], as_index=False).agg(
        oi_total=("oi", "sum"), amt_total=("amount", "sum"), n_live=("ts_code", "size"))
    f1 = d[d["rk"] == 1][["variety", "date", "oi", "amount", "px"]].rename(
        columns={"oi": "oi_front", "amount": "amt_front", "px": "px_front"})

    p = (idx.merge(held, on=["variety", "date"], how="left")
            .merge(main_c, on=["variety", "date"], how="left")
            .merge(total, on=["variety", "date"], how="left")
            .merge(f1, on=["variety", "date"], how="left"))
    p["hp_front"] = p["oi1"] / p["oi_total"].replace(0, np.nan)
    p = p.sort_values(["variety", "date"]).reset_index(drop=True)
    p.to_parquet(os.path.join(DATA, "panel.parquet"), index=False)
    log("panel rows", len(p), "cols", len(p.columns))

    # 流动性/在册概况（用于品种池筛选）
    last3 = p[p["date"] >= "2023-01-01"]
    liq = last3.groupby("variety").agg(
        days=("date", "nunique"),
        amt_med=("amt_total", "median"),
        oi_med=("oi_total", "median"),
        first_date=("date", "min"), last_date=("date", "max")).reset_index()
    allperiod = p.groupby("variety").agg(first_all=("date", "min"), days_all=("date", "nunique")).reset_index()
    liq = liq.merge(allperiod, on="variety", how="left")
    liq.to_csv(os.path.join(DATA, "universe_liq.csv"), index=False)
    log("liquidity table", len(liq))
    log(liq.sort_values("amt_med", ascending=False).head(45).to_string(index=False))

if __name__ == "__main__":
    main()