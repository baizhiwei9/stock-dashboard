#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""特征版本标注（供 T+1 / T+5 / 周报卡片共用）

为什么要有这个
--------------
2026-10-20 大佬：「在周度任务那个报告里边改一下就行，合并一下就行。」
背景：extra_feats 现在按指数白名单挂周度特征（中证1000 挂 w_amt_mom4、
中证500 挂 6 列 w_turnover），其余三个指数不挂。但报告里看不出来
某天的信号到底用没用周度特征，于是出现两种误读：
  · 以为五个指数都加了（实际只两个）
  · 以为加了就一定更准（A/B 实测 OOS 平均只 +0.4pp，且不显著）

本模块只做一件事：把「这个指数的信号用了哪些外部特征、增益多大」变成
报告里的一行小字，带上**A/B 实测的净差值**，而不是只说「已启用」。

数据来源（不要硬编码结论）
--------------------------
.cache/wide_ens/ab_stats.json —— 由 research/weekly_forecast/wk_ab_stats.py
生成的 A/B 汇总（开/关 extra_feats 的 OOS 命中率差）。缺失时降级为
「未测」，绝不编数字。

用法
----
    from feats_note import feat_tag, feat_footer
    feat_tag('000852_SH')      -> '周度特征：w_amt_mom4'
    feat_footer('000852_SH')   -> 一行可直接塞进卡片的灰字
"""
import os
import json

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AB_JSON = os.path.join(WS, '.cache/wide_ens/ab_stats.json')

# 与 bin/wide_ens/extra_feats.py 的 WEEKLY_BY_CODE 必须一致
BY_CODE = {
    '000852_SH': ('w_amt_mom4', '周成交额4周动量'),
    '000905_SH': ('w_turnover*', '周换手6列'),
}
_CODE_CN = {'000300_SH': '沪深300', '000905_SH': '中证500', '000852_SH': '中证1000',
            '000688_SH': '科创50', '399006_SZ': '创业板'}


def _ab():
    if not os.path.exists(AB_JSON):
        return {}
    try:
        return json.load(open(AB_JSON, encoding='utf-8'))
    except Exception:
        return {}


def enabled(code):
    return code in BY_CODE


def feat_tag(code):
    """短标签：用在表格的「特征」列"""
    if code not in BY_CODE:
        return '日频基础'
    return '周度·%s' % BY_CODE[code][1]


def ab_text(code):
    """A/B 实测的 OOS 命中率差，返回 (文字, 是否显著)"""
    d = _ab().get(code)
    if not d:
        return ('未做A/B', False)
    dp = d.get('dpp_mean')
    p = d.get('p_min')
    if dp is None:
        return ('未做A/B', False)
    sig = bool(p is not None and p < 0.05)
    return ('%+.1fpp%s' % (dp, '' if sig else '（不显著）'), sig)


def feat_footer(code_all=None):
    """一行灰字，放在卡片尾部口径里"""
    parts = []
    for code in (code_all or ['000852_SH', '000905_SH']):
        if code not in BY_CODE:
            continue
        t, sig = ab_text(code)
        parts.append('%s + %s（A/B OOS %s）' % (
            _CODE_CN.get(code, code), BY_CODE[code][1], t))
    if not parts:
        return '特征版本：全部为日频基础特征（无周度外部特征）'
    other = [c for c in (code_all or []) if c not in BY_CODE]
    tail = '；其余指数为纯日频特征' if other else ''
    return ('特征版本：' + '；'.join(parts) + tail +
            '。增益为同模型同门槛开关特征后的 OOS 命中率差，样本量有限（n≈60~80），'
            '不代表每次报告都会更高。')


if __name__ == '__main__':
    for c in ['000300_SH', '000905_SH', '000852_SH', '000688_SH', '399006_SZ']:
        print('%-10s %-10s %s' % (_CODE_CN[c], feat_tag(c), ab_text(c)))
    print()
    print(feat_footer())
