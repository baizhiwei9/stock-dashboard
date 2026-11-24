# -*- coding: utf-8 -*-
"""RRG 数据加载 + 扩散指标预计算 (适配 data_rrg/)
产物:
  data_rrg/diffusion.csv    行业 x 交易日的自由流通市值加权扩散指标(MA20平滑后)
  data_rrg/warmup.pkl       股票后复权价 wide 表快照(可选, 避免重复 pivot)
"""
import os, glob
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data_rrg')


def load_industry_prices():
    """industry 指数 close wide: trade_date x 行业"""
    files = sorted(glob.glob(os.path.join(DATA, 'industry', '*.csv')))
    rows = []
    for fp in files:
        name = os.path.basename(fp)[:-4].split('_')[0]  # 行业名
        df = pd.read_csv(fp, dtype={'trade_date': str})
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        rows.append(df.set_index('trade_date')['close'].rename(name))
    wide = pd.concat(rows, axis=1).sort_index()
    return wide


def load_constituents():
    """constituents.csv -> dict 行业名 -> [code]"""
    df = pd.read_csv(os.path.join(DATA, 'constituents.csv'), dtype={'con_code': str})
    return df.groupby('industry')['con_code'].apply(list).to_dict()


def _load_day_files(dates=None):
    """载入全部 day/ 文件 -> (adj_close_wide, circ_mv_first)"""
    files = sorted(glob.glob(os.path.join(DATA, 'day', '*.csv')))
    if dates is not None:
        valid = {d for d in dates}
        files = [f for f in files if os.path.basename(f)[:8] in valid and os.path.basename(f)[4:6] in {'01', ''} or os.path.basename(f)[:8] in valid]
    # 只保留每月最后交易日 + 220 日前瞻需求: 扩散指标需逐日, 全载
    ac, mv = {}, {}
    for fp in files:
        d = os.path.basename(fp)[:8]
        df = pd.read_csv(fp)
        if df.empty:
            continue
        ac[d] = df.set_index('ts_code')['adj_close']
        mv[d] = df.set_index('ts_code')['circ_mv']
    ac_w = pd.DataFrame(ac).T.sort_index()      # date x code
    mv_w = pd.DataFrame(mv).T.sort_index()
    ac_w.index = pd.to_datetime(ac_w.index)
    mv_w.index = pd.to_datetime(mv_w.index)
    return ac_w, mv_w


def build_diffusion(lookback=220, ma_n=20):
    """扩散指标: 对每个行业 sum(circ_mv * I(adj_close_t > adj_close_{t-220}))/sum(circ_mv) -> MA20
    返回 wide: trade_date x 行业
    """
    cons = load_constituents()
    if not os.path.exists(os.path.join(DATA, 'day')):
        return pd.DataFrame()
    ac_w, mv_w = _load_day_files()
    # 全市场 up 矩阵: 逐列计算
    # 为控内存: 按行业逐个算
    out = {}
    for ind, codes in cons.items():
        codes = [c for c in codes if c in ac_w.columns]
        if len(codes) < 5:
            continue
        ac = ac_w[codes]
        mv = mv_w[codes]
        up = ac > ac.shift(lookback)
        num = (up * mv).sum(axis=1)
        den = mv.sum(axis=1)
        diff = num / den.replace(0, np.nan)
        diff = diff.rolling(ma_n, min_periods=ma_n).mean()
        out[ind] = diff
        print(f'diff {ind}: {len(codes)} stocks', flush=True)
    df = pd.DataFrame(out).sort_index()
    df.to_csv(os.path.join(DATA, 'diffusion.csv'))
    return df


if __name__ == '__main__':
    df = build_diffusion()
    print('diffusion shape:', df.shape)
    print(df.dropna().tail(3))