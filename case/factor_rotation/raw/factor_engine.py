# -*- coding: utf-8 -*-
"""A股因子旋转周报 - 因子计算引擎
输入：raw/daily_260d.csv, raw/daily_basic_65d.csv, raw/cons_*.csv,
      raw/yjbb_*.csv, raw/profit_forecast.csv,
      raw/fund_hold_*.csv, raw/inst_hold_*.csv（机构/公募重仓真实持仓）
输出：raw/factor_weekly.csv（每周因子截面+周度多空收益），供RRG与报告使用
"""
import os, json, warnings
import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
os.makedirs(OUT, exist_ok=True)

# ---------------- 1. 加载数据 ----------------
daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"ts_code": str, "trade_date": str})
basic = pd.read_csv(os.path.join(RAW, "daily_basic_65d.csv"), dtype={"ts_code": str, "trade_date": str})
cal = pd.read_csv(os.path.join(RAW, "trade_cal.csv"))
cal["trade_date"] = pd.to_datetime(cal["trade_date"])

# 指数成分并集（样本空间：HS300+ZZ500+ZZ1000+ZZA500，公募重仓以A500代理并标注）
cons_sets = {}
for tag in ["HS300", "ZZ500", "ZZ1000", "ZZA500"]:
    df = pd.read_csv(os.path.join(RAW, f"cons_{tag}.csv"))
    cons_sets[tag] = set(df["成分券代码"].astype(int).astype(str).str.zfill(6))

universe = set()
for s in cons_sets.values():
    universe |= s
# 统一转为6位代码（daily.ts_code 形如 000001.SZ）
def normalize(code):
    return str(code).split(".")[0].zfill(6)
universe6 = {normalize(c) for c in universe}
print(f"[universe] {len(universe6)} 只（HS300∪ZZ500∪ZZ1000∪ZZA500）", flush=True)

# ---------------- 2. 载入财务/预期数据 ----------------
yjbb = {}
for tag in ["2025Q3", "2025A", "2026Q1", "2026Q2"]:
    fp = os.path.join(RAW, f"yjbb_{tag}.csv")
    if os.path.exists(fp):
        yjbb[tag] = pd.read_csv(fp)
        print(f"[yjbb] {tag} cols={list(yjbb[tag].columns)[:20]} rows={len(yjbb[tag])}", flush=True)

forecast = None
fpf = os.path.join(RAW, "profit_forecast.csv")
if os.path.exists(fpf):
    forecast = pd.read_csv(fpf)
    print(f"[forecast] cols={list(forecast.columns)} rows={len(forecast)}", flush=True)

