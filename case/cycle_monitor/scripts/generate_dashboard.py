#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存信贷周期监控系统 - HTML仪表盘生成器
"""

import os
import sqlite3
import json
import numpy as np
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 指标配置
INDICATOR_CONFIG = {
    'C001': '社融存量同比', 'C002': 'M2同比', 'C003': '人民币贷款余额同比',
    'C004': '企业中长期贷款增量', 'C005': '居民部门贷款增量',
    'C006': '政府债券融资同比', 'C007': 'DR007月度均值', 'C008': '贷款加权平均利率',
    'I001': '工业企业产成品存货同比', 'I002': '工业企业营业收入同比',
    'I003': '工业企业利润总额同比', 'I004': 'PPI同比', 'I005': 'PPIRM同比',
    'I006': '制造业PMI新订单指数', 'I007': '制造业PMI产成品库存指数',
    'I008': '工业增加值同比',
    'I009': '产成品存货同比', 'I010': '营业收入同比',
    'V001': '社融-M2剪刀差', 'V002': 'PMI新订单-产成品库存差', 'V003': '出口同比',
}

# 周期颜色配置
CREDIT_CYCLE_COLORS = {
    '信贷扩张期': '#00C853',
    '信贷收缩期': '#FF5252',
    '信贷宽松期': '#448AFF',
    '信贷收紧期': '#FF9800',
}

INVENTORY_CYCLE_COLORS = {
    '主动补库': '#00C853',
    '被动补库': '#FF9800',
    '主动去库': '#FF5252',
    '被动去库': '#448AFF',
}

# 周期平均持续月数
CREDIT_AVG_DURATION = 18
INVENTORY_AVG_DURATION = 12


def get_db_path():
    """获取数据库路径"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', 'data', 'cycle.db')


def get_output_path():
    """获取输出路径"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', 'outputs', 'dashboard.html')


def connect_db():
    """连接数据库"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    return conn


def fetch_indicator_data(conn, indicator_id, limit_years=5):
    """获取指定指标的数据"""
    query = """
    SELECT date, value, ma3 
    FROM economic_indicators 
    WHERE indicator_id = ?
    ORDER BY date DESC
    """
    df = pd.read_sql_query(query, conn, params=(indicator_id,))
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    cutoff_date = pd.Timestamp.now() - pd.DateOffset(years=limit_years)
    df = df[df['date'] >= cutoff_date]
    return df.sort_values('date')


def fetch_all_indicators(conn, limit_years=5):
    """获取所有指标数据"""
    data = {}
    for indicator_id in INDICATOR_CONFIG.keys():
        df = fetch_indicator_data(conn, indicator_id, limit_years)
        if not df.empty:
            data[indicator_id] = df
    return data


def calculate_v001(conn, limit_years=5):
    """计算社融-M2剪刀差"""
    c001 = fetch_indicator_data(conn, 'C001', limit_years)
    c002 = fetch_indicator_data(conn, 'C002', limit_years)
    
    if c001.empty or c002.empty:
        return pd.DataFrame()
    
    merged = pd.merge(c001[['date', 'value']], c002[['date', 'value']], 
                      on='date', suffixes=('_c001', '_c002'))
    merged['value'] = merged['value_c001'] - merged['value_c002']
    merged['date'] = pd.to_datetime(merged['date'])
    merged = merged.sort_values('date')
    merged['ma3'] = merged['value'].rolling(3).mean()
    
    return merged[['date', 'value', 'ma3']]


def calculate_v002(conn, limit_years=5):
    """计算PMI新订单-产成品库存差"""
    i006 = fetch_indicator_data(conn, 'I006', limit_years)
    i007 = fetch_indicator_data(conn, 'I007', limit_years)
    
    if i006.empty or i007.empty:
        return pd.DataFrame()
    
    merged = pd.merge(i006[['date', 'value']], i007[['date', 'value']], 
                      on='date', suffixes=('_i006', '_i007'))
    merged['value'] = merged['value_i006'] - merged['value_i007']
    merged['date'] = pd.to_datetime(merged['date'])
    merged = merged.sort_values('date')
    merged['ma3'] = merged['value'].rolling(3).mean()
    
    return merged[['date', 'value', 'ma3']]


def fetch_cycle_judgments(conn):
    """获取周期判断数据"""
    query = "SELECT * FROM cycle_judgments ORDER BY date DESC"
    df = pd.read_sql_query(query, conn)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


def fetch_latest_update_time(conn):
    """获取最后更新时间"""
    query = "SELECT MAX(updated_at) as last_update FROM economic_indicators"
    df = pd.read_sql_query(query, conn)
    if not df.empty and df['last_update'].iloc[0]:
        return df['last_update'].iloc[0]
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ============================================================
# 周期阶段历史分析
# ============================================================

# 各阶段历史平均持续月数（基于2006-2026年中国数据回溯）
CREDIT_PHASE_AVG = {
    '信贷扩张期': 12, '政策收紧期': 8, '信贷收缩期': 10, '政策宽松期': 6,
}
INVENTORY_PHASE_AVG = {
    '主动补库存': 12, '被动补库存': 8, '主动去库存': 12, '被动去库存': 10,
}
# 完整周期平均
CREDIT_FULL_CYCLE_AVG = 36  # 信贷周期完整一轮约3年
INVENTORY_FULL_CYCLE_AVG = 42  # 库存周期完整一轮约42个月(3.5年)


