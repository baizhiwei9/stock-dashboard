# -*- coding: utf-8 -*-
"""T3 校验：目标命中与区分度（全部读本轮脚本产物，不新造数字）。"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
pd.set_option("display.width", 300)

stk = pd.read_csv(os.path.join(OUT, "final_fullA_stock.csv"), dtype={"ts_code": str})
stk["rk_s10"] = stk["s10"].rank(ascending=False, method="min")
stk["rk_w7"] = stk["w7"].rank(ascending=False, method="min")
N = len(stk)
print(f"[池] 全 A 个股池(≥30亿) {N} 只；近20日等权 {stk['ret20'].mean():+.2%}")

print("\n########## 一、区分度：s10 vs w7 ##########")
for c in ("s10", "w7"):
    print(f"  {c}: 与近20日收益相关 {stk[c].corr(stk['ret20']):+.3f}；"
          f"Top50 覆盖行业数 {stk.nlargest(50, c)['行业'].nunique()}/"
          f"{stk['行业'].nunique()}；Top50 中位市值 {stk.nlargest(50, c)['总市值_亿'].median():.0f}亿；"
          f"Top50 平均近20日 {stk.nlargest(50, c)['ret20'].mean():+.2%}")

print("\n  --- 十分位单调性（同期，仅说明口径方向，非前瞻证据）---")
for c in ("s10", "w7"):
    d = stk.dropna(subset=[c]).copy()
    d["dec"] = pd.qcut(d[c].rank(ascending=False, method="first"), 10, labels=False) + 1
    g = d.groupby("dec")["ret20"].mean()
    print(f"  {c}: D1 {g.get(1, np.nan):+.2%} / D5 {g.get(5, np.nan):+.2%} / D10 {g.get(10, np.nan):+.2%}")

print("\n########## 二、目标个股在个股总榜的名次迁移 ##########")
WATCH = ["文投控股", "盛天网络", "德才股份", "芒果超媒", "光线传媒", "中国电影", "果麦文化",
         "襄阳轴承", "上纬新材", "杰克科技", "华依科技", "福莱新材", "光洋股份", "华阳智能",
         "大洋电机", "兆威机电", "鸣志电器", "奥普特", "慈星股份", "横店影视"]
sub = stk[stk["name"].isin(WATCH)].copy().sort_values("rk_w7", kind="mergesort")
print(sub[["name", "行业", "总市值_亿", "rk_s10", "rk_w7", "s10", "w7", "ret20"]]
      .to_string(index=False, formatters={"rk_s10": "{:.0f}".format, "rk_w7": "{:.0f}".format,
                                          "s10": "{:.3f}".format, "w7": "{:.3f}".format,
                                          "ret20": "{:+.2%}".format, "总市值_亿": "{:.0f}".format}))
miss = [w for w in WATCH if w not in set(stk["name"])]
print(f"  不在池（≥30亿）: {miss if miss else '无'}")

print("\n########## 三、赛道内选股产物核对 ##########")
ts = pd.read_csv(os.path.join(OUT, "target_sector_stocks.csv"), dtype={"ts_code": str})
for sec in ts["赛道"].unique():
    m = ts[ts["赛道"] == sec]
    top10 = m.nsmallest(10, "rk_new_in")
    print(f"  {sec}: 成分 {len(m)}；new_raw Top10 平均近20日 {top10['ret20'].mean():+.2%}"
          f"；赛道整体 {m['ret20'].mean():+.2%}"
          f"；Top10 ∩ 赛道内涨幅Top10 {len(set(top10['ts_code']) & set(m.nsmallest(10, 'rk_ret20_in')['ts_code']))}/10")
    print(f"      名单: {'、'.join(top10['name'].tolist())}")

print("\n########## 四、点名概念龙头产物核对 ##########")
nb = pd.read_csv(os.path.join(OUT, "named_board_stocks.csv"), dtype={"ts_code": str})
for nm in ["电机", "AI视频", "短剧游戏", "减速器", "虚拟数字人"]:
    m = nb[nb["板块"] == nm]
    t = m.nsmallest(8, "rk_new_in")
    print(f"  {nm}({len(m)}只): {'、'.join(t['name'].tolist())}")
    print(f"      对应近20日: {'、'.join(f'{r:.1%}' for r in t['ret20'].tolist())}")

print("\n########## 五、前瞻证据（来自 cand_factor_eval.csv，32 周）##########")
ev = pd.read_csv(os.path.join(OUT, "cand_factor_eval.csv"))
sel = ["board_str20_con", "rs_con", "accel_5_20", "trend_c1", "trend_c2", "ret20", "vol_ratio"]
print(ev[ev["factor"].isin(sel)][["factor", "weeks", "ls_mean", "ls_win", "ls_t", "ic_mean"]]
      .to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                          "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format}))
print("[done]")