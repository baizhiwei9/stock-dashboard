#!/usr/bin/env python3
"""
Hodge Laplacian 每日监测脚本
MA40 趋势过滤版（中证全A基准）

每日 19:00 运行：
  1. 拉取最新 ETF 行情 + 中证全A(000985)
  2. 计算 Hodge Laplacian 谱角距离、流分解 z_in/z_out
  3. 趋势过滤：中证全A vs MA40，线下空仓
  4. 个股/板块预测：每只 ETF 的动量衰减率、流入流出信号
  5. 生成飞书交互卡片报告并发送（内容在前、署名「来自 DoMate」挂标题末尾）
"""
import os, sys, json, time, warnings, re


# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''

# 本地依赖优先（沙箱清理兜底）：优先使用工作区 pylib 中的 scipy/numpy
_PYLIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib')
if os.path.isdir(_PYLIB) and _PYLIB not in sys.path:
    sys.path.insert(0, _PYLIB)

# 五大宽基风险共享模块（2026-10-22 新增：报告统一给出沪深300/中证500/中证1000/科创50/创业板指明确结论）
_SHARED = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
if os.path.isdir(_SHARED) and _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

import requests
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, f as f_dist
from scipy.linalg import lstsq
from datetime import datetime, timedelta
from itertools import combinations

warnings.filterwarnings('ignore')

# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
# ETF/指数日线与当日快照全部改由 data_hub 提供，本文件不再直连腾讯行情接口。
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# =================== PARAMETERS ===================
W_WIN, W_MA, W_Q = 40, 40, 120
Q_TH, FLOOR, DROP = 0.90, 0.25, 0.20
RHO_TH, P_TH = 0.4, 0.10
REBAL, MOM_WIN, HOLD, SEQ = 10, 60, 20, 15
TRANS, MIN_HIST = 3, 60
MA_WINDOW = 40

FEISHU_WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8'
REPORT_HEADER = '🔬 Hodge Laplacian 每日监测'

ETF_LIST = {
    '512480': 'sh', '512760': 'sh', '515880': 'sh', '515050': 'sh',
    '510300': 'sh', '510500': 'sh', '512100': 'sh', '512890': 'sh',
    '512200': 'sh', '512010': 'sh', '512690': 'sh', '512800': 'sh',
    '512660': 'sh', '159915': 'sz', '510050': 'sh', '510880': 'sh',
    '512720': 'sh', '159928': 'sz',
    '159825': 'sz', '159865': 'sz', '512400': 'sh', '515210': 'sh',
    '515220': 'sh', '516160': 'sh', '515790': 'sh', '515170': 'sh',
    '512980': 'sh', '512000': 'sh', '516020': 'sh', '516950': 'sh',
    '512580': 'sh', '159996': 'sz', '515700': 'sh', '516780': 'sh',
    '159611': 'sz', '516110': 'sh', '515230': 'sh',
}

ETF_NAMES = {
    '512480': '半导体', '512760': '芯片', '515880': '通信', '515050': '5G通信',
    '510300': '沪深300', '510500': '中证500', '512100': '中证1000', '512890': '红利低波',
    '512200': '房地产', '512010': '医药', '512690': '酒', '512800': '银行',
    '512660': '军工', '159915': '创业板', '510050': '上证50', '510880': '红利',
    '512720': '计算机', '159928': '消费',
    '159825': '农业', '159865': '养殖', '512400': '有色', '515210': '钢铁',
    '515220': '煤炭', '516160': '新能源', '515790': '光伏', '515170': '食品饮料',
    '512980': '传媒', '512000': '券商', '516020': '化工', '516950': '基建',
    '512580': '环保', '159996': '家电', '515700': '新能源车', '516780': '稀土',
    '159611': '电力', '516110': '汽车', '515230': '软件',
}

BENCHMARK_CODE = 'sh000985'  # 中证全A
BENCHMARK_NAME = '中证全A'

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_long')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# =================== DATA FETCH ===================
_INDEX_SUFFIX = {
    '000001': '.SH', '000016': '.SH', '000300': '.SH', '000688': '.SH',
    '000905': '.SH', '000852': '.SH', '000985': '.CSI',
    '399001': '.SZ', '399006': '.SZ', '399106': '.SZ',
}


def _split_symbol(code):
    """'sh512480' -> ('etf','512480')；'sh000985' -> ('index','000985.CSI')。"""
    raw = str(code).strip().lower()
    prefix = raw[:2] if raw[:2] in ('sh', 'sz') else ''
    num = (raw[2:] if prefix else raw).split('.')[0]
    if num.startswith(('15', '16', '51', '52', '56', '58')):
        return 'etf', num
    if num in _INDEX_SUFFIX:
        return 'index', num + _INDEX_SUFFIX[num]
    return 'index', num + ('.SH' if (prefix == 'sh' or num.startswith('000')) else '.SZ')


def _norm_date(value):
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return f'{text[:4]}-{text[4:6]}-{text[6:8]}'
    return text[:10]


def fetch_kline(code, days=800):
    """DataHub 股票模块前复权日线：{date: close}。"""
    kind, sym = _split_symbol(code)
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, start_date=start_date, end_date=end_date)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            print(f'  dataHub {code}({sym}) 日线返回空')
            return {}
        dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
        bars = {}
        for _, row in df.iterrows():
            try:
                bars[_norm_date(row[dcol])] = float(row['close'])
            except Exception:
                continue
        return bars
    except Exception as e:
        print(f'  ERROR fetching {code} via dataHub: {e}')
        return {}


def fetch_realtime_close(code):
    """DataHub 快照取当日收盘价（盘后即当日收盘价）。code 形如 sh510300。"""
    kind, sym = _split_symbol(code)
    today = datetime.now().strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, trade_date=today)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, trade_date=today)
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]['close'])
    except Exception:
        pass
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_realtime_spot
            px, _src = get_realtime_spot(sym, verbose=False)
            return px
        from data_hub.collectors.stocks import get_index_spot
        spot = get_index_spot(index_codes=[code])
        if spot is not None and len(spot) > 0 and pd.notna(spot.iloc[0].get('price')):
            return float(spot.iloc[0]['price'])
    except Exception:
        pass
    return None


