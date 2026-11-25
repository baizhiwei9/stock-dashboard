#!/usr/bin/env python3
"""
结构因子模型 - 基于"相对值"和"绝对值"的区间判断系统
参考文档：《短期基线交易的基本原理和框架流程（四）》
核心概念：
  - 相对值：测试场内筹码结构所反馈出的结构因子值变化的斜率，观察场外资金池
  - 绝对值：反映场外资金回归场内的拐点
  - 区间一：风险释放阶段（相对值双顶确认、绝对值创新低）
  - 区间二：修复反弹阶段（绝对值不再创新低、相对值下行）
  - 赔率模型：左侧交易，在区间一末端提前布局
  - 胜率模型：右侧交易，在区间二启动确认后跟进
"""

import numpy as np
import pandas as pd
from scipy import signal
from scipy.ndimage import gaussian_filter1d
import warnings
warnings.filterwarnings('ignore')


class RelativeValue:
    """
    相对值因子 - 反映筹码结构斜率变化和场外游离资金池
    
    核心逻辑：
    1. 计算价格变化的斜率（一阶导数），反映筹码结构挤压/松动速度
    2. 结合成交量结构，加权处理大单的贡献
    3. 输出标准化后的相对值序列
    
    相对值触顶含义：
    - 相对值第一次触顶（红线）：空头能量第一次加速扩张，场外游离资金池开始形成
    - 相对值第二次触顶（粉线）：空头能量第二次加速且无法继续加速，空头能量接近出清
    """
    
    def __init__(self, period=20, smooth_window=5, threshold_pct=85):
        self.period = period          # 计算窗口
        self.smooth_window = smooth_window  # 平滑窗口
        self.threshold_pct = threshold_pct   # 阈值百分位（触顶判断）
    
    def calculate(self, prices, volumes=None, market_cap=None):
        """
        计算相对值
        
        Args:
            prices: np.array, 收盘价序列
            volumes: np.array, 成交量序列（可选）
            market_cap: np.array, 市值序列（可选，用于归一化）
            
        Returns:
            relative_value: np.array, 相对值序列
        """
        n = len(prices)
        
        # 1. 计算价格变化的斜率（一阶导数近似）
        price_diff = np.diff(prices, prepend=prices[0])
        
        # 2. 计算斜率的斜率（二阶导数近似），反映加速度
        acceleration = np.diff(price_diff, prepend=price_diff[0])
        
        # 3. 标准化处理 - 使用滚动窗口Z-Score
        rv = np.zeros(n)
        for i in range(self.period, n):
            window = price_diff[i-self.period:i+1]
            rv[i] = (price_diff[i] - np.mean(window)) / (np.std(window) + 1e-8)
        
        # 4. 用成交量加权（如果提供了成交量数据）
        if volumes is not None:
            volume_ma = pd.Series(volumes).rolling(window=self.period, min_periods=1).mean().values
            volume_ratio = volumes / (volume_ma + 1e-8)
            rv = rv * volume_ratio
        
        # 5. 高斯平滑
        if self.smooth_window > 1:
            rv = gaussian_filter1d(rv, sigma=self.smooth_window/3)
        
        # 6. 归一化到 [-1, 1] 范围
        rv_max = np.percentile(np.abs(rv[self.period:]), 99) if len(rv) > self.period else 1
        rv = np.clip(rv / (rv_max + 1e-8), -1, 1)
        
        return rv
    
    def detect_peaks(self, rv, min_peak_height=0.5, min_peak_distance=20):
        """
        检测相对值的双顶结构
        
        Args:
            rv: np.array, 相对值序列
            min_peak_height: float, 最小峰值高度
            min_peak_distance: int, 最小峰值间距
            
        Returns:
            dict: {'first_peak_idx': int, 'second_peak_idx': int, 
                   'is_double_top': bool, 'interval_strength': float}
        """
        # 找局部极大值（波峰）
        peaks, properties = signal.find_peaks(rv, 
                                               height=min_peak_height,
                                               distance=min_peak_distance,
                                               prominence=0.1)
        
        if len(peaks) < 2:
            return {
                'first_peak_idx': peaks[0] if len(peaks) > 0 else None,
                'second_peak_idx': None,
                'is_double_top': False,
                'interval_strength': 0
            }
        
        # 取最近两个波峰
        peak1_idx = peaks[-2]
        peak2_idx = peaks[-1]
        peak1_val = rv[peak1_idx]
        peak2_val = rv[peak2_idx]
        
        # 判断双顶条件
        # 条件1：第二峰不比第一峰高太多（顶部钝化）
        peak_ratio = peak2_val / (peak1_val + 1e-8)
        is_double_top = peak_ratio < 1.05 and peak_ratio > 0.6
        
        # 条件2：两峰之间有足够间距
        interval_distance = peak2_idx - peak1_idx
        
        # 计算区间强度（0-1）
        # 基于：1) 峰高度 2) 间距 3) 第二峰相对高度
        interval_strength = (peak1_val + peak2_val) / 2  # 峰高度
        interval_strength *= min(1, interval_distance / min_peak_distance)  # 间距
        interval_strength *= (1 - abs(1 - peak_ratio))  # 两峰均衡度
        
        return {
            'first_peak_idx': peak1_idx,
            'second_peak_idx': peak2_idx,
            'first_peak_val': peak1_val,
            'second_peak_val': peak2_val,
            'is_double_top': is_double_top,
            'interval_strength': interval_strength,
            'peak_distance': interval_distance,
            'peak_ratio': peak_ratio
        }
    
    def assess_money_pool(self, rv, peaks_info):
        """
        评估场外游离资金池规模
        
        红框圈定的部分：相对值第一次触顶
        - 峰值越大、持续高位横盘时间越长，代表游离资金池初始体量越大
        
        粉框圈定的部分：相对值第二次触顶
        - 代表空头能量第二次加速且无法继续加速，空头能量接近出清
        """
        if not peaks_info['first_peak_idx']:
            return {'pool_size': 0, 'status': 'unknown'}
        
        first_peak = peaks_info['first_peak_idx']
        first_val = peaks_info['first_peak_val']
        
        # 计算红框区域（第一次触顶前后的持续高位）
        start_red = max(0, first_peak - 5)
        end_red = min(len(rv), first_peak + 5)
        red_area_duration = end_red - start_red
        red_area_avg = np.mean(rv[start_red:end_red])
        
        # 资金池规模 = 峰值 × 持续时间 × 平均高度
        pool_size = first_val * red_area_duration * red_area_avg
        
        status = 'building'  # 正在形成
        if peaks_info['second_peak_idx']:
            second_peak = peaks_info['second_peak_idx']
            # 如果已经过了第二峰，资金池进入出清阶段
            if peaks_info['is_double_top']:
                status = 'exhausting'  # 空头能量接近出清
            else:
                status = 'expanding'  # 仍在扩大
        
        return {
            'pool_size': pool_size,
            'status': status,
            'first_peak_duration': red_area_duration,
            'first_peak_avg': red_area_avg
        }


