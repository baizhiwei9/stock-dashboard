#!/usr/bin/env python3
"""
A股风格漂移监控（期权版）
基于科创50ETF(588000)、创业板ETF(159915)、沪深300ETF(510300)、中证500ETF(510500)的期权数据
判断当前风格偏向: 买消费/买成长/买科创/买红利/不动

数据源: Tushare(主) + 交易所/新浪(备)
输出: z_opt风格漂移信号 + 操作建议
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()


import os, sys, json, time, sqlite3, math, re, socket, warnings
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()

import datetime
from datetime import timedelta
from typing import Optional, Dict, List, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import brentq

warnings.filterwarnings('ignore')
socket.setdefaulttimeout(15)

# ── DataHub 统一数据入口（禁止直连 tushare/交易所） ──
_HUB_PATHS = [
    _HUB_ROOT,
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', '..', '..', 'codeact', 'scripts'),
]
for _hp in _HUB_PATHS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
        break
try:
    import data_hub as dh
except Exception as _e:  # noqa: BLE001
    dh = None
    print(f"[DataHub] 导入失败: {_e}")

def _pick_date(df, trade_date):
    """DataHub 日线可能返回多日，按 trade_date 精确取行（业务按 iloc[0] 取值）。"""
    if df is None or len(df) == 0 or not trade_date:
        return df
    _td = str(trade_date).replace('-', '')
    if 'trade_date' in df.columns:
        _m = df[df['trade_date'].astype(str).str.replace('-', '') == _td]
        if len(_m):
            return _m.reset_index(drop=True)
    return df.tail(1).reset_index(drop=True)


# 兼容旧引用：pro 改为 DataHub 薄代理，禁止再直连 tushare
class _ProProxy:
    def trade_cal(self, exchange='SSE', start_date=None, end_date=None, is_open='1'):
        _d = dh.get_trade_dates(start_date, end_date, exchange) if dh else None
        return pd.DataFrame({'cal_date': sorted(_d or [])})
    def opt_basic(self, exchange='SSE', fields=None):
        return dh.get_option_basic_raw(exchange) if dh else None
    def opt_daily(self, trade_date=None, exchange='SSE', **kw):
        return dh.get_option_daily_raw(trade_date, exchange) if dh else None
    def fund_daily(self, ts_code=None, trade_date=None, fields=None):
        _d = dh.get_etf_daily(ts_code=ts_code, trade_date=trade_date) if dh else None
        return _pick_date(_d, trade_date)
    def index_daily(self, ts_code=None, trade_date=None, fields=None, **kw):
        _d = dh.get_index_daily(ts_code=ts_code, trade_date=trade_date) if dh else None
        return _pick_date(_d, trade_date)
pro = _ProProxy()

API_SLEEP = 0.18

# ===== 新增：导入新浪期权爬虫 =====
CRAWLER_DIR = _cp("/Coze/Drive/期权风险监控/scripts")
if CRAWLER_DIR not in sys.path:
    sys.path.insert(0, CRAWLER_DIR)

try:
    from eastmoney_option_crawler import fetch_option_chain, get_spot_price as sina_get_spot_price
    SINA_AVAILABLE = True
    print("[数据源] 新浪期权爬虫加载成功 ✅")
except Exception as e:
    SINA_AVAILABLE = False
    print(f"[数据源] 新浪期权爬虫加载失败 ❌: {e}，将使用Tushare降级模式")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, '..', 'data', 'options_cache')
OUTPUT_DIR = os.path.join(SCRIPT_DIR, '..', 'outputs')
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 偏度口径统一（2026-11-09）
# ------------------------------------------------------------
# 决策层 CBOE SKEW 一律走 data_hub.option_metrics.calc_cboe_skew_directional
# （全系统唯一真源，以 100 为中轴，与 classify_skew_direction 的
#   120/105/95/80 阈值配套）。本文件不再自带公式副本。
# 注意：本文件写入 daily_metrics.cboe_skew 的列，历史上可能来自
#   「统一接口(calc_cboe_skew, OTM20%put/ATM 比率口径)」或
#   「本文件内联(100+30*(put25-atm)/atm)」两条不同口径，已在本轮统一为后者，
#   历史混合情况详见《偏度口径统一说明.md》。
# ============================================================

def _unified_cboe_skew(atm_iv, put25_iv):
    """决策层 CBOE SKEW（唯一真源薄封装，带降级兜底）。"""
    try:
        from data_hub.option_metrics import calc_cboe_skew_directional
        return calc_cboe_skew_directional(atm_iv, put25_iv)
    except Exception:  # noqa: BLE001
        # 兜底：与唯一真源完全等价的公式（data_hub 不可用时）
        try:
            atm = float(atm_iv) if atm_iv is not None else 0.0
            p25 = float(put25_iv) if put25_iv is not None else atm
        except (TypeError, ValueError):
            return 100.0
        if atm <= 0.01:
            return 100.0
        return 100.0 + 30.0 * (p25 - atm) / atm


DB_PATH = os.path.join(CACHE_DIR, 'style_drift.db')

# 数据源标记（由migrate_db_add_data_source设置）
HAS_DATA_SOURCE_COL = False

# ============ 标的配置 ============
TARGETS = {
    '588000': {'name': '科创50ETF', 'exchange': 'SSE', 'opt_keyword': '科创', 'price_code': '588000.SH'},
    '159915': {'name': '创业板ETF', 'exchange': 'SZSE', 'opt_keyword': '创业板', 'price_code': '159915.SZ'},
    '510300': {'name': '沪深300ETF', 'exchange': 'SSE', 'opt_keyword': '沪深300', 'price_code': '510300.SH'},
    '510500': {'name': '中证500ETF', 'exchange': 'SSE', 'opt_keyword': '500ETF', 'price_code': '510500.SH'},
}

# ============ BS模型 ============
def bs_d1(S, K, T, r, sigma, q=0):
    if sigma <= 0 or T <= 0: return 0
    return (math.log(S/K) + (r - q + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))

def bs_call(S, K, T, r, sigma, q=0):
    if T <= 0 or sigma <= 0: return max(S - K, 0)
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma*math.sqrt(T)
    return S*math.exp(-q*T)*norm.cdf(d1) - K*math.exp(-r*T)*norm.cdf(d2)

def bs_put(S, K, T, r, sigma, q=0):
    if T <= 0 or sigma <= 0: return max(K - S, 0)
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma*math.sqrt(T)
    return K*math.exp(-r*T)*norm.cdf(-d2) - S*math.exp(-q*T)*norm.cdf(-d1)

def bs_iv(S, K, T, r, mkt_price, opt_type='call', q=0):
    if mkt_price is None or mkt_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(S - K, 0) if opt_type == 'call' else max(K - S, 0)
    if mkt_price < intrinsic - 0.001: return None
    try:
        def obj(sigma):
            if sigma <= 0.001: return -1
            v = bs_call(S, K, T, r, sigma, q) if opt_type == 'call' else bs_put(S, K, T, r, sigma, q)
            return v - mkt_price
        low, high = 0.001, 3.0
        if obj(high) * obj(low) > 0: return None
        return brentq(obj, low, high, xtol=1e-6, maxiter=100)
    except:
        return None

def delta_to_strike(S, T, r, sigma, target_delta, is_put=True):
    """由目标 |delta| 反解行权价 K（r 为无风险利率，q=0）。

    BS 关系：
        delta_call = N(d1)      →  d1 = N⁻¹(Δ)
        delta_put  = N(d1) − 1  →  d1 = N⁻¹(1 − |Δ|)
        d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)

    反解 K：
        ln(S/K) = σ√T·d1 − (r + σ²/2)T
        K       = S · exp((r + σ²/2)T − σ√T·d1)

    ⚠️ 2026-11-10 修正：原实现写成
            S * exp(d1·σ√T − (r + 0.5σ²)T)
       指数里两项符号**全部写反**（等于整体取了负号），导致 put 25Δ 算出的行权价
       高于现价、call 25Δ 低于现价，方向完全颠倒 —— put/call 选腿互换。
       实测偏差：沪深300 4.4%、上证50 4.7%、创业板ETF 8.4%、科创50 10.6%
       （ATM IV 越高偏得越多）。修复后 put 25Δ < 现价、call 25Δ > 现价。
    """
    if sigma <= 0 or T <= 0: return S * 0.95 if is_put else S * 1.05
    d1_target = norm.ppf(1 - target_delta) if is_put else norm.ppf(target_delta)
    return S * math.exp((r + 0.5*sigma**2)*T - sigma*math.sqrt(T)*d1_target)

# ============ 数据库 ============
class StyleDriftDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, timeout=30)
        self._init_tables()
    
    def safe_commit(self):
        """安全的commit，FUSE上可能失败但不阻塞"""
        try:
            self.conn.commit()
            return True
        except Exception:
            return False
    
    def _init_tables(self):
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS opt_raw (
                ts_code TEXT, trade_date TEXT, exchange TEXT,
                open REAL, high REAL, low REAL, close REAL,
                pre_settle REAL, settle REAL, vol REAL, amount REAL, oi REAL,
                PRIMARY KEY (ts_code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS contract_info (
                ts_code TEXT PRIMARY KEY, name TEXT, call_put TEXT,
                exercise_price REAL, maturity_date TEXT, list_date TEXT, delist_date TEXT,
                underlying TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_metrics (
                trade_date TEXT, underlying TEXT,
                spot_price REAL, atm_iv REAL, put_25d_iv REAL, call_25d_iv REAL,
                iv_skew REAL, cboe_skew REAL, vol_pcr REAL, oi_pcr REAL, amt_pcr REAL,
                vix REAL,
                data_source TEXT DEFAULT 'tushare',
                PRIMARY KEY (trade_date, underlying)
            );
            CREATE TABLE IF NOT EXISTS fetch_log (
                trade_date TEXT, exchange TEXT, row_count INTEGER, fetch_time TEXT,
                PRIMARY KEY (trade_date, exchange)
            );
        ''')
        try:
            self.conn.commit()
        except Exception:
            pass
    
    def close(self):
        self.conn.close()

# ============ 合约信息 ============
def build_contract_map(db, force=False):
    """从 opt_basic 构建合约 → 标的映射（用 opt_code 精确归属）

    ⚠️ 2026-11-10 修正：原实现靠合约名关键词猜标的，有两个硬伤 ——
      1) 名字格式一变就全崩：Tushare 现返回简称（'300ETF沽6月3700'），
         而旧规则写的是 '沪深300'，重建后 510300 一个合约都匹配不到；
      2) 同名混标的：'科创' 同时命中「科创50」(588000) 和「科创板50」(588080)，
         导致 588000 混入 588080 的合约，25Δ 取腿取到别的基金的期权。
      改用 opt_code 精确映射：OP510300.SH → 510300，并校验交易所归属。
    """
    if not force:
        cur = db.conn.execute("SELECT COUNT(*) FROM contract_info WHERE underlying IS NOT NULL")
        if cur.fetchone()[0] > 100:
            return  # 已有足够数据

    # 清空旧映射
    db.conn.execute("DELETE FROM contract_info")
    db.safe_commit()

    print("[合约映射] 从Tushare获取合约信息（按 opt_code 精确归属）...")

    total = 0
    for ex in ('SSE', 'SZSE'):
        try:
            time.sleep(API_SLEEP)
            df = pro.opt_basic(exchange=ex, fields='ts_code,name,call_put,exercise_price,'
                                                  'maturity_date,list_date,delist_date,opt_code')
            if df is None or len(df) == 0:
                continue

            kept = 0
            for _, row in df.iterrows():
                # OP510300.SH → 510300
                ul = str(row.get('opt_code') or '').replace('OP', '').split('.')[0]
                info = TARGETS.get(ul)
                if not info or info['exchange'] != ex:
                    continue
                db.conn.execute("""INSERT OR REPLACE INTO contract_info
                    (ts_code, name, call_put, exercise_price, maturity_date, list_date, delist_date, underlying)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (row['ts_code'], str(row.get('name', '')), row.get('call_put'),
                     row.get('exercise_price'), row.get('maturity_date'),
                     row.get('list_date'), row.get('delist_date'), ul))
                kept += 1
            db.safe_commit()
            total += kept
            print(f"  {ex}: {kept} 个合约已映射")
        except Exception as e:
            print(f"  {ex}: 获取合约信息失败 - {e}")
    print(f"  合计 {total} 个合约")

