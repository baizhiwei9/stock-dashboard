#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日增量预测（live）：把「收益未到期」的尾部也训出来，产出最新一日的 score。

为什么需要它
------------
ms / enh / train 三个家族训练完后都做了 `dropna(subset=['ret'])`：
  · T+5 要等 5 个交易日才有收益 → 最后 5 行被删
  · 结果是 T+5 的 csv 停在 2026-09-29，而行情已到 2026-10-16
也就是说**现有 csv 里的最后一条 score 是两周前的**，直接拿来出信号会用一个过期信号。

本脚本复用各家族**完全相同**的 build() 与训练参数（窗口/最小样本/重训间隔/超参），
只是不再要求 ret 存在 → 得到最新交易日的 score，并沿用「滚动 500 日分位」算门槛分位。

一致性保障（必须与训练时一致，否则门槛失效）
--------------------------------------------
  ms    : multi_scale.TRAIN_WIN=750  MIN_TRAIN=500  RETRAIN=20  n_est=400 lr=.05 mcs=30
  enh   : enhanced.TRAIN_WIN=1000    MIN_TRAIN=500  RETRAIN=20  同上
  train : train_win=750 min_train=500 retrain=20  model/target 由 tag 决定

用法：
  python3 bin/wide_ens/unified_live.py --family ms --code 000300_SH --h 5 --depth 5
  python3 bin/wide_ens/unified_live.py --family train --code 000300_SH --h 5 \
          --model lgb --target tri --depth 3
  python3 bin/wide_ens/unified_live.py --family enh --code 000905_SH --h 1 --depth 3