class AbsoluteValue:
    """
    绝对值因子 - 反映场外资金回归场内的拐点
    
    核心逻辑：
    1. 绝对值零轴穿越 = 场外资金开始回流或持续流出
    2. 绝对值不再创新低 = 空头能量衰竭，潜在反转信号
    3. 绝对值持续上行 = 场外资金持续回归
    
    绝对值关键信号：
    - 绝对值零轴上方 = 多头能量占优
    - 绝对值零轴下方 = 空头能量占优
    - 绝对值从下方上穿零轴 = 胜率提升起始点（右侧交易信号）
    - 绝对值不再创新低 = 赔率入场点（左侧交易信号）
    """
    
    def __init__(self, period=20, short_period=5, long_period=60):
        self.period = period
        self.short_period = short_period
        self.long_period = long_period
    
    def calculate(self, prices, volumes=None, turnover=None):
        """
        计算绝对值
        
        Args:
            prices: np.array, 收盘价序列
            volumes: np.array, 成交量序列（可选）
            turnover: np.array, 换手率序列（可选）
            
        Returns:
            absolute_value: np.array, 绝对值序列
        """
        n = len(prices)
        
        # 1. 计算资金流向指标（类似OBV的变体）
        # 价格上涨日累计成交量，下跌日扣除成交量
        money_flow = np.zeros(n)
        for i in range(1, n):
            if prices[i] > prices[i-1]:
                money_flow[i] = money_flow[i-1] + (volumes[i] if volumes is not None else 1)
            elif prices[i] < prices[i-1]:
                money_flow[i] = money_flow[i-1] - (volumes[i] if volumes is not None else 1)
            else:
                money_flow[i] = money_flow[i-1]
        
        # 2. 计算长期与短期均线差
        short_ma = pd.Series(prices).rolling(window=self.short_period, min_periods=1).mean().values
        long_ma = pd.Series(prices).rolling(window=self.long_period, min_periods=1).mean().values
        
        # 3. 归一化后的均线差
        price_range = np.max(prices) - np.min(prices)
        ma_diff = (short_ma - long_ma) / (price_range + 1e-8)
        
        # 4. 计算动量
        momentum = np.zeros(n)
        for i in range(self.period, n):
            momentum[i] = (prices[i] - prices[i-self.period]) / (prices[i-self.period] + 1e-8)
        
        # 5. 综合绝对值
        # 归一化资金流向
        mf_norm = np.zeros(n)
        for i in range(self.period, n):
            window = money_flow[max(0, i-self.period):i+1]
            if np.std(window) > 0:
                mf_norm[i] = (money_flow[i] - np.mean(window)) / np.std(window)
        
        # 6. 组合：均线差权重50% + 动量30% + 资金流向20%
        av = 0.5 * ma_diff + 0.3 * momentum + 0.2 * mf_norm
        
        # 7. 高斯平滑
        av = gaussian_filter1d(av, sigma=2)
        
        # 8. 归一化到 [-1, 1]
        av_max = np.percentile(np.abs(av[self.long_period:]), 99) if len(av) > self.long_period else 1
        av = np.clip(av / (av_max + 1e-8), -1, 1)
        
        return av
    
    def detect_zero_cross(self, av):
        """
        检测绝对值的零轴穿越
        
        Returns:
            list of dict: 每次穿越的信息
        """
        crosses = []
        for i in range(1, len(av)):
            if av[i-1] < 0 and av[i] >= 0:
                crosses.append({
                    'index': i,
                    'type': 'up_cross',  # 上穿零轴
                    'value': av[i],
                    'prev_value': av[i-1]
                })
            elif av[i-1] > 0 and av[i] <= 0:
                crosses.append({
                    'index': i,
                    'type': 'down_cross',  # 下穿零轴
                    'value': av[i],
                    'prev_value': av[i-1]
                })
        return crosses
    
    def detect_low_extremes(self, av, window=20):
        """
        检测绝对值不再创新低（赔率入场信号）
        
        Returns:
            dict: {'is_new_low': bool, 'lowest_idx': int, 'confirmation': bool}
        """
        if len(av) < window * 2:
            return {'is_new_low': False, 'confirmation': False}
        
        # 找最近窗口的最低点
        recent = av[-window:]
        recent_low = np.min(recent)
        recent_low_idx = np.argmin(recent) + (len(av) - window)
        
        # 找历史最低点
        historical = av[:len(av)-window]
        if len(historical) > 0:
            historical_low = np.min(historical)
        else:
            historical_low = recent_low
        
        # 判断条件
        is_new_low = recent_low < historical_low - 0.05  # 新低阈值
        
        # 确认：连续3天不再创新低
        confirmation = False
        if len(av) >= 3:
            last_3 = av[-3:]
            confirmation = all(last_3[i] >= last_3[i-1] for i in range(1, 3))
        
        return {
            'is_new_low': is_new_low,
            'recent_low': recent_low,
            'historical_low': historical_low,
            'confirmation': confirmation,
            'lowest_idx': recent_low_idx
        }


