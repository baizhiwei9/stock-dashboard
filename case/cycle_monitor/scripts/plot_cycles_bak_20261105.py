#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存周期 & 信贷周期 组合曲线图（静态 PNG）
三子图：库存核心指标 / 库存领先指标 / 信贷周期
"""
import os
import sqlite3
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'cycle.db')
OUT_DIR = os.path.join(PROJECT_DIR, 'outputs')

plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'Noto Sans CJK JP', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False

BG = '#0e1117'
PANEL = '#161b26'
GRID = '#2a3142'
TXT = '#d8dee9'
SUB = '#8b95a7'
C1 = '#4ea8ff'
C2 = '#ff8c42'
C3 = '#3ddc97'
C4 = '#ff5c7a'
C5 = '#b28dff'


def load(indicator_id, limit=None):
    conn = sqlite3.connect(DB_PATH)
    rows = list(conn.execute(
        f"SELECT date, value FROM economic_indicators WHERE indicator_id='{indicator_id}' ORDER BY date"))
    conn.close()
    out = []
    for d, v in rows:
        if v is None:
            continue
        try:
            out.append((datetime.strptime(d[:10], '%Y-%m-%d'), float(v)))
        except Exception:
            continue
    return out[-limit:] if limit else out


def style(ax, title, sub=None):
    ax.set_facecolor(PANEL)
    ax.grid(True, color=GRID, linestyle='--', linewidth=0.7, alpha=0.7)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=SUB, labelsize=9, length=3)
    ax.text(0, 1.075, title, transform=ax.transAxes, color=TXT, fontsize=13.5,
            fontweight='bold', va='bottom', ha='left')
    if sub:
        ax.text(0, 1.018, sub, transform=ax.transAxes, color=SUB, fontsize=9.5,
                va='bottom', ha='left')


def month_axis(ax, interval=2):
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=interval))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(0)
        lbl.set_horizontalalignment('center')
        lbl.set_fontsize(8.5)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    i009, i010 = load('I009'), load('I010')
    i006, i007 = load('I006', 13), load('I007', 13)
    c001, c002 = load('C001', 36), load('C002', 36)

    fig = plt.figure(figsize=(13.5, 16), facecolor=BG)
    gs = fig.add_gridspec(3, 1, hspace=0.62, left=0.075, right=0.945,
                          top=0.885, bottom=0.055)

    # ===== ① 库存核心 =====
    ax1 = fig.add_subplot(gs[0])
    style(ax1, '① 库存周期 · 核心指标（统计局口径）',
          '产成品存货同比 vs 营业收入同比　双降 = 主动去库存')
    x1, y1 = [d for d, _ in i009], [v for _, v in i009]
    x2, y2 = [d for d, _ in i010], [v for _, v in i010]
    lo, hi = min(y1 + y2), max(y1 + y2)
    ax1.set_ylim(lo - 1.0, hi + 0.8)
    ax1.set_xlim(x1[0], datetime(2026, 7, 15))

    ax1.axvspan(datetime(2026, 3, 25), datetime(2026, 7, 15), color=C4, alpha=0.10)
    ax1.text(datetime(2026, 4, 5), lo - 0.9, '数据缺口\n2026-04 至今未更新', color=C4,
             fontsize=9.5, va='bottom', ha='left', linespacing=1.5)

    ax1.plot(x1, y1, color=C1, lw=2.8, marker='o', ms=5.5, label='产成品存货同比 (I009)')
    ax1.plot(x2, y2, color=C2, lw=2.8, marker='s', ms=5.5, label='营业收入同比 (I010)')
    ax1.axhline(0, color=SUB, lw=0.9, ls=':', alpha=0.6)
    for xs, ys, c, va in [(x1, y1, C1, 'bottom'), (x2, y2, C2, 'top')]:
        ax1.annotate(f'{ys[-1]:.1f}%', xy=(xs[-1], ys[-1]), xytext=(9, 6 if va == 'bottom' else -6),
                     textcoords='offset points', color=c, fontsize=11, fontweight='bold',
                     va=va, ha='left')
        ax1.annotate(f'{ys[0]:.1f}%', xy=(xs[0], ys[0]), xytext=(-9, 0),
                     textcoords='offset points', color=c, fontsize=9.5, va='center',
                     ha='right', alpha=0.75)
    ax1.legend(loc='upper left', facecolor=PANEL, edgecolor=GRID, labelcolor=TXT,
               fontsize=9.5, framealpha=0.95)
    month_axis(ax1, 2)

    # ===== ② 库存领先 =====
    ax2 = fig.add_subplot(gs[1])
    style(ax2, '② 库存周期 · 领先指标（PMI，更新至 2026-10）',
          'PMI新订单 vs PMI产成品库存　50 为荣枯线，双降 = 主动去库存')
    xo, yo = [d for d, _ in i006], [v for _, v in i006]
    xs_, ys_ = [d for d, _ in i007], [v for _, v in i007]
    ax2.set_ylim(min(yo + ys_) - 1.6, max(yo + ys_) + 1.4)
    ax2.plot(xo, yo, color=C3, lw=2.8, marker='o', ms=6.5, label='PMI 新订单 (I006)')
    ax2.plot(xs_, ys_, color=C4, lw=2.8, marker='s', ms=6.5, label='PMI 产成品库存 (I007)')
    ax2.fill_between(xo, yo, ys_, where=[a > b for a, b in zip(yo, ys_)],
                     color=C3, alpha=0.10, interpolate=True)
    ax2.axhline(50, color=SUB, lw=1.2, ls='--', alpha=0.85)
    ax2.text(xo[0], 50.05, ' 荣枯线 50', color=SUB, fontsize=9, va='bottom')
    for xa, ya, c, va in [(xo, yo, C3, 'bottom'), (xs_, ys_, C4, 'top')]:
        ax2.annotate(f'{ya[-1]:.1f}', xy=(xa[-1], ya[-1]), xytext=(9, 5 if va == 'bottom' else -5),
                     textcoords='offset points', color=c, fontsize=12, fontweight='bold', va=va)
    ax2.legend(loc='lower left', facecolor=PANEL, edgecolor=GRID, labelcolor=TXT,
               fontsize=9.5, framealpha=0.95)
    month_axis(ax2, 1)

    # ===== ③ 信贷周期 =====
    ax3 = fig.add_subplot(gs[2])
    style(ax3, '③ 信贷周期 · 社融 vs M2（近 36 个月）',
          '社融同比快速回落、M2 同步下行　信贷扩张动能衰减')
    xa, ya = [d for d, _ in c001], [v for _, v in c001]
    xb, yb = [d for d, _ in c002], [v for _, v in c002]
    ax3.set_ylim(min(ya + yb) - 1.8, max(ya + yb) + 1.6)
    ax3.plot(xa, ya, color=C1, lw=2.8, marker='o', ms=4.5, label='社融存量同比 (C001)')
    ax3.plot(xb, yb, color=C5, lw=2.8, marker='^', ms=4.5, label='M2同比 (C002)')
    ax3.axhline(0, color=SUB, lw=0.9, ls=':', alpha=0.6)
    for x_, y_, c in [(xa, ya, C1), (xb, yb, C5)]:
        ax3.annotate(f'{y_[-1]:.1f}%', xy=(x_[-1], y_[-1]), xytext=(9, 0),
                     textcoords='offset points', color=c, fontsize=11.5,
                     fontweight='bold', va='center')
    ax3.legend(loc='upper left', facecolor=PANEL, edgecolor=GRID, labelcolor=TXT,
               fontsize=9.5, framealpha=0.95)
    month_axis(ax3, 4)

    # 总标题
    fig.text(0.075, 0.972, '库存周期 & 信贷周期 监控曲线', color='#ffffff',
             fontsize=20, fontweight='bold', va='top')
    fig.text(0.075, 0.012,
             f'生成 {datetime.now().strftime("%Y-%m-%d %H:%M")}　│　数据源 国家统计局 / 中国人民银行　│　cycle.db　│　'
             f'注：① 存货/营收数据止于 2026-03，② PMI 为最新可用领先指标',
             color=SUB, fontsize=9.5, va='bottom')

    out = os.path.join(OUT_DIR, '20261105_周期曲线图.png')
    fig.savefig(out, dpi=150, facecolor=BG, bbox_inches='tight', pad_inches=0.35)
    plt.close(fig)
    print('saved:', out)
    return out


if __name__ == '__main__':
    main()
