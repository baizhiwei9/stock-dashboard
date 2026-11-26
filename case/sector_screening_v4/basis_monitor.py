#!/usr/bin/env python3
"""
股指期货升贴水监控系统 v4 — 四合约连续序列
=============================================
对 IF/IC/IM 各构建4条连续序列:
  CM(当月) / NM(次月) / CQ(当季) / NQ(下季)
每条独立计算 rank_252 + z_score_90 双轨信号
"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import tushare as ts

pro = ts.pro_api()

PRODUCTS = {
    'IF': {'index_code': '000300.SH', 'index_name': '沪深300'},
    'IC': {'index_code': '000905.SH', 'index_name': '中证500'},
    'IM': {'index_code': '000852.SH', 'index_name': '中证1000'},
}

QUARTER_MONTHS = [3, 6, 9, 12]
CONTRACT_TYPES = ['CM', 'NM', 'CQ', 'NQ']
TYPE_LABELS = {'CM': '当月', 'NM': '次月', 'CQ': '当季', 'NQ': '下季'}


# ─────────── 工具函数 ───────────

def get_third_friday(year, month):
    first = datetime(year, month, 1)
    first_fri = first + timedelta(days=(4 - first.weekday()) % 7)
    return (first_fri + timedelta(weeks=2)).strftime('%Y%m%d')


def identify_four_contracts(date_str):
    """
    给定交易日，返回 {'CM': ym, 'NM': ym, 'CQ': ym, 'NQ': ym}
    
    CFFEX 规则: 同时挂牌当月、下月、及随后两个季月共4个合约
    当月合约交割后，原"下月"升级为新"当月"，新挂一个远季月
    """
    td = datetime.strptime(date_str, '%Y%m%d')
    cur_y, cur_m = td.year, td.month
    cur_expiry = get_third_friday(cur_y, cur_m)
    
    # 判断当月合约是否已到期
    if date_str <= cur_expiry:
        # 当月合约还在
        cm_ym = f"{cur_y}{cur_m:02d}"
        # 下月
        nm_y = cur_y + (cur_m // 12)
        nm_m = cur_m % 12 + 1
        nm_ym = f"{nm_y}{nm_m:02d}"
    else:
        # 当月已交割，下月变为当月
        nm_y = cur_y + (cur_m // 12)
        nm_m = cur_m % 12 + 1
        cm_ym = f"{nm_y}{nm_m:02d}"
        # 新下月
        nm2_y = nm_y + (nm_m // 12)
        nm2_m = nm_m % 12 + 1
        nm_ym = f"{nm2_y}{nm2_m:02d}"
    
    # 当季: >= cm_ym 的最近季月
    def next_quarter_from(ym):
        y, m = int(ym[:4]), int(ym[4:])
        for offset in range(12):
            my = y + (m - 1 + offset) // 12
            mm = (m - 1 + offset) % 12 + 1
            if mm in QUARTER_MONTHS:
                qym = f"{my}{mm:02d}"
                qexp = get_third_friday(my, mm)
                if qexp >= date_str:
                    return qym
        return None
    
    cq_ym = next_quarter_from(cm_ym)
    
    # 下季: 当季之后的下一个季月
    if cq_ym:
        cq_y, cq_m = int(cq_ym[:4]), int(cq_ym[4:])
        for offset in range(1, 12):
            ny = cq_y + (cq_m - 1 + offset) // 12
            nm_ = (cq_m - 1 + offset) % 12 + 1
            if nm_ in QUARTER_MONTHS:
                nq_ym = f"{ny}{nm_:02d}"
                break
        else:
            nq_ym = None
    else:
        nq_ym = None
    
    return {'CM': cm_ym, 'NM': nm_ym, 'CQ': cq_ym, 'NQ': nq_ym}


def sym(product, ym): return f"{product}{ym[2:]}"
def tscode(product, ym): return f"{product}{ym[2:]}.CFX"


def fetch_futures_daily(tc, start_date=None, end_date=None):
    df = pro.fut_daily(ts_code=tc, start_date=start_date, end_date=end_date)
    if df is None or len(df) == 0: return pd.DataFrame()
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['trade_date'] = df['trade_date'].astype(str)
    return df


def fetch_index_daily(tc, start_date=None, end_date=None):
    df = pro.index_daily(ts_code=tc, start_date=start_date, end_date=end_date)
    if df is None or len(df) == 0: return pd.DataFrame()
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['trade_date'] = df['trade_date'].astype(str)
    return df[['trade_date', 'close']].rename(columns={'close': 'S_close'})


# ─────────── 连续序列构建 ───────────

def build_continuous_series(product, ctype, all_futures, spot_df, all_dates):
    """
    构建单条连续序列 (当月/次月/当季/下季)
    返回 DataFrame: [date, contract, F_close, S_close, volume, oi, DTE]
    """
    records = []
    for date in all_dates:
        four = identify_four_contracts(date)
        ym = four.get(ctype)
        if ym is None: continue
        
        # 获取期货数据
        tc = tscode(product, ym)
        if ym not in all_futures: continue
        fdf = all_futures[ym]
        day_f = fdf[fdf['trade_date'] == date]
        if len(day_f) == 0: continue
        
        # 现货
        s_row = spot_df[spot_df['trade_date'] == date]
        if len(s_row) == 0: continue
        
        # DTE
        expiry = get_third_friday(int(ym[:4]), int(ym[4:]))
        dte = (datetime.strptime(expiry, '%Y%m%d') - datetime.strptime(date, '%Y%m%d')).days
        
        records.append({
            'date': date,
            'contract': sym(product, ym),
            'F_close': day_f['close'].values[0],
            'S_close': s_row['S_close'].values[0],
            'volume': day_f['vol'].values[0],
            'oi': day_f['oi'].values[0],
            'DTE': max(dte, 0),
        })
    
    if not records: return pd.DataFrame()
    df = pd.DataFrame(records)
    
    # ── Panama Canal 调整 ──
    df['contract_change'] = df['contract'] != df['contract'].shift(1)
    cum_adj = 0.0
    adjs = []
    for i in range(len(df)):
        if i > 0 and df.iloc[i]['contract_change']:
            cum_adj += df.iloc[i-1]['F_close'] - df.iloc[i]['F_close']
        adjs.append(cum_adj)
    df['adj_F_close'] = df['F_close'] + pd.Series(adjs)
    df = df.drop(columns=['contract_change'])
    
    return df


# ─────────── 特征与信号 ───────────

def compute_features(df, ctype):
    """
    计算升贴水特征与信号
    
    中位数 vs 均值: 基差率在极端行情(2015/2022)大幅偏离，
    均值被拉偏→Z-Score低估极端程度。中位数抗异常值，
    更准确识别"真正的极端"。
    """
    df = df.copy()
    
    # 年化基差率
    df['basis_rate'] = (df['F_close'] / df['S_close'] - 1) * (365.0 / df['DTE'].clip(lower=1))
    df['basis_points'] = df['F_close'] - df['S_close']
    
    # 数据清洗
    df.loc[(df['volume'] == 0) | (df['oi'] == 0), 'basis_rate'] = np.nan
    df['is_delivery_week'] = df['DTE'] < 5
    df['basis_rate_clean'] = df['basis_rate'].where(~df['is_delivery_week'], np.nan)
    
    # 轨道A: 252日滚动分位数
    min_obs = 60 if ctype in ['CQ', 'NQ'] else 40  # CM/NM历史较短, 放宽
    df['rank_252'] = df['basis_rate_clean'].rolling(252, min_periods=min_obs).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x.dropna()) >= 20 else np.nan, raw=False)
    
    # 轨道B: 90日中位数去均值 → 残差Z-Score
    min_obs_b = 30 if ctype in ['CQ', 'NQ'] else 20
    df['median_90'] = df['basis_rate_clean'].rolling(90, min_periods=min_obs_b).median()
    df['residual'] = df['basis_rate_clean'] - df['median_90']
    df['std_90'] = df['residual'].rolling(90, min_periods=min_obs_b).std()
    df['z_score_90'] = df['residual'] / df['std_90']
    
    # 信号
    def classify(row):
        r, z = row['rank_252'], row['z_score_90']
        if pd.isna(r) or pd.isna(z): return '数据不足'
        if r < 0.05 and z < -2.0: return '🔴 极端恐慌(做多)'
        if r < 0.10 and z < -1.5: return '🟠 深度贴水(观察)'
        if r > 0.95 and z > 2.0:  return '🟣 异常收敛(做空)'
        if r < 0.25 and z < -0.5: return '🟡 温和贴水'
        if r > 0.75 and z > 0.5:  return '🟢 温和升水'
        return '⚪ 中性'
    
    df['signal'] = df.apply(classify, axis=1)
    return df


# ─────────── 主程序 ───────────

def main():
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=700)).strftime('%Y%m%d')
    
    all_results = {}  # product -> {ctype -> df}
    all_summary = []
    
    for product in PRODUCTS:
        print(f"\n{'='*70}")
        print(f"  {product} ({PRODUCTS[product]['index_name']}) 升贴水监控 — 四合约对比")
        print(f"{'='*70}")
        
        idx_code = PRODUCTS[product]['index_code']
        spot_df = fetch_index_daily(idx_code, start_date=start_date, end_date=end_date)
        if len(spot_df) == 0:
            print(f"  [{product}] 指数数据为空, 跳过"); continue
        
        all_dates = sorted(spot_df['trade_date'].tolist())
        
        # 收集所有可能需要的合约月份
        needed_ym = set()
        for date in all_dates:
            four = identify_four_contracts(date)
            for ym in four.values():
                if ym: needed_ym.add(ym)
        
        # 批量获取期货数据
        print(f"  [{product}] 获取 {len(needed_ym)} 个合约日线...")
        all_futures = {}
        for ym in sorted(needed_ym):
            tc = tscode(product, ym)
            df = fetch_futures_daily(tc, start_date=start_date, end_date=end_date)
            if len(df) > 0:
                all_futures[ym] = df
        
        active_ym = len(all_futures)
        print(f"  [{product}] 成功获取 {active_ym}/{len(needed_ym)} 个合约")
        
        # 构建4条连续序列
        product_results = {}
        for ctype in CONTRACT_TYPES:
            df = build_continuous_series(product, ctype, all_futures, spot_df, all_dates)
            if len(df) == 0: continue
            df = compute_features(df, ctype)
            product_results[ctype] = df
        
        all_results[product] = product_results
        
        # ── 四合约最新状态对比 ──
        print(f"\n  ┌─── 四合约最新状态 ({all_dates[-1]}) ───")
        print(f"  │ {'类型':<6} {'合约':<10} {'DTE':>4} {'期货':>9} {'现货':>9} {'价差':>8} {'年化基差率':>11} {'rank':>7} {'Z':>7} {'信号'}")
        print(f"  │ {'-'*85}")
        
        for ctype in CONTRACT_TYPES:
            if ctype not in product_results: continue
            df = product_results[ctype]
            if len(df) == 0: continue
            L = df.iloc[-1]
            rk = f"{L['rank_252']:.4f}" if not pd.isna(L['rank_252']) else '  N/A '
            zs = f"{L['z_score_90']:+.2f}" if not pd.isna(L['z_score_90']) else '  N/A '
            print(f"  │ {TYPE_LABELS[ctype]:<6} {L['contract']:<10} {L['DTE']:>4} "
                  f"{L['F_close']:>9.2f} {L['S_close']:>9.2f} {L['basis_points']:>+8.1f} "
                  f"{L['basis_rate']*100:>+10.4f}% {rk:>7} {zs:>7} {L['signal']}")
        
        print(f"  └{'─'*92}")
        
        # ── 各合约近5日趋势 ──
        for ctype in CONTRACT_TYPES:
            if ctype not in product_results: continue
            df = product_results[ctype]
            if len(df) < 2: continue
            print(f"\n  {TYPE_LABELS[ctype]}连续 — 近5日:")
            for _, r in df.tail(5).iterrows():
                rk = f"{r['rank_252']:.3f}" if not pd.isna(r['rank_252']) else 'N/A'
                zs = f"{r['z_score_90']:+.2f}" if not pd.isna(r['z_score_90']) else 'N/A'
                dte_w = '⚠️' if r['DTE'] < 5 else '  '
                print(f"    {r['date']}{dte_w}| {r['contract']} | DTE {r['DTE']:>3}d | "
                      f"基差率 {r['basis_rate']*100:>+9.4f}% | rank {rk} | Z {zs} | {r['signal']}")
        
        # 汇总
        for ctype in CONTRACT_TYPES:
            if ctype not in product_results: continue
            L = product_results[ctype].iloc[-1]
            rk = f"{L['rank_252']:.4f}" if not pd.isna(L['rank_252']) else 'N/A'
            zs = f"{L['z_score_90']:+.2f}" if not pd.isna(L['z_score_90']) else 'N/A'
            all_summary.append({
                '品种': product, '类型': TYPE_LABELS[ctype],
                '合约': L['contract'], 'DTE': L['DTE'],
                '基差率': f"{L['basis_rate']*100:+.4f}%",
                '价差': f"{L['basis_points']:+.1f}",
                'rank': rk, 'Z': zs, '信号': L['signal'],
            })
    
    # ── 全局汇总 ──
    print(f"\n{'='*70}")
    print(f"  全品种信号汇总")
    print(f"{'='*70}")
    hdr = f"  {'品种':<5} {'类型':<6} {'合约':<10} {'DTE':>4} {'基差率':<12} {'价差':>7} {'rank':<8} {'Z':<7} {'信号'}"
    print(hdr)
    print(f"  {'-'*85}")
    for s in all_summary:
        print(f"  {s['品种']:<5} {s['类型']:<6} {s['合约']:<10} {s['DTE']:>4} "
              f"{s['基差率']:<12} {s['价差']:>7} {s['rank']:<8} {s['Z']:<7} {s['信号']}")
    
    # ── 保存CSV ──
    for product, pr in all_results.items():
        for ctype, df in pr.items():
            fname = f'basis_{product}_{ctype}.csv'
            out_cols = ['date','contract','F_close','S_close','DTE','basis_rate','basis_points',
                        'rank_252','z_score_90','signal']
            avail = [c for c in out_cols if c in df.columns]
            df[avail].to_csv(fname, index=False)
    print(f"\n  CSV已保存: basis_IF/IC/IM_CM/NM/CQ/NQ.csv")
    
    return all_results


if __name__ == '__main__':
    results = main()
