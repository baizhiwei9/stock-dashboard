# -*- coding: utf-8 -*-
"""因子层组合的**历史前瞻检验**（概念层，纯个股因子聚合口径）。

口径：
  截面周 = 每周最后交易日（W-FRI）；前瞻 = 其后 5 个交易日。
  每期重建全 A 因子截面（factor_engine.build_cross_section，universe=全 A），
  个股因子 direction 调整后在本期池内 pct rank；概念层 = 成分股分位等权平均。
  资金流N = Σ(截至 we 的近 N 日 net_mf_amount×10) / Σ(近 N 日 amount)，方向 +1。
  检验：概念层前 20% − 后 20% 的次周等权收益差 = LS，附胜率、t、Spearman IC。

注意（诚实标注）：
  预期PEG / 预期净利润环比 / 三个月机构覆盖 来自**当前**快照（profit_forecast.csv 单期），
  历史各周不可时点还原 → 含该因子的组合 LS 属**上界参考**，不能当无偏证据。
  纯价格+资金流因子（一个月反转/三个月反转/一年动量/资金流）为时点可得 → 无前视。

输出：output/factor_layer_forward.csv、output/factor_layer_forward_weekly.csv
"""
import os
import sys
import json
import importlib.util
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
sys.path.insert(0, ROOT)
os.environ.setdefault("PYTHONPATH", "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs")

spec = importlib.util.spec_from_file_location("fe", os.path.join(RAW, "factor_engine.py"))
fe = importlib.util.module_from_spec(spec)
sys.modules["fe"] = fe
spec.loader.exec_module(fe)

fullA = sorted(set(fe.daily["ts_code"].map(fe.normalize)))
fe.universe6 = set(fullA)
print(f"[universe] 全 A {len(fe.universe6)}")

dates = sorted(fe.daily["trade_date"].unique())
pct = fe.daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = fe.daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
obs = amt.notna().sum(axis=0)
sb = fe.basic_sb if hasattr(fe, "basic_sb") else pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
ST = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in amt.columns})

mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"),
                 usecols=["trade_date", "ts_code", "net_mf_amount"], dtype={"trade_date": str, "ts_code": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
mfmat = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").sort_index(axis=1)

# 概念固定成分（与 factor_layer_final 同规则：整体成分 >=5）
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in amt.columns]) >= 5}
BCS = sorted(con_members)
print(f"[concept] {len(BCS)}")

# 周截面（W-FRI）
tmp = pd.DataFrame({"trade_date": dates})
tmp["week"] = pd.to_datetime(tmp["trade_date"]).dt.to_period("W-FRI")
week_ends = list(sorted(tmp.groupby("week")["trade_date"].max().unique()))
fwd = {}
for we in week_ends:
    i = dates.index(we)
    nxt = dates[i + 1:i + 6]
    if len(nxt) < 5:
        continue
    fwd[we] = (1 + pct.loc[nxt]).prod(skipna=True) - 1
print(f"[weeks] {len(week_ends)}  可用前瞻 {len(fwd)}")

# 原 26 因子（方向与 factor_engine 一致），用于旧口径对照
FACTORS26 = [
    ("BP", +1), ("单季EP", +1), ("单季SP", +1), ("EPTTM", +1), ("SPTTM", +1),
    ("EPTTM一年分位点", +1), ("股息率", +1),
    ("一个月反转", -1), ("三个月反转", -1), ("一年动量", +1),
    ("单季净利同比增速", +1), ("单季营收同比增速", +1),
    ("单季ROE", +1), ("DELTAROE", +1),
    ("非流动性冲击", -1), ("一个月换手", -1), ("三个月换手", -1),
    ("一个月波动", -1), ("三个月波动", -1), ("特异度", -1),
    ("预期EPTTM", +1), ("预期PEG", +1), ("预期净利润环比", +1),
    ("三个月机构覆盖", +1), ("公募重仓", +1), ("机构持仓", +1),
]
DIR26 = {f: d for f, d in FACTORS26}
DIR = {"一个月反转": -1, "三个月反转": -1, "一年动量": +1, "预期PEG": +1}
NEED = [f for f, _ in FACTORS26]

