# -*- coding: utf-8 -*-
"""中美库存周期切换预测报告：自包含 HTML（内嵌 SVG）+ 飞书推送。

原三代报告脚本已合并到本文件：
  build_report    旧「中美库存周期变化与阶段预测」报告（已被取代）
  build_report2   修正版报告 + 通用绘图/表格工具函数（panel/phase_legend/bar 等）
  build_report3   「库存周期切换预测报告」+ 飞书交互卡片推送
"""
import json
import os
import sys

import pandas as pd
import requests


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


BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e7d65e97affeGRaKRrqXn281SL"
OUT = BASE + "/库存周期切换预测报告.html"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

PHASE_COLOR = {
    "主动补库存": ("#E7F6EE", "#1F7A4D"),
    "被动补库存": ("#FBEFE3", "#A5561F"),
    "主动去库存": ("#FBEAE6", "#B2472F"),
    "被动去库存": ("#E9F2FB", "#2C6CA0"),
}
CN_COLOR, US_COLOR = "#C0392B", "#0A78C2"


def mkey(m):
    return int(m[:4]) * 12 + int(m[4:]) - 1


def f1(x):
    return "—" if x is None else f"{x:.1f}"


def panel(months, hist_d, hist_i, proj_d, proj_i, phases, proj_start, color, ylabel, unit, title, y_pad=0.12):
    W, H = 940, 300
    L, R, T, B = 62, 66, 34, 40
    PW, PH = W - L - R, H - T - B
    n = len(months)
    xs = [L + (PW * i / (n - 1)) for i in range(n)]

    vals = [v for v in (hist_d + hist_i + proj_d + proj_i) if v is not None]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    lo -= span * y_pad
    hi += span * y_pad
    X = lambda i: xs[i]
    Y = lambda v: T + PH * (1 - (v - lo) / (hi - lo))

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
           f'aria-label="{title}：{ylabel} 月度走势，含四阶段色带与未来数月预测">']
    out.append(f'<text x="{L}" y="20" font-size="13" font-weight="600" fill="#222">{title}</text>')

    i = 0
    while i < n:
        p = phases[i]
        if p is None:
            i += 1
            continue
        j = i
        while j + 1 < n and phases[j + 1] == p:
            j += 1
        x0 = max(L, X(i) - (X(1) - X(0)) / 2 if i > 0 else L)
        x1 = min(W - R, X(j) + (X(1) - X(0)) / 2 if j < n - 1 else W - R)
        out.append(f'<rect x="{x0:.1f}" y="{T}" width="{max(0, x1 - x0):.1f}" height="{PH}" fill="{PHASE_COLOR[p][0]}"/>')
        i = j + 1

    if proj_start is not None:
        xp = X(proj_start)
        out.append(f'<rect x="{xp:.1f}" y="{T}" width="{W - R - xp:.1f}" height="{PH}" fill="#8A8F98" fill-opacity="0.13"/>')

    out.append(f'<rect x="{L}" y="{T}" width="{PW}" height="{PH}" fill="none" stroke="#DDD"/>')
    for gy in range(5):
        yy = T + PH * gy / 4
        out.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{W - R}" y2="{yy:.1f}" stroke="#EFEFEF"/>')
        out.append(f'<text x="{L - 6}" y="{yy + 3:.1f}" font-size="10" fill="#999" text-anchor="end">{hi - (hi - lo) * gy / 4:.0f}</text>')
    out.append(f'<line x1="{L}" y1="{Y(0):.1f}" x2="{W - R}" y2="{Y(0):.1f}" stroke="#C9C9C9"/>')

    if proj_start is not None:
        xp = X(proj_start)
        out.append(f'<line x1="{xp:.1f}" y1="{T}" x2="{xp:.1f}" y2="{T + PH}" stroke="#B9B9B9" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{xp + 5:.1f}" y="{T + 12}" font-size="10" fill="#8A8A8A">预测区</text>')

    out.append(f'<line x1="{L}" y1="{T + PH}" x2="{W - R}" y2="{T + PH}" stroke="#BBB"/>')
    for i2, m in enumerate(months):
        if m[4:] == "01":
            out.append(f'<text x="{X(i2):.1f}" y="{T + PH + 16}" font-size="10" fill="#888" text-anchor="middle">{m[:4]}</text>')

    def seg(vals_, dashed):
        pts = [(X(k), Y(v)) for k, v in enumerate(vals_) if v is not None]
        if len(pts) < 2:
            return
        d = " ".join(f"{'M' if k == 0 else 'L'}{p[0]:.1f},{p[1]:.1f}" for k, p in enumerate(pts))
        da = ' stroke-dasharray="6 4"' if dashed else ""
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{da} stroke-linejoin="round"/>')

    seg(hist_d, False)
    seg(hist_i, False)
    if proj_d:
        j0 = proj_start - 1
        rd = [None] * n
        ri = [None] * n
        rd[j0], ri[j0] = hist_d[j0], hist_i[j0]
        for k, v in enumerate(proj_d, start=1):
            rd[j0 + k] = v
        for k, v in enumerate(proj_i, start=1):
            ri[j0 + k] = v
        seg(rd, True)
        seg(ri, True)

    for k in reversed(range(n)):
        if hist_d[k] is not None:
            out.append(f'<circle cx="{X(k):.1f}" cy="{Y(hist_d[k]):.1f}" r="3.5" fill="{color}"/>')
            out.append(f'<text x="{X(k) + 6:.1f}" y="{Y(hist_d[k]) - 8:.1f}" font-size="10.5" fill="#333">'
                       f'需求 {hist_d[k]:.1f}{unit}</text>')
            break
    for k in reversed(range(n)):
        if hist_i[k] is not None:
            out.append(f'<circle cx="{X(k):.1f}" cy="{Y(hist_i[k]):.1f}" r="3.5" fill="{color}"/>')
            out.append(f'<text x="{X(k) + 6:.1f}" y="{Y(hist_i[k]) + 15:.1f}" font-size="10.5" fill="#444">'
                       f'库存 {hist_i[k]:.1f}{unit}</text>')
            break

    lx = L
    for txt, dash in (("需求（实线）", False), ("库存（虚线）", True)):
        da = ' stroke-dasharray="6 4"' if dash else ""
        out.append(f'<line x1="{lx}" y1="{H - 11}" x2="{lx + 22}" y2="{H - 11}" stroke="{color}" stroke-width="2"{da}/>')
        out.append(f'<text x="{lx + 27}" y="{H - 8}" font-size="11" fill="#555">{txt}</text>')
        lx += 108
    out.append('</svg>')
    return "\n".join(out)


