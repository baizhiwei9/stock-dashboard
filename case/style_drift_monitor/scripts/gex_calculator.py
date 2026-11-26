#!/usr/bin/env python3
"""
A股ETF期权GEX（Gamma Exposure）计算脚本
计算5个ETF期权的Gamma敞口，识别Gamma挤压风险
"""

import os
import sys
import socket
import time
import math
from datetime import datetime, timedelta
from scipy.stats import norm
from scipy.optimize import brentq
import warnings
warnings.filterwarnings('ignore')

# 设置超时
socket.setdefaulttimeout(15)

try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False
    print("警告: tushare未安装，将使用备选方案")

# tushare token
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

TUSHARE_TOKEN = _tushare_token()

# 5个标的配置（4个ETF + 1个股指期权）
ETF_CONFIG = [
    {"name": "300ETF", "code": "510300", "exchange": "SSE", "filter": "300ETF", "opt_code": "OP510300.SH"},
    {"name": "500ETF", "code": "510500", "exchange": "SSE", "filter": "500ETF", "opt_code": "OP510500.SH"},
    {"name": "创业板ETF", "code": "159915", "exchange": "SZSE", "filter": "创业板", "opt_code": "OP159915.SZ"},
    {"name": "科创50ETF", "code": "588000", "exchange": "SSE", "filter": "科创50", "opt_code": "OP588000.SH"},
    {"name": "中证1000股指", "code": "MO", "exchange": "CFFEX", "filter": "中证1000", "underlying_code": "000852", "opt_code": "OP000852.SH"},
]

def get_trade_date():
    """获取最近交易日"""
    today = datetime.now()
    # 简单实现：如果是周末，返回上周五
    weekday = today.weekday()
    if weekday == 5:  # 周六
        return (today - timedelta(days=1)).strftime("%Y%m%d")
    elif weekday == 6:  # 周日
        return (today - timedelta(days=2)).strftime("%Y%m%d")
    return today.strftime("%Y%m%d")

def black_scholes_d1(S, K, T, r, sigma, q=0):
    """计算Black-Scholes的d1"""
    if T <= 0 or sigma <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return d1
    except:
        return None

def black_scholes_call_price(S, K, T, r, sigma, q=0):
    """计算Call期权价格"""
    if T <= 0 or sigma <= 0:
        return None
    d1 = black_scholes_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    if d1 is None:
        return None
    try:
        call_price = S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        return call_price
    except:
        return None

def black_scholes_put_price(S, K, T, r, sigma, q=0):
    """计算Put期权价格"""
    if T <= 0 or sigma <= 0:
        return None
    d1 = black_scholes_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    if d1 is None:
        return None
    try:
        put_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)
        return put_price
    except:
        return None

def gamma(S, K, T, r, sigma, q=0):
    """计算期权Gamma（Call和Put相同）"""
    if T <= 0 or sigma <= 0 or S <= 0:
        return None
    d1 = black_scholes_d1(S, K, T, r, sigma, q)
    if d1 is None:
        return None
    try:
        gamma = math.exp(-q * T) * norm.pdf(d1) / (S * sigma * math.sqrt(T))
        return gamma
    except:
        return None

def implied_volatility(S, K, T, r, market_price, option_type='call', q=0):
    """用Brent方法求解隐含波动率"""
    if T <= 0 or market_price <= 0:
        return None
    
    # 过滤明显不合理的价格
    if option_type == 'call':
        if market_price < max(0, S - K * math.exp(-r * T)):  # 低于内在价值
            return None
    else:
        if market_price < max(0, K * math.exp(-r * T) - S):  # 低于内在价值
            return None
    
    def objective(sigma):
        if option_type == 'call':
            price = black_scholes_call_price(S, K, T, r, sigma, q)
        else:
            price = black_scholes_put_price(S, K, T, r, sigma, q)
        if price is None:
            return 1e10
        return price - market_price
    
    try:
        # 波动率搜索范围: 1% 到 500%
        iv = brentq(objective, 0.01, 5.0, xtol=0.0001)
        # 过滤不合理的IV
        if iv < 0.05 or iv > 3.0:  # IV在5%到300%之间
            return None
        return iv
    except:
        return None

