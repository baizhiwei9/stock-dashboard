#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一数据获取层（多级回退）
==========================
数据源优先级：
  0. DataHub（data_hub 统一入口：多源并行 + 列/单位统一）
  1. 腾讯行情（DataFetcherTencent，内部已 DataHub 优先）
  2. TuShare Pro
  3. Coze Skill
  4. 腾讯爬虫（直接 HTTP）

当某一级数据源失败时，自动切换到下一级。
所有数据缺失/异常都会记录在 data_issues 中，最终体现在报告里。
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import time
import os
import json
import requests

from data_fetcher_tencent import DataFetcherTencent
from data_fetcher_tushare import DataFetcherTuShare, TUSHARE_AVAILABLE, _secret
from data_fetcher_skill import DataFetcherSkill, SKILL_AVAILABLE


class TencentCrawlerFetcher:
    """
    第三数据源：直接爬取腾讯自选股行情接口获取日线数据
    当 AKShare 和 TuShare 都不可用时使用
    """
    
    def __init__(self, start_date: str = '2023-01-01', end_date: str = None):
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        self.kline_url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    
    def get_stock_data(self, code: str) -> Optional[pd.DataFrame]:
        """
        通过腾讯行情接口获取单只股票日线数据
        
        Args:
            code: 腾讯格式代码，如 'sh600000'
        """
        try:
            params = {
                'param': f'{code},day,{self.start_date},{self.end_date},1000,qfq'
            }
            r = requests.get(self.kline_url, params=params, timeout=15)
            data = r.json()
            
            if 'data' not in data or code not in data['data']:
                return None
            
            stock_data = data['data'][code]
            
            if 'qfqday' in stock_data:
                days = stock_data['qfqday']
            elif 'day' in stock_data:
                days = stock_data['day']
            else:
                return None
            
            if not days or len(days) == 0:
                return None
            
            days_clean = [row[:6] for row in days]
            df = pd.DataFrame(days_clean, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
            
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df['amount'] = df['volume'] * df['close']
            
            df = df[df['volume'] > 0]
            df = df[df['close'] > 0]
            
            return df
            
        except Exception:
            return None
    
    def fetch_all_data(self, stock_pool: List[str]) -> Dict[str, pd.DataFrame]:
        """批量获取"""
        from tqdm import tqdm
        
        print(f"\n开始获取 {len(stock_pool)} 只股票数据 (腾讯爬虫)...")
        
        all_data = {}
        failed_codes = []
        
        for code in tqdm(stock_pool, desc="腾讯爬虫获取"):
            df = self.get_stock_data(code)
            if df is not None and len(df) > 100:
                all_data[code] = df
            else:
                failed_codes.append(code)
        
        print(f"✓ 腾讯爬虫获取: {len(all_data)} 只成功, {len(failed_codes)} 只失败")
        return all_data


class UnifiedDataFetcher:
    """
    统一数据获取器，多级自动切换：
    DataHub → 腾讯行情 → TuShare → Coze Skill → 腾讯爬虫
    """
    
    def __init__(self, start_date: str = '2023-01-01', end_date: str = None,
                 tushare_token: str = None, enable_tushare: bool = True):
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        
        # 第一数据源：腾讯行情（主）
        self.primary_fetcher = DataFetcherTencent(start_date=start_date, end_date=self.end_date)
        
        # 第二数据源：TuShare
        self.tushare_fetcher = None
        self.enable_tushare = enable_tushare and TUSHARE_AVAILABLE
        if self.enable_tushare:
            try:
                token = tushare_token or _secret('tushare.token', 'TUSHARE_TOKEN')
                if token:
                    self.tushare_fetcher = DataFetcherTuShare(
                        start_date=start_date, end_date=self.end_date, token=token)
                    print("✓ TuShare 备用数据源已启用")
                else:
                    print("⚠ TuShare token 未配置，备用数据源不可用")
            except Exception as e:
                print(f"⚠ TuShare 初始化失败: {e}")
        
        # 第三数据源：Coze Skill
        self.skill_fetcher = None
        self.enable_skill = SKILL_AVAILABLE
        if self.enable_skill:
            try:
                self.skill_fetcher = DataFetcherSkill(start_date=start_date, end_date=self.end_date)
                print("✓ Coze Skill 第三数据源已启用")
            except Exception as e:
                print(f"⚠ Coze Skill 初始化失败: {e}")
        
        # 第四数据源：腾讯爬虫
        self.crawler_fetcher = TencentCrawlerFetcher(start_date=start_date, end_date=self.end_date)
        print("✓ 腾讯爬虫 第四数据源已启用")
        
        # 连续失败计数和当前活跃数据源
        self.consecutive_failures = 0
        self.max_failures_before_switch = 5
        self.current_source = 'primary'  # 'primary' / 'tushare' / 'crawler'
        
        # 统计
        self.stats = {
            'primary_success': 0, 'primary_failure': 0,
            'tushare_success': 0, 'tushare_failure': 0,
            'skill_success': 0, 'skill_failure': 0,
            'crawler_success': 0, 'crawler_failure': 0,
            'switch_count': 0,
        }
        
        # 数据问题追踪
        self.data_issues = []
    
    def get_full_stock_pool(self) -> List[str]:
        """获取完整股票池"""
        print("\n获取股票池...")
        pool = self.primary_fetcher.get_full_stock_pool()
        self.data_issues.extend(self.primary_fetcher.get_data_issues())
        return pool
    
    def get_stock_names_batch(self, codes: List[str]) -> Dict[str, str]:
        """批量获取股票名称"""
        return self.primary_fetcher.get_stock_names_batch(codes)
    
    def get_stock_name(self, code: str) -> str:
        """获取单只股票名称"""
        return self.primary_fetcher.get_stock_name(code)
    
    def get_stock_data(self, code: str) -> Optional[pd.DataFrame]:
        """
        获取单只股票数据，三级回退
        """
        # 按当前活跃数据源顺序尝试
        source_order = self._get_source_order()
        
        for source_name, fetcher in source_order:
            df = fetcher.get_stock_data(code)
            if df is not None and len(df) > 0:
                self.stats[f'{source_name}_success'] += 1
                self.consecutive_failures = 0
                if source_name != self.current_source:
                    self.stats['switch_count'] += 1
                    self.current_source = source_name
                return df
            else:
                self.stats[f'{source_name}_failure'] += 1
        
        # 全部失败
        self.consecutive_failures += 1
        self.data_issues.append(f"{code} 四级数据源均获取失败")
        return None
    
    def _get_source_order(self) -> List[Tuple[str, object]]:
        """根据当前状态决定数据源尝试顺序"""
        sources = []
        
        if self.current_source == 'crawler':
            sources.append(('crawler', self.crawler_fetcher))
            if self.skill_fetcher:
                sources.append(('skill', self.skill_fetcher))
            if self.tushare_fetcher:
                sources.append(('tushare', self.tushare_fetcher))
            sources.append(('primary', self.primary_fetcher))
        elif self.current_source == 'skill':
            sources.append(('skill', self.skill_fetcher))
            sources.append(('crawler', self.crawler_fetcher))
            if self.tushare_fetcher:
                sources.append(('tushare', self.tushare_fetcher))
            sources.append(('primary', self.primary_fetcher))
        elif self.current_source == 'tushare':
            sources.append(('tushare', self.tushare_fetcher))
            if self.skill_fetcher:
                sources.append(('skill', self.skill_fetcher))
            sources.append(('primary', self.primary_fetcher))
            sources.append(('crawler', self.crawler_fetcher))
        else:
            sources.append(('primary', self.primary_fetcher))
            if self.tushare_fetcher:
                sources.append(('tushare', self.tushare_fetcher))
            if self.skill_fetcher:
                sources.append(('skill', self.skill_fetcher))
            sources.append(('crawler', self.crawler_fetcher))
        
        return sources
    
    def fetch_all_data(self, stock_pool: List[str]) -> Dict[str, pd.DataFrame]:
        """批量获取：DataHub 批量优先，剩下的逐只走回退链"""
        from tqdm import tqdm
        
        print(f"\n开始获取 {len(stock_pool)} 只股票数据...")
        print(f"  数据源回退顺序: DataHub → 腾讯行情 → TuShare → Coze Skill → 腾讯爬虫")
        
        all_data = {}
        failed_codes = []
        
        # 批量通道：DataFetcherTencent 内部已是「DataHub 批量 + 腾讯逐只兜底」，
        # 这里先整批交给它，只把没拿到的交给逐只回退链 —— 否则 1200 只逐只请求，
        # 光 DataHub 单只往返就够把 30 分钟超时吃满。
        try:
            all_data = self.primary_fetcher.fetch_all_data(stock_pool)
            self.stats['primary_success'] += len(all_data)
            self.stats['primary_failure'] += len(stock_pool) - len(all_data)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ 批量通道失败，全部走逐只回退: {e}")
            all_data = {}
        
        pending = [c for c in stock_pool if c not in all_data]
        for code in tqdm(pending, desc="回退取数"):
            df = self.get_stock_data(code)
            if df is not None and len(df) > 100:
                all_data[code] = df
            else:
                failed_codes.append(code)
                if code not in [iss for iss in self.data_issues if code in iss]:
                    self.data_issues.append(f"{code} 数据不足（<100天）")
        
        print(f"\n✓ 成功获取 {len(all_data)} 只股票数据")
        if failed_codes:
            print(f"  失败: {len(failed_codes)} 只")
        
        self._print_stats()
        return all_data
    
    def _print_stats(self):
        """打印统计"""
        print("\n数据获取统计:")
        print(f"  腾讯行情: 成功 {self.stats['primary_success']}, 失败 {self.stats['primary_failure']}")
        print(f"  TuShare:  成功 {self.stats['tushare_success']}, 失败 {self.stats['tushare_failure']}")
        print(f"  Coze Skill: {self.stats['skill_success']}, 失败 {self.stats['skill_failure']}")
        print(f"  腾讯爬虫: 成功 {self.stats['crawler_success']}, 失败 {self.stats['crawler_failure']}")
        print(f"  数据源切换: {self.stats['switch_count']} 次")
        
        total_s = self.stats['primary_success'] + self.stats['tushare_success'] + self.stats['skill_success'] + self.stats['crawler_success']
        total_f = self.stats['primary_failure'] + self.stats['tushare_failure'] + self.stats['skill_failure'] + self.stats['crawler_failure']
        if total_s + total_f > 0:
            print(f"  总成功率: {total_s/(total_s+total_f)*100:.1f}%")
        
        if self.data_issues:
            print(f"\n⚠ 数据问题 ({len(self.data_issues)} 条):")
            for issue in self.data_issues[:10]:
                print(f"  - {issue}")
            if len(self.data_issues) > 10:
                print(f"  ... 还有 {len(self.data_issues)-10} 条")
    
    def get_data_issues(self) -> List[str]:
        """获取所有数据问题"""
        return self.data_issues.copy()
    
    def get_stats(self) -> Dict:
        return self.stats.copy()


def create_fetcher(start_date: str = '2023-01-01', end_date: str = None,
                   tushare_token: str = None) -> UnifiedDataFetcher:
    return UnifiedDataFetcher(
        start_date=start_date, end_date=end_date,
        tushare_token=tushare_token, enable_tushare=True)


if __name__ == '__main__':
    print("="*60)
    print("测试统一数据获取层（三级回退）")
    print("="*60)
    
    fetcher = create_fetcher(start_date='2023-01-01')
    
    stock_pool = fetcher.get_full_stock_pool()
    print(f"\n股票池大小: {len(stock_pool)}")
    
    test_codes = stock_pool[:5]
    
    names = fetcher.get_stock_names_batch(test_codes)
    print(f"\n股票名称:")
    for code, name in names.items():
        print(f"  {code}: {name}")
    
    print(f"\n测试获取数据...")
    for code in test_codes:
        df = fetcher.get_stock_data(code)
        name = names.get(code, '?')
        if df is not None:
            print(f"  {code}({name}): {len(df)} 天, 最新 {df['close'].iloc[-1]:.2f}")
        else:
            print(f"  {code}({name}): 获取失败")
    
    fetcher._print_stats()
