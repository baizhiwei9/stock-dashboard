#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报预测台账 · 基准表（SQLite）——记录每次预测，事后结算，滚动统计当年准确度

大佬 2026-10-19 需求：
  「在数据库里建一个表（基准表），每一周对一下子，看我预测的胜率对不对。
    从今天开始算，一直统计历史胜率，统计当年预测的准确度，
    每次报告里带上当年开始统计的准确度。」

设计：
  · 表 forecast_ledger（基准表）：一条 = 某指数 × 某周期 × 某信号日的一次预测
  · record  ：周报生成后把 5 指数 × 2 周期（T+1/T+5）的预测写进表（幂等，可重跑）
  · settle  ：T+1 / T+5 到期后用真实收盘价结算 → 命中 / 未命中
  · stats   ：按「本年」聚合：方向命中率、平均涨跌、预测置信度 vs 实际命中率（校准）
  · --md    ：输出卡片用的一段 markdown（周报里自动带上）

口径（与校准表完全一致，避免自欺）：
  · 命中定义：看多为 r>0，看空为 r<0（r = 目标日收盘 / 信号日收盘 - 1）
  · 观望行不计入方向命中率，但记录 score 符号是否正确（dir_hit），供参考
  · 信号日 = 周报里模型的 date（通常周五收盘）；T+1 = 之后第 1 个交易日收盘，
    T+5 = 之后第 5 个交易日收盘。交易日历取自各指数自己的日线文件。

用法：
  python3 bin/wide_ens/weekly_ledger.py --init
  python3 bin/wide_ens/weekly_ledger.py --record          # 记录最新一期预测
  python3 bin/wide_ens/weekly_ledger.py --settle          # 结算到期预测
  python3 bin/wide_ens/weekly_ledger.py --stats           # 打印统计
  python3 bin/wide_ens/weekly_ledger.py --md              # 卡片用 markdown
