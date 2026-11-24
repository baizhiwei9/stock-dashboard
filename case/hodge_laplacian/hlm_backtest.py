#!/usr/bin/env python3
"""
Hodge Laplacian Monitor (HLM) Backtest Engine
Based on design doc v5.1 — A-share ETF rotation timing system.

Core logic:
  - Momentum decides WHAT to buy; Hodge Laplacian decides WHEN to switch.
  - Default: hold "leading half" (60-day momentum top half, equal weight).
  - On structural break signal (lead cluster decay + lag cluster anomaly + entry confirmation):
    switch to "lagging half" for 20 trading days, then revert.
"""
import os, sys, json, time, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, f as f_dist
from scipy.linalg import lstsq

warnings.filterwarnings('ignore')

# =================== PARAMETERS (from design doc §7) ===================
W_WIN   = 40    # complex/flow window
W_MA    = 40    # mean spectrum buffer
W_Q     = 120   # quantile lookback
Q_TH    = 0.90  # anomaly quantile
FLOOR   = 0.25  # angle distance floor
DROP    = 0.20  # peak drop confirmation
RHO_TH  = 0.4   # Spearman correlation edge threshold
P_TH    = 0.10  # Granger p threshold
REBAL   = 10    # rebalance period
MOM_WIN = 60    # momentum lookback
HOLD    = 20    # reversal hold period
SEQ     = 15    # sequential trigger window
TRANS   = 3     # transition period shield
MIN_HIST = 60   # min history for quantile/z-score
Z_TH    = 1.5   # flow z threshold (not used as trigger)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data_long')
RESULTS_DIR = os.path.dirname(__file__)

ETF_NAMES = {
    '512480': '半导体', '512760': '芯片', '515880': '通信', '515050': '5G通信',
    '510300': '沪深300', '510500': '中证500', '512100': '中证1000', '512890': '红利低波',
    '512200': '房地产', '512010': '医药', '512690': '酒', '512800': '银行',
    '512660': '军工', '159915': '创业板', '510050': '上证50', '510880': '红利',
    '512720': '计算机', '159928': '消费',
}


# =================== DATA LOADING ===================
def load_data():
    """Load close price matrix and HS300 benchmark."""
    df = pd.read_csv(os.path.join(DATA_DIR, 'close_matrix.csv'), parse_dates=['date'])
    df = df.sort_values('date').reset_index(drop=True)
    etf_codes = [c for c in df.columns if c != 'date']
    
    hs300 = pd.read_csv(os.path.join(DATA_DIR, 'hs300.csv'), parse_dates=['date'])
    hs300 = hs300.sort_values('date').reset_index(drop=True)
    
    return df, etf_codes, hs300


