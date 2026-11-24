#!/usr/bin/env python3
"""
TDA流形旋转策略 - 回测引擎 (股债50/50组合)
==========================================
用过去1年数据模拟策略每日运行:
1. 股票端: 8只ETF 60日价格 -> TDA分析 -> 防御权重 -> 加权收益*仓位
2. 债券端: 国债期货T0 60日价格 -> 宏观状态+TDA震荡 -> 多空方向 -> 方向*收益
3. 组合: combined_return = 0.5 * stock_return + 0.5 * bond_return
4. 累计收益、最大回撤、年化收益、基准对比、年度重置

信号时序: T日收盘生成信号 -> T+1日按信号持仓 -> T+1日收益用T日信号计算
"""

import os
import sys
import json
import time
import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import (
    FACTOR_ETF_MAP, fetch_all_etf_prices, fetch_benchmark_prices,
    get_trade_calendar, format_date_display
)
from tda_core import (
    run_tda_analysis_safe, compute_factor_sharpe, compute_defensive_weights
)
from bond_module import (
    fetch_bond_futures, fetch_bond_yield, fetch_credit_spread, fetch_dr007,
    judge_macro_state, compute_bond_tda_score, decide_bond_direction,
    compute_atr_stop
)

# ========== 股债组合回测引擎 ==========

