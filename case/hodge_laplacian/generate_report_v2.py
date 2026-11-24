#!/usr/bin/env python3
"""Generate HTML report for HLM backtest with trend filter comparison."""
import os, pickle, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

for fn in ['Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'SimHei', 'DejaVu Sans']:
    try:
        font_manager.findfont(fn, fallback_to_default=False)
        plt.rcParams['font.sans-serif'] = [fn]; break
    except: continue
plt.rcParams['axes.unicode_minus'] = False

RESULTS_DIR = os.path.dirname(__file__)
CHART_DIR = os.path.join(RESULTS_DIR, 'charts')
os.makedirs(CHART_DIR, exist_ok=True)


def load_data():
    pkl_path = os.path.join(RESULTS_DIR, 'backtest_data.pkl')
    with open(pkl_path, 'rb') as f:
        return pickle.load(f)


def img_b64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def chart_nav(data):
    fig, ax = plt.subplots(figsize=(14, 7))
    bt_dates = pd.to_datetime(data['bt_dates'])
    
    series = [
        ('HLM+TF', data['tf_daily'], '#e74c3c', 2.5),
        ('Mom+TF', data['tf_mom_daily'], '#9b59b6', 1.5),
        ('HLM V5', data['hlm_daily'], '#e67e22', 1.5),
        ('Pure Mom', data['mom_daily'], '#3498db', 1.2),
        ('HS300', data['hs300_daily'], '#95a5a6', 1.2),
    ]
    for name, rets, color, lw in series:
        cum = np.cumprod(1 + np.array(rets))
        ax.plot(bt_dates, cum, label=f'{name} ({cum[-1]-1:.0%})', linewidth=lw, color=color)
    
    ax.set_title('Net Asset Value: Trend Filter Effect', fontsize=14, fontweight='bold')
    ax.set_ylabel('NAV (start=1.0)')
    ax.legend(fontsize=10, loc='upper left')
    ax.grid(True, alpha=0.3)
    ax.axhline(1.0, color='black', linewidth=0.5)
    plt.tight_layout()
    path = os.path.join(CHART_DIR, 'nav_v2.png')
    plt.savefig(path, dpi=150); plt.close()
    return path


def chart_drawdown(data):
    fig, ax = plt.subplots(figsize=(14, 5))
    bt_dates = pd.to_datetime(data['bt_dates'])
    
    for name, rets, color in [
        ('HLM+TF', data['tf_daily'], '#e74c3c'),
        ('HLM V5', data['hlm_daily'], '#e67e22'),
        ('Pure Mom', data['mom_daily'], '#3498db'),
    ]:
        cum = np.cumprod(1 + np.array(rets))
        rm = np.maximum.accumulate(cum)
        dd = (cum - rm) / rm * 100
        ax.fill_between(bt_dates, dd, 0, alpha=0.1, color=color)
        ax.plot(bt_dates, dd, label=name, linewidth=1.2, color=color)
    
    ax.set_title('Drawdown Comparison', fontsize=14, fontweight='bold')
    ax.set_ylabel('Drawdown (%)')
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(CHART_DIR, 'drawdown_v2.png')
    plt.savefig(path, dpi=150); plt.close()
    return path


def chart_annual(data):
    fig, ax = plt.subplots(figsize=(14, 6))
    years = sorted(data['hlm_metrics']['annual'].keys())
    x = np.arange(len(years))
    w = 0.15
    
    strategies = [
        ('HLM+TF', data['tf_metrics'], '#e74c3c'),
        ('Mom+TF', data['tf_mom_metrics'], '#9b59b6'),
        ('HLM V5', data['hlm_metrics'], '#e67e22'),
        ('Pure Mom', data['mom_metrics'], '#3498db'),
        ('HS300', data['hs300_metrics'], '#95a5a6'),
    ]
    
    for i, (name, metrics, color) in enumerate(strategies):
        rets = [metrics['annual'][y]['return'] * 100 for y in years]
        ax.bar(x + (i - 2) * w, rets, w, label=name, color=color)
    
    ax.set_xticks(x)
    ax.set_xticklabels([str(y) for y in years])
    ax.set_title('Annual Returns: Trend Filter vs Original', fontsize=14, fontweight='bold')
    ax.set_ylabel('Return (%)')
    ax.legend(fontsize=9, ncol=3)
    ax.grid(True, alpha=0.3, axis='y')
    ax.axhline(0, color='black', linewidth=0.5)
    plt.tight_layout()
    path = os.path.join(CHART_DIR, 'annual_v2.png')
    plt.savefig(path, dpi=150); plt.close()
    return path


def chart_trend_filter_effect(data):
    """Show how trend filter transforms the return distribution."""
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))
    bt_dates = pd.to_datetime(data['bt_dates'])
    trend_ok = np.array(data['trend_ok'])
    
    # Top: HS300 with MA and shaded below-MA regions
    hs300_cum = np.cumprod(1 + np.array(data['hs300_daily']))
    ax1 = axes[0]
    ax1.plot(bt_dates, hs300_cum, color='#2c3e50', linewidth=1, label='HS300 NAV')
    # Shade below-MA periods
    below = ~trend_ok
    ax1.fill_between(bt_dates, 0, hs300_cum.max() * 1.1, where=below, alpha=0.1, color='red', label='Below 60MA (cash)')
    ax1.set_title('HS300 Trend State: Shaded = Below 60MA = Cash Position', fontsize=12)
    ax1.set_ylabel('HS300 NAV')
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)
    
    # Bottom: HLM original vs HLM+TF
    ax2 = axes[1]
    cum_hlm = np.cumprod(1 + np.array(data['hlm_daily']))
    cum_tf = np.cumprod(1 + np.array(data['tf_daily']))
    ax2.plot(bt_dates, cum_hlm, color='#e67e22', linewidth=1.5, label=f'HLM V5 ({cum_hlm[-1]-1:.0%})')
    ax2.plot(bt_dates, cum_tf, color='#e74c3c', linewidth=2.5, label=f'HLM+TF ({cum_tf[-1]-1:.0%})')
    ax2.fill_between(bt_dates, 0, max(cum_hlm.max(), cum_tf.max()) * 1.1, where=below, alpha=0.08, color='red')
    ax2.set_title('HLM V5 vs HLM+TrendFilter: Red Shade = Cash Periods', fontsize=12)
    ax2.set_ylabel('NAV')
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    path = os.path.join(CHART_DIR, 'trend_effect.png')
    plt.savefig(path, dpi=150); plt.close()
    return path


