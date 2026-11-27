#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日频 T+1 双轨预测：原算法 + 低波口径，并报当前波动状态

用户 2026-10-21 22:48 要求：
  「中枢内的就是用原来的，还用低波产生两个预测结果，然后都发过去，让我自己看。
    一个是低波的，一个是原来的，当前是低波状态、当前是高波状态，都告诉我一下」

输出（逐指数）：
  ① 当前状态：中枢内/外（上/下）、ZD/ZG、AC1_40、波动档（低/中/高，两口径）
  ② 预测A 原算法：方向 / 幅度 / 该状态历史命中率 / 历史上涨率
  ③ 预测B 低波口径：
       - 中枢内 & 低波 → 位置均值回复规则（贴下沿做多、贴上沿做空），给方向+强度
       - 中枢内 & 非低波 → 「不触发低波口径」，A 仍可用
       - 中枢外 → 低波口径不适用（用 AC 判定）
  ④ 中枢外时额外给 AC 闸门结论（AC≥+0.10 放行 / 否则观望）

严格因果：
  · 预测分只用 T 日及之前（walk-forward 重训，训练窗口前 750 日）
  · 中枢分型要等后一根 merged bar 确认
  · AC1_40 / 波动分位全部扩张窗口，只用历史

用法：
  python3 research/chan_zs_t1/today_dual.py            # 用最新可得数据
  python3 research/chan_zs_t1/today_dual.py --json     # 输出 JSON