class TaijiModel:
    """
    太极图模型 - 用数学表达多空能量转换
    
    太极图含义：
    - 白鱼（阳）：多头能量，面积代表多头力量总量
    - 黑鱼（阴）：空头能量，面积代表空头力量总量
    - 黑眼（白鱼中的黑点）：多头中的空头陷阱，预示反转
    - 白眼（黑鱼中的白点）：空头中的多头陷阱，预示反弹
    
    数学表达：
    - 白鱼面积 = ∫(max(绝对值, 0))dt
    - 黑鱼面积 = ∫(max(-绝对值, 0))dt
    - 鱼眼位置 = 绝对值极值点（陷阱位）
    - 面积比 = 白鱼面积/黑鱼面积，用于判断多空力量对比
    """
    
    def __init__(self):
        pass
    
    def calculate_areas(self, av):
        """
        计算太极图的白鱼和黑鱼面积
        
        Args:
            av: np.array, 绝对值序列
            
        Returns:
            dict: {'white_fish_area': float, 'black_fish_area': float,
                   'white_eye': int, 'black_eye': int,
                   'balance_ratio': float, 'dominant': str}
        """
        # 白鱼面积（多头能量）= 绝对值正部分的积分
        positive_mask = av > 0
        white_fish_area = np.sum(av[positive_mask])
        
        # 黑鱼面积（空头能量）= 绝对值负部分的绝对值积分
        negative_mask = av < 0
        black_fish_area = np.sum(-av[negative_mask])
        
        # 找鱼眼（陷阱位）
        # 黑眼：白鱼中的最低点（多头陷阱）
        if np.any(positive_mask):
            white_indices = np.where(positive_mask)[0]
            positive_av = av[positive_mask]
            if len(positive_av) > 0:
                black_eye_idx = white_indices[np.argmin(positive_av)]
                black_eye_val = np.min(positive_av)
            else:
                black_eye_idx = None
                black_eye_val = 0
        else:
            black_eye_idx = None
            black_eye_val = 0
        
        # 白眼：黑鱼中的最高点（空头陷阱）
        if np.any(negative_mask):
            black_indices = np.where(negative_mask)[0]
            negative_av = av[negative_mask]
            if len(negative_av) > 0:
                white_eye_idx = black_indices[np.argmax(negative_av)]
                white_eye_val = np.max(negative_av)
            else:
                white_eye_idx = None
                white_eye_val = 0
        else:
            white_eye_idx = None
            white_eye_val = 0
        
        # 力量平衡比
        total = white_fish_area + black_fish_area
        if total > 0:
            balance_ratio = white_fish_area / total
        else:
            balance_ratio = 0.5
        
        # 主导方
        if balance_ratio > 0.55:
            dominant = 'bull'  # 多头主导
        elif balance_ratio < 0.45:
            dominant = 'bear'  # 空头主导
        else:
            dominant = 'balanced'  # 平衡
        
        return {
            'white_fish_area': white_fish_area,
            'black_fish_area': black_fish_area,
            'balance_ratio': balance_ratio,
            'dominant': dominant,
            'black_eye_idx': black_eye_idx,
            'black_eye_val': black_eye_val,
            'white_eye_idx': white_eye_idx,
            'white_eye_val': white_eye_val
        }
    
    def detect_energy_transition(self, av, window=10):
        """
        检测能量转换临界点
        
        Returns:
            dict: {'transition_type': str, 'confidence': float}
        """
        if len(av) < window * 2:
            return {'transition_type': 'insufficient_data', 'confidence': 0}
        
        recent = av[-window:]
        previous = av[-window*2:-window]
        
        recent_area = np.sum(recent)
        previous_area = np.sum(previous)
        
        # 能量转换判断
        if previous_area < 0 and recent_area > 0:
            # 从空头能量转向多头能量
            transition = 'bear_to_bull'
            confidence = min(abs(recent_area) / (abs(previous_area) + 1e-8), 1.0)
        elif previous_area > 0 and recent_area < 0:
            # 从多头能量转向空头能量
            transition = 'bull_to_bear'
            confidence = min(abs(recent_area) / (abs(previous_area) + 1e-8), 1.0)
        else:
            transition = 'continuation'
            confidence = 0.5
        
        return {
            'transition_type': transition,
            'confidence': confidence,
            'recent_energy': recent_area,
            'previous_energy': previous_area
        }


