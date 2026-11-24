#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ξ关联长度 × D分形维 结构风控模块 (线上版, 防前视)
=====================================================
用途: 替代原 TDA 脆弱性分位控仓, 作为「扩散+RRG 行业轮动」的总仓位风控。
信号定义 (与 v2/v3 回测同族, 窗口适配本任务 60 日 x 30 行业):
  - ξ 关联长度: 行业截面收益相关矩阵特征值间隙 ξ = 1/sqrt(λ_max - mean(λ))
                (低关联=市场自发分散..看涨; 高关联=系统性共振..看跌)
  - D 分形维:   等权市场收益序列的 Higuchi 分形维 (乱度<1.3 看涨 / >1.7 看跌,
                线上用历史分位替代固定阈值, 防前视)
仓位映射 (与回测 v3 等权平均方案一致):
  qX = 今日X值在"此前全部历史"中的分位 (严格只用 target 之前, 防前视)
  pos_X = 100% (qX<0.25 看涨档) / 40% (qX>0.75 看跌档) / 75% (中性)
  position = 0.5*pos_ξ + 0.5*pos_D
历史不足 30 点 -> 中性 75%。
"""
import os, json, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, 'state')
SERIES_CSV = os.path.join(STATE, 'xid_series.csv')
WINDOW = 60


def xi_corr_len(rm):
    """相关矩阵特征值间隙 -> 关联长度 (复刻物理几何 corr_len)"""
    if rm is None or rm.shape[0] < 3:
        return None
    corr = np.nan_to_num(np.corrcoef(rm), nan=0.0)
    ev = np.linalg.eigvalsh(corr)
    ev = ev[ev > 0]
    if len(ev) < 2:
        return 0.0
    diff = ev[-1] - ev.mean()
    return 100.0 if diff < 1e-6 else float(1.0 / np.sqrt(diff))


def higuchi_d(s, kmax=8):
    """Higuchi 分形维 (复刻物理几何 higuchi)"""
    s = np.asarray(s, dtype=float)
    if s is None or len(s) < 20:
        return 1.5
    n = len(s); L = []
    x = np.arange(1, kmax + 1)
    for k in range(1, kmax + 1):
        Lk = 0
        for m in range(k):
            nm = int((n - m - 1) / k) + 1
            if nm < 2:
                continue
            idx = np.arange(m, min(m + nm * k, n), k)
            if len(idx) < 2:
                continue
            ll = np.sum(np.abs(np.diff(s[idx]))) * (n - 1) / (nm * k)
            Lk += ll
        L.append(Lk / k)
    L = np.array(L); v = L > 0
    if v.sum() < 3:
        return 1.5
    c = np.polyfit(np.log(1.0 / x[v]), np.log(L[v]), 1)
    return round(float(np.clip(c[0], 1.0, 2.0)), 4)


def window_features(prices, idx):
    """截至 idx (含) 的 WINDOW 日窗口 -> (ξ, D)"""
    if idx < WINDOW - 1:
        return None, None
    pw = prices.iloc[idx - WINDOW + 1:idx + 1]
    rets = pw.pct_change().dropna(how='any')
    if len(rets) < 30:
        return None, None
    xi = xi_corr_len(rets.values.T)
    mkt = rets.mean(axis=1).values      # 等权市场收益 (市场代理)
    D = higuchi_d(mkt)
    return xi, D


def build_history(prices, force=False):
    """全量重算 ξ/D 历史 -> state/xid_series.csv (date, xi, D)"""
    os.makedirs(STATE, exist_ok=True)
    rows = []
    for i in range(WINDOW - 1, len(prices)):
        xi, D = window_features(prices, i)
        if xi is None:
            continue
        rows.append({'date': prices.index[i], 'xi': xi, 'D': D})
    df = pd.DataFrame(rows).set_index('date')
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df.to_csv(SERIES_CSV)
    print(f'  [xid] 历史重建完成: {len(df)} 点 ({df.index[0].date()} ~ {df.index[-1].date()})', flush=True)
    return df


def ensure_history(prices):
    """已有缓存则增量补算, 无缓存则全量构建"""
    if not os.path.exists(SERIES_CSV):
        return build_history(prices)
    df = pd.read_csv(SERIES_CSV, index_col=0, parse_dates=True)
    df = df[~df.index.duplicated(keep='last')].sort_index()
    last = df.index[-1]
    if last >= prices.index[-1]:
        return df
    pos = prices.index.get_loc(last)
    new_rows = []
    for i in range(pos + 1, len(prices)):
        xi, D = window_features(prices, i)
        if xi is None:
            continue
        new_rows.append({'date': prices.index[i], 'xi': xi, 'D': D})
    if new_rows:
        ndf = pd.DataFrame(new_rows).set_index('date')
        df = pd.concat([df, ndf])
        df = df[~df.index.duplicated(keep='last')].sort_index()
        df.to_csv(SERIES_CSV)
        print(f'  [xid] 历史增量 {len(new_rows)} 点 -> {len(df)} 总点', flush=True)
    return df


def tier_from_quantile(q):
    """分位 -> 三档仓位 (q=今日值的历史分位)"""
    if q is None or np.isnan(q):
        return 0.75
    if q < 0.25:
        return 1.0
    if q > 0.75:
        return 0.40
    return 0.75


def compute_position(prices, target_date, hist):
    """最新交易日 ξ/D -> 历史滚动分位 -> 三档仓位 (防前视, 只用 target 前历史)"""
    t = pd.Timestamp(target_date)
    if t not in prices.index:
        raise ValueError(f'{target_date} not in prices')
    idx = prices.index.get_loc(t)
    xi, D = window_features(prices, idx)
    if xi is None or D is None:
        xi, D = (np.nan, np.nan)
    hist_past = hist[hist.index < t]
    if len(hist_past) >= 30:
        q_xi = float((hist_past['xi'] < xi).mean())
        q_D = float((hist_past['D'] < D).mean())
    else:
        q_xi = q_D = np.nan
    xi_pos = tier_from_quantile(q_xi)
    d_pos = tier_from_quantile(q_D)
    position = round(0.5 * xi_pos + 0.5 * d_pos, 3)
    return {'date': t, 'xi': None if np.isnan(xi) else round(float(xi), 3),
            'D': None if np.isnan(D) else round(float(D), 3),
            'q_xi': None if np.isnan(q_xi) else round(q_xi, 3),
            'q_D': None if np.isnan(q_D) else round(q_D, 3),
            'xi_pos': xi_pos, 'd_pos': d_pos, 'position': position}


def save_daily(date, res):
    """当日结果写入 state/xid_daily.csv (幂等检查用)"""
    os.makedirs(STATE, exist_ok=True)
    fp = os.path.join(STATE, 'xid_daily.csv')
    df = pd.DataFrame({'xi': [res['xi']], 'D': [res['D']], 'pos': [res['position']]},
                      index=[pd.Timestamp(date)])
    if os.path.exists(fp):
        old = pd.read_csv(fp, index_col=0, parse_dates=True)
        df = pd.concat([old, df])
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df.to_csv(fp)


def position_history(hist):
    """历史每日组合仓位 (用于图表): 逐日档案三档 -> 等权"""
    out = []
    for i in range(len(hist)):
        sub = hist.iloc[:i]
        if len(sub) < 30:
            out.append(0.75)
            continue
        qx = float((sub['xi'] < hist['xi'].iloc[i]).mean())
        qd = float((sub['D'] < hist['D'].iloc[i]).mean())
        out.append(0.5 * tier_from_quantile(qx) + 0.5 * tier_from_quantile(qd))
    return pd.Series(out, index=hist.index)