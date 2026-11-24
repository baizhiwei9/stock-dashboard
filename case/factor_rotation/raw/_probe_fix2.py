# -*- coding: utf-8 -*-
"""干净版影响面诊断：每个板块只做一次 synth/normalize，再在其上计算
(close/extreme) x (first_b3 False/True) 四种组合，打印买点明细与档位。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, theme_members = ns["synth"], ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc, WINDOW = ns["cf"], ns["cc"], ns["WINDOW"]


def backshift(ends, hist, plo, phi):
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
                ev.append(dict(row=r2b, kind="B1", anchor=r2b, zg=None))
        else:
            h1 = float(np.max(phi[r1a:r1b + 1])); h2 = float(np.max(phi[r2a:r2b + 1]))
            if h2 >= h1 and a2 < a1 * 0.9:
                ev.append(dict(row=r2b, kind="S1", anchor=r2b, zg=None))
    return ev


def buys2(ends, hist, plo, phi, close, first_b3):
    """返回 (全部买点, 三买日志, 中枢)"""
    bs = backshift(ends, hist, plo, phi)
    res = [e for e in bs if e["kind"] == "B1"]
    b1_rows = set(e["row"] for e in res)
    last_b1 = None
    for idx, (row, tp, px_) in enumerate(ends):
        if row in b1_rows:
            last_b1 = idx
        elif tp == "bottom" and last_b1 is not None and row not in b1_rows:
            if any(ends[k][1] == "top" for k in range(last_b1 + 1, idx)) and px_ > ends[last_b1][2]:
                res.append(dict(row=row, kind="B2", anchor=ends[last_b1][0], zg=None)); last_b1 = None
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    zinfo = [(ends[s][0], float(zg)) for s, _, zd, zg in zss]
    seen, b3log = set(), []
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
                if (row, "B3") not in seen:
                    seen.add((row, "B3")); res.append(dict(row=row, kind="B3", anchor=row, zg=az))
                b3log.append((row, round(float(zg), 1), round(float(az), 1)))
                if first_b3:
                    break
                left = False
    res.sort(key=lambda x: x["row"])
    return res, b3log, zss


def tier_of(buy, close, low, n, ends, zss):
    if buy is not None:
        r0 = buy["row"]; ac = close[r0 + 1:]
        inv = bool(ac.size and ((ac <= buy["zg"]).any() if buy["kind"] == "B3"
                                else (ac < float(low[buy["anchor"]])).any()))
        if not inv:
            ma10 = pd.Series(close).rolling(10).mean().values[-1]
            ma20 = pd.Series(close).rolling(20).mean().values[-1]
            k = 0
            for t in range(n - 1, 0, -1):
                if close[t] > close[t - 1]:
                    k += 1
                else:
                    break
            c1 = bool(np.isfinite(ma10) and np.isfinite(ma20) and ma10 > ma20)
            c3 = any(e[1] == "top" and e[0] > r0 for e in ends)
            return ("S1" if (c1 or k >= 3) else ("S2" if c3 else "S3")), inv
        return "D", inv
    zl = max(zss, key=lambda z: (z[1], z[0])) if zss else None
    zg = float(zl[3]) if zl else None
    return ("C" if (zg is not None and close[-1] > zg) else "E"), None


v4 = pd.read_csv(f"{OUT}/chan_final_v4.csv")
sel = v4[v4["档位_极值口径"].isin(["S1", "S2", "S3"])]
targets = [(r["层级"], r["名称"], int(r["因子名次"])) for _, r in sel.iterrows()]
targets += [("概念", "AI视频", None), ("主题", "AI软件与应用", None), ("主题", "机器人与智能制造", None),
            ("概念", "人形机器人", None), ("概念", "机器人概念", None), ("主题", "AI算力与芯片", None)]

print("板块 | 层 | 因子# | 中枢(端点区间,ZG) | 旧B3序列 | 新B3序列 | 旧(极值) | 新(极值) | 旧(close) | 新(close)")
for lv, name, rk_ in targets:
    mem = con_members.get(name, set()) if lv == "概念" else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set()))
    if not mem:
        print(f"{lv}/{name}: 无成分股"); continue
    dfi, nm = synth(mem)
    df2, _ = cf.normalize_input(dfi, name=name)
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
    n = len(close); datev = df2["time"].dt.strftime("%Y-%m-%d").values
    out = {}
    for mode, (plo, phi) in [("close", (close, close)), ("extreme", (low, high))]:
        for fb in (False, True):
            buys, b3log, zss = buys2(ends, m["hist"].values, plo, phi, close, fb)
            rec = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
            t, inv = tier_of(rec[-1] if rec else None, close, low, n, ends, zss)
            out[(mode, fb)] = dict(t=t, buy=rec[-1] if rec else None, b3=[(datev[r], z) for r, z, _ in b3log],
                                   zss=[(int(s), int(e), round(float(zg), 1)) for s, e, zd, zg in zss])
    rk_s = f"#{int(v4[v4['名称'] == name]['因子名次'].iloc[0])}" if rk_ is None and len(v4[v4['名称'] == name]) else f"#{rk_}"
    print(f"\n{lv}/{name} {rk_s}")
    print(f"   中枢 = {out[('extreme', False)]['zss']}")
    print(f"   旧B3 = {out[('extreme', False)]['b3']}")
    print(f"   新B3 = {out[('extreme', True)]['b3']}")
    for mode in ("extreme", "close"):
        o_, n_ = out[(mode, False)], out[(mode, True)]
        fb = lambda d: f"{d['t']}/{(d['buy']['kind'] + '@' + datev[d['buy']['row']]) if d['buy'] else '-'}"
        print(f"   [{mode}] 旧={fb(o_)}  新={fb(n_)}")