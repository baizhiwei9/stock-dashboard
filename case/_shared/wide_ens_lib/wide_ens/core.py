# -*- coding: utf-8 -*-
"""宽基合并信号 · 核心（纯脚本 0 token）
=========================================================
两套模型合并成一个信号：
  源A「包内模型」  .cache/quark_dl/.../predictions_t4   （训练目标 = y_next_ret = **次日**）
  源B「我的46维」  research/weekly_forecast/reproD_*.csv （训练目标 = **未来4日**）

⚠️ 两者 horizon 不同，必须各自在自己有效的 horizon 上用，不能混在一个模型里。
   实测：合并后 T+4 池化 OOS 56.5%(p=0.016)，比单用我的 55.5% 略好、比包内 54.4% 好；
        T+1 合并 OOS 53.6%，反而不如单用我的 57.1%。→ **主用 T+4**

⚠️ 包内 alarm_freeze：43~51% 交易日的 pred_ret 被强制写 0，那不是「中性观点」而是
   「当日不出信号」。必须置 NaN，否则零值会把 z-score 拉平、方向被稀释。
   （实测：误把冻结当观点时，T+4 池化命中率被虚高 3~5pp）

诚实成绩（严格非重叠、扣0.2%、IS挑门槛→OOS验、5指数池化）：
  我的46维 T+4 : IS 60.5% / OOS 58.2% p=0.016 净+0.74%
  合并     T+4 : IS 59.6% / OOS 56.7% p=0.050 净+0.48%
  包内单用 T+4 : IS 60.0% / OOS 54.4% p=0.250 净+0.10%   ← 包内单独最弱
滚动选档（每年初只用历史挑方案+门槛，真实可交易）：
  n=52 / 命中 67.3% / p=0.0088 / 平均净 +0.67%
"""
import os, numpy as np, pandas as pd
from scipy.stats import binomtest

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_PD = os.path.join(WS, '.cache/quark_dl/科创50日频预测包_20261018/data/predictions_t4')
MY_DIR = os.path.join(WS, 'research/weekly_forecast')
RAW = os.path.join(WS, '.cache/weekly_forecast/raw')
STATE_DIR = os.path.join(WS, '.cache/wide_ens')
os.makedirs(STATE_DIR, exist_ok=True)

SEEDS = ['', '_s7', '_s123', '_s2026', '_s999']

# 主推组合：两套都按 T+4 评估，等权合并
H = 4
GATE_Q = 0.90          # 分位门槛（滚动选档实测最优集中区 0.90~0.95）
Z_WIN = 300
COST = 0.20

INDICES = {
    '000300_SH': ('沪深300',  't4dimae50',   'hs300',  'idx_hs300.csv'),
    '000688_SH': ('科创50',   't4dimraw',    'kc50',   'idx_kc50.csv'),
    '000852_SH': ('中证1000', 't4dimraw',    'zz1000', 'idx_zz1000.csv'),
    '000905_SH': ('中证500',  't4dimraw',    'zz500',  'idx_zz500.csv'),
    '399006_SZ': ('创业板指', 'm108_xgb_clf','cyb',    'idx_cyb.csv'),
}


