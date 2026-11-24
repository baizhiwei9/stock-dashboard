#!/usr/bin/env python3
"""
TDA流形旋转策略 - 每日运行入口 (股债50/50组合)
================================================
每个交易日19:00自动运行:
1. 股票端: 8只ETF 60日价格 -> TDA分析 -> 防御权重 -> 个股选择
2. 债券端: 国债期货T0 -> 宏观状态+TDA震荡 -> 多空方向 -> 止损
3. 组合收益: 0.5*股票 + 0.5*债券
4. 年底自动重置累计收益
5. 飞书交互卡片推送 (含"报告来自于 DoMate"消息头)
"""

# ===== akshare 禁用 IPv6 =====
import socket
socket.setdefaulttimeout(30)
_orig_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kw: [r for r in _orig_getaddrinfo(*args, **kw) if r[0] == socket.AF_INET]

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

from data_fetcher import (
    FACTOR_ETF_MAP, fetch_all_etf_prices, fetch_benchmark_prices,
    get_trade_calendar, is_trading_day,
    select_all_factor_stocks, format_date_display
)
from tda_core import (
    run_tda_analysis_safe, compute_factor_sharpe, compute_defensive_weights
)
from bond_module import (
    fetch_bond_futures, run_bond_analysis
)
from feishu_pusher import generate_daily_report_card, send_feishu_card

# ========== 路径配置 ==========

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, 'state')
SIGNAL_FILE = os.path.join(STATE_DIR, 'signal_history.json')
PNL_FILE = os.path.join(STATE_DIR, 'pnl_history.json')
WINDOW = 60

# ========== 状态管理 ==========

def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)

def load_json(filepath, default=None):
    if default is None:
        default = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return default

def save_json(filepath, data):
    ensure_state_dir()
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def load_signal_history():
    return load_json(SIGNAL_FILE, {'signals': [], 'last_date': None})

def save_signal_history(history):
    save_json(SIGNAL_FILE, history)

def load_pnl_history():
    return load_json(PNL_FILE, {
        'daily_records': [],
        'cumulative_value': 1.0,
        'peak_value': 1.0,
        'max_drawdown': 0.0,
        'year': str(datetime.date.today().year),
        'year_start_value': 1.0,
    })

def save_pnl_history(history):
    save_json(PNL_FILE, history)

# ========== 年度重置 ==========

def check_year_reset(pnl_history, trade_date):
    current_year = trade_date[:4]
    stored_year = pnl_history.get('year', current_year)
    
    if current_year != stored_year:
        print(f"\n=== 年度重置: {stored_year} -> {current_year} ===")
        prev_year_return = (pnl_history['cumulative_value'] / pnl_history.get('year_start_value', 1.0) - 1) * 100
        print(f"  {stored_year}年度累计收益: {prev_year_return:.2f}%")
        
        pnl_history['year'] = current_year
        pnl_history['year_start_value'] = pnl_history['cumulative_value']
        pnl_history['daily_records'] = []
        pnl_history['peak_value'] = pnl_history['cumulative_value']
        pnl_history['max_drawdown'] = 0.0
        
        yearly_summary = {
            'year': stored_year,
            'total_return': round(prev_year_return, 2),
            'final_value': round(pnl_history['cumulative_value'], 6),
        }
        save_json(os.path.join(STATE_DIR, f'yearly_summary_{stored_year}.json'), yearly_summary)
    
    return pnl_history

# ========== 核心运行逻辑 ==========

