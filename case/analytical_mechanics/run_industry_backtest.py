#!/usr/bin/env python3
"""
分析力学量化交易系统 — 行业轮动回测
v4.0: 用申万三级行业指数替代个股，验证行业层面混沌信号是否更稳定

核心改进:
- 标的池: 21个申万三级行业指数（成份股>=30，有2019年以来完整数据）
- 信号: 复用v3.1的混沌方向分类风控
- 选行业: 用Hurst+LLE+Toroidality综合打分，选最强行业轮动
- 交易: 每周调仓，最多持有3个行业
"""

import os, sys, pickle, time
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, '.')
from hamiltonian_chaos_system import (
    FeatureEngine, StockSelector, RiskManager, BacktestEngine
)

# ============================================================
# 数据加载
# ============================================================

def load_industry_data():
    """加载行业数据和特征"""
    # 尝试加载已缓存的行业特征
    feat_path = 'data_cache/sw_industry_features.pkl'
    raw_path = 'data_cache/sw_industry_data.pkl'
    
    if os.path.exists(feat_path):
        print("从缓存加载行业特征数据...")
        with open(feat_path, 'rb') as f:
            industry_features = pickle.load(f)
        
        # 提取数据字典
        industry_data = {}
        for code, info in industry_features.items():
            industry_data[code] = info['data']
        
        # 用沪深300作为大盘参考
        with open('data_cache/features.pkl', 'rb') as f:
            market_features = pickle.load(f)
        market_df = market_features['market']
        
        return industry_data, market_df, industry_features
    else:
        raise FileNotFoundError(f"请先运行行业数据获取和特征计算")


# ============================================================
# 行业轮动回测引擎
# ============================================================

