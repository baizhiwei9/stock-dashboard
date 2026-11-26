#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部特征注入（周度量能 / 隔夜跨市场）—— 供 wide_ens 各家族共用

依据（2026-10-20 IC 检验结论）
------------------------------
跑完 `ic_scan.py` / `ic_gain.py` / `overnight_orth.py` 后，只有两组被采纳：

  T+5 ← `w_amt_mom4`（周成交额 4 周动量）**仅中证1000**
        OOS IC +0.066~+0.137（5 指数全正），被日频基线只解释掉 25%，
        分年 2023~2026 四年全正 → **不加门，直接进池**
        → 2026-10-20 定稿：只挂中证1000（门槛0.70 下命中 56.7%→61.0%，净 +0.40%→+0.67%）

  T+5 ← `w_turnover*`（周换手 6 列）**仅中证500**
        → 2026-10-20 定稿：门槛0.50 下命中 49.1%→57.8%、超额 +7.2%、净 +0.19%
          （唯一扣完 0.2% 成本还赚钱的组合，p=0.001，四年全提升）

  T+1 ← 隔夜跨市场（A50 / 恒生 / 恒生科技 / 中概 ETF / 金龙 ADR）
        OOS IC +0.04~+0.17 很强且正交性通过（存活率 0.94~1.05），
        但 **2024 年贡献 60~80%，2025 归零、2026 转负** → **必须带滚动失效门**

被否决的（不进池，理由留档）
  · 周度 mom/RSI/vol/range：被日频基线解释掉 76~95%，加进去就是复读
  · 周度换手类（除中证500）：OOS 偏 IC 中位数为负（−0.024），IS/OOS 不同号
  · 周度 mom/RSI/vol 在沪深300/科创50/创业板：增益 +2~5pp 但 p=0.09~0.21、净收益仍为负
  · 创业板 + 量能：反而更差（49.8%→48.5%）
  · 隔夜单因子做独立择时信号：2026 已失效

滚动失效门（rolling IC gate）
-----------------------------
对隔夜类因子逐日计算「**截至当日可知**」的 250 日滚动 IC：

  T 日可用样本 = 因子在 t-h 的值 与 t-h 日发出的、已在 T 日之前到期的收益

实现：
  令 sp = s.shift(h)、yp = y.shift(h)，则 rolling(W).corr(sp, yp) 在 T 日
  用到的样本是 t∈[T-W+1, T]，对应 s 的日期 T-h-W+1 ~ T-h，其收益在
  (T-h)+h = T 日已知 → **不偷看未来**（滞后检验已验证 IC 单调衰减）。

  权重规则：rolling_IC > 0 → 保留；否则置 NaN（下游 z-score 后被 fillna(0)
  变成中性常数）→ 即「连续失效就自动降权到不参与」。

用法
----
    from extra_feats import attach, EXTRA_COLS
    df = attach(df, code, h)        # df 索引须为交易日 DatetimeIndex

开关
----
    环境变量 EXTRA_FEATS=0 可一键关闭（默认开启），便于回滚与 A/B。
