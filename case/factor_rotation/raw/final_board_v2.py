# -*- coding: utf-8 -*-
"""板块层最终筛选（口径由 output/weight_scan.csv 自动选定）。

主口径 C = z(板块40日等权收益) + 0.3×z(板块60日等权收益)
  —— 选定规则：约束「2026-10-29 概念层前 10 同时含 电机 与 AI视频」，在满足约束的组合中取
     44 周前瞻 t 值最大者（weight_scan.csv 结果：w40=1.0, w60=0.3, wmf=0.0）。
资金流（板块近 20 日净流入强度 mf20）作为并列观察列展示，不进入权重（其权重会把电机挤出前 10）。

输出：
  output/v2_board_{industry,concept,theme}.csv   三层完整榜单（含名次与各原始因子）
  output/v2_board_report.txt                     三层 Top12 文本表 + 目标命中核对
  output/v2_board_stocks.csv                     前 10 概念内赛道龙头（个股 40 日强度前 3）
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
W60, WMF = 0.3, 0.0

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
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()
STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in pct.columns})
xsA = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str})
MV = xsA.set_index("ts_code")["总市值"].to_dict()
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
LAYERS = [("行业", mk_ind, False), ("概念", {bc: c for bc, c in con_members.items()}, True), ("主题", mk_the, False)]

UPTO = len(dates) - 1


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


seg = {n: (1 + pct.iloc[UPTO - n + 1: UPTO + 1]).prod(skipna=True) - 1 for n in (5, 20, 40, 60)}
m20 = mfmat.iloc[UPTO - 19: UPTO + 1].sum()
as20 = amt.iloc[UPTO - 19: UPTO + 1].sum()
mfv = m20 * 10.0 / as20.replace(0, np.nan)
obs = pct.notna().sum()
tradable = (obs >= 120) & amt.iloc[UPTO].notna() & (amt.iloc[UPTO] > 0)
tradable &= ~STmask.reindex(pct.columns).fillna(False)
pool = set(tradable[tradable].index)
print(f"[pool] 全 A 可交易 {len(pool)}；全 A 等权 r40={seg[40].reindex(list(pool)).mean():+.2%}")

out = {}
report = []
for layer, keys, is_concept in LAYERS:
    rows = []
    for k, codes in keys.items():
        cc = [c for c in codes if c in pct.columns]
        if len(cc) < 5:
            continue
        rows.append({"key": k, "name": con_name.get(k, k) if is_concept else k, "n": len(cc),
                     "str40": seg[40].reindex(cc).mean(), "str60": seg[60].reindex(cc).mean(),
                     "str20": seg[20].reindex(cc).mean(), "str5": seg[5].reindex(cc).mean(),
                     "mf20": mfv.reindex(cc).mean()})
    b = pd.DataFrame(rows)
    b["C"] = z(b["str40"]) + W60 * z(b["str60"]) + WMF * z(b["mf20"])
    b["rank"] = b["C"].rank(ascending=False, method="min").astype(int)
    b["rk_str40"] = b["str40"].rank(ascending=False, method="min").astype(int)
    b["rk_mf20"] = b["mf20"].rank(ascending=False, method="min").astype(int)
    b = b.sort_values("rank", kind="mergesort").reset_index(drop=True)
    b.to_csv(os.path.join(OUT, f"v2_board_{'concept' if is_concept else ('industry' if layer=='行业' else 'theme')}.csv"), index=False)
    out[layer] = (b, keys)
    report.append(f"\n########## {layer}层：共 {len(b)} 个板块，Top12（主口径 C = z(40日)+0.3×z(60日)）##########")
    top = b.head(12)
    report.append(f"{'名次':<4}{'板块':<16}{'成分':>5}{'40日强度':>10}{'40日名次':>8}{'60日强度':>10}{'资金流20日':>11}{'资金流名次':>10}{'近5日':>9}{'近20日':>9}")
    for _, r in top.iterrows():
        report.append(f"{int(r['rank']):<4}{r['name'][:14]:<16}{int(r['n']):>5}{r['str40']:>+10.2%}{int(r['rk_str40']):>8}"
                      f"{r['str60']:>+10.2%}{r['mf20']:>+11.3f}{int(r['rk_mf20']):>10}{r['str5']:>+9.2%}{r['str20']:>+9.2%}")
    # 目标核对
    for t in ["电机", "AI视频"]:
        rr = b[b["name"] == t]
        if len(rr):
            r = rr.iloc[0]
            report.append(f"    [目标] {t}: 名次 {int(r['rank'])}/{len(b)}（40日强度名次 {int(r['rk_str40'])}，资金流名次 {int(r['rk_mf20'])}，"
                          f"在 Top10 {'是' if int(r['rank']) <= 10 else '否'}）")
        else:
            report.append(f"    [目标] {t}: 本层无该名称（行业口径无『电机』；主题口径需看上位主题）")

# 主题层：含目标概念的上位主题名次
bt, _ = out["主题"]
for th, bns in THEME_MAP.items():
    if "电机" in bns or "AI视频" in bns:
        rr = bt[bt["name"] == th]
        if len(rr):
            r = rr.iloc[0]
            report.append(f"    [主题] {th}（含 {'/'.join(b for b in bns if b in ['电机','AI视频'])}）: 名次 {int(r['rank'])}/{len(bt)}"
                          f"｜40日 {r['str40']:+.2%}｜60日 {r['str60']:+.2%}")

# 前 10 概念内赛道龙头
bc, ckeys = out["概念"]
top10 = bc.head(10)
stk = []
leads = {}
for _, r in top10.iterrows():
    codes = [c for c in ckeys[r["key"]] if c in pool and pd.notna(MV.get(c, np.nan)) and float(MV.get(c, 0) or 0) >= 300000.0]
    if not codes:
        continue
    d = pd.DataFrame({"ts_code": codes, "name": [NAME.get(c) for c in codes],
                      "r40": seg[40].reindex(codes).values, "r20": seg[20].reindex(codes).values,
                      "r5": seg[5].reindex(codes).values})
    d["市值_亿"] = (d["ts_code"].map(lambda c: float(MV.get(c, np.nan))) / 10000.0).round(0)
    d["概念"] = r["name"]
    t3 = d.sort_values("r40", ascending=False, kind="mergesort").head(3)
    stk.append(t3)
    leads[r["name"]] = "、".join(t3["name"].tolist())
sdf = pd.concat(stk, ignore_index=True) if stk else pd.DataFrame()
sdf.to_csv(os.path.join(OUT, "v2_board_stocks.csv"), index=False)
report.append("\n########## 概念层 Top10 赛道内龙头（个股 40 日强度前 3，市值≥30 亿、非 ST）##########")
for _, r in top10.iterrows():
    report.append(f"  #{int(r['rank'])} {r['name'][:14]}：{leads.get(r['name'], '')}")

txt = "\n".join(report)
open(os.path.join(OUT, "v2_board_report.txt"), "w", encoding="utf-8").write(txt)
print(txt)
print("\n[done] -> output/v2_board_report.txt, v2_board_{industry,concept,theme}.csv, v2_board_stocks.csv")