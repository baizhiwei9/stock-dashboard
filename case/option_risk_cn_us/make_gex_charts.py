# -*- coding: utf-8 -*-
"""A股五轨 期权 GEX / Gamma Flip 图纸渲染（Pillow 中文，Linux 沙箱无 matplotlib）

产出（落在链路 data/ 目录，供报警邮件内嵌与人工查阅）：
  · data/gex_chart_{标的}.png —— 每个标的各一张（近半年，3 格）：
      ① 净 GEX(30/90 加权，亿元) + 0 线（负值=做市商空伽马，波动放大）
      ② Gamma Flip 距离 dist=(S−flip)/S (%) + 一年 5% 分位阈值线
      ③ 现价 S 与 Gamma Flip 价位（直观「贴近/跌破」关系）
  · data/gex_chart_alarm.png —— 当同一数据日有 ≥1 个标的越阈时，把【越阈标的】的 3 格
      按行拼成一张合并图（标题列出全部越阈标的）。只有一个标的中招时即为该标的的行。

标的：300ETF(510300) / 500ETF(510500) / KC50(588000) / CYB(159915) / ZZ1000(MO 000852)

注意：中文必须 layout_engine=BASIC（默认 RAQM 会出方框）。
"""
import json
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FP = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
HALF = 126

GEX = (31, 78, 121); NEG = (200, 40, 40); DIST = (112, 48, 160)
SPOT = (31, 78, 121); FLIP = (216, 116, 20)
THR_C = (220, 30, 30); ZERO = (140, 140, 140); GRID = (228, 228, 228)
AXIS = (90, 90, 90); BORDER = (200, 200, 200); LBL = (60, 60, 60)


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


