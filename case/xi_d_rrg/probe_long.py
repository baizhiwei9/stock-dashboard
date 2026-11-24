import sys, time, json
sys.path.insert(0, 'pylib'); sys.path.insert(0, '.')
from ts_config import init_tushare
pro = init_tushare()
interval = float(sys.argv[1])
duration = float(sys.argv[2])  # 秒
out = sys.argv[3]
ok = err = 0
fails = []
t0 = time.time()
while time.time() - t0 < duration:
    t = time.time() - t0
    try:
        pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260201')
        ok += 1
    except Exception as e:
        err += 1
        if len(fails) < 30:
            fails.append({'t': round(t, 1), 'msg': str(e).replace('\n', ' ')[:90]})
    time.sleep(interval)
dt = time.time() - t0
result = {
    'interval': interval, 'duration': round(dt, 1),
    'ok': ok, 'err': err,
    'rate': round(ok / (dt / 60), 1),  # 实际成功速率 次/分
    'fails': fails,
}
with open(out, 'w') as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print(f'DONE interval={interval}s dur={dt:.0f}s ok={ok} err={err} rate={result["rate"]}/min', flush=True)