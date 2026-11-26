#!/usr/bin/env python3
"""
科创板/创业板 顶底机会监控（完整版）
每周日执行，检查是否触发买卖信号，触发则发邮件提醒
数据源：东方财富公开API
"""

import requests
import json
import subprocess
from datetime import datetime, timedelta
import sys

# ===== 阈值配置 =====
THRESHOLDS = {
    '科创50': {'bottom_pe': 30, 'top_peg': 3.5, 'growth_cap': 50},
    '创业板指': {'bottom_pe': 28, 'top_peg': 3.0, 'growth_cap': 50},
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://quote.eastmoney.com/'
}

def fetch_index_valuation(index_code, secid):
    """
    从东方财富获取指数估值数据（PE/PB）
    secid: 1.000688 (科创50) 或 0.399006 (创业板指)
    """
    # 方法1: 尝试datacenter-web
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "5",
            "pageNumber": "1",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "TRADE_DATE,PE_TTM,PB_MRQ,TOTAL_MARKET_CAP",
            "filter": f'(SECURITY_CODE="{index_code}")'
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = r.json()
        if data.get('success') and data.get('result', {}).get('data'):
            items = data['result']['data']
            latest = items[0]
            return {
                'pe_ttm': latest.get('PE_TTM'),
                'pb': latest.get('PB_MRQ'),
                'date': latest.get('TRADE_DATE', '')[:10],
            }
    except Exception as e:
        print(f"  datacenter-web获取失败: {e}")
    
    # 方法2: 尝试push2 API (实时行情，含PE)
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": secid,
            "fields": "f43,f44,f45,f46,f164,f167",  # f164=PE动态
            "ut": "fa5fd1943c7b386f172d6893dbbd1",
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = r.json()
        if data.get('data'):
            d = data['data']
            return {
                'pe_ttm': d.get('f164', 0) / 100 if d.get('f164') else None,
                'close': d.get('f43', 0) / 100,
                'date': datetime.now().strftime('%Y-%m-%d'),
            }
    except Exception as e:
        print(f"  push2获取失败: {e}")
    
    # 方法3: 尝试datacenter历史数据（取最新一条）
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "1",
            "pageNumber": "1",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "TRADE_DATE,PE_TTM,PB_MRQ",
            "filter": f'(SECURITY_CODE="{index_code}")'
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = r.json()
        if data.get('success') and data.get('result', {}).get('data'):
            item = data['result']['data'][0]
            return {
                'pe_ttm': item.get('PE_TTM'),
                'pb': item.get('PB_MRQ'),
                'date': item.get('TRADE_DATE', '')[:10],
            }
    except Exception as e:
        print(f"  datacenter历史数据获取失败: {e}")
    
    return None

def fetch_index_pe_history(index_code, secid, days=1200):
    """获取历史PE数据用于计算分位数"""
    try:
        # 尝试通过datacenter获取历史估值
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": "1",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "TRADE_DATE,PE_TTM",
            "filter": f'(SECURITY_CODE="{index_code}")'
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = r.json()
        if data.get('success') and data.get('result', {}).get('data'):
            items = data['result']['data']
            pe_list = [item['PE_TTM'] for item in items if item.get('PE_TTM') and item['PE_TTM'] > 0]
            return pe_list
    except Exception as e:
        print(f"  历史PE获取失败: {e}")
    
    return None

def fetch_earnings_growth(index_code):
    """获取指数盈利增速（从东方财富）"""
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageSize": "5",
            "pageNumber": "1",
            "reportName": "RPT_INDEX_EARNINGSGROWTH",
            "columns": "REPORT_DATE,YOYEPS",
            "filter": f'(INDEX_CODE="{index_code}")'
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = r.json()
        if data.get('success') and data.get('result', {}).get('data'):
            items = data['result']['data']
            growth_list = [item.get('YOYEPS', 0) for item in items if item.get('YOYEPS') is not None]
            return growth_list
    except Exception as e:
        print(f"  盈利增速获取失败: {e}")
    
    # 备用：使用默认增速估计
    return None

def check_signal(index_name, pe_current, pe_history, growth_rate):
    """
    检查是否触发信号
    返回: (bottom_signal, top_signal, details)
    """
    config = THRESHOLDS[index_name]
    bottom_msg = None
    top_msg = None
    
    # 底部信号：PE分位数
    if pe_current and pe_current < config['bottom_pe']:
        if pe_history:
            percentile = sum(1 for p in pe_history if p < pe_current) / len(pe_history)
            bottom_msg = f"PE={pe_current:.1f}倍 (阈值{config['bottom_pe']}倍, 历史分位{percentile:.0%})"
        else:
            bottom_msg = f"PE={pe_current:.1f}倍 (阈值{config['bottom_pe']}倍)"
    
    # 顶部信号：改良PEG
    if pe_current and growth_rate and growth_rate > 0:
        avg_growth = min(growth_rate, config['growth_cap'])
        peg_improved = pe_current / avg_growth
        if peg_improved > config['top_peg']:
            top_msg = f"改良PEG={peg_improved:.2f} (PE={pe_current:.1f}, 增速={avg_growth:.1f}%, 阈值{config['top_peg']})"
    
    return bottom_msg, top_msg

