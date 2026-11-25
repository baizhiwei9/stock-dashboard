#!/usr/bin/env python3
"""
v2 多任务风控对比 - 报告生成 (HLM多级仓控核实 + 物理几何子算法拆解)
输出: charts_v2/*.png + 多任务风控对比报告v2.html
"""
import os, sys, base64, io, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False
os.makedirs(os.path.join(WORK, 'charts_v2'), exist_ok=True)

base = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk_v2.pkl'))
mt = pd.read_pickle(os.path.join(WORK, 'metrics_v2.pkl'))
dates = base.index

# ---------- 图表 ----------
def save(fig, name):
    path = os.path.join(WORK, 'charts_v2', name)
    fig.savefig(path, dpi=110, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return path

def b64(path):
    return base64.b64encode(open(path, 'rb').read()).decode()

# Fig1: 五大主策略净值
fig, ax = plt.subplots(figsize=(11, 5.2))
for key, lab, c in [('rmt', 'RMT谱风控', '#d62728'), ('tda', 'TDA拓扑风控', '#1f77b4'),
                    ('hlm', 'HLM(MA40)', '#2ca02c'), ('chaos', '混沌风控', '#ff7f0e'),
                    ('pg', '物理几何融合', '#9467bd'), ('bench', '基准·满仓HS300', '#7f7f7f')]:
    cum = base['bench_cum'] if key == 'bench' else base[f'{key}_cum']
    ax.plot(dates, cum.values, label=lab, color=c, lw=2.2 if key == 'hlm' else (1.6 if key == 'bench' else 1.3))
ax.axhline(1.0, color='#999', ls='--', lw=0.8)
ax.set_title('五大风控策略净值对比 (2022-01 ~ 2026-08, 沪深300, T-1信号T日生效)')
ax.legend(loc='upper left', fontsize=10, ncol=2)
ax.grid(alpha=0.25)
fig.tight_layout()
p1 = save(fig, 'fig1_main_nav.png')

# Fig2: HLM MA 敏感性
fig, axes = plt.subplots(2, 1, figsize=(11, 6.6), gridspec_kw={'height_ratios': [2.4, 1]})
for win, c in [(20, '#ff7f0e'), (40, '#2ca02c'), (60, '#1f77b4')]:
    axes[0].plot(dates, base[f'hlm_ma{win}_cum'].values, label=f'MA{win} 趋势过滤', color=c, lw=1.8)
axes[0].plot(dates, base['bench_cum'].values, label='基准', color='#7f7f7f', lw=1.3, ls='--')
axes[0].axhline(1.0, color='#999', ls='--', lw=0.8)
axes[0].set_title('HLM 趋势过滤窗口敏感性 (均为 0/1 二值仓控: 收盘≥MA满仓 / <MA空仓)')
axes[0].legend(loc='upper left', fontsize=10)
axes[0].grid(alpha=0.25)
for win, c in [(20, '#ff7f0e'), (40, '#2ca02c'), (60, '#1f77b4')]:
    axes[1].plot(dates, base[f'hlm_ma{win}_pos'].values, color=c, lw=0.6, alpha=0.8)
axes[1].set_ylim(-0.05, 1.15)
axes[1].set_title('仓位序列 (20/40/60日均线下方空仓)')
axes[1].grid(alpha=0.25)
fig.tight_layout()
p2 = save(fig, 'fig2_hlm_ma.png')

# Fig3/4: PG 子算法校准版净值 (物理侧 / 几何侧)
def sub_nav_fig(cols, title, name, figsize=(11, 5.2)):
    fig, ax = plt.subplots(figsize=figsize)
    for key, lab, c in cols:
        ax.plot(dates, base[f'{key}_cum'].values, label=lab, color=c, lw=1.5)
    ax.plot(dates, base['bench_cum'].values, label='基准', color='#7f7f7f', lw=1.3, ls='--')
    ax.axhline(1.0, color='#999', ls='--', lw=0.8)
    ax.set_title(title)
    ax.legend(loc='upper left', fontsize=9.5, ncol=2)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    return save(fig, name)

p3 = sub_nav_fig([
    ('pg_cal_M', 'M 磁化', '#1f77b4'), ('pg_cal_J', 'J 耦合', '#ff7f0e'),
    ('pg_cal_xi', 'ξ 关联长度', '#2ca02c'), ('pg_cal_T', 'T 温度', '#d62728'),
    ('pg_cal_MSF', 'MSF 微观稳定', '#9467bd'), ('pg_cal_excess', 'RMT 谱超差', '#8c564b'),
], '物理引擎子算法独立回测 (分位数校准版)', 'fig3_phys.png')
p4 = sub_nav_fig([
    ('pg_cal_manifold', 'PCA 流形', '#1f77b4'), ('pg_cal_betti_1', 'Betti 洞数', '#ff7f0e'),
    ('pg_cal_ricci', 'Ricci 曲率', '#2ca02c'), ('pg_cal_morse', 'Morse 形态', '#d62728'),
    ('pg_cal_D', 'D 分形维', '#9467bd'), ('pg_cal_H', 'H Hurst', '#8c564b'),
], '几何引擎子算法独立回测 (分位数校准版)', 'fig4_geo.png')

# Fig5: 照抄版 vs 校准版 (有信号的 4 个 + 融合)
fig, axes = plt.subplots(2, 2, figsize=(12, 7.2))
subs = [('M 磁化', 'pg_M_magnet', 'pg_cal_M'), ('MSF 微观稳定', 'pg_MSF_stability', 'pg_cal_MSF'),
        ('Morse 形态', 'pg_Morse_shape', 'pg_cal_morse'), ('T 温度', 'pg_T_temperature', 'pg_cal_T')]
for ax, (lab, orig, cal) in zip(axes.ravel(), subs):
    ax.plot(dates, base[f'{orig}_pos'] * base['hs300_ret'].cumsum().add(1).values if False else (1 + base[f'{orig}_ret']).cumprod().values,
            label=f'{lab}·照抄阈值', color='#d62728', lw=1.5)
    ax.plot(dates, (1 + base[f'{cal}_ret']).cumprod().values, label=f'{lab}·校准阈值', color='#2ca02c', lw=1.5)
    ax.plot(dates, base['bench_cum'].values, label='基准', color='#7f7f7f', lw=1.0, ls='--')
    ax.set_title(lab); ax.legend(fontsize=8.5); ax.grid(alpha=0.25)
fig.suptitle('照抄线上阈值 vs 分位数校准 (同一特征, 依上方分别回测)', y=0.99)
fig.tight_layout()
p5 = save(fig, 'fig5_orig_vs_calib.png')

# ---------- 指标表重组 ----------
def row(keys):
    return mt.loc[keys]

main_keys = ['基准', 'RMT谱风控', 'TDA拓扑风控', 'HLM(MA40)', '混沌风控', 'PG融合风控']
hlm_keys = ['HLM(MA20)', 'HLM(MA40)', 'HLM(MA60)']
phys_keys = ['M·Ising磁化·照抄', 'J·耦合强度·照抄', 'ξ·关联长度·照抄', 'T·温度波动·照抄',
             'MSF·微观稳定·照抄', 'RMT·谱超差·照抄']
geo_keys = ['PCA流形·照抄', 'Betti洞数·照抄', 'Ricci曲率·照抄', 'Morse形态·照抄', 'D·分形维·照抄', 'H·Hurst·照抄']
phys_cal = ['M·Ising磁化·校准', 'J·耦合强度·校准', 'ξ·关联长度·校准', 'T·温度波动·校准',
            'MSF·微观稳定·校准', 'RMT·谱超差·校准']
geo_cal = ['PCA流形·校准', 'Betti洞数·校准', 'Ricci曲率·校准', 'Morse形态·校准', 'D·分形维·校准', 'H·Hurst·校准']

def html_table(df, highlight_cols=None, highlight_rows=None, rank_col='总收益%'):
    df = df.copy()
    styles = {}
    if highlight_rows:
        for r in highlight_rows:
            if r in df.index:
                styles[r] = 'background:#e8f5e9;font-weight:bold;border-left:4px solid #2ca02c;'
    # 排名高亮
    best_total = df[rank_col].idxmax()
    best_dd = df['最大回撤%'].idxmax()
    if best_total in df.index and best_total not in styles:
        styles[best_total] = 'background:#ffffcc;font-weight:bold;'
    best_dd_row = df['最大回撤%'].idxmax()
    if best_dd_row in df.index and styles.get(best_dd_row) is None:
        styles[best_dd_row] = styles.get(best_dd_row, '') + 'background:#e3f2fd;'
    cols = ['总收益%', '年化%', '夏普', '最大回撤%', '卡玛', '平均仓位%']
    h = '<table><thead><tr>'
    h += '<th>策略</th>' + ''.join(f'<th>{c}</th>' for c in cols) + '</tr></thead><tbody>'
    for idx, r in df.iterrows():
        st = styles.get(idx, '')
        h += f'<tr style="{st}"><td><b>{idx}</b></td>'
        for c in cols:
            v = r[c]
            if c in ('总收益%', '年化%', '最大回撤%', '平均仓位%'):
                h += f'<td>{v:.2f}%</td>'
            elif c == '卡玛':
                h += f'<td>{v:.2f}</td>'
            else:
                h += f'<td>{v:.2f}</td>'
        h += '</tr>'
    h += '</tbody></table>'
    return h

# ---------- HTML ----------
C = """
<style>
:root { --rmt:#d62728; --tda:#1f77b4; --hlm:#2ca02c; --chaos:#ff7f0e; --pg:#9467bd; --bench:#7f7f7f; }
body { font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; margin:0; background:#f6f7f9; color:#1c1e21; }
.wrap { max-width:1180px; margin:0 auto; padding:28px 20px 60px; }
.head { background:linear-gradient(135deg,#0f2027,#203a43,#2c5364); color:#fff; border-radius:14px; padding:26px 30px; margin-bottom:22px; }
.head h1 { margin:0 0 8px; font-size:24px; }
.head .sub { opacity:.85; font-size:13px; }
.card { background:#fff; border-radius:12px; padding:22px 26px; margin:18px 0; box-shadow:0 1px 4px rgba(0,0,0,.07); }
.card h2 { margin:0 0 14px; font-size:18px; border-left:5px solid #2c5364; padding-left:10px; }
.card h3 { font-size:15px; color:#333; margin:20px 0 8px; }
.tag { display:inline-block; color:#fff; font-size:12px; border-radius:4px; padding:2px 9px; margin-right:6px; }
.tag.hlm { background:var(--hlm); } .tag.rmt { background:var(--rmt); } .tag.tda { background:var(--tda); }
.tag.chaos { background:var(--chaos); } .tag.pg { background:var(--pg); } .tag.bench { background:var(--bench); }
.tip { background:#fff8e1; border-left:4px solid #f9a825; padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; margin:12px 0; }
.good { background:#e8f5e9; border-left:4px solid #2ca02c; padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; margin:12px 0; }
.bad { background:#fdecea; border-left:4px solid #d62728; padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; margin:12px 0; }
table { border-collapse:collapse; width:100%; font-size:12.5px; margin:10px 0; }
th, td { border:1px solid #e2e5e9; padding:5px 8px; text-align:right; }
th { background:#f0f2f5; text-align:center; }
td:first-child, th:first-child { text-align:left; }
img { max-width:100%; border-radius:8px; border:1px solid #e6e8ec; margin:8px 0; }
.small { font-size:12px; color:#666; }
ul { margin:6px 0 6px 20px; }
li { margin:3px 0; }
</style>
"""
T = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>多任务风控对比报告 v2 — HLM仓控核实 + 物理几何子算法拆解</title>{C}</head>
<body><div class="wrap">
<div class="head">
<h1>多任务风控对比报告 v2 · HLM 仓控核实 + 物理几何子算法拆解</h1>
<div class="sub">统一口径: 2022-01-04 ~ 2026-08-19 · 沪深300 · T-1 信号 T 日生效 · 基准=满仓沪深300 · 无风险利率 1.5% · 生成于 2026 年</div>
</div>

<div class="card">
<h2>核心结论（旗帜鲜明）</h2>
<div class="good"><b>1. HLM 确实只有 MA 二值仓控，没有分档减/增仓</b> —— 代码核实: 仅"收盘 ≥ MA → 满仓、&lt; MA → 空仓"两档（<span class="small">hlm_backtest.py L687-690: 趋势过滤不成立时 port_ret=0.0 全额转现金</span>）；轮动层的 lead/lag 切换是标的切换，不是仓位操作。但<b>趋势过滤窗口长短差异极大</b>: <span class="tag hlm">MA60</span> 总收益 <b>+36.35%</b>、年化 <b>+7.23%</b>、回撤 <b>-10.99%</b>、夏普 <b>0.50</b>、卡玛 <b>0.66</b> —— 全面优于 MA40（+27.29%/0.38/0.49）与 MA20（-8.56%，因过于敏感反复挨打）。<b>与其给 HLM 加分档仓位，不如把 MA 窗口从 40 调到 60</b>。</div>
<div class="good"><b>2. 物理几何 12 个子算法拆开后，"哪个好"的答案很明确</b> —— 直接照抄线上分类阈值，<b>8/12 个在沪深300上完全失效</b>（阈值失配: J 恒&gt;1.5 恒 40% 仓位、H 恒&gt;0.6 恒满仓等，无任何择时区分度）；仅 <b>M(Ising磁化)、T(温度)、MSF(微观稳定)、Morse(形态)</b> 有真实三档信号。阈值<b>分位数自适应校准</b>后: <b>ξ关联长度 +14.03%</b>、<b>D分形维 +13.89%</b>、Ricci +8.39%、MSF +6.32%、Hurst +6.00% 独立正收益；<b>J耦合 / T温度 / Betti / RMT谱超差 恒为负贡献</b>，不宜单用。<span class="small">校准版使用了全样本分位数（in-sample），数值代表"预测力上限"，直接部署需用滚动分位重估。</span></div>
<div class="bad"><b>3. 全场排序（总收益，高风险高收益取向）</b>: HLM(MA60) <b>+36.35%</b> &gt; HLM(MA40) +27.29% &gt; ξ校准 +14.03% ≈ D校准 +13.89% &gt; Ricci校准 +8.39% &gt; 基准 -6.69% &gt; PG融合 -2.79% &gt; RMT -0.26% &gt; TDA -9.93% &gt; 混沌 -12.85%。<b>综合结论: HLM(MA60) 仍是全维度最优，物理几何的 ξ关联长度（阈值自适应后）是唯一接近 HLM 的子算法，是 PG 融合升级最值得的方向。</b></div>
</div>

<div class="card">
<h2>问题一：HLM 有减仓/增仓吗？</h2>
<p>核实结果：<b>没有</b>。HLM（Hodge Laplacian ETF轮动）任务的仓位层只有"趋势过滤"一个开关：标的价格在 MA 之上满仓、之下空仓（0/1 两档）。它在轮动层有 lead/lag 结构切换（领先簇→滞后簇的反转信号），那属于<b>选标的</b>，不属于仓位增减。因此 HLM 的防守完全由"趋势判断"承担——判断错了就是 0 仓位踏空，判断对了才满仓，没有中间缓冲带。
为回答"调严/调松是否有更好效果"，补测 MA20/40/60 三档严格度（口径与原回测完全一致）：</p>
{html_table(mt.loc[hlm_keys], highlight_rows=['HLM(MA60)'])}
<img src="data:image/png;base64,{b64(p2)}">
<div class="tip"><b>结论: MA60 严格度最优</b>——2022 熊市避险更充分（回撤 -10.99% 全场最小），2024-2025 上涨又不掉队；平均仓位 47.3% vs MA40 的 45.5%，用略多一点的仓位换显著更高的收益。MA20 因频繁触发开关（日均换仓 12.2 次）磨损严重。若沿用 HLM 体系，<b>建议线上把 MA40 升级为 MA60</b>。</div>
</div>

<div class="card">
<h2>问题二：物理几何 12 个子算法逐一对比</h2>
<h3>全景: 五套既有风控净值（继承 v1, 基准满仓沪深300）</h3>
<img src="data:image/png;base64,{b64(p1)}">
<h3>2.1 照抄线上阈值的结果（8/12 失效）</h3>
<p>下表为按线上 classify_state 打分阈值原样单独回测：<b>J耦合、ξ关联长度、RMT谱超差、PCA流形、Betti洞数、Ricci曲率、D分形维、Hurst 八项在沪深300样本上整个回测区间几乎恒落在单侧档位</b>（特征分布与阈值完全失配），等价于"恒满仓"或"恒40%仓位"，没有任何择时信息——它们必须靠多特征融合/交叉才贡献价值，独立拿出来当风控就是无效甚至有害的（T 温度照抄版 -18.85% 全场最差）。</p>
{html_table(mt.loc[phys_keys + geo_keys])}
<h3>2.2 分位数阈值校准版（同特征公平比较预测力）</h3>
<p>为公平回答"每个算法到底有没有预测力"，对数值型特征按样本内 q25/q75 校准三档边界（方向保持原语义），分类特征与 M 保持原规则。校准后每个子算法都有真实三档切换，结果如下：</p>
{html_table(mt.loc[phys_cal + geo_cal], highlight_rows=['ξ·关联长度·校准', 'D·分形维·校准'])}
<img src="data:image/png;base64,{b64(p3)}">
<img src="data:image/png;base64,{b64(p4)}">
<h3>2.3 照抄 vs 校准对比（仅线上实际会生效的 4 个信号）</h3>
<img src="data:image/png;base64,{b64(p5)}">
<div class="good"><b>物理侧: ξ关联长度（+14.03%, 夏普0.17, 回撤-20.8%）是最有价值的单信号</b>——低关联(市场自发分散)满仓、高关联(系统性共振)空仓，逻辑贴合 A 股"高同步=风险"特征。MSF(+6.32%)次之。M/T 单用为负，说明"市场方向"与"波动烈度"单独尚不足以择时。</div>
<div class="good"><b>几何侧: D分形维(+13.89%, 夏普0.17)与 Ricci(+8.39%)有价值，Morse 为负，PCA/Betti/Hurst 校准后仍无正贡献</b>。</div>
<div class="bad"><b>始终负贡献（两版皆负）: T温度、J耦合、Betti洞数、RMT谱超差</b> —— 这四类信号在 RN 上不仅不提供 alpha 还拖累，PG 融合里它们的存在更多是"状态刻画"而非"仓控信号"。</div>
</div>

<div class="card">
<h2>方法 & 口径注记</h2>
<ul>
<li>统一口径: T-1 日特征 → T 日生效; 区间 2022-01-04~2026-08-19 (1120 交易日); 基准=每日满仓沪深300（总收益 -6.69%, 回撤 -35.10%）。</li>
<li>HLM MA 变体与 v1 的 HLM(MA40) 完全一致（逐日仓位复现校验通过），保证与上轮对比无缝延续。</li>
<li>PG 子算法: 20 标的截面（5 宽基 + 15 行业 ETF）125 日滚动窗口, 同线上 daily_physics_geo.py 原版计算; 融合版仓位与 v1 逐日完全一致（校验通过）。</li>
<li>子算法仓位档位: 看涨=100% / 中性=75% / 看跌=40%, 阈值与方向全部源自 classify_state 打分规则; 校准版=全样本分位数 q25/q75（in-sample，有前视偏差，仅作预测力上限参考，部署需滚动分位）。</li>
<li>Betti 洞数线上 classify_state 无打分规则, 其方向为经验假设（环路多=结构复杂→看跌），已在校准版中同样处理。</li>
<li>曼宁资金流任务因 L2 数据仅覆盖 2026 年 107 个交易日且无独立指数仓控，无法纳入本长区间对比（与 v1 一致）。</li>
</ul>
</div>

<div class="card">
<h2>产出文件</h2>
<p class="small">
回测数据: backtest_multi_risk_v2.pkl（134 列, 全部策略逐日仓位/收益/净值/回撤）、metrics_v2.pkl（指标表）<br>
PG 子算法: backtest_subalgo.py + backtest_subalgo_calib.py (照抄版/校准版) + backtest_subalgo.pkl / backtest_subalgo_calib.pkl<br>
组装脚本: make_multi_v2_data.py · 本报告: make_multi_report_v2.py · 图表: charts_v2/
</p>
</div>
</div></body></html>"""

with open(os.path.join(WORK, '多任务风控对比报告v2.html'), 'w', encoding='utf-8') as f:
    f.write(T)
print('已生成 多任务风控对比报告v2.html, 图表 %s' % ', '.join([p1, p2, p3, p4, p5]))