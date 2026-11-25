# -*- coding: utf-8 -*-
"""A股 5 大标的 VVIX（波动率的波动率）× 对应指数/ETF 价格 —— 合一对照图（Pillow）

上下双面板、共享时间轴（取 5 标的 VVIX 序列共同可用区间，末 252 交易日封顶）：
  · 上面板：5 条 VVIX 曲线（口径见 vvix_ashare.py），图例含最新值与一年分位
  · 下面板：5 条对应标的价格（归一化，区间起点=100）以便同轴比较走势

用法：python make_vvix_all.py
产出：data/vvix_chart_ALL.png
注意：中文必须 layout_engine=BASIC。
"""
import json
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from make_skew_charts import AXIS, BORDER, F, GRID, dash, poly

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WIN = int(os.environ.get("WIN", "252"))

ORDER = ["300ETF", "500ETF", "KC50", "CYB", "ZZ1000"]
SHORT = {"300ETF": "沪深300ETF(510300)", "500ETF": "中证500ETF(510500)",
         "KC50": "科创50ETF(588000)", "CYB": "创业板ETF(159915)",
         "ZZ1000": "中证1000(MO 000852)"}
PRICE = {"300ETF": "510300.SH", "500ETF": "510500.SH", "KC50": "588000.SH",
         "CYB": "159915.SZ", "ZZ1000": "000852.SH"}
COL = {"300ETF": (31, 78, 121), "500ETF": (211, 84, 0), "KC50": (192, 0, 0),
       "CYB": (112, 48, 160), "ZZ1000": (0, 128, 96)}
BG = (255, 255, 255)


def load():
    j = json.load(open(os.path.join(DATA, "vvix_ashare.json"), encoding="utf-8"))
    px = pd.read_json(os.path.join(DATA, "price_ext.json"), orient="records",
                      dtype={"code": str})
    px["trade_date"] = px["trade_date"].astype(str)
    ser, pmap, meta = {}, {}, {}
    for k in ORDER:
        m = j[k]
        meta[k] = m
        ser[k] = {x["d"]: x["vvix"] for x in m["series"]}
        p = px[px["code"] == PRICE[k]].dropna(subset=["close"]).sort_values("trade_date")
        pmap[k] = dict(zip(p["trade_date"], p["close"].astype(float)))
    common = None
    for k in ORDER:
        ds = set(ser[k]) & set(pmap[k])
        common = ds if common is None else (common & ds)
    dts = sorted(common)[-WIN:]
    return meta, ser, pmap, dts


def build():
    meta, ser, pmap, dts = load()
    n = len(dts)
    vv = {k: [ser[k].get(d) for d in dts] for k in ORDER}
    px = {}
    for k in ORDER:
        raw = [pmap[k].get(d) for d in dts]
        base = next((x for x in raw if x is not None), None)
        px[k] = [None if x is None else x / base * 100.0 for x in raw]

    W, H = 1800, 1390
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    dr.text((W / 2, 26), "A股 5 大指数期权 VVIX（波动率的波动率）× 对应标的价格 对照",
            font=F(34), fill=(20, 20, 20), anchor="ma")
    dr.text((W / 2, 70), "近一年共同区间 %d 个交易日（%s ~ %s） ｜ 数据截至 %s"
            % (n, dts[0], dts[-1], dts[-1]), font=F(21), fill=(80, 80, 80), anchor="ma")

    def panel(rect, data, title, ylab, fmt, thr=None):
        x0, y0, x1, y1 = rect
        dr.rectangle([x0, y0, x1, y1], outline=BORDER, width=2)
        dr.text((x0 + 6, y0 - 30), title, font=F(23), fill=(20, 20, 20), anchor="lm")
        px0, py0 = x0 + 96, y0 + 18
        px1, py1 = x1 - 20, y1 - 46
        flat = [v for s in data.values() for v in s if v is not None and np.isfinite(v)]
        if thr is not None:
            flat.append(thr)
        lo, hi = min(flat), max(flat)
        pad = (hi - lo) * 0.10 or 1
        lo -= pad
        hi += pad

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
            dash(dr, px0, px1, Y(thr), (220, 30, 30), 2)
        for k in ORDER:
            pts = [(X(i), Y(x)) for i, x in enumerate(data[k]) if x is not None and np.isfinite(x)]
            poly(dr, pts, COL[k], 3)
            if pts:
                dr.ellipse([pts[-1][0] - 6, pts[-1][1] - 6, pts[-1][0] + 6, pts[-1][1] + 6],
                           fill=COL[k])
        # 图例（两列）
        lx, ly = px0 + 16, py0 + 10
        for j, k in enumerate(ORDER):
            cx = lx + (j % 2) * 330
            cy = ly + (j // 2) * 30
            dr.line([(cx, cy + 9), (cx + 30, cy + 9)], fill=COL[k], width=5)
            m = meta[k]
            lab = (SHORT[k] if data is vv else SHORT[k])
            if data is vv:
                txt = "%s  VVIX %.0f (%.0f%%分位)" % (lab, m["vvix"], m["pct"])
            else:
                txt = "%s  收盘 %.2f" % (lab, pmap[k][dts[-1]])
            dr.text((cx + 38, cy + 9), txt, font=F(18), fill=COL[k], anchor="lm")

    panel((60, 150, 1740, 700), vv,
          "① VVIX（30 交易日年化对数波动率，口径见脚注）", "VVIX", "%.0f")
    panel((60, 800, 1740, 1230), px,
          "② 对应标的价格（归一化，区间起点 = 100）", "相对(起点=100)", "%.0f")

    note = "口径：VVIX = 100 × √252 × std( Δln(IV), 30 交易日, ddof=1 )；IV 取券商 30 日期权波动率指数 QVIX（300ETF/500ETF/科创50/创业板），中证1000 无官方 QVIX，以 MO 股指期权当月平价 ATM 隐含波动率替代。"
    note2 = "CBOE VVIX 以 VIX 期权链按无模型方差法算 30 天预期波动率（前瞻）；A股无波动率期权，故以波动率指数自身 30 日滚动实现波动率等价替代（回望），"
    note3 = "趋势与相对高低可比、绝对水平不与 CBOE 混用。中证1000 序列自 20251126 起，故本图共同区间受其限制。"
    dr.text((60, 1278), note, font=F(18), fill=(90, 90, 90), anchor="lm")
    dr.text((60, 1306), note2, font=F(17), fill=(120, 120, 120), anchor="lm")
    dr.text((60, 1332), note3, font=F(17), fill=(120, 120, 120), anchor="lm")

    out = os.path.join(DATA, "vvix_chart_ALL.png")
    img.save(out)
    print("saved", out, img.size, "| n=%d | %s~%s" % (n, dts[0], dts[-1]))
    for k in ORDER:
        print("   %-7s VVIX=%.1f pct=%.1f%%  price=%s" % (k, meta[k]["vvix"], meta[k]["pct"], pmap[k][dts[-1]]))


if __name__ == "__main__":
    build()