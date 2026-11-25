#!/usr/bin/env python3
"""
V4 Final: 真实资金流数据回测
=============================
数据源: Tushare moneyflow_ind_ths (L2逐笔成交主动买卖)
- net_buy_amount: 主动买入金额(亿)
- net_sell_amount: 主动卖出金额(亿)  
- net_amount: 净额(亿) = 买入 - 卖出
- pct_change: 行业涨跌幅(%)

策略:
  A) 信号驱动: 机会信号建仓/风险信号清仓
  B) 纯排名: 每周选ANB最高的Top5 (研报原文)
  C) 信号+排名混合: 信号过滤+ANB排名选股

回测区间: 2026年YTD (1月~8月14日)
调仓频率: 周度
基准: 29个行业等权
"""

import pandas as pd
import numpy as np
from datetime import datetime
import warnings
import json
import os
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

warnings.filterwarnings('ignore')

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
from feishu_config import WEBHOOK_URL  # noqa: E402  凭据脱敏：见 DEPLOY.md「飞书凭据」
# 29个行业-ETF映射 (同花顺行业名 -> ETF代码)
INDUSTRY_ETF_MAP = {
    "半导体": ("512480", "半导体ETF"),
    "银行": ("512800", "银行ETF"),
    "证券": ("512000", "券商ETF"),
    "光伏设备": ("515790", "光伏ETF"),
    "军工装备": ("512660", "军工ETF"),
    "通信设备": ("515880", "通信ETF"),
    "计算机设备": ("512720", "计算机ETF"),
    "软件开发": ("515230", "软件ETF"),
    "化学制药": ("512010", "医药ETF"),
    "煤炭开采加工": ("515220", "煤炭ETF"),
    "钢铁": ("515210", "钢铁ETF"),
    "工业金属": ("512400", "有色ETF"),
    "房地产": ("512200", "房地产ETF"),
    "汽车整车": ("516110", "汽车ETF"),
    "白酒": ("512690", "酒ETF"),
    "食品加工制造": ("515170", "食品饮料ETF"),
    "生物制品": ("159992", "创新药ETF"),
    "医疗器械": ("159883", "医疗器械ETF"),
    "化学制品": ("159870", "化工ETF"),
    "风电设备": ("516950", "风电ETF"),
    "电力": ("159611", "电力ETF"),
    "消费电子": ("159732", "消费电子ETF"),
    "电池": ("159755", "电池ETF"),
    "贵金属": ("159933", "金ETF"),
    "建筑材料": ("159745", "建材ETF"),
    "物流": ("516910", "物流ETF"),
    "养殖业": ("159865", "养殖ETF"),
    "通信服务": ("515050", "5GETF"),
    "IT服务": ("515080", "AI智能ETF"),
}

W = 10  # MA窗口
TOP_K = 5


def load_data():
    """加载真实资金流数据, 返回面板"""
    df = pd.read_csv(os.path.join(OUTPUT_DIR, 'industry_moneyflow_real.csv'), dtype={'trade_date': str})
    df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df[df['industry'].isin(INDUSTRY_ETF_MAP.keys())].copy()
    df = df.sort_values(['date', 'industry']).reset_index(drop=True)
    
    # 数值列
    for col in ['net_buy_amount', 'net_sell_amount', 'net_amount', 'pct_change']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    print(f"  天数: {df['date'].dt.strftime('%Y%m%d').nunique()}, 行业数: {df['industry'].nunique()}")
    print(f"  日期范围: {df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')}")
    
    return df


