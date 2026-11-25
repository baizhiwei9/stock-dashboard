# -*- coding: utf-8 -*-
"""生成 HTML 回测报告: 商品期货横截面多空对冲策略 2025-2026"""
import pandas as pd, numpy as np, json

NAV_R=pd.read_csv("data/norm/nav_risk.csv", parse_dates=["date"]).set_index("date")
NAV_N=pd.read_csv("data/norm/nav_norisk.csv", parse_dates=["date"]).set_index("date")
stat=json.load(open("data/norm/stats.json"))
sens=pd.read_csv("data/norm/sens.csv").rename(columns={"降仓比例":"保留比例"})
det=json.load(open("data/norm/portfolio_detail.json"))
disp=json.load(open("data/norm/dispersion.json"))
inv_alerts=pd.read_csv("data/norm/inv_alerts.csv")

# ---------- SVG 工具 ----------
def svg_line_chart(series_list, labels, colors, height=260, width=860, ylabel="", log=False):
    pad_l,pad_r,pad_t,pad_b = 8,10,10,40
    W,H = width,height
    xs_all=[]
    for s in series_list:
        s=s.dropna()
        if len(s): xs_all.append((s.index-s.index[0]).days)
    if not xs_all: return ""
    xmax=max(max(x) for x in xs_all)
    ys=[]
    for s in series_list:
        s=s.dropna()
        if len(s): ys.append(float(s.max()))
    ymin=min(float(s.min()) for s in series_list if len(s))
    ymax=max(ys)
    rng=ymax-ymin
    if rng==0: rng=1
    ymin-=rng*0.05; ymax+=rng*0.05; rng=ymax-ymin
    def X(days): return pad_l+(days/xmax)*(W-pad_l-pad_r)
    def Y(v): return pad_t+(1-(v-ymin)/rng)*(H-pad_t-pad_b)
    out=[f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;font-family:sans-serif">']
    # 网格
    for i in range(5):
        gy=pad_t+(H-pad_t-pad_b)*i/4
        out.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{W-pad_r}" y2="{gy:.1f}" stroke="#e8e8e8" stroke-width="1"/>')
        out.append(f'<text x="{pad_l+2}" y="{gy-3:.1f}" font-size="10" fill="#999">{ymax-i*(rng)/4:.3f}</text>')
    for s,lab,col in zip(series_list,labels,colors):
        s=s.dropna()
        if len(s)==0: continue
        d0=s.index[0]
        pts=" ".join(f"{X((d-d0).days):.1f},{Y(v):.1f}" for d,v in s.items())
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.6"/>')
        # 图例
    lx=pad_l+10
    for lab,col in zip(labels,colors):
        out.append(f'<rect x="{lx}" y="{pad_t-2}" width="12" height="8" fill="{col}"/>')
        out.append(f'<text x="{lx+15}" y="{pad_t+5}" font-size="11" fill="#333">{lab}</text>')
        lx+=75
    # x轴刻度
    for d in range(0,xmax+1,180):
        out.append(f'<text x="{X(d)}" y="{H-14}" font-size="10" fill="#999" text-anchor="middle">{d//365+2025}</text>')
    out.append(f'<text x="{W-16}" y="{H-25}" font-size="10" fill="#666" text-anchor="end">{ylabel}</text>')
    out.append("</svg>")
    return "\n".join(out)

def svg_bar(data, labels, colors, unit="", height=240, width=860, fmt="{:.4f}"):
    pad_l,pad_r,pad_t,pad_b=40,10,10,40
    W,H=width,height
    vals=[float(v) for v in data]
    vmax=max(abs(v) for v in vals) if vals else 1
    rng=vmax*1.15
    mid=pad_l+(W-pad_l-pad_r)*(0 if rng<=0 else .5)
    def X(i): return pad_l+(W-pad_l-pad_r)*(i+0.5)/len(vals)
    out=[f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">']
    # 零线(中点)
    out.append(f'<line x1="{pad_l}" y1="{pad_t+(H-pad_t-pad_b)/2}" x2="{W-pad_r}" y2="{pad_t+(H-pad_t-pad_b)/2}" stroke="#aaa" stroke-width="1"/>')
    bw=(W-pad_l-pad_r)*0.6/len(vals)
    for i,(v,lab,col) in enumerate(zip(vals,labels,colors)):
        h=(abs(v)/rng)*(H-pad_t-pad_b)/2
        cx=X(i)
        y0=pad_t+(H-pad_t-pad_b)/2
        top=y0-(h if v>=0 else 0); bot=y0+(h if v<0 else 0)
        out.append(f'<rect x="{cx-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{bot-top:.1f}" fill="{col}" rx="1"/>')
        out.append(f'<text x="{cx:.1f}" y="{(top-4) if v>=0 else (bot+12):.1f}" font-size="10" fill="#333" text-anchor="middle">{fmt.format(v)}</text>')
        out.append(f'<text x="{cx:.1f}" y="{H-18}" font-size="10" fill="#555" text-anchor="middle" transform="rotate(-25 {cx:.1f} {H-18})">{lab}</text>')
    out.append("</svg>")
    return "\n".join(out)

# ---------- 数据准备 ----------
dd_r=(NAV_R["nav"].cummax()-NAV_R["nav"])/NAV_R["nav"].cummax()
dd_n=(NAV_N["nav"].cummax()-NAV_N["nav"])/NAV_N["nav"].cummax()
# 归因重算(四桶复利)
p=pd.read_csv("data/norm/factors.csv", parse_dates=["date"])
chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"]=np.where(p["variety"].isin(chem),"化工","农产品")
p=p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"]=1.0/p["atr20"]
sco=p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()
BASE=0.375
dates_all=np.sort(p["date"].unique()); start=pd.Timestamp("2025-01-01")
dates=[d for d in dates_all if d>=start]
wdf=pd.DataFrame({"date":dates}); wdf["week"]=pd.to_datetime(wdf["date"]).dt.to_period("W-FRI")
reb=set(wdf.groupby("week")["date"].max().values)
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
buckets={k:[] for k in ["化工多","化工空","农产多","农产空"]}; pos={}
for i,d in enumerate(dates):
    if d in reb or i==0:
        new={}
        for sec,lm,ls in [("化工","化工多","化工空"),("农产品","农产多","农产空")]:
            vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])]
            if len(vs)<4: continue
            n=3 if len(vs)>=6 else 2
            srt=sorted(vs,key=lambda v:sco.loc[d,v],reverse=True)
            for grp,lab in [(srt[:n],lm),(srt[-n:],ls)]:
                ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                if ws.sum()==0: continue
                for v,wv in (ws/ws.sum()*BASE).items(): new[v]=(wv,lab)
        pos={v:t for v,t in new.items()}
    for lab in buckets: buckets[lab].append(0.0)
    for v,(tw,lab) in pos.items():
        r=ret.loc[d,v] if d in ret.index else np.nan
        if pd.notna(r): buckets[lab][-1]+=tw*r
