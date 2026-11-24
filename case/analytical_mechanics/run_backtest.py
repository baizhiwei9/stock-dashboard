#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测主脚本：获取A股数据 + 运行回测 + 生成报告

数据源：AKShare
回测区间：2020-01-01 ~ 2026-08-11
重点验证：2022年下跌 + 2026年7月下跌
"""

import sys
import os
import pickle
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from datetime import datetime

from hamiltonian_chaos_system import (
    FeatureEngine, StockSelector, RiskManager, BacktestEngine,
    hurst_rs, rolling_hurst, largest_lyapunov_rosenstein
)

# 中文字体设置
plt.rcParams['font.sans-serif'] = ['Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False

# ============================================================
# 数据获取
# ============================================================

def get_index_data(symbol='sh000300', start_date='2019-01-01'):
    """
    获取指数日线数据
    使用腾讯接口（AKShare 的 stock_zh_index_daily_tx）
    """
    import akshare as ak

    print(f"获取指数数据: {symbol} ...")
    df = ak.stock_zh_index_daily_tx(symbol=symbol)
    df['date'] = pd.to_datetime(df['date'])
    df = df[df['date'] >= start_date].copy()
    df = df.sort_values('date').reset_index(drop=True)
    df['volume'] = df['amount']  # 指数用 amount 代替 volume

    print(f"  数据范围: {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"  数据量: {len(df)} 条")

    return df


def get_stock_data(code, start_date='2019-01-01', retry=3):
    """
    获取个股日线数据
    优先用腾讯接口（stock_zh_a_daily），失败时回退到东方财富
    """
    import akshare as ak
    import time

    # 腾讯接口代码格式: sh600519 / sz000858
    tx_code = ('sh' if code.startswith('6') else 'sz') + code

    # 方法1: 腾讯接口
    for attempt in range(retry):
        try:
            df = ak.stock_zh_a_daily(symbol=tx_code, adjust='qfq')
            if df is not None and len(df) > 200:
                df['date'] = pd.to_datetime(df['date'])
                df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
                df = df[df['date'] >= start_date].copy()
                df = df.sort_values('date').reset_index(drop=True)
                return df
            time.sleep(1)
        except Exception as e:
            print(f"  腾讯接口尝试 {attempt+1}/{retry} 失败: {e}")
            time.sleep(2)

    # 方法2: 东方财富 stock_zh_a_hist
    for attempt in range(retry):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                     start_date=start_date.replace('-', ''),
                                     end_date='20260812', adjust='qfq')
            if df is not None and len(df) > 200:
                df['date'] = pd.to_datetime(df['日期'])
                df = df.rename(columns={
                    '开盘': 'open', '收盘': 'close',
                    '最高': 'high', '最低': 'low',
                    '成交量': 'volume'
                })
                df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
                df = df.sort_values('date').reset_index(drop=True)
                return df
            time.sleep(1)
        except Exception as e:
            print(f"  东方财富尝试 {attempt+1}/{retry} 失败: {e}")
            time.sleep(2)

    return None


def get_stock_pool():
    """
    获取沪深300成分股样本（选流动性好的蓝筹股）
    为了回测效率，选 15 只代表性股票
    """
    stocks = [
        ('600519', '贵州茅台'),
        ('000858', '五粮液'),
        ('601318', '中国平安'),
        ('600036', '招商银行'),
        ('000651', '格力电器'),
        ('000333', '美的集团'),
        ('601012', '隆基绿能'),
        ('600276', '恒瑞医药'),
        ('000725', '京东方A'),
        ('002415', '海康威视'),
        ('300750', '宁德时代'),
        ('601888', '中国中免'),
        ('002594', '比亚迪'),
        ('600887', '伊利股份'),
        ('601628', '中国人寿'),
    ]
    return stocks


# ============================================================
# 数据缓存
# ============================================================

CACHE_DIR = 'data_cache'
os.makedirs(CACHE_DIR, exist_ok=True)


def load_or_fetch_data():
    """
    加载或获取数据（带缓存）
    """
    cache_file = os.path.join(CACHE_DIR, 'market_data.pkl')

    # 如果已有 stock_data.pkl 缓存，直接组装
    stock_cache_file = os.path.join(CACHE_DIR, 'stock_data.pkl')
    if os.path.exists(stock_cache_file) and not os.path.exists(cache_file):
        print("从个股缓存加载数据...")
        with open(stock_cache_file, 'rb') as f:
            stock_data = pickle.load(f)
        market_df = get_index_data('sh000300', '2019-01-01')
        stock_pool = get_stock_pool()
        data = {'market': market_df, 'stocks': stock_data, 'stock_pool': stock_pool}
        with open(cache_file, 'wb') as f:
            pickle.dump(data, f)
        print(f"  大盘数据: {len(data['market'])} 条")
        print(f"  个股数量: {len(data['stocks'])}")
        return data

    if os.path.exists(cache_file):
        print("从缓存加载数据...")
        with open(cache_file, 'rb') as f:
            data = pickle.load(f)
        print(f"  大盘数据: {len(data['market'])} 条")
        print(f"  个股数量: {len(data['stocks'])}")
        return data

    # 全新获取
    market_df = get_index_data('sh000300', '2019-01-01')
    stock_pool = get_stock_pool()
    stock_data = {}
    for code, name in stock_pool:
        print(f"获取个股: {code} {name} ...")
        df = get_stock_data(code, '2019-01-01')
        if df is not None and len(df) > 200:
            stock_data[code] = df
            print(f"  数据量: {len(df)} 条")
        else:
            print(f"  数据不足，跳过")

    data = {'market': market_df, 'stocks': stock_data, 'stock_pool': stock_pool}
    with open(cache_file, 'wb') as f:
        pickle.dump(data, f)
    print(f"\n数据缓存完成: {cache_file}")
    return data


# ============================================================
# 特征预计算
# ============================================================

def precompute_features(data):
    """
    预计算所有特征（大盘 + 个股）
    """
    cache_file = os.path.join(CACHE_DIR, 'features.pkl')

    if os.path.exists(cache_file):
        print("\n从缓存加载特征数据...")
        with open(cache_file, 'rb') as f:
            features = pickle.load(f)
        return features

    engine = FeatureEngine(hurst_window=100, lle_window=100,
                           tor_window=60, ham_window=20)

    # 大盘特征
    print("\n=== 计算大盘特征 ===")
    market_df = data['market'].copy()
    market_df = engine.compute_all_features(market_df)

    # 个股特征
    print("\n=== 计算个股特征 ===")
    stock_features = {}
    for code, df in data['stocks'].items():
        print(f"\n处理 {code} ...")
        enhanced = engine.compute_all_features(df)
        stock_features[code] = enhanced

    features = {
        'market': market_df,
        'stocks': stock_features,
    }

    with open(cache_file, 'wb') as f:
        pickle.dump(features, f)

    print(f"\n特征缓存完成: {cache_file}")
    return features


# ============================================================
# 风险时段验证
# ============================================================

def analyze_risk_periods(market_df):
    """
    分析关键风险时段的指标表现（v3.0 混沌方向分类风控）
    """
    from hamiltonian_chaos_system import RiskManager
    rm = RiskManager()

    print("\n" + "=" * 70)
    print("关键风险时段分析 (v3.0 混沌方向分类风控)")
    print("=" * 70)

    # 预计算连续在MA20下方天数
    market_df = market_df.copy().reset_index(drop=True)
    below_days_list = []
    consec = 0
    for _, row in market_df.iterrows():
        ma = row.get('ma20', row['close'])
        if not pd.isna(ma) and ma > 0 and row['close'] < ma:
            consec += 1
        else:
            consec = 0
        below_days_list.append(consec)

    # 逐日计算风控状态
    risk_levels = []
    for idx, row in market_df.iterrows():
        lle = row['lle'] if not pd.isna(row['lle']) else 0.05
        tor = row['toroidality'] if not pd.isna(row['toroidality']) else 0.25
        hurst = row['hurst'] if not pd.isna(row['hurst']) else 0.5
        close = row['close']
        ma20 = row.get('ma20', close)
        above_ma = close > ma20 if not pd.isna(ma20) and ma20 > 0 else True
        dev_ma = (close - ma20) / ma20 if not pd.isna(ma20) and ma20 > 0 else 0
        bd = below_days_list[idx]
        risk_levels.append(rm.check_market_risk(lle, tor, hurst, above_ma, dev_ma, below_ma20_days=bd))
    market_df['risk_level'] = risk_levels

    # 风控状态总体分布
    total = len(market_df[market_df['date'] >= '2020-01-01'])
    clear_n = len(market_df[(market_df['date'] >= '2020-01-01') & (market_df['risk_level'] == 'CLEAR')])
    warn_n = len(market_df[(market_df['date'] >= '2020-01-01') & (market_df['risk_level'] == 'WARNING')])
    danger_n = len(market_df[(market_df['date'] >= '2020-01-01') & (market_df['risk_level'] == 'DANGER')])
    print(f"\n全周期风控状态分布:")
    print(f"  CLEAR:   {clear_n:4d}天 ({clear_n/total*100:.1f}%)")
    print(f"  WARNING: {warn_n:4d}天 ({warn_n/total*100:.1f}%)")
    print(f"  DANGER:  {danger_n:4d}天 ({danger_n/total*100:.1f}%)")

    # 时段1: 2022年下跌
    print("\n>>> 时段1: 2022年下跌（沪深300从4917跌至3508）")
    period1 = market_df[
        (market_df['date'] >= '2022-01-01') &
        (market_df['date'] <= '2022-12-31')
    ].copy()

    if len(period1) > 0:
        print(f"  期初收盘: {period1.iloc[0]['close']:.2f}")
        print(f"  期末收盘: {period1.iloc[-1]['close']:.2f}")
        print(f"  最大跌幅: {((period1['close'].min() / period1.iloc[0]['close']) - 1) * 100:.1f}%")

        p1_clear = len(period1[period1['risk_level'] == 'CLEAR'])
        p1_warn = len(period1[period1['risk_level'] == 'WARNING'])
        p1_danger = len(period1[period1['risk_level'] == 'DANGER'])
        print(f"  风控状态: CLEAR {p1_clear}天, WARNING {p1_warn}天, DANGER {p1_danger}天")

        if p1_danger > 0:
            first_danger = period1[period1['risk_level'] == 'DANGER'].iloc[0]
            print(f"  首次DANGER: {first_danger['date'].strftime('%Y-%m-%d')}")

        valid_h = period1.dropna(subset=['hurst'])
        if len(valid_h) > 0:
            print(f"  Hurst 范围: {valid_h['hurst'].min():.3f} ~ {valid_h['hurst'].max():.3f}")

    # 时段2: 2026年7月下跌
    print("\n>>> 时段2: 2026年7月下跌")
    period2 = market_df[
        (market_df['date'] >= '2026-06-15') &
        (market_df['date'] <= '2026-08-11')
    ].copy()

    if len(period2) > 0:
        print(f"  期初收盘: {period2.iloc[0]['close']:.2f}")
        print(f"  期末收盘: {period2.iloc[-1]['close']:.2f}")
        print(f"  最大跌幅: {((period2['close'].min() / period2.iloc[0]['close']) - 1) * 100:.1f}%")

        p2_clear = len(period2[period2['risk_level'] == 'CLEAR'])
        p2_warn = len(period2[period2['risk_level'] == 'WARNING'])
        p2_danger = len(period2[period2['risk_level'] == 'DANGER'])
        print(f"  风控状态: CLEAR {p2_clear}天, WARNING {p2_warn}天, DANGER {p2_danger}天")

        if p2_danger > 0:
            first_danger = period2[period2['risk_level'] == 'DANGER'].iloc[0]
            print(f"  首次DANGER: {first_danger['date'].strftime('%Y-%m-%d')}")

        valid_h = period2.dropna(subset=['hurst'])
        if len(valid_h) > 0:
            print(f"  Hurst 范围: {valid_h['hurst'].min():.3f} ~ {valid_h['hurst'].max():.3f}")

    # 各年度风控状态 vs 基准涨跌
    print("\n>>> 各年度风控状态 vs 基准涨跌:")
    print(f"  {'年份':>6s}  {'基准涨跌':>8s}  {'CLEAR':>6s} {'WARNING':>8s} {'DANGER':>7s}")
    for year in range(2020, 2027):
        m_y = market_df[(market_df['date'] >= f'{year}-01-01') & (market_df['date'] <= f'{year}-12-31')]
        if len(m_y) == 0:
            continue
        ret = m_y['close'].iloc[-1] / m_y['close'].iloc[0] - 1
        c = len(m_y[m_y['risk_level'] == 'CLEAR'])
        w = len(m_y[m_y['risk_level'] == 'WARNING'])
        d = len(m_y[m_y['risk_level'] == 'DANGER'])
        print(f"  {year:>6d}  {ret:>+7.2%}  {c:>6d} {w:>8d} {d:>7d}")


# ============================================================
# 可视化
# ============================================================

def generate_charts(market_df, results, output_dir='charts'):
    """
    生成可视化图表
    """
    os.makedirs(output_dir, exist_ok=True)

    # ---- 图1: 净值曲线 + 回撤 ----
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [3, 1, 1]})

    nav_series = results['nav_series']
    drawdown = results['drawdown_series']

    # 净值曲线
    ax1 = axes[0]
    ax1.plot(nav_series.index, nav_series.values / 1_000_000, label='策略净值', linewidth=1.5)

    # 基准（沪深300归一化）
    bench = market_df[market_df['date'].isin(nav_series.index)].copy()
    if len(bench) > 0:
        bench_nav = bench['close'] / bench['close'].iloc[0]
        ax1.plot(bench['date'].values, bench_nav.values, label='沪深300', alpha=0.6, linewidth=1)

    # 标注风险时段
    ax1.axvspan(datetime(2022, 1, 1), datetime(2022, 12, 31), alpha=0.1, color='red', label='2022下跌')
    ax1.axvspan(datetime(2026, 6, 15), datetime(2026, 8, 11), alpha=0.1, color='orange', label='2026.07下跌')

    ax1.set_title('策略净值 vs 沪深300基准', fontsize=14)
    ax1.legend(loc='upper left', fontsize=10)
    ax1.set_ylabel('净值（归一化）')
    ax1.grid(True, alpha=0.3)

    # 回撤曲线
    ax2 = axes[1]
    ax2.fill_between(drawdown.index, drawdown.values * 100, 0, color='red', alpha=0.3)
    ax2.plot(drawdown.index, drawdown.values * 100, color='red', linewidth=0.8)
    ax2.set_title('回撤曲线', fontsize=12)
    ax2.set_ylabel('回撤 (%)')
    ax2.grid(True, alpha=0.3)

    # 大盘LLE
    ax3 = axes[2]
    market_plot = market_df[market_df['date'].isin(nav_series.index)].copy()
    ax3.plot(market_plot['date'], market_plot['lle'], color='purple', linewidth=0.8, label='LLE')
    ax3.axhline(y=0.15, color='red', linestyle='--', alpha=0.5, label='强混沌阈值')
    ax3.axhline(y=0.10, color='orange', linestyle='--', alpha=0.5, label='弱混沌阈值')
    ax3.set_title('大盘李雅普诺夫指数 (LLE)', fontsize=12)
    ax3.set_ylabel('LLE')
    ax3.legend(loc='upper right', fontsize=8)
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, '01_nav_drawdown_lle.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图1 已保存: {output_dir}/01_nav_drawdown_lle.png")

    # ---- 图2: 2022年风险时段放大 ----
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)

    period = market_df[
        (market_df['date'] >= '2021-10-01') &
        (market_df['date'] <= '2022-12-31')
    ].copy()

    ax1 = axes[0]
    ax1.plot(period['date'], period['close'], color='blue', linewidth=1.2)
    ax1.set_title('沪深300 收盘价 (2021Q4 ~ 2022)', fontsize=12)
    ax1.set_ylabel('价格')
    ax1.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.plot(period['date'], period['lle'], color='purple', linewidth=1)
    ax2.axhline(y=0.15, color='red', linestyle='--', alpha=0.5)
    ax2.axhline(y=0.10, color='orange', linestyle='--', alpha=0.5)
    ax2.fill_between(period['date'], 0, period['lle'].clip(upper=0.15),
                     where=period['lle'] > 0.10, color='red', alpha=0.2)
    ax2.set_title('李雅普诺夫指数 (LLE)', fontsize=12)
    ax2.set_ylabel('LLE')
    ax2.grid(True, alpha=0.3)

    ax3 = axes[2]
    ax3.plot(period['date'], period['toroidality'], color='green', linewidth=1)
    ax3.axhline(y=0.15, color='red', linestyle='--', alpha=0.5)
    ax3.fill_between(period['date'], 0, period['toroidality'].clip(upper=0.15),
                     where=period['toroidality'] < 0.15, color='red', alpha=0.2)
    ax3.set_title('KAM 环面度', fontsize=12)
    ax3.set_ylabel('Toroidality')
    ax3.grid(True, alpha=0.3)

    ax4 = axes[3]
    ax4.plot(period['date'], period['hurst'], color='teal', linewidth=1)
    ax4.axhline(y=0.55, color='blue', linestyle='--', alpha=0.5)
    ax4.axhline(y=0.45, color='orange', linestyle='--', alpha=0.5)
    ax4.set_title('Hurst 指数', fontsize=12)
    ax4.set_ylabel('Hurst')
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, '02_2022_risk_period.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图2 已保存: {output_dir}/02_2022_risk_period.png")

    # ---- 图3: 2026年7月下跌放大 ----
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)

    period = market_df[
        (market_df['date'] >= '2026-05-01') &
        (market_df['date'] <= '2026-08-11')
    ].copy()

    ax1 = axes[0]
    ax1.plot(period['date'], period['close'], color='blue', linewidth=1.2)
    ax1.set_title('沪深300 收盘价 (2026年5月~8月)', fontsize=12)
    ax1.set_ylabel('价格')
    ax1.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.plot(period['date'], period['lle'], color='purple', linewidth=1)
    ax2.axhline(y=0.15, color='red', linestyle='--', alpha=0.5)
    ax2.axhline(y=0.10, color='orange', linestyle='--', alpha=0.5)
    ax2.fill_between(period['date'], 0, period['lle'].clip(upper=0.15),
                     where=period['lle'] > 0.10, color='red', alpha=0.2)
    ax2.set_title('李雅普诺夫指数 (LLE)', fontsize=12)
    ax2.set_ylabel('LLE')
    ax2.grid(True, alpha=0.3)

    ax3 = axes[2]
    ax3.plot(period['date'], period['toroidality'], color='green', linewidth=1)
    ax3.axhline(y=0.15, color='red', linestyle='--', alpha=0.5)
    ax3.fill_between(period['date'], 0, period['toroidality'].clip(upper=0.15),
                     where=period['toroidality'] < 0.15, color='red', alpha=0.2)
    ax3.set_title('KAM 环面度', fontsize=12)
    ax3.set_ylabel('Toroidality')
    ax3.grid(True, alpha=0.3)

    ax4 = axes[3]
    ax4.plot(period['date'], period['hurst'], color='teal', linewidth=1)
    ax4.axhline(y=0.55, color='blue', linestyle='--', alpha=0.5)
    ax4.axhline(y=0.45, color='orange', linestyle='--', alpha=0.5)
    ax4.set_title('Hurst 指数', fontsize=12)
    ax4.set_ylabel('Hurst')
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, '03_2026_july_risk_period.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图3 已保存: {output_dir}/03_2026_july_risk_period.png")

    # ---- 图4: 哈密顿量 ----
    fig, axes = plt.subplots(2, 1, figsize=(14, 6), sharex=True)

    market_plot = market_df[market_df['date'] >= '2020-01-01'].copy()

    ax1 = axes[0]
    ax1.plot(market_plot['date'], market_plot['hamiltonian'], color='darkblue', linewidth=0.8)
    ax1.set_title('大盘哈密顿量 H(t)', fontsize=12)
    ax1.set_ylabel('H(t)')
    ax1.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.plot(market_plot['date'], market_plot['hamiltonian_z'], color='darkred', linewidth=0.8)
    ax2.axhline(y=2.0, color='red', linestyle='--', alpha=0.5, label='SPIKE')
    ax2.axhline(y=-2.0, color='blue', linestyle='--', alpha=0.5, label='COLLAPSE')
    ax2.fill_between(market_plot['date'], 2, market_plot['hamiltonian_z'].clip(upper=2),
                     where=market_plot['hamiltonian_z'] > 2, color='red', alpha=0.2)
    ax2.fill_between(market_plot['date'], -2, market_plot['hamiltonian_z'].clip(lower=-2),
                     where=market_plot['hamiltonian_z'] < -2, color='blue', alpha=0.2)
    ax2.set_title('哈密顿量 Z-Score', fontsize=12)
    ax2.set_ylabel('Z-Score')
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, '04_hamiltonian.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图4 已保存: {output_dir}/04_hamiltonian.png")

    # ---- 图5: 交易统计 ----
    if len(results.get('trades', pd.DataFrame())) > 0:
        trades = results['trades']
        sell_trades = trades[trades['action'] == 'SELL'].copy()

        fig, axes = plt.subplots(2, 2, figsize=(14, 8))

        # 卖出原因分布
        ax1 = axes[0, 0]
        reason_counts = sell_trades['reason'].value_counts()
        ax1.barh(range(len(reason_counts)), reason_counts.values)
        ax1.set_yticks(range(len(reason_counts)))
        ax1.set_yticklabels(reason_counts.index, fontsize=8)
        ax1.set_title('卖出原因分布', fontsize=12)
        ax1.set_xlabel('次数')

        # 盈亏分布
        ax2 = axes[0, 1]
        ax2.hist(sell_trades['pnl_pct'] * 100, bins=30, color='steelblue', edgecolor='white')
        ax2.axvline(x=0, color='red', linestyle='--', alpha=0.5)
        ax2.set_title('单笔盈亏分布', fontsize=12)
        ax2.set_xlabel('盈亏 (%)')
        ax2.set_ylabel('频次')

        # 持仓天数分布
        ax3 = axes[1, 0]
        ax3.hist(sell_trades['hold_days'], bins=20, color='coral', edgecolor='white')
        ax3.set_title('持仓天数分布', fontsize=12)
        ax3.set_xlabel('天数')
        ax3.set_ylabel('频次')

        # 策略胜率对比
        ax4 = axes[1, 1]
        for strategy in ['trend', 'reversal']:
            strat_trades = sell_trades[sell_trades['strategy'] == strategy]
            if len(strat_trades) > 0:
                wins = (strat_trades['pnl'] > 0).sum()
                losses = (strat_trades['pnl'] <= 0).sum()
                ax4.bar([f'{strategy}\n胜', f'{strategy}\n负'], [wins, losses],
                        color=['green', 'red'], alpha=0.6)
        ax4.set_title('策略胜负统计', fontsize=12)
        ax4.set_ylabel('次数')

        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, '05_trade_stats.png'), dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  图5 已保存: {output_dir}/05_trade_stats.png")


# ============================================================
# 主函数
# ============================================================

def main():
    print("=" * 70)
    print("分析力学量化交易系统 - 回测")
    print("=" * 70)

    # 1. 数据获取
    print("\n[1/5] 获取数据...")
    data = load_or_fetch_data()

    # 2. 特征计算
    print("\n[2/5] 计算特征...")
    features = precompute_features(data)

    # 3. 风险时段分析
    print("\n[3/5] 风险时段分析...")
    analyze_risk_periods(features['market'])

    # 4. 回测
    print("\n[4/5] 运行回测...")
    selector = StockSelector()
    risk_manager = RiskManager()
    backtest = BacktestEngine(
        initial_cash=1_000_000,
        commission=0.0003,
        slippage=0.001,
        max_positions=10,
    )

    market_df = features['market'].copy()
    stock_data_dict = {code: df for code, df in features['stocks'].items()}

    results = backtest.run(
        market_df=market_df,
        stock_data_dict=stock_data_dict,
        feature_engine=FeatureEngine(),
        selector=selector,
        risk_manager=risk_manager,
        start_date='2020-01-01',
        end_date='2026-08-11',
    )

    # 5. 绩效报告
    print("\n[5/5] 生成报告...")
    print("\n" + "=" * 70)
    print("回测绩效报告")
    print("=" * 70)

    print(f"\n初始资金:     ¥{backtest.initial_cash:,.0f}")
    print(f"期末净值:     ¥{results['nav_series'].iloc[-1]:,.0f}")
    print(f"累计收益率:   {results['total_return']:.2%}")
    print(f"年化收益率:   {results['cagr']:.2%}")
    print(f"最大回撤:     {results['max_drawdown']:.2%}")
    print(f"夏普比率:     {results['sharpe']:.2f}")
    print(f"卡玛比率:     {results['calmar']:.2f}")
    print(f"胜率:         {results['win_rate']:.2%}")
    print(f"盈亏比:       {results['profit_factor']:.2f}")
    print(f"月度胜率:     {results['monthly_win_rate']:.2%}")
    print(f"总交易次数:   {results['n_trades']}")

    # 基准对比
    bench = market_df[market_df['date'] >= '2020-01-01'].copy()
    bench_return = (bench['close'].iloc[-1] / bench['close'].iloc[0] - 1)
    bench_peak = bench['close'].expanding().max()
    bench_dd = ((bench['close'] - bench_peak) / bench_peak).min()

    print(f"\n--- 基准 (沪深300) ---")
    print(f"累计收益率:   {bench_return:.2%}")
    print(f"最大回撤:     {bench_dd:.2%}")

    print(f"\n--- 超额收益 ---")
    print(f"超额累计:     {results['total_return'] - bench_return:.2%}")
    print(f"回撤改善:     {results['max_drawdown'] - bench_dd:.2%}")

    # 6. 可视化
    print("\n生成图表...")
    generate_charts(features['market'], results)

    # 保存结果
    results_file = 'backtest_results.pkl'
    save_data = {
        'nav_series': results['nav_series'],
        'drawdown_series': results['drawdown_series'],
        'returns': results['returns'],
        'trades': results['trades'],
        'metrics': {
            'total_return': results['total_return'],
            'cagr': results['cagr'],
            'max_drawdown': results['max_drawdown'],
            'sharpe': results['sharpe'],
            'calmar': results['calmar'],
            'win_rate': results['win_rate'],
            'profit_factor': results['profit_factor'],
            'monthly_win_rate': results['monthly_win_rate'],
            'n_trades': results['n_trades'],
        },
        'market_df': features['market'],
    }
    with open(results_file, 'wb') as f:
        pickle.dump(save_data, f)
    print(f"\n结果已保存: {results_file}")

    print("\n" + "=" * 70)
    print("回测完成!")
    print("=" * 70)


if __name__ == '__main__':
    main()
