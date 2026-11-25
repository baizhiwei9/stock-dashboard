# -*- coding: utf-8 -*-
"""渲染单个自包含 HTML：上篇 A股五轨 · 下篇 美股三轨 期权风险释放监控日报"""
import os, json, math, datetime


def _fmt(yyyymmdd):
    """把 20261021 归一化为 2026-10-21；非 8 位原样返回"""
    s = str(yyyymmdd or "")
    return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8]) if len(s) == 8 else (s or "待更新")


HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
F = json.load(open(os.path.join(D, "final.json"), encoding="utf-8"))
ASOF = str(F.get("asof") or "20261021")
ASOFD = _fmt(ASOF)
_W = F.get("window") or []
WIN0 = _fmt(_W[0]) if len(_W) >= 2 else ASOFD
WIN1 = _fmt(_W[1]) if len(_W) >= 2 else ASOFD
GEND = datetime.date.today().strftime("%Y-%m-%d")
_u = F.get("us") or {}
try:
    _st = json.load(open(os.path.join(D, "data_status.json"), encoding="utf-8"))
    DATANOTE = str(_st.get("line") or "").replace("**", "")
except Exception:
    DATANOTE = "本次数据时点：待判定"


def _ud(k, f):
    return ((_u.get(k) or {}).get("vals") or {}).get(f)


UIDX = "、".join("%s %s" % (_ud(k, "idx") or k, _ud(k, "idx_last_date") or "待更新")
                 for k in ("SPX", "NDX", "RUT") if k in _u)
UETF = "、".join("%s %s" % (_ud(k, "etf") or k, _ud(k, "etf_last_date") or "待更新")
                 for k in ("SPX", "NDX", "RUT") if k in _u)
UCHAIN = str(_ud("SPX", "chain_ts") or "待更新")
OUT = os.path.join(HERE, "中美期权风险释放汇总_A股五轨_美股三轨_含波动率预警与5日预测_%s.html" % ASOF)

A_ORDER = ["300ETF", "500ETF", "KC50", "CYB", "ZZ1000"]
U_ORDER = ["SPX", "NDX", "RUT"]
A_COLS = ["300ETF期权<br><span class='sub'>510300 · 深159919备注</span>",
          "中证500ETF期权<br><span class='sub'>510500</span>",
          "科创50ETF期权<br><span class='sub'>588000 · 588080备注</span>",
          "创业板ETF期权<br><span class='sub'>159915易方达</span>",
          "中证1000股指期权<br><span class='sub'>MO主力+次月+当季 · IM基差<br><b>期货期权替代</b></span>"]
U_COLS = ["标普500<br><span class='sub'>VIX / SPY</span>",
          "纳斯达克100<br><span class='sub'>VXN / QQQ</span>",
          "罗素2000（小盘）<br><span class='sub'>RVX / IWM</span>"]

CHIP = {"🟢": ("safe", "🟢 绿灯 · 安全基线"),
        "🟡观察": ("watch", "🟡 观察 · 未达升级阈值"),
        "🟡Lv1": ("lv1", "🟡 黄灯 Lv1"),
        "🟡Lv2": ("lv2", "🟡 黄灯 Lv2"),
        "🔴": ("danger", "🔴 红灯")}


def chip(light):
    cls, txt = CHIP[light]
    return "<span class='chip %s'>%s</span>" % (cls, txt)


VOL_CHIP = {"🔴": ("danger", "🔴 红灯"), "🟡": ("watch", "🟡 偏高"), "🟢": ("safe", "🟢 正常")}


def vol_chip(level):
    cls, txt = VOL_CHIP.get(level or "🟢", ("safe", "🟢 正常"))
    return "<span class='chip %s'>%s</span>" % (cls, txt)


A_NAMES = {"300ETF": "沪深300ETF期权", "500ETF": "中证500ETF期权", "KC50": "科创50ETF期权",
           "CYB": "创业板ETF期权", "ZZ1000": "中证1000(MO)"}
U_NAMES = {"SPX": "标普500", "NDX": "纳斯达克100", "RUT": "罗素2000"}


def num(x, d=2, suf="", plus=False):
    if x is None:
        return "待更新"
    s = ("%+.*f" if plus else "%.*f") % (d, x)
    return s + suf


def spark(vals, dates, color="#c0392b"):
    if not vals:
        return ""
    w, h, pad = 260.0, 54.0, 6.0
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = pad + (w - 2 * pad) * (i / max(n - 1, 1))
        y = h - pad - (h - 2 * pad) * ((v - lo) / rng)
        pts.append("%.1f,%.1f" % (x, y))
    lastx, lasty = pts[-1].split(",")
    return ("<svg class='spark' viewBox='0 0 %.0f %.0f' preserveAspectRatio='none' role='img'>"
            "<polyline points='%s' fill='none' stroke='%s' stroke-width='1.6'/>"
            "<circle cx='%s' cy='%s' r='2.4' fill='%s'/>"
            "<text x='%.1f' y='10' font-size='9' fill='#8a8f98'>高 %.2f</text>"
            "<text x='%.1f' y='%.1f' font-size='9' fill='#8a8f98'>低 %.2f</text></svg>"
            % (w, h, " ".join(pts), color, lastx, lasty, color,
               pad, hi, pad, h - 2, lo))


def mark(ok, text):
    if ok is True:
        return "<li class='ok'><b>✓</b> %s</li>" % text
    if ok is False:
        return "<li class='no'><b>✗</b> %s</li>" % text
    return "<li class='unk'><b>○</b> %s</li>" % text


# ============================== HTML ==============================
a, u = F["ashare"], F["us"]


# 灯色/命中/释放 汇总表
def a_lights():
    out = ["<table class='grid tight'><thead><tr><th class='rk'>判灯明细</th>%s</tr></thead><tbody>"
           % "".join("<th class='tgt'>%s</th>" % c.split("<br>")[0] for c in A_COLS)]

    def r(label, fn, cls=""):
        out.append("<tr%s><th>%s</th>%s</tr>"
                   % (" class='%s'" % cls if cls else "", label,
                      "".join("<td>%s</td>" % fn(a[k]) for k in A_ORDER)))

    r("灯色", lambda r: chip(r["light"]))
    r("🟢安全基线（5项）", lambda r: "<b>%d/5</b> 满足" % r["nb"])
    r("🟡Lv1 命中（需≥2项才升级）", lambda r: ("<b>%d</b>/4" % r["n1"])
      + ("<span class='flag'>已升级</span>" if r["n1"] >= 2 else ""))
    r("🟡Lv2 命中", lambda r: "%d条" % r["n2"])
    r("🔴红灯条件命中", lambda r: ("<b class='bad'>%d</b>项" % r["nr"]) if r["nr"] else "0项")
    r("释放进度（四项）", lambda r: "<b>%d/4</b>" % r["nrel"], cls="bord")
    r("输出结论", lambda r: r["light_why"], cls="why")
    out.append("</tbody></table>")
    return "".join(out)


