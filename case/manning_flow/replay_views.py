# -*- coding: utf-8 -*-
"""回放验证：当前代码配置（等权W5 / MA20+5日动量 / 趋势优先规则）vs 旧规则（同向才出）
对比创业板/科创50 在 2026年7月大跌、9月反弹、10月大跌 三段的观点转折时点。
口径：与 weekly_report.py 完全一致（compute_anb / INDEX_CONFIG / 判定逻辑复刻）。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import numpy as np
from weekly_report import INDEX_CONFIG, INDUSTRY_ETF_MAP, compute_anb

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------- 1. 加载资金流面板（与主脚本同口径） ----------
df = pd.read_csv(os.path.join(BASE, 'industry_moneyflow_real.csv'))
anb_ma, net_panel, net_ratio_panel, pct_panel = compute_anb(df)

# ---------- 2. 加载指数价格缓存 ----------
price_cache = {}
for name in INDEX_CONFIG:
    p = os.path.join(BASE, 'price_cache', f'{name}.csv')
    if os.path.exists(p):
        px = pd.read_csv(p, dtype={'trade_date': str})
        px['trade_date'] = px['trade_date'].str.replace('-', '')
        px['close'] = pd.to_numeric(px['close'], errors='coerce')
        px = px.sort_values('trade_date').reset_index(drop=True)
        px['date'] = pd.to_datetime(px['trade_date'], format='%Y%m%d')
        price_cache[name] = px

# ---------- 3. 判定函数：复刻 compute_index_views 逻辑，按日期参数化 ----------
EPS = 0.03
MOM_FLOOR = 3.0
NET_BUFFER = 30.0

def view_on(trade_date, anb_ma, net_panel, price_cache, use_new_rule=True):
    """在 trade_date 当日（含）口径下计算宽基观点"""
    t = pd.Timestamp(trade_date)
    if t not in anb_ma.index:
        return None
    anb_row = anb_ma.loc[t]
    net_row = net_panel.loc[t]
    out = {}
    for name, cfg in INDEX_CONFIG.items():
        industries = cfg['industries']
        constituent_anbs = [anb_row.get(ind) for ind in industries if pd.notna(anb_row.get(ind))]
        agg_anb = float(np.mean(constituent_anbs)) if constituent_anbs else 0.0
        day_net = sum(float(net_row.get(ind, 0)) for ind in industries)

        trend_signal = 0
        mom5 = None
        close = ma20 = None
        if name in price_cache:
            px = price_cache[name]
            sub = px[px['date'] <= t]
            if len(sub) >= 20:
                close = float(sub['close'].iloc[-1])
                ma20 = float(sub['close'].rolling(20).mean().iloc[-1])
                close_5d = float(sub['close'].iloc[-6]) if len(sub) >= 6 else float(sub['close'].iloc[0])
                mom5 = (close - close_5d) / close_5d * 100
                above_ma = close > ma20
                momentum_up = mom5 > 0
                if above_ma and momentum_up:
                    trend_signal = 1
                elif not above_ma and not momentum_up:
                    trend_signal = -1
                else:
                    trend_signal = 0

        if use_new_rule:
            # ---- 新规则（当前代码）：价格趋势优先 ----
            if mom5 is not None and mom5 <= -MOM_FLOOR:
                view = "中性" if day_net >= NET_BUFFER else "看空"
            elif mom5 is not None and mom5 >= MOM_FLOOR:
                view = "中性" if day_net <= -NET_BUFFER else "看多"
            elif trend_signal != 0:
                if (trend_signal > 0 and agg_anb >= -EPS) or (trend_signal < 0 and agg_anb <= EPS):
                    view = "看多" if trend_signal > 0 else "看空"
                else:
                    view = "中性"
            else:
                if agg_anb >= EPS and day_net > 0:
                    view = "看多"
                elif agg_anb <= -EPS and day_net < 0:
                    view = "看空"
                else:
                    view = "中性"
        else:
            # ---- 旧规则：ANB 与趋势同向才出多空，否则中性 ----
            anb_positive = agg_anb > 0
            if trend_signal > 0 and anb_positive:
                view = "看多"
            elif trend_signal < 0 and not anb_positive:
                view = "看空"
            else:
                view = "中性"

        out[name] = {'agg_anb': agg_anb, 'day_net': day_net, 'trend': trend_signal,
                     'mom5': mom5, 'close': close, 'ma20': ma20, 'view': view}
    return out

# ---------- 4. 逐日回放 ----------
trade_dates = [d.strftime('%Y%m%d') for d in anb_ma.index]
start = pd.Timestamp('20260610')
trade_dates = [d for d in trade_dates if pd.Timestamp(d) >= start]

records = []
for td in trade_dates:
    new_v = view_on(td, anb_ma, net_panel, price_cache, use_new_rule=True)
    old_v = view_on(td, anb_ma, net_panel, price_cache, use_new_rule=False)
    if new_v is None:
        continue
    records.append({
        'date': td,
        'cyb_new': new_v.get('创业板', {}).get('view'),
        'cyb_old': old_v.get('创业板', {}).get('view'),
        'kc_new': new_v.get('科创50', {}).get('view'),
        'kc_old': old_v.get('科创50', {}).get('view'),
        'cyb_anb': new_v.get('创业板', {}).get('agg_anb'),
        'cyb_mom5': new_v.get('创业板', {}).get('mom5'),
        'kc_anb': new_v.get('科创50', {}).get('agg_anb'),
        'kc_mom5': new_v.get('科创50', {}).get('mom5'),
    })

rec = pd.DataFrame(records)

def find_transition(rec, col, from_views, to_view, date_from, date_to):
    """返回从 from_views 变成 to_view 的第一次日期（在 date_from~date_to 内且之前处于非to状态）"""
    sub = rec[(rec['date'] >= date_from) & (rec['date'] <= date_to)].reset_index(drop=True)
    for i, r in sub.iterrows():
        if r[col] == to_view:
            # 确认前一日（如果存在且在本区间外取区间首日之前的状态）不在 to_view
            prev = rec[rec['date'] < r['date']]
            if len(prev) == 0 or prev.iloc[-1][col] != to_view:
                return r['date']
    return None

# 大纲：创业板/科创50 三段转折
segments = [
    ("7月转空(6/25~7/25)", "20260625", "20260725", "看空"),
    ("9月转多(8/25~9/30)", "20260825", "20260930", "看多"),
    ("10月转空(10/8~10/22)", "20261008", "20261022", "看空"),
]

print("=" * 100)
print("三段行情观点转折对比（新规则=等权W5/MA20+动量5/趋势优先；旧规则=同向才出）")
print("=" * 100)
for seg_label, d1, d2, target in segments:
    print(f"\n### {seg_label} -> 目标观点: {target}")
    for idx_name, col in [("创业板", "cyb_new"), ("科创50", "kc_new")]:
        new_d = find_transition(rec, col, None, target, d1, d2)
        old_col = "cyb_old" if idx_name == "创业板" else "kc_old"
        old_d = find_transition(rec, old_col, None, target, d1, d2)
        print(f"  {idx_name}: 新规则={new_d or 'None(未触及)'}  旧规则={old_d or 'None(未触及)'}")

# 关键日细节：10-22当日（当前报告）；9月中旬反弹抓取细节
print("\n" + "=" * 100)
print("关键日期细节（新规则）")
print("=" * 100)
for td in ["20260910", "20260914", "20260915", "20260916", "20260917", "20260918", "20260921", "20260922",
           "20260930", "20261008", "20261009", "20261012", "20261020", "20261021", "20261022"]:
    nv = view_on(td, anb_ma, net_panel, price_cache, use_new_rule=True)
    ov = view_on(td, anb_ma, net_panel, price_cache, use_new_rule=False)
    if nv is None:
        print(f"  {td}: 无数据")
        continue
    for idx_name in ["创业板", "科创50"]:
        v = nv[idx_name]
        ov_v = ov[idx_name]
        mom = v['mom5']
        mom_s = f"{mom:+.2f}%" if mom is not None else "N/A"
        print(f"  {td} {idx_name}: 新={v['view']} 旧={ov_v['view']} ANB={v['agg_anb']:+.4f} 5日动量={mom_s} 收盘={v['close']} MA20={v['ma20']}")

# 汇总：整个回放窗口内的状态分布（新规则）
print("\n" + "=" * 100)
print("回放窗口 (6/10~10/22) 新规则观点分布")
print("=" * 100)
for idx_name, col in [("创业板", "cyb_new"), ("科创50", "kc_new")]:
    print(f"  {idx_name}: {rec[col].value_counts().to_dict()}")

rec.to_csv(os.path.join(BASE, 'replay_views_daily.csv'), index=False)
print("\n  明细已存: replay_views_daily.csv")