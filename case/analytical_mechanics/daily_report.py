#!/usr/bin/env python3
"""
分析力学量化交易系统 — 每日日报生成与飞书推送

每个交易日18:00自动执行：
1. 拉取沪深300 + 21个申万三级行业最新日线数据
2. 计算混沌特征（Hurst/LLE/Toroidality）
3. 生成明日多空观点 + 行业买卖建议
4. 通过飞书webhook发送交互式卡片

依赖：akshare, numpy, pandas, scipy
"""

import os, sys, json, time, pickle, subprocess, shutil, requests
import socket
socket.setdefaulttimeout(30)
_orig_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kw: [r for r in _orig_getaddrinfo(*args, **kw) if r[0] == socket.AF_INET]
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
# 指数日线与申万行业日线全部改由 data_hub 提供，本文件不再直连 akshare。
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_INDEX_TS = {
    'sh000300': '000300.SH', 'sh000001': '000001.SH', 'sh000905': '000905.SH',
    'sh000852': '000852.SH', 'sh000985': '000985.CSI', 'sh000688': '000688.SH',
    'sz399001': '399001.SZ', 'sz399006': '399006.SZ',
}


def _hub_index_daily(symbol):
    """DataHub 股票模块指数日线，返回列 date(datetime)/open/high/low/close/volume。"""
    from data_hub.collectors.stocks import get_index_daily
    ts_code = _INDEX_TS.get(symbol, symbol)
    df = get_index_daily(ts_code=ts_code, start_date='20190101',
                         end_date=datetime.now().strftime('%Y%m%d'))
    if df is None or len(df) == 0:
        raise RuntimeError(f'dataHub 指数日线为空 {symbol}')
    out = pd.DataFrame({
        'date': pd.to_datetime(df['trade_date'].astype(str)),
        'open': pd.to_numeric(df['open'], errors='coerce'),
        'high': pd.to_numeric(df['high'], errors='coerce'),
        'low': pd.to_numeric(df['low'], errors='coerce'),
        'close': pd.to_numeric(df['close'], errors='coerce'),
        'volume': pd.to_numeric(df.get('vol'), errors='coerce'),
    })
    return out.sort_values('date').reset_index(drop=True)


def _hub_sw_daily(code):
    """DataHub 股票模块申万行业日线，返回列 date(datetime)/close/open/high/low/volume。"""
    from data_hub.collectors.stocks import fetch_sw_index_daily
    df = fetch_sw_index_daily(str(code), start_date='20190101',
                              end_date=datetime.now().strftime('%Y%m%d'))
    if df is None or len(df) == 0:
        raise RuntimeError(f'dataHub 申万行业日线为空 {code}')
    out = pd.DataFrame({
        'date': pd.to_datetime(df['trade_date'].astype(str)),
        'open': pd.to_numeric(df['open'], errors='coerce'),
        'high': pd.to_numeric(df['high'], errors='coerce'),
        'low': pd.to_numeric(df['low'], errors='coerce'),
        'close': pd.to_numeric(df['close'], errors='coerce'),
        'volume': pd.to_numeric(df.get('vol'), errors='coerce'),
    })
    return out.sort_values('date').reset_index(drop=True)


# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''

_deps = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.deps')
if os.path.isdir(_deps):
    sys.path.insert(0, _deps)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hamiltonian_chaos_system import FeatureEngine, RiskManager

# ============================================================
# 配置
# ============================================================

FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# 邮件提醒配置：不看 5 日/20 日涨跌（看图即可，不构成邮件理由），
# 只在结构化风险项累计达到阈值时自动发邮件提醒
# 收件人（可通过环境变量 DAILY_REPORT_EMAIL_TO 覆盖）
EMAIL_TO = os.environ.get("DAILY_REPORT_EMAIL_TO", "410119229@qq.com")
# 结构化风险项数量阈值："两个以上"按含本数计，即 >=2 触发
RISK_ALERT_THRESHOLD = int(os.environ.get("RISK_ALERT_THRESHOLD", "2"))
# 均线破位风险要求的连续在 MA20 下方天数
MA_BREAK_DAYS = int(os.environ.get("MA_BREAK_DAYS", "5"))
# 结构风险阈值：Hurst 低于该值视为趋势记忆弱化，LLE 高于该值视为结构失序，
# Tor 高于该值视为拓扑失稳
RISK_HURST_LOW = 0.45
RISK_LLE_HIGH = 0.065
RISK_TOR_HIGH = 0.55

# 26个申万行业（24个三级 + 2个二级）：含农业/农产品与液冷映射行业
SW_INDUSTRIES = [
    ('850339', '其他化学制品', '化工'),
    ('850333', '农药', '化工'),
    ('850551', '铝', '有色'),
    ('850822', '印制电路板', '电子'),
    ('850831', '面板', '电子'),
    ('850832', 'LED', '电子'),
    ('850841', '其他电子', '电子'),
    ('851511', '原料药', '医药'),
    ('851512', '化学制剂', '医药'),
    ('851521', '中药', '医药'),
    ('851811', '住宅开发', '地产'),
    ('851931', '证券', '金融'),
    ('857344', '线缆部件及其他', '电力设备'),
    ('850716', '其他通用设备', '机械'),
    ('850731', '仪器仪表', '机械'),
    ('850751', '金属制品', '机械'),
    ('850725', '能源及重型设备', '机械'),
    ('850727', '其他专用设备', '机械'),
    ('850936', '轨交设备', '机械'),
    ('857421', '航空装备', '军工'),
    ('852226', 'IT服务', '计算机'),
    # 农业/农产品（申万三级）
    ('850111', '种子', '农林牧渔'),
    ('850154', '其他农产品加工', '农林牧渔'),
    ('850122', '水产养殖', '农林牧渔'),
    # 液冷映射（申万二级：液冷服务器/数据中心温控，通信设备含工业富联，专用设备含英维克/高澜）
    ('801102', '通信设备', '通信'),
    ('801074', '专用设备', '机械'),
]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_cache')

