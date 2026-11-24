# -*- coding: utf-8 -*-
"""定稿版：修正背驰配对(两段同向笔) + 用户右侧确认口径，双口径(close / 极值 low-high)对照。

用户口径：一买须"已经涨起来"——MA10>MA20（金叉在即/已上穿）、或末端连续上涨≥3根、或买点后向上笔成立。
档位：
  S1 = 买点仍有效 + 当前转强(MA10>MA20 或 连涨≥3)           <- 强确认
  S2 = 买点仍有效 + 买点后走出向上笔但当前未转强(仅C3)        <- 弱确认
  S3 = 买点仍有效但右侧未确认                                <- 仅结构、未获确认
  D  = 近40日买点已失效
  C  = 无近40日买点，但当前收盘>最近中枢上沿(突破中枢)
  E  = 无
"""
import numpy as np, pandas as pd, json
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"; OUT = f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, theme_members = ns["synth"], ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc, WINDOW = ns["cf"], ns["cc"], ns["WINDOW"]


def backshift_fix(ends, hist, plo, phi, min_seg):
    """标准口径：相邻两段同向笔比较。底 i: ends[i-3]->ends[i-2] 与 ends[i-1]->ends[i]。"""
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


def buys_detail_fix(ends, hist, plo, phi, close, min_seg):
    res = []
    bs = backshift_fix(ends, hist, plo, phi, min_seg)
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
    zss = cc.detect_zhongshu(ends, min_seg)
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
                # v5 修正：三买只取「向上离开该中枢后的首次回抽」，同一中枢不再反复产出
                # （旧实现 left 复位导致同一老中枢在高位每个回抽底都判一次三买，
                #   使玻璃 09-07、AI算力与芯片 09-11 等陈旧买点被当成近期买点）
                break
    res.sort(key=lambda x: x["row"])
    return res


def judge(buy, close, low, n, datev):
    if buy is None:
        return None
    r0 = buy["row"]; ac = close[r0 + 1:]
    if buy["kind"] == "B3":
        zg = buy["zg"]; inv = bool(ac.size and zg is not None and (ac <= zg).any())
        why = (f"买点后收盘跌回中枢内(≤ZG={zg:.1f})" if inv else ""); ref = round(float(zg), 2) if zg is not None else None
    else:
        alo = float(low[buy["anchor"]]); inv = bool(ac.size and (ac < alo).any())
        why = (f"买点后收盘跌破买点低点({alo:.1f})" if inv else ""); ref = round(float(alo), 2)
    return dict(kind=buy["kind"], date=str(datev[r0]), ago=int(n - 1 - r0), px=round(float(close[r0]), 2),
                ref=ref, valid=(not inv), inv=why)


def run_case(mem, px_mode):
    dfi, nm = synth(mem)
    if dfi is None:
        return None
    df2, _ = cf.normalize_input(dfi, name="b")
    ends = cf.get_endpoints(df2, "struct")
    m = cc.macd_on(df2, 20, 40, 5)
    close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
    n = len(close); datev = df2["time"].dt.strftime("%Y-%m-%d").values
    if px_mode == "close":
        plo = phi = close
    else:
        plo, phi = low, high
    buys = buys_detail_fix(ends, m["hist"].values, plo, phi, close, cc.MIN_BI_GAP)
    rec = [b for b in buys if (n - 1 - b["row"]) <= WINDOW]
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
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
    c2 = bool(k >= 3)
    c3 = bool(rec and any(e[1] == "top" and e[0] > rec[-1]["row"] for e in ends))
    if j and j["valid"]:
        tier = "S1" if (c1 or c2) else ("S2" if c3 else "S3")
    elif j:
        tier = "D"
    elif brk:
        tier = "C"
    else:
        tier = "E"
    return dict(最新收盘=round(float(close[-1]), 2), 日期=str(datev[-1]),
                最近中枢上沿=(round(zg, 2) if zg is not None else None),
                买点类型=(j["kind"] if j else None), 买点日期=(j["date"] if j else None),
                买点距今天=(j["ago"] if j else None), 买点价位=(j["px"] if j else None),
                买点关键位=(j["ref"] if j else None), 仍有效=(j["valid"] if j else None),
                MA10=(round(float(ma10), 2) if np.isfinite(ma10) else None),
                MA20=(round(float(ma20), 2) if np.isfinite(ma20) else None),
                MA10MA20差pct=round(float(ma10 / ma20 - 1) * 100, 2),
                末端连涨根数=k, C1=c1, C2=c2, C3=c3, 档位=tier)