def a_table_ok():
    """A股主表：字段数值 + 灯色行"""
    out = ["<table class='grid'><thead><tr><th class='rk'>字段</th>%s</tr></thead><tbody>"
           % "".join("<th class='tgt'>%s</th>" % c for c in A_COLS)]

    def r(label, fn, cls=""):
        out.append("<tr%s><th>%s</th>%s</tr>"
                   % (" class='%s'" % cls if cls else "", label,
                      "".join("<td>%s</td>" % fn(k, a[k]["vals"]) for k in A_ORDER)))

    r("① 30日隐波 VIX(%)",
      lambda k, v: "%.2f%s" % (v["vix"], " <span class='flag'>自算MO ATM-IV</span>" if k == "ZZ1000" else ""))
    r("② VIX 252日分位", lambda k, v: "<b>%.1f%%</b>%s"
      % (v["vix_pct"], "<span class='flag'>高于70%</span>" if v["vix_pct"] > 70 else ""))
    r("③ 近月IV/次月IV 比值 R",
      lambda k, v: "<span class='%s'>%.3f</span>%s" % ("bad" if v["R"] > 1 else "good", v["R"],
                                                       "<span class='flag'>近端倒挂</span>" if v["R"] > 1 else ""))
    r("④ SKEW（25Δ风险逆转）",
      lambda k, v: "%+.4f<br><span class='sub'>自身252日中位 %+.4f</span>" % (v["rr"], v["rr_med"]))
    r("⑤ IV−HV20 隐历差(pct)", lambda k, v: "<b>%+.2f</b>%s"
      % (v["ivhv"], "<span class='flag'>尾部溢价未消化</span>" if v["ivhv"] > 5 else ""))
    r("⑥ 持仓PCR（持仓口径）",
      lambda k, v: "%.3f<br><span class='sub'>近3日 %s</span>" % (v["pcr"], "→".join("%.3f" % x for x in v["pcr3"])))
    r("⑦ IM 各月基差年化 / 远月贴水Z", lambda k, v: (
        "当月 %.2f%% ｜ 次月 %.2f%% ｜ 当季 %.2f%%<br><span class='sub'>远月贴水Z = %+.2f（n=252）</span>"
        % (v["im"]["b1"], v["im"]["b2"], v["im"]["b3"], v["im"]["z_far"]))
      if v["im"] else "<span class='unkc'>— （仅1000适用）</span>")
    r("⑧ GEX代理分位（逐档OI×Γ加总）", lambda k, v: "<b>%.1f%%</b>%s" % (
        v["gex_pct"], "<span class='flag'>负Gamma</span>" if v["gex_pct"] < 20
        else ("<span class='flag'>偏低象限</span>" if v["gex_pct"] <= 40 else "")))
    r("⑨ |现价−GammaFlip|/现价",
      lambda k, v: ("%.2f%%<br><span class='sub'>Flip %.3f ｜ 现价 %.3f ｜ 档距 %.2f%% ｜ <b>%s</b></span>"
                    % (v["flip_dist"], v["flip"], v["spot"], v["grid_pct"], v["flip_state"]))
      if isinstance(v.get("flip_dist"), (int, float)) else
      "<span class='unkc'>待更新</span><br><span class='sub'>Flip 线未形成（净Gamma无由负转正的行权价）</span>")
    r("⑩ 期权波动率预警（独立一维，与判灯并列）",
      lambda k, v: "%s<br><span class='sub'>%s</span>"
      % (vol_chip((a[k].get("vol") or {}).get("level")), (a[k].get("vol") or {}).get("why", "")))
    r("灯色", lambda k, v: chip(a[k]["light"]), cls="bord")
    r("🔴红灯条件命中", lambda k, v: ("<b class='bad'>%d项</b>" % a[k]["nr"]) if a[k]["nr"] else "0项")
    r("释放进度", lambda k, v: "<b>%d/4</b>" % a[k]["nrel"])
    out.append("</tbody></table>")
    return "".join(out)


def cards(reg, order, spark_color):
    out = []
    for k in order:
        r = reg[k]
        v = r["vals"]
        base = "".join(mark(ok, "%s <span class='sub'>%s</span>" % (lab, ev)) for lab, ok, ev in r["baseline"])
        lv1 = "".join(mark(ok, "%s <span class='sub'>%s</span>" % (lab, ev)) for lab, ok, ev in r["lv1"])
        lv2 = "".join(mark(ok, "%s <span class='sub'>%s</span>" % (lab, ev)) for lab, ok, ev in r["lv2"]) or \
              "<li class='unk'><b>○</b> 本标的不适用/未触发</li>"
        red = "".join(mark(ok, "%s <span class='sub'>%s</span>" % (lab, ev)) for lab, ok, ev in r["red"])
        if r.get("suspect"):
            red += "".join("<li class='unk'><b>○</b> %s 方向不可分辨，待确认</li>" % s for s in r["suspect"])
        rel = "".join(mark(ok, "%s <span class='sub'>%s</span>" % (lab, ev)) for lab, ok, ev in r["rel"])
        sp = spark(r["spark"], r["spark_date"], spark_color)
        out.append("""
<article class="card %(cls)s">
  <header>
    <div><h3>%(label)s</h3><div class="sub">%(note)s</div></div>
    <div class="rt">%(chip)s<div class="sub">释放 %(nrel)d/4 ｜ 基线 %(nb)d/5</div></div>
  </header>
  <div class="why">%(why)s</div>
  <div class="cols">
    <div><h4>🟢 五项安全基线</h4><ul class="chk">%(base)s</ul></div>
    <div><h4>🟡 Lv1 条件（≥2项才升级）</h4><ul class="chk">%(lv1)s</ul>
         <h4>🟡 Lv2 条件</h4><ul class="chk">%(lv2)s</ul></div>
    <div><h4>🔴 红灯条件</h4><ul class="chk">%(red)s</ul></div>
    <div><h4>✅ 风险释放四项</h4><ul class="chk">%(rel)s</ul></div>
  </div>
  <div class="foot">
    <div><b>当前卡在哪：</b>%(stuck)s</div>
    <div><b>下次最先翻色观察项：</b>%(nxt)s</div>
    <div class="spk">%(sp)s<span class="sub">近60个交易日隐波轨迹（%(spd0)s → %(spd1)s）</span></div>
  </div>
</article>""" % dict(cls=CHIP[r["light"]][0], label=r["label"], note=r["note"], chip=chip(r["light"]),
                     nrel=r["nrel"], nb=r["nb"], why=r["light_why"], base=base, lv1=lv1, lv2=lv2,
                     red=red, rel=rel, stuck=r["stuck"], nxt=r["next_watch"], sp=sp,
                     spd0=(r["spark_date"][0] if r["spark_date"] else "—"),
                     spd1=(r["spark_date"][-1] if r["spark_date"] else "—")))
    return "".join(out)


