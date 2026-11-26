# -*- coding: utf-8 -*-
"""增强版：全历史(2004起) + 多尺度日度信息 + ETF 量能特征

针对用户重点：中证500、科创50
新增特征：
  1. 完整历史从 2004 起（原 2013 起，多 58% 样本）
  2. 多尺度日度（1/3/5/10/20/60/120 日动量、波动、分位、乖离、RSI、一致性）
  3. **ETF 量能**：
     - ETF 成交量/成交额相对分位（5/20/60日）
     - ETF 份额变化（申购赎回，fd_share）5/20 日变化率
     - ETF 溢价率（收盘价 vs 净值代理：用 close/pre_close 累积 vs 指数）
     - 成交额与指数涨跌的背离
  4. 期货基差（若有 fut 数据）

用法: python3 bin/wide_ens/enhanced.py --code 000905_SH --h 1 --depth 3
输出: .cache/wide_ens/enh/<code>_lgb_tri_d<depth>_T<h>.{json,csv}
"""
import os, sys, json, argparse, warnings, time
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt70 as H

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WS, '.cache/weekly_forecast/raw')
OUT = os.path.join(WS, '.cache/wide_ens/enh')
os.makedirs(OUT, exist_ok=True)

TRAIN_WIN, MIN_TRAIN, RETRAIN = 1000, 500, 20

ETF_MAP = {
    '000905_SH': ['etf500_daily.csv', 'etf500_share.csv'],
    '000852_SH': ['etf1000_daily.csv', 'etf1000_share.csv'],
    '000688_SH': ['etfkc50_daily.csv', 'etfkc50_share.csv'],
    '399006_SZ': ['etfcyb_daily.csv', None],
    '000300_SH': ['etf300_daily.csv', 'etf300_share.csv'],
}
FULL = {
    '000905_SH': 'idx_000905_SH_full.csv',
    '000852_SH': 'idx_000852_SH_full.csv',
    '000300_SH': 'idx_hs300.csv',
    '399006_SZ': 'idx_cyb.csv',
    '000688_SH': 'idx_kc50.csv',
}


