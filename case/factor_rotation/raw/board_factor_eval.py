# -*- coding: utf-8 -*-
"""板块层因子前瞻评估（概念 / 行业 / 主题）。

因子（全部板块层，成分等权）：
  str20/str40/str60 = 板块近 20/40/60 日等权收益
  rel40             = str40 - 全市场等权 40 日收益
  mf20              = 板块近 20 日净流入强度 = Σ成分股 net_mf_amount / Σ成分股 amount
  cmb40mf           = z(rel40) + z(mf20)
检验：板块层前 20% - 后 20% 的次周（下 5 交易日）等权收益差 = LS，附 t 与 Spearman IC。
窗口：需要 60 日回看 + 5 日前瞻，起点约 20251201；另给 20260313 起 32 周子样本对照。
输出：output/board_factor_eval.csv
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
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()
STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in pct.columns})

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()

sys_map = {}
import importlib.util
spec = importlib.util.spec_from_file_location("fp", os.path.join(RAW, "final_pick_fullA.py"))
src = open(os.path.join(RAW, "final_pick_fullA.py"), encoding="utf-8").read()
ns = {}
exec(src[src.index("THEME_MAP = {"): src.index("# ---------- 数据 ----------")], ns)
THEME_MAP = ns["THEME_MAP"]
name2code = {v: k for k, v in con_name.items()}

LAYERS = {}
mk_ind = {}
for c, i in IND.items():
    if isinstance(i, str):
        mk_ind.setdefault(i, []).append(c)
LAYERS["行业"] = mk_ind
LAYERS["概念"] = {bc: codes for bc, codes in con_members.items()}
mk_the = {}
for th, bnames in THEME_MAP.items():
    codes = []
    for b in bnames:
        if b in name2code:
            codes += con_members[name2code[b]]
    mk_the[th] = sorted(set(codes))
LAYERS["主题"] = mk_the

tmp = pd.DataFrame({"trade_date": dates})
tmp["dt"] = pd.to_datetime(tmp["trade_date"])
tmp["week"] = tmp["dt"].dt.to_period("W-FRI")
week_ends = sorted(tmp.groupby("week")["trade_date"].max().unique())
fwd = {}
for we in week_ends:
    i = dates.index(we)
    nxt = dates[i + 1:i + 6]
    if len(nxt) < 5:
        continue
    fwd[we] = (1 + pct.loc[nxt]).prod(skipna=True) - 1


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


recs = []
for we in week_ends:
    if we not in fwd:
        continue
    upto = dates.index(we)
    if upto < 60:
        continue
    seg = {}
    for n in (20, 40, 60):
        seg[n] = (1 + pct.iloc[upto - n + 1: upto + 1]).prod(skipna=True) - 1
    m20 = mfmat.iloc[upto - 19: upto + 1].sum()
    as20 = amt.iloc[upto - 19: upto + 1].sum()
    mf20v = m20 * 10.0 / as20.replace(0, np.nan)
    fr = fwd[we]
    for layer, keys in LAYERS.items():
        rows = []
        for k, codes in keys.items():
            cc = [c for c in codes if c in pct.columns and pd.notna(fr.get(c, np.nan))]
            if len(cc) < 5:
                continue
            rows.append({"key": k, "name": con_name.get(k, k) if layer == "概念" else k,
                         "n": len(cc),
                         "str20": seg[20].reindex(cc).mean(), "str40": seg[40].reindex(cc).mean(),
                         "str60": seg[60].reindex(cc).mean(), "mf20": mf20v.reindex(cc).mean(),
                         "next_ret": fr.reindex(cc).mean()})
        b = pd.DataFrame(rows)
        if len(b) < 10:
            continue
        mkt40 = float(b["str40"].mean())
        b["rel40"] = b["str40"] - mkt40
        b["cmb40mf"] = z(b["rel40"]) + z(b["mf20"])
        b["cmb40"] = z(b["rel40"]) + z(b["str20"])
        for f in ["str20", "str40", "str60", "rel40", "mf20", "cmb40mf", "cmb40"]:
            v = pd.to_numeric(b[f], errors="coerce")
            m = v.notna() & b["next_ret"].notna()
            if m.sum() < 10:
                continue
            vv, ff = v[m], b.loc[m, "next_ret"]
            q = vv.rank(pct=True, method="average")
            ls = ff[q >= 0.8].mean() - ff[q <= 0.2].mean()
            ic = vv.rank(pct=True).corr(ff.rank(pct=True))
            recs.append({"week": we, "layer": layer, "factor": f, "n_board": int(m.sum()),
                         "ls": float(ls), "ic": float(ic)})

ev = pd.DataFrame(recs)
for sub, tag in [(ev, "全窗口"), (ev[ev["week"] >= "20260313"], "32周子样本")]:
    print(f"\n############ {tag}（{sub['week'].min()}..{sub['week'].max()}，周数 {sub['week'].nunique()}）############")
    s = []
    for (layer, f), g in sub.groupby(["layer", "factor"]):
        ls = g["ls"]
        s.append({"layer": layer, "factor": f, "weeks": len(g), "ls_mean": ls.mean(),
                  "ls_win": (ls > 0).mean(),
                  "ls_t": ls.mean() / (ls.std() / np.sqrt(len(ls))) if ls.std() > 0 else np.nan,
                  "ic_mean": g["ic"].mean()})
    sm = pd.DataFrame(s).sort_values(["layer", "ls_t"], ascending=[True, False], kind="mergesort")
    print(sm.to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                                "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format}))
    if tag == "全窗口":
        sm.to_csv(os.path.join(OUT, "board_factor_eval.csv"), index=False)
bq = ev[ev["factor"] == "cmb40mf"].groupby("week")["ls"].mean()
print(f"\n[基准] 板块等权次周收益均值 = {ev.groupby('week')['ls'].mean().mean():+.2%}")
print("[done] -> output/board_factor_eval.csv")