#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
午盘实时数据获取模块
为风险预测日报午盘版提供实时数据源

数据源：
1. 指数实时行情：data_hub IndexSpotCollector（四级降级：akshare→tushare→coze_gateway→web）
2. A股实时行情：data_hub StockSpotCollector（四级降级：akshare→tushare→coze_gateway→web）
3. 东财涨停池：ak.stock_zt_pool_em(date='YYYYMMDD')
4. 新浪期货实时（hq.sinajs.cn）：IF/IC/IM主力
5. 新浪ETF实时（hq.sinajs.cn）：50ETF/300ETF/500ETF/创业板ETF/科创50ETF
6. 指数成分股：data_hub IndexComponentsCollector（三级降级：akshare→tushare→web）

设计原则：
- 零LLM，纯Python
- 所有函数返回结构化dict，便于主程序直接使用
- 失败时返回None，不抛出异常
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

import re
import time
import numpy as np
from datetime import datetime, timedelta

# ===== akshare延迟导入（避免模块导入时就加载） =====
try:
    import akshare as ak
    _ak_ready = True
except ImportError:
    _ak_ready = False

try:
    import requests
    _requests_ready = True
except ImportError:
    _requests_ready = False

# ===== data_hub 延迟导入（A股/指数实时行情主数据源，四级降级） =====
_data_hub_ready = False
_data_hub_error = ""
try:
    import sys
    import os
    # data_hub 位于同主对话下的 codeact/scripts
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _data_hub_path = os.path.normpath(os.path.join(_script_dir, '../../codeact/scripts'))
    if _data_hub_path not in sys.path:
        sys.path.insert(0, _data_hub_path)
    from data_hub.collectors.stock_spot import StockSpotCollector
    from data_hub.collectors.index_spot import IndexSpotCollector
    from data_hub.collectors.index_components import IndexComponentsCollector
    _data_hub_ready = True
except Exception as _e:
    _data_hub_error = str(_e)
    StockSpotCollector = None
    IndexSpotCollector = None
    IndexComponentsCollector = None


# ============================================================
#  基础数据获取
# ============================================================

def fetch_index_realtime():
    """获取指数实时行情（data_hub四级降级）
    
    Returns:
        dict: {index_code: {name, price, change_pct, high, low, volume, amount, prev_close}}
        失败返回 None
    """
    if not _data_hub_ready:
        print(f"  ⚠️  data_hub不可用，尝试akshare兜底: {_data_hub_error}")
        # 降级到直接 akshare 调用
        return _fetch_index_realtime_akshare()
    
    try:
        collector = IndexSpotCollector()
        result = collector.collect()
        if not result.success or result.data is None or len(result.data) == 0:
            print(f"  ⚠️  data_hub指数实时行情获取失败，尝试akshare兜底")
            return _fetch_index_realtime_akshare()
        
        df = result.data
        out = {}
        for _, row in df.iterrows():
            symbol = str(row.get('symbol', '')).lower()
            name = str(row.get('name', ''))
            price = float(row.get('price', 0)) or 0
            change_pct = float(row.get('change_pct', 0)) or 0
            high = float(row.get('high', 0)) or 0
            low = float(row.get('low', 0)) or 0
            volume = float(row.get('volume', 0)) or 0
            amount = float(row.get('amount', 0)) or 0
            prev_close = float(row.get('prev_close', 0)) or 0
            out[symbol] = {
                'name': name,
                'price': price,
                'change_pct': change_pct,
                'high': high,
                'low': low,
                'volume': volume,
                'amount': amount,
                'prev_close': prev_close,
            }
        
        print(f"  ✅ 指数实时行情: {len(out)} 条 (源: {result.source})")
        return out
    except Exception as e:
        print(f"  ❌ data_hub获取指数实时行情失败: {e}，尝试akshare兜底")
        return _fetch_index_realtime_akshare()


def _fetch_index_realtime_akshare():
    """备用：直接用 akshare 获取指数实时行情"""
    if not _ak_ready:
        return None
    
    try:
        df = ak.stock_zh_index_spot_sina()
        if df is None or len(df) == 0:
            return None
        
        result = {}
        for _, row in df.iterrows():
            code = str(row.get('代码', ''))
            name = str(row.get('名称', ''))
            price = float(row.get('最新价', 0)) or 0
            change_pct = float(row.get('涨跌幅', 0)) or 0
            high = float(row.get('最高', 0)) or 0
            low = float(row.get('最低', 0)) or 0
            volume = float(row.get('成交量', 0)) or 0
            amount = float(row.get('成交额', 0)) or 0
            prev_close = float(row.get('昨收', 0)) or 0
            
            result[code] = {
                'name': name,
                'price': price,
                'change_pct': change_pct,
                'high': high,
                'low': low,
                'volume': volume,
                'amount': amount,
                'prev_close': prev_close,
            }
        
        print(f"  ✅ 指数实时行情(akshare): {len(result)} 条")
        return result
    except Exception as e:
        print(f"  ❌ 获取指数实时行情失败: {e}")
        return None


