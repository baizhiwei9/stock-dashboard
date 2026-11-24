# -*- coding: utf-8 -*-
"""为入选/关注板块准备绘图数据：K线 + 缠论笔 + 中枢 + 缠论买卖点（极值口径，修正配对）。

v3（2026-10-30）：
  1) 三买/三卖严格「回抽不回中枢」：向上离开中枢后，若回抽又回到中枢内
     （回抽底 ≤ ZG / 回抽顶 ≥ ZD），本次「离开」作废，需重新突破才可能成立；
     每个 B3/S3 记录其所依据的中枢（端点序号的端点、行号、ZD、ZG）。
  2) 每个板块计算 MA10/MA20/MA40 与均线过滤：
     均线过关 = （最新收盘 > MA40）或（最新 MA10 > MA20）。
  3) 分组：A=买点有效且均线过关；B=买点有效但均线未过关；C=用户点名关注·暂无有效买点。
产出 raw/_panels.pkl 供 raw/chan_panels_plot.py 绘图。
"""
import re
import numpy as np, pandas as pd, pickle, json
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth = ns["synth"]; con_members, ind_members, theme_members = ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc, WINDOW = ns["cf"], ns["cc"], ns["WINDOW"]
CN = {"B1": "一买", "B2": "二买", "B3": "三买", "S1": "一卖", "S2": "二卖", "S3": "三卖"}

