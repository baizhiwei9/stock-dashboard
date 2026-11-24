import socket, time
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(15)
import warnings; warnings.filterwarnings('ignore')
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()
SD, ED = '20191230', '20260822'
codes = ['000002.SZ','000006.SZ','000008.SZ','000009.SZ','000012.SZ','000016.SZ','000027.SZ','000028.SZ','000031.SZ','000032.SZ']
t0 = time.time()
ok = 0; errs = []
for c in codes:
    try:
        df = pro.moneyflow(ts_code=c, start_date=SD, end_date=ED)
        if len(df): ok += 1
    except Exception as e:
        errs.append(str(e)[:50])
        time.sleep(3)
print('10 stocks in %.1fs (%.2fs each), ok=%d, errs=%d' % (time.time()-t0, (time.time()-t0)/10, ok, len(errs)))
print(errs[:3])