rk = pd.read_csv(f"{OUT}/nonneg_layer_rank.csv")
rows = []
for _, r0 in rk.iterrows():
    lv, name, rk_ = r0["层级"], r0["名称"], int(r0["名次"])
    mem = con_members.get(name, set()) if lv == "概念" else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set()))
    a = run_case(mem, "close")
    b = run_case(mem, "extreme") if len(mem) >= 5 else None
    if a is None:
        continue
    rec = dict(层级=lv, 名称=name, 因子名次=rk_)
    for kk, vv in a.items():
        rec[kk] = vv
    rec["档位_极值口径"] = (b["档位"] if b else None)
    rec["买点类型_极值口径"] = (b["买点类型"] if b else None)
    rows.append(rec)

o = pd.DataFrame(rows)
o.to_csv(f"{OUT}/chan_final_v4.csv", index=False, encoding="utf-8-sig")

print("===== 口径A：收盘价较新低/新高（与技能库 production 一致） =====")
for lv in ["行业", "主题", "概念"]:
    s = o[o["层级"] == lv]
    print(f"{lv}: n={len(s)}", {k: int((s["档位"] == k).sum()) for k in ["S1", "S2", "S3", "D", "C", "E"]})
print("合计:", {k: int((o["档位"] == k).sum()) for k in ["S1", "S2", "S3", "D", "C", "E"]})

print("\n===== 【S1】买点有效 + 当前转强（MA10>MA20 或 连涨≥3）=====")
s = o[o["档位"] == "S1"].sort_values(["层级", "因子名次"])
print(s[["层级", "名称", "因子名次", "买点类型", "买点日期", "买点距今天", "最新收盘", "MA10MA20差pct", "末端连涨根数", "C1", "C2"]].to_string(index=False))

print("\n===== 【S2】买点有效 + 买点后走出向上笔，但当前未转强 =====")
s = o[o["档位"] == "S2"].sort_values(["层级", "因子名次"])
print(s[["层级", "名称", "因子名次", "买点类型", "买点日期", "买点距今天", "最新收盘", "MA10MA20差pct", "末端连涨根数"]].to_string(index=False))

print("\n===== 【S3】买点有效但右侧未确认 =====")
s = o[o["档位"] == "S3"].sort_values(["层级", "因子名次"])
print(s[["层级", "名称", "因子名次", "买点类型", "买点日期", "买点距今天", "最新收盘", "MA10MA20差pct", "末端连涨根数"]].to_string(index=False))

print("\n===== 【C】无近40日买点，仅突破最近中枢上沿 =====")
s = o[o["档位"] == "C"].sort_values(["层级", "因子名次"])
print(s[["层级", "名称", "因子名次", "最新收盘", "最近中枢上沿", "MA10MA20差pct", "末端连涨根数"]].to_string(index=False))

print("\n===== 点名板块（收盘口径）=====")
for nm in ["国产航母", "培育钻石", "电机", "航空", "石油开采", "电信运营", "矿物制品", "银行", "路桥", "中船系", "煤炭开采"]:
    for _, r in o[o["名称"] == nm].iterrows():
        print(f"{r['层级']}/{nm}: 档位={r['档位']} 买点={r['买点类型']}@{r['买点日期']} 距今{r['买点距今天']} "
              f"最新{r['最新收盘']} MA10MA20={r['MA10MA20差pct']:+.2f}% 连涨={r['末端连涨根数']} "
              f"C1={r['C1']} C2={r['C2']} C3={r['C3']} | 极值口径={r['档位_极值口径']}/{r['买点类型_极值口径']}")

print("\n===== 两口径档位差异 =====")
diff = o[o["档位"] != o["档位_极值口径"]]
print(f"差异 {len(diff)}/{len(o)} 个")
print(diff[["层级", "名称", "因子名次", "档位", "买点类型", "买点日期", "档位_极值口径", "买点类型_极值口径", "MA10MA20差pct"]].to_string(index=False))