def fetch_stock_realtime():
    """获取A股实时行情（data_hub四级降级）
    
    Returns:
        DataFrame or None
        列: 代码, 名称, 最新价, 涨跌幅, 成交量, 成交额, 最高, 最低, 昨收, 今开, 换手率...
    """
    if not _data_hub_ready:
        print(f"  ⚠️  data_hub不可用，尝试akshare兜底: {_data_hub_error}")
        return _fetch_stock_realtime_akshare()
    
    try:
        collector = StockSpotCollector()
        result = collector.collect()
        if not result.success or result.data is None or len(result.data) == 0:
            print(f"  ⚠️  data_hub A股实时行情获取失败，尝试akshare兜底")
            return _fetch_stock_realtime_akshare()
        
        df = result.data.copy()
        # 转换列名为中文（兼容后续代码）
        col_map = {
            'symbol': '代码',
            'name': '名称',
            'price': '最新价',
            'change_pct': '涨跌幅',
            'change': '涨跌额',
            'open': '今开',
            'high': '最高',
            'low': '最低',
            'prev_close': '昨收',
            'volume': '成交量',
            'amount': '成交额',
            'turnover': '换手率',
            'tick_time': '时间戳',
            'ts_code': 'ts_code',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        
        print(f"  ✅ A股实时行情: {len(df)} 只 (源: {result.source})")
        return df
    except Exception as e:
        print(f"  ❌ data_hub获取A股实时行情失败: {e}，尝试akshare兜底")
        return _fetch_stock_realtime_akshare()


def _fetch_stock_realtime_akshare():
    """备用：直接用 akshare 获取A股实时行情"""
    if not _ak_ready:
        return None
    
    try:
        df = ak.stock_zh_a_spot()
        if df is None or len(df) == 0:
            return None
        
        print(f"  ✅ A股实时行情(akshare): {len(df)} 只")
        return df
    except Exception as e:
        print(f"  ❌ 获取A股实时行情失败: {e}")
        return None


def fetch_zt_pool(date_str):
    """获取东财涨停池
    
    Args:
        date_str: YYYYMMDD 格式
    
    Returns:
        list of dict or None
    """
    if not _ak_ready:
        return None
    
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or len(df) == 0:
            return []
        
        result = df.to_dict('records')
        print(f"  ✅ 涨停池: {len(result)} 只")
        return result
    except Exception as e:
        print(f"  ❌ 获取涨停池失败: {e}")
        return None


def _parse_sina_hq(text):
    """解析新浪行情接口返回的文本
    
    Args:
        text: "var hq_str_xxx=\"field1,field2,...";
    
    Returns:
        dict: {symbol: [field_list]}
    """
    result = {}
    pattern = r'var hq_str_(\w+)="([^"]*)";'
    matches = re.findall(pattern, text)
    for symbol, fields_str in matches:
        fields = fields_str.split(',')
        result[symbol] = fields
    return result


def fetch_futures_realtime():
    """获取新浪股指期货实时行情（IF/IC/IM主力）
    
    Returns:
        dict: {product: {name, price, open, high, low, prev_close, volume, amount, ...}}
    """
    if not _requests_ready:
        return None
    
    try:
        url = 'https://hq.sinajs.cn/list=nf_IF0,nf_IC0,nf_IM0'
        r = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'}, timeout=10)
        if r.status_code != 200:
            return None
        
        parsed = _parse_sina_hq(r.text)
        result = {}
        
        # 字段顺序（新浪期货）:
        # 0: 今开, 1: 昨结, 2: 最低价, 3: 最新价, 4: 最高价, 
        # 5: 买价, 6: 卖价, 7: 昨收, 8: 买量, 9: 卖量, 
        # 10: 持仓量, 11: 成交量, 12: 持仓量变化, ...
        # 实际解析：今开,昨结,最低,最新,最高,买价,卖价,昨收,买量,卖量,持仓量,成交量
        # 更准确的字段（从返回内容看）:
        # 0:今开 1:昨结 2:最低 3:最新 4:最高 5:买价 6:卖价 7:昨收 8:买量 9:卖量
        # 10:持仓量 11:成交量 12:持仓量变化? ...
        
        product_map = {
            'nf_IF0': 'IF',
            'nf_IC0': 'IC',
            'nf_IM0': 'IM',
        }
        
        name_map = {
            'IF': '沪深300股指期货',
            'IC': '中证500股指期货',
            'IM': '中证1000股指期货',
        }
        
        for symbol, product in product_map.items():
            if symbol not in parsed:
                continue
            fields = parsed[symbol]
            if len(fields) < 12:
                continue
            
            try:
                result[product] = {
                    'name': name_map.get(product, product),
                    'price': float(fields[3]),      # 最新价
                    'open': float(fields[0]),       # 今开
                    'high': float(fields[4]),      # 最高
                    'low': float(fields[2]),       # 最低
                    'prev_settle': float(fields[1]),  # 昨结
                    'prev_close': float(fields[7]),   # 昨收
                    'volume': float(fields[11]),    # 成交量
                    'open_interest': float(fields[10]),  # 持仓量
                }
            except (ValueError, IndexError):
                continue
        
        print(f"  ✅ 期货实时行情: {len(result)} 个品种")
        return result if result else None
    except Exception as e:
        print(f"  ❌ 获取期货实时行情失败: {e}")
        return None


def fetch_etf_realtime():
    """获取新浪ETF实时行情
    
    Returns:
        dict: {etf_code: {name, price, open, high, low, prev_close, volume, amount}}
    """
    if not _requests_ready:
        return None
    
    try:
        codes = ['sh510050', 'sh510300', 'sh510500', 'sz159915', 'sh588000']
        url = f'https://hq.sinajs.cn/list={",".join(codes)}'
        r = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'}, timeout=10)
        if r.status_code != 200:
            return None
        
        parsed = _parse_sina_hq(r.text)
        result = {}
        
        # ETF字段:
        # 0:名称 1:今开 2:昨收 3:最新价 4:最高 5:最低 
        # 6:买一价 7:卖一价 8:成交量 9:成交额 ...
        
        name_map = {
            'sh510050': '50ETF',
            'sh510300': '300ETF',
            'sh510500': '500ETF',
            'sz159915': '创业板ETF',
            'sh588000': '科创50ETF',
        }
        
        for code in codes:
            if code not in parsed:
                continue
            fields = parsed[code]
            if len(fields) < 10:
                continue
            
            try:
                result[code] = {
                    'name': name_map.get(code, fields[0]),
                    'price': float(fields[3]),      # 最新价
                    'open': float(fields[1]),       # 今开
                    'high': float(fields[4]),      # 最高
                    'low': float(fields[5]),       # 最低
                    'prev_close': float(fields[2]),   # 昨收
                    'volume': float(fields[8]),     # 成交量
                    'amount': float(fields[9]),     # 成交额
                }
            except (ValueError, IndexError):
                continue
        
        print(f"  ✅ ETF实时行情: {len(result)} 只")
        return result if result else None
    except Exception as e:
        print(f"  ❌ 获取ETF实时行情失败: {e}")
        return None


# ============================================================
#  指标计算
# ============================================================

def calc_futures_basis(index_data, futures_data):
    """计算股指期货升贴水
    
    Args:
        index_data: fetch_index_realtime() 返回的dict
        futures_data: fetch_futures_realtime() 返回的dict
    
    Returns:
        list of dict: [{product, index_name, futures_price, spot_price, 
                        basis, basis_rate, basis_rate_annual, ...}]
    """
    if not index_data or not futures_data:
        return []
    
    # 指数代码映射
    index_code_map = {
        'IF': 'sh000300',  # 沪深300
        'IC': 'sh000905',  # 中证500
        'IM': 'sh000852',  # 中证1000
    }
    
    index_name_map = {
        'IF': '沪深300',
        'IC': '中证500',
        'IM': '中证1000',
    }
    
    result = []
    for product, idx_code in index_code_map.items():
        if product not in futures_data:
            continue
        if idx_code not in index_data:
            continue
        
        f_price = futures_data[product]['price']
        s_price = index_data[idx_code]['price']
        
        if f_price <= 0 or s_price <= 0:
            continue
        
        basis = f_price - s_price
        basis_rate = basis / s_price * 100  # 百分比
        
        # 年化基差率（年化，假设距到期约60天，粗略估算）
        # 实际计算需要知道到期日，这里简化用当季约60天
        days_to_expiry = 60  # 粗略估算
        basis_rate_annual = basis_rate * (365.0 / days_to_expiry)
        
        result.append({
            'product': product,
            'index_name': index_name_map.get(product, product),
            'type': '主力连续',
            'futures_price': round(f_price, 2),
            'spot_price': round(s_price, 2),
            'basis': round(basis, 2),
            'basis_rate': round(basis_rate, 2),
            'basis_rate_annual': round(basis_rate_annual, 2),
            'rank_252': None,  # 午盘版无历史分位
        })
    
    return result


def calc_market_breadth(stock_df, zt_list=None):
    """计算市场广度指标（涨跌停、AR因子等）
    
    Args:
        stock_df: fetch_stock_realtime() 返回的DataFrame
        zt_list: fetch_zt_pool() 返回的涨停列表
    
    Returns:
        dict: {up_count, down_count, flat_count, up_limit, down_limit, 
               ar_ratio, total_amount, concentration_top5z, ...}
    """
    if stock_df is None or len(stock_df) == 0:
        return None
    
    result = {}
    
    # 涨跌家数
    change_col = '涨跌幅'
    if change_col in stock_df.columns:
        changes = stock_df[change_col].astype(float)
        up_count = int((changes > 0).sum())
        down_count = int((changes < 0).sum())
        flat_count = int((changes == 0).sum())
        result['up_count'] = up_count
        result['down_count'] = down_count
        result['flat_count'] = flat_count
        result['total_stocks'] = len(stock_df)
        
        # AR（涨跌比）= 上涨家数 / 下跌家数
        ar_ratio = up_count / max(down_count, 1)
        result['ar_ratio'] = round(ar_ratio, 2)
        
        # 上涨股数占比
        result['up_ratio'] = round(up_count / max(len(stock_df), 1) * 100, 1)
    
    # 成交额
    amount_col = '成交额'
    if amount_col in stock_df.columns:
        amounts = stock_df[amount_col].astype(float)
        total_amount = amounts.sum()
        result['total_amount'] = round(total_amount / 1e12, 2)  # 万亿
        
        # TOP5%成交集中度
        sorted_amounts = amounts.sort_values(ascending=False)
        top5pct_idx = int(len(sorted_amounts) * 0.05)
        if top5pct_idx > 0:
            top5z_amount = sorted_amounts.iloc[:top5pct_idx].sum()
            top5z_ratio = top5z_amount / total_amount * 100
            result['top5z_concentration'] = round(top5z_ratio, 2)
        
        # TOP10%成交集中度
        top10pct_idx = int(len(sorted_amounts) * 0.1)
        if top10pct_idx > 0:
            top10z_amount = sorted_amounts.iloc[:top10pct_idx].sum()
            top10z_ratio = top10z_amount / total_amount * 100
            result['top10z_concentration'] = round(top10z_ratio, 2)
    
    # 涨跌停
    if zt_list is not None:
        result['up_limit'] = len(zt_list)
    else:
        result['up_limit'] = 0
    
    # 跌停数（从实时行情估算：跌幅>=9.8%视为跌停
    if change_col in stock_df.columns:
        # 考虑ST股跌停是 -5%，普通是 -10%，粗略用 -9.5% 估算
        down_limit_count = int((changes <= -9.5).sum())
        result['down_limit'] = int(down_limit_count)
    
    # 涨跌停比
    up = result.get('up_limit', 0)
    down = result.get('down_limit', 0)
    if down > 0:
        result['zt_dt_ratio'] = round(up / down, 2)
    else:
        result['zt_dt_ratio'] = up if up > 0 else 0
    
    # VIX估算（用日内振幅近似）
    # 方法：用全市场振幅的加权平均作为波动率代理
    high_col = '最高'
    low_col = '最低'
    prev_col = '昨收'
    if all(c in stock_df.columns for c in [high_col, low_col, prev_col]):
        try:
            highs = stock_df[high_col].astype(float)
            lows = stock_df[low_col].astype(float)
            prevs = stock_df[prev_col].astype(float)
            # 日内振幅 = (最高 - 最低) / 昨收 * 100
            intraday_range = ((highs - lows) / prevs.replace(0, np.nan)) * 100
            intraday_range = intraday_range.dropna()
            # 取中位数避免极端值影响
            median_range = float(np.median(intraday_range)) if len(intraday_range) > 0 else 0
            # 年化波动率近似：单日振幅 * sqrt(252) 作为年化波动率估算
            vix_estimate = median_range * np.sqrt(252)
            result['vix_estimate'] = round(vix_estimate, 2)
            result['intraday_range_median'] = round(median_range, 2)
        except Exception:
            pass
    
    return result


def calc_intraday_amplitude(index_data):
    """计算主要指数的日内振幅（用于VIX近似）
    
    Args:
        index_data: fetch_index_realtime() 返回的dict
    
    Returns:
        dict: {index_name: {amplitude_pct, ...}}
    """
    if not index_data:
        return {}
    
    result = {}
    name_map = {
        'sh000300': '沪深300',
        'sh000001': '上证指数',
        'sh000905': '中证500',
        'sh000852': '中证1000',
        'sz399006': '创业板指',
        'sh000688': '科创50',
        'sh000016': '上证50',
    }
    
    for code, info in index_data.items():
        name = name_map.get(code, info.get('name', code))
        high = info.get('high', 0)
        low = info.get('low', 0)
        prev = info.get('prev_close', 0)
        
        if prev > 0 and high > 0 and low > 0:
            amplitude = (high - low) / prev * 100
            # 年化VIX近似
            vix_approx = amplitude * np.sqrt(252)
            result[name] = {
                'amplitude_pct': round(amplitude, 2),
                'vix_approx': round(vix_approx, 2),
            }
    
    return result


def estimate_up50_from_historical(tushare_pro, index_name, current_price, lookback=50):
    """基于历史数据估算up50（股价在50日均线上方的股票占比）
    
    午盘版简化方法：
    1. 用昨日已知的up50值作为基础
    2. 根据指数今日涨跌幅估算up50的变化
    3. 近似公式：up50_today ≈ up50_yesterday + 指数涨跌幅 * 敏感系数
    
    由于午盘没有完整日线数据，此为估算值
    
    Args:
        tushare_pro: tushare pro接口
        index_name: 指数名称
        current_price: 当前指数当前价格
        lookback: 均线周期
    
    Returns:
        float or None
    """
    # 午盘版无法精确计算up50，返回None让主程序处理
    return None


def get_vix_percentile_approx(vix_value, index_name='沪深300'):
    """基于历史波动率百分位估算VIX分位数
    
    午盘版简化：使用固定阈值判断
    
    Args:
        vix_value: 估算的VIX值
        index_name: 指数名称
    
    Returns:
        float: 估算的百分位 (0-100)
    """
    # A股常见波动水平：IV通常在15%-40%之间
    # 简化映射：20%→20分位，30%→50分位，40%→80分位，50%→95分位
    if vix_value is None:
        return None
    
    if vix_value <= 15:
        return 5.0
    elif vix_value <= 20:
        return 20.0
    elif vix_value <= 25:
        return 40.0
    elif vix_value <= 30:
        return 60.0
    elif vix_value <= 35:
        return 75.0
    elif vix_value <= 40:
        return 85.0
    elif vix_value <= 50:
        return 95.0
    else:
        return 99.0


# ============================================================
#  综合获取：一次性获取所有午盘数据
# ============================================================

def fetch_all_midday_data(trade_date):
    """获取所有午盘实时数据
    
    Args:
        trade_date: YYYYMMDD 格式
    
    Returns:
        dict with keys:
        - index_data: 指数实时行情
        - stock_data: A股实时行情DataFrame
        - zt_list: 涨停池列表
        - futures_data: 期货实时行情
        - etf_data: ETF实时行情
        - basis_data: 股指期货基差
        - market_breadth: 市场广度指标
        - index_amplitude: 指数日内振幅
    """
    print("📡 获取午盘实时数据...")
    
    result = {}
    
    # 1. 指数实时行情
    print("\n--- 指数实时行情 ---")
    result['index_data'] = fetch_index_realtime()
    
    # 2. A股实时行情
    print("\n--- A股实时行情 ---")
    result['stock_data'] = fetch_stock_realtime()
    
    # 3. 涨停池
    print("\n--- 涨停池 ---")
    result['zt_list'] = fetch_zt_pool(trade_date)
    
    # 4. 期货实时行情
    print("\n--- 期货实时行情 ---")
    result['futures_data'] = fetch_futures_realtime()
    
    # 5. ETF实时行情
    print("\n--- ETF实时行情 ---")
    result['etf_data'] = fetch_etf_realtime()
    
    # 6. 计算基差
    print("\n--- 计算股指期货基差 ---")
    result['basis_data'] = calc_futures_basis(
        result.get('index_data'),
        result.get('futures_data')
    )
    if result['basis_data']:
        for b in result['basis_data']:
            print(f"  {b['product']} ({b['index_name']}): 基差{b['basis_rate']:+.2f}% (年化{b['basis_rate_annual']:+.2f}%)")
    
    # 7. 计算市场广度
    print("\n--- 计算市场广度指标 ---")
    result['market_breadth'] = calc_market_breadth(
        result.get('stock_data'),
        result.get('zt_list')
    )
    if result['market_breadth']:
        mb = result['market_breadth']
        print(f"  涨/跌/平: {mb.get('up_count', 0)}/{mb.get('down_count', 0)}/{mb.get('flat_count', 0)}")
        print(f"  全A成交额: {mb.get('total_amount', 0)} 万亿")
        print(f"  涨跌停: {mb.get('up_limit', 0)}/{mb.get('down_limit', 0)}")
        print(f"  TOP5%成交集中度: {mb.get('top5z_concentration', 0)}%")
        if 'vix_estimate' in mb:
            print(f"  VIX估算值: {mb['vix_estimate']}%")
    
    # 8. 指数日内振幅
    print("\n--- 指数日内振幅 ---")
    result['index_amplitude'] = calc_intraday_amplitude(result.get('index_data'))
    for name, info in result['index_amplitude'].items():
        print(f"  {name}: 振幅{info['amplitude_pct']}% VIX≈{info['vix_approx']}%")
    
    return result


# ============================================================
#  自测
# ============================================================

if __name__ == '__main__':
    today = datetime.now().strftime('%Y%m%d')
    data = fetch_all_midday_data(today)
    print("\n\n✅ 数据获取完成")


# ============================================================
#  实时期权数据获取与SKEW/GEX计算（午盘增强）
# ============================================================

# 股指期权标的配置
_INDEX_OPTION_MAP = {
    '上证50': {
        'name': '上证50股指期权',
        'prefix': 'ho',
        'underlying_index': 'sh000016',
        'ak_func': 'option_cffex_sz50_spot_sina',
    },
    '沪深300': {
        'name': '沪深300股指期权',
        'prefix': 'io',
        'underlying_index': 'sh000300',
        'ak_func': 'option_cffex_hs300_spot_sina',
    },
    '中证1000': {
        'name': '中证1000股指期权',
        'prefix': 'mo',
        'underlying_index': 'sh000852',
        'ak_func': 'option_cffex_zz1000_spot_sina',
    },
}


def _get_current_option_month():
    """获取当前期权主力月份
    股指期权到期日：每月第三个周五
    若距离当月到期日不足5天，切换到下月
    
    返回格式: '2607' (YYMM)
    """
    now = datetime.now()
    year = now.year
    month = now.month
    
    # 计算当月第三个周五
    import calendar
    cal = calendar.monthcalendar(year, month)
    # 周五是第4天（0=周一, 4=周五）
    fridays = [week[4] for week in cal if week[4] != 0]
    if len(fridays) >= 3:
        third_friday = fridays[2]
    else:
        third_friday = fridays[-1] if fridays else 15
    
    expiry_day = datetime(year, month, third_friday)
    days_to_expiry = (expiry_day - now).days
    
    # 如果距离到期不足5天，用下月
    if days_to_expiry < 5:
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    
    return f'{str(year)[-2:]}{month:02d}'


def _get_next_option_month(current_month_str):
    """获取下一个月份的期权合约代码
    Args:
        current_month_str: '2607' 格式
    Returns:
        '2608' 格式
    """
    year = 2000 + int(current_month_str[:2])
    month = int(current_month_str[2:])
    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    return f'{str(year)[-2:]}{month:02d}'


def fetch_option_realtime():
    """获取实时期权行情（股指期权）
    
    数据源：新浪财经股指期权T型报价
    覆盖标的：上证50(HO)、沪深300(IO)、中证1000(MO)
    
    Returns:
        dict: {index_name: {calls: DataFrame, puts: DataFrame, underlying_price: float, strike: list}}
        失败返回 None
    """
    if not _ak_ready:
        return None
    
    month = _get_current_option_month()
    result = {}
    
    for idx_name, config in _INDEX_OPTION_MAP.items():
        try:
            prefix = config['prefix']
            symbol = f'{prefix}{month}'
            
            # 调用对应的akshare函数
            func_name = config['ak_func']
            if hasattr(ak, func_name):
                func = getattr(ak, func_name)
                df = func(symbol=symbol)
            else:
                continue
            
            if df is None or len(df) == 0:
                continue
            
            # 解析T型报价数据
            # 列: 看涨合约-买量, 看涨合约-买价, 看涨合约-最新价, 看涨合约-卖价, 看涨合约-卖量, 
            #      看涨合约-持仓量, 看涨合约-涨跌, 行权价, 看涨合约-标识,
            #      看跌合约-买量, 看跌合约-买价, 看跌合约-最新价, 看跌合约-卖价, 看跌合约-卖量,
            #      看跌合约-持仓量, 看跌合约-涨跌, 看跌合约-标识
            
            calls = []
            puts = []
            strikes = []
            
            for _, row in df.iterrows():
                strike = float(row.get('行权价', 0))
                if strike <= 0:
                    continue
                strikes.append(strike)
                
                # 看涨合约
                call_price = float(row.get('看涨合约-最新价', 0) or 0)
                call_oi = float(row.get('看涨合约-持仓量', 0) or 0)
                call_vol = float(row.get('看涨合约-买量', 0) or 0) + float(row.get('看涨合约-卖量', 0) or 0)
                calls.append({
                    'strike': strike,
                    'price': call_price,
                    'oi': call_oi,
                    'volume': call_vol,
                    'code': str(row.get('看涨合约-标识', '')),
                })
                
                # 看跌合约
                put_price = float(row.get('看跌合约-最新价', 0) or 0)
                put_oi = float(row.get('看跌合约-持仓量', 0) or 0)
                put_vol = float(row.get('看跌合约-买量', 0) or 0) + float(row.get('看跌合约-卖量', 0) or 0)
                puts.append({
                    'strike': strike,
                    'price': put_price,
                    'oi': put_oi,
                    'volume': put_vol,
                    'code': str(row.get('看跌合约-标识', '')),
                })
            
            if not calls or not puts:
                continue
            
            # 估算标的价格（用平值期权的看涨+看跌价格反推近似）
            # 简化：找看涨和看跌价格最接近的行权价作为ATM
            min_diff = float('inf')
            atm_strike = None
            atm_call_price = 0
            atm_put_price = 0
            for c, p in zip(calls, puts):
                if c['strike'] != p['strike']:
                    continue
                diff = abs(c['price'] - p['price'])
                if diff < min_diff and c['price'] > 0 and p['price'] > 0:
                    min_diff = diff
                    atm_strike = c['strike']
                    atm_call_price = c['price']
                    atm_put_price = p['price']
            
            # 用看涨看跌平价估算标的价格: S ≈ C - P + K (忽略利率和股息)
            if atm_strike and atm_call_price > 0 and atm_put_price > 0:
                underlying_price = atm_call_price - atm_put_price + atm_strike
            else:
                underlying_price = atm_strike if atm_strike else 0
            
            result[idx_name] = {
                'calls': calls,
                'puts': puts,
                'strikes': strikes,
                'underlying_price': round(underlying_price, 2),
                'atm_strike': atm_strike,
                'option_symbol': symbol,
                'contract_count': len(calls),
            }
            
            print(f"  ✅ {idx_name}期权({symbol}): {len(calls)}个行权价, 标的≈{underlying_price:.2f}")
            
        except Exception as e:
            print(f"  ❌ 获取{idx_name}实时期权失败: {e}")
            continue
    
    return result if result else None


def calc_skew_realtime(option_data_dict):
    """基于实时期权链计算简化版SKEW
    
    算法：用平值附近多档期权的看跌/看涨价格不对称性估算偏度
    - 深度虚值期权价格触底（最小报价单位）时用近ATM档位
    - 计算看跌侧与看涨侧的平均价格比
    - 映射到CBOE SKEW范围（约100-150）
    
    Args:
        option_data_dict: fetch_option_realtime() 返回的dict
    
    Returns:
        dict: {index_name: {cboe_skew, iv_skew_approx, atm_price, skew_level}}
    """
    if not option_data_dict:
        return None
    
    result = {}
    
    for idx_name, opt_data in option_data_dict.items():
        try:
            calls = opt_data['calls']
            puts = opt_data['puts']
            S = opt_data['underlying_price']
            
            if S <= 0 or not calls or not puts:
                continue
            
            n = len(calls)
            if n < 5:  # 至少5档才有意义
                continue
            
            # 找平值（ATM）索引 - 找看涨看跌价差最小的
            min_diff = float('inf')
            atm_idx = n // 2  # 默认中间
            for i in range(n):
                c_price = calls[i]['price']
                p_price = puts[i]['price']
                if c_price > 0 and p_price > 0:
                    diff = abs(c_price - p_price)
                    if diff < min_diff:
                        min_diff = diff
                        atm_idx = i
            
            if atm_idx < 2 or atm_idx >= n - 2:
                continue
            
            atm_call_price = calls[atm_idx]['price']
            atm_put_price = puts[atm_idx]['price']
            atm_price = (atm_call_price + atm_put_price) / 2 if atm_call_price > 0 and atm_put_price > 0 else max(atm_call_price, atm_put_price)
            
            if atm_price <= 0:
                continue
            
            # 计算多档虚值的偏度：用2-5档的平均
            # 看跌侧（低行权价）和看涨侧（高行权价）的价格对比
            put_side_prices = []
            call_side_prices = []
            
            # 取ATM两侧对称的几档（2-5档）
            for offset in range(2, min(6, n - atm_idx, atm_idx + 1)):
                p_price = puts[atm_idx - offset]['price']  # 低行权价看跌（OTM put）
                c_price = calls[atm_idx + offset]['price']  # 高行权价看涨（OTM call）
                if p_price > 0 and c_price > 0:
                    put_side_prices.append(p_price)
                    call_side_prices.append(c_price)
            
            if not put_side_prices or not call_side_prices:
                # 如果对称档位不够，用非对称的方式估算
                # 找几个OTM put和OTM call
                for i in range(max(0, atm_idx - 5), atm_idx):
                    if puts[i]['price'] > 0:
                        put_side_prices.append(puts[i]['price'])
                for i in range(atm_idx + 1, min(n, atm_idx + 6)):
                    if calls[i]['price'] > 0:
                        call_side_prices.append(calls[i]['price'])
            
            if not put_side_prices or not call_side_prices:
                continue
            
            avg_put = np.mean(put_side_prices)
            avg_call = np.mean(call_side_prices)
            
            if avg_call <= 0:
                continue
            
            # 偏度比率 = (OTM put均价 - OTM call均价) / ATM价格
            skew_ratio = (avg_put - avg_call) / atm_price
            
            # 映射到CBOE SKEW范围（100-150）
            # 正常市场：put略贵于call → skew_ratio ≈ 0.2~0.5 → SKEW ≈ 115~125
            # 恐慌时：put贵很多 → skew_ratio > 1 → SKEW > 135
            # 乐观时：call更贵 → skew_ratio < 0 → SKEW < 110
            cboe_skew = round(115 + skew_ratio * 20, 2)
            cboe_skew = max(95, min(155, cboe_skew))
            
            # 偏度等级
            if cboe_skew >= 135:
                skew_level = '🔴 极端尾部风险'
            elif cboe_skew >= 125:
                skew_level = '🟠 尾部风险偏高'
            elif cboe_skew >= 115:
                skew_level = '⚪ 中性'
            elif cboe_skew >= 105:
                skew_level = '🟢 尾部风险低'
            else:
                skew_level = '🟢 极低尾部风险'
            
            # ===== ATM IV 估算 =====
            # 用 ATM 期权均价近似反推：IV ≈ atm_price * sqrt(2π) / (S * sqrt(T))
            # T 取约30天到期（股指期权当月合约通常约30天）
            T_days = 30
            T = T_days / 365.0
            if S > 0 and T > 0:
                atm_iv_approx = atm_price * np.sqrt(2 * np.pi) / (S * np.sqrt(T))
                # 限制在合理范围 (5% ~ 150%)
                atm_iv_approx = max(0.05, min(1.5, atm_iv_approx))
                atm_iv_pct = round(atm_iv_approx * 100, 2)  # 百分比形式
            else:
                atm_iv_pct = None

            # ===== PCR 计算 =====
            # vol_pcr: 成交量PCR（买量+卖量近似成交量）
            total_call_vol = sum(c.get('volume', 0) for c in calls if c.get('volume', 0) > 0)
            total_put_vol = sum(p.get('volume', 0) for p in puts if p.get('volume', 0) > 0)
            if total_call_vol > 0:
                vol_pcr = round(total_put_vol / total_call_vol, 2)
            else:
                vol_pcr = None

            # amt_pcr: 持仓量PCR（午盘成交额数据不足，用持仓量近似）
            total_call_oi = sum(c.get('oi', 0) for c in calls if c.get('oi', 0) > 0)
            total_put_oi = sum(p.get('oi', 0) for p in puts if p.get('oi', 0) > 0)
            if total_call_oi > 0:
                amt_pcr = round(total_put_oi / total_call_oi, 2)
            else:
                # 持仓量不可用时用成交量PCR兜底
                amt_pcr = vol_pcr  # 统一口径：不再单独计算amt_pcr

            result[idx_name] = {
                'cboe_skew': cboe_skew,
                'skew_ratio': round(skew_ratio, 4),
                'atm_price': round(atm_price, 2),
                'atm_iv': atm_iv_pct,
                'avg_otm_put': round(avg_put, 2),
                'avg_otm_call': round(avg_call, 2),
                'underlying_price': S,
                'vol_pcr': vol_pcr,
                'amt_pcr': amt_pcr,
                'label': skew_level,
                'is_midday_est': True,
                'note': f'午盘实时估算（{len(put_side_prices)}档OTM近似）',
            }
            
        except Exception as e:
            print(f"  ⚠️  {idx_name} SKEW计算失败: {e}")
            continue
    
    return result if result else None


def calc_gex_realtime(option_data_dict):
    """基于实时期权链计算简化版GEX（Gamma敞口）
    
    简化算法：
    - 假设ATM Gamma最大，向两边递减（高斯近似）
    - 用持仓量估算总Gamma暴露
    - 认购GEX为正（做市商做空看涨→涨了要买→稳定）
    - 认沽GEX为负（做市商做空看跌→跌了要卖→加剧）
    
    Args:
        option_data_dict: fetch_option_realtime() 返回的dict
    
    Returns:
        dict: {index_name: {total_gex, gex_yi, risk_level, flip_point_est}}
    """
    if not option_data_dict:
        return None
    
    result = {}
    
    for idx_name, opt_data in option_data_dict.items():
        try:
            calls = opt_data['calls']
            puts = opt_data['puts']
            S = opt_data['underlying_price']
            
            if S <= 0 or not calls or not puts:
                continue
            
            total_gex = 0.0
            
            # 简化Gamma模型：ATM处gamma最大，随moneyness偏离递减
            # gamma(K) = gamma_atm * exp(-(ln(K/S))^2 / (2 * sigma^2))
            # 这里用简化的高斯函数近似
            
            # 先估算ATM的gamma系数（用ATM期权价格和vega近似）
            # 简化：假设ATM gamma = 0.08 (股指期权典型值)
            # 合约乘数：股指期权每点100元
            contract_multiplier = 100  # 股指期权合约乘数
            
            # 波动率参数（控制gamma曲线宽度）
            sigma_approx = 0.2  # 假设20%波动率
            
            for i, c in enumerate(calls):
                K = c['strike']
                if K <= 0:
                    continue
                
                oi = c['oi']
                if oi <= 0:
                    # 如果没有持仓量，用成交量的一半近似
                    oi = c.get('volume', 0) * 0.5
                if oi <= 0:
                    continue
                
                # 计算moneyness
                moneyness = K / S
                ln_m = np.log(moneyness) if moneyness > 0 else 0
                
                # Gamma因子（高斯近似）
                # 假设剩余期限约0.1年（约30天）
                T = 30.0 / 365.0
                sigma_sqrt_T = sigma_approx * np.sqrt(T) if sigma_approx > 0 else 0.1
                
                if sigma_sqrt_T > 0:
                    gamma_factor = np.exp(-0.5 * (ln_m / sigma_sqrt_T) ** 2)
                else:
                    gamma_factor = max(0, 1 - abs(moneyness - 1) * 5)
                
                # 每合约Gamma敞口
                # Gamma * S * 0.01 = 1%价格变动对应的Delta变化量
                # 再乘以合约乘数和持仓量
                gex_per_contract = oi * gamma_factor * contract_multiplier * S * 0.01
                
                # 认购期权：做市商做空看涨 → GEX为正（涨→买入→推升）
                total_gex += gex_per_contract
            
            for i, p in enumerate(puts):
                K = p['strike']
                if K <= 0:
                    continue
                
                oi = p['oi']
                if oi <= 0:
                    oi = p.get('volume', 0) * 0.5
                if oi <= 0:
                    continue
                
                moneyness = K / S
                ln_m = np.log(moneyness) if moneyness > 0 else 0
                
                T = 30.0 / 365.0
                sigma_sqrt_T = sigma_approx * np.sqrt(T) if sigma_approx > 0 else 0.1
                
                if sigma_sqrt_T > 0:
                    gamma_factor = np.exp(-0.5 * (ln_m / sigma_sqrt_T) ** 2)
                else:
                    gamma_factor = max(0, 1 - abs(moneyness - 1) * 5)
                
                gex_per_contract = oi * gamma_factor * contract_multiplier * S * 0.01
                
                # 认沽期权：做市商做空看跌 → GEX为负（跌→卖出→加剧）
                total_gex -= gex_per_contract
            
            # 转换为亿元
            total_gex_yi = round(total_gex / 1e8, 2)
            
            # 风险等级
            if total_gex_yi >= 20:
                risk_level = '🟢 强Gamma支撑'
            elif total_gex_yi >= 10:
                risk_level = '🟢 Gamma支撑'
            elif total_gex_yi >= 0:
                risk_level = '⚪ 中性'
            elif total_gex_yi >= -10:
                risk_level = '🟠 Gamma压制'
            else:
                risk_level = '🔴 强Gamma压制'
            
            # 估算Gamma翻转点（简化）
            # 翻转点≈ATM + (总GEX / 日波动幅度) 的偏移
            flip_point_est = round(S * (1 + np.sign(total_gex) * 0.02), 2) if total_gex != 0 else round(S, 2)
            
            result[idx_name] = {
                'total_gex': round(total_gex, 0),
                'total_gex_yi': total_gex_yi,
                'risk_level': risk_level,
                'flip_point_est': flip_point_est,
                'underlying_price': S,
                'contract_count': len(calls),
                'is_midday_est': True,
                'note': '午盘实时估算（高斯Gamma近似）',
            }
            
        except Exception as e:
            print(f"  ⚠️  {idx_name} GEX计算失败: {e}")
            continue
    
    return result if result else None


# ============================================================
#  CFC午盘估算（成分股实时涨幅 + 权重）
# ============================================================

# 指数代码 → 中证指数代码映射
_CFC_INDEX_MAP = {
    '上证50': '000016',
    '沪深300': '000300',
    '中证500': '000905',
    '中证1000': '000852',
    '创业板指': '399006',
    '科创50': '000688',
}

# 缓存成分股权重（避免重复获取）
_weight_cache = {}


def fetch_index_weights(index_code):
    """获取指数成分股权重（优先使用data_hub）
    
    降级策略：
    1. data_hub IndexComponentsCollector（三级降级：akshare→tushare→web）
    2. 中证指数官方权重（ak.index_stock_cons_weight_csindex）— 沪市指数
    3. 东财成分股+等权重（ak.index_stock_cons）— 深市指数
    
    Args:
        index_code: 指数代码，如 '000300'（沪深300）、'399006'（创业板指）
    
    Returns:
        dict: {stock_code: weight} 或 None
    """
    global _weight_cache
    
    if index_code in _weight_cache:
        return _weight_cache[index_code]
    
    weights = None
    
    # ===== Level 1: data_hub IndexComponentsCollector（主数据源） =====
    if _data_hub_ready and IndexComponentsCollector is not None:
        try:
            # 转为 tushare 格式代码
            if '.' not in index_code:
                if index_code.startswith('399'):
                    ts_code = index_code + '.SZ'
                elif index_code.startswith(('000', '001', '002', '003', '688')):
                    ts_code = index_code + '.SH'
                else:
                    ts_code = index_code + '.SH'
            else:
                ts_code = index_code
            
            collector = IndexComponentsCollector()
            result = collector.collect(index_code=ts_code)
            if result.success and result.data and len(result.data) >= 10:
                components = result.data
                n = len(components)
                equal_weight = 100.0 / n
                weights = {}
                for code in components:
                    # 提取6位代码
                    if '.' in code:
                        num = code.split('.')[0]
                    else:
                        num = code
                    num = num.zfill(6)
                    if len(num) == 6:
                        weights[num] = equal_weight
                if len(weights) >= 10:
                    print(f"  ✅ {index_code} 成分股权重(data_hub, {result.source}): {len(weights)}只")
                    _weight_cache[index_code] = weights
                    return weights
        except Exception as e:
            print(f"  ⚠️  data_hub获取成分股权重失败({index_code}): {e}")
    
    # ===== Level 2: 中证指数官方权重 =====
    if not weights and _ak_ready:
        try:
            df = ak.index_stock_cons_weight_csindex(symbol=index_code)
            if df is not None and len(df) > 0:
                weights = {}
                for _, row in df.iterrows():
                    code = str(row.get('成分券代码', ''))
                    weight = float(row.get('权重', 0) or 0)
                    if code and weight > 0:
                        code = code.zfill(6)
                        weights[code] = weight
        except Exception as e:
            # 深证指数（399开头）中证接口不支持，跳过
            if not index_code.startswith('399'):
                print(f"  ⚠️  中证权重接口失败({index_code}): {e}")
    
    # ===== Level 3: 东财成分股 + 等权重 =====
    if not weights and _ak_ready:
        try:
            df = ak.index_stock_cons(symbol=index_code)
            if df is not None and len(df) > 0:
                weights = {}
                code_col = None
                # 尝试不同列名
                for col in ['品种代码', '代码', '成分券代码', '股票代码']:
                    if col in df.columns:
                        code_col = col
                        break
                
                if code_col:
                    n = len(df)
                    equal_weight = 100.0 / n  # 等权重，总和100%
                    for _, row in df.iterrows():
                        code = str(row.get(code_col, ''))
                        code = code.zfill(6)
                        if code and len(code) == 6 and code[0] in '012345689':
                            weights[code] = equal_weight
                
                if len(weights) < 10:
                    weights = None
                    print(f"  ⚠️  东财成分股接口返回有效样本不足({index_code})")
                else:
                    print(f"  ℹ️  使用东财成分股+等权重({index_code}): {len(weights)}只")
        except Exception as e:
            print(f"  ⚠️  东财成分股接口失败({index_code}): {e}")
    
    if weights:
        _weight_cache[index_code] = weights
        print(f"  ✅ {index_code} 成分股权重: {len(weights)}只")
        return weights
    
    print(f"  ❌ 获取{index_code}成分股权重失败")
    return None


def calc_cfc_midday(stock_df, index_name_list=None):
    """午盘CFC结构因子估算
    
    算法：用成分股实时涨跌幅的加权离散度估算CFC
    CFC = 加权标准差 / 加权平均涨跌幅的绝对值
    值越大说明集中度越高（分化越大）
    
    Args:
        stock_df: fetch_stock_realtime() 返回的DataFrame
        index_name_list: 要计算的指数列表，默认计算所有可计算的
    
    Returns:
        dict: {index_name: {cfc_value, up_count, down_count, avg_change, std_change, note}}
    """
    if stock_df is None or len(stock_df) == 0:
        return None
    
    if index_name_list is None:
        index_name_list = list(_CFC_INDEX_MAP.keys())
    
    # 构建股票代码 → 涨跌幅映射
    change_map = {}
    amount_map = {}
    
    for _, row in stock_df.iterrows():
        code = str(row.get('代码', ''))
        # 去除前缀（sh/sz/bj等）
        if len(code) > 6:
            code = code[-6:]
        try:
            change = float(row.get('涨跌幅', 0))
            amount = float(row.get('成交额', 0) or 0)
            change_map[code] = change
            amount_map[code] = amount
        except (ValueError, TypeError):
            continue
    
    result = {}
    
    for idx_name in index_name_list:
        try:
            idx_code = _CFC_INDEX_MAP.get(idx_name)
            if not idx_code:
                continue
            
            weights = fetch_index_weights(idx_code)
            if not weights:
                continue
            
            # 匹配有实时行情的成分股
            matched_changes = []
            matched_weights = []
            matched_amounts = []
            
            for stock_code, weight in weights.items():
                if stock_code in change_map:
                    matched_changes.append(change_map[stock_code])
                    matched_weights.append(weight)
                    matched_amounts.append(amount_map.get(stock_code, 0))
            
            if len(matched_changes) < 30:  # 至少30只才有意义
                continue
            
            # 匹配率低于60%也跳过
            match_ratio = len(matched_changes) / len(weights)
            if match_ratio < 0.6:
                continue
            
            # 归一化权重
            total_weight = sum(matched_weights)
            if total_weight <= 0:
                continue
            norm_weights = [w / total_weight for w in matched_weights]
            
            # 加权平均涨跌幅
            changes_arr = np.array(matched_changes)
            weights_arr = np.array(norm_weights)
            
            weighted_avg = float(np.sum(changes_arr * weights_arr))
            
            # 加权标准差
            weighted_var = float(np.sum(weights_arr * (changes_arr - weighted_avg) ** 2))
            weighted_std = float(np.sqrt(weighted_var))
            
            # CFC = 离散度 / |平均涨跌幅|
            # 如果平均涨跌幅接近0，用绝对值避免除零
            avg_abs = max(abs(weighted_avg), 0.1)
            cfc_value = round(weighted_std / avg_abs, 2)
            
            # 涨跌家数
            up_count = int((changes_arr > 0).sum())
            down_count = int((changes_arr < 0).sum())
            
            result[idx_name] = {
                'cfc_value': cfc_value,
                'weighted_avg_change': round(weighted_avg, 2),
                'weighted_std': round(weighted_std, 2),
                'up_count': up_count,
                'down_count': down_count,
                'matched_count': len(matched_changes),
                'total_count': len(weights),
                'is_midday_est': True,
                'note': '午盘估算（成分股实时加权离散度）',
            }
            
            print(f"  ✅ {idx_name} CFC≈{cfc_value} (样本{len(matched_changes)}/{len(weights)}只)")
            
        except Exception as e:
            print(f"  ❌ {idx_name} CFC估算失败: {e}")
            continue
    
    return result if result else None


# ============================================================
#  结构因子午盘简化版（AbsFactor / RelFactor）
# ============================================================

def calc_structural_factor_midday(stock_df, index_data=None):
    """午盘结构因子估算
    
    AbsFactor（绝对因子）：基于涨跌幅分布的多空力量对比
    - 正值 = 多头力量 > 空头力量
    - 计算：(上涨家数占比 - 下跌家数占比) * 波动率调整
    
    RelFactor（相对因子）：基于资金流的相对强弱
    - 正值 = 资金流出（风险）
    - 用大额成交占比、涨跌停强度等近似
    
    Args:
        stock_df: fetch_stock_realtime() 返回的DataFrame
        index_data: fetch_index_realtime() 返回的dict
    
    Returns:
        dict: {index_name: {abs_factor, rel_factor, abs_level, rel_level, note}}
    """
    if stock_df is None or len(stock_df) == 0:
        return None
    
    result = {}
    
    # 全市场基础数据
    try:
        changes = stock_df['涨跌幅'].astype(float)
        amounts = stock_df['成交额'].astype(float)
        
        total = len(changes)
        up_count = int((changes > 0).sum())
        down_count = int((changes < 0).sum())
        flat_count = total - up_count - down_count
        
        up_ratio = up_count / max(total, 1)
        down_ratio = down_count / max(total, 1)
        
        # AbsFactor：多头力量 - 空头力量
        # 用涨跌家数比 + 涨跌幅度比综合计算
        avg_up = float(changes[changes > 0].mean()) if (changes > 0).any() else 0
        avg_down = float(abs(changes[changes < 0].mean())) if (changes < 0).any() else 0
        
        # 综合多空强度
        bull_strength = up_ratio * avg_up
        bear_strength = down_ratio * avg_down
        
        # 归一化到 -2 ~ +2 范围（经验系数）
        abs_factor_raw = (bull_strength - bear_strength) * 10
        # 限制范围
        abs_factor = round(max(-2.5, min(2.5, abs_factor_raw)), 2)
        
        # AbsFactor等级
        if abs_factor >= 2:
            abs_level = '🟢 极端多头'
        elif abs_factor >= 1:
            abs_level = '🟢 多头趋势'
        elif abs_factor >= -1:
            abs_level = '⚪ 震荡'
        elif abs_factor >= -2:
            abs_level = '🟠 空头趋势'
        else:
            abs_level = '🔴 极端空头'
        
        # RelFactor：资金流相对强弱（简化估算）
        # 上涨股成交额占比 vs 下跌股成交额占比
        up_amount = float(amounts[changes > 0].sum()) if (changes > 0).any() else 0
        down_amount = float(amounts[changes < 0].sum()) if (changes < 0).any() else 0
        total_amount = float(amounts.sum())
        
        if total_amount > 0:
            # 资金流出指数（上涨成交占比低但上涨家数少 → 资金在大盘股流出）
            # 简化：用下跌成交占比 - 下跌家数占比
            down_amount_ratio = down_amount / total_amount
            rel_factor_raw = (down_amount_ratio - down_ratio) * 5
            rel_factor = round(max(-2.5, min(2.5, rel_factor_raw)), 2)
        else:
            rel_factor = 0
        
        # RelFactor等级
        if rel_factor >= 2:
            rel_level = '🔴 资金极端流出'
        elif rel_factor >= 1:
            rel_level = '🟠 资金流出'
        elif rel_factor >= -1:
            rel_level = '⚪ 中性'
        elif rel_factor >= -2:
            rel_level = '🟢 资金流入'
        else:
            rel_level = '🟢 资金极端流入'
        
        # 万得全A（用全市场数据近似）
        result['万得全A'] = {
            'abs_factor': abs_factor,
            'rel_factor': rel_factor,
            'abs_level': abs_level,
            'rel_level': rel_level,
            'up_count': up_count,
            'down_count': down_count,
            'avg_up_pct': round(avg_up, 2),
            'avg_down_pct': round(avg_down, 2),
            'is_midday_est': True,
            'note': '午盘估算（全市场涨跌幅分布）',
        }
        
        print(f"  ✅ 万得全A AbsFactor≈{abs_factor}, RelFactor≈{rel_factor}")
        
    except Exception as e:
        print(f"  ❌ 全市场结构因子估算失败: {e}")
    
    # 各指数的结构因子（用指数成分股近似）
    # 简化：用指数涨跌幅和振幅估算
    if index_data:
        # 指数 → 代码映射
        index_map = {
            'sh000300': '沪深300',
            'sh000016': '上证50',
            'sh000905': '中证500',
            'sh000852': '中证1000',
            'sz399006': '创业板指',
            'sh000688': '科创50',
        }
        
        for code, name in index_map.items():
            if code not in index_data:
                continue
            idx = index_data[code]
            change_pct = idx.get('change_pct', 0)
            high = idx.get('high', 0)
            low = idx.get('low', 0)
            prev = idx.get('prev_close', 0)
            
            if prev <= 0:
                continue
            
            # 振幅
            amplitude = (high - low) / prev * 100
            
            # AbsFactor：用涨跌幅 * 振幅调整
            # 涨跌幅方向 + 振幅强度
            abs_f = round(change_pct / 3.0, 2)  # 3%涨跌幅对应约1单位
            abs_f = max(-2.5, min(2.5, abs_f))
            
            if abs_f >= 2:
                abs_l = '🟢 极端多头'
            elif abs_f >= 1:
                abs_l = '🟢 多头趋势'
            elif abs_f >= -1:
                abs_l = '⚪ 震荡'
            elif abs_f >= -2:
                abs_l = '🟠 空头趋势'
            else:
                abs_l = '🔴 极端空头'
            
            # RelFactor：用振幅/涨跌幅比估算
            # 振幅大但涨跌小 → 多空博弈激烈 → 资金分歧
            if abs(change_pct) > 0.1:
                rel_f = round((amplitude / abs(change_pct) - 2) * 0.5, 2)
                rel_f = max(-2.5, min(2.5, rel_f))
            else:
                rel_f = 0
            
            if rel_f >= 2:
                rel_l = '🔴 资金极端分歧'
            elif rel_f >= 1:
                rel_l = '🟠 资金分歧'
            elif rel_f >= -1:
                rel_l = '⚪ 中性'
            elif rel_f >= -2:
                rel_l = '🟢 资金一致'
            else:
                rel_l = '🟢 资金极端一致'
            
            result[name] = {
                'abs_factor': abs_f,
                'rel_factor': rel_f,
                'abs_level': abs_l,
                'rel_level': rel_l,
                'change_pct': change_pct,
                'amplitude': round(amplitude, 2),
                'is_midday_est': True,
                'note': '午盘估算（指数涨跌幅+振幅近似）',
            }
    
    return result if result else None


# ============================================================
#  综合获取增强版：午盘所有数据
# ============================================================

def fetch_all_midday_data_enhanced(trade_date, tushare_pro=None, data_hub_get_data=None):
    """获取所有午盘实时数据（增强版，含期权SKEW/GEX、up50/top5z/CDI、结构因子）
    
    注意：CFC午盘估算已移除（离散度公式与收盘版峰度+偏度口径不一致，无参考价值）。
    
    Args:
        trade_date: YYYYMMDD 格式
        tushare_pro: tushare pro接口对象（用于up50的MA50计算）
        data_hub_get_data: data_hub.get_data函数（up50降级用）
    
    Returns:
        dict with all keys from fetch_all_midday_data +:
        - option_data: 实时期权链数据
        - skew_data: 实时SKEW估算
        - gex_data: 实时GEX估算
        - up50_data: 午盘up50估算
        - top5z_data: 午盘top5z估算
        - cdi_data: 午盘CDI估算
        - structural_data: 午盘结构因子估算
    """
    print("📡 获取午盘实时数据（增强版）...")
    
    # 先获取基础数据
    result = fetch_all_midday_data(trade_date)
    
    # ===== 新增：实时期权数据 =====
    print("\n--- 实时期权行情（股指期权）---")
    result['option_data'] = fetch_option_realtime()
    
    # 计算SKEW
    print("\n--- 实时SKEW估算 ---")
    result['skew_data'] = calc_skew_realtime(result.get('option_data'))
    if result['skew_data']:
        for name, data in result['skew_data'].items():
            print(f"  {name}: SKEW≈{data['cboe_skew']} → {data['label']}")
    else:
        print("  ⚪ 无法估算SKEW")
    
    # 计算GEX
    print("\n--- 实时GEX估算 ---")
    result['gex_data'] = calc_gex_realtime(result.get('option_data'))
    if result['gex_data']:
        for name, data in result['gex_data'].items():
            print(f"  {name}: GEX≈{data['total_gex_yi']}亿 → {data['risk_level']}")
    else:
        print("  ⚪ 无法估算GEX")
    
    # ===== CFC午盘估算已移除 =====
    # 原离散度公式（加权标准差/平均涨跌幅绝对值）与收盘版CFC（0.4×峰度+0.6×偏度）
    # 口径完全不同，数值量级差10倍以上，复用收盘分位数阈值无参考价值。
    # calc_cfc_midday 函数保留在文件中以备后用，但此处不再调用。
    
    # ===== 新增：up50午盘估算 =====
    print("\n--- up50午盘估算（四级降级）---")
    up50_indices = list(_CFC_INDEX_MAP.keys()) + ['万得全A']
    result['up50_data'] = calc_up50_midday(
        result.get('stock_data'),
        index_name_list=up50_indices,
        tushare_pro=tushare_pro,
        data_hub_get_data=data_hub_get_data
    )
    
    # ===== 新增：top5z午盘估算 =====
    print("\n--- top5z午盘估算 ---")
    top5z_indices = list(_CFC_INDEX_MAP.keys()) + ['万得全A']
    result['top5z_data'] = calc_top5z_midday(
        result.get('stock_data'),
        index_name_list=top5z_indices
    )
    
    # ===== 新增：CDI午盘估算 =====
    print("\n--- CDI午盘估算 ---")
    cdi_indices = list(_CFC_INDEX_MAP.keys()) + ['万得全A']
    result['cdi_data'] = calc_cdi_midday(
        result.get('stock_data'),
        index_name_list=cdi_indices
    )
    
    # ===== 新增：结构因子午盘估算 =====
    print("\n--- 结构因子午盘估算 ---")
    result['structural_data'] = calc_structural_factor_midday(
        result.get('stock_data'),
        result.get('index_data')
    )
    
    return result


# ============================================================
#  up50 / top5z / CDI 午盘实时估算（四级降级）
# ============================================================

# MA50全局缓存：{截止日期YYYYMMDD: {6位股票代码: ma50值}}
_ma50_cache = {}


def _fetch_ma50_map(end_date, tushare_pro=None, data_hub_get_data=None, lookback=55):
    """获取全市场股票截至end_date的50日均线（五级降级）
    
    降级顺序：
    Level 1: AKShare 成分股日线（核心成分股快速获取）
    Level 2: data_hub StockDailyCollector 全市场按日批量（Tushare兜底）
    Level 3: 直接 Tushare 批量日线（按日期获取全市场）
    Level 4: data_hub / Coze网关 get_data 模式
    Level 5: 网页爬取成分股MA50（简化版）
    
    Args:
        end_date: 截止日期 YYYYMMDD（通常是前一交易日）
        tushare_pro: tushare pro接口对象
        data_hub_get_data: data_hub.get_data函数
        lookback: 回溯自然日数（确保覆盖50个交易日）
    
    Returns:
        dict: {6位股票代码: ma50} 或 None
    """
    global _ma50_cache
    
    if end_date in _ma50_cache:
        print(f"  📊 MA50缓存命中: {end_date}")
        return _ma50_cache[end_date]
    
    end_dt = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback * 2)
    start_date = start_dt.strftime('%Y%m%d')
    
    ma50_map = None
    source_used = None
    
    # ===== Level 1: AKShare 成分股日线（核心成分股快速获取）=====
    if _ak_ready and ma50_map is None:
        try:
            print(f"  📊 [Level1] AKShare获取成分股MA50...")
            # 获取所有配置指数的成分股，按权重排序取Top部分（保证≥60%覆盖率）
            all_stocks_weighted = {}  # {code: max_weight}
            for idx_code in _CFC_INDEX_MAP.values():
                weights = fetch_index_weights(idx_code)
                if weights:
                    for code, w in weights.items():
                        if code not in all_stocks_weighted or w > all_stocks_weighted[code]:
                            all_stocks_weighted[code] = w
            
            if len(all_stocks_weighted) < 50:
                raise ValueError(f"成分股数量不足: {len(all_stocks_weighted)}")
            
            # 按权重从大到小排序，只获取高权重部分（保证核心指数覆盖率）
            sorted_stocks = sorted(all_stocks_weighted.items(), key=lambda x: -x[1])
            # 最多取600只（覆盖主要指数权重前60%），限制总时间
            max_stocks = min(600, len(sorted_stocks))
            target_stocks = [code for code, _ in sorted_stocks[:max_stocks]]
            
            print(f"    共{len(all_stocks_weighted)}只成分股，获取Top{len(target_stocks)}只核心股...")
            
            ma50_map = {}
            success_count = 0
            fail_streak = 0  # 连续失败计数
            
            for i, code in enumerate(target_stocks):
                try:
                    # stock_zh_a_hist 使用纯6位代码，无需sh/sz前缀
                    df = ak.stock_zh_a_hist(
                        symbol=code, period="daily",
                        start_date=start_date, end_date=end_date,
                        adjust="qfq"
                    )
                    if df is not None and len(df) >= 40:
                        close_col = '收盘' if '收盘' in df.columns else 'close'
                        ma50 = float(df[close_col].tail(50).mean())
                        ma50_map[code] = ma50
                        success_count += 1
                        fail_streak = 0
                    else:
                        fail_streak += 1
                    
                    # 快速失败：连续10只失败，说明接口有问题，直接降级
                    if fail_streak >= 10:
                        print(f"    ⚠️  连续{fail_streak}只失败，AKShare接口异常，快速降级")
                        ma50_map = None
                        break
                    
                    # 轻量限速
                    time.sleep(0.05)
                    
                except Exception:
                    fail_streak += 1
                    if fail_streak >= 10:
                        print(f"    ⚠️  连续{fail_streak}只异常，快速降级")
                        ma50_map = None
                        break
                    continue
                
                if (i + 1) % 100 == 0:
                    print(f"    进度: {i+1}/{len(target_stocks)}, 成功: {success_count}")
            
            if ma50_map is not None and success_count >= 100:
                source_used = 'akshare'
                print(f"  ✅ [Level1] AKShare获取成功: {success_count}只核心成分股")
            else:
                raise ValueError(f"AKShare成功数量不足: {success_count}只")
                ma50_map = None
                
        except Exception as e:
            print(f"  ❌ [Level1] AKShare获取失败: {e}")
            ma50_map = None
    
    # ===== Level 2: data_hub StockDailyCollector 全市场按日批量 =====
    if ma50_map is None and _data_hub_ready:
        try:
            print(f"  📊 [Level2] data_hub StockDailyCollector 获取最近50个交易日...")
            
            from data_hub.collectors.stock_daily import StockDailyCollector
            
            # 获取交易日历（向前推找到50个交易日）
            trade_dates = []
            check_date = end_dt
            fail_streak = 0
            
            sd_collector = StockDailyCollector()
            
            while len(trade_dates) < 55 and (end_dt - check_date).days < lookback * 2:
                date_str = check_date.strftime('%Y%m%d')
                try:
                    result = sd_collector.collect(trade_date=date_str)
                    if result.success and result.data is not None and len(result.data) > 100:
                        trade_dates.append((date_str, result.data))
                        fail_streak = 0
                    else:
                        fail_streak += 1
                        if fail_streak >= 7:  # 连续7天失败，大概率越界，停止
                            break
                except Exception:
                    fail_streak += 1
                    if fail_streak >= 7:
                        break
                
                check_date -= timedelta(days=1)
            
            # 取最近50个交易日
            trade_dates = trade_dates[-50:]
            
            if len(trade_dates) < 40:
                raise ValueError(f"data_hub有效交易日不足: {len(trade_dates)}天")
            
            print(f"    共 {len(trade_dates)} 个有效交易日，计算MA50...")
            
            close_sum = {}
            close_count = {}
            
            for i, (date_str, df) in enumerate(trade_dates):
                # 兼容不同列名格式
                code_col = 'ts_code' if 'ts_code' in df.columns else 'code'
                close_col = 'close' if 'close' in df.columns else '收盘'
                
                for _, row in df.iterrows():
                    code_raw = str(row[code_col])
                    code6 = code_raw[:6] if len(code_raw) >= 6 else code_raw.zfill(6)
                    try:
                        close = float(row[close_col])
                        if code6 not in close_sum:
                            close_sum[code6] = 0.0
                            close_count[code6] = 0
                        close_sum[code6] += close
                        close_count[code6] += 1
                    except (ValueError, TypeError):
                        continue
                
                if (i + 1) % 10 == 0:
                    print(f"    进度: {i+1}/{len(trade_dates)} 天...")
            
            ma50_map = {}
            for code6 in close_sum:
                if close_count[code6] >= 40:
                    ma50_map[code6] = close_sum[code6] / close_count[code6]
            
            if len(ma50_map) >= 1000:
                source_used = 'data_hub_stock_daily'
                print(f"  ✅ [Level2] data_hub获取成功: {len(ma50_map)}只股票")
                _ma50_cache[end_date] = ma50_map
                return ma50_map
            else:
                raise ValueError(f"data_hub数据量不足: {len(ma50_map)}只")
                ma50_map = None
                
        except Exception as e:
            print(f"  ❌ [Level2] data_hub获取失败: {e}")
            ma50_map = None
    
    # ===== Level 3: Tushare 批量日线（按日期获取全市场）=====
    if ma50_map is None and tushare_pro is not None:
        try:
            print(f"  📊 [Level2] Tushare获取最近50个交易日日线数据...")
            
            # 获取交易日历
            cal_df = tushare_pro.trade_cal(
                exchange='SSE',
                start_date=start_date,
                end_date=end_date,
                is_open='1'
            )
            if cal_df is None or len(cal_df) == 0:
                raise ValueError("交易日历获取失败")
            
            trade_dates = sorted(cal_df['cal_date'].tolist())
            trade_dates = trade_dates[-50:]  # 取最近50个交易日
            
            if len(trade_dates) < 40:
                raise ValueError(f"交易日不足: {len(trade_dates)}天")
            
            print(f"    共 {len(trade_dates)} 个交易日，按日批量获取...")
            
            close_sum = {}
            close_count = {}
            
            for i, td in enumerate(trade_dates):
                df = tushare_pro.daily(trade_date=td)
                if df is not None and len(df) > 0:
                    for _, row in df.iterrows():
                        code6 = str(row['ts_code'])[:6]
                        close = float(row['close'])
                        if code6 not in close_sum:
                            close_sum[code6] = 0.0
                            close_count[code6] = 0
                        close_sum[code6] += close
                        close_count[code6] += 1
                
                time.sleep(0.55)  # Tushare限速
                
                if (i + 1) % 10 == 0:
                    print(f"    进度: {i+1}/{len(trade_dates)} 天...")
            
            # 计算MA50（至少40天有效）
            ma50_map = {}
            for code6 in close_sum:
                if close_count[code6] >= 40:
                    ma50_map[code6] = close_sum[code6] / close_count[code6]
            
            if len(ma50_map) >= 1000:
                source_used = 'tushare'
                print(f"  ✅ [Level2] Tushare获取成功: {len(ma50_map)}只股票")
            else:
                raise ValueError(f"Tushare数据量不足: {len(ma50_map)}只")
                ma50_map = None
                
        except Exception as e:
            print(f"  ❌ [Level2] Tushare获取失败: {e}")
            ma50_map = None
    
    # ===== Level 3: data_hub / Coze网关 =====
    if ma50_map is None and data_hub_get_data is not None:
        try:
            print(f"  📊 [Level3] data_hub获取最近50个交易日日线数据...")
            
            # 先用单日测试可用性
            test_df = data_hub_get_data("stock_daily", trade_date=end_date)
            if test_df is None or len(test_df) == 0:
                raise ValueError("data_hub单日数据为空")
            
            # 获取交易日历（用Tushare或推算）
            trade_dates = []
            check_date = end_dt
            while len(trade_dates) < 55 and (end_dt - check_date).days < lookback * 2:
                date_str = check_date.strftime('%Y%m%d')
                df = data_hub_get_data("stock_daily", trade_date=date_str)
                if df is not None and len(df) > 100:
                    trade_dates.append(date_str)
                check_date -= timedelta(days=1)
            
            trade_dates = sorted(trade_dates)[-50:]
            
            if len(trade_dates) < 40:
                raise ValueError(f"data_hub有效交易日不足: {len(trade_dates)}天")
            
            print(f"    共 {len(trade_dates)} 个有效交易日...")
            
            close_sum = {}
            close_count = {}
            
            for i, td in enumerate(trade_dates):
                df = data_hub_get_data("stock_daily", trade_date=td)
                if df is not None and len(df) > 0:
                    # 兼容不同列名格式
                    code_col = 'ts_code' if 'ts_code' in df.columns else 'code'
                    close_col = 'close' if 'close' in df.columns else '收盘'
                    
                    for _, row in df.iterrows():
                        code_raw = str(row[code_col])
                        code6 = code_raw[:6] if len(code_raw) >= 6 else code_raw.zfill(6)
                        try:
                            close = float(row[close_col])
                            if code6 not in close_sum:
                                close_sum[code6] = 0.0
                                close_count[code6] = 0
                            close_sum[code6] += close
                            close_count[code6] += 1
                        except (ValueError, TypeError):
                            continue
                
                if (i + 1) % 10 == 0:
                    print(f"    进度: {i+1}/{len(trade_dates)} 天...")
            
            ma50_map = {}
            for code6 in close_sum:
                if close_count[code6] >= 40:
                    ma50_map[code6] = close_sum[code6] / close_count[code6]
            
            if len(ma50_map) >= 1000:
                source_used = 'data_hub'
                print(f"  ✅ [Level3] data_hub获取成功: {len(ma50_map)}只股票")
            else:
                raise ValueError(f"data_hub数据量不足: {len(ma50_map)}只")
                ma50_map = None
                
        except Exception as e:
            print(f"  ❌ [Level3] data_hub获取失败: {e}")
            ma50_map = None
    
    # ===== Level 4: 网页爬取（东财个股页面提取MA50）=====
    if ma50_map is None and _requests_ready:
        try:
            print(f"  📊 [Level4] 网页爬取成分股MA50（简化版）...")
            # 简化：只爬取主要指数Top50成分股，确保有核心数据
            # 实际应用中可扩展为全量爬取
            all_stocks = set()
            for idx_code in _CFC_INDEX_MAP.values():
                weights = fetch_index_weights(idx_code)
                if weights:
                    # 取权重最大的前50只
                    sorted_stocks = sorted(weights.items(), key=lambda x: -x[1])[:50]
                    all_stocks.update([s[0] for s in sorted_stocks])
            
            if len(all_stocks) < 20:
                raise ValueError("爬取样本股不足")
            
            ma50_map = {}
            success_count = 0
            print(f"    爬取 {len(all_stocks)} 只核心成分股...")
            
            for i, code in enumerate(sorted(all_stocks)):
                try:
                    # 东方财富网个股行情页，提取均线数据
                    # 简化实现：构造东财K线API地址
                    secid = f"1.{code}" if code.startswith('6') else f"0.{code}"
                    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
                           f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
                           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                           f"&klt=101&fqt=1&end={end_date}&lmt=60")
                    
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        klines = data.get('data', {}).get('klines', [])
                        if len(klines) >= 40:
                            # 每根K线: 日期,开盘,收盘,最高,最低,成交量,成交额,...
                            closes = [float(k.split(',')[2]) for k in klines[-50:]]
                            ma50 = sum(closes) / len(closes)
                            ma50_map[code] = ma50
                            success_count += 1
                    
                    time.sleep(0.15)
                    
                except Exception:
                    continue
                
                if (i + 1) % 20 == 0:
                    print(f"    进度: {i+1}/{len(all_stocks)}, 成功: {success_count}")
            
            if success_count >= 30:
                source_used = 'web_scrape'
                print(f"  ✅ [Level4] 网页爬取成功: {success_count}只")
            else:
                raise ValueError(f"网页爬取成功数量不足: {success_count}只")
                ma50_map = None
                
        except Exception as e:
            print(f"  ❌ [Level4] 网页爬取失败: {e}")
            ma50_map = None
    
    # 缓存结果（即使失败也缓存None避免重复尝试）
    if ma50_map is not None:
        _ma50_cache[end_date] = ma50_map
        print(f"  💾 MA50数据已缓存（{source_used}），共{len(ma50_map)}只")
    
    return ma50_map


