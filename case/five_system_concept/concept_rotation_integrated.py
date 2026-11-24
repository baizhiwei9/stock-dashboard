#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整合脚本: 合并 Hodge Laplacian + RMT 两套概念轮动系统的信号
输出统一的20概念机会/风险综合评价表
"""
import pandas as pd
import numpy as np
from numpy.linalg import eigh
import sys

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

# ===================== 参数(与两个子系统一致) =====================
CORR_WINDOW = 30
CORR_THRESHOLD = 0.3
MA_PERIOD = 40
MOM_PERIOD = 20
TOP_K_EIGEN = 5
SPECTRAL_ANGLE_THRESHOLD = 0.3
DELTA_S_THRESHOLD = 0.5
RMT_WINDOW = 60

def load_data():
    returns = pd.read_pickle(f'{OUT_DIR}/concept_returns.pkl')
    close = pd.read_pickle(f'{OUT_DIR}/concept_close.pkl')
    return returns, close

# ===================== Hodge 组件 =====================
def build_corr_graph(window):
    corr = window.corr().fillna(0).values
    adj = (np.abs(corr) > CORR_THRESHOLD).astype(float)
    np.fill_diagonal(adj, 0)
    return corr, corr * adj

def compute_laplacian(adj_w):
    D = np.diag(adj_w.sum(axis=1))
    return D - adj_w

def spectral_decomp(L):
    ev, vec = eigh(L)
    idx = np.argsort(ev)
    return ev[idx], vec[:, idx]

def spectral_angle(v1, v2):
    k = min(TOP_K_EIGEN, v1.shape[1]-1, v2.shape[1]-1)
    if k < 1: return 0.0
    V1 = v1[:, 1:k+1]; V2 = v2[:, 1:k+1]
    P1 = V1 @ V1.T; P2 = V2 @ V2.T
    overlap = np.trace(P1 @ P2)
    cos_a = np.clip(overlap / k, -1, 1)
    return float(np.arccos(cos_a))

def hodge_signals(returns, close, concept_names):
    n = len(concept_names); nd = len(returns)
    hma = close.rolling(MA_PERIOD, min_periods=1).mean()
    hmom = close.pct_change(MOM_PERIOD) * 100
    hlc = close.iloc[-1]; hma_last = hma.iloc[-1]; hmom_last = hmom.iloc[-1]

    # 滚动谱分析
    angles = []; fiedler_hist = []
    for t in range(CORR_WINDOW, nd + 1):
        w = returns.iloc[t - CORR_WINDOW:t]
        _, aw = build_corr_graph(w)
        L = compute_laplacian(aw)
        ev, evec = spectral_decomp(L)
        fv = ev[1] if len(ev) > 1 else 0
        fiedler_hist.append({'date': returns.index[t-1], 'fv': float(fv),
                              'fvec': evec[:, 1] if len(ev) > 1 else np.zeros(n)})
        if t > CORR_WINDOW:
            pw = returns.iloc[t - CORR_WINDOW - 1: t - 1]
            _, paw = build_corr_graph(pw)
            _, pevec = spectral_decomp(compute_laplacian(paw))
            angles.append({'date': returns.index[t-1], 'angle': spectral_angle(evec, pevec)})

    latest_angle = angles[-1]['angle'] if angles else 0
    avg_angle = np.mean([a['angle'] for a in angles[-5:]]) if len(angles) >= 5 else latest_angle
    struct_stab = 'stable' if avg_angle < 0.15 else ('shifting' if avg_angle < SPECTRAL_ANGLE_THRESHOLD else 'unstable')

    cf = fiedler_hist[-1] if fiedler_hist else None
    cf_vec = cf['fvec'] if cf else np.zeros(n)

    signals = {}
    for i, name in enumerate(concept_names):
        c_close = float(hlc[name]) if not np.isnan(hlc[name]) else 0
        c_ma = float(hma_last[name]) if not np.isnan(hma_last[name]) else 0
        c_mom = float(hmom_last[name]) if not np.isnan(hmom_last[name]) else 0
        above = c_close > c_ma if c_ma > 0 else False
        gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0
        fcomp = float(cf_vec[i])
        comm = 'leading' if fcomp >= 0 else 'lagging'

        ts = 2 if above else -2
        ms = 2 if c_mom > 5 else (1 if c_mom > 0 else (-2 if c_mom < -5 else -1))
        fs = 1 if comm == 'leading' else -1
        ss = 1 if struct_stab == 'stable' else (0 if struct_stab == 'shifting' else -1)
        total = ts + ms + fs + ss
        ev_label = '机会' if total >= 3 else ('偏多' if total >= 1 else ('中性' if total >= -1 else ('偏空' if total >= -3 else '风险')))

        signals[name] = {
            'hodge_score': total, 'hodge_eval': ev_label,
            'above_ma40': above, 'ma40_gap': round(gap, 2),
            'mom20': round(c_mom, 2), 'fiedler': round(fcomp, 4),
            'community': comm, 'struct_stab': struct_stab,
            'latest_angle': round(latest_angle, 4),
        }
    return signals

# ===================== RMT 组件 =====================
def mp_bounds(N, T):
    Q = T / N if T > N else 1.001
    lm = float((1 - 1/np.sqrt(Q))**2)
    lp = float((1 + 1/np.sqrt(Q))**2)
    return lm, lp

def rmt_denoise(corr, N, T):
    C = (corr + corr.T) / 2
    ev, evec = eigh(C)
    idx = np.argsort(ev)[::-1]
    ev = ev[idx]; evec = evec[:, idx]
    lm, lp = mp_bounds(N, T)
    noise = (ev >= lm) & (ev <= lp)
    dev = ev.copy(); dev[noise] = 0
    Cd = evec @ np.diag(dev) @ evec.T
    d = np.sqrt(np.diag(Cd)); d[d == 0] = 1
    Cd = Cd / np.outer(d, d); np.fill_diagonal(Cd, 1.0)
    return Cd, ev, int(noise.sum()), int((~noise).sum()), lm, lp

def compute_delta_s(window, N, T):
    corr = window.corr().fillna(0).values
    C = (corr + corr.T) / 2
    ev, _ = eigh(C); ev = np.sort(ev)[::-1]
    lm, lp = mp_bounds(N, T)
    te = np.sum(ev**2)
    if te == 0: return 0.0
    se = np.sum(ev[(ev > lp) | (ev < lm)]**2)
    return float(se / te)

def entropy(ev):
    e = ev[ev > 0]
    if len(e) == 0: return 0.0
    e = e / e.sum()
    ent = -np.sum(e * np.log(e + 1e-15))
    mx = np.log(len(e))
    return float(ent / mx) if mx > 0 else 0.0

def rmt_signals(returns, close, concept_names):
    n = len(concept_names); nd = len(returns)
    T = min(RMT_WINDOW, nd)
    rw = returns.iloc[-T:]
    corr_raw = rw.corr().fillna(0).values
    corr_raw = (corr_raw + corr_raw.T) / 2
    Cd, raw_ev, n_noise, n_sig, rmt_lm, rmt_lp = rmt_denoise(corr_raw, n, T)
    Q = float(T / n)

    max_ev = float(raw_ev[0]); max_ratio = float(max_ev / n)
    gap1 = float(raw_ev[0] / raw_ev[1]) if raw_ev[1] > 0 else float('inf')
    ent = entropy(raw_ev)
    ds = compute_delta_s(rw, n, T)
    extreme = ds > DELTA_S_THRESHOLD

    avg_corr_raw = float((corr_raw.sum() - np.trace(corr_raw)) / (n * (n-1)))
    avg_corr_dn = float((Cd.sum() - np.trace(Cd)) / (n * (n-1)))

    concept_avg = {}
    for i, name in enumerate(concept_names):
        row = Cd[i, :].copy(); row[i] = 0
        concept_avg[name] = float(row.mean())

    rma = close.rolling(MA_PERIOD, min_periods=1).mean()
    rmom = close.pct_change(MOM_PERIOD) * 100
    rlc = close.iloc[-1]; rma_last = rma.iloc[-1]; rmom_last = rmom.iloc[-1]
    mom_ranked = rmom_last.sort_values(ascending=False)
    top5 = mom_ranked.head(5).index
    bot5 = mom_ranked.tail(5).index

    # delta_s 历史
    ds_hist = []
    for t in range(max(T, 30), nd + 1):
        w = returns.iloc[t-T:t] if t >= T else returns.iloc[:t]
        ds_hist.append(compute_delta_s(w, n, len(w)))
    ds_latest = ds_hist[-1] if ds_hist else ds

    if extreme: mode = 'extreme'
    elif ds > DELTA_S_THRESHOLD * 0.7: mode = 'elevated'
    elif ds > DELTA_S_THRESHOLD * 0.4: mode = 'normal'
    else: mode = 'dispersed'

    signals = {}
    for name in concept_names:
        c_close = float(rlc[name]) if not np.isnan(rlc[name]) else 0
        c_ma = float(rma_last[name]) if not np.isnan(rma_last[name]) else 0
        c_mom = float(rmom_last[name]) if not np.isnan(rmom_last[name]) else 0
        above = c_close > c_ma if c_ma > 0 else False
        gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0
        ac = concept_avg.get(name, 0)
        is_top = name in top5; is_bot = name in bot5
        mr = int(mom_ranked.index.get_loc(name)) + 1

        ts = 2 if above else -2
        ms = 2 if c_mom > 5 else (1 if c_mom > 0 else (-2 if c_mom < -5 else -1))
        tps = 2 if is_top else (1 if mr <= 8 else 0)
        bs = -2 if is_bot else 0
        dss = -1 if mode == 'extreme' else (1 if mode == 'normal' else 0)
        cs = 1 if ac < 0.1 else (-1 if ac > 0.3 else 0)
        total = ts + ms + tps + bs + dss + cs
        ev_label = '机会' if total >= 4 else ('偏多' if total >= 2 else ('中性' if total >= 0 else ('偏空' if total >= -2 else '风险')))
        if extreme and ev_label in ['机会', '偏多']: ev_label = '中性'

        signals[name] = {
            'rmt_score': total, 'rmt_eval': ev_label,
            'is_top5': is_top, 'is_bottom5': is_bot, 'mom_rank': mr,
            'avg_corr_dn': round(ac, 4),
            'rmt_mode': mode, 'delta_s': round(ds, 4), 'extreme': extreme,
        }
    return signals, {
        'Q': Q, 'lambda_minus': rmt_lm, 'lambda_plus': rmt_lp,
        'max_ev': max_ev, 'max_ratio': max_ratio, 'gap1': gap1,
        'entropy': ent, 'n_noise': n_noise, 'n_sig': n_sig,
        'avg_corr_raw': avg_corr_raw, 'avg_corr_dn': avg_corr_dn,
        'delta_s': ds, 'extreme': extreme, 'mode': mode,
    }

# ===================== 综合合并 =====================
def merge_signals(hodge_s, rmt_s, concept_names):
    merged = []
    for name in concept_names:
        h = hodge_s[name]; r = rmt_s[name]
        hs = h['hodge_score']; rs = r['rmt_score']
        combined_score = round((hs + rs) / 2, 1)
        eval_order = {'机会': 4, '偏多': 3, '中性': 2, '偏空': 1, '风险': 0}
        h_val = eval_order.get(h['hodge_eval'], 2)
        r_val = eval_order.get(r['rmt_eval'], 2)
        combined_val = min(h_val, r_val)
        rev_order = {v: k for k, v in eval_order.items()}
        combined_eval = rev_order[combined_val]

        if h_val >= 3 and r_val >= 3:
            combined_eval = '机会'
            combined_score = max(hs, rs)
        elif h_val >= 3 or r_val >= 3:
            combined_eval = '偏多'
        elif h_val <= 1 or r_val <= 1:
            combined_eval = '风险' if (h_val <= 0 or r_val <= 0) else '偏空'

        merged.append({
            'name': name,
            'combined_score': combined_score,
            'combined_eval': combined_eval,
            'hodge_score': hs, 'hodge_eval': h['hodge_eval'],
            'rmt_score': rs, 'rmt_eval': r['rmt_eval'],
            'above_ma40': h['above_ma40'],
            'ma40_gap': h['ma40_gap'],
            'mom20': h['mom20'],
            'mom_rank': r['mom_rank'],
            'is_top5': r['is_top5'],
            'fiedler': h['fiedler'],
            'community': h['community'],
            'struct_stab': h['struct_stab'],
            'avg_corr_dn': r['avg_corr_dn'],
            'rmt_mode': r['rmt_mode'],
            'delta_s': r['delta_s'],
        })
    merged.sort(key=lambda x: x['combined_score'], reverse=True)
    return merged

def main():
    print("=" * 120, file=sys.stderr)
    print("概念轮动双系统整合: Hodge Laplacian + RMT", file=sys.stderr)
    print("=" * 120, file=sys.stderr)

    returns, close = load_data()
    concept_names = list(returns.columns)
    n = len(concept_names); nd = len(returns)

    print(f"数据: {nd}交易日 x {n}概念, {returns.index[0].strftime('%Y-%m-%d')}~{returns.index[-1].strftime('%Y-%m-%d')}", file=sys.stderr)

    print("[1/3] Hodge Laplacian分析...", file=sys.stderr)
    h_s = hodge_signals(returns, close, concept_names)

    print("[2/3] RMT随机矩阵分析...", file=sys.stderr)
    r_s, r_info = rmt_signals(returns, close, concept_names)

    print("[3/3] 合并双系统信号...", file=sys.stderr)
    merged = merge_signals(h_s, r_s, concept_names)

    # ===================== 输出 =====================
    print("\n" + "=" * 130)
    print(f"概念轮动双系统综合评价 (数据截止: {returns.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 130)

    # 系统级状态
    print(f"\n--- 系统级状态 ---")
    print(f"  [Hodge]  结构稳定性: {h_s[concept_names[0]]['struct_stab']}  谱角距离: {h_s[concept_names[0]]['latest_angle']:.4f}")
    print(f"  [RMT]    MP: Q={r_info['Q']:.2f}, [{r_info['lambda_minus']:.4f}, {r_info['lambda_plus']:.4f}]")
    print(f"           最大特征值={r_info['max_ev']:.4f}({r_info['max_ratio']*100:.1f}%), Gap1={r_info['gap1']:.4f}")
    print(f"           噪声={r_info['n_noise']}个, 显著={r_info['n_sig']}个, 熵={r_info['entropy']:.4f}")
    print(f"           delta_s={r_info['delta_s']:.4f}, 模式={r_info['mode']}{' [极端!]' if r_info['extreme'] else ''}")
    print(f"           去噪后平均相关: {r_info['avg_corr_raw']:.4f} -> {r_info['avg_corr_dn']:.4f}")

    # 综合评价表
    print(f"\n{'='*130}")
    print("20概念双系统综合评价表 (按综合评分降序)")
    print("=" * 130)
    print(f"{'排名':<4} {'概念':<14} {'综合分':<6} {'综合评价':<6} {'Hodge分':<7} {'Hodge评':<7} {'RMT分':<6} {'RMT评':<7} {'MA40':<4} {'偏离%':<7} {'20日动量':<8} {'排名':<4} {'Top5':<4} {'Fiedler':<9} {'社区':<6} {'去噪相关':<7}")
    print("-" * 130)

    markers = {'机会': '★', '偏多': '▲', '中性': '─', '偏空': '▼', '风险': '✕'}

    for i, s in enumerate(merged):
        m = markers.get(s['combined_eval'], '')
        ma_s = 'Y' if s['above_ma40'] else 'N'
        top_s = 'Y' if s['is_top5'] else '-'
        hm = markers.get(s['hodge_eval'], '')
        rm = markers.get(s['rmt_eval'], '')
        print(f"{i+1:<4} {s['name']:<14} {s['combined_score']:<+6.1f} {s['combined_eval']:<6}{m} "
              f"{s['hodge_score']:<+7} {s['hodge_eval']:<7}{hm} "
              f"{s['rmt_score']:<+6} {s['rmt_eval']:<7}{rm} "
              f"{ma_s:<4} {s['ma40_gap']:<7.2f} {s['mom20']:<8.2f} "
              f"{s['mom_rank']:<4} {top_s:<4} {s['fiedler']:<9.4f} {s['community']:<6} {s['avg_corr_dn']:<7.4f}")

    # 强弱分布
    print(f"\n{'='*130}")
    print("概念板块综合强弱分布:")
    for label in ['机会', '偏多', '中性', '偏空', '风险']:
        names = [s['name'] for s in merged if s['combined_eval'] == label]
        if names:
            print(f"  {label} ({len(names)}个): {', '.join(names)}")

    # 双系统对比
    print(f"\n{'='*130}")
    print("双系统信号对比 (Hodge vs RMT):")
    print("-" * 130)
    print(f"{'概念':<14} {'Hodge分':<8} {'Hodge评':<8} {'RMT分':<8} {'RMT评':<8} {'差异':<6} {'一致?':<6} {'综合评价'}")
    print("-" * 130)
    for s in merged:
        diff = s['hodge_score'] - s['rmt_score']
        agree = 'Y' if s['hodge_eval'] == s['rmt_eval'] else 'N'
        print(f"{s['name']:<14} {s['hodge_score']:<+8} {s['hodge_eval']:<8} "
              f"{s['rmt_score']:<+8} {s['rmt_eval']:<8} {diff:<+6} {agree:<6} {s['combined_eval']}")

    agree_count = sum(1 for s in merged if s['hodge_eval'] == s['rmt_eval'])
    print(f"\n  双系统评价一致率: {agree_count}/{len(merged)} ({agree_count/len(merged)*100:.0f}%)")

    # 液冷专题
    yeleng = [s for s in merged if s['name'] == '液冷概念']
    if yeleng:
        s = yeleng[0]
        print(f"\n{'='*130}")
        print(f"液冷概念专题分析:")
        print(f"-" * 130)
        print(f"  综合评价: {s['combined_eval']} (评分{s['combined_score']:+.1f})")
        print(f"  Hodge:    {s['hodge_eval']} (评分{s['hodge_score']:+d})  Fiedler={s['fiedler']:+.4f}  社区={s['community']}")
        print(f"  RMT:      {s['rmt_eval']} (评分{s['rmt_score']:+d})  动量排名={s['mom_rank']}  Top5={'Y' if s['is_top5'] else 'N'}  去噪相关={s['avg_corr_dn']:.4f}")
        print(f"  MA40:     {'线上' if s['above_ma40'] else '线下'} (偏离{s['ma40_gap']:.2f}%)")
        print(f"  20日动量: {s['mom20']:+.2f}%")
        print(f"  RMT模式:  {s['rmt_mode']}  delta_s={s['delta_s']:.4f}")
        if s['combined_eval'] in ['机会', '偏多']:
            print(f"  结论: 液冷概念被双系统识别为机会信号")
        elif s['hodge_eval'] in ['机会', '偏多']:
            print(f"  结论: Hodge系统识别液冷为机会, RMT因极端状态降级")

    # 机会与风险总结
    print(f"\n{'='*130}")
    print("综合机会与风险总结:")
    print("-" * 130)

    opps = [s for s in merged if s['combined_eval'] in ['机会', '偏多']]
    risks = [s for s in merged if s['combined_eval'] in ['风险', '偏空']]
    neutrals = [s for s in merged if s['combined_eval'] == '中性']

    print(f"\n  系统状态: Hodge结构={h_s[concept_names[0]]['struct_stab']}, RMT模式={r_info['mode']}{'(极端)' if r_info['extreme'] else ''}")
    print(f"  双系统一致率: {agree_count}/{len(merged)}")

    print(f"\n  综合机会区({len(opps)}个):")
    for s in opps:
        reasons = []
        if s['above_ma40']: reasons.append(f"MA40线上({s['ma40_gap']:.1f}%)")
        if s['mom20'] > 0: reasons.append(f"动量+{s['mom20']:.1f}%")
        if s['is_top5']: reasons.append(f"RMT Top5(第{s['mom_rank']}名)")
        if s['hodge_eval'] in ['机会', '偏多']: reasons.append(f"Hodge={s['hodge_eval']}")
        print(f"    {s['name']:<14} 综合{s['combined_score']:+.1f}  {' | '.join(reasons)}")

    print(f"\n  综合风险区({len(risks)}个):")
    for s in risks:
        reasons = []
        if not s['above_ma40']: reasons.append(f"MA40线下({s['ma40_gap']:.1f}%)")
        if s['mom20'] < 0: reasons.append(f"动量{s['mom20']:.1f}%")
        if s['rmt_eval'] in ['风险', '偏空']: reasons.append(f"RMT={s['rmt_eval']}")
        if s['hodge_eval'] in ['风险', '偏空']: reasons.append(f"Hodge={s['hodge_eval']}")
        print(f"    {s['name']:<14} 综合{s['combined_score']:+.1f}  {' | '.join(reasons)}")

    print(f"\n  中性区({len(neutrals)}个): {', '.join(s['name'] for s in neutrals)}")

    print(f"\n{'='*130}")
    print("综合评分规则: (Hodge分 + RMT分) / 2")
    print("综合评价: 两套系统一致时取共识; 不一致时取更保守的一方(风险优先)")
    print("         两套都>=偏多 -> 机会; 任一>=偏多 -> 偏多; 任一<=偏空 -> 偏空/风险")
    print("=" * 130)

if __name__ == '__main__':
    main()
