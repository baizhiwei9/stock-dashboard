# -*- coding: utf-8 -*-
"""成长价值轮动的全景式框架 —— 复现引擎 v3 (日度信号 + 周五采样, 周度调仓)
   文章: 《成长价值轮动的全景式框架|开源金工》(2026-08-21)
   区间: 默认 2022-01-01 ~ 2026-08-21; 可扩展至 2010 起
   基准: 50%国证成长 + 50%国证价值
   信号>0 满仓成长; <0 满仓价值; 无信号延续前信号
"""
import os, glob, re
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')

# ================= 数据加载 =================
def load_index():
    g = pd.read_csv(os.path.join(DATA, 'index_399370.csv'), dtype={'trade_date': str})
    v = pd.read_csv(os.path.join(DATA, 'index_399371.csv'), dtype={'trade_date': str})
    g['trade_date'] = pd.to_datetime(g['trade_date'])
    v['trade_date'] = pd.to_datetime(v['trade_date'])
    g = g[['trade_date', 'close', 'amount']].rename(columns={'close': 'close_g', 'amount': 'amount_g'})
    v = v[['trade_date', 'close', 'amount']].rename(columns={'close': 'close_v', 'amount': 'amount_v'})
    df = g.merge(v, on='trade_date', how='inner').sort_values('trade_date').reset_index(drop=True)
    df = df[df['trade_date'] >= pd.Timestamp('20100101')]
    df['g_ret'] = df['close_g'] / df['close_g'].shift(1) - 1
    df['v_ret'] = df['close_v'] / df['close_v'].shift(1) - 1
    df['xs_ret'] = df['g_ret'] - df['v_ret']
    df['week'] = df['trade_date'].dt.to_period('W-FRI')
    return df

def load_weights():
    g = pd.read_csv(os.path.join(DATA, 'weights_growth.csv'))
    v = pd.read_csv(os.path.join(DATA, 'weights_value.csv'))
    return g, v

def load_constituents():
    c = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))
    return c['code'].tolist()

