# -*- coding: utf-8 -*-
"""本周强势上升因子 → 行业 Top5 / 个股 Top10（周报前置部分）

选取规则（用户 2026-10-28 确认）：
- 取桑基图最右列（最新截面）因子动量排名，从第 1 名往下依次取；
- 只保留名次上升的因子（rank_change_4w > 0，即相对 4 周前名次变好）；
- 一旦遇到名次下降（<=0）的因子即截止；上限 10 个。

打分口径：
- 个股：对每个入选因子在最新截面做横截面分位（正向因子取分位本身，反向因子取 1-分位），
  入选因子等权平均得综合分。**要求全部入选因子都有取值**——否则样本因子数不同、
  少因子样本可凭极值抬高均值，排名不可比（此前 3 因子样本挤进 Top10 即此因）。
  再过滤 总市值 >= 30 亿、剔除 ST/退市。
- 行业：按 stock_basic.industry 归组，成员综合分等权平均；行业内样本 >= 10 只取 Top5。

输出：
- raw/top_factor_picks.json（供 push_weekly.py 前置卡片读取）
- 标准输出打印入选因子 / 行业 / 个股，便于人工核对

自校验（任一不符即报错退出，避免手写或错口径数值进入周报）：
1) 入选因子名次连续 1..k，且第 k+1 名因子名次已下降；
2) 入选因子与桑基图数据源 raw/sankey_flow.csv 的最右列逐条一致（因子名 + 名次）；
3) 桑基图最右列 26 个因子的名次与 raw/factor_weekly_pivot.csv 的 rank_this_week 全表一致。
"""
import os
import json
import sys

import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
DIAG = os.path.join(os.path.dirname(RAW), "diag")
PROFILE_FILE = os.path.join(DIAG, "selected_profile.json")


def active_profile():
    """默认档位：读 diag/selected_profile.json（当前 = P0A2），可用 --profile 覆盖。"""
    name = "P0A2"
    if os.path.exists(PROFILE_FILE):
        try:
            name = json.load(open(PROFILE_FILE)).get("default", name)
        except Exception:
            pass
    for i, a in enumerate(sys.argv):
        if a == "--profile" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    return name
OUT_JSON = os.path.join(RAW, "top_factor_picks.json")
CAP = 10
MIN_MV = 300000        # 万元 => 30 亿
MIN_INDUSTRY_N = 10
N_STOCK = 10
N_INDUSTRY = 5


def latest_rank_col(cols):
    rcs = sorted(c for c in cols if c.startswith("rank_") and len(c) == 13)
    return rcs[-1], rcs[-1].replace("rank_", "")


