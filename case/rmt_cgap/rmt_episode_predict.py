#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「指标 × 事件窗口」预警有效性统计 —— 回答"这几波行情，哪个指标能提前看出来"。

数据：rmt_hist_indicators.json（539 个交易日，全谱指标，与线上 λ₁ 交叉校验一致）。
窗口：四个事件（见 WINDOWS），锚点=下跌波用峰日、上涨用谷日，判定"领先/同步/无"。
判定口径（本文件唯一维护）：
  · 领先：锚点前 20 个交易日内首次触发，且提前量 = 锚点 − 首触日 ≥ 3 个交易日；
  · 同步：锚点后 0~5 个交易日内首次触发（已经跌了/涨了才亮）；
  · 无  ：锚点前 20 日与锚点后 5 日内都没有触发。
  同时给出"触发后 20 个交易日全A 平均收益/最大回撤"，与全样本基准对比，检验方向是否正确。
用法：python3 rmt_episode_predict.py  →  写 rmt_episode_stats.json / rmt_episode_stats.csv
"""
import os
import json

import numpy as np
import pandas as pd

WORK = os.path.dirname(os.path.abspath(__file__))
ROLL = 250
FWD = 20
LEAD_MIN = 3          # 至少领先 3 个交易日才算"领先"
PRE_WIN = 20          # 锚点前 20 个交易日为"预警观察期"
POST_WIN = 5          # 锚点后 5 个交易日为"同步观察期"

# 四个事件窗口（峰/谷日期取自 469 日指数序列的实际高低点，见报告口径）
WINDOWS = [
    {'key': 'W1', 'name': '2026-07 下跌波', 'dir': 'down',
     'anchor': '2026-06-22', 'end': '2026-07-30', 'note': '峰 6624 → 谷 5581'},
    {'key': 'W2', 'name': '2026-08 上涨', 'dir': 'up',
     'anchor': '2026-07-30', 'end': '2026-08-17', 'note': '谷 5581 → 峰 6113'},
    {'key': 'W3', 'name': '2026-10 下跌波', 'dir': 'down',
     'anchor': '2026-09-01', 'end': '2026-10-30', 'note': '峰 5939 → 谷 4953（急跌段 09-24→10-30 为 −14.4%）'},
    {'key': 'W4', 'name': '近日暴涨', 'dir': 'up',
     'anchor': '2026-10-30', 'end': '2026-11-05', 'note': '谷 4953 → 5212（10-30→11-05 +5.2%）'},
]


def roll_pct(s, win=ROLL):
    out = []
    a = s.values
    for i in range(len(a)):
        w = a[max(0, i - win + 1):i + 1]
        w = w[~np.isnan(w)]
        out.append(np.nan if len(w) < 20 or np.isnan(a[i]) else float((w <= a[i]).mean() * 100))
    return pd.Series(out, index=s.index)


def build_signals(df):
    """(key, 名称, 类别, 掩码, 方向说明)。类别：风险 / 机会 / 未实现。"""
    q = {}
    for col in ('rbar', 'entropy_gap', 'ipr', 'gap1', 'd_gap1_5', 'lambda1', 'entropy', 'd_entropy_5'):
        q[col] = roll_pct(df[col])
    return [
        ('r_lo', '⟨r⟩ < 0.43（泊松/齐涨）', '风险', df['rbar'] < 0.43, '齐涨齐跌 → 分散化失效'),
        ('r_hi', '⟨r⟩ > 0.47（Wigner/分化）', '风险', df['rbar'] > 0.47, '分化/混沌'),
        ('r_pct_hi', '⟨r⟩ 250日分位 > 90%', '风险', q['rbar'] > 90, '相对自身历史偏高'),
        ('l1_pct_hi', 'λ₁ 250日分位 > 90%', '风险', df['q250_cg'] > 90, '系统性同步（已证有前瞻区分度）'),
        ('c_abs', 'C(t) > 35%（绝对线）', '风险', df['C'] > 35, '本池恒触发，无区分度'),
        ('eg_pct_hi', '复杂度缺口 250日分位 > 90%', '风险', q['entropy_gap'] > 90, '谱集中 → 狂热拐点'),
        ('eg_up', '熵缺口 5 日上升 > +0.30', '风险', df['d_entropy_5'] > 0.30, '维度扩张'),
        ('ipr_abs', 'IPR > 0.15（绝对线）', '机会', df['ipr'] > 0.15, '龙头拥挤（本池从未触发）'),
        ('ipr_pct_hi', 'IPR 250日分位 > 90%', '机会', q['ipr'] > 90, '抱团更紧'),
        ('gap_narrow', 'Δ收窄：gap₁ 5日变化 > 0', '机会', df['d_gap1_5'] > 0, '风格切换窗口'),
        ('gap_narrow_hi', 'Δ收窄 250日分位 > 90%', '机会', q['d_gap1_5'] > 90, '收窄幅度极端'),
        ('ds_lo', 'ΔS < −0.10（维度坍缩/恐慌底）', '机会', df['d_entropy_5'] < -0.10, '恐慌底'),
        ('tau', 'τ 隧穿（壳层驻留/放量）', '未实现', None, '图上未给 ε 与放量口径，未实现'),
    ]


def main():
    raw = json.load(open(os.path.join(WORK, 'rmt_hist_indicators.json'), encoding='utf-8'))
    df = pd.DataFrame(raw['rows'])
    df = df.rename(columns={'q250': 'q250_cg'})           # q250 来自 CGAP 持仓（λ₁ 250 日分位）
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    have_idx = df['index_close'].notna()
    print(f'样本 {len(df)} 交易日（{df["date"].iloc[0].date()} → {df["date"].iloc[-1].date()}），'
          f'其中带指数收盘 {int(have_idx.sum())} 天')

    # 前瞻收益与回撤（仅在有指数收盘的区间）
    n = len(df)
    fwd20 = np.full(n, np.nan)
    dd20 = np.full(n, np.nan)
    ic = df['index_close'].values
    for i in range(n):
        w = ic[i:i + FWD + 1]
        if len(w) < FWD + 1 or np.isnan(w).any():
            continue
        fwd20[i] = w[-1] / w[0] - 1
        dd20[i] = w.min() / w[0] - 1
    df['fwd20'] = fwd20
    df['dd20'] = dd20
    base_fwd = float(np.nanmean(fwd20))
    base_dd = float(np.nanmean(dd20))

    idx_of = {d.strftime('%Y-%m-%d'): i for i, d in enumerate(df['date'])}
    sigs = build_signals(df)

    rows = []
    for sig_key, label, kind, mask, meaning in sigs:
        r = {'信号': label, '类别': kind, '含义': meaning,
             '全样本触发占比': None, '全样本触发后20日': None, '全样本回撤差': None}
        if mask is not None:
            valid = ~np.isnan(fwd20)
            m = mask.values & valid
            r['全样本触发占比'] = round(float(mask.mean() * 100), 1)
            if m.sum() >= 5:
                on_f = float(np.nanmean(fwd20[m]))
                on_d = float(np.nanmean(dd20[m]))
                r['全样本触发后20日'] = round(on_f, 4)
                r['全样本回撤差'] = round(on_d - base_dd, 4)
                r['前瞻差(pp)'] = round((on_f - base_fwd) * 100, 2)
            # 判定：常态触发 / 极罕 / 有或没有前瞻区分度（门槛 2pp）
            tp = r['全样本触发占比']
            if tp > 30:
                r['判定'] = '常态触发（占比>30%，亮着不代表预警）'
            elif tp < 5:
                r['判定'] = '极罕触发（<5%）'
            elif r['全样本触发后20日'] is None:
                r['判定'] = '样本不足'
            else:
                d = r['全样本触发后20日'] - base_fwd
                r['判定'] = ('有前瞻区分度' if abs(d) >= 0.02 else '区分度不足')
                if (kind == '风险' and d > 0) or (kind == '机会' and d < 0):
                    r['判定'] += '（方向相反）'
        else:
            r['判定'] = '未实现（口径缺失）'
        for W in WINDOWS:
            a = idx_of.get(W['anchor'])
            if a is None:
                r[W['key']] = '锚点缺失'
                continue
            if mask is None:
                r[W['key']] = '未实现'
                continue
            mm = mask.values
            pre = [i for i in range(max(0, a - PRE_WIN), a + 1) if mm[i]]
            post = [i for i in range(a, min(n, a + POST_WIN + 1)) if mm[i]]
            if pre:
                lead = a - pre[0]
                f0 = pre[0]
                tag = ('领先' if lead >= LEAD_MIN else '临界') + \
                      ('（至锚点仍在触发）' if mm[a] else '')
                extra = f'提前 {lead} 个交易日'
            elif post:
                tag = '同步'
                extra = f'锚点后 {post[0] - a} 个交易日'
                f0 = post[0]
            else:
                r[W['key']] = '无'
                r[W['key'] + '_days'] = 0
                continue
            r[W['key']] = f'{tag}'
            r[W['key'] + '_days'] = (a - f0) if pre else (f0 - a)
            r[W['key'] + '_when'] = df['date'].iloc[f0].strftime('%m-%d')
            r[W['key'] + '_fwd'] = None if np.isnan(fwd20[f0]) else round(float(fwd20[f0]), 4)
        rows.append(r)

    out = pd.DataFrame(rows)
    csv = os.path.join(WORK, 'rmt_episode_stats.csv')
    out.to_csv(csv, index=False, encoding='utf-8-sig')
    json.dump({'base_fwd20': base_fwd, 'base_dd20': base_dd,
               'windows': WINDOWS,
               'criteria': {'lead_min': LEAD_MIN, 'pre_win': PRE_WIN, 'post_win': POST_WIN,
                            'roll': ROLL, 'fwd': FWD},
               'rows': json.loads(out.to_json(orient='records'))},
              open(os.path.join(WORK, 'rmt_episode_stats.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    print(f'基准：全样本 20 日前瞻全A 平均 {base_fwd * 100:+.2f}%，平均最大回撤 {base_dd * 100:+.2f}%\n')
    cols = ['信号', 'W1', 'W2', 'W3', 'W4', '全样本触发占比']
    print(out[cols].to_string(index=False, max_colwidth=26))
    print(f'\nCSV -> {csv}')


if __name__ == '__main__':
    main()