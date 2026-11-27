# -*- coding: utf-8 -*-
"""market_momentum 的 render 层 —— 文字报告 + 图（matplotlib，纯本地，不联网）

产物：
    out/report.md            主报告（也是 dispatch 的正文）
    out/momentum.png         五只宽基的动量柱状图 + 排名标注

图里只画算得出来的量；某项缺数就在图上标「NA」，不画 0 值（0 会被误读成「平」）。
"""
from __future__ import annotations

import os

MOM_DAYS_DEFAULT = 20


def _f(v, dec=2, unit="", sign=False):
    if v is None:
        return "NA"
    fmt = ("%+" if sign else "%") + ".%df" % dec
    return (fmt % v) + unit


def build_text(per, secs, sw, env, as_of, note=""):
    L = []
    L.append("## 宽基动量 · %s" % as_of)
    if note:
        L.append("> %s" % note)
    L.append("")
    # ① 市场环境
    e = env or {}
    bits = []
    for k in ("上证指数", "深证成指"):
        v = (e.get("pct") or {}).get(k)
        if v is not None:
            bits.append("%s %s" % (k, _f(v, 2, "%", sign=True)))
    if e.get("amt_total"):
        bits.append("两市成交 %.0f亿" % e["amt_total"])
        if e.get("amt_ratio"):
            bits.append("前4日均 %s" % _f(e["amt_ratio"] * 100, 0, "%"))
    if bits:
        L.append("**① 市场环境**：" + "｜".join(bits))
        L.append("")

    # ② 宽基动量
    L.append("**② 宽基动量（%d 交易日涨幅）**" % MOM_DAYS_DEFAULT)
    L.append("")
    L.append("| 排名 | 标的 | 近%d日 | 动量分 | 拐头 | 收盘 |" % MOM_DAYS_DEFAULT)
    L.append("|---|---|---|---|---|---|")
    order = sorted([per[c] for c, _n in secs if c in per],
                   key=lambda x: (x.get("rank") is None, x.get("rank") or 99))
    for v in order:
        turn = {1: "▲ 拐头向上", -1: "▼ 拐头向下", 0: "—"}.get(v.get("turn"), "—")
        L.append("| %s | %s | %s | %s | %s | %s |"
                 % (v.get("rank") or "—", v["name"],
                    _f(v.get("mom"), 2, "%", sign=True),
                    _f(v.get("score"), 0, ""),
                    turn,
                    _f(v.get("close"), 3)))
    L.append("")

    strongest = [v for v in order if v.get("rank")]
    if strongest:
        top = strongest[0]
        L.append("最强：**%s**（%s，动量分 %s）；最弱：**%s**（%s）。"
                 % (top["name"], _f(top.get("mom"), 2, "%", sign=True),
                    _f(top.get("score"), 0),
                    strongest[-1]["name"],
                    _f(strongest[-1].get("mom"), 2, "%", sign=True)))
    ups = [v["name"] for v in order if v.get("turn") == 1]
    dns = [v["name"] for v in order if v.get("turn") == -1]
    if ups or dns:
        seg = []
        if ups:
            seg.append("向上 %s" % "、".join(ups))
        if dns:
            seg.append("向下 %s" % "、".join(dns))
        L.append("拐头信号：" + "；".join(seg) + "。")
    else:
        L.append("拐头信号：无（各标的动量分都在中间区间，无翻转）。")
    L.append("")

    # ③ 申万一级行业动量
    L.append("**③ 申万一级行业动量（参考，纯价格口径）**")
    if (sw or {}).get("error"):
        L.append("")
        L.append("_未生成：%s_" % sw["error"])
    else:
        L.append("")
        L.append("数据日 %s（基准 %s，%d 个行业）" % (sw.get("date"), sw.get("base_date"), sw.get("n") or 0))
        L.append("")
        L.append("| 近%d日前十 | 涨幅 | 当日前十 | 涨幅 |" % MOM_DAYS_DEFAULT)
        L.append("|---|---|---|---|")
        a = sw.get("by_mom") or []
        b = sw.get("by_day") or []
        for i in range(max(len(a), len(b))):
            an = a[i]["name"] if i < len(a) else ""
            av = _f(a[i]["mom"], 2, "%", sign=True) if i < len(a) else ""
            bn = b[i]["name"] if i < len(b) else ""
            bv = _f(b[i]["day"], 2, "%", sign=True) if i < len(b) else ""
            L.append("| %s | %s | %s | %s |" % (an, av, bn, bv))
        if sw.get("tail_mom"):
            L.append("")
            L.append("近%d日垫底：%s" % (MOM_DAYS_DEFAULT,
                                        "、".join("%s %s" % (x["name"], _f(x["mom"], 2, "%", sign=True))
                                                 for x in sw["tail_mom"][-5:])))
    L.append("")
    L.append("_口径：动量=收盘价近 %d 交易日涨幅；动量分=当日动量在当日宽基横截面的百分位；"
             "拐头=动量分从 <40 跳到 ≥60（向上）或从 >60 落到 ≤40（向下）。"
             "以上为复盘观察，不构成投资建议。_" % MOM_DAYS_DEFAULT)
    return "\n".join(L) + "\n"


