#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场临界监测 C(t) & Gap 的预警判定规则（单一来源）。

严格按用户给定口径实现，图表构建（build_cgap_html.py）与飞书推送（send_cgap_feishu.py）
共用本模块，避免两处各写一套导致口径漂移。

规则
----
🟡 黄警：C > 25%  或  Gap < 5  或  (Δλ₁ < −5 且连续 2 天)
🟠 橙警：C > 30%  且  λ₁ 250 日分位 > 90%
🔴 红警：C > 35%  或  (Gap < 3 且 Δλ₁ < −10)  或  (C > 30% 且 IPR > 0.15)
取值顺序：红 > 橙 > 黄；同级别可同时列出多条触发原因。
退潮诊断（Δλ₁ < 0 时）：λ₂ ↑ → 软着陆；λ₂ 平/微变（±0.5%）→ 过渡期；λ₂ ↓ → 硬着陆。
"""
import numpy as np

FLAT_BAND = 0.5      # λ₂ 变动 ±0.5% 以内视为“平/微变”
WATCH_DAYS = 2       # Δλ₁ < −5 需连续天数
DIVERGE_WIN = 20     # 全A 与 C(t) 背离比较窗口（交易日）
FLAT_C = 0.5         # C(t) 窗口内变动 ±0.5 个百分点视为持平
FLAT_IDX = 0.5       # 全A 窗口内涨跌 ±0.5% 视为持平

TAGS = {'red': ('🔴 红警', '#c0392b'), 'orange': ('🟠 橙警', '#e67e22'),
        'yellow': ('🟡 黄警', '#f1c40f'), 'none': ('⚪ 无预警', '#7f8c8d')}


def _f(v, spec='+'):
    """Δλ₁ 之类可能为 None（序列首日）的安全格式化。"""
    if v is None:
        return 'n/a'
    return f"{v:+.4f}" if spec == '+' else f"{v:.4f}"


def _level(C, Gap, q250, ipr, d, dp):
    """唯一的等级判定：返回 'red'/'orange'/'yellow'/'none'。"""
    if C > 35 or (Gap < 3 and d is not None and d < -10) or (C > 30 and ipr > 0.15):
        return 'red'
    if C > 30 and q250 > 90:
        return 'orange'
    if C > 25 or Gap < 5 or (d is not None and dp is not None and d < -5 and dp < -5):
        return 'yellow'
    return 'none'


def evaluate_conditions(t, hist):
    """逐条核对预警条件，给出 当前值 / 阈值 / 数据日期 / 是否触发 与分组状态。"""
    C, Gap, q250, ipr = t['C'], t['Gap'], t['q250'], t['IPR']
    d = t.get('d_lambda1')
    dp = hist[-2].get('d_lambda1') if len(hist) >= 2 else None
    dd = t.get('date') or t.get('data_date')
    rows = [
        ('黄警', 1, 'C(t) > 25%', f"{C:.2f}%", '> 25%', C > 25, '当日市场协同度'),
        ('黄警', 2, 'Gap < 5（进入切换预警区）', f"{Gap:.3f}", '< 5', Gap < 5, '主线集中度'),
        ('黄警', 3, f'Δλ₁ < −5 且连续 {WATCH_DAYS} 天',
         f"{_f(d)} / {_f(dp)}", f'两日均 < −5', (d is not None and dp is not None and d < -5 and dp < -5),
         '今日 / 昨日 Δλ₁'),
        ('橙警', 4, 'C > 30% 且 λ₁ 250日分位 > 90%',
         f"C {C:.2f}% / 分位 {q250:.1f}%", 'C>30% 且 分位>90%', C > 30 and q250 > 90, '两者需同时成立'),
        ('红警', 5, 'C(t) > 35%', f"{C:.2f}%", '> 35%', C > 35, '当日市场协同度'),
        ('红警', 6, 'Gap < 3 且 Δλ₁ < −10',
         f"Gap {Gap:.3f} / Δλ₁ {_f(d)}", 'Gap<3 且 Δλ₁<−10', Gap < 3 and d is not None and d < -10, '两者需同时成立'),
        ('红警', 7, 'C > 30% 且 IPR > 0.15',
         f"C {C:.2f}% / IPR {ipr:.4f}", 'C>30% 且 IPR>0.15', C > 30 and ipr > 0.15, '龙头拥挤度'),
    ]
    out = []
    for lv, seq, name, cur, thr, trig, note in rows:
        out.append({'level': lv, 'seq': seq, 'name': name, 'current': cur, 'threshold': thr,
                    'data_date': dd, 'triggered': bool(trig),
                    'status': '已触发' if trig else '未触发', 'note': note})
    # 辅助项：成交量变化率不参与预警，接口未更新时明确标为数据未知
    vol_ok = t.get('vol_ratio') is not None
    out.append({'level': '辅助', 'seq': 8, 'name': '成交量变化率（辅助，不参与预警判定）',
                'current': f"{t['vol_ratio']:.3f}" if vol_ok else '不可用',
                'threshold': '仅参考', 'data_date': dd, 'triggered': False,
                'status': '未触发' if vol_ok else '数据未知',
                'note': f"当日成交量接口覆盖率 {t.get('vol_coverage', 'n/a')}"})
    return out


def level_history(hist):
    """逐日重算历史预警等级，统计当前等级连续天数与近期序列。"""
    levels = []
    for i, r in enumerate(hist):
        d = r.get('d_lambda1')
        dp = hist[i - 1].get('d_lambda1') if i >= 1 else None
        levels.append(_level(r['C'], r['Gap'], r['q250'], r['IPR'], d, dp))
    dates = [r['date'] for r in hist]
    cur = levels[-1]
    streak = 1
    for lv in reversed(levels[:-1]):
        if lv == cur:
            streak += 1
        else:
            break
    # 上一次不同等级出现的日期（用于说明“已连续自 X 起”）
    prev_diff = None
    for i in range(len(levels) - 1, -1, -1):
        if levels[i] != cur:
            prev_diff = dates[i]
            break
    return {'levels': levels, 'dates': dates, 'current': cur, 'streak': streak,
            'since': dates[-streak], 'prev_diff': prev_diff,
            'recent_n': 20, 'recent_levels': levels[-20:], 'recent_dates': dates[-20:],
            'last10': [{'date': dates[i], 'C': hist[i]['C'], 'Gap': hist[i]['Gap'],
                        'd_lambda1': hist[i].get('d_lambda1'), 'level': levels[i]}
                       for i in range(max(0, len(hist) - 10), len(hist))]}


def divergence(hist, win=DIVERGE_WIN):
    """全A 与 C(t) 的背离判定（同一窗口、同一交易日）。"""
    if len(hist) <= win:
        return {'ok': False, 'text': f'历史不足 {win} 个交易日，无法判定背离'}
    ic = [r.get('index_close') for r in hist]
    if ic[-1] is None or ic[-1 - win] is None:
        return {'ok': False, 'text': '全A 序列存在缺失，无法判定背离'}
    C_now, C_prev = hist[-1]['C'], hist[-1 - win]['C']
    dC = C_now - C_prev
    dI = (ic[-1] / ic[-1 - win] - 1) * 100
    rc = '持平' if abs(dC) < FLAT_C else ('上行' if dC > 0 else '下行')
    ri = '持平' if abs(dI) < FLAT_IDX else ('上行' if dI > 0 else '下行')
    table = {
        ('上行', '下行'): ('⚠️ 背离：资金抱团少数标的、其余失血（协同度升而全A 跌）', 'warn'),
        ('上行', '上行'): ('✅ 同向共振：集中度与全A 同步走强（普涨式集中）', 'ok'),
        ('下行', '上行'): ('✅ 结构扩散：集中度回落而全A 走强（健康扩散）', 'ok'),
        ('下行', '下行'): ('⚠️ 同步退潮：集中度与全A 同步走弱', 'warn'),
        ('持平', '下行'): ('⚠️ 偏背离：协同度持平而全A 走弱', 'warn'),
        ('下行', '持平'): ('— 集中度回落、全A 走平', 'mid'),
        ('上行', '持平'): ('— 集中度抬升、全A 走平（资金在内部轮动）', 'mid'),
        ('持平', '上行'): ('— 协同度持平、全A 走强（普涨但未抱团）', 'mid'),
        ('持平', '持平'): ('— 协同度与全A 均基本持平', 'mid'),
    }
    text, mark = table[(rc, ri)]
    return {'ok': True, 'win': win, 'dC': dC, 'dI': dI, 'dir_C': rc, 'dir_I': ri,
            'text': text, 'mark': mark, 'C_now': C_now, 'C_prev': C_prev,
            'idx_now': ic[-1], 'idx_prev': ic[-1 - win]}


def retreat_detail(hist):
    """退潮诊断详情：Δλ₁ 为负时按 λ₂ 走势判定软/硬着陆。"""
    if len(hist) < 2:
        return {'applicable': False, 'text': '历史不足 2 日，无法诊断'}
    d = hist[-1].get('d_lambda1')
    l1n, l1p = hist[-1]['lambda1'], hist[-2]['lambda1']
    l2n, l2p = hist[-1]['lambda2'], hist[-2]['lambda2']
    chg = (l2n - l2p) / l2p * 100 if l2p else 0.0
    if d is None or d >= 0:
        return {'applicable': False, 'lambda1': l1n, 'lambda2': l2n,
                'text': f"不适用（Δλ₁={_f(d)} ≥ 0，一致性仍在回升，未进入退潮）",
                'detail': f"λ₁ {l1p:.4f}→{l1n:.4f}（{_f(d)}），λ₂ {l2p:.4f}→{l2n:.4f}"}
    if chg > FLAT_BAND:
        verdict = f'✅ 软着陆：λ₂ 上行（{l2p:.3f}→{l2n:.3f}，{chg:+.2f}%），资金换赛道，可跟新方向'
    elif chg < -FLAT_BAND:
        verdict = f'❌ 硬着陆：λ₂ 下行（{l2p:.3f}→{l2n:.3f}，{chg:+.2f}%），系统性退潮，降仓避险'
    else:
        verdict = f'⚠️ 过渡期：λ₂ 基本持平（{l2p:.3f}→{l2n:.3f}，{chg:+.2f}%），方向未定，观望'
    return {'applicable': True, 'lambda1': l1n, 'lambda2': l2n, 'chg_l2': chg,
            'text': verdict,
            'detail': f"Δλ₁={_f(d)}（负）触发退潮诊断；λ₂ 变动 {chg:+.2f}%，"
                      f"判定阈值 ±{FLAT_BAND}%"}


def percentile_reference(hist, t):
    """本池自身分位刻度参照：说明 15/25/35 与 10/5/3 在该池的适用性。"""
    C_all = np.array([r['C'] for r in hist])
    G_all = np.array([r['Gap'] for r in hist])
    def row(name, arr, cur, unit_fmt):
        return {'name': name, 'p50': float(np.percentile(arr, 50)),
                'p75': float(np.percentile(arr, 75)), 'p90': float(np.percentile(arr, 90)),
                'p95': float(np.percentile(arr, 95)), 'cur': cur,
                'rank_full': float((arr <= cur).mean() * 100),
                'rank250': float((arr[-250:] <= cur).mean() * 100),
                'min': float(arr.min()), 'max': float(arr.max())}
    return {'n_days': len(hist),
            'rows': [row('C(t) %', C_all, t['C'], None),
                     row('Gap', G_all, t['Gap'], None)],
            'lambda1_rank250': t.get('q250')}


def evaluate_rules(t, hist):
    """汇总：等级 / 标签 / 触发原因 / 条件核对 / 预警历史 / 背离 / 退潮 / 分位参照。"""
    conditions = evaluate_conditions(t, hist)
    lh = level_history(hist)
    lvl_from_list = _level(t['C'], t['Gap'], t['q250'], t['IPR'],
                           t.get('d_lambda1'),
                           hist[-2].get('d_lambda1') if len(hist) >= 2 else None)
    if lvl_from_list != lh['current']:
        print(f'[WARN] 等级一致性异常：条件核对={lvl_from_list} 历史重算={lh["current"]}')

    reasons = [f"{c['name']}（当前 {c['current']}，阈值 {c['threshold']}，数据日 {c['data_date']}）"
               for c in conditions if c['status'] == '已触发']
    tag, color = TAGS[lvl_from_list]
    retreat = retreat_detail(hist)['text']

    C_all = [r['C'] for r in hist]
    G_all = [r['Gap'] for r in hist]
    Cfull = (min(C_all), float(np.percentile(C_all, 50)), max(C_all))
    Gfull = (min(G_all), float(np.percentile(G_all, 50)), max(G_all))
    Crank = (np.array(C_all) <= t['C']).mean() * 100
    Grank = (np.array(G_all) <= t['Gap']).mean() * 100
    Crank250 = (np.array(C_all[-250:]) <= t['C']).mean() * 100
    Grank250 = (np.array(G_all[-250:]) <= t['Gap']).mean() * 100

    advice = ('减仓避险：红警条件已满足'
              if lvl_from_list == 'red' else
              '减仓观望：橙警条件成立' if lvl_from_list == 'orange' else
              '观望：黄警条件成立，控制新增仓位' if lvl_from_list == 'yellow' else
              '正常：无预警')
    watch = ('λ₁ 的日变化（Δλ₁）是否由正转负；若转负，立即看 λ₂ 是升是降以判软/硬着陆；'
             '同时看全A 与 C(t) 是否背离（C 升而全A 跌＝抱团少数标的）')

    return {'level': lvl_from_list, 'tag': tag, 'color': color, 'reasons': reasons,
            'conditions': conditions, 'level_history': lh,
            'divergence': divergence(hist), 'retreat_detail': retreat_detail(hist),
            'percentile_ref': percentile_reference(hist, t),
            'retreat': retreat, 'advice': advice, 'watch': watch,
            'Cfull': Cfull, 'Gfull': Gfull,
            'Crank': Crank, 'Grank': Grank,
            'Crank250': Crank250, 'Grank250': Grank250}