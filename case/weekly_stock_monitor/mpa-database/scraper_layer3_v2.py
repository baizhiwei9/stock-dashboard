#!/usr/bin/env python3
"""
层3 v2: 用东方财富+同花顺抓取银行房地产贷款细分数据
数据源: 银行年报/半年报中的"贷款按行业分类"和"贷款按担保方式分类"
"""

import requests
import json
import sqlite3
import re
import time
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

DB_PATH = "/root/.coze/agents/7691719640636244234/workspace/mpa-database/mpa_coefficients.db"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/html',
}

# 上市银行信息
BANKS = {
    '601169': {'name': '北京银行', 'province': '北京', 'type': '城商行'},
    '601009': {'name': '南京银行', 'province': '江苏', 'type': '城商行'},
    '002142': {'name': '宁波银行', 'province': '浙江', 'type': '城商行'},
    '600919': {'name': '江苏银行', 'province': '江苏', 'type': '城商行'},
    '600926': {'name': '杭州银行', 'province': '浙江', 'type': '城商行'},
    '601229': {'name': '上海银行', 'province': '上海', 'type': '城商行'},
    '601838': {'name': '成都银行', 'province': '四川', 'type': '城商行'},
    '601577': {'name': '长沙银行', 'province': '湖南', 'type': '城商行'},
    '601997': {'name': '贵阳银行', 'province': '贵州', 'type': '城商行'},
    '002948': {'name': '青岛银行', 'province': '山东', 'type': '城商行'},
    '601665': {'name': '齐鲁银行', 'province': '山东', 'type': '城商行'},
    '002936': {'name': '郑州银行', 'province': '河南', 'type': '城商行'},
    '600928': {'name': '西安银行', 'province': '陕西', 'type': '城商行'},
    '001227': {'name': '兰州银行', 'province': '甘肃', 'type': '城商行'},
    '601187': {'name': '厦门银行', 'province': '福建', 'type': '城商行'},
    '601963': {'name': '重庆银行', 'province': '重庆', 'type': '城商行'},
    '002966': {'name': '苏州银行', 'province': '江苏', 'type': '城商行'},
    '601077': {'name': '渝农商行', 'province': '重庆', 'type': '农商行'},
    '601825': {'name': '沪农商行', 'province': '上海', 'type': '农商行'},
    '002839': {'name': '张家港行', 'province': '江苏', 'type': '农商行'},
    '601128': {'name': '常熟银行', 'province': '江苏', 'type': '农商行'},
    '002807': {'name': '江阴银行', 'province': '江苏', 'type': '农商行'},
    '600908': {'name': '无锡银行', 'province': '江苏', 'type': '农商行'},
    '603323': {'name': '苏农银行', 'province': '江苏', 'type': '农商行'},
    '601860': {'name': '紫金银行', 'province': '江苏', 'type': '农商行'},
    '002958': {'name': '青农商行', 'province': '山东', 'type': '农商行'},
}


def get_eastmoney_loan_data(stock_code):
    """
    从东方财富获取银行财报中的贷款明细数据
    接口: 东方财富数据中心
    """
    try:
        # 东方财富个股财报 - 资产负债明细
        url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZCFZBGetList"
        params = {
            'code': stock_code,
            'type': '年报'
        }
        
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = resp.json()
        
        if 'data' in data and data['data']:
            return data['data']
    except Exception as e:
        print(f"    东方财富接口失败: {e}")
    
    return None


def get_eastmoney_main_indexes(stock_code):
    """
    从东方财富获取银行主要财务指标
    包含不良率等关键数据
    """
    try:
        url = "https://datacenter.eastmoney.com/securities/api/data/get"
        params = {
            'type': 'RPT_F10_FINANCE_MAINFINADATA',
            'sty': 'ALL',
            'filter': f'(SECURITY_CODE="{stock_code}")',
            'p': '1',
            'ps': '8',
            'sr': '-1',
            'st': 'REPORT_DATE',
            'source': 'HSF10',
            'client': 'PC'
        }
        
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        
        if data.get('result') and data['result'].get('data'):
            return data['result']['data']
    except Exception as e:
        print(f"    东方财富指标接口失败: {e}")
    
    return None


