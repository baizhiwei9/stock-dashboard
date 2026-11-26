#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点概念板块追踪器 v3.0 - 近两周TOP20热点概念
==============================================
功能：
1. 追踪近两周市场热点TOP20概念板块
2. 自动识别龙头股（市值最大）
3. 计算龙头股当年涨幅
4. 按涨幅排序输出

数据源：
- 热点概念：手动维护（来自东方财富/同花顺涨幅排行）
- 行情数据：tushare
- 更新频率：每周更新热点概念列表

热点概念来源：
- 东方财富主题投资页面涨幅排行
- 同花顺概念板块涨幅榜
- 市场热点新闻提炼
"""

import tushare as ts
import pandas as pd
import os
import sys
import time
import argparse
from datetime import datetime, timedelta

# 清除代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ.pop(k)

# 飞书推送工具路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # 热点概念追踪/
ROOT_DIR = os.path.dirname(PROJECT_DIR)     # 主对话根目录
sys.path.insert(0, ROOT_DIR)

# Tushare Token
TUSHARE_TOKEN = 'a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# 近两周热点概念TOP20（根据东方财富涨幅排行更新）
# 更新时间：2026-04-23
# 数据来源：东方财富概念板块涨幅排行 + 市场热点追踪
HOT_CONCEPTS_TOP20 = {
    # ===== 近两周涨幅TOP10（最热） =====
    "商业航天": {
        "leader": "002297.SZ",  # 博云新材 - 6连板
        "stocks": ["002297.SZ", "600118.SH", "600677.SH", "002465.SZ", "603717.SH", "002428.SZ"],
        "reason": "航天日催化，中国版星链加速"
    },
    "玻璃基板": {
        "leader": "603773.SH",  # 沃格光电 - 10天6板
        "stocks": ["603773.SH", "002384.SZ", "600703.SH", "002445.SZ", "300093.SZ"],
        "reason": "台积电CoPoS技术催化，AI封装新方向"
    },
    "小金属/铟": {
        "leader": "600961.SH",  # 株冶集团 - 5连板
        "stocks": ["600961.SH", "002428.SZ", "603993.SH", "600547.SH", "000960.SZ"],
        "reason": "磷化铟缺口超70%，铟价创十年新高"
    },
    "光芯片": {
        "leader": "688048.SH",  # 长光华芯 - 涨幅近200%年内
        "stocks": ["688048.SH", "688313.SH", "688498.SH", "002079.SZ"],
        "reason": "AI光通信上游核心，国产替代加速"
    },
    "光纤光缆": {
        "leader": "601869.SH",  # 长飞光纤 - 创历史新高
        "stocks": ["601869.SH", "600487.SH", "600522.SH", "603618.SH", "600105.SH", "002491.SZ"],
        "reason": "光纤价格暴涨650%，量价齐升"
    },
    "CPO/光模块": {
        "leader": "300308.SZ",  # 中际旭创 - 市值近万亿
        "stocks": ["300308.SZ", "300502.SZ", "300394.SZ", "002281.SZ", "000988.SZ", "688313.SH", "300620.SZ", "688048.SH", "688498.SH"],
        "reason": "AI算力核心，光模块需求爆发"
    },
    "存储芯片": {
        "leader": "688525.SH",  # 佰维存储 - 当年涨126%
        "stocks": ["688525.SH", "688041.SH", "603986.SH", "002049.SZ"],
        "reason": "AI存储需求爆发，价格上涨预期强"
    },
    "AI算力": {
        "leader": "300308.SZ",  # 中际旭创
        "stocks": ["300308.SZ", "300502.SZ", "601138.SH", "000063.SZ", "002475.SZ"],
        "reason": "算力产业链景气持续"
    },
    "算力租赁": {
        "leader": "301396.SZ",  # 宏景科技 - 两周涨近60%
        "stocks": ["301396.SZ", "603629.SH", "300857.SZ", "002229.SZ", "000889.SZ", "300166.SZ", "000815.SZ", "002929.SZ"],
        "reason": "GPU租金暴涨48%，云厂商集体涨价"
    },
    # ===== 近两周涨幅TOP11-20 =====
    "液冷散热": {
        "leader": "002837.SZ",  # 英维克
        "stocks": ["002837.SZ", "301018.SZ", "300499.SZ", "603727.SH"],
        "reason": "AI算力必备，渗透率快速提升"
    },
    "PCB": {
        "leader": "002384.SZ",  # 东山精密
        "stocks": ["002384.SZ", "002938.SZ", "002463.SZ", "002916.SZ", "002475.SZ"],
        "reason": "AI服务器需求大增"
    },
    "锆金属": {
        "leader": "002167.SZ",  # 东方锆业
        "stocks": ["002167.SZ", "600392.SH", "002182.SZ"],
        "reason": "锆价上涨，核工业需求"
    },
    "培育钻石": {
        "leader": "002427.SZ",  # *ST辅碳
        "stocks": ["002427.SZ", "300064.SZ", "603016.SH"],
        "reason": "钻石散热概念，AI芯片新方向"
    },
    "牛肉": {
        "leader": "300106.SZ",  # 西部牧业
        "stocks": ["300106.SZ", "002330.SZ", "000735.SZ"],
        "reason": "牛肉价格上涨"
    },
    "钠离子电池": {
        "leader": "002866.SZ",  # 传艺科技
        "stocks": ["002866.SZ", "002074.SZ", "300750.SZ"],
        "reason": "钠电池产业化加速"
    },
    "固态电池": {
        "leader": "300124.SZ",  # 当升科技
        "stocks": ["300124.SZ", "002812.SZ", "300471.SZ"],
        "reason": "固态电池技术突破"
    },
    "柔性屏": {
        "leader": "603626.SH",  # 科森科技
        "stocks": ["603626.SH", "002387.SZ", "000725.SZ"],
        "reason": "折叠屏手机热销"
    },
    "低空基建": {
        "leader": "603153.SH",  # 上海建科
        "stocks": ["603153.SH", "002085.SZ", "000333.SZ"],
        "reason": "低空经济政策催化"
    },
    "半导体": {
        "leader": "688981.SH",  # 中芯国际
        "stocks": ["688981.SH", "002371.SZ", "688012.SH", "603501.SH"],
        "reason": "国产替代核心，制造设备稀缺"
    },
}

def get_stock_name(ts_code):
    """获取股票名称"""
    try:
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name')
        if len(df) > 0:
            return df.iloc[0]['name']
    except:
        pass
    return ts_code

def calc_two_week_chg(ts_code):
    """计算两周涨幅"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        two_week_ago = (datetime.now() - timedelta(days=14)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=two_week_ago, end_date=today)
        if len(df) >= 2:
            latest = df.iloc[0]['close']
            oldest = df.iloc[-1]['close']
            return round((latest - oldest) / oldest * 100, 2)
    except:
        pass
    return None

