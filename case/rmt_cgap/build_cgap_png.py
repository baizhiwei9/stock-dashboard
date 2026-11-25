#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场临界监测 · 分面板图（每个指标一个子图：各指标自身 250 日滚动分位曲线 + 共用 90% 临界线 + 全A 对照）。

三个子图（只作展示，不参与预警判定）——曲线一律为该指标自身的 250 日滚动分位（0–100%），共用同一条 90% 临界线：
  1) λ1（C(t)）250 日滚动分位                        —— 风险预警：分位 ≥ 90% 看空（临界固定 90%）
  2) 复杂度缺口（ln(18) − 熵）250 日滚动分位           —— 风险预警：分位 ≥ 90% 看空
  3) Δ收窄（gap1 的 5 交易日变化，gap1=(λ1−λ2)/λ1）250 日滚动分位 —— 监控/机会：分位 ≥ 90% 看多（风格切换窗口）
每个子图另标注该指标当日现值与"临界水平"（该指标自身近 250 日第 90 百分位对应的原始值），
右轴为全A（中证全指 000985）收盘，仅作同一时间轴对照。

绘制顺序：先在透明层画"90% 临界之上"的看空/看多遮罩并合成，再画边框、曲线与文字。
文本一律用 ASCII 下标（λ1/λ2/gap1）：实测本机 NotoSansSC 缺 ₁₂₃ 字形，下标会渲染成方块。