def u_table():
    out = ["<table class='grid'><thead><tr><th class='rk'>字段</th>%s</tr></thead><tbody>"
           % "".join("<th class='tgt'>%s</th>" % c for c in U_COLS)]

    def r(label, fn, cls=""):
        out.append("<tr%s><th>%s</th>%s</tr>"
                   % (" class='%s'" % cls if cls else "", label,
                      "".join("<td>%s</td>" % fn(k, u[k]["vals"]) for k in U_ORDER)))

    r("① CBOE 波动率指数", lambda k, v: "<b>%s %.2f</b><br><span class='sub'>%s 收盘（%s 252日区间 %.2f–%.2f）</span>"
      % (v["idx"], v["idx_last"], v["idx_last_date"], v["idx"],
         v["idx_min252"], v["idx_max252"]))
    r("② 该指数 252日分位", lambda k, v: "<b>%.1f%%</b>" % v["idx_pct"]
      + ("<span class='flag'>高于70%</span>" if v["idx_pct"] > 70 else ""))
    r("③ 近月IV/次月IV 比值 R（链内）",
      lambda k, v: "<span class='%s'>%.3f</span><br><span class='sub'>近月%s %.2f ｜ 次月%s %.2f</span>"
      % ("bad" if v["R"] > 1 else "good", v["R"], v["near"], v["iv_near"], v["next"], v["iv_next"]))
    r("④ SKEW 偏度", lambda k, v: (
        "<b>CBOE SKEW %.2f</b><br><span class='sub'>自身252日中位 %.2f ｜ 中位+1σ %.2f ｜ 分位 %.1f%%</span>"
        % (v["skew"]["val"], v["skew"]["med"], v["skew"]["med"] + v["skew"]["sd"], v["skew"]["pct"]))
      if v.get("skew") else
      "<span class='unkc'>待更新</span><br><span class='sub'>CBOE 未单独发布该指数偏度；链内25Δrr = %.2f 可参照</span>" % v["rr"])
    r("⑤ IV−HV20 隐历差(pct)", lambda k, v: "<b>%+.2f</b>%s"
      % (v["ivhv"], "<span class='flag'>尾部溢价未消化</span>" if v["ivhv"] > 5 else ""))
    r("⑥ 持仓PCR（持仓口径）", lambda k, v: "%.3f<br><span class='sub'>链内全部到期日合计</span>" % v["pcr"])
    r("⑦ IM 各月基差 / 远月贴水Z", lambda k, v: "<span class='unkc'>—（A股专用字段）</span>")
    r("⑧ GEX代理分位（逐档OI×Γ加总）", lambda k, v:
      "<span class='unkc'>待更新</span><br><span class='sub'>缺链历史；当日归一化净Gamma %+.2e%s</span>"
      % (v["gex_norm"], "（净负Gamma）" if v["gex_norm"] < 0 else ""))
    r("⑨ |现价−GammaFlip|/现价",
      lambda k, v: ("%.2f%%<br><span class='sub'>Flip %.1f ｜ 现价 %.2f ｜ 档距 %.2f%% ｜ <b>%s</b></span>"
                    % (v["flip_dist"], v["flip"], v["chain_spot"], v["grid_pct"], v["flip_state"]))
      if isinstance(v.get("flip_dist"), (int, float)) else
      "<span class='unkc'>待更新</span><br><span class='sub'>Flip 线未形成</span>")
    r("⑩ 期限结构与VVIX（美股补充）",
      lambda k, v: ("VIX9D %.2f &lt; VIX %.2f &lt; VIX3M %.2f（正常Contango）<br>"
                    "<span class='sub'>VVIX %.2f（波动率的波动率，自身高位区）</span>"
                    % (v["term"]["vix9d"], v["idx_last"], v["term"]["vix3m"], US_VVIX))
      if v.get("term") else "<span class='unkc'>—（仅标普500口径发布）</span>")
    r("⑪ 标的现价 / 对应ETF", lambda k, v: "%.2f / %s %.2f<br><span class='sub'>ETF %s 收盘</span>"
      % (v["chain_spot"], v["etf"], v["etf_last"], v["etf_last_date"]))
    r("⑫ 期权波动率预警（独立一维，与判灯并列）",
      lambda k, v: "%s<br><span class='sub'>%s</span>"
      % (vol_chip((u[k].get("vol") or {}).get("level")), (u[k].get("vol") or {}).get("why", "")))
    r("灯色", lambda k, v: chip(u[k]["light"]), cls="bord")
    r("🔴红灯条件命中", lambda k, v: ("<b class='bad'>%d项</b>" % u[k]["nr"]) if u[k]["nr"] else "0项")
    r("释放进度", lambda k, v: "<b>%d/4</b>" % u[k]["nrel"])
    out.append("</tbody></table>")
    return "".join(out)


US_VVIX = float([x for x in json.load(open(os.path.join(D, "us_VVIX.json"), encoding="utf-8"))][-1]["VVIX"])


