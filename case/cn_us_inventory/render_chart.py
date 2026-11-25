# -*- coding: utf-8 -*-
"""渲染中美库存周期图表(PNG) 并用飞书应用凭据上传后推送到飞书"""
import json
import os
import sys
import numpy as np
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont


_SW_EMPTY = {
    "countries": {
        "china": {"current": {"demand": "—", "inventory": "—"},
                  "predicted_switch": {"center": "", "window": ["", ""],
                                       "early_case": [""], "target_side": "—"},
                  "triggers": {}},
        "us": {"current": {"demand": "—", "inventory": "—"},
               "predicted_switch": {"center": "", "window": ["", ""],
                                    "early_case": [""], "target_side": "—"},
               "triggers": {}},
    }
}


def _load_switches(path):
    """switches.json 是人工研判的周期切换配置（仓库外维护，新环境/首跑常见缺失）。
    缺失或字段不全时按占位结构补齐：报告照常出，只是预测切换相关字段显示「—」，
    而不是整条链路炸掉。"""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:                                       # noqa: BLE001
        raw = {}
    out = json.loads(json.dumps(_SW_EMPTY))
    for ck, cv in (raw.get("countries") or {}).items():
        base = json.loads(json.dumps(_SW_EMPTY["countries"]["china"]))
        tgt = out["countries"].setdefault(ck, base)
        for f, v in (cv or {}).items():
            if isinstance(v, dict) and isinstance(tgt.get(f), dict):
                tgt[f].update(v)
            else:
                tgt[f] = v
    return out


# 原指向 ses_gffe5e7d65...（空目录，无任何数据）。改为本 case 目录，
# data/ 下的 csv/json 才是实际在用的那一份。
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = BASE + "/data"
OUT_PNG = BASE + "/中美库存周期图表.png"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

# ---- 路径统一口径：case/_lib/paths.py（换机器只改一处）----------------------
try:
    from paths import font_path
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    import os as _o, sys as _s
    _d = _o.path.dirname(_o.path.abspath(__file__))
    while not _o.path.isfile(_o.path.join(_d, "_lib", "paths.py")):
        _n = _o.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    _s.path.insert(0, _o.path.join(_d, "_lib"))
    from paths import font_path

FONT = font_path()        # workspace/.fonts → 系统 Noto CJK（须在 import 之后）


import app_secrets  # noqa: E402

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

PH = {"主动补库存": (231, 246, 238), "被动补库存": (251, 233, 217),
      "主动去库存": (251, 234, 230), "被动去库存": (233, 242, 251)}
NEXT = {"主动补库存": "被动补库存", "被动补库存": "主动去库存",
        "主动去库存": "被动去库存", "被动去库存": "主动补库存"}
CN, US = (192, 57, 43), (10, 120, 194)


def mkey(m):
    m = m.replace("-", "")
    return int(m[:4]) * 12 + int(m[4:]) - 1


def mlist(a, b):
    out = []
    k = mkey(a)
    while k <= mkey(b):
        y, m = divmod(k, 12)
        out.append(f"{y}{m + 1:02d}")
        k += 1
    return out


def load():
    ch = pd.read_csv(DATA + "/china_hard_base.csv", dtype={"month": str})
    us = pd.read_csv(DATA + "/us_base.csv", dtype={"month": str})
    fc = json.load(open(DATA + "/forecast2.json", encoding="utf-8"))
    sw = _load_switches(DATA + "/switches.json")
    return ch, us, fc, sw


