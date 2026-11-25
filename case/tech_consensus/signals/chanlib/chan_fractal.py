#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论分型版 · 识别入口（自包含）
================================================================
输入：任意标的标准 OHLCV 时间序列 ——
  方式一：CSV 文件（至少含 open/high/low/close 与时间列 time/date/datetime）
  方式二：pandas DataFrame（同字段）
输出：结构性交易机会 JSON ——
  笔端点 / 中枢结构 / 一二三买卖点（B1·S1 背驰、B2·S2、B3·S3）+ 可选回测统计。
定版 MACD(20,40,5)，柱面积背驰（弱 10% 判定）；通用沪深300/中证500/期货等标的，
通用日线/60分钟/15分钟等多级别（级别可由相邻K线时间间隔自动推断或显式指定）。

用法（命令行）：
  python3 chan_fractal.py --csv 510300_day.csv --level day --backtest --name 沪深300
  python3 chan_fractal.py --csv 60min.csv --level 60min
用法（库调用）：
  from chan_fractal import analyze
  result = analyze(df, level='auto', backtest=True, name='RB主力')
"""
import os, sys, json, argparse
import numpy as np
import pandas as pd

# 允许脚本在任何目录被调用时找到同目录 chan_core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chan_core as cc

MACD_FAST, MACD_SLOW, MACD_SIG = 20, 40, 5   # 定版参数
LEVEL_FREQ = {'day': 252, '60min': 4 * 252, '15min': 16 * 252, '30min': 8 * 252}
KIND_CN = {
    'B1': '一买(底背驰)', 'S1': '一卖(顶背驰)',
    'B2': '二买(回调不破前低)', 'S2': '二卖(反弹不创新高)',
    'B3': '三买(中枢突破回抽不回中枢)', 'S3': '三卖(中枢突破回抽不回中枢)',
}


# ================= 输入标准化 =================
def _norm_columns(df):
    """列名归一化：时间列 -> time；OHLC -> open/high/low/close；其余保留。"""
    df = df.copy()
    low_map = {c.lower(): c for c in df.columns}
    for std, cands in {'time': ['time', 'date', 'datetime', 'timestamp', 'day', '日期', '时间'],
                       'open': ['open', 'o', '开盘'],
                       'high': ['high', 'h', '最高'],
                       'low': ['low', 'l', '最低'],
                       'close': ['close', 'c', '收盘']}.items():
        for cand in cands:
            if cand in low_map and low_map[cand] in df.columns:
                df = df.rename(columns={low_map[cand]: std})
                break
        else:
            if std == 'time':
                # 时间列缺省：尝试把索引当作时间
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(columns={'index': 'time'})
                else:
                    raise ValueError('未找到时间列，请提供 time/date/datetime 列或 DatetimeIndex')
    return df


def _parse_time(col):
    if pd.api.types.is_datetime64_any_dtype(col):
        return col
    s = pd.Series(col).astype(str)
    # 兼容 '20260105103000000' 这类 14~17 位数字时间戳（%Y%m%d%H%M%S[%f]）
    if s.str.fullmatch(r'\d{14,17}').all():
        return pd.to_datetime(s.str[:14], format='%Y%m%d%H%M%S')
    return pd.to_datetime(col)


def normalize_input(data, name=None):
    """接受 CSV 路径或 DataFrame，返回 (df, meta)。"""
    if isinstance(data, str):
        df = pd.read_csv(data)
        src = os.path.basename(data)
    elif isinstance(data, pd.DataFrame):
        df = data
        src = name if name else 'DataFrame'
    else:
        raise TypeError('data 必须是 CSV 路径或 pandas DataFrame')
    df = _norm_columns(df)
    df['time'] = _parse_time(df['time'])
    df = df.sort_values('time').drop_duplicates('time', keep='last').reset_index(drop=True)
    for c in ['open', 'high', 'low', 'close']:
        df[c] = pd.to_numeric(df[c], errors='raise')
    return df, src


def infer_level(df):
    """由相邻K线时间间隔中位数推断级别。返回 (level_label, freq_per_year)。"""
    if len(df) < 2:
        return '自定义', 252
    d = df['time'].diff().dropna()
    med = float(d.median().total_seconds()) / 3600.0   # 小时
    if med >= 20:
        return '日线', LEVEL_FREQ['day']
    if abs(med - 1.0) < 0.15:
        return '60分钟', LEVEL_FREQ['60min']
    if abs(med - 0.5) < 0.1:
        return '30分钟', LEVEL_FREQ['30min']
    if abs(med - 0.25) < 0.05:
        return '15分钟', LEVEL_FREQ['15min']
    # 其他周期：按 240 分钟/交易日的市场惯例折算年频（仅为年化参考）
    freq = int(round(252 * 240.0 / max(med * 60.0, 1.0)))
    return f'{med:.0f}分钟', max(freq, 252)


def get_endpoints(df, base):
    """按信号基座生成端点序列（原始K线行号轴）。base: struct / ma5-40 / ma10-40"""
    if base == 'struct':
        ends, merged, _ = cc.bis_from_df(df)
        raw = [merged[e[0]]['end_idx'] for e in ends]
        return [(raw[i], ends[i][1], ends[i][2]) for i in range(len(ends))]
    fast, slow = {'ma5-40': (5, 40), 'ma10-40': (10, 40)}[base]
    return cc.ma_bi_ends(df, fast=fast, slow=slow)


def build_structure(df, ends):
    """输出笔端点与中枢结构（供查验）。"""
    bi_rows = []
    for row, tp, px in ends:
        bi_rows.append({'行号': int(row), '时间': str(df['time'].iloc[row]),
                        '类型': '顶' if tp == 'top' else '底', '价格': round(float(px), 4)})
    zs = []
    for s, e, zd, zg in cc.detect_zhongshu(ends, cc.MIN_BI_GAP):
        zs.append({'起始端点序号': int(s), '结束端点序号': int(e),
                   '下沿ZD': round(float(zd), 4), '上沿ZG': round(float(zg), 4),
                   '起止时间': f"{str(df['time'].iloc[ends[s][0]])} ~ {str(df['time'].iloc[ends[e][0]])}"})
    return {'笔端点': bi_rows, '中枢': zs}


def detect_signals(df, ends, macd_combo):
    """返回交易机会列表（含中文说明）。"""
    m = cc.macd_on(df, fast=macd_combo[0], slow=macd_combo[1], sig=macd_combo[2])
    ev = cc.detect_buy_sell(ends, m['hist'].values, df['close'].values, cc.MIN_BI_GAP)
    out = []
    for e in ev:
        row = int(e['row'])
        sig = int(e['sig'])
        out.append({'行号': row, '时间': str(df['time'].iloc[row]),
                    '价格': round(float(df['close'].iloc[row]), 4),
                    '方向': '买入' if sig == 1 else '卖出',
                    '类型': e['kind'], '说明': KIND_CN.get(e['kind'], e['kind'])})
    return out


def run_backtest(df, signals, freq):
    buy = sorted(s['行号'] for s in signals if s['方向'] == '买入')
    sell = sorted(s['行号'] for s in signals if s['方向'] == '卖出')
    daily, trades = cc.run_chan_bt(df['close'].values, df['time'].values, buy, sell, None, '')
    met = cc.bt_metrics(daily, df['close'].values, trades, '', freq_per_year=freq)
    trades_out = [{'入场时间': t['entry_time'], '出场时间': t['exit_time'],
                   '入场价': t['entry_px'], '出场价': t['exit_px'],
                   '收益': t['ret'], '平仓原因': t['reason']} for t in trades]
    return {'指标': met, '交易明细': trades_out}


def analyze(data, level='auto', base='struct', macd_combo=(20, 40, 5),
            backtest=False, name=None):
    """主入口。返回结构化结果 dict。"""
    df, src = normalize_input(data, name=name)
    if level in LEVEL_FREQ:
        level_label, freq = {'day': '日线', '60min': '60分钟', '15min': '15分钟',
                             '30min': '30分钟'}[level], LEVEL_FREQ[level]
    elif level == 'auto':
        level_label, freq = infer_level(df)
    else:
        raise ValueError(f'level 仅支持 {list(LEVEL_FREQ)} 或 auto')
    ends = get_endpoints(df, base)
    signals = detect_signals(df, ends, macd_combo)
    result = {
        '_meta': {
            '标的/来源': src, '级别': level_label, '数据根数': int(len(df)),
            '区间': f"{str(df['time'].iloc[0])} ~ {str(df['time'].iloc[-1])}",
            'MACD': f"{macd_combo[0]}-{macd_combo[1]}-{macd_combo[2]}",
            '信号基座': '结构版(分型笔)' if base == 'struct' else f'均线版{base}',
            '回测口径': '信号次根收盘成交, 只做多, 每笔双边成本0.2%',
        },
        '结构': build_structure(df, ends),
        '交易机会': signals,
    }
    if backtest:
        result['回测'] = run_backtest(df, signals, freq)
    return result


# ================= 命令行入口 =================
def main():
    ap = argparse.ArgumentParser(description='缠论分型版：时间序列 -> 结构性交易机会')
    ap.add_argument('--csv', required=True, help='OHLCV CSV 文件路径')
    ap.add_argument('--level', default='auto', choices=['auto', 'day', '60min', '30min', '15min'],
                    help='K线级别（默认 auto 自动推断）')
    ap.add_argument('--base', default='struct', choices=['struct', 'ma5-40', 'ma10-40'],
                    help='信号基座（默认结构版分型笔）')
    ap.add_argument('--macd', default='20,40,5', help='MACD 参数 fast,slow,sig（默认 20,40,5）')
    ap.add_argument('--backtest', action='store_true', help='同时输出回测统计与交易明细')
    ap.add_argument('--name', default=None, help='标的名（用于 _meta 展示）')
    ap.add_argument('--out', default=None, help='结果 JSON 输出路径（缺省打印到 stdout）')
    args = ap.parse_args()
    macd = tuple(int(x) for x in args.macd.split(','))
    result = analyze(args.csv, level=args.level, base=args.base, macd_combo=macd,
                     backtest=args.backtest, name=args.name)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'已写入 {args.out}')
    else:
        print(text)


if __name__ == '__main__':
    main()