def _get_prev_trade_date(tushare_pro=None):
    """获取前一交易日日期
    
    Args:
        tushare_pro: tushare pro接口对象（可选）
    
    Returns:
        str: YYYYMMDD格式日期
    """
    now = datetime.now()
    today = now.strftime('%Y%m%d')
    
    # 方法1：用Tushare交易日历
    if tushare_pro is not None:
        try:
            start_dt = now - timedelta(days=10)
            cal_df = tushare_pro.trade_cal(
                exchange='SSE',
                start_date=start_dt.strftime('%Y%m%d'),
                end_date=today,
                is_open='1'
            )
            if cal_df is not None and len(cal_df) > 0:
                dates = sorted(cal_df['cal_date'].tolist())
                # 取最近一个不是今天的交易日
                for d in reversed(dates):
                    if d < today:
                        return d
        except Exception:
            pass
    
    # 方法2：简单推算（跳过周末）
    check_dt = now - timedelta(days=1)
    while check_dt.weekday() >= 5:  # 5=周六, 6=周日
        check_dt -= timedelta(days=1)
    return check_dt.strftime('%Y%m%d')


def _calc_up50_fast_from_history(stock_df, index_name_list):
    """午盘up50快速估算（历史值 + 指数涨跌幅修正）
    
    从 CFC结构因子/historical_factors.json 读取最近收盘日up50，
    根据今日指数涨跌幅做一阶修正，耗时<2秒。
    
    修正公式：up50_today ≈ up50_yesterday + 涨跌幅 * beta
    beta≈1.5（up50对涨跌的敏感系数，经验值）
    
    Returns:
        dict: {index_name: {up50_value, matched_count, total_count, is_midday_est, note}}
        或 None（历史数据不可用）
    """
    import json as _json
    
    hist_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', '..', 'CFC结构因子', 'historical_factors.json'
    )
    # 也尝试绝对路径
    if not os.path.exists(hist_path):
        alt_paths = [
            (os.environ.get('CASE_WORKSPACE') or _cp('/Coze/Drive/蓁宝/所有对话/主对话')) + '/CFC结构因子/historical_factors.json',
            os.path.join(ROOT_DIR, 'CFC结构因子', 'historical_factors.json'),
        ]
        for p in alt_paths:
            if os.path.exists(p):
                hist_path = p
                break
        else:
            return None
    
    try:
        with open(hist_path, 'r', encoding='utf-8') as f:
            hist_data = _json.load(f)
    except Exception:
        return None
    
    if not hist_data:
        return None
    
    # CFC池名 → 指数名映射
    pool_map = {
        '沪深300': '沪深300',
        '中证1000': '中证1000',
        '创业板': '创业板指',
        '科创板': '科创50',
        '全市场前2000': '万得全A',
    }
    
    # 从stock_df计算各指数今日涨跌幅
    # 指数代码 → 成分股代码列表
    idx_code_map = {
        '沪深300': '000300',
        '中证1000': '000852',
        '创业板指': '399006',
        '科创50': '000688',
        '上证50': '000016',
        '中证500': '000905',
    }
    
    # 简化：用指数实时行情或成分股加权估算涨跌幅
    # 这里用全市场涨跌分布+池名映射做估算
    # 先获取每个池的最近up50
    latest_up50 = {}
    for pool_name, idx_name in pool_map.items():
        if pool_name not in hist_data:
            continue
        dates = sorted(hist_data[pool_name].keys())
        if not dates:
            continue
        latest_date = dates[-1]
        up50_val = hist_data[pool_name][latest_date].get('up50')
        if up50_val is not None:
            latest_up50[idx_name] = {
                'up50': up50_val,
                'top5z': hist_data[pool_name][latest_date].get('top5z'),
                'cdi': hist_data[pool_name][latest_date].get('cdi'),
                'date': latest_date,
            }
    
    if not latest_up50:
        return None
    
    # 估算全市场今日涨跌幅（用stock_df里的涨跌分布）
    up_count = 0
    down_count = 0
    total_pct = 0.0
    valid_count = 0
    
    for _, row in stock_df.iterrows():
        try:
            pct = float(row.get('涨跌幅', row.get('change_pct', 0)))
            if pct != 0 or valid_count < len(stock_df) * 0.5:
                valid_count += 1
                total_pct += pct
                if pct > 0:
                    up_count += 1
                elif pct < 0:
                    down_count += 1
        except (ValueError, TypeError):
            continue
    
    if valid_count == 0:
        return None
    
    avg_pct = total_pct / valid_count / 100  # 转成小数
    
    # up50对涨跌的敏感系数（经验值：up50变化 ≈ 指数涨跌幅 × 1.5）
    beta = 1.5
    
    result = {}
    for idx_name in index_name_list:
        base_info = latest_up50.get(idx_name)
        if base_info is None:
            # 没有直接对应：用全市场/沪深300近似
            for alias in ['万得全A', '沪深300']:
                if alias in latest_up50:
                    base_info = latest_up50[alias]
                    break
        if base_info is None:
            continue
        
        base_up50 = base_info['up50']
        # 修正：up50_today = up50_yesterday + avg_pct * beta
        est_up50 = base_up50 + avg_pct * beta
        # 限制在合理范围
        est_up50 = max(0.05, min(0.95, est_up50))
        
        result[idx_name] = {
            'up50_value': round(est_up50, 4),
            'up_count': int(est_up50 * 300),  # 粗略估算
            'matched_count': 300,
            'total_count': 300,
            'is_midday_est': True,
            'note': '快速估算（基于' + base_info['date'] + '历史值+今日涨跌修正）',
            'is_fast_est': True,
        }
    
    return result if result else None


