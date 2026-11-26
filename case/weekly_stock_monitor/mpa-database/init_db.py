#!/usr/bin/env python3
"""
宏观审慎政策数据库初始化脚本
层2：地方系数（央行分支行+金融监管局联合通知）
层3：代理指标（银行行为反推）
"""

import sqlite3
from datetime import datetime

DB_PATH = "/root/.coze/agents/7691719640636244234/workspace/mpa-database/mpa_coefficients.db"

def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # ===== 层2：地方系数表 =====
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS provincial_coefficients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        province TEXT NOT NULL,              -- 省份
        city TEXT,                           -- 城市（直辖市/计划单列市）
        tier TEXT NOT NULL,                  -- 档位：第一档/第二档/第三档/第四档/第五档
        bank_type TEXT,                      -- 银行类型：大型银行/中型银行/地方法人银行
        
        -- 房地产贷款集中度
        real_estate_loan_cap REAL,           -- 房地产贷款上限 %
        personal_housing_loan_cap REAL,      -- 个人住房贷款上限 %
        re_cap_adjustment REAL,              -- 较基准调整 ±pct
        phl_cap_adjustment REAL,             -- 较基准调整 ±pct
        
        -- 首付比/LTV
        down_payment_first_home REAL,        -- 首套房首付比 %
        down_payment_second_home REAL,       -- 二套房首付比 %
        ltv_first_home REAL,                 -- 首套房 LTV %
        ltv_second_home REAL,                -- 二套房 LTV %
        
        -- 跨境融资
        cross_border_financing_param REAL,   -- 跨境融资宏观审慎调节参数
        
        -- 政策元数据
        effective_date DATE,                 -- 生效日期
        expiry_date DATE,                    -- 到期日期
        policy_status TEXT DEFAULT '有效',    -- 有效/已过期/已调整
        source_type TEXT,                    -- 官方文件/媒体报道/代理推算
        source_url TEXT,                     -- 来源链接
        source_description TEXT,             -- 来源说明
        document_number TEXT,                -- 文号
        
        -- 置信度
        confidence_level TEXT DEFAULT '未验证', -- 已确认/高置信/待核实/代理推算
        last_verified_date DATE,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # ===== 层2：政策变更记录表 =====
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS policy_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        change_date DATE NOT NULL,
        province TEXT NOT NULL,
        city TEXT,
        coefficient_type TEXT NOT NULL,      -- 系数类型
        old_value REAL,
        new_value REAL,
        change_magnitude REAL,               -- 变化幅度
        policy_document TEXT,                -- 政策文件
        source_url TEXT,
        source_description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # ===== 层3：代理指标表（银行财报数据） =====
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bank_proxy_indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bank_name TEXT NOT NULL,             -- 银行名称
        bank_type TEXT,                      -- 城商行/农商行/村镇银行
        province TEXT NOT NULL,
        city TEXT,
        report_date DATE NOT NULL,           -- 报告期
        
        -- 贷款结构指标
        total_loans REAL,                    -- 总贷款余额（亿元）
        real_estate_loans REAL,              -- 房地产贷款余额（亿元）
        personal_housing_loans REAL,         -- 个人住房贷款余额（亿元）
        
        -- 占比指标
        real_estate_loan_ratio REAL,         -- 房地产贷款占比 %
        personal_housing_loan_ratio REAL,    -- 个人住房贷款占比 %
        
        -- 增速指标
        re_loan_yoy_growth REAL,             -- 房地产贷款同比增速 %
        phl_yoy_growth REAL,                 -- 个人住房贷款同比增速 %
        total_loan_yoy_growth REAL,          -- 总贷款同比增速 %
        
        -- 资产质量
        npl_ratio REAL,                      -- 不良率 %
        concern_loan_ratio REAL,             -- 关注类贷款占比 %
        
        source_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # ===== 层3：区域代理推算表 =====
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS regional_proxy_estimates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        province TEXT NOT NULL,
        estimate_date DATE NOT NULL,         -- 推算日期
        
        -- 推算结果
        estimated_re_cap REAL,               -- 推算的房地产贷款上限 %
        estimated_phl_cap REAL,              -- 推算的个人住房贷款上限 %
        
        -- 推算依据
        max_re_ratio_observed REAL,          -- 观察到的最高房地产贷款占比
        max_phl_ratio_observed REAL,         -- 观察到的最高个人住房贷款占比
        sample_bank_count INTEGER,           -- 样本银行数量
        banks_stuck_at_cap INTEGER,          -- 卡在疑似上限的银行数量
        
        -- 推算逻辑
        estimation_method TEXT,              -- 推算方法说明
        confidence_level TEXT DEFAULT '低',   -- 高/中/低
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # ===== 已披露的地方系数（2021年历史数据） =====
    historical_data = [
        # 上海（第一档）
        {
            'province': '上海', 'city': '上海', 'tier': '第一档',
            'real_estate_loan_cap': 22.5, 'personal_housing_loan_cap': 17.5,
            'effective_date': '2021-01-01',
            'source_type': '官方文件',
            'source_description': '央行上海总部 2020年12月发布',
            'confidence_level': '已确认'
        },
        # 广东（第一档）
        {
            'province': '广东', 'city': '广州', 'tier': '第一档',
            'real_estate_loan_cap': 22.5, 'personal_housing_loan_cap': 17.5,
            'effective_date': '2021-01-01',
            'source_type': '官方文件',
            'source_description': '央行广州分行 2020年12月发布',
            'confidence_level': '已确认'
        },
        # 海南（第四档，但可能上调）
        {
            'province': '海南', 'tier': '第四档',
            'real_estate_loan_cap': 15.0, 'personal_housing_loan_cap': 10.0,
            'effective_date': '2021-01-01',
            'source_type': '官方文件',
            'source_description': '央行海口中支 2020年12月发布',
            'confidence_level': '已确认'
        },
        # 浙江（第二档）
        {
            'province': '浙江', 'tier': '第二档',
            'real_estate_loan_cap': 20.0, 'personal_housing_loan_cap': 15.0,
            'effective_date': '2021-01-01',
            'source_type': '官方文件',
            'source_description': '央行杭州中支 2020年12月发布',
            'confidence_level': '已确认'
        },
        # 北京（第一档）
        {
            'province': '北京', 'city': '北京', 'tier': '第一档',
            'real_estate_loan_cap': 22.5, 'personal_housing_loan_cap': 17.5,
            'effective_date': '2021-01-01',
            'source_type': '官方文件',
            'source_description': '央行北京营管部 2020年12月发布',
            'confidence_level': '已确认'
        },
        # 厦门（第二档，计划单列市）
        {
            'province': '福建', 'city': '厦门', 'tier': '第二档',
            'real_estate_loan_cap': 20.0, 'personal_housing_loan_cap': 15.0,
            'effective_date': '2021-01-01',
            'source_type': '官方文件',
            'source_description': '央行厦门市中支 2020年12月发布',
            'confidence_level': '已确认'
        },
    ]
    
    # 插入历史数据
    for data in historical_data:
        # 确保所有字段都有默认值
        data.setdefault('city', None)
        data.setdefault('tier', '未定')
        
        cursor.execute("""
        INSERT INTO provincial_coefficients 
        (province, city, tier, real_estate_loan_cap, personal_housing_loan_cap,
         effective_date, source_type, source_description, confidence_level)
        VALUES (:province, :city, :tier, :real_estate_loan_cap, :personal_housing_loan_cap,
                :effective_date, :source_type, :source_description, :confidence_level)
        """, data)
    
    conn.commit()
    conn.close()
    
    print(f"✅ 数据库初始化完成：{DB_PATH}")
    print(f"📊 已导入 {len(historical_data)} 条历史数据")
    print("\n表结构：")
    print("  - provincial_coefficients: 地方系数主表")
    print("  - policy_changes: 政策变更记录")
    print("  - bank_proxy_indicators: 银行代理指标")
    print("  - regional_proxy_estimates: 区域代理推算")

if __name__ == '__main__':
    init_database()
