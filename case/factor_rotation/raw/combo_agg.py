# -*- coding: utf-8 -*-
"""多因子综合分（因子名次线性递减加权）× 四层聚合（行业/概念/主题/个股）+ 热门方向诊断。

综合分口径（用户口径，不得回退）：
  order  = raw/factor_weekly_pivot.csv 的 rank_this_week（本周因子排名，第 1 名最强）
  n      = 参与合成的因子个数（取前 n 名）
  w_i    = n+1-i（第 i 名权重），归一化 Σw=1
  adj    = direction>0 取分位、direction<0 取 1-分位（direction 取自 factor_weekly_raw.csv）
  score  = Σ w_i × adj_i   （要求入选因子全部有值）

四层聚合：
  行业 = stock_basic.industry（110 个）| 概念 = theme_member_all ∪ theme_member_ai（135 个同花顺概念）
  主题 = 将 128 个同花顺概念按显式映射归并为 16 个上位主题（映射表见 THEME_MAP，随产物披露）
  个股 = latest_cross_section 有综合分的个股（总市值 ≥ 30 亿）
  组得分 = 组内有值成员综合分均值；组收益 = 全市场成员等权（基准日 20261029）
  命中因子数 = 组内成员（不要求 26 因子全有值）在 26 个因子上的方向调整分位均值 ≥ 0.60 的因子个数

三种口径（列名前缀）：
  s{n}  = 字面口径：direction 调整分位，反转类（direction=-1）低值得高分 → 超跌股靠前（用户原始口径）
  ms{n} = 反转顺势口径（诊断）：仅把 一个月反转/三个月反转 两条翻正，其余同 s → 检验“反转类是否为病根”
  nr{n} = 无反转口径（用户建议的“把它去掉”）：直接从 factor 排序中剔除 一个月反转/三个月反转，
          剩余因子按原名次顺序取前 n 名、权重仍为 n+1-i 重新归一化
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
XSEC_DATE, LAST_DATE = "20261028", "20261029"
MIN_INDUSTRY_N, MIN_CONCEPT_N, MIN_THEME_N, MIN_MV = 10, 5, 8, 300000.0
NS = [5, 10, 26]
REV = ["一个月反转", "三个月反转"]          # 反转类因子（诊断用）
HIT_THR = 0.60
pd.set_option("display.width", 260)

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

# ---------- 1. 数据 ----------
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
fr = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
piv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
DIR = fr.drop_duplicates("factor").set_index("factor")["direction"].to_dict()
order = piv[["factor", "rank_this_week"]].dropna().sort_values("rank_this_week", kind="mergesort")["factor"].tolist()
FACTORS = order[:]
missing = [f for f in FACTORS if f not in xs.columns]
assert not missing, f"latest_cross_section 缺列: {missing}"

# 方向调整分位（全池，用于命中因子与综合分）
for f in FACTORS:
    pr = xs[f].rank(pct=True, method="average")
    xs["adj_" + f] = pr if DIR.get(f, 1) > 0 else (1.0 - pr)

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "close"], dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "close"], dtype={"ts_code": str, "trade_date": str})
px = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
px["close"] = pd.to_numeric(px["close"], errors="coerce")
px = px.sort_values(["ts_code", "trade_date"], kind="mergesort")
dates = np.sort(px["trade_date"].unique())
pv = px[px["trade_date"].isin(dates[-21:])].pivot_table(
    index="ts_code", columns="trade_date", values="close", aggfunc="last").sort_index(axis=1)
ret = pd.DataFrame({"ts_code": pv.index,
                    "r3": pv.iloc[:, -1] / pv.iloc[:, -4] - 1.0,
                    "r20": pv.iloc[:, -1] / pv.iloc[:, -21] - 1.0}).reset_index(drop=True).dropna(subset=["r20"])

# ---------- 2. 综合分（主口径 + 反转顺势口径） ----------
def build(n, mode="s"):
    if mode == "nr":
        facs = [f for f in order if f not in REV][:n]
    else:
        facs = order[:n]
    w = {f: float(n + 1 - i) for i, f in enumerate(facs)}
    tot = float(sum(w.values()))
    d = xs.dropna(subset=facs)
    sc = pd.Series(0.0, index=d.index)
    for f in facs:
        a = d["adj_" + f]
        if mode == "ms" and f in REV:
            a = 1.0 - a
        sc = sc + (w[f] / tot) * a
    return pd.DataFrame({"ts_code": d["ts_code"].values, "score": sc.values}), len(d), facs

WT = []
info = {}
base = xs[["ts_code", "总市值", "close"]].copy()
for n in NS:
    for mode in ("s", "ms", "nr"):
        s, cnt, facs = build(n, mode)
        miss = [f for f in facs if f not in xs.columns]
        assert not miss, f"遗漏因子列 {miss}"
        base = base.merge(s.rename(columns={"score": f"{mode}{n}"}), on="ts_code", how="left")
        info[(n, mode)] = (cnt, facs)
    for mode in ("s", "nr"):
        facs = order[:n] if mode == "s" else [f for f in order if f not in REV][:n]
        for i, f in enumerate(facs, 1):
            WT.append({"n": n, "mode": mode, "rank": i, "factor": f, "direction": DIR.get(f),
                       "weight": float(n + 1 - i) / sum(range(1, n + 2))})
    print(f"[n={n:>2}] 主口径全有值 {info[(n,'s')][0]}/{len(xs)}  反转顺势 {info[(n,'ms')][0]}/{len(xs)}"
          f"  无反转 {info[(n,'nr')][0]}/{len(xs)}")
base = base.merge(ret, on="ts_code", how="left")
pd.DataFrame(WT).to_csv(os.path.join(OUT, "combo_weights_table.csv"), index=False)

# ---------- 3. 分组 ----------
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
IND = sb.set_index("ts_code")["industry"].to_dict()
ind_key, ind_name = {}, {}
for t, i in IND.items():
    if isinstance(i, str):
        ind_key.setdefault(i, []).append(t)
        ind_name[i] = i

tm = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str)
tm = pd.concat([tm, pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_key, con_name = {}, {}
for (bc, bn), g in tm.groupby(["board_code", "board_name"]):
    con_key[bc] = g["con_code"].tolist()
    con_name[bc] = bn
con_boards = sorted(con_name.values())
mapped = [b for bs in THEME_MAP.values() for b in bs]
assert sorted(mapped) == con_boards, f"主题映射与概念板块不一致: 缺 {set(con_boards)-set(mapped)} 多 {set(mapped)-set(con_boards)}"
th_key, th_name = {}, {}
for th, bs in THEME_MAP.items():
    codes = []
    for b in bs:
        codes += con_key[[k for k, v in con_name.items() if v == b][0]]
    th_key[th] = sorted(set(codes))
    th_name[th] = th
print(f"[groups] 行业 {len(ind_key)}  概念 {len(con_key)}  主题 {len(th_key)}")

# ---------- 4. 聚合 ----------
CMP = [f"{p}{n}" for n in NS for p in ("s", "ms", "nr")]
FACS_ORDER = order


def hitinfo(codes):
    """组在单因子上的成员均值（不要求 26 因子全有值），返回 {factor: mean}。"""
    sub = xs[xs["ts_code"].isin(codes)]
    return {f: sub["adj_" + f].mean() for f in FACS_ORDER}


def agg(keys, names, min_n):
    recs, means = [], []
    for k, codes in keys.items():
        sub = ret[ret["ts_code"].isin(codes)]
        if len(sub) < min_n:
            continue
        s = base[base["ts_code"].isin(codes)]
        r = {"key": k, "name": names.get(k, k), "n": len(sub),
             "r3": float(sub["r3"].mean()), "r20": float(sub["r20"].mean())}
        for c in CMP:
            v = s[c].mean(skipna=True)
            r[c] = None if pd.isna(v) else float(v)
            r["cov" + c] = int(s[c].notna().sum())
        recs.append(r)
        means.append(hitinfo(codes))
    df = pd.DataFrame(recs)
    M = pd.DataFrame(means)
    # 命中判定：该组在某一因子上的成员均值在这一层内排前 25%（规模无关）
    pr = M.rank(pct=True, method="average")
    hits_n, hits_txt = [], []
    for i in range(len(M)):
        hs = [f for f in FACS_ORDER if pr.iloc[i][f] >= 0.75]
        hs.sort(key=lambda x: -M.iloc[i][x])
        hits_n.append(len(hs))
        hits_txt.append(";".join(f"{x} {M.iloc[i][x]:.2f}" for x in hs[:5]))
    df["命中因子数"] = hits_n
    df["命中因子Top5"] = hits_txt
    return df


ind = agg(ind_key, ind_name, MIN_INDUSTRY_N)
con = agg(con_key, con_name, MIN_CONCEPT_N)
the = agg(th_key, th_name, MIN_THEME_N)
for df, f in [(ind, "combo_industry_rank.csv"), (con, "combo_concept_rank.csv"), (the, "combo_theme_rank.csv")]:
    df.to_csv(os.path.join(OUT, f), index=False)
print(f"[agg] 行业 {len(ind)}  概念 {len(con)}  主题 {len(the)}")
print(f"[基准] 全市场等权 近3日={ret['r3'].mean():+.2%} 近20日={ret['r20'].mean():+.2%}"
      f" | 行业均值 r20={ind['r20'].mean():+.2%} | 概念均值 r20={con['r20'].mean():+.2%}"
      f" | 主题均值 r20={the['r20'].mean():+.2%}")

# ---------- 5. 榜单打印 ----------
F = {"r3": "{:+.2%}".format, "r20": "{:+.2%}".format, "命中因子数": "{:.0f}".format}
for c in CMP:
    F[c] = "{:.3f}".format


def show(df, c, top=15):
    d = df.dropna(subset=[c]).sort_values(c, ascending=False, kind="mergesort")
    cols = ["name", "n", "r3", "r20", c, "cov" + c, "命中因子数", "命中因子Top5"]
    print(d[cols].head(top).to_string(index=False, formatters=F))


for n in NS:
    print(f"\n########## 主口径 n={n} ##########")
    print(f"--- 主题 Top16（成员≥{MIN_THEME_N}）---"); show(the, f"s{n}", 16)
    print(f"--- 行业 Top12 ---"); show(ind, f"s{n}", 12)
    print(f"--- 概念 Top15 ---"); show(con, f"s{n}", 15)

print(f"\n########## 反转顺势口径 n={NS[-1]}（诊断） ##########")
print("--- 概念 Top15 ---"); show(con, f"ms{NS[-1]}", 15)
print("--- 行业 Top12 ---"); show(ind, f"ms{NS[-1]}", 12)

print(f"\n########## 无反转口径（剔除 一个月反转/三个月反转 重新归一化） ##########")
for n in NS:
    print(f"--- 无反转 n={n} 主题 Top16 ---"); show(the, f"nr{n}", 16)
    print(f"--- 无反转 n={n} 行业 Top12 ---"); show(ind, f"nr{n}", 12)
    print(f"--- 无反转 n={n} 概念 Top15 ---"); show(con, f"nr{n}", 15)

# ---------- 6. 热门方向核查 ----------
WATCH_CONCEPT = ["人形机器人", "机器人概念", "减速器", "机器视觉", "特斯拉概念", "一体化压铸", "无人驾驶",
                 "AI视频", "多模态AI", "AIGC概念", "AI应用", "AI智能体", "短剧游戏", "虚拟数字人", "文化传媒概念",
                 "网络游戏", "手机游戏", "中国AI 50", "人工智能", "存储芯片", "先进封装", "光刻机", "共封装光学(CPO)",
                 "芯片概念", "第三代半导体", "磷化工", "化肥", "金属铜", "小金属概念", "黄金概念", "高股息精选",
                 "创新药", "CRO概念", "军工", "低空经济", "固态电池", "锂电池概念"]
WATCH_INDUSTRY = ["半导体", "元器件", "小金属", "铜", "铝", "汽车零部件", "电机", "通用机械", "专用机械", "电气设备",
                  "影视音像", "互联网", "软件服务", "通信设备", "化学制药", "医疗保健", "证券", "银行", "煤炭开采",
                  "石油开采", "出版业", "包装印刷", "化工原料", "农药化肥", "保险", "其他商业"]
print("\n########## 热门方向名次核查（主口径 n=10 / 反转顺势 n=26） ##########")


def watch_rows(df, layer, names):
    d = df.set_index("name")
    rk = {c: d[c].rank(ascending=False, method="min") for c in ("s10", "nr10", "r20")}
    out = []
    for nm in names:
        if nm not in d.index:
            continue
        r = d.loc[nm]
        if isinstance(r, pd.DataFrame):
            r = r.iloc[0]
        out.append({"层": layer, "名称": nm, "n": r["n"], "s5": r["s5"], "s10": r["s10"], "s26": r["s26"],
                    "nr5": r["nr5"], "nr10": r["nr10"], "ms26": r["ms26"],
                    "rk_s10": rk["s10"].loc[nm], "rk_nr10": rk["nr10"].loc[nm], "rk_r20": rk["r20"].loc[nm],
                    "命中因子数": r["命中因子数"], "r3": r["r3"], "r20": r["r20"]})
    return out


wk = pd.DataFrame(watch_rows(con, "概念", WATCH_CONCEPT) + watch_rows(ind, "行业", WATCH_INDUSTRY))
wk = wk.sort_values(["层", "rk_s10"], kind="mergesort")
print(wk.to_string(index=False, formatters={**F, "rk_s10": "{:.0f}".format, "rk_r20": "{:.0f}".format,
                                            "rk_nr10": "{:.0f}".format, "n": "{:.0f}".format}))
wk.to_csv(os.path.join(OUT, "combo_watch_rank.csv"), index=False)

for df, nm in [(ind, "行业"), (con, "概念"), (the, "主题")]:
    d = df.dropna(subset=["s10"])
    dn = df.dropna(subset=["nr10"])
    print(f"[{nm}] 主口径 s10~r20 {d['s10'].corr(d['r20']):+.3f}"
          f" | 无反转 nr10~r20 {dn['nr10'].corr(dn['r20']):+.3f}"
          f" | 反转顺势 ms26~r20 {d.dropna(subset=['ms26'])['ms26'].corr(d.dropna(subset=['ms26'])['r20']):+.3f}")

# ---------- 7. 个股榜 ----------
pool = base[base["总市值"] >= MIN_MV].copy()
pool["name"] = pool["ts_code"].map(NAME)
pool["行业"] = pool["ts_code"].map(IND)
pool["总市值_亿元"] = (pd.to_numeric(pool["总市值"], errors="coerce") / 10000).round(1)
main = pool.dropna(subset=["s10"]).sort_values("s10", ascending=False, kind="mergesort").reset_index(drop=True)
main.insert(0, "rank", np.arange(1, len(main) + 1))
cols = ["rank", "ts_code", "name", "行业", "总市值_亿元", "s5", "s10", "s26", "nr5", "nr10", "ms5", "ms10", "ms26", "r3", "r20"]
main[cols].to_csv(os.path.join(OUT, "combo_stock_rank.csv"), index=False)
print(f"\n[个股] 池内 {len(pool)}  有综合分 {len(main)}  输出 combo_stock_rank.csv")
print("--- 个股 Top15（主口径 n=10）---")
print(main[["rank", "name", "行业", "s10", "r20"]].head(15).to_string(index=False, formatters={"s10": "{:.4f}".format, "r20": "{:+.2%}".format}))
print("--- 个股 Top15（无反转 n=10）---")
mn = pool.dropna(subset=["nr10"]).sort_values("nr10", ascending=False, kind="mergesort").reset_index(drop=True)
print(mn[["name", "行业", "nr10", "r20"]].head(15).to_string(index=False, formatters={"nr10": "{:.4f}".format, "r20": "{:+.2%}".format}))
print("--- 个股 Top15（反转顺势 n=26，诊断）---")
mv = pool.dropna(subset=["ms26"]).sort_values("ms26", ascending=False, kind="mergesort").reset_index(drop=True)
print(mv[["name", "行业", "ms26", "r20"]].head(15).to_string(index=False, formatters={"ms26": "{:.4f}".format, "r20": "{:+.2%}".format}))

# ---------- 8. 板块成分个股 ----------
print("\n########## 板块成分个股（概念层，主口径 n=10） ##########")
nm2code = {v: k for k, v in con_name.items()}
for bn in ["AI视频", "人形机器人", "减速器", "短剧游戏"]:
    if bn not in nm2code:
        continue
    codes = con_key[nm2code[bn]]
    sub = base[base["ts_code"].isin(codes)].copy()
    sub["name"] = sub["ts_code"].map(NAME)
    sub = sub.sort_values("s10", ascending=False, kind="mergesort")
    print(f"\n--- {bn}（成员 {len(codes)}，有综合分 {int(sub['s10'].notna().sum())}）按主口径 n=10 Top10 ---")
    print(sub[["name", "s10", "nr10", "ms26", "r3", "r20"]].head(10).to_string(index=False, formatters={"s10": "{:.3f}".format, "nr10": "{:.3f}".format, "ms26": "{:.3f}".format, "r3": "{:+.2%}".format, "r20": "{:+.2%}".format}))
    print(f"--- {bn} 按无反转 n=10 Top10 ---")
    sub2 = sub.dropna(subset=["nr10"]).sort_values("nr10", ascending=False, kind="mergesort")
    print(sub2[["name", "s10", "nr10", "ms26", "r3", "r20"]].head(10).to_string(index=False, formatters={"s10": "{:.3f}".format, "nr10": "{:.3f}".format, "ms26": "{:.3f}".format, "r3": "{:+.2%}".format, "r20": "{:+.2%}".format}))
print("\n[done]")