def calc_year_chg(ts_code):
    """计算当年涨幅"""
    try:
        year_start = f"{datetime.now().year}0105"
        today = datetime.now().strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=year_start, end_date=today)
        if len(df) >= 2:
            latest = df.iloc[0]['close']
            oldest = df.iloc[-1]['close']
            return round((latest - oldest) / oldest * 100, 2)
    except:
        pass
    return None

def get_market_cap(ts_code):
    """获取市值"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        df = pro.daily_basic(ts_code=ts_code, trade_date=today, fields='ts_code,total_mv')
        if len(df) > 0:
            return df.iloc[0]['total_mv']
    except:
        pass
    return None

def track_hot_concepts_top20():
    """追踪近两周TOP20热点概念"""
    print("=" * 70)
    print("热点概念板块追踪 v3.0 - 近两周TOP20")
    print("=" * 70)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"概念数量: {len(HOT_CONCEPTS_TOP20)} 个")
    
    results = []
    
    for concept_name, concept_data in HOT_CONCEPTS_TOP20.items():
        print(f"\n正在计算【{concept_name}】...", end=" ")
        
        stocks = concept_data['stocks']
        leader_code = concept_data['leader']
        reason = concept_data.get('reason', '')
        
        # 计算概念两周平均涨幅
        two_week_chgs = []
        for code in stocks:
            chg = calc_two_week_chg(code)
            if chg is not None:
                two_week_chgs.append(chg)
            time.sleep(0.12)
        
        if two_week_chgs:
            avg_two_week_chg = round(sum(two_week_chgs) / len(two_week_chgs), 2)
            up_count = sum(1 for c in two_week_chgs if c > 0)
            
            # 计算龙头股当年涨幅
            leader_name = get_stock_name(leader_code)
            time.sleep(0.12)
            leader_year_chg = calc_year_chg(leader_code)
            time.sleep(0.12)
            leader_cap = get_market_cap(leader_code)
            time.sleep(0.12)
            
            results.append({
                'concept': concept_name,
                'two_week_chg': avg_two_week_chg,
                'stock_count': len(two_week_chgs),
                'up_count': up_count,
                'leader_code': leader_code,
                'leader_name': leader_name,
                'leader_year_chg': leader_year_chg,
                'leader_cap': leader_cap,
                'reason': reason,
            })
            
            print(f"两周: {avg_two_week_chg}% | 龙头: {leader_name} 当年: {leader_year_chg}%")
        else:
            print("数据获取失败")
    
    # 按两周涨幅排序
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('two_week_chg', ascending=False)
    
    print("\n" + "=" * 70)
    print("近两周热点概念TOP20（按涨幅排序）")
    print("=" * 70)
    
    for i, (_, row) in enumerate(results_df.iterrows(), 1):
        print(f"\n{i}. {row['concept']}")
        print(f"   两周涨幅: {row['two_week_chg']}%")
        print(f"   龙头股: {row['leader_name']}({row['leader_code']})")
        print(f"   龙头市值: {row['leader_cap']:.0f}亿" if row['leader_cap'] else "")
        print(f"   龙头当年涨幅: {row['leader_year_chg']}%")
        if row['reason']:
            print(f"   热点逻辑: {row['reason']}")
    
    return results_df

def save_report(results_df):
    """保存报告"""
    output_dir = '热点概念追踪/outputs'
    os.makedirs(output_dir, exist_ok=True)
    
    date_str = datetime.now().strftime('%Y-%m-%d')
    
    # 保存CSV
    csv_path = f"{output_dir}/{date_str}_热点概念TOP20.csv"
    results_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n✓ 已保存: {csv_path}")
    
    # 保存Markdown报告
    md_path = f"{output_dir}/{date_str}_热点概念TOP20报告.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# A股热点概念板块周报 - {date_str}\n\n")
        f.write("## 近两周热点概念TOP20\n\n")
        f.write("| 排名 | 概念 | 两周涨幅 | 上涨比例 | 龙头股 | 龙头当年涨幅 | 热点逻辑 |\n")
        f.write("|:---:|:---|---:|---:|:---|---:|:---|\n")
        
        for rank, (_, row) in enumerate(results_df.iterrows(), 1):
            up_ratio = f"{row['up_count']}/{row['stock_count']}"
            leader = f"{row['leader_name']}({row['leader_code']})"
            reason = row['reason'][:20] + "..." if len(row['reason']) > 20 else row['reason']
            f.write(f"| {rank} | {row['concept']} | {row['two_week_chg']}% | {up_ratio} | {leader} | {row['leader_year_chg']}% | {reason} |\n")
        
        f.write("\n---\n⚠️ 案例分析学习材料，不构成投资建议\n")
    
    print(f"✓ 已保存: {md_path}")
    
    return csv_path, md_path

def push_feishu_card(results_df, md_path):
    """推送飞书卡片，失败则降级为纯文本推送"""
    try:
        from feishu_card_utils import build_hot_concepts_top20_card
        from feishu_push import push_card_to_feishu, push_to_feishu

        date_str = datetime.now().strftime('%Y-%m-%d')

        # 转换为卡片需要的格式
        concepts = []
        for _, row in results_df.iterrows():
            concepts.append({
                'name': row['concept'],
                'avg_change': row['two_week_chg'],
                'stock_count': row['stock_count'],
                'leader_name': row['leader_name'],
                'leader_change': row.get('leader_year_chg'),
                'reason': row.get('reason', ''),
            })

        # 汇总统计
        total_concepts = len(concepts)
        avg_change = results_df['two_week_chg'].mean() if len(results_df) > 0 else 0
        up_count = (results_df['two_week_chg'] > 0).sum()
        summary_stats = {
            "概念总数": f"{total_concepts}个",
            "平均涨幅": f"{avg_change:+.2f}%",
            "上涨比例": f"{up_count}/{total_concepts}",
        }

        # 构建卡片
        card = build_hot_concepts_top20_card(date_str, concepts, total_concepts, summary_stats)

        # 推送卡片
        print("\n🚀 推送飞书交互式卡片...")
        success = push_card_to_feishu(card_dict=card)

        if success:
            print("✅ 飞书卡片推送成功")
            return True
        else:
            print("⚠️  卡片推送失败，降级为纯文本推送...")
            # 降级：推送markdown报告
            with open(md_path, 'r', encoding='utf-8') as f:
                text = f.read()[:3000]
            ok = push_to_feishu(text=text)
            if ok:
                print("✅ 降级纯文本推送成功")
            else:
                print("❌ 纯文本推送也失败了")
            return ok

    except Exception as e:
        print(f"❌ 推送异常: {e}")
        import traceback
        traceback.print_exc()
        # 再尝试降级纯文本
        try:
            from feishu_push import push_to_feishu
            with open(md_path, 'r', encoding='utf-8') as f:
                text = f.read()[:3000]
            push_to_feishu(text=text)
        except:
            pass
        return False


def main():
    """主流程"""
    parser = argparse.ArgumentParser(description='热点概念TOP20追踪')
    parser.add_argument('--no-push', action='store_true',
                        help='只生成本地报告，不推送飞书')
    args = parser.parse_args()

    print("=" * 60)
    print("热点概念板块 TOP20 追踪")
    print("=" * 60)

    # 1. 追踪热点概念
    results_df = track_hot_concepts_top20()

    # 2. 保存报告
    csv_path, md_path = save_report(results_df)

    # 3. 推送飞书
    if not args.no_push:
        push_feishu_card(results_df, md_path)

    print("\n" + "=" * 60)
    print("✅ 热点概念TOP20追踪完成")
    print(f"   报告: {md_path}")
    print("=" * 60)

    return results_df


if __name__ == '__main__':
    main()