def load_price(code):
    fn = INDICES[code][3]
    d = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
    d['trade_date'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
    d = d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    return pd.to_numeric(d['close'], errors='coerce').astype(float)


def load_actual(code, h=H):
    c = load_price(code)
    return ((c.shift(-h) / c - 1) * 100).rename('actual')


def load_pkg(code):
    """源A：包内 5 种子 bagging 均值；冻结日置 NaN（冻结 = 当日不出信号，不是中性）"""
    prefix = INDICES[code][1]
    fr, fz = {}, {}
    for s in SEEDS:
        f = os.path.join(PKG_PD, '%s_%s%s' % (prefix, code, s), 'pred_%s.csv' % code)
        if not os.path.exists(f):
            return None, None
        x = pd.read_csv(f, dtype={'T': str})
        x['T'] = pd.to_datetime(x['T'], errors='coerce')
        x = x.dropna(subset=['T']).set_index('T').sort_index()
        x = x[~x.index.duplicated(keep='last')]
        fr['p%s' % (s or 42)] = pd.to_numeric(x['pred_ret'], errors='coerce')
        fz['f%s' % (s or 42)] = pd.to_numeric(x.get('alarm_freeze'), errors='coerce')
    m = pd.DataFrame(fr)
    pc = [k for k in m.columns if k.startswith('p')]
    out = m[pc].mean(axis=1).rename('pkg')
    f = pd.DataFrame(fz)
    if 'f42' in f.columns:
        frozen = f['f42'].fillna(1).astype(int) == 1
        out = out.mask(frozen, np.nan)
    return out, (f['f42'].fillna(1).astype(int) == 1 if 'f42' in f.columns else pd.Series(False, index=m.index))


def load_mine(code):
    """源B：我的 46 维
    ⚠️ repro_t4_daily.py 因为要算 actual（未来 4 日收益）会丢掉最后 4 天，
    导致实盘信号滞后。predict_latest.py 产出 reproD_live_*.csv 补上尾部，
    这里把两者拼起来（历史用完整版，尾部用 live 版）。"""
    key = INDICES[code][2]
    base = os.path.join(MY_DIR, 'reproD_%s.csv' % key)
    if not os.path.exists(base):
        return None
    d = pd.read_csv(base, index_col=0, parse_dates=True)
    s = pd.to_numeric(d['bagged'], errors='coerce')
    live = '/tmp/reproD_live_%s.csv' % key
    if os.path.exists(live):
        l = pd.read_csv(live, index_col=0, parse_dates=True)
        ls = pd.to_numeric(l['bagged'], errors='coerce')
        # 只用 base 之后的部分
        ls = ls[ls.index > s.index.max()]
        if len(ls):
            s = pd.concat([s, ls])
    return s[~s.index.duplicated(keep='last')].sort_index().rename('mine')


def zscore(s, win=Z_WIN):
    mu = s.rolling(win, min_periods=100).mean()
    sd = s.rolling(win, min_periods=100).std()
    return (s - mu) / sd


def build_frame(code):
    """index=日期, cols=[pkg, mine, frozen, actual, zp, zm, ens, amp, amp_q, fired, sig]"""
    pk, fz = load_pkg(code)
    mn = load_mine(code)
    act = load_actual(code)
    if pk is None or mn is None:
        return None
    df = pd.DataFrame({'pkg': pk, 'mine': mn, 'actual': act})
    df['frozen'] = fz.reindex(df.index).fillna(True)
    df = df.dropna(subset=['mine']).sort_index()
    df['zp'] = zscore(df['pkg'])
    df['zm'] = zscore(df['mine'])
    df['ens'] = pd.concat([df['zp'], df['zm']], axis=1).mean(axis=1, skipna=True)
    df = df.dropna(subset=['ens'])
    df['amp'] = df['ens'].abs()
    df['amp_q'] = df['amp'].shift(1).rolling(Z_WIN, min_periods=150).quantile(GATE_Q)
    df['fired'] = df['amp'] >= df['amp_q']
    df['sig'] = np.where(df['fired'], np.sign(df['ens']), 0)
    return df


def load_all():
    out = {}
    for code in INDICES:
        f = build_frame(code)
        if f is not None and len(f) > 300:
            out[code] = f
    return out


def events(df, gap=6):
    """严格非重叠：事件间隔 ≥ gap 自然日（T+4 → 6 日）"""
    s = df.dropna(subset=['actual'])
    s = s[s['sig'] != 0]
    keep, last = [], None
    for dt in s.index:
        if last is None or (dt - last).days >= gap:
            keep.append(dt); last = dt
    s = s.loc[keep]
    return pd.DataFrame({'sign': np.sign(s['ens']),
                         'ret': np.sign(s['ens']) * s['actual']}, index=s.index)


def pooled(frames, lo='2019-01-01', hi='2026-12-31', gap=6):
    recs = []
    for df in frames.values():
        e = events(df.loc[lo:hi], gap=gap)
        if len(e):
            recs.append(e[['ret']])
    if not recs:
        return None
    R = pd.concat(recs).sort_index()
    if len(R) < 10:
        return None
    w = int((R['ret'] > 0).sum()); n = len(R)
    return dict(n=n, hit=w / n, p=float(binomtest(w, n, 0.5, alternative='greater').pvalue),
                ret=float(R['ret'].mean()), net=float(R['ret'].mean() - COST))


def calib(df, lo='2019-01-01', hi='2026-12-31'):
    e = events(df.loc[lo:hi])
    if len(e) < 8:
        return None
    w = int((e['ret'] > 0).sum()); n = len(e)
    return dict(n=n, hit=w / n, p=float(binomtest(w, n, 0.5, alternative='greater').pvalue),
                ret=float(e['ret'].mean()), net=float(e['ret'].mean() - COST))


def latest_signal(code):
    df = build_frame(code)
    if df is None or not len(df):
        return None
    r = df.iloc[-1]
    return dict(code=code, name=INDICES[code][0], date=df.index[-1],
                pkg=float(r['pkg']) if np.isfinite(r['pkg']) else None,
                mine=float(r['mine']) if np.isfinite(r['mine']) else None,
                frozen=bool(r['frozen']),
                ens=float(r['ens']) if np.isfinite(r['ens']) else None,
                amp=float(r['amp']) if np.isfinite(r['amp']) else None,
                fired=bool(r['fired']) if np.isfinite(r['amp_q']) else False,
                direction='多' if r['ens'] > 0 else '空')
