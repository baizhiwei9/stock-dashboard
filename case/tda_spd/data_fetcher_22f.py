#!/usr/bin/env python3
"""
TDA流形旋转策略 - 22因子数据获取模块
=====================================
使用申万一级行业指数替代原8只ETF:
  - 14个行业/主题因子 -> 直接映射申万一级行业
  - 8个风格因子 -> 用申万一级行业作为代理
  - 数据源: akshare index_hist_sw (申万行业指数日线)
  - 本地缓存: state/sw_cache/ 避免重复拉取
  - Tushare仍用于: 个股数据、交易日历、市值筛选
"""

import os
import sys

# 确保公共依赖库路径 (优先持久化共享目录, 兼容旧pip-global路径)
# 必须在 import numpy/pandas/akshare 之前执行: 全新沙箱中 /tmp 不持久, 否则导入即失败
_SHARED_DEPS = [
    '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps',
    '/tmp/.pip-global/lib/python3.12/site-packages',
]
for _p in reversed(_SHARED_DEPS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import time
import json
import datetime
import numpy as np
import pandas as pd

# ===== akshare 禁用 IPv6 =====
import socket
socket.setdefaulttimeout(30)
_orig_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kw: [r for r in _orig_getaddrinfo(*args, **kw) if r[0] == socket.AF_INET]

import akshare as ak

# Tushare配置 (用于个股数据)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts_config import init_tushare

_pro = None
_last_tushare_call = 0.0
_TUSHARE_MIN_INTERVAL = 1.5

def get_pro():
    global _pro
    if _pro is None:
        _pro = init_tushare()
    return _pro

def _tushare_throttle():
    global _last_tushare_call
    now = time.time()
    elapsed = now - _last_tushare_call
    if elapsed < _TUSHARE_MIN_INTERVAL:
        time.sleep(_TUSHARE_MIN_INTERVAL - elapsed)
    _last_tushare_call = time.time()

def ts_call(func, **kwargs):
    _tushare_throttle()
    pro = get_pro()
    api_func = getattr(pro, func)
    for attempt in range(3):
        try:
            result = api_func(**kwargs)
            return result
        except Exception as e:
            err = str(e).lower()
            if 'limit' in err or 'freq' in err or 'ip' in err or 'too many' in err:
                print(f"  Tushare限频, 等待5秒... ({func})")
                time.sleep(5)
                _last_tushare_call = time.time()
            else:
                if attempt < 2:
                    time.sleep(2)
                else:
                    raise
    return None

# ========== 22因子映射表 ==========

FACTOR_22_MAP = {
    # === 14个行业/主题因子 ===
    '科技成长':   {'sw_code': '801080', 'sw_name': '电子',       'category': '行业', 'industries': ['电子']},
    '高端制造':   {'sw_code': '801890', 'sw_name': '机械设备',   'category': '行业', 'industries': ['机械设备']},
    '新能源':     {'sw_code': '801730', 'sw_name': '电力设备',   'category': '行业', 'industries': ['电力设备']},
    '医药健康':   {'sw_code': '801150', 'sw_name': '医药生物',   'category': '行业', 'industries': ['医药生物']},
    '大消费':     {'sw_code': '801120', 'sw_name': '食品饮料',   'category': '行业', 'industries': ['食品饮料']},
    '金融地产':   {'sw_code': '801780', 'sw_name': '银行',       'category': '行业', 'industries': ['银行', '非银金融', '房地产']},
    '资源材料':   {'sw_code': '801050', 'sw_name': '有色金属',   'category': '行业', 'industries': ['有色金属', '基础化工', '钢铁']},
    '数字经济':   {'sw_code': '801750', 'sw_name': '计算机',     'category': '行业', 'industries': ['计算机']},
    '国防军工':   {'sw_code': '801740', 'sw_name': '国防军工',   'category': '行业', 'industries': ['国防军工']},
    '绿色低碳':   {'sw_code': '801970', 'sw_name': '环保',       'category': '行业', 'industries': ['环保']},
    '农业养殖':   {'sw_code': '801010', 'sw_name': '农林牧渔',   'category': '行业', 'industries': ['农林牧渔']},
    '低波防御':   {'sw_code': '801160', 'sw_name': '公用事业',   'category': '行业', 'industries': ['公用事业']},
    '红利高息':   {'sw_code': '801170', 'sw_name': '交通运输',   'category': '行业', 'industries': ['交通运输']},
    '价值周期':   {'sw_code': '801040', 'sw_name': '钢铁',       'category': '行业', 'industries': ['钢铁', '建筑材料', '建筑装饰']},
    # === 8个风格因子 (用申万一级行业代理) ===
    '动量':       {'sw_code': '801880', 'sw_name': '汽车',       'category': '风格', 'industries': ['汽车']},
    '反转':       {'sw_code': '801180', 'sw_name': '房地产',     'category': '风格', 'industries': ['房地产']},
    '小盘成长':   {'sw_code': '801210', 'sw_name': '社会服务',   'category': '风格', 'industries': ['社会服务']},
    '大盘蓝筹':   {'sw_code': '801790', 'sw_name': '非银金融',   'category': '风格', 'industries': ['非银金融']},
    '质量因子':   {'sw_code': '801110', 'sw_name': '家用电器',   'category': '风格', 'industries': ['家用电器']},
    '成长因子':   {'sw_code': '801760', 'sw_name': '传媒',       'category': '风格', 'industries': ['传媒']},
    '价值因子':   {'sw_code': '801030', 'sw_name': '基础化工',   'category': '风格', 'industries': ['基础化工']},
    '流动性因子': {'sw_code': '801950', 'sw_name': '煤炭',       'category': '风格', 'industries': ['煤炭']},
}

FACTOR_NAMES = list(FACTOR_22_MAP.keys())
N_FACTORS = len(FACTOR_NAMES)

# 沪深300作为基准
BENCHMARK_CODE = '000300'
BENCHMARK_NAME = '沪深300'

# ========== 申万一级 -> Tushare二级行业映射 ==========
# Tushare stock_basic 的 industry 字段是申万二级行业名
# 这里建立申万一级 -> 二级的映射, 用于选股时匹配
SW_L1_TO_L2 = {
    '电子':       ['半导体', '元器件', 'IT设备', '光学光电子'],
    '机械设备':   ['工程机械', '机床制造', '机械基件', '专用机械', '运输设备', '纺织机械', '农用机械', '轻工机械'],
    '电力设备':   ['电气设备', '电池', '电机'],
    '医药生物':   ['化学制药', '中成药', '生物制药', '医疗保健', '医药商业', '医疗器械'],
    '食品饮料':   ['白酒', '啤酒', '软饮料', '食品', '饲料', '红黄酒', '乳制品', '调味品'],
    '银行':       ['银行'],
    '非银金融':   ['证券', '保险', '多元金融'],
    '房地产':     ['全国地产', '区域地产', '房产服务', '园区开发'],
    '有色金属':   ['黄金', '铜', '铝', '铅锌', '小金属', '矿物制品'],
    '基础化工':   ['化工原料', '染料涂料', '塑料', '橡胶', '化纤', '农药化肥'],
    '钢铁':       ['普钢', '特种钢', '钢加工'],
    '计算机':     ['软件服务', '互联网', 'IT设备'],
    '国防军工':   ['航空', '船舶', '航天军工', '兵器兵装'],
    '环保':       ['环境保护'],
    '农林牧渔':   ['种植业', '渔业', '林业', '农业综合', '饲料'],
    '公用事业':   ['火力发电', '水力发电', '新型电力', '供气供热', '水务'],
    '交通运输':   ['港口', '水运', '公路', '铁路', '空运', '机场', '仓储物流', '公共交通', '路桥'],
    '社会服务':   ['酒店餐饮', '旅游景点', '旅游服务', '商贸代理', '百货', '超市连锁', '商品城', '其他商业'],
    '家用电器':   ['家用电器', '家居用品'],
    '传媒':       ['影视音像', '广告包装', '出版业', '互联网'],
    '建筑材料':   ['水泥', '玻璃', '其他建材'],
    '建筑装饰':   ['建筑工程', '装修装饰'],
    '煤炭':       ['煤炭开采', '焦炭加工'],
    '石油石化':   ['石油加工', '石油开采', '石油贸易'],
    '汽车':       ['汽车整车', '汽车配件', '汽车服务', '摩托车'],
    '商贸零售':   ['百货', '超市连锁', '商品城', '商贸代理', '批发业'],
    '纺织服饰':   ['纺织', '服饰', '鞋类'],
    '美容护理':   ['日用化工', '化妆品'],
    '综合':       ['综合类'],
}

def get_sw_l2_industries(sw_l1_names):
    """将申万一级行业名列表转为Tushare二级行业名列表"""
    result = []
    for l1 in sw_l1_names:
        if l1 in SW_L1_TO_L2:
            result.extend(SW_L1_TO_L2[l1])
        else:
            # 如果不在映射表中，直接用一级名尝试匹配
            result.append(l1)
    return list(set(result))  # 去重

# ========== 路径配置 ==========

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'state', 'sw_cache')

