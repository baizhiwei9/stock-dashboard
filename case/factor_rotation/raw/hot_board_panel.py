# -*- coding: utf-8 -*-
"""赛道层（概念/行业/主题）盘面与资金流面板，用于检验「前10是否覆盖电机与AI视频」。

全部口径：2026-10-29 收盘、全 A 可交易池（obs>=120 个交易日、当日有收盘与成交、非 ST/退）。
板块指标：
  r5 / r20 / r60  成分等权近 5/20/60 交易日收益
  brel20 / brel60 相对全 A 等权的超额（百分点）
  mf5 / mf20      成分净流入强度 = Σ(net_mf_amount 万元×10) / Σ(amount 千元)  近 5 / 20 日
                  （与 cand_factor_panel.py 的 mf_net5 定义一致：万元→千元 / 千元→无量纲）
综合分候选（截面 z 标准化后等权相加）：
  S1 = z(brel20)+z(brel60)+z(mf5)
  S2 = z(brel20)+z(brel60)+z(mf5)+z(mf20)
  S3 = z(brel20)+z(mf5)
  S4 = z(brel20)+z(brel60)+z(mf5)+z(brel20·板块内个股风格)  —— 见脚本内注释
输出：output/hot_board_panel.csv（全部板块）+ 控制台诊断
"""
import ast
import os
import sys

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
upto = dates.index("20261029")
print(f"[daily] dates={len(dates)} {dates[0]}..{dates[-1]}  upto={dates[upto]} idx={upto}", flush=True)

close = daily.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index(axis=1)
pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)

mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"), dtype={"ts_code": str, "trade_date": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
mfmat = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").sort_index(axis=1)
mfmat = mfmat.reindex(dates)
print(f"[moneyflow] dates={mf['trade_date'].nunique()} {mf['trade_date'].min()}..{mf['trade_date'].max()}", flush=True)

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()

# 从既有脚本抽取 THEME_MAP，避免重复维护
src = open(os.path.join(RAW, "final_pick_fullA.py"), encoding="utf-8").read()
tree = ast.parse(src)
THEME_MAP = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "THEME_MAP" for t in node.targets):
        THEME_MAP = ast.literal_eval(node.value)
assert THEME_MAP, "THEME_MAP not found"
name2code = {v: k for k, v in con_name.items()}


def cum(n):
    seg = pct.iloc[max(0, upto - n + 1): upto + 1]
    return (1 + seg).prod(skipna=True) - 1


r5, r20, r60 = cum(5), cum(20), cum(60)
obs = pct.iloc[:upto + 1].notna().sum()
STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in close.columns})
tradable = (obs >= 120) & close.iloc[upto].notna() & (amt.iloc[upto] > 0)
tradable &= ~STmask.reindex(close.columns).fillna(False)
pool = tradable[tradable].index.tolist()
poolset = set(pool)

m5 = mfmat.iloc[max(0, upto - 4): upto + 1].sum()
m20 = mfmat.iloc[max(0, upto - 19): upto + 1].sum()
as5 = amt.iloc[max(0, upto - 4): upto + 1].sum()
as20 = amt.iloc[max(0, upto - 19): upto + 1].sum()

xs = pd.DataFrame({"r5": r5, "r20": r20, "r60": r60,
                   "mf5": m5 * 10.0 / as5.replace(0, np.nan),
                   "mf20": m20 * 10.0 / as20.replace(0, np.nan)}).reindex(pool)
xs["行业"] = pd.Series({t: IND.get(t) for t in xs.index})
print(f"[pool] 全 A 可交易池 {len(pool)} 只 | 全 A 等权 r5={xs['r5'].mean():+.2%} r20={xs['r20'].mean():+.2%} r60={xs['r60'].mean():+.2%}"
      f" | mf5 中位 {xs['mf5'].median():+.3f}", flush=True)
MKT = {"r5": xs["r5"].mean(), "r20": xs["r20"].mean(), "r60": xs["r60"].mean()}


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


mk_ind = {}
for c, i in IND.items():
    if isinstance(i, str):
        mk_ind.setdefault(i, []).append(c)
mk_the = {}
for th, bnames in THEME_MAP.items():
    codes = []
    for b in bnames:
        if b in name2code:
            codes += con_members[name2code[b]]
    mk_the[th] = sorted(set(codes))

recs = []


