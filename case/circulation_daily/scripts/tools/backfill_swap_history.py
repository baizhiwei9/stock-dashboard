#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 cache.json 里的 swap_history 按**交易日白名单**回填进 market.db 的 swap_1y_history。

背景：2026-09-30 之后 circulation_daily.py 的 SQLite 落库环节静默失败，
但 cache['swap_history'] 一直有正常累积，导致 swap_1y_history 从 09-29 直接跳到 11-14，
中间 30 个交易日断档，MA5/MA10 与金叉死叉判定全部失真。

口径：
  - 交易日白名单取自 data_hub EDB 的 BOND.CN.NCD.AAA.1Y（真实银行间交易日）
  - cache 里落在非交易日的记录（周末/国庆假期沿用值）一律丢弃，不写库
  - cache 缺的交易日不插值、不猜测，保持空缺（后续可由当日实时抓取补齐）
  - 已存在的行不覆盖（除非 --overwrite）

用法：
  python3 backfill_swap_history.py            # 干跑，只报告
  python3 backfill_swap_history.py --apply    # 实际写库
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()
import argparse
import json
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAILY_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
MODULES_DIR = os.path.join(DAILY_DIR, 'scripts', 'modules')
sys.path.insert(0, MODULES_DIR)

CACHE_FILE = os.path.join(DAILY_DIR, 'scripts', 'cache.json')
DB_CANDIDATES = [
    os.path.join(DAILY_DIR, 'data', 'market.db'),
    _cp('/opt/coze/.fuse_drive/蓁宝/所有对话/主对话/环流日报/data/market.db'),
]
CAL_SID = 'BOND.CN.NCD.AAA.1Y'   # 交易日历参照序列


def find_db():
    for p in DB_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit('找不到 market.db')


def trading_days(limit=600):
    import edb_helper as E
    rows = E.rows(CAL_SID, limit)
    if not rows:
        raise SystemExit('EDB 交易日历取数失败，中止（不允许在无日历情况下回填）')
    return [r['date'] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写库')
    ap.add_argument('--overwrite', action='store_true', help='覆盖已有行')
    args = ap.parse_args()

    db = find_db()
    print(f'DB: {db}')

    cache = json.load(open(CACHE_FILE))
    sh = cache.get('swap_history') or {}
    print(f'cache swap_history: {len(sh)} 条  {min(sh)} ~ {max(sh)}')

    cal = set(trading_days())
    cal_lo, cal_hi = min(cal), max(cal)
    print(f'交易日历: {len(cal)} 天  {cal_lo} ~ {cal_hi}')

    conn = sqlite3.connect(db)
    have = {r[0] for r in conn.execute('select date from swap_1y_history')}
    print(f'库内现有: {len(have)} 条')

    todo, dropped_offcal = [], []
    for d, v in sorted(sh.items()):
        if v is None:
            continue
        if not (cal_lo <= d <= cal_hi):
            # 日历覆盖范围之外，不做交易日校验，仅按库里是否已有决定
            if d in have and not args.overwrite:
                continue
            todo.append((d, float(v)))
            continue
        if d not in cal:
            dropped_offcal.append((d, v))
            continue
        if d in have and not args.overwrite:
            continue
        todo.append((d, float(v)))

    print(f'\n可回填 {len(todo)} 条；丢弃非交易日 {len(dropped_offcal)} 条')
    if dropped_offcal:
        print('  非交易日样例:', dropped_offcal[:8])

    if todo:
        print('  回填区间:', todo[0][0], '~', todo[-1][0])
        # cache 缺的交易日（日历内、区间内、库和 cache 都没有）
        lo, hi = todo[0][0], todo[-1][0]
        miss = sorted(d for d in cal
                      if lo <= d <= hi and d not in sh and d not in have)
        print(f'  仍缺的交易日 {len(miss)} 个: {miss}')

    if not args.apply:
        print('\n[干跑] 加 --apply 实际写库')
        return

    n = 0
    for d, v in todo:
        conn.execute(
            'INSERT OR REPLACE INTO swap_1y_history '
            '(date, annualized_pct, pips, spot, source) VALUES (?, ?, NULL, NULL, ?)',
            (d, v, 'cache回填'),
        )
        n += 1
    conn.commit()
    total = conn.execute('select count(*) from swap_1y_history').fetchone()[0]
    print(f'\n写入 {n} 条，库内共 {total} 条')
    print('末尾 6 条:')
    for r in conn.execute('select date, annualized_pct, source from swap_1y_history '
                          'order by date desc limit 6'):
        print('  ', r)
    conn.close()


if __name__ == '__main__':
    main()
