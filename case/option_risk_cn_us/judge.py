# -*- coding: utf-8 -*-
"""按【付鹏缩圈框架·A股五轨期权风险监控终版】判灯 + 四项释放核对
   上篇 A股五轨, 下篇 美股三轨(标普/纳指/罗素2000)
   输出 data/final.json"""
import os, json, math

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
DEFAULT_ASOF = "20261021"
GRID_A = {"300ETF": 0.10, "500ETF": 0.25, "KC50": 0.05, "CYB": 0.05, "ZZ1000": 100.0}
GRID_U = {"SPX": 5.0, "NDX": 5.0, "RUT": 5.0}

import pandas as pd

A = json.load(open(os.path.join(D, "ashare_final.json"), encoding="utf-8"))
U = json.load(open(os.path.join(D, "us_final.json"), encoding="utf-8"))
ASOF = str(A.get("asof") or DEFAULT_ASOF)   # 报告口径日按 A股数据源动态取值

# ---------- A股 QVIX 近60交易日(画图) ----------
QV = {"300ETF": "qvix_300etf.json", "500ETF": "qvix_500etf.json",
      "KC50": "qvix_kcb.json", "CYB": "qvix_cyb.json"}
spark_a, spark_a_date = {}, {}
for k, f in QV.items():
    q = pd.read_json(os.path.join(D, f), orient="records")
    q["d"] = pd.to_datetime(q["date"], unit="ms", errors="coerce")
    q = q.dropna(subset=["d", "close"]).sort_values("d").tail(60)
    spark_a[k] = [round(float(x), 2) for x in q["close"]]
    spark_a_date[k] = [d.strftime("%m-%d") for d in q["d"]]
cm = pd.read_json(os.path.join(D, "ashare_chain_metrics.json"), orient="records")
cm["date"] = cm["date"].astype(str)
q1000 = cm[cm.target == "ZZ1000"].sort_values("date").tail(60)
spark_a["ZZ1000"] = [round(float(x) * 100, 2) for x in q1000["iv_near"]]
spark_a_date["ZZ1000"] = [d[4:6] + "-" + d[6:] for d in q1000["date"]]

# ---------- 美股三指数近60收盘(画图) ----------
spark_u, spark_u_date = {}, {}
for k, f in [("SPX", "us_VIX.json"), ("NDX", "us_VXN.json"), ("RUT", "us_RVX.json")]:
    v = pd.read_json(os.path.join(D, f), orient="records")
    v["d"] = pd.to_datetime(v["DATE"], format="%m/%d/%Y")
    v = v.sort_values("d").tail(60)
    spark_u[k] = [round(float(x), 2) for x in v["CLOSE"]]
    spark_u_date[k] = [d.strftime("%m-%d") for d in v["d"]]


def pct_str(x):
    return "待更新" if x is None else "%.1f%%" % x


def flip_state(spot, flip, grid_pct):
    """三态: 未破 / 疑似(距线不足一个行权价档) / 确认收破"""
    if spot is None or flip is None or grid_pct is None:
        return "待更新"
    if spot >= flip:
        return "未破"
    return "确认收破" if (flip - spot) / spot * 100 > grid_pct else "疑似收破"


A_ORDER = ["300ETF", "500ETF", "KC50", "CYB", "ZZ1000"]

