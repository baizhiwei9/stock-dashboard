# -*- coding: utf-8 -*-
"""多子图 v5（2026-10-30）——格式修整版。

相较 v4 的三处改动：
  1) 保留集合：A 档（买点有效 + 现价均线过关）+ C 档（点名关注·待买点/待突破，全部保留）；
     B 档（买点有效但均线未过关）剔除。→ 共 22 格（2 + 20）。
  2) 版面放大：2 列布局、每格约 9.0 x 5.6 英寸，字号整体放大约 1.7 倍（标题 20.5pt），
     面板下不再留大块空白（图例高度压到一行文字）。
  3) 拆成 3 张独立图片，避免一张长图在聊天窗口里被压缩到看不清：
       - A 档精选 2 格（银行 / 路桥）
       - C 档待买点 10 格（名次 1-10）
       - C 档待买点 10 格（名次 11-20）
     长口径文字移出图片（写在对话正文），图内只保留 2 行精简图例。
"""
import pickle
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.font_manager import FontProperties

NOTO = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
FP = FontProperties(fname=NOTO)
FPB = FontProperties(fname=NOTO, weight="bold")
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
plt.rcParams["axes.unicode_minus"] = False

panels_all = pickle.load(open(f"{BASE}/raw/_panels.pkl", "rb"))
WIN = 170
CO = dict(B1="#c0392b", B2="#e67e22", B3="#8e44ad", S1="#27ae60", S2="#16a085", S3="#7f8c8d")
CN = {"B1": "一买", "B2": "二买", "B3": "三买", "S1": "一卖", "S2": "二卖", "S3": "三卖"}
COL = dict(A="#1a7f37", C="#1f618d")
MA_C = dict(ma10="#1565c0", ma20="#8e44ad", ma40="#111111")

# 字号（v5 放大）
FS = dict(title=20.5, tick=10.3, badge=12.2, ma=12.0, info=11.2,
          sell=10.0, buy=10.0, buy_sel=14.0, zg=10.0, legend=13.0, legend_hdr=15.5, suptitle=19.0)

A = sorted([p for p in panels_all if p["grp"] == "A"], key=lambda p: p["因子名次"])
C = sorted([p for p in panels_all if p["grp"] == "C"], key=lambda p: p["因子名次"])


def zs_dev(ends, s, e, zd, zg):
    ps = [px for (row, tp, px) in ends if s <= row <= e]
    if not ps:
        return 0.0, 0.0
    up = max(ps) / zg - 1 if zg else 0.0
    dn = 1 - min(ps) / zd if zd else 0.0
    return up * 100, dn * 100


