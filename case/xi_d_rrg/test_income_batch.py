import socket, time
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(15)
import warnings; warnings.filterwarnings('ignore')
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()
t0 = time.time()
df = pro.income(period='20240630', fields='ts_code,end_date,report_type,n_income,total_revenue')
print('income_by_period rows=', len(df), 'time=%.2fs' % (time.time()-t0))
print(df.head(2).to_string())
