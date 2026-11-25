#!/usr/bin/env python3
"""
v3 报告: ξ+D 组合风控 vs SPD黎曼流形漂移风控
输出: charts_v3/*.png + 多任务风控对比报告v3.html
"""
import os, sys, base64, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WORK, 'pylib'))
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False
os.makedirs(os.path.join(WORK, 'charts_v3'), exist_ok=True)

base = pd.read_pickle(os.path.join(WORK, 'backtest_multi_risk_v2.pkl'))
xd = pd.read_pickle(os.path.join(WORK, 'backtest_xiD_spd.pkl'))
mt = pd.read_pickle(os.path.join(WORK, 'metrics_xiD_spd.pkl'))
dates = base.index

def save(fig, name):
    p = os.path.join(WORK, 'charts_v3', name)
    fig.savefig(p, dpi=110, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return p

def b64(p):
    return base64.b64encode(open(p, 'rb').read()).decode()

# Fig1: 净值对比
fig, ax = plt.subplots(figsize=(11, 5.4))
lines = [
    ('xiD_avg_cum', 'ξ+D 组合(等权平均)', '#2ca02c', 2.2),
    ('xiD_min_cum', 'ξ+D 组合(保守min)', '#66bb6a', 1.6),
    ('spd_total_cum', 'SPD 全矩阵漂移仓控', '#d62728', 1.5),
    ('spd_avg_cum', 'SPD 平均Fisher-z漂移仓控', '#ff8a65', 1.3, '--'),
    ('hlm_ma60_cum', 'HLM(MA60)·v2全场最佳参照', '#1f77b4', 1.8),
]
for k, lab, c, lw, *rest in lines:
    src = base if k.startswith(('hlm_', 'bench')) else xd
    ax.plot(dates, src[k].values, label=lab, color=c, lw=lw,
            ls=rest[0] if rest else '-')
ax.plot(dates, base['bench_cum'].values, label='基准·满仓沪深300', color='#7f7f7f', lw=1.2, ls='--')
ax.axhline(1.0, color='#999', ls='--', lw=0.8)
ax.set_title('ξ+D 组合风控 vs SPD黎曼流形漂移风控 (2022-01~2026-08, 沪深300, T-1信号T日生效)')
ax.legend(loc='upper left', fontsize=9.5, ncol=2)
ax.grid(alpha=0.25)
fig.tight_layout()
p1 = save(fig, 'fig1_nav.png')

# Fig2: 仓位时序
fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
for k, lab, c in [('xiD_avg_pos', 'ξ+D 等权', '#2ca02c'), ('xiD_min_pos', 'ξ+D 保守min', '#66bb6a')]:
    axes[0].plot(dates, xd[k].values, label=lab, color=c, lw=0.7)
axes[0].set_ylim(0, 1.2); axes[0].set_title('ξ+D 组合仓位'); axes[0].legend(loc='upper right', fontsize=9)
axes[0].grid(alpha=0.25)
axes[1].plot(dates, xd['spd_total_pos'].values, color='#d62728', lw=0.7, label='SPD total_drift 仓位')
axes[1].plot(dates, xd['spd_avg_pos'].values, color='#ff8a65', lw=0.7, alpha=0.8, label='SPD avg_drift 仓位')
axes[1].set_ylim(0, 1.2); axes[1].set_title('SPD 漂移仓位 (三档: 100/75/40%)'); axes[1].legend(loc='upper right', fontsize=9)
axes[1].grid(alpha=0.25)
fig.tight_layout()
p2 = save(fig, 'fig2_pos.png')

# Fig3: SPD 漂移序列 vs 沪深300走势
fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
axes[0].plot(dates, xd['spd_total_drift'].values, color='#d62728', lw=0.9, label='全矩阵 Log-Euclidean 漂移')
axes[0].set_title('SPD 全矩阵漂移 (22行业相关矩阵测地距离)'); axes[0].legend(fontsize=9); axes[0].grid(alpha=0.25)
axes[1].plot(dates, (1 + base['hs300_ret']).cumprod().values, color='#1f77b4', lw=1.0)
axes[1].set_title('沪深300 净值 (对照)'); axes[1].grid(alpha=0.25)
fig.tight_layout()
p3 = save(fig, 'fig3_drift.png')

def html_table(df, highlight_rows=()):
    cols = ['总收益%', '年化%', '夏普', '最大回撤%', '卡玛', '平均仓位%']
    h = '<table><thead><tr><th>策略</th>' + ''.join(f'<th>{c}</th>' for c in cols) + '</tr></thead><tbody>'
    for idx, r in df.iterrows():
        st = 'background:#e8f5e9;font-weight:bold;border-left:4px solid #2ca02c;' if idx in highlight_rows else ''
        h += f'<tr style="{st}"><td><b>{idx}</b></td>'
        for c in cols:
            v = r[c]
            h += f'<td>{v:.2f}' + ('%</td>' if c in ('总收益%', '年化%', '最大回撤%', '平均仓位%') else '</td>')
        h += '</tr>'
    h += '</tbody></table>'
    return h

C = """
<style>
body { font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; margin:0; background:#f6f7f9; color:#1c1e21; }
.wrap { max-width:1180px; margin:0 auto; padding:28px 20px 60px; }
.head { background:linear-gradient(135deg,#1b4332,#2d6a4f,#40916c); color:#fff; border-radius:14px; padding:26px 30px; margin-bottom:22px; }
.head h1 { margin:0 0 8px; font-size:24px; }
.head .sub { opacity:.85; font-size:13px; }
.card { background:#fff; border-radius:12px; padding:22px 26px; margin:18px 0; box-shadow:0 1px 4px rgba(0,0,0,.07); }
.card h2 { margin:0 0 14px; font-size:18px; border-left:5px solid #2d6a4f; padding-left:10px; }
.card h3 { font-size:15px; color:#333; margin:20px 0 8px; }
.tag { display:inline-block; color:#fff; font-size:12px; border-radius:4px; padding:2px 9px; margin-right:6px; }
.tag.best { background:#2d6a4f; } .tag.pg { background:#9467bd; } .tag.spd { background:#d62728; }
.good { background:#e8f5e9; border-left:4px solid #2ca02c; padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; margin:12px 0; }
.bad { background:#fdecea; border-left:4px solid #d62728; padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; margin:12px 0; }
.tip { background:#fff8e1; border-left:4px solid #f9a825; padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; margin:12px 0; }
table { border-collapse:collapse; width:100%; font-size:13px; margin:10px 0; }
th, td { border:1px solid #e2e5e9; padding:5px 8px; text-align:right; }
th { background:#f0f2f5; text-align:center; }
td:first-child, th:first-child { text-align:left; }
img { max-width:100%; border-radius:8px; border:1px solid #e6e8ec; margin:8px 0; }
.small { font-size:12px; color:#666; }
ul { margin:6px 0 6px 20px; } li { margin:3px 0; }
</style>
"""

T = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>风控对比报告 v3 — ξ+D组合 vs SPD黎曼流形</title>{C}</head>
<body><div class="wrap">
<div class="head">
<h1>风控对比报告 v3 · ξ 关联长度 × D 分形维 组合 vs SPD 黎曼流形</h1>
<div class="sub">统一口径: 2022-01-05 ~ 2026-08-19 · 沪深300 · T-1 信号 T 日生效 · 基准=满仓沪深300 · 延续 v2 全部成果</div>
</div>

<div class="card">
<h2>核心结论（旗帜鲜明）</h2>
<div class="good"><b>1. ξ+D 组合有效，但提升有限</b> — 等权平均 <span class="tag pg">+14.08%</span>（夏普 0.18、回撤 -21.44%），略优于 ξ 单信号（+14.03%）、D 单信号（+13.89%），并把日均换手从 D 的 2.95% 平滑到 2.04%。两个信号互补性不高（同属"结构复杂度"信号），组合主要赢在<b>降噪而非进攻</b>；保守版（min，任一信号看跌即降仓）收益降到 +8.23% 但回撤收窄到 -19.02%，适合求稳。高风险高收益取向 → <b>选等权版</b>。</div>
<div class="bad"><b>2. SPD 漂移当指数仓控不成立</b> — 全矩阵 Log-Euclidean 漂移 <span class="tag spd">-9.74%</span>、平均 Fisher-z 漂移 -10.12%，均为负收益、回撤 -28%~-30%，跑输基准。原因：SPD 漂移在 v5 任务中的角色是<b>"行业选择"</b>（漂移大的行业仓重下注），而非"指数仓控"；把它整体搬到沪深300仓位上，2022-2023 板块轮动剧烈期漂移持续偏高→误判风险、长期低配，反而两头挨打。</div>
<div class="good"><b>3. 总排序（总收益）</b>: HLM(MA60) <b>+36.35%</b> ≫ ξ+D等权 +14.08% &gt; ξ +14.03% ≈ D +13.89% &gt; ξ+D保守 +8.23% &gt; 基准 -6.69% &gt; SPD全矩阵 -9.74% ≈ SPD平均 -10.12%。<b>ξ+D 组合在物理几何体系内是最佳仓控之一，但仍远不及 HLM(MA60)；SPD 流形本身不适合做沪深300指数仓控</b>（它的仓位职责在 TDA 状态控仓，v2 已实测 -9.93%）。</div>
</div>

<div class="card">
<h2>组合构造 & 对比口径</h2>
<ul>
<li><b>ξ 关联长度</b>（v2 校准版）: 20标的截面相关矩阵特征值间隙 ξ=1/√Δλ, 低关联→满仓100%, 高关联→40%（阈值=q25/q75 分位校准）; 单信号 +14.03%。</li>
<li><b>D 分形维</b>（v2 校准版）: Higuchi 分形维, 低乱度→满仓, 高乱度→40%; 单信号 +13.89%。</li>
<li><b>ξ+D 等权</b>: pos = 0.5×ξ仓 + 0.5×D仓（可取连续中间值）; <b>ξ+D 保守</b>: pos = min(ξ仓, D仓)（一票否决制）。</li>
<li><b>SPD 漂移</b>（忠实复刻线上 spd_core.py）: 22 申万行业 60 日收益率窗口, 当前 vs 60 日前参考窗口; total_drift=两相关矩阵 Log-Euclidean 测地距离 ‖logm(R_t)-logm(R_t-ref)‖_F; avg_drift=各行业与等权市场的 Fisher-z 测地漂移均值。均为"漂移高=风险高"→三档仓位（100/75/40%, q25/q75 分位校准, 与子算法同口径）。<span class="small">校准阈值 in-sample（全样本分位数），存在前视，数值代表预测力上限。</span></li>
</ul>
<img src="data:image/png;base64,{b64(p1)}">
<img src="data:image/png;base64,{b64(p2)}">
</div>

<div class="card">
<h2>指标对比表</h2>
{html_table(mt, highlight_rows=('ξ+D 等权平均', 'HLM(MA60)·v2最优'))}
<div class="tip">口径同 v2: T-1 特征→T 日生效; 区间 1120 交易日; 无风险利率 1.5%; SPD 前 120 日窗口（数据不足期）按中性 75% 计（共 8 天）。ξ+D 组合直接继承 v2 校准版仓位序列, 与 v2 报告完全可并列。</div>
</div>

<div class="card">
<h2>SPD 漂移信号与市场走势对照</h2>
<img src="data:image/png;base64,{b64(p3)}">
<div class="tip"><b>可读的结论</b>: SPD 全矩阵漂移的高点集中在 2022 年初/2023 中枢与 2024 初（板块结构剧变期），其减仓动作本身不算错；<b>问题在于它在结构剧烈轮动但指数上涨的时段（如 2024-02 之后）也持续低仓，踏空整个上行段</b>——漂移是高敏感的结构指标，做仓控比做"行业轮动触发"（其本业）噪声大一档。</div>
</div>

<div class="card">
<h2>产出文件</h2>
<p class="small">
回测数据: backtest_xiD_spd.pkl（组合/SPD 逐日仓位·收益·净值·漂移序列）、metrics_xiD_spd.pkl（指标表）<br>
回测脚本: backtest_xiD_spd.py · 本报告: make_multi_report_v3.py · 图表: charts_v3/<br>
复用: backtest_subalgo_calib.pkl（ξ/D 校准仓位）、backtest_multi_risk_v2.pkl（参照策略）
</p>
</div>
</div></body></html>"""

with open(os.path.join(WORK, '多任务风控对比报告v3.html'), 'w', encoding='utf-8') as f:
    f.write(T)
print('已生成 多任务风控对比报告v3.html:', p1, p2, p3)