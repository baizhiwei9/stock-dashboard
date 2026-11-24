#!/usr/bin/env python3
"""
TDA流形旋转策略 - 持久同调计算可视化
=====================================
生成4张图:
1. 整体流程图: 8因子价格 -> UMAP -> 2D点云 -> VR复形 -> 持久图 -> β₀/β₁/曲率
2. UMAP降维效果: 8D价格矩阵 -> 2D点云散点图
3. Vietoris-Rips复形: 点云 + 不同半径ε下的复形
4. 持久图(Persistence Diagram): birth-death图, 标注β₀/β₁
"""

import sys, os
sys.path.insert(0, '/tmp/.pip-global/lib/python3.12/site-packages')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from matplotlib.collections import PatchCollection
import warnings
warnings.filterwarnings('ignore')

# 字体设置
plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

from tda_core import umap_reduce, compute_persistent_homology, extract_topological_features

# ========== 生成模拟数据 ==========

np.random.seed(42)
n_days = 60
n_factors = 8
factors = ['Tech', 'Mfg', 'Energy', 'Pharma', 'Consumer', 'Value', 'Dividend', 'Momentum']

# 模拟价格 (有趋势+噪声, 含一定环结构)
t = np.linspace(0, 4 * np.pi, n_days)
prices = np.zeros((n_days, n_factors))
for i in range(n_factors):
    trend = np.linspace(1.0, 1.0 + i * 0.05, n_days)
    cycle = 0.03 * np.sin(t + i * 0.5)
    noise = np.random.randn(n_days) * 0.01
    prices[:, i] = trend + cycle + noise

# ========== 图1: 整体流程图 ==========

fig1, ax = plt.subplots(1, 1, figsize=(16, 10))
ax.set_xlim(0, 16)
ax.set_ylim(0, 10)
ax.set_aspect('equal')
ax.axis('off')
ax.set_title('TDA Manifold Rotation - Full Pipeline', fontsize=18, fontweight='bold', pad=20)

# 流程节点
nodes = [
    (1.5, 7.5, 'Step 1\n8 ETF Prices\n(60 days x 8 factors)\nMatrix P in R^(60x8)', '#E8F5E9'),
    (5.0, 7.5, 'Step 2\nUMAP Reduction\nZ-score -> 2D\n60 points in R^2', '#E3F2FD'),
    (8.5, 7.5, 'Step 3\nVietoris-Rips Complex\nBuild simplicial complex\nat radius epsilon', '#FFF3E0'),
    (12.0, 7.5, 'Step 4\nPersistent Homology\n(ripser library)\nBirth-Death pairs', '#F3E5F5'),
    (14.5, 7.5, 'Step 5\nExtract Features\nbeta_0, beta_1\nCurvature', '#FFEBEE'),
    (1.5, 3.0, 'beta_0 = #{infinite bars in H_0}\n= connected components\nTypical: 1 (unified market)', '#E8F5E9'),
    (5.0, 3.0, 'beta_1 = #{bars in H_1}\n= topological loops/holes\nTypical: 2-14', '#E3F2FD'),
    (8.5, 3.0, 'Curvature = min(1, max_pers_1 * 10)\nHigh = clear directional structure\nLow = flat/ambiguous', '#FFF3E0'),
    (12.0, 3.0, 'Vulnerability = f(vol, beta_1, curvature)\nPosition = max(15%, 100% - vuln)\nState: Bull/Bear/Range', '#F3E5F5'),
    (14.5, 3.0, '(s+1)^2 Weights\nDefensive factors (s<0)\nget higher weight\nMomentum (s>0) x0.5', '#FFEBEE'),
]

for x, y, text, color in nodes:
    rect = plt.Rectangle((x-1.3, y-0.9), 2.6, 1.8, facecolor=color, edgecolor='black', linewidth=1.5, zorder=2)
    ax.add_patch(rect)
    ax.text(x, y, text, ha='center', va='center', fontsize=8, fontweight='bold', zorder=3)

# 箭头 (上排)
for x in [2.8, 6.3, 9.8, 13.3]:
    ax.annotate('', xy=(x+0.7, 7.5), xytext=(x, 7.5),
                arrowprops=dict(arrowstyle='->', lw=2, color='#333'))

# 下排箭头
for x in [2.8, 6.3, 9.8, 13.3]:
    ax.annotate('', xy=(x+0.7, 3.0), xytext=(x, 3.0),
                arrowprops=dict(arrowstyle='->', lw=2, color='#333'))

# 连接线 上->下
for x in [1.5, 5.0, 8.5, 12.0, 14.5]:
    ax.plot([x, x], [6.6, 3.9], 'k--', lw=1, alpha=0.3)