# 前视因子（profit_forecast.csv 单期快照，历史不可时点还原）
FORECAST = {"预期EPTTM", "预期PEG", "预期净利润环比", "三个月机构覆盖"}

feat_rows = []
COMBOS = {
    "一年动量+资金流20(等权)": {"一年动量": 1.0, "资金流20": 1.0},
    "近端动量+一年动量+资金流20": {"一个月反转": -1.0, "一年动量": 1.0, "资金流20": 1.0},
    "一年动量+预期PEG+资金流20": {"一年动量": 0.25, "预期PEG": 0.35, "资金流20": 0.40},
    "预期PEG+资金流20(最简)": {"预期PEG": 0.43, "资金流20": 0.57},
    "三个月反转反向+预期PEG+资金流20": {"三个月反转": -0.9, "预期PEG": 1.0, "资金流20": 0.9},
    "A候选:一年动量0.10+预期PEG0.40+资金流20_0.50": {"一年动量": 0.10, "预期PEG": 0.40, "资金流20": 0.50},
    "非负三因子等权(0.20/0.40/0.40)": {"一年动量": 0.20, "预期PEG": 0.40, "资金流20": 0.40},
    "单因子:一年动量": {"一年动量": 1.0},
    "单因子:资金流20": {"资金流20": 1.0},
    "单因子:预期PEG": {"预期PEG": 1.0},
}

for wi, we in enumerate(week_ends):
    if we not in fwd:
        continue
    i = dates.index(we)
    if i < 60:
        continue
    # 本期池：截至 we 观测 >=120（只用截至 we 的数据，避免前视）、当日有成交、非 ST
    obs_i = amt.iloc[:i + 1].notna().sum(axis=0)
    tr = (obs_i >= 120) & amt.iloc[i].notna() & (amt.iloc[i] > 0) & ~ST.reindex(amt.columns).fillna(False)
    wp = sorted(tr[tr].index)
    xs = fe.build_cross_section(we, week_ends)
    xs = xs.set_index("ts_code")
    cols = {}
    for f, dd in FACTORS26:
        v = pd.to_numeric(xs[f], errors="coerce").reindex(wp) * dd
        cols[f] = v.rank(pct=True, ascending=True, method="average")
    for w in (5, 20):
        i_mf = [j for j, d in enumerate(dates) if d <= we]
        upto = i_mf[-1]
        net = mfmat.iloc[upto - w + 1: upto + 1].sum(min_count=1)
        a = amt.iloc[upto - w + 1: upto + 1].sum(min_count=1)
        cols[f"资金流{w}"] = ((net * 10.0) / a.replace(0, np.nan)).reindex(wp).rank(pct=True, ascending=True)
    # 概念层聚合
    con = {}
    for bc, mem in con_members.items():
        for f, s in cols.items():
            ss = s.reindex(mem).dropna()
            con.setdefault(f, {})[bc] = ss.mean() if len(ss) else np.nan
    fr = fwd[we]
    rec = {"week": we}
    for bc in BCS:
        cc = [c for c in con_members[bc] if c in pct.columns and pd.notna(fr.get(c, np.nan))]
        rec.setdefault("next_ret", {})[bc] = fr.reindex(cc).mean() if len(cc) >= 5 else np.nan
    feat_rows.append({"week": we, "con": con, "next_ret": rec["next_ret"]})
    if wi % 8 == 0:
        print(f"  [{wi}] {we} 完成")

# 计算各组合 LS
NR = pd.DataFrame({r["week"]: r["next_ret"] for r in feat_rows}).T
NR.index.name = "week"
CONF = {}
for f in sorted(set([k for c in COMBOS.values() for k in c]) | set(DIR26)):
    CONF[f] = pd.DataFrame({r["week"]: r["con"].get(f, {}) for r in feat_rows}).T
