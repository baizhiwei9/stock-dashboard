import baostock as bs
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
import time

# 固定日期
LAST_DAY = '2026-02-08'  # 筛选基准日
FIRST_DAY = '2026-01-05'  # 2026年第一个交易日

def get_a_stock_codes():
    """获取所有A股股票代码，排除指数"""
    lg = bs.login()
    if lg.error_code != '0':
        print(f"登录失败: {lg.error_msg}")
        return pd.DataFrame()
    
    # 获取所有证券基本信息
    rs = bs.query_stock_basic()
    if rs.error_code != '0':
        print(f"查询股票列表失败: {rs.error_msg}")
        bs.logout()
        return pd.DataFrame()
    
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
    
    bs.logout()
    
    if not data_list:
        return pd.DataFrame()
    
    # 转换为DataFrame
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 过滤A股股票
    # 股票代码格式：sh.600000, sz.000001, bj.430001
    # 排除指数（上证综合指数等）
    # 根据type字段：1-股票，2-指数，3-债券，4-基金
    df = df[df['type'] == '1']  # 只保留股票
    
    # 排除ST/*ST股票（可选，根据实际需要）
    # df = df[~df['code_name'].str.contains('ST')]
    
    print(f"获取到 {len(df)} 只A股股票")
    return df

def get_stock_history(ts_code, start_date, end_date):
    """获取单只股票的历史日线数据"""
    # baostock需要登录会话
    lg = bs.login()
    if lg.error_code != '0':
        print(f"登录失败: {lg.error_msg}")
        return None
    
    # 查询历史数据
    # fields: date,code,open,high,low,close,volume,amount,adjustflag
    rs = bs.query_history_k_data_plus(ts_code,
        "date,code,open,high,low,close,volume",
        start_date=start_date, end_date=end_date,
        frequency="d", adjustflag="3")  # 3=后复权
    
    data_list = []
    if rs.error_code == '0':
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
    
    bs.logout()
    
    if not data_list:
        return None
    
    # 转换为DataFrame
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 数据类型转换
    df['date'] = pd.to_datetime(df['date'])
    df['close'] = df['close'].astype(float)
    
    # 按日期排序
    df = df.sort_values('date')
    
    return df

