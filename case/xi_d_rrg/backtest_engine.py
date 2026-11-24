# -*- coding: utf-8 -*-
"""成长价值轮动的全景式框架 —— 复现引擎 v2"""
import os, glob
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')

# ---------------- 基础加载 ----------------
def load_index():
    g = pd.read_csv(os.path.join(DATA, 'index_399370.csv'), dtype={'trade_date': str})
    v = pd.read_csv(os.path.join(DATA, 'index_399371.csv'), dtype={'trade_date': str})
    df = g[['trade_date', 'close', 'amount']].merge(
        v[['trade_date', 'close', 'amount']], on='trade_date', suffixes=('_g', '_v'))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df[['trade_date', 'close_g', 'close_v', 'amount_g', 'amount_v']].sort_values('trade_date').reset_index(drop=True)

def load_weights():
    g = pd.read_csv(os.path.join(DATA, 'weights_growth.csv'))
    v = pd.read_csv(os.path.join(DATA, 'weights_value.csv'))
    return g, v

# ---------------- 周度基础 ----------------
def weekly_base(idx, start='20191230', end='20260822'):
    df = idx[(idx['trade_date'] >= pd.Timestamp(start)) & (idx['trade_date'] <= pd.Timestamp(end))].copy()
    wk = df.set_index('trade_date').resample('W-FRI').agg(
        last_date=('close_g', 'last'),
        close_g=('close_g', 'last'), close_v=('close_v', 'last'),
        amt_g=('amount_g', 'sum'), amt_v=('amount_v', 'sum')).dropna()
    # last_date 修正为每周最后一个实际交易日
    ld = df.groupby(df['trade_date'].dt.to_period('W-FRI'))['trade_date'].max()
    wk['last_date'] = [ld.get(p, wk['last_date'].iloc[i]) for i, p in enumerate(wk.index.to_period('W-FRI'))]
    wk['g_ret'] = wk['close_g'] / wk['close_g'].shift(1) - 1
    wk['v_ret'] = wk['close_v'] / wk['close_v'].shift(1) - 1
    wk['xs_ret'] = wk['g_ret'] - wk['v_ret']
    wk = wk.reset_index().rename(columns={'trade_date': 'week_end'})
    wk['week'] = wk['week_end'].dt.to_period('W-FRI')
    return wk

def safe_sign(x, fill=0.0):
    s = pd.Series(np.sign(x), index=x.index)
    return s.replace({np.nan: fill})

# ---------------- S1/S2 交易维度 ----------------
def sig_trade(wk, k_ret=8, k_amt=4):
    wk['xs_cum'] = wk['xs_ret'].rolling(k_ret, min_periods=2).sum()
    wk['s1'] = wk['xs_cum'].diff()
    wk['s1'] = np.where(np.isnan(wk['s1']), wk['xs_cum'], wk['s1'])
    wk['s1'] = np.sign(wk['s1'])
    amt_share = wk['amt_g'] / (wk['amt_g'] + wk['amt_v'])
    wk['s2'] = np.sign(amt_share - amt_share.shift(k_amt))
    return wk

# ---------------- 个股周宽表 ----------------
def load_stock_weekly(kind, factor_fn, agg='mean'):
    """读取 data/{kind}/*.csv, 返回宽表 df[week(Period)] -> code 列"""
    files = sorted(glob.glob(os.path.join(DATA, kind, '*.csv')))
    rows = []
    for fp in files:
        code = os.path.basename(fp)[:-4].replace('_', '.')
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if df.empty or 'trade_date' not in df.columns:
            continue
        df['trade_date'] = df['trade_date'].astype(str)
        df = df[df['trade_date'] >= '20200101']
        fv = factor_fn(df)
        if fv is None or len(fv) == 0:
            continue
        out = pd.DataFrame({'trade_date': fv.index, code: fv.values})
        rows.append(out)
    if not rows:
        return pd.DataFrame()
    big = pd.concat(rows, ignore_index=True)
    big['trade_date'] = pd.to_datetime(big['trade_date'])
    big['week'] = big['trade_date'].dt.to_period('W-FRI')
    wide = big.drop(columns='trade_date').groupby('week').agg(agg if agg == 'mean' else 'mean')
    if agg == 'sum':
        wide = big.drop(columns='trade_date').groupby('week').sum()
    return wide

