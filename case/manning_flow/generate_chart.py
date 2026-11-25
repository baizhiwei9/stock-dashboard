#!/usr/bin/env python3
"""用PIL生成回测图表 - 双字体方案"""

import pandas as pd
import numpy as np
import json
import os
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import subprocess

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# 查找中文字体 - 直接用fc-list获取文件路径
result = subprocess.run(['fc-list', ':lang=zh', 'file'], capture_output=True, text=True)
zh_font_path = None
for line in result.stdout.strip().split('\n'):
    path = line.split(':')[0].strip()
    if path and os.path.exists(path):
        zh_font_path = path
        break

en_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

print(f"中文字体: {zh_font_path}")
print(f"英文字体: {en_font_path}")

# 用matplotlib + PIL双字体方案
font_prop = FontProperties(fname=zh_font_path)
# 注册字体到matplotlib
from matplotlib.font_manager import fontManager
fontManager.addfont(zh_font_path)
font_name = font_prop.get_name()
plt.rcParams['font.family'] = font_name
plt.rcParams['axes.unicode_minus'] = False
print(f"注册字体: {font_name} -> {zh_font_path}")

# 加载数据
nav_df = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_nav.csv"), index_col=0, parse_dates=True)
with open(os.path.join(OUTPUT_DIR, "backtest_metrics.json"), 'r', encoding='utf-8') as f:
    metrics = json.load(f)

holdings_df = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_holdings.csv"))
weekly_ret = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_weekly_returns.csv"), index_col=0, parse_dates=True)

# 创建图表
fig = plt.figure(figsize=(16, 20), facecolor='white')

# ============================================================
# 图1: 净值曲线
# ============================================================
ax1 = plt.subplot2grid((5, 2), (0, 0), colspan=2, rowspan=2)

colors = {
    '\u7efc\u5408\u56e0\u5b50(\u884c\u4e1a\u6307\u6570)': '#E03328',
    '\u7efc\u5408\u56e0\u5b50(ETF\u5b9e\u9645)': '#FF8C00',
    '\u7b49\u6743\u57fa\u51c6': '#2E86C1',
}

for col in nav_df.columns:
    color = colors.get(col, '#888888')
    lw = 2.5 if '\u7efc\u5408' in col else 1.5
    ls = '-' if '\u7efc\u5408' in col else '--'
    ax1.plot(nav_df.index, nav_df[col], label=col, color=color, linewidth=lw, linestyle=ls)

ax1.axhline(y=1.0, color='gray', linewidth=0.5, linestyle=':')
ax1.set_title('\u884c\u4e1a\u8d44\u91d1\u6d41\u5411\u56fe\u8f6e\u52a8\u7b56\u7565 - 2026\u5e74YTD\u51c0\u503c\u66f2\u7ebf', fontsize=16, fontproperties=font_prop, fontweight='bold')
ax1.set_ylabel('\u51c0\u503c', fontsize=12, fontproperties=font_prop)
ax1.legend(prop=font_prop, fontsize=11, loc='upper right')
ax1.grid(True, alpha=0.3)
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
ax1.xaxis.set_major_locator(mdates.MonthLocator())

for col in nav_df.columns:
    final_nav = nav_df[col].iloc[-1]
    ret = (final_nav - 1) * 100
    ax1.annotate(f'{ret:+.1f}%', 
                xy=(nav_df.index[-1], final_nav),
                xytext=(10, 0), textcoords='offset points',
                fontsize=10, fontproperties=font_prop,
                color=colors.get(col, '#888888'), fontweight='bold')

# ============================================================
# 图2: 回撤曲线
# ============================================================
ax2 = plt.subplot2grid((5, 2), (2, 0), colspan=2)

for col in nav_df.columns:
    color = colors.get(col, '#888888')
    peak = nav_df[col].expanding().max()
    drawdown = (nav_df[col] - peak) / peak * 100
    lw = 2 if '\u7efc\u5408' in col else 1.2
    ax2.fill_between(drawdown.index, drawdown.values, 0, alpha=0.15, color=color)
    ax2.plot(drawdown.index, drawdown.values, label=col, color=color, linewidth=lw)

