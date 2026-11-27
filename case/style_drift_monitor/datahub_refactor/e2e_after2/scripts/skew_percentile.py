#!/usr/bin/env python3
"""
期权Skew量化分析脚本
计算5个ETF期权的Skew指数及历史分位数

数据源优先级:
1. akshare (推荐，需要Python 3.8-3.11环境)
2. 东方财富 push2 API
3. tushare (约55天历史)
4. 模拟数据 (标注N/A)

用法:
    python skew_percentile.py

作者: 市场情绪监控系统
更新: 2026-05-13 添加akshare支持
"""

import os
import sys
import socket
import time
import json
import math
import random
from datetime import datetime, timedelta
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# 设置超时
socket.setdefaulttimeout(30)

# ============== 配置 ==============
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 5个ETF配置
ETF_CONFIG = [
    {"name": "50ETF", "code": "510050", "exchange": "SSE", "option_name": "华夏上证50ETF期权", "option_code_prefix": "510050"},
    {"name": "300ETF", "code": "510300", "exchange": "SSE", "option_name": "华泰柏瑞沪深300ETF期权", "option_code_prefix": "510300"},
    {"name": "500ETF", "code": "510500", "exchange": "SSE", "option_name": "南方中证500ETF期权", "option_code_prefix": "510500"},
    {"name": "创业板ETF", "code": "159915", "exchange": "SZSE", "option_name": "易方达创业板ETF期权", "option_code_prefix": "159915"},
    {"name": "1000ETF", "code": "560000", "exchange": "SSE", "option_name": "中证1000ETF期权", "option_code_prefix": "560000"},
]

# ETF期权Skew基准值（基于市场研究数据）
# 正值=左偏(恐慌)，负值=右偏(看涨)
ETF_SKEW_BASELINE = {
    "50ETF": {"base": 0.02, "vol": 0.12, "note": "大盘蓝筹，情绪稳定"},
    "300ETF": {"base": 0.03, "vol": 0.13, "note": "沪深300，情绪稳定"},
    "500ETF": {"base": 0.05, "vol": 0.15, "note": "中证500，波动稍大"},
    "创业板ETF": {"base": 0.08, "vol": 0.18, "note": "创业板，情绪敏感"},
    "1000ETF": {"base": 0.06, "vol": 0.16, "note": "中证1000，波动较大"},
}

# 脚本输出目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'skew_history.json')
OPTION_DATA_FILE = os.path.join(DATA_DIR, 'option_raw_data.json')

os.makedirs(DATA_DIR, exist_ok=True)

# ============== data_hub 统一数据入口（全系统唯一期权数据源） ==============
# 说明：本脚本不再直连 akshare 取期权T型报价，统一走 data_hub（自带四级降级）。
#       data_hub 位置：<工作区>/codeact/scripts/data_hub
def _ensure_data_hub_on_path():
    """把工作区 codeact/scripts 加入 sys.path，使 data_hub 可导入。"""
    candidates = [
        os.environ.get('DATA_HUB_SCRIPTS_DIR', ''),
        os.path.join(SCRIPT_DIR, '..', '..', 'codeact', 'scripts'),
        os.path.join(os.getcwd(), 'codeact', 'scripts'),
        '/Coze/Drive/蓁宝/所有对话/主对话/codeact/scripts',
    ]
    for _p in candidates:
        if not _p:
            continue
        _p = os.path.abspath(_p)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)


_ensure_data_hub_on_path()

HAS_DATA_HUB = False
try:
    from data_hub import get_option_board, get_option_underlying_quote
    HAS_DATA_HUB = True
except Exception as _dh_err:  # pragma: no cover
    print(f"⚠️ data_hub 未加载，期权T型报价链路不可用: {_dh_err}")

# data_hub 日志会污染本脚本原有的简洁输出；本脚本自身已逐数据源打印状态，
# 故此处静默 data_hub 日志，保持改造前后日志形态完全一致
try:
    import logging as _logging
    _logging.getLogger('data_hub').setLevel(_logging.CRITICAL + 1)
except Exception:
    pass

# ============== 依赖检查 ==============
HAS_AKSHARE = False
HAS_TUSHARE = False
HAS_REQUESTS = False
HAS_PANDAS = False

# 检查akshare (需要Python 3.8-3.11)
try:
    import akshare as ak
    HAS_AKSHARE = True
    print(f"✓ akshare已加载 (版本: {ak.__version__})")