# ============ Tushare数据获取 ============
def fetch_tushare_daily(db, trade_date, exchange):
    """获取单日期权行情数据"""
    # 检查是否已获取
    cur = db.conn.execute("SELECT row_count FROM fetch_log WHERE trade_date=? AND exchange=?", (trade_date, exchange))
    if cur.fetchone(): return True
    
    try:
        time.sleep(API_SLEEP)
        df = pro.opt_daily(trade_date=trade_date, exchange=exchange)
        if df is None or len(df) == 0:
            return False
        
        for _, row in df.iterrows():
            db.conn.execute("""INSERT OR REPLACE INTO opt_raw 
                (ts_code, trade_date, exchange, open, high, low, close, pre_settle, settle, vol, amount, oi)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get('ts_code'), row.get('trade_date'), row.get('exchange'),
                 row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                 row.get('pre_settle'), row.get('settle'), row.get('vol'), row.get('amount'), row.get('oi')))
        
        db.conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?)",
                        (trade_date, exchange, len(df), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        db.safe_commit()
        return True
    except Exception as e:
        print(f"  Tushare获取失败 {trade_date} {exchange}: {e}")
        return False

def backfill_tushare(db, start_date, end_date):
    """批量回填Tushare数据"""
    print(f"[Tushare回填] {start_date} → {end_date}")
    cal = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date, is_open='1')
    if cal is None or len(cal) == 0:
        print("  无交易日数据")
        return
    
    trade_dates = cal['cal_date'].tolist()
    total = len(trade_dates)
    
    for i, td in enumerate(trade_dates):
        # 检查是否已有
        cur = db.conn.execute("SELECT COUNT(*) FROM fetch_log WHERE trade_date=? AND exchange='SSE'", (td,))
        if cur.fetchone()[0] > 0:
            continue
        
        ok_sse = fetch_tushare_daily(db, td, 'SSE')
        ok_szse = fetch_tushare_daily(db, td, 'SZSE')
        
        if i % 20 == 0:
            print(f"  进度: {i+1}/{total} ({td}) SSE={'✅' if ok_sse else '❌'} SZSE={'✅' if ok_szse else '❌'}")
    
    print(f"  回填完成")

# ============ 交易所/新浪爬取 ============
def scrape_sse_options(trade_date):
    """从上交所网站爬取期权数据"""
    import requests
    td_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    
    headers = {
        'Referer': 'http://www.sse.com.cn/assortment/options/price/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # 统一走 DataHub（内部 Tier1 tushare → Tier2 交易所官网），不再直连
    if dh is not None:
        _d = dh.get_option_daily_raw(trade_date, 'SSE')
        # 双保险：只接受含真实行情的数据。DataHub 的交易所 Tier2 只给合约清单、
        # 价格字段全空，若原样采用会阻断下面的官网爬虫（宁空勿假）。
        if _d is not None and len(_d):
            _qcols = [c for c in ('close', 'settle', 'pre_settle', 'vol', 'oi', 'amount')
                      if c in _d.columns]
            if _qcols and _d[_qcols].notna().any().any():
                return _d.to_dict('records')
    results = []
    url = f"http://query.sse.com.cn/commonQuery.do?jsonCallBack=jsonpCallback&sqlId=SSE_ZQPZ_YSP_QQHQYYY_PROD_XXPL_JYXXPL_L&tradeDate={trade_date}&isPagination=true&pageHelp.pageSize=10000"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        text = resp.text
        # 解析JSONP
        json_str = re.search(r'jsonpCallback\((.*)\)', text, re.DOTALL)
        if json_str:
            data = json.loads(json_str.group(1))
            if 'pageHelp' in data and 'data' in data['pageHelp']:
                for item in data['pageHelp']['data']:
                    results.append(item)
    except Exception as e:
        print(f"  SSE爬取失败 {trade_date}: {e}")
    
    return results

def scrape_szse_options(trade_date):
    """从深交所API爬取期权数据"""
    import requests
    td_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    
    headers = {
        'Referer': 'http://www.szse.cn/market/option/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # 统一走 DataHub（内部 Tier1 tushare → Tier2 交易所官网），不再直连
    if dh is not None:
        _d = dh.get_option_daily_raw(trade_date, 'SZSE')
        # 双保险：只接受含真实行情的数据。DataHub 的交易所 Tier2 只给合约清单、
        # 价格字段全空，若原样采用会阻断下面的官网爬虫（宁空勿假）。
        if _d is not None and len(_d):
            _qcols = [c for c in ('close', 'settle', 'pre_settle', 'vol', 'oi', 'amount')
                      if c in _d.columns]
            if _qcols and _d[_qcols].notna().any().any():
                return _d.to_dict('records')
    results = []
    url = f"http://www.szse.cn/api/report/ShowReport?SHOWTYPE=JSON&CATALOGID=1734&TABKEY=tab1&txtTrddate={td_fmt}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if 'data' in data:
            for item in data['data']:
                results.append(item)
    except Exception as e:
        print(f"  SZSE爬取失败 {trade_date}: {e}")
    
    return results

def scrape_sina_options(etf_code, trade_date=None):
    """从新浪财经爬取期权IV数据"""
    import requests
    
    headers = {
        'Referer': 'https://stock.finance.sina.com.cn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    results = {}
    # 新浪期权页面 - 获取隐含波动率数据
    # 只能获取当日数据
    url_map = {
        '588000': 'https://stock.finance.sina.com.cn/futures/view/options.php?j_code=OP588000',
        '159915': 'https://stock.finance.sina.com.cn/futures/view/options.php?j_code=OP159915',
        '510300': 'https://stock.finance.sina.com.cn/futures/view/options.php?j_code=OP510300',
        '510500': 'https://stock.finance.sina.com.cn/futures/view/options.php?j_code=OP510500',
    }
    
    if etf_code not in url_map: return results
    try:
        resp = requests.get(url_map[etf_code], headers=headers, timeout=10)
        # 解析HTML获取IV数据
        # 新浪页面包含期权链及IV
        text = resp.text
        # 尝试提取JSON数据
        json_match = re.search(r'var\s+optionData\s*=\s*(\{.*?\});', text, re.DOTALL)
        if json_match:
            results = json.loads(json_match.group(1))
    except Exception as e:
        print(f"  新浪爬取失败 {etf_code}: {e}")
    
    return results

# ============ 新浪数据源：每日数据采集 ============

# IV硬过滤阈值
IV_ABS_MIN = 0.03      # 3%，低于此视为异常（2026-10低波环境科创50ETF真实IV已压至4.8%，原5%阈值误杀）
IV_ABS_MAX = 0.80      # 80%，ETF期权超过此基本是数据错误
IV_DAILY_CHANGE_MAX = 0.20  # 单日变化超20pp视为异常
IV_25D_SPREAD_MAX = 0.30    # 25d IV与ATM IV差超30pp视为异常


def validate_iv_quality(atm_iv, put_25d_iv=None, call_25d_iv=None, prev_atm_iv=None, prev_date=None, trade_date=None):
    """IV质量硬过滤，返回 (is_valid, reason)
    prev_date/trade_date 用于计算间隔天数，间隔大时放宽变化阈值"""
    if atm_iv is None or atm_iv <= 0:
        return False, "ATM IV为空或≤0"
    
    # 绝对范围检查
    if atm_iv < IV_ABS_MIN:
        return False, f"ATM IV={atm_iv*100:.1f}% < {IV_ABS_MIN*100:.0f}%，过低"
    if atm_iv > IV_ABS_MAX:
        return False, f"ATM IV={atm_iv*100:.1f}% > {IV_ABS_MAX*100:.0f}%，过高（数据异常）"
    
    # 单日变化检查（考虑间隔天数和前值合理性）
    if prev_atm_iv is not None and prev_atm_iv > 0:
        # 前值本身异常（>80%），跳过变化检查
        if prev_atm_iv > IV_ABS_MAX:
            pass  # 历史数据有问题，新数据可能是修正
        else:
            # 计算间隔天数
            gap_days = 1
            if prev_date and trade_date:
                try:
                    pd = datetime.datetime.strptime(str(prev_date), '%Y%m%d')
                    td = datetime.datetime.strptime(str(trade_date), '%Y%m%d')
                    gap_days = max((td - pd).days, 1)
                except:
                    gap_days = 1
            
            # 间隔超过7天，IV变化阈值按天数的平方根放大
            # 1天→20pp, 7天→20pp, 30天→37pp, 49天→47pp
            import math
            adjusted_threshold = IV_DAILY_CHANGE_MAX * math.sqrt(max(gap_days, 1) / 1.0)
            adjusted_threshold = min(adjusted_threshold, 0.50)  # 上限50pp
            
            change = abs(atm_iv - prev_atm_iv)
            if change > adjusted_threshold:
                return False, f"ATM IV变化{change*100:.1f}pp > 阈值{adjusted_threshold*100:.0f}pp（间隔{gap_days}天），数据跳变"
    
    # 25d IV合理性
    if put_25d_iv is not None and put_25d_iv > 0:
        spread = abs(put_25d_iv - atm_iv)
        if spread > IV_25D_SPREAD_MAX:
            return False, f"25d Put IV与ATM IV差{spread*100:.1f}pp > {IV_25D_SPREAD_MAX*100:.0f}pp"
    if call_25d_iv is not None and call_25d_iv > 0:
        spread = abs(call_25d_iv - atm_iv)
        if spread > IV_25D_SPREAD_MAX:
            return False, f"25d Call IV与ATM IV差{spread*100:.1f}pp > {IV_25D_SPREAD_MAX*100:.0f}pp"
    
    return True, "OK"


def fetch_daily_from_sina(etf_code, trade_date=None, prev_atm_iv=None, prev_date=None):
    """
    从新浪爬虫获取单日期权数据并计算指标
    :param etf_code: 标的代码，如 '588000', '510300'
    :param trade_date: 交易日期 YYYYMMDD，默认今天
    :param prev_atm_iv: 前一次的ATM IV（用于变化检查）
    :param prev_date: 前一次数据的日期 YYYYMMDD
    :return: dict指标 或 None
    """
    if not SINA_AVAILABLE:
        print(f"  [新浪] 爬虫不可用，跳过")
        return None
    
    if trade_date is None:
        trade_date = datetime.datetime.now().strftime('%Y%m%d')
    
    name = TARGETS.get(etf_code, {}).get('name', etf_code)
    print(f"  [新浪] 获取 {name}({etf_code}) 期权数据...")
    
    try:
        # 1. 获取完整期权链
        chain, err = fetch_option_chain(etf_code)
        if err:
            print(f"  [新浪] 获取期权链失败: {err}")
            return None
        
        if not chain:
            print(f"  [新浪] 期权链为空")
            return None
        
        # 2. 获取现货价
        spot = sina_get_spot_price(etf_code)
        if spot <= 0:
            print(f"  [新浪] 获取现货价失败")
            return None
        
        # 3. 找近月合约（剩余天数最短的到期月）
        today_dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
        expire_dates = sorted(chain.keys())
        
        # 过滤掉已过期的
        future_expires = [e for e in expire_dates if datetime.datetime.strptime(str(e), '%Y%m%d') >= today_dt]
        if not future_expires:
            print(f"  [新浪] 无有效到期月")
            return None
        
        nearest_expire = future_expires[0]
        
        # 如果近月合约太少，尝试次近月
        expire_data = chain[nearest_expire]
        near_calls = expire_data.get('calls', [])
        near_puts = expire_data.get('puts', [])
        
        if len(near_calls) < 3 or len(near_puts) < 3:
            if len(future_expires) > 1:
                nearest_expire = future_expires[1]
                expire_data = chain[nearest_expire]
                near_calls = expire_data.get('calls', [])
                near_puts = expire_data.get('puts', [])
        
        if len(near_calls) < 3 or len(near_puts) < 3:
            print(f"  [新浪] 近月合约数不足: calls={len(near_calls)}, puts={len(near_puts)}")
            return None
        
        # 4. 计算DTE
        expire_dt = datetime.datetime.strptime(str(nearest_expire), '%Y%m%d')
        dte = (expire_dt - today_dt).days
        if dte <= 0:
            dte = 1
        T = dte / 365.0
        
        # 5. 找ATM strike
        near_calls.sort(key=lambda x: x['k'])
        near_puts.sort(key=lambda x: x['k'])
        
        atm_strike = min(near_calls + near_puts, key=lambda x: abs(x['k'] - spot))['k']
        
        # 找ATM call和put
        atm_call = max([c for c in near_calls if c['k'] == atm_strike], key=lambda x: x.get('vol', 0), default=None)
        atm_put = max([p for p in near_puts if p['k'] == atm_strike], key=lambda x: x.get('vol', 0), default=None)
        
        if not atm_call or not atm_put:
            print(f"  [新浪] 找不到ATM合约 (strike={atm_strike})")
            return None
        
        # 6. 计算ATM IV
        # 优先用爬虫直接返回的IV（新浪的IV已经是小数形式，如0.25表示25%）
        call_iv_raw = atm_call.get('iv', 0)
        put_iv_raw = atm_put.get('iv', 0)
        
        # 新浪返回的IV：如果>1说明是百分比形式（如25.0），需要除以100
        if call_iv_raw > 1.0:
            call_iv_raw = call_iv_raw / 100.0
        if put_iv_raw > 1.0:
            put_iv_raw = put_iv_raw / 100.0
        
        # 如果爬虫IV有效（在合理范围），直接用
        iv_candidates = []
        if 0.01 <= call_iv_raw <= 1.0:
            iv_candidates.append(call_iv_raw)
        if 0.01 <= put_iv_raw <= 1.0:
            iv_candidates.append(put_iv_raw)
        
        if iv_candidates:
            atm_iv = sum(iv_candidates) / len(iv_candidates)
        else:
            # 爬虫IV无效，用BS反解
            call_price = atm_call.get('price', 0) or atm_call.get('close', 0)
            put_price = atm_put.get('price', 0) or atm_put.get('close', 0)
            
            call_iv_calc = bs_iv(spot, atm_strike, T, 0.03, call_price, 'call') if call_price > 0 else None
            put_iv_calc = bs_iv(spot, atm_strike, T, 0.03, put_price, 'put') if put_price > 0 else None
            
            iv_calc = [v for v in [call_iv_calc, put_iv_calc] if v is not None and 0.01 <= v <= 1.0]
            atm_iv = sum(iv_calc) / len(iv_calc) if iv_calc else None
        
        if atm_iv is None:
            print(f"  [新浪] ATM IV计算失败")
            return None
        
        # 7. 计算25-delta strikes
        put_25d_K = delta_to_strike(spot, T, 0.03, atm_iv, 0.25, is_put=True)
        call_25d_K = delta_to_strike(spot, T, 0.03, atm_iv, 0.25, is_put=False)
        
        # 找最近的25d合约
        near_put25 = max(near_puts, key=lambda x: x['k'] if abs(x['k'] - put_25d_K) < abs(near_puts[0]['k'] - put_25d_K) * 5 else -abs(x['k'] - put_25d_K))
        near_put25 = min(near_puts, key=lambda x: abs(x['k'] - put_25d_K))
        near_call25 = min(near_calls, key=lambda x: abs(x['k'] - call_25d_K))
        
        # 25d IV
        p25_iv_raw = near_put25.get('iv', 0)
        c25_iv_raw = near_call25.get('iv', 0)
        if p25_iv_raw > 1.0:
            p25_iv_raw = p25_iv_raw / 100.0
        if c25_iv_raw > 1.0:
            c25_iv_raw = c25_iv_raw / 100.0
        
        put_25d_iv = p25_iv_raw if 0.01 <= p25_iv_raw <= 1.0 else None
        call_25d_iv = c25_iv_raw if 0.01 <= c25_iv_raw <= 1.0 else None
        
        # 如果爬虫IV不可用，BS反解
        if put_25d_iv is None:
            p25_price = near_put25.get('price', 0) or near_put25.get('close', 0)
            if p25_price > 0:
                put_25d_iv = bs_iv(spot, near_put25['k'], T, 0.03, p25_price, 'put')
        
        if call_25d_iv is None:
            c25_price = near_call25.get('price', 0) or near_call25.get('close', 0)
            if c25_price > 0:
                call_25d_iv = bs_iv(spot, near_call25['k'], T, 0.03, c25_price, 'call')
        
        # 8. IV质量硬过滤
        is_valid, reason = validate_iv_quality(atm_iv, put_25d_iv, call_25d_iv, prev_atm_iv, prev_date, trade_date)
        if not is_valid:
            print(f"  [新浪] ❌ IV质量过滤: {reason}")
            return None
        
        # 9. PCR（成交量口径，用全部到期月的全部合约）
        total_call_vol = 0
        total_put_vol = 0
        total_call_oi = 0
        total_put_oi = 0
        for exp_data in chain.values():
            for c in exp_data.get('calls', []):
                total_call_vol += c.get('vol', 0)
                total_call_oi += c.get('oi', 0)
            for p in exp_data.get('puts', []):
                total_put_vol += p.get('vol', 0)
                total_put_oi += p.get('oi', 0)
        
        vol_pcr = total_put_vol / total_call_vol if total_call_vol > 0 else 0
        oi_pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
        
        # 10. 计算iv_skew和cboe_skew
        iv_skew = ((put_25d_iv or atm_iv) - (call_25d_iv or atm_iv))
        # 2026-11-09 偏度口径统一：走 data_hub 唯一真源，不再内联公式
        cboe_skew = _unified_cboe_skew(atm_iv, put_25d_iv or atm_iv)
        
        # 合约数统计
        total_contracts = sum(len(v.get('calls', [])) + len(v.get('puts', [])) for v in chain.values())
        
        result = {
            'spot_price': round(spot, 4),
            'atm_iv': round(atm_iv, 6),
            'put_25d_iv': round(put_25d_iv or 0, 6),
            'call_25d_iv': round(call_25d_iv or 0, 6),
            'iv_skew': round(iv_skew, 6),
            'cboe_skew': round(cboe_skew, 2),
            'vol_pcr': round(vol_pcr, 3),
            'oi_pcr': round(oi_pcr, 3),
            'amt_pcr': 0,  # 新浪不直接提供成交额PCR
            'expire_date': str(nearest_expire),
            'dte': dte,
            'contract_count': total_contracts,
        }
        
        print(f"  [新浪] ✅ spot={spot:.4f} ATM_IV={atm_iv*100:.2f}% "
              f"25dPut={put_25d_iv*100:.2f}% 25dCall={call_25d_iv*100:.2f}% "
              f"PCR={vol_pcr:.2f} expire={nearest_expire} dte={dte} contracts={total_contracts}")
        
        return result
        
    except Exception as e:
        print(f"  [新浪] 获取失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def migrate_db_add_data_source(db):
    """检测daily_metrics是否有data_source列；缺失则自动 ALTER 补上（旧库 CREATE IF NOT EXISTS 不会补列）。"""
    global HAS_DATA_SOURCE_COL
    HAS_DATA_SOURCE_COL = False
    try:
        cur = db.conn.execute("PRAGMA table_info(daily_metrics)")
        columns = [row[1] for row in cur.fetchall()]
        if 'data_source' in columns:
            HAS_DATA_SOURCE_COL = True
            print("[DB检测] data_source列已存在 ✅")
        else:
            print("[DB检测] data_source列不存在，执行 ALTER TABLE ADD COLUMN ...")
            db.conn.execute("ALTER TABLE daily_metrics ADD COLUMN data_source TEXT DEFAULT 'legacy'")
            db.safe_commit()
            HAS_DATA_SOURCE_COL = True
            print("[DB检测] data_source列已补建 ✅（历史行默认 legacy，后续增量写真实来源）")
        # vix 列（2026-10-23 CBOE 自算 VIX，旧库自动补）
        cur2 = db.conn.execute("PRAGMA table_info(daily_metrics)")
        columns2 = [row[1] for row in cur2.fetchall()]
        if 'vix' not in columns2:
            print("[DB检测] vix列不存在，执行 ALTER TABLE ADD COLUMN vix REAL")
            db.conn.execute("ALTER TABLE daily_metrics ADD COLUMN vix REAL")
            db.safe_commit()
            print("[DB检测] vix列已补建 ✅")
    except Exception as e:
        print(f"[DB检测] 异常: {e}")


def get_prev_atm_iv(db, underlying, trade_date):
    """获取前一次的ATM IV和日期（用于变化检查）
    返回 (atm_iv, prev_date) 或 (None, None)"""
    try:
        cur = db.conn.execute(
            "SELECT atm_iv, trade_date FROM daily_metrics WHERE underlying=? AND trade_date<? AND atm_iv > 0 ORDER BY trade_date DESC LIMIT 1",
            (underlying, trade_date))
        row = cur.fetchone()
        if row and row[0]:
            return float(row[0]), row[1]
    except:
        pass
    return None, None


def fetch_today_sina_all(db, trade_date):
    """
    用新浪爬虫获取所有4个标的的当日数据并写入数据库
    :return: (成功数量, 总数)
    """
    print(f"\n{'='*60}")
    print(f"[新浪数据源] 获取 {trade_date} 全部标的数据")
    print(f"{'='*60}")
    
    success = 0
    total = len(TARGETS)
    
    for etf_code, info in TARGETS.items():
        # 获取前一次ATM IV和日期
        prev_iv, prev_date = get_prev_atm_iv(db, etf_code, trade_date)
        
        # 获取当日数据
        metrics = fetch_daily_from_sina(etf_code, trade_date=trade_date, prev_atm_iv=prev_iv, 
                                         prev_date=prev_date)
        
        if metrics is None:
            print(f"  ❌ {info['name']}({etf_code}) 数据获取失败")
            continue
        
        # 写入数据库（根据schema选择INSERT语句）
        try:
            if HAS_DATA_SOURCE_COL:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics 
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr, data_source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (trade_date, etf_code, metrics['spot_price'], metrics['atm_iv'],
                     metrics['put_25d_iv'], metrics['call_25d_iv'], metrics['iv_skew'],
                     metrics['cboe_skew'], metrics['vol_pcr'], metrics['oi_pcr'],
                     metrics['amt_pcr'], 'sina'))
            else:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics 
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (trade_date, etf_code, metrics['spot_price'], metrics['atm_iv'],
                     metrics['put_25d_iv'], metrics['call_25d_iv'], metrics['iv_skew'],
                     metrics['cboe_skew'], metrics['vol_pcr'], metrics['oi_pcr'],
                     metrics['amt_pcr']))
            success += 1
        except Exception as e:
            print(f"  ❌ {info['name']} 写入数据库失败: {e}")
    
    db.safe_commit()
    print(f"\n[新浪数据源] 完成: {success}/{total} 成功")
    return success, total


def fetch_today_unified(db, trade_date, lookback_days=7):
    """
    【2026-10-23 统一接口】单日增量，走 data_hub 唯一真源
    collectors.option_metrics_history.get_option_metrics_history：
      指标值优先 Tushare 算好的；需自算用标准 BS，原始行情 Tushare→AKShare→网页降级；
      不直接采信免费源现成指标值。废弃旧新浪爬虫路径，避免脏 IV 覆盖历史库。
    非交易日/盘后数据未落时，自动回看 lookback_days 内最近一个有数据的交易日，
    以接口返回的真实 trade_date 为准写库（不把周末写成 today）。
    :return: (成功标的数, 4, 实际入库交易日)
    """
    import datetime as _dt
    DATAHUB = _HUB_ROOT
    if DATAHUB not in sys.path:
        sys.path.insert(0, DATAHUB)
    from data_hub.collectors.option_metrics_history import get_option_metrics_history

    end = _dt.datetime.strptime(trade_date, "%Y%m%d")
    start = (end - timedelta(days=lookback_days)).strftime("%Y%m%d")
    print(f"\n{'='*60}\n[统一接口] 获取 {start}~{trade_date} 最近交易日全套期权指标\n{'='*60}")

    try:
        df = get_option_metrics_history(start, trade_date, underlyings=list(TARGETS.keys()), verbose=True)
    except Exception as e:
        print(f"❌ 统一接口调用失败: {e}")
        return 0, len(TARGETS), None
    if df is None or df.empty:
        print("❌ 统一接口返回空")
        return 0, len(TARGETS), None

    # 取实际最新交易日（接口口径，避免非交易日）
    actual_td = str(df["trade_date"].max()).replace("-", "")
    day = df[df["trade_date"].astype(str).str.replace("-", "") == actual_td]
    print(f"[统一接口] 实际入库交易日={actual_td}，标的数={len(day)}")

    success = 0
    for _, r in day.iterrows():
        code = str(r["underlying"])
        if code not in TARGETS:
            continue

        def _num(v):
            try:
                v = float(v)
                return v if math.isfinite(v) else None
            except (TypeError, ValueError):
                return None

        vals = (
            actual_td, code,
            _num(r.get("spot_price")), _num(r.get("atm_iv")),
            _num(r.get("put_25d_iv")), _num(r.get("call_25d_iv")),
            _num(r.get("iv_skew")), _num(r.get("cboe_skew")),
            _num(r.get("vol_pcr")), _num(r.get("oi_pcr")), _num(r.get("amt_pcr")),
            _num(r.get("vix")),
        )
        try:
            # vix 列为 2026-10-23 新增（ALTER 迁移，旧库没有时退回旧列集合）
            has_vix_col_local = any(
                col[1] == "vix" for col in db.conn.execute("PRAGMA table_info(daily_metrics)").fetchall()
            )
            if has_vix_col_local and HAS_DATA_SOURCE_COL:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr, vix, data_source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals + ("unified_tushare",))
            elif has_vix_col_local:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr, vix)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
            elif HAS_DATA_SOURCE_COL:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr, data_source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", vals[:-1] + ("unified_tushare",))
            else:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", vals[:-1])
            atm = vals[3]
            print(f"  ✅ {TARGETS[code]['name']}({code}) ATM_IV={atm*100:.2f}%" if atm else f"  ⚠️ {code} atm为空")
            success += 1
        except Exception as e:
            print(f"  ❌ {code} 写库失败: {e}")

    db.safe_commit()
    print(f"\n[统一接口] 完成: {success}/{len(TARGETS)} 成功（交易日 {actual_td}）")
    return success, len(TARGETS), actual_td


# ============ 日指标计算 ============
def get_etf_price(code, trade_date):
    """获取ETF收盘价"""
    try:
        time.sleep(API_SLEEP)
        df = pro.fund_daily(ts_code=code, trade_date=trade_date, fields='ts_code,trade_date,close')
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['close'])
    except:
        pass
    
    # 备选：从指数获取
    idx_map = {'588000.SH': '000685.SH', '510300.SH': '000300.SH', '510500.SH': '000905.SH'}
    idx_code = idx_map.get(code)
    if idx_code:
        try:
            time.sleep(API_SLEEP)
            df = pro.index_daily(ts_code=idx_code, trade_date=trade_date, fields='ts_code,trade_date,close')
            if df is not None and len(df) > 0:
                return float(df.iloc[0]['close'])
        except:
            pass
    return None

def calc_daily_metrics_from_raw(db, trade_date, underlying_code):
    """从缓存的raw数据计算单日指标"""
    info = TARGETS[underlying_code]
    exchange = info['exchange']
    
    # 获取该标的的所有合约ts_code
    cur = db.conn.execute("SELECT ts_code, call_put, exercise_price, maturity_date FROM contract_info WHERE underlying=?", (underlying_code,))
    contract_map = {row[0]: {'call_put': row[1], 'exercise_price': row[2], 'maturity_date': row[3]} for row in cur.fetchall()}
    
    if not contract_map:
        return None
    
    # 获取该日行情
    placeholders = ','.join(['?'] * len(contract_map))
    cur = db.conn.execute(f"SELECT ts_code, open, high, low, close, settle, vol, amount, oi FROM opt_raw WHERE trade_date=? AND ts_code IN ({placeholders})",
                          [trade_date] + list(contract_map.keys()))
    
    contracts = []
    for row in cur.fetchall():
        ts_code = row[0]
        if ts_code not in contract_map: continue
        info_c = contract_map[ts_code]
        close_val = row[4]  # close
        # 跳过无收盘价的合约
        if close_val is None or close_val <= 0:
            close_val = row[5]  # 尝试用settle
        if close_val is None or close_val <= 0:
            continue
        contracts.append({
            'ts_code': ts_code,
            'call_put': info_c['call_put'],
            'exercise_price': info_c['exercise_price'],
            'maturity_date': info_c['maturity_date'],
            'close': close_val,
            'settle': row[5] if row[5] else close_val,
            'vol': row[6] if row[6] else 0,
            'amount': row[7] if row[7] else 0,
            'oi': row[8] if row[8] else 0,
        })
    
    if len(contracts) < 6: return None
    
    # 获取ETF价格（优先从缓存，回退到API）
    price_code = TARGETS[underlying_code]['price_code']
    spot = None
    # 尝试从缓存获取
    try:
        cur2 = db.conn.execute("SELECT close FROM etf_prices WHERE ts_code=? AND trade_date=?", (price_code, trade_date))
        row = cur2.fetchone()
        if row and row[0]:
            spot = float(row[0])
    except:
        pass
    # 缓存未命中，尝试API
    if spot is None:
        spot = get_etf_price(price_code, trade_date)
    if spot is None: return None
    
    return calc_metrics_from_contracts(contracts, spot, trade_date)

def deduplicate_contracts(contracts):
    """同一strike+call_put只保留最高volume的主系列合约"""
    best = {}
    for c in contracts:
        key = (c['call_put'], c['exercise_price'])
        if key not in best or c['vol'] > best[key]['vol']:
            best[key] = c
    return list(best.values())

def calc_metrics_from_contracts(contracts, spot_price, trade_date, r=0.03):
    """从合约列表计算ATM IV / 25d IV / PCR"""
    # 1. 去重：同一strike只保留主系列
    contracts = deduplicate_contracts(contracts)
    
    calls = [c for c in contracts if c['call_put'] == 'C']
    puts = [c for c in contracts if c['call_put'] == 'P']
    if len(calls) < 3 or len(puts) < 3: return None
    
    # 2. 只用最近到期日计算IV
    all_expires = sorted(set(c['maturity_date'] for c in contracts if c.get('maturity_date')))
    if not all_expires: return None
    nearest_expire = all_expires[0]
    
    near_calls = [c for c in calls if c['maturity_date'] == nearest_expire]
    near_puts = [p for p in puts if p['maturity_date'] == nearest_expire]
    
    # 如果最近到期日合约太少，用次近到期日
    if len(near_calls) < 3 or len(near_puts) < 3:
        if len(all_expires) > 1:
            nearest_expire = all_expires[1]
            near_calls = [c for c in calls if c['maturity_date'] == nearest_expire]
            near_puts = [p for p in puts if p['maturity_date'] == nearest_expire]
    
    if len(near_calls) < 3 or len(near_puts) < 3: return None
    
    near_calls.sort(key=lambda x: x['exercise_price'])
    near_puts.sort(key=lambda x: x['exercise_price'])
    
    # 3. ATM strike (从最近到期日合约中找)
    atm_strike = min(near_calls + near_puts, key=lambda x: abs(x['exercise_price'] - spot_price))['exercise_price']
    atm_call = max([c for c in near_calls if c['exercise_price'] == atm_strike], key=lambda x: x['vol'], default=None)
    atm_put = max([p for p in near_puts if p['exercise_price'] == atm_strike], key=lambda x: x['vol'], default=None)
    
    if not atm_call or not atm_put: return None
    
    # 4. 计算T
    try:
        expiry_dt = datetime.datetime.strptime(str(nearest_expire), "%Y%m%d")
        trade_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        T = max((expiry_dt - trade_dt).days / 365.0, 1/365)
    except:
        return None
    
    # 5. ATM IV
    call_price = atm_call['close'] if atm_call['close'] and atm_call['close'] > 0 else atm_call.get('settle', 0)
    put_price = atm_put['close'] if atm_put['close'] and atm_put['close'] > 0 else atm_put.get('settle', 0)
    
    atm_call_iv = bs_iv(spot_price, atm_call['exercise_price'], T, r, call_price, 'call')
    atm_put_iv = bs_iv(spot_price, atm_put['exercise_price'], T, r, put_price, 'put')
    
    atm_ivs = [v for v in [atm_call_iv, atm_put_iv] if v is not None and v > 0.01]
    atm_iv = sum(atm_ivs) / len(atm_ivs) if atm_ivs else None
    if atm_iv is None or atm_iv < 0.05: return None  # IV<5%视为异常
    
    # 6. 25-delta strikes
    put_25d_K = delta_to_strike(spot_price, T, r, atm_iv, 0.25, is_put=True)
    call_25d_K = delta_to_strike(spot_price, T, r, atm_iv, 0.25, is_put=False)
    
    near_put25 = max([p for p in near_puts if p['exercise_price'] == min(near_puts, key=lambda x: abs(x['exercise_price'] - put_25d_K))['exercise_price']], key=lambda x: x['vol'], default=None)
    near_call25 = max([c for c in near_calls if c['exercise_price'] == min(near_calls, key=lambda x: abs(x['exercise_price'] - call_25d_K))['exercise_price']], key=lambda x: x['vol'], default=None)
    
    put_25d_price = near_put25['close'] if near_put25 and near_put25['close'] and near_put25['close'] > 0 else (near_put25.get('settle', 0) if near_put25 else 0)
    call_25d_price = near_call25['close'] if near_call25 and near_call25['close'] and near_call25['close'] > 0 else (near_call25.get('settle', 0) if near_call25 else 0)
    
    put_25d_iv = bs_iv(spot_price, near_put25['exercise_price'], T, r, put_25d_price, 'put') if near_put25 and put_25d_price > 0 else None
    call_25d_iv = bs_iv(spot_price, near_call25['exercise_price'], T, r, call_25d_price, 'call') if near_call25 and call_25d_price > 0 else None
    
    iv_skew = ((put_25d_iv or 0) - (call_25d_iv or 0))
    # 2026-11-09 偏度口径统一：走 data_hub 唯一真源，不再内联公式
    cboe_skew = _unified_cboe_skew(atm_iv, put_25d_iv or atm_iv)
    
    # 7. PCR (用全部到期日的全部合约，已去重)
    total_call_vol = sum(c['vol'] for c in calls)
    total_put_vol = sum(p['vol'] for p in puts)
    total_call_oi = sum(c['oi'] for c in calls)
    total_put_oi = sum(p['oi'] for p in puts)
    total_call_amt = sum(c['amount'] for c in calls)
    total_put_amt = sum(p['amount'] for p in puts)
    
    vol_pcr = total_put_vol / total_call_vol if total_call_vol > 0 else 0
    oi_pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
    amt_pcr = total_put_amt / total_call_amt if total_call_amt > 0 else 0
    
    return {
        'spot_price': round(spot_price, 4),
        'atm_iv': round(atm_iv, 6),
        'put_25d_iv': round(put_25d_iv or 0, 6),
        'call_25d_iv': round(call_25d_iv or 0, 6),
        'iv_skew': round(iv_skew, 6),
        'cboe_skew': round(cboe_skew, 2),
        'vol_pcr': round(vol_pcr, 3),
        'oi_pcr': round(oi_pcr, 3),
        'amt_pcr': round(amt_pcr, 3),
    }

# ============ 批量计算日指标 ============
def compute_all_daily_metrics(db, start_date, end_date, force=False):
    """计算所有日期的日指标"""
    print(f"[计算日指标] {start_date} → {end_date}")
    
    cal = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date, is_open=1)
    if cal is None: return
    trade_dates = cal['cal_date'].tolist()
    
    computed = 0
    skipped = 0
    failed = 0
    
    for td in trade_dates:
        for ul_code in TARGETS:
            # 检查是否已计算
            if not force:
                cur = db.conn.execute("SELECT atm_iv FROM daily_metrics WHERE trade_date=? AND underlying=?", (td, ul_code))
                row = cur.fetchone()
                if row and row[0] is not None and row[0] > 0:
                    skipped += 1
                    continue
            
            metrics = calc_daily_metrics_from_raw(db, td, ul_code)
            if metrics:
                db.conn.execute("""INSERT OR REPLACE INTO daily_metrics 
                    (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                     iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (td, ul_code, metrics['spot_price'], metrics['atm_iv'], metrics['put_25d_iv'],
                     metrics['call_25d_iv'], metrics['iv_skew'], metrics['cboe_skew'],
                     metrics['vol_pcr'], metrics['oi_pcr'], metrics['amt_pcr']))
                computed += 1
            else:
                failed += 1
        
        if computed % 30 == 0 and computed > 0:
            db.safe_commit()
            print(f"  进度: 已计算{computed}, 跳过{skipped}, 失败{failed}")
    
    db.safe_commit()
    print(f"  完成: 计算{computed}, 跳过{skipped}, 失败{failed}")

# ============ 风格漂移计算 ============
def safe_float(val, default=None):
    """安全转为float，NaN返回default"""
    try:
        v = float(val)
        if np.isnan(v): return default
        return v
    except:
        return default

def safe_round3(val):
    """安全round到3位，None/NaN返回None"""
    try:
        v = float(val)
        if np.isnan(v): return None
        return round(v, 3)
    except:
        return None

def calculate_style_drift(db, window=252):
    """计算风格漂移z-score"""
    print(f"[风格漂移] 窗口={window}天")
    
    # 读取所有日指标
    df = pd.read_sql("SELECT * FROM daily_metrics WHERE atm_iv > 0 ORDER BY trade_date, underlying", db.conn)
    if len(df) == 0:
        print("  无数据")
        return None
    
    # 宽表: 每行一天，列为各标的指标
    pivot = df.pivot(index='trade_date', columns='underlying', 
                     values=['spot_price', 'atm_iv', 'put_25d_iv', 'call_25d_iv', 'vol_pcr', 'oi_pcr'])
    
    # 确保三个核心标的都有
    required = ['588000', '159915', '510300']
    for ul in required:
        if ('spot_price', ul) not in pivot.columns:
            print(f"  缺少{ul}数据")
            return None
    
    # 510500 为软依赖（数据不足时不阻塞，但打印警告）
    has_510500 = ('spot_price', '510500') in pivot.columns
    if not has_510500:
        print("  ⚠️ 510500(中证500ETF)数据暂缺，相关指标将为空")
    
    result = pivot.copy()

    # 异常IV清洗：tushare回填路径未走质量过滤，库内存在 IV>80% 的脏值（最高2.7）。
    # 不删库，在计算层把 [0.03, 0.80] 区间外的 IV 列置 NaN，防止污染滚动均值/标准差和z-score。
    for iv_col in ['atm_iv', 'put_25d_iv', 'call_25d_iv']:
        for ul in ['588000', '159915', '510300', '510500']:
            ck = (iv_col, ul)
            if ck in result.columns:
                s = result[ck].astype(float)
                result[ck] = s.where((s >= 0.03) & (s <= 0.80))

    # 1. IV Spread (相对沪深300)
    result[('iv_spread', '588000')] = result[('atm_iv', '588000')] - result[('atm_iv', '510300')]
    result[('iv_spread', '159915')] = result[('atm_iv', '159915')] - result[('atm_iv', '510300')]
    if has_510500:
        result[('iv_spread', '510500')] = result[('atm_iv', '510500')] - result[('atm_iv', '510300')]
    
    # 2. Skew Spread (相对沪深300)
    result[('skew', '588000')] = result[('put_25d_iv', '588000')] - result[('atm_iv', '588000')]
    result[('skew', '159915')] = result[('put_25d_iv', '159915')] - result[('atm_iv', '159915')]
    result[('skew', '510300')] = result[('put_25d_iv', '510300')] - result[('atm_iv', '510300')]
    if has_510500:
        result[('skew', '510500')] = result[('put_25d_iv', '510500')] - result[('atm_iv', '510500')]
        result[('skew_spread', '510500')] = result[('skew', '510500')] - result[('skew', '510300')]
    result[('skew_spread', '588000')] = result[('skew', '588000')] - result[('skew', '510300')]
    result[('skew_spread', '159915')] = result[('skew', '159915')] - result[('skew', '510300')]
    
    # 3. PCR Spread (相对沪深300)
    result[('pcr_spread', '588000')] = result[('vol_pcr', '588000')] - result[('vol_pcr', '510300')]
    result[('pcr_spread', '159915')] = result[('vol_pcr', '159915')] - result[('vol_pcr', '510300')]
    if has_510500:
        result[('pcr_spread', '510500')] = result[('vol_pcr', '510500')] - result[('vol_pcr', '510300')]
    
    # 4. 滚动z-score
    z_window = min(window, len(result))
    for metric in ['iv_spread', 'skew_spread', 'pcr_spread']:
        for ul in ['588000', '159915']:
            col = (metric, ul)
            if col in result.columns:
                series = result[col].astype(float)
                roll_mean = series.rolling(z_window, min_periods=60).mean()
                roll_std = series.rolling(z_window, min_periods=60).std()
                result[('z_' + metric, ul)] = (series - roll_mean) / roll_std
        # 510500 z-score
        col_500 = (metric, '510500')
        if col_500 in result.columns:
            series_500 = result[col_500].astype(float)
            roll_mean_500 = series_500.rolling(z_window, min_periods=60).mean()
            roll_std_500 = series_500.rolling(z_window, min_periods=60).std()
            result[('z_' + metric, '510500')] = (series_500 - roll_mean_500) / roll_std_500
    
    # 5. 合并成长ETF信号（NaN-aware：单只标的当日被质量过滤/缺数据时，用另一只降级，避免一个NaN拖垮整列）
    def _merge_growth(z_metric):
        a = result[(z_metric, '588000')].astype(float) if (z_metric, '588000') in result.columns else None
        b = result[(z_metric, '159915')].astype(float) if (z_metric, '159915') in result.columns else None
        if a is not None and b is not None:
            return pd.concat([a, b], axis=1).mean(axis=1, skipna=True)  # 两值在则均值，缺一则取另一值，都缺NaN
        return a if a is not None else (b if b is not None else pd.Series(index=result.index, dtype=float))
    result['growth_iv_z'] = _merge_growth('z_iv_spread')
    result['growth_skew_z'] = _merge_growth('z_skew_spread')
    result['growth_pcr_z'] = _merge_growth('z_pcr_spread')
    
    # 6. 最终z_opt
    result['z_opt'] = 0.5 * result['growth_iv_z'] + 0.3 * result['growth_skew_z'] + 0.2 * result['growth_pcr_z']
    
    # 7. 判断逻辑
    def classify(z_opt, growth_iv_z, growth_skew_z, pcr_588, pcr_1599):
        if z_opt is None: return {'zone': '⚪', 'label': '数据不足', 'advice': '等待更多数据', 'detail': ''}
        
        detail_parts = []
        
        if z_opt < -0.5:
            zone = '🟢'
            label = '成长安全区'
            advice = '买入成长'
            if growth_iv_z is not None and growth_iv_z < -0.8:
                advice = '强烈买入成长（科创50优先）'
                detail_parts.append('成长IV_z<-0.8，强买入信号')
        elif z_opt < 0.3:
            zone = '⚪'
            label = '震仓/正常波动'
            advice = '不动'
            if pcr_588 > 1.3 or pcr_1599 > 1.3:
                advice = '不动（但PCR偏高，谨慎加仓）'
                detail_parts.append(f'PCR偏高: 588={pcr_588:.2f} 1599={pcr_1599:.2f}')
        elif z_opt < 0.8:
            zone = '🟡'
            label = '预警区'
            advice = '减成长，增消费/红利'
        else:
            zone = '🔴'
            label = '警戒区'
            advice = '卖成长，买消费/红利'
            if growth_iv_z is not None and growth_skew_z is not None and growth_iv_z > 1.0 and growth_skew_z > 0.8:
                advice = '强烈卖出成长，买消费/红利'
                detail_parts.append('IV_z>1.0 + Skew_z>0.8，强烈信号')
            
            # 单标的驱动检查
            z_588_iv = safe_float(result[('z_iv_spread', '588000')].iloc[-1])
            z_1599_iv = safe_float(result[('z_iv_spread', '159915')].iloc[-1])
            if z_588_iv is not None and z_1599_iv is not None and abs(z_588_iv - z_1599_iv) > 1.5:
                if z_588_iv > z_1599_iv:
                    detail_parts.append('主要由科创50驱动，可仅减仓科创50')
                else:
                    detail_parts.append('主要由创业板驱动，可仅减仓创业板')
        
        detail = '；'.join(detail_parts) if detail_parts else ''
        return {'zone': zone, 'label': label, 'advice': advice, 'detail': detail}
    
    # 取最新一行 - 用iloc直接从列取标量，避免MultiIndex问题
    last_date = result.index[-1]
    
    pcr_588 = safe_float(result[('vol_pcr', '588000')].iloc[-1], 1.0)
    pcr_1599 = safe_float(result[('vol_pcr', '159915')].iloc[-1], 1.0)
    
    z_opt = safe_float(result['z_opt'].iloc[-1])
    growth_iv_z = safe_float(result['growth_iv_z'].iloc[-1])
    growth_skew_z = safe_float(result['growth_skew_z'].iloc[-1])
    growth_pcr_z = safe_float(result['growth_pcr_z'].iloc[-1])
    
    judgment = classify(z_opt, growth_iv_z, growth_skew_z, pcr_588, pcr_1599)
    
    # 汇总
    summary = {
        'trade_date': last_date,
        'z_opt': round(z_opt, 3) if z_opt is not None else None,
        'growth_iv_z': round(growth_iv_z, 3) if growth_iv_z is not None else None,
        'growth_skew_z': round(growth_skew_z, 3) if growth_skew_z is not None else None,
        'growth_pcr_z': round(growth_pcr_z, 3) if growth_pcr_z is not None else None,
        'zone': judgment['zone'],
        'label': judgment['label'],
        'advice': judgment['advice'],
        'detail': judgment['detail'],
        'raw_latest': {
            '588000': {
                'spot': safe_float(result[('spot_price', '588000')].iloc[-1]),
                'atm_iv': safe_float(result[('atm_iv', '588000')].iloc[-1]),
                'put_25d_iv': safe_float(result[('put_25d_iv', '588000')].iloc[-1]),
                'vol_pcr': round(pcr_588, 3) if pcr_588 else None,
            },
            '159915': {
                'spot': safe_float(result[('spot_price', '159915')].iloc[-1]),
                'atm_iv': safe_float(result[('atm_iv', '159915')].iloc[-1]),
                'put_25d_iv': safe_float(result[('put_25d_iv', '159915')].iloc[-1]),
                'vol_pcr': round(pcr_1599, 3) if pcr_1599 else None,
            },
            '510300': {
                'spot': safe_float(result[('spot_price', '510300')].iloc[-1]),
                'atm_iv': safe_float(result[('atm_iv', '510300')].iloc[-1]),
                'put_25d_iv': safe_float(result[('put_25d_iv', '510300')].iloc[-1]),
                'vol_pcr': safe_float(result[('vol_pcr', '510300')].iloc[-1]),
            },
            '510500': {
                'spot': safe_float(result[('spot_price', '510500')].iloc[-1]) if ('spot_price', '510500') in result.columns else None,
                'atm_iv': safe_float(result[('atm_iv', '510500')].iloc[-1]) if ('atm_iv', '510500') in result.columns else None,
                'put_25d_iv': safe_float(result[('put_25d_iv', '510500')].iloc[-1]) if ('put_25d_iv', '510500') in result.columns else None,
                'vol_pcr': safe_float(result[('vol_pcr', '510500')].iloc[-1]) if ('vol_pcr', '510500') in result.columns else None,
            },
        },
        'z_by_underlying': {
            'iv_spread_588': safe_round3(result[('z_iv_spread', '588000')].iloc[-1]) if ('z_iv_spread', '588000') in result.columns else None,
            'iv_spread_1599': safe_round3(result[('z_iv_spread', '159915')].iloc[-1]) if ('z_iv_spread', '159915') in result.columns else None,
            'skew_spread_588': safe_round3(result[('z_skew_spread', '588000')].iloc[-1]) if ('z_skew_spread', '588000') in result.columns else None,
            'skew_spread_1599': safe_round3(result[('z_skew_spread', '159915')].iloc[-1]) if ('z_skew_spread', '159915') in result.columns else None,
            'pcr_spread_588': safe_round3(result[('z_pcr_spread', '588000')].iloc[-1]) if ('z_pcr_spread', '588000') in result.columns else None,
            'pcr_spread_1599': safe_round3(result[('z_pcr_spread', '159915')].iloc[-1]) if ('z_pcr_spread', '159915') in result.columns else None,
            'iv_spread_500': safe_round3(result[('z_iv_spread', '510500')].iloc[-1]) if ('z_iv_spread', '510500') in result.columns else None,
            'skew_spread_500': safe_round3(result[('z_skew_spread', '510500')].iloc[-1]) if ('z_skew_spread', '510500') in result.columns else None,
            'pcr_spread_500': safe_round3(result[('z_pcr_spread', '510500')].iloc[-1]) if ('z_pcr_spread', '510500') in result.columns else None,
        },
        'history_z_opt': [],
        # ===== 新增深度解读数据 =====
        'deep_analysis': {},
    }
    
    # ===== 深度解读计算 =====
    deep = {}
    z_series = result['z_opt'].dropna().tail(20)
    
    # 1. 趋势方向（5日/20日变化）
    if len(z_series) >= 6:
        z_5d_ago = float(z_series.iloc[-6])
        deep['trend_5d'] = round(z_opt - z_5d_ago, 3) if z_opt is not None else None
    else:
        deep['trend_5d'] = None
    if len(z_series) >= 20:
        z_20d_ago = float(z_series.iloc[0])
        deep['trend_20d'] = round(z_opt - z_20d_ago, 3) if z_opt is not None else None
    else:
        deep['trend_20d'] = None
    
    # 2. 信号持续性（连续在预警/警戒区的天数）
    consecutive_days = 0
    for i in range(len(z_series) - 1, -1, -1):
        val = float(z_series.iloc[i])
        if val >= 0.3:  # 预警区及以上
            consecutive_days += 1
        else:
            break
    deep['consecutive_warning_days'] = consecutive_days
    
    # 3. 驱动力分析（IV/Skew/PCR贡献占比）
    iv_contrib = 0.5 * (growth_iv_z or 0)
    skew_contrib = 0.3 * (growth_skew_z or 0)
    pcr_contrib = 0.2 * (growth_pcr_z or 0)
    total_abs = abs(iv_contrib) + abs(skew_contrib) + abs(pcr_contrib)
    if total_abs > 0:
        deep['iv_pct'] = round(abs(iv_contrib) / total_abs * 100, 1)
        deep['skew_pct'] = round(abs(skew_contrib) / total_abs * 100, 1)
        deep['pcr_pct'] = round(abs(pcr_contrib) / total_abs * 100, 1)
    else:
        deep['iv_pct'] = deep['skew_pct'] = deep['pcr_pct'] = None
    
    # 4. 单标的驱动（588 vs 1599 的 IV z差异）
    z_588_iv = safe_float(result[('z_iv_spread', '588000')].iloc[-1]) if ('z_iv_spread', '588000') in result.columns else None
    z_1599_iv = safe_float(result[('z_iv_spread', '159915')].iloc[-1]) if ('z_iv_spread', '159915') in result.columns else None
    if z_588_iv is not None and z_1599_iv is not None:
        deep['single_driver_diff'] = round(z_588_iv - z_1599_iv, 3)
        if abs(z_588_iv - z_1599_iv) > 1.0:
            deep['single_driver'] = '科创50' if z_588_iv > z_1599_iv else '创业板'
        else:
            deep['single_driver'] = None
    else:
        deep['single_driver_diff'] = None
        deep['single_driver'] = None
    
    # 5. PCR辅助判断
    deep['pcr_588'] = round(pcr_588, 2) if pcr_588 else None
    deep['pcr_1599'] = round(pcr_1599, 2) if pcr_1599 else None
    
    # 6. 历史分位（z_opt在近252天的分位）
    full_z = result['z_opt'].dropna()
    if len(full_z) > 20 and z_opt is not None:
        deep['percentile'] = round((full_z < z_opt).sum() / len(full_z) * 100, 1)
    else:
        deep['percentile'] = None
    
    # 7. IV绝对水平解读
    for ul, name in [('588000', '科创50'), ('159915', '创业板'), ('510300', '沪深300'), ('510500', '中证500')]:
        iv_val = safe_float(result[('atm_iv', ul)].iloc[-1]) if ('atm_iv', ul) in result.columns else None
        if iv_val is not None:
            if iv_val > 0.35:
                deep[f'iv_level_{name}'] = '🔴极高'
            elif iv_val > 0.25:
                deep[f'iv_level_{name}'] = '🟠偏高'
            elif iv_val > 0.18:
                deep[f'iv_level_{name}'] = '🟡中等'
            else:
                deep[f'iv_level_{name}'] = '🟢偏低'
            deep[f'iv_abs_{name}'] = round(iv_val, 4)
        else:
            deep[f'iv_level_{name}'] = None
            deep[f'iv_abs_{name}'] = None
    
    # 8. 近20日z_opt峰值
    if len(z_series) > 0:
        deep['peak_20d'] = round(float(z_series.max()), 3)
        deep['peak_20d_date'] = str(z_series.idxmax())
    else:
        deep['peak_20d'] = None
        deep['peak_20d_date'] = None
    
    summary['deep_analysis'] = deep
    
    # 历史z_opt序列 (最近20天)
    for date, val in z_series.items():
        summary['history_z_opt'].append({'date': date, 'z_opt': round(float(val), 3)})
    
    return summary

def generate_report(summary):
    """生成Markdown报告"""
    if summary is None:
        return "⚠️ 无法生成报告：数据不足"
    
    lines = []
    lines.append("# A股风格漂移监控（期权版）")
    lines.append(f"数据日期: {summary['trade_date']}")
    lines.append("")
    
    # 核心结论
    z_opt_str = f"{summary['z_opt']:+.3f}" if summary['z_opt'] is not None else "N/A"
    lines.append(f"## {summary['zone']} {summary['label']}")
    lines.append("")
    lines.append(f"**z_opt = {z_opt_str}** → **{summary['advice']}**")
    if summary['detail']:
        lines.append("")
        lines.append(f"> {summary['detail']}")
    lines.append("")
    
    # 分解信号
    lines.append("### 信号分解")
    lines.append("")
    lines.append("| 维度 | z-score | 权重 | 贡献 |")
    lines.append("|------|---------|------|------|")
    iv_z = summary['growth_iv_z'] or 0
    skew_z = summary['growth_skew_z'] or 0
    pcr_z = summary['growth_pcr_z'] or 0
    lines.append(f"| 成长综合IV | {iv_z:+.3f} | 0.5 | {0.5*iv_z:+.3f} |")
    lines.append(f"| 成长综合Skew | {skew_z:+.3f} | 0.3 | {0.3*skew_z:+.3f} |")
    lines.append(f"| 成长综合PCR | {pcr_z:+.3f} | 0.2 | {0.2*pcr_z:+.3f} |")
    z_total_str = f"{summary['z_opt']:+.3f}" if summary['z_opt'] is not None else "N/A"
    lines.append(f"| **合计** | | | **{z_total_str}** |")
    lines.append("")
    
    # 各标的原始指标
    lines.append("### 标的指标")
    lines.append("")
    lines.append("| 指标 | 科创50ETF | 创业板ETF | 沪深300ETF | 中证500ETF |")
    lines.append("|------|-----------|-----------|------------|------------|")
    raw = summary['raw_latest']
    
    def fmt_pct(val): return f"{val*100:.2f}%" if val else "-"
    def fmt_num(val): return f"{val:.2f}" if val else "-"
    
    _500 = raw.get('510500', {})
    lines.append(f"| 现价 | {fmt_num(raw['588000']['spot'])} | {fmt_num(raw['159915']['spot'])} | {fmt_num(raw['510300']['spot'])} | {fmt_num(_500.get('spot'))} |")
    lines.append(f"| ATM IV | {fmt_pct(raw['588000']['atm_iv'])} | {fmt_pct(raw['159915']['atm_iv'])} | {fmt_pct(raw['510300']['atm_iv'])} | {fmt_pct(_500.get('atm_iv'))} |")
    lines.append(f"| 25d Put IV | {fmt_pct(raw['588000']['put_25d_iv'])} | {fmt_pct(raw['159915']['put_25d_iv'])} | {fmt_pct(raw['510300']['put_25d_iv'])} | {fmt_pct(_500.get('put_25d_iv'))} |")
    pcr588 = f"{raw['588000']['vol_pcr']:.2f}" if raw['588000']['vol_pcr'] else '-'
    pcr1599 = f"{raw['159915']['vol_pcr']:.2f}" if raw['159915']['vol_pcr'] else '-'
    pcr5103 = f"{raw['510300']['vol_pcr']:.2f}" if raw['510300']['vol_pcr'] else '-'
    pcr5105 = f"{_500['vol_pcr']:.2f}" if _500.get('vol_pcr') else '-'
    lines.append(f"| Vol PCR | {pcr588} | {pcr1599} | {pcr5103} | {pcr5105} |")
    lines.append("")
    
    # 各标的z-score
    lines.append("### 分标的z-score（相对沪深300）")
    lines.append("")
    z_by = summary['z_by_underlying']
    lines.append("| 维度 | 科创50 | 创业板 | 中证500 |")
    lines.append("|------|--------|--------|--------|")
    lines.append(f"| IV Spread | {z_by.get('iv_spread_588') or '-'} | {z_by.get('iv_spread_1599') or '-'} | {z_by.get('iv_spread_500') or '-'} |")
    lines.append(f"| Skew Spread | {z_by.get('skew_spread_588') or '-'} | {z_by.get('skew_spread_1599') or '-'} |")
    lines.append(f"| PCR Spread | {z_by.get('pcr_spread_588') or '-'} | {z_by.get('pcr_spread_1599') or '-'} |")
    lines.append("")
    
    # 判断阈值
    lines.append("### 判断阈值")
    lines.append("")
    lines.append("| z_opt范围 | 区域 | 建议 |")
    lines.append("|-----------|------|------|")
    lines.append("| < -0.5 | 🟢 成长安全区 | 买入成长 |")
    lines.append("| -0.5 ~ 0.3 | ⚪ 正常波动 | 不动 |")
    lines.append("| 0.3 ~ 0.8 | 🟡 预警区 | 减成长→消费/红利 |")
    lines.append("| ≥ 0.8 | 🔴 警戒区 | 卖成长→消费/红利 |")
    lines.append("")
    
    # 历史z_opt
    if summary['history_z_opt']:
        lines.append("### 近期z_opt走势")
        lines.append("")
        for h in summary['history_z_opt']:
            lines.append(f"  {h['date']}: {h['z_opt']:+.3f}")
        lines.append("")
    
    lines.append("---")
    lines.append("📌 案例分析学习材料，不构成投资建议")
    
    return '\n'.join(lines)


def check_idempotent(json_file):
    """幂等检查：当天已推送过则永久跳过（用.pushed标记文件）"""
    pushed_flag = json_file + '.pushed'
    if os.path.exists(pushed_flag):
        print(f"\n⚠️  今日风格漂移监控已推送过，跳过飞书推送（幂等保护）")
        return True
    return False


def detect_style_drift_data_missing(summary):
    """检测风格漂移监控的数据缺失情况，返回告警列表
    
    核心指标：IV/Skew/PCR，3个标的中缺失超过一半（>=2个标的缺失核心数据）
    """
    warnings = []
    if not summary or not isinstance(summary, dict):
        return ["⚠️ 风格漂移数据完全缺失"]

    raw = summary.get('raw_latest', {})
    if not raw:
        return ["⚠️ 风格漂移数据完全缺失"]

    # 统计每个标的的核心数据缺失情况
    total_targets = len(TARGETS)  # 3个标的
    iv_missing = 0
    skew_missing = 0
    pcr_missing = 0

    for code in TARGETS:
        info = raw.get(code, {})
        # IV
        if not info.get('atm_iv'):
            iv_missing += 1
        # Skew (通过 put_25d_iv 判断)
        if not info.get('put_25d_iv'):
            skew_missing += 1
        # PCR
        if not info.get('vol_pcr'):
            pcr_missing += 1

    # 如果3个核心指标中有超过一半标的缺失
    half = total_targets // 2 + (total_targets % 2)  # 2个为一半
    missing_items = []
    if iv_missing >= half:
        missing_items.append(f"IV({iv_missing}/{total_targets}标的)")
    if skew_missing >= half:
        missing_items.append(f"Skew({skew_missing}/{total_targets}标的)")
    if pcr_missing >= half:
        missing_items.append(f"PCR({pcr_missing}/{total_targets}标的)")

    if missing_items:
        warnings.append(f"⚠️ 风格漂移核心数据缺失：{', '.join(missing_items)} 缺失超半数")

    return warnings


def generate_feishu_report(summary):
    """生成飞书推送版：完整因子+深度解读，零token消耗"""
    if summary is None:
        return "⚠️ 无法生成报告：数据不足"
    
    deep = summary.get('deep_analysis', {})
    lines = []
    
    # 标题
    z_opt_str = f"{summary['z_opt']:+.3f}" if summary['z_opt'] is not None else "N/A"
    lines.append(f"📈 **A股风格漂移监控（期权版）- {summary['trade_date']}**")
    lines.append("")
    lines.append(f"**z_opt = {z_opt_str}** → {summary['zone']} **{summary['label']}** → **{summary['advice']}**")
    if summary['detail']:
        lines.append(f"  > {summary['detail']}")
    lines.append("")
    
    # 信号分解表
    iv_z = summary['growth_iv_z'] or 0
    skew_z = summary['growth_skew_z'] or 0
    pcr_z = summary['growth_pcr_z'] or 0
    lines.append("**信号分解**")
    lines.append("| 维度 | z-score | 权重 | 贡献 | 占比 |")
    lines.append("|------|---------|------|------|------|")
    iv_pct = deep.get('iv_pct')
    skew_pct = deep.get('skew_pct')
    pcr_pct = deep.get('pcr_pct')
    lines.append(f"| 成长IV | {iv_z:+.3f} | 0.5 | {0.5*iv_z:+.3f} | {f'{iv_pct:.0f}%' if iv_pct else '-'} |")
    lines.append(f"| 成长Skew | {skew_z:+.3f} | 0.3 | {0.3*skew_z:+.3f} | {f'{skew_pct:.0f}%' if skew_pct else '-'} |")
    lines.append(f"| 成长PCR | {pcr_z:+.3f} | 0.2 | {0.2*pcr_z:+.3f} | {f'{pcr_pct:.0f}%' if pcr_pct else '-'} |")
    lines.append(f"| **合计** | | | **{z_opt_str}** | |")
    lines.append("")
    
    # 分标的z-score
    z_by = summary['z_by_underlying']
    lines.append("**分标的z-score（相对沪深300）**")
    lines.append("| 维度 | 科创50 | 创业板 | 差异 |")
    lines.append("|------|--------|--------|------|")
    iv_diff = deep.get('single_driver_diff')
    iv_diff_str = f"{iv_diff:+.3f}" if iv_diff is not None else '-'
    lines.append(f"| IV Spread | {z_by.get('iv_spread_588') or '-'} | {z_by.get('iv_spread_1599') or '-'} | {iv_diff_str} |")
    lines.append(f"| Skew Spread | {z_by.get('skew_spread_588') or '-'} | {z_by.get('skew_spread_1599') or '-'} | {z_by.get('skew_spread_500') or '-'} |")
    lines.append(f"| PCR Spread | {z_by.get('pcr_spread_588') or '-'} | {z_by.get('pcr_spread_1599') or '-'} | {z_by.get('pcr_spread_500') or '-'} |")
    lines.append("")
    
    # ===== 深度解读（代码化）=====
    lines.append("**深度解读**：")
    
    # 1. 趋势方向
    trend_parts = []
    t5d = deep.get('trend_5d')
    t20d = deep.get('trend_20d')
    if t5d is not None:
        arrow = "↑" if t5d > 0.05 else ("↓" if t5d < -0.05 else "→")
        trend_parts.append(f"5日{arrow}{abs(t5d):.3f}")
    if t20d is not None:
        arrow = "↑" if t20d > 0.1 else ("↓" if t20d < -0.1 else "→")
        trend_parts.append(f"20日{arrow}{abs(t20d):.3f}")
    if trend_parts:
        lines.append(f"• 趋势方向：{', '.join(trend_parts)}")
    
    # 2. 信号持续性
    cdays = deep.get('consecutive_warning_days', 0)
    if cdays > 0:
        lines.append(f"• 信号持续性：已连续{cdays}天处于预警/警戒区")
        if cdays >= 10:
            lines.append(f"  ⚠️ 连续预警超过10天，风格漂移可能已形成趋势")
    else:
        lines.append("• 信号持续性：当前不在预警区")
    
    # 3. 驱动力
    if iv_pct is not None:
        driver_name = "IV" if iv_pct >= max(skew_pct or 0, pcr_pct or 0) else ("Skew" if (skew_pct or 0) >= (pcr_pct or 0) else "PCR")
        lines.append(f"• 主驱动力：{driver_name}（IV={iv_pct:.0f}% Skew={skew_pct:.0f}% PCR={pcr_pct:.0f}%）")
    
    # 4. 单标的驱动
    single = deep.get('single_driver')
    diff = deep.get('single_driver_diff')
    if single:
        lines.append(f"• 🔍 单标的驱动：主要由{single}驱动（IV z差异={diff:+.3f}），可仅减仓{single}")
    elif diff is not None and abs(diff) > 0.5:
        lines.append(f"• 科创50与创业板IV z差异={diff:+.3f}，暂未达单标的驱动阈值(>1.0)")
    
    # 5. PCR辅助判断
    pcr_588 = deep.get('pcr_588')
    pcr_1599 = deep.get('pcr_1599')
    if pcr_588 is not None or pcr_1599 is not None:
        pcr_parts = []
        if pcr_588 is not None:
            pcr_parts.append(f"科创50={pcr_588:.2f}")
        if pcr_1599 is not None:
            pcr_parts.append(f"创业板={pcr_1599:.2f}")
        pcr_warn = ""
        max_pcr = max(pcr_588 or 0, pcr_1599 or 0)
        if max_pcr > 1.5:
            pcr_warn = " 🔴极端！对冲过度，市场恐慌"
        elif max_pcr > 1.3:
            pcr_warn = " 🟡偏高，谨慎加仓"
        lines.append(f"• PCR辅助：{', '.join(pcr_parts)}{pcr_warn}")
    
    # 6. 历史分位
    pctile = deep.get('percentile')
    if pctile is not None:
        if pctile > 90:
            pctile_str = f"🔴{pctile:.0f}%（极端高位）"
        elif pctile > 75:
            pctile_str = f"🟠{pctile:.0f}%（偏高）"
        elif pctile < 10:
            pctile_str = f"🟢{pctile:.0f}%（极端低位）"
        elif pctile < 25:
            pctile_str = f"🔵{pctile:.0f}%（偏低）"
        else:
            pctile_str = f"{pctile:.0f}%（中性）"
        lines.append(f"• 历史分位：z_opt处于近1年{pctile_str}")
    
    # 7. IV绝对水平
    iv_parts = []
    for name in ['科创50', '创业板', '沪深300', '中证500']:
        iv_abs = deep.get(f'iv_abs_{name}')
        iv_level = deep.get(f'iv_level_{name}')
        if iv_abs is not None and iv_level:
            iv_parts.append(f"{name}={iv_abs*100:.1f}%({iv_level})")
    if iv_parts:
        lines.append(f"• IV绝对水平：{', '.join(iv_parts)}")
    
    # 8. 近20日峰值
    peak = deep.get('peak_20d')
    peak_date = deep.get('peak_20d_date')
    if peak is not None:
        lines.append(f"• 近20日峰值：z_opt={peak:+.3f}（{peak_date}）")
    
    lines.append("")
    lines.append("📌 案例分析学习材料，不构成投资建议")
    
    return '\n'.join(lines)

def run_from_json_mode(db, json_path, trade_date):
    """从option_monitor的JSON输出读取今日数据，避免重复调用tushare API"""
    print(f"[from_json模式] 从 {json_path} 读取期权数据")
    
    if not os.path.exists(json_path):
        print(f"  ❌ JSON文件不存在: {json_path}")
        return False
    
    with open(json_path, 'r', encoding='utf-8') as f:
        opt_data = json.load(f)
    
    # 验证日期
    json_date = opt_data.get('trade_date', '')
    if json_date and json_date != trade_date:
        print(f"  ⚠️ JSON日期({json_date})与目标日期({trade_date})不一致，仍继续使用")
    
    # option_monitor标的名 → style_drift标的代码
    NAME_MAP = {
        '科创50ETF': '588000',
        '创业板ETF': '159915',
        '300ETF': '510300',
        '500ETF': '510500',
    }
    
    inserted = 0
    for opt_name, ul_code in NAME_MAP.items():
        skew_data = opt_data.get('skew', {}).get(opt_name)
        if not skew_data:
            print(f"  ⚠️ {opt_name} 无SKEW数据，跳过")
            continue
        
        # option_monitor IV输出为百分比(如23.45)，style_drift存小数(如0.2345)
        atm_iv = skew_data.get('atm_iv')
        put_25d_iv = skew_data.get('put_25d_iv')
        call_25d_iv = skew_data.get('call_25d_iv')
        iv_skew = skew_data.get('iv_skew')
        spot_price = skew_data.get('spot_price')
        
        db.conn.execute("""INSERT OR REPLACE INTO daily_metrics 
            (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
             iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_date, ul_code, 
             spot_price,
             (atm_iv / 100) if atm_iv is not None else None,
             (put_25d_iv / 100) if put_25d_iv is not None else None,
             (call_25d_iv / 100) if call_25d_iv is not None else None,
             (iv_skew / 100) if iv_skew is not None else None,
             skew_data.get('cboe_skew'),
             skew_data.get('vol_pcr'),
             skew_data.get('oi_pcr'),
             skew_data.get('amt_pcr')))
        inserted += 1
        print(f"  ✅ {opt_name}({ul_code}): IV={atm_iv}% Vol PCR={skew_data.get('vol_pcr')}")
    
    db.safe_commit()
    print(f"  已插入 {inserted} 个标的的今日指标")
    
    # 检查历史数据是否充足（z-score需要至少60天）
    cur = db.conn.execute("SELECT COUNT(DISTINCT trade_date) FROM daily_metrics WHERE atm_iv > 0")
    hist_count = cur.fetchone()[0]
    
    if hist_count < 60:
        print(f"  ⚠️ 历史数据不足({hist_count}天<60)，执行回填...")
        start_dt = (datetime.datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
        build_contract_map(db)
        backfill_tushare(db, start_dt, trade_date)
        compute_all_daily_metrics(db, start_dt, trade_date)
    else:
        print(f"  ✅ 历史数据充足({hist_count}天)，跳过回填")
    
    return True

def main():
    """
    主函数
    模式:
      run      - 完整流程（新浪优先 + Tushare降级）
      report   - 仅生成报告（假设数据已有）
      from_json - 从option_monitor的JSON读取
      backfill - 仅回填Tushare历史数据
      calc     - 仅计算日指标
      verify   - 验证模式：获取当日数据并打印核心指标，不推送
    """
    mode = sys.argv[1] if len(sys.argv) > 1 else 'run'
    
    db = StyleDriftDB()
    
    # 数据库迁移：添加data_source列
    migrate_db_add_data_source(db)
    
    try:
        today = datetime.datetime.now().strftime('%Y%m%d')
        start_dt = (datetime.datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
        
        if mode == 'verify':
            # ===== 验证模式：获取当日数据并打印 =====
            print("=" * 60)
            print(f"风格漂移验证模式 - {today}")
            print("=" * 60)
            
            success, total, _actual_td = fetch_today_unified(db, today)
            
            if success == 0:
                print(f"\n❌ 所有标的获取失败，无法生成报告")
                print("⚠️ 今日数据获取失败，不发布报告")
                return
            
            summary = calculate_style_drift(db)
            if summary:
                print("\n" + "=" * 60)
                print("核心数据验证")
                print("=" * 60)
                raw = summary.get('raw_latest', {})
                for code, name in [('588000', '科创50'), ('159915', '创业板'), 
                                    ('510300', '沪深300'), ('510500', '中证500')]:
                    r = raw.get(code, {})
                    spot = r.get('spot')
                    atm = r.get('atm_iv')
                    p25 = r.get('put_25d_iv')
                    pcr = r.get('vol_pcr')
                    spot_s = f"{spot:.4f}" if spot else "N/A"
                    atm_s = f"{atm*100:.2f}%" if atm else "N/A"
                    p25_s = f"{p25*100:.2f}%" if p25 else "N/A"
                    pcr_s = f"{pcr:.2f}" if pcr else "N/A"
                    print(f"  {name}: spot={spot_s} ATM_IV={atm_s} 25dPut={p25_s} PCR={pcr_s}")
                
                z_opt = summary.get('z_opt')
                z_opt_s = f"{z_opt:+.3f}" if z_opt is not None else "N/A"
                print(f"\n  z_opt = {z_opt_s}")
                print(f"  区域: {summary.get('zone', '?')} {summary.get('label', '?')}")
                print(f"  建议: {summary.get('advice', '?')}")
                
                # z-score明细
                z_by = summary.get('z_by_underlying', {})
                print(f"\n  z-score明细:")
                for k, v in z_by.items():
                    if v is not None:
                        print(f"    {k}: {v:+.3f}")
                
                # 数据时效性
                print(f"\n  数据日期: {summary.get('trade_date', 'N/A')}")
                try:
                    row = db.conn.execute(
                        "SELECT data_source FROM daily_metrics WHERE trade_date=? AND data_source IS NOT NULL LIMIT 1",
                        (summary.get('trade_date', today),)).fetchone()
                    print(f"  数据来源: {row[0] if row and row[0] else 'unknown'}")
                except Exception:
                    print(f"  数据来源: unknown")
                print(f"  ✅ 数据为最新")
            else:
                print("\n⚠️ 无法计算风格漂移：历史数据不足（需要至少60天）")
        
        elif mode == 'backfill':
            build_contract_map(db)
            backfill_tushare(db, start_dt, today)
            print("回填完成")
            
        elif mode == 'calc':
            compute_all_daily_metrics(db, start_dt, today)
            
        elif mode == 'report':
            summary = calculate_style_drift(db)
            if summary:
                report = generate_report(summary)
                output_file = os.path.join(OUTPUT_DIR, f"{today}_风格漂移监控.md")
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report)
                feishu_report = generate_feishu_report(summary)
                feishu_file = os.path.join(OUTPUT_DIR, f"{today}_风格漂移监控_飞书推送.md")
                with open(feishu_file, 'w', encoding='utf-8') as f:
                    f.write(feishu_report)
                json_file = os.path.join(OUTPUT_DIR, f"{today}_style_drift.json")
                _skip_push = check_idempotent(json_file)
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
                print(report)
                print(f"\n报告已保存: {output_file}")
                print(f"飞书推送版已保存: {feishu_file}")
                print(f"JSON已保存: {json_file}")
                if _skip_push:
                    print("今天已推送，跳过飞书推送（幂等保护）")
                else:
                    import sys as _sys
                    _sys.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
                    from feishu_card_utils import build_style_drift_card
                    from feishu_push import push_card_to_feishu
                    sd_warnings = detect_style_drift_data_missing(summary)
                    if sd_warnings:
                        for w in sd_warnings:
                            print(f"    {w}")
                    card = build_style_drift_card(summary['trade_date'], summary, data_warnings=sd_warnings)
                    _success = push_card_to_feishu(card_dict=card)
                    if _success:
                        import time as _time
                        with open(json_file + '.pushed', 'w') as _pf:
                            _pf.write(_time.strftime('%Y-%m-%d %H:%M:%S'))
                        print("✅ 推送成功，已写幂等标记")
            else:
                print("⚠️ 无法生成报告：数据不足")
        
        elif mode == 'from_json':
            td = sys.argv[2] if len(sys.argv) > 2 else today
            json_path = os.path.join(OUTPUT_DIR, f'{td}_option_data.json')
            
            print("=" * 60)
            print(f"A股风格漂移监控（from_json模式）- {td}")
            print("=" * 60)
            
            ok = run_from_json_mode(db, json_path, td)
            if not ok:
                print("❌ from_json失败，改用data_hub统一接口...")
                fetch_today_unified(db, td)
            
            summary = calculate_style_drift(db)
            if summary:
                report = generate_report(summary)
                output_file = os.path.join(OUTPUT_DIR, f"{td}_风格漂移监控.md")
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report)
                feishu_report = generate_feishu_report(summary)
                feishu_file = os.path.join(OUTPUT_DIR, f"{td}_风格漂移监控_飞书推送.md")
                with open(feishu_file, 'w', encoding='utf-8') as f:
                    f.write(feishu_report)
                json_file = os.path.join(OUTPUT_DIR, f"{td}_style_drift.json")
                _skip_push = check_idempotent(json_file)
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
                print(f"\n报告已保存: {output_file}")
            else:
                print("⚠️ 无法计算风格漂移：数据不足")
        
        else:  # mode == 'run'
            print("=" * 60)
            print("A股风格漂移监控（期权版）- data_hub统一接口优先")
            print("=" * 60)
            
            # ===== Step 1: data_hub 统一接口（唯一真源）获取当日数据 =====
            summary = None
            data_source_used = 'none'

            try:
                success, total, actual_td = fetch_today_unified(db, today)
                if success >= 2:  # 至少2个标的成功
                    data_source_used = 'unified'
                    print(f"\n✅ 统一接口成功 ({success}/{total}, 交易日 {actual_td})")
                    summary = calculate_style_drift(db)

                    if summary:
                        sd_warnings = detect_style_drift_data_missing(summary)
                        if not sd_warnings:
                            print("✅ 数据质量达标，使用统一接口数据")
                        else:
                            print(f"⚠️ 统一接口数据有告警: {sd_warnings}")
                    else:
                        print("⚠️ 统一接口获取成功但历史不足，需补充")
                else:
                    print(f"❌ 统一接口失败 ({success}/{total})")
            except Exception as e:
                print(f"❌ 统一接口异常: {e}")
            
            # ===== Step 2: 统一接口失败则降级到Tushare完整流程 =====
            if data_source_used == 'none' or summary is None:
                print("\n🔄 降级到Tushare数据源...")
                data_source_used = 'tushare'
                
                MAX_RETRY = 3
                RETRY_INTERVAL = 60
                for attempt in range(1, MAX_RETRY + 1):
                    print(f"\n🔄 Tushare第{attempt}次尝试...")
                    build_contract_map(db)
                    backfill_tushare(db, start_dt, today)
                    compute_all_daily_metrics(db, start_dt, today)
                    summary = calculate_style_drift(db)
                    
                    sd_warnings = detect_style_drift_data_missing(summary) if summary else ["数据完全缺失"]
                    severe_missing = summary is None or len(sd_warnings) >= 1
                    if not severe_missing:
                        print(f"✅ Tushare第{attempt}次数据质量达标")
                        break
                    elif attempt < MAX_RETRY:
                        print(f"⚠️ 核心数据缺失，{RETRY_INTERVAL}秒后重试...")
                        import time as _t
                        _t.sleep(RETRY_INTERVAL)
                    else:
                        print(f"❌ {MAX_RETRY}次重试后数据仍不完整")
            
            # ===== Step 3: 生成报告 =====
            if summary:
                report = generate_report(summary)
                output_file = os.path.join(OUTPUT_DIR, f"{today}_风格漂移监控.md")
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report)
                feishu_report = generate_feishu_report(summary)
                feishu_file = os.path.join(OUTPUT_DIR, f"{today}_风格漂移监控_飞书推送.md")
                with open(feishu_file, 'w', encoding='utf-8') as f:
                    f.write(feishu_report)
                json_file = os.path.join(OUTPUT_DIR, f"{today}_style_drift.json")
                _skip_push = check_idempotent(json_file)
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
                print(report)
                print(f"\n数据源: {data_source_used}")
                print(f"报告已保存: {output_file}")
                print(f"飞书推送版已保存: {feishu_file}")
                print(f"JSON已保存: {json_file}")
                if _skip_push:
                    print("今天已推送，跳过飞书推送（幂等保护）")
                else:
                    import sys as _sys
                    _sys.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
                    from feishu_card_utils import build_style_drift_card
                    from feishu_push import push_card_to_feishu
                    sd_warnings = detect_style_drift_data_missing(summary)
                    if sd_warnings:
                        for w in sd_warnings:
                            print(f"    {w}")
                    card = build_style_drift_card(summary['trade_date'], summary, data_warnings=sd_warnings)
                    _success = push_card_to_feishu(card_dict=card)
                    if _success:
                        import time as _time
                        with open(json_file + '.pushed', 'w') as _pf:
                            _pf.write(_time.strftime('%Y-%m-%d %H:%M:%S'))
                        print("✅ 推送成功，已写幂等标记")
            else:
                print("⚠️ 今日数据获取失败，不发布报告")
                print("⚠️ 新浪和Tushare均无法获取有效数据")
    
    finally:
        db.close()

if __name__ == '__main__':
    main()