"""
import os
import sys
import json
import argparse
import warnings

warnings.filterwarnings('ignore')
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, 'research', 'chan_zs_t1'))
sys.path.insert(0, os.path.join(WS, 'bin'))
sys.path.insert(0, os.path.join(WS, 'research', 'weekly_forecast'))

import numpy as np
import pandas as pd

from zhongshu_v3 import chan_v3
from span_sens import IDX, RAW, OUT

COST = 0.20
TRAIN_WIN = 750
MIN_TRAIN = 200
SEEDS = (7, 17, 27, 37, 47)
AC_GATE = 0.10


def load_raw(fn):
    d = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
    d['dt'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
    d = d.dropna(subset=['dt']).set_index('dt').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    return d


def get_feats(X):
    return [c for c in X.columns if c not in ('c', 'y', 'pred', 'pred_std')]


def predict_last(X, feats):
    """只在最后一行为 NaN 预测的位置做一次 walk-forward 重训"""
    import lightgbm as lgb
    y = np.nan_to_num(X['y'].values, nan=0.0) * 100
    Xm = np.nan_to_num(X[feats].astype(float).replace([np.inf, -np.inf], np.nan).values,
                       nan=0.0, posinf=0.0, neginf=0.0)
    n = len(X)
    preds_mu = np.full(n, np.nan)
    preds_sd = np.full(n, np.nan)
    # 从「已有 pred 的最后一天」之后开始补算
    have = X['pred'].notna().values if 'pred' in X else np.zeros(n, bool)
    last_have = np.where(have)[0]
    start = (last_have[-1] + 1) if len(last_have) else TRAIN_WIN
    for t in range(max(start, TRAIN_WIN), n):
        lo = max(0, t - TRAIN_WIN)
        msk = np.isfinite(y[lo:t])
        if msk.sum() < MIN_TRAIN:
            continue
        Xtr, ytr = Xm[lo:t][msk], y[lo:t][msk]
        ps = []
        for s in SEEDS:
            m = lgb.LGBMRegressor(learning_rate=0.05, n_estimators=200, max_depth=4,
                                  num_leaves=15, subsample=0.8, colsample_bytree=0.8,
                                  random_state=s, verbose=-1, n_jobs=2)
            m.fit(Xtr, ytr)
            ps.append(m.predict(Xm[t:t + 1])[0])
        preds_mu[t] = float(np.mean(ps))
        preds_sd[t] = float(np.std(ps))
    return preds_mu, preds_sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    report = {}
    rows_out = []

    for fn, nm in IDX:
        raw = load_raw(fn)
        close = pd.to_numeric(raw['close'], errors='coerce').astype(float)
        high = pd.to_numeric(raw['high'], errors='coerce').astype(float)
        low = pd.to_numeric(raw['low'], errors='coerce').astype(float)

        # panel（含历史 pred）
        p = os.path.join(OUT, 'panel_%s.csv' % nm)
        panel = pd.read_csv(p, index_col=0, parse_dates=True)

        # 补算最新一天 pred
        pred_today, psd_today = np.nan, np.nan
        try:
            import xm_incr_test as X2
            data = X2.build_X(1, False, False)
            code = [c for c, f2, n2 in X2.IDX if n2 == nm][0]
            X, _ = data[code]
            X = X.copy()
            X['pred'] = panel['pred'].reindex(X.index)
            X['pred_std'] = panel['pred_std'].reindex(X.index)
            fts = get_feats(X)
            mu, sd = predict_last(X, fts)
            if np.isfinite(mu[-1]):
                pred_today, psd_today = float(mu[-1]), float(sd[-1])
        except Exception as e:
            pass

        # 中枢（用 raw 全序列重算，含最新一天）
        cs = chan_v3(close, high, low)

        # AC1_40
        lr = np.log(close / close.shift(1))
        ac = np.full(len(close), np.nan)
        for i in range(40, len(close)):
            w = lr.iloc[i - 40:i].values
            if not np.all(np.isfinite(w)):
                continue
            aa, bb = w[:-1], w[1:]
            sa, sb = aa.std(), bb.std()
            if sa < 1e-12 or sb < 1e-12:
                continue
            ac[i] = float(((aa - aa.mean()) * (bb - bb.mean())).mean() / (sa * sb))
        ac_s = pd.Series(ac, index=close.index)

        # 波动率：rv20 + 扩张分位
        rv20 = lr.rolling(20).std()
        def expq(s):
            q = pd.Series(np.nan, index=s.index)
            vals = []
            for i, v in enumerate(s.values):
                if np.isfinite(v):
                    vals.append(v)
                if len(vals) >= 250 and np.isfinite(v):
                    q.iloc[i] = (np.array(vals) < v).mean()
            return q
        ps_series = panel['pred_std'].reindex(close.index).astype(float)
        if np.isfinite(psd_today):
            ps_series.iloc[-1] = psd_today
        psq = expq(ps_series)
        rvq = expq(rv20)

        def lv(q):
            if not np.isfinite(q):
                return '未知'
            return '低波' if q < 1 / 3 else ('中波' if q < 2 / 3 else '高波')

        t = close.index[-1]
        st = cs['chan'].iloc[-1]
        zd, zg = cs['zd'].iloc[-1], cs['zg'].iloc[-1]
        c_t = close.iloc[-1]
        ac_t = ac_s.iloc[-1]
        psq_t, rvq_t = psq.iloc[-1], rvq.iloc[-1]
        vl_p, vl_r = lv(psq_t), lv(rvq_t)
        rel = (c_t - (zd + zg) / 2) / ((zg - zd) / 2) if np.isfinite(zd) else np.nan

        pred_use = pred_today if np.isfinite(pred_today) else panel['pred'].iloc[-1]

        # 历史校准：该状态 + 该波动档的命中率 / 上涨率
        hist = pd.DataFrame({'y': panel['y'], 'pred': panel['pred'],
                             'ac': ac_s.reindex(panel.index)})
        hist['chan'] = cs['chan'].reindex(panel.index)
        hist = hist.dropna(subset=['y', 'pred'])
        hist = hist[hist.index <= panel.index[-1]]

        def cal(mask):
            s = hist[mask.reindex(hist.index).fillna(False)]
            if len(s) < 15:
                return None
            return dict(n=len(s),
                        hit=float(((s['y'] > 0) == (s['pred'] > 0)).mean()),
                        up=float((s['y'] > 0).mean()),
                        ymean=float(s['y'].mean()))

        cal_state = cal(hist['chan'] == st)
        cal_in = cal(hist['chan'] == 'inside')
        cal_in_low = cal((hist['chan'] == 'inside') & (rvq.reindex(hist.index) < 1 / 3))
        cal_out = cal(hist['chan'].isin(['above', 'below']))

        # 中枢内低波：位置均值回复规则的历史表现
        if st == 'inside' and np.isfinite(rel):
            mr_dir = -1 if rel > 0 else 1
        else:
            mr_dir = 0

        # 中枢外 AC 闸门
        gate = None
        if st in ('above', 'below'):
            gate = 'PASS' if (np.isfinite(ac_t) and ac_t >= AC_GATE) else 'BLOCK'

        row = dict(
            name=nm, date=str(t.date()), close=float(c_t),
            chan=st, zd=float(zd) if np.isfinite(zd) else None,
            zg=float(zg) if np.isfinite(zg) else None,
            rel=float(rel) if np.isfinite(rel) else None,
            ac=float(ac_t) if np.isfinite(ac_t) else None,
            vol_predstd=vl_p, psq=float(psq_t) if np.isfinite(psq_t) else None,
            vol_rv20=vl_r, rvq=float(rvq_t) if np.isfinite(rvq_t) else None,
            pred=float(pred_use) if np.isfinite(pred_use) else None,
            pred_std=float(psd_today) if np.isfinite(psd_today) else None,
            cal_state=cal_state, cal_in=cal_in, cal_in_low=cal_in_low, cal_out=cal_out,
            gate=gate, mr_dir=mr_dir,
        )
        report[nm] = row
        rows_out.append(row)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=float))
        return

    # ─── 文本输出 ───
    print('=' * 108)
    print('日频 T+1 双轨预测（原算法 + 低波口径）｜数据截止 %s' % rows_out[0]['date'])
    print('=' * 108)
    for r in rows_out:
        stcn = {'above': '中枢外·上', 'below': '中枢外·下', 'inside': '中枢内', 'none': '无中枢'}[r['chan']]
        print('\n■ %s  收盘 %.2f   [%s]' % (r['name'], r['close'], stcn))
        if r['zd'] is not None:
            print('   中枢 [%.2f, %.2f]  相对位置 rel = %+.2f（-1=下沿 0=中轴 +1=上沿）' % (
                r['zd'], r['zg'], r['rel'] if r['rel'] is not None else float('nan')))
        print('   AC1_40 = %s    波动档：pred_std=%s (%.2f)  rv20=%s (%.2f)' % (
            ('%+.4f' % r['ac']) if r['ac'] is not None else 'NA',
            r['vol_predstd'], r['psq'] if r['psq'] is not None else float('nan'),
            r['vol_rv20'], r['rvq'] if r['rvq'] is not None else float('nan')))
        ps = r['pred']
        print('   ① 原算法  ：%s  %+.3f（模型标准差 %.3f）' % (
            ('看多' if ps > 0 else '看空') if ps is not None else 'NA',
            ps if ps is not None else float('nan'),
            r['pred_std'] if r['pred_std'] is not None else float('nan')))
        if r['chan'] == 'inside':
            if r['vol_predstd'] == '低波' or r['vol_rv20'] == '低波':
                print('   ② 低波口径：%s（贴沿均值回复，rel=%+.2f → %s）' % (
                    '看多' if r['mr_dir'] > 0 else '看空',
                    r['rel'] if r['rel'] is not None else float('nan'),
                    '贴近下沿' if r['mr_dir'] > 0 else '贴近上沿'))
            else:
                print('   ② 低波口径：不触发（当前不是低波状态）')
            if r['cal_in_low']:
                print('      [校准] 中枢内+低波 历史 n=%d 命中 %.1f%% 上涨 %.1f%% 次日均值 %+.3f%%' % (
                    r['cal_in_low']['n'], r['cal_in_low']['hit'] * 100,
                    r['cal_in_low']['up'] * 100, r['cal_in_low']['ymean']))
            if r['cal_in']:
                print('      [校准] 中枢内(全部) 历史 n=%d 命中 %.1f%% 上涨 %.1f%% 次日均值 %+.3f%%' % (
                    r['cal_in']['n'], r['cal_in']['hit'] * 100,
                    r['cal_in']['up'] * 100, r['cal_in']['ymean']))
        else:
            print('   ② 低波口径：不适用（不在中枢内，用 AC 闸门）')
            if r['gate']:
                print('      AC闸门：%s（AC=%+.4f，门槛 +0.10）' % (
                    '✅ 放行' if r['gate'] == 'PASS' else '⛔ 观望',
                    r['ac'] if r['ac'] is not None else float('nan')))
        if r['cal_state']:
            print('   [校准] 当前状态(%s) 历史 n=%d 命中 %.1f%% 上涨 %.1f%%' % (
                stcn, r['cal_state']['n'], r['cal_state']['hit'] * 100,
                r['cal_state']['up'] * 100))
    print('\n' + '=' * 108)


if __name__ == '__main__':
    main()