# 宽基指数列表（沪深300为主分析，这里列出其余4个：中证500/中证1000/科创50/创业板指）
BROAD_INDICES = [
    ('sh000905', '中证500'),
    ('sh000852', '中证1000'),
    ('sh000688', '科创50'),
    ('sz399006', '创业板指'),
]


# ============================================================
# 数据获取
# ============================================================

def fetch_market_data():
    """获取沪深300最新数据"""
    try:
        df = _hub_index_daily('sh000300')
        df = df.rename(columns={'date': 'date'})
        df['date'] = pd.to_datetime(df['date'])
        df = df[df['date'] >= '2019-01-01'].copy()
        df = df.sort_values('date').reset_index(drop=True)
        # 标准列名
        if 'volume' not in df.columns and 'amount' in df.columns:
            df['volume'] = df['amount']
        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col not in df.columns:
                df[col] = df.iloc[:, 1]
        print(f"  沪深300: {len(df)}条, 最新日期{df['date'].iloc[-1].date()}")
        return df
    except Exception as e:
        print(f"  沪深300获取失败: {e}")
        # 尝试缓存
        cache_path = os.path.join(CACHE_DIR, 'stock_data.pkl')
        if os.path.exists(cache_path):
            with open(cache_path, 'rb') as f:
                cached = pickle.load(f)
            if 'market' in cached:
                cached_market = cached['market']
                # 新鲜度校验：仅当缓存最新日期 == 今天才复用，陈旧或未来日期一律拒绝并告警（防静默使用旧数据）
                try:
                    latest_date = pd.to_datetime(cached_market['date']).iloc[-1].date()
                    today = datetime.now().date()
                    if latest_date == today:
                        print(f"  使用缓存的大盘数据（最新 {latest_date}）")
                        return cached_market
                    print(f"  WARNING: 大盘缓存数据已过期（最新 {latest_date}，今天 {today}），拒绝使用陈旧缓存")
                except Exception as ce:
                    print(f"  WARNING: 大盘缓存新鲜度校验失败({ce})，拒绝使用缓存")
        return None


def fetch_index_data(symbol, name):
    """获取宽基指数数据（与沪深300同源，腾讯接口）"""
    try:
        df = _hub_index_daily(symbol)
        df = df.rename(columns={'date': 'date'})
        df['date'] = pd.to_datetime(df['date'])
        df = df[df['date'] >= '2019-01-01'].copy()
        df = df.sort_values('date').reset_index(drop=True)
        if 'volume' not in df.columns and 'amount' in df.columns:
            df['volume'] = df['amount']
        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col not in df.columns:
                df[col] = df.iloc[:, 1]
        print(f"  {name}: {len(df)}条, 最新日期{df['date'].iloc[-1].date()}")
        return df
    except Exception as e:
        print(f"  {name}({symbol})获取失败: {e}")
        return None


