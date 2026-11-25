# -*- coding: utf-8 -*-
"""偏度分位图纸渲染（Pillow 中文，Linux 沙箱无 matplotlib）

产出（落在链路 data/ 目录，供报警推送与人工查阅）：
  · data/skew_chart_all.png —— 中美 6 格：A股 5 标的偏度 + 标普500 CBOE SKEW（近半年，含一年 P90 阈值线与报警红框）
  · data/skew_iv_all.png    —— A股 5 格近月 25Δ 认沽/认购隐含波动率（近半年）

注意：中文必须 layout_engine=BASIC（默认 RAQM 会出方框）。
"""
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FP = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
WIN, HALF = 252, 126

CN = [("300ETF", "沪深300ETF期权"), ("500ETF", "中证500ETF期权"), ("CYB", "创业板ETF期权"),
      ("KC50", "科创50ETF期权"), ("ZZ1000", "中证1000股指期权")]
PUT = (192, 0, 0); CALL = (31, 78, 121); SKEW = (112, 48, 160)
THR_C = (220, 30, 30); GRID = (228, 228, 228); AXIS = (90, 90, 90); BORDER = (200, 200, 200)


def F(sz):
    return ImageFont.truetype(FP, sz, layout_engine=ImageFont.Layout.BASIC)


def dash(dr, x0, x1, y, color, w=2, on=9, off=7):
    x = x0
    while x < x1:
        dr.line([(x, y), (min(x + on, x1), y)], fill=color, width=w)
        x += on + off


def poly(dr, pts, color, w=3):
    if len(pts) >= 2:
        dr.line(pts, fill=color, width=w, joint="curve")


