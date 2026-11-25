#!/usr/bin/env python3
"""
V4: 真实资金流数据回测
======================
数据源: Tushare moneyflow_ind_ths (L2逐笔成交主动买卖)
替换V3的CMF代理, 用真实资金流构建因子和信号

核心因子 (复现研报原文):
  主动净买入 = (买入占比 - 卖出占比) / 成交额占比, 做W日MA
  其中: 买入占比 = 行业买入额 / 全市场买入额
        卖出占比 = 行业卖出额 / 全市场卖出额
        成交额占比 = 行业成交额 / 全市场成交额

信号:
  资金净流入转正 = 机会信号
  资金净流入转负 = 风险信号
"""

import pandas as pd
import numpy as np
from datetime import datetime
import warnings
import json
import os
import requests
import akshare as ak

warnings.filterwarnings('ignore')

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

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

W = 10  # MA窗口, 因数据只有46天, 用较短窗口
TOP_K = 5
MAX_HOLDINGS = 5
TRADE_START = "2026-01-01"


def load_real_moneyflow():
    """加载真实资金流数据"""
    df = pd.read_csv(os.path.join(OUTPUT_DIR, 'industry_moneyflow_real.csv'))
    df['trade_date'] = df['trade_date'].astype(str)
    df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    # 只保留我们映射表中的行业
    df = df[df['industry'].isin(INDUSTRY_ETF_MAP.keys())].copy()
    return df.sort_values(['industry', 'date']).reset_index(drop=True)


def get_industry_data(name):
    """获取行业指数历史数据(用于计算收益)"""
    df = ak.stock_board_industry_index_ths(symbol=name, start_date="20260101", end_date="20260814")
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期').reset_index(drop=True)
    df.rename(columns={'日期':'date','收盘价':'close','成交额':'amount'}, inplace=True)
    df['ret'] = df['close'].pct_change()
    return df[['date','close','amount','ret']]


def get_etf_data(code):
    prefix = "sh" if code.startswith("5") else "sz"
    df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['ret'] = df['close'].pct_change()
    return df[['date','close','ret']]


def compute_anb_factor(mf_df):
    """
    研报原文: 主动净买入因子
    Qi = (买入占比 - 卖出占比) / 成交额占比
    做W日移动平均
    
    net_buy_amount = 主动买入金额(亿)
    net_sell_amount = 主动卖出金额(亿)
    net_amount = 净额(亿) = 买入 - 卖出
    成交额 = 买入 + 卖出 (近似)
    """
    # 按日期汇总全市场
    daily_totals = mf_df.groupby('date').agg({
        'net_buy_amount': 'sum',
        'net_sell_amount': 'sum',
    }).rename(columns={'net_buy_amount': 'total_buy', 'net_sell_amount': 'total_sell'})
    
    # 合并回去计算占比
    mf_df = mf_df.merge(daily_totals, on='date')
    mf_df['buy_ratio'] = mf_df['net_buy_amount'] / mf_df['total_buy']
    mf_df['sell_ratio'] = mf_df['net_sell_amount'] / mf_df['total_sell']
    mf_df['amount'] = mf_df['net_buy_amount'] + mf_df['net_sell_amount']
    
    daily_amt_total = mf_df.groupby('date')['amount'].sum().rename('total_amount')
    mf_df = mf_df.merge(daily_amt_total, on='date')
    mf_df['amount_ratio'] = mf_df['amount'] / mf_df['total_amount']
    
    # 主动净买入因子
    mf_df['anb'] = (mf_df['buy_ratio'] - mf_df['sell_ratio']) / mf_df['amount_ratio'].replace(0, np.nan)
    
    # 净流入占比 (净额 / 总成交额)
    mf_df['net_ratio'] = mf_df['net_amount'] / mf_df['amount'].replace(0, np.nan)
    
    # W日MA
    panels = mf_df.set_index(['date', 'industry'])
    
    # 构建面板
    anb_panel = panels['anb'].unstack('industry')
    net_ratio_panel = panels['net_ratio'].unstack('industry')
    buy_panel = panels['net_buy_amount'].unstack('industry')
    sell_panel = panels['net_sell_amount'].unstack('industry')
    net_panel = panels['net_amount'].unstack('industry')
    pct_change_panel = panels['pct_change'].unstack('industry')
    
    # W日MA
    anb_ma = anb_panel.rolling(window=W, min_periods=W-2).mean()
    net_ratio_ma = net_ratio_panel.rolling(window=W, min_periods=W-2).mean()
    net_ma = net_panel.rolling(window=W, min_periods=W-2).mean()
    
    return {
        'anb': anb_ma,              # 主动净买入因子(研报核心)
        'net_ratio': net_ratio_ma,  # 净流入占比
        'net_amount': net_ma,       # 净额MA
        'buy': buy_panel,           # 买入额
        'sell': sell_panel,         # 卖出额
        'net': net_panel,           # 净额
        'pct_change': pct_change_panel,  # 行业涨跌幅
        'anb_raw': anb_panel,       # 原始ANB(未做MA)
        'net_ratio_raw': net_ratio_panel,  # 原始净流入占比
    }


