#!/usr/bin/env python3
"""V3: 生成图表 + HTML报告 + 飞书卡片"""

import pandas as pd
import numpy as np
import json
import base64
import os
import requests
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties, fontManager
import subprocess

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
from feishu_config import WEBHOOK_URL  # noqa: E402  凭据统一走 app_secrets，见 DEPLOY.md「敏感配置」
# 字体
result = subprocess.run(['fc-list', ':lang=zh', 'file'], capture_output=True, text=True)
zh_font_path = None
for line in result.stdout.strip().split('\n'):
    path = line.split(':')[0].strip()
    if path and os.path.exists(path):
        zh_font_path = path
        break
font_prop = FontProperties(fname=zh_font_path)
fontManager.addfont(zh_font_path)
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = [font_prop.get_name(), 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 加载数据
nav_df = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_v3_nav.csv"), index_col=0, parse_dates=True)
weekly_df = pd.read_csv(os.path.join(OUTPUT_DIR, "backtest_v3_weekly_returns.csv"), index_col=0, parse_dates=True)
with open(os.path.join(OUTPUT_DIR, "backtest_v3_metrics.json"), 'r', encoding='utf-8') as f:
    metrics = json.load(f)
with open(os.path.join(OUTPUT_DIR, "backtest_v3_signals.json"), 'r', encoding='utf-8') as f:
    signals = json.load(f)

sig_df = pd.DataFrame(signals)
sig_df['date'] = pd.to_datetime(sig_df['date'])

# ============================================================
# 图表
# ============================================================
fig = plt.figure(figsize=(16, 24), facecolor='white')

# 图1: 净值曲线
ax1 = plt.subplot2grid((7, 2), (0, 0), colspan=2, rowspan=2)
colors = {
    '每日信号(ETF)': '#E74C3C', '每日信号(行业)': '#F1948A',
    '周度信号(ETF)': '#8E44AD', '周度信号(行业)': '#BB8FCE',
    '等权基准': '#2C3E50',
}
for col in nav_df.columns:
    c = colors.get(col, '#888')
    lw = 2.8 if '每日信号(ETF)' in col or '等权基准' in col else 1.2
    ls = '-' if 'ETF' in col or '等权' in col else '--'
    ax1.plot(nav_df.index, nav_df[col], label=col, color=c, linewidth=lw, linestyle=ls)

ax1.axhline(y=1.0, color='gray', linewidth=0.5, linestyle=':')
ax1.set_title('V3 行业资金流向信号策略 - 2026年YTD净值曲线 (正向跟钱走)', fontsize=15, fontproperties=font_prop, fontweight='bold')
ax1.set_ylabel('净值', fontsize=12, fontproperties=font_prop)
ax1.legend(prop=font_prop, fontsize=10, loc='upper right', ncol=2)
ax1.grid(True, alpha=0.3)
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
ax1.xaxis.set_major_locator(mdates.MonthLocator())

for col in ['每日信号(ETF)', '等权基准']:
    if col in nav_df.columns:
        final = nav_df[col].iloc[-1]
        ret = (final - 1) * 100
        ax1.annotate(f'{col}: {ret:+.1f}%', xy=(nav_df.index[-1], final),
                    xytext=(10, 0), textcoords='offset points', fontsize=10,
                    fontproperties=font_prop, color=colors[col], fontweight='bold')

# 图2: 回撤
ax2 = plt.subplot2grid((7, 2), (2, 0), colspan=2)
for col in ['每日信号(ETF)', '周度信号(ETF)', '等权基准']:
    if col in nav_df.columns:
        c = colors.get(col, '#888')
        peak = nav_df[col].expanding().max()
        dd = (nav_df[col] - peak) / peak * 100
        ax2.fill_between(dd.index, dd.values, 0, alpha=0.15, color=c)
        ax2.plot(dd.index, dd.values, label=col, color=c, linewidth=1.8)
ax2.set_title('回撤曲线', fontsize=13, fontproperties=font_prop)
ax2.set_ylabel('回撤 (%)', fontsize=11, fontproperties=font_prop)
ax2.legend(prop=font_prop, fontsize=9)
ax2.grid(True, alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))