class IntervalDetector:
    """
    区间判断器 - 自动识别当前处于区间一（风险释放）还是区间二（修复反弹）
    
    判断逻辑：
    
    区间一（风险释放）：
    - 相对值处于高位或双顶结构
    - 绝对值持续创新低或处于零轴下方
    - 太极图黑鱼面积大于白鱼面积
    - 赔率模型活跃（左侧交易机会）
    
    区间二（修复反弹）：
    - 相对值下行（从第二峰回落）
    - 绝对值不再创新低，开始上行
    - 绝对值上穿零轴（胜率模型信号）
    - 太极图能量从黑鱼转向白鱼
    """
    
    def __init__(self, rv_period=20, av_period=20):
        self.rv = RelativeValue(period=rv_period)
        self.av = AbsoluteValue(period=av_period)
        self.taiji = TaijiModel()
    
    def analyze(self, prices, volumes=None):
        """
        综合分析当前区间状态
        
        Args:
            prices: np.array, 价格序列
            volumes: np.array, 成交量序列（可选）
            
        Returns:
            dict: 完整的区间分析结果
        """
        # 1. 计算因子
        rv_values = self.rv.calculate(prices, volumes)
        av_values = self.av.calculate(prices, volumes)
        
        # 2. 检测相对值双顶
        rv_peaks = self.rv.detect_peaks(rv_values)
        
        # 3. 评估资金池
        money_pool = self.rv.assess_money_pool(rv_values, rv_peaks)
        
        # 4. 检测绝对值零轴穿越
        av_crosses = self.av.detect_zero_cross(av_values)
        
        # 5. 检测绝对值不再创新低
        av_extremes = self.av.detect_low_extremes(av_values)
        
        # 6. 计算太极图
        taiji_info = self.taiji.calculate_areas(av_values)
        
        # 7. 区间判断
        interval = self._determine_interval(rv_values, av_values, rv_peaks, av_extremes, taiji_info)
        
        return {
            'interval': interval,
            'interval_confidence': interval['confidence'],
            'relative_value': {
                'values': rv_values,
                'peaks': rv_peaks,
                'money_pool': money_pool
            },
            'absolute_value': {
                'values': av_values,
                'crosses': av_crosses,
                'extremes': av_extremes
            },
            'taiji': taiji_info
        }
    
    def _determine_interval(self, rv, av, rv_peaks, av_extremes, taiji_info):
        """
        核心区间判断逻辑
        
        区间一判定条件（至少满足3个）：
        1. 相对值处于高位（>0.5）或双顶结构
        2. 绝对值处于零轴下方或持续下行
        3. 太极图黑鱼面积大于白鱼面积
        4. 绝对值创新低
        
        区间二判定条件（至少满足3个）：
        1. 相对值从第二峰回落（下行）
        2. 绝对值不再创新低，开始上行
        3. 绝对值上穿零轴
        4. 太极图能量从黑鱼转向白鱼
        """
        
        score_interval1 = 0
        score_interval2 = 0
        
        # 条件1：相对值状态
        if rv_peaks['is_double_top'] or np.mean(rv[-10:]) > 0.4:
            score_interval1 += 1
        if rv_peaks['second_peak_idx'] and len(rv) - rv_peaks['second_peak_idx'] > 5:
            # 已经过了第二峰一段时间，相对值开始回落
            score_interval2 += 1
        
        # 条件2：绝对值状态
        if np.mean(av[-10:]) < -0.2:
            score_interval1 += 1
        if av_extremes['confirmation'] and not av_extremes['is_new_low']:
            score_interval2 += 1
        
        # 条件3：太极图能量
        if taiji_info['balance_ratio'] < 0.45:
            score_interval1 += 1
        if taiji_info['balance_ratio'] > 0.55:
            score_interval2 += 1
        
        # 条件4：绝对值创新低
        if av_extremes['is_new_low']:
            score_interval1 += 1
        
        # 判断结果
        if score_interval1 >= 3:
            return {
                'type': 'interval_1',
                'name': '区间一（风险释放）',
                'confidence': score_interval1 / 4,
                'description': '市场处于风险释放阶段，相对值双顶确认，绝对值持续下行',
                'strategy': '赔率模型（左侧交易）：等待绝对值不再创新低+相对值下行，寻找入场点'
            }
        elif score_interval2 >= 3:
            return {
                'type': 'interval_2',
                'name': '区间二（修复反弹）',
                'confidence': score_interval2 / 4,
                'description': '市场处于修复反弹阶段，相对值回落，绝对值上行',
                'strategy': '胜率模型（右侧交易）：绝对值上穿零轴后跟进，寻找胜率提升起始点'
            }
        else:
            return {
                'type': 'transition',
                'name': '过渡阶段',
                'confidence': 0.5,
                'description': '市场处于区间切换的过渡阶段，信号混杂',
                'strategy': '观望为主，等待更明确的信号'
            }