用法：python3 build_cgap_png.py [文件名后缀]
"""
import os, sys, json
from PIL import Image, ImageDraw, ImageFont

from cgap_warning_power import warning_power

WORK = os.path.dirname(os.path.abspath(__file__))
WS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace'
FONT_CANDIDATES = [os.path.join(WS, '.fonts', 'NotoSansSC-Regular.ttf'),
                   os.path.join(WS, '.fonts', 'NotoSansSC-Bold.ttf'),
                   '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
                   os.path.join(WS, 'fonts', 'NotoSansCJKsc-Regular.otf'),
                   '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
                   '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']


def font_ok(path):
    """真字形校验：能加载，且汉字/数字/≥/λ 均非豆腐块（排除缺字表与坏字体）。"""
    try:
        f = ImageFont.truetype(path, 24)
    except Exception:
        return False
    try:
        tofu = bytes(f.getmask('\ue000'))
        for ch in ('市', '5', 'A', '≥', 'λ'):
            m = f.getmask(ch)
            if m.size[0] <= 0 or bytes(m) == tofu:
                return False
        return True
    except Exception:
        return False


SHOW_DAYS = 62
hist = json.load(open(os.path.join(WORK, 'rmt_cgap_history.json'), encoding='utf-8'))
t = json.load(open(os.path.join(WORK, 'rmt_cgap_today.json'), encoding='utf-8'))
seg = hist[-SHOW_DAYS:]
SI = {it['key']: it for it in (t.get('show_indicators') or {}).get('items', [])}
WP = warning_power(hist)
R90 = (next((r for r in WP['rows'] if r['key'] == 'q250>90'), None) or {}) if WP.get('ok') else {}

dates = [r['date'][5:] for r in seg]
IV = [r.get('index_close') for r in seg]

# 三块子图：series＝该指标自身 250 日滚动分位（%，0–100）；raw / raw_crit＝原始现值与原始临界水平（仅标注用）
PANELS = [
    {'name': 'λ1（C(t)）250 日滚动分位', 'kind': '风险预警', 'side': 'risk',
     'series': [r.get('q250') for r in seg], 'raw_same': True,
     'raw': [r.get('q250') for r in seg], 'raw_fmt': '{:.1f}%',
     'raw_crit': [90.0] * len(seg), 'raw_crit_fmt': '{:.0f}',
     'crit': [90.0] * len(seg), 'crit_desc': '临界：分位 90%（三个子图共用）',
     'color': (31, 78, 121), 'shade': (255, 165, 0, 55), 'key': 'l1_q250'},
    {'name': '复杂度缺口（ln(18) − 熵）250 日滚动分位', 'kind': '风险预警', 'side': 'risk',
     'series': [r.get('q_egap') for r in seg],
     'raw': [r.get('entropy_gap') for r in seg], 'raw_fmt': '{:.4f}',
     'raw_crit': [r.get('crit_eg') for r in seg], 'raw_crit_fmt': '{:.4f}',
     'crit': [90.0] * len(seg), 'crit_desc': '临界：分位 90%（三个子图共用）',
     'color': (125, 60, 152), 'shade': (255, 165, 0, 55), 'key': 'complexity_gap'},
    {'name': 'Δ收窄（gap1 的 5 交易日变化；gap1=(λ1−λ2)/λ1）250 日滚动分位', 'kind': '监控/机会', 'side': 'opp',
     'series': [r.get('q_dgap') for r in seg],
     'raw': [r.get('d_gap1_5') for r in seg], 'raw_fmt': '{:+.4f}',
     'raw_crit': [r.get('crit_dg') for r in seg], 'raw_crit_fmt': '{:.4f}',
     'crit': [90.0] * len(seg), 'crit_desc': '临界：分位 90%（三个子图共用）',
     'color': (194, 65, 12), 'shade': (39, 174, 96, 45), 'key': 'gap_narrow'},
]

W = 1440
HDR, PH, GAP = 138, 352, 52
ML, MR = 122, 200
PX0, PX1 = ML, W - MR
PANEL_END = HDR + 3 * PH + 2 * GAP          # 最后一个子图底边
FOOT_TOP = PANEL_END + 34
H = FOOT_TOP + 4 * 26 + 16
STEP = (PX1 - PX0) / max(1, len(dates) - 1)

FONT_PATH = next((f for f in FONT_CANDIDATES if os.path.exists(f) and font_ok(f)), None)
if not FONT_PATH:
    print('[错误] 找不到可用的中文字体'); sys.exit(3)
print('[字体] 使用', FONT_PATH)
F = {n: ImageFont.truetype(FONT_PATH, s) for n, s in
     (('title', 29), ('sub', 16), ('panel', 19), ('axis', 15), ('small', 15), ('tiny', 14))}


def geom(k, p):
    top = HDR + k * (PH + GAP)
    y0, y1 = top + 46, top + PH - 28
    lo, hi = 0.0, 100.0          # 左轴固定 0–100%（分位刻度，三个子图可比）

    def bounds(vals, frac=0.08):
        v = [x for x in vals if x is not None]
        lo_, hi_ = (min(v), max(v)) if v else (0.0, 1.0)
        if hi_ == lo_:
            hi_ = lo_ + 1.0
        pad = (hi_ - lo_) * frac
        return lo_ - pad, hi_ + pad

    iv_lo, iv_hi = bounds(IV)

    def Y(v):
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    def YI(v):
        return y1 - (v - iv_lo) / (iv_hi - iv_lo) * (y1 - y0)

    return dict(top=top, y0=y0, y1=y1, lo=lo, hi=hi, iv_lo=iv_lo, iv_hi=iv_hi, Y=Y, YI=YI,
                cpts=[(PX0 + i * STEP, Y(c)) for i, c in enumerate(p['crit']) if c is not None],
                spts=[(PX0 + i * STEP, Y(v)) for i, v in enumerate(p['series']) if v is not None],
                ipts=[(PX0 + i * STEP, YI(v)) for i, v in enumerate(IV) if v is not None])


img = Image.new('RGB', (W, H), 'white')
overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)
G = [geom(k, p) for k, p in enumerate(PANELS)]
for k, p in enumerate(PANELS):
    if G[k]['cpts']:
        od.polygon(G[k]['cpts'] + [(PX1, G[k]['y0']), (PX0, G[k]['y0'])], fill=p['shade'])
img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
d = ImageDraw.Draw(img)

print('  [子图读数核对]')
for k, p in enumerate(PANELS):
    g = G[k]
    y0, y1, top, lo, hi, Y, YI = g['y0'], g['y1'], g['top'], g['lo'], g['hi'], g['Y'], g['YI']
    cur_p = p['series'][-1]
    raw_now, raw_crit_now = p['raw'][-1], p['raw_crit'][-1]
    crit_now = p['crit'][-1]
    passed = crit_now is not None and cur_p is not None and cur_p >= crit_now
    mark = '　←　已过临界' if passed else '　（未到临界）'
    cond = '看空：分位 ≥ 90%' if p['side'] == 'risk' else '看多（机会）：分位 ≥ 90%'
    if p.get('raw_same'):
        rule = f"{cond}（今日分位 {cur_p:.1f}%，临界固定 90%）{mark}"
    else:
        rule = (f"{cond}（今日分位 {cur_p:.1f}%；现值 {p['raw_fmt'].format(raw_now)}"
                f"，其 250 日第 90 百分位＝临界水平 "
                f"{p['raw_crit_fmt'].format(raw_crit_now).replace('+', '')}）{mark}")
    sub = rule + f"　｜　{p['crit_desc']}"
    print(f"    子图{k + 1}: 分位 {cur_p:.1f}% | 现值 {p['raw_fmt'].format(raw_now)} | "
          f"临界水平 {p['raw_crit_fmt'].format(raw_crit_now).replace('+', '')} | {p['side']} | "
          f"{'已过临界' if passed else '未到临界'}")

    d.text((ML, top), f"{k + 1}) {p['name']}", font=F['panel'], fill=p['color'])
    d.text((W - MR + 48, top + 4), p['kind'], font=F['small'], fill=(120, 129, 143), anchor='ra')
    d.text((ML, top + 24), sub, font=F['small'], fill=(70, 78, 92))
    over = d.textlength(sub, font=F['small']) - (W - MR + 48 - ML)
    if over > 0:
        print(f'  [WARN] 子图 {k + 1} 说明行超出可用宽度 {over:.0f}px')

    d.rectangle([PX0, y0, PX1, y1], outline=(205, 210, 217), width=1)
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        yy = Y(v)
        d.line([PX0, yy, PX1, yy], fill=(238, 240, 243), width=1)
        d.text((PX0 - 10, yy), f'{v:.0f}%', font=F['axis'], fill=p['color'], anchor='rm')
    d.text((PX0 - 10, y0 - 15), '分位（左轴）', font=F['tiny'], fill=p['color'], anchor='rm')
    for i in range(3):
        v = g['iv_lo'] + (g['iv_hi'] - g['iv_lo']) * i / 2
        d.text((W - 14, YI(v)), f'{v:,.0f}', font=F['axis'], fill=(15, 118, 110), anchor='rm')
    d.text((W - 14, y0 - 15), '全A（右轴）', font=F['tiny'], fill=(15, 118, 110), anchor='rm')

    d.line(g['ipts'], fill=(15, 118, 110), width=2, joint='curve')
    if len(g['cpts']) > 1:
        d.line(g['cpts'], fill=(230, 126, 34), width=2)
    d.line(g['spts'], fill=p['color'], width=3, joint='curve')

    ex, ey = g['spts'][-1]
    d.ellipse([ex - 4, ey - 4, ex + 4, ey + 4], fill=p['color'])
    ycrit = Y(crit_now) if crit_now is not None else y1
    # 端点读数：分位 + 原始现值，按余量方向上下堆叠，避开临界线标签
    down = ey < (y0 + y1) / 2 or abs(ey - ycrit) < 24
    y1_lab = ey + 10 if down else ey - 10
    y2_lab = y1_lab + 20 if down else y1_lab - 20
    d.text((PX1 - 4, y1_lab), f"分位 {cur_p:.1f}%", font=F['small'], fill=p['color'],
           anchor='la' if down else 'la')
    if not p.get('raw_same'):
        raw_txt = p['raw_fmt'].format(raw_now)
        d.text((PX1 - 4, y2_lab), f"现值 {raw_txt}", font=F['tiny'], fill=(70, 78, 92), anchor='la')
    if crit_now is not None:
        yc_lab = ycrit - 18 if down else ycrit + 12
        d.text((PX1 - 4, yc_lab), '临界 90%', font=F['tiny'], fill=(230, 126, 34), anchor='ls')

    lx = PX0 + 8
    _labs = [('分位', p['color']), ('临界 90%', (230, 126, 34)), ('全A 对照', (15, 118, 110))]
    _lw = sum(21 + d.textlength(lab, font=F['tiny']) + 18 for lab, _c in _labs) - 18
    d.rectangle([PX0 + 3, y0 + 2, PX0 + 12 + _lw, y0 + 24], fill=(255, 255, 255))   # 图例底衬，避免被曲线压住
    for lab, col in _labs:
        d.line([lx, y0 + 12, lx + 16, y0 + 12], fill=col, width=3 if col == p['color'] else 2)
        d.text((lx + 21, y0 + 12), lab, font=F['tiny'], fill=col, anchor='lm')
        lx += 21 + d.textlength(lab, font=F['tiny']) + 18

    nlab = 4 if k < 2 else 6
    for i2 in range(nlab):
        i = int(round(i2 * (len(dates) - 1) / (nlab - 1)))
        px = PX0 + i * STEP
        d.line([px, y1, px, y1 - 5], fill=(170, 176, 185), width=1)
        anchor = 'la' if i2 == 0 else ('ra' if i2 == nlab - 1 else 'ma')
        d.text((px, y1 + 10), dates[i], font=F['tiny'], fill=(120, 129, 143), anchor=anchor)

d.text((ML, 24), '市场临界监测 · 分面板（每个指标 250 日分位 vs 全A 对照）', font=F['title'], fill=(31, 36, 48))
d.text((W - MR + 48, 36), '来自 DoMate', font=F['sub'], fill=(138, 147, 164), anchor='ra')
_hdr_lines = [
    (f"数据日 {t['date']}（真实行情） | 18 只行业/宽基 ETF、{t['window']} 交易日滚动相关矩阵 | "
     f"近 {len(seg)} 个交易日（约 3 个月） | 每个子图右轴为全A（中证全指 000985）对照",
     64, F['sub'], (93, 102, 117)),
    ("怎么读：三个子图的曲线都是该指标自身的 250 日滚动分位（左轴 0–100%），共用同一条 90% 临界线。",
     86, F['small'], (70, 78, 92)),
    ("1) 2) 风险指标 —— 分位 ≥ 90% → 看空（风险升温）；3) 监控/机会指标 —— 分位 ≥ 90% → 看多（风格切换窗口）。"
     "临界之上已用底色标出（淡橙＝看空区，淡绿＝看多区）。",
     106, F['small'], (70, 78, 92)),
]
for _ln, _y, _f, _col in _hdr_lines:
    d.text((ML, _y), _ln, font=_f, fill=_col)
    _over = d.textlength(_ln, font=_f) - (W - 2 * ML)
    if _over > 0:
        print(f'  [WARN] 头部 y={_y} 行超出画布 {_over:.0f}px')


def fnum(x, spec):
    try:
        return format(float(x), spec)
    except Exception:
        return 'n/a'


l1q, egs, gns = SI.get('l1_q250', {}), SI.get('complexity_gap', {}), SI.get('gap_narrow', {})
lines = [
    (f"当日读数　1) λ1 分位 {fnum(l1q.get('value'), '.1f')}%（临界 90%）→ {l1q.get('stance', 'n/a')}",
     (70, 78, 92)),
    (f"2) 复杂度缺口 现值 {fnum(egs.get('value'), '.4f')}（临界水平 {fnum(egs.get('crit_level'), '.4f')}）"
     f"＝ 自身分位 {fnum(egs.get('percentile'), '.1f')}% → {egs.get('stance', 'n/a')}"
     f"（差 {abs(egs.get('gap_to_crit') or 0):.1f} 个百分位）", (70, 78, 92)),
    (f"3) Δ收窄 现值 {fnum(gns.get('value'), '+.4f')}（临界水平 {fnum(gns.get('crit_level'), '.4f')}）"
     f"＝ 自身分位 {fnum(gns.get('percentile'), '.1f')}% → {gns.get('stance', 'n/a')}"
     f"（差 {abs(gns.get('gap_to_crit') or 0):.1f} 个百分位）", (70, 78, 92)),
    (f"预警有效性（469 日实测）：1) 其后 20 日全A {fnum((R90.get('fwd20_on') or 0) * 100, '+.2f')}%"
     f"（低 {abs(R90.get('d_fwd20') or 0) * 100:.2f}pp）；2) −3.61%（低 4.06pp）；3) +3.09%（高 2.65pp）"
     f"　｜　三项只作展示，不改动既有预警判定", (31, 78, 121)),
]
for i, (ln, col) in enumerate(lines):
    d.text((ML, FOOT_TOP + i * 26), ln, font=F['small'], fill=col)
    over = d.textlength(ln, font=F['small']) - (W - 2 * ML)
    if over > 0:
        print(f'  [WARN] 页脚第 {i + 1} 行超出画布 {over:.0f}px')

suffix = sys.argv[1] if len(sys.argv) > 1 else ''
out = os.path.join(WORK, f"market_cgap_chart_{t['date'].replace('-', '')}{suffix}.png")
img.save(out)
print(f'PNG -> {out}  ({os.path.getsize(out) / 1024:.0f} KB)  字体 {os.path.basename(FONT_PATH)}')
print(f'  子图 {len(PANELS)} 个 | x 轴 {len(dates)} 交易日（{dates[0]} → {dates[-1]}）| 画布 {W}x{H}')