# 公式标注
ax.text(8.0, 0.8, 'Key Formulas:  beta_0 = |{(b,inf) in H_0}|    beta_1 = |H_1|    curvature = min(1, max(d-b in H_1) * 10)',
        ha='center', fontsize=10, style='italic', color='#555',
        bbox=dict(boxstyle='round', facecolor='#FFFDE7', edgecolor='#999'))

plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'tda_pipeline.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Figure 1: Pipeline saved")

# ========== 实际计算 ==========

# Z-score标准化
means = prices.mean(axis=0)
stds = prices.std(axis=0)
stds[stds == 0] = 1
normalized = (prices - means) / stds
normalized = np.nan_to_num(normalized)

# UMAP 2D降维
embedding = umap_reduce(prices, n_components=2, n_neighbors=15, min_dist=0.1)

# 持久同调
persistence = compute_persistent_homology(embedding, maxdim=1)
features = extract_topological_features(persistence)

print(f"Features: beta_0={features['beta_0']}, beta_1={features['beta_1']}, curvature={features['curvature']:.4f}")
print(f"  max_pers_0={features['max_persistence_0']:.4f}, max_pers_1={features['max_persistence_1']:.4f}")

# ========== 图2: UMAP降维效果 ==========

fig2, axes = plt.subplots(1, 3, figsize=(18, 5))

# 左: 原始8D价格序列
ax = axes[0]
for i in range(n_factors):
    ax.plot(prices[:, i], label=factors[i], alpha=0.7, linewidth=1.2)
ax.set_title('Step 1: 8 ETF Daily Prices (60 days)', fontsize=12, fontweight='bold')
ax.set_xlabel('Trading Day')
ax.set_ylabel('Price')
ax.legend(fontsize=7, loc='upper left', ncol=2)

# 中: 标准化后
ax = axes[1]
for i in range(n_factors):
    ax.plot(normalized[:, i], label=factors[i], alpha=0.7, linewidth=1.2)
ax.set_title('Step 1b: Z-score Normalized', fontsize=12, fontweight='bold')
ax.set_xlabel('Trading Day')
ax.set_ylabel('Z-score')
ax.legend(fontsize=7, loc='upper left', ncol=2)

# 右: UMAP 2D降维结果
ax = axes[2]
scatter = ax.scatter(embedding[:, 0], embedding[:, 1], c=range(n_days), cmap='viridis', s=50, edgecolors='black', linewidth=0.5)
ax.set_title(f'Step 2: UMAP 2D Embedding\n({n_days} points, each = 1 trading day)', fontsize=12, fontweight='bold')
ax.set_xlabel('UMAP Dimension 1')
ax.set_ylabel('UMAP Dimension 2')
cbar = plt.colorbar(scatter, ax=ax, label='Day Index')
# 标注首尾点
ax.annotate('Day 1', (embedding[0, 0], embedding[0, 1]), fontsize=8, fontweight='bold', color='red')
ax.annotate(f'Day {n_days}', (embedding[-1, 0], embedding[-1, 1]), fontsize=8, fontweight='bold', color='red')

plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'tda_umap.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Figure 2: UMAP saved")

# ========== 图3: Vietoris-Rips复形 ==========

fig3, axes = plt.subplots(1, 3, figsize=(18, 5))

# 计算点对距离矩阵
from scipy.spatial.distance import pdist, squareform
dist_matrix = squareform(pdist(embedding))
max_dist = dist_matrix[dist_matrix < 1e10].max()

# 三个不同的epsilon值
epsilons = [max_dist * 0.15, max_dist * 0.35, max_dist * 0.6]
titles = [
    f'epsilon = {epsilons[0]:.3f} (small)\nDisconnected components',
    f'epsilon = {epsilons[1]:.3f} (medium)\nLoops forming',
    f'epsilon = {epsilons[2]:.3f} (large)\nFully connected'
]

for idx, (ax, eps, title) in enumerate(zip(axes, epsilons, titles)):
    # 画点
    ax.scatter(embedding[:, 0], embedding[:, 1], c='steelblue', s=40, zorder=5, edgecolors='black', linewidth=0.5)
    
    # 画边 (距离 < epsilon)
    for i in range(n_days):
        for j in range(i+1, n_days):
            d = dist_matrix[i, j]
            if d <= eps:
                ax.plot([embedding[i, 0], embedding[j, 0]],
                       [embedding[i, 1], embedding[j, 1]],
                       'b-', alpha=0.3, linewidth=0.8, zorder=2)
    
    # 画半径圆
    for i in range(n_days):
        circle = Circle((embedding[i, 0], embedding[i, 1]), eps/2, 
                       fill=False, edgecolor='red', alpha=0.08, linewidth=0.5)
        ax.add_patch(circle)
    
    # 统计连通分量和环
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse import csr_matrix
    adj = (dist_matrix <= eps).astype(int) - np.eye(n_days)
    adj[adj < 0] = 0
    n_comp, _ = connected_components(csr_matrix(adj))
    
    ax.set_title(f'{title}\nbeta_0 = {n_comp} (components)', fontsize=10, fontweight='bold')
    ax.set_xlabel('UMAP Dim 1')
    ax.set_ylabel('UMAP Dim 2')
    ax.set_aspect('equal')

