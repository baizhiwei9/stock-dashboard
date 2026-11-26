# -*- coding: utf-8 -*-
"""五指数方向预测 · 核心库（纯脚本，0 token）

设计原则
--------
1. **只信方向，不信模型自报的幅度**（实测幅度 IC≈0，区间标定失效）
2. **置信度 = 历史上同档位的真实胜率**（经验校准，不是模型自报的分数）
3. **预期幅度 = 历史上同档位的真实平均收益**（同上，非模型外推）
4. **可交易 = 胜率 + 幅度 + 样本量三者同时达标**

目标：T+H 日方向（H=4，约一周）
"""
import os, sys, warnings
warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
RAW = os.path.join(ROOT, '.cache', 'weekly_forecast', 'raw')
STATE_DIR = os.path.join(ROOT, '.cache', 'wide_daily')
os.makedirs(STATE_DIR, exist_ok=True)

import numpy as np
import pandas as pd

H = int(os.environ.get('WIDE_H', '4'))          # 预测周期（交易日）
SEEDS = [42, 7, 2026, 123, 999]
WIN = 1500                                       # 训练窗口
RETRAIN = 20                                     # 每 N 日重训
MIN_TRAIN = 300

INDICES = [
    ('000300_SH', '沪深300', 'idx_hs300.csv'),
    ('000905_SH', '中证500', 'idx_zz500.csv'),
    ('000852_SH', '中证1000', 'idx_zz1000.csv'),
    ('399006_SZ', '创业板指', 'idx_cyb.csv'),
    ('000688_SH', '科创50', 'idx_kc50.csv'),
]


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def load_index(fn):
    p = os.path.join(RAW, fn)
    if not os.path.exists(p):
        return None
    d = pd.read_csv(p, dtype={'trade_date': str})
    d['trade_date'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
    d = d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    return d


def load_macro():
    """利差/QVIX 因子（可缺失，缺失则该组特征为 NaN → 填 0）"""
    p = os.path.join(RAW, 'spread_factors.csv')
    if not os.path.exists(p):
        return pd.DataFrame()
    return pd.read_csv(p, index_col=0, parse_dates=True)


MACRO_COLS = ['ts_10y1y', 'ts_10y1y_p250', 'cn_cs_3y', 'cn_cs_3y_p250',
              'us_cs_baa10y', 'us_cs_baa10y_p250', 'gz_10y', 'cn_us_10y',
              'qvix300', 'qvix500', 'iv_atm300', 'skew300']


def build_features(d, sp=None, code=None, h=None):
    """技术特征 + 宏观利差特征。全部 T 日收盘可知，无未来函数。

    code/h 给定时额外挂上外部特征（见 bin/wide_ens/extra_feats.py）：
      T+1 ← 隔夜跨市场（A50/恒生/恒生科技/中概 ADR，带滚动失效门）
      T+5 ← 周度成交额动量 w_amt_mom4
    环境变量 EXTRA_FEATS=0 可关闭（回滚 / A-B 对照）。
    """
    c = pd.to_numeric(d['close'], errors='coerce').astype(float)
    o = pd.to_numeric(d['open'], errors='coerce').astype(float)
    hi = pd.to_numeric(d['high'], errors='coerce').astype(float)
    lo = pd.to_numeric(d['low'], errors='coerce').astype(float)
    vol = pd.to_numeric(d['vol'], errors='coerce').astype(float)
    amt = pd.to_numeric(d['amount'], errors='coerce').astype(float)
    r = c.pct_change() * 100

    X = pd.DataFrame(index=c.index)
    X['c'] = c
    for n in [1, 2, 3, 5, 10, 20, 60]:
        X[f'mom{n}'] = c / c.shift(n) - 1
    for n in [5, 10, 20, 60]:
        X[f'ma{n}'] = c / c.rolling(n).mean() - 1
    X['rsi14'] = rsi(c)
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    X['macd'] = (dif - dea) * 2 / c
    X['vol_r'] = vol / vol.rolling(5).mean()
    X['vol_r20'] = vol / vol.rolling(20).mean()
    X['amt_r'] = amt / amt.rolling(20).mean()
    X['dvol20'] = r.rolling(20).std()
    X['dvol5'] = r.rolling(5).std()
    X['up5'] = (r > 0).rolling(5).mean()
    X['up20'] = (r > 0).rolling(20).mean()
    X['amp'] = (hi - lo) / c.shift(1) * 100
    X['amp20'] = X['amp'].rolling(20).mean()
    rng = hi.rolling(20).max() - lo.rolling(20).min()
    X['hl_pos'] = (c - lo.rolling(20).min()) / rng.replace(0, np.nan)
    X['pos52'] = c / c.rolling(250).max() - 1
    X['dd52'] = c / c.rolling(250).max() - 1
    X['gap'] = (o - c.shift(1)) / c.shift(1) * 100
    X['vr'] = r.abs() / vol.rolling(20).mean()
    up = (r > 0).astype(int)
    X['streak'] = up.groupby((up != up.shift()).cumsum()).cumcount() + 1

    if sp is not None and len(sp):
        for col in MACRO_COLS:
            if col in sp.columns:
                X[col] = sp[col].reindex(X.index.union(sp.index)).ffill().reindex(X.index)

    if code is not None and h is not None:
        try:
            sys.path.insert(0, os.path.join(ROOT, 'bin', 'wide_ens'))
            import extra_feats as _XF
            X = _XF.attach(X, code, h)
        except Exception as _e:
            print('[warn] extra_feats 挂载失败: %r' % (_e,), flush=True)

    return X.dropna(subset=['c'])


def feature_cols(X):
    return [c for c in X.columns if c not in ('c', 'y_next')]


def make_xy(d, sp, code=None, h=None):
    X = build_features(d, sp, code=code, h=(h if h is not None else H))
    c = X['c']
    y = (c.shift(-H) / c - 1) * 100          # 未来 H 日收益 %
    cols = feature_cols(X)
    Xm = np.nan_to_num(
        X[cols].astype(float).replace([np.inf, -np.inf], np.nan).values,
        nan=0.0, posinf=0.0, neginf=0.0)
    return Xm, y.values, X.index, cols


def fit_one(Xtr, ytr, seed):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(learning_rate=0.05, n_estimators=200, max_depth=4,
                          num_leaves=15, subsample=0.8, colsample_bytree=0.8,
                          random_state=seed, verbose=-1, n_jobs=2)
    m.fit(Xtr, ytr)
    return m


def save_models(path, models):
    import pickle
    with open(path, 'wb') as f:
        pickle.dump(models, f)


def load_saved_models(path):
    import pickle
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return pickle.load(f)


def email(subject, body, to=None, markdown=True):
    """走 scripts/send_email.py（163 通道）"""
    import subprocess, tempfile
    script = os.path.join(ROOT, 'scripts', 'send_email.py')
    if not os.path.exists(script):
        print('[email] send_email.py 不存在，跳过')
        return False
    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(body)
        bf = f.name
    cmd = [sys.executable, script, '--subject', subject, '--body-file', bf]
    if to:
        cmd += ['--to', to]
    if markdown:
        cmd += ['--html']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0
        print('[email] %s %s' % ('成功' if ok else '失败', (r.stdout or r.stderr).strip()[:200]))
        return ok
    except Exception as e:
        print('[email] 异常 %s' % e)
        return False
    finally:
        try:
            os.unlink(bf)
        except Exception:
            pass
