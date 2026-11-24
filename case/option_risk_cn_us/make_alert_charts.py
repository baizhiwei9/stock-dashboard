# -*- coding: utf-8 -*-
"""预警邮件逐监控标的走势图（Pillow 中文，Linux 沙箱无 matplotlib）

产出（落在链路 data/ 目录），每个监控标的各一张近半年走势图，供报警邮件正文逐一对应内嵌：
  · data/alert_skew_{300ETF,500ETF,KC50,CYB,ZZ1000}.png —— A股 5 标的期权偏度分位
        （近月(剩余>20天) 25Δ认沽IV − 25Δ认购IV）
  · data/alert_skew_us_spx.png —— 美股 标普500 CBOE SKEW 指数
  · data/alert_vol_us_spx.png  —— 美股 标普500 VIX
  · data/alert_vol_us_ndx.png  —— 美股 纳指100 VXN
  · data/alert_vol_us_rut.png  —— 美股 罗素2000 RVX
  · data/alert_vvix_spx.png    —— 美股 标普500 CBOE VVIX（波动率的波动率）

每张图 = 近半年（126 交易日）走势 + 一年（252 交易日）P90 阈值虚线；分位 ≥ 阈值时画报警红框。
阈值取自 data/skew_pct_summary.json 的 thr（默认 90），可用环境变量 SKEW_ALARM_THR 覆盖。

注意：中文必须 layout_engine=BASIC（默认 RAQM 会出方框）。
"""
import json
import os

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from make_skew_charts import DATA, F, HALF, THR_C, WIN, panel

CN_ORDER = [
    ("300ETF", "沪深300ETF期权(510300)"),
    ("500ETF", "中证500ETF期权(510500)"),
    ("KC50", "科创50ETF期权(588000)"),
    ("CYB", "创业板ETF期权(159915)"),
    ("ZZ1000", "中证1000股指期权(MO 000852)"),
]
SKEW_C = (112, 48, 160)
VOL_C = (31, 78, 121)
VV_C = (198, 106, 0)

MADE = []


def pct_rank(arr, val):
    """分位（与 skew_pct_monitor.py 同口径）：≤ val 的占比 × 100。"""
    a = np.asarray([x for x in arr if x is not None and np.isfinite(x)], dtype=float)
    if (not len(a)) or val is None or not np.isfinite(val):
        return None
    return float((a <= val).sum() / len(a) * 100)


def _thr():
    p = os.path.join(DATA, "skew_pct_summary.json")
    try:
        return float(json.load(open(p, encoding="utf-8")).get("thr", 90) or 90)
    except Exception:
        return float(os.environ.get("SKEW_ALARM_THR", "90"))


def _nan(vals):
    out = []
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(f if np.isfinite(f) else None)
    return out


def _one(fname, title, sub, series, thr=None, alarm=False, legend=None, xdates=None,
         W=1180, H=620):
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    panel(img, dr, (26, 22, W - 26, H - 22), title, sub, series, thr=thr, ylab="",
          xdates=xdates, legend=legend, alarm=alarm)
    p = os.path.join(DATA, fname)
    img.save(p)
    MADE.append(os.path.basename(p))


def _line_chart(fname, title, unit, dts, vals, cur, wvals, thr, alarm, label, color):
    pct = pct_rank(wvals, cur)
    p90 = float(np.percentile([x for x in wvals if x is not None and np.isfinite(x)], 90))
    sub = ("最新 %.2f%s ｜ 一年分位 %.1f%% ｜ 一年P90 %.2f%s"
           % (cur, unit, pct or 0, p90, unit))
    if alarm:
        sub += "　🔴越阈"
    _one(fname, title, sub, [(label, color, vals, 3)], thr=p90, alarm=alarm,
         legend=[(label, color), ("一年P90阈值", THR_C)], xdates=dts)


