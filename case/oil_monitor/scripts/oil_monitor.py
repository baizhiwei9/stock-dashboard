#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原油月度监控报告（WTI + 布伦特）
纯 Python 零 token：采集 → 落库 → 指标计算 → 规则研判 → md 报告 + PNG 图 → 飞书卡片/图片 + 邮件

数据源（四级降级）：
  1) akshare futures_foreign_hist('CL')  → WTI 连续合约日线（新浪外盘）
  2) akshare futures_foreign_hist('OIL') → 布伦特连续合约日线
  3) data_hub global_macro.get_global_macro() → brent 当前值兜底
  4) 库内历史数据（不伪造，标注数据日期）

用法：
  python3 oil_monitor.py                # 完整跑：采集+报告+图+推送+邮件
  python3 oil_monitor.py --no-push      # 只采集+出报告，不推送
  python3 oil_monitor.py --dry-run      # 只采集+计算，打印摘要
"""
import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
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
    return '/opt/coze/.fuse_drive/dataHub_三级体系'

_HUB_ROOT = _hub_root_path()


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, 'data')
OUT_DIR = os.path.join(PROJECT_DIR, 'outputs')
DB_PATH = os.path.join(DATA_DIR, 'oil.db')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

# 复用主目录的飞书推送
sys.path.insert(0, os.path.dirname(PROJECT_DIR))   # 主对话目录，feishu_push.py 所在

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ============ 邮件配置（与周期监控保持一致）============
EMAIL_CONFIG = {
    'smtp_server': 'smtp.qq.com',
    'smtp_port': 465,
    'sender': '410119229@qq.com',
    'auth_code': 'jnmmytxtdlwebghj',
    'receiver': 'moergen@coze.email',
}


# ==================== 数据采集 ====================
def _fetch_akshare_foreign(symbol: str) -> pd.DataFrame:
    """外盘连续合约日线（DataHub 统一接口）。CL=WTI原油，OIL=布伦特原油"""
    try:
        sys.path.insert(0, _HUB_ROOT)
        import data_hub as dh
        df = dh.get_foreign_futures_daily(symbol, normalize=True)
    except Exception as e:
        logger.warning(f'DataHub 外盘期货失败: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df[['date', 'open', 'high', 'low', 'close']].copy()
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    for c in ['open', 'high', 'low', 'close']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close'])
    df = df[df['close'] > 0]
    return df.sort_values('date').drop_duplicates('date', keep='last').reset_index(drop=True)


def _fetch_global_macro_brent() -> float:
    """降级源：data_hub 的 global_macro 只提供布伦特当前值（无历史）"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(PROJECT_DIR)), 'codeact', 'scripts'))
        from data_hub.collectors.global_macro import get_global_macro
        d = get_global_macro()
        if d and d.get('brent'):
            return float(d['brent'])
    except Exception as e:
        logger.warning(f'global_macro 降级源不可用: {e}')
    return None


def fetch_oil_data():
    """返回 (wti_df, brent_df, notes)"""
    notes = []
    wti = pd.DataFrame()
    brent = pd.DataFrame()

    for name, sym in [('WTI', 'CL'), ('布伦特', 'OIL')]:
        try:
            df = _fetch_akshare_foreign(sym)
            if len(df) > 0:
                if name == 'WTI':
                    wti = df
                else:
                    brent = df
                notes.append(f'{name}({sym}) {len(df)} 行，最新 {df["date"].iloc[-1]} 收 {df["close"].iloc[-1]:.2f}')
            else:
                notes.append(f'⚠️ {name}({sym}) 返回空')
        except Exception as e:
            notes.append(f'⚠️ {name}({sym}) 采集失败: {type(e).__name__}: {str(e)[:80]}')

    # 降级：WTI 缺则尝试用库内历史；布伦特缺则尝试 global_macro 当前值补一行
    if len(brent) == 0:
        b = _fetch_global_macro_brent()
        if b:
            today = datetime.now().strftime('%Y-%m-%d')
            brent = pd.DataFrame([{'date': today, 'open': b, 'high': b, 'low': b, 'close': b}])
            notes.append(f'⚠️ 布伦特走 global_macro 降级，仅当前值 {b:.2f}（无历史序列）')

    return wti, brent, notes


