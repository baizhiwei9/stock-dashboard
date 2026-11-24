#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场临界监测 C(t) & Gap —— 日频指标计算（真实行情，不编造）

口径（沿用既有 RMT 监测的标的池与窗口，保证与历史任务一致）：
  标的池 : 18 只行业/宽基 ETF（与 rmt_daily_monitor.py 的 ETF_LIST 相同）
  窗口   : 60 个交易日收益率滚动相关矩阵（WINDOW=60）
  特征值 : 每日在窗口末端重算相关矩阵特征分解，得到 λ1、λ2 的日频序列
  指标   : C(t)   = λ1 / Σλ × 100   （Σλ = trace = 标的数量 → 第一主成分吸收率 / 市场协同度）
           Gap(t) = λ1 / λ2
           Δλ1    = λ1(t) − λ1(t−1)  （一致性日变化）
           IPR    = Σ(v1_i)^4         （第一特征向量参与率 → 龙头拥挤度）
           分位   = 当日 λ1 在最近 250 个交易日 λ1 中的百分位
           量能   = 池内合计成交量（今日）/ 前 20 日均值

数据铁律：最新交易日必须是今天（交易日）；缺当日真实数据的标的不静默 ffill，直接拒绝。
"""
import os, sys, json, time, re, warnings
import numpy as np
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from cgap_opportunity import compute_opportunity
from cgap_drilldown import run_drilldown, to_csv_rows, ETF_DIRECTION
# 合并：RMT 预测监测（原 rmt_daily_monitor）的指标与红/黄/绿灯直接复用其函数，不在本文件重复实现
from rmt_daily_monitor import (compute_rmt, evaluate_alert, ALERT_RED_DELTA_S,
                               ALERT_WARN_DELTA_S, ALERT_GREEN_ENTROPY, ALERT_GREEN_DELTA_S)
from stock_picker import get_top_stocks_for_etf

warnings.filterwarnings('ignore')

# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
# 取数口径全部改由 data_hub.collectors.stocks / misc 提供，本文件不再直连腾讯行情接口。
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WORK = os.path.dirname(os.path.abspath(__file__))
WINDOW = 60          # 相关矩阵滚动窗口（交易日）
QWIN = 250           # λ1 滚动分位窗口
VOLWIN = 20          # 成交量变化率基准窗口
HIST_PATH = os.path.join(WORK, 'rmt_cgap_history.json')
TODAY_PATH = os.path.join(WORK, 'rmt_cgap_today.json')

ETF_LIST = {
    '512480': 'sh', '512760': 'sh', '515880': 'sh', '515050': 'sh',
    '510300': 'sh', '510500': 'sh', '512100': 'sh', '512890': 'sh',
    '512200': 'sh', '512010': 'sh', '512690': 'sh', '512800': 'sh',
    '512660': 'sh', '159915': 'sz', '510050': 'sh', '510880': 'sh',
    '512720': 'sh', '159928': 'sz',
}
ETF_NAMES = {
    '512480': '半导体', '512760': '芯片', '515880': '通信', '515050': '5G通信',
    '510300': '沪深300', '510500': '中证500', '512100': '中证1000', '512890': '红利低波',
    '512200': '房地产', '512010': '医药', '512690': '酒', '512800': '银行',
    '512660': '军工', '159915': '创业板', '510050': '上证50', '510880': '红利',
    '512720': '计算机', '159928': '消费',
}

# 全A指数：仅作图上叠加对照（看 C(t)/Gap 变动时全市场是涨是跌），不并入相关矩阵，
# 因此 λ1/Σλ 与六条阈值线口径保持不变。取公开可得的中证全指（000985）作为全A口径。
INDEX_CODE = 'sh000985'
INDEX_NAME = '全A（中证全指 000985）'


_INDEX_SUFFIX = {
    '000001': '.SH', '000016': '.SH', '000300': '.SH', '000688': '.SH',
    '000905': '.SH', '000852': '.SH', '000985': '.CSI',
    '399001': '.SZ', '399006': '.SZ', '399106': '.SZ',
}


def _split_symbol(code):
    """'sh512480' -> ('etf','512480')；'sh000985' -> ('index','000985.CSI')。"""
    raw = str(code).strip().lower()
    prefix = raw[:2] if raw[:2] in ('sh', 'sz') else ''
    num = (raw[2:] if prefix else raw).split('.')[0]
    if num.startswith(('15', '16', '51', '52', '56', '58')):
        return 'etf', num
    if num in _INDEX_SUFFIX:
        return 'index', num + _INDEX_SUFFIX[num]
    return 'index', num + ('.SH' if (prefix == 'sh' or num.startswith('000')) else '.SZ')


def _norm_date(value):
    """DataHub 的 YYYYMMDD -> 与旧腾讯 K 线一致的 YYYY-MM-DD。"""
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return f'{text[:4]}-{text[4:6]}-{text[6:8]}'
    return text[:10]


def fetch_kline(code, days=800):
    """DataHub 股票模块前复权日线：返回 {date: (close, volume)}。"""
    kind, sym = _split_symbol(code)
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, start_date=start, end_date=end)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, start_date=start, end_date=end)
        if df is None or len(df) == 0:
            print(f'  dataHub {code}({sym}) 日线返回空')
            return {}
        dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
        bars = {}
        for _, row in df.iterrows():
            try:
                close = float(row['close'])
            except Exception:
                continue
            vol = np.nan
            if 'vol' in df.columns and pd.notna(row.get('vol')):
                try:
                    vol = float(row['vol'])
                except Exception:
                    vol = np.nan
            bars[_norm_date(row[dcol])] = (close, vol)
        return bars
    except Exception as e:
        print(f'  fetch_kline({code}) dataHub error: {e}')
        return {}


def fetch_realtime_quote(code):
    """DataHub 快照：返回 (最新价, 成交量)。

    先取当日日线（含成交量）；当日日线未发布时退实时价，成交量如实返回 None，
    由调用方决定是否留 NaN，不静默填 0。
    """
    kind, sym = _split_symbol(code)
    today = datetime.now().strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, trade_date=today)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, trade_date=today)
        if df is not None and len(df) > 0:
            # 只认当日那一行：历史/区间接口可能只返回到前一交易日，
            # 直接取 iloc[-1] 会把「昨日收盘」当成「今日收盘」写进指标，属数据造假，必须校验日期。
            dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
            hit = df[df[dcol].astype(str).str.replace('-', '').str[:8] == today]
            if len(hit) > 0:
                row = hit.iloc[-1]
                px = float(row['close']) if pd.notna(row.get('close')) else None
                vol = None
                if 'vol' in df.columns and pd.notna(row.get('vol')):
                    vol = float(row['vol'])
                return px, vol
    except Exception:
        pass
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_realtime_spot
            px, _src = get_realtime_spot(sym, verbose=False)
            return px, None
        from data_hub.collectors.stocks import get_index_spot
        spot = get_index_spot(index_codes=[code])
        if spot is None or len(spot) == 0:
            return None, None
        row = spot.iloc[0]
        px = float(row['price']) if pd.notna(row.get('price')) else None
        vol = float(row['volume']) if pd.notna(row.get('volume')) else None
        return px, vol
    except Exception:
        return None, None


def is_trading_day():
    today = datetime.now().date()
    if today.weekday() >= 5:
        return False
    try:
        from data_hub import edb
        day = today.strftime('%Y%m%d')
        cal = edb.get_dataset('CN.MARKET.CAL.TRADE', start_date=day, end_date=day)
        return cal is not None and len(cal) > 0
    except Exception as e:
        # 交易日历不可用时不拦截任务，交由下游数据闸门判定当日是否真有行情
        print(f'  is_trading_day dataHub 交易日历不可用: {e}')
        return True


ETF_WORKERS = 6     # DataHub 并发档位：18 只 ETF 并行取数，同时避免把接口打到限流


def _warm_hub():
    """线程池启动前预热 DataHub 模块，避免多线程同时 import 触发 ModuleLock 死锁。"""
    try:
        from data_hub.collectors.misc import get_etf_daily, get_realtime_spot  # noqa: F401
        from data_hub.collectors.stocks import get_index_daily, get_index_spot  # noqa: F401
    except Exception as e:
        print(f'  [WARN] DataHub 模块预热失败: {e}')


def fetch_bars_parallel(codes, days=800):
    """并行取多只标的日线，返回 {code: {date: (close, vol)}}，键序与 codes 一致。"""
    out = {}
    if not codes:
        return out
    with ThreadPoolExecutor(max_workers=min(ETF_WORKERS, len(codes))) as ex:
        futs = {ex.submit(fetch_kline, f'{ETF_LIST[c]}{c}', days): c for c in codes}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                out[c] = fut.result() or {}
            except Exception as e:
                print(f'  fetch_kline({c}) 并行取数异常: {e}')
                out[c] = {}
    return {c: out.get(c, {}) for c in codes}


def fetch_quotes_parallel(codes):
    """并行取快照，返回 {code: (px, vol)}。"""
    out = {}
    if not codes:
        return out
    with ThreadPoolExecutor(max_workers=min(ETF_WORKERS, len(codes))) as ex:
        futs = {ex.submit(fetch_realtime_quote, f'{ETF_LIST[c]}{c}'): c for c in codes}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                out[c] = fut.result()
            except Exception:
                out[c] = (None, None)
    return out


def load_prices():
    """拉取 ETF 日线（收盘 + 成交量），含重试与实时兜底，返回 (df_close, df_vol)。

    取数走 DataHub，18 只 ETF 并行（ETF_WORKERS 并发），重试与实时兜底同样并行；
    回填与打印按 ETF_LIST 顺序进行，输出与串行版逐字一致。
    """
    print('Fetching ETF data...')
    _warm_hub()
    codes_all = list(ETF_LIST.keys())
    all_bars = {}
    for code, bars in fetch_bars_parallel(codes_all).items():
        for d, (c, v) in bars.items():
            all_bars.setdefault(d, {})[code] = (c, v)

    dates_sorted = sorted(all_bars.keys())
    max_d = dates_sorted[-1] if dates_sorted else None

    # 第一轮重试：K线接口盘后分批更新（缺当日 K 线的标的并行补取一次）
    if max_d:
        retry = [c for c in codes_all if c not in all_bars.get(max_d, {})]
        if retry:
            time.sleep(1.0)
            for code, bars in fetch_bars_parallel(retry).items():
                for d, (c, v) in bars.items():
                    all_bars.setdefault(d, {})[code] = (c, v)

    # 第二轮兜底：实时接口补当日收盘价与当日成交量。
    # 盘后早期日线接口常只发布部分标的的当日 K 线（或成交量字段为空），此前兜底只补价格、
    # 成交量留 NaN，导致池内合计成交量被低估成"量能骤降"。此处一并补齐。
    if max_d:
        need = []
        for code in codes_all:
            bar = all_bars.get(max_d, {}).get(code)
            if bar is None or pd.isna(bar[0]) or pd.isna(bar[1]):
                need.append(code)
        quotes = fetch_quotes_parallel(need)
        for code in need:
            bar = all_bars.get(max_d, {}).get(code)
            px, vol = quotes.get(code, (None, None))
            if px is None and (bar is None or pd.isna(bar[0])):
                print(f'  !! {code}({ETF_NAMES.get(code, code)}) 实时接口也无数据')
                continue
            new_px = bar[0] if (bar is not None and not pd.isna(bar[0])) else px
            new_vol = bar[1] if (bar is not None and not pd.isna(bar[1])) else vol
            # 成交量单位校正：不同数据源「手 / 股」相差 100 倍。兜底档与本标的近 20 日序列相比
            # 若量级差到 1/50 以下，按 手→股 ×100 校正，避免把单位差异误报成「量能骤降」。
            if new_vol is not None and not pd.isna(new_vol):
                prev = [all_bars[d][code][1] for d in dates_sorted[:-1]
                        if code in all_bars.get(d, {})
                        and not pd.isna(all_bars[d][code][1])][-20:]
                if prev:
                    _med = float(np.median(prev))
                    if _med > 0 and new_vol < _med * 0.02:
                        print(f'  [单位校正] {code}({ETF_NAMES.get(code, code)}) 成交量 '
                              f'{new_vol:.4g} -> {new_vol * 100:.4g}（按手→股 ×100）')
                        new_vol = new_vol * 100.0
            all_bars.setdefault(max_d, {})[code] = (
                new_px, new_vol if new_vol is not None else np.nan)
            print(f'  实时兜底 {code}({ETF_NAMES.get(code, code)}) {max_d}: '
                  f'close={new_px} vol={new_vol}')

    dates_sorted = sorted(all_bars.keys())
    codes = list(ETF_LIST.keys())
    df_close = pd.DataFrame({'date': pd.to_datetime(dates_sorted)})
    df_vol = pd.DataFrame({'date': pd.to_datetime(dates_sorted)})
    for code in codes:
        df_close[code] = [all_bars[d].get(code, (np.nan, np.nan))[0] for d in dates_sorted]
        df_vol[code] = [all_bars[d].get(code, (np.nan, np.nan))[1] for d in dates_sorted]

    # 剔除数据不足的标的
    codes = [c for c in codes if df_close[c].notna().sum() > 10]
    df_close = df_close[['date'] + codes]
    df_vol = df_vol[['date'] + codes]

    latest_d = df_close['date'].max().date()
    today = datetime.now().date()
    last_row = df_close.iloc[-1]
    missing = [c for c in codes if pd.isna(last_row[c])]
    print(f'  ETF: {len(df_close)} bars, {len(codes)} codes, latest {latest_d}, '
          f'完整度 {len(codes) - len(missing)}/{len(codes)}')
    if latest_d < today:
        if is_trading_day():
            print(f'[DATA-GATE] ETF数据日期({latest_d})落后于今天({today})，拒绝使用旧数据')
            sys.exit(3)
        print(f'[DATA-GATE] 今日非交易日，使用最近交易日({latest_d})数据')
    if missing:
        print(f'[DATA-GATE] 缺少最新交易日({latest_d})真实数据: {",".join(missing)}，拒绝使用旧数据')
        sys.exit(3)
    # 窗口完整度：最近 WINDOW+5 根内若有缺失，相关矩阵会退化为 0 相关，需如实标注
    tail = df_close[codes].tail(WINDOW + 5)
    nan_counts = {c: int(tail[c].isna().sum()) for c in codes if tail[c].isna().sum() > 0}
    print(f'  窗口内缺失: {nan_counts if nan_counts else "无（窗口内数据完整）"}')
    return df_close, df_vol


def eigen_metrics(win_ret):
    """对 60 日收益率窗口算 (λ1, λ2, λ3, Σλ, IPR, 熵, 复杂度缺口, gap₁)。

    与既有管线同口径清洗相关矩阵。新增三项（只用于展示，不参与预警判定）：
      熵 S = −Σ p ln p，p = λ/Σλ；复杂度缺口 = ln(N) − S（越大 = 谱越集中 = 分散化失效）；
      gap₁ = (λ₁ − λ₂)/λ₁（两个最大特征值之差，相对 λ₁）。
    """
    C = win_ret.corr().values
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    C = (C + C.T) / 2
    vals, vecs = np.linalg.eigh(C)
    order = np.argsort(vals)[::-1]
    vals = np.maximum(vals[order], 1e-12)
    v1 = vecs[:, order[0]]
    s = float(vals.sum())
    ipr = float(np.sum(v1 ** 4))
    p = vals / s
    entropy = float(-np.sum(p * np.log(p)))
    return (float(vals[0]), float(vals[1]), float(vals[2]), s, ipr,
            entropy, float(np.log(len(vals)) - entropy), float((vals[0] - vals[1]) / vals[0]))


def roll_pct_series(s, win):
    """滚动分位（0~100）：当日值在过去 win 个交易日中的百分位；样本不足或缺失记为 NaN。"""
    out = []
    for i in range(len(s)):
        v = s.iloc[i]
        w = s.iloc[max(0, i - win + 1):i + 1].dropna()
        out.append(np.nan if (not np.isfinite(v) or len(w) < 20)
                   else float((w <= v).mean() * 100.0))
    return pd.Series(out, index=s.index)


def _f(x):
    """NaN/None 安全转 float（写 JSON 前用，避免出现 NaN）。"""
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    return None if (np.isnan(v) or np.isinf(v)) else v


def eigen_modes(win_ret, codes, topn=5):
    """当日相关矩阵前 3 个主成分的特征向量载荷（对应行业 ETF），用于把特征值映射到行业。

    same_sign=True 表示该主成分全部同向（市场模式，代表大盘整体方向）；
    否则为对比模式（行业间分化，正向组 vs 反向组）。
    """
    C = win_ret.corr().values
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    C = (C + C.T) / 2
    vals, vecs = np.linalg.eigh(C)
    order = np.argsort(vals)[::-1]
    modes = []
    for k in range(3):
        if k >= len(order):
            break
        idx = order[k]
        v = vecs[:, idx]
        if v.sum() < 0:                      # 符号锚定：让主成分整体指向正方向，跨日稳定
            v = -v
        vs = v.tolist()
        pairs = sorted(zip(codes, vs), key=lambda x: -abs(x[1]))[:topn]
        pos = sorted([(c, w) for c, w in zip(codes, vs) if w > 0], key=lambda x: -x[1])[:3]
        neg = sorted([(c, w) for c, w in zip(codes, vs) if w < 0], key=lambda x: x[1])[:3]
        dom = max(sum(1 for w in vs if w > 0), sum(1 for w in vs if w < 0)) / len(vs)
        modes.append({
            'rank': k + 1,
            'lambda': float(max(vals[idx], 1e-12)),
            'market_mode': bool(dom >= 0.8),       # 全体同向 → 市场模式（代表大盘方向）
            'dom_ratio': float(dom),
            'top': [{'code': c, 'name': ETF_NAMES.get(c, c), 'loading': float(w)}
                    for c, w in pairs],
            'pos': [{'code': c, 'name': ETF_NAMES.get(c, c), 'loading': float(w)}
                    for c, w in pos],
            'neg': [{'code': c, 'name': ETF_NAMES.get(c, c), 'loading': float(w)}
                    for c, w in neg],
        })
    return modes


def fetch_index_series(dates):
    """全A指数（中证全指）日线，按 ETF 池的交易日在位对齐；缺失记 NaN（不 ffill）。"""
    bars = fetch_kline(INDEX_CODE, days=1000)
    last_d = dates[-1].strftime('%Y-%m-%d')
    if last_d not in bars:
        rv, _ = fetch_realtime_quote(INDEX_CODE)
        if rv is not None:
            bars[last_d] = (rv, np.nan)
            print(f'  实时兜底 {INDEX_NAME} {last_d}: {rv}')
    s = pd.Series({d: v[0] for d, v in bars.items()})
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    out = pd.Series([s.get(pd.Timestamp(d), np.nan) for d in dates],
                    index=[pd.Timestamp(d) for d in dates])
    miss = int(out.isna().sum())
    print(f'  {INDEX_NAME}: {len(bars)} bars, 对齐缺失 {miss}/{len(dates)}')
    if miss:
        print(f'  [WARN] 全A 有 {miss} 个交易日无数据（按 NaN 处理）')
    if pd.isna(out.iloc[-1]):
        print(f'[DATA-GATE] 全A 最新交易日({last_d})无数据，拒绝继续')
        sys.exit(3)
    return out


def main():
    t0 = time.time()
    df_close, df_vol = load_prices()
    codes = [c for c in df_close.columns if c != 'date']
    N = len(codes)

    rets = df_close[codes] / df_close[codes].shift(1) - 1
    dates = list(df_close['date'])

    # 全A指数（叠加对照，不进相关矩阵）
    idx = fetch_index_series(dates)

    rows = []
    for i in range(WINDOW, len(df_close)):
        win = rets.iloc[i - WINDOW + 1:i + 1]
        l1, l2, l3, s, ipr, ent, egap, gap1 = eigen_metrics(win)
        rows.append({
            'date': dates[i].strftime('%Y-%m-%d'),
            'lambda1': l1, 'lambda2': l2, 'lambda3': l3, 'sum_lambda': s,
            'C': l1 / s * 100.0, 'Gap': l1 / l2, 'IPR': ipr, 'n_assets': N,
            'entropy': ent, 'entropy_gap': egap, 'gap1': gap1,
        })

    hist = pd.DataFrame(rows)
    hist['d_lambda1'] = hist['lambda1'].diff()
    hist['q250'] = [float((hist['lambda1'].iloc[max(0, i - QWIN + 1):i + 1]
                           <= hist['lambda1'].iloc[i]).mean() * 100.0)
                    for i in range(len(hist))]
    # 三项展示指标的逐日序列（只用于展示与说明，不参与预警判定）
    hist['d_gap1_5'] = hist['gap1'] - hist['gap1'].shift(5)      # Δ收窄：gap₁ 的 5 交易日变化
    hist['q_egap'] = roll_pct_series(hist['entropy_gap'], QWIN)  # 复杂度缺口的 250 日滚动分位
    hist['q_dgap'] = roll_pct_series(hist['d_gap1_5'], QWIN)     # Δ收窄的 250 日滚动分位
    # 各自“临界水平”的逐日序列：近 250 个交易日的第 90 百分位（随窗口滚动），供图上画临界线
    _mp = max(30, QWIN // 4)
    hist['crit_eg'] = hist['entropy_gap'].rolling(QWIN, min_periods=_mp).quantile(0.9)
    hist['crit_dg'] = hist['d_gap1_5'].rolling(QWIN, min_periods=_mp).quantile(0.9)
    idx_map = {d.strftime('%Y-%m-%d'): float(v) for d, v in idx.items()}
    hist['index_close'] = [idx_map.get(d, np.nan) for d in hist['date']]

    # 成交量变化率：池内合计成交量 今日 / 前 20 日均值
    vol_tot = df_vol[codes].sum(axis=1, skipna=True)
    vol_avg20 = vol_tot.rolling(VOLWIN).mean().shift(1)
    vol_ratio = vol_tot / vol_avg20

    last = hist.iloc[-1].to_dict()
    # 当日前 3 个主成分的特征向量载荷（特征值 → 行业映射）
    last['eigen_modes'] = eigen_modes(rets.iloc[-WINDOW:], codes)
    # 机会侧：只做同一批行情上的多维排序，不改动 C(t)/Gap 与六条阈值口径
    last['opportunity'] = compute_opportunity(df_close, df_vol, idx, ETF_NAMES)

    # 机会侧行业下钻：仅对「机会」档且能映射到宽泛方向的方向执行
    # （宽泛方向 → 申万一级 → 二级 → 三级 → 该三级成分股；不改动既有监测与机会侧口径）
    last['drilldown'] = None
    try:
        _seeds = []
        for _r in (last.get('opportunity') or {}).get('rows', []):
            if _r.get('grade') == '机会':
                _d = ETF_DIRECTION.get(_r['code'])
                if _d and _d not in _seeds:
                    _seeds.append(_d)
        if _seeds:
            _dd = run_drilldown(_seeds)
            last['drilldown'] = _dd
            _csv = os.path.join(WORK, f"cgap_drilldown_{last['date'].replace('-', '')}.csv")
            pd.DataFrame(to_csv_rows(_dd)).to_csv(_csv, index=False, encoding='utf-8-sig')
            print(f'  [下钻] 方向 {"、".join(_seeds)} → {os.path.basename(_csv)}')
            for _b in _dd['directions']:
                print('  [下钻] ' + ' → '.join(f"{p['level']}:{p['name']}" for p in _b['path']))
                if _b.get('stocks'):
                    _top = '、'.join(f"{x['name']}({x['code']})"
                                    for x in _b['stocks']['selected'][:5])
                    print(f'  [下钻] 入选个股 Top5：{_top}')
            for _e in _dd.get('errors', []):
                print(f'  [下钻][缺失] {_e}')
        else:
            print('  [下钻] 机会档无行业方向（宽基/红利不参与），跳过')
    except Exception as _e:
        last['drilldown'] = {'error': f'{type(_e).__name__}: {_e}'}
        print(f'  [下钻] 执行失败：{type(_e).__name__}: {_e}')
    # 量能铁律：当日合计成交量若只覆盖少数标的（K线接口盘后分摊更新，多数标的走实时兜底、量缺失），
    # 则该比值失真（会把"只剩个别标的"误读成"量能骤降"）。覆盖率不足 80% 一律判为不可用。
    vol_cov = int(df_vol[codes].iloc[-1].notna().sum())
    n_codes = len(codes)
    vt = vol_tot.iloc[-1]
    last['vol_coverage'] = f'{vol_cov}/{n_codes}'
    if pd.isna(vt) or vt <= 0 or vol_cov < 0.8 * n_codes:
        last['vol_ratio'] = None
        last['vol_today'] = None
        last['vol_avg20'] = float(vol_avg20.iloc[-1]) if pd.notna(vol_avg20.iloc[-1]) else None
        print(f'  [量能] 当日成交量覆盖 {vol_cov}/{n_codes} 只，不足 80% → 成交量变化率判为不可用')
    else:
        last['vol_ratio'] = float(vol_ratio.iloc[-1]) if vol_ratio.notna().iloc[-1] else None
        last['vol_today'] = float(vt)
        last['vol_avg20'] = float(vol_avg20.iloc[-1]) if pd.notna(vol_avg20.iloc[-1]) else None
    last['data_date'] = last['date']
    last['n_assets'] = int(N)
    last['assets'] = codes
    last['window'] = WINDOW

    # ---- 三项展示指标（只作展示与说明，不参与任何预警判定；判定口径未改）----
    _eg = hist['entropy_gap']
    _dg = hist['d_gap1_5']
    _w_eg = _eg.iloc[-QWIN:].dropna()
    _w_dg = _dg.iloc[-QWIN:].dropna()
    _crit_eg = _f(_w_eg.quantile(0.9)) if len(_w_eg) else None
    _crit_dg = _f(_w_dg.quantile(0.9)) if len(_w_dg) else None

    def _show_item(key, name, kind, formula, value, percentile, crit_level, crit_note, unit=''):
        pc = _f(percentile)
        gap = None if pc is None else round(pc - 90.0, 1)
        if pc is None:
            stance = '分位不可用'
        elif kind == '风险预警':
            stance = '已过临界 · 偏空' if pc >= 90 else '未到临界 · 风险侧未确认'
        else:
            stance = '已过临界 · 偏多' if pc >= 90 else '未到临界 · 机会侧待确认'
        return {'key': key, 'name': name, 'kind': kind, 'formula': formula,
                'value': _f(value), 'unit': unit, 'percentile': pc,
                'crit': 90.0, 'crit_level': _f(crit_level), 'crit_note': crit_note,
                'gap_to_crit': gap, 'stance': stance}

    last['show_indicators'] = {
        'note': ('以下三项只作展示与说明，不参与预警判定；临界＝该指标近 %d 个交易日的第 90 百分位'
                 '（λ₁ 分位本身即百分位，临界固定 90%%）' % QWIN),
        'window_days': QWIN,
        'items': [
            _show_item('l1_q250', 'λ₁ 250 日滚动分位', '风险预警',
                       '当日 λ₁ 在过去 250 个交易日 λ₁ 中的百分位（λ₁＝60 日相关矩阵最大特征值）',
                       last['q250'], hist['q250'].iloc[-1], 90.0,
                       '90% 即预警线：历史 7.4% 的交易日触发，触发后 20 日全A 平均 −3.84%（比基准低 4.29pp）',
                       unit='%'),
            _show_item('complexity_gap', '复杂度缺口', '风险预警',
                       'ln(18) − 熵，熵 = −Σp ln p（p = λ/Σλ）；缺口越大＝谱越集中＝分散化失效',
                       _eg.iloc[-1], hist['q_egap'].iloc[-1], _crit_eg,
                       '临界＝近 250 日第 90 百分位水平；分位 >90% 触发后 20 日全A 平均 −3.61%（低 4.06pp）'),
            _show_item('gap_narrow', 'Δ收窄（两个特征值之差的变化）', '监控/机会',
                       'gap₁ = (λ₁ − λ₂)/λ₁（两个最大特征值之差，相对 λ₁）；Δ收窄＝gap₁ 的 5 交易日变化',
                       _dg.iloc[-1], hist['q_dgap'].iloc[-1], _crit_dg,
                       '临界＝近 250 日第 90 百分位水平；分位 >90% 触发后 20 日全A 平均 +3.09%（比基准高 2.65pp）'),
        ],
    }
    for _it in last['show_indicators']['items']:
        print(f'  [展示] {_it["name"]}（{_it["kind"]}）：现值 {_it["value"]}，250日分位 {_it["percentile"]}，'
              f'临界 90%，{_it["stance"]}｜近250日第90百分位水平 {_it["crit_level"]}')

    # ---- 合并：RMT 预测监测块（口径沿用原 rmt_daily_monitor，直接复用它自己的函数）----
    last['rmt_prediction'] = None
    try:
        _rp = compute_rmt(rets)
        _alert = evaluate_alert(_rp)
        # 与本院线的全谱指标交叉核对（差值应≈0；不一致时如实标注，不静默覆盖）
        _cc = {'gap_1': _rp['gap_1'] - float(hist['gap1'].iloc[-1]),
               'entropy': _rp['entropy'] - float(hist['entropy'].iloc[-1]),
               'delta_s': _rp['delta_s'] - float(hist['entropy'].iloc[-1] - hist['entropy'].iloc[-6]),
               'lambda1': _rp['eigs_top5'][0] - float(hist['lambda1'].iloc[-1])}
        _picks, _pick_err = {}, {}
        for _c in _rp['top5'].keys():
            try:
                _sp = get_top_stocks_for_etf(_c, top_n=3)
                if _sp:
                    _picks[_c] = _sp
                else:
                    _pick_err[_c] = '无数据'
            except Exception as _e2:
                _pick_err[_c] = f'{type(_e2).__name__}: {_e2}'
        last['rmt_prediction'] = {
            'indicators': {k: _f(_rp[k]) for k in ('gap_1', 'entropy', 'delta_s', 'snr', 'lambda_plus')},
            'eigs_top5': [_f(v) for v in _rp['eigs_top5']],
            'status': _rp['status'], 'position': _f(_rp['position']), 'action': _rp['action'],
            'template': _rp['template'],
            'alert': {'level': _alert['level'], 'reasons': _alert['reasons'], 'watch': _alert['watch']},
            'thresholds': {'red_dS': ALERT_RED_DELTA_S, 'warn_dS': ALERT_WARN_DELTA_S,
                           'green_entropy': ALERT_GREEN_ENTROPY, 'green_dS': ALERT_GREEN_DELTA_S},
            'top5': [{'code': _c, 'name': ETF_NAMES.get(_c, _c), 'mom20': _f(_v),
                      'picks': _picks.get(_c, [])} for _c, _v in _rp['top5'].items()],
            'bottom5': [{'code': _c, 'name': ETF_NAMES.get(_c, _c), 'mom20': _f(_v)}
                        for _c, _v in _rp['bottom5'].items()],
            'pick_errors': _pick_err,
            'cross_check': {k: _f(v) for k, v in _cc.items()},
            'source': ('rmt_daily_monitor.compute_rmt / evaluate_alert（红黄绿灯阈值原样）'
                       ' + stock_picker.get_top_stocks_for_etf（Top5 动量行业各前 3 只个股）'),
        }
        print(f"  [预测] 灯={_alert['level']}　status={_rp['status']}　action={_rp['action']}　"
              f"gap1={_rp['gap_1']:.4f}　ΔS={_rp['delta_s']:+.4f}　熵={_rp['entropy']:.4f}　SNR={_rp['snr']:.2f}")
        print(f"  [预测] Top5 动量行业：{'、'.join(ETF_NAMES.get(c, c) for c in _rp['top5'])}；"
              f"个股精选 {sum(len(v) for v in _picks.values())} 只"
              + (f"；未取到：{'、'.join(_pick_err)}" if _pick_err else ''))
        print('  [预测] 与本管线交叉核对差值：' + '、'.join(f'{k} {v:+.2e}' for k, v in _cc.items()))
    except Exception as _e:
        last['rmt_prediction'] = {'error': f'{type(_e).__name__}: {_e}'}
        print(f'  [预测] 合并块执行失败：{type(_e).__name__}: {_e}')

    # 全A指数当日值 / 当日涨跌 / 20日涨跌
    ic = hist['index_close']
    last['index_name'] = INDEX_NAME
    last['index_code'] = INDEX_CODE
    last['index_close'] = float(ic.iloc[-1]) if pd.notna(ic.iloc[-1]) else None
    last['index_chg_1'] = float(ic.iloc[-1] / ic.iloc[-2] - 1) if len(ic) > 1 and pd.notna(ic.iloc[-2]) else None
    last['index_mom_20'] = float(ic.iloc[-1] / ic.iloc[-21] - 1) if len(ic) > 20 and pd.notna(ic.iloc[-21]) else None

    hist.to_json(HIST_PATH, orient='records', indent=2, force_ascii=False)
    with open(TODAY_PATH, 'w', encoding='utf-8') as f:
        json.dump(last, f, indent=2, ensure_ascii=False, default=str)

    print(f'\n最新交易日 {last["date"]}  标的数 {N}  窗口 {WINDOW} 日')
    print(f'  λ1={last["lambda1"]:.4f}  λ2={last["lambda2"]:.4f}  λ3={last["lambda3"]:.4f}  Σλ={last["sum_lambda"]:.2f}')
    print(f'  C(t)={last["C"]:.2f}%  Gap={last["Gap"]:.3f}')
    print(f'  Δλ1={last["d_lambda1"]:+.4f}  IPR={last["IPR"]:.4f}  λ1 250日分位={last["q250"]:.1f}%')
    vr = last['vol_ratio']
    print(f'  成交量变化率={("n/a" if vr is None else f"{vr:.3f}")}  历史序列 {len(hist)} 天 -> {HIST_PATH}')
    print(f'  {INDEX_NAME}: 收盘 {last["index_close"]:.2f}  '
          f'当日 {last["index_chg_1"]*100:+.2f}%  20日 {last["index_mom_20"]*100:+.2f}%')
    opp = last.get('opportunity')
    if opp:
        t5 = ' | '.join(f"{x['name']}{x['score']:.2f}({x['grade']})" for x in opp['top'])
        print(f'  [机会侧] Top5: {t5}')
        print(f'  [机会侧] 机会名单: {"、".join(opp["strong"]) if opp["strong"] else "无"}'
              f'  弱势: {"、".join(opp["weak"]) if opp["weak"] else "无"}')
    else:
        print('  [机会侧] 数据不足，未生成')
    print(f'Today json -> {TODAY_PATH}  ({time.time() - t0:.1f}s)')


if __name__ == '__main__':
    main()