def _classify_credit_phase(c001_slope, c002_slope):
    if pd.isna(c001_slope) or pd.isna(c002_slope):
        return None
    if c001_slope > 0 and c002_slope > 0: return '信贷扩张期'
    if c001_slope <= 0 and c002_slope > 0: return '政策宽松期'
    if c001_slope <= 0 and c002_slope <= 0: return '信贷收缩期'
    if c001_slope > 0 and c002_slope <= 0: return '政策收紧期'


def _classify_inventory_phase(inv_slope, rev_slope):
    """库存周期判断 - 产成品存货同比+营收同比"""
    if pd.isna(inv_slope) or pd.isna(rev_slope):
        return None
    if rev_slope > 0 and inv_slope > 0: return '主动补库存'
    if rev_slope <= 0 and inv_slope > 0: return '被动补库存'
    if rev_slope <= 0 and inv_slope <= 0: return '主动去库存'
    if rev_slope > 0 and inv_slope <= 0: return '被动去库存'


def _shift_month(date_str, delta=-1):
    """将YYYY-MM格式日期平移delta个月（正数后移，负数前移）"""
    year, month = map(int, date_str.split('-'))
    month += delta
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f'{year:04d}-{month:02d}'

def _compute_transitions(phases_series, lag_correction=-1):
    """从阶段序列提取切换点 → [(start_date_str, phase, duration_months), ...]
    
    lag_correction: 滞后修正月数，默认-1（将检测到的切换点前移1个月）。
    MA6+diff3方法检测趋势转向时天然有约1个月滞后，需要修正。
    """
    transitions = []
    prev_phase = None
    phase_start = None
    for date, phase in phases_series.items():
        if phase is None:
            continue
        if phase != prev_phase:
            if prev_phase is not None and phase_start is not None:
                months = max(1, round((date - phase_start).days / 30.44))
                corrected_start = _shift_month(phase_start.strftime('%Y-%m'), lag_correction)
                transitions.append((corrected_start, prev_phase, months))
            phase_start = date
            prev_phase = phase
    if prev_phase is not None and phase_start is not None:
        months = max(1, round((pd.Timestamp.now() - phase_start).days / 30.44))
        corrected_start = _shift_month(phase_start.strftime('%Y-%m'), lag_correction)
        transitions.append((corrected_start, prev_phase, months))
    return transitions