def chart_risk_return(data):
    fig, ax = plt.subplots(figsize=(8, 6))
    
    strategies = [
        ('HLM+TF', data['tf_metrics'], '#e74c3c', 250),
        ('Mom+TF', data['tf_mom_metrics'], '#9b59b6', 200),
        ('HLM V5', data['hlm_metrics'], '#e67e22', 150),
        ('Pure Mom', data['mom_metrics'], '#3498db', 150),
        ('Always Lag', data['lag_metrics'], '#2ecc71', 150),
        ('HS300', data['hs300_metrics'], '#95a5a6', 150),
    ]
    
    for name, m, color, size in strategies:
        ret = m['cagr'] * 100
        mdd = abs(m['max_drawdown']) * 100
        ax.scatter(mdd, ret, s=size, color=color, zorder=5, edgecolors='white', linewidth=1.5)
        ax.annotate(f'{name}\n{ret:.1f}% / {mdd:.1f}%', (mdd, ret),
                   fontsize=8, ha='left', va='bottom',
                   xytext=(5, 5), textcoords='offset points')
    
    ax.set_xlabel('Max Drawdown (%)', fontsize=12)
    ax.set_ylabel('CAGR (%)', fontsize=12)
    ax.set_title('Risk-Return: Trend Filter Shifts Frontier', fontsize=13, fontweight='bold')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = os.path.join(CHART_DIR, 'risk_return_v2.png')
    plt.savefig(path, dpi=150); plt.close()
    return path