def weight_lookup(g_w, v_w, weeks):
    """两周度权重表: 对每个周, 用<=该周快照的最近权重. 返回 (idx_g, idx_v) 长表字典
    key: (week) -> DataFrame[code, weight]"""
    def build(w, wt):
        w = w.copy(); wt = wt.copy()
        wt['trade_date'] = pd.to_datetime(wt['trade_date'])
        wt['week'] = wt['trade_date'].dt.to_period('W-FRI')
        w['week'] = pd.to_datetime(w['trade_date']).dt.to_period('W-FRI')
        w = w.groupby(['week', 'con_code'], as_index=False)['weight'].last()
        # 为每周找<=它的最近快照周
        snaps = sorted(wt['week'].unique())
        mapping = {}
        j = 0
        for wk in weeks:
            wks = [s for s in snaps if s <= wk]
            if wks:
                mapping[wk] = wks[-1]
            else:
                mapping[wk] = None
        return w, mapping
    wg, mg = build(g_w, g_w)
    wv, mv = build(v_w, v_w)
    return (wg, mg), (wv, mv)

def weekly_index_series(wide, wtable):
    """宽表(week x code) -> 指标周序列(加权平均)"""
    w, mapping = wtable
    out = pd.Series(index=wide.index, dtype=float)
    for wk in wide.index:
        snap = mapping.get(wk)
        if snap is None:
            continue
        wt = w[w['week'] == snap].set_index('con_code')['weight']
        common = wide.columns.intersection(wt.index)
        if len(common) == 0:
            continue
        vals = wide.loc[wk, common].astype(float)
        ws = wt[common].astype(float)
        out[wk] = np.average(vals, weights=ws) if ws.sum() > 0 else np.nan
    return out

def index_diff_series(wide_g, wg, wide_v, wv, delta_only=False, k=4):
    """成长-价值 周序列(或其方向)"""
    gs = weekly_index_series(wide_g, wg)
    vs = weekly_index_series(wide_v, wv)
    d = (gs - vs).replace([np.inf, -np.inf], np.nan)
    if delta_only:
        return np.sign(d.diff(k)).replace({np.nan: 0})
    return d

# ---------------- S3/S4 资金流 ----------------
def _mf_factor(df):
    df['big_net'] = df['buy_elg_amount'].fillna(0) + df['buy_lg_amount'].fillna(0) - df['sell_elg_amount'].fillna(0) - df['sell_lg_amount'].fillna(0)
    df['big_tot'] = (df['buy_elg_amount'].fillna(0) + df['buy_lg_amount'].fillna(0)
                     + df['sell_elg_amount'].fillna(0) + df['sell_lg_amount'].fillna(0))
    s = df.set_index('trade_date')['big_net'] / df.set_index('trade_date')['big_tot'].replace(0, np.nan)
    return s

def _margin_factor(df):
    s = df.set_index('trade_date')['rzmre']
    return s

def sig_moneyflow(g_w, v_w, wk_weeks, k=4):
    print('load moneyflow ...', flush=True)
    mf_g = load_stock_weekly('moneyflow', _mf_factor, agg='mean') if os.path.isdir(os.path.join(DATA, 'moneyflow')) else pd.DataFrame()
    mf_v = mf_g
    (wg, mg), (wv, mv) = weight_lookup(g_w, v_w, wk_weeks)
    s3 = index_diff_series(mf_g, (wg, mg) if mf_g is not None else wg, mf_v, (wv, mv) if mf_v is not None else wv, delta_only=True, k=k) if not mf_g.empty else pd.Series()
    print('load margin ...', flush=True)
    mgm = load_stock_weekly('margin', _margin_factor, agg='sum') if os.path.isdir(os.path.join(DATA, 'margin')) else pd.DataFrame()
    s4 = index_diff_series(mgm, (wg, mg), mgm, (wv, mv), delta_only=True, k=k) if not mgm.empty else pd.Series()
    return s3, s4

# ---------------- S5/S6 基本面 ----------------
def _income_factor(df):
    """净利润同比: 用最新报告期(ann<=当周日需要asof; 简化: 取report_type=1, 按end_date计算同比)"""
    cols = [c for c in ['end_date', 'ann_date', 'report_type', 'n_income'] if c in df.columns]
    d = df[cols].copy()
    d['end_date'] = d['end_date'].astype(str)
    d = d[d['end_date'].str.match(r'^\d{8}$')]
    d['report_type'] = d['report_type'].astype(str)
    d = d[d['report_type'] == '1']
    d['period'] = d['end_date'].str[:6]
    d['n_income'] = pd.to_numeric(d['n_income'], errors='coerce')
    # 每年同期: 用同一period(月)的去年值
    d['year'] = d['period'].str[:4].astype(int)
    d['mon'] = d['period'].str[4:]
    d = d.sort_values(['year', 'mon'])
    d['prev'] = d.groupby('mon')['n_income'].shift(1)
    d['yoy'] = d['n_income'] / d['prev'] - 1
    # 只保留最新披露(yoy>0 且 prev>0), 输出 index=end_date
    d = d.dropna(subset=['yoy'])
    last = d.groupby('end_date')['yoy'].last()
    return last

