#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场临界监测 C(t) & Gap —— 机会侧「行业下钻到个股」模块。

触发与定位
----------
当机会侧（cgap_opportunity）把某个宽泛方向（消费、酒、医药、银行、房地产、科技、军工）
判为「机会」档时，本模块沿申万 2021 三级体系逐级下钻：

    宽泛方向 → 一级行业（层内排序选胜出）→ 二级 → 三级 → 该三级行业成分股 → 个股名单

每层都给出全部候选的指标与排名，并只在胜出分支内继续下钻，最终到个股层给出逐只入选依据。

口径（全部可复算；不改动监测既有口径）
--------------------------------------
行业层级
  · 数据源：申万 2021 一级/二级/三级行业指数日线（DataHub 股票模块 sw_index_daily，源端最新可得日，
    可能滞后一个交易日，逐层标注 data_date）。
  · 层内指标：20 日收益、20 日超额（相对全A 中证全指 000985，取同日口径 20 日收益）、
    量能比（近 5 日平均成交量 / 前 20 日均值）、趋势分 = (收盘>MA20) + (MA20>MA60)。
  · 层内综合分 = 0.40×层内百分位(20 日超额) + 0.30×(趋势分/2) + 0.30×层内百分位(量能比)，
    与机会侧同一公式，百分位在「该层候选集合内」计算；胜出者 = 综合分最高。
个股层
  · 成分股来自 DataHub 股票模块（sw_industry 宽表，乐咕申万三级成分为主源）；
  · 量价：腾讯日线（与当日监测同源），指标与层内公式完全相同；
  · 估值/财务字段（市值、PE、股息率、净利增速、营收增速）直接取成分股表并标注报告期，
    不另做换算，也不作为打分输入。
