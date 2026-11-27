# -*- coding: utf-8 -*-
"""周期（H）横评：H=1/2/3/4/5 哪个胜率最高、样本外最稳

用完全相同的管线（同特征、同 walk-forward、同 5 种子、同门槛扫描），
只改目标定义 close.shift(-H)，看哪个 H 的「IS 挑档 → OOS 验」表现最好。

输出 .cache/wide_daily/horizon_cmp.json + 屏幕表
"""
import os, sys, json, time, warnings, itertools
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from scipy.stats import binomtest
from multiprocessing import Pool

import core as C

HORIZONS = [1, 2, 3, 4, 5]
IS_LO, IS_HI = '2019-01-01', '2024-06-30'
OOS_LO, OOS_HI = '2024-07-01', '2026-12-31'
ALPHAS = [0.50, 0.70, 0.80, 0.88, 0.93, 0.97]
DIVS = [None, 0.60, 0.70, 0.80]
OUT = os.path.join(C.STATE_DIR, 'horizon_cmp.json')


def train_one(args):
    """单个 (指数, H) 训练 + 出预测序列"""
    code, name, fn, H = args
    d = C.load_index(fn)
    if d is None or len(d) < 500:
        return None
    sp = C.load_macro()

    # 用指定 H 重建目标
    X = C.build_features(d, sp, code=code, h=H)
    c = X['c']
    y = ((c.shift(-H) / c - 1) * 100).values
    cols = C.feature_cols(X)
    Xm = np.nan_to_num(
        X[cols].astype(float).replace([np.inf, -np.inf], np.nan).values,
        nan=0.0, posinf=0.0, neginf=0.0)
    idx = X.index
    n = len(idx)

    anchor = None
    for i in range(n):
        if pd.Timestamp('2019-01-01') <= idx[i]:
            anchor = i
            break
    if anchor is None:
        return None

    preds = {s: np.full(n, np.nan) for s in C.SEEDS}
    models = None
    t0 = time.time()
    for i in range(anchor, n):
        need = models is None or (i - anchor) % C.RETRAIN == 0
        tr = np.arange(max(0, i - C.WIN), i)
        tr = tr[np.isfinite(y[tr])]
        if len(tr) < C.MIN_TRAIN:
            continue
        if need:
            models = []
            for s in C.SEEDS:
                try:
                    models.append(C.fit_one(Xm[tr], y[tr], s))
                except Exception:
                    models.append(None)
        for si in range(len(C.SEEDS)):
            if models[si] is None:
                continue
            try:
                preds[C.SEEDS[si]][i] = float(models[si].predict(Xm[i:i + 1])[0])
            except Exception:
                pass

    df = pd.DataFrame(preds, index=idx)
    df['bagged'] = df.mean(axis=1)
    df['div'] = df.std(axis=1)
    df['amp'] = df['bagged'].abs()
    df['actual'] = y
    for a in ALPHAS:
        k = int(a * 100)
        df['amp_q%d' % k] = df['amp'].shift(1).rolling(300, min_periods=150).quantile(a)
    for dv in DIVS:
        if dv is None:
            continue
        k = int(dv * 100)
        df['div_q%d' % k] = df['div'].shift(1).rolling(300, min_periods=150).quantile(dv)
    df = df.dropna(subset=['bagged'])
    # 落盘供后续分析
    df.to_csv(os.path.join(C.STATE_DIR, 'h%d_%s.csv' % (H, code)))
    print('[H=%d] %s 完成 %.0fs' % (H, name, time.time() - t0), flush=True)
    return (code, name, H, len(df))


def gate_stats(df, lo, hi, minn=15):
    ev = df.loc[lo:hi].dropna(subset=['actual'])
    out = {}
    for a in ALPHAS:
        for dv in DIVS:
            k = int(a * 100)
            if 'amp_q%d' % k not in ev.columns:
                continue
            sel = ev[ev['amp'] >= ev['amp_q%d' % k]]
            g = 'amp>%d%%' % k
            if dv is not None:
                kd = int(dv * 100)
                if 'div_q%d' % kd not in ev.columns:
                    continue
                sel = sel[sel['div'] <= sel['div_q%d' % kd]]
                g += ' x div<%d%%' % kd
            sel = sel.dropna(subset=['actual'])
            if len(sel) < minn:
                continue
            ok = ((sel['actual'] > 0) == (sel['bagged'] > 0))
            ret = (np.sign(sel['bagged']) * sel['actual'])
            out[g] = dict(n=len(sel), hit=float(ok.mean()),
                          p=float(binomtest(int(ok.sum()), len(sel), 0.5,
                                            alternative='greater').pvalue),
                          ret=float(ret.mean()))
    return out


