#!/usr/bin/env python3
"""
TDA流形旋转策略 - 22因子回测引擎 v2 (集中持仓+债券修复)
=========================================================
关键改进(vs v1):
1. 股票端: 22因子做TDA拓扑分析(信号更丰富), 但实际持仓集中在Top5因子(避免过度分散)
2. 债券端: 修复方向判断 -- 以国债期货价格趋势(MA20 vs MA60)为主信号, 宏观状态仅做确认
3. 组合: 80%股票 + 20%债券 (v4改进: 提高股票端权重冲击10%+)
4. 债券端: MA10/MA30短周期趋势 (v4改进: 减少降息周期做空天数)

回测流程:
1. 股票端: 22个申万行业指数 60日价格 -> TDA分析 -> 流形旋转权重(偏好×(s+1)²) -> Top5集中持仓 -> 加权收益*仓位
2. 债券端: 国债期货T0 MA10/MA30趋势为主 + 宏观确认 + TDA震荡过滤 -> 多空方向 -> 方向*收益
3. 组合: combined_return = 0.8 * stock_return + 0.2 * bond_return
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

# 确保pip-global包路径
_pip_global = '/tmp/.pip-global/lib/python3.12/site-packages'
if os.path.exists(_pip_global) and _pip_global not in sys.path:
    sys.path.insert(0, _pip_global)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher_22f import (
    FACTOR_22_MAP, FACTOR_NAMES, N_FACTORS,
    fetch_all_sw_prices, fetch_benchmark_prices,
    get_trade_calendar, format_date_display
)
from tda_core import (
    run_tda_analysis_safe, compute_factor_sharpe, compute_defensive_weights,
    compute_rotation_weights, compute_rotation_weights_v2,
    classify_market_state_v2, PREFERENCE_MATRIX
)
from bond_module import (
    fetch_bond_futures, fetch_bond_yield, fetch_credit_spread, fetch_dr007,
    judge_macro_state, compute_bond_tda_score, compute_atr_stop
)

# ========== 改进1: 流形旋转 + 集中持仓 ==========

def compute_concentrated_weights(sharpe_dict, market_state='oscillation', top_n=5, 
                                 momentum_discount=0.5, price_window=None, alpha=0.4):
    """
    流形旋转权重v2(动量+投影) + Top N集中持仓
    
    流程:
    1. 用偏好系数×[(1-α)×(s+1)² + α×投影权重]计算全部22因子的旋转权重
    2. 只取权重最高的Top N个因子
    3. 在Top N内部重新归一化分配权重
    
    参数:
        sharpe_dict: 全部22因子的夏普比
        market_state: 5种市场状态之一(决定偏好系数)
        top_n: 实际持仓的因子数量
        momentum_discount: 正夏普(动量)因子的折扣系数
        price_window: 价格窗口(用于计算因子投影得分)
        alpha: 投影权重占比(0-1), 默认0.4即动量60%+投影40%
    """
    # Step 1: 用流形旋转权重v2(偏好×(动量+投影))
    all_weights = compute_rotation_weights_v2(
        sharpe_dict, market_state, price_window=price_window,
        momentum_discount=momentum_discount, alpha=alpha
    )
    
    # Step 2: 取Top N
    sorted_factors = sorted(all_weights.items(), key=lambda x: x[1], reverse=True)
    top_factors = sorted_factors[:top_n]
    
    # Step 3: 在Top N内部重新归一化
    total = sum(w for _, w in top_factors)
    if total == 0:
        weights = {f: 1.0 / top_n for f, _ in top_factors}
    else:
        weights = {f: w / total for f, w in top_factors}
    
    # 按权重降序排列
    weights = dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))
    weights = {k: round(v, 4) for k, v in weights.items()}
    
    return weights

# ========== 改进2: 债券方向判断(趋势为主) ==========

def decide_bond_direction_v2(bond_prices, macro_state=None, tda_score=0.5, 
                              ma_short=20, ma_long=60):
    """
    修复版债券方向判断 -- 以价格趋势为主信号
    
    逻辑:
    1. 主信号: MA_short vs MA_long (趋势跟踪)
       - MA_short > MA_long -> 做多(利率下行=国债上涨)
       - MA_short < MA_long -> 做空(利率上行=国债下跌)
    2. 宏观确认: 如果宏观方向与趋势一致, 增加置信度
    3. TDA震荡过滤: 高震荡时降低仓位但不反转方向
    
    返回:
        direction: +1(做多), -1(做空), 0(观望)
    """
    if bond_prices is None or len(bond_prices) < ma_long:
        return {'direction': 0, 'direction_str': '观望', 'confidence': 0.3, 
                'tda_score': tda_score, 'reason': '数据不足'}
    
    # 主信号: 趋势跟踪
    ma_s = bond_prices.tail(ma_short).mean()
    ma_l = bond_prices.tail(ma_long).mean()
    
    if ma_s > ma_l:
        trend_direction = 1  # 上行趋势→做多
        trend_str = '上行趋势'
    elif ma_s < ma_l:
        trend_direction = -1  # 下行趋势→做空
        trend_str = '下行趋势'
    else:
        trend_direction = 0
        trend_str = '无趋势'
    
    # 宏观方向约束: 宽货币周期(偏多/中性偏多)禁止做空, 紧货币周期(偏空/中性偏空)禁止做多
    # 原因: 降息周期债券价格长期上行, MA短期穿头是假信号, 做空大概率亏钱
    macro_bias = 0  # 0=无约束
    if macro_state:
        bond_impl = macro_state.get('bond_implication', '中性')
        if '多' in bond_impl:
            macro_bias = 1   # 宏观偏多
        elif '空' in bond_impl:
            macro_bias = -1  # 宏观偏空
    
    if macro_bias > 0 and trend_direction < 0:
        # 宽货币周期, MA说做空 → 不做空, 改为观望
        direction = 0
        trend_str = f'{trend_str}(宏观偏多, 禁止做空→观望)'
    elif macro_bias < 0 and trend_direction > 0:
        # 紧货币周期, MA说做多 → 不做多, 改为观望
        direction = 0
        trend_str = f'{trend_str}(宏观偏空, 禁止做多→观望)'
    else:
        direction = trend_direction
    
    # 宏观确认: 方向与宏观一致时增置信度
    confidence = 0.5
    if macro_state:
        bond_impl = macro_state.get('bond_implication', '中性')
        if direction > 0 and '多' in bond_impl:
            confidence = 0.8
        elif direction < 0 and '空' in bond_impl:
            confidence = 0.8
        elif direction == 0:
            confidence = 0.4
    
    # TDA震荡过滤: 高震荡时降低置信度但不反转
    if tda_score > 0.75:
        confidence *= 0.5
    
    return {
        'direction': direction,
        'direction_str': '做多' if direction > 0 else ('做空' if direction < 0 else '观望'),
        'confidence': round(confidence, 3),
        'tda_score': tda_score,
        'trend': trend_str,
        'ma_short': round(ma_s, 3),
        'ma_long': round(ma_l, 3),
    }

# ========== 22因子股债组合回测引擎 ==========

class TDA22FBacktest:
    """TDA流形旋转策略回测引擎 (22因子股债80/20组合)"""
    
    def __init__(self, price_df, benchmark_series, bond_prices, window=60,
                 stock_weight=0.8, bond_weight=0.2):
        self.price_df = price_df.copy()
        self.benchmark = benchmark_series.copy()
        self.bond_prices = bond_prices.copy()
        self.window = window
        self.stock_weight = stock_weight
        self.bond_weight = bond_weight
        
        # 注意: 不能整行 dropna, 否则任一因子整列缺失会清空所有因子的收益行;
        # 缺失在收益求和处按因子单独跳过
        self.returns_df = self.price_df.pct_change()
        self.bench_returns = self.benchmark.pct_change().dropna()
        self.bond_returns = self.bond_prices.pct_change().dropna()
        
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
            print(f"窗口: {self.window}日, 因子数: {self.price_df.shape[1]}, 股票端{self.stock_weight*100:.0f}% + 债券端{self.bond_weight*100:.0f}%")
        
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
        
        year_start_value = 1.0
        current_year = backtest_dates[0][:4]
        
        for i, trade_date in enumerate(backtest_dates):
            current_idx = all_dates.index(trade_date)
            window_start = max(0, current_idx - self.window + 1)
            price_window = self.price_df.iloc[window_start:current_idx+1]
            
            if len(price_window) < self.window:
                continue
            
            # ===== 股票端TDA分析 =====
            tda_result = run_tda_analysis_safe(price_window)
            sharpe = compute_factor_sharpe(price_window, window=self.window)
            
            # v3: 流形旋转 -- 用曲率+β₁判定5种市场状态, 再用偏好系数×(s+1)²
            rotation = classify_market_state_v2(tda_result['features'], price_window)
            market_state_v2 = rotation['state']  # bull_integrated / bull_fragmented / etc.
            
            # 集中持仓: 22因子旋转权重v2(动量+投影) -> Top5
            weights = compute_concentrated_weights(
                sharpe, market_state=market_state_v2, top_n=5,
                price_window=price_window, alpha=0.4
            )
            
            market = tda_result['market']
            # v3: 仓位也根据市场状态调整 -- 牛市更高, 熊市更低
            if 'bull' in market_state_v2:
                position_ratio = max(0.50, 1.0 - market['vulnerability'] / 100.0 * 0.5)
            elif 'bear' in market_state_v2:
                position_ratio = max(0.15, 1.0 - market['vulnerability'] / 100.0 * 0.9)
            else:
                position_ratio = max(0.30, 1.0 - market['vulnerability'] / 100.0 * 0.7)
            market['position_ratio'] = round(position_ratio, 4)
            market['position_pct'] = round(position_ratio * 100, 1)
            market['rotation_state'] = rotation['state_cn']
            market['rotation'] = rotation
            
            # ===== 债券端分析 (每5天重新计算) =====
            bond_window = self.bond_prices[self.bond_prices.index <= trade_date].tail(self.window)
            if len(bond_window) >= 20:
                if i % 5 == 0 or prev_bond_direction == 0:
                    macro = judge_macro_state(dr007_df, credit_df, lookback=20)
                    tda_score = compute_bond_tda_score(bond_window, window=self.window)
                    # v4改进: MA10/MA30短周期让方向切换更灵敏, 减少降息周期做空
                    bond_signal = decide_bond_direction_v2(
                        bond_window, macro_state=macro, tda_score=tda_score,
                        ma_short=10, ma_long=30
                    )
                    current_bond_direction = bond_signal['direction']
                    bond_dir_str = bond_signal['direction_str']
                    bond_tda_score = tda_score
                    macro_state = macro['macro_state']
                    bond_implication = macro['bond_implication']
                    bond_trend = bond_signal.get('trend', 'N/A')
                else:
                    current_bond_direction = prev_bond_direction
                    bond_dir_str = '做多' if current_bond_direction > 0 else ('做空' if current_bond_direction < 0 else '观望')
                    bond_tda_score = 0.5
                    macro_state = 'N/A'
                    bond_implication = 'N/A'
                    bond_trend = 'N/A'
            else:
                current_bond_direction = 0
                bond_dir_str = '观望'
                bond_tda_score = 0.5
                macro_state = 'N/A'
                bond_implication = 'N/A'
                bond_trend = 'N/A'
            
            # ===== 计算T日收益(用T-1日信号) =====
            if prev_stock_signal is None:
                stock_return = 0.0
                bond_return = 0.0
            else:
                prev_weights = prev_stock_signal['weights']
                prev_position = prev_stock_signal['position_ratio']
                if trade_date in self.returns_df.index:
                    day_returns = self.returns_df.loc[trade_date]
                else:
                    day_returns = pd.Series(0.0, index=self.price_df.columns)
                # v2: 只用Top5因子的权重计算收益(其他因子权重=0)
                # 对缺失数据稳健: 当日无行情的因子按 0 收益计入, 避免 0*NaN 污染整组收益
                contrib = 0.0
                for f in prev_weights:
                    w = prev_weights.get(f, 0)
                    if not w:
                        continue
                    r = day_returns.get(f, 0)
                    if pd.isna(r):
                        continue
                    contrib += w * float(r)
                stock_return = prev_position * contrib
                
                if prev_bond_direction != 0 and trade_date in self.bond_returns.index:
                    bond_return = prev_bond_direction * self.bond_returns.loc[trade_date]
                else:
                    bond_return = 0.0
            
            combined_return = self.stock_weight * stock_return + self.bond_weight * bond_return
            
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
            
            bench_ret = self.bench_returns.get(trade_date, 0.0)
            year_cumulative = (cumulative_value / year_start_value - 1) * 100
            
            result = {
                'trade_date': trade_date,
                'market_state': market['state'],
                'rotation_state': rotation['state'],
                'rotation_cn': rotation['state_cn'],
                'signed_curvature': rotation['signed_curvature'],
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
                'bond_trend': bond_trend,
                'bond_tda_score': bond_tda_score,
                'macro_state': macro_state,
                'top_factor': list(weights.keys())[0] if weights else 'N/A',
                'top_weight': list(weights.values())[0] if weights else 0,
                'beta_0': tda_result['features']['beta_0'],
                'beta_1': tda_result['features']['beta_1'],
                'curvature': round(tda_result['features']['curvature'], 4),
                'n_factors': self.price_df.shape[1],
            }
            self.results.append(result)
            
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
                      f"状态={market['state']}, "
                      f"Top={result['top_factor']}({result['top_weight']*100:.1f}%)")
        
        if verbose:
            print(f"\n回测完成: {len(self.results)}个交易日")
        
        return self.results
    
    def get_summary(self):
        if not self.results:
            return {}
        
        combined_returns = [r['combined_return'] / 100 for r in self.results]
        stock_returns = [r['stock_return'] / 100 for r in self.results]
        bond_returns = [r['bond_return'] / 100 for r in self.results]
        cum_value = self.results[-1]['cumulative_value']
        
        bench_returns = [r['benchmark_return'] / 100 for r in self.results]
        bench_cum = np.prod([1 + r for r in bench_returns]) - 1
        
        total_return = cum_value - 1
        n_days = len(self.results)
        annualized = (1 + total_return) ** (252 / n_days) - 1 if n_days > 0 else 0
        
        rf_daily = 0.02 / 252
        excess_returns = [r - rf_daily for r in combined_returns]
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252) if np.std(excess_returns) > 0 else 0
        
        win_days = sum(1 for r in combined_returns if r > 0)
        loss_days = sum(1 for r in combined_returns if r < 0)
        win_rate = win_days / n_days if n_days > 0 else 0
        
        max_dd = max(r['max_drawdown'] for r in self.results)
        
        stock_total = np.prod([1 + r for r in stock_returns]) - 1
        bond_total = np.prod([1 + r for r in bond_returns]) - 1
        
        state_dist = {}
        for r in self.results:
            state = r['market_state']
            state_dist[state] = state_dist.get(state, 0) + 1
        
        # 流形旋转状态分布
        rotation_dist = {}
        for r in self.results:
            rs = r.get('rotation_state', 'oscillation')
            rotation_dist[rs] = rotation_dist.get(rs, 0) + 1
        
        bond_dir_dist = {}
        for r in self.results:
            d = r['bond_direction']
            bond_dir_dist[d] = bond_dir_dist.get(d, 0) + 1
        
        avg_position = np.mean([r['position_pct'] for r in self.results])
        
        # Top因子统计
        top_factor_count = {}
        for r in self.results:
            f = r['top_factor']
            top_factor_count[f] = top_factor_count.get(f, 0) + 1
        
        summary = {
            'backtest_period': f"{self.results[0]['trade_date']} ~ {self.results[-1]['trade_date']}",
            'n_trading_days': n_days,
            'n_factors': self.results[0].get('n_factors', 22),
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
            'rotation_distribution': rotation_dist,
            'bond_direction_distribution': bond_dir_dist,
            'top_factor_distribution': top_factor_count,
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

def run_backtest_22f(start_date='20250819', end_date='20260818', window=60, verbose=True):
    """运行22因子股债组合回测"""
    print("=" * 60)
    print("TDA流形旋转策略 - 22因子股债50/50组合历史回测")
    print("=" * 60)
    
    # 1. 获取22个申万行业指数数据
    print(f"\n[1/4] 获取{N_FACTORS}个申万行业指数数据...")
    cal = get_trade_calendar()
    start_idx = cal.index(start_date) if start_date in cal else 0
    data_start_idx = max(0, start_idx - window)
    data_start = cal[data_start_idx]
    
    price_df = fetch_all_sw_prices(data_start, end_date)
    print(f"  价格矩阵: {price_df.shape}")
    
    # 2. 获取基准数据
    print("\n[2/4] 获取沪深300基准数据...")
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
    print(f"\n[4/4] 运行22因子股债组合回测 (窗口={window}日)...")
    bt = TDA22FBacktest(price_df, benchmark, bond_prices, window=window)
    bt.run(verbose=verbose)
    
    summary = bt.get_summary()
    
    print("\n" + "=" * 60)
    print("22因子股债组合回测结果摘要")
    print("=" * 60)
    print(f"  回测区间:       {summary['backtest_period']}")
    print(f"  交易日数:       {summary['n_trading_days']}")
    print(f"  因子数量:       {summary['n_factors']}")
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
    
    print(f"\n  Top因子分布(出现次数):")
    for f, count in sorted(summary['top_factor_distribution'].items(), key=lambda x: x[1], reverse=True):
        pct = count / summary['n_trading_days'] * 100
        print(f"    {f}: {count}次 ({pct:.1f}%)")
    
    # 保存结果
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state')
    os.makedirs(results_dir, exist_ok=True)
    bt.save_results(os.path.join(results_dir, 'backtest_22f_results.json'))
    
    return bt, summary


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TDA 22因子回测')
    parser.add_argument('--start', type=str, default='20250819', help='回测开始日期')
    parser.add_argument('--end', type=str, default='20260818', help='回测结束日期')
    parser.add_argument('--window', type=int, default=60, help='TDA分析窗口')
    args = parser.parse_args()
    
    run_backtest_22f(start_date=args.start, end_date=args.end, window=args.window)
