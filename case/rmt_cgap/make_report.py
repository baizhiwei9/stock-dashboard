#!/usr/bin/env python3
"""RMT vs TDA 风控对比 - 生成HTML分析报告"""
import os, sys, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)

_CJK_FONT = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
if os.path.exists(_CJK_FONT):
    font_manager.fontManager.addfont(_CJK_FONT)
plt.rcParams['font.family'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False

df = pd.read_pickle(os.path.join(WORK, 'backtest_rmt_vs_tda.pkl'))

def img_b64(path):
    with open(path, 'rb') as f:
        return 'data:image/png;base64,' + base64.b64encode(f.read()).decode()

# ===== 指标计算 =====
def metrics(ret, cum, rf=0.015):
    n = len(ret)
    years = n / 252
    total = cum.iloc[-1] - 1
    annual = (1 + total) ** (1 / years) - 1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252 - rf) / vol
    mdd = (cum / cum.cummax() - 1).min()
    calmar = annual / abs(mdd) if mdd != 0 else 0
    win = (ret > 0).mean()
    return total, annual, vol, sharpe, mdd, calmar, win, n

r_total, r_ann, r_vol, r_sharpe, r_mdd, r_cal, r_win, r_n = metrics(df['rmt_return'], df['rmt_cum'])
t_total, t_ann, t_vol, t_sharpe, t_mdd, t_cal, t_win, t_n = metrics(df['tda_return'], df['tda_cum'])
b_total, b_ann, b_vol, b_sharpe, b_mdd, b_cal, b_win, b_n = metrics(df['bench_return'], df['bench_cum'])

# 年度收益
years = sorted(set(df.index.year))
grp = df.groupby(df.index.year)
rmt_yr = grp['rmt_return'].apply(lambda x: (1+x).prod()-1)
tda_yr = grp['tda_return'].apply(lambda x: (1+x).prod()-1)
bench_yr = grp['bench_return'].apply(lambda x: (1+x).prod()-1)

# 信号分布
rmt_status = df['rmt_status'].value_counts()
tda_state = df['tda_state'].value_counts()

# 关键区间
segments = [
    ('2022年熊市', '2022-01-04', '2022-04-26'),
    ('2022全年', '2022-01-04', '2022-12-30'),
    ('2023全年', '2023-01-03', '2023-12-29'),
    ('2024年初微盘股危机', '2024-01-02', '2024-02-08'),
    ('2024年924行情爆发', '2024-09-24', '2024-10-08'),
    ('2025全年', '2025-01-02', '2025-12-31'),
    ('2026至今', '2026-01-02', '2026-08-19'),
]
seg_rows = []
for name, s, e in segments:
    sub = df.loc[s:e] if e in df.index else df.loc[s:df.index[-1]]
    rr = (1+sub['rmt_return']).prod()-1
    tr = (1+sub['tda_return']).prod()-1
    br = (1+sub['bench_return']).prod()-1
    seg_rows.append((name, rr, tr, br))

# ===== 图表 =====
charts = {}
def make_chart(name, fig):
    path = os.path.join(WORK, 'charts', name)
    fig.savefig(path, dpi=110, bbox_inches='tight')
    plt.close(fig)
    charts[name] = img_b64(path)

# 净值
fig, ax = plt.subplots(figsize=(11, 5.2))
ax.plot(df.index, df['rmt_cum'], label='RMT风控', color='#d62728', lw=1.8)
ax.plot(df.index, df['tda_cum'], label='TDA风控', color='#1f77b4', lw=1.8)
ax.plot(df.index, df['bench_cum'], label='基准(满仓沪深300)', color='#7f7f7f', lw=1.4, ls='--')
ax.axhline(1.0, color='black', lw=0.6, alpha=0.4)
ax.set_title('净值曲线对比 (2022-01 ~ 2026-08, 持仓标的: 沪深300)', fontsize=13)
ax.set_ylabel('净值')
ax.legend(loc='upper left', fontsize=10)
ax.grid(alpha=0.3)
import matplotlib.dates as mdates
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
make_chart('equity_curve.png', fig)

# 回撤
fig, ax = plt.subplots(figsize=(11, 4.4))
for col, lab, c in [('rmt_cum_dd','RMT风控','#d62728'), ('tda_cum_dd','TDA风控','#1f77b4'), ('bench_cum_dd','基准(满仓)','#7f7f7f')]:
    ax.fill_between(df.index, df[col], 0, alpha=0.22, color=c)
    ax.plot(df.index, df[col], label=lab, color=c, lw=1.3)
