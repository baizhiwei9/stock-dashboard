# -*- coding: utf-8 -*-
"""探测：板块中期强度(40日) + 板块资金流(20日净流入强度) 能否把
   电机 / AI视频 等热门方向带进概念层前 10。诊断用，不做前瞻。"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "close", "pct_chg", "amount"],
                    dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "close", "pct_chg", "amount"],
                  dtype={"ts_code": str, "trade_date": str})
daily = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
for c in ["close", "pct_chg", "amount"]:
    daily[c] = pd.to_numeric(daily[c], errors="coerce")
dates = sorted(daily["trade_date"].unique())
pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)

mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"), dtype={"ts_code": str, "trade_date": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
mfmat = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last")
mfmat = mfmat.reindex(dates).sort_index(axis=1)

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
print(f"[members] boards={len(con_members)}")

STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in pct.columns})
UPTO = len(dates) - 1


def cum(n):
    seg = pct.iloc[max(0, UPTO - n + 1): UPTO + 1]
    return (1 + seg).prod(skipna=True) - 1


r20, r40, r60 = cum(20), cum(40), cum(60)
obs = pct.notna().sum()
tradable = (obs >= 120) & amt.iloc[UPTO].notna() & (amt.iloc[UPTO] > 0)
tradable &= ~STmask.reindex(pct.columns).fillna(False)
pool = [t for t in pct.columns if bool(tradable.get(t, False))]
print(f"[pool] 全 A 可交易 {len(pool)}；全 A 等权 r20={r20.reindex(pool).mean():+.2%} r40={r40.reindex(pool).mean():+.2%}")

m5 = mfmat.iloc[max(0, UPTO - 4): UPTO + 1].sum()
m20 = mfmat.iloc[max(0, UPTO - 19): UPTO + 1].sum()
as5 = amt.iloc[max(0, UPTO - 4): UPTO + 1].sum()
as20 = amt.iloc[max(0, UPTO - 19): UPTO + 1].sum()
mf5 = m5 * 10.0 / as5.replace(0, np.nan)
mf20 = m20 * 10.0 / as20.replace(0, np.nan)

rows = []
for bc, codes in con_members.items():
    cc = [c for c in codes if c in set(pool)]
    if len(cc) < 5:
        continue
    rows.append({"board": bc, "name": con_name.get(bc, bc), "n": len(cc),
                 "str40": r40.reindex(cc).mean(), "str20": r20.reindex(cc).mean(),
                 "str60": r60.reindex(cc).mean(), "mf20": mf20.reindex(cc).mean(),
                 "mf5": mf5.reindex(cc).mean()})
b = pd.DataFrame(rows)
for c in ["str40", "str20", "str60", "mf20", "mf5"]:
    b["rk_" + c] = b[c].rank(ascending=False, method="min")
b["n_board"] = len(b)
print(f"[boards] {len(b)} 概念（成分≥5 在池）")


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


b["cmb_mf"] = z(b["str40"]) + z(b["mf20"])
b["cmb_mf2"] = z(b["str40"]) + z(b["mf20"]) + z(b["str20"])
b["rk_cmb_mf"] = b["cmb_mf"].rank(ascending=False, method="min")
b["rk_cmb_mf2"] = b["cmb_mf2"].rank(ascending=False, method="min")

TGT = ["电机", "AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉", "短剧游戏", "文化传媒概念",
       "网络游戏", "手机游戏", "虚拟数字人", "存储芯片", "光刻机", "先进封装", "CPO"]
print(f"\n=== 概念层名次（共 {len(b)} 个，{dates[UPTO]}）===")
show = b[b["name"].isin(TGT)][["name", "n", "str40", "rk_str40", "str20", "rk_str20", "str60",
                               "mf20", "rk_mf20", "rk_cmb_mf", "rk_cmb_mf2"]]
print(show.to_string(index=False, formatters={"str40": "{:+.2%}".format, "str20": "{:+.2%}".format,
                                              "str60": "{:+.2%}".format, "mf20": "{:+.3f}".format}))
print("\n  str40 Top15：")
print(b.sort_values("str40", ascending=False, kind="mergesort").head(15)[
    ["name", "str40", "rk_str40", "rk_mf20", "rk_cmb_mf"]].to_string(
    index=False, formatters={"str40": "{:+.2%}".format}))
print("\n  mf20 Top15：")
print(b.sort_values("mf20", ascending=False, kind="mergesort").head(15)[
    ["name", "mf20", "rk_str40", "rk_mf20", "rk_cmb_mf"]].to_string(
    index=False, formatters={"mf20": "{:+.3f}".format}))
print("\n  cmb_mf(str40+mf20) Top15：")
print(b.sort_values("cmb_mf", ascending=False, kind="mergesort").head(15)[
    ["name", "str40", "mf20", "rk_cmb_mf"]].to_string(
    index=False, formatters={"str40": "{:+.2%}".format, "mf20": "{:+.3f}".format}))

b.to_csv(os.path.join(OUT, "board40_probe.csv"), index=False)
print("[done] -> output/board40_probe.csv")