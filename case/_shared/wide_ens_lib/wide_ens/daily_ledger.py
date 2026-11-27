#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日频预测台账 · 基准表（SQLite）——五指数每日信号入表，到期结算，滚动统计当年准确度

大佬 2026-10-19 需求（本会话两个任务）：
  「在数据库里建一个基准表，每一周对一下子，看我预测的胜率对不对。
    从今天开始算，一直统计历史胜率，统计当年预测的准确度，每次报告里带上当年准确度。
    日频你就统计日频的，周频你就统计周频的预测的准确度。」

分工：
  · 周频（周日 20:00 卡片，T+1/T+5 双周期）→ weekly_ledger.py / weekly_forecast_card.py ⑥ 段
  · 日频（每交易日 15:40 卡片，各指数按自己的 h）→ **本脚本** / signal_card.py ⑤ 段

口径（与卡片、校准表完全一致，避免自欺）：
  · 一条 = 某指数 × 某信号日的一次预测（h 取该指数在卡片里实际用的周期）
  · 命中：看多 r>0，看空 r<0；r = 目标日收盘 / 信号日收盘 - 1（%）
  · 观望不计入方向命中率，但记录 score 符号是否正确（dir_hit）供参考
  · 目标日 = 信号日之后第 h 个交易日（交易日历取自各指数自己的日线）
  · 「同期基准上涨率」= 同样本里 r>0 的比例 —— 不看这列，看多命中率会被 beta 骗
  · backfill 的 pred_hit 用**扩窗**（只用该日之前已结算的样本）→ 无后视

用法：
  python3 bin/wide_ens/daily_ledger.py --init
  python3 bin/wide_ens/daily_ledger.py --backfill        # 回补本年至今
  python3 bin/wide_ens/daily_ledger.py --settle          # 结算到期
  python3 bin/wide_ens/daily_ledger.py --record          # 记录今日信号
  python3 bin/wide_ens/daily_ledger.py --stats
  python3 bin/wide_ens/daily_ledger.py --md              # 卡片用 markdown 块