class TDABacktest:
    """TDA流形旋转策略回测引擎 (股债50/50组合)"""
    
    def __init__(self, price_df, benchmark_series, bond_prices, window=60,
                 stock_weight=0.5, bond_weight=0.5):
        """
        参数:
            price_df: DataFrame, 8因子ETF收盘价
            benchmark_series: Series, 沪深300收盘价
            bond_prices: Series, 国债期货T0主力合约收盘价
            window: TDA分析窗口
            stock_weight: 股票端权重
            bond_weight: 债券端权重
        """
        self.price_df = price_df.copy()
        self.benchmark = benchmark_series.copy()
        self.bond_prices = bond_prices.copy()
        self.window = window
        self.stock_weight = stock_weight
        self.bond_weight = bond_weight
        
        # 股票端日收益率
        # 注意: 不能整行 dropna, 否则任一因子整列缺失会清空所有因子的收益行;
        # 缺失在收益求和处按因子单独跳过
        self.returns_df = self.price_df.pct_change()
        self.bench_returns = self.benchmark.pct_change().dropna()
        # 债券端日收益率
        self.bond_returns = self.bond_prices.pct_change().dropna()
        
        # 回测结果
        self.results = []
        self.signals = []
    
    def run(self, start_date=None, end_date=None, verbose=True):
        """运行回测"""
        all_dates = self.price_df.index.tolist()
        
        if start_date is None:
            start_idx = self.window
        else:
            start_idx = max(self.window, all_dates.index(start_date) if start_date in all_dates else self.window)
        
        if end_date is None:
            end_idx = len(all_dates) - 1
        else:
            end_idx = all_dates.index(end_date) if end_date in all_dates else len(all_dates) - 1
        
        backtest_dates = all_dates[start_idx:end_idx+1]
        
        if verbose:
            print(f"回测区间: {backtest_dates[0]} ~ {backtest_dates[-1]}, 共{len(backtest_dates)}个交易日")
            print(f"窗口: {self.window}日, 股票端{self.stock_weight*100:.0f}% + 债券端{self.bond_weight*100:.0f}%")
        
        # 预取债券端宏观数据
        if verbose:
            print(f"\n预取债券端宏观数据...")
        dr007_df = fetch_dr007(backtest_dates[0], backtest_dates[-1])
        credit_df = fetch_credit_spread(backtest_dates[0], backtest_dates[-1])
        yield_df = fetch_bond_yield(backtest_dates[0], backtest_dates[-1])
        if verbose:
            print(f"  DR007: {len(dr007_df)}条, 信用利差: {len(credit_df)}条, 国债收益率: {len(yield_df)}条")
        
        prev_stock_signal = None
        prev_bond_direction = 0
        cumulative_value = 1.0
        peak_value = 1.0
        max_drawdown = 0.0
        
        # 年度重置
        year_start_value = 1.0
        current_year = backtest_dates[0][:4]
        
        for i, trade_date in enumerate(backtest_dates):
            current_idx = all_dates.index(trade_date)
            window_start = max(0, current_idx - self.window + 1)
            price_window = self.price_df.iloc[window_start:current_idx+1]
            
            if len(price_window) < self.window:
                continue
            
            # ===== 股票端分析 =====
            tda_result = run_tda_analysis_safe(price_window)
            sharpe = compute_factor_sharpe(price_window, window=self.window)
            weights = compute_defensive_weights(sharpe)
            market = tda_result['market']
            position_ratio = market['position_ratio']
            
            # ===== 债券端分析 (简化版,每5天重新计算) =====
            bond_window = self.bond_prices[self.bond_prices.index <= trade_date].tail(self.window)
            if len(bond_window) >= 20:
                if i % 5 == 0 or prev_bond_direction == 0:
                    # 重新计算宏观状态和TDA
                    macro = judge_macro_state(dr007_df, credit_df, lookback=20)
                    tda_score = compute_bond_tda_score(bond_window, window=self.window)
                    bond_signal = decide_bond_direction(macro, tda_score, bond_prices=bond_window)
                    current_bond_direction = bond_signal['direction']
                    bond_dir_str = bond_signal['direction_str']
                    bond_tda_score = tda_score
                    macro_state = macro['macro_state']
                    bond_implication = macro['bond_implication']
                else:
                    current_bond_direction = prev_bond_direction
                    bond_dir_str = '做多' if current_bond_direction > 0 else ('做空' if current_bond_direction < 0 else '观望')
                    bond_tda_score = 0.5
                    macro_state = 'N/A'
                    bond_implication = 'N/A'
            else:
                current_bond_direction = 0
                bond_dir_str = '观望'
                bond_tda_score = 0.5
                macro_state = 'N/A'
                bond_implication = 'N/A'
            
            # ===== 计算T日收益(用T-1日信号) =====
            if prev_stock_signal is None:
                stock_return = 0.0
                bond_return = 0.0
            else:
                # 股票端收益 (对缺失数据稳健: 当日无行情的因子按 0 收益计入,
                # 不能用 0*NaN 的写法, 否则单个因子缺失会把整组收益污染成 NaN)
                prev_weights = prev_stock_signal['weights']
                prev_position = prev_stock_signal['position_ratio']
                if trade_date in self.returns_df.index:
                    day_returns = self.returns_df.loc[trade_date]
                else:
                    day_returns = pd.Series(0.0, index=self.price_df.columns)
                contrib = 0.0
                for f in self.price_df.columns:
                    w = prev_weights.get(f, 0)
                    if not w:
                        continue
                    r = day_returns.get(f, 0)
                    if pd.isna(r):
                        continue
                    contrib += w * float(r)
                stock_return = prev_position * contrib
                
                # 债券端收益
                if prev_bond_direction != 0 and trade_date in self.bond_returns.index:
                    bond_return = prev_bond_direction * self.bond_returns.loc[trade_date]
                else:
                    bond_return = 0.0
            
            # 组合收益
            combined_return = self.stock_weight * stock_return + self.bond_weight * bond_return
            
            # 更新净值
            cumulative_value *= (1 + combined_return)
            if cumulative_value > peak_value:
                peak_value = cumulative_value
            drawdown = (peak_value - cumulative_value) / peak_value
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            
            # 年度重置
            if trade_date[:4] != current_year:
                if verbose:
                    year_ret = (cumulative_value / year_start_value - 1) * 100
                    print(f"\n  === 年度重置: {current_year} -> {trade_date[:4]} (去年收益={year_ret:.2f}%) ===")
                current_year = trade_date[:4]
                year_start_value = cumulative_value
                peak_value = cumulative_value
                max_drawdown = 0.0
            
            # 基准收益
            bench_ret = self.bench_returns.get(trade_date, 0.0)
            
            # 年度累计
            year_cumulative = (cumulative_value / year_start_value - 1) * 100
            
            # 记录结果
            result = {
                'trade_date': trade_date,
                'market_state': market['state'],
                'vulnerability': market['vulnerability'],
                'position_pct': market['position_pct'],
                'stock_return': round(stock_return * 100, 4),
                'bond_return': round(bond_return * 100, 4),
                'combined_return': round(combined_return * 100, 4),
                'cumulative_return': round((cumulative_value - 1) * 100, 2),
                'year_cumulative': round(year_cumulative, 2),
                'cumulative_value': round(cumulative_value, 6),
                'drawdown': round(drawdown * 100, 2),
                'max_drawdown': round(max_drawdown * 100, 2),
                'benchmark_return': round(bench_ret * 100, 4),
                'excess_return': round((combined_return - bench_ret) * 100, 4),
                'bond_direction': bond_dir_str,
                'bond_tda_score': bond_tda_score,
                'macro_state': macro_state,
                'top_factor': list(weights.keys())[0] if weights else 'N/A',
                'top_weight': list(weights.values())[0] if weights else 0,
                'beta_0': tda_result['features']['beta_0'],
                'beta_1': tda_result['features']['beta_1'],
                'curvature': round(tda_result['features']['curvature'], 4),
            }
            self.results.append(result)
            
            # 记录信号
            signal = {
                'trade_date': trade_date,
                'weights': weights,
                'position_ratio': position_ratio,
                'market_state': market['state'],
                'bond_direction': current_bond_direction,
                'sharpe': sharpe,
            }
            self.signals.append(signal)
            prev_stock_signal = signal
            prev_bond_direction = current_bond_direction
            
            if verbose and (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(backtest_dates)}] {trade_date}: "
                      f"组合累计={result['cumulative_return']:.2f}%, "
                      f"年度={result['year_cumulative']:.2f}%, "
                      f"回撤={result['max_drawdown']:.2f}%, "
                      f"债券={bond_dir_str}")
        
        if verbose:
            print(f"\n回测完成: {len(self.results)}个交易日")
        
        return self.results
    
    def get_summary(self):
        """获取回测摘要统计"""
        if not self.results:
            return {}
        
        combined_returns = [r['combined_return'] / 100 for r in self.results]
        stock_returns = [r['stock_return'] / 100 for r in self.results]
        bond_returns = [r['bond_return'] / 100 for r in self.results]
        cum_value = self.results[-1]['cumulative_value']
        
        # 基准统计
        bench_returns = [r['benchmark_return'] / 100 for r in self.results]
        bench_cum = np.prod([1 + r for r in bench_returns]) - 1
        
        # 策略统计
        total_return = cum_value - 1
        n_days = len(self.results)
        annualized = (1 + total_return) ** (252 / n_days) - 1 if n_days > 0 else 0
        
        # 夏普比
        rf_daily = 0.02 / 252
        excess_returns = [r - rf_daily for r in combined_returns]
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252) if np.std(excess_returns) > 0 else 0
        
        # 胜率
        win_days = sum(1 for r in combined_returns if r > 0)
        loss_days = sum(1 for r in combined_returns if r < 0)
        win_rate = win_days / n_days if n_days > 0 else 0
        
        # 最大回撤
        max_dd = max(r['max_drawdown'] for r in self.results)
        
        # 股债分项统计
        stock_total = np.prod([1 + r for r in stock_returns]) - 1
        bond_total = np.prod([1 + r for r in bond_returns]) - 1
        
        # 状态分布
        state_dist = {}
        for r in self.results:
            state = r['market_state']
            state_dist[state] = state_dist.get(state, 0) + 1
        
        # 债券方向分布
        bond_dir_dist = {}
        for r in self.results:
            d = r['bond_direction']
            bond_dir_dist[d] = bond_dir_dist.get(d, 0) + 1
        
        # 平均仓位
        avg_position = np.mean([r['position_pct'] for r in self.results])
        
        summary = {
            'backtest_period': f"{self.results[0]['trade_date']} ~ {self.results[-1]['trade_date']}",
            'n_trading_days': n_days,
            'total_return': round(total_return * 100, 2),
            'annualized_return': round(annualized * 100, 2),
            'max_drawdown': round(max_dd, 2),
            'sharpe_ratio': round(sharpe, 3),
            'win_rate': round(win_rate * 100, 1),
            'win_days': win_days,
            'loss_days': loss_days,
            'avg_daily_return': round(np.mean(combined_returns) * 100, 4),
            'std_daily_return': round(np.std(combined_returns) * 100, 4),
            'avg_position': round(avg_position, 1),
            'stock_total_return': round(stock_total * 100, 2),
            'bond_total_return': round(bond_total * 100, 2),
            'benchmark_total_return': round(bench_cum * 100, 2),
            'excess_return': round((total_return - bench_cum) * 100, 2),
            'state_distribution': state_dist,
            'bond_direction_distribution': bond_dir_dist,
            'final_cumulative_value': round(cum_value, 4),
        }
        
        return summary
    
    def get_results_df(self):
        return pd.DataFrame(self.results)
    
    def save_results(self, filepath):
        summary = self.get_summary()
        data = {
            'summary': summary,
            'daily_results': self.results,
            'signals': [{k: v for k, v in s.items() if k != 'sharpe'} for s in self.signals],
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"回测结果已保存: {filepath}")