def _missing_today_cols(df, etf_codes, target_date):
    """返回 df 最后一行（target_date）中缺失的 ETF 代码列表"""
    if df is None or len(df) == 0:
        return list(etf_codes)
    last_row = df.iloc[-1]
    return [c for c in etf_codes if pd.isna(last_row.get(c, float('nan')))]


def load_or_fetch_data():
    """Load cached data or fetch fresh.

    数据新鲜度铁律：
    1. 缓存仅在【每一列】都有最新交易日真实数据时才可复用；
    2. 今日K线缺失的代码用实时行情接口(qt.gtimg.cn)补齐当日收盘价；
    3. 仍然缺失的代码绝不 ffill 静默填充，必须告警，报告据此拒发或明示。
    """
    cached = os.path.join(DATA_DIR, 'close_matrix.csv')
    cached_bench = os.path.join(DATA_DIR, 'benchmark.csv')

    df = None
    etf_codes = list(ETF_LIST.keys())
    need_fetch = True
    today = datetime.now().date()
    if os.path.exists(cached):
        df = pd.read_csv(cached, parse_dates=['date'])
        df = df.sort_values('date').reset_index(drop=True)
        latest_d = df['date'].max().date()
        etf_codes = [c for c in ETF_LIST.keys() if c in df.columns]
        # 缓存保护：仅当缓存最新日期==今天 且 每一列今天都有真实数据（非ffill填充）时才复用
        if latest_d == today:
            missing = _missing_today_cols(df, etf_codes, today)
            if not missing:
                print(f'Using cached ETF data (latest: {latest_d}, all {len(etf_codes)} cols fresh)')
                df[etf_codes] = df[etf_codes].ffill().bfill()
                need_fetch = False
            else:
                print(f'WARNING 缓存今天行缺失 {len(missing)} 列({",".join(missing[:5])}...)，缓存不可用，强制重拉')
        elif latest_d > today:
            print(f'WARNING 缓存数据日期({latest_d})晚于今天({today})，异常，强制重拉')

    if need_fetch:
        print('Fetching fresh ETF data...')
        all_bars = {}
        for code, prefix in ETF_LIST.items():
            bars = fetch_kline(f'{prefix}{code}', days=800)
            for d, c in bars.items():
                if d not in all_bars:
                    all_bars[d] = {}
                all_bars[d][code] = c
            time.sleep(0.15)
        dates_sorted = sorted(all_bars.keys())
        max_d = str(dates_sorted[-1]) if dates_sorted else None

        # 第一轮：K线接口已确认有最新交易日(max_d)的列直接可用；
        # 缺失的代码逐只重试一次（腾讯K线接口盘后存在分批更新延迟）
        if max_d:
            for code, prefix in ETF_LIST.items():
                if code not in all_bars.get(max_d, {}):
                    time.sleep(1.0)
                    bars = fetch_kline(f'{prefix}{code}', days=800)
                    for d, c in bars.items():
                        if d not in all_bars:
                            all_bars[d] = {}
                        all_bars[d][code] = c
                    time.sleep(0.3)

        # 第二轮：仍缺失最新交易日数据的代码，用实时行情接口补当日收盘价
        # （仅在K线接口已确认 max_d 存在时补，避免把非交易日的旧价写成新K线）
        if max_d:
            still_missing = [c for c in ETF_LIST
                             if c not in all_bars.get(max_d, {})]
            for code in still_missing:
                rp = ETF_LIST[code]
                rv = fetch_realtime_close(f'{rp}{code}')
                if rv is not None:
                    all_bars.setdefault(max_d, {})[code] = rv
                    print(f'  实时兜底 {code}({ETF_NAMES.get(code,code)}) {max_d}: {rv}')
                else:
                    print(f'  !! {code}({ETF_NAMES.get(code,code)}) 实时接口也无数据')

        dates_sorted = sorted(all_bars.keys())
        rows = [{'date': d, **all_bars[d]} for d in dates_sorted]
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        etf_codes = [c for c in ETF_LIST.keys() if c in df.columns]
        latest_d = df['date'].max().date()
        missing = _missing_today_cols(df, etf_codes, latest_d)
        # 写盘保留 NaN（不 ffill），确保下次缓存复用能识别缺失列
        df.to_csv(cached, index=False)
        completeness = len(etf_codes) - len(missing)
        print(f'ETF data: {len(df)} bars, latest {latest_d}, 完整度 {completeness}/{len(etf_codes)}')
        if missing:
            print(f'CRITICAL 以下ETF缺少最新交易日({latest_d})真实数据: {",".join(missing)}')
        if latest_d < today:
            print(f'CRITICAL ETF数据日期({latest_d})落后于今天({today})，腾讯接口未返回最新行情！')

    # Fetch benchmark (中证全A)
    need_bench = True
    if os.path.exists(cached_bench):
        bench_df = pd.read_csv(cached_bench, parse_dates=['date'])
        bench_df = bench_df.sort_values('date').reset_index(drop=True)
        bench_latest = bench_df['date'].max().date()
        last_valid_bench = bench_df['close'].dropna()
        if len(last_valid_bench) > 0 and bench_latest == today and last_valid_bench.index[-1] == len(bench_df) - 1:
            print(f'Using cached benchmark (latest: {bench_latest})')
            need_bench = False
        elif len(bench_df) > 0 and bench_latest > today:
            print(f'WARNING 基准缓存日期({bench_latest})晚于今天({today})，异常，强制重拉')

    if need_bench:
        print(f'Fetching {BENCHMARK_NAME}...')
        bench_bars = fetch_kline(BENCHMARK_CODE, days=800)
        bench_df = pd.DataFrame([{'date': d, 'close': c} for d, c in sorted(bench_bars.items())])
        bench_df['date'] = pd.to_datetime(bench_df['date'])
        # 最新交易日 close 缺失时用实时接口兜底（K线接口盘后分批更新）
        if len(bench_df) > 0:
            bl = bench_df['date'].max().date()
            last_close = bench_df.loc[bench_df['date'].dt.date == bl, 'close'].dropna()
            if len(last_close) == 0 and bl == today:
                rv = fetch_realtime_close(BENCHMARK_CODE)
                if rv is not None:
                    bench_df.loc[bench_df['date'].dt.date == bl, 'close'] = rv
                    print(f'  实时兜底基准 {bl}: {rv}')
                else:
                    print(f'CRITICAL 基准实时接口也无 {bl} 数据')
        bench_df.to_csv(cached_bench, index=False)
        bench_latest = bench_df['date'].max().date()
        has_close = bench_df.loc[bench_df['date'].dt.date == bench_latest, 'close'].notna().any()
        print(f'Benchmark: {len(bench_df)} bars, latest {bench_latest}, 有收盘价={has_close}')
        if bench_latest < today or not has_close:
            print(f'CRITICAL 基准数据日期({bench_latest})无最新收盘价，报告将不可信')

    return df, etf_codes, bench_df


