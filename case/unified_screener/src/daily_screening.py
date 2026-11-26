import sys
import os
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')
import baostock as bs

# ==================== 数据源配置 ====================
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

# Tushare 开关：因沙箱多出口IP触发"IP数量超限"，默认禁用，从数据源优先级中排除
# 如需恢复tushare，将 USE_TUSHARE 改为 True 即可
USE_TUSHARE = False

# 数据源优先级 (按顺序尝试) - baostock优先
# 当 USE_TUSHARE=True 时会自动加入 tushare 作为第二优先级
if USE_TUSHARE:
    DATA_SOURCE_PRIORITY = ['baostock', 'tushare', 'finance_mcp']
else:
    DATA_SOURCE_PRIORITY = ['baostock', 'finance_mcp']

# 全局变量记录实际使用的数据源
USED_DATA_SOURCE = None

# ==================== FinanceMCP 连接（带重试） ====================
scripts_path = os.path.join(os.getcwd(), "astock-casestudy", "scripts")
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

from finance_query import FinanceMCP

def connect_finance_mcp_with_retry(max_retries=3, retry_delay=2):
    """连接恒生聚源数据库，带重试机制"""
    for retry in range(max_retries):
        try:
            mcp = FinanceMCP()
            mcp.connect()
            print(f"✅ 恒生聚源数据库连接成功 (第{retry+1}次尝试)")
            return mcp
        except Exception as e:
            if retry == max_retries - 1:
                print(f"❌ 恒生聚源数据库连接失败，已达最大重试次数")
                raise
            print(f"⚠️  连接失败，第{retry+1}次重试... ({str(e)[:50]})")
            time.sleep(retry_delay)
    return None

# ==================== tushare 工具函数 ====================
def get_tushare_pro():
    """获取tushare pro实例"""
    try:
        import tushare as ts
        ts.set_token(TUSHARE_TOKEN)
        return ts.pro_api()
    except Exception as e:
        print(f"❌ tushare初始化失败: {e}")
        return None

def innercode_to_tscode(inner_code, stocks_df):
    """将innercode转换为tushare代码格式（如 '000001.SZ'）"""
    stock_info = stocks_df[stocks_df['InnerCode'] == inner_code]
    if stock_info.empty:
        return None
    
    stock = stock_info.iloc[0]
    secu_code = stock['SecuCode']
    secu_market = stock['SecuMarket']
    
    # 映射市场代码到交易所后缀
    # 根据实际数据: 83: 上交所 -> SH, 90: 深交所 -> SZ
    if secu_market == 83:
        return f"{secu_code}.SH"
    elif secu_market == 90:
        return f"{secu_code}.SZ"
    else:
        return None

def innercode_to_bs_code(inner_code, stocks_df):
    """将innercode转换为baostock代码格式（如 'sz.000001'）"""
    stock_info = stocks_df[stocks_df['InnerCode'] == inner_code]
    if stock_info.empty:
        return None
    
    stock = stock_info.iloc[0]
    secu_code = stock['SecuCode']
    secu_market = stock['SecuMarket']
    
    # 映射市场代码到交易所前缀
    # 根据实际数据: 83: 上交所 -> sh., 90: 深交所 -> sz.
    if secu_market == 83:
        return f"sh.{secu_code}"
    elif secu_market == 90:
        return f"sz.{secu_code}"
    else:
        # 北交所 bj.
        return f"bj.{secu_code}"

