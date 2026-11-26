# -*- coding: utf-8 -*-
"""
LPPL (Log-Periodic Power Law) 模型 — A股崩盘/泡沫预警
==================
基于 Sornette 教授的对数周期幂律模型，用于检测和预测市场泡沫与崩盘。

核心公式（C1/C2参数化）：
ln(p(t)) = A + B*(tc-t)^m + C1*(tc-t)^m*cos(ω*ln(tc-t)) + C2*(tc-t)^m*sin(ω*ln(tc-t))

参数说明：
- A, B, C1, C2: 线性参数（OLS精确求解）
- tc: 临界时间（崩盘/拐点时间）— 非线性参数
- m: 幂律指数 (0.1 < m < 0.9) — 非线性参数
- ω: 对数周期振荡频率 (6 ≤ ω ≤ 13) — 非线性参数

拟合方法：两步法（平头哥/东北证券方法论）
  第一步：差分进化搜索3个非线性参数(tc, m, ω)
  第二步：OLS精确求解4个线性参数(A, B, C1, C2)

五条约束（平头哥方法论）：
  1. 0.2 ≤ m ≤ 0.8（脆弱性指数有效区间）
  2. 6 ≤ ω ≤ 13（羊群层级指数典型区间）
  3. bars_to_tc ≤ 0.6 × 窗口长度（避免过度外推）
  4. B < 0（看涨泡沫）/ B > 0（看跌泡沫）
  5. |C|/|B| ≤ 1（振荡幅度不超过趋势幅度）

高概率信号额外条件（二元判定框架）：
  - R² > 0.95
  - tc 在 10~30 个交易日内
  - 稳健性检验：微调起始点tc波动 < ±5交易日
  - 至少2个相关指数共振

数据源：tushare (指数/个股)
"""

import numpy as np
import pandas as pd
import tushare as ts
from scipy.optimize import differential_evolution
from datetime import datetime, timedelta
import json
import os
import warnings
warnings.filterwarnings('ignore')

TOKEN = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"
pro = ts.pro_api(TOKEN)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_data(ts_code='000001.SH', start_date=None, end_date=None, days=500):
    """获取指数/个股日线数据（自动判断类型）"""
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    # 判断是指数还是个股：指数代码格式 000001.SH / 399001.SZ
    is_index = (ts_code.endswith('.SH') and ts_code[:6].isdigit() and int(ts_code[:3]) < 600) or \
               (ts_code.endswith('.SZ') and ts_code[:6].isdigit() and ts_code[:3] == '399')
    
    if is_index:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    else:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df[['trade_date', 'close', 'pct_chg', 'vol', 'amount']]


def get_index_data(ts_code='000001.SH', start_date=None, end_date=None, days=500):
    """获取指数日线数据（兼容旧调用）"""
    return get_data(ts_code, start_date, end_date, days)


def lppl_func(t, A, B, C1, C2, tc, m, omega):
    """
    LPPL模型核心函数（C1/C2参数化，优于C/φ参数化）
    ln(p(t)) = A + B*(tc-t)^m + C1*(tc-t)^m*cos(ω*ln(tc-t)) + C2*(tc-t)^m*sin(ω*ln(tc-t))
    
    优势：固定(tc,m,ω)后，(A,B,C1,C2)全是线性参数，可OLS精确求解
    """
    dt = tc - t
    dt = np.maximum(dt, 1e-10)
    
    power_term = np.power(dt, m)
    log_term = np.log(dt)
    
    return A + B * power_term + C1 * power_term * np.cos(omega * log_term) + C2 * power_term * np.sin(omega * log_term)


