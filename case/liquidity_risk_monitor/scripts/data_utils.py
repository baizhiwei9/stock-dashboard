# -*- coding: utf-8 -*-
"""
公共数据获取模块 - 替代失效的akshare EM接口

数据源:
- 腾讯行情 qt.gtimg.cn: A股实时+港股实时
- 新浪行情分页: A股全量列表
- tushare: A股日线、期货日线、期权日线
- baostock: A股日线/周线/分钟线

Author: Data Utils Module
"""

import os
import sys
import socket
import time
import json
import requests
import pandas as pd
import baostock as bs
import tushare as ts
from typing import Optional, List

# 设置默认超时
socket.setdefaulttimeout(15)

# Tushare Token
def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import tushare_token as _tushare_token, smtp_password as _smtp_password  # noqa: E402

TS_TOKEN = _tushare_token()

# 初始化tushare
try:
    ts.set_token(TS_TOKEN)
    pro = ts.pro_api()
except Exception as e:
    print(f"[WARN] tushare初始化失败: {e}")
    pro = None

# ==================== 腾讯行情接口 ====================

def get_tencent_quote_batch(codes: List[str], market: str = 'cn') -> pd.DataFrame:
    """
    腾讯行情批量获取（统一接口，支持A股和港股）
    
    Args:
        codes: 代码列表，如['sh600519','sz000001']或['r_hk00700','r_hk03690']
        market: 市场标识，'cn'=A股，'hk'=港股
    
    Returns:
        DataFrame包含: code, name, price, change_pct, volume, amount等
    """
    if not codes:
        return pd.DataFrame()
    
    # 腾讯行情批量一次不超过50只
    batch_size = 50
    all_dfs = []
    
    for i in range(0, len(codes), batch_size):
        batch_codes = codes[i:i+batch_size]
        
        # 构建代码字符串，用逗号分隔
        code_str = ','.join(batch_codes)
        
        url = f"http://qt.gtimg.cn/q={code_str}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.qq.com'
        }
        
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = 'gbk'
            
            lines = resp.text.strip().split('\n')
            
            for line in lines:
                if not line.strip():
                    continue
                
                parts = line.split('~')
                if len(parts) < 10:
                    continue
                
                try:
                    # 解析字段
                    full_code = parts[0].replace('v_', '').replace('r_', '')
                    
                    df_row = {
                        'code': full_code,
                        'name': parts[1] if len(parts) > 1 else '',
                        'price': float(parts[3]) if parts[3].replace('.', '').replace('-', '').isdigit() else None,
                        'prev_close': float(parts[4]) if len(parts) > 4 and parts[4].replace('.', '').replace('-', '').isdigit() else None,
                        'open': float(parts[5]) if len(parts) > 5 and parts[5].replace('.', '').replace('-', '').isdigit() else None,
                        'volume': int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0,  # 成交量(手)
                        'bid_price': float(parts[9]) if len(parts) > 9 and parts[9].replace('.', '').replace('-', '').isdigit() else None,
                        'ask_price': float(parts[19]) if len(parts) > 19 and parts[19].replace('.', '').replace('-', '').isdigit() else None,
                        'date': parts[30] if len(parts) > 30 else '',
                        'time': parts[31] if len(parts) > 31 else '',
                        'change_pct': float(parts[32]) if len(parts) > 32 and parts[32].replace('.', '').replace('-', '').isdigit() else None,
                        'amount': float(parts[37]) if len(parts) > 37 and parts[37].replace('.', '').replace('-', '').isdigit() else None,  # 成交额(万)
                        'turnover': float(parts[38]) if len(parts) > 38 and parts[38].replace('.', '').replace('-', '').isdigit() else None,  # 换手率%
                        'pe': float(parts[39]) if len(parts) > 39 and parts[39].replace('.', '').replace('-', '').isdigit() else None,
                        'high': float(parts[33]) if len(parts) > 33 and parts[33].replace('.', '').replace('-', '').isdigit() else None,
                        'low': float(parts[34]) if len(parts) > 34 and parts[34].replace('.', '').replace('-', '').isdigit() else None,
                    }
                    all_dfs.append(df_row)
                except (IndexError, ValueError) as e:
                    continue
            
            time.sleep(0.1)  # 请求间隔
            
        except Exception as e:
            print(f"[ERROR] 腾讯行情获取失败: {e}")
            continue
    
    if all_dfs:
        return pd.DataFrame(all_dfs)
    return pd.DataFrame()


