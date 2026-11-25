# -*- coding: utf-8 -*-
"""科创50（588000）三联图：收盘价 + 科创50 VIX（QVIX 30日）+ 科创50 VVIX

上中下三个面板共享同一条时间轴：
  · 上面板：588000.SH 收盘价（元）
  · 中面板：科创50 VIX = 券商 QVIX(30日) 期权波动率指数（%），另附自身近一年 P90 与绝对水位 42
  · 下面板：科创50 VVIX（波动率的波动率，30 交易日年化对数波动率），另附自身近一年 P90

口径见 vvix_ashare.py（VVIX）与 analyze_ashare.py（VIX）。
用法： python make_kc50_vix_vvix.py            # 近一年 252 交易日
      WIN=126 python make_kc50_vix_vvix.py
产出： data/kcb50_px_vix_vvix.png
注意：中文必须 layout_engine=BASIC（默认 RAQM 会出方框）。
"""
import json
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from make_skew_charts import AXIS, BORDER, F, GRID, dash, poly

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

TGT = "KC50"
PRICE_CODE = "588000.SH"
WIN = int(os.environ.get("WIN", "252"))

VIX_ABS = 42.0          # 科创50 VIX 绝对水位（波动率预警红灯口径之一）
PX_C = (31, 78, 121)    # 蓝
VIX_C = (192, 0, 0)     # 红
VVIX_C = (211, 84, 0)   # 橙
THR_CH = (0, 128, 96)   # 阈值线（绿）
BG = (255, 255, 255)


def load():
    j = json.load(open(os.path.join(DATA, "vvix_ashare.json"), encoding="utf-8"))
    meta = j[TGT]
    px = pd.read_json(os.path.join(DATA, "price_ext.json"), orient="records",
                      dtype={"code": str})
    px["trade_date"] = px["trade_date"].astype(str)
    p = px[px["code"] == PRICE_CODE].dropna(subset=["close"]).sort_values("trade_date")
    pmap = dict(zip(p["trade_date"], p["close"].astype(float)))
    rows = [(x["d"], x["iv"], x["vvix"], pmap.get(x["d"])) for x in meta["series"]]
    rows = [r for r in rows if r[3] is not None][-WIN:]
    if not rows:
        raise SystemExit("kc50 三联图: 序列与价格无重叠")
    return meta, rows


def pct_of(all_vals, cur, n=252):
    w = np.asarray([x for x in all_vals[-n:] if x is not None and np.isfinite(x)], dtype=float)
    return float((w <= cur).sum() / len(w) * 100), float(np.percentile(w, 90))