def phase_legend():
    items = "".join(
        f'<span class="lg"><i style="background:{PHASE_COLOR[p][0]};border:1px solid {PHASE_COLOR[p][1]}"></i>{p}</span>'
        for p in ["主动补库存", "被动补库存", "主动去库存", "被动去库存"])
    return f'<div class="legend">{items}</div>'


def bar(v, vmax, color):
    w = 0 if vmax <= 0 else max(2, min(100, 100 * v / vmax))
    return (f'<span class="bar"><i style="width:{w:.1f}%;background:{color}"></i></span><b>{v:.2f}</b>')


def panel_for(base_df, key, unit, color, ylabel, tag):
    fc = json.load(open(BASE + "/data/forecast2.json", encoding="utf-8"))
    last = base_df["month"].iloc[-1]
    ftbl = fc[key]["base"]["table"]
    s_d, s_i = fc[key]["base"]["slope_demand"], fc[key]["base"]["slope_inv"]
    hmonths = list(base_df["month"])
    fmonths = [r["month"] for r in ftbl]
    months = hmonths + fmonths
    d_val = list(base_df["demand"].astype(float))
    i_val = list(base_df["inventory"].astype(float))
    proj_d = [round(d_val[-1] + s_d * k, 2) for k in range(1, len(fmonths) + 1)]
    proj_i = [round(i_val[-1] + s_i * k, 2) for k in range(1, len(fmonths) + 1)]
    hist_d = d_val + [None] * len(fmonths)
    hist_i = i_val + [None] * len(fmonths)
    phases = [p if isinstance(p, str) else None for p in (list(base_df["phase"]) + [r["phase"] for r in ftbl])]
    pi = months.index(fmonths[0])
    cut = 0
    sl = lambda a: a[cut:]
    svg = panel(sl(months), sl(hist_d), sl(hist_i), proj_d, proj_i, sl(phases), pi, color, ylabel, unit,
                f"{tag}库存周期：库存同比 vs 需求同比（含四阶段）")
    # 在内嵌图的图面内标注「当前周期 → 下一周期」
    _sw = _load_switches(BASE + "/data/switches.json")
    _nxt = {"主动补库存": "被动补库存", "被动补库存": "主动去库存",
            "主动去库存": "被动去库存", "被动去库存": "主动补库存"}
    _cur = str(base_df["phase"].iloc[-1])
    _cen = (_sw["countries"].get(key) or {}).get("predicted_switch", {}).get("center", "").replace("-", "")
    _tail = ("（预计 %s-%s 切换）" % (_cen[:4], _cen[4:])) if len(_cen) >= 6 else ""
    _ann = f"当前周期：{_cur}　→　下一周期：{_nxt.get(_cur, '—')}{_tail}"
    svg = svg.replace("</svg>", f'<text x="874" y="20" font-size="12.5" text-anchor="end" fill="#A8240F">{_ann}</text></svg>')
    return {"svg": svg, "last": last, "phase": base_df["phase"].iloc[-1],
            "d": d_val[-1], "i": i_val[-1]}