def get_a_quote_realtime(codes: List[str]) -> pd.DataFrame:
    """
    批量获取A股实时行情
    
    Args:
        codes: A股代码列表，如['sh600519','sz000001']
    
    Returns:
        DataFrame包含: code, name, price, change_pct, volume, amount等
    """
    if not codes:
        return pd.DataFrame()
    
    # 确保格式正确
    normalized_codes = []
    for code in codes:
        code = code.lower()
        if not code.startswith(('sh', 'sz', 'bj')):
            # 尝试自动补全
            if code.startswith('6'):
                code = 'sh' + code
            elif code.startswith(('0', '3')):
                code = 'sz' + code
            elif code.startswith('8', '4', '9'):
                code = 'bj' + code
        normalized_codes.append(code)
    
    return get_tencent_quote_batch(normalized_codes, market='cn')


def get_hk_quote_realtime(codes: List[str]) -> pd.DataFrame:
    """
    批量获取港股实时行情
    
    Args:
        codes: 港股代码列表，如['hk00700','hk03690']，会自动添加r_前缀
    
    Returns:
        DataFrame包含: code, name, price, change_pct, volume, amount等
    """
    if not codes:
        return pd.DataFrame()
    
    # 确保格式正确，添加r_hk前缀
    normalized_codes = []
    for code in codes:
        code = code.lower()
        if not code.startswith('r_hk'):
            if code.startswith('hk'):
                code = 'r_' + code
            else:
                code = 'r_hk' + code
        normalized_codes.append(code)
    
    df = get_tencent_quote_batch(normalized_codes, market='hk')
    
    # 清理code字段，去掉r_前缀
    if not df.empty and 'code' in df.columns:
        df['code'] = df['code'].str.replace('r_hk', 'hk', regex=False)
    
    return df


# ==================== 新浪行情接口 ====================

def get_a_all_quotes(page_size: int = 100, max_pages: int = 60) -> pd.DataFrame:
    """
    获取全部A股实时行情（新浪分页接口）
    
    Args:
        page_size: 每页数量，默认100
        max_pages: 最大页数，默认60（约5500只股票）
    
    Returns:
        DataFrame包含A股全量实时行情
    """
    all_data = []
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn'
    }
    
    for page in range(1, max_pages + 1):
        url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
        params = {
            'page': page,
            'num': page_size,
            'sort': 'symbol',
            'asc': 1,
            'node': 'hs_a',
            'symbol': '',
            '_s_r_a': 'page'
        }
        
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            
            # 解析JSONP格式
            text = resp.text
            if text.startswith('var') or text.startswith('('):
                text = text[text.index('(') + 1:text.rindex(')')]
            
            data = json.loads(text)
            
            if not data:
                break
            
            for item in data:
                all_data.append({
                    'symbol': item.get('symbol', ''),
                    'code': item.get('symbol', '').replace('sh', '').replace('sz', ''),
                    'name': item.get('name', ''),
                    'price': float(item.get('trade', 0)),
                    'prev_close': float(item.get('settlement', 0)),
                    'open': float(item.get('open', 0)),
                    'volume': int(item.get('volume', 0)),
                    'amount': float(item.get('amount', 0)),
                    'high': float(item.get('high', 0)),
                    'low': float(item.get('low', 0)),
                    'change_pct': float(item.get('pricechange', 0)),
                    'change_pct_rate': float(item.get('changepercent', 0)),
                    'turnover': float(item.get('turnoverratio', 0)),
                    'pe': float(item.get('per', 0)) if item.get('per', '0').replace('.', '').replace('-', '').isdigit() else None,
                    'pb': float(item.get('mktcap', 0)) if item.get('mktcap', '0').replace('.', '').replace('-', '').isdigit() else None,
                })
            
            time.sleep(0.15)  # 请求间隔
            
        except Exception as e:
            print(f"[ERROR] 新浪行情第{page}页获取失败: {e}")
            continue
    
    if all_data:
        return pd.DataFrame(all_data)
    return pd.DataFrame()


# ==================== Baostock接口 ====================

