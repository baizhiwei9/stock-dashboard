#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存周期 & 信贷周期自动化监控系统 v2.0
Author: Cycle Monitor System
Version: 2.0 - 修复C006 bug、新增爬虫、周期预测
"""

import os
import sys
import sqlite3
import logging
import time
import re
import random
import json
import argparse
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import warnings

import pandas as pd
import numpy as np
import akshare as ak
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings('ignore')
requests.packages.urllib3.disable_warnings()

# 配置日志
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'cycle_monitor.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 常量定义 - 使用相对路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(PROJECT_DIR)  # 主对话根目录
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'cycle.db')
OUTPUT_PATH = os.path.join(PROJECT_DIR, 'outputs')
HISTORY_YEARS = 5

# 添加根目录到路径，用于导入 feishu 工具
sys.path.insert(0, ROOT_DIR)

# ==================== DataHub 统一取数（禁止直连）====================
# 所有宏观/利率数据统一走 data_hub，禁止脚本内直连 akshare
_HUB_PATHS = [
    os.path.join(ROOT_DIR, 'codeact', 'scripts'),
    '/opt/coze/.fuse_drive/dataHub_三级体系',
]
for _hp in _HUB_PATHS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
        break
try:
    import data_hub as dh
    logger.info(f"[DataHub] 已接入，版本路径={[p for p in _HUB_PATHS if os.path.isdir(os.path.join(p,'data_hub'))][0]}")
except Exception as _e:
    dh = None
    logger.error(f"[DataHub] 导入失败，将回退直连: {_e}")

# 指标配置
INDICATOR_CONFIG = {
    # 信贷周期指标
    'C001': {'name': '社融滚动12月增量同比(旧口径)', 'source': 'akshare', 'func': 'get_social_financing_yoy',
             '替代说明': '实为12个月滚动增量合计的同比，非存量同比，波动-10%~+16%噪音大，已降级为辅助' },
    'C002': {'name': 'M2同比', 'source': 'akshare', 'func': 'get_m2_yoy'},
    'C003': {'name': '人民币贷款余额同比', 'source': 'akshare', 'func': 'get_loan_balance_yoy'},
    'C004': {'name': '企业中长期贷款增量', 'source': 'crawler_pbc', 'func': 'get_corp_mid_long_loan', '说明': '月度新增规模(亿元)'},
    'C005': {'name': '居民部门贷款增量', 'source': 'crawler_pbc', 'func': 'get_household_loan', '说明': '月度新增规模(亿元)'},
    'C006': {'name': '政府债券融资同比', 'source': 'akshare', 'func': 'get_gov_bond_yoy'},
    'C007': {'name': 'DR007月度均值', 'source': 'akshare', 'func': 'get_dr007_avg', '替代说明': '使用SHIBOR 1W替代'},
    'C008': {'name': '贷款加权平均利率', 'source': 'akshare', 'func': 'get_lending_rate', '替代说明': '使用LPR 1Y替代'},
    
    # 库存周期指标
    'I001': {'name': '工业企业产成品存货同比(旧·PPI占位)', 'source': '替代指标',
             '替代说明': '数值=PPI同比副本，非真实存货数据，已被I009替代，不参与周期判定'},
    'I002': {'name': '工业企业营业收入同比(旧)', 'source': '替代指标', '替代说明': '已被I010替代，使用工业增加值同比近似(I008)'},
    'I003': {'name': '工业企业利润总额同比', 'source': 'crawler_nbs', 'func': 'get_industrial_profit'},
    'I004': {'name': 'PPI同比', 'source': 'akshare', 'func': 'get_ppi_yoy'},
    'I005': {'name': 'PPIRM同比', 'source': 'akshare', 'func': 'get_ppirm_yoy', '替代说明': '使用企业商品价格指数替代'},
    'I006': {'name': '制造业PMI新订单指数', 'source': 'crawler_nbs', 'func': 'get_pmi_new_order'},
    'I007': {'name': '制造业PMI产成品库存指数', 'source': 'crawler_nbs', 'func': 'get_pmi_finished_inventory'},
    'I008': {'name': '工业增加值同比', 'source': 'akshare', 'func': 'get_industrial_production_yoy'},
    'I009': {'name': '产成品存货同比', 'source': 'crawler_nbs', 'func': 'get_inventory_yoy', '说明': '统计局规模以上工业企业产成品存货同比'},
    'I010': {'name': '营业收入同比', 'source': 'crawler_nbs', 'func': 'get_revenue_yoy', '说明': '统计局规模以上工业企业营业收入同比'},
    
    # 交叉验证指标
    'V001': {'name': '贷款余额同比-M2剪刀差', 'source': 'calculated', 'formula': 'C003-C002'},
    'V002': {'name': 'PMI新订单-产成品库存差', 'source': 'calculated', 'formula': 'I006-I007'},
    'V003': {'name': '出口同比', 'source': 'akshare', 'func': 'get_export_yoy'},
}


def init_database():
    """初始化数据库"""
    logger.info("初始化数据库...")
    os.makedirs(os.path.join(PROJECT_DIR, 'data'), exist_ok=True)
    conn = db_connect()
    cursor = conn.cursor()
    
    # 创建经济指标表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS economic_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_id VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            value FLOAT,
            ma3 FLOAT,
            source VARCHAR(50) NOT NULL,
            data_status VARCHAR(20) DEFAULT 'original',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(indicator_id, date) ON CONFLICT REPLACE
        )
    ''')
    
    # 创建周期判断表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cycle_judgments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL UNIQUE,
            credit_cycle VARCHAR(50) NOT NULL,
            inventory_cycle VARCHAR(50) NOT NULL,
            credit_duration_months INTEGER,
            inventory_duration_months INTEGER,
            comprehensive_judgment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建数据更新日志表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS update_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            indicators_updated INTEGER,
            status VARCHAR(20)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("数据库初始化完成")


def random_delay(min_sec=1, max_sec=3):
    """随机延迟"""
    time.sleep(random.uniform(min_sec, max_sec))


def retry_request(func, max_retries=3, delay=1, timeout=10):
    """带重试的请求"""
    for attempt in range(max_retries):
        try:
            result = func()
            return result
        except Exception as e:
            logger.warning(f"请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                random_delay(delay, delay * 2)
    return None


def safe_request(url: str, headers: dict = None, encoding='utf-8') -> Optional[BeautifulSoup]:
    """安全的HTTP请求"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
    }
    if headers:
        default_headers.update(headers)
    
    try:
        response = requests.get(url, headers=default_headers, timeout=10, verify=False)
        response.encoding = encoding
        return BeautifulSoup(response.text, 'lxml')
    except Exception as e:
        logger.error(f"请求失败 {url}: {e}")
        return None


def parse_chinese_date(date_str: str) -> str:
    """将中文日期转换为标准格式 YYYY-MM-01"""
    if pd.isna(date_str):
        return None
    
    date_str = str(date_str).strip()
    
    # 2025年05月份 -> 2025-05-01
    match = re.match(r'(\d{4})年(\d{1,2})月份?', date_str)
    if match:
        year, month = match.groups()
        return f"{year}-{int(month):02d}-01"
    
    # 201501 -> 2015-01-01
    match = re.match(r'^(\d{4})(\d{2})$', date_str)
    if match:
        year, month = match.groups()
        return f"{year}-{month}-01"
    
    # 2025-05 -> 2025-05-01
    match = re.match(r'(\d{4})-(\d{2})$', date_str)
    if match:
        return f"{match.group(1)}-{match.group(2)}-01"
    
    # 2025-05-01 -> 2025-05-01
    match = re.match(r'(\d{4}-\d{2}-\d{2})', date_str)
    if match:
        return match.group(1)
    
    return None


# ==================== akshare 数据获取函数 ====================

def get_m2_data() -> pd.DataFrame:
    """获取M2同比数据"""
    logger.info("获取M2同比数据...")
    try:
        df = dh.get_money_supply()
        df['date'] = df['月份'].apply(parse_chinese_date)
        df = df[['date', '货币和准货币(M2)-同比增长']].copy()
        df.columns = ['date', 'value']
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        df = df.dropna(subset=['date', 'value'])
        return df
    except Exception as e:
        logger.error(f"获取M2数据失败: {e}")
        return pd.DataFrame()


def get_social_financing_data() -> pd.DataFrame:
    """获取社会融资数据并计算同比"""
    logger.info("获取社会融资数据...")
    try:
        df = dh.get_social_financing()
        df['date'] = df['月份'].apply(parse_chinese_date)
        df['value'] = pd.to_numeric(df['社会融资规模增量'], errors='coerce')
        df = df.sort_values('date').reset_index(drop=True)
        df['rolling_12m'] = df['value'].rolling(window=12, min_periods=12).sum()
        df['value_yoy'] = df['rolling_12m'] / df['rolling_12m'].shift(12) - 1
        df['value_yoy'] = df['value_yoy'] * 100
        df_result = df[['date', 'value_yoy']].copy()
        df_result.columns = ['date', 'value']
        df_result = df_result.dropna(subset=['date', 'value'])
        return df_result
    except Exception as e:
        logger.error(f"获取社融数据失败: {e}")
        return pd.DataFrame()


def get_loan_balance_data() -> pd.DataFrame:
    """获取人民币贷款余额同比"""
    logger.info("获取人民币贷款数据...")
    try:
        df = dh.get_rmb_loan()
        df['date'] = df['月份'].apply(parse_chinese_date)
        # 用"累计人民币贷款-同比"作为贷款余额同比（而非"新增人民币贷款-同比"）
        df['value'] = pd.to_numeric(df['累计人民币贷款-同比'].str.replace('%', ''), errors='coerce')
        df = df[['date', 'value']].copy()
        df = df.dropna(subset=['date', 'value'])
        return df
    except Exception as e:
        logger.error(f"获取贷款数据失败: {e}")
        return pd.DataFrame()


def get_gov_bond_data() -> pd.DataFrame:
    """
    获取政府债券融资同比
    社融增量表没有直接的政府债券列，使用东方财富社融数据中的各分项计算
    政府债券 = 社融总量 - 人民币贷款 - 外币贷款 - 委托贷款 - 信托贷款 - 未贴现票据 - 企业债券 - 股票融资
    """
    logger.info("获取政府债券融资数据...")
    try:
        # 获取社融各分项数据
        df = dh.get_social_financing()
        df['date'] = df['月份'].apply(parse_chinese_date)
        
        # 转换为数值
        for col in df.columns:
            if col not in ['月份', 'date']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # 按日期排序
        df = df.sort_values('date').reset_index(drop=True)
        
        # 计算非政府债券分项总和（社融中实际没有政府债券，需要估算）
        # 用社融总量减去已知分项，剩余部分可近似为政府债券
        cols_to_subtract = [
            '其中-人民币贷款', '其中-外币贷款（折人民币）', '其中-委托贷款', 
            '其中-信托贷款', '其中-未贴现银行承兑汇票', 
            '其中-企业债券融资', '其中-非金融企业境内股票融资'
        ]
        
        # 计算剩余项（近似为政府债券+其他）
        available_cols = [c for c in cols_to_subtract if c in df.columns]
        df['other_items_sum'] = df[available_cols].sum(axis=1)
        df['gov_bond_proxy'] = df['社会融资规模增量'] - df['other_items_sum']
        
        # 计算12个月滚动累计
        df['rolling_12m'] = df['gov_bond_proxy'].rolling(window=12, min_periods=12).sum()
        df['value_yoy'] = df['rolling_12m'] / df['rolling_12m'].shift(12) - 1
        df['value_yoy'] = df['value_yoy'] * 100
        
        df_result = df[['date', 'value_yoy']].copy()
        df_result.columns = ['date', 'value']
        df_result = df_result.dropna(subset=['date', 'value'])
        
        return df_result
    except Exception as e:
        logger.error(f"获取政府债券数据失败: {e}")
        return pd.DataFrame()


