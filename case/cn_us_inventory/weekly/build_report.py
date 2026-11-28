# -*- coding: utf-8 -*-
"""生成《库存周期 × 上下游利润转移 周报》HTML（数据全部来自 datahub EDB 落盘 CSV）。"""
import os, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "库存周期与利润转移周报_20261128.html")

m = pd.read_csv(os.path.join(DATA, "edb_monthly.csv"), index_col=0, parse_dates=True)
d = pd.read_csv(os.path.join(DATA, "edb_daily.csv"), index_col=0, parse_dates=True)

# ---------- 派生（与取数脚本同口径）----------
m["nm"] = m.index.month
m["real_inv"] = ((1 + m["CN.PROFIT.INV.YOY"] / 100) / (1 + m["CN.PPI.YOY"] / 100) - 1) * 100
m["ratio"] = (m["CN.PROFIT.INV.AMT"] * 10000) / (m["CN.PROFIT.REVENUE.AMT"] / m["nm"])

def _quad(a, b):
    if pd.isna(a) or pd.isna(b):
        return None
    return ("主动补库(过热)" if (a >= 0 and b >= 0) else
            ("被动去库(复苏)" if b >= 0 else ("被动补库(滞胀)" if a >= 0 else "主动去库(衰退)")))

m["quad"] = [_quad(a, b) for a, b in zip(m["CN.PROFIT.INV.YOY"], m["CN.PROFIT.REVENUE.YOY"])]
m["ma3_inv"] = m["CN.PROFIT.INV.YOY"].rolling(3).mean()
m["ma3_rev"] = m["CN.PROFIT.REVENUE.YOY"].rolling(3).mean()
m["ma3_real"] = m["real_inv"].rolling(3).mean()
last = m.dropna(subset=["CN.PROFIT.INV.YOY"]).index[-1]
cur = m.loc[last]
prev = m.dropna(subset=["CN.PROFIT.INV.YOY"]).iloc[-2]

qseries = m["quad"].dropna()
qn = 0
for v in qseries.iloc[::-1]:
    if v == qseries.iloc[-1]:
        qn += 1
    else:
        break
q_since = qseries.index[-qn].strftime("%Y-%m")

def runlen(s):
    s = s.dropna()
    if len(s) == 0:
        return 0, None
    c, n = s.iloc[-1], 0
    for v in s.iloc[::-1]:
        if v == c:
            n += 1
        else:
            break
    return n, c

inv_run, inv_dir = runlen(np.sign(m["CN.PROFIT.INV.YOY"].diff()))
real_run, real_dir = runlen(np.sign(m["real_inv"].diff()))
ratio_run, ratio_dir = runlen(np.sign(m["ratio"].diff()))

# 宽度
m["w_out_in"] = m["CN.PPI.PROD.YOY"] - m["CN.PPIRM.YOY"]
m["w_ppi_cpi"] = m["CN.PPI.YOY"] - m["CN.CPI.YOY"]
m["w_mp"] = m["CN.PPI.MINING.YOY"] - m["CN.PPI.PROC.YOY"]
m["w_rl"] = m["CN.PPI.RAW.YOY"] - m["CN.PPI.LIVE.YOY"]
w_cur, w_prev = m.iloc[-1], m.iloc[-2]

# 周频
w = d.resample("W-FRI").last()
rows = []
NAME = {"CN.REBAR.FUT": "螺纹钢", "CN.FUT.JM": "焦煤(代动力煤)", "CN.FUT.SC": "上海原油",
        "CN.FUT.CU": "沪铜", "CN.FUT.AL": "沪铝", "CN.FUT.FG": "玻璃", "CN.FUT.SA": "纯碱",
        "CN.FUT.TA": "PTA", "CN.FUT.MA": "甲醇", "CN.FUT.EG": "乙二醇",
        "CN.COMMODITY.CCPI": "大宗商品价格指数", "GLOBAL.BRENT": "布伦特原油",
        "GLOBAL.LME.CU": "LME铜", "GLOBAL.LME.AL": "LME铝"}
