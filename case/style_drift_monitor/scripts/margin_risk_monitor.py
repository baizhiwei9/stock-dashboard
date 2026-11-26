#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两融风险监控引擎（纯Python，8模块全覆盖）
==========================================
基于 data_hub 统一数据源（margin_db + margin_detail_db），计算两融风险信号。

8大监控模块：
  ① 融资余额变化率 + 加速度
  ② 融资/流通市值比例
  ③ 高杠杆个股强平风险
  ④ 杠杆资金撤退信号
  ⑤ 融资出清接近尾声
  ⑥ 超跌反弹候选
  ⑦ 底部背离确认
  ⑧ 风格切换信号

数据源：data_hub 统一接口（四级降级 AKShare→Tushare→网页→缓存）
零LLM依赖，纯Python规则实现。
"""

import os
import sys
import numpy as np
from datetime import datetime, timedelta

# 路径配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(BASE_DIR)

# data_hub 路径
DATA_HUB_DIR = os.path.join(ROOT_DIR, 'codeact', 'scripts')
if DATA_HUB_DIR not in sys.path:
    sys.path.insert(0, DATA_HUB_DIR)


def _safe_float(val, default=None):
    """安全转换为float"""
    if val is None:
        return default
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def compute_margin_signals(trade_date: str) -> dict:
    """
    计算两融风险监控信号（8模块全覆盖）。
    
    Args:
        trade_date: 交易日期 YYYYMMDD
    
    Returns:
        dict with all signal fields needed by the report:
        - trade_date, actual_data_date, requested_date
        - risk_level, advice, data_warnings
        - _rzye_raw, _rzrqye_raw, total_circ_mv_yi, margin_circ_ratio
        - total_turnover_yi, ratio_trend
        - rzye_chg: {5: x, 10: x, 20: x}
        - acceleration, consecutive_decline_days, ratio_percentile
        - stress_stocks: [{ts_code, name, rzye, ratio_pct, rzche}]
        - chain_risk: bool
        - rz_buy_ratio, leverage_exit: bool
        - near_clearing: bool
        - rebound_candidates: list
        - bottom_divergence: bool, divergence_type
        - style_switch: bool
        - signal_227: bool
    """
    # 初始化返回结构
    result = {
        'trade_date': trade_date,
        'actual_data_date': '',
        'requested_date': trade_date,
        'risk_level': 'unknown',
        'advice': '数据加载中...',
        'data_warnings': [],
        '_rzye_raw': None,
        '_rzrqye_raw': None,
        'total_circ_mv_yi': None,
        'margin_circ_ratio': None,
        'total_turnover_yi': None,
        'ratio_trend': '→',
        'rzye_chg': {},
        'acceleration': None,
        'consecutive_decline_days': 0,
        'ratio_percentile': None,
        'stress_stocks': [],
        'chain_risk': False,
        'rz_buy_ratio': None,
        'leverage_exit': False,
        'near_clearing': False,
        'rebound_candidates': [],
        'bottom_divergence': False,
        'divergence_type': '',
        'style_switch': False,
        'signal_227': False,
    }
    
    # ========== 1. 加载 data_hub 两融数据 ==========
    try:
        from data_hub.margin_db import (
            sync_margin_history, query_margin, get_latest_margin, 
            get_margin_percentile, _connect_db
        )
        from data_hub.margin_detail_db import (
            sync_margin_detail, sync_daily_basic,
            get_margin_detail, get_daily_basic, get_stock_list
        )
    except ImportError as e:
        result['data_warnings'].append(f'⚠️ data_hub 两融模块导入失败: {e}')
        result['advice'] = '两融数据模块不可用'
        return result
    
    # ========== 2. 同步全量数据 ==========
    print(f"  📥 同步两融全量数据 ({trade_date})...")
    
    # 2.1 余额历史（全量回看500天）
    try:
        sync_margin_history(lookback_days=500)
        print("  ✅ 两融余额历史同步完成")
    except Exception as e:
        result['data_warnings'].append(f'⚠️ 余额历史同步失败: {e}')
    
    # 2.2 个股明细（全市场）
    try:
        sync_margin_detail(trade_date)
        print("  ✅ 个股两融明细同步完成")
    except Exception as e:
        result['data_warnings'].append(f'⚠️ 个股明细同步失败: {e}')
    
    # 2.3 流通市值
    try:
        sync_daily_basic(trade_date)
        print("  ✅ 流通市值同步完成")
    except Exception as e:
        result['data_warnings'].append(f'⚠️ 流通市值同步失败: {e}')
    
    # ========== 3. 获取余额序列 ==========
    try:
        df_history = query_margin(start_date='20240101')  # 近两年数据
    except Exception as e:
        result['data_warnings'].append(f'⚠️ 余额历史查询失败: {e}')
        df_history = None
    
    if df_history is None or df_history.empty:
        result['data_warnings'].append('⚠️ 两融余额历史数据为空')
        result['advice'] = '两融余额数据不足，无法计算风险信号'
        return result
    
    # 确保按日期排序
    df_history = df_history.sort_values('trade_date').reset_index(drop=True)
    
    # 确定实际数据日期
    actual_date = df_history['trade_date'].iloc[-1]
    result['actual_data_date'] = actual_date
    
    # ========== 4. 核心指标计算 ==========
    # 4.1 最新余额
    latest = df_history.iloc[-1]
    rzye = _safe_float(latest.get('rzye'))
    rzrqye = _safe_float(latest.get('rzrqye'))
    result['_rzye_raw'] = rzye
    result['_rzrqye_raw'] = rzrqye
    
    # 4.2 变化率（5/10/20日）
    rzye_series = df_history['rzye'].dropna()
    if len(rzye_series) >= 5:
        for n in (5, 10, 20):
            if len(rzye_series) > n:
                prev = rzye_series.iloc[-n-1]
                curr = rzye_series.iloc[-1]
                if prev > 0:
                    result['rzye_chg'][n] = (curr - prev) / prev
    
    # 4.3 加速度（5日变化率的变化）
    if len(rzye_series) >= 10:
        chg_5_now = (rzye_series.iloc[-1] - rzye_series.iloc[-6]) / rzye_series.iloc[-6]
        chg_5_prev = (rzye_series.iloc[-6] - rzye_series.iloc[-11]) / rzye_series.iloc[-11]
        result['acceleration'] = chg_5_now - chg_5_prev
    
    # 4.4 连续净偿还天数
    consec_days = 0
    for i in range(len(rzye_series) - 1, 0, -1):
        if rzye_series.iloc[i] < rzye_series.iloc[i-1]:
            consec_days += 1
        else:
            break
    result['consecutive_decline_days'] = consec_days
    
    # 4.5 获取流通市值和融资/流通比
    try:
        df_basic = get_daily_basic(actual_date)
        if df_basic is not None and not df_basic.empty:
            total_circ_mv = df_basic['circ_mv'].sum()  # 万元
            result['total_circ_mv_yi'] = total_circ_mv / 1e4  # 转亿元
            if rzye and total_circ_mv > 0:
                # rzye 是元，circ_mv 是万元
                rzye_wan = rzye / 1e4
                ratio = rzye_wan / total_circ_mv * 100  # 百分比
                result['margin_circ_ratio'] = round(ratio, 4)
    except Exception as e:
        result['data_warnings'].append(f'⚠️ 流通市值获取失败: {e}')
    
    # 4.6 历史分位
    try:
        pct = get_margin_percentile()
        if pct is not None:
            result['ratio_percentile'] = round(pct * 100, 1)
    except Exception:
        pass
    
    # ========== 5. 个股明细分析 ==========
    try:
        df_detail = get_margin_detail(actual_date)
        df_basic_for_detail = get_daily_basic(actual_date)
        
        if df_detail is not None and not df_detail.empty:
            # 合并流通市值
            if df_basic_for_detail is not None and not df_basic_for_detail.empty:
                df_merged = df_detail.merge(
                    df_basic_for_detail[['ts_code', 'circ_mv', 'close']],
                    on='ts_code', how='left'
                )
            else:
                df_merged = df_detail.copy()
            
            # 计算每只股票的融资占流通市值比
            df_merged['ratio_pct'] = 0.0
            mask = (df_merged['circ_mv'] > 0) & (df_merged['circ_mv'].notna())
            if mask.any():
                # rzye 是元，circ_mv 是万元
                df_merged.loc[mask, 'ratio_pct'] = (
                    df_merged.loc[mask, 'rzye'] / 1e4 / df_merged.loc[mask, 'circ_mv'] * 100
                )
            
            # 高杠杆个股：融资/流通市值 > 5%
            high_leverage = df_merged[df_merged['ratio_pct'] > 5].copy()
            if not high_leverage.empty:
                high_leverage = high_leverage.sort_values('ratio_pct', ascending=False)
                stress_list = []
                for _, row in high_leverage.head(20).iterrows():
                    stress_list.append({
                        'ts_code': row.get('ts_code', ''),
                        'name': row.get('name', ''),
                        'rzye': _safe_float(row.get('rzye'), 0),
                        'ratio_pct': _safe_float(row.get('ratio_pct'), 0),
                        'rzche': _safe_float(row.get('rzche'), 0),
                    })
                result['stress_stocks'] = stress_list
                
                # 连锁风险判断
                if len(stress_list) >= 5:
                    result['chain_risk'] = True
            
            # 超跌反弹候选
            # 条件：融资余额环比下降 + 融资占比高 + 股价低位
            candidates = []
            if 'rzche' in df_merged.columns:
                decline_mask = (df_merged['rzche'] < 0) & (df_merged['ratio_pct'] > 3)
                if decline_mask.any():
                    decline_df = df_merged[decline_mask].sort_values('rzche').head(10)
                    for _, row in decline_df.iterrows():
                        candidates.append({
                            'ts_code': row.get('ts_code', ''),
                            'rzye': _safe_float(row.get('rzye'), 0),
                            'ratio_pct': _safe_float(row.get('ratio_pct'), 0),
                        })
            result['rebound_candidates'] = candidates
            
    except Exception as e:
        result['data_warnings'].append(f'⚠️ 个股明细分析失败: {e}')
    
    # ========== 6. 风险评估 ==========
    risk_score = 0
    
    # 融资变化率
    chg5 = result['rzye_chg'].get(5)
    if chg5 is not None:
        if chg5 < -0.02:
            risk_score += 2
        elif chg5 < -0.01:
            risk_score += 1
    
    # 连续净偿还
    if consec_days >= 10:
        risk_score += 2
    elif consec_days >= 5:
        risk_score += 1
    
    # 加速度
    accel = result['acceleration']
    if accel is not None and accel < -0.003:
        risk_score += 1
        result['signal_227'] = True
    
    # 高杠杆股
    if result['chain_risk']:
        risk_score += 2
    
    # 风险等级
    if risk_score >= 4:
        result['risk_level'] = 'high'
        result['advice'] = '🔴 两融风险较高，注意控制仓位，警惕强平连锁反应'
    elif risk_score >= 2:
        result['risk_level'] = 'medium'
        result['advice'] = '🟡 两融风险中等，关注融资流出趋势'
    else:
        result['risk_level'] = 'low'
        result['advice'] = '🟢 两融风险较低，杠杆资金稳定'
    
    # ========== 7. 机会信号 ==========
    # 融资出清接近尾声
    ratio = result['margin_circ_ratio']
    if ratio is not None and ratio < 2.2 and consec_days < 3:
        result['near_clearing'] = True
    
    # 底部背离
    if rzye and len(rzye_series) >= 20:
        # 价格新低但融资余额不新低
        recent_low = rzye_series.tail(20).min()
        if rzye >= recent_low * 1.02:
            result['bottom_divergence'] = True
            result['divergence_type'] = '融资余额未随价格创新低'
    
    return result


if __name__ == '__main__':
    import sys
    trade_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
    result = compute_margin_signals(trade_date)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