def compute_factors(df):
    """
    研报原文: 主动净买入因子
    ANB = (买入占比 - 卖出占比) / 成交额占比, 做W日MA
    
    买入占比 = 行业主动买入额 / 全市场主动买入额
    卖出占比 = 行业主动卖出额 / 全市场主动卖出额
    成交额占比 = 行业成交额 / 全市场成交额
    成交额 ≈ 主动买入额 + 主动卖出额
    
    净流入占比 = 净额 / 成交额
    """
    # 每日全市场汇总
    daily = df.groupby('date').agg(
        total_buy=('net_buy_amount', 'sum'),
        total_sell=('net_sell_amount', 'sum'),
    )
    df = df.merge(daily, on='date')
    
    df['buy_ratio'] = df['net_buy_amount'] / df['total_buy']
    df['sell_ratio'] = df['net_sell_amount'] / df['total_sell']
    df['amount'] = df['net_buy_amount'] + df['net_sell_amount']
    
    daily_amt = df.groupby('date')['amount'].sum().rename('total_amount')
    df = df.merge(daily_amt, on='date')
    df['amount_ratio'] = df['amount'] / df['total_amount']
    
    # ANB
    df['anb'] = (df['buy_ratio'] - df['sell_ratio']) / df['amount_ratio'].replace(0, np.nan)
    # 净流入占比
    df['net_ratio'] = df['net_amount'] / df['amount'].replace(0, np.nan)
    
    # 构建面板
    anb_panel = df.set_index(['date', 'industry'])['anb'].unstack('industry')
    net_ratio_panel = df.set_index(['date', 'industry'])['net_ratio'].unstack('industry')
    ret_panel = df.set_index(['date', 'industry'])['pct_change'].unstack('industry') / 100.0  # 转为小数
    
    # W日MA
    anb_ma = anb_panel.rolling(W, min_periods=3).mean()
    net_ratio_ma = net_ratio_panel.rolling(W, min_periods=3).mean()
    
    return {
        'anb': anb_ma,
        'anb_raw': anb_panel,
        'net_ratio': net_ratio_ma,
        'net_ratio_raw': net_ratio_panel,
        'ret': ret_panel,
    }


def get_weekly_dates(dates):
    """取每周最后一个交易日"""
    weekly = []
    for d in dates:
        if len(weekly) == 0 or d.isocalendar()[1] != weekly[-1].isocalendar()[1]:
            weekly.append(d)
        else:
            weekly[-1] = d
    return weekly


