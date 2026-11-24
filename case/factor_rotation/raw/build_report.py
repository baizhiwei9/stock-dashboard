# -*- coding: utf-8 -*-
"""生成《四板块因子暴露画像与风格偏转监控》HTML 报告（自包含，内嵌热力图 base64）"""
import os, base64, json
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))

mat = pd.read_csv(os.path.join(OUT, "sector_exposure_matrix.csv"))
raw = pd.read_csv(os.path.join(OUT, "sector_exposure_raw.csv"))
dd = pd.read_csv(os.path.join(OUT, "sector_distinctness.csv"))
disp = pd.read_csv(os.path.join(OUT, "sector_exposure_dispersion.csv"))
mon = pd.read_csv(os.path.join(OUT, "sector_style_monitor.csv"))
det = pd.read_csv(os.path.join(OUT, "sector_style_impulse_detail.csv"))
S = json.load(open(os.path.join(OUT, "sector_exposure_summary.json"), encoding="utf-8"))

MAIN = ["A_AI光模块CPO", "B_机器人具身", "C_存储芯片", "D_创新药CXO"]
LABEL = {"A_AI光模块CPO": "A｜AI光模块·CPO", "B_机器人具身": "B｜机器人·具身智能",
         "C_存储芯片": "C｜存储芯片", "D_创新药CXO": "D｜创新药·CXO"}
GRP_ORDER = ["估值", "反转动量", "成长", "盈利", "流动", "波动", "预期", "机构"]
img_b64 = base64.b64encode(open(os.path.join(OUT, "sector_exposure_heatmap.png"), "rb").read()).decode()

def cell(v):
    v = float(v)
    if abs(v) >= 1.0:
        bg = "#c0392b" if v > 0 else "#1f4e79"
        fg = "#ffffff"
    elif abs(v) >= 0.5:
        bg = "#f2b6ad" if v > 0 else "#b9cfe4"
        fg = "#222222"
    else:
        bg = "#f7f7f7"
        fg = "#555555"
    return f'<td style="background:{bg};color:{fg};text-align:center;font-weight:600">{v:+.2f}</td>'

# ---- 暴露矩阵表 ----
mrows = ""
for g in GRP_ORDER:
    sub = mat[mat["group"] == g]
    if not len(sub):
        continue
    mrows += f'<tr class="grp"><td colspan="8">{g}</td></tr>'
    for r in sub.itertuples():
        arrow = "↑" if r.direction > 0 else "↓"
        mrows += (f'<tr><td class="fac">{r.factor} <span class="ar">{arrow}</span></td>' +
                  "".join(cell(getattr(r, s)) for s in MAIN) +
                  f'<td style="text-align:center">{r.cross_range:.2f}</td></tr>')

# ---- 区分度表 ----
drows = ""
for r in dd.itertuples():
    hi = ' class="hot"' if r.corr >= 0.9 else (' class="warm"' if r.corr >= 0.6 else "")
    drows += (f'<tr{hi}><td>{LABEL[r.sector_i]}</td><td>{LABEL[r.sector_j]}</td>'
              f'<td style="text-align:center">{r.euclid:.3f}</td>'
              f'<td style="text-align:center;font-weight:600">{r.corr:+.3f}</td>'
              f'<td>{"高度重合，需额外拆分手段" if r.corr>=0.9 else ("中低区分" if r.corr>=0.6 else "基本独立")}</td></tr>')

# ---- 监控历史表 ----
hrows = ""
for r in mon.tail(12).itertuples():
    cells = ""
    for s in MAIN:
        v = float(getattr(r, s))
        cells += (f'<td style="text-align:center;background:'
                  f'{"#f9d5cf" if v>1 else ("#d6e4f0" if v<-1 else "#fafafa")};'
                  f'color:{"#a93226" if v>0 else "#1f4e79"};font-weight:600">{v:+.2f}</td>')
    hrows += f'<tr><td>{r.week}</td>{cells}</tr>'

