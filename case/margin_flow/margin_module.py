# -*- coding: utf-8 -*-
"""两融模块（风险双轨 + 机会双轨 + 行业背离 + 个股扫描 + ETF资金流）-> margin_module.json
口径严格遵循用户给定规范，固定阈值原样保留，分位窗口统一 120 个交易日。
"""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D, load_days, pct_rank, run_len

days = [int(d) for d in load_days()]
LAST = days[-1]
W = 120  # 分位窗口

# ---------------- 载入 ----------------
ms = pd.read_csv(os.path.join(D, "margin_summary.csv")); ms["trade_date"] = ms.trade_date.astype(int)
m2 = (ms[ms.exchange_id.isin(["SSE", "SZSE"])]
      .groupby("trade_date")[["rzye", "rqye", "rzmre", "rzche", "rzrqye"]].sum().sort_index())
db = pd.read_csv(os.path.join(D, "daily_basic.csv"), usecols=["ts_code", "trade_date", "close", "circ_mv"])
db["trade_date"] = db.trade_date.astype(int)
db["mk"] = db.ts_code.str.split(".").str[1]
cm = db[db.mk.isin(["SH", "SZ"])].groupby("trade_date").circ_mv.sum()          # 万元
idx = pd.read_csv(os.path.join(D, "index_amount.csv")); idx["trade_date"] = idx.trade_date.astype(int)
amt = idx[idx.ts_code.isin(["000001.SH", "399106.SZ"])].groupby("trade_date").amount.sum()  # 千元
amain = pd.read_csv(os.path.join(D, "index_main.csv")); amain["trade_date"] = amain.trade_date.astype(int)
def idx_close(code):
    return amain[amain.ts_code == code].sort_values("trade_date").set_index("trade_date").close
s300, s500 = idx_close("000300.SH"), idx_close("000905.SH")

lev = (m2.rzye / (cm * 1e4)) * 100          # 杠杆率 %
turn = (m2.rzmre / (amt * 1000)) * 100      # 融资买入额 / 两市成交额 %
netbuy = m2.rzmre - m2.rzche                # 净买入（负=净偿还）
tot = pd.DataFrame({"rzye": m2.rzye, "rqye": m2.rqye, "rzmre": m2.rzmre, "rzche": m2.rzche,
                    "rzrqye": m2.rzrqye, "circ_mv_yi": cm * 1e4 / 1e8, "lev_pct": lev,
                    "turn_pct": turn, "netbuy": netbuy})
tot["dod_pct"] = m2.rzye.pct_change() * 100
for k in (5, 10, 20):
    tot[f"chg{k}_pct"] = m2.rzye.pct_change(k) * 100
tot["netbuy20"] = netbuy.rolling(W_ := 20).sum()
L = tot.loc[LAST]
net_repay = -netbuy   # 净偿还额（正=偿还）

# ---------------- 个股 / 行业 面板 ----------------
md = pd.read_csv(os.path.join(D, "margin_detail.csv")); md["trade_date"] = md.trade_date.astype(int)
sb = pd.read_csv(os.path.join(D, "stock_basic.csv")); nmap = dict(zip(sb.ts_code, sb.name))
im = pd.read_csv(os.path.join(D, "industry_map.csv")); imap = dict(zip(im.ts_code, im.l1_name))
rz = md.pivot_table(index="trade_date", columns="ts_code", values="rzye").sort_index()
rce = md.pivot_table(index="trade_date", columns="ts_code", values="rzche").sort_index()
rmr = md.pivot_table(index="trade_date", columns="ts_code", values="rzmre").sort_index()
bcm = db.pivot_table(index="trade_date", columns="ts_code", values="circ_mv").sort_index()
bcl = db.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
slev = rz / (bcm * 1e4) * 100               # 个股杠杆率 %

def last_pct(series_df):
    """按列取最后一日的 120 日分位，返回 Series"""
    df = series_df.iloc[-W:]
    last = df.iloc[-1]
    return pd.Series({c: (np.nan if pd.isna(v) or df[c].dropna().shape[0] < 2
                          else float((df[c].dropna() <= v).mean() * 100))
                      for c, v in last.items()})

