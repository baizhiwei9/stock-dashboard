#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把候选预警信号与全A（中证全指）放到同一张图对照，覆盖 2026-05 ~ 2026-11（含 7 月、10 月两波下跌）。

上panel：全A 收盘 + MA20，底色标出两波下跌区间，三角标出 RMT ΔS 红/黄灯触发日；
下panel：C(t)% 与 Gap 及其报警阈线（红警 35%、黄警 25%、切换 5、混战 3），用于判断阈值是否退化。
数据同源：rmt_cgap_history.json（λ/C/Gap/全A） + _hist_rmt.json 与 rmt_report_*.json（ΔS）。
"""
import os, sys, json, glob
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

WORK = os.path.dirname(os.path.abspath(__file__))
WS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace'
FONT_CANDIDATES = [os.path.join(WS, '.fonts', 'NotoSansSC-Regular.ttf'),
                   '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
                   os.path.join(WS, 'fonts', 'NotoSansCJKsc-Regular.otf'),
                   '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf']
START = '2026-05-06'


def font_ok(path):
    try:
        f = ImageFont.truetype(path, 24)
    except Exception:
        return False
    try:
        tofu = bytes(f.getmask('\ue000'))
        for ch in ('市', '5', 'A', '↑'):
            m = f.getmask(ch)
            if m.size[0] <= 0 or bytes(m) == tofu:
                return False
        return True
    except Exception:
        return False


FONT_PATH = next((f for f in FONT_CANDIDATES if os.path.exists(f) and font_ok(f)), None)
if not FONT_PATH:
    print('[错误] 找不到可用字体'); sys.exit(3)
print('[字体] 使用', FONT_PATH)

# ---------------- 数据 ----------------
cg = pd.DataFrame(json.load(open(os.path.join(WORK, 'rmt_cgap_history.json'), encoding='utf-8')))
cg['date'] = pd.to_datetime(cg['date'])
cg = cg.sort_values('date').reset_index(drop=True)
rm = pd.DataFrame(json.load(open(os.path.join(WORK, '_hist_rmt.json'), encoding='utf-8')))
rm['date'] = pd.to_datetime(rm['date'])
extra = []
for p in sorted(glob.glob(os.path.join(WORK, 'rmt_report_*.json'))):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    extra.append({'date': pd.to_datetime(p.split('_')[-1].split('.')[0]),
                  'delta_s': d.get('delta_s'), 'entropy': d.get('entropy'), 'gap_1': d.get('gap_1')})
rm = pd.concat([rm, pd.DataFrame(extra)], ignore_index=True).drop_duplicates('date').sort_values('date')
m = cg.merge(rm[['date', 'delta_s', 'entropy']], on='date', how='left')
m['ma20'] = m['index_close'].rolling(20).mean()
m = m[m['date'] >= pd.Timestamp(START)].reset_index(drop=True)

x = m['date'].dt.strftime('%m-%d').tolist()
px = m['index_close'].values
pm = m['ma20'].values
C = m['C'].values
G = m['Gap'].values
ds = m['delta_s'].values
red = m['date'].values == pd.Timestamp('2026-06-22')
WAVES = [('2026-06-22', '2026-07-30', '7月波 -15.7%'), ('2026-09-22', '2026-10-30', '10月波 -16.4%')]

W, H = 1500, 940
img = Image.new('RGB', (W, H), 'white')
d = ImageDraw.Draw(img)
f_title = ImageFont.truetype(FONT_PATH, 30)
f_sub = ImageFont.truetype(FONT_PATH, 17)
f_ax = ImageFont.truetype(FONT_PATH, 16)
f_sm = ImageFont.truetype(FONT_PATH, 15)
f_tri = ImageFont.truetype(FONT_PATH, 19)

# ---------------- 上 panel：全A ----------------
AX0, AX1 = 110, W - 150
A0, A1 = 96, 500
lo, hi = np.nanmin(px) * 0.96, np.nanmax(px) * 1.03


def yA(v):
    return A1 - (v - lo) / (hi - lo) * (A1 - A0)


def xi(datestr):
    idx = m.index[m['date'] == pd.Timestamp(datestr)]
    return AX0 + int(idx[0]) * (AX1 - AX0) / max(1, len(m) - 1) if len(idx) else None


# 两波底色
for a, b, lab in WAVES:
    xa, xb = xi(a), xi(b)
    if xa is not None and xb is not None:
        d.rectangle([xa, A0, xb, A1], fill=(252, 233, 233))

d.rectangle([AX0, A0, AX1, A1], outline=(200, 204, 210), width=1)
for v in range(int(lo // 100 * 100), int(hi) + 1, 200):
    if lo <= v <= hi:
        y = yA(v)
        d.line([AX0, y, AX1, y], fill=(238, 240, 243), width=1)
        d.text((AX0 - 12, y), f'{v}', font=f_ax, fill=(31, 78, 121), anchor='rm')
d.text((AX0 - 12, A0 - 24), '全A（中证全指）', font=f_ax, fill=(31, 78, 121), anchor='lm')

# 竖刻度线
step_x = (AX1 - AX0) / max(1, len(m) - 1)
for k in range(9):
    i = int(round(k * (len(m) - 1) / 8))
    xk = AX0 + i * step_x
    d.line([xk, A0, xk, A1], fill=(242, 244, 247), width=1)


def path(vals, fn):
    return [(AX0 + i * step_x, fn(v)) for i, v in enumerate(vals) if v is not None and not np.isnan(v)]


d.line(path(pm, yA), fill=(230, 126, 34), width=2, joint='curve')
d.line(path(px, yA), fill=(31, 78, 121), width=3, joint='curve')

# RMT ΔS 触发三角
n_red = n_yel = 0
for i, v in enumerate(ds):
    if v is None or np.isnan(v) or v < 0.05:
        continue
    xk = AX0 + i * step_x
    col = (192, 57, 43) if v >= 0.08 else (243, 156, 18)
    n_red += v >= 0.08
    n_yel += v < 0.08
    d.polygon([(xk, yA(px[i]) - 12), (xk - 7, yA(px[i]) - 24), (xk + 7, yA(px[i]) - 24)], fill=col)

d.text((AX0, A0 - 58), '全A 收盘 vs RMT ΔS 红/黄灯触发点（两波下跌区间粉底）', font=f_sub, fill=(60, 66, 78))
d.text((AX1, A0 - 58), '来自 DoMate', font=f_sub, fill=(138, 147, 164), anchor='ra')
# 图例
lx = AX0
d.line([lx, A0 + 18, lx + 26, A0 + 18], fill=(31, 78, 121), width=3)
d.text((lx + 32, A0 + 18), '全A 收盘', font=f_sm, fill=(31, 78, 121), anchor='lm'); lx += 32 + d.textlength('全A 收盘', font=f_sm) + 26
d.line([lx, A0 + 18, lx + 26, A0 + 18], fill=(230, 126, 34), width=2)
d.text((lx + 32, A0 + 18), 'MA20', font=f_sm, fill=(230, 126, 34), anchor='lm'); lx += 32 + d.textlength('MA20', font=f_sm) + 26
d.polygon([(lx + 8, A0 + 12), (lx + 1, A0 + 24), (lx + 15, A0 + 24)], fill=(192, 57, 43))
d.text((lx + 22, A0 + 18), f'ΔS≥+0.08 红灯（{n_red}）', font=f_sm, fill=(192, 57, 43), anchor='lm')
lx += 22 + d.textlength(f'ΔS≥+0.08 红灯（{n_red}）', font=f_sm) + 22
d.polygon([(lx + 8, A0 + 12), (lx + 1, A0 + 24), (lx + 15, A0 + 24)], fill=(243, 156, 18))
d.text((lx + 22, A0 + 18), f'ΔS≥+0.05 黄灯（{n_yel}）', font=f_sm, fill=(200, 130, 10), anchor='lm')

# ---------------- 下 panel：C(t) 与 Gap ----------------
B0, B1 = 600, 860
cx_lo, cx_hi = 0, 100          # C(t) %
gx_lo, gx_hi = 0, 6            # Gap


def yC(v):
    return B1 - (v - cx_lo) / (cx_hi - cx_lo) * (B1 - B0)


def yG(v):
    return B1 - (v - gx_lo) / (gx_hi - gx_lo) * (B1 - B0)


d.rectangle([AX0, B0, AX1, B1], outline=(200, 204, 210), width=1)
for v in range(0, 101, 20):
    y = yC(v)
    d.line([AX0, y, AX1, y], fill=(238, 240, 243), width=1)
    d.text((AX0 - 12, y), f'{v}%', font=f_ax, fill=(31, 78, 121), anchor='rm')
for v in (3, 5):
    y = yG(v)
    xx = AX0
    while xx < AX1:
        d.line([xx, y, min(xx + 10, AX1), y], fill=(192, 57, 43) if v == 3 else (230, 126, 34), width=2)
        xx += 16
    d.text((AX1 - 6, y - 10), f'{"混战线" if v == 3 else "切换线"} {v}', font=f_sm,
           fill=(192, 57, 43) if v == 3 else (200, 130, 10), anchor='rs')
for v in (25, 35):
    y = yC(v)
    xx = AX0
    while xx < AX1:
        d.line([xx, y, min(xx + 10, AX1), y], fill=(192, 57, 43) if v == 35 else (243, 156, 18), width=2)
        xx += 16
    d.text((AX1 - 6, y - 10), f'{"红警线" if v == 35 else "黄警线"} {v}%', font=f_sm,
           fill=(192, 57, 43) if v == 35 else (200, 130, 10), anchor='rs')

d.line(path(C, yC), fill=(31, 78, 121), width=3, joint='curve')
d.line(path(G, yG), fill=(194, 65, 12), width=2, joint='curve')
d.text((AX0, B0 - 24), 'C(t)% 与 Gap 及其阈线：C(t) 全程高于红警线 35%，Gap 全程低于切换线 5 → 阈值长期常亮',
       font=f_sub, fill=(60, 66, 78))
lx = AX0
d.line([lx, B0 - 2, lx + 26, B0 - 2], fill=(31, 78, 121), width=3)
d.text((lx + 32, B0 - 2), 'C(t) 市场协同度（左轴 %）', font=f_sm, fill=(31, 78, 121), anchor='lm')
lx += 32 + d.textlength('C(t) 市场协同度（左轴 %）', font=f_sm) + 24
d.line([lx, B0 - 2, lx + 26, B0 - 2], fill=(194, 65, 12), width=2)
d.text((lx + 32, B0 - 2), 'Gap 主线集中度（右轴，0–6）', font=f_sm, fill=(194, 65, 12), anchor='lm')

# 横轴日期（两个 panel 共用刻度）
for k in range(9):
    i = int(round(k * (len(m) - 1) / 8))
    xk = AX0 + i * step_x
    anchor = 'la' if k == 0 else ('ra' if k == 8 else 'ma')
    d.text((xk, B1 + 14), x[i], font=f_sm, fill=(120, 129, 143), anchor=anchor)
d.text(((AX0 + AX1) / 2, B1 + 42), f'交易日（{x[0]} ~ {x[-1]}，共 {len(m)} 日）',
       font=f_sm, fill=(140, 148, 162), anchor='ma')

d.text((110, 30), '全A 与 RMT/CGAP 预警信号对照（2026-05 ~ 2026-11，含 7 月、10 月两波下跌）',
       font=f_title, fill=(31, 36, 48))
out = os.path.join(WORK, 'cgap_vs_allA_20261103.png')
img.save(out)
print(f'PNG -> {out}  ({os.path.getsize(out) / 1024:.0f} KB)  红灯 {n_red} 个 / 黄灯 {n_yel} 个（样本内）')