#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单指数 · 指定预测周期（h）的当日预测 + SHAP 归因，输出独立 json。

用途：周日周报要同时要「T+1（下周一）」和「T+5（下周整周）」两套预测，
      而 signal_shap.py 的 CFG 把 h 写死了，并行跑必须一指数一周期一文件，避免互相覆盖。

用法：
  python3 bin/wide_ens/predict_by_h.py --code 000688_SH --h 5 --depth 3
  python3 bin/wide_ens/predict_by_h.py --code 399006_SZ --h 1 --depth 3 --out /tmp/x.json
"""
import os
import sys
import json
import argparse
import warnings

warnings.filterwarnings('ignore')
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hunt70 as H               # noqa: E402
import signal_shap as S          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True, help='如 000688_SH')
    ap.add_argument('--h', type=int, required=True, help='预测周期（交易日数）：1=次日，5=一周')
    ap.add_argument('--depth', type=int, default=3)
    ap.add_argument('--topk', type=int, default=6)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    name = H.IDX[a.code][0]
    print('>>> %s 训练中（T+%d d%d）...' % (name, a.h, a.depth), flush=True)
    r = S.predict_with_shap(a.code, a.h, a.depth, topk=a.topk)
    if not r:
        print('   数据不足')
        return 1

    out = dict(code=a.code, name=name, h=a.h, depth=a.depth,
               date=r['date'], p_up=r['p_up'], p_mid=r['p_mid'], p_down=r['p_down'],
               score=r['score'], up=r['up'], down=r['down'], n_feat=r.get('n_feat'))
    path = a.out or os.path.join(WS, '.cache/wide_ens/weekly',
                                 'T%d_%s.json' % (a.h, a.code))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print('   %s T+%d score=%+.3f (P涨 %.2f P跌 %.2f)' % (r['date'], a.h, r['score'],
                                                          r['p_up'], r['p_down']))
    print('   ⬆ 推涨: %s' % r['up'])
    print('   ⬇ 压跌: %s' % r['down'])
    print('   已存 %s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
