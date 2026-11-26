#!/usr/bin/env python3
"""
宏观审慎政策爬虫 - 层2数据抓取
搜索央行分支行+金融监管局关于房地产贷款集中度的政策文件
数据来源：央行官网、各省金融监管局、财经媒体
"""

import requests
import sqlite3
import json
import re
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

DB_PATH = "/root/.coze/agents/7691719640636244234/workspace/mpa-database/mpa_coefficients.db"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 全国基准值
BASELINE = {
    '第一档': {'re': 22.5, 'phl': 17.5},
    '第二档': {'re': 20.0, 'phl': 15.0},
    '第三档': {'re': 17.5, 'phl': 12.5},
    '第四档': {'re': 15.0, 'phl': 10.0},
    '第五档': {'re': 12.5, 'phl': 7.5},
}

# 31省列表
PROVINCES = [
    '北京', '天津', '河北', '山西', '内蒙古',
    '辽宁', '吉林', '黑龙江',
    '上海', '江苏', '浙江', '安徽', '福建', '江西', '山东',
    '河南', '湖北', '湖南',
    '广东', '广西', '海南',
    '重庆', '四川', '贵州', '云南', '西藏',
    '陕西', '甘肃', '青海', '宁夏', '新疆'
]

# 省会映射
CAPITAL_MAP = {
    '河北': '石家庄', '山西': '太原', '内蒙古': '呼和浩特',
    '辽宁': '沈阳', '吉林': '长春', '黑龙江': '哈尔滨',
    '江苏': '南京', '浙江': '杭州', '安徽': '合肥',
    '福建': '福州', '江西': '南昌', '山东': '济南',
    '河南': '郑州', '湖北': '武汉', '湖南': '长沙',
    '广东': '广州', '广西': '南宁', '海南': '海口',
    '四川': '成都', '贵州': '贵阳', '云南': '昆明',
    '西藏': '拉萨', '陕西': '西安', '甘肃': '兰州',
    '青海': '西宁', '宁夏': '银川', '新疆': '乌鲁木齐',
}

# 央行分支行关键词
PBOC_KEYWORDS = [
    '房地产贷款集中度', '个人住房贷款', '房地产贷款占比',
    '住房贷款集中度', '房贷集中度', '集中度管理',
    '宏观审慎', 'MPA', '激励约束',
]

# 财经媒体搜索源
MEDIA_SOURCES = [
    '21世纪经济报道', '经济参考报', '财联社', '第一财经',
    '证券时报', '中国证券报', '金融时报',
]


def search_baidu(keyword, num_results=10):
    """百度搜索，返回结果列表"""
    url = f"https://www.baidu.com/s?wd={quote(keyword)}&rn={num_results}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        results = []
        for item in soup.select('.result'):
            title_elem = item.select_one('h3 a')
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)
            link = title_elem.get('href', '')
            
            # 尝试获取摘要
            abstract_elem = item.select_one('.c-abstract') or item.select_one('.content-right_8Zs40')
            abstract = abstract_elem.get_text(strip=True) if abstract_elem else ''
            
            results.append({
                'title': title,
                'url': link,
                'abstract': abstract,
                'source': 'baidu'
            })
        
        time.sleep(2)  # 避免被封
        return results
    except Exception as e:
        print(f"  ⚠️ 百度搜索失败: {e}")
        return []


def search_bing_cn(keyword, num_results=10):
    """Bing中国搜索"""
    url = f"https://cn.bing.com/search?q={quote(keyword)}&count={num_results}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        results = []
        for item in soup.select('li.b_algo'):
            title_elem = item.select_one('h2 a')
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)
            link = title_elem.get('href', '')
            
            snippet_elem = item.select_one('.b_caption p') or item.select_one('p')
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''
            
            results.append({
                'title': title,
                'url': link,
                'abstract': snippet,
                'source': 'bing'
            })
        
        time.sleep(2)
        return results
    except Exception as e:
        print(f"  ⚠️ Bing搜索失败: {e}")
        return []


def search_sogou(keyword, num_results=10):
    """搜狗搜索"""
    url = f"https://www.sogou.com/web?query={quote(keyword)}&num={num_results}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        results = []
        for item in soup.select('.vrwrap, .rb'):
            title_elem = item.select_one('h3 a')
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)
            link = title_elem.get('href', '')
            
            abstract_elem = item.select_one('.star-wiki, .ft, p.str_info')
            abstract = abstract_elem.get_text(strip=True) if abstract_elem else ''
            
            results.append({
                'title': title,
                'url': link,
                'abstract': abstract,
                'source': 'sogou'
            })
        
        time.sleep(2)
        return results
    except Exception as e:
        print(f"  ⚠️ 搜狗搜索失败: {e}")
        return []


def fetch_page_content(url, max_retries=2):
    """抓取页面正文内容"""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
            resp.encoding = resp.apparent_encoding or 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # 移除脚本和样式
            for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            
            # 提取正文
            content = soup.get_text(separator='\n', strip=True)
            # 截断过长内容
            if len(content) > 5000:
                content = content[:5000]
            return content
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            continue
    return None