# =================== GRANGER CAUSALITY ===================
def granger_aic_test(ret_i, ret_j, max_lag=5):
    mask = np.isfinite(ret_i) & np.isfinite(ret_j)
    ret_i, ret_j = ret_i[mask], ret_j[mask]
    n_total = len(ret_j)
    if n_total < 15:
        return 1.0, False
    best_aic, best_pval = np.inf, 1.0
    for lag in range(1, max_lag + 1):
        n = n_total - lag
        if n < lag * 2 + 5:
            continue
        y = ret_j[lag:]
        cols_r, cols_u = [np.ones(n)], [np.ones(n)]
        for l in range(1, lag + 1):
            cols_r.append(ret_j[lag-l:n_total-l])
            cols_u.append(ret_j[lag-l:n_total-l])
            cols_u.append(ret_i[lag-l:n_total-l])
        X_r, X_u = np.column_stack(cols_r), np.column_stack(cols_u)
        try:
            beta_r, _, _, _ = np.linalg.lstsq(X_r, y, rcond=None)
            beta_u, _, _, _ = np.linalg.lstsq(X_u, y, rcond=None)
        except:
            continue
        rss_r = np.dot(y - X_r @ beta_r, y - X_r @ beta_r)
        rss_u = np.dot(y - X_u @ beta_u, y - X_u @ beta_u)
        if rss_u <= 1e-15 or rss_r <= 1e-15:
            continue
        k_u = X_u.shape[1]
        aic = n * np.log(rss_u / n) + 2 * k_u
        if aic < best_aic:
            best_aic = aic
            df1, df2 = lag, n - k_u
            if df2 > 0:
                F_stat = ((rss_r - rss_u) / df1) / (rss_u / df2)
                if F_stat >= 0:
                    best_pval = float(f_dist.sf(F_stat, df1, df2))
    return best_pval, best_pval < P_TH


# =================== SIMPLICIAL COMPLEX ===================
def build_complex(rets_5d, rets_1d, members, win_start, win_end):
    n = len(members)
    if n < 2:
        return [], None, None
    w5 = np.nan_to_num(rets_5d[win_start:win_end, members], nan=0.0)
    w1 = np.nan_to_num(rets_1d[win_start:win_end, members], nan=0.0)
    edges = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if np.std(w5[:, i]) < 1e-10 or np.std(w5[:, j]) < 1e-10:
                continue
            rho, _ = spearmanr(w5[:, i], w5[:, j])
            if abs(rho) < RHO_TH:
                continue
            pval, sig = granger_aic_test(w1[:, i], w1[:, j])
            if sig:
                edges.append((i, j))
    if not edges:
        return [], None, None
    V, E = n, len(edges)
    B1 = np.zeros((V, E))
    for e_idx, (i, j) in enumerate(edges):
        B1[i, e_idx] = -1
        B1[j, e_idx] = +1
    edge_set = set(edges)
    triangles = []
    for a, b, c in combinations(range(n), 3):
        if (a,b) in edge_set and (b,c) in edge_set and (c,a) in edge_set:
            triangles.append([(a,b),(b,c),(c,a)])
        elif (a,c) in edge_set and (c,b) in edge_set and (b,a) in edge_set:
            triangles.append([(a,c),(c,b),(b,a)])
    T = len(triangles)
    B2 = np.zeros((E, T))
    if T > 0:
        edge_index = {e: idx for idx, e in enumerate(edges)}
        for t_idx, tri in enumerate(triangles):
            for e in tri:
                if e in edge_index:
                    B2[edge_index[e], t_idx] = +1
    return edges, B1, B2


def hodge_spectrum(B1, B2, top_k=5):
    if B1 is None or B1.shape[1] == 0:
        return np.zeros(top_k)
    L1 = B1.T @ B1
    if B2 is not None and B2.shape[1] > 0:
        L1 = L1 + B2 @ B2.T
    try:
        eigenvalues = np.linalg.eigvalsh(L1)
        eigenvalues = np.sort(eigenvalues)[::-1]
    except:
        eigenvalues = np.array([0.0])
    if len(eigenvalues) < top_k:
        eigenvalues = np.pad(eigenvalues, (0, top_k - len(eigenvalues)))
    return eigenvalues[:top_k]


def angle_distance(sigma, sigma_bar):
    ns, nb = np.linalg.norm(sigma), np.linalg.norm(sigma_bar)
    if ns < 1e-12 or nb < 1e-12:
        return 0.0
    cos_d = np.clip(np.dot(sigma, sigma_bar) / (ns * nb), -1.0, 1.0)
    return float(np.arccos(cos_d))