class OddsModel:
    """
    赔率模型 - 左侧交易
    
    核心逻辑：
    1. 相对值双顶确认（红线→粉线）= 空头能量加速且无法继续加速
    2. 绝对值不再创新低 = 空头能量衰竭
    3. 两者同时满足 = 赔率入场点
    
    入场条件：
    - 相对值处于第二峰后的下行阶段
    - 绝对值连续N天不再创新低
    - 绝对值处于零轴下方（空头能量仍占优）
    
    出场条件：
    - 绝对值上穿零轴（转为胜率模型）
    - 相对值重新上行（空头能量重新积聚）
    """
    
    def __init__(self, confirmation_days=3):
        self.confirmation_days = confirmation_days
    
    def get_signal(self, rv_values, av_values, rv_peaks, av_extremes):
        """
        获取赔率模型信号
        
        Returns:
            dict: {'signal': str, 'confidence': float, 'reason': str}
        """
        signal = 'hold'
        confidence = 0
        reason = []
        
        # 条件1：相对值双顶确认或处于高位下行
        if rv_peaks['is_double_top'] and rv_peaks['second_peak_idx']:
            # 已经过了第二峰
            if len(rv_values) - rv_peaks['second_peak_idx'] > 3:
                confidence += 0.3
                reason.append(f"相对值双顶确认，第二峰已过{len(rv_values) - rv_peaks['second_peak_idx']}天")
        elif np.mean(rv_values[-10:]) > 0.3:
            confidence += 0.15
            reason.append("相对值处于高位")
        
        # 条件2：绝对值不再创新低
        if av_extremes['confirmation']:
            confidence += 0.4
            reason.append("绝对值不再创新低，空头能量衰竭")
        
        # 条件3：绝对值处于零轴下方（空头仍占优，有反弹空间）
        if np.mean(av_values[-5:]) < 0:
            confidence += 0.2
            reason.append("绝对值处于零轴下方，赔率空间大")
        
        # 综合判断
        if confidence >= 0.7:
            signal = 'buy'
        elif confidence >= 0.5:
            signal = 'prepare_buy'
        elif confidence < 0.3 and rv_peaks['is_double_top'] is False:
            signal = 'exit'
            reason.append("赔率窗口已关闭")
        
        return {
            'signal': signal,
            'confidence': confidence,
            'reason': '；'.join(reason),
            'model': 'odds',
            'type': '左侧交易'
        }


