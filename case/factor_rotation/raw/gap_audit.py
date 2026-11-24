# -*- coding: utf-8 -*-
"""T0 漏选根因与因子缺口审计。

三件事：
  ①现有 26 因子清单的分类与"缺失因子类型"判定；
  ②机器人(电机链)/AI视频 两方向成分股在当前打分池(4指数并集)与全 A 的覆盖差、26 因子全值率差；
  ③两方向龙头/代表股：在池/不在池、综合分名次、近20日收益。
输出：output/gap_factor_audit.csv, output/gap_coverage.csv, output/gap_leaders.csv
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

# ---------- 1. 因子清单与语义分类 ----------
piv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
fr = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
DIR = fr.drop_duplicates("factor").set_index("factor")["direction"].to_dict()
GRP = fr.drop_duplicates("factor").set_index("factor")["group"].to_dict()
order = piv[["factor", "rank_this_week"]].dropna().sort_values("rank_this_week", kind="mergesort")["factor"].tolist()

# 语义标签：数据层级（个股/组合）+ 类型
SEM = {
    "估值": ("个股", "基本面·估值"),
    "反转动量": ("个股", "技术·时序动量"),
    "成长": ("个股", "基本面·成长"),
    "盈利": ("个股", "基本面·盈利"),
    "流动": ("个股", "技术·流动性"),
    "波动": ("个股", "技术·波动"),
    "治理": ("个股", "基本面·治理"),
    "预期": ("个股", "预期·一致预期"),
    "机构": ("个股", "持仓·机构"),
}
rows = []
for f in order:
    g = GRP.get(f, "?")
    lvl, typ = SEM.get(g, ("?", "?"))
    rows.append({"factor": f, "rank_this_week": int(piv.set_index("factor").loc[f, "rank_this_week"]),
                 "group": g, "direction": DIR.get(f), "数据层级": lvl, "类型": typ})
aud = pd.DataFrame(rows).sort_values("rank_this_week", kind="mergesort")

# 关键能力标记：是否能表达"板块/主题强度"类信息
CAP_KEYS = {
    "板块/主题强度": "板块（行业/概念）自身的涨幅或相对强度",
    "板块内相对强弱RS": "个股在所属板块内部的排名强度",
    "资金流/量能": "成交额放量、净流入、大单、量比等增量资金指标",
    "动量加速": "动量的二阶变化（近端相对远端的加速度）",
    "事件/情绪": "新闻热度、龙虎榜、融资余额、涨停等事件型指标",
}
HAS = {k: [] for k in CAP_KEYS}
for _, r in aud.iterrows():
    f, g = r["factor"], r["group"]
    if f == "非流动性冲击":
        HAS["资金流/量能"].append(f + "（Amihud 非流动性，仅量价存量，非净流入）")
    if f == "一年动量":
        HAS["动量加速"].append(f + "（仅 250 日水平，非二阶）")
aud.to_csv(os.path.join(OUT, "gap_factor_audit.csv"), index=False)
print("=== 26 因子分类 ===")
print(aud.to_string(index=False))
print("\n=== 能力覆盖判定（目标：能表达板块/主题/赛道驱动的因子） ===")
for k, v in HAS.items():
    print(f"  {k:16s}：{'缺失' if not v else '；'.join(v)}")

# ---------- 2. 目标方向成分股 ----------
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
board2codes = {bn: g["con_code"].tolist() for bn, g in tm.groupby("board_name")}

DIRECTIONS = {
    "AI视频": ["AI视频"],
    "机器人(电机链)": ["人形机器人", "机器人概念", "减速器", "机器视觉", "电机"],
}

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), usecols=["ts_code", "trade_date", "close", "amount"],
                    dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"), usecols=["ts_code", "trade_date", "close", "amount"],
                  dtype={"ts_code": str, "trade_date": str})
fullA = set(pd.concat([daily, d29])["ts_code"].unique())
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
universe = set(xs["ts_code"])
FACTORS = order

# 26 因子全值率
xs["_nfull"] = xs[FACTORS].notna().sum(axis=1)
fullval = set(xs.loc[xs["_nfull"] == len(FACTORS), "ts_code"])

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()

cov = []
for dname, boards in DIRECTIONS.items():
    codes = sorted({c for b in boards for c in board2codes.get(b, [])})
    in_univ = [c for c in codes if c in universe]
    in_fullA = [c for c in codes if c in fullA]
    fullv = [c for c in codes if c in fullval]
    cov.append({"方向": dname, "板块": "/".join(boards), "成分股数": len(codes),
                "在4指数池": len(in_univ), "在池占比": len(in_univ) / max(len(codes), 1),
                "全A可得行情": len(in_fullA), "全A占比": len(in_fullA) / max(len(codes), 1),
                "在池且26因子全值": len(fullv),
                "全值占在池比": len(fullv) / max(len(in_univ), 1), "缺失成分": len(codes) - len(in_fullA)})
covdf = pd.DataFrame(cov)
covdf.to_csv(os.path.join(OUT, "gap_coverage.csv"), index=False)
print("\n=== 覆盖差（当前打分池 vs 全A） ===")
print(covdf.to_string(index=False))

# ---------- 3. 龙头/代表股名次 ----------
csr = pd.read_csv(os.path.join(OUT, "combo_stock_rank.csv"), dtype={"ts_code": str})
CS = csr.set_index("ts_code")
# 全池综合分名次（池内 rank 已给）
pt = pd.read_csv(os.path.join(ROOT, "raw", "daily_260d.csv"), nrows=1)  # noop
# 近20日收益（合并 260d + 1029）
px = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
px["close"] = pd.to_numeric(px["close"], errors="coerce")
px = px.sort_values(["ts_code", "trade_date"], kind="mergesort")
pvt = px.pivot_table(index="ts_code", columns="trade_date", values="close", aggfunc="last").sort_index(axis=1)
ret20 = (pvt.iloc[:, -1] / pvt.iloc[:, -21] - 1.0)
ret5 = (pvt.iloc[:, -1] / pvt.iloc[:, -6] - 1.0)

LEADERS = {
    "AI视频": ["600715.SH", "605287.SH", "300494.SZ", "300133.SZ", "300413.SZ", "603533.SH", "002517.SZ"],
    "机器人(电机链)": ["002196.SZ", "300718.SZ", "003021.SZ", "002050.SZ", "603728.SH", "300124.SZ",
                    "002472.SZ", "300304.SZ", "300224.SZ"],
}
lead = []
for dname, codes in LEADERS.items():
    for c in codes:
        inpool = c in universe
        insc = c in set(csr["ts_code"])
        row = {"方向": dname, "ts_code": c, "name": NAME.get(c, "?"), "行业": IND.get(c, "?"),
               "在4指数池": inpool, "20日收益": ret20.get(c, np.nan), "5日收益": ret5.get(c, np.nan),
               "综合分s10": None, "池内名次": None, "池内名次(池外按全池名次)": None}
        if insc:
            r = CS.loc[c]
            row["综合分s10"] = float(r["s10"])
            row["池内名次"] = int(r["rank"])
        lead.append(row)
ldf = pd.DataFrame(lead)
ldf.to_csv(os.path.join(OUT, "gap_leaders.csv"), index=False)
print("\n=== 龙头/代表股 ===")
print(ldf.to_string(index=False, formatters={"20日收益": "{:+.2%}".format, "5日收益": "{:+.2%}".format,
                                             "综合分s10": "{:.4f}".format}))
print("\n[基准] 池内个股等权 近20日 = {:+.2%} (n={})".format(ret20.reindex(list(universe)).mean(), len(universe)))
print("[done]")