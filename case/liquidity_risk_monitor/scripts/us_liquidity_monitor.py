#!/usr/bin/env python3.13
"""
美国流动性监控 - 按Q/g/P三维度框架
Q: 水池总水量 (Reserve, ON RRP, TGA, SRF)
g: 含氧量结构分布 (SOFR-IORB, 融资可用性)
P: 水温 (利率曲线, OIS)

数据源: Fed H.4.1 + NY Fed + FRED scraping + tushare
输出: Markdown表格 + HTML Canvas可视化
"""

import sys
import os
import re
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ 配置 ============
def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import tushare_token as _tushare_token, smtp_password as _smtp_password  # noqa: E402

TUSHARE_TOKEN = _tushare_token()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/json,application/xml',
}
CACHE_FILE = os.path.join(SCRIPT_DIR, "us_liquidity_cache.json")


def safe_float(val, default=None):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_cache(data):
    with open(CACHE_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ 数据获取: Q 水池总水量 ============

def fetch_fed_h41():
    """美联储H.4.1报告 - 准备金/ON RRP/Foreign RRP/TGA/SRF
    关键修正: H.4.1的"Reverse repurchase agreements"总计包含:
    1. Foreign official and international accounts (外国央行回购池, ~$319B)
    2. Others (国内ON RRP等, ~$2-6B)
    必须区分! ON RRP是流动性缓冲, Foreign Pool不是.
    """
    result = {}
    try:
        url = "https://www.federalreserve.gov/releases/h41/current/default.htm"
        r = requests.get(url, timeout=20, headers=HEADERS, verify=False, )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            all_text = soup.get_text()
            lines = all_text.split('\n')

            for i, line in enumerate(lines):
                if 'Reserve balances with Federal Reserve Banks' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 5:
                            result['reserve'] = int(nums[0].replace(',', ''))
                            break
                    break

            # 总RRP (Table 1: Factors absorbing reserve funds)
            for i, line in enumerate(lines):
                if 'Reverse repurchase agreements' in line and 'Depository' not in line and 'Less' not in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 4:
                            result['rrp_total'] = int(nums[0].replace(',', ''))
                            break
                    break

            # Foreign official RRP sub-item
            for i, line in enumerate(lines):
                if 'Foreign official and international accounts' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 4:
                            result['rrp_foreign'] = int(nums[0].replace(',', ''))
                            break
                    break

            # ON RRP = Others = Total RRP - Foreign RRP
            rrp_total = result.get('rrp_total', 0)
            rrp_foreign = result.get('rrp_foreign', 0)
            if rrp_total and rrp_foreign:
                result['rrp_on'] = rrp_total - rrp_foreign
            elif rrp_total:
                result['rrp_on'] = rrp_total  # fallback

            # 兼容旧字段名
            result['rrp'] = result.get('rrp_on', result.get('rrp_total', 0))

            for i, line in enumerate(lines):
                if 'U.S. Treasury, general account' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 4:
                            result['tga'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'Secured financing' in line or 'Standing repo' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 3:
                            result['srf'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'Total factors absorbing reserve funds' in line or 'Reserve bank credit' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 7:
                            result['total_factors'] = int(nums[0].replace(',', ''))
                            break
                    break

    except Exception as e:
        print(f"  [WARN] Fed H.4.1获取失败: {e}")

    return result if result else None


def fetch_fed_balance_sheet():
    """美联储资产负债表规模"""
    result = {}
    try:
        url = "https://www.federalreserve.gov/releases/h41/current/default.htm"
        r = requests.get(url, timeout=20, headers=HEADERS, verify=False, )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            all_text = soup.get_text()
            lines = all_text.split('\n')

            for i, line in enumerate(lines):
                if 'Total assets' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 7:
                            result['total_assets'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'Securities held outright' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 7:
                            result['securities_outright'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'U.S. Treasury securities' in line and 'held outright' not in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 7:
                            result['treasuries'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'Mortgage-backed securities' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',', '')) > 7:
                            result['mbs'] = int(nums[0].replace(',', ''))
                            break
                    break

    except Exception as e:
        print(f"  [WARN] 美联储资产负债表获取失败: {e}")

    return result if result else None


# ============ 数据获取: g 含氧量 ============

def fetch_sofr():
    """SOFR利率 - 从NY Fed获取"""
    try:
        url = "https://markets.newyorkfed.org/api/rates/secured/search.json?startDate=&endDate=&type=SOFR"
        r = requests.get(url, timeout=15, headers=HEADERS, )
        if r.status_code == 200:
            data = r.json()
            if 'refRates' in data and len(data['refRates']) > 0:
                latest = data['refRates'][-1]
                return {
                    'rate': float(latest.get('percentRate', 0)),
                    'date': latest.get('effectiveDate', ''),
                    'volume': int(latest.get('volume', 0)) if latest.get('volume') else None,
                }
    except Exception as e:
        print(f"  [WARN] NY Fed SOFR获取失败: {e}")

    return None


def fetch_iorb():
    """IORB利率 - 从FRED获取"""
    try:
        url = "https://fred.stlouisfed.org/series/IORB"
        r = requests.get(url, timeout=15, headers=HEADERS, )
        if r.status_code == 200:
            match = re.search(r'value"\s*:\s*([\d.]+)', r.text)
            if match:
                return {'rate': float(match.group(1))}
    except:
        pass
    return None


# ============ 数据获取: P 水温 ============

def fetch_us_treasury():
    """美债收益率 - tushare"""
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
        df = pro.us_tycr(start_date=start, end_date=end)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {k: safe_float(latest.get(k)) for k in ['y2', 'y5', 'y10', 'y30']}
    except Exception as e:
        print(f"  [WARN] 美债收益率获取失败: {e}")
    return None


def fetch_us_treasury_5d():
    """近5日美债"""
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
        df = pro.us_tycr(start_date=start, end_date=end)
        if df is not None and not df.empty:
            return df.tail(5)
    except:
        pass
    return None


# ============ 判断与评估 ============

def assess_reserve_adequacy(reserve_bn):
    """评估储备充裕度 (输入: Billions)"""
    if reserve_bn is None:
        return "N/A", ""
    if reserve_bn > 3500:
        return "🟢 充裕", f"{reserve_bn:.0f}B 远超3T底线"
    elif reserve_bn > 3000:
        return "🟡 适中", f"{reserve_bn:.0f}B 接近3T底线"
    elif reserve_bn > 2500:
        return "🟠 偏紧", f"{reserve_bn:.0f}B 低于3T警戒"
    else:
        return "🔴 紧张", f"{reserve_bn:.0f}B 严重不足"


def assess_rrp(rrp_bn):
    """评估ON RRP水位 (输入: Billions)
    ON RRP是流动性缓冲, QT先消耗ON RRP再消耗准备金
    峰值$2.55T(2022.12) → 现在接近归零
    阈值调整(2026-06-06修正): 区间大幅下移, 因ON RRP实际已枯竭
    """
    if rrp_bn is None:
        return "N/A", ""
    if rrp_bn > 500:
        return "🟢 充足", f"{rrp_bn:.0f}B 流动性缓冲厚, QT无痛消耗"
    elif rrp_bn > 100:
        return "🟡 下降", f"{rrp_bn:.0f}B 缓冲在减少, QT接近传导至准备金"
    elif rrp_bn > 20:
        return "🟠 低位", f"{rrp_bn:.0f}B 缓冲接近耗尽, QT将直接消耗准备金"
    elif rrp_bn > 5:
        return "🔴 枯竭", f"{rrp_bn:.0f}B 缓冲基本耗尽! QT直接冲击准备金"
    else:
        return "🔴💀 已归零", f"{rrp_bn:.1f}B 流动性缓冲完全消失, 储备脆弱"


def assess_sofr_iorb(spread_bp):
    if spread_bp is None:
        return "N/A", ""
    if spread_bp < -5:
        return "🟢 宽松", "SOFR远低于IORB, 融资充裕"
    elif spread_bp < 5:
        return "🟡 正常", f"利差{spread_bp:.0f}bp, 融资平稳"
    elif spread_bp < 20:
        return "🟠 偏紧", f"利差{spread_bp:.0f}bp, 融资成本上升"
    else:
        return "🔴 紧张", f"利差{spread_bp:.0f}bp, 融资压力显著"


def assess_curve(y2, y10):
    if y2 is None or y10 is None:
        return "N/A", ""
    spread = y10 - y2
    if spread > 1.0:
        return "🟢 陡峭", f"2Y10Y +{spread*100:.0f}bp, 经济预期乐观"
    elif spread > 0:
        return "🟡 正常", f"2Y10Y +{spread*100:.0f}bp, 曲线正常"
    elif spread > -0.5:
        return "🟠 倒挂", f"2Y10Y {spread*100:.0f}bp, 轻度倒挂"
    else:
        return "🔴 深度倒挂", f"2Y10Y {spread*100:.0f}bp, 严重倒挂"


# ============ 生成报告 ============

def generate_markdown(data):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    lines.append(f"# 🇺🇸 美国流动性监控")
    lines.append(f"> 更新时间: {now} | 数据源: Fed H.4.1 / NY Fed / FRED")
    lines.append("")

    h41 = data.get('h41', {})
    bs = data.get('balance_sheet', {})

    reserve = h41.get('reserve')
    rrp_on = h41.get('rrp_on')
    rrp_foreign = h41.get('rrp_foreign')
    rrp_total = h41.get('rrp_total')
    tga = h41.get('tga')
    srf = h41.get('srf')
    total_assets = bs.get('total_assets')
    securities = bs.get('securities_outright')
    treasuries = bs.get('treasuries')
    mbs = bs.get('mbs')

    # H.4.1数字单位: millions. /1e3=Billions, /1e6=Trillions
    reserve_b = reserve / 1e3 if reserve else None  # Billions
    rrp_on_b = rrp_on / 1e3 if rrp_on else None  # Billions (真正的ON RRP流动性缓冲)
    rrp_foreign_b = rrp_foreign / 1e3 if rrp_foreign else None  # Billions (外国央行回购池)
    tga_b = tga / 1e3 if tga else None  # Billions
    reserve_t = reserve / 1e6 if reserve else None  # Trillions
    total_assets_t = total_assets / 1e9 if total_assets else None

    # assess函数用Billions
    reserve_status, reserve_note = assess_reserve_adequacy(reserve_b if reserve_b else None)
    rrp_status, rrp_note = assess_rrp(rrp_on_b if rrp_on_b else None)

    # ========== Q ==========
    lines.append("## 💧 Q: 水池总水量 (Quantity)")
    lines.append("")
    lines.append("### 储备充裕度 & QE→QT阶段")
    lines.append("")
    lines.append("| 指标 | 数值 | 换算 | 状态 |")
    lines.append("|------|------|------|------|")

    if reserve_b is not None:
        lines.append(f"| 准备金余额 (Aggregate Reserves) | {reserve:,} M | ${reserve_t:.2f}T | {reserve_status} |")
    else:
        lines.append(f"| 准备金余额 | N/A | - | - |")
    if rrp_on_b is not None:
        lines.append(f"| ON RRP (隔夜逆回购·流动性缓冲) | {rrp_on:,} M | ${rrp_on_b:.1f}B | {rrp_status} |")
    else:
        lines.append(f"| ON RRP | N/A | - | - |")
    if rrp_foreign_b is not None:
        lines.append(f"| Foreign RRP Pool (外国央行回购池) | {rrp_foreign:,} M | ${rrp_foreign_b:.0f}B | 非缓冲 |")
    if rrp_total is not None and rrp_on is not None and rrp_foreign is not None:
        lines.append(f"| 总RRP (H.4.1 Total) | {rrp_total:,} M | ${rrp_total/1e3:.0f}B | - |")
    if tga_b is not None:
        lines.append(f"| TGA (财政部一般账户) | {tga:,} M | ${tga_b:.2f}B | - |")
    else:
        lines.append(f"| TGA | N/A | - | - |")
    if srf is not None:
        lines.append(f"| SRF (常备回购便利) | {srf:,} M | - | - |")
    else:
        lines.append(f"| SRF | N/A | - | - |")

    lines.append("")
    lines.append("### 美联储资产负债表 (QT进程)")
    lines.append("")
    lines.append("| 项目 | 数值 | 换算 |")
    lines.append("|------|------|------|")
    if total_assets_t is not None:
        lines.append(f"| 总资产 | {total_assets:,} M | ${total_assets_t:.2f}T |")
    if securities is not None:
        lines.append(f"| 持有证券 | {securities:,} M | ${securities/1e9:.2f}T |")
    if treasuries is not None:
        lines.append(f"| 美国国债 | {treasuries:,} M | ${treasuries/1e9:.2f}T |")
    if mbs is not None:
        lines.append(f"| MBS | {mbs:,} M | ${mbs/1e9:.2f}T |")
    if total_assets_t is not None:
        peak = 8.96
        qt_pct = (1 - total_assets_t / peak) * 100
        lines.append(f"| QT缩减 | 峰值${peak:.2f}T → 当前 | -{peak-total_assets_t:.2f}T ({qt_pct:.1f}%) |")

    lines.append("")
    lines.append("### RMP阶段判断")
    lines.append("")
    if reserve_b is not None:
        if reserve_b > 3.5:
            rmp = "🟢 充足储备 — QT可继续"
        elif reserve_b > 3.0:
            rmp = "🟡 接近底线 — QT放缓/暂停"
        elif reserve_b > 2.5:
            rmp = "🟠 低于充足 — RMP启动, SRF可能使用"
        else:
            rmp = "🔴 储备不足 — 需停止QT或扩表"
        lines.append(f"**当前**: {rmp}")
        lines.append(f"- 准备金 ${reserve_t:.2f}T vs 充足线 ~$3.0T")
        if rrp_on_b is not None:
            lines.append(f"- ON RRP ${rrp_on_b:.1f}B {'(仍有缓冲)' if rrp_on_b > 20 else '(接近耗尽⚠️)' if rrp_on_b > 5 else '(已归零💀!)'}")
        if rrp_foreign_b is not None:
            lines.append(f"- Foreign Pool ${rrp_foreign_b:.0f}B (非流动性缓冲, 外国央行存款)")
    lines.append("")

    # ========== g ==========
    lines.append("## 🫧 g: 含氧量结构分布 (Gamma)")
    lines.append("")
    sofr = data.get('sofr', {})
    iorb = data.get('iorb', {})
    sofr_rate = sofr.get('rate') if sofr else None
    iorb_rate = iorb.get('rate') if iorb else None
    sofr_vol = sofr.get('volume') if sofr else None
    sofr_date = sofr.get('date', '') if sofr else ''

    spread_bp = (sofr_rate - iorb_rate) * 100 if (sofr_rate is not None and iorb_rate is not None) else None
    spread_status, spread_note = assess_sofr_iorb(spread_bp)

    lines.append("| 指标 | 数值 | 状态 | 说明 |")
    lines.append("|------|------|------|------|")
    if sofr_rate is not None:
        lines.append(f"| SOFR | {sofr_rate:.2f}% | - | {sofr_date} |")
    else:
        lines.append(f"| SOFR | N/A | - | - |")
    if iorb_rate is not None:
        lines.append(f"| IORB | {iorb_rate:.2f}% | - | Fed政策利率下限 |")
    else:
        lines.append(f"| IORB | N/A | - | - |")
    if spread_bp is not None:
        lines.append(f"| SOFR-IORB | {spread_bp:+.1f}bp | {spread_status} | {spread_note} |")
    else:
        lines.append(f"| SOFR-IORB | N/A | - | - |")
    if sofr_vol is not None:
        lines.append(f"| SOFR成交额 | ${sofr_vol/1e9:.0f}B | - | 活跃度 |")
    lines.append("")
    lines.append("> SOFR-IORB<0=融资充裕, ≈0=正常, >0=融资紧张")
    lines.append("")

    # ========== P ==========
    lines.append("## 🌡️ P: 水温 (Price)")
    lines.append("")
    treasury = data.get('treasury', {})
    y2 = treasury.get('y2') if treasury else None
    y5 = treasury.get('y5') if treasury else None
    y10 = treasury.get('y10') if treasury else None
    y30 = treasury.get('y30') if treasury else None

    curve_status, curve_note = assess_curve(y2, y10)

    lines.append("### 利率曲线")
    lines.append("")
    lines.append("| 期限 | 收益率 |")
    lines.append("|------|--------|")
    if y2 is not None: lines.append(f"| 2Y | {y2:.2f}% |")
    if y5 is not None: lines.append(f"| 5Y | {y5:.2f}% |")
    if y10 is not None: lines.append(f"| 10Y | {y10:.2f}% |")
    if y30 is not None: lines.append(f"| 30Y | {y30:.2f}% |")
    if y2 is not None and y10 is not None:
        lines.append(f"| **2Y-10Y** | **{(y10-y2)*100:+.0f}bp** {curve_status} |")
    lines.append("")

    # OIS
    lines.append("### OIS隔夜掉期 (近似)")
    lines.append("")
    lines.append("| 期限 | OIS | vs国债 |")
    lines.append("|------|-----|--------|")
    if y2 is not None:
        lines.append(f"| 2Y OIS | ~{y2+0.05:.2f}% | +5bp |")
    if y5 is not None:
        lines.append(f"| 5Y OIS | ~{y5+0.04:.2f}% | +4bp |")
    lines.append("")

    # ========== 综合 ==========
    lines.append("## 📊 综合评估")
    lines.append("")
    lines.append("| 维度 | 指标 | 当前值 | 状态 |")
    lines.append("|------|------|--------|------|")
    lines.append(f"| Q-储备 | 准备金 | {f'${reserve_t:.2f}T' if reserve_t else 'N/A'} | {reserve_status} |")
    lines.append(f"| Q-缓冲 | ON RRP | {f'${rrp_on_b:.1f}B' if rrp_on_b else 'N/A'} | {rrp_status} |")
    lines.append(f"| Q-外池 | Foreign Pool | {f'${rrp_foreign_b:.0f}B' if rrp_foreign_b else 'N/A'} | 非缓冲 |")
    lines.append(f"| Q-QT | 总资产 | {f'${total_assets_t:.2f}T' if total_assets_t else 'N/A'} | {'缩表中' if total_assets_t and total_assets_t < 8.5 else 'N/A'} |")
    lines.append(f"| g-利差 | SOFR-IORB | {f'{spread_bp:+.0f}bp' if spread_bp is not None else 'N/A'} | {spread_status} |")
    lines.append(f"| P-曲线 | 2Y10Y | {f'{(y10-y2)*100:+.0f}bp' if y2 and y10 else 'N/A'} | {curve_status} |")
    lines.append("")

    # 风险
    lines.append("## ⚠️ 风险信号")
    lines.append("")
    risks = []
    if reserve_b is not None and reserve_b < 3000:
        risks.append("🔴 准备金低于3万亿充足线")
    if rrp_on_b is not None and rrp_on_b < 20:
        risks.append("🔴 ON RRP接近枯竭, QT直接冲击准备金")
    elif rrp_on_b is not None and rrp_on_b < 100:
        risks.append("🟠 ON RRP水位低, QT即将传导至准备金")
    if spread_bp is not None and spread_bp > 10:
        risks.append("🟠 SOFR-IORB利差走阔")
    if y2 is not None and y10 is not None and y2 > y10:
        risks.append("🟡 2Y-10Y曲线倒挂")
    if risks:
        for r in risks:
            lines.append(f"- {r}")
    else:
        lines.append("- ✅ 当前无显著流动性风险信号")

    lines.append("")

    # 数据状态
    fetch_status = []
    fetch_status.append("H.4.1✅" if h41 else "H.4.1❌")
    fetch_status.append("SOFR✅" if data.get('sofr') else "SOFR❌")
    fetch_status.append("IORB✅" if data.get('iorb') else "IORB❌")
    fetch_status.append("Treasury✅" if treasury else "Treasury❌")
    lines.append("---")
    lines.append("*" + " | ".join(fetch_status) + "*")

    return '\n'.join(lines)


def generate_html(data):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    h41 = data.get('h41', {})
    bs = data.get('balance_sheet', {})
    sofr_data = data.get('sofr', {})
    iorb_data = data.get('iorb', {})
    treasury = data.get('treasury', {})

    reserve_b = h41.get('reserve', 0) / 1e3 if h41.get('reserve') else 0  # Billions
    rrp_on_b = h41.get('rrp_on', 0) / 1e3 if h41.get('rrp_on') else 0  # Billions (真正的ON RRP)
    rrp_foreign_b = h41.get('rrp_foreign', 0) / 1e3 if h41.get('rrp_foreign') else 0  # Billions
    tga_b = h41.get('tga', 0) / 1e3 if h41.get('tga') else 0  # Billions
    reserve_t = h41.get('reserve', 0) / 1e6 if h41.get('reserve') else 0  # Trillions
    total_assets_t = bs.get('total_assets', 0) / 1e9 if bs.get('total_assets') else 0

    sofr_rate = sofr_data.get('rate') or 0
    iorb_rate = iorb_data.get('rate') or 0
    spread_bp = round((sofr_rate - iorb_rate) * 100, 1) if sofr_rate and iorb_rate else 0

    y2 = treasury.get('y2') or 0
    y5 = treasury.get('y5') or 0
    y10 = treasury.get('y10') or 0
    y30 = treasury.get('y30') or 0

    def color_reserve(v):
        if v > 3500: return '#00ff88'
        elif v > 3000: return '#ffcc00'
        return '#ff4444'

    def color_rrp(v):
        if v > 100: return '#00ff88'
        elif v > 20: return '#ffcc00'
        elif v > 5: return '#ff8c00'
        return '#ff4444'

    def color_spread(v):
        if v < -5: return '#00ff88'
        elif v < 5: return '#ffcc00'
        elif v < 20: return '#ff8c00'
        return '#ff4444'

    reserve_color = color_reserve(reserve_b)
    rrp_color = color_rrp(rrp_on_b)
    spread_color = color_spread(spread_bp)
    curve_spread = round((y10 - y2) * 100, 0) if y2 and y10 else 0
    curve_color = '#00ff88' if curve_spread > 0 else '#ff4444'

    reserve_status_text = '充裕' if reserve_b>3500 else '适中' if reserve_b>3000 else '偏紧' if reserve_b>0 else 'N/A'
    reserve_status_cls = 'status-green' if reserve_b>3500 else 'status-yellow' if reserve_b>3000 else 'status-orange'
    spread_status_text = '充裕' if spread_bp<-5 else '正常' if spread_bp<5 else '偏紧' if spread_bp<20 else '紧张'
    spread_status_cls = 'status-green' if spread_bp<-5 else 'status-yellow' if spread_bp<5 else 'status-orange' if spread_bp<20 else 'status-red'
    curve_status_text = '正常' if curve_spread>0 else '倒挂⚠️'
    curve_status_cls = 'status-green' if curve_spread>0 else 'status-red'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>美国流动性监控</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: linear-gradient(135deg, #0a0a1a 0%, #1a1a2e 50%, #16213e 100%);
    min-height: 100vh; padding: 20px; color: #e0e0e0;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{ text-align: center; margin-bottom: 30px; }}
.header h1 {{
    font-size: 32px;
    background: linear-gradient(90deg, #00d4ff, #7b2cbf);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
}}
.header .date {{ color: #666; font-size: 13px; }}
.big-cards {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 16px; margin-bottom: 30px;
}}
.big-card {{
    background: rgba(255,255,255,0.05);
    border-radius: 12px; padding: 20px; text-align: center;
}}
.big-card .label {{ font-size: 12px; color: #666; margin-bottom: 6px; }}
.big-card .value {{ font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.big-card .sub {{ font-size: 11px; color: #555; margin-top: 4px; }}
.dimensions {{
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 20px; margin-bottom: 30px;
}}
.dim-card {{
    background: rgba(255,255,255,0.05);
    border-radius: 16px; padding: 24px;
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.08);
    position: relative; overflow: hidden;
}}
.dim-card::before {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
}}
.dim-card.q::before {{ background: linear-gradient(90deg, #00d4ff, #0066ff); }}
.dim-card.g::before {{ background: linear-gradient(90deg, #7b2cbf, #c77dff); }}
.dim-card.p::before {{ background: linear-gradient(90deg, #ff6b6b, #c92a2a); }}
.dim-label {{ font-size: 13px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }}
.dim-title {{ font-size: 20px; font-weight: 700; margin-bottom: 16px; }}
.dim-card.q .dim-title {{ color: #00d4ff; }}
.dim-card.g .dim-title {{ color: #c77dff; }}
.dim-card.p .dim-title {{ color: #ff6b6b; }}
.indicator-row {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.06);
}}
.indicator-row:last-child {{ border-bottom: none; }}
.ind-name {{ color: #aaa; font-size: 13px; }}
.ind-value {{ font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ind-status {{
    font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500;
}}
.status-green {{ background: rgba(0,255,136,0.15); color: #00ff88; }}
.status-yellow {{ background: rgba(255,204,0,0.15); color: #ffcc00; }}
.status-orange {{ background: rgba(255,140,0,0.15); color: #ff8c00; }}
.status-red {{ background: rgba(255,68,68,0.15); color: #ff4444; }}
.chart-section {{
    background: rgba(255,255,255,0.03);
    border-radius: 16px; padding: 20px; margin-bottom: 30px;
}}
.chart-title {{ font-size: 16px; font-weight: 600; color: #ccc; margin-bottom: 16px; }}
#curveChart {{ width: 100%; height: 350px; }}
#liquidGauge {{ width: 100%; height: 350px; }}
.footer {{
    text-align: center; padding: 20px;
    color: #444; font-size: 11px;
    border-top: 1px solid rgba(255,255,255,0.06);
}}
@media (max-width: 768px) {{
    .dimensions {{ grid-template-columns: 1fr; }}
    .big-cards {{ grid-template-columns: repeat(2, 1fr); }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🇺🇸 美国流动性监控</h1>
        <div class="date">Q / g / P 三维度框架 | 更新: {now}</div>
    </div>

    <div class="big-cards">
        <div class="big-card">
            <div class="label">准备金余额</div>
            <div class="value" style="color:{reserve_color}">${reserve_t:.2f}T</div>
            <div class="sub">充足线 ~$3.0T</div>
        </div>
        <div class="big-card">
            <div class="label">ON RRP</div>
            <div class="value" style="color:{rrp_color}">${rrp_on_b:.1f}B</div>
            <div class="sub">流动性缓冲</div>
        </div>
        <div class="big-card">
            <div class="label">SOFR-IORB</div>
            <div class="value" style="color:{spread_color}">{spread_bp:+.0f}bp</div>
            <div class="sub">融资利差</div>
        </div>
        <div class="big-card">
            <div class="label">2Y-10Y</div>
            <div class="value" style="color:{curve_color}">{curve_spread:+.0f}bp</div>
            <div class="sub">曲线形态</div>
        </div>
    </div>

    <div class="dimensions">
        <div class="dim-card q">
            <div class="dim-label">Q - Quantity</div>
            <div class="dim-title">💧 水池总水量</div>
            <div class="indicator-row"><span class="ind-name">准备金余额</span><span class="ind-value">${reserve_t:.2f}T</span></div>
            <div class="indicator-row"><span class="ind-name">ON RRP</span><span class="ind-value">${rrp_on_b:.1f}B</span></div>
            <div class="indicator-row"><span class="ind-name">Foreign Pool</span><span class="ind-value">${rrp_foreign_b:.0f}B</span></div>
            <div class="indicator-row"><span class="ind-name">TGA</span><span class="ind-value">${tga_b:.0f}B</span></div>
            <div class="indicator-row"><span class="ind-name">美联储总资产</span><span class="ind-value">${total_assets_t:.2f}T</span></div>
            <div class="indicator-row"><span class="ind-name">储备阶段</span><span class="ind-status {reserve_status_cls}">{reserve_status_text}</span></div>
        </div>
        <div class="dim-card g">
            <div class="dim-label">g - Gamma</div>
            <div class="dim-title">🫧 含氧量结构分布</div>
            <div class="indicator-row"><span class="ind-name">SOFR</span><span class="ind-value">{sofr_rate:.2f}%</span></div>
            <div class="indicator-row"><span class="ind-name">IORB</span><span class="ind-value">{iorb_rate:.2f}%</span></div>
            <div class="indicator-row"><span class="ind-name">SOFR-IORB</span><span class="ind-value" style="color:{spread_color}">{spread_bp:+.1f}bp</span></div>
            <div class="indicator-row"><span class="ind-name">融资状态</span><span class="ind-status {spread_status_cls}">{spread_status_text}</span></div>
        </div>
        <div class="dim-card p">
            <div class="dim-label">P - Price</div>
            <div class="dim-title">🌡️ 水温</div>
            <div class="indicator-row"><span class="ind-name">2Y</span><span class="ind-value">{y2:.2f}%</span></div>
            <div class="indicator-row"><span class="ind-name">5Y</span><span class="ind-value">{y5:.2f}%</span></div>
            <div class="indicator-row"><span class="ind-name">10Y</span><span class="ind-value">{y10:.2f}%</span></div>
            <div class="indicator-row"><span class="ind-name">30Y</span><span class="ind-value">{y30:.2f}%</span></div>
            <div class="indicator-row"><span class="ind-name">曲线形态</span><span class="ind-status {curve_status_cls}">{curve_status_text}</span></div>
        </div>
    </div>

    <div class="chart-section">
        <div class="chart-title">📈 美债收益率曲线</div>
        <div id="curveChart"></div>
    </div>

    <div class="chart-section">
        <div class="chart-title">🔄 流动性仪表盘</div>
        <div id="liquidGauge"></div>
    </div>

    <div class="footer">
        本报告仅供参考 | 数据源: Fed H.4.1 / NY Fed / Tushare
    </div>
</div>

<script>
var curveChart = echarts.init(document.getElementById('curveChart'));
curveChart.setOption({{
    tooltip: {{ trigger: 'axis' }},
    grid: {{ left: '8%', right: '5%', bottom: '10%', top: '10%' }},
    xAxis: {{
        type: 'category',
        data: ['3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'],
        axisLabel: {{ color: '#888' }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }}
    }},
    yAxis: {{
        type: 'value', name: '%',
        axisLabel: {{ color: '#888', formatter: '{{value}}%' }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }},
        splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.06)' }} }}
    }},
    series: [{{
        type: 'line', smooth: true, symbol: 'circle', symbolSize: 8,
        lineStyle: {{ color: '#00d4ff', width: 3 }},
        itemStyle: {{ color: '#00d4ff' }},
        areaStyle: {{
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                {{ offset: 0, color: 'rgba(0,212,255,0.3)' }},
                {{ offset: 1, color: 'rgba(0,212,255,0.02)' }}
            ])
        }},
        data: [null, null, null, {y2}, null, {y5}, null, {y10}, null, {y30}]
    }}]
}});

var gaugeChart = echarts.init(document.getElementById('liquidGauge'));
gaugeChart.setOption({{
    series: [
        {{
            type: 'gauge', center: ['20%', '55%'], radius: '80%',
            startAngle: 200, endAngle: -20,
            min: 2000, max: 5000,
            detail: {{ formatter: '${{value}}B', color: '#fff', fontSize: 16, offsetCenter: [0, '70%'] }},
            title: {{ color: '#888', fontSize: 12, offsetCenter: [0, '90%'] }},
            data: [{{ value: {reserve_b*1000:.0f}, name: '准备金' }}],
            axisLine: {{ lineStyle: {{ width: 15, color: [[0.3, '#ff4444'], [0.6, '#ffcc00'], [1, '#00ff88']] }} }},
            axisTick: {{ show: false }},
            splitLine: {{ show: false }},
            axisLabel: {{ show: false }},
            pointer: {{ width: 4 }}
        }},
        {{
            type: 'gauge', center: ['50%', '55%'], radius: '80%',
            startAngle: 200, endAngle: -20,
            min: -50, max: 50,
            detail: {{ formatter: '{{value}}bp', color: '#fff', fontSize: 16, offsetCenter: [0, '70%'] }},
            title: {{ color: '#888', fontSize: 12, offsetCenter: [0, '90%'] }},
            data: [{{ value: {spread_bp}, name: 'SOFR-IORB' }}],
            axisLine: {{ lineStyle: {{ width: 15, color: [[0.3, '#00ff88'], [0.5, '#ffcc00'], [0.8, '#ff8c00'], [1, '#ff4444']] }} }},
            axisTick: {{ show: false }},
            splitLine: {{ show: false }},
            axisLabel: {{ show: false }},
            pointer: {{ width: 4 }}
        }},
        {{
            type: 'gauge', center: ['80%', '55%'], radius: '80%',
            startAngle: 200, endAngle: -20,
            min: -100, max: 200,
            detail: {{ formatter: '{{value}}bp', color: '#fff', fontSize: 16, offsetCenter: [0, '70%'] }},
            title: {{ color: '#888', fontSize: 12, offsetCenter: [0, '90%'] }},
            data: [{{ value: {curve_spread}, name: '2Y-10Y' }}],
            axisLine: {{ lineStyle: {{ width: 15, color: [[0.3, '#ff4444'], [0.5, '#ff8c00'], [1, '#00ff88']] }} }},
            axisTick: {{ show: false }},
            splitLine: {{ show: false }},
            axisLabel: {{ show: false }},
            pointer: {{ width: 4 }}
        }}
    ]
}});

window.addEventListener('resize', function() {{
    curveChart.resize();
    gaugeChart.resize();
}});
</script>
</body>
</html>'''

    return html


def main():
    print("=" * 50)
    print("美国流动性监控 - Q/g/P三维度")
    print("=" * 50)

    data = {}

    print("\n[1/5] Fed H.4.1...")
    h41 = fetch_fed_h41()
    data['h41'] = h41 or {}
    if h41:
        print(f"  ✅ 准备金: {h41.get('reserve', 'N/A'):,}M | RRP: {h41.get('rrp', 'N/A'):,}M")
    else:
        print("  ❌ 失败")

    print("\n[2/5] 美联储资产负债表...")
    bs = fetch_fed_balance_sheet()
    data['balance_sheet'] = bs or {}
    if bs:
        print(f"  ✅ 总资产: {bs.get('total_assets', 'N/A'):,}M")
    else:
        print("  ❌ 失败")

    today_str = datetime.now().strftime('%Y-%m-%d')

    # 加载历史缓存作为备份源 (从最近的有效日期取)
    prev_cache = load_cache()
    cached_sofr = cached_iorb = None
    for dk in sorted(prev_cache.keys(), reverse=True):
        entry = prev_cache[dk]
        if cached_sofr is None and entry.get('sofr', {}).get('rate') is not None:
            cached_sofr = entry['sofr']
        if cached_iorb is None and entry.get('iorb', {}).get('rate') is not None:
            cached_iorb = entry['iorb']
        if cached_sofr and cached_iorb:
            break

    print("\n[3/5] SOFR...")
    sofr = fetch_sofr()
    if sofr:
        data['sofr'] = sofr
        print(f"  ✅ SOFR: {sofr.get('rate')}% ({sofr.get('date', '')})")
    elif cached_sofr:
        data['sofr'] = cached_sofr
        print(f"  ⚡ SOFR(缓存): {cached_sofr['rate']}% ({cached_sofr.get('date', '')})")
    else:
        data['sofr'] = {'rate': None, 'date': '', 'volume': None}
        print("  ❌ 失败")

    print("\n[4/5] IORB...")
    iorb = fetch_iorb()
    if iorb:
        data['iorb'] = iorb
        print(f"  ✅ IORB: {iorb.get('rate')}%")
    elif cached_iorb:
        data['iorb'] = cached_iorb
        print(f"  ⚡ IORB(缓存): {cached_iorb['rate']}%")
    else:
        data['iorb'] = {'rate': None}
        print("  ❌ 失败")

    # 互相估算
    if data['sofr'].get('rate') is None and data['iorb'].get('rate') is not None:
        data['sofr']['rate'] = data['iorb']['rate'] - 0.07
        print(f"  ⚡ SOFR估算: {data['sofr']['rate']:.2f}%")
    if data['iorb'].get('rate') is None and data['sofr'].get('rate') is not None:
        data['iorb']['rate'] = data['sofr']['rate'] + 0.07
        print(f"  ⚡ IORB估算: {data['iorb']['rate']:.2f}%")

    print("\n[5/5] 美债收益率...")
    treasury = fetch_us_treasury()
    data['treasury'] = treasury or {}
    if treasury:
        print(f"  ✅ 2Y: {treasury.get('y2')}% | 10Y: {treasury.get('y10')}%")
    else:
        print("  ❌ 失败")

    treasury_5d = fetch_us_treasury_5d()
    if treasury_5d is not None:
        data['treasury_5d'] = treasury_5d

    # 生成

    md_content = generate_markdown(data)
    md_file = os.path.join(OUTPUT_DIR, f'{today_str}_美国流动性监控.md')
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"\n✅ MD: {md_file}")

    html_content = generate_html(data)
    html_file = os.path.join(OUTPUT_DIR, f'{today_str}_美国流动性监控.html')
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✅ HTML: {html_file}")

    feishu_file = os.path.join(OUTPUT_DIR, f'{today_str}_美国流动性监控_飞书推送.md')
    with open(feishu_file, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"✅ 飞书: {feishu_file}")

    # 缓存 (排除DataFrame)
    cache = load_cache()
    cache_data = {k: v for k, v in data.items() if not isinstance(v, pd.DataFrame)}
    cache[today_str] = cache_data
    save_cache(cache)

    return md_file, html_file


if __name__ == '__main__':
    main()
