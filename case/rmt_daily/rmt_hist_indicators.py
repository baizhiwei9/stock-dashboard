#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RMT 指标全历史重算 —— 为「指标 × 事件窗口」统计准备统一口径的逐日序列。

为什么要重算：工作区既有的 `rmt_cgap_history.json` 只存了 λ₁/λ₂/λ₃（没有完整谱，无法算
⟨r⟩、熵缺口这类需要全谱的指标）；`_hist_rmt.json` 只到 2026-10-22。本脚本用与线上完全相同的
清洗口径重算全谱指标，并**与既存 λ₁ 交叉校验**，保证口径一致。

清洗口径（与 rmt_cgap_daily.eigen_metrics / rmt_daily_monitor.compute_rmt 相同）：
  60 个交易日收益率窗口 → corr()，NaN→0，对角线置 1，对称化 → eigh 降序，特征值下限 1e-12。

指标定义（本文件为唯一口径来源）
--------------------------------
  λᵢ        : 相关矩阵第 i 大特征值
  C(t)      : λ₁ / Σλ × 100（市场协同度／第一主成分吸收率）
  Gap       : λ₁ / λ₂
  gap₁      : (λ₁ − λ₂) / λ₁（最大/次大特征值间隙比；等价 1 − 1/Gap）—— 对应图上的"
              能级间距比"家族，但归一方式不同（图上为全谱相邻间距比均值）
  λ+        : MP 随机矩阵上界 (1+√q)²，q = N/窗口长度 = 18/60 = 0.3
  ⟨r⟩       : **越轨**（>λ+）特征值的相邻间距比均值：s_j = λ_j − λ_{j+1}，
              r_j = min(s_j, s_{j+1}) / max(s_j, s_{j+1})，⟨r⟩ = mean(r_j)；
              越轨特征值少于 3 个时记 NaN（无法构成间距比）
  S         : Von Neumann 熵 = −Σ pⱼ ln pⱼ，pⱼ = λⱼ / Σλ（高 = 多因子独立）
  熵缺口     : ln(N) − S（N=18；越大 = 谱越集中 = 分散化失效）—— 对应"复杂度缺口"
  ΔS        : S(t) − S(t−5)
  Δ收窄      : gap₁(t) − gap₁(t−5)（>0 = 间隙收窄、风格趋同）—— 对应"Δ收窄"
  IPR       : Σ(v₁ᵢ)⁴（第一特征向量参与率；单位向量下与 Σv⁴/(Σv²)² 等价）
  SNR       : λ₁ / mean(λ₂…λ_N)
  τ 隧穿     : **未实现**（图上未给 ε 带宽与"放量"口径，不臆造）

