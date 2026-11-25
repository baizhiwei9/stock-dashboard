# -*- coding: utf-8 -*-
"""T4: 生成优化验证 HTML 报告"""
import json

DATA = json.load(open("data/norm/chart_opt.json"))
data_js = json.dumps(DATA, ensure_ascii=False)

rows_bw = """
<tr class="hl"><td class="nm">双周·N20·λ0.6（推荐配置）</td><td>+14.52%</td><td>1.2662</td><td class="g">4.44%</td><td>2026-04-07</td><td>66.6</td><td>仅改频率一参数</td></tr>
<tr><td class="nm">双周·λ0.5</td><td>+11.81%</td><td>1.2145</td><td class="g">5.70%</td><td>2026-04-07</td><td>66.8</td><td>动量偏轻 · 未达12%</td></tr>
<tr><td class="nm">双周·λ0.7</td><td>+12.62%</td><td>1.2300</td><td class="g">5.16%</td><td>2026-04-07</td><td>66.3</td><td>动量偏重</td></tr>
<tr><td class="nm">双周·λ0.8</td><td>+14.35%</td><td>1.2630</td><td class="g">5.16%</td><td>2026-04-07</td><td>68.6</td><td>再加重</td></tr>
<tr><td class="nm">双周·λ1.0</td><td>+15.86%</td><td>1.2921</td><td class="g">5.44%</td><td>2026-04-07</td><td>70.2</td><td>惩罚最重</td></tr>
<tr><td class="nm">双周·N15·λ0.6</td><td>+6.72%</td><td>1.1199</td><td class="g">6.00%</td><td>—</td><td>68.0</td><td>窗口错配 · 已排除</td></tr>
<tr><td class="nm">双周·腿数 2+2</td><td>+15.05%</td><td>1.2765</td><td>6.14%</td><td>2026-04-07</td><td>73.7</td><td>集中度↑ 回撤↑</td></tr>
<tr><td class="nm">双周·腿数 4+4</td><td>+7.53%</td><td>1.1348</td><td class="g">5.88%</td><td>—</td><td>61.9</td><td>过度分散 · 已排除</td></tr>
<tr><td class="nm">双周·板块化工60</td><td>+15.50%</td><td>1.2851</td><td class="g">5.52%</td><td>2026-04-07</td><td>66.6</td><td>化工偏重</td></tr>
<tr><td class="nm">双周·板块农产60</td><td>+13.52%</td><td>1.2471</td><td class="g">3.70%</td><td>2026-04-07</td><td>66.6</td><td>农产偏重</td></tr>
<tr><td class="nm">双周·偏空 0.40/0.60</td><td>+15.98%</td><td>1.2945</td><td class="g">5.12%</td><td>2026-04-07</td><td>66.4</td><td>空头加码</td></tr>
<tr><td class="nm">双周·偏多 0.60/0.40</td><td>+13.00%</td><td>1.2372</td><td class="g">5.54%</td><td>—</td><td>66.8</td><td>多头加码</td></tr>
<tr><td class="nm">双周·化工60·偏空</td><td>+17.24%</td><td>1.3191</td><td>6.52%</td><td>2026-04-07</td><td>66.4</td><td>最高收益 · 双参数叠加</td></tr>
<tr><td class="nm">双周·农产60·偏空</td><td>+14.70%</td><td>1.2697</td><td class="g">4.02%</td><td>2026-04-07</td><td>66.4</td><td>回撤最低档</td></tr>
<tr><td class="nm">双周·无风控对照</td><td>+5.86%</td><td>1.1056</td><td>9.48%</td><td>—</td><td>—</td><td>风控贡献验证</td></tr>
"""

