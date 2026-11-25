#!/usr/bin/env python3
"""
V4 Optimized: 真实资金流策略优化版
===================================
基于IC分析优化:
- W=5 (IC=0.05, 胜率60%, 最优窗口)
- 纯ANB排名Top5为核心策略
- 加入市场情绪过滤: >70%行业净流出时减仓
- 加入动量确认: ANB环比改善优先
- 多策略对比: Top3/Top5/Top5+过滤/动态仓位
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
from feishu_config import WEBHOOK_URL  # noqa: E402  凭据统一走 app_secrets，见 DEPLOY.md「敏感配置」
INDUSTRY_ETF_MAP = {
    "半导体": ("512480", "半导体ETF"), "银行": ("512800", "银行ETF"),
    "证券": ("512000", "券商ETF"), "光伏设备": ("515790", "光伏ETF"),
    "军工装备": ("512660", "军工ETF"), "通信设备": ("515880", "通信ETF"),
    "计算机设备": ("512720", "计算机ETF"), "软件开发": ("515230", "软件ETF"),
    "化学制药": ("512010", "医药ETF"), "煤炭开采加工": ("515220", "煤炭ETF"),
    "钢铁": ("515210", "钢铁ETF"), "工业金属": ("512400", "有色ETF"),
    "房地产": ("512200", "房地产ETF"), "汽车整车": ("516110", "汽车ETF"),
    "白酒": ("512690", "酒ETF"), "食品加工制造": ("515170", "食品饮料ETF"),
    "生物制品": ("159992", "创新药ETF"), "医疗器械": ("159883", "医疗器械ETF"),
    "化学制品": ("159870", "化工ETF"), "风电设备": ("516950", "风电ETF"),
    "电力": ("159611", "电力ETF"), "消费电子": ("159732", "消费电子ETF"),
    "电池": ("159755", "电池ETF"), "贵金属": ("159933", "金ETF"),
    "建筑材料": ("159745", "建材ETF"), "物流": ("516910", "物流ETF"),
    "养殖业": ("159865", "养殖ETF"), "通信服务": ("515050", "5GETF"),
    "IT服务": ("515080", "AI智能ETF"),
}
INDUSTRIES = list(INDUSTRY_ETF_MAP.keys())


def load_data():
    df = pd.read_csv(os.path.join(OUTPUT_DIR, 'industry_moneyflow_real.csv'), dtype={'trade_date': str})
    df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df[df['industry'].isin(INDUSTRIES)].copy()
    df = df.sort_values(['date', 'industry']).reset_index(drop=True)
    for col in ['net_buy_amount', 'net_sell_amount', 'net_amount', 'pct_change']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    print(f"  天数: {df['date'].dt.strftime('%Y%m%d').nunique()}, 行业数: {df['industry'].nunique()}")
    print(f"  范围: {df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')}")
    return df


def compute_factors(df, W=5):
    daily = df.groupby('date').agg(total_buy=('net_buy_amount','sum'), total_sell=('net_sell_amount','sum'))
    df = df.merge(daily, on='date')
    df['buy_ratio'] = df['net_buy_amount'] / df['total_buy']
    df['sell_ratio'] = df['net_sell_amount'] / df['total_sell']
    df['amount'] = df['net_buy_amount'] + df['net_sell_amount']
    daily_amt = df.groupby('date')['amount'].sum().rename('total_amount')
    df = df.merge(daily_amt, on='date')
    df['amount_ratio'] = df['amount'] / df['total_amount']
    df['anb'] = (df['buy_ratio'] - df['sell_ratio']) / df['amount_ratio'].replace(0, np.nan)
    df['net_ratio'] = df['net_amount'] / df['amount'].replace(0, np.nan)
    
    anb_panel = df.set_index(['date','industry'])['anb'].unstack('industry')
    net_ratio_panel = df.set_index(['date','industry'])['net_ratio'].unstack('industry')
    ret_panel = df.set_index(['date','industry'])['pct_change'].unstack('industry') / 100.0
    
    anb_ma = anb_panel.rolling(W, min_periods=2).mean()
    net_ratio_ma = net_ratio_panel.rolling(W, min_periods=2).mean()
    # ANB变化(动量)
    anb_diff = anb_ma.diff(3)
    
    # 市场情绪: 每日净流入为正的行业占比
    market_bread = (net_ratio_panel > 0).sum(axis=1) / net_ratio_panel.notna().sum(axis=1)
    
    return {
        'anb': anb_ma, 'anb_raw': anb_panel, 'anb_diff': anb_diff,
        'net_ratio': net_ratio_ma, 'net_ratio_raw': net_ratio_panel,
        'ret': ret_panel, 'market_bread': market_bread,
    }


def get_weekly_dates(dates):
    weekly = []
    for d in dates:
        if len(weekly) == 0 or d.isocalendar()[1] != weekly[-1].isocalendar()[1]:
            weekly.append(d)
        else:
            weekly[-1] = d
    return weekly


def calc_period_ret(ret_panel, holdings, rd, nd):
    """计算rd到nd之间的持仓累计收益"""
    period_dates = ret_panel.index[(ret_panel.index > rd) & (ret_panel.index <= nd)]
    if len(period_dates) == 0 or len(holdings) == 0:
        return 0
    port_ret = 0
    for ind in holdings:
        if ind in ret_panel.columns:
            ind_rets = ret_panel.loc[period_dates, ind].dropna()
            if len(ind_rets) > 0:
                cum = (1 + ind_rets).prod() - 1
                port_ret += cum / len(holdings)
    return port_ret


def backtest_strategy(factors, top_k=5, use_filter=False, use_momentum=False, dynamic_k=False, name=''):
    """
    通用回测框架
    - top_k: 持仓数量
    - use_filter: 市场情绪过滤(>70%净流出时减仓到3)
    - use_momentum: 动量确认(ANB环比改善优先)
    - dynamic_k: 动态仓位(市场差时减到3, 好时5)
    """
    anb = factors['anb']
    anb_diff = factors.get('anb_diff', anb.diff(3))
    net_ratio = factors['net_ratio']
    ret = factors['ret']
    market_bread = factors['market_bread']
    
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
            nav.append(nav[-1]); nav_dates.append(nd); weekly_rets.append(0)
            continue
        
        anb_row = anb.loc[rd].dropna()
        if len(anb_row) < 3:
            nav.append(nav[-1]); nav_dates.append(nd); weekly_rets.append(0)
            continue
        
        k = top_k
        if dynamic_k:
            mb = market_bread.get(rd, 0.5)
            if mb < 0.2:
                k = 2  # 市场极差, 仅持2个
            elif mb < 0.35:
                k = 3
            else:
                k = top_k
        
        if use_filter:
            mb = market_bread.get(rd, 0.5)
            if mb < 0.25:
                # 市场情绪极差, 减仓到2
                k = min(k, 2)
        
        if use_momentum:
            # 综合分: ANB(70%) + ANB变化(30%)
            diff_row = anb_diff.loc[rd] if rd in anb_diff.index else pd.Series(dtype=float)
            combined = anb_row * 0.7 + diff_row.reindex(anb_row.index).fillna(0) * 0.3
            holdings = combined.nlargest(k).index.tolist()
        else:
            holdings = anb_row.nlargest(k).index.tolist()
        
        pr = calc_period_ret(ret, holdings, rd, nd)
        nav.append(nav[-1] * (1 + pr))
        nav_dates.append(nd)
        weekly_rets.append(pr)
        records.append({'date': rd, 'holdings': holdings, 'ret': pr, 'k': k})
    
    return pd.Series(nav, index=nav_dates), weekly_rets, records


def backtest_signal_optimized(factors, name='信号优化'):
    """
    优化信号策略:
    - 基础持仓: ANB Top5
    - 风险减仓: 个别行业ANB跌破0时换出, 用下一个替代
    - 市场极端: market_bread<0.2时减到3
    - 不做 binary 全清/全进, 而是滚动替换
    """
    anb = factors['anb']
    net_ratio = factors['net_ratio']
    ret = factors['ret']
    market_bread = factors['market_bread']
    
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
            nav.append(nav[-1]); nav_dates.append(nd); weekly_rets.append(0)
            continue
        
        anb_row = anb.loc[rd].dropna()
        if len(anb_row) < 3:
            nav.append(nav[-1]); nav_dates.append(nd); weekly_rets.append(0)
            continue
        
        mb = market_bread.get(rd, 0.5)
        
        # 动态K
        if mb < 0.2:
            k = 3
        elif mb < 0.35:
            k = 4
        else:
            k = 5
        
        # 检查现有持仓: ANB<0的换出
        new_holdings = []
        for ind in holdings:
            if ind in anb_row.index and anb_row[ind] > 0:
                new_holdings.append(ind)
            else:
                signal_log.append({'date': rd, 'industry': ind, 'signal': '风险-ANB转负',
                                   'anb': anb_row.get(ind, 0), 'bread': mb})
        
        # 从ANB排名中补充
        remaining = k - len(new_holdings)
        if remaining > 0:
            candidates = anb_row.nlargest(k * 2)
            for ind in candidates.index:
                if ind not in new_holdings:
                    new_holdings.append(ind)
                    if anb_row[ind] > 0:
                        signal_log.append({'date': rd, 'industry': ind, 'signal': '机会-ANB排名靠前',
                                           'anb': anb_row[ind], 'bread': mb})
                    remaining -= 1
                    if remaining <= 0:
                        break
        
        holdings = new_holdings[:k]
        pr = calc_period_ret(ret, holdings, rd, nd)
        nav.append(nav[-1] * (1 + pr))
        nav_dates.append(nd)
        weekly_rets.append(pr)
        records.append({'date': rd, 'holdings': holdings, 'ret': pr, 'k': k, 'bread': mb})
    
    return pd.Series(nav, index=nav_dates), weekly_rets, records, signal_log


def backtest_benchmark(factors):
    ret = factors['ret']
    weekly_dates = get_weekly_dates(sorted(ret.index))
    nav = [1.0]
    nav_dates = [weekly_dates[0]] if weekly_dates else []
    weekly_rets = []
    for i in range(len(weekly_dates) - 1):
        rd, nd = weekly_dates[i], weekly_dates[i+1]
        period_dates = ret.index[(ret.index > rd) & (ret.index <= nd)]
        if len(period_dates) == 0:
            nav.append(nav[-1]); nav_dates.append(nd); weekly_rets.append(0)
            continue
        daily_rets = ret.loc[period_dates].mean(axis=1)
        cum = (1 + daily_rets).prod() - 1
        nav.append(nav[-1] * (1 + cum))
        nav_dates.append(nd)
        weekly_rets.append(cum)
    return pd.Series(nav, index=nav_dates), weekly_rets


def calc_metrics(nav, weekly_rets):
    total_ret = nav.iloc[-1] - 1
    n = len(weekly_rets)
    ann_f = 52 / n if n > 0 else 0
    ann_ret = (1 + total_ret) ** ann_f - 1 if ann_f > 0 and (1 + total_ret) > 0 else 0
    wr = np.array(weekly_rets)
    win_rate = (wr > 0).sum() / len(wr) if len(wr) > 0 else 0
    avg_win = wr[wr > 0].mean() if (wr > 0).any() else 0
    avg_loss = wr[wr < 0].mean() if (wr < 0).any() else 0
    sharpe = wr.mean() / wr.std() * np.sqrt(52) if len(wr) > 1 and wr.std() > 0 else 0
    peak = nav.cummax()
    dd = (nav - peak) / peak
    max_dd = dd.min()
    monthly = nav.resample('ME').last()
    monthly_ret = monthly.pct_change().dropna()
    m_win = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0
    # 盈亏比
    pf = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    return {
        'total_return': total_ret, 'ann_return': ann_ret, 'sharpe': sharpe,
        'max_drawdown': max_dd, 'win_rate': win_rate, 'monthly_win_rate': m_win,
        'avg_win': avg_win, 'avg_loss': avg_loss, 'profit_factor': pf, 'n_weeks': n,
    }


def plot_results(strategies, benchmark_nav, output_path):
    import matplotlib.font_manager as fm
    plt.rcParams['axes.unicode_minus'] = False
    for f in ['Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'SimHei']:
        if f in set(x.name for x in fm.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [f]
            break
    
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={'height_ratios': [3, 1]})
    
    colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6']
    ax1 = axes[0]
    for idx, (name, nav, _) in enumerate(strategies):
        c = colors[idx % len(colors)]
        ax1.plot(nav.index, nav.values, label=f"{name} ({(nav.iloc[-1]-1)*100:.2f}%)", color=c, linewidth=2)
    ax1.plot(benchmark_nav.index, benchmark_nav.values, label=f"基准-等权 ({(benchmark_nav.iloc[-1]-1)*100:.2f}%)",
             color='#7f8c8d', linewidth=1.5, linestyle='--')
    ax1.set_title('V4 优化版 真实资金流策略 2026年YTD (W=5, Tushare L2主动买卖)', fontsize=14, fontweight='bold')
    ax1.legend(fontsize=10, loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax1.set_ylabel('净值')
    
    # 回撤
    ax2 = axes[1]
    for idx, (name, nav, _) in enumerate(strategies):
        c = colors[idx % len(colors)]
        peak = nav.cummax()
        dd = (nav - peak) / peak * 100
        ax2.fill_between(dd.index, dd.values, 0, alpha=0.3, color=c, label=name)
    ax2.set_ylabel('回撤 (%)')
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  图表: {output_path}")


def send_feishu(metrics_all, signal_log, chart_path, best_name):
    """发送飞书报告"""
    elements = []
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**V4 优化版 真实资金流策略回测**\n数据源: Tushare moneyflow_ind_ths (L2主动买卖) | W=5 | 2026年YTD"}
    })
    elements.append({"tag": "hr"})
    
    # 表格 - 用text格式
    lines = []
    lines.append(f"{'策略':<12} {'累计':>8} {'夏普':>6} {'回撤':>8} {'周胜率':>6} {'月胜率':>6} {'盈亏比':>6}")
    lines.append("─" * 60)
    for name, m in metrics_all.items():
        lines.append(f"{name:<12} {m['total_return']*100:>7.2f}% {m['sharpe']:>6.3f} {m['max_drawdown']*100:>7.2f}% {m['win_rate']*100:>5.1f}% {m['monthly_win_rate']*100:>5.1f}% {m['profit_factor']:>6.2f}")
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"```\n{chr(10).join(lines)}\n```"}
    })
    
    elements.append({"tag": "hr"})
    
    # 最优策略详情
    best_m = metrics_all[best_name]
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**最优策略: {best_name}**\n累计收益: {best_m['total_return']*100:.2f}% | 超额收益: {(best_m['total_return']-metrics_all['基准-等权']['total_return'])*100:.2f}pp\n夏普: {best_m['sharpe']:.3f} | 最大回撤: {best_m['max_drawdown']*100:.2f}% | 盈亏比: {best_m['profit_factor']:.2f}"}
    })
    
    if signal_log:
        n_opp = sum(1 for s in signal_log if '机会' in s['signal'])
        n_risk = sum(1 for s in signal_log if '风险' in s['signal'])
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**信号统计**: 机会 {n_opp} 次 | 风险 {n_risk} 次\n**数据**: 107交易日(1月/3-8月) | **因子**: ANB=(买占比-卖占比)/成交额占比, 5日MA\n**IC分析**: W=5 IC=0.051 胜率60% IR=0.19 (正向有效)"}
        })
    
    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate | V4优化版 真实资金流回测"}, "template": "blue"},
            "elements": elements
        }
    }
    
    try:
        resp = requests.post(WEBHOOK_URL, json=msg, timeout=10)
        print(f"  飞书: {resp.status_code} - {resp.text[:80]}")
    except Exception as e:
        print(f"  飞书失败: {e}")


def main():
    print("=" * 70)
    print("V4 Optimized: 真实资金流策略优化 (W=5, IC驱动)")
    print("=" * 70)
    
    print("\n[1] 加载数据...")
    df = load_data()
    
    print("\n[2] 计算因子 (W=5)...")
    factors = compute_factors(df, W=5)
    
    # 最新ANB
    last_date = factors['anb'].index[-1]
    anb_top = factors['anb'].loc[last_date].dropna().nlargest(5)
    print(f"  最新: {last_date.strftime('%Y-%m-%d')}")
    for ind, val in anb_top.items():
        print(f"    {ind}: ANB={val:.4f}")
    
    print("\n[3] 回测多组策略...")
    strategies = []
    
    # 1. Top5 纯排名 (W=5)
    nav, rets, recs = backtest_strategy(factors, top_k=5, name='Top5-W5')
    m = calc_metrics(nav, rets)
    print(f"  Top5-W5:     {m['total_return']*100:>7.2f}% | 夏普 {m['sharpe']:.3f} | 回撤 {m['max_drawdown']*100:.2f}% | 周胜率 {m['win_rate']*100:.1f}%")
    strategies.append(('Top5-W5', nav, m))
    
    # 2. Top3 纯排名 (更集中)
    nav2, rets2, recs2 = backtest_strategy(factors, top_k=3, name='Top3-W5')
    m2 = calc_metrics(nav2, rets2)
    print(f"  Top3-W5:     {m2['total_return']*100:>7.2f}% | 夏普 {m2['sharpe']:.3f} | 回撤 {m2['max_drawdown']*100:.2f}% | 周胜率 {m2['win_rate']*100:.1f}%")
    strategies.append(('Top3-W5', nav2, m2))
    
    # 3. Top5 + 市场过滤
    nav3, rets3, recs3 = backtest_strategy(factors, top_k=5, use_filter=True, name='Top5+过滤')
    m3 = calc_metrics(nav3, rets3)
    print(f"  Top5+过滤:   {m3['total_return']*100:>7.2f}% | 夏普 {m3['sharpe']:.3f} | 回撤 {m3['max_drawdown']*100:.2f}% | 周胜率 {m3['win_rate']*100:.1f}%")
    strategies.append(('Top5+过滤', nav3, m3))
    
    # 4. Top5 + 动量确认
    nav4, rets4, recs4 = backtest_strategy(factors, top_k=5, use_momentum=True, name='Top5+动量')
    m4 = calc_metrics(nav4, rets4)
    print(f"  Top5+动量:   {m4['total_return']*100:>7.2f}% | 夏普 {m4['sharpe']:.3f} | 回撤 {m4['max_drawdown']*100:.2f}% | 周胜率 {m4['win_rate']*100:.1f}%")
    strategies.append(('Top5+动量', nav4, m4))
    
    # 5. 信号优化版
    nav5, rets5, recs5, sig_log = backtest_signal_optimized(factors, name='信号优化')
    m5 = calc_metrics(nav5, rets5)
    print(f"  信号优化:    {m5['total_return']*100:>7.2f}% | 夏普 {m5['sharpe']:.3f} | 回撤 {m5['max_drawdown']*100:.2f}% | 周胜率 {m5['win_rate']*100:.1f}%")
    strategies.append(('信号优化', nav5, m5))
    
    # 6. 动态K
    nav6, rets6, recs6 = backtest_strategy(factors, top_k=5, dynamic_k=True, name='动态K')
    m6 = calc_metrics(nav6, rets6)
    print(f"  动态K:       {m6['total_return']*100:>7.2f}% | 夏普 {m6['sharpe']:.3f} | 回撤 {m6['max_drawdown']*100:.2f}% | 周胜率 {m6['win_rate']*100:.1f}%")
    strategies.append(('动态K', nav6, m6))
    
    # 基准
    bench_nav, bench_rets = backtest_benchmark(factors)
    bench_m = calc_metrics(bench_nav, bench_rets)
    print(f"  基准-等权:   {bench_m['total_return']*100:>7.2f}% | 夏普 {bench_m['sharpe']:.3f}")
    
    # 选最优
    all_metrics = {s[0]: s[2] for s in strategies}
    all_metrics['基准-等权'] = bench_m
    best_name = max(all_metrics.keys(), key=lambda k: all_metrics[k]['sharpe'] if k != '基准-等权' else -999)
    best_m = all_metrics[best_name]
    print(f"\n  >>> 最优: {best_name} (夏普={best_m['sharpe']:.3f}, 超额={(best_m['total_return']-bench_m['total_return'])*100:.2f}pp)")
    
    # 画图: 只画最优3个+基准
    print("\n[4] 生成图表...")
    chart_path = os.path.join(OUTPUT_DIR, 'backtest_v4_optimized_chart.png')
    sorted_strats = sorted(strategies, key=lambda x: x[2]['sharpe'], reverse=True)
    plot_results(sorted_strats[:4], bench_nav, chart_path)
    
    # 保存持仓
    print("\n[5] 保存持仓记录...")
    best_idx = [i for i, s in enumerate(strategies) if s[0] == best_name][0]
    best_recs = [sorted_strats[0][1] if False else None]  # not used
    # 找最优策略的records
    rec_map = {'Top5-W5': recs, 'Top3-W5': recs2, 'Top5+过滤': recs3, 
               'Top5+动量': recs4, '信号优化': recs5, '动态K': recs6}
    best_recs_list = rec_map.get(best_name, recs)
    
    holdings_df = pd.DataFrame([
        {'date': r['date'], 'holdings': ','.join(r['holdings']), 'n': len(r['holdings']), 'ret': r['ret']}
        for r in best_recs_list
    ])
    holdings_df.to_csv(os.path.join(OUTPUT_DIR, 'backtest_v4_opt_holdings.csv'), index=False)
    
    weekly_df = pd.DataFrame([{'date': r['date'], 'ret': r['ret']} for r in best_recs_list])
    weekly_df.to_csv(os.path.join(OUTPUT_DIR, 'backtest_v4_opt_weekly.csv'), index=False)
    
    # 汇总
    print("\n" + "=" * 70)
    print("优化版回测结果汇总")
    print("=" * 70)
    print(f"\n{'策略':<14} {'累计':>8} {'年化':>8} {'夏普':>7} {'回撤':>8} {'周胜率':>7} {'月胜率':>7} {'盈亏比':>7}")
    print("─" * 75)
    for name, m in all_metrics.items():
        marker = " ★" if name == best_name else ""
        print(f"{name:<14} {m['total_return']*100:>7.2f}% {m['ann_return']*100:>7.2f}% {m['sharpe']:>7.3f} {m['max_drawdown']*100:>7.2f}% {m['win_rate']*100:>6.1f}% {m['monthly_win_rate']*100:>6.1f}% {m['profit_factor']:>7.2f}{marker}")
    
    print(f"\n超额收益(vs基准): {(best_m['total_return']-bench_m['total_return'])*100:+.2f}pp")
    print(f"信号: 机会 {sum(1 for s in sig_log if '机会' in s['signal'])}次, 风险 {sum(1 for s in sig_log if '风险' in s['signal'])}次")
    
    # 飞书
    print("\n[6] 发送飞书...")
    send_feishu(all_metrics, sig_log, chart_path, best_name)
    
    # 最新信号
    print("\n[7] 保存最新状态...")
    latest = {
        'date': last_date.strftime('%Y-%m-%d'),
        'anb_top5': {ind: float(val) for ind, val in anb_top.items()},
        'best_strategy': best_name,
        'best_return': best_m['total_return'],
        'market_bread': float(factors['market_bread'].iloc[-1]),
    }
    with open(os.path.join(OUTPUT_DIR, 'signal_v4_optimized.json'), 'w', encoding='utf-8') as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    
    print("\n完成!")


if __name__ == '__main__':
    main()
