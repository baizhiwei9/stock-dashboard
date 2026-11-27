# -*- coding: utf-8 -*-
"""单指数训练器（供多进程并行调用）

用法：
  python3 bin/wide_ens/train_one.py --code 000300_SH --model xgb --h 5 [--retrain 20] [--prob-thr 0.60]

输出：
  .cache/wide_ens/train/<code>_<model>_T<h>.json   指标
  .cache/wide_ens/train/<code>_<model>_T<h>.csv    逐日概率（非重叠评估点）
  .cache/wide_ens/train/<code>_<model>_T<h>.imp.csv  特征重要性
"""
import os, sys, json, argparse, warnings, time
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from scipy.stats import binomtest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hunt70 as H

OUT = __import__('wsenv').state_dir('.cache', 'wide_ens', 'train')
os.makedirs(OUT, exist_ok=True)


def fwd(c, h):
    return (c.shift(-h) / c - 1) * 100


def build_xy(code, h, start='2015-01-01', keep_tail=False):
    c, df = H.build(code, with_extra_h=h)
    y_ret = fwd(c, h)
    X = df.copy()
    cov = X.notna().mean()
    # 外部特征豁免覆盖率门槛：隔夜类天然只有 37~69% 覆盖
    # （美股/港股交易日历不同 + 滚动失效门会主动置空），走统一规则会被整批筛掉
    keep = (cov >= 0.60) | X.columns.str.startswith('XF_')
    X = X.loc[:, keep & (X.nunique(dropna=True) > 5)]
    rm = X.rolling(250, min_periods=60).mean()
    rs = X.rolling(250, min_periods=60).std()
    Xz = ((X - rm) / rs).clip(-5, 5).fillna(0.0)
    d = Xz.join(y_ret.rename('ret')).loc[start:]
    if not keep_tail:
        d = d.dropna(subset=['ret'])
    else:
        # live 模式：保留尾部（未来收益还没发生的日子），ret 可为 NaN
        d = d.loc[d.index >= pd.Timestamp(start)]
    Xo = d.drop(columns=['ret'])
    sd = Xo.std()
    Xo = Xo[sd[sd > 1e-6].index]
    return Xo, (d['ret'] > 0).astype(int), d['ret']


def mk_model(name, seed=0, n_est=400, depth=4, lr=0.05, mcs=30):
    if name == 'xgb':
        import xgboost as xgb
        return xgb.XGBClassifier(n_estimators=n_est, max_depth=depth, learning_rate=lr,
                                 subsample=0.8, colsample_bytree=0.5,
                                 reg_lambda=3.0, min_child_weight=mcs,
                                 eval_metric='logloss', n_jobs=1, verbosity=0,
                                 tree_method='hist', random_state=seed)
    if name == 'lgb':
        import lightgbm as lgb
        return lgb.LGBMClassifier(n_estimators=n_est, max_depth=depth, num_leaves=max(7, 2 ** depth - 1),
                                  learning_rate=lr, subsample=0.8,
                                  colsample_bytree=0.5, reg_lambda=3.0,
                                  min_child_samples=mcs, n_jobs=1,
                                  verbose=-1, random_state=seed)
    if name == 'lgbr':
        import lightgbm as lgb
        return lgb.LGBMRegressor(n_estimators=n_est, max_depth=depth, num_leaves=max(7, 2 ** depth - 1),
                                 learning_rate=lr, subsample=0.8,
                                 colsample_bytree=0.5, reg_lambda=3.0,
                                 min_child_samples=mcs, n_jobs=1,
                                 verbose=-1, random_state=seed)
    if name == 'logit':
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
        return Pipeline([('sc', StandardScaler()),
                         ('m', LogisticRegression(C=0.05, max_iter=1000))])
    raise ValueError(name)


def make_target(ret, mode):
    """返回 (y, is_reg)"""
    if mode == 'cls':
        return (ret > 0).astype(int), False
    if mode == 'tri':
        q1, q2 = ret.quantile(0.30), ret.quantile(0.70)
        y = pd.Series(1, index=ret.index)
        y[ret <= q1] = 0
        y[ret >= q2] = 2
        return y.astype(int), False
    if mode == 'reg':
        return ret, True
    raise ValueError(mode)