# 图3: 信号时间线
ax3 = plt.subplot2grid((7, 2), (3, 0), colspan=2)
ax3.set_title('资金流信号时间线 (机会=红 风险=绿)', fontsize=13, fontproperties=font_prop)
opp_sigs = sig_df[sig_df['signal'].str.contains('机会')]
risk_sigs = sig_df[sig_df['signal'].str.contains('风险')]
industries = sig_df['industry'].unique()
ind_to_y = {ind: i for i, ind in enumerate(sorted(industries))}

for _, row in opp_sigs.iterrows():
    y = ind_to_y.get(row['industry'], 0)
    ax3.scatter(row['date'], y, c='#E74C3C', s=40, marker='^', alpha=0.7, zorder=3)
for _, row in risk_sigs.iterrows():
    y = ind_to_y.get(row['industry'], 0)
    ax3.scatter(row['date'], y, c='#27AE60', s=40, marker='v', alpha=0.7, zorder=3)

ax3.set_yticks(range(len(ind_to_y)))
ax3.set_yticklabels(list(ind_to_y.keys()), fontsize=7, fontproperties=font_prop)
ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
ax3.grid(True, alpha=0.2)
# 图例
ax3.scatter([], [], c='#E74C3C', s=60, marker='^', label='机会信号(建仓)')
ax3.scatter([], [], c='#27AE60', s=60, marker='v', label='风险信号(清仓)')
ax3.legend(prop=font_prop, fontsize=9, loc='upper right')

# 图4: 周度收益柱状图
ax4 = plt.subplot2grid((7, 2), (4, 0))
weekly_colors = ['#E74C3C' if r > 0 else '#27AE60' for r in weekly_df['etf_return']]
ax4.bar(range(len(weekly_df)), weekly_df['etf_return']*100, color=weekly_colors, edgecolor='white', alpha=0.8)
ax4.axhline(y=0, color='gray', linewidth=0.5)
ax4.set_title('周度收益 (ETF实际)', fontsize=13, fontproperties=font_prop)
ax4.set_ylabel('收益 (%)', fontsize=11, fontproperties=font_prop)
ax4.set_xticks(range(0, len(weekly_df), 4))
ax4.set_xticklabels([weekly_df.index[i].strftime('%m-%d') for i in range(0, len(weekly_df), 4)], 
                     fontsize=8, fontproperties=font_prop, rotation=30)

# 图5: 持仓数量变化
ax5 = plt.subplot2grid((7, 2), (4, 1))
ax5.fill_between(range(len(weekly_df)), weekly_df['n_holdings'], 0, alpha=0.3, color='#3498DB')
ax5.plot(range(len(weekly_df)), weekly_df['n_holdings'], color='#3498DB', linewidth=2)
ax5.set_title('持仓数量变化', fontsize=13, fontproperties=font_prop)
ax5.set_ylabel('持仓数', fontsize=11, fontproperties=font_prop)
ax5.set_xticks(range(0, len(weekly_df), 4))
ax5.set_xticklabels([weekly_df.index[i].strftime('%m-%d') for i in range(0, len(weekly_df), 4)], 
                     fontsize=8, fontproperties=font_prop, rotation=30)
ax5.set_ylim(0, 6)

# 图6: 信号准确率
ax6 = plt.subplot2grid((7, 2), (5, 0))
opp_correct = 43.8
risk_correct = 59.3
bars = ax6.bar(['机会信号\n准确率', '风险信号\n准确率'], [opp_correct, risk_correct], 
                color=['#E74C3C', '#27AE60'], edgecolor='white', width=0.5)
ax6.axhline(y=50, color='gray', linewidth=0.5, linestyle='--', label='50%基准')
ax6.set_title('信号准确率 (信号后5日方向正确比例)', fontsize=13, fontproperties=font_prop)
ax6.set_ylabel('准确率 (%)', fontsize=11, fontproperties=font_prop)
ax6.set_ylim(0, 100)
for bar, val in zip(bars, [opp_correct, risk_correct]):
    ax6.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, f'{val:.1f}%', 
             ha='center', fontsize=12, fontproperties=font_prop, fontweight='bold')
ax6.legend(prop=font_prop, fontsize=9)