# ========== 运行回测 ==========

def run_backtest(start_date='20250819', end_date='20260818', window=60, verbose=True):
    """运行完整股债组合回测"""
    print("=" * 60)
    print("TDA流形旋转策略 - 股债50/50组合历史回测")
    print("=" * 60)
    
    # 1. 获取ETF数据
    print("\n[1/4] 获取ETF价格数据...")
    cal = get_trade_calendar()
    start_idx = cal.index(start_date) if start_date in cal else 0
    data_start_idx = max(0, start_idx - window)
    data_start = cal[data_start_idx]
    
    price_df = fetch_all_etf_prices(data_start, end_date)
    print(f"  价格矩阵: {price_df.shape}")
    
    # 2. 获取基准数据
    print("\n[2/4] 获取基准数据...")
    benchmark = fetch_benchmark_prices(data_start, end_date)
    print(f"  基准数据: {len(benchmark)}条")
    
    # 3. 获取国债期货数据
    print("\n[3/4] 获取国债期货数据...")
    bond_df = fetch_bond_futures('T0', data_start, end_date)
    if bond_df.empty:
        print("  国债期货数据获取失败, 回测将仅含股票端")
        bond_prices = pd.Series(dtype=float)
    else:
        bond_prices = bond_df.set_index('trade_date')['收盘价'].astype(float)
        print(f"  国债期货T0: {len(bond_prices)}条")
    
    # 4. 运行回测
    print(f"\n[4/4] 运行股债组合回测 (窗口={window}日)...")
    bt = TDABacktest(price_df, benchmark, bond_prices, window=window)
    bt.run(verbose=verbose)
    
    # 输出结果
    summary = bt.get_summary()
    
    print("\n" + "=" * 60)
    print("股债组合回测结果摘要")
    print("=" * 60)
    print(f"  回测区间:       {summary['backtest_period']}")
    print(f"  交易日数:       {summary['n_trading_days']}")
    print(f"  组合总收益:     {summary['total_return']:.2f}%")
    print(f"  年化收益:       {summary['annualized_return']:.2f}%")
    print(f"  最大回撤:       {summary['max_drawdown']:.2f}%")
    print(f"  夏普比:         {summary['sharpe_ratio']:.3f}")
    print(f"  胜率:           {summary['win_rate']:.1f}% ({summary['win_days']}胜/{summary['loss_days']}负)")
    print(f"  日均收益:       {summary['avg_daily_return']:.4f}%")
    print(f"  日波动率:       {summary['std_daily_return']:.4f}%")
    print(f"  平均仓位:       {summary['avg_position']:.1f}%")
    print(f"  股票端收益:     {summary['stock_total_return']:.2f}%")
    print(f"  债券端收益:     {summary['bond_total_return']:.2f}%")
    print(f"  沪深300基准:    {summary['benchmark_total_return']:.2f}%")
    print(f"  超额收益:       {summary['excess_return']:.2f}%")
    print(f"\n  市场状态分布:")
    for state, count in summary['state_distribution'].items():
        pct = count / summary['n_trading_days'] * 100
        print(f"    {state}: {count}天 ({pct:.1f}%)")
    print(f"\n  债券方向分布:")
    for d, count in summary['bond_direction_distribution'].items():
        pct = count / summary['n_trading_days'] * 100
        print(f"    {d}: {count}天 ({pct:.1f}%)")
    
    # 保存结果
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state')
    os.makedirs(results_dir, exist_ok=True)
    bt.save_results(os.path.join(results_dir, 'backtest_results.json'))
    
    return bt, summary


if __name__ == '__main__':
    bt, summary = run_backtest()