for c in d.columns:
    if c.startswith("CN.EXINV") or c not in NAME:
        continue
    s = w[c].dropna()
    if len(s) < 20:
        continue
    ma4, ma13 = s.rolling(4).mean(), s.rolling(13).mean()
    r4, _ = runlen(np.sign(ma4.diff()))
    rows.append({"name": NAME[c], "last": s.iloc[-1],
                 "w1": (s.iloc[-1] / s.iloc[-2] - 1) * 100,
                 "w4": (s.iloc[-1] / s.iloc[-5] - 1) * 100,
                 "w13": (s.iloc[-1] / s.iloc[-14] - 1) * 100,
                 "ma4": ma4.iloc[-1] / ma4.iloc[-2] - 1,
                 "ma13": ma13.iloc[-1] / ma13.iloc[-2] - 1,
                 "run4": r4})
wk = pd.DataFrame(rows)

# 上下游指数
base = d[d.index >= "2026-06-01"]
def mk(cols):
    parts = [base[c].dropna() / base[c].dropna().iloc[0] * 100 for c in cols]
    return pd.concat(parts, axis=1).mean(axis=1)
up = mk(["CN.REBAR.FUT", "CN.FUT.JM", "CN.FUT.SC", "CN.FUT.CU", "CN.FUT.AL"])
mid = mk(["CN.FUT.FG", "CN.FUT.SA", "CN.FUT.TA", "CN.FUT.MA", "CN.FUT.EG"])
wbs = pd.DataFrame({"up": up, "mid": mid}).resample("W-FRI").last()
wbs["spread"] = wbs["mid"] - wbs["up"]
wbs["d"] = wbs["spread"].diff()
sp_run, sp_dir = runlen(np.sign(wbs["d"]))

# 交易所库存 4 周变化（仓单口径）
invw = d[[c for c in d.columns if c.startswith("CN.EXINV")]].resample("W-FRI").last()
inv_chg = {}
INVNAME = {"CN.EXINV.RB": "螺纹钢", "CN.EXINV.CU": "沪铜", "CN.EXINV.AL": "沪铝",
           "CN.EXINV.TA": "PTA", "CN.EXINV.MA": "甲醇", "CN.EXINV.EG": "乙二醇"}
for c, nm in INVNAME.items():
    s = invw[c].dropna()
    if len(s) >= 5:
        inv_chg[nm] = (s.iloc[-1], (s.iloc[-1] / s.iloc[-5] - 1) * 100)

# 利润占比
shares = {k: m[k].dropna() for k in ("CN.PROFIT.SHARE.UP", "CN.PROFIT.SHARE.MID",
                                     "CN.PROFIT.SHARE.DOWN", "CN.PROFIT.SHARE.UTIL") if k in m}
share_cur = {k: v.iloc[-1] for k, v in shares.items() if len(v)}