用法：python3 rmt_hist_indicators.py  →  写 rmt_hist_indicators.json
"""
import os
import sys
import json
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

WORK = os.path.dirname(os.path.abspath(__file__))
WINDOW = 60
LABEL_LAG = 5

ETF_LIST = {
    '512480': 'sh', '512760': 'sh', '515880': 'sh', '515050': 'sh',
    '510300': 'sh', '510500': 'sh', '512100': 'sh', '512890': 'sh',
    '512200': 'sh', '512010': 'sh', '512690': 'sh', '512800': 'sh',
    '512660': 'sh', '159915': 'sz', '510050': 'sh', '510880': 'sh',
    '512720': 'sh', '159928': 'sz',
}


# ---- DataHub 股票模块（三级体系）：ETF/指数日线统一入口 ----
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_INDEX_SUFFIX = {
    '000001': '.SH', '000016': '.SH', '000300': '.SH', '000688': '.SH',
    '000905': '.SH', '000852': '.SH', '000985': '.CSI',
    '399001': '.SZ', '399006': '.SZ', '399106': '.SZ',
}


def _split_symbol(code):
    raw = str(code).strip().lower()
    prefix = raw[:2] if raw[:2] in ('sh', 'sz') else ''
    num = (raw[2:] if prefix else raw).split('.')[0]
    if num.startswith(('15', '16', '51', '52', '56', '58')):
        return 'etf', num
    if num in _INDEX_SUFFIX:
        return 'index', num + _INDEX_SUFFIX[num]
    return 'index', num + ('.SH' if (prefix == 'sh' or num.startswith('000')) else '.SZ')


def _hub_daily_rows(code, days):
    """DataHub 日线 -> (close Series, vol Series)，索引为 YYYY-MM-DD。"""
    kind, sym = _split_symbol(code)
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    if kind == 'etf':
        from data_hub.collectors.misc import get_etf_daily
        df = get_etf_daily(code=sym, start_date=start, end_date=end)
    else:
        from data_hub.collectors.stocks import get_index_daily
        df = get_index_daily(ts_code=sym, start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
    dates = (df[dcol].astype(str).str.replace('-', '')
             .map(lambda x: f'{x[:4]}-{x[4:6]}-{x[6:8]}'))
    close = pd.Series(pd.to_numeric(df['close'], errors='coerce').values, index=dates).sort_index()
    vcol = 'vol' if 'vol' in df.columns else ('volume' if 'volume' in df.columns else None)
    vol = (pd.Series(pd.to_numeric(df[vcol], errors='coerce').values, index=dates).sort_index()
           if vcol else pd.Series(index=close.index, dtype=float))
    return close.dropna(), vol


def fetch_kline(code, days=900):
    """DataHub 股票模块前复权日线：返回 {YYYY-MM-DD: close}。"""
    try:
        close, _vol = _hub_daily_rows(code, days)
        return {str(k): float(v) for k, v in close.items()}
    except Exception as e:
        print(f'  fetch_kline({code}) dataHub error: {type(e).__name__}: {str(e)[:100]}')
        return {}


def spectrum_metrics(win_ret):
    """返回该窗口的相关矩阵全谱与派生指标（口径与线上一致）。"""
    C = win_ret.corr().values
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    C = (C + C.T) / 2
    vals, vecs = np.linalg.eigh(C)
    order = np.argsort(vals)[::-1]
    vals = np.maximum(vals[order], 1e-12)
    v1 = vecs[:, order[0]]
    N = len(vals)
    q = N / len(win_ret)
    lam_plus = (1 + np.sqrt(q)) ** 2
    p = vals / vals.sum()
    S = float(-np.sum(p * np.log(p)))
    # ⟨r⟩：两种口径都算，避免用一个在 18 只池上凑不出样本的定义
    # ① 越轨（>λ+）特征值的相邻间距比 —— 图上的原始定义，但 18 只池上越轨常只有 1~3 个，多半记 NaN
    out = vals[vals > lam_plus]
    rbar_out = np.nan
    if len(out) >= 3:
        s = out[:-1] - out[1:]
        rbar_out = float(np.mean(np.minimum(s[:-1], s[1:]) / np.maximum(s[:-1], s[1:])))
    # ② 体内谱（≤λ+）展开后的相邻间距比均值 —— RMT 文献的标准 r̄（泊松≈0.386，GOE≈0.531）
    bulk = np.sort(vals[vals <= lam_plus])
    rbar = np.nan
    if len(bulk) >= 4:
        s = np.diff(bulk)
        s = s / s.mean()
        rbar = float(np.mean(np.minimum(s[:-1], s[1:]) / np.maximum(s[:-1], s[1:])))
    return {
        'lambda1': float(vals[0]), 'lambda2': float(vals[1]), 'lambda3': float(vals[2]),
        'sum_lambda': float(vals.sum()), 'ipr': float(np.sum(v1 ** 4)),
        'lambda_plus': float(lam_plus), 'n_outlier': int(len(out)), 'n_bulk': int(len(bulk)),
        'rbar': rbar, 'rbar_out': rbar_out,
        'entropy': S, 'entropy_gap': float(np.log(N) - S),
        'gap1': float((vals[0] - vals[1]) / vals[0]),
        'snr': float(vals[0] / (np.mean(vals[1:]) + 1e-12)),
    }


def main():
    t0 = time.time()
    print('Fetching pool prices ...')
    bars = {}
    for i, (code, prefix) in enumerate(ETF_LIST.items(), 1):
        d = fetch_kline(f'{prefix}{code}')
        for dt, c in d.items():
            bars.setdefault(dt, {})[code] = c
        time.sleep(0.12)
    codes = list(ETF_LIST)
    dates = sorted(bars)
    df_close = pd.DataFrame({'date': pd.to_datetime(dates)})
    for c in codes:
        df_close[c] = [bars[d].get(c, np.nan) for d in dates]
    print(f'  {len(df_close)} 个交易日 × {len(codes)} 只，最新 {df_close["date"].iloc[-1].date()}')

    rets = df_close[codes] / df_close[codes].shift(1) - 1
    rows = []
    for i in range(WINDOW, len(df_close)):
        m = spectrum_metrics(rets.iloc[i - WINDOW + 1:i + 1])
        m['date'] = df_close['date'].iloc[i].strftime('%Y-%m-%d')
        rows.append(m)
    df = pd.DataFrame(rows)

    # 5 日变化量
    df = df.sort_values('date').reset_index(drop=True)
    df['d_entropy_5'] = df['entropy'] - df['entropy'].shift(LABEL_LAG)
    df['d_gap1_5'] = df['gap1'] - df['gap1'].shift(LABEL_LAG)
    df['d_rbar_5'] = df['rbar'] - df['rbar'].shift(LABEL_LAG)
    df['C'] = df['lambda1'] / df['sum_lambda'] * 100.0
    df['Gap'] = df['lambda1'] / df['lambda2']

    # 与既存 λ₁ 交叉校验
    old = pd.DataFrame(json.load(open(os.path.join(WORK, 'rmt_cgap_history.json'), encoding='utf-8')))
    old = old[['date', 'lambda1', 'lambda2', 'C', 'Gap', 'IPR', 'index_close']].rename(
        columns={'lambda1': 'l1_old', 'lambda2': 'l2_old', 'C': 'C_old', 'Gap': 'Gap_old',
                 'IPR': 'IPR_old'})
    mg = df.merge(old, on='date', how='inner')
    diff = {
        'n': len(mg),
        'max_abs_d_lambda1': float((mg['lambda1'] - mg['l1_old']).abs().max()),
        'max_abs_d_lambda2': float((mg['lambda2'] - mg['l2_old']).abs().max()),
        'max_abs_d_C': float((mg['C'] - mg['C_old']).abs().max()),
        'max_abs_d_IPR': float((mg['ipr'] - mg['IPR_old']).abs().max()),
    }
    print('  与既存 λ₁ 交叉校验：', diff)

    # 用既存指数收盘价补齐（同时间轴对照）
    idx = pd.DataFrame(json.load(open(os.path.join(WORK, 'rmt_cgap_history.json'), encoding='utf-8')))
    idx = idx[[c for c in ['date', 'index_close', 'q250', 'd_lambda1'] if c in idx.columns]]
    df = df.merge(idx, on='date', how='left')
    out = os.path.join(WORK, 'rmt_hist_indicators.json')
    rows_out = json.loads(df.to_json(orient='records'))       # NaN → null
    json.dump({'window': WINDOW, 'label_lag': LABEL_LAG, 'codes': codes,
               'cross_check': diff, 'rows': rows_out},
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'  {len(df)} 行 → {out}  ({time.time() - t0:.1f}s)')
    print(f'  覆盖 {df["date"].iloc[0]} → {df["date"].iloc[-1]}；'
          f'⟨r⟩ 有效 {int(df["rbar"].notna().sum())} 天，越轨特征值中位 {df["n_outlier"].median():.0f} 个')


if __name__ == '__main__':
    main()