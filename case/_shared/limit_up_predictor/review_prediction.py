#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预测复盘模块
============
对比昨日预测的 TOP20 股票与今日实际涨跌，判断是否上涨。
涨了就✅，跌了就❌，并显示昨日涨幅。
"""

import os
import json
import glob
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


def is_up(change_pct: float) -> bool:
    """判断是否上涨（涨幅>0即为涨）"""
    return change_pct > 0


def fetch_realtime_quotes(codes: List[str]) -> Dict[str, Dict]:
    """
    通过腾讯实时行情接口批量获取股票今日数据
    返回: {code: {name, current, prev_close, change_pct, high, low}}
    """
    results = {}
    batch_size = 50

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        query = ','.join(batch)

        try:
            url = f'https://qt.gtimg.cn/q={query}'
            r = requests.get(url, timeout=15)

            for line in r.text.strip().split(';'):
                line = line.strip()
                if not line or '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 35:
                    continue

                stock_code_raw = parts[2]
                # 匹配回 code
                matched_code = None
                for code in batch:
                    if code[2:] == stock_code_raw:
                        matched_code = code
                        break
                if not matched_code:
                    continue

                try:
                    current = float(parts[3]) if parts[3] else 0
                    prev_close = float(parts[4]) if parts[4] else 0
                    change_pct = float(parts[32]) if parts[32] else 0
                    high = float(parts[33]) if parts[33] else 0
                    low = float(parts[34]) if parts[34] else 0
                except (ValueError, IndexError):
                    continue

                results[matched_code] = {
                    'name': parts[1],
                    'current': current,
                    'prev_close': prev_close,
                    'change_pct': change_pct,
                    'high': high,
                    'low': low,
                }

            time.sleep(0.1)
        except Exception as e:
            print(f"  获取实时行情失败(batch {i}): {e}")

    return results


def find_yesterday_prediction_file(output_dir: str = None) -> Optional[str]:
    """
    查找最近一个交易日的预测文件
    按日期倒序查找 output/daily_top20_YYYY-MM-DD_morning.json
    """
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')

    pattern = os.path.join(output_dir, 'daily_top20_*_morning.json')
    files = glob.glob(pattern)

    if not files:
        return None

    # 按文件名（含日期）倒序
    files.sort(reverse=True)

    today_str = datetime.now().strftime('%Y-%m-%d')
    for f in files:
        # 跳过今天的文件（今天的是当前预测，不是昨日预测）
        if today_str in f:
            continue
        return f

    # 如果今天没有文件，返回最新的
    return files[0] if files else None


def load_prediction(filepath: str) -> Optional[Dict]:
    """加载预测文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  加载预测文件失败: {filepath}, {e}")
        return None


def review_predictions(prediction_file: str = None, top_n: int = 20) -> Dict:
    """
    复盘核心逻辑

    Returns:
        {
            'pred_date': '2026-07-13',
            'review_date': '2026-07-14',
            'results': [
                {
                    'rank': 1,
                    'code': 'sz000059',
                    'name': '华锦股份',
                    'pred_prob': 0.545,
                    'pred_close': 4.27,
                    'actual_change_pct': 3.51,
                    'up': False,
                },
                ...
            ],
            'up_count': 2,
            'total': 20,
            'up_rate': 0.10,
        }
    """
    # 1. 找到预测文件
    if prediction_file is None:
        prediction_file = find_yesterday_prediction_file()

    if prediction_file is None:
        print("✗ 未找到历史预测文件")
        return None

    print(f"  加载预测文件: {prediction_file}")
    pred_data = load_prediction(prediction_file)
    if pred_data is None:
        return None

    pred_date = pred_data.get('date', 'unknown')
    top20 = pred_data.get('top20', [])

    if not top20:
        print("✗ 预测文件中没有 TOP20 数据")
        return None

    # 2. 提取代码列表
    codes = [item['code'] for item in top20[:top_n]]

    # 3. 获取今日实际行情
    print(f"  获取 {len(codes)} 只股票今日行情...")
    quotes = fetch_realtime_quotes(codes)

    if not quotes:
        print("✗ 未获取到今日行情数据")
        return None

    # 4. 逐只对比
    results = []
    hit_count = 0

    for item in top20[:top_n]:
        code = item['code']
        name = item.get('name', code)
        pred_prob = item.get('pred_prob', 0)
        pred_close = item.get('close', 0)
        actual = quotes.get(code)
        if actual is None:
            results.append({
                'rank': item.get('rank', len(results) + 1),
                'code': code,
                'name': name,
                'pred_prob': pred_prob,
                'pred_close': pred_close,
                'actual_change_pct': None,
                'up': False,
                'error': '无行情数据',
            })
            continue

        change_pct = actual['change_pct']
        up = is_up(change_pct)

        if up:
            hit_count += 1

        results.append({
            'rank': item.get('rank', len(results) + 1),
            'code': code,
            'name': actual.get('name', name),
            'pred_prob': pred_prob,
            'pred_close': pred_close,
            'actual_change_pct': change_pct,
            'actual_close': actual['current'],
            'up': up,
        })

    return {
        'pred_date': pred_date,
        'review_date': datetime.now().strftime('%Y-%m-%d'),
        'results': results,
        'up_count': hit_count,
        'total': len(results),
        'up_rate': hit_count / len(results) if results else 0,
    }


