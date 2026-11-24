# -*- coding: utf-8 -*-
"""用「本周名次前几的因子」按因子层口径重新选股/选行业/选主题/选概念（v2，口径统一版）。

口径（与 raw/factor_layer_final.py 完全一致，纯个股因子聚合，不用板块自身价格动量）：
  1) 打分池 = 全 A 可交易池（obs>=120 & 最近交易日有成交 & 非 ST）＝ 5292 只。
  2) 个股因子 direction 调整后（值×direction，统一"大=好"）在池内做 pct rank（0~1）。
  3) 【板块层】得分 = Σ w_i × X[i]，其中 X[i] = 成分股（池内、该因子有值）的因子分位的
     NaN 跳过均值（pandas .mean() 默认 skipna）—— 与 factor_layer_final.py 第 88 行一致。
  4) 【个股层】得分 = Σ w_i × (个股因子分位，缺失以 0.5 中性补)
     —— 与 factor_layer_final.py 第 220 行 stk_sc = P[NAMES].fillna(0.5) @ w 一致。
  5) 板块名次 = 1 + Σ(S > S_该板块)，分母为全部板块数（NaN 板块不计入"大于"）。

头部因子选择规则（沿用既有口径）：按本周名次从第 1 名往下取，遇首个「本周名次差于上周」即截止。
权重 = 名次序号反向：第 1 名 → k，第 k 名 → 1。

输出：控制台（对话交付）；同时落 output/head_factor_picks_27f.csv 供复核。
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

# ---------- 0. 头部因子（按排名表规则截断） ----------
pv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
pv = pv.dropna(subset=["rank_this_week"]).sort_values("rank_this_week", kind="mergesort").reset_index(drop=True)
head = []
for _, r in pv.iterrows():
    rk, rk_prev = int(r["rank_this_week"]), r["rank_last_week"]
    if head and pd.notna(rk_prev) and rk > rk_prev:
        break  # 首个「本周名次差于上周」
    head.append((r["factor"], rk, rk_prev))
n = len(head)
W = {f: (n - i) for i, (f, _, _) in enumerate(head)}
print(f"[head] 截断后 {n} 个因子（权重按名次线性递减）")
for f, rk, rkp in head:
    print(f"   #{rk:<3d} {f:<16s} 上周第 {int(rkp) if pd.notna(rkp) else '-':>3}  权重 {W[f]}")

# ---------- 1. 打分池 + 个股因子分位矩阵 ----------
P = pd.read_csv(os.path.join(RAW, "factor_layer_stock_pct.csv"),
                dtype={"Unnamed: 0": str}).rename(columns={"Unnamed: 0": "ts_code"}).set_index("ts_code")
pool = list(P.index)
print(f"[pool] {len(pool)} 只")

# 补算 预期PEG三年复合（引擎公式：预测PE / 三年复合增速×100，direction +1）
xsA = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str}).set_index("ts_code")
fc = pd.read_csv(os.path.join(RAW, "profit_forecast.csv"))
fc["code6"] = fc["代码"].astype(str).str.split(".").str[0].str.zfill(6)
fc = fc.drop_duplicates(subset=["code6"], keep="first").set_index("code6")
e25 = pd.to_numeric(fc["2025预测每股收益"], errors="coerce")
e26 = pd.to_numeric(fc["2026预测每股收益"], errors="coerce")
e28 = pd.to_numeric(fc["2028预测每股收益"], errors="coerce")
close6 = pd.Series(pd.to_numeric(xsA["close"], errors="coerce").values,
                   index=xsA.index.str.split(".").str[0].str.zfill(6))
close6 = close6[~close6.index.duplicated()]
pe_f = (close6 / e26.reindex(close6.index)).replace([np.inf, -np.inf], np.nan)
pos = (e25 > 0) & (e28 > 0)
g3 = pd.Series(np.nan, index=fc.index)
g3[pos] = (e28[pos] / e25[pos]) ** (1.0 / 3.0) - 1.0
g3 = g3.replace([np.inf, -np.inf], np.nan)
peg3 = (pe_f / (g3.reindex(close6.index) * 100)).replace([np.inf, -np.inf], np.nan)
pool6 = pd.Series([c.split(".")[0].zfill(6) for c in pool], index=pool)
peg3_stock = pd.Series(peg3.reindex(pool6.values).values, index=pool)
P["预期PEG三年复合"] = peg3_stock.rank(pct=True, ascending=True, method="average")

eng = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str}).set_index("ts_code")
common = [c for c in eng.index if c in pool]
a = peg3_stock.reindex(common)
b = pd.to_numeric(eng["预期PEG三年复合"], errors="coerce").reindex(common)
m = a.notna() & b.notna()
corr = np.corrcoef(a[m].astype(float), b[m].astype(float))[0, 1]
print(f"[check] peg3 与引擎一致样本 {int(m.sum())}，相关系数 {corr:.6f}，最大绝对差 {float((a[m]-b[m]).abs().max()):.4f}")

ALLF = list(P.columns)
print(f"[factors] 可用 {len(ALLF)} 列；头部因子池内非空率：",
      {f: round(float(P[f].notna().mean()), 3) for f in W})

# ---------- 2. 板块映射 ----------
td_cols = set(pd.read_csv(os.path.join(RAW, "daily_260d.csv"), usecols=["ts_code"], dtype=str)["ts_code"].unique())
td_cols |= set(pd.read_csv(os.path.join(RAW, "daily_20261029.csv"), usecols=["ts_code"], dtype=str)["ts_code"].unique())

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in td_cols]) >= 5}
BCS = sorted(con_members)
name2code = {v: k for k, v in con_name.items()}

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()
mk_ind = {}
for c, i in IND.items():
    if isinstance(i, str) and c in pool:
        mk_ind.setdefault(i, []).append(c)
mk_ind = {k: v for k, v in mk_ind.items() if len(v) >= 5}

src = open(os.path.join(RAW, "final_pick_fullA.py"), encoding="utf-8").read()
ns = {}
exec(src[src.index("THEME_MAP = {"): src.index("# ---------- 数据 ----------")], ns)
THEME_MAP = ns["THEME_MAP"]
mk_the = {}
for th, bns in THEME_MAP.items():
    cc = []
    for bn in bns:
        bc = name2code.get(bn)
        if bc in con_members:
            cc += con_members[bc]
    cc = sorted(set(c for c in cc if c in pool))
    if len(cc) >= 5:
        mk_the[th] = cc
print(f"[layers] 概念 {len(BCS)} / 行业 {len(mk_ind)} / 主题 {len(mk_the)}")

# 各层板块得分矩阵 X（n_board × n_factor），X[i] = 成分股池内分位的 NaN 跳过均值
def build_X(members):
    keys = list(members)
    out = np.full((len(keys), len(ALLF)), np.nan)
    for i, k in enumerate(keys):
        mem = [c for c in members[k] if c in P.index]
        if not mem:
            continue
        out[i, :] = P.reindex(mem).mean().values      # pandas mean 默认 skipna
    return keys, out

CK, XC = build_X(con_members)
IK, XI = build_X(mk_ind)
TK, XT = build_X(mk_the)
XMAP = {"concept": (CK, XC), "industry": (IK, XI), "theme": (TK, XT)}
LNAME = {"concept": "概念", "industry": "行业", "theme": "主题"}
TARGETS = ["电机", "AI视频", "短剧游戏", "减速器", "人形机器人", "特斯拉概念"]
MV = xsA["总市值"].to_dict()


def run(facs, weights, tag):
    wsum = sum(weights.values())
    wvec = np.array([weights[f] / wsum for f in facs], dtype=float)
    sc = (P[list(facs)].fillna(0.5) @ pd.Series({f: weights[f] / wsum for f in facs})).astype(float)
    res = {"tag": tag, "weights": {f: round(weights[f] / wsum, 4) for f in facs}}

    top = sc.sort_values(ascending=False, kind="mergesort").head(10)
    res["stock"] = [(NAME.get(c, c), IND.get(c, ""), round(float(v), 4)) for c, v in top.items()]

    jidx = [ALLF.index(f) for f in facs]
    for layer in ("concept", "industry", "theme"):
        keys, X = XMAP[layer]
        S = X[:, jidx] @ wvec
        names = np.array([con_name.get(k, k) if layer == "concept" else k for k in keys])
        srt = np.argsort(-np.nan_to_num(S, nan=-9e9), kind="stable")
        rank_of = {}
        for t in TARGETS:
            hit = np.where(names == t)[0]
            if len(hit):
                ti = int(hit[0])
                rank_of[t] = (1 + int(np.nansum(S > S[ti])), len(keys), round(float(S[ti]), 4))
        res[layer] = [(int(np.where(srt == i)[0][0]) + 1, names[i], float(S[i])) for i in srt]
        res[layer + "_target"] = rank_of
    return res


def show(res):
    print(f"\n================= 口径 {res['tag']} =================")
    print("权重：" + "　".join(f"{f} {w}" for f, w in res["weights"].items()))
    print("【选股 Top10】")
    for i, (nm, ind, v) in enumerate(res["stock"], 1):
        print(f"  {i:>2}. {nm:<8s}（{ind}） {v:.4f}")
    for layer, topn in (("industry", 5), ("theme", 5), ("concept", 10)):
        print(f"【选{LNAME[layer]} Top{topn}】")
        for r, nm, v in res[layer][:topn]:
            print(f"  #{r:<3d} {nm:<18s} {v:.4f}")
    for layer in ("concept", "theme", "industry"):
        t = res[layer + "_target"]
        if t:
            print(f"  [{LNAME[layer]}层目标名次] " + "；".join(
                f"{k} #{v[0]}/{v[1]}（{v[2]}）" for k, v in t.items()))


# A：头部 6 因子
facsA = [f for f, _, _ in head]
resA = run(facsA, {f: W[f] for f in facsA}, "A 头部因子·原名次权重")
show(resA)

# B：剔除反转类
REV = {"三个月反转", "一个月反转"}
facsB = [f for f in facsA if f not in REV]
WB = {f: (len(facsB) - i) for i, f in enumerate(facsB)}
resB = run(facsB, WB, "B 头部·剔除反转类")
show(resB)

# D：因子层含负权组合（三个月反转反向 + 预期PEG + 资金流20）—— 复现 factor_layer_final 的 signed_best
facsD = ["三个月反转", "预期PEG", "资金流20"]
WD = {"三个月反转": -0.9, "预期PEG": 1.0, "资金流20": 0.9}
resD = run(facsD, WD, "D 因子层含负权(三个月反转反向+预期PEG+资金流20)")
show(resD)

# E：因子层非负·剔除反转类 最简组合（由 head_nonneg_search.py 搜出）
facsE = ["预期PEG三年复合", "资金流5"]
WE = {"预期PEG三年复合": 0.516, "资金流5": 0.484}
resE = run(facsE, WE, "E 因子层非负·剔反转 最简(预期PEG三年复合+资金流5)")
show(resE)

# E0：单因子 —— 本轮新增的 预期PEG三年复合
resE0 = run(["预期PEG三年复合"], {"预期PEG三年复合": 1.0}, "E0 单因子(预期PEG三年复合)")
show(resE0)

# E20：非负·剔反转·长窗口（预期PEG三年复合 + 资金流20），规避 5 日窗口
resE20 = run(["预期PEG三年复合", "资金流20"],
             {"预期PEG三年复合": 0.516, "资金流20": 0.484},
             "E20 因子层非负·剔反转(预期PEG三年复合+资金流20)")
show(resE20)

# ---------- 3. 落盘复核 ----------
rows = []
for res in (resA, resB, resD, resE, resE20, resE0):
    for layer in ("concept", "industry", "theme"):
        for r, nm, v in res[layer]:
            rows.append({"口径": res["tag"], "层级": layer, "名称": nm,
                         "名次": r, "得分": round(v, 4)})
pd.DataFrame(rows).to_csv(os.path.join(OUT, "head_factor_picks_27f.csv"), index=False)
print("\n[done] -> output/head_factor_picks_27f.csv")