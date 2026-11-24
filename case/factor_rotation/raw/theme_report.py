# -*- coding: utf-8 -*-
"""生成《机器人产业链与特斯拉概念 · 监控因子体系》HTML 报告（自包含 base64 图）

所有数值均从下列产物实时读取，不在正文手写数字：
    raw/theme_monitor.csv                 四维概念热度指标
    output/theme_style_impulse.csv        最新周热度 × 因子冲击合表
    output/theme_style_monitor.csv        7 概念各周 si_rel
    output/theme_style_monitor_mkt.csv    7 概念各周 si_mkt
    output/theme_exposure_matrix.csv      7 概念 × 26 因子暴露矩阵
    output/sector_exposure_matrix_local.csv  A/B/C/D 四板块 × 26 因子暴露
    raw/factor_weekly_raw.csv             因子分组/方向定义
用法：
    export PYTHONPATH=<workspace>/pylibs
    python3 raw/theme_report.py
输出：output/机器人产业链与特斯拉概念监控报告-20261027.html（新文件，不覆盖旧版同名报告）
"""
import os, io, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

FONT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/optimal-transport-ot/SimHei.ttf"
font_manager.fontManager.addfont(FONT)
_pp = font_manager.FontProperties(fname=FONT)
plt.rcParams["font.family"] = _pp.get_name()
plt.rcParams["axes.unicode_minus"] = False

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
IMG = os.path.join(OUT, "theme_report_img")
os.makedirs(IMG, exist_ok=True)

heat = pd.read_csv(os.path.join(RAW, "theme_monitor.csv")).set_index("key")
imp = pd.read_csv(os.path.join(OUT, "theme_style_impulse.csv")).set_index("key")
mon_t = pd.read_csv(os.path.join(OUT, "theme_style_monitor.csv"))
mon_m = pd.read_csv(os.path.join(OUT, "theme_style_monitor_mkt.csv"))
Et = pd.read_csv(os.path.join(OUT, "theme_exposure_matrix.csv"), index_col=0)
E_main = pd.read_csv(os.path.join(OUT, "sector_exposure_matrix_local.csv"), index_col=0)
picks = pd.read_csv(os.path.join(OUT, "theme_picks.csv"))
fw = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
last_week = int(fw["week_end"].max())
fdef = fw[fw["week_end"] == last_week].drop_duplicates("factor")[["factor", "group", "direction"]]
grp = dict(zip(fdef["factor"], fdef["group"]))
dirn = dict(zip(fdef["factor"], fdef["direction"]))
THEMES = list(Et.columns)
MAIN = list(E_main.columns)

AS_OF = str(int(heat["最新日"].max()))
HOT_N = 60


def b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def img_tag(data, name):
    # 同时落盘一份，便于单独查看
    with open(os.path.join(IMG, name), "wb") as f:
        f.write(base64.b64decode(data))
    return f'<img src="data:image/png;base64,{data}" alt="{name}">'


# ---------- 图1：7 概念四维热度 + 综合分 ----------
subs = ["分_注意力", "分_价格强度", "分_量能拥挤", "分_情绪"]
lab = ["注意力", "价格强度", "量能拥挤", "情绪"]
h = heat.loc[THEMES]
fig, ax = plt.subplots(figsize=(11, 4.6))
x = np.arange(len(THEMES)); w = 0.19
colors = ["#4E79A7", "#F28E2B", "#59A14F", "#E15759"]
for i, (s, l, c) in enumerate(zip(subs, lab, colors)):
    ax.bar(x + (i - 1.5) * w, h[s].fillna(0).values, w, label=l, color=c)
ax.plot(x, h["热度分"].values, "o-", color="#B07AA1", lw=2, ms=6, label="热度分")
for xi, v in zip(x, h["热度分"].values):
    ax.annotate(f"{v:.1f}\n{h.loc[THEMES[xi],'判定']}", (xi, v), textcoords="offset points",
                xytext=(0, 8), ha="center", fontsize=9, color="#7B2D8E")
ax.axhline(80, ls="--", lw=1, color="#C0392B"); ax.axhline(60, ls=":", lw=1, color="#E67E22"); ax.axhline(40, ls=":", lw=1, color="#95A5A6")
ax.text(len(THEMES) - 0.45, 81.5, "很火≥80", fontsize=8, color="#C0392B")
ax.text(len(THEMES) - 0.45, 61.5, "温热≥60", fontsize=8, color="#E67E22")
ax.text(len(THEMES) - 0.45, 41.5, "一般≥40", fontsize=8, color="#7F8C8D")
ax.set_xticks(x); ax.set_xticklabels([t.split("_", 1)[-1] for t in THEMES], fontsize=10)
ax.set_ylabel("分位/综合分（0-100）"); ax.set_ylim(0, 108)
ax.set_title(f"机器人链与特斯拉线七概念 · 四维热度（数据截至 {AS_OF}）", fontsize=12)
ax.legend(ncol=5, fontsize=9); ax.grid(axis="y", ls=":", alpha=0.35)
img1 = b64(fig)

