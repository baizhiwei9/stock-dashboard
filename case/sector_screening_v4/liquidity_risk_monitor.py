#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场流动性与风险偏好监控系统
================================
功能：
1. 计算每日流动性综合评分（0-60分）
2. 计算每日风险偏好综合评分（0-40分）
3. 生成近7天趋势柱状图
4. 输出HTML报告

指标体系：
- 流动性指标（60%权重）
  - M2同比增速
  - 隔夜利率DR001
  - 沪深成交额
  - 市场换手率

- 风险偏好指标（40%权重）
  - 涨停家数
  - 跌停家数（反向）
  - 北向资金净流入
  - 融资余额变化
"""

import sys
import tushare as ts
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
import json

# 清除代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ.pop(k)

# Tushare Token

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
from hub_root import tushare_token as _tushare_token  # noqa: E402
TUSHARE_TOKEN = _tushare_token()
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def get_m2_growth():
    """获取M2同比增速（%）"""
    try:
        # 获取最新宏观数据
        df = pro.cn_m(symbol='M2')
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['m2_yoy'])
    except Exception as e:
        print(f"获取M2数据失败: {e}")
    return 10.5  # 默认值

def get_dr001():
    """获取DR001隔夜利率（%）"""
    try:
        # 获取shibor数据
        df = pro.shibor(start_date=(datetime.now()-timedelta(days=7)).strftime('%Y%m%d'))
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]['dr001'])
    except Exception as e:
        print(f"获取DR001数据失败: {e}")
    return 1.8  # 默认值

def get_market_amount(date):
    """获取沪深市场总成交额（万亿）"""
    try:
        # 获取上证指数数据
        df_sh = pro.daily(ts_code='000001.SH', trade_date=date)
        # 获取深圳成指数据
        df_sz = pro.daily(ts_code='399001.SZ', trade_date=date)
        
        total = 0
        if df_sh is not None and len(df_sh) > 0:
            total += df_sh.iloc[0]['amount']
        if df_sz is not None and len(df_sz) > 0:
            total += df_sz.iloc[0]['amount']
            
        return total / 1e8  # 转换为亿元
    except Exception as e:
        print(f"获取成交额数据失败: {e}")
    return 12000  # 默认值（1.2万亿）

def get_turnover(date):
    """获取市场换手率（%）"""
    try:
        # 获取A股整体数据
        df = pro.index_dailybasic(ts_code='000001.SH', trade_date=date, fields='turnover_rate')
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['turnover_rate'])
    except Exception as e:
        print(f"获取换手率数据失败: {e}")
    return 2.5  # 默认值

def get_limit_up_down(date):
    """获取涨跌停家数"""
    try:
        df = pro.limit_list(trade_date=date, limit_type='U')
        limit_up = len(df) if df is not None else 0
        
        df = pro.limit_list(trade_date=date, limit_type='D')
        limit_down = len(df) if df is not None else 0
        
        return limit_up, limit_down
    except Exception as e:
        print(f"获取涨跌停数据失败: {e}")
    return 50, 10  # 默认值

def get_northbound(date):
    """获取北向资金净流入（亿元）"""
    try:
        df = pro.moneyflow_hsgt(trade_date=date)
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['north_money'])
    except Exception as e:
        print(f"获取北向资金数据失败: {e}")
    return 20  # 默认值

def get_margin_balance(date):
    """获取融资余额（亿元）"""
    try:
        df = pro.margin(trade_date=date)
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['rzye']) / 1e8  # 转换为亿元
    except Exception as e:
        print(f"获取融资余额数据失败: {e}")
    return 15000  # 默认值

def calculate_liquidity_score(m2, dr001, amount, turnover):
    """计算流动性综合评分（0-60分）"""
    score = 0
    
    # M2同比增速（15分）
    if m2 >= 10:
        score += 15
    elif m2 >= 8:
        score += 10
    else:
        score += 5
    
    # DR001隔夜利率（15分）
    if dr001 <= 1.5:
        score += 15
    elif dr001 <= 2.0:
        score += 10
    else:
        score += 5
    
    # 市场成交额（15分）
    if amount >= 15000:  # 1.5万亿
        score += 15
    elif amount >= 10000:  # 1万亿
        score += 10
    else:
        score += 5
    
    # 市场换手率（15分）
    if turnover >= 3.0:
        score += 15
    elif turnover >= 2.0:
        score += 10
    else:
        score += 5
    
    return round(score, 2)

def calculate_risk_preference_score(limit_up, limit_down, northbound, margin_change):
    """计算风险偏好综合评分（0-40分）"""
    score = 0
    
    # 涨停家数（10分）
    if limit_up >= 100:
        score += 10
    elif limit_up >= 50:
        score += 7
    else:
        score += 3
    
    # 跌停家数（反向10分）
    if limit_down <= 10:
        score += 10
    elif limit_down <= 30:
        score += 7
    else:
        score += 3
    
    # 北向资金（10分）
    if northbound >= 100:
        score += 10
    elif northbound >= 0:
        score += 7
    else:
        score += 3
    
    # 融资余额变化（10分）
    if margin_change > 0:
        score += 10
    elif margin_change == 0:
        score += 7
    else:
        score += 3
    
    return round(score, 2)

def generate_html_report(data):
    """生成HTML报告"""
    dates = [d['date'] for d in data]
    liquidity_scores = [d['liquidity_score'] for d in data]
    risk_scores = [d['risk_score'] for d in data]
    
    html_content = f"""<html>