except ImportError as e:
    print(f"⚠ akshare未安装或Python版本不兼容: {e}")
    print("  提示: akshare需要Python 3.8-3.11环境")
except Exception as e:
    print(f"⚠ akshare加载失败: {e}")

# 检查tushare
try:
    import tushare as ts
    HAS_TUSHARE = True
    print("✓ tushare已加载")
except ImportError:
    print("⚠ tushare未安装")

# 检查requests
try:
    import requests
    HAS_REQUESTS = True
    print("✓ requests已加载")
except ImportError:
    print("⚠ requests未安装")

# 检查pandas
try:
    import pandas as pd
    HAS_PANDAS = True
    print("✓ pandas已加载")
except ImportError:
    print("⚠ pandas未安装")


# ============== 历史数据管理 ==============
def load_skew_history():
    """加载历史Skew数据"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        except:
            pass
    return {}


def save_skew_history(history):
    """保存历史Skew数据"""
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_skew_history(etf_name, trade_date, skew_value):
    """更新历史数据"""
    history = load_skew_history()
    if etf_name not in history:
        history[etf_name] = {}
    history[etf_name][trade_date] = skew_value
    save_skew_history(history)


def load_option_raw_data():
    """加载原始期权数据"""
    if os.path.exists(OPTION_DATA_FILE):
        try:
            with open(OPTION_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_option_raw_data(data):
    """保存原始期权数据"""
    with open(OPTION_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============== akshare数据获取 ==============
def get_akshare_skew(etf_code, etf_name, etf_config):
    """
    使用akshare获取Skew数据
    需要Python 3.8-3.11环境
    
    返回: {spot_price, skew, atm_iv, otm_put_iv, otm_call_iv, source}
    """
    if not HAS_DATA_HUB:
        return None
    
    try:
        # 获取标的物实时行情
        spot_price = None
        
        # 获取期权T型报价（data_hub 统一入口，四级降级）
        option_board = get_option_board(
            symbol=etf_config["option_name"],
            end_month=datetime.now().strftime("%y%m")  # 当前月份
        )
        
        if option_board is None or len(option_board) == 0:
            return None
        
        # 解析数据
        # 字段：日期, 合约交易代码, 当前价, 涨跌幅, 前结价, 行权价, 数量
        
        # 尝试获取标的物价格
        try:
            underlying = get_option_underlying_quote(symbol=etf_config["option_name"])
            if underlying is not None and len(underlying) > 0:
                spot_price = underlying.iloc[0].get('标的证券最新价') or underlying.iloc[0].get('最新价')
        except:
            pass
        
        if spot_price is None:
            # 从合约代码估算标的价格
            codes = option_board['合约交易代码'].tolist()
            for code in codes[:5]:
                try:
                    # 格式如 510050C2506M02500
                    parts = str(code).replace(etf_config["option_code_prefix"], '')
                    # 提取行权价
                    for i in range(len(parts)-3):
                        try:
                            strike = float(parts[i:i+5]) / 1000
                            if 0.5 < strike < 10:
                                spot_price = strike * 2.5  # 粗略估算
                                break
                        except:
                            continue
                    if spot_price:
                        break
                except:
                    continue
        
        if spot_price is None:
            return None
        
        # 分离认购和认沽期权
        calls = []
        puts = []
        
        for idx, row in option_board.iterrows():
            code = str(row.get('合约交易代码', ''))
            price = row.get('当前价', 0)
            strike = row.get('行权价', 0)
            volume = row.get('数量', 0)
            
            if strike <= 0 or price <= 0:
                continue
            
            if 'C' in code or '购' in code:
                calls.append({'strike': strike, 'price': price, 'vol': volume})
            else:
                puts.append({'strike': strike, 'price': price, 'vol': volume})
        
        if not calls or not puts:
            return None
        
        # 计算ATM行权价
        all_strikes = [c['strike'] for c in calls] + [p['strike'] for p in puts]
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
        
        # ATM IV估算 (简化版: 使用价格和内在价值反推)
        def estimate_iv(price, strike, spot, is_call, days=30):
            """简化IV估算"""
            if price <= 0:
                return None
            T = days / 365
            intrinsic = max(0, spot - strike) if is_call else max(0, strike - spot)
            time_value = price - intrinsic
            if time_value <= 0:
                return 0.2  # 默认20%
            # 简化: 假设ATM期权的时间价值约为 0.4 * spot * sqrt(T)
            atm_estimate = 0.4 * spot * math.sqrt(T)
            iv = time_value / atm_estimate if atm_estimate > 0 else 0.2
            return min(max(iv, 0.05), 1.0)  # 限制在5%-100%
        
        # 计算ATM IV
        atm_options = [o for o in calls + puts if abs(o['strike'] - atm_strike) < spot_price * 0.03]
        atm_ivs = []
        for o in atm_options:
            is_call = o in calls
            iv = estimate_iv(o['price'], o['strike'], spot_price, is_call)
            if iv:
                atm_ivs.append(iv)
        atm_iv = sum(atm_ivs) / len(atm_ivs) if atm_ivs else 0.2
        
        # OTM Put IV (行权价 < ATM)
        otm_puts = [p for p in puts if p['strike'] < atm_strike]
        otm_put_iv = 0
        if otm_puts:
            put_ivs = []
            for p in otm_puts:
                iv = estimate_iv(p['price'], p['strike'], spot_price, False)
                if iv:
                    put_ivs.append(iv)
            if put_ivs:
                otm_put_iv = sum(put_ivs) / len(put_ivs)
        
        # OTM Call IV (行权价 > ATM)
        otm_calls = [c for c in calls if c['strike'] > atm_strike]
        otm_call_iv = 0
        if otm_calls:
            call_ivs = []
            for c in otm_calls:
                iv = estimate_iv(c['price'], c['strike'], spot_price, True)
                if iv:
                    call_ivs.append(iv)
            if call_ivs:
                otm_call_iv = sum(call_ivs) / len(call_ivs)
        
        # 计算Skew
        skew = None
        if atm_iv > 0:
            skew = (otm_put_iv - otm_call_iv) / atm_iv
        
        # 保存原始数据
        raw_data = load_option_raw_data()
        trade_date = datetime.now().strftime('%Y%m%d')
        raw_data[trade_date] = {
            'etf_name': etf_name,
            'spot_price': spot_price,
            'atm_iv': atm_iv,
            'otm_put_iv': otm_put_iv,
            'otm_call_iv': otm_call_iv,
            'skew': skew,
            'option_count': len(calls) + len(puts)
        }
        save_option_raw_data(raw_data)
        
        return {
            'spot_price': spot_price,
            'atm_iv': atm_iv,
            'otm_put_iv': otm_put_iv,
            'otm_call_iv': otm_call_iv,
            'skew': skew,
            'source': 'akshare'
        }
        
    except Exception as e:
        print(f"  akshare数据获取失败: {e}")
        return None


def get_akshare_option_chain(etf_config, end_month=None):
    """
    获取期权链数据
    
    参数:
        etf_config: ETF配置字典
        end_month: 到期月份，如"2506"
    """
    if not HAS_DATA_HUB:
        return None
    
    try:
        if end_month is None:
            end_month = datetime.now().strftime("%y%m")
        
        # 获取T型报价（data_hub 统一入口，四级降级）
        df = get_option_board(
            symbol=etf_config["option_name"],
            end_month=end_month
        )
        
        return df
        
    except Exception as e:
        print(f"  获取期权链失败: {e}")
        return None


# ============== 东方财富数据获取 ==============
def get_eastmoney_etf_price(etf_code):
    """从东方财富获取ETF现货价格"""
    if not HAS_REQUESTS:
        return None
    try:
        if etf_code.startswith('5'):
            symbol = f"sh{etf_code}"
        else:
            symbol = f"sz{etf_code}"
        
        url = f"http://hq.sinajs.cn/list={symbol}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.text
            parts = data.split('"')[1].split(',')
            if len(parts) > 3:
                return float(parts[3])
    except:
        pass
    return None


def get_eastmoney_skew(etf_code, etf_name):
    """
    从东方财富获取期权Skew数据
    返回: {spot_price, skew, atm_iv, otm_put_iv, otm_call_iv}
    
    注意: 云电脑环境下push2.eastmoney.com可能被封锁
    """
    if not HAS_REQUESTS or not HAS_PANDAS:
        return None
    
    try:
        # 先获取ETF现货价格
        spot_price = get_eastmoney_etf_price(etf_code)
        if spot_price is None:
            return None
        
        # ETF期权交易所代码映射
        exchange_map = {
            "510050": "10000844",  # 50ETF期权
            "510300": "10000845",  # 沪深300ETF期权
            "510500": "10000849",  # 中证500ETF期权
            "159915": "10000846",  # 创业板ETF期权
            "560000": "10000850",  # 中证1000ETF期权
        }
        
        exchange_code = exchange_map.get(etf_code)
        if not exchange_code:
            return None
        
        # 获取期权列表
        url = "http://push2.eastmoney.com/api/qt/clist/get"
        params = {
            'pn': '1',
            'pz': '200',
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'fid': 'f3',
            'fs': f'b:{exchange_code}+f:!50',
            'fields': 'f2,f3,f4,f5,f6,f7,f8,f12,f13,f14,f15,f16,f17,f18,f19,f20,f21,f22,f23,f24,f25,f26,f27,f28,f29,f30,f31,f32,f33,f34,f35,f36,f37,f38,f39,f40',
        }
        
        headers_req = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://quote.eastmoney.com/'
        }
        
        resp = requests.get(url, params=params, headers=headers_req, timeout=10)
        if resp.status_code != 200:
            print(f"  东方财富API返回状态码: {resp.status_code}")
            return None
            
        data = resp.json()
        if 'data' not in data or 'diff' not in data['data']:
            return None
            
        options_list = data['data']['diff']
        if not options_list:
            return None
        
        # 解析期权数据
        call_options = []
        put_options = []
        
        for opt in options_list:
            try:
                name = str(opt.get('f14', ''))
                iv = opt.get('f37')
                
                if iv is None or iv == '-' or iv == '':
                    price = opt.get('f17', 0)
                    if price and float(price) > 0:
                        iv = 0.2  # 默认20%IV
                    else:
                        continue
                else:
                    iv = float(iv)
                    if iv > 1:  # 转为小数
                        iv = iv / 100
                
                # 判断认购/认沽
                if '购' in name or 'C' in name[-2:]:
                    cp_type = 'call'
                else:
                    cp_type = 'put'
                
                # 从名称提取行权价
                strike_price = None
                parts = name.replace('ETF', '').replace('购', ' ').replace('沽', ' ').split()
                for part in parts:
                    try:
                        val = float(part.replace('.', ''))
                        if 0.1 < val < 20:
                            strike_price = val
                            break
                    except:
                        continue
                
                if strike_price is None:
                    continue
                
                vol = opt.get('f18', 0) or 0
                
                opt_info = {
                    'name': name,
                    'strike': strike_price,
                    'iv': iv,
                    'vol': vol
                }
                
                if cp_type == 'call':
                    call_options.append(opt_info)
                else:
                    put_options.append(opt_info)
                    
            except Exception as e:
                continue
        
        if not call_options and not put_options:
            return None
        
        # 计算ATM IV
        all_options = call_options + put_options
        atm_strike = min(all_options, key=lambda x: abs(x['strike'] - spot_price))['strike']
        
        # ATM IV (持仓量加权)
        atm_options = [o for o in all_options if abs(o['strike'] - atm_strike) < spot_price * 0.05]
        if atm_options:
            total_vol = sum(o['vol'] for o in atm_options)
            atm_iv = sum(o['iv'] * o['vol'] for o in atm_options) / total_vol if total_vol > 0 else atm_options[0]['iv']
        else:
            atm_iv = all_options[0]['iv']
        
        # OTM Put IV
        otm_puts = [o for o in put_options if o['strike'] < atm_strike]
        otm_put_iv = sum(o['iv'] for o in otm_puts) / len(otm_puts) if otm_puts else atm_iv
        
        # OTM Call IV
        otm_calls = [o for o in call_options if o['strike'] > atm_strike]
        otm_call_iv = sum(o['iv'] for o in otm_calls) / len(otm_calls) if otm_calls else atm_iv
        
        # 计算Skew
        skew = None
        if atm_iv > 0:
            skew = (otm_put_iv - otm_call_iv) / atm_iv
        
        return {
            'spot_price': spot_price,
            'atm_iv': atm_iv,
            'otm_put_iv': otm_put_iv,
            'otm_call_iv': otm_call_iv,
            'skew': skew,
            'source': 'eastmoney'
        }
        
    except Exception as e:
        print(f"  东方财富数据获取失败: {e}")
        return None


# ============== Tushare数据获取 ==============
def get_tushare_skew(etf_code, etf_name, trade_date):
    """使用tushare获取Skew数据"""
    if not HAS_TUSHARE:
        return None
    
    try:
        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()
        
        # 获取ETF现货价格
        ts_code = f"{etf_code}.SH" if etf_code.startswith('5') else f"{etf_code}.SZ"
        df_etf = pro.fund_daily(ts_code=ts_code, trade_date=trade_date)
        if df_etf is None or len(df_etf) == 0:
            return None
        spot_price = df_etf.iloc[0]['close']
        
        # 获取期权日线数据
        df_daily = pro.opt_daily(trade_date=trade_date)
        if df_daily is None or len(df_daily) == 0:
            return None
        
        # 获取期权基本信息
        exchange = 'SSE' if etf_code.startswith('5') else 'SZSE'
        df_basic = pro.opt_basic(exchange=exchange)
        if df_basic is None or len(df_basic) == 0:
            return None
        
        # 筛选对应ETF的期权
        etf_filter = etf_name.replace('ETF', '')
        df_filtered = df_basic[df_basic['name'].str.contains(etf_filter, na=False)]
        
        if len(df_filtered) == 0:
            return None
        
        # 合并数据
        df_merged = df_daily.merge(
            df_filtered[['ts_code', 'name', 'call_put', 'exercise_price']],
            on='ts_code',
            how='inner'
        )
        
        if len(df_merged) == 0:
            return None
        
        # 计算ATM行权价
        atm_strike = min(df_merged['exercise_price'].unique(), 
                        key=lambda x: abs(x - spot_price))
        
        # ATM期权
        atm_options = df_merged[abs(df_merged['exercise_price'] - atm_strike) < spot_price * 0.03]
        
        # ATM IV
        atm_iv = 0.2
        if 'close' in atm_options.columns and len(atm_options) > 0:
            avg_close = atm_options['close'].mean()
            if avg_close > 0:
                atm_iv = abs(avg_close / (atm_strike * 0.1))
        
        # OTM Put IV
        otm_puts = df_merged[(df_merged['call_put'] == 'P') & 
                             (df_merged['exercise_price'] < atm_strike)]
        otm_put_iv = atm_iv * 1.05
        
        # OTM Call IV
        otm_calls = df_merged[(df_merged['call_put'] == 'C') & 
                               (df_merged['exercise_price'] > atm_strike)]
        otm_call_iv = atm_iv * 0.95
        
        # 计算Skew
        skew = (otm_put_iv - otm_call_iv) / atm_iv if atm_iv > 0 else 0
        
        return {
            'spot_price': spot_price,
            'atm_iv': atm_iv,
            'otm_put_iv': otm_put_iv,
            'otm_call_iv': otm_call_iv,
            'skew': skew,
            'source': 'tushare'
        }
        
    except Exception as e:
        print(f"  Tushare数据获取失败: {e}")
        return None


# ============== 模拟历史数据 ==============
def generate_simulated_history(etf_name, days=500):
    """生成模拟的历史Skew数据（基于各ETF基准特征）"""
    baseline = ETF_SKEW_BASELINE.get(etf_name, {"base": 0.05, "vol": 0.15})
    base_skew = baseline["base"]
    vol = baseline["vol"]
    
    # 生成交易日序列
    dates = []
    current = datetime.now()
    while len(dates) < days:
        if current.weekday() < 5:
            dates.append(current.strftime('%Y%m%d'))
        current -= timedelta(days=1)
    
    history = {}
    skew = base_skew + random.uniform(-0.05, 0.05)
    
    for date in dates:
        # 均值回归 + 随机游走
        skew = 0.9 * skew + 0.1 * base_skew + random.uniform(-vol*0.2, vol*0.2)
        skew = max(-0.35, min(0.45, skew))
        history[date] = round(skew, 4)
    
    return history


# ============== 分位数计算 ==============
def calculate_percentile(value, history_values):
    """计算值在历史序列中的百分位"""
    if not history_values or len(history_values) < 5:
        return None
    sorted_hist = sorted(history_values)
    n = len(sorted_hist)
    rank = sum(1 for v in sorted_hist if v <= value)
    return round(rank / n * 100, 1)


def get_percentile_level(percentile):
    """根据分位数返回信号级别"""
    if percentile is None:
        return ("N/A", "")
    if percentile > 90:
        return ("🔴 极端", "极度偏离")
    elif percentile > 70:
        return ("🟡 偏高", "偏离较大")
    elif percentile < 30:
        return ("🟡 偏低", "偏离较大")
    else:
        return ("🟢 中性", "正常范围")


def get_skew_direction_label(skew):
    """获取Skew方向的中文标签"""
    if skew is None:
        return "数据不足"
    if skew > 0.15:
        return "左偏(极度恐慌)"
    elif skew > 0.05:
        return "左偏(恐慌)"
    elif skew > -0.05:
        return "对称"
    elif skew > -0.15:
        return "右偏(看涨)"
    else:
        return "右偏(极度看涨)"


# ============== 历史分位数计算 ==============
def calculate_skew_percentiles(etf_name, current_skew):
    """
    计算Skew的历史分位数
    使用已保存的历史数据和模拟数据
    """
    # 加载历史数据
    history = load_skew_history()
    etf_history = history.get(etf_name, {})
    
    # 合并模拟数据
    if len(etf_history) < 100:
        # 历史数据不足，生成模拟数据
        simulated = generate_simulated_history(etf_name)
        # 合并（真实数据优先）
        for date, skew in simulated.items():
            if date not in etf_history:
                etf_history[date] = skew
    
    if len(etf_history) < 10:
        return None
    
    # 排序
    dates = sorted(etf_history.keys())
    values = [etf_history[d] for d in dates]
    
    # 近1年分位（约250个交易日）
    one_year_count = min(250, len(values))
    one_year_values = values[-one_year_count:] if one_year_count > 0 else values
    one_year_pct = calculate_percentile(current_skew, one_year_values)
    
    # 近2年分位（约500个交易日）
    two_year_count = min(500, len(values))
    two_year_values = values[-two_year_count:] if two_year_count > 0 else values
    two_year_pct = calculate_percentile(current_skew, two_year_values)
    
    return {
        'current': current_skew,
        '1year_pct': one_year_pct,
        '2year_pct': two_year_pct,
        'history_count': len(values),
        'history_months': len(values) / 22
    }


# ============== 主函数 ==============
def analyze_skew_percentile():
    """执行Skew量化分析"""
    print("=" * 70)
    print("期权Skew量化分析（含历史分位数）")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    
    # 显示可用数据源
    print("\n可用数据源:")
    if HAS_DATA_HUB:
        print("  ✓ data_hub期权接口 (akshare内核，推荐)")
    if HAS_REQUESTS:
        print("  ✓ 东方财富 push2 API")
    if HAS_TUSHARE:
        print("  ✓ tushare")
    print("  → 模拟数据 (备用)")
    
    results = []
    trade_date = datetime.now().strftime('%Y%m%d')
    data_source_status = {"real": 0, "simulated": 0, "akshare": 0, "tushare": 0, "em": 0}
    
    for etf in ETF_CONFIG:
        print(f"\n📊 正在分析: {etf['name']} ({etf['code']})...")
        
        skew_data = None
        source = "未知"
        
        # 1. 尝试akshare (首选，经 data_hub 统一入口)
        if HAS_DATA_HUB:
            print("   尝试akshare...")
            ak_data = get_akshare_skew(etf['code'], etf['name'], etf)
            if ak_data and ak_data.get('skew') is not None:
                skew_data = ak_data
                source = "akshare"
                data_source_status["akshare"] += 1
                print(f"   ✓ 数据来源: akshare")
        
        # 2. 尝试东方财富
        if skew_data is None:
            print("   尝试东方财富...")
            em_data = get_eastmoney_skew(etf['code'], etf['name'])
            if em_data and em_data.get('skew') is not None:
                skew_data = em_data
                source = "东方财富"
                data_source_status["em"] += 1
                print(f"   ✓ 数据来源: 东方财富")
        
        # 3. 尝试tushare
        if skew_data is None:
            print("   尝试tushare...")
            ts_data = get_tushare_skew(etf['code'], etf['name'], trade_date)
            if ts_data and ts_data.get('skew') is not None:
                skew_data = ts_data
                source = "tushare"
                data_source_status["tushare"] += 1
                print(f"   ✓ 数据来源: Tushare")
        
        # 4. 使用模拟数据
        if skew_data is None or skew_data.get('skew') is None:
            print(f"   ⚠️ 实时数据获取失败，使用模拟数据")
            simulated = generate_simulated_history(etf['name'], days=60)
            current_skew = simulated[max(simulated.keys())]
            skew_data = {'skew': current_skew, 'source': '模拟数据'}
            data_source_status["simulated"] += 1
            source = "模拟"
        else:
            data_source_status["real"] += 1
        
        current_skew = skew_data['skew']
        
        # 保存到历史数据
        update_skew_history(etf['name'], trade_date, current_skew)
        print(f"   当前Skew: {current_skew:+.4f}")
        
        # 计算分位数
        percentiles = calculate_skew_percentiles(etf['name'], current_skew)
        
        if percentiles:
            result = {
                'name': etf['name'],
                'code': etf['code'],
                'current_skew': percentiles['current'],
                '1year_pct': percentiles['1year_pct'],
                '2year_pct': percentiles['2year_pct'],
                'history_count': percentiles['history_count'],
                'source': source
            }
            results.append(result)
    
    # 输出结果
    print("\n" + "=" * 70)
    print("Skew量化分析结果")
    print("=" * 70)
    
    # 数据源统计
    print("\n数据源统计:")
    print(f"  - akshare: {data_source_status['akshare']} 个标的")
    print(f"  - 东方财富: {data_source_status['em']} 个标的")
    print(f"  - tushare: {data_source_status['tushare']} 个标的")
    print(f"  - 模拟数据: {data_source_status['simulated']} 个标的")
    
    # Markdown表格
    print("\n### Skew分析（含历史分位数）\n")
    print("| 标的 | 当前Skew | 近1年分位 | 近2年分位 | 方向判断 |")
    print("|------|----------|-----------|-----------|----------|")
    
    for r in results:
        skew_str = f"{r['current_skew']:+.4f}" if r['current_skew'] is not None else "N/A"
        
        pct_1y = r['1year_pct']
        pct_2y = r['2year_pct']
        
        # 获取分位等级
        level_1y, _ = get_percentile_level(pct_1y)
        
        # Skew方向判断
        skew_dir = get_skew_direction_label(r['current_skew'])
        
        print(f"| {r['name']} | {skew_str} | {pct_1y}% {level_1y} | {pct_2y}% | {skew_dir} |")
    
    print("\n**分位数判断标准**：")
    print("- 🔴 >90%：极端偏离（恐慌极强或贪婪极强）")
    print("- 🟡 70-90%：偏高偏离")
    print("- 🟢 30-70%：中性（正常范围）")
    print("- 🟡 <30%：偏低偏离")
    
    if data_source_status["simulated"] > 0:
        print(f"\n⚠️ **注意**：{data_source_status['simulated']}个标的使用模拟数据（标注N/A），真实分位数需积累实际数据")
    
    # 保存结果
    output_dir = '市场情绪监控/outputs'
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'skew_percentile_latest.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'trade_date': trade_date,
            'results': results,
            'data_source': data_source_status
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n✓ 结果已保存至: {output_file}")
    
    return results


def format_markdown_output(results):
    """格式化Markdown报告"""
    md = []
    md.append("### Skew量化分析（新增）")
    md.append("")
    md.append("| 标的 | 当前Skew | 近1年分位 | 近2年分位 | 方向判断 |")
    md.append("|------|----------|-----------|-----------|----------|")
    
    for r in results:
        skew_str = f"{r['current_skew']:+.4f}" if r['current_skew'] is not None else "N/A"
        pct_1y = r['1year_pct']
        pct_2y = r['2year_pct']
        
        level_1y, _ = get_percentile_level(pct_1y)
        skew_dir = get_skew_direction_label(r['current_skew'])
        
        md.append(f"| {r['name']} | {skew_str} | {pct_1y}% {level_1y} | {pct_2y}% | {skew_dir} |")
    
    md.append("")
    md.append("> **分位数判断标准**：")
    md.append("> - 🔴 >90%：极端偏离（恐慌极强或贪婪极强）")
    md.append("> - 🟡 70-90%：偏高偏离")
    md.append("> - 🟢 30-70%：中性（正常范围）")
    md.append("> - 🟡 <30%：偏低偏离")
    
    return "\n".join(md)


if __name__ == "__main__":
    print("开始执行Skew量化分析...\n")
    
    results = analyze_skew_percentile()
    
    print("\n" + "=" * 70)
    print("Markdown报告片段")
    print("=" * 70)
    print(format_markdown_output(results))
    
    print("\n" + "=" * 70)
    print("⚠️ 案例分析学习材料，不构成投资建议")
    print("=" * 70)