ax.set_title('回撤曲线对比', fontsize=13)
ax.set_ylabel('回撤幅度')
ax.legend(loc='lower left', fontsize=10)
ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
make_chart('drawdown_curve.png', fig)

# 仓位
fig, ax = plt.subplots(figsize=(11, 4.2))
ax.plot(df.index, df['rmt_pos']*100, label='RMT风控仓位', color='#d62728', lw=1.1)
ax.plot(df.index, df['tda_pos']*100, label='TDA风控仓位', color='#1f77b4', lw=1.1)
ax.set_title('仓位控制对比 (T日信号, T+1生效)', fontsize=13)
ax.set_ylabel('仓位 (%)')
ax.set_ylim(0, 105)
ax.legend(loc='upper right', fontsize=10)
ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
make_chart('position_curve.png', fig)

# 年度收益
fig, ax = plt.subplots(figsize=(11, 5))
x = np.arange(len(years)); w = 0.27
ax.bar(x-w, rmt_yr*100, w, label='RMT风控', color='#d62728')
ax.bar(x, tda_yr*100, w, label='TDA风控', color='#1f77b4')
ax.bar(x+w, bench_yr*100, w, label='基准(满仓)', color='#7f7f7f')
ax.axhline(0, color='black', lw=0.7)
ax.set_xticks(x); ax.set_xticklabels([str(y) for y in years])
ax.set_ylabel('收益 (%)'); ax.set_title('年度收益对比 (%)', fontsize=13)
ax.legend(loc='upper left', fontsize=10); ax.grid(axis='y', alpha=0.3)
for i in x:
    for j, arr in enumerate([rmt_yr, tda_yr, bench_yr]):
        v = arr.iloc[i]*100
        ax.text(i+(j-1)*w, v+(0.6 if v>=0 else -2.2), f'{v:.1f}', ha='center', va='bottom' if v>=0 else 'top', fontsize=8)
make_chart('yearly_returns.png', fig)

# 月度超额
grp_m = df.groupby([df.index.year, df.index.month])
rmt_m = grp_m['rmt_return'].apply(lambda x: (1+x).prod()-1)
tda_m = grp_m['tda_return'].apply(lambda x: (1+x).prod()-1)
bench_m = grp_m['bench_return'].apply(lambda x: (1+x).prod()-1)
re_ex = rmt_m - bench_m
te_ex = tda_m - bench_m
fig, ax = plt.subplots(figsize=(11, 5))
idx = np.arange(len(re_ex))
ax.bar(idx-0.19, re_ex.values*100, 0.38, label='RMT超额', color='#d62728', alpha=0.85)
ax.bar(idx+0.19, te_ex.values*100, 0.38, label='TDA超额', color='#1f77b4', alpha=0.85)
ax.axhline(0, color='black', lw=0.7)
ax.set_xticks(idx); ax.set_xticklabels([f'{y}-{m:02d}' for y, m in re_ex.index], rotation=90, fontsize=7)
ax.set_ylabel('月度超额收益 (%)'); ax.set_title('月度超额收益对比 (相对满仓沪深300)', fontsize=13)
ax.legend(loc='upper left', fontsize=10); ax.grid(axis='y', alpha=0.3)
make_chart('monthly_excess.png', fig)

# ===== HTML =====
def pct(v, digits=2):
    return f'{v*100:.{digits}f}%'

rows_metric = [
    ('总收益', pct(r_total), pct(t_total), pct(b_total)),
    ('年化收益', pct(r_ann), pct(t_ann), pct(b_ann)),
    ('年化波动率', pct(r_vol), pct(t_vol), pct(b_vol)),
    ('夏普比率(1.5%无风险)', f'{r_sharpe:.2f}', f'{t_sharpe:.2f}', f'{b_sharpe:.2f}'),
    ('最大回撤', pct(r_mdd), pct(t_mdd), pct(b_mdd)),
    ('卡玛比率', f'{r_cal:.2f}', f'{t_cal:.2f}', f'{b_cal:.2f}'),
    ('日胜率', pct(r_win), pct(t_win), pct(b_win)),
    ('平均仓位', pct(df['rmt_pos'].mean()), pct(df['tda_pos'].mean()), '100%'),
    ('交易日数', str(r_n), str(t_n), str(b_n)),
]
seg_rows_html = ''.join(
    f'<tr><td>{name}</td><td>{pct(rr)}</td><td>{pct(tr)}</td><td>{pct(br)}</td>'
    f'<td>{pct(rr-br)}</td><td>{pct(tr-br)}</td></tr>' for name, rr, tr, br in seg_rows)