def generate_signals(factors):
    """
    用真实资金流生成信号 (V4改进版)
    ==============================
    不只看穿0, 还看趋势变化:
    机会: 净流入占比上穿0, 或净流入占比从低位加速上升
    风险: 净流入占比下穿0, 或净流入占比从高位加速下降(即使仍为正)
    """
    net_ratio = factors['net_ratio']
    anb = factors['anb']
    net_raw = factors['net_ratio_raw']
    
    dates = net_ratio.index
    industries = net_ratio.columns
    
    signals = pd.DataFrame(0, index=dates, columns=industries, dtype=float)
    strength = pd.DataFrame(0.0, index=dates, columns=industries)
    
    for ind in industries:
        nr = net_ratio[ind]
        nr_prev = nr.shift(1)
        nr_prev3 = nr.shift(3)  # 3期前
        anb_val = anb[ind]
        anb_prev = anb_val.shift(1)
        
        # 机会: 净流入占比上穿0
        opp_cross = (nr_prev < 0) & (nr >= 0)
        # 机会: ANB从负转正
        opp_anb = (anb_prev < 0) & (anb_val >= 0)
        # 机会: 净流入占比从低位大幅改善(3日内从负转正或显著上升)
        opp_accel = (nr_prev3 < -0.01) & (nr > 0) & (nr > nr_prev)
        
        # 风险: 净流入占比下穿0
        risk_cross = (nr_prev > 0) & (nr <= 0)
        # 风险: ANB从正转负
        risk_anb = (anb_prev > 0) & (anb_val <= 0)
        # 风险: 净流入占比从高位显著下降(即使仍为正, 流入在减速)
        risk_decel = (nr_prev > 0.01) & (nr < nr_prev) & (nr < nr_prev3) & (nr < 0.005)
        
        # 持有: 净流入>0且ANB>0
        hold = (nr > 0) & (anb_val > 0) & ~opp_cross & ~opp_anb & ~opp_accel
        # 空仓: 净流入<0且ANB<0
        empty = (nr < 0) & (anb_val < 0) & ~risk_cross & ~risk_anb
        # 预警: 净流入>0但ANB<0, 或净流入<0但ANB>0 (矛盾信号)
        warning = ((nr > 0) & (anb_val < 0)) | ((nr < 0) & (anb_val > 0))
        warning = warning & ~opp_cross & ~opp_anb & ~risk_cross & ~risk_anb
        
        signals[ind] = np.where(opp_cross | opp_anb | opp_accel, 1.0,
                       np.where(risk_cross | risk_anb | risk_decel, -1.0,
                       np.where(warning, -0.5,
                       np.where(hold, 0.5,
                       np.where(empty, -0.5, 0.0)))))
        
        # 信号强度
        strength[ind] = nr * 0.4 + anb_val * 0.6
    
    return signals, strength


