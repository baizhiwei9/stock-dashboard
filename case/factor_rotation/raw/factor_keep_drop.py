# -*- coding: utf-8 -*-
"""候选新因子取舍收口：合并短期动量族(32周)与既有候选族评估，逐项给出取舍结论。

输出：output/factor_keep_drop.csv
"""
import os
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")

a = pd.read_csv(os.path.join(OUT, "stmom_eval.csv"))
a["src"] = "stmom32"
b = pd.read_csv(os.path.join(OUT, "cand_factor_eval.csv"))
b["src"] = "cand32"
keep = ["factor", "weeks", "ls_mean", "ls_win", "ls_t", "ic_mean", "corr_with_ret20", "src"]
c = pd.concat([a[keep], b[keep]], ignore_index=True)
c = c.drop_duplicates("factor", keep="first")

FAMILY = {
    "ret3": "绝对动量", "ret5": "绝对动量", "ret10": "绝对动量", "ret20": "绝对动量(现行反转原始量)",
    "ret60": "绝对动量(现行反转原始量)", "ret120": "绝对动量",
    "accel_5_20": "动量加速", "accel_10_20": "动量加速", "accel_20_60": "动量加速", "accel_5_60": "动量加速",
    "rs5_con": "概念内相对", "rs10_con": "概念内相对", "rs_con": "概念内相对",
    "rs_ind": "行业内相对",
    "brel3_con": "板块相对动量", "brel5_con": "板块相对动量", "brel10_con": "板块相对动量",
    "brel20_con": "板块相对动量", "brel60_con": "板块相对动量",
    "board_rel_con": "板块相对动量", "board_rel_ind": "板块相对动量",
    "board_str20_con": "板块绝对动量", "board_str20_ind": "板块绝对动量",
    "dist_high20": "趋势位置", "dist_high60": "趋势位置",
    "vol_ratio": "量能", "mf_net5": "资金流", "mf_net20": "资金流", "board_mf5_con": "资金流",
    "trend_c1": "复合", "trend_c2": "复合", "trend_c3": "复合", "c_st1": "复合", "c_st3": "复合",
}
VERDICT = {
    "brel10_con": ("纳入", "板块相对10日动量：族内前瞻最强(+0.95%/周,t=1.14,胜率62.5%)，且能把电机#1/减速器#2/人形机器人#3选进前3"),
    "brel5_con": ("纳入", "板块相对5日动量：+0.70%/周(t=1.14,胜率59.4%)，与10日高度同向，作为短窗备份"),
    "accel_5_20": ("纳入", "动量加速：+0.62%/周(t=0.79)，与ret20相关仅-0.74，提供独立信息；但对'已涨很多'龙头给低分"),
    "c_st1": ("观察", "+0.44%/周(t=0.81)，为ret5与accel_5_20的合成，信息与二者重叠"),
    "accel_10_20": ("观察", "+0.49%/周(t=0.67)，同上，重叠"),
    "brel60_con": ("观察", "+0.37%/周(t=0.36)，中期窗口，方向对但偏弱"),
    "trend_c1": ("观察", "+0.21%/周(t=0.39)，含rs_con(负向)，被拖累"),
    "dist_high60": ("观察", "+0.17%/周(t=0.23)，趋势位置类，与ret20相关0.60"),
    "c_st3": ("观察", "+0.20%/周(t=0.29)，板块相对+概念内相对+5日动量合成"),
    "brel3_con": ("观察", "+0.11%/周(t=0.17)，3日窗口过短"),
    "ret5": ("观察", "+0.09%/周(t=0.15)，绝对短期动量α≈0；但板块层可识别热门方向(ret5: 网络游戏#1/手机游戏#2/AI视频#8)"),
    "ret10": ("观察", "+0.08%/周(t=0.11)，同上；板块层识别力最强(电机#1/减速器#2/人形机器人#3)"),
    "ret3": ("不纳入", "-0.02%/周(t=-0.04)，3日窗口噪声主导"),
    "dist_high20": ("不纳入", "-0.03%/周(t=-0.05)，过短"),
    "rs5_con": ("不纳入", "-0.03%/周(t=-0.05)，概念内相对5日，α≈0"),
    "ret60": ("不纳入", "-0.06%/周(t=-0.07)，与现行三个月反转同源镜像"),
    "accel_5_60": ("不纳入", "-0.12%/周(t=-0.14)"),
    "rs10_con": ("不纳入", "-0.09%/周(t=-0.14)"),
    "brel20_con": ("不纳入", "-0.13%/周(t=-0.15)，与现行20日反转同源，测出负向"),
    "board_rel_con": ("不纳入", "同brel20_con，20日口径测出-0.13%/周"),
    "board_rel_ind": ("不纳入", "-0.43%/周(t=-0.64)"),
    "board_str20_con": ("不纳入", "-0.13%/周(t=-0.15)"),
    "board_str20_ind": ("不纳入", "-0.43%/周(t=-0.64)"),
    "ret120": ("不纳入", "-0.38%/周(t=-0.41)"),
    "accel_20_60": ("不纳入", "-0.54%/周(t=-0.59)"),
    "ret20": ("不纳入", "-0.52%/周(t=-0.64)，正向20日动量前瞻为负，即现行'一个月反转'方向成立"),
    "rs_con": ("不纳入", "-0.51%/周(t=-0.74)，概念内相对20日"),
    "rs_ind": ("不纳入", "-0.36%/周(t=-0.62)"),
    "vol_ratio": ("不纳入", "-0.63%/周(t=-1.13)，量能放大反而拖累"),
    "trend_c2": ("不纳入", "-0.03%/周(t=-0.04)，赛道轨道（含20日口径），α≈0"),
    "trend_c3": ("不纳入", "在trend_c2上叠加资金流，样本不足且无α"),
    "mf_net5": ("不纳入(样本不足)", "moneyflow仅25个交易日，做不成20日窗口前瞻；需补取≈130个交易日再验"),
    "mf_net20": ("不纳入(样本不足)", "同mf_net5"),
    "board_mf5_con": ("不纳入(样本不足)", "板块资金流，样本仅25日"),
}
rows = []
for _, r in c.iterrows():
    f = r["factor"]
    v, note = VERDICT.get(f, ("不适用", ""))
    rows.append({"factor": f, "family": FAMILY.get(f, "-"), "weeks": int(r["weeks"]),
                 "ls_mean": r["ls_mean"], "ls_win": r["ls_win"], "ls_t": r["ls_t"],
                 "corr_with_ret20": r["corr_with_ret20"], "verdict": v, "note": note, "src": r["src"]})
k = pd.DataFrame(rows).sort_values(["verdict", "ls_t"], ascending=[True, False], kind="mergesort")
k.to_csv(os.path.join(OUT, "factor_keep_drop.csv"), index=False)
pd.set_option("display.width", 220)
print(k.to_string(index=False, formatters={"ls_mean": "{:+.2%}".format, "ls_win": "{:.1%}".format,
                                           "ls_t": "{:+.2f}".format, "corr_with_ret20": "{:+.2f}".format}))
print("\n[汇总] 纳入 %d 项 / 观察 %d 项 / 不纳入 %d 项" % (
    (k["verdict"] == "纳入").sum(), (k["verdict"] == "观察").sum(),
    k["verdict"].str.startswith("不纳入").sum()))
print("[done]")