def panel(d, dr, key, color, title, unit, sw, x_start, x_end):
    """返回 (绘图信息)"""
    ftbl = fc0[key]["base"]["table"]
    sd, si = fc0[key]["base"]["slope_demand"], fc0[key]["base"]["slope_inv"]
    months = mlist(x_start, x_end)
    hist_m = list(d["month"])
    f_m = [r["month"] for r in ftbl]
    dem = {m: None for m in months}
    inv = {m: None for m in months}
    phz = {m: None for m in months}
    for m in hist_m:
        i = d.index[d["month"] == m][0]
        dem[m] = float(d["demand"].iloc[i]); inv[m] = float(d["inventory"].iloc[i]); phz[m] = d["phase"].iloc[i] if isinstance(d["phase"].iloc[i], str) else None
    dm = list(d["demand"]); im = list(d["inventory"])
    for k, r in enumerate(ftbl, 1):
        mm = r["month"]
        if mm in dem:
            dem[mm] = round(dm[-1] + sd * k, 2); inv[mm] = round(im[-1] + si * k, 2); phz[mm] = r["phase"]
    _ps = (sw.get("countries") or {}).get(key, {}).get("predicted_switch") or {}
    _win = [str(x).replace("-", "") for x in (_ps.get("window") or [])]
    _cen = str(_ps.get("center") or "").replace("-", "")
    if len(_win) < 2:
        _win = ["", ""]
    _cur = str(d["phase"].iloc[-1])
    return {"months": months, "dem": dem, "inv": inv, "phz": phz,
            "last_hist": hist_m[-1], "f0": f_m[0], "f1": f_m[-1],
            "win": _win, "center": _cen,
            "cur": _cur, "nxt": NEXT.get(_cur, "—"),
            "curve": color, "title": title, "unit": unit}


