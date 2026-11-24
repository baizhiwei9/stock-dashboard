# -*- coding: utf-8 -*-
"""多子图 v3（2026-10-30）：在 v2 基础上——
  1) 画出 MA10/MA20/MA40 三条均线，肉眼可验「站上 MA40 / MA10 上穿 MA20」过滤；
  2) 三买格子把「所依据的锚定中枢」高亮（紫框 + ZG 水平线 + 跨度/日期），肉眼可验「回抽不回中枢」；
  3) 按 A/B/C 分档重排：A=买点有效且现价均线过关；B=买点有效但现价均线未过关；C=用户点名关注·暂无有效买点；
  4) 对锚定中枢做「越界端点」校验，偏离>20% 标注「中枢存疑」。
"""
import pickle, numpy as np
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

panels = pickle.load(open(f"{BASE}/raw/_panels.pkl", "rb"))
WIN = 170
CO = dict(B1="#c0392b", B2="#e67e22", B3="#8e44ad", S1="#27ae60", S2="#16a085", S3="#7f8c8d")
CN = {"B1": "一买", "B2": "二买", "B3": "三买", "S1": "一卖", "S2": "二卖", "S3": "三卖"}
COL = dict(A="#1e7a34", B="#b03a2e", C="#1f618d")
GC = dict(A="A 买点有效·现价均线过关", B="B 买点有效·现价均线未过关", C="C 点名关注·暂无有效买点")
MA_C = dict(ma10="#1565c0", ma20="#8e44ad", ma40="#111111")


def zs_dev(ends, s, e, zd, zg):
    """锚定中枢越界端点校验：返回 (上偏%, 下偏%)。"""
    ps = [px for (row, tp, px) in ends if s <= row <= e]
    if not ps:
        return 0.0, 0.0
    up = max(ps) / zg - 1 if zg else 0.0
    dn = 1 - min(ps) / zd if zd else 0.0
    return up * 100, dn * 100


npanel = len(panels)
ncol = 6
nrow = (npanel + ncol - 1) // ncol
fig = plt.figure(figsize=(38.0, 4.75 * nrow + 4.0), dpi=100)
gs = fig.add_gridspec(nrow + 1, ncol, left=0.028, right=0.988, top=0.930, bottom=0.028,
                      hspace=0.46, wspace=0.14, height_ratios=[1.0] * nrow + [0.72])