结论为规则化排序，非预测模型，不构成买卖建议。任何取数失败如实记入 errors，不编造、不用旧值顶替。
"""
import os
import re
import sys
import json
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

WORK = os.path.dirname(os.path.abspath(__file__))
PY_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
if os.path.isdir(PY_DEPS) and PY_DEPS not in sys.path:
    sys.path.insert(0, PY_DEPS)

CACHE_DIR = os.path.join(WORK, 'drill_cache')
CACHE_TTL = 7200          # 秒；同日内重跑复用，源端更新后可用 --no-cache 强制刷新
W_RET = 20                # 动量窗口
W_FAST, W_SLOW = 20, 60   # 趋势均线
W_VS, W_VL = 5, 20        # 量能比窗口
TOP_STOCKS = 10           # 个股层展示名额

ALL_A_CODE = 'sh000985'   # 全A：中证全指，仅作相对强弱基准

# 宽泛方向 → 申万一级行业候选（本监测显式映射，非申万官方分组）
SEED_MAP = {
    '消费': ['农林牧渔', '食品饮料', '家用电器', '纺织服饰', '轻工制造',
             '商贸零售', '社会服务', '美容护理'],
    '酒': ['食品饮料'],
    '医药': ['医药生物'],
    '银行': ['银行'],
    '房地产': ['房地产'],
    '科技': ['电子', '计算机', '通信'],
    '军工': ['国防军工'],
}

# 机会侧 ETF → 宽泛方向（宽基与红利/红利低波不参与下钻）
ETF_DIRECTION = {
    '159928': '消费', '512690': '酒', '512010': '医药', '512800': '银行',
    '512200': '房地产', '512480': '科技', '512760': '科技', '515880': '科技',
    '515050': '科技', '512720': '科技', '512660': '军工',
}

METHOD = ('逐级下钻：宽泛方向 → 申万一级 → 二级 → 三级 → 该三级成分股；'
          '各层综合分 = 0.40×层内百分位(20日超额) + 0.30×(趋势分/2) + 0.30×层内百分位(量能比)，'
          '层内百分位在该层候选集合内计算；规则化排序、非预测模型、不构成买卖建议')


# ---------------------------------------------------------------- 取数


# ---- DataHub 股票模块（三级体系）：ETF/指数日线统一入口 ----
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_INDEX_SUFFIX = {
    '000001': '.SH', '000016': '.SH', '000300': '.SH', '000688': '.SH',
    '000905': '.SH', '000852': '.SH', '000985': '.CSI',
    '399001': '.SZ', '399006': '.SZ', '399106': '.SZ',
}


def _split_symbol(code):
    raw = str(code).strip().lower()
    prefix = raw[:2] if raw[:2] in ('sh', 'sz') else ''
    num = (raw[2:] if prefix else raw).split('.')[0]
    if num.startswith(('15', '16', '51', '52', '56', '58')):
        return 'etf', num
    if num in _INDEX_SUFFIX:
        return 'index', num + _INDEX_SUFFIX[num]
    return 'index', num + ('.SH' if (prefix == 'sh' or num.startswith('000')) else '.SZ')


def _hub_daily_rows(code, days):
    """DataHub 日线 -> (close Series, vol Series)，索引为 YYYY-MM-DD。"""
    kind, sym = _split_symbol(code)
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    if kind == 'etf':
        from data_hub.collectors.misc import get_etf_daily
        df = get_etf_daily(code=sym, start_date=start, end_date=end)
    else:
        from data_hub.collectors.stocks import get_index_daily
        df = get_index_daily(ts_code=sym, start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
    dates = (df[dcol].astype(str).str.replace('-', '')
             .map(lambda x: f'{x[:4]}-{x[4:6]}-{x[6:8]}'))
    close = pd.Series(pd.to_numeric(df['close'], errors='coerce').values, index=dates).sort_index()
    vcol = 'vol' if 'vol' in df.columns else ('volume' if 'volume' in df.columns else None)
    vol = (pd.Series(pd.to_numeric(df[vcol], errors='coerce').values, index=dates).sort_index()
           if vcol else pd.Series(index=close.index, dtype=float))
    return close.dropna(), vol


def _session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0'})
    return s


def fetch_tencent_kline(code, days=800, session=None):
    """DataHub 股票模块前复权日线：返回 (close Series, vol Series)，索引 YYYY-MM-DD。"""
    try:
        return _hub_daily_rows(code, days)
    except Exception as e:
        print(f'  fetch_tencent_kline({code}) dataHub error: {type(e).__name__}: {str(e)[:100]}')
        return pd.Series(dtype=float), pd.Series(dtype=float)


def _cache_path(kind, key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f'{kind}_{key}.json')


def _cache_get(path):
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > CACHE_TTL:
        return None
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return None


def _cache_put(path, rows):
    json.dump({'fetched_at': datetime.now().isoformat(timespec='seconds'), 'rows': rows},
              open(path, 'w', encoding='utf-8'), ensure_ascii=False, allow_nan=False)


def fetch_sw_index(code, no_cache=False):
    """申万行业指数日线（date/close/vol），带磁盘缓存。返回 DataFrame 或 None。"""
    p = _cache_path('swidx', code)
    if not no_cache:
        c = _cache_get(p)
        if c and c.get('rows'):
            return pd.DataFrame(c['rows'])
    try:
        from data_hub.collectors.stocks import fetch_sw_index_daily
        raw = fetch_sw_index_daily(code)
        if raw is None or len(raw) == 0:
            return None
        df = pd.DataFrame({
            'date': raw['trade_date'].astype(str).str.replace('-', '').map(
                lambda x: f'{x[:4]}-{x[4:6]}-{x[6:8]}'),
            'close': pd.to_numeric(raw['close'], errors='coerce'),
            'vol': pd.to_numeric(raw['vol'] if 'vol' in raw.columns else raw.get('volume'), errors='coerce'),
        }).dropna(subset=['close']).sort_values('date').reset_index(drop=True)
        _cache_put(p, json.loads(df.to_json(orient='records', force_ascii=False)))
        return df
    except Exception as e:
        print(f'  fetch_sw_index({code}) error: {type(e).__name__}: {str(e)[:120]}')
        return None


def fetch_sw_matrices(no_cache=False):
    """申万一级/二级/三级行业清单（DataHub 股票模块），列名沿用 行业代码/行业名称/上级行业。"""
    out = {}
    try:
        from data_hub.collectors.stocks import get_sw_l1, get_sw_l2, get_sw_l3

        def _cn(df):
            if df is None or len(df) == 0:
                return pd.DataFrame(columns=['行业代码', '行业名称', '上级行业', '成份个数'])
            return pd.DataFrame({
                '行业代码': df['sw_code'].astype(str) + '.SI',
                '行业名称': df['sw_name'].astype(str),
                '上级行业': df['parent_name'] if 'parent_name' in df.columns else None,
                '成份个数': pd.to_numeric(df.get('member_count'), errors='coerce'),
            })

        out['first'] = _cn(get_sw_l1(force=no_cache))
        out['second'] = _cn(get_sw_l2(force=no_cache))
        out['third'] = _cn(get_sw_l3(force=no_cache))
    except Exception as e:
        print(f'  fetch_sw_matrices error: {type(e).__name__}: {str(e)[:120]}')
    return out


def fetch_realtime_quote(code, session=None):
    """DataHub 快照：返回 (最新价, 成交量)；当日日线未发布时退实时价，成交量如实返回 None。"""
    kind, sym = _split_symbol(code)
    today = datetime.now().strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, trade_date=today)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, trade_date=today)
        if df is not None and len(df) > 0:
            row = df.iloc[-1]
            px = float(row['close']) if pd.notna(row.get('close')) else None
            vcol = 'vol' if 'vol' in df.columns else ('volume' if 'volume' in df.columns else None)
            v = float(row[vcol]) if (vcol and pd.notna(row.get(vcol))) else None
            return px, v
    except Exception:
        pass
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_realtime_spot
            px, _src = get_realtime_spot(sym, verbose=False)
            return px, None
        from data_hub.collectors.stocks import get_index_spot
        spot = get_index_spot(index_codes=[code])
        if spot is not None and len(spot) > 0:
            r0 = spot.iloc[0]
            px = float(r0['price']) if pd.notna(r0.get('price')) else None
            v = float(r0['volume']) if pd.notna(r0.get('volume')) else None
            return px, v
    except Exception:
        pass
    return None, None


def _cons_ok(df):
    """成分股表可用性校验：至少 3 行且多数行带可识别代码，防止源端半页导致静默空转。"""
    if df is None or len(df) < 3 or '股票代码' not in df.columns:
        return False
    s = df['股票代码'].astype(str)
    return int(s.str.contains(r'[0-9]{6}').sum()) >= 3


def fetch_sw_third_cons(code3, no_cache=False):
    """申万三级行业成分股表（带缓存与可用性校验）。"""
    p = _cache_path('swcons', code3)
    if not no_cache:
        c = _cache_get(p)
        if c and c.get('rows'):
            df_c = pd.DataFrame(c['rows'])
            if _cons_ok(df_c):
                return df_c
    try:
        from data_hub.collectors.stocks import get_sw_members
        df = get_sw_members(str(code3).replace('.SI', ''), level=3, raw=True)
        if df is None or len(df) == 0:
            print(f'  fetch_sw_third_cons({code3}) DataHub 返回空')
            return None
        if not _cons_ok(df):
            print(f'  fetch_sw_third_cons({code3}) 源端返回不完整：shape={getattr(df, "shape", None)}')
            return None
        _cache_put(p, json.loads(df.to_json(orient='records', force_ascii=False)))
        return df
    except Exception as e:
        print(f'  fetch_sw_third_cons({code3}) error: {type(e).__name__}: {str(e)[:120]}')
        return None


# ---------------------------------------------------------------- 指标

def _series_metrics(close: pd.Series, vol: pd.Series, allA_ret20_by_date: dict):
    """给定一条日线序列，返回截至其最后一日的层内指标。"""
    n = len(close)
    if n < W_SLOW + 5:
        return None
    c = close.values
    v = vol.values
    c_end = c[-1]
    ret20 = c_end / c[-1 - W_RET] - 1
    allA_r20 = allA_ret20_by_date.get(close.index[-1], np.nan)
    rs20 = ret20 - allA_r20 if not np.isnan(allA_r20) else np.nan
    ma20 = float(np.mean(c[-W_FAST:]))
    ma60 = float(np.mean(c[-W_SLOW:]))
    trend = int(c_end > ma20) + int(ma20 > ma60)
    v5 = float(np.mean(v[-W_VS:]))
    v20 = float(np.mean(v[-1 - W_VL:-1]))
    volr = (v5 / v20) if v20 else np.nan
    return {'data_date': str(close.index[-1]), 'close': round(float(c_end), 4),
            'ret20': ret20, 'rs20': rs20, 'ma20': ma20, 'ma60': ma60,
            'trend': trend, 'volr': volr, 'above_ma20': int(c_end > ma20),
            'ma20_gt_ma60': int(ma20 > ma60)}


def _score_rows(rows):
    """层内综合分：0.40×层内百分位(rs20) + 0.30×(trend/2) + 0.30×层内百分位(volr)。"""
    df = pd.DataFrame(rows)
    for col in ('rs20', 'volr'):
        df[f'pct_{col}'] = df[col].rank(pct=True, method='average')
    df['score'] = (0.40 * df['pct_rs20'].fillna(0.5)
                   + 0.30 * (df['trend'] / 2.0)
                   + 0.30 * df['pct_volr'].fillna(0.5))
    df = df.sort_values('score', ascending=False).reset_index(drop=True)
    df['rank'] = df.index + 1
    return df


def _row_out(r, keep_winner):
    o = {'rank': int(r['rank']), 'code': r['code'], 'name': r['name'],
         'data_date': r['data_date'], 'close': r.get('close'),
         'ret20': None if pd.isna(r['ret20']) else round(float(r['ret20']), 4),
         'rs20': None if pd.isna(r['rs20']) else round(float(r['rs20']), 4),
         'trend': int(r['trend']), 'volr': None if pd.isna(r['volr']) else round(float(r['volr']), 3),
         'again_ma20': int(r['above_ma20']), 'ma20_gt_ma60': int(r['ma20_gt_ma60']),
         'pct_rs20': None if pd.isna(r['pct_rs20']) else round(float(r['pct_rs20']), 4),
         'pct_volr': None if pd.isna(r['pct_volr']) else round(float(r['pct_volr']), 4),
         'score': round(float(r['score']), 4), 'winner': bool(keep_winner)}
    return o


# ---------------------------------------------------------------- 下钻

def _allA_table(session, no_cache=False, refresh=False):
    """全A（中证全指）日线：返回 pd.Series(date -> close)，用于计算同口径 20 日超额。"""
    p = _cache_path('allA', 'sz000985')
    if not no_cache and not refresh:
        c = _cache_get(p)
        if c and c.get('rows'):
            return pd.Series({r['date']: r['close'] for r in c['rows']}).sort_index()
    cs, _ = fetch_tencent_kline(ALL_A_CODE, session=session)
    if len(cs):
        _cache_put(p, [{'date': d, 'close': float(v)} for d, v in cs.items()])
    return cs


def _allA_ret20_map(allA: pd.Series):
    """date -> 全A 20 日收益（同口径，逐日）。"""
    m = {}
    a = allA.values
    dates = list(allA.index)
    for i in range(W_RET, len(a)):
        m[dates[i]] = float(a[i] / a[i - W_RET] - 1)
    return m


def _level_candidates(matrices, level, parent_name=None, seed_names=None):
    """返回该层候选 (code, name)。level ∈ {'first','second','third'}。"""
    df = matrices.get(level)
    if df is None or df.empty:
        return []
    if level == 'first':
        src = df[df['行业名称'].isin(seed_names or [])]
    else:
        src = df[df['上级行业'] == parent_name] if parent_name else df.iloc[0:0]
    return [(r['行业代码'], r['行业名称']) for _, r in src.iterrows()]


def _level_block(level_label, cands, allA_map, session, no_cache, errors):
    rows = []
    for code, name in cands:
        code_num = code.split('.')[0]
        df = fetch_sw_index(code_num, no_cache=no_cache)
        if df is None or df.empty:
            errors.append(f'{level_label}行业指数取数失败：{name}({code})')
            continue
        m = _series_metrics(df.set_index('date')['close'], df.set_index('date')['vol'], allA_map)
        if m is None:
            errors.append(f'{level_label}行业指数样本不足：{name}({code})，行数={len(df)}')
            continue
        m.update({'code': code, 'name': name})
        rows.append(m)
    if not rows:
        return None
    scored = _score_rows(rows)
    out_rows = [_row_out(r, keep_winner=(i == 0)) for i, (_, r) in enumerate(scored.iterrows())]
    win = out_rows[0]
    return {'level': level_label, 'data_date': win['data_date'],
            'source': '申万 2021 行业指数日线（DataHub sw_index_daily）',
            'candidates': out_rows,
            'winner': {'code': win['code'], 'name': win['name'],
                       'score': win['score'], 'rs20': win['rs20'],
                       'volr': win['volr'], 'trend': win['trend']}}


def _split_code(raw):
    """把成分股表里的代码拆成 (6 位数字, 市场后缀)。

    支持 '600519.SH'、'000858.SZ'、'600519' 三种写法；无后缀时按首位推断
    （6→SH，0/3→SZ，4/8→BJ）；推断不出返回 (None, None)，由调用方记账，不静默丢弃。
    """
    if not raw or raw.lower() == 'nan':
        return None, None
    if '.' in raw:
        num, suf = raw.split('.')[0], raw.split('.')[-1].upper()
    else:
        num, suf = raw, None
    num = num.strip().zfill(6)
    if not num.isdigit():
        return None, None
    if suf is None:
        suf = 'SH' if num[0] == '6' else ('SZ' if num[0] in '03' else ('BJ' if num[0] in '48' else None))
    return num, suf


def _stock_layer(code3, name3, allA_map, session, no_cache, errors):
    cons = fetch_sw_third_cons(code3, no_cache=no_cache)
    if cons is None or cons.empty:
        errors.append(f'三级行业成分股取数失败或源端不完整：{name3}({code3})')
        return None
    today = datetime.now().strftime('%Y-%m-%d')
    rows, skipped, rt_filled = [], [], 0
    for _, r in cons.iterrows():
        raw = str(r.get('股票代码', '')).strip()
        sn = str(r.get('股票简称', '')).strip()
        num, suf = _split_code(raw)
        prefix = {'SH': 'sh', 'SZ': 'sz', 'BJ': 'bj'}.get(suf or '')
        if prefix is None:
            skipped.append(f'{sn or raw}({raw})')
            continue
        cs, vs = fetch_tencent_kline(f'{prefix}{num}', session=session)
        if not len(cs):
            errors.append(f'个股价量取数失败：{sn}({raw})')
            continue
        # 当日 K 线尚未发布时，用实时行情补最后一根（价格/成交量同源同单位，与监测一致）
        bar_src = 'kline'
        if today not in cs.index:
            px, rv = fetch_realtime_quote(f'{prefix}{num}', session=session)
            if px is not None:
                cs = pd.concat([cs, pd.Series({today: float(px)})]).sort_index()
                if rv is not None:
                    vs = pd.concat([vs, pd.Series({today: float(rv)})]).sort_index()
                bar_src = 'kline+realtime' if rv is not None else 'kline+realtime(无量)'
                rt_filled += 1
        m = _series_metrics(cs, vs, allA_map)
        if m is None:
            errors.append(f'个股样本不足：{sn}({raw})，行数={len(cs)}')
            continue
        m.update({'code': raw, 'name': sn, 'bar_src': bar_src,
                  'mktcap': _num(r.get('市值')), 'pe_ttm': _num(r.get('市盈率ttm')),
                  'div_yield': _num(r.get('股息率')),
                  'np_growth': _num(_pick_col(cons, r, '净利润增速')),
                  'rev_growth': _num(_pick_col(cons, r, '营收增速')),
                  'chg5': _num(_pick_col(cons, r, '近5日涨幅')),
                  'sw3': r.get('申万3级')})
        rows.append(m)
    if skipped:
        errors.append(f'{name3} 成分股代码不可识别 {len(skipped)} 只（已记账、未计入排名）：'
                      + '、'.join(skipped[:6]))
    if not rows:
        errors.append(f'个股层无有效样本：{name3}({code3})')
        return None
    scored = _score_rows(rows)
    out = []
    for i, (_, r) in enumerate(scored.iterrows()):
        o = _row_out(r, keep_winner=(i < TOP_STOCKS))
        o.update({'mktcap': _num(r.get('mktcap')), 'pe_ttm': _num(r.get('pe_ttm')),
                  'div_yield': _num(r.get('div_yield')), 'np_growth': _num(r.get('np_growth')),
                  'rev_growth': _num(r.get('rev_growth')), 'chg5': _num(r.get('chg5')),
                  'bar_src': r.get('bar_src')})
        o['basis'] = _basis(o)
        o['selected'] = bool(i < TOP_STOCKS)
        out.append(o)
    r20 = [x['ret20'] for x in out if x['ret20'] is not None]
    rs = [x['rs20'] for x in out if x['rs20'] is not None]
    return {'level': '个股', 'l3_code': code3, 'l3_name': name3,
            'data_date': out[0]['data_date'],
            'source': 'DataHub 申万三级成分股宽表 + DataHub 个股日线（当日 K 线缺失时用快照补齐）',
            'total': len(out), 'top_n': TOP_STOCKS, 'rows': out,
            'selected': [x for x in out if x['selected']],
            'realtime_filled': rt_filled,
            'agg': {'equal_weight_ret20': round(float(np.mean(r20)), 4) if r20 else None,
                    'equal_weight_rs20': round(float(np.mean(rs)), 4) if rs else None,
                    'n': len(out)}}


def _basis(o):
    parts = []
    if o['rs20'] is not None:
        parts.append(f"20日超额 {o['rs20'] * 100:+.2f}%")
    parts.append('站上 MA20' if o['again_ma20'] else '跌破 MA20')
    parts.append('MA20>MA60' if o['ma20_gt_ma60'] else 'MA20<MA60')
    if o['volr'] is not None:
        parts.append(f"量能比 {o['volr']:.2f}")
    if o.get('np_growth') is not None:
        parts.append(f"净利增速 {o['np_growth']:+.1f}%")
    if o.get('mktcap') is not None:
        parts.append(f"市值 {o['mktcap']:.0f} 亿")
    return '；'.join(parts)


def _num(v):
    try:
        f = float(v)
        return None if (np.isnan(f)) else f
    except Exception:
        return None


def _pick_col(cons, r, kw):
    for c in cons.columns:
        if kw in str(c):
            return r.get(c)
    return None


def _growth_key(cons, kw):
    for c in cons.columns:
        if kw in str(c) and '（' in str(c):
            return str(c).split('（')[1].rstrip('）')
    return ''


def run_drilldown(directions, no_cache=False):
    """对给定宽泛方向执行下钻，返回结果字典。"""
    t0 = time.time()
    session = _session()
    matrices = fetch_sw_matrices(no_cache=no_cache)
    allA = _allA_table(session, no_cache=no_cache)
    if not len(allA):
        return {'as_of': datetime.now().strftime('%Y-%m-%d'), 'method': METHOD,
                'directions': [], 'errors': ['全A（中证全指 000985）行情取数失败，无法计算相对强弱']}
    allA_map = _allA_ret20_map(allA)

    res = {'as_of': datetime.now().strftime('%Y-%m-%d'), 'method': METHOD,
           'allA_last_date': str(allA.index[-1]), 'directions': [], 'errors': []}

    for d in directions:
        seeds = SEED_MAP.get(d)
        if not seeds:
            res['errors'].append(f'未定义的下钻方向：{d}（可选项：{"、".join(SEED_MAP)}）')
            continue
        block = {'direction': d, 'seed_l1': seeds, 'levels': [], 'stocks': None, 'path': []}
        # 一级
        c1 = _level_candidates(matrices, 'first', seed_names=seeds)
        b1 = _level_block('一级', c1, allA_map, session, no_cache, res['errors']) if c1 else None
        if not b1:
            res['errors'].append(f'方向「{d}」一级行业层无有效数据')
            res['directions'].append(block)
            continue
        block['levels'].append(b1)
        win1 = b1['winner']['name']
        block['path'].append({'level': '一级', 'name': win1, 'code': b1['winner']['code']})
        # 二级
        c2 = _level_candidates(matrices, 'second', parent_name=win1)
        b2 = _level_block('二级', c2, allA_map, session, no_cache, res['errors']) if c2 else None
        if not b2:
            res['errors'].append(f'{win1} 二级行业层无有效数据')
            res['directions'].append(block)
            continue
        block['levels'].append(b2)
        win2 = b2['winner']['name']
        block['path'].append({'level': '二级', 'name': win2, 'code': b2['winner']['code']})
        # 三级
        c3 = _level_candidates(matrices, 'third', parent_name=win2)
        b3 = _level_block('三级', c3, allA_map, session, no_cache, res['errors']) if c3 else None
        if not b3:
            res['errors'].append(f'{win2} 三级行业层无有效数据')
            res['directions'].append(block)
            continue
        block['levels'].append(b3)
        win3 = b3['winner']['name']
        block['path'].append({'level': '三级', 'name': win3, 'code': b3['winner']['code']})
        # 个股层
        block['stocks'] = _stock_layer(b3['winner']['code'], win3, allA_map, session,
                                       no_cache, res['errors'])
        if block['stocks']:
            block['path'].append({'level': '个股', 'name': f"{len(block['stocks']['selected'])} 只入选",
                                  'code': block['stocks']['l3_code']})
        res['directions'].append(block)

    res['note'] = ('申万三级为官方最细层级，故下钻终点为「三级行业 → 其成分股」；'
                   '行业层数据日期为申万指数源端最新可得日（可能滞后一个交易日），'
                   '个股层为腾讯日线最新交易日，两者日期已分别标注。')
    res['elapsed'] = round(time.time() - t0, 1)
    return res


def to_csv_rows(res):
    """把下钻结果摊平成可复算的明细行。"""
    rows = []
    for b in res.get('directions', []):
        d = b['direction']
        for lv in b.get('levels', []):
            for c in lv['candidates']:
                rows.append({
                    '方向': d, '层级': lv['level'], '行业/个股代码': c['code'], '名称': c['name'],
                    '数据日期': c['data_date'], '20日收益': c.get('ret20'), '20日超额': c.get('rs20'),
                    '层内百分位(超额)': c.get('pct_rs20'), '趋势分': c.get('trend'),
                    '站上MA20': c.get('again_ma20'), '量能比(5/20)': c.get('volr'),
                    '层内百分位(量能)': c.get('pct_volr'), '综合分': c.get('score'),
                    '层内排名': c.get('rank'), '本层胜出': int(bool(c.get('winner'))),
                    '入选个股': '', '市值(亿)': '', 'PE(TTM)': '', '股息率': '',
                    '净利增速(%)': '', '营收增速(%)': '', '近5日涨幅(%)': '',
                    '依据': ('本层综合分最高，进入下一层下钻' if c.get('winner') else '本层候选，未胜出'),
                    '来源': lv['source'],
                })
        st = b.get('stocks')
        if st:
            for s in st['rows']:
                rows.append({
                    '方向': d, '层级': '个股', '行业/个股代码': s['code'], '名称': s['name'],
                    '数据日期': s['data_date'], '20日收益': s.get('ret20'), '20日超额': s.get('rs20'),
                    '层内百分位(超额)': s.get('pct_rs20'), '趋势分': s.get('trend'),
                    '站上MA20': s.get('again_ma20'), '量能比(5/20)': s.get('volr'),
                    '层内百分位(量能)': s.get('pct_volr'), '综合分': s.get('score'),
                    '层内排名': s.get('rank'), '本层胜出': '',
                    '入选个股': int(bool(s.get('selected'))), '市值(亿)': s.get('mktcap'),
                    'PE(TTM)': s.get('pe_ttm'), '股息率': s.get('div_yield'),
                    '净利增速(%)': s.get('np_growth'), '营收增速(%)': s.get('rev_growth'),
                    '近5日涨幅(%)': s.get('chg5'),
                    '依据': s.get('basis'), '来源': st['source'],
                })
    return rows


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='机会侧行业下钻（宽泛方向 → 三级以下 → 个股）')
    ap.add_argument('--directions', default='消费,酒')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--out-json', default='cgap_drilldown_today.json')
    ap.add_argument('--out-csv', default='')
    a = ap.parse_args()
    dirc = [x.strip() for x in a.directions.split(',') if x.strip()]
    res = run_drilldown(dirc, no_cache=a.no_cache)
    with open(a.out_json, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, allow_nan=False, indent=2)
    print(f'JSON -> {a.out_json}  ({res.get("elapsed")}s)')
    if a.out_csv:
        pd.DataFrame(to_csv_rows(res)).to_csv(a.out_csv, index=False, encoding='utf-8-sig')
        print(f'CSV  -> {a.out_csv}')
    for b in res['directions']:
        path = ' → '.join(f"{p['level']}:{p['name']}" for p in b['path'])
        print(f'  [{b["direction"]}] 路径 {path}')
        if b['stocks']:
            sel = '、'.join(f"{x['name']}({x['code']},{x['score']:.2f})"
                            for x in b['stocks']['selected'][:10])
            print(f'      入选个股（共 {b["stocks"]["total"]} 只候选）：{sel}')
    for e in res['errors']:
        print(f'  [缺失] {e}')