def calc_up50_midday(stock_df, index_name_list=None, tushare_pro=None, data_hub_get_data=None):
    """午盘up50估算（股价在50日均线上方的股票占比）
    
    使用四级降级获取MA50数据，与实时价格对比计算
    
    Args:
        stock_df: fetch_stock_realtime() 返回的DataFrame
        index_name_list: 要计算的指数列表，默认所有配置指数
        tushare_pro: tushare pro接口对象
        data_hub_get_data: data_hub.get_data函数
    
    Returns:
        dict: {index_name: {up50_value, matched_count, total_count, is_midday_est, note}}
    """
    if stock_df is None or len(stock_df) == 0:
        return None
    
    if index_name_list is None:
        index_name_list = list(_CFC_INDEX_MAP.keys())
    
    # ===== Level 0 (午盘快速路径): 历史up50 + 指数涨跌幅估算 =====
    # 从 CFC结构因子/historical_factors.json 读取最近up50值，再根据今日涨跌幅估算
    # 耗时<2秒，避免拉取50天全市场日线的8+分钟等待
    fast_result = _calc_up50_fast_from_history(stock_df, index_name_list)
    if fast_result and len(fast_result) >= max(3, len(index_name_list) * 0.5):
        print(f"  ⚡ 快速路径命中，{len(fast_result)}/{len(index_name_list)}个指数up50已估算")
        return fast_result
    
    # 获取前一交易日（MA50截止日）
    prev_date = _get_prev_trade_date(tushare_pro)
    print(f"  📅 MA50截止日: {prev_date}")
    
    # 获取MA50映射（慢路径：拉取50天全市场日线）
    ma50_map = _fetch_ma50_map(prev_date, tushare_pro, data_hub_get_data)
    if not ma50_map:
        print("  ⚠️  无法获取MA50数据，up50计算失败")
        return None
    
    # 构建实时价格映射
    price_map = {}
    for _, row in stock_df.iterrows():
        code = str(row.get('代码', ''))
        if len(code) > 6:
            code = code[-6:]
        try:
            price = float(row.get('最新价', 0))
            if price > 0:
                price_map[code] = price
        except (ValueError, TypeError):
            continue
    
    print(f"  📈 实时价格样本: {len(price_map)}只，MA50样本: {len(ma50_map)}只")
    
    result = {}
    
    for idx_name in index_name_list:
        try:
            idx_code = _CFC_INDEX_MAP.get(idx_name)
            if not idx_code:
                continue
            
            weights = fetch_index_weights(idx_code)
            if not weights:
                continue
            
            # 统计有MA50且有实时价格的成分股
            up_count = 0
            matched_count = 0
            
            for stock_code in weights.keys():
                if stock_code in price_map and stock_code in ma50_map:
                    matched_count += 1
                    if price_map[stock_code] > ma50_map[stock_code]:
                        up_count += 1
            
            total_count = len(weights)
            
            # 覆盖率检查：至少60%的成分股有数据才输出
            if total_count == 0 or matched_count / total_count < 0.6:
                print(f"  ⚠️  {idx_name} up50覆盖率不足: {matched_count}/{total_count}")
                continue
            
            up50_value = round(up_count / matched_count, 4)
            
            result[idx_name] = {
                'up50_value': up50_value,
                'up_count': up_count,
                'matched_count': matched_count,
                'total_count': total_count,
                'is_midday_est': True,
                'note': f'午盘估算（{matched_count}/{total_count}只样本）',
            }
            
            print(f"  ✅ {idx_name} up50≈{up50_value*100:.1f}% ({up_count}/{matched_count}只)")
            
        except Exception as e:
            print(f"  ❌ {idx_name} up50估算失败: {e}")
            continue
    
    # ===== 万得全A特殊处理：直接用全市场数据 =====
    if index_name_list and '万得全A' in index_name_list:
        try:
            # 统计所有有MA50和实时价格的股票
            up_count = 0
            total_count = 0
            for code in price_map:
                if code in ma50_map:
                    total_count += 1
                    if price_map[code] > ma50_map[code]:
                        up_count += 1
            
            if total_count >= 500:
                up50_value = round(up_count / total_count, 4)
                result['万得全A'] = {
                    'up50_value': up50_value,
                    'up_count': up_count,
                    'matched_count': total_count,
                    'total_count': total_count,
                    'is_midday_est': True,
                    'note': f'午盘估算（全市场{total_count}只样本）',
                }
                print(f"  ✅ 万得全A up50≈{up50_value*100:.1f}% ({up_count}/{total_count}只)")
        except Exception as e:
            print(f"  ❌ 万得全A up50估算失败: {e}")
    
    return result if result else None