def build_cn(thr):
    raw = pd.read_json(os.path.join(DATA, "skew_pct_cn_raw.json"), orient="records")
    for key, name in CN_ORDER:
        g = raw[raw["target"] == key].sort_values("date")
        if len(g) < 10:
            print("[warn] %s 偏度序列不足，跳过" % key)
            continue
        w, h = g.tail(WIN), g.tail(HALF)
        cur = float(w.iloc[-1]["skew"])
        pct = pct_rank(w["skew"].tolist(), cur)
        vals = _nan([v * 100 for v in h["skew"]])
        dts = ["%s-%s" % (str(d)[4:6], str(d)[6:]) for d in h["date"]]
        _line_chart("alert_skew_%s.png" % key,
                    "%s 期权偏度分位（近半年）" % name, "pt", dts, vals, cur * 100,
                    w["skew"].tolist(), thr, bool(pct is not None and pct >= thr),
                    "偏度=25Δ认沽IV−25Δ认购IV", SKEW_C)


def build_us_skew(thr):
    df = pd.read_json(os.path.join(DATA, "us_SKEW.json"), orient="records")
    df["d"] = pd.to_datetime(df["DATE"])
    df = df.dropna(subset=["SKEW"]).sort_values("d")
    w, h = df.tail(WIN), df.tail(HALF)
    cur = float(df.iloc[-1]["SKEW"])
    pct = pct_rank(w["SKEW"].tolist(), cur)
    _line_chart("alert_skew_us_spx.png", "标普500 CBOE SKEW 指数（近半年）", "",
                [d.strftime("%m-%d") for d in h["d"]], _nan(h["SKEW"]), cur,
                w["SKEW"].tolist(), thr, bool(pct is not None and pct >= thr),
                "CBOE SKEW 指数", SKEW_C)


def build_vvix(thr):
    df = pd.read_json(os.path.join(DATA, "us_VVIX.json"), orient="records")
    df["d"] = pd.to_datetime(df["DATE"])
    df = df.dropna(subset=["VVIX"]).sort_values("d")
    w, h = df.tail(WIN), df.tail(HALF)
    cur = float(df.iloc[-1]["VVIX"])
    pct = pct_rank(w["VVIX"].tolist(), cur)
    _line_chart("alert_vvix_spx.png",
                "标普500 CBOE VVIX 波动率的波动率（近半年）", "", 
                [d.strftime("%m-%d") for d in h["d"]], _nan(h["VVIX"]), cur,
                w["VVIX"].tolist(), thr, bool(pct is not None and pct >= thr),
                "CBOE VVIX（VIX 期权隐含波动率）", VV_C)


def build_us_vol(thr):
    for fn, fname, name, label in [
        ("us_VIX.json", "alert_vol_us_spx.png", "标普500 VIX", "VIX 指数"),
        ("us_VXN.json", "alert_vol_us_ndx.png", "纳指100 VXN", "VXN 指数"),
        ("us_RVX.json", "alert_vol_us_rut.png", "罗素2000 RVX", "RVX 指数"),
    ]:
        df = pd.read_json(os.path.join(DATA, fn), orient="records")
        df["d"] = pd.to_datetime(df["DATE"])
        df = df.dropna(subset=["CLOSE"]).sort_values("d")
        w, h = df.tail(WIN), df.tail(HALF)
        cur = float(df.iloc[-1]["CLOSE"])
        pct = pct_rank(w["CLOSE"].tolist(), cur)
        _line_chart(fname, "%s（近半年）" % name, "",
                    [d.strftime("%m-%d") for d in h["d"]], _nan(h["CLOSE"]), cur,
                    w["CLOSE"].tolist(), thr, bool(pct is not None and pct >= thr),
                    label, VOL_C)


def build():
    thr = _thr()
    # 先清掉上一轮残留，避免出图失败时邮件带上过期数据日的旧图
    import glob
    for p in glob.glob(os.path.join(DATA, "alert_*.png")):
        try:
            os.remove(p)
        except OSError:
            pass
    build_cn(thr)
    build_us_skew(thr)
    build_us_vol(thr)
    build_vvix(thr)
    print("saved %d alert charts (thr=%.1f%%): %s" % (len(MADE), thr, MADE))


if __name__ == "__main__":
    build()