# -*- coding: utf-8 -*-
"""探针 v7：为「三买不破中枢 + 中枢绑定」与「MA40 / MA10上穿MA20 过滤」取证。
输出 raw/_ma_probe.csv（全 254 板块的均线状态 + 双口径档位）与 B3 锚定中枢明细。
"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth = ns["synth"]; con_members, ind_members, theme_members = ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc, WINDOW = ns["cf"], ns["cc"], ns["WINDOW"]


def backshift_fix(ends, hist, plo, phi):
    ev = []
    for i in range(3, len(ends)):
        if ends[i][1] != ends[i - 2][1]:
            continue
        r1a, r1b = ends[i - 3][0], ends[i - 2][0]
        r2a, r2b = ends[i - 1][0], ends[i][0]
        if r1b <= r1a or r2b <= r2a:
            continue
        s1 = hist[r1a:r1b + 1]; s2 = hist[r2a:r2b + 1]
        if len(s1) < 2 or len(s2) < 2:
            continue
        a1, a2 = abs(float(np.sum(s1))), abs(float(np.sum(s2)))
        if a1 <= 1e-12:
            continue
        if ends[i][1] == "bottom":
            l1 = float(np.min(plo[r1a:r1b + 1])); l2 = float(np.min(plo[r2a:r2b + 1]))
            if l2 <= l1 and a2 < a1 * 0.9:
                ev.append((r2b, "B1"))
        else:
            h1 = float(np.max(phi[r1a:r1b + 1])); h2 = float(np.max(phi[r2a:r2b + 1]))
            if h2 >= h1 and a2 < a1 * 0.9:
                ev.append((r2b, "S1"))
    return ev


def detect_bs_fixed(ends, hist, plo, phi, close):
    """修正版：三买/三卖严格「回抽不回中枢」——回抽若回到中枢内则本次离开作废。
    返回 (res, zss)，B3/S3 附带所依据中枢。"""
    ev = backshift_fix(ends, hist, plo, phi)
    res = [dict(row=r, kind=k, zs=None) for r, k in ev]
    b1 = set(r for r, k in ev if k == "B1"); s1 = set(r for r, k in ev if k == "S1")
    last_b1 = None
    for idx, (row, tp, px) in enumerate(ends):
        if row in b1:
            last_b1 = idx
        elif tp == "bottom" and last_b1 is not None and row not in b1:
            if any(ends[k][1] == "top" for k in range(last_b1 + 1, idx)) and px > ends[last_b1][2]:
                res.append(dict(row=row, kind="B2", zs=None)); last_b1 = None
    last_s1 = None
    for idx, (row, tp, px) in enumerate(ends):
        if row in s1:
            last_s1 = idx
        elif tp == "top" and last_s1 is not None and row not in s1:
            if any(ends[k][1] == "bottom" for k in range(last_s1 + 1, idx)) and px < ends[last_s1][2]:
                res.append(dict(row=row, kind="S2", zs=None)); last_s1 = None
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    seen = set()
    for (s, e_, zd, zg) in zss:
        left = False
        for idx in range(e_, len(ends)):
            row, tp, px = ends[idx]
            if tp == "top":
                if idx > 0 and max(px, ends[idx - 1][2]) > zg:
                    left = True
            elif tp == "bottom":
                if left and px > zg:
                    if (row, "B3") not in seen:
                        seen.add((row, "B3"))
                        res.append(dict(row=row, kind="B3", zs=(s, e_, zd, zg)))
                    break
                elif left and px <= zg:
                    left = False  # 回抽跌回中枢 -> 本次离开作废
    seen = set()
    for (s, e_, zd, zg) in zss:
        left = False
        for idx in range(e_, len(ends)):
            row, tp, px = ends[idx]
            if tp == "bottom":
                if idx > 0 and min(px, ends[idx - 1][2]) < zd:
                    left = True
            elif tp == "top":
                if left and px < zd:
                    if (row, "S3") not in seen:
                        seen.add((row, "S3"))
                        res.append(dict(row=row, kind="S3", zs=(s, e_, zd, zg)))
                    break
                elif left and px >= zd:
                    left = False
    res.sort(key=lambda x: x["row"])
    return res, zss


rk = pd.read_csv(f"{OUT}/nonneg_layer_rank.csv")
rows = []; b3_rows = []
for _, r0 in rk.iterrows():
    lv, name, rk_ = r0["层级"], r0["名称"], int(r0["名次"])
    mem = con_members.get(name, set()) if lv == "概念" else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set()))
    dfi, nm = synth(mem)
    if dfi is None:
        continue
    df2, _ = cf.normalize_input(dfi, name=name)
    close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
    n = len(close); datev = df2["time"].dt.strftime("%Y-%m-%d").values
    s = pd.Series(close)
    ma10 = s.rolling(10).mean().values; ma20 = s.rolling(20).mean().values; ma40 = s.rolling(40).mean().values
    above40 = bool(np.isfinite(ma40[-1]) and close[-1] > ma40[-1])
    gt = bool(np.isfinite(ma10[-1]) and np.isfinite(ma20[-1]) and ma10[-1] > ma20[-1])
    cross_ago = None
    for t in range(n - 1, 0, -1):
        if np.isfinite(ma20[t]) and np.isfinite(ma20[t - 1]) and ma10[t] > ma20[t] and ma10[t - 1] <= ma20[t - 1]:
            cross_ago = int(n - 1 - t); break
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    bs_fixed, zss = detect_bs_fixed(ends, m["hist"].values, low, high, close)
    rec_fixed = [b for b in bs_fixed if (n - 1 - b["row"]) <= WINDOW]
    b3s = [b for b in rec_fixed if b["kind"] == "B3"]
    s3s = [b for b in rec_fixed if b["kind"] == "S3"]
    rows.append(dict(层级=lv, 名称=name, 因子名次=rk_, 最新收盘=round(float(close[-1]), 2),
                     MA10=round(float(ma10[-1]), 2), MA20=round(float(ma20[-1]), 2),
                     站上MA40=above40, MA10gtMA20=gt, MA10上穿MA20距今天=cross_ago,
                     MA过滤通过=bool(above40 or gt),
                     近40买点固定=",".join(f"{b['kind']}@{datev[b['row']][5:]}" for b in rec_fixed) or "",
                     近40卖点固定=",".join(f"{b['kind']}@{datev[b['row']][5:]}" for b in s3s) or ""))
    for b in b3s:
        z = b["zs"]
        b3_rows.append(dict(层级=lv, 名称=name, 因子名次=rk_, 买点日期=datev[b["row"]],
                            买点价=round(float(close[b["row"]]), 2),
                            ZD=round(z[2], 2), ZG=round(z[3], 2),
                            中枢起=datev[int(ends[z[0]][0])], 中枢止=datev[int(ends[z[1]][0])]))

p = pd.DataFrame(rows); p.to_csv(f"{RAW}/_ma_probe.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(b3_rows).to_csv(f"{RAW}/_b3_probe.csv", index=False, encoding="utf-8-sig")
print("=== 全池 MA 过滤通过数 ===", int(p["MA过滤通过"].sum()), "/", len(p))
print("\n=== 站上MA40 明细（不通过MA10>MA20 但通过MA40 的） ===")
print("通过组合分布:", p.groupby(["站上MA40", "MA10gtMA20"]).size().to_dict())
print("\n=== S档参照（极值口径 S1/S2/S3）——均线状态 ===")
v4 = pd.read_csv(f"{OUT}/chan_final_v4.csv")
S = v4[v4["档位_极值口径"].isin(["S1", "S2", "S3"])][["层级", "名称", "因子名次", "买点类型_极值口径", "买点日期", "档位_极值口径"]]
S = S.merge(p, on=["层级", "名称", "因子名次"], how="left")
print(S[["层级", "名称", "因子名次", "买点类型_极值口径", "买点日期", "MA10", "MA20", "站上MA40", "MA10gtMA20", "MA10上穿MA20距今天", "MA过滤通过"]].to_string(index=False))
print("\n=== 点名：电机 ===")
print(p[p["名称"] == "电机"].to_string(index=False))
print("\n=== B3 明细（近40日，修正后） ===")
print(pd.DataFrame(b3_rows).to_string(index=False) if b3_rows else "(无)")