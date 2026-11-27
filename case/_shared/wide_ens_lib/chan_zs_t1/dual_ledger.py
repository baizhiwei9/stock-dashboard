#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日频双轨预测 · 台账基准表（SQLite）——记录每次预测，到期结算，滚动统计当年准确率

大佬 2026-10-22 需求：
  「不是让你建立那个数据库基线吗？就是开始统计预测的正确准确率。」

设计（与 daily_ledger.py / weekly_ledger.py 同思路，但适配**双轨**信号）：
  · 表 dual_ledger：一条 = 某指数 × 某信号日的一次预测
  · 同时记三条轨，互不混淆：
      ① 原算法（pred 符号，全部样本）
      ② ①经 AC 闸门过滤后（中枢外且 AC<+0.10 → 观望，不计方向）
      ③ 低波口径（仅「中枢内 + 低波」触发，贴沿均值回复）
  · record  ：每日流水线跑完 today_dual 后，把 5 指数写进表（幂等）
  · settle  ：下一交易日收盘后结算 → actual_ret / 命中
  · stats   ：当年（YTD）与全历史 方向命中率、基线上涨率、AC 闸门增益、校准分档

口径（严格因果，避免自欺）：
  · 命中：看多 y>0，看空 y<0；y = 目标日收盘/信号日收盘-1（%）
  · pred 本身就是 walk-forward（750 日训练窗、5 种子均值），历史列**无后视**
  · AC1_40 / rv20 分位全部扩张窗口（≥250 样本才出分位）
  · 同期基线「上涨率」= 同样本 y>0 的比例 —— 不看这列，命中率会被 beta 骗

用法：
  python3 research/chan_zs_t1/dual_ledger.py --init
  python3 research/chan_zs_t1/dual_ledger.py --backfill     # 回补 2016 至今（无后视）
  python3 research/chan_zs_t1/dual_ledger.py --record       # 记录最新一天（实盘）
  python3 research/chan_zs_t1/dual_ledger.py --settle       # 结算到期
  python3 research/chan_zs_t1/dual_ledger.py --stats        # 全年/全期统计
  python3 research/chan_zs_t1/dual_ledger.py --md           # 卡片用简短块
