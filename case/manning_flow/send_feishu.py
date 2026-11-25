#!/usr/bin/env python3
"""生成HTML回测报告 + 发送飞书交互卡片"""

import pandas as pd
import json
import base64
import os
import requests
from datetime import datetime

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# 加载数据
with open(os.path.join(OUTPUT_DIR, "backtest_metrics.json"), 'r', encoding='utf-8') as f:
    metrics = json.load(f)

nav_df = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_nav.csv"), index_col=0, parse_dates=True)
holdings_df = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_holdings.csv"))
weekly_ret = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_weekly_returns.csv"), index_col=0, parse_dates=True)

# 读取图表base64
chart_path = os.path.join(OUTPUT_DIR, "backtest_chart.png")
with open(chart_path, 'rb') as f:
    chart_b64 = base64.b64encode(f.read()).decode()

# ============================================================
# 1. 生成HTML报告
# ============================================================
def generate_html():
    # 构建持仓记录表格
    holdings_html = ""
    for _, row in holdings_df.iterrows():
        holdings_html += f"""
        <tr>
            <td>{row['date']}</td>
            <td>{row['industry']}</td>
            <td>{row['etf_code']}</td>
            <td>{row['etf_name']}</td>
            <td>{row['factor_value']:.4f}</td>
        </tr>"""
    
    # 构建指标对比表
    metrics_html = ""
    metric_keys = ['total_return', 'annualized_return', 'annualized_vol', 'sharpe', 'max_drawdown', 'win_rate', 'calmar']
    metric_labels = ['累计收益', '年化收益', '年化波动', '夏普比率', '最大回撤', '胜率', 'Calmar']
    for key, label in zip(metric_keys, metric_labels):
        row = f"<tr><td class='label'>{label}</td>"
        for m in metrics:
            val = m.get(key, 'N/A')
            cls = 'neg' if '-' in str(val) else 'pos'
            row += f"<td class='{cls}'>{val}</td>"
        row += "</tr>"
        metrics_html += row
    
    # 周度收益表
    weekly_html = ""
    for date, row in weekly_ret.iterrows():
        weekly_html += f"""
        <tr>
            <td>{date.strftime('%Y-%m-%d')}</td>"""
        for col in weekly_ret.columns:
            val = row[col]
            if pd.notna(val):
                cls = 'neg' if val < 0 else 'pos'
                weekly_html += f"<td class='{cls}'>{val*100:.2f}%</td>"
            else:
                weekly_html += "<td>-</td>"
        weekly_html += "</tr>"
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>行业资金流向图轮动策略回测报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif; background: #f5f5f5; color: #333; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1a237e 0%, #283593 100%); color: white; padding: 30px; border-radius: 12px; margin-bottom: 20px; text-align: center; }}
.header h1 {{ font-size: 24px; margin-bottom: 10px; }}
.header .subtitle {{ font-size: 14px; opacity: 0.8; }}
.header .source {{ font-size: 12px; opacity: 0.6; margin-top: 8px; }}
.section {{ background: white; border-radius: 12px; padding: 25px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
.section h2 {{ font-size: 18px; color: #1a237e; border-left: 4px solid #1a237e; padding-left: 12px; margin-bottom: 15px; }}
.chart-container {{ text-align: center; margin: 15px 0; }}
.chart-container img {{ max-width: 100%; border-radius: 8px; }}
table {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 14px; }}
th {{ background: #1a237e; color: white; padding: 10px; text-align: center; font-weight: 500; }}
td {{ padding: 8px 10px; text-align: center; border-bottom: 1px solid #eee; }}
td.label {{ font-weight: bold; background: #f5f5f5; text-align: left; }}
td.pos {{ color: #c62828; }}
td.neg {{ color: #2e7d32; }}
tr:hover {{ background: #f9f9f9; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 15px 0; }}
.card {{ background: #f8f9fa; border-radius: 8px; padding: 15px; text-align: center; border: 1px solid #e0e0e0; }}
.card .title {{ font-size: 12px; color: #666; margin-bottom: 5px; }}
.card .value {{ font-size: 22px; font-weight: bold; }}
.card .value.pos {{ color: #c62828; }}
.card .value.neg {{ color: #2e7d32; }}
.note {{ background: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; border-radius: 8px; margin: 15px 0; font-size: 13px; }}
.note strong {{ color: #e65100; }}
.disclaimer {{ font-size: 12px; color: #999; text-align: center; margin-top: 20px; padding: 15px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>华泰金工《行业资金流向图应用于行业轮动》策略回测报告</h1>
        <div class="subtitle">回测区间: 2026-01-01 ~ 2026-08-14 | 29个行业ETF | 周度调仓Top5等权</div>
        <div class="source">报告来自于 DoMate | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>
    
    <div class="section">
        <h2>核心结论</h2>
        <div class="summary">
            <div class="card">
                <div class="title">综合因子(ETF实际)累计收益</div>
                <div class="value neg">-24.89%</div>
            </div>
            <div class="card">
                <div class="title">等权基准累计收益</div>
                <div class="value neg">-6.57%</div>
            </div>
            <div class="card">
                <div class="title">超额收益(vs基准)</div>
                <div class="value neg">-18.32%</div>
            </div>
            <div class="card">
                <div class="title">最大回撤</div>
                <div class="value neg">-32.18%</div>
            </div>
        </div>
        <div class="note">
            <strong>关键发现:</strong> 本回测使用CMF(Chaikin Money Flow)作为主动净买入因子的代理指标, 
            替代研报中Wind的主动买卖金额数据。由于数据源差异和代理指标的局限性, 
            2026年YTD策略跑输基准。研报原文使用2016-2024年Wind全量主动买卖数据, 
            年化收益21.97%且跑赢基准12.66pct。代理因子与原始因子存在本质差异, 
            研报中的曼宁公式行业资金流向图也无法在缺乏tick级数据的情况下完整复现。
        </div>
    </div>
    
    <div class="section">
        <h2>净值曲线与回测图表</h2>
        <div class="chart-container">
            <img src="data:image/png;base64,{chart_b64}" alt="回测图表">
        </div>
    </div>
    
    <div class="section">
        <h2>回测指标对比</h2>
        <table>
            <thead>
                <tr>
                    <th>指标</th>
                    <th>{metrics[0]['name']}</th>
                    <th>{metrics[1]['name']}</th>
                    <th>{metrics[2]['name']}</th>
                    <th>{metrics[3]['name']}</th>
                </tr>
            </thead>
            <tbody>
                {metrics_html}
            </tbody>
        </table>
    </div>
    
    <div class="section">
        <h2>持仓记录</h2>
        <table>
            <thead>
                <tr>
                    <th>调仓日期</th>
                    <th>行业</th>
                    <th>ETF代码</th>
                    <th>ETF名称</th>
                    <th>因子值</th>
                </tr>
            </thead>
            <tbody>
                {holdings_html}
            </tbody>
        </table>
    </div>
    
    <div class="section">
        <h2>周度收益明细</h2>
        <table>
            <thead>
                <tr>
                    <th>日期</th>
                    <th>综合因子(行业指数)</th>
                    <th>综合因子(ETF实际)</th>
                    <th>等权基准</th>
                </tr>
            </thead>
            <tbody>
                {weekly_html}
            </tbody>
        </table>
    </div>
    
    <div class="disclaimer">
        本报告由DuMate自动生成, 仅供研究参考, 不构成投资建议。<br>
        策略基于华泰证券金融工程研报《行业资金流向图应用于行业轮动》, 因数据源限制使用代理指标实现。
    </div>
</div>
</body>
</html>"""
    
    html_path = os.path.join(OUTPUT_DIR, "backtest_report.html")
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML报告已保存: {html_path}")
    return html_path


# ============================================================
# 2. 发送飞书交互卡片
# ============================================================
def send_feishu_card():
    """发送飞书交互卡片"""
    
    # 构建指标表格元素 (column_set + column)
    def make_metric_table():
        elements = []
        
        # 表头
        header_labels = ["指标", "综合(行业)", "综合(ETF)", "单因子", "基准"]
        
        # 数据行
        metric_rows = [
            ("累计收益", [m.get('total_return','') for m in metrics]),
            ("年化收益", [m.get('annualized_return','') for m in metrics]),
            ("最大回撤", [m.get('max_drawdown','') for m in metrics]),
            ("夏普比率", [m.get('sharpe','') for m in metrics]),
            ("胜率", [m.get('win_rate','') for m in metrics]),
        ]
        
        # 表头行
        col_set = {
            "tag": "column_set",
            "flex_mode": "none",
            "background_style": "grey",
            "columns": [
                {"tag": "column", "width": "weighted", "weight": 1, 
                 "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{label}**"}}], 
                 "vertical_align": "center"}
                for label in header_labels
            ]
        }
        elements.append(col_set)
        
        # 数据行
        for label, vals in metric_rows:
            col_set = {
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "default",
                "columns": [
                    {"tag": "column", "width": "weighted", "weight": 1, 
                     "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}], 
                     "vertical_align": "center"}
                    for content in [label] + vals
                ]
            }
            elements.append(col_set)
        
        return elements
    
    # 构建持仓摘要
    latest_holding = holdings_df[holdings_df['date'] == holdings_df['date'].max()]
    latest_holdings_str = "\n".join([
        f"  {row['industry']}({row['etf_name']})" 
        for _, row in latest_holding.iterrows()
    ])
    
    # 行业被选次数Top5
    top_industries = holdings_df['industry'].value_counts().head(5)
    top_industries_str = "\n".join([
        f"  {ind}: {cnt}次" 
        for ind, cnt in top_industries.items()
    ])
    
    card = {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "报告来自于 DoMate"
            },
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**华泰金工《行业资金流向图应用于行业轮动》策略回测报告**\n"
                               f"回测区间: 2026-01-01 ~ 2026-08-14 | 29个行业ETF | 周度调仓Top5等权\n"
                               f"报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**核心结果**"
                }
            },
            {
                "tag": "column_set",
                "flex_mode": "bypass",
                "background_style": "grey",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": f"综合因子(ETF)\n**-24.89%**"}
                        }],
                        "vertical_align": "center"
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": f"等权基准\n**-6.57%**"}
                        }],
                        "vertical_align": "center"
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": f"超额收益\n**-18.32%**"}
                        }],
                        "vertical_align": "center"
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": f"最大回撤\n**-32.18%**"}
                        }],
                        "vertical_align": "center"
                    }
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**回测指标对比**"
                }
            },
            *make_metric_table(),
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**最新一期持仓 (2026-08-07)**"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": latest_holdings_str
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**被选次数Top5行业**"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": top_industries_str
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**重要说明**\n"
                               "1. 主动净买入因子使用CMF(Chaikin Money Flow)作为代理, 替代Wind主动买卖金额数据\n"
                               "2. 行业资金流向图(曼宁公式)因缺乏tick级数据无法完整复现, 使用4个微观结构指标代理\n"
                               "3. 研报原文(2016-2024年Wind数据)年化21.97%, 跑赢基准12.66pct\n"
                               "4. 代理因子与原始因子存在本质差异, 2026年策略跑输基准\n"
                               "5. 完整HTML报告(含净值曲线图)已生成为附件"
                }
            },
            {
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": "本报告由DuMate自动生成, 仅供研究参考, 不构成投资建议"
                }]
            }
        ]
    }
    
    payload = {
        "msg_type": "interactive",
        "card": card
    }
    
    print("\n发送飞书交互卡片...")
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            print("飞书卡片发送成功!")
        else:
            print(f"飞书返回: {result}")
    except Exception as e:
        print(f"发送失败: {e}")


# ============================================================
# 执行
# ============================================================
html_path = generate_html()
send_feishu_card()

print("\n完成!")
