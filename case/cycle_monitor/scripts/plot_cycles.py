#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存周期 & 信贷周期 组合曲线图（静态 PNG）
带阶段色带 / 切换星标 / 当前阶段标签 / 预估切换月份
"""
import os
import sys
import sqlite3
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle, Patch
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'cycle.db')
OUT_DIR = os.path.join(PROJECT_DIR, 'outputs')
sys.path.insert(0, SCRIPT_DIR)

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

# 阶段配色
PHASE_COLOR = {
    '主动补库存': '#3ddc97',
    '被动补库存': '#ffd166',
    '主动去库存': '#ff8c42',
    '被动去库存': '#4ea8ff',
    '信贷扩张期': '#3ddc97',
    '政策宽松期': '#4ea8ff',
    '信贷收缩期': '#ff5c7a',
    '政策收紧期': '#ffd166',
}

# 阶段平均时长（月）
INV_AVG = {'主动补库存': 14, '被动补库存': 8, '主动去库存': 12, '被动去库存': 10}
CREDIT_AVG = {'信贷扩张期': 16, '信贷收缩期': 12, '政策宽松期': 8, '政策收紧期': 10}
INV_NEXT = {'主动补库存': '被动补库存', '被动补库存': '主动去库存',
            '主动去库存': '被动去库存', '被动去库存': '主动补库存'}
CREDIT_NEXT = {'信贷扩张期': '政策收紧期', '政策收紧期': '信贷收缩期',
               '信贷收缩期': '政策宽松期', '政策宽松期': '信贷扩张期'}


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


def add_months(dt, n):
    y, m = dt.year, dt.month + n
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return datetime(y, m, 1)


def style(ax, title, sub=None):
    ax.set_facecolor(PANEL)
    ax.grid(True, color=GRID, linestyle='--', linewidth=0.7, alpha=0.55)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=SUB, labelsize=9, length=3)
    ax.text(0, 1.085, title, transform=ax.transAxes, color=TXT, fontsize=13.5,
            fontweight='bold', va='bottom', ha='left')
    if sub:
        ax.text(0, 1.022, sub, transform=ax.transAxes, color=SUB, fontsize=9.2,
                va='bottom', ha='left')


def month_axis(ax, interval=2):
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=interval))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(0)
        lbl.set_horizontalalignment('center')
        lbl.set_fontsize(8.5)


def draw_phases(ax, transitions, ymin, ymax, end_date, label_y, est_next=None, est_month=None,
                overdue_months=0, next_phase=None):
    """在 ax 上画阶段色带 + 切换星标 + 阶段名称"""
    if not transitions:
        return
    xl = mdates.num2date(ax.get_xlim()[0]).replace(tzinfo=None)
    xr = mdates.num2date(ax.get_xlim()[1]).replace(tzinfo=None)
    for i, (start_str, phase, months) in enumerate(transitions):
        y, m = map(int, start_str.split('-'))
        x0 = datetime(y, m, 1)
        x1 = end_date if i == len(transitions) - 1 else None
        if x1 is None:
            ny, nm = map(int, transitions[i + 1][0].split('-'))
            x1 = datetime(ny, nm, 1)
        if x1 <= x0:
            continue
        col = PHASE_COLOR.get(phase, SUB)
        ax.axvspan(x0, x1, color=col, alpha=0.085, zorder=0)
        # 切换星标（视野外的切换点不画，避免在轴边缘留残影）
        if i > 0 and xl <= x0 <= xr:
            ax.plot([x0], [ymax], marker='*', ms=15, color=col,
                    markeredgecolor='#0e1117', markeredgewidth=0.8,
                    zorder=6, clip_on=False)
            ax.axvline(x0, color=col, lw=1.0, ls=':', alpha=0.65, zorder=1)
        # 阶段名
        span_days = (x1 - x0).days
        # 完全在视野外的阶段不画名（否则 clamp 后会跟相邻阶段的标签叠在一起）
        if span_days > 55 and x1 > xl and x0 < xr:
            xm = x0 + (x1 - x0) / 2
            pad = (xr - xl) * 0.055
            if xm < xl + pad:
                xm = xl + pad
            if xm > xr - pad:
                xm = xr - pad
            ax.text(xm, label_y, phase, color=col, fontsize=9,
                    ha='center', va='center', alpha=0.95,
                    bbox=dict(boxstyle='round,pad=0.25', fc=PANEL, ec=col, lw=0.7, alpha=0.85))

    # 当前阶段标签
    cur_start, cur_phase, cur_months = transitions[-1]
    col = PHASE_COLOR.get(cur_phase, SUB)
    nxt = next_phase or est_next or '—'
    ax.annotate(f'当前：{cur_phase}（已 {cur_months} 个月）\n下一阶段：{nxt}',
                xy=(end_date, label_y), xytext=(0, -26), textcoords='offset points',
                color=col, fontsize=10.5, fontweight='bold', ha='right', va='top',
                bbox=dict(boxstyle='round,pad=0.4', fc='#1d2433', ec=col, lw=1.2))

    # 预估切换 / 已超均值
    if est_next and est_month:
        ax.axvline(est_month, color='#ffd166', lw=1.4, ls='--', alpha=0.85, zorder=5)
        ax.plot([est_month], [ymax], marker='*', ms=17, color='#ffd166',
                markeredgecolor='#0e1117', markeredgewidth=0.9, zorder=7, clip_on=False)
        ax.annotate(f'预估切换 → {est_next}\n{est_month.strftime("%Y-%m")} 前后',
                    xy=(est_month, ymax), xytext=(-8, 30), textcoords='offset points',
                    color='#ffd166', fontsize=9.5, fontweight='bold', ha='right', va='bottom',
                    bbox=dict(boxstyle='round,pad=0.35', fc='#1d2433', ec='#ffd166', lw=1.0))
    elif overdue_months > 0:
        # 已超历史均值：不能画"预估切换"，那会落在过去的月份上
        ax.annotate(f'已超历史均值 {overdue_months} 个月\n切换条件尚未满足\n下一阶段：{nxt}',
                    xy=(end_date, ymax), xytext=(-10, 28), textcoords='offset points',
                    color='#ffd166', fontsize=9.5, fontweight='bold', ha='right', va='bottom',
                    bbox=dict(boxstyle='round,pad=0.35', fc='#1d2433', ec='#ffd166', lw=1.0))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    import cycle_phase_analyzer as cpa

    credit_trans, inv_trans, df_credit, df_inv = cpa.analyze_cycles()

    i009, i010 = load('I009'), load('I010')
    i006, i007 = load('I006', 12), load('I007', 12)
    c003, c002 = load('C003'), load('C002', 40)

    inv_end = pd.to_datetime(df_inv.dropna(subset=['phase'])['date']).max().to_pydatetime()
    cred_end = pd.to_datetime(df_credit.dropna(subset=['phase'])['date']).max().to_pydatetime()

    cur_inv = inv_trans[-1][1] if inv_trans else '未知'
    cur_inv_months = inv_trans[-1][2] if inv_trans else 0
    cur_cred = credit_trans[-1][1] if credit_trans else '未知'
    cur_cred_months = credit_trans[-1][2] if credit_trans else 0

    inv_avg = INV_AVG.get(cur_inv, 12)
    # 【2026-11-05】允许负数：负值表示已超历史均值、切换条件尚未满足，
    # 此时不能画"预估切换"星标（会画在过去某个月，误导成"早该切了"）。
    inv_remain = inv_avg - cur_inv_months
    inv_overdue = max(0, -inv_remain)
    inv_est = add_months(inv_end, inv_remain) if inv_remain > 0 else None
    cred_avg = CREDIT_AVG.get(cur_cred, 12)
    cred_remain = CREDIT_AVG.get(cur_cred, 12) - cur_cred_months
    cred_overdue = max(0, -cred_remain)
    cred_est = add_months(cred_end, cred_remain) if cred_remain > 0 else None
    # 坐标轴右端参考点（预估月可能为 None）
    inv_axis_end = inv_est or add_months(inv_end, 2)
    cred_axis_end = cred_est or add_months(cred_end, 2)

    fig = plt.figure(figsize=(14.5, 19), facecolor=BG)
    gs = fig.add_gridspec(4, 1, hspace=0.75, left=0.075, right=0.93,
                          top=0.905, bottom=0.04,
                          height_ratios=[1.15, 1.0, 1.0, 0.62])

    # ===== ① 库存周期：统计局核心 + PMI 领先 =====
    ax1 = fig.add_subplot(gs[0])
    style(ax1, '① 库存周期 · 阶段演进（统计局同比为核心口径）',
          f'存货/营收同比 I009/I010 最新至 {inv_end.strftime("%Y-%m")}　│　PMI 仅作最新月动能参考，不参与阶段判定')
    x1, y1 = [d for d, _ in i009], [v for _, v in i009]
    x2, y2 = [d for d, _ in i010], [v for _, v in i010]
    ax1.plot(x1, y1, color=C1, lw=2.8, marker='o', ms=5.5, label='产成品存货同比 (I009, 左轴)')
    ax1.plot(x2, y2, color=C2, lw=2.8, marker='s', ms=5.5, label='营业收入同比 (I010, 左轴)')
    ax1.axhline(0, color=SUB, lw=0.9, ls=':', alpha=0.5)
    lo, hi = min(y1 + y2), max(y1 + y2)
    ax1.set_ylim(lo - 2.4, hi + 3.2)
    ax1.set_xlim(add_months(x1[0], -2), add_months(inv_axis_end, 3))

    ax1b = ax1.twinx()
    xo, yo = [d for d, _ in i006], [v for _, v in i006]
    xs_, ys_ = [d for d, _ in i007], [v for _, v in i007]
    ax1b.plot(xo, yo, color=C3, lw=2.4, ls='-', marker='o', ms=5, alpha=0.95,
              label='PMI 新订单 (I006, 右轴)')
    ax1b.plot(xs_, ys_, color=C4, lw=2.4, ls='-', marker='s', ms=5, alpha=0.95,
              label='PMI 产成品库存 (I007, 右轴)')
    ax1b.axhline(50, color=SUB, lw=0.9, ls='--', alpha=0.5)
    ax1b.set_ylim(min(yo + ys_) - 2.2, max(yo + ys_) + 2.2)
    ax1b.tick_params(colors=SUB, labelsize=8.5)
    for s in ax1b.spines.values():
        s.set_color(GRID)

    draw_phases(ax1, inv_trans, ax1.get_ylim()[0], ax1.get_ylim()[1], inv_end,
                label_y=lo - 1.6, est_next=INV_NEXT.get(cur_inv), est_month=inv_est,
                overdue_months=inv_overdue, next_phase=INV_NEXT.get(cur_inv))

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc='upper left', bbox_to_anchor=(0.0, 0.915), ncol=2, facecolor=PANEL, edgecolor=GRID,
               labelcolor=TXT, fontsize=8.6, framealpha=0.95)
    month_axis(ax1, 2)

    # ===== ② 库存周期 PMI 特写 =====
    ax2 = fig.add_subplot(gs[1])
    style(ax2, '② 最新月动能特写（PMI 环比扩散指数，不作阶段判定）',
          'PMI新订单 vs PMI产成品库存　50 为荣枯线　仅反映当月环比强弱')
    ax2.plot(xo, yo, color=C3, lw=3.0, marker='o', ms=8, label='PMI 新订单 (I006)')
    ax2.plot(xs_, ys_, color=C4, lw=3.0, marker='s', ms=8, label='PMI 产成品库存 (I007)')
    ax2.fill_between(xo, yo, ys_, where=[a > b for a, b in zip(yo, ys_)],
                     color=C3, alpha=0.09, interpolate=True)
    ax2.axhline(50, color=SUB, lw=1.2, ls='--', alpha=0.8)
    ax2.text(xo[0], 50.06, ' 荣枯线 50', color=SUB, fontsize=9, va='bottom')
    ax2.set_ylim(min(yo + ys_) - 1.9, max(yo + ys_) + 1.7)
    for xa, ya, c, va in [(xo, yo, C3, 'bottom'), (xs_, ys_, C4, 'top')]:
        ax2.annotate(f'{ya[-1]:.1f}', xy=(xa[-1], ya[-1]), xytext=(10, 6 if va == 'bottom' else -6),
                     textcoords='offset points', color=c, fontsize=12.5, fontweight='bold', va=va)
    ax2.legend(loc='upper right', facecolor=PANEL, edgecolor=GRID, labelcolor=TXT,
               fontsize=9.5, framealpha=0.95)
    month_axis(ax2, 1)

    # ===== ③ 信贷周期 =====
    ax3 = fig.add_subplot(gs[2])
    style(ax3, '③ 信贷周期 · 阶段演进（贷款余额同比 + M2，均为存量同比）',
          '旧口径「社融滚动12月增量同比」波动 -10%~+16% 噪音过大，已降级为辅助，不再参与阶段判定')
    xa, ya = [d for d, _ in c003], [v for _, v in c003]
    xb, yb = [d for d, _ in c002], [v for _, v in c002]
    ax3.plot(xa, ya, color=C1, lw=2.8, marker='o', ms=5, label='人民币贷款余额同比 (C003)')
    ax3.plot(xb, yb, color=C5, lw=2.8, marker='^', ms=5, label='M2同比 (C002)')
    ax3.axhline(0, color=SUB, lw=0.9, ls=':', alpha=0.5)
    lo3, hi3 = min(ya + yb), max(ya + yb)
    ax3.set_ylim(lo3 - 2.6, hi3 + 2.6)
    ax3.set_xlim(xa[0], add_months(cred_axis_end, 3))
    for x_, y_, c in [(xa, ya, C1), (xb, yb, C5)]:
        ax3.annotate(f'{y_[-1]:.2f}%', xy=(x_[-1], y_[-1]), xytext=(10, 0),
                     textcoords='offset points', color=c, fontsize=11,
                     fontweight='bold', va='center')
    draw_phases(ax3, credit_trans, ax3.get_ylim()[0], ax3.get_ylim()[1], cred_end,
                label_y=lo3 - 1.5, est_next=CREDIT_NEXT.get(cur_cred), est_month=cred_est,
                overdue_months=cred_overdue, next_phase=CREDIT_NEXT.get(cur_cred))
    ax3.legend(loc='upper right', bbox_to_anchor=(1.0, 0.90), facecolor=PANEL, edgecolor=GRID,
               labelcolor=TXT, fontsize=9.5, framealpha=0.95)
    month_axis(ax3, 3)

    # ===== ④ 阶段时间轴（甘特） =====
    ax4 = fig.add_subplot(gs[3])
    style(ax4, '④ 阶段时间轴　★ = 已发生的切换点　☆ = 预估切换点',
          '库存周期在上、信贷周期在下，色块长度 = 该阶段持续月数')
    ax4.set_facecolor(PANEL)
    ax4.grid(True, axis='x', color=GRID, linestyle='--', linewidth=0.7, alpha=0.55)
    ax4.set_axisbelow(True)
    for s in ax4.spines.values():
        s.set_color(GRID)
    ax4.tick_params(colors=SUB, labelsize=9)

    rows = [(1, inv_trans, inv_end, inv_est, '库存周期'), (0, credit_trans, cred_end, cred_est, '信贷周期')]
    for ypos, trans, end_dt, est_dt, row_name in rows:
        shown = trans[-7:]
        for i, (start_str, phase, months) in enumerate(shown):
            y, m = map(int, start_str.split('-'))
            x0 = datetime(y, m, 1)
            if i == len(shown) - 1:
                x1 = end_dt
            else:
                ny, nm = map(int, shown[i + 1][0].split('-'))
                x1 = datetime(ny, nm, 1)
            if x1 <= x0:
                continue
            col = PHASE_COLOR.get(phase, SUB)
            ax4.barh(ypos, (x1 - x0).days, left=x0, height=0.5, color=col,
                     alpha=0.8, edgecolor=BG, linewidth=1.2)
            if (x1 - x0).days > 70:
                ax4.text(x0 + (x1 - x0) / 2, ypos, phase, ha='center', va='center',
                         color='#0e1117', fontsize=9, fontweight='bold')
        # 下一阶段：无论是否已出预估月份，都在末尾画一段虚框把"下一阶段叫什么"标出来
        nxt_name = (INV_NEXT if row_name == '库存周期' else CREDIT_NEXT).get(shown[-1][1], '—')
        nxt_col = PHASE_COLOR.get(nxt_name, SUB)
        if est_dt is not None:
            ext_end = est_dt
        else:
            # 已超历史均值：给一段 6 个月的示意区间，仅表示"接下来是它"，不是预测月份
            ext_end = end_dt + pd.DateOffset(months=6)
        ax4.barh(ypos, (ext_end - end_dt).days, left=end_dt, height=0.5,
                 color=nxt_col, alpha=0.22, edgecolor=nxt_col, linewidth=1.2, linestyle='--')
        ax4.text(end_dt + (ext_end - end_dt) / 2, ypos, f'下一阶段\n{nxt_name}',
                 ha='center', va='center', color=nxt_col, fontsize=8.2, fontweight='bold')
        if est_dt is not None:
            ax4.plot([est_dt], [ypos + 0.34], marker='*', ms=15, color='#ffd166',
                     markeredgecolor=BG, markeredgewidth=0.8, zorder=6, clip_on=False)

    ax4.set_yticks([1, 0])
    ax4.set_yticklabels(['库存周期', '信贷周期'], color=TXT, fontsize=10.5, fontweight='bold')
    ax4.set_ylim(-0.75, 1.75)
    ax4.set_xlim(min(i009[0][0], xa[0]), max(inv_axis_end, cred_axis_end) + pd.Timedelta(days=45))
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%y-%m'))
    ax4.xaxis.set_major_locator(mdates.MonthLocator(interval=6))

    # ===== 结论条 =====
    inv_next = INV_NEXT.get(cur_inv, '—')
    cred_next = CREDIT_NEXT.get(cur_cred, '—')
    concl = (
        f'库存周期：{cur_inv}（{inv_trans[-1][0]} 起，已 {cur_inv_months} 个月，历史均值 {inv_avg} 个月）'
        f'　→　{(inv_est.strftime("%Y-%m") + " 前后切换为 " + inv_next) if inv_est else (f"已超均值 {inv_overdue} 个月，切换条件未满足（下一阶段 {inv_next}）")}'
        f'　　信贷周期：{cur_cred}（{credit_trans[-1][0]} 起，已 {cur_cred_months} 个月，历史均值 {cred_avg} 个月）'
        f'　→　{(cred_est.strftime("%Y-%m") + " 前后切换为 " + cred_next) if cred_est else (f"已超均值 {cred_overdue} 个月，切换条件未满足（下一阶段 {cred_next}）")}'
    )
    fig.text(0.075, 0.968, '库存周期 & 信贷周期 监控曲线', color='#ffffff',
             fontsize=21, fontweight='bold', va='top')
    fig.text(0.075, 0.014,
             f'生成 {datetime.now().strftime("%Y-%m-%d %H:%M")}　│　数据源 国家统计局 / 中国人民银行　│　'
             f'库存数据截止 {inv_end.strftime("%Y-%m")}，信贷数据截止 {cred_end.strftime("%Y-%m")}',
             color=SUB, fontsize=9.2, va='bottom')

    out = os.path.join(OUT_DIR, f'{datetime.now().strftime("%Y%m%d")}_周期曲线图.png')
    fig.savefig(out, dpi=130, facecolor=BG)
    plt.close(fig)
    print('saved:', out)
    print('库存:', cur_inv, cur_inv_months, '月 ->', inv_next,
          inv_est.strftime('%Y-%m') if inv_est else f'已超均值{inv_overdue}月')
    print('信贷:', cur_cred, cur_cred_months, '月 ->', cred_next,
          cred_est.strftime('%Y-%m') if cred_est else f'已超均值{cred_overdue}月')
    return out


if __name__ == '__main__':
    main()