rows_robust = """
<tr><td class="nm">分年收益</td><td>2025 <b>+6.9%</b> / 2026 <b>+18.5%</b></td><td>两年皆正，无单年暴利；2026 增益与动量反转 IC 持续为负一致</td></tr>
<tr><td class="nm">月度分布</td><td>最好月 +5.2% / 最差月 -1.2% / 正收益月占比 71%</td><td>对比周频 +3.8% / -3.0% / 62%：收益更平滑，尾部风险显著收窄</td></tr>
<tr><td class="nm">持仓期 IC（10日）</td><td>combo10 全期 +0.041（2026 +0.070）；mom10 全期 -0.074（2026 -0.067）</td><td>动量修正后的组合排序在持仓周期内有预测力；两周窗口内动量仍是反转信号，惩罚逻辑未失效</td></tr>
<tr><td class="nm">风控 8% 降仓触发</td><td>0 次</td><td>回撤 4.44% 由选仓 + 日频 score 反转风控承担，组合降仓层未激活</td></tr>
<tr><td class="nm">无风控对照</td><td>+5.86% / 回撤 9.48%</td><td>风控贡献约 +8.7pp 收益与 -5.0pp 回撤，三层风控不可去除</td></tr>
<tr><td class="nm">排序改写核查</td><td>改写期占 95% · 平均替换 1.65 腿</td><td>λ=0.6 动量深度参与排序（上轮已确认档位），红线张力见采纳建议</td></tr>
<tr><td class="nm">8% 纪律核对</td><td>推荐 4.44% / 最高收益档 6.52% / 最低回撤档 4.02%</td><td>所有达标档回撤均 ≤ 8%</td></tr>
"""

html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>商品期货横截面多空对冲策略 · 优化验证（2025-2026）</title>
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

<h1>商品期货横截面多空对冲策略 · 优化验证（2025-01-02 ~ 2026-09-30）</h1>
<div class="sub">化工（9 品种）＋ 农产品（10 品种）· 板块内部 3 多 + 3 空 · ATR20 倒数加权 · 总杠杆 1.5 倍 · 四类风控齐备 · 0.025% 单边成本 · 424 交易日 · 目标：年化收益 ≥ 12%</div>

<h2>一、结论速览</h2>
<div class="kpis">
  <div class="kpi"><div class="k">方案A基线（周频·N20·λ0.6）</div><div class="v">+9.30%<span style="font-size:13px">/年</span></div><div class="k">最大回撤 7.30% · 换手 111</div></div>
  <div class="kpi"><div class="k">推荐配置（双周·N20·λ0.6）</div><div class="v g">+14.52%<span style="font-size:13px">/年</span></div><div class="k">最大回撤 4.44% · <b>达标 12%+</b></div></div>
  <div class="kpi"><div class="k">最高收益（双周·化工60·偏空）</div><div class="v g">+17.24%<span style="font-size:13px">/年</span></div><div class="k">最大回撤 6.52% · 双参数叠加需验证</div></div>
  <div class="kpi"><div class="k">最低回撤（双周·农产60·偏空）</div><div class="v g">4.02%</div><div class="k">年化 +14.70% · 条件最优取舍点</div></div>
</div>
<div class="card">
<p><b>答案：能，而且不需要堆叠高风险参数——把调仓频率从周频改成双周一个参数，年化从 <b>+9.30% 提到 +14.52%</b>、最大回撤从 7.30% 收窄到 <b>4.44%</b>，同时满足「年化 ≥ 12%」与「回撤 ≤ 8%」。</b>关键事实：</p>
<ul style="font-size:14px;padding-left:20px">
<li><b>双周调仓是决定性增益且来源健康</b>：换手 111 → 67，成本仅省约 0.96%/年，其余约 +4.2pp 来自持仓周期拉长——慢变量因子（库存/展期）与动量反转信号在两周持仓期内兑现，不再被周频调仓反复打断；月度收益分布更均匀（最差月 -1.2% vs -3.0%，正收益月占比 71%）。</li>
<li><b>两年皆正、非靠单年暴利</b>：2025 +6.9%、2026 +18.5%；持仓期 10 日口径 IC 全期 +0.041、2026 升至 +0.070——组合排序在持仓周期内有真实预测力，动量反转逻辑（mom10 IC -0.074）在 2026 年未失效。</li>
<li><b>风控与双周协同、不可去除</b>：去掉全部日频风控后年化仅 +5.86%、回撤放大到 9.48%（破 8% 红线）——4.44% 回撤是「双周选仓 + 日频 score 反转风控 + 8% 组合降仓」三层共同结果（8% 层 0 次触发，前两层已足够）。</li>
<li><b>红线代价如实披露</b>：动量惩罚保持上轮已确认的 λ=0.6 未加重，但双周下排序改写率 95%、平均替换 1.65 腿——混合排序实质不变，取舍需你拍板（见第六节）。</li>
</ul>
</div>

