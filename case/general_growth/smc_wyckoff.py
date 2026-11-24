# -*- coding: utf-8 -*-
"""SMC（聪明钱）与威科夫结构识别：日线 OHLCV -> 可标注结构清单

口径（沿用此前 SMC 工作术语：摆动点 / BOS / CHoCH / OB / 流动性扫取 / MSS；威科夫：区间 / Spring / Upthrust / Test / SOS / SOW）
输入：DataFrame，列 open/high/low/close/vol，按日期升序
输出：结构与事件列表，每条含 类型(中文)、关键价位、起止索引、说明
"""
import numpy as np

K_SWING = 2          # 摆动点分形半径
RANGE_WIN = 60       # 威科夫交易区间回看根数
RANGE_MIN = 40       # 区间最小根数
MAX_PER_TYPE = 2     # 每类最多保留最近条数（避免图面拥挤）
MAX_SMC = 4          # 每张子图最多标注的 SMC 结构条数
MAX_WY = 3           # 每张子图最多标注的威科夫结构条数
SHORT = {"CHoCH(结构破坏)": "CHoCH", "BOS(结构延续)": "BOS", "OB(订单块)": "OB",
         "流动性扫取(扫低点)": "扫低", "流动性扫取(扫高点)": "扫高",
         "FVG(看涨不平衡)": "FVG↑", "FVG(看跌不平衡)": "FVG↓",
         "Spring(假跌破)": "Spring", "Upthrust(假突破)": "UT",
         "Test(测试)": "Test", "SOS(强势信号)": "SOS", "SOW(弱势信号)": "SOW"}


def swings(df, k=K_SWING):
    hi, lo = df["high"].values, df["low"].values
    n = len(df)
    ph, pl = [], []
    for i in range(k, n - k):
        if hi[i] == max(hi[i - k:i + k + 1]):
            ph.append(i)
        if lo[i] == min(lo[i - k:i + k + 1]):
            pl.append(i)
    return ph, pl


