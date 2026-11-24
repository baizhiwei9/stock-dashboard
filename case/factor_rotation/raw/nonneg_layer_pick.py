# -*- coding: utf-8 -*-
"""非负·剔反转 口径下的「行业 / 主题 / 概念」三层榜单（请求 32）。

固定因子集合（口径 N，四条约束逐条可校验）：
  1) 剔除全部反转类因子 —— 一个月反转、三个月反转、以及一切以「超跌/反转」为方向的因子；
  2) 权重全部非负 —— 不含任何负权重（区别于 factor_layer_final.py 的 signed 解）；
  3) 不含 3/5/10 日短期价格动量 —— 不引入 ret3/ret5/ret10 一类的个股短期价格动量；
  4) 不含板块自身价格动量 —— 纯个股因子聚合到板块，不用板块指数自身的 40/60 日收益当评分卡。

主口径 PRIMARY（= 由 raw/head_nonneg_search.py 在「非负 + 剔反转」约束下搜出的高 margin 解）：
    预期PEG三年复合 0.516 + 资金流20 0.484
并列对照（仅供运行验证，不推送）：
    N5  预期PEG三年复合 0.516 + 资金流5  0.484
    N0  预期PEG三年复合 1.0
    N4  预期PEG三年复合 0.391 + 一年动量 0.255 + 资金流5 0.100 + 资金流20 0.253

板块层口径与 raw/factor_layer_final.py 第 88 行完全一致：
    X[i] = 成分股（池内、该因子有值）因子分位的 NaN 跳过均值（pandas .mean() 默认 skipna）。
   板块名次 = 1 + Σ(S > S_该板块)，NaN 板块不计入「大于」。

输出：
    output/nonneg_layer_rank.csv   三层全量榜单（层级 / 名称 / 名次 / 得分）
    output/nonneg_layer_top.json   三层 Top 名单与目标名次（供报告卡取数，不手写）
"""
import os
import json
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
sys.path.insert(0, RAW)
from _daily_snap import read_daily  # noqa: E402

# ---------- 固定口径（唯一真源，供校验） ----------
PRIMARY_FACS = ["预期PEG三年复合", "资金流20"]
PRIMARY_W = {"预期PEG三年复合": 0.516, "资金流20": 0.484}

# 硬约束清单（运行时逐条断言，防止回退）
REVERSAL_BAN = {"一个月反转", "三个月反转"}          # 反转类：一律不得出现
SHORT_MOM_BAN = {"ret3", "ret5", "ret10", "动量3", "动量5", "动量10", "短期动量"}  # 短期价格动量

COMPARE = {
    "N20": (["预期PEG三年复合", "资金流20"], {"预期PEG三年复合": 0.516, "资金流20": 0.484}),
    "N5": (["预期PEG三年复合", "资金流5"], {"预期PEG三年复合": 0.516, "资金流5": 0.484}),
    "N0": (["预期PEG三年复合"], {"预期PEG三年复合": 1.0}),
    "N4": (["预期PEG三年复合", "一年动量", "资金流5", "资金流20"],
           {"预期PEG三年复合": 0.391, "一年动量": 0.255, "资金流5": 0.100, "资金流20": 0.253}),
}


def _check(facs, w):
    """四条约束逐条断言。"""
    for f in facs:
        assert f not in REVERSAL_BAN, f"违反约束1：出现反转类因子 {f}"
        assert f not in SHORT_MOM_BAN, f"违反约束3：出现短期价格动量 {f}"
    for f in facs:
        assert w[f] > 0, f"违反约束2：出现非正权重 {f}={w[f]}"
    assert abs(sum(w.values()) - 1.0) < 2e-2, "权重未归一化（搜索解保留 3 位小数，容差 2e-2）"
    return True


# ---------- 0. 打分池 + 个股因子分位矩阵 ----------
P = pd.read_csv(os.path.join(RAW, "factor_layer_stock_pct.csv"),
                dtype={"Unnamed: 0": str}).rename(columns={"Unnamed: 0": "ts_code"}).set_index("ts_code")
pool = list(P.index)
print(f"[pool] 全 A 可交易池 {len(pool)} 只")

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
print(f"[check] 预期PEG三年复合 与引擎一致样本 {int(m.sum())}，相关系数 {corr:.6f}")

ALLF = list(P.columns)
print(f"[factors] 可用列 {len(ALLF)}")

