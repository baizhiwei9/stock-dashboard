#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场临界监测 C(t) & Gap —— 机会侧口径（行业/宽基 ETF 多维排序）。

与风险侧（C(t)/Gap 与六条阈值预警）并列使用，**不改动任何预警口径**：
本模块只用同一批已取到的行情做派生计算，输出"池内哪些标的相对更有机会"的排序。

口径（全部可复算，属于规则化的派生排序，不是预测模型，也不给出胜率承诺）
--------------------------------------------------------------------------
相对动量 RS20 : ETF 近 20 个交易日收益 − 全A（中证全指 000985）同期收益
趋势分  trend  : (收盘 > MA20) + (MA20 > MA60) → 0/1/2 分
量能扩张 volr  : 近 5 个交易日平均成交量 ÷ 前 20 个交易日平均成交量
池内协动 corr  : 该 ETF 与池内其余标的在 60 日窗口的平均相关系数（越低越走独立行情）
位置          : 距 60 日高点 pos60、250 日区间位置 pos250（仅作参照，不入综合分）

综合分 score = 0.40 × 池内百分位(RS20) + 0.30 × (trend / 2) + 0.30 × 池内百分位(volr)
等级   grade : score ≥ 0.75 且 RS20 > 0 且站上 MA20 → 机会；≥ 0.55 → 关注；
               ≥ 0.40 → 观察；其余 → 弱势（相对弱且未站上 MA20，作为反向参照）

数据缺失一律按 NaN 处理并在结果中标注，不做前向填充、不编造。
"""
import numpy as np
import pandas as pd

W_RET = 20            # 相对动量窗口（交易日）
W_TREND_FAST = 20     # 快均线
W_TREND_SLOW = 60     # 慢均线
W_VOL_SHORT = 5       # 量能扩张：短窗口
W_VOL_LONG = 20       # 量能扩张：基准窗口
W_POS60 = 60          # 距 60 日高点
W_POS250 = 250        # 250 日区间位置
CORR_WIN = 60         # 池内协动度窗口

METHOD = ('多维排序：相对动量 RS20 40% + 趋势(MA20/MA60) 30% + 量能扩张(5日/20日) 30%，'
          '各项取池内百分位后加权；规则化排序，非预测模型')


def _pct_rank(s):
    """池内百分位（0~1），并列取平均秩；全为 NaN 时回 0.5，避免整列失真。"""
    r = s.rank(pct=True, method='average')
    return r.fillna(0.5)


def _grade(score, rs20, above_ma20):
    if score >= 0.75 and rs20 is not None and rs20 > 0 and above_ma20 == 1:
        return '机会'
    if score >= 0.55:
        return '关注'
    if score >= 0.40:
        return '观察'
    return '弱势'


def _f(v, nd=4):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


def compute_opportunity(df_close, df_vol, idx, names, as_of=None):
    """返回机会侧结果字典；数据不足时返回 None（调用方如实标注）。"""
    codes = [c for c in df_close.columns if c != 'date']
    if len(df_close) < W_TREND_SLOW + W_RET + 5:
        return None

    cl = df_close.set_index('date')[codes].astype(float)
    vl = df_vol.set_index('date')[codes].astype(float)
    idx = pd.Series(idx).astype(float)
    if len(idx) != len(cl):
        return None

    close = cl.iloc[-1]
    prev = cl.iloc[-2]
    ret20 = close / cl.iloc[-W_RET - 1] - 1
    allA_ret20 = float(idx.iloc[-1] / idx.iloc[-W_RET - 1] - 1)
    rs20 = ret20 - allA_ret20

    ma20 = cl.iloc[-W_TREND_FAST:].mean()
    ma60 = cl.iloc[-W_TREND_SLOW:].mean()
    above = (close > ma20).astype(int)
    ma20_gt60 = (ma20 > ma60).astype(int)
    trend = above + ma20_gt60

    v5 = vl.iloc[-W_VOL_SHORT:].mean()
    v20 = vl.iloc[-W_VOL_LONG - 1:-1].mean()
    volr = v5 / v20.replace(0, np.nan)

    rets = cl.pct_change().iloc[-CORR_WIN:]
    cm = rets.corr()
    corr_avg = cm.apply(lambda r: (r.sum() - 1) / (len(r) - 1))

    hi60 = cl.iloc[-W_POS60:].max()
    pos60 = close / hi60 - 1
    w250 = cl.iloc[-W_POS250:] if len(cl) >= W_POS250 else cl
    lo250, hi250 = w250.min(), w250.max()
    pos250 = (close - lo250) / (hi250 - lo250).replace(0, np.nan)

    df = pd.DataFrame({
        'code': codes,
        'name': [names.get(c, c) for c in codes],
        'close': close.values,
        'chg1': (close / prev - 1).values,
        'ret20': ret20.values,
        'rs20': rs20.values,
        'above_ma20': above.values,
        'ma20_gt_ma60': ma20_gt60.values,
        'trend': trend.values,
        'volr': volr.values,
        'corr_avg': corr_avg.values,
        'pos60': pos60.values,
        'pos250': pos250.values,
    })
    df['score'] = (0.40 * _pct_rank(df['rs20'])
                   + 0.30 * (df['trend'] / 2.0)
                   + 0.30 * _pct_rank(df['volr']))
    df['grade'] = [_grade(float(s), (None if pd.isna(r) else float(r)), int(a))
                   for s, r, a in zip(df['score'], df['rs20'], df['above_ma20'])]
    df = df.sort_values('score', ascending=False).reset_index(drop=True)

    rows = []
    for _, r in df.iterrows():
        rows.append({
            'code': r['code'], 'name': r['name'],
            'close': _f(r['close'], 4),
            'chg1': _f(r['chg1'], 4),
            'ret20': _f(r['ret20'], 4),
            'rs20': _f(r['rs20'], 4),
            'above_ma20': int(r['above_ma20']),
            'ma20_gt_ma60': int(r['ma20_gt_ma60']),
            'trend': int(r['trend']),
            'volr': _f(r['volr'], 3),
            'corr_avg': _f(r['corr_avg'], 3),
            'pos60': _f(r['pos60'], 4),
            'pos250': _f(r['pos250'], 4),
            'score': _f(r['score'], 4),
            'grade': r['grade'],
        })

    strong = [f"{x['name']}({x['code']})" for x in rows if x['grade'] == '机会']
    weak = [f"{x['name']}({x['code']})" for x in rows if x['grade'] == '弱势']
    return {
        'as_of': as_of or (cl.index[-1].strftime('%Y-%m-%d')),
        'method': METHOD,
        'allA_ret20': _f(allA_ret20, 4),
        'rows': rows,
        'top': [{'name': x['name'], 'code': x['code'], 'score': x['score'],
                 'rs20': x['rs20'], 'volr': x['volr'], 'trend': x['trend'],
                 'grade': x['grade']} for x in rows[:5]],
        'strong': strong,
        'weak': weak,
    }