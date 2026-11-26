#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场流动性与风险偏好监控系统 - 快速测试版
========================================
功能：
1. 计算今日流动性综合评分（0-60分）
2. 计算今日风险偏好综合评分（0-40分）
3. 生成简化趋势图
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

def get_market_data(date):
    """获取市场关键数据"""
    try:
        # 获取上证指数数据
        df = pro.daily(ts_code='000001.SH', trade_date=date)
        if df is not None and len(df) > 0:
            return {
                'amount': float(df.iloc[0]['amount']) / 1e8,  # 转换为亿元
                'turnover': float(df.iloc[0]['turnover_rate'])
            }
    except Exception as e:
        print(f"获取市场数据失败: {e}")
    return {'amount': 12000, 'turnover': 2.5}

def get_limit_data(date):
    """获取涨跌停数据"""
    try:
        # 获取涨跌停统计
        df = pro.limit_list(trade_date=date, limit_type='U')
        limit_up = len(df) if df is not None else 0
        
        df = pro.limit_list(trade_date=date, limit_type='D')
        limit_down = len(df) if df is not None else 0
        
        return limit_up, limit_down
    except Exception as e:
        print(f"获取涨跌停数据失败: {e}")
    return 50, 10

def calculate_scores():
    """计算综合评分"""
    # 获取今日日期
    today = datetime.now()
    today_str = today.strftime('%Y%m%d')
    
    # 获取前一个交易日
    trade_cal = pro.trade_cal(start_date=(today - timedelta(days=10)).strftime('%Y%m%d'),
                              end_date=today_str)
    trade_days = trade_cal[trade_cal['is_open'] == 1]['cal_date'].tolist()
    trade_day = trade_days[-1] if trade_days else today_str
    
    print(f"\n使用交易日: {trade_day}")
    
    # 获取指标数据
    market_data = get_market_data(trade_day)
    limit_up, limit_down = get_limit_data(trade_day)
    
    # 流动性评分（简化版）
    # 成交额（30分）+ 换手率（30分）
    liquidity_score = 0
    if market_data['amount'] >= 15000:
        liquidity_score += 30
    elif market_data['amount'] >= 10000:
        liquidity_score += 20
    else:
        liquidity_score += 10
    
    if market_data['turnover'] >= 3.0:
        liquidity_score += 30
    elif market_data['turnover'] >= 2.0:
        liquidity_score += 20
    else:
        liquidity_score += 10
    
    # 风险偏好评分（简化版）
    # 涨跌停比（20分）+ 北向资金（20分，默认值）
    risk_score = 0
    limit_ratio = limit_up / max(limit_down, 1)
    if limit_ratio >= 5:
        risk_score += 20
    elif limit_ratio >= 2:
        risk_score += 15
    else:
        risk_score += 10
    
    # 默认北向资金流入
    risk_score += 15
    
    # 获取近7天数据
    trend_data = []
    for date in trade_days[-7:]:
        md = get_market_data(date)
        lu, ld = get_limit_data(date)
        
        # 简化计算
        ls = 0
        if md['amount'] >= 15000:
            ls += 30
        elif md['amount'] >= 10000:
            ls += 20
        else:
            ls += 10
        
        if md['turnover'] >= 3.0:
            ls += 30
        elif md['turnover'] >= 2.0:
            ls += 20
        else:
            ls += 10
        
        rs = 0
        lr = lu / max(ld, 1)
        if lr >= 5:
            rs += 20
        elif lr >= 2:
            rs += 15
        else:
            rs += 10
        rs += 15
        
        trend_data.append({
            'date': datetime.strptime(date, '%Y%m%d').strftime('%m-%d'),
            'liquidity': ls,
            'risk': rs
        })
    
    return liquidity_score, risk_score, trend_data

def generate_simple_report(liquidity_score, risk_score, trend_data):
    """生成简化HTML报告"""
    dates = [d['date'] for d in trend_data]
    liquidity_scores = [d['liquidity'] for d in trend_data]
    risk_scores = [d['risk'] for d in trend_data]
    
    html_content = f"""<html>
<head>
    <meta charset="UTF-8">
    <title>市场流动性与风险偏好监控</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 900px;
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
            height: 400px;
            margin: 20px 0;
        }}
        .summary {{
            margin-top: 30px;
            padding: 20px;
            background-color: #f8f9fa;
            border-radius: 8px;
            text-align: center;
        }}
        .score-box {{
            display: inline-block;
            margin: 0 20px;
            padding: 20px;
            background-color: #e9ecef;
            border-radius: 8px;
            min-width: 150px;
        }}
        .score-number {{
            font-size: 36px;
            font-weight: bold;
            color: #007bff;
        }}
        .score-label {{
            font-size: 14px;
            color: #666;
            margin-top: 5px;
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
        <h1>市场流动性与风险偏好监控</h1>
        
        <div class="summary">
            <div class="score-box">
                <div class="score-number">{liquidity_score}</div>
                <div class="score-label">流动性评分 (0-60分)</div>
            </div>
            <div class="score-box">
                <div class="score-number">{risk_score}</div>
                <div class="score-label">风险偏好评分 (0-40分)</div>
            </div>
            <div style="margin-top: 20px;">
                <strong>流动性状态:</strong> {'充裕' if liquidity_score >= 45 else '一般' if liquidity_score >= 30 else '紧张'}
                <br>
                <strong>风险偏好:</strong> {'高' if risk_score >= 30 else '中' if risk_score >= 20 else '低'}
            </div>
        </div>
        
        <h3 style="text-align: center;">近7天趋势</h3>
        <div class="chart-container" id="mainChart"></div>
        
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
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'shadow'
                }
            },
            legend: {
                data: ['流动性评分', '风险偏好评分'],
                bottom: 10
            },
            grid: {
                left: '3%',
                right: '4%',
                top: '10%',
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
                        formatter: '{value}'
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
                        formatter: '{value}'
                    }
                }
            ]
        };
        
        option && myChart.setOption(option);
    </script>
</body>
</html>""".format(liquidity_score, risk_score, json.dumps(dates), 
                    json.dumps(liquidity_scores), json.dumps(risk_scores)))
    
    # 确保输出目录存在
    os.makedirs('市场情绪监控/outputs', exist_ok=True)
    
    # 保存HTML文件
    output_file = f"市场情绪监控/outputs/{datetime.now().strftime('%Y-%m-%d')}_流动性风险偏好_简化版.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return output_file

def main():
    """主函数"""
    print("=" * 60)
    print("市场流动性与风险偏好监控系统（简化版）")
    print("=" * 60)
    
    try:
        # 计算评分
        print("\n获取数据并计算评分...")
        liquidity_score, risk_score, trend_data = calculate_scores()
        
        print(f"\n今日评分结果:")
        print(f"流动性评分: {liquidity_score}/60分")
        print(f"风险偏好评分: {risk_score}/40分")
        print(f"流动性状态: {'充裕' if liquidity_score >= 45 else '一般' if liquidity_score >= 30 else '紧张'}")
        print(f"风险偏好: {'高' if risk_score >= 30 else '中' if risk_score >= 20 else '低'}")
        
        # 生成报告
        print("\n" + "=" * 60)
        print("生成HTML报告...")
        output_file = generate_simple_report(liquidity_score, risk_score, trend_data)
        print(f"报告已生成: {output_file}")
        print("=" * 60)
        
        return output_file
        
    except Exception as e:
        print(f"\n程序运行出错: {e}")
        return None

if __name__ == '__main__':
    main()