def flow_decompose(rets_1d, members, win_start, win_end):
    n = len(members)
    if n < 3:
        return 0.0, 0.0
    w1 = np.nan_to_num(rets_1d[win_start:win_end, members], nan=0.0)
    T = w1.shape[0]
    if T < 5:
        return 0.0, 0.0
    rets_5d_w = np.zeros((T - 4, n))
    for i in range(n):
        cum = np.cumsum(w1[:, i])
        rets_5d_w[:, i] = cum[4:] - cum[:-4]
    edge_list = list(combinations(range(n), 2))
    E, V = len(edge_list), n
    X = np.zeros(E)
    for e_idx, (i, j) in enumerate(edge_list):
        ri_lag, rj_fwd = w1[:-1, i], w1[1:, j]
        rj_lag, ri_fwd = w1[:-1, j], w1[1:, i]
        if np.std(ri_lag) < 1e-10 or np.std(rj_fwd) < 1e-10:
            continue
        if np.std(rj_lag) < 1e-10 or np.std(ri_fwd) < 1e-10:
            continue
        c_ij = np.corrcoef(ri_lag, rj_fwd)[0, 1]
        c_ji = np.corrcoef(rj_lag, ri_fwd)[0, 1]
        if rets_5d_w.shape[0] > 2 and np.std(rets_5d_w[:, i]) > 1e-10 and np.std(rets_5d_w[:, j]) > 1e-10:
            rho5, _ = spearmanr(rets_5d_w[:, i], rets_5d_w[:, j])
        else:
            rho5 = 0.0
        X[e_idx] = 0.5 * (c_ij - c_ji) * max(abs(float(rho5)), 0.05)
    norm_X = np.linalg.norm(X)
    if norm_X < 1e-12:
        return 0.0, 0.0
    B1 = np.zeros((V, E))
    for e_idx, (i, j) in enumerate(edge_list):
        B1[i, e_idx] = -1
        B1[j, e_idx] = +1
    L0 = B1 @ B1.T
    try:
        phi_hat = np.linalg.pinv(L0) @ B1 @ X
    except:
        phi_hat = np.zeros(V)
    grad = B1.T @ phi_hat
    tri_list = list(combinations(range(n), 3))
    T_tri = len(tri_list)
    B2 = np.zeros((E, T_tri))
    edge_index = {e: idx for idx, e in enumerate(edge_list)}
    for t_idx, (a, b, c) in enumerate(tri_list):
        for e in [(a,b),(b,c),(a,c)]:
            if e in edge_index:
                B2[edge_index[e], t_idx] = +1
    residual = X - grad
    if T_tri > 0:
        try:
            psi_hat, _, _, _ = lstsq(B2, residual)
            curl = B2 @ psi_hat
        except:
            curl = np.zeros(E)
    else:
        curl = np.zeros(E)
    return float(np.dot(grad, grad) / (norm_X ** 2)), float(np.dot(curl, curl) / (norm_X ** 2))


# =================== INDIVIDUAL ETF PREDICTION ===================
def compute_etf_predictions(closes, rets_1d, etf_codes, idx, lead_members, lag_members,
                             sigma_lead, sigma_lag, d_lead, d_lag, anomaly_lead, anomaly_lag):
    """Compute individual ETF level predictions."""
    predictions = []

    for ci in range(len(etf_codes)):
        name = ETF_NAMES.get(etf_codes[ci], etf_codes[ci])
        in_lead = ci in lead_members
        cluster = '领涨' if in_lead else '领跌'

        # Momentum at multiple horizons
        mom_5 = closes[idx, ci] / closes[idx-5, ci] - 1 if closes[idx-5, ci] > 0 else 0
        mom_20 = closes[idx, ci] / closes[idx-20, ci] - 1 if closes[idx-20, ci] > 0 else 0
        mom_60 = closes[idx, ci] / closes[idx-60, ci] - 1 if closes[idx-60, ci] > 0 else 0

        # Momentum decay: 5d mom vs 60d mom
        # If 5d mom << 60d mom/12 (expected proportional), momentum is decaying
        mom_decay = mom_5 - mom_60 / 12  # positive = accelerating, negative = decaying

        # Lead-lag flow signal: does this ETF lead others?
        # Compute average cross-correlation of its lagged returns with others' current returns
        lead_score = 0
        count = 0
        for cj in range(len(etf_codes)):
            if cj == ci:
                continue
            ri_lag = rets_1d[idx-W_WIN:idx, ci]
            rj_fwd = rets_1d[idx-W_WIN+1:idx+1, cj]
            if np.std(ri_lag) > 1e-10 and np.std(rj_fwd) > 1e-10:
                c = np.corrcoef(ri_lag, rj_fwd)[0, 1]
                lead_score += c
                count += 1
        if count > 0:
            lead_score /= count

        # Prediction logic
        signals = []

        # Signal 1: Lead cluster member with decaying momentum + anomaly
        if in_lead and mom_decay < -0.01 and anomaly_lead:
            signals.append(f'动量衰减({mom_decay*100:+.1f}%) + 领涨簇结构异常 → 见顶风险')

        # Signal 2: Lag cluster member with positive 20d momentum + anomaly in lag
        if not in_lead and mom_20 > 0 and anomaly_lag:
            signals.append(f'领跌簇中20日动量转正({mom_20*100:+.1f}%) + 结构异常 → 反弹信号')

        # Signal 3: High lead score (this ETF leads others)
        if lead_score > 0.15:
            signals.append(f'领先指标得分高({lead_score:.2f}) → 趋势风向标')

        # Signal 4: Momentum acceleration
        if mom_decay > 0.02 and mom_60 > 0:
            signals.append(f'动量加速({mom_decay*100:+.1f}%) → 短期看好')

        # Signal 5: Momentum sharp reversal
        if mom_5 < -0.03 and mom_60 > 0.05:
            signals.append(f'短期急跌({mom_5*100:+.1f}%)但中期仍涨 → 回调或反转')

        pred = '—'.join(signals) if signals else '无明显信号'

        predictions.append({
            'name': name,
            'code': etf_codes[ci],
            'cluster': cluster,
            'mom_5': mom_5,
            'mom_20': mom_20,
            'mom_60': mom_60,
            'mom_decay': mom_decay,
            'lead_score': lead_score,
            'prediction': pred,
            'has_signal': len(signals) > 0,
        })

    # Sort by prediction strength (signals first, then by momentum)
    predictions.sort(key=lambda x: (not x['has_signal'], -abs(x['mom_decay'])))
    return predictions


