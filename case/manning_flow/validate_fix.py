#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线复算验证: 2026-10-22 曼宁资金流周报修正
- 修正1: Top1/Top3 近期确认过滤 (当日净流入>0 或 5日动量>-3%)
- 修正2: 宽基观点 价格趋势优先 + 聚合ANB显著阈值
仅使用本地缓存 industry_moneyflow_real.csv + 腾讯指数行情, 不发飞书。
"""
import os
import sys
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import weekly_report as wr

print("=" * 70)
print("复算验证: 曼宁资金流周报修正 (数据日期 2026-10-22)")
print("=" * 70)

# 1. 加载缓存(含10/22当日90行业全量)
df = wr.load_cache()
print(f"\n[1] 缓存数据: {df.shape[0]} 行, {df['trade_date'].nunique()} 个交易日")
print(f"    最新日期: {df['trade_date'].max()} | 数据覆盖: {df['trade_date'].min()} ~ {df['trade_date'].max()}")

# 2. 复算 ANB / 净流入 / 5日动量
anb_ma, net_panel, net_ratio_panel, pct_panel = wr.compute_anb(df)
latest_date = anb_ma.index[-1]
anb_row = anb_ma.loc[latest_date]
net_row = net_panel.loc[latest_date]

def _prod_ret(x):
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.nan
    return x.prod() - 1

mom5_panel = (1 + pct_panel / 100.0).rolling(5, min_periods=3).apply(_prod_ret, raw=True) * 100.0
mom5_row = mom5_panel.loc[latest_date]

print(f"\n[2] 复算基准 ({latest_date.strftime('%Y-%m-%d')})")
for ind in ["白酒", "银行", "半导体", "养殖业", "医疗器械", "软件开发", "IT服务", "通信设备"]:
    anb = anb_row.get(ind, np.nan)
    net = net_row.get(ind, np.nan)
    mom = mom5_row.get(ind, np.nan)
    if not (pd.isna(anb) and pd.isna(net)):
        print(f"    {ind:<6} ANB(W={wr.W})={anb:+.4f}  当日净流入={net:+7.1f}亿  5日动量={mom:+.2f}%")

# 3. 修正后 Top 过滤复刻 (与 weekly_report.main 同逻辑)
MOM5_FLOOR = -3.0
anb_sorted = anb_row.dropna().sort_values(ascending=False)
top_candidates = []
for ind in anb_sorted.index:
    net_v = net_row.get(ind, 0)
    mom_v = mom5_row.get(ind, np.nan)
    mom_v = -999.0 if pd.isna(mom_v) else mom_v
    if net_v > 0 or mom_v > MOM5_FLOOR:
        top_candidates.append(ind)
    if len(top_candidates) >= 3:
        break
if len(top_candidates) < 3:
    for ind in anb_sorted.index:
        if ind in top_candidates:
            continue
        if net_row.get(ind, 0) > 0:
            top_candidates.append(ind)
        if len(top_candidates) >= 3:
            break
if len(top_candidates) < 3:
    for ind in anb_sorted.index:
        if ind not in top_candidates:
            top_candidates.append(ind)
        if len(top_candidates) >= 3:
            break

print("\n[3] 近期确认过滤 (当日净流入>0 或 5日动量>-3%)")
print(f"    ANB原始Top5: {[f'{i}({anb_row[i]:.4f})' for i in anb_sorted.index[:5]]}")
print(f"    过滤后Top3:   {top_candidates}")
top1 = top_candidates[0]
print(f"    Top1 -> {top1} (ANB={anb_row[top1]:.4f}, 净流入={net_row.get(top1, 0):+.1f}亿, 5日动量={mom5_row.get(top1, np.nan):+.2f}%)")

# 4. 修正后宽基观点
print("\n[4] 宽基指数多空观点 (修正后)")
index_prices = wr.fetch_index_prices(latest_date)
index_views = wr.compute_index_views(anb_ma, net_panel, index_prices)
for name, code, agg_anb, trend_sig, view, detail in index_views:
    trend_str = "多" if trend_sig > 0 else "空" if trend_sig < 0 else "中"
    print(f"    {name:<6} {code:<10} 聚合ANB={agg_anb:+.4f} 趋势={trend_str} 观点={view}")
    print(f"          {detail}")

# 5. 关键校验断言
print("\n[5] 校验断言")
checks = []
checks.append(("白酒被过滤出Top1", top1 != "白酒"))
checks.append(("Top3不含白酒", "白酒" not in top_candidates[:3]))
# W=5 口径下 ANB原始第一为养殖业(动量正、资金无流出), 修正预期为"银行或养殖业"
checks.append(("Top1通过近期确认(养殖业/银行)", top1 in ("养殖业", "银行")))
checks.append(("净流入榜前列未漏推银行", "银行" in top_candidates[:3]))
cyj_view = next((v for n, _, _, _, v, _ in index_views if n == "创业板"), None)
checks.append(("创业板判看空", cyj_view == "看空"))
for name, want in [("全A", "看空"), ("沪深300", "看空"), ("创业板", "看空"), ("科创50", "看空")]:
    got = next((v for n, _, _, _, v, _ in index_views if n == name), None)
    print(f"    {name:<6} 观点={got} (期望={want})")
    checks.append((f"{name}观点={want}", got == want))
for desc, ok in checks:
    print(f"    [{'PASS' if ok else 'FAIL'}] {desc}")

print("\n完成!")