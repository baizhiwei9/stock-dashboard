#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 v7.0 - 增强版
数据源：akshare(东财)
筛选条件：
1. 股价 > MA20 > MA40 > MA80 > MA160
2. MA80 和 MA160 必须向上（当日值 > 前一日值）
3. 近半年（120交易日）未跌破 MA40
4. 近1个月（20交易日）未跌破 MA20
排序：按当月涨幅降序
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


def get_stock_list():
    """获取全A股列表"""
    print("[akshare] 获取全市场A股列表...")
    stock_df = ak_retry(ak.stock_zh_a_spot_em, max_retries=3, delay=8)
    if stock_df is None:
        return None
    result = stock_df[['代码', '名称']].copy()
    result.columns = ['code', 'name']
    print(f"[akshare] 获取到 {len(result)} 只股票")
    return result


def filter_st_and_board(stock_df):
    """过滤ST/退市/科创板/北交所"""
    stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
    stock_df = stock_df[stock_df['code'].str.match(r'^\d{6}$')]
    stock_df = stock_df[~stock_df['code'].str.startswith('688')]  # 科创板
    stock_df = stock_df[~stock_df['code'].str.startswith('8')]    # 北交所
    stock_df = stock_df[~stock_df['code'].str.startswith('4')]    # 北交所
    return stock_df


def get_daily_data(codes):
    """并行获取日线数据（需要足够长以计算MA160+近120交易日检查）"""
    print(f"[akshare] 并行获取日线数据（10线程）...")
    all_data = []

    # 需要至少 160(MA160) + 120(检查跌破MA40的窗口) ≈ 280 个交易日
    # 为安全起见，拉取约 350 个交易日的数据（约14个月）
    start_date = (datetime.now() - timedelta(days=420)).strftime('%Y%m%d')
    end_date = datetime.now().strftime('%Y%m%d')

    def _get_one(code):
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
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


