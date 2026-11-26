#!/usr/bin/env python3
"""
TDA流形旋转策略 - 飞书报告生成与推送 (股债组合版)
==================================================
飞书交互卡片结构:
1. 报告头部: "报告来自于 DoMate" + 日期
2. 股票策略: 市场状态/脆弱性/仓位 + 因子权重表 + 推荐个股
3. 债券策略: 宏观状态/TDA震荡/多空方向/止损位
4. 组合收益: 股票端/债券端/组合累计/年化/回撤
"""

import os
import sys
import json
import time
import requests
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feishu_config import WEBHOOK_URL

# ========== 飞书卡片构建工具 ==========

def make_header(title, template='blue'):
    return {
        'title': {'tag': 'plain_text', 'content': title},
        'template': template,
    }

def make_text_element(content, tag='lark_md'):
    return {
        'tag': 'div',
        'text': {'tag': tag, 'content': content},
    }

def make_column_set(columns):
    elements = []
    for col in columns:
        content = col[0]
        width_type = col[1] if len(col) > 1 else 'weighted'
        width_weight = col[2] if len(col) > 2 else 1
        elements.append({
            'tag': 'column',
            'width': width_type,
            'weight': width_weight,
            'elements': [make_text_element(content)],
        })
    return {
        'tag': 'column_set',
        'flex_mode': 'bisect',
        'background_style': 'default',
        'columns': elements,
    }

def make_divider():
    return {'tag': 'hr'}

def make_note(content):
    return {
        'tag': 'note',
        'elements': [{'tag': 'plain_text', 'content': content}],
    }

# ========== 每日报告卡片 ==========

