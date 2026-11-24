# -*- coding: utf-8 -*-
"""短期动量因子缺口专项验证（全 A 面板）。

目标：回答"现行 26 因子是否缺短期动量；补上短期动量能否选中机器人-电机/AI视频"。

候选（全部个股级，另做概念板块等权聚合）：
  绝对动量     ret3 / ret5 / ret10 / ret20 / ret60 / ret120
  动量加速     accel_5_20 = ret5-ret20 / accel_10_20 / accel_20_60 / accel_5_60
  概念内相对   rs5_con / rs10_con / rs_con(=ret20 相对)
  板块相对     brel3/5/10/20/60_con = 概念等权收益 - 全A等权收益
  趋势位置     dist_high20 / dist_high60 = close / 近 N 日最高 - 1
  量能         vol_ratio = 近5日均额 / 近60日均额
  复合         c_st1 = z(ret5)+z(accel_5_20) ; c_st3 = z(brel5)+z(rs5)+z(ret5)

统一前瞻口径：全 A 可交易池，每周因子前 20% 与后 20% 的下一周（下 5 交易日）等权收益差 = LS。
输出：output/stmom_eval.csv、output/stmom_board_latest.csv、output/stmom_stock_latest.csv、
      output/stmom_board_top_latest.csv
"""
import os
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
print(f"[daily] rows={len(daily)} dates={len(dates)} {dates[0]}..{dates[-1]}", flush=True)

close = daily.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index(axis=1)
pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
rmax20 = close.rolling(20, min_periods=10).max()
rmax60 = close.rolling(60, min_periods=30).max()

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()
NAME2CODE = {}
for t, n in NAME.items():
    NAME2CODE.setdefault(n, t)

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_of_stock = {}
for bc, codes in con_members.items():
    for c in codes:
        con_of_stock.setdefault(c, []).append(bc)

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
print(f"[weeks] {len(week_ends)} 周截面，可用前瞻周 {len(fwd)}", flush=True)

STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or ""))
                    for t in close.columns})


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


def cum(n, upto):
    seg = pct.iloc[max(0, upto - n + 1): upto + 1]
    return (1 + seg).prod(skipna=True) - 1


def build_week(upto):
    r3, r5, r10, r20, r60, r120 = cum(3, upto), cum(5, upto), cum(10, upto), cum(20, upto), cum(60, upto), cum(120, upto)
    a5 = amt.iloc[max(0, upto - 4): upto + 1].mean()
    a60 = amt.iloc[max(0, upto - 59): upto + 1].mean()
    obs = pct.iloc[:upto + 1].notna().sum()
    tradable = (obs >= 120) & close.iloc[upto].notna() & (amt.iloc[upto] > 0)
    tradable &= ~STmask.reindex(close.columns).fillna(False)
    xs = pd.DataFrame({
        "ret3": r3, "ret5": r5, "ret10": r10, "ret20": r20, "ret60": r60, "ret120": r120,
        "accel_5_20": r5 - r20, "accel_10_20": r10 - r20, "accel_20_60": r20 - r60, "accel_5_60": r5 - r60,
        "dist_high20": close.iloc[upto] / rmax20.iloc[upto] - 1,
        "dist_high60": close.iloc[upto] / rmax60.iloc[upto] - 1,
        "vol_ratio": a5 / a60.replace(0, np.nan),
    })
    xs = xs[tradable.reindex(xs.index).fillna(False)]
    # 概念板块等权聚合（成分 >=5 才计入）
    bstr = {}
    for bc, codes in con_members.items():
        cc = [c for c in codes if c in xs.index]
        if len(cc) < 5:
            continue
        bstr[bc] = xs.loc[cc].mean(numeric_only=True)
    bmat = pd.DataFrame(bstr).T                                   # index=board_code
    # 个股 → 所属概念的板块强度（多概念取均值）
    def agg(col):
        return [np.nanmean([bmat.at[b, col] for b in con_of_stock.get(t, []) if b in bmat.index])
                if any(b in bmat.index for b in con_of_stock.get(t, [])) else np.nan for t in xs.index]
    for f in ["ret3", "ret5", "ret10", "ret20", "ret60"]:
        xs["bs_" + f] = agg(f)
    xs["rs5_con"] = xs["ret5"] - xs["bs_ret5"]
    xs["rs10_con"] = xs["ret10"] - xs["bs_ret10"]
    xs["rs_con"] = xs["ret20"] - xs["bs_ret20"]
    m = {f: xs[f].mean() for f in ["ret3", "ret5", "ret10", "ret20", "ret60"]}
    xs["brel3_con"] = xs["bs_ret3"] - m["ret3"]
    xs["brel5_con"] = xs["bs_ret5"] - m["ret5"]
    xs["brel10_con"] = xs["bs_ret10"] - m["ret10"]
    xs["brel20_con"] = xs["bs_ret20"] - m["ret20"]
    xs["brel60_con"] = xs["bs_ret60"] - m["ret60"]
    xs["c_st1"] = z(xs["ret5"]) + z(xs["accel_5_20"])
    xs["c_st3"] = z(xs["brel5_con"]) + z(xs["rs5_con"]) + z(xs["ret5"])
    # 板块粒度派生列（同一板块内为常数，便于板块层对照）
    if "ret3" in bmat.columns:
        for f in ["ret3", "ret5", "ret10", "ret20", "ret60"]:
            bmat["brel" + f[3:] + "_con"] = bmat[f] - m[f]
        bmat["c_st1"] = z(bmat["ret5"]) + z(bmat["accel_5_20"])
        bmat["c_st3"] = z(bmat["brel5_con"]) + z(bmat["ret5"])
    return xs, bmat


