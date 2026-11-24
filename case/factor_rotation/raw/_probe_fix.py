# -*- coding: utf-8 -*-
"""诊断：B3「同一中枢反复产出」缺陷的影响面 + 「每中枢只取首个 B3」修正后的档位变化。
对 11 个当前入选板块 + 用户点名的 6 个高名次板块，逐一双口径复算。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, theme_members = ns["synth"], ns["con_members"], ns["ind_members"], ns["theme_members"]
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
                ev.append(dict(row=r2b, kind="B1", anchor=r2b))
        else:
            h1 = float(np.max(phi[r1a:r1b + 1])); h2 = float(np.max(phi[r2a:r2b + 1]))
            if h2 >= h1 and a2 < a1 * 0.9:
                ev.append(dict(row=r2b, kind="S1", anchor=r2b))
    return ev


def buys_detail(ends, hist, plo, phi, close, first_b3):
    """first_b3=True: 每个中枢只取「离开中枢后的首次回抽」B3（标准三买）。"""
    res, b3log = [], []
    bs = backshift_fix(ends, hist, plo, phi)
    b1_rows = set(e["row"] for e in bs if e["kind"] == "B1")
    for e in bs:
        res.append(dict(row=e["row"], kind="B1", anchor=e["row"], zg=None))
    last_b1 = None
    for idx, (row, tp, px_) in enumerate(ends):
        if row in b1_rows:
            last_b1 = idx
        elif tp == "bottom" and last_b1 is not None and row not in b1_rows:
            if any(ends[k][1] == "top" for k in range(last_b1 + 1, idx)) and px_ > ends[last_b1][2]:
                res.append(dict(row=row, kind="B2", anchor=ends[last_b1][0], zg=None)); last_b1 = None
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    zinfo = [(ends[s][0], float(zg)) for s, _, zd, zg in zss]
    seen = set()
    for s, e_, zd, zg in zss:
        left = False
        for idx in range(e_, len(ends)):
            row, tp, px_ = ends[idx]
            if tp == "top":
                if idx > 0 and max(px_, ends[idx - 1][2]) > zg:
                    left = True
            elif left and tp == "bottom" and px_ > zg:
                bc = float(close[row]); cand = [z for (sr, z) in zinfo if z < bc and sr <= row]
                az = max(cand) if cand else float(zg)
                e_ok = (row, "B3") not in seen
                if e_ok:
                    seen.add((row, "B3")); res.append(dict(row=row, kind="B3", anchor=row, zg=az))
                b3log.append(dict(row=row, date=None, zg_anchor=round(az, 2), zs_zg=round(float(zg), 2)))
                if first_b3:
                    break
                left = False
    res.sort(key=lambda x: x["row"])
    return res, zss, b3log


def judge(buy, close, low, n, datev):
    if buy is None:
        return None
    r0 = buy["row"]; ac = close[r0 + 1:]
    if buy["kind"] == "B3":
        zg = buy["zg"]; inv = bool(ac.size and zg is not None and (ac <= zg).any())
    else:
        alo = float(low[buy["anchor"]]); inv = bool(ac.size and (ac < alo).any())
    return dict(kind=buy["kind"], date=str(datev[r0]), ago=int(n - 1 - r0),
                valid=(not inv))


def run(mem, px_mode, first_b3):
    dfi, nm = synth(mem)
    if dfi is None:
        return None
    df2, _ = cf.normalize_input(dfi, name="b")
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
    n = len(close); datev = df2["time"].dt.strftime("%Y-%m-%d").values
    plo, phi = (close, close) if px_mode == "close" else (low, high)
    buys, zss, b3log = buys_detail(ends, m["hist"].values, plo, phi, close, first_b3)
    for b in b3log:
        b["date"] = datev[b["row"]]
    rec = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
    zl = max(zss, key=lambda z: (z[1], z[0])) if zss else None
    zg = float(zl[3]) if zl else None
    brk = zg is not None and close[-1] > zg
    j = judge(rec[-1] if rec else None, close, low, n, datev)
    ma10 = pd.Series(close).rolling(10).mean().values[-1]
    ma20 = pd.Series(close).rolling(20).mean().values[-1]
    k = 0
    for t in range(n - 1, 0, -1):
        if close[t] > close[t - 1]:
            k += 1
        else:
            break
    c1 = bool(np.isfinite(ma10) and np.isfinite(ma20) and ma10 > ma20)
    c3 = bool(rec and any(e[1] == "top" and e[0] > rec[-1]["row"] for e in ends))
    if j and j["valid"]:
        tier = "S1" if (c1 or k >= 3) else ("S2" if c3 else "S3")
    elif j:
        tier = "D"
    elif brk:
        tier = "C"
    else:
        tier = "E"
    return dict(tier=tier, zg=(round(zg, 2) if zg is not None else None), close=round(float(close[-1]), 2),
                ma=round(float(ma10 / ma20 - 1) * 100, 2) if np.isfinite(ma10) and np.isfinite(ma20) else None,
                streak=k, j=j, n=n, ends=ends, zss=zss, buys=buys, b3log=b3log,
                zs_rows=[(int(ends[s][0]), int(ends[e_][0]), float(zd), float(zg)) for s, e_, zd, zg in zss],
                dates=datev, b3_all_dates=[(b["row"], datev[b["row"]], b["zg_anchor"]) for b in b3log])


v4 = pd.read_csv(f"{OUT}/chan_final_v4.csv")
v5 = v4[v4["档位_极值口径"].isin(["S1", "S2", "S3"])]
targets = [(r["层级"], r["名称"], int(r["因子名次"]), r["档位_极值口径"], r["买点类型_极值口径"], str(r["买点日期"]))
           for _, r in v5.iterrows()]
extra = [("概念", "AI视频", None), ("主题", "AI软件与应用", None), ("主题", "机器人与智能制造", None),
         ("概念", "人形机器人", None), ("概念", "机器人概念", None), ("主题", "AI算力与芯片", None)]
print("=== 当前入选（极值口径 S1/S2/S3）===")
for lv, name, rk_, t0, bk0, bd0 in targets:
    mem = con_members.get(name, set()) if lv == "概念" else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set()))
    if not mem:
        print(f"{lv}/{name}: 无成分股"); continue
    old = run(mem, "extreme", False)
    new = run(mem, "extreme", True)
    print(f"\n{lv}/{name}  因子#{rk_}  旧={t0}/{bk0}@{bd0}  新={new['tier']}/{(new['j']['kind']+'@'+new['j']['date']) if new['j'] else None}")
    print(f"   中枢(端点区间→行号, ZG): {[(int(o[0]), int(o[1]), int(o[2]), round(o[3],1)) for o in old['zs_rows']]}")
    print(f"   旧B3序列: {[(d, round(z,1)) for _, d, z in old['b3_all_dates']]}")
    print(f"   新B3序列: {[(d, round(z,1)) for _, d, z in new['b3_all_dates']]}")
    print(f"   最新 {new['close']} MA10/20={new['ma']}% 连涨={new['streak']} 最近中枢ZG={new['zg']}")

print("\n=== 新档候选（用户点名 6 个，极值口径，修正后）===")
for lv, name, _ in extra:
    mem = con_members.get(name, set()) if lv == "概念" else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set()))
    if not mem:
        print(f"{lv}/{name}: 无成分股"); continue
    new = run(mem, "extreme", True)
    rkrow = v4[(v4["名称"] == name)]
    rk_ = int(rkrow["因子名次"].iloc[0]) if len(rkrow) else None
    print(f"{lv}/{name} 因子#{rk_} 新档={new['tier']} 买点={(new['j']['kind']+'@'+new['j']['date']) if new['j'] else None} "
          f"最新{new['close']} MA10/20={new['ma']}% 连涨={new['streak']} 最近中枢ZG={new['zg']} 笔数={len(new['ends'])}")