def generate_daily_report_card(signal_data, bond_data=None, pnl_data=None, trade_date=None):
    """
    生成每日报告飞书卡片 (股债组合版)
    
    参数:
        signal_data: 股票端信号 (市场状态/因子权重/推荐个股/拓扑特征)
        bond_data: 债券端信号 (宏观状态/TDA震荡/多空方向/止损位)
        pnl_data: 收益跟踪数据
        trade_date: 交易日期
    """
    if trade_date is None:
        trade_date = datetime.date.today().strftime('%Y%m%d')
    
    date_display = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}" if len(trade_date) == 8 else trade_date
    elements = []
    
    # ===== 报告头部 =====
    elements.append(make_text_element(
        f"**报告来自于 DoMate**  |  TDA流形旋转策略(股债50/50)  |  {date_display}"
    ))
    elements.append(make_divider())
    
    # ===== 第一部分: 股票端 - 市场状态 =====
    market = signal_data.get('market', {})
    features = signal_data.get('features', {})
    
    elements.append(make_text_element("**一、股票端 - 市场状态**"))
    
    state = market.get('state', 'N/A')
    vuln = market.get('vulnerability', 0)
    pos_pct = market.get('position_pct', 0)
    beta_0 = features.get('beta_0', 0)
    beta_1 = features.get('beta_1', 0)
    curvature = features.get('curvature', 0)
    
    elements.append(make_column_set([
        (f"市场状态: **{state}**", 'weighted', 1),
        (f"脆弱性: **{vuln:.1f}%**", 'weighted', 1),
        (f"建议仓位: **{pos_pct:.0f}%**", 'weighted', 1),
    ]))
    
    elements.append(make_column_set([
        (f"β₀(连通分量): {beta_0}", 'weighted', 1),
        (f"β₁(结构洞): {beta_1}", 'weighted', 1),
        (f"曲率: {curvature:.3f}", 'weighted', 1),
    ]))
    
    elements.append(make_divider())
    
    # ===== 第二部分: 股票端 - 因子配置与推荐个股 =====
    weights = signal_data.get('weights', {})
    stocks = signal_data.get('stocks', {})
    
    elements.append(make_text_element("**二、因子配置与推荐个股**"))
    
    elements.append(make_column_set([
        ("**因子**", 'weighted', 1),
        ("**权重**", 'weighted', 1),
        ("**推荐个股(Top3)**", 'weighted', 2),
    ]))
    
    for factor, weight in weights.items():
        stock_list = stocks.get(factor, [])
        if stock_list:
            stock_str = "  ".join([f"{s['name']}({s['code'][:6]})" for s in stock_list])
        else:
            stock_str = "--"
        
        elements.append(make_column_set([
            (factor, 'weighted', 1),
            (f"{weight*100:.1f}%", 'weighted', 1),
            (stock_str, 'weighted', 2),
        ]))
    
    elements.append(make_divider())
    
    # ===== 第三部分: 债券端 =====
    if bond_data:
        elements.append(make_text_element("**三、债券端 - 国债期货T0**"))
        
        macro_state = bond_data.get('macro', {}).get('macro_state', 'N/A')
        bond_impl = bond_data.get('macro', {}).get('bond_implication', 'N/A')
        tda_score = bond_data.get('tda_score', 0)
        direction_str = bond_data.get('direction_str', 'N/A')
        bond_price = bond_data.get('bond_price', 0)
        stop_price = bond_data.get('stop_price', 0)
        stop_pct = bond_data.get('stop_pct', 0)
        yield_10y = bond_data.get('yield_10y', 0)
        yield_1y = bond_data.get('yield_1y', 0)
        yield_spread = bond_data.get('yield_spread', 0)
        
        elements.append(make_column_set([
            (f"宏观状态: **{macro_state}**", 'weighted', 1),
            (f"债券含义: **{bond_impl}**", 'weighted', 1),
            (f"TDA震荡: **{tda_score:.2f}**", 'weighted', 1),
        ]))
        
        elements.append(make_column_set([
            (f"方向: **{direction_str}**", 'weighted', 1),
            (f"当前价: {bond_price}", 'weighted', 1),
            (f"止损位: {stop_price} ({stop_pct}%)", 'weighted', 1),
        ]))
        
        elements.append(make_column_set([
            (f"10Y国债: {yield_10y}%", 'weighted', 1),
            (f"1Y国债: {yield_1y}%", 'weighted', 1),
            (f"10Y-1Y利差: {yield_spread}%", 'weighted', 1),
        ]))
        
        elements.append(make_divider())
    
    # ===== 第四部分: 组合收益 =====
    if pnl_data:
        elements.append(make_text_element("**四、实盘跟踪收益(股债50/50)**"))
        
        elements.append(make_column_set([
            (f"组合累计: **{pnl_data.get('cumulative_return', 0):.2f}%**", 'weighted', 1),
            (f"年化收益: **{pnl_data.get('annualized_return', 0):.2f}%**", 'weighted', 1),
            (f"最大回撤: **{pnl_data.get('max_drawdown', 0):.2f}%**", 'weighted', 1),
        ]))
        
        elements.append(make_column_set([
            (f"今日组合: **{pnl_data.get('today_return', 0):.2f}%**", 'weighted', 1),
            (f"股票端: {pnl_data.get('stock_return', 0):.2f}%", 'weighted', 1),
            (f"债券端: {pnl_data.get('bond_return', 0):.2f}%", 'weighted', 1),
        ]))
        
        elements.append(make_column_set([
            (f"沪深300: {pnl_data.get('benchmark_return', 0):.2f}%", 'weighted', 1),
            (f"夏普比: {pnl_data.get('sharpe', 0):.3f}", 'weighted', 1),
            (f"胜率: {pnl_data.get('win_rate', 0):.1f}%", 'weighted', 1),
        ]))
        
        elements.append(make_divider())
    
    # ===== 底部备注 =====
    elements.append(make_note(
        f"TDA流形旋转策略 v3.3(防御取向) | 股债50/50组合 | 信号T日收盘生成,T+1日生效 | 仅供参考,不构成投资建议"
    ))
    
    card = {
        'config': {'wide_screen_mode': True},
        'header': make_header(
            f"TDA流形旋转策略日报 | {date_display}",
            'blue' if market.get('state') != '弱势下跌' else 'red'
        ),
        'elements': elements,
    }
    
    return card