def format_review_report(review_data: Dict) -> str:
    """格式化复盘报告"""
    if review_data is None:
        return "❌ 复盘数据不可用"

    lines = []
    lines.append(f"## 📋 预测复盘 ({review_data['pred_date']} → {review_data['review_date']})")
    lines.append("")

    for r in review_data['results']:
        code = r['code']
        pure_code = code[2:] if len(code) > 2 else code
        name = r['name']
        prob_pct = f"{r['pred_prob']*100:.1f}%"

        if r.get('actual_change_pct') is not None:
            change = r['actual_change_pct']
            change_str = f"+{change:.2f}%" if change > 0 else f"{change:.2f}%"

            if r.get('up', r.get('hit', False)):
                mark = "✅"
            else:
                mark = "❌"

            lines.append(f"{mark} **{pure_code} {name}** | 预测概率 {prob_pct} | 昨日涨幅 {change_str}")
        else:
            error_msg = r.get('error', '未知')
            lines.append(f"⚠️ **{pure_code} {name}** | 预测概率 {prob_pct} | {error_msg}")

    lines.append("")
    up_count = review_data.get('up_count', review_data.get('hit_count', 0))
    up_rate = review_data.get('up_rate', review_data.get('hit_rate', 0))
    lines.append(f"**上涨命中率**: {up_count}/{review_data['total']} "
                 f"({up_rate*100:.1f}%)")

    return "\n".join(lines)


def format_review_brief(review_data: Dict) -> str:
    """简洁版复盘（用于嵌入晨报）"""
    if review_data is None:
        return ""

    lines = []
    up_count = review_data.get('up_count', review_data.get('hit_count', 0))
    total = review_data['total']
    up_rate = review_data.get('up_rate', review_data.get('hit_rate', 0))
    rate = up_rate * 100

    lines.append(f"## 📋 昨日预测复盘 ({review_data['pred_date']} → {review_data['review_date']})")
    lines.append(f"**上涨命中率: {up_count}/{total} ({rate:.1f}%)**")
    lines.append("")

    for r in review_data['results']:
        pure_code = r['code'][2:] if len(r['code']) > 2 else r['code']
        up = r.get('up', r.get('hit', False))
        mark = "✅" if up else "❌"

        if r.get('actual_change_pct') is not None:
            change = r['actual_change_pct']
            change_str = f"+{change:.2f}%" if change > 0 else f"{change:.2f}%"
            lines.append(f"{mark} {pure_code} {r['name']}  {change_str}")
        else:
            lines.append(f"⚠️ {pure_code} {r['name']}  无数据")

    lines.append("")
    return "\n".join(lines)


if __name__ == '__main__':
    print("=" * 60)
    print("预测复盘")
    print("=" * 60)

    result = review_predictions()
    if result:
        report = format_review_report(result)
        print("\n" + report)
    else:
        print("未能生成复盘报告")
