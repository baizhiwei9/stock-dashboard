# -*- coding: utf-8 -*-
"""三条线图表：订单 / 产出 / 物价。

输出 PNG（白底、900x360、dpi150），同时适配飞书卡片与邮件内联图。
数据全部来自 EDB（经 edb_bridge），不直连外部网站。
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
from matplotlib import font_manager                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import edb_bridge as edb                              # noqa: E402

BLUE, RED, ORANGE, GREY = "#1f4e79", "#b03a2e", "#c77d0a", "#8a99a8"


def _pick_font():
    for name in ("Noto Sans SC", "Noto Sans CJK SC", "WenQuanYi Zen Hei",
                 "Droid Sans Fallback", "Source Han Sans SC"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:                              # noqa: BLE001
            continue
    return "DejaVu Sans"


plt.rcParams["font.sans-serif"] = [_pick_font(), "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c9d3de")
    ax.grid(axis="y", color="#e9eef4", lw=0.9)
    ax.tick_params(labelsize=9, colors="#5b6b7f")


def _save(fig, path):
    fig.tight_layout(pad=0.8)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def _empty(path, msg):
    """序列缺失时画一张占位图，而不是抛错中断整条推送。"""
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12, color=GREY)
    ax.axis("off")
    return _save(fig, path)


def _months(code, n):
    ms = edb.month_series(code, limit=n)
    return [m for m, _ in ms][::-1], [v for _, v in ms][::-1]


def order_chart(path, n=13):
    """订单：PMI 新订单 vs 新出口订单。"""
    m1, v1 = _months("CN.PMI.NEW.ORDER", n)
    m2, v2 = _months("CN.PMI.NEW.EXPORT", n)
    if not v1:
        return _empty(path, "订单：本期 PMI 分项未取到")
    x = list(range(len(m1)))
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    ax.plot(x, v1, color=BLUE, lw=2.2, marker="o", ms=4, label="PMI 新订单")
    if v2:
        ax.plot(x[:len(v2)], v2, color=ORANGE, lw=2.0, marker="s", ms=3.5, label="PMI 新出口订单")
    ax.axhline(50, color=GREY, ls="--", lw=1)
    ax.text(x[0], 50.05, "荣枯线 50", color=GREY, fontsize=8, va="bottom")
    ax.set_title("订单：新订单与新出口订单（PMI）", color=BLUE, fontsize=11, loc="left")
    ax.set_xticks(x[::2])
    ax.set_xticklabels([m[2:] for m in m1[::2]])
    pool = v1 + v2
    ax.set_ylim(min(pool + [48]) - 0.6, max(pool + [51]) + 0.6)
    for series, color, label in ((v1, BLUE, "新订单"), (v2, ORANGE, "新出口")):
        if not series:
            continue
        ax.annotate(f"{label} {series[-1]:.1f}", (len(series) - 1, series[-1]),
                    xytext=(-4, 8 if label == "新订单" else -14),
                    textcoords="offset points", color=color, fontsize=9, ha="right")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    _style(ax)
    return _save(fig, path)


def output_chart(path, n=36):
    """产出：出口当月同比（柱）+ PMI 生产（右轴线）。"""
    m1, v1 = _months("CN.EXP.YOY", n)
    m2, v2 = _months("CN.PMI.PROD", 13)
    if not v1:
        return _empty(path, "产出：出口序列未取到")
    x = list(range(len(m1)))
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    colors = [BLUE if v >= 0 else RED for v in v1]
    ax.bar(x, v1, color=colors, width=0.62, alpha=0.85, label="出口当月同比（%）")
    ax.axhline(0, color="#b9c4d0", lw=0.9)
    ax.set_title("产出：出口同比与 PMI 生产", color=BLUE, fontsize=11, loc="left")
    ax.set_xticks(x[::4])
    ax.set_xticklabels([m[2:] for m in m1[::4]])
    ax.set_ylabel("出口同比 %", fontsize=8, color=GREY)
    pos = {m: i for i, m in enumerate(m1)}
    xs = [pos[m] for m in m2 if m in pos]
    ys = [v for m, v in zip(m2, v2) if m in pos]
    if xs:
        ax2 = ax.twinx()
        ax2.plot(xs, ys, color=ORANGE, lw=2.0, marker="o", ms=3.5, label="PMI 生产")
        ax2.axhline(50, color=GREY, ls=":", lw=1)
        ax2.set_ylim(min(ys + [49]) - 1.2, max(ys + [52]) + 1.2)
        ax2.tick_params(labelsize=8, colors=ORANGE)
        for side in ("top", "left"):
            ax2.spines[side].set_visible(False)
        ax2.set_ylabel("PMI 生产", fontsize=8, color=ORANGE, labelpad=7)
        ax2.annotate(f"{ys[-1]:.1f}", (xs[-1], ys[-1]), xytext=(-8, -16),
                     textcoords="offset points", color=ORANGE, fontsize=9, ha="right",
                     bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.annotate(f"{v1[-1]:+.1f}%", (x[-1], v1[-1]), xytext=(-2, 6 if v1[-1] >= 0 else -12),
                textcoords="offset points", color=BLUE, fontsize=9, ha="right")
    _style(ax)
    return _save(fig, path)


def price_chart(path, n=36):
    """物价：PPI 同比（左轴）+ 布伦特月均（右轴）。"""
    m1, v1 = _months("CN.PPI.YOY", n)
    if not v1:
        return _empty(path, "物价：PPI 序列未取到")
    rows = edb.series("GLOBAL.BRENT")
    by_m = {}
    for d, v in rows.items():
        by_m.setdefault(d[:7], []).append(float(v))
    brent = {m: sum(vs) / len(vs) for m, vs in by_m.items()}
    x = list(range(len(m1)))
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    ax.plot(x, v1, color=RED, lw=2.2, marker="o", ms=3.5, label="PPI 同比（%）")
    ax.axhline(0, color="#b9c4d0", lw=0.9)
    ax.set_title("物价：PPI 同比与布伦特月均", color=BLUE, fontsize=11, loc="left")
    ax.set_xticks(x[::4])
    ax.set_xticklabels([m[2:] for m in m1[::4]])
    ax.set_ylabel("PPI 同比 %", fontsize=8, color=RED)
    new_axis = ax.twinx()
    bs = [brent.get(m) for m in m1]
    xs = [i for i, v in enumerate(bs) if v is not None]
    ys = [v for v in bs if v is not None]
    if xs:
        new_axis.plot(xs, ys, color=GREY, lw=1.6, ls="--", label="布伦特月均（美元）")
        new_axis.set_ylabel("布伦特 美元/桶", fontsize=8, color=GREY, labelpad=7)
        new_axis.tick_params(labelsize=8, colors=GREY)
        for side in ("top", "left"):
            new_axis.spines[side].set_visible(False)
        new_axis.annotate(f"{ys[-1]:.0f}", (xs[-1], ys[-1]), xytext=(-10, 8),
                          textcoords="offset points", color=GREY, fontsize=9, ha="right",
                          bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.annotate(f"{v1[-1]:+.1f}%", (x[-1], v1[-1]), xytext=(-6, 10),
                textcoords="offset points", color=RED, fontsize=9, ha="right",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    _style(ax)
    return _save(fig, path)


def order_nmfg_chart(path, n=12):
    """订单（非制造业）：服务业新订单 vs 建筑业新订单。"""
    m1, v1 = _months("CN.PMI.SVC.NEW.ORDER", n)
    m2, v2 = _months("CN.PMI.CONS.NEW.ORDER", n)
    if not v1 and not v2:
        return _empty(path, "非制造业订单：本期未取到")
    x = list(range(max(len(v1), len(v2))))
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    if v1:
        ax.plot(range(len(v1)), v1, color=BLUE, lw=2.2, marker="o", ms=4, label="服务业新订单")
    if v2:
        ax.plot(range(len(v2)), v2, color=ORANGE, lw=2.0, marker="s", ms=3.5, label="建筑业新订单")
    ax.axhline(50, color=GREY, ls="--", lw=1)
    ax.set_title("订单（非制造业）：服务业与建筑业新订单", color=BLUE, fontsize=11, loc="left")
    labels = m1 if len(m1) >= len(m2) else m2
    ax.set_xticks(x[::2])
    ax.set_xticklabels([m[2:] for m in labels[::2]])
    pool = v1 + v2
    ax.set_ylim(min(pool + [40]) - 1.0, max(pool + [51]) + 1.0)
    for series, color, label in ((v1, BLUE, "服务业"), (v2, ORANGE, "建筑业")):
        if series:
            ax.annotate(f"{label} {series[-1]:.1f}", (len(series) - 1, series[-1]),
                        xytext=(-4, 8 if label == "服务业" else -14),
                        textcoords="offset points", color=color, fontsize=9, ha="right",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    _style(ax)
    return _save(fig, path)


def durable_chart(path, n=12):
    """耐用品 vs 非耐用品：PPI 耐用消费品 vs 一般日用品（同比）。"""
    m1, v1 = _months("CN.PPI.DURABLE.YOY", n)
    m2, v2 = _months("CN.PPI.DAILY.YOY", n)
    if not v1 and not v2:
        return _empty(path, "耐用品/非耐用品：本期未取到")
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    if v1:
        ax.plot(range(len(v1)), v1, color=RED, lw=2.2, marker="o", ms=4, label="耐用品（耐用消费品）")
    if v2:
        ax.plot(range(len(v2)), v2, color=BLUE, lw=2.0, marker="s", ms=3.5, label="非耐用品（一般日用品）")
    ax.axhline(0, color="#b9c4d0", lw=0.9)
    ax.set_title("耐用品 vs 非耐用品：PPI 同比", color=BLUE, fontsize=11, loc="left")
    labels = m1 if len(m1) >= len(m2) else m2
    ax.set_xticks(range(0, len(labels), 2))
    ax.set_xticklabels([m[2:] for m in labels[::2]])
    pool = v1 + v2
    ax.set_ylim(min(pool + [-1]) - 0.8, max(pool + [2]) + 0.8)
    for series, color, label in ((v1, RED, "耐用品"), (v2, BLUE, "非耐用品")):
        if series:
            ax.annotate(f"{label} {series[-1]:+.1f}%", (len(series) - 1, series[-1]),
                        xytext=(-4, 8 if label == "耐用品" else -14),
                        textcoords="offset points", color=color, fontsize=9, ha="right",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    _style(ax)
    return _save(fig, path)

def build_all(out_dir, prefix="brief"):
    """生成五张图，返回 {key: path}。"""
    os.makedirs(out_dir, exist_ok=True)
    return {
        "order": order_chart(os.path.join(out_dir, f"{prefix}_order.png")),
        "order_nmfg": order_nmfg_chart(os.path.join(out_dir, f"{prefix}_order_nmfg.png")),
        "output": output_chart(os.path.join(out_dir, f"{prefix}_output.png")),
        "price": price_chart(os.path.join(out_dir, f"{prefix}_price.png")),
        "durable": durable_chart(os.path.join(out_dir, f"{prefix}_durable.png")),
    }


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
    print(build_all(out))