def generate_backtest_report_card(summary):
    """生成回测报告飞书卡片"""
    elements = []
    
    elements.append(make_text_element(
        f"**报告来自于 DoMate**  |  TDA流形旋转策略 - 股债组合历史回测报告"
    ))
    elements.append(make_divider())
    
    elements.append(make_text_element("**回测结果摘要**"))
    
    elements.append(make_column_set([
        (f"回测区间: {summary.get('backtest_period', 'N/A')}", 'weighted', 1),
        (f"交易日数: {summary.get('n_trading_days', 0)}", 'weighted', 1),
    ]))
    
    elements.append(make_column_set([
        (f"组合总收益: **{summary.get('total_return', 0):.2f}%**", 'weighted', 1),
        (f"年化收益: **{summary.get('annualized_return', 0):.2f}%**", 'weighted', 1),
        (f"最大回撤: **{summary.get('max_drawdown', 0):.2f}%**", 'weighted', 1),
    ]))
    
    elements.append(make_column_set([
        (f"夏普比: **{summary.get('sharpe_ratio', 0):.3f}**", 'weighted', 1),
        (f"胜率: **{summary.get('win_rate', 0):.1f}%**", 'weighted', 1),
        (f"平均仓位: **{summary.get('avg_position', 0):.1f}%**", 'weighted', 1),
    ]))
    
    elements.append(make_column_set([
        (f"股票端收益: **{summary.get('stock_total_return', 0):.2f}%**", 'weighted', 1),
        (f"债券端收益: **{summary.get('bond_total_return', 0):.2f}%**", 'weighted', 1),
        (f"沪深300: {summary.get('benchmark_total_return', 0):.2f}%", 'weighted', 1),
    ]))
    
    elements.append(make_divider())
    
    # 市场状态分布
    state_dist = summary.get('state_distribution', {})
    if state_dist:
        elements.append(make_text_element("**市场状态分布**"))
        state_lines = []
        for state, count in state_dist.items():
            pct = count / summary['n_trading_days'] * 100
            state_lines.append(f"  {state}: {count}天 ({pct:.1f}%)")
        elements.append(make_text_element("\n".join(state_lines)))
    
    # 债券方向分布
    bond_dist = summary.get('bond_direction_distribution', {})
    if bond_dist:
        elements.append(make_text_element("**债券方向分布**"))
        bond_lines = []
        for d, count in bond_dist.items():
            pct = count / summary['n_trading_days'] * 100
            bond_lines.append(f"  {d}: {count}天 ({pct:.1f}%)")
        elements.append(make_text_element("\n".join(bond_lines)))
    
    elements.append(make_note(
        "回测使用ETF+国债期货真实数据,未考虑交易成本 | 仅供参考,不构成投资建议"
    ))
    
    card = {
        'config': {'wide_screen_mode': True},
        'header': make_header("TDA流形旋转策略 - 回测报告", 'green'),
        'elements': elements,
    }
    
    return card


# ========== 飞书消息发送 ==========

def send_feishu_card(card, webhook_url=None):
    if webhook_url is None:
        webhook_url = WEBHOOK_URL
    
    payload = {'msg_type': 'interactive', 'card': card}
    
    for attempt in range(3):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=30)
            result = resp.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                print("飞书卡片发送成功")
                return True
            else:
                print(f"飞书返回错误: {result}")
                if attempt == 2:
                    return send_feishu_text(card, webhook_url)
        except Exception as e:
            print(f"发送飞书卡片失败(尝试{attempt+1}): {e}")
            time.sleep(2)
    return False


def send_feishu_text(card, webhook_url=None):
    if webhook_url is None:
        webhook_url = WEBHOOK_URL
    
    texts = []
    for elem in card.get('elements', []):
        if elem.get('tag') == 'div' and 'text' in elem:
            texts.append(elem['text'].get('content', ''))
    
    content = '\n'.join(texts)
    title = card.get('header', {}).get('title', {}).get('content', 'TDA策略报告')
    
    payload = {
        'msg_type': 'text',
        'content': {'text': f"【{title}】\n\n{content}"}
    }
    
    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        print("飞书纯文本消息发送成功(降级模式)")
        return True
    except Exception as e:
        print(f"飞书纯文本发送也失败: {e}")
        return False


def send_backtest_summary(summary, webhook_url=None):
    card = generate_backtest_report_card(summary)
    return send_feishu_card(card, webhook_url)