def get_etf_price(pro, code, exchange, underlying_code=None, trade_date=None):
    """获取ETF/指数现货价格"""
    if trade_date is None:
        trade_date = get_trade_date()
    
    # CFFEX股指期权：获取指数价格
    if exchange == 'CFFEX' and underlying_code:
        # 优先：tushare index_daily
        try:
            idx_df = pro.index_daily(ts_code=f"{underlying_code}.SH", start_date=trade_date, end_date=trade_date)
            if idx_df is not None and len(idx_df) > 0:
                return float(idx_df.iloc[0]['close'])
        except Exception as e:
            print(f"tushare获取指数价格失败 {underlying_code}: {e}")
        # 备选1：新浪API
        try:
            import requests
            headers = {'User-Agent': 'Mozilla/5.0'}
            symbol = f"sh{underlying_code}"
            url = f"http://hq.sinajs.cn/list={symbol}"
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.text
                parts = data.split('"')[1].split(',')
                if len(parts) > 3:
                    return float(parts[3])
        except Exception as e2:
            print(f"新浪获取指数价格也失败 {underlying_code}: {e2}")
        # 备选2：tushare取最近交易日
        try:
            idx_df = pro.index_daily(ts_code=f"{underlying_code}.SH", start_date='20260101', end_date=trade_date)
            if idx_df is not None and len(idx_df) > 0:
                idx_df = idx_df.sort_values('trade_date', ascending=False)
                return float(idx_df.iloc[0]['close'])
        except Exception as e3:
            print(f"tushare最近交易日也失败: {e3}")
        return None
    
    # ETF期权：获取ETF价格
    try:
        # tushare fund_daily需要不带前缀的代码
        ts_code = f"{code}.SH" if exchange == 'SSE' else f"{code}.SZ"
        df = pro.fund_daily(ts_code=ts_code, trade_date=trade_date)
        if df is not None and len(df) > 0:
            return df.iloc[0]['close']
    except Exception as e:
        print(f"获取ETF价格失败 {code}: {e}")
    
    # 备选：使用新浪API获取实时价格
    try:
        import requests
        headers = {'User-Agent': 'Mozilla/5.0'}
        if exchange == 'SSE':
            symbol = f"sh{code}"
        else:
            symbol = f"sz{code}"
        url = f"http://hq.sinajs.cn/list={symbol}"
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.text
            # 格式: var hq_str_xxx="name,open,pre_close,close,high,low,..."
            parts = data.split('"')[1].split(',')
            if len(parts) > 3:
                return float(parts[3])
    except Exception as e2:
        print(f"备选获取ETF价格也失败 {code}: {e2}")
    
    return None

def get_shibor(pro):
    """获取SHIBOR作为无风险利率"""
    try:
        df = pro.shibor()
        if df is not None and len(df) > 0:
            # 获取1年SHIBOR
            shibor_row = df[df['tenor'] == '1Y']
            if len(shibor_row) > 0:
                return shibor_row.iloc[0]['on'] / 100  # 转换为小数
            # 默认返回1Y shibor
            return df.iloc[0]['on'] / 100 if 'on' in df.columns else 0.03
    except:
        pass
    return 0.03  # 默认3%

