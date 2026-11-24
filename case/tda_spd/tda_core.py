#!/usr/bin/env python3
"""
TDA流形旋转策略 - 拓扑数据分析核心
====================================
1. UMAP 2D降维 (8因子60日价格 -> 60个2D点)
2. Vietoris-Rips 持久同调 (ripser)
3. 提取 β₀(连通分量), β₁(结构洞), 曲率
4. 市场状态判定 + 脆弱性计算 + 建议仓位
"""

import numpy as np
from scipy import stats
from ripser import ripser
import umap

# ========== UMAP 降维 ==========

def umap_reduce(price_matrix, n_components=2, n_neighbors=15, min_dist=0.1, random_state=42):
    """
    UMAP降维
    
    参数:
        price_matrix: shape (n_days, n_factors) 的价格矩阵
        n_components: 目标维度
        n_neighbors: UMAP局部邻域大小
        min_dist: UMAP最小点间距
    
    返回:
        embedding: shape (n_days, n_components) 的降维结果
    """
    # Z-score标准化
    means = price_matrix.mean(axis=0)
    stds = price_matrix.std(axis=0)
    stds[stds == 0] = 1  # 避免除零
    normalized = (price_matrix - means) / stds
    
    # 处理NaN
    normalized = np.nan_to_num(normalized, nan=0.0)
    
    # UMAP降维
    # 数据量较小时降低n_neighbors
    n_samples = normalized.shape[0]
    effective_neighbors = min(n_neighbors, max(2, n_samples - 1))
    
    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=effective_neighbors,
        min_dist=min_dist,
        random_state=random_state,
        metric='euclidean'
    )
    
    embedding = reducer.fit_transform(normalized)
    return embedding

# ========== 持久同调 ==========

def compute_persistent_homology(point_cloud, maxdim=1):
    """
    计算Vietoris-Rips持久同调
    
    参数:
        point_cloud: shape (n_points, n_dims) 的点云
        maxdim: 最大同调维度 (0=β₀, 1=β₁)
    
    返回:
        persistence_dict: {0: [(birth, death), ...], 1: [(birth, death), ...]}
    """
    result = ripser(point_cloud, maxdim=maxdim)
    dgms = result['dgms']
    
    persistence = {}
    for dim in range(maxdim + 1):
        if dim < len(dgms):
            persistence[dim] = [(float(b), float(d)) for b, d in dgms[dim]]
        else:
            persistence[dim] = []
    
    return persistence

def extract_topological_features(persistence):
    """
    从持久同调图中提取特征
    
    返回:
        beta_0: 连通分量数 (持久0维条中无限长的数量)
        beta_0_finite: 有限持续0维条数量 (临时分裂的分量)
        beta_1: 结构洞数 (1维条数量)
        curvature: 流形曲率 (基于持久图的分布特征)
        max_persistence_0: 0维最大持久性
        max_persistence_1: 1维最大持久性
        avg_persistence_1: 1维平均持久性
    """
    # 0维持久图
    dgm_0 = persistence.get(0, [])
    # β₀ = 无限长条的数量 (最终连通分量)
    beta_0 = sum(1 for b, d in dgm_0 if d == np.inf or d > 1e10)
    # 有限持续0维条
    beta_0_finite = sum(1 for b, d in dgm_0 if d < 1e10)
    
    # 1维持久图
    dgm_1 = persistence.get(1, [])
    beta_1 = len(dgm_1)
    
    # 1维持久性统计
    persistences_1 = [d - b for b, d in dgm_1 if d < 1e10]
    max_persistence_1 = max(persistences_1) if persistences_1 else 0.0
    avg_persistence_1 = np.mean(persistences_1) if persistences_1 else 0.0
    
    # 0维最大持久性
    persistences_0 = [d - b for b, d in dgm_0 if d < 1e10]
    max_persistence_0 = max(persistences_0) if persistences_0 else 0.0
    
    # 流形曲率: 基于1维持久图的分布特征
    # 曲率高 = 存在显著的结构洞(环)，说明有清晰的方向性结构
    # 曲率低 = 结构平坦，方向不明
    if beta_1 > 0 and max_persistence_1 > 0:
        # 归一化曲率: 1维持久性的强度
        curvature = min(1.0, max_persistence_1 * 10)  # 缩放因子
    else:
        curvature = 0.0
    return {
        'beta_0': beta_0,
        'beta_0_finite': beta_0_finite,
        'beta_1': beta_1,
        'curvature': curvature,
        'max_persistence_0': max_persistence_0,
        'max_persistence_1': max_persistence_1,
        'avg_persistence_1': avg_persistence_1,
        'total_0_features': len(dgm_0),
        'total_1_features': len(dgm_1),
    }