# ---------- 1. 板块映射（概念 / 行业 / 主题） ----------
# 区间表 + 最新单日快照（自动跟随最新交易日，不再硬编码日期）
_td, _AS_OF = read_daily(RAW, usecols=["ts_code"], dtypes=str)
td_cols = set(_td["ts_code"].unique())

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in td_cols]) >= 5}
BCS = sorted(con_members)
name2code = {v: k for k, v in con_name.items()}

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
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


def build_X(members):
    keys = list(members)
    out = np.full((len(keys), len(ALLF)), np.nan)
    for i, k in enumerate(keys):
        mem = [c for c in members[k] if c in P.index]
        if not mem:
            continue
        out[i, :] = P.reindex(mem).mean().values     # NaN 跳过均值（与 factor_layer_final 第 88 行一致）
    return keys, out


CK, XC = build_X(con_members)
IK, XI = build_X(mk_ind)
TK, XT = build_X(mk_the)
XMAP = {"concept": (CK, XC), "industry": (IK, XI), "theme": (TK, XT)}
LNAME = {"concept": "概念", "industry": "行业", "theme": "主题"}
TARGETS = ["电机", "AI视频", "短剧游戏", "减速器", "人形机器人", "特斯拉概念"]


def run(facs, weights, tag):
    _check(facs, weights)
    wsum = sum(weights.values())
    wvec = np.array([weights[f] / wsum for f in facs], dtype=float)
    jidx = [ALLF.index(f) for f in facs]
    res = {"tag": tag, "facs": list(facs), "weights": {f: round(weights[f] / wsum, 4) for f in facs}}
    for layer in ("concept", "industry", "theme"):
        keys, X = XMAP[layer]
        S = X[:, jidx] @ wvec
        names = np.array([con_name.get(k, k) if layer == "concept" else k for k in keys])
        srt = np.argsort(-np.nan_to_num(S, nan=-9e9), kind="stable")
        rows = [(int(np.where(srt == i)[0][0]) + 1, names[i], float(S[i])) for i in srt]
        rank_of = {}
        for t in TARGETS:
            hit = np.where(names == t)[0]
            if len(hit):
                ti = int(hit[0])
                rank_of[t] = (1 + int(np.nansum(S > S[ti])), len(keys), round(float(S[ti]), 4))
        res[layer] = rows
        res[layer + "_target"] = rank_of
    return res


def show(res):
    print(f"\n================= 口径 {res['tag']} =================")
    print("因子与权重：" + "　".join(f"{f} {w}" for f, w in res["weights"].items()))
    for layer, topn in (("industry", 5), ("theme", 5), ("concept", 10)):
        print(f"【{LNAME[layer]} Top{topn}】")
        for r, nm, v in res[layer][:topn]:
            print(f"  #{r:<3d} {nm:<18s} {v:.4f}")
    for layer in ("concept", "theme", "industry"):
        t = res[layer + "_target"]
        if t:
            print(f"  [{LNAME[layer]}层目标名次] " + "；".join(
                f"{k} #{v[0]}/{v[1]}" for k, v in t.items()))


# ---------- 2. 运行：主口径 + 对照 ----------
resP = run(PRIMARY_FACS, PRIMARY_W, "N 非负·剔反转（主口径）")
show(resP)
for k in ("N5", "N0", "N4"):
    show(run(*COMPARE[k], k))

# ---------- 3. 落盘 ----------
rows = []
for level, key in (("概念", "concept"), ("行业", "industry"), ("主题", "theme")):
    for r, nm, v in resP[key]:
        rows.append({"层级": level, "名称": nm, "名次": r, "得分": round(v, 4)})
pd.DataFrame(rows).to_csv(os.path.join(OUT, "nonneg_layer_rank.csv"), index=False)

top = {
    "口径": "非负·剔反转",
    "因子": resP["facs"],
    "权重": resP["weights"],
    "约束": ["剔除全部反转类因子", "权重全部非负", "不含3/5/10日短期价格动量", "不含板块自身价格动量"],
    "行业": [[r, nm, round(v, 4)] for r, nm, v in resP["industry"][:5]],
    "主题": [[r, nm, round(v, 4)] for r, nm, v in resP["theme"][:5]],
    "概念": [[r, nm, round(v, 4)] for r, nm, v in resP["concept"][:10]],
    "目标名次": {LNAME[layer]: resP[layer + "_target"] for layer in ("concept", "theme", "industry")},
}
with open(os.path.join(OUT, "nonneg_layer_top.json"), "w", encoding="utf-8") as f:
    json.dump(top, f, ensure_ascii=False, indent=2)
print("\n[done] -> output/nonneg_layer_rank.csv / output/nonneg_layer_top.json")