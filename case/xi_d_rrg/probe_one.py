import sys, time
sys.path.insert(0, 'pylib'); sys.path.insert(0, '.')
from ts_config import init_tushare
pro = init_tushare()
interval = float(sys.argv[1])
n = int(sys.argv[2])
ok = err = 0
fails = []
t0 = time.time()
for i in range(n):
    t = time.time() - t0
    try:
        pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260201')
        ok += 1
    except Exception as e:
        err += 1
        msg = str(e).replace('\n', ' ')[:100]
        fails.append((round(t, 1), msg))
        if err >= n:
            break
    time.sleep(interval)
dt = time.time() - t0
print(f'=== 间隔{interval}s n={n}: ok={ok} err={err} 总耗时={dt:.1f}s ===', flush=True)
for f in fails[:15]:
    print(f'  FAIL@ {f[0]}s: {f[1]}', flush=True)