def load_close_full(code):
    """优先用全历史文件"""
    fn = FULL.get(code)
    if fn and os.path.exists(os.path.join(RAW, fn)):
        d = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
        d['trade_date'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
        d = d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
        d = d[~d.index.duplicated(keep='last')]
        return pd.to_numeric(d['close'], errors='coerce').astype(float), d
    # 回退原函数
    c, _ = None, None
    d = pd.read_csv(os.path.join(RAW, H.IDX[code][1]), dtype={'trade_date': str})
    d['trade_date'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
    d = d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    return pd.to_numeric(d['close'], errors='coerce').astype(float), d


def multiscale(c):
    r1 = c.pct_change()
    F = {}
    for k in (1, 3, 5, 10, 20, 60, 120):
        F['MS_ret_%d' % k] = c / c.shift(k) - 1
    for k in (5, 10, 20, 60):
        F['MS_vol_%d' % k] = r1.rolling(k).std()
    for a, b in ((5, 20), (5, 60), (10, 60), (20, 120)):
        F['MS_vr_%d_%d' % (a, b)] = r1.rolling(a).std() / r1.rolling(b).std().replace(0, np.nan)
    for k in (5, 10, 20, 60, 120):
        F['MS_pct_%d' % k] = c.rolling(k).apply(
            lambda x: (x[-1] - x.min()) / (x.max() - x.min()) if x.max() > x.min() else 0.5, raw=True)
    for k in (5, 10, 20, 60, 120):
        F['MS_dev_%d' % k] = c / c.rolling(k).mean() - 1
    for k in (6, 14, 28):
        up = r1.clip(lower=0).rolling(k).mean()
        dn = (-r1.clip(upper=0)).rolling(k).mean().replace(0, np.nan)
        F['MS_rsi_%d' % k] = 100 - 100 / (1 + up / dn)
    mom = pd.DataFrame({k: F['MS_ret_%d' % k] for k in (1, 3, 5, 10, 20, 60)})
    F['MS_agree'] = (np.sign(mom) > 0).mean(axis=1)
    for a, b in ((1, 5), (3, 10), (5, 20), (10, 60)):
        F['MS_acc_%d_%d' % (a, b)] = F['MS_ret_%d' % a] - F['MS_ret_%d' % b]
    F['MS_range_20'] = ((c - c.rolling(20).min()) /
                        (c.rolling(20).max() - c.rolling(20).min()).replace(0, np.nan))
    return pd.DataFrame(F, index=c.index)


def etf_feats(code):
    """ETF 成交量 / 份额变化 / 相对强弱"""
    pair = ETF_MAP.get(code, [None, None])
    dfn, shn = pair[0], pair[1]
    F = {}
    if dfn and os.path.exists(os.path.join(RAW, dfn)):
        d = pd.read_csv(os.path.join(RAW, dfn), dtype={'trade_date': str})
        d['trade_date'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
        d = d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
        d = d[~d.index.duplicated(keep='last')]
        if 'vol' in d.columns:
            v = pd.to_numeric(d['vol'], errors='coerce')
        elif 'amount' in d.columns:
            v = pd.to_numeric(d['amount'], errors='coerce')
        else:
            v = None
        if v is not None and v.notna().sum() > 100:
            F['ETF_vol'] = v
            for k in (5, 20, 60):
                F['ETF_vol_ma%d' % k] = v.rolling(k).mean()
                F['ETF_vol_r%d' % k] = v / v.rolling(k).mean().replace(0, np.nan)
            for k in (20, 60):
                F['ETF_vol_pct%d' % k] = v.rolling(k).apply(
                    lambda x: (x[-1] - x.min()) / (x.max() - x.min()) if x.max() > x.min() else 0.5, raw=True)
            F['ETF_vol_chg5'] = v.pct_change(5)
            F['ETF_vol_chg20'] = v.pct_change(20)
        if 'amount' in d.columns:
            a = pd.to_numeric(d['amount'], errors='coerce')
            F['ETF_amt'] = a
            F['ETF_amt_r20'] = a / a.rolling(20).mean().replace(0, np.nan)
            F['ETF_amt_pct60'] = a.rolling(60).apply(
                lambda x: (x[-1] - x.min()) / (x.max() - x.min()) if x.max() > x.min() else 0.5, raw=True)
        # ETF 区间收益（跟踪误差的粗略代理）
        ec = pd.to_numeric(d['close'], errors='coerce')
        for k in (5, 20):
            F['ETF_ret_%d' % k] = ec / ec.shift(k) - 1
    if shn and os.path.exists(os.path.join(RAW, shn)):
        s = pd.read_csv(os.path.join(RAW, shn), dtype={'trade_date': str})
        s['trade_date'] = pd.to_datetime(s['trade_date'], format='%Y%m%d', errors='coerce')
        s = s.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
        s = s[~s.index.duplicated(keep='last')]
        if 'fd_share' in s.columns:
            sh = pd.to_numeric(s['fd_share'], errors='coerce')
            F['ETF_share'] = sh
            for k in (5, 20, 60):
                F['ETF_share_chg%d' % k] = sh.pct_change(k)
            F['ETF_share_z60'] = (sh - sh.rolling(60).mean()) / sh.rolling(60).std().replace(0, np.nan)
    if not F:
        return pd.DataFrame()
    return pd.DataFrame(F)


def build(code, h, use_pkg=True):
    c, raw = load_close_full(code)
    # 基础因子：包内111维 + orth34 + opt PCR（H.build 提供），但行情用全历史
    if use_pkg:
        _, dfp = H.build(code)
    else:
        dfp = pd.DataFrame(index=c.index)
    base = raw.copy().apply(pd.to_numeric, errors='coerce')
    base = base.drop(columns=[c2 for c2 in ('ts_code',) if c2 in base.columns])
    # 全历史行情 + 因子面板（因子面板历史较短，后面按覆盖率判断）
    X = pd.concat([base, dfp], axis=1)
    # 追加多尺度 + ETF（均基于全历史行情，2004 年起有）
    X = pd.concat([X, multiscale(c)], axis=1)
    ef = etf_feats(code)
    if len(ef):
        X = pd.concat([X, ef], axis=1)
    # 外部特征（T+1 隔夜带滚动门 / T+5 周度成交额动量）
    # ⚠️ 必须在覆盖率过滤之前挂上，且下方对 XF_ 列豁免「覆盖<85%」规则：
    #    隔夜因子实际覆盖率仅 37~69%（受美股交易日历 + 失效门影响），
    #    若走统一规则会被整批筛掉，等于没加。
    try:
        import extra_feats as _XF
        if 'c' not in X.columns:
            X['c'] = c.reindex(X.index)
        X = _XF.attach(X, code, h)
    except Exception as _e:
        print('[warn] extra_feats 挂载失败: %r' % (_e,), flush=True)
    y_ret = (c.shift(-h) / c - 1) * 100
    # ✅ 覆盖率按「该列首个有效日之后」算，避免长历史把短因子面板洗掉
    keep_cols = []
    for col in X.columns:
        s = X[col]
        if col == 'c':
            continue
        fv = s.first_valid_index()
        if fv is None:
            continue
        if col.startswith('XF_'):
            # 外部特征豁免覆盖率门槛（否则隔夜因子全被筛掉）
            if s.nunique(dropna=True) > 5:
                keep_cols.append(col)
            continue
        tail = s.loc[fv:]
        if len(tail) < 300:
            continue
        if tail.notna().mean() < 0.85:
            continue
        if s.nunique(dropna=True) <= 5:
            continue
        keep_cols.append(col)
    X = X[keep_cols]
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


def run(code, h, depth, seed=0, n_est=400, lr=0.05, mcs=30, is_start='2010-01-01', use_pkg=True):
    t0 = time.time()
    X, y_ret, lab = build(code, h, use_pkg=use_pkg)
    q1, q2 = lab['ret'].quantile(0.30), lab['ret'].quantile(0.70)

    def label(v):
        if v <= q1: return 0
        if v >= q2: return 2
        return 1

    y_all = y_ret.apply(lambda v: label(v) if pd.notna(v) else -1)
    idx = X.index
    score = pd.Series(np.nan, index=idx)
    imps = []
    n_fit = 0
    for pos in range(0, len(idx), RETRAIN):
        lo = max(0, pos - TRAIN_WIN)
        if pos - lo < MIN_TRAIN:
            continue
        seg_lab = y_all.iloc[lo:pos]
        ok = seg_lab >= 0
        if ok.sum() < MIN_TRAIN or seg_lab[ok].nunique() < 3:
            continue
        m = mk_lgb(seed, n_est, depth, lr, mcs)
        m.fit(X.iloc[lo:pos][ok.values].values, seg_lab[ok].values.astype(int))
        seg = idx[pos:min(pos + RETRAIN, len(idx))]
        pc = m.predict_proba(X.loc[seg].values)
        score.loc[seg] = pc[:, 2] - pc[:, 0]
        n_fit += 1
        imps.append(pd.Series(m.feature_importances_, index=X.columns))

    ev_pts = idx[::h]
    ev = pd.DataFrame({'score': score, 'ret': y_ret}).loc[ev_pts].dropna()
    ev = ev.loc[is_start:]
    if len(ev) < 50:
        return None

    def st(d):
        if not len(d):
            return dict(n=0)
        r = d['ret'] * np.sign(d['score'])
        n = len(r); w = int((r > 0).sum())
        p = binomtest(w, n, 0.5, alternative='greater').pvalue if n >= 8 else float('nan')
        return dict(n=n, hit=round(w / n, 4), p=round(p, 4) if p == p else None,
                    net=round(r.mean() - 0.2, 3), mean=round(d['ret'].mean(), 3))

    # 时间切分：前 60% IS，后 40% OOS
    dates = ev.index
    cut = dates[int(len(dates) * 0.6)]
    res = dict(code=code, name=H.IDX[code][0], h=h, depth=depth, n_fit=n_fit, use_pkg=use_pkg,
               n_eval=len(ev), cols=X.shape[1], cut=str(cut.date()),
               range='%s~%s' % (dates.min().date(), dates.max().date()),
               base_up=round(float((ev['ret'] > 0).mean()), 4),
               elapsed_s=round(time.time() - t0, 1), bands={})
    for th in (0.0, 0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80):
        b = ev[ev['score'].abs() >= th]
        res['bands']['th%.2f' % th] = dict(all=st(b), is_=st(b.loc[:cut]), oos=st(b.loc[cut:]))
    tag = '%s_lgb_tri_d%d_T%d%s' % (code, depth, h, '' if use_pkg else '_ohlcv')
    ev.to_csv(os.path.join(OUT, tag + '.csv'))
    if imps:
        imp = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
        imp.to_csv(os.path.join(OUT, tag + '.imp.csv'), header=['imp'])
        res['top15'] = {k: round(float(v), 3) for k, v in imp.head(15).items()}
    with open(os.path.join(OUT, tag + '.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--h', type=int, default=1)
    ap.add_argument('--depth', type=int, default=3)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--no-pkg', action='store_true', help='只用行情+多尺度+ETF（可回溯到2004）')
    a = ap.parse_args()
    r = run(a.code, a.h, a.depth, a.seed, use_pkg=not a.no_pkg)
    if not r:
        print('NO DATA', flush=True)
    else:
        b = r['bands']
        print(json.dumps(dict(name=r['name'], h=r['h'], d=r['depth'], cols=r['cols'],
                              n=r['n_eval'], range=r['range'], cut=r['cut'],
                              oos50=b['th0.50']['oos'], oos55=b['th0.55']['oos'],
                              sec=r['elapsed_s']), ensure_ascii=False), flush=True)
