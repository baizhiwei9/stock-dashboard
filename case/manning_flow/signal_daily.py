#!/usr/bin/env python3
"""
V3 实时信号生成器
==================
每日/每周运行, 输出当前各行业的资金流信号状态:
  - 机会信号: CMF上穿0, 资金从流出转流入 -> 建仓
  - 风险信号: CMF下穿0, 资金从流入转流出 -> 清仓
  - 预警信号: 短期资金流出但长期趋势未破 -> 减仓
  - 持有信号: 资金持续流入 -> 继续持有
  - 观望: 无明显信号

用法: python3 signal_daily.py
输出: 控制台打印 + 飞书推送 + JSON保存
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
import json
import os
import requests

warnings.filterwarnings('ignore')

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
from feishu_config import WEBHOOK_URL  # 凭据见 config/secrets.json
INDUSTRY_ETF_MAP = {
    "半导体": ("512480", "半导体ETF"),
    "银行": ("512800", "银行ETF"),
    "证券": ("512000", "券商ETF"),
    "光伏设备": ("515790", "光伏ETF"),
    "军工装备": ("512660", "军工ETF"),
    "通信设备": ("515880", "通信ETF"),
    "计算机设备": ("512720", "计算机ETF"),
    "软件开发": ("515230", "软件ETF"),
    "化学制药": ("512010", "医药ETF"),
    "煤炭开采加工": ("515220", "煤炭ETF"),
    "钢铁": ("515210", "钢铁ETF"),
    "工业金属": ("512400", "有色ETF"),
    "房地产": ("512200", "房地产ETF"),
    "汽车整车": ("516110", "汽车ETF"),
    "白酒": ("512690", "酒ETF"),
    "食品加工制造": ("515170", "食品饮料ETF"),
    "生物制品": ("159992", "创新药ETF"),
    "医疗器械": ("159883", "医疗器械ETF"),
    "化学制品": ("159870", "化工ETF"),
    "风电设备": ("516950", "风电ETF"),
    "电力": ("159611", "电力ETF"),
    "消费电子": ("159732", "消费电子ETF"),
    "电池": ("159755", "电池ETF"),
    "贵金属": ("159933", "金ETF"),
    "建筑材料": ("159745", "建材ETF"),
    "物流": ("516910", "物流ETF"),
    "养殖业": ("159865", "养殖ETF"),
    "通信服务": ("515050", "5GETF"),
    "IT服务": ("515080", "AI智能ETF"),
}

CMF_SHORT = 10
CMF_LONG = 20
LOOKBACK_DAYS = 90  # 拉取最近90个交易日


def get_industry_data(name):
    """获取行业指数历史数据"""
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - pd.Timedelta(days=LOOKBACK_DAYS*2)).strftime("%Y%m%d")
    df = ak.stock_board_industry_index_ths(symbol=name, start_date=start, end_date=today)
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期').reset_index(drop=True)
    df.rename(columns={'日期':'date','开盘价':'open','最高价':'high','最低价':'low',
                        '收盘价':'close','成交量':'volume','成交额':'amount'}, inplace=True)
    df['ret'] = df['close'].pct_change()
    return df[['date','open','high','low','close','volume','amount','ret']]


def compute_cmf_for_industry(df):
    """计算单个行业的CMF"""
    high = df['high']
    low = df['low']
    close = df['close']
    amount = df['amount']
    
    # CLV
    rv = high - low
    clv = pd.Series(0.0, index=close.index)
    valid = rv > 0
    clv[valid] = (2*close[valid] - high[valid] - low[valid]) / rv[valid]
    
    # 资金流
    money_flow = clv * amount
    
    # CMF短期和长期
    cmf_short = money_flow.rolling(window=CMF_SHORT, min_periods=CMF_SHORT-2).sum() / \
                amount.rolling(window=CMF_SHORT, min_periods=CMF_SHORT-2).sum()
    cmf_long = money_flow.rolling(window=CMF_LONG, min_periods=CMF_LONG-2).sum() / \
               amount.rolling(window=CMF_LONG, min_periods=CMF_LONG-2).sum()
    
    return cmf_short, cmf_long, clv, money_flow


def classify_signal(cmf_s_now, cmf_s_prev, cmf_l_now, cmf_l_prev):
    """
    信号分类
    返回: (信号类型, 信号描述, 建议操作)
    """
    # 机会: CMF短线上穿0
    if cmf_s_prev < 0 and cmf_s_now >= 0:
        if cmf_l_now > 0:
            return ('机会', 'CMF短线上穿0且长线为正, 资金流入趋势确认', '建仓')
        else:
            return ('机会', 'CMF短线上穿0, 资金从流出转流入', '建仓(轻仓)')
    
    # 风险: CMF短线下穿0
    if cmf_s_prev > 0 and cmf_s_now <= 0:
        if cmf_l_now < 0:
            return ('风险', 'CMF短线下穿0且长线为负, 资金流出趋势确认', '清仓')
        else:
            return ('风险', 'CMF短线下穿0, 资金从流入转流出', '清仓')
    
    # 持有: CMF短线>0且长线>0
    if cmf_s_now > 0 and cmf_l_now > 0:
        # 判断是否在加速
        if cmf_s_now > cmf_s_prev:
            return ('持有', 'CMF为正且加速上升, 资金流入增强', '持有/加仓')
        else:
            return ('持有', 'CMF为正但增速放缓, 资金流入减速', '持有')
    
    # 预警: CMF短线<0但长线>0
    if cmf_s_now < 0 and cmf_l_now > 0:
        return ('预警', '短期资金流出但长期趋势未破', '减仓观察')
    
    # 持续风险: CMF短线<0且长线<0
    if cmf_s_now < 0 and cmf_l_now < 0:
        return ('空仓', 'CMF持续为负, 资金持续流出', '空仓等待')
    
    return ('观望', '无明显信号', '观望')


def main():
    print("=" * 70)
    print("V3 行业资金流向信号 - 实时生成")
    print("=" * 70)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"CMF短期={CMF_SHORT}日, CMF长期={CMF_LONG}日")
    print()
    
    # Step 1: 获取数据并计算信号
    results = []
    errors = []
    
    for i, (name, (etf_code, etf_name)) in enumerate(INDUSTRY_ETF_MAP.items()):
        print(f"  [{i+1}/{len(INDUSTRY_ETF_MAP)}] {name}({etf_name})...", end=" ")
        try:
            df = get_industry_data(name)
            if len(df) < CMF_LONG + 2:
                print(f"数据不足({len(df)}天)")
                errors.append(name)
                continue
            
            cmf_s, cmf_l, clv, mf = compute_cmf_for_industry(df)
            
            # 最新两个交易日的CMF
            latest_idx = cmf_s.last_valid_index()
            if latest_idx is None or latest_idx < 1:
                print("CMF计算失败")
                errors.append(name)
                continue
            
            cmf_s_now = cmf_s.iloc[-1]
            cmf_s_prev = cmf_s.iloc[-2]
            cmf_l_now = cmf_l.iloc[-1]
            cmf_l_prev = cmf_l.iloc[-2]
            
            latest_date = df['date'].iloc[-1]
            latest_close = df['close'].iloc[-1]
            latest_ret = df['ret'].iloc[-1] * 100
            
            # 5日涨跌幅
            if len(df) >= 6:
                ret_5d = (df['close'].iloc[-1] / df['close'].iloc[-6] - 1) * 100
            else:
                ret_5d = 0
            
            # 信号分类
            sig_type, sig_desc, sig_action = classify_signal(
                cmf_s_now, cmf_s_prev, cmf_l_now, cmf_l_prev
            )
            
            # 信号强度 (综合评分)
            strength = cmf_s_now * 0.4 + cmf_l_now * 0.3 + latest_ret/100 * 0.1
            
            results.append({
                'industry': name,
                'etf_code': etf_code,
                'etf_name': etf_name,
                'date': latest_date.strftime('%Y-%m-%d'),
                'close': latest_close,
                'ret_1d': latest_ret,
                'ret_5d': ret_5d,
                'cmf_short': cmf_s_now,
                'cmf_long': cmf_l_now,
                'cmf_short_prev': cmf_s_prev,
                'cmf_long_prev': cmf_l_prev,
                'signal': sig_type,
                'signal_desc': sig_desc,
                'action': sig_action,
                'strength': strength,
            })
            
            print(f"{sig_type} ({sig_action})")
            
        except Exception as e:
            print(f"失败: {str(e)[:50]}")
            errors.append(name)
    
    print(f"\n成功: {len(results)}, 失败: {len(errors)}")
    
    if not results:
        print("无有效数据, 退出")
        return
    
    # Step 2: 汇总信号
    print("\n" + "=" * 70)
    print("信号汇总")
    print("=" * 70)
    
    # 按信号类型分组
    opp_list = [r for r in results if r['signal'] == '机会']
    risk_list = [r for r in results if r['signal'] == '风险']
    hold_list = [r for r in results if r['signal'] == '持有']
    warn_list = [r for r in results if r['signal'] == '预警']
    empty_list = [r for r in results if r['signal'] == '空仓']
    wait_list = [r for r in results if r['signal'] == '观望']
    
    print(f"\n  机会信号 ({len(opp_list)}个):")
    for r in sorted(opp_list, key=lambda x: x['strength'], reverse=True):
        print(f"    {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} CMF长={r['cmf_long']:.4f} -> {r['action']}")
    
    print(f"\n  风险信号 ({len(risk_list)}个):")
    for r in sorted(risk_list, key=lambda x: x['strength']):
        print(f"    {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} CMF长={r['cmf_long']:.4f} -> {r['action']}")
    
    print(f"\n  持有信号 ({len(hold_list)}个):")
    for r in sorted(hold_list, key=lambda x: x['strength'], reverse=True):
        print(f"    {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} -> {r['action']}")
    
    print(f"\n  预警信号 ({len(warn_list)}个):")
    for r in warn_list:
        print(f"    {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} CMF长={r['cmf_long']:.4f} -> {r['action']}")
    
    print(f"\n  空仓等待 ({len(empty_list)}个):")
    for r in empty_list:
        print(f"    {r['industry']}({r['etf_name']})")
    
    if wait_list:
        print(f"\n  观望 ({len(wait_list)}个):")
        for r in wait_list:
            print(f"    {r['industry']}({r['etf_name']})")
    
    # Step 3: 建议持仓组合
    print("\n" + "=" * 70)
    print("建议持仓")
    print("=" * 70)
    
    # 优先: 机会信号 > 持有信号(加仓) > 持有信号
    portfolio = []
    portfolio.extend([r for r in opp_list])
    portfolio.extend([r for r in hold_list if '加仓' in r['action']])
    portfolio.extend([r for r in hold_list if '加仓' not in r['action']])
    portfolio = sorted(portfolio, key=lambda x: x['strength'], reverse=True)[:5]
    
    if portfolio:
        print(f"\n  建议持有Top{len(portfolio)} (按信号强度排序):")
        for i, r in enumerate(portfolio):
            print(f"    {i+1}. {r['industry']}({r['etf_code']} {r['etf_name']}) "
                  f"信号={r['signal']} CMF短={r['cmf_short']:.4f} 5日涨跌={r['ret_5d']:+.2f}%")
    else:
        print("\n  当前无建议持仓, 空仓等待")
    
    # 需要清仓的
    if risk_list:
        print(f"\n  建议清仓 ({len(risk_list)}个):")
        for r in risk_list:
            print(f"    {r['industry']}({r['etf_name']}) -> {r['signal_desc']}")
    
    # Step 4: 保存结果
    latest_date = results[0]['date']
    signal_file = os.path.join(OUTPUT_DIR, f"signal_{latest_date}.json")
    with open(signal_file, 'w', encoding='utf-8') as f:
        json.dump({
            'run_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_date': latest_date,
            'results': results,
            'summary': {
                'opportunity': len(opp_list),
                'risk': len(risk_list),
                'hold': len(hold_list),
                'warning': len(warn_list),
                'empty': len(empty_list),
                'wait': len(wait_list),
            },
            'portfolio': [{'industry': r['industry'], 'etf_code': r['etf_code'], 
                           'etf_name': r['etf_name'], 'signal': r['signal'],
                           'cmf_short': r['cmf_short'], 'cmf_long': r['cmf_long'],
                           'action': r['action']} for r in portfolio],
        }, f, ensure_ascii=False, indent=2)
    print(f"\n信号已保存: {signal_file}")
    
    # Step 5: 发送飞书
    send_feishu(results, opp_list, risk_list, hold_list, warn_list, 
                empty_list, portfolio, latest_date)
    
    return results


def send_feishu(results, opp_list, risk_list, hold_list, warn_list, 
                empty_list, portfolio, latest_date):
    """发送飞书信号卡片"""
    
    # 机会信号文本
    opp_text = "\n".join([
        f"  {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} -> {r['action']}"
        for r in sorted(opp_list, key=lambda x: x['strength'], reverse=True)
    ]) if opp_list else "  无"
    
    # 风险信号文本
    risk_text = "\n".join([
        f"  {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} -> {r['action']}"
        for r in sorted(risk_list, key=lambda x: x['strength'])
    ]) if risk_list else "  无"
    
    # 持有信号文本
    hold_text = "\n".join([
        f"  {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f} -> {r['action']}"
        for r in sorted(hold_list, key=lambda x: x['strength'], reverse=True)
    ]) if hold_list else "  无"
    
    # 预警信号文本
    warn_text = "\n".join([
        f"  {r['industry']}({r['etf_name']}) CMF短={r['cmf_short']:.4f}"
        for r in warn_list
    ]) if warn_list else "  无"
    
    # 建议持仓
    portfolio_text = "\n".join([
        f"  {i+1}. {r['industry']}({r['etf_name']}) [{r['signal']}]"
        for i, r in enumerate(portfolio)
    ]) if portfolio else "  空仓等待"
    
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content":
            f"**行业资金流向信号 ({latest_date})**\n"
            f"数据日期: {latest_date} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"}},
        {"tag": "hr"},
    ]
    
    # 信号统计
    elements.append({"tag": "column_set", "flex_mode": "bypass", "background_style": "grey", "columns": [
        {"tag": "column", "width": "weighted", "weight": 1, 
         "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"机会\n**{len(opp_list)}**"}}], 
         "vertical_align": "center"},
        {"tag": "column", "width": "weighted", "weight": 1, 
         "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"风险\n**{len(risk_list)}**"}}], 
         "vertical_align": "center"},
        {"tag": "column", "width": "weighted", "weight": 1, 
         "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"持有\n**{len(hold_list)}**"}}], 
         "vertical_align": "center"},
        {"tag": "column", "width": "weighted", "weight": 1, 
         "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"预警\n**{len(warn_list)}**"}}], 
         "vertical_align": "center"},
        {"tag": "column", "width": "weighted", "weight": 1, 
         "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"空仓\n**{len(empty_list)}**"}}], 
         "vertical_align": "center"},
    ]})
    
    elements.append({"tag": "hr"})
    
    # 机会信号
    template = "red" if opp_list else "grey"
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**机会信号 ({len(opp_list)}个) - 资金流入, 考虑建仓**"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": opp_text}})
    
    # 风险信号
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**风险信号 ({len(risk_list)}个) - 资金流出, 考虑清仓**"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": risk_text}})
    
    # 持有信号
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**持有信号 ({len(hold_list)}个) - 资金持续流入, 继续持有**"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": hold_text}})
    
    # 预警信号
    if warn_list:
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**预警信号 ({len(warn_list)}个) - 短期资金流出, 减仓观察**"}})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": warn_text}})
    
    # 建议持仓
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**建议持仓Top{len(portfolio)} (按信号强度排序)**"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": portfolio_text}})
    
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
        "**信号说明**\n"
        "机会: CMF短线上穿0, 资金从流出转流入\n"
        "风险: CMF短线下穿0, 资金从流入转流出\n"
        "持有: CMF为正, 资金持续流入\n"
        "预警: 短期CMF<0但长期CMF>0, 趋势未破\n"
        "空仓: CMF持续为负, 资金持续流出"}})
    
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", 
        "content": "报告来自于 DoMate | 仅供研究参考, 不构成投资建议"}]})
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "报告来自于 DoMate"}, "template": "red"},
        "elements": elements
    }
    
    try:
        resp = requests.post(WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=15)
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            print("\n飞书信号卡片发送成功!")
        else:
            print(f"\n飞书返回: {result}")
    except Exception as e:
        print(f"\n飞书发送失败: {e}")


if __name__ == "__main__":
    main()