def cum(arr):
    n=1.0
    for r in arr: n*=(1+r)
    return n
yrs=((pd.Timestamp(dates[-1])-pd.Timestamp(dates[0])).days)/365.25
attr={k:(cum(v), cum(v)**(1/yrs)-1) for k,v in buckets.items()}

ev=pd.read_csv("data/norm/events.csv", parse_dates=["date"])
evcnt=ev.groupby("type").size().to_dict()

# 分化度: 最新
disp_flt={"化工":[v for v in disp["化工"] if v is not None],"农产品":[v for v in disp["农产品"] if v is not None]}
disp_mean={"化工":float(np.mean(disp_flt["化工"])),"农产品":float(np.mean(disp_flt["农产品"]))}
disp_latest={"化工":disp["化工"][-1],"农产品":disp["农产品"][-1]}

# IC 数据（预计算值, 由脚本复算）
def spearman(a,b):
    a=pd.Series(a); b=pd.Series(b)
    return a.rank().corr(b.rank())
p["fwd_wk"]=p.groupby("variety")["ret_hold"].shift(-1).rolling(5,min_periods=1).sum()
p["week"]=p["date"].dt.to_period("W-FRI")
wk=p[p["date"]==p.groupby(["variety","week"])["date"].transform("max")]
bt=wk[wk["date"]>=start]
ic_res={}
for f in ["score","z_inv","z_roll","z_basis","z_droll"]:
    ics=[]
    for d,g in bt.groupby("date"):
        g=g.dropna(subset=[f,"fwd_wk"])
        if len(g)>=4: ics.append(spearman(g[f],g["fwd_wk"]))
    s=pd.Series(ics)
    ic_res[f]=(float(s.mean()), float(s.mean()/s.std()*np.sqrt(len(s))) if s.std()>0 else 0.0, float((s>0).mean()))
ic_25={"score":ic_res["score"]}; 
ics25=[]
for d,g in bt[bt["date"]<"2026-01-01"].groupby("date"):
    g=g.dropna(subset=["score","fwd_wk"])
    if len(g)>=4: ics25.append(spearman(g["score"],g["fwd_wk"]))