def mfmt0(m):
    if not m:
        return "—"
    m = m.replace("-", "")
    if len(m) < 6 or not m.isdigit():
        return "—"
    return f"{m[:4]}年{int(m[4:]):d}月"


def main(push=True):
    ch = pd.read_csv(BASE + "/data/china_hard_base.csv", dtype={"month": str})
    us = pd.read_csv(BASE + "/data/us_base.csv", dtype={"month": str})
    sw = _load_switches(BASE + "/data/switches.json")
    fc = json.load(open(BASE + "/data/forecast2.json", encoding="utf-8"))
    gen = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    cn = panel_for(ch, "china", "%", CN_COLOR, "中国同比 %", "中国")
    usc = panel_for(us, "us", "%", US_COLOR, "美国同比 %", "美国")
    # 下一周期名称（按周期序列推进）
    NEXT = {"主动补库存": "被动补库存", "被动补库存": "主动去库存",
            "主动去库存": "被动去库存", "被动去库存": "主动补库存"}
    cn_next = NEXT.get(cn["phase"], "—")
    us_next = NEXT.get(usc["phase"], "—")
    cn_center = mfmt0(sw["countries"]["china"]["predicted_switch"]["center"])
    us_center = mfmt0(sw["countries"]["us"]["predicted_switch"]["center"])
    cn_win = " ~ ".join(mfmt0(x) for x in sw["countries"]["china"]["predicted_switch"]["window"])
    us_win = " ~ ".join(mfmt0(x) for x in sw["countries"]["us"]["predicted_switch"]["window"])
    cn_early = mfmt0(sw["countries"]["china"]["predicted_switch"]["early_case"][0])
    us_early = mfmt0(sw["countries"]["us"]["predicted_switch"]["early_case"][0])

    def mfmt(m):
        return mfmt0(m)

    rows = ""
    for key, nm, cur in (("china", "中国", cn), ("us", "美国", usc)):
        s = sw["countries"][key]
        tgt = s["predicted_switch"]
        cc = PHASE_COLOR[cur["phase"]]
        rows += (f'<tr><td>{nm}</td>'
                 f'<td><span class="pill" style="background:{cc[0]};color:{cc[1]}">{cur["phase"]}</span></td>'
                 f'<td>{s["current"]["demand"]}% / {s["current"]["inventory"]}%</td>'
                 f'<td><b>{mfmt(tgt["center"][:4]+tgt["center"][4:]) if len(tgt["center"])==7 else tgt["center"]}</b></td>'
                 f'<td>{mfmt(tgt["window"][0][:4]+tgt["window"][0][4:])} ~ {mfmt(tgt["window"][1][:4]+tgt["window"][1][4:])}</td>'
                 f'<td>{tgt["target_side"]}</td></tr>')

    trig = ""
    for key, nm in (("china", "中国"), ("us", "美国")):
        s = sw["countries"][key]
        items = "".join(f"<li>{v['desc']}</li>" for v in s["triggers"].values())
        trig += f"<h3>{nm}</h3><ul>{items}</ul>"

    def gap_table(key, fam):
        rs = fc[key]["base"]["table"]
        vmax = max(max(r["gap_restock"] for r in rs), max(r["gap_destock"] for r in rs), 0.1)
        body = ""
        for r in rs:
            cc = PHASE_COLOR[r["phase"]]
            body += (f'<tr><td>{r["month"][:4]}-{r["month"][4:]}</td><td>{f1(r["dma"])}{fam}</td><td>{f1(r["ima"])}{fam}</td>'
                     f'<td>{f1(r["g"])}</td><td><span class="pill" style="background:{cc[0]};color:{cc[1]}">{r["phase"]}</span></td>'
                     f'<td>{bar(r["gap_restock"], vmax, "#1F7A4D")}</td><td>{bar(r["gap_destock"], vmax, "#B2472F")}</td></tr>')
        return ('<table class="tbl"><tr><th>月份</th><th>预测需求</th><th>预测库存</th><th>库销增速差</th>'
                f'<th>预测阶段</th><th>到主动补库存差距</th><th>到主动去库存差距</th></tr>{body}</table>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>库存周期切换预测报告</title>
<style>
  :root {{ --bd:#E6E6E6; --tx:#1a1a1a; --sub:#666; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:26px 20px 40px; background:#FAFAFA; color:var(--tx);
    font-family:-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Source Han Sans SC",sans-serif; line-height:1.6; }}
  .wrap {{ max-width:1000px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 6px; }}
  .meta {{ color:var(--sub); font-size:13px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin:26px 0 10px; padding-left:9px; border-left:3px solid #C0392B; }}
  h3 {{ font-size:13px; margin:14px 0 4px; }}
  .hero {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  .hcard {{ border:1px solid var(--bd); border-left:4px solid #C0392B; border-radius:0 10px 10px 0; padding:14px 16px; background:#fff; }}
  .hcard .t {{ font-size:13px; color:#666; }}
  .hcard .m {{ font-size:20px; font-weight:700; margin:4px 0; }}
  .hcard .s {{ font-size:12.5px; color:#555; }}
  table.tbl {{ width:100%; border-collapse:collapse; font-size:12.5px; margin-top:8px; }}
  table.tbl th, table.tbl td {{ border-bottom:1px solid var(--bd); padding:6px 8px; text-align:left; }}
  table.tbl th {{ background:#F5F5F5; color:#555; font-weight:600; }}
  .pill {{ font-size:12px; font-weight:600; padding:2px 9px; border-radius:20px; }}
  .figure {{ border:1px solid var(--bd); border-radius:10px; background:#fff; padding:12px; margin-top:10px; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:14px; font-size:12px; color:#555; margin:6px 2px 2px; }}
  .lg {{ display:flex; align-items:center; gap:6px; }} .lg i {{ width:14px; height:12px; border-radius:2px; display:inline-block; }}
  .bar {{ display:inline-block; vertical-align:middle; width:70px; height:9px; background:#F0F0F0; border-radius:5px; margin-right:6px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; }} .bar + b {{ font-size:12px; }}
  .note {{ font-size:12.5px; color:#555; background:#fff; border:1px solid var(--bd); border-radius:10px; padding:14px 16px; }}
  .note li {{ margin:4px 0; }}
  ul {{ margin:4px 0; padding-left:20px; }}
  footer {{ color:#999; font-size:11.5px; margin-top:22px; text-align:center; }}
</style></head>
<body><div class="wrap">
  <h1>中美库存周期切换预测报告</h1>
  <div class="meta">口径：库存同比 vs 需求同比（中国：产成品存货同比 / 营业收入累计同比；美国：库存同比 / 销售同比）｜
    中国数据截至 {cn['last'][:4]}-{cn['last'][4:]}，美国数据截至 {usc['last'][:4]}-{usc['last'][4:]}｜生成：{gen}</div>

  <h2>一、当前周期与下一周期（切换月份预测）</h2>
  <div class="hero">
    <div class="hcard"><div class="t">中国 · 当前周期：<b>{cn['phase']}</b></div>
      <div class="m">下一周期：{cn_next}</div>
      <div class="s">预计切换月 <b>{cn_center}</b>｜预测窗口 {cn_win}｜提前情景 {cn_early} 前后</div></div>
    <div class="hcard"><div class="t">美国 · 当前周期：<b>{usc['phase']}</b></div>
      <div class="m">下一周期：{us_next}</div>
      <div class="s">预计切换月 <b>{us_center}</b>｜预测窗口 {us_win}｜提前情景 {us_early} 前后</div></div>
  </div>
  <table class="tbl"><tr><th>国家</th><th>当前周期</th><th>下一周期</th><th>需求%/库存%</th><th>预计切换月</th><th>预测窗口</th></tr>
    <tr><td>中国</td>
      <td><span class="pill" style="background:{PHASE_COLOR[cn['phase']][0]};color:{PHASE_COLOR[cn['phase']][1]}">{cn['phase']}</span></td>
      <td><b>{cn_next}</b></td><td>{sw['countries']['china']['current']['demand']}% / {sw['countries']['china']['current']['inventory']}%</td>
      <td><b>{cn_center}</b></td><td>{cn_win}</td></tr>
    <tr><td>美国</td>
      <td><span class="pill" style="background:{PHASE_COLOR[usc['phase']][0]};color:{PHASE_COLOR[usc['phase']][1]}">{usc['phase']}</span></td>
      <td><b>{us_next}</b></td><td>{sw['countries']['us']['current']['demand']}% / {sw['countries']['us']['current']['inventory']}%</td>
      <td><b>{us_center}</b></td><td>{us_win}</td></tr>
  </table>
  <div class="note" style="margin-top:10px">预测方法：以「需求动量（3 个月平滑后的 3 个月变化）趋零」判定需求见顶，库存通常滞后需求 3–6 个月见顶；库存同比见顶即视为库存周期开始变化（由补库转向去库）。中国：需求动量自 2026-04 的 +2.33 回落至 +0.83（趋零），库存动量仍在 +2.80（偏强），故基准略晚于美国。美国：销售同比动量自 2026-05 的 +4.59 快速衰减至 +0.64（近 3 个月斜率约 −1.32/月），需求见顶在先。</div>

  <h2>二、变化触发线（用于自动监测）</h2>
  {trig}

  <h2>三、中国库存周期变化图</h2>
  <div class="figure">{cn['svg']}{phase_legend()}</div>
  <h2>四、美国库存周期变化图</h2>
  <div class="figure">{usc['svg']}{phase_legend()}</div>

  <h2>五、未来数月差距预估</h2>
  <h3>中国</h3>{gap_table('china','pct')}
  <h3>美国</h3>{gap_table('us','pct')}

  <footer>数据来源：国家统计局（规模以上工业企业财务指标）、美国普查局（经 FRED）｜本页自包含、可离线打开</footer>
</div></body></html>
"""
    open(OUT, "w", encoding="utf-8").write(html)
    print("written:", OUT, len(html))

    if not push:
        return

    # ---- 推送飞书（一条交互式卡片：图表图片 + 报告要点）----
    hook = app_secrets.get("feishu.webhook", "")
    if not hook:
        print("[飞书] 未配置 feishu.webhook")
        return
    # 先按最新数据重绘图表（保证图片与报告同源），再上传取 image_key
    import render_chart
    render_chart.main(push=False)
    t = None
    app_id = app_secrets.get("feishu.app_id", "")
    app_secret = app_secrets.get("feishu.app_secret", "")
    if app_id and app_secret:
        tok = requests.post(TOKEN_URL, json={"app_id": app_id,
                                             "app_secret": app_secret}, timeout=30).json()
        t = tok.get("tenant_access_token")
    else:
        print("[飞书] 未配置 feishu.app_id/app_secret，跳过图片上传")
    img_key = None
    if t:
        with open(BASE + "/中美库存周期图表.png", "rb") as fh:
            up = requests.post(UPLOAD_URL, headers={"Authorization": "Bearer %s" % t},
                               files={"image": ("chart.png", fh, "image/png")},
                               data={"image_type": "message"}, timeout=60).json()
        img_key = (up.get("data") or {}).get("image_key")
    cg, ug = fc["china"]["base"], fc["us"]["base"]
    cn_sc, us_sc = sw["countries"]["china"], sw["countries"]["us"]

    def _trig(key):
        return "；".join(v["desc"] for v in sw["countries"][key]["triggers"].values())

    def _gap_lines(key, nm, dname, iname):
        rs = fc[key]["base"]["table"]
        rows = [f"{r['month'][:4]}-{r['month'][4:]}　{dname} {r['dma']:.1f}% / {iname} {r['ima']:.1f}%　→　{r['phase']}"
                for r in rs]
        return nm + "：\n" + "\n".join(rows)

    sec1 = ("**一、当前周期与下一周期**\n"
            f"**中国** 当前：**{cn['phase']}**　→　下一周期：**{cn_next}**\n"
            f"预计切换月 **{cn_center}**（窗口 {cn_win}；提前情景 {cn_early} 前后）\n"
            f"存货同比 {cn_sc['current']['inventory']}% / 营收同比 {cn_sc['current']['demand']}%"
            f"　｜　距主动补库存 {cg['cur_gap_restock']:.2f}pct，距主动去库存 {cg['cur_gap_destock']:.2f}pct\n\n"
            f"**美国** 当前：**{usc['phase']}**　→　下一周期：**{us_next}**\n"
            f"预计切换月 **{us_center}**（窗口 {us_win}；提前情景 {us_early} 前后）\n"
            f"库存同比 {us_sc['current']['inventory']}% / 销售同比 {us_sc['current']['demand']}%"
            f"　｜　距主动去库存 {ug['cur_gap_destock']:.2f}pct")
    sec2 = ("**二、变化触发线（自动监测口径）**\n"
            f"中国：{_trig('china')}\n"
            f"美国：{_trig('us')}")
    sec3 = ("**三、未来数月差距预估（基准情景）**\n"
            + _gap_lines("china", "中国", "需求", "库存") + "\n"
            + _gap_lines("us", "美国", "销售", "库存"))
    elements = []
    if img_key:
        elements.append({"tag": "img", "img_key": img_key, "alt": {"tag": "plain_text", "content": "中美库存周期图表"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": sec1}})
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": sec2}})
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": sec3}})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content":
        "数据来源：国家统计局（规模以上工业企业财务指标）、美国普查局（经 FRED）｜"
        "口径：库存同比 vs 需求同比｜本报告由 DuMate 库存周期监测自动生成"}]})
    card = {"config": {"wide_screen_mode": True},
            "header": {"template": "red", "title": {"tag": "plain_text",
                      "content": f"中美库存周期切换预测报告 | 中国截至 {cn['last'][:4]}-{cn['last'][4:]}、美国截至 {usc['last'][:4]}-{usc['last'][4:]} | 来自 DoMate"}},
            "elements": elements}
    r = requests.post(hook, json={"msg_type": "interactive", "card": card}, timeout=30)
    res = r.json()
    print("[飞书] 响应:", str(res)[:300], "| image_key:", img_key)


if __name__ == "__main__":
    main(push="--no-push" not in sys.argv)