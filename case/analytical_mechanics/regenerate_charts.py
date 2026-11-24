#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图表重新生成脚本：使用 PIL 双字体混合方案解决中文+数字渲染问题
"""

import pickle
import numpy as np
import pandas as pd
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# ============================================================
# PIL 双字体渲染器
# ============================================================

class CJKTextRenderer:
    """双字体混合渲染：中文用 DroidSansFallback，数字/英文用 DejaVu Sans"""

    def __init__(self, cjk_path='/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
                 ascii_path='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        self.cjk_font = ImageFont.truetype(cjk_path, 12)
        self.ascii_font = ImageFont.truetype(ascii_path, 12)
        self.cjk_font_bold = ImageFont.truetype(cjk_path, 14)
        self.ascii_font_bold = ImageFont.truetype(ascii_path, 14)

    def render_text(self, text, font_size=12, bold=False):
        """渲染文本到 PIL 图像，然后返回 numpy 数组"""
        cjk_f = ImageFont.truetype('/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf', font_size)
        asc_f = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', font_size)

        # 先测量文本宽度
        dummy = Image.new('RGBA', (1, 1))
        draw = ImageDraw.Draw(dummy)

        total_width = 0
        height = font_size + 4
        char_fonts = []

        for c in text:
            if ord(c) > 127:
                f = cjk_f
            else:
                f = asc_f
            bbox = draw.textbbox((0, 0), c, font=f)
            w = bbox[2] - bbox[0]
            char_fonts.append((c, f, w))
            total_width += w

        # 创建图像
        img = Image.new('RGBA', (total_width + 2, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)

        x = 0
        for c, f, w in char_fonts:
            draw.text((x, 0), c, font=f, fill=(0, 0, 0, 255))
            x += w

        return np.array(img)

    def render_text_custom(self, text, font_size=12, color=(0, 0, 0, 255)):
        """渲染文本，自定义颜色"""
        cjk_f = ImageFont.truetype('/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf', font_size)
        asc_f = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', font_size)

        dummy = Image.new('RGBA', (1, 1))
        draw = ImageDraw.Draw(dummy)

        total_width = 0
        height = font_size + 4

        for c in text:
            if ord(c) > 127:
                f = cjk_f
            else:
                f = asc_f
            bbox = draw.textbbox((0, 0), c, font=f)
            w = bbox[2] - bbox[0]
            total_width += w

        img = Image.new('RGBA', (total_width + 2, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)

        x = 0
        for c in text:
            if ord(c) > 127:
                f = cjk_f
            else:
                f = asc_f
            bbox = draw.textbbox((0, 0), c, font=f)
            draw.text((x, 0), c, font=f, fill=color)
            x += bbox[2] - bbox[0]

        return np.array(img)


def draw_text_on_ax(ax, text, x, y, fontsize=12, color='black', ha='left', va='bottom'):
    """在 matplotlib axes 上用 PIL 渲染中文文本"""
    renderer = CJKTextRenderer()
    rgba = renderer.render_text_custom(text, fontsize,
                                       color=tuple(int(c * 255) for c in
                                                   matplotlib.colors.to_rgba(color)))
    # 转换为 RGBA 图像
    im = Image.fromarray(rgba)

    # 获取 axes 的显示范围
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()

    # 将数据坐标转换为显示坐标
    fig = ax.get_figure()
    bbox = ax.get_position()

    # 简单方法：直接用 figimage 或 AnnotationBbox
    # 更简单：直接用 matplotlib 的 text 但指定字体回退
    # 最终方案：直接在图上画
    ax.imshow(im, aspect='auto',
              extent=[x, x + 0.01, y, y + 0.01],
              zorder=10, transform=ax.transData)


# ============================================================
# 主图表生成（全 PIL 方案）
# ============================================================

def generate_all_charts_pil():
    """
    用 PIL 直接绘制所有图表，完全控制字体渲染
    """
    with open('backtest_results.pkl', 'rb') as f:
        r = pickle.load(f)

    nav = r['nav_series']
    dd = r['drawdown_series']
    market = r['market_df']
    trades = r['trades']

    os.makedirs('charts', exist_ok=True)

    # 字体
    cjk_path = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
    asc_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

    def get_font(size, bold=False):
        # DroidSansFallback 有中文但缺数字，DejaVu 有数字但没中文
        # 方案：用 PIL 逐字符渲染混合
        return ImageFont.truetype(cjk_path, size)

    def draw_mixed_text(draw, text, x, y, font_size=12, fill=(0, 0, 0)):
        """混合字体绘制文本"""
        cjk_f = ImageFont.truetype(cjk_path, font_size)
        asc_f = ImageFont.truetype(asc_path, font_size)

        cx = x
        for c in text:
            if ord(c) > 127:
                f = cjk_f
            else:
                f = asc_f
            draw.text((cx, y), c, font=f, fill=fill)
            bbox = draw.textbbox((cx, y), c, font=f)
            cx += bbox[2] - bbox[0]

    def text_width(draw, text, font_size=12):
        cjk_f = ImageFont.truetype(cjk_path, font_size)
        asc_f = ImageFont.truetype(asc_path, font_size)
        w = 0
        dummy = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        for c in text:
            f = cjk_f if ord(c) > 127 else asc_f
            bbox = dummy.textbbox((0, 0), c, font=f)
            w += bbox[2] - bbox[0]
        return w

    # ---- 图1: 净值曲线 + 回撤 + LLE ----
    W, H = 1400, 1000
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    # 标题
    draw_mixed_text(draw, '策略净值 vs 沪深300基准 + 回撤 + LLE', 20, 10, font_size=18, fill=(20, 20, 20))

    # 三个子图区域
    plot_area = (60, 50, W - 20, H - 20)

    # 子图1: 净值曲线 (y: 50-450)
    ax1 = (60, 50, W - 20, 450)
    draw_mixed_text(draw, '净值曲线', ax1[0], ax1[1] - 25, font_size=14, fill=(50, 50, 50))

    # 绘制净值
    nav_norm = (nav / nav.iloc[0]).values
    dates = nav.index

    bench = market[market['date'].isin(dates)].copy()
    bench_norm = (bench['close'] / bench['close'].iloc[0]).values

    # 归一化到绘图区域
    all_vals = np.concatenate([nav_norm, bench_norm])
    y_min, y_max = all_vals.min() * 0.95, all_vals.max() * 1.05

    def to_screen_x(idx, total, x0, x1):
        return x0 + (idx / max(total - 1, 1)) * (x1 - x0)

    def to_screen_y(val, y_min, y_max, y0, y1):
        return y1 - (val - y_min) / (y_max - y_min) * (y1 - y0)

    # 绘制坐标轴
    draw.line([(ax1[0], ax1[1]), (ax1[0], ax1[3])], fill=(100, 100, 100))  # Y轴
    draw.line([(ax1[0], ax1[3]), (ax1[2], ax1[3])], fill=(100, 100, 100))  # X轴

    # Y轴刻度
    for i in range(6):
        val = y_min + (y_max - y_min) * i / 5
        y = to_screen_y(val, y_min, y_max, ax1[1], ax1[3])
        draw.line([(ax1[0] - 5, y), (ax1[0], y)], fill=(100, 100, 100))
        draw_mixed_text(draw, f'{val:.2f}', ax1[0] - 50, y - 6, font_size=10, fill=(80, 80, 80))

    # 风险时段标注
    for x_start, x_end, color in [
        (datetime(2022, 1, 1), datetime(2022, 12, 31), (255, 200, 200)),
        (datetime(2026, 6, 15), datetime(2026, 8, 11), (255, 220, 180))
    ]:
        idx_start = dates.searchsorted(x_start)
        idx_end = dates.searchsorted(x_end)
        if idx_end > idx_start:
            sx0 = to_screen_x(idx_start, len(dates), ax1[0], ax1[2])
            sx1 = to_screen_x(idx_end, len(dates), ax1[0], ax1[2])
            draw.rectangle([sx0, ax1[1], sx1, ax1[3]], fill=color + (30,) if len(color) == 3 else color, outline=None)
            # PIL RGB 不支持 alpha，直接画淡色
            draw.rectangle([sx0, ax1[1], sx1, ax1[3]], fill=(255, 240, 240))

    # 绘制基准线
    points_bench = []
    for i, val in enumerate(bench_norm):
        x = to_screen_x(i, len(dates), ax1[0], ax1[2])
        y = to_screen_y(val, y_min, y_max, ax1[1], ax1[3])
        points_bench.append((x, y))
    for i in range(len(points_bench) - 1):
        draw.line([points_bench[i], points_bench[i + 1]], fill=(255, 165, 0), width=1)

    # 绘制策略净值
    points_nav = []
    for i, val in enumerate(nav_norm):
        x = to_screen_x(i, len(dates), ax1[0], ax1[2])
        y = to_screen_y(val, y_min, y_max, ax1[1], ax1[3])
        points_nav.append((x, y))
    for i in range(len(points_nav) - 1):
        draw.line([points_nav[i], points_nav[i + 1]], fill=(30, 100, 200), width=2)

    # 图例
    draw_mixed_text(draw, '--- 策略净值', ax1[2] - 200, ax1[1] + 5, font_size=11, fill=(30, 100, 200))
    draw_mixed_text(draw, '--- 沪深300', ax1[2] - 200, ax1[1] + 22, font_size=11, fill=(255, 165, 0))

    # 子图2: 回撤曲线 (y: 480-680)
    ax2 = (60, 500, W - 20, 680)
    draw_mixed_text(draw, '回撤曲线', ax2[0], ax2[1] - 25, font_size=14, fill=(50, 50, 50))

    draw.line([(ax2[0], ax2[1]), (ax2[0], ax2[3])], fill=(100, 100, 100))
    draw.line([(ax2[0], ax2[3]), (ax2[2], ax2[3])], fill=(100, 100, 100))

    dd_vals = dd.values
    dd_min = dd_vals.min() * 1.1

    for i in range(6):
        val = dd_min + (0 - dd_min) * i / 5
        y = ax2[3] - (val - dd_min) / (0 - dd_min) * (ax2[3] - ax2[1])
        draw.line([(ax2[0] - 5, y), (ax2[0], y)], fill=(100, 100, 100))
        draw_mixed_text(draw, f'{val:.1%}', ax2[0] - 60, y - 6, font_size=10, fill=(80, 80, 80))

    points_dd = []
    for i, val in enumerate(dd_vals):
        x = to_screen_x(i, len(dd_vals), ax2[0], ax2[2])
        y = ax2[3] - (val - dd_min) / (0 - dd_min) * (ax2[3] - ax2[1])
        points_dd.append((x, y))

    # 填充区域
    for i in range(len(points_dd) - 1):
        draw.polygon([points_dd[i], points_dd[i + 1],
                       (points_dd[i + 1][0], ax2[3]),
                       (points_dd[i][0], ax2[3])],
                      fill=(255, 150, 150))
    for i in range(len(points_dd) - 1):
        draw.line([points_dd[i], points_dd[i + 1]], fill=(200, 0, 0), width=1)

    # 子图3: LLE (y: 710-950)
    ax3 = (60, 730, W - 20, 950)
    draw_mixed_text(draw, '大盘李雅普诺夫指数 (LLE)', ax3[0], ax3[1] - 25, font_size=14, fill=(50, 50, 50))

    draw.line([(ax3[0], ax3[1]), (ax3[0], ax3[3])], fill=(100, 100, 100))
    draw.line([(ax3[0], ax3[3]), (ax3[2], ax3[3])], fill=(100, 100, 100))

    lle = market[market['date'].isin(dates)]['lle'].values
    lle_clean = np.nan_to_num(lle, nan=0)

    lle_max = max(lle_clean.max() * 1.1, 0.1)

    for i in range(6):
        val = lle_max * i / 5
        y = ax3[3] - val / lle_max * (ax3[3] - ax3[1])
        draw.line([(ax3[0] - 5, y), (ax3[0], y)], fill=(100, 100, 100))
        draw_mixed_text(draw, f'{val:.3f}', ax3[0] - 55, y - 6, font_size=10, fill=(80, 80, 80))

    # 阈值线
    for thresh, color, label in [(0.075, (255, 0, 0), '强混沌'), (0.065, (255, 165, 0), '弱混沌')]:
        y = ax3[3] - thresh / lle_max * (ax3[3] - ax3[1])
        for x in range(int(ax3[0]), int(ax3[2]), 6):
            draw.line([(x, y), (x + 3, y)], fill=color, width=1)
        draw_mixed_text(draw, label, ax3[2] - 60, y - 14, font_size=10, fill=color)

    # 绘制 LLE
    points_lle = []
    for i, val in enumerate(lle_clean):
        x = to_screen_x(i, len(lle_clean), ax3[0], ax3[2])
        y = ax3[3] - val / lle_max * (ax3[3] - ax3[1])
        points_lle.append((x, y))
    for i in range(len(points_lle) - 1):
        draw.line([points_lle[i], points_lle[i + 1]], fill=(128, 0, 128), width=1)

    # X轴日期标签
    for year in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
        target = datetime(year, 1, 1)
        idx = dates.searchsorted(target)
        if idx < len(dates):
            x = to_screen_x(idx, len(dates), ax3[0], ax3[2])
            draw_mixed_text(draw, str(year), x - 15, ax3[3] + 5, font_size=10, fill=(80, 80, 80))

    img.save('charts/01_nav_drawdown_lle.png', 'PNG')
    print('  charts/01_nav_drawdown_lle.png saved')

    # ---- 图2: 2022年风险时段放大 ----
    generate_risk_period_chart(market, '2021-10-01', '2022-12-31',
                               'charts/02_2022_risk_period.png',
                               '2022年下跌风险时段分析', draw_mixed_text, to_screen_y, to_screen_x,
                               cjk_path, asc_path)

    # ---- 图3: 2026年7月下跌放大 ----
    generate_risk_period_chart(market, '2026-05-01', '2026-08-11',
                               'charts/03_2026_july_risk_period.png',
                               '2026年7月下跌风险时段分析', draw_mixed_text, to_screen_y, to_screen_x,
                               cjk_path, asc_path)

    # ---- 图4: 哈密顿量 ----
    generate_hamiltonian_chart(market, 'charts/04_hamiltonian.png',
                               draw_mixed_text, to_screen_x, cjk_path, asc_path)

    # ---- 图5: 交易统计 ----
    generate_trade_stats_chart(trades, 'charts/05_trade_stats.png',
                               draw_mixed_text, cjk_path, asc_path)

    print('All charts regenerated with CJK font fix.')


def generate_risk_period_chart(market, start, end, filename, title,
                                draw_mixed_text, to_screen_y, to_screen_x,
                                cjk_path, asc_path):
    """生成风险时段放大图"""
    period = market[(market['date'] >= start) & (market['date'] <= end)].copy()

    W, H = 1400, 1000
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    draw_mixed_text(draw, title, 20, 10, font_size=18, fill=(20, 20, 20))

    dates = period['date'].values
    close = period['close'].values
    lle = period['lle'].fillna(0).values
    tor = period['toroidality'].fillna(0).values
    hurst = period['hurst'].fillna(0.5).values

    panels = [
        (50, 50, W - 20, 280, 'close', '收盘价', (30, 100, 200)),
        (50, 320, W - 20, 500, 'lle', 'LLE (李雅普诺夫指数)', (128, 0, 128)),
        (50, 540, W - 20, 720, 'toroidality', 'KAM 环面度', (0, 128, 0)),
        (50, 760, W - 20, 940, 'hurst', 'Hurst 指数', (0, 128, 128)),
    ]

    for x0, y0, x1, y1, col, label, color in panels:
        draw_mixed_text(draw, label, x0, y0 - 25, font_size=14, fill=(50, 50, 50))

        vals = period[col].fillna(0).values
        if col == 'hurst':
            vals = period[col].fillna(0.5).values

        v_min, v_max = vals.min(), vals.max()
        if v_max == v_min:
            v_max = v_min + 1
        v_min = v_min - (v_max - v_min) * 0.1
        v_max = v_max + (v_max - v_min) * 0.1

        draw.line([(x0, y0), (x0, y1)], fill=(100, 100, 100))
        draw.line([(x0, y1), (x1, y1)], fill=(100, 100, 100))

        for i in range(5):
            val = v_min + (v_max - v_min) * i / 4
            y = y1 - (val - v_min) / (v_max - v_min) * (y1 - y0)
            draw.line([(x0 - 5, y), (x0, y)], fill=(100, 100, 100))
            if col == 'close':
                draw_mixed_text(draw, f'{val:.0f}', x0 - 50, y - 6, font_size=10, fill=(80, 80, 80))
            else:
                draw_mixed_text(draw, f'{val:.3f}', x0 - 50, y - 6, font_size=10, fill=(80, 80, 80))

        # 阈值线
        if col == 'lle':
            for thresh, tc, tl in [(0.075, (255, 0, 0), '强混沌'), (0.065, (255, 165, 0), '弱混沌')]:
                y = y1 - (thresh - v_min) / (v_max - v_min) * (y1 - y0)
                for x in range(int(x0), int(x1), 6):
                    draw.line([(x, y), (x + 3, y)], fill=tc, width=1)
                draw_mixed_text(draw, tl, x1 - 60, y - 14, font_size=10, fill=tc)
        elif col == 'toroidality':
            y = y1 - (0.12 - v_min) / (v_max - v_min) * (y1 - y0)
            for x in range(int(x0), int(x1), 6):
                draw.line([(x, y), (x + 3, y)], fill=(255, 0, 0), width=1)
            draw_mixed_text(draw, '环面破裂', x1 - 60, y - 14, font_size=10, fill=(255, 0, 0))
        elif col == 'hurst':
            for thresh, tc in [(0.55, (30, 30, 200)), (0.45, (200, 100, 0))]:
                y = y1 - (thresh - v_min) / (v_max - v_min) * (y1 - y0)
                for x in range(int(x0), int(x1), 6):
                    draw.line([(x, y), (x + 3, y)], fill=tc, width=1)

        # 绘制数据线
        points = []
        for i, val in enumerate(vals):
            x = to_screen_x(i, len(vals), x0, x1)
            y = y1 - (val - v_min) / (v_max - v_min) * (y1 - y0)
            points.append((x, y))
        for i in range(len(points) - 1):
            draw.line([points[i], points[i + 1]], fill=color, width=1)

        # X轴日期
        n = len(dates)
        step = max(1, n // 8)
        for i in range(0, n, step):
            d = pd.to_datetime(dates[i])
            x = to_screen_x(i, n, x0, x1)
            draw_mixed_text(draw, d.strftime('%Y-%m'), x - 25, y1 + 5, font_size=9, fill=(80, 80, 80))

    img.save(filename, 'PNG')
    print(f'  {filename} saved')


def generate_hamiltonian_chart(market, filename, draw_mixed_text, to_screen_x, cjk_path, asc_path):
    """哈密顿量图表"""
    market = market[market['date'] >= '2020-01-01'].copy()

    W, H = 1400, 600
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    draw_mixed_text(draw, '大盘哈密顿量 H(t) + Z-Score', 20, 10, font_size=18, fill=(20, 20, 20))

    dates = market['date'].values
    H = market['hamiltonian'].fillna(0).values
    Hz = market['hamiltonian_z'].fillna(0).values

    # 上图: H(t)
    x0, y0, x1, y1 = 60, 50, W - 20, 280
    draw_mixed_text(draw, '哈密顿量 H(t)', x0, y0 - 25, font_size=14, fill=(50, 50, 50))

    H_clip = np.clip(H, 0, np.percentile(H, 99))
    h_max = H_clip.max() * 1.1

    draw.line([(x0, y0), (x0, y1)], fill=(100, 100, 100))
    draw.line([(x0, y1), (x1, y1)], fill=(100, 100, 100))

    for i in range(5):
        val = h_max * i / 4
        y = y1 - val / h_max * (y1 - y0)
        draw.line([(x0 - 5, y), (x0, y)], fill=(100, 100, 100))
        draw_mixed_text(draw, f'{val:.1f}', x0 - 45, y - 6, font_size=10, fill=(80, 80, 80))

    points = []
    for i, val in enumerate(H_clip):
        x = to_screen_x(i, len(H_clip), x0, x1)
        y = y1 - val / h_max * (y1 - y0)
        points.append((x, y))
    for i in range(len(points) - 1):
        draw.line([points[i], points[i + 1]], fill=(0, 0, 128), width=1)

    # 下图: Z-Score
    x0b, y0b, x1b, y1b = 60, 340, W - 20, 570
    draw_mixed_text(draw, '哈密顿量 Z-Score', x0b, y0b - 25, font_size=14, fill=(50, 50, 50))

    z_max = max(abs(Hz.min()), abs(Hz.max()), 3) * 1.1

    draw.line([(x0b, y0b), (x0b, y1b)], fill=(100, 100, 100))
    draw.line([(x0b, y1b), (x1b, y1b)], fill=(100, 100, 100))

    # 0线
    y_zero = y1b - (0 - (-z_max)) / (2 * z_max) * (y1b - y0b)
    draw.line([(x0b, y_zero), (x1b, y_zero)], fill=(150, 150, 150), width=1)

    for i in range(5):
        val = -z_max + 2 * z_max * i / 4
        y = y1b - (val - (-z_max)) / (2 * z_max) * (y1b - y0b)
        draw.line([(x0b - 5, y), (x0b, y)], fill=(100, 100, 100))
        draw_mixed_text(draw, f'{val:.1f}', x0b - 40, y - 6, font_size=10, fill=(80, 80, 80))

    # 阈值线
    for thresh, tc, tl in [(2.0, (255, 0, 0), 'SPIKE'), (-2.0, (0, 0, 200), 'COLLAPSE')]:
        y = y1b - (thresh - (-z_max)) / (2 * z_max) * (y1b - y0b)
        for x in range(int(x0b), int(x1b), 6):
            draw.line([(x, y), (x + 3, y)], fill=tc, width=1)
        draw_mixed_text(draw, tl, x1b - 60, y - 14, font_size=10, fill=tc)

    points_z = []
    for i, val in enumerate(Hz):
        x = to_screen_x(i, len(Hz), x0b, x1b)
        y = y1b - (val - (-z_max)) / (2 * z_max) * (y1b - y0b)
        points_z.append((x, y))
    for i in range(len(points_z) - 1):
        draw.line([points_z[i], points_z[i + 1]], fill=(128, 0, 0), width=1)

    # X轴
    for year in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
        target = datetime(year, 1, 1)
        idx = np.searchsorted(dates, np.datetime64(target))
        if idx < len(dates):
            x = to_screen_x(idx, len(dates), x0b, x1b)
            draw_mixed_text(draw, str(year), x - 15, y1b + 5, font_size=10, fill=(80, 80, 80))

    img.save(filename, 'PNG')
    print(f'  {filename} saved')


def generate_trade_stats_chart(trades, filename, draw_mixed_text, cjk_path, asc_path):
    """交易统计图表"""
    from collections import Counter

    sells = trades[trades['action'] == 'SELL'].copy()

    W, H = 1200, 800
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    draw_mixed_text(draw, '交易统计分析', 20, 10, font_size=18, fill=(20, 20, 20))

    # 1. 卖出原因分布
    x0, y0 = 50, 50
    draw_mixed_text(draw, '卖出原因分布', x0, y0, font_size=14, fill=(50, 50, 50))

    # 简化原因分类
    def simplify_reason(r):
        if 'STOP_LOSS' in r:
            return '止损'
        if 'TAKE_PROFIT' in r or 'TRAILING' in r:
            return '止盈'
        return r

    sells['reason_simple'] = sells['reason'].apply(simplify_reason)
    reason_counts = sells['reason_simple'].value_counts()

    y_pos = y0 + 30
    max_count = reason_counts.max()
    for reason, count in reason_counts.items():
        bar_width = int(count / max_count * 300)
        draw.rectangle([x0 + 100, y_pos, x0 + 100 + bar_width, y_pos + 20], fill=(70, 130, 180))
        draw_mixed_text(draw, reason, x0, y_pos + 4, font_size=11, fill=(50, 50, 50))
        draw_mixed_text(draw, f' {count}', x0 + 100 + bar_width + 5, y_pos + 4, font_size=11, fill=(50, 50, 50))
        y_pos += 26

    # 2. 盈亏分布
    x0b, y0b = 650, 50
    draw_mixed_text(draw, '单笔盈亏分布', x0b, y0b, font_size=14, fill=(50, 50, 50))

    pnl = sells['pnl_pct'].values * 100
    hist_bins = np.linspace(pnl.min(), pnl.max(), 25)
    hist, _ = np.histogram(pnl, bins=hist_bins)

    bar_area_x0 = x0b
    bar_area_y0 = y0b + 30
    bar_area_w = 500
    bar_area_h = 200

    max_h = hist.max()
    bar_width = bar_area_w / len(hist)

    draw.line([(bar_area_x0, bar_area_y0), (bar_area_x0, bar_area_y0 + bar_area_h)], fill=(100, 100, 100))
    draw.line([(bar_area_x0, bar_area_y0 + bar_area_h),
               (bar_area_x0 + bar_area_w, bar_area_y0 + bar_area_h)], fill=(100, 100, 100))

    for i, h in enumerate(hist):
        if h > 0:
            bar_h = h / max_h * bar_area_h
            bx = bar_area_x0 + i * bar_width
            color = (100, 200, 100) if hist_bins[i] >= 0 else (200, 100, 100)
            draw.rectangle([bx, bar_area_y0 + bar_area_h - bar_h, bx + bar_width - 1, bar_area_y0 + bar_area_h],
                           fill=color)

    # 0线标注
    zero_idx = np.searchsorted(hist_bins, 0)
    zero_x = bar_area_x0 + zero_idx * bar_width
    draw.line([(zero_x, bar_area_y0), (zero_x, bar_area_y0 + bar_area_h)], fill=(150, 150, 150))

    # 3. 关键统计
    x0c, y0c = 50, 400
    draw_mixed_text(draw, '关键统计', x0c, y0c, font_size=14, fill=(50, 50, 50))

    stats = [
        f'总交易次数: {len(sells)}',
        f'胜率: {(sells["pnl"] > 0).mean():.1%}',
        f'平均盈利: {sells[sells["pnl"] > 0]["pnl_pct"].mean():.2%}' if (sells['pnl'] > 0).any() else 'N/A',
        f'平均亏损: {sells[sells["pnl"] <= 0]["pnl_pct"].mean():.2%}' if (sells['pnl'] <= 0).any() else 'N/A',
        f'盈亏比: {abs(sells[sells["pnl"] > 0]["pnl"].mean() / sells[sells["pnl"] <= 0]["pnl"].mean()):.2f}' if (sells['pnl'] <= 0).any() and sells[sells['pnl'] <= 0]['pnl'].mean() != 0 else 'N/A',
        f'平均持仓: {sells["hold_days"].mean():.1f}天',
        f'最大盈利: {sells["pnl_pct"].max():.2%}',
        f'最大亏损: {sells["pnl_pct"].min():.2%}',
    ]

    for i, s in enumerate(stats):
        draw_mixed_text(draw, s, x0c, y0c + 30 + i * 25, font_size=12, fill=(50, 50, 50))

    # 4. 策略对比
    x0d, y0d = 650, 400
    draw_mixed_text(draw, '策略胜负统计', x0d, y0d, font_size=14, fill=(50, 50, 50))

    for j, strategy in enumerate(['trend', 'reversal']):
        strat_sells = sells[sells['strategy'] == strategy]
        wins = (strat_sells['pnl'] > 0).sum()
        losses = (strat_sells['pnl'] <= 0).sum()

        y_base = y0d + 40 + j * 80
        draw_mixed_text(draw, f'{strategy}:', x0d, y_base, font_size=12, fill=(50, 50, 50))
        draw.rectangle([x0d + 80, y_base, x0d + 80 + wins * 3, y_base + 25], fill=(100, 200, 100))
        draw_mixed_text(draw, f'胜 {wins}', x0d + 80, y_base + 5, font_size=11, fill=(50, 50, 50))
        draw.rectangle([x0d + 80, y_base + 30, x0d + 80 + losses * 3, y_base + 55], fill=(200, 100, 100))
        draw_mixed_text(draw, f'负 {losses}', x0d + 80, y_base + 35, font_size=11, fill=(50, 50, 50))

    img.save(filename, 'PNG')
    print(f'  {filename} saved')


if __name__ == '__main__':
    generate_all_charts_pil()
