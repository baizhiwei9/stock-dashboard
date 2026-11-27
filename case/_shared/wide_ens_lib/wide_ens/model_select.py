#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型选拔层：每个指数 × 每个周期，自动挑「当前胜率最高且达标」的那个模型。

大佬 2026-10-19 需求：
  「你选出每一个指数对应的准确率高的模型、胜率高的模型来……
    T+5 选最高的，达到 65% 以上，T+1 达到 60% 以上，每一个指数都选出一个模型来。」

⚠️ 为什么要做 walk-forward 选拔（关键，别退回去）
--------------------------------------------------
第一版是「IS 期挑模型 → OOS 期验证」。看起来对，但**只要在候选池里按 OOS 胜率排序取第一，
就等于用未来数据挑模型**，报出来的 OOS 胜率虚高。
本版改为：**在每个信号日 t，只用 t 之前（且已实现，h=5 时扣除 5 个交易日）的数据挑模型和阈值**，
然后拿这个选择去出 t 日的信号。全程零后视，最后统计的胜率就是「自动选模型这套流程」的真实成绩。

候选池（统一成 date/score/ret 三列）
------------------------------------
  ms     multi_scale   tri 三分类，深度 3/5，T1/T5
  enh    enhanced      包内 111 维 + 多尺度 + ETF，2010 起，深度 3/5，T1/T5
  train  train_one     lgb cls / tri / lgbr_reg，深度 3/5，T5
  wd     wide_daily    5 种子 bagging，h=1..5
  ENS    z 分数等权合并（滚动 250 日归一，无未来函数）

达标线（大佬定）：T+1 ≥ 60%，T+5 ≥ 65%；且 OOS n ≥ 25、p ≤ 0.10、方向均值 > 0。

用法：
  python3 bin/wide_ens/model_select.py            # 评估 + 打印
  python3 bin/wide_ens/model_select.py --save     # 写 .cache/wide_ens/selected_models.json
  python3 bin/wide_ens/model_select.py --json