_w = open(f"{RAW}/final_pick_fullA.py", encoding="utf-8").read()
def _lst(tag):
    m = re.search(tag + r"\s*=\s*\[(.*?)\]", _w, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []
WATCH_NAMES = list(dict.fromkeys(_lst("TARGET_BOARDS") + _lst("TARGET_CON") + _lst("TARGET_THE")))


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


def detect_bs(ends, hist, plo, phi):
    """修正版买点：三买/三卖严格「回抽不回中枢」；B3/S3 带所依据中枢 (s_row,e_row,zd,zg)。"""
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
    # 3买：向上离开中枢(ZG) -> 回抽底 > ZG（不破中枢）；回抽跌回中枢则本次离开作废
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
                        res.append(dict(row=row, kind="B3",
                                        zs=(int(ends[s][0]), int(ends[e_][0]), float(zd), float(zg))))
                    break
                elif left and px <= zg:
                    left = False  # 回抽回到中枢内 -> 本次离开作废
    # 3卖：向下离开中枢(ZD) -> 回抽顶 < ZD（不回中枢）；回抽重回中枢则作废
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
                        res.append(dict(row=row, kind="S3",
                                        zs=(int(ends[s][0]), int(ends[e_][0]), float(zd), float(zg))))
                    break
                elif left and px >= zd:
                    left = False
    res.sort(key=lambda x: x["row"])
    return res, zss


def status_of(grp, tc, kd, dt):
    if grp in ("A", "B"):
        base = {"S1": "右侧已确认", "S2": "向上笔确认", "S3": "待右侧确认"}.get(tc, "待右侧确认")
        return (("均线过关 · " + base) if grp == "A" else ("均线未过关 · " + base + "（趋势未确认）"))
    tc = str(tc); kd = str(kd); dt = str(dt)
    if tc == "D":
        return f"点名关注 · B点已失效（{CN.get(kd, kd)} {dt[5:] if len(dt) >= 10 else dt}）"
    if tc == "C":
        return "点名关注 · 已站上中枢上沿·待回抽确认"
    if tc in ("S1", "S2", "S3"):
        return f"点名关注 · 收盘口径{CN.get(kd, kd)}未获极值口径确认"
    return "点名关注 · 待买点 / 待突破"


v4 = pd.read_csv(f"{OUT}/chan_final_v4.csv")
S_sel = v4[v4["档位_极值口径"].isin(["S1", "S2", "S3"])].copy()
W_sel = v4[(~v4["档位_极值口径"].isin(["S1", "S2", "S3"])) & v4["名称"].isin(WATCH_NAMES)].copy()
S_sel = S_sel.sort_values(["因子名次"])  # 组内先按名次，绘图前按均线分组
print(f"[select] S档(极值口径买点)={len(S_sel)} W档(点名关注·暂无买点)={len(W_sel)}")

panels = []
for _, r in pd.concat([S_sel, W_sel], ignore_index=True).iterrows():
    lv, name = r["层级"], r["名称"]
    mem = (con_members.get(name, set()) if lv == "概念"
           else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set())))
    dfi, nm = synth(mem)
    if dfi is None:
        print("skip", lv, name); continue
    df2, _ = cf.normalize_input(dfi, name=name)
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
    bs, zss = detect_bs(ends, m["hist"].values, low, high)
    openv = df2["open"].values
    datev = df2["time"].dt.strftime("%Y-%m-%d").values
    n = len(close)
    # 均线
    s = pd.Series(close)
    ma10 = s.rolling(10).mean().values; ma20 = s.rolling(20).mean().values; ma40 = s.rolling(40).mean().values
    above40 = bool(np.isfinite(ma40[-1]) and close[-1] > ma40[-1])
    gt = bool(np.isfinite(ma10[-1]) and np.isfinite(ma20[-1]) and ma10[-1] > ma20[-1])
    ma_pass = bool(above40 or gt)
    cross_ago = None
    for t in range(n - 1, 0, -1):
        if np.isfinite(ma20[t]) and np.isfinite(ma20[t - 1]) and ma10[t] > ma20[t] and ma10[t - 1] <= ma20[t - 1]:
            cross_ago = int(n - 1 - t); break
    # 组别 + 选中买点（极值口径，近 WINDOW 内最后一个买点）
    is_S = r["档位_极值口径"] in ("S1", "S2", "S3")
    if is_S:
        rec = [b for b in bs if b["kind"][0] == "B" and (n - 1 - b["row"]) <= WINDOW]
        sel_b = rec[-1] if rec else None
        grp = "A" if ma_pass else "B"
        tag = status_of(grp, r["档位_极值口径"], sel_b["kind"] if sel_b else "", datev[sel_b["row"]] if sel_b else "")
        tagcol = "#1e7a34" if grp == "A" else "#b03a2e"
    else:
        sel_b = None; grp = "C"
        tag = status_of("C", r["档位"], r["买点类型"], r["买点日期"])
        tagcol = "#1f618d"
    buy_kind = sel_b["kind"] if sel_b else None
    buy_row = int(sel_b["row"]) if sel_b else None
    buy_date = str(datev[sel_b["row"]]) if sel_b else ""
    sel_zs = sel_b["zs"] if (sel_b and sel_b["zs"]) else None
    ma_at_buy = None
    if buy_row is not None:
        bb40 = bool(np.isfinite(ma40[buy_row]) and close[buy_row] > ma40[buy_row])
        bgt = bool(np.isfinite(ma20[buy_row]) and ma10[buy_row] > ma20[buy_row])
        ma_at_buy = ("站上MA40" if bb40 else "") + ("/MA10>MA20" if bgt else "")
        ma_at_buy = ma_at_buy or "均线未过关"
    zs_rows = [(int(ends[a][0]), int(ends[b][0]), float(zd), float(zg)) for a, b, zd, zg in zss]
    zg_line = float(sel_zs[3]) if sel_zs else (float(r["最近中枢上沿"]) if pd.notna(r["最近中枢上沿"]) else None)
    panels.append(dict(
        层级=lv, 名称=name, 因子名次=int(r["因子名次"]), grp=grp, tag=tag, tag_col=tagcol,
        buy_kind=buy_kind, buy_kind_cn=CN.get(buy_kind, ""), buy_date=buy_date, buy_row=buy_row,
        sel_zs=sel_zs, zg_line=zg_line, zs_zd=(round(sel_zs[2], 2) if sel_zs else None),
        zs_zg=(round(sel_zs[3], 2) if sel_zs else None),
        latest=float(close[-1]), chg=float(close[-1] / close[-2] - 1) * 100,
        ma_diff=float(r["MA10MA20差pct"]), streak=int(r["末端连涨根数"]),
        dist_zg=(float(close[-1] / zg_line - 1) * 100 if zg_line else None),
        above40=above40, ma_gt=gt, ma_pass=ma_pass, cross_ago=cross_ago, ma_at_buy=ma_at_buy,
        n=n, dates=list(datev), o=list(openv), h=list(high), l=list(low), c=list(close),
        ma10=list(ma10), ma20=list(ma20), ma40=list(ma40),
        ends=[(int(a2), b2, float(px)) for a2, b2, px in ends],
        zs=zs_rows, buys=[(b["row"], b["kind"]) for b in bs],
        sells=[(b["row"], b["kind"]) for b in bs if b["kind"].startswith("S")]))
    ms = ("站上MA40" if above40 else "") + ("&MA10>MA20" if gt else "")
    print(f"[{grp}] {lv}/{name}: 名次#{int(r['因子名次'])} MA10={ma10[-1]:.1f} MA20={ma20[-1]:.1f} MA40={ma40[-1]:.1f} "
          f"过滤={'✓' if ma_pass else '✗'}({ms or '均线未过关'}) 选中={buy_kind}@{buy_date} 中枢={sel_zs}")

pickle.dump(panels, open(f"{RAW}/_panels.pkl", "wb"))
json.dump([{k: v for k, v in p.items() if k in ("层级", "名称", "因子名次", "grp", "tag", "buy_kind", "buy_date",
                                                  "ma_pass", "above40", "ma_gt")}
           for p in panels], open(f"{RAW}/_panels_index.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n[saved]", f"{RAW}/_panels.pkl", "panels=", len(panels))
print("组别计数:", pd.Series([p["grp"] for p in panels]).value_counts().to_dict())