ax2.set_title('\u56de\u64a4\u66f2\u7ebf', fontsize=13, fontproperties=font_prop)
ax2.set_ylabel('\u56de\u64a4 (%)', fontsize=11, fontproperties=font_prop)
ax2.legend(prop=font_prop, fontsize=9, loc='lower left')
ax2.grid(True, alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
ax2.xaxis.set_major_locator(mdates.MonthLocator())

# ============================================================
# 图3: 周度收益分布
# ============================================================
ax3 = plt.subplot2grid((5, 2), (3, 0))

for col in weekly_ret.columns:
    color = colors.get(col, '#888888')
    ax3.hist(weekly_ret[col].dropna(), bins=15, alpha=0.5, label=col, color=color, edgecolor='white')

ax3.axvline(x=0, color='gray', linewidth=0.5, linestyle=':')
ax3.set_title('\u5468\u5ea6\u6536\u76ca\u5206\u5e03', fontsize=13, fontproperties=font_prop)
ax3.set_xlabel('\u5468\u6536\u76ca\u7387', fontsize=11, fontproperties=font_prop)
ax3.set_ylabel('\u9891\u6570', fontsize=11, fontproperties=font_prop)
ax3.legend(prop=font_prop, fontsize=9)

# ============================================================
# 图4: 各行业被选次数
# ============================================================
ax4 = plt.subplot2grid((5, 2), (3, 1))

industry_counts = holdings_df['industry'].value_counts()
etf_names = holdings_df.groupby('industry')['etf_name'].first()
bar_colors = ['#E03328' if i < 5 else '#FF8C00' if i < 10 else '#2E86C1' if i < 15 else '#888888' 
              for i in range(len(industry_counts))]
bars = ax4.barh(range(len(industry_counts)), industry_counts.values, color=bar_colors, edgecolor='white')
ax4.set_yticks(range(len(industry_counts)))
labels = [f"{ind}({etf_names[ind]})" for ind in industry_counts.index]
ax4.set_yticklabels(labels, fontsize=8, fontproperties=font_prop)
ax4.set_xlabel('\u88ab\u9009\u6b21\u6570', fontsize=11, fontproperties=font_prop)
ax4.set_title('\u884c\u4e1a\u88ab\u9009\u6b21\u6570 (Top5\u5468\u5ea6\u8c03\u4ed3)', fontsize=13, fontproperties=font_prop)
ax4.invert_yaxis()
for bar, val in zip(bars, industry_counts.values):
    ax4.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height()/2, str(val), 
             va='center', fontsize=8, fontproperties=font_prop)

# ============================================================
# 图5: 关键指标表格
# ============================================================
ax5 = plt.subplot2grid((5, 2), (4, 0), colspan=2)
ax5.axis('off')

metric_labels = ['\u7d2f\u8ba1\u6536\u76ca', '\u5e74\u5316\u6536\u76ca', '\u5e74\u5316\u6ce2\u52a8', '\u590f\u666e\u6bd4\u7387', '\u6700\u5927\u56de\u64a4', '\u80dc\u7387', 'Calmar']
col_names = [m['name'] for m in metrics]
table_data = []
for key in ['total_return', 'annualized_return', 'annualized_vol', 'sharpe', 'max_drawdown', 'win_rate', 'calmar']:
    row = [m.get(key, 'N/A') for m in metrics]
    table_data.append(row)

table = ax5.table(cellText=table_data,
                  rowLabels=metric_labels,
                  colLabels=col_names,
                  cellLoc='center',
                  loc='center',
                  colWidths=[0.18]*len(col_names))

table.auto_set_font_size(False)
table.set_fontsize(10)
table.scale(1, 1.8)

for i in range(len(metric_labels) + 1):
    for j in range(len(col_names)):
        cell = table[i, j]
        if i == 0:
            cell.set_facecolor('#2C3E50')
            cell.set_text_props(color='white', fontproperties=font_prop, fontweight='bold')
        else:
            if j == 0:
                cell.set_facecolor('#FADBD8')
            elif j == 3:
                cell.set_facecolor('#D6EAF8')
            else:
                cell.set_facecolor('#F9F9F9')
            cell.set_text_props(fontproperties=font_prop)

for i in range(len(metric_labels)):
    cell = table[i+1, -1]
    cell.set_text_props(fontproperties=font_prop, fontweight='bold')

ax5.set_title('\u56de\u6d4b\u6307\u6807\u6c47\u603b', fontsize=14, fontproperties=font_prop, fontweight='bold', pad=20)

plt.tight_layout(pad=2.0)
chart_path = os.path.join(OUTPUT_DIR, "backtest_chart.png")
plt.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor='white')
plt.close()

# 验证图片
img = Image.open(chart_path)
print(f"\n图表已保存: {chart_path}")
print(f"图片尺寸: {img.size}")