# 风险轨一-3：高杠杆（>8%）群体偿还 5 日均值
grp = slev > 8
grp_rep = rce.where(grp).mean(axis=1)
grp_rep5 = grp_rep.rolling(5).mean()
p_grp_rep = pct_rank(grp_rep5)
grp_rep5_slope = (grp_rep5.iloc[-1] / grp_rep5.iloc[-6] - 1) * 100 if grp_rep5.iloc[-6] else np.nan

# 机会轨二：出清深度分位 / 偿还收敛分位
rz60max = rz.rolling(60).max()
dd = (1 - rz / rz60max)                     # 融资余额距 60 日高点回落幅度
p_dd = last_pct(dd)
rz3 = rce.rolling(3).mean()
rz20p = rce.rolling(20).mean().shift(5)
conv_ratio = rz3 / rz20p                    # 近3日偿还 / 前20日日均偿还，<20%分位=充分收敛
p_conv = last_pct(conv_ratio)
rz60max_cl = bcl.rolling(60).max()
drawdown = (1 - bcl / rz60max_cl)          # 股价距 60 日高点回撤
rollmin20 = bcl.rolling(20).min()
newlow = bcl.iloc[-1] <= rollmin20.iloc[-1]

# ---------------- 风险轨一 ----------------
dod = tot.dod_pct
run_dod = run_len(dod < -0.3)
accel_out = bool(run_dod >= 2 and dod.iloc[-1] < dod.iloc[-2])
roll_neg = bool((L.chg5_pct < 0) and (L.chg10_pct < 0) and (L.chg20_pct < 0))
# 连续<-0.3% 近期最长游程（诊断用）
mask = (dod < -0.3).values
best = cur = 0; last_run_end = None
for i, m in enumerate(mask):
    cur = cur + 1 if m else 0
    if cur > best: best = cur
# 成交占比：9% -> 6% 撤退阈值
turn_drop_from_peak = turn.max() - L.turn_pct
turn_below6 = bool(L.turn_pct < 6.0)
# 227% 等效信号：加速流出阈值触发 且 融资买入占比骤降
turn_5ago = turn.iloc[-6]
turn_plunge = bool((turn_5ago - L.turn_pct) >= 1.0) or bool(pct_rank(turn) < 20)
sig227 = bool(accel_out and turn_plunge)
risk_triggers = []
if accel_out: risk_triggers.append(f"加速流出：日环比连续{run_dod}天<-0.3%且加速（最新{dod.iloc[-1]:+.2f}%）")
elif roll_neg: risk_triggers.append(f"持续净流出：5日{L.chg5_pct:+.2f}%/10日{L.chg10_pct:+.2f}%/20日{L.chg20_pct:+.2f}%（未达连续<-0.3%加速阈值）")
if abs(L.lev_pct - 2.2) <= 0.15: risk_triggers.append(f"杠杆率{L.lev_pct:.2f}%接近2.2%弱势中枢")
if p_grp_rep >= 80: risk_triggers.append(f"高杠杆(>8%)群体偿还5日均值处于{p_grp_rep:.0f}%分位（测算{grp_rep5.iloc[-1]/1e8:.1f}亿/日）")
if turn_below6: risk_triggers.append(f"融资买入占比{L.turn_pct:.2f}%<6%（杠杆资金撤退）")
elif turn_drop_from_peak >= 3.0: risk_triggers.append(f"融资买入占比自区间高点{turn.max():.2f}%回落至{L.turn_pct:.2f}%（尚未跌破6%）")
if sig227: risk_triggers.append("227%担保比例等效信号：触发")
else: risk_triggers.append("227%担保比例等效信号：未触发")

# ---------------- 风险轨二 ----------------
p_lev = pct_rank(tot.lev_pct); p_net = pct_rank(tot.netbuy20); p_turn = pct_rank(tot.turn_pct)
risk_pct = {"杠杆率分位": round(p_lev, 1), "融资净买入分位": round(p_net, 1),
            "成交占比分位": round(p_turn, 1), "高杠杆群体偿还分位": round(float(p_grp_rep), 1)}
risk_pct_extreme = []
if p_lev >= 80: risk_pct_extreme.append("杠杆率分位≥80%")
if p_net <= 20: risk_pct_extreme.append(f"净买入分位P{p_net:.0f}≤20%（持续净偿还）")
if p_turn <= 20: risk_pct_extreme.append(f"成交占比分位P{p_turn:.0f}≤20%（杠杆参与极低）")
if p_grp_rep >= 80: risk_pct_extreme.append(f"高杠杆群体偿还分位P{p_grp_rep:.0f}≥80%")

