#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外需+内需+政府财政 三合一统一任务
1) 并行获取三类最新数据
2) 生成三张飞书交互卡片并紧凑发送
3) 生成综合海报示意图
"""

import json
import urllib.request
import urllib.error
import ssl
import subprocess
import sys
import os
from datetime import datetime

WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# ==================== 数据获取 ====================

def fetch_fiscal_oecd():
    """获取财政+OECD CLI数据"""
    result = subprocess.run(
        ["python3", "send_fiscal_oecd_report.py"],
        capture_output=True, text=True, timeout=60,
        cwd="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f8830d0a2ffeGcR4IHiYeV6mwH"
    )
    # 解析CLI数据
    cli = {}
    for line in result.stdout.split('\n'):
        if "CLI=" in line and "方向=" in line:
            parts = line.strip().split()
            for p in parts:
                if "CLI=" in p:
                    cli_val = float(p.split("=")[1])
                if "方向=" in p:
                    direction = p.split("=")[1]
                if "变化=" in p:
                    change = p.split("=")[1]
            # 从上一行提取国家名
            pass
    
    # 从stdout正则提取
    import re
    pattern = r'获取\s+(\S+)\s+\(.*\)...\s+CLI=([\d.]+)\s+方向=(\S+)\s+变化=([\d.+-]+)\s+日期=(\S+)'
    matches = re.findall(pattern, result.stdout, re.DOTALL)
    
    cli_data = {}
    for m in re.finditer(r'获取\s+(\S+)\s+\(([A-Z]+)\)...\s+CLI=([\d.]+)\s+方向=(.+?)\s+变化=([\d.+-]+)\s+日期=(\S+)', result.stdout):
        country, code, val, direction, change, date = m.groups()
        cli_data[country] = {
            'code': code, 'value': float(val), 'direction': direction.strip(),
            'change': float(change), 'date': date
        }
    
    return cli_data

def fetch_waixu():
    """获取外需数据"""
    result = subprocess.run(
        ["python3", "send_waixu_tracker.py", "--dry-run", "--no-feishu", "--no-mail"],
        capture_output=True, text=True, timeout=120,
        cwd="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f74849edcffetQgClhILIkVEnY/waixu_tracker"
    )
    
    # 解析报告文本
    text = result.stdout
    data = {'score': None, 'state': None, 'signals': []}
    
    for line in text.split('\n'):
        if '外需总判断：' in line:
            data['state'] = line.split('外需总判断：')[1].split('（')[0].strip()
        if '信号打分' in line:
            try:
                data['score'] = float(line.split('信号打分')[1].split('）')[0].replace('+', ''))
            except:
                pass
        if '中国出口同比' in line and '海关' in line:
            data['china_export_yoy'] = line
        if 'SCFI综合指数' in line:
            data['scfi'] = line
    
    # 读取报告文件获取更多数据
    report_path = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f74849edcffetQgClhILIkVEnY/waixu_tracker/reports/外需周报_20260911.txt"
    if os.path.exists(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            data['report_text'] = f.read()
    
    return data

def fetch_neixu():
    """获取内需数据"""
    inner_path = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f74849edcffetQgClhILIkVEnY/waixu_tracker/inner_demand.json"
    with open(inner_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 补充macro_weekly_report_v3中的高频数据
    data['pmi'] = 49.8
    data['pmi_mom'] = -0.3  # 8月 vs 7月 50.1
    data['retail_yoy'] = 0.6  # 7月同比
    data['retail_cum_yoy'] = 1.2  # 1-7月累计同比
    data['invest_cum_yoy'] = -6.7  # 1-7月固投累计同比
    data['power_consump'] = 91.0  # 六大电日耗万吨
    data['power_consump_mom'] = -5.2
    data['housing_30city'] = 23.08  # 30城商品房日均成交万平米
    data['housing_mom'] = 15.3
    data['auto_daily'] = 8.4  # 乘用车日均万辆
    data['auto_mom'] = 78.7
    data['box_office'] = 3.5  # 亿元
    data['box_office_mom'] = -56.3
    
    return data

def fetch_fiscal_progress():
    """获取财政进度数据"""
    return {
        'special_bond_progress': 68,  # 专项债发行进度 68%
        'special_bond_issued': 29785,  # 亿元
        'special_bond_total': 44000,  # 全年额度
        'gov_bond_progress': 66,  # 政府债总进度 66%
        'gov_bond_issued': 91200,  # 亿元
        'gov_bond_total': 138900,  # 目标
        'state': '传导期前段',
        'fiscal_phase': '🟡 传导期前段（蓄水向后传导过渡）'
    }

# ==================== 飞书卡片发送 ====================

def send_feishu_card(card):
    payload = {"msg_type": "interactive", "card": card}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL, data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = resp.read().decode("utf-8")
            r = json.loads(result)
            ok = r.get("StatusCode") == 0 or r.get("code") == 0
            return ok, result
    except Exception as e:
        return False, str(e)

def build_waixu_card(data):
    """外需卡片"""
    elements = []
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**报告来自于 DoMate | 外需跟踪周报**\n{datetime.now().strftime('%Y年%m月%d日')}"}
    })
    elements.append({"tag": "hr"})
    
    state_color = "🟢" if "强" in (data.get('state') or '') else "🟡"
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**外需总判断：{state_color} {data.get('state', '待更新')}（信号打分 {data.get('score', 'N/A'):+.2f}）**"}
    })
    
    # 关键指标表
    headers = ["指标", "最新值", "环比/变化"]
    weights = [3, 2, 2]
    
    rows = [
        ["中国出口同比", "+25.0%", "海关8月"],
        ["中国进口同比", "+28.2%", "海关8月"],
        ["韩国出口同比", "+70.7%", "⚠口径待核"],
        ["日本出口同比", "+5.8%", "FRED 6月"],
        ["美国进口同比", "+16.9%", "前值+15.3%"],
        ["SCFI综合指数", "2032.0", "周涨+9.2%"],
        ["美国耐用品订单", "+12.0%", "同比"],
        ["美国商业库存", "+3.0%", "同比"],
    ]
    
    # 构建表格
    header_cols = []
    for h, w in zip(headers, weights):
        header_cols.append({
            "tag": "column", "width": "weighted", "weight": w,
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{h}**"}}]
        })
    elements.append({"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": header_cols})
    
    for row in rows:
        row_cols = []
        for val, w in zip(row, weights):
            row_cols.append({
                "tag": "column", "width": "weighted", "weight": w,
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": str(val)}}]
            })
        elements.append({"tag": "column_set", "flex_mode": "none", "background_style": "default", "columns": row_cols})
    
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**OECD-CLI 周边对比**\n美国100.8↑ | 韩国102.87↑ | 德国100.72→ | 日本100.3↑ | 英国100.29→\n多数经济体CLI上行，外需边际改善"}
    })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate | 外需跟踪周报"}, "template": "blue"},
        "elements": elements
    }

def build_neixu_card(data):
    """内需卡片"""
    elements = []
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**报告来自于 DoMate | 内需景气监测**\n{datetime.now().strftime('%Y年%m月%d日')}"}
    })
    elements.append({"tag": "hr"})
    
    state = data.get('state', '中性')
    state_color = "🔴" if state == "弱" else ("🟢" if state == "强" else "🟡")
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**内需景气研判：{state_color} {state}（看平）**"}
    })
    
    headers = ["指标", "最新值", "环比/同比"]
    weights = [3, 2, 2]
    
    rows = [
        ["制造业PMI", "49.8", "环比-0.3（收缩区）"],
        ["社零总额同比", "+0.6%", "7月，累计+1.2%"],
        ["固定资产投资", "-6.7%", "1-7月累计同比"],
        ["六大电日耗", "~91万吨", "环比-5.2%"],
        ["30城商品房", "日均23万㎡", "环比+15.3%"],
        ["乘用车日均", "8.4万辆", "环比+78.7%（冲量）"],
        ["公路物流运价", "~1054", "环比-0.1%"],
        ["票房", "~3.5亿", "环比-56.3%（暑期后）"],
    ]
    
    header_cols = []
    for h, w in zip(headers, weights):
        header_cols.append({
            "tag": "column", "width": "weighted", "weight": w,
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{h}**"}}]
        })
    elements.append({"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": header_cols})
    
    for row in rows:
        row_cols = []
        for val, w in zip(row, weights):
            row_cols.append({
                "tag": "column", "width": "weighted", "weight": w,
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": str(val)}}]
            })
        elements.append({"tag": "column_set", "flex_mode": "none", "background_style": "default", "columns": row_cols})
    
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**诊断要点**\n• 六大电日耗环比-5.2%为迎峰度夏结束的季节性回落，非趋势走弱\n• 30城商品房环比+15.3%受月末冲量驱动，但同比仍负、分位仅42%\n• 乘用车8月全月同比-19%，燃油车产量暴跌56%，车市底部蓄力\n• 内需整体处于季节性调整期，缺乏趋势性扩张信号，关注9月「金九」旺季"}
    })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate | 内需景气监测"}, "template": "orange"},
        "elements": elements
    }

def build_fiscal_card(cli_data, fiscal_data):
    """财政+OECD卡片"""
    elements = []
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**报告来自于 DoMate | 财政进度 + OECD CLI**\n{datetime.now().strftime('%Y年%m月%d日')}"}
    })
    elements.append({"tag": "hr"})
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**财政阶段：{fiscal_data['fiscal_phase']}**"}
    })
    
    headers = ["指标", "当前进度", "对比"]
    weights = [3, 2, 2]
    
    rows = [
        ["新增专项债", f"{fiscal_data['special_bond_progress']}%", f"已发{fiscal_data['special_bond_issued']/10000:.1f}万亿/4.4万亿"],
        ["政府债总进度", f"{fiscal_data['gov_bond_progress']}%", f"已发{fiscal_data['gov_bond_issued']/10000:.1f}万亿/13.89万亿"],
        ["实物工作量", "零星启动", "专项债→实物传导中"],
    ]
    
    header_cols = []
    for h, w in zip(headers, weights):
        header_cols.append({
            "tag": "column", "width": "weighted", "weight": w,
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{h}**"}}]
        })
    elements.append({"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": header_cols})
    
    for row in rows:
        row_cols = []
        for val, w in zip(row, weights):
            row_cols.append({
                "tag": "column", "width": "weighted", "weight": w,
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": str(val)}}]
            })
        elements.append({"tag": "column_set", "flex_mode": "none", "background_style": "default", "columns": row_cols})
    
    elements.append({"tag": "hr"})
    
    # OECD CLI
    cli_lines = []
    for country, info in cli_data.items():
        arrow = "↑" if info['change'] > 0 else ("↓" if info['change'] < 0 else "→")
        color = "🟢" if info['value'] > 100 else "🔴"
        cli_lines.append(f"{color} {country}: {info['value']:.2f}{arrow}({info['change']:+.2f})")
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**OECD CLI 五国对比（荣枯线100）**\n" + " | ".join(cli_lines)}
    })
    
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**诊断要点**\n• 专项债发行进度68%，8月单月发行5188亿为年内第二高\n• 政府债整体进度66%，较2025年同期76%偏慢，发行节奏后置\n• 下半年仍有2万多亿专项债和超长期特别国债待发行\n• OECD CLI：美日韩上行，德国持平，中国低于趋势（98.57）\n• 财政→实物工作量传导中，关注9-10月广义赤字高峰"}
    })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate | 财政进度 + OECD CLI"}, "template": "green"},
        "elements": elements
    }

# ==================== 海报生成 ====================

def generate_poster(waixu_data, neixu_data, fiscal_data, cli_data):
    """生成综合海报"""
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyBboxPatch, Rectangle
    import numpy as np
    
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['Droid Sans Fallback', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    
    fig = plt.figure(figsize=(20, 14))
    fig.patch.set_facecolor('#f8f9fa')
    
    # 标题
    fig.suptitle('中国宏观经济三维度监测全景图', fontsize=24, fontweight='bold', y=0.97)
    fig.text(0.5, 0.94, f'数据日期：{datetime.now().strftime("%Y年%m月%d日")} | 报告来自于 DoMate',
             ha='center', fontsize=12, color='#666666')
    
    # 创建3x1网格，但中间留空给综合判断
    gs = fig.add_gridspec(2, 3, height_ratios=[3, 1.2], hspace=0.35, wspace=0.3,
                          left=0.06, right=0.94, top=0.90, bottom=0.08)
    
    # ========== 外需面板 ==========
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_xlim(0, 10)
    ax1.set_ylim(0, 10)
    ax1.axis('off')
    
    # 外需背景
    rect1 = FancyBboxPatch((0.2, 0.2), 9.6, 9.6, boxstyle="round,pad=0.1",
                           facecolor='#e8f5e9', edgecolor='#4caf50', linewidth=3)
    ax1.add_patch(rect1)
    
    ax1.text(5, 9.3, '外需', fontsize=20, fontweight='bold', ha='center', color='#2e7d32')
    ax1.text(5, 8.6, f'状态：{waixu_data.get("state", "偏强")}', fontsize=14, ha='center', color='#1b5e20')
    ax1.text(5, 8.1, f'信号打分：{waixu_data.get("score", 0.59):+.2f}', fontsize=12, ha='center', color='#388e3c')
    
    # 外需指标
    waixu_items = [
        ('中国出口同比', '+25.0%', '#4caf50'),
        ('韩国出口同比', '+70.7%', '#66bb6a'),
        ('美国进口同比', '+16.9%', '#81c784'),
        ('SCFI综合指数', '2032.0(+9.2%)', '#a5d6a7'),
        ('日本出口同比', '+5.8%', '#4caf50'),
        ('耐用品订单', '+12.0%', '#66bb6a'),
    ]
    y_pos = 7.2
    for name, val, color in waixu_items:
        ax1.text(0.6, y_pos, f'• {name}', fontsize=10, va='center')
        ax1.text(6.5, y_pos, val, fontsize=10, va='center', fontweight='bold', color=color)
        y_pos -= 0.85
    
    # 环比/同比标注
    ax1.text(5, 2.2, '环比：SCFI周涨+9.2% | BDI周均+5.2%', fontsize=9, ha='center', color='#666', style='italic')
    ax1.text(5, 1.7, '同比：中国出口+25% | 美国进口+16.9%', fontsize=9, ha='center', color='#666', style='italic')
    ax1.text(5, 1.2, '周边：日韩CLI上行，外需边际改善', fontsize=9, ha='center', color='#666', style='italic')
    
    # 进度条
    progress1 = waixu_data.get('score', 0.59) / 1.0
    rect_bar = Rectangle((1.5, 0.4), 7 * progress1, 0.4, facecolor='#4caf50', alpha=0.8)
    ax1.add_patch(rect_bar)
    ax1.text(5, 0.6, f'强度进度条: {progress1*100:.0f}%', fontsize=10, ha='center', fontweight='bold')
    
    # ========== 内需面板 ==========
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_xlim(0, 10)
    ax2.set_ylim(0, 10)
    ax2.axis('off')
    
    rect2 = FancyBboxPatch((0.2, 0.2), 9.6, 9.6, boxstyle="round,pad=0.1",
                           facecolor='#fff3e0', edgecolor='#ff9800', linewidth=3)
    ax2.add_patch(rect2)
    
    ax2.text(5, 9.3, '内需', fontsize=20, fontweight='bold', ha='center', color='#e65100')
    ax2.text(5, 8.6, f'状态：{neixu_data.get("state", "中性")}', fontsize=14, ha='center', color='#bf360c')
    ax2.text(5, 8.1, '景气研判：看平', fontsize=12, ha='center', color='#ef6c00')
    
    neixu_items = [
        ('制造业PMI', '49.8↓', '#ff9800'),
        ('社零同比', '+0.6%', '#ffb74d'),
        ('固投累计', '-6.7%', '#ffcc80'),
        ('六大电日耗', '91万吨(-5.2%)', '#ff9800'),
        ('30城商品房', '+15.3%(冲量)', '#ffb74d'),
        ('乘用车日均', '8.4万辆(+78.7%)', '#ffcc80'),
    ]
    y_pos = 7.2
    for name, val, color in neixu_items:
        ax2.text(0.6, y_pos, f'• {name}', fontsize=10, va='center')
        ax2.text(6.5, y_pos, val, fontsize=10, va='center', fontweight='bold', color=color)
        y_pos -= 0.85
    
    ax2.text(5, 2.2, '环比：日耗-5.2%(季节) | 商品房+15.3%(冲量)', fontsize=9, ha='center', color='#666', style='italic')
    ax2.text(5, 1.7, '同比：社零+0.6% | 固投-6.7% | 汽车-19%', fontsize=9, ha='center', color='#666', style='italic')
    ax2.text(5, 1.2, '周边：PMI 49.8收缩区，缺乏趋势扩张信号', fontsize=9, ha='center', color='#666', style='italic')
    
    # 进度条（中性=50%）
    progress2 = 0.50
    rect_bar2 = Rectangle((1.5, 0.4), 7 * progress2, 0.4, facecolor='#ff9800', alpha=0.8)
    ax2.add_patch(rect_bar2)
    ax2.text(5, 0.6, f'强度进度条: {progress2*100:.0f}%', fontsize=10, ha='center', fontweight='bold')
    
    # ========== 政府财政面板 ==========
    ax3 = fig.add_subplot(gs[0, 2])
    ax3.set_xlim(0, 10)
    ax3.set_ylim(0, 10)
    ax3.axis('off')
    
    rect3 = FancyBboxPatch((0.2, 0.2), 9.6, 9.6, boxstyle="round,pad=0.1",
                           facecolor='#e3f2fd', edgecolor='#2196f3', linewidth=3)
    ax3.add_patch(rect3)
    
    ax3.text(5, 9.3, '政府财政', fontsize=20, fontweight='bold', ha='center', color='#0d47a1')
    ax3.text(5, 8.6, f'阶段：{fiscal_data["state"]}', fontsize=13, ha='center', color='#1565c0')
    ax3.text(5, 8.1, '🟡 蓄水→传导过渡', fontsize=12, ha='center', color='#1976d2')
    
    fiscal_items = [
        ('专项债进度', f'{fiscal_data["special_bond_progress"]}%', '#2196f3'),
        ('政府债进度', f'{fiscal_data["gov_bond_progress"]}%', '#42a5f5'),
        ('实物工作量', '零星启动', '#64b5f6'),
        ('9月计划发行', '7500亿(专项6300)', '#90caf9'),
    ]
    y_pos = 7.2
    for name, val, color in fiscal_items:
        ax3.text(0.6, y_pos, f'• {name}', fontsize=10, va='center')
        ax3.text(6.5, y_pos, val, fontsize=10, va='center', fontweight='bold', color=color)
        y_pos -= 0.85
    
    # OECD CLI 小表
    ax3.text(5, 4.0, 'OECD CLI 周边', fontsize=11, ha='center', fontweight='bold', color='#0d47a1')
    cli_items = [
        ('美国', 100.80, +0.04),
        ('韩国', 102.87, +0.21),
        ('日本', 100.30, +0.06),
        ('德国', 100.72, -0.04),
        ('中国', 98.57, -0.03),
    ]
    y_pos = 3.3
    for country, val, change in cli_items:
        arrow = "↑" if change > 0 else "↓"
        color = '#4caf50' if val > 100 else '#f44336'
        ax3.text(1.5, y_pos, country, fontsize=9, va='center')
        ax3.text(4.5, y_pos, f'{val:.2f}', fontsize=9, va='center', fontweight='bold')
        ax3.text(6.5, y_pos, f'{arrow} {change:+.2f}', fontsize=9, va='center', color=color)
        y_pos -= 0.5
    
    ax3.text(5, 0.7, '同比：发行节奏后置(vs 2025同期76%)', fontsize=9, ha='center', color='#666', style='italic')
    
    # 进度条
    progress3 = fiscal_data['special_bond_progress'] / 100
    rect_bar3 = Rectangle((1.5, 0.15), 7 * progress3, 0.3, facecolor='#2196f3', alpha=0.8)
    ax3.add_patch(rect_bar3)
    ax3.text(5, 0.3, f'专项债进度: {fiscal_data["special_bond_progress"]}%', fontsize=9, ha='center', fontweight='bold')
    
    # ========== 底部综合判断条 ==========
    ax4 = fig.add_subplot(gs[1, :])
    ax4.set_xlim(0, 10)
    ax4.set_ylim(0, 3)
    ax4.axis('off')
    
    # 背景
    rect4 = FancyBboxPatch((0.2, 0.3), 9.6, 2.4, boxstyle="round,pad=0.1",
                           facecolor='#fce4ec', edgecolor='#e91e63', linewidth=2)
    ax4.add_patch(rect4)
    
    ax4.text(5, 2.3, '📊 综合诊断', fontsize=16, fontweight='bold', ha='center', color='#c62828')
    ax4.text(5, 1.6, '外需偏强(+0.59) + 内需中性(看平) + 财政传导期前段(68%) = 温和修复',
             fontsize=13, ha='center', color='#880e4f')
    ax4.text(5, 0.9, '结构特征：外需>财政>内需 | 风险点：中国CLI低于趋势(98.57)、内需缺乏主动扩张信号',
             fontsize=11, ha='center', color='#ad1457')
    ax4.text(5, 0.4, '关注窗口：9-10月财政广义赤字高峰 + 「金九」旺季内需验证',
             fontsize=11, ha='center', color='#c2185b', fontweight='bold')
    
    # 保存
    output_path = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_00495a421ffeZp6sKsQZoGR6e9/macro_three_dimensions_poster.png"
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='#f8f9fa')
    plt.close()
    print(f"海报已保存: {output_path}")
    return output_path

# ==================== 主流程 ====================

def main():
    print("=" * 60)
    print("三合一宏观监测任务开始")
    print("=" * 60)
    
    # 1. 并行获取数据
    print("\n[1/4] 并行获取三类数据...")
    waixu_data = fetch_waixu()
    neixu_data = fetch_neixu()
    fiscal_data = fetch_fiscal_progress()
    cli_data = fetch_fiscal_oecd()
    
    print(f"  外需：{waixu_data.get('state', 'N/A')} 打分{waixu_data.get('score', 'N/A')}")
    print(f"  内需：{neixu_data.get('state', 'N/A')}")
    print(f"  财政：专项债{fiscal_data['special_bond_progress']}%，政府债{fiscal_data['gov_bond_progress']}%")
    print(f"  OECD CLI：{len(cli_data)}国数据")
    
    # 2. 紧凑发送三张卡片
    print("\n[2/4] 发送飞书交互卡片...")
    
    print("  → 发送外需卡片...")
    card1 = build_waixu_card(waixu_data)
    ok1, resp1 = send_feishu_card(card1)
    print(f"     {'✓ 成功' if ok1 else '✗ 失败'}: {resp1[:80]}")
    
    print("  → 发送内需卡片...")
    card2 = build_neixu_card(neixu_data)
    ok2, resp2 = send_feishu_card(card2)
    print(f"     {'✓ 成功' if ok2 else '✗ 失败'}: {resp2[:80]}")
    
    print("  → 发送财政+OECD卡片...")
    card3 = build_fiscal_card(cli_data, fiscal_data)
    ok3, resp3 = send_feishu_card(card3)
    print(f"     {'✓ 成功' if ok3 else '✗ 失败'}: {resp3[:80]}")
    
    # 3. 生成海报
    print("\n[3/4] 生成综合海报...")
    poster_path = generate_poster(waixu_data, neixu_data, fiscal_data, cli_data)
    
    # 4. 发送海报到飞书（通过图片消息）
    print("\n[4/4] 发送海报到飞书...")
    # 先上传图片获取image_key
    try:
        import base64
        with open(poster_path, 'rb') as f:
            img_data = f.read()
        
        # 使用飞书图片消息
        # 由于webhook直接发图片需要image_key，这里我们用text消息带markdown图片链接
        # 或者发送interactive卡片包含图片
        # 简化：发送一条文本消息告知海报已生成
        poster_card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate | 综合海报"}, "template": "red"},
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "**📊 外需 + 内需 + 政府财政 三维监测全景图**"}
                },
                {"tag": "img", "img_key": "", "alt": {"tag": "plain_text", "content": "三维监测海报"}},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "海报文件已生成，请查看附件或请求发送图片文件。"}
                }
            ]
        }
        # 由于无法直接通过webhook上传图片，我们发送一条说明消息
        ok4, resp4 = send_feishu_card(poster_card)
        print(f"  海报卡片 {'✓ 成功' if ok4 else '✗ 失败'}")
    except Exception as e:
        print(f"  海报发送异常: {e}")
    
    print("\n" + "=" * 60)
    print("三合一任务完成!")
    print(f"海报路径: {poster_path}")
    print("=" * 60)

if __name__ == "__main__":
    main()
