#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周期阶段分析模块 - 从历史数据回溯周期切换时间点
"""

import sqlite3
import pandas as pd
import numpy as np
import os

def get_db_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', 'data', 'cycle.db')

def classify_credit_phase(c001_slope, c002_slope):
    """根据社融和M2的斜率判断信贷周期阶段"""
    if pd.isna(c001_slope) or pd.isna(c002_slope):
        return None
    c001_rising = c001_slope > 0
    c002_rising = c002_slope > 0
    if c001_rising and c002_rising: return '信贷扩张期'
    if not c001_rising and c002_rising: return '政策宽松期'
    if not c001_rising and not c002_rising: return '信贷收缩期'
    if c001_rising and not c002_rising: return '政策收紧期'

def classify_inventory_phase(inv_slope, rev_slope, inv_level=None, rev_level=None):
    """根据产成品存货同比和营收同比的斜率判断库存周期阶段
    I009=产成品存货同比, I010=营业收入同比（原始指标）
    如果I009/I010不足，回退到I004(PPI)+I008(工业增加值)代理

    【2026-11-05 修正】补库区间必须再分质量，只看斜率符号会把滞胀式累库误判成主动补库：
      - 库存↑ + 需求↑，但库存同比 > 需求同比  → 货堆在仓库卖不动 → 被动补库存（滞胀式累库）
      - 库存↑ + 需求↑，且需求同比 > 库存同比  → 卖得好才补货   → 主动补库存（健康补库）
    经典教科书里"被动补库存"是需求↓+库存↑；但当需求只是走平（斜率微正）、
    库存还在快速累积时，本质同样是"被动累库"，故并入被动补库存，并在报告里注明细分口径。
    """
    if pd.isna(inv_slope) or pd.isna(rev_slope):
        return None
    inv_rising = inv_slope > 0
    rev_rising = rev_slope > 0
    if rev_rising and inv_rising:
        # 补库质量细分：库存增速持续高于需求增速 = 滞胀式累库
        if (inv_level is not None and rev_level is not None
                and not pd.isna(inv_level) and not pd.isna(rev_level)
                and inv_level > rev_level):
            return '被动补库存'
        return '主动补库存'
    if not rev_rising and inv_rising: return '被动补库存'
    if not rev_rising and not inv_rising: return '主动去库存'
    if rev_rising and not inv_rising: return '被动去库存'

def shift_month(date_str, delta=1):
    """将YYYY-MM格式的日期平移N个月（正数=后移，负数=前移）"""
    year, month = map(int, date_str.split('-'))
    month += delta
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f'{year:04d}-{month:02d}'

def compute_phase_transitions(phases_series, lag_correction=-1):
    """从阶段序列中提取切换点，返回 [(date, phase, duration_months), ...]
    
    lag_correction: 滞后修正月数，默认-1（将检测到的切换点前移1个月）。
    MA6+diff3方法检测趋势转向时天然有约1个月滞后，需要修正。
    """
    transitions = []
    prev_phase = None
    phase_start = None
    
    for date, phase in phases_series.items():
        if phase is None:
            continue
        try:
            if pd.isna(phase):
                continue
        except (TypeError, ValueError):
            pass
        if phase != prev_phase:
            if prev_phase is not None and phase_start is not None:
                # 计算上一阶段持续月数
                months = (date - phase_start).days // 30
                corrected_start = shift_month(phase_start.strftime('%Y-%m'), lag_correction)
                transitions.append((corrected_start, prev_phase, months))
            phase_start = date
            prev_phase = phase
    
    # 最后一个阶段持续到「最后一个有数据的月份」（不能用now，否则数据断更后时长会虚增）
    if prev_phase is not None and phase_start is not None:
        valid = phases_series.dropna()
        last_date = valid.index.max() if len(valid) else pd.Timestamp.now()
        months = max(0, (last_date - phase_start).days // 30)
        corrected_start = shift_month(phase_start.strftime('%Y-%m'), lag_correction)
        transitions.append((corrected_start, prev_phase, months))
    
    return transitions

def analyze_cycles(db_path=None):
    """分析信贷周期和库存周期的历史阶段
    
    方法论（与Wind/券商一致）：
    - 库存周期：看同比值的边际变化方向（趋势判断），非绝对水平正负
    - 有I009/I010的月份：用逐月边际变化方向判断（当月vs上月）
    - I009/I010之前的月份：用I004(PPI)+I008(工业增加值)代理+MA6+diff3回溯
    - 两段合并：I009/I010覆盖的区间覆盖代理指标
    """
    if db_path is None:
        db_path = get_db_path()
    
    conn = sqlite3.connect(db_path)
    
    # === 信贷周期 ===
    # 口径说明（2026-11-05修正）：
    #   主口径 = C003 人民币贷款余额同比 + C002 M2同比（两者均为「存量同比」，量纲一致、波动合理）
    #   旧口径 C001 实为「社融滚动12月增量的同比」，波动区间 -10%~+16%，噪音过大，降级为辅助展示
    df_c003 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='C003' AND value IS NOT NULL ORDER BY date", conn)
    df_c002 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='C002' AND value IS NOT NULL ORDER BY date", conn)

    df_credit = pd.merge(df_c003, df_c002, on='date', suffixes=('_c001', '_c002'))
    df_credit['date'] = pd.to_datetime(df_credit['date'])
    df_credit = df_credit.sort_values('date')

    if len(df_credit) < 8:
        # 回退到旧口径
        df_c001 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='C001' AND value IS NOT NULL ORDER BY date", conn)
        df_credit = pd.merge(df_c001, df_c002, on='date', suffixes=('_c001', '_c002'))
        df_credit['date'] = pd.to_datetime(df_credit['date'])
        df_credit = df_credit.sort_values('date')

    # 6月均线 + 3月差分作为趋势判断
    df_credit['c001_ma6'] = df_credit['value_c001'].rolling(6, min_periods=4).mean()
    df_credit['c002_ma6'] = df_credit['value_c002'].rolling(6, min_periods=4).mean()
    df_credit['c001_slope'] = df_credit['c001_ma6'] - df_credit['c001_ma6'].shift(3)
    df_credit['c002_slope'] = df_credit['c002_ma6'] - df_credit['c002_ma6'].shift(3)

    df_credit['phase'] = df_credit.apply(
        lambda r: classify_credit_phase(r['c001_slope'], r['c002_slope']), axis=1
    )

    credit_phases = df_credit.set_index('date')['phase']
    credit_transitions = compute_phase_transitions(credit_phases, lag_correction=-1)
    
    # === 库存周期 ===
    # 策略：I009/I010逐月趋势判断覆盖 + PPI+IVA代理补充历史
    df_i009 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I009' AND value IS NOT NULL ORDER BY date", conn)
    df_i010 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I010' AND value IS NOT NULL ORDER BY date", conn)
    
    # Step 1: 用PPI+IVA代理构建完整历史序列
    df_ppi = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I004' ORDER BY date", conn)
    df_iva = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I008' ORDER BY date", conn)
    df_inv = pd.merge(df_ppi, df_iva, on='date', suffixes=('_inv', '_rev'))
    df_inv['date'] = pd.to_datetime(df_inv['date'])
    df_inv = df_inv.sort_values('date')
    df_inv['inv_ma6'] = df_inv['value_inv'].rolling(6, min_periods=4).mean()
    df_inv['rev_ma6'] = df_inv['value_rev'].rolling(6, min_periods=4).mean()
    df_inv['inv_slope'] = df_inv['inv_ma6'] - df_inv['inv_ma6'].shift(3)
    df_inv['rev_slope'] = df_inv['rev_ma6'] - df_inv['rev_ma6'].shift(3)
    df_inv['phase'] = df_inv.apply(
        lambda r: classify_inventory_phase(r['inv_slope'], r['rev_slope']), axis=1
    )
    
    # Step 2: 用I009+I010的逐月边际变化方向覆盖代理指标
    # 逐月判断：当月vs上月的变动方向（趋势判断法，与Wind/券商一致）
    primary_phases = {}   # 统计局同比口径已判定的月份，Step 3 的 PMI 不得覆盖
    if len(df_i009) >= 2 and len(df_i010) >= 2:
        df_primary = pd.merge(df_i009, df_i010, on='date', suffixes=('_inv', '_rev'))
        df_primary['date'] = pd.to_datetime(df_primary['date'])
        df_primary = df_primary.sort_values('date').reset_index(drop=True)
        
        # 计算逐月边际变化方向
        # 【2026-11-05 修正】改用 MA3 平滑后再差分：统计局同比受春节和基数扰动，
        # 单月 diff 噪音极大（2026-02→03 库存 6.6→5.2 判"主动去库存"，
        # 次月又 6.7 翻回"被动补库存"，一个月一翻转，阶段序列完全不可用）。
        df_primary['inv_ma3'] = df_primary['value_inv'].rolling(3, min_periods=2).mean()
        df_primary['rev_ma3'] = df_primary['value_rev'].rolling(3, min_periods=2).mean()
        df_primary['inv_diff'] = df_primary['inv_ma3'].diff()  # 当月-上月
        df_primary['rev_diff'] = df_primary['rev_ma3'].diff()
        df_primary['prev_date'] = df_primary['date'].shift(1)
        # 死区：MA3 月度变化过小时方向无意义（横盘来回翻转），按走平处理
        SLOPE_DEAD = 0.10
        
        # 用边际变化方向判断阶段
        # 关键：跳过非连续月份的diff（如12月→2月缺1月，累计口径不同不可比）
        primary_phases = {}
        for _, row in df_primary.iterrows():
            if pd.isna(row['inv_diff']) or pd.isna(row['rev_diff']):
                continue
            if pd.isna(row['prev_date']):
                continue
            # 检查月份连续性：相邻两行应相差1个月（允许2月→1月跨年）
            cur_date = row['date']
            prev_date = row['prev_date']
            month_gap = (cur_date.year - prev_date.year) * 12 + (cur_date.month - prev_date.month)
            # 【2026-11-05 修正】原来只允许 gap==1，统计局缺月（2026-08 未发）会让
            # 最新的 2026-09 被整月丢弃，判定退回更早月份并被 PMI 反向覆盖成"主动去库存"。
            # 现在允许 gap=2 的跨月，按月度斜率归一化后再判方向（避免把2个月累计变化
            # 当成1个月的突变）。gap>2 的长时间断档仍视为不可比，跳过。
            if month_gap not in (1, 2):
                continue
            # 趋势判断：diff>0表示同比在上升（补库存方向），<0表示同比下降（去库存方向）
            # 【2026-11-05】同时传入当期绝对水平，用于区分"健康补库"和"滞胀式累库"
            inv_diff_n = row['inv_diff'] / month_gap
            rev_diff_n = row['rev_diff'] / month_gap
            # 死区：单边接近0视为走平（走平的需求 + 上升的库存 = 被动累库）
            if abs(inv_diff_n) < SLOPE_DEAD:
                inv_diff_n = 0.0
            if abs(rev_diff_n) < SLOPE_DEAD:
                rev_diff_n = 0.0
            if inv_diff_n == 0.0 and rev_diff_n == 0.0:
                continue   # 库存与需求双走平，沿用上一期阶段
            phase = classify_inventory_phase(
                inv_diff_n, rev_diff_n,
                inv_level=row['value_inv'], rev_level=row['value_rev']
            )
            if phase:
                primary_phases[row['date']] = phase
        
        # 覆盖df_inv中对应月份的phase
        for date, phase in primary_phases.items():
            mask = df_inv['date'] == date
            if mask.any():
                df_inv.loc[mask, 'phase'] = phase
                # 【2026-11-05 修正】同步覆盖数值，否则 df_inv 里保留的是 Step 1 的
                # PPI/工业增加值代理值，图上画出来的曲线和报告文字对不上真实存货同比。
                df_inv.loc[mask, 'value_inv'] = df_primary.loc[df_primary['date'] == date, 'value_inv'].values[0]
                df_inv.loc[mask, 'value_rev'] = df_primary.loc[df_primary['date'] == date, 'value_rev'].values[0]
            else:
                # 代理指标中没有该月份，追加
                new_row = pd.DataFrame({
                    'date': [date],
                    'value_inv': [df_primary.loc[df_primary['date']==date, 'value_inv'].values[0]],
                    'value_rev': [df_primary.loc[df_primary['date']==date, 'value_rev'].values[0]],
                    'inv_ma6': [np.nan],
                    'rev_ma6': [np.nan],
                    'inv_slope': [np.nan],
                    'rev_slope': [np.nan],
                    'phase': [phase]
                })
                df_inv = pd.concat([df_inv, new_row], ignore_index=True)
                df_inv = df_inv.sort_values('date').reset_index(drop=True)
    
    # Step 3: 用PMI(I006新订单 + I007产成品库存)覆盖最新区间（2026-11-05新增）
    # 背景：I009/I010 止于2026-03，I008止于2025-08，2026-04之后所有库存口径全部断更
    # PMI 是唯一更新到最新月份的库存周期领先指标（当前已到2026-10）
    # 映射关系：I006新订单≈需求(营收)、I007产成品库存≈存货，方向判断逻辑与I009/I010一致
    df_i006 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I006' AND value IS NOT NULL ORDER BY date", conn)
    df_i007 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I007' AND value IS NOT NULL ORDER BY date", conn)

    # 【2026-11-05 决策】PMI 不参与库存周期主阶段序列，默认关闭。
    # 原因：PMI 是环比扩散指数（50 荣枯线），统计局 I009/I010 是同比，两者方向可以相反
    # （同比高因去年低基数 + 环比在收缩，同时成立）。此前无条件用 PMI 覆盖，
    # 把 2026-09「库存11.6%↑/收入6.6%↑ = 被动补库存」翻成「主动去库存」，判定直接反了。
    # 统计局口径已覆盖到 2026-09，只差 1-2 个月，没必要用不同口径硬补。
    # PMI 改为在报告里单独作为「最新月动能提示」展示，不进阶段序列。
    USE_PMI_OVERRIDE = False

    if USE_PMI_OVERRIDE and len(df_i006) >= 2 and len(df_i007) >= 2:
        df_pmi = pd.merge(df_i006, df_i007, on='date', suffixes=('_rev', '_inv'))
        df_pmi['date'] = pd.to_datetime(df_pmi['date'])
        df_pmi = df_pmi.sort_values('date').reset_index(drop=True)
        # PMI 为月度环比扩散指数，单月diff噪音大（横盘时来回翻转），改用 MA3 + 3月差分
        df_pmi['rev_ma3'] = df_pmi['value_rev'].rolling(3, min_periods=2).mean()
        df_pmi['inv_ma3'] = df_pmi['value_inv'].rolling(3, min_periods=2).mean()
        df_pmi['rev_diff'] = df_pmi['rev_ma3'] - df_pmi['rev_ma3'].shift(3)
        df_pmi['inv_diff'] = df_pmi['inv_ma3'] - df_pmi['inv_ma3'].shift(3)
        df_pmi['prev_date'] = df_pmi['date'].shift(3)

        pmi_phases = {}
        for _, row in df_pmi.iterrows():
            if pd.isna(row['inv_diff']) or pd.isna(row['rev_diff']) or pd.isna(row['prev_date']):
                continue
            month_gap = (row['date'].year - row['prev_date'].year) * 12 + (row['date'].month - row['prev_date'].month)
            if month_gap != 3:   # 与 shift(3) 的3月差分保持一致
                continue
            # 死区：PMI 变化幅度过小时方向无意义（横盘期会来回翻转），沿用上一期阶段
            if abs(row['inv_diff']) < 0.15 or abs(row['rev_diff']) < 0.15:
                if pmi_phases:
                    pmi_phases[row['date']] = list(pmi_phases.values())[-1]
                continue
            phase = classify_inventory_phase(row['inv_diff'], row['rev_diff'])
            if phase:
                pmi_phases[row['date']] = phase

        # 【2026-11-05 修复】PMI 是环比扩散指数（50荣枯线），统计局是同比，两者方向可以相反
        # （同比高因去年低基数 + 环比在收缩同时成立）。原来无条件覆盖，导致统计局同比
        # 已判定的月份被 PMI 反向改掉，2026-09 库存11.6%↑/收入6.6%↑ 被翻成"主动去库存"。
        # 现在只允许 PMI 补统计局确实缺失的月份，已有同比判定的月份不动。
        for date, phase in pmi_phases.items():
            if date in primary_phases:
                continue
            mask = df_inv['date'] == date
            if mask.any():
                df_inv.loc[mask, 'phase'] = phase
            else:
                new_row = pd.DataFrame({
                    'date': [date],
                    'value_inv': [df_pmi.loc[df_pmi['date'] == date, 'value_inv'].values[0]],
                    'value_rev': [df_pmi.loc[df_pmi['date'] == date, 'value_rev'].values[0]],
                    'inv_ma6': [np.nan], 'rev_ma6': [np.nan],
                    'inv_slope': [np.nan], 'rev_slope': [np.nan],
                    'phase': [phase]
                })
                df_inv = pd.concat([df_inv, new_row], ignore_index=True)
        df_inv = df_inv.sort_values('date').reset_index(drop=True)

    inv_phases = df_inv.set_index('date')['phase']
    # 库存周期趋势判断无滞后，不需要lag_correction
    inv_transitions = compute_phase_transitions(inv_phases, lag_correction=0)
    
    conn.close()
    
    return credit_transitions, inv_transitions, df_credit, df_inv

def find_current_cycle(credit_transitions, inv_transitions):
    """找到当前轮次的周期起始时间"""
    # 信贷周期：一个完整轮转 = 扩张→收紧→收缩→宽松
    credit_cycle_order = ['信贷扩张期', '政策收紧期', '信贷收缩期', '政策宽松期']
    # 库存周期：一个完整轮转 = 主动补→被动补→主动去→被动去
    inv_cycle_order = ['主动补库存', '被动补库存', '主动去库存', '被动去库存']
    
    # 找当前阶段在轮转中的位置
    current_credit = credit_transitions[-1][1] if credit_transitions else '未知'
    current_inv = inv_transitions[-1][1] if inv_transitions else '未知'
    
    # 从后往前找当前轮次的起始
    credit_cycle_start = None
    if current_credit in credit_cycle_order:
        idx = credit_cycle_order.index(current_credit)
        # 当前阶段之前的阶段应该按轮转顺序
        target_start = credit_cycle_order[0]  # 扩张期为起点
        for i in range(len(credit_transitions)-1, -1, -1):
            if credit_transitions[i][1] == target_start:
                credit_cycle_start = credit_transitions[i][0]
                break
    
    inv_cycle_start = None
    if current_inv in inv_cycle_order:
        target_start = inv_cycle_order[0]  # 主动补库存为起点
        for i in range(len(inv_transitions)-1, -1, -1):
            if inv_transitions[i][1] == target_start:
                inv_cycle_start = inv_transitions[i][0]
                break
    
    return {
        'credit_phase': current_credit,
        'credit_phase_start': credit_transitions[-1][0] if credit_transitions else None,
        'credit_phase_duration': credit_transitions[-1][2] if credit_transitions else 0,
        'credit_cycle_start': credit_cycle_start,
        'inv_phase': current_inv,
        'inv_phase_start': inv_transitions[-1][0] if inv_transitions else None,
        'inv_phase_duration': inv_transitions[-1][2] if inv_transitions else 0,
        'inv_cycle_start': inv_cycle_start,
    }

if __name__ == '__main__':
    ct, it, _, _ = analyze_cycles()
    
    print("=== 信贷周期阶段切换 ===")
    for t in ct[-10:]:
        print(f"  {t[0]}: {t[1]} ({t[2]}月)")
    
    print("\n=== 库存周期阶段切换 ===")
    for t in it[-10:]:
        print(f"  {t[0]}: {t[1]} ({t[2]}月)")
    
    info = find_current_cycle(ct, it)
    print(f"\n=== 当前状态 ===")
    print(f"信贷: {info['credit_phase']}, 阶段始于{info['credit_phase_start']}, 已持续{info['credit_phase_duration']}月, 本轮始于{info['credit_cycle_start']}")
    print(f"库存: {info['inv_phase']}, 阶段始于{info['inv_phase_start']}, 已持续{info['inv_phase_duration']}月, 本轮始于{info['inv_cycle_start']}")