s25=pd.Series(ics25)
ics26=[]
for d,g in bt[bt["date"]>="2026-01-01"].groupby("date"):
    g=g.dropna(subset=["score","fwd_wk"])
    if len(g)>=4: ics26.append(spearman(g["score"],g["fwd_wk"]))
s26=pd.Series(ics26)
ic_year={"2025":(float(s25.mean()),float(s25.mean()/s25.std()*np.sqrt(len(s25))) if s25.std()>0 else 0,float((s25>0).mean())),
         "2026":(float(s26.mean()),float(s26.mean()/s26.std()*np.sqrt(len(s26))) if s26.std()>0 else 0,float((s26>0).mean()))}

# 敏感性整理
sens=sens.sort_values(["触发线","保留比例"])
sens_rows=[]
for _,r in sens.iterrows():
    sens_rows.append((f"{r['触发线']:.0%} / 保留{r['保留比例']:.0%}",f"{r['maxdd']:.2%}",f"{r['年化']:.2%}",f"{r['final']:.3f}"))
sens_table="".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a,b,c,d in sens_rows)

# 组合明细表格(最新一期)
def port_table(d,sec):
    d0=det[d]
    rows=""
    for side,nm in [("long","做多"),("short","做空")]:
        for it in d0[sec][side]:
            rows+=f'<tr><td>{it["variety"]}</td><td>{nm}</td><td>{it["score"]:+.2f}</td><td>{it["w"]:.2%}</td><td>{it["atr"]:.0f}</td></tr>'
    return rows

# 因子明细最新一期
latest="2026-09-24"
p_lat=p[p["date"]==pd.Timestamp(latest)]
def fac_table(sec):
    g=p_lat[p_lat["sector"]==sec].sort_values("score",ascending=False)
    return "".join(f'<tr><td>{r.variety}</td><td>{r.z_inv:+.2f}</td><td>{r.z_roll:+.2f}</td><td>{r.z_basis:+.2f}</td><td>{r.z_droll:+.2f}</td><td><b>{r.score:+.2f}</b></td></tr>' for r in g.itertuples())

# 库存审查表
alerts_tbl=""
if len(inv_alerts):
    alerts_tbl="".join(f'<tr><td>{r.variety}</td><td>{str(r.date)[:10]}</td><td>{r.stock:.0f}</td><td>{r["分位"]:.0%}</td><td>{r["周涨1"]:.1f}%</td><td>{r["周涨2"]:.1f}%</td></tr>' for _,r in inv_alerts.iterrows())

# 事件统计表
ev_tbl="".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k,v in evcnt.items())

r_ev=pd.read_csv("data/norm/events.csv", parse_dates=["date"])
r_ev2026=r_ev[r_ev["date"].dt.year==2026]
# 冲突/风险品种: 因子极值但组合外
risk_vars=[]
for sec in ["化工","农产品"]:
    g=p_lat[p_lat["sector"]==sec].sort_values("score")
    top=g.iloc[:3]; bot=g.iloc[-3:]
    for r in g.itertuples():
        if r.variety in top["variety"].values and (r.z_roll<0 or r.z_inv<0):
            risk_vars.append((r.variety,"做多组但展期/库存因子为负",f'z_roll={r.z_roll:+.2f}, z_inv={r.z_inv:+.2f}'))

# ---------- HTML ----------
risk_html='<br>'.join(f'<li><b>{a}</b>：{b}（{c}）</li>' for a,b,c in risk_vars) or "<li>无显著矛盾信号</li>"

nav_risk=NAV_R["nav"]; nav_norisk=NAV_N["nav"]
chart1=svg_line_chart([nav_risk,nav_norisk],["含风控净值","无风控基线"],["#1f77b4","#c0392b"])
chart2=svg_line_chart([dd_r],["含风控回撤"],["#d62728"],ylabel="回撤")
chart3=svg_bar([attr["化工多"][1],attr["化工空"][1],attr["农产多"][1],attr["农产空"][1]],
               ["化工多","化工空","农产多","农产空"],["#2ca02c","#d62728","#98df8a","#ff9896"],fmt="{:.2%}")
chart4=svg_bar([ic_res["z_inv"][0],ic_res["z_roll"][0],ic_res["z_basis"][0],ic_res["z_droll"][0],ic_res["score"][0]],
               ["库存z","展期z","基差z","结构z","综合score"],["#1f77b4","#ff7f0e","#2ca02c","#9467bd","#333333"],fmt="{:.3f}")