# ================= 信号计算 (返回日度/月度 Series, index=日期) =================
def sig_s1(idx, w=15):
    """S1 价格动量: 成长/价值日超额收益的 w 日累计符号 (文章: 超额收益上行看多成长)"""
    s = np.sign(idx.set_index('trade_date')['xs_ret'].rolling(w, min_periods=max(3, w // 2)).sum())
    return s.rename('s1')

def sig_s2(idx, w=8):
    """S2 成交额动量: 成长成交额占比的 w 周变动方向"""
    d = idx.copy()
    # 周度占比
    wk = d.groupby('week')[['amount_g', 'amount_v']].sum()
    share = wk['amount_g'] / (wk['amount_g'] + wk['amount_v'])
    sig_w = np.sign(share - share.shift(w))
    # 映射回日度(该周最后交易日生效)
    sig_d = d.set_index('trade_date')['week'].map(sig_w)
    return sig_d.rename('s2')

def _load_stock_wide(kind, factor_fn, start='20200101'):
    """载入 data/{kind}/*.csv 每个股票日度因子, 返回(wide日频表格: trade_date x code)"""
    files = sorted(glob.glob(os.path.join(DATA, kind, '*.csv')))
    files = [f for f in files if not re.match(r'^\d{6}\.csv$', os.path.basename(f))]  # 排除按月命名的遗留文件
    rows = []
    for fp in files:
        code = os.path.basename(fp)[:-4].replace('_', '.')
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if df.empty:
            continue
        if 'trade_date' in df.columns:
            df['trade_date'] = df['trade_date'].astype(str)
            df = df[df['trade_date'] >= start]
        fv = factor_fn(df)
        if fv is None or len(fv) == 0:
            continue
        fv = fv[~fv.index.duplicated(keep='last')].sort_index()
        fv = fv.rename(code)
        rows.append(fv)
    if not rows:
        return pd.DataFrame()
    wide = pd.concat(rows, axis=1).sort_index()
    return wide

def _merge_weight(wide, weights, idx):
    """宽表 x 月度权重快照 -> 指数日度因子序列(成长/价值)
    权重快照月末取值, 在快照期间内适用; 返回当日所有成分加权平均"""
    w = weights[['con_code', 'trade_date', 'weight']].copy()
    w['trade_date'] = pd.to_datetime(w['trade_date'].astype(str))  # int64 YYYYMMDD -> datetime
    w = w.sort_values('trade_date')
    result = pd.Series(np.nan, index=wide.index)
    # 权重生效区间: [当月快照日, 下月快照日)
    dates = sorted(w['trade_date'].unique())
    for i, dt in enumerate(dates):
        nxt = dates[i + 1] if i + 1 < len(dates) else idx['trade_date'].max() + pd.Timedelta(days=1)
        wi = w[w['trade_date'] == dt].set_index('con_code')['weight']
        mask = (wide.index >= dt) & (wide.index < nxt)
        seg = wide.loc[mask]
        codes = [c for c in wi.index if c in seg.columns]
        if not codes or seg.empty:
            continue
        wsum = wi[codes].sum()
        if wsum <= 0:
            continue
        result.loc[mask] = seg[codes].mul(wi[codes], axis=1).sum(axis=1) / wsum
    return result

def sig_s3(idx, g_w, v_w, k=20):
    """S3 超大单占比: 成长-价值(超大单+大单主动净流入/总成交)占比差, 变动方向"""
    def factor(df):
        if not {'buy_elg_amount', 'sell_elg_amount', 'buy_lg_amount', 'sell_lg_amount', 'buy_md_amount', 'sell_md_amount', 'buy_sm_amount', 'sell_sm_amount'}.issubset(df.columns):
            return None
        bn = (df['buy_elg_amount'].fillna(0) + df['buy_lg_amount'].fillna(0)
              - df['sell_elg_amount'].fillna(0) - df['sell_lg_amount'].fillna(0))
        tot = (df['buy_elg_amount'].fillna(0) + df['buy_lg_amount'].fillna(0)
               + df['buy_md_amount'].fillna(0) + df['buy_sm_amount'].fillna(0)
               + df['sell_elg_amount'].fillna(0) + df['sell_lg_amount'].fillna(0)
               + df['sell_md_amount'].fillna(0) + df['sell_sm_amount'].fillna(0))
        r = (bn / tot.replace(0, np.nan)).rename('f')
        return pd.Series(r.values, index=pd.to_datetime(df['trade_date']))
    if not os.path.isdir(os.path.join(DATA, 'moneyflow')):
        return pd.Series(dtype=float, name='s3')
    wide = _load_stock_wide('moneyflow', factor)
    if wide.empty:
        return pd.Series(dtype=float, name='s3')
    gs = _merge_weight(wide, g_w, idx)
    vs = _merge_weight(wide, v_w, idx)
    diffg = (gs - vs)
    # 周度采样 diff 方向
    d = pd.DataFrame({'trade_date': idx['trade_date'], 'diffg': diffg.reindex(idx['trade_date']).values})
    wk = d.groupby(idx['week'])['diffg'].last()
    sig_w = np.sign(wk - wk.shift(1))
    sig_d = idx.assign(sig=idx['week'].map(sig_w)).set_index('trade_date')['sig']
    return sig_d.rename('s3')

def sig_s4(idx, g_w, v_w, k=4):
    """S4 融资买盘比: 成长-价值融资买入额加权和, 变动方向"""
    def factor(df):
        if 'rzmre' not in df.columns:
            return None
        r = df['rzmre'].fillna(0).rename('f')
        return pd.Series(r.values, index=pd.to_datetime(df['trade_date']))
    if not os.path.isdir(os.path.join(DATA, 'margin')):
        return pd.Series(dtype=float, name='s4')
    wide = _load_stock_wide('margin', factor)
    if wide.empty:
        return pd.Series(dtype=float, name='s4')
    gs = _merge_weight(wide, g_w, idx).fillna(0)
    vs = _merge_weight(wide, v_w, idx).fillna(0)
    ratio_g = (gs - vs)
    d = pd.DataFrame({'trade_date': idx['trade_date'], 'diffg': ratio_g.reindex(idx['trade_date']).values})
    wk = d.groupby(idx['week'])['diffg'].last()
    sig_w = np.sign(wk - wk.shift(k))
    sig_d = idx.assign(sig=idx['week'].map(sig_w)).set_index('trade_date')['sig']
    return sig_d.rename('s4')

def sig_s5(idx, g_w, v_w):
    """S5 净利润增速: 成长-价值 成分净利润同比(整体法) 差值的变动方向 (季度)"""
    def factor(df):
        if not {'end_date', 'ann_date', 'report_type', 'n_income'}.issubset(df.columns):
            return None
        d = df.copy()
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
        eff = pd.to_datetime(d['ann_date'], errors='coerce').fillna(
            pd.to_datetime(d['end_date']) + pd.Timedelta(days=45))
        s = pd.Series(d['yoy'].values, index=eff).sort_index()
        return s[~s.index.duplicated(keep='last')]
    if not os.path.isdir(os.path.join(DATA, 'income')):
        return pd.Series(dtype=float, name='s5')
    wide = _load_stock_wide('income', factor, start='20180101')
    if wide.empty:
        return pd.Series(dtype=float, name='s5')
    gs = _merge_weight(wide, g_w, idx)
    vs = _merge_weight(wide, v_w, idx)
    dg = (gs - vs)
    # 周度映射 + diff 方向 (财报因子为季度事件, 公告日后 ffill 持续生效)
    d = pd.DataFrame({'trade_date': idx['trade_date'], 'dg': dg.reindex(idx['trade_date']).values})
    d['dg'] = d['dg'].ffill()
    wk = d.groupby(idx['week'])['dg'].last()
    sig_w = np.sign(wk - wk.shift(1))
    sig_d = idx.assign(sig=idx['week'].map(sig_w)).set_index('trade_date')['sig']
    return sig_d.rename('s5')

def sig_s6(idx, g_w, v_w, k=20):
    """S6 股息比: 价值/成长 近一年股息率比值 vs 其k日均值 -> 比值低看多成长"""
    def factor(df):
        if 'dv_ratio' not in df.columns:
            return None
        r = df['dv_ratio'].fillna(0).rename('f')
        return pd.Series(r.values, index=pd.to_datetime(df['trade_date']))
    if not os.path.isdir(os.path.join(DATA, 'daily_basic')):
        return pd.Series(dtype=float, name='s6')
    wide = _load_stock_wide('daily_basic', factor)
    if wide.empty:
        return pd.Series(dtype=float, name='s6')
    gs = _merge_weight(wide, g_w, idx)
    vs = _merge_weight(wide, v_w, idx)
    ratio = (vs / gs.replace(0, np.nan))
    ma = ratio.rolling(k, min_periods=k // 2).mean()
    sig = np.sign(ratio - ma)
    d = pd.DataFrame({'trade_date': idx['trade_date'], 'sg': sig.reindex(idx['trade_date']).values})
    wk = d.groupby(idx['week'])['sg'].last()
    sig_d = idx.assign(sig=idx['week'].map(wk)).set_index('trade_date')['sig']
    return sig_d.rename('s6')

def _load_macro():
    mac = {}
    for name, key in [('ppi', '中国PPI年率报告'), ('cpi', '中国CPI月率报告')]:
        df = pd.read_csv(os.path.join(DATA, f'macro_{name}.csv'))
        df = df[df['商品'] == key][['日期', '今值']].dropna()
        df['日期'] = pd.to_datetime(df['日期'])
        mac[name] = df.set_index('日期')['今值'].astype(float).sort_index()
    m = pd.read_csv(os.path.join(DATA, 'macro_m1m2.csv'))
    m['月份'] = pd.to_datetime(m['月份'].str.replace('年', '-').str.replace('月份', ''))
    f = pd.read_csv(os.path.join(DATA, 'macro_fi.csv'))
    f['月份'] = pd.to_datetime(f['月份'].str.replace('年', '-').str.replace('月份', ''))
    return mac, m, f

def _macro_to_daily(mseries, idx, fill='ffill'):
    """月度序列 -> 每日(前向填充, 月末发布假设次月初生效)"""
    ts = mseries.sort_index()
    daily = pd.Series(np.nan, index=idx['trade_date'])
    vals = ts.reindex(ts.index.union(idx['trade_date'])).sort_index().ffill()
    daily = vals.reindex(idx['trade_date'])
    return daily

def sig_s7(idx, w=36, lo=0.3, hi=0.7):
    """S7 PPI-CPI分位: PPI同比占劣 且 分位差低 -> 成长; 占优 且 高 -> 价值"""
    mac, m, f = _load_macro()
    pc = (mac['ppi'] - mac['cpi']).dropna()
    pr = pc.rolling(w, min_periods=24).rank(pct=True)
    s_m = np.where((pc < 0) & (pr < lo), 1, np.where((pc > 0) & (pr > hi), -1, 0))
    s_m = pd.Series(s_m, index=pc.index)
    s_d = _macro_to_daily(s_m, idx)
    return s_d.rename('s7')

def sig_s8(idx):
    """S8 固投同比: 同比下行 -> 成长(逆周期); 上行 -> 价值"""
    mac, m, f = _load_macro()
    fi = f.set_index('月份')['同比增长'].astype(float).dropna()
    chg = fi.diff()
    s_m = np.where(chg < 0, 1, -1)
    s_m = pd.Series(s_m, index=chg.index)
    s_d = _macro_to_daily(s_m, idx)
    return s_d.rename('s8')

def sig_s9(idx, w=36, th=0.7):
    """S9 M1-M2分位: 分位差较高 -> 成长; 较低 -> 价值"""
    mac, m, f = _load_macro()
    m1 = m.set_index('月份')['货币(M1)-同比增长'].astype(float)
    m2 = m.set_index('月份')['货币和准货币(M2)-同比增长'].astype(float)
    mm = (m1 - m2).dropna()
    mr = mm.rolling(w, min_periods=24).rank(pct=True)
    s_m = np.where(mr >= th, 1, -1)
    s_m = pd.Series(s_m, index=mr.index)
    s_d = _macro_to_daily(s_m, idx)
    return s_d.rename('s9')

# ================= 组合层 =================
def build_signal_frame(idx, g_w, v_w, start='20220101', with_stock=True):
    """构建周度采样信号表: 每周末最后交易日一行的 9 信号"""
    sigs = {
        's1': sig_s1(idx),
        's2': sig_s2(idx),
        's7': sig_s7(idx),
        's8': sig_s8(idx),
        's9': sig_s9(idx),
    }
    if with_stock:
        print('  S3 moneyflow ...', flush=True)
        sigs['s3'] = sig_s3(idx, g_w, v_w)
        print('  S4 margin ...', flush=True)
        sigs['s4'] = sig_s4(idx, g_w, v_w)
        print('  S5 income ...', flush=True)
        sigs['s5'] = sig_s5(idx, g_w, v_w)
        print('  S6 daily_basic ...', flush=True)
        sigs['s6'] = sig_s6(idx, g_w, v_w)
    df = idx[['trade_date', 'week']].copy()
    for k, s in sigs.items():
        s.name = k
        s = s.reindex(df['trade_date'])
        df[k] = s.values
    # 周五采样(每周最后交易日)
    wk = df.groupby('week').tail(1).set_index('week').sort_index()
    wk = wk[(wk['trade_date'] >= pd.Timestamp(start))]
    return wk

def ffill_rule(s):
    """0 视为无信号, 延续前信号; ±1 更新"""
    cur = 0.0
    out = np.zeros(len(s))
    for i, v in enumerate(s):
        if not np.isnan(v) and v != 0:
            cur = v
        out[i] = cur
    return out

def backtest_weekly(wk_sig, idx, start='20220101', end='20260822'):
    """wk_sig: 周采样信号表(index=week, 含 trade_date 与各信号列) -> 收益 Dataframe
    信号在周末最后交易日收盘确定, 持有到下一周末 -> 收益 = 下一周成长/价值周收益"""
    last = idx.groupby(idx['week'])['trade_date'].max()
    g_ret_d = idx.set_index('trade_date')['g_ret']
    v_ret_d = idx.set_index('trade_date')['v_ret']
    # 每周收益: 该周最后交易日close / 上周最后交易日close - 1
    gl = g_ret_d.reindex(last).groupby(last.index).last()
    vl = v_ret_d.reindex(last).groupby(last.index).last()
    # 注意 g_ret_d 是日度收益, 周收益需用周末close比: 重新计算更稳
    close_g = idx.set_index('trade_date')['close_g']
    close_v = idx.set_index('trade_date')['close_v']
    cg = close_g.reindex(last)
    cv = close_v.reindex(last)
    g_wk_ret = cg / cg.shift(1) - 1
    v_wk_ret = cv / cv.shift(1) - 1
    wk = pd.DataFrame({'last_date': last, 'g_ret': g_wk_ret.values, 'v_ret': v_wk_ret.values})
    wk.index = last.index
    wk = wk.dropna()
    wk = wk[(wk.index >= pd.Period(start, 'W-FRI')) & (wk.index <= pd.Period(end, 'W-FRI'))]
    m = wk_sig.merge(wk, left_index=True, right_index=True, how='inner')
    # 关键: 信号在t周末确定并以收盘价交易 -> 收益为 t+1 周的 g_ret/v_ret
    m['g_ret'] = m['g_ret'].shift(-1)
    m['v_ret'] = m['v_ret'].shift(-1)
    return m

def equal_weight(m, sig_cols):
    """等权聚合 + 持仓规则(0延续)"""
    S = m[sig_cols].fillna(0).values
    # 有数据的信号才纳入均值(无数据信号NaN视为缺, 不参与平均)
    n_avail = m[sig_cols].notna().sum(axis=1)
    sc = m[sig_cols].fillna(0).mean(axis=1)
    sc = sc.where(n_avail > 0, np.nan)
    return np.sign(S.sum(axis=1) / np.maximum(n_avail.values, 1))

def dynamic_weight_series(m, sig_cols, W=120):
    """滚动120周 SLSQP: min ||(S·w)*R - |R|||^2, s.t. w>=0, sum(w)=1
    R = 成长/价值周超额收益 (需与组合收益同期: 已shift过)
    最优解收益 = |R| (后验满仓跑赢方), 组合信号收益 = (S·w)*R
    即: 让组合信号方向尽量与超额收益同号, 且幅度接近最优*/
    """
    from scipy.optimize import minimize
    S = m[sig_cols].fillna(0).values
    R = m['g_ret'].values - m['v_ret'].values
    Rn = np.where(np.isnan(R), 0, R)
    out = np.zeros(len(m))
    init = np.ones(len(sig_cols)) / len(sig_cols)
    for t in range(len(m)):
        if t < 30:
            out[t] = S[t].sum() / len(sig_cols)
            continue
        lo = max(0, t - W + 1)
        Sn = S[lo:t + 1]
        Rn_w = Rn[lo:t + 1]
        cons = {'type': 'eq', 'fun': lambda w: w.sum() - 1}
        def obj(w):
            comb = Sn @ w
            return np.mean((comb * Rn_w - np.abs(Rn_w)) ** 2)
        try:
            res = minimize(obj, init, method='SLSQP',
                           bounds=[(0., 1.)] * len(sig_cols), constraints=cons,
                           options={'maxiter': 500, 'ftol': 1e-12})
            if res.success:
                init = res.x
                out[t] = float(S[t] @ res.x)
            else:
                out[t] = S[t].sum() / len(sig_cols)
        except Exception:
            out[t] = S[t].sum() / len(sig_cols)
    return out

def position_from_signal(s):
    """策略: 信号>0 满仓成长; <0 满仓价值; 无信号(0/NaN)延续前信号"""
    pos = np.zeros(len(s))
    cur = 0.0
    for i, v in enumerate(s):
        if not np.isnan(v) and v != 0:
            cur = 1.0 if v > 0 else -1.0
        pos[i] = cur
    return pos

def compute_perf(m, pos, name=''):
    """基于周收益的绩效统计"""
    df = m[['last_date', 'g_ret', 'v_ret']].copy()
    df['pos'] = pos
    df['ret'] = np.where(df['pos'] > 0, df['g_ret'],
                         np.where(df['pos'] < 0, df['v_ret'], 0.5 * df['g_ret'] + 0.5 * df['v_ret']))
    df['bench'] = 0.5 * df['g_ret'] + 0.5 * df['v_ret']
    df = df.dropna(subset=['ret'])
    nav = (1 + df['ret']).cumprod()
    bnav = (1 + df['bench']).cumprod()
    n = len(df)
    yrs = n / 52.0
    ann = nav.iloc[-1] ** (1 / yrs) - 1
    bann = bnav.iloc[-1] ** (1 / yrs) - 1
    r, b = df['ret'], df['bench']
    vol = r.std() * np.sqrt(52)
    sharpe = (r.mean() * 52 - 0.02) / vol
    mdd = (nav / nav.cummax() - 1).min()
    ex = r - b
    ir = ex.mean() * 52 / (ex.std() * np.sqrt(52))
    ex_nav = (1 + ex).cumprod()
    ex_mdd = (ex_nav / ex_nav.cummax() - 1).min()
    # 信号次数/次均持仓(交易日)
    p = df['pos']
    n_sig = int(np.sum((p != 0) & (p.shift(1).fillna(0) != p)))
    days = len(df) * 5
    hold = days / n_sig if n_sig else np.nan
    # 次胜率/次赔率: 每段持仓
    outs = []
    i = 0
    while i < len(df):
        if df['pos'].iloc[i] == 0:
            i += 1
            continue
        st = i
        dpos = df['pos'].iloc[i]
        while i < len(df) and df['pos'].iloc[i] == dpos:
            i += 1
        seg = df.iloc[st:i]
        if seg.empty:
            continue
        rseg = (1 + seg['ret']).prod() - 1
        bseg = (1 + seg['bench']).prod() - 1
        outs.append({'dir': dpos, 'excess': rseg - bseg})
    if outs:
        odf = pd.DataFrame(outs)
        wins = odf[odf['excess'] > 0]
        losses = odf[odf['excess'] <= 0]
        win_rate = len(wins) / len(odf)
        payoff = (wins['excess'].mean() if len(wins) else 0) / \
                 (-losses['excess'].mean() if len(losses) else np.nan)
    else:
        win_rate = payoff = np.nan
    return dict(name=name, n=len(df), years=yrs, ann=ann, bench_ann=bann, excess=ann - bann,
                vol=vol, sharpe=sharpe, mdd=mdd, ir=ir, ex_mdd=ex_mdd,
                n_sig=n_sig, hold_days=hold, win_rate=win_rate, payoff=payoff,
                nav=nav, ret=df)

ALL_SIGS = ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9']

def run_repro(start='20220101', end='20260822', with_stock=True, W=120):
    idx = load_index()
    g_w, v_w = load_weights()
    m = build_signal_frame(idx, g_w, v_w, start=start, with_stock=with_stock)
    m = backtest_weekly(m, idx, start=start, end=end)
    sig_cols = [c for c in ALL_SIGS if c in m.columns]
    print('信号可用周数覆盖:')
    print(m[sig_cols].notna().sum())
    results = {}
    # 等权
    pos_eq = position_from_signal(equal_weight(m, sig_cols))
    results['等权综合'] = compute_perf(m, pos_eq, '等权综合')
    # 动态赋权
    dyn = dynamic_weight_series(m, sig_cols, W=W)
    pos_dyn = position_from_signal(dyn)
    results['动态赋权'] = compute_perf(m, pos_dyn, '动态赋权')
    return m, results

if __name__ == '__main__':
    m, res = run_repro(with_stock=False)
    for k, v in res.items():
        print('=' * 16, k)
        print({kk: (round(vv, 4) if isinstance(vv, float) else vv)
               for kk, vv in v.items() if kk not in ('nav', 'ret')})
    import json
    json.dump({k: {kk: (vv if not hasattr(vv, 'item') else vv.item()) for kk, vv in v.items()
                   if kk not in ('nav', 'ret')} for k, v in res.items()},
              open('perf_5sig.json', 'w'), ensure_ascii=False, indent=1, default=str)

if __name__ == '__main__':
    idx = load_index()
    g_w, v_w = load_weights()
    wk = build_signal_frame(idx, g_w, v_w, with_stock=False)
    print('周数', len(wk))
    print('信号覆盖:')
    print(wk[['s1', 's2', 's7', 's8', 's9']].notna().sum())