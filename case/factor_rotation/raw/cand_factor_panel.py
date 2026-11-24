# -*- coding: utf-8 -*-
"""T2 候选新因子逐项验证（全 A 面板）。

候选因子（目标：能表达"板块/主题/赛道强度 + 资金流 + 动量加速"）：
  个股级
    ret5 / ret20 / ret60            近端动量（ret20/ret60 = 现行反转因子的原始量，方向相反）
    accel_5_20 = ret5 - ret20       动量加速（近端相对 20 日的加速度）
    accel_20_60 = ret20 - ret60     动量加速（20 日相对 60 日）
    vol_ratio = 近5日均额 / 近60日均额   量能放大
    mf_net5 / mf_net20               净流入额 / 成交额（大中小单净额，万元）
    rs_ind = ret20 - 行业均值 ret20    行业内相对强弱
    rs_con = ret20 - 概念均值 ret20    概念内相对强弱
  板块级
    board_str20_con = 概念板块 20 日等权收益
    board_str20_ind = 行业 20 日等权收益
    board_mf5/mf20  = 板块净流入强度（成分股净额合计 / 成分股成交额合计）

统一检验口径：全 A 可交易池（剔除 ST/退、上市不足约 6 个月、当期停牌），
每周横截面取因子前 20% 与后 20% 的下一周（下 5 个交易日）等权收益差 = LS，
并给乘 rank IC。输出：output/cand_factor_eval.csv、output/cand_board_eval.csv。
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

# ---------- 数据 ----------
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

mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"), dtype={"ts_code": str, "trade_date": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")   # 万元
mfmat = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").sort_index(axis=1)
mfmat = mfmat.reindex(dates)   # 对齐到完整交易日索引，保证按位切片正确

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_of_stock = {}
for bc, codes in con_members.items():
    for c in codes:
        con_of_stock.setdefault(c, []).append(bc)

# ---------- 周网格（与 factor_engine 一致：W-FRI 最后一个交易日） ----------
import datetime as _dt
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
    r = (1 + pct.loc[nxt]).prod(skipna=True) - 1
    fwd[we] = r
print(f"[weeks] {len(week_ends)} 周截面，可用前瞻周 {len(fwd)}", flush=True)

STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or ""))
                    for t in close.columns})


def cum(n, upto):
    """截至 upto（含）过去 n 个交易日累计收益，返回 Series by ts_code。"""
    seg = pct.iloc[max(0, upto - n + 1): upto + 1]
    return (1 + seg).prod(skipna=True) - 1


def build_week(we, upto):
    """构造当周个股因子截面（全 A 可交易池）。"""
    r5, r20, r60 = cum(5, upto), cum(20, upto), cum(60, upto)
    a5 = amt.iloc[max(0, upto - 4): upto + 1].mean()
    a60 = amt.iloc[max(0, upto - 59): upto + 1].mean()
    m5 = mfmat.iloc[max(0, upto - 4): upto + 1].sum()
    m20 = mfmat.iloc[max(0, upto - 19): upto + 1].sum()
    as5 = amt.iloc[max(0, upto - 4): upto + 1].sum()
    as20 = amt.iloc[max(0, upto - 19): upto + 1].sum()
    obs = pct.iloc[:upto + 1].notna().sum()
    tradable = (obs >= 120) & close.iloc[upto].notna() & (amt.iloc[upto] > 0)
    tradable &= ~STmask.reindex(close.columns).fillna(False)
    xs = pd.DataFrame({
        "ret5": r5, "ret20": r20, "ret60": r60,
        "accel_5_20": r5 - r20, "accel_20_60": r20 - r60,
        "vol_ratio": a5 / a60.replace(0, np.nan),
        "mf_net5": m5 * 10.0 / as5.replace(0, np.nan),      # 万元→元 / 千元→元
        "mf_net20": m20 * 10.0 / as20.replace(0, np.nan),
        "close": close.iloc[upto],
    })
    xs = xs[tradable.reindex(xs.index).fillna(False)]
    # 行业 / 概念强度（全 A 成分等权）
    xs["行业"] = pd.Series({t: IND.get(t) for t in xs.index})
    xs["board_str20_ind"] = xs.groupby("行业")["ret20"].transform("mean")
    xs["rs_ind"] = xs["ret20"] - xs["board_str20_ind"]
    bstr = {}
    bmf = {}
    for bc, codes in con_members.items():
        cc = [c for c in codes if c in xs.index]
        if len(cc) < 5:
            continue
        bstr[bc] = xs.loc[cc, "ret20"].mean()
        bmf[bc] = xs.loc[cc, "mf_net5"].mean()
    xs["board_str20_con"] = [np.mean([bstr[b] for b in con_of_stock.get(t, []) if b in bstr])
                             if any(b in bstr for b in con_of_stock.get(t, [])) else np.nan for t in xs.index]
    xs["board_mf5_con"] = [np.mean([bmf[b] for b in con_of_stock.get(t, []) if b in bmf])
                           if any(b in bmf for b in con_of_stock.get(t, [])) else np.nan for t in xs.index]
    xs["rs_con"] = xs["ret20"] - xs["board_str20_con"]
    # 板块相对强度（相对全市场等权 20 日收益）
    mkt20 = xs["ret20"].mean()
    xs["board_rel_con"] = xs["board_str20_con"] - mkt20
    xs["board_rel_ind"] = xs["board_str20_ind"] - mkt20
    # 复合趋势分（标准化后等权）：板块相对强度 + 概念内相对强弱 + 近端加速
    def z(s):
        s = pd.to_numeric(s, errors="coerce")
        return (s - s.mean()) / (s.std() + 1e-12)
    xs["trend_c1"] = z(xs["rs_con"]) + z(xs["accel_5_20"])
    xs["trend_c2"] = z(xs["board_rel_con"]) + z(xs["rs_con"]) + z(xs["accel_5_20"])
    xs["trend_c3"] = z(xs["board_rel_con"]) + z(xs["rs_con"]) + z(xs["accel_5_20"]) + z(xs["mf_net5"])
    return xs, bstr, bmf, codes_all(xs)


def codes_all(xs):
    return xs.index.tolist()


# ---------- 个股级因子评估 ----------
FAC = ["ret5", "ret20", "ret60", "accel_5_20", "accel_20_60", "vol_ratio",
       "mf_net5", "mf_net20", "board_str20_ind", "board_str20_con", "rs_ind", "rs_con", "board_mf5_con",
       "board_rel_con", "board_rel_ind", "trend_c1", "trend_c2", "trend_c3"]
# 资金流类因子需完整窗口覆盖
idx_mf_start = dates.index(min(mfmat.dropna(how="all").index.tolist()))
recs = []
board_recs = []
for we in week_ends:
    if we not in fwd:
        continue
    upto = dates.index(we)
    xs, bstr, bmf, _ = build_week(we, upto)
    fr = fwd[we].reindex(xs.index)
    for f in FAC:
        if f.startswith("mf_") or f == "board_mf5_con" or f.endswith("c3"):
            if upto < idx_mf_start + 20:
                continue
        v = xs[f]
        m = v.notna() & fr.notna()
        if m.sum() < 50:
            continue
        vv, ff = v[m], fr[m]
        q = vv.rank(pct=True, method="average")
        top = ff[q >= 0.8].mean()
        bot = ff[q <= 0.2].mean()
        ic = vv.rank(pct=True).corr(ff.rank(pct=True))
        recs.append({"week": we, "factor": f, "n": int(m.sum()), "ls": float(top - bot), "ic": float(ic),
                     "ls_ret20_spearman": float(vv.corr(xs.loc[vv.index, "ret20"].rank(pct=True)))})
    # 板块级
    for bc, s in bstr.items():
        members = [c for c in con_members[bc] if c in xs.index]
        bret = fr.reindex(members).mean()
        board_recs.append({"week": we, "board": bc, "name": con_name.get(bc, bc), "n": len(members),
                           "strength": float(s), "next_ret": float(bret) if pd.notna(bret) else np.nan,
                           "mf": float(bmf.get(bc, np.nan)) if pd.notna(bmf.get(bc, np.nan)) else np.nan})

ev = pd.DataFrame(recs)
bdf = pd.DataFrame(board_recs)
if len(bdf):
    bdf = bdf[bdf["next_ret"].notna()]
    bdf["st_rank"] = bdf.groupby("week")["strength"].rank(pct=True, method="average")
    bq = bdf.groupby("week").apply(lambda g: g.loc[g["st_rank"] >= 0.8, "next_ret"].mean()
                                   - g.loc[g["st_rank"] <= 0.2, "next_ret"].mean(), include_groups=False)
    print("\n=== 板块强度（概念）分组 LS：板块20日强度前20% - 后20%，次周等权收益 ===")
    print(f"  周均 {bq.mean():+.2%}  胜率 {(bq>0).mean():.1%}  t={bq.mean()/(bq.std()/np.sqrt(len(bq))):+.2f}  n={len(bq)}")
    bdf.to_csv(os.path.join(OUT, "cand_board_panel.csv"), index=False)

summ = []
for f, g in ev.groupby("factor"):
    ls = g["ls"]
    summ.append({"factor": f, "weeks": len(g), "ls_mean": ls.mean(), "ls_win": (ls > 0).mean(),
                 "ls_t": ls.mean() / (ls.std() / np.sqrt(len(ls))) if ls.std() > 0 else np.nan,
                 "ic_mean": g["ic"].mean(),
                 "corr_with_ret20": g["ls_ret20_spearman"].mean()})
sm = pd.DataFrame(summ).sort_values("ls_t", ascending=False, kind="mergesort")
sm.to_csv(os.path.join(OUT, "cand_factor_eval.csv"), index=False)
print("\n=== 个股级候选因子评估（全 A，44 周）===")
print(sm.to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                            "ls_t": "{:+.2f}".format, "ic_mean": "{:+.3f}".format,
                                            "corr_with_ret20": "{:+.2f}".format}))
print(f"\n[基准] 全 A 等权周收益均值 = {(pd.DataFrame(fwd).T).mean(axis=1).mean():+.2%}")

# ---------- 最新周：目标方向诊断 ----------
we = week_ends[-1]   # 最新周（10-29），诊断用，不要求前瞻
upto = dates.index(we)
xs, bstr, bmf, _ = build_week(we, upto)
print(f"\n=== 最新周 {we} 目标方向诊断（全 A 池 {len(xs)} 只）===")
TARGET_BOARDS = ["AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉", "电机", "短剧游戏", "文化传媒概念"]
bs = pd.DataFrame([{"board": b, "name": con_name.get(b, b), "strength": v} for b, v in bstr.items()])
bs["rk"] = bs["strength"].rank(ascending=False, method="min")
for nm in TARGET_BOARDS:
    r = bs[bs["name"] == nm]
    if len(r):
        r = r.iloc[0]
        print(f"  {nm:12s} 板块强度 {r['strength']:+.2%}  名次 {int(r['rk'])}/{len(bs)}")
top_boards = bs.sort_values("strength", ascending=False, kind="mergesort").head(15)
print("\n  板块强度 Top15（概念）：")
print(top_boards[["name", "strength", "rk"]].to_string(index=False, formatters={"strength": "{:+.2%}".format, "rk": "{:.0f}".format}))
print("[done]")