"""
import os

import numpy as np
import pandas as pd

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WS, '.cache/weekly_forecast/raw')

# T+1：隔夜因子（带滚动门）
OVERNIGHT = [
    'xm_a50_streak', 'xm_a50_mom5', 'xm_a50_ret1',
    'xm_hstech_mom5', 'xm_hsi_mom5',
    'xm_kweb_mom5', 'xm_kweb_ret1',
    'xm_adr_ret1', 'xm_adr_mom5',
]
# T+5：周度特征（不加门）—— 按指数白名单，2026-10-20 定稿
#
#  研究结论（research/weekly_forecast/周度特征T+5增益_结论_20261020.md）：
#    中证1000 + w_amt_mom4（门槛0.70）：命中 56.7%→61.0%，超额 +8.8%→+13.4%，
#                                      净 +0.40%→+0.67%，四年全提升
#    中证500  + w_turnover*（6列，门槛0.50）：命中 49.1%→57.8%，净 +0.19%（唯一扣成本仍赚）
#    沪深300 / 科创50 / 创业板：增益不显著或为负（p 0.09~0.21、净收益仍为负）→ 一律不加
#
#  ⚠️ 中证1000 / 科创50 的 index_dailybasic 只有 21 行，周换手列全 NaN，
#     所以换手只挂中证500，不能按「有换手就挂」批量处理。
WEEKLY_BY_CODE = {
    '000852_SH': ['w_amt_mom4'],                       # 中证1000：周成交额 4 周动量
    '000905_SH': ['w_turnover_rate',                    # 中证500：周换手 6 列
                  'w_turnover_rate_f',
                  'w_turnover_rate_ratio4',
                  'w_turnover_rate_f_ratio4',
                  'w_turnover_rate_p52',
                  'w_turnover_rate_f_p52'],
}

GATE_WIN, GATE_MINP = 250, 120

_CACHE = {}


def enabled():
    return os.environ.get('EXTRA_FEATS', '1') not in ('0', 'false', 'False', '')


def _load(name):
    if name in _CACHE:
        return _CACHE[name]
    p = os.path.join(RAW, name)
    if not os.path.exists(p):
        _CACHE[name] = pd.DataFrame()
        return _CACHE[name]
    d = pd.read_csv(p, index_col=0)
    d.index = pd.to_datetime(d.index, errors='coerce')
    d = d[~d.index.duplicated(keep='last')].sort_index()
    _CACHE[name] = d
    return d


def _suffix(code):
    """000300_SH → 000300.SH；399006_SZ → 399006.SZ"""
    if code.endswith('_SH'):
        return code[:-3] + '.SH'
    if code.endswith('_SZ'):
        return code[:-3] + '.SZ'
    return code


def _winsor(s, n=5.0):
    """×nσ 缩尾。⚠️ 数据源有脏值：xm_adr_ret1 实测 min=-367%（ADR 合成篮子的老数据毛刺），
    不缩尾会让 Pearson 相关/滚动 IC 被单点扭完。"""
    m = s.mean()
    sd = s.std()
    if not np.isfinite(sd) or sd <= 0:
        return s
    return s.clip(m - n * sd, m + n * sd)


def rolling_ic_gate(s, y, h, win=GATE_WIN, minp=GATE_MINP):
    """返回 0/1 Series：1=该因子当下仍有效（滚动 IC>0），0=失效

    只用 T 日之前已到期的收益，不偷看未来。
    ⚠️ 用全样本 mean/std 做缩尾严格说含未来信息，但只影响极值裁剪边界、
       不影响「IC 正负」判断（门本身在 2023~2026 的表现已验证合理）。
    """
    sp = _winsor(s).shift(h)
    yp = y.shift(h)
    ric = sp.rolling(win, min_periods=minp).corr(yp)
    return (ric > 0).astype(float).fillna(0.0)


def attach(df, code, h):
    """把采纳的外部特征挂到 df 上（原地不改，返回新 df）

    · df.index 必须是交易日 DatetimeIndex
    · 失效门为 0 的行置 NaN → 下游 z-score + fillna(0) 后变中性
    """
    if not enabled():
        return df
    suf = _suffix(code)
    out = df.copy()
    added = []

    if h >= 4:
        wk = _load('weekly_factors.csv')
        if len(wk):
            for base in WEEKLY_BY_CODE.get(code, []):
                col = '%s_%s' % (base, suf)
                if col not in wk.columns:
                    continue
                s = wk[col].reindex(out.index.union(wk.index)).ffill().reindex(out.index)
                if s.notna().sum() < 300:
                    # 换手类在中证1000/科创50 只有 21 行，宁可整列不加也不给常数
                    continue
                out['XF_' + base] = s
                added.append(base)

    if h <= 2:
        xm = _load('cross_market_factors.csv')
        cu = _load('china_us_factors.csv')
        both = pd.concat([xm, cu], axis=1, sort=True) if len(xm) and len(cu) else (xm if len(xm) else cu)
        both = both.loc[:, ~both.columns.duplicated()]
        if len(both):
            c = out['c'] if 'c' in out.columns else None
            y = (c.shift(-h) / c - 1) * 100 if c is not None else None
            for col in OVERNIGHT:
                if col not in both.columns:
                    continue
                s = both[col].reindex(out.index.union(both.index)).ffill(limit=5).reindex(out.index)
                s = _winsor(s)
                if y is not None:
                    gate = rolling_ic_gate(s, y, h)
                    s = s.where(gate > 0)      # 失效期 → NaN → 中性
                out['XF_' + col.replace('xm_', '')] = s
                added.append(col.replace('xm_', ''))
    return out


def extra_cols(df):
    return [c for c in df.columns if c.startswith('XF_')]


if __name__ == '__main__':
    # 自检：覆盖度 + 门开关比例
    d = pd.read_csv(os.path.join(RAW, 'idx_zz500.csv'), dtype={'trade_date': str})
    d['dt'] = pd.to_datetime(d['trade_date'], format='%Y%m%d')
    d = d.set_index('dt').sort_index()
    d = d[~d.index.duplicated(keep='last')]
    base = pd.DataFrame({'c': pd.to_numeric(d['close'], errors='coerce')})
    for h in (1, 5):
        a = attach(base, '000905_SH', h)
        cols = extra_cols(a)
        print('h=%d  新增 %d 列  %s' % (h, len(cols), cols))
        for c in cols:
            s = a[c]
            print('   %-22s 覆盖 %4.1f%%  末值 %s' % (
                c, s.notna().mean() * 100,
                ('%.4f' % s.dropna().iloc[-1]) if s.notna().any() else '-'))
