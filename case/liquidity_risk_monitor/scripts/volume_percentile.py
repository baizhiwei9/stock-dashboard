#!/usr/bin/env python3.13
"""
全A成交额年度分位数计算
数据源: tushare (上证+深证综指合并≈全A)
输出: JSON到 stdout，同时保存到 outputs/
"""

import os
import sys
import json
import time
import numpy as np
import tushare as ts
from datetime import datetime, timedelta

TUSHARE_TOKEN = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'outputs')


def calc_volume_percentile(trade_date, lookback=252):
    """
    全A成交额年度分位数
    方法: 上证指数(000001.SH) + 深证综指(399106.SZ) 合并 ≈ 全A
    备用: 逐日daily汇总(慢) → 市值前2000(无历史分位)
    """
    end_dt = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=int(lookback * 1.6))
    start_str = start_dt.strftime('%Y%m%d')

    # 1) 上证 + 深证综指 合并（高效，2次API调用）
    amounts = None
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
    except Exception as e:
        print(f"  [警告] 双指数合并失败: {e}", file=sys.stderr)

    # 2) 备用：逐日取全市场成交额（慢，限流风险）
    if amounts is None or len(amounts) < 60:
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
        except:
            pass

    # 3) 最终备用：市值前2000（无历史分位）
    if amounts is None or len(amounts) < 60:
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

    # 分位数标签: ≥80%🔴放量过热, ≤20%🔴缩量危险
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


def main():
    import re
    raw_arg = sys.argv[1] if len(sys.argv) >= 2 else None
    if raw_arg and re.match(r'^\d{8}$', raw_arg):
        trade_date = raw_arg
    else:
        trade_date = datetime.now().strftime('%Y%m%d')
        if raw_arg:
            print(f"⚠️  日期参数格式无效: {raw_arg}，使用当天日期")
    print(f"=== 全A成交额年度分位数 {trade_date} ===")

    result = calc_volume_percentile(trade_date)

    if result:
        # 保存JSON
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_file = os.path.join(OUTPUT_DIR, f'{trade_date}_volume_percentile.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"结果已保存: {output_file}")

        # stdout输出JSON（供执行session读取）
        print(json.dumps(result, ensure_ascii=False))

        # 飞书推送（交互式卡片）
        formatted_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        
        import sys as _sys
        _sys.path.insert(0, '/Coze/Drive/蓁宝/所有对话/主对话')
        from feishu_card_utils import build_volume_percentile_card
        from feishu_push import push_card_to_feishu
        
        card = build_volume_percentile_card(formatted_date, result)
        push_card_to_feishu(card_dict=card)
    else:
        print("⚠️ 成交额分位数获取失败", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
