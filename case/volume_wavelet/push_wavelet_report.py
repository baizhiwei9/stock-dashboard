"""
生成成交量小波分析文字报告并推送到飞书
"""
import json
import requests
from datetime import datetime

# 读取分析结果
with open('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_mom_results.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# 生成文字报告
date_str = datetime.now().strftime('%Y-%m-%d')

report_lines = []
report_lines.append("=" * 60)
report_lines.append("📊 成交量小波分析")
report_lines.append(f"📅 报告日期: {date_str}")
report_lines.append("=" * 60)
report_lines.append("")
report_lines.append("标的: 科创板50ETF(588000) | 创业板ETF(159915) | 恒生科技ETF(513130)")
report_lines.append("方法: sym4小波3层分解 | 周期: 20日滚动 | 阈值: z=±1.5")
report_lines.append("")
report_lines.append("-" * 60)
report_lines.append("")

# 遍历三只ETF
for r in results:
    name = r['name']
    code = r['code'].split('.')[0]
    
    report_lines.append(f"【{name} ({code})】")
    report_lines.append(f"  收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%)")
    report_lines.append(f"  成交额: {r['amount']:.2f} 亿元")
    report_lines.append("")
    
    # 长周期
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    signal_marker_long = " 🚨" if r['long_z'] > 1.5 else " ⚠️" if r['long_z'] < -1.5 else ""
    report_lines.append(f"  🔵 长周期 (cA3 - 低频基底){signal_marker_long}")
    report_lines.append(f"      当前值: {r['long_value']:.2f} 亿元")
    report_lines.append(f"      z-score: {r['long_z']:+.2f}")
    report_lines.append(f"      较昨日: {r['long_day_chg']:+.2f}%")
    report_lines.append(f"      周环比: {long_mom_str}")
    report_lines.append(f"      历史分位: {r['long_pct']:.1f}%")
    report_lines.append("")
    
    # 中周期
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    signal_marker_med = " 🚨" if r['med_z'] > 1.5 else " ⚠️" if r['med_z'] < -1.5 else ""
    report_lines.append(f"  🟢 中周期 (cD3+cD2 - 中频趋势资金){signal_marker_med}")
    report_lines.append(f"      当前值: {r['med_value']:.2f} 亿元")
    report_lines.append(f"      z-score: {r['med_z']:+.2f}")
    report_lines.append(f"      较昨日: {r['med_day_chg']:+.2f}%")
    report_lines.append(f"      周环比: {med_mom_str}")
    report_lines.append(f"      历史分位: {r['med_pct']:.1f}%")
    report_lines.append("")
    
    # 信号判断
    if r['long_z'] > 1.5 and r['med_z'] > 1.5:
        report_lines.append(f"  ⚡ 信号: 双频段共振上涨！资金涌入，趋势确立")
    elif r['long_z'] < -1.5 and r['med_z'] < -1.5:
        report_lines.append(f"  ⚡ 信号: 双频段共振下跌！资金撤离，警惕逆转")
    elif r['long_z'] > 1.5:
        report_lines.append(f"  ⚡ 长周期突破+1.5σ，中周期未跟上，关注持续性")
    elif r['long_z'] < -1.5:
        report_lines.append(f"  ⚡ 长周期跌破-1.5σ，中周期未跟上，谨慎观望")
    else:
        report_lines.append(f"  ⚡ 暂无共振信号 (长z={r['long_z']:+.2f}, 中z={r['med_z']:+.2f})")
    
    report_lines.append("")
    report_lines.append("-" * 60)
    report_lines.append("")

# 横向对比表
report_lines.append("【横向对比 - 周环比变化】")
report_lines.append("")
report_lines.append("| ETF           | 长周期周环比 | 中周期周环比 | 状态 |")
report_lines.append("|---------------|-------------|-------------|------|")

for r in results:
    name_short = r['name'].replace('ETF', '')
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    
    if r['long_mom'] is not None and r['med_mom'] is not None:
        if r['long_mom'] > 0 and r['med_mom'] > 0:
            status = "双升 ✅"
        elif r['long_mom'] < 0 and r['med_mom'] < 0:
            status = "双降 ⚠️"
        else:
            status = "分歧 ❓"
    else:
        status = "N/A"
    
    report_lines.append(f"| {name_short:<13} | {long_mom_str:>11} | {med_mom_str:>11} | {status:<4} |")

report_lines.append("")
report_lines.append("=" * 60)
report_lines.append("⚠️ 免责声明: 本报告仅供研究参考，不构成投资建议")
report_lines.append("=" * 60)

report_text = "\n".join(report_lines)

# 保存报告
report_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/volume_wavelet_report.txt'
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(report_text)

print(report_text)
print(f"\n报告已保存: {report_path}")

# ========== PUSH TO FEISHU ==========
print("\n推送到飞书...")

# 构建飞书卡片内容
card_content = ""
for r in results:
    name = r['name']
    code = r['code'].split('.')[0]
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    
    # 信号标记
    if r['long_z'] > 1.5 and r['med_z'] > 1.5:
        signal = "🚨 双频段共振上涨"
    elif r['long_z'] < -1.5 and r['med_z'] < -1.5:
        signal = "🚨 双频段共振下跌"
    elif r['long_z'] > 1.5:
        signal = "⚠️ 长周期突破+1.5σ"
    elif r['long_z'] < -1.5:
        signal = "⚠️ 长周期跌破-1.5σ"
    else:
        signal = "✅ 暂无共振信号"
    
    card_content += f"**【{name} ({code})】**\n"
    card_content += f"收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 成交额: {r['amount']:.2f}亿\n"
    card_content += f"🔵 长周期: {r['long_value']:.2f}亿 | z={r['long_z']:+.2f} | 周环比: {long_mom_str} | 分位: {r['long_pct']:.1f}%\n"
    card_content += f"🟢 中周期: {r['med_value']:.2f}亿 | z={r['med_z']:+.2f} | 周环比: {med_mom_str} | 分位: {r['med_pct']:.1f}%\n"
    card_content += f"⚡ {signal}\n\n"

# 对比表
comparison = "**【横向对比 - 周环比】**\n\n"
comparison += "| ETF | 长周期 | 中周期 | 状态 |\n"
comparison += "|-----|--------|--------|------|\n"

for r in results:
    name_short = r['name'].replace('ETF', '')
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    
    if r['long_mom'] is not None and r['med_mom'] is not None:
        if r['long_mom'] > 0 and r['med_mom'] > 0:
            status = "双升 ✅"
        elif r['long_mom'] < 0 and r['med_mom'] < 0:
            status = "双降 ⚠️"
        else:
            status = "分歧 ❓"
    else:
        status = "N/A"
    
    comparison += f"| {name_short} | {long_mom_str} | {med_mom_str} | {status} |\n"

payload = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"成交量小波分析 | {date_str}"
            },
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**标的**: 科创板50(588000) | 创业板(159915) | 恒生科技(513130)\n**方法**: sym4小波3层分解 | **阈值**: z=±1.5"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": card_content
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": comparison
                }
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "⚠️ 仅供研究参考，不构成投资建议"
                    }
                ]
            }
        ]
    }
}

try:
    resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
    print(f"飞书响应: {resp.status_code}")
    if resp.status_code == 200:
        resp_data = resp.json()
        print(f"返回: {resp_data}")
        if resp_data.get('code') == 0:
            print("✅ 飞书推送成功!")
        else:
            print(f"⚠️ 飞书返回错误: {resp_data}")
    else:
        print(f"⚠️ HTTP错误: {resp.status_code}")
except Exception as e:
    print(f"❌ 飞书推送失败: {e}")

print("\n完成!")