def vol_banner():
    """期权波动率预警前置横幅（与飞书卡片同一份 vol_summary）"""
    VS = F.get("vol_summary") or {}
    vsa, vsu = VS.get("ashare") or {}, VS.get("us") or {}
    na, nu = vsa.get("n_red") or 0, vsu.get("n_red") or 0
    tot = na + nu

    def dm(vs):
        return {d["key"]: d["why"] for d in (vs.get("detail") or [])}

    rows = []
    if tot:
        rows.append("<div class='vb-head vred'>⚠️ 期权波动率预警 · 共 %d 个红灯（已前置提示）</div>" % tot)
    else:
        rows.append("<div class='vb-head vok'>✅ 期权波动率预警 · 全部标的无红灯</div>")
    for tag, vs, names in (("A股五轨", vsa, A_NAMES), ("美股三轨", vsu, U_NAMES)):
        reds = vs.get("red") or []
        if reds:
            d = dm(vs)
            rows.append("<div><span class='vred'>🔴 %s · %d 个红灯：%s</span></div>"
                        % (tag, len(reds), "；".join("%s（%s）" % (names.get(k, k), d.get(k, ""))
                                                    for k in reds)))
        else:
            rows.append("<div><span class='vok'>🟢 %s · 0 个红灯</span></div>" % tag)
    y = []
    if vsa.get("yellow"):
        y.append("A股 " + "、".join(A_NAMES.get(k, k) for k in vsa["yellow"]))
    if vsu.get("yellow"):
        y.append("美股 " + "、".join(U_NAMES.get(k, k) for k in vsu["yellow"]))
    if y:
        rows.append("<div><span class='vamber'>🟡 波动率偏高（未达红灯）：%s</span></div>" % "　｜　".join(y))
    cls = "volbanner red" if tot else "volbanner"
    return "<div class='%s'>%s</div>" % (cls, "".join(rows))


VOLBAN = vol_banner()


def vol_table():
    """期权波动率预警逐标的明细表（A股五轨 + 美股三轨）"""
    out = ["<table class='grid tight'><thead><tr><th class='rk'>波动率预警 · 标的</th>"
           "<th>波动率灯</th><th>判定依据（红灯优先，其次黄灯）</th></tr></thead><tbody>"]
    for reg, order, names in ((a, A_ORDER, A_NAMES), (u, U_ORDER, U_NAMES)):
        for k in order:
            vol = reg[k].get("vol") or {}
            out.append("<tr><th>%s</th><td>%s</td><td>%s</td></tr>"
                       % (names.get(k, k), vol_chip(vol.get("level")), vol.get("why", "")))
    out.append("</tbody></table>")
    return "".join(out)


VOLTAB = vol_table()

# ============ 未来 5 个交易日波动率预测（OU/AR(1) + EWMA 参照） ============
VF = json.load(open(os.path.join(D, "vol_forecast.json"), encoding="utf-8"))
VT = VF.get("targets") or {}
# 与 judge.py VOL_RULES["A"]["abs_red"] 保持同一份阈值，用于可外推项的预判
ABS_RED = {"KC50": (42.0, "VIX"), "CYB": (34.0, "VIX"), "ZZ1000": (28.0, "ATM-IV")}


def vf(k):
    return VT.get(k) or {}


def _fcf(k, h):
    t = vf(k)
    fc = t.get("forecast") or []
    if not t.get("ok") or len(fc) < h:
        return "<span class='unkc'>待更新</span>"
    d = fc[h - 1]
    return ("<b>%.2f</b><br><span class='sub'>%s<br>80%%：%.1f–%.1f</span>"
            % (d["point"], d.get("date_fmt") or "待更新", d["lo"], d["hi"]))


def fc_table(order, names):
    out = ["<table class='grid'><thead><tr><th class='rk'>标的</th><th>最新隐波（数据日）</th>"
           + "".join("<th>t+%d</th>" % h for h in range(1, 6)) + "</tr></thead><tbody>"]
    for k in order:
        t = vf(k)
        if not t.get("ok"):
            out.append("<tr><th>%s</th><td colspan='5'><span class='unkc'>数据不足，待更新</span></td></tr>"
                       % names.get(k, k))
            continue
        out.append("<tr><th>%s</th><td><b>%s</b><br><span class='sub'>%s ｜ %s</span></td>%s</tr>"
                   % (names.get(k, k), num(t.get("last_vol")), _fmt(t.get("last_date")),
                      t.get("vol_name") or "",
                      "".join("<td>%s</td>" % _fcf(k, h) for h in range(1, 6))))
    out.append("</tbody></table>")
    return "".join(out)


def fc_param_table(order, names):
    out = ["<table class='grid tight'><thead><tr><th class='rk'>标的</th><th>最新隐波（数据日）</th>"
           "<th>252日长期均值</th><th>回归系数 φ（回复速度）</th>"
           "<th>EWMA 实现波动率锚（λ=0.94）</th><th>残差 σ_ln</th></tr></thead><tbody>"]
    for k in order:
        t = vf(k)
        if not t.get("ok"):
            out.append("<tr><th>%s</th><td colspan='5'><span class='unkc'>待更新</span></td></tr>" % names.get(k, k))
            continue
        out.append("<tr><th>%s</th><td>%s<br><span class='sub'>%s ｜ %s ｜ n=%s</span></td>"
                   "<td>%s</td><td>%.3f</td><td>%s</td><td>%.4f</td></tr>"
                   % (names.get(k, k), num(t.get("last_vol")), _fmt(t.get("last_date")),
                      t.get("vol_name") or "", t.get("n"), num(t.get("long_mean")),
                      t.get("phi") or 0.0, num(t.get("hv_ewma")), t.get("sigma_log") or 0.0))
    out.append("</tbody></table>")
    return "".join(out)


def _abs_verdict(k, p):
    """绝对水位口径预判：区分当前是否已在该口径触发，避免把“本未触发”写成“红灯缓解”"""
    thr, unit = ABS_RED[k]
    cur = vf(k).get("last_vol")
    above_now = isinstance(cur, (int, float)) and cur > thr
    above_fc = p > thr
    if above_now and above_fc:
        return "<b class='bad'>仍 ≥ %g</b>（%s %.2f，该口径红灯延续）" % (thr, unit, p)
    if above_now and not above_fc:
        return "<span class='good'>回落至 %g 以下</span>（%s %.2f，该口径红灯缓解）" % (thr, unit, p)
    if (not above_now) and above_fc:
        return "<b class='bad'>升破 %g</b>（%s %.2f，该口径红灯风险上升）" % (thr, unit, p)
    return "<span class='good'>仍 &lt; %g</span>（%s %.2f，该口径未触发）" % (thr, unit, p)


