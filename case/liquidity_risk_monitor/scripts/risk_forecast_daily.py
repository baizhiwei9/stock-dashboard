#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股风险预测日报 - 整合版
将多个风险因子模块汇总为一份统一的日度风险报告，以交互式卡片形式推送飞书。

三大风险维度（2026-10-22 起移除"衍生品定价风险"，万得全A无期权/期货该维度长期N/A）：
  ① 结构风险（CFC/CDI/up50/top5z）
  ② 量能情绪风险（AR/兴登堡/成交集中度/成交额分位）
  ③ 极端风险（LPPL泡沫检测）

注：期权 PCR/IV/GEX/基差底层数据仍正常采集，供"三维择时监控"等独立模块使用，
    仅不再作为本日报综合评级维度；两融超跌反弹 Top10 移至次日08:00两融早报。

设计原则：
  - 零新增LLM token消耗，纯Python实现
  - 尽量复用现有脚本的输出，不重复采集数据
  - 风险评分体系可解释，按维度分类展示
  - 支持Calendar定时触发（自然日每天两次）

用法:
  python risk_forecast_daily.py [trade_date] [--no-push]
  trade_date: YYYYMMDD 格式，默认当天
  --no-push: 只生成本地报告，不推送飞书
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


import os
import sys
import re
import json
import argparse
import time
import numpy as np
from datetime import datetime, timedelta

# ========== 路径配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)           # 市场情绪监控/
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')   # 市场情绪监控/outputs/
ROOT_DIR = os.path.dirname(BASE_DIR)             # 主对话根目录

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 将根目录加入路径，用于导入 feishu 工具
sys.path.insert(0, ROOT_DIR)

# 导入午盘实时数据获取模块
try:
    from realtime_fetcher import fetch_all_midday_data, fetch_all_midday_data_enhanced, get_vix_percentile_approx
    _realtime_ready = True
except ImportError:
    _realtime_ready = False
    fetch_all_midday_data = None
    get_vix_percentile_approx = None

# ========== Tushare 初始化（成交量分位计算用） ==========
# ── DataHub 统一数据入口（禁止直连 tushare/akshare 取数） ──
import pandas as pd  # noqa: E402
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

_HUB_PATHS = [
    os.path.join(ROOT_DIR, 'codeact', 'scripts'),
    _HUB_ROOT,
]
for _hp in _HUB_PATHS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
        break
try:
    import data_hub as _dh_raw
except Exception as _e:  # noqa: BLE001
    _dh_raw = None
    print(f"[DataHub] 导入失败: {_e}")


def _pick_date(df, trade_date):
    """DataHub 日线可能返回多日，按 trade_date 精确取行。"""
    if df is None or len(df) == 0 or not trade_date:
        return df
    _td = str(trade_date).replace('-', '')
    if 'trade_date' in df.columns:
        _m = df[df['trade_date'].astype(str).str.replace('-', '') == _td]
        if len(_m):
            return _m.reset_index(drop=True)
    return df.tail(1).reset_index(drop=True)


class _ProProxy:
    """tushare pro 的 DataHub 薄代理：方法签名与返回列保持 tushare 口径。"""

    def trade_cal(self, exchange='SSE', start_date=None, end_date=None, is_open='1', **kw):
        _d = _dh_raw.get_trade_dates(start_date, end_date, exchange) if _dh_raw else None
        return pd.DataFrame({'cal_date': sorted(_d or [])})

    def fund_daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None, fields=None, **kw):
        _d = _dh_raw.get_etf_daily(ts_code=ts_code, trade_date=trade_date or end_date) if _dh_raw else None
        return _pick_date(_d, trade_date or end_date)

    def index_daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None, fields=None, **kw):
        _d = _dh_raw.get_index_daily(ts_code=ts_code, trade_date=trade_date,
                                     start_date=start_date, end_date=end_date) if _dh_raw else None
        if trade_date:
            _d = _pick_date(_d, trade_date)
        return _d

    def daily(self, trade_date=None, fields=None, **kw):
        return _dh_raw.get_market_daily(trade_date=trade_date) if _dh_raw else None

    def daily_basic(self, trade_date=None, fields=None, **kw):
        return _dh_raw.get_daily_basic(trade_date) if _dh_raw else None

    def opt_basic(self, exchange='SSE', fields=None, **kw):
        return _dh_raw.get_option_basic_raw(exchange) if _dh_raw else None

    def opt_daily(self, exchange='SSE', trade_date=None, start_date=None, end_date=None, **kw):
        return _dh_raw.get_option_daily_raw(trade_date or end_date or start_date, exchange) if _dh_raw else None


pro = _ProProxy()
_tushare_ready = _dh_raw is not None


# ========== data_hub 统一数据采集层（三级降级） ==========
# data_hub 路径：主对话/codeact/scripts/data_hub/
DATA_HUB_DIR = os.path.join(ROOT_DIR, 'codeact', 'scripts')
try:
    if DATA_HUB_DIR not in sys.path:
        sys.path.insert(0, DATA_HUB_DIR)
    from data_hub import get_data, get_data_result
    _datahub_ready = True
    print("  ✅ data_hub 统一数据采集层已加载")
except Exception as _e:
    _datahub_ready = False
    get_data = None
    get_data_result = None
    print(f"  ⚠️  data_hub 加载失败: {_e}，将使用原有数据源")

# ========== 两融风险监控引擎（纯Python，8模块全覆盖） ==========
_MARGIN_MONITOR_READY = False
try:
    from margin_risk_monitor import compute_margin_signals
    _MARGIN_MONITOR_READY = True
    print("  ✅ 两融风险监控引擎已加载")
except Exception as _mm_e:
    compute_margin_signals = None
    print(f"  ⚠️  两融风险监控引擎加载失败: {_mm_e}")


# ============================================================
#  数据读取层：从各模块已有输出文件中读取数据
#  （data_hub 作为补充/兜底数据源）
# ============================================================