输出：.cache/wide_ens/live/<family>_<code>_<tag>.json
"""
import os
import sys
import json
import time
import argparse
import warnings

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

LIVE_DIR = os.path.join(WS, '.cache/wide_ens/live')
os.makedirs(LIVE_DIR, exist_ok=True)

PCT_WIN, PCT_MINP = 500, 150


def _tri_labels(y_ret, lab):
    q1, q2 = lab['ret'].quantile(0.30), lab['ret'].quantile(0.70)

    def label(v):
        if v <= q1:
            return 0
        if v >= q2:
            return 2
        return 1

    return y_ret.apply(lambda v: label(v) if pd.notna(v) else -1)


def _walk_forward(X, y_all, mk, train_win, min_train, retrain, n_est=400,
                  depth=3, lr=0.05, mcs=30, seed=0):
    """与各家族 run() 完全相同的循环，唯一区别：不因最新日 ret 为 NaN 而丢弃"""
    idx = X.index
    n = len(idx)
    score = pd.Series(np.nan, index=idx)
    n_fit = 0
    for pos in range(0, n, retrain):
        lo = max(0, pos - train_win)
        if pos - lo < min_train:
            continue
        seg_lab = y_all.iloc[lo:pos]
        ok = seg_lab >= 0
        if ok.sum() < min_train or seg_lab[ok].nunique() < 3:
            continue
        Xtr = X.iloc[lo:pos][ok.values].values
        ytr = seg_lab[ok].values.astype(int)
        m = mk(seed, n_est, depth, lr, mcs)
        m.fit(Xtr, ytr)
        seg = idx[pos:min(pos + retrain, n)]
        pc = m.predict_proba(X.loc[seg].values)
        score.loc[seg] = pc[:, 2] - pc[:, 0]
        n_fit += 1
    return score, n_fit


def _pct_of(hist_abs, live_abs):
    """把最新一日的 |score| 放进历史序列里算滚动分位（与 unified_select 同一套口径）"""
    s = pd.concat([hist_abs, pd.Series([live_abs])], ignore_index=True)
    win = s.iloc[-PCT_WIN:]
    if len(win) < PCT_MINP:
        return None
    return float((win <= live_abs).sum() / len(win))


def _hist_abs(family, fname_tag, h):
    """读训练期已落盘的 csv，取历史 |score| 序列（作为分位基准）"""
    p = os.path.join(WS, '.cache/wide_ens', family, fname_tag + '.csv')
    if not os.path.exists(p):
        return None
    d = pd.read_csv(p)
    col = next((c for c in ('score', 'proba', 'bagged') if c in d.columns), None)
    if col is None:
        return None
    return d[col].abs().dropna()


def run_ms(code, h, depth, seed=0):
    import multi_scale as MS
    X, y_ret, lab = MS.build(code, h)
    y_all = _tri_labels(y_ret, lab)
    score, n_fit = _walk_forward(X, y_all, MS.mk_lgb, MS.TRAIN_WIN, MS.MIN_TRAIN,
                                 MS.RETRAIN, depth=depth, seed=seed)
    tag = '%s_lgb_tri_d%d_T%d' % (code, depth, h)
    return score, n_fit, 'ms', tag, '%s_lgb_tri_d%d_T%d' % (code, depth, h)


def run_enh(code, h, depth, seed=0):
    import enhanced as EN
    X, y_ret, lab = EN.build(code, h, use_pkg=True)
    y_all = _tri_labels(y_ret, lab)
    score, n_fit = _walk_forward(X, y_all, EN.mk_lgb, EN.TRAIN_WIN, EN.MIN_TRAIN,
                                 EN.RETRAIN, depth=depth, seed=seed)
    tag = '%s_lgb_tri_d%d_T%d' % (code, depth, h)
    return score, n_fit, 'enh', tag, tag


def _fast_last(X, y_all, mk, train_win, min_train, retrain, n_est=400,
               depth=3, lr=0.05, mcs=30, seed=0):
    """只算最后一天：直接拟合「最后一个重训块」的模型，再预测最后一行。

    与全量 walk_forward 等价（最后一天的 score 本来就来自最后一个重训块），
    但不用把历史上每个块都重训一遍 → 400s 降到几秒。
    """
    n = len(X)
    pos = ((n - 1) // retrain) * retrain   # 最后一块的起点（与 run() 的 range 一致）
    lo = max(0, pos - train_win)
    if pos - lo < min_train:
        return None, 0
    seg_lab = y_all.iloc[lo:pos]
    ok = seg_lab >= 0
    if ok.sum() < min_train or seg_lab[ok].nunique() < 3:
        return None, 0
    m = mk(seed, n_est, depth, lr, mcs)
    m.fit(X.iloc[lo:pos][ok.values].values, seg_lab[ok].values.astype(int))
    pc = m.predict_proba(X.iloc[[n - 1]].values)[0]
    return float(pc[2] - pc[0]), 1


def run_train(code, h, model, target, depth, seed=0):
    """复用 train_one 的 build_xy（keep_tail=True）与模型工厂"""
    import train_one as T
    X, y_cls, r = T.build_xy(code, h)
    y, is_reg = T.make_target(r, target)
    idx = X.index
    n = len(idx)
    if is_reg:
        proba = pd.Series(np.nan, index=idx)
    else:
        proba = pd.Series(np.nan, index=idx)
    n_fit = 0
    TRAIN_WIN, MIN_TRAIN, RETRAIN = 750, 500, 20
    for pos in range(0, n, RETRAIN):
        lo = max(0, pos - TRAIN_WIN)
        if pos - lo < MIN_TRAIN:
            continue
        Xtr = X.iloc[lo:pos].fillna(0.0)
        ytr = y.iloc[lo:pos]
        if is_reg:
            msk = r.iloc[lo:pos].notna().values
            if msk.sum() < MIN_TRAIN:
                continue
            Xtr, ytr = Xtr[msk], ytr[msk]
        else:
            # 分类：用有标签的样本（尾部标签缺失时按 0 处理会把噪音喂进去）
            msk = r.iloc[lo:pos].notna().values
            if msk.sum() < MIN_TRAIN or ytr[msk].nunique() < 2:
                continue
            Xtr, ytr = Xtr[msk], ytr[msk]
        m = T.mk_model(model, seed, 400, depth, 0.05, 30)
        m.fit(Xtr.values, np.asarray(ytr.values, dtype=(float if is_reg else int)))
        seg = idx[pos:min(pos + RETRAIN, n)]
        Xs = X.loc[seg].fillna(0.0)
        if not len(Xs):
            continue
        if is_reg:
            proba.loc[seg] = m.predict(Xs.values)
        elif target == 'tri':
            pc = m.predict_proba(Xs.values)
            proba.loc[seg] = pc[:, 2] - pc[:, 0]
        else:
            proba.loc[seg] = m.predict_proba(Xs.values)[:, 1]
        n_fit += 1
    tag = '%s_%s_%s_d%d_T%d' % (code, model, target, depth, h)
    return proba, n_fit, 'train', tag, tag


def run_fast(family, code, h, depth, model='lgb', target='tri', seed=0):
    """只算最后一天（快路）。返回 (date, score, hist_tag)"""
    if family == 'ms':
        import multi_scale as MS
        X, y_ret, lab = MS.build(code, h)
        y_all = _tri_labels(y_ret, lab)
        sc, _ = _fast_last(X, y_all, MS.mk_lgb, MS.TRAIN_WIN, MS.MIN_TRAIN,
                           MS.RETRAIN, depth=depth, seed=seed)
        tag = '%s_lgb_tri_d%d_T%d' % (code, depth, h)
    elif family == 'enh':
        import enhanced as EN
        X, y_ret, lab = EN.build(code, h, use_pkg=True)
        y_all = _tri_labels(y_ret, lab)
        sc, _ = _fast_last(X, y_all, EN.mk_lgb, EN.TRAIN_WIN, EN.MIN_TRAIN,
                           EN.RETRAIN, depth=depth, seed=seed)
        tag = '%s_lgb_tri_d%d_T%d' % (code, depth, h)
    else:
        import train_one as T
        X, y_cls, r = T.build_xy(code, h)
        y, is_reg = T.make_target(r, target)
        n = len(X)
        TRAIN_WIN, MIN_TRAIN, RETRAIN = 750, 500, 20
        pos = ((n - 1) // RETRAIN) * RETRAIN
        lo = max(0, pos - TRAIN_WIN)
        msk = r.iloc[lo:pos].notna().values if pos > lo else np.array([], bool)
        if msk.sum() < MIN_TRAIN:
            return None, None, None
        Xtr = X.iloc[lo:pos][msk].fillna(0.0)
        ytr = y.iloc[lo:pos][msk]
        mm = T.mk_model(model, seed, 400, depth, 0.05, 30)
        mm.fit(Xtr.values, np.asarray(ytr.values, dtype=(float if is_reg else int)))
        Xs = X.iloc[[n - 1]].fillna(0.0)
        if is_reg:
            sc = float(mm.predict(Xs.values)[0])
        elif target == 'tri':
            pc = mm.predict_proba(Xs.values)[0]
            sc = float(pc[2] - pc[0])
        else:
            sc = float(mm.predict_proba(Xs.values)[0][1])
        tag = '%s_%s_%s_d%d_T%d' % (code, model, target, depth, h)
    if sc is None:
        return None, None, None
    return str(X.index[-1].date()), sc, tag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', required=True, choices=['ms', 'enh', 'train'])
    ap.add_argument('--code', required=True)
    ap.add_argument('--h', type=int, required=True)
    ap.add_argument('--depth', type=int, default=3)
    ap.add_argument('--model', default='lgb')
    ap.add_argument('--target', default='tri')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--fast', action='store_true', help='只算最后一天（秒级）')
    a = ap.parse_args()

    t0 = time.time()
    if a.fast:
        date, sc, hist_tag = run_fast(a.family, a.code, a.h, a.depth,
                                      a.model, a.target, a.seed)
        if sc is None:
            print('无输出')
            return 1
        hist = _hist_abs(a.family, hist_tag, a.h)
        pct = _pct_of(hist, abs(sc)) if hist is not None and len(hist) else None
        out = dict(family=a.family, code=a.code, h=a.h, depth=a.depth,
                   model=a.model if a.family == 'train' else 'lgb',
                   target=a.target if a.family == 'train' else 'tri',
                   tag=hist_tag, date=date, score=round(sc, 6),
                   pct=round(pct, 6) if pct is not None else None,
                   n_fit=1, fast=True, elapsed_s=round(time.time() - t0, 1))
        # ⚠️ 文件名必须带 family 前缀：ms 与 enh 的 hist_tag 完全相同（都是
        #    <code>_lgb_tri_d<d>_T<h>），不加前缀会互相覆盖，导致 unified_select
        #    只能拿到后写的那一家，另一家信号卡在 csv 尾部（2026-10-19 踩过）
        p = os.path.join(LIVE_DIR, '%s_%s.json' % (a.family, hist_tag))
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print('[live-fast] %-5s %-9s T+%d d%d  %s score=%+.4f pct=%s  %.0fs' % (
            a.family, a.code, a.h, a.depth, date, sc,
            ('%.3f' % pct) if pct is not None else '-', time.time() - t0))
        return 0

    if a.family == 'ms':
        score, n_fit, fam, tag, hist_tag = run_ms(a.code, a.h, a.depth, a.seed)
    elif a.family == 'enh':
        score, n_fit, fam, tag, hist_tag = run_enh(a.code, a.h, a.depth, a.seed)
    else:
        score, n_fit, fam, tag, hist_tag = run_train(a.code, a.h, a.model,
                                                     a.target, a.depth, a.seed)

    s = score.dropna()
    if not len(s):
        print('无输出')
        return 1
    last_date = str(s.index[-1].date())
    last_score = float(s.iloc[-1])
    hist = _hist_abs(fam, hist_tag, a.h)
    pct = _pct_of(hist, abs(last_score)) if hist is not None and len(hist) else None

    # 与历史 csv 里的最后一条比对（用于自检一致性）
    hist_last_date = str(hist.index[-1]) if hist is not None and len(hist) else None
    out = dict(family=fam, code=a.code, h=a.h, depth=a.depth,
               model=a.model if fam == 'train' else 'lgb',
               target=a.target if fam == 'train' else 'tri',
               tag=tag, date=last_date, score=round(last_score, 6),
               pct=round(pct, 6) if pct is not None else None,
               n_fit=n_fit, elapsed_s=round(time.time() - t0, 1),
               hist_last_date=hist_last_date,
               stale_days=(len(s) and None))
    # 同上：文件名带 family 前缀，避免 ms/enh 同名互相覆盖
    p = os.path.join(LIVE_DIR, '%s_%s.json' % (fam, tag))
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('[live] %-6s %-9s T+%d d%d  %s score=%+.4f pct=%s  %.0fs → %s' % (
        fam, a.code, a.h, a.depth, last_date, last_score,
        ('%.3f' % pct) if pct is not None else '-', time.time() - t0, p))
    return 0


if __name__ == '__main__':
    sys.exit(main())
