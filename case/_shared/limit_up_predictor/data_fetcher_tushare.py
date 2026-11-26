#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据获取模块 - TuShare 备用数据源
当 AKShare 被限流时使用
支持沪深300+中证500+中证1000+科创板+创业板
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime
import time
import os
import sys

# 敏感配置统一入口（app_secrets）：环境变量 > ~/.config/stock-dashboard/secrets.json
for _sp in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'case', '_lib'),
            _cp('/Coze/Drive/股市监控画布/stock-dashboard/case/_lib')):
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
try:
    import app_secrets as _app_secrets
except Exception:  # noqa: BLE001
    _app_secrets = None


def _secret(dotted, *env_names, default=''):
    for _n in env_names:
        _v = os.environ.get(_n)
        if _v:
            return _v
    if _app_secrets is None:
        return default
    try:
        return _app_secrets.get(dotted) or default
    except Exception:  # noqa: BLE001
        return default


try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    print("警告: tushare 未安装，备用数据源不可用")


class DataFetcherTuShare:
    """使用 TuShare Pro API 获取数据"""
    
    def __init__(self, start_date: str = '2023-01-01', end_date: str = None, token: str = None):
        """
        初始化数据获取器
        
        Args:
            start_date: 开始日期
            end_date: 结束日期，默认为今天
            token: TuShare Pro token，默认从环境变量 TUSHARE_TOKEN 读取
        """
        if not TUSHARE_AVAILABLE:
            raise RuntimeError("tushare 未安装，请运行: pip install tushare")
        
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        
        # 格式化日期为 TuShare 格式 YYYYMMDD
        self.start_date_ts = self.start_date.replace('-', '')
        self.end_date_ts = self.end_date.replace('-', '')
        
        # 获取 token
        if token is None:
            token = _secret('tushare.token', 'TUSHARE_TOKEN')
        
        if not token:
            raise ValueError("TuShare token 未配置，请设置环境变量 TUSHARE_TOKEN 或传入 token 参数")
        
        ts.set_token(token)
        self.pro = ts.pro_api()
        
        # 限流控制
        self.min_interval = 0.3  # 最小调用间隔（秒）
        self.last_call_time = 0
        
    def _rate_limit(self):
        """限流控制"""
        current_time = time.time()
        elapsed = current_time - self.last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call_time = time.time()
    
    def get_full_stock_pool(self) -> List[str]:
        """
        获取完整股票池：沪深300 + 中证500 + 中证1000 + 科创板 + 创业板
        
        Returns:
            去重后的股票代码列表，格式如 ['sh600000', 'sz000001', ...]
        """
        all_codes = set()
        
        # 沪深300
        try:
            self._rate_limit()
            df300 = self.pro.index_weight(index_code='399300.SZ', start_date=self.end_date_ts, end_date=self.end_date_ts)
            if df300 is not None and len(df300) > 0:
                codes_300 = [self._convert_code(c) for c in df300['con_code'].astype(str)]
                print(f"  沪深300: {len(codes_300)} 只")
                all_codes.update(codes_300)
        except Exception as e:
            print(f"  沪深300获取失败: {e}")
        
        # 中证500
        try:
            self._rate_limit()
            df500 = self.pro.index_weight(index_code='399905.SZ', start_date=self.end_date_ts, end_date=self.end_date_ts)
            if df500 is not None and len(df500) > 0:
                codes_500 = [self._convert_code(c) for c in df500['con_code'].astype(str)]
                print(f"  中证500: {len(codes_500)} 只")
                all_codes.update(codes_500)
        except Exception as e:
            print(f"  中证500获取失败: {e}")
        
        # 中证1000
        try:
            self._rate_limit()
            df1000 = self.pro.index_weight(index_code='000852.SH', start_date=self.end_date_ts, end_date=self.end_date_ts)
            if df1000 is not None and len(df1000) > 0:
                codes_1000 = [self._convert_code(c) for c in df1000['con_code'].astype(str)]
                print(f"  中证1000: {len(codes_1000)} 只")
                all_codes.update(codes_1000)
        except Exception as e:
            print(f"  中证1000获取失败: {e}")
        
        result = sorted(all_codes)
        
        # 统计各板块
        star_count = sum(1 for c in result if c.startswith('sh688'))
        chinext_count = sum(1 for c in result if c.startswith('sz300') or c.startswith('sz301'))
        print(f"  科创板(688): {star_count} 只")
        print(f"  创业板(300/301): {chinext_count} 只")
        print(f"  合计（去重）: {len(result)} 只")
        
        return result
    
    def _convert_code(self, code: str) -> str:
        """
        转换股票代码格式
        
        Args:
            code: TuShare 格式，如 '600000.SH'
            
        Returns:
            腾讯格式代码，如 'sh600000'
        """
        # 移除后缀
        pure_code = code.split('.')[0].zfill(6)
        if pure_code.startswith('6'):
            return f'sh{pure_code}'
        else:
            return f'sz{pure_code}'
    
    @staticmethod
    def get_stock_board(code: str) -> str:
        """
        根据股票代码判断所属板块
        
        Args:
            code: 股票代码，如 'sh600000' 或 'sz300001'
            
        Returns:
            板块名称: 'star'(科创板), 'chinext'(创业板), 'main'(主板)
        """
        pure_code = code[2:] if len(code) > 2 else code
        if pure_code.startswith('688'):
            return 'star'
        elif pure_code.startswith('300') or pure_code.startswith('301'):
            return 'chinext'
        else:
            return 'main'
    
    def get_stock_data(self, code: str) -> Optional[pd.DataFrame]:
        """
        获取单只股票的日线数据（前复权）
        
        Args:
            code: 股票代码，格式如 'sh600000'
            
        Returns:
            DataFrame包含 OHLCV 数据
        """
        try:
            # 转换为 TuShare 格式
            pure_code = code[2:] if len(code) > 2 else code
            if pure_code.startswith('6'):
                ts_code = f"{pure_code}.SH"
            else:
                ts_code = f"{pure_code}.SZ"
            
            self._rate_limit()
            
            # 获取日线数据
            df = self.pro.daily(ts_code=ts_code, start_date=self.start_date_ts, end_date=self.end_date_ts)
            
            if df is None or len(df) == 0:
                return None
            
            # 获取复权因子
            self._rate_limit()
            adj_df = self.pro.adj_factor(ts_code=ts_code, start_date=self.start_date_ts, end_date=self.end_date_ts)
            
            if adj_df is not None and len(adj_df) > 0:
                # 合并复权因子
                df = df.merge(adj_df[['trade_date', 'adj_factor']], on='trade_date', how='left')
                df['adj_factor'] = df['adj_factor'].ffill()
                
                # 计算前复权价格
                latest_adj = df['adj_factor'].iloc[0]  # 最新复权因子
                if latest_adj > 0:
                    adj_ratio = df['adj_factor'] / latest_adj
                    for col in ['open', 'high', 'low', 'close']:
                        df[col] = df[col] * adj_ratio
            else:
                print(f"  警告: {code} 复权因子获取失败，使用未复权数据")
            
            # 转换日期格式
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.rename(columns={'trade_date': 'date'})
            
            # 重命名列
            df = df[['date', 'open', 'high', 'low', 'close', 'vol']]
            df = df.rename(columns={'vol': 'volume'})
            
            # 设置索引
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            
            # 计算成交额
            df['amount'] = df['volume'] * df['close']
            
            # 过滤无效数据
            df = df[df['volume'] > 0]
            df = df[df['close'] > 0]
            
            return df
            
        except Exception as e:
            # print(f"获取 {code} 数据失败: {e}")
            return None
    
    def fetch_all_data(self, stock_pool: List[str]) -> Dict[str, pd.DataFrame]:
        """
        批量获取股票数据
        
        Args:
            stock_pool: 股票池列表
            
        Returns:
            字典，key为股票代码，value为DataFrame
        """
        from tqdm import tqdm
        
        print(f"\n开始获取 {len(stock_pool)} 只股票数据 (TuShare)...")
        print(f"预计耗时: {len(stock_pool) * self.min_interval / 60:.1f} 分钟")
        
        all_data = {}
        failed_codes = []
        
        for code in tqdm(stock_pool, desc="获取数据"):
            df = self.get_stock_data(code)
            if df is not None and len(df) > 100:  # 至少100个交易日数据
                all_data[code] = df
            else:
                failed_codes.append(code)
        
        print(f"✓ 成功获取 {len(all_data)} 只股票数据")
        if failed_codes:
            print(f"  失败: {len(failed_codes)} 只")
        
        return all_data


if __name__ == '__main__':
    # 测试
    token = _secret('tushare.token', 'TUSHARE_TOKEN')
    if not token:
        print("错误: 请设置环境变量 TUSHARE_TOKEN")
        print("  export TUSHARE_TOKEN='your_token_here'")
        exit(1)
    
    fetcher = DataFetcherTuShare(start_date='2023-01-01', token=token)
    
    print("="*60)
    print("测试 TuShare 数据获取")
    print("="*60)
    
    # 获取股票池
    stock_pool = fetcher.get_full_stock_pool()
    print(f"\n股票池大小: {len(stock_pool)}")
    
    # 测试获取几只股票
    test_codes = stock_pool[:3]
    print(f"\n测试获取 {len(test_codes)} 只股票...")
    
    for code in test_codes:
        df = fetcher.get_stock_data(code)
        if df is not None:
            print(f"{code}: {len(df)} 天数据, 最新收盘 {df['close'].iloc[-1]:.2f}")
        else:
            print(f"{code}: 获取失败")
