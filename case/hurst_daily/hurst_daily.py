#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hurst 指数（DFA-1 去趋势波动分析）日报
===========================================
计算 5 大指数长短周期 Hurst 指数，给出金叉/死叉/临界信号。

标的：
- 沪深300（000300.SH）
- 中证1000（000852.SH）
- 科创50（000688.SH）
- 恒生指数（HSI）
- 恒生科技指数（HSTECH）

用法:
    python hurst_daily.py [trade_date]
    python hurst_daily.py 20260818
    python hurst_daily.py --skip-push
"""

from __future__ import annotations
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()


from trading_day_check import check_trading_day
check_trading_day()

import sys
import os
import json
import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()


# ===== 路径设置 =====
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_HUB_DIR = Path(_HUB_ROOT)
CARD_UTILS_DIR = Path(_cp("/Coze/Drive/蓁宝/所有对话/主对话"))
FEISHU_DIR = Path(_cp("/Coze/Drive/蓁宝/所有对话/主对话"))

sys.path.insert(0, str(DATA_HUB_DIR))
sys.path.insert(0, str(CARD_UTILS_DIR))
sys.path.insert(0, str(FEISHU_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

# ===== 常量 =====
# 飞书目标群走统一配置（DataHub 通知模块 app profile → 本应用 secrets），不在仓库写死
from notify_init import chat_id as _chat_id  # noqa: E402
CHAT_ID = _chat_id("stocks")  # AI股票消息群
STATE_DB = SCRIPT_DIR / "hurst_state.db"

# Hurst 标的配置
HURST_INDEXES = [
    {"name": "沪深300", "code": "000300.SH", "market": "cn"},
    {"name": "中证1000", "code": "000852.SH", "market": "cn"},
    {"name": "科创50", "code": "000688.SH", "market": "cn"},
    {"name": "恒生指数", "code": "HSI", "market": "hk"},
    {"name": "恒生科技", "code": "HSTECH", "market": "hk"},
]

# 短周期窗口与尺度
SHORT_WINDOW = 60
SHORT_SCALES = [4, 6, 8, 10, 15, 20, 30]

# 长周期窗口与尺度
LONG_WINDOW = 250
LONG_SCALES = [10, 15, 20, 30, 40, 60, 80, 100, 150]

# 波动率 Hurst 窗口（250日）
VOL_HURST_WINDOW = 250
VOL_HURST_SCALES = [10, 15, 20, 30, 40, 60, 80, 100, 150]

# 最少需要的数据天数
MIN_DATA_DAYS = 300


# ============================================================
# DFA-1 去趋势波动分析
# ============================================================

def dfa_1(series: np.ndarray, scales: List[int]) -> float:
    """
    计算 DFA-1 Hurst 指数。

    算法步骤：
    1. 去均值
    2. 累积求和 → 轮廓(profile)
    3. 对每个尺度 n，分段做 1 阶线性拟合
    4. 计算去趋势后的波动 F(n)
    5. log(n) ~ log(F(n)) 线性回归，斜率即为 H

    Args:
        series: 输入时间序列（1D numpy array）
        scales: 尺度列表（窗口大小）

    Returns:
        hurst: Hurst 指数 H 值
    """
    x = np.asarray(series, dtype=np.float64)
    n_total = len(x)

    # 1. 去均值
    y = x - np.mean(x)

    # 2. 累积求和（轮廓）
    Y = np.cumsum(y)

    # 3-6. 对每个尺度计算波动
    F_values = []
    valid_scales = []

    for n in scales:
        # n 最大不超过序列长度的 1/4
        if n > n_total // 4:
            continue
        if n < 4:
            continue

        # 分段数量（不重叠等长段）
        n_segments = n_total // n
        if n_segments < 2:
            continue

        # 将序列 reshape 为 n_segments x n
        trimmed_len = n_segments * n
        Y_trimmed = Y[:trimmed_len]
        Y_segments = Y_trimmed.reshape(n_segments, n)

        # 每段做 1 阶线性拟合，计算去趋势波动
        segment_fluctuations = np.zeros(n_segments)
        t = np.arange(n)

        for i in range(n_segments):
            seg = Y_segments[i]
            # 一阶线性拟合: y = a*t + b
            coeffs = np.polyfit(t, seg, 1)
            trend = np.polyval(coeffs, t)
            # 去趋势后的波动
            residual = seg - trend
            segment_fluctuations[i] = np.sqrt(np.mean(residual ** 2))

        # 所有段的平均波动
        F_n = np.mean(segment_fluctuations)
        if F_n > 0:
            F_values.append(F_n)
            valid_scales.append(n)

    if len(valid_scales) < 3:
        return np.nan

    # 8. log(n) ~ log(F(n)) 线性回归，斜率 = Hurst 指数
    log_n = np.log(np.array(valid_scales, dtype=np.float64))
    log_F = np.log(np.array(F_values, dtype=np.float64))

    # 线性回归
    slope, _ = np.polyfit(log_n, log_F, 1)
    return float(slope)


def calc_hurst_for_series(
    close_prices: np.ndarray,
) -> Dict[str, float]:
    """
    对收盘价序列计算短周期 Hurst、长周期 Hurst 和波动率 Hurst。

    DFA-1 直接对收益率序列（平稳序列）进行计算：
    - 价格 Hurst：基于对数收益率序列，衡量收益的长记忆性 / 趋势性
    - 波动率 Hurst：基于收益率绝对值序列，衡量波动率的长记忆性

    Args:
        close_prices: 收盘价序列（按时间升序），至少 250+ 个点

    Returns:
        dict: {
            'short_h': 短周期 Hurst (60日窗口),
            'long_h': 长周期 Hurst (250日窗口),
            'vol_h': 波动率 Hurst (250日窗口, 基于收益率绝对值),
            'short_h_prev': 前一日短H,
            'long_h_prev': 前一日长H,
        }
    """
    log_prices = np.log(np.asarray(close_prices, dtype=np.float64))
    n = len(log_prices)

    # 对数收益率序列（平稳序列，DFA 的输入）
    returns = np.diff(log_prices)  # 长度 = n-1
    abs_returns = np.abs(returns)

    result = {}

    # 短周期 Hurst（60日收益率窗口 = 61个价格点）—— 当前
    if len(returns) >= SHORT_WINDOW:
        short_series = returns[-SHORT_WINDOW:]
        short_scales = [s for s in SHORT_SCALES if s <= SHORT_WINDOW // 4]
        result['short_h'] = dfa_1(short_series, short_scales)
    else:
        result['short_h'] = np.nan

    # 短周期 Hurst —— 前一日（向前移一位）
    if len(returns) >= SHORT_WINDOW + 1:
        short_series_prev = returns[-(SHORT_WINDOW + 1):-1]
        short_scales = [s for s in SHORT_SCALES if s <= SHORT_WINDOW // 4]
        result['short_h_prev'] = dfa_1(short_series_prev, short_scales)
    else:
        result['short_h_prev'] = np.nan

    # 长周期 Hurst（250日收益率窗口 = 251个价格点）—— 当前
    if len(returns) >= LONG_WINDOW:
        long_series = returns[-LONG_WINDOW:]
        long_scales = [s for s in LONG_SCALES if s <= LONG_WINDOW // 4]
        result['long_h'] = dfa_1(long_series, long_scales)
    else:
        result['long_h'] = np.nan

    # 长周期 Hurst —— 前一日
    if len(returns) >= LONG_WINDOW + 1:
        long_series_prev = returns[-(LONG_WINDOW + 1):-1]
        long_scales = [s for s in LONG_SCALES if s <= LONG_WINDOW // 4]
        result['long_h_prev'] = dfa_1(long_series_prev, long_scales)
    else:
        result['long_h_prev'] = np.nan

    # 波动率 Hurst（250日窗口，收益率绝对值）
    if len(abs_returns) >= VOL_HURST_WINDOW:
        vol_series = abs_returns[-VOL_HURST_WINDOW:]
        vol_scales = [s for s in VOL_HURST_SCALES if s <= VOL_HURST_WINDOW // 4]
        result['vol_h'] = dfa_1(vol_series, vol_scales)
    else:
        result['vol_h'] = np.nan

    return result


# ============================================================
# 信号判定
# ============================================================

def determine_signal(
    short_h: float,
    long_h: float,
    short_h_prev: float,
    long_h_prev: float,
    vol_h: float,
    close: float,
    ma60: float,
    ma5: float,
    ma5_prev: float,
) -> Tuple[str, str, str]:
    """
    判定 Hurst 信号。

    优先级：临界 > 金叉/死叉 > 有序/退相

    Returns:
        (signal_type, signal_emoji, advice)
        signal_type: 'critical' | 'golden_cross' | 'death_cross' | 'orderly' | 'decoherence'
        signal_emoji: 🟠 临界 / 🟢 金叉 / 🔴 死叉 / 🟡 有序 / ⚪ 退相
        advice: 操作建议文字
    """
    # NaN 保护
    if any(np.isnan(v) for v in [short_h, long_h, vol_h]):
        return ('unknown', '⚪', '数据不足')

    # 金叉判定：短H上穿长H
    is_golden_cross = False
    if not np.isnan(short_h_prev) and not np.isnan(long_h_prev):
        if short_h_prev <= long_h_prev and short_h > long_h:
            if short_h > 0.55 and long_h > 0.5:
                is_golden_cross = True

    # 死叉判定：短H下穿长H + 至少2条附加条件
    is_death_cross = False
    if not np.isnan(short_h_prev) and not np.isnan(long_h_prev):
        if short_h_prev >= long_h_prev and short_h < long_h:
            if short_h < 0.55:
                # 统计满足的附加条件
                conditions_met = 0
                # 条件1：价格低于60日均线
                if not np.isnan(ma60) and close < ma60:
                    conditions_met += 1
                # 条件2：波动率 Hurst > 0.7
                if vol_h > 0.7:
                    conditions_met += 1
                # 条件3：短 H < 0.4
                if short_h < 0.4:
                    conditions_met += 1
                # 条件4：近5日价格重心下移（5日均线向下）
                if not np.isnan(ma5) and not np.isnan(ma5_prev) and ma5 < ma5_prev:
                    conditions_met += 1

                if conditions_met >= 2:
                    is_death_cross = True

    # 临界预警：短H > 0.7 且 波动率 Hurst > 0.7
    is_critical = (short_h > 0.7 and vol_h > 0.7)

    # 优先级：临界 > 金叉/死叉 > 有序/退相
    if is_critical:
        return ('critical', '🟠', '停止开新仓，减仓或买 Put')
    elif is_golden_cross:
        return ('golden_cross', '🟢', '顺势开仓，仓位可积极')
    elif is_death_cross:
        return ('death_cross', '🔴', '清仓或对冲')
    elif short_h > 0.55 and long_h > 0.5:
        return ('orderly', '🟡', '顺势持有')
    else:
        return ('decoherence', '⚪', '观望为主')


def signal_evidence(
    r: Dict,
) -> List[str]:
    """
    生成信号判定依据明细（用于详情折叠区展示）。

    Args:
        r: 单个指数的 Hurst 结果 dict

    Returns:
        list of str，每条是一个判定条件的满足情况
    """
    short_h = r.get('short_h', np.nan)
    long_h = r.get('long_h', np.nan)
    short_h_prev = r.get('short_h_prev', np.nan)
    long_h_prev = r.get('long_h_prev', np.nan)
    vol_h = r.get('vol_h', np.nan)
    signal_type = r.get('signal_type', '')
    close = r.get('close', np.nan)
    ma60 = r.get('ma60', np.nan)
    ma5 = r.get('ma5', np.nan)
    ma5_prev = r.get('ma5_prev', np.nan)

    lines = []

    def _fmt(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 'N/A'
        return f"{v:.3f}"

    # === 金叉条件 ===
    lines.append("**金叉条件**:")
    if not np.isnan(short_h_prev) and not np.isnan(long_h_prev):
        cross_up = short_h_prev <= long_h_prev and short_h > long_h
        status = "✅" if cross_up else "❌"
        lines.append(f"  {status} 短H上穿长H: 昨日({_fmt(short_h_prev)} vs {_fmt(long_h_prev)}) → 今日({_fmt(short_h)} vs {_fmt(long_h)})")
    else:
        lines.append("  ❓ 昨日数据不足，无法判断上穿")
    status = "✅" if short_h > 0.55 else "❌"
    lines.append(f"  {status} 短H > 0.55: {_fmt(short_h)}")
    status = "✅" if long_h > 0.5 else "❌"
    lines.append(f"  {status} 长H > 0.5: {_fmt(long_h)}")
    lines.append("")

    # === 死叉条件 ===
    lines.append("**死叉条件**:")
    if not np.isnan(short_h_prev) and not np.isnan(long_h_prev):
        cross_down = short_h_prev >= long_h_prev and short_h < long_h
        status = "✅" if cross_down else "❌"
        lines.append(f"  {status} 短H下穿长H: 昨日({_fmt(short_h_prev)} vs {_fmt(long_h_prev)}) → 今日({_fmt(short_h)} vs {_fmt(long_h)})")
    else:
        lines.append("  ❓ 昨日数据不足，无法判断下穿")
    status = "✅" if short_h < 0.55 else "❌"
    lines.append(f"  {status} 短H < 0.55: {_fmt(short_h)}")

    # 4条附加条件
    lines.append("  **附加条件 (需≥2条满足)**:")
    # 条件1
    if not np.isnan(ma60) and not np.isnan(close):
        cond1 = close < ma60
        status = "✅" if cond1 else "❌"
        lines.append(f"    {status} 价格低于60日均线: 当前{close:.2f} vs MA60={ma60:.2f}")
    else:
        lines.append("    ❓ 价格/均线数据不足")
    # 条件2
    cond2 = vol_h > 0.7
    status = "✅" if cond2 else "❌"
    lines.append(f"    {status} 波动率Hurst > 0.7: {_fmt(vol_h)}")
    # 条件3
    cond3 = short_h < 0.4
    status = "✅" if cond3 else "❌"
    lines.append(f"    {status} 短H < 0.4: {_fmt(short_h)}")
    # 条件4
    if not np.isnan(ma5) and not np.isnan(ma5_prev):
        cond4 = ma5 < ma5_prev
        status = "✅" if cond4 else "❌"
        direction = "向下" if cond4 else "向上"
        lines.append(f"    {status} 5日均线{direction}: 昨日MA5={_fmt(ma5_prev)} → 今日MA5={_fmt(ma5)}")
    else:
        lines.append("    ❓ 5日均线数据不足")
    lines.append("")

    # === 临界条件 ===
    lines.append("**临界预警条件**:")
    cond_c1 = short_h > 0.7
    cond_c2 = vol_h > 0.7
    status = "✅" if cond_c1 else "❌"
    lines.append(f"  {status} 短H > 0.7: {_fmt(short_h)}")
    status = "✅" if cond_c2 else "❌"
    lines.append(f"  {status} 波动率H > 0.7: {_fmt(vol_h)}")
    both_critical = cond_c1 and cond_c2
    lines.append(f"  → {'✅ 触发临界' if both_critical else '❌ 未触发'}")
    lines.append("")

    # === 最终结论 ===
    signal_label_map = {
        'golden_cross': '🟢 金叉',
        'death_cross': '🔴 死叉',
        'critical': '🟠 临界预警',
        'orderly': '🟡 有序状态',
        'decoherence': '⚪ 退相状态',
        'unknown': '⚪ 未知',
    }
    lines.append(f"**最终信号**: {signal_label_map.get(signal_type, signal_type)}")

    return lines


# ============================================================
# 风控标注
# ============================================================

def check_data_quality(
    close_prices: np.ndarray,
    window: int = 250,
) -> List[str]:
    """
    检查数据质量，标注异常。

    - 单日涨跌幅 > 9%（涨跌停断层）
    - 连续停牌超5日（用涨跌幅=0或成交量=0近似，这里只用价格判断）

    Args:
        close_prices: 收盘价序列
        window: 检查窗口长度

    Returns:
        list of warning strings
    """
    warnings = []
    n = len(close_prices)
    if n < 10:
        return warnings

    # 取最近 window 个点
    recent = close_prices[-min(window, n):]

    # 计算日涨跌幅
    pct_changes = np.diff(recent) / recent[:-1] * 100

    # 单日涨跌幅 > 9%
    big_moves = np.where(np.abs(pct_changes) > 9)[0]
    if len(big_moves) > 0:
        warnings.append(f"窗口内有{len(big_moves)}次单日涨跌超9%")

    # 连续零波动（近似停牌）—— 连续5天以上涨跌幅绝对值 < 0.01%
    zero_days = np.abs(pct_changes) < 0.01
    max_streak = 0
    current_streak = 0
    for z in zero_days:
        if z:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    if max_streak >= 5:
        warnings.append(f"连续{max_streak}日零波动（疑似停牌）")

    return warnings


# ============================================================
# 数据获取
# ============================================================

def _fetch_cn_index(code: str, end_date: str, min_days: int = 400) -> pd.DataFrame:
    """获取 A 股指数日线数据（走 data_hub）。"""
    from data_hub import get_index_daily

    # 计算起始日期（多取一些，至少 min_days 个交易日）
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=int(min_days * 1.8))
    start_date = start_dt.strftime("%Y%m%d")

    df = get_index_daily(ts_code=code, start_date=start_date, end_date=end_date)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()

    # 确保按日期升序
    if 'trade_date' in df.columns:
        df = df.sort_values('trade_date').reset_index(drop=True)

    return df


def _fetch_hk_index(code: str, end_date: str, min_days: int = 400) -> pd.DataFrame:
    """获取港股指数日线数据（走 akshare）。"""
    import akshare as ak

    try:
        df = ak.stock_hk_index_daily_sina(symbol=code)
        if df is None or df.empty:
            return pd.DataFrame()

        # 日期格式转换
        if 'date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            df = df.sort_values('trade_date').reset_index(drop=True)

        # 截断到 end_date
        df = df[df['trade_date'] <= end_date].reset_index(drop=True)

        # 确保有 close 列
        if 'close' not in df.columns:
            return pd.DataFrame()

        return df
    except Exception as e:
        print(f"  ⚠️ 港股指数 {code} 获取失败: {e}")
        return pd.DataFrame()


def fetch_hurst_data(
    index_config: Dict,
    end_date: str,
) -> pd.DataFrame:
    """
    获取单个指数的 Hurst 分析所需数据。

    Returns:
        DataFrame with columns: trade_date, close
    """
    name = index_config['name']
    code = index_config['code']
    market = index_config['market']

    if market == 'cn':
        df = _fetch_cn_index(code, end_date, min_days=MIN_DATA_DAYS)
    else:
        df = _fetch_hk_index(code, end_date, min_days=MIN_DATA_DAYS)

    if df.empty or 'close' not in df.columns or len(df) < 100:
        print(f"  ⚠️ {name} 数据不足（{len(df)}行）")
        return pd.DataFrame()

    # 统一提取 trade_date 和 close
    result = df[['trade_date', 'close']].copy()
    result['close'] = pd.to_numeric(result['close'], errors='coerce')
    result = result.dropna(subset=['close']).reset_index(drop=True)

    return result


# ============================================================
# 核心计算函数
# ============================================================

def calc_hurst_indicators(end_date: str) -> List[Dict]:
    """
    计算所有指数的 Hurst 指标及信号。

    Args:
        end_date: 截止日期 YYYYMMDD

    Returns:
        list of dict，每个指数一条，包含：
        - name: 指数名
        - code: 指数代码
        - short_h: 短周期H
        - long_h: 长周期H
        - short_long_diff: 短长差
        - vol_h: 波动率H
        - signal_type: 信号类型
        - signal_emoji: 信号emoji
        - advice: 操作建议
        - warnings: 风控警告列表
        - data_date: 最新数据日期
        - close: 最新收盘价
        - ma60: 60日均线
        - ma5: 5日均线
    """
    results = []

    for idx in HURST_INDEXES:
        name = idx['name']
        print(f"  📊 计算 {name} Hurst 指数...")

        try:
            df = fetch_hurst_data(idx, end_date)
            if df.empty or len(df) < SHORT_WINDOW + 10:
                print(f"    ⚠️ {name} 数据不足，跳过")
                results.append({
                    'name': name,
                    'code': idx['code'],
                    'short_h': np.nan,
                    'long_h': np.nan,
                    'short_long_diff': np.nan,
                    'vol_h': np.nan,
                    'signal_type': 'unknown',
                    'signal_emoji': '⚪',
                    'advice': '数据不足',
                    'warnings': ['数据不足'],
                    'data_date': '',
                    'close': np.nan,
                    'ma60': np.nan,
                    'ma5': np.nan,
                    'ma5_prev': np.nan,
                    'short_h_prev': np.nan,
                    'long_h_prev': np.nan,
                })
                continue

            close_prices = df['close'].values
            data_date = df['trade_date'].iloc[-1]

            # 计算 Hurst
            hurst_vals = calc_hurst_for_series(close_prices)
            short_h = hurst_vals['short_h']
            long_h = hurst_vals['long_h']
            vol_h = hurst_vals['vol_h']
            short_h_prev = hurst_vals.get('short_h_prev', np.nan)
            long_h_prev = hurst_vals.get('long_h_prev', np.nan)

            short_long_diff = short_h - long_h if not np.isnan(short_h) and not np.isnan(long_h) else np.nan

            # 计算均线
            close = close_prices[-1]
            ma60 = np.mean(close_prices[-60:]) if len(close_prices) >= 60 else np.nan
            ma5 = np.mean(close_prices[-5:]) if len(close_prices) >= 5 else np.nan
            ma5_prev = np.mean(close_prices[-6:-1]) if len(close_prices) >= 6 else np.nan

            # 信号判定
            signal_type, signal_emoji, advice = determine_signal(
                short_h=short_h,
                long_h=long_h,
                short_h_prev=short_h_prev,
                long_h_prev=long_h_prev,
                vol_h=vol_h,
                close=close,
                ma60=ma60,
                ma5=ma5,
                ma5_prev=ma5_prev,
            )

            # 风控检查
            warnings = check_data_quality(close_prices)

            results.append({
                'name': name,
                'code': idx['code'],
                'short_h': short_h,
                'long_h': long_h,
                'short_h_prev': short_h_prev,
                'long_h_prev': long_h_prev,
                'short_long_diff': short_long_diff,
                'vol_h': vol_h,
                'signal_type': signal_type,
                'signal_emoji': signal_emoji,
                'advice': advice,
                'warnings': warnings,
                'data_date': data_date,
                'close': close,
                'ma60': ma60,
                'ma5': ma5,
                'ma5_prev': ma5_prev,
            })

            print(f"    ✅ 短H={short_h:.3f}, 长H={long_h:.3f}, 波动H={vol_h:.3f}, 信号={signal_emoji}{signal_type}")

        except Exception as e:
            print(f"    ❌ {name} 计算失败: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                'name': name,
                'code': idx['code'],
                'short_h': np.nan,
                'long_h': np.nan,
                'short_long_diff': np.nan,
                'vol_h': np.nan,
                'signal_type': 'error',
                'signal_emoji': '⚪',
                'advice': f'计算错误: {e}',
                'warnings': ['计算异常'],
                'data_date': '',
                'close': np.nan,
                'ma60': np.nan,
                'ma5': np.nan,
                'ma5_prev': np.nan,
                'short_h_prev': np.nan,
                'long_h_prev': np.nan,
            })

    return results


# ============================================================
# 飞书卡片构建
# ============================================================

def build_hurst_card(
    date_str: str,
    hurst_results: List[Dict],
    title_suffix: str = "午盘",
) -> Dict:
    """
    构建 Hurst 指数日报飞书卡片。

    Args:
        date_str: 日期 YYYY-MM-DD
        hurst_results: calc_hurst_indicators 的返回结果
        title_suffix: 标题后缀（如 "午盘"、"收盘"）

    Returns:
        飞书卡片 dict
    """
    from feishu_card_utils import (
        build_card_header, build_section_header, build_markdown,
        build_hr, build_data_table, build_note, wrap_card,
    )

    # 确定数据日期（取最新有数据的日期）
    data_dates = [r['data_date'] for r in hurst_results if r['data_date']]
    if data_dates:
        latest_data_date = max(data_dates)
        # 格式化为 YYYY-MM-DD
        d = latest_data_date
        data_date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    else:
        data_date_str = date_str

    # 统计整体状态
    golden_count = sum(1 for r in hurst_results if r['signal_type'] == 'golden_cross')
    death_count = sum(1 for r in hurst_results if r['signal_type'] == 'death_cross')
    critical_count = sum(1 for r in hurst_results if r['signal_type'] == 'critical')
    orderly_count = sum(1 for r in hurst_results if r['signal_type'] == 'orderly')
    decoherence_count = sum(1 for r in hurst_results if r['signal_type'] == 'decoherence')

    # 卡片标题颜色：临界>死叉>金叉>有序>退相
    if critical_count > 0:
        template = 'orange'
    elif death_count > 0:
        template = 'red'
    elif golden_count > 0:
        template = 'green'
    elif orderly_count > 0:
        template = 'yellow'
    else:
        template = 'grey'

    header = build_card_header(
        f"📈 Hurst 指数日报 | {date_str} {title_suffix}",
        template=template,
    )

    elements = []

    # 数据日期说明
    elements.append(build_markdown(
        f"> **数据截止日期**: {data_date_str}  \n"
        f"> 基于 DFA-1 去趋势波动分析，短周期={SHORT_WINDOW}日，长周期={LONG_WINDOW}日"
    ))
    elements.append(build_hr())

    # 主表
    elements.append(build_section_header("📊 五大指数 Hurst 指标"))
    elements.append(build_hr())

    table_headers = ['指数', '短H', '长H', '短长差', '波动H', '信号', '操作建议']
    table_rows = []

    for r in hurst_results:
        name = r['name']
        warnings = r.get('warnings', [])

        # 格式化 H 值
        def _fmt_h(val):
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return 'N/A'
            return f"{val:.3f}"

        def _fmt_diff(val):
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return 'N/A'
            sign = '+' if val >= 0 else ''
            return f"{sign}{val:.3f}"

        short_h_str = _fmt_h(r['short_h'])
        long_h_str = _fmt_h(r['long_h'])
        diff_str = _fmt_diff(r['short_long_diff'])
        vol_h_str = _fmt_h(r['vol_h'])

        emoji = r.get('signal_emoji', '⚪')
        advice = r.get('advice', '')

        # 短长差着色
        diff_val = r.get('short_long_diff', np.nan)
        if not np.isnan(diff_val) if isinstance(diff_val, float) else False:
            if diff_val > 0.05:
                diff_str = f"<font color='green'>{diff_str}</font>"
            elif diff_val < -0.05:
                diff_str = f"<font color='red'>{diff_str}</font>"

        # 风控警告标注
        name_display = name
        if warnings:
            name_display = f"{name} ⚠️"

        table_rows.append([
            name_display,
            short_h_str,
            long_h_str,
            diff_str,
            vol_h_str,
            f"{emoji} {_signal_label(r['signal_type'])}",
            advice,
        ])

    elements.append(build_data_table(table_headers, table_rows))
    elements.append(build_hr())

    # === 每个指数的详情折叠区（交互式卡片） ===
    elements.append(build_section_header("🔍 各指数详情（点击展开）"))
    elements.append(build_hr())

    detail_lines = []
    for i, r in enumerate(hurst_results):
        name = r['name']
        emoji = r.get('signal_emoji', '⚪')
        signal_label = _signal_label(r['signal_type'])
        warnings = r.get('warnings', [])
        close = r.get('close', np.nan)
        ma60 = r.get('ma60', np.nan)
        ma5 = r.get('ma5', np.nan)
        ma5_prev = r.get('ma5_prev', np.nan)
        short_h_prev = r.get('short_h_prev', np.nan)
        long_h_prev = r.get('long_h_prev', np.nan)

        def _fh(v):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return 'N/A'
            return f"{v:.3f}"

        # 折叠区 summary 标题
        summary_title = f"{emoji} {name} — {signal_label}"
        if warnings:
            summary_title += " ⚠️"

        detail_lines.append(f"<details>")
        detail_lines.append(f"<summary>{summary_title}</summary>")
        detail_lines.append("")

        # 基本指标
        detail_lines.append("**基本指标**:")
        detail_lines.append(f"  - 短H (今日/昨日): **{_fh(r['short_h'])}** / {_fh(short_h_prev)}")
        detail_lines.append(f"  - 长H (今日/昨日): **{_fh(r['long_h'])}** / {_fh(long_h_prev)}")
        detail_lines.append(f"  - 短长差: **{_fh(r['short_long_diff'])}**")
        detail_lines.append(f"  - 波动率H: **{_fh(r['vol_h'])}**")
        detail_lines.append("")

        # 均线与价格
        detail_lines.append("**价格与均线**:")
        if not np.isnan(close) and not np.isnan(ma60):
            diff_pct = (close / ma60 - 1) * 100
            pos = "上方" if close > ma60 else "下方"
            detail_lines.append(f"  - 当前价: {close:.2f}")
            detail_lines.append(f"  - 60日均线: {ma60:.2f} ({pos}{abs(diff_pct):.2f}%)")
        else:
            detail_lines.append(f"  - 当前价: {close if not np.isnan(close) else 'N/A'}")
            detail_lines.append(f"  - 60日均线: {ma60 if not np.isnan(ma60) else 'N/A'}")

        if not np.isnan(ma5) and not np.isnan(ma5_prev):
            direction = "向上 ↑" if ma5 > ma5_prev else "向下 ↓"
            detail_lines.append(f"  - 5日均线: {ma5:.2f} ({direction}，昨{ma5_prev:.2f})")
        elif not np.isnan(ma5):
            detail_lines.append(f"  - 5日均线: {ma5:.2f}")
        detail_lines.append("")

        # 风控标注
        if warnings:
            detail_lines.append("**⚠️ 风控警告**:")
            for w in warnings:
                detail_lines.append(f"  - {w}")
            detail_lines.append("")

        # 信号判定依据
        evidence = signal_evidence(r)
        detail_lines.append("**📋 信号判定依据**:")
        for line in evidence:
            # 缩进一层
            detail_lines.append(f"  {line}" if line else "")
        detail_lines.append("")

        detail_lines.append("</details>")
        detail_lines.append("")

    elements.append(build_markdown("\n".join(detail_lines)))
    elements.append(build_hr())

    # 整体市场状态总结
    elements.append(build_section_header("📋 今日整体状态"))
    elements.append(build_hr())

    summary_parts = []
    if critical_count > 0:
        summary_parts.append(f"🟠 **临界预警**: {critical_count} 个")
    if golden_count > 0:
        summary_parts.append(f"🟢 **金叉信号**: {golden_count} 个")
    if death_count > 0:
        summary_parts.append(f"🔴 **死叉信号**: {death_count} 个")
    if orderly_count > 0:
        summary_parts.append(f"🟡 **有序状态**: {orderly_count} 个")
    if decoherence_count > 0:
        summary_parts.append(f"⚪ **退相状态**: {decoherence_count} 个")

    summary_text = "　".join(summary_parts)
    elements.append(build_markdown(summary_text))

    # 综合判断
    if critical_count >= 2:
        overall_text = "<font color='orange'>**⚠️ 多指数进入临界状态，市场系统性风险上升，建议全面减仓避险**</font>"
    elif golden_count >= 2 and death_count == 0:
        overall_text = "<font color='green'>**多指数金叉，市场趋势性增强，可顺势加仓**</font>"
    elif death_count >= 2:
        overall_text = "<font color='red'>**多指数死叉，趋势记忆性瓦解，建议防御为主**</font>"
    elif golden_count == 1 and death_count == 0 and critical_count == 0:
        overall_text = "🟡 局部机会显现，关注金叉品种的趋势延续性"
    elif death_count == 1 and golden_count == 0:
        overall_text = "🟡 局部走弱，规避死叉品种，等待信号明朗"
    else:
        overall_text = "⚪ 市场整体处于无序/震荡状态，观望为主"

    elements.append(build_markdown(f"\n**综合判断**: {overall_text}"))
    elements.append(build_hr())

    # 信号说明折叠区
    elements.append(build_collapsible_md("📖 信号说明", [
        "**🟢 金叉**: 短H上穿长H，短H>0.55且长H>0.5 → 市场从随机游走进入有序相，趋势刚形成",
        "**🔴 死叉**: 短H下穿长H，短H<0.55，且至少满足2条附加条件 → 趋势记忆性瓦解，相变退相",
        "**🟠 临界预警**: 短H>0.7 且 波动率H>0.7 → 临界慢化，系统濒临崩溃",
        "**🟡 有序状态**: 短H>0.55 且 长H>0.5，未触发金叉 → 趋势进行中",
        "**⚪ 退相状态**: 短H<0.5 或 长H<0.5 → 无序震荡，观望为主",
        "**优先级**: 临界 > 金叉/死叉 > 有序/退相",
        "**⚠️ 数据警告**: 窗口内有涨跌停断层或连续停牌，Hurst值可能失真",
    ]))

    # 底部备注
    elements.append(build_hr())
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    elements.append(build_note(
        f"算法：DFA-1 去趋势波动分析 | 短周期{SHORT_WINDOW}日 / 长周期{LONG_WINDOW}日 | "
        f"生成：{now_str} | 案例分析学习材料，不构成投资建议"
    ))

    return wrap_card(header, elements)


def _signal_label(signal_type: str) -> str:
    """信号类型 → 中文标签"""
    mapping = {
        'golden_cross': '金叉',
        'death_cross': '死叉',
        'critical': '临界',
        'orderly': '有序',
        'decoherence': '退相',
        'unknown': '未知',
        'error': '错误',
    }
    return mapping.get(signal_type, signal_type)


def build_collapsible_md(title: str, content_lines: List[str]) -> Dict:
    """构建折叠区块（markdown 内容），兼容 feishu_card_utils 中的 build_collapsible"""
    from feishu_card_utils import build_collapsible
    return build_collapsible(title, content_lines)


# ============================================================
# 状态库（幂等去重）
# ============================================================

def _init_state_db():
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hurst_daily_cache (
            trade_date TEXT PRIMARY KEY,
            card_json TEXT,
            pushed INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _is_cached(trade_date: str) -> bool:
    if not STATE_DB.exists():
        return False
    conn = sqlite3.connect(str(STATE_DB))
    row = conn.execute(
        "SELECT 1 FROM hurst_daily_cache WHERE trade_date=? AND pushed=1",
        (trade_date,)
    ).fetchone()
    conn.close()
    return row is not None


def _save_cache(trade_date: str, card_json: str):
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB))
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO hurst_daily_cache (trade_date, card_json, pushed, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
    """, (trade_date, card_json, now, now))
    conn.commit()
    conn.close()


