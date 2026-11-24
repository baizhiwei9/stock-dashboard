import socket, time, threading
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(15)
import warnings; warnings.filterwarnings('ignore')
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
from concurrent.futures import ThreadPoolExecutor
pro = init_tushare()
SD, ED = '20191230', '20260822'
codes = [f'{i:06d}.SZ' for i in [1,2,6,8,9,12,16,27,28,31,32,33,36,39,42,46,50,60,61,63,69,70,78,88,89,95,96,100,150,151]]
lock = threading.Lock()
errors = []

def work(c):
    try:
        df = pro.moneyflow(ts_code=c, start_date=SD, end_date=ED)
        return (c, 'ok', len(df))
    except Exception as e:
        with lock: errors.append(str(e)[:60])
        return (c, 'err', None)

t0 = time.time()
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(work, codes))
dt = time.time() - t0
oks = [r for r in res if r[1]=='ok']
print(f'{len(codes)} stocks in {dt:.1f}s ({dt/len(codes):.2f}s each), ok={len(oks)}, err={len(errors)}')
print(errors[:3])
