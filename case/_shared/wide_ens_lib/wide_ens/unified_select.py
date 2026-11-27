#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一选模引擎（四套算法家族口径归一）

为什么必须先统一（2026-10-19 大佬拍板）
--------------------------------------
原来 model_select.py 用 abs(score) >= th 统一过滤全部候选，但四个家族的分数
量纲完全不同，同一个 th 的严格程度差好几倍：

  train:lgb_cls    0.03 ~ 0.99    th0.50 → 覆盖 53%
  train:lgb_tri   -0.94 ~ 0.95    th0.50 → 覆盖 29%
  train:lgbr_reg  -4.21 ~ 4.29    th0.50 → 覆盖 72%   ← 回归输出，值域大
  wd:bag          -4.25 ~ 7.11    th0.50 → 覆盖 62%   ← 回归输出，值域大

不统一就比，等于让回归模型「什么都算触发」，必然占便宜。

统一办法：把每个候选的 |score| 换成**自身过去 500 日的百分位排名**
（rolling rank，只用历史，不含未来）。门槛改为 pct 门槛，覆盖率即被统一。

统一后的四点规范
----------------
1. 门槛：pct >= {0.40,0.50,0.60}(普适) / {0.85,0.90,0.95}(高精度, T+5 下探 0.80)
2. 重叠样本：wd 的 T+5 是逐日重叠的（ms/enh 已用 idx[::h] 抽样），
   wd-T+5 **先按完整日历每 5 日取 1，再套门槛**（先筛后抽仍会重叠）
3. lgb_cls 标 [只做多]：proba 恒 >=0 → sign() 恒 +1，胜率含 beta，
   必须和「同期基准上涨率」对比着看，不与多空策略混排
4. 选模不后视：IS(2019~2022) 只用已实现数据挑（Wilson 下限排序），
   OOS(2023起) 只做验收；OOS 再切两段看稳定性

用法:
  python3 bin/wide_ens/unified_select.py            # 打印四张表
  python3 bin/wide_ens/unified_select.py --json
