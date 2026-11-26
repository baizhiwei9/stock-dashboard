#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 v6.0 - 多数据源版
数据源优先级：akshare(东财) → tushare(需token)
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import akshare as ak
import pandas as pd
import numpy as np
import os
import sys
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

# Python3.13 akshare兼容
try:
    sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()
except:
    pass

# Tushare配置
TUSHARE_TOKEN = os.environ.get('COZE_TUSHARE_TOKEN_7649020808588689460',
                                'a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265')

# Tushare 开关：因沙箱多出口IP触发"IP数量超限"，默认禁用，akshare失败时不再回退到tushare
# 如需恢复tushare回退，将 USE_TUSHARE 改为 True 即可
USE_TUSHARE = False


def ak_retry(func, max_retries=3, delay=5, **kwargs):
    """akshare调用重试封装"""
    for attempt in range(max_retries):
        try:
            result = func(**kwargs)
            if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                return result
        except Exception as e:
            print(f"  第{attempt+1}次尝试失败: {e}")
        if attempt < max_retries - 1:
            wait = delay * (attempt + 1)
            print(f"  等待{wait}秒后重试...")
            time.sleep(wait)
    return None


def get_stock_list_akshare():
    """通过akshare获取股票列表"""
    print("[akshare] 获取全市场A股列表...")
    stock_df = ak_retry(ak.stock_zh_a_spot_em, max_retries=3, delay=8)
    if stock_df is None:
        return None
    result = stock_df[['代码', '名称']].copy()
    result.columns = ['code', 'name']
    print(f"[akshare] 获取到 {len(result)} 只股票")
    return result


def get_stock_list_tushare():
    """通过tushare获取股票列表（备用）"""
    print("[tushare] 获取全市场A股列表...")
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        if df is None or df.empty:
            return None
        # ts_code格式：000001.SZ → 提取纯数字代码
        result = pd.DataFrame({
            'code': df['ts_code'].str[:6],
            'name': df['name']
        })
        print(f"[tushare] 获取到 {len(result)} 只股票")
        return result
    except Exception as e:
        print(f"[tushare] 获取股票列表失败: {e}")
        return None


def get_daily_akshare(codes):
    """通过akshare批量获取日线数据"""
    print(f"[akshare] 并行获取日线数据（10线程）...")
    all_data = []

    def _get_one(code):
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date='20241001',
                    end_date=TODAY.replace('-', ''),
                    adjust="qfq"
                )
                if df is not None and not df.empty:
                    df['code'] = code
                    return df
                return None
            except Exception:
                if attempt == 0:
                    time.sleep(1)
                else:
                    return None

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_get_one, code): code for code in codes}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 500 == 0:
                print(f"  进度: {completed}/{len(codes)}")
            result = future.result()
            if result is not None:
                all_data.append(result)

    print(f"  进度: {len(codes)}/{len(codes)}")
    return all_data


def get_daily_tushare():
    """通过tushare按日期批量获取日线数据（备用，全市场一次拉完）
    需要拉约400个交易日(20241001起)，每2次API调用/分钟限制下约需30分钟
    优化：只拉最近200个交易日的数据（够算MA160即可），大幅减少API调用
    """
    print(f"[tushare] 按日期批量获取日线数据...")
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)

        end_date = TODAY.replace('-', '')

        # 先获取交易日历，只取最近170个交易日（够算MA160+缓冲）
        cal_df = pro.trade_cal(exchange='SSE', end_date=end_date, is_open='1')
        if cal_df is None or cal_df.empty:
            print("[tushare] 获取交易日历失败")
            return []
        
        # 只取最近170个交易日
        cal_df = cal_df.tail(170)
        trade_dates = cal_df['cal_date'].tolist()
        print(f"  需获取 {len(trade_dates)} 个交易日数据")

        all_dfs = []
        call_count = 0

        for i, trade_date in enumerate(trade_dates):
            try:
                df = pro.daily(trade_date=trade_date)
                call_count += 1

                if call_count % 80 == 0:
                    print(f"  进度: {i+1}/{len(trade_dates)}, API调用{call_count}次，暂停65s...")
                    time.sleep(65)

                if df is not None and not df.empty:
                    all_dfs.append(df)

            except Exception as e:
                if '每分钟' in str(e) or 'limit' in str(e).lower() or 'freq' in str(e).lower():
                    print(f"  触发限流，暂停65s...")
                    time.sleep(65)
                    try:
                        df = pro.daily(trade_date=trade_date)
                        call_count += 1
                        if df is not None and not df.empty:
                            all_dfs.append(df)
                    except:
                        pass
                else:
                    print(f"  日期{trade_date}获取失败: {e}")

            if (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(trade_dates)}")

        if not all_dfs:
            print("[tushare] 未获取到任何日线数据")
            return []

        combined = pd.concat(all_dfs, ignore_index=True)
        combined['code'] = combined['ts_code'].str[:6]
        combined['trade_date'] = pd.to_datetime(combined['trade_date'])
        combined['close'] = combined['close']  # tushare不复权，但均线方向判断影响不大

        print(f"  tushare日线获取完成，API调用{call_count}次，共{len(combined)}条记录")
        return [combined]  # 返回单元素列表，外层concat处理
    except Exception as e:
        print(f"[tushare] 获取日线数据失败: {e}")
        return []


def filter_st_and_board(stock_df):
    """过滤ST/退市/科创板/北交所"""
    stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
    stock_df = stock_df[stock_df['code'].str.match(r'^\d{6}$')]
    stock_df = stock_df[~stock_df['code'].str.startswith('688')]  # 科创板
    stock_df = stock_df[~stock_df['code'].str.startswith('8')]    # 北交所
    stock_df = stock_df[~stock_df['code'].str.startswith('4')]    # 北交所
    return stock_df