def add_layer(keys, names, min_n, layer):
    for k, codes in keys.items():
        cc = [c for c in codes if c in poolset]
        if len(cc) < min_n:
            continue
        sub = xs.loc[cc]
        rec = {"layer": layer, "key": k, "name": names.get(k, k), "n": len(cc),
               "r5": float(sub["r5"].mean()), "r20": float(sub["r20"].mean()), "r60": float(sub["r60"].mean()),
               "mf5": float(sub["mf5"].mean(skipna=True)), "mf20": float(sub["mf20"].mean(skipna=True)),
               "mf5_pos": float((sub["mf5"] > 0).mean()), "cov_pool": len(cc)}
        rec["brel20"] = rec["r20"] - MKT["r20"]
        rec["brel60"] = rec["r60"] - MKT["r60"]
        recs.append(rec)


add_layer(mk_ind, {k: k for k in mk_ind}, 8, "行业")
add_layer(con_members, con_name, 5, "概念")
add_layer(mk_the, {k: k for k in mk_the}, 8, "主题")

bp = pd.DataFrame(recs)
gp = bp.groupby("layer")


def add_scores(g):
    g = g.copy()
    for c in ["brel20", "brel60", "mf5", "mf20", "r20", "r60"]:
        g["z_" + c] = (g[c] - g[c].mean()) / (g[c].std() + 1e-12)
    g["S1"] = g["z_brel20"] + g["z_brel60"] + g["z_mf5"]
    g["S2"] = g["z_brel20"] + g["z_brel60"] + g["z_mf5"] + g["z_mf20"]
    g["S3"] = g["z_brel20"] + g["z_mf5"]
    g["S4"] = g["z_brel20"] + g["z_brel60"] + g["z_mf5"] + g["z_mf20"] + g["z_brel60"] * 0  # 占位
    for s in ["S1", "S2", "S3"]:
        g["rk_" + s] = g[s].rank(ascending=False, method="min")
    g["rk_r20"] = g["r20"].rank(ascending=False, method="min")
    g["rk_r60"] = g["r60"].rank(ascending=False, method="min")
    g["rk_mf5"] = g["mf5"].rank(ascending=False, method="min")
    return g


bp = gp.apply(add_scores, include_groups=False).reset_index()
bp.to_csv(os.path.join(OUT, "hot_board_panel.csv"), index=False)
print(f"[panel] rows={len(bp)} layers={bp.layer.unique().tolist()}", flush=True)

TARGETS = ["电机", "AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉", "短剧游戏", "文化传媒概念", "特斯拉概念"]
pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 60)

for lay in ["概念", "行业", "主题"]:
    g = bp[bp["layer"] == lay]
    N = len(g)
    print(f"\n########## {lay}层 N={N} ##########")
    for s in ["S1", "S2", "S3"]:
        t = g.sort_values("rk_" + s, kind="mergesort").head(12)
        print(f"\n  --- {s} Top12 ---")
        print(t[["name", "n", "r5", "r20", "r60", "mf5", "rk_S1" if s == "S1" else "rk_" + s]]
              .to_string(index=False, formatters={"r5": "{:+.2%}".format, "r20": "{:+.2%}".format,
                                                  "r60": "{:+.2%}".format, "mf5": "{:+.3f}".format}))
    print("\n  --- 目标板块名次（各口径）---")
    for nm in TARGETS:
        r = g[g["name"] == nm]
        if not len(r):
            continue
        r = r.iloc[0]
        print(f"  {nm:10s} n={int(r['n']):>3d} r5={r['r5']:+.2%} r20={r['r20']:+.2%} r60={r['r60']:+.2%} "
              f"mf5={r['mf5']:+.3f} | 名次 r20={int(r['rk_r20'])}/{N} r60={int(r['rk_r60'])}/{N} mf5={int(r['rk_mf5'])}/{N} "
              f"S1={int(r['rk_S1'])} S2={int(r['rk_S2'])} S3={int(r['rk_S3'])}")
    print("\n  --- Top10 / 目标是否命中 ---")
    for s in ["S1", "S2", "S3"]:
        top = g.sort_values("rk_" + s, kind="mergesort").head(10)["name"].tolist()
        hit = [nm for nm in ["电机", "AI视频"] if nm in top]
        print(f"  {s} Top10: {', '.join(top)}\n     命中 -> {hit if hit else '无'}")
print("\n[done]")