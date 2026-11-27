# -*- coding: utf-8 -*-
"""SHAP 解释模块：为 GBDT 三分类信号给出「为什么看涨/看跌」

对 score = P(涨) − P(跌) 做精确分解：
  score = base_score + Σ SHAP_i
  其中 SHAP_i 用 TreeExplainer 对 class2(涨) 与 class0(跌) 的概率贡献相减
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd


def shap_explain(model, X_row, feature_names, topk=6):
    """返回 (base_margin, contribs)

    ⚠️ SHAP 解释的是 **raw margin (logit)** 空间：
        margin_diff = margin(涨) - margin(跌) = base + Σ shap_i
    而 score = P(涨) - P(跌) 是 softmax 后的概率差，与 margin_diff **单调但非线性**。
    所以校验要对 margin_diff，不是对 score。
    """
    try:
        import shap
    except ImportError:
        return None, None
    try:
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X_row)
        bv = explainer.expected_value
    except Exception:
        return None, None

    if isinstance(sv, list):
        s_up = np.asarray(sv[2]).reshape(-1)
        s_dn = np.asarray(sv[0]).reshape(-1)
        b = np.asarray(bv).reshape(-1)
        base = float(b[2] - b[0]) if b.size >= 3 else 0.0
    else:
        arr = np.asarray(sv)
        if arr.ndim == 3:
            s_up = arr[0, :, 2]
            s_dn = arr[0, :, 0]
            b = np.asarray(bv).reshape(-1)
            base = float(b[2] - b[0]) if b.size >= 3 else 0.0
        else:
            s_up = arr.reshape(-1)
            s_dn = np.zeros_like(s_up)
            b = np.asarray(bv).reshape(-1)
            base = float(b[0]) if b.size else 0.0

    contrib = s_up - s_dn          # 对 score 的净贡献（正=推涨，负=推跌）
    df = pd.DataFrame({'feature': feature_names,
                       'shap': contrib,
                       'value': np.asarray(X_row).reshape(-1)})
    df['abs'] = df['shap'].abs()
    df = df.sort_values('abs', ascending=False).drop(columns='abs').reset_index(drop=True)
    return base, df


# 特征中文名映射（覆盖常见前缀）
NAME_MAP = {
    'MS_ret': '多尺度动量', 'MS_vol': '多尺度波动', 'MS_vr': '波动比',
    'MS_pct': '价格分位', 'MS_dev': '均线乖离', 'MS_rsi': 'RSI',
    'MS_agree': '动量一致性', 'MS_acc': '动量加速度', 'MS_range': '区间位置',
    'ETF_vol': 'ETF成交量', 'ETF_amt': 'ETF成交额', 'ETF_share': 'ETF份额',
    'ETF_ret': 'ETF收益',
    'pkg_vol_ratio': '量比', 'pkg_macd': 'MACD', 'pkg_rsi': 'RSI',
    'pkg_skew': '偏度', 'pkg_mom': '动量', 'pkg_ma': '均线',
    'pkg_boll': '布林', 'pkg_q50': '期权Q50', 'pkg_q300': '期权Q300',
    'pkg_510050': '50ETF期权', 'pkg_510300': '300ETF期权',
    'pkg_f_cpi': 'CPI', 'pkg_f_pmi': 'PMI', 'pkg_f_ir': '利率',
    'pkg_us_y': '美债收益率', 'pkg_us_vix': 'VIX', 'pkg_us_msft': '美股',
    'pkg_m_rz': '融资余额', 'pkg_m_mom': '融资动量',
    'orth_ind_breadth': '行业宽度', 'orth_ind_disp': '行业离散度',
    'orth_ind_rotation': '行业轮动', 'orth_ind_mom': '行业动量',
    'orth_ind_ew_mom': '行业等权动量', 'orth_ind_hhi': '行业集中度',
    'orth_shibor': 'SHIBOR', 'orth_elec': '用电量',
    'orth_m': '中票利差', 'orth_cb': '信用债',
    'close': '收盘价', 'vol': '成交量', 'amount': '成交额',
    'pct_chg': '涨跌幅', 'high': '最高价', 'low': '最低价', 'open': '开盘价',
}


def cn_name(f):
    """把因子名翻成可读中文（保留后缀数字）"""
    best = None
    for k, v in NAME_MAP.items():
        if f.startswith(k) and (best is None or len(k) > len(best[0])):
            best = (k, v)
    if best:
        suffix = f[len(best[0]):].lstrip('_')
        return best[1] + (('·' + suffix) if suffix else '')
    return f


def format_reasons(df, score, topk=6):
    """把 SHAP 结果排成「看涨原因 / 看跌原因」两行文字"""
    up = df[df['shap'] > 0].head(topk)
    dn = df[df['shap'] < 0].head(topk)
    up_txt = '、'.join('%s(%+.3f)' % (cn_name(r.feature), r.shap) for r in up.itertuples()) or '无'
    dn_txt = '、'.join('%s(%+.3f)' % (cn_name(r.feature), r.shap) for r in dn.itertuples()) or '无'
    return up_txt, dn_txt