# ---------------- 机会轨一 ----------------
# 1 出清接近尾声：杠杆率接近2.2% 且 净偿还额连续3日收窄
rep3 = net_repay.iloc[-3:].values
rep_shrink = bool(rep3[0] > rep3[1] > rep3[2])   # 净偿还递减 = 收窄
lev_near = bool(abs(L.lev_pct - 2.2) <= 0.15)
opp1 = bool(lev_near and rep_shrink)
# 2 超跌反弹候选池（固定阈值）
cand = pd.DataFrame({
    "name": [nmap.get(c, "") for c in bcl.columns],
    "industry": [imap.get(c, "") for c in bcl.columns],
    "retrace": drawdown.iloc[-1].reindex(bcl.columns).values,
    "rz_drop": dd.iloc[-1].reindex(bcl.columns).values,
    "conv_ratio": conv_ratio.iloc[-1].reindex(bcl.columns).values,
    "p_conv": p_conv.reindex(bcl.columns).values,
    "p_dd": p_dd.reindex(bcl.columns).values,
    "close": bcl.iloc[-1].reindex(bcl.columns).values,
}, index=bcl.columns)
opp_cand = cand[(cand.retrace > 0.30) & (cand.rz_drop > 0.20) & (cand.conv_ratio < 1.0)].copy()
opp_cand["score"] = opp_cand.retrace * (1 - opp_cand.conv_ratio)
opp_cand = opp_cand.sort_values("score", ascending=False)
# 3 底部确认信号：指数横盘/微跌但融资余额止降 或 指数新低但融资余额不再新低
idx_flat = bool(abs(s300.pct_change().iloc[-1] * 100) < 1.0 or abs(s500.pct_change().iloc[-1] * 100) < 1.0)
rz_stop_fall = bool(tot.dod_pct.iloc[-1] >= 0)
idx_newlow60 = bool(s300.iloc[-1] <= s300.iloc[-60:].min() or s500.iloc[-1] <= s500.iloc[-60:].min())
rz_newlow60 = bool(tot.rzye.iloc[-1] <= tot.rzye.iloc[-60:].min())
bot_div = bool((idx_flat and rz_stop_fall) or (idx_newlow60 and not rz_newlow60))
# 4 风格切换：融资出清完成（流速企稳）
clear_done = bool(tot.netbuy20.iloc[-1] > 0)
# 机会轨二
p_conv_all = float(np.nanmedian(p_conv.values))
opp_pct = {"出清深度分位(中位)": round(float(np.nanmedian(p_dd.values)), 1),
           "偿还收敛分位(中位)": round(p_conv_all, 1),
           "杠杆率分位": round(p_lev, 1),
           "净买入分位": round(p_net, 1)}
# 底部背离分位：指数价格分位 vs 融资余额分位
def price_pct(s):
    w = s.iloc[-W:]; return float((w <= w.iloc[-1]).mean() * 100)
px_pct = max(price_pct(s300), price_pct(s500))
rzbal_pct = pct_rank(tot.rzye)
bot_div_pct = float(px_pct - rzbal_pct)
opp_pct["底部背离差值(指数价格分位-融资余额分位)"] = round(bot_div_pct, 1)
style_switch_pct = bool(clear_done and p_lev < 40)

opp_triggers = []
opp_triggers.append(("融资出清接近尾声：达标" if opp1 else
                     f"融资出清接近尾声：未达标（杠杆率{L.lev_pct:.2f}%未接近2.2%"
                     + ("" if not lev_near else "") + "；净偿还近3日"
                     + ("收窄" if rep_shrink else "未连续收窄") + "）"))
opp_triggers.append(f"超跌反弹候选池：{len(opp_cand)}只达标（回撤>30%+融资回落>20%+近3日偿还收敛）")
opp_triggers.append(("底部确认信号：触发" if bot_div else
                     "底部确认信号：未触发（指数非横盘微跌且融资余额仍在创新低）"))
opp_triggers.append(("风格切换提示：启动（融资流速企稳）" if clear_done else
                     "风格切换提示：未启动（融资出清未完成）"))
