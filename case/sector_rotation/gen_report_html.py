# -*- coding: utf-8 -*-
"""行业轮动机会风险报告 HTML 生成
结构：
  1) 头部摘要：截止日、三方法、加权规则
  2) 加权汇总表（一级行业：机会分/风险分/净分/动量排名/方法明细）
  3) 行业流动桑基图：左=流出(风险一级宽口径,带动量排名) 右=流入(机会下钻三级/概念,带动量排名)
  4) 机会下钻明细表（申万三级 + 概念动量）
  5) 各方法原始名单
  6) 口径与缺口说明
"""
import json
import os

import pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
REPORT = os.path.join(BASE, "report_run")
ECHARTS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/open-source-industry-rotation/assets/echarts.min.js"

comb = pd.read_csv(os.path.join(REPORT, "l1_combined.csv"))
drill = pd.read_csv(os.path.join(REPORT, "l3_drilldown.csv")) if os.path.exists(os.path.join(REPORT, "l3_drilldown.csv")) else pd.DataFrame()
meta = json.load(open(os.path.join(REPORT, "combined.json")))
concepts = json.load(open(os.path.join(REPORT, "concepts", "concepts.json")))
open_rows = pd.read_csv(os.path.join(REPORT, "行业轮动数据明细_20260928.csv"))
stom = json.load(open(os.path.join(BASE, "ses_gffe5f330c6707ffeqvFIsPha0gz5nv",
                                  "daily_momentum_out", "signal_2026-09-28.json")))
tda = json.load(open(os.path.join(BASE, "tda_run", "results.json")))

ASOF = "2026-09-28"

# ---------- 概念动量 ----------
concept_rows = []
for k, v in concepts.items():
    s = pd.Series({r["trade_date"]: r["close"] for r in v}).sort_index()
    if len(s) < 61:
        continue
    mom20 = (s.iloc[-1] / s.iloc[-21] - 1) * 100
    mom60 = (s.iloc[-1] / s.iloc[-61] - 1) * 100
    concept_rows.append({"概念": k, "20日动量%": round(mom20, 2), "60日动量%": round(mom60, 2),
                         "数据可用": "是"})
concept_df = pd.DataFrame(concept_rows).sort_values("20日动量%", ascending=False)

# ---------- 桑基图：流出(风险一级) + 流入(机会一级下钻/概念) ----------
# 流出端：风险分>=1 的一级行业（宽口径），节点名带 #动量排名
risk_l1 = comb[comb["风险分"] >= 1].sort_values(["风险分", "机会分"],
                                                ascending=[False, True]).head(12)
inflow_items = []  # (name, tag, weight, group)
# 机会一级 -> 下钻三级（TDA 机会+观察 中最强的细分，每一级最多3个）
opp_l1 = comb[comb["机会分"] >= 1].sort_values(["机会分", "横截面动量排名"],
                                                ascending=[False, True])
drill_ok = drill[drill["label"].isin(["机会", "机会观察"])] if len(drill) else pd.DataFrame()
seen = {}
for _, r in opp_l1.iterrows():
    l1n = r["一级行业"]
    sub = drill_ok[drill_ok["l1"] == l1n] if len(drill_ok) else pd.DataFrame()
    if len(sub):
        sub = sub.sort_values("动量排名").head(3)
        for _, s in sub.iterrows():
            name = s["name"]
            if (l1n, name) in seen:
                continue
            seen[(l1n, name)] = 1
            inflow_items.append({"name": f"{l1n}·{name}", "weight": float(r["机会分"]),
                                 "group": "三级下钻"})
# 概念（20日动量 > 0 或强制纳入用户点名的存储/CPO/白酒）
concept_keep = concept_df[concept_df["20日动量%"] > concept_df["20日动量%"].median()].head(6)
extra = {"存储芯片", "共封装光学(CPO)", "白酒概念", "黄酒", "光模块", "光纤概念"}
for _, c in concept_keep.iterrows():
    extra.add(c["概念"])
for cn in sorted(extra, key=lambda x: -concept_df.loc[concept_df["概念"] == x, "20日动量%"].iloc[0]):
    row = concept_df[concept_df["概念"] == cn].iloc[0]
    inflow_items.append({"name": f"概念·{cn}", "weight": 1.0, "group": "概念",
                         "mom20": row["20日动量%"]})
# 去重
seen2 = set()
uniq_in = []
for it in inflow_items:
    if it["name"] in seen2:
        continue
    seen2.add(it["name"])
    uniq_in.append(it)
inflow_items = uniq_in