# ================= 期权波动率预警（独立一维，与「风险释放」判灯并列） =================
# 设计口径（每标的以自身 252 日基线独立判级，不跨标比绝对值）：
#   🔴 红灯（任一命中）：
#       ① 自身 252 日分位 ≥ 90%
#       ② 绝对水位（仅科创50/创业板/中证1000 设；300ETF/500ETF 无此项）
#       ③ 隐历差 IV−HV20 ≥ +8pct
#   🟡 黄灯（红灯未命中时任一命中）：
#       ① 自身 252 日分位 ≥ 70%（科创50/创业板 用 75%）
#       ② 隐历差 IV−HV20 ≥ +5pct
#       ③ R ≥ 1.00（近月/次月隐波近端倒挂）
#   🟢 绿灯：其余
VOL_RULES = {
    "A": {
        "idx_name": "VIX",
        "pct_red": 90.0,
        "pct_yellow": 70.0,
        "pct_yellow_override": {"KC50": 75.0, "CYB": 75.0},
        "abs_red": {"KC50": (42.0, "VIX"), "CYB": (34.0, "VIX"), "ZZ1000": (28.0, "ATM-IV")},
        "ivhv_red": 8.0,
        "ivhv_yellow": 5.0,
        "R_yellow": 1.00,
    },
    "U": {
        "idx_name": None,          # 用各标的自己的 idx（VIX/VXN/RVX）
        "pct_red": 90.0,
        "pct_yellow": 70.0,
        "pct_yellow_override": {},
        "abs_red": {},
        "ivhv_red": 8.0,
        "ivhv_yellow": 5.0,
        "R_yellow": 1.00,
    },
}


def vol_alert(k, v, kind="A"):
    """单标的期权波动率预警：返回 dict(level, red_hits, yellow_hits, why, vix, vix_pct, ivhv, R)"""
    r = VOL_RULES[kind]
    if kind == "A":
        vix, pct, idx_name = v.get("vix"), v.get("vix_pct"), r["idx_name"]
    else:
        vix, pct, idx_name = v.get("idx_last"), v.get("idx_pct"), (v.get("idx") or "波动率指数")
    ivhv, R = v.get("ivhv"), v.get("R")

    red, yel = [], []
    if pct is not None and pct >= r["pct_red"]:
        red.append("%s 252日分位 %.1f%% ≥ %.0f%%" % (idx_name, pct, r["pct_red"]))
    if k in r["abs_red"]:
        thr, unit = r["abs_red"][k]
        if vix is not None and vix > thr:
            red.append("%s %.2f > %.0f（绝对水位）" % (("ATM-IV" if unit == "ATM-IV" else idx_name), vix, thr))
    if ivhv is not None and ivhv >= r["ivhv_red"]:
        red.append("隐历差 IV−HV20 %+.2fpct ≥ +%.0fpct" % (ivhv, r["ivhv_red"]))
    if red:
        return dict(level="🔴", red_hits=red, yellow_hits=[], why="；".join(red),
                    vix=vix, vix_pct=pct, ivhv=ivhv, R=R)

    ythr = r["pct_yellow_override"].get(k, r["pct_yellow"])
    if pct is not None and pct >= ythr:
        yel.append("%s 252日分位 %.1f%% ≥ %.0f%%" % (idx_name, pct, ythr))
    if ivhv is not None and ivhv >= r["ivhv_yellow"]:
        yel.append("隐历差 IV−HV20 %+.2fpct ≥ +%.0fpct" % (ivhv, r["ivhv_yellow"]))
    if R is not None and R >= r["R_yellow"]:
        yel.append("R %.3f ≥ %.2f（近月/次月隐波近端倒挂）" % (R, r["R_yellow"]))
    if yel:
        return dict(level="🟡", red_hits=[], yellow_hits=yel, why="；".join(yel),
                    vix=vix, vix_pct=pct, ivhv=ivhv, R=R)
    return dict(level="🟢", red_hits=[], yellow_hits=[], why="未触发波动率预警阈值",
                vix=vix, vix_pct=pct, ivhv=ivhv, R=R)


def vol_summary(reg):
    """把一个区域的 vol 结果汇总成 前置提示（红灯在前，含全部红灯及原因）"""
    red = [k for k in reg if reg[k]["vol"]["level"] == "🔴"]
    yel = [k for k in reg if reg[k]["vol"]["level"] == "🟡"]
    grn = [k for k in reg if reg[k]["vol"]["level"] == "🟢"]
    detail = [dict(key=k, label=reg[k]["label"], why=reg[k]["vol"]["why"]) for k in red]
    return dict(n_red=len(red), n_yellow=len(yel), n_green=len(grn),
                red=red, yellow=yel, green=grn,
                red_labels=[reg[k]["label"] for k in red],
                yellow_labels=[reg[k]["label"] for k in yel],
                detail=detail)