def structures(df):
    """返回 dict：smc(list) / wyckoff(list)"""
    close = df["close"].values
    hi, lo = df["high"].values, df["low"].values
    vol = df["vol"].values if "vol" in df else np.ones(len(df))
    n = len(df)
    ph, pl = swings(df)
    out_smc, out_wk = [], []

    # ---- SMC：BOS / CHoCH（摆动点序列上的结构破坏） ----
    pts = sorted([(i, "H") for i in ph] + [(i, "L") for i in pl])
    last_h = last_l = None
    trend = 0  # 1 上  -1 下  0 未定
    for i, kind in pts:
        if kind == "H":
            if last_h is not None and close[i] > hi[last_h]:
                t = "BOS(结构延续)" if trend == 1 else "CHoCH(结构破坏)"
                out_smc.append({"type": t, "kind": "bull", "x": i, "y": hi[i],
                                "note": "%s前摆动高点 %.2f" % ("收盘突破" if trend == 1 else "向上破坏", hi[last_h])})
                trend = 1
            last_h = i
        else:
            if last_l is not None and close[i] < lo[last_l]:
                t = "BOS(结构延续)" if trend == -1 else "CHoCH(结构破坏)"
                out_smc.append({"type": t, "kind": "bear", "x": i, "y": lo[i],
                                "note": "%s前摆动低点 %.2f" % ("收盘跌破" if trend == -1 else "向下破坏", lo[last_l])})
                trend = -1
            last_l = i

    # ---- SMC：流动性扫取（假突破后收回） ----
    for k, i in enumerate(pl):
        prev = [x for x in pl if x < i]
        if prev and lo[i] < lo[prev[-1]] and close[i] > lo[prev[-1]]:
            out_smc.append({"type": "流动性扫取(扫低点)", "kind": "bull", "x": i, "y": lo[i],
                            "xs": i, "xe": i, "note": "跌破前低 %.2f 后收回" % lo[prev[-1]]})
    for k, i in enumerate(ph):
        prev = [x for x in ph if x < i]
        if prev and hi[i] > hi[prev[-1]] and close[i] < hi[prev[-1]]:
            out_smc.append({"type": "流动性扫取(扫高点)", "kind": "bear", "x": i, "y": hi[i],
                            "xs": i, "xe": i, "note": "突破前高 %.2f 后收回" % hi[prev[-1]]})

    # ---- SMC：OB 订单块（结构破坏前最后一根反向K线实体） ----
    for s in list(out_smc):
        if "CHoCH" not in s["type"]:
            continue
        j = s["x"]
        want_bull = s["kind"] == "bull"
        for k in range(j, max(0, j - 12), -1):
            bear = close[k] < df["open"].values[k]
            if (want_bull and bear) or ((not want_bull) and not bear):
                y0, y1 = min(df["open"].values[k], close[k]), max(df["open"].values[k], close[k])
                out_smc.append({"type": "OB(订单块)", "kind": s["kind"], "x": k, "y": (y0 + y1) / 2,
                                "xs": k, "xe": min(j, k + 8), "lo": y0, "up": y1,
                                "note": "结构破坏前的反向K线实体 %.2f~%.2f" % (y0, y1)})
                break

    # ---- SMC：FVG 不平衡（三根K线缺口） ----
    for i in range(2, n):
        if hi[i - 2] < lo[i]:
            out_smc.append({"type": "FVG(看涨不平衡)", "kind": "bull", "x": i - 1, "y": (hi[i - 2] + lo[i]) / 2,
                            "xs": i - 2, "xe": i, "lo": hi[i - 2], "up": lo[i],
                            "note": "缺口区间 %.2f~%.2f" % (hi[i - 2], lo[i])})
        if lo[i - 2] > hi[i]:
            out_smc.append({"type": "FVG(看跌不平衡)", "kind": "bear", "x": i - 1, "y": (lo[i - 2] + hi[i]) / 2,
                            "xs": i - 2, "xe": i, "lo": hi[i], "up": lo[i - 2],
                            "note": "缺口区间 %.2f~%.2f" % (hi[i], lo[i - 2])})

    # ---- 威科夫：交易区间 + Spring / Upthrust / Test / SOS / SOW ----
    for start in range(RANGE_MIN, n, max(10, RANGE_WIN // 3)):
        seg = slice(max(0, start - RANGE_WIN), start)
        rlo, rhi = float(lo[seg].min()), float(hi[seg].max())
        j = start
        if j >= n:
            break
        if lo[j] < rlo and close[j] > rlo:
            out_wk.append({"type": "Spring(假跌破)", "kind": "bull", "x": j, "y": lo[j],
                           "xs": max(0, start - RANGE_WIN), "xe": start, "lo": rlo, "up": rhi,
                           "note": "跌破区间下沿 %.2f 后收回（吸筹结束特征）" % rlo})
        elif hi[j] > rhi and close[j] < rhi:
            out_wk.append({"type": "Upthrust(假突破)", "kind": "bear", "x": j, "y": hi[j],
                           "xs": max(0, start - RANGE_WIN), "xe": start, "lo": rlo, "up": rhi,
                           "note": "突破区间上沿 %.2f 后回落（派发特征）" % rhi})
    # Test：Spring 后 3-8 根内回踩前低（缩量）
    for ev in [x for x in out_wk if "Spring" in x["type"]]:
        for j in range(ev["x"] + 1, min(n, ev["x"] + 9)):
            if abs(lo[j] - lo[ev["x"]]) / max(1e-9, lo[ev["x"]]) < 0.01 and vol[j] < vol[ev["x"]]:
                out_wk.append({"type": "Test(测试)", "kind": "bull", "x": j, "y": lo[j],
                               "note": "缩量回踩 Spring 低点 %.2f" % lo[ev["x"]]})
                break
    # SOS / SOW：放量突破/跌破近 20 根极值
    vmean = float(np.mean(vol[-120:])) if n > 120 else float(np.mean(vol))
    for i, kind in pts:
        if i <= 20:
            continue
        if kind == "H" and hi[i] > hi[i - 20:i].max() and vol[i] > 1.5 * vmean:
            out_wk.append({"type": "SOS(强势信号)", "kind": "bull", "x": i, "y": hi[i],
                           "note": "放量突破近 20 根高点"})
        if kind == "L" and lo[i] < lo[i - 20:i].min() and vol[i] > 1.5 * vmean:
            out_wk.append({"type": "SOW(弱势信号)", "kind": "bear", "x": i, "y": lo[i],
                           "note": "放量跌破近 20 根低点"})

    def trim(lst):
        """同类最多保留最近 MAX_PER_TYPE 条；合计超过 MAX_TOTAL 条时只留最近的"""
        by = {}
        for x in lst:
            by.setdefault(x["type"], []).append(x)
        res = []
        for k, v in by.items():
            res += sorted(v, key=lambda z: z["x"])[-MAX_PER_TYPE:]
        res.sort(key=lambda z: z["x"])
        return sorted(res, key=lambda z: z["x"])

    return {"smc": trim(out_smc)[-MAX_SMC:], "wyckoff": trim(out_wk)[-MAX_WY:]}


if __name__ == "__main__":
    import pandas as pd, os, json, sys
    task = sys.argv[1] if len(sys.argv) > 1 else "."
    cfg = json.load(open(os.path.join(task, "config.json"), encoding="utf-8"))
    from collections import Counter
    tot = Counter()
    rows = []
    for d in cfg["directions"]:
        for s in d["stocks"]:
            p = os.path.join(task, "data", "ohlcv", "%s.csv" % s["code"].replace(".", "_"))
            if not os.path.exists(p):
                continue
            df = pd.read_csv(p).dropna(subset=["close"]).reset_index(drop=True)
            r = structures(df)
            c = Counter(x["type"] for x in r["smc"]) + Counter(x["type"] for x in r["wyckoff"])
            tot.update(c)
            rows.append((s["name"], len(df), len(r["smc"]), len(r["wyckoff"]), dict(c)))
    print("股票数", len(rows))
    for x in rows[:6]:
        print("  %-6s 根数%4d SMC%2d 威科夫%2d %s" % (x[0], x[1], x[2], x[3], x[4]))
    print("\n全部标注类型累计：")
    for k, v in tot.most_common():
        print("  %-18s %d" % (k, v))