<head>
    <meta charset="UTF-8">
    <title>市场流动性与风险偏好监控报告</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            text-align: center;
            margin-bottom: 30px;
        }}
        .chart-container {{
            height: 500px;
            margin: 20px 0;
        }}
        .summary {{
            margin-top: 30px;
            padding: 20px;
            background-color: #f8f9fa;
            border-radius: 8px;
        }}
        .day-data {{
            margin: 10px 0;
            padding: 10px;
            border-left: 4px solid #007bff;
            background-color: #f8f9fa;
        }}
        .disclaimer {{
            margin-top: 30px;
            text-align: center;
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>市场流动性与风险偏好监控报告</h1>
        <h3 style="text-align: center;">近7天趋势</h3>
        
        <div class="chart-container" id="mainChart"></div>
        
        <div class="summary">
            <h3>指标说明</h3>
            <p><strong>流动性评分（0-60分）</strong>：由M2增速、隔夜利率、市场成交额、换手率加权计算，分数越高表示流动性越充裕</p>
            <p><strong>风险偏好评分（0-40分）</strong>：由涨跌停家数、北向资金、融资余额变化加权计算，分数越高表示市场风险偏好越强</p>
        </div>
        
        <h3>每日详细数据</h3>
        {''.join([f"""
        <div class="day-data">
            <h4>{d['date']} ({d['weekday']})</h4>
            <p><strong>流动性评分:</strong> {d['liquidity_score']}/60分 - {d['liquidity_desc']}</p>
            <p><strong>风险偏好评分:</strong> {d['risk_score']}/40分 - {d['risk_desc']}</p>
            <p><strong>关键指标:</strong> M2增速 {d['m2']}% | DR001 {d['dr001']}% | 成交额 {d['amount']}亿元 | 北向资金 {d['northbound']}亿元</p>
        </div>
        """ for d in data])}
        
        <div class="disclaimer">
            <p>⚠️ 本报告仅供参考，不构成任何投资建议</p>
            <p>数据来源：Tushare API | 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
    
    <script type="text/javascript">
        var chartDom = document.getElementById('mainChart');
        var myChart = echarts.init(chartDom);
        var option;
        
        option = {
            title: {
                text: '流动性与风险偏好趋势'
            },
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'shadow'
                }
            },
            legend: {
                data: ['流动性评分', '风险偏好评分']
            },
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: {
                type: 'category',
                data: {json.dumps(dates)}
            },
            yAxis: {
                type: 'value',
                max: 60,
                axisLabel: {
                    formatter: '{value}分'
                }
            },
            series: [
                {
                    name: '流动性评分',
                    type: 'bar',
                    data: {json.dumps(liquidity_scores)},
                    itemStyle: {
                        color: '#007bff'
                    },
                    label: {
                        show: true,
                        position: 'top',
                        formatter: '{value}分'
                    }
                },
                {
                    name: '风险偏好评分',
                    type: 'bar',
                    data: {json.dumps(risk_scores)},
                    itemStyle: {
                        color: '#28a745'
                    },
                    label: {
                        show: true,
                        position: 'top',
                        formatter: '{value}分'
                    }
                }
            ]
        };
        
        option && myChart.setOption(option);
    </script>