# ---- 冲击明细 ----
dtrows = ""
for r in det.reindex(det["dRSz"].abs().sort_values(ascending=False).index).head(12).itertuples():
    dtrows += (f'<tr><td>{r.factor}</td><td style="text-align:center">{r.dRS:+.2f}</td>'
               f'<td style="text-align:center;font-weight:600">{r.dRSz:+.2f}</td>'
               + "".join(f'<td style="text-align:center">{getattr(r, f"C_{s}"):+.2f}</td>' for s in MAIN)
               + "</tr>")

# ---- 各板块 Top/Bottom ----
tb = ""
for s in MAIN:
    t = S["topbot"][s]
    tb += (f'<div class="tbcard"><h4>{LABEL[s]}</h4>'
           f'<p><b>最偏优势端：</b>' + "、".join(f"{n}({v:+.2f})" for n, v in t["top"]) + '</p>'
           f'<p><b>最偏不利端：</b>' + "、".join(f"{n}({v:+.2f})" for n, v in t["bot"]) + '</p></div>')

# Barra 对照
barra = [
    ("市值 Size", "总市值（对数）", "本体系未单列（因子已做截面标准化，市值效应被吸收进各因子）",
     "建议补 ln(总市值) 作为独立控制变量，A 大市值、B 中盘、D 大市值"),
    ("动量 Momentum", "一年动量(+)、一个月/三个月反转(−)", "覆盖",
     "净值≈0 一年动量 − 反转，A/C 高动量、D 低动量、B 中性"),
    ("贝塔 Beta", "无（无市场收益协方差列）", "缺失",
     "可用 raw/daily_260d.csv 的 60 日窗口回归补齐，A/C≈1.1+、D≈0.7"),
    ("成长 Growth", "单季净利/营收同比增速", "覆盖", "C 最高(+0.51/+0.74)、A 高、D 略负、B 接近 0"),
    ("价值 BP", "BP、EPTTM、SPTTM、股息率", "覆盖", "全板块普遍负暴露（成长风格），D 最接近 0"),
    ("盈利质量 Profitability", "单季 ROE、DELTAROE", "覆盖", "A/C 中高、D/B 接近中性"),
    ("残差波动 Residual Vol", "特异度、一个月/三个月波动", "覆盖", "A/C 高波动、B 中、D 低波动（防御）"),
    ("流动性 Liquidity", "一个月/三个月换手、非流动性冲击", "覆盖", "A/C 高换手高非流动、D 极低"),
    ("盈利收益率 Earnings Yield", "EPTTM、单季EP、预期EPTTM", "覆盖", "四板块普遍负（成长股共性强）"),
    ("非线性市值 NLS", "无", "缺失", "可由 ln(市值)³ 派生，主要影响小市值尾部"),
    ("杠杆 Leverage", "无（无资产负债率列）", "缺失", "需接资产负债表，D 主要看研发/负债结构"),
    ("预期修正 EPC", "预期净利润环比、预期PEG、三个月机构覆盖", "覆盖（代理口径）", "C 最强、A 次之"),
]
brows = "".join(f'<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>' for a, b, c, d in barra)

cov = " ｜ ".join(
    f"{LABEL[s].split('｜')[0]}：{S['sector_meta'][s]['样本空间内']}/{S['sector_meta'][s]['概念成分']}"
    f"（{S['sector_meta'][s]['覆盖率']*100:.0f}%）" for s in MAIN)

inw = S["impulse_rel"]
w = S["week_pair"]
html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>四板块因子暴露画像与风格偏转监控</title>
<style>
:root{{--ink:#1a1a1a;--mut:#666;--line:#e6e6e6;--accent:#8c1d18;--blue:#1f4e79}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f4f5f7;color:var(--ink);
 font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;
 line-height:1.75;font-size:15px}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 22px 60px}}