def run(code, model, h, train_win=750, min_train=500, retrain=20, seed=0,
        n_est=400, depth=4, lr=0.05, mcs=30, target='cls'):
    t0 = time.time()
    X, y_cls, r = build_xy(code, h)
    y, is_reg = make_target(r, target)
    idx = X.index
    proba = pd.Series(np.nan, index=idx)
    imps = []
    n_fit = 0
    for pos in range(0, len(idx), retrain):
        lo = max(0, pos - train_win)
        if pos - lo < min_train:
            continue
        Xtr = X.iloc[lo:pos].fillna(0.0)
        ytr = y.iloc[lo:pos]
        if len(Xtr) < min_train or (not is_reg and ytr.nunique() < 2):
            continue
        m = mk_model(model, seed, n_est, depth, lr, mcs)
        m.fit(Xtr.values, np.asarray(ytr.values, dtype=(float if is_reg else int)))
        seg = idx[pos:min(pos + retrain, len(idx))]
        Xs = X.loc[seg].fillna(0.0)
        if len(Xs):
            if is_reg:
                proba.loc[seg] = m.predict(Xs.values)
            elif target == 'tri':
                pc = m.predict_proba(Xs.values)
                # proba = P(up) - P(down)，范围 [-1,1]
                proba.loc[seg] = pc[:, 2] - pc[:, 0]
            else:
                proba.loc[seg] = m.predict_proba(Xs.values)[:, 1]
        n_fit += 1
        try:
            if hasattr(m, 'feature_importances_'):
                imps.append(pd.Series(m.feature_importances_, index=X.columns))
            elif hasattr(m, 'named_steps'):
                imps.append(pd.Series(np.abs(m.named_steps['m'].coef_[0]), index=X.columns))
        except Exception:
            pass

    # 非重叠评估点
    ev_pts = idx[::h]
    ev = pd.DataFrame({'proba': proba, 'ret': r}).loc[ev_pts]
    ev = ev.dropna(subset=['proba']).loc['2019-01-01':]
    if not len(ev):
        return None
    if is_reg:
        # 回归：用符号当方向
        ev['pred'] = (ev['proba'] >= 0).astype(int)
        center = 0.0
    elif target == 'tri':
        ev['pred'] = (ev['proba'] >= 0).astype(int)
        center = 0.0
    else:
        ev['pred'] = (ev['proba'] >= 0.5).astype(int)
        center = 0.5
    ev['win'] = (ev['pred'] == (ev['ret'] > 0)).astype(int)

    def seg_stat(d):
        n = len(d)
        if n == 0:
            return None
        w = int(d['win'].sum())
        p = binomtest(w, n, 0.5, alternative='greater').pvalue if n >= 8 else float('nan')
        return dict(n=n, hit=round(w / n, 4), p=round(p, 4) if p == p else None,
                    mean_ret=round(d['ret'].mean(), 3),
                    net=round(d['ret'].mean() - 0.2, 3))

    # 高置信度子集（proba 越高越准）
    res = dict(code=code, name=H.IDX[code][0], model=model, h=h, target=target,
               hp=dict(n_est=n_est, depth=depth, lr=lr, mcs=mcs, win=train_win, retrain=retrain),
               n_fit=n_fit, n_eval=len(ev), rows=len(X), cols=X.shape[1],
               elapsed_s=round(time.time() - t0, 1),
               base_up=round((ev['ret'] > 0).mean(), 4),
               all=seg_stat(ev),
               is_=seg_stat(ev.loc['2019-01-01':'2022-12-31']),
               oos=seg_stat(ev.loc['2023-01-01':]),
               yearly={str(y): seg_stat(ev.loc[str(y)]) for y in range(2019, 2027)
                       if len(ev.loc[str(y):str(y)])},
               by_thr={})
    scale = 0.10 if is_reg else 0.10   # 相对 center 的偏移量（回归用绝对值，分类用概率）
    for thr in (0.55, 0.60, 0.65, 0.70):
        if is_reg or target == 'tri':
            d_hi, d_lo = thr, -thr
            hi = ev[ev['proba'] >= (d_hi if is_reg else 0.10)]
            lo = ev[ev['proba'] <= (d_lo if is_reg else -0.10)]
        else:
            hi = ev[ev['proba'] >= thr]
            lo = ev[ev['proba'] <= 1 - thr]
        res['by_thr']['hi_%.2f' % thr] = seg_stat(hi)
        res['by_thr']['lo_%.2f' % thr] = seg_stat(lo)
    # 高置信度净收益（多空各自）
    if is_reg or target == 'tri':
        hi = ev[ev['proba'] >= 0.10]
        lo = ev[ev['proba'] <= -0.10]
    else:
        hi = ev[ev['proba'] >= 0.60]
        lo = ev[ev['proba'] <= 0.40]
    res['long_only_060'] = dict(n=len(hi), hit=round((hi['ret'] > 0).mean(), 4) if len(hi) else None,
                                 mean=round(hi['ret'].mean(), 3) if len(hi) else None)
    res['short_only_040'] = dict(n=len(lo), hit=round((lo['ret'] < 0).mean(), 4) if len(lo) else None,
                                 mean=round(-lo['ret'].mean(), 3) if len(lo) else None)

    tag = '%s_%s_%s_d%d_T%d' % (code, model, target, depth, h)
    ev.to_csv(os.path.join(OUT, tag + '.csv'))
    if imps:
        imp = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
        imp.to_csv(os.path.join(OUT, tag + '.imp.csv'), header=['importance'])
        res['top10'] = {k: round(float(v), 4) for k, v in imp.head(10).items()}
    with open(os.path.join(OUT, tag + '.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--model', default='xgb')
    ap.add_argument('--h', type=int, default=5)
    ap.add_argument('--retrain', type=int, default=20)
    ap.add_argument('--train-win', type=int, default=750)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--n-est', type=int, default=400)
    ap.add_argument('--depth', type=int, default=4)
    ap.add_argument('--lr', type=float, default=0.05)
    ap.add_argument('--mcs', type=int, default=30)
    ap.add_argument('--target', default='cls', choices=['cls', 'tri', 'reg'])
    a = ap.parse_args()
    out = run(a.code, a.model, a.h, train_win=a.train_win, retrain=a.retrain, seed=a.seed,
              n_est=a.n_est, depth=a.depth, lr=a.lr, mcs=a.mcs, target=a.target)
    if out:
        print(json.dumps({k: out[k] for k in ('name', 'model', 'target', 'h', 'n_eval', 'base_up', 'all', 'is_', 'oos', 'elapsed_s')},
                         ensure_ascii=False), flush=True)
    else:
        print('NO DATA', flush=True)
