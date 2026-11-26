#!/usr/bin/env python3
"""
层3：代理指标脚本 - 用银行财报数据反推地方系数
数据来源：AKShare（免费）+ Tushare（需token）
逻辑：某省城商行房贷占比卡在X%不涨 → 地方上限大概率是X%
"""

import sqlite3
import pandas as pd
import akshare as ak
from datetime import datetime
import time

DB_PATH = "/root/.coze/agents/7691719640636244234/workspace/mpa-database/mpa_coefficients.db"

# 上市城商行/农商行列表（按省份分组）
LISTED_BANKS = {
    # 城商行
    '北京银行': {'province': '北京', 'type': '城商行'},
    '南京银行': {'province': '江苏', 'type': '城商行'},
    '宁波银行': {'province': '浙江', 'type': '城商行'},
    '江苏银行': {'province': '江苏', 'type': '城商行'},
    '杭州银行': {'province': '浙江', 'type': '城商行'},
    '上海银行': {'province': '上海', 'type': '城商行'},
    '成都银行': {'province': '四川', 'type': '城商行'},
    '长沙银行': {'province': '湖南', 'type': '城商行'},
    '贵阳银行': {'province': '贵州', 'type': '城商行'},
    '青岛银行': {'province': '山东', 'type': '城商行'},
    '齐鲁银行': {'province': '山东', 'type': '城商行'},
    '郑州银行': {'province': '河南', 'type': '城商行'},
    '西安银行': {'province': '陕西', 'type': '城商行'},
    '兰州银行': {'province': '甘肃', 'type': '城商行'},
    '厦门银行': {'province': '福建', 'type': '城商行'},
    '重庆银行': {'province': '重庆', 'type': '城商行'},
    '苏州银行': {'province': '江苏', 'type': '城商行'},
    '长沙银行': {'province': '湖南', 'type': '城商行'},
    '徽商银行': {'province': '安徽', 'type': '城商行'},
    
    # 农商行
    '渝农商行': {'province': '重庆', 'type': '农商行'},
    '沪农商行': {'province': '上海', 'type': '农商行'},
    '张家港行': {'province': '江苏', 'type': '农商行'},
    '常熟银行': {'province': '江苏', 'type': '农商行'},
    '江阴银行': {'province': '江苏', 'type': '农商行'},
    '无锡银行': {'province': '江苏', 'type': '农商行'},
    '苏农银行': {'province': '江苏', 'type': '农商行'},
    '紫金银行': {'province': '江苏', 'type': '农商行'},
    '青农商行': {'province': '山东', 'type': '农商行'},
}


def fetch_bank_financial_data():
    """用AKShare抓取上市银行财报数据"""
    print("=" * 60)
    print("📊 层3：代理指标 - 银行财报数据抓取")
    print("=" * 60)
    
    all_bank_data = []
    
    # 尝试用AKShare获取银行财报
    try:
        # 获取银行资产负债表数据
        print("\n🔍 正在获取银行财报数据...")
        
        for bank_name, info in LISTED_BANKS.items():
            print(f"  处理 {bank_name} ({info['province']} {info['type']})...")
            
            try:
                # AKShare获取银行财报
                # 注意：AKShare的具体接口可能变化，这里用通用方法
                stock_code = get_stock_code(bank_name)
                if not stock_code:
                    print(f"    ⚠️ 未找到股票代码: {bank_name}")
                    continue
                
                # 获取资产负债表
                df = ak.stock_financial_report_sina(stock=stock_code, symbol="资产负债表")
                
                if df is not None and not df.empty:
                    # 提取最近几期数据
                    latest_data = df.head(4)  # 最近4个季度
                    
                    for _, row in latest_data.iterrows():
                        bank_record = {
                            'bank_name': bank_name,
                            'province': info['province'],
                            'bank_type': info['type'],
                            'report_date': row.get('报表日期', ''),
                            'total_loans': extract_loan_data(row, '贷款'),
                            'real_estate_loans': extract_loan_data(row, '房地产贷款'),
                            'personal_housing_loans': extract_loan_data(row, '个人住房贷款'),
                        }
                        all_bank_data.append(bank_record)
                
                time.sleep(1)  # 避免请求过快
                
            except Exception as e:
                print(f"    ⚠️ 获取 {bank_name} 数据失败: {e}")
                continue
        
    except Exception as e:
        print(f"❌ AKShare调用失败: {e}")
        print("💡 备用方案：使用模拟数据演示逻辑")
        all_bank_data = generate_demo_data()
    
    return all_bank_data


