#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASCII 图形工具：块字符折线 + 横向条形图（近 N 天流动性变化）

周报卡片的「近 10 天走势」用。纯文本，不依赖图片，飞书卡片 / 邮件都直接显示。

用法：
    from ascii_chart import spark, bars, chart
    spark([1,2,3])                     # ▁▄█
    bars([('10-16', 92.3), ...])       # 逐日横条
    chart('隔夜占比', rows, unit='%')   # 折线 + 横条 + 首尾变化
    chart('两融净买入', rows, zero_base=True)   # 带正负 → 中线左右
"""
from __future__ import annotations

_BLOCKS = ' ▁▂▃▄▅▆▇█'


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None   # 去 NaN


def _lab(d):
    s = str(d)
    return s[5:] if len(s) >= 10 and s[4] == '-' else s


def spark(series) -> str:
    """块字符折线（左旧右新）。缺值用空格。"""
    s = [_num(x) for x in series]
    vals = [v for v in s if v is not None]
    if len(vals) < 2:
        return ''
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return '▄' * len(s)
    out = []
    for v in s:
        if v is None:
            out.append(' ')
            continue
        idx = int(round((v - lo) / (hi - lo) * (len(_BLOCKS) - 1)))
        out.append(_BLOCKS[max(0, min(len(_BLOCKS) - 1, idx))])
    return ''.join(out)


def bars(rows, width=14, lo=None, hi=None, fmt='.1f', unit='', zero_base=False) -> list[str]:
    """横向条形图。rows = [(label, value), ...]，按传入顺序画。

    lo/hi 不给就用样本极值。value=None 画空。
    zero_base=True 时以 0 为基准（带正负的净买入/净流入）：
      正值向右 █、负值向左 ▒，中线对准 0。
    """
    vals = [_num(v) for _, v in rows]
    good = [v for v in vals if v is not None]
    m = 0.0
    if zero_base:
        m = max([abs(v) for v in good]) or 1.0
        lo, hi = -m, m
    else:
        if lo is None:
            lo = min(good) if good else 0.0
        if hi is None:
            hi = max(good) if good else 1.0
    span = (hi - lo) or 1.0
    out = []
    for (label, _), vv in zip(rows, vals):
        if vv is None:
            out.append(f'{_lab(label)} {"·" * width} —')
            continue
        if zero_base:
            half = width // 2
            n = int(round(abs(vv) / m * half))
            if vv != 0:
                n = max(n, 1)
            if vv >= 0:
                bar = '·' * half + '█' * n
            else:
                bar = '▒' * n + '·' * half
            bar = bar.ljust(width, '·')[:width]
        else:
            n = max(0, min(width, int(round((vv - lo) / span * width))))
            n = max(n, 1)
            bar = '█' * n + '·' * (width - n)
        out.append(f'{_lab(label)} {bar} {format(vv, fmt)}{unit}')
    return out


def chart(title, rows, unit='', width=14, fmt='.1f', lo=None, hi=None,
          reverse=True, extra='', zero_base=False) -> str:
    """一张完整的近 N 天小图：标题 + 折线 + 逐日横条 + 首尾变化。

    rows: [(label, value)]，按时间正序传入（旧→新）
    reverse: True 时条形图按新→旧排（卡片里更常见，默认开）
    """
    rows = [(_lab(l), _num(v)) for l, v in rows]
    if not rows:
        return f'**{title}**：数据不足'
    ys = [v for _, v in rows if v is not None]
    if not ys:
        return f'**{title}**：数据不足'
    first, last = ys[0], ys[-1]
    d = last - first
    arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
    dfmt = fmt.replace('+', '')          # 变化量不带正负号（前头已有箭头）
    lines = [f"**{title}** {format(last, fmt)}{unit}"
             f"　<font color='grey'>近{len(rows)}日 {arrow}{format(abs(d), dfmt)}{unit}</font>",
             f"  <font color='grey'>{rows[0][0]} → {rows[-1][0]}</font>  {spark([v for _, v in rows])}"]
    seq = list(reversed(rows)) if reverse else rows
    lines.extend('  ' + x for x in bars(seq, width=width, lo=lo, hi=hi, fmt=fmt,
                                        unit=unit, zero_base=zero_base))
    if extra:
        lines.append(f"  <font color='grey'>{extra}</font>")
    return '\n'.join(lines)


def pair(a_title, a_rows, b_title, b_rows, **kw) -> str:
    """两张图上下叠（一屏两看）"""
    return chart(a_title, a_rows, **kw) + '\n' + chart(b_title, b_rows, **kw)


def plain(text: str) -> str:
    """把 chart 输出里的 <font> 标签去掉（用在 Markdown 代码块里）"""
    import re
    return re.sub(r"</?font[^>]*>", '', text)


def multi(items, head=None) -> str:
    """多指标一行一个：名称 + 最新值 + 近N日变化 + 折线（紧凑，适合卡片）

    items = [(name, rows, unit, fmt), ...]   rows 为旧→新
    """
    L = [head] if head else []
    for name, rows, unit, fmt in items:
        rows = [(_lab(l), _num(v)) for l, v in rows]
        ys = [v for _, v in rows if v is not None]
        if len(ys) < 2:
            L.append(f"{name}：数据不足")
            continue
        d = ys[-1] - ys[0]
        arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
        dfmt = fmt.replace('+', '')
        L.append(f"{name} {format(ys[-1], fmt)}{unit} {arrow}{format(abs(d), dfmt)}  "
                 f"{spark([v for _, v in rows])}")
    return '\n'.join(L)