def parse_coefficient_from_text(text):
    """从文本中提取系数数据"""
    findings = {}
    
    # 匹配百分比数值
    patterns = [
        r'房地产贷款[占比上限]*[为是不超][^\d]*(\d+\.?\d*)\s*%',
        r'房地产贷款集中度[上限]*[为是不超][^\d]*(\d+\.?\d*)\s*%',
        r'个人住房贷款[占比上限]*[为是不超][^\d]*(\d+\.?\d*)\s*%',
        r'住房贷款集中度[上限]*[为是不超][^\d]*(\d+\.?\d*)\s*%',
        r'(?:上限|限额|比例)[为是不]+(\d+\.?\d*)\s*%',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            findings[pattern] = [float(m) for m in matches]
    
    return findings


def search_province_policy(province):
    """搜索某省的房地产贷款集中度政策"""
    print(f"\n🔍 搜索 {province} ...")
    all_results = []
    
    # 搜索策略1：央行分支行
    capital = CAPITAL_MAP.get(province, '')
    if capital:
        keyword1 = f"中国人民银行{capital} 房地产贷款集中度"
        results = search_baidu(keyword1, 5)
        all_results.extend(results)
        print(f"  📌 央行搜索: {len(results)} 条")
    
    # 搜索策略2：省级+关键词
    keyword2 = f"{province} 房地产贷款集中度 上限 地方"
    results = search_bing_cn(keyword2, 5)
    all_results.extend(results)
    print(f"  📌 Bing搜索: {len(results)} 条")
    
    # 搜索策略3：媒体报道
    keyword3 = f"{province} 房贷集中度 调整 2024 2025 2026"
    results = search_sogou(keyword3, 5)
    all_results.extend(results)
    print(f"  📌 搜狗搜索: {len(results)} 条")
    
    return all_results


def search_hot_city_policy(city):
    """搜索热点城市首付比/LTV政策"""
    print(f"\n🔍 搜索 {city} 首付比/LTV政策 ...")
    all_results = []
    
    keywords = [
        f"{city} 首付比例 2025 2026",
        f"{city} 住房贷款 LTV 首付比 最新",
        f"{city} 限购限贷 最新政策",
    ]
    
    for kw in keywords:
        results = search_baidu(kw, 5)
        all_results.extend(results)
    
    return all_results


def search_cross_border_params():
    """搜索跨境融资宏观审慎参数"""
    print(f"\n🔍 搜索跨境融资宏观审慎参数 ...")
    all_results = []
    
    keywords = [
        "跨境融资宏观审慎调节参数 最新",
        "外汇风险准备金率 2025 2026",
        "远期售汇准备金率 调整",
        "跨境融资 宏观审慎 央行",
    ]
    
    for kw in keywords:
        results = search_baidu(kw, 5)
        all_results.extend(results)
        time.sleep(1)
    
    return all_results


def search_mpa_adjustments():
    """搜索MPA执行口径调整"""
    print(f"\n🔍 搜索MPA执行口径调整 ...")
    all_results = []
    
    keywords = [
        "MPA考核 2025 2026 最新",
        "央行 MPA 激励约束 调整",
        "宏观审慎评估 MPA 口径",
    ]
    
    for kw in keywords:
        results = search_baidu(kw, 5)
        all_results.extend(results)
        time.sleep(1)
    
    return all_results


def save_results_to_db(province, results, content_snippets=None):
    """保存搜索结果到数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 保存到policy_changes表作为搜索记录
    for r in results[:5]:  # 只保存前5条最相关的
        cursor.execute("""
        INSERT INTO policy_changes (change_date, province, coefficient_type, 
                                     policy_document, source_url, source_description)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime('%Y-%m-%d'),
            province,
            '搜索记录',
            r.get('title', ''),
            r.get('url', ''),
            r.get('abstract', '')[:200] if r.get('abstract') else ''
        ))
    
    conn.commit()
    conn.close()


def run_full_search():
    """执行完整搜索流程"""
    print("=" * 60)
    print(f"🚀 宏观审慎政策爬虫启动 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    all_findings = {}
    
    # === 1. 逐省搜索房地产贷款集中度政策 ===
    print("\n" + "=" * 40)
    print("📋 阶段1: 各省房地产贷款集中度政策")
    print("=" * 40)
    
    for province in PROVINCES:
        results = search_province_policy(province)
        all_findings[province] = {
            'search_results': results,
            're_cap': None,
            'phl_cap': None,
            'notes': []
        }
        save_results_to_db(province, results)
    
    # === 2. 热点城市首付比/LTV ===
    print("\n" + "=" * 40)
    print("📋 阶段2: 热点城市首付比/LTV")
    print("=" * 40)
    
    hot_cities = ['北京', '上海', '广州', '深圳', '杭州', '南京', 
                  '成都', '武汉', '天津', '重庆', '苏州', '西安', '长沙', '合肥']
    
    for city in hot_cities:
        results = search_hot_city_policy(city)
        all_findings[city] = {
            'search_results': results,
            'notes': []
        }
        save_results_to_db(city, results)
    
    # === 3. 跨境融资参数 ===
    print("\n" + "=" * 40)
    print("📋 阶段3: 跨境融资宏观审慎参数")
    print("=" * 40)
    
    cross_border_results = search_cross_border_params()
    all_findings['_跨境融资'] = {'search_results': cross_border_results}
    
    # === 4. MPA口径 ===
    print("\n" + "=" * 40)
    print("📋 阶段4: MPA执行口径")
    print("=" * 40)
    
    mpa_results = search_mpa_adjustments()
    all_findings['_MPA'] = {'search_results': mpa_results}
    
    # === 汇总输出 ===
    print("\n" + "=" * 60)
    print("📊 搜索汇总")
    print("=" * 60)
    
    for key, data in all_findings.items():
        count = len(data.get('search_results', []))
        if count > 0:
            print(f"  {key}: {count} 条结果")
    
    # 保存原始搜索结果到JSON
    output_path = "/root/.coze/agents/7691719640636244234/workspace/mpa-database/search_results.json"
    
    # 序列化时去掉过长的内容
    serializable = {}
    for key, data in all_findings.items():
        serializable[key] = {
            'search_results': data.get('search_results', [])[:10],
            'notes': data.get('notes', [])
        }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 原始结果已保存: {output_path}")
    return all_findings


if __name__ == '__main__':
    run_full_search()
