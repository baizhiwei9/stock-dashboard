#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投资组合跟踪系统
==============
功能：
- 初始资金100万
- 按每日推荐股票的pred_prob作为权重分配买入
- 每日记录净值、收益率
- 跟踪当年收益、总收益、总收益率
- 类似基金的跟踪方式

作者: Codex AI
日期: 2026-07-15
"""

import os
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import time


# 配置
INITIAL_CAPITAL = 1_000_000  # 初始资金100万
OUTPUT_DIR = './output/portfolio'
TOP_N_STOCKS = 20  # 使用top N股票构建组合


class PortfolioTracker:
    """投资组合跟踪器"""
    
    def __init__(self, initial_capital: float = INITIAL_CAPITAL):
        self.initial_capital = initial_capital
        self.portfolio_dir = OUTPUT_DIR
        os.makedirs(self.portfolio_dir, exist_ok=True)
        
        # 组合状态文件
        self.state_file = f'{self.portfolio_dir}/portfolio_state.json'
        self.history_file = f'{self.portfolio_dir}/portfolio_history.csv'
        
        # 加载或初始化状态
        self.state = self._load_state()
        self.history = self._load_history()
        
    def _load_state(self) -> Dict:
        """加载组合状态"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 初始状态
        return {
            'initial_capital': self.initial_capital,
            'current_nav': self.initial_capital,
            'start_date': datetime.now().strftime('%Y-%m-%d'),
            'last_update': None,
            'holdings': {},
            'total_invested': 0,
            'year_start_nav': self.initial_capital,
            'year_pnl': 0,
            'total_pnl': 0,
            'total_return': 0,
            'year_return': 0,
            'trade_count': 0,
            'cash': self.initial_capital,
        }
    
    def _save_state(self):
        """保存组合状态"""
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
    
    def _load_history(self) -> pd.DataFrame:
        """加载历史记录"""
        if os.path.exists(self.history_file):
            return pd.read_csv(self.history_file)
        return pd.DataFrame(columns=[
            'date', 'nav', 'daily_return', 'daily_return_pct',
            'year_pnl', 'year_return', 'total_pnl', 'total_return',
            'holdings_count', 'turnover'
        ])
    
    def _save_history(self):
        """保存历史记录"""
        self.history.to_csv(self.history_file, index=False)
    
    def get_stock_prices(self, codes: List[str]) -> Dict[str, float]:
        """获取股票当前价格 - 腾讯行情"""
        prices = {}
        
        batch_size = 50
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i+batch_size]
            code_str = ','.join(batch)
            
            try:
                url = f'https://qt.gtimg.cn/q={code_str}'
                resp = requests.get(url, timeout=10)
                resp.encoding = 'gbk'
                
                for line in resp.text.strip().split('\n'):
                    if '~' not in line:
                        continue
                    parts = line.split('~')
                    if len(parts) < 5:
                        continue
                    
                    # 从行首 v_sh600808="1~... 提取代码
                    code_raw = line.split('=')[0].replace('v_', '').strip()
                    price = float(parts[3]) if parts[3] else 0
                    if price > 0:
                        prices[code_raw] = price
                            
            except Exception as e:
                print(f"  获取行情失败: {e}")
            
            time.sleep(0.1)
        
        return prices
    
    def load_daily_recommendation(self, date: str = None, mode: str = 'morning') -> Optional[Dict]:
        """加载某日的推荐数据"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        json_file = f'./output/daily_top20_{date}_{mode}.json'
        
        if not os.path.exists(json_file):
            for m in ['noon', 'morning']:
                json_file = f'./output/daily_top20_{date}_{m}.json'
                if os.path.exists(json_file):
                    break
            else:
                return None
        
        with open(json_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def calculate_weights(self, recommendations: List[Dict]) -> Dict[str, float]:
        """根据pred_prob计算权重（归一化）"""
        probs = [r['pred_prob'] for r in recommendations]
        total_prob = sum(probs)
        
        if total_prob == 0:
            weight = 1.0 / len(recommendations)
            return {r['code']: weight for r in recommendations}
        
        weights = {}
        for r in recommendations:
            weights[r['code']] = r['pred_prob'] / total_prob
        
        return weights
    
    def initialize_portfolio(self, recommendations: List[Dict], prices: Dict[str, float]):
        """初始化组合（首次建仓）"""
        print("\n[初始化组合]")
        
        weights = self.calculate_weights(recommendations)
        
        holdings = {}
        total_invested = 0
        
        for rec in recommendations:
            code = rec['code']
            weight = weights.get(code, 0)
            price = prices.get(code, rec.get('close', 0))
            
            if price <= 0:
                continue
            
            alloc_amount = self.initial_capital * weight
            shares = int(alloc_amount / price / 100) * 100
            if shares < 100:
                shares = 100
            
            actual_cost = shares * price
            
            holdings[code] = {
                'name': rec.get('name', ''),
                'shares': shares,
                'cost_price': price,
                'current_price': price,
                'cost': actual_cost,
                'weight': weight,
                'industry': rec.get('industry', ''),
                'pred_prob': rec.get('pred_prob', 0),
            }
            total_invested += actual_cost
        
        cash = self.initial_capital - total_invested
        
        self.state['holdings'] = holdings
        self.state['total_invested'] = total_invested
        self.state['cash'] = cash
        self.state['current_nav'] = self.initial_capital
        self.state['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.state['trade_count'] += 1
        
        print(f"  初始资金: {self.initial_capital:,.0f}")
        print(f"  持仓股票: {len(holdings)} 只")
        print(f"  投入金额: {total_invested:,.0f}")
        print(f"  剩余现金: {cash:,.0f}")
        
        self._save_state()
    
    def rebalance_portfolio(self, new_recommendations: List[Dict], prices: Dict[str, float]):
        """调仓：根据新推荐调整持仓"""
        print("\n[调仓]")
        
        total_value = self.state.get('cash', 0)
        for code, holding in self.state['holdings'].items():
            price = prices.get(code, holding.get('current_price', 0))
            holding['current_price'] = price
            total_value += holding['shares'] * price
        
        weights = self.calculate_weights(new_recommendations)
        new_codes = set(weights.keys())
        old_codes = set(self.state['holdings'].keys())
        
        to_sell = old_codes - new_codes
        for code in to_sell:
            holding = self.state['holdings'][code]
            price = prices.get(code, holding.get('current_price', 0))
            sell_value = holding['shares'] * price
            self.state['cash'] += sell_value
            print(f"  卖出 {code} {holding['name']}: {holding['shares']}股 @ {price:.2f} = {sell_value:,.0f}")
            del self.state['holdings'][code]
        
        for code in new_codes:
            weight = weights[code]
            target_value = total_value * weight
            
            if code in self.state['holdings']:
                holding = self.state['holdings'][code]
                current_value = holding['shares'] * prices.get(code, holding['current_price'])
                diff = target_value - current_value
                
                price = prices.get(code, holding['current_price'])
                if price <= 0:
                    continue
                
                if abs(diff) > price * 100:
                    shares_diff = int(diff / price / 100) * 100
                    if shares_diff > 0 and shares_diff * price <= self.state['cash']:
                        holding['shares'] += shares_diff
                        self.state['cash'] -= shares_diff * price
                        print(f"  加仓 {code} {holding['name']}: +{shares_diff}股")
                    elif shares_diff < 0:
                        holding['shares'] += shares_diff
                        self.state['cash'] -= shares_diff * price
                        print(f"  减仓 {code} {holding['name']}: {shares_diff}股")
            else:
                price = prices.get(code, 0)
                if price <= 0:
                    for rec in new_recommendations:
                        if rec['code'] == code:
                            price = rec.get('close', 0)
                            break
                
                if price <= 0:
                    continue
                
                shares = int(target_value / price / 100) * 100
                if shares < 100:
                    shares = 100
                
                cost = shares * price
                if cost <= self.state['cash']:
                    rec_info = next((r for r in new_recommendations if r['code'] == code), {})
                    
                    self.state['holdings'][code] = {
                        'name': rec_info.get('name', ''),
                        'shares': shares,
                        'cost_price': price,
                        'current_price': price,
                        'cost': cost,
                        'weight': weight,
                        'industry': rec_info.get('industry', ''),
                        'pred_prob': rec_info.get('pred_prob', 0),
                    }
                    self.state['cash'] -= cost
                    print(f"  买入 {code} {rec_info.get('name', '')}: {shares}股 @ {price:.2f}")
        
        self.state['trade_count'] += 1
        self._save_state()
    
    def update_daily(self, date: str = None):
        """每日更新净值"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        print(f"\n[每日更新] {date}")
        
        codes = list(self.state['holdings'].keys())
        if not codes:
            print("  无持仓")
            return
        
        prices = self.get_stock_prices(codes)
        
        if not prices:
            print("  无法获取价格，跳过更新")
            return
        
        total_value = self.state.get('cash', 0)
        holding_value = 0
        
        for code, holding in self.state['holdings'].items():
            price = prices.get(code, holding.get('current_price', 0))
            holding['current_price'] = price
            value = holding['shares'] * price
            holding_value += value
            total_value += value
        
        prev_nav = self.state['current_nav']
        daily_pnl = total_value - prev_nav
        daily_return_pct = (daily_pnl / prev_nav * 100) if prev_nav > 0 else 0
        
        year_start = self.state.get('year_start_nav', self.initial_capital)
        year_pnl = total_value - year_start
        year_return = (year_pnl / year_start * 100) if year_start > 0 else 0
        
        total_pnl = total_value - self.initial_capital
        total_return = (total_pnl / self.initial_capital * 100)
        
        self.state['current_nav'] = total_value
        self.state['last_update'] = f"{date} {datetime.now().strftime('%H:%M:%S')}"
        self.state['year_pnl'] = year_pnl
        self.state['year_return'] = year_return
        self.state['total_pnl'] = total_pnl
        self.state['total_return'] = total_return
        
        new_row = {
            'date': date,
            'nav': total_value,
            'daily_return': daily_pnl,
            'daily_return_pct': daily_return_pct,
            'year_pnl': year_pnl,
            'year_return': year_return,
            'total_pnl': total_pnl,
            'total_return': total_return,
            'holdings_count': len(self.state['holdings']),
            'turnover': self.state.get('trade_count', 0),
        }
        
        if len(self.history) > 0 and self.history['date'].iloc[-1] == date:
            self.history = self.history[self.history['date'] != date]
        
        self.history = pd.concat([self.history, pd.DataFrame([new_row])], ignore_index=True)
        
        self._save_state()
        self._save_history()
        
        print(f"  持仓市值: {holding_value:,.0f}")
        print(f"  现金: {self.state.get('cash', 0):,.0f}")
        print(f"  总资产: {total_value:,.0f}")
        print(f"  当日收益: {daily_pnl:+,.0f} ({daily_return_pct:+.2f}%)")
        print(f"  当年收益: {year_pnl:+,.0f} ({year_return:+.2f}%)")
        print(f"  总收益: {total_pnl:+,.0f} ({total_return:+.2f}%)")
    
    def generate_report(self) -> str:
        """生成组合报告"""
        report = []
        report.append("=" * 60)
        report.append("📊 投资组合跟踪报告")
        report.append("=" * 60)
        report.append(f"报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"初始资金: {self.initial_capital:,.0f} 元")
        report.append(f"开始日期: {self.state.get('start_date', 'N/A')}")
        report.append("")
        
        report.append("【资产概览】")
        nav = self.state.get('current_nav', self.initial_capital)
        cash = self.state.get('cash', 0)
        holding_value = nav - cash
        
        report.append(f"  总资产: {nav:,.0f} 元")
        report.append(f"  持仓市值: {holding_value:,.0f} 元")
        report.append(f"  现金: {cash:,.0f} 元")
        report.append("")
        
        report.append("【收益统计】")
        report.append(f"  当日收益: {self.state.get('daily_pnl', 0):+,.0f} 元")
        report.append(f"  当年收益: {self.state.get('year_pnl', 0):+,.0f} 元 ({self.state.get('year_return', 0):+.2f}%)")
        report.append(f"  总收益: {self.state.get('total_pnl', 0):+,.0f} 元 ({self.state.get('total_return', 0):+.2f}%)")
        report.append(f"  调仓次数: {self.state.get('trade_count', 0)}")
        report.append("")
        
        # 最大回撤
        dd = self.calculate_max_drawdown()
        report.append("【最大回撤】")
        if dd['max_drawdown_pct'] == 0:
            report.append("  暂无回撤数据")
        else:
            report.append(f"  最大回撤: {dd['max_drawdown_pct']:.2f}%")
            report.append(f"  峰值: {dd['peak_nav']:,.0f} 元 ({dd['peak_date']})")
            report.append(f"  谷底: {dd['trough_nav']:,.0f} 元 ({dd['trough_date']})")
        report.append("")
        
        holdings = self.state.get('holdings', {})
        if holdings:
            report.append("【持仓明细】")
            report.append(f"  {'代码':<12} {'名称':<8} {'股数':>8} {'成本':>8} {'现价':>8} {'收益率':>10} {'权重':>6}")
            report.append("  " + "-" * 70)
            
            for code, h in sorted(holdings.items(), key=lambda x: -x[1].get('weight', 0)):
                cost_price = h['cost_price']
                current_price = h['current_price']
                return_pct = ((current_price - cost_price) / cost_price * 100) if cost_price > 0 else 0
                
                # 大于10%收益标红星
                if return_pct > 10:
                    return_str = f"🔴{return_pct:>+7.2f}%🔴"
                else:
                    return_str = f"{return_pct:>+9.2f}%"
                
                report.append(f"  {code:<12} {h['name']:<8} {h['shares']:>8,d} {cost_price:>8.2f} {current_price:>8.2f} {return_str:>14} {h['weight']*100:>5.1f}%")
            
            report.append("")
        
        if len(self.history) >= 1:
            report.append("【近期表现】")
            recent = self.history.tail(10)
            for _, row in recent.iterrows():
                emoji = "📈" if row['daily_return_pct'] >= 0 else "📉"
                report.append(f"  {row['date']} {emoji} {row['daily_return_pct']:+.2f}% | 净值: {row['nav']:,.0f} | 累计: {row['total_return']:+.2f}%")
            report.append("")
        
        report.append("=" * 60)
        
        return '\n'.join(report)
    
    def calculate_max_drawdown(self) -> Dict:
        """计算最大回撤及回撤详情"""
        if len(self.history) < 2:
            return {'max_drawdown_pct': 0, 'peak_date': '', 'trough_date': '', 'peak_nav': 0, 'trough_nav': 0}
        
        navs = self.history['nav'].values
        dates = self.history['date'].values
        
        # 计算累计最高点
        peak = np.maximum.accumulate(navs)
        drawdown = (navs - peak) / peak * 100  # 百分比
        
        max_dd_idx = np.argmin(drawdown)
        max_dd_pct = drawdown[max_dd_idx]
        
        # 找到回撤起点（峰值位置）
        peak_idx = np.argmax(navs[:max_dd_idx + 1])
        
        return {
            'max_drawdown_pct': max_dd_pct,
            'peak_date': str(dates[peak_idx]),
            'trough_date': str(dates[max_dd_idx]),
            'peak_nav': navs[peak_idx],
            'trough_nav': navs[max_dd_idx],
        }
    
    def run_daily_task(self, date: str = None, mode: str = 'morning'):
        """执行每日任务"""
        print("\n" + "=" * 60)
        print("投资组合跟踪系统 - 每日任务")
        print("=" * 60)
        
        print("\n[1/4] 加载推荐数据...")
        rec_data = self.load_daily_recommendation(date, mode)
        
        if rec_data is None:
            print("  未找到推荐数据，跳过调仓")
            recommendations = []
        else:
            recommendations = rec_data.get('top20', [])[:TOP_N_STOCKS]
            print(f"  找到 {len(recommendations)} 只推荐股票")
        
        print("\n[2/4] 获取行情数据...")
        codes = [r['code'] for r in recommendations]
        existing_codes = list(self.state.get('holdings', {}).keys())
        all_codes = list(set(codes + existing_codes))
        
        prices = self.get_stock_prices(all_codes)
        print(f"  获取到 {len(prices)} 只股票价格")
        
        print("\n[3/4] 处理持仓...")
        if not self.state.get('holdings'):
            if recommendations:
                self.initialize_portfolio(recommendations, prices)
        else:
            if recommendations:
                self.rebalance_portfolio(recommendations, prices)
        
        print("\n[4/4] 更新净值...")
        self.update_daily(date)
        
        report = self.generate_report()
        print("\n" + report)
        
        report_file = f"{self.portfolio_dir}/portfolio_report_{date or datetime.now().strftime('%Y-%m-%d')}.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n✓ 报告已保存: {report_file}")
        
        return report




