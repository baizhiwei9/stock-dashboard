# -*- coding: utf-8 -*-
"""T3: 生成 Top10 腿数验证 HTML 报告"""
import json

DATA = json.load(open("data/norm/chart_legs.json"))
data_js = json.dumps(DATA, ensure_ascii=False)

rows_matrix = """
<tr class="hl"><td class="nm">基线 · 双周 · 3+3（每板块 3 多 3 空 · 6 对）</td><td><b>+14.52%</b></td><td>1.2662</td><td class="g">4.44%</td><td>2026-04-07</td><td>66.6</td><td>推荐配置</td></tr>
<tr><td class="nm">双周 · 板块内满池 4+4 / 5+5（9 对）</td><td>+4.92%</td><td>1.0872</td><td>6.79%</td><td>2026-04-07</td><td>54.8</td><td>守板块隔离红线 · 满池口径</td></tr>
<tr><td class="nm">双周 · 跨板块 Top10 / Bottom10（19 池折 9 对）</td><td>+5.89%</td><td>1.1048</td><td class="g">5.07%</td><td>2026-07-06</td><td>57.0</td><td>放宽板块隔离 · Top10 口径</td></tr>
<tr><td class="nm">双周 · 跨板块 Top10 · 无风控对照</td><td>+1.40%</td><td>1.0245</td><td>7.29%</td><td>2026-07-06</td><td>58.5</td><td>风控贡献验证</td></tr>
<tr><td class="nm">周频 · 3+3</td><td>+9.30%</td><td>1.1674</td><td>7.30%</td><td>2026-04-07</td><td>111.4</td><td>频率对照</td></tr>
<tr><td class="nm">周频 · 板块内满池 4+4 / 5+5</td><td>-1.51%</td><td>0.9738</td><td>9.62%</td><td>2026-04-21</td><td>86.4</td><td>满池 × 周频最差</td></tr>
<tr><td class="nm">周频 · 跨板块 Top10</td><td>+4.19%</td><td>1.0741</td><td>6.68%</td><td>2026-04-21</td><td>92.7</td><td>Top10 × 周频</td></tr>
"""

rows_dilute = """
<tr><td class="nm">排序证据</td><td>第 1 名 score 1.33</td><td>第 3 名 0.12</td><td>第 5 名 -0.14</td><td>榜尾 -1.57</td></tr>
<tr><td class="nm">相对第 1 名衰减</td><td>—</td><td>第 3 名已衰减 <b>-91%</b></td><td>第 5 名已衰减 <b>-111%</b>（转负）</td><td>榜尾 -218%</td></tr>
<tr><td class="nm">持仓期预测力（多空腿 10 日收益差）</td><td>3+3：全期 +0.43%</td><td>满池 9 对：+0.18%</td><td>跨板块：+0.18%</td><td>—</td></tr>
<tr><td class="nm">2025 年单独切片</td><td>3+3：+0.33%</td><td>满池 9 对：<b>-0.14%（负）</b></td><td>跨板块：+0.23%</td><td>—</td></tr>
"""

rows_annual = """
<tr class="hl"><td class="nm">基线 · 双周 · 3+3</td><td>+7.3%</td><td>+18.7%</td><td>-1.1%</td><td>71%</td><td>两年皆正 · 月度最均匀</td></tr>
<tr><td class="nm">双周 · 板块内满池 9 对</td><td class="r">+2.6%</td><td class="r">+6.1%</td><td>-2.6%</td><td>52%</td><td>2025 落后 4.7pp · 正月占比最低</td></tr>
<tr><td class="nm">双周 · 跨板块 Top10</td><td class="r">+2.5%</td><td class="r">+8.0%</td><td>-2.3%</td><td>57%</td><td>2025 落后 4.8pp</td></tr>
<tr><td class="nm">周频 · 3+3</td><td>+7.8%</td><td>+9.4%</td><td>-3.0%</td><td>67%</td><td>2026 落后 9.3pp（频率损失）</td></tr>
"""