</body>
</html>""".format(json.dumps(dates), json.dumps(liquidity_scores), json.dumps(risk_scores)))
    
    # 确保输出目录存在
    os.makedirs('市场情绪监控/outputs', exist_ok=True)
    
    # 保存HTML文件
    output_file = f"市场情绪监控/outputs/{datetime.now().strftime('%Y-%m-%d')}_流动性风险偏好.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return output_file

def get_weekday(date_str):
    """获取星期几"""
    weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    date = datetime.strptime(date_str, '%Y%m%d')
    return weekdays[date.weekday()]

def main():
    """主函数"""
    print("=" * 60)
    print("市场流动性与风险偏好监控系统")
    print("=" * 60)
    
    # 获取近7个交易日数据
    data = []
    today = datetime.now()
    
    # 获取前7个交易日
    trade_days = pro.trade_cal(start_date=(today - timedelta(days=14)).strftime('%Y%m%d'), 
                              end_date=today.strftime('%Y%m%d'))
    trade_days = trade_days[trade_days['is_open'] == 1]['cal_date'].tolist()
    trade_days = trade_days[-7:]  # 取最近7个交易日
    
    if not trade_days:
        print("无法获取交易日数据")
        return
    
    # 上一日融资余额
    prev_margin = None
    
    for date in trade_days:
        print(f"\n正在处理日期: {date}")
        
        # 获取各项指标
        m2 = get_m2_growth()
        dr001 = get_dr001()
        amount = get_market_amount(date)
        turnover = get_turnover(date)
        limit_up, limit_down = get_limit_up_down(date)
        northbound = get_northbound(date)
        margin = get_margin_balance(date)
        
        # 计算融资余额变化
        margin_change = 0
        if prev_margin:
            margin_change = margin - prev_margin
        prev_margin = margin
        
        # 计算评分
        liquidity_score = calculate_liquidity_score(m2, dr001, amount, turnover)
        risk_score = calculate_risk_preference_score(limit_up, limit_down, northbound, margin_change)
        
        # 生成描述
        liquidity_desc = "充裕" if liquidity_score >= 45 else "一般" if liquidity_score >= 30 else "紧张"
        risk_desc = "高" if risk_score >= 30 else "中" if risk_score >= 20 else "低"
        
        data.append({
            'date': datetime.strptime(date, '%Y%m%d').strftime('%Y-%m-%d'),
            'weekday': get_weekday(date),
            'm2': m2,
            'dr001': dr001,
            'amount': round(amount, 2),
            'turnover': turnover,
            'limit_up': limit_up,
            'limit_down': limit_down,
            'northbound': round(northbound, 2),
            'margin': round(margin, 2),
            'liquidity_score': liquidity_score,
            'risk_score': risk_score,
            'liquidity_desc': liquidity_desc,
            'risk_desc': risk_desc
        })
        
        print(f"流动性评分: {liquidity_score}/60分 ({liquidity_desc})")
        print(f"风险偏好评分: {risk_score}/40分 ({risk_desc})")
        
        # 控制请求频率
        time.sleep(0.5)
    
    # 生成HTML报告
    if data:
        print("\n" + "=" * 60)
        print("生成HTML报告...")
        output_file = generate_html_report(data)
        print(f"报告已生成: {output_file}")
        print("=" * 60)
        
        # 计算平均值
        avg_liquidity = round(np.mean([d['liquidity_score'] for d in data]), 2)
        avg_risk = round(np.mean([d['risk_score'] for d in data]), 2)
        
        print("\n近7天总结:")
        print(f"平均流动性评分: {avg_liquidity}/60分")
        print(f"平均风险偏好评分: {avg_risk}/40分")
        print(f"流动性趋势: {'↑' if data[-1]['liquidity_score'] > data[0]['liquidity_score'] else '↓'}")
        print(f"风险偏好趋势: {'↑' if data[-1]['risk_score'] > data[0]['risk_score'] else '↓'}")

if __name__ == '__main__':
    main()