# ========== 市场状态判定 ==========

def judge_market_state(features, price_window=None):
    """
    基于拓扑特征+市场波动率判断市场状态
    
    波动率锚定脆弱性:
    - 主分量: 20日年化波动率 -> 20-60%脆弱性
    - TDA修正: β₁显著结构洞 -> 0-30%附加脆弱性
    - 曲率微调: 方向性结构 -> ±10%调整
    
    状态:
    - 强势上涨: 低波动、低β₁、高曲率
    - 震荡偏强: 中波动、结构稳定
    - 震荡偏弱: 中高波动、结构模糊
    - 弱势下跌: 高波动、高β₁、低曲率
    """
    beta_0 = features['beta_0']
    beta_1 = features['beta_1']
    curvature = features['curvature']
    avg_persistence_1 = features.get('avg_persistence_1', 0)
    
    # ===== 主分量: 波动率锚定 =====
    vol_vuln = 30.0  # 默认中等脆弱性
    if price_window is not None:
        try:
            # 只剔除全空行(how='all'): 单个因子的缺口不应把该日从全部因子中删除
            returns = price_window.pct_change().dropna(how='all')
            if len(returns) >= 20:
                recent_vol = returns.tail(20).std().mean()
                annualized_vol = recent_vol * np.sqrt(252)
                # 映射: 10%年化波动 -> 25%脆弱性, 25% -> 50%, 40% -> 60%
                vol_vuln = np.clip(15 + annualized_vol * 150, 20, 65)
        except:
            pass
    
    # ===== TDA修正: 结构洞 =====
    # 用持久性区分真实结构洞vs噪声
    if avg_persistence_1 > 0.1:  # 显著结构洞
        tda_vuln = min(25, beta_1 * 3)
    else:  # 噪声主导
        tda_vuln = min(10, beta_1 * 1.5)
    
    # ===== 曲率微调 =====
    if curvature > 0.7:  # 清晰方向性结构
        curv_adjust = -8
    elif curvature < 0.3:  # 方向不明
        curv_adjust = 8
    else:
        curv_adjust = 0
    
    vulnerability = np.clip(vol_vuln + tda_vuln + curv_adjust, 10, 90)
    
    # ===== 市场状态判定 =====
    # 综合波动率和TDA特征
    if vulnerability < 35 and curvature > 0.5:
        state = '强势上涨'
    elif vulnerability < 50:
        state = '震荡偏强'
    elif vulnerability < 70:
        state = '震荡偏弱'
    else:
        state = '弱势下跌'
    
    # 建议仓位: 脆弱性越高仓位越低
    position_ratio = max(0.15, 1.0 - vulnerability / 100.0)
    
    return {
        'state': state,
        'vulnerability': round(vulnerability, 1),
        'position_ratio': round(position_ratio, 4),
        'position_pct': round(position_ratio * 100, 1),
    }

def beta_0_finite_score(features):
    """计算β₀有限分量对脆弱性的贡献"""
    finite = features.get('beta_0_finite', 0)
    # 0-2个有限分量: 低风险 (0-20)
    # 3-5个: 中风险 (20-50)
    # 6+: 高风险 (50-100)
    if finite <= 2:
        return finite * 10
    elif finite <= 5:
        return 20 + (finite - 2) * 10
    else:
        return min(100, 50 + (finite - 5) * 15)

# ========== 完整TDA分析 ==========

def run_tda_analysis(price_window):
    """
    对一个价格窗口运行完整TDA分析
    
    参数:
        price_window: DataFrame, shape (60, 8), 60日8因子收盘价
    
    返回:
        dict: 包含拓扑特征、市场状态、脆弱性、建议仓位
    """
    # 转为numpy数组 (n_days, n_factors)
    price_array = price_window.values.astype(float)
    
    # UMAP降维到2D
    embedding = umap_reduce(price_array, n_components=2)
    
    # 持久同调
    persistence = compute_persistent_homology(embedding, maxdim=1)
    
    # 提取特征
    features = extract_topological_features(persistence)
    
    # 市场状态判定 (传入价格窗口用于波动率计算)
    market = judge_market_state(features, price_window)
    
    return {
        'embedding': embedding,
        'persistence': persistence,
        'features': features,
        'market': market,
    }