def fetch_prices_tushare_batch(inner_codes, stocks_df, last_day):
    """使用tushare获取批量股票日线数据"""
    try:
        pro = get_tushare_pro()
        if pro is None:
            return pd.DataFrame()
        
        # 获取最近160个交易日日期
        # 先获取最近一年的交易日历，然后取最后160个
        start_date = (datetime.strptime(last_day, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')
        cal_df = pro.trade_cal(exchange='', start_date=start_date, end_date=last_day, is_open='1')
        if cal_df.empty:
            return pd.DataFrame()
        
        trading_days = sorted(cal_df['cal_date'].tolist(), reverse=True)
        if len(trading_days) < 160:
            # 如果不够160个交易日，使用所有交易日
            earliest_date = trading_days[-1]
        else:
            earliest_date = trading_days[159]  # 第160个（从0开始）
        
        # 分批获取股票数据（tushare限制每分钟500次）
        all_data = []
        batch_size = 100  # 每批100只股票
        
        for i in range(0, len(inner_codes), batch_size):
            batch = inner_codes[i:i+batch_size]
            batch_ts_codes = []
            
            for inner_code in batch:
                ts_code = innercode_to_tscode(inner_code, stocks_df)
                if ts_code:
                    batch_ts_codes.append(ts_code)
            
            if not batch_ts_codes:
                continue
            
            # 构建tushare查询代码字符串（逗号分隔）
            ts_codes_str = ','.join(batch_ts_codes)
            
            # 获取日线数据
            try:
                df = pro.daily(ts_code=ts_codes_str, start_date=earliest_date, end_date=last_day)
                if df.empty:
                    continue
                
                # 转换数据格式以匹配现有处理逻辑
                # 需要将ts_code映射回inner_code
                ts_to_inner = {}
                for inner_code in batch:
                    ts_code = innercode_to_tscode(inner_code, stocks_df)
                    if ts_code:
                        ts_to_inner[ts_code] = inner_code
                
                df['InnerCode'] = df['ts_code'].map(ts_to_inner)
                df = df.rename(columns={'trade_date': 'TradingDay', 'close': 'ClosePrice'})
                df['TradingDay'] = pd.to_datetime(df['TradingDay'], format='%Y%m%d')
                
                # 只保留需要的列
                df = df[['InnerCode', 'TradingDay', 'ClosePrice']].copy()
                all_data.append(df)
                
                # 避免触发频率限制
                time.sleep(0.5)
                
            except Exception as e:
                print(f"⚠️  tushare批量查询失败: {str(e)[:100]}")
                continue
        
        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)
            return result_df
        else:
            return pd.DataFrame()
            
    except Exception as e:
        print(f"❌ tushare数据获取失败: {str(e)[:100]}")
        return pd.DataFrame()

def fetch_prices_baostock_batch(inner_codes, stocks_df, last_day):
    """使用baostock获取批量股票日线数据（改进版：会话保持与重试）"""
    try:
        # 登录baostock（带重试）
        max_login_retries = 3
        login_success = False
        for retry in range(max_login_retries):
            try:
                lg = bs.login()
                if lg.error_code == '0':
                    login_success = True
                    print(f"✅ baostock登录成功 (第{retry+1}次尝试)")
                    break
                else:
                    print(f"⚠️  baostock登录失败第{retry+1}次: {lg.error_msg}")
                    if retry < max_login_retries - 1:
                        time.sleep(2)
            except Exception as e:
                print(f"⚠️  baostock登录异常第{retry+1}次: {str(e)[:50]}")
                if retry < max_login_retries - 1:
                    time.sleep(2)
        
        if not login_success:
            print("❌ baostock登录失败，已达最大重试次数")
            return pd.DataFrame()
        
        # 计算开始日期：需要足够长的历史数据计算ma160（约160个交易日，约8个月）
        # 为了确保数据充足，回溯一年
        start_date_obj = datetime.strptime(last_day, '%Y-%m-%d') - timedelta(days=365)
        start_date = start_date_obj.strftime('%Y-%m-%d')
        
        all_data = []
        batch_size = 50  # 分批处理，避免请求过大
        
        for i in range(0, len(inner_codes), batch_size):
            batch = inner_codes[i:i+batch_size]
            batch_bs_codes = []
            
            # 构建inner_code到baostock代码的映射
            inner_to_bs = {}
            for inner_code in batch:
                bs_code = innercode_to_bs_code(inner_code, stocks_df)
                if bs_code:
                    batch_bs_codes.append(bs_code)
                    inner_to_bs[inner_code] = bs_code
            
            if not batch_bs_codes:
                continue
            
            # baostock查询不支持批量代码查询，需要逐只股票查询
            for inner_code, bs_code in inner_to_bs.items():
                max_query_retries = 2
                query_success = False
                query_data = None
                
                for query_retry in range(max_query_retries):
                    try:
                        # 查询历史数据
                        rs = bs.query_history_k_data_plus(bs_code,
                            "date,code,open,high,low,close,volume",
                            start_date=start_date,
                            end_date=last_day,
                            frequency="d",
                            adjustflag="3")  # 3=后复权
                        
                        if rs.error_code == '0':
                            # 获取数据
                            data_list = []
                            while (rs.error_code == '0') & rs.next():
                                data_list.append(rs.get_row_data())
                            
                            if data_list:
                                # 转换为DataFrame
                                df = pd.DataFrame(data_list, columns=rs.fields)
                                
                                # 数据转换
                                df['date'] = pd.to_datetime(df['date'])
                                df['close'] = df['close'].astype(float)
                                
                                # 重命名以匹配现有格式
                                df = df.rename(columns={'date': 'TradingDay', 'close': 'ClosePrice'})
                                df['InnerCode'] = inner_code
                                
                                # 只保留需要的列
                                df = df[['InnerCode', 'TradingDay', 'ClosePrice']].copy()
                                query_data = df
                                query_success = True
                                break
                            else:
                                if query_retry < max_query_retries - 1:
                                    time.sleep(1)
                        else:
                            print(f"⚠️  baostock查询失败 {bs_code} (第{query_retry+1}次): {rs.error_msg}")
                            if query_retry < max_query_retries - 1:
                                time.sleep(1)
                    
                    except Exception as e:
                        print(f"⚠️  baostock查询异常 {bs_code} (第{query_retry+1}次): {str(e)[:50]}")
                        if query_retry < max_query_retries - 1:
                            time.sleep(1)
                
                if query_success and query_data is not None:
                    all_data.append(query_data)
                else:
                    print(f"⚠️  放弃查询 {bs_code}，已尝试{max_query_retries}次")
                
                # 避免请求频率限制
                time.sleep(0.15)
        
        # 登出（带异常处理）
        try:
            bs.logout()
            print("✅ baostock登出成功")
        except Exception as e:
            print(f"⚠️  baostock登出异常: {str(e)[:50]}")
        
        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)
            print(f"✅ baostock获取数据成功，共{len(result_df)}条记录，涉及{len(inner_codes)}只股票")
            return result_df
        else:
            print("⚠️  baostock未获取到有效数据")
            return pd.DataFrame()
            
    except Exception as e:
        print(f"❌ baostock数据获取失败: {str(e)[:100]}")
        # 确保登出
        try:
            bs.logout()
        except:
            pass
        return pd.DataFrame()

