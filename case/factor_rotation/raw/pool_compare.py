# -*- coding: utf-8 -*-
"""T1 收口②：扩池前后（4 指数池 vs 全 A）同因子同权重口径的名次对照。"""
import os
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")

c4 = pd.read_csv(os.path.join(OUT, "combo_concept_rank.csv"))
cA = pd.read_csv(os.path.join(OUT, "final_fullA_concept.csv"))
m = c4[["name", "n", "s10", "r20"]].rename(columns={"n": "n_4idx", "s10": "s10_4idx", "r20": "r20_4idx"}).merge(
    cA[["name", "n", "s10", "r20"]].rename(columns={"n": "n_fullA", "s10": "s10_fullA", "r20": "r20_fullA"}),
    on="name", how="outer")
m["rk_4idx"] = m["s10_4idx"].rank(ascending=False, method="min")
m["rk_fullA"] = m["s10_fullA"].rank(ascending=False, method="min")
m["d_rank"] = m["rk_fullA"] - m["rk_4idx"]
both = m.dropna(subset=["s10_4idx", "s10_fullA"])
print(f"[概念层] 4指数池 {int(m['s10_4idx'].notna().sum())} 个 vs 全A {int(m['s10_fullA'].notna().sum())} 个；"
      f"两侧均有分 {len(both)} 个；s10 分位相关 {both['s10_4idx'].corr(both['s10_fullA']):+.3f}；"
      f"名次相关 {both['rk_4idx'].corr(both['rk_fullA']):+.3f}")
print(f"  成分数变化：4指数池合计 {int(both['n_4idx'].sum())} 只 成分 → 全A {int(both['n_fullA'].sum())} 只成分"
      f"（概念成员为板块成分，非池内成员）")
TARGETS = ["电机", "AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉", "短剧游戏",
           "文化传媒概念", "虚拟数字人", "网络游戏", "手机游戏", "光刻机", "存储芯片"]
print("\n--- 目标概念：扩池前后 s10 名次（133 个概念内）---")
for nm in TARGETS:
    r = m[m["name"] == nm]
    if not len(r):
        print(f"  {nm:14s} 两侧均无")
        continue
    r = r.iloc[0]
    print(f"  {nm:14s} 4指数池 #{r['rk_4idx']:>5.0f} → 全A #{r['rk_fullA']:>5.0f}（Δ{r['d_rank']:+.0f}）"
          f"  近20日 4指数 {r['r20_4idx']:+.2%} / 全A {r['r20_fullA']:+.2%}")
m.sort_values("rk_fullA", kind="mergesort").to_csv(os.path.join(OUT, "pool_compare_concept.csv"), index=False)

s4 = pd.read_csv(os.path.join(OUT, "combo_stock_rank.csv"), dtype={"ts_code": str})
sA = pd.read_csv(os.path.join(OUT, "final_fullA_stock.csv"), dtype={"ts_code": str})
s4["rk_4idx"] = s4["s10"].rank(ascending=False, method="min")
sA["rk_fullA"] = sA["s10"].rank(ascending=False, method="min")
sv = s4[["ts_code", "name", "s10", "rk_4idx"]].merge(
    sA[["ts_code", "name", "s10", "rk_fullA"]], on="ts_code", how="outer",
    suffixes=("_4idx", "_fullA"))
print(f"\n[个股层] 4指数池榜单 {len(s4)} 行 vs 全A ≥30亿 {len(sA)} 行；"
      f"交集 {len(s4[s4['ts_code'].isin(set(sA['ts_code']))])} 只")
WATCH4 = ["文投控股", "盛天网络", "德才股份", "芒果超媒", "襄阳轴承", "上纬新材", "杰克科技",
          "华依科技", "光阳股份", "华阳智能", "兆威机电", "鸣志电器", "光线传媒"]
print("--- 目标个股在两张榜单中的存在性 ---")
for nm in WATCH4:
    a = s4[s4["name"] == nm]
    b = sA[sA["name"] == nm]
    sa = f"#{int(a.iloc[0]['rk_4idx'])}/{len(s4)}" if len(a) else "不在4指数池榜单"
    sb = f"#{int(b.iloc[0]['rk_fullA'])}/{len(sA)}" if len(b) else "不在全A榜"
    print(f"  {nm:8s} 4指数池 {sa:>16s}   全A {sb:>16s}")
sv.to_csv(os.path.join(OUT, "pool_compare_stock.csv"), index=False)
print("[done]")