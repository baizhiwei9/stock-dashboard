# -*- coding: utf-8 -*-
"""RRG(相对旋转图)框架下的行业和ETF轮动策略 —— 复现引擎
   文章: 《相对旋转图RRG框架下的行业和ETF轮动策略》西部证券 (2026-05-25)
   范围: 中信一级行业(剔除综合、综合金融, 共28个), 每月末调仓
   实现策略:
     1) RRG 1+2象限: 每月末等权配置处于1+2象限且离原点最远的6个行业
     2) 扩散指标: 每月末筛选自由流通市值加权扩散指标最高的6个行业等权
     3) 扩散指标+RRG: 选扩散最高6个行业后, 剔除RRG中处于三、四象限的行业
   指标口径:
     RS = 行业价格 / 基准价格(等权行业指数)
     RS-Ratio = 100 + 100*(RS/SMA(RS,220) - 1); RS-Mom = 100 + 100*(RS/SMA(RS,60) - 1)
     两者均再 MA20 平滑; 象限以 RS-Ratio>100 与 RS-Mom>100 划分
     距离原点 = sqrt((RS-Ratio-100)^2 + (RS-Mom-100)^2)
"""
import os, glob, re
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data_rrg')


# ================= 数据加载 =================
def load_industry_prices(start='20200101'):
    """载入 data_rrg/industry/*.csv 每个行业指数日线(close), 返回 wide: trade_date x industry"""
    files = sorted(glob.glob(os.path.join(DATA, 'industry', '*.csv')))
    rows = []
    for fp in files:
        name = os.path.basename(fp)[:-4]
        df = pd.read_csv(fp, dtype={'trade_date': str})
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        s = df.set_index('trade_date')['close'].rename(name)
        rows.append(s)
    wide = pd.concat(rows, axis=1).sort_index()
    return wide


def load_constituents():
    """载入 data_rrg/constituents.csv (industry, code)"""
    df = pd.read_csv(os.path.join(DATA, 'constituents.csv'), dtype={'code': str})
    return df


def load_stock_prices(start='20200101'):
    """载入成分股日线 close + circ_mv, 返回 dict[code] -> DataFrame(trade_date, close, circ_mv)"""
    out = {}
    files = glob.glob(os.path.join(DATA, 'stock', '*.csv'))
    for fp in files:
        code = os.path.basename(fp)[:-4].replace('_', '.')
        df = pd.read_csv(fp, dtype={'trade_date': str})
        if df.empty:
            continue
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df[df['trade_date'] >= pd.Timestamp(start)].set_index('trade_date')
        out[code] = df
    return out


# ================= RRG 计算 =================
def compute_rrg(prices, rs_n=220, rs_mom_n=60, ma_n=20):
    """日频 RRG: 返回 dict 行业名 -> DataFrame(rs_ratio, rs_mom, quadrant, dist)
    基准 = 等权行业指数净值
    """
    # 等权基准净值: 每日收益等权复利
    rets = prices.pct_change()
    bench_nav = (1 + rets.mean(axis=1, skipna=True)).cumprod()
    bench_price = bench_nav * 100.0  # 归一化起算

    out = {}
    for col in prices.columns:
        p = prices[col]
        rs = p / bench_price  # 相对强度
        rs_ratio_raw = 100 + 100 * (rs / rs.rolling(rs_n, min_periods=rs_n).mean() - 1)
        rs_mom_raw = 100 + 100 * (rs / rs.rolling(rs_mom_n, min_periods=rs_mom_n).mean() - 1)
        rs_ratio = rs_ratio_raw.rolling(ma_n, min_periods=ma_n).mean()
        rs_mom = rs_mom_raw.rolling(ma_n, min_periods=ma_n).mean()
        quad = pd.Series(np.nan, index=p.index)
        quad[(rs_ratio > 100) & (rs_mom > 100)] = 1
        quad[(rs_ratio <= 100) & (rs_mom > 100)] = 2
        quad[(rs_ratio <= 100) & (rs_mom <= 100)] = 3
        quad[(rs_ratio > 100) & (rs_mom <= 100)] = 4
        dist = np.sqrt((rs_ratio - 100) ** 2 + (rs_mom - 100) ** 2)
        out[col] = pd.DataFrame({'rs_ratio': rs_ratio, 'rs_mom': rs_mom,
                                 'quadrant': quad, 'dist': dist})
    return out


# ================= 扩散指标 =================
def compute_diffusion(stocks, stocks_by_industry, lookback=220, ma_n=20):
    """自由流通市值加权扩散指标, 日频:
       diff_ind(t) = sum(circ_mv_i * I(P_i(t)>P_i(t-lookback))) / sum(circ_mv_i)
       再 MA20 平滑
       stocks: dict code -> DataFrame(trade_date, close, circ_mv)
       stocks_by_industry: dict 行业名 -> [code,...]
    """
    result = {}
    for ind, codes in stocks_by_industry.items():
        df_list = []
        for code in codes:
            s = stocks.get(code)
            if s is None or s.empty or 'close' not in s.columns:
                continue
            close = s['close']
            up = close > close.shift(lookback)
            mv = s.get('circ_mv', pd.Series(np.nan, index=s.index))
            tmp = pd.DataFrame({'up': up, 'mv': mv}).dropna(subset=['up'])
            df_list.append(tmp)
        if not df_list:
            continue
        mat = pd.concat(df_list, axis=1, keys=range(len(df_list)))
        up_mat = mat.xs('up', axis=1, level=1)
        mv_mat = mat.xs('mv', axis=1, level=1)
        diff = (up_mat * mv_mat).sum(axis=1) / mv_mat.sum(axis=1)
        diff = diff.rolling(ma_n, min_periods=ma_n).mean()
        # circ_mv 单位: 万元; 比例无单位
        result[ind] = diff.rename(ind)
    df = pd.concat(result, axis=1).sort_index()
    return df