<h2>二、优化路径：为什么是双周调仓</h2>
<div class="card">
<p>以方案A基线（周频·N20·λ0.6）为原点，五个维度逐档单变量扫描。<b>只有「调仓频率」同时达成年化提升与回撤收窄</b>；动量 λ、腿数、板块杠杆、多空分配在周频下均无法两全。频率是当前策略最大的结构性短板。</p>
<table>
<tr><th>维度</th><th>年化收益</th><th>期末净值</th><th>最大回撤</th><th>回撤日期</th><th>累计换手(×)</th><th>结论</th></tr>
<tr class="hl"><td class="nm">基线 · 周频调仓</td><td>+9.30%</td><td>1.1674</td><td>7.30%</td><td>2026-04-07</td><td>111.4</td><td>—</td></tr>
<tr><td class="nm">频率 · 双周调仓</td><td><b>+14.52%</b></td><td>1.2662</td><td class="g">4.44%</td><td>2026-04-07</td><td>66.6</td><td>唯一「无代价」优化：收益↑ 回撤↓</td></tr>
<tr><td class="nm">频率 · 月频调仓</td><td>+2.73%</td><td>1.0479</td><td class="g">6.74%</td><td>2026-04-07</td><td>33.0</td><td>信号衰减严重 · 已排除</td></tr>
<tr><td class="nm">动量 · N20 λ0.5</td><td>+1.83%</td><td>1.0321</td><td>8.20%</td><td>2026-04-21</td><td>109.9</td><td>惩罚不足</td></tr>
<tr><td class="nm">动量 · N20 λ0.7</td><td>+8.24%</td><td>1.1478</td><td class="g">5.57%</td><td>2026-04-07</td><td>108.7</td><td>回撤改善但收益下滑</td></tr>
<tr><td class="nm">动量 · N20 λ0.8</td><td>+9.55%</td><td>1.1709</td><td class="g">5.50%</td><td>2026-04-07</td><td>108.5</td><td>与基线收益持平，回撤略优</td></tr>
<tr><td class="nm">动量 · N10 / N15</td><td>-2.5% ~ +1.6%</td><td>—</td><td>&gt;8%</td><td>—</td><td>—</td><td>窗口过短 · 全灭 · 已排除</td></tr>
<tr><td class="nm">腿数 · 2+2</td><td>+8.66%</td><td>1.1556</td><td class="g">5.86%</td><td>2026-04-07</td><td>132.8</td><td>回撤优但收益降</td></tr>
<tr><td class="nm">腿数 · 4+4</td><td>-1.85%</td><td>0.9680</td><td>9.64%</td><td>2026-04-07</td><td>97.8</td><td>稀释信号 · 已排除</td></tr>
<tr><td class="nm">板块 · 化工60</td><td>+5.61%</td><td>1.0997</td><td>9.54%</td><td>2026-04-07</td><td>112.9</td><td>破坏对冲结构</td></tr>
<tr><td class="nm">板块 · 农产60</td><td>+7.69%</td><td>1.1378</td><td class="g">5.05%</td><td>2026-04-07</td><td>109.9</td><td>回撤优但收益降</td></tr>
<tr><td class="nm">多空 · 偏空 0.30/0.45</td><td>+5.21%</td><td>1.0924</td><td>9.33%</td><td>2026-04-07</td><td>111.2</td><td>周频下无增益</td></tr>
<tr><td class="nm">多空 · 偏多 0.45/0.30</td><td>+8.92%</td><td>1.1603</td><td class="g">5.77%</td><td>2026-07-06</td><td>111.5</td><td>收益仍低于基线</td></tr>
</table>
<p class="note">✔ 绿色 = 回撤 ≤ 8%；单变量扫描结论：<b>双周调仓是唯一「收益和回撤双改善」的维度</b>，其余维度在周频框架下均无法补足收益。</p>
</div>

<h2>三、双周框架下的联合优化（15 组）</h2>
<div class="card">
<table>
<tr><th>组合</th><th>年化收益</th><th>期末净值</th><th>最大回撤</th><th>回撤日期</th><th>累计换手(×)</th><th>说明</th></tr>
__ROWS_BW__
</table>
<p class="note">全部组合：双周调仓 · N20 动量惩罚（N15 组例外）· 四类风控齐全 · 0.025% 单边成本；推荐与激进档见第六节。</p>
</div>

