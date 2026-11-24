#!/usr/bin/env python3
"""
华泰金工《行业资金流向图应用于行业轮动》策略回测
================================================
基于研报核心逻辑实现行业轮动回测:
1. 主动净买入因子 (使用CMF资金流代理Wind主动买卖数据)
2. 行业资金流向图4维度微观信息 (简化版: 基于超额收益和成交额的关联分析)
3. 综合因子 = 5因子截面标准化等权合成
4. 每周调仓, 选Top5行业, 等权配置
5. 使用行业ETF计算实际收益

回测区间: 2026-01-01 ~ 2026-08-14
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import os
import json

warnings.filterwarnings('ignore')

# ============================================================
# 1. 配置: 行业-ETF映射
# ============================================================
INDUSTRY_ETF_MAP = {
    # THS行业名: (ETF代码, ETF名称, THS行业代码)
    "半导体":       ("512480", "半导体ETF",   "881121"),
    "银行":         ("512800", "银行ETF",     "881155"),
    "证券":         ("512000", "券商ETF",     "881157"),
    "光伏设备":     ("515790", "光伏ETF",     "881279"),
    "军工装备":     ("512660", "军工ETF",     "881166"),
    "通信设备":     ("515880", "通信ETF",     "881129"),
    "计算机设备":   ("512720", "计算机ETF",   "881130"),
    "软件开发":     ("515230", "软件ETF",     "881272"),
    "化学制药":     ("512010", "医药ETF",     "881140"),
    "煤炭开采加工": ("515220", "煤炭ETF",     "881105"),
    "钢铁":         ("515210", "钢铁ETF",     "881112"),
    "工业金属":     ("512400", "有色ETF",     "881168"),
    "房地产":       ("512200", "房地产ETF",   "881153"),
    "汽车整车":     ("516110", "汽车ETF",     "881125"),
    "白酒":         ("512690", "酒ETF",       "881273"),
    "食品加工制造": ("515170", "食品饮料ETF", "881134"),
    "生物制品":     ("159992", "创新药ETF",   "881142"),
    "医疗器械":     ("159883", "医疗器械ETF", "881144"),
    "化学制品":     ("159870", "化工ETF",     "881109"),
    "风电设备":     ("516950", "风电ETF",     "881280"),
    "电力":         ("159611", "电力ETF",     "881145"),
    "消费电子":     ("159732", "消费电子ETF", "881124"),
    "电池":         ("159755", "电池ETF",     "881281"),
    "贵金属":       ("159933", "金ETF",       "881169"),
    "建筑材料":     ("159745", "建材ETF",     "881115"),
    "物流":         ("516910", "物流ETF",     "881152"),
    "养殖业":       ("159865", "养殖ETF",     "881102"),
    "通信服务":     ("515050", "5GETF",       "881162"),
    "IT服务":       ("515080", "AI智能ETF",   "881271"),
}

N_INDUSTRIES = len(INDUSTRY_ETF_MAP)
W = 22  # 移动平均窗长 (研报W=20-24, 取中间值)
TOP_K = 5  # 每周选Top5行业
BT_START = "2025-06-01"  # 因子计算起始(需6个月warmup)
BT_END = "2026-08-14"
TRADE_START = "2026-01-01"  # 实际交易回测起始

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_etf_data(etf_code):
    """从Sina获取ETF历史数据"""
    prefix = "sh" if etf_code.startswith("5") else "sz"
    df = ak.fund_etf_hist_sina(symbol=f"{prefix}{etf_code}")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['ret'] = df['close'].pct_change()
    return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'ret']]


def get_industry_data(industry_name):
    """从THS获取行业指数历史数据"""
    df = ak.stock_board_industry_index_ths(
        symbol=industry_name,
        start_date=BT_START.replace("-", ""),
        end_date=BT_END.replace("-", "")
    )
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期').reset_index(drop=True)
    df.rename(columns={
        '日期': 'date', '开盘价': 'open', '最高价': 'high',
        '最低价': 'low', '收盘价': 'close', '成交量': 'volume',
        '成交额': 'amount'
    }, inplace=True)
    df['ret'] = df['close'].pct_change()
    return df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'ret']]


def compute_active_net_buy_proxy(industry_data_dict):
    """
    主动净买入因子代理 (基于Chaikin Money Flow)
    ============================================
    研报原始方法:
      Qi = 主动买入金额占比 - 主动卖出金额占比
      因子 = Qi / Vi  (Vi=成交额占比), 做W日MA
    
    代理方法:
      CLV = (2*close - high - low) / (high - low)  ∈ [-1, 1]
      主动买入代理 = amount * (1 + CLV) / 2
      主动卖出代理 = amount * (1 - CLV) / 2
      然后按研报方法做跨行业标准化
    """
    print("\n[1/4] 计算主动净买入因子代理...")
    
    # 收集所有行业的日频数据
    all_dates = None
    for name, df in industry_data_dict.items():
        if all_dates is None:
            all_dates = df['date'].tolist()
        else:
            all_dates = sorted(set(all_dates) & set(df['date'].tolist()))
    
    # 构建面板数据
    close_panel = pd.DataFrame(index=all_dates)
    high_panel = pd.DataFrame(index=all_dates)
    low_panel = pd.DataFrame(index=all_dates)
    amount_panel = pd.DataFrame(index=all_dates)
    ret_panel = pd.DataFrame(index=all_dates)
    
    for name, df in industry_data_dict.items():
        df_idx = df.set_index('date')
        close_panel[name] = df_idx['close']
        high_panel[name] = df_idx['high']
        low_panel[name] = df_idx['low']
        amount_panel[name] = df_idx['amount']
        ret_panel[name] = df_idx['ret']
    
    # 计算CLV (Close Location Value)
    clv = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    for col in close_panel.columns:
        range_val = high_panel[col] - low_panel[col]
        # 避免除零: high==low时CLV=0
        valid = range_val > 0
        clv.loc[valid, col] = (2 * close_panel.loc[valid, col] - 
                                high_panel.loc[valid, col] - 
                                low_panel.loc[valid, col]) / range_val[valid]
        clv.loc[~valid, col] = 0.0
    
    # 主动买入/卖出代理
    active_buy = amount_panel * (1 + clv) / 2
    active_sell = amount_panel * (1 - clv) / 2
    
    # 跨行业标准化 (消除系统性误差)
    total_buy = active_buy.sum(axis=1)
    total_sell = active_sell.sum(axis=1)
    total_amount = amount_panel.sum(axis=1)
    
    buy_ratio = active_buy.div(total_buy, axis=0)
    sell_ratio = active_sell.div(total_sell, axis=0)
    amount_ratio = amount_panel.div(total_amount, axis=0)
    
    # 主动净买入因子 = (买入占比 - 卖出占比) / 成交额占比
    anb_factor = (buy_ratio - sell_ratio) / amount_ratio.replace(0, np.nan)
    
    # W日移动平均
    anb_factor_ma = anb_factor.rolling(window=W, min_periods=W-2).mean()
    
    print(f"  因子计算完成: {anb_factor_ma.shape}")
    print(f"  有效日期范围: {anb_factor_ma.dropna().index[0].date()} ~ {anb_factor_ma.dropna().index[-1].date()}")
    
    return anb_factor_ma, ret_panel, amount_panel, close_panel, high_panel, low_panel


def compute_capital_flow_dimensions(anb_factor, ret_panel, amount_panel, close_panel, high_panel, low_panel):
    """
    行业资金流向图4维度微观信息 (简化代理版)
    ========================================
    研报原始方法: 用曼宁公式反演行业间资金流Qi,j, 提取4个维度
      1) 行业间最大净流入  2) 净流入为正比例  3) 净流出之和  4) 市场外部净流入
    
    代理方法: 由于无法获取tick级主动买卖数据求解曼宁方程,
    使用4个与主动净买入因子低相关的微观结构指标作为代理:
    
      维度1 - 资金流集中度: 行业i的成交额占全市场比例的变化
              (成交额占比上升 = 资金向该行业集中)
      维度2 - 资金流持续性: 行业i的正收益天数占比 * 成交额放大倍数
              (持续正收益+放量 = 资金持续流入)
      维度3 - 资金流分歧度: 行业i日内振幅 * 成交额占比
              (高振幅+高占比 = 多空分歧大, 资金流出风险)
      维度4 - 外部资金净流入: 行业i的CMF偏离全市场均值
              (CMF显著高于市场 = 外部资金净流入)
    """
    print("\n[2/4] 计算行业资金流向图4维度微观信息(代理)...")
    
    all_dates = ret_panel.index
    industries = ret_panel.columns.tolist()
    
    # 成交额占比
    amount_ratio = amount_panel.div(amount_panel.sum(axis=1), axis=0)
    
    # 日内振幅 (high-low)/close
    if high_panel is not None and low_panel is not None:
        intraday_range = (high_panel - low_panel) / close_panel
    else:
        intraday_range = pd.DataFrame(0, index=close_panel.index, columns=close_panel.columns, dtype=float)
    
    # CLV (Close Location Value) 用于计算CMF
    clv = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    for col in close_panel.columns:
        range_val = high_panel[col] - low_panel[col]
        valid = range_val > 0
        clv.loc[valid, col] = (2 * close_panel.loc[valid, col] - 
                                high_panel.loc[valid, col] - 
                                low_panel.loc[valid, col]) / range_val[valid]
        clv.loc[~valid, col] = 0.0
    
    money_flow = clv * amount_panel  # 日资金流
    cmf = money_flow.rolling(window=W, min_periods=W-2).sum() / \
          amount_panel.rolling(window=W, min_periods=W-2).sum()
    
    # 维度1: 资金流集中度 = W日成交额占比变化
    dim1 = amount_ratio.rolling(window=W, min_periods=W-2).mean() - \
           amount_ratio.rolling(window=W*2, min_periods=W*2-2).mean().shift(W)
    # 简化: 当前W日均值 vs 前W日均值
    current_share = amount_ratio.rolling(window=W, min_periods=W-2).mean()
    prev_share = amount_ratio.shift(W).rolling(window=W, min_periods=W-2).mean()
    dim1 = current_share - prev_share
    
    # 维度2: 资金流持续性 = 正收益天数占比 * 成交额相对放大倍数
    pos_ret = (ret_panel > 0).astype(float)
    pos_rate = pos_ret.rolling(window=W, min_periods=W-2).mean()
    vol_ratio = amount_panel.rolling(window=W, min_periods=W-2).mean() / \
                amount_panel.rolling(window=W*3, min_periods=W*3-5).mean()
    dim2 = pos_rate * vol_ratio
    
    # 维度3: 资金流分歧度 = -日内振幅均值 * 成交额占比 (负号: 分歧越大越不利)
    avg_range = intraday_range.rolling(window=W, min_periods=W-2).mean()
    dim3 = -avg_range * current_share  # 负值, 越大(负得越少)越好
    
    # 维度4: 外部资金净流入 = 行业CMF - 市场平均CMF
    market_cmf = cmf.mean(axis=1)
    dim4 = cmf.sub(market_cmf, axis=0)
    
    print(f"  维度1(资金流集中度): mean={dim1.mean().mean():.6f}")
    print(f"  维度2(资金流持续性): mean={dim2.mean().mean():.6f}")
    print(f"  维度3(资金流分歧度): mean={dim3.mean().mean():.6f}")
    print(f"  维度4(外部资金净流入): mean={dim4.mean().mean():.6f}")
    
    # 检查与主动净买入因子的相关性
    for dim, name in [(dim1, "集中度"), (dim2, "持续性"), (dim3, "分歧度"), (dim4, "外部流入")]:
        corr = dim.corrwith(anb_factor, axis=1).mean()
        print(f"  {name} vs 主动净买入 相关性: {corr:.4f}")
    
    return dim1, dim2, dim3, dim4


def compute_composite_factor(anb_factor, max_inflow, pos_ratio, neg_outflow_sum, external_inflow):
    """
    综合因子 = 5因子截面标准化后等权合成
    """
    print("\n[3/4] 计算综合因子...")
    
    factors = [anb_factor, max_inflow, pos_ratio, neg_outflow_sum, external_inflow]
    factor_names = ["主动净买入", "最大净流入", "正流入比例", "净流出之和", "外部净流入"]
    
    # 截面标准化 (z-score), 处理NaN和常数列
    standardized = []
    for f, name in zip(factors, factor_names):
        mean = f.mean(axis=1)
        std = f.std(axis=1)
        z = f.sub(mean, axis=0).div(std.replace(0, np.nan), axis=0)
        z = z.fillna(0)  # 常数列或NaN填0
        standardized.append(z)
        print(f"  {name}: mean={z.mean().mean():.4f}, std={z.std().mean():.4f}")
    
    # 等权合成
    composite = sum(standardized) / len(standardized)
    
    return composite


def run_backtest(composite_factor, anb_factor, etf_data_dict, ret_panel):
    """
    回测: 每周调仓, 选Top5, 等权配置
    """
    print("\n[4/4] 运行回测...")
    
    # 获取交易日 (从2026年开始)
    trade_dates = [d for d in composite_factor.index if d >= pd.Timestamp(TRADE_START)]
    
    # 找每周最后一个交易日
    weekly_dates = []
    for d in trade_dates:
        # 每周五或本周最后交易日
        if len(weekly_dates) == 0 or d.isocalendar()[1] != weekly_dates[-1].isocalendar()[1]:
            weekly_dates.append(d)
        else:
            weekly_dates[-1] = d
    
    print(f"  调仓日数: {len(weekly_dates)}")
    
    # 回测
    strategy_returns = []
    benchmark_returns = []
    holdings_history = []
    anb_holdings_history = []
    
    for i in range(len(weekly_dates) - 1):
        rebalance_date = weekly_dates[i]
        next_date = weekly_dates[i + 1]
        
        # 获取调仓日的因子值
        if rebalance_date not in composite_factor.index:
            continue
        
        factor_row = composite_factor.loc[rebalance_date].dropna()
        anb_row = anb_factor.loc[rebalance_date].dropna() if rebalance_date in anb_factor.index else pd.Series()
        
        if len(factor_row) < TOP_K:
            continue
        
        # 选Top5
        top_industries = factor_row.nlargest(TOP_K).index.tolist()
        strategy_returns.append({
            'date': next_date,
            'return': np.mean([ret_panel.loc[next_date, ind] if next_date in ret_panel.index and ind in ret_panel.columns else 0 
                              for ind in top_industries]),
            'holdings': top_industries
        })
        holdings_history.append({
            'date': rebalance_date,
            'holdings': top_industries,
            'factor_values': {k: v for k, v in factor_row.nlargest(TOP_K).items()}
        })
        
        # 也记录单因子(主动净买入)的选股
        if len(anb_row) >= TOP_K:
            anb_top = anb_row.nlargest(TOP_K).index.tolist()
            anb_holdings_history.append({
                'date': rebalance_date,
                'holdings': anb_top,
                'return': np.mean([ret_panel.loc[next_date, ind] if next_date in ret_panel.index and ind in ret_panel.columns else 0 
                                  for ind in anb_top])
            })
        
        # 基准: 全部行业等权
        benchmark_returns.append({
            'date': next_date,
            'return': ret_panel.loc[next_date].mean() if next_date in ret_panel.index else 0
        })
    
    # 转为DataFrame
    strat_df = pd.DataFrame(strategy_returns).set_index('date')
    bench_df = pd.DataFrame(benchmark_returns).set_index('date')
    
    # 计算ETF实际收益 (用ETF数据)
    etf_returns = []
    for i in range(len(weekly_dates) - 1):
        rebalance_date = weekly_dates[i]
        next_date = weekly_dates[i + 1]
        
        if rebalance_date not in composite_factor.index:
            continue
        
        factor_row = composite_factor.loc[rebalance_date].dropna()
        if len(factor_row) < TOP_K:
            continue
        
        top_industries = factor_row.nlargest(TOP_K).index.tolist()
        
        # 映射到ETF并计算收益
        weekly_ret = 0
        valid_count = 0
        for ind in top_industries:
            if ind in INDUSTRY_ETF_MAP:
                etf_code = INDUSTRY_ETF_MAP[ind][0]
                if etf_code in etf_data_dict:
                    etf_df = etf_data_dict[etf_code]
                    mask = (etf_df['date'] > rebalance_date) & (etf_df['date'] <= next_date)
                    period_ret = etf_df.loc[mask, 'ret'].sum()
                    if not np.isnan(period_ret):
                        weekly_ret += period_ret
                        valid_count += 1
        
        if valid_count > 0:
            etf_returns.append({
                'date': next_date,
                'return': weekly_ret / valid_count,
                'holdings': top_industries
            })
    
    etf_df = pd.DataFrame(etf_returns).set_index('date') if etf_returns else pd.DataFrame()
    
    return strat_df, bench_df, etf_df, holdings_history, anb_holdings_history


def calculate_metrics(returns_series, name="策略"):
    """计算回测指标"""
    if len(returns_series) == 0:
        return {}
    
    cum_ret = (1 + returns_series).cumprod()
    total_ret = cum_ret.iloc[-1] - 1
    ann_ret = (1 + total_ret) ** (52 / len(returns_series)) - 1  # 年化(周频)
    
    ann_vol = returns_series.std() * np.sqrt(52)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    # 最大回撤
    peak = cum_ret.expanding().max()
    drawdown = (cum_ret - peak) / peak
    max_dd = drawdown.min()
    
    # 胜率
    win_rate = (returns_series > 0).mean()
    
    # Calmar比率
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    
    return {
        'name': name,
        'total_return': f"{total_ret*100:.2f}%",
        'annualized_return': f"{ann_ret*100:.2f}%",
        'annualized_vol': f"{ann_vol*100:.2f}%",
        'sharpe': f"{sharpe:.3f}",
        'max_drawdown': f"{max_dd*100:.2f}%",
        'win_rate': f"{win_rate*100:.1f}%",
        'calmar': f"{calmar:.3f}",
        'n_periods': len(returns_series),
    }


def main():
    print("=" * 70)
    print("华泰金工《行业资金流向图应用于行业轮动》策略回测")
    print("=" * 70)
    print(f"行业数: {N_INDUSTRIES}, 因子MA窗长W={W}, 每周选Top{TOP_K}")
    print(f"回测区间: {TRADE_START} ~ {BT_END}")
    print(f"因子warmup数据起始: {BT_START}")
    
    # ============================================================
    # Step 1: 获取行业指数数据
    # ============================================================
    print("\n" + "=" * 70)
    print("Step 1: 获取行业指数历史数据")
    print("=" * 70)
    
    industry_data_dict = {}
    cache_file = os.path.join(OUTPUT_DIR, "industry_data_cache.json")
    
    for i, (name, (etf_code, etf_name, ths_code)) in enumerate(INDUSTRY_ETF_MAP.items()):
        print(f"  [{i+1}/{N_INDUSTRIES}] {name} ({etf_name})...", end=" ")
        try:
            df = get_industry_data(name)
            industry_data_dict[name] = df
            print(f"✓ {len(df)}天")
        except Exception as e:
            print(f"✗ {e}")
    
    print(f"\n成功获取 {len(industry_data_dict)} 个行业数据")
    
    # ============================================================
    # Step 2: 获取ETF价格数据
    # ============================================================
    print("\n" + "=" * 70)
    print("Step 2: 获取行业ETF价格数据")
    print("=" * 70)
    
    etf_data_dict = {}
    for i, (name, (etf_code, etf_name, ths_code)) in enumerate(INDUSTRY_ETF_MAP.items()):
        print(f"  [{i+1}/{N_INDUSTRIES}] {etf_code} {etf_name}...", end=" ")
        try:
            df = get_etf_data(etf_code)
            etf_data_dict[etf_code] = df
            print(f"✓ {len(df)}天")
        except Exception as e:
            print(f"✗ {e}")
    
    print(f"\n成功获取 {len(etf_data_dict)} 个ETF数据")
    
    # ============================================================
    # Step 3: 计算因子
    # ============================================================
    print("\n" + "=" * 70)
    print("Step 3: 计算因子")
    print("=" * 70)
    
    # 3.1 主动净买入因子
    anb_factor, ret_panel, amount_panel, close_panel, high_panel, low_panel = compute_active_net_buy_proxy(industry_data_dict)
    
    # 3.2 资金流向图4维度
    dim1, dim2, dim3, dim4 = compute_capital_flow_dimensions(
        anb_factor, ret_panel, amount_panel, close_panel, high_panel, low_panel
    )
    
    # 3.3 综合因子
    composite = compute_composite_factor(anb_factor, dim1, dim2, dim3, dim4)
    
    # ============================================================
    # Step 4: 回测
    # ============================================================
    print("\n" + "=" * 70)
    print("Step 4: 回测")
    print("=" * 70)
    
    strat_df, bench_df, etf_df, holdings_hist, anb_hist = run_backtest(
        composite, anb_factor, etf_data_dict, ret_panel
    )
    
    # ============================================================
    # Step 5: 计算指标
    # ============================================================
    print("\n" + "=" * 70)
    print("Step 5: 回测结果")
    print("=" * 70)
    
    # 综合因子策略 (行业指数收益)
    metrics_composite = calculate_metrics(strat_df['return'], "综合因子(行业指数)")
    
    # 主动净买入单因子策略
    anb_df = pd.DataFrame(anb_hist).set_index('date') if anb_hist else pd.DataFrame()
    if 'return' in anb_df.columns:
        metrics_anb = calculate_metrics(anb_df['return'], "主动净买入单因子(行业指数)")
    else:
        metrics_anb = {'name': '主动净买入单因子', 'total_return': 'N/A'}
    
    # ETF实际收益
    if len(etf_df) > 0:
        metrics_etf = calculate_metrics(etf_df['return'], "综合因子(ETF实际)")
    else:
        metrics_etf = {'name': '综合因子(ETF实际)', 'total_return': 'N/A'}
    
    # 基准
    metrics_bench = calculate_metrics(bench_df['return'], "行业等权基准")
    
    # 打印结果
    print(f"\n{'指标':<25} {'综合因子(行业)':<18} {'综合因子(ETF)':<18} {'主动净买入单因子':<18} {'等权基准':<15}")
    print("-" * 94)
    
    metric_keys = ['total_return', 'annualized_return', 'annualized_vol', 'sharpe', 
                   'max_drawdown', 'win_rate', 'calmar', 'n_periods']
    metric_labels = ['累计收益', '年化收益', '年化波动', '夏普比率', '最大回撤', '胜率', 'Calmar', '调仓次数']
    
    for key, label in zip(metric_keys, metric_labels):
        v1 = metrics_composite.get(key, 'N/A')
        v2 = metrics_etf.get(key, 'N/A')
        v3 = metrics_anb.get(key, 'N/A')
        v4 = metrics_bench.get(key, 'N/A')
        print(f"  {label:<23} {v1:<18} {v2:<18} {v3:<18} {v4:<15}")
    
    # 打印持仓历史
    print("\n" + "=" * 70)
    print("持仓历史 (综合因子策略)")
    print("=" * 70)
    for h in holdings_hist:
        date_str = h['date'].strftime('%Y-%m-%d')
        holdings_str = ", ".join([f"{ind}({INDUSTRY_ETF_MAP[ind][1]})" for ind in h['holdings']])
        print(f"  {date_str}: {holdings_str}")
    
    # ============================================================
    # Step 6: 保存结果
    # ============================================================
    print("\n" + "=" * 70)
    print("Step 6: 保存结果")
    print("=" * 70)
    
    # 保存净值曲线
    strat_nav = (1 + strat_df['return']).cumprod()
    bench_nav = (1 + bench_df['return']).cumprod()
    etf_nav = (1 + etf_df['return']).cumprod() if len(etf_df) > 0 else pd.Series()
    
    nav_df = pd.DataFrame({
        '综合因子(行业指数)': strat_nav,
        '等权基准': bench_nav,
    })
    if len(etf_nav) > 0:
        nav_df['综合因子(ETF实际)'] = etf_nav
    
    nav_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_nav.csv"))
    print(f"  净值曲线已保存: backtest_nav.csv")
    
    # 保存持仓记录
    holdings_records = []
    for h in holdings_hist:
        for ind in h['holdings']:
            holdings_records.append({
                'date': h['date'].strftime('%Y-%m-%d'),
                'industry': ind,
                'etf_code': INDUSTRY_ETF_MAP[ind][0],
                'etf_name': INDUSTRY_ETF_MAP[ind][1],
                'factor_value': h['factor_values'][ind]
            })
    pd.DataFrame(holdings_records).to_csv(os.path.join(OUTPUT_DIR, "backtest_holdings.csv"), index=False)
    print(f"  持仓记录已保存: backtest_holdings.csv")
    
    # 保存指标
    all_metrics = [metrics_composite, metrics_etf, metrics_anb, metrics_bench]
    with open(os.path.join(OUTPUT_DIR, "backtest_metrics.json"), 'w', encoding='utf-8') as f:
        json.dump(all_metrics, f, ensure_ascii=False, indent=2)
    print(f"  回测指标已保存: backtest_metrics.json")
    
    # 保存周度收益
    weekly_returns = pd.DataFrame({
        '综合因子(行业指数)': strat_df['return'],
        '等权基准': bench_df['return'],
    })
    if len(etf_df) > 0:
        weekly_returns['综合因子(ETF实际)'] = etf_df['return']
    weekly_returns.to_csv(os.path.join(OUTPUT_DIR, "backtest_weekly_returns.csv"))
    print(f"  周度收益已保存: backtest_weekly_returns.csv")
    
    print("\n" + "=" * 70)
    print("回测完成!")
    print("=" * 70)
    
    return {
        'metrics': all_metrics,
        'nav': nav_df,
        'holdings': holdings_hist,
        'weekly_returns': weekly_returns,
    }


if __name__ == "__main__":
    results = main()