def fetch_industry_data(code, name, retries=4):
    """获取单个行业指数数据（申万swsresearch接口偶发超时/空响应，内置重试）

    该接口每次返回全量历史(约1.5MB)、单次耗时数秒~十几秒，且会随机出现
    ReadTimeout 或 200空响应(JSONDecodeError)。历史日志显示每轮约1~4个行业
    会被这种瞬时故障命中，若不做重试就会被静默丢弃。此处按指数退避重试，
    实测首次重试即可恢复。
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            df = _hub_sw_daily(code)
            df = df.rename(columns={
                '日期': 'date', '收盘': 'close', '开盘': 'open',
                '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
            })
            df['date'] = pd.to_datetime(df['date'])
            df = df[df['date'] >= '2019-01-01'].copy()
            df = df.sort_values('date').reset_index(drop=True)
            if len(df) >= 1500:
                return df
            last_err = f"数据不足({len(df)}条)"
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(0.8 * attempt)
    print(f"  {code} {name} 获取失败(已重试{retries}次): {last_err}")
    return None


def load_or_fetch_industry_data():
    """加载或获取行业数据"""
    # 尝试使用缓存的特征数据（当天已计算过）
    feat_cache = os.path.join(CACHE_DIR, 'sw_industry_features.pkl')
    raw_cache = os.path.join(CACHE_DIR, 'sw_industry_data.pkl')
    
    # 检查缓存是否是今天的且行业集合与当前行业池一致（防止行业池变动后误用旧缓存）
    use_cache = False
    cached = None
    if os.path.exists(raw_cache):
        mtime = datetime.fromtimestamp(os.path.getmtime(raw_cache))
        try:
            with open(raw_cache, 'rb') as f:
                cached = pickle.load(f)
            cached_keys = set(cached.keys())
            expected_keys = {c for c, _, _ in SW_INDUSTRIES}
            cache_matches_pool = cached_keys == expected_keys
            # 数据内容校验：每只行业指数缓存的最新日期必须 == 今天（交易日盘后运行即当日数据）
            # 严格口径：绝不回退旧数据；akshare 盘后更新慢导致当日数据未到位则按缺失重拉
            if cache_matches_pool:
                last_dates = []
                for code, info in cached.items():
                    data = info['data']
                    last_dates.append(pd.to_datetime(data['date']).max())
                today = datetime.now().date()
                content_fresh = bool(last_dates) and all(
                    d.date() == today for d in last_dates)
                cache_matches_pool = content_fresh
                if not content_fresh:
                    print("缓存内容非今日数据（行业指数最新日期 != 今天），视为过期重拉")
        except Exception:
            cache_matches_pool = False
        if mtime.date() == datetime.now().date() and cache_matches_pool:
            use_cache = True
    
    if use_cache:
        print("使用今日缓存的行业数据...")
        industry_data = {}
        for code, info in cached.items():
            industry_data[code] = info['data']
        return industry_data
    
    # 重新获取（两轮：首轮全量 + 对缺失行业补拉，防止瞬时失败被静默丢弃）
    print("获取行业最新数据...")
    industry_data = {}

    def _fetch_round(codes_list, base_sleep):
        for code, name, sector in codes_list:
            df = fetch_industry_data(code, name)
            if df is not None:
                industry_data[code] = df
                print(f"  {code} {name}: {len(df)}条")
            time.sleep(base_sleep)

    _fetch_round(SW_INDUSTRIES, 0.3)

    # 缺失校验 + 第二轮补拉
    missing = [(c, n, s) for c, n, s in SW_INDUSTRIES if c not in industry_data]
    if missing:
        print(f"WARNING: 首轮缺失 {len(missing)} 个行业，第二轮补拉: "
              + ", ".join(f"{c}{n}" for c, n, _ in missing))
        time.sleep(1.0)
        _fetch_round(missing, 1.0)
        still_missing = [(c, n, s) for c, n, s in SW_INDUSTRIES if c not in industry_data]
        if still_missing:
            print(f"WARNING: 仍有 {len(still_missing)} 个行业获取失败，本次日报将缺失: "
                  + ", ".join(f"{c}{n}" for c, n, _ in still_missing))
    
    # 保存缓存
    os.makedirs(CACHE_DIR, exist_ok=True)
    name_map = {c: n for c, n, s in SW_INDUSTRIES}
    cache_data = {code: {'name': name_map.get(code, code), 'data': df} for code, df in industry_data.items()}
    with open(raw_cache, 'wb') as f:
        pickle.dump(cache_data, f)
    
    return industry_data


# ============================================================
# 特征计算与信号生成
# ============================================================

def compute_features(data_dict):
    """计算混沌特征（日报模式：只算最新窗口，提速~1600x）"""
    # 短周期特征窗口：Hurst 40个交易日（约两个月，贴近近期热点）；LLE 60日（40日样本不足以稳定计算 Rosenstein LLE）
    fe = FeatureEngine(hurst_window=40, lle_window=60, tor_window=40)
    features = {}
    for code, df in data_dict.items():
        df = df.copy()
        df = fe.compute_all_features(df, latest_only=True)
        features[code] = df
    return features


def analyze_market(market_df):
    """分析大盘状态"""
    # 短周期特征窗口：Hurst 40日（贴近近期热点），LLE 60日（40日样本不足以稳定计算）
    fe = FeatureEngine(hurst_window=40, lle_window=60, tor_window=40)
    rm = RiskManager()
    
    df = fe.compute_all_features(market_df.copy())
    latest = df.iloc[-1]
    
    lle = latest.get('lle', 0.05)
    if pd.isna(lle):
        lle = 0.05
    tor = latest.get('toroidality', 0.25)
    if pd.isna(tor):
        tor = 0.25
    hurst = latest.get('hurst', 0.5)
    if pd.isna(hurst):
        hurst = 0.5
    
    close = latest['close']
    ma20 = latest.get('ma20', close)
    if pd.isna(ma20):
        ma20 = close
    above_ma20 = close > ma20 if ma20 > 0 else True
    dev_ma20 = (close - ma20) / ma20 if ma20 > 0 else 0
    
    # 连续在MA20下方天数
    below_days = 0
    for i in range(len(df)-1, -1, -1):
        ma = df.iloc[i].get('ma20', df.iloc[i]['close'])
        if ma > 0 and df.iloc[i]['close'] < ma:
            below_days += 1
        else:
            break
    
    risk = rm.check_market_risk(lle, tor, hurst, above_ma20, dev_ma20, below_ma20_days=below_days)
    
    # 多空观点
    if risk == 'DANGER':
        outlook = '偏空'
        outlook_detail = '混沌指标显示趋势崩塌风险，建议减仓避险'
        if not above_ma20 and hurst < 0.45:
            outlook_detail = '高Tor+低Hurst+均线下方，有序下跌趋势，建议清仓'
        elif above_ma20 and hurst < 0.45 and lle > 0.065:
            outlook_detail = '顶部反转信号！价格在均线上方但Hurst崩塌，趋势即将反转'
    elif risk == 'WARNING':
        outlook = '谨慎'
        outlook_detail = '市场出现混沌信号但方向不明，建议控制仓位'
    else:
        if hurst > 0.58 and above_ma20:
            outlook = '偏多'
            outlook_detail = '趋势记忆强+均线上方，上涨趋势延续'
            if lle > 0.065 and tor < 0.20:
                outlook_detail = '爆拉前奏信号！高混沌+高Hurst+低Tor，可能即将加速上涨'
            elif tor > 0.22:
                outlook_detail = '趋势加速！高Hurst+高Tor，动量强劲'
        elif hurst < 0.45 and not above_ma20:
            outlook = '底部区域'
            outlook_detail = '低Hurst+均线下方，可能接近底部反弹区'
        else:
            outlook = '中性'
            outlook_detail = '混沌指标中性，无明显方向信号'
    
    # 近5天涨跌
    if len(df) >= 6:
        ret_5d = df['close'].iloc[-1] / df['close'].iloc[-6] - 1
    else:
        ret_5d = 0
    
    # 近20天涨跌
    if len(df) >= 21:
        ret_20d = df['close'].iloc[-1] / df['close'].iloc[-21] - 1
    else:
        ret_20d = 0
    
    return {
        'date': latest['date'],
        'close': close,
        'hurst': hurst,
        'lle': lle,
        'tor': tor,
        'above_ma20': above_ma20,
        'dev_ma20': dev_ma20,
        'risk': risk,
        'outlook': outlook,
        'outlook_detail': outlook_detail,
        'ret_5d': ret_5d,
        'ret_20d': ret_20d,
        'below_days': below_days,
    }


def analyze_broad_indices():
    """用 analyze_market 同样逻辑分析多个宽基指数，生成多空观点"""
    results = []
    for symbol, name in BROAD_INDICES:
        df = fetch_index_data(symbol, name)
        if df is None or len(df) < 200:
            print(f"  {name} 数据不足，跳过")
            continue
        try:
            info = analyze_market(df)
            info['name'] = name
            results.append(info)
            print(f"  {name}: {info['outlook']} | H={info['hurst']:.3f} LLE={info['lle']:.4f} Tor={info['tor']:.3f}")
        except Exception as e:
            print(f"  {name} 分析失败: {e}")
        time.sleep(0.3)
    return results


def analyze_industries(industry_features, market_info):
    """分析各行业并生成买卖建议"""
    market_risk = market_info['risk']
    results = []
    
    for code, df in industry_features.items():
        if len(df) < 105:
            continue
        
        latest = df.iloc[-1]
        H = latest.get('hurst', 0.5)
        if pd.isna(H):
            H = 0.5
        lle = latest.get('lle', 0.05)
        if pd.isna(lle):
            lle = 0.05
        tor = latest.get('toroidality', 0.2)
        if pd.isna(tor):
            tor = 0.2
        
        close = latest['close']
        ma20 = latest.get('ma20', close)
        if pd.isna(ma20):
            ma20 = close
        above_ma20 = close > ma20 if ma20 > 0 else True
        
        # 近5天/10天/20天涨跌（动量）
        closes = df['close'].values
        if len(df) >= 6:
            ret_5d = closes[-1] / closes[-6] - 1
        else:
            ret_5d = 0
        if len(df) >= 11:
            ret_10d = closes[-1] / closes[-11] - 1
        else:
            ret_10d = 0
        if len(df) >= 21:
            ret_20d = closes[-1] / closes[-21] - 1
        else:
            ret_20d = 0
        
        # 找行业名称
        name = ''
        sector = ''
        for c, n, s in SW_INDUSTRIES:
            if c == code:
                name = n
                sector = s
                break
        
        # 打分：以近期涨幅动量为核心（10日/20日涨幅），Hurst 仅作趋势确认修正
        score = 0
        signal = ''
        
        # 动量核心分（%）：10日涨幅权重2，20日涨幅权重1
        momentum_score = ret_10d * 200 + ret_20d * 100
        score = momentum_score
        # Hurst 修正：趋势显著加分，反转为反转减分（避免只信 Hurst 不看涨幅）
        if H > 0.55:
            score += 15
        elif H < 0.45:
            score -= 15
        
        # 信号标签（基于短周期Hurst与动量组合）
        if ret_5d > 0 and momentum_score > 10:
            if H > 0.55:
                signal = '动量+趋势共振'
            else:
                signal = '动量上行'
        elif ret_10d > 0 and ret_5d <= 0:
            signal = '短期回调'
        elif ret_20d < -0.03:
            signal = '趋势走弱'
        elif ret_10d < 0:
            signal = '动量下行'
        else:
            signal = '中性'
        
        # 过滤：Hurst 反转信号降权（LLE 异常叠加时重度降分）
        if lle > 0.065 and H < 0.45:
            signal = '顶部反转'
            score -= 20
        
        # 买卖建议：动量高分 + 近5日实际在涨才推买入/关注，下跌行业一律不推荐
        if market_risk == 'DANGER':
            action = '回避'
        elif market_risk == 'WARNING':
            if score > 30 and ret_5d > 0:
                action = '小仓位关注'
            elif score < -20:
                action = '减仓'
            else:
                action = '观望'
        else:  # CLEAR
            if score > 30 and ret_5d > 0 and ret_10d > 0:
                action = '买入'
            elif score > 15 and ret_5d > 0:
                action = '关注'
            elif score < -20 and ret_5d < 0:
                action = '回避'
            else:
                action = '持有'
        
        results.append({
            'code': code,
            'name': name,
            'sector': sector,
            'hurst': H,
            'lle': lle,
            'tor': tor,
            'above_ma20': above_ma20,
            'score': score,
            'signal': signal,
            'action': action,
            'ret_5d': ret_5d,
            'close': close,
        })
    
    # 按得分排序
    results.sort(key=lambda x: x['score'], reverse=True)
    return results


# ============================================================
# 龙头股获取 — 近两周涨幅最好的行业成份股
# ============================================================

def _fetch_stock_return(code, name, start_date, end_date):
    """获取单只股票近两周涨幅（内部函数，供线程池调用）"""
    try:
        from data_hub.collectors.stocks import get_stock_daily
        ts_code = f"{code}.{'SZ' if code.startswith(('0', '3')) else 'SH'}"
        df = get_stock_daily(ts_code, start_date=start_date, end_date=end_date)
        if df is not None and len(df) >= 2:
            closes = pd.to_numeric(df.sort_values('trade_date')['close'], errors='coerce').dropna()
            if len(closes) >= 2:
                ret = closes.iloc[-1] / closes.iloc[0] - 1
                return (code, name, ret)
    except Exception:
        pass
    return None


def fetch_top_stocks(industry_code, top_n=3):
    """获取行业近两周涨幅最好的成份股（龙头股）
    
    策略：从申万行业成份股中按权重取前20只，拉近两周行情，按涨幅排序取top_n。
    并行拉取以加速。
    """
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=14)).strftime('%Y%m%d')

    # 获取成份股：DataHub 股票模块指数成分（申万行业指数代码同口径）
    try:
        from data_hub.collectors.stocks import get_index_components
        codes = get_index_components(industry_code)[:20]
        from data_hub import edb
        basic = edb.get_dataset('CN.MARKET.STOCK.BASIC')
        name_map = dict(zip(basic['ts_code'].str.split('.').str[0], basic['name'])) if basic is not None and len(basic) else {}
        stocks = [(c.split('.')[0], name_map.get(c.split('.')[0], c.split('.')[0])) for c in codes]
    except Exception as e:
        print(f"    获取成份股失败 {industry_code}: {e}")
        return []
    
    # 并行拉取行情
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_fetch_stock_return, code, name, start_date, end_date): (code, name)
            for code, name in stocks
        }
        try:
            for future in as_completed(futures, timeout=30):
                try:
                    result = future.result(timeout=10)
                    if result:
                        results.append(result)
                except Exception:
                    pass
        except TimeoutError:
            # 部分请求超时，使用已获取的结果继续
            pass
    
    # 按涨幅排序取top_n
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:top_n]


def enrich_industries_with_leaders(industry_results):
    """为买入/关注/回避行业添加龙头股信息"""
    # 只为排名前10的行业获取龙头股（控制耗时）
    top_industries = industry_results[:10]
    
    print("\n  获取行业龙头股（近两周涨幅最佳）...")
    for ind in top_industries:
        code = ind['code']
        name = ind['name']
        action = ind['action']
        
        # 只为买入/关注/回避的行业获取龙头股
        if action not in ['买入', '小仓位关注', '关注', '回避', '减仓']:
            ind['leaders'] = []
            continue
        
        print(f"    {name}({action}) -> ", end='', flush=True)
        leaders = fetch_top_stocks(code, top_n=3)
        ind['leaders'] = leaders
        
        if leaders:
            leader_str = ', '.join([f"{n}({ret:+.1%})" for c, n, ret in leaders])
            print(leader_str)
        else:
            print('无')
        
        time.sleep(0.2)
    
    # 其余行业补空列表
    for ind in industry_results[10:]:
        ind['leaders'] = []
    
    return industry_results


# ============================================================
# 方向标记：看空 → 红色星标，看多 → 绿色月亮
# 逐处添加，同一指数/行业可出现多个标记
# ============================================================

BEAR_MARK = '🔴★'
BULL_MARK = '🟢☾'

BEAR_VIEWS = {'偏空', '谨慎'}
BULL_VIEWS = {'偏多', '底部区域'}
BEAR_RISKS = {'DANGER', 'WARNING'}
BEAR_ACTIONS = {'回避', '减仓'}
BULL_ACTIONS = {'买入', '小仓位关注'}
BEAR_SIGNALS = {'趋势走弱', '动量下行', '短期回调', '顶部反转'}
BULL_SIGNALS = {'动量+趋势共振', '动量上行'}


def _dir_mark(value):
    """按方向性取值返回标记：看空加红星标，看多加绿月亮，其余返回空串"""
    if value in BEAR_VIEWS or value in BEAR_RISKS or value in BEAR_ACTIONS or value in BEAR_SIGNALS:
        return BEAR_MARK
    if value in BULL_VIEWS or value in BULL_ACTIONS or value in BULL_SIGNALS:
        return BULL_MARK
    return ''


def _sign_mark(value):
    """按涨跌方向返回标记：上涨绿月亮，下跌红星标，零值不标"""
    try:
        if value > 0:
            return BULL_MARK
        if value < 0:
            return BEAR_MARK
    except (TypeError, ValueError):
        pass
    return ''


def _ma_mark(above_ma20):
    """均线位置标记：均线上方看多，下方看空"""
    return BULL_MARK if above_ma20 else BEAR_MARK


# ============================================================
# 飞书卡片生成与发送
# ============================================================

def build_feishu_card(market_info, industry_results, report_date, broad_index_outlooks=None):
    """构建飞书交互式卡片"""
    
    # 卡片头部颜色
    risk = market_info['risk']
    outlook = market_info['outlook']
    if risk == 'DANGER':
        header_color = 'red'
        header_icon = '🔴'
    elif risk == 'WARNING':
        header_color = 'orange'
        header_icon = '🟠'
    else:
        if outlook == '偏多':
            header_color = 'green'
            header_icon = '🟢'
        elif outlook == '底部区域':
            header_color = 'blue'
            header_icon = '🔵'
        else:
            header_color = 'grey'
            header_icon = '⚪'
    
    # 行业建议分组
    buy_list = [i for i in industry_results if i['action'] in ['买入', '小仓位关注']]
    watch_list = [i for i in industry_results if i['action'] in ['关注', '持有']]
    avoid_list = [i for i in industry_results if i['action'] in ['回避', '减仓']]
    
    # 构建行业建议文本
    def format_industry(ind):
        sig = ind['signal']
        return (f"{ind['name']}({ind['sector']}){_dir_mark(ind['action'])} "
                f"H={ind['hurst']:.2f} 5D={ind['ret_5d']:+.1%}{_sign_mark(ind['ret_5d'])} "
                f"[{sig}]{_dir_mark(sig)}")
    
    def format_leaders(ind):
        """格式化龙头股信息"""
        leaders = ind.get('leaders', [])
        if not leaders:
            return ''
        leader_str = '  '.join([f"{name} {ret:+.1%}" for code, name, ret in leaders])
        return f"\n    └ 龙头(2W): {leader_str}"
    
    buy_text = '\n'.join([f"▶ {format_industry(i)}{format_leaders(i)}" for i in buy_list[:5]]) if buy_list else '无'
    watch_text = '\n'.join([f"  {format_industry(i)}{format_leaders(i)}" for i in watch_list[:5]]) if watch_list else '无'
    avoid_text = '\n'.join([f"✕ {format_industry(i)}{format_leaders(i)}" for i in avoid_list[:5]]) if avoid_list else '无'
    
    # 宽基指数多空观点
    outlook_emoji = {'偏多': '🟢', '中性': '⚪', '偏空': '🔴', '底部区域': '🔵', '谨慎': '🟠'}
    if broad_index_outlooks:
        broad_lines = []
        for bi in broad_index_outlooks:
            emoji = outlook_emoji.get(bi['outlook'], '⚪')
            line = (f"{emoji} **{bi['name']}** 收盘{bi['close']:.2f} | "
                    f"**{bi['outlook']}**{_dir_mark(bi['outlook'])} | "
                    f"H={bi['hurst']:.3f} LLE={bi['lle']:.4f} Tor={bi['tor']:.3f} | "
                    f"MA20{'上' if bi['above_ma20'] else '下'}{_ma_mark(bi['above_ma20'])} "
                    f"5D={bi['ret_5d']:+.1%}{_sign_mark(bi['ret_5d'])}")
            broad_lines.append(line)
        broad_text = '\n'.join(broad_lines)
    else:
        broad_text = '无'
    
    # 大盘指标
    m = market_info
    market_text = (
        f"收盘: {m['close']:.2f}  |  MA20{'上方' if m['above_ma20'] else '下方'}({m['dev_ma20']:+.1%}){_ma_mark(m['above_ma20'])}\n"
        f"Hurst: {m['hurst']:.3f}  |  LLE: {m['lle']:.4f}  |  Tor: {m['tor']:.3f}\n"
        f"5日涨跌: {m['ret_5d']:+.2%}{_sign_mark(m['ret_5d'])}  |  20日涨跌: {m['ret_20d']:+.2%}{_sign_mark(m['ret_20d'])}\n"
        f"风控等级: {m['risk']}{_dir_mark(m['risk'])}  |  连续破位天数: {m['below_days']}{BEAR_MARK if m['below_days'] else ''}"
    )
    
    _has_risk = risk in ('DANGER', 'WARNING') or outlook in ('偏空', '谨慎')
    _has_opp = outlook in ('偏多', '底部区域')
    _mark = ('注意风险' + BEAR_MARK + ' | ' if _has_risk else '') + ('注意机会' + BULL_MARK + ' | ' if _has_opp else '')
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"分析力学量化日报 {report_date} | {_mark}来自 DoMate"
            },
            "template": header_color
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"{header_icon} **明日多空观点: {outlook}**{_dir_mark(outlook)}\n{m['outlook_detail']}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**宽基指数多空观点（沪深300/中证500/中证1000/科创50/创业板）**\n{broad_text}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {"tag": "lark_md", "content": f"**大盘指标**\n{market_text}"}
                    }
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**买入/关注行业**\n{buy_text}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**持有/观望行业**\n{watch_text}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**回避/减仓行业**\n{avoid_text}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**算法说明（分析力学框架）**\n\n"
                        "**Hurst 指数** — R/S（重标极差）分析法，滚动窗口100日。衡量价格序列的长期记忆性：H>0.5 趋势延续（记忆强），H=0.5 随机游走，H<0.5 均值回归。H>0.55 判为趋势，H<0.45 判为反转。\n\n"
                        "**LLE（最大李雅普诺夫指数）** — Rosenstein 方法，滚动窗口100日。相空间重构后追踪最近邻点对的发散速率。LLE>0 表示系统混沌，值越大越不可预测。校准阈值：>0.075 强混沌（约历史90分位），<0.045 稳定（约25分位），>0.065 弱混沌。\n\n"
                        "**Tor（KAM 环面度）** — 滚动窗口60日。相空间重构→计算角动量序列→FFT 频谱分析，取主频能量占比。值越高表示价格运动越有序（类环面轨道），>0.20 稳定，<0.12 环面破裂（进入混沌）。\n\n"
                        "**Hamiltonian（哈密顿量）** — H = T + V，滚动窗口20日。T（动能）= 收益率平方×年化波动率，衡量市场活性；V（势能）= 标准化价格偏离MA20的平方，衡量均值回归张力。对 H 做 z-score，超过±2.0 触发能量突增/崩塌预警。\n\n"
                        "**多空观点** — 综合大盘 Hurst/LLE/Tor/MA20 位置，风控模块三层判定：DANGER（清仓级）> WARNING（控仓级）> CLEAR（正常）。CLEAR 下再按 Hurst 方向细分为偏多/中性/底部区域。\n\n"
                        "**行业打分** — H>0.55 给趋势分（趋势加速/延续/爆拉前奏），H<0.45 给反转分（顶部反转/底部区域）。叠加 LLE 和 Tor 修正信号类型。得分>30 且信号匹配 = 买入，< -20 = 回避。龙头股取该行业成份股近2周涨幅前3。"
                }
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "分析力学量化系统 v4.3 | 数据源: AKShare(申万三级行业指数) | 仅供参考，不构成投资建议"
                    }
                ]
            }
        ]
    }
    
    card["elements"] += _bs('analytical_mechanics', 'card')
    return card


def send_feishu_card(card):
    """发送飞书交互式卡片"""
    payload = {"msg_type": "interactive", "card": card}
    
    try:
        resp = requests.post(
            FEISHU_WEBHOOK,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            timeout=30
        )
        result = resp.json()
        if result.get('StatusCode') == 0 or result.get('code') == 0:
            print("  飞书发送成功!")
            return True
        else:
            print(f"  飞书发送失败: {result}")
            return False
    except Exception as e:
        print(f"  飞书发送异常: {e}")
        return False


# ============================================================
# 邮件提醒（宽基指数看空数量达到阈值时触发）
# ============================================================

def _find_dws():
    """定位 dws CLI（钉钉开放能力），支持环境变量 DWS_BIN 覆盖"""
    cand = os.environ.get("DWS_BIN")
    if cand and os.path.exists(cand):
        return cand
    found = shutil.which("dws")
    if found:
        return found
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, ".local", "bin", "dws"),
        os.path.join(os.path.dirname(base), ".local", "bin", "dws"),
        os.path.join(os.path.expanduser("~"), ".local", "bin", "dws"),
        "/usr/local/bin/dws",
        "/usr/bin/dws",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _get_from_email(dws_bin):
    """获取发件邮箱（优先企业邮箱，其次第一个账户）"""
    try:
        r = subprocess.run(
            [dws_bin, "mail", "mailbox", "list", "--format", "json"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            accounts = data.get("emailAccounts") or []
            for acc in accounts:
                if isinstance(acc, dict) and acc.get("type") == "ORG" and acc.get("email"):
                    return acc["email"]
            if accounts and isinstance(accounts[0], dict):
                return accounts[0].get("email")
    except Exception as e:
        print(f"  获取发件邮箱异常: {e}")
    return "baizhiwei9492@dingtalk.com"


def collect_risk_items(market_info, broad_index_outlooks):
    """汇总“非动量类”结构化风险项，返回 [(风险类别, 说明), ...]

    只统计混沌结构、均线位置、风控等级和宽基结构性走弱，不含 5 日/20 日涨跌：
    单纯动量下跌看图即可，不构成邮件预警理由。
    """
    m = market_info or {}
    items = []

    below_days = int(m.get('below_days') or 0)
    if not m.get('above_ma20') and below_days >= MA_BREAK_DAYS:
        items.append(('均线破位', f"沪深300收盘在MA20下方，已连续{below_days}个交易日"))

    try:
        hurst = float(m.get('hurst'))
    except (TypeError, ValueError):
        hurst = 0.5
    if hurst < RISK_HURST_LOW:
        items.append(('趋势记忆弱化', f"Hurst={hurst:.3f} < {RISK_HURST_LOW}"))

    try:
        lle = float(m.get('lle'))
    except (TypeError, ValueError):
        lle = 0.0
    if lle > RISK_LLE_HIGH:
        items.append(('结构失序', f"LLE={lle:.4f} > {RISK_LLE_HIGH}"))

    try:
        tor = float(m.get('tor'))
    except (TypeError, ValueError):
        tor = 0.0
    if tor > RISK_TOR_HIGH:
        items.append(('拓扑失稳', f"Tor={tor:.3f} > {RISK_TOR_HIGH}"))

    if m.get('risk') in ('WARNING', 'DANGER'):
        items.append(('风控预警', f"风控等级{m['risk']}"))

    for b in (broad_index_outlooks or []):
        if b.get('name') == '沪深300':
            # 沪深300 已作为大盘计入上面各项，避免同一标的重算
            continue
        if b.get('outlook') == '偏空':
            items.append(('宽基偏空', f"{b.get('name')}结构判定偏空"))

    return items


def build_alert_email(report_date, market_info, broad_index_outlooks, risk_items):
    """构建邮件提醒的主题与正文

    正文按 Markdown 组织：块之间留空行、逐条用「- 」列表项，正文中的单个换行
    会被邮箱渲染成空格，只有空行分段和列表项才能在收件端真正换行。
    """
    m = market_info
    items = risk_items or []
    n = len(items)
    labels = "、".join(dict.fromkeys([k for k, _ in items]))
    subject = f"【预警】分析力学量化日报 {report_date}：{n}项结构风险（{labels}）"

    lines = []
    lines.append("### 分析力学量化交易系统 — 每日日报提醒")
    lines.append("")
    lines.append(f"**日期：** {report_date}")
    lines.append("")
    lines.append(
        f"**触发条件：** 非动量类结构化风险项累计 {n} 项（阈值 ≥{RISK_ALERT_THRESHOLD}），"
        f"5日/20日涨跌不计入触发条件"
    )
    lines.append("")
    lines.append(f"**大盘观点：** {m['outlook']}{_dir_mark(m['outlook'])} — {m['outlook_detail']}")
    lines.append("")
    lines.append("**命中的风险项：**")
    lines.append("")
    for label, detail in items:
        lines.append(f"- {label}{BEAR_MARK}：{detail}")
    lines.append("")
    lines.append(f"- 收盘：{m['close']:.2f}")
    lines.append(f"- 风控等级：{m['risk']}{_dir_mark(m['risk'])}")
    lines.append(f"- 均线位置：MA20{'上方' if m['above_ma20'] else '下方'}（{m['dev_ma20']:+.1%}）{_ma_mark(m['above_ma20'])}")
    lines.append(f"- 涨跌（仅参考，不计入触发）：5日 {m['ret_5d']:+.2%}{_sign_mark(m['ret_5d'])} ／ 20日 {m['ret_20d']:+.2%}{_sign_mark(m['ret_20d'])}")
    lines.append(f"- 混沌指标：Hurst {m['hurst']:.3f} ／ LLE {m['lle']:.4f} ／ Tor {m['tor']:.3f}")
    lines.append("")
    lines.append("**宽基指数多空观点：**")
    lines.append("")
    for b in broad_index_outlooks:
        lines.append(
            f"- **{b['name']}**：{b['outlook']}{_dir_mark(b['outlook'])} ｜ 收盘 {b['close']:.2f} ｜ "
            f"MA20{'上' if b['above_ma20'] else '下'}{_ma_mark(b['above_ma20'])} ｜ "
            f"5日 {b['ret_5d']:+.1%}{_sign_mark(b['ret_5d'])} ｜ "
            f"Hurst {b['hurst']:.3f} ／ LLE {b['lle']:.4f} ／ Tor {b['tor']:.3f}"
        )
    lines.append("")
    lines.append("—— 本邮件由分析力学量化日报脚本自动触发，仅供参考，不构成投资建议")
    return subject, "\n".join(lines)


def send_email_alert(report_date, market_info, broad_index_outlooks):
    """仅当非动量类结构化风险项达到阈值时，通过 dws（钉钉邮箱）发送邮件提醒"""
    risk_items = collect_risk_items(market_info, broad_index_outlooks)
    n = len(risk_items)
    if n < RISK_ALERT_THRESHOLD:
        print(f"  结构化风险项 {n} 个（< 阈值 {RISK_ALERT_THRESHOLD}），不发送邮件提醒")
        return False

    labels = "、".join(dict.fromkeys([k for k, _ in risk_items]))
    print(f"  结构化风险项 {n} 个（≥ 阈值 {RISK_ALERT_THRESHOLD}）：{labels}，触发邮件提醒")
    dws_bin = _find_dws()
    if not dws_bin:
        print("  邮件提醒失败: 未找到 dws 可执行文件（可设置环境变量 DWS_BIN）")
        return False

    from_email = _get_from_email(dws_bin)
    subject, body = build_alert_email(report_date, market_info, broad_index_outlooks, risk_items)
    try:
        r = subprocess.run(
            [
                dws_bin, "mail", "message", "send",
                "--from", from_email,
                "--to", EMAIL_TO,
                "--subject", subject,
                "--content", body,
                "--format", "json",
                "-y",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        print(f"  邮件提醒异常: {e}")
        return False

    if r.returncode == 0:
        print(f"  邮件提醒发送成功! (收件人: {EMAIL_TO})")
        out = (r.stdout or "").strip()
        if out:
            print(f"    dws 返回: {out[:300]}")
        return True
    print(f"  邮件提醒发送失败: {(r.stderr or r.stdout).strip()}")
    return False


# ============================================================
# 主函数
# ============================================================

def main():
    print("="*60)
    print("分析力学量化交易系统 — 每日日报")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # 1. 获取数据
    print("\n[1/6] 获取市场数据...")
    market_df = fetch_market_data()
    if market_df is None:
        print("  大盘数据获取失败，退出")
        return
    
    industry_data = load_or_fetch_industry_data()
    print(f"  行业数据: {len(industry_data)}个行业")
    
    # 2. 计算特征
    print("\n[2/6] 计算混沌特征...")
    market_features = compute_features({'market': market_df})['market']
    industry_features = compute_features(industry_data)
    
    # 3. 分析
    print("\n[3/6] 生成分析...")
    market_info = analyze_market(market_df)
    industry_results = analyze_industries(industry_features, market_info)
    
    # 宽基指数多空观点（沪深300主分析 + 中证500/中证1000/科创50/创业板）
    print("\n  分析宽基指数多空观点...")
    broad_index_outlooks = analyze_broad_indices()
    hs300_view = dict(market_info)
    hs300_view['name'] = '沪深300'
    broad_index_outlooks.insert(0, hs300_view)
    
    # 获取龙头股
    print("\n[4/6] 获取行业龙头股...")
    industry_results = enrich_industries_with_leaders(industry_results)
    
    report_date = market_info['date']
    if hasattr(report_date, 'strftime'):
        report_date = report_date.strftime('%Y-%m-%d')
    
    print(f"\n  日期: {report_date}")
    print(f"  大盘收盘: {market_info['close']:.2f}")
    print(f"  多空观点: {market_info['outlook']} - {market_info['outlook_detail']}")
    print(f"  风控等级: {market_info['risk']}")
    print(f"  Hurst: {market_info['hurst']:.3f}  LLE: {market_info['lle']:.4f}  Tor: {market_info['tor']:.3f}")
    
    print(f"\n  行业建议:")
    for ind in industry_results[:5]:
        print(f"    {ind['action']:>6s} | {ind['name']:>8s} H={ind['hurst']:.3f} 分={ind['score']:.0f} [{ind['signal']}]")
        leaders = ind.get('leaders', [])
        if leaders:
            leader_str = ', '.join([f"{n}({ret:+.1%})" for c, n, ret in leaders])
            print(f"           龙头: {leader_str}")
    print("  ...")
    for ind in industry_results[-3:]:
        print(f"    {ind['action']:>6s} | {ind['name']:>8s} H={ind['hurst']:.3f} 分={ind['score']:.0f} [{ind['signal']}]")
    
    # 5. 发送飞书
    print("\n[5/6] 发送飞书日报...")
    card = build_feishu_card(market_info, industry_results, str(report_date), broad_index_outlooks)
    success = send_feishu_card(card)
    
    if success:
        print("\n日报发送完成!")
    else:
        print("\n日报发送失败，请检查webhook配置")

    # 6. 邮件提醒：宽基指数看空数量达到阈值时发送
    print("\n[6/6] 检查非动量类结构风险项并发邮件提醒...")
    send_email_alert(str(report_date), market_info, broad_index_outlooks)


if __name__ == '__main__':
    main()