class WinRateModel:
    """
    胜率模型 - 右侧交易
    
    核心逻辑：
    1. 绝对值上穿零轴 = 胜率提升起始点
    2. 绝对值持续在零轴上方 = 胜率维持
    3. 绝对值从高位回落到零轴 = 胜率提升终止点
    
    入场条件：
    - 绝对值上穿零轴
    - 相对值已从第二峰回落（确认区间转换）
    - 太极图能量从黑鱼转向白鱼
    
    出场条件：
    - 绝对值下穿零轴（胜率结束）
    - 相对值重新上行（空头能量重新积聚）
    """
    
    def __init__(self):
        pass
    
    def get_signal(self, av_values, av_crosses, rv_values, rv_peaks, taiji_info):
        """
        获取胜率模型信号
        
        Returns:
            dict: {'signal': str, 'confidence': float, 'reason': str}
        """
        signal = 'hold'
        confidence = 0
        reason = []
        
        # 检查最近是否有零轴上穿
        recent_cross = None
        for cross in reversed(av_crosses):
            if cross['type'] == 'up_cross':
                recent_cross = cross
                break
        
        # 条件1：绝对值上穿零轴
        if recent_cross and len(av_values) - recent_cross['index'] <= 10:
            confidence += 0.4
            reason.append(f"绝对值{len(av_values) - recent_cross['index']}天前上穿零轴")
        
        # 条件2：绝对值持续在零轴上方
        if np.mean(av_values[-5:]) > 0.1:
            confidence += 0.3
            reason.append("绝对值持续在零轴上方")
        
        # 条件3：相对值已从第二峰回落
        if rv_peaks['second_peak_idx'] and len(rv_values) - rv_peaks['second_peak_idx'] > 5:
            confidence += 0.2
            reason.append("相对值已从第二峰回落，区间转换确认")
        
        # 条件4：太极图能量转向
        if taiji_info['balance_ratio'] > 0.55:
            confidence += 0.1
            reason.append("太极图显示多头能量占优")
        
        # 综合判断
        if confidence >= 0.7:
            signal = 'buy'
        elif confidence >= 0.5:
            signal = 'prepare_buy'
        elif confidence < 0.3 and recent_cross is None:
            # 没有上穿信号，胜率模型不活跃
            signal = 'no_signal'
            reason.append("胜率模型未激活")
        
        return {
            'signal': signal,
            'confidence': confidence,
            'reason': '；'.join(reason),
            'model': 'win_rate',
            'type': '右侧交易',
            'recent_cross': recent_cross
        }