def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)

# ========== 交易日历 ==========

_trade_calendar = None

def get_trade_calendar(start_date='20250101', end_date='20261231'):
    global _trade_calendar
    if _trade_calendar is not None:
        return _trade_calendar
    
    for attempt in range(3):
        try:
            df = ts_call('trade_cal', exchange='SSE', start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                _trade_calendar = df[df['is_open'] == 1]['cal_date'].sort_values().tolist()
                return _trade_calendar
        except Exception as e:
            print(f"获取交易日历失败(尝试{attempt+1}): {e}")
            time.sleep(3)
    
    # fallback: akshare
    try:
        df = ak.tool_trade_date_hist_sina()
        df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
        _trade_calendar = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]['trade_date'].sort_values().tolist()
        return _trade_calendar
    except Exception as e:
        print(f"akshare获取交易日历也失败: {e}")
        return []

def is_trading_day(date_str=None):
    if date_str is None:
        date_str = datetime.date.today().strftime('%Y%m%d')
    cal = get_trade_calendar()
    return date_str in cal

def get_recent_trading_days(n=5, end_date=None):
    if end_date is None:
        end_date = datetime.date.today().strftime('%Y%m%d')
    cal = get_trade_calendar()
    past = [d for d in cal if d <= end_date]
    return past[-n:] if len(past) >= n else past

