#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
曼宁资金流相变概念轮动系统

方法核心:
  成交量 x 价格变化方向 -> 资金流(Money Flow) -> 相变检测:
    1. 资金流强度(MFI): 成交量 x 收益率方向, 衡量资金净流入/流出
    2. 累积资金流(CMF): 累积资金流比率, 检测持续流入/流出
    3. 资金流相变(Phase Transition): 检测资金流方向的突变点
       - 用滚动窗口均值差分检测相变
       - 相变点 = 资金流方向从流入转流出或反向
    4. 资金流动量(MFM): 资金流的变化率, 衡量资金加速/减速
  -> 结合MA40趋势过滤 -> 输出机会/风险信号
"""
import pandas as pd
import numpy as np
import sys

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

MA_PERIOD = 40
MOM_PERIOD = 20
FLOW_WINDOW = 10       # 资金流滚动窗口
PHASE_WINDOW = 20      # 相变检测窗口(前后对比)
FLOW_THRESHOLD = 0.0   # 资金流正负阈值

def load_data():
    returns = pd.read_pickle(f'{OUT_DIR}/concept_returns.pkl')
    close = pd.read_pickle(f'{OUT_DIR}/concept_close.pkl')
    volume = pd.read_pickle(f'{OUT_DIR}/concept_volume.pkl')
    return returns, close, volume

def compute_money_flow(returns, volume):
    """计算资金流: 成交量 x 收益率方向
    正收益 x 成交量 = 资金流入
    负收益 x 成交量 = 资金流出
    """
    # 资金流 = sign(return) * volume * |return|
    # 即: 涨的时候成交量算流入, 跌的时候算流出, 幅度按|return|加权
    signed_return = np.sign(returns) * np.abs(returns)
    money_flow = signed_return * volume
    return money_flow

def compute_cmf(money_flow, volume, window=10):
    """累积资金流比率 (Chaikin Money Flow)
    CMF = sum(money_flow, window) / sum(volume, window)
    > 0 = 资金净流入, < 0 = 资金净流出
    """
    mf_sum = money_flow.rolling(window=window, min_periods=1).sum()
    vol_sum = volume.rolling(window=window, min_periods=1).sum()
    vol_sum = vol_sum.replace(0, np.nan)
    cmf = mf_sum / vol_sum
    return cmf.fillna(0)

def detect_phase_transition(cmf, window=20):
    """检测资金流相变
    相变 = 前window日均值与后window日均值的符号反转
    正->负 = 流出相变(风险信号)
    负->正 = 流入相变(机会信号)
    """
    forward_avg = cmf.rolling(window=window, min_periods=1).mean()
    # 后向均值: 用shift模拟"之前window日"
    backward_avg = forward_avg.shift(window)
    
    phase = pd.DataFrame(index=cmf.index, columns=cmf.columns, dtype=float)
    for col in cmf.columns:
        fwd = forward_avg[col]
        bwd = backward_avg[col]
        # 相变强度 = 前后均值之差
        diff = fwd - bwd
        phase[col] = diff
    
    return phase

def compute_flow_momentum(money_flow, window=10):
    """资金流动量: 资金流的变化率"""
    mf_ma = money_flow.rolling(window=window, min_periods=1).mean()
    mf_prev = mf_ma.shift(window)
    mfm = (mf_ma - mf_prev) / (mf_prev.abs() + 1e-10)
    return mfm.replace([np.inf, -np.inf], 0).fillna(0)

def compute_flow_persistence(cmf, window=20):
    """资金流持续性: 最近window日中CMF>0的比例"""
    return (cmf > 0).rolling(window=window, min_periods=1).mean()

def compute_ma(close, period=40):
    return close.rolling(window=period, min_periods=1).mean()

def compute_momentum(close, period=20):
    return close.pct_change(periods=period) * 100

def main():
    print("=" * 110, file=sys.stderr)
    print("曼宁资金流相变概念轮动系统 (37概念版)", file=sys.stderr)
    print("=" * 110, file=sys.stderr)

    returns, close, volume = load_data()
    concept_names = list(returns.columns)
    N = len(concept_names)
    T = len(returns)

    print(f"数据: {T}交易日 x {N}概念, {returns.index[0].strftime('%Y-%m-%d')}~{returns.index[-1].strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"参数: MA{MA_PERIOD}, 动量{MOM_PERIOD}日, 资金流窗口{FLOW_WINDOW}, 相变窗口{PHASE_WINDOW}", file=sys.stderr)

    # ===================== Step 1: 资金流计算 =====================
    print(f"\n[1/5] 资金流计算...", file=sys.stderr)
    
    # 对齐returns和volume的索引
    vol_aligned = volume.reindex(returns.index).ffill().fillna(0)
    ret_aligned = returns.fillna(0)
    
    money_flow = compute_money_flow(ret_aligned, vol_aligned)
    cmf = compute_cmf(money_flow, vol_aligned, FLOW_WINDOW)
    phase = detect_phase_transition(cmf, PHASE_WINDOW)
    mfm = compute_flow_momentum(money_flow, FLOW_WINDOW)
    persistence = compute_flow_persistence(cmf, PHASE_WINDOW)

    print(f"  资金流矩阵: {money_flow.shape}", file=sys.stderr)
    print(f"  CMF范围: [{cmf.values.min():.4f}, {cmf.values.max():.4f}]", file=sys.stderr)
    
    # 当前CMF分布
    latest_cmf = cmf.iloc[-1]
    inflow_count = (latest_cmf > 0).sum()
    outflow_count = (latest_cmf < 0).sum()
    print(f"  最新CMF: {inflow_count}个净流入, {outflow_count}个净流出", file=sys.stderr)

    # ===================== Step 2: 相变检测 =====================
    print(f"\n[2/5] 资金流相变检测...", file=sys.stderr)
    
    latest_phase = phase.iloc[-1]
    latest_persistence = persistence.iloc[-1]
    latest_mfm = mfm.iloc[-1]
    
    # 统计相变
    inflow_transition = 0  # 负->正 (流入相变)
    outflow_transition = 0  # 正->负 (流出相变)
    for col in concept_names:
        pv = float(latest_phase[col]) if not np.isnan(latest_phase[col]) else 0
        if pv > 0.001:
            inflow_transition += 1
        elif pv < -0.001:
            outflow_transition += 1
    
    print(f"  流入相变(负->正): {inflow_transition}个概念", file=sys.stderr)
    print(f"  流出相变(正->负): {outflow_transition}个概念", file=sys.stderr)

    # ===================== Step 3: 趋势+综合信号 =====================
    print(f"\n[3/5] 生成综合信号...", file=sys.stderr)
    
    ma = compute_ma(close, MA_PERIOD)
    mom = compute_momentum(close, MOM_PERIOD)
    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_mom = mom.iloc[-1]

    # CMF历史趋势(最近5日)
    cmf_trend = {}
    for name in concept_names:
        recent_cmf = cmf[name].tail(5).values
        if len(recent_cmf) >= 2:
            cmf_trend[name] = 'rising' if recent_cmf[-1] > recent_cmf[0] else 'falling'
        else:
            cmf_trend[name] = 'stable'

    concept_signals = []
    for name in concept_names:
        c_close = float(latest_close[name]) if not np.isnan(latest_close[name]) else 0
        c_ma = float(latest_ma[name]) if not np.isnan(latest_ma[name]) else 0
        c_mom = float(latest_mom[name]) if not np.isnan(latest_mom[name]) else 0
        above_ma = c_close > c_ma if c_ma > 0 else False
        ma_gap = ((c_close / c_ma - 1) * 100) if c_ma > 0 else 0
        
        c_cmf = float(latest_cmf[name]) if not np.isnan(latest_cmf[name]) else 0
        c_phase = float(latest_phase[name]) if not np.isnan(latest_phase[name]) else 0
        c_persist = float(latest_persistence[name]) if not np.isnan(latest_persistence[name]) else 0
        c_mfm = float(latest_mfm[name]) if not np.isnan(latest_mfm[name]) else 0
        c_trend = cmf_trend.get(name, 'stable')

        # 信号评分
        # 趋势分
        trend_score = 2 if above_ma else -2
        # 动量分
        if c_mom > 5: mom_score = 2
        elif c_mom > 0: mom_score = 1
        elif c_mom < -5: mom_score = -2
        else: mom_score = -1
        # CMF分: 净流入加分, 净流出扣分
        if c_cmf > 0.1: cmf_score = 2  # 强净流入
        elif c_cmf > 0: cmf_score = 1
        elif c_cmf < -0.1: cmf_score = -2  # 强净流出
        elif c_cmf < 0: cmf_score = -1
        else: cmf_score = 0
        # 相变分: 流入相变+2, 流出相变-2
        if c_phase > 0.002: phase_score = 2  # 流入相变
        elif c_phase > 0: phase_score = 1
        elif c_phase < -0.002: phase_score = -2  # 流出相变
        elif c_phase < 0: phase_score = -1
        else: phase_score = 0
        # 持续性分: 高持续性(>0.6)=持续流入+1, 低持续性(<0.4)=不可靠-1
        if c_persist > 0.6: persist_score = 1
        elif c_persist < 0.4: persist_score = -1
        else: persist_score = 0
        # 资金流动量分
        if c_mfm > 0.5: mfm_score = 1  # 资金加速流入
        elif c_mfm < -0.5: mfm_score = -1  # 资金加速流出
        else: mfm_score = 0

        total_score = trend_score + mom_score + cmf_score + phase_score + persist_score + mfm_score

        if total_score >= 4: eval_label = '机会'
        elif total_score >= 2: eval_label = '偏多'
        elif total_score >= 0: eval_label = '中性'
        elif total_score >= -2: eval_label = '偏空'
        else: eval_label = '风险'

        concept_signals.append({
            'name': name, 'total_score': total_score, 'eval': eval_label,
            'close': round(c_close, 3), 'ma40': round(c_ma, 3),
            'above_ma40': above_ma, 'ma40_gap': round(ma_gap, 2),
            'mom20': round(c_mom, 2),
            'cmf': round(c_cmf, 4), 'phase': round(c_phase, 4),
            'persistence': round(c_persist, 2), 'mfm': round(c_mfm, 4),
            'cmf_trend': c_trend,
            'trend_score': trend_score, 'mom_score': mom_score,
            'cmf_score': cmf_score, 'phase_score': phase_score,
            'persist_score': persist_score, 'mfm_score': mfm_score,
        })

    concept_signals.sort(key=lambda x: x['total_score'], reverse=True)

    # ===================== Step 4: 输出 =====================
    print(f"[4/5] 输出结果...", file=sys.stderr)

    print("\n" + "=" * 130)
    print(f"曼宁资金流相变概念轮动分析 (数据截止: {returns.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 130)

    # 系统级状态
    print(f"\n--- 曼宁资金流系统级状态 ---")
    print(f"  最新CMF分布: {inflow_count}个净流入, {outflow_count}个净流出")
    print(f"  流入相变(负->正): {inflow_transition}个概念")
    print(f"  流出相变(正->负): {outflow_transition}个概念")
    avg_cmf = float(latest_cmf.mean())
    print(f"  全概念平均CMF: {avg_cmf:.4f} ({'整体净流入' if avg_cmf > 0 else '整体净流出'})")
    avg_persist = float(latest_persistence.mean())
    print(f"  全概念平均持续性: {avg_persist:.2f} ({'偏流入' if avg_persist > 0.5 else '偏流出'})")

    # 概念信号表
    print(f"\n{'='*130}")
    print("概念板块 曼宁资金流 信号表 (按综合评分降序)")
    print("=" * 130)
    print(f"{'排名':<4} {'概念':<14} {'评分':<5} {'评价':<5} {'MA40':<4} {'偏离%':<7} {'20日动量':<8} {'CMF':<7} {'相变':<7} {'持续':<5} {'动量':<7} {'趋势':<7} {'趋':<3} {'动':<3} {'CMF':<4} {'相':<3} {'持':<3} {'流'}")
    print("-" * 130)

    markers = {'机会': '★', '偏多': '▲', '中性': '─', '偏空': '▼', '风险': '✕'}

    for i, s in enumerate(concept_signals):
        m = markers.get(s['eval'], '')
        ma_s = 'Y' if s['above_ma40'] else 'N'
        print(f"{i+1:<4} {s['name']:<14} {s['total_score']:<+5} {s['eval']:<5}{m} "
              f"{ma_s:<4} {s['ma40_gap']:<7.2f} {s['mom20']:<8.2f} "
              f"{s['cmf']:<7.4f} {s['phase']:<7.4f} {s['persistence']:<5.2f} {s['mfm']:<7.4f} {s['cmf_trend']:<7} "
              f"{s['trend_score']:<+3} {s['mom_score']:<+3} {s['cmf_score']:<+4} {s['phase_score']:<+3} {s['persist_score']:<+3} {s['mfm_score']:<+3}")

    # 强弱分布
    print(f"\n{'='*130}")
    print("概念板块强弱分布:")
    for label in ['机会', '偏多', '中性', '偏空', '风险']:
        names = [s['name'] for s in concept_signals if s['eval'] == label]
        if names:
            print(f"  {label} ({len(names)}个): {', '.join(names)}")

    # 资金流相变分析
    print(f"\n{'='*130}")
    print("资金流相变分析 (流入相变 vs 流出相变):")
    print("-" * 130)
    inflow_trans = [s for s in concept_signals if s['phase'] > 0.002]
    outflow_trans = [s for s in concept_signals if s['phase'] < -0.002]
    
    print(f"  流入相变({len(inflow_trans)}个) - 资金从流出转流入, 机会信号:")
    for s in inflow_trans[:10]:
        print(f"    {s['name']:<14} 相变={s['phase']:+.4f}  CMF={s['cmf']:+.4f}  持续={s['persistence']:.2f}  评分={s['total_score']:+d}")
    
    print(f"\n  流出相变({len(outflow_trans)}个) - 资金从流入转流出, 风险信号:")
    for s in outflow_trans[:10]:
        print(f"    {s['name']:<14} 相变={s['phase']:+.4f}  CMF={s['cmf']:+.4f}  持续={s['persistence']:.2f}  评分={s['total_score']:+d}")

    # CMF排名
    print(f"\n{'='*130}")
    print("CMF资金流排名 (Top10净流入 / Bottom10净流出):")
    print("-" * 130)
    cmf_sorted = sorted(concept_signals, key=lambda x: x['cmf'], reverse=True)
    print(f"  Top10净流入:")
    for s in cmf_sorted[:10]:
        print(f"    {s['name']:<14} CMF={s['cmf']:+.4f}  持续={s['persistence']:.2f}  评分={s['total_score']:+d}  评价={s['eval']}")
    print(f"\n  Bottom10净流出:")
    for s in cmf_sorted[-10:]:
        print(f"    {s['name']:<14} CMF={s['cmf']:+.4f}  持续={s['persistence']:.2f}  评分={s['total_score']:+d}  评价={s['eval']}")

    # 液冷专题
    yeleng = [s for s in concept_signals if s['name'] == '液冷概念']
    if yeleng:
        s = yeleng[0]
        print(f"\n{'='*130}")
        print(f"液冷概念 曼宁资金流 专题:")
        print(f"-" * 130)
        print(f"  评价: {s['eval']} (评分{s['total_score']:+d})")
        print(f"  MA40: {'线上' if s['above_ma40'] else '线下'} (偏离{s['ma40_gap']:.2f}%)")
        print(f"  20日动量: {s['mom20']:+.2f}%")
        print(f"  CMF: {s['cmf']:+.4f} ({'净流入' if s['cmf'] > 0 else '净流出'})")
        print(f"  相变: {s['phase']:+.4f} ({'流入相变' if s['phase'] > 0 else '流出相变' if s['phase'] < 0 else '无相变'})")
        print(f"  持续性: {s['persistence']:.2f} ({'持续流入' if s['persistence'] > 0.6 else '持续流出' if s['persistence'] < 0.4 else '不稳定'})")
        print(f"  资金流动量: {s['mfm']:+.4f} ({'加速流入' if s['mfm'] > 0.5 else '加速流出' if s['mfm'] < -0.5 else '稳定'})")
        print(f"  CMF趋势: {s['cmf_trend']}")

    # 机会与风险总结
    print(f"\n{'='*130}")
    print("曼宁资金流机会与风险总结:")
    print("-" * 130)
    opps = [s for s in concept_signals if s['eval'] in ['机会', '偏多']]
    risks = [s for s in concept_signals if s['eval'] in ['风险', '偏空']]
    neutrals = [s for s in concept_signals if s['eval'] == '中性']

    print(f"\n  系统状态: 平均CMF={avg_cmf:.4f}, 平均持续性={avg_persist:.2f}")
    print(f"  流入相变{inflow_transition}个, 流出相变{outflow_transition}个")
    
    print(f"\n  机会区({len(opps)}个):")
    for s in opps:
        reasons = []
        if s['above_ma40']: reasons.append(f"MA40线上({s['ma40_gap']:.1f}%)")
        if s['cmf'] > 0: reasons.append(f"CMF净流入({s['cmf']:+.4f})")
        if s['phase'] > 0: reasons.append(f"流入相变({s['phase']:+.4f})")
        if s['persistence'] > 0.6: reasons.append(f"持续流入({s['persistence']:.2f})")
        if s['mom20'] > 0: reasons.append(f"动量+{s['mom20']:.1f}%")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")

    print(f"\n  风险区({len(risks)}个):")
    for s in risks:
        reasons = []
        if not s['above_ma40']: reasons.append(f"MA40线下({s['ma40_gap']:.1f}%)")
        if s['cmf'] < 0: reasons.append(f"CMF净流出({s['cmf']:+.4f})")
        if s['phase'] < 0: reasons.append(f"流出相变({s['phase']:+.4f})")
        if s['persistence'] < 0.4: reasons.append(f"持续流出({s['persistence']:.2f})")
        if s['mom20'] < 0: reasons.append(f"动量{s['mom20']:.1f}%")
        print(f"    {s['name']:<14} 评分{s['total_score']:+d}  {' | '.join(reasons)}")

    print(f"\n  中性区({len(neutrals)}个): {', '.join(s['name'] for s in neutrals)}")

    print(f"\n{'='*130}")
    print("评分: 趋势(MA40±2) + 动量(±1~±2) + CMF(±1~±2) + 相变(±1~±2) + 持续性(±1) + 资金流动量(±1)")
    print("评价: 机会(≥4) | 偏多(2~3) | 中性(0~1) | 偏空(-1~-2) | 风险(≤-3)")
    print("=" * 130)

if __name__ == '__main__':
    main()