def fc_verdict_table(order, names, title):
    rows = []
    for k in order:
        t = vf(k)
        fc = t.get("forecast") or []
        if not t.get("ok") or not fc:
            rows.append("<tr><th>%s</th><td colspan='3'><span class='unkc'>待更新</span></td></tr>" % names.get(k, k))
            continue
        p1, p5 = fc[0]["point"], fc[-1]["point"]
        lm = t.get("long_mean")
        if k in ABS_RED:
            cells = "<td>%s</td><td>%s</td>" % (_abs_verdict(k, p1), _abs_verdict(k, p5))
        else:
            cells = ("<td colspan='2'><span class='unkc'>无绝对水位阈值（仅分位/隐历差口径，不可外推）"
                     "</span></td>")
        if isinstance(lm, (int, float)):
            vs = "高于" if p5 > lm else "低于"
            vs_txt = "t+5 预测 %.2f %s自身长期均值 %s" % (p5, vs, num(lm))
        else:
            vs_txt = "<span class='unkc'>长期均值待更新</span>"
        rows.append("<tr><th>%s</th>%s<td>%s</td></tr>" % (names.get(k, k), cells, vs_txt))
    return ("<table class='grid tight'><thead><tr><th class='rk'>%s</th><th>t+1 预判（绝对水位）</th>"
            "<th>t+5 预判（绝对水位）</th><th>相对自身长期均值</th></tr></thead><tbody>%s</tbody></table>"
            % (title, "".join(rows)))


FC_A_PAR = fc_param_table(A_ORDER, A_NAMES)
FC_U_PAR = fc_param_table(U_ORDER, U_NAMES)
FC_A_TAB = fc_table(A_ORDER, A_NAMES)
FC_U_TAB = fc_table(U_ORDER, U_NAMES)
FC_A_VER = fc_verdict_table(A_ORDER, A_NAMES, "A股五轨")
FC_U_VER = fc_verdict_table(U_ORDER, U_NAMES, "美股三轨")
FC_METHOD = VF.get("method") or "OU(log-IV AR1) + EWMA实现波动率参照"
FC_GEN = VF.get("generated") or GEND

a_sum_rows = "".join("<tr><th>%s</th><td>%s</td></tr>" % (c.split("<br>")[0], a[k]["light_why"]) for k, c in zip(A_ORDER, A_COLS))
u_sum_rows = "".join("<tr><th>%s</th><td>%s</td></tr>" % (c.split("<br>")[0], u[k]["light_why"]) for k, c in zip(U_ORDER, U_COLS))
a_where = "".join("<tr><th>%s</th><td>%s</td></tr>" % (c.split("<br>")[0], a[k]["stuck"]) for k, c in zip(A_ORDER, A_COLS))
u_where = "".join("<tr><th>%s</th><td>%s</td></tr>" % (c.split("<br>")[0], u[k]["stuck"]) for k, c in zip(U_ORDER, U_COLS))
a_next = "".join("<tr><th>%s</th><td>%s</td></tr>" % (c.split("<br>")[0], a[k]["next_watch"]) for k, c in zip(A_ORDER, A_COLS))
u_next = "".join("<tr><th>%s</th><td>%s</td></tr>" % (c.split("<br>")[0], u[k]["next_watch"]) for k, c in zip(U_ORDER, U_COLS))

# ---------------- 分位阈值（A股 5 标的 VVIX 已按用户口径停用，不再输出该章节） ----------------
try:
    _SK = json.load(open(os.path.join(D, "skew_pct_summary.json"), encoding="utf-8"))
except Exception:
    _SK = {}
SKEW_THR = float(_SK.get("thr", 90) or 90)

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中美期权风险释放汇总 · A股五轨 · 美股三轨 · 含波动率预警与5日预测 · @ASOFD@</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#22252b;--sub:#757b86;--line:#e6e3dd;
 --red:#c0392b;--amber:#c98a12;--green:#2e8b57;--blue:#2f5aa8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 60px}