"""
import os
import sys
import csv
import json
import glob
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

CACHE = os.path.join(WS, '.cache/wide_ens')
OUT_JSON = os.path.join(CACHE, 'unified_selected.json')

IS_LO, IS_HI = '2019-01-01', '2022-12-31'
OOS_LO, OOS_HI = '2023-01-01', '2030-01-01'
SPLIT = '2025-01-01'                 # OOS 稳定性切点
PCT_WIN, PCT_MINP = 500, 150         # 滚动分位窗口
MIN_N_IS = 25                        # IS 触发下限
MIN_N_IS_DD = 18                     # 去重叠序列样本天然少，放宽
MIN_N_OOS = 25
COST = 0.20
BAR = {1: 0.60, 5: 0.65}

# 两档门槛（按 pct，跨家族可比）
BROAD = dict(grid=(0.40, 0.50, 0.60), cov_lo=0.35, cov_hi=0.65,
             label='普适（覆盖 35~65%）')
PRECISE = {1: dict(grid=(0.85, 0.90, 0.95), cov_lo=0.03, cov_hi=0.20,
                   label='高精度（覆盖 3~20%）'),
           5: dict(grid=(0.80, 0.85, 0.90), cov_lo=0.05, cov_hi=0.22,
                   label='高精度（覆盖 5~22%）')}

NAMES = {'000300_SH': '沪深300', '000688_SH': '科创50', '000852_SH': '中证1000',
         '000905_SH': '中证500', '399006_SZ': '创业板'}
ORDER = ['000300_SH', '000905_SH', '000852_SH', '000688_SH', '399006_SZ']


# ---------------- 读取 + 归一 ----------------
def _load(path):
    with open(path, encoding='utf-8') as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        sc = next((c for c in ('score', 'proba', 'bagged') if c in cols), None)
        rc = next((c for c in ('ret', 'actual') if c in cols), None)
        if sc is None or rc is None:
            return None
        rows = []
        for r in rd:
            d = (r.get('trade_date') or '').strip()
            try:
                s = float(r[sc])
                v = float(r[rc])
            except (TypeError, ValueError):
                # ret 缺失（收益未到期）时仍保留 score —— 这就是 live 信号行
                try:
                    s = float(r[sc])
                except (TypeError, ValueError):
                    continue
                v = float('nan')
            if len(d) != 10 or not np.isfinite(s):
                continue
            rows.append((d, s, v))
    if len(rows) < 200:
        return None
    df = pd.DataFrame(rows, columns=['date', 'score', 'ret']).drop_duplicates(
        'date', keep='last').set_index('date').sort_index()
    return _normalize(df)


def _normalize(df):
    """统一标准：|score| 的滚动百分位；同时记录是否只能做多"""
    df = df.copy()
    df['longonly'] = bool((df['score'] >= 0).all())
    a = df['score'].abs()
    df['pct'] = a.rolling(PCT_WIN, min_periods=PCT_MINP).rank(pct=True)
    return df


LIVE_DIR = os.path.join(CACHE, 'live')


def _apply_live(dfs, code):
    """把 live/*.json 里的最新一日 score 接到候选曲线尾部（ret=NaN，不参与回测统计）

    ⚠️ 必须按 code 过滤：候选池 key 是 'family:短tag'（不含指数代码），
       五个指数的 live 文件名都叫 <code>_lgb_tri_d5_T1.json，
       不过滤就会把别的指数的信号接到本指数上（踩过：创业板拿到中证1000的分数）。
    """
    if not os.path.isdir(LIVE_DIR):
        return dfs
    for fn in os.listdir(LIVE_DIR):
        if not fn.endswith('.json'):
            continue
        try:
            o = json.load(open(os.path.join(LIVE_DIR, fn), encoding='utf-8'))
        except Exception:
            continue
        if o.get('code') != code:
            continue
        tag = o.get('tag') or fn[:-5]
        d, sc = o.get('date'), o.get('score')
        if not d or sc is None:
            continue
        # 候选池里的 key = 'family:lgb_tri_d3_T5'（family 之后不带指数代码），
        # 而 live 文件名 / tag 带代码前缀 → 这里必须剥掉，否则接不上
        parts = tag.split('_')
        short = '_'.join(parts[2:]) if len(parts) > 2 and parts[1].endswith(('SH', 'SZ')) else tag
        name = '%s:%s' % (o.get('family'), short)
        df = dfs.get(name)
        if df is None or d <= str(df.index[-1]):
            continue
        row = pd.DataFrame({'score': [float(sc)], 'ret': [np.nan],
                            'longonly': [bool(df['longonly'].iloc[0])],
                            'pct': [np.nan]}, index=[d])
        dfs[name] = pd.concat([df, row]).sort_index()
        a = dfs[name]['score'].abs()
        dfs[name]['pct'] = a.rolling(PCT_WIN, min_periods=PCT_MINP).rank(pct=True)
    return dfs


def build_pool(with_ens=True):
    pool = {}
    for kind in ('ms', 'enh', 'train'):
        for p in sorted(glob.glob(os.path.join(CACHE, kind, '*.csv'))):
            b = os.path.basename(p)
            if b.endswith('.imp.csv'):
                continue
            parts = b[:-4].split('_')
            if len(parts) < 3:
                continue
            code, tag = parts[0] + '_' + parts[1], '_'.join(parts[2:])
            if '_T' not in tag:
                continue
            h = int(tag.rsplit('_T', 1)[1])
            if h not in (1, 5):
                continue
            df = _load(p)
            if df is not None:
                pool.setdefault(code, {}).setdefault(h, {})['%s:%s' % (kind, tag)] = df

    for code in NAMES:
        for h in (1, 5):
            p = os.path.join(WS, '.cache/wide_daily', 'h%d_%s.csv' % (h, code))
            if not os.path.exists(p):
                continue
            rows = []
            with open(p, encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    try:
                        bg = float(r['bagged'])
                    except (TypeError, ValueError):
                        continue
                    try:
                        ac = float(r['actual'])
                    except (TypeError, ValueError):
                        ac = float('nan')
                    if not np.isfinite(bg):
                        continue
                    rows.append((r['trade_date'], bg, ac))
            if len(rows) < 200:
                continue
            df = pd.DataFrame(rows, columns=['date', 'score', 'ret']).drop_duplicates(
                'date', keep='last').set_index('date').sort_index()
            pool.setdefault(code, {}).setdefault(h, {})['wd:bag%d' % h] = _normalize(df)

    # live 尾部接上（T+5 的 csv 停在收益到期那天，不接就用了两周前的信号）
    for code in list(pool):
        for h in (1, 5):
            if pool[code].get(h):
                pool[code][h] = _apply_live(pool[code][h], code)

    # ENS：家族间 z 分数等权（滚动 250 日归一，无未来函数）。它是派生候选，非独立家族
    if with_ens:
        for code in list(pool):
            for h in (1, 5):
                dfs = pool[code].get(h) or {}
                if len(dfs) < 3:
                    continue
                cols, rets = {}, []
                for k, df in dfs.items():
                    s = df['score'].astype(float)
                    mu = s.rolling(250, min_periods=120).mean()
                    sd = s.rolling(250, min_periods=120).std()
                    cols[k] = ((s - mu) / sd.replace(0, np.nan)).clip(-4, 4)
                    rets.append(df['ret'])
                Z = pd.DataFrame(cols)
                ens = Z.mean(axis=1, skipna=True)
                ret = rets[0]
                for r in rets[1:]:
                    ret = ret.combine_first(r)
                e = pd.DataFrame({'score': ens, 'ret': ret}).dropna(subset=['score'])
                e = e[e['score'].abs() > 1e-9]
                if len(e) >= 200:
                    pool[code][h]['ENS:zavg'] = _normalize(e)
    return pool


# ---------------- 统计 ----------------
def wilson(w, n, z=1.96):
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - m) / d)


def binom_p(w, n):
    try:
        from scipy.stats import binomtest
        return float(binomtest(w, n, 0.5, alternative='greater').pvalue)
    except Exception:
        return 1.0


def seg(df, lo, hi, th, dedup=False, h=1):
    """pct 门槛切片。dedup=True 时先在完整日历上每 h 日取 1，再套门槛。"""
    d = df[(df.index >= lo) & (df.index <= hi)].dropna(subset=['pct', 'ret'])
    if dedup:
        d = d.iloc[::h]
    if not len(d):
        return None
    s = d[d['pct'] >= th]
    if not len(s):
        return None
    dr = s['ret'] * np.sign(s['score'])
    n = len(dr)
    w = int((dr > 0).sum())
    return dict(n=n, hit=w / n, wilson=wilson(w, n), p=binom_p(w, n),
                raw=float(dr.mean()), net=float(dr.mean()) - COST,
                cov=n / len(d), base_up=float((d['ret'] > 0).mean()))


def scan(dfs, h, grid, spec):
    """IS 期扫描所有 (候选, 门槛)，返回合格列表（按 Wilson 下限降序）"""
    out = []
    for name, df in dfs.items():
        # T+5 的重叠：wd 本身日频；ENS 由 wd 日频 union 而来，同重叠
        dd = (h == 5 and (name.startswith('wd') or name.startswith('ENS')))
        need = MIN_N_IS_DD if dd else MIN_N_IS
        for th in grid:
            st = seg(df, IS_LO, IS_HI, th, dedup=dd, h=h)
            if not st:
                continue
            st = dict(st, cand=name, thr=th, dedup=dd,
                      longonly=bool(df['longonly'].iloc[0]),
                      ok_band=spec['cov_lo'] <= st['cov'] <= spec['cov_hi'])
            out.append(st)
    out.sort(key=lambda r: (r['wilson'], r['n']), reverse=True)
    return out, need


def pick_one(dfs, h, spec):
    """按统一标准挑一个：覆盖率落在档位区间内 + IS 样本达标 + IS 方向为正，Wilson 下限最高"""
    cands, need = scan(dfs, h, spec['grid'], spec)
    valid = [c for c in cands if c['ok_band'] and c['n'] >= need and c['raw'] > 0]
    if not valid:
        # 记录为什么挑不出来
        reason = []
        if not cands:
            reason.append('该档位下无任何候选产生样本')
        else:
            top = cands[0]
            if not top['ok_band']:
                reason.append('覆盖率 %.0f%% 落在档位区间外' % (top['cov'] * 100))
            if top['n'] < need:
                reason.append('IS 样本不足（最多 %d < 需 %d）' % (top['n'], need))
            if top['raw'] <= 0:
                reason.append('IS 方向为负（%+.2f%%）' % (top['raw'] * 100))
        return dict(status='no_valid', reason='；'.join(reason) or '无合格候选',
                    nearest=cands[0] if cands else None)
    best = valid[0]
    top3 = []
    for c in valid[:3]:
        top3.append(dict(cand=c['cand'], thr=c['thr'], is_hit=c['hit'],
                         is_n=c['n'], is_cov=c['cov'], wilson=c['wilson']))
    df = dfs[best['cand']]
    dd = best['dedup']
    oos = seg(df, OOS_LO, OOS_HI, best['thr'], dedup=dd, h=h)
    o1 = seg(df, OOS_LO, SPLIT, best['thr'], dedup=dd, h=h)
    o2 = seg(df, SPLIT, OOS_HI, best['thr'], dedup=dd, h=h)
    ok = bool(oos and oos['n'] >= MIN_N_OOS and oos['hit'] >= BAR[h]
              and oos['p'] <= 0.10 and oos['raw'] > 0)
    # 稳定性：两段 OOS 是否同向
    stable = None
    if o1 and o2 and o1['n'] >= 8 and o2['n'] >= 8:
        stable = bool(o1['hit'] > 0.5 and o2['hit'] > 0.5)
    # 覆盖率漂移：门槛是按分数排序定的，若 OOS 触发率暴跌 → 分布变了，不可用
    drift = None
    if oos and best['cov'] > 0:
        drift = oos['cov'] / best['cov']
    collapsed = bool(oos and best['cov'] > 0 and drift is not None and drift < 0.5)
    return dict(status='ok' if ok else 'below_bar', bar=BAR[h],
                cand=best['cand'], thr=best['thr'], is_stat=best, oos=oos,
                oos_1=o1, oos_2=o2, stable=stable,
                longonly=best['longonly'], dedup=dd, top3=top3,
                n_valid=len(valid), cov_drift=drift, collapsed=collapsed)


def run(pool):
    res = {}
    for code in ORDER:
        res[code] = {}
        for h in (1, 5):
            dfs = (pool.get(code) or {}).get(h) or {}
            if not dfs:
                res[code][h] = {'broad': dict(status='no_candidate'),
                                'precise': dict(status='no_candidate')}
                continue
            res[code][h] = {'broad': pick_one(dfs, h, BROAD),
                            'precise': pick_one(dfs, h, PRECISE[h]),
                            'n_cand': len(dfs)}
    return res


# ---------------- 输出 ----------------
def _line(nm, h, r):
    if r.get('status') in ('no_valid', 'no_candidate'):
        if r.get('status') == 'no_candidate':
            return '%-9s T+%-2d | %-40s' % (nm, h, '⚠️ 该指数该周期无候选')
        near = r.get('nearest')
        tail = ''
        if near:
            tail = ' [最近: %s pct%.2f IS%.1f%% n=%d 覆盖%.0f%%]' % (
                near['cand'][:22], near['thr'], near['hit'] * 100,
                near['n'], near['cov'] * 100)
        return '%-9s T+%-2d | ⚠️ 选不出: %s%s' % (nm, h, r['reason'], tail)
    i, o = r['is_stat'], r['oos']
    is_txt = '%.1f%% n=%d' % (i['hit'] * 100, i['n'])
    if o:
        o_txt = '%.1f%% n=%d' % (o['hit'] * 100, o['n'])
        net = '%+.2f%%' % o['net']
        cov_o = '%.0f%%' % (o['cov'] * 100)
        base = '%.0f%%' % (o['base_up'] * 100)
    else:
        o_txt, net, cov_o, base = '-', '-', '-', '-'
    stab = '稳' if r.get('stable') is True else ('不稳' if r.get('stable') is False else '-')
    if r.get('collapsed'):
        stab = '塌缩'
    lo = ' [只多]' if r.get('longonly') else ''
    flag = 'OK' if r['status'] == 'ok' else 'NG'
    return '%-9s T+%-2d | %-30s pct%.2f | IS %-4s %-14s | OOS %-4s %-14s | %-8s | 基准%-4s | %-4s | %s%s' % (
        nm, h, r['cand'][:30], r['thr'], '%.0f%%' % (i['cov'] * 100), is_txt,
        cov_o, o_txt, net, base, stab, flag, lo)


def render(res, h, mode):
    spec = BROAD if mode == 'broad' else PRECISE[h]
    L = ['', '【T+%d / %s】门槛网格 pct %s，排序=Wilson 下限' % (
        h, spec['label'], '/'.join('%.2f' % t for t in spec['grid'])), '']
    L.append('%-9s %-4s | %-30s %-6s | %-5s %-14s | %-5s %-14s | %-8s | %-7s | %-4s | %s' % (
        '指数', '周期', '选定模型', '门槛', 'IS覆盖', 'IS胜率/样本',
        'OOS覆盖', 'OOS胜率/样本', 'OOS净收益', '基准上涨', '两段', '达标'))
    L.append('-' * 150)
    for code in ORDER:
        L.append(_line(NAMES[code], h, res[code][h][mode]))
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    pool = build_pool()
    res = run(pool)

    obj = dict(updated=pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
               standard='|score| 滚动500日分位（无未来函数）；IS(2019~2022)挑 → OOS(2023+)验收',
               rules=dict(broad=BROAD, precise=PRECISE, bar=BAR,
                          min_n_is=MIN_N_IS, min_n_is_dedup=MIN_N_IS_DD,
                          min_n_oos=MIN_N_OOS, cost_pct=COST),
               selected=res)
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)

    if a.json:
        print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
        return

    L = ['四套算法家族已统一口径后的选模结果',
         '标准：|score| 换成自身滚动 500 日百分位（跨家族可比）；IS 2019~2022 挑，OOS 2023 起验收',
         '数据时间：%s' % obj['updated'],
         '「基准」= 同期指数上涨频率；「两段」= OOS 前半段(2023~24)与后半段(2025~)是否都过半']
    for h in (1, 5):
        L += render(res, h, 'broad')
        L += render(res, h, 'precise')
    print('\n'.join(L))
    print('\n[save] %s' % OUT_JSON)


if __name__ == '__main__':
    main()