def run_tda_analysis_safe(price_window):
    """
    安全版TDA分析 (带异常处理)
    如果UMAP或ripser失败，返回默认值
    """
    try:
        return run_tda_analysis(price_window)
    except Exception as e:
        print(f"TDA分析异常: {e}, 返回默认值")
        features = {
            'beta_0': 1,
            'beta_0_finite': 0,
            'beta_1': 0,
            'curvature': 0.5,
            'max_persistence_0': 0,
            'max_persistence_1': 0,
            'avg_persistence_1': 0,
        }
        market = judge_market_state(features, price_window)
        return {
            'embedding': None,
            'persistence': {},
            'features': features,
            'market': market,
        }

# ========== 因子夏普比计算 ==========

def compute_factor_sharpe(prices, window=60, annualization=252):
    """
    计算各因子的夏普比
    
    参数:
        prices: DataFrame, 各因子的价格序列
        window: 回看窗口
        annualization: 年化因子 (252交易日)
    
    返回:
        dict: {因子名: sharpe_ratio}
    """
    # 注意: 不能对整表 dropna(默认按行), 否则任一因子缺失(全 NaN 列)会清空所有行,
    # 导致全部因子夏普退化为 0。缺失因子在下方按列 dropna 单独处理。
    returns = prices.pct_change()
    
    # 取最近window期(按行取窗口, 各列再单独去 NaN)
    if len(returns) > window:
        returns = returns.tail(window)
    
    sharpe = {}
    for col in returns.columns:
        r = returns[col].dropna()
        if len(r) < 10:
            sharpe[col] = 0.0
            continue
        
        mean_r = r.mean()
        std_r = r.std()
        
        if std_r == 0:
            sharpe[col] = 0.0
        else:
            # 日夏普比 * sqrt(年化因子) = 年化夏普比
            # 但文档中的s是"60日夏普比"，这里用日夏普比
            sharpe[col] = round(mean_r / std_r * np.sqrt(annualization), 4)
    
    return sharpe

# ========== 防御权重分配 ==========

def compute_defensive_weights(sharpe_dict, momentum_discount=0.5):
    """
    (s+1)² 防御权重分配
    
    参数:
        sharpe_dict: {因子名: 夏普比}
        momentum_discount: 正夏普(动量)因子的折扣系数
    
    返回:
        dict: {因子名: 权重(0-1)}, 按权重降序排列
    """
    raw_weights = {}
    
    for factor, s in sharpe_dict.items():
        # (s+1)² 原始权重
        raw_w = (s + 1) ** 2
        
        # 正夏普(动量因子)打折
        if s > 0:
            raw_w = raw_w * momentum_discount
        
        raw_weights[factor] = max(0, raw_w)  # 确保非负
    
    # 归一化
    total = sum(raw_weights.values())
    if total == 0:
        # 全部为零时等权
        n = len(raw_weights)
        weights = {k: 1.0 / n for k in raw_weights}
    else:
        weights = {k: v / total for k, v in raw_weights.items()}
    
    # 按权重降序排列
    weights = dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))
    
    # 四舍五入
    weights = {k: round(v, 4) for k, v in weights.items()}
    
    return weights

# ========== 流形旋转: 有符号曲率 + 5状态分类 + 偏好矩阵 ==========

