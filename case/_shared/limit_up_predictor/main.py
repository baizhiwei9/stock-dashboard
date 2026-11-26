#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成学习选股 - 涨停预测回测框架
===================
功能：
1. 使用baostock获取A股日线数据
2. 三维特征工程（收益率、波动率、成交量）
3. 前置过滤器（成交量突变 + 波动率挤压）
4. Stacking集成模型（XGBoost + LightGBM + CatBoost + LR元模型）
5. 时间序列滚动回测
6. 评估指标与可视化

作者: Codex AI
日期: 2026-07-14
"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
from typing import Tuple, Dict, List, Optional
import joblib
from tqdm import tqdm

# 机器学习库
from sklearn.model_selection import TimeSeriesSplit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


# =============================================================================
# 第一部分：数据获取模块
# =============================================================================

class DataFetcher:
    """数据获取类 - 使用baostock获取A股数据"""
    
    def __init__(self, start_date: str = '2020-01-01', end_date: str = '2025-12-31'):
        """
        初始化数据获取器
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
        """
        self.start_date = start_date
        self.end_date = end_date
        self._init_baostock()
        
    def _init_baostock(self):
        """初始化baostock连接"""
        try:
            import baostock as bs
            self.bs = bs
            lg = bs.login()
            if lg.error_code != '0':
                raise Exception(f"baostock登录失败: {lg.error_msg}")
            print("✓ baostock连接成功")
        except ImportError:
            print("⚠ baostock未安装，使用模拟数据模式")
            self.bs = None
    
    def get_hs300_components(self, date: str = None) -> List[str]:
        """
        获取沪深300成分股列表
        
        Args:
            date: 查询日期，默认为最新
            
        Returns:
            股票代码列表
        """
        if self.bs is None:
            return self._get_mock_pool('hs300')
        
        rs = self.bs.query_hs300_stocks(date=date)
        stocks = []
        while rs.error_code == '0' and rs.next():
            stocks.append(rs.get_row_data())
        
        df = pd.DataFrame(stocks, columns=rs.fields)
        return df['code'].tolist()
    
    def get_zz500_components(self, date: str = None) -> List[str]:
        """获取中证500成分股列表"""
        if self.bs is None:
            return self._get_mock_pool('zz500')
        
        rs = self.bs.query_zz500_stocks(date=date)
        stocks = []
        while rs.error_code == '0' and rs.next():
            stocks.append(rs.get_row_data())
        
        df = pd.DataFrame(stocks, columns=rs.fields)
        return df['code'].tolist()
    
    def get_zz1000_components(self, date: str = None) -> List[str]:
        """
        获取中证1000成分股列表
        baostock 无直接接口，通过 akshare 获取
        """
        try:
            import akshare as ak
            df = ak.index_stock_cons(symbol='000852')
            codes = df['品种代码'].tolist()
            # 转换为 baostock 格式
            result = []
            for code in codes:
                code = str(code).zfill(6)
                if code.startswith('6'):
                    result.append(f"sh.{code}")
                else:
                    result.append(f"sz.{code}")
            return result
        except Exception as e:
            print(f"⚠ akshare 获取中证1000失败: {e}，使用模拟数据")
            return self._get_mock_pool('zz1000')
    
    def get_star_market_stocks(self) -> List[str]:
        """
        获取科创板全部股票 (688xxx)
        通过 akshare 获取实时列表
        """
        try:
            import akshare as ak
            df = ak.stock_kc_a_spot_em()
            codes = df['代码'].tolist()
            return [f"sh.{str(c).zfill(6)}" for c in codes if str(c).startswith('688')]
        except Exception as e:
            print(f"⚠ 获取科创板列表失败: {e}，使用模拟数据")
            return self._get_mock_pool('star')
    
    def get_chinext_stocks(self) -> List[str]:
        """
        获取创业板全部股票 (300xxx)
        通过 akshare 获取实时列表
        """
        try:
            import akshare as ak
            # 使用指数成分股近似，或全量A股过滤
            df = ak.stock_zh_a_spot_em()
            codes = df['代码'].tolist()
            return [f"sz.{str(c).zfill(6)}" for c in codes if str(c).startswith('300')]
        except Exception as e:
            print(f"⚠ 获取创业板列表失败: {e}，使用模拟数据")
            return self._get_mock_pool('chinext')
    
    def get_full_stock_pool(self, date: str = None,
                            include_hs300: bool = True,
                            include_zz500: bool = True,
                            include_zz1000: bool = True,
                            include_star: bool = True,
                            include_chinext: bool = True) -> List[str]:
        """
        获取完整股票池：沪深300 + 中证500 + 中证1000 + 科创板 + 创业板
        覆盖A股市值前约2000只股票
        
        Args:
            date: 查询日期
            include_hs300: 包含沪深300
            include_zz500: 包含中证500
            include_zz1000: 包含中证1000
            include_star: 包含科创板
            include_chinext: 包含创业板
            
        Returns:
            去重后的股票代码列表
        """
        all_codes = set()
        
        if include_hs300:
            codes = self.get_hs300_components(date)
            print(f"  沪深300: {len(codes)} 只")
            all_codes.update(codes)
        
        if include_zz500:
            codes = self.get_zz500_components(date)
            print(f"  中证500: {len(codes)} 只")
            all_codes.update(codes)
        
        if include_zz1000:
            codes = self.get_zz1000_components(date)
            print(f"  中证1000: {len(codes)} 只")
            all_codes.update(codes)
        
        if include_star:
            codes = self.get_star_market_stocks()
            print(f"  科创板: {len(codes)} 只")
            all_codes.update(codes)
        
        if include_chinext:
            codes = self.get_chinext_stocks()
            print(f"  创业板: {len(codes)} 只")
            all_codes.update(codes)
        
        result = sorted(all_codes)
        print(f"  合计（去重）: {len(result)} 只")
        return result
    
    @staticmethod
    def get_stock_board(code: str) -> str:
        """
        根据股票代码判断所属板块
        
        Args:
            code: 股票代码，如 'sh.600000' 或 'sz.300001'
            
        Returns:
            板块名称: 'star'(科创板), 'chinext'(创业板), 'main'(主板)
        """
        pure_code = code.split('.')[-1] if '.' in code else code
        if pure_code.startswith('688'):
            return 'star'
        elif pure_code.startswith('300'):
            return 'chinext'
        else:
            return 'main'
    
    def _get_mock_pool(self, pool_name: str) -> List[str]:
        """生成模拟股票池代码用于测试"""
        codes = []
        if pool_name == 'hs300':
            for i in range(600000, 600050):
                codes.append(f"sh.{i}")
            for i in range(601000, 601030):
                codes.append(f"sh.{i}")
            for i in range(0, 20):
                codes.append(f"sz.000{i:03d}")
        elif pool_name == 'zz500':
            for i in range(600100, 600200):
                codes.append(f"sh.{i}")
            for i in range(0, 100):
                codes.append(f"sz.000{100+i:03d}")
        elif pool_name == 'zz1000':
            for i in range(600300, 600400):
                codes.append(f"sh.{i}")
            for i in range(200, 300):
                codes.append(f"sz.000{200+i:03d}")
        elif pool_name == 'star':
            for i in range(688001, 688050):
                codes.append(f"sh.{i}")
        elif pool_name == 'chinext':
            for i in range(300001, 300050):
                codes.append(f"sz.{i}")
        return codes
    
    def get_stock_data(self, code: str) -> Optional[pd.DataFrame]:
        """
        获取单只股票的日线数据
        
        Args:
            code: 股票代码，格式如 'sh.600000'
            
        Returns:
            DataFrame包含 OHLCV 数据
        """
        if self.bs is None:
            return self._generate_mock_data(code)
        
        try:
            rs = self.bs.query_history_k_data_plus(
                code,
                "date,code,open,high,low,close,volume,amount",
                start_date=self.start_date,
                end_date=self.end_date,
                frequency="d",
                adjustflag="2"  # 前复权
            )
            
            data_list = []
            while rs.error_code == '0' and rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                return None
            
            df = pd.DataFrame(data_list, columns=rs.fields)
            
            # 数据类型转换
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            
            # 过滤无效数据
            df = df[df['volume'] > 0]
            df = df[df['close'] > 0]
            
            return df
            
        except Exception as e:
            print(f"获取 {code} 数据失败: {e}")
            return None
    
    def _generate_mock_data(self, code: str) -> pd.DataFrame:
        """生成模拟股票数据用于测试"""
        np.random.seed(hash(code) % 2**32)
        
        dates = pd.date_range(self.start_date, self.end_date, freq='B')
        n = len(dates)
        
        # 生成随机价格序列
        returns = np.random.normal(0.0005, 0.02, n)
        close = 10 * np.exp(np.cumsum(returns))
        
        # 生成OHLCV数据
        high = close * (1 + np.abs(np.random.normal(0, 0.01, n)))
        low = close * (1 - np.abs(np.random.normal(0, 0.01, n)))
        open_price = close * (1 + np.random.normal(0, 0.005, n))
        
        # 确保 high >= max(open, close) 且 low <= min(open, close)
        high = np.maximum(high, np.maximum(open_price, close))
        low = np.minimum(low, np.minimum(open_price, close))
        
        volume = np.random.lognormal(15, 1, n).astype(int)
        amount = volume * close
        
        df = pd.DataFrame({
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume,
            'amount': amount
        }, index=dates)
        df.index.name = 'date'
        
        return df
    
    def fetch_all_data(self, stock_pool: List[str] = None) -> Dict[str, pd.DataFrame]:
        """
        批量获取股票数据
        
        Args:
            stock_pool: 股票池，默认获取沪深300
            
        Returns:
            字典，key为股票代码，value为DataFrame
        """
        if stock_pool is None:
            stock_pool = self.get_hs300_components()
        
        print(f"\n开始获取 {len(stock_pool)} 只股票数据...")
        
        all_data = {}
        for code in tqdm(stock_pool, desc="获取数据"):
            df = self.get_stock_data(code)
            if df is not None and len(df) > 100:  # 至少100个交易日数据
                all_data[code] = df
        
        print(f"✓ 成功获取 {len(all_data)} 只股票数据")
        return all_data
    
    def close(self):
        """关闭连接"""
        if self.bs is not None:
            self.bs.logout()