# ==================== 落库 ====================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA journal_mode=MEMORY')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS oil_price (
        date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        updated_at TEXT,
        PRIMARY KEY (date, symbol))''')
    conn.commit()
    return conn


def save_to_db(conn, df, symbol):
    if df is None or len(df) == 0:
        return 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = [(r['date'], symbol, r['open'], r['high'], r['low'], r['close'], now)
            for _, r in df.iterrows()]
    conn.executemany('''INSERT INTO oil_price (date, symbol, open, high, low, close, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(date, symbol) DO UPDATE SET
          open=excluded.open, high=excluded.high, low=excluded.low,
          close=excluded.close, updated_at=excluded.updated_at''', rows)
    conn.commit()
    return len(rows)


def load_from_db(conn, symbol, days=800):
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    df = pd.read_sql_query(
        'SELECT date, open, high, low, close FROM oil_price WHERE symbol=? AND date>=? ORDER BY date',
        conn, params=(symbol, start))
    return df


# ==================== 指标计算 ====================
def _pct(a, b):
    if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return (a - b) / b * 100


def compute_metrics(df, label):
    """单个品种的核心指标"""
    if df is None or len(df) < 5:
        return None
    d = df.copy().reset_index(drop=True)
    d['date_dt'] = pd.to_datetime(d['date'])
    close = d['close']

    latest = close.iloc[-1]
    latest_date = d['date'].iloc[-1]
    prev = close.iloc[-2] if len(close) >= 2 else None

    def back(n):
        return close.iloc[-1 - n] if len(close) > n else None

    # 月度涨跌：与上一自然月最后一个交易日比
    cur_ym = d['date_dt'].iloc[-1].strftime('%Y-%m')
    prev_month = d[d['date_dt'].dt.strftime('%Y-%m') < cur_ym]
    month_base = prev_month['close'].iloc[-1] if len(prev_month) else None
    month_base_date = prev_month['date'].iloc[-1] if len(prev_month) else None

    # 年初至今
    cur_year = d['date_dt'].iloc[-1].year
    ytd = d[d['date_dt'].dt.year < cur_year]
    ytd_base = ytd['close'].iloc[-1] if len(ytd) else None

    # 均线
    ma20 = close.tail(20).mean() if len(close) >= 20 else None
    ma60 = close.tail(60).mean() if len(close) >= 60 else None
    ma120 = close.tail(120).mean() if len(close) >= 120 else None

    # 年内高低
    ytd_df = d[d['date_dt'].dt.year == cur_year]
    ytd_high = ytd_df['high'].max() if len(ytd_df) else d['high'].max()
    ytd_low = ytd_df['low'].min() if len(ytd_df) else d['low'].min()
    pos = (latest - ytd_low) / (ytd_high - ytd_low) * 100 if ytd_high > ytd_low else None

    # 60日高低
    hi60 = d['high'].tail(60).max()
    lo60 = d['low'].tail(60).min()

    # 20日年化波动率
    ret = np.log(close / close.shift(1)).dropna()
    vol20 = ret.tail(20).std() * np.sqrt(252) * 100 if len(ret) >= 20 else None
    vol_series = ret.rolling(20).std() * np.sqrt(252) * 100
    vol_hist = vol_series.dropna()
    vol_pct = (vol_hist < vol20).mean() * 100 if (vol20 is not None and len(vol_hist) > 60) else None

    # 月度均价（近 13 个自然月）
    d['ym'] = d['date_dt'].dt.strftime('%Y-%m')
    monthly = d.groupby('ym').agg(avg=('close', 'mean'), last=('close', 'last')).tail(13)

    return {
        'label': label,
        'latest': round(float(latest), 2),
        'latest_date': latest_date,
        'day_chg': round(float(latest - prev), 2) if prev is not None else None,
        'day_pct': round(_pct(latest, prev), 2) if prev is not None else None,
        'w1_pct': round(_pct(latest, back(5)), 2) if back(5) else None,
        'm1_pct': round(_pct(latest, back(20)), 2) if back(20) else None,
        'm3_pct': round(_pct(latest, back(60)), 2) if back(60) else None,
        'month_pct': round(_pct(latest, month_base), 2) if month_base else None,
        'month_base_date': month_base_date,
        'ytd_pct': round(_pct(latest, ytd_base), 2) if ytd_base else None,
        'ma20': round(float(ma20), 2) if ma20 is not None else None,
        'ma60': round(float(ma60), 2) if ma60 is not None else None,
        'ma120': round(float(ma120), 2) if ma120 is not None else None,
        'ytd_high': round(float(ytd_high), 2),
        'ytd_low': round(float(ytd_low), 2),
        'ytd_pos': round(float(pos), 1) if pos is not None else None,
        'hi60': round(float(hi60), 2),
        'lo60': round(float(lo60), 2),
        'vol20': round(float(vol20), 1) if vol20 is not None else None,
        'vol_pct': round(float(vol_pct), 0) if vol_pct is not None else None,
        'monthly': monthly,
        'n_rows': len(d),
        'series': d[['date', 'close', 'high', 'low']].tail(520),
    }


def judge_trend(m):
    """规则化趋势判定（纯代码，不调模型）"""
    if not m:
        return '数据不足', '—'
    c, ma20, ma60, ma120 = m['latest'], m['ma20'], m['ma60'], m['ma120']
    m1 = m['m1_pct'] if m['m1_pct'] is not None else 0
    m3 = m['m3_pct'] if m['m3_pct'] is not None else 0

    if ma20 and ma60 and ma120:
        if ma20 > ma60 > ma120 and c > ma20:
            trend = '上行趋势'
            desc = f'MA20({ma20}) > MA60({ma60}) > MA120({ma120}) 多头排列，价格站上 MA20'
        elif ma20 < ma60 < ma120 and c < ma20:
            trend = '下行趋势'
            desc = f'MA20({ma20}) < MA60({ma60}) < MA120({ma120}) 空头排列，价格跌破 MA20'
        else:
            trend = '震荡'
            desc = f'均线缠绕（MA20 {ma20} / MA60 {ma60} / MA120 {ma120}），方向不明'
    elif ma20 and ma60:
        trend = '上行趋势' if (ma20 > ma60 and c > ma20) else ('下行趋势' if (ma20 < ma60 and c < ma20) else '震荡')
        desc = f'MA20 {ma20} vs MA60 {ma60}'
    else:
        trend, desc = '数据不足', '—'

    if abs(m1) < 1.5 and abs(m3) < 3:
        desc += '；近1/3月涨跌幅均在窄幅区间，横盘特征'
    return trend, desc


# ==================== 传导研判（规则化）====================
def build_transmission(wti_m, brent_m, trend):
    """油价 → 通胀 → 利率 → 港股/A股 的规则化传导提示。
    主人持仓以港股成长为主（腾讯/美团/中芯/百济/康方/药明合联/信达/心动/长飞/协鑫），
    油价对其主要走「美国通胀 → 美债利率 → 港股估值」这条间接链。"""
    lines = []
    if not wti_m:
        return ['数据不足，无法给出传导判断'], '未知'

    price = wti_m['latest']
    m1 = wti_m['m1_pct'] or 0
    ytd = wti_m['ytd_pct'] or 0
    pos = wti_m['ytd_pos']

    # 绝对价位分档
    if price >= 90:
        lvl = '高位'
    elif price >= 75:
        lvl = '中高位'
    elif price >= 60:
        lvl = '中位'
    else:
        lvl = '低位'

    lines.append(f'**价位分档**：WTI {price} 美元/桶，处于{lvl}（年内区间 {wti_m["ytd_low"]}–{wti_m["ytd_high"]}，分位 {pos}%）')

    # 边际方向优先（涨跌是通胀链最直接的边际信号），价位只决定"起点高低"
    # 口径：近20交易日(m1) 与 本月以来(month_pct) 同号时取变动更大者——月界后急跌/急涨是最新信息；
    #       异号时以近20交易日为准（月界效应可能导致本月口径失真）。
    mp_raw = wti_m.get('month_pct')
    mom_val, mom_tag = m1, '近20交易日'
    if mp_raw is not None and m1 * mp_raw > 0 and abs(mp_raw) > abs(m1):
        mom_val, mom_tag = mp_raw, '本月以来'
    if mom_val <= -8:
        mom, mom_desc = 'down', f'{mom_tag}大跌 {mom_val:.1f}%'
    elif mom_val >= 8:
        mom, mom_desc = 'up', f'{mom_tag}大涨 {mom_val:.1f}%'
    else:
        mom, mom_desc = 'flat', f'{mom_tag} {mom_val:+.1f}%'

    # 通胀链条
    if mom == 'up' and price >= 90:
        chain = (f'{mom_desc} 且价位处于高位 → 美国 CPI 能源分项同比抬升 → 核心通胀回落节奏被打断 → '
                 '美联储降息预期推迟、美债长端利率承压 → **港股成长股估值分母端受压**（腾讯/美团/百济/康方/心动等久期长的标的对此最敏感）')
        bias = '偏空'
    elif mom == 'up':
        chain = (f'{mom_desc}，价位{lvl} → 通胀边际抬升但绝对价位不构成强约束 → '
                 '对港股估值的影响偏温和负面，主要体现为上游能源盈利改善、中下游成本抬升')
        bias = '中性偏空'
    elif mom == 'down' and price >= 90:
        chain = (f'{mom_desc}，但绝对价位仍在高位 → 通胀压力出现见顶迹象 → 若持续下行则打开降息空间 → '
                 '**港股成长估值分母端边际改善**，但中下游成本压力尚未解除')
        bias = '中性偏多'
    elif mom == 'down' and price < 75:
        chain = (f'{mom_desc} 且价位偏低 → 美国通胀压力缓解、降息空间打开 → 美债利率下行 → '
                 '**港股成长股估值分母端受益**，同时降低航空/化工/交运成本')
        bias = '偏多'
    elif mom == 'down':
        chain = (f'{mom_desc}，价位{lvl} → 通胀压力边际缓解 → '
                 '**港股成长估值分母端小幅受益**，是本轮偏正面信号')
        bias = '偏多'
    else:
        chain = (f'{mom_desc}，价位{lvl} → 对通胀和利率的边际影响有限 → '
                 '港股估值主导变量仍在美债利率与国内流动性，油价暂非主要矛盾')
        bias = '中性'
    lines.append(f'**通胀→利率→港股估值链**：{chain}')

    # 中期/短期动能分列：两个窗口差异大时不强行合并成一个数，避免"同一输入推出相反结论"
    if mp_raw is not None and abs(mp_raw - m1) > 8:
        same_dir = (m1 * mp_raw > 0)
        lines.append(f'**动能拆解**：中期（近20交易日）{m1:+.1f}%　短期（本月以来，自 {wti_m.get("month_base_date")} 收盘）'
                     f'{mp_raw:+.1f}%　→　'
                     + ('两个窗口**同向**，趋势方向可信度较高，倾向判定采用变动更大的「' + mom_tag + '」口径'
                        if same_dir else
                        '两个窗口**反向**，中期与短期矛盾，倾向判定降级为「分歧」，只作线索不作依据'))

    # A股产业链映射：与上方动能判定同口径（mom_val），YTD 只在边际走平时作补充
    m1 = mom_val
    if m1 > 3:
        a_yes = '石油石化（中石油/中石化/中海油服）、油服工程、煤化工（油煤价差扩大）'
        a_no = '航空（燃油成本）、化纤/塑料下游、交运物流、汽车（用油成本）'
    elif m1 < -3:
        a_yes = '航空、化纤下游、交运物流、汽车（成本下行）'
        a_no = '石油石化上游、油服工程、煤化工'
    elif ytd > 20:
        a_yes = '石油石化上游（年内累计涨幅大，但本月边际走平，追高性价比下降）'
        a_no = '航空、化纤下游（成本仍处高位）'
    elif ytd < -20:
        a_yes = '航空、化纤下游（成本年内持续下行）'
        a_no = '石油石化上游、油服工程'
    else:
        a_yes = '无明显单向受益板块（油价窄幅波动，产业链利润分配变化有限）'
        a_no = '无明显单向受损板块'
    lines.append(f'**A股产业链**：受益方向 {a_yes}；受损方向 {a_no}')

    # 波动率提示
    if wti_m.get('vol20') and wti_m.get('vol_pct') is not None:
        v = wti_m['vol20']
        vp = wti_m['vol_pct']
        if vp >= 80:
            lines.append(f'**波动风险**：20日年化波动 {v}%，处于近一年 {vp:.0f}% 分位（偏高）—— 能源类资产与相关 ETF 短线波动放大，仓位需控制')
        elif vp <= 20:
            lines.append(f'**波动风险**：20日年化波动 {v}%，处于近一年 {vp:.0f}% 分位（偏低）—— 市场对油价定价分歧小，注意低波动后的方向性突破')
        else:
            lines.append(f'**波动风险**：20日年化波动 {v}%，处于近一年 {vp:.0f}% 分位（中性）')

    # 极端波动日：连续合约未做换月价差调整，单日跳空可能含换月因素，需人工留意
    s = wti_m.get('series')
    if s is not None and len(s) > 5:
        ss = s.copy()
        ss['pct'] = ss['close'].pct_change() * 100
        recent = ss.tail(60)                      # 先取最近60个交易日，再筛极端日
        big = recent[recent['pct'].abs() > 6]
        if len(big) > 0:
            items = '、'.join(f'{r["date"]} {r["pct"]:+.1f}%' for _, r in big.iterrows())
            lines.append(f'**极端波动日（近60日单日|涨跌|>6%，共 {len(big)} 次）**：{items}'
                         ' —— 新浪外盘为连续合约、未做换月价差调整，大幅跳空可能含换月因素，'
                         '做单日归因时需核对具体合约月份')

    return lines, bias


# ==================== 报告 ====================
def fmt(v, suffix='', nd=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 'N/A'
    if isinstance(v, (int, float)):
        return f'{v:.{nd}f}{suffix}'
    return str(v)


def fmt_pct(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 'N/A'
    return f'{v:+.2f}%'


def build_report(wti_m, brent_m, notes, spread, trend_wti, trend_desc_wti, trend_brent, trans_lines, bias):
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    r = []
    r.append(f'# 原油月度监控报告 · {today}\n')
    r.append(f'**消息来源**：coze.DeepSeek · 原油月度监控　│　**生成时间**：{now.strftime("%Y-%m-%d %H:%M:%S")}\n')
    r.append(f'> 数据截至 {wti_m["latest_date"] if wti_m else "N/A"}｜WTI {fmt(wti_m["latest"] if wti_m else None)} 美元/桶'
             f'｜布伦特 {fmt(brent_m["latest"] if brent_m else None)} 美元/桶｜综合倾向 **{bias}**\n')

    r.append('## 一、核心价格与涨跌\n')
    r.append('| 品种 | 最新价 | 日涨跌 | 近1周 | 近1月 | 近3月 | 本月以来 | 年初至今 |')
    r.append('|---|---|---|---|---|---|---|---|')
    for m in [wti_m, brent_m]:
        if not m:
            continue
        r.append(f'| {m["label"]} | {fmt(m["latest"])} | {fmt(m["day_chg"])} ({fmt_pct(m["day_pct"])}) '
                 f'| {fmt_pct(m["w1_pct"])} | {fmt_pct(m["m1_pct"])} | {fmt_pct(m["m3_pct"])} '
                 f'| {fmt_pct(m["month_pct"])} | {fmt_pct(m["ytd_pct"])} |')
    if spread is not None:
        r.append(f'\n**WTI-布伦特价差**：{spread:+.2f} 美元/桶（WTI 减布伦特；负值属正常，反映两地供需与运费差异）\n')
    r.append('')

    r.append('## 二、趋势结构\n')
    if wti_m:
        r.append(f'- **WTI**：{trend_wti}。{trend_desc_wti}')
        r.append(f'  - 区间：年内 {wti_m["ytd_low"]}–{wti_m["ytd_high"]}（当前分位 {fmt(wti_m["ytd_pos"], nd=0)}%）'
                 f'；60日 {wti_m["lo60"]}–{wti_m["hi60"]}')
    if brent_m:
        r.append(f'- **布伦特**：{trend_brent}。MA20 {fmt(brent_m["ma20"])} / MA60 {fmt(brent_m["ma60"])} / MA120 {fmt(brent_m["ma120"])}')
        r.append(f'  - 区间：年内 {brent_m["ytd_low"]}–{brent_m["ytd_high"]}（当前分位 {fmt(brent_m["ytd_pos"], nd=0)}%）')
    r.append('')

    r.append('## 三、波动率\n')
    if wti_m:
        r.append(f'- WTI 20日年化波动率 **{fmt(wti_m["vol20"], "%")}**，近一年分位 {fmt(wti_m["vol_pct"], nd=0)}%')
    if brent_m:
        r.append(f'- 布伦特 20日年化波动率 **{fmt(brent_m["vol20"], "%")}**，近一年分位 {fmt(brent_m["vol_pct"], nd=0)}%')
    r.append('')

    r.append('## 四、月度均价（WTI，近13个月）\n')
    if wti_m and wti_m.get('monthly') is not None and len(wti_m['monthly']) > 0:
        r.append('| 月份 | 均价 | 月末收盘 | 环比 |')
        r.append('|---|---|---|---|')
        mo = wti_m['monthly']
        prev_avg = None
        for ym, row in mo.iterrows():
            chg = fmt_pct((row['avg'] - prev_avg) / prev_avg * 100) if prev_avg else '—'
            r.append(f'| {ym} | {row["avg"]:.2f} | {row["last"]:.2f} | {chg} |')
            prev_avg = row['avg']
    r.append('')

    r.append('## 五、传导研判（规则化，纯代码）\n')
    for line in trans_lines:
        r.append(f'- {line}')
    r.append('')

    r.append('## 六、数据源与质量\n')
    for n in notes:
        r.append(f'- {n}')
    r.append(f'- 落库：oil.db 全量历史（WTI/布伦特各约 7700/2600 行）；本报告计算使用近 800 天序列'
             f'（WTI {wti_m["n_rows"] if wti_m else 0} 行 / 布伦特 {brent_m["n_rows"] if brent_m else 0} 行）')
    r.append('')
    r.append('---')
    r.append(f'*本报告由纯 Python 脚本生成（零 token），指标口径固定、不含模型主观判断。生成时间 {now.strftime("%Y-%m-%d %H:%M:%S")}*')

    return '\n'.join(r)


# ==================== 图表 ====================
def build_chart(wti_m, brent_m, trend_wti='—', bias='—'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'Noto Sans CJK JP', 'Droid Sans Fallback']
    plt.rcParams['axes.unicode_minus'] = False

    BG, PANEL, GRID, TXT, SUB = '#0e1117', '#161b26', '#2a3142', '#d8dee9', '#8b95a7'
    C1, C2, C3, C4 = '#4ea8ff', '#ff8c42', '#3ddc97', '#ff5c7a'

    if not wti_m:
        return None

    fig = plt.figure(figsize=(15, 10), facecolor=BG)
    gs = fig.add_gridspec(2, 2, hspace=0.32, wspace=0.18,
                          left=0.06, right=0.97, top=0.875, bottom=0.07)

    def style(ax, title, sub=''):
        ax.set_facecolor(PANEL)
        ax.grid(True, color=GRID, lw=0.6, alpha=0.7)
        for s in ax.spines.values():
            s.set_color(GRID)
        ax.tick_params(colors=SUB, labelsize=9)
        ax.set_title(title, color=TXT, fontsize=12.5, fontweight='bold', loc='left', pad=14)
        if sub:
            ax.text(0, 1.02, sub, transform=ax.transAxes, color=SUB, fontsize=9, va='bottom')

    # ① 价格 + 均线（近2年）
    ax1 = fig.add_subplot(gs[0, :])
    s = wti_m['series'].copy()
    s['dt'] = pd.to_datetime(s['date'])
    s = s[s['dt'] >= s['dt'].max() - pd.Timedelta(days=730)]
    ax1.plot(s['dt'], s['close'], color=C1, lw=1.8, label='WTI 收盘', zorder=3)
    for w, col, lb in [(20, C3, 'MA20'), (60, C2, 'MA60')]:
        if len(s) >= w:
            ax1.plot(s['dt'], s['close'].rolling(w).mean(), color=col, lw=1.2, ls='--', alpha=0.9, label=lb)
    if brent_m:
        sb = brent_m['series'].copy()
        sb['dt'] = pd.to_datetime(sb['date'])
        sb = sb[sb['dt'] >= s['dt'].max() - pd.Timedelta(days=730)]
        ax1.plot(sb['dt'], sb['close'], color='#b28dff', lw=1.3, alpha=0.85, label='布伦特 收盘')
    # 最新价标注
    ax1.scatter([s['dt'].iloc[-1]], [s['close'].iloc[-1]], color=C1, s=55, zorder=6,
                edgecolor=BG, linewidth=1.2)
    ax1.annotate(f'{wti_m["latest"]:.2f}', xy=(s['dt'].iloc[-1], s['close'].iloc[-1]),
                 xytext=(8, 10), textcoords='offset points', color=C1,
                 fontsize=11, fontweight='bold')
    ax1.legend(loc='upper left', facecolor=PANEL, edgecolor=GRID, labelcolor=TXT, fontsize=9.5, ncol=4)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    style(ax1, '① WTI / 布伦特 价格与均线（近2年）')

    # ② 月度均价柱
    ax2 = fig.add_subplot(gs[1, 0])
    mo = wti_m['monthly']
    if len(mo) > 0:
        cols = [C3 if i == len(mo) - 1 else C1 for i in range(len(mo))]
        ax2.bar(range(len(mo)), mo['avg'].values, color=cols, alpha=0.85, width=0.66, edgecolor=BG)
        ax2.set_xticks(range(len(mo)))
        ax2.set_xticklabels([x[2:] for x in mo.index], rotation=45, ha='right', fontsize=8.5)
        for i, v in enumerate(mo['avg'].values):
            ax2.text(i, v, f'{v:.0f}', ha='center', va='bottom', color=TXT, fontsize=8)
        ax2.axhline(wti_m['latest'], color=C4, lw=1.1, ls=':', alpha=0.9)
    style(ax2, '② WTI 月度均价（近13个月）', '绿柱=当前月（未结束）　虚线=最新价')

    # ③ 波动率
    ax3 = fig.add_subplot(gs[1, 1])
    ss = wti_m['series'].copy()
    ss['dt'] = pd.to_datetime(ss['date'])
    ret = np.log(ss['close'] / ss['close'].shift(1))
    vol = ret.rolling(20).std() * np.sqrt(252) * 100
    vv = pd.DataFrame({'dt': ss['dt'], 'vol': vol}).dropna()
    vv = vv[vv['dt'] >= vv['dt'].max() - pd.Timedelta(days=730)]
    ax3.fill_between(vv['dt'], vv['vol'], color=C2, alpha=0.28)
    ax3.plot(vv['dt'], vv['vol'], color=C2, lw=1.5)
    if wti_m.get('vol20'):
        ax3.axhline(wti_m['vol20'], color=C4, lw=1.1, ls='--', alpha=0.9)
        ax3.annotate(f'当前 {wti_m["vol20"]:.1f}%　近一年 {wti_m["vol_pct"]:.0f}% 分位',
                     xy=(0.03, 0.90), xycoords='axes fraction', color=C4, fontsize=10,
                     ha='left', va='center', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.35', fc=PANEL, ec=C4, lw=0.9, alpha=0.9))
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    style(ax3, '③ WTI 20日年化波动率（近2年）', '波动率高位=定价分歧大，能源类资产短线风险抬升')

    fig.suptitle('原油月度监控（WTI / 布伦特）', color=TXT, fontsize=17, fontweight='bold',
                 x=0.06, ha='left', y=0.965)
    fig.text(0.06, 0.915,
             f'趋势：{trend_wti}　│　综合倾向：{bias}　│　数据源 akshare 新浪外盘　│　纯 Python 零 token',
             color=SUB, fontsize=10.5, ha='left')

    out = os.path.join(OUT_DIR, f'{datetime.now().strftime("%Y%m%d")}_原油月度图.png')
    fig.savefig(out, dpi=135, facecolor=BG, bbox_inches='tight')
    plt.close(fig)
    logger.info(f'原油图已生成: {out}')
    return out


# ==================== 推送 ====================
def push_feishu(report_path, chart_path):
    ok_card = ok_img = False
    try:
        # 用全文卡片通道：markdown_to_card 会把整份 md 塞进单个 markdown 元素，
        # 表格会渲染成 |管道符| 乱码且超长会被截断；push_report_full_to_feishu 会把表格转成文本行
        from feishu_push import push_report_full_to_feishu
        ok_card = push_report_full_to_feishu(report_path)
        logger.info(f'飞书报告全文推送: {"成功" if ok_card else "失败"}')
    except Exception as e:
        logger.error(f'飞书卡片推送异常: {type(e).__name__}: {e}')

    if chart_path and os.path.exists(chart_path):
        try:
            from feishu_push import push_image_to_feishu
            ok_img = push_image_to_feishu(chart_path)
            logger.info(f'飞书图片推送: {"成功" if ok_img else "失败"}')
        except Exception as e:
            logger.error(f'飞书图片推送异常: {type(e).__name__}: {e}')
    return ok_card, ok_img


def send_email(subject, md_text, chart_path=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    from email.header import Header
    from email.utils import formataddr

    cfg = EMAIL_CONFIG
    msg = MIMEMultipart('related')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('coze.DeepSeek·原油监控', 'utf-8')), cfg['sender']))
    msg['To'] = cfg['receiver']

    # md → 简易 HTML
    html_lines = []
    for line in md_text.split('\n'):
        l = line.rstrip()
        if l.startswith('# '):
            html_lines.append(f'<h2 style="color:#1a4d8f;border-bottom:2px solid #1a4d8f;padding-bottom:6px">{l[2:]}</h2>')
        elif l.startswith('## '):
            html_lines.append(f'<h3 style="color:#2b6cb0;margin-top:18px">{l[3:]}</h3>')
        elif l.startswith('|'):
            cells = [c.strip() for c in l.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells):
                continue
            html_lines.append('<tr>' + ''.join(f'<td style="border:1px solid #ddd;padding:5px 9px">{c}</td>' for c in cells) + '</tr>')
        elif l.startswith('- '):
            txt = l[2:].replace('**', '')
            html_lines.append(f'<li>{txt}</li>')
        elif l.startswith('> '):
            html_lines.append(f'<blockquote style="border-left:4px solid #4ea8ff;margin:8px 0;padding:6px 12px;background:#f5f9ff;color:#333">{l[2:].replace("**","")}</blockquote>')
        elif l.startswith('---'):
            html_lines.append('<hr>')
        elif l.strip():
            html_lines.append(f'<p style="line-height:1.75;margin:6px 0">{l.replace("**","")}</p>')
    # 包 table 标签
    body = []
    in_table = False
    for h in html_lines:
        if h.startswith('<tr>') and not in_table:
            body.append('<table style="border-collapse:collapse;font-size:13px;margin:8px 0">')
            in_table = True
        elif not h.startswith('<tr>') and in_table:
            body.append('</table>')
            in_table = False
        body.append(h)
    if in_table:
        body.append('</table>')

    if chart_path and os.path.exists(chart_path):
        body.append('<p style="margin-top:14px"><img src="cid:oilchart" style="width:100%;max-width:1000px;border-radius:8px"></p>')

    msg.attach(MIMEText('<div style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;max-width:1000px">'
                        + ''.join(body) + '</div>', 'html', 'utf-8'))

    if chart_path and os.path.exists(chart_path):
        try:
            with open(chart_path, 'rb') as f:
                img = MIMEImage(f.read())
            img.add_header('Content-ID', '<oilchart>')
            img.add_header('Content-Disposition', 'inline', filename=os.path.basename(chart_path))
            msg.attach(img)
        except Exception as e:
            logger.warning(f'邮件内嵌图失败（不影响邮件）: {e}')

    try:
        with smtplib.SMTP_SSL(cfg['smtp_server'], cfg['smtp_port'], timeout=25) as server:
            server.login(cfg['sender'], cfg['auth_code'])
            server.sendmail(cfg['sender'], [cfg['receiver']], msg.as_string())
        logger.info(f'✅ 邮件已发送: {subject}')
        return True
    except Exception as e:
        logger.error(f'❌ 邮件发送失败: {type(e).__name__}: {e}')
        return False


# ==================== 主流程 ====================
PUSH_LOCK = os.path.join(OUT_DIR, '.push_lock.json')


def _already_pushed_today():
    """同日推送幂等：调试时反复运行不会把多版报告推给大佬"""
    try:
        import json
        with open(PUSH_LOCK, 'r', encoding='utf-8') as f:
            st = json.load(f)
        return st.get('date') == datetime.now().strftime('%Y-%m-%d')
    except Exception:
        return False


def _mark_pushed():
    try:
        import json
        with open(PUSH_LOCK, 'w', encoding='utf-8') as f:
            json.dump({'date': datetime.now().strftime('%Y-%m-%d'),
                       'pushed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, f)
    except Exception as e:
        logger.warning(f'推送锁写入失败（不影响推送）: {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true', help='不推送飞书/邮件')
    ap.add_argument('--dry-run', action='store_true', help='只采集+计算，打印摘要')
    ap.add_argument('--force', action='store_true', help='忽略同日推送锁，强制重推')
    args = ap.parse_args()

    logger.info('=' * 60)
    logger.info('原油月度监控启动')
    logger.info('=' * 60)

    # 1) 采集
    wti_df, brent_df, notes = fetch_oil_data()
    for n in notes:
        logger.info(n)

    # 2) 落库
    conn = init_db()
    n_wti = save_to_db(conn, wti_df, 'WTI')
    n_brent = save_to_db(conn, brent_df, 'BRENT')
    logger.info(f'落库: WTI {n_wti} 行 / 布伦特 {n_brent} 行')

    # 3) 从库读（统一口径，保证序列完整）
    wti_db = load_from_db(conn, 'WTI', days=800)
    brent_db = load_from_db(conn, 'BRENT', days=800)
    conn.close()

    wti_m = compute_metrics(wti_db, 'WTI 原油')
    brent_m = compute_metrics(brent_db, '布伦特原油')

    if not wti_m:
        logger.error('❌ WTI 数据不足，无法生成报告')
        return 1

    spread = round(wti_m['latest'] - brent_m['latest'], 2) if brent_m else None
    trend_wti, trend_desc_wti = judge_trend(wti_m)
    trend_brent, _ = judge_trend(brent_m)
    trans_lines, bias = build_transmission(wti_m, brent_m, trend_wti)

    if args.dry_run:
        print(f'WTI {wti_m["latest"]} ({wti_m["latest_date"]}) 日{wti_m["day_pct"]}% 月{wti_m["month_pct"]}% YTD{wti_m["ytd_pct"]}%')
        print(f'趋势={trend_wti} 倾向={bias} 价差={spread}')
        for l in trans_lines:
            print(' -', l)
        return 0

    # 4) 报告
    report = build_report(wti_m, brent_m, notes, spread, trend_wti, trend_desc_wti,
                          trend_brent, trans_lines, bias)
    report_path = os.path.join(OUT_DIR, f'{datetime.now().strftime("%Y-%m-%d")}_原油月报.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    logger.info(f'报告已保存: {report_path}')

    # 5) 图
    chart_path = None
    try:
        chart_path = build_chart(wti_m, brent_m, trend_wti, bias)
    except Exception as e:
        logger.error(f'❌ 原油图生成失败（不影响报告）: {type(e).__name__}: {e}')

    # 6) 推送（同日幂等，避免调试过程中把多版报告连推给大佬）
    if not args.no_push:
        if _already_pushed_today() and not args.force:
            logger.info('今日已推送过（推送锁），跳过。需重推请加 --force')
            logger.info('原油月度监控完成')
            return 0
        push_feishu(report_path, chart_path)
        subject = (f'coze.DeepSeek · 原油月报 {datetime.now().strftime("%Y-%m")} | '
                   f'WTI {wti_m["latest"]}（本月 {fmt_pct(wti_m["month_pct"])}） | 倾向 {bias}')
        send_email(subject, report, chart_path)
        _mark_pushed()
    else:
        logger.info('--no-push，跳过推送')

    logger.info('原油月度监控完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