def build():
    meta, rows = load()
    n = len(rows)
    dts = [r[0] for r in rows]
    vix = [r[1] for r in rows]
    vv = [r[2] for r in rows]
    pxv = [r[3] for r in rows]
    cur_px, cur_vix, cur_vv = pxv[-1], vix[-1], vv[-1]
    # 分位取日链落盘口径（vvix_ashare.json / ashare_final.json），保证图与卡片一致
    try:
        af = json.load(open(os.path.join(DATA, "ashare_final.json"), encoding="utf-8"))
        vix_pct = float(af["targets"][TGT]["vix_pct"])
    except Exception:
        vix_pct, _ = pct_of([x["iv"] for x in meta["series"]], cur_vix)
    _, vix_p90 = pct_of([x["iv"] for x in meta["series"]], cur_vix)
    vv_pct = float(meta.get("pct", pct_of([x["vvix"] for x in meta["series"]], cur_vv)[0]))
    vv_p90 = float(meta.get("p90", np.percentile([x["vvix"] for x in meta["series"]][-252:], 90)))

    W, H = 1800, 1810
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)

    # ---------- 标题 ----------
    dr.text((W / 2, 28), "科创50（588000）：ETF 收盘价 ｜ 科创50 VIX ｜ 科创50 VVIX",
            font=F(34), fill=(20, 20, 20), anchor="ma")
    dr.text((W / 2, 74), "近一年 %d 个交易日（%s ~ %s） ｜ 数据截至 %s ｜ "
                         "价格 %.3f 元 ｜ VIX %.2f（一年分位 %.1f%%） ｜ VVIX %.2f（一年分位 %.1f%%）"
            % (n, dts[0], dts[-1], dts[-1], cur_px, cur_vix, vix_pct, cur_vv, vv_pct),
            font=F(21), fill=(80, 80, 80), anchor="ma")

    def draw_panel(rect, vals, color, title, ylab, fmt, extras=None,
                   thr=None, thr_label=None, thr_color=THR_CH, extra_lines=()):
        x0, y0, x1, y1 = rect
        dr.rectangle([x0, y0, x1, y1], outline=BORDER, width=2)
        dr.text((x0 + 6, y0 - 32), title, font=F(23), fill=(20, 20, 20), anchor="lm")
        px0, py0 = x0 + 104, y0 + 18
        px1, py1 = x1 - 220, y1 - 42
        v = [x for x in vals if x is not None and np.isfinite(x)]
        for t in ([thr] + [e[0] for e in extra_lines]):
            if t is not None:
                v.append(float(t))
        lo, hi = min(v), max(v)
        pad = (hi - lo) * 0.10 or 1
        lo -= pad; hi += pad

        def X(i):
            return px0 + (px1 - px0) * (i / max(n - 1, 1))

        def Y(x):
            return py1 - (py1 - py0) * ((x - lo) / (hi - lo))

        for k in range(5):
            val = lo + (hi - lo) * k / 4
            yy = Y(val)
            dr.line([(px0, yy), (px1, yy)], fill=GRID, width=1)
            dr.text((px0 - 10, yy), fmt % val, font=F(18), fill=AXIS, anchor="rm")
        dr.text((px0 - 14, py0 - 12), ylab, font=F(18), fill=AXIS, anchor="lm")
        idxs = sorted(set([0, (n - 1) // 4, (n - 1) // 2, 3 * (n - 1) // 4, n - 1]))
        for i in idxs:
            d = dts[i]
            dr.text((X(i), py1 + 10), "%s-%s" % (d[4:6], d[6:]), font=F(17),
                    fill=AXIS, anchor="ma")
        if thr is not None and lo <= thr <= hi:
            dash(dr, px0, px1, Y(thr), thr_color, 2)
            dr.text((px1 - 6, Y(thr) - 8), thr_label or ("%.2f" % thr), font=F(17),
                    fill=thr_color, anchor="rd")
        for t, lab in extra_lines:
            if t is not None and lo <= t <= hi:
                dash(dr, px0, px1, Y(t), (140, 140, 140), 2)
                dr.text((px0 + 8, Y(t) - 8), lab, font=F(17), fill=(140, 140, 140), anchor="ld")
        pts = [(X(i), Y(x)) for i, x in enumerate(vals) if x is not None and np.isfinite(x)]
        poly(dr, pts, color, 3)
        if vals[-1] is not None and np.isfinite(vals[-1]):
            dr.ellipse([X(n - 1) - 6, Y(vals[-1]) - 6, X(n - 1) + 6, Y(vals[-1]) + 6], fill=color)
        for txt, col in (extras or []):
            dr.text((px0 + 14, py0 + 10), txt, font=F(19), fill=col, anchor="lm")

    draw_panel((60, 175, 1740, 640), pxv, PX_C,
               "① 科创50ETF（588000.SH）收盘价", "价格(元)", "%.2f",
               extras=[("最新 %.3f 元" % cur_px, PX_C)])
    draw_panel((60, 745, 1740, 1210), vix, VIX_C,
               "② 科创50 VIX（券商 QVIX 30日 期权波动率指数）", "VIX(%)", "%.1f",
               thr=vix_p90, thr_label="一年P90=%.1f" % vix_p90,
               extra_lines=((VIX_ABS, "绝对水位 %.0f" % VIX_ABS),),
               extras=[("最新 %.2f ｜ 一年分位 %.1f%%%s" % (cur_vix, vix_pct,
                    " 🔴≥90%" if vix_pct >= 90 else ""), VIX_C)])
    draw_panel((60, 1315, 1740, 1780), vv, VVIX_C,
               "③ 科创50 VVIX（波动率的波动率，30 交易日年化对数波动率）", "VVIX", "%.0f",
               thr=vv_p90, thr_label="一年P90=%.0f" % vv_p90,
               extras=[("最新 %.2f ｜ 一年分位 %.1f%%%s" % (cur_vv, vv_pct,
                    " 🔴≥90%" if vv_pct >= 90 else ""), VVIX_C)])

    out = os.path.join(DATA, "kcb50_px_vix_vvix.png")
    img.save(out)
    print("saved %s %s | n=%d | px=%.3f VIX=%.2f(%.1f%%,P90=%.1f) VVIX=%.2f(%.1f%%,P90=%.0f)"
          % (out, img.size, n, cur_px, cur_vix, vix_pct, vix_p90, cur_vv, vv_pct, vv_p90))


if __name__ == "__main__":
    build()