# =================== MONITOR ===================
def run_monitor():
    """Run Hodge Laplacian monitoring for current day."""
    df, etf_codes, bench_df = load_or_fetch_data()

    # ── 数据新鲜度闸门：缺任何一只ETF的最新交易日数据，坚决拒绝计算与发送 ──
    latest_d = df['date'].max().date()
    missing = _missing_today_cols(df, etf_codes, latest_d)
    bench_latest = bench_df['date'].max().date()
    bench_has_close = bench_df.loc[bench_df['date'].dt.date == bench_latest, 'close'].notna().any()
    gate_failed = []
    if missing:
        gate_failed.append(f'ETF缺{latest_d}数据: {",".join(missing)}')
    if bench_latest < latest_d or not bench_has_close:
        gate_failed.append(f'基准({BENCHMARK_NAME})缺最新收盘价: latest={bench_latest} has_close={bench_has_close}')
    if gate_failed:
        msg = '[DATA-GATE] 数据不完整，拒绝生成/发送今日报告: ' + '; '.join(gate_failed)
        print(f'❌ {msg}')
        print('请稍后重试，待腾讯接口全部刷新后再运行（绝不使用旧数据出报告）。')
        sys.exit(3)

    dates = df['date'].values
    closes = df[etf_codes].values.astype(float)
    # 历史缺失值用前向填充（正常情况下不应再出现NaN，闸门已在上方拦截今日缺失）
    closes = pd.DataFrame(closes, columns=etf_codes).ffill().bfill().values
    closes = np.nan_to_num(closes, nan=1.0)
    N = len(etf_codes)

    rets_1d = np.zeros_like(closes)
    rets_1d[1:] = closes[1:] / closes[:-1] - 1
    rets_1d = np.nan_to_num(rets_1d, nan=0.0)

    rets_5d = np.zeros_like(closes)
    for t in range(5, len(closes)):
        rets_5d[t] = closes[t] / closes[t-5] - 1
    rets_5d = np.nan_to_num(rets_5d, nan=0.0)

    idx = len(dates) - 1
    today_str = pd.Timestamp(dates[idx]).strftime('%Y-%m-%d')

    # 1. Trend filter: 中证全A vs MA40
    bench_s = bench_df.set_index('date')['close']
    bench_aligned = bench_s.reindex(pd.to_datetime(dates)).ffill().bfill().values
    ma = pd.Series(bench_aligned).rolling(MA_WINDOW, min_periods=1).mean().values
    bench_now = float(bench_aligned[idx])
    ma_now = float(ma[idx])
    trend_ok = bench_now >= ma_now
    trend_pct = (bench_now / ma_now - 1) * 100

    # 2. Cluster membership
    universe = [ci for ci in range(N) if np.where(~np.isnan(df[etf_codes[ci]].values))[0][0] <= idx - 60]
    momenta = [(closes[idx, ci] / closes[idx - MOM_WIN, ci] - 1 if closes[idx - MOM_WIN, ci] > 0 else -999, ci) for ci in universe]
    momenta.sort(key=lambda x: -x[0])
    half = len(momenta) // 2
    lead_members = [m[1] for m in momenta[:half]]
    lag_members = [m[1] for m in momenta[half:]]

    lead_names = [ETF_NAMES[etf_codes[ci]] for ci in lead_members]
    lag_names = [ETF_NAMES[etf_codes[ci]] for ci in lag_members]
    lead_mom = float(np.mean([m[0] for m in momenta[:half]]))
    lag_mom = float(np.mean([m[0] for m in momenta[half:]]))
    # 成员级动量标注：(名称, 当日涨跌, 60日动量)，顺序与 lead_names/lag_names 一致（按60日动量降序）
    def _mem_label(ci, mom_60):
        d1 = closes[idx, ci] / closes[idx - 1, ci] - 1 if closes[idx - 1, ci] > 0 else 0.0
        return (ETF_NAMES[etf_codes[ci]], d1, mom_60)
    lead_members_mom = [_mem_label(ci, m) for m, ci in momenta[:half]]
    lag_members_mom = [_mem_label(ci, m) for m, ci in momenta[half:]]

    # 3. Hodge spectrum + angle distance history
    win_start = idx - W_WIN
    win_end = idx + 1

    # Build spectrum history by scanning backward
    scan_start = max(MOM_WIN + W_WIN + 10, win_start - W_Q)
    spec_hist_lead, spec_hist_lag = [], []
    d_hist_lead, d_hist_lag = [], []
    e_grad_hist, e_curl_hist = [], []

    # Pre-compute all historical spectra
    all_spec_lead = []
    all_spec_lag = []
    for scan_idx in range(scan_start, idx + 1):
        ws = scan_idx - W_WIN
        we = scan_idx + 1
        if ws < 0:
            continue
        _, B1_sl, B2_sl = build_complex(rets_5d, rets_1d, lead_members, ws, we)
        s_l = hodge_spectrum(B1_sl, B2_sl)
        all_spec_lead.append(s_l)

        _, B1_sg, B2_sg = build_complex(rets_5d, rets_1d, lag_members, ws, we)
        s_g = hodge_spectrum(B1_sg, B2_sg)
        all_spec_lag.append(s_g)

        # Flow decomposition for this window
        eg, ec = flow_decompose(rets_1d, lag_members, ws, we)
        e_grad_hist.append(eg)
        _, ec_l = flow_decompose(rets_1d, lead_members, ws, we)
        e_curl_hist.append(ec_l)

    # Current spectrum
    sigma_lead = hodge_spectrum(*build_complex(rets_5d, rets_1d, lead_members, win_start, win_end)[1:])
    sigma_lag = hodge_spectrum(*build_complex(rets_5d, rets_1d, lag_members, win_start, win_end)[1:])

    n_edges_lead = len(build_complex(rets_5d, rets_1d, lead_members, win_start, win_end)[0])
    n_edges_lag = len(build_complex(rets_5d, rets_1d, lag_members, win_start, win_end)[0])

    # Angle distance: current vs rolling mean
    spec_hist_lead = all_spec_lead[-W_MA:]
    spec_hist_lag = all_spec_lag[-W_MA:]

    if len(spec_hist_lead) >= 2:
        sigma_bar_lead = np.mean(spec_hist_lead, axis=0)
        d_lead = angle_distance(sigma_lead, sigma_bar_lead)
    else:
        d_lead = 0.0

    if len(spec_hist_lag) >= 2:
        sigma_bar_lag = np.mean(spec_hist_lag, axis=0)
        d_lag = angle_distance(sigma_lag, sigma_bar_lag)
    else:
        d_lag = 0.0

    # Historical angle distances for quantile
    if len(all_spec_lead) >= 2:
        sigma_bar_all_lead = np.mean(all_spec_lead, axis=0)
        d_hist_lead = [angle_distance(s, sigma_bar_all_lead) for s in all_spec_lead]
    else:
        d_hist_lead = [0.0]

    if len(all_spec_lag) >= 2:
        sigma_bar_all_lag = np.mean(all_spec_lag, axis=0)
        d_hist_lag = [angle_distance(s, sigma_bar_all_lag) for s in all_spec_lag]
    else:
        d_hist_lag = [0.0]

    # Anomaly detection
    anomaly_lead = False
    anomaly_lag = False
    q90_lead = 0.0
    q90_lag = 0.0
    if len(d_hist_lead) >= MIN_HIST:
        q90_lead = float(np.percentile(d_hist_lead[-W_Q:], Q_TH * 100))
        anomaly_lead = d_lead > q90_lead and d_lead > FLOOR
    elif len(d_hist_lead) >= 10:
        q90_lead = float(np.percentile(d_hist_lead, Q_TH * 100))
        anomaly_lead = d_lead > q90_lead and d_lead > FLOOR

    if len(d_hist_lag) >= MIN_HIST:
        q90_lag = float(np.percentile(d_hist_lag[-W_Q:], Q_TH * 100))
        anomaly_lag = d_lag > q90_lag and d_lag > FLOOR
    elif len(d_hist_lag) >= 10:
        q90_lag = float(np.percentile(d_hist_lag, Q_TH * 100))
        anomaly_lag = d_lag > q90_lag and d_lag > FLOOR

    # 4. Flow decomposition z-scores
    e_grad_now = e_grad_hist[-1] if e_grad_hist else 0.0
    e_curl_now = e_curl_hist[-1] if e_curl_hist else 0.0

    z_in = None
    if len(e_grad_hist) >= 10:
        hist = e_grad_hist[-W_Q:] if len(e_grad_hist) >= W_Q else e_grad_hist
        mu = float(np.mean(hist))
        std = float(np.std(hist))
        if std > 1e-10:
            z_in = (e_grad_now - mu) / std

    z_out = None
    if len(e_curl_hist) >= 10:
        hist = e_curl_hist[-W_Q:] if len(e_curl_hist) >= W_Q else e_curl_hist
        mu = float(np.mean(hist))
        std = float(np.std(hist))
        if std > 1e-10:
            z_out = (e_curl_now - mu) / std

    # 5. Individual ETF predictions
    predictions = compute_etf_predictions(
        closes, rets_1d, etf_codes, idx, lead_members, lag_members,
        sigma_lead, sigma_lag, d_lead, d_lag, anomaly_lead, anomaly_lag
    )

    # 6. Position recommendation
    if not trend_ok:
        position = '空仓'
        reason = f'{BENCHMARK_NAME}({bench_now:.1f}) < MA{MA_WINDOW}({ma_now:.1f})，趋势下行，空仓避险'
    else:
        position = '持领涨半区'
        reason = f'{BENCHMARK_NAME}({bench_now:.1f}) >= MA{MA_WINDOW}({ma_now:.1f})，趋势上行'
        if anomaly_lead:
            position = '持领涨（注意结构变化）'
            reason += f'；领涨簇谱角距离异常({d_lead:.3f}>{q90_lead:.3f})'
        if anomaly_lag:
            reason += f'；领跌簇谱角距离异常({d_lag:.3f}>{q90_lag:.3f})'
        if z_in is not None and z_in > 1.5:
            reason += f'；z_in={z_in:.2f}，领跌簇流入组织化增强'

    lag_mom_20 = float(np.mean([closes[idx, ci] / closes[idx - 20, ci] - 1
                                 for ci in lag_members if closes[idx - 20, ci] > 0]))

    report = {
        'date': today_str,
        'benchmark': bench_now,
        'ma40': ma_now,
        'trend_ok': trend_ok,
        'trend_pct': trend_pct,
        'position': position,
        'reason': reason,
        'lead_names': lead_names,
        'lag_names': lag_names,
        'lead_members_mom': lead_members_mom,
        'lag_members_mom': lag_members_mom,
        'lead_mom': lead_mom,
        'lag_mom': lag_mom,
        'lag_mom_20': lag_mom_20,
        'd_lead': d_lead,
        'd_lag': d_lag,
        'q90_lead': q90_lead,
        'q90_lag': q90_lag,
        'anomaly_lead': anomaly_lead,
        'anomaly_lag': anomaly_lag,
        'z_in': z_in,
        'z_out': z_out,
        'n_edges_lead': n_edges_lead,
        'n_edges_lag': n_edges_lag,
        'predictions': predictions,
    }

    return report


