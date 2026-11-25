# -*- coding: utf-8 -*-
"""重建 2022 起四因子面板（与既有 factors_mom.csv 同口径）
z_roll/z_basis/z_droll: 时间序列滚动z（120日窗口 minp60，验证匹配率89.6%）
z_inv: -sqrt(12)*(p-0.5)，p=近5年同期(±15d, 年替换)库存分位
score = 0.35*z_inv + 0.35*z_roll + 0.15*z_basis + 0.15*z_droll
mom: ret_hold 20/60日收益 + 横截面z(与既有 factors_mom 同法，先对齐校验)
"""
import pandas as pd, numpy as np, math

p = pd.read_csv("data/full/panel_full.csv", parse_dates=["date"])
p = p.sort_values(["variety","date"]).reset_index(drop=True)
p["sector"] = np.where(p["variety"].isin({"MA","TA","SA","UR","V","PP","L","EG","EB"}), "化工","农产品")

# ---- 时间序列因子（按品种）----
def time_z(series, win=120, minp=60):
    mean = series.rolling(win, min_periods=minp).mean()
    std  = series.rolling(win, min_periods=minp).std(ddof=0)
    return (series - mean) / std

p["z_roll"]  = p.groupby("variety", group_keys=False)["roll_annual"].apply(lambda s: time_z(s))
p["z_basis"] = p.groupby("variety", group_keys=False)["basis_rate"].apply(lambda s: time_z(s))
# droll5 = 5日变化率
p["droll5"] = p.groupby("variety", group_keys=False)["roll_annual"].diff(5)
p["z_droll"] = p.groupby("variety", group_keys=False)["droll5"].apply(lambda s: time_z(s))

# ---- z_inv：近5年同期分位 ----
inv_paths = {v: None for v in p.variety.unique()}
inv = {}
for v in p.variety.unique():
    inv[v] = pd.read_csv(f"data/norm/inventory_{v}.csv", parse_dates=["date"]).dropna()
    inv[v]["y"] = inv[v]["date"].dt.year
    inv[v]["doy"] = inv[v]["date"].dt.dayofyear

def inv_z(v, d):
    df = inv[v]; yy = d.year; doy = d.dayofyear
    rows = df[df["date"] == d]
    if len(rows) == 0: return np.nan
    val = rows.iloc[0]["stock"]
    seas = []
    for y in range(1, 6):
        yy2 = yy - y
        g = df[(df["y"] == yy2) & (df["doy"].between(doy-15, doy+15))]
        seas += g["stock"].tolist()
    if not seas: return np.nan
    seas = sorted(seas)
    pv = (sum(1 for x in seas if x <= val) + 0.5) / (len(seas) + 1)
    return -math.sqrt(12) * (pv - 0.5)

# 按品种日期分批（向量化加速：对每个品种预计算(year,doy)->stock；
# 当日无库存时取最近一期（<=d 的最后一条），避免库存数据滞后导致 z_inv 全 NaN）
print("计算 z_inv ...")
zinv_vals = []
for v, gp in p.groupby("variety"):
    df = inv[v]
    stock_map = {(int(r.y), int(r.doy)): r.stock for r in df.itertuples()}
    # 最近可得日期索引（先升序）
    dseq = df.sort_values("date").reset_index(drop=True)
    d_arr = dseq["date"].values.astype("datetime64[D]")
    s_arr = dseq["stock"].values
    seas_by_ydoy = {}
    for y0 in sorted(df["y"].unique()):
        ser = df[df["y"]==y0].set_index("doy")["stock"]
        seas_by_ydoy[y0] = ser
    out = {}
    for r in gp.itertuples():
        d = r.date; yy = d.year; doy = d.dayofyear
        key = (yy, doy)
        if key in stock_map:
            val = stock_map[key]
        else:
            # 当日无库存：取最近一期（<= d），避免 z_inv 直接 NaN
            pos = int(np.searchsorted(d_arr, np.datetime64(d, "D"), side="right")) - 1
            if pos < 0:
                out[(yy, doy)] = np.nan
                continue
            val = float(s_arr[pos])
        seas = []
        for y in range(1,6):
            yy2 = yy - y
            ser = seas_by_ydoy.get(yy2)
            if ser is None: continue
            lo, hi = doy-15, doy+15
            sel = ser[(ser.index>=lo)&(ser.index<=hi)]
            seas += sel.tolist()
        if not seas:
            out[(yy, doy)] = np.nan
        else:
            seas_sorted = sorted(seas)
            pv = (sum(1 for x in seas_sorted if x <= val) + 0.5)/(len(seas_sorted)+1)
            out[(yy,doy)] = -math.sqrt(12)*(pv-0.5)
    zinv_vals.append(gp.assign(z_inv=gp.apply(lambda r: out.get((r.date.year, r.date.dayofyear), np.nan), axis=1)))
zinv = pd.concat(zinv_vals)
p["z_inv"] = zinv["z_inv"].values

# ---- score（单项因子缺失时权重归一化，score 不因库存断更整体变 NaN）----
W = {"z_inv": 0.35, "z_roll": 0.35, "z_basis": 0.15, "z_droll": 0.15}
def robust_score(row):
    acc_w, acc_x = 0.0, 0.0
    for k, w in W.items():
        x = row[k]
        if pd.notna(x):
            acc_w += w; acc_x += w * x
    return acc_x / acc_w if acc_w > 0 else np.nan
p["score"] = p.apply(robust_score, axis=1)

# ---- mom：20/60 日 ret_hold 收益 ----
p["momret_20"] = p.groupby("variety", group_keys=False)["ret_hold"].apply(lambda s: (1+s).rolling(20).apply(np.prod, raw=True) - 1)
p["momret_60"] = p.groupby("variety", group_keys=False)["ret_hold"].apply(lambda s: (1+s).rolling(60).apply(np.prod, raw=True) - 1)
# 板块内横截面z（与既有 factors_mom 一致）
CHEMM = {"MA","TA","SA","UR","V","PP","L","EG","EB"}
def xz(s):
    return (s - s.mean())/s.std(ddof=1)
p["momz_20"] = p.groupby(["date","sector"])["momret_20"].transform(xz)
p["momz_60"] = p.groupby(["date","sector"])["momret_60"].transform(xz)

p.to_csv("data/full/factors_full.csv", index=False)
print("factors_full:", p.shape, p.date.min(), "->", p.date.max())
print("z_inv 有效:", p.z_inv.notna().mean(), "| score 有效:", p.score.notna().mean())

# ---- 与既有 factors_mom.csv 重叠区间验证 ----
old = pd.read_csv("data/norm/factors_mom.csv", parse_dates=["date"])
m = old.merge(p, on=["date","variety"], suffixes=("_o","_n"))
m = m[m.date>="2024-06-03"]
for c in ["z_roll","z_basis","z_droll","z_inv","score","momz_20"]:
    mo = m[[c+"_o",c+"_n"]].dropna()
    if len(mo)>100:
        corr = mo[c+"_o"].corr(mo[c+"_n"])
        print(f"{c}: n={len(mo)} corr={corr:.4f}")