# ================= A股 =================
def judge_ashare():
    res = {}
    for k, v in A["targets"].items():
        gp = GRID_A[k] / v["spot"] * 100
        fs = flip_state(v["spot"], v["flip"], gp)
        v["grid_pct"] = round(gp, 2)
        v["flip_state"] = fs
        # Gamma Flip 线未形成时（净Gamma无由负转正的行权价）flip/flip_dist 为空：
        # 断言一律视为不触发，展示为「Flip线未形成，待更新」，避免 None 参与比较/格式化
        _fd = v["flip_dist"] if isinstance(v["flip_dist"], (int, float)) else None
        _fd_ok = _fd is not None

        baseline = [
            ("R≤0.98", v["R"] <= 0.98, "R=%.3f" % v["R"]),
            ("SKEW（25Δ风险逆转）在自身252日中位数下方",
             v["rr"] < v["rr_med"], "rr=%+.4f / 中位%+.4f" % (v["rr"], v["rr_med"])),
            ("VIX分位<65%", v["vix_pct"] < 65, "分位%.1f%%" % v["vix_pct"]),
            ("隐历差<+5pct", v["ivhv"] < 5, "IV-HV20=%+.2fpct" % v["ivhv"]),
            ("|Flip距离|>2.2%", (_fd_ok and _fd > 2.2),
             "距线%.2f%%（档距%.2f%%）" % (_fd, gp) if _fd_ok else "Flip线未形成，待更新"),
        ]
        nb = sum(1 for _, ok, _ in baseline if ok)

        lv1_thr = 75 if k in ("KC50", "CYB") else 70
        lv1 = [
            ("R连续3日>1.00（近端倒挂确认）", bool(v["R_up3"]),
             "近4日 " + " / ".join("%.3f" % x for x in v["R_prev"])),
            ("VIX分位>%d%%（%s按自身基线）" % (lv1_thr, "创业板/科创" if k in ("KC50", "CYB") else "常规"),
             v["vix_pct"] > lv1_thr, "分位%.1f%%" % v["vix_pct"]),
            ("SKEW突破自身中位数+1σ", bool(v["rr_break"]),
             "rr=%+.4f / 中位+1σ=%+.4f" % (v["rr"], v["rr_med"] + v["rr_sd"])),
            ("持仓PCR连续3日上行（排除极端乐观回流）", bool(v["pcr_up3"]),
             "近3日 " + "→".join("%.3f" % x for x in v["pcr3"])),
        ]
        n1 = sum(1 for _, ok, _ in lv1 if ok)

        lv2 = []
        if k == "KC50":
            lv2.append(("科创50 VIX重上40%+ 且隐历差不收敛", v["vix"] > 40 and v["ivhv"] >= 5,
                        "VIX=%.2f, 隐历差=%+.2fpct" % (v["vix"], v["ivhv"])))
            lv2.append(("科创50 VIX分位>80%", v["vix_pct"] > 80, "分位%.1f%%" % v["vix_pct"]))
        if k == "CYB":
            lv2.append(("创业板VIX分位>85%", v["vix_pct"] > 85, "分位%.1f%%" % v["vix_pct"]))
        if k == "500ETF":
            lv2.append(("500 VIX分位>85%", v["vix_pct"] > 85, "分位%.1f%%" % v["vix_pct"]))
        if k == "ZZ1000":
            zf = v["im"]["z_far"]
            lv2.append(("1000 R>1.05 且 IM远月贴水再走阔1σ",
                        v["R"] > 1.05 and zf < -1,
                        "R=%.3f, 远月基差Z=%+.2f" % (v["R"], zf)))
        lv2.append(("GEX代理分位跌进20%-40%区间", 20 <= v["gex_pct"] <= 40,
                    "分位%.1f%%" % v["gex_pct"]))
        n2 = sum(1 for _, ok, _ in lv2 if ok)

        red = []
        if k == "ZZ1000":
            red.append(("IM远月贴水再走阔1σ", v["im"]["z_far"] < -1,
                        "远月基差年化 %.2f%%，Z=%+.2f" % (v["im"]["b3"], v["im"]["z_far"])))
        red.append(("R>1.05持续5日", bool(v["R_gt105_5"]), "近4日最高%.3f" % max(v["R_prev"])))
        if k == "KC50":
            red.append(("科创50 VIX>42%", v["vix"] > 42, "现%.2f" % v["vix"]))
        if k == "CYB":
            red.append(("创业板VIX>34% 且虚值put IV甩开call",
                        v["vix"] > 34 and v["rr"] > 0,
                        "VIX=%.2f / 25Δrr=%+.4f" % (v["vix"], v["rr"])))
        if k == "500ETF":
            red.append(("500 VIX分位>90%", v["vix_pct"] > 90, "分位%.1f%%" % v["vix_pct"]))
        if k == "ZZ1000":
            red.append(("1000 ATM-IV冲28%+", v["vix"] > 28, "现%.2f" % v["vix"]))
        red.append(("GEX代理分位<20%", v["gex_pct"] < 20, "分位%.1f%%" % v["gex_pct"]))
        red.append(("现价收破Gamma Flip线", fs == "确认收破",
                    ("现价%.3f / Flip %.3f，距线%.2f%%（档距%.2f%%）→ %s"
                     % (v["spot"], v["flip"], _fd, gp, fs)) if _fd_ok
                    else "Flip线未形成，待更新"))
        # 疑似收破不作为红灯依据, 但单独标注
        susp = [lab for lab, ok, _ in red if lab.startswith("现价收破") and fs == "疑似收破"]
        nr = sum(1 for _, ok, _ in red if ok)

        if nb == 5:
            light, why = "🟢", "五项安全基线全满足"
        elif n1 >= 2 and nr >= 1:
            light, why = "🔴", "Lv1命中%d项 + 红灯条件%d项触发（%s）" % (
                n1, nr, " / ".join(lab for lab, ok, _ in red if ok))
        elif n1 >= 2 and n2 >= 1:
            light, why = "🟡Lv2", "Lv1命中%d项 + Lv2条件%d项触发" % (n1, n2)
        elif n1 >= 2:
            light, why = "🟡Lv1", "Lv1命中%d项：%s" % (n1, " / ".join(lab for lab, ok, _ in lv1 if ok))
        else:
            light, why = "🟡观察", "基线%d/5未全满足，Lv1仅%d项未达升级阈值（需≥2项）" % (nb, n1)

        rel = []
        r3 = v["R_prev"][-3:]
        rel.append(("R重回≤0.97连续3日", all(x <= 0.97 for x in r3),
                    "当日R=%.3f，近3日 %s" % (v["R"], " / ".join("%.3f" % x for x in r3))))
        rel.append(("SKEW（25Δrr）回踩至自身中位数下方", v["rr"] < v["rr_med"],
                    "现%+.4f / 中位%+.4f" % (v["rr"], v["rr_med"])))
        rel.append(("VIX分位退出>%d%%区间" % lv1_thr, v["vix_pct"] <= lv1_thr,
                    "现分位%.1f%%" % v["vix_pct"]))
        rel.append(("GEX分位回升>42% 且|Flip距离|>2.0%",
                    v["gex_pct"] > 42 and _fd_ok and _fd > 2.0,
                    "GEX分位%.1f%% / %s" % (v["gex_pct"],
                                           ("距线%.2f%%" % _fd) if _fd_ok else "Flip线未形成")))
        nrel = sum(1 for _, ok, _ in rel if ok)

        # ---- 当前卡在哪 ----
        stuck = "；".join("%s（%s）" % (lab, ev) for lab, ok, ev in rel if not ok) or "—"
        hitred = [lab for lab, ok, _ in red if ok]
        if hitred:
            stuck = "已触发红灯条件：" + " / ".join(hitred) + " ｜ 释放未过项：" + stuck
        if susp:
            stuck += " ｜ 另有 现价与Flip线方向不可分辨（距线%.2f%% < 一个行权价档%.2f%%），待确认" % (
                v["flip_dist"], gp)

        # ---- 下次最先翻色观察项（tier 越小越优先，tier 内按距离排序）----
        cand = []
        if fs == "疑似收破":
            cand.append((0, 0.5, "现价（%.3f）与 Gamma Flip 线（%.3f）方向确认：站回线上=负Gamma解除"
                         % (v["spot"], v["flip"])))
        if v["gex_pct"] < 20:
            cand.append((1, (20 - v["gex_pct"]) / 20,
                         "GEX代理分位（%.1f%%）能否回到20%%以上（负Gamma解除）" % v["gex_pct"]))
        if fs == "确认收破":
            cand.append((1, 0.6, "现价（%.3f）能否收复 Gamma Flip 线（%.3f）" % (v["spot"], v["flip"])))
        if k == "CYB" and v["vix"] > 34 and v["rr"] > 0:
            cand.append((1, (v["vix"] - 34) / 34,
                         "创业板VIX（%.2f）能否回落34%%下方且25Δrr（%+.4f）转负" % (v["vix"], v["rr"])))
        if k == "ZZ1000" and v["vix"] > 28:
            cand.append((1, (v["vix"] - 28) / 28, "1000 ATM-IV（%.2f%%）能否回落至28%%以下" % v["vix"]))
        lv1c = []
        lv1c.append((9.9 if v["R"] >= 1.00 else 1.00 - v["R"],
                     "R（%.3f）若站上1.00并连续3日则命中Lv1" % v["R"]))
        lv1c.append((9.9 if v["vix_pct"] > lv1_thr else (lv1_thr - v["vix_pct"]) / lv1_thr,
                     "VIX分位（%.1f%%）若升破%d%%则命中Lv1" % (v["vix_pct"], lv1_thr)))
        brk = v["rr_med"] + v["rr_sd"]
        lv1c.append((9.9 if v["rr"] >= brk else (brk - v["rr"]) / max(v["rr_sd"], 1e-9) / 3,
                     "25Δrr（%+.4f）距中位+1σ（%+.4f）还差%.2fσ即命中Lv1"
                     % (v["rr"], brk, max(0, (brk - v["rr"]) / max(v["rr_sd"], 1e-9)))))
        lv1c.append((0.5, "持仓PCR（%.3f）若连续3日上行则命中Lv1" % v["pcr"]))
        lv1c.sort(key=lambda x: x[0])
        cand.append((2, lv1c[0][0], lv1c[0][1]))
        if v["gex_pct"] > 20:
            cand.append((3, (42 - v["gex_pct"]) / 42, "GEX代理分位（%.1f%%）回升破42%%" % v["gex_pct"]))
        need = 3 - sum(1 for x in r3 if x <= 0.97)
        if need:
            cand.append((3, 0.03 * need, "R（%.3f）还差%d个交易日凑满连续3日≤0.97" % (v["R"], need)))
        cand.sort(key=lambda x: (x[0], x[1]))
        nxt = "；".join(t for _, _, t in cand[:2]) if cand else "—"

        res[k] = dict(key=k, label=v["label"], note=v["note"], vals=v,
                      baseline=baseline, nb=nb, lv1=lv1, n1=n1, lv2=lv2, n2=n2,
                      red=red, nr=nr, suspect=susp, light=light, light_why=why,
                      rel=rel, nrel=nrel, stuck=stuck, next_watch=nxt,
                      vol=vol_alert(k, v, "A"),
                      spark=spark_a.get(k, []), spark_date=spark_a_date.get(k, []))
    return res