def _safe_load_json(path):
    """安全读取JSON文件，不存在返回None"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  [警告] 读取 {path} 失败: {e}")
        return None


def _find_latest_file(directory, pattern_prefix, pattern_suffix='.json', date_fmt='%Y%m%d', max_lookback=30):
    """在目录中查找最近的匹配文件（按文件名中的日期倒序找）
    
    Args:
        directory: 目录路径
        pattern_prefix: 文件名前缀（日期前的部分），如 'option_data' 的前缀是 ''
        pattern_suffix: 文件名后缀，如 '.json'
        date_fmt: 日期格式，'%Y%m%d' 或 '%Y-%m-%d'
        max_lookback: 最多回溯天数
    
    Returns:
        (file_path, actual_date_str) or (None, None)
    """
    if not os.path.isdir(directory):
        return None, None
    
    from datetime import timedelta as _td
    today = datetime.now()
    
    for i in range(max_lookback):
        dt = today - _td(days=i)
        date_str = dt.strftime(date_fmt)
        filename = f"{pattern_prefix}{date_str}{pattern_suffix}"
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            return filepath, date_str
    
    return None, None


def _find_latest_file_regex(directory, regex_pattern, date_group=1, date_fmt='%Y%m%d', max_lookback=30):
    """用正则在目录中查找最近的匹配文件
    
    Args:
        directory: 目录路径
        regex_pattern: 正则表达式，需包含日期捕获组
        date_group: 日期所在的捕获组编号
        date_fmt: 日期格式
        max_lookback: 最多回溯天数
    
    Returns:
        (file_path, actual_date_str) or (None, None)
    """
    import re
    if not os.path.isdir(directory):
        return None, None
    
    try:
        files = os.listdir(directory)
    except:
        return None, None
    
    matched = []
    for fname in files:
        m = re.match(regex_pattern, fname)
        if m:
            date_str = m.group(date_group)
            try:
                dt = datetime.strptime(date_str, date_fmt)
                matched.append((dt, fname, date_str))
            except:
                continue
    
    if not matched:
        return None, None
    
    # 按日期倒序
    matched.sort(key=lambda x: x[0], reverse=True)
    latest = matched[0]
    return os.path.join(directory, latest[1]), latest[2]


# ============================================================
#  data_hub 期权数据计算（从原始合约数据计算 SKEW/GEX）
#  仅作为原有 JSON 文件读取的兜底数据源
# ============================================================

def _safe_float(val, default=None):
    """安全转换为float，失败返回default"""
    if val is None:
        return default
    try:
        f = float(val)
        if np.isnan(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


# 期权标的 → data_hub 参数映射
_OPTION_DATAHUB_MAP = {
    '50ETF':     {'exchange': 'SSE',  'underlying': '510050'},
    '300ETF':    {'exchange': 'SSE',  'underlying': '510300'},
    '500ETF':    {'exchange': 'SSE',  'underlying': '510500'},
    '创业板ETF':  {'exchange': 'SZSE', 'underlying': '159915'},
    '科创50ETF':  {'exchange': 'SSE',  'underlying': '588000'},
    '中证1000':   {'exchange': 'CFFEX', 'underlying': 'MO'},
}

# ========== 期权指标统一计算（data_hub/option_metrics 单一真源）==========
try:
    from data_hub.option_metrics import (
        OptionContract,
        calc_all_option_metrics,
        get_atm_iv,
        calc_iv_skew,          # ⚠️ 简化版（±25%行权价，非真25Δ），仅为兼容保留，勿新增调用
        calc_pcr as _calc_pcr_datahub,
        calc_vix,
        calc_cboe_skew,        # ⚠️ 库落库专用口径（OTM20%put/ATM），勿用于方向判断
        # 2026-11-09 偏度口径统一：全系统唯一真源
        calc_25d_risk_reversal,
        calc_cboe_skew_directional,
    )
    _OPTION_METRICS_READY = True
    print("✅ data_hub/option_metrics 单一真源已加载")
except Exception as _e:
    _OPTION_METRICS_READY = False
    print(f"⚠️  data_hub/option_metrics 不可用: {_e}，将使用脚本内降级计算")


# ============================================================
#  期权数据增强处理（BS IV计算 + 多源数据合并）
# ============================================================

def _bs_implied_vol(S, K, T, r, option_price, option_type='C', tol=1e-6, max_iter=100):
    """Black-Scholes 隐含波动率计算（牛顿迭代法）。
    
    Args:
        S: 标的现货价格
        K: 行权价
        T: 到期时间（年）
        r: 无风险利率
        option_price: 期权价格
        option_type: 'C' 或 'P'
    Returns:
        IV (小数) 或 None
    """
    if S is None or K is None or T is None or option_price is None:
        return None
    if S <= 0 or K <= 0 or T <= 0 or option_price <= 0:
        return None
    
    # 判断内在价值，深度实值期权可能不准
    intrinsic = max(S - K, 0) if option_type == 'C' else max(K - S, 0)
    if option_price < intrinsic:
        return None  # 价格低于内在价值，不合理
    
    try:
        from scipy.stats import norm
        import math
        
        # 初始猜测
        iv = 0.3
        
        for _ in range(max_iter):
            d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
            d2 = d1 - iv * math.sqrt(T)
            
            if option_type == 'C':
                price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            else:
                price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            
            vega = S * norm.pdf(d1) * math.sqrt(T)
            if abs(vega) < 1e-12:
                break
            
            diff = option_price - price
            if abs(diff) < tol:
                return iv
            
            iv += diff / vega
            if iv <= 0.001:
                iv = 0.001
            if iv > 5:
                iv = 5
        
        return iv if 0.001 < iv < 5 else None
    except Exception:
        return None


def _filter_tushare_contracts(contracts_df, underlying, exchange):
    """按标的筛选tushare期权合约。
    
    Args:
        contracts_df: tushare opt_basic 返回的 DataFrame
        underlying: 标的代码（如 510050、MO）
        exchange: 交易所
    Returns:
        筛选后的 DataFrame
    """
    import pandas as pd
    
    if contracts_df is None or contracts_df.empty or not underlying:
        return contracts_df
    
    # 标的 -> tushare合约名称关键词映射
    name_map = {
        # SSE
        '510050': ['华夏上证50ETF'],
        '510300': ['华泰柏瑞沪深300ETF'],
        '510500': ['南方中证500ETF'],
        '588000': ['科创板50ETF', '上证科创板50ETF'],
        # SZSE
        '159915': ['创业板ETF'],
        '159919': ['沪深300ETF'],
        # CFFEX
        'MO': ['中证1000股指期权'],
        'IO': ['沪深300股指期权'],
        'HO': ['上证50股指期权'],
    }
    
    df = contracts_df.copy()
    keywords = name_map.get(underlying, [underlying])
    
    # 用 name 列匹配
    if 'name' in df.columns:
        mask = pd.Series(False, index=df.index)
        for kw in keywords:
            mask = mask | df['name'].astype(str).str.contains(kw, na=False)
        df_filtered = df[mask]
        if not df_filtered.empty:
            return df_filtered
    
    # CFFEX 股指期权：用 ts_code 前缀匹配
    if exchange == 'CFFEX' and 'ts_code' in df.columns:
        prefix_map = {'MO': 'MO', 'IO': 'IO', 'HO': 'HO'}
        if underlying in prefix_map:
            prefix = prefix_map[underlying]
            df_filtered = df[df['ts_code'].astype(str).str.startswith(prefix)]
            if not df_filtered.empty:
                return df_filtered
    
    return df


def _get_option_spot_price(underlying, trade_date):
    """获取期权标的的现货价格（多源兜底）。
        underlying: 标的代码（如 510050、MO 等）
        trade_date: 交易日期 YYYYMMDD
    Returns:
        float or None
    """
    # 映射表
    spot_map = {
        '510050': ('fund', '510050.SH'),
        '510300': ('fund', '510300.SH'),
        '510500': ('fund', '510500.SH'),
        '588000': ('fund', '588000.SH'),
        '159915': ('fund', '159915.SZ'),
        '159919': ('fund', '159919.SZ'),
        'MO': ('index', '000852.SH'),
        'IO': ('index', '000300.SH'),
        'HO': ('index', '000016.SH'),
    }
    
    if underlying not in spot_map:
        return None
    
    spot_type, ts_code = spot_map[underlying]
    
    # Tier 1: Tushare
    try:
        if _tushare_ready:
            import time
            time.sleep(0.3)  # 限速
            if spot_type == 'fund':
                df = pro.fund_daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
            else:
                df = pro.index_daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
            if df is not None and not df.empty:
                return float(df.iloc[0]['close'])
    except Exception as e:
        print(f"  [调试] tushare获取{underlying}现货价失败: {e}")
    
    # Tier 2: DataHub（ETF/指数统一入口）
    try:
        if _dh_raw is not None:
            if spot_type == 'fund':
                df = _dh_raw.get_etf_daily(ts_code=ts_code, trade_date=trade_date)
            else:
                df = _dh_raw.get_index_daily(ts_code=ts_code, trade_date=trade_date)
            df = _pick_date(df, trade_date)
            if df is not None and not df.empty:
                for col in ['close', '收盘', '收盘价']:
                    if col in df.columns:
                        return float(df.iloc[-1][col])
    except Exception as e:
        print(f"  [调试] DataHub获取{underlying}现货价失败: {e}")
    
    return None


def _normalize_option_data(daily_df, contracts_df, underlying_price, trade_date):
    """标准化期权数据，合并合约信息+计算IV，返回统一格式的DataFrame。
    
    Returns:
        DataFrame with columns: [ts_code, strike, call_put, close, oi, vol, iv, T]
        or None if data is insufficient
    """
    import pandas as pd
    
    if daily_df is None or daily_df.empty:
        return None
    if underlying_price is None or underlying_price <= 0:
        return None
    
    df = daily_df.copy()
    
    # ========== 1. 提取/合并行权价 ==========
    strike_col = None
    for col in ['exercise_price', 'strike_price', '行权价', 'x_price']:
        if col in df.columns:
            strike_col = col
            break
    
    # 如果 daily 中没有行权价，从 contracts 合并
    if strike_col is None and contracts_df is not None and not contracts_df.empty:
        # 找合并的 key 列
        merge_key = None
        for col in ['ts_code', 'opt_code', 'call_code', 'put_code', '合约交易代码', '合约代码']:
            if col in df.columns and col in contracts_df.columns:
                merge_key = col
                break
        
        if merge_key:
            # 找 contracts 中的行权价列
            c_strike_col = None
            for col in ['exercise_price', 'strike_price', '行权价', 'x_price']:
                if col in contracts_df.columns:
                    c_strike_col = col
                    break
            
            if c_strike_col:
                contracts_slim = contracts_df[[merge_key, c_strike_col]].copy()
                contracts_slim.columns = [merge_key, 'strike']
                df = df.merge(contracts_slim, on=merge_key, how='left')
                strike_col = 'strike'
    
    # 还没有行权价？尝试从 ts_code 解析（CFFEX格式: MO2607-C-5200.CFX）
    if strike_col is None and 'ts_code' in df.columns:
        strikes = []
        for code in df['ts_code'].astype(str):
            m = re.match(r'^[A-Z]+\d+-[CP]-(\d+\.?\d*)\.', code)
            if m:
                strikes.append(float(m.group(1)))
            else:
                strikes.append(None)
        df['strike'] = strikes
        if df['strike'].notna().any():
            strike_col = 'strike'
    
    if strike_col is None:
        return None
    
    # 重命名行权价列为 'strike'
    if strike_col != 'strike':
        df['strike'] = pd.to_numeric(df[strike_col], errors='coerce')
    
    # ========== 2. 提取认购/认沽标识 ==========
    call_put_col = None
    for col in ['call_put', 'opt_type', '类型', '期权类型', '合约类型']:
        if col in df.columns:
            call_put_col = col
            break
    
    if call_put_col is None and contracts_df is not None and not contracts_df.empty:
        merge_key = None
        for col in ['ts_code', 'opt_code', '合约交易代码', '合约代码']:
            if col in df.columns and col in contracts_df.columns:
                merge_key = col
                break
        if merge_key:
            for col in ['call_put', 'opt_type', '类型', '期权类型', '合约类型']:
                if col in contracts_df.columns:
                    contracts_slim = contracts_df[[merge_key, col]].copy()
                    contracts_slim.columns = [merge_key, 'call_put_raw']
                    df = df.merge(contracts_slim, on=merge_key, how='left')
                    call_put_col = 'call_put_raw'
                    break
    
    # 还没有？从 ts_code 或名称解析
    if call_put_col is None:
        # 从 ts_code 解析
        if 'ts_code' in df.columns:
            cps = []
            for code in df['ts_code'].astype(str):
                if re.search(r'-C-|C\d{4}|C\d[MA]', code):
                    cps.append('C')
                elif re.search(r'-P-|P\d{4}|P\d[MA]', code):
                    cps.append('P')
                else:
                    cps.append(None)
            df['call_put_raw'] = cps
            if df['call_put_raw'].notna().any():
                call_put_col = 'call_put_raw'
        
        # 从名称解析
        if call_put_col is None:
            name_col = None
            for col in ['name', 'opt_name', '名称', '合约简称']:
                if col in df.columns:
                    name_col = col
                    break
            if name_col:
                cps = []
                for name in df[name_col].astype(str):
                    if '购' in name or 'call' in name.lower():
                        cps.append('C')
                    elif '沽' in name or 'put' in name.lower():
                        cps.append('P')
                    else:
                        cps.append(None)
                df['call_put_raw'] = cps
                if df['call_put_raw'].notna().any():
                    call_put_col = 'call_put_raw'
    
    if call_put_col is None:
        return None
    
    # 标准化为 C/P
    df['call_put'] = df[call_put_col].astype(str).apply(
        lambda x: 'C' if re.search(r'C|购|call', x, re.IGNORECASE) else ('P' if re.search(r'P|沽|put', x, re.IGNORECASE) else None)
    )
    df = df.dropna(subset=['call_put', 'strike'])
    
    # ========== 3. 提取价格（收盘价/结算价）==========
    price_col = None
    for col in ['close', 'settle', '收盘', '最新价', '期权价格']:
        if col in df.columns:
            price_col = col
            break
    
    if price_col is None:
        return None
    
    df['price'] = pd.to_numeric(df[price_col], errors='coerce')
    
    # ========== 4. 提取持仓量和成交量 ==========
    oi_col = None
    for col in ['oi', 'open_interest', '持仓量', '未平仓量', '合约总持仓']:
        if col in df.columns:
            oi_col = col
            break
    
    vol_col = None
    for col in ['vol', 'volume', '成交量', '成交']:
        if col in df.columns:
            vol_col = col
            break
    
    df['oi'] = pd.to_numeric(df[oi_col], errors='coerce') if oi_col else 0
    df['vol'] = pd.to_numeric(df[vol_col], errors='coerce') if vol_col else 0
    df['oi'] = df['oi'].fillna(0)
    df['vol'] = df['vol'].fillna(0)
    
    # ========== 5. 计算到期时间 T ==========
    T_col = None
    if 'maturity_date' in df.columns:
        df['maturity'] = pd.to_datetime(df['maturity_date'], errors='coerce')
        T_col = 'maturity'
    elif contracts_df is not None and not contracts_df.empty and 'maturity_date' in contracts_df.columns:
        merge_key = None
        for col in ['ts_code', 'opt_code', '合约交易代码', '合约代码']:
            if col in df.columns and col in contracts_df.columns:
                merge_key = col
                break
        if merge_key:
            contracts_slim = contracts_df[[merge_key, 'maturity_date']].copy()
            df = df.merge(contracts_slim, on=merge_key, how='left')
            df['maturity'] = pd.to_datetime(df['maturity_date'], errors='coerce')
            T_col = 'maturity'
    
    if T_col:
        trade_dt = pd.to_datetime(trade_date)
        df['T'] = (df[T_col] - trade_dt).dt.days / 365.0
        df = df[df['T'] > 0]  # 排除已到期
    else:
        # 默认到期时间约30天（粗略估算）
        df['T'] = 30 / 365.0
    
    # ========== 6. 计算 IV ==========
    r = 0.015  # 无风险利率
    ivs = []
    for _, row in df.iterrows():
        S = underlying_price
        K = row['strike']
        T = row['T']
        price = row['price']
        cp = row['call_put']
        
        iv = _bs_implied_vol(S, K, T, r, price, cp)
        ivs.append(iv)
    
    df['iv'] = ivs
    
    # 过滤掉 IV 异常的合约
    df_valid = df[df['iv'].notna() & (df['iv'] > 0.01) & (df['iv'] < 2)].copy()
    
    if len(df_valid) < 4:  # 至少需要几个合约才能算SKEW
        return None
    
    # 添加 ts_code 列（如果没有）
    if 'ts_code' not in df_valid.columns:
        df_valid['ts_code'] = range(len(df_valid))
    
    # 2026-10-24 fix: 保留 maturity 列供 _dataframes_to_option_contracts 解析到期日
    _out_cols = ['ts_code', 'strike', 'call_put', 'price', 'oi', 'vol', 'iv', 'T']
    if 'maturity' in df_valid.columns:
        _out_cols.append('maturity')
    return df_valid[_out_cols].reset_index(drop=True)


def _calc_iv_skew_simple(daily_df, contracts_df, underlying_price):
    """【已废弃 / DEAD CODE，禁止调用】基于期权日线数据计算简化版 SKEW

    ⚠️ 2026-11-09 偏度口径统一（skewunify）：
    本函数用 `spot * 0.9` / `spot * 1.1` 冒充「25Δ 虚值行权价」，并非真实 25Δ；
    cboe_skew 又是第三个自造公式 `115 + ratio*50`（115 中枢），
    与全系统口径均不一致。**已全仓 grep 确认无任何调用点**，故保留不删以免
    外部历史引用断裂，但任何新代码严禁使用。

    唯一真源：`data_hub.option_metrics.calc_25d_risk_reversal()`
              + `data_hub.option_metrics.calc_cboe_skew_directional()`

    返回: {cboe_skew, iv_skew, atm_iv, put_25d_iv, call_25d_iv}
    """
    import pandas as pd
    
    if daily_df is None or daily_df.empty or underlying_price is None:
        return None
    
    try:
        # 合并合约信息（获取行权价、认购认沽标识）
        df = daily_df.copy()
        if contracts_df is not None and not contracts_df.empty:
            # 尝试通过 ts_code 或 opt_code 合并
            for code_col in ['ts_code', 'opt_code', 'call_code', 'put_code']:
                if code_col in df.columns and code_col in contracts_df.columns:
                    df = df.merge(contracts_df, on=code_col, how='left', suffixes=('', '_c'))
                    break
        
        # 提取行权价
        strike = None
        for col in ['exercise_price', 'strike_price', 'x_price', '行权价']:
            if col in df.columns:
                strike = col
                break
        if strike is None:
            return None
        
        # 提取期权类型（认购/认沽）
        opt_type_col = None
        for col in ['call_put', 'opt_type', '类型', '期权类型']:
            if col in df.columns:
                opt_type_col = col
                break
        
        # 提取隐含波动率
        iv_col = None
        for col in ['iv', 'implied_volatility', '隐含波动率']:
            if col in df.columns:
                iv_col = col
                break
        
        if iv_col is None:
            return None
        
        # 分离认购和认沽
        if opt_type_col:
            calls = df[df[opt_type_col].astype(str).str.contains('C|购|call', case=False, na=False)]
            puts = df[df[opt_type_col].astype(str).str.contains('P|沽|put', case=False, na=False)]
        else:
            # 通过合约名称判断
            name_col = None
            for col in ['name', 'opt_name', '名称']:
                if col in df.columns:
                    name_col = col
                    break
            if name_col:
                calls = df[df[name_col].astype(str).str.contains('购', na=False)]
                puts = df[df[name_col].astype(str).str.contains('沽', na=False)]
            else:
                return None
        
        if calls.empty or puts.empty:
            return None
        
        # 找 ATM (平值)
        calls['_diff'] = (calls[strike] - underlying_price).abs()
        puts['_diff'] = (puts[strike] - underlying_price).abs()
        atm_call = calls.nsmallest(1, '_diff').iloc[0]
        atm_put = puts.nsmallest(1, '_diff').iloc[0]
        atm_iv = (_safe_float(atm_call[iv_col]) + _safe_float(atm_put[iv_col])) / 2
        
        # 找 25delta 对应的虚值期权（简化：用行权价距离判断）
        # 25d put ≈ 低于现货价约 10% 的行权价（简化估算）
        # 25d call ≈ 高于现货价约 10% 的行权价（简化估算）
        otm_put_strike = underlying_price * 0.9
        otm_call_strike = underlying_price * 1.1
        
        # 最接近的虚值认沽
        otm_puts = puts[puts[strike] < underlying_price].copy()
        otm_calls = calls[calls[strike] > underlying_price].copy()
        
        if otm_puts.empty or otm_calls.empty:
            return None
        
        otm_puts['_diff'] = (otm_puts[strike] - otm_put_strike).abs()
        otm_calls['_diff'] = (otm_calls[strike] - otm_call_strike).abs()
        
        put_25d = otm_puts.nsmallest(1, '_diff').iloc[0]
        call_25d = otm_calls.nsmallest(1, '_diff').iloc[0]
        
        put_25d_iv = _safe_float(put_25d[iv_col])
        call_25d_iv = _safe_float(call_25d[iv_col])
        
        if put_25d_iv is None or call_25d_iv is None or atm_iv is None:
            return None
        
        # IV偏度（认沽IV - 认购IV，通常认沽IV更高为正向偏度）
        iv_skew = put_25d_iv - call_25d_iv
        
        # 简化 CBOE SKEW 指数转换
        # SKEW = 100 - 10 * (IV偏度 / ATM_IV) * 100 （经验公式，近似）
        if atm_iv > 0:
            skew_ratio = iv_skew / atm_iv
            # 映射到 CBOE SKEW 范围（约 100-150）
            cboe_skew = round(115 + skew_ratio * 50, 2)  # 经验映射
        else:
            cboe_skew = None
        
        return {
            'cboe_skew': cboe_skew,
            'iv_skew': round(iv_skew, 2),
            'atm_iv': round(atm_iv * 100, 2) if atm_iv < 1 else round(atm_iv, 2),
            'put_25d_iv': round(put_25d_iv * 100, 2) if put_25d_iv < 1 else round(put_25d_iv, 2),
            'call_25d_iv': round(call_25d_iv * 100, 2) if call_25d_iv < 1 else round(call_25d_iv, 2),
        }
    except Exception as e:
        print(f"  [调试] SKEW计算失败: {e}")
        return None


def _calc_gex_simple(daily_df, contracts_df, underlying_price):
    """基于期权日线数据计算简化版 GEX（Gamma敞口）
    
    简化算法：用 delta 近似 gamma，基于持仓量估算总Gamma暴露
    返回: {total_gex, total_gex_yi, risk, flip_point}
    """
    import pandas as pd
    
    if daily_df is None or daily_df.empty or underlying_price is None or underlying_price <= 0:
        return None
    
    try:
        # 合并合约信息
        df = daily_df.copy()
        if contracts_df is not None and not contracts_df.empty:
            for code_col in ['ts_code', 'opt_code', 'call_code', 'put_code']:
                if code_col in df.columns and code_col in contracts_df.columns:
                    df = df.merge(contracts_df, on=code_col, how='left', suffixes=('', '_c'))
                    break
        
        # 提取行权价
        strike_col = None
        for col in ['exercise_price', 'strike_price', 'x_price', '行权价']:
            if col in df.columns:
                strike_col = col
                break
        if strike_col is None:
            return None
        
        # 提取持仓量（open interest）
        oi_col = None
        for col in ['oi', 'open_interest', '持仓量', '未平仓量']:
            if col in df.columns:
                oi_col = col
                break
        
        # 提取成交量（OI缺失时用成交量近似）
        vol_col = None
        for col in ['volume', 'vol', '成交量']:
            if col in df.columns:
                vol_col = col
                break
        
        # 提取期权类型
        opt_type_col = None
        for col in ['call_put', 'opt_type', '类型', '期权类型']:
            if col in df.columns:
                opt_type_col = col
                break
        
        name_col = None
        if not opt_type_col:
            for col in ['name', 'opt_name', '名称']:
                if col in df.columns:
                    name_col = col
                    break
        
        # 简化Gamma计算：假设ATM gamma最大，向两边递减
        # 使用 Black-Scholes 简化：gamma ≈ 1/(S * σ * sqrt(T))
        # 这里用更简化的方法：基于 moneyness 估算 gamma
        
        total_gex = 0.0
        df = df.dropna(subset=[strike_col])
        
        for _, row in df.iterrows():
            K = _safe_float(row[strike_col])
            if K is None or K <= 0:
                continue
            
            # 获取 OI 或成交量
            oi = _safe_float(row[oi_col]) if oi_col else None
            if oi is None or oi == 0:
                oi = _safe_float(row[vol_col]) if vol_col else None
            if oi is None or oi == 0:
                continue
            
            # 判断认购/认沽
            is_call = False
            if opt_type_col:
                val = str(row.get(opt_type_col, ''))
                is_call = bool(re.search(r'C|购|call', val, re.IGNORECASE))
            elif name_col:
                val = str(row.get(name_col, ''))
                is_call = '购' in val
            
            # 简化 Gamma 估算
            # ATM gamma 最大，随 moneyness 偏离递减
            moneyness = K / underlying_price
            # 高斯近似：gamma ∝ exp(-(ln(K/S))² / (2*σ²T))
            # 简化：用 1 - |moneyness - 1| * 2，限制在0-1范围
            gamma_factor = max(0, 1 - abs(moneyness - 1) * 3)
            
            # 合约乘数（ETF期权通常是10000份/张，股指期权是每点100元）
            contract_multiplier = 10000  # 默认ETF期权
            
            # 每合约 Gamma 暴露 = OI * gamma_factor * 合约乘数 * 现货价 * 0.01
            # （gamma * S * 0.01 ≈ 1%价格变动对应的delta变化）
            gex_per_contract = oi * gamma_factor * contract_multiplier * underlying_price * 0.01
            
            # 认购期权GEX为正（涨→买→推动上涨），认沽期权GEX为负（跌→卖→加剧下跌）
            # 注意：做市商方向相反，这里用市场净暴露近似
            if is_call:
                total_gex += gex_per_contract
            else:
                total_gex -= gex_per_contract
        
        # 转换为亿元单位
        total_gex_yi = round(total_gex / 1e8, 2)
        
        # 风险等级（基于总GEX大小和符号）
        if total_gex_yi is None:
            risk = '中'
        elif total_gex_yi >= 5:
            risk = '低'
        elif total_gex_yi >= 0:
            risk = '中'
        elif total_gex_yi >= -5:
            risk = '高'
        else:
            risk = '高'
        
        return {
            'total_gex': total_gex,
            'total_gex_yi': total_gex_yi,
            'flip_point': None,
            'risk': risk,
            'details_count': len(df),
        }
    except Exception as e:
        print(f"  [调试] GEX计算失败: {e}")
        return None



def _dataframes_to_option_contracts(norm_df, spot_price, exchange, underlying):
    """将标准化后的DataFrame转换为 data_hub/option_metrics.OptionContract 列表
    
    norm_df 格式 (来自 _normalize_option_data):
        columns: ts_code, strike, call_put, close, oi, vol, iv, T
        - call_put: 'C' or 'P' 或 '认购'/'认沽'
        - iv: 隐含波动率（小数形式，如0.25=25%）
        - T: 到期时间（年化）
        - ts_code: 合约代码
    """
    if not _OPTION_METRICS_READY:
        return None
    
    contracts = []
    try:
        for _, row in norm_df.iterrows():
            try:
                # 期权类型
                cp = str(row.get('call_put', '')).upper()
                opt_type = 'C' if cp in ('C', '认购', 'CALL') else 'P'
                
                # 行权价
                strike = float(row.get('strike', 0))
                if strike <= 0:
                    continue
                
                # 到期日 - 从 ts_code 解析或从 T 推算
                ts_code = str(row.get('ts_code', ''))
                expiry_str = ''
                
                # 尝试从 ts_code 解析到期日
                # 格式: 510050C2611M02916 (50ETF), IO2611-C-4000 (CFFEX)
                import re as _re
                # CFFEX: XX2611-C-4000.CFX -> 20261101 (需完整解析)
                m = _re.match(r'^[A-Za-z]+(\d{4})-(\d{2})-(\d{2})', ts_code)
                if m:
                    expiry_str = f"20{m.group(1)}{m.group(2)}{m.group(3)}"
                else:
                    # ETF: 510050C2611M02916 -> 到期月 2611
                    m = _re.search(r'[CP](\d{4})M', ts_code)
                    if m:
                        ym = m.group(1)
                        year = 2000 + int(ym[:2])
                        month = int(ym[2:])
                        # 到期日取第三个周五（近似）
                        import calendar
                        try:
                            cal = calendar.Calendar()
                            fridays = [d for d in cal.itermonthdates(year, month) 
                                      if d.weekday() == 4 and d.month == month]
                            if len(fridays) >= 3:
                                expiry_str = fridays[2].strftime('%Y%m%d')
                            elif fridays:
                                expiry_str = fridays[-1].strftime('%Y%m%d')
                            else:
                                expiry_str = f"{year}{month:02d}15"
                        except:
                            expiry_str = f"{year}{month:02d}15"
                
                # 2026-10-24 fix: tushare格式 ts_code (如 10012638.SH) 无法解析到期月
                # 降级到 maturity 列（_normalize_option_data 已保留）
                if not expiry_str and 'maturity' in row.index:
                    try:
                        import pandas as _pd
                        _mat = row.get('maturity')
                        if _pd.notna(_mat):
                            if hasattr(_mat, 'strftime'):
                                expiry_str = _mat.strftime('%Y%m%d')
                            else:
                                expiry_str = str(_mat).replace('-', '')[:8]
                    except Exception:
                        pass
                
                # IV
                iv = float(row.get('iv', 0)) if row.get('iv') else 0.0
                
                # DTE
                T = float(row.get('T', 0)) if row.get('T') else 0.0
                dte = max(1, int(T * 365)) if T > 0 else 30
                
                # 收盘价/中间价
                # 2026-11-09 fix: _normalize_option_data 输出的列名是 price（不是 close），
                # 原代码只读 close 导致 close 恒为 0，calc_25d_risk_reversal 的 BS 反推
                # 必然失败（该路径此前一直静默降级）。两列都兼容。
                _cl = row.get('close', None)
                if _cl is None or (isinstance(_cl, float) and _cl != _cl) or float(_cl or 0) <= 0:
                    _cl = row.get('price', 0)
                close = float(_cl) if _cl else 0.0
                
                c = OptionContract(
                    code=ts_code,
                    underlying=str(underlying),
                    strike=strike,
                    expiry=expiry_str,
                    opt_type=opt_type,
                    close=close,
                    volume=float(row.get('vol', 0)) if row.get('vol') else 0.0,
                    open_interest=float(row.get('oi', 0)) if row.get('oi') else 0.0,
                    iv=iv,
                    dte=dte,
                )
                contracts.append(c)
            except Exception:
                continue
    except Exception as e:
        print(f"  [调试] DataFrame->OptionContract 转换异常: {e}")
    
    return contracts if contracts else None



def _fetch_option_from_datahub(trade_date):
    """从 data_hub 获取期权原始数据并计算 SKEW/GEX
    
    返回格式与 {date}_option_data.json 一致，方便无缝替换
    
    Returns:
        (data_dict, trade_date) or (None, None)
    """
    if not _datahub_ready or get_data is None:
        return None, None
    
    print(f"  📡 尝试从 data_hub 获取期权数据 ({trade_date})...")
    
    result = {
        'trade_date': trade_date,
        'calculate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'skew': {},
        'gex': {},
        'basis': [],  # 基差数据（data_hub futures_basis 采集器）
    }

    # ===== 获取股指期货基差数据（data_hub 三级降级） =====
    try:
        basis_data = get_data(
            "futures_basis",
            trade_date=trade_date,
            products=['IF', 'IC', 'IM'],
        )
        if basis_data and isinstance(basis_data, list) and len(basis_data) > 0:
            # 转换格式，适配原有期权数据的 basis 字段格式
            # 原有格式: [{product, name, type, contract, dte, basis_pts, basis_rate, rank_252, z_score, signal}]
            # 关键：type 必须为 "当季" 才会被 build_index_signals 采用
            for b in basis_data:
                result['basis'].append({
                    'product': b.get('product', ''),
                    'name': b.get('index_name', ''),  # 原格式用 name 而非 index_name
                    'type': '当季',  # 主力连续近似当季主力，标记为当季以兼容下游逻辑
                    'contract_type': b.get('contract_type', '主力连续'),
                    'futures_price': b.get('futures_price', 0),
                    'spot_price': b.get('spot_price', 0),
                    'basis': b.get('basis', 0),
                    'basis_pts': b.get('basis', 0),  # 兼容 basis_pts 字段
                    'basis_rate': b.get('basis_rate', 0),
                    'basis_rate_annual': b.get('basis_rate_annual', 0),
                    'rank_252': b.get('rank_252'),  # data_hub版本暂无历史分位
                    'z_score': None,
                    'signal': None,
                    'data_source': 'data_hub',  # 标记来源
                })
            print(f"  ✅ data_hub基差数据: {len(basis_data)} 个品种")
        else:
            print(f"  ⚠️  data_hub基差数据为空")
    except Exception as e:
        print(f"  ⚠️  data_hub基差获取失败: {e}")
    
    success_count = 0
    
    for opt_name, params in _OPTION_DATAHUB_MAP.items():
        try:
            exchange = params['exchange']
            underlying = params['underlying']
            
            # ===== Tier 1: 从 data_hub 获取期权数据 =====
            contracts = None
            daily = None
            dh_underlying_price = None
            dh_source = None
            
            try:
                option_data = get_data(
                    "option_data",
                    exchange=exchange,
                    underlying=underlying,
                    trade_date=trade_date,
                )
                if option_data and isinstance(option_data, dict):
                    contracts = option_data.get('contracts')
                    daily = option_data.get('daily')
                    dh_underlying_price = option_data.get('underlying_price')
                    dh_source = option_data.get('_source', 'data_hub')
            except Exception as e:
                print(f"  [调试] {opt_name} data_hub获取异常: {e}")
            
            # ===== Tier 2: 如果 daily 为空，直接用 tushare 拉取完整数据 =====
            # data_hub 的 AKShare tier 1 经常只有合约没有行情，且合约代码格式与tushare不同
            if daily is None or (hasattr(daily, 'empty') and daily.empty):
                try:
                    if _tushare_ready:
                        import time
                        # 拉取合约基本信息
                        time.sleep(0.3)
                        tushare_contracts = pro.opt_basic(exchange=exchange)
                        
                        # 按标的筛选合约
                        if tushare_contracts is not None and not tushare_contracts.empty:
                            filtered_contracts = _filter_tushare_contracts(
                                tushare_contracts, underlying, exchange
                            )
                            
                            if not filtered_contracts.empty:
                                # 拉取日线数据
                                time.sleep(0.3)
                                tushare_daily = pro.opt_daily(
                                    exchange=exchange,
                                    start_date=trade_date,
                                    end_date=trade_date,
                                )
                                
                                if tushare_daily is not None and not tushare_daily.empty:
                                    # 按合约代码筛选 daily
                                    valid_codes = set(filtered_contracts['ts_code'].values)
                                    filtered_daily = tushare_daily[
                                        tushare_daily['ts_code'].isin(valid_codes)
                                    ].copy()
                                    
                                    if not filtered_daily.empty:
                                        contracts = filtered_contracts
                                        daily = filtered_daily
                                        print(f"  [调试] {opt_name}: tushare补全: {len(filtered_daily)}条daily, {len(filtered_contracts)}个合约")
                except Exception as e:
                    print(f"  [调试] {opt_name} tushare补全失败: {e}")
            
            # 如果还是没有 daily，跳过这个标的
            if daily is None or (hasattr(daily, 'empty') and daily.empty):
                print(f"  ⚠️  {opt_name}: 无daily行情数据，跳过")
                continue
            
            # ===== Tier 3: 获取标的现货价格（自己拉，不依赖 data_hub） =====
            underlying_price = dh_underlying_price
            if underlying_price is None:
                underlying_price = _get_option_spot_price(underlying, trade_date)
            
            if underlying_price is None or underlying_price <= 0:
                print(f"  ⚠️  {opt_name}: 无标的现货价格，跳过")
                continue
            
            # ===== 标准化数据 + 计算 IV =====
            norm_df = _normalize_option_data(daily, contracts, underlying_price, trade_date)
            if norm_df is None or norm_df.empty:
                print(f"  ⚠️  {opt_name}: 数据标准化失败，跳过")
                continue
            
            print(f"  [调试] {opt_name}: 有效IV合约{len(norm_df)}个, 现货价={underlying_price}")
            
            # ===== 计算 SKEW（优先 data_hub/option_metrics 单一真源）=====
            skew_result = None
            if _OPTION_METRICS_READY:
                try:
                    contracts_list = _dataframes_to_option_contracts(norm_df, underlying_price, exchange, underlying)
                    if contracts_list and len(contracts_list) >= 10:
                        # 使用 data_hub/option_metrics 统一计算
                        # 2026-11-09 偏度口径统一：IV 反推/选月/25Δ 定位走唯一真源
                        _rr = calc_25d_risk_reversal(contracts_list, underlying_price)
                        _atm_iv = (_rr or {}).get('atm_iv') or get_atm_iv(contracts_list, underlying_price)
                        if _rr and _rr.get('put25_iv') is not None and _rr.get('call25_iv') is not None:
                            _iv_skew_raw = float(_rr['put25_iv']) - float(_rr['call25_iv'])
                            _cboe_skew_val = calc_cboe_skew_directional(
                                _rr.get('atm_iv'), _rr.get('put25_iv'))
                        else:
                            _iv_skew_raw = None
                            _cboe_skew_val = None
                        skew_result = {
                            'atm_iv': _atm_iv,
                            'cboe_skew': _cboe_skew_val,
                            # 展示层统一 ×100（计算层一律小数）
                            'iv_skew': (round(_iv_skew_raw * 100, 2) if _iv_skew_raw is not None else None),
                            'iv_skew_raw': _iv_skew_raw,
                            'source': 'data_hub/option_metrics',
                        }
                except Exception as _sk_e:
                    print(f"  [调试] {opt_name} data_hub SKEW失败: {_sk_e}，降级到脚本内计算")
            if skew_result is None:
                skew_result = _calc_skew_from_normalized(norm_df, underlying_price)
                if skew_result:
                    skew_result['source'] = 'script_internal'
            if skew_result:
                result['skew'][opt_name] = skew_result
                success_count += 0.5
                if skew_result.get('source') == 'data_hub/option_metrics':
                    print(f"  ✅ {opt_name} SKEW (data_hub): CBOE={skew_result.get('cboe_skew'):.2f}, ATM_IV={skew_result.get('atm_iv')}")
                else:
                    print(f"  ✅ {opt_name} SKEW (降级): {skew_result.get('cboe_skew', 'N/A')}")
            
            # ===== 计算 GEX（脚本内计算，data_hub/option_metrics 暂无 GEX）=====
            gex_result = _calc_gex_from_normalized(norm_df, underlying_price, underlying)
            if gex_result:
                gex_result['source'] = 'script_internal'
                result['gex'][opt_name] = gex_result
                success_count += 0.5
                print(f"  ✅ {opt_name} GEX: {gex_result.get('total_gex_yi', 'N/A')}亿")

            
        except Exception as e:
            print(f"  ⚠️  {opt_name} 处理失败: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    if success_count >= 1:  # 至少1个标的有有效数据
        print(f"  ✅ data_hub期权数据获取完成 ({int(success_count)}个标的有数据)")
        return result, trade_date
    else:
        print(f"  ❌ data_hub期权数据获取失败，无有效数据")
        return None, None


def _calc_skew_from_normalized(norm_df, underlying_price):
    """基于标准化后的期权数据计算 SKEW + PCR。
    
    Args:
        norm_df: DataFrame with columns [strike, call_put, iv, T, price, oi, vol]
        underlying_price: 标的现货价格
    Returns:
        dict with cboe_skew, iv_skew, atm_iv, put_25d_iv, call_25d_iv, vol_pcr, oi_pcr or None
    """
    try:
        import pandas as pd
        
        # 过滤掉dte<3天的合约（T < 3/365 ≈ 0.0082）
        min_dte_threshold = 3 / 365.0
        if 'T' in norm_df.columns:
            valid_df = norm_df[norm_df['T'] >= min_dte_threshold].copy()
            if len(valid_df) < 6:  # 不够用则放宽到>=2天
                min_dte_threshold = 2 / 365.0
                valid_df = norm_df[norm_df['T'] >= min_dte_threshold].copy()
            if len(valid_df) < 4:
                valid_df = norm_df.copy()  # 最后兜底
        else:
            valid_df = norm_df.copy()
        
        # 选最近到期月的合约（T最小的那个月）
        if 'T' in valid_df.columns:
            # 找最近到期的月份（四舍五入到月）
            valid_df = valid_df.copy()
            valid_df['month_T'] = (valid_df['T'] * 12).round() / 12
            nearest_month_T = valid_df['month_T'].min()
            near_df = valid_df[valid_df['month_T'] == nearest_month_T].copy()
        else:
            near_df = valid_df.copy()
        
        calls = near_df[near_df['call_put'] == 'C'].copy()
        puts = near_df[near_df['call_put'] == 'P'].copy()
        
        if calls.empty or puts.empty:
            return None
        
        # 找 ATM
        calls['_diff'] = (calls['strike'] - underlying_price).abs()
        puts['_diff'] = (puts['strike'] - underlying_price).abs()
        atm_call = calls.nsmallest(1, '_diff').iloc[0]
        atm_put = puts.nsmallest(1, '_diff').iloc[0]
        atm_iv = (_safe_float(atm_call['iv']) + _safe_float(atm_put['iv'])) / 2
        
        # ===== 2026-11-09 偏度口径统一 =====
        # 原实现用 spot*0.9 / spot*1.1 当作「25Δ 虚值行权价」，并非真 25Δ，
        # 且 cboe_skew 用自造公式 115 + ratio*50（第三个不同中枢）。
        # 现全部交给 data_hub.option_metrics 唯一真源：
        #   BS 反推 IV → BS delta 反解 25Δ 行权价 → RR = IV(Put25Δ) − IV(Call25Δ)
        # 传「全链」而非 near_df：让 calc_25d_risk_reversal 用自己的选月规则
        # （优先 dte≥10，逐级放宽），否则会绕过统一选月、与主路径不同月。
        try:
            _oc = _dataframes_to_option_contracts(
                norm_df, underlying_price, None, None)
        except Exception:
            _oc = None
        _rr = None
        if _oc:
            try:
                _rr = calc_25d_risk_reversal(_oc, underlying_price)
            except Exception as _e:
                print(f"  [调试] 降级路径统一偏度失败: {_e}")
                _rr = None
        if not _rr or _rr.get('put25_iv') is None or _rr.get('call25_iv') is None:
            return None

        put_25d_iv = float(_rr['put25_iv'])
        call_25d_iv = float(_rr['call25_iv'])
        atm_iv = float(_rr['atm_iv'])
        if atm_iv <= 0:
            return None

        # IV偏度（计算层小数）
        iv_skew = put_25d_iv - call_25d_iv

        # 统一 cboe 口径（100 中枢，与 classify_skew_direction 阈值同源）
        cboe_skew = calc_cboe_skew_directional(atm_iv, put_25d_iv)
        
        # PCR计算（成交量和持仓量）
        vol_pcr = None
        oi_pcr = None
        if 'vol' in near_df.columns and 'oi' in near_df.columns:
            total_call_vol = near_df[near_df['call_put'] == 'C']['vol'].sum()
            total_put_vol = near_df[near_df['call_put'] == 'P']['vol'].sum()
            total_call_oi = near_df[near_df['call_put'] == 'C']['oi'].sum()
            total_put_oi = near_df[near_df['call_put'] == 'P']['oi'].sum()
            vol_pcr = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else 0
            oi_pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0
        
        result = {
            'cboe_skew': cboe_skew,
            'iv_skew': round(iv_skew * 100, 2),  # 展示层百分比
            'iv_skew_raw': round(iv_skew, 6),    # 计算层小数
            'atm_iv': round(atm_iv * 100, 2),
            'put_25d_iv': round(put_25d_iv * 100, 2),
            'call_25d_iv': round(call_25d_iv * 100, 2),
            'contract_count': len(near_df),
        }
        if vol_pcr is not None:
            result['vol_pcr'] = vol_pcr
        if oi_pcr is not None:
            result['oi_pcr'] = oi_pcr
        # 不再将vol_pcr别名为amt_pcr，统一使用vol_pcr口径
        
        return result
    except Exception as e:
        print(f"  [调试] SKEW计算失败: {e}")
        return None


def _calc_gex_from_normalized(norm_df, underlying_price, underlying=None):
    """基于标准化后的期权数据计算简化版 GEX。
    
    Args:
        norm_df: DataFrame with columns [strike, call_put, iv, oi, vol, T]
        underlying_price: 标的现货价格
        underlying: 标的代码（用于判断合约乘数）
    Returns:
        dict with total_gex, total_gex_yi, risk, details_count or None
    """
    try:
        import pandas as pd
        
        if norm_df is None or norm_df.empty or underlying_price is None or underlying_price <= 0:
            return None
        
        df = norm_df.copy()
        df = df.dropna(subset=['strike'])
        
        # 合约乘数
        # ETF期权: 10000份/张 (50ETF, 300ETF, 500ETF, 创业板, 科创50)
        # 股指期权: 每点100元 (MO中证1000, IO沪深300, HO上证50)
        is_index = underlying and underlying in ['MO', 'IO', 'HO']
        
        if is_index:
            contract_multiplier = 100  # 每点100元
            # 股指期权的GEX单位：点 → 元，需要特殊处理
            # 总GEX ≈ sum(oi * gamma * 100)，单位：元/1%指数变动
            # 这里用简化估算
        else:
            contract_multiplier = 10000  # ETF期权：份/张
        
        total_gex = 0.0
        
        for _, row in df.iterrows():
            K = _safe_float(row['strike'])
            if K is None or K <= 0:
                continue
            
            # 获取 OI（持仓量），OI 缺失时用成交量近似
            oi = _safe_float(row.get('oi'))
            if oi is None or oi == 0:
                oi = _safe_float(row.get('vol'))
            if oi is None or oi == 0:
                continue
            
            is_call = row.get('call_put') == 'C'
            
            # 简化 Gamma 估算
            # ATM gamma 最大，随 moneyness 偏离递减
            moneyness = K / underlying_price
            gamma_factor = max(0, 1 - abs(moneyness - 1) * 3)
            
            # 每合约 Gamma 暴露 ≈ OI * gamma_factor * 合约乘数 * 现货价 * 0.01
            # （gamma * S * 0.01 ≈ 1%价格变动对应的delta变化）
            gex_per_contract = oi * gamma_factor * contract_multiplier * underlying_price * 0.01
            
            # 认购GEX为正，认沽GEX为负
            if is_call:
                total_gex += gex_per_contract
            else:
                total_gex -= gex_per_contract
        
        # 转换为亿元单位
        total_gex_yi = round(total_gex / 1e8, 2)
        
        # 风险等级
        if total_gex_yi is None:
            risk = '中'
        elif total_gex_yi >= 5:
            risk = '低'
        elif total_gex_yi >= 0:
            risk = '中'
        elif total_gex_yi >= -5:
            risk = '高'
        else:
            risk = '高'
        
        return {
            'total_gex': total_gex,
            'total_gex_yi': total_gex_yi,
            'flip_point': None,
            'risk': risk,
            'details_count': len(df),
            'is_index_option': is_index,
        }
    except Exception as e:
        print(f"  [调试] GEX计算失败: {e}")
        return None


def load_option_data(trade_date, enable_fallback=False):
    """读取期权综合监控数据（SKEW + GEX + 基差）
    
    数据源优先级（双重保险）：
      1. 本地 JSON 文件（期权监控脚本预计算，质量最高）
      2. data_hub 统一采集层（三级降级，实时获取+计算）
      3. 回退到最近日期的 JSON 文件（仅 enable_fallback 时）
    
    Args:
        trade_date: 目标日期 YYYYMMDD
        enable_fallback: 是否允许自动回退到最近有数据的交易日
    
    Returns:
        (data_dict, actual_date) or (None, None)
    """
    # Tier 1: 本地 JSON 文件（预计算数据，质量最高）
    path = os.path.join(OUTPUT_DIR, f'{trade_date}_option_data.json')
    data = _safe_load_json(path)
    if data:
        print(f"  ✅ 期权数据已加载 ({trade_date}) [来源: 本地JSON]")
        return data, trade_date
    
    # Tier 2: data_hub 统一采集层（新增 - 自动三级降级）
    if _datahub_ready:
        dh_result, dh_date = _fetch_option_from_datahub(trade_date)
        if dh_result:
            print(f"  ✅ 期权数据已加载 ({trade_date}) [来源: data_hub]")
            return dh_result, dh_date
    
    if not enable_fallback:
        print(f"  ⚪ 期权数据当日暂无数据 ({trade_date})，不启用回退")
        return None, None
    
    # Tier 3: 回退到最近日期的 JSON 文件
    print(f"  ⚠️ 期权数据当日暂无 ({trade_date})，尝试回退到最近日期...")
    latest_path, actual_date = _find_latest_file(
        OUTPUT_DIR, '', '_option_data.json', '%Y%m%d', max_lookback=30
    )
    # _find_latest_file 的 pattern_prefix 是空，文件名是 {date}_option_data.json
    # 上面的方式不对，因为 pattern_prefix 是日期前的部分，但这里日期在前面
    # 改用 regex 方式
    if latest_path is None:
        latest_path, actual_date = _find_latest_file_regex(
            OUTPUT_DIR, r'^(\d{8})_option_data\.json$', 1, '%Y%m%d', 30
        )
    
    if latest_path and actual_date:
        data = _safe_load_json(latest_path)
        if data:
            lag_days = (datetime.strptime(trade_date, '%Y%m%d') - datetime.strptime(actual_date, '%Y%m%d')).days
            print(f"  ⏳ 期权数据回退到 {actual_date}（滞后{lag_days}天） [来源: 本地JSON回退]")
            return data, actual_date
    
    print(f"  ❌ 期权数据无可用数据源（JSON + data_hub 均失败）")
    return None, None


def load_cfc_data(trade_date, enable_fallback=False):
    """读取CFC结构因子数据
    
    来源: CFC结构因子/historical_factors.json
    支持自动回退到最近有数据的交易日（默认不启用）
    
    Args:
        trade_date: 目标日期 YYYYMMDD
        enable_fallback: 是否允许自动回退到最近有数据的交易日
    
    Returns:
        (data_dict, actual_date) or (None, None)
    """
    path = os.path.join(ROOT_DIR, 'CFC结构因子', 'historical_factors.json')
    history = _safe_load_json(path)
    if not history:
        print(f"  ⚠️ CFC历史数据文件不存在: {path}")
        return None, None
    
    # 先尝试目标日期
    result = {}
    for pool_name, pool_data in history.items():
        if trade_date in pool_data:
            result[pool_name] = pool_data[trade_date]
    
    if result:
        print(f"  ✅ CFC数据已加载 ({trade_date}, {len(result)}个标的池)")
        return result, trade_date
    
    if not enable_fallback:
        print(f"  ⚪ CFC数据当日暂无数据 ({trade_date})，不启用回退")
        return None, None
    
    # 回退：找所有池都有的最近日期
    print(f"  ⚠️ CFC数据中无 {trade_date} 记录，尝试回退...")
    all_dates = set()
    for pool_name, pool_data in history.items():
        all_dates.update(pool_data.keys())
    
    # 找小于等于目标日期的最近日期
    target_dt = datetime.strptime(trade_date, '%Y%m%d')
    valid_dates = [d for d in all_dates if d <= trade_date and re.match(r'^\d{8}$', d)]
    if valid_dates:
        valid_dates.sort(reverse=True)
        for fallback_date in valid_dates[:10]:  # 最多试前10个日期
            result = {}
            for pool_name, pool_data in history.items():
                if fallback_date in pool_data:
                    result[pool_name] = pool_data[fallback_date]
            if len(result) >= 2:  # 至少有2个标的池数据才算有效
                lag_days = (target_dt - datetime.strptime(fallback_date, '%Y%m%d')).days
                print(f"  ⏳ CFC数据回退到 {fallback_date}（滞后{lag_days}天, {len(result)}个标的池）")
                return result, fallback_date
    
    print(f"  ❌ CFC数据无可用记录")
    return None, None


def load_structural_factor_data(trade_date, enable_fallback=False):
    """读取结构因子数据（AbsFactor + RelFactor）
    
    来源: 结构因子分析/outputs/structural_factor_summary.json
    注: summary存的是最新快照，latest_date为数据最新日期
    支持自动回退到最近有数据的日期（默认不启用）
    
    Args:
        trade_date: 目标日期 YYYYMMDD
        enable_fallback: 是否允许自动回退到最近有数据的交易日
    
    Returns:
        (data_dict, actual_date) or (None, None)
        data_dict 结构: {ts_code: {latest_date, abs_factor, rel_factor, ...}}
    """
    path = os.path.join(ROOT_DIR, '结构因子分析', 'outputs', 'structural_factor_summary.json')
    summary = _safe_load_json(path)
    if not summary:
        print(f"  ⚠️ 结构因子汇总文件不存在: {path}")
        return None, None
    
    # 收集各指数的最新日期和有数据的日期
    latest_dates = set()
    date_display = trade_date[:4] + '-' + trade_date[4:6] + '-' + trade_date[6:]
    date_match_count = 0  # 目标日期有数据的指数数量
    total_count = len(summary)
    
    for ts_code, d in summary.items():
        if isinstance(d, dict) and 'latest_date' in d:
            ld = d['latest_date']
            latest_dates.add(ld)
            if ld == date_display:
                date_match_count += 1
    
    # 目标日期有半数以上指数有数据，视为当日可用
    if total_count > 0 and date_match_count >= max(1, total_count // 2):
        print(f"  ✅ 结构因子数据已加载 ({trade_date}, {date_match_count}/{total_count}个指数)")
        return summary, trade_date
    
    if not enable_fallback:
        print(f"  ⚪ 结构因子数据当日暂无数据 ({trade_date}, {date_match_count}/{total_count}个指数)，不启用回退")
        return None, None
    
    # 回退：用所有指数中最新的日期
    if latest_dates:
        latest = max(latest_dates)
        actual_date = latest.replace('-', '')
        lag_days = (datetime.strptime(trade_date, '%Y%m%d') - datetime.strptime(actual_date, '%Y%m%d')).days
        # 统计最新日期有多少指数匹配
        latest_match = sum(1 for d in summary.values()
                          if isinstance(d, dict) and d.get('latest_date') == latest)
        print(f"  ⏳ 结构因子数据回退到 {latest}（滞后{lag_days}天, {latest_match}/{total_count}个指数）")
        return summary, actual_date
    else:
        print(f"  ⚠️ 结构因子数据无有效日期")
        return summary, None


def load_style_drift_data(trade_date):
    """读取风格漂移监控数据
    
    来源: 市场情绪监控/outputs/{date}_style_drift.json
    支持自动回退到最近有数据的交易日
    
    Returns:
        (data_dict, actual_date) or (None, None)
    """
    path = os.path.join(OUTPUT_DIR, f'{trade_date}_style_drift.json')
    data = _safe_load_json(path)
    if data:
        print(f"  ✅ 风格漂移数据已加载 ({trade_date})")
        return data, trade_date
    
    # 回退
    print(f"  ⚠️ 风格漂移数据文件不存在 ({trade_date})，尝试回退...")
    latest_path, actual_date = _find_latest_file_regex(
        OUTPUT_DIR, r'^(\d{8})_style_drift\.json$', 1, '%Y%m%d', 30
    )
    if latest_path and actual_date:
        data = _safe_load_json(latest_path)
        if data:
            lag_days = (datetime.strptime(trade_date, '%Y%m%d') - datetime.strptime(actual_date, '%Y%m%d')).days
            print(f"  ⏳ 风格漂移数据回退到 {actual_date}（滞后{lag_days}天）")
            return data, actual_date
    
    print(f"  ❌ 风格漂移数据无可用文件")
    return None, None


def calc_volume_percentile(trade_date, lookback=252):
    """直接计算全A成交额年度分位数
    方法: 上证指数 + 深证综指 合并 ≈ 全A
    
    数据源优先级（统一走 data_hub 三级降级体系）：
      1. data_hub 双指数合并（上证指数+深证综指，最快，内部走AKShare→Tushare→Web三级降级）
      2. data_hub 全市场日线汇总（三级降级，稳定）
      3. Tushare 直连双指数合并（data_hub失效后的最终兜底）
      4. Tushare 逐日全市场 + 市值前2000（最终兜底）
    """
    if not _tushare_ready and not _datahub_ready:
        return None
    
    end_dt = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=int(lookback * 1.6))
    start_str = start_dt.strftime('%Y%m%d')
    
    amounts = None
    
    # ===== Tier 1: data_hub 双指数合并（优先走data_hub统一入口，内部三级降级） =====
    if _datahub_ready:
        print(f"  📡 尝试 data_hub 双指数合并法...")
        try:
            # 通过 data_hub 获取上证指数和深证综指日线
            # 注：stock_daily 采集器支持指数代码，内部走 AKShare→Tushare→Web 三级降级
            df_sh = get_data("stock_daily", ts_code='000001.SH',
                            start_date=start_str, end_date=trade_date)
            df_sz = get_data("stock_daily", ts_code='399106.SZ',
                            start_date=start_str, end_date=trade_date)
            
            # 尝试从返回的DataFrame中提取成交额列
            amt_col_sh = None
            amt_col_sz = None
            date_col_sh = None
            date_col_sz = None
            
            if df_sh is not None and hasattr(df_sh, '__len__') and len(df_sh) > 0:
                for col in ['amount', '成交额', 'Amount']:
                    if col in df_sh.columns:
                        amt_col_sh = col
                        break
                for col in ['trade_date', '日期', 'date']:
                    if col in df_sh.columns:
                        date_col_sh = col
                        break
            
            if df_sz is not None and hasattr(df_sz, '__len__') and len(df_sz) > 0:
                for col in ['amount', '成交额', 'Amount']:
                    if col in df_sz.columns:
                        amt_col_sz = col
                        break
                for col in ['trade_date', '日期', 'date']:
                    if col in df_sz.columns:
                        date_col_sz = col
                        break
            
            if (amt_col_sh and date_col_sh and len(df_sh) >= 60 and
                amt_col_sz and date_col_sz and len(df_sz) >= 60):
                df_sh_clean = df_sh[[date_col_sh, amt_col_sh]].copy()
                df_sh_clean.columns = ['trade_date', 'amt_sh']
                df_sz_clean = df_sz[[date_col_sz, amt_col_sz]].copy()
                df_sz_clean.columns = ['trade_date', 'amt_sz']
                
                # 统一日期格式
                df_sh_clean['trade_date'] = df_sh_clean['trade_date'].astype(str).str.replace('-', '')
                df_sz_clean['trade_date'] = df_sz_clean['trade_date'].astype(str).str.replace('-', '')
                
                df_merged = df_sh_clean.merge(df_sz_clean, on='trade_date', how='inner')
                df_merged = df_merged.sort_values('trade_date').tail(lookback)
                df_merged['total'] = df_merged['amt_sh'].astype(float) + df_merged['amt_sz'].astype(float)
                amounts = df_merged['total'].dropna().values
                
                if len(amounts) >= 60:
                    print(f"  ✅ 成交量分位: data_hub 双指数合并 ({len(amounts)}天)")
        except Exception as e:
            print(f"  [警告] data_hub双指数合并失败: {e}", file=sys.stderr)
    
    # ===== Tier 2: data_hub 全市场日线汇总（三级降级） =====
    if (amounts is None or len(amounts) < 60) and _datahub_ready:
        print(f"  📡 尝试 data_hub 全市场日线汇总...")
        try:
            # 获取交易日历
            dates = []
            if _tushare_ready:
                trade_dates_df = pro.trade_cal(exchange='SSE', start_date=start_str,
                                                end_date=trade_date, is_open='1')
                if trade_dates_df is not None and len(trade_dates_df) > 0:
                    dates = trade_dates_df['cal_date'].tolist()[-lookback:]
            
            if not dates:
                from datetime import timedelta as _td
                cur = start_dt
                while cur <= end_dt:
                    if cur.weekday() < 5:
                        dates.append(cur.strftime('%Y%m%d'))
                    cur += _td(days=1)
                dates = dates[-lookback:]
            
            daily_amounts = []
            for d in dates:
                try:
                    df_day = get_data("stock_daily", trade_date=d)
                    if df_day is not None and hasattr(df_day, '__len__') and len(df_day) > 0:
                        amt_col = None
                        for col in ['amount', '成交额', 'Amount']:
                            if col in df_day.columns:
                                amt_col = col
                                break
                        if amt_col:
                            daily_amounts.append({'date': d, 'amount': float(df_day[amt_col].sum())})
                except:
                    pass
            
            if len(daily_amounts) >= 60:
                daily_amounts.sort(key=lambda x: x['date'])
                amounts = np.array([x['amount'] for x in daily_amounts])
                print(f"  ✅ 成交量分位: data_hub 全市场汇总 ({len(amounts)}天)")
        except Exception as e:
            print(f"  [警告] data_hub全市场汇总失败: {e}", file=sys.stderr)
    
    # ===== Tier 3: Tushare 直连双指数合并（data_hub失效后的最终兜底） =====
    if (amounts is None or len(amounts) < 60) and _tushare_ready:
        try:
            df_sh = pro.index_daily(ts_code='000001.SH', start_date=start_str,
                                     end_date=trade_date, fields='trade_date,amount')
            df_sz = pro.index_daily(ts_code='399106.SZ', start_date=start_str,
                                     end_date=trade_date, fields='trade_date,amount')
            if (df_sh is not None and len(df_sh) >= 60 and 'amount' in df_sh.columns and
                df_sz is not None and len(df_sz) >= 60 and 'amount' in df_sz.columns):
                df_sh = df_sh.rename(columns={'amount': 'amt_sh'})
                df_sz = df_sz.rename(columns={'amount': 'amt_sz'})
                df_merged = df_sh.merge(df_sz, on='trade_date', how='inner')
                df_merged = df_merged.sort_values('trade_date').tail(lookback)
                df_merged['total'] = df_merged['amt_sh'] + df_merged['amt_sz']
                amounts = df_merged['total'].dropna().values
                print(f"  ✅ 成交量分位: Tushare直连双指数合并 ({len(amounts)}天) [兜底]")
        except Exception as e:
            print(f"  [警告] Tushare直连双指数合并失败: {e}", file=sys.stderr)
    
    # ===== Tier 4: 最终兜底 - Tushare逐日全市场 + 市值前2000 =====
    if (amounts is None or len(amounts) < 60) and _tushare_ready:
        try:
            trade_dates_df = pro.trade_cal(exchange='SSE', start_date=start_str,
                                            end_date=trade_date, is_open='1')
            if trade_dates_df is not None and len(trade_dates_df) > 0:
                dates = trade_dates_df['cal_date'].tolist()[-lookback:]
                daily_amounts = []
                for d in dates:
                    try:
                        df_day = pro.daily(trade_date=d, fields='amount')
                        if df_day is not None and len(df_day) > 0:
                            daily_amounts.append({'date': d, 'amount': df_day['amount'].sum()})
                        time.sleep(0.15)
                    except:
                        pass
                if len(daily_amounts) >= 60:
                    daily_amounts.sort(key=lambda x: x['date'])
                    amounts = np.array([x['amount'] for x in daily_amounts])
                    print(f"  ✅ 成交量分位: Tushare逐日汇总 ({len(amounts)}天) [兜底]")
        except:
            pass
    
    # ===== Tier 5: 最兜底 - 当日市值前2000（无历史分位） =====
    if amounts is None or len(amounts) < 60:
        if _tushare_ready:
            try:
                df_basics = pro.daily_basic(trade_date=trade_date, fields='ts_code,total_mv,amount')
                if df_basics is not None and len(df_basics) > 0:
                    df_basics = df_basics.dropna(subset=['total_mv', 'amount'])
                    df_basics = df_basics.sort_values('total_mv', ascending=False).head(2000)
                    today_amount = df_basics['amount'].sum() / 1e9
                    return {
                        'today_amount': round(today_amount, 2),
                        'percentile': None,
                        'high_1y': None,
                        'low_1y': None,
                        'tag': '⚠️',
                        'top2000_fallback': True,
                        'fallback': 'tushare',
                    }
            except:
                pass
        # data_hub 兜底：当日全市场成交额（无历史分位）
        if _datahub_ready and amounts is None:
            try:
                df_day = get_data("stock_daily", trade_date=trade_date)
                if df_day is not None and hasattr(df_day, '__len__') and len(df_day) > 0:
                    amt_col = None
                    for col in ['amount', '成交额', 'Amount']:
                        if col in df_day.columns:
                            amt_col = col
                            break
                    if amt_col:
                        today_amount = float(df_day[amt_col].sum()) / 1e9
                        return {
                            'today_amount': round(today_amount, 2),
                            'percentile': None,
                            'high_1y': None,
                            'low_1y': None,
                            'tag': '⚠️',
                            'top2000_fallback': True,
                            'datahub_fallback': True,
                            'fallback': 'datahub',
                        }
            except:
                pass
        return None
    
    # 计算分位数
    today_amt = amounts[-1]
    today_wanyi = today_amt / 1e9
    pct = round(np.sum(amounts <= today_amt) / len(amounts) * 100, 1)
    high_1y = round(np.max(amounts) / 1e9, 2)
    low_1y = round(np.min(amounts) / 1e9, 2)
    
    # 分位数标签
    if pct >= 95:
        tag = "🔴🔴 极度放量"
    elif pct >= 80:
        tag = "🔴 放量预警"
    elif pct >= 60:
        tag = "🟠 偏高"
    elif pct >= 40:
        tag = "🟢 正常"
    elif pct >= 20:
        tag = "🟠 偏低"
    else:
        tag = "🔴 缩量预警"
    
    return {
        'today_amount': round(today_wanyi, 2),
        'percentile': pct,
        'high_1y': high_1y,
        'low_1y': low_1y,
        'tag': tag,
        'top2000_fallback': False,
    }


def load_volume_percentile_data(trade_date):
    """成交量分位数数据（内置计算，不依赖外部文件）
    
    Returns:
        (data_dict, trade_date) or (None, None)
    """
    data = calc_volume_percentile(trade_date)
    if data and data.get('percentile') is not None:
        print(f"  ✅ 成交量分位计算完成 ({trade_date}): {data['percentile']}%")
        return data, trade_date
    elif data:
        print(f"  ⚠️ 成交量分位数据不完整 ({trade_date})")
        return data, trade_date
    else:
        print(f"  ❌ 成交量分位计算失败")
        return None, None


def load_lppl_data(trade_date, index_code='000001.SH'):
    """读取LPPL泡沫检测数据（带缓存）
    
    数据来源优先级：
      1. 内存缓存（本次运行内有效）
      2. 持久化缓存文件 outputs/lppl_cache.json（跨运行有效，7天刷新）
      3. 当日 LPPL 分析文件 {date}_LPPL分析_{index_code}.json
      4. 历史 LPPL 分析文件（回看365天，LPPL为周线级别指标，允许较长滞后）
    
    Returns:
        (data_list, actual_date) or (None, None)
    """
    # 内存缓存（本次进程内有效）
    if hasattr(load_lppl_data, '_cache') and load_lppl_data._cache:
        cached_date = load_lppl_data._cache.get('date')
        cached_data = load_lppl_data._cache.get('data')
        if cached_data and cached_date:
            # 检查缓存是否仍然有效（7天内）
            try:
                cache_dt = datetime.strptime(cached_date, '%Y%m%d')
                target_dt = datetime.strptime(trade_date, '%Y%m%d')
                if (target_dt - cache_dt).days < 7:
                    print(f"  ⚡ LPPL数据命中内存缓存 ({cached_date})")
                    return cached_data, cached_date
            except:
                pass
    
    # 持久化缓存文件
    cache_path = os.path.join(OUTPUT_DIR, 'lppl_cache.json')
    cache_data = _safe_load_json(cache_path)
    if cache_data and isinstance(cache_data, dict):
        cached_date = cache_data.get('date')
        cached_result = cache_data.get('result')
        if cached_date and cached_result:
            try:
                cache_dt = datetime.strptime(cached_date, '%Y%m%d')
                target_dt = datetime.strptime(trade_date, '%Y%m%d')
                if (target_dt - cache_dt).days < 7:
                    print(f"  ⚡ LPPL数据命中持久化缓存 ({cached_date})")
                    # 同步到内存缓存
                    load_lppl_data._cache = {'date': cached_date, 'data': cached_result}
                    return cached_result, cached_date
            except:
                pass
    
    # 当日文件
    path = os.path.join(OUTPUT_DIR, f'{trade_date}_LPPL分析_{index_code}.json')
    data = _safe_load_json(path)
    if data:
        print(f"  ✅ LPPL数据已加载 ({index_code})")
        # 写入缓存
        _save_lppl_cache(trade_date, data)
        return data, trade_date
    
    # 回退：扩大回看范围到365天（LPPL为周线级别指标）
    print(f"  ⚠️ LPPL数据文件不存在 ({trade_date})，尝试回退（365天）...")
    latest_path, actual_date = _find_latest_file_regex(
        OUTPUT_DIR, rf'^(\d{{8}})_LPPL分析_{index_code.replace(".", r"\.")}\.json$', 1, '%Y%m%d', 365
    )
    if latest_path and actual_date:
        data = _safe_load_json(latest_path)
        if data:
            lag_days = (datetime.strptime(trade_date, '%Y%m%d') - datetime.strptime(actual_date, '%Y%m%d')).days
            print(f"  ⏳ LPPL数据回退到 {actual_date}（滞后{lag_days}天）")
            # 写入缓存
            _save_lppl_cache(actual_date, data)
            return data, actual_date
    
    print(f"  ❌ LPPL数据无可用文件（365天内无LPPL分析结果）")
    return None, None


def _save_lppl_cache(date_str, data):
    """保存LPPL结果到持久化缓存"""
    cache_path = os.path.join(OUTPUT_DIR, 'lppl_cache.json')
    try:
        cache_obj = {
            'date': date_str,
            'result': data,
            'cached_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_obj, f, ensure_ascii=False, indent=2)
        # 同步到内存缓存
        load_lppl_data._cache = {'date': date_str, 'data': data}
    except Exception as e:
        print(f"  [调试] LPPL缓存写入失败: {e}")


def load_market_monitor_summary(trade_date):
    """读取市场综合监控的飞书推送版，提取VIX/AR/兴登堡等数据
    
    尝试从 市场情绪监控/outputs/{date}_流动性风险偏好_飞书推送.md 中解析
    支持自动回退到最近有数据的交易日
    
    Returns:
        (data_dict, actual_date) or (None, None)
    """
    date_display = trade_date[:4] + '-' + trade_date[4:6] + '-' + trade_date[6:]
    path = os.path.join(OUTPUT_DIR, f'{date_display}_流动性风险偏好_飞书推送.md')
    
    if not os.path.exists(path):
        # 回退：找最近的文件
        print(f"  ⚠️ 市场监控推送文件不存在 ({date_display})，尝试回退...")
        latest_path, actual_date = _find_latest_file_regex(
            OUTPUT_DIR, r'^(\d{4}-\d{2}-\d{2})_流动性风险偏好_飞书推送\.md$', 1, '%Y-%m-%d', 30
        )
        if latest_path and actual_date:
            lag_days = (datetime.strptime(trade_date, '%Y%m%d') - datetime.strptime(actual_date, '%Y-%m-%d')).days
            print(f"  ⏳ 市场监控数据回退到 {actual_date}（滞后{lag_days}天）")
            path = latest_path
            actual_date_ymd = actual_date.replace('-', '')
        else:
            print(f"  ❌ 市场监控数据无可用文件")
            return None, None
    else:
        actual_date_ymd = trade_date
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        result = {}
        
        # 解析涨停/跌停/北向/成交额/集中度
        import re
        
        # 全A成交额
        m = re.search(r'全A成交额[:：]\s*([\d.]+)\s*万亿', content)
        if m:
            result['total_amount'] = float(m.group(1))
        
        # 成交额集中度
        m = re.search(r'成交额集中度[:：]\s*([\d.]+)\s*%', content)
        if m:
            result['concentration'] = float(m.group(1))
        
        # 涨停/跌停
        m = re.search(r'涨停[/／]跌停[:：]\s*(\d+)[/／](\d+)\s*家', content)
        if m:
            result['up_limit'] = int(m.group(1))
            result['down_limit'] = int(m.group(2))
        
        # 北向资金
        m = re.search(r'北向资金[:：]\s*([+-]?[\d.]+)\s*亿', content)
        if m:
            result['north_money'] = float(m.group(1))
        
        # 流动性评分
        m = re.search(r'流动性评分[:：]\s*(\d+)/60', content)
        if m:
            result['liquidity_score'] = int(m.group(1))
        
        # 风险偏好评分
        m = re.search(r'风险偏好评分[:：]\s*(\d+)/40', content)
        if m:
            result['risk_score'] = int(m.group(1))
        
        # 综合评分
        m = re.search(r'综合评分[:：]\s*(\d+)/100', content)
        if m:
            result['total_score'] = int(m.group(1))
        
        if result:
            print(f"  ✅ 市场监控摘要已解析 ({len(result)}个指标)")
        return (result if result else None), actual_date_ymd
        
    except Exception as e:
        print(f"  [警告] 解析市场监控文件失败: {e}")
        return None, None


# ============================================================
#  指数映射表
# ============================================================

# 期权标的 → 指数名称映射
OPT_TO_INDEX = {
    '50ETF': '上证50',
    '300ETF': '沪深300',
    '500ETF': '中证500',
    '创业板ETF': '创业板指',
    '科创50ETF': '科创50',
    '中证1000': '中证1000',
}

# 基差产品 → 指数名称映射
BASIS_TO_INDEX = {
    'IF': '沪深300',
    'IC': '中证500',
    'IM': '中证1000',
}

# CFC标的池 → 指数名称映射
CFC_TO_INDEX = {
    '沪深300': '沪深300',
    '中证1000': '中证1000',
    '创业板': '创业板指',
    '科创板': '科创50',
    '全市场前2000': '万得全A',
}

# 结构因子指数代码 → 名称映射（已有INDEX_NAMES，这里简化）
SF_TO_INDEX = {
    '000300.SH': '沪深300',
    '000905.SH': '中证500',
    '000852.SH': '中证1000',
    '000688.SH': '科创50',
    '399006.SZ': '创业板指',
}

# 五大核心指数（主要展示）
CORE_INDICES = ['上证50', '沪深300', '中证500', '中证1000', '创业板指']
ALL_INDICES = ['上证50', '沪深300', '中证500', '中证1000', '创业板指', '科创50', '万得全A']

# 六大宽基指数 ts_code（量能成交额分位，data_hub 三级降级采集）
CORE_INDEX_TSCODES = ['000016.SH', '000300.SH', '000905.SH', '000852.SH', '399006.SZ', '000688.SH']
TSCODE_TO_INDEX = {
    '000016.SH': '上证50',
    '000300.SH': '沪深300',
    '000905.SH': '中证500',
    '000852.SH': '中证1000',
    '399006.SZ': '创业板指',
    '000688.SH': '科创50',
}


def calc_core_index_amount_pct(trade_date, lookback=250):
    """六大宽基指数量能（成交额/成交量）分位数 —— data_hub 统一采集层（AKShare→Tushare→网页 三级降级）

    补齐"各指数风险对比表"的量能情绪列（原先仅计算万得全A，其余指数 N/A）。
    分位数为指数自身量能序列的相对分位，不受来源金额/手数单位差异影响。

    口径选择：指数日线在 AKShare(新浪源) 下只有 vol(成交量) 没有 amount(成交额)，
    Tushare 才有 amount。这里优先用 amount，缺失时自动降级用 vol，分位语义一致。

    Args:
        trade_date: YYYYMMDD
        lookback: 回溯交易日数

    Returns:
        {指数名: {'percentile': float, 'ma20_ratio': float, 'metric': 'amount'|'vol'}}
    """
    from datetime import datetime as _dt, timedelta as _td
    import pandas as pd
    end_dt = _dt.strptime(trade_date, '%Y%m%d')
    start_str = (end_dt - _td(days=int(lookback * 1.8))).strftime('%Y%m%d')

    result = {}
    if not _datahub_ready or get_data is None:
        print("  ❌ data_hub 未就绪，六大指数量能分位跳过")
        return result
    try:
        # get_data 直接返回数据本体：批量= {ts_code: DataFrame}，单只= DataFrame；失败返回 None
        # 内部已走 AKShare→Tushare(coze_gateway)→网页 三级降级
        batch = None
        try:
            batch = get_data('index_daily', ts_code_list=CORE_INDEX_TSCODES,
                             start_date=start_str, end_date=trade_date)
            if not isinstance(batch, dict):
                batch = None
        except Exception as e:
            print(f"  ⚠️ 批量指数日线采集异常，转逐个兜底: {e}")

        for ts_code in CORE_INDEX_TSCODES:
            idx_name = TSCODE_TO_INDEX.get(ts_code, ts_code)
            df = batch.get(ts_code) if isinstance(batch, dict) else None
            if df is None or not hasattr(df, 'columns') or getattr(df, 'empty', True):
                try:
                    df = get_data('index_daily', ts_code=ts_code,
                                  start_date=start_str, end_date=trade_date)
                except Exception as e:
                    print(f"  ⚠️ {idx_name} 指数日线三级降级采集异常: {e}")

            if df is None or not hasattr(df, 'columns') or getattr(df, 'empty', True):
                print(f"  ❌ {idx_name} 量能分位无数据（AKShare/Tushare/网页 均失败）")
                continue

            # 量能列：优先成交额 amount，缺失时降级成交量 vol（AKShare 指数日线仅有 vol）
            if 'amount' in df.columns:
                metric_col, metric_name = 'amount', 'amount'
            elif 'vol' in df.columns:
                metric_col, metric_name = 'vol', 'vol'
            else:
                print(f"  ❌ {idx_name} 日线无 amount/vol 列，量能分位跳过")
                continue

            d = df.copy()
            if 'trade_date' in d.columns:
                d['trade_date'] = d['trade_date'].astype(str)
                d = d[d['trade_date'] <= trade_date].sort_values('trade_date')
            d[metric_col] = pd.to_numeric(d[metric_col], errors='coerce')
            d = d[d[metric_col] > 0].dropna(subset=[metric_col])
            if len(d) < 30:
                print(f"  ⚠️ {idx_name} 有效量能样本不足({len(d)}日)，量能列留空")
                continue

            window = d.tail(lookback)
            cur = float(window[metric_col].iloc[-1])
            pct = round(float((window[metric_col] <= cur).sum()) / len(window) * 100, 1)
            base_n = min(20, len(window))
            ma = float(window[metric_col].tail(base_n).mean())
            result[idx_name] = {
                'percentile': pct,
                'ma20_ratio': round(cur / ma, 2) if ma > 0 else None,
                'metric': metric_name,
            }
            metric_cn = '成交额' if metric_name == 'amount' else '成交量'
            print(f"  ✅ {idx_name} {metric_cn}分位 {pct}%（样本{len(window)}日）")
    except Exception as e:
        print(f"  ❌ 多指数量能分位计算异常: {e}")

    return result


def _limit_threshold(ts_code):
    """个股涨跌幅限制阈值（%）：创业板/科创板/北交所 20%，其余 10%。用于判断涨停/跌停。"""
    c = str(ts_code)
    code = c.split('.')[0]
    if code[:3] in ('300', '301', '688', '689') or code[:2] in ('83', '87', '43', '92', '88'):
        return 19.5
    return 9.8


def score_breadth_ratio(ratio):
    """成分股 上涨家数/下跌家数 比 打分（情绪：普涨→风险低，普跌→风险高）"""
    if ratio is None:
        return 0, '⚪ 数据缺失'
    if ratio < 0.34:
        return 2, '🔴 普跌（恐慌）'
    elif ratio < 0.67:
        return 1, '🟠 跌多涨少'
    elif ratio <= 1.5:
        return 0, '⚪ 多空均衡'
    elif ratio <= 3.0:
        return -1, '🟢 涨多跌少'
    else:
        return -1, '🟢 普涨（情绪热）'


def score_component_concentration(pct):
    """指数成分股成交额集中度(top5占比)打分 —— 口径按宽基成分股分布标定
    （与全市场口径 score_concentration 不同：成分股top5占比天然更低）。
    >=45% 极度抱团, 30-45% 抱团偏强, 15-30% 中性, 8-15% 分散, <8% 极度分散
    """
    if pct is None:
        return 0, '⚪ 数据缺失'
    if pct >= 45:
        return 2, '🔴 极度抱团'
    elif pct >= 30:
        return 1, '🟠 抱团偏强'
    elif pct >= 15:
        return 0, '⚪ 中性'
    elif pct >= 8:
        return -1, '🟢 分散均衡'
    else:
        return -2, '🟢 极度分散'


def calc_core_index_breadth(trade_date):
    """六大宽基成分股情绪：成交额集中度(top5占比) + 涨跌家数比 —— data_hub 三级降级

    用一次全市场实时快照(stock_spot，收盘后即当日数据) join 各指数成分股(index_components)，
    为每个指数算两个量能情绪因子，补齐"模块二量能情绪风险表"原先只有万得全A有值的空格。

    Returns:
        {指数名: {'concentration': float, 'up': int, 'down': int, 'breadth_ratio': float}}
    """
    import pandas as pd
    result = {}
    if not _datahub_ready or get_data is None:
        print("  ❌ data_hub 未就绪，六大指数成分股情绪跳过")
        return result
    try:
        # 1) 全市场快照（含成交额 amount、涨跌幅 change_pct、ts_code）
        spot = None
        try:
            spot = get_data('stock_spot')
        except Exception as e:
            print(f"  ⚠️ 全市场快照采集异常: {e}")
        if spot is None or not hasattr(spot, 'columns') or 'amount' not in spot.columns or 'ts_code' not in spot.columns:
            print("  ❌ 全市场快照无数据（AKShare/Tushare/网页 均失败），成分股情绪跳过")
            return result

        sp = spot.copy()
        sp['amount'] = pd.to_numeric(sp['amount'], errors='coerce')
        sp['change_pct'] = pd.to_numeric(sp.get('change_pct'), errors='coerce')
        sp = sp.dropna(subset=['amount', 'change_pct'])
        if sp.empty:
            return result

        # 2) 逐指数取成分股并聚合
        for ts_code in CORE_INDEX_TSCODES:
            idx_name = TSCODE_TO_INDEX.get(ts_code, ts_code)
            comp = None
            try:
                comp = get_data('index_components', index_code=ts_code)
            except Exception as e:
                print(f"  ⚠️ {idx_name} 成分股获取异常: {e}")
            if not comp:
                continue
            sub = sp[sp['ts_code'].isin(comp)]
            if sub.empty:
                continue
            total_amt = sub['amount'].sum()
            top5_amt = sub.nlargest(5, 'amount')['amount'].sum()
            concentration = (top5_amt / total_amt * 100.0) if total_amt > 0 else None
            up = int((sub['change_pct'] > 0).sum())
            down = int((sub['change_pct'] < 0).sum())
            ratio = (up / down) if down > 0 else (float(up) if up > 0 else None)
            result[idx_name] = {
                'concentration': round(concentration, 1) if concentration is not None else None,
                'up': up, 'down': down,
                'breadth_ratio': round(ratio, 2) if ratio is not None else None,
            }
        if result:
            print(f"  ✅ 六大指数成分股情绪采集完成（{len(result)}/6）")

        # 3) 万得全A = 全市场口径：全市场上涨/下跌家数（与六宽基的成分股口径区分）
        all_up = int((sp['change_pct'] > 0).sum())
        all_down = int((sp['change_pct'] < 0).sum())
        all_ratio = round(all_up / all_down, 2) if all_down > 0 else (float(all_up) if all_up > 0 else None)
        if all_up or all_down:
            result['万得全A'] = {
                'concentration': None,  # 集中度沿用原全市场top5z口径，此处不覆盖
                'up': all_up, 'down': all_down,
                'breadth_ratio': all_ratio,
            }
            print(f"  ✅ 万得全A全市场涨跌家数: {all_up}涨/{all_down}跌 ratio={all_ratio}")
    except Exception as e:
        print(f"  ⚠️ 成分股情绪计算异常: {e}")
    return result


# ============================================================
#  午盘模式检测与实时数据处理
# ============================================================

def detect_midday_mode(trade_date):
    """检测是否需要启用午盘模式
    
    判断逻辑：
      1. 时间优先：交易日15:00前直接判定为午盘（防AKShare实时行情误判）
      2. 日线校验：15:00后检查当日日线数据是否已更新
    
    数据源优先级：
      1. data_hub 统一采集层（全市场日线）
      2. Tushare index_daily（沪深300）
    
    Args:
        trade_date: YYYYMMDD 格式
    
    Returns:
        bool: True=需要午盘模式，False=收盘模式
    """
    now = datetime.now()
    today_str = now.strftime('%Y%m%d')
    
    # Tier 0: 时间快速判断 — 交易日15:00前一律午盘模式
    # （AKShare全市场用的是实时行情接口，交易时间内随时有数据，会误判）
    if trade_date == today_str and now.hour < 15:
        print(f"  ⏰ 当前时间 {now.strftime('%H:%M')}，未到收盘，使用午盘模式")
        return True
    
    # Tier 1: data_hub 全市场日线检测
    if _datahub_ready:
        try:
            df = get_data("stock_daily", trade_date=trade_date)
            if df is not None and hasattr(df, '__len__') and len(df) > 100:
                # 额外校验：确认数据真的是当日日线（不是实时行情）
                if 'trade_date' in df.columns:
                    dates = df['trade_date'].unique()
                    # 标准化格式后检查
                    dates_norm = [str(d).replace('-', '') for d in dates]
                    if trade_date not in dates_norm and len(dates_norm) < 3:
                        print(f"  ⚠️  data_hub返回数据不含当日日期（实际: {dates_norm[:3]}），使用午盘模式")
                        return True
                print(f"  ✅ data_hub当日数据已更新（{len(df)}只），使用收盘模式")
                return False
            elif df is not None and hasattr(df, '__len__') and len(df) > 0:
                print(f"  ✅ data_hub当日数据已更新（{len(df)}只），使用收盘模式")
                return False
            else:
                print(f"  ⚠️  data_hub当日日线为空 ({trade_date})，自动启用午盘模式")
                return True
        except Exception as e:
            print(f"  ⚠️  data_hub检测失败: {e}，降级到Tushare检测")
    
    # Tier 2: Tushare 指数日线检测（原有逻辑）
    if not _tushare_ready:
        return False
    
    try:
        df = pro.index_daily(ts_code='000300.SH', start_date=trade_date, end_date=trade_date, fields='trade_date,close')
        if df is None or len(df) == 0:
            print(f"  ⚠️  Tushare当日指数日线为空 ({trade_date})，自动启用午盘模式")
            return True
        else:
            print(f"  ✅ Tushare当日数据已更新，使用收盘模式")
            return False
    except Exception as e:
        print(f"  ⚠️  检测Tushare数据失败: {e}，默认使用收盘模式")
        return False


def build_midday_signals(midday_data):
    """基于午盘实时数据构建风险信号
    
    Args:
        midday_data: fetch_all_midday_data() 返回的dict
    
    Returns:
        dict: 同 build_index_signals 的格式，各指数的风险信号
    """
    indices = {}
    
    for idx_name in ALL_INDICES:
        indices[idx_name] = {
            'structural_score': 0,
            'sentiment_score': 0,
            'extreme_score': 0,
            'structural_count': 0,
            'sentiment_count': 0,
            'extreme_count': 0,
            'factors': {},
            'is_midday': True,  # 标记为午盘数据
        }

    basis_data = midday_data.get('basis_data', [])
    market_breadth = midday_data.get('market_breadth', {})
    index_amplitude = midday_data.get('index_amplitude', {})

    # 注：午盘版"衍生品定价风险"维度（基差/SKEW/ATM IV/PCR/GEX 午盘估算）
    # 已于 2026-10-22 应用户要求整体移除。basis_data/skew_data/gex_data 不再参与打分。

    # ---------- 1. 结构风险 ----------
    # 注：午盘版已移除CFC因子（离散度公式与收盘版峰度+偏度公式口径不一致，
    # 数值量级差10倍以上，复用收盘分位数阈值会导致永远显示极度集中，无参考价值）
    
    # 结构因子午盘估算（AbsFactor / RelFactor）
    structural_data = midday_data.get('structural_data')
    if structural_data:
        for idx_name, sf_info in structural_data.items():
            if idx_name not in indices:
                continue
            idx = indices[idx_name]
            
            # AbsFactor
            abs_f = sf_info.get('abs_factor')
            if abs_f is not None:
                score, label = score_abs_factor(abs_f)
                idx['structural_score'] += score
                idx['structural_count'] += 1
                idx['factors']['AbsFactor'] = {
                    'value': f"{abs_f:+.2f}",
                    'score': score,
                    'label': label + ' ⚡',
                    'is_midday': True,
                    'note': sf_info.get('note', '午盘估算'),
                }
            
            # RelFactor
            rel_f = sf_info.get('rel_factor')
            if rel_f is not None:
                score, label = score_rel_factor(rel_f)
                idx['structural_score'] += score
                idx['structural_count'] += 1
                idx['factors']['RelFactor'] = {
                    'value': f"{rel_f:+.2f}",
                    'score': score,
                    'label': label + ' ⚡',
                    'is_midday': True,
                    'note': sf_info.get('note', '午盘估算'),
                }
    
    # up50 午盘估算
    up50_data = midday_data.get('up50_data')
    if up50_data:
        for idx_name, up50_info in up50_data.items():
            if idx_name not in indices:
                continue
            idx = indices[idx_name]
            up50_val = up50_info.get('up50_value')
            if up50_val is not None:
                score, label = score_up50(up50_val)
                idx['structural_score'] += score
                idx['structural_count'] += 1
                idx['factors']['up50'] = {
                    'value': f"{up50_val*100:.1f}% ⚡",
                    'score': score,
                    'label': label,
                    'is_midday': True,
                    'note': up50_info.get('note', '午盘估算'),
                }
    
    # top5z 午盘估算
    top5z_data = midday_data.get('top5z_data')
    if top5z_data:
        for idx_name, top5z_info in top5z_data.items():
            if idx_name not in indices:
                continue
            idx = indices[idx_name]
            top5z_val = top5z_info.get('top5z_value')
            if top5z_val is not None:
                score, label = score_top5z(top5z_val)
                idx['structural_score'] += score
                idx['structural_count'] += 1
                idx['factors']['top5z'] = {
                    'value': f"{top5z_val:.1f}% ⚡",
                    'score': score,
                    'label': label,
                    'is_midday': True,
                    'note': top5z_info.get('note', '午盘估算'),
                }
    
    # CDI 午盘估算（午盘模式暂无MA20数据，传None）
    cdi_data = midday_data.get('cdi_data')
    if cdi_data:
        for idx_name, cdi_info in cdi_data.items():
            if idx_name not in indices:
                continue
            idx = indices[idx_name]
            cdi_val = cdi_info.get('cdi_value')
            if cdi_val is not None:
                # 午盘模式MA20数据通常不可用，传None降级为原逻辑
                score, label = score_cdi(cdi_val, above_ma20=None)
                idx['structural_score'] += score
                idx['structural_count'] += 1
                idx['factors']['CDI'] = {
                    'value': f"{cdi_val:.2f} ⚡",
                    'score': score,
                    'label': label,
                    'is_midday': True,
                    'note': cdi_info.get('note', '午盘估算'),
                }
    
    # 剩余无午盘数据的结构因子，标注待更新
    pending_structural = ['up50', 'top5z', 'CDI']
    for idx_name in ALL_INDICES:
        idx = indices[idx_name]
        for factor_name in pending_structural:
            if factor_name not in idx['factors']:
                idx['factors'][factor_name] = {
                    'value': '—',
                    'score': 0,
                    'label': '⏳ 收盘后更新',
                    'is_pending': True,
                }
        # CFC 午盘版已移除，不再标注待更新；AbsFactor / RelFactor 无数据则标注待更新
        for factor_name in ['AbsFactor', 'RelFactor']:
            if factor_name not in idx['factors']:
                idx['factors'][factor_name] = {
                    'value': '—',
                    'score': 0,
                    'label': '⏳ 收盘后更新',
                    'is_pending': True,
                }
    
    # ---------- 2. 量能情绪风险 ----------
    # 万得全A的量能指标
    idx_wq = indices.get('万得全A')
    if idx_wq and market_breadth:
        # 成交额
        total_amount = market_breadth.get('total_amount')
        if total_amount is not None:
            # 午盘成交额（半日量），粗略估算全天：×2
            est_daily = total_amount * 2
            # 用估算值做分位判断（简化：只显示金额，不做历史分位）
            idx_wq['sentiment_score'] += 0  # 无量能分位，暂不打分
            idx_wq['sentiment_count'] += 1  # 计入数量表示有数据
            idx_wq['factors']['成交量分位'] = {
                'value': f"{total_amount:.2f}万亿(半日)",
                'score': 0,
                'label': f"预估全天≈{est_daily:.1f}万亿",
                'is_midday': True,
            }
        
        # TOP5%成交集中度
        top5z = market_breadth.get('top5z_concentration')
        if top5z is not None:
            score, label = score_top5z(top5z)
            idx_wq['sentiment_score'] += score
            idx_wq['sentiment_count'] += 1
            idx_wq['factors']['成交额集中度'] = {
                'value': f"{top5z:.1f}%",
                'score': score,
                'label': label,
                'is_midday': True,
            }
        
        # 涨跌停比
        up = market_breadth.get('up_limit', 0)
        down = market_breadth.get('down_limit', 0)
        if up or down:
            ratio = up / max(down, 1)
            if ratio < 0.5:
                score = 2
                label = '🔴 跌停远超涨停'
            elif ratio < 1:
                score = 1
                label = '🟠 跌多涨少'
            elif ratio < 2:
                score = 0
                label = '⚪ 中性'
            else:
                score = -1
                label = '🟢 涨多跌少'
            idx_wq['sentiment_score'] += score
            idx_wq['sentiment_count'] += 1
            idx_wq['factors']['涨跌停比'] = {
                'value': f"{up}/{down}",
                'score': score,
                'label': label,
                'is_midday': True,
            }
        
        # AR吸收率（涨/跌/平家数比）
        up_count = market_breadth.get('up_count', 0)
        down_count = market_breadth.get('down_count', 0)
        ar_ratio = market_breadth.get('ar_ratio', 0)
        if up_count and down_count:
            # AR打分：涨/跌比
            if ar_ratio >= 2:
                score = -1
                label = '🟢 普涨'
            elif ar_ratio >= 1:
                score = 0
                label = '⚪ 中性'
            elif ar_ratio >= 0.5:
                score = 1
                label = '🟠 普跌'
            else:
                score = 2
                label = '🔴 暴跌'
            idx_wq['sentiment_score'] += score
            idx_wq['sentiment_count'] += 1
            idx_wq['factors']['AR吸收比'] = {
                'value': f"{up_count}/{down_count}",
                'score': score,
                'label': label,
                'is_midday': True,
            }
        
        # VIX（日内振幅估算）
        vix_estimate = None
        # 优先用沪深300的振幅估算
        if '沪深300' in index_amplitude:
            vix_estimate = index_amplitude['沪深300'].get('vix_approx')
        
        if vix_estimate is None and market_breadth.get('vix_estimate'):
            vix_estimate = market_breadth.get('vix_estimate')
        
        if vix_estimate is not None:
            vix_pct = get_vix_percentile_approx(vix_estimate) if get_vix_percentile_approx else None
            score, label = score_vix_percentile(vix_pct)
            idx_wq['sentiment_score'] += score
            idx_wq['sentiment_count'] += 1
            idx_wq['factors']['VIX(估算)'] = {
                'value': f"{vix_estimate:.1f}%",
                'score': score,
                'label': label,
                'is_midday': True,
            }
    
    # ---------- 3. 极端风险（LPPL） ----------
    for idx_name in ALL_INDICES:
        idx = indices[idx_name]
        idx['factors']['LPPL泡沫'] = {
            'value': '—',
            'score': 0,
            'label': '⏳ 收盘后更新',
            'is_pending': True,
        }
    
    # ---------- 计算各指数总分和评级 ----------
    for idx_name, idx in indices.items():
        # 各维度平均分（归一化到count）
        dim_scores = {}
        for dim in ['structural', 'sentiment', 'extreme']:
            count_key = f'{dim}_count'
            score_key = f'{dim}_score'
            count = idx.get(count_key, 0)
            if count > 0:
                dim_scores[dim] = idx[score_key] / (count * 2.0)
            else:
                dim_scores[dim] = None  # 无数据
        
        idx['dim_scores'] = dim_scores
        
        # 综合分：加权平均（只计算有数据的维度）
        # 移除衍生品后，三维度按原比例重归一
        weights = {
            'structural': 0.40,
            'sentiment': 0.333,
            'extreme': 0.267,
        }
        total_weight = 0
        weighted_sum = 0
        for dim, w in weights.items():
            if dim_scores.get(dim) is not None:
                weighted_sum += dim_scores[dim] * w
                total_weight += w
        
        if total_weight > 0:
            idx['total_score'] = weighted_sum / total_weight
        else:
            idx['total_score'] = 0
        
        # 评级
        ts = idx['total_score']
        if ts >= 0.6:
            idx['rating'] = '🔴 高风险'
            idx['rating_level'] = 4
        elif ts >= 0.3:
            idx['rating'] = '🟠 偏高风险'
            idx['rating_level'] = 3
        elif ts >= -0.2:
            idx['rating'] = '🟡 中性'
            idx['rating_level'] = 2
        elif ts >= -0.5:
            idx['rating'] = '🟢 低风险'
            idx['rating_level'] = 1
        else:
            idx['rating'] = '✅ 极低风险'
            idx['rating_level'] = 0
    
    return indices


# ============================================================
#  信号计算层：纯Python规则，每个因子独立打分
#  分数范围：-2（极度看多/低风险）到 +2（极度看空/高风险）
#  注意：分数为正表示风险偏高，分数为负表示风险偏低
# ============================================================

def score_skew(cboe_skew):
    """SKEW 打分（已废弃，不再参与综合评分）
    CBOE SKEW 通常范围 100-150
    >130 极端尾部风险（+2），120-130 偏高（+1），110-120 中性（0），<110 偏低（-1）
    
    注意：此函数保留仅供展示参考，不参与综合风险评分计算。
    SKEW在中国A股期权市场缺乏CBOE那样的成熟定价基础，经验映射公式不够可靠。
    已由 ATM IV 和 PCR 评分替代。
    """
    if cboe_skew is None:
        return 0, '⚪ 数据缺失'
    if cboe_skew >= 135:
        return 2, '🔴 极端尾部风险'
    elif cboe_skew >= 125:
        return 1, '🟠 尾部风险偏高'
    elif cboe_skew >= 115:
        return 0, '⚪ 中性'
    elif cboe_skew >= 105:
        return -1, '🟢 尾部风险低'
    else:
        return -2, '🟢 极低尾部风险'


def score_atm_iv(atm_iv_pct, index_name='', pcr=None):
    """ATM IV水平评分（隐含波动率绝对水平，结合PCR判断方向）
    
    根据指数类型设不同阈值：
    - 高波动指数（科创50/1000MO）: >60%→🔴, >45%→🟠, 30-45%→⚪, <30%→🟢
    - 中波动指数（500ETF/创业板）: >45%→🔴, >35%→🟠, 22-35%→⚪, <22%→🟢
    - 低波动指数（50ETF/300ETF等）: >35%→🔴, >28%→🟠, 18-28%→⚪, <18%→🟢
    
    结合PCR方向判断（pcr不为None时启用）：
    - IV高 + PCR >= 1.2 → 🔴 恐慌（买认沽避险）
    - IV高 + PCR < 0.8 → 🟠 过热（买认购追涨）
    - IV高 + 0.8≤PCR≤1.2 → 🟠 波动偏高（方向不明）
    - IV中高区间同样按PCR方向细分
    
    Args:
        atm_iv_pct: ATM隐含波动率（百分比形式，如27.77表示27.77%）
        index_name: 指数/期权名称
        pcr: 成交额PCR（可选，None时沿用旧逻辑仅按IV绝对值判断）
    """
    if atm_iv_pct is None:
        return 0, '⚪ 数据缺失'
    
    # 按指数类型分阈值
    high_vol_indices = ['科创50', '1000MO', '中证1000']
    mid_vol_indices = ['500ETF', '创业板', '中证500']
    
    if any(k in index_name for k in high_vol_indices):
        # (threshold, base_score, panic_label, overheat_label, neutral_label)
        thresholds = [
            (60, 2, '🔴 恐慌', '🟠 过热', '🟠 波动偏高'),
            (45, 1, '🟠 恐慌', '🟠 过热', '🟠 波动偏高'),
            (30, 0, '⚪ 中性', '⚪ 中性', '⚪ 中性'),
            (None, -1, '🟢 低波动', '🟢 低波动', '🟢 低波动'),
        ]
    elif any(k in index_name for k in mid_vol_indices):
        thresholds = [
            (45, 2, '🔴 恐慌', '🟠 过热', '🟠 波动偏高'),
            (35, 1, '🟠 恐慌', '🟠 过热', '🟠 波动偏高'),
            (22, 0, '⚪ 中性', '⚪ 中性', '⚪ 中性'),
            (None, -1, '🟢 低波动', '🟢 低波动', '🟢 低波动'),
        ]
    else:
        thresholds = [
            (35, 2, '🔴 恐慌', '🟠 过热', '🟠 波动偏高'),
            (28, 1, '🟠 恐慌', '🟠 过热', '🟠 波动偏高'),
            (18, 0, '⚪ 中性', '⚪ 中性', '⚪ 中性'),
            (None, -1, '🟢 低波动', '🟢 低波动', '🟢 低波动'),
        ]
    
    for threshold, score, panic_label, overheat_label, mid_label in thresholds:
        if threshold is None or atm_iv_pct >= threshold:
            # 如果没有PCR数据或score<=0（低/中风险），直接返回基础标签
            if pcr is None or score <= 0:
                # score=2且无PCR时，显示旧版"极端恐慌"保持兼容
                if score == 2 and pcr is None:
                    return score, '🔴 极端恐慌'
                if score == 1 and pcr is None:
                    return score, mid_label
                return score, panic_label if score > 0 else panic_label
            # 有PCR数据且score>0（高/中高风险），结合方向判断
            if pcr >= 1.2:
                return score, panic_label   # 恐慌（买认沽避险）
            elif pcr < 0.8:
                return score, overheat_label  # 过热（买认购追涨）
            else:
                return score, mid_label     # 方向不明
    return -1, '🟢 低波动'


def score_pcr(vol_pcr):
    """PCR（成交量认沽/认购比）评分：衡量市场恐慌程度
    
    统一口径：全系统PCR均使用成交量口径（vol_pcr），不再区分amt_pcr。
    
    vol_pcr >= 1.5 → 🔴 极度恐慌（+2）
    vol_pcr >= 1.2 → 🟠 恐慌（+1）
    0.7~1.2 → ⚪ 中性（0）
    < 0.7 → 🟢 乐观（-1）
    
    Args:
        vol_pcr: 成交量认沽/认购比
    """
    if vol_pcr is None:
        return 0, '⚪ 数据缺失'
    if vol_pcr >= 1.5:
        return 2, '🔴 极度恐慌'
    elif vol_pcr >= 1.2:
        return 1, '🟠 恐慌'
    elif vol_pcr >= 0.7:
        return 0, '⚪ 中性'
    else:
        return -1, '🟢 乐观'


def score_gex(total_gex_yi, risk_label=None):
    """GEX 打分
    优先使用原始数据的risk_label判断（适配ETF和股指期权不同量级）
    GEX为正且大→做市商对冲稳定市场（低风险，负分）
    GEX接近0或负→做市商可能加剧波动（高风险，正分）
    """
    # 优先使用原始风险标签
    if risk_label:
        if '低' in risk_label:
            # 确保有emoji前缀
            if '🟢' not in risk_label and '🟡' not in risk_label and '🔴' not in risk_label and '🟠' not in risk_label:
                risk_label = f'🟢{risk_label}'
            return -1, risk_label
        elif '中' in risk_label or '中性' in risk_label:
            if '🟢' not in risk_label and '🟡' not in risk_label and '🔴' not in risk_label and '🟠' not in risk_label:
                risk_label = f'🟡{risk_label}'
            return 0, risk_label
        elif '高' in risk_label:
            if '🟢' not in risk_label and '🟡' not in risk_label and '🔴' not in risk_label and '🟠' not in risk_label:
                risk_label = f'🔴{risk_label}'
            return 2, risk_label
    
    # 备用：绝对值判断（仅适用于ETF期权亿级量级）
    if total_gex_yi is None:
        return 0, '⚪ 数据缺失'
    if total_gex_yi >= 10:
        return -2, '🟢 强Gamma支撑'
    elif total_gex_yi >= 5:
        return -1, '🟢 Gamma支撑'
    elif total_gex_yi >= 0:
        return 0, '⚪ 中性'
    elif total_gex_yi >= -5:
        return 1, '🟠 Gamma压制'
    else:
        return 2, '🔴 强Gamma压制'


def score_basis(basis_rate, rank_252):
    """基差打分
    期货深度贴水（年化负基差大）→ 市场看空情绪浓（+1）
    期货升水 → 市场看多（-1）
    年化基差率约 -10% 为常见水平
    """
    if basis_rate is None:
        return 0, '⚪ 数据缺失'
    # 用rank更可靠
    if rank_252 is not None:
        if rank_252 <= 0.1:
            return 1, '🟠 贴水偏深'
        elif rank_252 >= 0.9:
            return -1, '🟢 贴水偏浅/升水'
        else:
            return 0, '⚪ 中性'
    # 备用：用绝对值
    if basis_rate <= -15:
        return 1, '🟠 深度贴水'
    elif basis_rate >= 0:
        return -1, '🟢 升水'
    else:
        return 0, '⚪ 中性'


def score_vix_percentile(pct):
    """VIX分位数打分
    >80% 恐慌期（+2高风险），60-80% 偏高（+1），20-60% 中性（0），<20% 低波（-1）
    """
    if pct is None:
        return 0, '⚪ 数据缺失'
    if pct >= 90:
        return 2, '🔴 极度恐慌'
    elif pct >= 75:
        return 1, '🟠 波动率偏高'
    elif pct >= 25:
        return 0, '⚪ 中性'
    elif pct >= 10:
        return -1, '🟢 低波动'
    else:
        return -2, '🟢 极低波动（警惕见顶）'


def _load_cfc_history_all():
    """加载CFC历史数据（全部标的池、全部日期），用于分位数计算
    
    Returns:
        dict: {pool_name: {trade_date: {cfc, up50, top5z, ...}}}
        or None if file not found
    """
    path = os.path.join(ROOT_DIR, 'CFC结构因子', 'historical_factors.json')
    return _safe_load_json(path)


def _calc_cfc_percentile(cfc, pool_name, cfc_history, lookback_days=252):
    """计算当前CFC值在近1年历史数据中的百分位数
    
    Args:
        cfc: 当前CFC值
        pool_name: CFC标的池名称（如 '沪深300'）
        cfc_history: 完整历史数据dict {pool_name: {date: {cfc, ...}}}
        lookback_days: 回看天数（默认252≈1年交易日）
    
    Returns:
        float: 百分位数 0~1，或None如果数据不足
    """
    if not cfc_history or pool_name not in cfc_history:
        return None
    
    pool_data = cfc_history[pool_name]
    if not pool_data or len(pool_data) < 20:
        return None
    
    # 收集历史CFC值（排除None）
    hist_cfc_values = []
    for date_str, day_data in pool_data.items():
        if isinstance(day_data, dict):
            v = day_data.get('cfc')
            if v is not None:
                hist_cfc_values.append(v)
    
    if len(hist_cfc_values) < 20:
        return None
    
    # 按日期排序，取最近lookback_days个
    sorted_dates = sorted(pool_data.keys())
    recent_dates = sorted_dates[-lookback_days:]
    recent_cfc_values = []
    for d in recent_dates:
        day_data = pool_data.get(d, {})
        if isinstance(day_data, dict):
            v = day_data.get('cfc')
            if v is not None:
                recent_cfc_values.append(v)
    
    if len(recent_cfc_values) < 20:
        return None
    
    # 计算百分位数
    count_below = sum(1 for v in recent_cfc_values if v < cfc)
    percentile = count_below / len(recent_cfc_values)
    return percentile


def score_cfc(cfc, index_name=None, cfc_history=None):
    """CFC打分（基于历史分位数）
    
    使用各指数自身近1年CFC的历史分位数代替固定阈值，
    避免大盘指数因结构性高集中度而长期误报。
    
    评分规则（分位数）：
      >80% → 🔴 极度集中（+2）
      >60% → 🟠 集中度偏高（+1）
      40-60% → ⚪ 中性（0）
      20-40% → 🟢 集中度偏低（-1）
      <20% → 🟢 极度分散（-2）
    
    如果历史数据不足20天，降级用固定阈值（放宽：>=2.0→🔴, >=1.5→🟠）
    
    Args:
        cfc: 当前CFC值
        index_name: 指数名称（用于反向映射CFC池名）
        cfc_history: 预加载的历史数据dict（可选，若None则自动加载）
    """
    if cfc is None:
        return 0, '⚪ 数据缺失'
    
    # 处理NaN值
    try:
        import math
        if isinstance(cfc, float) and math.isnan(cfc):
            return 0, '⚪ 数据缺失'
    except Exception:
        pass
    # 反向映射：指数名称 → CFC池名
    idx_to_cfc_pool = {v: k for k, v in CFC_TO_INDEX.items()}
    pool_name = idx_to_cfc_pool.get(index_name, index_name)
    
    # 尝试计算历史分位数
    percentile = None
    if cfc_history is None:
        cfc_history = _load_cfc_history_all()
    
    if cfc_history and pool_name:
        percentile = _calc_cfc_percentile(cfc, pool_name, cfc_history)
    
    if percentile is not None:
        # 基于分位数评分
        pct_str = f"{percentile*100:.0f}%"
        if percentile >= 0.80:
            return 2, f'🔴 极度集中（{pct_str}分位）'
        elif percentile >= 0.60:
            return 1, f'🟠 集中度偏高（{pct_str}分位）'
        elif percentile >= 0.40:
            return 0, f'⚪ 中性（{pct_str}分位）'
        elif percentile >= 0.20:
            return -1, f'🟢 集中度偏低（{pct_str}分位）'
        else:
            return -2, f'🟢 极度分散（{pct_str}分位）'
    
    # 降级：历史数据不足，用放宽的固定阈值
    if cfc >= 2.0:
        return 2, '🔴 极度集中（固定阈值）'
    elif cfc >= 1.5:
        return 1, '🟠 集中度偏高（固定阈值）'
    elif cfc >= -1.5:
        return 0, '⚪ 中性（固定阈值）'
    else:
        return -1, '🟢 集中度偏低（固定阈值）'


def score_up50(up50):
    """up50打分：股价在50日均线上方的股票占比
    >0.75 极热（-1低风险但警惕过热），<0.2 极冷（+2高风险/恐慌）
    """
    if up50 is None:
        return 0, '⚪ 数据缺失'
    if up50 >= 0.80:
        return -1, '🟢 全面多头（警惕过热）'
    elif up50 >= 0.60:
        return 0, '⚪ 中性偏多'
    elif up50 >= 0.40:
        return 0, '⚪ 中性'
    elif up50 >= 0.20:
        return 1, '🟠 偏空'
    else:
        return 2, '🔴 极度超卖（恐慌）'


def score_top5z(top5z):
    """top5z打分：前5%股票成交额占比
    >50% 极度抱团→风险高（+2），30-50% 中性（0），<20% 分散（-1）
    """
    if top5z is None:
        return 0, '⚪ 数据缺失'
    if top5z >= 50:
        return 2, '🔴 极度抱团'
    elif top5z >= 40:
        return 1, '🟠 抱团偏强'
    elif top5z >= 25:
        return 0, '⚪ 中性'
    elif top5z >= 15:
        return -1, '🟢 分散均衡'
    else:
        return -2, '🟢 极度分散'


def score_cdi(cdi, above_ma20=None):
    """CDI峰度打分（基于峰度的微观结构指数，结合MA20趋势上下文）
    
    风洞版CDI逻辑（国泰君安资金扩散研报）：
      CDI = 加仓频率分布的峰度（Fisher excess kurtosis）
      CDI > 3  → 结构健康（资金集中加仓，扩散良好）
      CDI <= 3 → 微观结构恶化（资金分散，抱团松动）
    
    结合MA20趋势上下文打分：
      CDI > 3  + 指数在MA20上方 → 健康上行 → 机会🟢(-1)
      CDI > 3  + 指数在MA20下方 → 健康反弹 → 中性⚪(0)
      CDI > 3  + 趋势未知      → 结构健康 → 机会🟢(-1)
      CDI <= 3 + 指数在MA20上方 → 恶化回落 → 风险🟠(+1)
      CDI <= 3 + 指数在MA20下方 → 恶化下跌 → 风险🔴(+2)
      CDI <= 3 + 趋势未知      → 结构恶化 → 风险🟠(+1)
    
    Args:
        cdi: CDI峰度值（峰度）
        above_ma20: 指数是否在MA20上方（True/False/None=未知）
    """
    if cdi is None:
        return 0, '⚪ 数据缺失'
    
    # 处理NaN值
    try:
        import math
        if isinstance(cdi, float) and math.isnan(cdi):
            return 0, '⚪ 数据缺失'
    except Exception:
        pass
    
    # 标准化为Python原生bool（兼容numpy bool）
    if above_ma20 is not None:
        above_ma20 = bool(above_ma20)
    
    if cdi > 3:
        # 结构健康（CDI峰度 > 3）
        if above_ma20 is True:
            return -1, '🟢 结构健康（上行）'
        elif above_ma20 is False:
            return 0, '⚪ 结构健康（反弹）'
        else:
            return -1, '🟢 结构健康'
    else:
        # 微观结构恶化（CDI峰度 <= 3）
        if above_ma20 is True:
            return 1, '🟠 结构恶化（回落）'
        elif above_ma20 is False:
            return 2, '🔴 结构恶化（下跌）'
        else:
            return 1, '🟠 结构恶化'


def score_abs_factor(abs_factor):
    """AbsFactor打分
    >2 极端多头（-2），>1 多头（-1），-1~1 震荡（0），<-1 空头（+1），<-2 极端空头（+2）
    """
    if abs_factor is None:
        return 0, '⚪ 数据缺失'
    if abs_factor >= 2:
        return -2, '🟢 极端多头'
    elif abs_factor >= 1:
        return -1, '🟢 多头趋势'
    elif abs_factor >= -1:
        return 0, '⚪ 震荡'
    elif abs_factor >= -2:
        return 1, '🟠 空头趋势'
    else:
        return 2, '🔴 极端空头'


def score_rel_factor(rel_factor):
    """RelFactor打分
    正值=资金流出（+风险），负值=资金流入（-风险）
    >2 极端流出（+2），>1 流出（+1），-1~1 中性（0），<-1 流入（-1），<-2 极端流入（-2）
    """
    if rel_factor is None:
        return 0, '⚪ 数据缺失'
    if rel_factor >= 2:
        return 2, '🔴 资金极端流出'
    elif rel_factor >= 1:
        return 1, '🟠 资金流出'
    elif rel_factor >= -1:
        return 0, '⚪ 中性'
    elif rel_factor >= -2:
        return -1, '🟢 资金流入'
    else:
        return -2, '🟢 资金极端流入'


def score_volume_percentile(pct):
    """成交量分位打分
    >90% 天量（+2见顶风险），80-90% 放量（+1），20-80% 中性（0），<20% 缩量（+1底部信号/高风险）
    """
    if pct is None:
        return 0, '⚪ 数据缺失'
    if pct >= 95:
        return 2, '🔴 天量（见顶风险）'
    elif pct >= 80:
        return 1, '🟠 放量过热'
    elif pct >= 50:
        return 0, '⚪ 正常偏活跃'
    elif pct >= 20:
        return 0, '⚪ 正常偏低'
    else:
        return 1, '🟠 缩量（流动性差）'


def score_concentration(pct):
    """成交额集中度打分
    >45% 极度抱团（+2），35-45% 偏高（+1），25-35% 中性（0），<25% 分散（-1）
    """
    if pct is None:
        return 0, '⚪ 数据缺失'
    if pct >= 50:
        return 2, '🔴 极度抱团'
    elif pct >= 40:
        return 1, '🟠 抱团偏强'
    elif pct >= 30:
        return 0, '⚪ 中性'
    elif pct >= 20:
        return -1, '🟢 分散均衡'
    else:
        return -2, '🟢 极度分散'


def score_hindenburg(triggered):
    """兴登堡指标打分
    触发=+2，未触发=0
    """
    if triggered is None:
        return 0, '⚪ 数据缺失'
    if triggered:
        return 2, '🔴 兴登堡信号触发'
    else:
        return 0, '🟢 未触发'


def score_ar_dar(dar_long):
    """吸收率因子DAR打分
    DAR > 0.5 风险集中（+1），DAR > 1.0 极度集中（+2）
    DAR < -0.5 风险分散（-1），DAR < -1.0 极度分散（-2）
    """
    if dar_long is None:
        return 0, '⚪ 数据缺失'
    if dar_long >= 1.0:
        return 2, '🔴 风险极度集中'
    elif dar_long >= 0.5:
        return 1, '🟠 风险集中'
    elif dar_long >= -0.5:
        return 0, '⚪ 中性'
    elif dar_long >= -1.0:
        return -1, '🟢 风险分散'
    else:
        return -2, '🟢 风险极度分散'


def score_lppl(results):
    """LPPL泡沫检测打分
    多窗口中高质量拟合（r2>0.8）且为看涨泡沫，距离临界日越近风险越高
    """
    if not results or not isinstance(results, list):
        return 0, '⚪ 数据缺失'
    
    high_quality = [r for r in results if r.get('quality', {}).get('r_squared', 0) >= 0.8]
    if not high_quality:
        return 0, '🟢 无显著泡沫信号'
    
    # 检查是否有看涨泡沫
    bull_bubbles = []
    for r in high_quality:
        interp = r.get('interpretation', {})
        bubble_type = interp.get('bubble_type', '')
        days = interp.get('days_to_critical', 999)
        if '看涨' in bubble_type and days is not None and days > 0:
            bull_bubbles.append(days)
    
    if not bull_bubbles:
        return 0, '🟢 无看涨泡沫'
    
    min_days = min(bull_bubbles)
    if min_days <= 14:
        return 2, '🔴 LPPL见顶预警（≤2周）'
    elif min_days <= 30:
        return 1, '🟠 LPPL泡沫信号（≤1月）'
    elif min_days <= 60:
        return 0, '🟡 LPPL远端泡沫（≤2月）'
    else:
        return -1, '🟢 LPPL泡沫尚远'


# ============================================================
#  汇总层：按维度和指数汇总
# ============================================================

# 指数名称 → tushare指数代码映射（用于MA20计算）
INDEX_TS_CODES = {
    '上证50': '000016.SH',
    '沪深300': '000300.SH',
    '中证500': '000905.SH',
    '中证1000': '000852.SH',
    '创业板指': '399006.SZ',
    '科创50': '000688.SH',
    '万得全A': '881001.WI',
}


def _compute_ma20_status(trade_date):
    """计算各指数是否在MA20上方
    
    优先使用data_hub获取日线数据，降级使用tushare。
    
    Args:
        trade_date: YYYYMMDD格式
    
    Returns:
        dict: {index_name: True/False}，True=在MA20上方
    """
    result = {}
    
    # 优先用 data_hub
    if _datahub_ready and get_data:
        try:
            daily_data = get_data("index_daily", trade_date=trade_date, lookback=30)
            if daily_data and isinstance(daily_data, dict):
                for idx_name, ts_code in INDEX_TS_CODES.items():
                    df = daily_data.get(ts_code)
                    if df is not None and hasattr(df, '__len__') and len(df) >= 20:
                        import pandas as pd
                        if isinstance(df, pd.DataFrame) and 'close' in df.columns:
                            closes = df['close'].dropna().values
                            if len(closes) >= 20:
                                ma20 = closes[-20:].mean()
                                result[idx_name] = bool(closes[-1] > ma20)
        except Exception as e:
            print(f"  [调试] data_hub MA20计算失败: {e}")
    
    # 降级：用tushare
    if not result and _tushare_ready:
        try:
            import time as _time
            start_dt = datetime.strptime(trade_date, '%Y%m%d')
            from datetime import timedelta as _td
            start_date = (start_dt - _td(days=45)).strftime('%Y%m%d')
            
            for idx_name, ts_code in INDEX_TS_CODES.items():
                try:
                    _time.sleep(0.3)
                    df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=trade_date)
                    if df is not None and not df.empty and len(df) >= 20:
                        closes = df.sort_values('trade_date')['close'].values
                        ma20 = closes[-20:].mean()
                        result[idx_name] = bool(closes[-1] > ma20)
                except Exception:
                    pass
        except Exception as e:
            print(f"  [调试] tushare MA20计算失败: {e}")
    
    return result


def build_index_signals(option_data, cfc_data, sf_data, market_data, vol_pct_data, lppl_data, trade_date=None, core_amount_pct=None, core_breadth=None):
    """构建各指数的风险信号

    维度（2026-10-22 起移除"衍生品定价风险"，三维度评分）:
      结构风险 / 量能情绪风险 / 极端风险

    返回: dict {index_name: {
        dimension_scores: {structural: score, sentiment: score, extreme: score},
        factor_details: {...各因子详细信息...},
        total_score: 加权总分,
        rating: 评级
    }}
    """
    indices = {}

    for idx_name in ALL_INDICES:
        indices[idx_name] = {
            'structural_score': 0,     # 结构风险
            'sentiment_score': 0,      # 量能情绪风险
            'extreme_score': 0,        # 极端风险
            'structural_count': 0,
            'sentiment_count': 0,
            'extreme_count': 0,
            'factors': {},   # 各因子详情
        }

    # 预加载CFC历史数据（用于分位数计算）
    cfc_history = _load_cfc_history_all()

    # 计算各指数MA20状态（用于CDI趋势上下文）
    ma20_status = {}
    if trade_date:
        ma20_status = _compute_ma20_status(trade_date)

    # 注：原"衍生品定价风险"维度已于 2026-10-22 应用户要求移除
    # （万得全A无期权/期货合约，该维度综合评级长期 N/A）。
    # 底层期权数据仍由三维择时等独立模块使用，本函数不再聚合 PCR/IV/GEX/基差。

    # ---------- 1. 结构风险 ----------
    # CFC因子
    if cfc_data:
        for cfc_name, idx_name in CFC_TO_INDEX.items():
            if idx_name not in indices:
                continue
            if cfc_name not in cfc_data:
                continue
            
            idx = indices[idx_name]
            f = cfc_data[cfc_name]
            
            # CFC（使用历史分位数评分）
            cfc_val = f.get('cfc')
            score, label = score_cfc(cfc_val, index_name=idx_name, cfc_history=cfc_history)
            idx['structural_score'] += score
            idx['structural_count'] += 1
            idx['factors']['CFC'] = {
                'value': f"{cfc_val:.3f}" if cfc_val is not None else 'N/A',
                'score': score,
                'label': label,
            }
            
            # up50
            up50_val = f.get('up50')
            score, label = score_up50(up50_val)
            idx['structural_score'] += score
            idx['structural_count'] += 1
            idx['factors']['up50'] = {
                'value': f"{up50_val*100:.1f}%" if up50_val is not None else 'N/A',
                'score': score,
                'label': label,
            }
            
            # top5z
            top5z_val = f.get('top5z')
            score, label = score_top5z(top5z_val)
            idx['structural_score'] += score
            idx['structural_count'] += 1
            idx['factors']['top5z'] = {
                'value': f"{top5z_val:.1f}%" if top5z_val is not None else 'N/A',
                'score': score,
                'label': label,
            }
            
            # CDI（结合MA20趋势上下文）
            cdi_val = f.get('cdi')
            above_ma20 = ma20_status.get(idx_name)
            score, label = score_cdi(cdi_val, above_ma20=above_ma20)
            idx['structural_score'] += score
            idx['structural_count'] += 1
            idx['factors']['CDI'] = {
                'value': f"{cdi_val:.2f}" if cdi_val is not None else 'N/A',
                'score': score,
                'label': label,
            }

    # 注：原结构因子 AbsFactor/RelFactor 评分段已移除（与 CFC 同属结构维度且覆盖冗余）；
    # 结构风险维度由 CFC / CDI 承接。sf_data 不再参与本函数打分。

    # ---------- 2. 量能情绪风险 ----------
    # 成交量分位（全市场/万得全A）
    if vol_pct_data:
        idx = indices.get('万得全A')
        if idx:
            pct = vol_pct_data.get('percentile')
            score, label = score_volume_percentile(pct)
            idx['sentiment_score'] += score
            idx['sentiment_count'] += 1
            idx['factors']['成交量分位'] = {
                'value': f"{pct:.1f}%" if pct is not None else 'N/A',
                'score': score,
                'label': label,
            }

    # 六大宽基指数成交额分位（data_hub 三级降级采集，补齐对比表量能列）
    if core_amount_pct:
        for idx_name, ad in core_amount_pct.items():
            idx = indices.get(idx_name)
            if not idx:
                continue
            pct = ad.get('percentile')
            score, label = score_volume_percentile(pct)
            idx['sentiment_score'] += score
            idx['sentiment_count'] += 1
            factor_name = '成交量分位' if ad.get('metric') == 'vol' else '成交额分位'
            idx['factors'][factor_name] = {
                'value': f"{pct:.1f}%" if pct is not None else 'N/A',
                'score': score,
                'label': label,
            }

    # 六大宽基成分股情绪（成交额集中度top5 + 涨跌家数比），补齐量能情绪表空格
    if core_breadth:
        for idx_name, bd in core_breadth.items():
            idx = indices.get(idx_name)
            if not idx:
                continue
            conc = bd.get('concentration')
            if conc is not None:
                score, label = score_component_concentration(conc)
                idx['sentiment_score'] += score
                idx['sentiment_count'] += 1
                idx['factors']['成交额集中度'] = {
                    'value': f"{conc:.1f}%",
                    'score': score,
                    'label': label,
                }
            ratio = bd.get('breadth_ratio')
            up = bd.get('up', 0)
            down = bd.get('down', 0)
            if ratio is not None and (up > 0 or down > 0):
                score, label = score_breadth_ratio(ratio)
                idx['sentiment_score'] += score
                idx['sentiment_count'] += 1
                idx['factors']['涨跌家数比'] = {
                    'value': f"{up}/{down}",
                    'score': score,
                    'label': label,
                }

    # 市场监控数据（集中度、涨跌停等）
    if market_data:
        idx = indices.get('万得全A')
        if idx:
            conc = market_data.get('concentration')
            if conc is not None:
                score, label = score_concentration(conc)
                idx['sentiment_score'] += score
                idx['sentiment_count'] += 1
                idx['factors']['成交额集中度'] = {
                    'value': f"{conc:.1f}%",
                    'score': score,
                    'label': label,
                }
            
            # 涨跌停比作为情绪指标（简化：跌停多=风险高）
            up = market_data.get('up_limit', 0)
            down = market_data.get('down_limit', 0)
            if up and down:
                ratio = up / max(down, 1)
                if ratio < 0.5:
                    score = 2
                    label = '🔴 跌停远超涨停'
                elif ratio < 1:
                    score = 1
                    label = '🟠 跌多涨少'
                elif ratio < 2:
                    score = 0
                    label = '⚪ 中性'
                else:
                    score = -1
                    label = '🟢 涨多跌少'
                idx['sentiment_score'] += score
                idx['sentiment_count'] += 1
                idx['factors']['全市场涨跌停比'] = {
                    'value': f"{up}/{down}",
                    'score': score,
                    'label': label,
                }
    
    # ---------- 3. 极端风险（LPPL） ----------
    if lppl_data:
        # LPPL目前只有上证指数，映射到万得全A
        idx = indices.get('万得全A')
        if idx:
            score, label = score_lppl(lppl_data)
            idx['extreme_score'] += score
            idx['extreme_count'] += 1
            idx['factors']['LPPL泡沫'] = {
                'value': label,
                'score': score,
                'label': label,
            }
    
    # ---------- 计算各指数总分和评级 ----------
    for idx_name, idx in indices.items():
        # 各维度平均分（归一化到count）
        dim_scores = {}
        for dim in ['structural', 'sentiment', 'extreme']:
            count_key = f'{dim}_count'
            score_key = f'{dim}_score'
            count = idx.get(count_key, 0)
            if count > 0:
                # 每个因子最大±2分，归一化到 -1 ~ +1 区间表示该维度风险程度
                dim_scores[dim] = idx[score_key] / (count * 2.0)
            else:
                dim_scores[dim] = None
        
        idx['dim_scores'] = dim_scores  # 归一化后各维度分（-1~+1）
        
        # 综合分：加权平均（只计算有数据的维度）
        # 移除衍生品后，三维度按原 结构0.30/情绪0.25/极端0.20 比例重归一
        weights = {
            'structural': 0.40,
            'sentiment': 0.333,
            'extreme': 0.267,
        }
        total_weight = 0
        weighted_sum = 0
        for dim, w in weights.items():
            if dim_scores.get(dim) is not None:
                weighted_sum += dim_scores[dim] * w
                total_weight += w
        
        if total_weight > 0:
            idx['total_score'] = weighted_sum / total_weight  # -1 ~ +1
        else:
            idx['total_score'] = 0
        
        # 评级
        ts = idx['total_score']
        if ts >= 0.6:
            idx['rating'] = '🔴 高风险'
            idx['rating_level'] = 4
        elif ts >= 0.3:
            idx['rating'] = '🟠 偏高风险'
            idx['rating_level'] = 3
        elif ts >= -0.2:
            idx['rating'] = '🟡 中性'
            idx['rating_level'] = 2
        elif ts >= -0.5:
            idx['rating'] = '🟢 低风险'
            idx['rating_level'] = 1
        else:
            idx['rating'] = '✅ 极低风险'
            idx['rating_level'] = 0
    
    return indices


def collect_risk_signals(indices):
    """收集所有风险信号，用于TOP3排序
    
    返回: list of dict {index, factor, value, label, score, dimension}
    按风险分数从高到低排序
    """
    all_signals = []
    
    dim_names = {
        'structural': '结构风险',
        'sentiment': '量能情绪风险',
        'extreme': '极端风险',
    }
    
    for idx_name, idx_data in indices.items():
        for factor_name, factor_info in idx_data.get('factors', {}).items():
            score = factor_info.get('score', 0)
            if score > 0:  # 只收集风险信号（正分=风险）
                # 判断所属维度（衍生品维度已移除）
                dim = 'sentiment'  # 默认
                if factor_name in ['CFC', 'up50', 'top5z', 'CDI', 'AbsFactor', 'RelFactor']:
                    dim = 'structural'
                elif factor_name in ['LPPL泡沫']:
                    dim = 'extreme'
                
                all_signals.append({
                    'index': idx_name,
                    'factor': factor_name,
                    'value': factor_info.get('value', 'N/A'),
                    'label': factor_info.get('label', ''),
                    'score': score,
                    'dimension': dim_names.get(dim, dim),
                })
    
    # 按分数降序排序
    all_signals.sort(key=lambda x: x['score'], reverse=True)
    return all_signals


def collect_opportunity_signals(indices):
    """收集所有机会信号（负分=看多/低风险=机会）
    
    返回: list of dict，按机会程度从高到低排序（score越小越优）
    """
    all_signals = []
    
    dim_names = {
        'structural': '结构面',
        'sentiment': '量能情绪',
        'extreme': '极端值',
    }
    
    for idx_name, idx_data in indices.items():
        for factor_name, factor_info in idx_data.get('factors', {}).items():
            score = factor_info.get('score', 0)
            if score < 0:  # 负分=低风险/看多
                dim = 'sentiment'
                if factor_name in ['CFC', 'up50', 'top5z', 'CDI', 'AbsFactor', 'RelFactor']:
                    dim = 'structural'
                elif factor_name in ['LPPL泡沫']:
                    dim = 'extreme'
                
                all_signals.append({
                    'index': idx_name,
                    'factor': factor_name,
                    'value': factor_info.get('value', 'N/A'),
                    'label': factor_info.get('label', ''),
                    'score': score,  # 负值
                    'dimension': dim_names.get(dim, dim),
                })
    
    # 按分数升序（越小越优）
    all_signals.sort(key=lambda x: x['score'])
    return all_signals


def generate_conclusion(wanquan_idx, top_risks, top_opps):
    """生成一句话核心结论（纯规则，零LLM）"""
    ts = wanquan_idx.get('total_score', 0)
    rating = wanquan_idx.get('rating', '🟡 中性')
    
    # 根据主要风险类型生成结论
    risk_dims = set()
    for r in top_risks[:3]:
        risk_dims.add(r['dimension'])
    
    conclusion_parts = []
    
    if ts >= 0.5:
        conclusion_parts.append("多维度风险共振，整体偏谨慎")
    elif ts >= 0.3:
        if '结构风险' in risk_dims:
            conclusion_parts.append("结构层面有隐忧，注意控制仓位")
        elif '量能情绪风险' in risk_dims:
            conclusion_parts.append("情绪面偏热，警惕短期回调")
        else:
            conclusion_parts.append("局部风险偏高，保持警惕")
    elif ts >= 0:
        if top_opps:
            conclusion_parts.append("整体中性，结构性机会犹存")
        else:
            conclusion_parts.append("整体中性偏谨慎，观望为主")
    elif ts >= -0.3:
        if top_opps:
            conclusion_parts.append("风险偏好回暖，可适度积极")
        else:
            conclusion_parts.append("整体中性偏乐观")
    else:
        conclusion_parts.append("多维信号向好，进攻窗口打开")
    
    # 补充最突出的1个风险点
    if top_risks and ts >= 0.1:
        r0 = top_risks[0]
        conclusion_parts.append(f"关注{r0['factor']}信号")
    
    return f"{rating} | " + "，".join(conclusion_parts)


# ============================================================
#  两融风险监控 Markdown 章节生成
# ============================================================

def _generate_margin_markdown(margin_data):
    """生成两融风险监控的 Markdown 章节（8模块全覆盖）。
    
    Args:
        margin_data: compute_margin_signals 返回的 dict
    
    Returns:
        list[str]: Markdown 行列表
    """
    lines = []
    td = margin_data.get('trade_date', '')
    dt_str = f"{td[:4]}-{td[4:6]}-{td[6:]}" if len(td) == 8 else td
    
    # 标注实际数据日期（两融数据通常 T+1 发布）
    actual_date = margin_data.get('actual_data_date', '')
    requested_date = margin_data.get('requested_date', '')
    if actual_date and requested_date and actual_date != requested_date:
        date_note = f"（数据截至{actual_date[:4]}-{actual_date[4:6]}-{actual_date[6:]}，T+1延迟）"
    elif actual_date:
        date_note = f"（数据截至{actual_date[:4]}-{actual_date[4:6]}-{actual_date[6:]}）"
    else:
        date_note = ""
    
    risk = margin_data.get('risk_level', 'low')
    lbl = {'high': '🔴 高风险', 'medium': '🟡 中风险', 'low': '🟢 低风险'}.get(risk, '⚪')
    
    lines.append(f"### ④ 两融风险监控 | {dt_str} {lbl} {date_note}")
    lines.append("")
    
    # 数据缺失
    warns = margin_data.get('data_warnings', [])
    if warns:
        for w in warns:
            lines.append(f"> {w}")
        lines.append("")
    
    # 操作建议
    lines.append(f"**{margin_data.get('advice', '')}**")
    lines.append("")
    
    # ---- 核心指标 ----
    rzye_raw = margin_data.get('_rzye_raw')
    rzrqye_raw = margin_data.get('_rzrqye_raw')
    circ_mv = margin_data.get('total_circ_mv_yi')
    ratio = margin_data.get('margin_circ_ratio')
    
    metric_parts = []
    if rzye_raw:
        metric_parts.append(f"融资余额 **{rzye_raw / 1e8:.0f}亿**")
    if rzrqye_raw:
        metric_parts.append(f"两融 **{rzrqye_raw / 1e8:.0f}亿**")
    if circ_mv:
        if circ_mv > 10000:
            metric_parts.append(f"流通市值 **{circ_mv / 10000:.1f}万亿**")
        else:
            metric_parts.append(f"流通市值 **{circ_mv:.0f}亿**")
    # 两市成交额
    total_turnover_yi = margin_data.get('total_turnover_yi')
    if total_turnover_yi:
        if total_turnover_yi > 10000:
            metric_parts.append(f"两市成交 **{total_turnover_yi / 10000:.2f}万亿**")
        else:
            metric_parts.append(f"两市成交 **{total_turnover_yi:.0f}亿**")
    if ratio is not None:
        t = margin_data.get('ratio_trend', '→')
        metric_parts.append(f"融资/流通 **{ratio:.3f}%** {t}")
    if metric_parts:
        lines.append(" | ".join(metric_parts))
        lines.append("")
    
    # ---- 风险监控 ----
    lines.append("**一、风险监控**")
    lines.append("")
    
    # 信号1: 融资余额变化率 + 加速
    chg = margin_data.get('rzye_chg', {})
    cp = []
    for n in (5, 10, 20):
        v = chg.get(n)
        if v is not None:
            arrow = "↑" if v > 0 else "↓" if v < 0 else "→"
            cp.append(f"{n}日{v * 100:+.2f}%{arrow}")
    if cp:
        lines.append(f"1. 融资变化率: {' | '.join(cp)}")
    
    accel = margin_data.get('acceleration')
    if accel is not None:
        at = '⚡加速流出' if accel < -0.003 else ('减速中' if accel > 0.003 else '平稳')
        lines.append(f"   加速度: **{accel * 100:+.3f}%** ({at})")
    
    consec = margin_data.get('consecutive_decline_days', 0)
    if consec > 0:
        lines.append(f"   连续净偿还: **{consec}天**")
    
    # 信号2: 融资/流通市值比例
    if ratio is not None:
        pct = margin_data.get('ratio_percentile')
        pct_str = f"，历史分位{pct:.0f}%" if pct else ""
        lines.append(f"2. 融资/流通市值: {ratio:.3f}%{pct_str}")
    
    # 信号3: 高杠杆个股
    stress = margin_data.get('stress_stocks', [])
    chain_risk = margin_data.get('chain_risk', False)
    if chain_risk:
        lines.append(f"3. 🔴 **强平连锁风险** 高杠杆股集体加速偿还（{len(stress)}只）")
    elif stress:
        lines.append(f"3. 🟡 高杠杆股 {len(stress)}只，待观察")
    
    # 信号4: 杠杆资金撤退
    rz_buy = margin_data.get('rz_buy_ratio')
    leverage_exit = margin_data.get('leverage_exit', False)
    if rz_buy is not None:
        if leverage_exit:
            lines.append(f"4. 🔴 **杠杆资金撤退** 融资/成交额 {rz_buy:.2f}%（骤降）")
        else:
            lines.append(f"4. 融资买入/成交额: {rz_buy:.2f}%")
    
    lines.append("")
    
    # ---- 机会监控 ----
    lines.append("**二、机会监控**")
    lines.append("")
    
    # 信号5
    if margin_data.get('near_clearing'):
        lines.append("5. 🟢 **融资出清接近尾声** — 余额/流通市值<2.2% 且净偿还连续收窄")
    else:
        lines.append(f"5. 融资出清: 未触发")
    
    # 信号6
    rebound = margin_data.get('rebound_candidates', [])
    if rebound:
        lines.append(f"6. 🟢 超跌反弹候选: {len(rebound)}只（个股Top10见次日08:00两融早报⑥）")
    else:
        lines.append(f"6. 超跌反弹候选: 无")
    
    # 信号7
    if margin_data.get('bottom_divergence'):
        lines.append(f"7. 🟢 **底部确认信号** — {margin_data.get('divergence_type', '')}")
    else:
        lines.append(f"7. 底部背离: 未触发")
    
    # 信号8
    if margin_data.get('style_switch'):
        lines.append("8. 🟢 **风格切换信号** — 微盘股/低杠杆/高换手风格或占优")
    else:
        lines.append(f"8. 风格切换: 未触发")
    
    lines.append("")
    
    # ---- 227%信号 ----
    if margin_data.get('signal_227'):
        lines.append("⚠️ **227%担保比例等效信号已触发** — 融资流速加速（加速度<-0.3%），等效担保比例逼近警戒线")
        lines.append("")
    
    # ---- 三、输出：Top10 个股 ----
    lines.append("**三、个股明细**")
    lines.append("")
    
    # 融资压力最大 Top10
    stress_top = stress[:10]
    if stress_top:
        lines.append("**🔴 融资压力最大 Top10**")
        lines.append("")
        lines.append("| 股票 | 融资余额 | 占流通市值 | 日偿还 |")
        lines.append("|------|----------|------------|--------|")
        for s in stress_top:
            name = s.get('name', s['ts_code'].split('.')[0])
            code_short = s['ts_code']
            label = f"{name}({code_short})"
            rzche_str = f"{s['rzche'] / 1e4:.0f}万" if s.get('rzche') else '-'
            lines.append(f"| {label} | {s['rzye'] / 1e8:.1f}亿 | {s['ratio_pct']:.1f}% | {rzche_str} |")
        lines.append("")
    
    # 超跌反弹候选 Top10 已于 2026-10-22 移出晚报：
    # 该内容属两融栏目，完整 Top10 由次日 08:00《两融风险监控早报》模块⑥统一推送，
    # 此处仅保留候选数量汇总（见上方信号第6条），不再展开个股表，避免晚报过长截断/重复。

    if not stress_top:
        lines.append("个股数据不足或暂无触发条件")
        lines.append("")

    return lines


# ============================================================
#  报告生成
# ============================================================

def generate_markdown_report(trade_date, indices, top_risks, top_opps, conclusion, data_date_info=None, is_midday=False, timing_data=None, margin_data=None):
    """生成Markdown格式风险预测日报
    
    Args:
        data_date_info: dict with earliest, latest, max_lag_days, sources, target_date
        is_midday: 是否为午盘版
        timing_data: 三维择时监控数据（收盘模式才有）
        margin_data: 两融风险监控数据（8模块信号 dict）
    """
    date_display = trade_date[:4] + '-' + trade_date[4:6] + '-' + trade_date[6:]
    wq = indices.get('万得全A', {})
    
    lines = []
    title_suffix = " 🌞 午盘版" if is_midday else ""
    lines.append(f"# 📊 A股风险预测日报 | {date_display}{title_suffix}")
    lines.append("")
    lines.append("> 📎 此消息来自 Coze Agent 蓁宝")
    lines.append("")
    
    # 午盘版提示
    if is_midday:
        lines.append("> 🌞 **午盘版（实时估算） | ATM IV/PCR/GEX/结构因子已基于实时数据估算，CFC已移除（口径不一致），SKEW仅供参考，LPPL等周线指标收盘后更新**")
        lines.append("")
    
    # 数据日期标注（如有回退）
    if data_date_info and data_date_info.get('max_lag_days', 0) > 0 and not is_midday:
        earliest = data_date_info['earliest']
        latest = data_date_info['latest']
        max_lag = data_date_info['max_lag_days']
        earliest_disp = f"{earliest[:4]}-{earliest[4:6]}-{earliest[6:]}"
        latest_disp = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"
        if earliest == latest:
            lines.append(f"> 📅 数据日期: {earliest_disp}（滞后{max_lag}天）")
        else:
            lines.append(f"> 📅 数据日期: {earliest_disp} ~ {latest_disp}（最早滞后{max_lag}天）")
        lines.append("")
    
    # 综合评级
    lines.append("## 🏆 综合风险评级（万得全A）")
    lines.append("")
    lines.append(f"**{conclusion}**")
    lines.append("")
    lines.append(f"- 综合得分: {wq.get('total_score', 0):+.3f} (-1~+1)")
    lines.append(f"- 评级: {wq.get('rating', 'N/A')}")
    lines.append("")
    
    # 各维度得分
    dims = wq.get('dim_scores', {})
    dim_labels = [
        ('structural', '结构风险'),
        ('sentiment', '量能情绪风险'),
        ('extreme', '极端风险'),
    ]
    lines.append("| 维度 | 风险分 | 状态 |")
    lines.append("|------|--------|------|")
    for dim_key, dim_name in dim_labels:
        s = dims.get(dim_key)
        if s is not None:
            if s >= 0.5:
                status = '🔴 高风险'
            elif s >= 0.2:
                status = '🟠 偏高'
            elif s >= -0.2:
                status = '🟡 中性'
            elif s >= -0.5:
                status = '🟢 低风险'
            else:
                status = '✅ 极低风险'
            lines.append(f"| {dim_name} | {s:+.3f} | {status} |")
        else:
            lines.append(f"| {dim_name} | N/A | ⚪ 数据缺失 |")
    lines.append("")
    
    # 核心风险信号 TOP3
    lines.append("## ⚠️ 核心风险信号 TOP 3")
    lines.append("")
    if top_risks:
        for i, r in enumerate(top_risks[:3]):
            lines.append(f"{i+1}. **{r['index']} - {r['factor']}**: {r['value']} → {r['label']} ({r['dimension']})")
    else:
        lines.append("暂无显著风险信号 ✅")
    lines.append("")
    
    # 潜在机会信号
    lines.append("## ✅ 潜在机会信号")
    lines.append("")
    if top_opps:
        for i, o in enumerate(top_opps[:5]):
            lines.append(f"- **{o['index']} - {o['factor']}**: {o['value']} → {o['label']} ({o['dimension']})")
    else:
        lines.append("暂无明确机会信号")
    lines.append("")
    
    # 各指数对比
    lines.append("## 📈 各指数风险对比")
    lines.append("")
    lines.append("| 指数 | 综合评级 | 结构 | 量能情绪 | 极端 |")
    lines.append("|------|----------|------|----------|------|")
    for idx_name in CORE_INDICES + ['科创50', '万得全A']:
        idx = indices.get(idx_name, {})
        rating = idx.get('rating', 'N/A')
        ds = idx.get('dim_scores', {})
        d_row = []
        for dim in ['structural', 'sentiment', 'extreme']:
            s = ds.get(dim)
            if s is not None:
                d_row.append(f"{s:+.2f}")
            else:
                d_row.append('N/A')
        lines.append(f"| {idx_name} | {rating} | {d_row[0]} | {d_row[1]} | {d_row[2]} |")
    lines.append("")
    
    # 各模块明细
    lines.append("---")
    lines.append("")
    lines.append("## 📋 各模块明细")
    lines.append("")
    
    # 结构风险
    lines.append("### ① 结构风险")
    lines.append("")
    struct_indices = [i for i in CORE_INDICES + ['科创50', '万得全A'] if indices.get(i, {}).get('structural_count', 0) > 0]
    if struct_indices:
        # 午盘版不显示CFC列（午盘离散度公式与收盘版峰度+偏度口径不一致，已移除）
        if is_midday:
            struct_keys = ['up50', 'top5z', 'CDI', 'AbsFactor', 'RelFactor']
            struct_headers = '| 指数 | up50 | top5z | CDI峰度 | AbsFactor | RelFactor | 综合 |'
            struct_sep = '|------|------|-------|-----|-----------|-----------|------|'
        else:
            struct_keys = ['CFC', 'up50', 'top5z', 'CDI', 'AbsFactor', 'RelFactor']
            struct_headers = '| 指数 | CFC | up50 | top5z | CDI峰度 | AbsFactor | RelFactor | 综合 |'
            struct_sep = '|------|-----|------|-------|-----|-----------|-----------|------|'
        lines.append(struct_headers)
        lines.append(struct_sep)
        for idx_name in struct_indices:
            idx = indices[idx_name]
            f = idx.get('factors', {})
            vals = []
            for key in struct_keys:
                vals.append(f.get(key, {}).get('label', '-'))
            ds = idx.get('dim_scores', {}).get('structural', 0)
            vals.append(f"{ds:+.2f}")
            lines.append(f"| {idx_name} | " + " | ".join(vals) + " |")
    else:
        lines.append("暂无数据")
    lines.append("")
    
    # 量能情绪风险
    lines.append("### ② 量能情绪风险")
    lines.append("")
    sent_indices = [i for i in ['万得全A'] + CORE_INDICES if indices.get(i, {}).get('sentiment_count', 0) > 0]
    if sent_indices:
        lines.append("| 指数 | 成交额/量分位 | 成交额集中度 | 涨/跌家数 | 综合 |")
        lines.append("|------|------------|--------------|----------|------|")
        for idx_name in sent_indices:
            idx = indices[idx_name]
            f = idx.get('factors', {})
            vp = (f.get('成交额分位') or f.get('成交量分位') or {}).get('label', '-')
            conc = f.get('成交额集中度', {}).get('label', '-')
            ratio = (f.get('涨跌家数比') or f.get('全市场涨跌停比') or f.get('涨跌停比') or {}).get('label', '-')
            ds = idx.get('dim_scores', {}).get('sentiment', 0)
            ds = ds if ds is not None else 0
            lines.append(f"| {idx_name} | {vp} | {conc} | {ratio} | {ds:+.2f} |")
    else:
        lines.append("暂无数据")
    lines.append("")
    
    # 极端风险
    lines.append("### ③ 极端风险（LPPL）")
    lines.append("")
    ext_indices = [i for i in ['万得全A'] + CORE_INDICES if indices.get(i, {}).get('extreme_count', 0) > 0]
    if ext_indices:
        lines.append("| 指数 | LPPL信号 | 综合 |")
        lines.append("|------|----------|------|")
        for idx_name in ext_indices:
            idx = indices[idx_name]
            f = idx.get('factors', {})
            lppl = f.get('LPPL泡沫', {}).get('label', '-')
            ds = idx.get('dim_scores', {}).get('extreme', 0)
            lines.append(f"| {idx_name} | {lppl} | {ds:+.2f} |")
    else:
        lines.append("暂无LPPL数据")
    lines.append("")

    # ④ 两融风险监控（独立栏目）
    if margin_data and margin_data.get('risk_level') != 'unknown':
        margin_md = _generate_margin_markdown(margin_data)
        lines.extend(margin_md)
        lines.append("")

    # 三维择时监控
    if timing_data is not None:
        timing_md = generate_timing_markdown(timing_data)
        lines.extend(timing_md)
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 案例分析学习材料，不构成投资建议*")
    
    return '\n'.join(lines)


# ============================================================
#  主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='A股风险预测日报')
    parser.add_argument('trade_date', nargs='?', default=None,
                        help='交易日期 YYYYMMDD，默认当天')
    parser.add_argument('--no-push', action='store_true',
                        help='只生成本地报告，不推送飞书')
    parser.add_argument('--midday', action='store_true',
                        help='强制使用午盘模式（实时数据源）')
    args = parser.parse_args()
    
    # 确定交易日期（校验格式，防止result_mode等非日期参数被当成日期）
    import re
    if args.trade_date and re.match(r'^\d{8}$', args.trade_date):
        trade_date = args.trade_date
    else:
        if args.trade_date:
            print(f"⚠️  日期参数格式无效: {args.trade_date}，使用当天日期")
        trade_date = datetime.now().strftime('%Y%m%d')
    
    date_display = trade_date[:4] + '-' + trade_date[4:6] + '-' + trade_date[6:]
    
    print("=" * 60)
    print(f"A股风险预测日报 | {date_display}")
    print("=" * 60)
    print()
    
    # ========== 判断模式：午盘 / 收盘 ==========
    is_midday = args.midday
    if not is_midday:
        # 自动检测：检查Tushare当日数据是否已更新
        print("🔍 自动检测数据模式...")
        is_midday = detect_midday_mode(trade_date)
    
    mode_name = "🌞 午盘版" if is_midday else "🌙 收盘版"
    print(f"\n📌 运行模式: {mode_name}")
    print()
    
    # ---- 数据采集重试机制 ----
    # 午盘版时效性强：2次重试，每次间隔30秒，总等待≤60秒；数据不全则带告警推送
    # 收盘版可等待：3次重试，每次间隔60秒（保持原逻辑）
    if is_midday:
        MAX_RETRY = 2
        RETRY_INTERVAL = 30  # 秒
    else:
        MAX_RETRY = 3
        RETRY_INTERVAL = 60  # 秒
    indices = {}
    top_risks = []
    top_opps = []
    conclusion = ""
    data_date_info = ""
    style_drift = None
    timing_data = None

    def _check_core_data_missing(indices_dict):
        """检查核心维度数据缺失情况，返回缺失数量"""
        missing = 0
        wq = indices_dict.get('万得全A', {})
        core_keys = ['pe_score', 'pb_score', 'risk_score', 'total_score', 'rating']
        for k in core_keys:
            if wq.get(k) is None or wq.get(k) == 0:
                missing += 1
        # 如果连万得全A都没数据，严重缺失
        if not wq:
            missing += 10
        return missing

    _cached_margin_data = None  # 缓存两融数据，避免重试时重复计算（耗时长）

    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n🔄 第{attempt}次数据采集...")
        if is_midday:
            indices, top_risks, top_opps, conclusion, data_date_info = run_midday_mode(trade_date)
            style_drift = None
            timing_data = None
            margin_data = None
        else:
            indices, top_risks, top_opps, conclusion, data_date_info, style_drift, timing_data, margin_data = run_closing_mode(trade_date, _cached_margin_data=_cached_margin_data)
            # 缓存两融数据供后续重试使用
            if margin_data and margin_data.get('risk_level') != 'unknown':
                _cached_margin_data = margin_data

        missing = _check_core_data_missing(indices)
        if missing < 2:
            print(f"✅ 第{attempt}次采集数据质量达标（核心缺失{missing}项），停止重试")
            break
        elif attempt < MAX_RETRY:
            print(f"⚠️  核心数据缺失{missing}项，{RETRY_INTERVAL}秒后进行第{attempt+1}次重试...")
            import time as _t
            _t.sleep(RETRY_INTERVAL)
        else:
            print(f"❌ {MAX_RETRY}次重试后数据仍不完整（缺失{missing}项），将带告警推送")

    # 万得全A综合
    wanquan = indices.get('万得全A', {})
    
    print(f"\n📊 综合评级: {wanquan.get('rating', 'N/A')}")
    print(f"📝 核心结论: {conclusion}")
    print(f"⚠️ 风险信号数: {len(top_risks)}")
    print(f"✅ 机会信号数: {len(top_opps)}")
    
    # ========== 3. 生成报告 ==========
    print("\n\n📝 生成报告...")
    
    md_report = generate_markdown_report(
        trade_date, indices, top_risks, top_opps, conclusion,
        data_date_info=data_date_info,
        is_midday=is_midday,
        timing_data=timing_data,
        margin_data=margin_data,
    )
    
    report_suffix = '_风险预测日报_午盘版.md' if is_midday else '_风险预测日报.md'
    report_path = os.path.join(OUTPUT_DIR, f'{date_display}{report_suffix}')

    # 幂等检查：当天已推送过则永久跳过（用.pushed标记文件）
    pushed_flag = report_path + '.pushed'
    skip_push = False
    if os.path.exists(pushed_flag):
        skip_push = True
        print(f"\n⚠️  今日 {date_display} 报告已推送过，跳过飞书推送（幂等保护）")

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(md_report)
    print(f"  报告已保存: {report_path}")
    
    # ========== 4. 推送飞书卡片 ==========
    if not args.no_push and not skip_push:
        print("\n\n🚀 推送飞书卡片...")
        try:
            from feishu_card_utils import build_risk_forecast_cards
            from feishu_push import push_card_to_feishu

            # ---- 数据缺失检测 ----
            data_warnings = []

            # 从 indices 中统计核心维度的数据缺失情况
            wanquan = indices.get('万得全A', {})
            factors = wanquan.get('factors', {})

            # 1. CFC结构风险数据：检查CFC因子是否缺失
            cfc_val = factors.get('CFC', {}).get('value', '')
            if not cfc_val or cfc_val in ('-', '—', 'N/A', '') or '数据缺失' in str(cfc_val):
                cfc_val2 = factors.get('资金拥挤度', {}).get('value', '')
                if not cfc_val2 or cfc_val2 in ('-', '—', 'N/A', ''):
                    data_warnings.append("⚠️ CFC结构风险因子数据缺失")

            # 2. 结构因子数据：AbsFactor/RelFactor（按核心指数维度）
            #    5个核心指数中少于2个有结构因子数据才告警
            sf_core_indices = ['沪深300', '中证500', '中证1000', '创业板指', '科创50']
            sf_available_count = 0
            for _idx_name in sf_core_indices:
                _idx = indices.get(_idx_name, {})
                _f = _idx.get('factors', {})
                _abs_v = _f.get('AbsFactor', {}).get('value', '')
                _rel_v = _f.get('RelFactor', {}).get('value', '')
                _abs_ok = bool(_abs_v) and _abs_v not in ('-', '—', 'N/A', '') and '数据缺失' not in str(_abs_v) and not _f.get('AbsFactor', {}).get('is_pending', False)
                _rel_ok = bool(_rel_v) and _rel_v not in ('-', '—', 'N/A', '') and '数据缺失' not in str(_rel_v) and not _f.get('RelFactor', {}).get('is_pending', False)
                if _abs_ok or _rel_ok:
                    sf_available_count += 1
            if sf_available_count < 2:
                data_warnings.append(f"⚠️ 结构因子(AbsFactor/RelFactor)数据缺失（{sf_available_count}/5个核心指数）")

            # 3. 六大宽基量能（成交额/成交量分位）数据缺失检测
            core_vol_names = ['上证50', '沪深300', '中证500', '中证1000', '创业板指', '科创50']
            vol_ok = 0
            for _idx_name in core_vol_names:
                _f = indices.get(_idx_name, {}).get('factors', {})
                if _f.get('成交额分位', {}).get('value') or _f.get('成交量分位', {}).get('value'):
                    vol_ok += 1
            if vol_ok < 3:
                data_warnings.append(f"⚠️ 六大宽基量能分位数据缺失（仅{vol_ok}/6个指数取到）")

            if data_warnings:
                print(f"  ⚠️ 数据缺失告警: {len(data_warnings)}项")
                for w in data_warnings:
                    print(f"    {w}")

            # 多卡片：总览 / 风险模块明细 / 两融&三维择时（防飞书单卡过长截断，每张都带标题）
            cards = build_risk_forecast_cards(
                date_display,
                indices=indices,
                conclusion=conclusion,
                top_risks=top_risks[:5],
                top_opps=top_opps,
                style_drift=style_drift,
                timing_data=timing_data,
                margin_data=margin_data,
                is_midday=is_midday,
                data_warnings=data_warnings,
            )

            # 午盘版：每张卡片顶部插入实时估算提示
            if is_midday:
                midday_note = {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "<font color='orange'>🌞 **午盘版** | 结构因子/量能为实时估算，CFC已移除（口径不一致），LPPL等周线指标收盘后更新；期权PCR仅在三维择时模块呈现</font>"
                    }
                }
                for c in cards:
                    if c.get('elements'):
                        c['elements'].insert(0, midday_note)

            # 依次推送所有卡片（幂等：同日同任务由外层调度保证不重复触发）
            push_ok = 0
            for ci, card in enumerate(cards, 1):
                if push_card_to_feishu(card_dict=card):
                    push_ok += 1
                    print(f"✅ 飞书卡片 {ci}/{len(cards)} 推送成功")
                else:
                    print(f"❌ 飞书卡片 {ci}/{len(cards)} 推送失败")
            success = push_ok == len(cards)
            if success:
                print(f"✅ 飞书全部 {len(cards)} 张卡片推送完成")
            else:
                print(f"⚠️ 飞书卡片部分推送失败：{push_ok}/{len(cards)}")
        except Exception as e:
            print(f"❌ 飞书推送异常: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n⏭️  跳过飞书推送（--no-push）")
    
    print("\n" + "=" * 60)
    print(f"✅ 风险预测日报生成完成（{mode_name}）")
    print("=" * 60)
    
    return report_path


def run_midday_mode(trade_date):
    """运行午盘模式（实时数据源）
    
    Returns:
        (indices, top_risks, top_opps, conclusion, data_date_info)
    """
    if not _realtime_ready:
        print("❌ 实时数据模块不可用，无法运行午盘模式")
        return {}, [], [], "⚠️ 实时数据模块不可用", None
    
    # 获取所有午盘实时数据（增强版，含期权SKEW/GEX、up50/top5z/CDI、结构因子；CFC已移除）
    if fetch_all_midday_data_enhanced is not None:
        midday_data = fetch_all_midday_data_enhanced(
            trade_date,
            tushare_pro=pro if _tushare_ready else None,
            data_hub_get_data=get_data if _datahub_ready else None
        )
    else:
        midday_data = fetch_all_midday_data(trade_date)
    
    # 构建风险信号
    print("\n\n🧮 计算午盘风险信号...")
    indices = build_midday_signals(midday_data)
    
    # 收集风险和机会信号
    top_risks = collect_risk_signals(indices)
    top_opps = collect_opportunity_signals(indices)
    
    # 万得全A综合
    wanquan = indices.get('万得全A', {})
    
    # 生成结论
    conclusion = generate_conclusion(wanquan, top_risks, top_opps)
    
    # 数据日期信息
    data_date_info = {
        'earliest': trade_date,
        'latest': trade_date,
        'max_lag_days': 0,
        'sources': {'实时行情': trade_date},
        'target_date': trade_date,
        'is_midday': True,
    }
    
    return indices, top_risks, top_opps, conclusion, data_date_info


def run_closing_mode(trade_date, _cached_margin_data=None):
    """运行收盘模式（原有Tushare数据源）
    
    Args:
        trade_date: 交易日期
        _cached_margin_data: 缓存的两融数据，避免重试时重复计算
    
    Returns:
        (indices, top_risks, top_opps, conclusion, data_date_info, style_drift, timing_data, margin_data)
    """
    # ========== 1. 读取各模块数据 ==========
    print("📥 读取各模块数据...")
    
    # 记录各数据源的实际日期，用于报告标注
    data_sources = {}
    
    print("\n--- 期权数据（供三维择时PCR，不参与综合评级） ---")
    option_result = load_option_data(trade_date)
    option_data = option_result[0] if option_result else None
    option_date = option_result[1] if option_result and len(option_result) > 1 else None
    if option_date:
        data_sources['期权数据'] = option_date
    
    print("\n--- 结构风险（CFC） ---")
    cfc_result = load_cfc_data(trade_date, enable_fallback=True)
    cfc_data = cfc_result[0] if cfc_result else None
    cfc_date = cfc_result[1] if cfc_result and len(cfc_result) > 1 else None
    if cfc_date:
        data_sources['CFC因子'] = cfc_date
    
    print("\n--- 结构因子（AbsFactor/RelFactor） ---")
    sf_result = load_structural_factor_data(trade_date, enable_fallback=True)
    sf_data = sf_result[0] if sf_result else None
    sf_date = sf_result[1] if sf_result and len(sf_result) > 1 else None
    if sf_date:
        data_sources['结构因子'] = sf_date
    
    print("\n--- 风格漂移 ---")
    sd_result = load_style_drift_data(trade_date)
    style_drift = sd_result[0] if sd_result else None
    sd_date = sd_result[1] if sd_result and len(sd_result) > 1 else None
    if sd_date:
        data_sources['风格漂移'] = sd_date
    
    print("\n--- 成交量分位 ---")
    vp_result = load_volume_percentile_data(trade_date)
    vol_pct_data = vp_result[0] if vp_result else None
    vp_date = vp_result[1] if vp_result and len(vp_result) > 1 else None
    if vp_date:
        data_sources['成交量分位'] = vp_date

    # 六大宽基指数量能分位（data_hub 三级降级，补齐各指数对比表量能列）
    print("\n--- 六大指数量能分位（data_hub 三级降级） ---")
    core_amount_pct = calc_core_index_amount_pct(trade_date)
    if core_amount_pct:
        data_sources['指数量能分位'] = trade_date

    # 六大宽基成分股情绪（成交额集中度top5 + 涨跌家数比，补齐量能情绪模块空格）
    print("\n--- 六大指数成分股情绪（集中度/涨跌家数比） ---")
    core_breadth = calc_core_index_breadth(trade_date)
    if core_breadth:
        data_sources['成分股情绪'] = trade_date
    
    print("\n--- LPPL泡沫检测 ---")
    lppl_result = load_lppl_data(trade_date, '000001.SH')
    lppl_data = lppl_result[0] if lppl_result else None
    lppl_date = lppl_result[1] if lppl_result and len(lppl_result) > 1 else None
    if lppl_date:
        data_sources['LPPL检测'] = lppl_date
    
    print("\n--- 市场综合监控（VIX/AR/兴登堡） ---")
    mm_result = load_market_monitor_summary(trade_date)
    market_data = mm_result[0] if mm_result else None
    mm_date = mm_result[1] if mm_result and len(mm_result) > 1 else None
    if mm_date:
        data_sources['市场监控'] = mm_date
    
    # ========== 先计算两融风险监控（供三维择时复用，避免两套结论） ==========
    margin_data = None
    if _MARGIN_MONITOR_READY:
        if _cached_margin_data and _cached_margin_data.get('risk_level') != 'unknown':
            margin_data = _cached_margin_data
            print("\n--- 两融风险监控 ---")
            print(f"  ⚡ 使用缓存数据（跳过重复计算）风险={margin_data.get('risk_level')}")
        else:
            print("\n--- 两融风险监控 ---")
            try:
                from data_hub.margin_detail_db import sync_margin_detail, sync_daily_basic
                sync_margin_detail(trade_date)
                import time as _time_mod
                _time_mod.sleep(2)
                sync_daily_basic(trade_date)
                margin_data = compute_margin_signals(trade_date)
                if margin_data:
                    print(f"  ✅ 两融信号: 风险={margin_data.get('risk_level', '?')} | 建议={margin_data.get('advice', '?')}")
                else:
                    print(f"  ⚠️  两融信号返回空")
            except Exception as e:
                print(f"  ❌ 两融信号计算异常: {e}")
                import traceback
                traceback.print_exc()
    
    print("\n--- 三维择时监控 ---")
    timing_data = load_timing_monitor_data(trade_date, option_data=option_data, margin_data=margin_data)
    
    # 计算数据日期范围和最大滞后
    all_dates = [d for d in data_sources.values() if d]
    if all_dates:
        earliest_date = min(all_dates)
        latest_date = max(all_dates)
        max_lag_days = (datetime.strptime(trade_date, '%Y%m%d') - datetime.strptime(earliest_date, '%Y%m%d')).days
        data_date_info = {
            'earliest': earliest_date,
            'latest': latest_date,
            'max_lag_days': max_lag_days,
            'sources': data_sources,
            'target_date': trade_date,
        }
    else:
        data_date_info = None
    
    # ========== 2. 计算各指数风险信号 ==========
    print("\n\n🧮 计算风险信号...")
    
    indices = build_index_signals(option_data, cfc_data, sf_data, market_data, vol_pct_data, lppl_data, trade_date=trade_date, core_amount_pct=core_amount_pct, core_breadth=core_breadth)
    
    # 加入风格漂移到结果（作为附加信息）
    if style_drift:
        indices['风格漂移'] = style_drift
    
    # 收集风险和机会信号
    top_risks = collect_risk_signals(indices)
    top_opps = collect_opportunity_signals(indices)
    
    # 生成结论
    wanquan = indices.get('万得全A', {})
    conclusion = generate_conclusion(wanquan, top_risks, top_opps)
    
    # 两融风险监控已在上方提前计算（供三维择时复用），此处不再重复
    # margin_data 已在上方计算，直接返回
    
    return indices, top_risks, top_opps, conclusion, data_date_info, style_drift, timing_data, margin_data


# ============================================================
#  三维择时监控模块（6个子维度）
#  数据来源：data_hub 统一采集层（四级降级）
#  零LLM依赖，纯Python规则实现
# ============================================================

# 三维择时监控 - 宽基ETF列表
TIMING_ETF_LIST = [
    ('510300.SH', '沪深300ETF'),
    ('512100.SH', '中证1000ETF'),
    ('588000.SH', '科创50ETF'),
]

# 热门科技板块关键词（用于公募持仓估算的代理指标）
HOT_TECH_SECTORS = ['CPO', '光模块', 'AI硬件', '人工智能', '半导体', '芯片', '存储芯片', '算力']


def _safe_num(val, default=None):
    """安全转换为数值，失败返回default"""
    if val is None:
        return default
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _pct_change(new_val, old_val):
    """计算百分比变化，返回小数（如0.05表示+5%）"""
    if old_val is None or new_val is None or old_val == 0:
        return None
    return (new_val - old_val) / abs(old_val)


# ============================================================
#  ① ETF资金流监控
# ============================================================

def calc_etf_flow_signal(trade_date):
    """计算ETF资金流信号。
    
    标的：沪深300ETF(510300)、中证1000ETF(512100)、科创50ETF(588000)
    计算：每日份额变化、连续增长/减少天数、周增长率
    信号：🟢连续3日净增长或单周>2% | 🟡±1%以内 | 🔴连续5日净减少或破前低
    
    Returns:
        dict: {
            'signal': 'green'/'yellow'/'red'/None,
            'label': 信号标签,
            'etfs': {code: {name, latest_share, daily_change_pct, streak_days, weekly_change_pct, trend}},
            'summary': 综合判断文字
        }
    """
    result = {
        'signal': None,
        'label': '⚪ 数据缺失',
        'etfs': {},
        'summary': '',
        'data_available': False,
    }
    
    if not _datahub_ready:
        result['summary'] = 'data_hub不可用'
        return result
    
    etf_details = {}
    all_green = 0
    all_red = 0
    all_yellow = 0
    
    for ts_code, name in TIMING_ETF_LIST:
        try:
            # 从 data_hub 获取最近20天的份额数据
            df = get_data('etf_share', ts_code=ts_code, lookback_days=20)
            if df is None or len(df) < 3:
                etf_details[ts_code] = {
                    'name': name,
                    'error': f'数据不足（仅{len(df) if df is not None else 0}天）',
                }
                continue
            
            df_sorted = df.sort_values('trade_date').reset_index(drop=True)
            shares = df_sorted['fund_share'].tolist()
            dates = df_sorted['trade_date'].tolist()
            
            latest_share = shares[-1]
            prev_share = shares[-2] if len(shares) >= 2 else None
            daily_change = _pct_change(latest_share, prev_share)
            
            # 计算连续增长/减少天数
            streak_days = 0
            streak_dir = 0  # 1=增长, -1=减少, 0=方向未定
            for i in range(len(shares)-1, 0, -1):
                diff = shares[i] - shares[i-1]
                if diff > 0:
                    if streak_dir == 0 or streak_dir == 1:
                        streak_days += 1
                        streak_dir = 1
                    else:
                        break
                elif diff < 0:
                    if streak_dir == 0 or streak_dir == -1:
                        streak_days += 1
                        streak_dir = -1
                    else:
                        break
                else:
                    break  # 持平则中断连续
            
            # 周增长率（最近5个交易日）
            weekly_change = None
            if len(shares) >= 5:
                weekly_change = _pct_change(latest_share, shares[-5])
            
            # 趋势判断
            if streak_dir == 1 and streak_days >= 3:
                trend = 'up'
                etf_signal = 'green'
                all_green += 1
            elif streak_dir == -1 and streak_days >= 5:
                trend = 'down'
                etf_signal = 'red'
                all_red += 1
            elif weekly_change is not None and weekly_change > 0.02:
                trend = 'up_weekly'
                etf_signal = 'green'
                all_green += 1
            elif weekly_change is not None and abs(weekly_change) <= 0.01:
                trend = 'flat'
                etf_signal = 'yellow'
                all_yellow += 1
            else:
                trend = 'neutral'
                etf_signal = 'yellow'
                all_yellow += 1
            
            etf_details[ts_code] = {
                'name': name,
                'latest_share': latest_share,
                'latest_date': dates[-1],
                'daily_change_pct': daily_change,
                'streak_days': streak_days,
                'streak_dir': streak_dir,
                'weekly_change_pct': weekly_change,
                'trend': trend,
                'signal': etf_signal,
            }
        except Exception as e:
            etf_details[ts_code] = {
                'name': name,
                'error': str(e),
            }
    
    result['etfs'] = etf_details
    
    # 综合判断：至少2只ETF同方向则采纳该信号
    valid_count = sum(1 for v in etf_details.values() if 'signal' in v)
    if valid_count == 0:
        result['signal'] = None
        result['label'] = '⚪ 数据缺失'
        result['summary'] = '暂无有效ETF份额数据'
        return result
    
    result['data_available'] = True
    
    if all_green >= 2:
        result['signal'] = 'green'
        result['label'] = '🟢 资金流入'
        result['summary'] = f'{all_green}只宽基ETF连续净申购，资金持续流入'
    elif all_red >= 2:
        result['signal'] = 'red'
        result['label'] = '🔴 资金流出'
        result['summary'] = f'{all_red}只宽基ETF连续赎回，资金持续流出'
    else:
        result['signal'] = 'yellow'
        result['label'] = '🟡 资金中性'
        result['summary'] = f'宽基ETF资金流分化，整体呈中性'
    
    return result


# ============================================================
#  ② 市场拥挤度监控
# ============================================================

def calc_crowding_signal(trade_date):
    """计算市场拥挤度信号。
    
    计算：全市场成交额前10%个股占比（成交集中度）
    信号：🟢<35%且5日均线拐头向下 | 🟡35%-50% | 🔴>50%或5日均>50%
    
    Returns:
        dict: {signal, label, concentration_pct, ma5_concentration, trend, summary}
    """
    result = {
        'signal': None,
        'label': '⚪ 数据缺失',
        'concentration_pct': None,
        'ma5_concentration': None,
        'trend': '',
        'summary': '',
        'data_available': False,
    }
    
    if not _datahub_ready:
        result['summary'] = 'data_hub不可用'
        return result
    
    try:
        # 从 data_hub 获取最近10个交易日的全市场行情（用于计算集中度和均线）
        # 使用 stock_daily 按 trade_date 获取单日全市场数据
        # 这里复用已有的成交量分位/市场监控数据中的成交额集中度
        # 如果没有，则从 data_hub 重新计算
        
        # 尝试获取最近10天的全市场成交额数据
        # 由于全市场数据量很大，我们用最近1天的数据 + 历史值来估算
        df_day = get_data('stock_daily', trade_date=trade_date)
        
        if df_day is None or len(df_day) == 0:
            result['summary'] = '无法获取全市场行情数据'
            return result
        
        # 计算成交额前10%个股的成交额占比
        amount_col = None
        for col in ['amount', '成交额', '成交金额']:
            if col in df_day.columns:
                amount_col = col
                break
        
        if amount_col is None:
            result['summary'] = '无法识别成交额列'
            return result
        
        # 过滤掉成交额为0或NaN的
        amounts = df_day[amount_col].dropna()
        amounts = amounts[amounts > 0]
        
        if len(amounts) < 100:
            result['summary'] = f'有效个股数量不足（{len(amounts)}只）'
            return result
        
        # 排序，取前10%
        amounts_sorted = amounts.sort_values(ascending=False)
        top_n = max(1, int(len(amounts_sorted) * 0.1))
        top_amount = amounts_sorted.iloc[:top_n].sum()
        total_amount = amounts_sorted.sum()
        
        if total_amount <= 0:
            result['summary'] = '总成交额为0'
            return result
        
        concentration = top_amount / total_amount  # 小数，如0.45表示45%
        
        result['concentration_pct'] = concentration
        result['data_available'] = True
        
        # 计算5日均线（需要历史数据，这里简化处理，只看当日值）
        # 完整实现应该取最近5天数据，这里先用当日值近似
        # TODO: 优化为取5天数据计算均线
        ma5 = concentration  # 简化
        result['ma5_concentration'] = ma5
        
        # 信号判断
        if concentration > 0.50 or ma5 > 0.50:
            result['signal'] = 'red'
            result['label'] = '🔴 极度拥挤'
            result['trend'] = 'over_crowded'
            result['summary'] = f'成交集中度{concentration*100:.1f}%，资金极度抱团龙头股'
        elif concentration >= 0.35:
            result['signal'] = 'yellow'
            result['label'] = '🟡 正常偏集中'
            result['trend'] = 'neutral'
            result['summary'] = f'成交集中度{concentration*100:.1f}%，市场情绪正常'
        else:
            result['signal'] = 'green'
            result['label'] = '🟢 分散健康'
            result['trend'] = 'healthy'
            result['summary'] = f'成交集中度{concentration*100:.1f}%，资金分散，市场健康'
    
    except Exception as e:
        result['summary'] = f'计算失败: {e}'
    
    return result


# ============================================================
#  ③ 期权情绪监控（PCR）
# ============================================================

def calc_pcr_signal(option_data):
    """计算期权PCR（Put-Call Ratio）情绪信号。
    
    标的：科创50ETF期权（588000系列）
    计算：当日PCR值（成交量/持仓量）
    信号：🟢从>1.5回落至1.0-1.2 | 🟡0.8-1.5 | 🔴>1.7或<0.6
    
    Args:
        option_data: 期权数据（复用脚本已有期权数据模块）
    
    Returns:
        dict: {signal, label, pcr_value, pcr_trend, underlying, summary}
    """
    result = {
        'signal': None,
        'label': '⚪ 数据缺失',
        'pcr_value': None,
        'pcr_trend': '',
        'underlying': '588000 科创50ETF',
        'summary': '',
        'data_available': False,
    }
    
    if option_data is None:
        result['summary'] = '期权数据不可用'
        return result
    
    try:
        pcr = None
        pcr_oi = None  # 持仓量PCR
        
        # 方式1（优先）：从 option_data['skew'] 中提取PCR（与期权采集同源同口径）
        if isinstance(option_data, dict):
            skew_data = option_data.get('skew', {})
            # 优先使用科创50ETF的PCR（与③ 期权情绪(PCR)标的匹配）
            # 若科创50无数据，降级到其他流动性好的标的
            for opt_name in ['科创50ETF', '300ETF', '50ETF', '500ETF', '创业板ETF', '中证1000']:
                if opt_name in skew_data and skew_data[opt_name]:
                    sk = skew_data[opt_name]
                    pcr = sk.get('vol_pcr')
                    if pcr is None:
                        pcr = sk.get('amt_pcr')
                    pcr_oi = sk.get('oi_pcr')
                    if pcr is not None:
                        result['underlying'] = f'{opt_name}'
                        break
        
        # 方式2（兜底）：如果没有现成的，用data_hub获取期权数据计算
        if pcr is None and _datahub_ready:
            try:
                opt_data = get_data('option_data', exchange='SSE', underlying='588000')
                if opt_data is not None:
                    pcr = _calc_pcr_from_contracts(opt_data)
            except Exception:
                pass
        
        result['pcr_value'] = pcr
        result['data_available'] = pcr is not None
        
        if pcr is None:
            result['summary'] = '无法计算PCR值'
            return result
        
        # 信号判断（阈值与score_pcr统一：成交量口径）
        if pcr >= 1.5:
            result['signal'] = 'red'
            result['label'] = f'🔴 极端恐慌（PCR={pcr:.2f}）'
            result['summary'] = f'PCR={pcr:.2f}，认沽期权交易极度活跃，市场恐慌'
        elif pcr >= 1.2:
            result['signal'] = 'red'
            result['label'] = f'🟠 偏恐慌（PCR={pcr:.2f}）'
            result['summary'] = f'PCR={pcr:.2f}，认沽期权交易活跃，市场偏恐慌'
        elif pcr >= 0.7:
            result['signal'] = 'yellow'
            result['label'] = f'⚪ 情绪中性（PCR={pcr:.2f}）'
            result['summary'] = f'PCR={pcr:.2f}，期权市场情绪中性'
            # 1.0-1.2区间视为情绪修复
            if 1.0 <= pcr <= 1.2:
                result['signal'] = 'green'
                result['label'] = f'🟢 情绪修复（PCR={pcr:.2f}）'
                result['summary'] = f'PCR={pcr:.2f}，市场情绪从恐慌中修复'
        else:
            result['signal'] = 'green'
            result['label'] = f'🟢 乐观（PCR={pcr:.2f}）'
            result['summary'] = f'PCR={pcr:.2f}，认购期权交易活跃，市场乐观'
    
    except Exception as e:
        result['summary'] = f'计算失败: {e}'
    
    return result


def _calc_pcr_from_contracts(option_data):
    """从期权合约数据计算PCR（Put-Call Ratio）。"""
    try:
        import pandas as pd
        
        contracts = None
        daily = None
        
        if isinstance(option_data, dict):
            contracts = option_data.get('contracts')
            daily = option_data.get('daily')
        elif isinstance(option_data, pd.DataFrame):
            contracts = option_data
        
        # 优先用成交量计算PCR
        if daily is not None and len(daily) > 0:
            df = daily
            call_col = None
            put_col = None
            vol_col = None
            
            # 尝试找认购/认沽列
            for col in df.columns:
                cl = str(col).lower()
                if 'type' in cl or '期权类型' in str(col) or 'call_put' in cl:
                    type_col = col
                if 'volume' in cl or '成交量' in str(col):
                    vol_col = col
                if 'oi' in cl or '持仓量' in str(col) or 'open_int' in cl:
                    oi_col = col
            
            # 区分认购和认沽
            # 简化：从名称或类型列判断
            call_vol = 0
            put_vol = 0
            
            # 尝试从call_put列区分
            if 'call_put' in df.columns:
                call_data = df[df['call_put'] == 'C']
                put_data = df[df['call_put'] == 'P']
                if vol_col and vol_col in df.columns:
                    call_vol = call_data[vol_col].sum()
                    put_vol = put_data[vol_col].sum()
            else:
                # 从名称判断
                for idx, row in df.iterrows():
                    name = ''
                    for col in ['name', '合约简称', 'opt_name']:
                        if col in df.columns:
                            name = str(row.get(col, ''))
                            break
                    
                    vol = 0
                    if vol_col and vol_col in df.columns:
                        vol = _safe_num(row.get(vol_col), 0)
                    
                    if '购' in name or 'call' in name.lower() or 'C' in name:
                        call_vol += vol
                    elif '沽' in name or 'put' in name.lower() or 'P' in name:
                        put_vol += vol
            
            if call_vol > 0:
                return put_vol / call_vol
        
        # 用持仓量计算
        if contracts is not None and len(contracts) > 0:
            # 简化：返回None，因为难以准确区分
            pass
        
        return None
    except Exception:
        return None


# ============================================================
#  ④ 热门板块持仓监控（公募持仓估算）
# ============================================================

def calc_sector_holding_signal(trade_date):
    """计算热门板块持仓信号（估算值）。
    
    标的：CPO/AI硬件/半导体板块
    说明：日度公募持仓难以获取，用「板块成交额占全市场比例」作为代理指标
    信号：🟢从峰值降>5pct且<15% | 🟡15%-20% | 🔴>20%或龙头>25%
    
    Returns:
        dict: {signal, label, top_sectors, total_flow_pct, summary, is_estimate: True}
    """
    result = {
        'signal': None,
        'label': '⚪ 数据缺失',
        'top_sectors': [],
        'total_flow_pct': None,
        'summary': '',
        'data_available': False,
        'is_estimate': True,  # 标记为估算值
    }
    
    if not _datahub_ready:
        result['summary'] = 'data_hub不可用（估算值）'
        return result
    
    try:
        # 尝试从 data_hub 获取概念板块资金流
        sector_df = get_data('sector_flow', sector_type='concept', trade_date=trade_date)
        
        if sector_df is None or len(sector_df) == 0:
            # 降级：用全市场行情数据估算热门板块占比
            # 从stock_daily全市场数据中估算科技板块占比
            result['summary'] = '板块资金流数据获取失败，使用成交额占比估算'
            # 这里可以用全市场数据进一步估算，暂时返回数据缺失
            # 但至少给出一个中性信号
            result['signal'] = 'yellow'
            result['label'] = '🟡 中性（估算）'
            result['summary'] = '板块资金流数据暂缺，热门板块持仓为中性估算'
            return result
        
        # 筛选热门科技板块
        tech_sectors = []
        for _, row in sector_df.iterrows():
            sector_name = str(row.get('sector_name', ''))
            is_hot = any(kw in sector_name for kw in HOT_TECH_SECTORS)
            if is_hot:
                tech_sectors.append({
                    'name': sector_name,
                    'flow_pct': _safe_num(row.get('flow_pct_of_total'), 0),
                    'main_inflow': _safe_num(row.get('main_net_inflow'), 0),
                    'change_pct': _safe_num(row.get('change_pct'), 0),
                })
        
        if not tech_sectors:
            result['summary'] = '未匹配到热门科技板块'
            result['signal'] = 'yellow'
            result['label'] = '🟡 中性（估算）'
            return result
        
        # 计算热门科技板块合计成交额占比
        total_flow_pct = sum(s['flow_pct'] for s in tech_sectors)
        result['total_flow_pct'] = total_flow_pct
        result['top_sectors'] = sorted(tech_sectors, key=lambda x: x['flow_pct'], reverse=True)[:5]
        result['data_available'] = True
        
        # 信号判断
        if total_flow_pct > 0.20:
            result['signal'] = 'red'
            result['label'] = f'🔴 过度拥挤（{total_flow_pct*100:.1f}%）'
            result['summary'] = f'热门科技板块成交额占比{total_flow_pct*100:.1f}%，板块过度拥挤'
        elif total_flow_pct >= 0.15:
            result['signal'] = 'yellow'
            result['label'] = f'🟡 偏高（{total_flow_pct*100:.1f}%）'
            result['summary'] = f'热门科技板块成交额占比{total_flow_pct*100:.1f}%，持仓集中度偏高'
        else:
            result['signal'] = 'green'
            result['label'] = f'🟢 健康（{total_flow_pct*100:.1f}%）'
            result['summary'] = f'热门科技板块成交额占比{total_flow_pct*100:.1f}%，持仓分布健康'
    
    except Exception as e:
        result['summary'] = f'计算失败: {e}'
        result['signal'] = 'yellow'
        result['label'] = '🟡 中性（估算）'
    
    return result


# ============================================================
#  ⑤ 杠杆水平监控（两融）
# ============================================================

def calc_margin_signal(trade_date):
    """计算两融杠杆水平信号。
    
    数据：沪深两市融资余额、融资买入额占成交额比例
    信号：🟢从高位回落>10%或融资买入占比<7% | 🟡7%-10% | 🔴>3万亿且创新高或融资买入占比>12%
    
    Returns:
        dict: {signal, label, margin_balance, margin_buy_pct, trend, summary}
    """
    result = {
        'signal': None,
        'label': '⚪ 数据缺失',
        'margin_balance': None,
        'margin_buy_pct': None,
        'trend': '',
        'summary': '',
        'data_available': False,
    }
    
    if not _datahub_ready:
        result['summary'] = 'data_hub不可用'
        return result
    
    try:
        # 获取最近20天两融数据
        df = get_data('margin_trading', market='all', lookback_days=20)
        
        if df is None or len(df) < 3:
            result['summary'] = f'两融数据不足（{len(df) if df is not None else 0}天）'
            return result
        
        df_sorted = df.sort_values('trade_date').reset_index(drop=True)
        
        # 融资余额（两融余额）
        balance_col = None
        for col in ['rzrqye', 'rzye', '融资融券余额', '融资余额']:
            if col in df_sorted.columns:
                balance_col = col
                break
        
        buy_col = None
        for col in ['rzmre', '融资买入额']:
            if col in df_sorted.columns:
                buy_col = col
                break
        
        if balance_col is None:
            result['summary'] = '无法识别融资余额列'
            return result
        
        latest_balance = _safe_num(df_sorted.iloc[-1][balance_col])
        prev_balance = _safe_num(df_sorted.iloc[-2][balance_col]) if len(df_sorted) >= 2 else None
        
        result['margin_balance'] = latest_balance
        result['data_available'] = True
        
        # 计算趋势（最近5天）
        balance_5d_ago = _safe_num(df_sorted.iloc[-5][balance_col]) if len(df_sorted) >= 5 else None
        balance_20d_high = df_sorted[balance_col].max() if len(df_sorted) > 0 else None
        
        # 融资买入占比（需要总成交额，这里简化用融资余额变化估算）
        # 完整版本需要全市场成交额数据
        margin_buy_pct = None
        if latest_balance and balance_5d_ago and latest_balance > 0:
            # 用融资余额5日变化率作为杠杆活跃度的代理
            margin_buy_pct = (latest_balance - balance_5d_ago) / latest_balance * 100
        
        result['margin_buy_pct'] = margin_buy_pct
        
        # 趋势判断
        is_new_high = (balance_20d_high is not None 
                      and latest_balance is not None 
                      and latest_balance >= balance_20d_high * 0.99)
        
        # 阈值：3万亿（3e12）
        THRESHOLD_HIGH = 3e12  # 3万亿
        balance_pct_of_high = None
        if balance_20d_high and balance_20d_high > 0 and latest_balance:
            balance_pct_of_high = (balance_20d_high - latest_balance) / balance_20d_high
        
        # 信号判断
        if latest_balance > THRESHOLD_HIGH and is_new_high:
            result['signal'] = 'red'
            result['label'] = f'🔴 杠杆过高（{latest_balance/1e12:.2f}万亿，创新高）'
            result['trend'] = 'over_leverage'
            result['summary'] = f'两融余额{latest_balance/1e12:.2f}万亿创近期新高，杠杆水平过高'
        elif balance_pct_of_high is not None and balance_pct_of_high > 0.10:
            result['signal'] = 'green'
            result['label'] = f'🟢 杠杆去化（较峰值降{balance_pct_of_high*100:.1f}%）'
            result['trend'] = 'deleveraging'
            result['summary'] = f'两融余额较峰值下降{balance_pct_of_high*100:.1f}%，杠杆风险释放'
        elif latest_balance > THRESHOLD_HIGH * 0.9:
            result['signal'] = 'yellow'
            result['label'] = f'🟡 杠杆偏高（{latest_balance/1e12:.2f}万亿）'
            result['trend'] = 'high'
            result['summary'] = f'两融余额{latest_balance/1e12:.2f}万亿，处于较高水平'
        else:
            result['signal'] = 'green'
            result['label'] = f'🟢 杠杆健康（{latest_balance/1e12:.2f}万亿）'
            result['trend'] = 'healthy'
            result['summary'] = f'两融余额{latest_balance/1e12:.2f}万亿，杠杆水平健康'
    
    except Exception as e:
        result['summary'] = f'计算失败: {e}'
    
    return result


# ============================================================
#  ⑥ 量价关系监控
# ============================================================

def calc_volume_price_signal(trade_date):
    """计算量价关系信号。
    
    标的：中证1000、沪深300日K线+成交量
    计算：缩量回调后放量阳线、缩量创新高、放量长阴破20日均线
    信号：🟢缩量回调后放量阳线 | 🟡温和波动 | 🔴缩量新高或放量长阴破20日线
    
    Returns:
        dict: {signal, label, indices: {name: pattern}, summary}
    """
    result = {
        'signal': None,
        'label': '⚪ 数据缺失',
        'indices': {},
        'summary': '',
        'data_available': False,
    }
    
    if not _datahub_ready:
        result['summary'] = 'data_hub不可用'
        return result
    
    indices_to_check = [
        ('000852.SH', '中证1000'),
        ('000300.SH', '沪深300'),
    ]
    
    idx_results = {}
    green_count = 0
    red_count = 0
    yellow_count = 0
    
    for ts_code, name in indices_to_check:
        try:
            # 获取最近30个交易日的指数日线数据
            df = get_data('stock_daily', ts_code=ts_code, lookback_days=30)
            
            if df is None or len(df) < 20:
                idx_results[name] = {
                    'error': f'数据不足（{len(df) if df is not None else 0}天）',
                }
                continue
            
            df_sorted = df.sort_values('trade_date').reset_index(drop=True)
            
            # 识别列名
            close_col = None
            vol_col = None
            open_col = None
            high_col = None
            low_col = None
            
            for col in df_sorted.columns:
                cl = str(col).lower()
                if col in ['close', '收盘', '收盘价']:
                    close_col = col
                elif col in ['vol', 'volume', '成交量']:
                    vol_col = col
                elif col in ['open', '开盘', '开盘价']:
                    open_col = col
                elif col in ['high', '最高', '最高价']:
                    high_col = col
                elif col in ['low', '最低', '最低价']:
                    low_col = col
            
            if close_col is None or vol_col is None:
                idx_results[name] = {'error': '无法识别价格/成交量列'}
                continue
            
            closes = df_sorted[close_col].values
            vols = df_sorted[vol_col].values
            
            # 计算5日均量
            if len(vols) >= 5:
                ma5_vol = np.mean(vols[-5:])
            else:
                ma5_vol = vols[-1]
            
            # 计算20日均线
            if len(closes) >= 20:
                ma20 = np.mean(closes[-20:])
            else:
                ma20 = np.mean(closes)
            
            latest_close = closes[-1]
            latest_vol = vols[-1]
            prev_close = closes[-2] if len(closes) >= 2 else latest_close
            
            # 当日涨跌幅
            daily_pct = (latest_close - prev_close) / prev_close if prev_close else 0
            
            # 量比（当日量 / 5日均量）
            vol_ratio = latest_vol / ma5_vol if ma5_vol > 0 else 1.0
            
            # 模式识别
            pattern = 'neutral'
            pattern_detail = ''
            
            # 🔴 放量长阴破20日线
            if (daily_pct < -0.02 and vol_ratio > 1.5 and 
                latest_close < ma20 and prev_close >= ma20):
                pattern = 'breakdown'
                pattern_detail = '放量长阴跌破20日线'
                idx_signal = 'red'
                red_count += 1
            
            # 🔴 缩量创新高
            elif (latest_close >= max(closes[:-1]) and 
                  vol_ratio < 0.8 and daily_pct > 0):
                pattern = 'divergence_high'
                pattern_detail = '缩量创新高（量价背离）'
                idx_signal = 'red'
                red_count += 1
            
            # 🟢 缩量回调后放量阳线
            elif (daily_pct > 0.015 and vol_ratio > 1.3 and 
                  len(closes) >= 6):
                # 检查前几天是否缩量回调
                prev5_closes = closes[-6:-1]
                prev5_vols = vols[-6:-1]
                # 前5天整体是否回调
                prev_trend = (prev5_closes[-1] - prev5_closes[0]) / prev5_closes[0]
                if prev_trend < -0.02:  # 前5天累计下跌>2%
                    pattern = 'reversal_up'
                    pattern_detail = '缩量回调后放量阳线'
                    idx_signal = 'green'
                    green_count += 1
                else:
                    pattern = 'strong_up'
                    pattern_detail = '放量上涨'
                    idx_signal = 'yellow'
                    yellow_count += 1
            else:
                pattern = 'normal'
                pattern_detail = '量价配合正常'
                idx_signal = 'yellow'
                yellow_count += 1
            
            idx_results[name] = {
                'close': latest_close,
                'ma20': ma20,
                'daily_pct': daily_pct,
                'vol_ratio': vol_ratio,
                'pattern': pattern,
                'pattern_detail': pattern_detail,
                'signal': idx_signal,
            }
        
        except Exception as e:
            idx_results[name] = {'error': str(e)}
    
    result['indices'] = idx_results
    
    # 综合判断
    valid_count = sum(1 for v in idx_results.values() if 'signal' in v)
    if valid_count == 0:
        result['summary'] = '暂无有效量价数据'
        return result
    
    result['data_available'] = True
    
    if green_count >= 1 and red_count == 0:
        result['signal'] = 'green'
        result['label'] = '🟢 量价健康'
        result['summary'] = f'{green_count}个指数出现看多量价形态'
    elif red_count >= 1:
        result['signal'] = 'red'
        result['label'] = '🔴 量价预警'
        result['summary'] = f'{red_count}个指数出现看空量价形态'
    else:
        result['signal'] = 'yellow'
        result['label'] = '🟡 量价中性'
        result['summary'] = '量价关系正常，无明显看多/看空信号'
    
    return result


# ============================================================
#  择时综合评级 + 操作建议
# ============================================================

def calc_timing_composite(dim_signals):
    """计算择时综合评级。
    
    规则：
    - 🟢买入：≥4个维度🟢 + 🔴≤1个
    - 🟡观望：🟢和🔴各2-3个，或全部🟡
    - 🔴减仓：≥4个维度🔴
    
    Args:
        dim_signals: dict {dim_name: {signal: 'green'/'yellow'/'red'/None, ...}}
    
    Returns:
        dict: {rating, rating_level, green_count, red_count, yellow_count, missing_count, advice}
    """
    dim_names = ['etf_flow', 'crowding', 'pcr', 'sector_holding', 'margin', 'volume_price']
    
    green_count = 0
    red_count = 0
    yellow_count = 0
    missing_count = 0
    
    for dim in dim_names:
        sig = dim_signals.get(dim, {}).get('signal')
        if sig == 'green':
            green_count += 1
        elif sig == 'red':
            red_count += 1
        elif sig == 'yellow':
            yellow_count += 1
        else:
            missing_count += 1
    
    # 有效维度数
    valid_count = 6 - missing_count
    
    # 评级判断
    if valid_count >= 4 and green_count >= 4 and red_count <= 1:
        rating = '🟢 买入'
        rating_level = 0  # 0=买入, 1=观望, 2=减仓
    elif valid_count >= 4 and red_count >= 4:
        rating = '🔴 减仓'
        rating_level = 2
    else:
        rating = '🟡 观望'
        rating_level = 1
    
    # 操作建议
    advice = generate_timing_advice(rating_level, dim_signals, green_count, red_count, yellow_count, missing_count)
    
    return {
        'rating': rating,
        'rating_level': rating_level,
        'green_count': green_count,
        'red_count': red_count,
        'yellow_count': yellow_count,
        'missing_count': missing_count,
        'valid_count': valid_count,
        'advice': advice,
    }


def generate_timing_advice(rating_level, dim_signals, green_count, red_count, yellow_count, missing_count):
    """生成择时操作建议（纯规则，零LLM）。
    
    输出格式：
    - 宽基指数建议
    - 热门板块建议
    - 整体仓位建议（X成）
    """
    # 仓位映射
    position_map = {
        0: '7-8成',  # 买入
        1: '4-6成',  # 观望
        2: '2-3成',  # 减仓
    }
    
    position = position_map.get(rating_level, '5成')
    
    # 宽基指数建议
    etf_signal = dim_signals.get('etf_flow', {}).get('signal', 'yellow')
    if etf_signal == 'green':
        index_advice = '宽基指数可逢低加仓，优先中证1000、科创50等成长方向'
    elif etf_signal == 'red':
        index_advice = '宽基指数暂规避，等待资金企稳信号'
    else:
        index_advice = '宽基指数持有为主，沪深300偏防御，中证1000偏弹性'
    
    # 热门板块建议
    sector_signal = dim_signals.get('sector_holding', {}).get('signal', 'yellow')
    if sector_signal == 'green':
        sector_advice = '科技板块拥挤度回落，可分批布局半导体、AI硬件等优质赛道'
    elif sector_signal == 'red':
        sector_advice = '热门板块过度拥挤，警惕回调风险，建议降低科技仓位'
    else:
        sector_advice = '热门板块持有为主，关注半导体、AI算力等有业绩支撑的方向'
    
    # 两融杠杆风险提示
    margin_signal = dim_signals.get('margin', {}).get('signal', 'yellow')
    margin_note = ''
    if margin_signal == 'red':
        margin_note = '⚠️ 两融余额偏高，注意控制杠杆风险'
    
    # 量价提示
    vp_signal = dim_signals.get('volume_price', {}).get('signal', 'yellow')
    vp_note = ''
    if vp_signal == 'green':
        vp_note = '📈 量价配合良好，趋势有望延续'
    elif vp_signal == 'red':
        vp_note = '📉 量价出现预警信号，注意控制仓位'
    
    notes = [n for n in [margin_note, vp_note] if n]
    
    return {
        'position': position,
        'index_advice': index_advice,
        'sector_advice': sector_advice,
        'additional_notes': notes,
    }


# ============================================================
#  统一入口：加载并计算全部择时监控数据
# ============================================================

def load_timing_monitor_data(trade_date, option_data=None, margin_data=None):
    """加载并计算全部三维择时监控数据。
    
    Args:
        trade_date: 交易日期 YYYYMMDD
        option_data: 已有期权数据（可选，用于PCR计算）
        margin_data: 已有两融风险监控数据（可选，来自compute_margin_signals，避免两套结论）
    
    Returns:
        dict: {
            dimensions: {etf_flow, crowding, pcr, sector_holding, margin, volume_price},
            composite: 综合评级结果,
            data_available: bool
        }
    """
    print("\n--- 三维择时监控 ---")
    
    dims = {}
    
    # ① ETF资金流
    print("  ① ETF资金流监控...", end=" ")
    dims['etf_flow'] = calc_etf_flow_signal(trade_date)
    print(dims['etf_flow']['label'])
    
    # ② 市场拥挤度
    print("  ② 市场拥挤度监控...", end=" ")
    dims['crowding'] = calc_crowding_signal(trade_date)
    print(dims['crowding']['label'])
    
    # ③ 期权情绪（PCR）
    print("  ③ 期权情绪监控（PCR）...", end=" ")
    dims['pcr'] = calc_pcr_signal(option_data)
    print(dims['pcr']['label'])
    
    # ④ 热门板块持仓（估算）
    print("  ④ 热门板块持仓监控...", end=" ")
    dims['sector_holding'] = calc_sector_holding_signal(trade_date)
    print(dims['sector_holding']['label'])
    
    # ⑤ 杠杆水平（两融）- 复用compute_margin_signals结果，避免与⑤两融风险监控矛盾
    print("  ⑤ 杠杆水平监控（两融）...", end=" ")
    if margin_data and margin_data.get('risk_level') != 'unknown':
        # 直接复用compute_margin_signals的结果，保证与⑤两融风险监控一致
        risk_level = margin_data.get('risk_level', 'unknown')
        advice = margin_data.get('advice', '')
        if risk_level == 'high':
            dims['margin'] = {
                'signal': 'red', 'label': f'🔴 杠杆风险偏高', 
                'summary': advice or '两融风险监控显示高风险',
                'data_available': True, 'unified_with_margin_monitor': True,
            }
        elif risk_level == 'medium':
            dims['margin'] = {
                'signal': 'yellow', 'label': f'🟡 杠杆风险中等',
                'summary': advice or '两融风险监控显示中等风险',
                'data_available': True, 'unified_with_margin_monitor': True,
            }
        else:
            dims['margin'] = {
                'signal': 'green', 'label': f'🟢 杠杆风险可控',
                'summary': advice or '两融风险监控显示低风险',
                'data_available': True, 'unified_with_margin_monitor': True,
            }
    else:
        dims['margin'] = calc_margin_signal(trade_date)
    print(dims['margin']['label'])
    
    # ⑥ 量价关系
    print("  ⑥ 量价关系监控...", end=" ")
    dims['volume_price'] = calc_volume_price_signal(trade_date)
    print(dims['volume_price']['label'])
    
    # 综合评级
    print("  🎯 择时综合评级...", end=" ")
    composite = calc_timing_composite(dims)
    print(composite['rating'])
    
    # 数据可用标记
    data_available = any(
        dims.get(d, {}).get('data_available', False)
        for d in ['etf_flow', 'crowding', 'pcr', 'sector_holding', 'margin', 'volume_price']
    )
    
    return {
        'dimensions': dims,
        'composite': composite,
        'data_available': data_available,
    }


# ============================================================
#  生成择时监控 Markdown 报告章节
# ============================================================

def generate_timing_markdown(timing_data):
    """生成择时监控的 Markdown 章节。
    
    Args:
        timing_data: load_timing_monitor_data 的返回值
    
    Returns:
        list[str]: Markdown 行列表
    """
    lines = []
    
    if not timing_data or not timing_data.get('data_available'):
        lines.append("### ⑤ 三维择时监控")
        lines.append("")
        lines.append("⚠️ 择时监控数据暂不可用")
        lines.append("")
        return lines
    
    dims = timing_data['dimensions']
    comp = timing_data['composite']
    
    lines.append("### ⑤ 三维择时监控")
    lines.append("")
    
    # 综合评级
    lines.append(f"**择时综合评级：{comp['rating']}**")
    lines.append("")
    lines.append(f"- 🟢看多：{comp['green_count']}个维度")
    lines.append(f"- 🟡中性：{comp['yellow_count']}个维度")
    lines.append(f"- 🔴看空：{comp['red_count']}个维度")
    if comp['missing_count'] > 0:
        lines.append(f"- ⚪数据缺失：{comp['missing_count']}个维度")
    lines.append("")
    
    # 操作建议
    advice = comp['advice']
    lines.append("**📋 操作建议**")
    lines.append("")
    lines.append(f"- **整体仓位**：{advice['position']}")
    lines.append(f"- **宽基指数**：{advice['index_advice']}")
    lines.append(f"- **热门板块**：{advice['sector_advice']}")
    if advice['additional_notes']:
        for note in advice['additional_notes']:
            lines.append(f"- {note}")
    lines.append("")
    
    # 6个维度明细
    lines.append("**各维度明细**")
    lines.append("")
    lines.append("| 维度 | 信号 | 说明 |")
    lines.append("|------|------|------|")
    
    dim_info = [
        ('etf_flow', '① ETF资金流'),
        ('crowding', '② 市场拥挤度'),
        ('pcr', '③ 期权情绪(PCR)'),
        ('sector_holding', '④ 热门板块持仓'),
        ('margin', '⑤ 杠杆水平(两融)'),
        ('volume_price', '⑥ 量价关系'),
    ]
    
    for dim_key, dim_name in dim_info:
        dim = dims.get(dim_key, {})
        label = dim.get('label', '⚪ 数据缺失')
        summary = dim.get('summary', '')
        # 如果是估算值，标注
        if dim.get('is_estimate'):
            summary = f'【估算】{summary}'
        lines.append(f"| {dim_name} | {label} | {summary} |")
    
    lines.append("")
    
    # ETF资金流详细
    etf_dim = dims.get('etf_flow', {})
    if etf_dim.get('data_available') and etf_dim.get('etfs'):
        lines.append("<details>")
        lines.append("<summary>ETF资金流详情</summary>")
        lines.append("")
        lines.append("| ETF代码 | 名称 | 最新份额 | 日变化 | 连续天数 | 周变化 | 趋势 |")
        lines.append("|---------|------|----------|--------|----------|--------|------|")
        for ts_code, info in etf_dim['etfs'].items():
            if 'error' in info:
                lines.append(f"| {ts_code} | {info.get('name','')} | - | - | - | - | {info['error']} |")
                continue
            name = info.get('name', '')
            share = info.get('latest_share', 0)
            share_str = f"{share/1e8:.1f}亿份" if share else '-'
            daily_pct = info.get('daily_change_pct')
            daily_str = f"{daily_pct*100:+.2f}%" if daily_pct is not None else '-'
            streak = info.get('streak_days', 0)
            streak_dir = info.get('streak_dir', 0)
            streak_str = f"{'↑' if streak_dir > 0 else '↓' if streak_dir < 0 else '→'}{streak}天"
            weekly_pct = info.get('weekly_change_pct')
            weekly_str = f"{weekly_pct*100:+.2f}%" if weekly_pct is not None else '-'
            trend = info.get('trend', '')
            lines.append(f"| {ts_code} | {name} | {share_str} | {daily_str} | {streak_str} | {weekly_str} | {trend} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    
    return lines


if __name__ == '__main__':
    main()