FAC = ["ret3", "ret5", "ret10", "ret20", "ret60", "ret120",
       "accel_5_20", "accel_10_20", "accel_20_60", "accel_5_60",
       "rs5_con", "rs10_con", "rs_con",
       "brel3_con", "brel5_con", "brel10_con", "brel20_con", "brel60_con",
       "dist_high20", "dist_high60", "vol_ratio", "c_st1", "c_st3"]

recs = []
for we in week_ends:
    if we not in fwd:
        continue
    upto = dates.index(we)
    xs, _ = build_week(upto)
    fr = fwd[we].reindex(xs.index)
    for f in FAC:
        v = xs[f]
        mm = v.notna() & fr.notna()
        if mm.sum() < 50:
            continue
        vv, ff = v[mm], fr[mm]
        q = vv.rank(pct=True, method="average")
        top, bot = ff[q >= 0.8].mean(), ff[q <= 0.2].mean()
        ic = vv.rank(pct=True).corr(ff.rank(pct=True))
        recs.append({"week": we, "factor": f, "n": int(mm.sum()), "ls": float(top - bot), "ic": float(ic),
                     "corr_with_ret20": float(vv.corr(xs.loc[vv.index, "ret20"].rank(pct=True)))})

ev = pd.DataFrame(recs)
summ = []
for f, g in ev.groupby("factor"):
    ls = g["ls"]
    summ.append({"factor": f, "weeks": len(g), "ls_mean": ls.mean(), "ls_win": (ls > 0).mean(),
                 "ls_t": ls.mean() / (ls.std() / np.sqrt(len(ls))) if ls.std() > 0 else np.nan,
                 "ic_mean": g["ic"].mean(), "corr_with_ret20": g["corr_with_ret20"].mean()})
sm = pd.DataFrame(summ).sort_values("ls_t", ascending=False, kind="mergesort")
sm.to_csv(os.path.join(OUT, "stmom_eval.csv"), index=False)
print("\n=== 短期动量族前瞻检验（全A，32 周）===")
print(sm.to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                            "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format,
                                            "corr_with_ret20": "{:+.2f}".format}))
base = pd.DataFrame(fwd).T.mean(axis=1).mean()
print(f"[基准] 全 A 等权周收益均值 = {base:+.2%}")

# ---------- 最新截面（2026-10-29）：板块层与个股层名次 ----------
we = week_ends[-1]
upto = dates.index(we)
xs, bmat = build_week(upto)
print(f"\n=== 最新截面 {we}（池 {len(xs)} 只，概念 {len(bmat)} 个）===")

TGT_BOARDS = ["电机", "AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉",
              "短剧游戏", "文化传媒概念", "虚拟数字人", "多模态AI", "AIGC概念",
              "网络游戏", "手机游戏", "特斯拉概念", "光刻机", "存储芯片", "先进封装", "CPO概念"]