class IndustryRotationBacktest:
    """
    行业轮动回测引擎
    
    策略:
    1. 用沪深300大盘特征做风控（v3.1风控逻辑）
    2. 每个行业独立计算混沌特征
    3. 每周（5个交易日）调仓一次
    4. 选最强3个行业等权持有
    5. 风控DANGER时清仓
    """
    
    def __init__(self, initial_cash=1_000_000, commission=0.0003,
                 slippage=0.001, max_positions=3, rebalance_days=5):
        self.initial_cash = initial_cash
        self.commission = commission
        self.slippage = slippage
        self.max_positions = max_positions
        self.rebalance_days = rebalance_days
        
        self.cash = initial_cash
        self.positions = {}  # {code: {shares, entry_price, entry_date, ...}}
        self.trade_log = []
        self.nav_history = []
        
    def _calculate_industry_score(self, features, market_lle, market_tor):
        """
        计算行业得分: 综合Hurst、LLE、Toroidality
        
        逻辑:
        - 高Hurst+均线上方 = 趋势强，高分
        - 高LLE+高Hurst+低Tor = 爆拉前奏，加分
        - 高LLE+低Hurst = 顶部反转，减分
        """
        H = features.get('hurst', 0.5)
        lle = features.get('lle', 0.05)
        tor = features.get('toroidality', 0.2)
        
        # 基础趋势分
        if H > 0.55:
            base_score = (H - 0.5) * 200  # H=0.55 -> 10, H=0.75 -> 50
        elif H < 0.45:
            base_score = (0.5 - H) * 50   # 反转行业低分
        else:
            base_score = 0
        
        # 爆拉前奏加成: 高LLE+高Hurst+低Tor
        if lle > 0.065 and H > 0.55 and tor < 0.20:
            base_score += 20  # 数据验证: 未来20天+1.33%, 胜率57.5%
        
        # 顶部反转惩罚: 高LLE+低Hurst
        if lle > 0.065 and H < 0.45:
            base_score -= 30
        
        # 高Tor+高Hurst = 趋势加速，加分
        if H > 0.55 and tor > 0.22:
            base_score += 10
        
        return base_score
    
    def run(self, industry_data, market_df, risk_manager, start_date='2020-01-01', 
            end_date='2026-08-11'):
        """
        运行行业轮动回测
        
        参数:
        - industry_data: {code: df} 行业数据（含特征）
        - market_df: 大盘数据（含特征）
        - risk_manager: 风控器
        """
        print(f"\n行业轮动回测")
        print(f"  区间: {start_date} ~ {end_date}")
        print(f"  行业数: {len(industry_data)}")
        print(f"  最大持仓: {self.max_positions}个行业")
        print(f"  调仓频率: 每{self.rebalance_days}个交易日")
        
        # 准备交易日历（用大盘日期）
        market_df = market_df.copy()
        market_df['date'] = pd.to_datetime(market_df['date'])
        trading_days = market_df[
            (market_df['date'] >= start_date) & (market_df['date'] <= end_date)
        ]['date'].tolist()
        
        print(f"  交易日数: {len(trading_days)}")
        
        # 预计算大盘连续在MA20下方天数
        market_df = market_df.set_index('date')
        market_below_days = {}
        consecutive_below = 0
        for d in market_df.index:
            row = market_df.loc[d]
            mc = row.get('close', 0)
            mm = row.get('ma20', mc)
            if mm > 0 and mc < mm:
                consecutive_below += 1
            else:
                consecutive_below = 0
            market_below_days[d] = consecutive_below
        
        # 预处理行业数据：设置date为index
        ind_data = {}
        for code, df in industry_data.items():
            df = df.copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            ind_data[code] = df
        
        last_rebalance = -self.rebalance_days  # 确保第一天就调仓
        
        for i, date in enumerate(trading_days):
            if i % 100 == 0:
                print(f"  进度: {i}/{len(trading_days)} ({date.strftime('%Y-%m-%d')})")
            
            # 获取大盘特征
            if date not in market_df.index:
                continue
            
            market_row = market_df.loc[date]
            market_lle = market_row.get('lle', 0.05) if not pd.isna(market_row.get('lle', 0.05)) else 0.05
            market_tor = market_row.get('toroidality', 0.25) if not pd.isna(market_row.get('toroidality', 0.25)) else 0.25
            market_hurst = market_row.get('hurst', 0.5) if not pd.isna(market_row.get('hurst', 0.5)) else 0.5
            market_close = market_row.get('close', 0)
            market_ma20 = market_row.get('ma20', market_close)
            market_above_ma20 = market_close > market_ma20 if market_ma20 > 0 else True
            market_dev_ma20 = (market_close - market_ma20) / market_ma20 if market_ma20 > 0 else 0
            market_below_ma20_days = market_below_days.get(date, 0)
            
            # 大盘风控
            market_risk = risk_manager.check_market_risk(
                market_lle, market_tor, market_hurst,
                market_above_ma20, market_dev_ma20,
                below_ma20_days=market_below_ma20_days
            )
            
            daily_signal = {
                'date': date,
                'market_lle': market_lle,
                'market_tor': market_tor,
                'market_hurst': market_hurst,
                'market_risk': market_risk,
                'nav': 0,
                'cash_ratio': 0,
                'n_positions': 0,
                'buy_signals': [],
                'sell_signals': [],
            }
            
            # ====== DANGER: 清仓避险 ======
            if market_risk == 'DANGER':
                for code in list(self.positions.keys()):
                    if code in ind_data and date in ind_data[code].index:
                        sell_price = ind_data[code].loc[date, 'close']
                        self._execute_sell(code, sell_price, date, 'MARKET_DANGER')
                        daily_signal['sell_signals'].append({'code': code, 'reason': 'MARKET_DANGER'})
            
            # ====== 每日检查持仓和选行业（不再5天才调仓一次）======
            elif True:
                last_rebalance = i
                
                # 先检查持仓：是否需要卖出
                for code in list(self.positions.keys()):
                    if code not in ind_data or date not in ind_data[code].index:
                        continue
                    
                    hist = ind_data[code].loc[:date]
                    if len(hist) < 2:
                        continue
                    
                    current_price = hist.iloc[-1]['close']
                    current_features = {
                        'lle': hist.iloc[-1].get('lle', 0.05),
                        'hurst': hist.iloc[-1].get('hurst', 0.5),
                        'toroidality': hist.iloc[-1].get('toroidality', 0.2),
                    }
                    
                    stock_ma20 = hist.iloc[-1].get('ma20', current_price)
                    stock_above_ma20 = current_price > stock_ma20 if stock_ma20 > 0 else True
                    
                    pos = self.positions[code]
                    pnl = (current_price - pos['entry_price']) / pos['entry_price']
                    
                    # 卖出条件
                    should_sell = False
                    reason = ''
                    
                    # 1. 顶部反转
                    if (current_features['lle'] > 0.065 and 
                            current_features['hurst'] < 0.45 and 
                            stock_above_ma20):
                        should_sell = True
                        reason = 'TOP_REVERSAL'
                    
                    # 2. Hurst崩塌+均线下方
                    elif (current_features['hurst'] < 0.45 and not stock_above_ma20):
                        should_sell = True
                        reason = 'TREND_REVERSAL'
                    
                    # 3. 止损
                    elif pnl < -0.08:
                        should_sell = True
                        reason = f'STOP_LOSS({pnl:.1%})'
                    
                    # 4. 移动止盈
                    elif pnl > 0.15:
                        max_price = pos.get('max_price', pos['entry_price'])
                        if current_price > max_price:
                            pos['max_price'] = current_price
                            max_price = current_price
                        trailing = (current_price - max_price) / max_price
                        if trailing < -0.06:
                            should_sell = True
                            reason = f'TRAILING_PROFIT({pnl:.1%})'
                    
                    # 5. 持仓超时（仅在不赚钱时才退出）
                    elif (date - pos['entry_date']).days > 40 and pnl < 0.02:
                        should_sell = True
                        reason = 'TIME_EXIT'
                    
                    if should_sell:
                        self._execute_sell(code, current_price, date, reason)
                        daily_signal['sell_signals'].append({'code': code, 'reason': reason})
                
                # 选行业买入
                if len(self.positions) < self.max_positions:
                    candidates = []
                    
                    for code, df in ind_data.items():
                        if code in self.positions:
                            continue
                        if date not in df.index:
                            continue
                        
                        hist = df.loc[:date]
                        if len(hist) < 105:
                            continue
                        
                        latest = hist.iloc[-1]
                        features = {
                            'lle': latest.get('lle', 0.05),
                            'hurst': latest.get('hurst', 0.5),
                            'toroidality': latest.get('toroidality', 0.2),
                            'hamiltonian_z': latest.get('hamiltonian_z', 0),
                        }
                        
                        # 过滤: 高LLE+低Hurst = 顶部反转，不买
                        if features['lle'] > 0.065 and features['hurst'] < 0.45:
                            continue
                        
                        # 过滤: 均线下方+低Hurst = 弱势
                        ma20 = latest.get('ma20', latest['close'])
                        if ma20 > 0 and latest['close'] < ma20 and features['hurst'] < 0.45:
                            continue
                        
                        score = self._calculate_industry_score(features, market_lle, market_tor)
                        
                        if score > 0:  # 降低门槛：只要正分就考虑
                            candidates.append((code, score, features, latest['close']))
                    
                    # 按得分排序
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    
                    # 买入前5名
                    for code, score, features, price in candidates[:5]:
                        if code in self.positions:
                            continue
                        
                        # 仓位：等权分配
                        available_slots = self.max_positions - len(self.positions)
                        if available_slots <= 0:
                            break
                        
                        if market_risk == 'WARNING':
                            weight = 0.08  # WARNING时每个行业8%
                        else:
                            weight = 0.18  # CLEAR时每个行业18%
                        
                        target_amount = weight * self.cash
                        
                        if target_amount > 0:
                            self._execute_buy(code, price, target_amount, date, features, score)
                            daily_signal['buy_signals'].append({
                                'code': code, 'score': score
                            })
            
            # 日终估值
            price_data = {}
            for code in self.positions:
                if code in ind_data and date in ind_data[code].index:
                    price_data[code] = ind_data[code].loc[date, 'close']
            
            current_nav = self._mark_to_market(price_data)
            daily_signal['nav'] = current_nav
            daily_signal['cash_ratio'] = self.cash / current_nav if current_nav > 0 else 1
            daily_signal['n_positions'] = len(self.positions)
            
            self.nav_history.append(daily_signal)
        
        # 回测结束，清算
        for code in list(self.positions.keys()):
            if code in ind_data:
                last_date = ind_data[code].index[-1]
                price = ind_data[code].loc[last_date, 'close']
                self._execute_sell(code, price, last_date, 'BACKTEST_END')
        
        return self._generate_results(market_df, trading_days)
    
    def _execute_buy(self, code, price, target_amount, date, features, score):
        buy_price = price * (1 + self.slippage)
        shares = int(target_amount / buy_price / 100) * 100
        if shares < 100:
            return
        cost = shares * buy_price
        commission_fee = cost * self.commission
        total_cost = cost + commission_fee
        
        if total_cost > self.cash:
            shares = int(self.cash / buy_price / 100) * 100
            if shares < 100:
                return
            cost = shares * buy_price
            commission_fee = cost * self.commission
            total_cost = cost + commission_fee
        
        self.cash -= total_cost
        
        self.positions[code] = {
            'code': code,
            'shares': shares,
            'entry_price': buy_price,
            'max_price': buy_price,
            'entry_date': date,
            'entry_lle': features['lle'],
            'entry_hurst': features['hurst'],
            'entry_toroidality': features['toroidality'],
            'score': score,
            'cost': total_cost,
        }
        
        self.trade_log.append({
            'date': date, 'code': code, 'action': 'BUY',
            'price': buy_price, 'shares': shares, 'commission': commission_fee,
            'score': score, 'strategy': 'rotation',
            'lle': features['lle'], 'hurst': features['hurst'],
            'toroidality': features['toroidality'],
        })
    
    def _execute_sell(self, code, price, date, reason):
        if code not in self.positions:
            return
        pos = self.positions[code]
        sell_price = price * (1 - self.slippage)
        revenue = pos['shares'] * sell_price
        commission_fee = revenue * self.commission
        net_revenue = revenue - commission_fee
        self.cash += net_revenue
        
        pnl = net_revenue - pos['cost']
        pnl_pct = (sell_price - pos['entry_price']) / pos['entry_price']
        
        self.trade_log.append({
            'date': date, 'code': code, 'action': 'SELL',
            'price': sell_price, 'shares': pos['shares'], 'commission': commission_fee,
            'reason': reason, 'pnl': pnl, 'pnl_pct': pnl_pct,
            'hold_days': (date - pos['entry_date']).days,
            'strategy': 'rotation',
        })
        del self.positions[code]
    
    def _mark_to_market(self, price_data):
        total = self.cash
        for code, pos in self.positions.items():
            if code in price_data:
                total += pos['shares'] * price_data[code]
            else:
                total += pos['shares'] * pos['entry_price']
        return total
    
    def _generate_results(self, market_df, trading_days):
        nav_df = pd.DataFrame(self.nav_history)
        if len(nav_df) == 0:
            return {}
        nav_df = nav_df.set_index('date')
        nav_series = nav_df['nav']
        
        # 基准：沪深300
        market_bench = market_df[market_df.index.isin(trading_days)]['close']
        
        # 回撤
        peak = nav_series.expanding().max()
        drawdown = nav_series / peak - 1
        
        # 交易统计
        trades = pd.DataFrame(self.trade_log)
        
        # 绩效指标
        total_return = nav_series.iloc[-1] / self.initial_cash - 1
        n_years = (nav_series.index[-1] - nav_series.index[0]).days / 365.25
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        
        daily_ret = nav_series.pct_change().dropna()
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if len(daily_ret) > 1 and daily_ret.std() > 0 else 0
        
        max_dd = drawdown.min()
        
        sells = trades[trades['action'] == 'SELL'].copy() if len(trades) > 0 else pd.DataFrame()
        if len(sells) > 0 and 'pnl_pct' in sells.columns:
            valid = sells[sells['pnl_pct'].notna()]
            wins = valid[valid['pnl_pct'] > 0]
            losses = valid[valid['pnl_pct'] <= 0]
            win_rate = len(wins) / len(valid) if len(valid) > 0 else 0
            avg_win = wins['pnl_pct'].mean() if len(wins) > 0 else 0
            avg_loss = losses['pnl_pct'].mean() if len(losses) > 0 else 0
            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        else:
            win_rate = 0
            profit_factor = 0
        
        bench_return = market_bench.iloc[-1] / market_bench.iloc[0] - 1 if len(market_bench) > 0 else 0
        
        results = {
            'nav_series': nav_series,
            'drawdown_series': drawdown,
            'trades': trades,
            'market_df': market_df.reset_index(),
            'total_return': total_return,
            'annual_return': annual_return,
            'max_drawdown': max_dd,
            'sharpe': sharpe,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'n_trades': len(trades),
            'bench_return': bench_return,
            'excess_return': total_return - bench_return,
        }
        
        print(f"\n{'='*60}")
        print(f"行业轮动回测结果")
        print(f"{'='*60}")
        print(f"初始资金:     ¥{self.initial_cash:,.0f}")
        print(f"期末净值:     ¥{nav_series.iloc[-1]:,.0f}")
        print(f"累计收益率:   {total_return:.2%}")
        print(f"年化收益率:   {annual_return:.2%}")
        print(f"基准收益(沪深300): {bench_return:.2%}")
        print(f"超额收益:     {total_return - bench_return:+.2%}")
        print(f"最大回撤:     {max_dd:.2%}")
        print(f"夏普比率:     {sharpe:.2f}")
        print(f"胜率:         {win_rate:.1%}")
        print(f"盈亏比:       {profit_factor:.2f}")
        print(f"总交易次数:   {len(trades)}")
        
        return results