# ==================== 恒生聚源数据库数据获取 ====================
def fetch_prices_finance_mcp(mcp, inner_codes, last_day):
    """使用恒生聚源数据库获取批量股票日线数据"""
    if not inner_codes:
        return pd.DataFrame()
    
    inner_codes_str = ','.join(map(str, inner_codes))
    
    # 先获取最近160个交易日的日期列表
    sql_dates = f"""
    SELECT TradingDate
    FROM qt_tradingdaynew 
    WHERE SecuMarket = 83 
      AND TradingDate <= '{last_day}'
      AND IfTradingDay = 1
    ORDER BY TradingDate DESC
    LIMIT 160
    """
    dates_df = mcp.query(sql_dates)
    if dates_df.empty:
        return pd.DataFrame()
    
    date_list = dates_df['TradingDate'].dt.strftime('%Y-%m-%d').tolist()
    earliest_date = date_list[-1]
    
    # 获取这批股票在这些日期的收盘价
    sql_prices = f"""
    SELECT InnerCode, TradingDay, ClosePrice
    FROM dz_dailyquote 
    WHERE InnerCode IN ({inner_codes_str})
      AND TradingDay >= '{earliest_date}'
      AND TradingDay <= '{last_day}'
    ORDER BY InnerCode, TradingDay
    """
    return mcp.query(sql_prices)