bc2name = {bc: con_name.get(bc, bc) for bc in bmat.index}
bmat = bmat.copy()
bmat["name"] = bmat.index.map(lambda b: bc2name.get(b, b))
# 板块层：每个因子在 133 概念内的名次（多概念只取"最佳形成"口径 → 用因子值本身降序）
rows = []
for f in FAC:
    if f not in bmat.columns:
        continue
    rk = bmat[f].rank(ascending=False, method="min")
    for nm in TGT_BOARDS:
        hit = bmat[bmat["name"] == nm]
        if len(hit):
            i = hit.index[0]
            rows.append({"board": nm, "factor": f, "value": float(bmat.at[i, f]),
                         "rank": int(rk[i]), "n_board": len(bmat)})
bd = pd.DataFrame(rows)
bd.to_csv(os.path.join(OUT, "stmom_board_latest.csv"), index=False)

print("\n[板块层] 目标概念在各动量因子下的名次（共 133 概念；数值=概念等权因子值）")
piv = bd.pivot_table(index="board", columns="factor", values="rank", aggfunc="min")
order = ["ret3", "ret5", "ret10", "accel_5_20", "rs5_con", "brel5_con", "ret20", "rs_con",
         "brel20_con", "ret60", "brel60_con", "dist_high60"]
print(piv.reindex(TGT_BOARDS)[[c for c in order if c in piv.columns]].to_string())

# 板块层 Top12（按 c_st3 与 brel5_con、accel_5_20）
for f in ["ret5", "accel_5_20", "brel5_con", "c_st3"]:
    tp = bmat[[f, "name"]].copy()
    tp["rk"] = tp[f].rank(ascending=False, method="min")
    tp = tp.sort_values(f, ascending=False, kind="mergesort").head(12)
    print(f"\n  板块层 Top12（{f}）：")
    print("   " + " | ".join(f"{r['name']}{r[f]:+.2%}" for _, r in tp.iterrows()))

# 个股层
TGT_STOCKS = ["文投控股", "德才股份", "盛天网络", "华策影视", "芒果超媒", "光线传媒", "中国电影",
              "掌阅科技", "中文在线", "横店影视", "百纳千成", "果麦文化", "幸福蓝海", "南方传媒",
              "上海电影", "锋尚文化", "天娱数科", "中文传媒",
              "上纬新材", "世纪恒通", "泛亚微透", "襄阳轴承", "杰克科技", "华依科技", "福莱新材",
              "华阳智能", "东贝集团", "兆威机电", "鸣志电器", "大洋电机", "光洋股份", "山东矿机"]
rows = []
for nm in TGT_STOCKS:
    tc = NAME2CODE.get(nm)
    if tc is None or tc not in xs.index:
        rows.append({"name": nm, "ts_code": tc or "-", "in_pool": False})
        continue
    rec = {"name": nm, "ts_code": tc, "in_pool": True, "r3": xs.at[tc, "ret3"], "r20": xs.at[tc, "ret20"]}
    for f in FAC:
        if f in xs.columns:
            rec["rk_" + f] = int(xs[f].rank(ascending=False, method="min")[tc])
    rows.append(rec)
st = pd.DataFrame(rows)
st.to_csv(os.path.join(OUT, "stmom_stock_latest.csv"), index=False)
show_cols = ["name", "in_pool", "r3", "r20", "rk_ret5", "rk_ret10", "rk_accel_5_20", "rk_rs5_con",
             "rk_brel5_con", "rk_ret20", "rk_ret60", "rk_c_st3"]
print(f"\n[个股层] 目标个股名次（共 {len(xs)} 只；rk_* 为因子降序名次，越小越靠前）")
print(st[[c for c in show_cols if c in st.columns]].to_string(index=False))

# 个股层 Top10（c_st3 / ret5 / accel_5_20），看能否命中目标
for f in ["ret5", "c_st3", "accel_5_20"]:
    if f not in xs.columns:
        continue
    tp = xs[[f]].copy()
    tp["n"] = tp.index.map(lambda t: NAME.get(t, t))
    tp = tp.sort_values(f, ascending=False, kind="mergesort").head(10)
    print(f"\n  个股层 Top10（{f}）：")
    print("   " + " | ".join(f"{r['n']}{r[f]:+.2%}" for _, r in tp.iterrows()))

print("[done]")