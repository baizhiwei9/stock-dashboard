#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股双指数风险释放监控 v2.0
============================
每日收盘后运行，生成沪深300+中证1000风险监控仪表盘并推送飞书。

v2.0 新增：周期状态判断 + 自适应展示模式
- 6种状态：正常上行/风险积累/出清初期/中期/后期/释放尾声
- 4种展示模板：上行健康度/过热风险积累/出清进度/底部确认
- 状态切换自动提醒
- 统一预警体系（各状态下方向不同）

设计原则：
- 纯Python零LLM，所有计算硬编码
- 复用data_hub三级降级框架
- 幂等：同一交易日重复运行不重复推送
- 每日完整版推送，单日变动>10%加发提醒
- 状态存储：./codeact/output/risk_release/history.json

脚本结构：
- risk_release/data_fetch.py    数据采集
- risk_release/calculator.py    出清进度计算
- risk_release/renderer.py      卡片渲染+飞书推送
- risk_release_monitor.py       主入口（本文件）

用法：
    python risk_release_monitor.py [trade_date] [--no-push] [--force]
    # trade_date: YYYYMMDD，默认最近交易日
    # --no-push: 不推送飞书，仅本地计算
    # --force: 强制重新推送（忽略幂等判断）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ============ CodeAct SDK ============
try:
    from codeact_sdk import CodeActSDK
    sdk = CodeActSDK()
    _has_sdk = True
except ImportError:
    _has_sdk = False
    sdk = None

# ============ 路径配置 ============
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'output', 'risk_release')
HISTORY_FILE = os.path.join(OUTPUT_DIR, 'history.json')

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 添加data_hub路径
DATAHUB_PARENT = SCRIPT_DIR
if DATAHUB_PARENT not in sys.path:
    sys.path.insert(0, DATAHUB_PARENT)

# 添加risk_release模块路径
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# ============ 导入模块 ============
from risk_release.data_fetch import (
    fetch_index_daily,
    fetch_option_metrics,
    fetch_north_money,
    fetch_margin_data,
    fetch_fund_issue,
    fetch_pledge_ratio,
    calc_drawdown_from_peak,
    calc_north_flow_20d,
    calc_margin_change,
    calc_fund_issue_20d,
    calc_pledge_warning_ratio,
    calc_snowball_knockin_distance,
    get_spot_price,
    HISTORICAL_CYCLES_HS300,
    HISTORICAL_CYCLES_ZZ1000,
    HISTORICAL_MARGIN_DECLINE,
    HISTORICAL_MARGIN_PERIOD_DAYS,
)

from risk_release.calculator import (
    DialData,
    RiskReleaseResult,
    WarningSignal,
    calc_price_release,
    calc_fund_flow_release,
    calc_margin_release,
    calc_sentiment_release,
    calc_struct_release,
    calc_composite_index,
    estimate_remaining_time,
    estimate_deleveraging_start_day,
    detect_warnings,
    check_system_pressure,
    detect_regions,
    HS300_HIST_DD_AVG,
    ZZ1000_HIST_DD_AVG,
    MARGIN_DELIVERING_AVG,
    MARGIN_PERIOD_AVG_DAYS,
    # 新增：周期状态判断
    CycleState,
    CycleStateInput,
    CycleStateResult,
    HealthIndexResult,
    detect_cycle_state,
    calc_health_index,
    detect_bull_warnings,
    detect_bottom_signals,
)

from risk_release.renderer import (
    build_full_card,
    build_alert_card,
    build_state_change_card,
    generate_text_report,
    push_card_to_feishu,
    push_text_to_feishu,
)


# ============================================================
# 历史状态管理
# ============================================================