def get_dr007_data() -> pd.DataFrame:
    """获取DR007月度均值（用SHIBOR 1W近似）"""
    logger.info("获取DR007数据(使用SHIBOR 1W)...")
    try:
        df = dh.get_shibor_all()
        df['date'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-01')
        monthly = df.groupby('date')['1W-定价'].mean().reset_index()
        monthly.columns = ['date', 'value']
        return monthly
    except Exception as e:
        logger.error(f"获取DR007数据失败: {e}")
        return pd.DataFrame()


def get_lending_rate_data() -> pd.DataFrame:
    """获取贷款加权平均利率（用LPR替代）"""
    logger.info("获取贷款利率数据(使用LPR)...")
    try:
        df = dh.get_lpr_history()
        df['date'] = pd.to_datetime(df['TRADE_DATE']).dt.strftime('%Y-%m-01')
        monthly = df.groupby('date')['LPR1Y'].mean().reset_index()
        monthly.columns = ['date', 'value']
        return monthly
    except Exception as e:
        logger.error(f"获取贷款利率数据失败: {e}")
        return pd.DataFrame()


def get_ppi_data() -> pd.DataFrame:
    """获取PPI同比数据"""
    logger.info("获取PPI数据...")
    try:
        df = dh.get_ppi()
        df['date'] = df['月份'].apply(parse_chinese_date)
        df['value'] = pd.to_numeric(df['当月同比增长'], errors='coerce')
        df = df[['date', 'value']].copy()
        df = df.dropna(subset=['date', 'value'])
        return df
    except Exception as e:
        logger.error(f"获取PPI数据失败: {e}")
        return pd.DataFrame()


def get_ppirm_data() -> pd.DataFrame:
    """获取PPIRM同比数据（用企业商品交易价格指数替代）"""
    logger.info("获取PPIRM数据(使用企业商品交易价格指数)...")
    try:
        df = dh.get_qyspjg()
        df['date'] = df['月份'].apply(parse_chinese_date)
        df['value'] = pd.to_numeric(df['总指数-同比增长'], errors='coerce')
        df = df[['date', 'value']].copy()
        df = df.dropna(subset=['date', 'value'])
        return df
    except Exception as e:
        logger.error(f"获取PPIRM数据失败: {e}")
        return pd.DataFrame()


def get_industrial_production_data() -> pd.DataFrame:
    """获取工业增加值同比"""
    logger.info("获取工业增加值数据...")
    try:
        df = dh.get_industrial_production_yoy()
        df['date'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-01')
        df['value'] = pd.to_numeric(df['今值'], errors='coerce')
        df = df[['date', 'value']].copy()
        df = df.dropna(subset=['date', 'value'])
        return df
    except Exception as e:
        logger.error(f"获取工业增加值数据失败: {e}")
        return pd.DataFrame()


def get_export_data() -> pd.DataFrame:
    """获取出口同比"""
    logger.info("获取出口数据...")
    try:
        df = dh.get_exports_yoy()
        df['date'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-01')
        df['value'] = pd.to_numeric(df['今值'], errors='coerce')
        df = df[['date', 'value']].copy()
        df = df.dropna(subset=['date', 'value'])
        return df
    except Exception as e:
        logger.error(f"获取出口数据失败: {e}")
        return pd.DataFrame()


# ==================== 央行数据爬虫 ====================

def get_pbc_stat_page_urls() -> List[Tuple[str, str]]:
    """获取央行金融统计数据报告页面链接列表"""
    logger.info("获取央行统计数据报告列表...")
    urls = []
    base_url = "http://www.pbc.gov.cn"
    list_url = f"{base_url}/diaochatongjisi/116219/116225/index.html"
    
    soup = safe_request(list_url)
    if not soup:
        return urls
    
    # 查找报告链接
    links = soup.find_all('a', href=True)
    for link in links:
        href = link.get('href', '')
        text = link.get_text(strip=True)
        if '金融统计数据' in text or '20' in text[:4]:
            if href.startswith('/'):
                full_url = base_url + href
            elif href.startswith('http'):
                full_url = href
            else:
                continue
            
            # 提取日期
            date_match = re.search(r'(\d{4})年(\d{1,2})月', text)
            if date_match:
                year, month = date_match.groups()
                date_str = f"{year}-{int(month):02d}-01"
                urls.append((date_str, full_url))
    
    logger.info(f"找到 {len(urls)} 个央行报告链接")
    return urls


def parse_pbc_report(url: str) -> Dict[str, Dict]:
    """
    解析央行金融统计数据报告
    返回: {'corp_mid_long': value, 'household': value, 'date': date_str}
    注意：报告中的"企（事）业单位"包含企业+机关团体，数字较大
    """
    soup = safe_request(url)
    if not soup:
        return {}
    
    result = {}
    text = soup.get_text()
    
    # 查找人民币贷款增加的那一行（包含分部门数据）
    loan_line_pattern = r'分部门看，([^。]+)。'
    loan_line_match = re.search(loan_line_pattern, text)
    if not loan_line_match:
        return result
    
    loan_text = loan_line_match.group(1)
    
    # 提取住户贷款（住户贷款减少xxxx亿元 或 住户贷款增加xxxx亿元）
    household_pattern = r'住户贷款[增加减损]+[\d\.]+[万亿亿元]+'
    household_match = re.search(household_pattern, loan_text)
    if household_match:
        household_text = household_match.group(0)
        # 提取数字
        num_match = re.search(r'([+-]?\d+\.?\d*)', household_text.replace('万亿', '0000').replace('亿元', ''))
        if num_match:
            val = float(num_match.group(1))
            # 如果是万亿单位，转换为亿
            if '万亿' in household_text:
                val = val * 10000
            result['household'] = val
    
    # 更精确地匹配企（事）业单位中长期贷款（在中长期贷款增加X万亿中）
    # 格式："企（事）业单位贷款增加X，其中...中长期贷款增加Y"
    corp_pattern = r'企[\(（]事[\)）]业单位贷款[^\n]*?中长期贷款[^\d]*([+-]?\d+\.?\d*)\s*([万亿亿万]+)'
    corp_match = re.search(corp_pattern, loan_text)
    if corp_match:
        val = float(corp_match.group(1))
        unit = corp_match.group(2)
        if '万' in unit:
            val = val * 10000
        result['corp_mid_long'] = val
    
    # 提取住户中长期贷款
    household_mid_pattern = r'住户贷款[^\n]*?中长期贷款[^\d]*([+-]?\d+\.?\d*)\s*([万亿亿万]+)'
    household_mid_match = re.search(household_mid_pattern, loan_text)
    if household_mid_match:
        val = float(household_mid_match.group(1))
        unit = household_mid_match.group(2)
        if '万' in unit:
            val = val * 10000
        result['household_mid_long'] = val
    
    return result


def get_corp_mid_long_loan() -> pd.DataFrame:
    """
    获取企业中长期贷款增量数据
    从央行金融统计数据报告爬取
    """
    logger.info("获取企业中长期贷款数据(央行报告)...")
    urls = get_pbc_stat_page_urls()
    
    all_data = []
    for date_str, url in urls[:24]:  # 最近24个月
        random_delay(1, 2)
        data = parse_pbc_report(url)
        if 'corp_mid_long' in data:
            all_data.append({
                'date': date_str,
                'value': data['corp_mid_long']
            })
            logger.info(f"  {date_str}: 企业中长期贷款 {data['corp_mid_long']:.0f} 亿元")
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    df = df.sort_values('date').reset_index(drop=True)
    
    # 计算同比（需要累计值，这里简化处理用增量趋势）
    # 实际上更准确的是获取余额数据，这里先用增量代替
    return df


def get_household_loan() -> pd.DataFrame:
    """
    获取居民部门贷款增量数据
    从央行金融统计数据报告爬取
    """
    logger.info("获取居民部门贷款数据(央行报告)...")
    urls = get_pbc_stat_page_urls()
    
    all_data = []
    for date_str, url in urls[:24]:  # 最近24个月
        random_delay(1, 2)
        data = parse_pbc_report(url)
        if 'household' in data:
            all_data.append({
                'date': date_str,
                'value': data['household']
            })
            logger.info(f"  {date_str}: 居民贷款 {data['household']:.0f} 亿元")
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    df = df.sort_values('date').reset_index(drop=True)
    return df


# ==================== 统计局PMI爬虫 ====================

def get_pmi_report_urls() -> List[Tuple[str, str]]:
    """获取统计局PMI报告页面链接"""
    logger.info("获取统计局PMI报告列表...")
    urls = []
    base_url = "https://www.stats.gov.cn/sj/zxfb/"
    
    soup = safe_request(base_url)
    if not soup:
        return urls
    
    # 查找所有链接
    links = soup.find_all('a', href=True)
    for link in links:
        href = link.get('href', '')
        text = link.get_text(strip=True)
        
        # PMI报告通常标题含"PMI"或"采购经理指数"或"采购经理"
        if 'PMI' in text.upper() or ('采购经理' in text) or ('采购经理指数' in text):
            if href.startswith('/'):
                full_url = 'https://www.stats.gov.cn' + href
            elif href.startswith('http'):
                full_url = href
            else:
                full_url = 'https://www.stats.gov.cn/sj/zxfb/' + href.lstrip('./')
            
            # 从文本中提取日期
            date_match = re.search(r'(\d{4})年(\d{1,2})月', text)
            if date_match:
                year, month = date_match.groups()
                date_str = f"{year}-{int(month):02d}-01"
                urls.append((date_str, full_url))
                logger.info(f"  找到PMI报告: {text[:40]}... -> {date_str}")
    
    logger.info(f"找到 {len(urls)} 个PMI报告链接")
    return urls


def parse_pmi_report(url: str) -> Dict[str, float]:
    """
    解析PMI报告页面
    返回: {'new_order': value, 'finished_inventory': value}
    """
    soup = safe_request(url)
    if not soup:
        return {}
    
    result = {}
    
    # 查找所有表格
    tables = soup.find_all('table')
    logger.info(f"  找到 {len(tables)} 个表格")
    
    for table_idx, table in enumerate(tables):
        try:
            # 手动解析表格
            rows = table.find_all('tr')
            if len(rows) < 3:
                continue
            
            # 获取表头 - PMI表格表头可能是多行的
            # 先尝试找到包含列名的行
            header_row_idx = -1
            headers = []
            
            for row_idx, row in enumerate(rows[:5]):  # 只在前5行中找表头
                cells = row.find_all(['th', 'td'])
                row_text = ' '.join([c.get_text(strip=True) for c in cells])
                
                # 优先查找包含"新订单"或"产成品库存"的行
                if '新订单' in row_text or '产成品库存' in row_text or '生产' in row_text:
                    header_row_idx = row_idx
                    headers = [c.get_text(strip=True) for c in cells]
                    break
            
            # 如果没找到，尝试其他方式
            if header_row_idx < 0:
                for row_idx, row in enumerate(rows[:5]):
                    cells = row.find_all(['th', 'td'])
                    row_text = ' '.join([c.get_text(strip=True) for c in cells])
                    if 'PMI' in row_text or '单位' in row_text:
                        header_row_idx = row_idx
                        headers = [c.get_text(strip=True) for c in cells]
            
            if not headers:
                continue
            
            # 解析数据行 - 数据从表头后一行开始
            data_start = header_row_idx + 1
            
            for row_idx, row in enumerate(rows[data_start:], data_start):
                cells = row.find_all(['th', 'td'])
                if len(cells) < 2:
                    continue
                
                row_data = [cell.get_text(strip=True) for cell in cells]
                
                # 第一列是日期
                date_text = row_data[0] if row_data else ''
                if '2026' not in date_text and '2025' not in date_text:
                    continue
                
                # 提取日期
                date_match = re.search(r'(\d{4})年(\d{1,2})月', date_text)
                if not date_match:
                    continue
                
                # 查找新订单列
                if table_idx == 0:
                    for col_idx, header in enumerate(headers):
                        if '新订单' in header:
                            if col_idx < len(row_data):
                                try:
                                    val = float(row_data[col_idx])
                                    result['new_order'] = val
                                    logger.info(f"  Table {table_idx}: 新订单={val}")
                                except:
                                    pass
                
                # 查找产成品库存列
                if table_idx == 1:
                    for col_idx, header in enumerate(headers):
                        if '产成品库存' in header or '产成品' in header:
                            if col_idx < len(row_data):
                                try:
                                    val = float(row_data[col_idx])
                                    result['finished_inventory'] = val
                                    logger.info(f"  Table {table_idx}: 产成品库存={val}")
                                except:
                                    pass
        except Exception as e:
            logger.debug(f"  解析表格 {table_idx} 失败: {e}")
            continue
    
    return result


def get_pmi_new_order() -> pd.DataFrame:
    """获取PMI新订单指数"""
    logger.info("获取PMI新订单数据...")
    urls = get_pmi_report_urls()
    
    all_data = []
    for date_str, url in urls[:13]:  # 最近12-13个月
        random_delay(1, 2)
        data = parse_pmi_report(url)
        if 'new_order' in data:
            all_data.append({
                'date': date_str,
                'value': data['new_order']
            })
            logger.info(f"  {date_str}: PMI新订单 {data['new_order']:.1f}")
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    df = df.sort_values('date').reset_index(drop=True)
    return df


def get_pmi_finished_inventory() -> pd.DataFrame:
    """获取PMI产成品库存指数"""
    logger.info("获取PMI产成品库存数据...")
    urls = get_pmi_report_urls()
    
    all_data = []
    for date_str, url in urls[:13]:  # 最近12-13个月
        random_delay(1, 2)
        data = parse_pmi_report(url)
        if 'finished_inventory' in data:
            all_data.append({
                'date': date_str,
                'value': data['finished_inventory']
            })
            logger.info(f"  {date_str}: PMI产成品库存 {data['finished_inventory']:.1f}")
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    df = df.sort_values('date').reset_index(drop=True)
    return df


# ==================== 统计局工业企业利润爬虫 ====================

def get_industrial_profit_urls() -> List[Tuple[str, str]]:
    """获取统计局工业企业利润报告链接"""
    logger.info("获取统计局工业企业利润报告列表...")
    urls = []
    base_url = "https://www.stats.gov.cn/sj/zxfb/"
    
    soup = safe_request(base_url)
    if not soup:
        return urls
    
    links = soup.find_all('a', href=True)
    for link in links:
        href = link.get('href', '')
        text = link.get_text(strip=True)
        
        if any(kw in text for kw in ['工业企业利润', '规模以上工业', '工业效益', '工业利润']):
            if href.startswith('/'):
                full_url = 'https://www.stats.gov.cn' + href
            elif href.startswith('http'):
                full_url = href
            else:
                full_url = 'https://www.stats.gov.cn/sj/zxfb/' + href.lstrip('./')
            
            date_match = re.search(r'(\d{4})年(\d{1,2})月', text)
            if date_match:
                year, month = date_match.groups()
                date_str = f"{year}-{int(month):02d}-01"
                urls.append((date_str, full_url))
                logger.info(f"  找到工业利润报告: {text[:40]}... -> {date_str}")
    
    logger.info(f"找到 {len(urls)} 个工业企业利润报告链接")
    return urls


def parse_industrial_profit_report(url: str) -> Dict[str, float]:
    """解析工业企业利润报告"""
    soup = safe_request(url)
    if not soup:
        return {}
    
    result = {}
    text = soup.get_text()
    
    # 提取利润总额同比 - 标题中通常包含
    profit_match = re.search(r'利润(?:增长|下降)\s*([\d.]+)%', text)
    if profit_match:
        val = float(profit_match.group(1))
        # 直接看正则匹配到的增长/下降
        start = profit_match.start()
        if '下降' in text[start:start+10]:
            val = -val
        result['profit'] = val
    
    # 提取营业收入同比
    rev_match = re.search(r'营业收入[\d.]+万亿?元.*?同比(?:增长|下降)\s*([\d.]+)%', text)
    if rev_match:
        val = float(rev_match.group(1))
        # 看正则匹配到的"同比"后面是增长还是下降
        start = rev_match.start()
        matched_text = text[start:rev_match.end()]
        if '下降' in matched_text:
            val = -val
        result['revenue'] = val
    
    # 提取产成品存货同比
    inv_match = re.search(r'产成品存货[\d.]+万亿?元.*?(?:增长|下降|增加|减少)\s*([\d.]+)%', text)
    if inv_match:
        val = float(inv_match.group(1))
        start = inv_match.start()
        matched_text = text[start:inv_match.end()]
        if '下降' in matched_text or '减少' in matched_text:
            val = -val
        result['inventory'] = val
    
    return result


def get_industrial_profit() -> pd.DataFrame:
    """获取工业企业利润总额同比"""
    logger.info("获取工业企业利润数据...")
    urls = get_industrial_profit_urls()
    
    all_data = []
    for date_str, url in urls[:24]:
        random_delay(1, 2)
        data = parse_industrial_profit_report(url)
        if 'profit' in data:
            all_data.append({
                'date': date_str,
                'value': data['profit']
            })
            logger.info(f"  {date_str}: 利润总额同比 {data['profit']:.1f}%")
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    df = df.sort_values('date').reset_index(drop=True)
    return df


def _crawl_all_industrial_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """从统计局工业企业利润报告批量爬取利润、存货、营收同比数据
    返回: (利润df, 存货df, 营收df)
    """
    logger.info("批量爬取统计局工业企业数据(利润/存货/营收)...")
    
    urls = []
    base_url = "https://www.stats.gov.cn/sj/zxfb/"
    
    # 翻页搜索利润报告（最多翻15页，约2年数据）
    for page in range(1, 16):
        page_url = f'{base_url}index_{page}.html' if page > 1 else base_url
        soup = safe_request(page_url)
        if not soup:
            break
        
        links = soup.find_all('a', href=True)
        for link in links:
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            if '利润' not in text or '工业' not in text:
                continue
            
            if href.startswith('/'):
                full_url = 'https://www.stats.gov.cn' + href
            elif href.startswith('http'):
                full_url = href
            else:
                full_url = 'https://www.stats.gov.cn/sj/zxfb/' + href.lstrip('./')
            
            # 提取月份
            date_match = re.search(r'(\d{4})年1?[—\-](\d{1,2})月份', text)
            if date_match:
                year, month = date_match.groups()
                date_str = f"{year}-{int(month):02d}-01"
                urls.append((date_str, full_url, text[:40]))
        
        random_delay(0.5, 1)
    
    # 去重
    seen = set()
    unique_urls = []
    for date_str, url, title in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append((date_str, url, title))
    
    logger.info(f"找到 {len(unique_urls)} 个工业企业利润报告")
    
    profit_data = []
    inventory_data = []
    revenue_data = []
    
    for date_str, url, title in unique_urls[:36]:
        random_delay(1, 2)
        data = parse_industrial_profit_report(url)
        
        if 'profit' in data:
            profit_data.append({'date': date_str, 'value': data['profit']})
        if 'inventory' in data:
            inventory_data.append({'date': date_str, 'value': data['inventory']})
        if 'revenue' in data:
            revenue_data.append({'date': date_str, 'value': data['revenue']})
        
        all_vals = []
        if 'profit' in data: all_vals.append(f"利润{data['profit']:.1f}%")
        if 'inventory' in data: all_vals.append(f"存货{data['inventory']:.1f}%")
        if 'revenue' in data: all_vals.append(f"营收{data['revenue']:.1f}%")
        if all_vals:
            logger.info(f"  {date_str}: {', '.join(all_vals)}")
    
    def to_df(data_list):
        if not data_list:
            return pd.DataFrame()
        df = pd.DataFrame(data_list)
        df = df.sort_values('date').reset_index(drop=True)
        return df
    
    return to_df(profit_data), to_df(inventory_data), to_df(revenue_data)


# 缓存上次爬取结果，避免重复请求
_industrial_data_cache = {'profit': None, 'inventory': None, 'revenue': None}

def _get_industrial_data_cached(data_type: str) -> pd.DataFrame:
    """获取缓存或重新爬取的工业企业数据"""
    global _industrial_data_cache
    if _industrial_data_cache[data_type] is not None:
        return _industrial_data_cache[data_type]
    
    profit_df, inv_df, rev_df = _crawl_all_industrial_data()
    _industrial_data_cache['profit'] = profit_df
    _industrial_data_cache['inventory'] = inv_df
    _industrial_data_cache['revenue'] = rev_df
    
    return _industrial_data_cache[data_type]


def get_inventory_yoy() -> pd.DataFrame:
    """获取产成品存货同比数据"""
    return _get_industrial_data_cached('inventory')


def get_revenue_yoy() -> pd.DataFrame:
    """获取营业收入同比数据"""
    return _get_industrial_data_cached('revenue')


# ==================== 数据处理函数 ====================

def clean_data(df: pd.DataFrame, indicator_id: str) -> pd.DataFrame:
    """数据清洗"""
    if df.empty:
        return df
    
    # 增量类指标（央行爬虫获取的贷款增量）数值可能很大，不用阈值过滤
    # 同比类指标用±100%阈值过滤
    # 替代指标（如I001用PPI、I002用工业增加值）也不用阈值
    if indicator_id in ['C004', 'C005', 'I001', 'I002']:
        pass  # 不做数值过滤
    else:
        df = df[df['value'].abs() <= 100]  # 同比类指标去除极端异常值
    
    df = df.sort_values('date')
    return df


def calculate_ma3(df: pd.DataFrame) -> pd.DataFrame:
    """计算3个月移动平均"""
    if df.empty or 'value' not in df.columns:
        return df
    
    if len(df) < 3:
        df['ma3'] = df['value']
        return df
    
    df = df.sort_values('date').copy()
    df['ma3'] = df['value'].rolling(window=3, min_periods=1).mean()
    return df



def db_connect(path=None, timeout=30):
    """统一的 SQLite 连接（适配云盘文件系统）

    /Coze/Drive 是云盘挂载，SQLite 默认的 delete 模式要在同目录反复创建并 fsync
    journal 文件，云盘对 fcntl 锁和 fsync 支持不稳定，会偶发 disk I/O error。
    改用内存 journal + 延长 busy_timeout 可规避。
    """
    conn = sqlite3.connect(path or DB_PATH, timeout=timeout)
    try:
        conn.execute('PRAGMA journal_mode=MEMORY')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=30000')
    except Exception as e:
        logger.warning(f'设置 PRAGMA 失败（不影响使用）: {e}')
    return conn


def db_commit_retry(conn, retries=3):
    """提交失败重试（云盘偶发抖动）"""
    last = None
    for i in range(1, retries + 1):
        try:
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            last = e
            logger.warning(f'commit 第 {i}/{retries} 次失败: {e}')
            if i < retries:
                time.sleep(1.5)
    logger.error(f'❌ commit 连续 {retries} 次失败: {last}')
    return False


def save_to_database(indicator_id: str, df: pd.DataFrame, source: str, data_status: str = 'original'):
    """保存数据到数据库"""
    if df.empty:
        logger.warning(f"{indicator_id}: 无数据可保存")
        return 0
    
    conn = db_connect()
    cursor = conn.cursor()
    
    count = 0
    for _, row in df.iterrows():
        cursor.execute('''
            INSERT OR REPLACE INTO economic_indicators 
            (indicator_id, date, value, ma3, source, data_status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (indicator_id, row['date'], row['value'], row.get('ma3', None), source, data_status))
        count += 1

    db_commit_retry(conn)
    conn.close()

    logger.info(f"{indicator_id}: 保存了 {count} 条数据")
    return count


def fetch_all_data() -> Dict[str, pd.DataFrame]:
    """获取所有指标数据"""
    data_dict = {}
    
    # akshare 数据获取
    akshare_funcs = {
        'C001': (get_social_financing_data, 3),
        'C002': (get_m2_data, 3),
        'C003': (get_loan_balance_data, 3),
        'C006': (get_gov_bond_data, 3),
        'C007': (get_dr007_data, 3),
        'C008': (get_lending_rate_data, 3),
        'I004': (get_ppi_data, 3),
        'I005': (get_ppirm_data, 3),
        'I008': (get_industrial_production_data, 3),
        'V003': (get_export_data, 3),
    }
    
    for key, (func, retries) in akshare_funcs.items():
        for attempt in range(retries):
            try:
                df = func()
                if df is not None and not df.empty and 'value' in df.columns:
                    data_dict[key] = df
                    break
                elif attempt < retries - 1:
                    logger.warning(f"{key}: 获取数据为空，尝试重试 ({attempt + 1}/{retries})")
                    time.sleep(2)
                else:
                    data_dict[key] = pd.DataFrame()
                    logger.warning(f"{key}: 获取数据失败，使用空DataFrame")
            except Exception as e:
                if attempt < retries - 1:
                    logger.warning(f"{key}: 获取失败 ({attempt + 1}/{retries}): {e}")
                    time.sleep(2)
                else:
                    data_dict[key] = pd.DataFrame()
                    logger.error(f"{key}: 获取数据异常: {e}")
        
        time.sleep(1)
    
    # 爬虫数据获取
    # 【2026-11-05 修复】三处严重缺陷：
    #   1) I009/I010 此前完全不在采集列表里 → 数据库永久停在 2026-03，
    #      库存周期判定实际用的是半年前数据（真实已到 2026-09）。
    #   2) I003 走 get_industrial_profit() 单独发现 URL，长期返回空；
    #      改走 _get_industrial_data_cached('profit')，与 I009/I010 共用一次爬取。
    #   3) 三者共用缓存，避免 _crawl_all_industrial_data 被重复触发 3 次（每次 20+ 请求）。
    crawler_funcs = {
        'C004': (get_corp_mid_long_loan, 'crawler_pbc'),
        'C005': (get_household_loan, 'crawler_pbc'),
        'I006': (get_pmi_new_order, 'crawler_nbs'),
        'I007': (get_pmi_finished_inventory, 'crawler_nbs'),
        'I003': (lambda: _get_industrial_data_cached('profit'), 'crawler_nbs'),
        'I009': (get_inventory_yoy, 'crawler_nbs'),
        'I010': (get_revenue_yoy, 'crawler_nbs'),
    }
    
    for key, (func, source) in crawler_funcs.items():
        for attempt in range(3):
            try:
                df = func()
                if df is not None and not df.empty and 'value' in df.columns:
                    data_dict[key] = df
                    break
                elif attempt < 2:
                    logger.warning(f"{key}: 爬取数据为空，尝试重试 ({attempt + 1}/3)")
                    time.sleep(2)
                else:
                    data_dict[key] = pd.DataFrame()
                    logger.warning(f"{key}: 爬取数据失败")
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"{key}: 爬取失败 ({attempt + 1}/3): {e}")
                    time.sleep(2)
                else:
                    data_dict[key] = pd.DataFrame()
                    logger.error(f"{key}: 爬取数据异常: {e}")
        
        time.sleep(random.uniform(2, 4))
    
    # 替代指标处理
    # I001 使用 I004(PPI同比) 作为代理
    if 'I004' in data_dict and not data_dict['I004'].empty:
        data_dict['I001'] = data_dict['I004'].copy()
        logger.info("I001: 使用PPI同比作为产成品存货代理")
    
    # I002 使用 I008(工业增加值同比) 作为代理
    if 'I008' in data_dict and not data_dict['I008'].empty:
        data_dict['I002'] = data_dict['I008'].copy()
        logger.info("I002: 使用工业增加值同比作为营收代理")
    
    # 数据清洗和计算MA3
    for key in data_dict:
        data_dict[key] = clean_data(data_dict[key], key)
        data_dict[key] = calculate_ma3(data_dict[key])
    
    return data_dict


# ==================== 周期判断函数 ====================

def determine_credit_cycle(conn: sqlite3.connect, months: int = 3) -> str:
    """判断信贷周期"""
    cursor = conn.cursor()

    # 【统一口径 2026-11-05】优先走 cycle_phase_analyzer（C003贷款余额同比 + C002 M2，均为存量同比）
    # 旧口径 C001 实为「社融滚动12月增量同比」，噪音过大，已降级
    try:
        from cycle_phase_analyzer import analyze_cycles
        _cred_trans, _, _, _ = analyze_cycles(DB_PATH)
        if _cred_trans:
            _phase, _start, _dur = _cred_trans[-1][1], _cred_trans[-1][0], _cred_trans[-1][2]
            if _phase in ('信贷扩张期', '政策收紧期', '信贷收缩期', '政策宽松期'):
                logger.info(f"信贷周期判断(统一口径): {_phase}（始于 {_start}，已持续 {_dur} 月）")
                return _phase
    except Exception as _e:
        logger.warning(f"cycle_phase_analyzer 调用失败，回退本地判断: {_e}")

    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            e1.date,
            e1.value as c001,
            e2.value as c002
        FROM economic_indicators e1
        JOIN economic_indicators e2 ON e1.date = e2.date AND e2.indicator_id = 'C002'
        WHERE e1.indicator_id = 'C001'
        ORDER BY e1.date DESC
        LIMIT ?
    ''', (months,))
    
    rows = cursor.fetchall()
    if len(rows) < months:
        logger.warning(f"周期判断数据不足: 只有 {len(rows)} 条记录")
        return "数据不足"
    
    c001_values = [r[1] for r in rows if r[1] is not None]
    c002_values = [r[2] for r in rows if r[2] is not None]
    
    if len(c001_values) < months or len(c002_values) < months:
        return "数据不足"
    
    c001_trend = np.mean(c001_values)
    c002_trend = np.mean(c002_values)
    
    logger.info(f"信贷周期判断: C001均值={c001_trend:.2f}%, C002均值={c002_trend:.2f}%")
    
    if c001_trend < 0 and c002_trend < 0:
        return "信贷收缩期"
    elif c001_trend < 0 and c002_trend > 0:
        return "政策宽松期"
    elif c001_trend > 0 and c002_trend > 0:
        return "信贷扩张期"
    else:
        return "政策收紧期"


def determine_inventory_cycle(conn: sqlite3.connect, months: int = 4) -> str:
    """判断库存周期 - 基于趋势方向（边际变化），与Wind/券商研报方法论一致
    
    库存周期四阶段判断依据（看同比值的边际变化方向，非绝对水平）：
    - 主动补库存：存货同比趋势↑ + 营收同比趋势↑
    - 被动补库存：存货同比趋势↑ + 营收同比趋势↓
    - 主动去库存：存货同比趋势↓ + 营收同比趋势↓
    - 被动去库存：存货同比趋势↓ + 营收同比趋势↑
    
    例如：存货同比从6.6%降到5.2%，虽然仍为正值，但趋势在下降→去库存方向。
    这与Wind/国金证券等券商的判断方法一致。
    
    判断方法：
    - 如果有≥2条I009/I010数据，用最近2个数据点的变动方向（对拐点最灵敏）
    - 否则回退到I004(PPI)+I008(工业增加值)代理，用前后半段均值差判断
    """
    cursor = conn.cursor()

    # 【统一口径 2026-11-05】优先走 cycle_phase_analyzer，保证报告/图/邮件三处判定完全一致
    # 原因：I009/I010 止于 2026-03、I008 止于 2025-08，本地判断只能用 8 个月前的旧数据；
    # analyzer 已用 PMI(I006+I007) 覆盖到最新月，是当前唯一有效口径。
    try:
        from cycle_phase_analyzer import analyze_cycles
        _, _inv_trans, _, _ = analyze_cycles(DB_PATH)
        if _inv_trans:
            _phase, _start, _dur = _inv_trans[-1][1], _inv_trans[-1][0], _inv_trans[-1][2]
            if _phase in ('主动补库存', '被动补库存', '主动去库存', '被动去库存'):
                logger.info(f"库存周期判断(统一口径): {_phase}（始于 {_start}，已持续 {_dur} 月）")
                return _phase
    except Exception as _e:
        logger.warning(f"cycle_phase_analyzer 调用失败，回退本地判断: {_e}")

    # 优先使用原始指标I009+I010
    i009_count = cursor.fetchone()[0]
    
    if i009_count >= 2:
        cursor.execute('''
            SELECT 
                e1.date,
                e1.value as inventory,
                e2.value as revenue
            FROM economic_indicators e1
            JOIN economic_indicators e2 ON e1.date = e2.date AND e2.indicator_id = 'I010'
            WHERE e1.indicator_id = 'I009'
            ORDER BY e1.date DESC
            LIMIT 2
        ''')
        
        rows = cursor.fetchall()
        if len(rows) >= 2:
            # rows按日期降序: [最新, 次新]
            inv_latest = rows[0][1]  # 最新值
            inv_prev = rows[1][1]    # 次新值
            rev_latest = rows[0][2]  # 最新值
            rev_prev = rows[1][2]    # 次新值
            
            if all(v is not None for v in [inv_latest, inv_prev, rev_latest, rev_prev]):
                inv_slope = inv_latest - inv_prev
                rev_slope = rev_latest - rev_prev
                
                inv_rising = inv_slope > 0
                rev_rising = rev_slope > 0
                
                logger.info(f"库存周期判断(原始趋势): 存货同比 {inv_prev:.2f}%→{inv_latest:.2f}% (变动{inv_slope:+.2f}), 营收同比 {rev_prev:.2f}%→{rev_latest:.2f}% (变动{rev_slope:+.2f})")
                
                if rev_rising and inv_rising:
                    return "主动补库存"
                elif not rev_rising and inv_rising:
                    return "被动补库存"
                elif not rev_rising and not inv_rising:
                    return "主动去库存"
                elif rev_rising and not inv_rising:
                    return "被动去库存"
    
    # 回退到替代指标I004(PPI同比)+I008(工业增加值同比)
    cursor.execute('''
        SELECT 
            e1.date,
            e1.value as iva,
            e2.value as ppi
        FROM economic_indicators e1
        JOIN economic_indicators e2 ON e1.date = e2.date AND e2.indicator_id = 'I004'
        WHERE e1.indicator_id = 'I008'
        ORDER BY e1.date DESC
        LIMIT ?
    ''', (months,))
    
    rows = cursor.fetchall()
    if len(rows) < months:
        logger.warning(f"库存周期判断数据不足: 只有 {len(rows)} 条记录")
        return "数据不足"
    
    iva_values = [r[1] for r in rows if r[1] is not None]
    ppi_values = [r[2] for r in rows if r[2] is not None]
    
    if len(iva_values) < months or len(ppi_values) < months:
        return "数据不足"
    
    iva_values = list(reversed(iva_values))
    ppi_values = list(reversed(ppi_values))
    
    half = months // 2
    rev_recent = np.mean(iva_values[-half:])
    rev_earlier = np.mean(iva_values[:half])
    inv_recent = np.mean(ppi_values[-half:])
    inv_earlier = np.mean(ppi_values[:half])
    
    inv_slope = inv_recent - inv_earlier
    rev_slope = rev_recent - rev_earlier
    
    inv_rising = inv_slope > 0
    rev_rising = rev_slope > 0
    
    logger.info(f"库存周期判断(替代趋势): PPI {inv_earlier:.2f}%→{inv_recent:.2f}% (斜率{inv_slope:+.2f}), 工业增加值 {rev_earlier:.2f}%→{rev_recent:.2f}% (斜率{rev_slope:+.2f})")
    
    if rev_rising and inv_rising:
        return "主动补库存"
    elif not rev_rising and inv_rising:
        return "被动补库存"
    elif not rev_rising and not inv_rising:
        return "主动去库存"
    elif rev_rising and not inv_rising:
        return "被动去库存"
    
    return "数据不足"


def get_cycle_duration(conn: sqlite3.connect, cycle_type: str, current_cycle: str) -> Tuple[int, Optional[str]]:
    """
    获取当前周期阶段已持续月数
    返回: (持续月数, 周期开始日期)
    """
    cursor = conn.cursor()
    
    if cycle_type == 'credit':
        table_col = 'credit_cycle'
    else:
        table_col = 'inventory_cycle'
    
    cursor.execute(f'''
        SELECT date, {table_col} FROM cycle_judgments
        ORDER BY date DESC
        LIMIT 50
    ''')
    
    rows = cursor.fetchall()
    if not rows:
        return 0, None
    
    # 找到当前阶段开始的位置
    start_date = None
    same_count = 0
    
    for i, (date, cycle) in enumerate(rows):
        if cycle == current_cycle:
            if i == 0:
                start_date = date
            same_count += 1
        else:
            if start_date:
                break
    
    if start_date:
        # 计算月数
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.now()
        months = (end.year - start.year) * 12 + (end.month - start.month)
        return months, start_date
    
    return same_count, None


def get_latest_value(conn: sqlite3.connect, indicator_id: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """获取最新值"""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT value, ma3, date FROM economic_indicators
        WHERE indicator_id = ?
        ORDER BY date DESC LIMIT 1
    ''', (indicator_id,))
    
    row = cursor.fetchone()
    if row:
        return row[0], row[1], row[2]
    return None, None, None


def calc_spread_ma3_trend(conn, id_a: str, id_b: str, n: int = 4):
    """计算两个指标之差（A-B）的最新值、3月均值、趋势

    V001/V002 这类派生指标原先在报告里 3月均值硬写成 N/A，用户看到一片 N/A 以为数据缺失。
    实际只要按同一日期对齐后逐期相减，再取最近 3 期均值即可。
    返回 (latest, ma3, trend)，无数据时返回 (None, None, None)。
    """
    cursor = conn.cursor()
    cursor.execute('''
        SELECT a.date, a.value - b.value AS spread
        FROM economic_indicators a
        JOIN economic_indicators b ON a.date = b.date AND b.indicator_id = ?
        WHERE a.indicator_id = ? AND a.value IS NOT NULL AND b.value IS NOT NULL
        ORDER BY a.date DESC LIMIT ?
    ''', (id_b, id_a, n))
    rows = cursor.fetchall()
    if not rows:
        return None, None, None
    vals = [r[1] for r in rows]          # 最新在前
    latest = vals[0]
    ma3 = sum(vals[:3]) / len(vals[:3])
    trend = '—'
    if len(vals) >= 2:
        if latest > vals[1] + 1e-9:
            trend = '↑上升'
        elif latest < vals[1] - 1e-9:
            trend = '↓下降'
        else:
            trend = '→持平'
    return latest, ma3, trend


def get_data_status(indicator_id: str) -> str:
    """获取数据状态标注"""
    config = INDICATOR_CONFIG.get(indicator_id, {})
    
    if '替代说明' in config:
        return "🔄替代指标"
    elif config.get('source') == 'crawler_pbc':
        return "✅央行爬虫"
    elif config.get('source') == 'crawler_nbs':
        return "✅统计局爬虫"
    elif config.get('source') == 'akshare':
        return "✅原始数据"
    else:
        return "⚠️不可得"


def get_trend(value: Optional[float], ma3: Optional[float]) -> str:
    """判断趋势"""
    if value is None or ma3 is None:
        return "N/A"
    
    diff = value - ma3
    if abs(diff) < 0.5:
        return "平稳"
    elif diff > 0:
        return "↑上升"
    else:
        return "↓下降"


# ==================== 周期尾声预测模块 ====================

def predict_cycle_ending(credit_cycle: str, inventory_cycle: str, conn: sqlite3.connect) -> str:
    """
    周期尾声预测
    优先使用cycle_phase_analyzer的阶段持续时间，回退到cycle_judgments表
    """
    from cycle_phase_analyzer import analyze_cycles, find_current_cycle
    
    prediction = []
    
    # 信贷周期统计规律
    credit_duration_avg = {
        '信贷扩张期': 16,   # 14-18个月
        '信贷收缩期': 12,   # 10-14个月
        '政策宽松期': 8,    # 6-10个月
        '政策收紧期': 10,   # 8-12个月
    }
    
    # 库存周期统计规律
    inventory_duration_avg = {
        '主动补库存': 14,   # 12-16个月
        '被动补库存': 8,    # 6-10个月
        '主动去库存': 12,   # 10-14个月
        '被动去库存': 10,   # 8-12个月
    }
    
    # 周期轮转顺序
    credit_transitions_map = {
        '信贷扩张期': '政策收紧期',
        '政策收紧期': '信贷收缩期',
        '信贷收缩期': '政策宽松期',
        '政策宽松期': '信贷扩张期',
    }
    
    inventory_transitions_map = {
        '主动补库存': '被动补库存',
        '被动补库存': '主动去库存',
        '主动去库存': '被动去库存',
        '被动去库存': '主动补库存',
    }
    
    # 从phase_analyzer获取当前阶段持续时间
    try:
        credit_trans, inv_trans, _, _ = analyze_cycles()
        cycle_info = find_current_cycle(credit_trans, inv_trans)
        credit_months = cycle_info.get('credit_phase_duration', 0)
        credit_start = cycle_info.get('credit_phase_start', None)
        inv_months = cycle_info.get('inv_phase_duration', 0)
        inv_start = cycle_info.get('inv_phase_start', None)
    except Exception:
        # 回退到数据库查询
        credit_months, credit_start = get_cycle_duration(conn, 'credit', credit_cycle)
        inv_months, inv_start = get_cycle_duration(conn, 'inventory', inventory_cycle)
    
    # 信贷周期预测
    if credit_cycle in credit_duration_avg:
        credit_avg = credit_duration_avg[credit_cycle]
        credit_remaining = credit_avg - credit_months
        
        prediction.append(f"### 信贷周期预测")
        prediction.append(f"- 当前阶段: **{credit_cycle}**")
        prediction.append(f"- 已持续: **{credit_months}** 个月")
        prediction.append(f"- 历史平均: **{credit_avg}** 个月")
        
        if credit_remaining <= 0:
            prediction.append(f"- ⚠️ **可能即将切换** (已超出平均 {abs(credit_remaining)} 个月)")
        else:
            prediction.append(f"- 预计还需: **{credit_remaining}** 个月可能进入下一阶段")
        
        if credit_cycle in credit_transitions_map:
            next_credit = credit_transitions_map[credit_cycle]
            prediction.append(f"- 下一阶段预告: **{next_credit}**")
    
    # 库存周期预测
    if inventory_cycle in inventory_duration_avg:
        inv_avg = inventory_duration_avg[inventory_cycle]
        inv_remaining = inv_avg - inv_months
        
        prediction.append(f"\n### 库存周期预测")
        prediction.append(f"- 当前阶段: **{inventory_cycle}**")
        prediction.append(f"- 已持续: **{inv_months}** 个月")
        prediction.append(f"- 历史平均: **{inv_avg}** 个月")
        
        if inv_remaining <= 0:
            prediction.append(f"- ⚠️ **可能即将切换** (已超出平均 {abs(inv_remaining)} 个月)")
        else:
            prediction.append(f"- 预计还需: **{inv_remaining}** 个月可能进入下一阶段")
        
        if inventory_cycle in inventory_transitions_map:
            next_inv = inventory_transitions_map[inventory_cycle]
            prediction.append(f"- 下一阶段预告: **{next_inv}**")
    
    # 综合研判
    prediction.append(f"\n### 综合研判")
    
    # 判断周期是否可能共振
    if credit_cycle in ['信贷扩张期', '政策宽松期'] and inventory_cycle in ['主动补库存', '被动补库存']:
        prediction.append("- 📈 **顺周期共振**: 信贷扩张+库存上升，经济复苏动能较强")
    elif credit_cycle in ['信贷收缩期', '政策收紧期'] and inventory_cycle in ['主动去库存', '被动去库存']:
        prediction.append("- 📉 **逆周期共振**: 信贷收缩+库存下降，经济下行压力较大")
    elif credit_cycle in ['信贷扩张期', '政策宽松期'] and inventory_cycle in ['主动去库存', '被动去库存']:
        prediction.append("- 🔄 **错位周期**: 政策宽松但库存仍在去化，可能处于复苏初期")
    elif credit_cycle in ['信贷收缩期', '政策收紧期'] and inventory_cycle in ['主动补库存', '被动补库存']:
        prediction.append("- ⚠️ **被动补库**: 需求收缩但被动补库，可能滞胀风险")
    
    return "\n".join(prediction)


# ==================== 报告生成函数 ====================

def generate_report(data_dict: Dict[str, pd.DataFrame]) -> str:
    """生成Markdown报告"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    conn = db_connect()
    
    # 获取周期判断
    credit_cycle = determine_credit_cycle(conn)
    inventory_cycle = determine_inventory_cycle(conn)
    
    # 获取周期阶段时间轴
    from cycle_phase_analyzer import analyze_cycles, find_current_cycle
    credit_trans, inv_trans, _, _ = analyze_cycles()
    cycle_info = find_current_cycle(credit_trans, inv_trans)
    
    credit_cycle_start = cycle_info.get('credit_cycle_start')
    inv_cycle_start = cycle_info.get('inv_cycle_start')
    
    # 信贷周期四阶段标准顺序
    credit_phase_order = ['信贷扩张期', '政策收紧期', '信贷收缩期', '政策宽松期']
    # 库存周期四阶段标准顺序
    inv_phase_order = ['主动补库存', '被动补库存', '主动去库存', '被动去库存']
    
    def build_timeline(transitions, phase_order, num_phases=6):
        """从阶段切换列表构建时间轴，显示最近num_phases个阶段（含当前进行中）
        同时返回本轮起始时间（当前阶段在phase_order中的位置往前回溯到第一个阶段的起始时间）
        """
        if not transitions:
            return "| - | - | - |\n", None
        # 过滤掉nan阶段
        valid = [t for t in transitions if str(t[1]) != 'nan']
        if not valid:
            return "| - | - | - |\n", None
        # 取最近num_phases个
        recent = valid[-num_phases:]
        lines = ""
        for t in recent:
            is_current = (t == valid[-1])
            marker = " 🔵" if is_current else ""
            duration_str = f"{t[2]}月" + (" (进行中)" if is_current else "")
            lines += f"| {t[1]} | {t[0]} | {duration_str}{marker} |\n"
        
        # 计算本轮起始：当前阶段在phase_order中的位置，往前回溯4个阶段
        current_phase = valid[-1][1]
        if current_phase in phase_order:
            idx = phase_order.index(current_phase)
            # 本轮起始 = 往前idx个阶段
            round_start_idx = len(valid) - 1 - idx
            if round_start_idx >= 0:
                round_start = valid[round_start_idx][0]
            else:
                round_start = valid[0][0]
        else:
            round_start = valid[-1][0]
        
        return lines, round_start
    
    credit_timeline, credit_round_start = build_timeline(credit_trans, credit_phase_order, num_phases=6)
    inv_timeline, inv_round_start = build_timeline(inv_trans, inv_phase_order, num_phases=6)
    
    # 用回溯计算的round_start替代find_current_cycle的结果
    if credit_round_start:
        credit_cycle_start = credit_round_start
    if inv_round_start:
        inv_cycle_start = inv_round_start
    
    report = f"""# 库存周期 & 信贷周期监控月报

**消息来源**：coze.DeepSeek · 库存信贷周期监控　│　**报告日期**：{today}　│　**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 一、周期状态总览

| 周期类型 | 当前状态 | 本轮起始 | 当前阶段起始 |
|:--------|:--------|:--------|:------------|
| 信贷周期 | **{credit_cycle}** | {credit_cycle_start or 'N/A'} | {cycle_info.get('credit_phase_start', 'N/A')} |
| 库存周期 | **{inventory_cycle}** | {inv_cycle_start or 'N/A'} | {cycle_info.get('inv_phase_start', 'N/A')} |

### 信贷周期时间轴

| 阶段 | 起始时间 | 持续 |
|:-----|:--------|:-----|
{credit_timeline}

### 库存周期时间轴

| 阶段 | 起始时间 | 持续 |
|:-----|:--------|:-----|
{inv_timeline}

---

## 二、信贷周期指标

| 指标ID | 指标名称 | 最新值 | 3月均值 | 趋势 | 数据状态 |
|:------|:--------|:------|:------|:-----|:--------|
"""
    
    credit_indicators = ['C001', 'C002', 'C003', 'C004', 'C005', 'C006', 'C007', 'C008']
    for ind_id in credit_indicators:
        config = INDICATOR_CONFIG.get(ind_id, {})
        name = config.get('name', '未知')
        
        value, ma3, date = get_latest_value(conn, ind_id)
        trend = get_trend(value, ma3)
        status = get_data_status(ind_id)
        
        # 特殊标注替代说明
        if '替代说明' in config:
            status = f"🔄{config['替代说明']}"
        
        if value is not None:
            ma3_str = f"{ma3:.2f}" if ma3 is not None else "N/A"
            report += f"| {ind_id} | {name} | {value:.2f} | {ma3_str} | {trend} | {status} |\n"
        else:
            report += f"| {ind_id} | {name} | N/A | N/A | N/A | ⚠️不可得 |\n"
    
    report += """
---

## 三、库存周期指标

| 指标ID | 指标名称 | 最新值 | 3月均值 | 趋势 | 数据状态 |
|:------|:--------|:------|:------|:-----|:--------|
"""
    
    # I001 是 PPI 的副本（占位指标），与 I004 完全重复，展示会误导，故从表中剔除
    inventory_indicators = ['I002', 'I003', 'I004', 'I005', 'I006', 'I007', 'I008', 'I009', 'I010']
    for ind_id in inventory_indicators:
        config = INDICATOR_CONFIG.get(ind_id, {})
        name = config.get('name', '未知')
        
        value, ma3, date = get_latest_value(conn, ind_id)
        trend = get_trend(value, ma3)
        status = get_data_status(ind_id)
        
        if '替代说明' in config:
            status = f"🔄{config['替代说明']}"
        
        if value is not None:
            ma3_str = f"{ma3:.2f}" if ma3 is not None else "N/A"
            report += f"| {ind_id} | {name} | {value:.2f} | {ma3_str} | {trend} | {status} |\n"
        else:
            report += f"| {ind_id} | {name} | N/A | N/A | N/A | ⚠️不可得 |\n"
    
    report += """
---

## 四、交叉验证指标

| 指标ID | 指标名称 | 最新值 | 3月均值 | 趋势 | 含义 |
|:------|:--------|:------|:------|:-----|:-----|
"""
    
    # V001: 贷款余额同比 - M2 剪刀差
    # 口径修正（2026-11-05）：原用 C001 算「社融-M2剪刀差」，但 C001 实为滚动12月增量同比，
    # 会算出 -10% 这类无意义值并误报「社融负增长」。改用同为存量同比的 C003 才有可比性。
    v001_val, v001_ma3, v001_trend = calc_spread_ma3_trend(conn, 'C003', 'C002')
    if v001_val is not None:
        report += (f"| V001 | 贷款余额同比-M2剪刀差 | {v001_val:.2f} | {v001_ma3:.2f} | {v001_trend} "
                   f"| 正值=信用扩张强于货币投放 |\n")
    else:
        report += f"| V001 | 贷款余额同比-M2剪刀差 | N/A | N/A | N/A | 数据不足 |\n"

    # V002: PMI新订单-产成品库存差
    v002_val, v002_ma3, v002_trend = calc_spread_ma3_trend(conn, 'I006', 'I007')
    if v002_val is not None:
        report += (f"| V002 | PMI新订单-产成品库存差 | {v002_val:.2f} | {v002_ma3:.2f} | {v002_trend} "
                   f"| 正值需求强于库存 |\n")
    else:
        report += f"| V002 | PMI新订单-产成品库存差 | N/A | N/A | N/A | 数据不足 |\n"
    
    # V003: 出口同比
    v003_val, v003_ma3, _ = get_latest_value(conn, 'V003')
    v003_trend = get_trend(v003_val, v003_ma3)
    if v003_val is not None:
        v003_ma3_str = f"{v003_ma3:.2f}" if v003_ma3 is not None else "N/A"
        report += f"| V003 | 出口同比 | {v003_val:.2f} | {v003_ma3_str} | {v003_trend} | 外需强弱 |\n"
    else:
        report += f"| V003 | 出口同比 | N/A | N/A | N/A | 数据不足 |\n"
    
    report += f"""

---

## 五、综合研判

### 信贷周期分析
{credit_cycle}："""

    # 信贷主口径原始值（V001 已改用 calc_spread_ma3_trend，这里单独取用于文字描述）
    c003_val, _, _ = get_latest_value(conn, 'C003')
    c002_val, _, _ = get_latest_value(conn, 'C002')

    if c003_val is not None and c002_val is not None:
        if c003_val > c002_val:
            report += f"\n- 贷款余额同比 {c003_val:.2f}% 高于 M2 同比 {c002_val:.2f}%，信用扩张强于货币投放，资金在向实体渗透"
        else:
            report += f"\n- 贷款余额同比 {c003_val:.2f}% 低于 M2 同比 {c002_val:.2f}%，货币投放未充分转化为信贷，资金活化偏弱"
        
        if credit_cycle == "政策宽松期":
            report += "\n- 政策面持续宽松，逆周期调节力度加大"
        elif credit_cycle == "政策收紧期":
            report += "\n- 政策面边际收紧，防风险优先级提升"
        elif credit_cycle == "信贷扩张期":
            report += "\n- 实体经济融资需求旺盛，经济活跃度高"
        elif credit_cycle == "信贷收缩期":
            report += "\n- 信用收缩明显，经济下行压力较大"

    report += f"""

### 库存周期分析
{inventory_cycle}："""

    i004_val, _, _ = get_latest_value(conn, 'I004')
    i008_val, _, _ = get_latest_value(conn, 'I008')

    # 判定依据（2026-11-05 重构）：主口径 = 统计局 I009 产成品存货同比 / I010 营业收入同比
    # 旧版把 PMI（环比扩散指数）当成主判定依据，与统计局同比方向相反时会把阶段判反，
    # 现改为：统计局同比定阶段，PMI 只作最新月动能提示。
    i009_v, _, i009_d = get_latest_value(conn, 'I009')
    i010_v, _, i010_d = get_latest_value(conn, 'I010')
    if i009_v is not None and i010_v is not None:
        report += f"\n- **判定依据（统计局同比，最新 {str(i009_d)[:7]}）**：产成品存货同比 {i009_v}%、营业收入同比 {i010_v}%"
        diff_pt = i009_v - i010_v
        if diff_pt > 0:
            report += f"，**存货增速比营收增速快 {diff_pt:.1f} 个百分点** —— 货在仓库里累积的速度快于卖出去的速度，属于滞胀式累库（被动补库存）"
        else:
            report += f"，营收增速比存货增速快 {abs(diff_pt):.1f} 个百分点 —— 卖得好才补货，属于健康补库（主动补库存）"

    i006_v, _, i006_d = get_latest_value(conn, 'I006')
    i007_v, _, i007_d = get_latest_value(conn, 'I007')
    if i006_v is not None and i007_v is not None:
        report += f"\n- **最新月动能（PMI {str(i006_d)[:7]}，环比扩散指数，仅供参考不作阶段判定）**：新订单 {i006_v}、产成品库存 {i007_v}"
        if i006_v < 50 and i007_v < 50:
            report += "，双双低于荣枯线 50 —— 环比看需求与库存同步走弱，同比与环比方向不一致时以同比为准"
        elif i006_v >= 50 and i007_v >= 50:
            report += "，双双高于荣枯线 50 —— 环比看需求与库存同步走强"
        else:
            report += "，一强一弱，环比方向存在分歧"

    if i004_val is not None:
        report += ("\n- PPI 处于正区间，工业品价格环境相对温和" if i004_val > 0
                   else "\n- PPI 处于负区间，存在工业品通缩压力")
    if i008_val is not None:
        if i008_val > 5:
            report += "\n- 工业增加值同比处于较高水平，生产端仍活跃"
        elif i008_val > 0:
            report += "\n- 工业增加值同比温和增长"
        else:
            report += "\n- 工业增加值同比转负，生产端面临下行压力"

    report += """

### 关键信号识别
"""

    signals = []
    
    if c003_val is not None and c003_val > 12:
        signals.append(f"✅ 贷款余额同比 {c003_val:.2f}% 高位：宽信用政策效果显现")
    elif c003_val is not None and c003_val < 7:
        signals.append(f"⚠️ 贷款余额同比 {c003_val:.2f}% 偏低：实体融资需求不足")
    
    if i004_val is not None and i004_val < -3:
        signals.append("🔴 PPI深度负值：工业品通缩风险")
    elif i004_val is not None and i004_val > 5:
        signals.append("🟢 PPI高位：通胀压力显现")
    
    if i008_val is not None and i008_val > 8:
        signals.append("🟢 工业强劲：经济基本面良好")
    elif i008_val is not None and i008_val < 3:
        signals.append("🟡 工业疲弱：需要政策支持")
    
    if v003_val is not None and v003_val > 10:
        signals.append("🟢 外需强劲：出口增长良好")
    elif v003_val is not None and v003_val < 0:
        signals.append("⚠️ 外需疲弱：出口面临压力")
    
    if not signals:
        signals.append("📊 数据不足，请关注后续更新")
    
    for signal in signals:
        report += f"- {signal}\n"

    # === 数据异常汇总 ===
    all_check_indicators = credit_indicators + inventory_indicators + ['V003']
    failed_list = []
    stale_list = []       # 严重断更（>180 天，真问题）
    lag_list = []         # 正常发布滞后（60-180 天，宏观数据固有节奏）
    abnormal_list = []
    
    for ind_id in all_check_indicators:
        val, _, dt = get_latest_value(conn, ind_id)
        cfg = INDICATOR_CONFIG.get(ind_id, {})
        nm = cfg.get('name', ind_id)
        
        if val is None:
            failed_list.append(f"{ind_id}({nm})")
            continue
        
        if dt and isinstance(dt, str):
            try:
                days_old = (pd.Timestamp.now() - pd.Timestamp(dt)).days
                if days_old > 180:
                    stale_list.append(f"{ind_id}({nm}, 最新{str(dt)[:7]}, 断更{days_old}天)")
                elif days_old > 60:
                    lag_list.append(f"{ind_id}(最新{str(dt)[:7]})")
            except:
                pass
        
        if ind_id == 'C003' and val is not None and val < 0:
            abnormal_list.append(f"{ind_id}({nm}): {val:.2f}% (异常负值，需排查数据源)")
    
    # I009/I010数据缺口检查
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM economic_indicators WHERE indicator_id='I009' AND value IS NOT NULL")
    i009_latest_row = cursor.fetchone()
    if i009_latest_row and i009_latest_row[0]:
        try:
            days_old = (pd.Timestamp.now() - pd.Timestamp(i009_latest_row[0])).days
            if days_old > 180:
                stale_list.append(f"I009/I010(产成品存货/营收同比, 最新{str(i009_latest_row[0])[:7]}, 断更{days_old}天)")
        except:
            pass
    
    data_warnings = ""
    if failed_list:
        data_warnings += f"\n❌ **获取失败**: {', '.join(failed_list)}\n"
    if stale_list:
        data_warnings += f"\n🔴 **严重断更（>180天，判定已改用替代口径）**: {', '.join(stale_list)}\n"
    if lag_list:
        data_warnings += f"\n🟡 **正常发布滞后（60-180天，宏观数据固有节奏，非异常）**: {', '.join(lag_list)}\n"
    if abnormal_list:
        data_warnings += f"\n⚠️ **数据异常**: {'; '.join(abnormal_list)}\n"
    if not failed_list and not stale_list and not abnormal_list:
        data_warnings = "\n✅ 所有指标数据正常获取\n"

    # 六、周期尾声预测
    report += f"""

---

## 六、周期尾声预测

{predict_cycle_ending(credit_cycle, inventory_cycle, conn)}

---

## 七、数据说明

### 数据来源
- **M2/货币供应**: 中国人民银行 via akshare
- **社会融资**: 中国人民银行 via akshare
- **PPI/PPIRM**: 国家统计局 via akshare
- **PMI**: 国家统计局官方发布页面爬取
- **工业增加值**: 国家统计局 via akshare
- **出口数据**: 海关总署 via akshare
- **企业中长期贷款/居民贷款**: 中国人民银行金融统计数据报告页面爬取
- **工业企业利润**: 国家统计局工业企业经济效益报告页面爬取

### 替代指标说明
- **DR007**: 使用SHIBOR 1W替代（银行间质押式回购利率的代理指标）
- **贷款利率**: 使用LPR 1Y替代（贷款市场报价利率）
- **PPIRM**: 使用企业商品交易价格指数替代
- **产成品存货(I001)**: 旧占位指标，数值为 PPI 同比副本（非真实存货），已从指标表剔除，不参与周期判定

### 数据状态说明
- ✅原始数据: 直接从官方数据源获取
- ✅央行爬虫/统计局爬虫: 从官方报告页面结构化提取
- 🔄替代指标: 使用近似指标替代真实数据
- ⚠️不可得: 暂时无法获取的数据

### 周期判断逻辑
- **信贷周期**: 基于社融同比与M2同比的趋势组合判断
- **库存周期**: 基于产成品存货同比(I009)与营收同比(I010)的边际变化方向判断（趋势判断法，与Wind/券商一致）
- 非连续月份（如12月→2月缺1月）的边际变化跳过，避免季节性口径跳变干扰

---

## 八、数据异常汇总
{data_warnings}

---

## 九、风险提示

⚠️ **本报告仅作为案例分析学习材料，不构成任何投资建议。**

宏观经济数据存在滞后性，且可能受季节因素、政策干预等影响，请谨慎参考。

---

*报告由库存周期 & 信贷周期监控系统 v2.2 自动生成*  
*数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    
    conn.close()
    
    return report


def save_report(report: str, date: str):
    """保存报告"""
    os.makedirs(OUTPUT_PATH, exist_ok=True)
    filename = os.path.join(OUTPUT_PATH, f"{date}_周期监控.md")
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(report)
    
    logger.info(f"报告已保存: {filename}")
    return filename


def update_cycle_judgments():
    """更新周期判断"""
    conn = db_connect()
    cursor = conn.cursor()
    
    today = datetime.now().strftime('%Y-%m-%d')
    credit = determine_credit_cycle(conn)
    inventory = determine_inventory_cycle(conn)
    
    # 获取持续月数
    credit_months, _ = get_cycle_duration(conn, 'credit', credit)
    inventory_months, _ = get_cycle_duration(conn, 'inventory', inventory)
    
    # 生成综合研判
    judgment = f"当前信贷周期处于{credit}，库存周期处于{inventory}。"
    
    if "扩张" in credit or "宽松" in credit:
        judgment += " 流动性环境相对宽松。"
    else:
        judgment += " 流动性环境相对收紧。"
    
    if "补库" in inventory:
        judgment += " 企业处于补库阶段，需求端有一定支撑。"
    elif "去库" in inventory:
        judgment += " 企业处于去库阶段，供需调整中。"
    
    cursor.execute('''
        INSERT OR REPLACE INTO cycle_judgments
        (date, credit_cycle, inventory_cycle, credit_duration_months, inventory_duration_months, comprehensive_judgment)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (today, credit, inventory, credit_months, inventory_months, judgment))
    
    conn.commit()
    conn.close()
    
    logger.info(f"周期判断已更新: 信贷={credit}, 库存={inventory}")


def collect_indicators_for_card():
    """从数据库收集卡片所需的指标数据"""
    conn = db_connect()
    indicators = {}

    all_inds = list(INDICATOR_CONFIG.keys()) + ['V001', 'V002', 'V003']
    for ind_id in all_inds:
        config = INDICATOR_CONFIG.get(ind_id, {})
        name = config.get('name', ind_id)

        if ind_id.startswith('V'):
            # 交叉验证指标：特殊计算
            if ind_id == 'V001':
                # 口径与报告一致：C003(贷款余额同比) - C002(M2同比)，均为存量同比
                c003_val, _, _ = get_latest_value(conn, 'C003')
                c002_val, _, _ = get_latest_value(conn, 'C002')
                if c003_val is not None and c002_val is not None:
                    val = c003_val - c002_val
                    indicators[ind_id] = {
                        'name': name,
                        'value': val,
                        'ma3': None,
                        'trend': '↑' if val > 0 else '↓',
                        'status': '✅计算值',
                        'meaning': '正值=信用扩张强于货币投放'
                    }
            elif ind_id == 'V002':
                i006_val, _, _ = get_latest_value(conn, 'I006')
                i007_val, _, _ = get_latest_value(conn, 'I007')
                if i006_val is not None and i007_val is not None:
                    val = i006_val - i007_val
                    indicators[ind_id] = {
                        'name': name,
                        'value': val,
                        'ma3': None,
                        'trend': '↑' if val > 0 else '↓',
                        'status': '✅计算值',
                        'meaning': '正值需求强于库存'
                    }
            elif ind_id == 'V003':
                val, ma3, _ = get_latest_value(conn, 'V003')
                if val is not None:
                    indicators[ind_id] = {
                        'name': name,
                        'value': val,
                        'ma3': ma3,
                        'trend': get_trend(val, ma3),
                        'status': '✅原始数据',
                        'meaning': '外需强弱'
                    }
        else:
            val, ma3, date = get_latest_value(conn, ind_id)
            if val is not None:
                status = get_data_status(ind_id)
                indicators[ind_id] = {
                    'name': name,
                    'value': val,
                    'ma3': ma3,
                    'trend': get_trend(val, ma3),
                    'status': status,
                }

    conn.close()
    return indicators



def _email_fallback(report_path, reason='飞书推送失败'):
    """飞书推送失败时的邮件兜底：把报告摘要 + 失败原因发到主人邮箱"""
    try:
        from cycle_switch_alert import send_email, EMAIL_CONFIG
        with open(report_path, 'r', encoding='utf-8') as f:
            text = f.read()
        summary = text[:6000]
        today = datetime.now().strftime('%Y-%m-%d')
        subject = f'【周期监控】{today} 报告（飞书推送失败，邮件兜底）'
        html = f"""<html><head><meta charset="utf-8"></head>
<body style="background:#0e1117;color:#d8dee9;font-family:-apple-system,'PingFang SC',sans-serif;padding:22px;line-height:1.7">
<div style="max-width:760px;margin:0 auto;background:#161b26;border:1px solid #2a3142;border-radius:12px;padding:24px">
<h1 style="font-size:18px;color:#ff8c42;margin:0 0 4px">⚠️ 飞书推送失败，报告改由邮件送达</h1>
<div style="color:#8b95a7;font-size:13px;margin-bottom:16px">{today}</div>
<div style="background:#1d2433;border-radius:8px;padding:14px 16px;margin-bottom:16px">
<div style="color:#ff5c7a;font-weight:700;margin-bottom:6px">失败原因</div>
<div style="font-size:13.5px">{reason}</div>
<div style="color:#8b95a7;font-size:12.5px;margin-top:8px">
完整错误已写入 /var/log/feishu_push_error.log。修复方式：终端执行 lark-cli config init --new 重新绑定，再 lark-cli auth status 确认 bot 为 ready。
</div>
</div>
<div style="background:#1d2433;border-radius:8px;padding:14px 16px">
<div style="color:#8b95a7;font-size:12.5px;margin-bottom:8px">报告摘要（完整文件：{os.path.basename(report_path)}）</div>
<pre style="white-space:pre-wrap;font-size:12.5px;color:#d8dee9;margin:0;font-family:monospace">{summary}</pre>
</div>
</div></body></html>"""
        ok = send_email(subject, html, f'{reason}\n\n{summary}')
        if ok:
            logger.info("✅ 邮件兜底已发送")
        else:
            logger.error("❌ 邮件兜底也失败了")
        return ok
    except Exception as e:
        logger.error(f"❌ 邮件兜底异常: {e}")
        return False


def send_report_email(report_path, subject_extra=''):
    """每月报告正常发邮件

    大佬要求：周期监控每月跑一次 → 发报告 + 发邮件。此前只有飞书推送失败才发兜底邮件，
    正常流程根本不发信，等于漏了这项交付。
    """
    try:
        from cycle_switch_alert import send_email
        with open(report_path, 'r', encoding='utf-8') as f:
            text = f.read()
        today = datetime.now().strftime('%Y-%m-%d')
        subject = f'coze.DeepSeek · 周期监控月报 {today}'
        if subject_extra:
            subject += f' | {subject_extra}'
        html = f"""<html><head><meta charset="utf-8"></head>
<body style="background:#0e1117;color:#d8dee9;font-family:-apple-system,'PingFang SC',sans-serif;padding:22px;line-height:1.7">
<div style="max-width:820px;margin:0 auto;background:#161b26;border:1px solid #2a3142;border-radius:12px;padding:24px">
<div style="color:#4ea8ff;font-size:12.5px;letter-spacing:.5px;margin-bottom:6px">coze.DeepSeek · 库存信贷周期监控</div>
<h1 style="font-size:19px;color:#ffd166;margin:0 0 4px">库存周期 &amp; 信贷周期监控月报</h1>
<div style="color:#8b95a7;font-size:13px;margin-bottom:18px">{today}</div>
<div style="background:#1d2433;border-radius:8px;padding:16px 18px">
<pre style="white-space:pre-wrap;font-size:12.8px;color:#d8dee9;margin:0;font-family:ui-monospace,Menlo,monospace">{text}</pre>
</div>
<div style="color:#8b95a7;font-size:12px;margin-top:14px">本报告为案例分析学习材料，不构成投资建议。</div>
</div></body></html>"""
        return send_email(subject, html, text)
    except Exception as e:
        logger.error(f'❌ 报告邮件发送异常: {type(e).__name__}: {e}')
        return False


def push_feishu_card(report_path):
    """推送飞书卡片，失败则降级为纯文本推送"""
    try:
        from feishu_card_utils import build_cycle_monitor_card
        from feishu_push import push_card_to_feishu, push_to_feishu

        conn = db_connect()

        # 获取周期判断
        credit_cycle = determine_credit_cycle(conn)
        inventory_cycle = determine_inventory_cycle(conn)

        # 获取周期信息
        try:
            from cycle_phase_analyzer import analyze_cycles, find_current_cycle
            credit_trans, inv_trans, _, _ = analyze_cycles()
            cycle_info = find_current_cycle(credit_trans, inv_trans)
        except Exception:
            cycle_info = None

        # 获取周期预测
        prediction = predict_cycle_ending(credit_cycle, inventory_cycle, conn)

        conn.close()

        # 收集指标数据
        indicators = collect_indicators_for_card()

        date_str = datetime.now().strftime('%Y-%m-%d')

        # 构建卡片
        card = build_cycle_monitor_card(
            date_str,
            credit_cycle=credit_cycle,
            inventory_cycle=inventory_cycle,
            indicators=indicators,
            cycle_info=cycle_info,
            prediction=prediction,
        )

        # 推送卡片
        logger.info("推送飞书交互式卡片...")
        success = push_card_to_feishu(card_dict=card)

        if success:
            logger.info("✅ 飞书卡片推送成功")
            return True
        else:
            logger.warning("⚠️  卡片推送失败，降级为纯文本推送...")
            # 降级：推送markdown报告前半部分
            with open(report_path, 'r', encoding='utf-8') as f:
                text = f.read()[:3000]
            if len(text) >= 3000:
                text += "\n\n... (报告较长，完整内容见文件)"
            ok = push_to_feishu(text=text)
            if ok:
                logger.info("✅ 降级纯文本推送成功")
            else:
                logger.error("❌ 纯文本推送也失败了 → 改用邮件兜底")
                _email_fallback(report_path, reason='飞书卡片推送失败 + 纯文本降级也失败')
            return ok

    except Exception as e:
        logger.error(f"❌ 推送异常: {e}")
        import traceback
        traceback.print_exc()
        # 再尝试降级纯文本，仍失败则邮件兜底
        try:
            from feishu_push import push_to_feishu
            with open(report_path, 'r', encoding='utf-8') as f:
                text = f.read()[:3000]
            if not push_to_feishu(text=text):
                _email_fallback(report_path, reason=f'推送流程异常: {e}')
        except Exception as e2:
            _email_fallback(report_path, reason=f'推送流程异常: {e}；降级也异常: {e2}')
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='库存信贷周期监控')
    parser.add_argument('--no-push', action='store_true',
                        help='只生成本地报告，不推送飞书')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("库存周期 & 信贷周期监控系统 v2.0 启动")
    logger.info("=" * 60)
    
    # 初始化数据库
    init_database()
    
    # 获取数据
    logger.info("开始获取数据...")
    data_dict = fetch_all_data()
    
    # 保存数据
    total_saved = 0
    for indicator_id, df in data_dict.items():
        config = INDICATOR_CONFIG.get(indicator_id, {})
        source = config.get('source', 'unknown')
        status = '替代' if '替代说明' in config else 'original'
        count = save_to_database(indicator_id, df, source, status)
        total_saved += count
    
    logger.info(f"数据更新完成，共保存 {total_saved} 条记录")
    
    # 更新周期判断
    update_cycle_judgments()
    
    # 生成报告
    logger.info("生成报告...")
    today = datetime.now().strftime('%Y-%m-%d')
    report = generate_report(data_dict)
    report_path = save_report(report, today)

    # 生成周期曲线图（2026-11-05 新增：此前报告只有文字、没有图，飞书卡片里看不到走势）
    chart_path = None
    try:
        from plot_cycles import main as build_cycles_chart
        chart_path = build_cycles_chart()
        logger.info(f"周期曲线图已生成: {chart_path}")
    except Exception as e:
        logger.error(f"❌ 周期曲线图生成失败（不影响报告）: {e}")
    
    # 记录更新日志
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO update_log (indicators_updated, status)
        VALUES (?, ?)
    ''', (len([d for d in data_dict if not data_dict[d].empty]), 'success'))
    conn.commit()
    conn.close()

    # 推送飞书卡片
    if not args.no_push:
        logger.info("推送飞书消息...")
        push_feishu_card(report_path)
        # 报告全文单独推一次：卡片里只有摘要（周期定位+6个核心指标），完整报告正文必须走文本通道，
        # 否则大佬只能看到图、看不到报告（2026-11-05 反馈"只收到图片没收到报告"）
        try:
            from feishu_push import push_report_full_to_feishu
            if push_report_full_to_feishu(report_path):
                logger.info("✅ 报告全文卡片已推送")
            else:
                logger.warning("⚠️ 报告全文推送失败，已写入推送错误日志")
        except Exception as e:
            logger.error(f"❌ 报告全文推送异常: {e}")
        # 图单独以图片消息推送：卡片里只有文字摘要，图必须走图片通道才能直接看到
        if chart_path and os.path.exists(chart_path):
            try:
                from feishu_push import push_image_to_feishu
                if push_image_to_feishu(chart_path):
                    logger.info(f"周期曲线图已推送: {os.path.basename(chart_path)}")
                else:
                    logger.warning("周期曲线图推送失败，已写入推送错误日志")
            except Exception as e:
                logger.error(f"❌ 图片推送异常: {e}")
        else:
            logger.warning("无图可推送（图表生成失败）")

        # 邮件：每月报告正常发信（不只是推送失败时的兜底）
        try:
            _extra = ''
            try:
                _c = db_connect()
                _inv = determine_inventory_cycle(_c)
                _c.close()
                _nxt = {'主动补库存': '被动补库存', '被动补库存': '主动去库存',
                        '主动去库存': '被动去库存', '被动去库存': '主动补库存'}.get(_inv, '')
                _extra = f'库存 {_inv}' + (f' → 下一阶段 {_nxt}' if _nxt else '')
            except Exception:
                pass
            if send_report_email(report_path, subject_extra=_extra):
                logger.info("✅ 报告邮件已发送")
            else:
                logger.warning("⚠️ 报告邮件发送失败")
        except Exception as e:
            logger.error(f"❌ 报告邮件异常: {e}")

    # 周期切换提醒（提前预警 + 事后确认 + 切换后跟踪），邮件通道，幂等
    try:
        from cycle_switch_alert import run_alerts
        sent = run_alerts()
        if sent:
            logger.info(f"周期提醒已发送 {len(sent)} 封: {'; '.join(sent)}")
        else:
            logger.info("周期提醒：本轮无新增事件")
    except Exception as e:
        logger.error(f"❌ 周期提醒模块异常（不影响主流程）: {e}")
    
    logger.info("=" * 60)
    logger.info("监控系统执行完成")
    logger.info(f"报告路径: {report_path}")
    logger.info("=" * 60)
    
    return report_path


if __name__ == '__main__':
    main()