def analyze_cycle_history(conn, since='2018-01-01'):
    """从数据库回溯信贷周期和库存周期的历史阶段"""
    # ---- 信贷周期 ----
    df_c001 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='C001' ORDER BY date", conn)
    df_c002 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='C002' ORDER BY date", conn)
    df_cr = pd.merge(df_c001, df_c002, on='date', suffixes=('_c001', '_c002'))
    df_cr['date'] = pd.to_datetime(df_cr['date'])
    df_cr = df_cr.sort_values('date')
    df_cr['c001_ma6'] = df_cr['value_c001'].rolling(6, min_periods=4).mean()
    df_cr['c002_ma6'] = df_cr['value_c002'].rolling(6, min_periods=4).mean()
    df_cr['c001_slope'] = df_cr['c001_ma6'] - df_cr['c001_ma6'].shift(3)
    df_cr['c002_slope'] = df_cr['c002_ma6'] - df_cr['c002_ma6'].shift(3)
    df_cr['phase'] = df_cr.apply(lambda r: _classify_credit_phase(r['c001_slope'], r['c002_slope']), axis=1)
    df_cr = df_cr[df_cr['date'] >= since]
    credit_transitions = _compute_transitions(df_cr.set_index('date')['phase'])

    # ---- 库存周期 ----
    # 优先使用原始指标I009(产成品存货同比)+I010(营收同比)
    # 如果数据不足24个月，回退到I004(PPI)+I008(工业增加值)代理，并用I009+I010修正当前阶段
    df_i009 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I009' AND value IS NOT NULL ORDER BY date", conn)
    df_i010 = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I010' AND value IS NOT NULL ORDER BY date", conn)
    
    if len(df_i009) >= 24 and len(df_i010) >= 24:
        df_inv = pd.merge(df_i009, df_i010, on='date', suffixes=('_inv', '_rev'))
        df_inv['date'] = pd.to_datetime(df_inv['date'])
        df_inv = df_inv.sort_values('date')
        df_inv['inv_ma6'] = df_inv['value_inv'].rolling(6, min_periods=4).mean()
        df_inv['rev_ma6'] = df_inv['value_rev'].rolling(6, min_periods=4).mean()
        df_inv['inv_slope'] = df_inv['inv_ma6'] - df_inv['inv_ma6'].shift(3)
        df_inv['rev_slope'] = df_inv['rev_ma6'] - df_inv['rev_ma6'].shift(3)
        df_inv['phase'] = df_inv.apply(lambda r: _classify_inventory_phase(r['inv_slope'], r['rev_slope']), axis=1)
    else:
        # 回退到替代指标
        df_ppi = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I004' ORDER BY date", conn)
        df_iva = pd.read_sql("SELECT date, value FROM economic_indicators WHERE indicator_id='I008' ORDER BY date", conn)
        df_inv = pd.merge(df_ppi, df_iva, on='date', suffixes=('_inv', '_rev'))
        df_inv['date'] = pd.to_datetime(df_inv['date'])
        df_inv = df_inv.sort_values('date')
        df_inv['inv_ma6'] = df_inv['value_inv'].rolling(6, min_periods=4).mean()
        df_inv['rev_ma6'] = df_inv['value_rev'].rolling(6, min_periods=4).mean()
        df_inv['inv_slope'] = df_inv['inv_ma6'] - df_inv['inv_ma6'].shift(3)
        df_inv['rev_slope'] = df_inv['rev_ma6'] - df_inv['rev_ma6'].shift(3)
        df_inv['phase'] = df_inv.apply(lambda r: _classify_inventory_phase(r['inv_slope'], r['rev_slope']), axis=1)
        
        # 用I009+I010最新数据修正当前阶段
        if len(df_i009) >= 3 and len(df_i010) >= 3:
            recent_inv = df_i009.sort_values('date').tail(3)['value'].mean()
            recent_rev = df_i010.sort_values('date').tail(3)['value'].mean()
            current_phase = _classify_inventory_phase(recent_inv, recent_rev)
            if current_phase:
                last_phase = df_inv['phase'].iloc[-1] if not df_inv.empty and pd.notna(df_inv['phase'].iloc[-1]) else None
                if last_phase != current_phase:
                    idx = len(df_inv) - 1
                    while idx > 0 and df_inv['phase'].iloc[idx] == last_phase:
                        idx -= 1
                    start_idx = idx + 1
                    df_inv.iloc[start_idx:, df_inv.columns.get_loc('phase')] = current_phase
    inv_transitions = _compute_transitions(df_inv.set_index('date')['phase'])

    # ---- 当前轮次起始 ----
    credit_order = ['信贷扩张期', '政策收紧期', '信贷收缩期', '政策宽松期']
    inv_order = ['主动补库存', '被动补库存', '主动去库存', '被动去库存']

    current_credit = credit_transitions[-1][1] if credit_transitions else '未知'
    current_inv = inv_transitions[-1][1] if inv_transitions else '未知'

    credit_cycle_start = None
    for i in range(len(credit_transitions) - 1, -1, -1):
        if credit_transitions[i][1] == credit_order[0]:
            credit_cycle_start = credit_transitions[i][0]
            break

    inv_cycle_start = None
    for i in range(len(inv_transitions) - 1, -1, -1):
        if inv_transitions[i][1] == inv_order[0]:
            inv_cycle_start = inv_transitions[i][0]
            break

    return {
        'credit_transitions': credit_transitions,
        'inv_transitions': inv_transitions,
        'current_credit': current_credit,
        'current_credit_start': credit_transitions[-1][0] if credit_transitions else None,
        'current_credit_duration': credit_transitions[-1][2] if credit_transitions else 0,
        'credit_cycle_start': credit_cycle_start,
        'current_inv': current_inv,
        'current_inv_start': inv_transitions[-1][0] if inv_transitions else None,
        'current_inv_duration': inv_transitions[-1][2] if inv_transitions else 0,
        'inv_cycle_start': inv_cycle_start,
    }


def get_indicator_stats(conn, indicator_id):
    """获取指标统计数据"""
    query = """
    SELECT 
        date, value,
        (SELECT AVG(value) FROM economic_indicators WHERE indicator_id = ? AND date >= date('now', '-3 months')) as ma3_avg
    FROM economic_indicators 
    WHERE indicator_id = ?
    ORDER BY date DESC
    LIMIT 1
    """
    df = pd.read_sql_query(query, conn, params=(indicator_id, indicator_id))
    if df.empty:
        return {'latest': None, 'ma3_avg': None, 'trend': None}
    
    latest = df['value'].iloc[0]
    ma3_avg = df['ma3_avg'].iloc[0] if df['ma3_avg'].iloc[0] else latest
    
    trend = '持平'
    if ma3_avg and pd.notna(ma3_avg):
        if latest > ma3_avg * 1.01:
            trend = '上升'
        elif latest < ma3_avg * 0.99:
            trend = '下降'
    
    return {'latest': latest, 'ma3_avg': ma3_avg, 'trend': trend}


def create_indicator_table(all_data, conn):
    """创建指标数据表格HTML"""
    rows = []
    for indicator_id, name in INDICATOR_CONFIG.items():
        if indicator_id in ['V001', 'V002']:
            continue
        
        stats = get_indicator_stats(conn, indicator_id)
        latest = f"{stats['latest']:.2f}" if stats['latest'] is not None and pd.notna(stats['latest']) else '-'
        ma3_avg = f"{stats['ma3_avg']:.2f}" if stats['ma3_avg'] is not None and pd.notna(stats['ma3_avg']) else '-'
        
        trend_symbol = {'上升': '↑', '下降': '↓', '持平': '→'}
        trend_display = f"<span class='trend-{stats['trend']}'>{trend_symbol.get(stats['trend'], '-')}</span>"
        
        df = all_data.get(indicator_id, pd.DataFrame())
        if df.empty:
            data_status = '<span class="status-no">暂无数据</span>'
        elif len(df) < 5:
            data_status = '<span class="status-limited">数据较少</span>'
        else:
            data_status = '<span class="status-ok">完整</span>'
        
        rows.append(f"""
        <tr>
            <td>{indicator_id}</td>
            <td>{name}</td>
            <td class="value">{latest}</td>
            <td class="value">{ma3_avg}</td>
            <td>{trend_display}</td>
            <td>{data_status}</td>
        </tr>
        """)
    
    return '\n'.join(rows)


