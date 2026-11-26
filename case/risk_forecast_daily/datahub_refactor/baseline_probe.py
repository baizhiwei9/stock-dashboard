#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期权 akshare 链路 基准/对照探针
==============================
直接调用 option_monitor.py / skew_percentile.py 的 akshare 数据获取函数，
把原始结果 + 派生指标落盘成 JSON，用于改造前后逐字段对照。

用法:
    python baseline_probe.py <tag> [YYYYMMDD]
      tag: baseline / after
"""
import os
import sys
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, '..', 'scripts'))
sys.path.insert(0, SCRIPTS)

TAG = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
TRADE_DATE = sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now().strftime('%Y%m%d')

out = {'tag': TAG, 'trade_date': TRADE_DATE, 'ts': datetime.datetime.now().isoformat()}

import option_monitor as om


def _summarize_contracts(contracts):
    """对合约列表做可对照的摘要（条数 + 字段 + 逐合约关键值）"""
    if not contracts:
        return {'count': 0, 'calls': 0, 'puts': 0, 'rows': []}
    rows = []
    for c in contracts:
        rows.append({
            'ts_code': str(c.get('ts_code')),
            'call_put': c.get('call_put'),
            'exercise_price': round(float(c.get('exercise_price') or 0), 4),
            'maturity_date': str(c.get('maturity_date')),
            'close': round(float(c.get('close') or 0), 6),
            'volume': int(c.get('volume') or 0),
            'oi': int(c.get('oi') or 0),
        })
    rows.sort(key=lambda r: (r['maturity_date'], r['call_put'], r['exercise_price']))
    return {
        'count': len(contracts),
        'calls': sum(1 for c in contracts if c.get('call_put') == 'C'),
        'puts': sum(1 for c in contracts if c.get('call_put') == 'P'),
        'fields': sorted(set(k for c in contracts for k in c.keys())),
        'rows': rows,
    }


def _round_metrics(d):
    if not isinstance(d, dict):
        return d
    out_d = {}
    for k, v in d.items():
        if isinstance(v, float):
            out_d[k] = round(v, 6)
        elif isinstance(v, (list, dict)):
            out_d[k] = json.loads(json.dumps(v, default=str))
        else:
            out_d[k] = v
    return out_d


# ---------- 1. 现货价 akshare 链路 ----------
spot = {}
try:
    spot['index_000852'] = om._spot_akshare_index('000852')
except Exception as e:
    spot['index_000852'] = f'ERR:{type(e).__name__}:{e}'
for code, ex in [('510050', 'SSE'), ('510300', 'SSE'), ('510500', 'SSE'),
                 ('588000', 'SSE'), ('159915', 'SZSE')]:
    try:
        spot[f'etf_{code}'] = om._spot_akshare_etf(code, ex)
    except Exception as e:
        spot[f'etf_{code}'] = f'ERR:{type(e).__name__}:{e}'
out['spot_akshare'] = spot

# ---------- 2. akshare 合约链路 + 派生指标 ----------
targets = [
    ('50ETF', '510050', 'SSE'),
    ('300ETF', '510300', 'SSE'),
    ('500ETF', '510500', 'SSE'),
    ('创业板ETF', '159915', 'SZSE'),
    ('科创50ETF', '588000', 'SSE'),
    ('中证1000', 'MO', 'CFFEX'),
]
tgt_out = {}
for name, code, ex in targets:
    rec = {}
    try:
        contracts = om.get_option_contracts_akshare(code, ex, TRADE_DATE)
        rec['contracts'] = _summarize_contracts(contracts)
        sp = spot.get('index_000852') if ex == 'CFFEX' else spot.get(f'etf_{code}')
        if isinstance(sp, str):
            sp = None
        rec['spot_used'] = round(sp, 6) if isinstance(sp, (int, float)) else None
        if contracts and sp:
            try:
                rec['skew'] = _round_metrics(om.calc_skew(contracts, sp, TRADE_DATE, 0.03))
            except Exception as e:
                rec['skew'] = f'ERR:{type(e).__name__}:{e}'
            try:
                rec['gex'] = _round_metrics(om.calc_gex(contracts, sp, TRADE_DATE, 0.03))
            except Exception as e:
                rec['gex'] = f'ERR:{type(e).__name__}:{e}'
            try:
                rec['risk_reversal'] = _round_metrics(om.calc_risk_reversal(contracts, sp, TRADE_DATE, 0.03))
            except Exception as e:
                rec['risk_reversal'] = f'ERR:{type(e).__name__}:{e}'
    except Exception as e:
        rec['error'] = f'{type(e).__name__}:{e}'
    tgt_out[name] = rec
    print(f'[probe] {name} done: contracts={rec.get("contracts", {}).get("count")}')
out['targets'] = tgt_out

# ---------- 3. skew_percentile.py akshare 链路 ----------
try:
    import skew_percentile as sp_mod
    sp_out = {}
    end_month = datetime.datetime.now().strftime('%y%m')
    for cfg in sp_mod.ETF_CONFIG:
        rec = {}
        try:
            df = sp_mod.get_akshare_option_chain(cfg, end_month)
            if df is None:
                rec['board'] = None
            else:
                rec['board'] = {
                    'rows': int(len(df)),
                    'columns': [str(c) for c in df.columns],
                    'head': json.loads(df.head(8).to_json(orient='records', force_ascii=False, date_format='iso')),
                }
        except Exception as e:
            rec['board'] = f'ERR:{type(e).__name__}:{e}'
        try:
            r = sp_mod.get_akshare_skew(cfg['code'], cfg['name'], cfg)
            rec['skew_result'] = _round_metrics(r) if r else None
        except Exception as e:
            rec['skew_result'] = f'ERR:{type(e).__name__}:{e}'
        sp_out[cfg['name']] = rec
        print(f'[probe] skew_percentile {cfg["name"]} done')
    out['skew_percentile'] = sp_out
except Exception as e:
    out['skew_percentile'] = f'ERR:{type(e).__name__}:{e}'

# 直接探针：option_finance_board / option_finance_sse_underlying
try:
    import akshare as ak
    raw = {}
    for symbol in ['华夏上证50ETF期权', '华泰柏瑞沪深300ETF期权', '南方中证500ETF期权', '易方达创业板ETF期权', '中证1000ETF期权']:
        try:
            d = ak.option_finance_board(symbol=symbol, end_month=end_month)
            raw[symbol] = {
                'rows': 0 if d is None else int(len(d)),
                'columns': [] if d is None else [str(c) for c in d.columns],
            }
        except Exception as e:
            raw[symbol] = f'ERR:{type(e).__name__}:{e}'
    out['raw_board_probe'] = raw
except Exception as e:
    out['raw_board_probe'] = f'ERR:{type(e).__name__}:{e}'

dst = os.path.join(HERE, f'probe_{TAG}.json')
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
print(f'\n[saved] {dst}')