html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>商品期货横截面多空对冲策略 · Top10 腿数放宽验证（2025-2026）</title>
<style>
:root{--ink:#1a2233;--mut:#5b6b80;--line:#e3e8ef;--bg:#f6f8fb;--blue:#2f6fd0;--red:#d24d3f;--green:#2e8b57;}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;background:var(--bg);color:var(--ink);line-height:1.65}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:24px;margin:8px 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:22px}
h2{font-size:18px;margin:34px 0 10px;padding-left:10px;border-left:4px solid var(--blue)}
p{font-size:14px;margin:8px 0}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(20,30,60,.05)}
.kpis{display:flex;flex-wrap:wrap;gap:12px}
.kpi{flex:1;min-width:200px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:22px;font-weight:700}
.kpi .k{font-size:12px;color:var(--mut)}
.kpi .g{color:var(--green)}.kpi .r{color:var(--red)}
table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:right}
th{background:#f0f4f9;font-weight:600;text-align:right}
td.nm{text-align:left;font-weight:600}
tr.hl td{background:#fdf3e7}
td.g{color:var(--green);font-weight:700}
td.r{color:var(--red);font-weight:700}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;margin-right:6px}
.t-bl{background:#e8eef7;color:#2f6fd0}.t-rd{background:#fbe9e7;color:#c0392b}.t-gr{background:#e6f4ec;color:#2e8b57}
.note{font-size:12px;color:var(--mut)}
svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:#334;margin:8px 0 2px}
.legend span{display:inline-flex;align-items:center;gap:5px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.footer{margin-top:40px;font-size:12px;color:var(--mut);border-top:1px solid var(--line);padding-top:12px}
code{background:#eef1f6;padding:1px 6px;border-radius:4px;font-size:12.5px;font-family:ui-monospace,Consolas,monospace}
</style></head><body><div class="wrap">

<h1>商品期货横截面多空对冲策略 · Top10 腿数放宽验证（2025-01-02 ~ 2026-09-30）</h1>
<div class="sub">化工（9 品种）＋ 农产品（10 品种）· 板块内部多空对冲 · ATR20 倒数加权 · 总杠杆 1.5 倍 · 四类风控齐备 · 0.025% 单边成本 · 424 交易日 · 验证目标：把腿数从 3+3 放宽到 Top10 是否更优</div>

<h2>一、结论速览：Top10 两种口径都不如 3+3</h2>
<div class="kpis">
  <div class="kpi"><div class="k">基线 · 双周 · 3+3（6 对）</div><div class="v g">+14.52%<span style="font-size:13px">/年</span></div><div class="k">回撤 4.44% · 最均衡</div></div>
  <div class="kpi"><div class="k">板块内满池 9 对（守红线）</div><div class="v r">+4.92%<span style="font-size:13px">/年</span></div><div class="k">回撤 6.79% · 年化损失 9.6pp</div></div>
  <div class="kpi"><div class="k">跨板块 Top10 / Bottom10</div><div class="v r">+5.89%<span style="font-size:13px">/年</span></div><div class="k">回撤 5.07% · 年化损失 8.6pp</div></div>
  <div class="kpi"><div class="k">周频 · 3+3</div><div class="v">+9.30%<span style="font-size:13px">/年</span></div><div class="k">回撤 7.30% · 频率对照</div></div>
</div>
<div class="card">
<p><b>直接回答你的两个问题：</b></p>
<p><b>① 「双周和一周到底差在哪？一周后信号就不在交易机会里了吗？」</b>——不是信号过期，恰恰相反：<b>信号有效期超过一周</b>（持仓 10 日口径多空收益差全期 +0.43%，2026 年 +0.57%，排序在第 10 天依然有效）。周频的问题是它在第 5 天就把第 2 周本来还在生效的持仓平掉换新，等于把「蛋还没孵完就换窝」；双周让信号完整兑现两周再换。风控没有变慢——score 反转、back→contango 这些风控仍然<b>每天</b>在跑，变的只是「重新选组合」的频率，不是「监控风险」的频率；所以不存在「一周后就不在交易机会里」——机会（持仓逻辑）有效两周，风控（风险刹车）每天看着。</p>
<p><b>② 「那可以做 Top10 吗？」</b>——我按两种口径都给你实测了：<b>都不能，加宽是纯损失</b>。板块内满池 9 对年化只有 +4.92%（损失 9.6pp），跨板块 Top10/Bottom10 只有 +5.89%（损失 8.6pp），两种口径 2025 年都只有 +2.5~2.6%（基线 +7.3%），回撤反而更大。原因不是手续费，是<b>排序稀释</b>：你的综合 score 只有前 3 名有真实信号（第 1 名 1.33 → 第 3 名 0.12，已衰减 91%；第 5 名 -0.14，已衰减 111% 转负），第 4 名往后的腿是噪音甚至反向信号，加进去只会稀释确认度高的 3 条腿、抬高回撤。</p>
<p><b>简单说：3+3 不是我们「只做 3 对」的限制，而是当前因子体系「信号只够支撑 6 条腿」的事实。</b>想多赚钱的正路不是加宽腿数，而是提高单腿信号质量（前文优化已给出：双周调仓 +14.52%）。</p>
</div>

<h2>二、回测矩阵</h2>
<div class="card">
<table>
<tr><th>配置</th><th>年化收益</th><th>期末净值</th><th>最大回撤</th><th>回撤日期</th><th>累计换手(×)</th><th>说明</th></tr>
__ROWS_MATRIX__
</table>
<p class="note">✔ 绿色 = 回撤 ≤ 8%；跨板块 Top10 在 19 个品种下取前 10 多 + 后 10 空，席位重叠的第 10 名多空抵消后实际为 9 对（这也印证 10 对在 19 池中本就无法真正对称实现）。</p>
</div>

<h2>三、为什么加宽必输：排序稀释的证据</h2>
<div class="card">
<table>
<tr><th>证据项</th><th>头部</th><th>第 3 名</th><th>第 5 名</th><th>榜尾</th></tr>
__ROWS_DILUTE__
</table>
<p>解读：<b>综合 score（含 λ0.6 动量修正）截面分布中，只有前 2~3 名显著为正、榜尾 2~3 名显著为负</b>，中间名次趋近 0——这正是「3 多 3 空」的腿数依据。把腿加到第 4、5 名，等于把接近零信号甚至负信号的品种放进组合：它们按 ATR20 倒数权重挤占仓位（权重是「倒数 → 越小越靠前」，新腿权重大），摊薄头部确认度；同时 2025 年满池 9 对的持仓期预测力为 <b>-0.14%（负）</b>，说明加宽腿在 2025 年不仅是无效，还在拖累。</p>
</div>

<h2>四、净值与回撤曲线</h2>
<div class="card"><p style="margin:0 0 4px"><b>累计净值（1.5 倍杠杆 · 含风控与成本）</b></p><div id="chart"></div><div class="legend" id="legend"></div></div>
<div class="card"><p style="margin:0 0 4px"><b>组合回撤深度（%）</b></p><div id="chart-dd"></div><div class="legend" id="legend-dd"></div></div>

<h2>五、分年切片</h2>
<div class="card">
<table>
<tr><th>配置</th><th>2025 年</th><th>2026 年</th><th>最差月</th><th>正收益月占比</th><th>说明</th></tr>
__ROWS_ANNUAL__
</table>
<p class="note">加宽档的劣势并非集中在某一时段，而是<b>两年都输</b>：2025 年落后基线 4.7~4.8pp，2026 年落后 10.7~12.6pp，正收益月占比也明显下降（52~57% vs 71%）——说明稀释是系统性、结构性的，不是某个行情期的偶然。</p>
</div>

<h2>六、结论与建议</h2>
<div class="card">
<table><tr><th>选项</th><th>配置</th><th>结论</th></tr>
<tr><td class="nm"><span class="tag t-gr">确认有效</span>保持 3+3</td><td>双周调仓 · N20 · λ0.6 · 每板块 3 多 3 空 · 板块/多空等权</td><td>年化 +14.52%、回撤 4.44%，是当前因子体系下腿数边界与全局最优的平衡点</td></tr>
<tr><td class="nm"><span class="tag t-rd">不采纳</span>板块内满池 9 对</td><td>每板块 4~5 多 + 4~5 空</td><td>年化 +4.92%、回撤 6.79%，2025 预测力转负；「更多腿」= 更多噪音</td></tr>
<tr><td class="nm"><span class="tag t-rd">不采纳</span>跨板块 Top10/Bottom10</td><td>全市场 19 池按 score 取前 10 多 + 后 10 空</td><td>年化 +5.89%、回撤 5.07%；且需放宽「不做跨板块对冲」红线，双重代价</td></tr>
<tr><td class="nm">替代方向</td><td>若想提高组合容量/收益</td><td>① 提高单腿信号质量（调宽股池、引入更有效因子）而非加腿数；② 或按头部确认度对 3 条腿做动态权重（第 1 名单腿权重 > 第 3 名），把「信号只在上头」这件事变现</td></tr>
</table>
<p><b>操作建议：</b>① 维持双周 · 3+3 主配置；② 不加宽腿数——这是回测证据而非直觉；③ 若确需更多交易机会，唯一动腿数的合法方式是<b>扩大品种池</b>（新增符合四因子口径的化工/农产品品种），让「第 4 名」的 z-score 重新拉开，而不是在同一个 19 池里硬凑 10 对；④ 后续可测「头部确认度加权」（第 1/2/3 名不等权），我判断比加宽腿更可能有正贡献，需要的话我继续验证。</p>
</div>

<h2>七、数据缺口与局限</h2>
<div class="card">
<p class="note">与前几份报告一致：TA 库存 2025-11-19 后、AP 2026-05-29 后缺失，此后不入池；DCE 仓单不可得，以 fx168 社会库存替代；现货价不可得，基差因子用近月基差率代理；2026-09-25 全市场数据缺失。跨板块 Top10 因 19 品种 < 20 席位，重叠席位中性化，实际组合为 9 对；本验证仅覆盖 2025-2026 两年单一样本，稀释度结论依赖样本内排序质量，若引入显著更强的新因子使第 4~5 名 z-score 重新转正，腿数边界需重新测试。</p>
</div>

<div class="footer">由扩展引擎 backtest_legs.py 生成 · 基线复现 1.2662 / +14.52% / 4.44% 与上一份报告一致 · 生成时间 2026-10-02</div>
</div>

<script>
const DATA=__DATA_JS__;
const W=1120,H=380,PL=58,PR=14,PT=14,PB=30;
function makeChart(id,key,ymin,ymax,ticks,unit){
  const el=document.getElementById(id); if(!el) return;
  const dates=DATA[0].nav.map(p=>p[0]);
  const t0=dates[0],t1=dates[dates.length-1];
  const X=t=>PL+(t-t0)/(t1-t0)*(W-PL-PR);
  const Y=v=>PT+(1-(v-ymin)/(ymax-ymin))*(H-PT-PB);
  let svg='<svg viewBox="0 0 '+W+' '+H+'" xmlns="http://www.w3.org/2000/svg" font-size="11">';
  for(let i=0;i<=ticks;i++){const v=ymin+(ymax-ymin)*i/ticks;const y=Y(v);
    svg+='<line x1="'+PL+'" y1="'+y+'" x2="'+(W-PR)+'" y2="'+y+'" stroke="#eceff4" stroke-width="1"/>'+
         '<text x="'+(PL-6)+'" y="'+(y+4)+'" text-anchor="end" fill="#667">'+(key==='dd'?(v).toFixed(1):v.toFixed(2))+'</text>';}
  const quarts=["2025-01","2025-04","2025-07","2025-10","2026-01","2026-04","2026-07","2026-09"];
  quarts.forEach(q=>{const t=new Date(q+"-01T00:00:00Z").getTime();
    if(t>=t0&&t<=t1){const x=X(t);svg+='<line x1="'+x+'" y1="'+PT+'" x2="'+x+'" y2="'+(H-PB)+'" stroke="#f0f2f6" stroke-width="1" stroke-dasharray="3,3"/><text x="'+x+'" y="'+(H-PB+16)+'" text-anchor="middle" fill="#667">'+q+'</text>';}});
  if(key==='dd'){const y=Y(0);svg+='<line x1="'+PL+'" y1="'+y+'" x2="'+(W-PR)+'" y2="'+y+'" stroke="#99a" stroke-width="1.2"/>';}
  DATA.forEach(s=>{const pts=s[key].map(p=>X(p[0])+","+Y(p[1])).join(" ");
    svg+='<polyline points="'+pts+'" fill="none" stroke="'+s.color+'" stroke-width="1.8" opacity="0.95"/>';});
  svg+='</svg>';
  el.innerHTML=svg;
}
makeChart("chart","nav",0.95,1.32,5, "");
const lg=document.getElementById("legend");
lg.innerHTML=DATA.map(s=>'<span><i class="dot" style="background:'+s.color+'"></i>'+s.name+'</span>').join('');
makeChart("chart-dd","dd",-11,1,6, "%");
const lg2=document.getElementById("legend-dd");
lg2.innerHTML=DATA.map(s=>'<span><i class="dot" style="background:'+s.color+'"></i>'+s.name+'</span>').join('');
</script>
</body></html>
"""

html = html.replace("__ROWS_MATRIX__", rows_matrix).replace("__ROWS_DILUTE__", rows_dilute)
html = html.replace("__ROWS_ANNUAL__", rows_annual).replace("__DATA_JS__", data_js)
out = "商品期货横截面多空对冲策略-Top10腿数放宽验证-2025-2026.html"
open(out, "w", encoding="utf-8").write(html)
print("HTML 已写入:", out, "| 大小:", len(html), "字符")