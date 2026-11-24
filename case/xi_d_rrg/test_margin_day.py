import socket, time
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(15)
import warnings; warnings.filterwarnings('ignore')
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()
try:
    t0 = time.time()
    df = pro.margin_detail(trade_date='20240102')
    print('margin_detail_by_day rows=', len(df), 'time=%.2fs' % (time.time()-t0))
    print(df.head(2).to_string())
except Exception as e:
    print('ERR:', str(e)[:150])
try:
    t0 = time.time()
    df = pro.margin_detail(start_date='20240102', end_date='20240103')
    print('margin_detail_range rows=', len(df), 'time=%.2fs' % (time.time()-t0))
except Exception as e:
    print('RANGE ERR:', str(e)[:150])