def search_cninfo_annual_report(stock_code, bank_name):
    """
    从巨潮资讯网搜索银行年报，提取房地产贷款数据
    """
    try:
        # 搜索年报
        url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        params = {
            'stock': stock_code,
            'tabName': 'fulltext',
            'pageSize': '5',
            'pageNum': '1',
            'column': 'sse' if stock_code.startswith('6') else 'szse',
            'category': 'category_ndbg_szsh',  # 年度报告
            'plate': '',
            'seDate': '',
            'searchkey': '',
            'secid': '',
            'sortName': '',
            'sortType': '',
            'isHLtitle': 'true',
        }
        
        resp = requests.post(url, data=params, headers=HEADERS, timeout=15)
        data = resp.json()
        
        if data.get('announcements'):
            for ann in data['announcements'][:3]:
                title = ann.get('announcementTitle', '')
                if '年度报告' in title and '摘要' not in title:
                    print(f"    找到年报: {title}")
                    return {
                        'title': title,
                        'url': f"http://www.cninfo.com.cn/new/disclosure/detail?annoId={ann.get('announcementId')}",
                        'date': ann.get('announcementTime', ''),
                    }
    except Exception as e:
        print(f"    巨潮搜索失败: {e}")
    
    return None


def get_bank_real_estate_loan_from_web(stock_code, bank_name):
    """
    尝试从各财经网站获取银行房地产贷款占比数据
    """
    results = {}
    
    # 方法1: 东方财富F10中的经营分析
    try:
        url = f"https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"
        params = {'code': stock_code}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        
        if data:
            results['eastmoney_business'] = data
    except:
        pass
    
    # 方法2: 同花顺
    try:
        url = f"http://basic.10jqka.com.cn/api/stockph/business/{stock_code}/"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            results['ths'] = resp.json()
    except:
        pass
    
    return results


def get_loans_by_type_from_eastmoney(stock_code):
    """
    从东方财富获取贷款分类数据
    """
    try:
        # 尝试经营分析-主营业务构成
        url = "https://datacenter.eastmoney.com/securities/api/data/get"
        params = {
            'type': 'RPT_F10_FINANCE_MAINOPINCOME',
            'sty': 'ALL',
            'filter': f'(SECURITY_CODE="{stock_code}")',
            'p': '1',
            'ps': '20',
            'sr': '-1',
            'st': 'REPORT_DATE',
            'source': 'HSF10',
            'client': 'PC'
        }
        
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        
        if data.get('result') and data['result'].get('data'):
            return data['result']['data']
    except Exception as e:
        print(f"    贷款分类接口失败: {e}")
    
    return None


def fetch_all_bank_data():
    """遍历所有银行，抓取财报数据"""
    print("=" * 60)
    print("📊 层3 v2: 银行房地产贷款细分数据抓取")
    print(f"📅 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    all_records = []
    
    for code, info in BANKS.items():
        print(f"\n🏦 {info['name']} ({code}) - {info['province']}")
        
        # 1. 获取主要财务指标
        main_data = get_eastmoney_main_indexes(code)
        if main_data:
            for record in main_data[:4]:  # 最近4期
                npl = record.get('BASIC_EPS')  # 不良率可能在其他字段
                
                bank_record = {
                    'bank_name': info['name'],
                    'bank_type': info['type'],
                    'province': info['province'],
                    'report_date': record.get('REPORT_DATE', '')[:10],
                    'total_loans': record.get('TOTAL_OPERATE_INCOME'),
                    'npl_ratio': None,  # 需要其他字段
                }
                all_records.append(bank_record)
                print(f"  📊 {bank_record['report_date']}: 主要指标已获取")
        
        # 2. 获取贷款分类数据
        loan_type_data = get_loans_by_type_from_eastmoney(code)
        if loan_type_data:
            for record in loan_type_data[:4]:
                item_name = record.get('ITEM_NAME', '')
                if '房地产' in item_name or '个人住房' in item_name or '房贷' in item_name:
                    print(f"  🔑 找到: {item_name} = {record.get('ITEM_VALUE')}")
        
        # 3. 搜索年报
        ann = search_cninfo_annual_report(code, info['name'])
        if ann:
            print(f"  📄 年报: {ann['title']}")
        
        time.sleep(1)
    
    # 保存数据
    print(f"\n\n💾 保存 {len(all_records)} 条记录到数据库...")
    
    for record in all_records:
        cursor.execute("""
        INSERT INTO bank_proxy_indicators 
        (bank_name, bank_type, province, report_date, total_loans, npl_ratio, source_url)
        VALUES (:bank_name, :bank_type, :province, :report_date, :total_loans, :npl_ratio, '')
        """, record)
    
    conn.commit()
    conn.close()
    
    print("✅ 数据保存完成")
    
    # 输出汇总
    print("\n" + "=" * 60)
    print("📊 数据汇总")
    print("=" * 60)
    
    df = pd.DataFrame(all_records)
    if not df.empty:
        province_summary = df.groupby('province').agg({
            'bank_name': 'count',
        }).rename(columns={'bank_name': 'record_count'})
        print("\n各省数据条数:")
        print(province_summary)
    
    return all_records


if __name__ == '__main__':
    fetch_all_bank_data()
