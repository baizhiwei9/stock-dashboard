# -*- coding: utf-8 -*-
"""多尺度日度信息 + GBDT：科创50 / 创业板 高置信度胜率

在原有 168 列因子上，显式追加**多尺度日度信息**：
  - 多窗口收益率 ret_1/3/5/10/20/60/120
  - 多窗口波动率 vol_5/10/20/60
  - 多窗口价格分位 pct_5/10/20/60/120（close 在窗口内的百分位）
  - 多窗口乖离 ma_dev_5/10/20/60/120
  - 多窗口量比分位 vr_pct_5/10/20/60
  - 多尺度动量一致性 mom_agree（各窗口同向比例）

日度(T+1) 与 周度(T+5) 都跑，输出**阈值扫描**（高置信度子集）。

用法:
  python3 bin/wide_ens/multi_scale.py --code 000688_SH --h 5 --depth 3
输出: .cache/wide_ens/ms/<code>_lgb_tri_d<depth>_T<h>.{json,csv}
"""
import os, sys, json, argparse, warnings, time
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt70 as H

OUT = __import__('wsenv').state_dir('.cache', 'wide_ens', 'ms')
os.makedirs(OUT, exist_ok=True)

TRAIN_WIN, MIN_TRAIN, RETRAIN = 750, 500, 20


def add_multiscale(df, close):
    """追加多尺度日度特征"""
    c = close.reindex(df.index).ffill()
    r1 = c.pct_change()
    F = {}
    # 多窗口收益
    for k in (1, 3, 5, 10, 20, 60, 120):
        F['MS_ret_%d' % k] = c / c.shift(k) - 1
    # 多窗口波动
    for k in (5, 10, 20, 60):
        F['MS_vol_%d' % k] = r1.rolling(k).std()
    # 多窗口波动比（短/长）
    for a, b in ((5, 20), (5, 60), (10, 60), (20, 120)):
        F['MS_volratio_%d_%d' % (a, b)] = (r1.rolling(a).std() /
                                           r1.rolling(b).std().replace(0, np.nan))
    # 多窗口价格分位
    for k in (5, 10, 20, 60, 120):
        F['MS_pct_%d' % k] = c.rolling(k).apply(
            lambda x: (x[-1] - x.min()) / (x.max() - x.min()) if x.max() > x.min() else 0.5,
            raw=True)
    # 多窗口乖离
    for k in (5, 10, 20, 60, 120):
        F['MS_dev_%d' % k] = c / c.rolling(k).mean() - 1
    # 多窗口 RSI
    for k in (6, 14, 28):
        up = r1.clip(lower=0).rolling(k).mean()
        dn = (-r1.clip(upper=0)).rolling(k).mean().replace(0, np.nan)
        F['MS_rsi_%d' % k] = 100 - 100 / (1 + up / dn)
    # 多尺度动量一致性：各窗口收益同向比例
    mom = pd.DataFrame({k: F['MS_ret_%d' % k] for k in (1, 3, 5, 10, 20, 60)})
    F['MS_mom_agree'] = (np.sign(mom) > 0).mean(axis=1)
    # 多尺度加速度：短期动量 - 长期动量
    for a, b in ((1, 5), (3, 10), (5, 20), (10, 60)):
        F['MS_accel_%d_%d' % (a, b)] = F['MS_ret_%d' % a] - F['MS_ret_%d' % b]
    # 过夜跳空 & 日内幅度
    F['MS_gap_1'] = c / c.shift(1) - 1
    F['MS_range_20'] = ((c - c.rolling(20).min()) /
                        (c.rolling(20).max() - c.rolling(20).min()).replace(0, np.nan))
    M = pd.DataFrame(F, index=df.index)
    return pd.concat([df, M], axis=1)


def build(code, h, multiscale=True):
    c, df = H.build(code, with_extra_h=h)
    if multiscale:
        df = add_multiscale(df, c)
    y_ret = (c.shift(-h) / c - 1) * 100
    X = df.copy()
    cov = X.notna().mean()
    # 外部特征豁免覆盖率门槛：隔夜类天然只有 37~69% 覆盖
    # （美股/港股交易日历不同 + 滚动失效门会主动置空），走统一规则会被整批筛掉
    keep = (cov >= 0.60) | X.columns.str.startswith('XF_')
    X = X.loc[:, keep & (X.nunique(dropna=True) > 5)]
    rm = X.rolling(250, min_periods=60).mean()
    rs = X.rolling(250, min_periods=60).std()
    Xz = ((X - rm) / rs).clip(-5, 5).fillna(0.0)
    d = Xz.join(y_ret.rename('ret'))
    lab = d.dropna(subset=['ret'])
    sd = lab.drop(columns=['ret']).std()
    keep = sd[sd > 1e-6].index
    return Xz[keep], y_ret, lab