status_html = ''.join(
    f'<tr><td>{k}</td><td>{v}</td><td>{v/len(df)*100:.1f}%</td></tr>' for k, v in rmt_status.items())
tda_html = ''.join(
    f'<tr><td>{k}</td><td>{v}</td><td>{v/len(df)*100:.1f}%</td></tr>' for k, v in tda_state.items())

# 结论
r_better = all([r_total > t_total, r_sharpe > t_sharpe, r_ann > t_ann])
conclusion_main = ('RMT风控在收益端全面占优' if r_better else '两个风控各有优劣')

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RMT风控 vs TDA风控 对比回测报告</title>
<style>
:root {{ --rmt:#d62728; --tda:#1f77b4; --bench:#7f7f7f; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; background:#f5f6fa; color:#2d3436; line-height:1.65; }}
.wrap {{ max-width:1100px; margin:0 auto; padding:28px 20px 60px; }}
h1 {{ font-size:26px; margin-bottom:6px; }}
.sub {{ color:#636e72; font-size:14px; margin-bottom:22px; }}
.card {{ background:#fff; border-radius:12px; box-shadow:0 2px 12px rgba(0,0,0,.06); padding:24px 28px; margin-bottom:22px; }}
.card h2 {{ font-size:18px; margin-bottom:14px; border-left:4px solid #0984e3; padding-left:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ padding:8px 10px; text-align:right; border-bottom:1px solid #eee; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ background:#fafafa; color:#636e72; font-weight:600; }}
.verdict {{ background:linear-gradient(135deg,#0984e3,#6c5ce7); color:#fff; padding:18px 22px; border-radius:10px; font-size:15px; }}
.verdict b {{ font-size:17px; }}
.tag {{ display:inline-block; padding:2px 10px; border-radius:99px; font-size:12px; color:#fff; }}
.tag.rmt {{ background:var(--rmt); }} .tag.tda {{ background:var(--tda); }} .tag.bench {{ background:var(--bench); }}
img.chart {{ width:100%; border-radius:8px; border:1px solid #eee; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
@media(max-width:760px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.note {{ background:#fffbe6; border:1px solid #ffe58f; border-radius:8px; padding:12px 16px; font-size:13px; color:#8c6d1f; margin-top:12px; }}
.small {{ font-size:12.5px; color:#636e72; }}
</style>
</head>
<body>
<div class="wrap">
<h1>RMT风控 vs TDA风控：沪深300持仓对比回测</h1>
<div class="sub">回测区间 2022-01-04 ~ 2026-08-19 · 信号T日生成、T+1生效 · 无交易成本 · 基准=满仓买入持有沪深300</div>

<div class="card">
<div class="verdict">
<b>核心结论：{conclusion_main}</b><br>
从2022年至今（2026-08）持有沪深300：<span class="tag rmt">RMT风控</span> 总收益 {pct(r_total)}（年化 {pct(r_ann)}，最大回撤 {pct(r_mdd)}），
<span class="tag tda">TDA风控</span> 总收益 {pct(t_total)}（年化 {pct(t_ann)}，最大回撤 {pct(t_mdd)}），
<span class="tag bench">满仓基准</span> 总收益 {pct(b_total)}（年化 {pct(b_ann)}，最大回撤 {pct(b_mdd)}）。
RMT风控不仅显著跑赢TDA风控约{pct(r_total - t_total)}，而且相对满仓基准保留了几乎所有上涨收益的同时略微改善了回撤；TDA风控则以约{pct(1-df["tda_pos"].mean())}的平均空仓为代价换取了更低波动，但收益明显受损，夏普比率反而最差。
</div>
</div>

<div class="card">
<h2>核心指标对比</h2>
<table>
<tr><th>指标</th><th><span class="tag rmt">RMT风控</span></th><th><span class="tag tda">TDA风控</span></th><th><span class="tag bench">基准(满仓)</span></th></tr>
{''.join(f'<tr><td>{k}</td><td>{a}</td><td>{b}</td><td>{c}</td></tr>' for k,a,b,c in rows_metric)}
</table>
<div class="note">注：夏普比率按1.5%年化无风险利率计算。年化按252个交易日折算。无交易成本、无滑点；两个风控的仓位信号均于T日收盘生成、T+1日按仓位持有，未引入未来信息。</div>
</div>

<div class="card">
<h2>年度收益对比</h2>
<img class="chart" src="{charts['yearly_returns.png']}" alt="年度收益对比">
</div>

<div class="card">
<h2>净值曲线</h2>
<img class="chart" src="{charts['equity_curve.png']}" alt="净值曲线">
</div>

<div class="card grid2">
<div>
<h2>回撤曲线</h2>
<img class="chart" src="{charts['drawdown_curve.png']}" alt="回撤曲线">
</div>
<div>
<h2>仓位控制</h2>
<img class="chart" src="{charts['position_curve.png']}" alt="仓位控制">
</div>
</div>

<div class="card">
<h2>月度超额收益（相对满仓沪深300）</h2>
<img class="chart" src="{charts['monthly_excess.png']}" alt="月度超额">
</div>

<div class="card">
<h2>关键行情区间表现</h2>
<table>
<tr><th>区间</th><th>RMT风控</th><th>TDA风控</th><th>基准(满仓)</th><th>RMT超额</th><th>TDA超额</th></tr>
{seg_rows_html}
</table>
</div>

<div class="card grid2">
<div>
<h2>RMT信号分布</h2>
<table>
<tr><th>状态</th><th>天数</th><th>占比</th></tr>
{status_html}
</table>
<p class="small">RMT仓位区间: {pct(df['rmt_pos'].min())} ~ {pct(df['rmt_pos'].max())}（正常=100%、危机预警=70%、简并共振=90%、恐慌底=100%）</p>
</div>
<div>
<h2>TDA状态分布</h2>
<table>
<tr><th>状态</th><th>天数</th><th>占比</th></tr>
{tda_html}
</table>
<p class="small">TDA脆弱性: {df['tda_vuln'].min():.1f}% ~ {df['tda_vuln'].max():.1f}%（均值{df['tda_vuln'].mean():.1f}%）；仓位区间 {pct(df['tda_pos'].min())} ~ {pct(df['tda_pos'].max())}，随脆弱性与牛熊状态动态映射。</p>
</div>
</div>

<div class="card">
<h2>方法论与风控规则说明</h2>
<p><b>RMT风控</b>（随机矩阵理论）：以19个宽基/行业ETF的60日收益矩阵计算相关矩阵特征谱，提取简并度Gap₁、谱熵与熵变ΔS。状态机：Gap₁&lt;0.10 → 简并共振警报（仓位降至90%）；ΔS&gt;+0.03 → 危机预警（仓位降至70%）；ΔS&lt;-0.10 → 恐慌底（满仓100%）；其余正常（满仓100%）。设计哲学是<b>熔断器</b>——平时满仓不缺席上涨，仅在市场结构极端异常时减仓。</p>
<p><b>TDA风控</b>（拓扑数据分析）：以22个申万一级行业指数60日价格窗口，经UMAP降维到2D → ripser持续同调提取β₀/β₁/曲率 → 计算脆弱性并分类为牛熊+一体化/碎片化5状态。仓位映射：bull档 max(50%, 1-脆弱性/100×0.5)；bear档 max(15%, 1-脆弱性/100×0.9)；震荡档 max(30%, 1-脆弱性/100×0.7)。设计哲学是<b>持续动态调仓</b>——始终根据市场结构状态调整暴露。</p>
<p class="small">数据源：沪深300指数日线（akshare）、19只ETF日收益（本地缓存etf_returns_with_kc50.pkl）、22个申万行业指数收盘（state/sw_cache本地缓存）。RMT与TDA信号各自基于原始资产池计算，仅将仓位作用于沪深300单一标的，保证对比公平。</p>
</div>

</div>
</body>
</html>'''

out = os.path.join(WORK, 'RMT_vs_TDA_风控对比报告.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print('HTML报告已生成:', out)
print('大小:', os.path.getsize(out), 'bytes')