def get_option_data(pro, exchange, etf_name, trade_date, opt_code_filter=None):
    """获取期权数据"""
    try:
        # 获取期权合约列表
        df_basic = pro.opt_basic(exchange=exchange)
        if df_basic is None or len(df_basic) == 0:
            print(f"获取期权合约列表失败: {exchange}")
            return None
        
        # CFFEX: 用opt_code精确过滤
        if opt_code_filter:
            df_filtered = df_basic[df_basic['opt_code'] == opt_code_filter]
        else:
            # 无opt_code时：用名称模糊匹配
            df_filtered = df_basic[df_basic['name'].str.contains(etf_name, na=False)]
            if len(df_filtered) == 0:
                df_filtered = df_basic
        
        if len(df_filtered) == 0:
            print(f"未找到期权合约: {etf_name} / {opt_code_filter}")
            return None
        
        # CFFEX: 批量查询日线数据
        if exchange == 'CFFEX':
            try:
                daily_df = pro.opt_daily(exchange='CFFEX', start_date=trade_date, end_date=trade_date)
                if daily_df is None or daily_df.empty:
                    print(f"CFFEX批量日线数据为空")
                    return None
                
                target_ts_codes = set(df_filtered['ts_code'].tolist())
                daily_filtered = daily_df[daily_df['ts_code'].isin(target_ts_codes)]
                print(f"  CFFEX批量查询: {len(daily_df)}条, 过滤后: {len(daily_filtered)}条")
                
                all_data = []
                for ts_code in target_ts_codes:
                    daily_row = daily_filtered[daily_filtered['ts_code'] == ts_code]
                    basic_row = df_filtered[df_filtered['ts_code'] == ts_code]
                    if daily_row.empty or basic_row.empty:
                        continue
                    
                    basic_info = basic_row.iloc[0]
                    row = daily_row.iloc[0]
                    
                    # CFFEX期权：call_put字段直接从合约名判断
                    opt_name = basic_info['name']
                    if '认购' in opt_name or basic_info.get('call_put') == 'C':
                        cp_type = 'C'
                    elif '认沽' in opt_name or basic_info.get('call_put') == 'P':
                        cp_type = 'P'
                    else:
                        cp_type = 'C'
                    
                    # 到期日
                    exercise_date = basic_info.get('maturity_date', '')
                    if not exercise_date:
                        exercise_date = basic_info.get('delist_date', '')
                    
                    all_data.append({
                        'ts_code': ts_code,
                        'name': opt_name,
                        'exchange': exchange,
                        'call_put': cp_type,
                        'exercise_date': exercise_date,
                        'exercise_price': basic_info['exercise_price'],
                        'close': row['close'],
                        'oi': row['oi'],
                        'volume': row['vol'],
                    })
                
                if len(all_data) == 0:
                    return None
                return all_data
                
            except Exception as e:
                print(f"CFFEX批量查询失败: {e}")
                return None
        
        # SSE/SZSE: 也用批量查询提高效率，避免逐个合约API调用
        opt_codes = df_filtered['ts_code'].tolist()
        
        # 限制合约数量：只取近月和次月活跃合约
        if 'maturity_date' in df_filtered.columns:
            active = df_filtered[df_filtered['maturity_date'] >= trade_date]
            if len(active) > 0:
                # 取最近两个到期日的合约
                expiry_dates = sorted(active['maturity_date'].unique())[:2]
                df_filtered = active[active['maturity_date'].isin(expiry_dates)]
                opt_codes = df_filtered['ts_code'].tolist()
        
        try:
            daily_df = pro.opt_daily(exchange=exchange, start_date=trade_date, end_date=trade_date)
            if daily_df is None or daily_df.empty:
                print(f"{exchange}批量日线数据为空")
                return None
            
            target_ts_codes = set(opt_codes)
            daily_filtered = daily_df[daily_df['ts_code'].isin(target_ts_codes)]
            print(f"  {exchange}批量查询: {len(daily_df)}条, 过滤后: {len(daily_filtered)}条")
            
            all_data = []
            for ts_code in target_ts_codes:
                daily_row = daily_filtered[daily_filtered['ts_code'] == ts_code]
                basic_row = df_filtered[df_filtered['ts_code'] == ts_code]
                if daily_row.empty or basic_row.empty:
                    continue
                
                basic_info = basic_row.iloc[0]
                row = daily_row.iloc[0]
                
                # 判断Call还是Put
                opt_name = basic_info['name']
                if '认购' in opt_name or '购' in opt_name:
                    cp_type = 'C'
                elif '认沽' in opt_name or '沽' in opt_name:
                    cp_type = 'P'
                else:
                    # 从call_put字段判断（如果有）
                    cp_val = str(basic_info.get('call_put', ''))
                    cp_type = 'C' if cp_val == 'C' else 'P'
                
                # 到期日
                exercise_date = basic_info.get('maturity_date', '')
                if not exercise_date:
                    exercise_date = basic_info.get('delist_date', '')
                
                all_data.append({
                    'ts_code': ts_code,
                    'name': opt_name,
                    'exchange': exchange,
                    'call_put': cp_type,
                    'exercise_date': exercise_date,
                    'exercise_price': basic_info['exercise_price'],
                    'close': row['close'],
                    'oi': row['oi'],
                    'volume': row['vol'],
                })
            
            if len(all_data) == 0:
                return None
            return all_data
            
        except Exception as e:
            print(f"{exchange}批量查询失败: {e}")
            return None
    except Exception as e:
        print(f"获取期权数据失败: {e}")
        return None