def run_backtest(factors, signals, strength, ret_panel, etf_data_dict):
    """回测: 信号驱动 + 周度调仓"""
    print("\n运行V4回测...")
    
    trade_dates = sorted(set(signals.index) & set(ret_panel.index))
    
    # 找每周最后交易日
    weekly_dates = []
    for d in trade_dates:
        if len(weekly_dates) == 0 or d.isocalendar()[1] != weekly_dates[-1].isocalendar()[1]:
            weekly_dates.append(d)
        else:
            weekly_dates[-1] = d
    
    holdings = []
    weekly_records = []
    signal_records = []
    
    for i in range(len(weekly_dates) - 1):
        rd = weekly_dates[i]
        nd = weekly_dates[i + 1]
        
        if rd not in signals.index:
            continue
        
        # 1. 风险信号: 清仓
        new_holdings = []
        for ind in holdings:
            sig = signals.loc[rd, ind] if ind in signals.columns else 0
            if sig >= -0.5:
                new_holdings.append(ind)
            else:
                signal_records.append({
                    'date': rd, 'industry': ind, 'signal': '风险-清仓',
                    'net_ratio': factors['net_ratio'].loc[rd, ind],
                    'anb': factors['anb'].loc[rd, ind],
                })
        holdings = new_holdings
        
        # 2. 机会信号: 按信号强度排序建仓
        candidates = []
        for ind in signals.columns:
            if ind in holdings:
                continue
            sig = signals.loc[rd, ind] if ind in signals.columns else 0
            if sig >= 0.5:
                s = strength.loc[rd, ind]
                nr = factors['net_ratio'].loc[rd, ind]
                anb_val = factors['anb'].loc[rd, ind]
                candidates.append((ind, s, sig, nr, anb_val))
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        for ind, s, sig, nr, anb_val in candidates:
            if len(holdings) >= MAX_HOLDINGS:
                break
            holdings.append(ind)
            sig_label = '机会-建仓' if sig >= 1.0 else '机会-补仓'
            signal_records.append({
                'date': rd, 'industry': ind, 'signal': sig_label,
                'net_ratio': nr, 'anb': anb_val, 'strength': s,
            })
        
        # 3. 如果空仓, 用ANB最高的兜底
        if not holdings:
            anb_row = factors['anb'].loc[rd].dropna()
            if len(anb_row) > 0:
                top = anb_row.nlargest(min(3, len(anb_row)))
                for ind in top.index:
                    holdings.append(ind)
                    signal_records.append({
                        'date': rd, 'industry': ind, 'signal': 'ANB最高-兜底',
                        'net_ratio': factors['net_ratio'].loc[rd, ind],
                        'anb': top[ind],
                    })
        
        # 4. 计算收益
        if holdings:
            ind_ret = np.mean([ret_panel.loc[nd, ind] if nd in ret_panel.index and ind in ret_panel.columns else 0 
                              for ind in holdings])
            etf_ret = 0
            valid = 0
            for ind in holdings:
                if ind in INDUSTRY_ETF_MAP:
                    code = INDUSTRY_ETF_MAP[ind][0]
                    if code in etf_data_dict:
                        etf_df = etf_data_dict[code]
                        mask = (etf_df['date'] > rd) & (etf_df['date'] <= nd)
                        period_ret = etf_df.loc[mask, 'ret'].sum()
                        if not np.isnan(period_ret):
                            etf_ret += period_ret
                            valid += 1
            etf_ret = etf_ret / valid if valid > 0 else 0
        else:
            ind_ret = 0
            etf_ret = 0
        
        bench_ret = ret_panel.loc[nd].mean() if nd in ret_panel.index else 0
        
        weekly_records.append({
            'date': nd,
            'return': ind_ret,
            'etf_return': etf_ret,
            'bench_return': bench_ret,
            'n_holdings': len(holdings),
            'holdings': ', '.join([f"{ind}({INDUSTRY_ETF_MAP[ind][1]})" for ind in holdings]),
        })
    
    return pd.DataFrame(weekly_records).set_index('date'), signal_records