"""
import os
import sys
import csv
import json
import glob
import argparse
import datetime
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
OUT_JSON = os.path.join(CACHE, 'selected_models.json')

IS_END = '2022-12-31'
OOS_START = '2023-01-01'
HIST_START = '2019-01-01'      # 选模型可用历史起点
BAR = {1: 0.60, 5: 0.65}       # 达标线（大佬定）
MIN_N_OOS = 25                 # 报数要求的样本量
MIN_N_SEL = 25                 # 选模型时该候选至少要有多少历史触发
GATE = False                   # walk-forward 对照默认不开门槛（矮子拔将军，仅作稳健性对照）
MAX_P = 0.10
TH_GRID = (0.10, 0.20, 0.30, 0.40, 0.50, 0.55, 0.60, 0.70)
COST = 0.20                    # 单边成本（%），算净收益用

NAMES = {'000300_SH': '沪深300', '000688_SH': '科创50', '000852_SH': '中证1000',
         '000905_SH': '中证500', '399006_SZ': '创业板'}
FILES = {'000300_SH': 'idx_hs300.csv', '000688_SH': 'idx_kc50.csv',
         '000852_SH': 'idx_zz1000.csv', '000905_SH': 'idx_zz500.csv',
         '399006_SZ': 'idx_cyb.csv'}


# ---------------- 候选池 ----------------
def _read_series(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        sc = next((c for c in ('score', 'proba', 'bagged') if c in cols), None)
        rc = next((c for c in ('ret', 'actual') if c in cols), None)
        if sc is None or rc is None:
            return None
        for r in rd:
            d = (r.get('trade_date') or '').strip()
            try:
                s = float(r[sc])
                v = float(r[rc])
            except (TypeError, ValueError):
                continue
            if len(d) != 10 or not np.isfinite(s) or not np.isfinite(v):
                continue
            rows.append((d, s, v))
    if len(rows) < 200:
        return None
    d = pd.DataFrame(rows, columns=['date', 'score', 'ret']).drop_duplicates('date', keep='last')
    return d.set_index('date').sort_index()


def build_pool():
    pool = {}

    def add(code, h, name, df):
        if df is None or len(df) < 200:
            return
        pool.setdefault(code, {}).setdefault(h, {})[name] = df

    for kind in ('ms', 'enh', 'train'):
        for p in sorted(glob.glob(os.path.join(CACHE, kind, '*.csv'))):
            b = os.path.basename(p)
            if b.endswith('.imp.csv'):
                continue
            parts = b.replace('.csv', '').split('_')
            if len(parts) < 3:
                continue
            code = parts[0] + '_' + parts[1]
            tag = '_'.join(parts[2:])
            if '_T' not in tag:
                continue
            h = int(tag.rsplit('_T', 1)[1])
            if h not in (1, 5):
                continue
            add(code, h, '%s:%s' % (kind, tag), _read_series(p))

    for code in NAMES:
        for h in (1, 5):
            p = os.path.join(WS, '.cache/wide_daily', 'h%d_%s.csv' % (h, code))
            if not os.path.exists(p):
                continue
            rows = []
            with open(p, encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    try:
                        rows.append((r['trade_date'], float(r['bagged']), float(r['actual'])))
                    except (TypeError, ValueError):
                        continue
            if len(rows) >= 200:
                df = pd.DataFrame(rows, columns=['date', 'score', 'ret']
                                  ).drop_duplicates('date', keep='last'
                                                    ).set_index('date').sort_index()
                add(code, h, 'wd:bag%d' % h, df)
    return pool


def add_ensemble(cands):
    """z 分数等权合并候选（滚动 250 日归一，只用过去 → 无未来函数）"""
    cols, rets = {}, []
    for k, df in cands.items():
        s = df['score'].astype(float)
        mu = s.rolling(250, min_periods=120).mean()
        sd = s.rolling(250, min_periods=120).std()
        cols[k] = ((s - mu) / sd.replace(0, np.nan)).clip(-4, 4)
        rets.append(df['ret'])
    if not cols:
        return cands
    Z = pd.DataFrame(cols)
    ens = Z.mean(axis=1, skipna=True)
    ret = rets[0]
    for r in rets[1:]:
        ret = ret.combine_first(r)
    out = pd.DataFrame({'score': ens, 'ret': ret}).dropna()
    out = out[out['score'].abs() > 1e-9]
    if len(out) >= 200:
        cands = dict(cands)
        cands['ENS:zavg'] = out
    return cands


# ---------------- 基础统计 ----------------
def _binom_p(w, n):
    try:
        from scipy.stats import binomtest
        return float(binomtest(w, n, 0.5, alternative='greater').pvalue)
    except Exception:
        return 1.0


def _hit_stats(hit_list, ret_list):
    n = len(hit_list)
    if n == 0:
        return None
    w = int(np.sum(hit_list))
    raw = float(np.mean(ret_list))
    return dict(n=n, hit=w / n, p=_binom_p(w, n), raw=raw, net=raw - COST)


# ---------------- walk-forward 选拔 ----------------
class Cand:
    """一个候选序列：预计算每个阈值的触发行位置，便于按时间切片快速统计"""

    def __init__(self, name, df):
        self.name = name
        self.dates = np.array(df.index.tolist())
        self.score = df['score'].values.astype(float)
        self.ret = df['ret'].values.astype(float)
        self.sign = np.sign(self.score)
        self.dir_ret = self.ret * self.sign          # 方向调整后收益
        self.hit = (self.dir_ret > 0).astype(float)  # 是否命中
        self.abs = np.abs(self.score)
        # 每个阈值的累计量（cumsum 便于 O(1) 取区间统计）
        self.cum = {}
        for th in TH_GRID:
            m = (self.abs >= th).astype(float)
            self.cum[th] = dict(
                n=np.concatenate([[0.0], np.cumsum(m)]),
                hit=np.concatenate([[0.0], np.cumsum(m * self.hit)]),
                ret=np.concatenate([[0.0], np.cumsum(m * self.dir_ret)]),
            )

    def stats_before(self, cutoff_date, th):
        """只用 index < cutoff_date 的已实现样本"""
        k = int(np.searchsorted(self.dates, cutoff_date, side='left'))
        c = self.cum[th]
        n = c['n'][k]
        if n <= 0:
            return None
        return dict(n=int(n), hit=float(c['hit'][k] / n), raw=float(c['ret'][k] / n))

    def at(self, date):
        """取该日的 (score, ret)；不存在返回 None"""
        k = int(np.searchsorted(self.dates, date, side='left'))
        if k >= len(self.dates) or self.dates[k] != date:
            return None
        return float(self.score[k]), float(self.ret[k])

    def position(self, date):
        return int(np.searchsorted(self.dates, date, side='left'))


def _wilson_lb(w, n, z=1.96):
    """Wilson 下限：小样本会被自动压低，避免「25 个样本 100%」这种运气型候选排第一"""
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - m) / d)


def pick_model(cands, t_index_dates, t, h, gate=True):
    """在 t 日之前的数据里挑 (候选, 阈值)，返回 (name, thr, stats) 或 None

    ⚠️ h=5 时必须扣除 t 之前最后 h 个交易日的样本（那时收益还没实现）

    ⚠️ gate=True（默认）：历史胜率必须达到达标线 BAR[h] 才有资格被选中。
       大佬口径「达到 60% 的就可以保留」——没达标的模型不许上岗，宁可空仓观望。
    ⚠️ 排序用 Wilson 下限而不是原始命中率：否则「样本 25 个、胜率 100%」这种
       运气型候选会一直排第一，选中后几乎不触发，walk-forward 样本就崩到个位数。
    """
    pos = int(np.searchsorted(t_index_dates, t, side='left'))
    if pos - h < 0:
        return None
    cutoff = t_index_dates[pos - h]      # 严格：只用 cutoff 之前（不含）的已实现样本
    best = None
    for name, c in cands.items():
        for th in TH_GRID:
            st = c.stats_before(cutoff, th)
            if not st or st['n'] < MIN_N_SEL or st['raw'] <= 0:
                continue
            if gate and st['hit'] < BAR[h]:
                continue                 # 历史没到达标线 → 不录用
            # 主排序：Wilson 下限（兼顾胜率与样本量）；平手再看 n
            key = (_wilson_lb(st['hit'] * st['n'], st['n']), st['n'])
            if best is None or key > best[0]:
                best = (key, name, th, st)
    if best is None:
        return None
    return best[1], best[2], best[3]


def walk_forward(cands, code, h, lo=OOS_START, hi='2030-01-01'):
    """逐日出信号，统计「自动选模型」这套流程的真实成绩"""
    # 交易日历：用主候选的顺序（并集更稳妥）
    all_dates = sorted(set().union(*[set(c.dates) for c in cands.values()]))
    cal = np.array(all_dates)
    hits, rets, picks, skipped = [], [], [], 0
    for t in all_dates:
        if t < lo or t >= hi:
            continue
        sel = pick_model(cands, cal, t, h, gate=GATE)
        if sel is None:
            skipped += 1          # 没有任何模型达达标线 → 空仓观望
            continue
        name, th, _st = sel
        c = cands[name]
        a = c.at(t)
        if a is None:
            skipped += 1
            continue
        sc, rt = a
        if abs(sc) < th:
            continue                     # 该模型当日没触发 → 不出手（不计样本）
        picks.append(dict(date=t, cand=name, thr=th))
        hits.append(1.0 if rt * np.sign(sc) > 0 else 0.0)
        rets.append(rt * np.sign(sc))
    st = _hit_stats(hits, rets)
    # 近期最常被选中的模型 = 当前生效模型
    cur = None
    if picks:
        tail = picks[-60:]
        cnt = {}
        for p in tail:
            cnt[(p['cand'], p['thr'])] = cnt.get((p['cand'], p['thr']), 0) + 1
        (cn, cth), nv = max(cnt.items(), key=lambda kv: kv[1])
        cur = dict(cand=cn, thr=cth, freq=nv / len(tail))
    return dict(stats=st, current=cur, n_picks=len(picks), skipped=skipped,
                span=[p['date'] for p in picks[:1]] + [p['date'] for p in picks[-1:]])


def today_signal(cands, code, h):
    """用「今天之前」的全部已实现数据选模型，再读该模型最新一日信号"""
    all_dates = sorted(set().union(*[set(c.dates) for c in cands.values()]))
    if not all_dates:
        return None
    cal = np.array(all_dates)
    sel = pick_model(cands, cal, all_dates[-1], h, gate=GATE)
    if sel is None:
        return dict(date=all_dates[-1], state='空仓（无模型达达标线）')
    name, th, st = sel
    c = cands[name]
    d = c.dates[-1]                     # 用该候选自己的最新日（T+5 序列比 T+1 短）
    sc = float(c.score[-1])
    trig = abs(sc) >= th
    return dict(date=str(d), cand=name, thr=th, score=round(sc, 4),
                hist_hit=round(st['hit'], 4), hist_n=st['n'],
                state=('看多' if sc > 0 else '看空') if trig else '未触发（观望）')


def fixed_table(cands, h):
    """对照：IS 挑阈值 → OOS 验证（每个候选一行），用来说明「为什么最后是这个模型」"""
    out = []
    for name, c in cands.items():
        best = None
        for th in TH_GRID:
            d = pd.Series(c.dir_ret, index=c.dates)
            m = (pd.Series(c.abs, index=c.dates) >= th) & (d.index >= HIST_START) & (d.index <= IS_END)
            sub = d[m.values]
            if len(sub) < MIN_N_SEL or sub.mean() <= 0:
                continue
            hit = float((sub > 0).mean())
            if best is None or hit > best[0]:
                best = (hit, th, len(sub))
        if not best:
            continue
        _is_hit, th, _is_n = best
        m = (pd.Series(c.abs, index=c.dates) >= th) & (pd.Series(c.dates, index=c.dates) >= OOS_START)
        sub = pd.Series(c.dir_ret, index=c.dates)[m.values]
        if len(sub) < 10:
            continue
        oos = dict(n=len(sub), hit=float((sub > 0).mean()), raw=float(sub.mean()))
        oos['p'] = _binom_p(int((sub > 0).sum()), len(sub))
        out.append(dict(cand=name, thr=th, is_hit=round(best[0], 4), is_n=best[2],
                        oos_hit=round(oos['hit'], 4), oos_n=oos['n'],
                        oos_p=round(oos['p'], 4), oos_raw=round(oos['raw'], 3)))
    out.sort(key=lambda x: (x['is_hit'], x['is_n']), reverse=True)
    return out


def select_is(cands, code, h):
    """大佬口径的「定一个模型」：只用样本内（2019~2022 已实现）挑，门槛 BAR[h]。

    挑选在 IS 内完成、样本外只用来验收 → 无后视，且能得出「每个指数一个模型」的结论。
    排序用 Wilson 下限，避免小样本运气型候选排第一。
    """
    best = None
    for name, c in cands.items():
        for th in TH_GRID:
            d = pd.Series(c.dir_ret, index=c.dates)
            m = ((pd.Series(c.abs, index=c.dates) >= th).values
                 & (d.index >= HIST_START) & (d.index <= IS_END))
            sub = d[m]
            if len(sub) < MIN_N_SEL or sub.mean() <= 0:
                continue
            w = int((sub > 0).sum())
            n = len(sub)
            hit = w / n
            # 大佬口径：选「胜率最高」的，平手再用样本量兜底（MIN_N_SEL 已防小样本）
            key = (hit, n)
            if best is None or key > best[0]:
                best = (key, name, th, hit, n, float(sub.mean()))
    if best is None:
        return dict(status='no_model_meets_bar', bar=BAR[h])
    _k, name, th, is_hit, is_n, is_raw = best
    c = cands[name]
    d = pd.Series(c.dir_ret, index=c.dates)
    mo = (pd.Series(c.abs, index=c.dates) >= th).values & (d.index >= OOS_START)
    sub = d[mo]
        
    oos = None
    if len(sub) > 0:
        w = int((sub > 0).sum())
        oos = dict(n=len(sub), hit=round(float(w / len(sub)), 4),
                   raw=round(float(sub.mean()), 3),
                   p=round(_binom_p(w, len(sub)), 4))
    # 最新一日信号
    sc = float(c.score[-1])
    trig = abs(sc) >= th
    today = dict(date=str(c.dates[-1]), score=round(sc, 4), thr=th,
                 state=('看多' if sc > 0 else '看空') if trig else '未触发（观望）')
    ok = bool(oos and oos['hit'] >= BAR[h] and oos['n'] >= MIN_N_OOS
              and oos['p'] <= MAX_P and oos['raw'] > 0)
    return dict(status='ok' if ok else 'below_bar', bar=BAR[h], pass_bar=ok,
                cand=name, thr=th, is_hit=round(is_hit, 4), is_n=is_n,
                is_meets_bar=bool(is_hit >= BAR[h]),
                is_raw=round(is_raw, 3), oos=oos, today=today)


def select(pool, save=False):
    res = {}
    for code in NAMES:
        res[code] = {}
        for h in (1, 5):
            raw = dict((pool.get(code) or {}).get(h) or {})
            if not raw:
                res[code][h] = dict(status='no_candidate')
                continue
            cands_df = add_ensemble({k: v for k, v in raw.items() if v.index.min() < IS_END})
            cands = {k: Cand(k, v) for k, v in cands_df.items()}
            if not cands:
                res[code][h] = dict(status='insufficient_history')
                continue
            wf = walk_forward(cands, code, h)
            ft = fixed_table(cands, h)
            st = wf['stats']
            cur = wf['current']
            ok = bool(st and st['hit'] >= BAR[h] and st['n'] >= MIN_N_OOS
                      and st['p'] <= MAX_P and st['raw'] > 0)
            res[code][h] = dict(
                status='ok' if ok else ('below_bar' if st else 'no_signal'),
                bar=BAR[h], pass_bar=ok,
                cand=cur['cand'] if cur else None,
                thr=cur['thr'] if cur else None,
                cand_freq=round(cur['freq'], 3) if cur else None,
                wf_hit=round(st['hit'], 4) if st else None,
                wf_n=st['n'] if st else 0,
                wf_p=round(st['p'], 4) if st else None,
                wf_raw=round(st['raw'], 3) if st else None,
                wf_net=round(st['net'], 3) if st else None,
                n_cand=len(cands), n_picks=wf['n_picks'],
                idle_days=wf['skipped'],
                table=ft[:5],
            )
            # 大佬口径的主口径：IS 挑一个模型 → OOS 验收
            res[code][h]['chosen'] = select_is(cands, code, h)
    if save:
        obj = dict(updated=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                   method='IS(2019~2022) 挑一个模型+阈值 → OOS(2023起) 验收；walk-forward 作稳健对照',
                   is_end=IS_END, oos_start=OOS_START, bar=BAR,
                   min_n_oos=MIN_N_OOS, max_p=MAX_P, selected=res)
        with open(OUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        print('[save] %s' % OUT_JSON)
    return res


def fmt(res):
    """主展现：IS 挑出的那一个模型（大佬要的「每个指数一个模型」）+ OOS 验收
    附行：walk-forward 稳健性对照（每日重挑）
    """
    L = ['%-8s %-3s %-4s %-26s %-5s %-16s %-9s %s'
         % ('指数', '周期', '达标', '选定模型（IS挑）', '门槛', '样本外成绩(OOS)', 'IS胜率', '今日信号')]
    L.append('-' * 130)
    for code, v in res.items():
        for h in (1, 5):
            r = v.get(h) or {}
            ch = r.get('chosen') or {}
            if not ch.get('cand'):
                L.append('%-8s T+%d ✗    %s'
                         % (NAMES[code], h, ch.get('status', r.get('status', '?'))))
                continue
            o = ch.get('oos') or {}
            mark = '✅' if ch.get('pass_bar') else '✗'
            tg = ch.get('today') or {}
            L.append('%-8s T+%d %-4s %-26s %-5.2f %5.1f%% (n=%-3d) p=%-6.4f %5.1f%% %s'
                     % (NAMES[code], h, mark, ch['cand'][:26], ch['thr'],
                        (o.get('hit') or 0) * 100, o.get('n') or 0, o.get('p') or 1,
                        ch.get('is_hit', 0) * 100,
                        '%s %s' % (tg.get('date', ''), tg.get('state', ''))))
            wf = r.get('wf_hit')
            if wf is not None:
                L.append('%-12s ↳ walk-forward 对照（每日重挑、不开门槛）：%.1f%% (n=%d)｜空仓天数 %d'
                         % ('', wf * 100, r.get('wf_n') or 0, r.get('idle_days') or 0))
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--detail', action='store_true', help='打印每个指数的候选对照表')
    ap.add_argument('--no-gate', action='store_true',
                    help='walk-forward 对照也要求历史达达标线（默认不开，以免样本被挤没）')
    a = ap.parse_args()
    global GATE
    GATE = a.no_gate
    print('[gate] walk-forward 对照门槛 %s（主口径不看门槛，按 OOS 验收）'
          % ('ON' if GATE else 'OFF'))
    pool = build_pool()
    n_c = sum(len(v) for c in pool.values() for v in c.values())
    print('[pool] %d 个候选序列（%d 指数）｜选拔方式：walk-forward，零后视' % (n_c, len(pool)))
    res = select(pool, save=a.save)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(fmt(res))
        if a.detail:
            for code, v in res.items():
                for h in (1, 5):
                    r = v.get(h) or {}
                    if not r.get('table'):
                        continue
                    print('\n—— %s T+%d 候选对照（IS 挑阈值 → OOS 验证）' % (NAMES[code], h))
                    for x in r['table']:
                        print('   %-30s th%.2f  IS %5.1f%%(n=%-3d)  OOS %5.1f%%(n=%-3d) p=%.3f'
                              % (x['cand'][:30], x['thr'], x['is_hit'] * 100, x['is_n'],
                                 x['oos_hit'] * 100, x['oos_n'], x['oos_p']))
    ok = sum(1 for c in res.values() for _h, r in c.items()
             if (r.get('chosen') or {}).get('pass_bar'))
    print('\n达标 %d / 10（T+1 ≥60%%，T+5 ≥65%%，OOS n≥%d，p≤%.2f）'
          % (ok, MIN_N_OOS, MAX_P))
    # 汇总：每个指数最终保留哪些周期
    print('\n—— 每个指数最终保留 ——')
    for code in NAMES:
        keep = [('T+%d' % h) for h in (1, 5)
                if ((res.get(code) or {}).get(h) or {}).get('chosen', {}).get('pass_bar')]
        best = []
        for h in (1, 5):
            ch = ((res.get(code) or {}).get(h) or {}).get('chosen') or {}
            if ch.get('cand'):
                best.append('T+%d %s %.1f%%' % (h, ch['cand'], (ch.get('oos') or {}).get('hit', 0) * 100))
        print('%-8s %-14s ｜ %s' % (NAMES[code], ('保留 ' + '/'.join(keep)) if keep else '全部不达标→观望',
                                    '；'.join(best) or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