def panel(img, dr, rect, title, sub, series, thr=None, ylab="", xdates=None,
          legend=None, alarm=False, ymin=None, ymax=None):
    x0, y0, x1, y1 = rect
    dr.rectangle([x0, y0, x1, y1], outline=(THR_C if alarm else BORDER),
                 width=(4 if alarm else 2))
    dr.text(((x0 + x1) / 2, y0 + 8), title, font=F(26), fill=(20, 20, 20), anchor="ma")
    dr.text(((x0 + x1) / 2, y0 + 42), sub, font=F(19),
            fill=(THR_C if alarm else (90, 90, 90)), anchor="ma")
    px0, py0 = x0 + 74, y0 + 74
    px1, py1 = x1 - 20, y1 - 40
    vals = [v for s in series for v in s[2] if v is not None and np.isfinite(v)]
    if thr is not None:
        vals.append(thr)
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    if ymin is not None:
        lo = ymin
    if ymax is not None:
        hi = ymax
    pad = (hi - lo) * 0.12 or 1
    lo -= pad; hi += pad
    n = len(series[0][2]) if series else 0

    def X(i):
        return px0 + (px1 - px0) * (i / max(n - 1, 1))

    def Y(v):
        return py1 - (py1 - py0) * ((v - lo) / (hi - lo))

    for k in range(5):
        v = lo + (hi - lo) * k / 4
        yy = Y(v)
        dr.line([(px0, yy), (px1, yy)], fill=GRID, width=1)
        dr.text((px0 - 8, yy), "%.2f" % v, font=F(17), fill=AXIS, anchor="rm")
    dr.text((px0 - 12, py0 - 16), ylab, font=F(17), fill=AXIS, anchor="lm")
    if xdates:
        idxs = sorted(set([0, (n - 1) // 4, (n - 1) // 2, 3 * (n - 1) // 4, n - 1]))
        for i in idxs:
            dr.text((X(i), py1 + 6), xdates[i], font=F(16), fill=AXIS, anchor="ma")
    if thr is not None and lo <= thr <= hi:
        dash(dr, px0, px1, Y(thr), THR_C, 2)
        dr.text((px1 - 4, Y(thr) - 8), "一年P90=%.2f" % thr, font=F(16), fill=THR_C, anchor="rd")
    for name, col, vv, w in series:
        pts = [(X(i), Y(v)) for i, v in enumerate(vv) if v is not None and np.isfinite(v)]
        poly(dr, pts, col, w)
    name, col, vv, w = series[-1]
    if vv and vv[-1] is not None and np.isfinite(vv[-1]):
        dr.ellipse([X(n - 1) - 5, Y(vv[-1]) - 5, X(n - 1) + 5, Y(vv[-1]) + 5], fill=col)
    if legend:
        lx, ly = px0 + 12, py0 + 6
        for txt, col in legend:
            dr.line([(lx, ly + 9), (lx + 26, ly + 9)], fill=col, width=3)
            dr.text((lx + 32, ly), txt, font=F(17), fill=(50, 50, 50), anchor="lm")
            ly += 24


def build():
    raw = pd.read_json(os.path.join(DATA, "skew_pct_cn_raw.json"), orient="records")
    sk = pd.read_json(os.path.join(DATA, "us_SKEW.json"), orient="records")
    sk["d"] = pd.to_datetime(sk["DATE"])
    sk = sk.dropna(subset=["SKEW"]).sort_values("d")
    try:
        thr = float(json_load(os.path.join(DATA, "skew_pct_summary.json")).get("thr", 90))
    except Exception:
        thr = 90.0

    panels_skew, panels_iv = [], []
    for key, name in CN:
        g = raw[raw["target"] == key].sort_values("date")
        w = g.tail(WIN); p90 = float(np.percentile(w["skew"] * 100, 90))
        h = g.tail(HALF)
        dts = [str(d)[4:6] + "-" + str(d)[6:] for d in h["date"]]
        cur = h["skew"].iloc[-1] * 100
        pct = float((w["skew"] <= h["skew"].iloc[-1]).sum() / len(w) * 100)
        al = pct >= thr
        panels_skew.append(dict(
            title=name, sub="偏度 %.2fpt  分位 %.1f%%  %s" % (cur, pct, "🔴报警" if al else "未报警"),
            ylab="偏度(认沽−认购, IV点)",
            series=[("偏度", SKEW, (h["skew"] * 100).tolist(), 3)],
            thr=p90, x=dts, alarm=al,
            legend=[("偏度(25D认沽IV − 25D认购IV)", SKEW), ("一年P90阈值", THR_C)]))
        panels_iv.append(dict(
            title=name, sub="近月(剩余>20天) 25D 隐含波动率", ylab="隐含波动率(%)",
            series=[("25D认沽IV", PUT, (h["iv25p"] * 100).tolist(), 3),
                    ("25D认购IV", CALL, (h["iv25c"] * 100).tolist(), 3)],
            thr=None, x=dts, alarm=False,
            legend=[("25D认沽IV", PUT), ("25D认购IV", CALL)]))

    sw = sk.tail(WIN); sp90 = float(np.percentile(sw["SKEW"], 90))
    sh = sk.tail(HALF)
    sdts = [d.strftime("%m-%d") for d in sh["d"]]
    scur = float(sh["SKEW"].iloc[-1])
    spct = float((sw["SKEW"] <= scur).sum() / len(sw) * 100)
    panels_skew.append(dict(
        title="标普500 (CBOE SKEW 指数)",
        sub="SKEW %.2f  分位 %.1f%%  %s" % (scur, spct, "🔴报警" if spct >= thr else "未报警"),
        ylab="CBOE SKEW 指数",
        series=[("SKEW", SKEW, sh["SKEW"].tolist(), 3)],
        thr=sp90, x=sdts, alarm=spct >= thr,
        legend=[("CBOE SKEW 指数", SKEW), ("一年P90阈值", THR_C)]))

    cn_last = str(raw.sort_values("date")["date"].iloc[-1])
    cn_disp = "%s-%s-%s" % (cn_last[:4], cn_last[4:6], cn_last[6:])
    us_disp = sk["d"].iloc[-1].strftime("%Y-%m-%d")

    def grid(panels, rows, cols, W, H, hdr, foot):
        img = Image.new("RGB", (W, H), "white"); dr = ImageDraw.Draw(img)
        dr.text((W / 2, 26), hdr, font=F(36), fill=(10, 10, 10), anchor="ma")
        dr.text((W / 2, 78), foot, font=F(21), fill=(90, 90, 90), anchor="ma")
        L, R, T, B = 86, 40, 130, 62
        gx, gy = 66, 96
        pw = (W - L - R - gx * (cols - 1)) / cols
        ph = (H - T - B - gy * (rows - 1)) / rows
        for i, p in enumerate(panels):
            r, c = divmod(i, cols)
            x0 = L + c * (pw + gx); y0 = T + r * (ph + gy)
            panel(img, dr, (x0, y0, x0 + pw, y0 + ph), p["title"], p["sub"],
                  p["series"], thr=p["thr"], ylab=p["ylab"], xdates=p["x"],
                  legend=p["legend"], alarm=p["alarm"])
        return img

    img = grid(panels_skew, 3, 2, 1900, 1560, "中美期权偏度分位监控（近半年走势）",
               "偏度 = 近月(剩余>20天) 25Δ认沽IV − 25Δ认购IV；阈值取各标的自身近一年(252交易日)%g%%分位；"
               "数据截至 %s（A股）/ %s（美股）" % (thr, cn_disp, us_disp))
    p1 = os.path.join(DATA, "skew_chart_all.png")
    img.save(p1); print("saved", p1, img.size)

    img2 = grid(panels_iv, 3, 2, 1900, 1400, "A股近月25Δ隐含波动率（近半年走势）",
                "近月合约(剩余>20天) 25Δ 认沽/认购隐含波动率；数据截至 %s" % cn_disp)
    p2 = os.path.join(DATA, "skew_iv_all.png")
    img2.save(p2); print("saved", p2, img2.size)


def json_load(p):
    import json
    with open(p, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    build()