# ==================== 数据质量检查 ====================
def check_data_quality(prices_df):
    """检查收盘价数据质量"""
    if prices_df.empty:
        return False, "空数据"
    
    # 检查收盘价是否在合理范围内 (通常A股股价在1-1000元之间)
    close_prices = prices_df['ClosePrice']
    
    # 移除NaN值
    valid_prices = close_prices.dropna()
    
    if len(valid_prices) == 0:
        return False, "所有收盘价均为NaN"
    
    # 检查是否存在异常值
    # 设置合理范围：0.1元到10000元（考虑复权因素，极端情况可能超出）
    lower_bound = 0.1
    upper_bound = 10000
    
    outliers = valid_prices[(valid_prices < lower_bound) | (valid_prices > upper_bound)]
    
    if len(outliers) > 0:
        outlier_percent = len(outliers) / len(valid_prices) * 100
        print(f"⚠️  数据质量警告: {len(outliers)}个收盘价超出合理范围 ({lower_bound}-{upper_bound})，占比{outlier_percent:.1f}%")
        
        # 如果异常比例过高，可能需要切换数据源
        if outlier_percent > 30:
            return False, f"异常数据比例过高: {outlier_percent:.1f}%"
    
    # 检查数据完整性（是否有足够的历史数据）
    # 这里简单检查：至少需要160个交易日的数据
    stock_counts = prices_df.groupby('InnerCode').size()
    if stock_counts.min() < 160:
        print(f"⚠️  数据完整性警告: 部分股票数据点不足160个")
    
    return True, "数据质量检查通过"

# ==================== 核心数据获取函数（多数据源） ====================
def get_batch_prices(mcp, inner_codes, last_day, stocks_df, data_source_order=None):
    """批量获取股票日线数据，支持多数据源回退"""
    global USED_DATA_SOURCE
    
    if data_source_order is None:
        data_source_order = DATA_SOURCE_PRIORITY
    
    for source in data_source_order:
        try:
            print(f"尝试使用数据源: {source}...")
            
            if source == 'baostock':
                prices_df = fetch_prices_baostock_batch(inner_codes, stocks_df, last_day)
                
                if not prices_df.empty:
                    # 数据质量检查
                    quality_ok, quality_msg = check_data_quality(prices_df)
                    
                    if quality_ok:
                        USED_DATA_SOURCE = source
                        print(f"✅ 使用{source}获取数据成功，共{len(prices_df)}条记录")
                        return prices_df
                    else:
                        print(f"⚠️  {source}数据质量检查失败: {quality_msg}")
                        continue
                else:
                    print(f"⚠️  {source}返回空数据")
                    continue
                    
            elif source == 'tushare':
                prices_df = fetch_prices_tushare_batch(inner_codes, stocks_df, last_day)
                
                if not prices_df.empty:
                    # 数据质量检查
                    quality_ok, quality_msg = check_data_quality(prices_df)
                    
                    if quality_ok:
                        USED_DATA_SOURCE = source
                        print(f"✅ 使用{source}获取数据成功，共{len(prices_df)}条记录")
                        return prices_df
                    else:
                        print(f"⚠️  {source}数据质量检查失败: {quality_msg}")
                        continue
                else:
                    print(f"⚠️  {source}返回空数据")
                    continue
                
            elif source == 'finance_mcp':
                if mcp is None:
                    print("⚠️  FinanceMCP连接未建立，跳过...")
                    continue
                
                prices_df = fetch_prices_finance_mcp(mcp, inner_codes, last_day)
                
                if not prices_df.empty:
                    # 数据质量检查
                    quality_ok, quality_msg = check_data_quality(prices_df)
                    
                    if quality_ok:
                        USED_DATA_SOURCE = source
                        print(f"✅ 使用{source}获取数据成功，共{len(prices_df)}条记录")
                        return prices_df
                    else:
                        print(f"⚠️  {source}数据质量检查失败: {quality_msg}")
                        continue
                else:
                    print(f"⚠️  {source}返回空数据")
                    continue
                    
            else:
                print(f"⚠️  未知数据源: {source}")
                continue
                
        except Exception as e:
            print(f"❌ 数据源{source}获取失败: {str(e)[:100]}")
            continue
    
    # 所有数据源都失败
    print("❌ 所有数据源均失败")
    return pd.DataFrame()

# ==================== 原有辅助函数（保持不变） ====================
def get_last_trading_day(mcp, date):
    """获取指定日期之前的最后一个交易日"""
    sql = f"""
    SELECT MAX(TradingDate) as last_day
    FROM qt_tradingdaynew 
    WHERE SecuMarket = 83 
      AND TradingDate <= '{date}'
      AND IfTradingDay = 1
    """
    df = mcp.query(sql)
    return df.iloc[0]['last_day'].strftime('%Y-%m-%d')