def compute_signed_curvature(features, price_window):
    """
    计算有符号曲率: 用价格趋势给TDA曲率加方向
    
    曲率绝对值来自持久同调(结构洞强度)
    符号来自价格趋势: 20日短期趋势为主, 60日长期趋势为确认
    
    修复: 原版用60日均值判断方向, 市场转折时严重滞后(3月暴跌仍判牛市/4月反弹仍判熊市)
    改用20日短期趋势为主信号, 60日为确认:
    - 20日涨 + 60日涨 → 正曲率(牛市确认)
    - 20日跌 + 60日跌 → 负曲率(熊市确认)
    - 20日跌 + 60日涨 → 负曲率但幅度减半(牛市回调, 快速反应)
    - 20日涨 + 60日跌 → 正曲率但幅度减半(熊市反弹, 谨慎)
    """
    raw_curvature = features.get('curvature', 0.0)
    
    if price_window is None or len(price_window) < 20:
        return raw_curvature  # 无法判断方向, 保持正
    
    try:
        # 只剔除全空行(how='all'): 单个因子的缺口不应把该日从全部因子中删除
        returns = price_window.pct_change().dropna(how='all')
        if len(returns) < 10:
            return raw_curvature
        
        # 20日短期趋势(主信号)
        recent_20 = returns.tail(20) if len(returns) >= 20 else returns
        short_trend = recent_20.mean().mean()
        
        # 60日长期趋势(确认)
        long_trend = returns.mean().mean()
        
        if short_trend > 0 and long_trend > 0:
            # 牛市确认
            return raw_curvature
        elif short_trend < 0 and long_trend < 0:
            # 熊市确认
            return -raw_curvature
        elif short_trend < 0 and long_trend > 0:
            # 牛市回调: 快速转负但幅度减半(及时防御, 不过度悲观)
            return -raw_curvature * 0.5
        else:
            # 熊市反弹: 转正但幅度减半(谨慎参与)
            return raw_curvature * 0.5
    except:
        return raw_curvature

def classify_market_state_v2(features, price_window):
    """
    流形旋转 5状态分类 (曲率 × β₁ 二维表)
    
    |              | β₁ < 0.5 (一体化) | β₁ > 1.5 (碎片化) |
    |--------------|-------------------|-------------------|
    | 曲率 > 0.5   | 牛市-一体化        | 牛市-碎片化        |
    | 曲率 < -0.5  | 熊市-一体化        | 熊市-碎片化        |
    | |曲率| <= 0.5| 震荡市             | 震荡市             |
    
    返回:
        state: 5种状态之一
        signed_curvature: 有符号曲率
        beta_1: 结构洞数
        rotation: 旋转方向描述
    """
    beta_1 = features.get('beta_1', 0)
    signed_curv = compute_signed_curvature(features, price_window)
    
    # 判断一体化 vs 碎片化
    fragmented = beta_1 > 1.5
    integrated = beta_1 < 0.5
    
    # 判断牛 vs 熊 vs 震荡
    if signed_curv > 0.5:
        direction = 'bull'
    elif signed_curv < -0.5:
        direction = 'bear'
    else:
        direction = 'oscillation'
    
    # 5状态映射
    if direction == 'bull' and integrated:
        state = 'bull_integrated'
        rotation = '成长主导(一体化上行)'
    elif direction == 'bull' and fragmented:
        state = 'bull_fragmented'
        rotation = '板块轮动(碎片化上行)'
    elif direction == 'bear' and integrated:
        state = 'bear_integrated'
        rotation = '防御为主(一体化下行)'
    elif direction == 'bear' and fragmented:
        state = 'bear_fragmented'
        rotation = '避险优先(碎片化下行)'
    else:
        state = 'oscillation'
        rotation = '均衡配置(中性震荡)'
    
    return {
        'state': state,
        'state_cn': rotation,
        'signed_curvature': round(signed_curv, 4),
        'beta_1': beta_1,
        'direction': direction,
        'fragmented': fragmented,
    }

# ========== 22因子偏好系数矩阵 ==========

# 每种市场状态下22个因子的偏好系数
# 设计逻辑:
#   bull_integrated: 成长/动量/科技高偏好, 防御/红利低偏好
#   bull_fragmented: 动量/周期/资源高偏好(轮动快), 纯防御低
#   bear_integrated: 防御/红利/低波/质量高偏好, 成长/动量极低
#   bear_fragmented: 极端防御, 几乎只配低波/红利/大盘蓝筹
#   oscillation: 全部=1.0 (中性, 纯靠(s+1)²)