opp_pct_notes = []
if p_conv_all < 20: opp_pct_notes.append(f"偿还收敛分位P{p_conv_all:.0f}<20% → 群体偿还充分收敛")
if p_dd.median() > 80: opp_pct_notes.append(f"出清深度分位P{np.nanmedian(p_dd.values):.0f}，个股融资多已深度出清")
if bot_div_pct > 30: opp_pct_notes.append(f"底部背离差值{bot_div_pct:.0f}%>30% → 背离确认")
if style_switch_pct: opp_pct_notes.append("风格切换分位达标：20日净买入转正且杠杆率分位<40%")

# ---------------- 个股扫描 D ----------------
# 风险池：融资占比>8% 且 5日偿还/前20日日均偿还>1.3 且 股价创新低
accel5 = rce.rolling(5).mean() / rce.rolling(20).mean().shift(5)
p_accel = last_pct(accel5)
riskpool = pd.DataFrame({
    "name": [nmap.get(c, "") for c in bcl.columns],
    "industry": [imap.get(c, "") for c in bcl.columns],
    "lev": slev.iloc[-1].reindex(bcl.columns).values,
    "accel5": accel5.iloc[-1].reindex(bcl.columns).values,
    "accel_pct": p_accel.reindex(bcl.columns).values,
    "newlow": newlow.reindex(bcl.columns).values,
    "close": bcl.iloc[-1].reindex(bcl.columns).values,
    "rzye_yi": (rz.iloc[-1].reindex(bcl.columns) / 1e8).values,
    "rz_drop": dd.iloc[-1].reindex(bcl.columns).values,
}, index=bcl.columns)
risk10 = riskpool[(riskpool.lev > 8) & (riskpool.accel5 > 1.3) & (riskpool.newlow == True)].copy()
risk10["score"] = risk10.lev * risk10.accel5
risk10 = risk10.sort_values("score", ascending=False).head(10)
opp10 = opp_cand.head(10)

# ---------------- ETF 资金流模块 ----------------
EFTMAP = {"512480.SH": "电子/半导体", "588200.SH": "电子/半导体", "515880.SH": "通信/AI",
          "159819.SZ": "通信/AI", "512000.SH": "非银金融", "512800.SH": "非银金融",
          "512170.SH": "医药", "159992.SZ": "医药", "516270.SH": "电力设备", "159796.SZ": "电力设备",
          "159928.SZ": "食品饮料", "515170.SH": "食品饮料", "515080.SH": "红利", "563020.SH": "红利",
          "515220.SH": "资源", "512400.SH": "资源", "510300.SH": "宽基锚", "588000.SH": "宽基锚",
          "159915.SZ": "宽基锚"}
EFTN = {"512480.SH": "半导体ETF", "588200.SH": "科创芯片ETF", "515880.SH": "通信ETF",
        "159819.SZ": "人工智能ETF", "512000.SH": "券商ETF", "512800.SH": "银行ETF",
        "512170.SH": "医疗ETF", "159992.SZ": "创新药ETF", "516270.SH": "新能源ETF",
        "159796.SZ": "电池ETF", "159928.SZ": "消费ETF", "515170.SH": "食品饮料ETF",
        "515080.SH": "红利ETF", "563020.SH": "红利低波ETF", "515220.SH": "煤炭ETF",
        "512400.SH": "有色ETF", "510300.SH": "沪深300ETF", "588000.SH": "科创50ETF",
        "159915.SZ": "创业板ETF"}
esh = pd.read_csv(os.path.join(D, "etf_share.csv")); esh["trade_date"] = esh.trade_date.astype(int)
epx = pd.read_csv(os.path.join(D, "etf_price.csv")); epx["trade_date"] = epx.trade_date.astype(int)
esh = esh[esh.trade_date <= LAST]   # 只取 <=基准日，避免上游超前的份额/价格混入基准日报告
epx = epx[epx.trade_date <= LAST]
shp = esh.pivot_table(index="trade_date", columns="ts_code", values="fd_share").sort_index()
pxp = epx.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
sh_chg20 = shp.pct_change(20) * 100
sh_chg5 = shp.pct_change(5) * 100
p_flow = last_pct(sh_chg20)
flow_rows = []
for c in EFTN:
    if c not in shp.columns: continue
    d_share = (shp[c].iloc[-1] - shp[c].iloc[-21]) * 1e4          # 万份 -> 份
    px = pxp[c].iloc[-1]
    est = d_share * px / 1e8                                      # 亿元
    flow_rows.append({"code": c, "name": EFTN[c], "group": EFTMAP[c],
                      "chg20_pct": round(float(sh_chg20[c].iloc[-1]), 2),
                      "chg5_pct": round(float(sh_chg5[c].iloc[-1]), 2),
                      "est_flow_yi": round(float(est), 2),
                      "flow_pct120": round(float(p_flow.get(c, np.nan)), 1)})
