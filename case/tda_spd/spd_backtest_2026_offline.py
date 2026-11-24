#!/usr/bin/env python3
"""
SPD流形 2026年至今回测 (完全离线版, 直接读 state/sw_cache)
=========================================================
22申万行业因子, 60日窗口: SPD Fisher-z测地漂移 x 动量(s+1)^2 x discount, Top5集中
调仓 REBAL=5交易日, 信号T日->T+1生效
"""
import os, sys, json, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'pylib'))
from spd_core import compute_spd_weights

START, END = '20260101', '20260822'
TOP_N, REBAL, WINDOW, REF_OFFSET = 5, 5, 60, 60

# ---- 从缓存读 22 因子 close (完全离线) ----
CACHE = os.path.join(BASE, 'state', 'sw_cache')
FACTOR_FILES = {
    '科技成长': '801080', '高端制造': '801890', '新能源': '801730', '医药健康': '801150',
    '大消费': '801120', '金融地产': '801780', '资源材料': '801050', '数字经济': '801750',
    '国防军工': '801740', '绿色低碳': '801970', '农业养殖': '801010', '低波防御': '801160',
    '红利高息': '801170', '价值周期': '801040', '动量': '801880', '反转': '801180',
    '小盘成长': '801210', '大盘蓝筹': '801790', '质量因子': '801110', '成长因子': '801760',
    '价值因子': '801030', '流动性因子': '801950',
}

def load_prices():
    series = {}
    for name, code in FACTOR_FILES.items():
        fp = os.path.join(CACHE, f'sw_{code}.json')
        if not os.path.exists(fp):
            continue
        d = json.load(open(fp))
        s = pd.Series({r['trade_date']: r['close'] for r in d})
        s.index = pd.to_datetime(s.index, format='%Y%m%d')
        series[name] = s
    df = pd.DataFrame(series).sort_index()
    df = df.dropna(how='all')
    return df

def compute_factor_sharpe_local(pw, window=60):
    # 注意: 不能整行 dropna, 否则任一因子整列缺失会清空所有行, 使全部因子夏普退化;
    # 缺失因子按列单独 dropna 处理 (公式不变)
    ret = pw.pct_change()
    out = {}
    for c in pw.columns:
        r = ret[c].dropna()
        mean = r.mean() * window
        std = r.std() * np.sqrt(window)
        out[c] = (mean / std) if std > 0 else 0.0
    return out

def backtest_spd():
    price_df = load_prices()
    print(f'  price matrix: {price_df.shape} ({price_df.index[0].date()} ~ {price_df.index[-1].date()})')
    if len(price_df) < WINDOW + REF_OFFSET + 30:
        print('  !! 数据不足'); return None
    dates = price_df.index.tolist()
    bt_dates = [d for d in dates if START <= str(d.date()).replace('-', '') <= END]
    bt_dates_set = {str(d.date()).replace('-', '') for d in bt_dates}
    rets = price_df.pct_change().fillna(0)

    nav = 1.0; peak = 1.0; mdd = 0.0
    rows = []; holdings = {}; last_rebal = -999
    pos_log = []
    for i, d in enumerate(dates):
        ds = str(d.date())
        if ds.replace('-', '') not in bt_dates_set:
            continue
        if (i - last_rebal >= REBAL) and (i >= WINDOW):
            pw = price_df.iloc[i - WINDOW + 1:i + 1]
            sharpe = compute_factor_sharpe_local(pw, window=WINDOW)
            weights, total_drift = compute_spd_weights(
                pw, price_df, i, sharpe, top_n=TOP_N,
                momentum_discount=0.5, window=WINDOW, ref_offset=REF_OFFSET)
            holdings = weights
            last_rebal = i
            pos_log.append((ds, sorted(holdings.items(), key=lambda x: -x[1])[:3]))
        if holdings:
            h = [c for c in holdings if c in rets.columns]
            r = sum(holdings[c] * rets.iloc[i][c] for c in h) if h else 0.0
        else:
            r = 0.0
        nav *= (1 + r)
        peak = max(peak, nav)
        mdd = max(mdd, (peak - nav) / peak)
        rows.append((d, nav, r))

    df = pd.DataFrame(rows, columns=['date', 'nav', 'ret'])
    days = len(df)
    total = df['nav'].iloc[-1] - 1
    ann = (1 + total) ** (252 / days) - 1 if days > 0 else 0
    sharpe = df['ret'].mean() / df['ret'].std() * np.sqrt(252) if df['ret'].std() > 0 else 0
    # 年内高点到低点
    dd_series = df['nav'] / df['nav'].cummax() - 1
    mdd_date = dd_series.idxmin()
    return {
        'nav_df': df, 'total': total, 'ann': ann, 'mdd': mdd,
        'sharpe': sharpe, 'days': days, 'mdd_date': str(mdd_date.date()) if hasattr(mdd_date,'date') else str(mdd_date),
        'positions': pos_log,
    }

if __name__ == '__main__':
    print(f'=== SPD流形 2026至今 (离线, Top{TOP_N}, {WINDOW}日, REBAL={REBAL}) ===')
    r = backtest_spd()
    if r:
        print(f'  累计: {r["total"]:.2%}  年化: {r["ann"]:.2%}  最大回撤: {r["mdd"]:.2%}({r["mdd_date"]})  夏普: {r["sharpe"]:.3f}  T={r["days"]}')
        print('  Top3持仓样本:')
        for ds, top3 in r['positions'][:6] + r['positions'][-2:]:
            print(f'    {ds}: ' + ', '.join(f'{k}={v:.2f}' for k, v in top3))
        out = {
            'total': r['total'], 'ann': r['ann'], 'mdd': r['mdd'], 'sharpe': r['sharpe'],
            'days': r['days'], 'mdd_date': r['mdd_date'],
            'nav': r['nav_df'][['date', 'nav']].to_dict('records'),
        }
        json.dump(out, open(os.path.join(BASE, 'spd_2026_result.json'), 'w'), default=str, ensure_ascii=False, indent=1)
        print('  saved spd_2026_result.json')