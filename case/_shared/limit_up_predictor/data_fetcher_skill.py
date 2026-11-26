#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据获取模块 - 使用 Coze 内置 Skill（同花顺/腾讯等数据接口）
作为第三级数据源，当 AKShare 和 TuShare 都不可用时使用

支持多个 Coze Skill:
- 同花顺 iFinD (infindV2)
- 普通行情 (simple-*)
"""

import subprocess
import json
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime


# Coze Skill 路径
SKILL_DIR = Path("/root/.coze/agents/7649998561689780486/workspace/.agents/skills/同花顺咨询技能")
CLI_WRAPPER = SKILL_DIR / "bin" / "_cli_wrapper.py"

# 检查是否可用
SKILL_AVAILABLE = CLI_WRAPPER.exists()


class DataFetcherSkill:
    """使用 Coze 内置 Skill 获取数据（第三数据源）"""
    
    def __init__(self, start_date: str = '2023-01-01', end_date: str = None):
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        self.name_cache = {}
        self.data_issues = []
    
    def _convert_code_to_skill(self, code: str) -> str:
        """转换代码格式: sh600000 -> 600000.SH"""
        if len(code) <= 2:
            return code
        pure_code = code[2:]
        market = code[:2]
        if market == 'sh':
            return f"{pure_code}.SH"
        elif market == 'sz':
            return f"{pure_code}.SZ"
        return code
    
    def _convert_code_from_skill(self, code: str) -> str:
        """转换代码格式: 600000.SH -> sh600000"""
        if '.' not in code:
            return code
        pure_code, market = code.split('.')
        market = market.lower()
        return f"{market}{pure_code}"
    
    def _call_skill(self, operation: str, params: List[str], timeout: int = 30) -> Optional[Dict]:
        """调用 Coze Skill"""
        if not SKILL_AVAILABLE:
            return None
        
        try:
            cmd = ['python3', str(CLI_WRAPPER), 'call', operation] + params
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(SKILL_DIR)
            )
            
            if result.returncode != 0:
                return None
            
            if result.stdout:
                return json.loads(result.stdout)
            return None
        except Exception as e:
            self.data_issues.append(f"Coze Skill调用失败: {e}")
            return None
    
    def get_stock_data(self, code: str) -> Optional[pd.DataFrame]:
        """获取单只股票日线数据"""
        skill_code = self._convert_code_to_skill(code)
        
        # 计算需要的天数（考虑交易日）
        start = datetime.strptime(self.start_date, '%Y-%m-%d')
        end = datetime.strptime(self.end_date, '%Y-%m-%d')
        days_needed = (end - start).days * 2  # 多取一些
        
        params = [
            f'--param=code={skill_code}',
            '--param=period=day',
            '--param=fq=qfq',
            f'--param=count={min(days_needed, 2000)}'
        ]
        
        data = self._call_skill('simple-history-kline', params)
        
        if not data or 'data' not in data:
            return None
        
        try:
            records = data['data']
            if not records:
                return None
            
            df = pd.DataFrame(records)
            
            # 确保有必要的列
            required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
            for col in required_cols:
                if col not in df.columns:
                    return None
            
            # 转换数据类型
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            
            # 过滤日期范围
            df = df[df.index >= self.start_date]
            df = df[df.index <= self.end_date]
            
            # 计算成交额（如果没有）
            if 'amount' not in df.columns:
                df['amount'] = df['volume'] * df['close']
            
            # 过滤无效数据
            df = df[df['volume'] > 0]
            df = df[df['close'] > 0]
            
            return df
        except Exception as e:
            self.data_issues.append(f"Coze Skill解析数据失败 {code}: {e}")
            return None
    
    def get_stock_name(self, code: str) -> str:
        """获取单只股票名称"""
        if code in self.name_cache:
            return self.name_cache[code]
        
        skill_code = self._convert_code_to_skill(code)
        params = [f'--param=code={skill_code}']
        
        data = self._call_skill('simple-quote', params)
        
        if data and 'name' in data:
            name = data['name']
            self.name_cache[code] = name
            return name
        
        return code[2:] if len(code) > 2 else code
    
    def get_stock_names_batch(self, codes: List[str]) -> Dict[str, str]:
        """批量获取股票名称"""
        names = {}
        for code in codes:
            name = self.get_stock_name(code)
            names[code] = name
        return names
    
    def get_full_stock_pool(self) -> List[str]:
        """获取完整股票池（科创板专用）"""
        all_codes = []
        
        # 尝试通过搜索获取科创板股票
        for prefix in ['688']:
            params = [f'--param=keyword={prefix}']
            data = self._call_skill('simple-search', params)
            
            if data and 'items' in data:
                for item in data['items']:
                    market = item.get('market', '')
                    pure_code = item.get('code', '')
                    if market and pure_code:
                        code = f"{market}{pure_code}"
                        if code.startswith('sh688'):
                            all_codes.append(code)
        
        return all_codes
    
    def fetch_all_data(self, stock_pool: List[str]) -> Dict[str, pd.DataFrame]:
        """批量获取股票数据"""
        from tqdm import tqdm
        
        print(f"\n开始获取 {len(stock_pool)} 只股票数据 (Coze Skill)...")
        
        all_data = {}
        failed_codes = []
        
        for code in tqdm(stock_pool, desc="Coze Skill获取"):
            df = self.get_stock_data(code)
            if df is not None and len(df) > 100:
                all_data[code] = df
            else:
                failed_codes.append(code)
        
        print(f"✓ Coze Skill获取: {len(all_data)} 只成功, {len(failed_codes)} 只失败")
        return all_data
    
    def get_data_issues(self) -> List[str]:
        """获取数据问题"""
        return self.data_issues.copy()


if __name__ == '__main__':
    print("=" * 60)
    print("测试 Coze Skill 数据获取器")
    print("=" * 60)
    
    fetcher = DataFetcherSkill(start_date='2024-01-01')
    
    # 测试获取单只股票
    test_code = 'sh600519'
    print(f"\n测试获取 {test_code} 数据...")
    df = fetcher.get_stock_data(test_code)
    if df is not None:
        print(f"  ✓ 获取成功: {len(df)} 天")
        print(f"  最新: {df.index[-1]}, 收盘 {df['close'].iloc[-1]:.2f}")
    else:
        print(f"  ✗ 获取失败")
    
    # 测试获取名称
    print(f"\n测试获取名称...")
    name = fetcher.get_stock_name(test_code)
    print(f"  {test_code}: {name}")