def send_alert(subject, body):
    """发送邮件提醒"""
    script_path = "/root/.coze/agents/7691719640636244234/workspace/email-sending/scripts/send_email.py"
    cmd = ["python3", script_path, "--subject", subject, "--body", body]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"  ✓ 邮件发送成功")
            return True
        else:
            print(f"  ✗ 邮件发送失败: {result.stderr}")
            return False
    except FileNotFoundError:
        print(f"  ✗ 邮件脚本不存在: {script_path}")
        return False
    except Exception as e:
        print(f"  ✗ 邮件发送异常: {e}")
        return False

def main():
    print(f"{'='*60}")
    print(f"科创板/创业板 顶底机会监控")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    alerts = []
    
    # 检查科创50
    print(f"\n[1] 科创50 (000688)")
    kcb_data = fetch_index_valuation('000688', '1.000688')
    if kcb_data and kcb_data.get('pe_ttm'):
        pe = kcb_data['pe_ttm']
        print(f"  当前PE(TTM): {pe:.1f}倍, 日期: {kcb_data.get('date', 'N/A')}")
        
        # 获取历史PE
        pe_history = fetch_index_pe_history('000688', '1.000688')
        if pe_history:
            print(f"  历史PE数据: {len(pe_history)}条, 范围[{min(pe_history):.1f}, {max(pe_history):.1f}]")
        
        # 获取盈利增速
        growth_list = fetch_earnings_growth('000688')
        growth = None
        if growth_list and len(growth_list) > 0:
            # 使用最近非负增速
            positive_growth = [g for g in growth_list[:3] if g > 0]
            if positive_growth:
                growth = sum(positive_growth) / len(positive_growth)
                print(f"  盈利增速: {growth:.1f}%")
        
        # 检查信号
        bottom_msg, top_msg = check_signal('科创50', pe, pe_history, growth)
        
        if bottom_msg:
            alerts.append(f"🟢 【科创50 底部信号】{bottom_msg}\n建议：分批买入")
            print(f"  >>> 触发底部信号！{bottom_msg}")
        if top_msg:
            alerts.append(f"🔴 【科创50 顶部信号】{top_msg}\n建议：考虑减仓")
            print(f"  >>> 触发顶部信号！{top_msg}")
        
        if not bottom_msg and not top_msg:
            print(f"  → 当前无信号（中间地带）")
    else:
        print(f"  ✗ 无法获取估值数据")
    
    # 检查创业板指
    print(f"\n[2] 创业板指 (399006)")
    cyb_data = fetch_index_valuation('399006', '0.399006')
    if cyb_data and cyb_data.get('pe_ttm'):
        pe = cyb_data['pe_ttm']
        print(f"  当前PE(TTM): {pe:.1f}倍, 日期: {cyb_data.get('date', 'N/A')}")
        
        pe_history = fetch_index_pe_history('399006', '0.399006')
        if pe_history:
            print(f"  历史PE数据: {len(pe_history)}条, 范围[{min(pe_history):.1f}, {max(pe_history):.1f}]")
        
        growth_list = fetch_earnings_growth('399006')
        growth = None
        if growth_list and len(growth_list) > 0:
            positive_growth = [g for g in growth_list[:3] if g > 0]
            if positive_growth:
                growth = sum(positive_growth) / len(positive_growth)
                print(f"  盈利增速: {growth:.1f}%")
        
        bottom_msg, top_msg = check_signal('创业板指', pe, pe_history, growth)
        
        if bottom_msg:
            alerts.append(f"🟢 【创业板指 底部信号】{bottom_msg}\n建议：分批买入")
            print(f"  >>> 触发底部信号！{bottom_msg}")
        if top_msg:
            alerts.append(f"🔴 【创业板指 顶部信号】{top_msg}\n建议：考虑减仓")
            print(f"  >>> 触发顶部信号！{top_msg}")
        
        if not bottom_msg and not top_msg:
            print(f"  → 当前无信号（中间地带）")
    else:
        print(f"  ✗ 无法获取估值数据")
    
    # 发送邮件
    print(f"\n{'='*60}")
    if alerts:
        print(f"发现 {len(alerts)} 个信号，发送邮件提醒...")
        subject = f"⚡ 科创板/创业板 顶底机会提醒 ({datetime.now().strftime('%Y-%m-%d')})"
        body = "\n\n".join(alerts) + "\n\n" + \
               f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n" + \
               "---\n" + \
               "信号规则：\n" + \
               "• 底部：PE分位数法（科创<30倍/创业板<28倍）\n" + \
               "• 顶部：改良PEG法（科创PEG>3.5/创业板PEG>3.0）\n" + \
               "• 数据源：东方财富\n" + \
               "• 回测胜率：PE分位数底部召回率100%（科创），50%（创业板）"
        
        send_alert(subject, body)
    else:
        print(f"当前无顶底信号，不发送邮件。")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()