def backtest_signal_driven(factors, strategy_name='信号驱动'):
    """
    策略A: 信号驱动
    - 机会信号: 净流入占比上穿0 或 ANB从负转正 -> 建仓候选
    - 风险信号: 净流入占比下穿0 或 ANB从正转负 -> 清仓
    - 持有: 净流入>0且ANB>0
    - 空仓: 净流入<0且ANB<0
    """
    anb = factors['anb']
    net_ratio = factors['net_ratio']
    ret = factors['ret']
    
    trade_dates = sorted(set(anb.index) & set(ret.index))
    weekly_dates = get_weekly_dates(trade_dates)
    
    holdings = []
    nav = [1.0]
    nav_dates = [weekly_dates[0]] if weekly_dates else []
    weekly_rets = []
    records = []
    signal_log = []
    
    for i in range(len(weekly_dates) - 1):
        rd = weekly_dates[i]
        nd = weekly_dates[i + 1]
        
        if rd not in anb.index:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        # 计算信号
        new_holdings = []
        for ind in holdings:
            if ind not in anb.columns:
                continue
            a = anb.loc[rd, ind]
            n = net_ratio.loc[rd, ind]
            a_prev = anb.loc[rd, ind] if i > 0 else np.nan
            n_prev = net_ratio.shift(1).loc[rd, ind] if rd in net_ratio.index else np.nan
            
            if pd.isna(a) or pd.isna(n):
                new_holdings.append(ind)  # 数据缺失, 保持
            elif (n_prev > 0 and n <= 0) or (a_prev > 0 and a <= 0):
                signal_log.append({'date': rd, 'industry': ind, 'signal': '风险-清仓',
                                   'net_ratio': n, 'anb': a, 'pct_change': ret.loc[rd, ind] if rd in ret.index else 0})
            else:
                new_holdings.append(ind)
        holdings = new_holdings
        
        # 机会信号候选
        candidates = []
        for ind in anb.columns:
            if ind in holdings:
                continue
            a = anb.loc[rd, ind]
            n = net_ratio.loc[rd, ind]
            n_prev = net_ratio.shift(1).loc[rd, ind] if rd in net_ratio.index else np.nan
            a_raw_prev = anb_raw_prev = np.nan
            
            if pd.isna(a) or pd.isna(n):
                continue
            if n_prev is not None and not pd.isna(n_prev):
                if (n_prev < 0 and n >= 0):
                    candidates.append((ind, a, n, '上穿0'))
                elif (n_prev < -0.01 and n > 0 and n > n_prev):
                    candidates.append((ind, a, n, '低位回升'))
            if a > 0 and n > 0:
                candidates.append((ind, a, n, '持续流入'))
        
        # 按ANB排序选Top
        candidates.sort(key=lambda x: x[1], reverse=True)
        for ind, a, n, reason in candidates:
            if len(holdings) >= TOP_K:
                break
            holdings.append(ind)
            signal_log.append({'date': rd, 'industry': ind, 'signal': f'机会-{reason}',
                               'net_ratio': n, 'anb': a, 'pct_change': ret.loc[rd, ind] if rd in ret.index else 0})
        
        # 如果持仓不足, 用ANB Top补
        if len(holdings) < TOP_K:
            anb_row = anb.loc[rd].dropna()
            for ind in anb_row.nlargest(TOP_K * 2).index:
                if ind not in holdings and len(holdings) < TOP_K:
                    n = net_ratio.loc[rd, ind]
                    if not pd.isna(n) and n > -0.02:  # 不要净流出太大的
                        holdings.append(ind)
        
        # 计算区间收益
        period_dates = ret.index[(ret.index > rd) & (ret.index <= nd)]
        if len(period_dates) == 0 or len(holdings) == 0:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            records.append({'date': rd, 'holdings': list(holdings), 'ret': 0})
            continue
        
        port_ret = 0
        for ind in holdings:
            if ind in ret.columns:
                ind_rets = ret.loc[period_dates, ind].dropna()
                if len(ind_rets) > 0:
                    cum = (1 + ind_rets).prod() - 1
                    port_ret += cum / len(holdings)
        
        nav.append(nav[-1] * (1 + port_ret))
        nav_dates.append(nd)
        weekly_rets.append(port_ret)
        records.append({'date': rd, 'holdings': list(holdings), 'ret': port_ret})
    
    return pd.Series(nav, index=nav_dates), weekly_rets, records, signal_log


def backtest_ranking(factors, strategy_name='纯排名Top5'):
    """
    策略B: 纯ANB排名Top5 (研报原文)
    每周选ANB最高的5个行业, 等权持有
    """
    anb = factors['anb']
    ret = factors['ret']
    
    trade_dates = sorted(set(anb.index) & set(ret.index))
    weekly_dates = get_weekly_dates(trade_dates)
    
    nav = [1.0]
    nav_dates = [weekly_dates[0]] if weekly_dates else []
    weekly_rets = []
    records = []
    
    for i in range(len(weekly_dates) - 1):
        rd = weekly_dates[i]
        nd = weekly_dates[i + 1]
        
        if rd not in anb.index:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        anb_row = anb.loc[rd].dropna()
        if len(anb_row) < TOP_K:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        holdings = anb_row.nlargest(TOP_K).index.tolist()
        
        period_dates = ret.index[(ret.index > rd) & (ret.index <= nd)]
        if len(period_dates) == 0:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        port_ret = 0
        for ind in holdings:
            if ind in ret.columns:
                ind_rets = ret.loc[period_dates, ind].dropna()
                if len(ind_rets) > 0:
                    cum = (1 + ind_rets).prod() - 1
                    port_ret += cum / len(holdings)
        
        nav.append(nav[-1] * (1 + port_ret))
        nav_dates.append(nd)
        weekly_rets.append(port_ret)
        records.append({'date': rd, 'holdings': holdings, 'ret': port_ret})
    
    return pd.Series(nav, index=nav_dates), weekly_rets, records