def ols_solve(t_data, y_data, tc, m, omega):
    """
    固定非线性参数(tc,m,ω)后，OLS精确求解线性参数(A,B,C1,C2)
    
    构造设计矩阵 X = [1, f, g, h]，其中：
    - f = (tc-t)^m
    - g = (tc-t)^m * cos(ω*ln(tc-t))
    - h = (tc-t)^m * sin(ω*ln(tc-t))
    """
    dt = tc - t_data
    dt = np.maximum(dt, 1e-10)
    
    f = np.power(dt, m)
    g = f * np.cos(omega * np.log(dt))
    h = f * np.sin(omega * np.log(dt))
    
    # 设计矩阵 [1, f, g, h]
    X = np.column_stack([np.ones(len(t_data)), f, g, h])
    
    # OLS: beta = (X'X)^-1 X'y
    try:
        beta = np.linalg.lstsq(X, y_data, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    
    A, B, C1, C2 = beta
    
    # 约束检查
    if B >= 0:  # 看涨泡沫要求 B<0
        return None
    C = np.sqrt(C1**2 + C2**2)
    if C >= abs(B):  # |C|/|B| <= 1
        return None
    
    # 计算拟合质量
    predicted = X @ beta
    residuals = y_data - predicted
    ss_res = np.sum(residuals**2)
    
    return {
        'A': A, 'B': B, 'C1': C1, 'C2': C2,
        'C': C,
        'phi': np.arctan2(C2, C1),  # 反推相位
        'ss_res': ss_res,
        'predicted': predicted,
    }


def fit_lppl(dates, prices, tc_range_days=(5, 120), n_fits=3, bubble_type='bullish'):
    """
    两步法拟合LPPL模型（平头哥/东北证券方法论）
    
    第一步：网格搜索(tc, m, ω)粗扫 → 找出R²最高的候选区域
    第二步：从候选区域出发差分进化精细搜索
    第三步：固定(tc, m, ω)后OLS精确求解线性参数(A, B, C1, C2)
    
    参数:
    - dates: datetime数组
    - prices: 价格数组
    - tc_range_days: 临界时间搜索范围（数据末尾之后的天数）
    - n_fits: 差分进化精细搜索次数
    - bubble_type: 'bullish'(看涨泡沫,B<0) 或 'bearish'(看跌泡沫,B>0)
    """
    # 准备数据（用交易日序号，文章方法论）
    dates_list = pd.to_datetime(dates).to_pydatetime()
    t_data = np.arange(len(dates_list), dtype=float)  # 0,1,2,...,N-1
    y_data = np.log(prices.astype(float))
    
    t_max = max(t_data)
    n = len(t_data)
    tc_min = t_max + tc_range_days[0]
    tc_max = t_max + tc_range_days[1]
    
    def eval_grid_point(tc, m, omega):
        """评估单个(tc,m,ω)的拟合质量"""
        if m <= 0.2 or m >= 0.8:
            return None
        if omega < 6 or omega > 13:
            return None
        if tc <= t_max:
            return None
        bars_to_tc = tc - t_max
        if bars_to_tc > 0.6 * n:  # 放宽到60%，文章的tc明显超过30%
            return None
        
        ols = ols_solve(t_data, y_data, tc, m, omega)
        if ols is None:
            return None
        
        if bubble_type == 'bullish' and ols['B'] >= 0:
            return None
        if bubble_type == 'bearish' and ols['B'] <= 0:
            return None
        
        ss_tot = np.sum((y_data - np.mean(y_data))**2)
        r2 = 1 - ols['ss_res'] / ss_tot if ss_tot > 0 else 0
        
        return {'tc': tc, 'm': m, 'omega': omega, 'r2': r2, 'ols': ols}
    
    # === 第一步：粗网格搜索 ===
    n_tc = 30
    n_m = 15
    n_omega = 20
    
    tc_grid = np.linspace(tc_min, tc_max, n_tc)
    m_grid = np.linspace(0.22, 0.78, n_m)
    omega_grid = np.linspace(6.2, 12.8, n_omega)
    
    best_grid = None
    best_r2 = -1e10
    
    for tc_val in tc_grid:
        for m_val in m_grid:
            for omega_val in omega_grid:
                res = eval_grid_point(tc_val, m_val, omega_val)
                if res is not None and res['r2'] > best_r2:
                    best_r2 = res['r2']
                    best_grid = res
    
    if best_grid is None:
        print("  网格搜索未找到有效解")
        return None
    
    print(f"  网格搜索最优: R²={best_grid['r2']:.4f}, tc≈{best_grid['tc']:.1f}, m={best_grid['m']:.3f}, ω={best_grid['omega']:.2f}")
    
    # === 第二步：从网格最优区域出发差分进化 ===
    # 以网格最优为中心缩小搜索范围
    tc_center = best_grid['tc']
    m_center = best_grid['m']
    omega_center = best_grid['omega']
    
    tc_half = max((tc_max - tc_min) * 0.15, 5)
    m_half = 0.15
    omega_half = 2.0
    
    best_result = None
    best_cost = 1e10
    
    def objective(params):
        """目标函数：只搜索(tc, m, ω)，线性参数由OLS求解"""
        tc, m, omega = params
        
        if m <= 0.2 or m >= 0.8:
            return 1e10
        if omega < 6 or omega > 13:
            return 1e10
        if tc <= t_max:
            return 1e10
        bars_to_tc = tc - t_max
        if bars_to_tc > 0.6 * n:  # 放宽到60%，文章的tc明显超过30%
            return 1e10
        
        ols = ols_solve(t_data, y_data, tc, m, omega)
        if ols is None:
            return 1e10
        
        if bubble_type == 'bullish' and ols['B'] >= 0:
            return 1e10
        if bubble_type == 'bearish' and ols['B'] <= 0:
            return 1e10
        
        return ols['ss_res']
    
    for i in range(n_fits):
        try:
            # 以网格最优为中心，但允许较大范围搜索
            bounds = [
                (max(tc_min, tc_center - tc_half * 2), min(tc_max, tc_center + tc_half * 2)),
                (max(0.2, m_center - m_half * 2), min(0.8, m_center + m_half * 2)),
                (max(6, omega_center - omega_half * 2), min(13, omega_center + omega_half * 2)),
            ]
            
            result = differential_evolution(
                objective, 
                bounds,
                maxiter=3000,
                seed=42 + i,
                tol=1e-14,
                polish=True,
                mutation=(0.5, 1.5),
                recombination=0.9,
                popsize=60,
            )
            
            if result.fun < best_cost:
                best_cost = result.fun
                best_result = result
        except Exception as e:
            continue
    
    # 也尝试全局搜索（不限定在网格最优附近）
    for i in range(n_fits):
        try:
            bounds_global = [
                (tc_min, tc_max),
                (0.2, 0.8),
                (6, 13),
            ]
            
            result = differential_evolution(
                objective, 
                bounds_global,
                maxiter=3000,
                seed=100 + i,
                tol=1e-14,
                polish=True,
                mutation=(0.5, 1.5),
                recombination=0.9,
                popsize=60,
            )
            
            if result.fun < best_cost:
                best_cost = result.fun
                best_result = result
        except Exception as e:
            continue
    
    if best_result is None:
        return None
    
    tc, m, omega = best_result.x
    
    # 用最优(tc,m,ω)重新OLS求解线性参数
    ols = ols_solve(t_data, y_data, tc, m, omega)
    if ols is None:
        return None
    
    A, B, C1, C2, C, phi = ols['A'], ols['B'], ols['C1'], ols['C2'], ols['C'], ols['phi']
    predicted = ols['predicted']
    
    # 拟合质量
    residuals = y_data - predicted
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y_data - np.mean(y_data))**2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    
    # 临界时间转换（交易日序号→日期）
    bars_to_tc_int = int(round(tc - t_max))
    if bars_to_tc_int <= len(dates_list) - 1 - int(t_max):
        tc_date = dates_list[bars_to_tc_int + int(t_max)]
    else:
        # tc超出已知交易日范围，用最后的交易日+外推
        last_date = dates_list[-1]
        tc_date = last_date + timedelta(days=bars_to_tc_int)  # 近似
        # 更精确：按5个日历天=1周≈5交易日推算
        weeks_extra = bars_to_tc_int / 5
        tc_date = last_date + timedelta(weeks=weeks_extra)
    
    # 判断泡沫方向
    if B < 0:
        bubble_label = "看涨泡沫（上涨加速→见顶崩盘）"
    else:
        bubble_label = "看跌泡沫（下跌加速→见底反弹）"
    
    # bars_to_tc
    bars_to_tc = int(round(tc - t_max))
    
    # 五条约束检查
    constraints_passed = []
    constraints_passed.append(0.2 <= m <= 0.8)
    constraints_passed.append(6 <= omega <= 13)
    constraints_passed.append(bars_to_tc <= 0.6 * n)
    constraints_passed.append(B < 0 if bubble_type == 'bullish' else B > 0)
    constraints_passed.append(C < abs(B))
    n_constraints = sum(constraints_passed)
    
    period_days = 2 * np.pi / omega if omega > 0 else None
    
    result_dict = {
        'params': {
            'A': round(A, 4),
            'B': round(B, 4),
            'C': round(C, 4),
            'C1': round(C1, 4),
            'C2': round(C2, 4),
            'tc': round(tc, 2),
            'tc_date': tc_date.strftime('%Y-%m-%d'),
            'm': round(m, 4),
            'omega': round(omega, 4),
            'phi': round(phi, 4),
        },
        'quality': {
            'r_squared': round(r_squared, 4),
            'rmse': round(np.sqrt(np.mean(residuals**2)), 6),
            'n_points': len(t_data),
            'cost': round(best_cost, 6),
        },
        'constraints': {
            'n_passed': n_constraints,
            'm_valid': constraints_passed[0],
            'omega_valid': constraints_passed[1],
            'bars_to_tc_valid': constraints_passed[2],
            'B_sign_valid': constraints_passed[3],
            'C_ratio_valid': constraints_passed[4],
            'bars_to_tc': bars_to_tc,
            'bars_to_tc_ratio': round(bars_to_tc / n, 3),
            'C_over_B': round(C / abs(B), 4) if abs(B) > 0 else None,
        },
        'interpretation': {
            'bubble_type': bubble_label,
            'critical_date': tc_date.strftime('%Y-%m-%d'),
            'days_to_critical': bars_to_tc,
            'oscillation_period_days': round(period_days, 1) if period_days else None,
            'acceleration': f"m={m:.3f}（越接近0加速越快）",
            'confidence': f"{n_constraints}/5约束通过",
        }
    }
    
    return result_dict, predicted, t_data, y_data