# ==================== 便捷函数 ====================

def analyze_market(prices, volumes=None):
    """
    一键分析市场状态
    
    Args:
        prices: np.array, 收盘价序列
        volumes: np.array, 成交量序列（可选）
        
    Returns:
        dict: 完整的市场分析报告
    """
    detector = IntervalDetector()
    result = detector.analyze(prices, volumes)
    
    # 赔率模型信号
    odds = OddsModel()
    odds_signal = odds.get_signal(
        result['relative_value']['values'],
        result['absolute_value']['values'],
        result['relative_value']['peaks'],
        result['absolute_value']['extremes']
    )
    
    # 胜率模型信号
    win_rate = WinRateModel()
    win_rate_signal = win_rate.get_signal(
        result['absolute_value']['values'],
        result['absolute_value']['crosses'],
        result['relative_value']['values'],
        result['relative_value']['peaks'],
        result['taiji']
    )
    
    # 综合建议
    if result['interval']['type'] == 'interval_1':
        primary_strategy = '赔率模型（左侧交易）'
        if odds_signal['signal'] in ['buy', 'prepare_buy']:
            action = f"【入场】{odds_signal['reason']}"
        else:
            action = "【等待】等待赔率窗口出现（相对值双顶+绝对值不再创新低）"
    elif result['interval']['type'] == 'interval_2':
        primary_strategy = '胜率模型（右侧交易）'
        if win_rate_signal['signal'] in ['buy', 'prepare_buy']:
            action = f"【入场】{win_rate_signal['reason']}"
        else:
            action = "【等待】等待胜率确认（绝对值上穿零轴）"
    else:
        primary_strategy = '观望'
        action = "【观望】市场处于过渡阶段，信号混杂"
    
    return {
        'interval': result['interval'],
        'primary_strategy': primary_strategy,
        'action': action,
        'odds_signal': odds_signal,
        'win_rate_signal': win_rate_signal,
        'relative_value': result['relative_value'],
        'absolute_value': result['absolute_value'],
        'taiji': result['taiji'],
        'summary': f"当前市场处于{result['interval']['name']}，建议采用{primary_strategy}，{action}"
    }