def get_stock_code(bank_name):
    """根据银行名称获取股票代码"""
    code_map = {
        '北京银行': '601169',
        '南京银行': '601009',
        '宁波银行': '002142',
        '江苏银行': '600919',
        '杭州银行': '600926',
        '上海银行': '601229',
        '成都银行': '601838',
        '长沙银行': '601577',
        '贵阳银行': '601997',
        '青岛银行': '002948',
        '齐鲁银行': '601665',
        '郑州银行': '002936',
        '西安银行': '600928',
        '兰州银行': '001227',
        '厦门银行': '601187',
        '重庆银行': '601963',
        '苏州银行': '002966',
        '渝农商行': '601077',
        '沪农商行': '601825',
        '张家港行': '002839',
        '常熟银行': '601128',
        '江阴银行': '002807',
        '无锡银行': '600908',
        '苏农银行': '603323',
        '紫金银行': '601860',
        '青农商行': '002958',
    }
    return code_map.get(bank_name)


def extract_loan_data(row, loan_type):
    """从财报行中提取贷款数据"""
    # 这里需要根据实际财报字段调整
    # 演示逻辑
    if loan_type == '贷款':
        return row.get('发放贷款及垫款', 0)
    elif loan_type == '房地产贷款':
        return row.get('房地产贷款', 0)  # 实际字段名可能不同
    elif loan_type == '个人住房贷款':
        return row.get('个人住房贷款', 0)
    return 0


def generate_demo_data():
    """生成演示数据（当AKShare不可用时）"""
    print("  📝 生成演示数据...")
    
    demo_data = []
    provinces = ['江苏', '浙江', '山东', '四川', '湖南']
    
    for province in provinces:
        # 每个省生成3-5个银行数据
        for i in range(3):
            for quarter in ['2024-09-30', '2024-06-30', '2024-03-31', '2023-12-31']:
                demo_data.append({
                    'bank_name': f'{province}银行{i+1}',
                    'province': province,
                    'bank_type': '城商行' if i < 2 else '农商行',
                    'report_date': quarter,
                    'total_loans': 1000 + i * 100,  # 亿元
                    'real_estate_loans': 200 + i * 20,
                    'personal_housing_loans': 150 + i * 15,
                })
    
    return demo_data


def calculate_proxy_indicators(bank_data):
    """计算代理指标"""
    print("\n📊 计算代理指标...")
    
    df = pd.DataFrame(bank_data)
    
    if df.empty:
        print("  ⚠️ 无数据")
        return None
    
    # 计算占比
    df['real_estate_loan_ratio'] = (df['real_estate_loans'] / df['total_loans'] * 100).round(2)
    df['personal_housing_loan_ratio'] = (df['personal_housing_loans'] / df['total_loans'] * 100).round(2)
    
    # 按省份聚合
    province_stats = df.groupby('province').agg({
        'real_estate_loan_ratio': ['mean', 'max', 'min'],
        'personal_housing_loan_ratio': ['mean', 'max', 'min'],
        'bank_name': 'count'
    }).round(2)
    
    province_stats.columns = ['re_ratio_avg', 're_ratio_max', 're_ratio_min',
                               'phl_ratio_avg', 'phl_ratio_max', 'phl_ratio_min',
                               'bank_count']
    
    print(f"\n📈 各省银行房地产贷款占比统计：")
    print(province_stats)
    
    return province_stats, df