def mk_lgb(seed, n_est, depth, lr, mcs):
    import lightgbm as lgb
    return lgb.LGBMClassifier(n_estimators=n_est, max_depth=depth,
                              num_leaves=max(7, 2 ** depth - 1),
                              learning_rate=lr, subsample=0.8,
                              colsample_bytree=0.5, reg_lambda=3.0,
                              min_child_samples=mcs, n_jobs=1,
                              verbose=-1, random_state=seed)


def run(code, h, depth, seed=0, n_est=400, lr=0.05, mcs=30, target='tri'):
    t0 = time.time()
    X, y_ret, lab = build(code, h)
    # 三分类阈值（用训练样本分位，固定不随时间变）
    q1, q2 = lab['ret'].quantile(0.30), lab['ret'].quantile(0.70)

    def label(v):
        if v <= q1: return 0
        if v >= q2: return 2
        return 1

    y_all = y_ret.apply(lambda v: label(v) if pd.notna(v) else -1)
    idx = X.index
    score = pd.Series(np.nan, index=idx)
    n_fit = 0
    for pos in range(0, len(idx), RETRAIN):
        lo = max(0, pos - TRAIN_WIN)
        if pos - lo < MIN_TRAIN:
            continue
        seg_lab = y_all.iloc[lo:pos]
        ok = seg_lab >= 0
        if ok.sum() < MIN_TRAIN or seg_lab[ok].nunique() < 3:
            continue
        Xtr = X.iloc[lo:pos][ok.values].values
        ytr = seg_lab[ok].values.astype(int)
        m = mk_lgb(seed, n_est, depth, lr, mcs)
        m.fit(Xtr, ytr)
        seg = idx[pos:min(pos + RETRAIN, len(idx))]
        pc = m.predict_proba(X.loc[seg].values)
        score.loc[seg] = pc[:, 2] - pc[:, 0]
        n_fit += 1

    ev_pts = idx[::h]
    ev = pd.DataFrame({'score': score, 'ret': y_ret}).loc[ev_pts].dropna()
    ev = ev.loc['2019-01-01':]
    if len(ev) < 30:
        return None

    def st(d, sign=None):
        if not len(d):
            return dict(n=0)
        r = d['ret'] * np.sign(d['score'])
        n = len(r)
        w = int((r > 0).sum())
        p = binomtest(w, n, 0.5, alternative='greater').pvalue if n >= 8 else float('nan')
        return dict(n=n, hit=round(w / n, 4), p=round(p, 4) if p == p else None,
                    net=round(r.mean() - 0.2, 3), raw=round(d['ret'].mean(), 3))

    res = dict(code=code, name=H.IDX[code][0], h=h, depth=depth, target=target,
               n_fit=n_fit, n_eval=len(ev), cols=X.shape[1], elapsed_s=round(time.time() - t0, 1),
               base_up=round(float((ev['ret'] > 0).mean()), 4), bands={})
    for th in (0.0, 0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70):
        band = ev[ev['score'].abs() >= th]
        res['bands']['th%.2f' % th] = dict(
            all=st(band),
            is_=st(band.loc['2019-01-01':'2022-12-31']),
            oos=st(band.loc['2023-01-01':]),
        )
    tag = '%s_lgb_%s_d%d_T%d' % (code, target, depth, h)
    ev.to_csv(os.path.join(OUT, tag + '.csv'))
    with open(os.path.join(OUT, tag + '.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--h', type=int, default=5)
    ap.add_argument('--depth', type=int, default=3)
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    r = run(a.code, a.h, a.depth, a.seed)
    if not r:
        print('NO DATA')
    else:
        o = r['bands'].get('th0.50', {})
        print(json.dumps(dict(name=r['name'], h=r['h'], depth=r['depth'], cols=r['cols'],
                              n_eval=r['n_eval'], base=r['base_up'],
                              oos_th50=o.get('oos'), is_th50=o.get('is_'),
                              sec=r['elapsed_s']), ensure_ascii=False), flush=True)