def get_first_trading_day_of_year(mcp, year):
    """获取指定年份的第一个交易日"""
    sql = f"""
    SELECT MIN(TradingDate) as first_day
    FROM qt_tradingdaynew 
    WHERE SecuMarket = 83 
      AND YEAR(TradingDate) = {year}
      AND IfTradingDay = 1
    """
    df = mcp.query(sql)
    return df.iloc[0]['first_day'].strftime('%Y-%m-%d')

def get_stock_codes(mcp):
    """获取所有A股股票代码"""
    sql = """
    SELECT InnerCode, SecuCode, SecuAbbr, ChiName, SecuMarket
    FROM secumain 
    WHERE SecuMarket IN (83, 90) 
      AND ListedState = 1 
      AND SecuCategory = 1
    """
    return mcp.query(sql)

def calculate_ma_and_filter(stock_codes_df, prices_df, first_day):
    """计算移动平均并筛选符合条件的股票"""
    results = []
    
    # 按股票分组处理
    for inner_code, group in prices_df.groupby('InnerCode'):
        # 确保数据按日期排序
        group = group.sort_values('TradingDay')
        
        if len(group) < 160:
            continue
        
        # 计算移动平均
        group['ma20'] = group['ClosePrice'].rolling(window=20, min_periods=20).mean()
        group['ma40'] = group['ClosePrice'].rolling(window=40, min_periods=40).mean()
        group['ma80'] = group['ClosePrice'].rolling(window=80, min_periods=80).mean()
        group['ma160'] = group['ClosePrice'].rolling(window=160, min_periods=160).mean()
        
        latest = group.iloc[-1]
        
        # 检查条件：股价 > ma20 > ma40 > ma80 > ma160
        if (latest['ClosePrice'] > latest['ma20'] > latest['ma40'] > latest['ma80'] > latest['ma160']):
            # 计算年内涨幅
            ytd_group = group[group['TradingDay'] >= first_day]
            if len(ytd_group) >= 2:
                first_price = ytd_group.iloc[0]['ClosePrice']
                ytd_return = (latest['ClosePrice'] / first_price - 1) * 100
            else:
                ytd_return = None
            
            stock_info = stock_codes_df[stock_codes_df['InnerCode'] == inner_code].iloc[0]
            
            results.append({
                'InnerCode': inner_code,
                'SecuCode': stock_info['SecuCode'],
                'SecuAbbr': stock_info['SecuAbbr'],
                'ChiName': stock_info['ChiName'],
                'ClosePrice': round(latest['ClosePrice'], 2),
                'ma20': round(latest['ma20'], 2),
                'ma40': round(latest['ma40'], 2),
                'ma80': round(latest['ma80'], 2),
                'ma160': round(latest['ma160'], 2),
                'YTD_Return': round(ytd_return, 2) if ytd_return is not None else None
            })
    
    return results

