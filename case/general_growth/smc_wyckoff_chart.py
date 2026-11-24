# -*- coding: utf-8 -*-
"""入选个股 SMC／威科夫结构图：在价格走势上合并标注 SMC（聪明钱）与威科夫结构

沿用 chart_order.plan（买点等级排序、每图最多 max_stocks_per_chart 只）与 gen_chart 的底部对照表；
SMC 用蓝色系、威科夫用橙／紫色系，标注一律使用真实颜色与符号、不用颜色词文字代替；信息框置于坐标区下方留白带。
输出：out/<报告名>_<时点>_SMC威科夫结构版_第k张共n张.png，并把清单写入 data/smc_wyckoff_charts.json
用法: python3 smc_wyckoff_chart.py [--task-dir DIR] [--variant 标签]
"""
import argparse
import json
import math
import os
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

from chart_order import plan as build_plan, bp_rank, RANK_NAME   # noqa
from gen_chart import INK, GREEN_FILL, RED_FILL, GREEN_TEXT, RED_TEXT, draw_table  # noqa
from smc_wyckoff import structures, SHORT                     # noqa

CJK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
UP = "#D64545"
DOWN = "#2E9E63"
NCOL = 4
# SMC 蓝色系 / 威科夫 橙紫色系
C_SMC = "#1F4E79"
C_SMC_L = "#9DC3E6"
C_SMC_BAND = "#DEEBF7"
C_WK = "#C55A11"
C_WK_L = "#F4B183"
C_WK_BAND = "#FCE4D6"


def zone_span(df, x0, zlo, zup, n, maxlen=45, minw=6):
    """区带自形成延伸至价格**离开该区后再次回测**（缓解）处；未回测则延伸 maxlen 根（便于辨识）"""
    c = df["close"].values
    left = False
    for j in range(x0 + 1, min(n, x0 + maxlen)):
        inside = zlo <= c[j] <= zup
        if not inside:
            left = True
        elif left:
            return max(j, x0 + minw)
    return min(n - 1, x0 + maxlen)