# =================== FEISHU REPORT ===================
def _build_stock_text(report):
    """构建个股推荐文本"""
    recs = report.get('stock_recommendations', {})
    if not recs:
        return '当前无个股推荐数据'
    lines = []
    for etf_code, info in recs.items():
        etf_name = info.get('name', etf_code)
        stocks = info.get('stocks', [])
        if stocks:
            stock_str = '  '.join([f"{s['name']}({s['code']}) {s['mom_20']*100:+.1f}%" for s in stocks])
            lines.append(f"▪️ **{etf_name}**（{etf_code}）\n  {stock_str}")
    return '\n'.join(lines) if lines else '当前无个股推荐数据'


# 方向标记：看多=🟢 绿色圆点，看空=🔴 红色圆点，其余=➖（指数/行业结论醒目提示，站远处也能一眼看到）
DIR_MARK_BULL = '🟢'
DIR_MARK_BEAR = '🔴'
DIR_MARK_FLAT = '➖'


def _dir_marker(text):
    """按结论文本归类方向标记：看好/看多 → 🟢；风险/见顶/看空/偏空 → 🔴；其余 → ➖"""
    if not text:
        return DIR_MARK_FLAT
    if ('看好' in text) or ('看多' in text):
        return DIR_MARK_BULL
    if ('风险' in text) or ('见顶' in text) or ('看空' in text) or ('偏空' in text):
        return DIR_MARK_BEAR
    return DIR_MARK_FLAT


