#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念映射模块（纯程序，零 Token）
=================================
数据源：TuShare 同花顺概念指数
  ths_index(exchange='A', type='N')  → 395 个概念指数
  ths_member(con_code='600887.SH')   → 反查个股所属概念

过滤规则（只留「真概念」，剔掉宽泛指数与指数成分）：
  1. type == 'N'
  2. 5 <= 成分股数 <= 300
  3. 名称不含：样本股 / 成份股 / 指数 / 全A / 融资融券 / 沪深港通 / 境外指数

缓存：cache/concept_map_YYYYMMDD.pkl，默认 7 天有效
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
import os
import sys
import time
import pickle
from datetime import datetime, timedelta

import pandas as pd
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()


DATA_HUB_PATH = _HUB_ROOT
sys.path.insert(0, DATA_HUB_PATH)

BASE = (os.environ.get('CASE_WORKSPACE') or _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
CACHE_DIR = os.path.join(BASE, 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)

MIN_COUNT = 5
MAX_COUNT = 300
BLACK_WORDS = ['样本股', '成份股', '成分股', '指数', '全A', '融资融券',
               '沪股通', '深股通', '陆股通', '港股通', 'MSCI', '富时', '标普',
               '证金持股', '同花顺']


def _log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def to_ts_code(code6):
    """6 位代码 → TuShare 带后缀代码"""
    c = str(code6).strip().zfill(6)
    if c[0] == '6':
        return c + '.SH'
    if c[0] in ('0', '3'):
        return c + '.SZ'
    if c[0] in ('4', '8'):
        return c + '.BJ'
    return c + '.SZ'


def _cache_path():
    return os.path.join(CACHE_DIR, 'concept_map_%s.pkl' % datetime.now().strftime('%Y%m%d'))


def _load_recent_cache(max_days=7):
    """找最近 max_days 内的缓存"""
    if not os.path.isdir(CACHE_DIR):
        return None
    best, best_day = None, None
    today = datetime.now()
    for fn in os.listdir(CACHE_DIR):
        if not (fn.startswith('concept_map_') and fn.endswith('.pkl')):
            continue
        try:
            day = datetime.strptime(fn[len('concept_map_'):-4], '%Y%m%d')
        except ValueError:
            continue
        if (today - day).days > max_days:
            continue
        if best_day is None or day > best_day:
            best, best_day = os.path.join(CACHE_DIR, fn), day
    if best is None:
        return None
    with open(best, 'rb') as f:
        return pickle.load(f)


def build_concept_index(pro, refresh=False):
    """返回 (idx_map, valid_concepts)
    idx_map: {ts_code: {'name':..., 'count':...}}
    valid_concepts: set(ts_code)  通过过滤的概念
    """
    fp = os.path.join(CACHE_DIR, 'ths_index_concept.pkl')
    idx = None
    if os.path.exists(fp) and not refresh:
        try:
            idx = pd.read_pickle(fp)
        except Exception:
            idx = None
    if idx is None:
        idx = pro.ths_index(exchange='A', type='N')
        idx.to_pickle(fp)

    idx_map, valid = {}, set()
    for _, r in idx.iterrows():
        ts, nm = r['ts_code'], str(r['name'])
        cnt = r['count'] if r['count'] == r['count'] else 0
        idx_map[ts] = {'name': nm, 'count': int(cnt)}
        if not (MIN_COUNT <= cnt <= MAX_COUNT):
            continue
        if any(w in nm for w in BLACK_WORDS):
            continue
        valid.add(ts)
    return idx_map, valid


def get_stock_concepts(codes, refresh=False, sleep=0.0):
    """
    codes: 6 位代码可迭代
    返回 {code6: [(concept_name, count), ...]}  按成分股数升序（越窄越具体）
    """
    cache = None if refresh else _load_recent_cache()
    if cache is None:
        cache = {}

    todo = [c for c in codes if str(c).zfill(6) not in cache]
    if not todo:
        _log('概念映射全部命中缓存（%d 只）' % len(cache))
        return {str(c).zfill(6): cache.get(str(c).zfill(6), []) for c in codes}

    from data_hub.tushare_config import get_tushare_pro
    pro = get_tushare_pro()
    idx_map, valid = build_concept_index(pro, refresh=refresh)
    _log('概念池：%d 个通过过滤（总 %d）' % (len(valid), len(idx_map)))

    t0 = time.time()
    for i, c in enumerate(todo, 1):
        c6 = str(c).zfill(6)
        try:
            m = pro.ths_member(con_code=to_ts_code(c6))
        except Exception as e:
            _log('  %s 失败: %s' % (c6, e))
            cache[c6] = []
            continue
        items = []
        if m is not None and len(m):
            for ts in m['ts_code']:
                if ts not in valid:
                    continue
                meta = idx_map.get(ts)
                if not meta:
                    continue
                items.append((meta['name'], meta['count']))
        items.sort(key=lambda x: x[1])
        cache[c6] = items
        if sleep:
            time.sleep(sleep)
        if i % 20 == 0 or i == len(todo):
            el = time.time() - t0
            _log('  %d/%d  %.0fs' % (i, len(todo), el))

    with open(_cache_path(), 'wb') as f:
        pickle.dump(cache, f)
    return {str(c).zfill(6): cache.get(str(c).zfill(6), []) for c in codes}


if __name__ == '__main__':
    test = ['600887', '601088', '002142', '600256']
    res = get_stock_concepts(test)
    for k, v in res.items():
        print('%s: %s' % (k, '、'.join('%s(%d)' % (n, c) for n, c in v) or '（无）'))