def get_base_layout():
    """获取基础布局配置"""
    return {
        'paper_bgcolor': 'transparent',
        'plot_bgcolor': 'transparent',
        'font': {'color': '#e0e0e0', 'family': 'PingFang SC, Microsoft YaHei'},
        'margin': {'t': 20, 'r': 20, 'b': 50, 'l': 50},
        'xaxis': {
            'gridcolor': 'rgba(255,255,255,0.1)',
            'linecolor': 'rgba(255,255,255,0.2)',
            'tickfont': {'size': 10},
            'showspikes': True,
            'spikecolor': 'rgba(255,255,255,0.3)',
            'spikethickness': 1,
            'spikesnap': 'cursor'
        },
        'yaxis': {
            'gridcolor': 'rgba(255,255,255,0.1)',
            'linecolor': 'rgba(255,255,255,0.2)',
            'tickfont': {'size': 10}
        },
        'legend': {
            'bgcolor': 'rgba(0,0,0,0)',
            'borderwidth': 0,
            'font': {'size': 11},
            'orientation': 'h',
            'x': 0.5,
            'xanchor': 'center',
            'y': -0.2
        },
        'hovermode': 'x unified',
        'spikedistance': -1
    }


def generate_charts(all_data):
    """生成所有图表的JSON配置"""
    charts = {}
    base_layout = get_base_layout()
    
    # 信贷图表1: 社融同比 + M2同比
    if 'C001' in all_data and 'C002' in all_data:
        df1 = all_data['C001'].sort_values('date')
        df2 = all_data['C002'].sort_values('date')
        
        traces = [
            {'x': df1['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df1['value'].tolist(),
             'mode': 'lines', 'name': '社融同比', 'line': {'color': '#00C853', 'width': 2}},
            {'x': df1['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df1['ma3'].fillna(0).tolist(),
             'mode': 'lines', 'name': '社融3月均', 'line': {'color': '#00C853', 'width': 1.5, 'dash': 'dash'}, 'opacity': 0.7},
            {'x': df2['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df2['value'].tolist(),
             'mode': 'lines', 'name': 'M2同比', 'line': {'color': '#448AFF', 'width': 2}},
            {'x': df2['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df2['ma3'].fillna(0).tolist(),
             'mode': 'lines', 'name': 'M2 3月均', 'line': {'color': '#448AFF', 'width': 1.5, 'dash': 'dash'}, 'opacity': 0.7},
        ]
        
        layout = dict(base_layout, title='', xaxis=dict(base_layout['xaxis'],
            range=[df1['date'].min().strftime('%Y-%m-%d'), df1['date'].max().strftime('%Y-%m-%d')]))
        layout['xaxis']['showspikes'] = True
        
        charts['chart-credit-1'] = {'data': traces, 'layout': layout}
    
    # 信贷图表2: 社融-M2剪刀差
    if 'V001' in all_data and not all_data['V001'].empty:
        df = all_data['V001'].sort_values('date')
        colors = ['#00C853' if v >= 0 else '#FF5252' for v in df['value']]
        
        traces = [
            {'x': df['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df['value'].tolist(),
             'type': 'bar', 'name': '剪刀差', 'marker': {'color': colors}},
            {'x': df['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df['ma3'].fillna(0).tolist(),
             'mode': 'lines', 'name': '3月均线', 'line': {'color': '#FF9800', 'width': 2}},
        ]
        
        layout = dict(base_layout, title='')
        charts['chart-credit-2'] = {'data': traces, 'layout': layout}
    
    # 信贷图表3: 贷款分项
    has_c004 = 'C004' in all_data and len(all_data['C004']) > 0
    has_c005 = 'C005' in all_data and len(all_data['C005']) > 0
    
    if has_c004 or has_c005:
        traces = []
        
        if has_c004:
            df4 = all_data['C004'].sort_values('date')
            traces.append({
                'x': df4['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y': df4['value'].tolist(),
                'mode': 'lines', 'name': '企业贷款',
                'fill': 'tozeroy', 'line': {'color': '#00C853', 'width': 2},
                'fillcolor': 'rgba(0,200,83,0.2)'
            })
        
        if has_c005:
            df5 = all_data['C005'].sort_values('date')
            traces.append({
                'x': df5['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y': df5['value'].tolist(),
                'mode': 'lines', 'name': '居民贷款',
                'fill': 'tozeroy', 'line': {'color': '#448AFF', 'width': 2},
                'fillcolor': 'rgba(68,138,255,0.2)'
            })
        
        if traces:
            layout = dict(base_layout, title='')
            charts['chart-credit-3'] = {'data': traces, 'layout': layout}
    
    # 信贷图表4: 利率指标
    has_c007 = 'C007' in all_data and len(all_data['C007']) > 0
    has_c008 = 'C008' in all_data and len(all_data['C008']) > 0
    
    if has_c007 or has_c008:
        traces = []
        
        if has_c007:
            df7 = all_data['C007'].sort_values('date')
            traces.append({
                'x': df7['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y': df7['value'].tolist(),
                'mode': 'lines', 'name': 'DR007',
                'line': {'color': '#FF9800', 'width': 2}
            })
        
        if has_c008:
            df8 = all_data['C008'].sort_values('date')
            traces.append({
                'x': df8['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y': df8['value'].tolist(),
                'mode': 'lines', 'name': '贷款利率',
                'line': {'color': '#E040FB', 'width': 2}
            })
        
        if traces:
            layout = dict(base_layout, title='')
            charts['chart-credit-4'] = {'data': traces, 'layout': layout}
    
    # 库存图表1: PPI同比 + PPIRM同比
    if 'I004' in all_data and 'I005' in all_data:
        df4 = all_data['I004'].sort_values('date')
        df5 = all_data['I005'].sort_values('date')
        
        traces = [
            {'x': df4['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df4['value'].tolist(),
             'mode': 'lines', 'name': 'PPI同比', 'line': {'color': '#00C853', 'width': 2}},
            {'x': df5['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df5['value'].tolist(),
             'mode': 'lines', 'name': 'PPIRM同比', 'line': {'color': '#FF9800', 'width': 2}},
        ]
        
        layout = dict(base_layout, title='')
        charts['chart-inventory-1'] = {'data': traces, 'layout': layout}
    
    # 库存图表2: PMI分项
    has_i006 = 'I006' in all_data and len(all_data['I006']) > 0
    has_i007 = 'I007' in all_data and len(all_data['I007']) > 0
    
    if has_i006 or has_i007:
        traces = []
        
        if has_i006:
            df6 = all_data['I006'].sort_values('date')
            traces.append({
                'x': df6['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y': df6['value'].tolist(),
                'mode': 'lines+markers', 'name': 'PMI新订单',
                'line': {'color': '#00C853', 'width': 2}
            })
        
        if has_i007:
            df7 = all_data['I007'].sort_values('date')
            traces.append({
                'x': df7['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y': df7['value'].tolist(),
                'mode': 'lines+markers', 'name': 'PMI产成品库存',
                'line': {'color': '#FF5252', 'width': 2}
            })
        
        if traces:
            layout = dict(base_layout, title='')
            charts['chart-inventory-2'] = {'data': traces, 'layout': layout}
    
    # 库存图表3: 工业增加值 + 出口同比
    if 'I008' in all_data and 'V003' in all_data:
        df8 = all_data['I008'].sort_values('date')
        df3 = all_data['V003'].sort_values('date')
        
        traces = [
            {'x': df8['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df8['value'].tolist(),
             'type': 'bar', 'name': '工业增加值同比', 'marker': {'color': '#448AFF'}, 'opacity': 0.7, 'yaxis': 'y'},
            {'x': df3['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df3['value'].tolist(),
             'mode': 'lines', 'name': '出口同比', 'line': {'color': '#00C853', 'width': 2}, 'yaxis': 'y2'},
        ]
        
        layout = dict(base_layout, title='')
        layout['yaxis2'] = {
            'overlaying': 'y', 'side': 'right',
            'gridcolor': 'rgba(255,255,255,0.1)',
            'linecolor': 'rgba(255,255,255,0.2)',
            'tickfont': {'size': 10}
        }
        charts['chart-inventory-3'] = {'data': traces, 'layout': layout}
    
    # 库存图表4: PMI新订单-库存差
    if 'V002' in all_data and not all_data['V002'].empty:
        df = all_data['V002'].sort_values('date')
        colors = ['#00C853' if v >= 0 else '#FF5252' for v in df['value']]
        
        traces = [
            {'x': df['date'].dt.strftime('%Y-%m-%d').tolist(), 'y': df['value'].tolist(),
             'type': 'bar', 'name': 'PMI差值', 'marker': {'color': colors}},
        ]
        
        layout = dict(base_layout, title='')
        charts['chart-inventory-4'] = {'data': traces, 'layout': layout}
    
    return charts


def create_chart_script(charts):
    """生成图表JavaScript代码"""
    js_parts = []
    for chart_id, config in charts.items():
        js_parts.append(
            f"Plotly.newPlot('{chart_id}', {json.dumps(config['data'])}, {json.dumps(config['layout'])}, config);"
        )
    return '\n        '.join(js_parts)


def create_dashboard_html(all_data, cycle_judgments, update_time, cycle_info):
    """生成完整的HTML仪表盘"""
    
    current_credit = cycle_info['current_credit']
    current_inventory = cycle_info['current_inv']
    credit_duration = cycle_info['current_credit_duration']
    inventory_duration = cycle_info['current_inv_duration']
    
    # 用历史平均持续月数
    credit_avg = CREDIT_PHASE_AVG.get(current_credit, CREDIT_AVG_DURATION)
    inv_avg = INVENTORY_PHASE_AVG.get(current_inventory, INVENTORY_AVG_DURATION)
    
    credit_progress = min(100, (credit_duration / credit_avg) * 100) if credit_duration else 0
    inventory_progress = min(100, (inventory_duration / inv_avg) * 100) if inventory_duration else 0
    
    credit_remaining = max(0, credit_avg - credit_duration)
    inventory_remaining = max(0, inv_avg - inventory_duration)
    
    credit_color = CREDIT_CYCLE_COLORS.get(current_credit, '#888')
    inventory_color = INVENTORY_CYCLE_COLORS.get(current_inventory, '#888')
    
    # 周期起始标注
    credit_cycle_start = cycle_info.get('credit_cycle_start', '-')
    credit_phase_start = cycle_info.get('current_credit_start', '-')
    inv_cycle_start = cycle_info.get('inv_cycle_start', '-')
    inv_phase_start = cycle_info.get('current_inv_start', '-')
    
    # 下一阶段预告
    credit_order = ['信贷扩张期', '政策收紧期', '信贷收缩期', '政策宽松期']
    inv_order = ['主动补库存', '被动补库存', '主动去库存', '被动去库存']
    next_credit = credit_order[(credit_order.index(current_credit) + 1) % 4] if current_credit in credit_order else '未知'
    next_inv = inv_order[(inv_order.index(current_inventory) + 1) % 4] if current_inventory in inv_order else '未知'
    
    # 生成图表
    charts = generate_charts(all_data)
    charts_html = create_chart_script(charts)
    
    # 生成指标表格
    conn = connect_db()
    indicator_table = create_indicator_table(all_data, conn)
    conn.close()
    
    # ---- 生成周期阶段时间轴 HTML ----
    def build_timeline_html(transitions, colors_map, cycle_start, cycle_type):
        """生成单行周期阶段时间轴"""
        if not transitions:
            return '<div style="color:#666;padding:10px;">数据不足</div>'
        # 只显示从本轮起始开始的阶段
        start_idx = 0
        if cycle_start:
            for i, (d, p, m) in enumerate(transitions):
                if d == cycle_start:
                    start_idx = i
                    break
        visible = transitions[start_idx:]
        
        total_months = sum(m for _, _, m in visible)
        if total_months == 0:
            total_months = 1
        
        parts = []
        for dt, phase, months in visible:
            pct = max(3, round(months / total_months * 100))
            # 缩短名称
            short = phase.replace('信贷','').replace('政策','').replace('库存','').replace('期','')
            color = colors_map.get(phase, '#888')
            is_current = (dt == transitions[-1][0] and phase == transitions[-1][1])
            border = 'border:2px solid #fff;' if is_current else ''
            parts.append(f'''<div style="flex:{pct};background:{color};padding:6px 4px;text-align:center;
                font-size:0.8em;color:#fff;{border}border-radius:4px;position:relative;min-width:60px;">
                <div style="font-weight:600;">{short}</div>
                <div style="font-size:0.75em;opacity:0.85;">{dt}</div>
                <div style="font-size:0.7em;opacity:0.7;">{months}月</div>
            </div>''')
        
        return '<div style="display:flex;gap:4px;align-items:stretch;">' + ''.join(parts) + '</div>'
    
    credit_timeline = build_timeline_html(
        cycle_info['credit_transitions'], CREDIT_CYCLE_COLORS, credit_cycle_start, 'credit')
    inv_timeline = build_timeline_html(
        cycle_info['inv_transitions'], INVENTORY_CYCLE_COLORS, inv_cycle_start, 'inventory')
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>库存信贷周期监控系统</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'PingFang SC', 'Microsoft YaHei', -apple-system, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1600px;
            margin: 0 auto;
        }}
        
        .header {{
            text-align: center;
            padding: 20px 0;
            margin-bottom: 30px;
        }}
        
        .header h1 {{
            font-size: 2.2em;
            font-weight: 600;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }}
        
        .header .subtitle {{
            color: #888;
            font-size: 0.95em;
        }}
        
        .status-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .status-card {{
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 24px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
        }}
        
        .status-card.credit {{
            border-left: 4px solid {credit_color};
        }}
        
        .status-card.inventory {{
            border-left: 4px solid {inventory_color};
        }}
        
        .status-card .label {{
            font-size: 0.9em;
            color: #888;
            margin-bottom: 8px;
        }}
        
        .status-card .value {{
            font-size: 1.8em;
            font-weight: 700;
            margin-bottom: 16px;
        }}
        
        .status-card.credit .value {{
            color: {credit_color};
        }}
        
        .status-card.inventory .value {{
            color: {inventory_color};
        }}
        
        .progress-section {{
            margin-top: 10px;
        }}
        
        .progress-label {{
            display: flex;
            justify-content: space-between;
            font-size: 0.85em;
            color: #aaa;
            margin-bottom: 6px;
        }}
        
        .progress-bar {{
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
            overflow: hidden;
        }}
        
        .progress-fill {{
            height: 100%;
            border-radius: 4px;
            transition: width 0.5s ease;
        }}
        
        .progress-fill.credit {{
            background: linear-gradient(90deg, {credit_color}, {credit_color}88);
        }}
        
        .progress-fill.inventory {{
            background: linear-gradient(90deg, {inventory_color}, {inventory_color}88);
        }}
        
        .update-info {{
            text-align: right;
            color: #666;
            font-size: 0.85em;
            margin-bottom: 30px;
        }}
        
        .chart-section {{
            margin-bottom: 40px;
        }}
        
        .section-title {{
            font-size: 1.4em;
            font-weight: 600;
            margin-bottom: 20px;
            padding-left: 16px;
            border-left: 4px solid #00d4ff;
            color: #fff;
        }}
        
        .chart-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
        }}
        
        @media (max-width: 1200px) {{
            .chart-grid {{
                grid-template-columns: 1fr;
            }}
        }}
        
        .chart-container {{
            background: rgba(255, 255, 255, 0.03);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
        
        .chart-title {{
            font-size: 1.1em;
            font-weight: 500;
            color: #ddd;
            margin-bottom: 16px;
            text-align: center;
        }}
        
        .data-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9em;
        }}
        
        .data-table th {{
            background: rgba(255, 255, 255, 0.05);
            padding: 12px 8px;
            text-align: left;
            font-weight: 500;
            color: #aaa;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .data-table td {{
            padding: 10px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }}
        
        .data-table tr:hover {{
            background: rgba(255, 255, 255, 0.03);
        }}
        
        .data-table .value {{
            font-family: 'Roboto Mono', monospace;
            color: #00d4ff;
        }}
        
        .trend-up {{ color: #00C853; }}
        .trend-down {{ color: #FF5252; }}
        .trend-持平 {{ color: #888; }}
        
        .status-ok {{ color: #00C853; }}
        .status-limited {{ color: #FF9800; }}
        .status-no {{ color: #FF5252; }}
        
        .risk-warning {{
            margin-top: 40px;
            padding: 20px;
            background: rgba(255, 152, 0, 0.1);
            border: 1px solid rgba(255, 152, 0, 0.3);
            border-radius: 8px;
            text-align: center;
            color: #FF9800;
            font-size: 0.9em;
        }}
        
        .legend {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 10px;
            font-size: 0.85em;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        
        .legend-color {{
            width: 12px;
            height: 3px;
            border-radius: 2px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 库存信贷周期监控系统</h1>
            <p class="subtitle">基于经济指标的周期性分析 · 数据驱动决策支持</p>
        </div>
        
        <div class="status-cards">
            <div class="status-card credit">
                <div class="label">📈 信贷周期</div>
                <div class="value">{current_credit}</div>
                <div style="font-size:0.85em;color:#aaa;margin-bottom:8px;">
                    本轮始于 <b style="color:#fff">{credit_cycle_start}</b> · 
                    当前阶段始于 <b style="color:#fff">{credit_phase_start}</b> · 
                    下一阶段: <b style="color:{CREDIT_CYCLE_COLORS.get(next_credit, "#888")}">{next_credit}</b>
                </div>
                <div class="progress-section">
                    <div class="progress-label">
                        <span>已持续 {credit_duration} 个月</span>
                        <span>历史平均 {credit_avg} 个月</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill credit" style="width: {credit_progress}%"></div>
                    </div>
                    <div class="progress-label" style="margin-top: 6px;">
                        <span>预计 {credit_remaining} 个月后切换 → {next_credit}</span>
                    </div>
                </div>
            </div>
            
            <div class="status-card inventory">
                <div class="label">📦 库存周期</div>
                <div class="value">{current_inventory}</div>
                <div style="font-size:0.85em;color:#aaa;margin-bottom:8px;">
                    本轮始于 <b style="color:#fff">{inv_cycle_start}</b> · 
                    当前阶段始于 <b style="color:#fff">{inv_phase_start}</b> · 
                    下一阶段: <b style="color:{INVENTORY_CYCLE_COLORS.get(next_inv, "#888")}">{next_inv}</b>
                </div>
                <div class="progress-section">
                    <div class="progress-label">
                        <span>已持续 {inventory_duration} 个月</span>
                        <span>历史平均 {inv_avg} 个月</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill inventory" style="width: {inventory_progress}%"></div>
                    </div>
                    <div class="progress-label" style="margin-top: 6px;">
                        <span>预计 {inventory_remaining} 个月后切换 → {next_inv}</span>
                    </div>
                </div>
            </div>
            
            <div class="status-card" style="display: flex; flex-direction: column; justify-content: center;">
                <div class="label">🕐 数据更新时间</div>
                <div class="value" style="font-size: 1.2em; color: #00d4ff;">{update_time}</div>
                <div style="margin-top: 8px; color: #aaa; font-size: 0.82em;">
                    信贷周期 ≈ {CREDIT_FULL_CYCLE_AVG}月/轮 · 库存周期 ≈ {INVENTORY_FULL_CYCLE_AVG}月/轮
                </div>
                <div style="color: #666; font-size: 0.82em;">
                    共追踪 19 个经济指标
                </div>
            </div>
        </div>
        
        <div class="chart-section">
            <h2 class="section-title">🕐 周期阶段时间轴</h2>
            <div class="chart-container">
                <div style="margin-bottom:16px;">
                    <div style="font-size:1em;font-weight:500;color:#ddd;margin-bottom:8px;">📈 信贷周期（本轮）</div>
                    {credit_timeline}
                </div>
                <div>
                    <div style="font-size:1em;font-weight:500;color:#ddd;margin-bottom:8px;">📦 库存周期（本轮）</div>
                    {inv_timeline}
                </div>
            </div>
        </div>
        
        <div class="chart-section">
            <h2 class="section-title">💰 信贷周期指标</h2>
            <div class="chart-grid">
                <div class="chart-container">
                    <div class="chart-title">社融存量同比 (C001) + M2同比 (C002)</div>
                    <div id="chart-credit-1" style="height: 300px;"></div>
                </div>
                <div class="chart-container">
                    <div class="chart-title">社融-M2剪刀差 (V001)</div>
                    <div id="chart-credit-2" style="height: 300px;"></div>
                </div>
                <div class="chart-container">
                    <div class="chart-title">贷款分项 (C004 企业 + C005 居民)</div>
                    <div id="chart-credit-3" style="height: 300px;"></div>
                </div>
                <div class="chart-container">
                    <div class="chart-title">利率指标 (C007 DR007 + C008 贷款利率)</div>
                    <div id="chart-credit-4" style="height: 300px;"></div>
                </div>
            </div>
        </div>
        
        <div class="chart-section">
            <h2 class="section-title">🏭 库存周期指标</h2>
            <div class="chart-grid">
                <div class="chart-container">
                    <div class="chart-title">PPI同比 (I004) + PPIRM同比 (I005)</div>
                    <div id="chart-inventory-1" style="height: 300px;"></div>
                </div>
                <div class="chart-container">
                    <div class="chart-title">PMI分项 (I006 新订单 + I007 产成品库存)</div>
                    <div id="chart-inventory-2" style="height: 300px;"></div>
                </div>
                <div class="chart-container">
                    <div class="chart-title">工业增加值 (I008) + 出口同比 (V003)</div>
                    <div id="chart-inventory-3" style="height: 300px;"></div>
                </div>
                <div class="chart-container">
                    <div class="chart-title">PMI新订单-库存差 (V002)</div>
                    <div id="chart-inventory-4" style="height: 300px;"></div>
                </div>
            </div>
        </div>
        
        <div class="chart-section">
            <h2 class="section-title">📋 指标数据总览</h2>
            <div class="chart-container" style="overflow-x: auto;">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>代码</th>
                            <th>指标名称</th>
                            <th>最新值</th>
                            <th>3月均值</th>
                            <th>趋势</th>
                            <th>状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {indicator_table}
                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="risk-warning">
            ⚠️ 风险提示：本系统仅作为案例分析学习材料，不构成任何投资建议。过往业绩不代表未来表现，市场有风险，投资需谨慎。
        </div>
    </div>
    
    <script>
        const config = {{
            displayModeBar: true,
            responsive: true,
            displaylogo: false
        }};
        
        {charts_html}
    </script>
</body>
</html>'''
    
    return html


def main():
    """主函数"""
    print("=" * 60)
    print("库存信贷周期监控系统 - HTML仪表盘生成器")
    print("=" * 60)
    
    print("\n[1/4] 连接数据库...")
    conn = connect_db()
    db_path = get_db_path()
    print(f"    数据库路径: {db_path}")
    
    print("\n[2/4] 读取经济指标数据...")
    all_data = fetch_all_indicators(conn, limit_years=5)
    
    print("    计算派生指标 V001 (社融-M2剪刀差)...")
    v001 = calculate_v001(conn, limit_years=5)
    if not v001.empty:
        all_data['V001'] = v001
    
    print("    计算派生指标 V002 (PMI新订单-库存差)...")
    v002 = calculate_v002(conn, limit_years=5)
    if not v002.empty:
        all_data['V002'] = v002
    
    for indicator_id, df in all_data.items():
        print(f"    {indicator_id}: {len(df)} 条数据")
    
    print("\n[3/4] 分析周期阶段历史...")
    cycle_judgments = fetch_cycle_judgments(conn)
    cycle_info = analyze_cycle_history(conn)
    print(f"    当前信贷周期: {cycle_info['current_credit']} (始于{cycle_info['current_credit_start']}, 已{cycle_info['current_credit_duration']}月)")
    print(f"    信贷本轮始于: {cycle_info['credit_cycle_start']}")
    print(f"    当前库存周期: {cycle_info['current_inv']} (始于{cycle_info['current_inv_start']}, 已{cycle_info['current_inv_duration']}月)")
    print(f"    库存本轮始于: {cycle_info['inv_cycle_start']}")
    
    update_time = fetch_latest_update_time(conn)
    print(f"    数据更新时间: {update_time}")
    
    conn.close()
    
    print("\n[4/4] 生成HTML仪表盘...")
    html_content = create_dashboard_html(all_data, cycle_judgments, update_time, cycle_info)
    
    output_path = get_output_path()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    file_size = os.path.getsize(output_path)
    print(f"\n✅ 生成完成!")
    print(f"    输出路径: {output_path}")
    print(f"    文件大小: {file_size / 1024:.1f} KB")
    
    if file_size < 10000:
        print(f"    ⚠️ 警告: 文件大小可能过小，请检查生成结果")
    
    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
