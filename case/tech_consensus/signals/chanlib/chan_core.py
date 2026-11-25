#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论分型版 · 核心算法模块（自包含）
================================================================
从已通过单笔复核的回测实现中逐字提取，保证与
chan_multi_level.py / chan_multi_signal.py / chan_ma_vs_struct.py 一致。
依赖仅 numpy / pandas；不依赖 vmdpy、不依赖任何绝对路径。
对外主入口见 chan_fractal.py（analyze() / CLI）。
"""
import numpy as np
import pandas as pd

COST_PER_SIDE = 0.001   # 单边 0.1%（佣金 0.05% + 滑点 0.05%）
MIN_BI_GAP = 4   # 处理后K线索引差下限（含分型本身，即"笔至少 5 根K线"）
MIN_SEG_BI = 3   # 线段至少由 3 笔构成（端点索引差下限）


def merge_inclusion(klines):
    """klines: list of dict(high, low, raw_idx) 按时间升序。
    返回 merged: list of dict(high, low, start_idx, end_idx) 保持高(de)低点信息。"""
    merged = []
    for k in klines:
        if not merged:
            merged.append({'high': k['high'], 'low': k['low'],
                           'start_idx': k['raw_idx'], 'end_idx': k['raw_idx']})
            continue
        last = merged[-1]
        # 包含判定
        if (k['high'] >= last['high'] and k['low'] <= last['low']) or \
           (k['high'] <= last['high'] and k['low'] >= last['low']):
            # 方向：参考 merged 最近两根
            if len(merged) >= 2:
                prev = merged[-2]
                upward = last['high'] >= prev['high']
            else:
                upward = True
            if upward:
                new_h = max(k['high'], last['high'])
                new_l = max(k['low'], last['low'])
            else:
                new_h = min(k['high'], last['high'])
                new_l = min(k['low'], last['low'])
            merged[-1] = {'high': new_h, 'low': new_l,
                          'start_idx': last['start_idx'], 'end_idx': k['raw_idx']}
        else:
            merged.append({'high': k['high'], 'low': k['low'],
                           'start_idx': k['raw_idx'], 'end_idx': k['raw_idx']})
    return merged


# ================= 顶底分型 =================


def detect_pivots(merged):
    """严格顶/底分型：中间K线 high 与 low 同时最高/最低。
    返回 [(idx, type, price)] type: 'top'/'bottom'"""
    pivots = []
    for i in range(1, len(merged) - 1):
        m, l, r = merged[i], merged[i - 1], merged[i + 1]
        if m['high'] > l['high'] and m['high'] > r['high'] and \
           m['low'] > l['low'] and m['low'] > r['low']:
            pivots.append((i, 'top', m['high']))
        elif m['low'] < l['low'] and m['low'] < r['low'] and \
             m['high'] < l['high'] and m['high'] < r['high']:
            pivots.append((i, 'bottom', m['low']))
    return pivots


# ================= 笔 =================
MIN_BI_GAP = 4   # 处理后K线索引差下限（含分型本身，即"笔至少 5 根K线"）



def normalize_ends(ends):
    """保证 ends 相邻端点：异向交替 + 间隔>=MIN_BI_GAP。
    同向相邻 -> 保留更极端者；间隔不足 -> 更极端者上移，否则删除。"""
    while len(ends) >= 2:
        a, b = ends[-2], ends[-1]
        if a[1] == b[1]:
            if (a[1] == 'top' and b[2] > a[2]) or (a[1] == 'bottom' and b[2] < a[2]):
                ends[-2] = b
            del ends[-1]
        elif abs(a[0] - b[0]) < MIN_BI_GAP:
            if (b[1] == 'top' and b[2] > a[2]) or (b[1] == 'bottom' and b[2] < a[2]):
                ends[-2] = b
            del ends[-1]
        else:
            break
    return ends




def build_bis(merged):
    """由分型序列构建笔端点（统一 normalize 保证交替与间隔）。
    返回 ends: [(idx, type, price)]"""
    pivots = detect_pivots(merged)
    ends = []
    for p in pivots:
        if not ends:
            ends.append(p)
            continue
        last = ends[-1]
        if p[1] == last[1]:
            # 同向：只保留更极端者
            if (p[1] == 'top' and p[2] > last[2]) or (p[1] == 'bottom' and p[2] < last[2]):
                ends[-1] = p
                ends = normalize_ends(ends)
        else:
            ends.append(p)
            ends = normalize_ends(ends)
    return normalize_ends(ends)


# ================= 15分钟笔（从 DataFrame） =================


def bis_from_df(df, name='15min'):
    """df 需含 open/high/low/close，按时间升序。返回 (ends, merged, pivots)"""
    df = df.reset_index(drop=True)
    klines = [{'high': float(df.loc[i, 'high']), 'low': float(df.loc[i, 'low']),
               'raw_idx': i} for i in range(len(df))]
    merged = merge_inclusion(klines)
    pivots = detect_pivots(merged)
    ends = build_bis(merged)
    return ends, merged, pivots




def ends_to_df(ends, merged, df, raw_col):
    """把笔/线段端点转为 DataFrame：端点索引(处理后)、原始K线索引、时间戳、价格、类型。"""
    rows = []
    for i, tp, px in ends:
        raw_i = merged[i]['end_idx']
        rows.append({'seg_idx': i, 'raw_idx': raw_i, 'time': df[raw_col].iloc[raw_i],
                     'price': px, 'type': tp})
    return pd.DataFrame(rows)


# ================= 线段（60分钟级别，由15分钟笔递归） =================
MIN_SEG_BI = 3   # 线段至少由 3 笔构成（端点索引差下限）



def build_segments(bi_ends, min_seg=MIN_SEG_BI):
    """由 15 分钟笔端点序列递归构造线段端点（"笔的笔"结构）。
    规则：相邻线段端点异向；异向端点与前一线段端点间隔>=min_seg 个笔端点
    才成为新线段端点（线性段至少由 min_seg 笔构成）；同向端点只保留更极端者
    （回溯合并相邻同向端点）。"""
    seg_ends = []          # [(pos, idx, type, price)] pos=笔端点序号
    for pos, e in enumerate(bi_ends):
        cur = (pos, e[0], e[1], e[2])
        if not seg_ends:
            seg_ends.append(cur)
            continue
        last = seg_ends[-1]
        if e[1] == last[2]:
            # 同向：更极端则替换，并回溯合并相邻同向端点（保留更极端）
            if (e[1] == 'top' and e[2] > last[3]) or (e[1] == 'bottom' and e[2] < last[3]):
                seg_ends[-1] = cur
                while len(seg_ends) >= 2 and seg_ends[-1][2] == seg_ends[-2][2]:
                    a, b = seg_ends[-2], seg_ends[-1]
                    if (a[2] == 'top' and b[3] > a[3]) or (a[2] == 'bottom' and b[3] < a[3]):
                        seg_ends[-2] = b
                    del seg_ends[-1]
        else:
            # 异向：间隔达到 min_seg 笔才成为线段端点，否则视为线段内波动忽略
            if pos - last[0] >= min_seg:
                seg_ends.append(cur)
    return [(e[1], e[2], e[3]) for e in seg_ends]




def macd_on(df, fast=12, slow=26, sig=9):
    """在指定级别K线上计算 MACD（柱=2*(DIF-DEA)）。df 需有 close 且按时间升序。"""
    df = df.reset_index(drop=True)
    ema_f = df['close'].ewm(span=fast, adjust=False).mean()
    ema_s = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=sig, adjust=False).mean()
    hist = 2 * (dif - dea)
    return pd.DataFrame({'dif': dif, 'dea': dea, 'hist': hist})




def detect_backshift(ends, macd_hist, px_series, min_seg):
    """在同向端点序列上检测背驰买卖点。
    ends: [(k_idx, type, price)] 交替（笔/线段/走势类型端点），k_idx 为对应级别K线行号。
    规则：相邻两段同向走势，若第 2 段创价格新低/新高 且 MACD 柱面积绝对值
    比第 1 段弱 10% 以上 -> 底背驰(+1 买入) / 顶背驰(-1 卖出)。"""
    events = []
    n = len(ends)
    for i in range(2, n):
        if ends[i][1] != ends[i - 2][1]:
            continue
        r1a, r1b = ends[i - 2][0], ends[i - 1][0]
        r2a, r2b = ends[i - 1][0], ends[i][0]
        if r1b <= r1a or r2b <= r2a:
            continue
        seg1 = macd_hist[r1a:r1b + 1]
        seg2 = macd_hist[r2a:r2b + 1]
        if len(seg1) < 2 or len(seg2) < 2:
            continue
        a1, a2 = abs(float(np.sum(seg1))), abs(float(np.sum(seg2)))
        if a1 <= 1e-12:
            continue
        if ends[i][1] == 'bottom':
            low1 = float(np.min(px_series[r1a:r1b + 1]))
            low2 = float(np.min(px_series[r2a:r2b + 1]))
            if low2 <= low1 and a2 < a1 * 0.9:
                events.append({'k_idx': r2b, 'type': 'bottom', 'price': ends[i][2],
                               'signal': 1, 'kind': '底背驰(一买)'})
        else:
            high1 = float(np.max(px_series[r1a:r1b + 1]))
            high2 = float(np.max(px_series[r2a:r2b + 1]))
            if high2 >= high1 and a2 < a1 * 0.9:
                events.append({'k_idx': r2b, 'type': 'top', 'price': ends[i][2],
                               'signal': -1, 'kind': '顶背驰(一卖)'})
    return events




def run_chan_bt(close, time_idx, buy_rows, sell_rows, env_bull, label):
    """事件驱动回测：买/卖信号在第 i 行收盘确认，成交于第 i+1 根收盘；
    环境转空（日线方向过滤）当行确认、下一根强制平仓。
    持仓区间 [entry+1, exit_k] 逐根累计 close 收益，每笔交易在区间末根扣双边成本 2*COST_PER_SIDE。
    单笔收益 ret 与逐根序列 daily 严格一致。"""
    n = len(close)
    buy_set = set(buy_rows)
    sell_set = set(sell_rows)
    daily = np.zeros(n)
    trades = []
    pos = 0
    entry = None          # 买入信号行（成交于 entry+1 收盘）
    prev_bull = True
    for i in range(n):
        env_now = env_bull[i] if env_bull is not None else True
        force_exit = (env_bull is not None) and prev_bull and (not env_now)
        prev_bull = env_now

        if pos == 0 and i in buy_set and env_now:
            pos, entry = 1, i
        elif pos == 1 and (i in sell_set or force_exit):
            entry_k = entry + 1
            exit_k = min(i + 1, n - 1)
            seg_ret = 0.0
            if exit_k > entry_k:
                seg_prod = 1.0
                for t in range(entry_k, exit_k):
                    r = close[t + 1] / close[t] - 1
                    daily[t] += r
                    seg_prod *= (1 + r)
                daily[exit_k - 1] += -2 * COST_PER_SIDE
                seg_ret = (seg_prod - 1) - 2 * COST_PER_SIDE   # 复利连乘口径
            reason = '信号平仓' if i in sell_set else '环境转空'
            trades.append({'entry_time': str(time_idx[entry]), 'exit_time': str(time_idx[exit_k - 1]),
                           'entry_px': round(float(close[entry_k]), 3), 'exit_px': round(float(close[exit_k]), 3),
                           'ret': round(seg_ret, 6), 'reason': reason})
            pos, entry = 0, None
    if pos == 1 and entry is not None:
        entry_k = entry + 1
        exit_k = n - 1
        seg_ret = 0.0
        if exit_k > entry_k:
            seg_prod = 1.0
            for t in range(entry_k, exit_k):
                r = close[t + 1] / close[t] - 1
                daily[t] += r
                seg_prod *= (1 + r)
            daily[exit_k - 1] += -2 * COST_PER_SIDE
            seg_ret = (seg_prod - 1) - 2 * COST_PER_SIDE
        trades.append({'entry_time': str(time_idx[entry]), 'exit_time': str(time_idx[exit_k]),
                       'entry_px': round(float(close[entry_k]), 3), 'exit_px': round(float(close[exit_k]), 3),
                       'ret': round(seg_ret, 6), 'reason': '期末未平'})
    return daily, trades




def bt_metrics(daily, close, trades, label, freq_per_year=16 * 252):
    eq = np.cumprod(1 + daily)
    total = eq[-1] - 1
    ann = eq[-1] ** (freq_per_year / len(daily)) - 1 if len(daily) else 0
    dd = (eq / np.maximum.accumulate(eq) - 1).min() if len(eq) else 0
    sd = np.std(daily)
    sharpe = np.mean(daily) / (sd + 1e-12) * np.sqrt(freq_per_year)
    n_t = len(trades)
    rets = [t['ret'] for t in trades]
    win = sum(1 for r in rets if r > 0) / n_t if n_t else 0
    avg_win = np.mean([r for r in rets if r > 0]) if any(r > 0 for r in rets) else 0
    avg_loss = np.mean([r for r in rets if r <= 0]) if any(r <= 0 for r in rets) else 0
    bh = close[-1] / close[0] - 1
    return {'级别': label, '总收益': f'{total*100:.2f}%', '年化': f'{ann*100:.2f}%',
            '最大回撤': f'{dd*100:.2f}%', '夏普': f'{sharpe:.2f}',
            '买入持有': f'{bh*100:.2f}%', '交易次数': n_t, '胜率': f'{win*100:.1f}%',
            '平均盈利': f'{avg_win*100:.2f}%', '平均亏损': f'{avg_loss*100:.2f}%',
            '盈亏比': f'{abs(avg_win/avg_loss):.2f}' if avg_loss != 0 else 'N/A'}




def detect_zhongshu(ends, min_seg=3):
    """连续 min_seg 段（min_seg+1 个端点）重叠区间为中枢；相邻且区间重叠则延长。
    ends: [(row, type, price)] 交替端点。返回 [(s_ep, e_ep, zd, zg)]（端点序号区间）。"""
    n = len(ends)
    zs = []
    for i in range(0, n - min_seg):
        segs = []
        for k in range(min_seg):
            a, b = ends[i + k], ends[i + k + 1]
            segs.append((max(a[2], b[2]), min(a[2], b[2])))
        ZD = max(s[1] for s in segs)
        ZG = min(s[0] for s in segs)
        if ZG > ZD:
            if zs and i <= zs[-1][1] and zs[-1][2] < ZG and zs[-1][3] > ZD:
                zs[-1] = (zs[-1][0], i + min_seg, zs[-1][2], zs[-1][3])
                continue
            zs.append((i, i + min_seg, ZD, ZG))
    return zs


# ================= 一买/二买/三买 =================


def detect_buy_sell(ends, macd_hist, px_series, min_seg, kind_tag=''):
    """在端点序列上检测六个买/卖点。
    ends: [(row, type, price)] 交替端点，row 为回测轴行号（raw 行号）。
    返回 events: [{row, sig(+1买/-1卖), kind}]，row 直接可用作回测信号行。"""
    events = []
    # ---- 一买/一卖：背驰 ----
    bs = detect_backshift(ends, macd_hist, px_series, min_seg)
    b1_rows = set()
    s1_rows = set()
    for e in bs:
        k = 'B1' if e['signal'] == 1 else 'S1'
        events.append({'row': e['k_idx'], 'sig': e['signal'], 'kind': k})
        (b1_rows if e['signal'] == 1 else s1_rows).add(e['k_idx'])

    # ---- 二买：B1 之后反弹、回调不破 B1 低点 ----
    last_b1 = None
    for idx, (row, tp, px_) in enumerate(ends):
        if row in b1_rows:
            last_b1 = idx
        elif tp == 'bottom' and last_b1 is not None and row not in b1_rows:
            if any(ends[k][1] == 'top' for k in range(last_b1 + 1, idx)):
                if px_ > ends[last_b1][2]:
                    events.append({'row': row, 'sig': 1, 'kind': 'B2'})
                    last_b1 = None
    # ---- 二卖：S1 之后回落、反弹不创新高 ----
    last_s1 = None
    for idx, (row, tp, px_) in enumerate(ends):
        if row in s1_rows:
            last_s1 = idx
        elif tp == 'top' and last_s1 is not None and row not in s1_rows:
            if any(ends[k][1] == 'bottom' for k in range(last_s1 + 1, idx)):
                if px_ < ends[last_s1][2]:
                    events.append({'row': row, 'sig': -1, 'kind': 'S2'})
                    last_s1 = None

    # ---- 三买：中枢向上离开后回抽不回中枢（回抽底 > ZG） ----
    for s, e, zd, zg in detect_zhongshu(ends, min_seg):
        left = False
        for idx in range(e, len(ends)):
            row, tp, px_ = ends[idx]
            if tp == 'top':
                if idx > 0 and max(px_, ends[idx - 1][2]) > zg:
                    left = True
            elif left and tp == 'bottom':
                if px_ > zg:
                    events.append({'row': row, 'sig': 1, 'kind': 'B3'})
                left = False
    # ---- 三卖：中枢向下离开后回抽不回中枢（回抽顶 < ZD） ----
    for s, e, zd, zg in detect_zhongshu(ends, min_seg):
        left = False
        for idx in range(e, len(ends)):
            row, tp, px_ = ends[idx]
            if tp == 'bottom':
                if idx > 0 and min(px_, ends[idx - 1][2]) < zd:
                    left = True
            elif left and tp == 'top':
                if px_ < zd:
                    events.append({'row': row, 'sig': -1, 'kind': 'S3'})
                left = False
    if kind_tag:
        for e in events:
            e['kind'] += kind_tag
    return events


# ================= 多级别并行信号池 =================


def ma_bi_ends(df, fast=5, slow=40):
    """MA_fast 上穿 MA_slow = 金叉（底端点），下穿 = 死叉（顶端点）。
    返回交替端点 [(row, type, price)]，price 为交叉当根收盘价。"""
    close = df['close']
    ma_f = close.rolling(fast).mean()
    ma_s = close.rolling(slow).mean()
    # 逐根判断交叉（MA_fast 与 MA_slow 同列比较，跳过热身期）
    ends = []
    for i in range(1, len(df)):
        if np.isnan(ma_f[i]) or np.isnan(ma_s[i]) or np.isnan(ma_f[i - 1]) or np.isnan(ma_s[i - 1]):
            continue
        if ma_f.iloc[i - 1] <= ma_s.iloc[i - 1] and ma_f.iloc[i] > ma_s.iloc[i]:   # 金叉
            ends.append((i, 'bottom', float(close.iloc[i])))
        elif ma_f.iloc[i - 1] >= ma_s.iloc[i - 1] and ma_f.iloc[i] < ma_s.iloc[i]:  # 死叉
            ends.append((i, 'top', float(close.iloc[i])))
    return ends