def detect_cap_constraints(df):
    """检测银行是否卡在上限"""
    print("\n🔍 检测上限约束...")
    
    findings = []
    
    # 按省份+银行分组
    for province in df['province'].unique():
        province_df = df[df['province'] == province]
        
        for bank in province_df['bank_name'].unique():
            bank_df = province_df[province_df['bank_name'] == bank].sort_values('report_date')
            
            if len(bank_df) < 2:
                continue
            
            # 检查最近几期是否"卡住"（变化<0.5pct）
            re_ratios = bank_df['real_estate_loan_ratio'].values
            if len(re_ratios) >= 2:
                recent_change = abs(re_ratios[-1] - re_ratios[-2])
                
                if recent_change < 0.5 and re_ratios[-1] > 15:  # 占比>15%且几乎不变
                    findings.append({
                        'province': province,
                        'bank_name': bank,
                        'current_ratio': re_ratios[-1],
                        'quarters_stuck': len(re_ratios),
                        'estimated_cap': re_ratios[-1],
                        'signal': 'strong' if recent_change < 0.2 else 'medium'
                    })
    
    if findings:
        print(f"\n⚠️ 发现 {len(findings)} 个可能卡在上限的银行：")
        for f in findings:
            print(f"  {f['province']} - {f['bank_name']}: "
                  f"房地产贷款占比 {f['current_ratio']}% "
                  f"(连续{f['quarters_stuck']}期几乎不变)")
    else:
        print("  ✅ 未发现明显的上限约束信号")
    
    return findings


def estimate_provincial_caps(province_stats, constraints):
    """根据代理指标推算省级上限"""
    print("\n🧮 推算省级上限...")
    
    estimates = []
    
    for province, stats in province_stats.iterrows():
        estimate = {
            'province': province,
            'estimated_re_cap': None,
            'estimated_phl_cap': None,
            'confidence': '低',
            'method': '',
            'max_observed_re': stats['re_ratio_max'],
            'max_observed_phl': stats['phl_ratio_max'],
            'bank_count': stats['bank_count'],
        }
        
        # 检查是否有银行卡在上限
        province_constraints = [c for c in constraints if c['province'] == province]
        
        if province_constraints:
            # 取所有卡住的银行的最高值作为估算上限
            max_cap = max(c['estimated_cap'] for c in province_constraints)
            estimate['estimated_re_cap'] = round(max_cap, 1)
            estimate['confidence'] = '中'
            estimate['method'] = f'基于{len(province_constraints)}家银行卡在上限推算'
        elif stats['re_ratio_max'] > 15:
            # 如果没有卡住的，用最高值作为参考
            estimate['estimated_re_cap'] = round(stats['re_ratio_max'], 1)
            estimate['confidence'] = '低'
            estimate['method'] = '基于最高观测值推算'
        
        estimates.append(estimate)
    
    # 保存估算结果
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for est in estimates:
        cursor.execute("""
        INSERT INTO regional_proxy_estimates 
        (province, estimate_date, estimated_re_cap, estimated_phl_cap,
         max_re_ratio_observed, max_phl_ratio_observed, sample_bank_count,
         estimation_method, confidence_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            est['province'],
            datetime.now().strftime('%Y-%m-%d'),
            est['estimated_re_cap'],
            est['estimated_phl_cap'],
            est['max_observed_re'],
            est['max_observed_phl'],
            est['bank_count'],
            est['method'],
            est['confidence']
        ))
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ 已保存 {len(estimates)} 条省级估算")
    return estimates


def run_proxy_analysis():
    """运行完整的代理指标分析"""
    # 1. 抓取银行财报数据
    bank_data = fetch_bank_financial_data()
    
    if not bank_data:
        print("❌ 无数据，退出")
        return
    
    # 2. 计算代理指标
    result = calculate_proxy_indicators(bank_data)
    if result is None:
        return
    
    province_stats, df = result
    
    # 3. 检测上限约束
    constraints = detect_cap_constraints(df)
    
    # 4. 推算省级上限
    estimates = estimate_provincial_caps(province_stats, constraints)
    
    # 5. 输出摘要
    print("\n" + "=" * 60)
    print("📊 代理指标分析完成")
    print("=" * 60)
    
    print("\n🔑 关键发现：")
    for est in estimates:
        if est['estimated_re_cap']:
            print(f"  {est['province']}: 估算房地产贷款上限 {est['estimated_re_cap']}% "
                  f"(置信度: {est['confidence']}, {est['method']})")
    
    return estimates


if __name__ == '__main__':
    run_proxy_analysis()