def draw_panel(dr, info, x0, y0, w, h, ymin, ymax):
    months = info["months"]
    n = len(months)
    ml, mr = 56, 40
    pw = w - ml - mr
    X = lambda i: x0 + ml + pw * i / (n - 1)
    Y = lambda v: y0 + h - h * (v - ymin) / (ymax - ymin)

    # 阶段色带
    i = 0
    while i < n:
        p = info["phz"][months[i]]
        if p is None:
            i += 1; continue
        j = i
        while j + 1 < n and info["phz"][months[j + 1]] == p:
            j += 1
        dr.rectangle([X(i) - pw / (n - 1) / 2, y0, X(j) + pw / (n - 1) / 2, y0 + h], fill=PH[p])
        i = j + 1

    # 预测区
    if info["f0"] in months:
        xf = X(months.index(info["f0"]))
        dr.rectangle([xf, y0, X(n - 1), y0 + h], fill=(238, 238, 238))
        dr.line([xf, y0, xf, y0 + h], fill=(150, 150, 150), width=1)

    # 预测切换窗口
    w0, w1 = info["win"]
    if w0 in months and w1 in months:
        xa, xb = X(months.index(w0)), X(months.index(w1))
        overlay = Image.new("RGBA", (int(xb - xa) + 1, h), (255, 210, 120, 90))
        base_img.alpha_composite(overlay, (int(xa), y0))
        xc = X(months.index(info["center"]))
        dr.line([xc, y0, xc, y0 + h], fill=(200, 60, 30), width=2)

    # 网格与0线
    dr.rectangle([x0 + ml, y0, x0 + ml + pw, y0 + h], outline=(210, 210, 210))
    for v in range(int(np.ceil(ymin)), int(ymax) + 1, max(2, int((ymax - ymin) / 6))):
        dr.line([x0 + ml, Y(v), x0 + ml + pw, Y(v)], fill=(235, 235, 235))
        dr.text((x0 + ml - 6 - tw(dr, f"{v}%", F(12)), Y(v) - 7), f"{v}%", font=F(12), fill=(130, 130, 130))
    dr.line([x0 + ml, Y(0), x0 + ml + pw, Y(0)], fill=(180, 180, 180), width=1)

    def line(series, dash, width):
        pts = [(X(i), Y(series[months[i]])) for i in range(n) if series[months[i]] is not None]
        if len(pts) < 2:
            return
        if dash:
            for k in range(len(pts) - 1):
                xa, ya = pts[k]; xb, yb = pts[k + 1]
                seg = ((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5
                if seg == 0:
                    continue
                for t in np.arange(0, 1, 1000):
                    dr.line([xa + (xb - xa) * t / seg * 8, ya + (yb - ya) * t / seg * 8,
                             xa + (xb - xa) * (t + 0.5) / seg * 8, ya + (yb - ya) * (t + 0.5) / seg * 8],
                            fill=color_of(info), width=width)
        else:
            dr.line(pts, fill=color_of(info), width=width, joint="curve")

    # 需求（虚线）与库存（实线）
    def draw_series(series, dash, width):
        pts = [(X(i), Y(series[months[i]])) for i in range(n) if series[months[i]] is not None]
        if len(pts) < 2:
            return
        if dash:
            for k in range(len(pts) - 1):
                (xa, ya), (xb, yb) = pts[k], pts[k + 1]
                L = ((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5
                if L == 0:
                    continue
                steps = max(1, int(L / 8))
                for s in range(steps):
                    t0, t1 = s / steps, min(1, (s + 0.55) / steps)
                    dr.line([xa + (xb - xa) * t0, ya + (yb - ya) * t0,
                             xa + (xb - xa) * t1, ya + (yb - ya) * t1], fill=info["curve"], width=width)
        else:
            dr.line(pts, fill=info["curve"], width=width, joint="curve")

    draw_series(info["inv"], False, 3)
    draw_series(info["dem"], True, 3)

    # 端点标注：库存置于点上方、需求置于点下方，贴边时左移
    for series, txt, dy in ((info["inv"], "库存", -20), (info["dem"], "需求", 8)):
        idx = max(i for i in range(n) if series[months[i]] is not None)
        xv, yv = X(idx), Y(series[months[idx]])
        dr.ellipse([xv - 4, yv - 4, xv + 4, yv + 4], fill=info["curve"])
        lab = f"{txt} {series[months[idx]]:.1f}{info['unit']}"
        wl = tw(dr, lab, F(13))
        lx2 = (xv + 9) if xv + 9 + wl <= x0 + ml + pw else (xv - 9 - wl)
        dr.text((lx2, yv + dy), lab, font=F(13), fill=(50, 50, 50))

    # 预计切换月标注（置于面板上方，避免与端点标注重叠）
    if info["center"] in months:
        xc = X(months.index(info["center"]))
        lab = f"预计切换 {info['center'][:4]}-{info['center'][4:]}"
        wl = tw(dr, lab, F(12))
        bx = min(xc + 4, x0 + ml + pw - wl - 6)
        dr.rectangle([bx - 3, y0 + 5, bx + wl + 3, y0 + 22], fill=(255, 245, 240), outline=(200, 60, 30))
        dr.text((bx, y0 + 7), lab, font=F(12), fill=(170, 40, 20))

    # 年份刻度
    for i, m in enumerate(months):
        if m[4:] == "01":
            dr.line([X(i), y0 + h, X(i), y0 + h + 4], fill=(150, 150, 150))
            dr.text((X(i) - tw(dr, m[:4], F(12)) / 2, y0 + h + 6), m[:4], font=F(12), fill=(120, 120, 120))

    # 面板标题（左）与图例（右）分行排布，避免重叠
    dr.text((x0 + ml, y0 - 28), info["title"], font=F(17), fill=(30, 30, 30))
    # 当前周期 / 下一周期 标注（标题右侧，同一行）
    _ct = info.get("center") or ""
    _tail = ("（预计 %s-%s 切换）" % (_ct[:4], _ct[4:])) if len(_ct) >= 6 else ""
    ann = (f"当前周期：{info['cur']}（截至 {info['last_hist'][:4]}-{info['last_hist'][4:]}）"
           f"　→　下一周期：{info['nxt']}{_tail}")
    ax = x0 + ml + 78
    aw = tw(dr, ann, F(14))
    dr.rectangle([ax - 6, y0 - 36, ax + aw + 6, y0 - 14], fill=(255, 247, 242), outline=(214, 126, 96))
    dr.text((ax, y0 - 33), ann, font=F(14), fill=(168, 36, 20))
    legend = [("库存（实线）", False), ("需求（虚线）", True)]
    lgw = 250
    lgx = x0 + ml + pw - lgw
    for txt, dash in legend:
        if dash:
            for s in range(3):
                dr.line([lgx + s * 11, y0 - 20, lgx + s * 11 + 6, y0 - 20], fill=info["curve"], width=3)
            dr.text((lgx + 40, y0 - 28), txt, font=F(13), fill=(70, 70, 70))
            lgx += 120
        else:
            dr.line([lgx, y0 - 20, lgx + 26, y0 - 20], fill=info["curve"], width=3)
            dr.text((lgx + 32, y0 - 28), txt, font=F(13), fill=(70, 70, 70))
            lgx += 120


def color_of(info):
    return info["curve"]


_F = {}


def F(size, bold=False):
    k = (size, bold)
    if k not in _F:
        _F[k] = ImageFont.truetype(FONT, size)
    return _F[k]


def tw(dr, s, font):
    b = dr.textbbox((0, 0), s, font=font)
    return b[2] - b[0]


ch, us, fc, sw = load()
fc0 = fc
base_img = None

def main(push=True):
    global base_img
    W, H = 1240, 900
    base_img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    dr = ImageDraw.Draw(base_img)

    dr.text((40, 24), "中美库存周期：需求与库存同比走势（含四阶段与切换预测）", font=F(25, True), fill=(20, 20, 20))
    dr.text((40, 62), "中国：产成品存货同比 / 营业收入累计同比（统计局，截至2026-09） ｜ "
                      "美国：库存同比 / 销售同比（普查局，截至2026-08）", font=F(14), fill=(110, 110, 110))

    info_cn = panel(ch, dr, "china", CN, "中国", "%", sw, "2023-06", "2027-08")
    info_us = panel(us, dr, "us", US, "美国", "%", sw, "2021-01", "2027-06")

    # y 范围
    def rng(info):
        vals = [v for v in list(info["dem"].values()) + list(info["inv"].values()) if v is not None]
        lo, hi = min(vals + [0]), max(vals)
        pad = (hi - lo) * 0.15 + 1
        return lo - pad, hi + pad
    y0, y1 = rng(info_cn)
    draw_panel(dr, info_cn, 30, 150, W - 60, 290, y0, y1)
    y0, y1 = rng(info_us)
    draw_panel(dr, info_us, 30, 560, W - 60, 290, y0, y1)

    # 底部说明
    dr.text((40, 866), "背景色带=所处阶段（绿=主动补库 橙=被动补库 红=主动去库 蓝=被动去库）｜灰底=预测区｜"
                       "橙框=预测切换窗口 ｜ 红竖线=预计切换月", font=F(13), fill=(120, 120, 120))
    base_img.convert("RGB").save(OUT_PNG, "PNG")
    print("saved", OUT_PNG, base_img.size)

    if not push:
        return

    # 上传 + 推送飞书
    hook = app_secrets.get("feishu.webhook", "")
    app_id = app_secrets.get("feishu.app_id", "")
    app_secret = app_secrets.get("feishu.app_secret", "")
    if not (hook and app_id and app_secret):
        print("[飞书] 未配置 feishu.webhook / app_id / app_secret，跳过推送")
        return
    tok = requests.post(TOKEN_URL, json={"app_id": app_id,
                                         "app_secret": app_secret}, timeout=30).json()
    t = tok.get("tenant_access_token")
    print("token:", "ok" if t else str(tok)[:200])
    with open(OUT_PNG, "rb") as fh:
        up = requests.post(UPLOAD_URL, headers={"Authorization": "Bearer %s" % t},
                           files={"image": ("chart.png", fh, "image/png")},
                           data={"image_type": "message"}, timeout=60).json()
    key = (up.get("data") or {}).get("image_key")
    print("image_key:", key, "" if key else str(up)[:200])
    r = requests.post(hook, json={"msg_type": "image", "content": {"image_key": key}}, timeout=30).json()
    print("[飞书图片]", "发送成功" if r.get("code") == 0 or r.get("StatusCode") == 0 else str(r)[:200])


if __name__ == "__main__":
    main(push="--no-push" not in __import__("sys").argv)