# ---------------- 2.5 载入机构/公募重仓持仓数据（东财 dataapi，真实报告期 2026-06-30） ----------------
INST = {}
for tag, fname, label in [
    ("fund", "fund_hold", "基金"),
    ("qfii", "inst_hold_qfii", "QFII"),
    ("social", "inst_hold_social", "社保"),
    ("broker", "inst_hold_broker", "券商"),
    ("insurance", "inst_hold_insurance", "保险"),
    ("trust", "inst_hold_trust", "信托"),
]:
    fp = os.path.join(RAW, f"{fname}_20260630.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp)
        df["code6"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
        INST[tag] = df.drop_duplicates(subset=["code6"], keep="first").set_index("code6")
        print(f"[inst] {label} report=20260630 rows={len(INST[tag])}", flush=True)
INST_OK = bool(INST)
print(f"[inst] 机构/公募持仓数据就绪: {INST_OK}", flush=True)

# ---------------- 3. 因子定义 ----------------
# (名称, 分组, 方向sign, 方向说明)  方向：+1=正向（因子值越大越好），-1=反向（因子值越小越好，计算时×-1）
FACTORS = [
    # 估值(7)
    ("BP", "估值", +1, "正向（PB越低越好，BP=1/PB 越高越好）"),
    ("单季EP", "估值", +1, "正向（单季EPS/股价越高越好）"),
    ("单季SP", "估值", +1, "正向（单季营收/市值越高越好）"),
    ("EPTTM", "估值", +1, "正向（滚动PE越低越好，EP=1/PE 越高越好）"),
    ("SPTTM", "估值", +1, "正向（滚动PS越低越好，SP=1/PS 越高越好）"),
    ("EPTTM一年分位点", "估值", +1, "正向（EP在自身历史中处于高分位=相对便宜）"),
    ("股息率", "估值", +1, "正向（股息率越高越好）"),
    # 反转动量(3)
    ("一个月反转", "反转动量", -1, "反向（过去20日涨幅越高越危险，越低越好）"),
    ("三个月反转", "反转动量", -1, "反向（过去60日涨幅越高越危险，越低越好）"),
    ("一年动量", "反转动量", +1, "正向（过去250日收益越高越好）"),
    # 成长(6)
    ("单季净利同比增速", "成长", +1, "正向（净利同比增速越高越好）"),
    ("单季营收同比增速", "成长", +1, "正向（营收同比增速越高越好）"),
    ("单季营利同比增速", "成长", +1, "正向（营业利润同比增速越高越好）"),
    ("SUE", "成长", +1, "正向（标准化未预期盈利越高越好）"),
    ("SUR", "成长", +1, "正向（标准化未预期营收越高越好）"),
    ("单季超预期幅度", "成长", +1, "正向（实际-预期盈余超预期幅度越大越好）"),
    # 盈利(5)
    ("单季ROE", "盈利", +1, "正向（净资产收益率越高越好）"),
    ("单季ROA", "盈利", +1, "正向（总资产收益率越高越好）"),
    ("DELTAROE", "盈利", +1, "正向（ROE环比改善越好）"),
    ("DELTAROA", "盈利", +1, "正向（ROA环比改善越好）"),
    # 流动(3)
    ("非流动性冲击", "流动", -1, "反向（Amihud非流动性越小=流动性越好）"),
    ("一个月换手", "流动", -1, "反向（20日换手率越低越好）"),
    ("三个月换手", "流动", -1, "反向（60日换手率越低越好）"),
    # 波动(3)
    ("一个月波动", "波动", -1, "反向（20日收益波动越低越好）"),
    ("三个月波动", "波动", -1, "反向（60日收益波动越低越好）"),
    ("特异度", "波动", -1, "反向（60日残差波动越低越好）"),
    # 治理(1)
    ("高管薪酬", "治理", +1, "正向（高管薪酬水平越高激励越好）"),
    # 预期(6)
    ("预期EPTTM", "预期", +1, "正向（预期盈利收益率越高=预测PE越低越好）"),
    ("预期BP", "预期", +1, "正向（预期净资产折价越低越好）"),
    ("预期PEG", "预期", +1, "正向（预测PE/预测增速越低越好）"),
    ("预期PEG三年复合", "预期", +1, "正向（预测PE/三年复合增速越低越好；与一年增速版并行，均可比）"),
    ("预期净利润环比", "预期", +1, "正向（预期盈利环比改善越大越好）"),
    ("三个月盈利上下调", "预期", +1, "正向（盈利上下调净幅度越大越好）"),
    ("三个月机构覆盖", "预期", +1, "正向（近6个月研报覆盖越多越好）"),
    # 机构/公募重仓(2) —— 真实持仓数据（2026-06-30 报告期）
    ("公募重仓", "机构", +1, "正向（公募基金持股占流通比越高=机构重仓越集中）"),
    ("机构持仓", "机构", +1, "正向（QFII+社保+券商+保险+信托合计持仓家数越多=机构关注越高）"),
]

# 实际计算口径（标注数据缺口用）
AVAIL = {}  # factor -> (status, note)

# ---------------- 4. 主计算 ----------------
def main():
    global daily
    # 4.1 每周截面日（周五或周最后交易日）
    dates = sorted(daily["trade_date"].unique())
    daily = daily.sort_values(["ts_code", "trade_date"])
    # 每周最后一个交易日
    dt = pd.to_datetime(daily["trade_date"])
    week = dt.dt.to_period("W-FRI").rename("week")
    weekly_last = daily.groupby(week)["trade_date"].max().reset_index()
    weekly_last = weekly_last.rename(columns={"trade_date": "week_end"})
    week_ends = sorted(weekly_last["week_end"].unique())
    print(f"[weeks] {len(week_ends)} 个周截面: {week_ends[0]} .. {week_ends[-1]}", flush=True)

    # 未来一周收益（下一周 5 个交易日收益连乘）
    # 护栏：前瞻不足 5 个交易日的「半周」不参与因子排名。周中刷新数据时最新一周
    # 只走了 1~3 天，若照算会得到失真的周度多空收益与因子名次
    # （实测：2026-10-23 那周只跑到 10-28 时，会把「三个月反转」从第 1 名打到第 23 名）。
    ret_df = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg") / 100.0
    fwd, skipped = {}, []
    for we in week_ends:
        idx = dates.index(we)
        nxt = dates[idx + 1: idx + 6]  # 下一周（5 个交易日）
        if len(nxt) < 5:
            skipped.append(we)
            continue
        rr = (1 + ret_df.loc[nxt]).prod(skipna=True) - 1
        fwd[we] = rr
    if skipped:
        print(f"[skip] 前瞻不足 5 个交易日（半周），剔除 {len(skipped)} 个周截面: {skipped}", flush=True)
    fwd = pd.DataFrame(fwd).T
    fwd.index.name = "week_end"

    # 基准 = 全样本等权周收益
    bench = fwd.mean(axis=1)

    records = []
    for we in week_ends:
        # 截面因子值
        xsec = build_cross_section(we, week_ends)
        # 保存最新周截面（股票级，供主线龙头股检索）
        if we == week_ends[-1]:
            xs_out = xsec.copy()
            xs_out.to_csv(os.path.join(RAW, "latest_cross_section.csv"), index=False)
            print(f"[xsec] latest {we} saved rows={len(xs_out)}", flush=True)
        # 下周收益
        if we not in fwd.index:
            continue
        fw = fwd.loc[we]
        for fname, fgroup, direction, dirnote in FACTORS:
            if fname not in xsec.columns:
                AVAIL[fname] = ("数据不可用", "无数据源")
                continue
            vals = xsec[["ts_code", fname]].dropna()
            if len(vals) < 60:
                AVAIL[fname] = ("数据不足", f"有效样本{len(vals)}")
                continue
            merged = vals.merge(fw.rename("fut_ret"), left_on="ts_code", right_index=True, how="inner").dropna()
            if len(merged) < 60:
                AVAIL[fname] = ("数据不足", f"有收益样本{len(merged)}")
                continue
            # 方向处理：乘以direction（算法统一按"大=好"）
            merged["z"] = merged[fname] * direction
            q = merged["z"].rank(pct=True)
            top = merged.loc[q >= 0.8, "fut_ret"].mean()
            bot = merged.loc[q <= 0.2, "fut_ret"].mean()
            if np.isnan(top) or np.isnan(bot):
                continue
            ls = top - bot
            ex = top - bench.loc[we]
            records.append({
                "week_end": we, "factor": fname, "group": fgroup, "direction": direction,
                "direction_note": dirnote,
                "weekly_long_short_return": round(float(ls), 6),
                "excess_vs_benchmark": round(float(ex), 6),
                "n_cross": int(len(merged)),
            })
            AVAIL[fname] = ("已计算", f"样本{len(merged)}")
    df = pd.DataFrame(records)
    df.to_csv(os.path.join(RAW, "factor_weekly_raw.csv"), index=False)
    print(f"[factor_weekly] rows={len(df)}", flush=True)

    # 4.2 周排名（本周/上周）
    pivot = df.pivot(index="factor", columns="week_end", values="weekly_long_short_return")
    # 用最近两个周截面
    weeks_sorted = pivot.columns.tolist()
    if len(weeks_sorted) >= 2:
        pivot["rank_this_week"] = pivot[weeks_sorted[-1]].rank(ascending=False)
        pivot["rank_last_week"] = pivot[weeks_sorted[-2]].rank(ascending=False)
    pivot.to_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
    print("[pivot] saved", flush=True)

    # 4.3 可用性输出
    with open(os.path.join(RAW, "factor_avail.json"), "w", encoding="utf-8") as f:
        json.dump(AVAIL, f, ensure_ascii=False, indent=2)
    print("[avail]", json.dumps(AVAIL, ensure_ascii=False), flush=True)

def build_cross_section(we, week_ends):
    """在周截面日 we 计算全部可用因子截面"""
    # 基础行情截面
    d = daily[daily["trade_date"] == we].copy()
    if d.empty:
        return pd.DataFrame(columns=["ts_code"])
    d = d[d["ts_code"].map(lambda c: normalize(c) in universe6)].copy()
    d["code6"] = d["ts_code"].map(normalize)
    d = d.set_index("code6")
    xs = pd.DataFrame({"ts_code": d["ts_code"].values}, index=d.index)
    xs["close"] = d["close"].values

    # 估值截面（daily_basic）
    b = basic[basic["trade_date"] == we].copy()
    if not b.empty:
        b["code6"] = b["ts_code"].map(normalize)
        b = b.set_index("code6")
        xs["BP"] = 1.0 / b["pb"].replace(0, np.nan)
        xs["EPTTM"] = 1.0 / b["pe_ttm"].replace(0, np.nan)
        xs["SPTTM"] = 1.0 / b["ps_ttm"].replace(0, np.nan)
        xs["股息率"] = b["dv_ttm"]
        xs["总市值"] = b["total_mv"]
        xs["换手率"] = b["turnover_rate"]
        # EPTTM一年分位点 -> 用近3个月分位代理（数据缺口标注）
        hist_basic = basic[basic["trade_date"] <= we].copy()
        if len(hist_basic["trade_date"].unique()) >= 10 and we in hist_basic["trade_date"].unique():
            hb = hist_basic[["ts_code", "trade_date", "pe_ttm"]].copy()
            hb["code6"] = hb["ts_code"].map(normalize)
            hb["epttm"] = 1.0 / hb["pe_ttm"].replace(0, np.nan)
            # 每只股票在观察窗口内的分位（简化：用每只股票截至当日的分位）
            hb = hb.sort_values(["code6", "trade_date"])
            hb["pct"] = hb.groupby("code6")["epttm"].rank(pct=True)
            last_pct = hb[hb["trade_date"] == we][["code6", "pct"]]
            xs["EPTTM一年分位点"] = last_pct.set_index("code6")["pct"].reindex(xs.index)
        else:
            xs["EPTTM一年分位点"] = np.nan
    else:
        for c in ["BP", "EPTTM", "SPTTM", "股息率", "总市值", "换手率", "EPTTM一年分位点"]:
            xs[c] = np.nan

    # 单季EP/单季SP/成长/盈利 -> 用 yjbb
    xs = add_fundamental(xs, we)

    # 预期类 -> 用 profit_forecast
    xs = add_expected(xs, we)

    # 机构/公募重仓类 -> 用最新报告期(2026-06-30)真实持仓
    xs = add_institution(xs, we)

    # 时间序列类（动量/反转/波动/换手/流动性）
    xs = add_ts_factors(xs, we)

    return xs.reset_index(drop=True)

def add_fundamental(xs, we):
    if not yjbb:
        return xs
    # 用最新可用报告期（2026Q2）
    cur = yjbb.get("2026Q2")
    prev = yjbb.get("2026Q1")
    if cur is None:
        return xs
    # 统一代码
    def code_of(series):
        return series.astype(str).str.split(".").str[0].str.zfill(6)
    cur = cur.copy(); cur["code6"] = code_of(cur["股票代码"] if "股票代码" in cur.columns else cur.iloc[:, 1])
    cur = cur.drop_duplicates(subset=["code6"], keep="first")
    cur = cur.set_index("code6")
    if prev is not None:
        prev = prev.copy(); prev["code6"] = code_of(prev["股票代码"] if "股票代码" in prev.columns else prev.iloc[:, 1])
        prev = prev.drop_duplicates(subset=["code6"], keep="first")
        prev = prev.set_index("code6")
    for col_disp, col_candidates in [
        ("单季ROE", ["净资产收益率"]),
        ("单季净利同比增速", ["净利润-同比增长"]),
        ("单季营收同比增速", ["营业总收入-同比增长"]),
        ("单季营利同比增速", ["营业总利润同比" if "营业总利润同比" in cur.columns else "__none__", "没有营业利润同比"]),
        ("单季ROA", ["总资产收益率" if "总资产收益率" in cur.columns else "__none__", "没有ROA"]),
    ]:
        col = next((c for c in col_candidates if c in cur.columns), None)
        if col:
            xs[col_disp] = cur[col].reindex(xs.index)
            AVAIL.setdefault(col_disp, ("已计算", f"来源{col}"))
        else:
            xs[col_disp] = np.nan
            AVAIL.setdefault(col_disp, ("数据不可用", f"财务报表无字段{col_candidates[0]}"))
    # DELTAROE / DELTAROA
    if prev is not None:
        roe_col = next((c for c in ["净资产收益率"] if c in cur.columns and c in prev.columns), None)
        if roe_col:
            xs["DELTAROE"] = cur[roe_col].reindex(xs.index) - prev[roe_col].reindex(xs.index)
            AVAIL.setdefault("DELTAROE", ("已计算", "本期-上期"))
        else:
            xs["DELTAROE"] = np.nan
            AVAIL.setdefault("DELTAROE", ("数据不可用", ""))
        roa_col = next((c for c in ["总资产收益率"] if c in cur.columns and c in prev.columns), None)
        if roa_col:
            xs["DELTAROA"] = cur[roa_col].reindex(xs.index) - prev[roa_col].reindex(xs.index)
            AVAIL.setdefault("DELTAROA", ("已计算", "本期-上期"))
        else:
            xs["DELTAROA"] = np.nan
            AVAIL.setdefault("DELTAROA", ("数据不可用", ""))
    else:
        xs["DELTAROE"] = np.nan; xs["DELTAROA"] = np.nan
        AVAIL.setdefault("DELTAROE", ("数据不可用", "")); AVAIL.setdefault("DELTAROA", ("数据不可用", ""))
    # 单季EP / 单季SP（每股收益/营收，单季=本期-上期）
    eps_candidates = ["每股收益"]
    rev_candidates = ["营业总收入-营业总收入"]
    if prev is not None:
        eps_col = next((c for c in eps_candidates if c in cur.columns and c in prev.columns), None)
        rev_col = next((c for c in rev_candidates if c in cur.columns and c in prev.columns), None)
        if eps_col and "close" in xs.columns:
            eps_q = cur[eps_col].reindex(xs.index) - prev[eps_col].reindex(xs.index)
            xs["单季EP"] = eps_q / xs["close"]
            AVAIL.setdefault("单季EP", ("已计算", "单季EPS/股价"))
        if rev_col and "总市值" in xs.columns:
            rev_q = cur[rev_col].reindex(xs.index) - prev[rev_col].reindex(xs.index)
            xs["单季SP"] = rev_q / (xs["总市值"] * 1e4)  # 总市值万元->元
            AVAIL.setdefault("单季SP", ("已计算", "单季营收/市值"))
    # SUE/SUR/单季超预期幅度：无一致预期单季数据
    for fn in ["SUE", "SUR", "单季超预期幅度"]:
        xs[fn] = np.nan
        AVAIL.setdefault(fn, ("数据不可用", "无单季一致预期与预期偏差数据"))
    return xs

def add_expected(xs, we):
    if forecast is None:
        for fn in ["预期EPTTM", "预期BP", "预期PEG", "预期PEG三年复合", "预期净利润环比", "三个月盈利上下调", "三个月机构覆盖"]:
            xs[fn] = np.nan
            AVAIL.setdefault(fn, ("数据不可用", "盈利预测数据缺失"))
        return xs
    fc = forecast.copy()
    fc["code6"] = fc["代码"].astype(str).str.split(".").str[0].str.zfill(6)
    fc = fc.drop_duplicates(subset=["code6"], keep="first")
    fc = fc.set_index("code6")
    e2025 = pd.to_numeric(fc["2025预测每股收益"], errors="coerce")
    e2026 = pd.to_numeric(fc["2026预测每股收益"], errors="coerce")
    e2027 = pd.to_numeric(fc.get("2027预测每股收益"), errors="coerce") if "2027预测每股收益" in fc.columns else pd.Series(np.nan, index=fc.index)
    e2028 = pd.to_numeric(fc.get("2028预测每股收益"), errors="coerce") if "2028预测每股收益" in fc.columns else pd.Series(np.nan, index=fc.index)
    close = xs["close"]
    # 预期EPTTM = 2026预测EPS / 股价
    xs["预期EPTTM"] = (e2026 / close).reindex(xs.index)
    AVAIL.setdefault("预期EPTTM", ("代理变量", "2026预测EPS/股价(东财盈利预测)"))
    # 预期BP：无预测净资产数据 -> 数据不可用
    xs["预期BP"] = np.nan
    AVAIL.setdefault("预期BP", ("数据不可用", "无预测净资产/预期PB"))
    # 预期PEG = 预测PE / 预测增速
    g = (e2026 / e2025 - 1).replace([np.inf, -np.inf], np.nan)
    pe_f = (close / e2026).replace([np.inf, -np.inf], np.nan)
    xs["预期PEG"] = (pe_f / (g * 100)).reindex(xs.index)
    AVAIL.setdefault("预期PEG", ("代理变量", "预测PE/预测EPS增速(百分数)"))
    # 预期PEG三年复合（选项B：与一年增速版并行）= 预测PE / 三年复合增速
    # 三年 CAGR g3 = (2028预测EPS/2025预测EPS)^(1/3) - 1 ；分子分母均换成预测值
    pos = (e2025 > 0) & (e2028 > 0)
    g3 = pd.Series(np.nan, index=fc.index)
    g3[pos] = (e2028[pos] / e2025[pos]) ** (1.0 / 3.0) - 1.0
    g3 = g3.replace([np.inf, -np.inf], np.nan)
    xs["预期PEG三年复合"] = (pe_f / (g3 * 100)).reindex(xs.index)
    AVAIL.setdefault("预期PEG三年复合", ("代理变量", "预测PE/((2028/2025)^(1/3)-1 三年复合增速×100)"))
    # 预期净利润环比（预测同比） = 2026/2025 - 1
    xs["预期净利润环比"] = g.reindex(xs.index)
    AVAIL.setdefault("预期净利润环比", ("代理变量", "2026预测EPS/2025预测EPS-1"))
    # 三个月盈利上下调：无上下调幅度 -> 用评级占比代理
    buy = pd.to_numeric(fc.get("机构投资评级(近六个月)-买入"), errors="coerce").fillna(0)
    hold = pd.to_numeric(fc.get("机构投资评级(近六个月)-增持"), errors="coerce").fillna(0)
    nr = pd.to_numeric(fc.get("研报数"), errors="coerce")
    ratio = (buy + hold) / nr.replace(0, np.nan)
    xs["三个月盈利上下调"] = ratio.reindex(xs.index)
    AVAIL.setdefault("三个月盈利上下调", ("代理变量", "近6个月买入+增持评级占比(无上下调幅度)"))
    # 三个月机构覆盖 = 研报数
    xs["三个月机构覆盖"] = nr.reindex(xs.index)
    AVAIL.setdefault("三个月机构覆盖", ("代理变量", "近6个月研报数"))
    return xs

def add_institution(xs, we):
    """机构/公募重仓因子：基于最新报告期（2026-06-30）真实持仓。
    - 公募重仓：基金持股占流通股比（FREESHARES_RATIO）
    - 机构持仓：QFII+社保+券商+保险+信托合计持仓家数（HOULD_NUM 之和）
    持仓为季频披露：2026-06-30 报告期数据约 2026-07 月下旬披露，因此仅在
    we >= 2026-07-24（披露后的周截面）启用，避免用未来数据污染历史截面。
    """
    if not INST:
        return xs
    xs["公募重仓"] = np.nan
    xs["机构持仓"] = np.nan
    if int(we) < 20260724:   # 2026-06-30 报告期约 7 月下旬披露，披露前的周截面不启用
        return xs
    fund = INST.get("fund")
    if fund is not None and len(fund):
        s = pd.to_numeric(fund["FREESHARES_RATIO"], errors="coerce")
        xs["公募重仓"] = s.reindex(xs.index)
        AVAIL.setdefault("公募重仓", ("已计算", "基金持股占流通股比(2026-06-30报告期,东财dataapi)"))
    # 机构持仓 = 各机构持仓家数合计
    tot = None
    inst_tags = ["qfii", "social", "broker", "insurance", "trust"]
    for tag in inst_tags:
        dfi = INST.get(tag)
        if dfi is None or not len(dfi):
            continue
        s = pd.to_numeric(dfi["HOULD_NUM"], errors="coerce").reindex(xs.index).fillna(0)
        tot = s if tot is None else tot + s
    if tot is not None:
        xs["机构持仓"] = tot
        AVAIL.setdefault("机构持仓", ("已计算", "QFII+社保+券商+保险+信托持仓家数合计(2026-06-30报告期,东财dataapi)"))
    return xs

def add_ts_factors(xs, we):
    # 每只股票过去20/60/250交易日的时间序列因子
    dates = sorted(daily["trade_date"].unique())
    if we not in dates:
        return xs
    idx = dates.index(we)
    code6s = xs.index  # already code6
    pivot_pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg") / 100.0
    pivot_amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount")
    # 列名统一转成6位代码，与 xs.index (code6) 对齐
    pivot_pct.columns = [normalize(c) for c in pivot_pct.columns]
    pivot_amt.columns = [normalize(c) for c in pivot_amt.columns]
    cols = [c for c in pivot_pct.columns if c in universe6]
    p = pivot_pct[cols].iloc[max(0, idx - 250): idx + 1]
    amt = pivot_amt[cols].iloc[max(0, idx - 20): idx + 1]
    # 一个月/三个月/一年收益
    def cum_ret(n):
        seg = p.iloc[-n:]
        return (1 + seg).prod(skipna=True) - 1
    xs["一个月反转"] = cum_ret(20).reindex(xs.index)
    xs["三个月反转"] = cum_ret(60).reindex(xs.index)
    xs["一年动量"] = cum_ret(250).reindex(xs.index)
    AVAIL.setdefault("一个月反转", ("已计算", "过去20交易日收益,direction=-1"))
    AVAIL.setdefault("三个月反转", ("已计算", "过去60交易日收益,direction=-1"))
    AVAIL.setdefault("一年动量", ("已计算", "过去250交易日收益,direction=+1"))
    # 波动
    xs["一个月波动"] = p.iloc[-20:].std().reindex(xs.index)
    xs["三个月波动"] = p.iloc[-60:].std().reindex(xs.index)
    AVAIL.setdefault("一个月波动", ("已计算", "")); AVAIL.setdefault("三个月波动", ("已计算", ""))
    # 特异度：对全样本等权市场收益回归残差std（60日）
    mkt = p.mean(axis=1)
    res_std = {}
    for c in cols:
        y = p[c].iloc[-60:]
        x = mkt.iloc[-60:]
        mask = y.notna() & x.notna()
        if mask.sum() < 30:
            res_std[c] = np.nan
            continue
        yy, xx = y[mask], x[mask]
        slope = np.polyfit(xx.values, yy.values, 1)
        resid = yy.values - np.polyval(slope, xx.values)
        res_std[c] = np.std(resid)
    xs["特异度"] = pd.Series(res_std).reindex(xs.index)
    AVAIL.setdefault("特异度", ("已计算", "60日残差波动"))
    # 非流动性冲击 Amihud = mean(|ret|/amount)*1e9
    ret_abs = p.iloc[-20:].abs()
    amt20 = amt.iloc[-20:]
    amihud = (ret_abs / amt20.replace(0, np.nan)).mean() * 1e9
    xs["非流动性冲击"] = amihud.reindex(xs.index)
    AVAIL.setdefault("非流动性冲击", ("已计算", "20日|ret|/amount"))
    # 换手（基于 daily_basic 的 turnover_rate 汇总）
    lb = basic[basic["trade_date"] <= we].copy()
    if not lb.empty:
        lb["code6"] = lb["ts_code"].map(normalize)
        lb = lb.sort_values(["code6", "trade_date"])
        lb["turn"] = pd.to_numeric(lb["turnover_rate"], errors="coerce")
        tr20 = lb.groupby("code6")["turn"].apply(lambda s: s.tail(20).mean())
        tr60 = lb.groupby("code6")["turn"].apply(lambda s: s.tail(60).mean())
        xs["一个月换手"] = tr20.reindex(xs.index)
        xs["三个月换手"] = tr60.reindex(xs.index)
        AVAIL.setdefault("一个月换手", ("已计算", "20日均换手")); AVAIL.setdefault("三个月换手", ("已计算", "60日均换手"))
    else:
        xs["一个月换手"] = np.nan; xs["三个月换手"] = np.nan
    # 治理：高管薪酬无全市场批量数据
    xs["高管薪酬"] = np.nan
    AVAIL.setdefault("高管薪酬", ("数据不可用", "无全市场高管薪酬批量数据"))
    return xs

if __name__ == "__main__":
    main()