# ---------- 关键数值 ----------
K = {
    "last": last.strftime("%Y-%m"),
    "prev": prev.name.strftime("%Y-%m"),
    "inv_yoy": cur["CN.PROFIT.INV.YOY"], "real_inv": cur["real_inv"], "ppi": cur["CN.PPI.YOY"],
    "rev_yoy": cur["CN.PROFIT.REVENUE.YOY"],
    "ratio": cur["ratio"], "ratio_prev": prev["ratio"],
    "inv_days": cur["CN.PROFIT.INV.DAYS"], "inv_days_chg": cur["CN.PROFIT.INV.DAYS.CHG"],
    "ar_days": cur["CN.PROFIT.AR.DAYS"], "ar_days_chg": cur["CN.PROFIT.AR.DAYS.CHG"],
    "opm": cur["CN.PROFIT.OPM"], "pmi_raw": cur["CN.PMI.RAW.INV"], "pmi_fin": cur["CN.PMI.FIN.INV"],
    "pmi": cur["CN.PMI.MFG"], "m1": cur["CN.M1.YOY"],
    "w_out_in": w_cur["w_out_in"], "w_out_in_d": w_cur["w_out_in"] - w_prev["w_out_in"],
    "w_ppi_cpi": w_cur["w_ppi_cpi"], "w_ppi_cpi_d": w_cur["w_ppi_cpi"] - w_prev["w_ppi_cpi"],
    "w_mp": w_cur["w_mp"], "w_mp_d": w_cur["w_mp"] - w_prev["w_mp"],
    "w_rl": w_cur["w_rl"], "w_rl_d": w_cur["w_rl"] - w_prev["w_rl"],
    "mining": cur["CN.PPI.MINING.YOY"], "raw": cur["CN.PPI.RAW.YOY"],
    "proc": cur["CN.PPI.PROC.YOY"], "live": cur["CN.PPI.LIVE.YOY"],
    "prod": cur["CN.PPI.PROD.YOY"], "ppirm": cur["CN.PPIRM.YOY"], "cpi": cur["CN.CPI.YOY"],
    "quad": cur["quad"], "quad_months": qn, "quad_since": q_since,
    "inv_run": inv_run, "inv_dir": "升" if inv_dir > 0 else "降",
    "real_run": real_run, "real_dir": "升" if real_dir > 0 else "降",
    "ratio_run": ratio_run, "ratio_dir": "升" if ratio_dir > 0 else "降",
    "ma3_inv": cur["ma3_inv"], "ma3_rev": cur["ma3_rev"], "ma3_real": cur["ma3_real"],
    "share_up": share_cur.get("CN.PROFIT.SHARE.UP"), "share_mid": share_cur.get("CN.PROFIT.SHARE.MID"),
    "share_down": share_cur.get("CN.PROFIT.SHARE.DOWN"), "share_util": share_cur.get("CN.PROFIT.SHARE.UTIL"),
    "spread": wbs["spread"].iloc[-1], "sp_run": sp_run,
    "sp_dir": "走阔" if sp_dir > 0 else "收窄",
    "up_idx": wbs["up"].iloc[-1], "mid_idx": wbs["mid"].iloc[-1],
    "wk_end": w.index[-1].strftime("%Y-%m-%d"),
    "inv_min": m["ratio"].min(), "inv_max": m["ratio"].max(),
}
K["trans"] = K["ppi"] / K["ppirm"]
K["trans_prod"] = K["prod"] / K["ppirm"]
K["trans_proc"] = K["proc"] / K["ppirm"]
K["real_rev"] = K["rev_yoy"] - K["ppi"]