# ============================================================
# 主流程
# ============================================================

def run_hurst_daily(
    trade_date: str,
    skip_push: bool = False,
    title_suffix: str = "午盘",
) -> Dict:
    """运行 Hurst 指数日报。

    Args:
        trade_date: 交易日期 YYYYMMDD
        skip_push: 是否跳过推送
        title_suffix: 标题后缀

    Returns:
        执行结果 dict
    """
    print(f"\n{'='*60}")
    print(f"📈 Hurst 指数日报 - {trade_date} ({title_suffix})")
    print(f"{'='*60}\n")

    # 1. 幂等检查
    _init_state_db()
    if _is_cached(trade_date) and not skip_push:
        print(f"✅ {trade_date} 已推送，跳过（幂等）")
        return {"status": "cached", "trade_date": trade_date}

    # 2. 计算 Hurst 指标
    print("🧮 计算 Hurst 指标...\n")
    hurst_results = calc_hurst_indicators(trade_date)

    # 3. 构建卡片
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    card = build_hurst_card(date_str, hurst_results, title_suffix=title_suffix)
    card_json = json.dumps(card, ensure_ascii=False)

    # 4. 推送
    if not skip_push:
        print(f"\n📤 推送飞书卡片到群 {CHAT_ID[:10]}...")
        from feishu_push import push_card_to_feishu
        ok = push_card_to_feishu(card_dict=card, chat_id=CHAT_ID)
        if ok:
            print("✅ 推送成功")
        else:
            print("❌ 推送失败，仍保存缓存")
    else:
        print("\n⏭️ 跳过推送（--skip-push）")

    # 5. 保存缓存
    if not skip_push:
        _save_cache(trade_date, card_json)

    # 6. 输出摘要
    print(f"\n{'='*60}")
    print(f"📋 Hurst 指数摘要:")
    for r in hurst_results:
        sh = f"{r['short_h']:.3f}" if not np.isnan(r['short_h']) else 'N/A'
        lh = f"{r['long_h']:.3f}" if not np.isnan(r['long_h']) else 'N/A'
        vh = f"{r['vol_h']:.3f}" if not np.isnan(r['vol_h']) else 'N/A'
        print(f"  {r['name']:>6}: 短H={sh} 长H={lh} 波动H={vh} {r['signal_emoji']} {r['advice']}")
    print(f"{'='*60}\n")

    return {
        "status": "success",
        "trade_date": trade_date,
        "hurst_results": hurst_results,
        "card_size": len(card_json),
    }


