#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
波动率微笑图表模块

为 vol_monitor_daily.py 生成波动率微笑（smile）图表：
  - 每个标的一个子图，共6个
  - 近月 call IV / put IV 曲线
  - 远月 call IV / put IV 曲线（叠加对比）
  - 水平安全区间带
  - ATM 标记
  - 偏度方向标注（检测 KCB 等反向 skew）
"""

import os
import math
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

log = logging.getLogger("vol_smile")

# ──────────────────────────── 字体 ────────────────────────────
def _setup_font():
    """设置中文字体"""
    for fp in [
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/wqy-microhei/wqy-microhei.ttc',
        '/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    ]:
        if os.path.exists(fp):
            return FontProperties(fname=fp)
    return FontProperties()

FONT_PROP = _setup_font()


# ──────────────────────────── 数据收集 ────────────────────────────

def collect_smile_data(okey: str, chain_key: str, spot: float,
                       get_realtime_chain, get_daily_chain,
                       standardize, calc_iv, trade_date: str = "") -> Optional[dict]:
    """
    收集某个标的的波动率微笑数据。
    返回: {
        "near": {"calls": [(strike, iv), ...], "puts": [(strike, iv), ...], "expiry": "YYYYMMDD", "dte": int},
        "far":  {"calls": [(strike, iv), ...], "puts": [(strike, iv), ...], "expiry": "YYYYMMDD", "dte": int},
        "spot": float,
    }
    """
    chain = None
    price_source = None

    # 1) 实时链
    try:
        rt = get_realtime_chain(chain_key)
        if rt and rt.get("chain"):
            chain = rt["chain"]
            spot = rt.get("spot", spot) or spot
            price_source = f"realtime:{rt.get('source')}"
    except Exception as e:
        log.debug("实时链失败[%s]: %s", chain_key, e)

    # 2) 日频链
    if chain is None:
        try:
            df, spot_d = get_daily_chain(chain_key, trade_date, with_spot=True)
            if df is not None and not df.empty:
                chain = df
                if spot_d:
                    spot = spot_d
                price_source = "daily_chain"
        except Exception as e:
            log.debug("日频链失败[%s]: %s", chain_key, e)

    if chain is None or not spot or float(spot) <= 0:
        return None

    # 标准化 + 计算 IV
    try:
        contracts = standardize(chain, source="tushare", trade_date=trade_date or "")
        if not contracts:
            return None
        contracts = calc_iv(contracts, float(spot), r=0.02)
    except Exception as e:
        log.warning("标准化/IV计算失败[%s]: %s", chain_key, e)
        return None

    # 按到期月分组
    expiries = {}
    for c in contracts:
        if c.iv is None or c.iv <= 0 or c.iv > 5:
            continue
        if c.dte < 3:
            continue
        exp = c.expiry
        if exp not in expiries:
            expiries[exp] = {"dte": c.dte, "calls": [], "puts": []}
        expiries[exp][c.opt_type.lower() + "s"].append((c.strike, c.iv * 100))

    if len(expiries) < 1:
        return None

    # 排序选近月和远月
    sorted_exps = sorted(expiries.keys())
    near_exp = sorted_exps[0]
    far_exp = sorted_exps[-1] if len(sorted_exps) > 1 else None

    result = {
        "spot": float(spot),
        "near": {
            "expiry": near_exp,
            "dte": expiries[near_exp]["dte"],
            "calls": sorted(expiries[near_exp]["calls"], key=lambda x: x[0]),
            "puts": sorted(expiries[near_exp]["puts"], key=lambda x: x[0]),
        },
    }

    if far_exp and far_exp != near_exp:
        result["far"] = {
            "expiry": far_exp,
            "dte": expiries[far_exp]["dte"],
            "calls": sorted(expiries[far_exp]["calls"], key=lambda x: x[0]),
            "puts": sorted(expiries[far_exp]["puts"], key=lambda x: x[0]),
        }

    return result


def detect_skew_direction(near_data: dict) -> dict:
    """
    检测偏度方向。
    用 OTM put IV 和 OTM call IV 的差值判断：
    - 正常（上斜）：高行权价 call IV > 低行权价 put IV
    - 反向（下斜/KCB式）：高行权价 IV 反而更低
    返回 {"direction": "up"/"down"/"flat", "skew_pct": float, "description": str}
    """
    calls = near_data.get("calls", [])
    puts = near_data.get("puts", [])

    if len(calls) < 3 or len(puts) < 3:
        return {"direction": "unknown", "skew_pct": 0, "description": "数据不足"}

    # 计算 ATM 附近的 skew：取 25-delta 近似（用距 ATM 一定比例的行权价）
    call_ivs = np.array([iv for _, iv in calls])
    call_strikes = np.array([s for s, _ in calls])
    put_ivs = np.array([iv for _, iv in puts])
    put_strikes = np.array([s for s, _ in puts])

    # 简化：用最高行权价 call IV 减去最低行权价 put IV
    # 正值 = 正常上斜，负值 = 反向下斜
    high_call_iv = call_ivs[-1]
    low_put_iv = put_ivs[0]
    skew = high_call_iv - low_put_iv

    # 也计算 call 自身的斜率（按 ATM 归一化，使不同标的可比）
    if len(call_strikes) >= 3:
        call_slope = np.polyfit(call_strikes, call_ivs, 1)[0]
        atm_strike = np.median(call_strikes)
        rel_slope = call_slope * atm_strike  # 归一化斜率
    else:
        call_slope = 0
        rel_slope = 0

    if rel_slope < -0.01:
        direction = "down"
        desc = f"反向偏斜(下斜)：行权价越高IV越低，斜率={call_slope:.3f}"
    elif rel_slope > 0.01:
        direction = "up"
        desc = f"正常偏斜(上斜)：行权价越高IV越高，斜率={call_slope:.3f}"
    else:
        direction = "flat"
        desc = f"近似平坦：斜率={call_slope:.3f}"

    return {
        "direction": direction,
        "skew_pct": round(skew, 2),
        "slope": round(call_slope, 4),
        "description": desc,
    }


# ──────────────────────────── 图表生成 ────────────────────────────

def generate_smile_chart(all_smile_data: dict, iv_safe_ranges: dict,
                         underlying_names: dict, output_path: str,
                         date_str: str = "") -> Optional[str]:
    """
    生成波动率微笑图。
    all_smile_data: {okey: smile_data} (from collect_smile_data)
    iv_safe_ranges: {okey: (low, high)} 安全IV区间
    underlying_names: {okey: "中文名"}
    output_path: 输出PNG路径
    date_str: 日期显示文字
    返回图片路径或None
    """
    valid = {k: v for k, v in all_smile_data.items() if v is not None}
    if not valid:
        log.warning("无有效smile数据，跳过图表生成")
        return None

    n = len(valid)
    cols = min(3, n)
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(5.5 * cols, 4.5 * rows))
    if rows * cols == 1:
        axes = np.array([axes])
    axes = np.array(axes).flatten()

    fig.suptitle(
        f"波动率微笑 ({date_str})" if date_str else "波动率微笑",
        fontproperties=FONT_PROP, fontsize=16, fontweight='bold', y=0.98
    )

    colors = {
        "near_call": "#E74C3C",
        "near_put":  "#2E75B6",
        "far_call":  "#F1948A",
        "far_put":   "#85C1E9",
    }

    chart_idx = 0
    for okey, smile in valid.items():
        ax = axes[chart_idx]
        chart_idx += 1

        name = underlying_names.get(okey, okey)
        near = smile.get("near", {})
        far = smile.get("far")
        spot = smile.get("spot", 0)

        # 画安全区间带（配置是小数如0.12，需×100转为百分比）
        safe_range = iv_safe_ranges.get(okey)
        if safe_range:
            safe_lo = safe_range[0] * 100 if safe_range[0] < 1 else safe_range[0]
            safe_hi = safe_range[1] * 100 if safe_range[1] < 1 else safe_range[1]
            ax.axhspan(safe_lo, safe_hi, alpha=0.15, color='green', zorder=0)
            ax.axhline(safe_lo, color='green', linestyle='--', linewidth=0.8, alpha=0.6)
            ax.axhline(safe_hi, color='green', linestyle='--', linewidth=0.8, alpha=0.6)

        # 近月 call
        if near.get("calls"):
            strikes_c = [s for s, _ in near["calls"]]
            ivs_c = [iv for _, iv in near["calls"]]
            ax.plot(strikes_c, ivs_c, 'o-', color=colors["near_call"],
                    markersize=3, linewidth=1.5, label='近月Call IV', zorder=3)

        # 近月 put
        if near.get("puts"):
            strikes_p = [s for s, _ in near["puts"]]
            ivs_p = [iv for _, iv in near["puts"]]
            ax.plot(strikes_p, ivs_p, 's-', color=colors["near_put"],
                    markersize=3, linewidth=1.5, label='近月Put IV', zorder=3)

        # 远月 call
        if far and far.get("calls"):
            strikes_fc = [s for s, _ in far["calls"]]
            ivs_fc = [iv for _, iv in far["calls"]]
            ax.plot(strikes_fc, ivs_fc, '^--', color=colors["far_call"],
                    markersize=3, linewidth=1.2, label='远月Call IV', zorder=2)

        # 远月 put
        if far and far.get("puts"):
            strikes_fp = [s for s, _ in far["puts"]]
            ivs_fp = [iv for _, iv in far["puts"]]
            ax.plot(strikes_fp, ivs_fp, 'v--', color=colors["far_put"],
                    markersize=3, linewidth=1.2, label='远月Put IV', zorder=2)

        # ATM 线
        if spot > 0:
            ax.axvline(x=spot, color='black', linestyle=':', linewidth=1, alpha=0.5)
            ax.text(spot, ax.get_ylim()[1] * 0.98, f'ATM\n{spot:.2f}',
                    ha='center', va='top', fontsize=7, color='black', alpha=0.7)

        # 检测偏度
        skew_info = detect_skew_direction(near)
        skew_color = "#C00000" if skew_info["direction"] == "down" else "#007000"
        skew_emoji = "!!" if skew_info["direction"] == "down" else ""

        # 标题（含偏度警告）
        near_exp_str = near.get("expiry", "")
        if len(near_exp_str) == 8:
            near_exp_str = f"{near_exp_str[:4]}-{near_exp_str[4:6]}-{near_exp_str[6:]}"
        title = f"{name} ({near_exp_str}, DTE={near.get('dte', '?')})"
        ax.set_title(title, fontproperties=FONT_PROP, fontsize=11, fontweight='bold',
                     color=skew_color if skew_info["direction"] == "down" else "black")

        if skew_info["direction"] == "down":
            ax.annotate(
                f"[!!] 反向偏斜\n{skew_info['description']}",
                xy=(0.02, 0.02), xycoords='axes fraction',
                fontsize=7, color=skew_color, fontweight='bold',
                fontproperties=FONT_PROP,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#FFF5F5', edgecolor=skew_color, alpha=0.9),
            )

        # 安全区间标注
        if safe_range:
            safe_lo_t = safe_range[0] * 100 if safe_range[0] < 1 else safe_range[0]
            safe_hi_t = safe_range[1] * 100 if safe_range[1] < 1 else safe_range[1]
            ax.text(0.98, 0.98, f'安全区间\n{safe_lo_t:.0f}%-{safe_hi_t:.0f}%',
                    transform=ax.transAxes, ha='right', va='top',
                    fontsize=7, color='green', alpha=0.8,
                    fontproperties=FONT_PROP,
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='#F0FFF0', edgecolor='green', alpha=0.8))

        ax.set_xlabel('行权价', fontproperties=FONT_PROP, fontsize=8)
        ax.set_ylabel('IV (%)', fontproperties=FONT_PROP, fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)

        # 图例（仅第一个子图）
        if chart_idx == 1:
            ax.legend(prop=FONT_PROP, fontsize=7, loc='upper left', framealpha=0.8)
        else:
            ax.legend(prop=FONT_PROP, fontsize=6, loc='upper left', framealpha=0.8)

    # 隐藏多余子图
    for i in range(chart_idx, len(axes)):
        axes[i].set_visible = False
        axes[i].axis('off')

    plt.tight_layout(rect=[0, 0, 1, 0.95])

    # 水印
    fig.text(
        0.99, 0.005,
        "Claude Code · 股指波动率监控",
        transform=fig.transFigure,
        fontsize=8, color='gray', alpha=0.5,
        ha='right', va='bottom',
        fontproperties=FONT_PROP,
    )

    # 保存图片
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    log.info("波动率微笑图已保存: %s", output_path)
    return output_path


# ──────────────────────────── 风险检测 ────────────────────────────

def detect_smile_risks(all_smile_data: dict, iv_safe_ranges: dict,
                       underlying_names: dict) -> list:
    """
    检测波动率微笑中的风险信号。
    返回风险列表 [{"level": "high"/"medium", "okey": str, "name": str, "type": str, "detail": str}, ...]
    """
    risks = []
    valid = {k: v for k, v in all_smile_data.items() if v is not None}

    for okey, smile in valid.items():
        name = underlying_names.get(okey, okey)
        near = smile.get("near", {})
        far = smile.get("far")
        spot = smile.get("spot", 0)
        safe_range = iv_safe_ranges.get(okey)

        # 1. 反向偏斜检测
        calls = near.get("calls", [])
        if len(calls) >= 3:
            call_ivs = np.array([iv for _, iv in calls])
            call_strikes = np.array([s for s, _ in calls])
            call_slope = np.polyfit(call_strikes, call_ivs, 1)[0]
            atm_strike = np.median(call_strikes)
            rel_slope = call_slope * atm_strike
            if rel_slope < -0.01:
                risks.append({
                    "level": "high",
                    "okey": okey,
                    "name": name,
                    "type": "反向偏斜",
                    "detail": f"Call IV 斜率={call_slope:.3f}（归一化={rel_slope:.4f}），"
                              f"行权价越高IV越低，与市场正常结构相反"
                })

        # 2. IV 超出安全区间检测
        if safe_range:
            safe_lo = safe_range[0] * 100 if safe_range[0] < 1 else safe_range[0]
            safe_hi = safe_range[1] * 100 if safe_range[1] < 1 else safe_range[1]
            # 检查近月 call/put 的 IV 是否超出安全区间
            for opt_type, key in [("call", "calls"), ("put", "puts")]:
                items = near.get(key, [])
                strikes = [s for s, _ in items]
                ivs = [iv for _, iv in items]
                # ATM 附近的 IV（距 ATM ±10%）
                if spot > 0:
                    atm_items = [(s, iv) for s, iv in items if abs(s - spot) / spot < 0.10]
                    if atm_items:
                        atm_ivs = [iv for _, iv in atm_items]
                        min_iv = min(atm_ivs)
                        max_iv = max(atm_ivs)
                        if max_iv > safe_hi:
                            risks.append({
                                "level": "high",
                                "okey": okey,
                                "name": name,
                                "type": "IV 超上限",
                                "detail": f"近月 ATM {opt_type} IV 最高 {max_iv:.1f}%，"
                                          f"超出安全区间上限 {safe_hi:.0f}%，"
                                          f"市场恐慌情绪严重"
                            })
                        elif min_iv < safe_lo:
                            risks.append({
                                "level": "medium",
                                "okey": okey,
                                "name": name,
                                "type": "IV 低于下限",
                                "detail": f"近月 ATM {opt_type} IV 最低 {min_iv:.1f}%，"
                                          f"低于安全区间下限 {safe_lo:.0f}%，"
                                          f"隐含波动率极度压缩，可能即将剧烈波动"
                            })

        # 3. 远月 IV 远高于近月（期限结构倒挂/恐慌）
        if far:
            far_calls = far.get("calls", [])
            near_calls = near.get("calls", [])
            if far_calls and near_calls:
                # 比较 ATM 附近
                far_atm = [iv for s, iv in far_calls if spot > 0 and abs(s - spot) / spot < 0.10]
                near_atm = [iv for s, iv in near_calls if spot > 0 and abs(s - spot) / spot < 0.10]
                if far_atm and near_atm:
                    far_avg = np.mean(far_atm)
                    near_avg = np.mean(near_atm)
                    if far_avg > near_avg * 1.5:
                        risks.append({
                            "level": "high",
                            "okey": okey,
                            "name": name,
                            "type": "期限结构倒挂",
                            "detail": f"远月 ATM call IV {far_avg:.1f}% 远高于近月 {near_avg:.1f}% "
                                      f"（比值 {far_avg/near_avg:.2f}x），"
                                      f"市场对远期风险定价远高于近期，警惕黑天鹅事件"
                        })

    return risks


def format_risk_alert(risks: list, date_str: str = "") -> str:
    """格式化风险预警文本（用于飞书推送）"""
    if not risks:
        return "✅ 所有标的光滑率微笑结构正常，无异常信号"

    high_risks = [r for r in risks if r["level"] == "high"]
    medium_risks = [r for r in risks if r["level"] == "medium"]

    lines = []
    if high_risks:
        lines.append(f"⚠️ **高风险预警（{len(high_risks)}项）**")
        for r in high_risks:
            lines.append(f"- **{r['name']}** [{r['type']}]：{r['detail']}")
    if medium_risks:
        lines.append(f"\n⚡ **中等风险预警（{len(medium_risks)}项）**")
        for r in medium_risks:
            lines.append(f"- **{r['name']}** [{r['type']}]：{r['detail']}")

    return "\n".join(lines)