def backtest_hybrid(factors, strategy_name='信号+排名混合'):
    """
    策略C: 信号过滤+ANB排名
    - 只从净流入为正的行业中选ANB Top5
    - 如果净流入为正的行业不足5个, 用ANB最高的补
    """
    anb = factors['anb']
    net_ratio = factors['net_ratio']
    ret = factors['ret']
    
    trade_dates = sorted(set(anb.index) & set(ret.index))
    weekly_dates = get_weekly_dates(trade_dates)
    
    nav = [1.0]
    nav_dates = [weekly_dates[0]] if weekly_dates else []
    weekly_rets = []
    records = []
    
    for i in range(len(weekly_dates) - 1):
        rd = weekly_dates[i]
        nd = weekly_dates[i + 1]
        
        if rd not in anb.index:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        anb_row = anb.loc[rd].dropna()
        net_row = net_ratio.loc[rd]
        
        # 先筛净流入为正的
        positive = anb_row[net_row > 0] if rd in net_ratio.index else anb_row
        if len(positive) >= TOP_K:
            holdings = positive.nlargest(TOP_K).index.tolist()
        else:
            # 不足则用全部ANB Top
            holdings = anb_row.nlargest(TOP_K).index.tolist()
        
        period_dates = ret.index[(ret.index > rd) & (ret.index <= nd)]
        if len(period_dates) == 0:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        port_ret = 0
        for ind in holdings:
            if ind in ret.columns:
                ind_rets = ret.loc[period_dates, ind].dropna()
                if len(ind_rets) > 0:
                    cum = (1 + ind_rets).prod() - 1
                    port_ret += cum / len(holdings)
        
        nav.append(nav[-1] * (1 + port_ret))
        nav_dates.append(nd)
        weekly_rets.append(port_ret)
        records.append({'date': rd, 'holdings': holdings, 'ret': port_ret})
    
    return pd.Series(nav, index=nav_dates), weekly_rets, records


def backtest_benchmark(factors):
    """基准: 29个行业等权"""
    ret = factors['ret']
    weekly_dates = get_weekly_dates(sorted(ret.index))
    
    nav = [1.0]
    nav_dates = [weekly_dates[0]] if weekly_dates else []
    weekly_rets = []
    
    for i in range(len(weekly_dates) - 1):
        rd = weekly_dates[i]
        nd = weekly_dates[i + 1]
        period_dates = ret.index[(ret.index > rd) & (ret.index <= nd)]
        if len(period_dates) == 0:
            nav.append(nav[-1])
            nav_dates.append(nd)
            weekly_rets.append(0)
            continue
        
        daily_rets = ret.loc[period_dates].mean(axis=1)  # 等权
        cum = (1 + daily_rets).prod() - 1
        nav.append(nav[-1] * (1 + cum))
        nav_dates.append(nd)
        weekly_rets.append(cum)
    
    return pd.Series(nav, index=nav_dates), weekly_rets