def calc_metrics(returns, name):
    if len(returns) == 0:
        return {}
    cum = (1 + returns).cumprod()
    total = cum.iloc[-1] - 1
    ann = (1 + total) ** (52/len(returns)) - 1
    vol = returns.std() * np.sqrt(52)
    sharpe = ann / vol if vol > 0 else 0
    peak = cum.expanding().max()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    win = (returns > 0).mean()
    calmar = ann / abs(max_dd) if max_dd != 0 else 0
    monthly = (1 + returns).cumprod().resample('ME').last().pct_change().dropna()
    monthly_win = (monthly > 0).mean() if len(monthly) > 0 else 0
    return {
        'name': name,
        'total_return': total * 100,
        'annualized_return': ann * 100,
        'annualized_vol': vol * 100,
        'sharpe': sharpe,
        'max_drawdown': max_dd * 100,
        'win_rate_weekly': win * 100,
        'win_rate_monthly': monthly_win * 100,
        'calmar': calmar,
        'n_periods': len(returns),
    }


def main():
    print("=" * 70)
    print("V4: 真实资金流数据回测 (Tushare moneyflow_ind_ths)")
    print("=" * 70)
    print(f"数据源: L2逐笔成交主动买卖 | MA窗口W={W} | Top{TOP_K}")
    
    # Step 1: 加载真实资金流数据
    print("\n[1] 加载真实资金流数据...")
    mf_df = load_real_moneyflow()
    dates = sorted(mf_df['date'].unique())
    print(f"  天数: {len(dates)}, 行业数: {mf_df['industry'].nunique()}")
    print(f"  日期范围: {dates[0].date()} ~ {dates[-1].date()}")
    
    # Step 2: 计算因子
    print("\n[2] 计算主动净买入因子(研报原文方法)...")
    factors = compute_anb_factor(mf_df)
    
    # 看看因子值
    latest_date = factors['anb'].dropna().index[-1]
    print(f"  最新日期: {latest_date.date()}")
    print(f"  ANB Top5: ")
    anb_top = factors['anb'].loc[latest_date].dropna().nlargest(5)
    for ind, val in anb_top.items():
        print(f"    {ind}: ANB={val:.4f}, 净流入占比={factors['net_ratio'].loc[latest_date, ind]:.4f}")
    
    # Step 3: 生成信号
    print("\n[3] 生成信号...")
    signals, strength = generate_signals(factors)
    
    trade_signals = signals.loc[signals.index]
    opp_count = (trade_signals >= 1.0).sum().sum()
    risk_count = (trade_signals <= -1.0).sum().sum()
    hold_count = (trade_signals == 0.5).sum().sum()
    empty_count = (trade_signals == -0.5).sum().sum()
    print(f"  机会信号: {opp_count}次")
    print(f"  风险信号: {risk_count}次")
    print(f"  持有信号: {hold_count}次")
    print(f"  空仓信号: {empty_count}次")
    
    # Step 4: 获取行业指数和ETF数据
    print("\n[4] 获取行业指数和ETF数据...")
    industry_data_dict = {}
    etf_data_dict = {}
    
    for name, (code, etf_name) in INDUSTRY_ETF_MAP.items():
        try:
            df = get_industry_data(name)
            industry_data_dict[name] = df
        except:
            pass
        try:
            etf_df = get_etf_data(code)
            etf_data_dict[code] = etf_df
        except:
            pass
    print(f"  行业: {len(industry_data_dict)}, ETF: {len(etf_data_dict)}")
    
    # 构建收益面板
    all_dates = None
    for name, df in industry_data_dict.items():
        d = df['date'].tolist()
        if all_dates is None:
            all_dates = d
        else:
            all_dates = sorted(set(all_dates) & set(d))
    
    ret_panel = pd.DataFrame(index=all_dates)
    for name, df in industry_data_dict.items():
        ret_panel[name] = df.set_index('date')['ret']
    
    # Step 5: 回测
    print("\n[5] 回测...")
    weekly_df, signal_records = run_backtest(factors, signals, strength, ret_panel, etf_data_dict)
    
    # Step 6: 也做一版纯排名策略 (研报方法: 每周选ANB最高的Top5)
    print("\n[6] 纯排名策略 (研报原文方法)...")
    rank_holdings = []
    rank_records = []
    
    trade_dates_rank = sorted(set(factors['anb'].dropna().index) & set(ret_panel.index))
    weekly_dates_rank = []
    for d in trade_dates_rank:
        if len(weekly_dates_rank) == 0 or d.isocalendar()[1] != weekly_dates_rank[-1].isocalendar()[1]:
            weekly_dates_rank.append(d)
        else:
            weekly_dates_rank[-1] = d
    
    for i in range(len(weekly_dates_rank) - 1):
        rd = weekly_dates_rank[i]
        nd = weekly_dates_rank[i + 1]
        
        if rd not in factors['anb'].index:
            continue
        anb_row = factors['anb'].loc[rd].dropna()
        if len(anb_row) < TOP_K:
            continue
        
        selected = anb_row.nlargest(TOP_K).index.tolist()
        
        ind_ret = np.mean([ret_panel.loc[nd, ind] if nd in ret_panel.index and ind in ret_panel.columns else 0 
                          for ind in selected])
        
        etf_ret = 0
        valid = 0
        for ind in selected:
            if ind in INDUSTRY_ETF_MAP:
                code = INDUSTRY_ETF_MAP[ind][0]
                if code in etf_data_dict:
                    etf_df = etf_data_dict[code]
                    mask = (etf_df['date'] > rd) & (etf_df['date'] <= nd)
                    period_ret = etf_df.loc[mask, 'ret'].sum()
                    if not np.isnan(period_ret):
                        etf_ret += period_ret
                        valid += 1
        etf_ret = etf_ret / valid if valid > 0 else 0
        
        bench_ret = ret_panel.loc[nd].mean() if nd in ret_panel.index else 0
        
        rank_records.append({
            'date': nd, 'return': ind_ret, 'etf_return': etf_ret,
            'bench_return': bench_ret, 'holdings': ', '.join(selected),
        })
    
    rank_df = pd.DataFrame(rank_records).set_index('date') if rank_records else pd.DataFrame()
    
    # Step 7: 计算指标
    print("\n[7] 回测结果...")
    
    metrics = []
    # 信号策略
    m = calc_metrics(weekly_df['return'], "真实资金流信号(行业指数)")
    metrics.append(m)
    m = calc_metrics(weekly_df['etf_return'], "真实资金流信号(ETF实际)")
    metrics.append(m)
    # 排名策略
    if len(rank_df) > 0:
        m = calc_metrics(rank_df['return'], "ANB排名Top5(行业指数)")
        metrics.append(m)
        m = calc_metrics(rank_df['etf_return'], "ANB排名Top5(ETF实际)")
        metrics.append(m)
    # 基准
    bench_returns = weekly_df['bench_return']
    m = calc_metrics(bench_returns, "等权基准")
    metrics.append(m)
    
    print(f"\n{'策略':<30} {'累计收益':>10} {'年化':>10} {'夏普':>8} {'最大回撤':>10} {'周胜率':>8} {'月胜率':>8} {'Calmar':>8}")
    print("-" * 100)
    for m in metrics:
        print(f"  {m['name']:<28} {m['total_return']:>+9.2f}% {m['annualized_return']:>+9.2f}% {m['sharpe']:>8.3f} {m['max_drawdown']:>+9.2f}% {m['win_rate_weekly']:>7.1f}% {m['win_rate_monthly']:>7.1f}% {m['calmar']:>8.3f}")
    
    # 信号准确率
    print(f"\n[8] 信号准确率...")
    sig_df = pd.DataFrame(signal_records)
    if len(sig_df) > 0:
        opp_sigs = sig_df[sig_df['signal'].str.contains('机会')]
        risk_sigs = sig_df[sig_df['signal'].str.contains('风险')]
        
        opp_correct = 0
        opp_total = 0
        for _, row in opp_sigs.iterrows():
            sig_date = row['date']
            ind = row['industry']
            future_dates = [d for d in ret_panel.index if d > sig_date][:5]
            if future_dates and ind in ret_panel.columns:
                future_ret = ret_panel.loc[future_dates, ind].sum()
                if future_ret > 0:
                    opp_correct += 1
                opp_total += 1
        
        risk_correct = 0
        risk_total = 0
        for _, row in risk_sigs.iterrows():
            sig_date = row['date']
            ind = row['industry']
            future_dates = [d for d in ret_panel.index if d > sig_date][:5]
            if future_dates and ind in ret_panel.columns:
                future_ret = ret_panel.loc[future_dates, ind].sum()
                if future_ret < 0:
                    risk_correct += 1
                risk_total += 1
        
        print(f"  机会信号: {opp_total}次, 准确率={opp_correct/opp_total*100:.1f}%" if opp_total > 0 else "  机会信号: 0次")
        print(f"  风险信号: {risk_total}次, 准确率={risk_correct/risk_total*100:.1f}%" if risk_total > 0 else "  风险信号: 0次")
    
    # 持仓历史
    print(f"\n{'='*70}")
    print("持仓历史 (信号策略)")
    print(f"{'='*70}")
    for _, row in weekly_df.iterrows():
        date_str = row.name.strftime('%Y-%m-%d')
        print(f"  {date_str} [{row['n_holdings']}只] {row['holdings']}  ETF收益={row['etf_return']*100:+.2f}%")
    
    # 信号记录
    print(f"\n{'='*70}")
    print("信号记录")
    print(f"{'='*70}")
    for s in signal_records:
        date_str = s['date'].strftime('%Y-%m-%d')
        ind = s['industry']
        etf_name = INDUSTRY_ETF_MAP.get(ind, ('',''))[1]
        nr = s.get('net_ratio', 0)
        anb_val = s.get('anb', 0)
        sig = s['signal']
        print(f"  {date_str} {sig:12s} {ind}({etf_name})  净流入占比={nr:.4f} ANB={anb_val:.4f}")
    
    # Step 9: 保存
    print(f"\n[9] 保存结果...")
    
    nav_data = {}
    nav_data['信号策略(行业)'] = (1 + weekly_df['return']).cumprod()
    nav_data['信号策略(ETF)'] = (1 + weekly_df['etf_return']).cumprod()
    if len(rank_df) > 0:
        nav_data['ANB排名(行业)'] = (1 + rank_df['return']).cumprod()
        nav_data['ANB排名(ETF)'] = (1 + rank_df['etf_return']).cumprod()
    nav_data['等权基准'] = (1 + weekly_df['bench_return']).cumprod()
    nav_df = pd.DataFrame(nav_data)
    nav_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v4_nav.csv"))
    
    weekly_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v4_weekly_returns.csv"))
    
    sig_save = []
    for s in signal_records:
        s_copy = {k: (v.strftime('%Y-%m-%d') if isinstance(v, pd.Timestamp) else v) for k, v in s.items()}
        s_copy['etf_name'] = INDUSTRY_ETF_MAP.get(s['industry'], ('',''))[1]
        sig_save.append(s_copy)
    with open(os.path.join(OUTPUT_DIR, "backtest_v4_signals.json"), 'w', encoding='utf-8') as f:
        json.dump(sig_save, f, ensure_ascii=False, indent=2)
    
    metrics_save = []
    for m in metrics:
        m_save = {}
        for k, v in m.items():
            if k == 'name':
                m_save[k] = v
            elif k == 'n_periods':
                m_save[k] = v
            else:
                m_save[k] = f"{v:.2f}" if isinstance(v, float) else str(v)
                if k in ['total_return','annualized_return','annualized_vol','max_drawdown','win_rate_weekly','win_rate_monthly']:
                    m_save[k] = m_save[k] + "%"
                elif k in ['sharpe','calmar']:
                    pass
        metrics_save.append(m_save)
    with open(os.path.join(OUTPUT_DIR, "backtest_v4_metrics.json"), 'w', encoding='utf-8') as f:
        json.dump(metrics_save, f, ensure_ascii=False, indent=2)
    
    print(f"  结果已保存")
    return nav_df, metrics, signal_records, weekly_df


if __name__ == "__main__":
    results = main()