"""
import os
import sys
import csv
import json
import sqlite3
import argparse
import datetime
import warnings

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WS, '.cache/weekly_forecast/raw')
MS_DIR = os.path.join(WS, '.cache/wide_ens/ms')
DB = os.path.join(WS, '.cache/wide_ens/daily_ledger.db')

FILES = {'000300_SH': 'idx_hs300.csv', '000688_SH': 'idx_kc50.csv',
         '000852_SH': 'idx_zz1000.csv', '000905_SH': 'idx_zz500.csv',
         '399006_SZ': 'idx_cyb.csv'}

BACKFILL_FROM = '2026-01-01'   # 与周频台账同起点：本年 YTD
MIN_HIST = 20                  # 扩窗算 pred_hit 至少要有多少历史样本
MIN_N = 25                     # 选档门槛：样本数（与卡片 MIN_N 一致）
MIN_HIT = 0.60                 # 选档门槛：胜率
MAX_P = 0.10                   # 选档门槛：p 值
MIN_TH = 0.20                  # 门槛必须真能过滤（th0.00 = 永远在场 = beta）
TH_GRID = (0.0, 0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70)

DDL = """
CREATE TABLE IF NOT EXISTS daily_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sig_date      TEXT NOT NULL,          -- 信号日（卡片所依据的收盘日）
  code          TEXT NOT NULL,
  name          TEXT NOT NULL,
  h             INTEGER NOT NULL,       -- 该指数在卡片里实际使用的周期
  period        TEXT NOT NULL,          -- T+h
  act           TEXT NOT NULL,          -- 看多 / 看空 / 观望
  dirn          INTEGER,                -- 1 / -1 / 0
  score         REAL,                   -- 模型分 P(涨)-P(跌)
  thr           REAL,                   -- 触发阈值
  pred_hit      REAL,                   -- 发出时模型自报的同档历史胜率
  pred_band     TEXT,
  oos_n         INTEGER,                -- 该档样本外样本数
  reliable      INTEGER,
  pos           REAL,                   -- 当时建议仓位
  base_close    REAL,
  target_date   TEXT,
  target_close  REAL,
  actual_ret    REAL,                   -- 区间涨跌 %
  result        TEXT,                   -- 命中 / 未命中 / 观望不计 / 待结算
  dir_hit       INTEGER,
  settled_at    TEXT,
  created_at    TEXT,
  src           TEXT DEFAULT 'live',    -- live=每日实盘 / backfill=历史回补
  UNIQUE(sig_date, code, h)
);
CREATE INDEX IF NOT EXISTS ix_dl_sig ON daily_ledger(sig_date);
CREATE INDEX IF NOT EXISTS ix_dl_res ON daily_ledger(result);
"""


# ---------------- 基础 ----------------
def conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db(c):
    c.executescript(DDL)
    cols = {r['name'] for r in c.execute('PRAGMA table_info(daily_ledger)')}
    if 'src' not in cols:
        c.execute("ALTER TABLE daily_ledger ADD COLUMN src TEXT DEFAULT 'live'")
    c.commit()


_cclose = {}


def load_close(code):
    if code in _cclose:
        return _cclose[code]
    p = os.path.join(RAW, FILES[code])
    out = {}
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                d = (row.get('trade_date') or '').strip()
                try:
                    v = float(row.get('close'))
                except (TypeError, ValueError):
                    continue
                if len(d) == 8 and v > 0:
                    out['%s-%s-%s' % (d[:4], d[4:6], d[6:])] = v
    _cclose[code] = out
    return out


def trading_days(code):
    return sorted(load_close(code).keys())


def target_date_for(code, sig_date, h):
    ds = trading_days(code)
    if not ds:
        return None
    pos = None
    for i, d in enumerate(ds):
        if d <= sig_date:
            pos = i
        else:
            break
    if pos is None:
        return None
    j = pos + h
    return ds[j] if j < len(ds) else None


def band_flags(code, depth, h, thr):
    """该档位是否「统计达标」（n>=25 且 hit>=60% 且 p<=0.10 且 raw>0）"""
    p = os.path.join(MS_DIR, '%s_lgb_tri_d%d_T%d.json' % (code, depth, h))
    if not os.path.exists(p):
        return 0, None
    try:
        d = json.load(open(p))
        b = (d.get('bands') or {}).get('th%.2f' % thr) or {}
        o = b.get('oos') or {}
        ok = (o.get('n', 0) >= 25 and (o.get('hit') or 0) >= 0.60
              and (o.get('p') is not None and o.get('p') <= 0.10) and (o.get('raw') or 0) > 0)
        return (1 if ok else 0), o.get('n')
    except Exception:
        return 0, None


def _binom_p(w, n):
    try:
        from scipy.stats import binomtest
        return float(binomtest(w, n, 0.5, alternative='greater').pvalue)
    except Exception:
        return 1.0


def _pick_thr_walk(hist):
    """只用 hist（该日之前的样本）挑阈值，返回 (thr, hit, reliable)。

    与 weekly_ledger._pick_band_walk 同口径：优先选「n>=25 且 hit>=60% 且 p<=0.10
    且均值>0」里最低（最保守）的达标档；都不达标则退回样本足够里胜率最高档，
    但标 reliable=False。**禁止使用当日及未来的样本。**
    """
    if len(hist) < MIN_N:
        return None
    parsed = []
    for th in TH_GRID:
        sub = [(s, r) for s, r in hist if abs(s) >= th]
        n = len(sub)
        if n < MIN_N:
            continue
        w = sum(1 for s, r in sub if r * (1 if s > 0 else -1) > 0)
        raw = sum(r for s, r in sub) / n
        parsed.append(dict(th=th, n=n, hit=w / n, p=_binom_p(w, n), raw=raw))
    if not parsed:
        return None
    ok = [x for x in parsed if x['th'] >= MIN_TH and x['hit'] >= MIN_HIT
          and x['p'] <= MAX_P and x['raw'] > 0]
    if ok:
        ok.sort(key=lambda x: x['th'])
        return ok[0], True
    pool = [x for x in parsed if x['th'] >= MIN_TH] or parsed
    best = max(pool, key=lambda x: x['hit'])
    return best, False


# ---------------- record（每日实盘） ----------------
def cmd_record(verbose=True):
    import signal_card as SC

    rows = SC.build_rows()
    ok = [r for r in rows if not r.get('missing') and r.get('date')]
    if not ok:
        print('[record] 没有可用信号（缺 shap / 校准表？）')
        return 1

    c = conn()
    init_db(c)
    n_new = n_upd = 0
    for r in ok:
        code, name, h = r['code'], r['name'], int(r['tf'].replace('T+', ''))
        depth = dict((c, d) for c, _, _, d, _, _, _ in SC.CFG)[code]
        sig = r['date']
        closes = load_close(code)
        base = closes.get(sig)
        if base is None:
            ds = [d for d in trading_days(code) if d <= sig]
            base = closes.get(ds[-1]) if ds else None
        tgt = target_date_for(code, sig, h)
        cur = c.execute('SELECT id, result FROM daily_ledger WHERE sig_date=? AND code=? AND h=?',
                        (sig, code, h)).fetchone()
        if cur is not None and cur['result'] != '待结算':
            continue      # 已结算的不覆盖
        vals = dict(sig_date=sig, code=code, name=name, h=h, period='T+%d' % h,
                    act=r['act'], dirn=r.get('dirn', 0), score=r.get('score'),
                    thr=r.get('thr'), pred_hit=r.get('hit'), pred_band=r.get('band'),
                    oos_n=r.get('oos_n'),
                    reliable=band_flags(code, depth, h, r.get('thr') or 0)[0],
                    pos=r.get('pos') or 0.0, base_close=base, target_date=tgt,
                    result='待结算',
                    created_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if cur is None:
            c.execute(
                'INSERT INTO daily_ledger (sig_date,code,name,h,period,act,dirn,score,thr,'
                'pred_hit,pred_band,oos_n,reliable,pos,base_close,target_date,result,created_at) '
                'VALUES (:sig_date,:code,:name,:h,:period,:act,:dirn,:score,:thr,:pred_hit,'
                ':pred_band,:oos_n,:reliable,:pos,:base_close,:target_date,:result,:created_at)', vals)
            n_new += 1
        else:
            vals['id'] = cur['id']
            c.execute('UPDATE daily_ledger SET act=:act, dirn=:dirn, score=:score, thr=:thr, '
                      'pred_hit=:pred_hit, pred_band=:pred_band, oos_n=:oos_n, reliable=:reliable, '
                      'pos=:pos, base_close=:base_close, target_date=:target_date WHERE id=:id', vals)
            n_upd += 1
    c.commit()
    c.close()
    if verbose:
        print('[record] 信号日 %s：新增 %d 条，更新 %d 条' % (ok[0]['date'], n_new, n_upd))
    return 0


# ---------------- backfill（本年历史回补） ----------------
def cmd_backfill(verbose=True):
    """用 .cache/wide_ens/ms/<code>_lgb_tri_d<depth>_T<h>.csv 的 (score, ret) 回补。

    **阈值和 pred_hit 都用扩窗**：每个信号日只用「该日之前」的样本重新选档
    （与周频台账 walk-forward 同口径），否则回补出来的胜率会虚高。
    """
    import signal_card as SC

    c = conn()
    init_db(c)
    year = datetime.datetime.now().year
    y_start = '%d-01-01' % year
    n_new = 0
    for code, name, h, depth, thr, cap, note in SC.CFG:
        p = os.path.join(MS_DIR, '%s_lgb_tri_d%d_T%d.csv' % (code, depth, h))
        if not os.path.exists(p):
            if verbose:
                print('  ⚠️ 缺 %s' % os.path.basename(p))
            continue
        recs = []
        with open(p, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    d = row['trade_date']
                    sc = float(row['score'])
                    rt = float(row['ret'])
                except (KeyError, TypeError, ValueError):
                    continue
                recs.append((d, sc, rt))
        recs.sort()
        # ⚠️ 阈值用卡片里的固定门槛（与每日实盘同口径，回补行与实盘行可直接相加）；
        #    只有 pred_hit 用扩窗（只用该日之前已实现的样本）→ 自报胜率无后视。
        #    历史池从 2019 起算，所以年初几个月也有 pred_hit，不会漏记录。
        hist = []          # [(score, ret)] 该日之前已实现的样本（扩窗用）
        for d, sc, rt in recs:
            pool = [(s, r) for s, r in hist if abs(s) >= thr]
            pool_n = len(pool)
            if pool_n >= MIN_HIST:
                pred_hit = sum(1 for s, r in pool if r * (1 if s > 0 else -1) > 0) / pool_n
            elif len(hist) >= MIN_HIST:
                pool_n = len(hist)
                pred_hit = sum(1 for s, r in hist if r * (1 if s > 0 else -1) > 0) / pool_n
            else:
                pred_hit = None
            hist.append((sc, rt))          # 当日结果在此之后才入池

            if d < max(y_start, BACKFILL_FROM) or pred_hit is None:
                continue
            reliable, _ = band_flags(code, depth, h, thr)
            if sc >= thr:
                act, dirn = '看多', 1
            elif sc <= -thr:
                act, dirn = '看空', -1
            else:
                act, dirn = '观望', 0
            res = ('命中' if rt * dirn > 0 else '未命中') if dirn else '观望不计'
            # ⚠️ signal_card.pos_from_hit 返回 (仓位, 档位文字) 二元组，不是数字
            pos = float(SC.pos_from_hit(pred_hit, cap)[0]) if (dirn > 0 and reliable) else 0.0
            dir_hit = (1 if rt * (1 if sc > 0 else -1) > 0 else 0) if abs(rt) > 1e-12 else None
            cur = c.execute('SELECT id FROM daily_ledger WHERE sig_date=? AND code=? AND h=?',
                            (d, code, h)).fetchone()
            if cur:
                continue
            c.execute(
                'INSERT INTO daily_ledger (sig_date,code,name,h,period,act,dirn,score,thr,'
                'pred_hit,pred_band,oos_n,reliable,pos,base_close,target_close,actual_ret,result,'
                'dir_hit,settled_at,created_at,src) VALUES (:sig_date,:code,:name,:h,:period,'
                ':act,:dirn,:score,:thr,:pred_hit,:pred_band,:oos_n,:reliable,:pos,:base_close,'
                ':target_close,:actual_ret,:result,:dir_hit,:settled_at,:created_at,:src)',
                dict(sig_date=d, code=code, name=name, h=h, period='T+%d' % h, act=act,
                     dirn=dirn, score=round(sc, 6), thr=thr,
                     pred_hit=round(pred_hit, 4), pred_band='th%.2f' % thr,
                     oos_n=pool_n, reliable=reliable, pos=pos,
                     base_close=None, target_close=None, actual_ret=round(rt, 3), result=res,
                     dir_hit=dir_hit,
                     settled_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                     created_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                     src='backfill'))
            n_new += 1
    c.commit()
    c.close()
    if verbose:
        print('[backfill] 回补 %d 条（%d 年起，pred_hit 用扩窗，无后视）' % (n_new, year))
    return 0


# ---------------- settle ----------------
def cmd_settle(verbose=True):
    c = conn()
    init_db(c)
    rows = c.execute("SELECT * FROM daily_ledger WHERE result='待结算'").fetchall()
    done = pend = 0
    for r in rows:
        closes = load_close(r['code'])
        tgt = r['target_date'] or target_date_for(r['code'], r['sig_date'], r['h'])
        if not tgt:
            pend += 1
            continue
        px = closes.get(tgt)
        if px is None:
            pend += 1
            continue
        base = r['base_close']
        if not base:
            ds = [d for d in trading_days(r['code']) if d <= r['sig_date']]
            base = closes.get(ds[-1]) if ds else None
        if not base:
            pend += 1
            continue
        ret = (px / base - 1.0) * 100.0
        dirn = r['dirn'] or 0
        if dirn > 0:
            res = '命中' if ret > 0 else '未命中'
        elif dirn < 0:
            res = '命中' if ret < 0 else '未命中'
        else:
            res = '观望不计'
        sc = r['score']
        dir_hit = None
        if sc is not None and abs(sc) > 1e-12 and abs(ret) > 1e-12:
            dir_hit = 1 if ret * (1 if sc > 0 else -1) > 0 else 0
        c.execute('UPDATE daily_ledger SET target_date=?, target_close=?, actual_ret=?, result=?, '
                  'dir_hit=?, settled_at=?, base_close=? WHERE id=?',
                  (tgt, px, round(ret, 3), res, dir_hit,
                   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), base, r['id']))
        done += 1
    c.commit()
    left = c.execute("SELECT COUNT(*) n FROM daily_ledger WHERE result='待结算'").fetchone()['n']
    c.close()
    if verbose:
        print('[settle] 本期结算 %d 条，仍待结算 %d 条' % (done, left))
    return 0


# ---------------- stats ----------------
def walk_forward_stats(year=None):
    """最严格口径交叉验证：阈值也滚动重选（每日只用该日之前的样本挑档）。

    用来核对「固定门槛回补」有没有被后视抬高。慢（5 指数 × 每年约 2000 次选档），
    只在需要时调用，不挂在每日流程里。
    返回 {code: dict(n, hit, base, dir_avg, thr_dist)}
    """
    import signal_card as SC

    year = year or datetime.datetime.now().year
    y0 = '%d-01-01' % year
    out = {}
    for code, name, h, depth, _thr, cap, note in SC.CFG:
        p = os.path.join(MS_DIR, '%s_lgb_tri_d%d_T%d.csv' % (code, depth, h))
        if not os.path.exists(p):
            continue
        recs = []
        with open(p, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    recs.append((row['trade_date'], float(row['score']), float(row['ret'])))
                except (KeyError, TypeError, ValueError):
                    continue
        recs.sort()
        hist, hits, base, dirr, thrs, n, ntrig = [], 0, 0, 0.0, [], 0, 0
        for d, sc, rt in recs:
            picked = _pick_thr_walk(hist)
            hist.append((sc, rt))
            if not picked or d < y0:
                continue
            band, _rel = picked
            thr = band['th']
            dirn = 1 if sc >= thr else (-1 if sc <= -thr else 0)
            n += 1
            thrs.append(thr)
            if rt > 0:
                base += 1
            if dirn:
                ntrig += 1
                if rt * dirn > 0:
                    hits += 1
                dirr += rt * dirn
        if n:
            out[name] = dict(n=n, ntrig=ntrig, hit=(hits / ntrig if ntrig else 0.0),
                             base=base / n, dir_avg=dirr / n,
                             thr_med=sorted(thrs)[len(thrs) // 2])
    return out


def _agg(rows):
    n = len(rows)
    if n == 0:
        return dict(n=0, hit=0.0, avg=0.0, dir_avg=0.0, pred=0.0, base=0.0,
                    nlong=0, hitlong=0.0, nshort=0, hitshort=0.0)
    hit = sum(1 for r in rows if r['result'] == '命中')
    avg = sum(r['actual_ret'] or 0 for r in rows) / n
    # 方向调整后收益：看多取 r，看空取 -r，观望 0（这才是这套信号真正赚到的）
    dir_avg = sum((r['actual_ret'] or 0) * (r['dirn'] or 0) for r in rows) / n
    pr = [r['pred_hit'] for r in rows if r['pred_hit'] is not None]
    pred = sum(pr) / len(pr) if pr else 0.0
    base = sum(1 for r in rows if (r['actual_ret'] or 0) > 0) / n
    longs = [r for r in rows if (r['dirn'] or 0) > 0]
    shorts = [r for r in rows if (r['dirn'] or 0) < 0]
    hitlong = (sum(1 for r in longs if r['result'] == '命中') / len(longs)) if longs else 0.0
    hitshort = (sum(1 for r in shorts if r['result'] == '命中') / len(shorts)) if shorts else 0.0
    return dict(n=n, hit=hit / n, avg=avg, dir_avg=dir_avg, pred=pred, base=base,
                nlong=len(longs), hitlong=hitlong, nshort=len(shorts), hitshort=hitshort)


def stats(year=None):
    c = conn()
    init_db(c)
    year = year or datetime.datetime.now().year
    y0 = '%d-01-01' % year
    all_rows = c.execute('SELECT * FROM daily_ledger ORDER BY sig_date, code').fetchall()
    c.close()
    ytd = [r for r in all_rows if r['sig_date'] >= y0]
    settled = [r for r in ytd if r['result'] in ('命中', '未命中')]
    out = dict(year=year, ytd_n=len(ytd), settled_n=len(settled),
               live_n=len([r for r in settled if (r['src'] or 'live') == 'live']),
               pending=len([r for r in ytd if r['result'] == '待结算']),
               first_sig=min((r['sig_date'] for r in all_rows), default=None),
               all_n=len(all_rows), periods={}, idx={}, recent=[])

    for h in sorted({r['h'] for r in settled} | {1, 5}):
        sub = [r for r in settled if r['h'] == h]
        if sub:
            out['periods']['T+%d' % h] = _agg(sub)
    out['periods']['合计'] = _agg(settled)

    for name in sorted({r['name'] for r in settled}):
        sub = [r for r in settled if r['name'] == name]
        if sub:
            out['idx'][name] = _agg(sub)

    out['recent'] = [dict(d=r['sig_date'], name=r['name'], period=r['period'], act=r['act'],
                          ret=r['actual_ret'], res=r['result'], src=r['src'])
                     for r in settled[-6:]]
    return out


def fmt_stats(s):
    L = ['本年 %d 年 · 已结算 %d 条（实盘 %d / 回补 %d，待结算 %d）；台账共 %d 条，起点 %s'
         % (s['year'], s['settled_n'], s['live_n'], s['settled_n'] - s['live_n'],
            s['pending'], s['all_n'], s['first_sig'] or '—')]
    for k in ('T+1', 'T+5', '合计'):
        a = s['periods'].get(k)
        if a and a['n']:
            L.append('  %-4s n=%-3d 命中 %5.1f%%  基准上涨 %5.1f%%  预测均值 %5.1f%%  '
                     '方向收益 %+.2f%%'
                     % (k, a['n'], a['hit'] * 100, a['base'] * 100, a['pred'] * 100, a['dir_avg']))
    for k, a in s['idx'].items():
        L.append('   %-10s n=%-3d 命中 %5.1f%%  基准 %5.1f%%  方向收益 %+.2f%%'
                 % (k, a['n'], a['hit'] * 100, a['base'] * 100, a['dir_avg']))
    for r in s['recent']:
        L.append('   %s %-8s %-4s %-4s %+6.2f%%  %s' % (r['d'], r['name'], r['period'],
                                                        r['act'], r['ret'] or 0, r['res']))
    return '\n'.join(L)


# ---------------- 卡片 markdown ----------------
def build_md():
    """signal_card.py 用：返回 (kind, payload) 块列表"""
    s = stats()
    blocks = []
    if s['settled_n'] == 0:
        blocks.append(('md', '**⑤ 今年预测准确度（日频台账）**'))
        blocks.append(('md', '<font color=\'grey\'>台账已建（%d 条待结算，起点 %s）。'
                             '每交易日收盘入表，到 T+1 / T+5 自动用真实收盘结算，'
                             '结算后本节自动出现数字。</font>' % (s['pending'], s['first_sig'] or '—')))
        return blocks

    blocks.append(('md', '**⑤ 今年预测准确度（日频台账 · %d 年起）**' % s['year']))
    rows = [['周期', '已结算', '命中率', '同期基准上涨率', '方向收益']]
    for k in [x for x in s['periods'] if x != '合计'] + ['合计']:
        a = s['periods'].get(k) or {}
        if not a.get('n'):
            continue
        rows.append([k, str(a['n']), '**%.1f%%**' % (a['hit'] * 100),
                     '%.1f%%' % (a['base'] * 100), '%+.2f%%' % a['dir_avg']])
    blocks.append(('row', (rows, [1, 1, 1, 2, 1])))

    rows = [['周期', '看多 n / 命中', '看空 n / 命中']]
    for k in [x for x in s['periods'] if x != '合计']:
        a = s['periods'].get(k) or {}
        if not a.get('n'):
            continue
        rows.append([k,
                     '%d / **%.1f%%**' % (a['nlong'], a['hitlong'] * 100),
                     '%d / **%.1f%%**' % (a['nshort'], a['hitshort'] * 100)])
    if len(rows) > 1:
        blocks.append(('row', (rows, [2, 2, 2])))

    rows = [['周期', '模型预测值', '实际命中', '差值']]
    for k in [x for x in s['periods'] if x != '合计']:
        a = s['periods'].get(k) or {}
        if not a.get('n'):
            continue
        rows.append([k, '%.1f%%' % (a['pred'] * 100), '%.1f%%' % (a['hit'] * 100),
                     '%+.1fpp' % ((a['hit'] - a['pred']) * 100)])
    if len(rows) > 1:
        blocks.append(('row', (rows, [1, 2, 2, 2])))

    if s['idx']:
        parts = ['%s %.0f%%(n=%d)' % (k, v['hit'] * 100, v['n']) for k, v in s['idx'].items()]
        blocks.append(('md', '<font color=\'grey\'>分指数：%s</font>' % '、'.join(parts)))

    # 最严格口径交叉验证：阈值也滚动重选（每日只用历史挑档）
    try:
        wf = walk_forward_stats()
        if wf:
            tot_n = sum(v['n'] for v in wf.values())
            tot_trig = sum(v['ntrig'] for v in wf.values())
            tot_hit = sum(v['hit'] * v['ntrig'] for v in wf.values())
            if tot_trig:
                blocks.append(('md', '<font color=\'grey\'>交叉验证（最严口径：连阈值都每天只用历史重选）：'
                                     '触发 %d 次 / 命中 **%.1f%%** —— 与上表接近，说明固定门槛没被后视抬高。</font>'
                                     % (tot_trig, tot_hit / tot_trig * 100)))
    except Exception:
        pass

    if s['recent']:
        rows = [['信号日', '指数', '周期', '观点', '实际', '结果']]
        for r in s['recent'][-3:]:
            rows.append([r['d'][5:], r['name'], r['period'], r['act'],
                         '%+.2f%%' % (r['ret'] or 0), r['res']])
        blocks.append(('row', (rows, [1, 2, 1, 1, 1, 1])))

    blocks.append(('md', '<font color=\'grey\'>「命中率」= 方向命中率；**「同期基准上涨率」= 同样本里指数'
                         '本来就上涨的比例**——看多命中率不跑赢这列就等于没能力。'
                         '「方向收益」= 看多取涨、看空取跌后的平均（未扣成本），这才是真赚到的。'
                         '「模型预测值」= 发出时模型自报的同档历史胜率，与实际命中比对可校验置信度是否诚实。'
                         '台账 %s 起（历史回补档位/门槛无后视），实盘自 2026-10-16 起每交易日记录。'
                         '%d 条待结算。</font>' % (s['first_sig'] or '—', s['pending'])))
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--init', action='store_true')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--settle', action='store_true')
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--md', action='store_true')
    ap.add_argument('--year', type=int, default=None)
    a = ap.parse_args()

    if not any([a.init, a.record, a.settle, a.backfill, a.stats, a.md]):
        a.stats = True
    if a.init:
        c = conn(); init_db(c); c.close()
        print('[init] 基准表就绪：%s' % DB)
    if a.backfill:
        cmd_backfill()
    if a.settle:
        cmd_settle()
    if a.record:
        cmd_record()
    if a.stats:
        print(fmt_stats(stats(a.year)))
    if a.md:
        for k, v in build_md():
            print(k, v)
    return 0


if __name__ == '__main__':
    sys.exit(main())
