#!/usr/bin/env python3
"""
中证全A（000985）行情数据获取模块
数据源：akshare stock_zh_index_daily_tx（腾讯财经）
备选：stock_zh_index_hist_csindex（中证指数公司，含滚动市盈率等额外字段）

字段说明：
- stock_zh_index_daily_tx 返回: date, open, close, high, low, amount
- stock_zh_index_hist_csindex 返回: 日期, 开盘, 最高, 最低, 收盘, 涨跌, 涨跌幅, 成交量, 成交金额, 样本数量, 滚动市盈率
"""

import os
import sys
# 读取用户权限目录 py_deps 下的 akshare/tushare
_PD = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps"
if _PD not in sys.path and os.path.isdir(_PD):
    sys.path.insert(0, _PD)

import akshare as ak
import pandas as pd

# dataHub 统一取数（首选）；不可用时回退腾讯源
_HUB = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier"
_HUB_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps"
for _p in (_HUB, _HUB_DEPS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def _hub_index(codes, start, end):
    """dataHub 指数日线；失败返回 None 由调用方回退。"""
    try:
        from data_hub import edb
        return edb.get_dataset("CN.MARKET.INDEX.DAILY", codes=codes,
                               start_date=str(start), end_date=str(end))
    except Exception as exc:
        print(f"[数据] dataHub 指数日线不可用，回退腾讯源: {str(exc)[:90]}", flush=True)
        return None
import numpy as np


def fetch_csi_all_index(days=120, source='tx'):
    """
    获取中证全A（000985）日线行情数据

    Args:
        days: int, 获取最近多少个交易日的数据（默认120日，约6个月）
        source: str, 数据源 'tx'=腾讯, 'csindex'=中证指数公司

    Returns:
        dict: {
            'dates': pd.DatetimeIndex,
            'prices': np.array,      # 收盘价
            'opens': np.array,
            'highs': np.array,
            'lows': np.array,
            'volumes': np.array,     # 成交量（amount字段）
            'df': pd.DataFrame       # 原始DataFrame
        }
    """
    if source == 'tx':
        # 首选 dataHub（CN.MARKET.INDEX.DAILY，000985.CSI）；不可用回退腾讯源
        # 口径核对：腾讯源的 amount 实为成交量，对应 dataHub 的 vol（逐日比值 1.0000）
        df = None
        hub = _hub_index(['000985.CSI'],
                         (pd.Timestamp.now() - pd.Timedelta(days=days * 3)).strftime('%Y%m%d'),
                         pd.Timestamp.now().strftime('%Y%m%d'))
        if hub is not None and len(hub):
            df = pd.DataFrame({
                'date': pd.to_datetime(hub['trade_date'].astype(str)),
                'open': hub['open'].astype(float), 'close': hub['close'].astype(float),
                'high': hub['high'].astype(float), 'low': hub['low'].astype(float),
                'amount': hub['vol'].astype(float),
            })
        if df is None:
            df = ak.stock_zh_index_daily_tx(symbol='sh000985')
            df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        # 取最近 days 个交易日
        df = df.tail(days).reset_index(drop=True)

        result = {
            'dates': df['date'],
            'prices': df['close'].values.astype(float),
            'opens': df['open'].values.astype(float),
            'highs': df['high'].values.astype(float),
            'lows': df['low'].values.astype(float),
            'volumes': df['amount'].values.astype(float),  # 腾讯源amount为成交量
            'df': df
        }

    elif source == 'csindex':
        # 中证指数公司数据源 - 含滚动市盈率等额外字段
        end_date = pd.Timestamp.now().strftime('%Y%m%d')
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=days*2)).strftime('%Y%m%d')
        df = ak.stock_zh_index_hist_csindex(symbol='000985',
                                             start_date=start_date,
                                             end_date=end_date)
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期').reset_index(drop=True)
        df = df.tail(days).reset_index(drop=True)

        result = {
            'dates': df['日期'],
            'prices': df['收盘'].values.astype(float),
            'opens': df['开盘'].values.astype(float),
            'highs': df['最高'].values.astype(float),
            'lows': df['最低'].values.astype(float),
            'volumes': df['成交量'].values.astype(float),
            'turnover': df['成交金额'].values.astype(float),
            'pe_ttm': df['滚动市盈率'].values.astype(float),
            'df': df
        }
    else:
        raise ValueError(f"不支持的数据源: {source}, 可选 'tx' 或 'csindex'")

    print(f"[数据获取] 中证全A(000985) 数据源={source}")
    print(f"  交易日数: {len(result['dates'])}")
    print(f"  日期范围: {result['dates'].iloc[0].strftime('%Y-%m-%d')} ~ {result['dates'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"  最新收盘: {result['prices'][-1]:.2f}")

    return result


if __name__ == '__main__':
    # 测试数据获取
    data = fetch_csi_all_index(days=60, source='tx')
    print(f"\n价格序列前5个: {data['prices'][:5]}")
    print(f"价格序列后5个: {data['prices'][-5:]}")
    print(f"\n原始DataFrame:")
    print(data['df'].tail(10))
