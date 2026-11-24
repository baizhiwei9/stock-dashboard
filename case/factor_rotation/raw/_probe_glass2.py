# -*- coding: utf-8 -*-
"""深入打印玻璃（及家居用品/AI视频）修正后的买点明细，定位 D/B1@2026-09-22 来源。"""
import numpy as np
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"
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
    res, b3log = [], []
    bs = backshift_fix(ends, hist, plo, phi)
    b1_rows = set(e["row"] for e in bs if e["kind"] == "B1")
    for e in bs:
        if e["kind"] == "B1":
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
                if (row, "B3") not in seen:
                    seen.add((row, "B3")); res.append(dict(row=row, kind="B3", anchor=row, zg=az))
                b3log.append((row, round(float(zg), 2), round(az, 2)))
                if first_b3:
                    break
                left = False
    res.sort(key=lambda x: x["row"])
    return res, zss, b3log


for lv, name in [("行业", "玻璃"), ("行业", "家居用品"), ("概念", "AI视频")]:
    mem = ind_members.get(name, set()) if lv == "行业" else con_members.get(name, set())
    dfi, nm = synth(mem)
    df2, _ = cf.normalize_input(dfi, name=name)
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
    n = len(close); datev = df2["time"].dt.strftime("%Y-%m-%d").values
    print(f"\n===== {lv}/{name}  bars={n} {datev[0]}~{datev[-1]} 笔={len(ends)} =====")
    print("endpoints:", [(i, int(e[0]), datev[int(e[0])], e[1], round(float(e[2]), 2)) for i, e in enumerate(ends)])
    for mode, (plo, phi) in [("close", (close, close)), ("extreme", (low, high))]:
        for fb in (False, True):
            buys, zss, b3log = buys_detail(ends, m["hist"].values, plo, phi, close, fb)
            rec = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
            def rows(x):
                return [(b["kind"], datev[b["row"]], int(n - 1 - b["row"]), b["row"]) for b in x]
            print(f"  [{mode} first_b3={fb}] 中枢={[(int(s), int(e), round(float(zd),1), round(float(zg),1)) for s,e,zd,zg in zss]} "
                  f"| 全部买点={rows(buys)}")
            print(f"      近40日买点={rows(rec)}  取最后={rows(rec[-1:])}")