def calc_top5z_midday(stock_df, index_name_list=None):
    """午盘top5z估算（前5%股票成交额占总成交额比例）
    
    按成交额从高到低排序，取数量前5%的股票，计算其成交额占总成交额比例
    
    Args:
        stock_df: fetch_stock_realtime() 返回的DataFrame
        index_name_list: 要计算的指数列表，默认所有配置指数
    
    Returns:
        dict: {index_name: {top5z_value, matched_count, total_count, is_midday_est, note}}
    """
    if stock_df is None or len(stock_df) == 0:
        return None
    
    if index_name_list is None:
        index_name_list = list(_CFC_INDEX_MAP.keys())
    
    # 构建实时成交额映射
    amount_map = {}
    for _, row in stock_df.iterrows():
        code = str(row.get('代码', ''))
        if len(code) > 6:
            code = code[-6:]
        try:
            amount = float(row.get('成交额', 0))
            if amount > 0:
                amount_map[code] = amount
        except (ValueError, TypeError):
            continue
    
    result = {}
    
    for idx_name in index_name_list:
        try:
            idx_code = _CFC_INDEX_MAP.get(idx_name)
            if not idx_code:
                continue
            
            weights = fetch_index_weights(idx_code)
            if not weights:
                continue
            
            # 匹配有成交额数据的成分股
            matched_amounts = []
            for stock_code in weights.keys():
                if stock_code in amount_map:
                    matched_amounts.append((stock_code, amount_map[stock_code]))
            
            total_count = len(weights)
            matched_count = len(matched_amounts)
            
            # 覆盖率检查
            if total_count == 0 or matched_count / total_count < 0.6:
                print(f"  ⚠️  {idx_name} top5z覆盖率不足: {matched_count}/{total_count}")
                continue
            
            # 按成交额排序
            matched_amounts.sort(key=lambda x: -x[1])
            
            # 取前5%数量的股票（按成分股总数的5%，不是匹配数的5%）
            n_top = max(1, int(np.ceil(total_count * 0.05)))
            n_top = min(n_top, matched_count)
            
            total_amount = sum(a for _, a in matched_amounts)
            top5_amount = sum(a for _, a in matched_amounts[:n_top])
            
            if total_amount <= 0:
                continue
            
            top5z_value = round(top5_amount / total_amount * 100, 2)
            
            result[idx_name] = {
                'top5z_value': top5z_value,
                'n_top': n_top,
                'matched_count': matched_count,
                'total_count': total_count,
                'total_amount_yi': round(total_amount / 1e8, 2),
                'is_midday_est': True,
                'note': f'午盘估算（{matched_count}/{total_count}只样本，前{n_top}只）',
            }
            
            print(f"  ✅ {idx_name} top5z≈{top5z_value:.1f}% (前{n_top}只/{matched_count}只)")
            
        except Exception as e:
            print(f"  ❌ {idx_name} top5z估算失败: {e}")
            continue
    
    # ===== 万得全A特殊处理：直接用全市场数据 =====
    if index_name_list and '万得全A' in index_name_list:
        try:
            all_amounts = sorted(amount_map.values(), reverse=True)
            if len(all_amounts) >= 100:
                total_amount = sum(all_amounts)
                n_top = max(1, int(np.ceil(len(all_amounts) * 0.05)))
                top5_amount = sum(all_amounts[:n_top])
                top5z_value = round(top5_amount / total_amount * 100, 2) if total_amount > 0 else 0
                
                result['万得全A'] = {
                    'top5z_value': top5z_value,
                    'n_top': n_top,
                    'matched_count': len(all_amounts),
                    'total_count': len(all_amounts),
                    'total_amount_yi': round(total_amount / 1e8, 2),
                    'is_midday_est': True,
                    'note': f'午盘估算（全市场{len(all_amounts)}只，前{n_top}只）',
                }
                print(f"  ✅ 万得全A top5z≈{top5z_value:.1f}% (前{n_top}只/{len(all_amounts)}只)")
        except Exception as e:
            print(f"  ❌ 万得全A top5z估算失败: {e}")
    
    return result if result else None