r=stat["risk"]; n=stat["norisk"]
html=f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>商品期货横截面多空对冲策略回测报告 2025-2026</title>
<style>
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f7f8fa;color:#222;line-height:1.65}}
.wrap{{max-width:1080px;margin:0 auto;padding:24px 28px 60px}}
h1{{font-size:26px;margin:8px 0 4px}} h2{{font-size:19px;border-left:4px solid #1f77b4;padding-left:10px;margin:34px 0 12px}}
h3{{font-size:15px;margin:18px 0 8px}} .sub{{color:#888;font-size:13px;margin-bottom:6px}}
.card{{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:18px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
.kpi{{flex:1;min-width:170px;background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:14px 16px}}
.kpi .v{{font-size:24px;font-weight:700}} .kpi .l{{color:#777;font-size:12px;margin-top:2px}}
.kpi.red .v{{color:#c0392b}} .kpi.blue .v{{color:#1f77b4}} .kpi.green .v{{color:#2ca02c}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
th,td{{border:1px solid #e2e5e9;padding:6px 9px;text-align:left}}
th{{background:#f3f5f8;font-weight:600}}
tr:nth-child(even) td{{background:#fafbfc}}
.note{{background:#fff8e6;border:1px solid #f0e3b8;border-radius:8px;padding:10px 14px;font-size:13px;color:#7a6a2e;margin:12px 0}}
.warn{{background:#fdf0ef;border:1px solid #f0c8c3;border-radius:8px;padding:10px 14px;font-size:13px;color:#8c3b33;margin:12px 0}}
.ok{{background:#eef8ee;border:1px solid #bfe3bf;border-radius:8px;padding:10px 14px;font-size:13px;color:#2f6b2f;margin:12px 0}}
ul{{margin:6px 0;padding-left:22px}} li{{margin:4px 0}}
</style></head><body><div class="wrap">

<h1>商品期货横截面多空对冲策略 · 回测报告</h1>
<div class="sub">化工/农产品板块内对冲 · 四因子评分体系 · 2025-01-02 至 2026-09-30（424 个交易日 / 1.74 年）· 最新数据截至 2026-09-30</div>

<div class="kpis">
<div class="kpi red"><div class="v">{r['maxdd']:.1%}</div><div class="l">最大回撤（含风控）· 2026-08-05</div></div>
<div class="kpi red"><div class="v">{n['maxdd']:.1%}</div><div class="l">最大回撤（无风控基线）</div></div>
<div class="kpi"><div class="v">{r['ann']:.2%}</div><div class="l">年化收益（含风控，含成本）</div></div>
<div class="kpi"><div class="v">{r['final']:.3f}</div><div class="l">期末净值（1.5x 杠杆/多空合计）</div></div>
</div>

<h2>一、核心结论</h2>
<div class="card">
<h3>1. 年化的多空最大回撤是多少？</h3>
<ul>
<li><b>按您给定的完整风控规则（回撤&gt;8% 全组合降仓至 50%）：最大回撤 11.40%</b>，发生在 2026-08-05（届时已处于降仓后半仓状态，杠杆 0.75）。</li>
<li>完全不去触发回撤风控的同口径基线：最大回撤 14.75%（2026-07-06）。风控把最大回撤压低了约 3.4 个百分点。</li>
<li>分自然年看（含风控）：2025 年内最大回撤 7.57%，2026 年内 5.70%；全程 11.4% 的深回撤主要在 2025 年末至 2026 年 3 月 2 日触发降仓前累积（净值 1.00 → 0.91），触发后仅再扩大约 2.4 个百分点。</li>
</ul>
<h3>2. 最大回撤能不能控制住？——按既有规则不能，需要把触发线提前到 5% 且近乎清仓</h3>
<ul>
<li><b>您给定的"回撤&gt;8% 降仓 50%"是事后确认式风控：触及 8% 时回撤已成事实（本次触发日 2026-03-02 实际已回撤 9.09%），降仓 50% 只能放缓后续亏损斜率，无法把最大回撤收进 8% 内。</b></li>
<li>参数敏感性扫描（触发线 × 降仓后保留仓位）表明：<b>要控制到 8% 以内，触发线须提前到 5% 且降仓后仅保留 20% 以内仓位（5% 触发 + 清仓 → 最大回撤 7.30%；5% + 保留 20% → 8.25%）</b>；6% 触发 + 清仓 → 8.39%，仍略超。</li>
<li>代价：触发越早、降仓越狠，中后期收益弹性越低（本次回测降仓 50% 后直至样本末未收复触发点，后半程长期半仓运行）。</li>
</ul>
<div class="warn"><b>更根本的问题：策略本身在 2025-2026 样本内未产生正 alpha。</b>综合 score 的周横截面 IC 仅 +0.020（ICIR 0.65），2025 年 +0.044、2026 年转负 -0.013；四桶归因仅"农产品空头"贡献正收益（+3.84%/年），其余三桶均小幅为负。年化 -4.3% 主要不是回撤控制失效，而是多空信号在样本外没有稳定区分度。回撤控制讨论建立在"一个本身在亏钱的策略上少亏"。</div>
</div>

<h2>二、方法论与数据口径</h2>
<div class="card">
<h3>评分体系（严格执行）</h3>
<p><code>score = 0.35×z(库存反向) + 0.35×z(展期收益) + 0.15×z(基差) + 0.15×z(结构变化)</code>；库存为近 5 年同期分位取负（低库存=正分），展期收益为主力与次近月价差年化（back 为正），基差用近月基差率（现货价不可得），结构变化为展期收益 5 日变化、各因子 120 日滚动 z-score。</p>
<h3>品种池（19 个）</h3>
<table><tr><th>板块</th><th>品种</th></tr>
<tr><td>化工（9）</td><td>MA 甲醇 / TA PTA / SA 纯碱 / UR 尿素 / V PVC / PP 聚丙烯 / L 聚乙烯 / EG 乙二醇 / EB 苯乙烯</td></tr>
<tr><td>农产品（10）</td><td>M 豆粕 / RM 菜籽粕 / Y 豆油 / P 棕榈油 / OI 菜籽油 / SR 白糖 / CF 棉花 / C 玉米 / AP 苹果 / JD 鸡蛋</td></tr></table>
<h3>交易与风控规则（严格执行）</h3>
<p>两板块内部各自排序，互不跨板块对冲；每板块做多 score 最高 3 个、做空最低 3 个（板块有效 &lt;6 时 2+2）；仓位 ATR(20) 倒数加权；每板块多空各 0.375 名义、多空合计杠杆 1.5；周频调仓（每周最后交易日）、日频风控；换仓成本单边 0.025%（佣金+冲击），周内风控动作不再额外计成本（偏保守地少扣）。</p>
<ul>
<li>score 反转相对调仓基准：&gt;1σ 减仓 50%、&gt;2σ 全平（σ=该品种 60 日滚动 score 标准差，均值 0.59）</li>
<li>back 转 contango（建仓时展期为正、当前转负）：强制降至原仓位 30%</li>
<li>组合回撤 &gt;8%：全组合降仓至 50%，净值收复触发点前保持</li>
<li>库存证伪：低库存（分位&lt;20%）连续 2 周累库且速率超季节性 → 触发审查（仅审查标记，不自动平仓）</li>
</ul>
<div class="note"><b>数据缺口与替代口径（如实披露）</b>：① TA 库存数据止于 2025-11-19、AP 止于 2026-05-29，此后该两品种无法入池（2026 年 TA 缺席化工排序、AP 缺席农产品排序）；② DCE 仓单（V/PP/L/EG/EB/M/Y/P 等）官网与第三方通道 2026 年均被封锁，库存因子采用 fx168 汇总的社会/厂库+交易所库存序列替代；③ 现货价不可得，基差因子以"近月合约价 - 主力价"的近月基差率代理；④ 2026-09-25 全市场行情缺失（数据源当日无返回）。</div>
</div>

<h2>三、因子明细与多空组合（最新一期 {latest}）</h2>
<div class="card">
<h3>化工板块因子明细</h3>
<table><tr><th>品种</th><th>库存z</th><th>展期z</th><th>基差z</th><th>结构z</th><th>综合score</th></tr>{fac_table("化工")}</table>
<h3>农产品板块因子明细</h3>
<table><tr><th>品种</th><th>库存z</th><th>展期z</th><th>基差z</th><th>结构z</th><th>综合score</th></tr>{fac_table("农产品")}</table>
<h3>化工组合</h3>
<table><tr><th>品种</th><th>方向</th><th>score</th><th>仓位(名义)</th><th>ATR20</th></tr>{port_table(latest,"化工")}</table>
<h3>农产品组合</h3>
<table><tr><th>品种</th><th>方向</th><th>score</th><th>仓位(名义)</th><th>ATR20</th></tr>{port_table(latest,"农产品")}</table>
<div class="note">ATR 倒数加权：低波动品种（UR、C、RM）获得最高权重；板块内多空名义各 0.375，两板块合计 1.5 倍。注意 RM 以 -0.02 的弱负分入选农产品做多组，反映该板块当前分化不足、边际信号弱（见第四节分化度）。</div>
<h3>组合演进（代表性调仓日）</h3>
<table><tr><th>日期</th><th>化工做多</th><th>化工做空</th><th>农产做多</th><th>农产做空</th></tr>
{''.join(f"<tr><td>{d}</td><td>{'、'.join(x['variety'] for x in det[d]['化工']['long'])}</td><td>{'、'.join(x['variety'] for x in det[d]['化工']['short'])}</td><td>{'、'.join(x['variety'] for x in det[d]['农产品']['long'])}</td><td>{'、'.join(x['variety'] for x in det[d]['农产品']['short'])}</td></tr>" for d in det)}
</table>
</div>

<h2>四、板块分化度评估（对冲空间）</h2>
<div class="card">
<table><tr><th>板块</th><th>全程均值</th><th>分化度最高（周）</th><th>最近一期 {latest}</th></tr>
<tr><td>化工</td><td>{disp_mean['化工']:.3f}</td><td>{max(disp['化工']):.2f}</td><td>{disp['化工'][-1]:.2f}</td></tr>
<tr><td>农产品</td><td>{disp_mean['农产品']:.3f}</td><td>{max(disp['农产品']):.2f}</td><td>{disp['农产品'][-1]:.2f}</td></tr></table>
<p>经验判断：（score 标准差 &gt;0.7 空间充足 / 0.4–0.7 中等 / &lt;0.4 空间不足）。<b>2026-03-27 曾出现两板块同时低于 0.40 的分化极低时刻</b>（化工 0.38、农产 0.35），当时按规则仍执行 3+3 强配，属于"分化度极低但用户未要求跳过"的情形——这会放大噪音换手，也是该时段风控事件密集的原因之一。最近一期分化度回升（化工 1.06、农产 0.87），对冲空间恢复。</p>
<p>分化度序列（周频，score 标准差）：</p>
{svg_line_chart([pd.Series(disp['化工'],index=pd.date_range(pd.Timestamp(disp['date'][0]),periods=len(disp['化工']))),pd.Series(disp['农产品'],index=pd.date_range(pd.Timestamp(disp['date'][0]),periods=len(disp['农产品'])))],["化工分化度","农产品分化度"],["#1f77b4","#ff7f0e"])}
</div>

<h2>五、回测绩效</h2>
<div class="card">
<h3>净值曲线（含风控 vs 无风控基线，均已扣换仓成本）</h3>
{chart1}
<h3>回撤曲线（含风控）</h3>
{chart2}
<table><tr><th>口径</th><th>期末净值</th><th>累计收益</th><th>年化</th><th>最大回撤</th><th>回撤日期</th></tr>
<tr><td><b>含风控（您的规则全集）</b></td><td>{r['final']:.3f}</td><td>{r['cum']:.2%}</td><td>{r['ann']:.2%}</td><td><b>{r['maxdd']:.1%}</b></td><td>{r['maxdd_date']}</td></tr>
<tr><td>无回撤/无score风控基线</td><td>{n['final']:.3f}</td><td>{n['cum']:.2%}</td><td>{n['ann']:.2%}</td><td>{n['maxdd']:.1%}</td><td>{n['maxdd_date']}</td></tr></table>
<ul>
<li>累计换手（下单名义）约 123 倍净值，累计成本约 3.1%（单边 0.025%），占组合负收益的重要部分。</li>
<li>风控对收益的影响：降仓时点恰好避开了 2026 年 3 月后段的部分下跌，但也错过了部分反弹（触发后长期半仓），年化差异仅 -0.2pp，本质是"收益换回撤"。 </li>
</ul>
<h3>回撤风控参数敏感性（触发线 × 降仓后保留比例 → 最大回撤/年化）</h3>
<table><tr><th>触发线 / 保留比例</th><th>最大回撤</th><th>年化</th><th>期末净值</th></tr>{sens_table}</table>
<div class="ok"><b>解读：</b>您的原规则（8% 触发 / 保留 50%）→ 最大回撤 11.4%。要把最大回撤真正控在 8% 内，需"5% 触发 + 清仓（最大回撤 7.3%）"或"5% 触发 + 保留 20%（8.25%）"；任何触发线 ≥6% 都救不回已形成的深度回撤。</div>
</div>

<h2>六、风控校验（四类规则逐条核对）</h2>
<div class="card">
<table><tr><th>风控类型</th><th>触发次数</th></tr>{ev_tbl}</table>
<ul>
<li><b>score 反转 1σ/2σ（367+150=517 次）</b>：覆盖全部 19 品种，频率极高——周 Δscore 的标准差（0.82）大于 60 日 σ 的中位数（0.58），意味着信号周间抖动本身就超过风控阈值，组合经常在调仓后 1-2 日就被砍仓。抽样复核（MA/V/SA 等）事件明细与原始 score 数据吻合。</li>
<li><b>back→contango 降 30%（186 次）</b>：2025-2026 商品期限结构频繁在 back/contango 间翻转（多数品种 roll_annual 围绕 0 震荡），该规则将大量多头暴露长期压制在 30% 水平，直接削弱了展期因子的多头表达，但也避免了结构反转期的持有亏损。抽样复核（EB 2025-01 roll 0.13→-0.06）吻合。</li>
<li><b>组合回撤降仓（1 次）</b>：2026-03-02 触发（回撤 9.09%），杠杆 1.5→0.75，样本末仍未收复触发点、长期半仓运行；事后核算其将最大回撤从 14.75% 收敛至 11.40%。</li>
<li><b>库存证伪审查（{len(inv_alerts)} 条）</b>：低库存（分位&lt;20%）连续 2 周累库且速率超季节性。主要集中在 CF（2025 年上半年持续低库存累库）、SR（2025 年末-2026 年中多次）、C 与 P 各 1 次。这些审查点不应立即翻空，但需人工确认累库是否构成库存周期拐点。</li>
</ul>
<h3>库存证伪审查清单</h3>
<table><tr><th>品种</th><th>日期</th><th>库存</th><th>5年分位</th><th>第1周</th><th>第2周</th></tr>{alerts_tbl}</table>
<h3>四桶收益归因（复利，未扣成本）</h3>
{chart3}
<table><tr><th>桶</th><th>最终净值</th><th>年化贡献</th></tr>
<tr><td>化工多</td><td>{attr['化工多'][0]:.3f}</td><td>{attr['化工多'][1]:.2%}</td></tr>
<tr><td>化工空</td><td>{attr['化工空'][0]:.3f}</td><td>{attr['化工空'][1]:.2%}</td></tr>
<tr><td>农产多</td><td>{attr['农产多'][0]:.3f}</td><td>{attr['农产多'][1]:.2%}</td></tr>
<tr><td>农产空</td><td>{attr['农产空'][0]:.3f}</td><td>{attr['农产空'][1]:.2%}</td></tr></table>
<p>唯一正贡献来自<b>农产品空头</b>（+3.84%/年），其余三桶均小幅亏损——特别是化工/农产两个多头桶在低分化时段频繁追高信号，"最高 3 个"里混入了弱负分品种（如最新一期 RM -0.02）。</p>
<h3>因子 IC（周横截面秩相关，预测下周收益）</h3>
{chart4}
<table><tr><th>因子</th><th>周IC均值</th><th>ICIR</th><th>IC&gt;0占比</th></tr>
<tr><td>库存z</td><td>{ic_res['z_inv'][0]:+.3f}</td><td>{ic_res['z_inv'][1]:.2f}</td><td>{ic_res['z_inv'][2]:.0%}</td></tr>
<tr><td>展期z</td><td>{ic_res['z_roll'][0]:+.3f}</td><td>{ic_res['z_roll'][1]:.2f}</td><td>{ic_res['z_roll'][2]:.0%}</td></tr>
<tr><td>基差z</td><td>{ic_res['z_basis'][0]:+.3f}</td><td>{ic_res['z_basis'][1]:.2f}</td><td>{ic_res['z_basis'][2]:.0%}</td></tr>
<tr><td>结构z</td><td>{ic_res['z_droll'][0]:+.3f}</td><td>{ic_res['z_droll'][1]:.2f}</td><td>{ic_res['z_droll'][2]:.0%}</td></tr>
<tr><td><b>综合score</b></td><td>{ic_res['score'][0]:+.3f}</td><td>{ic_res['score'][1]:.2f}</td><td>{ic_res['score'][2]:.0%}</td></tr></table>
<table><tr><th>年度</th><th>综合score周IC</th><th>ICIR</th><th>IC&gt;0占比</th></tr>
<tr><td>2025</td><td>{ic_year['2025'][0]:+.3f}</td><td>{ic_year['2025'][1]:.2f}</td><td>{ic_year['2025'][2]:.0%}</td></tr>
<tr><td>2026</td><td>{ic_year['2026'][0]:+.3f}</td><td>{ic_year['2026'][1]:.2f}</td><td>{ic_year['2026'][2]:.0%}</td></tr></table>
<div class="note"><b>只有库存因子（ICIR 1.17）显著且稳定为正</b>；展期收益（0.15）、基差（0.20）在样本内几乎无预测力——这是"做多展期顺风品种"在 2025-2026 横截面上没有兑现收益的直接证据，也解释了 back→contango 风控高频触发的原因。</div>
</div>

<h2>七、风险提示</h2>
<div class="card">
<ul>
<li><b>因子失效—信号矛盾：</b>2026 年综合 score IC 转负（-0.013），最新一期做多组仍混入负分品种；展期与基差双因子 ICIR&lt;0.3 基本失效，仅库存单因子独撑。</li>
<li><b>风控过度敏感：</b>score 反转风控 517 次触发说明信号周间噪声 &gt; 阈值（Δstd 0.82 vs σ中位 0.58），当前阈值参数下组合实际持仓远低于名义 1.5 倍，多数时间在 0.7-1.1 之间摆动。</li>
<li><b>分化度极低时刻：</b>2026-03-27 两板块分化度同时 &lt;0.4，仍按 3+3 强配会放大噪声换手；按方法论应在分化度 &lt;0.4 时降杠杆或跳过该板块。</li>
<li><b>数据缺口</b>：TA（2025-11 后）、AP（2026-05 后）无法入池；DCE 系品种库存以社会库存替代，与交易所仓单口径存在偏差；9-25 缺失一个交易日。</li>
<li><b>极端行情：</b>2026 年二季度鸡蛋（JD2608 由 4300 涨至 4800+、展期大幅转 contango）、棕榈等远月升水扩张，空头端若未及时触发 back 风控将承受挖坑；本次回测中农产品空头恰恰是唯一正贡献桶，若结构翻转风控失效，该桶回吐风险最大。</li>
</ul>
</div>

<h2>八、换仓建议（针对最新一期 {latest}）</h2>
<div class="card">
<h3>结论：<b>维持</b>框架、<b>微调</b>权重与风控参数（不建议按当前参数实盘）</h3>
<ul>
<li><b>保留组合骨架</b>：化工多 MA/L/EG、空 UR/EB/V；农产多 OI/P/RM、空 SR/Y/C——当前分化度回升至 0.9 左右，对冲空间恢复，可继续持有。</li>
<li><b>权重暂不调整</b>：维持 ATR 倒数加权 + 每边 0.375 名义；若下一期化工分化度回落至 0.4 以下，将该板块名义降至 0.2。</li>
<li><b>风控参数建议（若继续用此框架实盘）</b>：① 回撤触发线从 8% 提前至 5%-6%、降仓力度从"保留 50%"改为"保留 ≤20% 或清仓"，否则无法兑现"回撤≤8%"目标；② score 反转增加"连续 3 日超阈值才动作"或把阈值放宽到 1.5σ/2.5σ，抑制噪音换手；③ back→contango 判定增加 3 日确认窗口，避免单日抖动反复触发。</li>
<li><b>因子权重临时再校准</b>：若 2026Q4 展期/基差 IC 仍 &lt;0.3，可将权重临时改为 库存 0.5 / 展期 0.2 / 基差 0.1 / 结构 0.2，待期限结构因子恢复后再调回。</li>
<li><b>不重建</b>：尚未出现结构性破坏（库存数据源、主力口径、成本结构均未变），不建议推倒重来；但要为 TA/AP 补数据源（DCE 仓单或第三方现货）后再纳入排序。</li>
</ul>
</div>

<h2>附录：口径与复现</h2>
<div class="card">
<ul>
<li>行情：新浪期货日线（主力连续按持仓量逐日判定，合约到期日按月末近似）；库存：fx168 汇总库存日频；收益采用"合约内收益+切换日同步换月"口径，消除主连跳空失真。</li>
<li>回测引擎：`backtest.py`；面板与因子：`data/norm/panel.csv`、`factors.csv`；风控事件：`events.csv`；净值：`nav_risk.csv` / `nav_norisk.csv`。</li>
<li>本报告所有数字均为 2026-10-02 快照所得，可直接复现。</li>
</ul>
</div>
</div></body></html>"""
open("商品期货横截面多空对冲回测报告-2025-2026.html","w",encoding="utf-8").write(html)
print("报告已生成:", len(html), "字符")
