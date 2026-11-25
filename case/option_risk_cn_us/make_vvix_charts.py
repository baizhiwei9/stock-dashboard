# -*- coding: utf-8 -*-
"""A股 VVIX（波动率的波动率）× 对应标的收盘价 —— 对照图渲染（Pillow，Linux 沙箱无 matplotlib）

上下双面板、共享时间轴：
  · 上面板：VVIX（30 交易日年化对数波动率，口径见 vvix_ashare.py），含一年 P90 虚线、最新值与一年分位标注
  · 下面板：对应标的收盘价（默认 588000.SH 科创50ETF），含最新价标注

用法：
  python make_vvix_charts.py                # 默认 KC50 / 588000.SH，近一年 252 交易日
  TARGET=CYB WIN=126 python make_vvix_charts.py

产出：data/vvix_chart_<TARGET>.png
注意：中文必须 layout_engine=BASIC（默认 RAQM 会出方框）。
"""
import json
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from make_skew_charts import AXIS, BORDER, F, GRID, THR_C, dash, poly

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

TGT = os.environ.get("TARGET", "KC50")
WIN = int(os.environ.get("WIN", "252"))

PRICE = {"300ETF": "510300.SH", "500ETF": "510500.SH", "KC50": "588000.SH",
         "CYB": "159915.SZ", "ZZ1000": "000852.SH"}
VVIX_C = (211, 84, 0)      # 橙
PX_C = (31, 78, 121)       # 蓝
BG = (255, 255, 255)


def load():
    j = json.load(open(os.path.join(DATA, "vvix_ashare.json"), encoding="utf-8"))
    if TGT not in j:
        raise SystemExit("vvix chart: %s 无序列" % TGT)
    meta = j[TGT]
    px = pd.read_json(os.path.join(DATA, "price_ext.json"), orient="records",
                      dtype={"code": str})
    px["trade_date"] = px["trade_date"].astype(str)
    p = px[px["code"] == PRICE[TGT]].dropna(subset=["close"]).sort_values("trade_date")
    pmap = dict(zip(p["trade_date"], p["close"].astype(float)))
    rows = [(x["d"], x["iv"], x["vvix"], pmap.get(x["d"])) for x in meta["series"]]
    rows = [r for r in rows if r[3] is not None][-WIN:]
    if not rows:
        raise SystemExit("vvix chart: 序列与价格无重叠")
    return meta, rows


def build():
    meta, rows = load()
    n = len(rows)
    dts = [r[0] for r in rows]
    vv = [r[2] for r in rows]
    pxv = [r[3] for r in rows]
    cur_vv, cur_px = vv[-1], pxv[-1]
    w = np.asarray([x["vvix"] for x in meta["series"][-252:]], dtype=float)
    pct = float((w <= cur_vv).sum() / len(w) * 100)
    p90 = float(np.percentile(w, 90))

    W, H = 1800, 1220
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)

    # ---------- 标题 ----------
    dr.text((W / 2, 26), "%s VVIX（波动率的波动率）× %s 收盘价"
            % (meta["label"], PRICE[TGT]), font=F(34), fill=(20, 20, 20), anchor="ma")
    dr.text((W / 2, 70), "近一年 %d 个交易日（%s ~ %s） ｜ 数据截至 %s ｜ "
                         "最新 VVIX %.2f（一年分位 %.1f%%） ｜ %s %.3f"
            % (n, dts[0], dts[-1], dts[-1], cur_vv, pct, PRICE[TGT], cur_px),
            font=F(21), fill=(80, 80, 80), anchor="ma")

    # ---------- 面板绘制 ----------
    def draw_panel(rect, vals, color, title, ylab, fmt, extras=None, thr=None):
        x0, y0, x1, y1 = rect
        dr.rectangle([x0, y0, x1, y1], outline=BORDER, width=2)
        dr.text((x0 + 6, y0 - 30), title, font=F(23), fill=(20, 20, 20), anchor="lm")
        px0, py0 = x0 + 96, y0 + 18
        px1, py1 = x1 - 20, y1 - 42
        v = [x for x in vals if x is not None and np.isfinite(x)]
        if thr is not None:
            v.append(thr)
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
            dr.text((X(i), py1 + 8), "%s-%s" % (d[4:6], d[6:]), font=F(17),
                    fill=AXIS, anchor="ma")
        if thr is not None and lo <= thr <= hi:
            dash(dr, px0, px1, Y(thr), THR_C, 2)
            dr.text((px1 - 4, Y(thr) - 8), "一年P90=%.2f" % thr, font=F(17),
                    fill=THR_C, anchor="rd")
        pts = [(X(i), Y(x)) for i, x in enumerate(vals) if x is not None and np.isfinite(x)]
        poly(dr, pts, color, 3)
        if vals[-1] is not None and np.isfinite(vals[-1]):
            dr.ellipse([X(n - 1) - 6, Y(vals[-1]) - 6, X(n - 1) + 6, Y(vals[-1]) + 6], fill=color)
        for txt, col in (extras or []):
            lx, ly = px0 + 14, py0 + 8
            dr.text((lx, ly), txt, font=F(19), fill=col, anchor="lm")

    draw_panel((60, 150, 1740, 660), vv, VVIX_C,
               "① VVIX（波动率的波动率，30 交易日年化对数波动率）", "VVIX",
               "%.0f", thr=p90,
               extras=[("最新 %.2f ｜ 一年分位 %.1f%%%s" % (cur_vv, pct, " 🔴≥90%" if pct >= 90 else ""),
                        VVIX_C)])
    draw_panel((60, 740, 1740, 1150), pxv, PX_C,
               "② %s 收盘价（元）" % PRICE[TGT], "价格(元)", "%.2f",
               extras=[("最新 %.3f 元" % cur_px, PX_C)])

    # ---------- 脚注 ----------
    note = ("口径：VVIX = 100 × √252 × std( Δln(QVIX), 30 交易日, ddof=1 )；QVIX = 券商 30 日期权波动率指数（%s）。"
            % meta["iv_source"])
    note2 = "CBOE VVIX 以 VIX 期权链按无模型方差法算 30 天预期波动率（前瞻）；A股无波动率期权，故以波动率指数自身 30 日滚动实现波动率等价替代（回望），趋势与相对高低可比、绝对水平不与 CBOE 混用。"
    dr.text((60, 1168), note, font=F(18), fill=(90, 90, 90), anchor="lm")
    dr.text((60, 1198), note2, font=F(17), fill=(120, 120, 120), anchor="lm")

    out = os.path.join(DATA, "vvix_chart_%s.png" % TGT)
    img.save(out)
    print("saved", out, img.size, "| n=%d | VVIX=%.2f pct=%.1f%% p90=%.2f | price=%.3f"
          % (n, cur_vv, pct, p90, cur_px))


if __name__ == "__main__":
    build()