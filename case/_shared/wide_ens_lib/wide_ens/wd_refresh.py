#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重算 wd 家族（.cache/wide_daily/h<H>_<code>.csv）的指定周期

为什么单独拎出来
----------------
compare_horizon.py 一次跑 H=1..5 共 25 个任务（约 10 分钟），
日频只需要 T+1，周频只需要 T+5，全跑太浪费。
本脚本复用 compare_horizon.train_one（同一套特征/walk-forward/5种子），
只跑指定 H，落盘文件名与原来完全一致，下游 unified_select 无感。

⚠️ wd 家族的 csv 里 bagged 就是当日 score，它由 walk-forward 重训得到，
   不重算就会一直停在旧日期（踩过：日频卡用了 3 天前的信号）。

用法：
  python3 bin/wide_ens/wd_refresh.py --h 1          # 5 指数 × T+1（约 4 分钟）
  python3 bin/wide_ens/wd_refresh.py --h 5          # 5 指数 × T+5
  python3 bin/wide_ens/wd_refresh.py --h 1,5 -p 4
"""
import os
import sys
import time
import argparse
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, 'bin/wide_daily'))

import warnings
warnings.filterwarnings('ignore')

import compare_horizon as CH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--h', default='1', help='周期，逗号分隔，如 1 或 1,5')
    ap.add_argument('-p', '--procs', type=int, default=4)
    a = ap.parse_args()
    hs = [int(x) for x in a.h.split(',') if x.strip()]
    jobs = [(code, name, fn, H) for H in hs for code, name, fn in CH.C.INDICES]
    print('[wd_refresh] %d 任务（%d 指数 × H=%s），%d 进程' % (
        len(jobs), len(CH.C.INDICES), hs, a.procs), flush=True)
    t0 = time.time()
    ok = 0
    failed = 0
    with Pool(a.procs) as pool:
        for r in pool.imap_unordered(CH.train_one, jobs):
            # train_one 数据不足/取数失败时返回 None —— 不能当成功计数，
            # 否则「全部没数据」也会打印 完成 N/N、rc=0，把失败抹成成功。
            if r is None:
                failed += 1
            else:
                ok += 1
    print('[wd_refresh] 完成 %d/%d（失败 %d），%.0fs'
          % (ok, len(jobs), failed, time.time() - t0), flush=True)
    return 0 if (ok == len(jobs) and failed == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