def main():
    t_all = time.time()
    jobs = [(code, name, fn, H) for H in HORIZONS for code, name, fn in C.INDICES]
    print('共 %d 个训练任务（%d 指数 × %d 周期），4 进程并行' % (len(jobs), len(C.INDICES), len(HORIZONS)))
    with Pool(4) as pool:
        for r in pool.imap_unordered(train_one, jobs):
            pass
    print('训练全部完成 %.0fs\n' % (time.time() - t_all))

    result = {}
    for H in HORIZONS:
        result[H] = {}
        for code, name, fn in C.INDICES:
            p = os.path.join(C.STATE_DIR, 'h%d_%s.csv' % (H, code))
            if not os.path.exists(p):
                continue
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            gis = gate_stats(df, IS_LO, IS_HI, 25)
            goos = gate_stats(df, OOS_LO, OOS_HI, 15)
            # IS 选：p 最小且胜率>55% 且净收益>0
            cand = {k: v for k, v in gis.items()
                    if v['p'] < 0.10 and v['hit'] > 0.55 and (v['ret'] - 0.20) > 0}
            best = min(cand.items(), key=lambda kv: kv[1]['p']) if cand else None
            result[H][code] = dict(name=name, is_gates=len(gis), is_best=best[0] if best else None,
                                   is_stat=best[1] if best else None, oos=goos.get(best[0]) if best else None)

    json.dump(result, open(OUT, 'w'), ensure_ascii=False, indent=1, default=str)

    # ---- 汇总表 ----
    print('=' * 104)
    print('周期横评：IS(2019-01~2024-06) 挑档 → OOS(2024-07~) 验')
    print('=' * 104)
    print('%-4s %-9s %-20s %6s %7s %8s | %6s %7s %8s %8s' % (
        'H', '指数', 'IS档位', 'IS_n', 'IS胜率', 'IS_p', 'OOS_n', 'OOS胜率', 'OOS_p', 'OOS净'))
    for H in HORIZONS:
        for code, name, fn in C.INDICES:
            r = result[H].get(code)
            if not r or not r['is_stat']:
                print('%-4d %-9s 无显著档位' % (H, name))
                continue
            o = r['oos']
            if not o:
                print('%-4d %-9s %-20s %6d %6.1f%% %8.4f | OOS样本不足' % (
                    H, name, r['is_best'], r['is_stat']['n'], r['is_stat']['hit'] * 100, r['is_stat']['p']))
                continue
            net = o['ret'] - 0.20
            flag = '✅' if (o['p'] < 0.10 and net > 0.10) else ('⚠️' if net > 0 else '❌')
            print('%-4d %-9s %-20s %6d %6.1f%% %8.4f | %6d %6.1f%% %8.4f %+7.2f%% %s' % (
                H, name, r['is_best'][:20], r['is_stat']['n'], r['is_stat']['hit'] * 100, r['is_stat']['p'],
                o['n'], o['hit'] * 100, o['p'], net, flag))
        print('-' * 104)

    # ---- 按 H 汇总 ----
    print('\n【按周期汇总：OOS 是否成立的指数个数 / 平均 OOS 胜率 / 平均 OOS 净收益】')
    print('%-4s %8s %12s %12s %12s' % ('H', '成立数', '平均OOS胜率', '平均OOS净', 'OOS事件合计'))
    summary = {}
    for H in HORIZONS:
        okn = 0
        hits, nets, nevs = [], [], 0
        for code, name, fn in C.INDICES:
            r = result[H].get(code)
            if not r or not r.get('oos'):
                continue
            o = r['oos']
            net = o['ret'] - 0.20
            hits.append(o['hit'])
            nets.append(net)
            nevs += o['n']
            if o['p'] < 0.10 and net > 0.10:
                okn += 1
        if hits:
            summary[H] = dict(ok=okn, hit=float(np.mean(hits)), net=float(np.mean(nets)), n=nevs)
            print('%-4d %6d/5 %11.1f%% %+11.2f%% %12d' % (
                H, okn, np.mean(hits) * 100, np.mean(nets), nevs))
    json.dump(summary, open(os.path.join(C.STATE_DIR, 'horizon_summary.json'), 'w'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