def run_daily(trade_date=None, send_feishu=True, select_stocks=True):
    """
    每日运行入口 (股债50/50组合)
    """
    if trade_date is None:
        trade_date = datetime.date.today().strftime('%Y%m%d')
    
    date_display = format_date_display(trade_date)
    print(f"\n{'='*60}")
    print(f"TDA流形旋转策略 - 每日运行(股债50/50) | {date_display}")
    print(f"{'='*60}")
    
    # 1. 检查交易日
    if not is_trading_day(trade_date):
        print(f"{date_display} 非交易日, 跳过")
        return None
    
    # 2. 获取ETF数据
    print(f"\n[1/7] 获取ETF数据 (窗口={WINDOW}日)...")
    cal = get_trade_calendar()
    if trade_date in cal:
        idx = cal.index(trade_date)
        start_idx = max(0, idx - WINDOW - 5)
        start_date = cal[start_idx]
    else:
        start_date = (datetime.datetime.strptime(trade_date, '%Y%m%d') - 
                      datetime.timedelta(days=WINDOW * 2)).strftime('%Y%m%d')
    
    price_df = fetch_all_etf_prices(start_date, trade_date)
    if price_df.empty or len(price_df) < WINDOW:
        print(f"ETF数据不足: {len(price_df)}行, 需要至少{WINDOW}行")
        return None
    
    price_window = price_df.tail(WINDOW)
    print(f"  价格窗口: {price_window.shape}")
    
    # 3. 股票端TDA分析
    print(f"\n[2/7] 股票端TDA分析...")
    tda_result = run_tda_analysis_safe(price_window)
    features = tda_result['features']
    market = tda_result['market']
    print(f"  状态: {market['state']}, 脆弱性: {market['vulnerability']}%, 仓位: {market['position_pct']}%")
    
    # 4. 防御权重
    print(f"\n[3/7] 计算(s+1)²防御权重...")
    sharpe = compute_factor_sharpe(price_window, window=WINDOW)
    weights = compute_defensive_weights(sharpe)
    for factor, w in weights.items():
        print(f"  {factor}: {w*100:.1f}% (夏普={sharpe.get(factor, 0):.3f})")
    
    # 5. 个股选择
    stocks = {}
    if select_stocks:
        print(f"\n[4/7] 下钻个股...")
        try:
            stocks = select_all_factor_stocks(trade_date)
            for factor, stock_list in stocks.items():
                if stock_list:
                    names = ", ".join([f"{s['name']}({s['code'][:6]})" for s in stock_list])
                    print(f"  {factor}: {names}")
        except Exception as e:
            print(f"  选股失败: {e}")
    else:
        print(f"\n[4/7] 跳过个股选择")
    
    # 6. 债券端分析
    print(f"\n[5/7] 债券端分析...")
    try:
        bond_data = run_bond_analysis(trade_date, window=WINDOW)
        if bond_data:
            print(f"  宏观: {bond_data['macro']['macro_state']}, 方向: {bond_data['direction_str']}")
            print(f"  T0价格: {bond_data['bond_price']}, 止损: {bond_data['stop_price']}")
        else:
            print(f"  债券端分析失败, 使用默认值")
            bond_data = None
    except Exception as e:
        print(f"  债券端异常: {e}")
        bond_data = None
    
    # 7. 组合收益计算
    print(f"\n[6/7] 组合收益跟踪...")
    pnl_history = load_pnl_history()
    pnl_history = check_year_reset(pnl_history, trade_date)
    
    signal_history = load_signal_history()
    # 前值信号: 取 signal_history 中 trade_date 严格早于当日的最近一条,
    # 避免同一交易日重复运行时误用当日自身信号作前值, 导致收益错算
    prev_signal = None
    for _s in sorted(signal_history.get('signals', []), key=lambda x: x.get('trade_date', '')):
        if _s.get('trade_date', '') < trade_date:
            prev_signal = _s
        else:
            break
    if prev_signal is None:
        _ls = signal_history.get('last_signal')
        if _ls and _ls.get('trade_date', '') < trade_date:
            prev_signal = _ls
    
    today_stock_return = 0.0
    today_bond_return = 0.0
    benchmark_return = 0.0
    
    if prev_signal:
        # 股票端收益 (对缺失数据稳健: 无持仓权重或当日无行情的因子按 0 收益计入,
        # 不能用 0*NaN 的写法, 否则单个因子缺失会把整组收益污染成 NaN)
        returns_df = price_df.pct_change()
        if trade_date in returns_df.index:
            day_returns = returns_df.loc[trade_date]
            prev_weights = prev_signal.get('weights', {})
            prev_position = prev_signal.get('position_ratio', 0)
            contrib = 0.0
            missing = []
            for f in price_df.columns:
                w = prev_weights.get(f, 0)
                if not w:
                    continue
                r = day_returns.get(f, 0.0)
                if pd.isna(r):
                    missing.append(f)
                    continue
                contrib += w * float(r)
            if missing:
                print(f"  [警告] 以下因子当日行情缺失, 已按0收益计入: {', '.join(missing)}")
            today_stock_return = prev_position * contrib
        
        # 债券端收益
        prev_bond_dir = prev_signal.get('bond_direction', 0)
        if prev_bond_dir != 0:
            bond_df = fetch_bond_futures('T0', start_date, trade_date)
            if not bond_df.empty:
                bond_prices = bond_df.set_index('trade_date')['收盘价'].astype(float)
                if trade_date in bond_prices.index:
                    idx_bp = bond_prices.index.tolist().index(trade_date)
                    if idx_bp > 0:
                        today_bond_return = prev_bond_dir * (
                            bond_prices.iloc[idx_bp] / bond_prices.iloc[idx_bp - 1] - 1
                        )
        
        # 基准收益
        bench = fetch_benchmark_prices(start_date, trade_date)
        if not bench.empty and len(bench) >= 2:
            benchmark_return = (bench.iloc[-1] / bench.iloc[-2] - 1) if bench.iloc[-2] > 0 else 0
    
    # 组合收益 (50/50)
    today_combined = 0.5 * today_stock_return + 0.5 * today_bond_return
    
    # 更新累计净值 (幂等: 按 trade_date upsert 后, 依日期顺序重算整条净值链,
    # 重复运行同一交易日不会再追加重复记录, 也不会重复累乘净值)
    record = {
        'trade_date': trade_date,
        'stock_return': round(today_stock_return * 100, 4),
        'bond_return': round(today_bond_return * 100, 4),
        'combined_return': round(today_combined * 100, 4),
        'benchmark_return': round(benchmark_return * 100, 4),
        'cumulative_value': 0.0,
        'year_cumulative': 0.0,
        'total_cumulative': 0.0,
        'drawdown': 0.0,
        'max_drawdown': 0.0,
        'market_state': market['state'],
        'position_pct': market['position_pct'],
        'bond_direction': bond_data['direction_str'] if bond_data else 'N/A',
    }
    records = [r for r in pnl_history.get('daily_records', []) if r.get('trade_date') != trade_date]
    records.append(record)
    records.sort(key=lambda r: r['trade_date'])
    
    year_start = pnl_history.get('year_start_value', 1.0)
    cum = year_start
    peak = year_start
    mdd = 0.0
    for r in records:
        cum *= (1 + r['combined_return'] / 100)
        peak = max(peak, cum)
        dd = (peak - cum) / peak if peak > 0 else 0.0
        mdd = max(mdd, dd)
        r['cumulative_value'] = round(cum, 6)
        r['year_cumulative'] = round((cum / year_start - 1) * 100, 2)
        r['total_cumulative'] = round((cum - 1) * 100, 2)
        r['drawdown'] = round(dd * 100, 2)
        r['max_drawdown'] = round(mdd * 100, 2)
    pnl_history['daily_records'] = records
    pnl_history['cumulative_value'] = cum
    pnl_history['peak_value'] = peak
    pnl_history['max_drawdown'] = mdd
    
    year_cumulative = (cum / year_start - 1) * 100
    total_cumulative = (cum - 1) * 100
    drawdown = records[-1]['drawdown'] / 100
    
    # 夏普比和胜率
    daily_returns = [r['combined_return'] / 100 for r in pnl_history['daily_records']]
    n_days = len(daily_returns)
    if n_days >= 2:
        avg_ret = np.mean(daily_returns)
        std_ret = np.std(daily_returns)
        sharpe_ratio = avg_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0
        win_days = sum(1 for r in daily_returns if r > 0)
        win_rate = win_days / n_days * 100
        annualized = (1 + avg_ret) ** 252 - 1
    else:
        sharpe_ratio = 0
        win_rate = 0
        annualized = 0
    
    pnl_data = {
        'cumulative_return': year_cumulative,
        'total_cumulative_return': total_cumulative,
        'annualized_return': annualized * 100,
        'max_drawdown': pnl_history['max_drawdown'] * 100,
        'today_return': today_combined * 100,
        'stock_return': today_stock_return * 100,
        'bond_return': today_bond_return * 100,
        'benchmark_return': benchmark_return * 100,
        'excess_return': (today_combined - benchmark_return) * 100,
        'tracking_days': n_days,
        'sharpe': sharpe_ratio,
        'win_rate': win_rate,
    }
    
    print(f"  今日组合: {today_combined*100:.2f}% (股{today_stock_return*100:.2f}%+债{today_bond_return*100:.2f}%)")
    print(f"  年度累计: {year_cumulative:.2f}%, 全期: {total_cumulative:.2f}%")
    print(f"  回撤: {pnl_history['max_drawdown']*100:.2f}%, 夏普: {sharpe_ratio:.3f}")
    
    # 8. 保存状态
    print(f"\n[7/7] 保存状态 & 推送报告...")
    
    current_signal = {
        'trade_date': trade_date,
        'weights': weights,
        'position_ratio': market['position_ratio'],
        'market_state': market['state'],
        'bond_direction': bond_data['direction'] if bond_data else 0,
    }
    # 幂等: 同一交易日 upsert, 重复运行不会重复追加信号
    signal_history['last_signal'] = current_signal
    signal_history['last_date'] = trade_date
    _sigs = [s for s in signal_history.get('signals', []) if s.get('trade_date') != trade_date]
    _sigs.append({
        'trade_date': trade_date,
        'weights': weights,
        'position_ratio': market['position_ratio'],
        'market_state': market['state'],
        'bond_direction': bond_data['direction'] if bond_data else 0,
        'sharpe': {k: float(v) for k, v in sharpe.items()},
    })
    _sigs.sort(key=lambda s: s['trade_date'])
    if len(_sigs) > 60:
        _sigs = _sigs[-60:]
    signal_history['signals'] = _sigs
    
    save_signal_history(signal_history)
    save_pnl_history(pnl_history)
    
    # 9. 飞书推送
    signal_data = {
        'market': market,
        'features': features,
        'weights': weights,
        'stocks': stocks,
    }
    
    if send_feishu:
        card = generate_daily_report_card(signal_data, bond_data, pnl_data, trade_date)
        success = send_feishu_card(card)
        if success:
            print("  飞书报告推送成功")
        else:
            print("  飞书报告推送失败")
    
    print(f"\n{'='*60}")
    print(f"每日运行完成 | {date_display}")
    print(f"{'='*60}")
    
    return {
        'signal': signal_data,
        'bond': bond_data,
        'pnl': pnl_data,
        'trade_date': trade_date,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TDA流形旋转策略 - 每日运行(股债50/50)')
    parser.add_argument('--date', type=str, default=None, help='交易日期 YYYYMMDD')
    parser.add_argument('--no-feishu', action='store_true', help='不发送飞书报告')
    parser.add_argument('--no-stocks', action='store_true', help='跳过个股选择')
    args = parser.parse_args()
    
    run_daily(
        trade_date=args.date,
        send_feishu=not args.no_feishu,
        select_stocks=not args.no_stocks
    )