print(f"[panel] weeks={NR.shape[0]}  concepts={NR.shape[1]}")

rows, weekly = [], []
for cname, w in COMBOS.items():
    S = sum(w[f] * CONF[f] for f in w)          # weeks x concepts
    ls, ics = [], []
    for we in S.index:
        s = S.loc[we].dropna()
        n = NR.loc[we].reindex(s.index)
        m = s.notna() & n.notna()
        s, n = s[m], n[m]
        if len(s) < 20:
            continue
        k = max(3, int(round(len(s) * 0.2)))
        top = n.nlargest(k)
        bot = n.nsmallest(k)
        hi = s.nlargest(len(s) // 5).index
        lo = s.nsmallest(len(s) // 5).index
        ls.append((n[hi].mean() - n[lo].mean()))
        ics.append(s.corr(n, method="spearman"))
        weekly.append({"week": we, "combo": cname, "ls": ls[-1], "ic": ics[-1],
                       "n_con": len(s), "baseline": n.mean()})
    a = np.array(ls)
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else np.nan
    rows.append({"combo": cname, "lookahead_free": all(f in {"一个月反转", "三个月反转", "一年动量", "资金流5", "资金流20"} for f in w),
                 "n_weeks": len(a), "ls_mean": a.mean(), "ls_win": (a > 0).mean(), "ls_t": t,
                 "ic_mean": np.nanmean(ics),
                 "weights": "+".join(f"{f}:{v}" for f, v in w.items())})
# ---------- 旧口径对照（26 因子等权聚合，NaN 感知） ----------
IDX = NR.index
SNAP = {"公募重仓", "机构持仓"}


def _ls(score_frame, tag, lookfree):
    ls, ics = [], []
    for we in score_frame.index:
        s = score_frame.loc[we].dropna()
        n = NR.loc[we].reindex(s.index)
        m = s.notna() & n.notna()
        s, n = s[m], n[m]
        if len(s) < 20:
            continue
        hi = s.nlargest(len(s) // 5).index
        lo = s.nsmallest(len(s) // 5).index
        ls.append(n[hi].mean() - n[lo].mean())
        ics.append(s.corr(n, method="spearman"))
    a = np.array(ls)
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else np.nan
    return {"combo": tag, "lookahead_free": lookfree, "n_weeks": len(a), "ls_mean": a.mean(),
            "ls_win": (a > 0).mean(), "ls_t": t, "ic_mean": np.nanmean(ics), "weights": "(等权)"}


for tag, grp, look in [
    ("旧口径:26因子等权聚合", [f for f, _ in FACTORS26], False),
    ("旧口径:26因子等权(剔6快照/前视)", [f for f, _ in FACTORS26 if f not in FORECAST and f not in SNAP], True),
]:
    arr = np.nanmean(np.stack([CONF[f].reindex(index=IDX, columns=NR.columns).values for f in grp]), axis=0)
    rows.append(_ls(pd.DataFrame(arr, index=IDX, columns=NR.columns), tag, look))

res = pd.DataFrame(rows).sort_values("ls_t", ascending=False)
res.to_csv(os.path.join(OUT, "factor_layer_forward.csv"), index=False)
pd.DataFrame(weekly).to_csv(os.path.join(OUT, "factor_layer_forward_weekly.csv"), index=False)

base = NR.mean(axis=1)
print(f"\n[基准] 概念层等权次周：均值 {base.mean():+.3%}  胜率 {(base>0).mean():.1%}  n={len(base)}")
pd.set_option("display.width", 220, "display.max_columns", 30)
print("\n===== 组合前瞻 LS（概念层前20%−后20%，次周） =====")
print(res[["combo", "lookahead_free", "n_weeks", "ls_mean", "ls_win", "ls_t", "ic_mean"]].to_string(index=False))
print("\n[done] -> output/factor_layer_forward.csv / _weekly.csv")