def multi_window_fit(ts_code='000001.SH', end_date=None, windows=[120, 180, 250, 360, 500], specific_period=None):
    """多窗口拟合LPPL，取一致性最高的结果
    
    specific_period: 若指定(start, end)日期字符串，仅用该区间拟合（复现文章用）
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    
    print(f"\n{'='*60}")
    print(f"LPPL多窗口拟合 — {ts_code} — 截至{end_date}")
    print(f"{'='*60}")
    
    results = []
    
    if specific_period:
        # 复现文章：使用指定区间
        start_date, period_end = specific_period
        df = get_data(ts_code, start_date=start_date, end_date=period_end)
        
        if df.empty or len(df) < 10:
            print(f"\n指定区间 {start_date}~{period_end}：数据不足({len(df) if not df.empty else 0}条)")
            return results
        
        # tc搜索范围：数据末尾后5~60天
        tc_range = (5, 60)
        
        print(f"\n指定区间：{len(df)}条数据 ({df['trade_date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['trade_date'].iloc[-1].strftime('%Y-%m-%d')})")
        
        fit_result = fit_lppl(df['trade_date'].values, df['close'].values, tc_range_days=tc_range, n_fits=3)
        
        if fit_result is None:
            print(f"  ❌ 拟合失败")
        else:
            res, predicted, t_data, y_data = fit_result
            params = res['params']
            print(f"  ✅ R²={res['quality']['r_squared']:.4f} | tc={params['tc_date']} ({res['interpretation']['days_to_critical']}天后) | m={params['m']:.3f} | ω={params['omega']:.2f} | B={params['B']:.4f} | C/|B|={res['constraints']['C_over_B']:.3f} | {res['interpretation']['confidence']} | {res['interpretation']['bubble_type']}")
            
            results.append({
                'window': len(df),
                'period': f"{df['trade_date'].iloc[0].strftime('%Y-%m-%d')}~{df['trade_date'].iloc[-1].strftime('%Y-%m-%d')}",
                **res,
            })
        
        return results
    
    for window in windows:
        start_dt = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=int(window*1.5))).strftime('%Y%m%d')
        df = get_data(ts_code, start_date=start_dt, end_date=end_date)
        
        if len(df) < window * 0.7:
            print(f"\n窗口{window}天：数据不足({len(df)}天)，跳过")
            continue
        
        # 取最近window天
        df = df.tail(window).reset_index(drop=True)
        
        print(f"\n窗口{window}天：{len(df)}条数据 ({df['trade_date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['trade_date'].iloc[-1].strftime('%Y-%m-%d')})")
        
        fit_result = fit_lppl(df['trade_date'].values, df['close'].values, n_fits=5)
        
        if fit_result is None:
            print(f"  ❌ 拟合失败")
            continue
        
        res, predicted, t_data, y_data = fit_result
        params = res['params']
        
        print(f"  ✅ R²={res['quality']['r_squared']:.4f} | tc={params['tc_date']} ({res['interpretation']['days_to_critical']}天后) | m={params['m']:.3f} | ω={params['omega']:.2f} | {res['interpretation']['confidence']} | {res['interpretation']['bubble_type']}")
        
        results.append({
            'window': window,
            **res,
        })
    
    return results


def generate_report(results, ts_code='000001.SH', end_date=None):
    """生成LPPL分析报告"""
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    
    lines = []
    lines.append(f"# LPPL模型泡沫预警报告 — {ts_code}")
    lines.append(f"分析日期: {end_date}")
    lines.append("")
    
    if not results:
        lines.append("⚠️ 所有窗口拟合失败，无法给出判断")
        return '\n'.join(lines)
    
    # 汇总表
    lines.append("## 多窗口拟合结果")
    lines.append("")
    lines.append("| 窗口 | R² | 临界日期 | 剩余天数 | m | ω | 泡沫类型 |")
    lines.append("|------|-----|----------|----------|------|------|----------|")
    
    tc_dates = []
    for r in results:
        p = r['params']
        q = r['quality']
        i = r['interpretation']
        lines.append(f"| {r['window']}天 | {q['r_squared']:.4f} | {p['tc_date']} | {i['days_to_critical']}天 | {p['m']:.3f} | {p['omega']:.2f} | {i['bubble_type'][:6]} |")
        tc_dates.append(i['days_to_critical'])
    
    lines.append("")
    
    # 一致性分析
    if tc_dates:
        tc_mean = np.mean(tc_dates)
        tc_std = np.std(tc_dates)
        tc_min_d = min(tc_dates)
        tc_max_d = max(tc_dates)
        
        lines.append("## 临界时间一致性分析")
        lines.append("")
        lines.append(f"- 均值：{tc_mean:.0f}天后")
        lines.append(f"- 标准差：{tc_std:.0f}天")
        lines.append(f"- 范围：{tc_min_d}~{tc_max_d}天")
        
        # 判断方向一致性
        bubble_types = [r['interpretation']['bubble_type'] for r in results]
        bullish_count = sum(1 for b in bubble_types if '看涨' in b)
        bearish_count = sum(1 for b in bubble_types if '看跌' in b)
        
        lines.append("")
        lines.append("## 方向判断")
        lines.append("")
        if bullish_count > bearish_count:
            lines.append(f"🔴 **看涨泡沫信号占优**（{bullish_count}/{len(results)}个窗口）")
            lines.append("")
            lines.append("当前市场可能处于上涨加速阶段，接近临界点后面临崩盘风险")
            lines.append("- 临界时间附近可能发生10-30%的快速回调")
            lines.append("- 建议：逐步减仓、收紧止损，避免追高")
        elif bearish_count > bullish_count:
            lines.append(f"🟢 **看跌泡沫信号占优**（{bearish_count}/{len(results)}个窗口）")
            lines.append("")
            lines.append("当前市场可能处于下跌加速阶段，接近临界点后可能迎来反弹")
            lines.append("- 临界时间附近可能出现超跌反弹")
            lines.append("- 建议：谨慎抄底，等待反转确认")
        else:
            lines.append(f"⚪ **方向信号分歧**（看涨{bullish_count}个 vs 看跌{bearish_count}个）")
            lines.append("")
            lines.append("多窗口结果不一致，当前市场可能处于震荡区间，无明显泡沫信号")
        
        # R²筛选高质量结果
        high_r2 = [r for r in results if r['quality']['r_squared'] > 0.85]
        if high_r2:
            lines.append("")
            lines.append("## 高质量拟合（R² > 0.85）")
            lines.append("")
            for r in high_r2:
                p = r['params']
                i = r['interpretation']
                lines.append(f"- {r['window']}天窗口：tc={p['tc_date']}（{i['days_to_critical']}天后），m={p['m']:.3f}，{i['bubble_type']}")
    
    lines.append("")
    lines.append("---")
    lines.append("📌 LPPL模型是统计物理学方法，对历史泡沫有较好拟合能力，但未来不保证重复。案例分析学习材料，不构成投资建议。")
    
    return '\n'.join(lines)


def reproduce_article():
    """复现「平头哥的全天候」5/14文章的LPPL预测
    
    文章三标的及分析区间：
    - 澜起科技(688008.SH): 2026-03-26~2026-05-12 (30交易日)
    - 深证成指(399001.SZ): 2026-03-27~2026-05-12 (32交易日)  
    - 中际旭创(300308.SZ): 2026-03-02~2026-05-12 (47交易日)
    
    文章结果：
    | 标的 | tc | B | m | ω | R² |
    | 澜起科技 | 6月2日 | -0.518 | 0.374 | 8.72 | 0.976 |
    | 深证成指 | 6月10日 | -0.287 | 0.426 | 9.23 | 0.968 |
    | 中际旭创 | 6月12日 | -0.624 | 0.312 | 8.17 | 0.982 |
    """
    targets = [
        ('688008.SH', '澜起科技', ('20260326', '20260512'), '6月2日'),
        ('399001.SZ', '深证成指', ('20260327', '20260512'), '6月10日'),
        ('300308.SZ', '中际旭创', ('20260302', '20260512'), '6月12日'),
    ]
    
    article_params = {
        '688008.SH': {'tc': '6月2日', 'B': -0.518, 'm': 0.374, 'omega': 8.72, 'r2': 0.976},
        '399001.SZ': {'tc': '6月10日', 'B': -0.287, 'm': 0.426, 'omega': 9.23, 'r2': 0.968},
        '300308.SZ': {'tc': '6月12日', 'B': -0.624, 'm': 0.312, 'omega': 8.17, 'r2': 0.982},
    }
    
    all_results = {}
    
    for ts_code, name, period, article_tc in targets:
        print(f"\n{'#'*60}")
        print(f"# 复现：{name}({ts_code}) — 文章预测tc={article_tc}")
        print(f"# 分析区间：{period[0]}~{period[1]}")
        print(f"{'#'*60}")
        
        results = multi_window_fit(ts_code, end_date='20260512', specific_period=period)
        all_results[ts_code] = {
            'name': name,
            'article': article_params[ts_code],
            'our_results': results,
        }
        
        if results:
            r = results[0]
            p = r['params']
            ap = article_params[ts_code]
            print(f"\n  📊 对比：")
            print(f"  {'':>12} {'文章':>10} {'我们的':>10}")
            print(f"  {'tc':>12} {ap['tc']:>10} {p['tc_date']:>10}")
            print(f"  {'B':>12} {ap['B']:>10.4f} {p['B']:>10.4f}")
            print(f"  {'m':>12} {ap['m']:>10.3f} {p['m']:>10.3f}")
            print(f"  {'ω':>12} {ap['omega']:>10.2f} {p['omega']:>10.2f}")
            print(f"  {'R²':>12} {ap['r2']:>10.3f} {r['quality']['r_squared']:>10.3f}")
    
    # 生成对比报告
    report_lines = []
    report_lines.append("# LPPL模型复现报告 — 平头哥5/14文章")
    report_lines.append(f"复现日期: 2026-06-01")
    report_lines.append("")
    report_lines.append("## 文章核心预测")
    report_lines.append("三标的泡沫临界点集中在6月2日~6月12日，属系统性AI泡沫")
    report_lines.append("")
    report_lines.append("## 逐标的对比")
    report_lines.append("")
    
    for ts_code, data in all_results.items():
        name = data['name']
        ap = data['article']
        results = data['our_results']
        
        report_lines.append(f"### {name}({ts_code})")
        report_lines.append("")
        
        if results:
            r = results[0]
            p = r['params']
            q = r['quality']
            
            report_lines.append(f"| 参数 | 文章 | 复现 | 偏差 |")
            report_lines.append(f"|------|------|------|------|")
            report_lines.append(f"| tc | {ap['tc']} | {p['tc_date']} | — |")
            report_lines.append(f"| B | {ap['B']:.4f} | {p['B']:.4f} | {abs(p['B']-ap['B']):.4f} |")
            report_lines.append(f"| m | {ap['m']:.3f} | {p['m']:.3f} | {abs(p['m']-ap['m']):.3f} |")
            report_lines.append(f"| ω | {ap['omega']:.2f} | {p['omega']:.2f} | {abs(p['omega']-ap['omega']):.2f} |")
            report_lines.append(f"| R² | {ap['r2']:.3f} | {q['r_squared']:.3f} | {abs(q['r_squared']-ap['r2']):.3f} |")
            report_lines.append(f"| C/\\|B\\| | — | {r['constraints']['C_over_B']:.3f} | — |")
            report_lines.append(f"| 约束通过 | — | {r['interpretation']['confidence']} | — |")
            report_lines.append("")
            
            # 判断方向是否一致
            tc_match = '6月' in ap['tc'] and p['tc_date'].startswith('2026-06')
            direction_match = (p['B'] < 0 and ap['B'] < 0)
            
            if tc_match and direction_match:
                report_lines.append(f"✅ **方向一致**：均判断为看涨泡沫，tc落在6月")
            elif direction_match:
                report_lines.append(f"🟡 **方向一致但tc偏差**：均判断看涨泡沫，但tc日期不同")
            else:
                report_lines.append(f"❌ **方向不一致**：文章看涨泡沫，我们B={p['B']:.4f}")
        else:
            report_lines.append("❌ 拟合失败，无法对比")
        
        report_lines.append("")
    
    # 总结
    report_lines.append("## 复现结论")
    report_lines.append("")
    
    consistent = 0
    for ts_code, data in all_results.items():
        results = data['our_results']
        ap = data['article']
        if results:
            p = results[0]['params']
            tc_match = '6月' in ap['tc'] and p['tc_date'].startswith('2026-06')
            direction_match = (p['B'] < 0 and ap['B'] < 0)
            if tc_match and direction_match:
                consistent += 1
    
    report_lines.append(f"- {consistent}/{len(all_results)}个标的方向+时间窗口一致")
    report_lines.append("- LPPL模型对初始值和优化算法敏感，不同实现可能得到不同局部最优解")
    report_lines.append("- 关键判断：是否检测到看涨泡沫信号，tc是否落在6月")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("📌 LPPL模型复现仅供参考，不同优化算法和约束条件会导致参数差异。不构成投资建议。")
    
    report = '\n'.join(report_lines)
    
    # 保存
    report_file = os.path.join(OUTPUT_DIR, '20260512_LPPL复现_平头哥文章.md')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n\n{'='*60}")
    print("复现报告已保存")
    print(report)
    
    return all_results


def main():
    import sys
    
    mode = sys.argv[1] if len(sys.argv) > 1 else 'default'
    
    if mode == 'reproduce':
        # 复现文章
        reproduce_article()
    else:
        # 默认分析上证指数
        ts_code = mode if mode != 'default' else '000001.SH'
        end_date = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%Y%m%d')
        
        # 多窗口拟合
        results = multi_window_fit(ts_code, end_date)
        
        # 生成报告
        report = generate_report(results, ts_code, end_date)
        
        # 保存
        date_str = end_date
        report_file = os.path.join(OUTPUT_DIR, f'{date_str}_LPPL分析_{ts_code}.md')
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        
        # 保存JSON
        json_file = os.path.join(OUTPUT_DIR, f'{date_str}_LPPL分析_{ts_code}.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        
        print(report)
        print(f"\n报告已保存: {report_file}")
        print(f"数据已保存: {json_file}")


if __name__ == '__main__':
    main()