def calc_metrics(nav_series, weekly_rets):
    """计算绩效指标"""
    total_ret = nav_series.iloc[-1] - 1
    n_weeks = len(weekly_rets)
    ann_factor = 52 / n_weeks if n_weeks > 0 else 0
    ann_ret = (1 + total_ret) ** ann_factor - 1 if ann_factor > 0 and (1 + total_ret) > 0 else 0
    
    wr = np.array(weekly_rets)
    win_rate = (wr > 0).sum() / len(wr) if len(wr) > 0 else 0
    avg_win = wr[wr > 0].mean() if (wr > 0).any() else 0
    avg_loss = wr[wr < 0].mean() if (wr < 0).any() else 0
    
    if len(wr) > 1 and wr.std() > 0:
        sharpe = wr.mean() / wr.std() * np.sqrt(52)
    else:
        sharpe = 0
    
    # 最大回撤
    peak = nav_series.cummax()
    dd = (nav_series - peak) / peak
    max_dd = dd.min()
    
    # 月胜率
    monthly = nav_series.resample('ME').last()
    monthly_ret = monthly.pct_change().dropna()
    monthly_win = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0
    
    return {
        'total_return': total_ret,
        'ann_return': ann_ret,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'win_rate': win_rate,
        'monthly_win_rate': monthly_win,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'n_weeks': n_weeks,
    }


def plot_results(results, output_path):
    """画净值曲线图"""
    # 检查中文字体
    import matplotlib.font_manager as fm
    plt.rcParams['axes.unicode_minus'] = False
    
    zh_fonts = ['Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'SimHei', 'DejaVu Sans']
    available = set(f.name for f in fm.fontManager.ttflist)
    for f in zh_fonts:
        if f in available:
            plt.rcParams['font.sans-serif'] = [f]
            break
    
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), gridspec_kw={'height_ratios': [3, 1, 1]})
    
    # 净值曲线
    ax1 = axes[0]
    colors = ['#e74c3c', '#3498db', '#2ecc71', '#95a5a6']
    for (name, (nav, _, _)), color in zip(results['strategies'], colors):
        ax1.plot(nav.index, nav.values, label=f"{name} ({(nav.iloc[-1]-1)*100:.2f}%)", 
                color=color, linewidth=2)
    ax1.plot(results['benchmark'][0].index, results['benchmark'][0].values, 
             label=f"基准-等权 ({(results['benchmark'][0].iloc[-1]-1)*100:.2f}%)", 
             color='#7f8c8d', linewidth=1.5, linestyle='--')
    ax1.set_title('V4 真实资金流策略 2026年YTD回测 (Tushare moneyflow_ind_ths)', fontsize=14, fontweight='bold')
    ax1.legend(fontsize=10, loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax1.set_ylabel('净值')
    
    # 回撤
    ax2 = axes[1]
    for (name, (nav, _, _)), color in zip(results['strategies'], colors):
        peak = nav.cummax()
        dd = (nav - peak) / peak * 100
        ax2.fill_between(dd.index, dd.values, 0, alpha=0.3, color=color, label=name)
    ax2.set_ylabel('回撤 (%)')
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    
    # 持仓数量 (用信号驱动)
    ax3 = axes[2]
    sig_data = results['strategies'][0][1]
    sig_records = sig_data[2] if len(sig_data) >= 3 else []
    dates = [r['date'] for r in sig_records]
    n_hold = [len(r['holdings']) for r in sig_records]
    ax3.bar(dates, n_hold, color='#3498db', alpha=0.7, width=3)
    ax3.set_ylabel('持仓数量')
    ax3.set_xlabel('日期')
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图表已保存: {output_path}")


def send_feishu(metrics_all, signal_log, chart_path):
    """发送飞书报告"""
    import base64
    
    # 构建卡片消息
    elements = []
    
    # 标题
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**V4 真实资金流策略回测报告**\n数据源: Tushare moneyflow_ind_ths (L2主动买卖) | 回测: 2026年YTD"}
    })
    
    # 分割线
    elements.append({"tag": "hr"})
    
    # 各策略指标表格
    header = ["策略", "累计收益", "夏普", "最大回撤", "周胜率", "月胜率"]
    rows = []
    for name, m in metrics_all.items():
        rows.append([
            name,
            f"{m['total_return']*100:.2f}%",
            f"{m['sharpe']:.3f}",
            f"{m['max_drawdown']*100:.2f}%",
            f"{m['win_rate']*100:.1f}%",
            f"{m['monthly_win_rate']*100:.1f}%",
        ])
    
    # 用column_set构建表格
    col_widths = [120, 100, 80, 100, 80, 80]
    # 表头
    header_cols = []
    for i, h in enumerate(header):
        header_cols.append({
            "tag": "column",
            "width": "weighted",
            "weight": col_widths[i],
            "elements": [{"tag": "text", "text": {"tag": "plain_text", "content": h}, "text_style": {"bold": True}}]
        })
    elements.append({"tag": "column_set", "columns": header_cols, "flex_mode": "stretch"})
    
    for row in rows:
        row_cols = []
        for i, val in enumerate(row):
            row_cols.append({
                "tag": "column",
                "width": "weighted",
                "weight": col_widths[i],
                "elements": [{"tag": "text", "text": {"tag": "plain_text", "content": val}}]
            })
        elements.append({"tag": "column_set", "columns": row_cols, "flex_mode": "stretch"})
    
    elements.append({"tag": "hr"})
    
    # 信号统计
    if signal_log:
        n_opp = sum(1 for s in signal_log if '机会' in s['signal'])
        n_risk = sum(1 for s in signal_log if '风险' in s['signal'])
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**信号统计**: 机会信号 {n_opp} 次 | 风险信号 {n_risk} 次\n**数据覆盖**: 107个交易日 (1月/3-8月, 2月因春节缺失)\n**因子**: 主动净买入 ANB = (买入占比-卖出占比)/成交额占比, {W}日MA"}
        })
    
    # 最新持仓
    elements.append({"tag": "hr"})
    
    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "报告来自于 DoMate | V4 真实资金流回测"},
                "template": "blue"
            },
            "elements": elements
        }
    }
    
    try:
        resp = requests.post(WEBHOOK_URL, json=msg, timeout=10)
        print(f"  飞书发送: {resp.status_code} - {resp.text[:100]}")
    except Exception as e:
        print(f"  飞书发送失败: {e}")