def _income_daily(df):
    """把上面yoy按披露日映射为每日可用值(披露规则: 季报截止后1月, 简化用ann_date)"""
    cols = [c for c in ['end_date', 'ann_date', 'report_type', 'n_income'] if c in df.columns]
    d = df[cols].copy()
    d['end_date'] = d['end_date'].astype(str)
    d['ann_date'] = d['ann_date'].astype(str)
    d = d[d['end_date'].str.match(r'^\d{8}$')]
    d['report_type'] = d['report_type'].astype(str)
    d = d[d['report_type'] == '1']
    d['n_income'] = pd.to_numeric(d['n_income'], errors='coerce')
    d['period'] = d['end_date'].str[:6]
    d['year'] = d['period'].str[:4].astype(int)
    d['mon'] = d['period'].str[4:]
    d = d.sort_values(['year', 'mon'])
    d['prev'] = d.groupby('mon')['n_income'].shift(1)
    d['yoy'] = d['n_income'] / d['prev'] - 1
    d = d.dropna(subset=['yoy'])
    # 用ann_date作为生效日(若缺失用end_date+45天)
    d['eff'] = pd.to_datetime(d['ann_date'], errors='coerce').fillna(
        pd.to_datetime(d['end_date']) + pd.Timedelta(days=45))
    s = d.set_index('eff')['yoy'].sort_index()
    return s

def _dv_factor(df):
    return df.set_index('trade_date')['dv_ratio']

def sig_fundamental(g_w, v_w, wk_weeks, k=4):
    """S5 净利润增速差方向; S6 股息比(价值/成长)低->看多成长"""
    income_dir = os.path.join(DATA, 'income')
    basic_dir = os.path.join(DATA, 'daily_basic')
    if os.path.isdir(income_dir):
        print('load income ...', flush=True)
        iw_g = load_stock_weekly('income', _income_daily, agg='mean')
        iw_v = iw_g
        # 注意: income是月度披露的稀疏数据, 宽表周度聚合需asof处理 -> 这里用每日效期再asof
        # 简化: 直接用宽表(周)合并
        (wg, mg), (wv, mv) = weight_lookup(g_w, v_w, wk_weeks)
        s5 = index_diff_series(iw_g.dropna(how='all'), (wg, mg), iw_v.dropna(how='all'), (wv, mv), delta_only=True, k=1) if not iw_g.empty else pd.Series()
    else:
        s5 = pd.Series()
    if os.path.isdir(basic_dir):
        print('load daily_basic ...', flush=True)
        dg = load_stock_weekly('daily_basic', _dv_factor, agg='mean') if os.path.isdir(basic_dir) else pd.DataFrame()
        (wg, mg), (wv, mv) = weight_lookup(g_w, v_w, wk_weeks)
        gs = weekly_index_series(dg.dropna(how='all'), (wg, mg)) if not dg.empty else pd.Series()
        vs = weekly_index_series(dg.dropna(how='all'), (wv, mv)) if not dg.empty else pd.Series()
        ratio = (vs / gs).replace([np.inf, -np.inf], np.nan)
        s6 = np.sign(ratio - ratio.rolling(k).mean())  # 比值低于均值 -> 看多成长
        s6 = pd.Series(s6, index=ratio.index).replace({np.nan: 0})
    else:
        s6 = pd.Series()
    return s5, s6

# ---------------- S7/S8/S9 宏观 ----------------
def load_macro():
    out = {}
    cpi = pd.read_csv(os.path.join(DATA, 'macro_cpi.csv'))
    ppi = pd.read_csv(os.path.join(DATA, 'macro_ppi.csv'))
    for df, key, nm in [(ppi, '中国PPI年率报告', 'ppi'), (cpi, '中国CPI月率报告', 'cpi')]:
        d = df[df['商品'] == key][['日期', '今值']].copy()
        d['日期'] = pd.to_datetime(d['日期'])
        out[nm] = d.set_index('日期')['今值'].astype(float).sort_index()
    m = pd.read_csv(os.path.join(DATA, 'macro_m1m2.csv'))
    m['月份'] = pd.to_datetime(m['月份'].str.replace('年', '-').str.replace('月份', ''))
    f = pd.read_csv(os.path.join(DATA, 'macro_fi.csv'))
    f['月份'] = pd.to_datetime(f['月份'].str.replace('年', '-').str.replace('月份', ''))
    return out, m, f

