import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(15)
import warnings; warnings.filterwarnings('ignore')
import akshare as ak
for sym, mkt in [('000002','sz'), ('000016','sz'), ('600519','sh'), ('300750','sz')]:
    try:
        df = ak.stock_individual_fund_flow(stock=sym, market=mkt)
        print(sym, 'OK rows=', len(df))
    except Exception as e:
        print(sym, 'ERR:', repr(str(e))[:200])