def calc_cdi_midday(stock_df, index_name_list=None):
    """午盘CDI估算（聚类离散度指数）
    
    用成分股实时涨跌幅做KMeans聚类，计算聚类间离散度作为CDI近似
    简化版：KMeans(n_clusters≈30) + 聚类中心分布的峰度
    
    Args:
        stock_df: fetch_stock_realtime() 返回的DataFrame
        index_name_list: 要计算的指数列表，默认所有配置指数
    
    Returns:
        dict: {index_name: {cdi_value, matched_count, total_count, is_midday_est, note}}
    """
    if stock_df is None or len(stock_df) == 0:
        return None
    
    if index_name_list is None:
        index_name_list = list(_CFC_INDEX_MAP.keys())
    
    # 延迟导入
    try:
        from sklearn.cluster import KMeans
        from scipy import stats as scipy_stats
        _kmeans_ready = True
    except ImportError:
        _kmeans_ready = False
        print("  ⚠️  sklearn/scipy不可用，CDI使用简化算法")
    
    # 构建实时涨跌幅映射
    change_map = {}
    for _, row in stock_df.iterrows():
        code = str(row.get('代码', ''))
        if len(code) > 6:
            code = code[-6:]
        try:
            change = float(row.get('涨跌幅', 0))
            change_map[code] = change
        except (ValueError, TypeError):
            continue
    
    result = {}
    
    for idx_name in index_name_list:
        try:
            idx_code = _CFC_INDEX_MAP.get(idx_name)
            if not idx_code:
                continue
            
            weights = fetch_index_weights(idx_code)
            if not weights:
                continue
            
            # 匹配有涨跌幅数据的成分股
            matched_changes = []
            for stock_code in weights.keys():
                if stock_code in change_map:
                    matched_changes.append(change_map[stock_code])
            
            total_count = len(weights)
            matched_count = len(matched_changes)
            
            # 覆盖率和样本量检查
            if total_count == 0 or matched_count / total_count < 0.6:
                print(f"  ⚠️  {idx_name} CDI覆盖率不足: {matched_count}/{total_count}")
                continue
            
            if matched_count < 30:
                print(f"  ⚠️  {idx_name} CDI样本不足: {matched_count}只")
                continue
            
            changes_arr = np.array(matched_changes)
            
            if _kmeans_ready:
                # KMeans聚类方案
                n_clusters = min(30, max(5, matched_count // 5))
                X = changes_arr.reshape(-1, 1)
                
                kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
                kmeans.fit(X)
                
                # 获取每个聚类的中心和样本数
                centers = kmeans.cluster_centers_.flatten()
                labels = kmeans.labels_
                
                # 计算每个聚类的样本占比
                cluster_sizes = np.bincount(labels, minlength=n_clusters)
                cluster_ratios = cluster_sizes / len(labels)
                
                # CDI = 聚类中心分布的峰度
                # 峰度高 = 聚类集中在少数中心 = 结构一致性强（CDI小）
                # 峰度低 = 聚类分散 = 结构分化大（CDI大）
                cdi_raw = scipy_stats.kurtosis(centers, fisher=True)
                
                # 归一化：将峰度映射到与原CDI可比的范围
                # 原CDI：>3健康，<=3恶化
                # 峰度范围：通常-2到+5之间
                # 映射：峰度高(集中) → CDI高(健康)，峰度低(分散) → CDI低(恶化)
                cdi_value = round(cdi_raw + 2.5, 2)  # 平移到与原CDI相近的范围
                
                note = f'午盘估算（KMeans k={n_clusters}, {matched_count}只样本）'
            else:
                # 降级方案：用涨跌幅分布的离散度近似
                # (p90 - p10) / std 表示分布的尾部厚度
                p10 = float(np.percentile(changes_arr, 10))
                p90 = float(np.percentile(changes_arr, 90))
                std = float(np.std(changes_arr))
                
                if std > 0:
                    dispersion = (p90 - p10) / std
                    # 映射到CDI范围：离散度大→CDI小(分化)，离散度小→CDI大(一致)
                    cdi_value = round(5.0 - dispersion * 0.5, 2)
                else:
                    cdi_value = 0.0
                
                note = f'午盘估算（分布离散度近似, {matched_count}只样本）'
            
            result[idx_name] = {
                'cdi_value': cdi_value,
                'matched_count': matched_count,
                'total_count': total_count,
                'is_midday_est': True,
                'note': note,
            }
            
            print(f"  ✅ {idx_name} CDI≈{cdi_value:.2f} ({matched_count}只样本)")
            
        except Exception as e:
            print(f"  ❌ {idx_name} CDI估算失败: {e}")
            continue
    
    # ===== 万得全A特殊处理：直接用全市场数据 =====
    if index_name_list and '万得全A' in index_name_list:
        try:
            all_changes = list(change_map.values())
            if len(all_changes) >= 200:
                changes_arr = np.array(all_changes)
                
                if _kmeans_ready:
                    n_clusters = min(30, max(10, len(all_changes) // 50))
                    X = changes_arr.reshape(-1, 1)
                    from sklearn.cluster import KMeans as KMeans2
                    kmeans = KMeans2(n_clusters=n_clusters, random_state=42, n_init=10)
                    kmeans.fit(X)
                    centers = kmeans.cluster_centers_.flatten()
                    from scipy import stats as scipy_stats2
                    cdi_raw = scipy_stats2.kurtosis(centers, fisher=True)
                    cdi_value = round(cdi_raw + 2.5, 2)
                    note = f'午盘估算（全市场KMeans k={n_clusters}, {len(all_changes)}只）'
                else:
                    p10 = float(np.percentile(changes_arr, 10))
                    p90 = float(np.percentile(changes_arr, 90))
                    std = float(np.std(changes_arr))
                    if std > 0:
                        dispersion = (p90 - p10) / std
                        cdi_value = round(5.0 - dispersion * 0.5, 2)
                    else:
                        cdi_value = 0.0
                    note = f'午盘估算（全市场分布离散度, {len(all_changes)}只）'
                
                result['万得全A'] = {
                    'cdi_value': cdi_value,
                    'matched_count': len(all_changes),
                    'total_count': len(all_changes),
                    'is_midday_est': True,
                    'note': note,
                }
                print(f"  ✅ 万得全A CDI≈{cdi_value:.2f} ({len(all_changes)}只样本)")
        except Exception as e:
            print(f"  ❌ 万得全A CDI估算失败: {e}")
    
    return result if result else None