def main():
    print("=" * 70)
    print("V4 Final: 真实资金流数据回测 (Tushare moneyflow_ind_ths)")
    print("=" * 70)
    print(f"数据源: L2逐笔成交主动买卖 | MA窗口W={W} | Top{TOP_K}")
    
    # 1. 加载数据
    print("\n[1] 加载真实资金流数据...")
    df = load_data()
    
    # 2. 计算因子
    print("\n[2] 计算主动净买入因子(研报原文方法)...")
    factors = compute_factors(df)
    
    # 最新日期ANB Top5
    last_date = factors['anb'].index[-1]
    anb_top = factors['anb'].loc[last_date].dropna().nlargest(5)
    print(f"  最新日期: {last_date.strftime('%Y-%m-%d')}")
    print(f"  ANB Top5:")
    for ind, val in anb_top.items():
        nr = factors['net_ratio'].loc[last_date, ind]
        print(f"    {ind}: ANB={val:.4f}, 净流入占比={nr:.4f}")
    
    # 3. 回测
    print("\n[3] 回测三组策略...")
    
    # A) 信号驱动
    print("  A) 信号驱动策略...")
    sig_nav, sig_rets, sig_records, signal_log = backtest_signal_driven(factors)
    sig_metrics = calc_metrics(sig_nav, sig_rets)
    print(f"     累计: {sig_metrics['total_return']*100:.2f}%, 夏普: {sig_metrics['sharpe']:.3f}, 回撤: {sig_metrics['max_drawdown']*100:.2f}%")
    
    # B) 纯排名
    print("  B) 纯ANB排名Top5 (研报原文)...")
    rank_nav, rank_rets, rank_records = backtest_ranking(factors)
    rank_metrics = calc_metrics(rank_nav, rank_rets)
    print(f"     累计: {rank_metrics['total_return']*100:.2f}%, 夏普: {rank_metrics['sharpe']:.3f}, 回撤: {rank_metrics['max_drawdown']*100:.2f}%")
    
    # C) 信号+排名混合
    print("  C) 信号+排名混合...")
    hyb_nav, hyb_rets, hyb_records = backtest_hybrid(factors)
    hyb_metrics = calc_metrics(hyb_nav, hyb_rets)
    print(f"     累计: {hyb_metrics['total_return']*100:.2f}%, 夏普: {hyb_metrics['sharpe']:.3f}, 回撤: {hyb_metrics['max_drawdown']*100:.2f}%")
    
    # 基准
    print("  基准: 29行业等权...")
    bench_nav, bench_rets = backtest_benchmark(factors)
    bench_metrics = calc_metrics(bench_nav, bench_rets)
    print(f"     累计: {bench_metrics['total_return']*100:.2f}%, 夏普: {bench_metrics['sharpe']:.3f}")
    
    # 4. 画图
    print("\n[4] 生成图表...")
    chart_path = os.path.join(OUTPUT_DIR, 'backtest_v4_chart.png')
    results = {
        'strategies': [
            ('信号驱动', (sig_nav, sig_rets, sig_records)),
            ('纯ANB排名', (rank_nav, rank_rets, rank_records)),
            ('信号+排名混合', (hyb_nav, hyb_rets, hyb_records)),
        ],
        'benchmark': (bench_nav, bench_rets),
    }
    plot_results(results, chart_path)
    
    # 5. 保存持仓记录
    print("\n[5] 保存持仓记录...")
    holdings_df = pd.DataFrame([
        {'date': r['date'], 'holdings': ','.join(r['holdings']), 'n': len(r['holdings']), 'ret': r['ret']}
        for r in sig_records
    ])
    holdings_df.to_csv(os.path.join(OUTPUT_DIR, 'backtest_v4_holdings.csv'), index=False)
    
    weekly_df = pd.DataFrame([
        {'date': r['date'], 'ret': r['ret']}
        for r in sig_records
    ])
    weekly_df.to_csv(os.path.join(OUTPUT_DIR, 'backtest_v4_weekly_returns.csv'), index=False)
    
    # 6. 汇总
    print("\n" + "=" * 70)
    print("回测结果汇总")
    print("=" * 70)
    metrics_all = {
        '信号驱动': sig_metrics,
        '纯ANB排名': rank_metrics,
        '信号+排名混合': hyb_metrics,
        '基准-等权': bench_metrics,
    }
    
    print(f"\n{'策略':<16} {'累计收益':>10} {'年化':>10} {'夏普':>8} {'最大回撤':>10} {'周胜率':>8} {'月胜率':>8}")
    print("-" * 80)
    for name, m in metrics_all.items():
        print(f"{name:<16} {m['total_return']*100:>9.2f}% {m['ann_return']*100:>9.2f}% {m['sharpe']:>8.3f} {m['max_drawdown']*100:>9.2f}% {m['win_rate']*100:>7.1f}% {m['monthly_win_rate']*100:>7.1f}%")
    
    print(f"\n信号统计: 机会 {sum(1 for s in signal_log if '机会' in s['signal'])} 次, 风险 {sum(1 for s in signal_log if '风险' in s['signal'])} 次")
    
    # 7. 发飞书
    print("\n[7] 发送飞书报告...")
    send_feishu(metrics_all, signal_log, chart_path)
    
    # 8. 保存最新信号
    print("\n[8] 保存最新信号状态...")
    last_signals = [s for s in signal_log if s['date'] == factors['anb'].index[-1]]
    with open(os.path.join(OUTPUT_DIR, 'signal_v4_latest.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'date': last_date.strftime('%Y-%m-%d'),
            'signals': [{**s, 'date': s['date'].strftime('%Y-%m-%d')} for s in last_signals],
            'anb_top5': {ind: float(val) for ind, val in anb_top.items()},
        }, f, ensure_ascii=False, indent=2)
    
    print("\n完成!")


if __name__ == '__main__':
    main()