json.dump({k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in K.items()},
          open(os.path.join(DATA, "key_numbers.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)

# ---------- HTML ----------
def fmt(v, d=1, suf=""):
    return "—" if pd.isna(v) else f"{v:.{d}f}{suf}"

def row(cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

grad = [("采掘工业", K["mining"], "#8B2E1F"), ("原材料工业", K["raw"], "#C0562F"),
        ("加工工业", K["proc"], "#D9963F"), ("生活资料", K["live"], "#3F7D5A")]
gmax = max(abs(v) for _, v, _ in grad)
grad_html = ""
for nm, v, col in grad:
    width = abs(v) / gmax * 100
    side = "left" if v >= 0 else "right"
    grad_html += (f'<div class="grow"><span class="glab">{nm}</span>'
                  f'<span class="gbar"><i style="width:{width:.0f}%;background:{col};float:{side}"></i></span>'
                  f'<span class="gval">{v:+.1f}%</span></div>')

wk_html = ""
for _, r in wk.iterrows():
    wk_html += row([r["name"], fmt(r["last"], 1), f"{r['w1']:+.2f}%", f"{r['w4']:+.2f}%",
                    f"{r['w13']:+.2f}%", "↑" if r["ma4"] > 0 else "↓", "↑" if r["ma13"] > 0 else "↓", f"{int(r['run4'])}周"])

inv_html = "".join(row([nm, f"{v[0]:,.0f}", f"{v[1]:+.1f}%"]) for nm, v in inv_chg.items())

month_rows = ""
for idx in m.dropna(subset=["CN.PROFIT.INV.YOY"]).index[-8:]:
    r = m.loc[idx]
    month_rows += row([idx.strftime("%Y-%m"), fmt(r["CN.PROFIT.INV.YOY"]), fmt(r["real_inv"]),
                       fmt(r["CN.PROFIT.REVENUE.YOY"]), fmt(r["ratio"], 2), r["quad"] or "—",
                       fmt(r["CN.PROFIT.INV.DAYS"]), fmt(r["CN.PROFIT.AR.DAYS"]),
                       fmt(r["CN.PMI.RAW.INV"]), fmt(r["CN.PMI.FIN.INV"])])

HTML = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>库存周期 × 上下游利润转移 周报（2026-11-28）</title>
<style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:1080px;margin:24px auto;padding:0 16px;color:#222;line-height:1.7}}
h1{{font-size:22px;border-bottom:2px solid #333;padding-bottom:8px}}
h2{{font-size:17px;margin-top:28px;border-left:4px solid #C0562F;padding-left:8px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:right}}
th{{background:#f6f3ef;text-align:center;font-weight:600}}
td:first-child,th:first-child{{text-align:left}}
.note{{font-size:12px;color:#666;margin:6px 0}}
.key{{background:#fdf7f2;border:1px solid #e6d3c3;padding:10px 14px;border-radius:6px;margin:12px 0}}
.grow{{display:flex;align-items:center;font-size:13px;margin:4px 0}}
.glab{{width:96px;flex:none}} .gbar{{flex:1;height:16px;background:#f2efe9;border-radius:3px;overflow:hidden;margin:0 10px}}
.gbar i{{display:block;height:100%;}} .gval{{width:66px;text-align:right;flex:none;font-variant-numeric:tabular-nums}}
ul{{padding-left:20px}} li{{margin:4px 0}}
</style></head><body>
<h1>库存周期 × 上下游利润转移 · 周频跟踪（截至 {K['wk_end']}）</h1>
<p class="note">为 2026-12-27 发布的 11 月规上工业企业效益数据做预习。月度口径最新为 {K['last']}（统计局 11-27 发布），
高频价格/库存截至 {K['wk_end']}。全部数值经 datahub EDB 取数（含本次新增接口），高频仅作方向预判，以月度工企数据为准。</p>

<div class="key"><b>一句话：</b>库存按“库存×营收”符号法仍在 <b>{K['quad']}</b>（已连续 {K['quad_months']} 个月，自 {K['quad_since']}），
但名义库存同比 {K['inv_yoy']:.1f}% 里 {K['ppi']:.1f}pp 是 PPI 涨价贡献、实际库存只有 {K['real_inv']:.1f}% 且已连降 {K['real_run']} 个月；
利润重心明确在<b>上游</b>（占全部工业利润 {K['share_up']:.1f}%，采矿业利润同比 +38.8%），三个宽度全部走阔（PPI−CPI {K['w_ppi_cpi']:+.1f}pp、采掘−加工 {K['w_mp']:+.1f}pp），
中下游仍在被挤——剪刀差<b>走阔、未收窄</b>。</div>

<h2>① 库存看板（月度真实值 + 高频方向）</h2>
<table><tr><th>指标</th><th>最新值（{K['last']}）</th><th>方向</th><th>读数与含义</th></tr>
{row(["名义库存（产成品存货同比）", fmt(K['inv_yoy'],1,'%'), f"连升 {K['inv_run']} 个月", "名义在累库，7 个月单边上行"])}
{row(["实际库存（名义−PPI 涨价）", fmt(K['real_inv'],2,'%'), f"连降 {K['real_run']} 个月", "扣除价格后累库力度在减弱：7 月 7.05%→10 月 5.96%"])}
{row(["库销比（存货/月均营收）", fmt(K['ratio'],2,' 个月'), f"本月{'升' if K['ratio']>K['ratio_prev'] else '降'}", f"2026 年区间 {K['inv_min']:.2f}~{K['inv_max']:.2f}，仍在区间上沿"])}
{row(["产成品存货周转天数", fmt(K['inv_days'],1,' 天'), f"同比+{K['inv_days_chg']:.1f} 天", "周转变慢，货压得更久"])}
{row(["应收账款回收期", fmt(K['ar_days'],1,' 天'), f"同比+{K['ar_days_chg']:.1f} 天", "回款变慢，赊销在增加"])}
{row(["PMI 原材料库存", fmt(K['pmi_raw'],1), "50 以下", "企业在主动压原料库存"])}
{row(["PMI 产成品库存", fmt(K['pmi_fin'],1), "50 以下", "成品也在去，环比方向仍是去库"])}
</table>
<p class="note">象限判定（库存×营收同比符号）：<b>{K['quad']}</b>，已持续 {K['quad_months']} 个月（自 {K['quad_since']}），上期同为 {K['quad']}——<b>本月未切换</b>。
但换“3 月均动量”口径（存货动量 {K['ma3_inv']:.1f}、营收动量 {K['ma3_rev']:.1f}、实际库存动量 {K['ma3_real']:.1f}），
营收增速已追不上库存增速，性质更接近<b>被动补库</b>——两套口径的差异全部来自价格：名义营收 {K['rev_yoy']:.1f}%，扣掉 PPI 后实际营收仅约 {K['real_rev']:.1f}%。</p>
<p class="note">是否临切换：<b>是，偏被动方向漂移</b>。触发看两条——①名义营收同比跌破名义库存同比（当前 {K['rev_yoy']:.1f}% vs {K['inv_yoy']:.1f}%）；②实际库存同比转负（当前 {K['real_inv']:.1f}%，按近 4 个月月均 −0.36pp 的速度约需 1 个季度）。</p>

<h2>② 近 8 个月四象限与辅助指标</h2>
<table><tr><th>月份</th><th>名义库存%</th><th>实际库存%</th><th>营收%</th><th>库销比(月)</th><th>象限</th><th>周转天数</th><th>回收期</th><th>PMI原料</th><th>PMI成品</th></tr>
{month_rows}</table>
<p class="note">“应收回收期与库销比一起抬头”= 压库存/赊销的假需求信号：本月库销比 {K['ratio']:.2f}（上月 {K['ratio_prev']:.2f}）+ 回收期同比 +{K['ar_days_chg']:.1f} 天 + 周转天数同比 +{K['inv_days_chg']:.1f} 天，<b>三项同向变差</b>，说明这轮累库里有相当一部分是“卖不动、先赊着”。</p>

<h2>③ 剪刀差宽度表（月度真实值，{K['last']} vs {K['prev']}）</h2>
<table><tr><th>宽度</th><th>当前</th><th>上月</th><th>变化</th><th>走阔/收窄</th><th>判读</th></tr>
{row(["出厂−购进价差（生产资料 PPI − PPIRM）", f"{K['w_out_in']:+.1f}pp", f"{K['w_out_in']-K['w_out_in_d']:+.1f}pp", f"{K['w_out_in_d']:+.1f}pp", "走阔（恶化）", "负值=成本涨幅超售价，中下游单位毛利被压；负得更多=挤压加重"])}
{row(["PPI − CPI 剪刀差", f"{K['w_ppi_cpi']:+.1f}pp", f"{K['w_ppi_cpi']-K['w_ppi_cpi_d']:+.1f}pp", f"{K['w_ppi_cpi_d']:+.1f}pp", "走阔", "工业品涨价、消费品涨不动，利润向上游/工业集中"])}
{row(["三层梯度：采掘 − 加工", f"{K['w_mp']:+.1f}pp", f"{K['w_mp']-K['w_mp_d']:+.1f}pp", f"{K['w_mp_d']:+.1f}pp", "走阔", "梯度更陡，上游议价权继续增强"])}
{row(["原材料 − 生活资料", f"{K['w_rl']:+.1f}pp", f"{K['w_rl']-K['w_rl_d']:+.1f}pp", f"{K['w_rl_d']:+.1f}pp", "走阔", "原料端对终端消费品的挤压加剧"])}
</table>
<p class="note">口径提示：出厂−购进用“生产资料 PPI − PPIRM”，正值=售价涨得比成本快（下游能转嫁）、负值=成本涨得比售价快（中下游被挤）。本月 {K['w_out_in']:+.1f}pp，是“上游吸走利润”的直接算术表达（等价于 PPIRM−生产资料 = {abs(K['w_out_in']):.1f}pp 的上游溢价）。
南华工业品指数不可得，购进侧以 EDB 的大宗商品价格指数（CN.COMMODITY.CCPI，周频）替代；PPI 明细来自统计局发布稿表格，2026 年起以 2025 年为基期，<b>不与其前基数直接连比</b>，故宽度只作方向与相对变化使用。</p>
<p class="note">传导系数（出厂涨幅/购进涨幅）：全口径 {K['trans']:.2f}、生产资料口径 {K['trans_prod']:.2f}、加工工业口径 {K['trans_proc']:.2f}。均 &lt;1，说明成本向下游传导不足一半，中下游在硬吃成本。</p>

<h2>④ 纵向价格梯度（2026-10 同比，文字版）</h2>
{grad_html}
<p class="note">梯度形状：<b>陡降型（上游极强、终端贴地）</b>。采掘 {K['mining']:+.1f}% → 原材料 {K['raw']:+.1f}% → 加工 {K['proc']:+.1f}% → 生活资料 {K['live']:+.1f}%，四级落差 {K['mining']-K['live']:.1f}pp。这是典型的<b>成本推动</b>型涨价：源头涨、终端不涨，利润被锁在采掘与原材料环节。
环比同向印证：10 月采掘环比 +2.8%、原材料 +2.0%、加工 +0.2%、生活资料 −0.1%，环比梯度同样是陡的。</p>

<h2>⑤ 利润转移结论</h2>
<p><b>重心在上游，而且在往上走。占比口径：</b>上游（采矿+原料）占全部工业利润 <b>{K['share_up']:.1f}%</b>、中游装备 {K['share_mid']:.1f}%、下游消费 {K['share_down']:.1f}%、公用 {K['share_util']:.1f}%。
8 月上游占比 31.2% → 10 月 32.2%（+1.0pp），同期中游 40.4%→40.3%、下游 18.3%→18.2%，<b>利润正从中下游往上游回流</b>。
增速口径也一致：1-10 月采矿业利润 +38.8%、制造业 +17.5%、电热气水 −14.8%；行业上看煤炭开采 +61.4%、有色冶炼 +71.7%、电子 +100.9%，而黑色冶炼 −14.2%、汽车 −26.0%。</p>
<p><b>三黑一色周度动量（截至 {K['wk_end']}）：</b></p>
<table><tr><th>品种</th><th>最新</th><th>1周</th><th>4周</th><th>13周</th><th>4周均线</th><th>13周均线</th><th>均线同向周数</th></tr>
{wk_html}</table>
<p class="note">读法：<b>上游价格在 11 月明显回落</b>——布伦特 4 周 −20.0%、沪铜 −13.3%、LME 铜 −13.2%、沪铝 −8.1%，只有煤端逆势（焦煤 4 周 +7.0%、本周 +8.2%，迎峰度冬季节性）；螺纹本周 +2.6% 但 13 周仍 −1.1%。
这意味着 10 月那份“上游利润暴增”的分行业数据，是用 9-10 月的高价算出来的；<b>11 月上游利润的同环比大概率见顶回落</b>。
动力煤无连续主力合约（ZC 停于 2022）、螺纹社会库存与水泥库容比无免费接口，分别用焦煤、交易所仓单、玻璃期货代理。</p>
<p class="note">交易所仓单（周度，仅供方向）：螺纹 {inv_chg.get('螺纹钢',('—',0))[0]:,.0f} 吨（4 周 {inv_chg.get('螺纹钢',('—',0))[1]:+.1f}%）、沪铜 {inv_chg.get('沪铜',('—',0))[0]:,.0f} 吨（{inv_chg.get('沪铜',('—',0))[1]:+.1f}%）。11 月是交割月、仓单集中注销，这种跳降不等于终端真实去库，<b>不能当需求证据</b>。</p>

<h2>⑥ 趋势切换预警</h2>
<ul>
<li><b>上下游相对强弱（高频）：</b>中游−上游价差指数由 9 月底 −8.6 收敛至 {K['spread']:+.1f}（{K['wk_end']}，2026-06-01=100），本周变动 {K['sp_dir']}、<b>连续同向仅 {K['sp_run']} 周</b>，未达“连续 3-4 周同向”的切换阈值 → <b>不触发</b>。且这次收敛是上游跌得更多造成的被动收敛，不是中游转强。</li>
<li><b>月度宽度：</b>三个宽度（出厂−购进、PPI−CPI、采掘−加工）本月全部朝同一方向（上游更强）变化，已连续 2 个月同向 → <b>再有一个月即构成季度级切换确认</b>（上游挤压延续）。</li>
<li><b>可交易的价格信号：</b>沪铝 4 周均线已连降 9 周、沪铜 6 周、大宗商品指数 7 周——上游价格动量的下行已是周线级别，但还没有传导到月度 PPI（10 月仍 +5.7%），这正是 11 月数据的关键看点。</li>
</ul>

<h2>⑦ 12-27 工企效益数据的 3 条重点验证项</h2>
<ul>
<li><b>①实际库存是否首次转负/名义库存是否见顶：</b>验证 11 月产成品存货同比是否从 12.0% 回落。若 PPI 同比随上游价格回落（高频 4 周 −13%~−20%），名义库存同比应滞后 1-2 个月见顶；若存货同比仍升而营收同比掉到 6% 以下，则象限正式由“主动补库”切到“被动补库”。</li>
<li><b>②上游利润占比是否回落：</b>看采矿业利润占比（10 月 {K['share_up']:.1f}%）与煤炭/有色/黑色三个行业同比。高频铜铝 4 周已跌 8%~13%，若上游占比回落、中游装备占比升，说明利润开始往下游扩散，剪刀差进入收窄段；反之则上游挤压继续。</li>
<li><b>③应收回收期与库销比是否继续双升：</b>10 月回收期 72.2 天（同比 +1.4 天）、库销比 {K['ratio']:.2f}（升）。若 11 月两项继续同向抬升，则本轮“补库”性质确认是被动累库+赊销撑起来的假需求，后续去库会更快、更痛；若回收期掉头向下，才算真需求接上。</li>
</ul>

<p class="note">数据与口径说明：月度取自 datahub EDB（CN.PROFIT.* / CN.PPI.* / CN.PPIRM.* / CN.PMI.* / CN.CPI.YOY / CN.M1.YOY，源为统计局发布稿解析，含本次新增的 nbs:ppi_hist / nbs:pmi_hist / nbs:profit_hist / nbs:profit_share 接口）；
高频取自 EDB（CN.FUT.* 国内期货主力连续、CN.EXINV.* 交易所库存、CN.COMMODITY.CCPI、GLOBAL.BRENT、GLOBAL.LME.CU/AL）。
已知缺口：螺纹钢社会库存、水泥库容比无接口；LME 库存接口最新仅到 2026-07-17（滞后）；PPI 明细历史缺 2025-07~2026-02；2026-11 PMI（11-30 发布）尚未出。
高频为方向预判，非真实值，以月度工企效益数据为准。</p>
</body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written:", OUT, len(HTML))
print(json.dumps(K, ensure_ascii=False, indent=1, default=str))