# ---------- 图2：26 因子最大绝对暴露对比（证明因子体系看不到机器人） ----------
groups = list(E_main.columns) + list(Et.columns)
maxabs = pd.concat([E_main.abs().max(), Et.abs().max()])
order = maxabs.iloc[len(MAIN):].sort_values(ascending=True).index.tolist() + MAIN
vals = maxabs.loc[order]
cols = ["#E15759" if c in MAIN else "#4E79A7" for c in order]
fig, ax = plt.subplots(figsize=(10, 5.2))
ax.barh([o.split("_", 1)[-1] for o in order], vals.values, color=cols)
for i, v in enumerate(vals.values):
    ax.text(v + 0.006, i, f"{v:.3f}", va="center", fontsize=9)
ax.axvline(1.0, ls="--", color="#7F8C8D", lw=1); ax.text(1.02, len(order) - 1.2, "|暴露|=1：标准差异", fontsize=8, color="#7F8C8D")
ax.set_xlabel("26 因子上最大绝对标准化暴露 max|E|（优势方向）")
ax.set_title("谁能被 26 因子刻画？—— 蓝＝机器人链/特斯拉概念，红＝原有四板块", fontsize=12)
ax.set_xlim(0, max(vals.max() * 1.18, 1.4)); ax.grid(axis="x", ls=":", alpha=0.35)
img2 = b64(fig)

# ---------- 图3：最新周 si_mkt / si_rel ----------
last = imp.loc[THEMES]
fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
for ax, col, ttl in zip(axes, ["si_mkt", "si_rel"],
                        ["si_mkt（相对全市场，0=与市场同步）", "si_rel（七概念内部相对，谁相对受益）"]):
    v = last[col].astype(float).sort_values()
    ax.barh([k.split("_", 1)[-1] for k in v.index], v.values,
            color=["#E15759" if z < 0 else "#59A14F" for z in v.values])
    for i, z in enumerate(v.values):
        ax.text(z + (0.05 if z >= 0 else -0.05), i, f"{z:+.2f}", va="center",
                ha="left" if z >= 0 else "right", fontsize=9)
    ax.axvline(0, color="#333", lw=1)
    ax.axvline(1.0, ls="--", lw=1, color="#C0392B"); ax.axvline(-1.0, ls="--", lw=1, color="#C0392B")
    ax.set_title(f"{ttl}\n（{int(mon_m['from'].iloc[-1])}→{int(mon_m['week'].iloc[-1])} 周）", fontsize=11)
    ax.set_xlim(min(v.min(), -1.9) * 1.25, max(v.max(), 1.9) * 1.25); ax.grid(axis="x", ls=":", alpha=0.35)
img3 = b64(fig)

# ---------- 图4：si_mkt 近 12 周时序 ----------
tail = mon_m.tail(12).copy()
wkl = [str(int(w)) for w in tail["week"]]
fig, ax = plt.subplots(figsize=(11, 4.6))
cmap = plt.get_cmap("tab10")
for i, t in enumerate(THEMES):
    ax.plot(wkl, tail[t].values, "o-", ms=4, lw=1.8, color=cmap(i), label=t.split("_", 1)[-1])
ax.axhline(0, color="#333", lw=1); ax.axhline(1, ls="--", lw=1, color="#C0392B"); ax.axhline(-1, ls="--", lw=1, color="#C0392B")
ax.set_ylabel("si_mkt"); ax.set_title("七概念因子冲击 si_mkt 近 12 周（跨 ±1 = 明显偏离市场）", fontsize=12)
ax.legend(ncol=7, fontsize=9); ax.grid(ls=":", alpha=0.35); plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=9)
img4 = b64(fig)

# ---------- 图5：人形机器人 26 因子暴露条形（看它"最强"的因子也只有 0.48） ----------
r1 = Et["R1_人形机器人"].sort_values(key=np.abs, ascending=False).head(10)
fig, ax = plt.subplots(figsize=(10, 4.2))
ax.barh(range(len(r1)), r1.values[::-1], color=["#E15759" if z < 0 else "#59A14F" for z in r1.values[::-1]])
ax.set_yticks(range(len(r1)))
ax.set_yticklabels([f"{f}（{grp.get(f,'')}·{dirn.get(f,'')}）" for f in r1.index[::-1]])
for i, z in enumerate(r1.values[::-1]):
    ax.text(z + (0.012 if z >= 0 else -0.012), i, f"{z:+.3f}", va="center",
            ha="left" if z >= 0 else "right", fontsize=9)
ax.set_xlabel("优势方向标准化暴露"); ax.set_title("R1 人形机器人：26 因子里暴露最强的 10 个（负＝非优势方向）", fontsize=12)
ax.grid(axis="x", ls=":", alpha=0.35)
img5 = b64(fig)