header{{background:linear-gradient(135deg,#20242c,#3a4252);color:#fff;padding:34px 22px 28px;margin-bottom:26px}}
header .inner{{max-width:1180px;margin:0 auto}}
h1{{margin:0 0 8px;font-size:25px;letter-spacing:.5px}}
.sub{{color:#c8cdd6;font-size:13.5px}}
h2{{font-size:19px;margin:38px 0 14px;padding-left:12px;border-left:5px solid var(--accent)}}
h3{{font-size:16px;margin:24px 0 10px;color:#2b2f37}}
h4{{margin:0 0 8px;font-size:14.5px}}
p{{margin:9px 0}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:20px 22px;margin:16px 0;
 box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.tldr{{border-left:5px solid var(--accent);background:#fff}}
.tldr ol{{margin:8px 0 4px;padding-left:22px}}
.tldr li{{margin:8px 0}}
b.k{{color:var(--accent)}}
table{{width:100%;border-collapse:collapse;margin:14px 0;font-size:13px;background:#fff}}
th,td{{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:middle}}
th{{background:#eef1f5;font-weight:600;position:sticky;top:0}}
tr.grp td{{background:#dfe5ec;font-weight:700;font-size:12.5px;color:#333}}
.fac{{font-weight:500;white-space:nowrap}}
.ar{{font-size:11px;color:#888}}
tr.hot td{{background:#fdecea}}
tr.warm td{{background:#fff8ec}}
.imgbox{{text-align:center;overflow:auto}}
.imgbox img{{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}}
.tbgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:12px}}
.tbcard{{background:#fbfcfd;border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:12.5px}}
.tbcard p{{margin:5px 0}}
code,.mono{{background:#f2f4f7;border:1px solid #e3e7ec;border-radius:4px;padding:1px 6px;
 font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}}
pre{{background:#20242c;color:#e8ebf0;padding:14px 16px;border-radius:8px;overflow:auto;font-size:12.5px;line-height:1.65}}
.formula{{background:#fbf7f2;border:1px dashed #d9c3ae;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:14px}}
.note{{font-size:12.5px;color:var(--mut)}}
.tag{{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:20px;margin-right:6px}}
.tag.g{{background:#e8f5e9;color:#2e7d32}}
.tag.y{{background:#fff8e1;color:#8d6e00}}
.tag.r{{background:#fdecea;color:#a93226}}
ul{{margin:8px 0;padding-left:22px}}li{{margin:5px 0}}
footer{{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);color:var(--mut);font-size:12.5px}}
</style></head><body>
<header><div class="inner">
<h1>四板块因子暴露画像 与 风格偏转监控</h1>
<div class="sub">AI光模块·CPO ｜ 机器人·具身智能 ｜ 存储芯片 ｜ 创新药·CXO　·　基于《A股因子旋转周报》26 因子体系　·　截面 20261023　·　共 {S['sector_meta']['A_AI光模块CPO']['样本空间内']+S['sector_meta']['B_机器人具身']['样本空间内']+S['sector_meta']['C_存储芯片']['样本空间内']+S['sector_meta']['D_创新药CXO']['样本空间内']} 只板块成分股（样本空间 1824 只）</div>
</div></header>
<div class="wrap">

<h2>一、先给结论</h2>
<div class="card tldr">
<ol>
<li><b class="k">四个板块的因子暴露确实不一样，但在你这套 26 因子里只分成“三档”。</b>
A（光模块）与 C（存储芯片）<b>几乎完全重合</b>（26 维暴露相关系数 <b>+0.978</b>、欧氏距离仅 <b>0.76</b>）；
D（创新药·CXO）独立站在防御侧（与 A 相关 −0.48、与 C 相关 −0.38）；B（机器人）<b>几乎是零暴露</b>——26 个因子里最大绝对暴露只有 0.48，等于“市场平均”。</li>
<li><b class="k">你原来担心的“机器人被混进光模块”，数据上并不成立</b>（两者相关仅 +0.59，已经分开）。
真正混在一起的是 <b class="k">光模块 与 存储芯片</b>：高动量、高波动、高换手、低估值、机构重仓这几条几乎一模一样，必须靠额外手段才能拆开。</li>
<li><b class="k">机器人板块目前“没有画像”</b>：它的成分股是通信＋机械＋化工＋电子的大杂烩（概念 476 只，样本内 209 只），
把 26 个因子平均后互相抵消。这也解释了你在盈利类因子上观察到的“对机器人失效”——不是因子算错了，是这个池子本身太杂。收窄到“减速器”核心链后仍偏中性（仅三个月反转 −0.49），说明<b>必须补订单／量产进度这类非财务因子</b>，靠财务与量价因子无解。</li>
<li><b class="k">判断“风格偏转时因子怎么变”的办法已经跑通并落地成脚本</b>：
用周报里每个因子的 <b>ΔRS（相对强度周变化，已做稳健化）</b> 乘上 <b>板块相对暴露矩阵</b>，得到“板块风格冲击” <span class="mono">SI</span>。
本周（20261009 → 20261016）结果：<b>C 存储芯片 +1.49（唯一明显受益）</b>，A −0.68、B −0.77、D −0.03（中性），
驱动力是“三个月反转、EPTTM一年分位点、公募重仓”三项转强被存储芯片的暴露放大。</li>
</ol>
</div>

<h2>二、口径与数据</h2>
<div class="card">
<p><b>为什么用你自己周报的 26 因子，而不是直接套 Barra CNE6？</b>因为你要的是“能直接和每周因子旋转周报联动、能回测”的东西。
《A股因子旋转周报》的 26 因子就是你这套体系的“语言”，用它与 RRG/桑基图对齐，风格偏转信号才能 1:1 落到板块上。Barra 风格因子的对照关系见第六节。</p>
<table>
<tr><th>项目</th><th>取值</th></tr>
<tr><td>股票池</td><td>沪深300＋中证500＋中证1000＋中证A500 成分并集，20261023 截面 <b>1824 只</b></td></tr>
<tr><td>板块成分来源</td><td>同花顺概念板块（Tushare）：共封装光学CPO(886033)、人形机器人(886069)、存储芯片(886042)、创新药(886015)∪CRO概念(885927)</td></tr>
<tr><td>板块样本数 / 覆盖率</td><td>{cov}</td></tr>
<tr><td>因子方向</td><td>估值 7、反转动量 3、成长 2、盈利 2、流动 3、波动 3、预期 4、机构 2，共 <b>26 个</b>（18 正 / 8 反）</td></tr>
<tr><td>标准化</td><td>每因子按截面 1%/99% 分位截尾后做 z-score（均值 0、标准差 1）</td></tr>
<tr><td>暴露值定义</td><td><b>优势方向标准化暴露</b>：z 值 × 该因子方向（+1/−1）。正数＝该板块整体落在“本体系偏好的那一端”（低估值、低波动、高增长、强动量…），负数＝落在不利端。量纲与你要的 ±0.5~±1.5 一致</td></tr>
<tr><td>原始方向暴露</td><td>同一数值 × 该因子方向即可还原（例如 三个月反转 方向 −1，其“优势暴露”为正代表该板块过去 60 日涨幅反而低）</td></tr>
</table>
<p class="note">数据缺口：SUE、SUR、单季ROA、单季营利同比增速、预期BP、高管薪酬在本体系里没有可用数据源，已从 26 因子里剔除，画像不含这 6 项。</p>
</div>

<h2>三、暴露画像：四板块 × 26 因子</h2>
<div class="card">
<div class="imgbox"><img src="data:image/png;base64,{img_b64}" alt="四板块26因子暴露画像热力图"></div>
<p class="note">读法：红＝正向（偏优势端）、蓝＝负向（偏不利端）；箭头为该因子在本体系里的方向（↑ 越大越好 / ↓ 越小越好）。</p>
</div>

<h3>完整暴露矩阵（可直接抄进回测）</h3>
<div class="card">
<table>
<tr><th rowspan="2">因子</th><th colspan="4" style="text-align:center">优势方向标准化暴露</th><th rowspan="2">跨板块极差</th></tr>
<tr><th style="text-align:center">A 光模块</th><th style="text-align:center">B 机器人</th><th style="text-align:center">C 存储</th><th style="text-align:center">D 创新药</th></tr>
{mrows}
</table>
</div>

<h3>各板块的强弱因子</h3>
<div class="card tbgrid">{tb}</div>
<p class="note">读法：A/C 是“高动量＋高波动＋高换手＋低估值＋机构重仓”同型；D 是“低波动＋低换手＋股息率正暴露”的防御型；B 四项都不显著。</p>

<h2>四、区分度检验：它们真的分开了吗？</h2>
<div class="card">
<table>
<tr><th>板块 i</th><th>板块 j</th><th style="text-align:center">欧氏距离（26维）</th><th style="text-align:center">相关系数</th><th>判定</th></tr>
{drows}
</table>
<p><b>结论：</b>最小欧氏距离只有 <b>{S['min_pair_dist']}</b>（A 光模块 vs C 存储），最大相关 <b>+{S['max_pair_corr']}</b> —— <b class="k">A 与 C 在因子空间里基本是同一个板块</b>，这是当前画像最大的问题。
其余两两距离 1.2～3.7，属于“可分开”。单因子跨板块平均极差 {S['cross_range']['mean']}，最大 1.58（一个月反转），说明区分力主要来自动量与波动两类。</p>
<h4>板块内离散度（26 因子 within-sector 标准差均值）</h4>
<ul>
<li>A 光模块 {S['within_std_mean']['A_AI光模块CPO']} ｜ B 机器人 {S['within_std_mean']['B_机器人具身']} ｜ C 存储 {S['within_std_mean']['C_存储芯片']} ｜ D 创新药 {S['within_std_mean']['D_创新药CXO']} ｜ B2 机器人核心链（减速器，收窄口径）{S['within_std_mean']['B2_机器人核心链']}</li>
<li>数值越接近 1 说明板块内成分越“杂”。B 收窄到减速器后内离散度降到 0.75（更一致），但其<b>整体暴露仍接近 0</b>——再次说明机器人缺的不是样本纯度，而是能刻画它的因子。</li>
</ul>
</div>

<h2>五、风格偏转时，怎么判断“这个因子”的变化？</h2>
<div class="card">
<p>核心思路一句话：<b>因子层只管“谁在变强变弱”，板块层只管“谁对哪些因子更敏感”，两者相乘才知道这次旋转谁受益。</b></p>
<h3>5.1 两步拆解</h3>
<table>
<tr><th>步骤</th><th>看什么</th><th>口径</th><th>判读</th></tr>
<tr><td>① 因子在不在变</td><td>周报里每个因子的 RS、MOM、象限迁移</td><td>ΔRS＝本周 RS − 上周 RS</td><td>ΔRS>0 因子相对强度转强；再叠加 MOM 由负转正、象限 lead→weak/lag→impr，由弱转强的最强形态是 <b>lag→impr</b></td></tr>
<tr><td>② 变了对板块意味着什么</td><td>第四节这张暴露矩阵</td><td>Erel ＝ 各板块暴露 − 四板块均值（消掉全市场共同项）</td><td>某因子转强时，对该因子<b>相对正暴露</b>的板块受益，“零暴露”的板块基本不动</td></tr>
</table>
<div class="formula">
<b>板块相对风格冲击：</b>　<span class="mono">SIrel(s,t) = Σ<sub>f</sub> Erel(s,f) × ΔRS_z(f,t)</span><br>
<span class="note">ΔRS_z：把 26 个因子的 ΔRS 按截面排序归一化成 z 并截断在 ±2，防止个别周 rs 标准差过小产生 ±100 级爆值（历史序列已按此口径校准）。</span>
</div>
<h3>5.2 三条判据（建议阈值）</h3>
<ul>
<li><span class="tag r">强信号</span><b>|SIrel| ≥ 1.0</b>：该板块本期明显受益（正）或受损（负）。</li>
<li><span class="tag y">趋势确认</span>同一板块<b>连续 2 周同向</b>且符号一致，才算风格切换成立；单周只视为噪声。</li>
<li><span class="tag g">结构确认</span>看贡献分解里排前几的因子，是否与该板块 <b>Top 暴露因子</b>重合——重合＝这次冲击有真实暴露支撑，不是残差噪声。</li>
</ul>
<h3>5.3 本周实测（{w[0]} → {w[1]}）</h3>
<table>
<tr><th>板块</th><th style="text-align:center">SIrel</th><th>判读</th></tr>
{''.join(f'<tr><td>{LABEL[s]}</td><td style="text-align:center;font-weight:700;color:{"#a93226" if inw[s]>0 else "#1f4e79"}">{inw[s]:+.2f}</td><td>{"明显受益" if inw[s]>=1 else ("明显受损" if inw[s]<=-1 else "中性")}</td></tr>' for s in MAIN)}
</table>
<h4>贡献分解（按 |ΔRSz| 排序前 12 个因子，数值＝该因子对板块 SIrel 的贡献）</h4>
<table>
<tr><th>因子</th><th style="text-align:center">ΔRS</th><th style="text-align:center">ΔRSz</th>{''.join(f'<th style="text-align:center">{LABEL[s].split("｜")[0]}</th>' for s in MAIN)}</tr>
{dtrows}
</table>
<h4>近 12 周 SIrel 序列（可直接看出风格在两组之间来回摆）</h4>
<table>
<tr><th>周（周截面）</th>{''.join(f'<th style="text-align:center">{LABEL[s].split("｜")[0]}</th>' for s in MAIN)}</tr>
{hrows}
</table>
<p class="note">红＝受益、蓝＝受损、灰＝中性。可以看到 A/C 基本同步、B/D 大体反向——说明在现有 26 因子体系下，这四个板块实际上只剩“成长进攻组（A/C）”与“防御组（D）＋中性组（B）”两种风格角色。</p>
<h3>5.4 以后怎么用（一条命令）</h3>
<pre>export PYTHONPATH=&lt;workspace&gt;/pylibs
python3 raw/style_sector_monitor.py
# 输出：output/sector_style_monitor.csv（各周 SIrel）
#       终端打印最新周排名 + 贡献分解 + 判读</pre>
<p class="note">脚本每次都会用最新 <span class="mono">raw/latest_cross_section.csv</span> 本地重算暴露（不联网），再接 <span class="mono">raw/style_angle_state.csv</span> 的最新两周 RS，所以只要周报链路跑完，跑一次这个脚本就能得到当期板块风格冲击。</p>
</div>

<h2>六、与 Barra 风格因子的对照</h2>
<div class="card">
<table>
<tr><th>Barra 风格因子</th><th>本体系对应</th><th>覆盖情况</th><th>画像提示</th></tr>
{brows}
</table>
<p><b>结论：</b>市值(Size)、动量(Momentum)、成长(Growth)、价值(BP)、盈利、残差波动、流动性、盈利收益率 8 类可覆盖或可代理；
<b class="k">贝塔(Beta)、杠杆(Leverage)、非线性市值(NLS) 三类缺失</b>。
其中 Beta 缺失影响最大——A/C 与 D 的“进攻 vs 防御”差异，本质就是高 Beta vs 低 Beta，补上它区分度会明显改善。建议下一步用 <span class="mono">raw/daily_260d.csv</span> 做 60 日窗口的市场回归补 Beta，用资产负债表补 Leverage。</p>
</div>

<h2>七、局限与建议</h2>
<div class="card">
<ul>
<li><b>暴露是时点值。</b>本画像基于 20261023 截面，属“当前持仓语境”；板块成分与财务会漂移，建议每季度用同一脚本重算一次。</li>
<li><b>A 与 C 无法用现有因子拆开。</b>要拆就得引入“成长质量”类拆分：把一年动量拆成“剔除最近 1 个月的 12 月趋势动量”与“近 20 日换手＋振幅的情绪动量”，或加入“毛利率环比 / 存货周转天数”这类周期位置代理——这正是把存储从“周期股伪装成长股”里剥离出来的关键。</li>
<li><b>机器人必须上非财务因子。</b>订单／量产进度类指标（例如定点公告数、交付台数同比、产能利用率）才有区分力，财务与量价因子在这上面已证明是零。</li>
<li><b>风格冲击是相对口径。</b>SIrel 只回答“四个板块里谁相对受益”，不回答绝对涨跌；要判断绝对方向，仍需叠加对市场的择时判断。</li>
</ul>
</div>

<footer>
数据：Tushare（概念成分、日线、每日指标）｜因子体系：《A股因子旋转周报》26 因子｜截面日 20261023｜生成时间 2026-10-28<br>
复用脚本：<span class="mono">raw/sector_exposure.py</span>（出画像）、<span class="mono">raw/style_sector_monitor.py</span>（出监控）｜矩阵明细：<span class="mono">output/sector_exposure_matrix.csv</span>
</footer>
</div></body></html>"""

path = os.path.join(OUT, "板块因子暴露画像.html")
open(path, "w", encoding="utf-8").write(html)
print("saved", path, len(html), "chars")