# ============================================================
# 主函数
# ============================================================

def main():
    print("="*60)
    print("分析力学量化交易系统 v4.0 — 行业轮动回测")
    print("="*60)
    
    # 1. 加载数据
    print("\n[1/3] 加载行业数据...")
    industry_data, market_df, industry_features = load_industry_data()
    print(f"  行业数: {len(industry_data)}")
    print(f"  大盘数据: {len(market_df)}条")
    
    # 2. 风控器
    print("\n[2/3] 初始化风控器...")
    risk_manager = RiskManager()
    
    # 3. 运行回测
    print("\n[3/3] 运行回测...")
    engine = IndustryRotationBacktest(
        initial_cash=1_000_000,
        commission=0.0003,
        slippage=0.001,
        max_positions=5,
        rebalance_days=5,
    )
    
    results = engine.run(industry_data, market_df, risk_manager)
    
    # 保存结果
    with open('backtest_results_v4.pkl', 'wb') as f:
        pickle.dump(results, f)
    print("\n结果已保存到 backtest_results_v4.pkl")
    
    # 年度收益
    nav = results['nav_series']
    market = results['market_df']
    print(f"\n年度收益:")
    print(f"  {'年份':>6s}  {'策略':>8s}  {'基准':>8s}  {'超额':>8s}")
    for year in range(2020, 2027):
        nav_y = nav[(nav.index >= f'{year}-01-01') & (nav.index <= f'{year}-12-31')]
        m_y = market[(market['date'] >= f'{year}-01-01') & (market['date'] <= f'{year}-12-31')]
        if len(nav_y) > 0 and len(m_y) > 0:
            s_ret = nav_y.iloc[-1] / nav_y.iloc[0] - 1
            b_ret = m_y['close'].iloc[-1] / m_y['close'].iloc[0] - 1
            print(f"  {year:>6d}  {s_ret:>+7.2%}  {b_ret:>+7.2%}  {s_ret-b_ret:>+7.2%}")
    
    # 关键时段
    print(f"\n关键时段:")
    for label, start, end in [('2022下跌', '2022-01-01', '2022-12-31'),
                               ('2026年7月', '2026-06-15', '2026-08-11')]:
        nav_p = nav[(nav.index >= start) & (nav.index <= end)]
        m_p = market[(market['date'] >= start) & (market['date'] <= end)]
        dd_p = results['drawdown_series'][(results['drawdown_series'].index >= start) & (results['drawdown_series'].index <= end)]
        s_ret = nav_p.iloc[-1] / nav_p.iloc[0] - 1 if len(nav_p) > 0 else 0
        b_ret = m_p['close'].iloc[-1] / m_p['close'].iloc[0] - 1 if len(m_p) > 0 else 0
        s_dd = dd_p.min() if len(dd_p) > 0 else 0
        b_dd = ((m_p['close'] / m_p['close'].expanding().max()) - 1).min() if len(m_p) > 0 else 0
        print(f"  {label}: 策略{s_ret:+.2%} 基准{b_ret:+.2%} | 回撤{s_dd:+.2%} vs {b_dd:+.2%}")
    
    # 卖出原因
    trades = results['trades']
    sells = trades[trades['action'] == 'SELL'].copy() if len(trades) > 0 else pd.DataFrame()
    if len(sells) > 0:
        print(f"\n卖出原因:")
        sells['cat'] = sells['reason'].apply(lambda r: 'STOP_LOSS' if r.startswith('STOP') else ('TRAILING_PROFIT' if r.startswith('TRAILING') else r))
        for cat, grp in sells.groupby('cat'):
            wr = (grp['pnl_pct'] > 0).mean() * 100
            avg = grp['pnl_pct'].mean() * 100
            print(f"  {cat:>25s}: {len(grp):3d}次  胜率={wr:.1f}%  平均={avg:+.2f}%")


if __name__ == '__main__':
    main()
