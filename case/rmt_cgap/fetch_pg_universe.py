#!/usr/bin/env python3
"""拉取物理几何分析的20标的截面数据 (5指数+15ETF) 2021-07 ~ 2026-08-19"""
import os, sys, time, warnings
warnings.filterwarnings('ignore')

PG_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fde225ad5ffePQAojl8RuNMiuQ'
sys.path.insert(0, os.path.join(PG_DIR, 'pylib'))
import akshare as ak
import numpy as np
import pandas as pd

START, END = '20210701', '20260819'

INDICES = {
    'HS300': ('sh000300', '沪深300'), 'CSI500': ('sh000905', '中证500'),
    'CSI1000': ('sh000852', '中证1000'), 'SCI50': ('sh000688', '科创50'),
    'GEM': ('sz399006', '创业板指'),
}
ETFS = {
    'METAL_ETF': ('sh512400', '有色金属ETF'), 'NE_ETF': ('sh516160', '新能源ETF'),
    'SEMI_ETF': ('sz159995', '半导体ETF'), 'CHEM_ETF': ('sz159870', '化工ETF'),
    'COM_ETF': ('sh515880', '通信ETF'), 'BROKER_ETF': ('sh515030', '券商ETF'),
    'REAL_ETF': ('sh512200', '房地产ETF'), '5G_ETF': ('sh515050', '5GETF'),
    'GAME_ETF': ('sz159869', '游戏ETF'), 'MEDIA_ETF': ('sh512980', '传媒ETF'),
    'AGRI_ETF': ('sz159825', '农业ETF'), 'FOOD_ETF': ('sh515170', '食品饮料ETF'),
    'BANK_ETF': ('sh512800', '银行ETF'), 'WINE_ETF': ('sh512690', '酒ETF'),
    'MED_ETF': ('sh512010', '医药ETF'),
}

frames = {}
for key, (sym, name) in INDICES.items():
    try:
        df = ak.stock_zh_index_daily_tx(symbol=sym)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')['close']
        df = df[(df.index >= '2021-06-01') & (df.index <= '2026-08-19')]
        frames[key] = df
        print(f'{key} {name}: {df.index.min().date()} ~ {df.index.max().date()} n={len(df)}')
        time.sleep(0.3)
    except Exception as e:
        print(f'{key} FAIL: {str(e)[:80]}')

for key, (sym, name) in ETFS.items():
    for attempt in range(3):
        try:
            df = ak.fund_etf_hist_sina(symbol=sym)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')['close']
            df = df[(df.index >= '2021-06-01') & (df.index <= '2026-08-19')]
            frames[key] = df
            print(f'{key} {name}: {df.index.min().date()} ~ {df.index.max().date()} n={len(df)}')
            break
        except Exception as e:
            print(f'{key} attempt{attempt} FAIL: {str(e)[:60]}')
            time.sleep(2)

panel = pd.DataFrame(frames).sort_index()
print('\n面板:', panel.shape)
print('缺失率:', (panel.isna().mean()*100).round(1).to_string())
panel.to_pickle(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pg_universe.pkl'))
print('已保存 pg_universe.pkl')