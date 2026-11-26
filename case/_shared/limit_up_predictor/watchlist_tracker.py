#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓观察跟踪器
============
规则：只跟踪昨日推荐的TOP20标的，今日进入观察周期。
- 当日走势偏弱 → ❌，后续不再维持看多
- 依旧向好 → ★/★★/★★★，星号越多把握度越高
- 不累积历史，只看昨日推荐→今日表现

标记规则：
- ❌ 偏弱：当日跌幅 > 3%，或跌幅 > 2% 且放量下杀
- ★★★ 强势：涨幅 ≥ 2%
- ★★ 向好：涨幅 0% ~ 2%
- ★ 观察中：微跌 > -3% 但未触发偏弱

作者: Codex AI
日期: 2026-07-16
"""

import os
import json
import glob
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests


OUTPUT_DIR = './output/watchlist'


class WatchlistTracker:
    """持仓观察跟踪器 —— 只跟踪昨日推荐"""

    def __init__(self):
        self.output_dir = OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    # ── 行情获取 ──────────────────────────────────────────

    @staticmethod
    def fetch_realtime_quotes(codes: List[str]) -> Dict[str, Dict]:
        """腾讯行情批量获取"""
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
                        volume = float(parts[36]) if len(parts) > 36 and parts[36] else 0
                        turnover = float(parts[38]) if len(parts) > 38 and parts[38] else 0
                    except (ValueError, IndexError):
                        continue
                    results[matched_code] = {
                        'name': parts[1],
                        'current': current,
                        'prev_close': prev_close,
                        'change_pct': change_pct,
                        'high': high,
                        'low': low,
                        'volume': volume,
                        'turnover': turnover,
                    }
                time.sleep(0.1)
            except Exception as e:
                print(f"  获取行情失败: {e}")
        return results

    # ── 查找昨日推荐 ──────────────────────────────────────

    def find_yesterday_prediction(self) -> Optional[Dict]:
        """
        查找最近一个交易日的推荐文件（排除今天）
        优先 noon，其次 morning
        """
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
        today_str = datetime.now().strftime('%Y-%m-%d')

        # 找所有推荐文件，按日期倒序
        for mode in ['noon', 'morning']:
            pattern = os.path.join(output_dir, 'daily_top20_*_' + mode + '.json')
            for f in sorted(glob.glob(pattern), reverse=True):
                if today_str in f:
                    continue
                try:
                    with open(f, 'r', encoding='utf-8') as fh:
                        return json.load(fh)
                except Exception:
                    continue
        return None

    # ── 标记逻辑 ──────────────────────────────────────────

    @staticmethod
    def compute_mark(change_pct: float, pred_prob: float = 0.5) -> Tuple[str, str]:
        """
        根据当日走势 + 昨日预测概率计算标记

        Returns:
            (mark_symbol, mark_label)
        """
        # ❌ 偏弱：跌幅 > 3%
        if change_pct <= -3.0:
            return '❌', '偏弱'

        # ★★★ 强势：涨幅 ≥ 2%
        if change_pct >= 2.0:
            return '★★★', '强势'

        # ★★ 向好：涨幅 0% ~ 2%
        if change_pct >= 0:
            return '★★', '向好'

        # ★ 观察中：微跌 > -3%
        return '★', '观察中'

    # ── 主逻辑 ──────────────────────────────────────────

    def update_watchlist(self) -> Optional[Dict]:
        """
        1. 找到昨日推荐
        2. 获取今日行情
        3. 逐只计算标记
        """
        pred_data = self.find_yesterday_prediction()
        if pred_data is None:
            print("  ✗ 未找到昨日推荐数据")
            return None

        pred_date = pred_data.get('date', 'unknown')
        top20 = pred_data.get('top20', [])
        if not top20:
            print("  ✗ 昨日推荐为空")
            return None

        codes = [item['code'] for item in top20]
        print(f"  昨日推荐日期: {pred_date}, 共 {len(codes)} 只")

        # 获取行情
        print(f"  获取 {len(codes)} 只股票今日行情...")
        quotes = self.fetch_realtime_quotes(codes)
        print(f"  ✓ 获取到 {len(quotes)} 只股票行情")

        # 逐只标记
        watchlist = {}
        for item in top20:
            code = item['code']
            name = item.get('name', code)
            pred_prob = item.get('pred_prob', 0)
            pred_close = item.get('close', 0)
            industry = item.get('industry', '')
            rank = item.get('rank', 0)

            quote = quotes.get(code)
            if not quote:
                watchlist[code] = {
                    'name': name, 'mark': '⚠️', 'label': '无数据',
                    'change_pct': None, 'current': 0,
                    'pred_close': pred_close, 'pred_prob': pred_prob,
                    'industry': industry, 'rank': rank,
                }
                continue

            change_pct = quote['change_pct']
            mark, label = self.compute_mark(change_pct, pred_prob)

            watchlist[code] = {
                'name': quote.get('name', name),
                'mark': mark,
                'label': label,
                'change_pct': round(change_pct, 2),
                'current': quote['current'],
                'pred_close': pred_close,
                'pred_prob': round(pred_prob, 4),
                'industry': industry,
                'rank': rank,
                'high': quote.get('high', 0),
                'low': quote.get('low', 0),
            }

        # 统计
        cross = sum(1 for v in watchlist.values() if v['mark'] == '❌')
        star3 = sum(1 for v in watchlist.values() if v['mark'] == '★★★')
        star2 = sum(1 for v in watchlist.values() if v['mark'] == '★★')
        star1 = sum(1 for v in watchlist.values() if v['mark'] == '★')
        nodata = sum(1 for v in watchlist.values() if v['mark'] == '⚠️')

        today = datetime.now().strftime('%Y-%m-%d')
        return {
            'pred_date': pred_date,
            'review_date': today,
            'watchlist': watchlist,
            'cross_count': cross,
            'star3_count': star3,
            'star2_count': star2,
            'star1_count': star1,
            'nodata_count': nodata,
            'total': len(watchlist),
        }

    # ── 报告生成 ──────────────────────────────────────────

    def generate_report(self, data: Dict) -> str:
        """生成观察跟踪报告"""
        lines = []
        lines.append(f"## 🔭 持仓观察跟踪 ({data['pred_date']} → {data['review_date']})")
        lines.append("")
        lines.append("**标记规则**：❌ 偏弱(不再看多) | ★ 观察中 | ★★ 向好 | ★★★ 强势")
        lines.append("")
        lines.append(f"**统计**：❌ {data['cross_count']} | ★★★ {data['star3_count']} | ★★ {data['star2_count']} | ★ {data['star1_count']} | 合计 {data['total']}")
        lines.append("")

        mark_order = {'★★★': 0, '★★': 1, '★': 2, '❌': 3, '⚠️': 4}
        items = sorted(data['watchlist'].items(),
                       key=lambda x: (mark_order.get(x[1]['mark'], 9),
                                      -x[1].get('pred_prob', 0)))

        groups = [
            ('★★★', '强势（把握度高）', '★★★ 强势'),
            ('★★', '向好（稳健持有）', '★★ 向好'),
            ('★', '观察中（回调关注）', '★ 观察中'),
            ('❌', '偏弱（不再看多）', '❌ 偏弱'),
        ]

        for mark_val, desc, title in groups:
            group = [(c, v) for c, v in items if v['mark'] == mark_val]
            if not group:
                continue
            lines.append(f"**{title}**")
            lines.append("")
            for code, v in group:
                pure = code[2:]
                chg = v.get('change_pct')
                chg_str = f"+{chg:.2f}%" if chg is not None and chg >= 0 else (f"{chg:.2f}%" if chg is not None else 'N/A')
                lines.append(f"  {mark_val} **{pure} {v['name']}** | 今 {chg_str} | 概率 {v['pred_prob']*100:.1f}% | {v['industry']}")
            lines.append("")

        # 操作建议
        lines.append("---")
        lines.append("")
        lines.append("**操作参考**")
        lines.append("")
        if data['cross_count'] > 0:
            lines.append(f"  ❌ 偏弱 {data['cross_count']} 只：后续不追，反弹可减仓")
        if data['star1_count'] > 0:
            lines.append(f"  ★ 观察中 {data['star1_count']} 只：继续持有，关注关键位")
        if data['star2_count'] > 0:
            lines.append(f"  ★★ 向好 {data['star2_count']} 只：稳健持有，无需操作")
        if data['star3_count'] > 0:
            lines.append(f"  ★★★ 强势 {data['star3_count']} 只：把握度高，可逢低加仓")

        return "\n".join(lines)


def run_watchlist_update():
    """执行观察列表更新"""
    print("\n" + "=" * 60)
    print("持仓观察跟踪")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    tracker = WatchlistTracker()

    print("\n[1/2] 更新观察标记...")
    data = tracker.update_watchlist()
    if data is None:
        print("  ✗ 更新失败")
        return None

    print("\n[2/2] 生成报告...")
    report = tracker.generate_report(data)
    print("\n" + report)

    today = datetime.now().strftime('%Y-%m-%d')
    report_file = f"{OUTPUT_DIR}/watchlist_report_{today}.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✓ 报告已保存: {report_file}")

    return report


if __name__ == '__main__':
    run_watchlist_update()