def process_and_screen(all_data, codes, stock_df):
    """处理日线数据并筛选多头排列"""
    if not all_data:
        print("无日线数据")
        return None

    daily_df = pd.concat(all_data, ignore_index=True)

    # 日期和收盘价标准化
    if 'close' not in daily_df.columns:
        if '收盘' in daily_df.columns:
            daily_df['close'] = daily_df['收盘']
            daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')
        elif 'trade_date' not in daily_df.columns:
            print("无法识别日期/收盘列")
            return None

    if 'trade_date' not in daily_df.columns and '日期' in daily_df.columns:
        daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')

    stock_counts = daily_df.groupby('code').size()
    print(f"获取到 {len(daily_df)} 条数据")
    print(f"平均每只股票: {stock_counts.mean():.0f} 条, 最少: {stock_counts.min()}, 最多: {stock_counts.max()}")

    # 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    qualified = 0

    for idx, code in enumerate(codes):
        stock_daily = daily_df[daily_df['code'] == code].copy()

        if len(stock_daily) < 160:
            continue

        stock_daily = stock_daily.sort_values('trade_date')

        stock_daily['ma20'] = stock_daily['close'].rolling(window=20, min_periods=20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(window=40, min_periods=40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(window=80, min_periods=80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(window=160, min_periods=160).mean()

        latest = stock_daily.iloc[-1]

        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] >
                latest['ma80'] > latest['ma160']):
                qualified += 1

                ytd_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(FIRST_DAY)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None

                exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'

                results.append({
                    '股票代码': code,
                    '交易所': exchange,
                    '股票简称': stock_df[stock_df['code'] == code]['name'].values[0] if code in stock_df['code'].values else code,
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                })

        if idx % 1000 == 0 and idx > 0:
            print(f"筛选进度: {idx}/{len(codes)} (已发现{qualified}只)")

    print(f"\n筛选完成！共 {len(results)} 只股票满足多头排列条件")
    return results


def main():
    print("=" * 60)
    print("A股多头排列筛选器 v6.0 (多数据源)")
    print("=" * 60)

    global TODAY, FIRST_DAY

    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-05"

    print(f"筛选基准日: {TODAY}")

    # 1. 获取股票列表（优先akshare，失败用tushare - 受USE_TUSHARE开关控制）
    stock_df = get_stock_list_akshare()
    data_source = 'akshare'

    if stock_df is None:
        if USE_TUSHARE:
            print("\n⚠️ akshare获取失败，切换tushare备用源...")
            stock_df = get_stock_list_tushare()
            data_source = 'tushare'
        else:
            print("\n⚠️ akshare获取失败，USE_TUSHARE=False，跳过tushare回退")

    if stock_df is None:
        print("❌ 所有数据源获取股票列表失败，退出")
        return

    # 过滤
    stock_df = filter_st_and_board(stock_df)
    codes = stock_df['code'].unique().tolist()
    print(f"排除ST/科创板/北交所后剩余 {len(codes)} 只")

    if not codes:
        print("没有符合条件的股票")
        return

    # 2. 获取日线数据（优先akshare，失败用tushare - 受USE_TUSHARE开关控制）
    if data_source == 'akshare':
        all_data = get_daily_akshare(codes)
        # 检查获取率，低于50%则切换tushare
        if len(all_data) < len(codes) * 0.5:
            if USE_TUSHARE:
                print(f"\n⚠️ akshare日线获取率仅 {len(all_data)}/{len(codes)}，切换tushare...")
                data_source = 'tushare'
            else:
                print(f"\n⚠️ akshare日线获取率仅 {len(all_data)}/{len(codes)}，USE_TUSHARE=False，继续使用akshare数据")

    if data_source == 'tushare':
        all_data = get_daily_tushare()

    # 3. 筛选多头排列
    results = process_and_screen(all_data, codes, stock_df)

    if not results:
        print("⚠️ 无满足条件的股票")
        return

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('年内涨幅', ascending=False)
    top20 = results_df.head(20)

    print("\n📊 年内涨幅Top20：")
    for i, row in top20.iterrows():
        code_full = f"{row['交易所']}.{row['股票代码']}"
        ytd = f"{row['年内涨幅']:.2f}%" if pd.notna(row['年内涨幅']) else "N/A"
        print(f"  {code_full} {row['股票简称']} 价:{row['当前价']} 涨:{ytd}")

    # 生成Markdown报告
    md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
    md_content += f"**筛选基准日**: {TODAY}\n\n"
    md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
    md_content += "**排序方式**: 按年内涨幅降序排列\n\n"
    md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"
    md_content += f"**数据源**: {data_source}\n\n"
    md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
    md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"

    for _, row in top20.iterrows():
        code_full = f"{row['交易所']}.{row['股票代码']}"
        ytd_display = f"{row['年内涨幅']:.2f}" if pd.notna(row['年内涨幅']) else "N/A"
        md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {ytd_display} |\n"

    md_content += f"\n*数据源: {data_source}*\n"
    md_content += "*案例分析学习材料，不构成投资建议*\n"
    md_content += "*股市有风险，投资需谨慎*\n"

    # 保存结果
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    root_dir = os.path.dirname(project_dir)
    output_dir = os.path.join(root_dir, "outputs", "股票筛选")
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"\n📁 已保存: {output_file}")
    print("=" * 60)
    print("✅ 任务完成！")
    print("=" * 60)

    # 推送飞书
    sys.path.insert(0, '/Coze/Drive/蓁宝/所有对话/主对话')
    from feishu_push import push_to_feishu
    push_to_feishu(file_path=output_file)


if __name__ == '__main__':
    main()