def calculate_gex_for_etf(option_data, spot_price, r, trade_date):
    """计算单个ETF的GEX"""
    if option_data is None or spot_price is None or len(option_data) == 0:
        return None
    
    # 按行权价分组
    strikes_data = {}
    today = datetime.strptime(trade_date, "%Y%m%d")
    
    for opt in option_data:
        try:
            # 解析到期日
            exercise_date_str = opt.get('exercise_date', '')
            if isinstance(exercise_date_str, str) and len(exercise_date_str) >= 8:
                # 尝试多种日期格式
                for fmt in ['%Y%m%d', '%Y-%m-%d', '%Y/%m/%d']:
                    try:
                        exercise_date = datetime.strptime(exercise_date_str[:8], fmt)
                        break
                    except:
                        continue
                else:
                    continue
            elif hasattr(exercise_date_str, 'strftime'):
                exercise_date = exercise_date_str
            else:
                continue
            
            T = (exercise_date - today).days / 365.0
            
            # 跳过临近到期的合约（<3天）
            if T < 3/365:
                continue
            
            strike = float(opt['exercise_price'])
            if strike <= 0 or spot_price <= 0:
                continue
            
            if strike not in strikes_data:
                strikes_data[strike] = {'call_oi': 0, 'put_oi': 0, 'call_gamma': None, 'put_gamma': None}
            
            option_type = opt['call_put']
            oi = float(opt['oi']) if opt['oi'] is not None else 0
            close_price = float(opt['close']) if opt['close'] is not None else 0
            
            # 计算IV
            if close_price > 0:
                iv = implied_volatility(spot_price, strike, T, r, close_price, 
                                        'call' if option_type == 'C' else 'put')
            else:
                iv = None
            
            if iv is not None:
                g = gamma(spot_price, strike, T, r, iv)
            else:
                g = None
            
            if option_type == 'C':  # Call
                strikes_data[strike]['call_oi'] += oi
                if g is not None:
                    strikes_data[strike]['call_gamma'] = g
            else:  # Put
                strikes_data[strike]['put_oi'] += oi
                if g is not None:
                    strikes_data[strike]['put_gamma'] = g
        except Exception as e:
            continue
    
    # 计算每个行权价的Net GEX
    results = []
    cumulative_gex = 0
    flip_point = None
    prev_sign = None
    
    for strike in sorted(strikes_data.keys()):
        data = strikes_data[strike]
        call_gamma = data['call_gamma'] if data['call_gamma'] is not None else 0
        put_gamma = data['put_gamma'] if data['put_gamma'] is not None else 0
        
        # Net GEX = (Call_OI * Call_Gamma - Put_OI * Put_Gamma) * 100 * S^2
        strike_gex = (data['call_oi'] * call_gamma - data['put_oi'] * put_gamma) * 100 * (spot_price ** 2)
        cumulative_gex += strike_gex
        
        results.append({
            'strike': strike,
            'call_oi': data['call_oi'],
            'put_oi': data['put_oi'],
            'call_gamma': call_gamma,
            'put_gamma': put_gamma,
            'net_gex': strike_gex,
            'cumulative_gex': cumulative_gex
        })
        
        # 找GEX Flip Point（累计GEX由正转负的位置）
        current_sign = 1 if cumulative_gex >= 0 else -1
        if prev_sign == 1 and current_sign == -1 and flip_point is None:
            flip_point = strike
        prev_sign = current_sign
    
    # 总Net GEX
    total_gex = cumulative_gex
    
    # 计算ATM IV（最接近现货价格的行权价）
    atm_iv = None
    if spot_price and len(strikes_data) > 0:
        closest_strike = min(strikes_data.keys(), key=lambda k: abs(k - spot_price))
        strike_data = strikes_data[closest_strike]
        # 优先用Call的IV，没有就用Put的
        if strike_data['call_gamma'] is not None:
            # 重新计算IV
            for opt in option_data:
                if abs(float(opt['exercise_price']) - closest_strike) < 0.001:
                    if opt['call_put'] == 'C' and float(opt['close']) > 0:
                        T_calc = (datetime.strptime(opt.get('exercise_date', '20261231')[:8], '%Y%m%d') - datetime.strptime(trade_date, "%Y%m%d")).days / 365.0
                        if T_calc > 0:
                            atm_iv = implied_volatility(spot_price, closest_strike, T_calc, r, float(opt['close']), 'call')
                            if atm_iv:
                                break
        if atm_iv is None:
            for opt in option_data:
                if abs(float(opt['exercise_price']) - closest_strike) < 0.001:
                    if opt['call_put'] == 'P' and float(opt['close']) > 0:
                        T_calc = (datetime.strptime(opt.get('exercise_date', '20261231')[:8], '%Y%m%d') - datetime.strptime(trade_date, "%Y%m%d")).days / 365.0
                        if T_calc > 0:
                            atm_iv = implied_volatility(spot_price, closest_strike, T_calc, r, float(opt['close']), 'put')
                            if atm_iv:
                                break
    
    return {
        'total_gex': total_gex,
        'flip_point': flip_point,
        'details': results,
        'spot_price': spot_price,
        'atm_iv': atm_iv
    }