"""
import os
import sys
import csv
import sqlite3
import argparse
import datetime
import warnings

warnings.filterwarnings('ignore')

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RAW = os.path.join(WS, '.cache/weekly_forecast/raw')
DB = os.path.join(WS, '.cache/wide_ens/weekly_ledger.db')
FILES = {'000300_SH': 'idx_hs300.csv', '000688_SH': 'idx_kc50.csv',
         '000852_SH': 'idx_zz1000.csv', '000905_SH': 'idx_zz500.csv',
         '399006_SZ': 'idx_cyb.csv'}

DDL = """
CREATE TABLE IF NOT EXISTS forecast_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sig_date      TEXT NOT NULL,          -- 信号日（预测所依据的收盘日）
  code          TEXT NOT NULL,
  name          TEXT NOT NULL,
  h             INTEGER NOT NULL,       -- 1 / 5
  period        TEXT NOT NULL,          -- T+1 / T+5
  act           TEXT NOT NULL,          -- 看多 / 看空 / 观望
  dirn          INTEGER,                -- 1 / -1 / 0
  score         REAL,                   -- 模型分 P(涨)-P(跌)
  thr           REAL,                   -- 采用的档位门槛
  pred_hit      REAL,                   -- 发出时模型给的同档历史胜率
  pred_band     TEXT,                   -- th0.30 这种
  reliable      INTEGER,                -- 该周期统计是否达标
  pos           REAL,                   -- 当时建议仓位
  base_close    REAL,                   -- 信号日收盘（基准价）
  target_date   TEXT,                   -- 应结算的交易日
  target_close  REAL,
  actual_ret    REAL,                   -- 区间涨跌（%）
  result        TEXT,                   -- 命中 / 未命中 / 观望不计 / 待结算
  dir_hit       INTEGER,                -- score 符号是否正确（含观望行）
  settled_at    TEXT,
  created_at    TEXT,
  src           TEXT DEFAULT 'live',    -- live=每周实盘记录 / backfill=历史回补
  UNIQUE(sig_date, code, h)
);
CREATE INDEX IF NOT EXISTS ix_ledger_sig ON forecast_ledger(sig_date);
CREATE INDEX IF NOT EXISTS ix_ledger_res ON forecast_ledger(result);
"""

VIEW = """
CREATE VIEW IF NOT EXISTS v_ledger_ytd AS
SELECT * FROM forecast_ledger
WHERE substr(sig_date, 1, 4) = strftime('%Y', 'now', '+8 hours');
"""


# ---------------- 基础 ----------------
def conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db(c):
    c.executescript(DDL)
    try:
        c.executescript(VIEW)
    except sqlite3.Error:
        pass
    # 老库补列
    cols = {r['name'] for r in c.execute('PRAGMA table_info(forecast_ledger)')}
    if 'src' not in cols:
        c.execute("ALTER TABLE forecast_ledger ADD COLUMN src TEXT DEFAULT 'live'")
    c.commit()


_cache_close = {}


def load_close(code):
    """{date_str: close}，date_str = YYYY-MM-DD"""
    if code in _cache_close:
        return _cache_close[code]
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
    _cache_close[code] = out
    return out


def trading_days(code):
    return sorted(load_close(code).keys())


def target_date_for(code, sig_date, h):
    """信号日之后第 h 个交易日"""
    ds = trading_days(code)
    if not ds:
        return None
    # 信号日取 <= sig_date 的最后一个交易日（防止周末/停牌错位）
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


# ---------------- record ----------------
def cmd_record(verbose=True):
    import weekly_forecast_card as W

    rows = W.build_rows()
    sig_dates = sorted({e.get('date') for r in rows for e in r['hs'].values() if e.get('date')})
    if not sig_dates:
        print('[record] 没有可用预测（缺文件？）')
        return 1
    sig = sig_dates[-1]

    c = conn()
    init_db(c)
    n_new = n_upd = 0
    for r in rows:
        for h in W.HS:
            e = r['hs'].get(h) or {}
            if e.get('missing') or not e.get('act'):
                continue
            code, name = r['code'], r['name']
            closes = load_close(code)
            base = closes.get(sig)
            if base is None:
                ds = [d for d in trading_days(code) if d <= sig]
                base = closes.get(ds[-1]) if ds else None
            tgt = target_date_for(code, sig, h)
            cur = c.execute(
                'SELECT id, result, target_close FROM forecast_ledger '
                'WHERE sig_date=? AND code=? AND h=?', (sig, code, h)).fetchone()
            vals = dict(
                sig_date=sig, code=code, name=name, h=h,
                period='T+%d' % h, act=e['act'], dirn=e.get('dirn', 0),
                score=e.get('score'), thr=e.get('thr'),
                pred_hit=e.get('hit'), pred_band='th%.2f' % e['thr'] if e.get('thr') is not None else None,
                reliable=1 if e.get('reliable') else 0, pos=e.get('pos') or 0.0,
                base_close=base, target_date=tgt, result='待结算',
                created_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            if cur is None:
                c.execute(
                    'INSERT INTO forecast_ledger (sig_date,code,name,h,period,act,dirn,score,thr,'
                    'pred_hit,pred_band,reliable,pos,base_close,target_date,result,created_at) '
                    'VALUES (:sig_date,:code,:name,:h,:period,:act,:dirn,:score,:thr,'
                    ':pred_hit,:pred_band,:reliable,:pos,:base_close,:target_date,:result,:created_at)',
                    vals)
                n_new += 1
            elif cur['result'] == '待结算':
                vals['id'] = cur['id']
                c.execute(
                    'UPDATE forecast_ledger SET act=:act, dirn=:dirn, score=:score, thr=:thr, '
                    'pred_hit=:pred_hit, pred_band=:pred_band, reliable=:reliable, pos=:pos, '
                    'base_close=:base_close, target_date=:target_date WHERE id=:id', vals)
                n_upd += 1
    c.commit()
    c.close()
    if verbose:
        print('[record] 信号日 %s：新增 %d 条，更新 %d 条（已结算的不动）' % (sig, n_new, n_upd))
    return 0


# ---------------- settle ----------------
# ---------------- backfill（历史回补） ----------------
BACKFILL_FROM = '2023-01-01'   # 与校准表 OOS 起点一致


def _weekly_last_days(code, dates):
    """从该指数全部交易日里，取每周（ISO 周）的最后一个交易日。

    周报是「周五收盘预测下周」，所以台账只认周频信号；
    回补也必须用同一口径，否则 T+1 会变成每天一条，和实盘不可比。
    """
    ds = trading_days(code)
    last = {}
    for d in ds:
        try:
            y, m, dd = (int(x) for x in d.split('-'))
            yw = datetime.date(y, m, dd).isocalendar()[:2]
        except ValueError:
            continue
        last[yw] = d
    keep = set(last.values())
    return {d for d in dates if d in keep}


def cmd_backfill(verbose=True):
    """把今年以来的「样本外预测序列」回补进基准表。

    关键：档位**不使用全期后视**，而是每个信号日只用「当时之前」的数据选档
    （扩窗 walk-forward），否则回补出来的胜率会虚高。
    """
    import weekly_forecast_card as W

    c = conn()
    init_db(c)
    now_year = datetime.datetime.now().year
    y_start = '%d-01-01' % now_year
    n_new = 0
    for code, name, depth in W.IDX:
        cap = W.CAP[name]
        for h in W.HS:
            p = os.path.join(W.MS_DIR, '%s_lgb_tri_d%d_T%d.csv' % (code, depth, h))
            if not os.path.exists(p):
                if verbose:
                    print('  ⚠️ 缺 %s' % p)
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
                    if d < BACKFILL_FROM:
                        continue
                    recs.append((d, sc, rt))
            recs.sort()
            # T+1 序列是逐日的，只保留每周最后一个交易日（周五收盘）→ 与实盘同口径
            # T+5 序列本身已是每 5 个交易日一行，不再筛
            if h == 1:
                wk = _weekly_last_days(code, [d for d, _, _ in recs])
                recs = [r for r in recs if r[0] in wk]
            for i, (d, sc, rt) in enumerate(recs):
                if d < y_start:
                    continue
                # 已入表的直接跳过（省掉昂贵的 walk-forward 选档）
                if c.execute('SELECT 1 FROM forecast_ledger WHERE sig_date=? AND code=? AND h=?',
                             (d, code, h)).fetchone():
                    continue
                # 只用 d 之前的数据选档
                hist = [(s, r) for dd, s, r in recs[:i]]
                band = _pick_band_walk(hist)
                if not band:
                    continue
                thr = band['th']
                if sc >= thr:
                    act, dirn = '看多', 1
                elif sc <= -thr:
                    act, dirn = '看空', -1
                else:
                    act, dirn = '观望', 0
                res = ('命中' if rt * dirn > 0 else '未命中') if dirn else '观望不计'
                pos = (W.pos_from_hit(band['hit'], cap)
                       if (dirn > 0 and band['reliable']) else 0.0)
                dir_hit = (1 if rt * (1 if sc > 0 else -1) > 0 else 0) if abs(rt) > 1e-12 else None
                vals = dict(sig_date=d, code=code, name=name, h=h, period='T+%d' % h,
                            act=act, dirn=dirn, score=round(sc, 6), thr=thr,
                            pred_hit=band['hit'], pred_band='th%.2f' % thr,
                            reliable=1 if band['reliable'] else 0, pos=pos,
                            base_close=None, target_close=None,
                            actual_ret=round(rt, 3), result=res, dir_hit=dir_hit,
                            settled_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            created_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            src='backfill')
                c.execute(
                    'INSERT INTO forecast_ledger (sig_date,code,name,h,period,act,dirn,score,thr,'
                    'pred_hit,pred_band,reliable,pos,base_close,target_close,actual_ret,result,'
                    'dir_hit,settled_at,created_at,src) VALUES (:sig_date,:code,:name,:h,:period,'
                    ':act,:dirn,:score,:thr,:pred_hit,:pred_band,:reliable,:pos,:base_close,'
                    ':target_close,:actual_ret,:result,:dir_hit,:settled_at,:created_at,:src)', vals)
                n_new += 1
    c.commit()
    c.close()
    if verbose:
        print('[backfill] 回补 %d 条（%d 年起，档位用扩窗 walk-forward，无后视）' % (n_new, now_year))
    return 0


def _pick_band_walk(hist):
    """hist=[(score, ret)]，只用这段历史挑档（与卡片硬门槛一致）"""
    import weekly_forecast_card as W
    if len(hist) < W.MIN_N:
        return None
    parsed = []
    for th in (0.0, 0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70):
        sub = [(s, r) for s, r in hist if abs(s) >= th]
        n = len(sub)
        if n < W.MIN_N:
            continue
        w = sum(1 for s, r in sub if r * (1 if s > 0 else -1) > 0)
        hit = w / n
        try:
            from scipy.stats import binomtest
            p = binomtest(w, n, 0.5, alternative='greater').pvalue
        except Exception:
            p = 1.0
        raw = sum(r for s, r in sub) / n
        parsed.append(dict(th=th, n=n, hit=hit, p=p, raw=raw))
    if not parsed:
        return None
    parsed.sort(key=lambda x: x['th'])
    ok = [x for x in parsed
          if x['th'] >= W.MIN_TH and x['hit'] >= W.MIN_HIT
          and x['p'] <= W.MAX_P and x['raw'] > 0]
    if ok:
        r = dict(ok[0]); r['reliable'] = True
        return r
    pool = [x for x in parsed if x['th'] >= W.MIN_TH] or parsed
    best = max(pool, key=lambda x: x['hit'])
    r = dict(best); r['reliable'] = False
    return r


def cmd_settle(verbose=True):
    c = conn()
    init_db(c)
    rows = c.execute("SELECT * FROM forecast_ledger WHERE result='待结算'").fetchall()
    done = pend = 0
    for r in rows:
        closes = load_close(r['code'])
        tgt = r['target_date'] or target_date_for(r['code'], r['sig_date'], r['h'])
        if not tgt:
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
        dir_hit = None
        sc = r['score']
        if sc is not None and abs(sc) > 1e-12 and abs(ret) > 1e-12:
            dir_hit = 1 if (ret * (1 if sc > 0 else -1)) > 0 else 0
        c.execute('UPDATE forecast_ledger SET target_date=?, target_close=?, actual_ret=?, '
                  'result=?, dir_hit=?, settled_at=?, base_close=? WHERE id=?',
                  (tgt, px, round(ret, 3), res, dir_hit,
                   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), base, r['id']))
        done += 1
    c.commit()
    left = c.execute("SELECT COUNT(*) n FROM forecast_ledger WHERE result='待结算'").fetchone()['n']
    c.close()
    if verbose:
        print('[settle] 本期结算 %d 条，仍待结算 %d 条' % (done, left))
    return 0


# ---------------- stats ----------------
def _agg(rows):
    n = len(rows)
    if n == 0:
        return dict(n=0, hit=0, avg=0.0, pred=0.0, cum=0.0, base=0.0, nlong=0, hitlong=0,
                    nshort=0, hitshort=0)
    hit = sum(1 for r in rows if r['result'] == '命中')
    avg = sum(r['actual_ret'] or 0 for r in rows) / n
    pred = sum(r['pred_hit'] or 0 for r in rows) / n
    base = sum(1 for r in rows if (r['actual_ret'] or 0) > 0) / n
    longs = [r for r in rows if (r['dirn'] or 0) > 0]
    shorts = [r for r in rows if (r['dirn'] or 0) < 0]
    hitlong = (sum(1 for r in longs if r['result'] == '命中') / len(longs)) if longs else 0.0
    hitshort = (sum(1 for r in shorts if r['result'] == '命中') / len(shorts)) if shorts else 0.0
    # 按仓位加权的累计收益（仓位=0 记为 0，观望行按 score 方向不下单 → 0）
    cum = sum(((r['actual_ret'] or 0) * (r['pos'] or 0) *
               (1 if (r['dirn'] or 0) > 0 else -1 if (r['dirn'] or 0) < 0 else 0)) / 100.0
              for r in rows)
    return dict(n=n, hit=hit / n, avg=avg, pred=pred, cum=cum * 100.0, base=base,
                nlong=len(longs), hitlong=hitlong, nshort=len(shorts), hitshort=hitshort)


def stats(year=None):
    c = conn()
    init_db(c)
    now = datetime.datetime.now()
    year = year or now.year
    y0 = '%d-01-01' % year
    all_rows = c.execute('SELECT * FROM forecast_ledger ORDER BY sig_date, code, h').fetchall()
    ytd = [r for r in all_rows if r['sig_date'] >= y0]
    c.close()

    settled = [r for r in ytd if r['result'] in ('命中', '未命中')]
    live = [r for r in settled if (r['src'] or 'live') == 'live']
    out = dict(year=year, ytd_n=len(ytd), settled_n=len(settled),
               live_n=len(live), backfill_n=len(settled) - len(live),
               first_sig=min((r['sig_date'] for r in all_rows), default=None),
               all_n=len(all_rows), periods={}, idx={}, calib=[], recent=[])

    for h in (1, 5):
        sub = [r for r in settled if r['h'] == h]
        out['periods'][('T+%d' % h)] = _agg(sub)
    out['periods']['合计'] = _agg(settled)
    if live:
        out['periods']['实盘合计'] = _agg(live)

    for name in sorted({r['name'] for r in settled}):
        for h in (1, 5):
            sub = [r for r in settled if r['name'] == name and r['h'] == h]
            if sub:
                out['idx']['%s|T+%d' % (name, h)] = _agg(sub)

    # 校准：模型说 X% 胜率，实际命中多少
    for lo, hi, lab in ((0, 0.60, '<60%（偏低）'), (0.60, 0.65, '60~65%'),
                        (0.65, 0.70, '65~70%'), (0.70, 1.01, '≥70%')):
        sub = [r for r in settled
               if r['pred_hit'] is not None and lo <= r['pred_hit'] < hi]
        if sub:
            a = _agg(sub)
            out['calib'].append(dict(label=lab, n=a['n'], pred=a['pred'], hit=a['hit']))

    out['recent'] = [dict(d=r['sig_date'], name=r['name'], period=r['period'],
                          act=r['act'], ret=r['actual_ret'], res=r['result'])
                     for r in settled[-8:]]
    return out


def fmt_stats(s):
    L = []
    L.append('本年 %d 年 · 已结算 %d 条（实盘 %d + 历史回补 %d）；台账共 %d 条，起点 %s'
             % (s['year'], s['settled_n'], s['live_n'], s['backfill_n'],
                s['all_n'], s['first_sig'] or '—'))
    for k in ('T+1', 'T+5', '合计', '实盘合计'):
        a = s['periods'].get(k) or {}
        if a.get('n'):
            L.append('  %-4s n=%-3d 命中率 %5.1f%%  基准上涨率 %5.1f%%  模型预测 %5.1f%%  '
                     '平均涨跌 %+.2f%%  看多 %d/%.0f%% 看空 %d/%.0f%%'
                     % (k, a['n'], a['hit'] * 100, a['base'] * 100, a['pred'] * 100, a['avg'],
                        a['nlong'], a['hitlong'] * 100, a['nshort'], a['hitshort'] * 100))
    for k, a in s['idx'].items():
        L.append('   %-16s n=%-3d 命中 %5.1f%%  平均 %+.2f%%'
                 % (k.replace('|', ' '), a['n'], a['hit'] * 100, a['avg']))
    for cb in s['calib']:
        L.append('   校准 %-10s n=%-3d 模型说 %.1f%% → 实际 %.1f%%'
                 % (cb['label'], cb['n'], cb['pred'] * 100, cb['hit'] * 100))
    for r in s['recent']:
        L.append('   %s %-8s %-4s %-4s %+6.2f%%  %s'
                 % (r['d'], r['name'], r['period'], r['act'], r['ret'] or 0, r['res']))
    return '\n'.join(L)


# ---------------- 卡片 markdown ----------------
def build_md():
    """给周报卡片用的「⑥ 今年预测准确度」段（表格行以 list 返回，便于 column_set）"""
    s = stats()
    blocks = []
    if s['settled_n'] == 0:
        blocks.append(('md', '**⑥ 今年预测准确度**'))
        blocks.append(('md', '<font color=\'grey\'>台账已开始记录（%d 条待结算，起点 %s）。'
                             'T+1 预测在下一个交易日收盘后结算、T+5 在 5 个交易日后结算，'
                             '所以本期数字要等结算才有——之后每周自动更新。</font>'
                             % (s['ytd_n'], s['first_sig'] or '—')))
        return blocks

    a = s['periods']['合计']
    blocks.append(('md', '**⑥ 今年预测准确度（%d 年起滚动统计）**' % s['year']))
    blocks.append(('row', ([['周期', '已结算', '命中率', '同期基准上涨率', '模型预测值']] +
                           [[k,
                             str((s['periods'].get(k) or {}).get('n', 0)),
                             '**%.1f%%**' % ((s['periods'].get(k) or {}).get('hit', 0) * 100),
                             '%.1f%%' % ((s['periods'].get(k) or {}).get('base', 0) * 100),
                             '%.1f%%' % ((s['periods'].get(k) or {}).get('pred', 0) * 100)]
                            for k in ('T+1', 'T+5', '合计')], [1, 1, 1, 1, 1])))
    # 多空拆分 —— 关键：看多的命中率不能直接和 50% 比，市场自己在涨
    pd_ = s['periods']
    t1, t5 = pd_.get('T+1') or {}, pd_.get('T+5') or {}
    blocks.append(('row', ([['周期', '看多 n / 命中', '看空 n / 命中']] +
                           [[k,
                             '%d / **%.1f%%**' % (v.get('nlong', 0), v.get('hitlong', 0) * 100),
                             '%d / **%.1f%%**' % (v.get('nshort', 0), v.get('hitshort', 0) * 100)]
                            for k, v in (('T+1', t1), ('T+5', t5))], [2, 2, 2])))
    blocks.append(('md', '<font color=\'grey\'>「命中率」= 方向命中率；**「同期基准上涨率」= 同期这些周里'
                         '指数本来就上涨的比例**——不跑赢这列，就等于没能力。'
                         '「模型预测值」= 发出时模型自报的同档历史胜率（用来校验置信度是否可信）。'
                         '台账起点 2026-01-08，前期是历史回补（档位按当时的扩窗数据选，无后视），'
                         '从 2026-10-16 起是每周实盘记录、到期自动结算。</font>'))

    # 各指数明细压成一行文字（卡片体积有限，表格只留总账）
    if s['idx']:
        parts = []
        for k, v in s['idx'].items():
            name, per = k.split('|')
            parts.append('%s%s %.0f%%(n=%d)' % (name, per, v['hit'] * 100, v['n']))
        blocks.append(('md', '<font color=\'grey\'>分指数：%s</font>' % '、'.join(parts)))

    if s['calib']:
        rows = [['模型说（历史胜率）', 'n', '实际命中', '差值']]
        for cb in s['calib']:
            rows.append([cb['label'], str(cb['n']), '%.1f%%' % (cb['hit'] * 100),
                         '%+.1fpp' % ((cb['hit'] - cb['pred']) * 100)])
        blocks.append(('row', (rows, [2, 1, 1, 1])))

    if s['recent']:
        rows = [['信号日', '指数', '周期', '观点', '实际', '结果']]
        for r in s['recent'][-3:]:
            rows.append([r['d'][5:], r['name'], r['period'], r['act'],
                         '%+.2f%%' % (r['ret'] or 0), r['res']])
        blocks.append(('row', (rows, [1, 2, 1, 1, 1, 1])))
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