# ================= 美股 =================
def judge_us():
    res = {}
    for k, v in U["targets"].items():
        gp = GRID_U[k] / v["chain_spot"] * 100
        fs = flip_state(v["chain_spot"], v["flip"], gp)
        v["grid_pct"] = round(gp, 2)
        v["flip_state"] = fs
        _ufd = v["flip_dist"] if isinstance(v["flip_dist"], (int, float)) else None
        _ufd_ok = _ufd is not None
        sk = v.get("skew")

        baseline = [
            ("R≤0.98", v["R"] is not None and v["R"] <= 0.98, "R=%.3f（链内近/次月IV）" % v["R"]),
            ("SKEW在自身252日中位数下方",
             (sk["val"] < sk["med"]) if sk else None,
             ("CBOE SKEW=%.2f / 中位%.2f" % (sk["val"], sk["med"])) if sk
             else "待更新（CBOE 未单独发布该指数偏度，仅链内25Δrr=%.2f 可参照）" % v["rr"]),
            ("VIX分位<65%", v["idx_pct"] < 65, "%s分位%.1f%%" % (v["idx"], v["idx_pct"])),
            ("隐历差<+5pct", v["ivhv"] < 5,
             "IV(%s)-HV20=%+.2fpct" % (v["idx"], v["ivhv"])),
            ("|Flip距离|>2.2%", (_ufd_ok and _ufd > 2.2),
             "距线%.2f%%（档距%.2f%%）" % (_ufd, gp) if _ufd_ok else "Flip线未形成，待更新"),
        ]
        nb = sum(1 for _, ok, _ in baseline if ok is True)
        nunk = sum(1 for _, ok, _ in baseline if ok is None)

        lv1 = [
            ("R连续3日>1.00（近端倒挂确认）", None, "待更新（缺链历史，仅当日R=%.3f）" % v["R"]),
            ("VIX分位>70%", v["idx_pct"] > 70, "%s分位%.1f%%" % (v["idx"], v["idx_pct"])),
            ("SKEW突破自身中位数+1σ",
             (sk["val"] > sk["med"] + sk["sd"]) if sk else None,
             ("中位+1σ=%.2f，现%.2f" % (sk["med"] + sk["sd"], sk["val"])) if sk
             else "待更新（缺该指数SKEW序列）"),
            ("持仓PCR连续3日上行", None, "待更新（缺链历史，当日持仓PCR=%.3f）" % v["pcr"]),
        ]
        n1 = sum(1 for _, ok, _ in lv1 if ok is True)

        lv2 = [("GEX分位跌进20%-40%区间", None, "待更新（缺链历史，仅当日gex_norm=%.2e）" % v["gex_norm"])]
        red = [
            ("GEX代理分位<20%", None, "待更新（缺链历史）"),
            ("现价收破Gamma Flip线", fs == "确认收破",
             ("现价%.2f / Flip %.2f，距线%.2f%%（档距%.2f%%）→ %s"
              % (v["chain_spot"], v["flip"], _ufd, gp, fs)) if _ufd_ok
             else "Flip线未形成，待更新"),
        ]
        nr = sum(1 for _, ok, _ in red if ok)
        susp = ["现价收破Gamma Flip线"] if fs == "疑似收破" else []

        if nb == 5:
            light, why = "🟢", "五项安全基线全满足"
        elif n1 >= 2 and nr >= 1:
            light, why = "🔴", "Lv1命中%d项 + 红灯条件触发" % n1
        elif n1 >= 2 and any(ok is True for _, ok, _ in lv2):
            light, why = "🟡Lv2", "Lv1命中%d项 + Lv2条件触发" % n1
        elif n1 >= 2:
            light, why = "🟡Lv1", "Lv1命中%d项：%s" % (n1, " / ".join(l for l, o, _ in lv1 if o))
        elif n1 == 1:
            light, why = ("🟡观察", "基线%d/5（另%d项待更新），Lv1命中1项：%s（需≥2项才升级）"
                          % (nb, nunk, " / ".join(l for l, o, _ in lv1 if o)))
        else:
            light, why = ("🟡观察", "基线%d/5（另%d项待更新），Lv1未命中" % (nb, nunk))

        rel = [
            ("R重回≤0.97连续3日", None, "待更新（缺链历史，当日R=%.3f）" % v["R"]),
            ("SKEW回踩至自身中位数下方", (sk["val"] < sk["med"]) if sk else None,
             ("现%.2f / 中位%.2f" % (sk["val"], sk["med"])) if sk else "待更新（缺该指数SKEW序列）"),
            ("VIX分位退出>70%区间", v["idx_pct"] <= 70, "现分位%.1f%%" % v["idx_pct"]),
            ("GEX分位回升>42% 且|Flip距离|>2.0%", None,
             "待更新（缺链历史）；当日距线%s" % (("%.2f%%" % _ufd) if _ufd_ok else "未形成")),
        ]
        nrel = sum(1 for _, ok, _ in rel if ok is True)

        stuck = "；".join("%s（%s）" % (lab, ev) for lab, ok, ev in rel if ok is False)
        unk = [lab for lab, ok, _ in rel if ok is None]
        if unk:
            stuck = ("待更新项：" + " / ".join(unk) + (" ｜ " if stuck else "") + stuck) if stuck else \
                    "待更新项：" + " / ".join(unk)
        hitred = [lab for lab, ok, _ in red if ok]
        if hitred:
            stuck = "已触发红灯条件：" + " / ".join(hitred) + " ｜ " + stuck
        if susp:
            stuck += " ｜ 现价与Flip线方向不可分辨（距线%.2f%% < 一个行权价档%.2f%%），待确认" % (
                v["flip_dist"], gp)

        cand = []
        if fs == "疑似收破":
            cand.append((0, 0.5, "现价（%.2f）与 Gamma Flip 线（%.2f）方向确认：站回线上=负Gamma解除"
                         % (v["chain_spot"], v["flip"])))
        lv1c = []
        lv1c.append((9.9 if v["R"] >= 1.00 else 1.00 - v["R"],
                     "R（%.3f，链内近/次月IV）若站上1.00并连续3日则命中Lv1（需链历史）" % v["R"]))
        lv1c.append((9.9 if v["idx_pct"] > 70 else (70 - v["idx_pct"]) / 70,
                     "%s分位（%.1f%%）若升破70%%则命中Lv1" % (v["idx"], v["idx_pct"])))
        if sk:
            brk = sk["med"] + sk["sd"]
            lv1c.append((9.9 if sk["val"] >= brk else (brk - sk["val"]) / max(sk["sd"], 1e-9) / 3,
                         "CBOE SKEW（%.2f）已高于中位+1σ（%.2f）" % (sk["val"], brk) if sk["val"] >= brk
                         else "CBOE SKEW（%.2f）距中位+1σ（%.2f）还差%.2fσ即命中Lv1"
                         % (sk["val"], brk, (brk - sk["val"]) / max(sk["sd"], 1e-9))))
        lv1c.append((0.5, "持仓PCR（%.3f）是否连续3日上行（需链历史）" % v["pcr"]))
        lv1c.sort(key=lambda x: x[0])
        cand.append((2, lv1c[0][0], lv1c[0][1]))
        if sk and sk["val"] > sk["med"]:
            cand.append((3, (sk["val"] - sk["med"]) / max(sk["sd"], 1e-9) / 3,
                         "CBOE SKEW（%.2f，高于中位%.2f 达%.2fσ）能否回踩中位数"
                         % (sk["val"], sk["med"], (sk["val"] - sk["med"]) / max(sk["sd"], 1e-9))))
        if v["idx_pct"] > 70:
            cand.append((3, (v["idx_pct"] - 70) / 70,
                         "%s分位（%.1f%%）能否回落至70%%下方" % (v["idx"], v["idx_pct"])))
        cand.sort(key=lambda x: (x[0], x[1]))
        nxt = "；".join(t for _, _, t in cand[:2]) if cand else "—"

        res[k] = dict(key=k, name=v["name"],
                      label="%s（%s / %s）" % (v["name"], v["idx"], v["etf"]),
                      note="美股面板", vals=v, baseline=baseline, nb=nb, nunk=nunk,
                      lv1=lv1, n1=n1, lv2=lv2, red=red, nr=nr, suspect=susp,
                      light=light, light_why=why, rel=rel, nrel=nrel,
                      stuck=stuck, next_watch=nxt,
                      vol=vol_alert(k, v, "U"),
                      spark=spark_u.get(k, []), spark_date=spark_u_date.get(k, []))
    return res