def get_a_kline(code: str, start_date: str, end_date: str, freq: str = 'd') -> pd.DataFrame:
    """
    baostock获取K线数据
    
    Args:
        code: 股票代码，格式如'sh.600519'或'600519'
        start_date: 开始日期，格式'YYYY-MM-DD'
        end_date: 结束日期，格式'YYYY-MM-DD'
        freq: 频率，'d'=日线，'w'=周线，'m'=月线，'5'=5分钟，'15'=15分钟，'30'=30分钟，'60'=60分钟
    
    Returns:
        DataFrame包含K线数据
    """
    # 标准化code格式
    if not code.startswith(('sh.', 'sz.', 'bj.')):
        code_lower = code.lower()
        if code_lower.startswith('6') or code_lower.startswith('5'):
            code = 'sh.' + code
        elif code_lower.startswith(('0', '3')):
            code = 'sz.' + code
        elif code_lower.startswith(('8', '4', '9')):
            code = 'bj.' + code
    
    try:
        # 登录baostock
        lg = bs.login()
        if lg.error_code != '0':
            print(f"[ERROR] baostock登录失败: {lg.error_msg}")
            return pd.DataFrame()
        
        # 确定频率参数
        frequency_map = {
            'd': 'd', 'day': 'd',
            'w': 'w', 'week': 'w',
            'm': 'm', 'month': 'm',
            '5': '5', '5min': '5',
            '15': '15', '15min': '15',
            '30': '30', '30min': '30',
            '60': '60', '60min': '60'
        }
        frequency = frequency_map.get(freq.lower(), 'd')
        
        # 获取数据
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount,turn",
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag='3'  # 不复权
        )
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        # 登出
        bs.logout()
        
        if data_list:
            df = pd.DataFrame(data_list, columns=rs.fields)
            # 转换数据类型
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            return df
        return pd.DataFrame()
        
    except Exception as e:
        print(f"[ERROR] baostock获取K线失败 ({code}): {e}")
        try:
            bs.logout()
        except:
            pass
        return pd.DataFrame()


# ==================== Tushare接口 ====================

def _check_tushare() -> bool:
    """检查tushare是否可用"""
    global pro
    if pro is None:
        try:
            ts.set_token(TS_TOKEN)
            pro = ts.pro_api()
        except Exception as e:
            print(f"[ERROR] tushare初始化失败: {e}")
            return False
    return True


def get_a_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    tushare获取A股日线数据
    
    Args:
        ts_code: 股票代码，格式如'000001.SZ'或'600519.SH'
        start_date: 开始日期，格式'YYYYMMDD'
        end_date: 结束日期，格式'YYYYMMDD'
    
    Returns:
        DataFrame包含日线数据
    """
    if not _check_tushare():
        return pd.DataFrame()
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df.sort_values('trade_date')
            # 转换日期格式
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"[ERROR] tushare获取A股日线失败 ({ts_code}): {e}")
        return pd.DataFrame()


def get_fut_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    tushare获取期货日线数据
    
    Args:
        ts_code: 期货代码，注意交易所后缀：
                 SHF=上海期货(非SHFE), ZCE=郑商所(非CZCE), DCE=大商所, INE=上期能源, GFEX=广期所
                 例如: 'SC2607.INE', 'MA2609.ZCE', 'CU2607.SHF', 'J2609.DCE'
        start_date: 开始日期，格式'YYYYMMDD'
        end_date: 结束日期，格式'YYYYMMDD'
    
    Returns:
        DataFrame包含期货日线数据
    """
    if not _check_tushare():
        return pd.DataFrame()
    
    try:
        # 期货日线接口
        df = pro.fut_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and not df.empty:
            df = df.sort_values('trade_date')
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"[ERROR] tushare获取期货日线失败 ({ts_code}): {e}")
        return pd.DataFrame()


def get_opt_basic(exchange: str = 'SSE') -> pd.DataFrame:
    """
    tushare获取期权合约列表
    
    Args:
        exchange: 交易所，'SSE'=上交所, 'SZSE'=深交所
    
    Returns:
        DataFrame包含期权合约列表
    """
    if not _check_tushare():
        return pd.DataFrame()
    
    try:
        df = pro.opt_basic(exchange=exchange)
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"[ERROR] tushare获取期权合约列表失败: {e}")
        return pd.DataFrame()