def send_feishu(report):
    """Send Hodge Laplacian monitoring report to Feishu."""

    d_lead_str = f'{report["d_lead"]:.3f}' if report['d_lead'] else 'N/A'
    d_lag_str = f'{report["d_lag"]:.3f}' if report['d_lag'] else 'N/A'
    q90_l_str = f'{report["q90_lead"]:.3f}' if report['q90_lead'] else 'N/A'
    q90_g_str = f'{report["q90_lag"]:.3f}' if report['q90_lag'] else 'N/A'
    z_in_str = f'{report["z_in"]:.2f}' if report['z_in'] is not None else 'N/A'
    z_out_str = f'{report["z_out"]:.2f}' if report['z_out'] is not None else 'N/A'

    if '空仓' in report['position']:
        pos_emoji = '⚪'
    elif '注意' in report['position']:
        pos_emoji = '🟡'
    else:
        pos_emoji = '🟢'

    anomaly_badge_l = '🔴 异常' if report['anomaly_lead'] else '🟢 正常'
    anomaly_badge_g = '🔴 异常' if report['anomaly_lag'] else '🟢 正常'

    trend_badge = '↑ 上行' if report['trend_ok'] else '↓ 下行'
    lead_str = '、'.join(report['lead_names'])
    lag_str = '、'.join(report['lag_names'])
    # 成员级标注：名称(当日/60d动量)，如 农业(+0.7%/+11.5%)，60日动量=分组口径
    lead_mem_str = '、'.join(f"{n}({d*100:+.1f}%/{m*100:+.1f}%)"
                             for n, d, m in report.get('lead_members_mom', [])) or lead_str
    lag_mem_str = '、'.join(f"{n}({d*100:+.1f}%/{m*100:+.1f}%)"
                            for n, d, m in report.get('lag_members_mom', [])) or lag_str

    # Build ETF prediction section (top signals only)
    # 板块/行业结论前加方向标记：看好 → 🟢，风险/见顶 → 🔴，其余 → ➖
    pred_lines = []
    for p in report['predictions'][:6]:
        if p['has_signal']:
            mk = _dir_marker(p['prediction'])
            pred_lines.append(f"{mk} ▪️ **{p['name']}** [{p['cluster']}] 5d:{p['mom_5']*100:+.1f}% 20d:{p['mom_20']*100:+.1f}%\n  → {p['prediction']}")
    if not pred_lines:
        pred_lines.append('当前无个股级异常信号')

    pred_text = '\n'.join(pred_lines)

    # 五大宽基风险块（2026-10-22 新增）
    try:
        import broad5_risk
        broad5_text = broad5_risk.summary_text(markers=True)
    except Exception as _e:
        broad5_text = f"**【五大宽基风险监控】**\n数据获取失败，本次未纳入：{str(_e)[:80]}"

    _sig_preds = [p for p in report['predictions'][:6] if p['has_signal']]
    _has_risk = any(_dir_marker(p['prediction']) == DIR_MARK_BEAR for p in _sig_preds)
    _has_opp = any(_dir_marker(p['prediction']) == DIR_MARK_BULL for p in _sig_preds)
    _mark = ('注意风险 | ' if _has_risk else '') + ('注意机会 | ' if _has_opp else '')
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{REPORT_HEADER} | {report['date']} | {_mark}来自 DoMate"
            },
            "template": "blue" if report['trend_ok'] else "red"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{pos_emoji} 持仓建议：{report['position']}**\n{report['reason']}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": broad5_text}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**趋势状态**\n{trend_badge} ({report['trend_pct']:+.1f}%)"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**{BENCHMARK_NAME} / MA{MA_WINDOW}**\n{report['benchmark']:.1f} / {report['ma40']:.1f}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**领涨簇动量(60d)**\n{report['lead_mom']*100:+.1f}%"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**领跌簇动量(60d)**\n{report['lag_mom']*100:+.1f}%"}},
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**🟢 领涨半区**（按60日动量排序，名称括号内 = 当日/60日动量）"}
            },
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**成员**\n{lead_mem_str}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**谱角距离**\n{d_lead_str} / 阈值{q90_l_str}\n{anomaly_badge_l}"}},
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**🔴 领跌半区**（按60日动量排序，名称括号内 = 当日/60日动量）"}
            },
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**成员**\n{lag_mem_str}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**谱角距离**\n{d_lag_str} / 阈值{q90_g_str}\n{anomaly_badge_g}"}},
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**z_in (流入组织化)**\n{z_in_str}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**z_out (流出组织化)**\n{z_out_str}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**领跌20日动量**\n{report['lag_mom_20']*100:+.1f}%"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**复形边数**\n领涨{report['n_edges_lead']} / 领跌{report['n_edges_lag']}"}},
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**📋 板块预测信号**"}
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": pred_text}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**📋 个股推荐（20日动量Top3·剔除短期急跌板块）**"}
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": _build_stock_text(report)}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**📖 指标解释**\n"
                    "**谱角距离**：当前Hodge Laplacian特征值谱与历史均谱的夹角。距离越大=ETF间关联结构变化越剧烈，超过90%分位数=异常，意味着该簇内部领涨/领跌关系正在重构。\n"
                    "**z_in**：领跌簇的梯度流z-score，正值=资金流入组织化增强，可能预示反转。\n"
                    "**z_out**：领涨簇的旋度流z-score，正值=内部循环加剧，结构松散化。\n"
                    f"**趋势过滤**：{BENCHMARK_NAME} vs MA{MA_WINDOW}，线下空仓避免系统性下跌。\n"
                    "**分组口径**：领涨/领跌半区 = 按**60日动量**排序取前/后半。成员括号内 = 当日涨跌/60日动量，当日下跌不代表60日动量转弱（如农业当日-0.7%但60日+11.5%）。\n"
                    "**动量衰减**：5日动量减去60日动量的1/12，负值=动量正在衰减。"
                }
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text",
                     "content": f"Hodge Laplacian Monitor v5.1 + MA{MA_WINDOW}({BENCHMARK_NAME}) | 动量决定买什么，霍奇拉普拉斯决定什么时候换"}
                ]
            }
        ]
    }
    card["elements"] += _bs('hlm', 'card')

    payload = {"msg_type": "interactive", "card": card}

    print(f'\nSending Feishu report...')
    print(f'Position: {report["position"]}')
    print(f'Trend: {"OK" if report["trend_ok"] else "BELOW MA"} ({report["trend_pct"]:+.1f}%)')
    print(f'd_lead: {d_lead_str}, d_lag: {d_lag_str}')
    print(f'z_in: {z_in_str}, z_out: {z_out_str}')
    print(f'Predictions with signals: {sum(1 for p in report["predictions"] if p["has_signal"])}')

    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        resp = r.json()
        if resp.get('StatusCode') == 0 or resp.get('code') == 0 or resp.get('msg') == 'success':
            print(f'Feishu report sent successfully')
            return True
        else:
            print(f'Feishu API error: {resp}')
            return False
    except Exception as e:
        print(f'Send error: {e}')
        return False