def generate_report(prices, volumes=None, dates=None, output_file=None):
    """
    生成结构因子分析报告
    
    Args:
        prices: np.array, 价格序列
        volumes: np.array, 成交量序列（可选）
        dates: list, 日期序列（可选）
        output_file: str, 输出文件路径（可选）
        
    Returns:
        str: 报告文本
    """
    result = analyze_market(prices, volumes)
    
    lines = [
        "# 结构因子分析报告",
        "",
        f"**生成时间：** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}",
        f"**数据长度：** {len(prices)} 个交易日",
        "",
        "---",
        "",
        "## 一、区间判断",
        "",
        f"**当前区间：** {result['interval']['name']}",
        f"**置信度：** {result['interval']['confidence']*100:.1f}%",
        f"**描述：** {result['interval']['description']}",
        f"**策略建议：** {result['interval']['strategy']}",
        "",
        "## 二、交易信号",
        "",
        "### 赔率模型（左侧交易）",
        "",
        f"| 项目 | 内容 |",
        f"|------|------|",
        f"| 信号 | {'买入' if result['odds_signal']['signal'] == 'buy' else ('准备买入' if result['odds_signal']['signal'] == 'prepare_buy' else '持有/观望')} |",
        f"| 置信度 | {result['odds_signal']['confidence']*100:.1f}% |",
        f"| 依据 | {result['odds_signal']['reason']} |",
        "",
        "### 胜率模型（右侧交易）",
        "",
        f"| 项目 | 内容 |",
        f"|------|------|",
        f"| 信号 | {'买入' if result['win_rate_signal']['signal'] == 'buy' else ('准备买入' if result['win_rate_signal']['signal'] == 'prepare_buy' else '未激活')} |",
        f"| 置信度 | {result['win_rate_signal']['confidence']*100:.1f}% |",
        f"| 依据 | {result['win_rate_signal']['reason']} |",
        "",
        "## 三、太极图分析",
        "",
        f"| 项目 | 内容 |",
        f"|------|------|",
        f"| 白鱼面积（多头能量） | {result['taiji']['white_fish_area']:.4f} |",
        f"| 黑鱼面积（空头能量） | {result['taiji']['black_fish_area']:.4f} |",
        f"| 力量平衡比 | {result['taiji']['balance_ratio']*100:.1f}% |",
        f"| 主导方 | {'多头' if result['taiji']['dominant'] == 'bull' else ('空头' if result['taiji']['dominant'] == 'bear' else '平衡')} |",
        "",
        "## 四、相对值分析",
        "",
        f"| 项目 | 内容 |",
        f"|------|------|",
    ]
    
    rv_peaks = result['relative_value']['peaks']
    lines.extend([
        f"| 第一峰位置 | {rv_peaks['first_peak_idx'] if rv_peaks['first_peak_idx'] else '未检测'} |",
        f"| 第一峰高度 | {rv_peaks.get('first_peak_val', 0):.4f} |",
        f"| 第二峰位置 | {rv_peaks['second_peak_idx'] if rv_peaks['second_peak_idx'] else '未检测'} |",
        f"| 第二峰高度 | {rv_peaks.get('second_peak_val', 0):.4f} |",
        f"| 双顶结构 | {'是' if rv_peaks['is_double_top'] else '否'} |",
        f"| 区间强度 | {rv_peaks.get('interval_strength', 0)*100:.1f}% |",
        f"| 资金池状态 | {result['relative_value']['money_pool']['status']} |",
        "",
        "## 五、绝对值分析",
        "",
        f"| 项目 | 内容 |",
        f"|------|------|",
    ])
    
    av_extremes = result['absolute_value']['extremes']
    lines.extend([
        f"| 是否创新低 | {'是' if av_extremes['is_new_low'] else '否'} |",
        f"| 不再创新低确认 | {'是' if av_extremes['confirmation'] else '否'} |",
        f"| 近期最低值 | {av_extremes['recent_low']:.4f} |",
        f"| 历史最低值 | {av_extremes['historical_low']:.4f} |",
        f"| 零轴穿越次数 | {len(result['absolute_value']['crosses'])} |",
        "",
        "## 六、综合建议",
        "",
        f"**{result['summary']}**",
        "",
        "---",
        "",
        "*报告由结构因子模型自动生成 | 参考：短期基线交易基本原理和框架流程（四）*",
    ])
    
    report = "\n".join(lines)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report)
    
    return report


if __name__ == "__main__":
    # 示例：用随机数据测试
    np.random.seed(42)
    n = 100
    
    # 模拟价格序列（带趋势和波动）
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volumes = np.abs(np.random.randn(n) * 1000000 + 5000000)
    
    # 分析
    result = analyze_market(prices, volumes)
    print(result['summary'])
    print("\n" + "="*60)
    
    # 生成报告
    report = generate_report(prices, volumes, output_file="structural_factor_report.md")
    print("\n报告已保存: structural_factor_report.md")
    print("\n前20行报告预览:")
    print("\n".join(report.split("\n")[:20]))