def build_chart(per, secs, as_of, out_path):
    """动量柱状图。matplotlib 用 Agg 后端，不弹窗、不联网。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    # 中文字体：框架 assets.ensure_cjk_font 找不到可用字体时会抛 RuntimeError。
    # 这里不再吞异常静默降级——找不到中文字体就让任务失败、进缺口表，
    # 而不是画一张中文全豆腐块的图发出去当成功。
    import sys
    import os as _os
    _lib = _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                           "..", "_lib"))
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    from assets import ensure_cjk_font
    fpath = ensure_cjk_font(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                          "fonts", "SimHei.ttf"))
    if fpath and os.path.isfile(fpath):
        font_manager.fontManager.addfont(fpath)
        name = font_manager.FontProperties(fname=fpath).get_name()
        plt.rcParams["font.family"] = name
    plt.rcParams["axes.unicode_minus"] = False

    items = [per[c] for c, _n in secs if c in per]
    items = sorted(items, key=lambda x: (x.get("rank") is None, x.get("rank") or 99))
    names = [v["name"] for v in items]
    vals = [v.get("mom") if v.get("mom") is not None else 0.0 for v in items]
    na = [v.get("mom") is None for v in items]
    colors = []
    for v, is_na in zip(items, na):
        if is_na:
            colors.append("#9aa4b2")
        else:
            colors.append("#e0533d" if (v.get("mom") or 0) >= 0 else "#2f9e6f")

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    bars = ax.bar(range(len(items)), vals, color=colors, width=0.6)
    ax.axhline(0, color="#888", lw=0.8)
    ax.set_xticks(range(len(items)))
    ax.set_xticklabels(names, fontsize=10)
    ax.set_ylabel("近 %d 交易日涨幅 (%%)" % MOM_DAYS_DEFAULT, fontsize=10)
    ax.set_title("宽基动量 · %s" % as_of, fontsize=12, pad=10)
    for b, v, is_na in zip(bars, items, na):
        y = b.get_height()
        lab = "NA" if is_na else ("%+.2f%%" % (v.get("mom") or 0))
        ax.annotate(lab, (b.get_x() + b.get_width() / 2, y),
                    textcoords="offset points",
                    xytext=(0, 4 if y >= 0 else -14),
                    ha="center", fontsize=9)
        if v.get("rank"):
            ax.annotate("#%d" % v["rank"],
                        (b.get_x() + b.get_width() / 2, y),
                        textcoords="offset points",
                        xytext=(0, 16 if y >= 0 else -26),
                        ha="center", fontsize=8, color="#666")
    ax.grid(axis="y", ls=":", lw=0.6, color="#ddd")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