def panel(img, dr, rect, title, sub, series, hline=None, hline_label="",
          thr=None, thr_label="", ylab="", xdates=None, legend=None, alarm=False):
    x0, y0, x1, y1 = rect
    dr.rectangle([x0, y0, x1, y1], outline=(THR_C if alarm else BORDER),
                 width=(4 if alarm else 2))
    dr.text(((x0 + x1) / 2, y0 + 8), title, font=F(26), fill=(20, 20, 20), anchor="ma")
    dr.text(((x0 + x1) / 2, y0 + 44), sub, font=F(19),
            fill=(THR_C if alarm else (90, 90, 90)), anchor="ma")
    px0, py0 = x0 + 96, y0 + 100
    px1, py1 = x1 - 22, y1 - 40
    vals = [v for s in series for v in s[2] if v is not None and np.isfinite(v)]
    for extra in (hline, thr):
        if extra is not None and np.isfinite(extra):
            vals.append(extra)
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
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
    dr.text((px0 - 14, py0 - 16), ylab, font=F(17), fill=AXIS, anchor="lm")
    if xdates:
        idxs = sorted(set([0, (n - 1) // 4, (n - 1) // 2, 3 * (n - 1) // 4, n - 1]))
        for i in idxs:
            dr.text((X(i), py1 + 6), xdates[i], font=F(16), fill=AXIS, anchor="ma")
    if hline is not None and lo <= hline <= hi:
        dash(dr, px0, px1, Y(hline), ZERO, 2)
        if hline_label:
            dr.text((px1 - 4, Y(hline) - 8), hline_label, font=F(16), fill=ZERO, anchor="rd")
    if thr is not None and lo <= thr <= hi:
        dash(dr, px0, px1, Y(thr), THR_C, 2, on=6, off=5)
        if thr_label:
            ly = Y(thr) - 12
            if ly < py0 + 16:
                ly = Y(thr) + 14
            dr.text((px0 + 6, ly), thr_label, font=F(16), fill=THR_C, anchor="la")
    for name, col, vv, w in series:
        pts = [(X(i), Y(v)) for i, v in enumerate(vv) if v is not None and np.isfinite(v)]
        poly(dr, pts, col, w)
    name, col, vv, w = series[-1]
    if vv and vv[-1] is not None and np.isfinite(vv[-1]):
        dr.ellipse([X(n - 1) - 5, Y(vv[-1]) - 5, X(n - 1) + 5, Y(vv[-1]) + 5], fill=col)
    if legend:
        lx, ly = px0 + 12, py0 + 8
        for txt, col in legend:
            dr.line([(lx, ly + 9), (lx + 26, ly + 9)], fill=col, width=3)
            dr.text((lx + 32, ly), txt, font=F(17), fill=(50, 50, 50), anchor="lm")
            ly += 24


def _nanify(xs):
    out = []
    for v in xs:
        try:
            f = float(v)
        except (TypeError, ValueError):
            out.append(None); continue
        out.append(f if np.isfinite(f) else None)
    return out


def _panels(recs_t, summ_t):
    """按标的构造 3 格面板参数（近半年）+ 日期轴。"""
    df = pd.DataFrame(recs_t).sort_values("date")
    h = df.tail(HALF).reset_index(drop=True)
    dts = [str(d)[4:6] + "-" + str(d)[6:] for d in h["date"]]
    gex = _nanify(h.get("gex_weighted", pd.Series([None] * len(h))))
    sub1 = "最新 %s 亿元  %s" % (("%.4f" % summ_t["gex_weighted"]) if summ_t.get("gex_weighted") is not None else "n/a",
                                 "●负值(空伽马)" if summ_t.get("cond_a") else "正值")
    p1 = dict(title="① 净 GEX（30/90 恒定期限加权，亿元）", sub=sub1, ylab="净GEX(亿元)",
              series=[("净GEX", GEX, gex, 3)], hline=0.0, hline_label="0 线",
              x=dts, alarm=bool(summ_t.get("cond_a")),
              legend=[("净GEX(30/90加权)", GEX), ("0 线", ZERO)])

    dist = _nanify(h.get("dist", pd.Series([None] * len(h))))
    thr_dist = summ_t.get("thr_dist")
    if summ_t.get("cond_a") and summ_t.get("cond_b"):
        _st2 = "●报警（条件A、B同时满足）"
    elif summ_t.get("cond_b"):
        _st2 = "●dist分位越阈（仅条件B；净GEX≥0，未构成报警）"
    else:
        _st2 = "未越阈"
    sub2 = ("最新 dist=%s%%  一年分位=%s%% (n=%s)  %s"
            % (summ_t.get("dist"), summ_t.get("pct"), summ_t.get("n_pct"), _st2))
    p2 = dict(title="② Gamma Flip 距离 dist=(S−flip)/S（%）", sub=sub2, ylab="dist(%)",
              series=[("dist", DIST, dist, 3)], hline=0.0, hline_label="0 线(跌破翻转)",
              thr=thr_dist,
              thr_label=("一年%.0f%%分位=%.2f%%" % (summ_t.get("thr", 5), thr_dist)) if thr_dist is not None else "",
              x=dts, alarm=bool(summ_t.get("cond_b")),
              legend=[("dist=(S−flip)/S", DIST), ("0 线=现价跌破 Flip", ZERO),
                      ("一年%.0f%%分位阈值" % summ_t.get("thr", 5), THR_C)])

    spot = _nanify(h["S"])
    flip = _nanify(h.get("flip_1", pd.Series([None] * len(h))))
    sub3 = "现价 %s  vs  Flip %s  %s" % (
        ("%.4f" % summ_t["S"]) if summ_t.get("S") is not None else "n/a",
        ("%.4f" % summ_t["flip_1"]) if summ_t.get("flip_1") is not None else "n/a",
        "● 现价已跌破 Flip" if summ_t.get("breach") else "现价仍在 Flip 上方")
    p3 = dict(title="③ 现价 S vs Gamma Flip 价位", sub=sub3, ylab="价位(元)",
              series=[("现价", SPOT, spot, 3), ("GammaFlip", FLIP, flip, 3)],
              hline=None, x=dts, alarm=bool(summ_t.get("breach")),
              legend=[("现价 S", SPOT), ("Gamma Flip 价位", FLIP)])
    return [p1, p2, p3], dts


def _draw_panel(dr, p, rect):
    panel(None, dr, rect, p["title"], p["sub"], p["series"],
          hline=p.get("hline"), hline_label=p.get("hline_label", ""),
          thr=p.get("thr"), thr_label=p.get("thr_label", ""),
          ylab=p["ylab"], xdates=p["x"], legend=p.get("legend"), alarm=p["alarm"])


def build_single(key, recs_t, summ_t):
    """单标的 1960×640 图。"""
    panels, _ = _panels(recs_t, summ_t)
    W, H = 1960, 640
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    status = {"red": "● 报警", "yellow": "○ 降级报警", "none": "— 未报警"}[summ_t.get("level", "none")]
    dr.text((W / 2, 22), "%s 期权 GEX / Gamma Flip 监控（近半年）" % summ_t.get("label", key),
            font=F(36), fill=(10, 10, 10), anchor="ma")
    dr.text((W / 2, 72),
            "报警口径：净GEX<0 且 Flip距离(dist)一年分位≤%g%%（两者同时满足）｜乘数 %g｜r=%.2f、DTE %d~%d｜%s｜数据截至 %s"
            % (summ_t.get("thr", 5), summ_t.get("mult", 10000), summ_t.get("r", 0.02),
               30, 180, status, _disp(summ_t.get("date"))),
            font=F(20), fill=(90, 90, 90), anchor="ma")
    L, R, T, B, gx = 96, 40, 118, 60, 60
    pw = (W - L - R - gx * 2) / 3
    ph = H - T - B
    for i, p in enumerate(panels):
        x0 = L + i * (pw + gx)
        _draw_panel(dr, p, (x0, T, x0 + pw, T + ph))
    out = os.path.join(DATA, "gex_chart_%s.png" % key)
    img.save(out)
    return out


def _disp(d):
    d = str(d or "")
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:]) if len(d) == 8 else d


def build_combined(alarm_keys, series, summ):
    """多标的越阈合并图：每个越阈标的占一行（标签行 + 3 格），标题列出全部越阈标的。"""
    W = 1960
    L, R, B, gx = 96, 40, 60, 60
    T = 122
    pw = (W - L - R - gx * 2) / 3
    ph = 340
    row_h = 40 + ph + 18
    H = T + len(alarm_keys) * row_h + B
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    names = "、".join(summ["targets"][k].get("label", k) for k in alarm_keys)
    dr.text((W / 2, 20), "A股期权 GEX / Gamma Flip 多标的报警（近半年）", font=F(36),
            fill=(10, 10, 10), anchor="ma")
    dr.text((W / 2, 68),
            "越阈标的：%s｜报警口径：净GEX<0 且 Flip距离(dist)一年分位≤%g%%（两者同时满足）｜数据截至 %s"
            % (names, summ.get("thr", 5), _disp(summ.get("asof"))),
            font=F(20), fill=(THR_C if alarm_keys else (90, 90, 90)), anchor="ma")

    for r, k in enumerate(alarm_keys):
        t = summ["targets"][k]
        panels, _ = _panels(series[k], t)
        y = T + r * row_h
        disp = "●" if t.get("level") == "red" else "○"
        dr.text((L, y + 6),
                "%s %s ｜ 净GEX=%s 亿元(<0:%s) ｜ Flip=%s ｜ dist=%s%% ｜ 一年分位=%s%% ≤%g%%"
                % (disp, t.get("label", k), t.get("gex_weighted"), t.get("cond_a"),
                   t.get("flip_1"), t.get("dist"), t.get("pct"), t.get("thr", 5)),
                font=F(24), fill=(THR_C if t.get("level") == "red" else (216, 116, 20)), anchor="la")
        py = y + 40
        for i, p in enumerate(panels):
            x0 = L + i * (pw + gx)
            _draw_panel(dr, p, (x0, py, x0 + pw, py + ph))
    out = os.path.join(DATA, "gex_chart_alarm.png")
    img.save(out)
    return out


def build():
    series = json.load(open(os.path.join(DATA, "gex_flip_series.json"), encoding="utf-8"))
    summ = json.load(open(os.path.join(DATA, "gex_flip_summary.json"), encoding="utf-8"))
    if not series or not summ.get("targets"):
        raise SystemExit("gex charts: 无序列数据")

    made = []
    for k, recs_t in series.items():
        if k not in summ["targets"]:
            continue
        if not recs_t:
            continue
        made.append(build_single(k, recs_t, summ["targets"][k]))
    print("saved", len(made), "single charts:", [os.path.basename(m) for m in made])

    alarm_keys = [k for k in series if summ["targets"].get(k, {}).get("alarm")]
    if alarm_keys:
        out = build_combined(alarm_keys, series, summ)
        print("saved", out, Image.open(out).size)
    else:
        # 无越阈标的：清理上一轮遗留的合并报警图，避免邮件带上过期数据日的旧图
        stale = os.path.join(DATA, "gex_chart_alarm.png")
        if os.path.exists(stale):
            try:
                os.remove(stale)
                print("no gex alarm -> removed stale combined chart")
            except OSError:
                pass
        print("no gex alarm -> skip combined chart")


if __name__ == "__main__":
    build()