def main():
    parser = argparse.ArgumentParser(description="Hurst 指数日报")
    parser.add_argument("trade_date", nargs="?", default=None,
                        help="交易日期 YYYYMMDD，默认最近交易日")
    parser.add_argument("--skip-push", action="store_true",
                        help="跳过飞书推送（仅本地运行）")
    parser.add_argument("--force", action="store_true",
                        help="强制重新运行（忽略幂等缓存）")
    parser.add_argument("--title-suffix", default="午盘",
                        help="标题后缀，默认'午盘'")
    args = parser.parse_args()

    # 确定交易日期
    if args.trade_date:
        try:
            datetime.strptime(args.trade_date, "%Y%m%d")
            trade_date = args.trade_date
        except ValueError:
            print(f"  ⚠️ trade_date 参数 '{args.trade_date}' 格式非法，使用默认最近交易日")
            trade_date = None
    else:
        trade_date = None

    if not trade_date:
        # 找最近交易日
        try:
            td = datetime.now()
            for i in range(7):
                check_date = (td - timedelta(days=i)).strftime("%Y%m%d")
                check_td = datetime.strptime(check_date, "%Y%m%d")
                if check_td.weekday() < 5:
                    trade_date = check_date
                    break
            else:
                trade_date = datetime.now().strftime("%Y%m%d")
        except Exception:
            trade_date = datetime.now().strftime("%Y%m%d")

    # 强制模式：删除缓存
    if args.force and STATE_DB.exists():
        conn = sqlite3.connect(str(STATE_DB))
        conn.execute("DELETE FROM hurst_daily_cache WHERE trade_date=?", (trade_date,))
        conn.commit()
        conn.close()
        print(f"🔄 已清除 {trade_date} 的缓存（--force）")

    result = run_hurst_daily(trade_date, skip_push=args.skip_push, title_suffix=args.title_suffix)

    # CodeAct 提交结果
    try:
        sys.path.insert(0, './codeact/scripts')
        from codeact_sdk import CodeActSDK
        sdk = CodeActSDK()

        # 统计
        hr = result.get('hurst_results', [])
        golden = sum(1 for r in hr if r['signal_type'] == 'golden_cross')
        death = sum(1 for r in hr if r['signal_type'] == 'death_cross')
        critical = sum(1 for r in hr if r['signal_type'] == 'critical')

        if critical > 0:
            msg_prefix = "🟠"
        elif death > 0:
            msg_prefix = "🔴"
        elif golden > 0:
            msg_prefix = "🟢"
        else:
            msg_prefix = "🟡"

        msg = (
            f"{msg_prefix} Hurst指数日报 - {trade_date}\n"
            f"金叉{golden}个 / 死叉{death}个 / 临界{critical}个\n"
            f"{'已推送飞书群' if not args.skip_push else '未推送（--skip-push）'}"
        )

        sdk.submit_result(
            status="success",
            result_mode="display_only",
            message=msg,
            data=result,
        )
    except Exception as e:
        print(f"\n✅ 分析完成: {result.get('status', 'unknown')}")
        print(f"(CodeAct SDK不可用: {e})")


if __name__ == '__main__':
    main()