sankey_nodes = []
for _, r in risk_l1.iterrows():
    sankey_nodes.append({"name": f"{r['一级行业']}（流出·动量#{int(r['横截面动量排名'])}）",
                         "itemStyle": {"color": "#e06c6c"}})
for it in inflow_items:
    sankey_nodes.append({"name": it["name"], "itemStyle": {"color": "#5fa8d3"}})

sankey_links = []
for _, r in risk_l1.iterrows():
    src = f"{r['一级行业']}（流出·动量#{int(r['横截面动量排名'])}）"
    for it in inflow_items:
        sankey_links.append({"source": src, "target": it["name"],
                             "value": round(float(max(it.get("weight", 1.0), 0.5)), 2)})

# ---------- 各方法名单 ----------
tda_opp = [i["name"] for i in tda["industries"] if i["label"] in ("机会", "机会观察")]
tda_opp_full = [f"{i['name']}({i['label']})" for i in tda["industries"] if i["label"] in ("机会", "机会观察")]
tda_risk_full = [f"{i['name']}({i['label']})" for i in tda["industries"] if i["label"] in ("风险", "风险观察")]
open_opp = sorted(set(open_rows.loc[open_rows["标记"].str.contains("★"), "行业"]))
open_risk = sorted(set(open_rows.loc[open_rows["标记"].str.contains("🔴"), "行业"]))
stom_opp = [x["name"] for x in stom["B_top10"]]
stom_risk = [x["name"] for x in stom["B_bottom10"]]

# ---------- 表格 HTML ----------
def esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

comb_rows = "".join(
    f"<tr><td class='name'>{esc(r['一级行业'])}</td><td><b>{int(r['机会分'])}</b></td>"
    f"<td><b style='color:#c0392b'>{int(r['风险分'])}</b></td>"
    f"<td><b>{int(r['净分']):+d}</b></td><td>#{int(r['横截面动量排名'])}</td>"
    f"<td>{r['20日动量%']:+.1f}%</td><td>{r['60日动量%']:+.1f}%</td>"
    f"<td class='det'>{esc(r['方法明细'])}</td></tr>"
    for _, r in comb.iterrows())

drill_rows = ""
if len(drill) and "name" in drill.columns:
    dd = drill.sort_values(["l1", "label"]).head(60)
    drill_rows = "".join(
        f"<tr><td>{esc(r['l1'])}</td><td class='name'>{esc(r['name'])}</td>"
        f"<td>{esc(r.get('label',''))}</td><td>{esc(r.get('level',''))}</td>"
        f"<td>#{int(r.get('动量排名', 99)) if pd.notna(r.get('动量排名')) else '—'}</td></tr>"
        for _, r in dd.iterrows())

concept_rows_html = "".join(
    f"<tr><td class='name'>{esc(r['概念'])}</td><td>{r['20日动量%']:+.1f}%</td>"
    f"<td>{r['60日动量%']:+.1f}%</td><td>{esc(r['数据可用'])}</td></tr>"
    for _, r in concept_df.iterrows())

# ---------- 桑基图 JS ----------
sankey_js = json.dumps({"nodes": sankey_nodes, "links": sankey_links}, ensure_ascii=False)

echarts_src = ""
if os.path.exists(ECHARTS):
    echarts_src = open(ECHARTS, encoding="utf-8").read()
cdn = "" if echarts_src else '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>'