def generate_report(gex_results, trade_date):
    """生成Markdown报告"""
    report = f"""# GEX Gamma挤压监控 | {trade_date}

## 一、各ETF Net GEX汇总

| 标的 | 现价 | Net GEX(亿) | GEX Flip Point | ATM IV | IV分位 | Gamma挤压风险 |
|------|------|-------------|----------------|--------|--------|---------------|
"""
    
    risk_assessment = []
    iv_warnings = []
    for etf_name, result in gex_results.items():
        if result is None:
            report += f"| {etf_name} | N/A | N/A | N/A | N/A | N/A | 数据不足 |\n"
            risk_assessment.append((etf_name, "N/A", "N/A", None, None, None))
        else:
            spot = result['spot_price']
            total_gex = result['total_gex']
            flip = result['flip_point']
            
            # 风险评估（改进版：考虑GEX体量和Flip Point）
            total_gex_yi = total_gex / 1e8  # 转为亿
            
            if total_gex < -1e9:  # < -10亿
                risk = "🔴 高"
                risk_level = "高"
            elif total_gex < -1e8:  # < -1亿
                risk = "🟡 中"
                risk_level = "中"
            elif abs(total_gex_yi) < 1.0:  # 绝对值 < 1亿
                risk = "⚠️ 敞口过小，信号无效"
                risk_level = "过小"
            else:
                risk = "🟢 低"
                risk_level = "低"
            
            # Flip Point显示（改进版：未计算时明确标识）
            if flip is None:
                flip_display = "⚠️ 未计算"
            else:
                flip_display = f"{flip:.3f}"
            
            # IV显示（改进版：分位数>90%标红标星）
            atm_iv = result.get('atm_iv')
            iv_percentile = result.get('iv_percentile')
            if atm_iv:
                iv_display = f"{atm_iv*100:.1f}%"
                if iv_percentile is not None:
                    if iv_percentile > 90:
                        iv_pct_display = f"🔴⭐{iv_percentile:.0f}%"
                        iv_warnings.append(f"**{etf_name}**：IV={atm_iv*100:.1f}%，处于历史{iv_percentile:.0f}%分位数（极高）")
                    elif iv_percentile > 70:
                        iv_pct_display = f"🟡{iv_percentile:.0f}%"
                    else:
                        iv_pct_display = f"{iv_percentile:.0f}%"
                else:
                    iv_pct_display = "N/A"
            else:
                iv_display = "N/A"
                iv_pct_display = "N/A"
            
            gex_display = f"{total_gex_yi:+.2f}" if abs(total_gex) < 1e12 else "极大"
            spot_display = f"{spot:.3f}" if spot else "N/A"
            
            report += f"| {etf_name} | {spot_display} | {gex_display} | {flip_display} | {iv_display} | {iv_pct_display} | {risk} |\n"
            risk_assessment.append((etf_name, risk_level, total_gex, flip, atm_iv, iv_percentile))
    
    # GEX分布详情
    report += """
## 二、GEX分布详情

"""
    
    for etf_name, result in gex_results.items():
        report += f"### {etf_name} GEX分布\n\n"
        
        if result is None or len(result['details']) == 0:
            report += "*数据暂不可用*\n\n"
            continue
        
        report += """| 行权价 | Call OI | Put OI | Call Gamma | Put Gamma | Net GEX(万) | 累计GEX(万) |
|--------|---------|--------|------------|-----------|------------|-------------|
"""
        
        for detail in result['details'][-15:]:  # 只显示最近15个行权价
            strike = detail['strike']
            call_oi = int(detail['call_oi'])
            put_oi = int(detail['put_oi'])
            call_g = detail['call_gamma'] if detail['call_gamma'] else 0
            put_g = detail['put_gamma'] if detail['put_gamma'] else 0
            net_gex = detail['net_gex'] / 1e4  # 转为万
            cum_gex = detail['cumulative_gex'] / 1e4
            
            report += f"| {strike:.3f} | {call_oi} | {put_oi} | {call_g:.6f} | {put_g:.6f} | {net_gex:+,.0f} | {cum_gex:+,.0f} |\n"
        
        report += "\n"
    
    # Gamma挤压风险评估
    report += """## 三、Gamma挤压风险评估

### 判断标准
- **Net GEX < 0**：做市商净空Gamma → 价格上涨时做市商被迫买入，下跌时被迫卖出 → 放大波动 → Gamma挤压风险
- **Net GEX > 0**：做市商净多Gamma → 价格上涨时做市商卖出，下跌时买入 → 抑制波动 → 市场稳定
- **GEX Flip Point接近现价**：微小的价格变动可能触发Gamma模式切换

### 综合判断
"""
    
    # 汇总分析
    negative_count = sum(1 for _, _, gex, _, _, _ in risk_assessment if isinstance(gex, float) and gex < 0)
    small_gex_count = sum(1 for _, rl, gex, _, _, _ in risk_assessment if rl == "过小")
    total_valid = sum(1 for _, _, gex, _, _, _ in risk_assessment if gex != "N/A")
    
    if total_valid > 0:
        if negative_count == total_valid:
            overall_risk = "高"
            summary = "所有ETF期权均呈现负GEX，做市商整体净空Gamma，存在较高的Gamma挤压风险。当标的价格大幅波动时，可能触发连锁对冲行为，放大市场波动。"
        elif negative_count >= total_valid / 2:
            overall_risk = "中"
            summary = "多数ETF期权呈现负GEX，市场整体存在一定的Gamma挤压风险。投资者应关注价格突变可能引发的流动性波动。"
        elif small_gex_count > 0:
            overall_risk = "注意"
            summary = f"有{small_gex_count}个品种GEX敞口过小（<1亿），信号有效性存疑。正GEX理论上抑制波动，但体量太小时做市商对冲能力有限，无法有效稳定市场。"
        else:
            overall_risk = "低"
            summary = "多数ETF期权呈现正GEX且体量充足，做市商整体净多Gamma，有利于市场稳定。预期波动率较低。"
    else:
        overall_risk = "数据不足"
        summary = "由于数据获取问题，无法进行准确评估。"
    
    report += f"""
**整体风险等级：{overall_risk}**

{summary}

"""
    
    for etf_name, risk_level, gex, flip, atm_iv, iv_pct in risk_assessment:
        if gex == "N/A":
            report += f"- **{etf_name}**：数据暂不可用\n"
        elif risk_level == "过小":
            iv_info = f"，IV={atm_iv*100:.1f}%({iv_pct:.0f}%分位)" if atm_iv and iv_pct else ""
            report += f"- **{etf_name}**：⚠️ Net GEX={gex/1e8:+.2f}亿，**敞口过小**，信号无效。Flip Point {'⚠️ 未计算' if flip is None else f'{flip:.3f}'}{iv_info}\n"
        elif gex < 0:
            report += f"- **{etf_name}**：Net GEX为负({gex/1e8:+.2f}亿)，存在看空压力，风险{risk_level}\n"
        else:
            report += f"- **{etf_name}**：Net GEX为正({gex/1e8:+.2f}亿)，市场相对稳定，风险{risk_level}\n"
    
    # IV极值警告
    report += """## 四、隐波(IV)极值警告

"""
    
    if iv_warnings:
        report += "**以下标的IV处于历史极高位（>90%分位数），需重点关注：**\n\n"
        for warning in iv_warnings:
            report += f"- {warning}\n"
        report += "\n"
    else:
        report += "当前所有标的IV均处于历史正常区间（<90%分位数），无极端警告。\n\n"
    
    # 策略提示
    report += """## 五、策略提示

### 基于GEX分析的参考建议
"""
    
    if overall_risk == "高":
        report += """
1. **波动率策略**：考虑配置波动率策略（如跨式组合），利用可能的高波动环境
2. **仓位管理**：降低单一方向仓位，避免在Gamma挤压发生时遭受重大损失
3. **对冲准备**：持有标的仓位的投资者可考虑买入保护性期权
4. **关注Flip Point**：当价格接近GEX Flip Point时，需特别警惕方向性变化
"""
    elif overall_risk == "中":
        report += """
1. **谨慎操作**：避免重仓单一方向，保持灵活性
2. **Gamma监控**：持续关注GEX变化，特别是接近到期日时
3. **适当对冲**：持有较大仓位的投资者可考虑适度对冲
"""
    else:
        report += """
1. **趋势跟随**：市场稳定时，可考虑趋势跟随策略
2. **卖出期权**：在IV较低时，卖出期权策略可能获得稳定收益
3. **保持仓位**：无需过度对冲，维持正常仓位即可
"""
    
    report += f"""
---
*数据来源：Tushare | 报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
*本报告仅供案例分析学习，不构成投资建议*
"""
    
    return report

