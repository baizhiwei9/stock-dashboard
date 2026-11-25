#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「市场临界监测 C(t) & Gap」的前三个特征值 λ1/λ2/λ3 画成 PNG（供飞书卡片内嵌）。

数据与 HTML / C(t)&Gap PNG 报告完全同源（rmt_cgap_history.json），只渲染、不重算指标。
- 近 SHOW_DAYS 个交易日（约近 3 个月），横轴逐刻度标注日期（避免“看起来没有日期”）；
- 每条曲线右端标注近 5 日走向：↑ 上行 / ↓ 下行 / → 走平；
- 纵轴取对数刻度，便于量级不同的三个特征值同图对比。
"""
import os, sys, json, math
from PIL import Image, ImageDraw, ImageFont

WORK = os.path.dirname(os.path.abspath(__file__))
WS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace'
# 字体优先级：必须是「汉字 + ASCII 数字/字母」都真字形的完整字体（Droid 缺数字字形，禁用）。
FONT_CANDIDATES = [os.path.join(WS, '.fonts', 'NotoSansSC-Regular.ttf'),
                   os.path.join(WS, '.fonts', 'NotoSansSC-Bold.ttf'),
                   '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
                   os.path.join(WS, 'fonts', 'NotoSansCJKsc-Regular.otf'),
                   '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf']


def font_ok(path):
    """真字形校验：能加载，且汉字与 ASCII 数字/字母均非豆腐块。"""
    try:
        f = ImageFont.truetype(path, 24)
    except Exception:
        return False
    try:
        tofu = bytes(f.getmask('\ue000'))
        for ch in ('市', '5', 'A'):
            m = f.getmask(ch)
            if m.size[0] <= 0 or bytes(m) == tofu:
                return False
        return True
    except Exception:
        return False


SHOW_DAYS = 60
hist = json.load(open(os.path.join(WORK, 'rmt_cgap_history.json'), encoding='utf-8'))
t = json.load(open(os.path.join(WORK, 'rmt_cgap_today.json'), encoding='utf-8'))
seg = hist[-SHOW_DAYS:]

x = [r['date'][5:] for r in seg]
S1 = [r.get('lambda1') for r in seg]
S2 = [r.get('lambda2') for r in seg]
S3 = [r.get('lambda3') for r in seg]

FONT_PATH = next((f for f in FONT_CANDIDATES if os.path.exists(f) and font_ok(f)), None)
if not FONT_PATH:
    print('[错误] 找不到「汉字 + ASCII 数字」均可用的字体'); sys.exit(3)
print('[字体] 使用', FONT_PATH)
f_title = ImageFont.truetype(FONT_PATH, 30)
f_sub = ImageFont.truetype(FONT_PATH, 17)
f_axis = ImageFont.truetype(FONT_PATH, 17)
f_leg = ImageFont.truetype(FONT_PATH, 17)
f_small = ImageFont.truetype(FONT_PATH, 15)
f_dir = ImageFont.truetype(FONT_PATH, 20)

W, H = 1440, 620
ML, MR, MT, MB = 95, 175, 126, 78
PX0, PY0, PX1, PY1 = ML, MT, W - MR, H - MB

allv = [v for v in (S1 + S2 + S3) if v is not None and v > 0]
lo = math.log10(min(allv) * 0.85)
hi = math.log10(max(allv) * 1.15)


def yE(v):
    return PY1 - (math.log10(v) - lo) / (hi - lo) * (PY1 - PY0)


img = Image.new('RGB', (W, H), 'white')
d = ImageDraw.Draw(img)

d.rectangle([PX0, PY0, PX1, PY1], outline=(200, 204, 210), width=1)
for tick in [1, 1.5, 2, 3, 5, 8, 10, 12, 15, 20]:
    lg = math.log10(tick)
    if not (lo <= lg <= hi):
        continue
    y = yE(tick)
    d.line([PX0, y, PX1, y], fill=(238, 240, 243), width=1)
    d.text((PX0 - 12, y), f'{tick:g}', font=f_axis, fill=(31, 78, 121), anchor='rm')


def pts(series):
    step = (PX1 - PX0) / max(1, len(series) - 1)
    return [(PX0 + i * step, yE(v)) for i, v in enumerate(series) if v is not None]


COLORS = [(31, 78, 121), (194, 65, 12), (16, 140, 96)]
NAMES = ['λ1 第一特征值（市场模式）', 'λ2 第二特征值（对比模式）', 'λ3 第三特征值']


def direction(series, win=5, flat=0.01):
    """近 win 个交易日的走向：↑ 上行 / ↓ 下行 / → 走平（相对变动 ±1% 内为走平）。"""
    xs = [v for v in series if v is not None]
    if len(xs) < win + 1:
        return '·', '数据不足'
    dd = xs[-1] - xs[-1 - win]
    base = xs[-1 - win]
    rel = dd / base if base else 0.0
    if rel > flat:
        return '↑', '上行'
    if rel < -flat:
        return '↓', '下行'
    return '→', '走平'


lx0 = ML
for idx, (name, col) in enumerate(zip(NAMES, COLORS)):
    series = [S1, S2, S3][idx]
    d.line(pts(series), fill=col, width=3 if idx == 0 else 2, joint='curve')
    okv = [v for v in series if v is not None]
    if okv:
        lx = PX0 + (PX1 - PX0)
        lastv = okv[-1]
        d.ellipse([lx - 5, yE(lastv) - 5, lx + 5, yE(lastv) + 5], fill=col)
        arrow, word = direction(series)
        d.text((lx + 10, yE(lastv)), f'{arrow} {word}', font=f_dir, fill=col, anchor='lm')
    d.line([lx0, 104, lx0 + 26, 104], fill=col, width=3)
    d.text((lx0 + 32, 104), name, font=f_leg, fill=col, anchor='lm')
    lx0 += 32 + d.textlength(name, font=f_leg) + 30

# 标题 / 副标题
d.text((ML, 30), '市场临界监测：前三个特征值走势（λ1 / λ2 / λ3）', font=f_title, fill=(31, 36, 48))
d.text((W - MR, 44), '来自 DoMate', font=f_sub, fill=(138, 147, 164), anchor='ra')
sub = (f"数据日 {t['date']}（真实行情） | {t['n_assets']} 只行业/宽基 ETF、{t['window']} 交易日滚动相关矩阵 | "
       f"近 {len(seg)} 个交易日 | 纵轴对数刻度 | 右端箭头＝近 5 日走向")
d.text((ML, 70), sub, font=f_sub, fill=(93, 102, 117))

# 横轴日期刻度
nlab = 7
step_x = (PX1 - PX0) / max(1, len(x) - 1)
_done = set()
for k in range(nlab):
    i = int(round(k * (len(x) - 1) / (nlab - 1)))
    if i in _done:
        continue
    _done.add(i)
    px = PX0 + i * step_x
    anchor = 'la' if k == 0 else ('ra' if k == nlab - 1 else 'ma')
    d.line([px, PY1, px, PY1 - 5], fill=(170, 176, 185), width=1)
    d.text((px, PY1 + 14), x[i], font=f_small, fill=(120, 129, 143), anchor=anchor)
d.text(((PX0 + PX1) / 2, PY1 + 44), f'交易日（近 {len(x)} 个交易日）',
       font=f_small, fill=(140, 148, 162), anchor='ma')

out = os.path.join(WORK, f"market_cgap_eig_chart_{t['date'].replace('-', '')}.png")
img.save(out)
print(f'EIG PNG -> {out}  ({os.path.getsize(out) / 1024:.0f} KB)  字体 {os.path.basename(FONT_PATH)}')