def sig_macro(wk, score_df, k_ppt=90):
    mac, m, f = load_macro()
    ppi, cpi = mac['ppi'], mac['cpi']
    pc = (ppi - cpi).dropna()
    pc_rank = pc.rolling(k_ppt, min_periods=24).rank(pct=True)
    # M1-M2
    m1m2 = (m.set_index('月份')['货币(M1)-同比增长'] - m.set_index('月份')['货币和准货币(M2)-同比增长']).dropna()
    m1m2_rank = m1m2.rolling(k_ppt, min_periods=24).rank(pct=True)
    # 固投
    fi = f.set_index('月份')['同比增长'].astype(float).dropna()
    fi_chg = fi.diff()
    # 映射到周
    last = pd.to_datetime(score_df['trade_date'])
    def map_series(ts, last_dates):
        ts = ts.sort_index()
        idx = ts.index.searchsorted(last_dates.to_numpy(), side='right') - 1
        vals = ts.to_numpy()
        return np.where(idx >= 0, vals[np.clip(idx, 0, len(vals)-1)], np.nan)
    pr = map_series(pc_rank, last)
    mr = map_series(m1m2_rank, last)
    fch = map_series(fi_chg, last)
    pcl = map_series(pc, last)  # 剪刀差水平
    s7 = np.where(np.isnan(pr), 0, np.where((pcl < 0) & (pr < 0.5), 1, np.where((pcl > 0) & (pr >= 0.5), -1, 0))).astype(float)
    s8 = np.where(np.isnan(fch), 0, np.sign(-fch)).astype(float)  # 固投同比下行->成长
    s9 = np.where(np.isnan(mr), 0, np.where(mr >= 0.5, 1, -1)).astype(float)
    return s7, s8, s9

# ---------------- 回测 ----------------
def backtest(scores, wk_ref, cost=0.0):
    """scores: df[trade_date(周最后交易日), score]; 信号t收盘定->持有t到t+1周收益"""
    df = scores.merge(wk_ref[['last_date', 'g_ret', 'v_ret']], left_on='trade_date',
                      right_on='last_date', how='inner')
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['g_ret'] = df['g_ret'].shift(-1)
    df['v_ret'] = df['v_ret'].shift(-1)
    pos = np.zeros(len(df)); cur = 0.0
    for i in range(len(df)):
        s = df['score'].iloc[i]
        if not np.isnan(s) and s != 0:
            cur = 1.0 if s > 0 else -1.0
        pos[i] = cur
    df['pos'] = pos
    df['ret'] = np.where(df['pos'] > 0, df['g_ret'], np.where(df['pos'] < 0, df['v_ret'],
                           0.5 * df['g_ret'] + 0.5 * df['v_ret'])) - cost
    df['bench'] = 0.5 * df['g_ret'] + 0.5 * df['v_ret']
    return df

def metrics(df, rf=0.02):
    nav = (1 + df['ret']).cumprod()
    bnav = (1 + df['bench']).cumprod()
    n = len(df); yrs = n / 52.0
    ann = nav.iloc[-1] ** (1 / yrs) - 1
    bann = bnav.iloc[-1] ** (1 / yrs) - 1
    r, b = df['ret'], df['bench']
    vol = r.std() * np.sqrt(52)
    sharpe = (r.mean() * 52 - rf) / vol
    mdd = (nav / nav.cummax() - 1).min()
    ex = r - b
    ir = ex.mean() * 52 / (ex.std() * np.sqrt(52))
    return dict(years=yrs, ann=ann, bench_ann=bann, excess=ann - bann, vol=vol,
                sharpe=sharpe, mdd=mdd, ir=ir)