# ================= 调仓与回测 =================
def month_end_indices(prices):
    """月末(最后交易日) index 列表"""
    idx = prices.index
    me = idx.to_period('M').to_timestamp('M')
    # 每个月的最后一个交易日
    s = pd.Series(np.arange(len(idx)), index=idx)
    grp = s.groupby(s.index.to_period('M')).last()
    return grp.values


def week_end_indices(prices):
    """周度调仓点: 每周最后一个交易日, 即最近的周五交易日 (周频调仓日) index 列表"""
    idx = prices.index
    return np.flatnonzero(pd.Series(idx.dayofweek, index=idx).values == 4)


def portfolio_returns(prices, weight_series):
    """输入: 日收益矩阵 r(t, i); weigh 函数每月末给出权重向量(下一持有期生效)
    weight_series: Series of dict(行业->权重), index=月末日期
    收益对齐: 月末 T 收盘调仓, 收益从 T+1 起计(T日信号日收盘价可得, 用次日执行避免前视)
    """
    rets = prices.pct_change().fillna(0)
    holding = {}
    out = pd.Series(0.0, index=rets.index)
    me_idx = month_end_indices(prices)
    for k, i in enumerate(me_idx):
        t = rets.index[i]
        w = weight_series.get(t)
        if w is None:
            continue
        holding = w
        # 从该月末次日(或下一日)起持有, 至下一个月末当日
        nxt = i + 1
        end = me_idx[k + 1] + 1 if k + 1 < len(me_idx) else len(rets)
        seg = rets.iloc[nxt:end]
        if len(seg) == 0:
            continue
        seg_w = seg[list(holding.keys())] if holding else pd.DataFrame(0, index=seg.index, columns=rets.columns)
        wv = pd.Series([holding.get(c, 0.0) for c in seg_w.columns], index=seg_w.columns)
        out.iloc[nxt:end] = seg_w.values @ wv.values
    nav = (1 + out).cumprod()
    return out, nav


def backtest(prices, diffusion, rrg, start='20220101', end='20260822'):
    """三类策略回测, 返回 dict 策略名->(ret_series, nav_series, 信号明细)"""
    rets = prices.pct_change().fillna(0)
    prices = prices[prices.index >= pd.Timestamp(start)]
    me_idx = month_end_indices(prices)

    signals = {n: {} for n in ['RRG_12', '扩散', '扩散+RRG']}
    for k, i in enumerate(me_idx):
        t = prices.index[i]
        q = {c: rrg[c].loc[t] for c in prices.columns}
        diff_t = diffusion.loc[t] if t in diffusion.index else pd.Series(dtype=float)

        # 1) RRG 1+2象限 最远6
        cand = {c: v['dist'] for c, v in q.items() if v['quadrant'] in (1, 2) and pd.notna(v['dist'])}
        top6 = sorted(cand, key=cand.get, reverse=True)[:6]
        signals['RRG_12'][t] = {c: 1 / 6 for c in top6} if top6 else {}

        # 2) 扩散最高6
        topd = diff_t.dropna().sort_values(ascending=False)
        top6d = topd.index[:6].tolist()
        signals['扩散'][t] = {c: 1 / 6 for c in top6d} if top6d else {}

        # 3) 扩散+RRG: 先选扩散最高6, 剔除QRG三四象限
        keep = [c for c in top6d if q.get(c, {}).get('quadrant', 3) in (1, 2)]
        signals['扩散+RRG'][t] = {c: 1 / len(keep) for c in keep} if keep else {}

    results = {}
    for name, sig in signals.items():
        ws = pd.Series(sig)
        r, nav = portfolio_returns(prices, ws)
        results[name] = (r, nav, sig)
    return results


# ================= 绩效统计(与成长价值口径一致) =================
def perf_stats(ret, nav, start=None, end=None, periods_per_year=252):
    sub = nav.copy()
    if start is not None:
        sub = sub[sub.index >= pd.Timestamp(start)]
    if end is not None:
        sub = sub[sub.index <= pd.Timestamp(end)]
    if len(sub) < 2:
        return {}
    r = sub.pct_change().dropna()
    if len(r) == 0:
        return {}
    yrs = len(sub) / periods_per_year
    total_ret = sub.iloc[-1] / sub.iloc[0] - 1
    ann_ret = (1 + total_ret) ** (1 / yrs) - 1 if yrs > 0 else 0
    vol = r.std(ddof=1) * np.sqrt(periods_per_year)
    sharpe = (r.mean() * periods_per_year - 0) / (r.std(ddof=1) * np.sqrt(periods_per_year)) if r.std(ddof=1) > 0 else 0
    dd = sub / sub.cummax() - 1
    maxdd = dd.min()
    return {
        'total_ret': total_ret, 'ann_ret': ann_ret, 'ann_vol': vol,
        'sharpe': sharpe, 'maxdd': maxdd,
    }