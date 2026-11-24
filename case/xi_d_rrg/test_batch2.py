import socket, time
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(15)
import warnings; warnings.filterwarnings('ignore')
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()

# fina_indicator 按报告期全市场?
try:
    t0 = time.time()
    df = pro.fina_indicator(period='20240630', fields='ts_code,end_date,netprofit_yoy')
    print('fina_by_period rows=', len(df), 'time=%.2fs' % (time.time()-t0))
except Exception as e:
    print('fina_by_period ERR:', str(e)[:100])

# margin 按日全市场(融资融券汇总)
try:
    t0 = time.time()
    df = pro.margin(trade_date='20240102')
    print('\nmargin_by_day rows=', len(df), 'time=%.2fs' % (time.time()-t0))
    print(df.head(2).to_string())
except Exception as e:
    print('margin_by_day ERR:', str(e)[:100])

# daily_basic 按日全市场
try:
    t0 = time.time()
    df = pro.daily_basic(trade_date='20240102', fields='ts_code,trade_date,pe_ttm,dv_ratio,total_mv,circ_mv')
    print('\ndaily_basic_by_day rows=', len(df), 'time=%.2fs' % (time.time()-t0))
except Exception as e:
    print('daily_basic_by_day ERR:', str(e)[:100])