def get_opt_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    tushare获取期权日线数据
    
    Args:
        ts_code: 期权代码，格式如'SH-510300-2506C3.0'
        start_date: 开始日期，格式'YYYYMMDD'
        end_date: 结束日期，格式'YYYYMMDD'
    
    Returns:
        DataFrame包含期权日线数据
    """
    if not _check_tushare():
        return pd.DataFrame()
    
    try:
        df = pro.opt_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df.sort_values('trade_date')
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"[ERROR] tushare获取期权日线失败 ({ts_code}): {e}")
        return pd.DataFrame()


def get_fut_basic(exchange: str = None) -> pd.DataFrame:
    """
    tushare获取期货主力合约列表
    
    Args:
        exchange: 交易所代码，可选'SHF','ZCE','DCE','INE','GFEX'，None表示全部
    
    Returns:
        DataFrame包含期货合约列表
    """
    if not _check_tushare():
        return pd.DataFrame()
    
    try:
        if exchange:
            df = pro.fut_basic(exchange=exchange, fut_type='1')  # 主力合约
        else:
            df = pro.fut_basic(fut_type='1')
        
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"[ERROR] tushare获取期货列表失败: {e}")
        return pd.DataFrame()


# ==================== 工具函数 ====================

def format_code(code: str, market: str = 'a') -> str:
    """
    标准化股票代码格式
    
    Args:
        code: 原始代码
        market: 市场类型，'a'=A股，'hk'=港股
    
    Returns:
        标准化后的代码
    """
    code = code.upper().strip()
    
    if market == 'hk':
        if not code.startswith('HK'):
            code = 'HK' + code
        return code
    
    # A股
    if code.startswith(('SH', 'SZ', 'BJ')):
        return code
    
    if code.startswith('6') or code.startswith('5'):
        return 'SH' + code
    elif code.startswith(('0', '3')):
        return 'SZ' + code
    elif code.startswith(('8', '4', '9')):
        return 'BJ' + code
    
    return code


def ts_code_to_tencent(code: str) -> str:
    """
    tushare格式转腾讯格式
    
    Args:
        code: tushare格式，如'600519.SH'或'000001.SZ'
    
    Returns:
        腾讯格式，如'sh600519'或'sz000001'
    """
    if code.endswith('.SH'):
        return 'sh' + code.replace('.SH', '')
    elif code.endswith('.SZ'):
        return 'sz' + code.replace('.SZ', '')
    elif code.endswith('.BJ'):
        return 'bj' + code.replace('.BJ', '')
    return code.lower()


def tencent_to_ts_code(code: str) -> str:
    """
    腾讯格式转tushare格式
    
    Args:
        code: 腾讯格式，如'sh600519'或'sz000001'
    
    Returns:
        tushare格式，如'600519.SH'或'000001.SZ'
    """
    code = code.lower()
    if code.startswith('sh'):
        return code.replace('sh', '') + '.SH'
    elif code.startswith('sz'):
        return code.replace('sz', '') + '.SZ'
    elif code.startswith('bj'):
        return code.replace('bj', '') + '.BJ'
    return code.upper()


# ==================== 主程序测试 ====================

if __name__ == '__main__':
    print("=== 测试数据获取模块 ===\n")
    
    # 测试1: 腾讯A股行情
    print("1. 测试腾讯A股行情...")
    test_codes = ['sh600519', 'sz000001', 'sz300750']
    df = get_a_quote_realtime(test_codes)
    print(f"   获取到 {len(df)} 条数据")
    if not df.empty:
        print(df[['code', 'name', 'price', 'change_pct']].head())
    print()
    
    # 测试2: 港股行情
    print("2. 测试港股行情...")
    hk_codes = ['hk00700', 'hk03690']
    df_hk = get_hk_quote_realtime(hk_codes)
    print(f"   获取到 {len(df_hk)} 条数据")
    if not df_hk.empty:
        print(df_hk[['code', 'name', 'price', 'change_pct']].head())
    print()
    
    # 测试3: Baostock日线
    print("3. 测试Baostock日线...")
    df_kline = get_a_kline('sh.600519', '2024-01-01', '2024-01-10')
    print(f"   获取到 {len(df_kline)} 条数据")
    if not df_kline.empty:
        print(df_kline.head())
    print()
    
    # 测试4: tushare期权列表
    print("4. 测试tushare期权列表...")
    df_opt = get_opt_basic('SSE')
    print(f"   获取到 {len(df_opt)} 条数据")
    if not df_opt.empty:
        print(df_opt.head())
    print()
    
    print("=== 测试完成 ===")