html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>行业轮动机会风险报告（截至{ASOF}）</title>
<style>
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f7fa;color:#303133;padding:24px;margin:0}}
.wrap{{max-width:1280px;margin:0 auto}}
h1{{font-size:24px;margin:0 0 6px}}.sub{{color:#909399;font-size:13px;line-height:1.7}}
.card{{background:#fff;border-radius:10px;padding:18px 20px;margin-top:16px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.badge{{display:inline-block;background:#ecf5ff;border:1px solid #a6c8ff;color:#2f6fd6;font-size:12px;padding:2px 12px;border-radius:12px;margin-bottom:10px}}
#sankey{{width:100%;height:640px}}
.legend{{display:flex;gap:18px;margin:10px 0;font-size:13px;color:#606266;flex-wrap:wrap}}
.tags{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
.tag{{font-size:12px;padding:3px 10px;border-radius:10px}}
.tag-g{{background:#f0f9eb;color:#529b2e;border:1px solid #a8d793}}
.tag-r{{background:#fef0f0;color:#c0392b;border:1px solid #f3c1c1}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
th,td{{padding:6px 8px;border-bottom:1px solid #ebeef5;text-align:center}}
th{{background:#fafbfc;color:#606266;font-weight:600}}
td.name{{text-align:left;font-weight:600}}
td.det{{text-align:left;color:#606266;font-size:11.5px}}
.note{{color:#909399;font-size:12px;margin-top:10px;line-height:1.8}}
h2{{font-size:17px;margin:0 0 4px}}
.col2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:900px){{.col2{{grid-template-columns:1fr}}}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:14px}}
.kpi{{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.kpi .n{{font-size:26px;font-weight:700}}
.kpi .t{{color:#909399;font-size:12px;margin-top:2px}}
</style></head><body><div class="wrap">

<h1>行业轮动机会风险报告</h1>
<div class="sub">数据截至 <b>{ASOF}</b> 收盘 · 三方法加权（各方法机会权重1 / 风险权重1）· 行业流动桑基图（流出宽口径、流入下钻）</div>

<div class="kpis">
  <div class="kpi"><div class="n" style="color:#529b2e">{int((comb['机会分']>=1).sum())}</div><div class="t">综合机会行业（净分≥1 或机会分≥1）</div></div>
  <div class="kpi"><div class="n" style="color:#c0392b">{int((comb['风险分']>=1).sum())}</div><div class="t">综合风险行业（风险分≥1）</div></div>
  <div class="kpi"><div class="n">{len(comb)}</div><div class="t">纳入汇总的申万一级行业数</div></div>
  <div class="kpi"><div class="n">{len(drill)}</div><div class="t">三级/二级下钻明细条数</div></div>
</div>

<div class="card">
<h2>核心结论</h2>
<div class="legend">
<span><b>综合机会（净分最高）</b>：{'、'.join(comb.sort_values('净分',ascending=False).head(5)['一级行业'].tolist())}</span>
<span><b>综合风险（风险分最高）</b>：{'、'.join(comb[comb['风险分']>=1].sort_values(['风险分','净分'],ascending=[False,True]).head(5)['一级行业'].tolist())}</span>
</div>
<div class="tags">
<span class="tag tag-r">流出端（风险，宽口径）</span>
<span class="tag">流入端（机会·下钻三级/概念）</span>
</div>
</div>

<div class="card">
<h2>行业流动桑基图</h2>
<div class="sub">左列 = 流出端（综合风险≥1 的申万一级行业，宽口径，节点带横截面动量排名#）；右列 = 流入端（机会下钻：申万三级细分 / 细粒度概念），节点带动量排名#或概念20日动量。流宽 = 机会强度。</div>
<div id="sankey"></div>
<div class="note">横向截面动量排名：20/60日动量 + 14日趋势强度的截面 z-score 平均，30 个申万一级行业降序（#1 最强）。流出端仅展示风险分≥1 且净排名前 12 的一级行业，避免桑基图过密。</div>
</div>

<div class="card">
<h2>一级行业加权汇总</h2>
<table><thead><tr><th>一级行业</th><th>机会分</th><th>风险分</th><th>净分</th><th>动量排名</th><th>20日动量</th><th>60日动量</th><th>方法明细</th></tr></thead>
<tbody>{comb_rows}</tbody></table>
<div class="note">方法明细 = 开源时序轮动（机会=Top4★ / 风险=Bottom4或单日暴跌🔴）｜TDA（机会=机会+机会观察 / 风险=风险+风险观察，三级/二级/一级单元映射至一级）｜STMOM 21日动量（B池行业ETF Top10机会 / Bottom10风险，映射至申万一级）。机会分 = Σ机会信号（每方法权重1），风险分 = Σ风险信号（每方法权重1），净分 = 机会分 − 风险分。</div>
</div>

<div class="col2">
<div class="card">
<h2>机会下钻 · 申万三级/二级</h2>
<table><thead><tr><th>一级</th><th>细分行业</th><th>信号</th><th>级别</th><th>一级动量排名</th></tr></thead>
<tbody>{drill_rows}</tbody></table>
<div class="note">仅列出 TDA 机会/机会观察映射的下钻明细（每级默认最多 60 条）；完整名单见 CSV。</div>
</div>
<div class="card">
<h2>细粒度概念动量（下钻补充）</h2>
<table><thead><tr><th>概念</th><th>20日动量</th><th>60日动量</th><th>可用性</th></tr></thead>
<tbody>{concept_rows_html}</tbody></table>
<div class="note">概念来源：同花顺概念指数（截至 {ASOF}）。「光模块」无独立概念指数，以「共封装光学(CPO)」代理；「黄酒」无独立概念指数（归入申万三级「其他酒类」，TDA 未触发信号），如实标注。</div>
</div>
</div>

<div class="col2">
<div class="card">
<h2>方法① 开源时序轮动（申万一级30行业）</h2>
<div class="sub">机会★：{', '.join(open_opp)}</div>
<div class="sub" style="margin-top:8px;color:#c0392b">风险🔴：{', '.join(open_risk)}</div>
<table><thead><tr><th>行业</th><th>上期分组</th><th>本期分组</th><th>迁移</th><th>评分</th><th>当日涨跌</th><th>标记</th></tr></thead>
<tbody>{''.join(f"<tr><td>{esc(r['行业'])}</td><td>{esc(r['上期分组'])}</td><td>{esc(r['本期分组'])}</td><td>{esc(r['迁移方向'])}</td><td>{r['本期评分']:.3f}</td><td>{r['当日涨跌%']:+.1f}%</td><td>{esc(r['标记'])}</td></tr>" for _, r in open_rows.iterrows())}</tbody></table>
</div>
<div class="card">
<h2>方法② STMOM 21日动量（行业ETF B池）</h2>
<div class="sub">机会（看多Top10）：{', '.join(stom_opp)}</div>
<div class="sub" style="margin-top:8px;color:#c0392b">风险（看空Bottom10）：{', '.join(stom_risk)}</div>
<div class="note" style="margin-top:8px">行业ETF 至申万一级映射：银行→银行；房地产→房地产；军工→国防军工；医药/生物医药→医药生物；食品饮料/消费→食品饮料；传媒→传媒；芯片→电子；通信→通信；光伏/新能源→电力设备；环保→环保；新能源车→汽车；有色→有色金属。</div>
</div>
</div>

<div class="card">
<h2>方法③ TDA 结构风险（申万三级体系扫描）</h2>
<div class="sub">机会名单（机会+机会观察，{len(tda_opp_full)} 个单元）：</div>
<div class="note">{'、'.join(tda_opp_full)}</div>
<div class="sub" style="margin-top:10px;color:#c0392b">风险名单（风险+风险观察，{len(tda_risk_full)} 个单元）：</div>
<div class="note">{'、'.join(tda_risk_full[:60])}{'…（完整见 CSV）' if len(tda_risk_full)>60 else ''}</div>
</div>

<div class="card">
<h2>口径与缺口说明</h2>
<div class="note">
1. 数据截至 {ASOF} 收盘；2026-09-25 中秋休市，上期截面取 2026-09-24。<br>
2. 三方法均为滞后/结构信号：动量类无法及时捕捉见顶暴跌，TDA 为 60 日结构窗口，请结合当日盘面复核。<br>
3. 「黄酒」无独立概念指数（归入申万三级「其他酒类」，本轮无 TDA 信号）；「光模块」无独立指数，以「共封装光学(CPO)」代理展示；「存储芯片」有独立概念指数（20日 -2.3%、60日 -22.7%）。<br>
4. TDA 数据范围：申万 2021 三级体系 346 个单元中 16 个行业指数因数据源于 2021-12-10 停止发布（粮食种植/海洋捕捞/林业Ⅲ/纯碱/钾肥/其他化学纤维/氨纶/稀土/钨/锂/农用机械/彩电/其他食品/机场/餐饮/综合电力设备商），已剔除并如实标注；其余 330 个单元覆盖至 2026-09-28。<br>
5. 本报告仅供研究参考，不构成投资建议。
</div>
</div>

</div>
{cdn}
<script>{echarts_src}</script>
<script>
const SANK={sankey_js};
const chart=echarts.init(document.getElementById("sankey"));
chart.setOption({{
  tooltip:{{trigger:"item",triggerOn:"mousemove",formatter:p=>{{
    if(p.dataType==="edge")return "<b>"+p.data.source+" → "+p.data.target+"</b><br/>流强度: "+p.data.value;
    return "<b>"+p.data.name+"</b>";
  }}}},
  series:[{{
    type:"sankey",
    layoutIterations:64,
    nodeWidth:16,nodeGap:14,
    data:SANK.nodes,
    links:SANK.links.map(l=>({{...l,lineStyle:{{color:"gradient",opacity:0.45,curveness:0.5}}}})),
    label:{{fontSize:12,color:"#303133"}},
    emphasis:{{focus:"adjacency"}}
  }}]
}});
window.addEventListener("resize",()=>chart.resize());
</script>
</body></html>"""

import sys
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPORT, "行业轮动机会风险报告_20260928.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("[OK]", out, f"{len(html)/1024:.0f} KB")
print("桑基节点:", len(sankey_nodes), "| 流:", len(sankey_links))