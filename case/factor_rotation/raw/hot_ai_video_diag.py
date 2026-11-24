# -*- coding: utf-8 -*-
"""近期强势方向核查 + 剔除反转因子的榜单重算（v2）。

收益口径：板块/行业收益 = 全市场成员等权平均（daily 全量，基准日 20261029）
因子口径：得分 = 横截面（截面 20261028，1823 只指数并集池）方向调整分位加权，要求篮子因子全有值
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
XSEC_DATE, LAST_DATE = "20261028", "20261029"
MIN_INDUSTRY_N, MIN_THEME_N = 10, 5
MIN_MV = 300000.0  # 万元 = 30 亿
pd.set_option("display.width", 200)

# ---------- 1. 基础数据 ----------
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
fr = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
DIRMAP = fr.drop_duplicates("factor").set_index("factor")["direction"].to_dict()
EXC = fr.groupby("factor")["excess_vs_benchmark"].mean().to_dict()   # 小数（0.0048 = 0.48%）

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "close"], dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "close"], dtype={"ts_code": str, "trade_date": str})
px = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
px["close"] = pd.to_numeric(px["close"], errors="coerce")
px = px.sort_values(["ts_code", "trade_date"], kind="mergesort")
dates = np.sort(px["trade_date"].unique())
last = dates[-1]
pv = px[px["trade_date"].isin(dates[-21:])].pivot_table(
    index="ts_code", columns="trade_date", values="close", aggfunc="last").sort_index(axis=1)
ret = pd.DataFrame({"close_last": pv.iloc[:, -1],
                    "r3": pv.iloc[:, -1] / pv.iloc[:, -4] - 1.0,
                    "r20": pv.iloc[:, -1] / pv.iloc[:, -21] - 1.0}).reset_index().dropna(subset=["r20"])
print(f"[px] dates {dates[0]}..{dates[-1]}  收益基准日={last}  全市场有收益股票={len(ret)}")
print(f"[基准] 全市场等权 近3日={ret['r3'].mean():+.2%}  近20日={ret['r20'].mean():+.2%}"
      f"  中位 近3日={ret['r3'].median():+.2%}  近20日={ret['r20'].median():+.2%}")

# ---------- 2. 因子得分 ----------
def pct_rank(s):
    return s.rank(pct=True, method="average")

BASKET_A = {"三个月反转": 5, "EPTTM一年分位点": 4, "一个月反转": 3, "预期PEG": 2, "公募重仓": 1}
REM = ["EPTTM一年分位点", "预期PEG", "公募重仓"]
BASKET_C = {"预期EPTTM": 0.228, "预期净利润环比": 0.210, "单季净利同比增速": 0.205,
            "单季EP": 0.201, "单季ROE": 0.156}
BASKET_BM = {f: EXC[f] / sum(EXC[g] for g in REM) for f in REM}
BASKET_BE = {f: 1.0 for f in REM}
print("[剩余因子周均超额] " + " | ".join(f"{f} {EXC[f]*100:+.4f}%" for f in REM))
print("[B_M 字面权重] " + " | ".join(f"{f} {w:+.4f}" for f, w in BASKET_BM.items())
      + f"  Σw={sum(BASKET_BM.values()):+.4f}  Σ|w|={sum(abs(v) for v in BASKET_BM.values()):.4f}")

NEED = sorted(set(REM) | set(BASKET_C))
base = xs[["ts_code", "总市值"] + NEED].copy()
FACS = {}
for nm, w in [("A", BASKET_A), ("BM", BASKET_BM), ("BE", BASKET_BE), ("C", BASKET_C)]:
    facs = list(w)
    d = xs.dropna(subset=facs).copy()
    tot = float(sum(abs(v) for v in w.values()))
    d["score"] = 0.0
    for f, v in w.items():
        pr = pct_rank(d[f])
        d["score"] += (v / tot) * (pr if DIRMAP.get(f, 1) > 0 else (1.0 - pr))
    FACS[nm] = d[["ts_code", "score"]].rename(columns={"score": f"score_{nm}"})
    base = base.merge(FACS[nm], on="ts_code", how="left")
    print(f"[score_{nm}] 因子全有值 {len(d)} / {len(xs)}")

pool = base.merge(ret, on="ts_code", how="left")
pool_mv = pool[pool["总市值"] >= MIN_MV]

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype={"ts_code": str})
NAME = sb.set_index("ts_code")["name"].to_dict()
ind_map = {}
for _t, _i in sb.set_index("ts_code")["industry"].to_dict().items():
    if isinstance(_i, str):
        ind_map.setdefault(_i, []).append(_t)

tm1 = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str)
tm2 = pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)
tm = pd.concat([tm1, tm2], ignore_index=True).drop_duplicates(["board_code", "con_code"])
th_key, th_name = {}, {}
for (bc, bn), gdf in tm.groupby(["board_code", "board_name"]):
    th_key[bc] = gdf["con_code"].tolist(); th_name[bc] = bn
print(f"[theme] boards={len(th_key)} members={len(tm)}")

# ---------- 3. 聚合 ----------
def agg(keys, names, min_n):
    rows = []
    for k, codes in keys.items():
        sub = ret[ret["ts_code"].isin(codes)]
        if len(sub) < min_n:
            continue
        s = pool_mv[pool_mv["ts_code"].isin(codes)]
        r = {"key": k, "name": names.get(k, k), "n": len(sub),
             "r3": float(sub["r3"].mean()), "r20": float(sub["r20"].mean()),
             "n_xsec": int(s["score_C"].notna().sum())}
        for nm in ["A", "BM", "BE", "C"]:
            v = s[f"score_{nm}"].mean(skipna=True)
            r[f"s{nm}"] = None if pd.isna(v) else float(v)
        rows.append(r)
    return pd.DataFrame(rows)

id_names = {k: k for k in ind_map}
ind = agg(ind_map, id_names, MIN_INDUSTRY_N)
th = agg(th_key, th_name, MIN_THEME_N)
ind.to_csv(os.path.join(OUT, "hot_industry_rank.csv"), index=False)
th.to_csv(os.path.join(OUT, "hot_board_rank.csv"), index=False)
print(f"[agg] 行业 {len(ind)} 个（成员≥{MIN_INDUSTRY_N}）  主题 {len(th)} 个（成员≥{MIN_THEME_N}）")
print(f"[基准] 全行业平均 近3日={ind['r3'].mean():+.2%} 近20日={ind['r20'].mean():+.2%}"
      f"   全主题平均 近3日={th['r3'].mean():+.2%} 近20日={th['r20'].mean():+.2%}")

# ---------- 4. 榜单 ----------
def lists(df, tag):
    d = df.dropna(subset=[tag]).sort_values(tag, ascending=False, kind="mergesort")
    return d

def stk_list(tag):
    col = "score_BM" if tag == "BM" else "score_C"
    facs = list(BASKET_BM if tag == "BM" else BASKET_C)
    d = pool_mv.dropna(subset=facs).sort_values(col, ascending=False, kind="mergesort").copy()
    d["name"] = d["ts_code"].map(NAME)
    return d

ind_bm, ind_c = lists(ind, "sBM"), lists(ind, "sC")
th_bm, th_c = lists(th, "sBM"), lists(th, "sC")
sk_bm, sk_c = stk_list("BM"), stk_list("C")

FMT = {"r3": "{:+.2%}".format, "r20": "{:+.2%}".format, "sBM": "{:.3f}".format, "sC": "{:.3f}".format}
for tag, a, b, st in [("BM", ind_bm, th_bm, sk_bm), ("C", ind_c, th_c, sk_c)]:
    print(f"\n########## 剔除反转口径 {tag} ##########")
    print(f"--- 行业 Top12（成员≥{MIN_INDUSTRY_N}，收益全市场成员等权）---")
    print(a[["name", "n", "r3", "r20", "n_xsec"]].head(12).to_string(index=False, formatters=FMT))
    print(f"--- 主题 Top15（成员≥{MIN_THEME_N}）---")
    print(b[["name", "n", "r3", "r20", "n_xsec"]].head(15).to_string(index=False, formatters=FMT))
    print(f"--- 个股 Top20（横截面池，市值≥30亿，全部子因子有值 n={len(st)}）---")
    print(st[["ts_code", "name", "score_" + tag, "r3", "r20"]].head(20).to_string(
        index=False, formatters={"score_" + tag: "{:.4f}".format, "r3": "{:+.2%}".format, "r20": "{:+.2%}".format}))
    print(f"[对照] 选择后组合表现：行业Top10 近3日={a.head(10)['r3'].mean():+.2%} 近20日={a.head(10)['r20'].mean():+.2%}"
          f" | 主题Top10 近3日={b.head(10)['r3'].mean():+.2%} 近20日={b.head(10)['r20'].mean():+.2%}"
          f" | 个股Top20 近3日={st.head(20)['r3'].mean():+.2%} 近20日={st.head(20)['r20'].mean():+.2%}")

ind_bm[["name", "n", "r3", "r20", "sBM", "sC"]].head(20).to_csv(os.path.join(OUT, "droprev_industry_top.csv"), index=False)
th_bm[["name", "n", "r3", "r20", "sBM", "sC"]].head(20).to_csv(os.path.join(OUT, "droprev_theme_top.csv"), index=False)
sk_bm[["ts_code", "name", "score_BM", "score_C", "r3", "r20", "总市值"]].head(20).to_csv(os.path.join(OUT, "droprev_stock_top.csv"), index=False)
ind_c[["name", "n", "r3", "r20", "sBM", "sC"]].head(20).to_csv(os.path.join(OUT, "basketc_industry_top.csv"), index=False)
th_c[["name", "n", "r3", "r20", "sBM", "sC"]].head(20).to_csv(os.path.join(OUT, "basketc_theme_top.csv"), index=False)
sk_c[["ts_code", "name", "score_C", "score_BM", "r3", "r20", "总市值"]].head(20).to_csv(os.path.join(OUT, "basketc_stock_top.csv"), index=False)

# ---------- 5. 真正在走强的方向 ----------
print("\n########## 近3日真正上涨的方向 ##########")
upth = th.sort_values("r3", ascending=False, kind="mergesort")
print("--- 主题近3日 Top15 ---")
print(upth[["name", "n", "r3", "r20"]].head(15).to_string(index=False, formatters=FMT))
upi = ind.sort_values("r3", ascending=False, kind="mergesort")
print("--- 行业近3日 Top12 ---")
print(upi[["name", "n", "r3", "r20"]].head(12).to_string(index=False, formatters=FMT))
print("--- 主题近20日 Top15 ---")
print(th.sort_values("r20", ascending=False, kind="mergesort")[["name", "n", "r3", "r20"]].head(15).to_string(index=False, formatters=FMT))

# ---------- 6. AI 视频 / 传媒内容 ----------
AI = ["AI视频", "多模态AI", "AIGC概念", "短剧游戏", "虚拟数字人", "IP经济(谷子经济)",
      "文化传媒概念", "云游戏", "元宇宙", "网络游戏", "手机游戏", "广告营销", "出版业", "影视院线"]
ai = th[th["name"].isin(AI)].sort_values("r20", ascending=False, kind="mergesort")
print("\n########## AI视频 / 传媒内容方向 ##########")
print(ai[["name", "n", "r3", "r20", "n_xsec", "sC", "sBM", "sA"]].to_string(index=False,
      formatters={**FMT, "sA": "{:.3f}".format}))
ai.to_csv(os.path.join(OUT, "hot_ai_board_detail.csv"), index=False)

bc = [k for k, v in th_name.items() if v == "AI视频"][0]
codes = th_key[bc]
sub = ret[ret["ts_code"].isin(codes)].copy()
sub["name"] = sub["ts_code"].map(NAME)
sup = sub.merge(pool[["ts_code", "score_C", "score_BM", "score_A"]], on="ts_code", how="left")
sup = sup.sort_values("r20", ascending=False, kind="mergesort")
print(f"\n--- AI视频板块（{th_name[bc]}，成分 {len(codes)} 只；横截面覆盖 {sup['score_C'].notna().sum()} 只）近20日 Top15 ---")
print(sup[["ts_code", "name", "r3", "r20", "score_C", "score_BM"]].head(15).to_string(index=False,
      formatters={"r3": "{:+.2%}".format, "r20": "{:+.2%}".format, "score_C": "{:.3f}".format, "score_BM": "{:.3f}".format}))
sup.to_csv(os.path.join(OUT, "hot_ai_video_members.csv"), index=False)

ai_all = sorted(set(sum([th_key[k] for k, v in th_name.items() if v in AI], [])))
sa = ret[ret["ts_code"].isin(ai_all)].copy(); sa["name"] = sa["ts_code"].map(NAME)
sa = sa.merge(pool[["ts_code", "score_C", "score_BM"]], on="ts_code", how="left").sort_values("r20", ascending=False, kind="mergesort")
print(f"\n--- AI+传媒概念全体成分（{len(ai_all)} 只去重）近20日 Top20 ---")
print(sa[["ts_code", "name", "r3", "r20", "score_C"]].head(20).to_string(index=False,
      formatters={"r3": "{:+.2%}".format, "r20": "{:+.2%}".format, "score_C": "{:.3f}".format}))
sa.to_csv(os.path.join(OUT, "hot_ai_media_members.csv"), index=False)

# AI/传媒 全体成分 vs 全市场
in_xsec = sa["score_C"].notna()
print(f"\n[AI/传媒 合计] 成分 {len(sa)} 只  近3日={sa['r3'].mean():+.2%} 近20日={sa['r20'].mean():+.2%}"
      f"   有因子值 {in_xsec.sum()} 只 其近3日={sa.loc[in_xsec,'r3'].mean():+.2%} 近20日={sa.loc[in_xsec,'r20'].mean():+.2%}"
      f"   score_C 均值={sa.loc[in_xsec,'score_C'].mean():.3f}（全池均值≈0.5）")