flow = pd.DataFrame(flow_rows).sort_values("est_flow_yi", ascending=False)

# ---------------- 落盘 ----------------
out = {
    "last_date": LAST,
    "market": {"rzye_yi": round(m2.rzye.loc[LAST] / 1e8, 1), "rqye_yi": round(m2.rqye.loc[LAST] / 1e8, 1),
               "rzrqye_yi": round(m2.rzrqye.loc[LAST] / 1e8, 1), "circ_mv_yi": round(cm.loc[LAST] * 1e4 / 1e8, 1),
               "lev_pct": round(float(L.lev_pct), 3), "turn_pct": round(float(L.turn_pct), 2),
               "netbuy_yi": round(float(L.netbuy) / 1e8, 1), "netbuy20_yi": round(float(L.netbuy20) / 1e8, 1),
               "dod_pct": round(float(L.dod_pct), 3), "chg5_pct": round(float(L.chg5_pct), 3),
               "chg10_pct": round(float(L.chg10_pct), 3), "chg20_pct": round(float(L.chg20_pct), 3),
               "rzye_peak_yi": round(m2.rzye.max() / 1e8, 1), "rzye_peak_date": int(m2.rzye.idxmax()),
               "rzye_drop_from_peak_pct": round((m2.rzye.loc[LAST] / m2.rzye.max() - 1) * 100, 2),
               "turn_peak_pct": round(float(turn.max()), 2)},
    "risk_track1": {"accel_outflow": accel_out, "run_dod_below_0_3": int(run_dod),
                    "max_run_dod_below_0_3_recent": int(best), "roll_negative": roll_neg,
                    "lev_pct": round(float(L.lev_pct), 3), "lev_near_2_2": lev_near,
                    "lev_trend_5d": round(float(tot.lev_pct.iloc[-1] - tot.lev_pct.iloc[-6]), 3),
                    "grp_rep5_yi": round(float(grp_rep5.iloc[-1]) / 1e8, 1),
                    "grp_rep5_slope_pct": round(float(grp_rep5_slope), 1),
                    "grp_lev_gt8_count": int((slev.iloc[-1] > 8).sum()),
                    "turn_pct": round(float(L.turn_pct), 2), "turn_below_6": turn_below6,
                    "turn_drop_from_peak": round(float(turn_drop_from_peak), 2),
                    "sig227_triggered": sig227, "triggers": risk_triggers},
    "risk_track2": {"percentiles": risk_pct, "extreme": risk_pct_extreme},
    "opportunity_track1": {"opp1": opp1, "lev_near_2_2": lev_near, "rep_shrink3": rep_shrink,
                           "netrepay3_yi": [round(float(x) / 1e8, 1) for x in rep3],
                           "opp_cand_count": int(len(opp_cand)),
                           "bottom_confirm": bot_div, "idx_flat": idx_flat, "rz_stop_fall": rz_stop_fall,
                           "idx_newlow60": idx_newlow60, "rz_newlow60": rz_newlow60,
                           "clear_done": clear_done, "triggers": opp_triggers},
    "opportunity_track2": {"percentiles": opp_pct, "notes": opp_pct_notes,
                           "style_switch_pct": style_switch_pct},
    "etf_flow": flow.to_dict("records"),
    "risk_top10": risk10.reset_index().rename(columns={"index": "code"}).to_dict("records"),
    "opp_top10": opp10.reset_index().rename(columns={"index": "code"}).to_dict("records"),
}
risk10.to_csv(os.path.join(D, "stock_risk_top10.csv"))
opp10.to_csv(os.path.join(D, "stock_opp_top10.csv"))
flow.to_csv(os.path.join(D, "etf_flow.csv"), index=False)
json.dump(out, open(os.path.join(D, "margin_module.json"), "w"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))