for pi, P in enumerate(panels):
    ax = fig.add_subplot(gs[pi // ncol, pi % ncol])
    n = P["n"]; x0 = max(0, n - WIN)
    idx = np.arange(x0, n)
    o = np.array(P["o"]); h = np.array(P["h"]); l = np.array(P["l"]); c = np.array(P["c"])
    ma10 = np.array(P["ma10"]); ma20 = np.array(P["ma20"]); ma40 = np.array(P["ma40"])
    selz = P["sel_zs"]
    # ---- 中枢（背景，非锚定者先画） ----
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
                    fontproperties=FP, fontsize=6.5, color="#a8641a", ha="center", va="bottom", zorder=4)
    # ---- K线 ----
    for i in idx:
        up = c[i] >= o[i]
        col = "#d94b4b" if up else "#2e9e5b"
        ax.plot([i, i], [l[i], h[i]], color=col, lw=0.6, zorder=2)
        lo_, hi_ = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - 0.42, lo_), 0.84, max(hi_ - lo_, 1e-6),
                               facecolor=col, edgecolor=col, lw=0.25, zorder=2))
    # ---- 笔 ----
    pv = None
    for e in P["ends"]:
        if e[0] < x0 - 12:
            pv = e
    seq = ([pv] if pv else []) + [e for e in P["ends"] if e[0] >= x0 - 12]
    if len(seq) >= 2:
        ax.plot([e[0] for e in seq], [e[2] for e in seq], color="#26557f", lw=1.15, zorder=3, alpha=0.92)
    # ---- 均线 ----
    fin = np.isfinite(ma40)
    ax.plot(idx, ma10[x0:n], color=MA_C["ma10"], lw=1.15, zorder=4, alpha=0.95)
    ax.plot(idx, ma20[x0:n], color=MA_C["ma20"], lw=1.15, zorder=4, alpha=0.95)
    if fin[x0:].any():
        ax.plot(idx, ma40[x0:n], color=MA_C["ma40"], lw=1.5, ls=(0, (6, 2)), zorder=4, alpha=0.9)
    # ---- 锚定中枢（高亮，画在最上层的中枢） ----
    ztxt = ""
    if selz is not None:
        s, e, zd, zg = selz
        xs = max(s, x0); xe = min(e, n - 1)
        if xe >= xs:
            ax.add_patch(Rectangle((xs - 0.5, zd), xe - xs + 1, zg - zd,
                                   facecolor="#c39bd3", alpha=0.20, edgecolor="#6c3483", lw=2.0, zorder=5))
        zg_txt = f"锚定中枢 ZG={zg:.1f}"
        ax.axhline(zg, color="#6c3483", lw=1.35, ls=(0, (5, 2)), alpha=0.9, zorder=5)
        ax.axhline(zd, color="#6c3483", lw=0.75, ls=(0, (1, 2)), alpha=0.55, zorder=5)
        ax.text(n - 1, zg, f" ZG={zg:.1f}", fontproperties=FPB, fontsize=6.6, color="#6c3483",
                ha="right", va="bottom", zorder=7,
                bbox=dict(boxstyle="square,pad=0.12", fc="white", ec="none", alpha=0.75))
        up, dn = zs_dev(P["ends"], s, e, zd, zg)
        flag = " · 中枢存疑(越界端点)" if (up > 20 or dn > 15) else ""
        ztxt = (f"锚定中枢 ZD={zd:.1f} ZG={zg:.1f} | 跨度{e - s + 1}行"
                f"{'(超宽)' if e - s + 1 > 60 else ''} | 上偏{up:.0f}%/下偏{dn:.0f}%{flag}")
    # ---- 买卖点 ----
    for (row, kind) in P["sells"]:
        if row < x0:
            continue
        ax.scatter([row], [h[row] * 1.008], marker="v", s=42, color=CO[kind], zorder=6,
                   edgecolors="white", linewidths=0.45)
        ax.annotate(CN[kind], (row, h[row] * 1.008), textcoords="offset points", xytext=(0, 6),
                    fontproperties=FP, fontsize=6.5, color=CO[kind], ha="center", zorder=7)
    for (row, kind) in P["buys"]:
        if row < x0:
            continue
        sel = (P["buy_row"] is not None and row == P["buy_row"] and kind == P["buy_kind"])
        ax.scatter([row], [l[row] * 0.992], marker="^", s=(190 if sel else 62), color=CO[kind], zorder=8,
                   edgecolors=("#111111" if sel else "white"), linewidths=(1.5 if sel else 0.45))
        ax.annotate(CN[kind] + ("★" if sel else ""), (row, l[row] * 0.992), textcoords="offset points",
                    xytext=(0, (-15 if sel else -10)), fontproperties=(FPB if sel else FP),
                    fontsize=(9.0 if sel else 6.7), color=CO[kind], ha="center", zorder=9)
    # 入选买点价水平线
    if P["buy_row"] is not None and P["buy_row"] >= x0:
        br = P["buy_row"]; bp = c[br]
        ax.axhline(bp, color=CO[P["buy_kind"]], lw=0.85, ls=(0, (5, 3)), alpha=0.55, zorder=3)
    # 最近中枢上沿参考线（无 B3 锚定中枢时提示待突破位）
    if selz is None and P.get("zg_line"):
        ax.axhline(P["zg_line"], color="#e69138", lw=0.85, ls=(0, (2, 2)), alpha=0.8, zorder=3)
    # ---- 坐标与标题 ----
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
    ax.set_xticks(tk); ax.set_xticklabels([P["dates"][t][2:] for t in tk], fontproperties=FP, fontsize=6.8)
    ax.tick_params(axis="y", labelsize=6.8)
    for t in ax.get_yticklabels():
        t.set_fontproperties(FP)
    grp = P["grp"]
    if grp in ("A", "B"):
        ttl = (f"[{grp}]【{P['层级']}】{P['名称']}   {P['buy_kind_cn']} {P['buy_kind']} @{P['buy_date'][5:]}   "
               f"均线{'√' if P['ma_pass'] else '×'}")
    else:
        ma_txt = ("站上MA40" if P["above40"] else "") + ("/MA10>MA20" if P["ma_gt"] else "")
        ttl = f"[C]【{P['层级']}】{P['名称']}   待买点 / 待突破   均线{'√' if P['ma_pass'] else '×'}"
        if ma_txt == "":
            ttl += "(未过关) "
    ax.set_title(ttl, fontproperties=FPB, fontsize=12.0, pad=7, color=COL[grp])
    ax.text(0.014, 0.973, f"名次#{P['因子名次']} · {P['tag']}", transform=ax.transAxes,
            fontproperties=FP, fontsize=7.9, color=P["tag_col"], va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=P["tag_col"], lw=0.85, alpha=0.93), zorder=10)
    # 左下信息（两行）
    d40 = (c[-1] / ma40[-1] - 1) * 100 if np.isfinite(ma40[-1]) else float("nan")
    ln1 = f"最新 {P['latest']:.1f} ({P['chg']:+.2f}%) · MA10/20 {P['ma_diff']:+.2f}% · 连涨{P['streak']}"
    ln2 = f"距MA40 {d40:+.2f}% · 距中枢上沿 {P['dist_zg']:+.1f}%"
    if P["buy_kind"]:
        ln2 += f" · 买点日 {P['ma_at_buy']}"
    if ztxt:
        ln2 += "\n" + ztxt
    ax.text(0.014, 0.035, ln1 + "\n" + ln2, transform=ax.transAxes, fontproperties=FP, fontsize=7.2,
            color="#222222", va="bottom", ha="left", zorder=11,
            bbox=dict(boxstyle="round,pad=0.20", fc="white", ec="#b8b8b8", lw=0.6, alpha=0.92))
    ax.grid(axis="y", color="#e8e8e8", lw=0.45, zorder=0)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    # 左上角：均线状态徽标（现价）
    ms = ("站上MA40" if P["above40"] else "") + ("+MA10>MA20" if P["ma_gt"] else "")
    if ms == "":
        ms = "均线未过关"
    ax.text(0.014, 0.845, f"现价均线：{ms}", transform=ax.transAxes, fontproperties=FPB, fontsize=7.8,
            color=("#1e7a34" if P["ma_pass"] else "#b03a2e"), va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.22", fc="#f7fbe9" if P["ma_pass"] else "#fdecea",
                      ec="#1e7a34" if P["ma_pass"] else "#b03a2e", lw=0.8, alpha=0.95), zorder=10)