# ========== 申万行业指数数据获取 ==========

def _fetch_sw_index_raw(sw_code):
    """
    获取单个申万一级行业指数的全量历史数据
    使用akshare index_hist_sw接口
    返回 DataFrame: 日期, 收盘
    """
    _ensure_cache_dir()
    cache_file = os.path.join(CACHE_DIR, f'sw_{sw_code}.json')
    
    # 检查缓存是否今天更新过
    if os.path.exists(cache_file):
        mtime = os.path.getmtime(cache_file)
        today_str = datetime.date.today().strftime('%Y%m%d')
        cache_date = datetime.datetime.fromtimestamp(mtime).strftime('%Y%m%d')
        if cache_date == today_str:
            try:
                df = pd.read_json(cache_file)
                # 确保trade_date是字符串格式YYYYMMDD
                df['trade_date'] = df['trade_date'].astype(str)
                # 处理可能的int64时间戳(orient=records序列化导致)
                if len(df) > 0 and len(str(df['trade_date'].iloc[0])) > 8:
                    df['trade_date'] = pd.to_datetime(df['trade_date'].astype('int64'), unit='ms').dt.strftime('%Y%m%d')
                return df
            except:
                pass
    
    # 从akshare获取 (带重试: 单次网络抖动不应导致该因子永久缺数据)
    for _attempt in range(3):
        try:
            df = ak.index_hist_sw(symbol=sw_code)
            if df is not None and len(df) > 0:
                # 列名: 代码, 日期, 收盘, 开盘, 最高, 最低, 成交量, 成交额
                df = df[['日期', '收盘']].copy()
                df.columns = ['trade_date', 'close']
                df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
                df['close'] = df['close'].astype(float)
                df = df.sort_values('trade_date').reset_index(drop=True)

                # 保存缓存
                df.to_json(cache_file, orient='records', force_ascii=False)
                return df
        except Exception as e:
            if _attempt < 2:
                print(f"  获取申万行业{sw_code}失败(尝试{_attempt+1}/3), 重试: {e}")
                time.sleep(2)
            else:
                print(f"  获取申万行业{sw_code}失败: {e}")
    
    # 尝试读取旧缓存
    if os.path.exists(cache_file):
        try:
            df = pd.read_json(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if len(df) > 0 and len(str(df['trade_date'].iloc[0])) > 8:
                df['trade_date'] = pd.to_datetime(df['trade_date'].astype('int64'), unit='ms').dt.strftime('%Y%m%d')
            return df
        except:
            pass
    
    return pd.DataFrame()

def fetch_all_sw_prices(start_date, end_date):
    """
    获取全部22个申万行业指数的收盘价
    返回 DataFrame: index=trade_date, columns=因子名称
    """
    all_prices = {}
    
    print(f"  获取{N_FACTORS}个申万行业指数数据...")
    for i, (factor_name, info) in enumerate(FACTOR_22_MAP.items()):
        sw_code = info['sw_code']
        sw_name = info['sw_name']
        
        df = _fetch_sw_index_raw(sw_code)
        if df is not None and len(df) > 0:
            # 过滤日期范围
            mask = (df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)
            filtered = df[mask].copy()
            prices = filtered.set_index('trade_date')['close']
            all_prices[factor_name] = prices
            if (i + 1) % 5 == 0:
                print(f"    [{i+1}/{N_FACTORS}] {factor_name}({sw_code} {sw_name}): {len(prices)}条")
        else:
            print(f"    [{i+1}/{N_FACTORS}] {factor_name}({sw_code} {sw_name}): 无数据!")
            all_prices[factor_name] = pd.Series(dtype=float)
        
        time.sleep(0.3)  # 轻微限频
    
    price_df = pd.DataFrame(all_prices)
    price_df = price_df.sort_index()
    price_df = price_df.dropna(how='all')
    
    print(f"  价格矩阵: {price_df.shape}")
    return price_df

def fetch_sw_prices_incremental(trade_date):
    """
    增量更新: 检查缓存数据是否包含最新交易日
    仅剔除不含该交易日的缓存(后续拉取时自动刷新), 保留仍然有效的缓存,
    避免单次网络抖动把全部缓存清空、导致部分因子永久缺数据
    """
    _ensure_cache_dir()
    stale_codes = []

    for factor_name, info in FACTOR_22_MAP.items():
        sw_code = info['sw_code']
        cache_file = os.path.join(CACHE_DIR, f'sw_{sw_code}.json')
        if not os.path.exists(cache_file):
            stale_codes.append(sw_code)
            continue
        try:
            df = pd.read_json(cache_file)
            if trade_date not in df['trade_date'].astype(str).values:
                stale_codes.append(sw_code)
        except Exception:
            stale_codes.append(sw_code)

    if stale_codes:
        print(f"  {len(stale_codes)}/{N_FACTORS}个行业缓存不含{trade_date}, 仅刷新这些行业...")
        for sw_code in stale_codes:
            cache_file = os.path.join(CACHE_DIR, f'sw_{sw_code}.json')
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                except OSError:
                    pass

    return bool(stale_codes)

# ========== 基准数据 ==========

def fetch_benchmark_prices(start_date, end_date):
    """获取沪深300指数日线数据"""
    # 优先Tushare
    try:
        df = ts_call('index_daily', ts_code='000300.SH', start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            return df.sort_values('trade_date').set_index('trade_date')['close']
    except Exception as e:
        print(f"  Tushare获取沪深300失败: {e}")
    
    # fallback: akshare腾讯接口
    try:
        df = ak.stock_zh_index_daily_tx(symbol='sh000300')
        if df is not None and len(df) > 0:
            df['trade_date'] = df['date'].astype(str).str.replace('-', '')
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
            return df.sort_values('trade_date').set_index('trade_date')['close']
    except Exception as e:
        print(f"  akshare获取沪深300也失败: {e}")
    
    return pd.Series(dtype=float)

# ========== 个股数据获取 (用于选股) ==========

_stock_basic_cache = None

def get_stock_basic():
    global _stock_basic_cache
    if _stock_basic_cache is not None:
        return _stock_basic_cache
    
    for attempt in range(3):
        try:
            df = ts_call('stock_basic', exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
            if df is not None and len(df) > 0:
                df = df[~df['name'].str.contains('ST', na=False)]
                df = df[~df['name'].str.contains('退', na=False)]
                _stock_basic_cache = df
                print(f"获取A股基本信息: {len(df)}只股票")
                return df
        except Exception as e:
            print(f"获取股票基本信息失败(尝试{attempt+1}): {e}")
            time.sleep(3)
    
    return pd.DataFrame()

def get_stocks_by_industries(industries):
    """按申万行业名称获取股票列表(自动将一级转为二级匹配)"""
    stock_basic = get_stock_basic()
    if stock_basic.empty:
        return []
    # 将申万一级行业名转为Tushare二级行业名
    l2_industries = get_sw_l2_industries(industries)
    mask = stock_basic['industry'].isin(l2_industries)
    matched = stock_basic[mask]
    return matched['ts_code'].tolist()

def fetch_daily_basic(trade_date):
    """获取某日全市场市值数据"""
    for attempt in range(3):
        try:
            df = ts_call('daily_basic', trade_date=trade_date,
                        fields='ts_code,trade_date,turnover_rate,total_mv,circ_mv')
            if df is not None and len(df) > 0:
                return df
            time.sleep(1)
        except Exception as e:
            print(f"获取daily_basic {trade_date} 失败(尝试{attempt+1}): {e}")
            time.sleep(3)
    return pd.DataFrame()

def get_top_stocks_by_mv(trade_date, n=2000):
    """获取市值排名前N的非ST股票"""
    basic = get_stock_basic()
    daily_basic = fetch_daily_basic(trade_date)
    if daily_basic.empty:
        return set()
    merged = daily_basic.merge(basic[['ts_code', 'name', 'industry']], on='ts_code', how='inner')
    merged = merged.sort_values('total_mv', ascending=False)
    return set(merged.head(n)['ts_code'].tolist())

_stock_price_cache = {}

def fetch_stock_daily(ts_code, start_date, end_date):
    """获取个股日线数据"""
    cache_key = f"{ts_code}_{start_date}_{end_date}"
    if cache_key in _stock_price_cache:
        return _stock_price_cache[cache_key]
    
    for attempt in range(3):
        try:
            df = ts_call('daily', ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                _stock_price_cache[cache_key] = df
                return df
        except Exception as e:
            if 'limit' in str(e).lower() or 'freq' in str(e).lower():
                time.sleep(3)
            else:
                time.sleep(1)
    
    # fallback: akshare
    try:
        symbol = ts_code[:6]
        df = ak.stock_zh_a_hist(symbol=symbol, period='daily',
                                start_date=start_date, end_date=end_date, adjust='qfq')
        if df is not None and len(df) > 0:
            df['trade_date'] = df['日期'].astype(str).str.replace('-', '')
            df = df.sort_values('trade_date').reset_index(drop=True)
            _stock_price_cache[cache_key] = df
            return df
    except:
        pass
    
    return pd.DataFrame()

def fetch_stock_batch(ts_codes, start_date, end_date):
    """批量获取个股日线数据 (Tushare daily接口)"""
    all_data = {}
    batch_size = 50
    
    for i in range(0, len(ts_codes), batch_size):
        batch = ts_codes[i:i+batch_size]
        for attempt in range(3):
            try:
                codes_str = ','.join(batch)
                df = ts_call('daily', ts_code=codes_str, start_date=start_date, end_date=end_date)
                if df is not None and len(df) > 0:
                    for code in batch:
                        code_df = df[df['ts_code'] == code]
                        if len(code_df) > 0:
                            code_df = code_df.sort_values('trade_date').reset_index(drop=True)
                            all_data[code] = code_df
                break
            except Exception as e:
                err_msg = str(e).lower()
                if 'limit' in err_msg or 'freq' in err_msg or 'too many' in err_msg:
                    print(f"  限频，等待3秒... (batch {i//batch_size + 1})")
                    time.sleep(3)
                else:
                    if attempt < 2:
                        time.sleep(1)
        
        if i > 0 and i % 100 == 0:
            print(f"  已获取 {i}/{len(ts_codes)} 只股票数据")
    
    return all_data

def select_top_stocks_for_factor(factor_name, trade_date, lookback=10, top_n=3):
    """
    为指定因子选取近lookback个交易日涨幅最好的Top N股票
    
    流程:
    1. 获取该因子对应的申万行业股票列表
    2. 筛选市值Top2000
    3. 获取近lookback个交易日的个股价格
    4. 计算涨幅，取Top N
    """
    info = FACTOR_22_MAP[factor_name]
    sw_industries = info['industries']
    
    all_stocks = get_stocks_by_industries(sw_industries)
    if not all_stocks:
        print(f"  {factor_name}: 未找到行业 {sw_industries} 的股票")
        return []
    
    # 获取市值Top2000
    top_stocks = get_top_stocks_by_mv(trade_date, n=2000)
    if top_stocks:
        candidate_stocks = [s for s in all_stocks if s in top_stocks]
    else:
        candidate_stocks = all_stocks
    
    if not candidate_stocks:
        print(f"  {factor_name}: 无候选股票")
        return []
    
    # 获取历史数据
    cal = get_trade_calendar()
    idx = cal.index(trade_date) if trade_date in cal else len(cal) - 1
    start_idx = max(0, idx - lookback - 5)
    start_date = cal[start_idx]
    
    stock_data = fetch_stock_batch(candidate_stocks, start_date, trade_date)
    
    # 计算涨幅
    returns = {}
    for code, df in stock_data.items():
        if len(df) >= lookback + 1:
            recent = df.tail(lookback + 1)
            start_price = recent.iloc[0]['close'] if 'close' in recent.columns else recent.iloc[0]['收盘']
            end_price = recent.iloc[-1]['close'] if 'close' in recent.columns else recent.iloc[-1]['收盘']
            if start_price > 0:
                ret = (end_price - start_price) / start_price
                returns[code] = ret
    
    # 取Top N
    sorted_stocks = sorted(returns.items(), key=lambda x: x[1], reverse=True)[:top_n]
    
    basic = get_stock_basic()
    result = []
    for code, ret in sorted_stocks:
        name = basic[basic['ts_code'] == code]['name'].values[0] if len(basic[basic['ts_code'] == code]) > 0 else code
        result.append({
            'code': code,
            'name': name,
            'return_2w': ret
        })
    
    return result

def select_top_factor_stocks(trade_date, top_factors=None, top_n_per_factor=3):
    """
    为权重最高的几个因子选取龙头股
    top_factors: [(factor_name, weight), ...] 按权重降序
    只选前5个因子，每个选3只
    """
    if top_factors is None:
        # 默认选所有因子
        top_factors = [(name, 0) for name in FACTOR_NAMES]
    
    # 只选前5个因子
    selected = top_factors[:5]
    
    results = {}
    for factor_name, weight in selected:
        print(f"  选股: {factor_name} (权重={weight*100:.1f}%)...")
        stocks = select_top_stocks_for_factor(factor_name, trade_date, top_n=top_n_per_factor)
        results[factor_name] = stocks
        time.sleep(1)
    
    return results

# ========== 工具函数 ==========

def date_to_str(date_obj):
    return date_obj.strftime('%Y%m%d')

def str_to_date(date_str):
    return datetime.datetime.strptime(date_str, '%Y%m%d').date()

def format_date_display(date_str):
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    return date_str

# ========== 测试 ==========

if __name__ == '__main__':
    print("=== 22因子数据获取测试 ===")
    print(f"因子数量: {N_FACTORS}")
    print(f"因子列表: {FACTOR_NAMES}")
    
    # 测试交易日历
    cal = get_trade_calendar()
    print(f"\n交易日历: {len(cal)}天, 最近5天: {cal[-5:] if len(cal) >= 5 else cal}")
    
    # 测试行业指数获取
    print("\n=== 测试申万行业指数获取 ===")
    price_df = fetch_all_sw_prices('20250801', '20260818')
    print(f"\n价格矩阵: {price_df.shape}")
    print(price_df.tail())
    
    # 检查缺失
    print(f"\n各因子数据量:")
    for col in price_df.columns:
        print(f"  {col}: {price_df[col].notna().sum()}条")
