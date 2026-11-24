# -*- coding: utf-8 -*-
"""生成精简桑基图页面（仅桑基图，echarts 走 CDN），并输出 data URL 供云端浏览器打开"""
import json, os, base64
import pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
REPORT = os.path.join(BASE, "report_run")

comb = pd.read_csv(os.path.join(REPORT, "l1_combined.csv"))
drill = pd.read_csv(os.path.join(REPORT, "l3_drilldown.csv"))
concepts = json.load(open(os.path.join(REPORT, "concepts", "concepts.json")))

concept_rows = []
for k, v in concepts.items():
    s = pd.Series({r["trade_date"]: r["close"] for r in v}).sort_index()
    if len(s) < 61:
        continue
    concept_rows.append((k, (s.iloc[-1] / s.iloc[-21] - 1) * 100, (s.iloc[-1] / s.iloc[-61] - 1) * 100))
concept_df = pd.DataFrame(concept_rows, columns=["概念", "m20", "m60"]).sort_values("m20", ascending=False)

risk_l1 = comb[comb["风险分"] >= 1].sort_values(["风险分", "机会分"], ascending=[False, True]).head(12)
inflow = []
opp_l1 = comb[comb["机会分"] >= 1].sort_values(["机会分", "横截面动量排名"], ascending=[False, True])
drill_ok = drill[drill["label"].isin(["机会", "机会观察"])]
seen = {}
for _, r in opp_l1.iterrows():
    l1n = r["一级行业"]
    sub = drill_ok[drill_ok["l1"] == l1n].sort_values("动量排名").head(3)
    for _, s in sub.iterrows():
        if (l1n, s["name"]) in seen:
            continue
        seen[(l1n, s["name"])] = 1
        inflow.append({"name": f"{l1n}·{s['name']}", "weight": float(r["机会分"])})
concept_keep = concept_df[concept_df["m20"] > concept_df["m20"].median()].head(6)
extra = {"存储芯片", "共封装光学(CPO)", "白酒概念"}
for _, c in concept_keep.iterrows():
    extra.add(c["概念"])
for cn in sorted(extra, key=lambda x: -concept_df.loc[concept_df["概念"] == x, "m20"].iloc[0]):
    row = concept_df[concept_df["概念"] == cn].iloc[0]
    inflow.append({"name": f"概念·{cn}", "weight": 1.0, "m20": row["m20"]})
seen2 = set()
uniq = []
for it in inflow:
    if it["name"] in seen2:
        continue
    seen2.add(it["name"])
    uniq.append(it)
inflow = uniq

nodes = []
for _, r in risk_l1.iterrows():
    nodes.append({"name": f"{r['一级行业']}（流出·动量#{int(r['横截面动量排名'])}）",
                  "itemStyle": {"color": "#e06c6c"}})
for it in inflow:
    nodes.append({"name": it["name"], "itemStyle": {"color": "#5fa8d3"}})
links = []
for _, r in risk_l1.iterrows():
    src = f"{r['一级行业']}（流出·动量#{int(r['横截面动量排名'])}）"
    for it in inflow:
        links.append({"source": src, "target": it["name"],
                      "value": round(float(max(it.get("weight", 1.0), 0.5)), 2)})

SANK = json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False)

tpl = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script></head>
<body style="margin:0;background:#fff"><div id="sankey" style="width:1500px;height:860px"></div>
<script>
const SANK = __SANKJSON__;
const chart = echarts.init(document.getElementById("sankey"));
chart.setOption({
  tooltip: {trigger: "item", triggerOn: "mousemove", formatter: function(p){
    if (p.dataType === "edge") return "<b>" + p.data.source + " → " + p.data.target + "</b><br/>流强度: " + p.data.value;
    return "<b>" + p.data.name + "</b>";
  }},
  series: [{type: "sankey", layoutIterations: 64, nodeWidth: 18, nodeGap: 14,
    data: SANK.nodes,
    links: SANK.links.map(function(l){ return Object.assign({}, l, {lineStyle: {color: "gradient", opacity: 0.45, curveness: 0.5}}); }),
    label: {fontSize: 13, color: "#303133"},
    emphasis: {focus: "adjacency"}}]
});
</script></body></html>"""
html = tpl.replace("__SANKJSON__", SANK)
with open(os.path.join(REPORT, "sankey_standalone.html"), "w", encoding="utf-8") as f:
    f.write(html)
b = base64.b64encode(html.encode("utf-8")).decode()
open(os.path.join(REPORT, "sankey_dataurl.txt"), "w").write("data:text/html;base64," + b)
print("data-url len KB:", len(b) // 1024, "nodes:", len(nodes), "links:", len(links))