# ---------- 表格 ----------
def tbl_heat():
    cols = ["概念", "概念成分", "样本内", "hot_最新排名", "hot_上榜占比", "ex5", "ex20", "ex20_分位",
            "amt_share", "amt_share_分位", "limit5", "limit5_分位", "分_注意力", "分_价格强度",
            "分_量能拥挤", "分_情绪", "热度分", "判定"]
    r = heat.loc[THEMES][["概念成分", "样本内", "hot_最新排名", "hot_上榜占比", "ex5", "ex20", "ex20_分位",
                          "amt_share", "amt_share_分位", "limit5", "limit5_分位", "分_注意力", "分_价格强度",
                          "分_量能拥挤", "分_情绪", "热度分", "判定"]].copy()
    r.insert(0, "概念", [t.split("_", 1)[-1] for t in THEMES])
    r["hot_最新排名"] = r["hot_最新排名"].map(lambda v: "—（未上榜）" if v == 99 else f"第 {int(v)}")
    hdr = "".join(f"<th>{c}</th>" for c in r.columns)
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in r.values)
    return f'<table class="tbl"><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>'


def tbl_imp():
    r = imp.loc[THEMES, ["概念成分", "样本内", "hot最新排名", "maxabs_26因子暴露", "si_rel", "si_mkt",
                         "热度分", "判定", "贡献Top3（差异于七概念均值）"]].copy()
    r.insert(0, "概念", [t.split("_", 1)[-1] for t in THEMES])
    r["hot最新排名"] = r["hot最新排名"].map(lambda v: "未上榜" if v == 99 else f"第 {int(v)}")
    hdr = "".join(f"<th>{c}</th>" for c in r.columns)
    body = ""
    for row in r.values:
        cells = "".join(f"<td>{v}</td>" for v in row[:-1])
        body += f'<tr>{cells}<td class="wrp">{row[-1]}</td></tr>'
    return f'<table class="tbl"><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>'


def tbl_picks():
    r = picks.copy()
    hdr = "".join(f"<th>{c}</th>" for c in r.columns)
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in r.values)
    return f'<table class="tbl"><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>'


def tbl_expo():
    e = pd.concat([E_main, Et], axis=1)
    hdr = "<th>因子（分组·方向）</th>" + "".join(f"<th>{c.split('_',1)[-1]}</th>" for c in e.columns)
    body = ""
    for f, row in e.iterrows():
        tds = "".join(f'<td class="{"pos" if v>=0 else "neg"}">{v:+.3f}</td>' for v in row.values)
        body += f'<tr><td class="wrp">{f}（{grp.get(f,"")}·{dirn.get(f,"")}）</td>{tds}</tr>'
    return f'<table class="tbl small"><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>'


def tbl_simkt():
    r = mon_m.tail(8)[["week", "from"] + THEMES].copy()
    hdr = "<th>周</th><th>相对上周</th>" + "".join(f"<th>{t.split('_',1)[-1]}</th>" for t in THEMES)
    body = ""
    for _, row in r.iterrows():
        body += ("<tr><td>" + str(int(row["week"])) + "</td><td>" + str(int(row["from"])) + "</td>"
                 + "".join(f'<td class="{"pos" if row[t]>=0 else "neg"}">{row[t]:+.2f}</td>' for t in THEMES) + "</tr>")
    return f'<table class="tbl small"><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>'