def select_factors(flow):
    """按桑基图最右列名次从 1 往下取，遇首个名次下降即截止（上限 CAP）。"""
    rank_col, week = latest_rank_col(list(flow.columns))
    f = flow.sort_values(rank_col).reset_index(drop=True)
    picked = []
    for _, r in f.iterrows():
        chg = r["rank_change_4w"]
        if chg is None or pd.isna(chg) or chg <= 0:
            break
        picked.append({
            "factor": str(r["factor"]),
            "direction": int(r["direction"]),
            "rank": int(r[rank_col]),
            "rank_change_4w": int(chg),
        })
        if len(picked) >= CAP:
            break
    # k=0 分支（2026-11-01 起）：名次最高的因子本周未上升（rank_change_4w<=0），
    # 按固化口径「名次从 1 往下取、遇首个下降(<=0)即截止」，本周入选为空集。
    # 规则不放宽、不绕过、不手写任何名单；仅如实输出空入选，供前置卡说明本周无新晋强势因子。
    if not picked:
        print("[select] 本周无满足条件（名次上升）的入选因子（k=0；规则不放宽）", flush=True)
    # 自校验 1：名次连续 1..k，且截断原因可解释——
    #   未满额 → 下一名已转降（≤0）；满额 → 下一名仍在上升（截断由「上限 10 个」触发）。
    for i, p in enumerate(picked, start=1):
        if p["rank"] != i:
            raise SystemExit(f"[ERR] 入选因子名次不连续：{p}")
        if p["factor"] != str(f.iloc[i - 1]["factor"]):
            raise SystemExit(f"[ERR] 入选因子与桑基图顺序不符：{p}")
    nxt = f.iloc[len(picked)]
    nxt_chg = nxt["rank_change_4w"]
    capped = len(picked) >= CAP
    if capped:
        # 满额截断分支（2026-11-08 起补齐）：本周第 1~11 名名次连续上升，
        # 选取由「上限 10 个」截断而非遇到下降。此时要求紧随其后一名仍在上升，
        # 否则说明截断位置异常（本应继续取或本应更早截止），报错交人工复核。
        if not (pd.notna(nxt_chg) and int(nxt_chg) > 0):
            raise SystemExit("[ERR] 已满额但下一名未上升：截断位置异常，需人工复核")
    elif pd.notna(nxt_chg) and int(nxt_chg) > 0:
        raise SystemExit("[ERR] 截断位置有误：下一名因子名次仍上升")
    # 自校验 3：桑基图最右列 vs 周度排名表 rank_this_week 全表一致
    piv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"), dtype={"factor": str})
    piv = piv.set_index("factor")
    bad = []
    for _, r in flow.iterrows():
        fac = str(r["factor"])
        if fac not in piv.index:
            bad.append(f"{fac}(周度排名表缺行)")
            continue
        if int(float(piv.loc[fac, "rank_this_week"])) != int(r[rank_col]):
            bad.append(f"{fac}(表 {int(float(piv.loc[fac, 'rank_this_week']))} != 图 {int(r[rank_col])})")
    if bad:
        raise SystemExit("[ERR] 桑基图最右列与周度排名表不一致：" + "；".join(bad))
    if capped:
        cut_desc = (f"已达上限 {CAP} 个（紧随其后的第 {len(picked) + 1} 名 {nxt['factor']} "
                    f"仍在上升 {int(nxt_chg):+d}）")
    else:
        cut_desc = f"截断于第 {len(picked) + 1} 名 {nxt['factor']}（{int(nxt_chg):+d}）"
    print(f"[select] 最新截面 {week} | 入选因子 {len(picked)} 个（名次上升）"
          f" | {cut_desc}"
          f" | 已与桑基图最右列 + 周度排名表三源互证", flush=True)
    cutoff = {"factor": str(nxt["factor"]), "rank": int(nxt[rank_col]),
              "direction": int(nxt["direction"]),
              "rank_change_4w": int(nxt_chg),
              "reason": "cap" if capped else "fall"}
    return week, picked, cutoff