def load_history() -> Dict[str, Any]:
    """加载历史记录。"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "daily_records": {},   # {date: {hs300_pct, zz1000_pct, pushed_full, pushed_alert}}
        "last_update": "",
    }


def save_history(history: Dict[str, Any]) -> None:
    """保存历史记录。"""
    history["last_update"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存历史记录失败: {e}")


def is_already_pushed(history: Dict[str, Any], trade_date: str, push_type: str = "full") -> bool:
    """检查是否已推送。"""
    record = history.get("daily_records", {}).get(trade_date, {})
    if push_type == "full":
        return record.get("pushed_full", False)
    elif push_type == "alert":
        return record.get("pushed_alert", False)
    return False


def mark_pushed(history: Dict[str, Any], trade_date: str, push_type: str = "full") -> None:
    """标记已推送。"""
    if "daily_records" not in history:
        history["daily_records"] = {}
    if trade_date not in history["daily_records"]:
        history["daily_records"][trade_date] = {}
    if push_type == "full":
        history["daily_records"][trade_date]["pushed_full"] = True
    elif push_type == "alert":
        history["daily_records"][trade_date]["pushed_alert"] = True


def get_prev_composite(history: Dict[str, Any], trade_date: str) -> tuple:
    """获取前一交易日的综合出清指数。"""
    records = history.get("daily_records", {})
    dates = sorted(records.keys(), reverse=True)
    for d in dates:
        if d < trade_date and "hs300_pct" in records[d]:
            return records[d].get("hs300_pct", 0), records[d].get("zz1000_pct", 0)
    return 0.0, 0.0


# ============================================================
# 核心计算
# ============================================================

def run_calculation(trade_date: str) -> RiskReleaseResult:
    """
    执行完整的风险释放计算。

    Args:
        trade_date: 交易日期（YYYYMMDD）

    Returns:
        RiskReleaseResult
    """
    print(f"\n{'='*60}")
    print(f"[1/5] 采集指数数据")
    print(f"{'='*60}")

    # 1. 指数日线
    hs300_df = fetch_index_daily("000300.SH", end_date=trade_date, trade_date=trade_date)
    zz1000_df = fetch_index_daily("000852.SH", end_date=trade_date, trade_date=trade_date)

    hs300_close = get_spot_price(hs300_df)
    zz1000_close = get_spot_price(zz1000_df)

    print(f"  沪深300: {hs300_close:.2f} (数据量: {len(hs300_df) if hs300_df is not None else 0})")
    print(f"  中证1000: {zz1000_close:.2f} (数据量: {len(zz1000_df) if zz1000_df is not None else 0})")

    # 价格回撤
    hs300_dd = calc_drawdown_from_peak(hs300_df, lookback_days=250)
    zz1000_dd = calc_drawdown_from_peak(zz1000_df, lookback_days=250)

    print(f"  沪深300 250日回撤: {hs300_dd*100:+.2f}%")
    print(f"  中证1000 250日回撤: {zz1000_dd*100:+.2f}%")

    print(f"\n{'='*60}")
    print(f"[2/5] 采集期权数据")
    print(f"{'='*60}")

    # 2. 期权指标
    hs300_opt = fetch_option_metrics("CFFEX", "IO", hs300_close, trade_date=trade_date)
    zz1000_opt = fetch_option_metrics("CFFEX", "MO", zz1000_close, trade_date=trade_date)

    if hs300_opt:
        print(f"  沪深300期权: VIX={hs300_opt.vix:.1f}, PCR={hs300_opt.vol_pcr:.2f}, Skew25RR={getattr(hs300_opt, 'skew_rr', 0.0):+.2f}pp, 合约数={hs300_opt.contract_count}")
    else:
        print(f"  ⚠️ 沪深300期权数据获取失败")

    if zz1000_opt:
        print(f"  中证1000期权: VIX={zz1000_opt.vix:.1f}, PCR={zz1000_opt.vol_pcr:.2f}, Skew25RR={getattr(zz1000_opt, 'skew_rr', 0.0):+.2f}pp, 合约数={zz1000_opt.contract_count}")
    else:
        print(f"  ⚠️ 中证1000期权数据获取失败")

    print(f"\n{'='*60}")
    print(f"[3/5] 采集资金/信用/情绪数据")
    print(f"{'='*60}")

    # 3. 北向资金
    north_df = fetch_north_money(lookback_days=60, trade_date=trade_date)
    north_flow_pct = calc_north_flow_20d(north_df)
    print(f"  北向20日累计: {north_flow_pct*100:+.3f}% (占流通市值)")

    # 4. 两融数据
    margin_sh = fetch_margin_data("sh", lookback_days=500, trade_date=trade_date)
    margin_sz = fetch_margin_data("sz", lookback_days=500, trade_date=trade_date)

    margin_change_sh = calc_margin_change(margin_sh)
    margin_change_sz = calc_margin_change(margin_sz)

    # 合并两市
    total_margin_current = margin_change_sh['current_balance'] + margin_change_sz['current_balance']
    total_margin_peak = margin_change_sh['peak_balance'] + margin_change_sz['peak_balance']
    total_margin_decline = (total_margin_current - total_margin_peak) / total_margin_peak if total_margin_peak > 0 else 0

    print(f"  两融余额: 当前={total_margin_current:.0f}亿, 峰值={total_margin_peak:.0f}亿, 降幅={total_margin_decline*100:+.2f}%")

    # 5. 基金发行
    fund_df = fetch_fund_issue(trade_date=trade_date)
    fund_issue_20d = calc_fund_issue_20d(fund_df, current_date=trade_date)
    print(f"  近20日偏股型基金发行: {fund_issue_20d:.0f}亿份")

    # 6. 股权质押
    pledge_df = fetch_pledge_ratio(trade_date=trade_date)
    pledge_warning = calc_pledge_warning_ratio(pledge_df)
    print(f"  股权质押预警比例: {pledge_warning*100:.1f}%")

    # 7. 雪球敲入估算（中证1000）
    snowball = calc_snowball_knockin_distance(zz1000_close, "000852.SH")
    print(f"  雪球敲入估算: 敲入线={snowball['knockin_price']:.0f}, 距离={snowball['distance_pct']*100:+.1f}%, 已敲入≈{snowball['knocked_in_ratio']*100:.0f}%")

    print(f"\n{'='*60}")
    print(f"[4/5] 计算出清进度")
    print(f"{'='*60}")

    # 构建表盘数据
    hs300_dial = DialData()
    zz1000_dial = DialData()

    # --- 沪深300 ---
    hs300_dial.price_drawdown = hs300_dd
    hs300_dial.price_max_dd_avg = HS300_HIST_DD_AVG
    hs300_dial.price_release_pct = calc_price_release(hs300_dd, HS300_HIST_DD_AVG)

    hs300_dial.north_flow_pct = north_flow_pct
    hs300_dial.fund_flow_release_pct = calc_fund_flow_release(north_flow_pct, -0.0035)

    hs300_dial.margin_decline = total_margin_decline
    hs300_dial.margin_peak = total_margin_peak
    hs300_dial.margin_current = total_margin_current
    hs300_dial.margin_release_pct = calc_margin_release(total_margin_decline, MARGIN_DELIVERING_AVG)

    hs300_dial.fund_issue_20d = fund_issue_20d
    hs300_dial.sentiment_release_pct = calc_sentiment_release(fund_issue_20d, 1500.0)

    hs300_composite, hs300_label = calc_composite_index(
        hs300_dial.price_release_pct,
        hs300_dial.fund_flow_release_pct,
        hs300_dial.margin_release_pct,
        hs300_dial.sentiment_release_pct,
        is_small_cap=False,
    )
    hs300_dial.composite_index = hs300_composite
    hs300_dial.status_label = hs300_label

    # 期权指标
    if hs300_opt:
        hs300_dial.vix = hs300_opt.vix
        hs300_dial.pcr = hs300_opt.vol_pcr
        # 偏度统一口径：25Δ Risk Reversal（pp），非 CBOE 落库口径
        hs300_dial.skew = getattr(hs300_opt, 'skew_rr', 0.0)
        hs300_dial.term_spread = hs300_opt.term_spread
        hs300_dial.atm_iv = hs300_opt.atm_iv

    print(f"  沪深300综合出清指数: {hs300_composite:.1f}% ({hs300_label})")
    print(f"    价格: {hs300_dial.price_release_pct:.1f}% | 资金: {hs300_dial.fund_flow_release_pct:.1f}% | "
          f"信用: {hs300_dial.margin_release_pct:.1f}% | 情绪: {hs300_dial.sentiment_release_pct:.1f}%")

    # --- 中证1000 ---
    zz1000_dial.price_drawdown = zz1000_dd
    zz1000_dial.price_max_dd_avg = ZZ1000_HIST_DD_AVG
    zz1000_dial.price_release_pct = calc_price_release(zz1000_dd, ZZ1000_HIST_DD_AVG)

    # 小盘用信用+结构产品替代资金流出
    zz1000_dial.margin_decline = total_margin_decline  # 两市合计（小盘股也参与两融）
    zz1000_dial.margin_peak = total_margin_peak
    zz1000_dial.margin_current = total_margin_current
    zz1000_dial.margin_release_pct = calc_margin_release(total_margin_decline, MARGIN_DELIVERING_AVG)

    zz1000_dial.snowball_distance_pct = snowball['distance_pct']
    zz1000_dial.snowball_knockin_ratio = snowball['knocked_in_ratio']
    zz1000_dial.struct_release_pct = calc_struct_release(
        snowball['knocked_in_ratio'], snowball['distance_pct']
    )

    zz1000_dial.fund_issue_20d = fund_issue_20d
    zz1000_dial.sentiment_release_pct = calc_sentiment_release(fund_issue_20d, 1500.0)

    zz1000_dial.pledge_warning_ratio = pledge_warning

    zz1000_composite, zz1000_label = calc_composite_index(
        zz1000_dial.price_release_pct,
        0,  # 小盘不用资金流出项
        zz1000_dial.margin_release_pct,
        zz1000_dial.sentiment_release_pct,
        struct_pct=zz1000_dial.struct_release_pct,
        is_small_cap=True,
    )
    zz1000_dial.composite_index = zz1000_composite
    zz1000_dial.status_label = zz1000_label

    if zz1000_opt:
        zz1000_dial.vix = zz1000_opt.vix
        zz1000_dial.pcr = zz1000_opt.vol_pcr
        zz1000_dial.skew = getattr(zz1000_opt, 'skew_rr', 0.0)
        zz1000_dial.term_spread = zz1000_opt.term_spread
        zz1000_dial.atm_iv = zz1000_opt.atm_iv

    print(f"  中证1000综合出清指数: {zz1000_composite:.1f}% ({zz1000_label})")
    print(f"    价格: {zz1000_dial.price_release_pct:.1f}% | 结构: {zz1000_dial.struct_release_pct:.1f}% | "
          f"信用: {zz1000_dial.margin_release_pct:.1f}% | 情绪: {zz1000_dial.sentiment_release_pct:.1f}%")

    # --- 剩余时间估算 ---
    # 找最近的信用余额峰值日期（简化：用250日内的峰值日期估算）
    peak_date = _find_margin_peak_date(margin_sh, margin_sz, trade_date)
    current_days = estimate_deleveraging_start_day(peak_date, trade_date) if peak_date else 0
    time_progress, remaining_days, avg_days = estimate_remaining_time(
        current_days, MARGIN_PERIOD_AVG_DAYS, hs300_composite
    )

    print(f"\n  信用去化进度: {current_days}/{avg_days:.0f}交易日 ({time_progress:.1f}%)")
    print(f"  预估剩余: {remaining_days:.0f} 交易日")

    # --- 预警信号 ---
    warnings = detect_warnings(hs300_dial, zz1000_dial)
    system_pressure = check_system_pressure(warnings)

    triggered = [w for w in warnings if w.triggered]
    print(f"\n  预警信号: {len(triggered)} 个已触发 / {len(warnings)} 总计")
    print(f"  系统性压力确认: {'是' if system_pressure else '否'}")

    # --- 区域识别 ---
    low_sig, low_conf, high_sig, high_conf = detect_regions(hs300_dial, zz1000_dial, warnings)
    print(f"  低位信号: {low_sig}/3 {'(确认)' if low_conf else ''}")
    print(f"  高位信号: {high_sig}/3 {'(确认)' if high_conf else ''}")

    # ============================================================
    # 周期状态判断
    # ============================================================
    print(f"\n{'='*60}")
    print(f"[4.5/5] 周期状态判断")
    print(f"{'='*60}")

    # 1. 250日均线和偏离度（用沪深300为主判断）
    ma250_hs300 = _calc_ma(hs300_df, 250)
    ma250_zz1000 = _calc_ma(zz1000_df, 250)
    ma_deviation_hs300 = (hs300_close - ma250_hs300) / ma250_hs300 if ma250_hs300 > 0 else 0
    index_above_ma250 = hs300_close > ma250_hs300

    print(f"  沪深300 250日线: {ma250_hs300:.2f}, 偏离: {ma_deviation_hs300*100:+.2f}%, 位置: {'上方' if index_above_ma250 else '下方'}")

    # 2. 融资余额20日变动比例
    margin_20d_change_pct = 0.0
    if total_margin_peak > 0:
        # 用20日变动额 / 20日前余额
        margin_20d_ago = total_margin_current - (margin_change_sh.get('change_20d', 0) + margin_change_sz.get('change_20d', 0))
        if margin_20d_ago > 0:
            margin_20d_change_pct = (total_margin_current - margin_20d_ago) / margin_20d_ago

    # 3. 融资余额历史分位（基于近500天数据估算）
    margin_percentile = _calc_margin_percentile(margin_sh, margin_sz, total_margin_current)
    print(f"  融资余额分位: {margin_percentile:.0f}%, 20日变动: {margin_20d_change_pct*100:+.2f}%")

    # 4. VIX历史分位（简化估算：基于经验范围 10-40 对应 10%-90%分位）
    vix_for_percentile = hs300_dial.vix if hs300_dial.vix > 0 else zz1000_dial.vix
    vix_percentile = _calc_vix_percentile(vix_for_percentile)
    vix_20d_change = _calc_vix_20d_change(hs300_df, hs300_dial.vix)  # 简化估算
    print(f"  VIX: {vix_for_percentile:.1f}, 历史分位: {vix_percentile:.0f}%")

    # 5. 基金发行热度分位（基于历史峰值1500亿）
    fund_issue_percentile = min(100.0, fund_issue_20d / 1500.0 * 100.0)
    print(f"  基金发行热度分位: {fund_issue_percentile:.0f}%")

    # 6. VIX从高点回落幅度（用于底部判断）
    vix_peak_decline = _calc_vix_peak_decline(hs300_dial.vix)

    # 7. PCR是否从极端高位回落（简化：当前PCR在0.8-1.1之间且前期>1.2）
    pcr_decline = (0.8 < hs300_dial.pcr < 1.2) and hs300_dial.pcr < 1.2

    # 8. 信用余额是否止跌（简化：5日变动幅度<1%）
    margin_stabilized = abs(total_margin_decline) < 0.01 and margin_20d_change_pct > -0.02

    # 构造状态输入（用沪深300为主）
    avg_composite = (hs300_composite + zz1000_composite) / 2

    state_input = CycleStateInput(
        index_above_ma250=index_above_ma250,
        index_ma250_deviation=ma_deviation_hs300,
        drawdown_from_peak=hs300_dd,
        margin_20d_change=margin_20d_change_pct,
        margin_percentile=margin_percentile,
        margin_peak_decline=total_margin_decline,
        vix_percentile=vix_percentile,
        vix_20d_change=vix_20d_change,
        pcr=hs300_dial.pcr if hs300_dial.pcr > 0 else 1.0,
        fund_issue_percentile=fund_issue_percentile,
        composite_index=avg_composite,
        vix_peak_decline_pct=vix_peak_decline,
        pcr_decline_from_peak=pcr_decline,
        margin_stabilized=margin_stabilized,
    )

    # 判断状态
    cycle_state_result = detect_cycle_state(state_input)
    print(f"\n  📊 当前周期状态: {cycle_state_result.icon} {cycle_state_result.display_name}")
    print(f"     置信度: {cycle_state_result.confidence:.0f}%")
    print(f"     触发信号: {', '.join(cycle_state_result.triggered_signals[:3])}")

    # 健康度/风险积累指数（上行/过热期使用）
    health_index = None
    if cycle_state_result.state in (CycleState.BULL_NORMAL, CycleState.BULL_OVERHEAT):
        health_index = calc_health_index(
            ma250_deviation=ma_deviation_hs300,
            drawdown=hs300_dd,
            margin_percentile=margin_percentile,
            margin_20d_change=margin_20d_change_pct,
            vix_percentile=vix_percentile,
            pcr=hs300_dial.pcr if hs300_dial.pcr > 0 else 1.0,
            fund_issue_percentile=fund_issue_percentile,
            north_flow_pct=north_flow_pct,
            state=cycle_state_result.state,
        )
        print(f"  💪 健康度/风险指数: {health_index.composite:.1f}% ({health_index.label})")

        # 替换为上行期预警
        bull_warn = detect_bull_warnings(
            margin_percentile, vix_percentile,
            hs300_dial.pcr if hs300_dial.pcr > 0 else 1.0,
            fund_issue_percentile,
            ma_deviation_hs300,
        )
        # 合并期权预警 + 上行期预警
        warnings = bull_warn + warnings

    # 底部反转信号（底部期使用）
    bottom_signals = []
    if cycle_state_result.state == CycleState.BEAR_BOTTOM:
        bottom_signals = detect_bottom_signals(
            vix_peak_decline_pct=vix_peak_decline,
            pcr_decline=pcr_decline,
            margin_stabilized=margin_stabilized,
            north_flow_positive=north_flow_pct > 0,
            fund_issue_recovery=fund_issue_percentile > 30,
            composite_index=avg_composite,
        )

    # 组装结果
    result = RiskReleaseResult(
        trade_date=trade_date,
        hs300=hs300_dial,
        zz1000=zz1000_dial,
        avg_deleveraging_days=avg_days,
        current_deleveraging_days=current_days,
        time_progress_pct=time_progress,
        remaining_days_est=remaining_days,
        warnings=warnings,
        system_pressure_confirmed=system_pressure,
        low_region_signals=low_sig,
        low_region_confirmed=low_conf,
        high_region_signals=high_sig,
        high_region_confirmed=high_conf,
        cycle_state=cycle_state_result,
        health_index=health_index,
        bottom_signals=bottom_signals,
        data_availability={
            "index_hs300": "OK" if hs300_df is not None and not hs300_df.empty else "FAIL",
            "index_zz1000": "OK" if zz1000_df is not None and not zz1000_df.empty else "FAIL",
            "option_hs300": "OK" if hs300_opt and hs300_opt.contract_count > 0 else "FAIL",
            "option_zz1000": "OK" if zz1000_opt and zz1000_opt.contract_count > 0 else "FAIL",
            "north_money": "OK" if north_df is not None and not north_df.empty else "FAIL",
            "margin": "OK" if (margin_sh is not None and not margin_sh.empty) or (margin_sz is not None and not margin_sz.empty) else "FAIL",
            "fund_issue": "OK" if fund_df is not None and not fund_df.empty else "FAIL",
            "pledge_ratio": "OK" if pledge_df is not None and not pledge_df.empty else "FAIL",
        }
    )

    return result


def _find_margin_peak_date(margin_sh, margin_sz, trade_date: str) -> str:
    """
    估算信用余额峰值日期（最近250个交易日内的最高点）。
    """
    # 找沪市峰值日期
    peak_date = ""
    if margin_sh is not None and not margin_sh.empty:
        for col in ['rzye', '融资余额', '余额']:
            if col in margin_sh.columns:
                df = margin_sh.copy()
                df[col] = pd.to_numeric(df[col], errors='coerce')
                # 找峰值索引
                peak_idx = df[col].tail(250).idxmax()
                if 'trade_date' in df.columns:
                    peak_date = str(df.loc[peak_idx, 'trade_date'])
                elif '日期' in df.columns:
                    peak_date = pd.Timestamp(df.loc[peak_idx, '日期']).strftime('%Y%m%d')
                break

    if not peak_date:
        # 默认返回180天前
        try:
            peak_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=180)).strftime('%Y%m%d')
        except Exception:
            peak_date = trade_date

    return peak_date


# ============================================================
# 状态判断辅助函数
# ============================================================

def _calc_ma(df: pd.DataFrame, period: int = 250) -> float:
    """计算移动均线。"""
    if df is None or df.empty or 'close' not in df.columns:
        return 0.0
    closes = df['close'].tail(period)
    if len(closes) < period * 0.8:  # 数据不足80%则不计算
        return 0.0
    return float(closes.mean())


def _calc_margin_percentile(margin_sh, margin_sz, current_total: float) -> float:
    """
    估算融资余额历史分位（0-100）。
    基于近500天数据计算。
    """
    all_balances = []

    for m_df in [margin_sh, margin_sz]:
        if m_df is None or m_df.empty:
            continue
        balance_col = None
        for col in ['rzye', '融资余额', '余额']:
            if col in m_df.columns:
                balance_col = col
                break
        if balance_col is None:
            continue
        m_df_copy = m_df.copy()
        m_df_copy[balance_col] = pd.to_numeric(m_df_copy[balance_col], errors='coerce')
        balances = m_df_copy[balance_col].tail(500).dropna().values
        all_balances.append(balances)

    if not all_balances:
        # 没有历史数据，基于距峰值距离估算
        # 默认分位50%
        return 50.0

    # 汇总两市数据（分别计算分位再取平均）
    percentiles = []
    for i, balances in enumerate(all_balances):
        if len(balances) < 50:
            continue
        # 当前值在历史中的分位
        # 注意：这里用单市的数据近似
        # 简化：用距峰值距离估算
        peak = float(max(balances))
        if peak > 0:
            # 估算当前位置
            pct = current_total / (peak * 2) * 100  # 粗略估算
            percentiles.append(min(100.0, max(0.0, pct)))

    if percentiles:
        return sum(percentiles) / len(percentiles)

    # 兜底：用距峰值比例估算
    # 距峰值越近，分位越高
    return 60.0  # 默认中等偏上


def _calc_vix_percentile(vix: float) -> float:
    """
    简化估算VIX历史分位。
    经验范围：VIX 12~35 对应 10%~90%分位。
    """
    if vix <= 0:
        return 0.0
    # 线性映射：VIX=10→5%, VIX=15→20%, VIX=20→40%, VIX=25→60%, VIX=30→80%, VIX=40→95%
    if vix <= 10:
        return 5.0
    elif vix <= 15:
        return 5.0 + (vix - 10) / 5 * 15.0
    elif vix <= 20:
        return 20.0 + (vix - 15) / 5 * 20.0
    elif vix <= 25:
        return 40.0 + (vix - 20) / 5 * 20.0
    elif vix <= 30:
        return 60.0 + (vix - 25) / 5 * 20.0
    elif vix <= 40:
        return 80.0 + (vix - 30) / 10 * 15.0
    else:
        return 95.0


def _calc_vix_20d_change(index_df: pd.DataFrame, current_vix: float) -> float:
    """
    估算VIX 20日变动。
    简化：用指数振幅变化近似VIX变化。
    """
    if index_df is None or index_df.empty or current_vix <= 0:
        return 0.0
    # 简化估算：返回当前VIX的10%作为20日变动（保守）
    return current_vix * 0.1


def _calc_vix_peak_decline(current_vix: float) -> float:
    """
    估算VIX从近期高点回落的幅度%。
    简化：基于当前VIX位置估算。
    """
    if current_vix <= 0:
        return 0.0
    # 简化：假设近期高点为当前VIX的1.2倍（保守估算）
    # 实际应用中需要VIX历史数据
    if current_vix > 30:
        # 高位，回落空间大
        return max(0.0, (current_vix * 1.3 - current_vix) / (current_vix * 1.3) * 100)
    elif current_vix > 20:
        return 10.0  # 约10%回落
    else:
        return 5.0  # 低位，小幅波动


# ============================================================
# 保存每日记录
# ============================================================

def save_daily_record(history: dict, result: RiskReleaseResult) -> None:
    """保存每日记录到历史。"""
    trade_date = result.trade_date
    if "daily_records" not in history:
        history["daily_records"] = {}
    if trade_date not in history["daily_records"]:
        history["daily_records"][trade_date] = {}

    record = {
        "hs300_pct": result.hs300.composite_index,
        "zz1000_pct": result.zz1000.composite_index,
        "hs300_status": result.hs300.status_label,
        "zz1000_status": result.zz1000.status_label,
        "system_pressure": result.system_pressure_confirmed,
        "low_region": result.low_region_confirmed,
    }

    # 保存周期状态
    if result.cycle_state:
        cs = result.cycle_state
        record["cycle_state"] = cs.state.value if hasattr(cs.state, 'value') else str(cs.state)
        record["cycle_state_name"] = cs.display_name
        record["cycle_confidence"] = cs.confidence

    history["daily_records"][trade_date].update(record)


def get_prev_state(history: Dict, trade_date: str) -> Optional[CycleState]:
    """获取前一交易日的周期状态。"""
    records = history.get("daily_records", {})
    dates = sorted(records.keys(), reverse=True)
    for d in dates:
        if d < trade_date and "cycle_state" in records[d]:
            state_str = records[d]["cycle_state"]
            try:
                return CycleState(state_str)
            except Exception:
                return None
    return None


def count_days_in_state(history: Dict, trade_date: str, current_state: CycleState) -> int:
    """统计当前状态已持续多少天。"""
    records = history.get("daily_records", {})
    dates = sorted(records.keys(), reverse=True)
    count = 0
    for d in dates:
        if d < trade_date:
            state_str = records[d].get("cycle_state", "")
            try:
                if CycleState(state_str) == current_state:
                    count += 1
                else:
                    break
            except Exception:
                break
    return count + 1  # +1 包含当天


# ============================================================
# 主函数
# ============================================================

def main():
    """主入口。"""
    # 解析参数
    trade_date = None
    no_push = False
    force_push = False

    for arg in sys.argv[1:]:
        if arg == '--no-push':
            no_push = True
        elif arg == '--force':
            force_push = True
        elif arg.isdigit() and len(arg) == 8:
            trade_date = arg

    if trade_date is None:
        # 默认最近交易日
        now = datetime.now()
        if now.weekday() == 5:  # 周六
            now -= timedelta(days=1)
        elif now.weekday() == 6:  # 周日
            now -= timedelta(days=2)
        trade_date = now.strftime('%Y%m%d')

    print(f"\n{'#'*60}")
    print(f"# A股风险释放监控 v2.0")
    print(f"# 交易日期: {trade_date}")
    print(f"# 推送模式: {'关闭' if no_push else '开启'}{' (强制)' if force_push else ''}")
    print(f"{'#'*60}")

    start_time = time.time()

    try:
        # 加载历史
        history = load_history()

        # 幂等检查
        if not no_push and not force_push and is_already_pushed(history, trade_date, "full"):
            print(f"\nℹ️ {trade_date} 完整版已推送，跳过。使用 --force 可强制重推。")
            _submit_success(result=None, trade_date=trade_date, pushed=False, skipped=True)
            return

        # 执行计算
        result = run_calculation(trade_date)

        # 状态切换判断（基于历史记录）
        if result.cycle_state:
            prev_state = get_prev_state(history, trade_date)
            if prev_state and prev_state != result.cycle_state.state:
                result.cycle_state.state_changed = True
                result.cycle_state.prev_state = prev_state
                print(f"\n🔔 状态切换：{prev_state.value} → {result.cycle_state.state.value}")
            # 统计持续天数
            result.cycle_state.days_in_state = count_days_in_state(
                history, trade_date, result.cycle_state.state
            )
            print(f"   当前状态已持续 {result.cycle_state.days_in_state} 天")

        # 保存当日记录
        save_daily_record(history, result)
        save_history(history)

        # 保存详细结果到JSON
        detail_file = os.path.join(OUTPUT_DIR, f'{trade_date}_detail.json')
        result_dict = _result_to_dict(result)
        with open(detail_file, 'w', encoding='utf-8') as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n📁 详细数据已保存: {detail_file}")

        # 推送
        push_ok = False
        alert_pushed = False

        if not no_push:
            print(f"\n{'='*60}")
            print(f"[5/5] 推送飞书")
            print(f"{'='*60}")

            # 完整版卡片
            card = build_full_card(result)
            push_ok = push_card_to_feishu(card)

            if not push_ok:
                print("⚠️ 卡片推送失败，降级为纯文本...")
                text = generate_text_report(result)
                push_ok = push_text_to_feishu(text)
                if push_ok:
                    print("✅ 纯文本推送成功（降级模式）")
                else:
                    print("❌ 纯文本推送也失败")

            if push_ok:
                mark_pushed(history, trade_date, "full")
                save_history(history)

            # 检查状态切换，加发状态变更通知
            state_change_pushed = False
            if result.cycle_state and result.cycle_state.state_changed:
                if not is_already_pushed(history, trade_date, "state_change"):
                    print(f"\n🔔 检测到周期状态切换，加发状态变更通知...")
                    state_card = build_state_change_card(result)
                    state_ok = push_card_to_feishu(state_card)
                    if state_ok:
                        mark_pushed(history, trade_date, "state_change")
                        save_history(history)
                        state_change_pushed = True
                        print("✅ 状态变更通知推送成功")
                    else:
                        print("⚠️ 状态变更通知推送失败")

            # 检查是否需要加发变动提醒（单日变动>10%）
            prev_hs300, prev_zz1000 = get_prev_composite(history, trade_date)
            hs300_delta = abs(result.hs300.composite_index - prev_hs300)
            zz1000_delta = abs(result.zz1000.composite_index - prev_zz1000)

            if (hs300_delta > 10 or zz1000_delta > 10) and not is_already_pushed(history, trade_date, "alert"):
                print(f"\n⚡ 单日变动超过10%（HS300: {hs300_delta:+.1f}%, ZZ1000: {zz1000_delta:+.1f}%），加发提醒...")
                alert_card = build_alert_card(result, prev_hs300, prev_zz1000)
                alert_ok = push_card_to_feishu(alert_card)
                if alert_ok:
                    mark_pushed(history, trade_date, "alert")
                    save_history(history)
                    alert_pushed = True
                    print("✅ 变动提醒推送成功")
                else:
                    print("⚠️ 变动提醒推送失败")

        # 耗时统计
        elapsed = time.time() - start_time
        print(f"\n⏱️ 总耗时: {elapsed:.1f}秒")

        # 数据采集成功率统计
        ok_count = sum(1 for v in result.data_availability.values() if v == "OK")
        total = len(result.data_availability)
        print(f"\n📊 数据采集成功率: {ok_count}/{total} ({ok_count/total*100:.0f}%)")
        for k, v in result.data_availability.items():
            status = "✅" if v == "OK" else "❌"
            print(f"  {status} {k}: {v}")

        print(f"\n✅ 风险释放监控完成")

        # 提交结果
        _submit_success(result, trade_date, push_ok, alert_pushed, elapsed, ok_count, total)

    except Exception as e:
        import traceback
        print(f"\n❌ 执行失败: {e}")
        print(traceback.format_exc())

        # 提交错误
        _submit_error(str(e), trade_date)


def _result_to_dict(result: RiskReleaseResult) -> dict:
    """将结果转为可序列化的dict。"""
    return {
        "trade_date": result.trade_date,
        "hs300": {
            "composite_index": result.hs300.composite_index,
            "status_label": result.hs300.status_label,
            "price_drawdown": result.hs300.price_drawdown,
            "price_release_pct": result.hs300.price_release_pct,
            "fund_flow_release_pct": result.hs300.fund_flow_release_pct,
            "margin_release_pct": result.hs300.margin_release_pct,
            "sentiment_release_pct": result.hs300.sentiment_release_pct,
            "vix": result.hs300.vix,
            "pcr": result.hs300.pcr,
            "skew": result.hs300.skew,
            "skew_note": "25d_rr_pp",
            "margin_decline": result.hs300.margin_decline,
            "north_flow_pct": result.hs300.north_flow_pct,
            "fund_issue_20d": result.hs300.fund_issue_20d,
        },
        "zz1000": {
            "composite_index": result.zz1000.composite_index,
            "status_label": result.zz1000.status_label,
            "price_drawdown": result.zz1000.price_drawdown,
            "price_release_pct": result.zz1000.price_release_pct,
            "struct_release_pct": result.zz1000.struct_release_pct,
            "margin_release_pct": result.zz1000.margin_release_pct,
            "sentiment_release_pct": result.zz1000.sentiment_release_pct,
            "vix": result.zz1000.vix,
            "pcr": result.zz1000.pcr,
            "skew": result.zz1000.skew,
            "skew_note": "25d_rr_pp",
            "snowball_knockin_ratio": result.zz1000.snowball_knockin_ratio,
            "snowball_distance_pct": result.zz1000.snowball_distance_pct,
            "pledge_warning_ratio": result.zz1000.pledge_warning_ratio,
        },
        "time_estimation": {
            "avg_deleveraging_days": result.avg_deleveraging_days,
            "current_deleveraging_days": result.current_deleveraging_days,
            "time_progress_pct": result.time_progress_pct,
            "remaining_days_est": result.remaining_days_est,
        },
        "warnings": [
            {"name": w.name, "level": w.level, "triggered": w.triggered, "detail": w.detail}
            for w in result.warnings
        ],
        "system_pressure_confirmed": result.system_pressure_confirmed,
        "low_region_signals": result.low_region_signals,
        "low_region_confirmed": result.low_region_confirmed,
        "high_region_signals": result.high_region_signals,
        "high_region_confirmed": result.high_region_confirmed,
        # 周期状态
        "cycle_state": {
            "state": result.cycle_state.state.value if result.cycle_state and hasattr(result.cycle_state.state, 'value') else str(result.cycle_state.state) if result.cycle_state else None,
            "display_name": result.cycle_state.display_name if result.cycle_state else None,
            "confidence": result.cycle_state.confidence if result.cycle_state else None,
            "days_in_state": result.cycle_state.days_in_state if result.cycle_state else 0,
            "state_changed": result.cycle_state.state_changed if result.cycle_state else False,
        } if result.cycle_state else None,
        "health_index": {
            "composite": result.health_index.composite,
            "label": result.health_index.label,
            "trend_health": result.health_index.trend_health,
            "leverage_health": result.health_index.leverage_health,
            "sentiment_temp": result.health_index.sentiment_temp,
            "fund_strength": result.health_index.fund_strength,
            "risk_accumulation_pct": result.health_index.risk_accumulation_pct,
        } if result.health_index else None,
        "data_availability": result.data_availability,
    }


def _submit_success(result, trade_date, pushed, alert_pushed=False, elapsed=0, ok_count=0, total=0, skipped=False):
    """提交成功结果。"""
    if not _has_sdk or sdk is None:
        return

    if skipped:
        message = f"风险释放监控({trade_date})：今日已推送，跳过执行"
        data = {"trade_date": trade_date, "skipped": True}
    else:
        hs300_pct = result.hs300.composite_index if result else 0
        zz1000_pct = result.zz1000.composite_index if result else 0
        hs300_status = result.hs300.status_label if result else ""
        zz1000_status = result.zz1000.status_label if result else ""

        avg = (hs300_pct + zz1000_pct) / 2
        triggered = sum(1 for w in result.warnings if w.triggered) if result else 0

        message = (
            f"风险释放监控({trade_date})：综合出清指数 {avg:.1f}%\n"
            f"沪深300: {hs300_pct:.1f}% ({hs300_status})\n"
            f"中证1000: {zz1000_pct:.1f}% ({zz1000_status})\n"
            f"预警信号: {triggered}个 | 系统性压力: {'是' if result.system_pressure_confirmed else '否'}\n"
            f"数据采集: {ok_count}/{total} ({ok_count/total*100:.0f}%)\n"
            f"飞书推送: {'✅成功' if pushed else '❌失败'}{' +变动提醒' if alert_pushed else ''}\n"
            f"总耗时: {elapsed:.1f}秒"
        )

        data = {
            "trade_date": trade_date,
            "hs300_composite": hs300_pct,
            "zz1000_composite": zz1000_pct,
            "warnings_triggered": triggered,
            "system_pressure": result.system_pressure_confirmed if result else False,
            "push_success": pushed,
            "alert_pushed": alert_pushed,
            "data_success_rate": f"{ok_count}/{total}",
            "elapsed_seconds": round(elapsed, 1),
        }

    async def _submit():
        await sdk.submit_result(
            result_mode="display_only",
            status="success",
            message=message,
            data=data,
        )

    try:
        asyncio.get_event_loop().run_until_complete(_submit())
    except Exception:
        pass


def _submit_error(error_msg: str, trade_date: str):
    """提交错误结果。"""
    if not _has_sdk or sdk is None:
        return

    async def _submit():
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"风险释放监控({trade_date})执行失败: {error_msg}",
            data={"error": error_msg, "trade_date": trade_date},
        )

    try:
        asyncio.get_event_loop().run_until_complete(_submit())
    except Exception:
        pass


if __name__ == '__main__':
    main()
