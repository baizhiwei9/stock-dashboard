# -*- coding: utf-8 -*-
"""板块层综合分候选组合筛选：既要覆盖 电机/AI视频（前 10），又要有前瞻依据。

候选板中组合（成分等权，全 A）：
  str40 / str60                       板块中期强度（40、60 日等权收益）
  mf20                                板块近 20 日净流入强度
  C1 = z(str40)
  C2 = z(str60)
  C3 = z(str40)+z(str60)
  C4 = z(str40)+z(mf20)
  C5 = z(str40)+z(str60)+z(mf20)
  C6 = z(str40)+z(str60)+0.5*z(mf20)
对每个组合：输出最新截面（2026-10-29）前 12 名单 + 电机/AI视频 名次；
并给 44 周（20251212..20261016）与 32 周子样本的前 20%-后 20% 次周 LS、t、IC。
输出：output/board_combo_pick.csv、output/board_combo_eval.csv
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "pct_chg", "amount"],
                    dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "pct_chg", "amount"],
                  dtype={"ts_code": str, "trade_date": str})
daily = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
for c in ["pct_chg", "amount"]:
    daily[c] = pd.to_numeric(daily[c], errors="coerce")
dates = sorted(daily["trade_date"].unique())
pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"), dtype={"ts_code": str, "trade_date": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
mfmat = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").reindex(dates).sort_index(axis=1)

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
IND = sb.set_index("ts_code")["industry"].to_dict()
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
src = open(os.path.join(RAW, "final_pick_fullA.py"), encoding="utf-8").read()
ns = {}
exec(src[src.index("THEME_MAP = {"): src.index("# ---------- 数据 ----------")], ns)
THEME_MAP = ns["THEME_MAP"]
name2code = {v: k for k, v in con_name.items()}

mk_ind = {}
for c, i in IND.items():
    if isinstance(i, str):
        mk_ind.setdefault(i, []).append(c)
mk_the = {}
for th, bns in THEME_MAP.items():
    cc = []
    for b in bns:
        if b in name2code:
            cc += con_members[name2code[b]]
    mk_the[th] = sorted(set(cc))
LAYERS = {"行业": mk_ind, "概念": {bc: c for bc, c in con_members.items()}, "主题": mk_the}

tmp = pd.DataFrame({"trade_date": dates})
tmp["week"] = pd.to_datetime(tmp["trade_date"]).dt.to_period("W-FRI")
week_ends = sorted(tmp.groupby("week")["trade_date"].max().unique())
fwd = {}
for we in week_ends:
    i = dates.index(we)
    nxt = dates[i + 1:i + 6]
    if len(nxt) >= 5:
        fwd[we] = (1 + pct.loc[nxt]).prod(skipna=True) - 1


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


def board_panel(upto, keys, is_concept):
    seg = {}
    for n in (20, 40, 60):
        seg[n] = (1 + pct.iloc[upto - n + 1: upto + 1]).prod(skipna=True) - 1
    m20 = mfmat.iloc[upto - 19: upto + 1].sum()
    as20 = amt.iloc[upto - 19: upto + 1].sum()
    mfv = m20 * 10.0 / as20.replace(0, np.nan)
    rows = []
    for k, codes in keys.items():
        cc = [c for c in codes if c in pct.columns]
        if len(cc) < 5:
            continue
        rows.append({"key": k, "name": con_name.get(k, k) if is_concept else k, "n": len(cc),
                     "str20": seg[20].reindex(cc).mean(), "str40": seg[40].reindex(cc).mean(),
                     "str60": seg[60].reindex(cc).mean(), "mf20": mfv.reindex(cc).mean(),
                     "r5": seg[5].reindex(cc).mean() if False else (1 + pct.iloc[upto - 4: upto + 1]).prod(skipna=True).reindex(cc).mean() - 1})
    b = pd.DataFrame(rows)
    b["rel40"] = b["str40"] - b["str40"].mean()
    b["rel60"] = b["str60"] - b["str60"].mean()
    b["C1"] = z(b["str40"])
    b["C2"] = z(b["str60"])
    b["C3"] = z(b["str40"]) + z(b["str60"])
    b["C4"] = z(b["str40"]) + z(b["mf20"])
    b["C5"] = z(b["str40"]) + z(b["str60"]) + z(b["mf20"])
    b["C6"] = z(b["str40"]) + z(b["str60"]) + 0.5 * z(b["mf20"])
    b["C7"] = z(b["str40"]) + z(b["str60"]) + 0.3 * z(b["mf20"])
    return b


COMBOS = ["C1", "C2", "C3", "C4", "C5", "C6", "C7"]
TGT = ["电机", "AI视频", "人形机器人", "减速器", "短剧游戏", "文化传媒概念", "网络游戏", "手机游戏", "机器视觉"]

# ---- 前瞻评估 ----
recs = []
for we in week_ends:
    if we not in fwd:
        continue
    upto = dates.index(we)
    if upto < 60:
        continue
    fr = fwd[we]
    for layer, keys in LAYERS.items():
        b = board_panel(upto, keys, layer == "概念")
        b["next_ret"] = [fr.reindex([c for c in keys[k] if c in pct.columns]).mean() for k in b["key"]]
        for f in COMBOS:
            v = pd.to_numeric(b[f], errors="coerce")
            m = v.notna() & b["next_ret"].notna()
            if m.sum() < 10:
                continue
            vv, ff = v[m], b.loc[m, "next_ret"]
            q = vv.rank(pct=True, method="average")
            recs.append({"week": we, "layer": layer, "combo": f,
                         "ls": float(ff[q >= 0.8].mean() - ff[q <= 0.2].mean()),
                         "ic": float(vv.rank(pct=True).corr(ff.rank(pct=True)))})
ev = pd.DataFrame(recs)
rows = []
for sub, tag in [(ev, "全窗口44周"), (ev[ev["week"] >= "20260313"], "32周")]:
    for (layer, f), g in sub.groupby(["layer", "combo"]):
        ls = g["ls"]
        rows.append({"window": tag, "layer": layer, "combo": f, "weeks": len(g),
                     "ls_mean": ls.mean(), "ls_win": (ls > 0).mean(),
                     "ls_t": ls.mean() / (ls.std() / np.sqrt(len(ls))) if ls.std() > 0 else np.nan,
                     "ic_mean": g["ic"].mean()})
ee = pd.DataFrame(rows)
ee.to_csv(os.path.join(OUT, "board_combo_eval.csv"), index=False)
print("=== 板块层组合前瞻评估（概念层）===")
print(ee[(ee["layer"] == "概念")].sort_values(["window", "ls_t"], ascending=[True, False], kind="mergesort")
      .to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                          "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format}))
print("\n=== 主题层 ===")
print(ee[(ee["layer"] == "主题")].sort_values(["window", "ls_t"], ascending=[True, False], kind="mergesort")
      .to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                          "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format}))
print("\n=== 行业层 ===")
print(ee[(ee["layer"] == "行业")].sort_values(["window", "ls_t"], ascending=[True, False], kind="mergesort")
      .to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                          "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format}))

# ---- 最新截面 ----
upto = len(dates) - 1
print(f"\n############ 最新截面 {dates[upto]} 前三层 Top12 + 目标名次 ############")
allrec = []
for layer, keys in LAYERS.items():
    b = board_panel(upto, keys, layer == "概念")
    for f in COMBOS:
        b["rk_" + f] = b[f].rank(ascending=False, method="min")
    print(f"\n---- {layer}层（共 {len(b)}）----")
    for f in COMBOS:
        top = b.sort_values(f, ascending=False, kind="mergesort").head(12)
        names = "、".join(top["name"].tolist())
        hit = [t for t in ["电机", "AI视频"] if t in top["name"].tolist()]
        print(f"  {f}: 命中={hit if hit else '无'} | {names}")
    sub = b[b["name"].isin(TGT)][["name", "n", "str40", "str60", "mf20"] + ["rk_" + c for c in COMBOS]]
    print("   目标名次：")
    print(sub.to_string(index=False, formatters={"str40": "{:+.2%}".format, "str60": "{:+.2%}".format,
                                                 "mf20": "{:+.3f}".format}))
    b["layer"] = layer
    allrec.append(b)
pd.concat(allrec, ignore_index=True).to_csv(os.path.join(OUT, "board_combo_pick.csv"), index=False)
print("\n[done] -> output/board_combo_pick.csv, output/board_combo_eval.csv")