def calculate_ma_and_filter(stock_df):
    """计算移动平均并筛选符合条件的股票"""
    results = []
    
    # 计算开始日期：需要足够长的历史数据计算ma160（约8个月）
    # 160个交易日大约相当于7-8个月，这里为了确保数据充足，回溯一年
    start_date = (datetime.strptime(LAST_DAY, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')
    
    total_stocks = len(stock_df)
    print(f"开始处理 {total_stocks} 只A股股票...")
    
    processed = 0
    for idx, row in stock_df.iterrows():
        ts_code = row['code']
        stock_name = row['code_name']
        ipo_date = row['ipoDate']
        
        if processed % 100 == 0:
            print(f"处理进度: {processed}/{total_stocks}")
        
        # 获取日线数据
        daily_df = get_stock_history(ts_code, start_date, LAST_DAY)
        if daily_df is None or len(daily_df) < 160:
            processed += 1
            continue
        
        # 计算移动平均
        daily_df['ma20'] = daily_df['close'].rolling(window=20, min_periods=20).mean()
        daily_df['ma40'] = daily_df['close'].rolling(window=40, min_periods=40).mean()
        daily_df['ma80'] = daily_df['close'].rolling(window=80, min_periods=80).mean()
        daily_df['ma160'] = daily_df['close'].rolling(window=160, min_periods=160).mean()
        
        latest = daily_df.iloc[-1]
        
        # 检查条件：股价 > ma20 > ma40 > ma80 > ma160
        # 需要确保所有ma值都不是NaN
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > latest['ma80'] > latest['ma160']):
                # 计算年内涨幅
                # 如果股票在年内上市，则从上市首日开始计算
                effective_start = max(FIRST_DAY, ipo_date)
                
                ytd_df = daily_df[daily_df['date'] >= effective_start]
                if len(ytd_df) >= 2:
                    first_price = ytd_df.iloc[0]['close']
                    ytd_return = (latest['close'] / first_price - 1) * 100
                else:
                    ytd_return = None
                
                results.append({
                    'ts_code': ts_code,
                    'name': stock_name,
                    'close': round(latest['close'], 2),
                    'ma20': round(latest['ma20'], 2),
                    'ma40': round(latest['ma40'], 2),
                    'ma80': round(latest['ma80'], 2),
                    'ma160': round(latest['ma160'], 2),
                    'YTD_Return': round(ytd_return, 2) if ytd_return is not None else None,
                    'ipo_date': ipo_date
                })
        
        processed += 1
        # 短暂延迟避免请求过于频繁
        time.sleep(0.05)
    
    return results

def main():
    print("=== 使用baostock数据执行全市场A股多头排列筛选验证 ===\n")
    
    print(f"筛选基准日: {LAST_DAY}")
    print(f"2026年第一个交易日: {FIRST_DAY}")
    
    # 获取所有A股股票代码
    print("获取A股股票列表中...")
    stocks_df = get_a_stock_codes()
    if stocks_df.empty:
        print("❌ 无法获取股票列表")
        return
    
    total_stocks = len(stocks_df)
    print(f"共获取 {total_stocks} 只A股股票")
    
    # 计算并筛选
    print("\n开始筛选符合条件的股票...")
    all_results = calculate_ma_and_filter(stocks_df)
    
    # 处理结果
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        # 按年内涨幅降序排序
        results_df = results_df.sort_values('YTD_Return', ascending=False)
        
        # 取前20只
        top20 = results_df.head(20)
        
        print(f"\n✅ 筛选完成！共发现 {len(results_df)} 只股票满足多头排列条件。")
        print(f"📊 取年内涨幅前20只股票如下：")
        
        # 显示简要信息
        display_cols = ['ts_code', 'name', 'close', 'ma20', 'ma40', 'ma80', 'ma160', 'YTD_Return']
        print(top20[display_cols].to_string(index=False))
        
        # 保存结果文件
        output_dir = "outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{LAST_DAY}_tushare验证_多头排列Top20.md")
        
        # 生成Markdown内容
        md_content = f"# {LAST_DAY} 全市场A股多头排列筛选Top20（baostock验证）\n\n"
        md_content += f"**筛选基准日**: {LAST_DAY}\n\n"
        md_content += f"**2026年第一个交易日**: {FIRST_DAY}\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**排序方式**: 按年内涨幅（从2026年第一个交易日到筛选基准日）降序排列\n\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"
        md_content += "| 股票代码 | 股票名称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        
        for _, row in top20.iterrows():
            ytd_display = f"{row['YTD_Return']:.2f}" if pd.notna(row['YTD_Return']) else "N/A"
            md_content += f"| {row['ts_code']} | {row['name']} | {row['close']} | {row['ma20']} | {row['ma40']} | {row['ma80']} | {row['ma160']} | {ytd_display} |\n"
        
        md_content += f"\n*数据来源: baostock*"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议，不能作为投资依据，注意投资风险，股市有风险，投资需谨慎*\n"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 结果文件已保存: {output_file}")
        
        return top20, output_file
    else:
        print("\n⚠️ 没有找到任何满足条件的股票。")
        # 创建空结果文件
        output_dir = "outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{LAST_DAY}_tushare验证_多头排列Top20.md")
        
        md_content = f"# {LAST_DAY} 全市场A股多头排列筛选Top20（baostock验证）\n\n"
        md_content += f"**筛选基准日**: {LAST_DAY}\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**结果**: 没有找到任何满足条件的股票\n\n"
        md_content += f"*数据来源: baostock*"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议，不能作为投资依据，注意投资风险，股市有风险，投资需谨慎*\n"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"📁 空结果文件已保存: {output_file}")
        
        return pd.DataFrame(), output_file

if __name__ == '__main__':
    main()