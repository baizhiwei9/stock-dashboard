# -*- coding: utf-8 -*-
"""等待官方直连的 IP 计数窗口清零后, 立即以稳定档(0.65s)启动 rrg 抓取
背景(2026-08-23): 官方 token 直连被沙箱 NAT 出口漂移触发 'IP数量超限(10个)',
高频突发会把它喂满; 限制窗口未知(数分钟~1小时)。本脚本每20s探测一次,
连续3次成功视为解除, 然后立刻调用 rrg_fetch 以 0.65s 稳定档跑。
"""
import sys, time, subprocess, os

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from ts_official import init_tushare
pro = init_tushare()

streak = 0
t0 = time.time()
while streak < 3 and time.time() - t0 < 1500:
    try:
        pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260201')
        streak += 1
        print(f'probe ok #{streak}  elapsed={time.time()-t0:.0f}s', flush=True)
    except Exception as e:
        streak = 0
        print(f'probe fail {str(e)[:40]}  elapsed={time.time()-t0:.0f}s', flush=True)
    time.sleep(20)
if streak < 3:
    print('LIMIT_NOT_CLEARED (25min内未解除)', flush=True)
    sys.exit(3)

print('LIMIT_CLEARED, 启动 rrg_fetch 0.65s档...', flush=True)
budget = sys.argv[1] if len(sys.argv) > 1 else '250'
r = subprocess.run([sys.executable, os.path.join(BASE, 'rrg_fetch.py'), budget],
                   cwd=BASE, capture_output=True, text=True, timeout=int(budget) + 120)
print(r.stdout[-2000:], flush=True)
if r.returncode != 0:
    print('RRG_FETCH exit', r.returncode, flush=True)
    sys.exit(r.returncode)
print('WARMUP_RUN_DONE', flush=True)