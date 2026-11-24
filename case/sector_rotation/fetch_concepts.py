# -*- coding: utf-8 -*-
import socket, json, time, sys, os

_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs')

import akshare as ak

concepts = ['存储芯片', '共封装光学(CPO)', '白酒概念', '光刻机', '机器人概念', '人形机器人',
            '算力租赁', '创新药', '光伏概念', '锂电池概念', '人工智能', '5G概念']
out = {}
for c in concepts:
    try:
        df = ak.stock_board_concept_index_ths(symbol=c, start_date='20240101', end_date='20260928')
        rows = [{'trade_date': str(r['日期']).replace('-', ''), 'close': float(r['收盘价'])} for _, r in df.iterrows()]
        out[c] = rows
        print(f'[ok] {c}: {len(rows)} last={rows[-1]["trade_date"]}', flush=True)
    except Exception as e:
        print(f'[err] {c}: type={type(e).__name__} msg={str(e)[:120]}', flush=True)
    time.sleep(1.2)

os.makedirs('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/report_run/concepts', exist_ok=True)
with open('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/report_run/concepts/concepts.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False)
print('saved', len(out))