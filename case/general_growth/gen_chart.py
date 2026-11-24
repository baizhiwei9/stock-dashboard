# -*- coding: utf-8 -*-
"""「将军成长股拓扑选股」合成图（全市场口径 + 缠论标注 + 分图）

展示范围＝全部全链通过对象；每张图最多 max_stocks_per_chart（默认 20）只个股子图，超出即分图；
每张图底部都带全市场收敛对照表（45 行）。
个股子图：价格＋缠论结构（中枢矩形＋上下沿虚线、笔折线、买卖点标记）＋自洽标注 ＋ 多归属结论。
输出：out/<报告名>_<时点>_全市场链路缠论版_第k张共n张.png，并把图片清单写入 data/charts.json
用法: python3 gen_chart.py [--task-dir DIR]
"""
import argparse
import csv
import json
import math
import os
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

CJK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
UP = "#D64545"
DOWN = "#2E9E63"
INK = "#1F3864"
GREEN_FILL = "#E4F5E9"
GREEN_TEXT = "#0B5B2E"
RED_FILL = "#FDE7EA"
RED_TEXT = "#9C1B24"
ZS_FILL = "#F6E3B0"
BI_COLOR = "#5B6B8C"
BUY_COLOR = "#C0392B"
SELL_COLOR = "#2E9E63"
BUY_TXT = {"B1": "一买", "B2": "二买", "B3": "三买"}
SELL_TXT = {"S1": "一卖", "S2": "二卖", "S3": "三卖"}
NCOL = 4


from chart_order import bp_rank  # noqa  分图排序口径的唯一实现


