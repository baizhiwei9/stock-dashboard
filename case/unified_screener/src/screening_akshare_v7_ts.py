#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 v7.1 - tushare备用版（带降级逻辑）
筛选条件：
【严格版】
1. 股价 > MA20 > MA40 > MA80 > MA160
2. MA80 和 MA160 必须向上
3. 近半年（120交易日）未跌破 MA40
4. 近1个月（20交易日）未跌破 MA20
【宽松版】（当严格版结果<10只时自动降级）
- 去掉条件4
排序：按当月涨幅降序
"""

import pandas as pd
import numpy as np
import os
import sys
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()
except:
    pass

TUSHARE_TOKEN = os.environ.get('COZE_TUSHARE_TOKEN_7649020808588689460',
                                'a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265')


def main():
    print("=" * 60)
    print("A股多头排列筛选器 v7.0 (tushare版)")
    print("=" * 60)

    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)

    TODAY = datetime.now().strftime('%Y-%m-%d')
    end_date = TODAY.replace('-', '')
    print(f"筛选基准日: {TODAY}")

    # 1. 获取股票列表
    print("\n[tushare] 获取股票列表...")
    stock_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    stock_df['code'] = stock_df['ts_code'].str[:6]
    # 过滤ST/科创板/北交所
    stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
    stock_df = stock_df[stock_df['code'].str.match(r'^\d{6}$')]
    stock_df = stock_df[~stock_df['code'].str.startswith('688')]
    stock_df = stock_df[~stock_df['code'].str.startswith('8')]
    stock_df = stock_df[~stock_df['code'].str.startswith('4')]
    codes = stock_df['code'].unique().tolist()
    print(f"排除ST/科创板/北交所后剩余 {len(codes)} 只")

    # 2. 获取交易日历
    cal_df = pro.trade_cal(exchange='SSE', end_date=end_date, is_open='1')
    cal_df = cal_df.sort_values('cal_date')
    # 取最近350个交易日（够算MA160+120窗口）
    trade_dates = cal_df.tail(350)['cal_date'].tolist()
    print(f"需获取 {len(trade_dates)} 个交易日数据")

    # 3. 按日期拉取全市场日线数据
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
            if '每分钟' in str(e) or 'limit' in str(e).lower():
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
        print("❌ 未获取到日线数据")
        return

    daily_df = pd.concat(all_dfs, ignore_index=True)
    daily_df['code'] = daily_df['ts_code'].str[:6]
    daily_df['trade_date'] = pd.to_datetime(daily_df['trade_date'])
    print(f"共获取 {len(daily_df)} 条日线记录")

    # 当月第一个交易日
    month_start = TODAY[:8] + '01'

    # 4. 筛选
    print("\n【严格版】计算均线并筛选...")
    results_strict = []
    results_loose = []  # 宽松版（去掉条件4）
    count_strict = 0
    count_loose = 0

    for idx, code in enumerate(codes):
        stock_daily = daily_df[daily_df['code'] == code].copy()
        if len(stock_daily) < 280:
            continue

        stock_daily = stock_daily.sort_values('trade_date').reset_index(drop=True)

        stock_daily['ma20'] = stock_daily['close'].rolling(window=20, min_periods=20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(window=40, min_periods=40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(window=80, min_periods=80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(window=160, min_periods=160).mean()

        latest = stock_daily.iloc[-1]
        prev = stock_daily.iloc[-2]

        # 条件1：多头排列
        if not (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and
                pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            continue
        if not (latest['close'] > latest['ma20'] > latest['ma40'] >
                latest['ma80'] > latest['ma160']):
            continue

        # 条件2：MA80和MA160向上
        if not (pd.notna(prev['ma80']) and pd.notna(prev['ma160'])):
            continue
        if latest['ma80'] <= prev['ma80']:
            continue
        if latest['ma160'] <= prev['ma160']:
            continue

        # 条件3：近120交易日未跌破MA40
        recent_120 = stock_daily.tail(120)
        if (recent_120['close'] < recent_120['ma40']).any():
            continue

        # 通过条件1-3，至少是宽松版候选
        count_loose += 1

        # 条件4：近20交易日未跌破MA20
        recent_20 = stock_daily.tail(20)
        passed_strict = not (recent_20['close'] < recent_20['ma20']).any()

        # 计算当月涨幅
        month_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(month_start)]
        if len(month_df) >= 2:
            month_return = (latest['close'] / month_df.iloc[0]['close'] - 1) * 100
        else:
            month_return = None

        exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'
        name = stock_df[stock_df['code'] == code]['name'].values
        name = name[0] if len(name) > 0 else code

        stock_info = {
            '股票代码': code,
            '交易所': exchange,
            '股票简称': name,
            '当前价': round(latest['close'], 2),
            'MA20': round(latest['ma20'], 2),
            'MA40': round(latest['ma40'], 2),
            'MA80': round(latest['ma80'], 2),
            'MA160': round(latest['ma160'], 2),
            '当月涨幅': round(month_return, 2) if month_return is not None else None
        }

        if passed_strict:
            count_strict += 1
            results_strict.append(stock_info.copy())
        
        results_loose.append(stock_info.copy())

        if count_loose % 50 == 0:
            print(f"  已扫描 {idx+1}/{len(codes)}, 宽松版候选{count_loose}只, 严格版{count_strict}只")

    print(f"\n筛选完成！严格版: {len(results_strict)} 只, 宽松版: {len(results_loose)} 只")

    # 降级逻辑：严格版<10只时用宽松版
    if len(results_strict) >= 10:
        results = results_strict
        mode = "strict"
        print(f"\n✅ 严格版结果 {len(results)} 只 >= 10，使用严格版")
    else:
        results = results_loose
        mode = "loose"
        print(f"\n⚠️ 严格版结果 {len(results_strict)} 只 < 10，降级使用宽松版（去掉'近1月未破MA20'条件）")

    if not results:
        results_df = pd.DataFrame()
    else:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('当月涨幅', ascending=False)

    top20 = results_df.head(20) if len(results_df) > 0 else results_df

    if len(results_df) > 0:
        print("\n📊 当月涨幅Top20：")
        for i, row in top20.iterrows():
            mtd = f"{row['当月涨幅']:.2f}%" if pd.notna(row['当月涨幅']) else "N/A"
            print(f"  {row['交易所']}.{row['股票代码']} {row['股票简称']} 价:{row['当前价']} 月涨:{mtd}")

    # 生成报告
    if mode == "strict":
        condition_text = """**筛选条件**（严格版）:
1. 股价 > MA20 > MA40 > MA80 > MA160（完整多头排列）
2. MA80 和 MA160 必须向上（斜率>0）
3. 近半年（120交易日）未跌破 MA40
4. 近1个月（20交易日）未跌破 MA20"""
    else:
        condition_text = """**筛选条件**（宽松版 ⚠️）:
1. 股价 > MA20 > MA40 > MA80 > MA160（完整多头排列）
2. MA80 和 MA160 必须向上（斜率>0）
3. 近半年（120交易日）未跌破 MA40
~~4. 近1个月（20交易日）未跌破 MA20~~ ← 已取消（因严格版结果<10只，自动降级）"""

    md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
    md_content += f"**筛选基准日**: {TODAY}\n\n"
    md_content += condition_text + "\n\n"
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

    md_content += "\n*数据源: tushare*\n*案例分析学习材料，不构成投资建议*\n"

    output_dir = "./长期计划/缠论主升浪/outputs/股票筛选"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"\n📁 已保存: {output_file}")
    print("✅ 完成！")


if __name__ == '__main__':
    main()
