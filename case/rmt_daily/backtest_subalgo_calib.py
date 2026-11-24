#!/usr/bin/env python3
"""
物理几何子算法 - 阈值自适应校准版 (分位数校准)
==============================================
照抄线上 classify_state 阈值的一版 (backtest_subalgo.py) 在 20标的沪深300 样本上
大面积失真 (J/xi/excess/Betti/Ricci/D/H 恒单侧)。本脚本对数值型特征用样本内
分位数 q25/q75 校准三档边界 (方向保持原语义), 分类特征 (manifold/morse) 与
M (Ising磁化, 天然±1对称有方向) 保持原规则, 使每个子算法都具备可比择时区分度。
输出 backtest_subalgo_calib.pkl
"""
import os, sys, warnings, pickle
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)
from backtest_subalgo import (ising_M, coupling_J, corr_len, msf_calc, rmt_calc,
                              pca_manifold, betti, ricci, morse, higuchi, hurst,
                              classify_state, WINDOW)

d = pickle.load(open(os.path.join(WORK, 'backtest_subalgo.pkl'), 'rb'))
feat = d['features']
dates = feat.index

# 方向定义: 每个特征 "看涨端" 的方向 -> 用分位数校准
#  high_good=True: 特征高=看涨(q75->满仓, q25->40%); high_good=False: 特征低=看涨
NUMERIC_DIR = {
    'J':    ('high_good', False),   # 高耦合群聚 -> 看跌
    'xi':   ('high_good', False),   # 高关联长度系统性 -> 看跌
    'T':    ('high_good', False),   # 高温度无序 -> 看跌
    'MSF':  ('high_good', True),    # 高微观稳定 -> 看涨
    'excess': ('high_good', False), # 谱超过RMT预测 -> 共同因子过强? 高=风险
    'betti_1': ('high_good', False),# 假设: 环路多=结构复杂分歧大 -> 看跌 (线上无打分, 经验校准)
    'ricci': ('high_good', True),   # 高相关均值(曲率+): 市场一致上行 -> 看涨
    'D':    ('high_good', False),   # 高乱度 -> 看跌
    'H':    ('high_good', True),    # 高趋势强度 -> 看涨
}
# 保持原语义规则的特征
KEEP_RULE = {'M': 'pg_M_magnet', 'manifold': 'pg_PCA_manifold', 'morse': 'pg_Morse_shape'}

def calib_pos(s, high_good):
    q25, q75 = s.quantile(0.25), s.quantile(0.75)
    if high_good:
        return np.where(s > q75, 1.0, np.where(s < q25, 0.40, 0.75))
    return np.where(s < q25, 1.0, np.where(s > q75, 0.40, 0.75))

sub_cal = pd.DataFrame(index=dates)
for col, (_, high_good) in NUMERIC_DIR.items():
    sub_cal['pg_cal_' + col] = calib_pos(feat[col].astype(float), high_good)
# M 保持线上规则
M = feat['M'].fillna(0.0).values
sub_cal['pg_cal_M'] = np.where(M > 0.3, 1.0, np.where(M <= -0.3, 0.40, 0.75))
# manifold / morse 保持分类规则
mf = feat['manifold'].fillna('未知')
sub_cal['pg_cal_manifold'] = np.where(mf.isin(['丝带', '球状']), 1.0,
                              np.where(mf == '散点', 0.40, 0.75))
mo = feat['morse'].fillna('未知')
sub_cal['pg_cal_morse'] = np.where(mo.isin(['上升坡面', '山谷']), 1.0,
                           np.where(mo.isin(['山顶', '下降坡面']), 0.40, 0.75))

with open(os.path.join(WORK, 'backtest_subalgo_calib.pkl'), 'wb') as fh:
    pickle.dump({'sub_pos_calib': sub_cal}, fh)
print('已保存 backtest_subalgo_calib.pkl, 平均仓位:')
print((sub_cal.mean() * 100).round(1).to_string())
print('\n各校准子算法档位天数 (100/75/40):')
for c in sub_cal.columns:
    vc = sub_cal[c].value_counts().reindex([1.0, 0.75, 0.4]).fillna(0).astype(int)
    print(f'{c:22s} 满仓{vc[1.0]:5d} 中性{vc[0.75]:5d} 空仓{vc[0.4]:5d}')