<h2>四、净值与回撤曲线</h2>
<div class="card"><p style="margin:0 0 4px"><b>累计净值（1.5 倍杠杆 · 含风控与成本）</b></p><div id="chart"></div><div class="legend" id="legend"></div></div>
<div class="card"><p style="margin:0 0 4px"><b>组合回撤深度（%）</b>——推荐配置全程未触及 8% 纪律线</p><div id="chart-dd"></div><div class="legend" id="legend-dd"></div></div>

<h2>五、稳健性验证（推荐配置：双周·N20·λ0.6）</h2>
<div class="card">
<table>
<tr><th>检验项</th><th>结果</th><th>解读</th></tr>
__ROWS_ROBUST__
</table>
</div>

<h2>六、采纳建议</h2>
<div class="card">
<table><tr><th>选项</th><th>配置</th><th>预期效果</th><th>代价 / 备注</th></tr>
<tr><td class="nm"><span class="tag t-gr">推荐</span>主配置</td><td><b>双周调仓 · N20 · λ0.6 · 板块/多空等权</b></td><td>年化 <b>+14.52%</b>，回撤 <b>4.44%</b>，达标 12%+</td><td>只改频率一个参数，改动最小、自由度最低、稳健性验证最充分</td></tr>
<tr><td class="nm"><span class="tag t-bl">稳健增强</span>求最低回撤</td><td>双周 · 农产60 · 偏空 0.40/0.60</td><td>年化 +14.70%，回撤 <b>4.02%</b></td><td>回撤最低档；接受空头桶依赖</td></tr>
<tr><td class="nm"><span class="tag t-rd">激进档</span>追求收益上限</td><td>双周 · 化工60 · 偏空 0.40/0.60</td><td>年化 <b>+17.24%</b>，回撤 6.52%</td><td>双参数叠加，样本内过拟合风险最高，上线前须 2026Q4 新数据滚动验证</td></tr>
<tr><td class="nm">明确不采纳</td><td>N10/N15 窗口 · 月频 · 4+4 腿 · 周频下一切杠杆分配</td><td>均差于或等价于基线</td><td>已排除，不再重复尝试</td></tr>
</table>
<p><b>操作建议：</b>① 主配置先上线（频率是机制性优化、过拟合风险最低）；② 若 2026Q4 回撤依然温和，再分步叠加「农产60」或「偏空」任一单参数，一次只加一个便于归因；③ 化工60+偏空 +17% 档暂缓，待样本外验证动量 IC 与双周优势延续后启用；④ 持续监控持仓期 10 日动量 IC——若转正（反转逻辑失效），λ 回退至 0.3 并接受收益回落；⑤ 每次调仓保持板块内 3 多 + 3 空、杠杆 1.5 上限不变。</p>
</div>

<h2>七、数据缺口与局限</h2>
<div class="card">
<p class="note">与前两份报告一致：TA 库存 2025-11-19 后、AP 2026-05-29 后缺失，此后不入池；DCE 仓单不可得，以 fx168 社会库存替代；现货价不可得，基差因子用近月基差率代理；2026-09-25 全市场数据缺失。双周框架收益集中度高于周频（2026 年贡献约 65% 累计收益），若 2026Q4 出现单边趋势市，双周+风控的优势窗口可能收窄；λ0.6 组合排序仍含显著动量成分，与「禁止以涨跌幅作排序主依据」的方法论红线存在张力，属上轮已确认取舍的延续而非新增突破。</p>
</div>

<div class="footer">由回测引擎 backtest_opt.py 生成 · 基线复现 1.1674 / +9.30% / 7.30% 与既有报告一致 · 27 组扫描（12 单变量 + 15 联合）· 生成时间 2026-10-02</div>
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
makeChart("chart","nav",0.95,1.36,5, "");
const lg=document.getElementById("legend");
lg.innerHTML=DATA.map(s=>'<span><i class="dot" style="background:'+s.color+'"></i>'+s.name+'</span>').join('');
makeChart("chart-dd","dd",-11,1,6, "%");
const lg2=document.getElementById("legend-dd");
lg2.innerHTML=DATA.map(s=>'<span><i class="dot" style="background:'+s.color+'"></i>'+s.name+'</span>').join('');
</script>
</body></html>
"""

html = html.replace("__ROWS_BW__", rows_bw).replace("__ROWS_ROBUST__", rows_robust)
html = html.replace("__DATA_JS__", data_js)
out = "商品期货横截面多空对冲策略-优化验证-2025-2026.html"
open(out, "w", encoding="utf-8").write(html)
print("HTML 已写入:", out, "| 大小:", len(html), "字符")