#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一选模 · 今日/本期信号（只输出达标槽位，其余观望）

逻辑
----
读 .cache/wide_ens/unified_selected.json 里挑好的 (指数, 周期, 档位) → (候选, pct门槛)，
再从候选曲线取**最新一日**的 score 与其滚动分位 pct：

    方向 = sign(score)            分位 = 最新 pct
    触发 = pct >= 门槛            → 否则观望（空仓）

三条硬规矩（都是之前踩过的坑）
------------------------------
1. 只认达标槽位。OOS 没过 60%(T+1)/65%(T+5) 的槽位一律「观望」，不给仓位。
2. T+5 的「最新一日」必须扣掉收益未实现的尾部：候选 csv 里 ret 为 NaN 的行就是
   还没到期的信号日，它的 score 才是真正可用于「今天下注」的信号。
3. 分位用「截至该日的历史」算，不含未来（rolling rank，与选模时同一套）。

用法：
  python3 bin/wide_ens/unified_signal.py              # 打印
  python3 bin/wide_ens/unified_signal.py --json       # 输出 json
  python3 bin/wide_ens/unified_signal.py --out x.json
"""
import os
import sys
import json
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

import unified_select as U

CACHE = U.CACHE
SEL_JSON = U.OUT_JSON
SIG_JSON = os.path.join(CACHE, 'unified_signal.json')

# 仓位映射（沿用五指数卡片口径）
def pos_from_hit(hit):
    if hit is None:
        return 0.0, '无效'
    if hit >= 0.70:
        return 1.00, '满仓'
    if hit >= 0.65:
        return 0.70, '七成'
    if hit >= 0.60:
        return 0.50, '五成'
    if hit >= 0.55:
        return 0.30, '三成'
    return 0.0, '不做'


def latest_signal(df, thr):
    """最新一日的 score / 分位 / 触发状态。

    ⚠️ T+5 候选 csv 的尾部 ret 可能是 NaN（收益还没到期），
       这些日子的 score 正是「今天可以下注」的信号，不能因为 ret 空就丢掉。
    """
    d = df.dropna(subset=['score'])
    if not len(d):
        return None
    last = d.index[-1]
    sc = float(d['score'].iloc[-1])
    pc = d['pct'].iloc[-1]
    pc = float(pc) if pd.notna(pc) else None
    trig = bool(pc is not None and pc >= thr)
    return dict(date=str(last), score=round(sc, 4),
                pct=round(pc, 4) if pc is not None else None,
                thr=thr, triggered=trig,
                state=('看多' if sc > 0 else '看空') if trig else '未触发（观望）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--out', default=SIG_JSON)
    a = ap.parse_args()

    if not os.path.exists(SEL_JSON):
        print('缺 %s，先跑 unified_select.py' % SEL_JSON)
        return 2
    sel = json.load(open(SEL_JSON, encoding='utf-8'))
    pool = U.build_pool()

    rows = []
    for code in U.ORDER:
        for h in (1, 5):
            for mode in ('broad', 'precise'):
                r = sel['selected'][code][str(h)][mode]
                nm = U.NAMES[code]
                base = dict(code=code, name=nm, h=h, mode=mode)
                if r.get('status') not in ('ok', 'below_bar'):
                    rows.append(dict(base, status='no_model',
                                     state='无模型（该档位选不出）', pos=0.0,
                                     reason=r.get('reason')))
                    continue
                df = (pool.get(code) or {}).get(h, {}).get(r['cand'])
                if df is None:
                    rows.append(dict(base, status='no_data',
                                     state='候选曲线缺失', pos=0.0))
                    continue
                sig = latest_signal(df, r['thr'])
                oos = r['oos'] or {}
                hit = oos.get('hit')
                reliable = bool(r['status'] == 'ok')
                pos, pos_txt = pos_from_hit(hit) if reliable else (0.0, '观望')
                # 不可靠周期不给仓位
                if not reliable:
                    pos, pos_txt = 0.0, '观望'
                # 看空 → 仓位为「回避」，不给多头仓位
                if sig and sig['triggered'] and sig['score'] < 0:
                    pos, pos_txt = 0.0, '回避（看空）'
                if sig and not sig['triggered']:
                    pos, pos_txt = 0.0, '未触发'
                rows.append(dict(base, status=r['status'],
                                 cand=r['cand'], score=sig['score'],
                                 pct=sig['pct'], thr=r['thr'],
                                 sig_date=sig['date'], state=sig['state'],
                                 triggered=sig['triggered'],
                                 oos_hit=hit, oos_n=oos.get('n'),
                                 oos_net=oos.get('net'),
                                 reliable=reliable, pos=pos, pos_txt=pos_txt,
                                 stable=r.get('stable'),
                                 collapsed=r.get('collapsed'),
                                 longonly=r.get('longonly')))

    obj = dict(updated=pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
               rule='只认 OOS 达标槽位（T+1>=60% / T+5>=65%，n>=25，p<=0.10，净>0），其余观望',
               signals=rows)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

    if a.json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
        return 0

    print('统一选模 · 信号表   生成 %s' % obj['updated'])
    print(obj['rule'])
    print()
    hdr = '%-9s %-4s %-9s | %-26s %-6s | %-6s %-8s | %-12s | %-6s %-6s'
    print(hdr % ('指数', '周期', '档位', '模型', '分位', '触发', '方向', '状态', '仓位', '可靠'))
    print('-' * 118)
    for r in rows:
        if r['status'] in ('no_model', 'no_data'):
            print('%-9s T+%-2d %-9s | %s' % (r['name'], r['h'],
                  '普适' if r['mode'] == 'broad' else '高精度',
                  ('⚠️ ' + str(r.get('state')))[:52]))
            continue
        print('%-9s T+%-2d %-9s | %-26s %-6s | %-6s %-8s | %-12s | %-6.2f %-6s' % (
            r['name'], r['h'], '普适' if r['mode'] == 'broad' else '高精度',
            r['cand'][:26], '%.2f' % r['thr'],
            '%.2f' % r['pct'] if r['pct'] is not None else '-',
            '是' if r['triggered'] else '否',
            r['state'], r['pos'], '是' if r['reliable'] else '否'))
    print()
    print('[save] %s' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