def draw_panel(ax, P):
    n = P["n"]; x0 = max(0, n - WIN)
    idx = np.arange(x0, n)
    o = np.array(P["o"]); h = np.array(P["h"]); l = np.array(P["l"]); c = np.array(P["c"])
    ma10 = np.array(P["ma10"]); ma20 = np.array(P["ma20"]); ma40 = np.array(P["ma40"])
    selz = P["sel_zs"]
    # 中枢背景
    for (s, e, zd, zg) in P["zs"]:
        if selz is not None and (s, e) == (selz[0], selz[1]):
            continue
        xs = max(s, x0); xe = min(e, n - 1)
        if xe < xs:
            continue
        ax.add_patch(Rectangle((xs - 0.5, zd), xe - xs + 1, zg - zd,
                               facecolor="#ffcf6b", alpha=0.28, edgecolor="#e69138", lw=1.0, zorder=1))
        if xe - xs > 14:
            ax.text((xs + xe) / 2, zg + (zg - zd) * 0.14, f"中枢 ZG={zg:.1f}",
                    fontproperties=FP, fontsize=FS["zg"], color="#a8641a", ha="center", va="bottom", zorder=4)
    # K 线
    for i in idx:
        up = c[i] >= o[i]
        col = "#d94b4b" if up else "#2e9e5b"
        ax.plot([i, i], [l[i], h[i]], color=col, lw=0.7, zorder=2)
        lo_, hi_ = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - 0.42, lo_), 0.84, max(hi_ - lo_, 1e-6),
                               facecolor=col, edgecolor=col, lw=0.25, zorder=2))
    # 缠论笔
    pv = None
    for e in P["ends"]:
        if e[0] < x0 - 12:
            pv = e
    seq = ([pv] if pv else []) + [e for e in P["ends"] if e[0] >= x0 - 12]
    if len(seq) >= 2:
        ax.plot([e[0] for e in seq], [e[2] for e in seq], color="#26557f", lw=1.25, zorder=3, alpha=0.92)
    # 均线
    fin = np.isfinite(ma40)
    ax.plot(idx, ma10[x0:n], color=MA_C["ma10"], lw=1.3, zorder=4, alpha=0.95)
    ax.plot(idx, ma20[x0:n], color=MA_C["ma20"], lw=1.3, zorder=4, alpha=0.95)
    if fin[x0:].any():
        ax.plot(idx, ma40[x0:n], color=MA_C["ma40"], lw=1.7, ls=(0, (6, 2)), zorder=4, alpha=0.9)
    # 锚定中枢
    ztxt = ""
    if selz is not None:
        s, e, zd, zg = selz
        xs = max(s, x0); xe = min(e, n - 1)
        if xe >= xs:
            ax.add_patch(Rectangle((xs - 0.5, zd), xe - xs + 1, zg - zd,
                                   facecolor="#c39bd3", alpha=0.20, edgecolor="#6c3483", lw=2.2, zorder=5))
        ax.axhline(zg, color="#6c3483", lw=1.5, ls=(0, (5, 2)), alpha=0.9, zorder=5)
        ax.axhline(zd, color="#6c3483", lw=0.8, ls=(0, (1, 2)), alpha=0.55, zorder=5)
        ax.text(n - 1, zg, f" ZG={zg:.1f}", fontproperties=FPB, fontsize=FS["zg"] + 0.4, color="#6c3483",
                ha="right", va="bottom", zorder=7,
                bbox=dict(boxstyle="square,pad=0.12", fc="white", ec="none", alpha=0.78))
        up, dn = zs_dev(P["ends"], s, e, zd, zg)
        flag = " · 中枢存疑(越界端点)" if (up > 20 or dn > 15) else ""
        ztxt = (f"锚定中枢 ZD={zd:.1f} ZG={zg:.1f} | 跨度{e - s + 1}行"
                f"{'(超宽)' if e - s + 1 > 60 else ''} | 上偏{up:.0f}%/下偏{dn:.0f}%{flag}")
    # 卖点
    for (row, kind) in P["sells"]:
        if row < x0:
            continue
        ax.scatter([row], [h[row] * 1.008], marker="v", s=70, color=CO[kind], zorder=6,
                   edgecolors="white", linewidths=0.5)
        ax.annotate(CN[kind], (row, h[row] * 1.008), textcoords="offset points", xytext=(0, 7),
                    fontproperties=FP, fontsize=FS["sell"], color=CO[kind], ha="center", zorder=7)
    # 买点
    for (row, kind) in P["buys"]:
        if row < x0:
            continue
        sel = (P["buy_row"] is not None and row == P["buy_row"] and kind == P["buy_kind"])
        ax.scatter([row], [l[row] * 0.992], marker="^", s=(320 if sel else 110), color=CO[kind], zorder=8,
                   edgecolors=("#111111" if sel else "white"), linewidths=(1.6 if sel else 0.5))
        ax.annotate(CN[kind] + ("★" if sel else ""), (row, l[row] * 0.992), textcoords="offset points",
                    xytext=(0, (-24 if sel else -16)), fontproperties=(FPB if sel else FP),
                    fontsize=(FS["buy_sel"] if sel else FS["buy"]), color=CO[kind], ha="center", zorder=9)
    if P["buy_row"] is not None and P["buy_row"] >= x0:
        br = P["buy_row"]; bp = c[br]
        ax.axhline(bp, color=CO[P["buy_kind"]], lw=1.0, ls=(0, (5, 3)), alpha=0.55, zorder=3)
    if selz is None and P.get("zg_line"):
        ax.axhline(P["zg_line"], color="#e69138", lw=1.0, ls=(0, (2, 2)), alpha=0.85, zorder=3)
    # 坐标轴
    ax.set_xlim(x0 - 1, n)
    ys = [l[x0:], h[x0:]]
    for arr in (ma10, ma20, ma40):
        w = arr[x0:][np.isfinite(arr[x0:])]
        if w.size:
            ys.append(w)
    if selz is not None:
        ys.append(np.array([selz[2], selz[3]]))
    ylo = min(a.min() for a in ys) * 0.982; yhi = max(a.max() for a in ys) * 1.016
    ax.set_ylim(ylo, yhi)
    tk = [min(n - 1, int(round(x0 + t * (n - 1 - x0) / 5))) for t in range(6)]
    ax.set_xticks(tk); ax.set_xticklabels([P["dates"][t][2:] for t in tk], fontproperties=FP, fontsize=FS["tick"])
    ax.tick_params(axis="y", labelsize=FS["tick"])
    for t in ax.get_yticklabels():
        t.set_fontproperties(FP)
    # 标题 / 角标
    grp = P["grp"]
    if grp == "A":
        ttl = (f"[A]【{P['层级']}】{P['名称']}   {P['buy_kind_cn']} {P['buy_kind']} @{P['buy_date'][5:]}   均线√")
    else:
        ttl = f"[C]【{P['层级']}】{P['名称']}   待买点/待突破   均线{'√' if P['ma_pass'] else '×'}"
    ax.set_title(ttl, fontproperties=FPB, fontsize=FS["title"], pad=9, color=COL[grp])
    ax.text(0.013, 0.975, f"名次#{P['因子名次']} · {P['tag']}", transform=ax.transAxes,
            fontproperties=FP, fontsize=FS["badge"], color=P["tag_col"], va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=P["tag_col"], lw=1.0, alpha=0.95), zorder=10)
    d40 = (c[-1] / ma40[-1] - 1) * 100 if np.isfinite(ma40[-1]) else float("nan")
    ln1 = f"最新 {P['latest']:.1f} ({P['chg']:+.2f}%) · MA10/20 {P['ma_diff']:+.2f}% · 连涨{P['streak']}"
    ln2 = f"距MA40 {d40:+.2f}% · 距中枢上沿 {P['dist_zg']:+.1f}%"
    if P["buy_kind"]:
        ln2 += f" · 买点日 {P['ma_at_buy']}"
    if ztxt:
        ln2 += "\n" + ztxt
    ax.text(0.013, 0.035, ln1 + "\n" + ln2, transform=ax.transAxes, fontproperties=FP, fontsize=FS["info"],
            color="#222222", va="bottom", ha="left", zorder=11,
            bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#b8b8b8", lw=0.7, alpha=0.93))
    ax.grid(axis="y", color="#e8e8e8", lw=0.5, zorder=0)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ms = ("站上MA40" if P["above40"] else "") + ("+MA10>MA20" if P["ma_gt"] else "")
    if ms == "":
        ms = "均线未过关"
    ax.text(0.013, 0.845, f"现价均线：{ms}", transform=ax.transAxes, fontproperties=FPB, fontsize=FS["ma"],
            color=("#1a7f37" if P["ma_pass"] else "#b03a2e"), va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.26", fc="#f6fbf0" if P["ma_pass"] else "#fdecea",
                      ec="#1a7f37" if P["ma_pass"] else "#b03a2e", lw=0.9, alpha=0.95), zorder=10)


