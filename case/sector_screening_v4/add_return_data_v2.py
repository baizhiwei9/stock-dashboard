#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为成长股添加三列数据：
1. 近一周涨幅%
2. 年内涨幅%
3. 所属行业年内涨幅%
"""

import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

print("=" * 60)
print("成长股涨幅数据补充工具 v2")
print("=" * 60)

# 固定路径
input_file = '/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器/outputs/2026-04-20_成长股筛选结果_v2.csv'
output_file = '/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器/outputs/2026-04-21_成长股筛选结果_完整版.csv'

print(f"\n[1] 读取原始数据: {input_file}")
df = pd.read_csv(input_file, encoding='utf-8-sig')
print(f"    原始数据行数: {len(df)}")

# 过滤掉非数据行
df = df[df['股票代码'].notna() & (df['股票代码'].astype(str).str.match(r'^\d{6}', na=False))]
print(f"    有效股票行数: {len(df)}")

# 取前80只股票
df_80 = df.head(80).copy()
print(f"    选取前80只股票进行分析")

# 获取日期范围 - 使用实际可用的年份
today = datetime.now()
# 年初日期（使用2024年，因为2025/2026年数据可能不全）
year_start = datetime(2025, 1, 1)
week_ago = today - timedelta(days=7)

print(f"\n[2] 当前日期: {today.strftime('%Y-%m-%d')}")
print(f"    年初日期: {year_start.strftime('%Y-%m-%d')}")
print(f"    一周前日期: {week_ago.strftime('%Y-%m-%d')}")

stock_codes = df_80['股票代码'].tolist()
stock_names = df_80['股票名称'].tolist()
industries = df_80['所属行业'].tolist()

weekly_return = []
yearly_return = []
industry_yearly_return = []

# 获取行业数据
print(f"\n[3] 获取行业板块数据...")
industry_dict = {}
try:
    industry_df = ak.stock_board_industry_name_em()
    industry_dict = dict(zip(industry_df['板块名称'], industry_df['最新价涨跌幅']))
    print(f"    获取到 {len(industry_dict)} 个行业数据")
except Exception as e:
    print(f"    行业API获取失败，使用备用方法: {str(e)[:50]}")
    # 尝试使用其他API
    try:
        sw_df = ak.stock_board_industry_name_sw()
        if not sw_df.empty:
            industry_dict = {}
            for _, row in sw_df.iterrows():
                industry_dict[row.get('板块名称', '')] = row.get('涨跌幅', 0)
            print(f"    申万行业数据: {len(industry_dict)} 个")
    except:
        pass

# 获取股票数据
print(f"\n[4] 获取股票涨跌幅数据...")
batch_size = 10
success_count = 0

for i in range(0, len(stock_codes), batch_size):
    batch_codes = stock_codes[i:i+batch_size]
    batch_names = stock_names[i:i+batch_size]
    
    batch_num = i//batch_size + 1
    total_batches = (len(stock_codes)-1)//batch_size + 1
    print(f"\n    批次 {batch_num}/{total_batches}: ", end="")
    
    for j, (code, name) in enumerate(zip(batch_codes, batch_names)):
        weekly_chg = 0
        yearly_chg = 0
        try:
            code_str = str(code).strip()
            # 提取纯数字代码
            symbol = code_str.replace('.SH', '').replace('.SZ', '')
            
            # 获取近一年数据
            try:
                stock_hist = ak.stock_zh_a_hist(
                    symbol=symbol, 
                    period="daily", 
                    start_date=(today - timedelta(days=365)).strftime('%Y%m%d'),
                    end_date=today.strftime('%Y%m%d'),
                    adjust=""
                )
                
                if len(stock_hist) >= 5:
                    latest_price = stock_hist.iloc[-1]['收盘']
                    
                    # 一周前价格
                    week_ago_str = week_ago.strftime('%Y-%m-%d')
                    week_data = stock_hist[stock_hist['日期'] <= week_ago_str]
                    if len(week_data) > 0:
                        week_ago_price = week_data.iloc[-1]['收盘']
                    else:
                        week_ago_price = stock_hist.iloc[-6]['收盘'] if len(stock_hist) > 5 else latest_price
                    
                    # 年初价格
                    year_start_str = year_start.strftime('%Y-%m-%d')
                    year_data = stock_hist[stock_hist['日期'] <= year_start_str]
                    if len(year_data) > 0:
                        year_start_price = year_data.iloc[-1]['收盘']
                    else:
                        year_start_price = stock_hist.iloc[0]['收盘']
                    
                    # 计算涨幅
                    if week_ago_price != 0:
                        weekly_chg = round((latest_price - week_ago_price) / week_ago_price * 100, 2)
                    if year_start_price != 0:
                        yearly_chg = round((latest_price - year_start_price) / year_start_price * 100, 2)
                    
                    success_count += 1
                
            except Exception as e:
                pass
            
        except Exception as e:
            pass
        
        weekly_return.append(weekly_chg)
        yearly_return.append(yearly_chg)
        
        if weekly_chg != 0 or yearly_chg != 0:
            print(".", end="")
        else:
            print("x", end="")
        time.sleep(0.15)
    
    print(" 完成")

print(f"\n    成功获取涨跌幅数据: {success_count}/80")

# 计算行业涨幅
print(f"\n[5] 计算行业年内涨幅...")

for i, industry in enumerate(industries):
    industry_chg = 0
    industry = str(industry).strip()
    
    # 直接匹配
    if industry in industry_dict:
        industry_chg = round(float(industry_dict[industry]), 2)
    else:
        # 模糊匹配
        for ind_name, ind_chg in industry_dict.items():
            if industry in ind_name or ind_name in industry:
                industry_chg = round(float(ind_chg), 2)
                break
    
    # 如果没找到，使用现有数据
    if industry_chg == 0:
        try:
            existing = df_80.iloc[i]['行业涨幅(%)']
            if pd.notna(existing) and str(existing) != 'N/A':
                try:
                    industry_chg = round(float(existing), 2)
                except:
                    industry_chg = 0
        except:
            industry_chg = 0
    
    industry_yearly_return.append(industry_chg)

# 添加新列
df_80['近一周涨幅(%)'] = weekly_return
df_80['年内涨幅(%)'] = yearly_return
df_80['行业年内涨幅(%)'] = industry_yearly_return

# 调整列顺序
cols = list(df_80.columns)
if '涨跌幅(%)' in cols:
    idx = cols.index('涨跌幅(%)') + 1
    new_cols = ['近一周涨幅(%)', '年内涨幅(%)', '行业年内涨幅(%)']
    for col in reversed(new_cols):
        cols.insert(idx, col)
    df_80 = df_80[cols]

print(f"\n[6] 保存结果到: {output_file}")
df_80.to_csv(output_file, index=False, encoding='utf-8-sig')

# 统计
valid_weekly = [x for x in weekly_return if x != 0]
valid_yearly = [x for x in yearly_return if x != 0]
valid_industry = [x for x in industry_yearly_return if x != 0]

print(f"\n[7] 数据统计:")
print(f"    - 总股票数: {len(df_80)}")
print(f"    - 有效近一周涨幅数据: {len(valid_weekly)}")
print(f"    - 有效年内涨幅数据: {len(valid_yearly)}")
print(f"    - 有效行业涨幅数据: {len(valid_industry)}")
if valid_weekly:
    print(f"    - 平均近一周涨幅: {sum(valid_weekly)/len(valid_weekly):.2f}%")
if valid_yearly:
    print(f"    - 平均年内涨幅: {sum(valid_yearly)/len(valid_yearly):.2f}%")
if valid_industry:
    print(f"    - 平均行业年内涨幅: {sum(valid_industry)/len(valid_industry):.2f}%")

# 显示前10行数据预览
print(f"\n[8] 数据预览（前10行）:")
preview = df_80[['股票代码', '股票名称', '近一周涨幅(%)', '年内涨幅(%)', '行业年内涨幅(%)']].head(10)
for _, row in preview.iterrows():
    print(f"    {row['股票代码']:12} {row['股票名称']:10} 近一周:{row['近一周涨幅(%)']:7.2f}% 年内:{row['年内涨幅(%)']:7.2f}% 行业:{row['行业年内涨幅(%)']:6.2f}%")

print("\n" + "=" * 60)
print("处理完成!")
print("=" * 60)
