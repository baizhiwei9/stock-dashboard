#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - 离线版本
使用预设的A股列表筛选
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


def main():
    print("=" * 60)
    print("A股多头排列筛选器 (离线版本)")
    print("=" * 60)
    
    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-01"
    
    print(f"筛选基准日: {TODAY}")
    
    # 使用预设的A股代码列表（主要股票）
    # 这里包含沪深主板和创业板的代表性股票
    preset_stocks = [
        # 上海主板
        ('sh.600000', '浦发银行'), ('sh.600016', '民生银行'), ('sh.600019', '宝钢股份'),
        ('sh.600028', '中国石化'), ('sh.600030', '中信证券'), ('sh.600036', '招商银行'),
        ('sh.600048', '保利发展'), ('sh.600050', '中国联通'), ('sh.600104', '上汽集团'),
        ('sh.600109', '国金证券'), ('sh.600111', '北方稀土'), ('sh.600150', '中国船舶'),
        ('sh.600153', '建发股份'), ('sh.600170', '上海建工'), ('sh.600183', '生益科技'),
        ('sh.600196', '复星医药'), ('sh.600276', '恒瑞医药'), ('sh.600309', '万华化学'),
        ('sh.600352', '浙江龙盛'), ('sh.600406', '国电南瑞'), ('sh.600436', '片仔癀'),
        ('sh.600519', '贵州茅台'), ('sh.600547', '山东黄金'), ('sh.600570', '恒生电子'),
        ('sh.600585', '海螺水泥'), ('sh.600588', '用友网络'), ('sh.600606', '绿地控股'),
        ('sh.600690', '海尔智家'), ('sh.600703', '三安光电'), ('sh.600745', '闻泰科技'),
        ('sh.600760', '中航沈飞'), ('sh.600809', '山西汾酒'), ('sh.600887', '伊利股份'),
        ('sh.600900', '长江电力'), ('sh.600905', '三峡能源'), ('sh.600918', '中泰证券'),
        ('sh.600999', '招商证券'), ('sh.601006', '大秦铁路'), ('sh.601012', '隆基绿能'),
        ('sh.601066', '中信建投'), ('sh.601088', '中国神华'), ('sh.601118', '海南橡胶'),
        ('sh.601166', '兴业银行'), ('sh.601169', '北京银行'), ('sh.601186', '中国铁建'),
        ('sh.601211', '国泰君安'), ('sh.601225', '陕西煤业'), ('sh.601288', '农业银行'),
        ('sh.601318', '中国平安'), ('sh.601328', '交通银行'), ('sh.601336', '新华保险'),
        ('sh.601398', '工商银行'), ('sh.601601', '中国太保'), ('sh.601628', '中国人寿'),
        ('sh.601668', '中国建筑'), ('sh.601688', '华泰证券'), ('sh.601698', '中国卫通'),
        ('sh.601728', '中国电信'), ('sh.601766', '中国中车'), ('sh.601800', '中国交建'),
        ('sh.601816', '京沪高铁'), ('sh.601857', '中国石油'), ('sh.601888', '中国中免'),
        ('sh.601899', '紫金矿业'), ('sh.601919', '中远海控'), ('sh.601939', '建设银行'),
        ('sh.601985', '中国核电'), ('sh.601988', '中国银行'), ('sh.601989', '中国重工'),
        ('sh.601995', '中金公司'), ('sh.601998', '中信银行'), ('sh.603259', '药明康德'),
        ('sh.603288', '海天味业'), ('sh.603501', '韦尔股份'), ('sh.603799', '华友钴业'),
        ('sh.603806', '福斯特'), ('sh.603986', '兆易创新'),
        # 深圳主板
        ('sz.000001', '平安银行'), ('sz.000002', '万科A'), ('sz.000063', '中兴通讯'),
        ('sz.000100', 'TCL科技'), ('sz.000333', '美的集团'), ('sz.000425', '徐工机械'),
        ('sz.000538', '云南白药'), ('sz.000568', '泸州老窖'), ('sz.000596', '古井贡酒'),
        ('sz.000651', '格力电器'), ('sz.000661', '长春高新'), ('sz.000708', '中信特钢'),
        ('sz.000725', '京东方A'), ('sz.000768', '中航西飞'), ('sz.000858', '五粮液'),
        ('sz.000876', '新希望'), ('sz.000895', '双汇发展'), ('sz.000938', '紫光股份'),
        ('sz.001979', '招商蛇口'), 
        # 创业板
        ('sz.300001', '特锐德'), ('sz.300014', '亿纬锂能'), ('sz.300015', '爱尔眼科'),
        ('sz.300033', '同花顺'), ('sz.300059', '东方财富'), ('sz.300122', '智飞生物'),
        ('sz.300124', '汇川技术'), ('sz.300142', '沃森生物'), ('sz.300223', '北京君正'),
        ('sz.300274', '阳光电源'), ('sz.300284', '苏试试验'), ('sz.300315', '冠昊生物'),
        ('sz.300347', '泰格医药'), ('sz.300363', '博腾股份'), ('sz.300396', '迪瑞医疗'),
        ('sz.300450', '先导智能'), ('sz.300529', '健帆生物'), ('sz.300595', '欧普康视'),
        ('sz.300661', '圣邦股份'), ('sz.300676', '华大基因'), ('sz.300750', '宁德时代'),
        ('sz.300759', '康龙化成'), ('sz.300760', '迈瑞医疗'), ('sz.300782', '卓胜微'),
        ('sz.300896', '爱美客'), ('sz.300919', '中伟股份'), ('sz.300957', '贝泰妮'),
        ('sz.300999', '金山办公'),
    ]
    
    stock_df = pd.DataFrame(preset_stocks, columns=['code', 'name'])
    codes = stock_df['code'].tolist()
    print(f"预设股票数量: {len(codes)}")
    
    if not codes:
        print("没有符合条件的股票")
        return
    
    # 生成模拟数据用于演示
    # 注意：实际使用时应该从真实数据源获取
    print("\n⚠️ 注意：当前使用预设数据演示")
    print("由于数据源连接问题，请使用真实数据源获取完整筛选结果")
    
    # 创建演示结果
    results = []
    import random
    random.seed(42)  # 固定种子保证可复现
    
    for code, name in preset_stocks[:30]:  # 取前30只演示
        base_price = random.uniform(10, 200)
        ma20 = base_price * random.uniform(0.95, 0.98)
        ma40 = base_price * random.uniform(0.90, 0.95)
        ma80 = base_price * random.uniform(0.85, 0.92)
        ma160 = base_price * random.uniform(0.80, 0.90)
        ytd_return = random.uniform(-20, 80)
        
        if base_price > ma20 > ma40 > ma80 > ma160:
            exchange = 'SH' if code.startswith('sh') else 'SZ'
            stock_code = code.replace('sh.', '').replace('sz.', '')
            results.append({
                '股票代码': stock_code,
                '交易所': exchange,
                '股票简称': name,
                '当前价': round(base_price, 2),
                'MA20': round(ma20, 2),
                'MA40': round(ma40, 2),
                'MA80': round(ma80, 2),
                'MA160': round(ma160, 2),
                '年内涨幅': round(ytd_return, 2)
            })
    
    print(f"\n演示数据筛选完成！共 {len(results)} 只股票满足条件（演示数据）")
    
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 年内涨幅Top20（演示数据）：")
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            ytd = f"{row['年内涨幅']:.2f}%"
            print(f"  {code_full} {row['股票简称']} 价:{row['当前价']} 涨:{ytd}")
        
        # 生成Markdown报告
        md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
        md_content += f"**⚠️ 警告**: 此文件为演示数据，请运行真实筛选脚本获取实际数据\n\n"
        md_content += f"**筛选基准日**: {TODAY} (最近交易日)\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**排序方式**: 按年内涨幅降序排列\n\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {row['年内涨幅']} |\n"
        
        md_content += f"\n*⚠️ 注意: 当前为演示数据，真实数据请使用真实数据源*\n"
        md_content += "*案例分析学习材料，不构成投资建议*\n"
        md_content += "*股市有风险，投资需谨慎*\n"
        
        # 保存结果
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(script_dir)
        output_dir = os.path.join(project_dir, "outputs", "股票筛选")
        os.makedirs(output_dir, exist_ok=True)
        
        output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 已保存: {output_file}")
        print("=" * 60)
        print("⚠️ 任务完成（演示数据）")
        print("=" * 60)


if __name__ == '__main__':
    main()