def sig_stats(df):
    outs = []; i = 0; n = len(df)
    while i < n:
        if df['pos'].iloc[i] == 0:
            i += 1; continue
        start = i; d = df['pos'].iloc[i]
        while i < n and df['pos'].iloc[i] == d:
            i += 1
        seg = df.iloc[start:i]
        if not len(seg):
            continue
        ret = (1 + seg['ret']).prod() - 1
        br = (1 + seg['bench']).prod() - 1
        outs.append({'weeks': len(seg), 'dir': d, 'excess': ret - br, 'ret': ret})
    st = pd.DataFrame(outs)
    if st.empty:
        return {}
    wins = st[st['excess'] > 0]; losses = st[st['excess'] <= 0]
    wr = len(wins) / len(st)
    payoff = (wins['excess'].mean() if len(wins) else 0) / (-losses['excess'].mean() if len(losses) else np.nan)
    return dict(n_signal=len(st), avg_weeks=st['weeks'].mean(), win_rate=wr, payoff=payoff)

# ---------------- 主流程 ----------------
SIG_NAMES = {'s1': '价格动量', 's2': '成交额动量', 's3': '超大单占比', 's4': '融资买盘比',
             's5': '净利润增速', 's6': '股息比', 's7': 'PPI-CPI分位', 's8': '固定资产投资', 's9': 'M1-M2分位'}

def run(start='20220101', end='20260822', k_ret=8, k_amt=4, W=120, cost=0.0, with_stock=True):
    idx = load_index()
    g_w, v_w = load_weights()
    wk = weekly_base(idx, start, end)
    wk = sig_trade(wk, k_ret, k_amt)
    weeks = wk['week'].tolist()
    base = wk[['week']].copy()
    base['trade_date'] = wk['last_date'].values
    base['s1'] = wk['s1'].values
    base['s2'] = wk['s2'].values
    s3 = s4 = s5 = s6 = pd.Series(dtype=float)
    if with_stock:
        s3, s4 = sig_moneyflow(g_w, v_w, weeks)
        s5, s6 = sig_fundamental(g_w, v_w, weeks)
    wk_ref = wk.set_index('week')
    for nm, s in [('s3', s3), ('s4', s4), ('s5', s5), ('s6', s6)]:
        if s is not None and len(s):
            tmp = s.rename(nm).reset_index()
            tmp.columns = ['week', nm]
            base = base.merge(tmp, on='week', how='left')
    s7, s8, s9 = sig_macro(wk, base)
    base['s7'] = s7
    base['s8'] = s8
    base['s9'] = s9
    # 可用信号数与等权
    have = [c for c in ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9'] if c in base.columns]
    base['n_avail'] = base[have].notna().sum(axis=1)
    base['score_eq'] = base[have].fillna(0).mean(axis=1)
    # 动态赋权
    base['score_dyn'] = dynamic_weight(base, wk_ref, have, W)
    results = {}
    for nm, col in [('等权综合', 'score_eq'), ('动态赋权', 'score_dyn')]:
        df = backtest(base[['trade_date', col]].rename(columns={col: 'score'}), wk, cost)
        results[nm] = dict(df=df, **metrics(df), **sig_stats(df))
    return base, wk, results

def dynamic_weight(base, wk_ref, have, W=120):
    from scipy.optimize import minimize
    S = base[have].fillna(0).values
    R = wk_ref['xs_ret'].values[:len(base)]
    dyn = np.zeros(len(base))
    nw = len(have)
    ratio = np.ones(nw) / nw
    for t in range(len(base)):
        if t < 20:
            dyn[t] = base['score_eq'].iloc[t]
            continue
        lo = max(0, t - W + 1)
        Sn = S[lo:t + 1]; Rn = R[lo:t + 1]
        valid = ~np.isnan(Sn).any(axis=1) & ~np.isnan(Rn)
        if valid.sum() < 60:
            dyn[t] = base['score_eq'].iloc[t]
            continue
        Sn, Rn = Sn[valid], Rn[valid]
        cons = {'type': 'eq', 'fun': lambda w: w.sum() - 1}
        try:
            r = minimize(lambda w: np.mean((Sn @ w - Rn) ** 2), ratio, method='SLSQP',
                         bounds=[(0, 1)] * nw, constraints=cons,
                         options={'maxiter': 400, 'ftol': 1e-10, 'disp': False})
            if r.success:
                ratio = r.x
                dyn[t] = float(S[t] @ ratio)
            else:
                dyn[t] = base['score_eq'].iloc[t]
        except Exception:
            dyn[t] = base['score_eq'].iloc[t]
    return dyn

if __name__ == '__main__':
    import json
    base, wk, res = run(with_stock=False)
    print('== 元数据 ==')
    print('周数', len(wk), '可用信号分布:')
    print(base[['s1', 's2', 's7', 's8', 's9']].notna().sum())
    for k, v in res.items():
        print('=' * 16, k)
        print({kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items() if kk != 'df'})