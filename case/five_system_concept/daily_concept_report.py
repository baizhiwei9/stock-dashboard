#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多系统扩展概念轮动日报 (akshare版 v4)
每日11:00: akshare拉数据 -> 五系统选概念 -> 技术指标验证 -> 选2龙头股 -> 飞书报告
五系统(概念): Hodge + RMT + SPD + 曼宁资金流 + 最优传输OT
并入指数多空系统: AC_VMD + OT多空 + 物理几何 + RMT量子能级 + Hodge板块 + 市场状态诊断
新增: MA40趋势 + VMD变分模态分解 + 横截面动量排名 + SPD谱趋势延续性 + 双数据源校验
"""
import sys, os, json, glob, time, requests
import pandas as pd, numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'pylib'))

from concept_data_pipeline_v2 import MANUAL_CONCEPTS, SINA_CONCEPTS, MAX_STOCKS_PER_CONCEPT, get_sina_concept_stocks, get_prefix
from concept_rotation_4sys import run_hodge, run_rmt, run_spd, run_manning, run_ot

# ===================== 配置 =====================
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
FEISHU_HEADER = "五系统扩展概念轮动日报 | 来自 DoMate"
DAYS = 60

try:
    import akshare as ak
    AKSHARE_OK = True
except ImportError:
    AKSHARE_OK = False

try:
    from vmdpy import VMD as _vmd_func
    VMD_OK = True
except ImportError:
    VMD_OK = False

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
})

# ===================== 工具函数 =====================
def normalize_code(code):
    for p in ('sh', 'sz', 'bj'):
        if code.startswith(p):
            return code[len(p):]
    return code

def to_symbol(code):
    code = normalize_code(code)
    return f"{get_prefix(code)}{code}"

# ===================== 数据拉取 =====================
def fetch_kline_akshare(code):
    symbol = to_symbol(code)
    today = datetime.now()
    end_date = today.strftime('%Y%m%d')
    start_date = (today - timedelta(days=120)).strftime('%Y%m%d')
    df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust='qfq')
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    return df[['close', 'volume']].tail(DAYS)

def fetch_kline_tencent(code):
    symbol = to_symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f"{symbol},day,,,{DAYS},qfq"}
    resp = SESSION.get(url, params=params, timeout=12)
    data = resp.json()
    kline = data.get('data', {}).get(symbol, {})
    bars = kline.get('qfqday') or kline.get('day')
    if not bars:
        return None
    df = pd.DataFrame(bars, columns=['date','open','close','high','low','volume'])
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    df = df.dropna(subset=['close'])
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    return df[['close', 'volume']]

def fetch_kline(code):
    if AKSHARE_OK:
        try:
            df = fetch_kline_akshare(code)
            if df is not None and len(df) > 0:
                return df, 'akshare'
        except Exception:
            pass
    try:
        df = fetch_kline_tencent(code)
        if df is not None and len(df) > 0:
            return df, 'tencent'
    except Exception:
        pass
    return None, 'failed'

# ===================== 概念合成 =====================
def build_concept_data(all_concepts, stock_data):
    concept_close = {}
    concept_volume = {}
    for cname, stocks in all_concepts.items():
        codes = [normalize_code(c) for c in stocks.keys()]
        s_close = {}
        s_vol = {}
        for code in codes:
            if code in stock_data:
                s_close[code] = stock_data[code]['close']
                s_vol[code] = stock_data[code]['volume']
        if not s_close:
            continue
        df_stocks = pd.DataFrame(s_close).sort_index()
        df_filled = df_stocks.ffill().bfill()
        first_vals = df_filled.iloc[0].replace(0, np.nan)
        df_norm = df_filled.div(first_vals).fillna(1)
        concept_close[cname] = df_norm.mean(axis=1)
        df_vol = pd.DataFrame(s_vol).reindex(df_stocks.index).fillna(0)
        concept_volume[cname] = df_vol.mean(axis=1)
    df_close = pd.DataFrame(concept_close).sort_index()
    df_volume = pd.DataFrame(concept_volume).reindex(df_close.index).fillna(0)
    df_returns = df_close.pct_change().dropna(how='all')
    return df_returns, df_close, df_volume

# ===================== VMD 变分模态分解 =====================
def compute_vmd_analysis(close_series):
    """VMD分解概念价格序列 -> 趋势方向 + 趋势强度
    方向用价格序列线性回归斜率(稳健), 强度用VMD趋势模态能量占比"""
    # 清洗: ffill+bfill+去0方差
    f = close_series.ffill().bfill().values.astype(float)
    if len(f) < 20:
        return {'direction': '?', 'strength': 0, 'label': '数据不足'}
    if np.std(f) < 1e-10:
        return {'direction': '→', 'strength': 0, 'label': '无波动'}
    # 方向: 价格序列线性回归斜率(稳健, 不受VMD收敛影响)
    x = np.arange(len(f))
    lr_slope = float(np.polyfit(x, f, 1)[0])
    direction = '↑' if lr_slope > 0 else '↓' if lr_slope < 0 else '→'
    # VMD分解: 去均值后再分解, 避免DC分量主导
    strength = 0.0; label = '弱'
    f_dm = f - np.mean(f)  # 去均值
    if VMD_OK and np.sum(f_dm**2) > 1e-10:
        for alpha_try in [1000, 500, 200]:
            try:
                K = 3; tau = 0; DC = 0; init = 1; tol = 1e-7
                u, u_hat, omega = _vmd_func(f_dm, alpha_try, tau, K, DC, init, tol)
                final_omega = omega[:, -1]
                trend_idx = int(np.argmin(final_omega))
                trend_mode = u[trend_idx]
                trend_e = float(np.sum(trend_mode**2))
                total_e = float(np.sum(f_dm**2))
                strength = trend_e / total_e if total_e > 0 else 0
                label = '强' if strength > 0.5 else '中' if strength > 0.3 else '弱'
                break
            except Exception:
                continue
    if strength == 0.0:
        # VMD失败, 用R²作为趋势强度替代
        preds = np.polyval([lr_slope, np.mean(f)], x)
        ss_res = float(np.sum((f - preds)**2))
        ss_tot = float(np.sum((f - np.mean(f))**2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        strength = max(0, r2)
        label = '强' if strength > 0.7 else '中' if strength > 0.4 else '弱'
    return {'direction': direction, 'strength': round(strength, 4), 'label': label}

# ===================== 技术指标验证 =====================
def compute_tech_indicators(df_close, df_returns, concept_names, spd, rmt):
    """对每个概念计算技术指标: MA40 + VMD + 横截面动量排名 + SPD趋势延续性"""
    ma40 = df_close.rolling(window=40, min_periods=1).mean()
    mom20 = df_close.pct_change(periods=20) * 100
    latest_mom = mom20.iloc[-1]
    mom_rank = latest_mom.rank(ascending=False, method='min')
    n = len(concept_names)
    tech = {}
    for name in concept_names:
        c_close = df_close[name].dropna()
        c_ma = ma40[name].dropna()
        if len(c_close) == 0 or len(c_ma) == 0:
            tech[name] = {
                'ma40_trend': '?', 'ma40_gap': 0,
                'vmd_dir': '?', 'vmd_str': 0, 'vmd_label': '?',
                'mom_rank': 0, 'n_total': n,
                'sp_angle': 0, 'sp_density': 0,
                'cont_score': 0, 'cont_label': '数据不足',
                'cont_prob': 0, 'cont_method': '数据不足',
            }
            continue
        latest_close = float(c_close.iloc[-1])
        latest_ma = float(c_ma.iloc[-1])
        above_ma = latest_close > latest_ma if latest_ma > 0 else False
        ma_gap = ((latest_close / latest_ma - 1) * 100) if latest_ma > 0 else 0
        ma_trend = '↑' if above_ma else '↓'
        # VMD
        vmd = compute_vmd_analysis(c_close)
        # 横截面动量排名
        rank = int(mom_rank[name])
        # SPD趋势延续性
        s = spd[name]
        sp_angle = s.get('spectral_angle', 0.5)
        sp_density = s.get('spectral_density', 0.5)
        # 趋势延续性概率(SPD谱角30%+谱密度25%+VMD强度25%+MA40确认20%)
        p_angle = max(0, 1 - sp_angle / 1.5) * 100
        p_density = sp_density * 100
        p_vmd = vmd['strength'] * 100
        vmd_up = vmd['direction'] == '↑'
        ma_confirms = (vmd_up and above_ma) or (not vmd_up and not above_ma)
        p_ma = 80 if ma_confirms else 20
        cont_prob = round(p_angle * 0.30 + p_density * 0.25 + p_vmd * 0.25 + p_ma * 0.20)
        cont_score = cont_prob // 15
        reasons = []
        reasons.append('谱角✓' if sp_angle < 0.5 else '谱角✗')
        reasons.append('密度✓' if sp_density > 0.5 else '密度✗')
        reasons.append('VMD✓' if vmd['strength'] > 0.3 else 'VMD✗')
        reasons.append('MA40✓' if ma_confirms else 'MA40✗')
        cont_method = ' '.join(reasons)
        if cont_prob >= 75: cont_label = '高概率延续'
        elif cont_prob >= 55: cont_label = '倾向延续'
        elif cont_prob >= 35: cont_label = '多空分歧'
        elif cont_prob >= 15: cont_label = '倾向反转'
        else: cont_label = '高概率反转'
        tech[name] = {
            'ma40_trend': ma_trend, 'ma40_gap': round(ma_gap, 2),
            'vmd_dir': vmd['direction'], 'vmd_str': vmd['strength'],
            'vmd_label': vmd['label'],
            'mom_rank': rank, 'n_total': n,
            'sp_angle': round(sp_angle, 4), 'sp_density': round(sp_density, 4),
            'cont_score': cont_score, 'cont_label': cont_label,
            'cont_prob': cont_prob, 'cont_method': cont_method,
        }
    return tech

# ===================== 宽基指数多空监控 =====================
INDEX_CONFIG = [
    {'name': '上证指数', 'symbol': 'sh000001'},
    {'name': '深证成指', 'symbol': 'sz399001'},
    {'name': '创业板指', 'symbol': 'sz399006'},
    {'name': '沪深300',  'symbol': 'sh000300'},
    {'name': '上证50',   'symbol': 'sh000016'},
    {'name': '科创50',   'symbol': 'sh000688'},
    {'name': '中证500',  'symbol': 'sh000905'},
    {'name': '中证1000', 'symbol': 'sh000852'},
]

def fetch_index_kline(symbol):
    """拉取宽基指数K线: akshare优先(新浪), 腾讯API回补"""
    if AKSHARE_OK:
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df = df[['close', 'volume']].tail(DAYS)
            if len(df) > 0:
                return df
        except Exception:
            pass
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {'param': f"{symbol},day,,,{DAYS},qfq"}
        resp = SESSION.get(url, params=params, timeout=12)
        data = resp.json()
        kline = data.get('data', {}).get(symbol, {})
        bars = kline.get('qfqday') or kline.get('day')
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
        df = df.dropna(subset=['close'])
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        return df[['close', 'volume']]
    except Exception:
        return None

def analyze_index_sentiment(cfg, close_series, mom_pct):
    """单个宽基指数的多空打分: MA40(±1) + VMD方向(±1/0) + 20日动量(±1) + 5日动量(±0.5) + 横截面相对强度(±0.5/0)
    score范围约[-4, +4]; >=+1.5看多, <=-1.5看空, 其余中性"""
    name = cfg['name']
    f = close_series.ffill().bfill().values.astype(float)
    base = {'name': name, 'score': 0.0, 'view': '数据不足', 'ok': False}
    if len(f) < 21 or np.std(f) < 1e-10:
        return base
    latest = float(f[-1])
    ma40 = float(np.mean(f[-40:])) if len(f) >= 40 else float(np.mean(f))
    ret20 = (latest / f[-21] - 1) * 100 if len(f) >= 21 else 0.0
    ret5 = (latest / f[-6] - 1) * 100 if len(f) >= 6 else 0.0
    s_ma = 1 if latest > ma40 else -1
    vmd = compute_vmd_analysis(close_series)
    s_vmd = 1 if vmd['direction'] == '↑' else -1 if vmd['direction'] == '↓' else 0
    s_mom20 = 1 if ret20 > 0 else -1
    s_mom5 = 0.5 if ret5 > 0 else -0.5
    s_rel = 0.5 if mom_pct >= 2/3 else -0.5 if mom_pct <= 1/3 else 0.0
    score = round(s_ma + s_vmd + s_mom20 + s_mom5 + s_rel, 1)
    view = '看多' if score >= 1.5 else '看空' if score <= -1.5 else '中性'
    return {
        'name': name, 'score': score, 'view': view, 'ok': True,
        'ma40_trend': '↑' if latest > ma40 else '↓',
        'ma40_gap': round((latest / ma40 - 1) * 100, 2) if ma40 > 0 else 0.0,
        'vmd_dir': vmd['direction'], 'vmd_label': vmd['label'],
        'ret20': round(ret20, 2), 'ret5': round(ret5, 2),
        'rel_pct': round(mom_pct * 100),
    }

def analyze_indices():
    """拉取全部宽基指数K线, 依次打分, 汇总看多/看空/中性数量"""
    klines = {}
    for cfg in INDEX_CONFIG:
        df = fetch_index_kline(cfg['symbol'])
        if df is not None and len(df) >= 21:
            klines[cfg['name']] = df
    if not klines:
        return {'bull': 0, 'bear': 0, 'neutral': 0, 'items': []}
    # 横截面相对强度: 20日动量百分位(0~1), 越大越强
    mom20 = {n: (float(d['close'].iloc[-1] / d['close'].iloc[-21] - 1)) for n, d in klines.items()}
    pct = pd.Series(mom20).rank(pct=True)
    items = [analyze_index_sentiment(c, klines[c['name']]['close'], pct[c['name']])
             for c in INDEX_CONFIG if c['name'] in klines]
    items = [it for it in items if it['ok']]
    bull = sum(1 for it in items if it['view'] == '看多')
    bear = sum(1 for it in items if it['view'] == '看空')
    neutral = sum(1 for it in items if it['view'] == '中性')
    return {'bull': bull, 'bear': bear, 'neutral': neutral, 'items': items}

# ===================== 五系统多空数量汇总 =====================
SYS_BULL_EVALS = ('机会', '偏多')
SYS_BEAR_EVALS = ('风险', '偏空')
TOTAL_BULL_EVALS = ('强机会', '机会', '偏多', '弱偏多')
TOTAL_BEAR_EVALS = ('强风险', '风险')

def summarize_votes(integrated):
    """汇总五系统各自及综合口径的看多/看空/中性概念数量"""
    sys_keys = [('hodge_eval', 'Hodge'), ('rmt_eval', 'RMT'), ('spd_eval', 'SPD'),
                ('manning_eval', '曼宁'), ('ot_eval', 'OT')]
    systems = {}
    for key, label in sys_keys:
        bull = sum(1 for s in integrated if s[key] in SYS_BULL_EVALS)
        bear = sum(1 for s in integrated if s[key] in SYS_BEAR_EVALS)
        systems[label] = {'bull': bull, 'bear': bear,
                          'neutral': len(integrated) - bull - bear}
    total_bull = sum(1 for s in integrated if s['eval'] in TOTAL_BULL_EVALS)
    total_bear = sum(1 for s in integrated if s['eval'] in TOTAL_BEAR_EVALS)
    return {
        'n': len(integrated),
        'systems': systems,
        'total': {'bull': total_bull, 'bear': total_bear,
                  'neutral': len(integrated) - total_bull - total_bear},
    }

# ===================== 算法×指数多空矩阵 =====================
# 外部多系统信号文件（工作区各独立指数多空日报的产物）
WORKSPACE_BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
EXT_SIGNAL_FILES = {
    'acvmd': os.path.join(WORKSPACE_BASE, "ses_gffe5fb137971effeOeiFnYksNGm1P0", "ac_vmd_signal.json"),
    'ot': os.path.join(WORKSPACE_BASE, "skills", "user", "optimal-transport-ot", "ot_signal.json"),
    'phygeo': os.path.join(WORKSPACE_BASE, "ses_gffe5fde225ad5ffePQAojl8RuNMiuQ", "results.json"),
    'rmt_dir': os.path.join(WORKSPACE_BASE, "ses_gffe5fcc907173ffeEGsG0e1KvWP05b"),
    'hodge_dir': os.path.join(WORKSPACE_BASE, "ses_00495a421ffeZp6sKsQZoGR6e9"),
    'market_dir': os.path.join(WORKSPACE_BASE, "ses_gffe5f37db22bbffe0KBzq2rKgCxlxy", "data", "daily"),
}

def _safe_load_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def _latest_file(directory, prefix, suffix='.json'):
    try:
        files = sorted(glob.glob(os.path.join(directory, f"{prefix}*{suffix}")))
        return files[-1] if files else None
    except Exception:
        return None

def collect_external_index_views():
    """读取工作区各独立指数多空系统的当日信号, 归类为看多/看空/中性, 全部容错。
    返回 dict: {system_key: {'ok': bool, 'date': str, 'bull': [...], 'bear': [...],
                              'neutral': [...], 'summary': str, 'detail': str}}"""
    out = {}
    today = datetime.now()

    # 1) AC_VMD 指数日报 (5宽基)
    av = _safe_load_json(EXT_SIGNAL_FILES['acvmd'])
    if av and av.get('detail'):
        bull, bear, neu = [], [], []
        detail_lines = []
        for it in av['detail']:
            name, ac, vmd, ma = it.get('name'), it.get('ac_status'), it.get('vmd_direction'), it.get('ma40_position')
            if ma == '上方' and vmd == '突破':
                bull.append(name); v = '看多'
            elif ma == '下方' and vmd == '未突破':
                bear.append(name); v = '看空'
            else:
                neu.append(name); v = '中性'
            detail_lines.append(f"{name}:AC{ac}/VMD{vmd}/MA40{ma}")
        out['acvmd'] = {'ok': True, 'date': av.get('date', str(today.date())),
                        'bull': bull, 'bear': bear, 'neutral': neu,
                        'detail': ' | '.join(detail_lines)}
    else:
        out['acvmd'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '无信号文件'}

    # 2) OT 最优传输多空 (16只)
    ot = _safe_load_json(EXT_SIGNAL_FILES['ot'])
    if ot and ot.get('signals'):
        bull, bear, neu = [], [], []
        for s in ot['signals']:
            if s.get('direction') == 1 or '看多' in s.get('signal', ''):
                bull.append(s['name'])
            elif s.get('direction') == -1 or '看空' in s.get('signal', ''):
                bear.append(s['name'])
            else:
                neu.append(s['name'])
        out['ot'] = {'ok': True, 'date': ot.get('date', str(today.date())),
                     'bull': bull, 'bear': bear, 'neutral': neu, 'detail': ''}
    else:
        out['ot'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '无信号文件'}

    # 3) 物理几何融合 results.json
    pg = _safe_load_json(EXT_SIGNAL_FILES['phygeo'])
    if pg and pg.get('indices'):
        bull, bear, neu = [], [], []
        detail_lines = []
        for k, v in pg['indices'].items():
            f = v.get('fusion', {})
            name = v.get('name', k)
            bp, rp, bep = f.get('bull_prob'), f.get('range_prob'), f.get('bear_prob')
            if bp is None or bep is None:
                neu.append(name); vv = '中性'
            elif bp >= bep and bp > 45:
                bull.append(name); vv = '看多'
            elif bep > bp and bep > 45:
                bear.append(name); vv = '看空'
            else:
                neu.append(name); vv = '中性'
            detail_lines.append(f"{name}:{f.get('judgment','')}")
        out['phygeo'] = {'ok': True, 'date': pg.get('date', str(today.date())),
                         'bull': bull, 'bear': bear, 'neutral': neu,
                         'detail': ' | '.join(detail_lines[:12])}
    else:
        out['phygeo'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '无信号文件'}

    # 4) RMT 量子能级观测 (18行业ETF, rmt_report_YYYYMMDD.json)
    rmtf = _latest_file(EXT_SIGNAL_FILES['rmt_dir'], 'rmt_report_')
    rmt = _safe_load_json(rmtf) if rmtf else None
    if rmt and rmt.get('top5'):
        top_map = {'515880': '通信', '515050': '5G/通信', '512200': '房地产', '512660': '军工',
                   '512480': '半导体', '510880': '红利', '512690': '酒', '510300': '沪深300',
                   '159928': '消费', '159915': '创业板'}
        t_names = [top_map.get(k, k) for k in list(rmt['top5'].keys())[:5]]
        b_names = [top_map.get(k, k) for k in list(rmt['bottom5'].keys())[:5]]
        out['rmt'] = {'ok': True, 'date': rmt.get('date', ''),
                      'bull': t_names, 'bear': b_names, 'neutral': [],
                      'detail': f"状态{rmt.get('status','?')} 动作{rmt.get('action','?')} gap1={rmt.get('gap_1',0):.3f} delta_s={rmt.get('delta_s',0):.3f} snr={rmt.get('snr',0):.1f}"}
    else:
        out['rmt'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '无信号文件'}

    # 5) Hodge Laplacian 板块监测 (hlm_report_YYYY-MM-DD.json)
    hl = _latest_file(EXT_SIGNAL_FILES['hodge_dir'], 'hlm_report_')
    hd = _safe_load_json(hl) if hl else None
    if hd and hd.get('lead_names'):
        out['hodge'] = {'ok': True, 'date': hd.get('date', ''),
                        'bull': hd.get('lead_names', []), 'bear': hd.get('lag_names', []),
                        'neutral': [],
                        'detail': f"基准{hd.get('benchmark')} 位置{hd.get('position','?')}: {hd.get('reason','')}"}
    else:
        out['hodge'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '无信号文件'}

    # 6) 市场状态诊断 (broad_YYYYMMDD.md, 池级归因+单指数分级)
    mkf = _latest_file(EXT_SIGNAL_FILES['market_dir'], 'broad_', suffix='.md')
    market_summary, market_detail = '', ''
    if mkf:
        try:
            with open(mkf, 'r', encoding='utf-8') as f:
                txt = f.read()
            gate = ''
            for line in txt.splitlines():
                if '归因结论' in line:
                    market_summary = line.split('】', 1)[-1].strip() if '】' in line else line.strip()
                if '激活门' in line and gate == '':
                    gate = line.split('】', 1)[-1].strip() if '】' in line else line.strip()
            strong = [ln.split('强势 ')[1].strip().split(':')[0] for ln in txt.splitlines() if '] 强势' in ln]
            risk = [ln.split('风险 ')[1].strip().split(':')[0] for ln in txt.splitlines() if '] 风险' in ln]
            out['market'] = {'ok': True, 'date': os.path.basename(mkf).replace('broad_', '').replace('.md', ''),
                             'bull': strong, 'bear': risk, 'neutral': [],
                             'detail': f"{market_summary} | 激活门:{gate}"}
        except Exception:
            out['market'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '读取失败'}
    else:
        out['market'] = {'ok': False, 'date': '', 'bull': [], 'bear': [], 'neutral': [], 'detail': '无当日文件'}

    return out

INDEX_SHORT = {
    '上证指数': '上证', '深证成指': '深成', '创业板指': '创业板',
    '沪深300': '沪深300', '上证50': '上证50', '科创50': '科创50',
    '中证500': '中证500', '中证1000': '中证1000',
}

def eval_to_sign(ev):
    """多空标签 -> 符号: 看多+1, 看空-1, 中性0"""
    if ev in SYS_BULL_EVALS:
        return 1
    if ev in SYS_BEAR_EVALS:
        return -1
    return 0

def run_index_votes():
    """五系统对8个宽基指数分别打分, 生成算法×指数多空矩阵(+1/-1/0)并统计正负1总数"""
    from concept_rotation_4sys import run_hodge, run_rmt, run_spd, run_manning, run_ot
    klines = {}
    for cfg in INDEX_CONFIG:
        df = fetch_index_kline(cfg['symbol'])
        if df is not None and len(df) >= 21:
            klines[cfg['name']] = df
    empty = {'ok': False, 'rows': [], 'cols': [], 'pos': 0, 'neg': 0, 'zero': 0}
    if not klines:
        return empty
    names = list(klines.keys())
    close = pd.DataFrame({n: d['close'] for n, d in klines.items()}).sort_index().ffill()
    volume = pd.DataFrame({n: d['volume'] for n, d in klines.items()}).sort_index().ffill()
    returns = close.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)
    try:
        systems = {
            'Hodge': run_hodge(returns, close, names),
            'RMT': run_rmt(returns, close, names),
            'SPD': run_spd(returns, close, names),
            '曼宁': run_manning(returns, close, volume, names),
            'OT': run_ot(returns, close, names),
        }
    except Exception:
        return empty
    rows, pos, neg, zero = [], 0, 0, 0
    for label, res in systems.items():
        row = [eval_to_sign(res[n]['eval']) for n in names]
        pos += sum(1 for v in row if v == 1)
        neg += sum(1 for v in row if v == -1)
        zero += sum(1 for v in row if v == 0)
        rows.append({'label': label, 'vals': row})
    return {'ok': True, 'rows': rows, 'cols': names,
            'pos': pos, 'neg': neg, 'zero': zero}

# ===================== 双数据源校验 =====================
def verify_data_sources(akshare_count, tencent_count, fail_count, total):
    """验证akshare和腾讯API都可用"""
    ak_ok = akshare_count > 0
    # 如果腾讯没被调用(akshare全成功), 主动测试腾讯
    if tencent_count == 0 and fail_count == 0:
        try:
            test_df = fetch_kline_tencent('002837')
            tc_ok = test_df is not None and len(test_df) > 0
        except Exception:
            tc_ok = False
    else:
        tc_ok = tencent_count > 0
    return ak_ok, tc_ok

# ===================== 龙头股选股 =====================
def select_leading_stocks(concept_stocks, stock_data, direction='bull', n=2):
    scores = []
    for code_raw, name in concept_stocks.items():
        code = normalize_code(code_raw)
        if code not in stock_data:
            continue
        df = stock_data[code]
        if len(df) < 21:
            continue
        close = df['close']
        vol = df['volume']
        ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0
        ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
        vol_ma20 = vol.iloc[-20:].mean()
        vol_ratio = vol.iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1
        score = ret_20d * 0.4 + ret_5d * 0.3 + min(vol_ratio, 5) * 3
        scores.append({
            'code': code, 'name': name,
            'ret_5d': round(ret_5d, 2), 'ret_20d': round(ret_20d, 2),
            'vol_ratio': round(vol_ratio, 2), 'score': round(score, 2),
        })
    if direction == 'bull':
        scores.sort(key=lambda x: x['score'], reverse=True)
    else:
        scores.sort(key=lambda x: x['score'])
    return scores[:n]

def build_consensus_leading_stocks(integrated, all_concepts, stock_data, n_top=10, per_concept=2):
    """多系统共识流入龙头股榜:
    凡被至少一个系统判定为流入方向('机会'/'偏多')的概念, 选bull龙头股并计入候选;
    每只股票的推荐次数 = 所属概念被几个系统判为流入方向(recs),
    按推荐次数倒序(并列按机会票数、概念总分、5日涨幅)取前n_top只"""
    cands = [s for s in integrated if s['bull_votes'] >= 1]
    if not cands:
        cands = integrated[:10]
    records = []
    for s in cands:
        picks = select_leading_stocks(
            all_concepts.get(s['name'], {}), stock_data, direction='bull', n=per_concept)
        for p in picks:
            records.append({
                'code': p['code'], 'stock_name': p['name'],
                'concept': s['name'],
                'recs': s['bull_votes'],          # 系统流入推荐数(主排序)
                'opp_cnt': s['opp_votes'],        # 系统'机会'票数
                'total_score': s['total_score'],
                'ret_5d': p['ret_5d'], 'ret_20d': p['ret_20d'],
                'vol_ratio': p['vol_ratio'],
            })
    records.sort(key=lambda x: (x['recs'], x['opp_cnt'], x['total_score'], x['ret_5d']), reverse=True)
    return records[:n_top]

# ===================== 飞书发送 =====================
def send_feishu_text(msg):
    payload = {"msg_type": "text", "content": {"text": msg}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
    return r.status_code, r.text

def send_feishu_card(card):
    payload = {"msg_type": "interactive", "card": card}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
    return r.status_code, r.text

# ===================== 报告格式化(交互卡片) =====================
def format_report(date_str, n_concepts, data_status, sys_status, src_check,
                  top_opps, top_risks, stock_picks, tech, consensus_picks,
                  index_summary, vote_summary, index_matrix, ext_views=None):
    W = [3, 1, 2, 2, 1, 2, 3]
    MW = [3, 1, 1, 1, 1]

    def make_row(cells):
        cols = []
        for i, text in enumerate(cells):
            cols.append({
                'tag': 'column', 'width': 'weighted', 'weight': W[i],
                'elements': [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': str(text)}}]
            })
        return {'tag': 'column_set', 'columns': cols}

    def make_row_w(cells, weights):
        cols = []
        for i, text in enumerate(cells):
            cols.append({
                'tag': 'column', 'width': 'weighted', 'weight': weights[i],
                'elements': [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': str(text)}}]
            })
        return {'tag': 'column_set', 'columns': cols}

    def build_matrix_table():
        """算法×指数多空矩阵: 行=算法(5), 列=指数(8, 拆两段各4), 格=+1/-1/0"""
        im = index_matrix
        if not im.get('ok'):
            return [make_row_w(['算法', '数据不足'], MW)]
        short = [INDEX_SHORT.get(n, n) for n in im['cols']]
        v = {r['label']: r['vals'] for r in im['rows']}
        labels = ['Hodge', 'RMT', 'SPD', '曼宁', 'OT']
        elems = []
        # 段1: 前4个指数
        head1 = ['算法'] + short[:4]
        elems.append(make_row_w(head1, MW))
        for lb in labels:
            cells = [lb] + [fmt_sign(x) for x in v[lb][:4]]
            elems.append(make_row_w(cells, MW))
        # 段2: 后4个指数
        head2 = ['算法'] + short[4:]
        elems.append(make_row_w(head2, MW))
        for lb in labels:
            cells = [lb] + [fmt_sign(x) for x in v[lb][4:]]
            elems.append(make_row_w(cells, MW))
        return elems

    def fmt_sign(x):
        if x == 0:
            return '0'
        return f"{x:+d}"

    def build_table(items, is_opp=True):
        rows = [make_row(['概念', '评分', 'MA40', 'VMD', '动量', '延续概率', '判断依据'])]
        for i, s in enumerate(items):
            ti = tech.get(s['name'], {})
            ev = s['eval']
            if is_opp:
                marker = '★★' if ev == '强机会' else '★' if ev == '机会' else '▲'
            else:
                marker = '✕✕' if ev == '强风险' else '✕' if ev == '风险' else '▼'
            mt = ti.get('ma40_trend', '?')
            ma40_str = f"{mt}线{ti.get('ma40_gap',0):+.1f}%" if mt != '?' else '?'
            vd = ti.get('vmd_dir', '?')
            vmd_str = f"{vd}{ti.get('vmd_label','?')}({ti.get('vmd_str',0):.2f})" if vd != '?' else '?'
            rows.append(make_row([
                f"{i+1}.{s['name']}", f"{s['total_score']:+d}{marker}",
                ma40_str, vmd_str, f"{ti.get('mom_rank',0)}/{ti.get('n_total',0)}",
                f"{ti.get('cont_prob',0)}% {ti.get('cont_label','')}",
                ti.get('cont_method', '数据不足'),
            ]))
        return rows

    ak_s = "✓" if src_check['akshare'] else "✗"
    tc_s = "✓" if src_check['tencent'] else "✗"
    src_ok = src_check['akshare'] and src_check['tencent']
    data_text = (
        f"**数据状态** ({date_str} | {n_concepts}概念)\n"
        f"数据源: akshare(sina) + 腾讯API\n"
        f"获取: {data_status['success']}/{data_status['total']}只 "
        f"(ak:{data_status['akshare']} 腾讯:{data_status['tencent']} 失败:{data_status['failed']})\n"
        f"双源校验: akshare{ak_s} 腾讯{tc_s} {'双源正常' if src_ok else '源异常'}"
    )
    sys_text = (
        f"**系统状态**\n"
        f"Hodge: {sys_status['hodge']}\n"
        f"RMT: {sys_status['rmt']}\n"
        f"SPD: {sys_status['spd']}\n"
        f"曼宁: {sys_status['manning']}\n"
        f"OT: {sys_status['ot']}"
    )
    index_lines = [f"**指数多空监控** (看多 {index_summary['bull']} | 看空 {index_summary['bear']} | 中性 {index_summary['neutral']})"]
    if index_summary['items']:
        for i, it in enumerate(index_summary['items'], 1):
            vd_txt = f"{it['vmd_dir']}{it['vmd_label']}" if it['vmd_dir'] in ('↑', '↓') else '平'
            index_lines.append(
                f"{i}. {it['name']} {it['score']:+.1f} {it['view']} | "
                f"MA40{it['ma40_trend']}线{it['ma40_gap']:+.1f}% | "
                f"VMD{vd_txt} | "
                f"20日{it['ret20']:+.1f}% 5日{it['ret5']:+.1f}% | "
                f"相对{it['rel_pct']}/100"
            )
    else:
        index_lines.append("今日无指数数据")
    index_text = '\n'.join(index_lines)
    im = index_matrix
    if im.get('ok'):
        matrix_lines = [
            f"**算法×指数多空矩阵** (看多+1 {im['pos']} | 看空-1 {im['neg']} | 中性0 {im['zero']})",
            "行=算法 列=指数  +1看多 -1看空 0中性",
        ]
    else:
        matrix_lines = ["**算法×指数多空矩阵** (今日指数数据不足)"]
    matrix_text = '\n'.join(matrix_lines)
    # 多系统指数归类 (外部独立的指数多空日报系统)
    ext_lines = ["**多系统指数归类** (AC_VMD/OT/物理几何/RMT量子/Hodge板块/市场状态)"]
    if ext_views:
        order = [('acvmd', 'AC_VMD'), ('ot', 'OT多空'), ('phygeo', '物理几何'),
                 ('rmt', 'RMT量子'), ('hodge', 'Hodge板块'), ('market', '市场状态')]
        any_ok = False
        for key, label in order:
            v = ext_views.get(key) or {}
            if not v.get('ok'):
                continue
            any_ok = True
            d = v.get('date', '')
            bull = v.get('bull', []); bear = v.get('bear', []); neu = v.get('neutral', [])
            bl = ('看多:' + '/'.join(bull[:6])) if bull else ''
            br = ('看空:' + '/'.join(bear[:6])) if bear else ''
            nu = ('中性:' + '/'.join(neu[:6])) if neu else ''
            seg = ' '.join(x for x in [bl, br, nu] if x)
            if not seg and v.get('detail'):
                seg = v['detail']
            ext_lines.append(f"{label}{f'({d})' if d else ''}: {seg}")
        n_ok = sum(1 for k, _ in order if (ext_views.get(k) or {}).get('ok'))
        if not any_ok:
            ext_lines.append("外部系统信号暂不可用(今日未生成)")
        else:
            ext_lines.append(f"(可用系统 {n_ok}/6)")
    else:
        ext_lines.append("外部系统信号暂不可用")
    ext_text = '\n'.join(ext_lines)
    vs = vote_summary
    vote_lines = [f"**五系统多空汇总** ({vs['n']}个概念)"]
    first_row, second_row = [], []
    for i, (label, v) in enumerate(vs['systems'].items()):
        txt = f"{label} 看多{v['bull']}/看空{v['bear']}/中性{v['neutral']}"
        (first_row if i < 3 else second_row).append(txt)
    vote_lines.append("综合(五票口径): 看多{} | 看空{} | 中性{}".format(
        vs['total']['bull'], vs['total']['bear'], vs['total']['neutral']))
    vote_lines.append('  '.join(first_row))
    if second_row:
        vote_lines.append('  '.join(second_row))
    vote_text = '\n'.join(vote_lines)
    method_text = (
        "**延续概率方法**\n"
        "概率 = SPD谱角(30%) + SPD谱密度(25%) + VMD强度(25%) + MA40确认(20%)\n"
        ">=75%高概率延续 | 55-74%倾向延续 | 35-54%多空分歧 | 15-34%倾向反转 | <15%高概率反转"
    )
    picks_lines = ["**龙头股**"]
    for s in top_opps + top_risks:
        picks = stock_picks.get(s['name'], [])
        for p in picks:
            picks_lines.append(
                f"{s['name']}: {p['name']}({p['code']}) 20日{p['ret_20d']:+.1f}% 5日{p['ret_5d']:+.1f}% 量比{p['vol_ratio']:.1f}"
            )
    consensus_lines = ["**多系统共识流入龙头股 TOP10** (推荐次数=被多少系统判为流入方向)"]
    if consensus_picks:
        for i, p in enumerate(consensus_picks, 1):
            opp_txt = f"(机会{p['opp_cnt']})" if p['opp_cnt'] > 0 else ""
            consensus_lines.append(
                f"{i}. {p['stock_name']}({p['code']}) {p['concept']} | "
                f"推荐{p['recs']}/5{opp_txt} "
                f"20日{p['ret_20d']:+.1f}% 5日{p['ret_5d']:+.1f}%"
            )
    else:
        consensus_lines.append("今日无多系统共识流入龙头股")

    _has_risk = any(s.get('eval') == '强风险' for s in (top_risks or []))
    _has_opp = any(s.get('eval') == '强机会' for s in (top_opps or []))
    _mark = ('注意风险 | ' if _has_risk else '') + ('注意机会 | ' if _has_opp else '')
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": f"多系统扩展概念轮动日报 | {_mark}来自 DoMate"},
            "template": "blue"
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": data_text}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": sys_text}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": index_text}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": matrix_text}},
            *build_matrix_table(),
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": ext_text}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": vote_text}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": method_text}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "**TOP10 机会概念**"}},
            *build_table(top_opps, True),
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "**TOP10 风险概念**"}},
            *build_table(top_risks, False),
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": '\n'.join(consensus_lines)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": '\n'.join(picks_lines)}},
        ]
    }
    card["elements"] += _bs('concept_rotation', 'card')
    return card

# ===================== 主函数 =====================
def main():
    print("=" * 80, file=sys.stderr)
    print("多系统扩展概念轮动日报 v4 (五系统选概念 + 六系统指数归类)", file=sys.stderr)
    print("=" * 80, file=sys.stderr)

    # Step 1: 概念成分股
    print("[1/7] 构建概念成分股...", file=sys.stderr)
    all_concepts = {}
    for cname, stocks in MANUAL_CONCEPTS.items():
        all_concepts[cname] = {normalize_code(c): n for c, n in stocks.items()}
    for node, cname in SINA_CONCEPTS.items():
        stocks = get_sina_concept_stocks(node, MAX_STOCKS_PER_CONCEPT)
        if stocks:
            all_concepts[cname] = {normalize_code(c): n for c, n in stocks.items()}
        time.sleep(0.12)
    all_codes = set()
    for stocks in all_concepts.values():
        all_codes.update(stocks.keys())
    all_codes = list(all_codes)
    print(f"  {len(all_concepts)}概念, {len(all_codes)}只个股", file=sys.stderr)

    # Step 2: 拉取K线
    print(f"[2/7] 拉取K线({len(all_codes)}只)...", file=sys.stderr)
    stock_data = {}
    akshare_count = 0; tencent_count = 0; fail_count = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_kline, code): code for code in all_codes}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  进度: {done}/{len(all_codes)}", file=sys.stderr)
            code = futures[f]
            try:
                df, source = f.result()
                if df is not None and len(df) > 0:
                    stock_data[code] = df
                    if source == 'akshare': akshare_count += 1
                    elif source == 'tencent': tencent_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1
    total = len(all_codes); success = len(stock_data)
    print(f"  成功:{success}/{total} (ak:{akshare_count} 腾讯:{tencent_count} 失败:{fail_count})", file=sys.stderr)

    # Step 3: 双数据源校验
    print("[3/7] 双数据源校验...", file=sys.stderr)
    ak_ok, tc_ok = verify_data_sources(akshare_count, tencent_count, fail_count, total)
    print(f"  akshare:{'✓' if ak_ok else '✗'} 腾讯:{'✓' if tc_ok else '✗'}", file=sys.stderr)

    if not ak_ok and not tc_ok:
        msg = f"{FEISHU_HEADER}\n\n⚠️ 双数据源均不可用!\nakshare:{akshare_count} 腾讯:{tencent_count}\n请检查网络和API状态"
        send_feishu_text(msg)
        print("双源全失败, 已发飞书告警", file=sys.stderr)
        return

    if not ak_ok or not tc_ok:
        warn = "akshare异常" if not ak_ok else "腾讯API异常"
        msg = f"{FEISHU_HEADER}\n\n⚠️ {warn}! 双源未全部通过,不发报告\nakshare:{akshare_count} 腾讯:{tencent_count}\n请检查网络和API状态"
        send_feishu_text(msg)
        print(f"  {warn}, 已发飞书告警, 不发报告", file=sys.stderr)
        return

    if success < total * 0.2:
        msg = f"{FEISHU_HEADER}\n\n⚠️ 数据获取严重失败: {success}/{total}只\n请检查网络"
        send_feishu_text(msg)
        return

    # Step 4: 合成概念指数
    print("[4/7] 合成概念指数...", file=sys.stderr)
    df_returns, df_close, df_volume = build_concept_data(all_concepts, stock_data)
    valid_cols = [c for c in df_close.columns if len(df_close[c].dropna()) >= DAYS]
    df_returns = df_returns[valid_cols]; df_close = df_close[valid_cols]; df_volume = df_volume[valid_cols]
    concept_names = list(df_returns.columns)
    print(f"  有效概念:{len(concept_names)}", file=sys.stderr)
    if len(df_returns) < 30:
        msg = f"{FEISHU_HEADER}\n\n⚠️ 数据不足: 仅{len(df_returns)}交易日"
        send_feishu_text(msg)
        return

    # 数据新鲜度闸门：合成概念指数最新日期必须是最近交易日（周末/节假日允许向后偏移）
    idx_last = df_close.index[-1].date()
    today_d = datetime.now().date()
    days_gap = (today_d - idx_last).days
    if days_gap > 4:
        msg = (f"{FEISHU_HEADER}\n\n⚠️ 数据陈旧,已拒绝生成报告!\n"
               f"概念指数最新日期: {idx_last}\n今天: {today_d}\n"
               f"相差 {days_gap} 天,超过4天容忍阈值\n"
               f"个股成功数: {success}/{total} (ak:{akshare_count} 腾讯:{tencent_count})\n"
               f"请检查数据接口,确保拉到最新行情")
        send_feishu_text(msg)
        print(f"  [DATA-GATE] 概念指数最新日期 {idx_last} 距今 {days_gap} 天,拒绝发报告", file=sys.stderr)
        return
    print(f"  数据新鲜度OK: 概念指数最新 {idx_last}, 距今 {days_gap} 天", file=sys.stderr)

    # Step 5: 五系统
    print("[5/7] 运行五系统...", file=sys.stderr)
    hodge = run_hodge(df_returns, df_close, concept_names)
    rmt = run_rmt(df_returns, df_close, concept_names)
    spd = run_spd(df_returns, df_close, concept_names)
    manning = run_manning(df_returns, df_close, df_volume, concept_names)
    ot = run_ot(df_returns, df_close, concept_names)
    print("  五系统完成", file=sys.stderr)

    # Step 6: 技术指标验证
    print("[6/7] 技术指标验证(MA40+VMD+横截面+SPD延续性)...", file=sys.stderr)
    tech = compute_tech_indicators(df_close, df_returns, concept_names, spd, rmt)

    # 宽基指数多空监控
    print("  指数多空监控(8宽基指数)...", file=sys.stderr)
    index_summary = analyze_indices()
    print(f"  指数多空: 看多{index_summary['bull']} 看空{index_summary['bear']} 中性{index_summary['neutral']}", file=sys.stderr)

    # 算法×指数多空矩阵
    print("  算法×指数多空矩阵(五系统×8指数)...", file=sys.stderr)
    index_matrix = run_index_votes()
    if index_matrix.get('ok'):
        print(f"  矩阵: 看多+1共{index_matrix['pos']} 看空-1共{index_matrix['neg']} 中性0共{index_matrix['zero']}", file=sys.stderr)
    else:
        print("  矩阵: 指数数据不足", file=sys.stderr)

    # 整合 + 选龙头股
    integrated = []
    for name in concept_names:
        h = hodge[name]; r = rmt[name]; s = spd[name]; m = manning[name]; o = ot[name]
        total_score = h['score'] + r['score'] + s['score'] + m['score'] + o['score']
        opp_votes = sum(1 for x in [h, r, s, m, o] if x['eval'] == '机会')
        bull_votes = sum(1 for x in [h, r, s, m, o] if x['eval'] in ['机会', '偏多'])
        risk_votes = sum(1 for x in [h, r, s, m, o] if x['eval'] in ['风险', '偏空'])
        if opp_votes >= 4: ev = '强机会'
        elif opp_votes >= 3: ev = '机会'
        elif bull_votes >= 4: ev = '偏多'
        elif bull_votes >= 1 and risk_votes == 0: ev = '弱偏多'
        elif risk_votes >= 4: ev = '强风险'
        elif risk_votes >= 3: ev = '风险'
        else: ev = '中性'
        integrated.append({
            'name': name, 'total_score': total_score, 'eval': ev,
            'opp_votes': opp_votes, 'bull_votes': bull_votes, 'risk_votes': risk_votes,
            'hodge_eval': h['eval'], 'rmt_eval': r['eval'],
            'spd_eval': s['eval'], 'manning_eval': m['eval'], 'ot_eval': o['eval'],
            'mom': h.get('mom', 0), 'delta_s': r.get('delta_s', 0),
            'rmt_mode': r.get('rmt_mode', ''),
            'spectral_angle': s.get('spectral_angle', 0), 'cmf': m.get('cmf', 0),
            'w1': o.get('w1', 0), 'w1_active': o.get('w1_active', False),
        })
    integrated.sort(key=lambda x: x['total_score'], reverse=True)
    top_opps = integrated[:10]
    top_risks = list(reversed(integrated[-10:]))

    stock_picks = {}
    for s in top_opps:
        stock_picks[s['name']] = select_leading_stocks(
            all_concepts.get(s['name'], {}), stock_data, direction='bull', n=2)
    for s in top_risks:
        stock_picks[s['name']] = select_leading_stocks(
            all_concepts.get(s['name'], {}), stock_data, direction='bear', n=2)

    # 多系统共识流入龙头股TOP10 (按系统流入推荐次数倒排)
    consensus_picks = build_consensus_leading_stocks(integrated, all_concepts, stock_data)
    print(f"  多系统共识流入龙头股TOP{len(consensus_picks)}只", file=sys.stderr)

    # Step 7: 格式化 + 发飞书
    print("[7/7] 格式化报告 + 发飞书...", file=sys.stderr)
    date_str = str(df_close.index[-1].strftime('%Y-%m-%d')) if len(df_close) > 0 else datetime.now().strftime('%Y-%m-%d')
    data_status = {'success': success, 'total': total,
                   'akshare': akshare_count, 'tencent': tencent_count, 'failed': fail_count}
    src_check = {'akshare': ak_ok, 'tencent': tc_ok}
    sys_status = {
        'hodge': f"结构{hodge[concept_names[0]].get('stability', '')} 谱角{hodge[concept_names[0]].get('latest_angle', 0):.4f}",
        'rmt': f"{rmt[concept_names[0]].get('rmt_mode', '')} delta_s={rmt[concept_names[0]].get('delta_s', 0):.4f}",
        'spd': f"谱密度{spd[concept_names[0]].get('spectral_density', 0):.4f}",
        'manning': f"{sum(1 for n in concept_names if manning[n].get('cmf', 0) > 0)}净流入 "
                   f"{sum(1 for n in concept_names if manning[n].get('cmf', 0) < 0)}净流出",
        'ot': f"W1={ot[concept_names[0]].get('w1', 0):.6f} "
              f"{'漂移活跃' if ot[concept_names[0]].get('w1_active', False) else '分布稳定'} "
              f"{sum(1 for n in concept_names if ot[n].get('w1_active', False))}活跃 "
              f"{sum(1 for n in concept_names if not ot[n].get('w1_active', False))}稳定",
    }

    # 多空数量汇总
    vote_summary = summarize_votes(integrated)
    print("  多空汇总: 综合", vote_summary['total'], file=sys.stderr)
    for label, v in vote_summary['systems'].items():
        print(f"    {label}: 看多{v['bull']} 看空{v['bear']} 中性{v['neutral']}", file=sys.stderr)

    # 外部六系统指数归并 (AC_VMD/OT/物理几何/RMT量子/Hodge板块/市场状态)
    print("  外部六系统指数归类(AC_VMD/OT/物理几何/RMT/Hodge/市场状态)...", file=sys.stderr)
    ext_views = collect_external_index_views()
    for key, v in ext_views.items():
        if v.get('ok'):
            print(f"    {key}: 看多{len(v['bull'])} 看空{len(v['bear'])} 中性{len(v['neutral'])}", file=sys.stderr)
        else:
            print(f"    {key}: 无信号", file=sys.stderr)

    report = format_report(date_str, len(concept_names), data_status, sys_status,
                           src_check, top_opps, top_risks, stock_picks, tech, consensus_picks,
                           index_summary, vote_summary, index_matrix, ext_views)
    print(f"  报告已生成(交互卡片)", file=sys.stderr)
    code, resp = send_feishu_card(report)
    print(f"\n飞书发送: HTTP {code} | {resp}", file=sys.stderr)
    if code == 200 and '"code":0' in resp:
        print("报告发送成功!", file=sys.stderr)
    else:
        print(f"发送失败或被拒绝: {resp}", file=sys.stderr)
        time.sleep(3)
        code2, resp2 = send_feishu_card(report)
        print(f"重试: HTTP {code2}", file=sys.stderr)

if __name__ == '__main__':
    main()