def place_labels(ax, labels, fp_b, n):
    """同一位置的多个标签做错位排布，右缘标签内移，避免叠印"""
    used = {}
    for (x, y, txt, up, color, fs) in labels:
        k = int(x // 3)
        i = used.get(k, 0)
        used[k] = i + 1
        base = -12 if up else 11
        step = -9 if up else 9
        dy = base + step * i
        ha, dx = ("right", -4) if x > 0.88 * n else ("center", 0)
        ax.annotate(txt, (x, y), textcoords="offset points", xytext=(dx, dy), ha=ha,
                    fontproperties=fp_b, fontsize=fs, color=color, zorder=9)


def draw_kline(ax, it, fp, fp_b):
    s, dt, px = it["s"], it["dt"], it["px"]
    df = it["df"]
    n = len(dt)
    lo, hi = min(px), max(px)
    res = structures(df)
    ylo, yhi = lo, hi
    for x in res["smc"] + res["wyckoff"]:
        for k in ("y", "lo", "up"):
            if x.get(k) is not None:
                ylo, yhi = min(ylo, x[k]), max(yhi, x[k])
    pad = 0.075 * (yhi - ylo)
    ax.set_ylim(ylo - pad, yhi + pad)

    col = UP if px[-1] >= px[0] else DOWN
    ax.fill_between(range(n), px, lo * 0.98, color=col, alpha=0.07, zorder=0)

    labels = []

    # 威科夫交易区间（最近一段）
    rgs = [x for x in res["wyckoff"] if x.get("lo") is not None and x.get("up") is not None]
    if rgs:
        g = rgs[-1]
        x0, x1 = max(0, g["xs"]), min(n - 1, g["xe"])
        ax.hlines([g["lo"], g["up"]], x0, x1, color=C_WK_L, lw=1.1, ls="--", zorder=2)

    # SMC 区带（OB / FVG）：延伸到被回测处，最小宽度 5 根
    for x in res["smc"]:
        if x["type"] == "OB(订单块)" or "FVG" in x["type"]:
            xs = max(0, x.get("xs", x["x"]))
            xe = zone_span(df, xs, x["lo"], x["up"], n)
            fc, ec, ls = (C_SMC_BAND, C_SMC, "-") if x["type"] == "OB(订单块)" else ("#EAF3FA", C_SMC_L, "--")
            ax.add_patch(Rectangle((xs, x["lo"]), max(1, xe - xs), max(1e-6, x["up"] - x["lo"]),
                                   facecolor=fc, edgecolor=ec, lw=1.0, ls=ls, alpha=0.95, zorder=1))
            labels.append((xs, x["up"], SHORT.get(x["type"], ""), True, C_SMC, 6.4))
            continue
        mk = "D" if "流动性扫取" in x["type"] else ("^" if x["kind"] == "bull" else "v")
        ax.scatter([x["x"]], [x["y"]], s=34, marker=mk, color=C_SMC, zorder=7,
                   edgecolor="white", lw=0.5)
        labels.append((x["x"], x["y"], SHORT.get(x["type"], ""), x["kind"] == "bull", C_SMC, 6.6))

    # 威科夫事件
    WK_MK = {"Spring(假跌破)": "*", "Upthrust(假突破)": "x", "Test(测试)": "o",
             "SOS(强势信号)": "^", "SOW(弱势信号)": "v"}
    for x in res["wyckoff"]:
        if x["type"] not in WK_MK:
            continue
        mk = WK_MK[x["type"]]
        ax.scatter([x["x"]], [x["y"]], s=46 if mk == "*" else 34, marker=mk, color=C_WK,
                   zorder=7, edgecolor="white", lw=0.5)
        labels.append((x["x"], x["y"], SHORT.get(x["type"], ""), x["kind"] == "bull", C_WK, 6.6))

    ax.plot(range(n), px, color=col, lw=1.5, zorder=4)
    place_labels(ax, labels, fp_b, n)

    step = max(1, n // 5)
    ax.set_xticks(list(range(0, n, step)))
    ax.set_xticklabels([dt[j][2:4] + "/" + dt[j][4:6] for j in range(0, n, step)],
                       fontproperties=fp, fontsize=8.0)
    ax.tick_params(axis="y", labelsize=8.0)
    for lb in ax.get_yticklabels():
        lb.set_fontproperties(fp)
    ax.grid(alpha=0.20, ls="--", lw=0.5, zorder=0)
    ax.set_title("%s（%s）· %s" % (s["name"], s["code"], it["dir"]["direction"]),
                 fontproperties=fp_b, fontsize=10.5, color=INK, pad=6)

    ch0 = s.get("chan") or {}
    lb = ch0.get("最近买点") or "-"
    at = s.get("归属") or {}
    box = ("现价 %.2f 元　买点等级 %s ｜ 2027E PE %s 倍／增速 %s%%（研报 %s 家）\n"
           "本图标出结构：SMC %d 条、威科夫 %d 条　｜　缠论：%s\n"
           "多归属：共 %s 个，增速-估值匹配 %s 个") % (
        px[-1], RANK_NAME.get(bp_rank(s), "—"), s.get("pe27E") if s.get("pe27E") is not None else "—",
        s.get("g27") if s.get("g27") is not None else "—", s.get("orgs", 0),
        len(res["smc"]), len(res["wyckoff"]), lb, at.get("归属数", "—"), at.get("归属匹配数", "—"))
    ax.text(0.0, -0.16, box, transform=ax.transAxes, fontproperties=fp, fontsize=7.0,
            va="top", ha="left", color="#333333", linespacing=1.30, clip_on=False,
            bbox=dict(boxstyle="round,pad=0.32", fc="#FFFFFF", ec=C_SMC, lw=0.8, alpha=0.97))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--variant", default="SMC威科夫结构版")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(a.task_dir, "config.json"), encoding="utf-8"))
    fp = FontProperties(fname=CJK)
    fp_b = FontProperties(fname=CJK, weight="bold")
    cap = int(cfg.get("max_stocks_per_chart", 20) or 20)
    pl = build_plan(cfg.get("directions", []), cap)

    items = []
    for d in cfg["directions"]:
        for s in d["stocks"]:
            p = os.path.join(a.task_dir, "data", "ohlcv", "%s.csv" % s["code"].replace(".", "_"))
            if not os.path.exists(p):
                continue
            df = pd.read_csv(p).dropna(subset=["close"]).reset_index(drop=True)
            if len(df) < 60:
                continue
            dt = [str(x) for x in df["date"].astype(str)]
            px = [float(x) for x in df["close"]]
            items.append({"dir": d, "s": s, "dt": dt, "px": px, "df": df})
    omap = {(x["direction"], x["code"]): i for i, x in enumerate(pl["order"])}
    items.sort(key=lambda it: omap.get((it["dir"]["direction"], it["s"]["code"]), 10 ** 6))
    chunks = [items[i:i + cap] for i in range(0, len(items), cap)] or [[]]
    n = len(chunks)

    tbl_rows = len(cfg["table"]) + 1
    tbl_in = 0.30 * tbl_rows
    base = "%s_%s_%s" % (cfg.get("report_name", "将军成长股拓扑选股"),
                         cfg["asof"].replace("-", ""), a.variant)
    outdir = os.path.join(a.task_dir, "out")
    os.makedirs(outdir, exist_ok=True)
    outs = []
    for k, ch in enumerate(chunks, 1):
        nrow = max(1, math.ceil(len(ch) / NCOL)) if ch else 1
        fig = plt.figure(figsize=(16.2, 3.72 * nrow + tbl_in + 3.4), dpi=128)
        gs = fig.add_gridspec(nrow + 1, NCOL, height_ratios=[1] * nrow + [tbl_in / 3.72],
                              hspace=0.68, wspace=0.15)
        for i, it in enumerate(ch):
            ax = fig.add_subplot(gs[i // NCOL, i % NCOL])
            draw_kline(ax, it, fp, fp_b)
        ax_t = fig.add_subplot(gs[nrow, :])
        draw_table(ax_t, cfg, fp, fp_b)
        fig.suptitle("%s ｜ 数据时点 %s　（SMC／威科夫结构版%s，共 %d 只个股）" % (
            cfg.get("report_name", "将军成长股拓扑选股"), cfg["asof"],
            "" if n == 1 else "　第 %d/%d 张，本图 %d 只" % (k, n, len(ch)), len(items)),
            fontproperties=fp_b, fontsize=16, color=INK, y=0.996)
        fig.text(0.5, 0.012,
                 "结构口径：SMC（聪明钱）＝摆动点(半径2)＋BOS/CHoCH 结构破坏＋OB 订单块（破坏前反向K线实体）＋"
                 "流动性扫取（假突破后收回）＋FVG 三根K线不平衡；威科夫＝交易区间(近60根)＋Spring 假跌破＋Upthrust 假突破＋Test＋SOS/SOW\n"
                 "图例（短标签）：SMC 蓝色系 —— BOS/CHoCH 结构破坏(▲看涨/▼看跌)、OB 订单块(实线蓝框)、FVG↑/FVG↓ 不平衡(虚线蓝框)、扫低/扫高 流动性扫取(◆)；"
                 "威科夫 橙紫色系 —— Spring(★)、UT 假突破(×)、Test(○)、SOS(▲)、SOW(▼)、交易区间(橙色虚线)\n"
                 "每子图最多标 7 条（SMC≤4、威科夫≤3，同类取最近 2 条）；OB/FVG 区带自形成延伸至被价格回测处（最小 6 根）｜"
                 "信息框位于价格区下方、不遮挡曲线　｜　数据：TuShare 代理　｜　来自 DoMate"
                 "　｜　本图 %d 只个股"
                 % len(ch),
                 ha="center", va="bottom", fontproperties=fp, fontsize=7.2, color="#666666", linespacing=1.55)
        fig.subplots_adjust(left=0.032, right=0.985, top=0.968, bottom=0.024)
        name = ("%s.png" % base) if n == 1 else ("%s_第%d张共%d张.png" % (base, k, n))
        out = os.path.join(outdir, name)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fig.savefig(out, facecolor="white")
            miss = [str(x.message) for x in w if "missing from font" in str(x.message).lower()
                    or "findfont" in str(x.message).lower()]
        plt.close(fig)
        outs.append(out)
        print("saved", out, os.path.getsize(out), "bytes | 本图个股", len(ch), "| 字体告警", miss or "无")
    json.dump({"charts": outs, "n_images": n, "n_stocks": len(items), "variant": a.variant},
              open(os.path.join(a.task_dir, "data", "smc_wyckoff_charts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    cfg["smc_wyckoff_chart_variant"] = a.variant
    cfg["smc_wyckoff_rule"] = ("SMC/威科夫结构图口径：SMC＝摆动点(半径2)+BOS/CHoCH 结构破坏+OB 订单块(破坏前反向K线实体)"
                               "+流动性扫取(假突破后收回)+FVG(三根K线不平衡)；威科夫＝交易区间(近60根)+Spring/Upthrust 假突破"
                               "+Test 缩量测试+SOS/SOW 放量突破跌破；每子图最多 7 条(SMC≤4、威科夫≤3，同类取最近2条)；"
                               "SMC 蓝色系、威科夫 橙紫色系，标注真实颜色与符号；信息框置于坐标区下方不遮挡价格；每图最多 %d 只。" % cap)
    json.dump(cfg, open(os.path.join(a.task_dir, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("共 %d 张图、%d 只个股 → data/smc_wyckoff_charts.json" % (n, len(items)))
    return outs


if __name__ == "__main__":
    main()