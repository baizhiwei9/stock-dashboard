#!/usr/bin/env python3
"""多任务风控对比 (终版: RMT/TDA/HLM/混沌/物理几何PG) - 图表 + HTML"""
import os, sys, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager

WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)

_CJK = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
if os.path.exists(_CJK):
    font_manager.fontManager.addfont(_CJK)
plt.rcParams['font.family'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False

os.makedirs(os.path.join(WORK, 'charts_multi'), exist_ok=True)
out = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk.pkl'))

STRATS = [
    ('RMT熔断器', 'rmt', '#d62728'),
    ('TDA拓扑控仓', 'tda', '#1f77b4'),
    ('HLM MA40趋势', 'hlm', '#2ca02c'),
    ('分析力学混沌', 'chaos', '#ff7f0e'),
    ('物理几何PG', 'pg', '#9467bd'),
    ('基准(满仓)', 'bench', '#7f7f7f'),
]
N_RISK = 5

def img_b64(path):
    with open(path, 'rb') as f:
        return 'data:image/png;base64,' + base64.b64encode(f.read()).decode()

def metrics(ret, cum, rf=0.015):
    n = len(ret); years = n/252
    total = cum.iloc[-1]-1; annual = (1+total)**(1/years)-1
    vol = ret.std()*np.sqrt(252)
    sharpe = (ret.mean()*252-rf)/vol if vol > 0 else 0
    mdd = (cum/cum.cummax()-1).min()
    calmar = annual/abs(mdd) if mdd != 0 else 0
    win = (ret > 0).mean()
    return total, annual, vol, sharpe, mdd, calmar, win

M = {}
for label, key, _ in STRATS:
    M[key] = metrics(out[f'{key}_ret'], out[f'{key}_cum'])

years = sorted(set(out.index.year))
yr_map = {}
for label, key, _ in STRATS:
    yr_map[key] = out[f'{key}_ret'].groupby(out.index.year).apply(lambda x: (1+x).prod()-1)

# ===== 图1 净值 =====
fig, ax = plt.subplots(figsize=(12, 5.8))
for label, key, c in STRATS:
    lw = 2.0 if key == 'hlm' else (1.5 if key == 'bench' else 1.3)
    ax.plot(out.index, out[f'{key}_cum'], label=label, color=c, lw=lw)
ax.axhline(1.0, color='black', lw=0.6, alpha=0.4)
ax.set_title('各任务风控策略在沪深300上的净值对比 (2022-01 ~ 2026-08-19)', fontsize=13)
ax.set_ylabel('净值'); ax.legend(loc='upper left', fontsize=9.5); ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
fig.savefig(os.path.join(WORK, 'charts_multi', 'equity.png'), dpi=110, bbox_inches='tight'); plt.close(fig)

# ===== 图2 回撤 =====
fig, ax = plt.subplots(figsize=(12, 4.9))
for label, key, c in STRATS:
    ax.fill_between(out.index, out[f'{key}_cum_dd'], 0, alpha=0.15, color=c)
    ax.plot(out.index, out[f'{key}_cum_dd'], label=label, color=c, lw=1.15)
ax.set_title('回撤曲线对比', fontsize=13); ax.set_ylabel('回撤')
ax.legend(loc='lower left', fontsize=9); ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
fig.savefig(os.path.join(WORK, 'charts_multi', 'drawdown.png'), dpi=110, bbox_inches='tight'); plt.close(fig)

# ===== 图3 仓位 =====
fig, ax = plt.subplots(figsize=(12, 4.6))
for label, key, c in STRATS[:N_RISK]:
    ax.plot(out.index, out[f'{key}_pos']*100, label=label, color=c, lw=1.0)
ax.set_title('仓位控制对比 (T-1收盘生成信号, T日生效)', fontsize=13)
ax.set_ylabel('仓位 (%)'); ax.set_ylim(-2, 105)
ax.legend(loc='upper right', fontsize=9); ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
fig.savefig(os.path.join(WORK, 'charts_multi', 'position.png'), dpi=110, bbox_inches='tight'); plt.close(fig)

# ===== 图4 年度收益 =====
fig, ax = plt.subplots(figsize=(12, 5.6))
x = np.arange(len(years)); w = 0.13
colors = {'rmt':'#d62728','tda':'#1f77b4','hlm':'#2ca02c','chaos':'#ff7f0e','pg':'#9467bd','bench':'#7f7f7f'}
labels = {'rmt':'RMT','tda':'TDA','hlm':'HLM','chaos':'混沌','pg':'物理几何','bench':'基准'}
for j, (label, key, _) in enumerate(STRATS):
    arr = yr_map[key]
    ax.bar(x+(j-(len(STRATS)-1)/2)*w, arr.values*100, w, label=labels[key], color=colors[key], alpha=0.9)
ax.axhline(0, color='black', lw=0.7)
ax.set_xticks(x); ax.set_xticklabels([str(y) for y in years])
ax.set_ylabel('收益 (%)'); ax.set_title('年度收益对比 (%)', fontsize=13)
ax.legend(loc='upper left', fontsize=9, ncol=3); ax.grid(axis='y', alpha=0.3)
fig.savefig(os.path.join(WORK, 'charts_multi', 'yearly.png'), dpi=110, bbox_inches='tight'); plt.close(fig)

# ===== 图5 月度超额 =====
grp = out.groupby([out.index.year, out.index.month])
def mom(x): return (1+x).prod()-1
m_ret = {key: grp[f'{key}_ret'].apply(mom) for _, key, _ in STRATS if key != 'bench'}
m_bench = grp['bench_ret'].apply(mom)
fig, ax = plt.subplots(figsize=(12, 5.4))
idx = np.arange(len(m_bench))
ex_list = [('rmt','RMT','#d62728'), ('tda','TDA','#1f77b4'), ('hlm','HLM','#2ca02c'),
           ('chaos','混沌','#ff7f0e'), ('pg','物理几何','#9467bd')]
n_e = len(ex_list); bw = 0.8/n_e
for j, (key, lab, c) in enumerate(ex_list):
    ex = m_ret[key] - m_bench
    ax.bar(idx+(j-(n_e-1)/2)*bw, ex.values*100, bw, label=lab, color=c, alpha=0.85)
ax.axhline(0, color='black', lw=0.7)
ax.set_xticks(idx); ax.set_xticklabels([f'{y}-{m:02d}' for y, m in m_bench.index], rotation=90, fontsize=7)
ax.set_ylabel('月度超额 (%)'); ax.set_title('月度超额收益对比 (相对满仓沪深300)', fontsize=13)
ax.legend(loc='upper left', fontsize=8.5, ncol=3); ax.grid(axis='y', alpha=0.3)
fig.savefig(os.path.join(WORK, 'charts_multi', 'monthly_excess.png'), dpi=110, bbox_inches='tight'); plt.close(fig)

charts = {k: img_b64(os.path.join(WORK, 'charts_multi', f'{k}.png')) for k in ['equity','drawdown','position','yearly','monthly_excess']}

# ===== 表数据 =====
def pct(v, d=2): return f'{v*100:.{d}f}%'
metric_rows = ''
for label, key, _ in STRATS:
    total, ann, vol, sharpe, mdd, calmar, win = M[key]
    ap = out[f'{key}_pos'].mean()*100 if key != 'bench' else 100.0
    cls = 'best' if key == 'hlm' else ('worst' if key == 'chaos' else '')
    metric_rows += (f'<tr><td><span class="tag {key}">{label}</span></td>'
                    f'<td class="{cls}">{pct(total)}</td><td>{pct(ann)}</td><td>{pct(vol)}</td>'
                    f'<td>{sharpe:.2f}</td><td>{mdd*100:.2f}%</td><td>{calmar:.2f}</td>'
                    f'<td>{pct(win)}</td><td>{ap:.1f}%</td></tr>')

yr_head = ''.join(f'<th>{labels[k]}</th>' for _, k, _ in STRATS)
yr_rows = ''
for y in years:
    cells = [f'<td>{y}</td>']
    for _, k, _ in STRATS:
        v = yr_map[k].get(y, float('nan'))
        cells.append(f'<td>{v*100:+.1f}</td>')
    yr_rows += '<tr>' + ''.join(cells) + '</tr>'

seg_rows = [
    ('2022年熊市全年', '2022-01-04','2022-12-30'),
    ('2022年4月加速下跌', '2022-04-01','2022-04-26'),
    ('2023年全年', '2023-01-03','2023-12-29'),
    ('2024年924行情前后', '2024-09-20','2024-10-08'),
    ('2024年全年', '2024-01-02','2024-12-31'),
    ('2025年全年', '2025-01-02','2025-12-31'),
    ('2026年至今', '2026-01-02','2026-08-19'),
]
seg_html = ''
for name, s, e in seg_rows:
    sub = out.loc[s:e]
    cells = [f'<td>{name}</td>']
    for label, key, _ in STRATS:
        r = (1+sub[f'{key}_ret']).prod()-1
        cells.append(f'<td>{pct(r, 1)}</td>')
    seg_html += '<tr>' + ''.join(cells) + '</tr>'

chaos_dist = out['chaos_risk'].value_counts()
order = {'DANGER': 0, 'WARNING': 1, 'CLEAR': 2}
chaos_rows = ''.join(
    f'<tr><td>{k}</td><td>{v}</td><td>{v/len(out)*100:.1f}%</td></tr>'
    for k, v in sorted(chaos_dist.items(), key=lambda x: order[x[0]]))

pg_dist = out['pg_state'].value_counts()
pg_rows = ''.join(
    f'<tr><td>{k}</td><td>{v}</td><td>{v/len(out)*100:.1f}%</td></tr>'
    for k, v in pg_dist.items())
pg_bear = (out['pg_pos'] < 0.6).mean()*100

html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>多任务风控策略对比报告 (沪深300)</title>
<style>
:root {{ --rmt:#d62728; --tda:#1f77b4; --hlm:#2ca02c; --chaos:#ff7f0e; --pg:#9467bd; --bench:#7f7f7f; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; background:#f5f6fa; color:#2d3436; line-height:1.65; }}
.wrap {{ max-width:1140px; margin:0 auto; padding:28px 20px 60px; }}
h1 {{ font-size:25px; margin-bottom:6px; }}
.sub {{ color:#636e72; font-size:14px; margin-bottom:22px; }}
.card {{ background:#fff; border-radius:12px; box-shadow:0 2px 12px rgba(0,0,0,.06); padding:24px 28px; margin-bottom:22px; }}
.card h2 {{ font-size:18px; margin-bottom:14px; border-left:4px solid #0984e3; padding-left:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
th,td {{ padding:7px 9px; text-align:right; border-bottom:1px solid #eee; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ background:#fafafa; color:#636e72; font-weight:600; }}
.verdict {{ background:linear-gradient(135deg,#07a5c0,#0984e3); color:#fff; padding:18px 22px; border-radius:10px; font-size:15px; }}
.verdict b {{ font-size:16.5px; }}
.tag {{ display:inline-block; padding:1px 9px; border-radius:99px; font-size:11.5px; color:#fff; }}
.tag.rmt {{ background:var(--rmt); }} .tag.tda {{ background:var(--tda); }} .tag.hlm {{ background:var(--hlm); }} .tag.chaos {{ background:var(--chaos); }} .tag.pg {{ background:var(--pg); }} .tag.bench {{ background:var(--bench); }}
img.chart {{ width:100%; border-radius:8px; border:1px solid #eee; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
@media(max-width:760px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.note {{ background:#fffbe6; border:1px solid #ffe58f; border-radius:8px; padding:11px 15px; font-size:13px; color:#8c6d1f; margin-top:12px; }}
.gap {{ background:#fff1f0; border:1px solid #ffccc7; border-radius:8px; padding:11px 15px; font-size:13px; color:#a8071a; margin-top:12px; }}
.small {{ font-size:12.5px; color:#636e72; }}
.best {{ color:#2ca02c; font-weight:700; }} .worst {{ color:#d62728; font-weight:700; }}
</style></head><body><div class="wrap">

<h1>多任务风控策略对比：沪深300持仓回测</h1>
<div class="sub">回测区间 2022-01-04 ~ 2026-08-19（1120交易日）· 标的沪深300 · T-1收盘生成信号、T日生效 · 无交易成本 · 基准=满仓买入持有</div>

<div class="card"><div class="verdict">
<b>核心结论：HLM MA40 趋势过滤大幅领先，是唯一显著正收益、回撤最小的风控；物理几何PG防守稳健但进攻不足。</b><br>
<span class="tag hlm">HLM(MA40趋势)</span> 总收益 <b>+27.29%</b>（年化 +5.58%），跑赢满仓基准约 <b>+34pct</b>，最大回撤仅 -11.41%（基准 -35.10%），夏普 0.38、卡玛 0.49 均全场最高——平均仅用 45.4% 仓位（54.6% 时间空仓），2022 熊市几乎完全避险（+0.1% vs 基准 -21.3%），2024-2025 上涨段又基本吃满。
<span class="tag rmt">RMT</span>（-0.26%）、<span class="tag pg">物理几何PG</span>（-2.79%）收益小幅跑赢基准且防御有效（PG 回撤 -21.9%、波动 11.0% 全场最低）；<span class="tag tda">TDA</span>（-9.93%）、<span class="tag chaos">混沌</span>（-12.85%）为负收益，混沌 2022 年防守失灵、TDA 长期低仓位拖累收益。
按收益-风险综合（总收益/夏普/卡玛/回撤）排序：<b>HLM &gt; RMT &gt; PG &gt; TDA &gt; 混沌</b>。
</div></div>

<div class="card">
<h2>核心指标对比（统一沪深300口径）</h2>
<table>
<tr><th>策略</th><th>总收益</th><th>年化</th><th>年化波动</th><th>夏普</th><th>最大回撤</th><th>卡玛</th><th>日胜率</th><th>平均仓位</th></tr>
{metric_rows}
</table>
<div class="note">注：夏普按 1.5% 年化无风险利率；5 个策略基于同一份沪深300日收益（口径完全一致），仅仓位信号不同；无交易成本与滑点。来源任务：RMT 轮动、TDA-22因子、Hodge Laplacian ETF轮动、分析力学量化系统、物理几何市场分析。</div>
</div>

<div class="card">
<h2>年度收益对比</h2>
<img class="chart" src="{charts['yearly']}" alt="年度收益">
<table>
<tr><th>年份</th>{yr_head}</tr>
{yr_rows}
</table>
</div>

<div class="card">
<h2>净值曲线</h2>
<img class="chart" src="{charts['equity']}" alt="净值">
</div>

<div class="card grid2">
<div><h2>回撤曲线</h2><img class="chart" src="{charts['drawdown']}" alt="回撤"></div>
<div><h2>仓位控制</h2><img class="chart" src="{charts['position']}" alt="仓位"></div>
</div>

<div class="card">
<h2>月度超额收益（相对满仓沪深300）</h2>
<img class="chart" src="{charts['monthly_excess']}" alt="月度超额">
</div>

<div class="card">
<h2>关键行情区间表现</h2>
<table>
<tr><th>区间</th><th>RMT</th><th>TDA</th><th>HLM</th><th>混沌</th><th>物理几何</th><th>基准</th></tr>
{seg_html}
</table>
</div>

<div class="card grid2">
<div>
<h2>分析力学混沌风控信号分布</h2>
<table>
<tr><th>等级</th><th>天数</th><th>占比</th></tr>
{chaos_rows}
</table>
<p class="small">映射: DANGER清仓0% / WARNING控仓40% / CLEAR正常100%。DANGER仅占{chaos_dist.get("DANGER",0)/len(out)*100:.1f}%，多数时间未触发强防守，2022 年 CLEAR 居多是其跑输基准主因。</p>
</div>
<div>
<h2>物理几何PG状态分布</h2>
<table>
<tr><th>状态</th><th>天数</th><th>占比</th></tr>
{pg_rows}
</table>
<p class="small">PG 绝大多数时间处于"过渡态"（与线上 2026-08-24 实盘输出一致），仓位由机会/风险分连续映射：<b>仓位=clip(1-0.8×看跌概率, 20%, 100%)</b>（映射假设可调）。低仓(&lt;60%)占比 {pg_bear:.1f}%。</p>
</div>
</div>

<div class="card">
<h2>各风控规则说明（映射到沪深300）</h2>
<p><b><span class="tag hlm">HLM MA40趋势过滤</span></b>（Hodge Laplacian ETF轮动任务）：线上部署版趋势过滤规则——标的价格 ≥ MA40 满仓，&lt; MA40 空仓；本回测直接作用于沪深300。</p>
<p><b><span class="tag pg">物理几何PG</span></b>（物理几何市场分析任务）：完整复刻线上 20 标的截面（沪深300/中证500/中证1000/科创50/创业板指 + 15 行业ETF）125日收益矩阵 → 物理引擎（Ising磁化M/耦合J/关联长度xi/温度/微观稳定MSF/RMT谱）+ 几何引擎（PCA流形/Betti/Ricci/Morse形态/Higuchi分形维/Hurst）→ 融合状态机给出机会/风险分与牛/震荡/熊概率 → 看跌概率越高仓位越低。</p>
<p><b><span class="tag chaos">分析力学混沌</span></b>（分析力学量化系统任务）：沪深300 100日Hurst(R/S)/100日最大Lyapunov(Rosenstein)/60日KAM环面度/MA20 → 三层判定 DANGER(0%)/WARNING(40%)/CLEAR(100%)。</p>
<p><b><span class="tag rmt">RMT熔断器</span></b>：19ETF相关谱 60日窗口，Gap₁&lt;0.10→90%、ΔS&gt;+0.03→70%、ΔS&lt;-0.10→100%抄底、其余满仓。</p>
<p><b><span class="tag tda">TDA拓扑控仓</span></b>：22申万行业 60日窗口 UMAP+ripser→5状态脆弱性，bull/bear/震荡档按脆弱性映射。</p>
</div>

<div class="card">
<h2>未纳入对比：曼宁资金流任务</h2>
<div class="gap">曼宁资金流（华泰金工 ANB 因子，Tushare moneyflow_ind_ths L2 主动买卖数据）任务本身是行业轮动策略（选Top行业ETF周频调仓），其真实资金流数据仅覆盖 2026 年 1 月及 3-8 月共 107 个交易日（2月因春节+接口限频缺失），且策略不含独立的指数仓位风控模块——无法构建 2022-2026 长区间的沪深300仓控序列。该任务 2026 YTD 回测：W7K1 +28.20%、W10K3 +3.49%（跑赢29行业等权基准-11.82%）。如需纳入统一对比，需先补齐 2024-2025 年 L2 资金流数据。</div>
</div>

<div class="card">
<h2>结论与建议（按高风险高收益取向）</h2>
<p><b>1. <span class="tag hlm">HLM MA40</span>（首选）</b> — 总收益 +27.29%、回撤 -11.41%、卡玛 0.49 全维度最优：上涨段满仓跟涨、下跌段空仓避险，规则极简、稳健性最强，可直接作为沪深300/宽基仓控基线。</p>
<p><b>2. <span class="tag rmt">RMT</span></b> — 收益与基准相当、回撤略优；作为轮动策略（18ETF +100.59%）的熔断器价值高于宽基仓控，适合配合高 beta 策略使用。</p>
<p><b>3. <span class="tag pg">物理几何PG</span></b> — 波动最低（11.0%）、防守稳健（回撤 -21.9%、2022 年跑赢基准 8.5pct），但仓位下限 20% + 看跌概率映射偏温和，2025 年行情吃不满（+10.1% vs 基准 +17.7%）；若提高减仓斜率可进攻性更强。</p>
<p><b>4. <span class="tag tda">TDA</span></b> — 防守型中端：回撤 -26.7% 尚可，但平均 63.7% 仓位拖累收益，适合高波动小众池而非宽基。</p>
<p><b>5. <span class="tag chaos">混沌</span></b> — 全周期负收益、2022 年防守失灵（-24.2% 反跑输基准），阈值体系为个股定制，指数层面暂不建议单独使用。</p>
</div>

</div></body></html>'''

report_path = os.path.join(WORK, '多任务风控对比报告.html')
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(html)
print('报告已生成:', report_path, os.path.getsize(report_path), 'bytes')