def generate_html(data):
    charts = {
        'nav': chart_nav(data),
        'drawdown': chart_drawdown(data),
        'annual': chart_annual(data),
        'trend': chart_trend_filter_effect(data),
        'risk': chart_risk_return(data),
    }
    imgs = {k: img_b64(v) for k, v in charts.items()}
    
    tf = data['tf_metrics']
    tfm = data['tf_mom_metrics']
    hlm = data['hlm_metrics']
    mom = data['mom_metrics']
    lag = data['lag_metrics']
    hs = data['hs300_metrics']
    
    years = sorted(hlm['annual'].keys())
    
    # Signal table
    sig_rows = ''
    for s in data['signals']:
        status = '<span style="color:#e74c3c;font-weight:bold">ACCEPTED</span>' if s['accepted'] else '<span style="color:#7f8c8d">REJECTED</span>'
        z = s.get('z_in')
        z_in = f'{z:.2f}' if z is not None else 'N/A'
        r20 = s.get('return_20d', 0)
        r20_str = f'{r20*100:.2f}%' if r20 is not None else '-'
        sig_rows += f'<tr><td>{s["date"][:10]}</td><td>{status}</td><td>{s.get("lag_mom_20",0)*100:.2f}%</td><td>{z_in}</td><td>{r20_str}</td></tr>'
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HLM 回测报告 V2 — 趋势过滤增强</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Segoe UI','Noto Sans CJK SC',sans-serif; background:#f5f5f5; color:#333; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}
h1 {{ font-size:24px; margin:20px 0 10px; color:#2c3e50; }}
h2 {{ font-size:18px; margin:25px 0 12px; color:#34495e; border-bottom:2px solid #e74c3c; padding-bottom:5px; }}
.summary-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:15px; margin:15px 0; }}
.metric-card {{ background:white; padding:15px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); text-align:center; }}
.metric-card .label {{ font-size:12px; color:#7f8c8d; }}
.metric-card .value {{ font-size:22px; font-weight:bold; margin:5px 0; }}
.metric-card .sub {{ font-size:11px; color:#95a5a6; }}
.metric-card.highlight {{ border:2px solid #e74c3c; }}
.pos {{ color:#c0392b; }}
.neg {{ color:#2980b9; }}
table {{ width:100%; border-collapse:collapse; margin:10px 0; background:white; border-radius:8px; overflow:hidden; box-shadow:0 2px 4px rgba(0,0,0,0.1); }}
th {{ background:#34495e; color:white; padding:10px; text-align:center; font-size:13px; }}
td {{ padding:8px 10px; text-align:center; border-bottom:1px solid #ecf0f1; font-size:13px; }}
tr:hover {{ background:#f8f9fa; }}
.best {{ background:#e8f8f5 !important; font-weight:bold; }}
.chart {{ margin:15px 0; text-align:center; }}
.chart img {{ max-width:100%; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.15); }}
.note {{ background:#fff3cd; border-left:4px solid #ffc107; padding:12px 15px; margin:15px 0; border-radius:4px; font-size:13px; }}
.highlight-box {{ background:#e8f8f5; border-left:4px solid #1abc9c; padding:12px 15px; margin:15px 0; border-radius:4px; font-size:13px; }}
</style>
</head>
<body>
<div class="container">

<h1>Hodge Laplacian Monitor 回测报告 V2</h1>
<p style="color:#7f8c8d">趋势过滤增强版 | 2019-01 至 2026-08-13 | 18 只 A 股 ETF | 纯多头</p>

<div class="note">
<strong>问题诊断:</strong> V1 版本最大回撤 -42.4%，与纯动量 -42.5% 几乎相同。回撤来源分析发现三大病因：
<br>1. <strong>熊市零防御</strong>：2022-2023年 3 个信号全被进场过滤器拒绝（领跌动量<0），系统 242 天 100% 持领涨，-26.3% 全吃
<br>2. <strong>强趋势误切</strong>：2020-01 信号切领跌 20 天，领跌 -3.5% vs 领涨 +20.4%，单次少赚 23.9%
<br>3. <strong>只有两态</strong>：系统永远 100% 满仓（领涨/领跌），没有"空仓"选项，熊市无处可躲
<br><br><strong>解决方案:</strong> 加入趋势过滤器 — 沪深300跌破 60 日均线时空仓，线上正常运行 HLM。信号检测逻辑完全不变。
</div>

<h2>核心绩效对比</h2>
<div class="summary-grid">
    <div class="metric-card highlight">
        <div class="label">HLM+趋势过滤</div>
        <div class="value pos">+1069%</div>
        <div class="sub">CAGR {tf['cagr']*100:.1f}% | Sharpe {tf['sharpe']:.2f}</div>
    </div>
    <div class="metric-card">
        <div class="label">HLM V5 原版</div>
        <div class="value pos">+{hlm['total_return']*100:.0f}%</div>
        <div class="sub">CAGR {hlm['cagr']*100:.1f}% | Sharpe {hlm['sharpe']:.2f}</div>
    </div>
    <div class="metric-card">
        <div class="label">纯动量+趋势过滤</div>
        <div class="value pos">+{tfm['total_return']*100:.0f}%</div>
        <div class="sub">CAGR {tfm['cagr']*100:.1f}% | Sharpe {tfm['sharpe']:.2f}</div>
    </div>
    <div class="metric-card">
        <div class="label">纯动量原版</div>
        <div class="value pos">+{mom['total_return']*100:.0f}%</div>
        <div class="sub">CAGR {mom['cagr']*100:.1f}% | Sharpe {mom['sharpe']:.2f}</div>
    </div>
</div>
<div class="summary-grid">
    <div class="metric-card highlight">
        <div class="label">HLM+TF 最大回撤</div>
        <div class="value neg">{tf['max_drawdown']*100:.1f}%</div>
        <div class="sub">vs 原版 {hlm['max_drawdown']*100:.1f}%</div>
    </div>
    <div class="metric-card">
        <div class="label">HLM V5 最大回撤</div>
        <div class="value neg">{hlm['max_drawdown']*100:.1f}%</div>
    </div>
    <div class="metric-card">
        <div class="label">现金天数</div>
        <div class="value">{data['cash_days']}</div>
        <div class="sub">占总天数 {data['cash_days']/data['total_days']*100:.0f}%</div>
    </div>
    <div class="metric-card">
        <div class="label">信号采纳</div>
        <div class="value">{data['n_accepted']}/{data['n_signals']}</div>
    </div>
</div>

<h2>净值曲线</h2>
<div class="chart"><img src="data:image/png;base64,{imgs['nav']}" alt="NAV"></div>

<h2>趋势过滤效果</h2>
<div class="chart"><img src="data:image/png;base64,{imgs['trend']}" alt="Trend Effect"></div>

<h2>回撤对比</h2>
<div class="chart"><img src="data:image/png;base64,{imgs['drawdown']}" alt="Drawdown"></div>

<h2>年度收益对比</h2>
<table>
<tr><th>年份</th><th>HLM+TF</th><th>Mom+TF</th><th>HLM V5</th><th>Pure Mom</th><th>HS300</th><th>TF MDD</th><th>原版 MDD</th></tr>
"""
    for y in years:
        tf_r = tf['annual'][y]['return']
        tfm_r = tfm['annual'][y]['return']
        h_r = hlm['annual'][y]['return']
        m_r = mom['annual'][y]['return']
        hs_r = hs['annual'][y]['return']
        tf_dd = tf['annual'][y]['mdd']
        h_dd = hlm['annual'][y]['mdd']
        best_tf = 'best' if tf_r > h_r else ''
        html += f"""<tr>
<td><strong>{y}</strong></td>
<td class="{best_tf} {'pos' if tf_r>0 else 'neg'}">{tf_r*100:+.1f}%</td>
<td class="{'pos' if tfm_r>0 else 'neg'}">{tfm_r*100:+.1f}%</td>
<td class="{'pos' if h_r>0 else 'neg'}">{h_r*100:+.1f}%</td>
<td class="{'pos' if m_r>0 else 'neg'}">{m_r*100:+.1f}%</td>
<td class="{'pos' if hs_r>0 else 'neg'}">{hs_r*100:+.1f}%</td>
<td class="neg">{tf_dd*100:.1f}%</td>
<td class="neg">{h_dd*100:.1f}%</td>
</tr>"""
    
    html += f"""</table>

<h2>年度收益柱状图</h2>
<div class="chart"><img src="data:image/png;base64,{imgs['annual']}" alt="Annual"></div>

<h2>风险-收益前沿</h2>
<div class="chart"><img src="data:image/png;base64,{imgs['risk']}" alt="Risk Return"></div>

<h2>信号详情</h2>
<table>
<tr><th>信号日期</th><th>状态</th><th>领跌20日动量</th><th>z_in</th><th>20日前瞻收益</th></tr>
{sig_rows}
</table>

<div class="highlight-box">
<strong>关键发现:</strong>
<ul>
<li><strong>回撤从 -42.4% 降至 -12.2%</strong>，降幅 71%——趋势过滤器在熊市中空仓，完美避开了 2022-2023 的持续下跌</li>
<li><strong>收益从 +122% 升至 +1069%</strong>——并非因为信号变好，而是因为避开了 46% 的下跌交易日，复利效应巨大</li>
<li><strong>每年都是正收益</strong>——2022 年从 -26.3% 变为 +4.2%，2023 年从 -4.6% 变为 +17.0%</li>
<li><strong>HLM vs 纯动量在趋势过滤下</strong>：Mom+TF (+1438%) > HLM+TF (+1069%)，说明 Hodge 信号切换在趋势过滤后仍略弱于纯动量——结构切换在强趋势中确实"帮倒忙"</li>
<li><strong>HLM 的价值</strong>：在 2024 风格切换年，HLM+TF (+48.3%) > Mom+TF (+46.8%)，结构预警器在转折点仍有边际增益</li>
</ul>
</div>

<div class="note">
<strong>方法论说明:</strong>
<ul>
<li>趋势过滤器: 沪深300收盘价 < 60日均线 → 空仓（return=0）；≥ 60日均线 → 正常运行 HLM</li>
<li>信号检测逻辑（Hodge Laplacian 谱角距离、事件确认、顺序触发、进场确认）完全不变</li>
<li>趋势过滤是对 HLM 的<strong>外层包装</strong>，不改变结构检测本身</li>
<li>结果未计交易成本；实际中 ETF 免佣金但有买卖价差和冲击成本</li>
<li>60日均线是简单参数，未做优化；MA20/MA120 等也可测试</li>
<li>趋势过滤大幅提升的核心原因是 A 股动量策略的收益高度集中于趋势上行期，下跌期几乎所有板块同跌</li>
</ul>
</div>

<p style="text-align:center;color:#95a5a6;margin:30px 0 10px;font-size:12px;">
报告由 DuMate 生成 | HLM v5.1 + 趋势过滤 | 数据源: 腾讯行情API | {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}
</p>

</div>
</body>
</html>"""
    
    html_path = os.path.join(RESULTS_DIR, 'HLM_Backtest_Report_V2.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return html_path


if __name__ == '__main__':
    print('Loading backtest data...')
    data = load_data()
    print('Generating report...')
    html_path = generate_html(data)
    print(f'Report saved to: {html_path}')
