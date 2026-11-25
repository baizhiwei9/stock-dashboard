#!/usr/bin/env python3
"""
结构因子可视化模块
绘制太极图、指标序列图、区间判断图

字体方案：matplotlib 3.11 支持 font.family 回退链
设置 ['Droid Sans Fallback', 'DejaVu Sans'] 后，中文走 DroidSansFallback，
ASCII（数字/字母/标点）自动回退到 DejaVu Sans，彻底解决方框乱码问题。
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Circle, Wedge
import matplotlib.patches as mpatches
from matplotlib.font_manager import FontProperties
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import os

# ==================== 字体配置 ====================
CJK_FONT_PATH = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
DEJAVU_FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

# 注册字体到 matplotlib 字体管理器
fm.fontManager.addfont(CJK_FONT_PATH)
fm.fontManager.addfont(DEJAVU_FONT_PATH)

# 设置字体回退链：中文 -> Droid Sans Fallback, ASCII -> DejaVu Sans
plt.rcParams['font.family'] = ['Droid Sans Fallback', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# FontProperties 实例（用于显式指定时的兼容）
fp = FontProperties(family=['Droid Sans Fallback', 'DejaVu Sans'])
fp_bold = FontProperties(family=['Droid Sans Fallback', 'DejaVu Sans'], size=14)
fp_small = FontProperties(family=['Droid Sans Fallback', 'DejaVu Sans'], size=8)

# 辅助函数：设置坐标轴中文标签
def set_cn_labels(ax, title=None, xlabel=None, ylabel=None, font=fp):
    if title:
        ax.set_title(title, fontproperties=font)
    if xlabel:
        ax.set_xlabel(xlabel, fontproperties=font)
    if ylabel:
        ax.set_ylabel(ylabel, fontproperties=font)

# 辅助函数：设置刻度标签中文
def set_cn_ticks(ax, font=fp):
    for label in ax.get_xticklabels():
        label.set_fontproperties(font)
    for label in ax.get_yticklabels():
        label.set_fontproperties(font)

# 辅助函数：创建中文图例
def cn_legend(ax, loc='best', font=fp, **kwargs):
    leg = ax.legend(loc=loc, prop=font, **kwargs)
    return leg


def plot_taiji(av_values, taiji_info, current_date=None, width=600, height=600):
    """
    绘制太极图 - 多空能量可视化
    
    白鱼 = 多头能量（阳）
    黑鱼 = 空头能量（阴）
    鱼眼 = 陷阱位（多头中的空头陷阱 / 空头中的多头陷阱）
    """
    fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-1.3, 1.3)
    ax.set_aspect('equal')
    ax.axis('off')
    
    # 计算能量比例
    white_area = taiji_info['white_fish_area']
    black_area = taiji_info['black_fish_area']
    total = white_area + black_area
    ratio = white_area / (total + 1e-8)
    
    # 太极圆外框
    circle = Circle((0, 0), 1, fill=False, color='black', linewidth=2)
    ax.add_patch(circle)
    
    # 白鱼（右半圆 - 阳/多头）
    if ratio > 0:
        white_wedge = Wedge((0, 0), 1, -90, 90, facecolor='white',
                           edgecolor='black', linewidth=2)
        ax.add_patch(white_wedge)
        
        if taiji_info.get('white_eye_idx'):
            white_eye = Circle((0, 0.5), 0.15, facecolor='white',
                              edgecolor='black', linewidth=1)
            ax.add_patch(white_eye)
            black_pupil = Circle((0, 0.5), 0.05, facecolor='black')
            ax.add_patch(black_pupil)
    
    # 黑鱼（左半圆 - 阴/空头）
    if ratio < 1:
        black_wedge = Wedge((0, 0), 1, 90, 270, facecolor='black',
                           edgecolor='black', linewidth=2)
        ax.add_patch(black_wedge)
        
        if taiji_info.get('black_eye_idx'):
            black_eye = Circle((0, -0.5), 0.15, facecolor='black',
                              edgecolor='white', linewidth=1)
            ax.add_patch(black_eye)
            white_pupil = Circle((0, -0.5), 0.05, facecolor='white')
            ax.add_patch(white_pupil)
    
    # 标题
    dominant = taiji_info['dominant']
    if dominant == 'bull':
        title_text = f'太极图 - 多头主导 ({ratio*100:.1f}%)'
    elif dominant == 'bear':
        title_text = f'太极图 - 空头主导 ({(1-ratio)*100:.1f}%)'
    else:
        title_text = f'太极图 - 力量平衡 ({ratio*100:.1f}% vs {(1-ratio)*100:.1f}%)'
    
    ax.text(0, 1.15, title_text, ha='center', va='center',
            fontsize=14, fontproperties=fp_bold)
    
    # 日期标注
    if current_date:
        date_text = f'日期: {current_date}'
        ax.text(0, -1.2, date_text, ha='center', va='center',
                fontsize=10, fontproperties=fp)
    
    # 图例
    legend_elements = [
        mpatches.Patch(facecolor='white', edgecolor='black',
                       label=f'白鱼(多头能量): {white_area:.2f}'),
        mpatches.Patch(facecolor='black', edgecolor='white',
                       label=f'黑鱼(空头能量): {black_area:.2f}')
    ]
    ax.legend(handles=legend_elements, loc='lower center',
              bbox_to_anchor=(0.5, -0.12), prop=fp)
    
    plt.tight_layout()
    
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', facecolor='white')
    buf.seek(0)
    img = Image.open(buf).convert('RGB')
    plt.close(fig)
    
    return img


def plot_indicators(prices, rv_values, av_values, rv_peaks, av_crosses,
                    dates=None, width=1200, height=800):
    """
    绘制指标序列图 - 4合1综合面板
    
    面板1: 价格走势
    面板2: 筹码结构斜率（相对值）
    面板3: 资金流向（绝对值）
    面板4: 市场区间判断
    """
    fig, axes = plt.subplots(4, 1, figsize=(width/100, height/100), dpi=100)
    
    # 生成日期序列
    if dates is None:
        end_date = pd.Timestamp.now()
        dates = pd.date_range(end=end_date, periods=len(prices), freq='D')
    
    # ==================== 面板1: 价格走势 ====================
    ax1 = axes[0]
    ax1.plot(dates, prices, 'b-', linewidth=1, label='收盘价')
    set_cn_labels(ax1, title='结构因子综合分析图', ylabel='价格')
    cn_legend(ax1, loc='upper left')
    ax1.grid(True, alpha=0.3)
    set_cn_ticks(ax1)
    
    # ==================== 面板2: 筹码结构斜率（相对值）====================
    ax2 = axes[1]
    ax2.plot(dates, rv_values, 'r-', linewidth=1.5, label='筹码结构斜率(相对值)')
    ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.5)
    ax2.axhline(y=0.5, color='orange', linestyle='--', linewidth=0.5,
                alpha=0.5, label='阈值线')
    
    # 标记波峰 - 第一峰（红线）
    if rv_peaks.get('first_peak_idx'):
        first_date = dates[rv_peaks['first_peak_idx']]
        ax2.axvline(x=first_date, color='red', linestyle=':',
                   linewidth=1.5, alpha=0.8, label='第一峰(红线)')
    
    # 标记波峰 - 第二峰（粉线）
    if rv_peaks.get('second_peak_idx'):
        second_date = dates[rv_peaks['second_peak_idx']]
        ax2.axvline(x=second_date, color='hotpink', linestyle=':',
                   linewidth=1.5, alpha=0.8, label='第二峰(粉线)')
    
    set_cn_labels(ax2, ylabel='筹码结构斜率')
    cn_legend(ax2, loc='upper right')
    ax2.grid(True, alpha=0.3)
    set_cn_ticks(ax2)
    
    # ==================== 面板3: 资金流向（绝对值）====================
    ax3 = axes[2]
    ax3.plot(dates, av_values, 'g-', linewidth=1.5, label='资金流向(绝对值)')
    ax3.axhline(y=0, color='gray', linestyle='-', linewidth=1)
    
    # 多头区（绿色填充）
    ax3.fill_between(dates, 0, av_values, where=(av_values > 0),
                     color='green', alpha=0.3, label='多头区')
    # 空头区（红色填充）
    ax3.fill_between(dates, 0, av_values, where=(av_values < 0),
                     color='red', alpha=0.3, label='空头区')
    
    # 标记零轴穿越
    for cross in av_crosses:
        if cross['index'] < len(dates):
            cross_date = dates[cross['index']]
            if cross['type'] == 'up_cross':
                ax3.axvline(x=cross_date, color='green', linestyle=':',
                           linewidth=1, alpha=0.7)
            else:
                ax3.axvline(x=cross_date, color='red', linestyle=':',
                           linewidth=1, alpha=0.7)
    
    set_cn_labels(ax3, ylabel='资金流向')
    cn_legend(ax3, loc='upper right')
    ax3.grid(True, alpha=0.3)
    set_cn_ticks(ax3)
    
    # ==================== 面板4: 市场区间判断 ====================
    ax4 = axes[3]
    interval_signal = np.zeros(len(prices))
    for i in range(len(prices)):
        if i < len(rv_values):
            rv_val = rv_values[i]
            av_val = av_values[i] if i < len(av_values) else 0
            if rv_val > 0.3 and av_val < -0.2:
                interval_signal[i] = -1  # 区间一（风险释放）
            elif rv_val < 0.2 and av_val > 0.1:
                interval_signal[i] = 1   # 区间二（修复反弹）
            else:
                interval_signal[i] = 0   # 过渡阶段
    
    colors = ['red' if s == -1 else 'green' if s == 1 else 'gray'
              for s in interval_signal]
    ax4.bar(dates, interval_signal, color=colors, alpha=0.6, width=0.8)
    ax4.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    
    set_cn_labels(ax4, ylabel='市场区间', xlabel='日期')
    ax4.set_yticks([-1, 0, 1])
    ax4.set_yticklabels(['区间一\n(风险释放)', '过渡阶段', '区间二\n(修复反弹)'],
                        fontproperties=fp)
    ax4.grid(True, alpha=0.3)
    set_cn_ticks(ax4)
    
    # 统一x轴日期格式
    for ax in axes:
        ax.tick_params(axis='x', rotation=45)
        for label in ax.get_xticklabels():
            label.set_fontproperties(fp_small)
    
    plt.tight_layout()
    
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', facecolor='white')
    buf.seek(0)
    img = Image.open(buf)
    plt.close(fig)
    
    return img


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from structural_factors import IntervalDetector, analyze_market, generate_report
    from data_fetcher import fetch_csi_all_index

    # 获取中证全A（000985）真实行情数据，取最近120个交易日
    data = fetch_csi_all_index(days=120, source='tx')
    prices = data['prices']
    volumes = data['volumes']
    dates = data['dates']

    print(f"日期数量: {len(dates)}, 价格数量: {len(prices)}")

    # 计算因子
    detector = IntervalDetector()
    result = detector.analyze(prices, volumes)

    # 一键分析（含赔率模型+胜率模型信号）
    market = analyze_market(prices, volumes)

    # 当前日期
    current_date = dates.iloc[-1].strftime('%Y-%m-%d')

    # 绘制太极图（带日期）
    taiji_img = plot_taiji(result['absolute_value']['values'], result['taiji'],
                           current_date=current_date)
    taiji_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'taiji_chart.png')
    taiji_img.save(taiji_path)
    print(f"太极图已保存: {taiji_path}")

    # 绘制指标序列图（带日期）
    indicators_img = plot_indicators(
        prices,
        result['relative_value']['values'],
        result['absolute_value']['values'],
        result['relative_value']['peaks'],
        result['absolute_value']['crosses'],
        dates=dates
    )
    indicators_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'indicators_chart.png')
    indicators_img.save(indicators_path)
    print(f"指标序列图已保存: {indicators_path}")
    print(f"数据日期范围: {dates.iloc[0].strftime('%Y-%m-%d')} 至 {dates.iloc[-1].strftime('%Y-%m-%d')}")
    print(f"当前日期标注: {current_date}")

    # 生成文本报告
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'structural_factor_report.md')
    report = generate_report(prices, volumes, dates=dates, output_file=report_path)
    print(f"文本报告已保存: {report_path}")
    print(f"\n综合建议: {market['summary']}")