def update_closing_nav(date: str = None):
    """收盘后更新净值（15:30后调用）"""
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"\n[收盘净值更新] {date}")
    tracker = PortfolioTracker()
    
    if not tracker.state.get('holdings'):
        print("  无持仓，跳过")
        return
    
    # 获取收盘价
    codes = list(tracker.state['holdings'].keys())
    prices = tracker.get_stock_prices(codes)
    
    if not prices:
        print("  无法获取收盘价")
        return
    
    # 更新持仓价格
    total_value = tracker.state.get('cash', 0)
    for code, holding in tracker.state['holdings'].items():
        price = prices.get(code, holding.get('current_price', 0))
        holding['current_price'] = price
        total_value += holding['shares'] * price
    
    # 计算收益
    prev_nav = tracker.state['current_nav']
    daily_pnl = total_value - prev_nav
    daily_return_pct = (daily_pnl / prev_nav * 100) if prev_nav > 0 else 0
    
    year_start = tracker.state.get('year_start_nav', tracker.initial_capital)
    year_pnl = total_value - year_start
    year_return = (year_pnl / year_start * 100) if year_start > 0 else 0
    
    total_pnl = total_value - tracker.initial_capital
    total_return = (total_pnl / tracker.initial_capital * 100)
    
    tracker.state['current_nav'] = total_value
    tracker.state['last_update'] = f"{date} 15:30:00"
    tracker.state['year_pnl'] = year_pnl
    tracker.state['year_return'] = year_return
    tracker.state['total_pnl'] = total_pnl
    tracker.state['total_return'] = total_return
    
    # 更新历史
    new_row = {
        'date': date,
        'nav': total_value,
        'daily_return': daily_pnl,
        'daily_return_pct': daily_return_pct,
        'year_pnl': year_pnl,
        'year_return': year_return,
        'total_pnl': total_pnl,
        'total_return': total_return,
        'holdings_count': len(tracker.state['holdings']),
        'turnover': tracker.state.get('trade_count', 0),
    }
    
    if len(tracker.history) > 0 and tracker.history['date'].iloc[-1] == date:
        tracker.history = tracker.history[tracker.history['date'] != date]
    
    tracker.history = pd.concat([tracker.history, pd.DataFrame([new_row])], ignore_index=True)
    tracker._save_state()
    tracker._save_history()
    
    print(f"  总资产: {total_value:,.0f}")
    print(f"  当日收益: {daily_pnl:+,.0f} ({daily_return_pct:+.2f}%)")
    print(f"  当年收益: {year_pnl:+,.0f} ({year_return:+.2f}%)")
    print(f"  总收益: {total_pnl:+,.0f} ({total_return:+.2f}%)")
    
    return tracker.generate_report()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='投资组合跟踪系统')
    parser.add_argument('--date', type=str, default=None, help='日期 (YYYY-MM-DD)')
    parser.add_argument('--mode', type=str, default='morning', choices=['morning', 'noon', 'closing'])
    parser.add_argument('--capital', type=float, default=INITIAL_CAPITAL, help='初始资金')
    
    args = parser.parse_args()
    
    tracker = PortfolioTracker(initial_capital=args.capital)
    
    if args.mode == 'closing':
        update_closing_nav(date=args.date)
    else:
        tracker.run_daily_task(date=args.date, mode=args.mode)