# ---- 通栏：图例与口径 ----
axl = fig.add_subplot(gs[nrow, :]); axl.axis("off")
axl.text(0.006, 0.955, "图例", fontproperties=FPB, fontsize=15.5, va="top")
axl.text(0.006, 0.60,
         "▲一买 B1(底背驰)  ▲二买 B2(回调不破前低)  ▲三买 B3(向上离开中枢后回抽不回中枢)   ★=本次入选所依据买点\n"
         "▼一卖 S1 / ▼二卖 S2 / ▼三卖 S3     蓝折线=缠论笔    橙实框=缠论中枢[ZD,ZG]（背景，浅橙）\n"
         "紫高亮框+紫虚线=三买/三卖所依据的「锚定中枢」，并在 ZG 处画水平线，肉眼可验「回抽不回中枢」\n"
         "均线：蓝=MA10  紫=MA20  黑粗虚线=MA40",
         fontproperties=FP, fontsize=12.0, va="top", color="#111111", linespacing=1.5)
m1 = 0.375
axl.text(m1, 0.955, "分档与均线过滤", fontproperties=FPB, fontsize=15.5, va="top")
axl.text(m1, 0.60,
         "前 2 格 = A：缠论买点有效，且现价均线过关。\n"
         "中 8 格 = B：缠论买点有效，但现价均线未过关（趋势未确认，仅备查）。\n"
         "后 20 格 = C：用户点名关注板块中，近 40 个交易日无有效买点者，标「待买点/待突破」。\n"
         "均线过滤 = 现价站上 MA40  或  MA10 > MA20（二者之一即过关）；每格左上徽标给出现价均线状态。\n"
         "电机（概念#1）：现价站上 MA40（882.88 vs MA40≈882.8，压线）+ 今日 MA10 刚上穿 MA20 → 过滤过关；\n"
         "  但近 40 个交易日无有效买点（仅 09-03 三卖），仍列 C 档。",
         fontproperties=FP, fontsize=12.0, va="top", color="#111111", linespacing=1.5)
m2 = 0.760
axl.text(m2, 0.955, "口径与提示", fontproperties=FPB, fontsize=15.5, va="top")
axl.text(m2, 0.60,
         "三买口径：向上离开中枢（顶 > ZG）后，回抽底 > ZG 才成立；若回抽跌回中枢内则本次离开作废、须重新突破；\n"
         "  同一中枢只取首次回抽（已修正旧中枢反复触发产生的「陈旧三买」，如玻璃 09-07 已剔除）。\n"
         "锚定中枢「越界端点」校验：中枢内端点价相对 [ZD,ZG] 上偏 >20% 或下偏 >15% 时标注「中枢存疑」。\n"
         "  跨度 >60 行（约 3 个月）标注「超宽」——此类中枢处「回抽不回中枢」约束较弱，需人工复核（如培育钻石）。\n"
         "板块指数为成分股等权合成，仅作风格代表；数据截至 2026-10-29 收盘；不构成投资建议。",
         fontproperties=FP, fontsize=12.0, va="top", color="#b03a2e", linespacing=1.5)

fig.suptitle("因子合成 × 缠论买卖点 × 均线过滤 · 三层板块（行业/主题/概念）子图   |   数据截至 2026-10-29 收盘   |   "
             "A 档 2 格（银行/路桥）· B 档 8 格（买点有效但均线未过关）· C 档 20 格（点名关注·待买点）",
             fontproperties=FPB, fontsize=19.5, y=0.962)
OUTNAME = "factor_chan_panels-30格-均线过滤-中枢校验.png"
fig.savefig(f"{BASE}/output/{OUTNAME}")
print("[saved]", f"{BASE}/output/{OUTNAME}", f"panels={npanel} rows={nrow}")
print("组别计数:", {g: sum(1 for p in panels if p["grp"] == g) for g in "ABC"})