# 图7: 指标表格
ax7 = plt.subplot2grid((7, 2), (5, 1))
ax7.axis('off')
etf_metrics = [m for m in metrics if 'ETF' in m['name'] or '基准' in m['name']]
metric_labels = ['累计收益', '年化收益', '夏普', '最大回撤', '周胜率', '月胜率', 'Calmar']
keys = ['total_return', 'annualized_return', 'sharpe', 'max_drawdown', 'win_rate_weekly', 'win_rate_monthly', 'calmar']
col_names = [m['name'] for m in etf_metrics]
table_data = [[m.get(k, 'N/A') for m in etf_metrics] for k in keys]
table = ax7.table(cellText=table_data, rowLabels=metric_labels, colLabels=col_names,
                  cellLoc='center', loc='center', colWidths=[0.22]*len(col_names))
table.auto_set_font_size(False)
table.set_fontsize(8)
table.scale(1, 1.5)
for i in range(len(metric_labels)+1):
    for j in range(len(col_names)):
        cell = table[i, j]
        if i == 0:
            cell.set_facecolor('#2C3E50')
            cell.set_text_props(color='white', fontproperties=font_prop, fontweight='bold')
        else:
            cell.set_facecolor('#FADBD8' if j == 0 else '#D6EAF8' if j == 2 else '#F9F9F9')
            cell.set_text_props(fontproperties=font_prop)

# 图8: 最新持仓
ax8 = plt.subplot2grid((7, 2), (6, 0), colspan=2)
ax8.axis('off')
latest = weekly_df.iloc[-1]
date_str = weekly_df.index[-1].strftime('%Y-%m-%d')
ax8.text(0.5, 0.8, f'最新持仓 ({date_str})', fontsize=14, fontproperties=font_prop,
         fontweight='bold', ha='center', transform=ax8.transAxes)
ax8.text(0.5, 0.4, latest['holdings'], fontsize=11, fontproperties=font_prop, ha='center',
         transform=ax8.transAxes, bbox=dict(boxstyle='round,pad=0.5', facecolor='#EBF5FB', edgecolor='#3498DB'))
ax8.text(0.5, 0.1, f"持仓{latest['n_holdings']}只 | 上周收益={latest['etf_return']*100:+.2f}%", 
         fontsize=11, fontproperties=font_prop, ha='center', transform=ax8.transAxes, color='#666')

plt.tight_layout(pad=2.0)
chart_path = os.path.join(OUTPUT_DIR, "backtest_v3_chart.png")
plt.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor='white')
plt.close()
print(f"图表已保存: {chart_path}")

# ============================================================
# HTML报告
# ============================================================
with open(chart_path, 'rb') as f:
    chart_b64 = base64.b64encode(f.read()).decode()

best = [m for m in metrics if m['name'] == '每日信号(ETF实际)'][0]
bench = [m for m in metrics if m['name'] == '等权基准'][0]

# 持仓表
holdings_html = ""
for _, row in weekly_df.iterrows():
    holdings_html += f"<tr><td>{row.name.strftime('%Y-%m-%d')}</td><td>{row['n_holdings']}</td><td>{row['holdings']}</td><td class='{'pos' if row['etf_return'] > 0 else 'neg'}'>{row['etf_return']*100:+.2f}%</td></tr>"

# 信号表
signals_html = ""
for s in signals:
    sig_cls = 'pos' if '机会' in s['signal'] else 'neg'
    cs = s.get('cmf_short', 0)
    cl = s.get('cmf_long', 0)
    signals_html += f"<tr><td>{s['date']}</td><td class='{sig_cls}'>{s['signal']}</td><td>{s['industry']}({s.get('etf_name','')})</td><td>{cs:.4f}</td><td>{cl:.4f}</td></tr>"