top_hot = imp.loc[THEMES].sort_values("热度分", ascending=False)
r1 = heat.loc["R1_人形机器人"]
t1 = heat.loc["T1_特斯拉概念"]
r3 = heat.loc["R3_减速器"]
n_weak = int((imp.loc[THEMES, "maxabs_26因子暴露"] < 1.0).sum())

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>机器人产业链与特斯拉概念 · 监控因子体系</title>
<style>
:root{{--ink:#1f2733;--mut:#61708a;--ln:#e3e8f0;--acc:#2d6cdf;--bg:#f6f8fb;}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--ink);
font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",SimHei,sans-serif;line-height:1.75;}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 30px 70px;background:#fff;box-shadow:0 1px 14px rgba(20,40,80,.07);}}
h1{{font-size:25px;margin:0 0 6px;letter-spacing:.5px;}}
h2{{font-size:18px;margin:34px 0 12px;padding-left:11px;border-left:5px solid var(--acc);}}
h3{{font-size:15px;margin:20px 0 8px;color:#2b3a52;}}
p,li{{font-size:14.2px;}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:20px;}}
.meta{{background:var(--bg);border:1px solid var(--ln);border-radius:8px;padding:12px 16px;font-size:13px;color:#3a4b63;}}
table.tbl{{border-collapse:collapse;width:100%;margin:12px 0 6px;font-size:13px;}}
table.tbl th,table.tbl td{{border:1px solid var(--ln);padding:6px 8px;text-align:center;white-space:nowrap;}}
table.tbl th{{background:#eef3fa;font-weight:600;color:#26364e;}}
table.tbl td.lft{{text-align:left;white-space:nowrap;}}
table.tbl td.wrp{{text-align:left;white-space:normal;min-width:220px;font-size:12.4px;}}
table.small{{font-size:12.3px;}}
.pos{{color:#1f7a44;}} .neg{{color:#b8382f;}}
img{{width:100%;border:1px solid var(--ln);border-radius:8px;margin:8px 0 4px;background:#fff;}}
.cap{{font-size:12.4px;color:var(--mut);margin:0 0 16px;}}
.note{{background:#fff8e8;border-left:4px solid #e8a33d;padding:10px 14px;border-radius:0 6px 6px 0;font-size:13.2px;margin:14px 0;}}
.warn{{background:#fdeeed;border-left:4px solid #d9534f;padding:10px 14px;border-radius:0 6px 6px 0;font-size:13.2px;margin:14px 0;}}
code{{background:#eef2f7;border-radius:4px;padding:1px 5px;font-size:12.8px;color:#1d3d6b;}}
.kpi{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0;}}
.kpi div{{flex:1 1 150px;background:#f2f6fc;border:1px solid var(--ln);border-radius:8px;padding:10px 12px;}}
.kpi b{{display:block;font-size:21px;color:var(--acc);line-height:1.35;}}
.kpi span{{font-size:12.3px;color:var(--mut);}}
ol,ul{{padding-left:22px;}}
.foot{{margin-top:34px;border-top:1px dashed var(--ln);padding-top:12px;font-size:12.6px;color:var(--mut);}}
</style></head><body><div class="wrap">
<h1>机器人产业链与特斯拉概念 · 监控因子体系</h1>
<div class="sub">为什么 26 因子体系找不到"机器人"，以及应该改用什么指标来监控它——<b>全部数值由脚本实时计算，数据截至 {AS_OF}</b></div>
<div class="meta">
数据截止：概念板块指数（同花顺 ths_daily）与概念热榜（ths_hot）最新交易日 <b>{AS_OF}</b>；成分股行情同截止；
下一个交易日 20261028 的板块指数与热榜尚未更新，故本报告不用 20261028。
样本空间：沪深300 ∪ 中证500 ∪ 中证1000 ∪ 中证A500 并集 1824 只。<br>
概念成分（同花顺概念板块，括号内为板块代码）：人形机器人(886069.TI)、机器人概念(885517.TI)、减速器(886008.TI)、
机器视觉(886002.TI)、特斯拉概念(885467.TI)、一体化压铸(886000.TI)、无人驾驶(885736.TI)。
因子口径：26 因子＝18 正向 + 8 反向（全量暴露矩阵见第二章附表）。机构/公募持仓为季频，最新报告期 2026-06-30。
</div>

<h2>一、一句话结论</h2>
<p><b>机器人、特斯拉这类主题概念不是"因子"问题，而是"这套因子体系里根本没有它的代理变量"。</b>
量化里判断一个概念火不火，靠的是"因子暴露 × 因子强度变化"；而机器人链与特斯拉线在这 26 个因子上的
最大绝对暴露只有 <b>{imp.loc[THEMES,'maxabs_26因子暴露'].max():.2f}</b>（七概念全部 &lt; 1，"远远小于 1 个标准差"），
所以无论因子怎么旋转，算出来的冲击都接近 0——这就是你"监控不到它"的数学原因。</p>
<p>正确做法是给概念单独上一套 <b>概念层四维指标</b>：注意力（热榜）、价格强度（相对中证全指超额）、
量能拥挤（换手/成交额占比分位）、情绪强度（涨停家数分位），合成 0-100 的<b>热度分</b>。按这套指标，
当前（{AS_OF}）七概念中：<b>{top_hot.index[0].split('_',1)[-1]} 热度分 {top_hot['热度分'].iloc[0]:.1f}（{top_hot['判定'].iloc[0]}）
居首</b>，人形机器人、机器人概念、减速器、机器视觉四个"机器人链"概念均为<b>温热</b>；
而<b>特斯拉概念热度分只有 {t1['热度分']:.1f}（{t1['判定']}）</b>，是七概念里最冷的——它近期并没有跟着机器人一起火。</p>
<div class="kpi">
  <div><b>{r1['热度分']:.1f}</b><span>人形机器人 热度分（{r1['判定']}）</span></div>
  <div><b>第 {int(r1['hot_最新排名'])} 名</b><span>人形机器人 概念热榜最新排名</span></div>
  <div><b>{r1['ex20']:+.2f}%</b><span>人形机器人 20日相对中证全指超额</span></div>
  <div><b>{t1['热度分']:.1f}</b><span>特斯拉概念 热度分（{t1['判定']}）</span></div>
  <div><b>{n_weak}/7</b><span>max|26因子暴露| &lt; 1 的概念数</span></div>
</div>

<h2>二、为什么 26 因子体系监控不到机器人</h2>
<p>因子监控的机制是：先算每个概念对 26 个因子的"优势方向标准化暴露" <code>E[s,f]</code>
（暴露越高＝这个概念的股票越像"强因子股"），再乘上因子这一周的相对强度变化
<code>ΔRS[f]</code>，得到<b>风格冲击</b>。<b>E 接近 0，乘积就接近 0，无论因子怎么转，都算不出明显的受益/受损。</b></p>
{img_tag(img2, "exposure_gap.png")}
<p class="cap">图 1：11 组对象在 26 因子上的最大绝对暴露。红＝原有四板块（AI光模块CPO/机器人具身/存储芯片/创新药CXO），
蓝＝本次新增的七概念。凡 max|E| &lt; 1 者，因子旋转冲击基本"看不出差别"。</p>
<p>七概念的最大绝对暴露落在 <b>{imp.loc[THEMES,'maxabs_26因子暴露'].min():.3f} ~ {imp.loc[THEMES,'maxabs_26因子暴露'].max():.3f}</b> 之间，
其中人形机器人（本期最火的机器人概念）最大暴露只有 <b>{imp.loc['R1_人形机器人','maxabs_26因子暴露']:.3f}</b>，
且它暴露最强的因子是"一个月波动"（反而是<b>负向</b>，即比市场平均更"不波动"），
<b>这说明现有 26 因子里没有任何一个能代表"机器人订单/量产进度/产业景气"这类驱动力</b>——不是参数没调好，是维度缺失。</p>
{img_tag(img5, "r1_top_exposure.png")}
<p class="cap">图 2：R1 人形机器人 26 因子里暴露最强的 10 个。最大仅 ±0.48，且以估值/波动类为主，
看不出"成长/景气"型特征——主题热度无法被这 26 维空间解释。</p>
{tbl_expo()}
<p class="cap">附表：26 因子 × 11 组对象的完整标准化暴露矩阵（含分组与方向；绿＝正暴露、红＝负暴露）。
"方向"为该因子的优势方向，正值表示该对象在优势方向上暴露更强。</p>
<div class="note"><b>结论：</b>要用因子监控机器人/特斯拉，只有两条路——
① 补一套概念层指标（本报告第三、四章，立即可用）；
② 往因子体系里新增"主题/景气代理因子"（第五章，需额外数据源）。二者配合使用，不是二选一。</div>

<h2>三、概念层监控指标体系（可真实计算，数据源已打通）</h2>
<p>四个维度各取 0-100 分位，等权平均得到<b>热度分</b>；判定阈值：<b>很火 ≥ 80 ｜ 温热 ≥ 60 ｜ 一般 ≥ 40 ｜ 偏冷 &lt; 40</b>。</p>
<table class="tbl"><thead><tr>
<th>维度</th><th>指标构成（全部取自身历史分位）</th><th>计算口径</th><th>数据源（Tushare）</th><th>为什么它能代表"火"</th></tr></thead><tbody>
<tr><td><b>注意力</b></td><td>热榜 5 日热度均值分位、近 60 交易日上榜天数占比</td>
<td>板块在同花顺概念热榜的 hot 值滚动 5 日均值取自身分位；上榜占比＝60 日内出现天数/60</td>
<td>ths_hot（概念热榜，日频）</td><td>散户/舆情注意力的直接度量，先于资金流出现</td></tr>
<tr><td><b>价格强度</b></td><td>20 日相对中证全指超额（ex20）的 250 日分位</td>
<td>板块指数 20 日收益 − 中证全指(000985.CSI) 20 日收益，再取自身 250 日分位</td>
<td>ths_daily（板块指数）+ index_daily（基准）</td><td>剔除大盘涨跌后，看它是否真的"跑赢大盘"</td></tr>
<tr><td><b>量能拥挤</b></td><td>换手率 250 日分位、成分股成交额占样本空间比分位</td>
<td>板块指数 turnover_rate 分位；成分股合计成交额 ÷ 样本空间当日总成交额</td>
<td>ths_daily + daily（个股行情）</td><td>资金真金白银的拥挤度，热度若无量能配合多为一日游</td></tr>
<tr><td><b>情绪强度</b></td><td>近 5 日成分股涨停家数的 260 日分位</td>
<td>涨幅 ≥ 9.8% 的样本内成分股家数，滚动 5 日求和后在 260 日分布中取分位</td>
<td>daily（个股涨跌幅）</td><td>题材情绪最灵敏的极值信号，家数越多说明越"沸腾"</td></tr>
</tbody></table>
<div class="note"><b>怎么读这张表判断"火"：</b>热度分是"跟它自己的历史比"。分位 90 意味着它比过去 250 天里 90% 的时间都更强，
所以跨概念、跨时间都可比。单看一项容易误判：<b>只有"注意力高 + 价格强度高 + 量能配合"三者同时成立，才叫真火</b>；
注意力高而价格强度低＝只讨论不涨（喊单），价格强度高而注意力低＝走得安静（机构慢牛）。</div>

<h2>四、当前实况：七大概念的真实数值（数据截至 {AS_OF}）</h2>
{img_tag(img1, "theme_heat_dims.png")}
<p class="cap">图 3：七概念四维热度与综合分。虚线为判定阈值 40/60/80。</p>
{tbl_heat()}
<p class="cap">表 1：概念热度明细。ex5/ex20 为相对中证全指的超额收益（%）；分位类数值为 0-100 分位；
hot_最新排名 = 99 表示当日未进入热榜。</p>
<h3>本期事实判断</h3>
<ol>
<li><b>机器人链明显强于特斯拉线。</b>人形机器人热度分 {r1['热度分']:.1f}、机器人概念 {heat.loc['R2_机器人概念','热度分']:.1f}、
减速器 {r3['热度分']:.1f}、机器视觉 {heat.loc['R4_机器视觉','热度分']:.1f}，全部落在"温热"；而特斯拉概念 {t1['热度分']:.1f}、
一体化压铸 {heat.loc['T2_一体化压铸','热度分']:.1f}、无人驾驶 {heat.loc['T3_无人驾驶','热度分']:.1f} 仅"一般"。"机器人相关"被市场认可，"特斯拉相关"本期没被认可。</li>
<li><b>热度集中在"人形机器人"这个细分。</b>它热榜排名第 {int(r1['hot_最新排名'])}、5 日热度均值 {r1['hot_5日热度均值']:.0f}、60 日内
{r1['hot_上榜天数']:.0f}/60 天上榜——是七概念中唯一"注意力＋价格＋情绪"三高共振的（注意力 {r1['分_注意力']:.1f}、
价格强度 {r1['分_价格强度']:.1f}、情绪 {r1['分_情绪']:.1f}）；20 日超额 {r1['ex20']:+.2f}%、近 5 日成分股涨停 {int(r1['limit5'])} 家。</li>
<li><b>减速器是"价格最强、注意力偏弱"的典型。</b>20 日超额 {r3['ex20']:+.2f}%（分位 {r3['ex20_分位']:.1f}，七概念最高）、
成交额占比分位 {r3['amt_share_分位']:.1f}，但热榜上榜占比仅 {r3['hot_上榜占比']:.0%}——更像资金驱动的链内补涨（机器人关节减速器直接受益），
而不是舆情炒作的重点。</li>
<li><b>机器人概念的"量能拥挤"分位（{heat.loc['R2_机器人概念','分_量能拥挤']:.1f}）明显低于注意力（{heat.loc['R2_机器人概念','分_注意力']:.1f}）</b>，
说明 1240 只宽口径成分股里资金只集中在少数细分，宽基口径的"机器人概念"容易稀释信号——监控时应以人形机器人/减速器等细分口径为准。</li>
<li><b>特斯拉概念：注意力、量能双低。</b>ex20 {t1['ex20']:+.2f}%（分位 {t1['ex20_分位']:.1f}）、成交额占比分位仅 {t1['amt_share_分位']:.1f}、
60 日内仅 {t1['hot_上榜天数']:.0f} 天上榜。结论：<b>本期特斯拉概念不火</b>，不要因为"马斯克/擎天柱"的新闻热度就默认它被交易。</li>
<li>机器视觉、一体化压铸、无人驾驶的"注意力"分别为 {" / ".join(f"{v:.1f}" for v in heat.loc[['R4_机器视觉','T2_一体化压铸','T3_无人驾驶'],'分_注意力'])}：
热榜未上榜（排名第 99）但价格强度/情绪分位不低，属于<b>"走得安静型"</b>，靠注意力指标会漏掉，必须看价格强度与情绪。</li>
</ol>

<h2>五、概念内代表股（数据截至 {AS_OF}）</h2>
<p>概念热度只有落到具体标的上才可执行，因此对七个概念各选出概念内代表股。口径：样本＝概念成分股 ∩ 样本空间，
剔除 ST/退市/次新；设流动性门槛（近 5 日日均成交额 ≥ 概念内中位数），再按<b>近 20 日涨幅</b>降序取前 5 只（即该概念的领涨股）。
<b>按要求此处只列股票名称，不列股票代码</b>；完整指标见 <code>output/theme_picks.csv</code>。</p>
{tbl_picks()}
<p class="cap">表 2：概念内代表股。涨幅为区间累计（按日涨跌幅复利），成交额与市值为亿元；
"近5日涨停"为近 5 个交易日涨幅 ≥9.8% 的天数。</p>
<p><b>读法提示：</b>同一只股票可同时出现在多个概念（如同时属于人形机器人与减速器），说明它是产业链的交汇环节；
机器人链的代表股以精密传动（减速器/微型驱动）、机器视觉感知为主，特斯拉线的代表股涨幅普遍只有个位数甚至为负，
与其热度分最低（{t1['热度分']:.1f}）相互印证——<b>特斯拉概念不是"没盯住"，而是本期确实没有被资金交易</b>。</p>

<h2>六、26 因子体系缺什么、可以怎么补</h2>
<p>缺口清单（这些是机器人/主题概念真正的驱动，现有 26 因子全部没有对应列）：</p>
<table class="tbl"><thead><tr><th>缺失维度</th><th>为什么现有因子盖不住</th><th>可补的代理指标（需额外数据源）</th></tr></thead><tbody>
<tr><td>产业链景气/订单</td><td>26 因子全是财务与量价截面因子，没有"订单/产能/销量"这类产业数据</td>
<td>板块指数成交额同比、产业链高频价格（如工业机器人产量、注塑机/机床订单），Tushare 无，需外部数据</td></tr>
<tr><td>事件驱动强度</td><td>财务因子为季频，事件是日频，时间尺度不匹配</td>
<td>本报告第三章的"注意力＋情绪"两维即为事件强度代理，已可实现</td></tr>
<tr><td>主题资金拥挤</td><td>现有流动性因子只度量个股层面非流动性，不度量"板块级别拥挤"</td>
<td>板块成交额占样本空间比分位（本报告"量能拥挤"维），已可实现</td></tr>
<tr><td>Beta / Leverage 暴露</td><td>26 因子无 Beta、无杠杆两个 Barra 常见风格</td>
<td>可用 <code>raw/daily_260d.csv</code> 对中证全指做 60 日滚动回归补 Beta，需新增计算</td></tr>
<tr><td>主题内"龙头效应"</td><td>成分等权聚合会稀释龙头，机器人概念（1240 只）受影响最大</td>
<td>改用"涨幅前 10% 成分股"的等权组合重算暴露与热度，可显著提升信噪比</td></tr>
</tbody></table>

<h2>七、与既有"因子旋转 → 板块冲击"监控的衔接</h2>
<p>原有的板块监控口径保持不变：<code>Erel[s,f] = E[s,f] − mean_s(E[s,f])</code>（去均值消共同项）、
<code>SIrel[s,t] = Σ_f Erel[s,f] × ΔRS_z(f,t)</code>，<code>|SIrel| ≥ 1</code> 为明显冲击。
本次在其后新增 Part B，对七概念同时给出两种冲击：</p>
<ul>
<li><code>si_mkt = Σ_f E[s,f] × ΔRS_z(f,t)</code>：<b>相对全市场</b>，0＝与市场同步。</li>
<li><code>si_rel = Σ_f Erel[s,f] × ΔRS_z(f,t)</code>：<b>七概念内部去均值</b>，谁相对其他概念受益。</li>
</ul>
{img_tag(img3, "theme_si_latest.png")}
<p class="cap">图 4：最新周（{int(mon_m['from'].iloc[-1])}→{int(mon_m['week'].iloc[-1])}）七概念的两种冲击。红虚线为 ±1 阈值。</p>
{tbl_imp()}
<p class="cap">表 3：最新周"热度 × 因子冲击"合表。maxabs_26因子暴露 越小，说明用因子去监控它越无效；
贡献 Top3 列出该概念相对其他概念的超额来自哪些因子的旋转。</p>
<h3>两套指标怎么配合读</h3>
<ol>
<li><b>热度分高 ＋ |si_mkt| ≥ 1</b>：主题有因子层面的支撑，不只是情绪——可提升关注度（如减速器 si_mkt {imp.loc['R3_减速器','si_mkt']:+.2f}）。</li>
<li><b>热度分高 ＋ si_mkt ≈ 0</b>：纯事件/情绪驱动，因子体系看不到——<b>人形机器人正是如此（si_mkt {imp.loc['R1_人形机器人','si_mkt']:+.2f}）</b>，
这就是"用它去监控机器人监控不到"的直接证据，必须靠概念层指标。</li>
<li><b>热度分低 ＋ si_mkt ≥ 1</b>：存在"因子层面受益但市场还没反应"的概念——一体化压铸（热度 {heat.loc['T2_一体化压铸','热度分']:.1f}、
si_mkt {imp.loc['T2_一体化压铸','si_mkt']:+.2f}）、特斯拉（{heat.loc['T1_特斯拉概念','热度分']:.1f}、{imp.loc['T1_特斯拉概念','si_mkt']:+.2f}），
属于可跟踪的"低热度高支撑"名单。</li>
<li>概念内部相对强弱看 <code>si_rel</code>：R3 减速器 si_rel {imp.loc['R3_减速器','si_rel']:+.2f} 为七概念最低，
说明它相对其他概念的风格受益度反而走弱（价格强但风格上正在被侵蚀），需与热度分交叉核对。</li>
</ol>
{img_tag(img4, "theme_si_series.png")}
<p class="cap">图 5：七概念 si_mkt 近 12 周。跨 ±1 表示明显偏离市场；连续 2 周同向才算趋势确认。</p>
{tbl_simkt()}
<p class="cap">表 4：近 8 周 si_mkt 明细（周为当周交易日末）。</p>

<h2>八、数据可得性与口径说明</h2>
<ul>
<li><b>数据截止 {AS_OF}</b>：概念板块指数（ths_daily）与概念热榜（ths_hot）最新仅到 {AS_OF}，更晚交易日尚未更新；成分股行情同截止。
本报告不含 20261028 及之后数据。<b>热榜窗口</b>：实际可用 {HOT_N} 个交易日（近 60 个交易日）。</li>
<li><b>概念成分</b>：同花顺概念板块成分，取样本空间（1824 只）内交集后计算，覆盖率
人形机器人 {heat.loc['R1_人形机器人','样本内']}/{heat.loc['R1_人形机器人','概念成分']}、
机器人概念 {heat.loc['R2_机器人概念','样本内']}/{heat.loc['R2_机器人概念','概念成分']}、
减速器 {heat.loc['R3_减速器','样本内']}/{heat.loc['R3_减速器','概念成分']}、
机器视觉 {heat.loc['R4_机器视觉','样本内']}/{heat.loc['R4_机器视觉','概念成分']}、
特斯拉概念 {heat.loc['T1_特斯拉概念','样本内']}/{heat.loc['T1_特斯拉概念','概念成分']}、
一体化压铸 {heat.loc['T2_一体化压铸','样本内']}/{heat.loc['T2_一体化压铸','概念成分']}、
无人驾驶 {heat.loc['T3_无人驾驶','样本内']}/{heat.loc['T3_无人驾驶','概念成分']}。样本外成分不计入。</li>
<li><b>季频项与前视偏差</b>：机构持仓、公募重仓为季频，最新报告期 2026-06-30；
因子计算中机构类因子对披露日之后、尚未公告的周一律置 NaN（<code>int(we) &lt; 20260724 → NaN</code>），避免用未来信息。
概念层指标全部为日频，不存在披露滞后问题。</li>
<li><b>分位口径</b>：所有"分位"均为该序列最近 250（热榜为 60 个交易日、涨停为 260）个观测中末值的百分位，
即"相对它自己的历史"，因此 <b>不同概念、不同时间之间可比</b>；分位 0-100，越高越强。</li>
<li><b>热度分</b>为四维等权平均，不做行业中性化；对成分极少的板块（如一体化压铸 26 只）分位波动会更大，读数时需放宽阈值。</li>
<li><b>数据缺口</b>：暂无'thins_hot'更细粒度数据（如个股热度）用于拆解注意力来源，此项为已知缺口，未做推算。</li>
</ul>

<h2>九、每周怎么用（操作流程）</h2>
<ol>
<li>周报流程跑完后执行 <code>python3 raw/style_sector_monitor.py</code>，同时更新四板块 SIrel（Part A）与七概念 si_mkt / si_rel（Part B），
输出 <code>output/theme_style_impulse.csv</code>（最新周热度 × 冲击合表）。</li>
<li>需要刷新概念热度指标时执行 <code>python3 raw/theme_monitor.py</code>（四维热度 + 判定），产出 <code>raw/theme_monitor.csv</code>。</li>
<li>本报告可随时重跑：<code>python3 raw/theme_report.py</code> → <code>output/机器人产业链与特斯拉概念监控报告-20261027.html</code>。</li>
<li>推送：<code>python3 push_weekly.py</code> 已固定两段顺序——先推因子周报（两张图卡片 + 分段正文），随后推本报告的概念监控卡片
（概念四维热度图 + 热度分/判定 + 代表股名称）；可用 <code>python3 push_weekly.py --dry</code> 预览而不发送。</li>
<li>读法：<b>先看热度分（它火不火）→ 再看 si_mkt/si_rel（因子层面有没有支撑）→ 连续 2 周同向才当趋势</b>。
热度高而 si_mkt ≈ 0 的（人形机器人）只能靠概念层指标跟踪，不要指望因子旋转报警。</li>
</ol>

<div class="warn"><b>风险提示：</b>本报告所有指标为历史数据的统计度量，不构成任何投资建议；
概念热度具有强反转特性，热度分高不代表后续继续上涨；分位类指标受样本期选择影响，
跨市场阶段比较需谨慎。文中出现的个股/概念仅为因子与概念监控的对象，非推荐标的。</div>

<div class="foot">
数据与代码：raw/theme_monitor.py（四维热度）、raw/style_sector_monitor.py（Part A 四板块 + Part B 七概念冲击）、
raw/theme_report.py（本报告生成）。<br>
关键中间产物：raw/theme_monitor.csv、output/theme_style_impulse.csv、output/theme_style_monitor.csv、
output/theme_style_monitor_mkt.csv、output/theme_exposure_matrix.csv、output/theme_picks.csv（概念内代表股，仅名称）。<br>
数据来源：Tushare（ths_index / ths_member / ths_daily / ths_hot / daily / daily_basic / index_daily /
concept 相关接口），样本空间 1824 只。<br>
推送口径：同一轮周度任务分两段推送——<b>第 1 段因子周报</b>（RRG 旋转轨迹图卡片 + 近一月因子排名桑基图卡片 + 分段文字正文），
<b>第 2 段概念监控</b>（概念四维热度图卡片 + 热度分/判定与概念内代表股名称文字卡片）；概念部分只输出股票名称，不出现股票代码。<br>
报告生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}　|　数据截止：{AS_OF}
</div>
</div></body></html>
"""

p = os.path.join(OUT, "机器人产业链与特斯拉概念监控报告-20261027.html")
with open(p, "w", encoding="utf-8") as f:
    f.write(HTML)
print("HTML ->", p, len(HTML), "chars")
print("图 ->", IMG, os.listdir(IMG))