def main():
    fa = judge_ashare()
    fu = judge_us()

    # ---- 期权波动率预警（前置汇总：红灯在前，含全部红灯及原因）----
    U_ORDER = ["SPX", "NDX", "RUT"]
    va = vol_summary({k: fa[k] for k in A_ORDER})
    vu = vol_summary({k: fu[k] for k in U_ORDER})

    print("#" * 68)
    for tag, vs in (("A股五轨", va), ("美股三轨", vu)):
        if vs["n_red"]:
            print("🔴 %s 波动率预警（%d 个红灯）：%s" % (tag, vs["n_red"], "、".join(vs["red_labels"])))
            for d in vs["detail"]:
                print("     · %s ｜ %s" % (d["label"], d["why"]))
        else:
            print("🟢 %s 波动率预警：无红灯（0 个红灯）" % tag)
        if vs["n_yellow"]:
            print("🟡 %s 波动率偏高（%d）：%s" % (tag, vs["n_yellow"], "、".join(vs["yellow_labels"])))
    print("#" * 68)

    # ---- 总览句 ----
    reds = [r["label"] for r in fa.values() if r["light"] == "🔴"]
    gex20 = [r["label"] for r in fa.values() if r["vals"]["gex_pct"] < 20]
    breach = [r["label"] for r in fa.values() if r["vals"]["flip_state"] == "确认收破"]
    suspA = [r["label"] for r in fa.values() if r["vals"]["flip_state"] == "疑似收破"]
    a_sum = ("五轨中%s触发红灯；%s 的GEX代理分位低于20%%（负Gamma状态）；%s已确认收破Gamma Flip线；%s现价贴线方向待确认。"
             % ("、".join(reds) if reds else "无标的",
                "、".join(gex20) if gex20 else "无标的",
                "、".join(breach) if breach else "无标的",
                "、".join(suspA) if suspA else "无标的"))
    redsU = [r["name"] for r in fu.values() if r["light"] == "🔴"]
    bU = [r["name"] for r in fu.values() if r["vals"]["flip_state"] == "确认收破"]
    sU = [r["name"] for r in fu.values() if r["vals"]["flip_state"] == "疑似收破"]
    u_sum = ("三轨均未触发红灯%s；%s；%s。美股缺期权链历史，R连续3日、持仓PCR连续3日、GEX分位与纳指/罗素偏度序列均为待更新。"
             % (("（%s）" % "、".join(redsU)) if redsU else "",
                ("%s已确认收破Flip线" % "、".join(bU)) if bU else "无标的确认收破Flip线",
                ("%s现价贴线待确认" % "、".join(sU)) if sU else "无标的贴线"))

    out = dict(asof=ASOF, window=A["window"], ashare=fa, us=fu,
               grid_a={k: v["vals"]["grid_pct"] for k, v in fa.items()},
               grid_u={k: v["vals"]["grid_pct"] for k, v in fu.items()},
               us_meta=U["snapshot"],
               vol_summary=dict(ashare=va, us=vu),
               summary=dict(ashare=a_sum, us=u_sum))
    json.dump(out, open(os.path.join(D, "final.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)

    for reg, R in [("A股", fa), ("美股", fu)]:
        print("=" * 24, reg)
        for k, r in R.items():
            print("  %-7s %-9s 基线%d/5 Lv1=%d 红灯=%d 释放%d/4  %s"
                  % (k, r["light"], r["nb"], r["n1"], r["nr"], r["nrel"], r["light_why"]))
            for coll in ("lv1", "lv2", "red"):
                for lab, ok, ev in r[coll]:
                    if ok:
                        print("        %s✓ %s ｜ %s" % (coll, lab, ev))
            print("        卡点:", r["stuck"][:200])
            print("        先翻:", r["next_watch"])


if __name__ == "__main__":
    main()