plt.suptitle('Step 3: Vietoris-Rips Complex at Different Scales', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'tda_vr_complex.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Figure 3: VR Complex saved")

# ========== 图4: 持久图(Persistence Diagram) ==========

fig4, axes = plt.subplots(1, 2, figsize=(16, 7))

# 左: 持久图
ax = axes[0]
dgm_0 = persistence.get(0, [])
dgm_1 = persistence.get(1, [])

# 画对角线
max_val = 0
for b, d in dgm_0 + dgm_1:
    if d < 1e10:
        max_val = max(max_val, d)
max_val = max(max_val, 1.0)

ax.plot([0, max_val * 1.1], [0, max_val * 1.1], 'k--', linewidth=1, alpha=0.5, label='Diagonal')

# 0维持久点 (蓝色)
for b, d in dgm_0:
    if d > 1e10:
        # 无限长条: 画在最上方
        ax.scatter(b, max_val * 1.05, c='blue', s=100, marker='*', zorder=5, edgecolors='black')
        ax.annotate(f'inf (beta_0)', (b, max_val * 1.05), fontsize=9, fontweight='bold', 
                    color='blue', xytext=(5, -15), textcoords='offset points')
    else:
        ax.scatter(b, d, c='blue', s=60, marker='o', zorder=5, edgecolors='black', alpha=0.7)

# 1维持久点 (红色)
for b, d in dgm_1:
    if d > 1e10:
        ax.scatter(b, max_val * 1.05, c='red', s=100, marker='*', zorder=5, edgecolors='black')
    else:
        ax.scatter(b, d, c='red', s=80, marker='s', zorder=5, edgecolors='black', alpha=0.8)

# 标注持久性
for b, d in dgm_1:
    if d < 1e10 and (d - b) > 0.05:
        ax.annotate(f'pers={d-b:.3f}', (b, d), fontsize=7, color='red',
                    xytext=(5, 5), textcoords='offset points')

ax.set_xlabel('Birth (epsilon)', fontsize=12)
ax.set_ylabel('Death (epsilon)', fontsize=12)
ax.set_title(f'Step 4: Persistence Diagram\n'
             f'beta_0 = {features["beta_0"]} (blue circles/stars)\n'
             f'beta_1 = {features["beta_1"]} (red squares)\n'
             f'Curvature = {features["curvature"]:.4f}', 
             fontsize=11, fontweight='bold')
ax.legend(['Diagonal', f'H_0 (dim 0): {len(dgm_0)} features', 
           f'H_1 (dim 1): {len(dgm_1)} features'], fontsize=9, loc='lower right')
ax.set_xlim(-0.05, max_val * 1.15)
ax.set_ylim(-0.05, max_val * 1.15)
ax.set_aspect('equal')
ax.grid(True, alpha=0.3)

# 右: 持久条形图(Barcode)
ax = axes[1]
y_pos = 0
line_height = 0.8

# H_0 条
for i, (b, d) in enumerate(dgm_0):
    if d > 1e10:
        d_plot = max_val * 1.1
        ax.plot([b, d_plot], [y_pos, y_pos], 'b-', linewidth=3)
        ax.plot(d_plot, y_pos, 'b>', markersize=8)
        ax.text(-0.02, y_pos, f'H0#{i}', ha='right', va='center', fontsize=7, color='blue')
    else:
        ax.plot([b, d], [y_pos, y_pos], 'b-', linewidth=3)
    y_pos -= line_height

# 分隔线
ax.axhline(y=y_pos + line_height/2, color='gray', linestyle='--', alpha=0.5)
y_pos -= line_height

# H_1 条
for i, (b, d) in enumerate(dgm_1):
    if d > 1e10:
        d_plot = max_val * 1.1
        ax.plot([b, d_plot], [y_pos, y_pos], 'r-', linewidth=3)
        ax.plot(d_plot, y_pos, 'r>', markersize=8)
    else:
        ax.plot([b, d], [y_pos, y_pos], 'r-', linewidth=3)
        pers = d - b
        if pers > 0.05:
            ax.text(d + 0.01, y_pos, f'pers={pers:.3f}', va='center', fontsize=7, color='red')
    ax.text(-0.02, y_pos, f'H1#{i}', ha='right', va='center', fontsize=7, color='red')
    y_pos -= line_height

ax.set_xlabel('Epsilon (filtration parameter)', fontsize=12)
ax.set_ylabel('Feature Index', fontsize=12)
ax.set_title(f'Step 4b: Persistence Barcode\n'
             f'Blue = H_0 ({len(dgm_0)} bars, {features["beta_0"]} infinite)\n'
             f'Red = H_1 ({len(dgm_1)} bars)\n'
             f'max_pers_1 = {features["max_persistence_1"]:.4f}',
             fontsize=11, fontweight='bold')
ax.set_xlim(-0.05, max_val * 1.2)
ax.grid(True, axis='x', alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'tda_persistence.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Figure 4: Persistence Diagram saved")

# ========== 图5: 脆弱性与仓位公式 ==========

fig5, axes = plt.subplots(1, 3, figsize=(18, 5))

# 左: (s+1)^2 权重曲线
ax = axes[0]
s_range = np.linspace(-2, 3, 200)
raw_w = (s_range + 1)**2
discounted = np.where(s_range > 0, raw_w * 0.5, raw_w)
ax.plot(s_range, raw_w, 'b-', linewidth=2, label='(s+1)^2 raw')
ax.plot(s_range, discounted, 'r--', linewidth=2, label='(s+1)^2 x 0.5 (if s>0)')
ax.axvline(x=0, color='gray', linestyle=':', alpha=0.5)
ax.axvline(x=-1, color='green', linestyle=':', alpha=0.5, label='s=-1 (min weight=0)')
ax.fill_between(s_range[s_range < 0], 0, raw_w[s_range < 0], alpha=0.1, color='blue', label='Defensive zone (s<0)')
ax.fill_between(s_range[s_range > 0], 0, discounted[s_range > 0], alpha=0.1, color='red', label='Momentum zone (s>0)')
ax.set_xlabel('Sharpe Ratio (s)', fontsize=12)
ax.set_ylabel('Raw Weight', fontsize=12)
ax.set_title('Step 5: (s+1)^2 Defensive Weight Formula', fontsize=12, fontweight='bold')
ax.legend(fontsize=8)
ax.set_ylim(0, 8)
ax.grid(True, alpha=0.3)

# 中: 脆弱性 -> 仓位映射
ax = axes[1]
vuln = np.linspace(0, 100, 200)
position = np.maximum(15, 100 - vuln)
ax.plot(vuln, position, 'g-', linewidth=3)
ax.fill_between(vuln, 0, position, alpha=0.1, color='green')
ax.axhline(y=15, color='red', linestyle='--', alpha=0.5, label='Floor: 15%')
ax.set_xlabel('Vulnerability (%)', fontsize=12)
ax.set_ylabel('Position Ratio (%)', fontsize=12)
ax.set_title('Position = max(15%, 100% - Vulnerability)', fontsize=12, fontweight='bold')
ax.legend(fontsize=9)
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.grid(True, alpha=0.3)

# 标注状态区间
for boundary, label, color in [(35, 'Bull', '#4CAF50'), (50, 'Range+', '#FF9800'), 
                                 (70, 'Range-', '#FF5722'), (100, 'Bear', '#F44336')]:
    ax.axvline(x=boundary, color=color, linestyle=':', alpha=0.3)

# 右: 波动率 -> 脆弱性映射
ax = axes[2]
vol = np.linspace(0.05, 0.50, 200)
vol_vuln = np.clip(15 + vol * 150, 20, 65)
ax.plot(vol * 100, vol_vuln, 'b-', linewidth=3, label='Vol component')
ax.fill_between(vol * 100, 20, vol_vuln, alpha=0.1, color='blue')
ax.set_xlabel('Annualized Volatility (%)', fontsize=12)
ax.set_ylabel('Volatility Vulnerability (%)', fontsize=12)
ax.set_title('Vulnerability = f(annualized_vol)\n= clip(15 + vol * 150, 20, 65)', fontsize=12, fontweight='bold')
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'tda_formulas.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Figure 5: Formulas saved")

print("\n=== All 5 figures generated ===")
print(f"  beta_0 = {features['beta_0']}")
print(f"  beta_1 = {features['beta_1']}")
print(f"  curvature = {features['curvature']:.4f}")
print(f"  max_persistence_0 = {features['max_persistence_0']:.4f}")
print(f"  max_persistence_1 = {features['max_persistence_1']:.4f}")
print(f"  avg_persistence_1 = {features['avg_persistence_1']:.4f}")