def process_and_screen(all_data, codes, stock_df):
    """处理日线数据并筛选"""
    if not all_data:
        print("无日线数据")
        return None

    daily_df = pd.concat(all_data, ignore_index=True)

    # 列名标准化
    if 'close' not in daily_df.columns:
        if '收盘' in daily_df.columns:
            daily_df['close'] = daily_df['收盘']
            daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')
        else:
            print("无法识别日期/收盘列")
            return None

    if 'trade_date' not in daily_df.columns and '日期' in daily_df.columns:
        daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')

    stock_counts = daily_df.groupby('code').size()
    print(f"获取到 {len(daily_df)} 条数据")
    print(f"平均每只股票: {stock_counts.mean():.0f} 条, 最少: {stock_counts.min()}, 最多: {stock_counts.max()}")

    # 当月第一个交易日
    month_start = datetime.now().strftime('%Y-%m-01')

    print("\n计算均线并筛选...")
    results = []
    qualified = 0

    for idx, code in enumerate(codes):
        stock_daily = daily_df[daily_df['code'] == code].copy()

        if len(stock_daily) < 280:  # 需要 MA160 + 120交易日窗口
            continue

        stock_daily = stock_daily.sort_values('trade_date').reset_index(drop=True)

        # 计算均线
        stock_daily['ma20'] = stock_daily['close'].rolling(window=20, min_periods=20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(window=40, min_periods=40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(window=80, min_periods=80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(window=160, min_periods=160).mean()

        latest = stock_daily.iloc[-1]
        prev = stock_daily.iloc[-2]  # 前一日用于判断均线方向

        # 条件1：基础多头排列
        if not (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and
                pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            continue
        if not (latest['close'] > latest['ma20'] > latest['ma40'] >
                latest['ma80'] > latest['ma160']):
            continue

        # 条件2：MA80 和 MA160 必须向上
        if not (pd.notna(prev['ma80']) and pd.notna(prev['ma160'])):
            continue
        if latest['ma80'] <= prev['ma80']:
            continue
        if latest['ma160'] <= prev['ma160']:
            continue

        # 条件3：近120交易日未跌破 MA40
        recent_120 = stock_daily.tail(120)
        if (recent_120['close'] < recent_120['ma40']).any():
            continue

        # 条件4：近20交易日未跌破 MA20
        recent_20 = stock_daily.tail(20)
        if (recent_20['close'] < recent_20['ma20']).any():
            continue

        # 通过所有条件！
        qualified += 1

        # 计算当月涨幅
        month_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(month_start)]
        if len(month_df) >= 2:
            month_return = (latest['close'] / month_df.iloc[0]['close'] - 1) * 100
        elif len(month_df) == 1:
            month_return = 0.0
        else:
            month_return = None

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
            'MA80方向': '↑',
            'MA160方向': '↑',
            '当月涨幅': round(month_return, 2) if month_return is not None else None
        })

        if qualified % 100 == 0:
            print(f"  已筛选出 {qualified} 只 (已扫描 {idx+1}/{len(codes)})")

    print(f"\n筛选完成！共 {len(results)} 只股票满足全部条件")
    return results


def main():
    print("=" * 60)
    print("A股多头排列筛选器 v7.0 (增强版)")
    print("=" * 60)

    global TODAY
    TODAY = datetime.now().strftime('%Y-%m-%d')
    print(f"筛选基准日: {TODAY}")

    # 1. 获取股票列表
    stock_df = get_stock_list()
    if stock_df is None:
        print("❌ 获取股票列表失败，退出")
        return

    stock_df = filter_st_and_board(stock_df)
    codes = stock_df['code'].unique().tolist()
    print(f"排除ST/科创板/北交所后剩余 {len(codes)} 只")

    if not codes:
        print("没有符合条件的股票")
        return

    # 2. 获取日线数据
    all_data = get_daily_data(codes)
    if not all_data:
        print("❌ 获取日线数据失败")
        return

    # 3. 筛选
    results = process_and_screen(all_data, codes, stock_df)

    if not results:
        print("⚠️ 无满足条件的股票")
        # 仍然生成空报告
        results_df = pd.DataFrame()
    else:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('当月涨幅', ascending=False)

    top20 = results_df.head(20) if len(results_df) > 0 else results_df

    # 打印结果
    if len(results_df) > 0:
        print("\n📊 当月涨幅Top20：")
        for i, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            mtd = f"{row['当月涨幅']:.2f}%" if pd.notna(row['当月涨幅']) else "N/A"
            print(f"  {code_full} {row['股票简称']} 价:{row['当前价']} 月涨:{mtd}")

    # 生成Markdown报告
    md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
    md_content += f"**筛选基准日**: {TODAY}\n\n"
    md_content += "**筛选条件**:\n"
    md_content += "1. 股价 > MA20 > MA40 > MA80 > MA160（完整多头排列）\n"
    md_content += "2. MA80 和 MA160 必须向上（斜率>0）\n"
    md_content += "3. 近半年（120交易日）未跌破 MA40\n"
    md_content += "4. 近1个月（20交易日）未跌破 MA20\n\n"
    md_content += "**排序方式**: 按当月涨幅降序排列\n\n"
    md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"

    if len(results_df) > 0:
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 当月涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            mtd_display = f"{row['当月涨幅']:.2f}" if pd.notna(row['当月涨幅']) else "N/A"
            md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {mtd_display} |\n"
    else:
        md_content += "⚠️ **今日无股票满足全部条件**\n"

    md_content += f"\n*数据源: akshare(东财)*\n"
    md_content += "*案例分析学习材料，不构成投资建议*\n"
    md_content += "*股市有风险，投资需谨慎*\n"

    # 保存结果
    output_dir = "./长期计划/缠论主升浪/outputs/股票筛选"
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"\n📁 已保存: {output_file}")
    print("=" * 60)
    print("✅ 任务完成！")
    print("=" * 60)


if __name__ == '__main__':
    main()