PREFERENCE_MATRIX = {
    'bull_integrated': {
        # 成长类: 高偏好
        '科技成长': 2.0, '数字经济': 2.0, '成长因子': 1.8, '高端制造': 1.6,
        '新能源': 1.5, '小盘成长': 1.5, '动量': 1.8, '国防军工': 1.3,
        # 中性
        '医药健康': 1.0, '大消费': 0.9, '质量因子': 1.0, '传媒': 1.1,
        # 周期
        '资源材料': 1.0, '价值周期': 1.0, '金融地产': 0.9,
        # 防御类: 低偏好
        '低波防御': 0.3, '红利高息': 0.4, '大盘蓝筹': 0.5,
        '流动性因子': 0.4, '反转': 0.3, '价值因子': 0.7,
        '农业养殖': 0.6, '绿色低碳': 0.8,
    },
    'bull_fragmented': {
        # 轮动快: 动量/周期/资源高偏好
        '动量': 1.8, '资源材料': 1.5, '价值周期': 1.4, '科技成长': 1.3,
        '高端制造': 1.3, '新能源': 1.2, '数字经济': 1.2, '成长因子': 1.2,
        # 中性
        '金融地产': 1.0, '大消费': 0.9, '医药健康': 0.9, '国防军工': 1.1,
        '小盘成长': 1.1, '质量因子': 0.9, '农业养殖': 0.8, '绿色低碳': 0.9,
        # 防御偏低但不极端
        '低波防御': 0.6, '红利高息': 0.7, '大盘蓝筹': 0.7,
        '流动性因子': 0.5, '反转': 0.6, '价值因子': 0.8, '传媒': 1.0,
    },
    'bear_integrated': {
        # 防御类: 高偏好
        '低波防御': 2.0, '红利高息': 2.0, '大盘蓝筹': 1.8, '质量因子': 1.5,
        '大消费': 1.4, '医药健康': 1.3, '流动性因子': 1.3,
        # 中性偏低
        '金融地产': 0.8, '农业养殖': 1.0, '价值因子': 1.0, '绿色低碳': 0.8,
        # 成长类: 极低偏好
        '科技成长': 0.2, '数字经济': 0.2, '新能源': 0.2, '小盘成长': 0.2,
        '动量': 0.3, '成长因子': 0.3, '高端制造': 0.4, '国防军工': 0.4,
        '资源材料': 0.5, '价值周期': 0.5, '反转': 0.6, '传媒': 0.4,
    },
    'bear_fragmented': {
        # 极端防御
        '低波防御': 2.5, '红利高息': 2.2, '大盘蓝筹': 2.0, '质量因子': 1.8,
        '大消费': 1.5, '流动性因子': 1.5, '医药健康': 1.2,
        # 其他: 极低
        '金融地产': 0.5, '农业养殖': 0.7, '价值因子': 0.6, '绿色低碳': 0.5,
        '科技成长': 0.1, '数字经济': 0.1, '新能源': 0.1, '小盘成长': 0.1,
        '动量': 0.15, '成长因子': 0.15, '高端制造': 0.2, '国防军工': 0.2,
        '资源材料': 0.3, '价值周期': 0.3, '反转': 0.4, '传媒': 0.2,
    },
    'oscillation': {
        # 全部中性, 纯靠(s+1)²决定
        '科技成长': 1.0, '高端制造': 1.0, '新能源': 1.0, '医药健康': 1.0,
        '大消费': 1.0, '金融地产': 1.0, '资源材料': 1.0, '数字经济': 1.0,
        '国防军工': 1.0, '绿色低碳': 1.0, '农业养殖': 1.0, '低波防御': 1.0,
        '红利高息': 1.0, '价值周期': 1.0, '动量': 1.0, '反转': 1.0,
        '小盘成长': 1.0, '大盘蓝筹': 1.0, '质量因子': 1.0, '成长因子': 1.0,
        '价值因子': 1.0, '流动性因子': 1.0,
    },
}

def compute_rotation_weights(sharpe_dict, market_state, momentum_discount=0.5):
    """
    流形旋转权重: preference_i × (s_i+1)²
    
    参数:
        sharpe_dict: {因子名: 夏普比}
        market_state: 5种市场状态之一
        momentum_discount: 正夏普(动量)因子的折扣系数
    
    返回:
        dict: {因子名: 权重(0-1)}, 按权重降序排列
    """
    # 获取当前市场状态的偏好系数
    preferences = PREFERENCE_MATRIX.get(market_state, PREFERENCE_MATRIX['oscillation'])
    
    raw_weights = {}
    
    for factor, s in sharpe_dict.items():
        # 偏好系数 (默认1.0如果因子不在矩阵中)
        pref = preferences.get(factor, 1.0)
        
        # (s+1)² 基础权重
        base_w = (s + 1) ** 2
        
        # 正夏普(动量因子)打折
        if s > 0:
            base_w = base_w * momentum_discount
        
        # 流形旋转: 偏好系数 × 基础权重
        raw_w = pref * base_w
        
        raw_weights[factor] = max(0, raw_w)
    
    # 归一化
    total = sum(raw_weights.values())
    if total == 0:
        n = len(raw_weights)
        weights = {k: 1.0 / n for k in raw_weights}
    else:
        weights = {k: v / total for k, v in raw_weights.items()}
    
    # 按权重降序排列
    weights = dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))
    weights = {k: round(v, 4) for k, v in weights.items()}
    
    return weights