# =================== GRANGER CAUSALITY (AIC single lag) ===================
def granger_aic_test(ret_i, ret_j, max_lag=5):
    """
    Test if i Granger-causes j.
    For each lag L=1..5, fit restricted (j only) and unrestricted (j+i) OLS.
    Select lag with min AIC (unrestricted model).
    Return (p_value at best lag, is_significant).
    """
    # Clean data: remove NaN/Inf
    mask = np.isfinite(ret_i) & np.isfinite(ret_j)
    ret_i = ret_i[mask]
    ret_j = ret_j[mask]
    
    n_total = len(ret_j)
    if n_total < 15:
        return 1.0, False
    
    best_aic = np.inf
    best_pval = 1.0
    
    for lag in range(1, max_lag + 1):
        n = n_total - lag
        if n < lag * 2 + 5:
            continue
        
        y = ret_j[lag:]
        cols_r = [np.ones(n)]
        cols_u = [np.ones(n)]
        for l in range(1, lag + 1):
            cols_r.append(ret_j[lag-l:n_total-l])
            cols_u.append(ret_j[lag-l:n_total-l])
            cols_u.append(ret_i[lag-l:n_total-l])
        
        X_r = np.column_stack(cols_r)
        X_u = np.column_stack(cols_u)
        
        try:
            beta_r, _, _, _ = np.linalg.lstsq(X_r, y, rcond=None)
            beta_u, _, _, _ = np.linalg.lstsq(X_u, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        
        resid_r = y - X_r @ beta_r
        resid_u = y - X_u @ beta_u
        rss_r = np.dot(resid_r, resid_r)
        rss_u = np.dot(resid_u, resid_u)
        
        if rss_u <= 1e-15 or rss_r <= 1e-15:
            continue
        
        k_u = X_u.shape[1]
        aic = n * np.log(rss_u / n) + 2 * k_u
        
        if aic < best_aic:
            best_aic = aic
            p_restrict = lag
            df1 = p_restrict
            df2 = n - k_u
            if df2 > 0:
                F_stat = ((rss_r - rss_u) / df1) / (rss_u / df2)
                if F_stat >= 0:
                    best_pval = float(f_dist.sf(F_stat, df1, df2))
    
    return best_pval, best_pval < P_TH


# =================== SIMPLICIAL COMPLEX ===================
def build_complex(rets_5d, rets_1d, members, win_start, win_end):
    """
    Build directed simplicial complex for a cluster within window.
    
    rets_5d: 5-day returns matrix (T x N_ETFs)
    rets_1d: 1-day returns matrix (T x N_ETFs)
    members: list of column indices in the returns matrices
    win_start, win_end: window boundaries
    
    Returns:
      edges: list of (i, j) directed edges (indices into members)
      B1: V x E boundary matrix
      B2: E x T boundary matrix
    """
    n = len(members)
    if n < 2:
        return [], None, None
    
    # Extract window data for members, clean NaN
    w5 = np.nan_to_num(rets_5d[win_start:win_end, members], nan=0.0)  # (W_WIN, n)
    w1 = np.nan_to_num(rets_1d[win_start:win_end, members], nan=0.0)
    
    # Step 1: Spearman rank correlation on 5-day returns
    edges = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            # Spearman correlation of 5-day returns
            if np.std(w5[:, i]) < 1e-10 or np.std(w5[:, j]) < 1e-10:
                continue
            rho, _ = spearmanr(w5[:, i], w5[:, j])
            if abs(rho) < RHO_TH:
                continue
            # Granger causality i -> j (on 1-day returns)
            pval, significant = granger_aic_test(w1[:, i], w1[:, j])
            if significant:
                edges.append((i, j))
    
    if len(edges) == 0:
        return [], None, None
    
    # Step 2: Build boundary matrix B1 (V x E)
    V = n
    E = len(edges)
    B1 = np.zeros((V, E))
    for e_idx, (i, j) in enumerate(edges):
        B1[i, e_idx] = -1
        B1[j, e_idx] = +1
    
    # Step 3: Find 2-simplices (directed 3-cycles)
    edge_set = set(edges)
    triangles = []
    from itertools import combinations
    for a, b, c in combinations(range(n), 3):
        # Check both directions of 3-cycle
        # a->b->c->a
        if (a, b) in edge_set and (b, c) in edge_set and (c, a) in edge_set:
            triangles.append((a, b, c, [(a, b), (b, c), (c, a)]))
        # a->c->b->a
        elif (a, c) in edge_set and (c, b) in edge_set and (b, a) in edge_set:
            triangles.append((a, c, b, [(a, c), (c, b), (b, a)]))
    
    T = len(triangles)
    B2 = np.zeros((E, T))
    if T > 0:
        edge_index = {e: idx for idx, e in enumerate(edges)}
        for t_idx, (a, b, c, tri_edges) in enumerate(triangles):
            for e in tri_edges:
                if e in edge_index:
                    B2[edge_index[e], t_idx] = +1
    
    return edges, B1, B2


# =================== HODGE LAPLACIAN SPECTRUM ===================
def hodge_spectrum(B1, B2, top_k=5):
    """Compute top-k eigenvalues of L1 = B1^T B1 + B2 B2^T."""
    if B1 is None or B1.shape[1] == 0:
        return np.zeros(top_k)
    
    L1 = B1.T @ B1
    if B2 is not None and B2.shape[1] > 0:
        L1 = L1 + B2 @ B2.T
    
    # L1 is symmetric PSD, eigenvalues = singular values
    try:
        eigenvalues = np.linalg.eigvalsh(L1)
        eigenvalues = np.sort(eigenvalues)[::-1]  # descending
    except:
        eigenvalues = np.array([0.0])
    
    # Pad to top_k
    if len(eigenvalues) < top_k:
        eigenvalues = np.pad(eigenvalues, (0, top_k - len(eigenvalues)))
    return eigenvalues[:top_k]


# =================== SPECTRAL ANGLE DISTANCE ===================
def angle_distance(sigma, sigma_bar):
    """Compute angular distance between spectrum and mean spectrum."""
    norm_s = np.linalg.norm(sigma)
    norm_b = np.linalg.norm(sigma_bar)
    if norm_s < 1e-12 or norm_b < 1e-12:
        return None
    cos_d = np.dot(sigma, sigma_bar) / (norm_s * norm_b)
    cos_d = np.clip(cos_d, -1.0, 1.0)
    return np.arccos(cos_d)


# =================== HODGE FLOW DECOMPOSITION (v6b) ===================
def flow_decompose(rets_1d, members, win_start, win_end):
    """
    Complete-graph Hodge flow decomposition.
    Returns (e_grad, e_curl).
    """
    n = len(members)
    if n < 3:
        return 0.0, 0.0
    
    w1 = np.nan_to_num(rets_1d[win_start:win_end, members], nan=0.0)  # (W_WIN, n)
    T = w1.shape[0]
    
    if T < 5:
        return 0.0, 0.0
    
    # Compute 5-day returns within window for Spearman
    rets_5d_w = np.zeros((T - 4, n))
    for i in range(n):
        cum = np.cumsum(w1[:, i])
        rets_5d_w[:, i] = cum[4:] - cum[:-4]  # 5-day cumulative return
    
    # Build complete graph edges (i < j orientation)
    from itertools import combinations
    edge_list = list(combinations(range(n), 2))  # [(0,1), (0,2), ...]
    E = len(edge_list)
    V = n
    
    # Compute antisymmetric flow X(i,j)
    X = np.zeros(E)
    for e_idx, (i, j) in enumerate(edge_list):
        # lag-1 cross-correlation
        ri_lag = w1[:-1, i]  # r_i(t-1)
        rj_fwd = w1[1:, j]   # r_j(t)
        rj_lag = w1[:-1, j]  # r_j(t-1)
        ri_fwd = w1[1:, i]   # r_i(t)
        
        if np.std(ri_lag) < 1e-10 or np.std(rj_fwd) < 1e-10:
            continue
        if np.std(rj_lag) < 1e-10 or np.std(ri_fwd) < 1e-10:
            continue
        
        c_ij = np.corrcoef(ri_lag, rj_fwd)[0, 1]  # i leads j
        c_ji = np.corrcoef(rj_lag, ri_fwd)[0, 1]  # j leads i
        
        # 5-day Spearman |rho|
        if rets_5d_w.shape[0] > 2 and np.std(rets_5d_w[:, i]) > 1e-10 and np.std(rets_5d_w[:, j]) > 1e-10:
            rho5, _ = spearmanr(rets_5d_w[:, i], rets_5d_w[:, j])
        else:
            rho5 = 0.0
        
        weight = max(abs(rho5), 0.05)
        X[e_idx] = 0.5 * (c_ij - c_ji) * weight
    
    norm_X = np.linalg.norm(X)
    if norm_X < 1e-12:
        return 0.0, 0.0
    
    # Build B1 for complete graph (V x E)
    B1 = np.zeros((V, E))
    for e_idx, (i, j) in enumerate(edge_list):
        B1[i, e_idx] = -1
        B1[j, e_idx] = +1
    
    # Build B2 for complete graph (all triangles)
    tri_list = list(combinations(range(n), 3))
    T_tri = len(tri_list)
    B2 = np.zeros((E, T_tri))
    edge_index = {e: idx for idx, e in enumerate(edge_list)}
    for t_idx, (a, b, c) in enumerate(tri_list):
        # Triangle (a,b,c): edges (a,b), (b,c), (a,c) with consistent orientation
        for e in [(a, b), (b, c), (a, c)]:
            if e in edge_index:
                B2[edge_index[e], t_idx] = +1
    
    # Decompose: X = grad + curl (+ harm=0 on complete graph)
    # grad = B1^T @ phi_hat, phi_hat = argmin ||X - B1^T phi||^2
    # => phi_hat = (B1 B1^T)^{-1} B1 X = L0^{-1} B1 X
    L0 = B1 @ B1.T  # graph Laplacian (V x V)
    try:
        # Use pseudo-inverse for stability
        phi_hat = np.linalg.pinv(L0) @ B1 @ X
    except:
        phi_hat = np.zeros(V)
    grad = B1.T @ phi_hat
    
    # curl = B2 @ psi_hat, psi_hat = argmin ||X - grad - B2 psi||^2
    residual = X - grad
    if T_tri > 0:
        try:
            psi_hat, _, _, _ = lstsq(B2, residual)
            curl = B2 @ psi_hat
        except:
            curl = np.zeros(E)
    else:
        curl = np.zeros(E)
    
    e_grad = np.dot(grad, grad) / (norm_X ** 2)
    e_curl = np.dot(curl, curl) / (norm_X ** 2)
    
    return e_grad, e_curl


# =================== MAIN BACKTEST ===================
def run_backtest(trend_filter=False, ma_window=60):
    """Run the complete HLM backtest from 2019-01 to 2026-08.
    
    Args:
        trend_filter: If True, go to cash when HS300 < MA(ma_window).
        ma_window: Moving average window for trend filter.
    """
    print(f'Loading data... (trend_filter={trend_filter})')
    close_df, etf_codes, hs300 = load_data()
    
    # Forward-fill NaN closes (standard for ETF data)
    close_df[etf_codes] = close_df[etf_codes].ffill().bfill()
    
    # Align dates
    dates = close_df['date'].values
    closes = close_df[etf_codes].values.astype(float)  # (T, N)
    N = len(etf_codes)
    
    # Replace any remaining NaN/Inf with 0
    closes = np.nan_to_num(closes, nan=1.0, posinf=1.0, neginf=1.0)
    
    # Trend filter: HS300 vs MA
    trend_ok = None  # will compute after actual_start_idx is known
    if trend_filter:
        hs300_s = hs300.set_index('date')['close']
        hs300_aligned = hs300_s.reindex(pd.to_datetime(dates)).ffill().bfill().values
        ma = pd.Series(hs300_aligned).rolling(ma_window, min_periods=1).mean().values
        trend_ok = hs300_aligned >= ma
    
    # Compute returns
    rets_1d = np.zeros_like(closes)
    rets_1d[1:] = (closes[1:] / closes[:-1] - 1)
    rets_1d = np.nan_to_num(rets_1d, nan=0.0, posinf=0.0, neginf=0.0)
    
    # 5-day returns
    rets_5d = np.zeros_like(closes)
    for t in range(5, len(closes)):
        rets_5d[t] = closes[t] / closes[t-5] - 1
    rets_5d = np.nan_to_num(rets_5d, nan=0.0, posinf=0.0, neginf=0.0)
    
    # Find backtest start (2019-01-01, but need MOM_WIN + W_WIN warmup)
    bt_start_idx = None
    for i, d in enumerate(dates):
        if pd.Timestamp(d) >= pd.Timestamp('2019-01-01'):
            bt_start_idx = i
            break
    if bt_start_idx is None:
        bt_start_idx = 0
    
    # Warmup: need at least MOM_WIN + W_WIN days before bt_start
    warmup = MOM_WIN + W_WIN + 10
    actual_start = max(warmup, bt_start_idx)
    
    print(f'Backtest: {dates[actual_start]} to {dates[-1]} ({len(dates) - actual_start} days)')
    print(f'ETFs: {N}')
    
    # ===== State variables =====
    # Cluster state
    cluster_members = None  # {'lead': [...], 'lag': [...]}
    last_rebal = -REBAL  # force rebalance on first day
    trans_countdown = 0   # transition period
    
    # Spectrum buffers (per cluster, reset on membership change)
    spec_buffer_lead = []  # list of sigma vectors
    spec_buffer_lag = []
    spec_members_lead = None  # track membership for reset
    spec_members_lag = None
    
    # Angle distance history (for quantile, any membership)
    d_history_lead = []
    d_history_lag = []
    
    # Event state machines (per cluster)
    # States: 'idle', 'tracking' (anomaly started, tracking peak)
    event_state_lead = {'state': 'idle', 't0': None, 'peak': None, 'peak_day': None, 'days_since_peak': 0}
    event_state_lag = {'state': 'idle', 't0': None, 'peak': None, 'peak_day': None, 'days_since_peak': 0}
    last_confirmed_lead = -100  # last confirmed event day (lead)
    last_confirmed_lag = -100
    
    # Trigger state
    pending_alert = None  # {'day': c_lead, 'expiry': c_lead + SEQ}
    last_signal_day = -100
    
    # Position state
    position = 'lead'  # 'lead' or 'lag'
    hold_countdown = 0  # days remaining in reversal position
    
    # Flow history (for z-scores)
    e_grad_history_lag = []
    e_curl_history_lead = []
    
    # ===== Results =====
    daily_returns = []
    daily_dates = []
    signals = []
    position_log = []
    cluster_log = []
    
    print('Running backtest...')
    t0_time = time.time()
    
    for idx in range(actual_start, len(dates)):
        if idx % 200 == 0:
            elapsed = time.time() - t0_time
            print(f'  Day {idx}/{len(dates)} ({dates[idx]}) [{elapsed:.0f}s]')
        
        # ===== 1. Check rebalance (every REBAL days) =====
        days_since_rebal = idx - last_rebal
        if days_since_rebal >= REBAL:
            # Determine universe (ETFs with enough history)
            universe = []
            for ci in range(N):
                first_valid = np.where(~np.isnan(closes[:, ci]))[0]
                if len(first_valid) > 0 and first_valid[0] <= idx - 60:
                    universe.append(ci)
            
            if len(universe) < 4:
                daily_returns.append(0)
                daily_dates.append(dates[idx])
                continue
            
            # Compute 60-day momentum
            momenta = []
            for ci in universe:
                p_now = closes[idx, ci]
                p_past = closes[idx - MOM_WIN, ci]
                if p_past > 0 and not np.isnan(p_now) and not np.isnan(p_past):
                    momenta.append((p_now / p_past - 1, ci))
                else:
                    momenta.append((-999, ci))
            
            momenta.sort(key=lambda x: -x[0])
            half = len(momenta) // 2
            lead_members = [m[1] for m in momenta[:half]]
            lag_members = [m[1] for m in momenta[half:]]
            
            # Check if membership changed
            new_lead_set = set(lead_members)
            new_lag_set = set(lag_members)
            
            if spec_members_lead is not None and set(spec_members_lead) != new_lead_set:
                spec_buffer_lead = []
            if spec_members_lag is not None and set(spec_members_lag) != new_lag_set:
                spec_buffer_lag = []
            
            spec_members_lead = lead_members
            spec_members_lag = lag_members
            cluster_members = {'lead': lead_members, 'lag': lag_members}
            last_rebal = idx
            trans_countdown = TRANS
            cluster_log.append({'day': idx, 'date': dates[idx], 'lead': lead_members, 'lag': lag_members})
        
        if cluster_members is None:
            daily_returns.append(0)
            daily_dates.append(dates[idx])
            continue
        
        # ===== 2. Compute spectrum for each cluster =====
        win_start = idx - W_WIN
        win_end = idx + 1
        
        if win_start < 0:
            daily_returns.append(0)
            daily_dates.append(dates[idx])
            continue
        
        # Lead cluster
        edges_l, B1_l, B2_l = build_complex(rets_5d, rets_1d, cluster_members['lead'], win_start, win_end)
        sigma_lead = hodge_spectrum(B1_l, B2_l)
        
        # Lag cluster
        edges_g, B1_g, B2_g = build_complex(rets_5d, rets_1d, cluster_members['lag'], win_start, win_end)
        sigma_lag = hodge_spectrum(B1_g, B2_g)
        
        # ===== 3. Update spectrum buffers and compute angle distance =====
        spec_buffer_lead.append(sigma_lead)
        if len(spec_buffer_lead) > W_MA:
            spec_buffer_lead = spec_buffer_lead[-W_MA:]
        
        spec_buffer_lag.append(sigma_lag)
        if len(spec_buffer_lag) > W_MA:
            spec_buffer_lag = spec_buffer_lag[-W_MA:]
        
        # Transition period: skip anomaly detection
        is_transition = trans_countdown > 0
        if trans_countdown > 0:
            trans_countdown -= 1
        
        # Angle distance for lead
        d_lead = None
        if len(spec_buffer_lead) >= 2 and not is_transition:
            sigma_bar_lead = np.mean(spec_buffer_lead, axis=0)
            d_lead = angle_distance(sigma_lead, sigma_bar_lead)
        
        # Angle distance for lag
        d_lag = None
        if len(spec_buffer_lag) >= 2 and not is_transition:
            sigma_bar_lag = np.mean(spec_buffer_lag, axis=0)
            d_lag = angle_distance(sigma_lag, sigma_bar_lag)
        
        # Update d history
        if d_lead is not None:
            d_history_lead.append(d_lead)
            if len(d_history_lead) > W_Q * 2:
                d_history_lead = d_history_lead[-W_Q * 2:]
        if d_lag is not None:
            d_history_lag.append(d_lag)
            if len(d_history_lag) > W_Q * 2:
                d_history_lag = d_history_lag[-W_Q * 2:]
        
        # ===== 4. Anomaly detection =====
        # Lead anomaly
        anomaly_lead = False
        if d_lead is not None and len(d_history_lead) >= MIN_HIST:
            lookback = d_history_lead[-W_Q:] if len(d_history_lead) >= W_Q else d_history_lead
            q90 = np.percentile(lookback, Q_TH * 100)
            if d_lead > q90 and d_lead > FLOOR:
                anomaly_lead = True
        
        # Lag anomaly
        anomaly_lag = False
        if d_lag is not None and len(d_history_lag) >= MIN_HIST:
            lookback = d_history_lag[-W_Q:] if len(d_history_lag) >= W_Q else d_history_lag
            q90 = np.percentile(lookback, Q_TH * 100)
            if d_lag > q90 and d_lag > FLOOR:
                anomaly_lag = True
        
        # ===== 5. Event confirmation state machine =====
        def update_event(state, anomaly, d_val, day_idx, last_confirmed):
            """Update event confirmation state machine."""
            if state['state'] == 'idle':
                if anomaly and d_val is not None:
                    state['state'] = 'tracking'
                    state['t0'] = day_idx
                    state['peak'] = d_val
                    state['peak_day'] = day_idx
                    state['days_since_peak'] = 0
            elif state['state'] == 'tracking':
                state['days_since_peak'] = day_idx - state['peak_day']
                # Track peak within [t0, t0+5]
                if day_idx - state['t0'] <= 5 and d_val is not None:
                    if d_val > state['peak']:
                        state['peak'] = d_val
                        state['peak_day'] = day_idx
                        state['days_since_peak'] = 0
                    # Check confirmation: d drops 20% from peak
                    if d_val <= (1 - DROP) * state['peak'] and day_idx - last_confirmed >= 10:
                        confirmed_day = day_idx
                        state['state'] = 'idle'
                        return confirmed_day
                elif state['days_since_peak'] <= 5 and d_val is not None:
                    if d_val <= (1 - DROP) * state['peak'] and day_idx - last_confirmed >= 10:
                        confirmed_day = day_idx
                        state['state'] = 'idle'
                        return confirmed_day
                elif state['days_since_peak'] > 5 or day_idx - state['t0'] > 10:
                    # Expire without confirmation
                    state['state'] = 'idle'
            return None
        
        confirmed_lead = update_event(event_state_lead, anomaly_lead, d_lead, idx, last_confirmed_lead)
        if confirmed_lead is not None:
            last_confirmed_lead = confirmed_lead
        
        confirmed_lag = update_event(event_state_lag, anomaly_lag, d_lag, idx, last_confirmed_lag)
        if confirmed_lag is not None:
            last_confirmed_lag = confirmed_lag
        
        # ===== 6. Flow decomposition (v6b) =====
        e_grad_lag, _ = flow_decompose(rets_1d, cluster_members['lag'], win_start, win_end)
        _, e_curl_lead = flow_decompose(rets_1d, cluster_members['lead'], win_start, win_end)
        
        e_grad_history_lag.append(e_grad_lag)
        if len(e_grad_history_lag) > W_Q * 2:
            e_grad_history_lag = e_grad_history_lag[-W_Q * 2:]
        e_curl_history_lead.append(e_curl_lead)
        if len(e_curl_history_lead) > W_Q * 2:
            e_curl_history_lead = e_curl_history_lead[-W_Q * 2:]
        
        # z_in: lag cluster e_grad z-score
        z_in = None
        if len(e_grad_history_lag) >= MIN_HIST:
            hist = e_grad_history_lag[-W_Q:] if len(e_grad_history_lag) >= W_Q else e_grad_history_lag
            mu = np.mean(hist)
            std = np.std(hist)
            if std > 1e-10:
                z_in = (e_grad_lag - mu) / std
        
        # z_out: lead cluster e_curl z-score
        z_out = None
        if len(e_curl_history_lead) >= MIN_HIST:
            hist = e_curl_history_lead[-W_Q:] if len(e_curl_history_lead) >= W_Q else e_curl_history_lead
            mu = np.mean(hist)
            std = np.std(hist)
            if std > 1e-10:
                z_out = (e_curl_lead - mu) / std
        
        # ===== 7. Sequential trigger =====
        # Lead confirmed -> pending alert
        if confirmed_lead is not None and idx - last_signal_day >= 10:
            pending_alert = {'day': confirmed_lead, 'expiry': confirmed_lead + SEQ}
        
        # Check pending alert expiry
        if pending_alert is not None and idx > pending_alert['expiry']:
            pending_alert = None
        
        # Lag confirmed within window -> candidate signal
        if confirmed_lag is not None and pending_alert is not None:
            if confirmed_lag >= pending_alert['day'] and idx - last_signal_day >= 10:
                # Entry confirmation: lag 20-day momentum > 0
                lag_mom_20 = np.mean([closes[idx, ci] / closes[idx-20, ci] - 1 
                                      for ci in cluster_members['lag'] 
                                      if closes[idx-20, ci] > 0 and not np.isnan(closes[idx, ci])])
                
                signal_info = {
                    'day': idx, 'date': str(dates[idx]),
                    'c_lead_day': pending_alert['day'],
                    'c_lag_day': confirmed_lag,
                    'lag_mom_20': lag_mom_20,
                    'z_in': z_in,
                    'z_out': z_out,
                    'd_lead': d_lead, 'd_lag': d_lag,
                    'accepted': lag_mom_20 > 0,
                }
                signals.append(signal_info)
                
                if lag_mom_20 > 0:
                    # Switch to lag half
                    position = 'lag'
                    hold_countdown = HOLD
                    last_signal_day = idx
                    pending_alert = None
                    print(f'  SIGNAL ACCEPTED @ {dates[idx]}: lag_mom20={lag_mom_20:.4f}, z_in={z_in}')
                else:
                    print(f'  SIGNAL REJECTED @ {dates[idx]}: lag_mom20={lag_mom_20:.4f}')
                    pending_alert = None
        
        # ===== 8. Position management =====
        if position == 'lag':
            hold_countdown -= 1
            if hold_countdown <= 0:
                position = 'lead'
        
        # ===== 9. Compute daily return =====
        # Trend filter: if HS300 < 60MA, go to cash
        if trend_filter and trend_ok is not None and not trend_ok[idx]:
            port_ret = 0.0  # cash position
            pos_label = 'cash'
        else:
            if position == 'lead':
                members = cluster_members['lead']
            else:
                members = cluster_members['lag']
            
            # Equal weight portfolio return
            port_ret = 0
            count = 0
            for ci in members:
                if idx > 0 and not np.isnan(closes[idx, ci]) and not np.isnan(closes[idx-1, ci]) and closes[idx-1, ci] > 0:
                    port_ret += rets_1d[idx, ci]
                    count += 1
            if count > 0:
                port_ret /= count
            pos_label = position
        
        daily_returns.append(port_ret)
        daily_dates.append(dates[idx])
        position_log.append({
            'day': idx, 'date': str(dates[idx]),
            'position': pos_label, 'hold_remaining': hold_countdown,
            'd_lead': d_lead, 'd_lag': d_lag, 'z_in': z_in, 'z_out': z_out
        })
    
    elapsed = time.time() - t0_time
    print(f'Backtest completed in {elapsed:.0f}s')
    
    return daily_returns, daily_dates, signals, position_log, cluster_log, close_df, hs300, etf_codes


# =================== BENCHMARKS ===================
def compute_benchmarks(close_df, hs300, etf_codes, bt_start_idx):
    """Compute benchmark strategies."""
    dates = close_df['date'].values
    closes = close_df[etf_codes].values
    N = len(etf_codes)
    
    rets_1d = np.zeros_like(closes)
    rets_1d[1:] = closes[1:] / closes[:-1] - 1
    
    # 1. Pure momentum (always hold lead half)
    mom_returns = []
    last_rebal = -REBAL
    cluster_members = None
    
    for idx in range(bt_start_idx, len(dates)):
        days_since_rebal = idx - last_rebal
        if days_since_rebal >= REBAL:
            universe = []
            for ci in range(N):
                first_valid = np.where(~np.isnan(closes[:, ci]))[0]
                if len(first_valid) > 0 and first_valid[0] <= idx - 60:
                    universe.append(ci)
            if len(universe) < 4:
                mom_returns.append(0)
                continue
            momenta = []
            for ci in universe:
                p_now = closes[idx, ci]
                p_past = closes[idx - MOM_WIN, ci]
                if p_past > 0 and not np.isnan(p_now) and not np.isnan(p_past):
                    momenta.append((p_now / p_past - 1, ci))
                else:
                    momenta.append((-999, ci))
            momenta.sort(key=lambda x: -x[0])
            half = len(momenta) // 2
            cluster_members = [m[1] for m in momenta[:half]]
            last_rebal = idx
        
        if cluster_members is None:
            mom_returns.append(0)
            continue
        
        port_ret = 0
        count = 0
        for ci in cluster_members:
            if idx > 0 and not np.isnan(closes[idx, ci]) and not np.isnan(closes[idx-1, ci]) and closes[idx-1, ci] > 0:
                port_ret += rets_1d[idx, ci]
                count += 1
        if count > 0:
            port_ret /= count
        mom_returns.append(port_ret)
    
    # 2. Always hold lag half
    lag_returns = []
    last_rebal = -REBAL
    cluster_members = None
    
    for idx in range(bt_start_idx, len(dates)):
        days_since_rebal = idx - last_rebal
        if days_since_rebal >= REBAL:
            universe = []
            for ci in range(N):
                first_valid = np.where(~np.isnan(closes[:, ci]))[0]
                if len(first_valid) > 0 and first_valid[0] <= idx - 60:
                    universe.append(ci)
            if len(universe) < 4:
                lag_returns.append(0)
                continue
            momenta = []
            for ci in universe:
                p_now = closes[idx, ci]
                p_past = closes[idx - MOM_WIN, ci]
                if p_past > 0 and not np.isnan(p_now) and not np.isnan(p_past):
                    momenta.append((p_now / p_past - 1, ci))
                else:
                    momenta.append((-999, ci))
            momenta.sort(key=lambda x: -x[0])
            half = len(momenta) // 2
            cluster_members = [m[1] for m in momenta[half:]]
            last_rebal = idx
        
        if cluster_members is None:
            lag_returns.append(0)
            continue
        
        port_ret = 0
        count = 0
        for ci in cluster_members:
            if idx > 0 and not np.isnan(closes[idx, ci]) and not np.isnan(closes[idx-1, ci]) and closes[idx-1, ci] > 0:
                port_ret += rets_1d[idx, ci]
                count += 1
        if count > 0:
            port_ret /= count
        lag_returns.append(port_ret)
    
    # 3. HS300
    hs300_dates = hs300['date'].values
    hs300_close = hs300['close'].values
    hs300_ret = np.zeros(len(hs300_close))
    hs300_ret[1:] = hs300_close[1:] / hs300_close[:-1] - 1
    
    # Align HS300 with backtest dates
    bt_dates = dates[bt_start_idx:]
    hs300_aligned = []
    hs300_dict = {pd.Timestamp(d): r for d, r in zip(hs300_dates, hs300_ret)}
    for d in bt_dates:
        hs300_aligned.append(hs300_dict.get(pd.Timestamp(d), 0))
    
    return mom_returns, lag_returns, hs300_aligned


# =================== METRICS ===================
def compute_metrics(returns, dates, name=''):
    """Compute performance metrics."""
    returns = np.array(returns)
    dates = pd.to_datetime(dates)
    
    # Cumulative returns
    cum = np.cumprod(1 + returns)
    total_ret = cum[-1] - 1
    
    # Annual returns
    years = pd.Series(dates).dt.year
    annual = {}
    for yr in sorted(years.unique()):
        mask = years.values == yr
        yr_ret = np.prod(1 + returns[mask]) - 1
        # Max drawdown within year
        cum_yr = np.cumprod(1 + returns[mask])
        running_max = np.maximum.accumulate(cum_yr)
        dd = (cum_yr - running_max) / running_max
        mdd = np.min(dd) if len(dd) > 0 else 0
        annual[int(yr)] = {'return': float(yr_ret), 'mdd': float(mdd)}
    
    # Overall max drawdown
    running_max = np.maximum.accumulate(cum)
    drawdown = (cum - running_max) / running_max
    max_dd = np.min(drawdown)
    
    # CAGR
    n_years = (dates[-1] - dates[0]).days / 365.25
    cagr = cum[-1] ** (1 / n_years) - 1 if n_years > 0 else 0
    
    # Sharpe (annualized, rf=0)
    if np.std(returns) > 0:
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
    else:
        sharpe = 0
    
    return {
        'name': name,
        'total_return': total_ret,
        'cagr': cagr,
        'max_drawdown': max_dd,
        'sharpe': sharpe,
        'annual': annual,
        'cumulative': cum.tolist(),
    }


# =================== MAIN ===================
if __name__ == '__main__':
    import pickle
    
    # === Run original HLM ===
    results = run_backtest(trend_filter=False)
    daily_returns, daily_dates, signals, position_log, cluster_log, close_df, hs300, etf_codes = results
    
    # Find backtest start index for benchmarks
    dates = close_df['date'].values
    bt_start_idx = None
    warmup = MOM_WIN + W_WIN + 10
    for i, d in enumerate(dates):
        if pd.Timestamp(d) >= pd.Timestamp('2019-01-01') and i >= warmup:
            bt_start_idx = i
            break
    if bt_start_idx is None:
        bt_start_idx = warmup
    
    # Compute benchmarks
    print('\nComputing benchmarks...')
    mom_returns, lag_returns, hs300_returns = compute_benchmarks(close_df, hs300, etf_codes, bt_start_idx)
    
    bt_dates = dates[bt_start_idx:]
    
    hlm_metrics = compute_metrics(daily_returns, bt_dates, 'HLM V5')
    mom_metrics = compute_metrics(mom_returns, bt_dates, 'Pure Momentum')
    lag_metrics = compute_metrics(lag_returns, bt_dates, 'Always Lag')
    hs300_metrics = compute_metrics(hs300_returns, bt_dates, 'HS300')
    
    # Signal statistics
    accepted_signals = [s for s in signals if s['accepted']]
    rejected_signals = [s for s in signals if not s['accepted']]
    
    for s in accepted_signals:
        sig_idx = s['day']
        local_idx = sig_idx - bt_start_idx
        if local_idx + 20 < len(daily_returns):
            s['return_20d'] = float(np.prod(1 + np.array(daily_returns[local_idx:local_idx+20])) - 1)
        else:
            s['return_20d'] = float(np.prod(1 + np.array(daily_returns[local_idx:])) - 1)
    
    # === Apply trend filter post-hoc (no need to re-run Hodge computation) ===
    print('\nApplying trend filter (HS300 vs 60MA)...')
    hs300_s = hs300.set_index('date')['close']
    hs300_aligned = hs300_s.reindex(pd.to_datetime(dates)).ffill().bfill().values
    ma60 = pd.Series(hs300_aligned).rolling(60, min_periods=1).mean().values
    trend_ok = hs300_aligned >= ma60
    
    tf_returns = []
    cash_days = 0
    for i, idx in enumerate(range(bt_start_idx, len(dates))):
        if trend_ok[idx]:
            tf_returns.append(daily_returns[i])
        else:
            tf_returns.append(0.0)
            cash_days += 1
    
    tf_metrics = compute_metrics(tf_returns, bt_dates, 'HLM+TrendFilter')
    
    # Also trend-filtered pure momentum
    tf_mom_returns = []
    for i, idx in enumerate(range(bt_start_idx, len(dates))):
        if trend_ok[idx]:
            tf_mom_returns.append(mom_returns[i])
        else:
            tf_mom_returns.append(0.0)
    tf_mom_metrics = compute_metrics(tf_mom_returns, bt_dates, 'Mom+TrendFilter')
    
    print(f'Trend filter: {cash_days} cash days ({cash_days/len(tf_returns)*100:.0f}%)')
    
    # === Print all results ===
    print('\n' + '='*60)
    print('BACKTEST RESULTS (2019-01 ~ 2026-08)')
    print('='*60)
    
    for m in [hlm_metrics, tf_metrics, mom_metrics, tf_mom_metrics, lag_metrics, hs300_metrics]:
        print(f'\n{m["name"]}:')
        print(f'  Total Return: {m["total_return"]*100:.1f}%')
        print(f'  CAGR: {m["cagr"]*100:.1f}%')
        print(f'  Max Drawdown: {m["max_drawdown"]*100:.1f}%')
        print(f'  Sharpe: {m["sharpe"]:.2f}')
        print(f'  Annual:')
        for yr, v in sorted(m['annual'].items()):
            print(f'    {yr}: {v["return"]*100:+.1f}% (MDD: {v["mdd"]*100:.1f}%)')
    
    print(f'\n--- Signal Statistics ---')
    print(f'Total sequential triggers: {len(signals)}')
    print(f'Accepted (passed entry confirmation): {len(accepted_signals)}')
    print(f'Rejected: {len(rejected_signals)}')
    if accepted_signals:
        wins = [s for s in accepted_signals if s.get('return_20d', 0) > 0]
        print(f'Win rate (20d): {len(wins)}/{len(accepted_signals)} = {len(wins)/len(accepted_signals)*100:.0f}%')
        avg_ret = np.mean([s.get('return_20d', 0) for s in accepted_signals])
        print(f'Avg 20d return: {avg_ret*100:.2f}%')
    
    # === Save everything for report generation ===
    all_data = {
        'hlm_metrics': {k: v for k, v in hlm_metrics.items() if k != 'cumulative'},
        'tf_metrics': {k: v for k, v in tf_metrics.items() if k != 'cumulative'},
        'mom_metrics': {k: v for k, v in mom_metrics.items() if k != 'cumulative'},
        'tf_mom_metrics': {k: v for k, v in tf_mom_metrics.items() if k != 'cumulative'},
        'lag_metrics': {k: v for k, v in lag_metrics.items() if k != 'cumulative'},
        'hs300_metrics': {k: v for k, v in hs300_metrics.items() if k != 'cumulative'},
        'signals': signals,
        'accepted_signals': accepted_signals,
        'n_signals': len(signals),
        'n_accepted': len(accepted_signals),
        'cash_days': cash_days,
        'total_days': len(tf_returns),
        # Save daily returns for charting
        'hlm_daily': [float(r) for r in daily_returns],
        'tf_daily': [float(r) for r in tf_returns],
        'mom_daily': [float(r) for r in mom_returns],
        'tf_mom_daily': [float(r) for r in tf_mom_returns],
        'lag_daily': [float(r) for r in lag_returns],
        'hs300_daily': [float(r) for r in hs300_returns],
        'bt_dates': [str(pd.Timestamp(d).strftime('%Y-%m-%d')) for d in bt_dates],
        'trend_ok': [bool(t) for t in trend_ok[bt_start_idx:]],
    }
    
    pkl_path = os.path.join(RESULTS_DIR, 'backtest_data.pkl')
    with open(pkl_path, 'wb') as f:
        pickle.dump(all_data, f)
    
    print(f'\nData saved to {pkl_path}')
    print(f'Run generate_report_v2.py to generate HTML report.')
