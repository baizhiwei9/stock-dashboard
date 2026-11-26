#!/usr/bin/env python3
"""
策略诊断与优化
==============
1. IC分析: 检验各因子的预测能力
2. 参数扫描: W/K/调仓频率
3. 方向检验: 因子是否反向
4. ETF跟踪误差诊断
5. 动量过滤/趋势过滤
6. 因子加权优化
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
    """构建面板数据"""
    all_dates = None
    for name, df in industry_data_dict.items():
        dates = df['date'].tolist()
        if all_dates is None:
            all_dates = dates
        else:
            all_dates = sorted(set(all_dates) & set(dates))
    
    panels = {}
    for field in ['close','high','low','amount','ret']:
        panels[field] = pd.DataFrame(index=all_dates)
    
    for name, df in industry_data_dict.items():
        df_idx = df.set_index('date')
        for field in ['close','high','low','amount','ret']:
            panels[field][name] = df_idx[field]
    
    return panels


def compute_factors(panels, W):
    """计算所有因子"""
    close = panels['close']
    high = panels['high']
    low = panels['low']
    amount = panels['amount']
    ret = panels['ret']
    
    # CLV
    clv = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for col in close.columns:
        rv = high[col] - low[col]
        valid = rv > 0
        clv.loc[valid, col] = (2*close.loc[valid,col] - high.loc[valid,col] - low.loc[valid,col]) / rv[valid]
        clv.loc[~valid, col] = 0.0
    
    active_buy = amount * (1 + clv) / 2
    active_sell = amount * (1 - clv) / 2
    
    total_buy = active_buy.sum(axis=1)
    total_sell = active_sell.sum(axis=1)
    total_amount = amount.sum(axis=1)
    
    buy_ratio = active_buy.div(total_buy, axis=0)
    sell_ratio = active_sell.div(total_sell, axis=0)
    amount_ratio = amount.div(total_amount, axis=0)
    
    # 因子1: 主动净买入
    f1 = (buy_ratio - sell_ratio) / amount_ratio.replace(0, np.nan)
    f1 = f1.rolling(window=W, min_periods=W-2).mean()
    
    # 因子2: 资金流集中度 (成交额占比变化)
    current_share = amount_ratio.rolling(window=W, min_periods=W-2).mean()
    prev_share = amount_ratio.shift(W).rolling(window=W, min_periods=W-2).mean()
    f2 = current_share - prev_share
    
    # 因子3: 资金流持续性 (正收益天数*成交额放大)
    pos_ret = (ret > 0).astype(float)
    pos_rate = pos_ret.rolling(window=W, min_periods=W-2).mean()
    vol_ratio = amount.rolling(window=W, min_periods=W-2).mean() / amount.rolling(window=W*3, min_periods=W*3-5).mean()
    f3 = pos_rate * vol_ratio
    
    # 因子4: 资金流分歧度 (-振幅*占比)
    intraday_range = (high - low) / close
    avg_range = intraday_range.rolling(window=W, min_periods=W-2).mean()
    f4 = -avg_range * current_share
    
    # 因子5: CMF偏离
    money_flow = clv * amount
    cmf = money_flow.rolling(window=W, min_periods=W-2).sum() / amount.rolling(window=W, min_periods=W-2).sum()
    f5 = cmf.sub(cmf.mean(axis=1), axis=0)
    
    # 额外因子: 动量
    f_mom = ret.rolling(window=W, min_periods=W-2).sum()  # W日累计收益
    f_mom2 = close / close.shift(W) - 1  # W日涨跌幅
    
    # 额外因子: 波动率 (低波更好)
    f_vol = -ret.rolling(window=W, min_periods=W-2).std()
    
    # 额外因子: 成交额趋势 (成交额MA5/MA20)
    f_amt_trend = amount.rolling(5, min_periods=3).mean() / amount.rolling(20, min_periods=10).mean()
    
    factors = {
        '主动净买入': f1,
        '资金流集中度': f2,
        '资金流持续性': f3,
        '资金流分歧度': f4,
        'CMF偏离': f5,
        '动量(W日累计)': f_mom,
        '动量(W日涨跌)': f_mom2,
        '低波动': f_vol,
        '成交额趋势': f_amt_trend,
    }
    
    return factors, ret


def compute_rank_ic(factor, ret, forward_days=5):
    """计算因子的Rank IC (因子截面排名 vs 未来N日收益截面排名的相关系数)"""
    common_dates = sorted(set(factor.dropna().index) & set(ret.index))
    ics = []
    for i, d in enumerate(common_dates):
        if i + forward_days >= len(common_dates):
            break
        future_d = common_dates[i + forward_days]
        f_row = factor.loc[d].dropna()
        r_row = ret.loc[future_d].dropna()
        common = f_row.index.intersection(r_row.index)
        if len(common) < 5:
            continue
        f_rank = f_row[common].rank()
        r_rank = r_row[common].rank()
        ic = f_rank.corr(r_rank, method='spearman')
        if not np.isnan(ic):
            ics.append(ic)
    return np.mean(ics) if ics else 0, np.std(ics) if ics else 0, len(ics)


def run_backtest(factor, ret_panel, trade_start, top_k=5, rebalance='weekly', direction='long'):
    """运行回测"""
    trade_dates = [d for d in factor.index if d >= pd.Timestamp(trade_start)]
    
    if rebalance == 'weekly':
        rebal_dates = []
        for d in trade_dates:
            if len(rebal_dates) == 0 or d.isocalendar()[1] != rebal_dates[-1].isocalendar()[1]:
                rebal_dates.append(d)
            else:
                rebal_dates[-1] = d
    elif rebalance == 'biweekly':
        rebal_dates = []
        for d in trade_dates:
            if len(rebal_dates) == 0 or d.isocalendar()[1] != rebal_dates[-1].isocalendar()[1]:
                rebal_dates.append(d)
            else:
                pass
        rebal_dates = rebal_dates[::2]
    elif rebalance == 'monthly':
        rebal_dates = []
        for d in trade_dates:
            if len(rebal_dates) == 0 or d.month != rebal_dates[-1].month:
                rebal_dates.append(d)
            else:
                rebal_dates[-1] = d
    
    strats = []
    benchs = []
    for i in range(len(rebal_dates) - 1):
        rd = rebal_dates[i]
        nd = rebal_dates[i + 1]
        
        if rd not in factor.index:
            continue
        f_row = factor.loc[rd].dropna()
        if len(f_row) < top_k:
            continue
        
        if direction == 'long':
            selected = f_row.nlargest(top_k).index.tolist()
        else:
            selected = f_row.nsmallest(top_k).index.tolist()
        
        period_ret = np.mean([ret_panel.loc[nd, ind] if nd in ret_panel.index and ind in ret_panel.columns else 0 
                             for ind in selected])
        bench_ret = ret_panel.loc[nd].mean() if nd in ret_panel.index else 0
        
        strats.append({'date': nd, 'return': period_ret, 'holdings': selected})
        benchs.append({'date': nd, 'return': bench_ret})
    
    strat_df = pd.DataFrame(strats).set_index('date')
    bench_df = pd.DataFrame(benchs).set_index('date')
    return strat_df, bench_df


def calc_metrics(returns, name="策略"):
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
    return {
        'name': name,
        'total_return': f"{total*100:.2f}%",
        'annualized_return': f"{ann*100:.2f}%",
        'annualized_vol': f"{vol*100:.2f}%",
        'sharpe': f"{sharpe:.3f}",
        'max_drawdown': f"{max_dd*100:.2f}%",
        'win_rate': f"{win*100:.1f}%",
        'calmar': f"{calmar:.3f}",
        'n_periods': len(returns),
    }


def main():
    print("=" * 70)
    print("策略诊断与优化")
    print("=" * 70)
    
    # Step 1: 获取数据
    print("\n[1] 获取行业数据...")
    industry_data_dict = {}
    for i, (name, (code, etf_name)) in enumerate(INDUSTRY_ETF_MAP.items()):
        try:
            df = get_industry_data(name)
            industry_data_dict[name] = df
        except:
            pass
    print(f"  获取 {len(industry_data_dict)} 个行业")
    
    panels = build_panels(industry_data_dict)
    ret_panel = panels['ret']
    
    # Step 2: IC分析
    print("\n[2] IC分析 (各因子对未来5日收益的预测能力)...")
    print(f"  {'因子':<20} {'Mean IC':>10} {'IC Std':>10} {'ICIR':>10} {'N':>6}")
    print("  " + "-" * 60)
    
    ic_results = {}
    for W in [10, 15, 20, 22, 30]:
        factors, _ = compute_factors(panels, W)
        for fname, fdata in factors.items():
            key = f"{fname}(W={W})"
            mean_ic, std_ic, n = compute_rank_ic(fdata, ret_panel, forward_days=5)
            icir = mean_ic / std_ic if std_ic > 0 else 0
            ic_results[key] = {'mean_ic': mean_ic, 'std_ic': std_ic, 'icir': icir, 'n': n, 'W': W, 'factor': fname}
            if W == 22:  # 只打印W=22的详细结果
                print(f"  {fname:<18} {mean_ic:>10.4f} {std_ic:>10.4f} {icir:>10.4f} {n:>6}")
    
    # 打印不同W的IC对比
    print("\n  不同W的主动净买入IC对比:")
    for W in [10, 15, 20, 22, 30]:
        key = f"主动净买入(W={W})"
        if key in ic_results:
            r = ic_results[key]
            print(f"    W={W}: IC={r['mean_ic']:.4f}, ICIR={r['icir']:.4f}")
    
    print("\n  不同W的动量(W日累计)IC对比:")
    for W in [10, 15, 20, 22, 30]:
        key = f"动量(W日累计)(W={W})"
        if key in ic_results:
            r = ic_results[key]
            print(f"    W={W}: IC={r['mean_ic']:.4f}, ICIR={r['icir']:.4f}")
    
    # Step 3: 参数扫描
    print("\n[3] 参数扫描 (W × K × 方向)...")
    results = []
    
    for W in [5, 10, 15, 20, 22, 30]:
        factors, _ = compute_factors(panels, W)
        
        # 综合因子 (只用IC>0的因子)
        pos_factors = {}
        for fname, fdata in factors.items():
            key = f"{fname}(W={W})"
            if key in ic_results and ic_results[key]['mean_ic'] > 0:
                pos_factors[fname] = fdata
        
        if not pos_factors:
            continue
        
        # 标准化合成
        composite = pd.DataFrame(0, index=ret_panel.index, columns=ret_panel.columns, dtype=float)
        for fname, fdata in pos_factors.items():
            mean = fdata.mean(axis=1)
            std = fdata.std(axis=1)
            z = fdata.sub(mean, axis=0).div(std.replace(0, np.nan), axis=0).fillna(0)
            composite += z
        composite = composite / len(pos_factors)
        
        for K in [3, 5, 8, 10]:
            for direction in ['long', 'short']:
                strat_df, bench_df = run_backtest(composite, ret_panel, TRADE_START, top_k=K, direction=direction)
                m = calc_metrics(strat_df['return'], f"综合(W={W},K={K},{direction})")
                bench_m = calc_metrics(bench_df['return'], "基准")
                excess = strat_df['return'].mean() - bench_df['return'].mean()
                m['excess_weekly'] = f"{excess*100:.2f}%"
                m['W'] = W
                m['K'] = K
                m['direction'] = direction
                m['n_factors'] = len(pos_factors)
                results.append(m)
    
    # 也测试单因子
    print("\n  单因子回测 (W=22, K=5, long):")
    factors_w22, _ = compute_factors(panels, 22)
    for fname, fdata in factors_w22.items():
        strat_df, bench_df = run_backtest(fdata, ret_panel, TRADE_START, top_k=5, direction='long')
        m = calc_metrics(strat_df['return'], f"{fname}(long)")
        excess = strat_df['return'].mean() - bench_df['return'].mean()
        print(f"    {fname:<18} 收益={m['total_return']:>8} 胜率={m['win_rate']:>6} 超额={excess*100:+.2f}%/周")
        
        # 也测试short
        strat_df_s, _ = run_backtest(fdata, ret_panel, TRADE_START, top_k=5, direction='short')
        m_s = calc_metrics(strat_df_s['return'], f"{fname}(short)")
        excess_s = strat_df_s['return'].mean() - bench_df['return'].mean()
        print(f"    {fname+'(反向)':<18} 收益={m_s['total_return']:>8} 胜率={m_s['win_rate']:>6} 超额={excess_s*100:+.2f}%/周")
    
    # Step 4: 输出Top10策略
    print("\n[4] Top10策略 (按累计收益排序):")
    results.sort(key=lambda x: float(x['total_return'].replace('%','').replace('+','')), reverse=True)
    print(f"  {'策略':<35} {'累计收益':>10} {'年化':>10} {'夏普':>8} {'回撤':>10} {'胜率':>8} {'超额/周':>10}")
    print("  " + "-" * 95)
    for r in results[:10]:
        print(f"  W={r['W']},K={r['K']},{r['direction']:<6}(n={r['n_factors']})  {r['total_return']:>10} {r['annualized_return']:>10} {r['sharpe']:>8} {r['max_drawdown']:>10} {r['win_rate']:>8} {r.get('excess_weekly','N/A'):>10}")
    
    # Step 5: 保存结果
    with open(os.path.join(OUTPUT_DIR, "optimization_results.json"), 'w', encoding='utf-8') as f:
        json.dump(results[:20], f, ensure_ascii=False, indent=2)
    
    # 保存IC结果
    ic_save = {k: {kk: vv for kk, vv in v.items() if kk != 'factor'} for k, v in ic_results.items()}
    with open(os.path.join(OUTPUT_DIR, "ic_results.json"), 'w', encoding='utf-8') as f:
        json.dump(ic_save, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存: optimization_results.json, ic_results.json")
    
    return results, ic_results


if __name__ == "__main__":
    results, ic_results = main()
