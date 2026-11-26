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
import sys, os
sys.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话/长期计划/A股选股器/src/选股器'))
os.chdir(_cp('/Coze/Drive/蓁宝/所有对话/主对话/长期计划/A股选股器/src/选股器'))
__file__ = _cp('/Coze/Drive/蓁宝/所有对话/主对话/长期计划/A股选股器/src/选股器/run_screener_20260605.py')
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多时段强势股筛选器 - 20260603版
数据源：Tushare批量接口
案例分析学习材料，不构成投资建议
"""

import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime
import time
import warnings
import os

warnings.filterwarnings('ignore')

pro = ts.pro_api()

def main():
    start_time = time.time()
    
    print("=" * 60)
    print("A股多时段强势股筛选器 (Tushare批量极速版)")
    print("案例分析学习材料，不构成投资建议")
    print("=" * 60)
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"\n执行日期: {today_str}")
    
    # ====== 1. 获取交易日历，定位关键日期 ======
    print("\n[1/6] 获取交易日历...")
    today_ymd = datetime.now().strftime('%Y%m%d')
    df_cal = pro.trade_cal(exchange='SSE', is_open='1', start_date='20250401', end_date=today_ymd)
    dates = df_cal['cal_date'].tolist()
    dates.sort()
    
    today = dates[-2] if len(dates) >= 2 else dates[-1]
    month_ago = dates[-22] if len(dates) >= 22 else dates[0]
    half_ago = dates[-127] if len(dates) >= 127 else dates[0]
    year_ago = dates[-253] if len(dates) >= 253 else dates[0]
    
    year_start_dates = [d for d in dates if d >= '20260101']
    year_start = year_start_dates[0] if year_start_dates else year_ago
    
    print(f"  今日: {today}")
    print(f"  1月前: {month_ago}")
    print(f"  半年前: {half_ago}")
    print(f"  1年前: {year_ago}")
    print(f"  年初: {year_start}")
    
    # ====== 2. 获取股票列表 + 排除条件 ======
    print("\n[2/6] 获取A股列表...")
    df_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,list_date')
    
    # 只保留A股（沪深主板+创业板+科创板）
    df_basic = df_basic[df_basic['ts_code'].str.match(r'^6\d{5}\.SH$|^0\d{5}\.SZ$|^3\d{5}\.SZ$')]
    print(f"  A股总数: {len(df_basic)}")
    
    # 排除ST/退市
    df_basic = df_basic[~df_basic['name'].str.contains('ST|退', na=False)]
    print(f"  排除ST后: {len(df_basic)}")
    
    # 排除上市不满1年
    one_year_ago = (datetime.now() - pd.Timedelta(days=365)).strftime('%Y%m%d')
    df_basic = df_basic[df_basic['list_date'] <= one_year_ago]
    print(f"  排除次新后: {len(df_basic)}")
    
    # ====== 3. 获取市值数据 + 排除小市值 ======
    print("\n[3/6] 获取市值数据...")
    df_mv = pd.DataFrame()
    for days_back in range(5):
        test_date = dates[-(1 + days_back)] if len(dates) > days_back else today
        try:
            df_mv = pro.daily_basic(trade_date=test_date, fields='ts_code,total_mv')
            if len(df_mv) > 1000:
                print(f"  市值数据日期: {test_date}, {len(df_mv)}只")
                break
        except:
            time.sleep(0.5)
    
    if not df_mv.empty:
        df_mv['mktcap_yi'] = df_mv['total_mv'] / 10000  # 万元→亿元
        big_cap = df_mv[df_mv['mktcap_yi'] >= 50]['ts_code'].tolist()
        before = len(df_basic)
        df_basic = df_basic[df_basic['ts_code'].isin(big_cap)]
        print(f"  排除市值<50亿后: {len(df_basic)} (排除{before - len(df_basic)}只)")
    
    valid_codes = set(df_basic['ts_code'].tolist())
    print(f"  最终待筛选: {len(valid_codes)}只")
    
    # ====== 4. 批量获取关键日期行情 ======
    print("\n[4/6] 批量获取关键日期行情...")
    
    key_dates = {
        'today': today,
        'month_ago': month_ago,
        'half_ago': half_ago,
        'year_ago': year_ago,
        'year_start': year_start
    }
    
    prices = {}
    for label, date in key_dates.items():
        try:
            df_day = pro.daily(trade_date=date, fields='ts_code,close')
            df_day = df_day[df_day['ts_code'].isin(valid_codes)]
            prices[label] = dict(zip(df_day['ts_code'], df_day['close']))
            print(f"  {label} ({date}): {len(df_day)}只")
        except Exception as e:
            print(f"  {label} ({date}): 获取失败 {e}")
            prices[label] = {}
        time.sleep(0.3)
    
    # ====== 5. 计算涨幅 + 四重筛选 ======
    print("\n[5/6] 计算涨幅并筛选...")
    
    results = []
    for _, row in df_basic.iterrows():
        ts_code = row['ts_code']
        name = row['name']
        symbol = row['symbol']
        
        p_today = prices['today'].get(ts_code)
        p_month = prices['month_ago'].get(ts_code)
        p_half = prices['half_ago'].get(ts_code)
        p_year = prices['year_ago'].get(ts_code)
        p_ystart = prices['year_start'].get(ts_code)
        
        if p_today is None or p_today <= 0:
            continue
        
        year_ret = (p_today / p_year - 1) * 100 if p_year and p_year > 0 else None
        half_ret = (p_today / p_half - 1) * 100 if p_half and p_half > 0 else None
        month_ret = (p_today / p_month - 1) * 100 if p_month and p_month > 0 else None
        ytd_ret = (p_today / p_ystart - 1) * 100 if p_ystart and p_ystart > 0 else None
        
        if any(v is None for v in [year_ret, half_ret, month_ret, ytd_ret]):
            continue
        
        results.append({
            'code': symbol,
            'name': name,
            'price': round(p_today, 2),
            'year_ret': round(year_ret, 2),
            'half_ret': round(half_ret, 2),
            'month_ret': round(month_ret, 2),
            'ytd_ret': round(ytd_ret, 2)
        })
    
    results_df = pd.DataFrame(results)
    print(f"  有效数据: {len(results_df)}只")
    
    if results_df.empty:
        print("无有效数据，退出")
        return
    
    # 四重筛选
    p80_year = results_df['year_ret'].quantile(0.80)
    p80_half = results_df['half_ret'].quantile(0.80)
    p85_month = results_df['month_ret'].quantile(0.85)
    top100_ytd = results_df.nlargest(100, 'ytd_ret')
    top100_min = top100_ytd['ytd_ret'].min()
    
    print(f"  近1年涨幅前20%阈值: {p80_year:.2f}%")
    print(f"  近半年涨幅前20%阈值: {p80_half:.2f}%")
    print(f"  近1月涨幅前15%阈值: {p85_month:.2f}%")
    print(f"  当年涨幅前100阈值: {top100_min:.2f}%")
    
    filtered = results_df[
        (results_df['year_ret'] >= p80_year) &
        (results_df['half_ret'] >= p80_half) &
        (results_df['month_ret'] >= p85_month) &
        (results_df['ytd_ret'] >= top100_min)
    ].copy()
    
    filtered = filtered.sort_values('ytd_ret', ascending=False)
    print(f"  四重筛选后: {len(filtered)}只")
    
    # ====== 6. 输出 ======
    print("\n[6/6] 保存结果...")
    
    output_df = filtered[['code', 'name', 'price', 'year_ret', 'half_ret', 'month_ret', 'ytd_ret']].copy()
    output_df.columns = ['股票代码', '名称', '最新价', '近1年涨幅_%', '近半年涨幅_%', '近1月涨幅_%', '当年涨幅_%']
    
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'outputs', '选股器')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'{today_str}_筛选结果.csv')
    
    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write('# 数据来源：Tushare；用途：案例分析学习材料，不构成任何投资建议；注意：股市有风险，投资需谨慎\n')
        f.write(output_df.to_csv(index=False))
        f.write(f'\n# 数据日期：{today}；样本量：{len(results_df)}；筛选阈值：1年>{p80_year:.2f}% | 半年>{p80_half:.2f}% | 1月>{p85_month:.2f}% | 当年TOP100>{top100_min:.2f}%\n')
    
    elapsed = time.time() - start_time
    print(f"\n结果已保存: {output_path}")
    print(f"总耗时: {elapsed:.1f}秒")
    print(f"\n{'=' * 60}")
    print("筛选结果TOP20:")
    print("=" * 60)
    print(output_df.head(20).to_string(index=False))
    
    return output_path, len(filtered)

if __name__ == '__main__':
    output_path, count = main()
    # 注意：独立推送已移除，统一由 unified_screener 推送交互式卡片
    print(f"\n✅ 筛选完成，共 {count} 只，结果已保存至: {output_path}")
    print("   推送由多策略综合选股器统一管理（交互式卡片）")

