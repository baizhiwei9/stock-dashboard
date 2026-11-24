# -*- coding: utf-8 -*-
"""T1/T3：全 A 打分池 + 新赛道因子（板块相对强度/概念内相对强弱/动量加速）四层榜单。

方案：
  方案A  s10   = 现行 26 因子名次前 10、名次线性递减加权、direction 调整分位（周报现行口径）
  方案C  new   = 新赛道轨道 = z(板块相对强度 board_rel_con)+z(概念内相对强弱 rs_con)+z(动量加速 accel_5_20)
  方案B  w     = (1-w)×rankpct(s10) + w×rankpct(new)，w ∈ {0.5,0.6,0.7,0.8,1.0}（1.0 即方案C）

四层：行业（stock_basic.industry）/ 概念（同花顺概念板块）/ 主题（THEME_MAP 归并）/ 个股
输出 output/final_fullA_{industry,concept,theme,stock}.csv 与 final_fullA_concept_strength.csv
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
sys.path.insert(0, RAW)

MIN_MV = 300000.0
MIN_INDUSTRY_N, MIN_CONCEPT_N, MIN_THEME_N = 8, 5, 8
WS = [0.5, 0.6, 0.7, 0.8, 1.0]
pd.set_option("display.width", 320)
pd.set_option("display.max_columns", 100)

THEME_MAP = {
    "AI算力与芯片": ["存储芯片", "先进封装", "光刻机", "光刻胶", "共封装光学(CPO)", "铜缆高速连接", "国家大基金持股",
                     "芯片概念", "第三代半导体", "汽车芯片", "PCB概念", "MLCC概念", "数据中心(AIDC)", "算力租赁",
                     "东数西算(算力)", "液冷服务器", "英伟达概念", "中国AI 50", "玻璃基板"],
    "AI软件与应用": ["AIGC概念", "AI应用", "AI智能体", "AI视频", "智谱AI", "人工智能", "云计算", "数据要素",
                     "数据确权", "网络安全", "国产操作系统", "华为欧拉", "量子科技", "脑机接口", "MLOps概念",
                     "6G概念", "F5G概念", "多模态AI", "虚拟数字人"],
    "传媒游戏": ["文化传媒概念", "短剧游戏", "手机游戏", "小红书概念", "快手概念", "广告营销", "网络游戏",
                 "IP经济(谷子经济)", "云游戏", "元宇宙"],
    "机器人与智能制造": ["人形机器人", "机器人概念", "减速器", "机器视觉", "工业母机"],
    "汽车与出行": ["特斯拉概念", "一体化压铸", "新能源汽车", "充电桩", "汽车零部件", "无人驾驶", "飞行汽车(eVTOL)", "电机"],
    "电池储能": ["锂电池概念", "固态电池", "钠离子电池", "PET铜箔"],
    "电力与新能源": ["光伏概念", "风电", "特高压", "绿色电力", "可控核聚变", "核电"],
    "通信电子": ["光纤概念", "华为概念", "智能音箱"],
    "资源与周期": ["金属铜", "小金属概念", "稀土永磁", "磷化工", "化肥", "草甘膦", "煤化工概念", "煤炭概念",
                   "天然气", "氟化工概念", "染料", "培育钻石", "碳纤维"],
    "贵金属": ["黄金概念"],
    "医药生物": ["创新药", "CRO概念", "减肥药", "生物疫苗", "细胞免疫治疗", "猴痘概念", "青蒿素", "重组蛋白",
                 "DRG/DIP", "合成生物"],
    "军工与航天": ["军工", "军民融合", "中船系", "兵装重组概念", "商业航天", "低空经济", "国产航母", "航运概念"],
    "农业与食品": ["农业种植", "转基因", "粮食概念", "玉米", "猪肉", "养鸡", "乳业", "代糖概念", "白酒概念", "啤酒概念"],
    "消费与服务": ["免税店", "跨境电商", "冰雪产业", "共享单车"],
    "金融与红利": ["互联网金融", "数字货币", "跨境支付(CIPS)", "高股息精选"],
    "区域改革与风格": ["国企改革", "福建自贸区", "雄安新区", "供销社", "赛马概念", "海峡两岸", "专精特新",
                       "新股与次新股", "注册制次新股", "科创次新股", "ST板块"],
}

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
close = daily.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index(axis=1)
pct = daily.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last").sort_index(axis=1) / 100.0
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
print(f"[daily] dates={len(dates)} {dates[0]}..{dates[-1]}", flush=True)

xsA = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str})
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
name2code = {v: k for k, v in con_name.items()}
con_of_stock = {}
for bc, codes in con_members.items():
    for c in codes:
        con_of_stock.setdefault(c, []).append(bc)

fr = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
piv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
DIR = fr.drop_duplicates("factor").set_index("factor")["direction"].to_dict()
order = piv[["factor", "rank_this_week"]].dropna().sort_values("rank_this_week", kind="mergesort")["factor"].tolist()

tmp = pd.DataFrame({"d": dates})
tmp["w"] = pd.to_datetime(tmp["d"]).dt.to_period("W-FRI")
we_last = tmp.groupby("w")["d"].max().max()
upto = dates.index(we_last)
print(f"[latest week] {we_last} (upto idx {upto})", flush=True)

STmask = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in close.columns})


def cum(n, i):
    seg = pct.iloc[max(0, i - n + 1): i + 1]
    return (1 + seg).prod(skipna=True) - 1


r5, r20, r60 = cum(5, upto), cum(20, upto), cum(60, upto)
obs = pct.iloc[:upto + 1].notna().sum()
tradable = (obs >= 120) & close.iloc[upto].notna() & (amt.iloc[upto] > 0)
tradable &= ~STmask.reindex(close.columns).fillna(False)
pool = tradable[tradable].index.tolist()
poolset = set(pool)
print(f"[pool] 全 A 可交易池 {len(pool)} 只", flush=True)

newf = pd.DataFrame({"ret5": r5, "ret20": r20, "ret60": r60, "accel_5_20": r5 - r20}).reindex(pool)
newf["行业"] = pd.Series({t: IND.get(t) for t in newf.index})
newf["board_str20_ind"] = newf.groupby("行业")["ret20"].transform("mean")
newf["rs_ind"] = newf["ret20"] - newf["board_str20_ind"]
bstr, bmem = {}, {}
for bc, codes in con_members.items():
    cc = [c for c in codes if c in poolset]
    if len(cc) < 5:
        continue
    bstr[bc] = float(newf.loc[cc, "ret20"].mean())
    bmem[bc] = cc
newf["board_str20_con"] = [np.mean([bstr[b] for b in con_of_stock.get(t, []) if b in bstr])
                           if any(b in bstr for b in con_of_stock.get(t, [])) else np.nan for t in newf.index]
newf["rs_con"] = newf["ret20"] - newf["board_str20_con"]
mkt20 = float(newf["ret20"].mean())
newf["board_rel_con"] = newf["board_str20_con"] - mkt20
print(f"[board] 概念板块 {len(bstr)} 个 | 全 A 等权 近20日 = {mkt20:+.2%}", flush=True)

bs = pd.DataFrame([{"board": b, "name": con_name.get(b, b), "strength": v} for b, v in bstr.items()])
bs["rk"] = bs["strength"].rank(ascending=False, method="min")
bs.to_csv(os.path.join(OUT, "final_fullA_concept_strength.csv"), index=False)
TARGET_BOARDS = ["AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉", "电机", "短剧游戏", "文化传媒概念",
                 "虚拟数字人", "多模态AI", "AIGC概念", "网络游戏", "手机游戏", "特斯拉概念", "工业母机"]
print("\n########## 概念板块 20 日强度（全 A 成分等权，2026-10-29）##########")
for nm in TARGET_BOARDS:
    r = bs[bs["name"] == nm]
    if len(r):
        r = r.iloc[0]
        print(f"  {nm:16s} {r['strength']:+.2%}  名次 {int(r['rk']):>3d}/{len(bs)}  相对全市场 {r['strength']-mkt20:+.2%}")
print("  --- Top20 ---")
print(bs.sort_values("strength", ascending=False, kind="mergesort").head(20)[["name", "strength", "rk"]]
      .to_string(index=False, formatters={"strength": "{:+.2%}".format, "rk": "{:.0f}".format}))

# ---------- 打分池 ----------
d = xsA[xsA["ts_code"].isin(poolset)].copy()
print(f"\n[xs] 全 A 截面 {len(xsA)} ∩ 可交易池 => {len(d)}", flush=True)


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / (s.std() + 1e-12)


facs_s10 = order[:10]
w10 = {f: float(10 - i) for i, f in enumerate(facs_s10)}
tot = float(sum(w10.values()))
for f in order:
    if f not in d.columns:
        continue
    pr = d[f].rank(pct=True, method="average")
    d["adj_" + f] = pr if DIR.get(f, 1) > 0 else (1.0 - pr)
# 允许部分覆盖：按可得因子权重重新归一化，至少 6 / 10 个因子有值
avail = sum(d["adj_" + f].notna().astype(int) for f in facs_s10)
num = sum(w10[f] * d["adj_" + f].fillna(0.0) for f in facs_s10)
den = sum(w10[f] * d["adj_" + f].notna().astype(float) for f in facs_s10)
d["s10"] = np.where(avail >= 6, num / den.replace(0, np.nan), np.nan)
print(f"[方案A] s10 可算(≥6/10 因子) {int(d['s10'].notna().sum())}/{len(d)}"
      f" | 全 10 因子有值 {int((avail == 10).sum())}", flush=True)

d = d.merge(newf[["ret5", "ret20", "ret60", "accel_5_20", "board_str20_con", "board_rel_con",
                  "rs_con", "board_str20_ind", "rs_ind"]], left_on="ts_code", right_index=True, how="left")
d["new_raw"] = (z(d["board_rel_con"]) + z(d["rs_con"]) + z(d["accel_5_20"])) / 3.0
d["s10_pct"] = d["s10"].rank(pct=True, method="average")
d["new_pct"] = d["new_raw"].rank(pct=True, method="average")
for wv in WS:
    d[f"w{int(wv*10)}"] = np.where(d["s10_pct"].notna(),
                                    (1 - wv) * d["s10_pct"].fillna(0.5) + wv * d["new_pct"], np.nan)

COL = ["s10", "new_raw"] + [f"w{int(wv*10)}" for wv in WS]
MAIN = "w7"

mk_ind, mk_con, mk_the = {}, {}, {}
for c, i in IND.items():
    if isinstance(i, str):
        mk_ind.setdefault(i, []).append(c)
for bc, codes in con_members.items():
    mk_con[bc] = codes
for th, bnames in THEME_MAP.items():
    codes = []
    for b in bnames:
        if b in name2code:
            codes += con_members[name2code[b]]
    mk_the[th] = sorted(set(codes))

d["name"] = d["ts_code"].map(NAME)
d["行业"] = d["ts_code"].map(IND)
d["总市值_亿"] = (pd.to_numeric(d["总市值"], errors="coerce") / 10000.0).round(1)


def agg(keys, names, min_n, tag):
    recs = []
    for k, codes in keys.items():
        sub = d[d["ts_code"].isin(codes)]
        if len(sub) < min_n:
            continue
        rec = {"key": k, "name": names.get(k, k), "n": len(sub)}
        for c in COL:
            rec[c] = float(sub[c].mean(skipna=True)) if sub[c].notna().any() else np.nan
            rec["cov_" + c] = int(sub[c].notna().sum())
        rec["r5"] = float(sub["ret5"].mean(skipna=True))
        rec["r20"] = float(sub["ret20"].mean(skipna=True))
        recs.append(rec)
    df = pd.DataFrame(recs)
    for c in COL:
        df["rk_" + c] = df[c].rank(ascending=False, method="min")
    df.to_csv(os.path.join(OUT, f"final_fullA_{tag}.csv"), index=False)
    return df


ind = agg(mk_ind, {k: k for k in mk_ind}, MIN_INDUSTRY_N, "industry")
con = agg(mk_con, con_name, MIN_CONCEPT_N, "concept")
the = agg(mk_the, {k: k for k in mk_the}, MIN_THEME_N, "theme")
print(f"\n[agg] 行业 {len(ind)}  概念 {len(con)}  主题 {len(the)}", flush=True)

# 电机行业规模诊断
print("  [行业名修正] stock_basic 中含『电』的行业名: " +
      ", ".join(f"{k}({len(v)})" for k, v in mk_ind.items() if "电" in k), flush=True)
for nm in ["电机", "通用机械", "专用机械", "汽车零部件", "影视音像", "互联网", "软件服务"]:
    r = ind[ind["name"] == nm]
    n_tot = len(mk_ind.get(nm, []))
    print(f"  [行业诊断] {nm}: stock_basic {n_tot} 只，打分池内 {len(d[d['行业']==nm])} 只，"
          f"入榜 {'是' if len(r) else '否'}", flush=True)

FMT = {c: "{:+.3f}".format for c in COL}
FMT.update({"r5": "{:+.2%}".format, "r20": "{:+.2%}".format, "n": "{:.0f}".format})
for c in COL:
    FMT["rk_" + c] = "{:.0f}".format


def show(df, c, top=12):
    dd = df.dropna(subset=[c]).sort_values(c, ascending=False, kind="mergesort")
    cols = ["name", "n", "r20", "s10", "new_raw", "rk_" + c, c]
    print(f"  --- {c} 名次；combo={c} 与近20日相关 {df[c].corr(df['r20']):+.3f} ---")
    print(dd[cols].head(top).to_string(index=False, formatters={**FMT, c: "{:.3f}".format, "new_raw": "{:+.3f}".format}))


W_MAIN = 0.7
for nm, df in (("概念", con), ("行业", ind), ("主题", the)):
    print(f"\n########## {nm}层 Top12（推荐口径 w={int(W_MAIN*10)}/{int(W_MAIN*10)}）##########")
    show(df, MAIN, 12)

# ---------- 目标名次敏感性 ----------
print("\n########## 目标方向名次 × 新轨道权重敏感性 ##########")
hdr = f"{'名称':16s}{'n':>5s}" + "".join(f"{'s10':>14s}" for _ in [0]) + "".join(f"{('w'+str(int(w*10))):>14s}" for w in WS)
print(hdr)
TARGET_CON = ["AI视频", "人形机器人", "机器人概念", "减速器", "机器视觉", "短剧游戏", "文化传媒概念",
              "虚拟数字人", "多模态AI", "AIGC概念", "网络游戏", "手机游戏", "电机", "特斯拉概念"]
for nm in TARGET_CON:
    r = con[con["name"] == nm]
    if not len(r):
        continue
    r = r.iloc[0]
    row = f"{nm:16s}{int(r['n']):>5d}" + f"(#{int(r['rk_s10']):>3d})".rjust(14)
    for wv in WS:
        c = f"w{int(wv*10)}"
        row += f"({int(r['rk_' + c]):>3d})".rjust(14)
    print(row + f"   近20日 {r['r20']:+.2%}")
print(f"  概念层总数 {len(con)}；括号为该口径下名次")
TARGET_IND = ["电机", "通用机械", "专用机械", "汽车零部件", "影视音像", "互联网", "软件服务", "元器件", "半导体"]
print("\n  —— 行业层 ——")
for nm in TARGET_IND:
    r = ind[ind["name"] == nm]
    if not len(r):
        continue
    r = r.iloc[0]
    row = f"{nm:16s}{int(r['n']):>5d}" + f"(#{int(r['rk_s10']):>3d})".rjust(14)
    for wv in WS:
        c = f"w{int(wv*10)}"
        row += f"({int(r['rk_' + c]):>3d})".rjust(14)
    print(row + f"   近20日 {r['r20']:+.2%}")
print(f"  行业层总数 {len(ind)}")
TARGET_THE = ["机器人与智能制造", "AI软件与应用", "传媒游戏", "汽车与出行", "通信电子"]
print("\n  —— 主题层 ——")
for nm in TARGET_THE:
    r = the[the["name"] == nm]
    if not len(r):
        continue
    r = r.iloc[0]
    row = f"{nm:16s}{int(r['n']):>5d}" + f"(#{int(r['rk_s10']):>3d})".rjust(14)
    for wv in WS:
        c = f"w{int(wv*10)}"
        row += f"({int(r['rk_' + c]):>3d})".rjust(14)
    print(row + f"   近20日 {r['r20']:+.2%}")
print(f"  主题层总数 {len(the)}")

# ---------- 个股 ----------
stk = d[pd.to_numeric(d["总市值"], errors="coerce") >= MIN_MV].dropna(subset=[MAIN]).copy()
stk = stk.sort_values(MAIN, ascending=False, kind="mergesort").reset_index(drop=True)
stk.insert(0, "rank", np.arange(1, len(stk) + 1))
stk[["rank", "ts_code", "name", "行业", "总市值_亿"] + COL + ["ret5", "ret20"]].to_csv(
    os.path.join(OUT, "final_fullA_stock.csv"), index=False)
print(f"\n[个股] 池内≥30亿 {len(stk)} → output/final_fullA_stock.csv")
print(f"--- 个股 Top20（推荐 w=7）---")
print(stk.head(20)[["rank", "name", "行业", "总市值_亿", MAIN, "s10", "new_raw", "ret5", "ret20"]]
      .to_string(index=False, formatters={**FMT, MAIN: "{:.4f}".format, "s10": "{:.4f}".format, "new_raw": "{:+.3f}".format}))
print("--- 个股 Top10（现行口径 s10，对照）---")
sa = d.dropna(subset=["s10"])
sa = sa[pd.to_numeric(sa["总市值"], errors="coerce") >= MIN_MV].sort_values("s10", ascending=False, kind="mergesort")
print(sa.head(10)[["name", "行业", "s10", "ret5", "ret20"]]
      .to_string(index=False, formatters={**FMT, "s10": "{:.4f}".format}))

# ---------- 目标赛道个股 ----------
motor_codes = set()
for b in ["电机", "人形机器人", "机器人概念", "减速器", "机器视觉"]:
    if b in name2code:
        motor_codes |= set(con_members[name2code[b]])
ai_codes = set(con_members.get(name2code.get("AI视频", ""), []))

def blk(tag, codes, topn=15):
    m = d[d["ts_code"].isin(codes) & (pd.to_numeric(d["总市值"], errors="coerce") >= MIN_MV)]
    print(f"\n########## {tag}：全A成分 {len(codes)}，打分池内≥30亿 {len(m)}（s10 有值 {int(m['s10'].notna().sum())}）##########")
    for c in (MAIN, "s10", "new_raw"):
        dd = m.dropna(subset=[c]).sort_values(c, ascending=False, kind="mergesort").head(topn)
        print(f"  --- 按 {c} Top{topn} ---")
        print(dd[["name", "行业", "总市值_亿", c, "ret5", "ret20"]]
              .to_string(index=False, formatters={**FMT, c: "{:.4f}".format, "new_raw": "{:+.3f}".format}))
    return m


mm = blk("机器人（电机链：电机∪人形机器人∪机器人概念∪减速器∪机器视觉）", motor_codes)
aa = blk("AI视频", ai_codes)

# 命中率
for tag, m in (("电机链", mm), ("AI视频", aa)):
    for c in (MAIN, "s10"):
        v = m.dropna(subset=[c])
        n20 = int((stk[c].rank(ascending=False, method="min").reindex(v["ts_code"]).values <= 20).sum())
        n50 = int((stk[c].rank(ascending=False, method="min").reindex(v["ts_code"]).values <= 50).sum())
        print(f"[命中] {tag} 在个股总榜 {c} 中进入前20：{n20} 只 / 前50：{n50} 只（成分 {len(v)} 只）")

# ---------- 目标赛道内选股（T1 收口）----------
SEC_DEF = {
    "机器人-电机链": ["电机", "人形机器人", "机器人概念", "减速器", "机器视觉"],
    "AI视频": ["AI视频"],
    "短剧游戏": ["短剧游戏"],
    "文化传媒概念": ["文化传媒概念"],
}
SEC_COLS = ["赛道", "ts_code", "name", "行业", "总市值_亿", "所属板块", "ret5", "ret20",
            "accel_5_20", "rs_con", "board_rel_con", "new_raw", "w7", "s10",
            "rk_new_in", "rk_ret20_in", "rk_w7_in"]
rows = []
for sec, boards in SEC_DEF.items():
    codes = set()
    for b in boards:
        bc = name2code.get(b)
        if bc:
            codes |= set(con_members.get(bc, []))
    m = d[d["ts_code"].isin(codes) & d["ts_code"].isin(poolset)].copy()
    m = m[pd.to_numeric(m["总市值"], errors="coerce") > 0]
    m["赛道"] = sec
    m["所属板块"] = ["/".join([con_name.get(b, b) for b in con_of_stock.get(t, [])
                              if con_name.get(b) in boards]) for t in m["ts_code"]]
    for c, tag in (("new_raw", "new"), ("ret20", "ret20"), ("w7", "w7")):
        m["rk_" + tag + "_in"] = m[c].rank(ascending=False, method="min")
    rows.append(m)
allsec = pd.concat(rows, ignore_index=True)
allsec[SEC_COLS].sort_values(["赛道", "rk_new_in"], kind="mergesort").to_csv(
    os.path.join(OUT, "target_sector_stocks.csv"), index=False)
print(f"\n[赛道内选股] {len(allsec)} 行 → output/target_sector_stocks.csv", flush=True)

SECF = {"accel_5_20": "{:+.3f}".format, "rs_con": "{:.3f}".format, "board_rel_con": "{:.3f}".format,
        "ret5": "{:+.2%}".format, "ret20": "{:+.2%}".format, "new_raw": "{:+.3f}".format,
        "w7": "{:.3f}".format, "s10": "{:.3f}".format, "总市值_亿": "{:.0f}".format,
        "rk_ret20_in": "{:.0f}".format}
for sec, boards in SEC_DEF.items():
    m = allsec[allsec["赛道"] == sec]
    print(f"\n########## {sec}（{'∪'.join(boards)}）可交易成分 {len(m)} 只 ##########")
    print("  --- 按新轨道 new_raw 排序 Top15 ---")
    print(m.sort_values("new_raw", ascending=False, kind="mergesort").head(15)
          [["name", "行业", "总市值_亿", "所属板块", "new_raw", "w7", "ret5", "ret20", "rk_ret20_in"]]
          .to_string(index=False, formatters=SECF))
    t_new = set(m.sort_values("new_raw", ascending=False, kind="mergesort").head(10)["ts_code"])
    t_act = set(m.sort_values("ret20", ascending=False, kind="mergesort").head(10)["ts_code"])
    print(f"  [重合] 赛道内 new_raw Top10 ∩ 实际近20日涨幅 Top10 = {len(t_new & t_act)}/10"
          f"；赛道内近20日等权 {m['ret20'].mean():+.2%}，全A等权 {mkt20:+.2%}")

# ---------- 概念板块内龙头个股（用户点名方向）----------
NAMED = ["电机", "AI视频", "人形机器人", "减速器", "机器视觉", "短剧游戏", "虚拟数字人",
         "多模态AI", "文化传媒概念", "网络游戏"]
rows2 = []
for nm in NAMED:
    bc = name2code.get(nm)
    if not bc:
        continue
    cc = [c for c in con_members.get(bc, []) if c in poolset]
    m = d[d["ts_code"].isin(cc)].copy()
    m = m[pd.to_numeric(m["总市值"], errors="coerce") > 0]
    m["板块"] = nm
    m["rk_new_in"] = m["new_raw"].rank(ascending=False, method="min")
    m["rk_ret20_in"] = m["ret20"].rank(ascending=False, method="min")
    rows2.append(m)
named = pd.concat(rows2, ignore_index=True)
named[["板块", "ts_code", "name", "行业", "总市值_亿", "ret5", "ret20", "accel_5_20", "rs_con",
       "new_raw", "w7", "s10", "rk_new_in", "rk_ret20_in"]].to_csv(
    os.path.join(OUT, "named_board_stocks.csv"), index=False)
print(f"\n[点名概念龙头个股] {len(named)} 行 → output/named_board_stocks.csv", flush=True)
for nm in NAMED:
    m = named[named["板块"] == nm]
    if not len(m):
        print(f"\n########## {nm}：可交易成分 0 ##########")
        continue
    print(f"\n########## {nm}（可交易成分 {len(m)}，板块20日强度 {m['ret20'].mean():+.2%}）new_raw Top10 ##########")
    print(m.sort_values("new_raw", ascending=False, kind="mergesort").head(10)
          [["name", "行业", "总市值_亿", "ret5", "ret20", "accel_5_20", "rs_con", "new_raw", "rk_ret20_in"]]
          .to_string(index=False, formatters=SECF))
print("[done]")