# ========== 因子投影权重 (UMAP投影×动量联合) ==========

def compute_factor_projection_scores(price_window):
    """
    将22个因子投影到2D流形空间, 计算每个因子的投影得分
    
    流程:
    1. 转置价格矩阵: (n_days, n_factors) → (n_factors, n_days)
       每个因子变成一个60维向量(60日价格序列)
    2. UMAP降到2D: 每个因子得到一个2D坐标
    3. 计算市场主方向: 所有因子2D坐标的PCA第一主成分方向
    4. 投影得分: 每个因子在主方向上的投影值, 归一化到[0, 1]
    
    投影得分高 = 因子与市场主方向对齐(强势因子)
    投影得分低 = 因子偏离主方向(弱势/反向因子)
    
    参数:
        price_window: DataFrame, shape (n_days, n_factors), 各因子收盘价
    
    返回:
        dict: {因子名: 投影得分(0-1)}
    """
    factor_names = price_window.columns.tolist()
    n_factors = len(factor_names)
    
    if n_factors < 3:
        return {f: 0.5 for f in factor_names}
    
    try:
        # Step 1: 转置 → (n_factors, n_days), 每行是一个因子的60日价格
        price_matrix = price_window.values.T  # (n_factors, n_days)
        
        # Z-score标准化(按因子)
        means = price_matrix.mean(axis=1, keepdims=True)
        stds = price_matrix.std(axis=1, keepdims=True)
        stds[stds == 0] = 1
        normalized = (price_matrix - means) / stds
        normalized = np.nan_to_num(normalized, nan=0.0)
        
        # Step 2: UMAP降到2D
        effective_neighbors = min(15, max(2, n_factors - 1))
        reducer = umap.UMAP(
            n_components=2,
            n_neighbors=effective_neighbors,
            min_dist=0.1,
            random_state=42,
            metric='euclidean'
        )
        embedding = reducer.fit_transform(normalized)  # (n_factors, 2)
        
        # Step 3: 计算市场主方向(PCA第一主成分)
        centered = embedding - embedding.mean(axis=0)
        # SVD分解, 第一主成分就是最大方差方向
        U, S, Vt = np.linalg.svd(centered, full_matrices=False)
        main_direction = Vt[0]  # 第一主成分方向向量 (2,)
        
        # Step 4: 投影得分 = 因子在主方向上的投影值
        projections = centered @ main_direction  # (n_factors,)
        
        # 用20日近期收益率给投影方向定符号
        # (确保投影方向跟"近期涨得好"的方向一致)
        recent_returns = price_window.pct_change().tail(20).mean()
        avg_ret = recent_returns.values
        # 如果主方向跟收益率负相关, 翻转方向
        if np.corrcoef(projections, avg_ret)[0, 1] < 0:
            projections = -projections
        
        # 归一化到[0, 1] (用min-max)
        p_min, p_max = projections.min(), projections.max()
        if p_max - p_min > 1e-10:
            scores = (projections - p_min) / (p_max - p_min)
        else:
            scores = np.ones(n_factors) * 0.5
        
        # 用指数变换增强区分度: score^0.5 (拉近中间值, 拉开极端值)
        scores = scores ** 0.5
        
        return {factor_names[i]: round(float(scores[i]), 4) for i in range(n_factors)}
        
    except Exception as e:
        print(f"  因子投影计算异常: {e}, 返回中性值")
        return {f: 0.5 for f in factor_names}