LEG1 = "▲一买 B1(底背驰)   ▲二买 B2(回调不破前低)   ▲三买 B3(向上离开中枢后回抽不回中枢)   ▼一卖 S1 / 二卖 S2 / 三卖 S3   ★=本次所依据买点"
LEG2 = ("蓝折线=缠论笔    橙框=缠论中枢[ZD,ZG]    紫框+紫虚线=三买/三卖所依据的「锚定中枢」(ZG 处画水平线，可验「回抽不回中枢」)"
        "    均线：蓝=MA10 紫=MA20 黑粗虚线=MA40    红涨绿跌")


def render(plist, outname, subtitle):
    npanel = len(plist)
    ncol = 2
    nrow = (npanel + ncol - 1) // ncol
    fig_h = 6.0 * nrow + 2.6
    fig = plt.figure(figsize=(18.9, fig_h), dpi=100)
    # 第一行专门留给总标题，避免与面板标题重叠（旧版把总标题压在面板标题上，导致看不清）
    gs = fig.add_gridspec(nrow + 2, ncol, left=0.052, right=0.986, top=0.995, bottom=0.012,
                          hspace=0.44, wspace=0.15, height_ratios=[0.34] + [1.0] * nrow + [0.30])
    axt = fig.add_subplot(gs[0, :]); axt.axis("off")
    axt.text(0.5, 0.50,
             f"因子合成 × 缠论买卖点 × 均线过滤 · {subtitle}    |   数据截至 2026-10-29 收盘",
             transform=axt.transAxes, ha="center", va="center",
             fontproperties=FPB, fontsize=FS["suptitle"], color="#111111")
    for pi, P in enumerate(plist):
        ax = fig.add_subplot(gs[1 + pi // ncol, pi % ncol])
        draw_panel(ax, P)
    axl = fig.add_subplot(gs[nrow + 1, :]); axl.axis("off")
    axl.text(0.002, 0.98, "图例", fontproperties=FPB, fontsize=FS["legend_hdr"], va="top")
    axl.text(0.075, 0.98, LEG1 + "\n" + LEG2, fontproperties=FP, fontsize=FS["legend"],
             va="top", color="#111111", linespacing=1.9)
    out = f"{BASE}/output/{outname}"
    fig.savefig(out)
    plt.close(fig)
    print("[saved]", outname, f"panels={npanel} rows={nrow} cols={ncol}")
    return out


JOBS = [
    (A, "factor_chan_panels-A档精选2格.png", "A 档：买点有效 + 现价均线过关（银行 / 路桥）"),
    (C[:10], "factor_chan_panels-待买点10格-01.png", "C 档：点名关注 · 待买点/待突破（按因子名次 1-10）"),
    (C[10:20], "factor_chan_panels-待买点10格-02.png", "C 档：点名关注 · 待买点/待突破（按因子名次 11-20）"),
]
for plist, name, sub in JOBS:
    render(plist, name, sub)
    print("   ", [(p["grp"], p["层级"], p["名称"], f"#{p['因子名次']}", p["buy_kind"]) for p in plist])
print("A档:", len(A), " C档:", len(C), " 剔除B档:", sum(1 for p in panels_all if p["grp"] == "B"))