def main():
    flow = pd.read_csv(os.path.join(RAW, "sankey_flow.csv"))
    week, picked, cutoff = select_factors(flow)

    xsec = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"))
    basic = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype={"symbol": str})
    d = xsec.merge(basic[["ts_code", "name", "industry"]], on="ts_code", how="left")
    tds = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"trade_date": str},
                      usecols=["trade_date"])
    xsec_date = str(tds["trade_date"].max())

    for p in picked:
        if p["factor"] not in d.columns:
            raise SystemExit(f"[ERR] 最新截面缺少因子列 {p['factor']}")

    S = pd.DataFrame(index=d.index)
    for p in picked:
        x = pd.to_numeric(d[p["factor"]], errors="coerce")
        pr = x.rank(pct=True)
        S[p["factor"]] = pr if p["direction"] > 0 else (1.0 - pr)
    d["score"] = S.mean(axis=1, skipna=True)
    d["n_valid"] = S.notna().sum(axis=1)
    d["mv"] = pd.to_numeric(d["总市值"], errors="coerce")

    # ---- 候选池：全部入选因子均有值 + ≥30 亿 + 非 ST/退 ----
    ok = d[(d["n_valid"] == len(picked)) & d["score"].notna() & d["mv"].notna()]
    ok = ok[ok["name"].notna() & ~ok["name"].astype(str).str.contains("ST|退", regex=True)]
    ok = ok[ok["mv"] >= MIN_MV]

    # ---- 档位叠加：P0A2 / 渐进式都要过「个股均线」这道闸，并给出目标仓位系数 ----
    prof = active_profile()
    ma_note = "未启用均线闸（27 因子里已是趋势类候选）"
    position_ratio, ma_pass_ratio, n_before = 1.0, None, int(len(ok))
    if prof in ("P0A2", "PROG_ENTRY_07", "PROG_ENTRY_05", "PROG_FULL_07"):
        px = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"trade_date": str},
                         usecols=["ts_code", "trade_date", "close"])
        pv = px.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
        c = pv.iloc[-1]
        ma10, ma20, ma40 = pv.rolling(10).mean().iloc[-1], pv.rolling(20).mean().iloc[-1], pv.rolling(40).mean().iloc[-1]
        above40 = c > ma40
        ma_or = above40 | (ma10 > ma20)
        ma_pass_ratio = float(ma_or.mean())
        position_ratio = float(min(1.0, ma_pass_ratio / 0.60))
        ok = ok[ok["ts_code"].map(ma_or).fillna(False)]
        ma_note = (f"已过均线闸（站上 MA40 或 MA10>MA20）；全样本过关比例 "
                   f"{ma_pass_ratio * 100:.1f}%")

    # ---- 个股 Top10 ----
    ok = ok.sort_values(["score", "mv"], ascending=[False, False], kind="mergesort")
    stocks = []
    for _, r in ok.head(N_STOCK).iterrows():
        stocks.append({"name": str(r["name"]), "industry": str(r["industry"]),
                       "score": round(float(r["score"]), 4),
                       "mv_yi": round(float(r["mv"]) / 10000.0, 1)})

    # ---- 行业 Top5 ----
    g = ok.groupby("industry").agg(n=("score", "size"), score=("score", "mean"))
    g = g[g["n"] >= MIN_INDUSTRY_N].sort_values(["score", "n"], ascending=[False, False],
                                                kind="mergesort")
    industries = [{"industry": str(i), "score": round(float(r["score"]), 4), "n": int(r["n"])}
                  for i, r in g.head(N_INDUSTRY).iterrows()]

    payload = {"week": week, "xsec_date": xsec_date, "factors": picked, "cutoff": cutoff,
               "industries": industries, "stocks": stocks,
               "pool_size": int(len(ok)),
               "rule": "名次从 1 往下取，仅留名次上升因子，遇首个下降即截止（上限 10 个）",
               "profile": prof, "position_ratio": round(position_ratio, 3),
               "ma_pass_ratio": (None if ma_pass_ratio is None else round(ma_pass_ratio, 3)),
               "n_before_ma": n_before, "ma_note": ma_note,
               "caliber": ("因子名次仍按 27 因子周度多空收益排名（周报口径）；"
                           "名单叠加 P0A2 的个股均线闸与市场仓位系数。回测口径为 PIT14 池，"
                           "两者因子池不同，收益不可直接等同。")}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("\n=== 入选强势上升因子（名次 / 4周变化 / 方向）===")
    for p in picked:
        print(f"  第{p['rank']}名 {p['factor']}  {p['rank_change_4w']:+d}  "
              f"{'正向' if p['direction'] > 0 else '反向'}")
    print(f"\n档位 {prof} | 均线闸前 {n_before} 只 → 闸后 {len(ok)} 只 | {ma_note}")
    print(f"\n候选池（全部 {len(picked)} 个因子均有值、≥30 亿、非 ST/退、过均线闸）：{len(ok)} 只")
    print("\n=== 行业 Top5（入选因子综合分）===")
    for i, x in enumerate(industries, 1):
        print(f"  {i}. {x['industry']}  得分 {x['score']:.4f}  样本 {x['n']}")
    print("\n=== 个股 Top10（名称 / 行业 / 综合分 / 市值亿元）===")
    for i, x in enumerate(stocks, 1):
        print(f"  {i}. {x['name']:<8} {x['industry']:<8} {x['score']:.4f}  {x['mv_yi']}")
    print(f"\n[out] {OUT_JSON}")


if __name__ == "__main__":
    main()