# =============================================================================
# 第二部分：特征工程模块
# =============================================================================

class FeatureEngineer:
    """特征工程类 - 计算三维特征及衍生因子"""
    
    def __init__(self):
        self.feature_names = []
        
    def compute_basic_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算基础三维特征
        
        Args:
            df: 包含OHLCV的DataFrame
            
        Returns:
            添加了基础特征的DataFrame
        """
        df = df.copy()
        
        # 1. 收益率特征
        df['log_return'] = np.log(df['close'] / df['close'].shift(1))
        df['return_1d'] = df['close'].pct_change(1)
        df['return_5d'] = df['close'].pct_change(5)
        df['return_10d'] = df['close'].pct_change(10)
        
        # 2. 波动率特征
        # ATR (Average True Range)
        df = self._compute_atr(df, period=14)
        
        # 布林带宽度
        df = self._compute_bollinger(df, period=20)
        
        # 历史波动率
        df['hv_20'] = df['log_return'].rolling(20).std() * np.sqrt(252)
        df['vol_change'] = df['hv_20'].pct_change(5)
        
        # 3. 成交量特征
        df['ma_volume_20'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['ma_volume_20']
        df['volume_shock'] = (df['volume'] - df['volume'].shift(1)) / df['volume'].shift(1)
        df['volume_ma5_ratio'] = df['volume'] / df['volume'].rolling(5).mean()
        
        return df
    
    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """计算ATR指标"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift(1))
        low_close = np.abs(df['low'] - df['close'].shift(1))
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr_14'] = true_range.rolling(period).mean()
        df['atr_pct'] = df['atr_14'] / df['close']  # 归一化ATR
        return df
    
    def _compute_bollinger(self, df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """计算布林带"""
        df['bb_middle'] = df['close'].rolling(period).mean()
        df['bb_std'] = df['close'].rolling(period).std()
        df['bb_upper'] = df['bb_middle'] + 2 * df['bb_std']
        df['bb_lower'] = df['bb_middle'] - 2 * df['bb_std']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
        return df
    
    def compute_derived_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算衍生特征因子
        
        Args:
            df: 包含基础特征的DataFrame
            
        Returns:
            添加了衍生特征的DataFrame
        """
        df = df.copy()
        
        # === 收益率衍生 ===
        # RSI
        df['rsi_14'] = self._compute_rsi(df['close'], 14)
        
        # MACD
        df = self._compute_macd(df)
        
        # 动量指标
        df['momentum_5'] = df['close'] / df['close'].shift(5) - 1
        df['momentum_10'] = df['close'] / df['close'].shift(10) - 1
        df['momentum_20'] = df['close'] / df['close'].shift(20) - 1
        
        # 收益标准差
        df['return_std_5'] = df['log_return'].rolling(5).std()
        df['return_std_10'] = df['log_return'].rolling(10).std()
        
        # === 波动率衍生 ===
        # 波动率比率
        df['vol_ratio_5_20'] = df['log_return'].rolling(5).std() / df['log_return'].rolling(20).std()
        
        # 波动率锥
        df['vol_percentile'] = df['hv_20'].rolling(100).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )
        
        # === 成交量衍生 ===
        # OBV (On Balance Volume)
        df['obv'] = self._compute_obv(df)
        df['obv_ma5'] = df['obv'].rolling(5).mean()
        df['obv_slope'] = df['obv_ma5'].pct_change(5)
        
        # MFI (Money Flow Index)
        df['mfi_14'] = self._compute_mfi(df, 14)
        
        # 成交额变化率
        df['amount_change'] = df['amount'].pct_change(1)
        df['amount_ma5_ratio'] = df['amount'] / df['amount'].rolling(5).mean()
        
        # 换手率代理（成交额/流通市值的代理）
        df['turnover_proxy'] = df['amount'] / df['close']
        
        # === 交互特征 ===
        df['return_x_volume'] = df['log_return'] * df['volume_ratio']
        df['vol_x_volume'] = df['bb_width'] * df['volume_ratio']
        df['return_x_vol'] = df['log_return'] * df['bb_width']
        
        # === 价格形态特征 ===
        df['upper_shadow'] = (df['high'] - df[['open', 'close']].max(axis=1)) / df['close']
        df['lower_shadow'] = (df[['open', 'close']].min(axis=1) - df['low']) / df['close']
        df['body_ratio'] = np.abs(df['close'] - df['open']) / (df['high'] - df['low'])
        
        return df
    
    def _compute_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        """计算RSI指标"""
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _compute_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算MACD指标"""
        ema_12 = df['close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd_diff'] = ema_12 - ema_26
        df['macd_signal'] = df['macd_diff'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd_diff'] - df['macd_signal']
        return df
    
    def _compute_obv(self, df: pd.DataFrame) -> pd.Series:
        """计算OBV指标"""
        direction = np.sign(df['close'].diff())
        obv = (direction * df['volume']).cumsum()
        return obv
    
    def _compute_mfi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算MFI指标"""
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        money_flow = typical_price * df['volume']
        
        positive_flow = money_flow.where(typical_price.diff() > 0, 0).rolling(period).sum()
        negative_flow = money_flow.where(typical_price.diff() < 0, 0).rolling(period).sum()
        
        money_ratio = positive_flow / negative_flow
        mfi = 100 - (100 / (1 + money_ratio))
        return mfi
    
    def compute_all_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有特征"""
        df = self.compute_basic_features(df)
        df = self.compute_derived_features(df)
        
        # 记录特征列名
        self.feature_names = [col for col in df.columns 
                              if col not in ['open', 'high', 'low', 'close', 
                                            'volume', 'amount', 'date']]
        return df


# =============================================================================
# 第三部分：前置过滤器模块
# =============================================================================

class PreFilter:
    """前置过滤器 - 筛选高概率涨停候选"""
    
    def __init__(self, volume_threshold: float = 2.0, vol_squeeze_percentile: float = 0.2):
        """
        初始化过滤器
        
        Args:
            volume_threshold: 成交量突变阈值（相对20日均量的倍数）
            vol_squeeze_percentile: 波动率挤压分位数阈值
        """
        self.volume_threshold = volume_threshold
        self.vol_squeeze_percentile = vol_squeeze_percentile
        
    def apply_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        应用前置过滤
        
        Args:
            df: 包含特征的DataFrame
            
        Returns:
            过滤后的DataFrame
        """
        df = df.copy()
        
        # 条件1: 成交量突变 - 当日成交量 > 20日均量的2倍
        df['volume_shock_flag'] = (df['volume'] > df['ma_volume_20'] * self.volume_threshold).astype(int)
        
        # 条件2: 波动率挤压 - 布林带宽度处于过去100天最低20%分位
        df['bb_width_percentile'] = df['bb_width'].rolling(100).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )
        df['vol_squeeze_flag'] = (df['bb_width_percentile'] <= self.vol_squeeze_percentile).astype(int)
        
        # 同时满足两个条件
        df['filter_pass'] = (df['volume_shock_flag'] == 1) & (df['vol_squeeze_flag'] == 1)
        
        return df
    
    def get_filtered_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """获取通过过滤的数据"""
        df = self.apply_filter(df)
        return df[df['filter_pass'] == True].copy()


# =============================================================================
# 第四部分：标签定义模块
# =============================================================================

class LabelGenerator:
    """标签生成器 - 定义涨停标签，支持不同板块不同阈值"""
    
    def __init__(self, limit_threshold: float = 0.098,
                 star_threshold: float = 0.198,
                 chinext_threshold: float = 0.198):
        """
        初始化标签生成器
        
        Args:
            limit_threshold: 主板涨停阈值，默认9.8%
            star_threshold: 科创板涨停阈值，默认19.8%（20%涨跌幅）
            chinext_threshold: 创业板涨停阈值，默认19.8%（20%涨跌幅）
        """
        self.limit_threshold = limit_threshold
        self.star_threshold = star_threshold
        self.chinext_threshold = chinext_threshold
        
    def get_threshold(self, code: str) -> float:
        """根据股票代码获取对应涨停阈值"""
        board = DataFetcher.get_stock_board(code)
        if board == 'star':
            return self.star_threshold
        elif board == 'chinext':
            return self.chinext_threshold
        return self.limit_threshold
    
    def generate_labels(self, df: pd.DataFrame, code: str = None) -> pd.DataFrame:
        """
        生成涨停标签
        
        Args:
            df: 包含价格数据的DataFrame
            code: 股票代码，用于判断板块和对应涨停阈值
            
        Returns:
            添加了标签的DataFrame
        """
        df = df.copy()
        
        # 次日涨幅
        df['next_return'] = df['close'].shift(-1) / df['close'] - 1
        
        # 根据板块选择涨停阈值
        threshold = self.limit_threshold
        if code is not None:
            threshold = self.get_threshold(code)
        
        # 涨停标签
        df['limit_up'] = (df['next_return'] >= threshold).astype(int)
        
        return df
    
    def balance_samples(self, df: pd.DataFrame, ratio: float = 3.0, 
                        random_state: int = 42) -> pd.DataFrame:
        """
        下采样平衡正负样本
        
        Args:
            df: 包含标签的DataFrame
            ratio: 负样本与正样本的比例
            random_state: 随机种子
            
        Returns:
            平衡后的DataFrame
        """
        positive = df[df['limit_up'] == 1]
        negative = df[df['limit_up'] == 0]
        
        n_positive = len(positive)
        n_negative_target = int(n_positive * ratio)
        
        if n_positive == 0:
            print("⚠ 没有正样本")
            return df
        
        if len(negative) > n_negative_target:
            negative = negative.sample(n=n_negative_target, random_state=random_state)
        
        balanced = pd.concat([positive, negative]).sort_index()
        
        print(f"✓ 样本平衡: 正样本 {n_positive}, 负样本 {len(negative)}, 比例 1:{len(negative)/n_positive:.1f}")
        
        return balanced


# =============================================================================
# 第五部分：Stacking集成模型模块
# =============================================================================

class StackingModel:
    """Stacking集成模型"""
    
    def __init__(self, n_splits: int = 5):
        """
        初始化Stacking模型
        
        Args:
            n_splits: 交叉验证折数
        """
        self.n_splits = n_splits
        self.base_models = {}
        self.meta_model = None
        self.scaler = StandardScaler()
        
        # 初始化基模型
        self._init_base_models()
        
    def _init_base_models(self):
        """初始化基模型"""
        # XGBoost
        self.base_models['xgboost'] = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            verbosity=0
        )
        
        # LightGBM
        self.base_models['lightgbm'] = lgb.LGBMClassifier(
            n_estimators=300,
            num_leaves=31,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            verbose=-1
        )
        
        # CatBoost
        self.base_models['catboost'] = CatBoostClassifier(
            iterations=300,
            depth=5,
            learning_rate=0.05,
            l2_leaf_reg=3,
            random_seed=42,
            verbose=0
        )
        
    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """
        训练Stacking模型
        
        Args:
            X: 特征矩阵
            y: 标签
        """
        print("\n训练Stacking模型...")
        
        # 使用交叉验证生成OOF预测
        tscv = TimeSeriesSplit(n_splits=self.n_splits)
        
        # 存储OOF预测
        oof_predictions = np.zeros((len(X), len(self.base_models)))
        
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            print(f"  Fold {fold + 1}/{self.n_splits}...")
            
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
            
            for i, (name, model) in enumerate(self.base_models.items()):
                # 训练模型
                model.fit(X_train, y_train)
                
                # 生成验证集预测
                oof_predictions[val_idx, i] = model.predict_proba(X_val)[:, 1]
        
        # 训练元模型
        print("  训练元模型...")
        self.meta_model = LogisticRegression(random_state=42, max_iter=1000)
        self.meta_model.fit(oof_predictions, y)
        
        # 在全部数据上重新训练基模型
        print("  重新训练基模型...")
        for name, model in self.base_models.items():
            model.fit(X, y)
        
        print("✓ Stacking模型训练完成")
        
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """
        预测概率
        
        Args:
            X: 特征矩阵
            
        Returns:
            预测概率数组
        """
        # 获取基模型预测
        base_predictions = np.zeros((len(X), len(self.base_models)))
        
        for i, (name, model) in enumerate(self.base_models.items()):
            base_predictions[:, i] = model.predict_proba(X)[:, 1]
        
        # 元模型预测
        final_prediction = self.meta_model.predict_proba(base_predictions)[:, 1]
        
        return final_prediction
    
    def get_feature_importance(self, feature_names: List[str]) -> pd.DataFrame:
        """获取特征重要性"""
        importance_dict = {}
        
        for name, model in self.base_models.items():
            if hasattr(model, 'feature_importances_'):
                importance_dict[name] = model.feature_importances_
        
        if not importance_dict:
            return pd.DataFrame()
        
        # 计算平均重要性
        avg_importance = np.mean(list(importance_dict.values()), axis=0)
        
        importance_df = pd.DataFrame({
            'feature': feature_names,
            'importance': avg_importance
        }).sort_values('importance', ascending=False)
        
        return importance_df


# =============================================================================
# 第六部分：回测引擎模块
# =============================================================================

class BacktestEngine:
    """回测引擎 - 时间序列滚动验证"""
    
    def __init__(self, initial_train_months: int = 24, retrain_months: int = 6,
                 top_percentile: float = 0.10):
        """
        初始化回测引擎
        
        Args:
            initial_train_months: 初始训练集月数
            retrain_months: 重新训练间隔月数
            top_percentile: 选股百分位
        """
        self.initial_train_months = initial_train_months
        self.retrain_months = retrain_months
        self.top_percentile = top_percentile
        
        self.results = []
        self.daily_signals = []
        
    def run_backtest(self, all_data: Dict[str, pd.DataFrame], 
                     feature_cols: List[str]) -> pd.DataFrame:
        """
        运行回测
        
        Args:
            all_data: 所有股票数据字典
            feature_cols: 特征列名
            
        Returns:
            回测结果DataFrame
        """
        print("\n" + "="*60)
        print("开始时间序列滚动回测")
        print("="*60)
        
        # 合并所有数据
        combined_data = self._combine_data(all_data)
        
        # 确定回测时间范围
        start_date = combined_data.index.min() + pd.DateOffset(months=self.initial_train_months)
        end_date = combined_data.index.max()
        
        # 生成回测时间点
        retrain_dates = self._generate_retrain_dates(start_date, end_date)
        
        print(f"回测期间: {start_date.date()} ~ {end_date.date()}")
        print(f"重新训练次数: {len(retrain_dates)}")
        
        # 滚动训练和预测
        for i, (train_end, test_start, test_end) in enumerate(retrain_dates):
            print(f"\n第 {i+1} 轮: 训练截止 {train_end.date()}, 测试 {test_start.date()} ~ {test_end.date()}")
            
            # 准备训练数据
            train_data = combined_data[combined_data.index <= train_end]
            test_data = combined_data[(combined_data.index >= test_start) & 
                                      (combined_data.index <= test_end)]
            
            if len(train_data) < 1000 or len(test_data) < 100:
                print("  ⚠ 数据量不足，跳过")
                continue
            
            # 训练模型
            model = StackingModel()
            
            # 准备特征和标签
            X_train = train_data[feature_cols].fillna(0)
            y_train = train_data['limit_up']
            
            # 处理无穷大值
            X_train = X_train.replace([np.inf, -np.inf], 0)
            
            model.fit(X_train, y_train)
            
            # 预测
            X_test = test_data[feature_cols].fillna(0)
            X_test = X_test.replace([np.inf, -np.inf], 0)
            
            predictions = model.predict_proba(X_test)
            
            # 生成信号
            test_data = test_data.copy()
            test_data['pred_prob'] = predictions
            
            # 每日选股
            for date in test_data.index.unique():
                day_data = test_data[test_data.index == date]
                
                if len(day_data) == 0:
                    continue
                
                # 选择Top N%
                threshold = day_data['pred_prob'].quantile(1 - self.top_percentile)
                signals = (day_data['pred_prob'] >= threshold).astype(int)
                
                day_signals = day_data[['code', 'close', 'next_return', 'pred_prob']].copy()
                day_signals['signal'] = signals
                day_signals['date'] = date
                
                self.daily_signals.append(day_signals)
        
        # 汇总结果
        if self.daily_signals:
            self.results = pd.concat(self.daily_signals)
            print(f"\n✓ 回测完成，共 {len(self.results)} 条信号记录")
        
        return self.results
    
    def _combine_data(self, all_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """合并所有股票数据"""
        dfs = []
        for code, df in all_data.items():
            df = df.copy()
            df['code'] = code
            dfs.append(df)
        
        combined = pd.concat(dfs).sort_index()
        return combined
    
    def _generate_retrain_dates(self, start_date: pd.Timestamp, 
                                 end_date: pd.Timestamp) -> List[Tuple]:
        """生成重新训练时间点"""
        dates = []
        current = start_date
        
        while current < end_date:
            train_end = current
            test_start = current + pd.DateOffset(days=1)
            test_end = current + pd.DateOffset(months=self.retrain_months)
            
            if test_end > end_date:
                test_end = end_date
            
            dates.append((train_end, test_start, test_end))
            current = current + pd.DateOffset(months=self.retrain_months)
        
        return dates


# =============================================================================
# 第七部分：评估指标模块
# =============================================================================

class Evaluator:
    """评估器 - 计算各项回测指标"""
    
    def __init__(self, risk_free_rate: float = 0.03):
        """
        初始化评估器
        
        Args:
            risk_free_rate: 无风险利率（年化）
        """
        self.risk_free_rate = risk_free_rate
        
    def compute_metrics(self, results: pd.DataFrame) -> Dict:
        """
        计算评估指标
        
        Args:
            results: 回测结果DataFrame
            
        Returns:
            指标字典
        """
        metrics = {}
        
        # 获取有持仓的日期
        holdings = results[results['signal'] == 1]
        
        if len(holdings) == 0:
            print("⚠ 没有持仓记录")
            return metrics
        
        # 计算每日收益
        daily_returns = holdings.groupby('date')['next_return'].mean()
        
        # 1. Precision@Top10%
        # 使用预测概率最高的10%作为正例
        top_10_pct = results.groupby('date').apply(
            lambda x: x.nlargest(max(1, int(len(x) * 0.1)), 'pred_prob')
        ).reset_index(drop=True)
        
        if 'limit_up' in top_10_pct.columns:
            precision = top_10_pct['limit_up'].mean()
            metrics['precision_top10'] = precision
        else:
            metrics['precision_top10'] = 0
        
        # 2. Recall
        if 'limit_up' in holdings.columns:
            total_limits = results['limit_up'].sum()
            if total_limits > 0:
                recall = holdings['limit_up'].sum() / total_limits
                metrics['recall'] = recall
            else:
                metrics['recall'] = 0
        else:
            metrics['recall'] = 0
        
        # 3. F1-Score
        precision = metrics.get('precision_top10', 0)
        recall = metrics.get('recall', 0)
        if precision + recall > 0:
            metrics['f1_score'] = 2 * precision * recall / (precision + recall)
        else:
            metrics['f1_score'] = 0
        
        # 4. Sharpe Ratio
        if len(daily_returns) > 1:
            excess_returns = daily_returns - self.risk_free_rate / 252
            sharpe = excess_returns.mean() / excess_returns.std() * np.sqrt(252)
            metrics['sharpe_ratio'] = sharpe
        else:
            metrics['sharpe_ratio'] = 0
        
        # 5. 最大回撤
        cumulative_returns = (1 + daily_returns).cumprod()
        rolling_max = cumulative_returns.expanding().max()
        drawdown = cumulative_returns / rolling_max - 1
        metrics['max_drawdown'] = drawdown.min()
        
        # 6. 年化收益
        total_return = cumulative_returns.iloc[-1] - 1 if len(cumulative_returns) > 0 else 0
        n_years = len(daily_returns) / 252
        if n_years > 0:
            metrics['annual_return'] = (1 + total_return) ** (1 / n_years) - 1
        else:
            metrics['annual_return'] = 0
        
        # 7. 胜率
        win_rate = (daily_returns > 0).mean()
        metrics['win_rate'] = win_rate
        
        # 8. 盈亏比
        avg_win = daily_returns[daily_returns > 0].mean() if (daily_returns > 0).any() else 0
        avg_loss = abs(daily_returns[daily_returns < 0].mean()) if (daily_returns < 0).any() else 1
        metrics['profit_loss_ratio'] = avg_win / avg_loss if avg_loss > 0 else 0
        
        return metrics
    
    def compute_benchmark_returns(self, all_data: Dict[str, pd.DataFrame],
                                   start_date: pd.Timestamp, 
                                   end_date: pd.Timestamp) -> pd.Series:
        """
        计算基准收益（等权持有所有股票）
        
        Args:
            all_data: 所有股票数据
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            基准每日收益序列
        """
        daily_returns = []
        
        for date in pd.date_range(start_date, end_date, freq='B'):
            returns = []
            for code, df in all_data.items():
                if date in df.index:
                    idx = df.index.get_loc(date)
                    if idx > 0:
                        ret = df['close'].iloc[idx] / df['close'].iloc[idx-1] - 1
                        returns.append(ret)
            
            if returns:
                daily_returns.append({
                    'date': date,
                    'benchmark_return': np.mean(returns)
                })
        
        if daily_returns:
            benchmark_df = pd.DataFrame(daily_returns).set_index('date')
            return benchmark_df['benchmark_return']
        else:
            return pd.Series(dtype=float)
    
    def print_report(self, metrics: Dict) -> None:
        """打印回测报告"""
        print("\n" + "="*60)
        print("回测评估报告")
        print("="*60)
        
        print(f"\n【收益指标】")
        print(f"  年化收益率: {metrics.get('annual_return', 0):.2%}")
        print(f"  最大回撤: {metrics.get('max_drawdown', 0):.2%}")
        print(f"  Sharpe比率: {metrics.get('sharpe_ratio', 0):.3f}")
        
        print(f"\n【选股指标】")
        print(f"  Precision@Top10%: {metrics.get('precision_top10', 0):.4f}")
        print(f"  Recall: {metrics.get('recall', 0):.4f}")
        print(f"  F1-Score: {metrics.get('f1_score', 0):.4f}")
        
        print(f"\n【交易指标】")
        print(f"  胜率: {metrics.get('win_rate', 0):.2%}")
        print(f"  盈亏比: {metrics.get('profit_loss_ratio', 0):.2f}")
        
        print("="*60)


# =============================================================================
# 第八部分：可视化模块
# =============================================================================

class Visualizer:
    """可视化类"""
    
    def __init__(self, output_dir: str = './output'):
        """
        初始化可视化器
        
        Args:
            output_dir: 输出目录
        """
        self.output_dir = output_dir
        import os
        os.makedirs(output_dir, exist_ok=True)
        
    def plot_cumulative_returns(self, strategy_returns: pd.Series,
                                 benchmark_returns: pd.Series,
                                 save_path: str = None) -> None:
        """
        绘制累计收益曲线
        
        Args:
            strategy_returns: 策略收益序列
            benchmark_returns: 基准收益序列
            save_path: 保存路径
        """
        fig, axes = plt.subplots(2, 1, figsize=(14, 10))
        
        # 累计收益曲线
        cum_strategy = (1 + strategy_returns).cumprod()
        cum_benchmark = (1 + benchmark_returns).cumprod()
        
        axes[0].plot(cum_strategy.index, cum_strategy.values, 
                     label='策略', color='#2E86AB', linewidth=2)
        axes[0].plot(cum_benchmark.index, cum_benchmark.values, 
                     label='基准(等权)', color='#A23B72', linewidth=2, linestyle='--')
        
        axes[0].set_title('累计收益曲线对比', fontsize=14, fontweight='bold')
        axes[0].set_ylabel('累计收益率')
        axes[0].legend(loc='upper left')
        axes[0].grid(True, alpha=0.3)
        axes[0].axhline(y=1, color='gray', linestyle='-', alpha=0.5)
        
        # 回撤曲线
        rolling_max = cum_strategy.expanding().max()
        drawdown = cum_strategy / rolling_max - 1
        
        axes[1].fill_between(drawdown.index, drawdown.values, 0, 
                             color='#A23B72', alpha=0.3, label='回撤')
        axes[1].plot(drawdown.index, drawdown.values, 
                     color='#A23B72', linewidth=1)
        
        axes[1].set_title('策略回撤', fontsize=14, fontweight='bold')
        axes[1].set_ylabel('回撤幅度')
        axes[1].legend(loc='lower left')
        axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        if save_path is None:
            save_path = f'{self.output_dir}/cumulative_returns.png'
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"✓ 累计收益曲线已保存至: {save_path}")
    
    def plot_feature_importance(self, importance_df: pd.DataFrame,
                                 top_n: int = 20,
                                 save_path: str = None) -> None:
        """
        绘制特征重要性图
        
        Args:
            importance_df: 特征重要性DataFrame
            top_n: 显示前N个特征
            save_path: 保存路径
        """
        if importance_df.empty:
            print("⚠ 没有特征重要性数据")
            return
        
        top_features = importance_df.head(top_n)
        
        fig, ax = plt.subplots(figsize=(10, 8))
        
        bars = ax.barh(range(len(top_features)), top_features['importance'].values,
                       color='#2E86AB', alpha=0.8)
        
        ax.set_yticks(range(len(top_features)))
        ax.set_yticklabels(top_features['feature'].values)
        ax.set_xlabel('重要性')
        ax.set_title(f'Top {top_n} 特征重要性', fontsize=14, fontweight='bold')
        ax.invert_yaxis()
        ax.grid(True, alpha=0.3, axis='x')
        
        plt.tight_layout()
        
        if save_path is None:
            save_path = f'{self.output_dir}/feature_importance.png'
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"✓ 特征重要性图已保存至: {save_path}")
    
    def plot_prediction_distribution(self, results: pd.DataFrame,
                                      save_path: str = None) -> None:
        """
        绘制预测概率分布图
        
        Args:
            results: 回测结果
            save_path: 保存路径
        """
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        
        # 预测概率分布
        axes[0].hist(results['pred_prob'].values, bins=50, 
                     color='#2E86AB', alpha=0.7, edgecolor='white')
        axes[0].set_title('预测概率分布', fontsize=14, fontweight='bold')
        axes[0].set_xlabel('预测概率')
        axes[0].set_ylabel('频数')
        axes[0].grid(True, alpha=0.3)
        
        # 按信号分组的概率分布
        if 'signal' in results.columns:
            signal_1 = results[results['signal'] == 1]['pred_prob']
            signal_0 = results[results['signal'] == 0]['pred_prob']
            
            axes[1].hist(signal_1.values, bins=30, alpha=0.6, 
                        label='买入信号', color='#2E86AB')
            axes[1].hist(signal_0.values, bins=30, alpha=0.6, 
                        label='空仓信号', color='#A23B72')
            axes[1].set_title('买入vs空仓的概率分布', fontsize=14, fontweight='bold')
            axes[1].set_xlabel('预测概率')
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        if save_path is None:
            save_path = f'{self.output_dir}/prediction_distribution.png'
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"✓ 预测分布图已保存至: {save_path}")


# =============================================================================
# 主程序
# =============================================================================

def main():
    """主函数"""
    print("="*60)
    print("集成学习选股 - 涨停预测回测框架")
    print("="*60)
    
    # 1. 初始化各模块
    print("\n[1/7] 初始化数据获取器...")
    fetcher = DataFetcher(start_date='2020-01-01', end_date='2025-12-31')
    
    print("\n[2/7] 获取股票数据...")
    # 使用完整股票池：沪深300 + 中证500 + 中证1000 + 科创板 + 创业板
    stock_pool = fetcher.get_full_stock_pool()
    all_data = fetcher.fetch_all_data(stock_pool)
    
    if not all_data:
        print("⚠ 未获取到数据，程序结束")
        return
    
    print("\n[3/7] 计算特征...")
    feature_engineer = FeatureEngineer()
    prefilter = PreFilter(volume_threshold=2.0, vol_squeeze_percentile=0.2)
    label_gen = LabelGenerator(
        limit_threshold=0.098,      # 主板10%涨跌幅
        star_threshold=0.198,       # 科创板20%涨跌幅
        chinext_threshold=0.198     # 创业板20%涨跌幅
    )
    
    # 处理每只股票
    processed_data = {}
    for code, df in tqdm(all_data.items(), desc="处理特征"):
        # 计算特征
        df = feature_engineer.compute_all_features(df)
        
        # 应用前置过滤
        df = prefilter.apply_filter(df)
        
        # 生成标签（传入股票代码以判断板块和对应涨停阈值）
        df = label_gen.generate_labels(df, code=code)
        
        processed_data[code] = df
    
    print("\n[4/7] 准备训练数据...")
    # 合并所有数据，只保留通过过滤的样本
    all_processed = []
    for code, df in processed_data.items():
        df_filtered = df[df['filter_pass'] == True].copy()
        if len(df_filtered) > 0:
            all_processed.append(df_filtered)
    
    if not all_processed:
        print("⚠ 没有通过过滤的样本，尝试使用全部数据...")
        for code, df in processed_data.items():
            all_processed.append(df)
    
    combined_df = pd.concat(all_processed)
    
    # 平衡样本
    combined_df = label_gen.balance_samples(combined_df, ratio=3.0)
    
    # 定义特征列
    feature_cols = [col for col in combined_df.columns 
                    if col not in ['open', 'high', 'low', 'close', 'volume', 'amount',
                                  'code', 'limit_up', 'next_return', 'filter_pass',
                                  'volume_shock_flag', 'vol_squeeze_flag', 
                                  'bb_width_percentile', 'date']]
    
    print(f"特征数量: {len(feature_cols)}")
    print(f"样本数量: {len(combined_df)}")
    print(f"正样本比例: {combined_df['limit_up'].mean():.4f}")
    
    print("\n[5/7] 运行回测...")
    backtest = BacktestEngine(
        initial_train_months=24,
        retrain_months=6,
        top_percentile=0.10
    )
    results = backtest.run_backtest(processed_data, feature_cols)
    
    if results.empty:
        print("⚠ 回测没有产生结果")
        return
    
    print("\n[6/7] 计算评估指标...")
    evaluator = Evaluator()
    metrics = evaluator.compute_metrics(results)
    evaluator.print_report(metrics)
    
    print("\n[7/7] 生成可视化...")
    visualizer = Visualizer(output_dir='./output')
    
    # 计算策略收益
    strategy_returns = results[results['signal'] == 1].groupby('date')['next_return'].mean()
    
    # 计算基准收益
    start_date = results['date'].min() if 'date' in results.columns else results.index.min()
    end_date = results['date'].max() if 'date' in results.columns else results.index.max()
    benchmark_returns = evaluator.compute_benchmark_returns(processed_data, start_date, end_date)
    
    # 绘图
    if len(strategy_returns) > 0 and len(benchmark_returns) > 0:
        visualizer.plot_cumulative_returns(strategy_returns, benchmark_returns)
    
    # 输出最近30个交易日的预测
    print("\n" + "="*60)
    print("最近30个交易日预测结果")
    print("="*60)
    
    if 'date' in results.columns:
        recent_dates = sorted(results['date'].unique())[-30:]
        recent_results = results[results['date'].isin(recent_dates)]
    else:
        recent_results = results.tail(30 * 10)  # 假设每天10只股票
    
    output_cols = ['code', 'pred_prob', 'next_return']
    if 'date' in recent_results.columns:
        output_cols.insert(1, 'date')
    if 'signal' in recent_results.columns:
        output_cols.append('signal')
    
    available_cols = [col for col in output_cols if col in recent_results.columns]
    print(recent_results[available_cols].head(30).to_string(index=False))
    
    # 保存结果
    results.to_csv('./output/backtest_results.csv', index=False)
    print("\n✓ 回测结果已保存至: ./output/backtest_results.csv")
    
    # 关闭连接
    fetcher.close()
    
    print("\n" + "="*60)
    print("回测完成!")
    print("="*60)


if __name__ == '__main__':
    main()