def load_series(task, code):
    p = os.path.join(task, "data", "ohlcv", "%s.csv" % code.replace(".", "_"))
    if os.path.exists(p):
        d, c = [], []
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("date") and r.get("close"):
                    d.append(r["date"]); c.append(float(r["close"]))
        if len(d) >= 60:
            return d, c
    p2 = os.path.join(task, "data", "px_%s.csv" % code)
    d, c = [], []
    with open(p2, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("close"):
                d.append(r["trade_date"]); c.append(float(r["close"]))
    return d, c


def draw_table(ax_t, cfg, fp, fp_b):
    ax_t.axis("off")
    ax_t.set_title("全市场收敛对照表（★＝全链通过，○＝未通过；排序＝全链通过→结构聚集→增速）；"
                   "资金聚集浅绿底、资金扩散浅红底；PEG＝PE_TTM中位／净利TTM增速中位，≤1 为增速-估值匹配",
                   fontproperties=fp_b, fontsize=12.5, color=INK, pad=10)
    cols = ["对象（口径）", "拓扑\n判定", "邻居数", "净利TTM\n同比中位", "PE_TTM\n中位",
            "PEG\n(估值/增速)", "近5日成交\n(亿元)", "指数级缠论", "全链\n结论"]
    rows, fills, texts = [], [], []
    for r in cfg["table"]:
        pg = None
        if r.get("pe_ttm") and r.get("g_ttm") and r["g_ttm"] > 0:
            pg = r["pe_ttm"] / r["g_ttm"]
        rows.append([("★ " if r["chain_pass"] else "○ ") + r["name"] +
                     "（%s）" % ("申万" if r["kind"] == "申万三级" else "概念"),
                     r["judge"], "—" if r["neighbors"] is None else "%.0f" % r["neighbors"],
                     "—" if r["g_ttm"] is None else "%+.1f%%" % r["g_ttm"],
                     "—" if r["pe_ttm"] is None else "%.1f" % r["pe_ttm"],
                     "—" if pg is None else "%.2f" % pg,
                     "%.1f" % (r["amt5"] or 0),
                     "%s（%s）" % (r["chan_verdict"], r["chan_pos"]),
                     "通过" if r["chain_pass"] else "未过"])
        fills.append([GREEN_FILL if r["judge"] == "聚集" else RED_FILL] * len(cols))
        texts.append(GREEN_TEXT if r["chain_pass"] else (INK if r["judge"] == "聚集" else RED_TEXT))
    tbl = ax_t.table(cellText=rows, colLabels=cols, cellColours=fills,
                     colWidths=[0.238, 0.07, 0.056, 0.092, 0.074, 0.082, 0.094, 0.212, 0.082],
                     loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    h = 0.955 / max(1, len(rows) + 1)
    for (ri, ci), cell in tbl.get_celld().items():
        cell.set_linewidth(0.55)
        cell.set_edgecolor("#D9D9D9")
        cell.set_height(h)
        if ri == 0:
            cell.set_text_props(fontproperties=fp_b, fontsize=8.0, color=INK)
            cell.set_facecolor("#EEF3FA")
        else:
            tc = texts[ri - 1]
            if cols[ci] == "PEG\n(估值/增速)":
                try:
                    tc = GREEN_TEXT if float(cell.get_text().get_text()) <= 1 else RED_TEXT
                except Exception:  # noqa
                    tc = "#808080"
            cell.set_text_props(fontproperties=fp, fontsize=7.8, color=tc)


def draw_stock(ax, it, fp, fp_b):
    s, dt, px = it["s"], it["dt"], it["px"]
    idx = {d: j for j, d in enumerate(dt)}
    last, first = px[-1], px[0]
    col = UP if last >= first else DOWN
    chg = (last / first - 1) * 100
    cs = s.get("chan_struct") or {}
    ch0 = s.get("chan") or {}
    zs = cs.get("中枢") or []
    lz = zs[-1] if zs else None
    lo, hi = min(px), max(px)
    ylo = min([lo] + [z["ZD"] for z in zs])
    yhi = max([hi] + [z["ZG"] for z in zs])
    pad = 0.045 * (yhi - ylo)
    ax.set_ylim(ylo - pad, yhi + pad)
    for z in zs:
        x0 = idx.get(z["起"].replace("-", ""))
        x1 = idx.get(z["止"].replace("-", ""))
        x0 = 0 if x0 is None else x0
        x1 = len(dt) - 1 if x1 is None else x1
        if x1 - x0 < 1:
            continue
        ax.add_patch(Rectangle((x0, z["ZD"]), x1 - x0, z["ZG"] - z["ZD"],
                               facecolor=ZS_FILL, edgecolor="#C9A227", lw=1.0, alpha=0.6, zorder=1))
    if lz:
        ax.axhline(lz["ZG"], color="#C9A227", lw=0.9, ls="--", alpha=0.75, zorder=2)
        ax.axhline(lz["ZD"], color="#C9A227", lw=0.9, ls="--", alpha=0.75, zorder=2)
        ax.text(len(dt) - 1, (lz["ZD"] + lz["ZG"]) / 2, "中枢 %.2f–%.2f" % (lz["ZD"], lz["ZG"]),
                fontproperties=fp, fontsize=7.2, color="#8A6D1E", ha="right", va="center", zorder=6)

    ax.plot(range(len(px)), px, color=col, lw=1.5, zorder=3)
    ax.fill_between(range(len(px)), px, lo * 0.985, color=col, alpha=0.08)
    bis = [(idx[b["时间"].replace("-", "")], b["价格"]) for b in (cs.get("笔") or [])
           if b["时间"].replace("-", "") in idx]
    if len(bis) >= 2:
        ax.plot([b[0] for b in bis], [b[1] for b in bis], color=BI_COLOR, lw=1.15, ls="-",
                alpha=0.78, zorder=4)
        ax.scatter([b[0] for b in bis], [b[1] for b in bis], s=10, color=BI_COLOR, zorder=5, marker="o")
    ok_buys = set(ch0.get("一致买点") or [])
    for g_ in (cs.get("买卖点") or []):
        k = g_["时间"].replace("-", "")
        if k not in idx:
            continue
        x, y = idx[k], g_["价格"]
        if g_["类型"] in BUY_TXT and ("%s@%s" % (g_["类型"], g_["时间"])) not in ok_buys:
            continue
        if g_["类型"] in BUY_TXT:
            ax.scatter([x], [y], s=52, marker="^", color=BUY_COLOR, zorder=7, edgecolor="white", lw=0.6)
            ax.annotate(BUY_TXT[g_["类型"]], (x, y), textcoords="offset points", xytext=(0, -12),
                        ha="center", fontproperties=fp_b, fontsize=7.6, color=BUY_COLOR, zorder=8)
        elif g_["类型"] in SELL_TXT:
            ax.scatter([x], [y], s=48, marker="v", color=SELL_COLOR, zorder=7, edgecolor="white", lw=0.6)
            ax.annotate(SELL_TXT[g_["类型"]], (x, y), textcoords="offset points", xytext=(0, 9),
                        ha="center", fontproperties=fp_b, fontsize=7.6, color=SELL_COLOR, zorder=8)
    ax.scatter([len(px) - 1], [last], s=42, color=col, zorder=6)
    step = max(1, len(dt) // 5)
    ax.set_xticks(list(range(0, len(dt), step)))
    ax.set_xticklabels([dt[j][2:4] + "/" + dt[j][4:6] for j in range(0, len(dt), step)],
                       fontproperties=fp, fontsize=8.2)
    ax.tick_params(axis="y", labelsize=8.2)
    for lb in ax.get_yticklabels():
        lb.set_fontproperties(fp)
    ax.grid(alpha=0.22, ls="--", lw=0.55, zorder=0)
    ax.set_title("%s（%s）· %s" % (s["name"], s["code"], it["dir"]["direction"]),
                 fontproperties=fp_b, fontsize=10.5, color=INK, pad=6)

    lb = ch0.get("最近买点") or "-"
    chan_line = "缠论：%s" % lb
    at = s.get("归属") or {}
    attr_line = "多归属：共 %s 个，增速-估值匹配 %s 个%s" % (
        at.get("归属数", "—"), at.get("归属匹配数", "—"),
        ("（%.0f%%）" % at["归属匹配率"]) if at.get("归属匹配率") is not None else "")
    box = ("现价 %.2f 元　区间 %+.1f%%\n"
           "2027E PE %s 倍／增速 %s%%（研报 %s 家）\n"
           "PEG(2027E) %s ｜ PEG(TTM) %s\n"
           "%s\n%s") % (
        last, chg, s["pe27E"] if s.get("pe27E") is not None else "—",
        s["g27"] if s.get("g27") is not None else "—", s.get("orgs", 0),
        s.get("peg27E") if s.get("peg27E") is not None else "—",
        s.get("pegTTM") if s.get("pegTTM") is not None else "—", chan_line, attr_line)
    ax.text(0.0, -0.17, box, transform=ax.transAxes, fontproperties=fp, fontsize=7.4,
            va="top", ha="left", color="#333333", linespacing=1.30, clip_on=False,
            bbox=dict(boxstyle="round,pad=0.34", fc="#FFFFFF", ec=col, lw=0.85, alpha=0.97))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--out", default=None, help="仅当单图时生效")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(a.task_dir, "config.json"), encoding="utf-8"))
    fp = FontProperties(fname=CJK)
    fp_b = FontProperties(fname=CJK, weight="bold")
    chunk = int(cfg.get("max_stocks_per_chart", 20) or 20)

    items = []
    for d in cfg["directions"]:
        for s in d["stocks"]:
            dt, px = load_series(a.task_dir, s["code"])
            if len(dt) < 30:
                continue
            items.append({"dir": d, "s": s, "dt": dt, "px": px})
    pl_path = os.path.join(a.task_dir, "data", "chart_plan.json")
    plan = json.load(open(pl_path, encoding="utf-8")) if os.path.exists(pl_path) else None
    if plan:
        chunk = int(plan.get("cap") or chunk)
        omap = {(x["direction"], x["code"]): i for i, x in enumerate(plan.get("order") or [])}
        items.sort(key=lambda it: omap.get((it["dir"]["direction"], it["s"]["code"]), 10 ** 6))
        print("按 chart_plan.json 排序（买点等级降序），计划分图 %d 张" % plan.get("n_images", 0))
    else:
        items.sort(key=lambda it: -bp_rank(it["s"]))
        print("[warn] 未找到 chart_plan.json，按买点等级本地排序")
    chunks = [items[i:i + chunk] for i in range(0, len(items), chunk)] or [[]]
    print("分图顺序（买点等级）：", [(it["s"]["name"], bp_rank(it["s"])) for it in items[:6]],
          "… 第2张首只:", items[chunk]["s"]["name"] if len(items) > chunk else "—")
    n = len(chunks)

    tbl_rows = len(cfg["table"]) + 1
    tbl_in = 0.30 * tbl_rows
    variant = cfg.get("chart_variant") or ""
    base = "%s_%s_全市场链路缠论版%s" % (cfg.get("report_name", "将军成长股拓扑选股"),
                                    cfg["asof"].replace("-", ""),
                                    ("_" + variant) if variant else "")
    outdir = os.path.join(a.task_dir, "out")
    os.makedirs(outdir, exist_ok=True)
    outs = []
    for k, ch in enumerate(chunks, 1):
        nrow = max(1, math.ceil(len(ch) / NCOL)) if ch else 1
        fig = plt.figure(figsize=(16.2, 3.62 * nrow + tbl_in + 3.1), dpi=128)
        gs = fig.add_gridspec(nrow + 1, NCOL, height_ratios=[1] * nrow + [tbl_in / 3.62],
                              hspace=0.62, wspace=0.15)
        for i, it in enumerate(ch):
            ax = fig.add_subplot(gs[i // NCOL, i % NCOL])
            draw_stock(ax, it, fp, fp_b)
        ax_t = fig.add_subplot(gs[nrow, :])
        draw_table(ax_t, cfg, fp, fp_b)
        un = cfg.get("universe", {})
        fig.suptitle("%s ｜ 数据时点 %s%s" % (
            cfg.get("report_name", "将军成长股拓扑选股"), cfg["asof"],
            ("　（第 %d/%d 张，本图 %d 只个股）" % (k, n, len(ch)) if n > 1 else "")),
            fontproperties=fp_b, fontsize=17, color=INK, y=0.996)
        fig.text(0.5, 0.012,
                 "口径：起点＝申万 2021 三级 %d ＋ 同花顺概念 %d（共 %d）｜条件过滤（体量/盈利/增速-估值匹配PEG≤1/容量）后余 %d｜"
                 "拓扑＝60日相关>0.8的邻居数（≥4聚集、≤3扩散）｜指数级缠论＝不在中枢下方或出现一买｜全链通过 %d、待核 %d\n"
                 "分图顺序（本轮调整）：按个股买点等级降序——三买>二买>一买>突破中枢>中枢内部>其他；"
                 "第1张为买点等级最高的 20 只（本图全部有买点），其后各张为买点较少者；每图最多 20 只、每图底部均带对照表\n"
                 "图例：黄色框＝最近中枢（含上沿ZG／下沿ZD虚线）｜蓝色折线＝笔｜▲红＝买点｜▼绿＝卖点（仅标注与当前状态自洽的买点）｜"
                 "本图 %d 只个股｜数据：TuShare 代理｜来自 DoMate"
                 % (un.get("申万三级", 0), un.get("同花顺概念", 0), un.get("全市场", 0),
                    un.get("条件全过", 0), un.get("全链通过", 0), un.get("缠论待核", 0), len(ch)),
                 ha="center", va="bottom", fontproperties=fp, fontsize=7.6, color="#666666",
                 linespacing=1.5)
        fig.subplots_adjust(left=0.032, right=0.985, top=0.968, bottom=0.022)
        name = (base + ".png") if n == 1 else ("%s_第%d张共%d张.png" % (base, k, n))
        out = a.out if (a.out and n == 1) else os.path.join(outdir, name)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fig.savefig(out, facecolor="white")
            miss = [str(x.message) for x in w if "missing from font" in str(x.message).lower()
                    or "findfont" in str(x.message).lower()]
        plt.close(fig)
        outs.append(out)
        print("saved", out, os.path.getsize(out), "bytes | 本图个股", len(ch), "| 字体告警",
              miss if miss else "无")
    json.dump({"charts": outs, "n_images": n, "n_stocks": len(items), "max_per_chart": chunk},
              open(os.path.join(a.task_dir, "data", "charts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("共 %d 张图、%d 只个股 → data/charts.json" % (n, len(items)))
    return outs


if __name__ == "__main__":
    main()