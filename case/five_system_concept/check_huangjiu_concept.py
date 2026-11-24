#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线验证: 概念轮动加入"黄酒"概念后, 黄酒能否出现在五系统打分/TOP榜中 (不发飞书)"""
import sys, os, time, json
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w/pylib")
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd, numpy as np

import daily_concept_report as dcr
from concept_data_pipeline_v2 import MANUAL_CONCEPTS, SINA_CONCEPTS, MAX_STOCKS_PER_CONCEPT, get_sina_concept_stocks

STEP = 0
def log(s):
    global STEP
    STEP += 1
    print(f"[{STEP}] {s}", file=sys.stderr, flush=True)

log("构建概念成分股(含黄酒)")
all_concepts = {}
for cname, stocks in MANUAL_CONCEPTS.items():
    all_concepts[cname] = {dcr.normalize_code(c): n for c, n in stocks.items()}
for node, cname in SINA_CONCEPTS.items():
    try:
        stocks = get_sina_concept_stocks(node, MAX_STOCKS_PER_CONCEPT)
        if stocks:
            all_concepts[cname] = {dcr.normalize_code(c): n for c, n in stocks.items()}
    except Exception:
        pass
    time.sleep(0.05)
all_codes = list({c for s in all_concepts.values() for c in s.keys()})
print(f"  共 {len(all_concepts)} 概念, {len(all_codes)} 只股票; 黄酒成分: {all_concepts.get('黄酒')}")

log("拉取K线(4并发)")
stock_data = {}
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(dcr.fetch_kline, code): code for code in all_codes}
    for f in as_completed(futs):
        code = futs[f]
        try:
            df, src = f.result()
            if df is not None and len(df) > 0:
                stock_data[code] = df
        except Exception:
            pass
print(f"  成功 {len(stock_data)}/{len(all_codes)}")

log("合成概念指数")
df_returns, df_close, df_volume = dcr.build_concept_data(all_concepts, stock_data)
valid_cols = [c for c in df_close.columns if len(df_close[c].dropna()) >= dcr.DAYS]
df_returns = df_returns[valid_cols]; df_close = df_close[valid_cols]; df_volume = df_volume[valid_cols]
concept_names = list(df_returns.columns)
print(f"  有效概念: {len(concept_names)}; 黄酒在列: {'黄酒' in concept_names}")
print(f"  数据截止: {df_close.index[-1].date()}")

log("运行五系统")
hodge = dcr.run_hodge(df_returns, df_close, concept_names)
rmt = dcr.run_rmt(df_returns, df_close, concept_names)
spd = dcr.run_spd(df_returns, df_close, concept_names)
manning = dcr.run_manning(df_returns, df_close, df_volume, concept_names)
ot = dcr.run_ot(df_returns, df_close, concept_names)

log("技术指标验证(MA40+VMD+横截面动量+延续)")
tech = dcr.compute_tech_indicators(df_close, df_returns, concept_names, spd, rmt)

log("整合评分")
integrated = []
for name in concept_names:
    h, r, s, m, o = hodge[name], rmt[name], spd[name], manning[name], ot[name]
    total = h['score'] + r['score'] + s['score'] + m['score'] + o['score']
    opp_votes = sum(1 for x in [h, r, s, m, o] if x['eval'] == '机会')
    bull_votes = sum(1 for x in [h, r, s, m, o] if x['eval'] in ['机会', '偏多'])
    risk_votes = sum(1 for x in [h, r, s, m, o] if x['eval'] in ['风险', '偏空'])
    if opp_votes >= 4: ev = '强机会'
    elif opp_votes >= 3: ev = '机会'
    elif bull_votes >= 4: ev = '偏多'
    elif bull_votes >= 1 and risk_votes == 0: ev = '弱偏多'
    elif risk_votes >= 4: ev = '强风险'
    elif risk_votes >= 3: ev = '风险'
    else: ev = '中性'
    integrated.append({
        'name': name, 'total_score': total, 'eval': ev,
        'opp_votes': opp_votes, 'bull_votes': bull_votes, 'risk_votes': risk_votes,
        'hodge_eval': h['eval'], 'rmt_eval': r['eval'], 'spd_eval': s['eval'],
        'manning_eval': m['eval'], 'ot_eval': o['eval'],
    })
integrated.sort(key=lambda x: x['total_score'], reverse=True)

print("\n" + "=" * 70)
print("TOP15 机会概念 (五系统总分)")
print("=" * 70)
for i, it in enumerate(integrated[:15], 1):
    print(f"{i:>2}. {it['name']:<10} 总分{it['total_score']:>5.1f} [{it['eval']}] "
          f"机会{it['opp_votes']}/偏多{it['bull_votes']}/风险{it['risk_votes']} "
          f"[{it['hodge_eval'][:2]}|{it['rmt_eval'][:2]}|{it['spd_eval'][:2]}|{it['manning_eval'][:2]}|{it['ot_eval'][:2]}]")

hj = next((x for x in integrated if x['name'] == '黄酒'), None)
print("\n" + "=" * 70)
if hj:
    rank = [x['name'] for x in integrated].index('黄酒') + 1
    print(f">>> 黄酒概念: 总分 {hj['total_score']:.2f}, 全场排名 {rank}/{len(integrated)}, 评级: {hj['eval']}")
    t = tech.get('黄酒', {})
    if t:
        print(f"    技术指标: MA40 {t.get('ma40_trend')} (gap {t.get('ma40_gap')}%), "
              f"VMD {t.get('vmd_dir')}/{t.get('vmd_label')}(强度{t.get('vmd_str')}), "
              f"动量排名 {t.get('mom_rank')}/{t.get('n_total')}, "
              f"延续概率 {t.get('cont_prob')}% [{t.get('cont_label')}]")
else:
    print(">>> 黄酒概念未进入有效概念列表!")
    print("有效概念:", concept_names)

out = {
    'date': str(df_close.index[-1].date()), 'n_concepts': len(concept_names),
    'huangjiu': hj, 'top15': integrated[:15],
}
with open("huangjiu_concept_check.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
print("\n已保存: huangjiu_concept_check.json")