# ==================== 主函数（修改版） ====================
def main():
    global USED_DATA_SOURCE
    
    print("=== 全市场A股多头排列筛选验证 ===\\n")
    
    # 连接数据库（带重试）
    mcp = None
    try:
        mcp = connect_finance_mcp_with_retry(max_retries=3, retry_delay=2)
    except Exception as e:
        print(f"❌ 数据库连接失败，无法继续: {e}")
        return
    
    # 日期设置
    today = '2026-02-22'
    year = 2026
    
    # 获取最近一个交易日（因为今天可能非交易日）
    last_day = get_last_trading_day(mcp, today)
    print(f"筛选基准日: {last_day}")
    
    if last_day != today:
        print(f"⚠️  注意：今日（{today}）非交易日，使用最近交易日 {last_day} 的数据进行筛选")
    
    # 获取2026年第一个交易日
    first_day = get_first_trading_day_of_year(mcp, year)
    print(f"2026年第一个交易日: {first_day}")
    
    # 获取所有A股股票代码
    print("获取股票列表中...")
    stocks_df = get_stock_codes(mcp)
    total_stocks = len(stocks_df)
    print(f"共获取 {total_stocks} 只股票")
    
    # 分批处理
    batch_size = 50
    inner_codes = stocks_df['InnerCode'].tolist()

    num_batches = (len(inner_codes) + batch_size - 1) // batch_size
    
    all_results = []
    
    for batch_idx in range(num_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, total_stocks)
        batch_codes = inner_codes[start_idx:end_idx]
        
        print(f"处理批次 {batch_idx+1}/{num_batches} (股票 {start_idx+1}-{end_idx})...")
        
        # 获取批量价格数据（多数据源）
        prices_df = get_batch_prices(mcp, batch_codes, last_day, stocks_df)
        if prices_df.empty:
            print(f"  本批次数据获取失败")
            continue
        
        # 计算并筛选
        batch_results = calculate_ma_and_filter(stocks_df, prices_df, first_day)
        all_results.extend(batch_results)
        
        print(f"  本批次筛选出 {len(batch_results)} 只符合条件的股票")
    
    # 处理结果
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        # 按年内涨幅降序排序
        results_df = results_df.sort_values('YTD_Return', ascending=False)
        
        # 取前20只
        top20 = results_df.head(20)
        
        print(f"\\n✅ 筛选完成！共发现 {len(results_df)} 只股票满足多头排列条件。")
        print(f"📊 取年内涨幅前20只股票如下：")
        
        # 显示简要信息
        display_cols = ['SecuCode', 'SecuAbbr', 'ClosePrice', 'ma20', 'ma40', 'ma80', 'ma160', 'YTD_Return']
        print(top20[display_cols].to_string(index=False))
        
        # 保存结果文件
        output_dir = "outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{today}_多头排列Top20.md")
        
        # 根据实际使用的数据源生成说明
        source_desc = {
            'baostock': '数据来源: baostock',
            'tushare': '数据来源: tushare',
            'finance_mcp': '数据来源: 恒生聚源数据库'
        }
        data_source_note = source_desc.get(USED_DATA_SOURCE, '数据来源: 未知')
        
        # 生成Markdown内容
        md_content = f"# {today} 全市场A股多头排列筛选Top20\\n\\n"
        md_content += f"**筛选基准日**: {last_day}\\n\\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\\n\\n"
        md_content += "**排序方式**: 按年内涨幅（从2026年第一个交易日到筛选基准日）降序排列\\n\\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\\n\\n"
        md_content += f"**数据源**: {USED_DATA_SOURCE or '未知'}\\n\\n"
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\\n"
        
        for _, row in top20.iterrows():
            ytd_display = f"{row['YTD_Return']:.2f}" if pd.notna(row['YTD_Return']) else "N/A"
            md_content += f"| {row['SecuCode']} | {row['SecuAbbr']} | {row['ClosePrice']} | {row['ma20']} | {row['ma40']} | {row['ma80']} | {row['ma160']} | {ytd_display} |\\n"
        
        md_content += f"\\n*{data_source_note}*\\n"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议，不能作为投资依据，注意投资风险，股市有风险，投资需谨慎*\\n"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\\n📁 结果文件已保存: {output_file}")
        
        return top20, output_file
    else:
        print("\\n⚠️ 没有找到任何满足条件的股票。")
        # 根据实际使用的数据源生成说明
        source_desc = {
            'baostock': '数据来源: baostock',
            'tushare': '数据来源: tushare',
            'finance_mcp': '数据来源: 恒生聚源数据库'
        }
        data_source_note = source_desc.get(USED_DATA_SOURCE, '数据来源: 未知')
        
        # 创建空结果文件
        output_dir = "outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{today}_多头排列Top20.md")
        
        md_content = f"# {today} 全市场A股多头排列筛选Top20\\n\\n"
        md_content += f"**筛选基准日**: {last_day}\\n\\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\\n\\n"
        md_content += "**结果**: 没有找到任何满足条件的股票\\n\\n"
        md_content += f"**数据源**: {USED_DATA_SOURCE or '未知'}\\n\\n"
        md_content += f"*{data_source_note}*\\n"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议，不能作为投资依据，注意投资风险，股市有风险，投资需谨慎*\\n"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"📁 空结果文件已保存: {output_file}")
        
        return pd.DataFrame(), output_file

if __name__ == '__main__':
    main()