def main():
    global HAS_TUSHARE
    
    print("=" * 60)
    print("A股ETF/股指期权GEX计算程序")
    print("=" * 60)
    
    # 支持命令行指定交易日期
    if len(sys.argv) >= 2:
        trade_date = sys.argv[1]
    else:
        trade_date = get_trade_date()
    print(f"\n交易日期: {trade_date}")
    
    use_tushare = HAS_TUSHARE
    
    # 初始化tushare
    if use_tushare:
        try:
            ts.set_token(TUSHARE_TOKEN)
            pro = ts.pro_api()
            print("Tushare初始化成功")
        except Exception as e:
            print(f"Tushare初始化失败: {e}")
            use_tushare = False
    
    gex_results = {}
    
    # 获取无风险利率
    r = 0.03  # 默认值
    if use_tushare:
        try:
            r = get_shibor(pro)
            print(f"无风险利率(SHIBOR 1Y): {r*100:.2f}%")
        except:
            print("使用默认无风险利率: 3%")
    else:
        print("使用默认无风险利率: 3%")
    
    # 处理每个ETF
    for etf in ETF_CONFIG:
        print(f"\n{'='*40}")
        print(f"处理: {etf['name']} ({etf['code']})")
        print('='*40)
        
        spot_price = None
        
        # 获取ETF/指数现货价格
        if use_tushare:
            try:
                underlying_code = etf.get('underlying_code')
                spot_price = get_etf_price(pro, etf['code'], etf['exchange'], underlying_code=underlying_code, trade_date=trade_date)
            except Exception as e:
                print(f"获取价格失败: {e}")
        
        if spot_price is None:
            print(f"  ⚠️ 无法获取{etf['name']}现货价格")
        
        # 获取期权数据
        if use_tushare:
            try:
                print(f"  获取期权数据中...")
                opt_code_filter = etf.get('opt_code')
                option_data = get_option_data(pro, etf['exchange'], etf['filter'], trade_date, opt_code_filter=opt_code_filter)
                
                if option_data is not None:
                    print(f"  获取到 {len(option_data)} 条期权记录")
                else:
                    print(f"  ⚠️ 无法获取期权数据")
                    option_data = None
            except Exception as e:
                print(f"  获取期权数据失败: {e}")
                option_data = None
        else:
            option_data = None
        
        # 计算GEX
        if option_data and spot_price:
            try:
                result = calculate_gex_for_etf(option_data, spot_price, r, trade_date)
                if result:
                    # 计算IV历史分位数
                    atm_iv = result.get('atm_iv')
                    if atm_iv:
                        iv_percentile, hv_median = get_historical_volatility_percentile(
                            etf['underlying_code'] if etf.get('underlying_code') else etf['code'],
                            etf['exchange'],
                            atm_iv,
                            trade_date
                        )
                        result['iv_percentile'] = iv_percentile
                        result['hv_median'] = hv_median
                    else:
                        result['iv_percentile'] = None
                        result['hv_median'] = None
                    
                    gex_results[etf['name']] = result
                    print(f"  ✓ Net GEX: {result['total_gex']/1e8:+.2f}亿")
                    print(f"  ✓ GEX Flip Point: {result['flip_point']}")
                    if atm_iv:
                        print(f"  ✓ ATM IV: {atm_iv*100:.1f}%")
                        if result.get('iv_percentile'):
                            print(f"  ✓ IV分位数: {result['iv_percentile']:.1f}%")
                else:
                    gex_results[etf['name']] = None
            except Exception as e:
                print(f"  GEX计算失败: {e}")
                gex_results[etf['name']] = None
        else:
            gex_results[etf['name']] = None
    
    # 生成报告
    print("\n" + "="*60)
    print("生成报告...")
    print("="*60)
    
    report = generate_report(gex_results, trade_date)
    
    # 保存报告
    report_dir = "市场情绪监控/outputs"
    os.makedirs(report_dir, exist_ok=True)
    report_path = f"{report_dir}/{trade_date}_GEX报告.md"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n报告已保存: {report_path}")
    
    # 打印报告摘要
    print("\n" + "="*60)
    print("报告摘要")
    print("="*60)
    print(report[:2000])
    
    return report_path, report