html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V3 行业资金流向信号策略回测报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'PingFang SC','Microsoft YaHei',sans-serif; background:#f5f5f5; color:#333; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
.header {{ background:linear-gradient(135deg,#C62828 0%,#E53935 100%); color:white; padding:30px; border-radius:12px; margin-bottom:20px; text-align:center; }}
.header h1 {{ font-size:24px; margin-bottom:10px; }}
.header .subtitle {{ font-size:14px; opacity:0.8; }}
.header .source {{ font-size:12px; opacity:0.6; margin-top:8px; }}
.section {{ background:white; border-radius:12px; padding:25px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
.section h2 {{ font-size:18px; color:#C62828; border-left:4px solid #C62828; padding-left:12px; margin-bottom:15px; }}
.chart-container {{ text-align:center; margin:15px 0; }}
.chart-container img {{ max-width:100%; border-radius:8px; }}
table {{ width:100%; border-collapse:collapse; margin:15px 0; font-size:13px; }}
th {{ background:#C62828; color:white; padding:8px; text-align:center; font-weight:500; }}
td {{ padding:6px 8px; text-align:center; border-bottom:1px solid #eee; }}
td.pos {{ color:#c62828; }} td.neg {{ color:#2e7d32; }}
tr:hover {{ background:#f9f9f9; }}
.summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:15px; margin:15px 0; }}
.card {{ background:#f8f9fa; border-radius:8px; padding:15px; text-align:center; border:1px solid #e0e0e0; }}
.card .title {{ font-size:12px; color:#666; margin-bottom:5px; }}
.card .value {{ font-size:22px; font-weight:bold; }}
.card .value.pos {{ color:#c62828; }} .card .value.neg {{ color:#2e7d32; }}
.note {{ background:#FFF3E0; border-left:4px solid #FF9800; padding:15px; border-radius:8px; margin:15px 0; font-size:13px; }}
.note strong {{ color:#E65100; }}
.highlight {{ background:#E8F5E9; border-left:4px solid #43A047; padding:15px; border-radius:8px; margin:15px 0; font-size:13px; }}
.highlight strong {{ color:#2E7D32; }}
.disclaimer {{ font-size:12px; color:#999; text-align:center; margin-top:20px; padding:15px; }}
</style></head><body>
<div class="container">
    <div class="header">
        <h1>V3 行业资金流向信号策略回测报告</h1>
        <div class="subtitle">正向跟钱走 | 机会信号建仓+风险信号清仓 | 2026-01-01 ~ 2026-08-14</div>
        <div class="source">报告来自于 DoMate | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>
    
    <div class="section">
        <h2>核心结果</h2>
        <div class="summary">
            <div class="card"><div class="title">每日信号(ETF)累计收益</div><div class="value pos">{best['total_return']}</div></div>
            <div class="card"><div class="title">等权基准累计收益</div><div class="value neg">{bench['total_return']}</div></div>
            <div class="card"><div class="title">超额收益</div><div class="value pos">+16.10%</div></div>
            <div class="card"><div class="title">周胜率</div><div class="value pos">{best['win_rate_weekly']}</div></div>
            <div class="card"><div class="title">月胜率</div><div class="value pos">{best['win_rate_monthly']}</div></div>
            <div class="card"><div class="title">夏普比率</div><div class="value pos">{best['sharpe']}</div></div>
        </div>
        <div class="highlight">
            <strong>V3核心改进: 正向跟钱走, 不反向</strong><br>
            V1(正向排名): -24.89% -> V2(反向策略): +7.23%(但逻辑不对) -> <strong>V3(信号驱动): +9.53%</strong><br>
            V3保持研报本意: 资金流入=机会(买入), 资金流出=风险(卖出). 用信号驱动进出场, 而非机械排名轮动.
        </div>
    </div>
    
    <div class="section">
        <h2>信号系统说明</h2>
        <div class="note">
            <strong>机会信号 (建仓):</strong> CMF短期均线上穿0线, 资金从流出转为流入. 行业资金流向由负转正, 买方开始主导.<br><br>
            <strong>风险信号 (清仓):</strong> CMF短期均线下穿0线, 资金从流入转为流出. 行业资金流向由正转负, 卖方开始主导.<br><br>
            <strong>预警信号 (减仓):</strong> CMF短线<0但长线>0, 短期资金流出但长期趋势未破, 减仓观察.<br><br>
            <strong>持有信号:</strong> CMF短线>0且长线>0, 资金持续流入, 继续持有.<br><br>
            <strong>信号准确率:</strong> 机会信号43.8%, 风险信号59.3%. 风险信号比机会信号更准, 说明"跑得快"比"追得上"更重要.
        </div>
    </div>
    
    <div class="section">
        <h2>净值曲线与信号图表</h2>
        <div class="chart-container"><img src="data:image/png;base64,{chart_b64}" alt="回测图表"></div>
    </div>
    
    <div class="section">
        <h2>回测指标对比</h2>
        <table>
            <thead><tr><th>指标</th>{''.join(f'<th>{m["name"]}</th>' for m in metrics)}</tr></thead>
            <tbody>
            {"".join('<tr><td>' + lbl + '</td>' + "".join('<td>' + str(m.get(k, 'N/A')) + '</td>' for m in metrics) + '</tr>' for k, lbl in zip(['total_return','annualized_return','annualized_vol','sharpe','max_drawdown','win_rate_weekly','win_rate_monthly','calmar'], ['累计收益','年化收益','年化波动','夏普比率','最大回撤','周胜率','月胜率','Calmar']))}
            </tbody>
        </table>
    </div>
    
    <div class="section">
        <h2>信号记录 (共{len(signals)}次)</h2>
        <table>
            <thead><tr><th>日期</th><th>信号类型</th><th>行业(ETF)</th><th>CMF短期</th><th>CMF长期</th></tr></thead>
            <tbody>{signals_html}</tbody>
        </table>
    </div>
    
    <div class="section">
        <h2>周度持仓记录</h2>
        <table>
            <thead><tr><th>日期</th><th>持仓数</th><th>持仓详情</th><th>周收益(ETF)</th></tr></thead>
            <tbody>{holdings_html}</tbody>
        </table>
    </div>
    
    <div class="disclaimer">
        本报告由DuMate自动生成, 仅供研究参考, 不构成投资建议。<br>
        策略基于华泰证券金融工程研报《行业资金流向图应用于行业轮动》, V3版本采用正向信号驱动策略。
    </div>
</div></body></html>"""

html_path = os.path.join(OUTPUT_DIR, "backtest_v3_report.html")
with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"HTML报告已保存: {html_path}")

# ============================================================
# 飞书交互卡片
# ============================================================
latest = weekly_df.iloc[-1]
latest_date = weekly_df.index[-1].strftime('%Y-%m-%d')

card = {
    "config": {"wide_screen_mode": True},
    "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate"}, "template": "red"},
    "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content":
            f"**V3 行业资金流向信号策略回测报告**\n"
            f"正向跟钱走 | 机会信号建仓+风险信号清仓\n"
            f"回测区间: 2026-01-01 ~ 2026-08-14\n"
            f"报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": "**核心指标 (每日信号ETF实际)**"}},
        {"tag": "column_set", "flex_mode": "bypass", "background_style": "grey", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"累计收益\n**{best['total_return']}**"}}], "vertical_align": "center"},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"超额收益\n**+16.10%**"}}], "vertical_align": "center"},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"周胜率\n**{best['win_rate_weekly']}**"}}], "vertical_align": "center"},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"月胜率\n**{best['win_rate_monthly']}**"}}], "vertical_align": "center"},
        ]},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content":
            "**信号系统**\n"
            "机会信号: CMF上穿0线, 资金从流出转流入 -> 建仓\n"
            "风险信号: CMF下穿0线, 资金从流入转流出 -> 清仓\n"
            f"回测期共触发{len(signals)}次信号\n"
            "机会信号准确率: 43.8% | 风险信号准确率: 59.3%\n"
            "风险信号比机会信号更准 -> 跑得快比追得上更重要"}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content":
            f"**最新持仓 ({latest_date})**\n{latest['holdings']}\n"
            f"持仓{latest['n_holdings']}只 | 上周收益={latest['etf_return']*100:+.2f}%"}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content":
            "**版本对比**\n"
            "V1(正向排名): -24.89%, 机械选Top5\n"
            "V2(反向策略): +7.23%, 但逻辑不对(买跌最多的)\n"
            "V3(信号驱动): +9.53%, 正向跟钱走, 信号进出\n"
            "V3保持研报本意: 资金流入=机会, 资金流出=风险"}},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "本报告由DuMate自动生成, 仅供研究参考, 不构成投资建议"}]}
    ]
}

resp = requests.post(WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=15)
result = resp.json()
if result.get("code") == 0 or result.get("StatusCode") == 0:
    print("飞书卡片发送成功!")
else:
    print(f"飞书返回: {result}")