"""
import os
import sys
import csv
import json
import glob
import sqlite3
import argparse
import datetime
import warnings

warnings.filterwarnings('ignore')

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WS, '.cache/chan_zs_t1')
RAW = os.path.join(WS, '.cache/weekly_forecast/raw')
DB = os.path.join(OUT, 'dual_ledger.db')

IDX = [('idx_hs300.csv', '沪深300', '000300_SH'),
       ('idx_zz500.csv', '中证500', '000905_SH'),
       ('idx_zz1000.csv', '中证1000', '000852_SH'),
       ('idx_kc50.csv', '科创50', '000688_SH'),
       ('idx_cyb.csv', '创业板', '399006_SZ')]

AC_GATE = 0.10
LOW_Q = 1 / 3
MIN_EXPQ = 250
BACKFILL_FROM = '2016-01-01'

DDL = """
CREATE TABLE IF NOT EXISTS dual_ledger (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  sig_date     TEXT NOT NULL,        -- 信号日（数据截止的收盘日）
  code         TEXT NOT NULL,
  name         TEXT NOT NULL,
  chan         TEXT,                 -- above / below / inside / none
  zd           REAL,
  zg           REAL,
  rel          REAL,                 -- 相对中枢半宽位置
  ac1_40       REAL,
  vol_band     TEXT,                 -- 低波 / 中波 / 高波（pred_std 分位）
  psq          REAL,
  rvq          REAL,
  pred         REAL,                 -- ① 原算法预测分（%）
  pred_std     REAL,
  act_raw      TEXT,                 -- ① 原算法方向：看多 / 看空
  act_gated    TEXT,                 -- ② AC 闸门后：看多 / 看空 / 观望
  gate         TEXT,                 -- PASS / BLOCK / NULL(中枢内不用闸门)
  act_low      TEXT,                 -- ③ 低波口径：看多 / 看空 / 不触发 / 不适用
  base_close   REAL,
  target_date  TEXT,
  target_close REAL,
  actual_ret   REAL,                 -- 次日涨跌 %
  hit_raw      INTEGER,              -- ① 是否命中
  hit_gated    INTEGER,              -- ② 是否命中（观望为空）
  hit_low      INTEGER,              -- ③ 是否命中（未触发为空）
  settled_at   TEXT,
  created_at   TEXT,
  src          TEXT DEFAULT 'live',  -- live=每日实盘 / backfill=历史回补
  UNIQUE(sig_date, code)
);
CREATE INDEX IF NOT EXISTS ix_dl2_sig ON dual_ledger(sig_date);
CREATE INDEX IF NOT EXISTS ix_dl2_code ON dual_ledger(code);
"""


# ---------------- 基础 ----------------
def conn():
    os.makedirs(OUT, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db(c):
    c.executescript(DDL)
    c.commit()


_closes = {}


def closes(nm):
    """{date_str: close}，date_str = YYYY-MM-DD"""
    if nm in _closes:
        return _closes[nm]
    fn = [f for f, n, _ in IDX if n == nm][0]
    out = {}
    with open(os.path.join(RAW, fn), encoding='utf-8') as f:
        for row in csv.DictReader(f):
            d = (row.get('trade_date') or '').strip()
            try:
                v = float(row.get('close'))
            except (TypeError, ValueError):
                continue
            if len(d) == 8 and v > 0:
                out['%s-%s-%s' % (d[:4], d[4:6], d[6:])] = v
    _closes[nm] = out
    return out


def next_day(nm, sig):
    ds = sorted(closes(nm).keys())
    for d in ds:
        if d > sig:
            return d
    return None


def actual_ret(nm, sig, tgt):
    if not tgt:
        return None
    cd = closes(nm)
    a, b = cd.get(sig), cd.get(tgt)
    if not a or not b:
        return None
    return (b / a - 1) * 100


# ---------------- 历史回补（panel，无后视） ----------------
def _expq(vals):
    """扩张窗口分位：只用当日及之前的样本（≥MIN_EXPQ）"""
    import numpy as np
    out, buf = [], []
    for v in vals:
        if v == v and v not in (float('inf'), float('-inf')):
            buf.append(v)
        if len(buf) >= MIN_EXPQ and v == v:
            out.append(float((np.array(buf) < v).mean()))
        else:
            out.append(None)
    return out


def build_hist_rows(nm):
    """从 panel + raw 生成本指数的全部历史行（含低波分位、闸门、命中）"""
    import numpy as np
    import pandas as pd

    fn = [f for f, n, _ in IDX if n == nm][0]
    raw = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
    raw['dt'] = pd.to_datetime(raw['trade_date'], format='%Y%m%d', errors='coerce')
    raw = raw.dropna(subset=['dt']).set_index('dt').sort_index()
    raw = raw[~raw.index.duplicated(keep='last')]
    c = pd.to_numeric(raw['close'], errors='coerce').astype(float)
    lr = np.log(c / c.shift(1))
    rv20 = lr.rolling(20).std()

    p = pd.read_csv(os.path.join(OUT, 'panel_%s.csv' % nm), index_col=0, parse_dates=True)
    p = p[~p.index.duplicated(keep='last')].sort_index()
    # ⚠️ panel 里的 chan/zd/zg/rel 是 chan_state.py（老 v1 口径）算的，与实盘 today_dual.py
    #    用的 zhongshu_v3.chan_v3 一致率只有 ~79%（实测沪深300 0.790 / 科创50 0.858）。
    #    台账必须与实盘卡片同源，否则 AC 闸门与低波口径的样本对不上 → 这里用 chan_v3 重算。
    try:
        from zhongshu_v3 import chan_v3
        cs = chan_v3(c, pd.to_numeric(raw['high'], errors='coerce').astype(float),
                     pd.to_numeric(raw['low'], errors='coerce').astype(float))
        for col in ('zd', 'zg', 'chan'):
            p[col] = cs[col].reindex(p.index).values
        mid = (p['zd'] + p['zg']) / 2
        half = (p['zg'] - p['zd']) / 2
        p['rel'] = (p['close'] - mid) / half.replace(0, np.nan)
    except Exception as e:
        print('  !! chan_v3 重算失败（退回 panel 老口径）：%s' % e, flush=True)
    p['rv20'] = rv20.reindex(p.index).values
    psq = _expq(p['pred_std'].astype(float).values)
    # rvq 与 today_dual 同口径：用 lr 全序列扩张分位（与 rv20 对齐取）
    rvq_full = dict(zip(c.index, _expq(lr.values)))
    p['psq'] = psq
    p['rvq'] = [rvq_full.get(i) for i in p.index]
    # ⚠️ AC1_40 必须与 today_dual.py 同口径（**对数收益**、窗口 40、含当日）
    #    panel 里的 ac1_40 是 chan_ac_state.py 用简单收益算的，两者会有小差异；
    #    台账要和实盘卡片同源，所以这里重算，不直接用 panel 列。
    lrv = lr.values
    acv = np.full(len(lrv), np.nan)
    for i in range(40, len(lrv)):
        w = lrv[i - 40:i]
        if not np.all(np.isfinite(w)):
            continue
        a, b = w[:-1], w[1:]
        sa, sb = a.std(), b.std()
        if sa < 1e-12 or sb < 1e-12:
            continue
        acv[i] = float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))
    ac_ser = pd.Series(acv, index=c.index)
    p['ac_log'] = ac_ser.reindex(p.index).values

    rows = []
    for dt, r in p.iterrows():
        pred = r['pred']
        if pred != pred:
            continue
        sig = str(dt.date())
        base = float(r['close'])
        psq_v = r['psq'] if r['psq'] == r['psq'] else None
        rvq_v = r['rvq'] if r['rvq'] == r['rvq'] else None
        low = ((psq_v is not None and psq_v < LOW_Q) or
               (rvq_v is not None and rvq_v < LOW_Q))
        band = None
        if psq_v is not None:
            band = '低波' if psq_v < LOW_Q else ('中波' if psq_v < 2 / 3 else '高波')
        chan = r['chan'] if isinstance(r['chan'], str) else None
        ac_v = r['ac_log'] if r['ac_log'] == r['ac_log'] else None
        gate = None
        if chan in ('above', 'below'):
            gate = 'PASS' if (ac_v is not None and ac_v >= AC_GATE) else 'BLOCK'
        act_raw = '看多' if pred > 0 else '看空'
        if gate == 'BLOCK':
            act_gated = '观望'
        elif gate is None:
            act_gated = '观望'          # 中枢内不走闸门逻辑，闸门轨不计
        else:
            act_gated = act_raw
        rel = r['rel'] if r['rel'] == r['rel'] else None
        if chan == 'inside' and low and rel is not None:
            act_low = '看多' if rel < 0 else '看空'
        elif chan == 'inside':
            act_low = '不触发'
        else:
            act_low = '不适用'

        y = float(r['y']) if r['y'] == r['y'] else None
        tgt = next_day(nm, sig) if y is not None else None
        tc = closes(nm).get(tgt) if tgt else None
        d_raw = 1 if pred > 0 else -1
        hit_raw = None if y is None else int(y * d_raw > 0)
        hit_gated = None
        if y is not None and act_gated in ('看多', '看空'):
            hit_gated = int(y * (1 if act_gated == '看多' else -1) > 0)
        hit_low = None
        if y is not None and act_low in ('看多', '看空'):
            hit_low = int(y * (1 if act_low == '看多' else -1) > 0)

        rows.append(dict(
            sig_date=sig, code=[k for f, n, k in IDX if n == nm][0], name=nm,
            chan=chan, zd=float(r['zd']) if r['zd'] == r['zd'] else None,
            zg=float(r['zg']) if r['zg'] == r['zg'] else None, rel=rel,
            ac1_40=ac_v,
            vol_band=band, psq=psq_v, rvq=rvq_v,
            pred=float(pred), pred_std=float(r['pred_std']) if r['pred_std'] == r['pred_std'] else None,
            act_raw=act_raw, act_gated=act_gated, gate=gate, act_low=act_low,
            base_close=base, target_date=tgt, target_close=tc,
            actual_ret=y, hit_raw=hit_raw, hit_gated=hit_gated, hit_low=hit_low,
            settled_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if y is not None else None,
            src='backfill'))
    return rows


def cmd_backfill(verbose=True):
    c = conn()
    init_db(c)
    total = 0
    for _, nm, _ in IDX:
        rows = [r for r in build_hist_rows(nm) if r['sig_date'] >= BACKFILL_FROM]
        for r in rows:
            c.execute(
                'INSERT INTO dual_ledger (sig_date,code,name,chan,zd,zg,rel,ac1_40,vol_band,'
                'psq,rvq,pred,pred_std,act_raw,act_gated,gate,act_low,base_close,target_date,'
                'target_close,actual_ret,hit_raw,hit_gated,hit_low,settled_at,src) '
                'VALUES (:sig_date,:code,:name,:chan,:zd,:zg,:rel,:ac1_40,:vol_band,:psq,:rvq,'
                ':pred,:pred_std,:act_raw,:act_gated,:gate,:act_low,:base_close,:target_date,'
                ':target_close,:actual_ret,:hit_raw,:hit_gated,:hit_low,:settled_at,:src) '
                'ON CONFLICT(sig_date,code) DO UPDATE SET '
                'chan=excluded.chan, pred=excluded.pred, act_raw=excluded.act_raw, '
                'act_gated=excluded.act_gated, gate=excluded.gate, act_low=excluded.act_low, '
                'vol_band=excluded.vol_band, ac1_40=excluded.ac1_40, actual_ret=excluded.actual_ret, '
                'hit_raw=excluded.hit_raw, hit_gated=excluded.hit_gated, hit_low=excluded.hit_low '
                'WHERE dual_ledger.src="backfill"', r)
            total += 1
        if verbose:
            print('  %s 回补 %d 行（%s ~ %s）' % (nm, len(rows),
                  rows[0]['sig_date'] if rows else '-', rows[-1]['sig_date'] if rows else '-'))
    c.commit()
    c.close()
    print('[backfill] 共 %d 行' % total)
    return 0


# ---------------- record（实盘，取最新 today_dual_*.json） ----------------
def cmd_record(verbose=True):
    fs = sorted(glob.glob(os.path.join(OUT, 'today_dual_*.json')))
    if not fs:
        print('[record] 找不到 today_dual_*.json')
        return 1
    d = json.load(open(fs[-1], encoding='utf-8'))
    sig = d['date']
    rep = d['rep']
    c = conn()
    init_db(c)
    n_new = n_upd = 0
    for nm, r in rep.items():
        code = [k for f, n, k in IDX if n == nm][0]
        chan = r.get('chan')
        gate = r.get('gate')
        pred = r.get('pred')
        if pred is None:
            continue
        act_raw = '看多' if pred > 0 else '看空'
        act_gated = act_raw if gate == 'PASS' else '观望'
        # 低波口径
        psq, rvq = r.get('psq'), r.get('rvq')
        low = ((psq is not None and psq < LOW_Q) or (rvq is not None and rvq < LOW_Q))
        rel = r.get('rel')
        if chan == 'inside' and low and rel is not None:
            act_low = '看多' if rel < 0 else '看空'
        elif chan == 'inside':
            act_low = '不触发'
        else:
            act_low = '不适用'
        base = closes(nm).get(sig) or r.get('close')
        tgt = next_day(nm, sig)
        cur = c.execute('SELECT id, src FROM dual_ledger WHERE sig_date=? AND code=?',
                        (sig, code)).fetchone()
        vals = dict(sig_date=sig, code=code, name=nm, chan=chan,
                    zd=r.get('zd'), zg=r.get('zg'), rel=rel,
                    ac1_40=r.get('ac'), vol_band=r.get('vol_predstd'),
                    psq=psq, rvq=rvq, pred=float(pred), pred_std=r.get('pred_std'),
                    act_raw=act_raw, act_gated=act_gated, gate=gate, act_low=act_low,
                    base_close=base, target_date=tgt,
                    created_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if cur is None:
            c.execute(
                'INSERT INTO dual_ledger (sig_date,code,name,chan,zd,zg,rel,ac1_40,vol_band,'
                'psq,rvq,pred,pred_std,act_raw,act_gated,gate,act_low,base_close,target_date,'
                'created_at,src) VALUES (:sig_date,:code,:name,:chan,:zd,:zg,:rel,:ac1_40,'
                ':vol_band,:psq,:rvq,:pred,:pred_std,:act_raw,:act_gated,:gate,:act_low,'
                ':base_close,:target_date,:created_at,"live")', vals)
            n_new += 1
        elif cur['src'] == 'live':
            vals['id'] = cur['id']
            c.execute('UPDATE dual_ledger SET chan=:chan, zd=:zd, zg=:zg, rel=:rel, '
                      'ac1_40=:ac1_40, vol_band=:vol_band, psq=:psq, rvq=:rvq, pred=:pred, '
                      'pred_std=:pred_std, act_raw=:act_raw, act_gated=:act_gated, gate=:gate, '
                      'act_low=:act_low, base_close=:base_close, target_date=:target_date '
                      'WHERE id=:id', vals)
            n_upd += 1
    c.commit()
    c.close()
    if verbose:
        print('[record] 信号日 %s：新增 %d 条，更新 %d 条' % (sig, n_new, n_upd))
    return 0


# ---------------- settle（结算到期） ----------------
def cmd_settle(verbose=True):
    c = conn()
    init_db(c)
    rows = c.execute("SELECT * FROM dual_ledger WHERE hit_raw IS NULL OR settled_at IS NULL").fetchall()
    n = 0
    for r in rows:
        nm = r['name']
        sig = r['sig_date']
        tgt = r['target_date'] or next_day(nm, sig)
        if not tgt:
            continue
        rt = actual_ret(nm, sig, tgt)
        if rt is None:
            continue
        d_raw = 1 if (r['pred'] or 0) > 0 else -1
        hit_raw = int(rt * d_raw > 0)
        hit_gated = None
        if r['act_gated'] in ('看多', '看空'):
            hit_gated = int(rt * (1 if r['act_gated'] == '看多' else -1) > 0)
        hit_low = None
        if r['act_low'] in ('看多', '看空'):
            hit_low = int(rt * (1 if r['act_low'] == '看多' else -1) > 0)
        tc = closes(nm).get(tgt)
        c.execute('UPDATE dual_ledger SET target_date=?, target_close=?, actual_ret=?, '
                  'hit_raw=?, hit_gated=?, hit_low=?, settled_at=? WHERE id=?',
                  (tgt, tc, rt, hit_raw, hit_gated, hit_low,
                   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), r['id']))
        n += 1
    c.commit()
    c.close()
    if verbose:
        print('[settle] 结算 %d 条（剩余待结算 %d）' % (n, len(rows) - n))
    return 0


# ---------------- stats ----------------
def _pct(w, n):
    return (w / n * 100) if n else float('nan')


def _binom_p(w, n):
    try:
        from scipy.stats import binomtest
        return float(binomtest(w, n, 0.5, alternative='greater').pvalue)
    except Exception:
        return float('nan')


def collect(where='', args=(), ytd=True):
    c = conn()
    init_db(c)
    year = datetime.datetime.now().year
    q = "SELECT * FROM dual_ledger WHERE hit_raw IS NOT NULL"
    if ytd:
        q += " AND sig_date >= '%d-01-01'" % year
    if where:
        q += ' AND ' + where
    rows = [dict(r) for r in c.execute(q, args)]
    c.close()
    return rows


def summarize(rows):
    out = {}
    # ① 原算法
    v = [r for r in rows if r['hit_raw'] is not None]
    out['raw'] = dict(n=len(v), hit=_pct(sum(r['hit_raw'] for r in v), len(v)),
                      up=_pct(sum(1 for r in v if (r['actual_ret'] or 0) > 0), len(v)),
                      mean=(sum(r['actual_ret'] or 0 for r in v) / len(v)) if v else float('nan'),
                      p=_binom_p(sum(r['hit_raw'] for r in v), len(v)))
    # ② 闸门后（看多/看空才算）
    v = [r for r in rows if r['hit_gated'] is not None]
    out['gated'] = dict(n=len(v), hit=_pct(sum(r['hit_gated'] for r in v), len(v)),
                        up=_pct(sum(1 for r in v if (r['actual_ret'] or 0) > 0), len(v)),
                        p=_binom_p(sum(r['hit_gated'] for r in v), len(v)))
    # ② 对照：被闸门挡掉的（BLOCK 档）
    v = [r for r in rows if r['gate'] == 'BLOCK' and r['hit_raw'] is not None]
    out['blocked'] = dict(n=len(v), hit=_pct(sum(r['hit_raw'] for r in v), len(v)),
                          p=_binom_p(sum(r['hit_raw'] for r in v), len(v)))
    # ③ 低波口径
    v = [r for r in rows if r['hit_low'] is not None]
    out['low'] = dict(n=len(v), hit=_pct(sum(r['hit_low'] for r in v), len(v)),
                      up=_pct(sum(1 for r in v if (r['actual_ret'] or 0) > 0), len(v)),
                      p=_binom_p(sum(r['hit_low'] for r in v), len(v)))
    return out


def recent_rows(n_days=20):
    """最近 n_days 个信号日（按台账里出现过的 sig_date 倒序取）"""
    c = conn()
    init_db(c)
    ds = [r[0] for r in c.execute(
        "SELECT DISTINCT sig_date FROM dual_ledger WHERE hit_raw IS NOT NULL "
        "ORDER BY sig_date DESC LIMIT ?", (n_days,))]
    if not ds:
        c.close()
        return []
    q = ("SELECT * FROM dual_ledger WHERE hit_raw IS NOT NULL AND sig_date>=? "
         "ORDER BY sig_date")
    rows = [dict(r) for r in c.execute(q, (min(ds),))]
    c.close()
    return rows


def cmd_stats(ytd_only=False):
    rows = collect(ytd=True) if ytd_only else collect(ytd=False)
    if not rows:
        print('台账暂无已结算记录')
        return 0
    year = datetime.datetime.now().year
    tag = ('%d 年' % year) if ytd_only else '全历史'
    print('=' * 72)
    print('日频双轨预测 · 准确率台账（%s，已结算 %d 条）' % (tag, len(rows)))
    print('=' * 72)
    s = summarize(rows)
    print('\n【① 原算法（全部样本）】n=%d  方向命中 %.2f%%  同期上涨率 %.2f%%  次日均值 %+.4f%%  p=%.3f' % (
        s['raw']['n'], s['raw']['hit'], s['raw']['up'], s['raw']['mean'], s['raw']['p']))
    print('【② AC 闸门放行】    n=%d  方向命中 %.2f%%  同期上涨率 %.2f%%  p=%.3f' % (
        s['gated']['n'], s['gated']['hit'], s['gated']['up'], s['gated']['p']))
    print('   （对照）被闸门挡掉 n=%d  方向命中 %.2f%%  p=%.3f' % (
        s['blocked']['n'], s['blocked']['hit'], s['blocked']['p']))
    print('【③ 低波口径触发】  n=%d  方向命中 %.2f%%  同期上涨率 %.2f%%  p=%.3f' % (
        s['low']['n'], s['low']['hit'], s['low']['up'], s['low']['p']))

    # 滚动窗口（用户要「每一周对一下子」）
    for w in (5, 20, 60):
        rw = recent_rows(w)
        if not rw:
            continue
        sw = summarize(rw)
        gtxt = ('%.1f%%(n=%d)' % (sw['gated']['hit'], sw['gated']['n'])
                if sw['gated']['n'] else '无放行')
        print('  近 %-2d 日（%s~%s）：① %6.2f%%(n=%d)  ② 闸门 %s  ③ 低波 %s' % (
            w, rw[0]['sig_date'], rw[-1]['sig_date'], sw['raw']['hit'], sw['raw']['n'],
            gtxt,
            ('%.1f%%(n=%d)' % (sw['low']['hit'], sw['low']['n'])
             if sw['low']['n'] else '无触发')))

    print('\n-- 分指数（① 原算法）--')
    for nm in [n for _, n, _ in IDX]:
        sub = [r for r in rows if r['name'] == nm]
        if not sub:
            continue
        w = sum(r['hit_raw'] for r in sub if r['hit_raw'] is not None)
        n = sum(1 for r in sub if r['hit_raw'] is not None)
        g = [r for r in sub if r['hit_gated'] is not None]
        gw = sum(r['hit_gated'] for r in g)
        print('  %-8s n=%-5d ①%6.2f%%   ②闸门后 n=%-4d %6.2f%%' % (
            nm, n, _pct(w, n), len(g), _pct(gw, len(g))))

    print('\n-- 分中枢状态（① 原算法）--')
    ST = {'above': '中枢外·上', 'below': '中枢外·下', 'inside': '中枢内', 'none': '无中枢', None: 'NA'}
    for st in ('above', 'below', 'inside'):
        sub = [r for r in rows if r['chan'] == st and r['hit_raw'] is not None]
        if not sub:
            continue
        w = sum(r['hit_raw'] for r in sub)
        print('  %-10s n=%-5d 命中 %6.2f%%  上涨率 %6.2f%%' % (
            ST[st], len(sub), _pct(w, len(sub)),
            _pct(sum(1 for r in sub if (r['actual_ret'] or 0) > 0), len(sub))))

    print('\n-- 校准：|pred| 五档（① 原算法）--')
    v = sorted([r for r in rows if r['pred'] is not None and r['hit_raw'] is not None],
               key=lambda r: abs(r['pred']))
    if v:
        k = max(1, len(v) // 5)
        for i in range(5):
            sub = v[i * k:(i + 1) * k] if i < 4 else v[4 * k:]
            if not sub:
                continue
            ap = sum(abs(r['pred']) for r in sub) / len(sub)
            w = sum(r['hit_raw'] for r in sub)
            print('  第%d档 |pred|≈%.3f  n=%-4d 命中 %6.2f%%  次日均值 %+.4f%%' % (
                i + 1, ap, len(sub), _pct(w, len(sub)),
                sum(r['actual_ret'] or 0 for r in sub) / len(sub)))
    return 0


def md_lines(ytd_only=True):
    """卡片用简短 markdown 行列表（push_feishu / make_today_report 直接引用）"""
    rows = collect(ytd=True) if ytd_only else collect(ytd=False)
    if not rows:
        return ['📊 当年准确度：台账暂无已结算记录']
    year = datetime.datetime.now().year
    s = summarize(rows)
    L = []
    tag = ('%d 年准确率' % year) if ytd_only else '全历史准确率'
    L.append('📊 **%s**（已结算 %d 条，含历史回补）' % (tag, len(rows)))
    L.append('① 原算法 **%.1f%%**（n=%d，同期上涨率 %.1f%%）' % (
        s['raw']['hit'], s['raw']['n'], s['raw']['up']))
    if s['gated']['n']:
        gain = s['gated']['hit'] - s['raw']['hit']
        L.append('② AC 闸门放行 **%.1f%%**（n=%d，%+.1fpp）｜ 被挡掉 %.1f%%（n=%d）' % (
            s['gated']['hit'], s['gated']['n'], gain,
            s['blocked']['hit'], s['blocked']['n']))
    else:
        L.append('② AC 闸门放行：区间内尚无放行样本')
    if s['low']['n']:
        L.append('③ 低波口径 **%.1f%%**（n=%d）' % (s['low']['hit'], s['low']['n']))
    else:
        L.append('③ 低波口径：区间内尚无触发样本')
    rw = recent_rows(20)
    if rw:
        sw = summarize(rw)
        L.append('近20日：① **%.1f%%**(n=%d) ② 闸门 %s ③ 低波 %s' % (
            sw['raw']['hit'], sw['raw']['n'],
            ('%.0f%%(n=%d)' % (sw['gated']['hit'], sw['gated']['n'])
             if sw['gated']['n'] else '无放行'),
            ('%.0f%%(n=%d)' % (sw['low']['hit'], sw['low']['n'])
             if sw['low']['n'] else '无触发')))
    return L


def cmd_md(ytd_only=True):
    print('\n'.join(md_lines(ytd_only=ytd_only)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--init', action='store_true')
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--settle', action='store_true')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--md', action='store_true')
    ap.add_argument('--all-history', action='store_true', help='stats/md 用全历史而非当年')
    ap.add_argument('--daily', action='store_true', help='record+settle+stats 一次跑完（管道用）')
    a = ap.parse_args()

    if a.init:
        c = conn()
        init_db(c)
        c.close()
        print('[init] %s' % DB)
    if a.backfill:
        cmd_backfill()
    if a.record or a.daily:
        cmd_record()
    if a.settle or a.daily:
        cmd_settle()
    if a.stats or a.daily:
        cmd_stats(ytd_only=not a.all_history)
    if a.md:
        cmd_md(ytd_only=not a.all_history)
    if not any([a.init, a.backfill, a.record, a.settle, a.stats, a.md, a.daily]):
        ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
