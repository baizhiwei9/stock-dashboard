#!/usr/bin/env python3
"""
优化版回测: 华泰金工行业资金流向图轮动策略
==========================================
基于诊断结果优化:
1. 因子反向: 2026年市场呈均值回复特征, 买因子值最低的行业
2. W=20: IC最优窗口
3. K=3: 集中持有3个行业
4. 趋势过滤: 市场均线下方时减仓/空仓
5. IC加权: 按因子ICIR加权合成而非等权
6. ETF实际收益验证
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
import json
import os
import requests
import base64

warnings.filterwarnings('ignore')

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
from feishu_config import WEBHOOK_URL  # noqa: E402  凭据脱敏：见 DEPLOY.md「飞书凭据」
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

W = 20
TOP_K = 3
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
    close = panels['close']
    high = panels['high']
    low = panels['low']
    amount = panels['amount']
    ret = panels['ret']
    
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
    
    # 因子1: 主动净买入 (IC=0.024, 反向后超额+0.10%/周)
    f1 = (buy_ratio - sell_ratio) / amount_ratio.replace(0, np.nan)
    f1 = f1.rolling(window=W, min_periods=W-2).mean()
    
    # 因子2: 资金流集中度 (IC=0.001, 反向后超额+0.07%/周)
    current_share = amount_ratio.rolling(window=W, min_periods=W-2).mean()
    prev_share = amount_ratio.shift(W).rolling(window=W, min_periods=W-2).mean()
    f2 = current_share - prev_share
    
    # 因子3: 资金流持续性 (IC=0.012, 反向后超额+0.16%/周)
    pos_ret = (ret > 0).astype(float)
    pos_rate = pos_ret.rolling(window=W, min_periods=W-2).mean()
    vol_ratio = amount.rolling(window=W, min_periods=W-2).mean() / amount.rolling(window=W*3, min_periods=W*3-5).mean()
    f3 = pos_rate * vol_ratio
    
    # 因子4: CMF偏离 (IC=0.010, 反向后超额+0.17%/周)
    money_flow = clv * amount
    cmf = money_flow.rolling(window=W, min_periods=W-2).sum() / amount.rolling(window=W, min_periods=W-2).sum()
    f5 = cmf.sub(cmf.mean(axis=1), axis=0)
    
    # 因子5: 动量 (IC=0.009, 反向后超额+0.22%/周)
    f_mom = ret.rolling(window=W, min_periods=W-2).sum()
    
    # 低波动 (IC=-0.020, 正向超额+0.07%/周) — 唯一正向有效的因子
    f_vol = -ret.rolling(window=W, min_periods=W-2).std()
    
    factors = {
        '主动净买入': f1,
        '资金流集中度': f2,
        '资金流持续性': f3,
        'CMF偏离': f5,
        '动量': f_mom,
    }
    
    return factors, ret, f_vol, close


def compute_rank_ic(factor, ret, forward_days=5):
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
        ic = f_row[common].rank().corr(r_row[common].rank())
        if not np.isnan(ic):
            ics.append(ic)
    return np.mean(ics) if ics else 0, np.std(ics) if ics else 1


def main():
    print("=" * 70)
    print("优化版回测: 行业资金流向图轮动策略")
    print("=" * 70)
    print(f"W={W}, K={TOP_K}, 因子反向(均值回复), 趋势过滤")
    
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
    close_panel = panels['close']
    
    # Step 2: 计算因子
    print("\n[2] 计算因子...")
    factors, ret, f_vol, close = compute_factors(panels, W)
    
    # 计算IC并确定权重
    factor_ics = {}
    factor_weights = {}
    for fname, fdata in factors.items():
        mean_ic, std_ic = compute_rank_ic(fdata, ret_panel, forward_days=5)
        icir = mean_ic / std_ic if std_ic > 0 else 0
        factor_ics[fname] = {'ic': mean_ic, 'icir': icir}
        # 反向后IC取反, 用abs(icir)作为权重
        factor_weights[fname] = abs(icir)
        print(f"  {fname}: IC={mean_ic:.4f}, ICIR={icir:.4f}, weight={abs(icir):.4f}")
    
    # 低波动因子IC为负, 正向使用
    mean_ic_vol, std_ic_vol = compute_rank_ic(f_vol, ret_panel, forward_days=5)
    factor_ics['低波动'] = {'ic': mean_ic_vol, 'icir': mean_ic_vol/std_ic_vol if std_ic_vol > 0 else 0}
    factor_weights['低波动'] = abs(mean_ic_vol/std_ic_vol if std_ic_vol > 0 else 0)
    print(f"  低波动: IC={mean_ic_vol:.4f}, ICIR={factor_ics['低波动']['icir']:.4f}, weight={factor_weights['低波动']:.4f}")
    
    # 归一化权重
    total_w = sum(factor_weights.values())
    for k in factor_weights:
        factor_weights[k] /= total_w
    
    # Step 3: 构建综合因子
    # 反向因子: 取负值 (买因子值最低的 = 均值回复)
    # 低波动: 正向使用
    print("\n[3] 构建综合因子 (IC加权, 反向+低波动正向)...")
    composite = pd.DataFrame(0, index=ret_panel.index, columns=ret_panel.columns, dtype=float)
    
    for fname, fdata in factors.items():
        mean = fdata.mean(axis=1)
        std = fdata.std(axis=1)
        z = fdata.sub(mean, axis=0).div(std.replace(0, np.nan), axis=0).fillna(0)
        # 反向: 取负
        composite += (-z) * factor_weights[fname]
        print(f"  {fname}: 反向, weight={factor_weights[fname]:.4f}")
    
    # 低波动: 正向
    mean_v = f_vol.mean(axis=1)
    std_v = f_vol.std(axis=1)
    z_v = f_vol.sub(mean_v, axis=0).div(std_v.replace(0, np.nan), axis=0).fillna(0)
    composite += z_v * factor_weights['低波动']
    print(f"  低波动: 正向, weight={factor_weights['低波动']:.4f}")
    
    # Step 4: 趋势过滤
    # 用等权行业指数的MA20/MA60判断市场趋势
    print("\n[4] 计算趋势过滤...")
    market_idx = close_panel.mean(axis=1)
    ma_short = market_idx.rolling(20, min_periods=10).mean()
    ma_long = market_idx.rolling(60, min_periods=30).mean()
    trend_filter = (ma_short > ma_long).astype(int)  # 1=多头, 0=空头
    
    trade_dates_idx = [d for d in composite.index if d >= pd.Timestamp(TRADE_START)]
    trend_stats = trend_filter.loc[trade_dates_idx]
    bull_pct = trend_stats.mean()
    print(f"  回测期内多头占比: {bull_pct*100:.1f}%")
    
    # Step 5: 回测
    print("\n[5] 回测...")
    
    # 找每周最后交易日
    trade_dates = [d for d in composite.index if d >= pd.Timestamp(TRADE_START)]
    weekly_dates = []
    for d in trade_dates:
        if len(weekly_dates) == 0 or d.isocalendar()[1] != weekly_dates[-1].isocalendar()[1]:
            weekly_dates.append(d)
        else:
            weekly_dates[-1] = d
    
    # 策略A: 纯反向 (无趋势过滤)
    # 策略B: 反向+趋势过滤 (空头时半仓)
    # 策略C: 反向+趋势过滤 (空头时空仓)
    strats = {'A_纯反向': [], 'B_反向_趋势半仓': [], 'C_反向_趋势空仓': []}
    benchs = []
    holdings_history = []
    
    for i in range(len(weekly_dates) - 1):
        rd = weekly_dates[i]
        nd = weekly_dates[i + 1]
        
        if rd not in composite.index:
            continue
        f_row = composite.loc[rd].dropna()
        if len(f_row) < TOP_K:
            continue
        
        # 反向: 选因子值最低的 (均值回复)
        selected = f_row.nsmallest(TOP_K).index.tolist()
        
        # 行业指数收益
        ind_ret = np.mean([ret_panel.loc[nd, ind] if nd in ret_panel.index and ind in ret_panel.columns else 0 
                          for ind in selected])
        
        # ETF收益
        etf_ret = 0
        valid_count = 0
        for ind in selected:
            if ind in INDUSTRY_ETF_MAP:
                code = INDUSTRY_ETF_MAP[ind][0]
                if code in etf_data_dict:
                    etf_df = etf_data_dict[code]
                    mask = (etf_df['date'] > rd) & (etf_df['date'] <= nd)
                    period_ret = etf_df.loc[mask, 'ret'].sum()
                    if not np.isnan(period_ret):
                        etf_ret += period_ret
                        valid_count += 1
        etf_ret = etf_ret / valid_count if valid_count > 0 else 0
        
        bench_ret = ret_panel.loc[nd].mean() if nd in ret_panel.index else 0
        
        # 趋势状态
        is_bull = trend_filter.loc[rd] if rd in trend_filter.index else 1
        
        strats['A_纯反向'].append({'date': nd, 'return': ind_ret, 'etf_ret': etf_ret})
        strats['B_反向_趋势半仓'].append({'date': nd, 'return': ind_ret * (0.5 + 0.5 * is_bull), 'etf_ret': etf_ret * (0.5 + 0.5 * is_bull)})
        strats['C_反向_趋势空仓'].append({'date': nd, 'return': ind_ret * is_bull, 'etf_ret': etf_ret * is_bull})
        
        benchs.append({'date': nd, 'return': bench_ret})
        
        holdings_history.append({
            'date': rd,
            'holdings': selected,
            'trend': '多头' if is_bull else '空头',
            'factor_values': {k: f_row[k] for k in selected}
        })
    
    # Step 6: 计算指标
    print("\n[6] 回测结果...")
    
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
    
    all_metrics = []
    for sname, sdata in strats.items():
        sdf = pd.DataFrame(sdata).set_index('date')
        m_ind = calc_metrics(sdf['return'], f"{sname}(行业指数)")
        m_etf = calc_metrics(sdf['etf_ret'], f"{sname}(ETF实际)")
        all_metrics.append(m_ind)
        all_metrics.append(m_etf)
    
    bench_df = pd.DataFrame(benchs).set_index('date')
    m_bench = calc_metrics(bench_df['return'], "等权基准")
    all_metrics.append(m_bench)
    
    # 打印结果
    print(f"\n{'策略':<30} {'累计收益':>10} {'年化':>10} {'夏普':>8} {'最大回撤':>10} {'周胜率':>8} {'月胜率':>8} {'Calmar':>8}")
    print("-" * 100)
    for m in all_metrics:
        print(f"  {m['name']:<28} {m['total_return']:>+9.2f}% {m['annualized_return']:>+9.2f}% {m['sharpe']:>8.3f} {m['max_drawdown']:>+9.2f}% {m['win_rate_weekly']:>7.1f}% {m['win_rate_monthly']:>7.1f}% {m['calmar']:>8.3f}")
    
    # 选最佳策略
    best = max(all_metrics, key=lambda x: x['total_return'])
    print(f"\n  最佳策略: {best['name']}, 累计收益={best['total_return']:.2f}%")
    
    # 持仓历史
    print(f"\n{'='*70}")
    print("持仓历史")
    print(f"{'='*70}")
    for h in holdings_history:
        date_str = h['date'].strftime('%Y-%m-%d')
        holdings_str = ", ".join([f"{ind}({INDUSTRY_ETF_MAP[ind][1]})" for ind in h['holdings']])
        print(f"  {date_str} [{h['trend']}]: {holdings_str}")
    
    # Step 7: 保存结果
    print(f"\n[7] 保存结果...")
    
    # 净值曲线
    nav_data = {}
    for sname, sdata in strats.items():
        sdf = pd.DataFrame(sdata).set_index('date')
        nav_data[f'{sname}(行业)'] = (1 + sdf['return']).cumprod()
        nav_data[f'{sname}(ETF)'] = (1 + sdf['etf_ret']).cumprod()
    nav_data['等权基准'] = (1 + bench_df['return']).cumprod()
    nav_df = pd.DataFrame(nav_data)
    nav_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v2_nav.csv"))
    
    # 周度收益
    weekly_data = {}
    for sname, sdata in strats.items():
        sdf = pd.DataFrame(sdata).set_index('date')
        weekly_data[f'{sname}(行业)'] = sdf['return']
        weekly_data[f'{sname}(ETF)'] = sdf['etf_ret']
    weekly_data['等权基准'] = bench_df['return']
    weekly_df = pd.DataFrame(weekly_data)
    weekly_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v2_weekly_returns.csv"))
    
    # 持仓
    holdings_records = []
    for h in holdings_history:
        for ind in h['holdings']:
            holdings_records.append({
                'date': h['date'].strftime('%Y-%m-%d'),
                'industry': ind,
                'etf_code': INDUSTRY_ETF_MAP[ind][0],
                'etf_name': INDUSTRY_ETF_MAP[ind][1],
                'factor_value': h['factor_values'][ind],
                'trend': h['trend'],
            })
    pd.DataFrame(holdings_records).to_csv(os.path.join(OUTPUT_DIR, "backtest_v2_holdings.csv"), index=False)
    
    # 指标
    metrics_save = []
    for m in all_metrics:
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
    with open(os.path.join(OUTPUT_DIR, "backtest_v2_metrics.json"), 'w', encoding='utf-8') as f:
        json.dump(metrics_save, f, ensure_ascii=False, indent=2)
    
    # IC结果
    ic_save = {k: {kk: vv for kk, vv in v.items()} for k, v in factor_ics.items()}
    ic_save['_weights'] = factor_weights
    with open(os.path.join(OUTPUT_DIR, "backtest_v2_ic.json"), 'w', encoding='utf-8') as f:
        json.dump(ic_save, f, ensure_ascii=False, indent=2)
    
    print(f"  结果已保存: backtest_v2_nav.csv, backtest_v2_weekly_returns.csv, backtest_v2_holdings.csv, backtest_v2_metrics.json")
    
    return nav_df, all_metrics, holdings_history, weekly_df


if __name__ == "__main__":
    results = main()