h1{font-size:23px;margin:0 0 6px}
h2{font-size:18px;margin:34px 0 6px;padding-left:10px;border-left:5px solid var(--red)}
h3{font-size:15px;margin:0}
h4{font-size:12.5px;margin:12px 0 4px;color:var(--sub);font-weight:600}
.meta{color:var(--sub);font-size:12.5px;margin-bottom:10px}
.banner{background:#fff7e6;border:1px solid #f0d9a8;border-radius:8px;padding:10px 12px;font-size:13px;margin:10px 0 4px}
.banner b{color:var(--amber)}
.volbanner{background:#f6fdf8;border:1px solid #b9e0cc;border-radius:8px;padding:11px 13px;font-size:13px;margin:12px 0 4px}
.volbanner.red{background:#fdecea;border-color:#f3c3bd}
.volbanner .vb-head{font-weight:700;margin-bottom:5px}
.volbanner .vred{color:var(--red);font-weight:600}
.volbanner .vok{color:var(--green)}
.volbanner .vamber{color:var(--amber)}
nav{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:14px 0 4px;font-size:13px}
nav a{color:var(--blue);text-decoration:none;margin-right:14px;white-space:nowrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0;border-top:4px solid var(--line)}
.card.safe{border-top-color:var(--green)}.card.watch{border-top-color:var(--amber)}
.card.lv1,.card.lv2{border-top-color:#d99a1e}.card.danger{border-top-color:var(--red)}
.card header{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
.card .rt{text-align:right}
.chip{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12.5px;font-weight:600}
.chip.safe{background:#e8f5ee;color:var(--green);border:1px solid #b9e0cc}
.chip.watch{background:#fdf3e0;color:#9a6b0c;border:1px solid #f0d9a8}
.chip.lv1,.chip.lv2{background:#fdf0d8;color:#a3610b;border:1px solid #ecc27a}
.chip.danger{background:#fdecea;color:var(--red);border:1px solid #f3c3bd}
.why{margin:8px 0 2px;font-size:13px;background:#fafaf8;border-left:3px solid var(--line);padding:7px 10px;border-radius:0 6px 6px 0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:6px 22px;margin-top:6px}
ul.chk{list-style:none;padding:0;margin:0;font-size:12.5px}
ul.chk li{padding:3px 0;border-bottom:1px dashed #f0eee9}
ul.chk b{display:inline-block;width:14px}
ul.chk li.ok b{color:var(--green)}ul.chk li.no b{color:var(--red)}ul.chk li.unk b{color:#b0b4bb}
.sub{color:var(--sub);font-size:11.5px;font-weight:400}
.foot{margin-top:12px;border-top:1px solid var(--line);padding-top:10px;font-size:12.5px}
.foot div{margin:5px 0}
.spk{margin-top:8px}
svg.spark{display:block;width:100%;max-width:260px;height:54px}
table.grid{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
table.grid th,table.grid td{border-bottom:1px solid var(--line);border-right:1px solid var(--line);padding:8px 11px;vertical-align:top;text-align:left}
table.grid thead th{background:#f0efeb;font-size:12.5px;text-align:left}
table.grid tr>th.rk,table.grid tbody th{background:#fbfaf8;font-weight:600;width:190px;font-size:12.5px}
table.grid.tight tbody th{width:170px}
td .flag{display:inline-block;margin-left:4px;background:#fdecea;color:var(--red);border-radius:4px;padding:0 5px;font-size:11px}
td .good{color:var(--green)}td .bad{color:var(--red);font-weight:700}
td .unkc{color:#b0b4bb}
table.grid tr.bord>th{border-top:2px solid #d8d4cc}
table.grid tr.why td{color:#555;font-size:12.5px}
.gridwrap{overflow-x:auto}
.wide{min-width:960px}
.cap{color:var(--sub);font-size:12px;margin:2px 0 8px}
.notes{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 16px;font-size:12.5px}
.notes li{margin:5px 0}
footer{margin-top:26px;color:var(--sub);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
</style></head><body><div class="wrap">

<h1>中美期权风险释放汇总 ｜ 波动率预警 + 未来5日预测 ｜ 上篇 A股五轨 · 下篇 美股三轨</h1>
<div style="background:#fffbe6;border:1px solid #ffe58f;border-radius:8px;padding:8px 12px;margin:8px 0;font-size:13.5px"><b>@DATANOTE@</b></div>
<div class="meta">框架：付鹏缩圈框架·期权风险层（各标的独立 252 日基线，禁跨标的比绝对值）　｜　报告口径日：<b>@ASOFD@（A股当日盘中）</b></div>
<div class="meta">数据时点：<b>A股</b> @ASOFD@ 盘中（当日实时快照 + 自算口径；252 日窗口 @WIN0@ ~ @WIN1@）　｜　
<b>美股</b> CBOE 波动率指数收盘 @UIDX@、ETF 收盘 @UETF@（各自最近可得收盘），指数期权链为最近延迟快照（数据时间 @UCHAIN@，盘中，非收盘价）</div>

@VOLBAN@
<div class="banner"><b>冷启动校准期 · 第 1 天</b>：五轨 + 三轨的 252 日基线已存盘，按纪律头 3 个交易日只校准不报警。下表灯色为「按规则判定」的参考值，正式报警自第 3 个交易日收后生效；缺历史项的标的（美股链历史）暂以「待更新」呈现，不做插值。</div>

<nav><b>目录</b>　<a href="#vol">期权波动率预警</a><a href="#fc">未来5日波动率预测</a><a href="#a">上篇 · A股五轨期权风险释放</a><a href="#u">下篇 · 美股三轨期权风险释放</a><a href="#bounds">口径与数据边界</a><a href="#src">数据来源</a></nav>

<h2 id="vol">期权波动率预警（独立一维，与风险释放判灯并列）</h2>
<div class="meta">红灯 = 自身 252 日分位 ≥ 90%　或　绝对水位（科创50 VIX&gt;42 / 创业板 VIX&gt;34 / 中证1000 ATM-IV&gt;28）　或　隐历差 IV−HV20 ≥ +8pct；黄灯 = 分位 ≥ 70%（科创50/创业板 75%）、隐历差 ≥ +5pct、或 R ≥ 1.00；绿灯 = 其余。每标的以自身 252 日基线独立判级，不跨标比绝对值。</div>
<div class="gridwrap wide">@VOLTAB@</div>

<h2 id="fc">未来 5 个交易日波动率预测（含下一交易日）</h2>
<div class="meta">方法：对各标的的隐含波动率序列（A股 300ETF/500ETF/科创50/创业板 取券商 QVIX 30日、中证1000 取 MO 当月经 ATM-IV；美股取 CBOE VIX / VXN / RVX）在<b>对数空间</b>拟合均值回复 AR(1)：x=ln(v)，OLS 估计 x<sub>t+1</sub>=c+φ·x<sub>t</sub>+ε（窗口 252 个交易日，要求 0&lt;φ&lt;1），长期均值 μ=c/(1−φ)，h 步点预测 = exp(μ+φ<sup>h</sup>(x<sub>t</sub>−μ))，区间为 <b>80% 预测区间</b>（z=1.2816，σ<sub>h</sub>=σ<sub>ε</sub>·√((1−φ<sup>2h</sup>)/(1−φ<sup>2</sup>))）；另以 RiskMetrics EWMA（λ=0.94）对标的日收益年化的实现波动率作参照锚，仅参照、不参与预测。未来交易日按工作日推算，自数据日之后的下一个交易日起连续 5 个。</div>
<div class="banner"><b>边界</b>：本表为<b>模型外推</b>，非交易所/券商发布值；φ 接近 1 表示波动率回复慢、预测路径更"黏"。缺序列或有效样本不足的标的写"待更新"，不插值、不跨标比绝对值。</div>

<h4>模型参数（逐标的拟合结果）</h4>
<div class="cap">窗口 252 个交易日；A股数据日 @ASOFD@（当日盘中，实时自算）；美股按各指数自身最近可得收盘。生成日 @FCGEN@。</div>
<div class="gridwrap wide">@FCAPAR@</div>
<div class="gridwrap wide">@FCUPAR@</div>

<h4>逐标的 · 5 个交易日波动率预测（点预测 + 80% 区间）</h4>
<div class="cap">A股五轨 · 预测交易日按工作日自数据日（@ASOFD@ 盘中）之后连续 5 个交易日推算</div>
<div class="gridwrap wide">@FCATAB@</div>
<div class="cap">美股三轨 · 按各指数自身最近可得收盘起算（具体日期见下表）</div>
<div class="gridwrap wide">@FCUTAB@</div>

<h4>预判：仅可外推口径（绝对水位阈值 + 相对长期均值）</h4>
<div class="cap">分位（需 252 日样本重算）与隐历差（需新增 HV20）无法由隐波单序列外推，故预判只覆盖「绝对水位阈值」（科创50 VIX 42 / 创业板 VIX 34 / 中证1000 ATM-IV 28）与「相对自身长期均值」两项；美股三轨无绝对水位阈值，仅列相对长期均值。均为模型外推下的情形，不等于届时实际判灯。</div>
<div class="gridwrap wide">@FCAVER@</div>
<div class="gridwrap wide">@FCUVER@</div>

<h2 id="a">上篇 · A股五轨期权风险释放</h2>
<div class="meta">五个独立监控对象：沪深300ETF期权、中证500ETF期权、科创50ETF期权、创业板ETF期权、中证1000股指期权（MO+IM，期货期权替代）。上证50ETF按框架不纳入。</div>

<h4>八字段监控表（+ 判灯与释放明细）</h4>
<div class="cap">数据日：@ASOFD@（A股当日盘中·实时自算口径）；分位 / R / SKEW / PCR / GEX 基线窗口 @WIN0@ ~ @WIN1@（252个交易日），各标的独立</div>
<div class="gridwrap wide">@ATABLE@</div>

<h4>灯色 · 命中项 · 释放进度对照</h4>
<div class="cap">判灯口径日：@ASOFD@（当日盘中）；🟡Lv1 需命中 ≥2 项才升级，🔴 红灯需在 Lv1≥2 项基础上再命中任一红灯条件</div>
<div class="gridwrap wide">@ALIGHT@
</div>

<h4>判灯依据（逐标的）</h4>
<div class="gridwrap wide"><table class="grid tight"><thead><tr><th class="rk">标的</th><th>输出结论</th></tr></thead><tbody>@ASUM@</tbody></table></div>

<h4>当前卡在哪</h4>
<div class="gridwrap wide"><table class="grid tight"><thead><tr><th class="rk">标的</th><th>卡点（未通过项 / 已命中红灯条件）</th></tr></thead><tbody>@AWHERE@</tbody></table></div>

<h4>下次最先翻色观察项</h4>
<div class="gridwrap wide"><table class="grid tight"><thead><tr><th class="rk">标的</th><th>观察项</th></tr></thead><tbody>@ANEXT@</tbody></table></div>

<h4>逐标的证据卡</h4>
@ACARDS@

<h2 id="u">下篇 · 美股三轨期权风险释放</h2>
<div class="meta">三个独立监控对象：标普500（VIX / SPY）、纳斯达克100（VXN / QQQ）、罗素2000小盘（RVX / IWM），均关注 CBOE 期权链的隐波、期限结构、偏度、持仓PCR 与 Gamma 代理。各标的独立 252 日基线。</div>

<h4>美股监控表（+ 判灯与释放明细）</h4>
<div class="cap">数据日：CBOE 波动率指数收盘 @UIDX@、ETF 收盘 @UETF@；指数期权链为最近延迟快照（数据时间 @UCHAIN@，盘中，非收盘价）；各指数 252 日窗口截至其最近可得收盘</div>
<div class="gridwrap wide">@UTABLE@</div>

<h4>判灯依据（逐标的）</h4>
<div class="gridwrap wide"><table class="grid tight"><thead><tr><th class="rk">标的</th><th>输出结论</th></tr></thead><tbody>@USUM@</tbody></table></div>

<h4>当前卡在哪</h4>
<div class="gridwrap wide"><table class="grid tight"><thead><tr><th class="rk">标的</th><th>卡点（待更新项 / 未通过项）</th></tr></thead><tbody>@UWHERE@</tbody></table></div>

<h4>下次最先翻色观察项</h4>
<div class="gridwrap wide"><table class="grid tight"><thead><tr><th class="rk">标的</th><th>观察项</th></tr></thead><tbody>@UNEXT@</tbody></table></div>

<h4>逐标的证据卡</h4>
@UCARDS@

<h2 id="bounds">口径与数据边界</h2>
<div class="notes"><ul>
<li><b>各标的各算各的 252 日基线</b>：VIX 绝对值天生差档（300 &lt; 500 &lt; 1000 &lt; 创业板 &lt; 科创50），本报告只比各标的自身的分位、R、SKEW 相对位置，不跨标的比大小。</li>
<li><b>SKEW 口径</b>：A股无官方偏度指数，用期权链自算的 25Δ 风险逆转（虚值put IV − 虚值call IV）代理，紧张线取该标的自身 252 日中位数 +1σ；美股标普500直接使用 CBOE SKEW 指数（1990 年起序列），纳指/罗素无单独偏度发布，列「待更新」并附链内 25Δrr 参照。</li>
<li><b>IV / Gamma 均为自算代理</b>：A股与美股期权链原始文件不含隐含波动率与 Gamma 字段，IV 由结算价 Black-Scholes 反解（无风险利率 1.8%），Gamma 由反解 IV 代入 BS 公式逐档计算；因此 §④⑤⑧⑨ 为「自算代理」口径，与券商金工 QVIX/SKEW 口径可能存在量级差，不与其绝对值直接混用。</li>
<li><b>30日隐波 VIX 口径</b>：300ETF/500ETF/科创50/创业板为<b>链上自算的 30 日恒定期限 ATM 隐含波动率</b>（两侧到期月在方差空间插值到 30 日；原券商 QVIX(30日) 序列源已按用户口径停用）；中证1000无官方 QVIX，以 MO 当月平价期权 ATM 隐含波动率自算并按 30 日历日折算，属「期货期权替代」口径。</li>
<li><b>GEX 代理与 Gamma Flip 线</b>：按逐档 持仓量×Gamma 的符号加总（call 为正、put 为负）求分位与过零点，不报绝对值。Flip 线精度受行权价档距限制（300ETF 2.39% ｜ 500ETF 3.64% ｜ 科创50 3.53% ｜ 创业板 1.84% ｜ 1000 1.47% ｜ 美股三个指数 0.06%~0.18%）；现价与 Flip 线距离小于一个行权价档时判为「疑似收破·方向不可分辨」，不作为红灯依据。</li>
<li><b>VVIX 口径</b>：美股 CBOE VVIX（波动率的波动率）为标普500 口径——由 VIX 期权链按无模型方差法算出的 30 天前瞻预期波动率，属前瞻口径，取自身近一年 252 交易日分位（分位 ≥ 阈值即报警）；纳指/罗素2000 无 VVIX 等价口径。A股 5 标的 VVIX（回望口径，波动率指数自身 30 日滚动年化对数波动率）已于 2026-10-30 按用户口径停用，本报告不再计算、展示与报警。</li>
<li><b>倒挂与其他「连续N日」条件</b>：R、持仓PCR 的连续判定按各标的最近 4 个交易日取值；A股为 @ASOFD@（盘中）及之前 3 个交易日，美股因缺期权链历史，相关连续性条件一律标「待更新」，只用当日值。</li>
<li><b>美股缺链历史</b>：CBOE 延迟链仅提供当日快照，无 252 日序列，故美股 GEX 分位、R 连续3日、持仓PCR连续3日、纳指/罗素 SKEW 基线为「待更新」；本报告不使用指数波动率历史替代链历史，也不做插值。</li>
<li><b>日期分层</b>：美股波动率指数与 ETF（各自最近可得收盘 @UIDX@、@UETF@）与指数期权链快照（@UCHAIN@）不是同一时点，两者不混算；A股全部字段为 @ASOFD@ 当日盘中（实时快照 + 自算）口径。</li>
<li><b>5 日波动率预测口径</b>：为本地脚本对数空间 AR(1)（均值回复）外推，窗口 252 个交易日、80% 预测区间，z=1.2816；配套 EWMA(λ=0.94) 实现波动率仅作参照锚。属模型外推，非官方发布值，不代表届时实际隐波；分位与隐历差两类红灯条件不参与外推，故"预判"仅覆盖绝对水位阈值与相对长期均值。</li>
<li><b>盘中实时化口径（2026-11-05 起）</b>：A股全部字段改由当日盘中实时快照（标的实时价：新浪；期权实时链：东财沪/深/中金所）自行计算，QVIX 改为链上自算 30 日恒定期限 ATM 隐波；不再依赖第三方 QVIX 日线与日终结算数据。</li>
<li><b>与旧模板的差异</b>：原「A股五轨模板」曾限定不输出美股 CBOE VIX/VVIX；本次按用户最新要求，下篇以美股三轨（含 CBOE 波动率指数与期权链）独立呈现；上证50ETF 仍不输出。</li>
</ul></div>

<h2 id="src">数据来源</h2>
<div class="notes"><ul>
<li>A股期权链与标的行情：<b>当日盘中实时快照</b>——标的实时价取新浪财经行情接口，期权实时链取东方财富行情接口（沪 m:10 / 深 m:12 / 中金所 MO m:221+t:2），含最新价与持仓量；合约基础信息（行权价 / 到期日 / 乘数）取交易所公开合约列表，并按 252 个交易日留存历史链。</li>
<li>A股隐波：链上<b>自算 30 日恒定期限 ATM 隐含波动率</b>（300ETF / 500ETF / 科创50 / 创业板，历史窗口同法重算）；中证1000 为 MO 链当月 ATM 自算。</li>
<li>美股波动率指数与偏度：CBOE 历史序列（VIX、VIX9D、VIX3M、VXN、RVX、SKEW、VVIX，最近可得收盘 @UIDX@）。</li>
<li>美股指数期权链：CBOE 延迟链快照（SPX / NDX / RUT，含各行权价持仓量，数据时间 @UCHAIN@）。</li>
<li>美股 ETF 与指数现价：NASDAQ 官方历史行情接口（SPY / QQQ / IWM，最近可得收盘 @UETF@）。</li>
<li>派生指标（分位、R、25Δrr、隐历差、PCR、GEX 代理、Flip 线、IM 基差 Z）均由本地脚本按上述原始数据确定性计算，原始响应与中间结果留存在任务工作区。</li>
<li>未来 5 个交易日波动率预测：由本地脚本对上述隐波序列（QVIX / MO ATM-IV / CBOE VIX·VXN·RVX）拟合对数空间 AR(1) 并外推，附 80% 预测区间；实现波动率参照锚取对应标的（510300/510500/588000/159915/000852/SPY/QQQ/IWM）日收益 EWMA(λ=0.94) 年化。预测结果留存在任务工作区（data/vol_forecast.json）。</li>
</ul></div>

<footer>
本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。数据可能存在延迟、遗漏或误差，请独立判断并根据自身风险承受能力审慎决策。
<div class="sub">生成时间：@GEND@　｜　框架：付鹏缩圈框架·期权风险层监控（A股五轨 + 美股三轨）　｜　冷启动校准期第 1 天，灯色为规则判定参考值</div>
</footer>
</div></body></html>"""

for _tok, _val in [("@ASOFD@", ASOFD), ("@WIN0@", WIN0), ("@WIN1@", WIN1),
                   ("@GEND@", GEND), ("@UIDX@", UIDX), ("@UETF@", UETF),
                   ("@UCHAIN@", UCHAIN), ("@FCGEN@", FC_GEN), ("@DATANOTE@", DATANOTE)]:
    HTML = HTML.replace(_tok, _val)

for _tok, _val in [("@VOLBAN@", VOLBAN), ("@VOLTAB@", VOLTAB),
                   ("@ATABLE@", a_table_ok()), ("@ALIGHT@", a_lights()), ("@ASUM@", a_sum_rows),
                   ("@AWHERE@", a_where), ("@ANEXT@", a_next),
                   ("@ACARDS@", cards(a, A_ORDER, "#c0392b")),
                   ("@UTABLE@", u_table()), ("@USUM@", u_sum_rows), ("@UWHERE@", u_where),
                   ("@UNEXT@", u_next), ("@UCARDS@", cards(u, U_ORDER, "#2f5aa8")),
                   ("@FCAPAR@", FC_A_PAR), ("@FCUPAR@", FC_U_PAR),
                   ("@FCATAB@", FC_A_TAB), ("@FCUTAB@", FC_U_TAB),
                   ("@FCAVER@", FC_A_VER), ("@FCUVER@", FC_U_VER)]:
    HTML = HTML.replace(_tok, _val)

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, len(HTML), "chars")