if __name__ == "__main__":
    report_path, full_report = main()


def get_historical_volatility_percentile(spot_code, exchange, current_iv, trade_date, lookback_days=250):
    """
    获取历史波动率分位数
    用最近1年的价格数据计算历史波动率，然后看当前IV在历史中的位置
    """
    if current_iv is None or current_iv <= 0:
        return None, None
    
    try:
        import pandas as pd
        
        # 获取历史价格数据
        end_date = trade_date
        start_date_obj = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=lookback_days)
        start_date = start_date_obj.strftime("%Y%m%d")
        
        historical_prices = []
        
        if exchange in ['SSE', 'SZSE']:
            # ETF：使用tushare fund_daily
            ts_code = f"{spot_code}.SH" if exchange == 'SSE' else f"{spot_code}.SZ"
            try:
                df = ts.pro_api().fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
                if df is not None and len(df) > 1:
                    df = df.sort_values('trade_date')
                    historical_prices = df['close'].tolist()
            except:
                pass
        elif exchange == 'CFFEX':
            # 指数：使用tushare index_daily
            try:
                df = ts.pro_api().index_daily(ts_code=f"{spot_code}.SH", start_date=start_date, end_date=end_date)
                if df is not None and len(df) > 1:
                    df = df.sort_values('trade_date')
                    historical_prices = df['close'].tolist()
            except:
                pass
        
        if len(historical_prices) < 30:
            return None, None
        
        # 计算日收益率
        prices = pd.Series(historical_prices)
        returns = prices.pct_change().dropna()
        
        # 计算20日滚动波动率（年化）
        rolling_vol = returns.rolling(window=20).std() * (252 ** 0.5) * 100  # 转为百分比
        rolling_vol = rolling_vol.dropna()
        
        if len(rolling_vol) == 0:
            return None, None
        
        # 计算当前IV在历史HV中的分位数
        current_iv_pct = current_iv * 100  # 转为百分比
        percentile = (rolling_vol < current_iv_pct).sum() / len(rolling_vol) * 100
        
        return percentile, rolling_vol.median()
        
    except Exception as e:
        print(f"计算历史波动率分位数失败: {e}")
        return None, None