def compute_rotation_weights_v2(sharpe_dict, market_state, price_window=None,
                                 momentum_discount=0.5, alpha=0.4):
    """
    流形旋转权重v2: 动量 + 投影 联合
    
    权重 = 偏好系数 × [(1-α) × 动量权重 + α × 投影权重]
    
    其中:
    - 动量权重 = (s+1)², 正夏普打折
    - 投影权重 = (projection_score × 2)², 投影得分越高权重越大
    - α = 投影权重占比 (默认0.4, 即动量60%+投影40%)
    
    参数:
        sharpe_dict: {因子名: 夏普比}
        market_state: 5种市场状态之一
        price_window: 价格窗口(用于计算投影)
        momentum_discount: 正夏普折扣
        alpha: 投影权重占比(0-1)
    
    返回:
        dict: {因子名: 权重(0-1)}, 按权重降序排列
    """
    preferences = PREFERENCE_MATRIX.get(market_state, PREFERENCE_MATRIX['oscillation'])
    
    # 计算投影得分
    if price_window is not None:
        proj_scores = compute_factor_projection_scores(price_window)
    else:
        proj_scores = {f: 0.5 for f in sharpe_dict}
    
    raw_weights = {}
    
    for factor, s in sharpe_dict.items():
        pref = preferences.get(factor, 1.0)
        
        # 动量权重
        momentum_w = (s + 1) ** 2
        if s > 0:
            momentum_w = momentum_w * momentum_discount
        momentum_w = max(0, momentum_w)
        
        # 投影权重: (score × 2)², score∈[0,1] → weight∈[0, 4]
        proj_s = proj_scores.get(factor, 0.5)
        projection_w = (proj_s * 2) ** 2
        
        # 联合: (1-α) × 动量 + α × 投影
        combined_w = (1 - alpha) * momentum_w + alpha * projection_w
        
        # 偏好系数
        raw_w = pref * combined_w
        raw_weights[factor] = max(0, raw_w)
    
    # 归一化
    total = sum(raw_weights.values())
    if total == 0:
        n = len(raw_weights)
        weights = {k: 1.0 / n for k in raw_weights}
    else:
        weights = {k: v / total for k, v in raw_weights.items()}
    
    weights = dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))
    weights = {k: round(v, 4) for k, v in weights.items()}
    
    return weights


if __name__ == '__main__':
    import pandas as pd
    
    print("=== TDA核心模块测试 ===")
    
    # 生成模拟数据
    np.random.seed(42)
    n_days = 60
    n_factors = 8
    dates = pd.date_range('2026-06-01', periods=n_days, freq='B')
    factors = ['科技成长', '高端制造', '新能源', '医药健康', '大消费', '价值周期', '红利低波', '动量']
    
    # 模拟价格 (有趋势+噪声)
    base = np.cumsum(np.random.randn(n_days, n_factors) * 0.01 + 0.001, axis=0) + 1.0
    price_df = pd.DataFrame(base, index=dates, columns=factors)
    
    print(f"价格矩阵: {price_df.shape}")
    
    # TDA分析
    print("\n=== TDA分析 ===")
    result = run_tda_analysis_safe(price_df)
    
    print(f"\n拓扑特征:")
    for k, v in result['features'].items():
        print(f"  {k}: {v}")
    
    print(f"\n市场状态:")
    for k, v in result['market'].items():
        print(f"  {k}: {v}")
    
    # 因子夏普比
    print("\n=== 因子夏普比 ===")
    sharpe = compute_factor_sharpe(price_df, window=60)
    for k, v in sharpe.items():
        print(f"  {k}: {v}")
    
    # v1 防御权重 (纯动量)
    print("\n=== v1 防御权重 (纯动量) ===")
    weights_v1 = compute_defensive_weights(sharpe)
    for k, v in weights_v1.items():
        print(f"  {k}: {v*100:.1f}%")
    
    # v2 旋转权重 (动量+投影)
    print("\n=== v2 旋转权重 (动量60% + 投影40%) ===")
    weights_v2 = compute_rotation_weights_v2(
        sharpe, result['market']['state'], price_window=price_df, alpha=0.4
    )
    for k, v in weights_v2.items():
        print(f"  {k}: {v*100:.1f}%")
    
    # 投影得分
    print("\n=== 因子投影得分 ===")
    proj_scores = compute_factor_projection_scores(price_df)
    for k, v in proj_scores.items():
        print(f"  {k}: {v:.4f}")
    
    print(f"\nv1权重总和: {sum(weights_v1.values()):.4f}")
    print(f"v2权重总和: {sum(weights_v2.values()):.4f}")
    print(f"\n权重变化(v2 vs v1):")
    for f in factors:
        v1 = weights_v1.get(f, 0)
        v2 = weights_v2.get(f, 0)
        delta = v2 - v1
        arrow = "↑" if delta > 0.005 else ("↓" if delta < -0.005 else "→")
        print(f"  {f}: {v1*100:.1f}% → {v2*100:.1f}% ({arrow} {abs(delta)*100:.1f}pp)")