# =================== MAIN ===================
def fetch_stock_picks(report, etf_codes):
    """为领涨半区每只 ETF 拉取 20 日动量 Top3 个股（剔除短期急跌板块）"""
    try:
        from stock_picker import get_top_stocks_for_etf, ETF_STOCK_POOL
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from stock_picker import get_top_stocks_for_etf, ETF_STOCK_POOL

    MOM5_FLOOR = -0.03  # 板块自身5日跌幅超过3%时不推荐个股（信号提示仍保留）

    def _eligible(p):
        return p.get('cluster') == '领涨' and p.get('mom_5', 0) >= MOM5_FLOOR

    stock_recs = {}
    lead_codes = []
    # 从 predictions 中提取领涨簇的 ETF code，剔除短期急跌板块
    for p in report.get('predictions', []):
        if _eligible(p):
            lead_codes.append(p['code'])

    skipped = [p['name'] for p in report.get('predictions', [])
               if p.get('cluster') == '领涨' and p.get('mom_5', 0) < MOM5_FLOOR]
    if skipped:
        print(f'  已剔除短期急跌板块(5日<-3%): {", ".join(skipped)}')

    # 如果没拿到，取动量 top5（同样过滤）
    if not lead_codes:
        eligible = [p for p in report.get('predictions', []) if _eligible(p)]
        sorted_preds = sorted(eligible, key=lambda x: -x.get('mom_60', 0))
        lead_codes = [p['code'] for p in sorted_preds[:5]]

    for code in lead_codes[:5]:
        picks = get_top_stocks_for_etf(code, top_n=3)
        if picks:
            etf_name = ETF_NAMES.get(code, code)
            stock_recs[code] = {'name': etf_name, 'stocks': picks}
            print(f'  {etf_name}({code}): {", ".join(s["name"] for s in picks)}')

    report['stock_recommendations'] = stock_recs
    return report


if __name__ == '__main__':
    print(f'=== Hodge Laplacian Daily Monitor ===')
    print(f'Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'Trend Filter: {BENCHMARK_NAME} vs MA{MA_WINDOW}')
    print()

    report = run_monitor()

    print(f'\n=== Monitor Result ({report["date"]}) ===')
    print(f'Position: {report["position"]}')
    print(f'Trend: {BENCHMARK_NAME} {report["benchmark"]:.1f} vs MA{MA_WINDOW} {report["ma40"]:.1f} ({report["trend_pct"]:+.1f}%)')
    print(f'Lead: {"、".join(report["lead_names"])} (mom {report["lead_mom"]*100:+.1f}%)')
    print(f'Lag:  {"、".join(report["lag_names"])} (mom {report["lag_mom"]*100:+.1f}%)')
    print(f'd_lead: {report["d_lead"]:.3f}, d_lag: {report["d_lag"]:.3f}')
    print(f'z_in: {report["z_in"]}, z_out: {report["z_out"]}')
    print(f'\n--- ETF Predictions ---')
    for p in report['predictions']:
        if p['has_signal']:
            print(f'  {p["name"]} [{p["cluster"]}] 5d:{p["mom_5"]*100:+.1f}% 20d:{p["mom_20"]*100:+.1f}% → {p["prediction"]}')

    print(f'\n--- Fetching Stock Picks ---')
    report = fetch_stock_picks(report, None)

    send_feishu(report)

    report_path = os.path.join(SCRIPT_DIR, f'hlm_report_{report["date"]}.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f'\nReport saved to {report_path}')
