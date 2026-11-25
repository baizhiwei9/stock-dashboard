#!/usr/bin/env python3
"""
V3: 行业资金流向信号策略 (正向跟钱走)
======================================
核心理念: 跟钱走, 不是反向
  - 资金流入增强 = 机会信号 -> 建仓/加仓
  - 资金流出加速 = 风险信号 -> 减仓/清仓
  - 无信号时空仓等待

信号设计:
  1. CMF(Chaikin Money Flow) 衡量资金流向
  2. CMF均线上穿0 = 机会信号(资金从流出转为流入)
  3. CMF均线下穿0 = 风险信号(资金从流入转为流出)
  4. CMF趋势加速 = 强化信号(资金流入/流出在加速)

持仓规则:
  - 最多持有5个行业
  - 出现机会信号 -> 入场
  - 出现风险信号 -> 出场
  - 市场整体资金流恶化 -> 全部清仓
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
import json
import os

warnings.filterwarnings('ignore')

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

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

BT_START = "2025-06-01"
BT_END = "2026-08-14"
TRADE_START = "2026-01-01"
MAX_HOLDINGS = 5
CMF_SHORT = 10   # 短期CMF
CMF_LONG = 20    # 长期CMF
SIGNAL_THRESHOLD = 0.0  # CMF穿过0线为信号


def get_industry_data(name):
    df = ak.stock_board_industry_index_ths(symbol=name, start_date=BT_START.replace("-",""), end_date=BT_END.replace("-",""))
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期').reset_index(drop=True)
    df.rename(columns={'日期':'date','开盘价':'open','最高价':'high','最低价':'low','收盘价':'close','成交量':'volume','成交额':'amount'}, inplace=True)
    df['ret'] = df['close'].pct_change()
    return df[['date','open','high','low','close','volume','amount','ret']]


def get_etf_data(code):
    prefix = "sh" if code.startswith("5") else "sz"
    df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['ret'] = df['close'].pct_change()
    return df[['date','close','volume','amount','ret']]


def build_panels(industry_data_dict):
    all_dates = None
    for name, df in industry_data_dict.items():
        dates = df['date'].tolist()
        if all_dates is None:
            all_dates = dates
        else:
            all_dates = sorted(set(all_dates) & set(dates))
    
    panels = {}
    for field in ['close','high','low','amount','ret','volume']:
        panels[field] = pd.DataFrame(index=all_dates)
    
    for name, df in industry_data_dict.items():
        df_idx = df.set_index('date')
        for field in ['close','high','low','amount','ret','volume']:
            panels[field][name] = df_idx[field]
    
    return panels


def compute_cmf(panels):
    """
    计算每个行业的CMF(Chaikin Money Flow) - 正向资金流指标
    CMF > 0: 资金净流入 (买方主导) = 机会
    CMF < 0: 资金净流出 (卖方主导) = 风险
    """
    close = panels['close']
    high = panels['high']
    low = panels['low']
    amount = panels['amount']
    volume = panels['volume']
    ret = panels['ret']
    
    # CLV: 收盘价在当日区间中的位置
    # CLV > 0: 收盘价接近最高价 = 买方强势
    # CLV < 0: 收盘价接近最低价 = 卖方强势
    clv = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for col in close.columns:
        rv = high[col] - low[col]
        valid = rv > 0
        clv.loc[valid, col] = (2*close.loc[valid,col] - high.loc[valid,col] - low.loc[valid,col]) / rv[valid]
        clv.loc[~valid, col] = 0.0
    
    # 资金流 = CLV * 成交额 (正=流入, 负=流出)
    money_flow = clv * amount
    
    # CMF短期 (10日): 敏感, 捕捉资金流变化
    cmf_short = money_flow.rolling(window=CMF_SHORT, min_periods=CMF_SHORT-2).sum() / \
                amount.rolling(window=CMF_SHORT, min_periods=CMF_SHORT-2).sum()
    
    # CMF长期 (20日): 平滑, 确认趋势
    cmf_long = money_flow.rolling(window=CMF_LONG, min_periods=CMF_LONG-2).sum() / \
               amount.rolling(window=CMF_LONG, min_periods=CMF_LONG-2).sum()
    
    # 主动净买入代理 (研报核心因子)
    # 跨行业标准化: 消除系统性偏差
    active_buy = amount * (1 + clv) / 2
    active_sell = amount * (1 - clv) / 2
    total_buy = active_buy.sum(axis=1)
    total_sell = active_sell.sum(axis=1)
    total_amount = amount.sum(axis=1)
    buy_ratio = active_buy.div(total_buy, axis=0)
    sell_ratio = active_sell.div(total_sell, axis=0)
    amount_ratio = amount.div(total_amount, axis=0)
    anb = (buy_ratio - sell_ratio) / amount_ratio.replace(0, np.nan)
    anb_ma = anb.rolling(window=CMF_LONG, min_periods=CMF_LONG-2).mean()
    
    # 成交额占比变化 (资金关注度)
    amt_share = amount_ratio.rolling(window=CMF_SHORT, min_periods=CMF_SHORT-2).mean()
    amt_share_prev = amount_ratio.shift(CMF_SHORT).rolling(window=CMF_SHORT, min_periods=CMF_SHORT-2).mean()
    amt_share_change = amt_share - amt_share_prev
    
    # 价格动量 (辅助确认)
    momentum = close.pct_change(CMF_SHORT)
    
    return {
        'cmf_short': cmf_short,
        'cmf_long': cmf_long,
        'anb': anb_ma,
        'amt_share_change': amt_share_change,
        'momentum': momentum,
        'ret': ret,
        'amount_ratio': amount_ratio,
    }


def generate_signals(factors):
    """
    生成机会/风险信号
    ==================
    机会信号 (买入):
      - CMF短线上穿0 (资金从流出转流入)
      - 或 CMF短线>0 且CMF长线上穿0 (资金流入趋势确认)
      - 且 成交额占比上升 (资金关注度增加)
    
    风险信号 (卖出):
      - CMF短线下穿0 (资金从流入转流出)
      - 或 CMF短线<0 且CMF长线下穿0 (资金流出趋势确认)
    
    观望 (持有):
      - CMF短线和长线都>0 (资金持续流入, 持有)
    
    预警 (减仓):
      - CMF短线<0 但长线>0 (短期资金流出, 趋势未破)
    """
    cmf_s = factors['cmf_short']
    cmf_l = factors['cmf_long']
    amt_change = factors['amt_share_change']
    momentum = factors['momentum']
    
    industries = cmf_s.columns
    dates = cmf_s.index
    
    # 信号矩阵: 1=机会, -1=风险, 0=观望, -0.5=预警
    signals = pd.DataFrame(0, index=dates, columns=industries, dtype=float)
    
    # 信号强度 (综合评分)
    signal_strength = pd.DataFrame(0.0, index=dates, columns=industries)
    
    for ind in industries:
        cs = cmf_s[ind]
        cl = cmf_l[ind]
        ac = amt_change[ind]
        mom = momentum[ind]
        
        # CMF交叉信号
        cs_prev = cs.shift(1)
        cl_prev = cl.shift(1)
        
        # 机会: CMF短线上穿0 (资金流入启动)
        opp_cross = (cs_prev < SIGNAL_THRESHOLD) & (cs > SIGNAL_THRESHOLD)
        
        # 机会强化: CMF短线>0 且 长线上穿0 (趋势确认)
        opp_confirm = (cs > SIGNAL_THRESHOLD) & (cl_prev < SIGNAL_THRESHOLD) & (cl > SIGNAL_THRESHOLD)
        
        # 机会持续: CMF短线>0 且 长线>0 (资金持续流入)
        opp_hold = (cs > SIGNAL_THRESHOLD) & (cl > SIGNAL_THRESHOLD)
        
        # 风险: CMF短线下穿0 (资金流出启动)
        risk_cross = (cs_prev > SIGNAL_THRESHOLD) & (cs < SIGNAL_THRESHOLD)
        
        # 风险强化: CMF短线<0 且 长线下穿0 (趋势确认)
        risk_confirm = (cs < SIGNAL_THRESHOLD) & (cl_prev > SIGNAL_THRESHOLD) & (cl < SIGNAL_THRESHOLD)
        
        # 预警: CMF短线<0 但 长线>0 (短期资金流出, 趋势未破)
        warning = (cs < SIGNAL_THRESHOLD) & (cl > SIGNAL_THRESHOLD)
        
        # 持续风险: CMF短线<0 且 长线<0
        risk_hold = (cs < SIGNAL_THRESHOLD) & (cl < SIGNAL_THRESHOLD)
        
        # 赋值信号
        signals[ind] = np.where(opp_cross | opp_confirm, 1.0, 
                       np.where(risk_cross | risk_confirm, -1.0,
                       np.where(warning, -0.5,
                       np.where(opp_hold, 0.5,  # 持有信号
                       np.where(risk_hold, -1.0, 0.0)))))
        
        # 信号强度: 综合CMF水平 + 成交额变化 + 动量
        signal_strength[ind] = (cs * 0.4 +  # CMF短期水平 (40%权重)
                                cl * 0.3 +  # CMF长期水平 (30%权重)
                                ac * 10 * 0.2 +  # 成交额占比变化 (20%权重)
                                mom * 10 * 0.1)  # 动量 (10%权重)
    
    return signals, signal_strength


def run_signal_backtest(factors, signals, signal_strength, etf_data_dict, ret_panel):
    """
    信号驱动回测
    ============
    每日检查信号:
      - 机会信号 -> 买入(有空位时)
      - 风险信号 -> 卖出
      - 预警信号 -> 标记观察
    最多持有MAX_HOLDINGS个行业, 等权配置
    """
    print("\n运行信号驱动回测...")
    
    trade_dates = [d for d in signals.index if d >= pd.Timestamp(TRADE_START)]
    
    # 持仓状态: {行业: 入场日期}
    holdings = {}
    # 每日持仓和收益记录
    daily_records = []
    # 信号记录
    signal_records = []
    
    for i, d in enumerate(trade_dates):
        if i == 0:
            continue
        
        prev_d = trade_dates[i-1]
        
        # 1. 检查已有持仓的风险信号 -> 先卖出
        to_sell = []
        for ind in list(holdings.keys()):
            sig = signals.loc[d, ind] if d in signals.index and ind in signals.columns else 0
            if sig <= -1.0:  # 风险信号
                to_sell.append(ind)
                signal_records.append({
                    'date': d, 'industry': ind, 'signal': '风险-清仓',
                    'cmf_short': factors['cmf_short'].loc[d, ind] if d in factors['cmf_short'].index else None,
                    'cmf_long': factors['cmf_long'].loc[d, ind] if d in factors['cmf_long'].index else None,
                })
            elif sig == -0.5 and len(holdings) > 3:  # 预警+持仓过多时减仓
                to_sell.append(ind)
                signal_records.append({
                    'date': d, 'industry': ind, 'signal': '预警-减仓',
                    'cmf_short': factors['cmf_short'].loc[d, ind] if d in factors['cmf_short'].index else None,
                    'cmf_long': factors['cmf_long'].loc[d, ind] if d in factors['cmf_long'].index else None,
                })
        
        for ind in to_sell:
            del holdings[ind]
        
        # 2. 检查机会信号 -> 买入
        if len(holdings) < MAX_HOLDINGS:
            # 找出有买入信号且不在持仓中的行业
            candidates = []
            for ind in signals.columns:
                if ind in holdings:
                    continue
                sig = signals.loc[d, ind] if d in signals.index else 0
                if sig >= 1.0:  # 机会信号
                    strength = signal_strength.loc[d, ind] if d in signal_strength.index else 0
                    candidates.append((ind, strength))
            
            # 按信号强度排序, 优先买入最强的
            candidates.sort(key=lambda x: x[1], reverse=True)
            
            for ind, strength in candidates:
                if len(holdings) >= MAX_HOLDINGS:
                    break
                holdings[ind] = d
                signal_records.append({
                    'date': d, 'industry': ind, 'signal': '机会-建仓',
                    'cmf_short': factors['cmf_short'].loc[d, ind] if d in factors['cmf_short'].index else None,
                    'cmf_long': factors['cmf_long'].loc[d, ind] if d in factors['cmf_long'].index else None,
                    'strength': strength,
                })
        
        # 3. 也检查持续持有信号: 如果持仓中有0.5(持有信号)且有空位, 可以补仓
        if len(holdings) < MAX_HOLDINGS:
            hold_candidates = []
            for ind in signals.columns:
                if ind in holdings:
                    continue
                sig = signals.loc[d, ind] if d in signals.index else 0
                if sig == 0.5:  # 持有信号(资金持续流入)
                    strength = signal_strength.loc[d, ind] if d in signal_strength.index else 0
                    hold_candidates.append((ind, strength))
            
            hold_candidates.sort(key=lambda x: x[1], reverse=True)
            for ind, strength in hold_candidates:
                if len(holdings) >= MAX_HOLDINGS:
                    break
                # 只在信号强度足够时才补仓
                if strength > 0:
                    holdings[ind] = d
                    signal_records.append({
                        'date': d, 'industry': ind, 'signal': '机会-补仓',
                        'cmf_short': factors['cmf_short'].loc[d, ind] if d in factors['cmf_short'].index else None,
                        'cmf_long': factors['cmf_long'].loc[d, ind] if d in factors['cmf_long'].index else None,
                        'strength': strength,
                    })
        
        # 4. 计算当日收益
        if holdings:
            # 行业指数收益
            ind_ret = np.mean([ret_panel.loc[d, ind] if d in ret_panel.index and ind in ret_panel.columns else 0 
                              for ind in holdings])
            
            # ETF收益
            etf_ret = 0
            valid = 0
            for ind in holdings:
                if ind in INDUSTRY_ETF_MAP:
                    code = INDUSTRY_ETF_MAP[ind][0]
                    if code in etf_data_dict:
                        etf_df = etf_data_dict[code]
                        if d in etf_df['date'].values:
                            r = etf_df.set_index('date').loc[d, 'ret']
                            if not np.isnan(r):
                                etf_ret += r
                                valid += 1
            etf_ret = etf_ret / valid if valid > 0 else 0
        else:
            ind_ret = 0
            etf_ret = 0
        
        bench_ret = ret_panel.loc[d].mean() if d in ret_panel.index else 0
        
        daily_records.append({
            'date': d,
            'return': ind_ret,
            'etf_return': etf_ret,
            'bench_return': bench_ret,
            'n_holdings': len(holdings),
            'holdings': list(holdings.keys()),
        })
    
    return pd.DataFrame(daily_records).set_index('date'), signal_records


def run_weekly_backtest(factors, signals, signal_strength, etf_data_dict, ret_panel):
    """
    周度调仓版: 每周最后一个交易日检查信号, 调仓
    保持研报的周度节奏, 但用信号驱动而非排名
    """
    print("\n运行周度信号回测...")
    
    trade_dates = [d for d in signals.index if d >= pd.Timestamp(TRADE_START)]
    
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
        
        # 1. 风险信号: 检查当前持仓, 有风险信号的先卖出
        new_holdings = []
        for ind in holdings:
            sig = signals.loc[rd, ind] if ind in signals.columns else 0
            if sig >= -0.5:  # 没有明确风险信号, 继续持有
                new_holdings.append(ind)
            else:
                signal_records.append({
                    'date': rd, 'industry': ind, 'signal': '风险-清仓',
                    'cmf_short': factors['cmf_short'].loc[rd, ind],
                    'cmf_long': factors['cmf_long'].loc[rd, ind],
                })
        holdings = new_holdings
        
        # 2. 机会信号: 找有买入信号的行业
        candidates = []
        for ind in signals.columns:
            if ind in holdings:
                continue
            sig = signals.loc[rd, ind] if ind in signals.columns else 0
            if sig >= 0.5:  # 机会或持有信号
                strength = signal_strength.loc[rd, ind]
                cs = factors['cmf_short'].loc[rd, ind]
                cl = factors['cmf_long'].loc[rd, ind]
                candidates.append((ind, strength, sig, cs, cl))
        
        # 按信号强度排序
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # 补充到MAX_HOLDINGS
        for ind, strength, sig, cs, cl in candidates:
            if len(holdings) >= MAX_HOLDINGS:
                break
            holdings.append(ind)
            sig_label = '机会-建仓' if sig >= 1.0 else '机会-补仓'
            signal_records.append({
                'date': rd, 'industry': ind, 'signal': sig_label,
                'cmf_short': cs, 'cmf_long': cl, 'strength': strength,
            })
        
        # 3. 如果持仓为空且没有信号, 用CMF最高的行业兜底
        if not holdings:
            cs_row = factors['cmf_short'].loc[rd].dropna()
            if len(cs_row) > 0:
                top = cs_row.nlargest(min(3, len(cs_row)))
                for ind in top.index:
                    holdings.append(ind)
                    signal_records.append({
                        'date': rd, 'industry': ind, 'signal': 'CMF最高-兜底',
                        'cmf_short': top[ind], 'cmf_long': factors['cmf_long'].loc[rd, ind],
                    })
        
        # 4. 计算周度收益
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
    
    # 月度胜率
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
    print("V3: 行业资金流向信号策略 (正向跟钱走)")
    print("=" * 70)
    print(f"CMF短期={CMF_SHORT}日, CMF长期={CMF_LONG}日, 最多持有{MAX_HOLDINGS}个行业")
    print(f"回测区间: {TRADE_START} ~ {BT_END}")
    
    # Step 1: 获取数据
    print("\n[1] 获取行业和ETF数据...")
    industry_data_dict = {}
    etf_data_dict = {}
    
    for i, (name, (code, etf_name)) in enumerate(INDUSTRY_ETF_MAP.items()):
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
    
    panels = build_panels(industry_data_dict)
    ret_panel = panels['ret']
    
    # Step 2: 计算CMF和信号
    print("\n[2] 计算资金流指标和信号...")
    factors = compute_cmf(panels)
    signals, signal_strength = generate_signals(factors)
    
    # 统计信号
    trade_dates = [d for d in signals.index if d >= pd.Timestamp(TRADE_START)]
    trade_signals = signals.loc[trade_dates]
    
    opp_count = (trade_signals >= 1.0).sum().sum()
    risk_count = (trade_signals <= -1.0).sum().sum()
    warning_count = (trade_signals == -0.5).sum().sum()
    hold_count = (trade_signals == 0.5).sum().sum()
    
    print(f"  机会信号(建仓): {opp_count} 次")
    print(f"  风险信号(清仓): {risk_count} 次")
    print(f"  预警信号(减仓): {warning_count} 次")
    print(f"  持有信号: {hold_count} 次")
    
    # IC分析 (正向)
    print("\n[3] IC分析 (正向方向)...")
    for fname, fdata in [('CMF短期', factors['cmf_short']), ('CMF长期', factors['cmf_long']), 
                          ('主动净买入', factors['anb']), ('信号强度', signal_strength)]:
        common_dates = sorted(set(fdata.dropna().index) & set(ret_panel.index))
        ics = []
        for i, d in enumerate(common_dates):
            if i + 5 >= len(common_dates):
                break
            future_d = common_dates[i + 5]
            f_row = fdata.loc[d].dropna()
            r_row = ret_panel.loc[future_d].dropna()
            common = f_row.index.intersection(r_row.index)
            if len(common) < 5:
                continue
            ic = f_row[common].rank().corr(r_row[common].rank())
            if not np.isnan(ic):
                ics.append(ic)
        mean_ic = np.mean(ics) if ics else 0
        print(f"  {fname}: IC={mean_ic:.4f}, N={len(ics)}")
    
    # Step 4: 回测
    print("\n[4] 运行回测...")
    
    # 周度信号回测
    weekly_df, signal_records = run_weekly_backtest(
        factors, signals, signal_strength, etf_data_dict, ret_panel
    )
    
    # 每日信号回测
    daily_df, daily_signals = run_signal_backtest(
        factors, signals, signal_strength, etf_data_dict, ret_panel
    )
    
    # Step 5: 计算指标
    print("\n[5] 回测结果...")
    
    metrics = []
    # 周度-行业指数
    m = calc_metrics(weekly_df['return'], "周度信号(行业指数)")
    metrics.append(m)
    # 周度-ETF实际
    m = calc_metrics(weekly_df['etf_return'], "周度信号(ETF实际)")
    metrics.append(m)
    # 每日-行业指数
    m = calc_metrics(daily_df['return'], "每日信号(行业指数)")
    metrics.append(m)
    # 每日-ETF实际
    m = calc_metrics(daily_df['etf_return'], "每日信号(ETF实际)")
    metrics.append(m)
    # 基准
    bench_returns = weekly_df['bench_return']
    m = calc_metrics(bench_returns, "等权基准")
    metrics.append(m)
    
    # 打印
    print(f"\n{'策略':<25} {'累计收益':>10} {'年化':>10} {'夏普':>8} {'最大回撤':>10} {'周胜率':>8} {'月胜率':>8} {'Calmar':>8}")
    print("-" * 95)
    for m in metrics:
        print(f"  {m['name']:<23} {m['total_return']:>+9.2f}% {m['annualized_return']:>+9.2f}% {m['sharpe']:>8.3f} {m['max_drawdown']:>+9.2f}% {m['win_rate_weekly']:>7.1f}% {m['win_rate_monthly']:>7.1f}% {m['calmar']:>8.3f}")
    
    # 信号准确率分析
    print(f"\n[6] 信号准确率分析...")
    sig_df = pd.DataFrame(signal_records)
    if len(sig_df) > 0:
        # 机会信号准确率: 信号后1周收益>0的比例
        opp_sigs = sig_df[sig_df['signal'].str.contains('机会')]
        risk_sigs = sig_df[sig_df['signal'].str.contains('风险')]
        
        opp_correct = 0
        opp_total = 0
        for _, row in opp_sigs.iterrows():
            sig_date = row['date']
            ind = row['industry']
            # 找信号日后1周的收益
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
        print(f"  总信号: {len(sig_df)}次")
    
    # 持仓历史
    print(f"\n{'='*70}")
    print("持仓历史 (周度信号策略)")
    print(f"{'='*70}")
    for _, row in weekly_df.iterrows():
        date_str = row.name.strftime('%Y-%m-%d')
        n = row['n_holdings']
        h = row['holdings']
        r = row['etf_return'] * 100
        print(f"  {date_str} [{n}只] {h}  周收益={r:+.2f}%")
    
    # 信号记录
    print(f"\n{'='*70}")
    print("信号记录")
    print(f"{'='*70}")
    for s in signal_records:
        date_str = s['date'].strftime('%Y-%m-%d')
        ind = s['industry']
        sig = s['signal']
        cs = s.get('cmf_short', 0)
        cl = s.get('cmf_long', 0)
        if cs is not None and cl is not None:
            etf_name = INDUSTRY_ETF_MAP.get(ind, ('',''))[1]
            print(f"  {date_str} {sig:12s} {ind}({etf_name})  CMF短={cs:.4f} CMF长={cl:.4f}")
    
    # Step 7: 保存结果
    print(f"\n[7] 保存结果...")
    
    # 净值
    nav_data = {}
    nav_data['周度信号(行业)'] = (1 + weekly_df['return']).cumprod()
    nav_data['周度信号(ETF)'] = (1 + weekly_df['etf_return']).cumprod()
    nav_data['每日信号(行业)'] = (1 + daily_df['return']).cumprod()
    nav_data['每日信号(ETF)'] = (1 + daily_df['etf_return']).cumprod()
    nav_data['等权基准'] = (1 + weekly_df['bench_return']).cumprod()
    nav_df = pd.DataFrame(nav_data)
    nav_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v3_nav.csv"))
    
    # 周度收益
    weekly_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v3_weekly_returns.csv"))
    
    # 信号记录
    sig_save = []
    for s in signal_records:
        s_copy = {k: (v.strftime('%Y-%m-%d') if isinstance(v, pd.Timestamp) else v) for k, v in s.items()}
        s_copy['etf_name'] = INDUSTRY_ETF_MAP.get(s['industry'], ('',''))[1]
        sig_save.append(s_copy)
    with open(os.path.join(OUTPUT_DIR, "backtest_v3_signals.json"), 'w', encoding='utf-8') as f:
        json.dump(sig_save, f, ensure_ascii=False, indent=2)
    
    # 指标
    metrics_save = []
    for m in metrics:
        m_save = {k: v for k, v in m.items()}
        m_save['total_return'] = f"{m['total_return']:.2f}%"
        m_save['annualized_return'] = f"{m['annualized_return']:.2f}%"
        m_save['annualized_vol'] = f"{m['annualized_vol']:.2f}%"
        m_save['sharpe'] = f"{m['sharpe']:.3f}"
        m_save['max_drawdown'] = f"{m['max_drawdown']:.2f}%"
        m_save['win_rate_weekly'] = f"{m['win_rate_weekly']:.1f}%"
        m_save['win_rate_monthly'] = f"{m['win_rate_monthly']:.1f}%"
        m_save['calmar'] = f"{m['calmar']:.3f}"
        metrics_save.append(m_save)
    with open(os.path.join(OUTPUT_DIR, "backtest_v3_metrics.json"), 'w', encoding='utf-8') as f:
        json.dump(metrics_save, f, ensure_ascii=False, indent=2)
    
    print(f"  结果已保存")
    
    return nav_df, metrics, signal_records, weekly_df


if __name__ == "__main__":
    results = main()
