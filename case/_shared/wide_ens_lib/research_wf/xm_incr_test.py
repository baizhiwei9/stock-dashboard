#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量检验：把跨市场隔夜因子加进现有特征，T+5 方向命中率有没有提升

设计（避免自欺）
----------------
· 两组唯一差别 = 特征集有没有 xm_* 列，其余全部相同（同 index、同 label、同窗口、
  同 walk-forward 时点、同 5 种子 seed、同门槛网格）。
· 结论只看 **OOS(2023 起)**。IS 只是用来「挑门槛」，两组都按同一规则挑。
· 命中口径与生产一致：方向 = sign(预测)，命中 = 方向与真实收益同号，扣 0.2% 成本。
· 同时报「命中率 / 样本数 / 相对同期基准上涨率的超额」——因为 A 股有 beta，
  单看命中率会被行情骗（牛市里看多随便都对）。

用法：
  python3 research/weekly_forecast/xm_incr_test.py --h 5
  python3 research/weekly_forecast/xm_incr_test.py --h 1
"""
import os
import sys
import json
import time
import argparse
import warnings

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(WS, 'bin'))

import numpy as np
import pandas as pd
from scipy.stats import binomtest
from sklearn.preprocessing import StandardScaler

RAW = os.path.join(WS, '.cache/weekly_forecast/raw')
XM = os.path.join(RAW, 'cross_market_factors.csv')
CU = os.path.join(RAW, 'china_us_factors.csv')
SP = os.path.join(RAW, 'spread_factors.csv')

IDX = [('000300_SH', 'idx_hs300.csv', '沪深300'),
       ('000905_SH', 'idx_zz500.csv', '中证500'),
       ('000852_SH', 'idx_zz1000.csv', '中证1000'),
       ('000688_SH', 'idx_kc50.csv', '科创50'),
       ('399006_SZ', 'idx_cyb.csv', '创业板')]

IS_LO, IS_HI = '2019-01-01', '2022-12-31'
OOS_LO = '2023-01-01'
SEEDS = [42, 7, 2026, 123, 999]
TRAIN_WIN = 750
MIN_TRAIN = 500
RETRAIN = 20
PCT_WIN, PCT_MINP = 500, 150
COST = 0.20
GRID = (0.40, 0.50, 0.60, 0.85, 0.90)


def load_idx(fn):
    d = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
    d['dt'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
    d = d.dropna(subset=['dt']).set_index('dt').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    return d


def _load_cu(index):
    """中概/中国ETF（美股挂牌，已按美东 D-1 对齐）"""
    if not os.path.exists(CU):
        return None
    cu = pd.read_csv(CU, index_col=0)
    cu.index = pd.to_datetime(cu.index, errors='coerce')
    cu = cu[~cu.index.duplicated(keep='last')].sort_index()
    return cu.reindex(index).ffill()


def build_X(H, use_xm, use_cu=False):
    """特征（全部 T 日收盘可知）+ 未来 H 日收益"""
    xm = pd.read_csv(XM, index_col=0)
    xm.index = pd.to_datetime(xm.index, errors='coerce')
    xm = xm[~xm.index.duplicated(keep='last')].sort_index()
    sp = pd.read_csv(SP, index_col=0, parse_dates=True) if os.path.exists(SP) else pd.DataFrame()
    out = {}
    for code, fn, nm in IDX:
        d = load_idx(fn)
        c = pd.to_numeric(d['close'], errors='coerce').astype(float)
        o = pd.to_numeric(d['open'], errors='coerce').astype(float)
        hi = pd.to_numeric(d['high'], errors='coerce').astype(float)
        lo = pd.to_numeric(d['low'], errors='coerce').astype(float)
        vol = pd.to_numeric(d['vol'], errors='coerce').astype(float)
        amt = pd.to_numeric(d['amount'], errors='coerce').astype(float)
        r = c.pct_change() * 100
        X = pd.DataFrame(index=c.index)
        X['c'] = c
        for n in (1, 2, 3, 5, 10, 20, 60):
            X['mom%d' % n] = c / c.shift(n) - 1
        for n in (5, 10, 20, 60):
            X['ma%d' % n] = c / c.rolling(n).mean() - 1
        dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        dea = dif.ewm(span=9, adjust=False).mean()
        X['macd'] = (dif - dea) * 2 / c
        X['vol_r'] = vol / vol.rolling(5).mean()
        X['amt_r'] = amt / amt.rolling(20).mean()
        X['dvol20'] = r.rolling(20).std()
        X['up5'] = (r > 0).rolling(5).mean()
        X['up20'] = (r > 0).rolling(20).mean()
        X['amp'] = (hi - lo) / c.shift(1) * 100
        X['amp20'] = X['amp'].rolling(20).mean()
        rng = (hi.rolling(20).max() - lo.rolling(20).min()).replace(0, np.nan)
        X['hl_pos'] = (c - lo.rolling(20).min()) / rng
        X['pos52'] = c / c.rolling(250).max() - 1
        X['gap'] = (o - c.shift(1)) / c.shift(1) * 100
        X['vr'] = r.abs() / vol.rolling(20).mean()
        up = (r > 0).astype(int)
        X['streak'] = up.groupby((up != up.shift()).cumsum()).cumcount() + 1
        if len(sp):
            for col in ('ts_10y1y', 'ts_10y1y_p250', 'cn_cs_3y', 'cn_cs_3y_p250',
                        'us_cs_baa10y', 'us_cs_baa10y_p250', 'gz_10y', 'cn_us_10y',
                        'qvix300', 'qvix500', 'iv_atm300', 'skew300'):
                if col in sp.columns:
                    X[col] = sp[col].reindex(X.index.union(sp.index)).ffill().reindex(X.index)
        if use_xm:
            xr = xm.reindex(X.index).ffill()   # ⚠️ 只 ffill，绝不 bfill
            for col in xr.columns:
                X[col] = xr[col]
        if use_cu:
            cr = _load_cu(X.index)
            if cr is not None:
                for col in cr.columns:
                    X[col] = cr[col]
        X['y'] = c.shift(-H) / c - 1
        out[code] = (X.dropna(subset=['c']), nm)
    return out


def walk_forward(X, feats, seeds=SEEDS):
    """滚动 20 日重训、750 日窗口、5 种子平均 → score 序列（与生产同一套）"""
    import lightgbm as lgb
    n = len(X)
    y = np.nan_to_num(X['y'].values, nan=0.0) * 100
    Xm = np.nan_to_num(X[feats].astype(float).replace([np.inf, -np.inf], np.nan).values,
                       nan=0.0, posinf=0.0, neginf=0.0)
    score = np.full(n, np.nan)
    pos = TRAIN_WIN
    while pos < n:
        lo = max(0, pos - TRAIN_WIN)
        msk = np.isfinite(y[lo:pos])
        if msk.sum() >= MIN_TRAIN:
            Xtr, ytr = Xm[lo:pos][msk], y[lo:pos][msk]
            preds = []
            for s in seeds:
                m = lgb.LGBMRegressor(learning_rate=0.05, n_estimators=200, max_depth=4,
                                      num_leaves=15, subsample=0.8, colsample_bytree=0.8,
                                      random_state=s, verbose=-1, n_jobs=2)
                m.fit(Xtr, ytr)
                preds.append(m.predict(Xm[pos:pos + RETRAIN]))
            score[pos:pos + RETRAIN] = np.mean(preds, axis=0)
        pos += RETRAIN
    return pd.Series(score, index=X.index)


def evaluate(sc, y, name):
    """IS 挑门槛 → OOS 验收（命中率 / 样本 / 超额）"""
    df = pd.DataFrame({'score': sc, 'ret': y * 100}).dropna()
    df['pct'] = df['score'].abs().rolling(PCT_WIN, min_periods=PCT_MINP).rank(pct=True)
    best = None
    for g in GRID:
        ev = df.loc[IS_LO:IS_HI].dropna(subset=['pct'])
        sel = ev[ev['pct'] >= g]
        if len(sel) < 25:
            continue
        hit = (((sel['ret'] > 0) == (sel['score'] > 0))).mean()
        net = (np.sign(sel['score']) * sel['ret']).mean() - COST
        if net <= 0:
            continue
        if best is None or hit > best[1]:
            best = (g, hit, len(sel), net)
    if best is None:
        return dict(name=name, gate=None, oos_n=0, oos_hit=float('nan'),
                    oos_base=float('nan'), oos_excess=float('nan'), is_hit=float('nan'))
    g = best[0]
    oo = df.loc[OOS_LO:].dropna(subset=['pct'])
    sel = oo[oo['pct'] >= g]
    if not len(sel):
        return dict(name=name, gate=g, oos_n=0, oos_hit=float('nan'),
                    oos_base=float('nan'), oos_excess=float('nan'), is_hit=best[1])
    hit = (((sel['ret'] > 0) == (sel['score'] > 0))).mean()
    base = (sel['ret'] > 0).mean()
    p = binomtest(int((((sel['ret'] > 0) == (sel['score'] > 0))).sum()), len(sel),
                  alternative='greater').pvalue
    return dict(name=name, gate=g, is_hit=best[1], is_n=best[2],
                oos_n=len(sel), oos_hit=hit, oos_base=base,
                oos_excess=hit - base, oos_p=p,
                oos_net=(np.sign(sel['score']) * sel['ret']).mean() - COST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--h', type=int, default=5)
    ap.add_argument('--sets', default='base,xm,cu',
                    help='要跑的组：base=基线, xm=加A50/恒指/美股, cu=加中概/中国ETF')
    a = ap.parse_args()
    t0 = time.time()
    print('周期 T+%d ｜ IS %s~%s 挑门槛，OOS %s 起验收' % (a.h, IS_LO, IS_HI, OOS_LO))
    print()

    rows = []
    SETS = {'base': (False, False, '基线'),
            'xm': (True, False, '含A50/恒指/美股'),
            'cu': (True, True, '含中概/中国ETF')}
    want = [x.strip() for x in a.sets.split(',') if x.strip()]
    for key in want:
        use_xm, use_cu, tag = SETS[key]
        data = build_X(a.h, use_xm, use_cu)
        for code, (X, nm) in data.items():
            feats = [c for c in X.columns if c not in ('c', 'y')]
            sc = walk_forward(X, feats)
            r = evaluate(sc, X['y'].values, nm)
            r['set'] = tag
            r['n_feat'] = len(feats)
            rows.append(r)
            print('[%s] %-9s 特征%3d  门槛%s  OOS n=%3d 命中%s 基准%s 超额%s  %.0fs' % (
                tag, nm, len(feats), r['gate'],
                r['oos_n'],
                ('%.1f%%' % (r['oos_hit'] * 100)) if r['oos_hit'] == r['oos_hit'] else ' -',
                ('%.1f%%' % (r['oos_base'] * 100)) if r['oos_base'] == r['oos_base'] else ' -',
                ('%+.1f%%' % (r['oos_excess'] * 100)) if r['oos_excess'] == r['oos_excess'] else ' -',
                time.time() - t0), flush=True)
    print()
    print('=' * 92)
    print('T+%d ｜ 基线 vs 含跨市场  （OOS %s 起）' % (a.h, OOS_LO))
    print('=' * 92)
    tags = [SETS[k][2] for k in want]
    print('%-9s | ' % '指数' + ' | '.join('%-20s' % t for t in tags))
    d = pd.DataFrame(rows).set_index(['name', 'set'])
    for code, fn, nm in IDX:
        cells = []
        for t in tags:
            try:
                r = d.loc[(nm, t)]
            except KeyError:
                cells.append('—')
                continue
            if r['oos_hit'] != r['oos_hit']:
                cells.append('无达标门槛')
            else:
                cells.append('%.1f%%(n=%d) %+.1f%%' % (r['oos_hit'] * 100, r['oos_n'], r['oos_excess'] * 100))
        print('%-9s | ' % nm + ' | '.join('%-20s' % c for c in cells))
    out = os.path.join(WS, '.cache/weekly_forecast', 'xm_incr_T%d.json' % a.h)
    json.dump(rows, open(out, 'w'), ensure_ascii=False, indent=1, default=str)
    print()
    print('[save] %s  (%.0fs)' % (out, time.time() - t0))


if __name__ == '__main__':
    main()
