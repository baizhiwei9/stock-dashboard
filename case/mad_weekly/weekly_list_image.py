# -*- coding: utf-8 -*-
"""把合格股票名单渲染成一张表格图（白底扁平、三线表风格），供飞书卡片使用。"""
import csv
import os
import sys

import pandas as pd

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
FONT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
OUT = WS + "/weekly_out"

# 宽口径标签不入名单图，只保留有指向性的行业/题材
BROAD = {"同花顺中特估100", "同花顺漂亮100", "高股息精选", "融资融券", "沪股通", "深股通", "证金持股",
         "央企国企改革", "国企改革", "中字头股票", "乡村振兴", "专精特新", "上海国企改革",
         "西部大开发", "举牌", "预盈预增", "2026中报预增", "股权转让（并购重组）"}


def concept_map():
    """优先用已交付的概念聚类明细表（经人工核对），缺项再按成分股表取最具体的概念兜底。"""
    m = {}
    try:
        # py_deps 内的 openpyxl 已损坏，读 xlsx 时临时把该路径移出 sys.path
        keep = [x for x in sys.path if "py_deps" not in x]
        sys.path[:] = keep
        import openpyxl
        wb = openpyxl.load_workbook(WS + "/合格股票_概念聚类_含MAD_截至20261030.xlsx")
        for r in wb["概念-股票明细"].iter_rows(min_row=3, values_only=True):
            if r[2]:
                m.setdefault(r[2], [])
                if r[0] not in BROAD and r[0] not in m[r[2]]:
                    m[r[2]].append(r[0])
    except Exception as e:
        print("[list-img] 概念明细表读取失败（%s），改用成分股表兜底" % type(e).__name__)
    try:
        sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
    except Exception:
        pass
    try:
        rows = list(csv.DictReader(open(WS + "/concept_members.csv", encoding="utf-8-sig")))
        cnt = {}
        for r in rows:
            cnt[(r["概念代码"], r["概念名称"])] = cnt.get((r["概念代码"], r["概念名称"]), 0) + 1
        bystock = {}
        for r in rows:
            n = cnt[(r["概念代码"], r["概念名称"])]
            if n >= 10 and r["概念名称"] not in BROAD:
                bystock.setdefault(r["股票代码"], []).append((n, r["概念名称"]))
        for code, lst in bystock.items():
            m.setdefault(code, [])
            for _, nm in sorted(lst):
                if nm not in m[code]:
                    m[code].append(nm)
                if len(m[code]) >= 6:
                    break
    except Exception as e:
        print("[list-img] 成分股表读取失败（%s）" % type(e).__name__)
    return m


def main():
    sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager as fm
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    q = pd.read_csv(OUT + "/qualified.csv", dtype={"ts_code": str}).sort_values("PEG").reset_index(drop=True)
    as_of = "20261030"
    try:
        import json
        as_of = json.load(open(OUT + "/report_data.json", encoding="utf-8")).get("as_of", as_of)
    except Exception:
        pass
    cmap = concept_map()
    tags, miss = [], []
    for code, nm in zip(q.ts_code, q["名称"]):
        lst = cmap.get(code, [])[:2]
        if not lst:
            miss.append(nm)
        tags.append("／".join(lst)[:13] if lst else "—")

    fp = fm.FontProperties(fname=FONT)
    cols = [("名称", 0.24), ("买点", 0.17), ("PEG", 0.13), ("2027增速", 0.16), ("PE(TTM)", 0.13), ("行业/概念", 0.17)]
    n = len(q)
    row_h = 0.34
    fig_h = 1.5 + row_h * (n + 1) + 0.8  # 预留底部余量，避免末行贴边被裁
    fig = plt.figure(figsize=(12.6, fig_h), dpi=100)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.text(0.02, 0.965, "MAD 均线指选股·合格名单（PEG≤1.5，按 PEG 升序）", fontproperties=fp, fontsize=17, color="#1F3864")
    ax.text(0.02, 0.925, "数据截至 %s ｜ 共 %d 只 ｜ 行业/概念列取 1–2 个代表项 ｜ 来自 DoMate" % (as_of, n),
            fontproperties=fp, fontsize=10.5, color="#888888")
    top = 0.885
    L, R = 0.02, 0.98
    width = R - L
    xs, x = [], L
    for name, w in cols:
        xs.append(x); x += w * width
    ax.add_patch(Rectangle((L, top - 0.052), width, 0.052, facecolor="#1F3864"))
    for (name, _), x0 in zip(cols, xs):
        ax.text(x0 + 0.012, top - 0.026, name, fontproperties=fp, fontsize=11, color="white", va="center")
    rh = row_h / fig_h
    for i, r in q.iterrows():
        yy = top - 0.052 - (i + 1) * rh
        if i % 2 == 1:
            ax.add_patch(Rectangle((L, yy), width, rh, facecolor="#F2F6FB", edgecolor="none"))
        vals = [r["名称"], r["命中"], "%.3f" % r["PEG"], "+%.1f%%" % r["增速2027"], "%.1f" % r["PE_TTM"], tags[i]]
        for (cname, _), x0, v in zip(cols, xs, vals):
            ax.text(x0 + 0.012, yy + rh / 2, v, fontproperties=fp, fontsize=10, color="#222222", va="center")
        ax.plot([L, R], [yy, yy], color="#DDDDDD", lw=0.8)
    ax.plot([L, R], [top - 0.052, top - 0.052], color="#1F3864", lw=1.4)
    ax.plot([L, R], [top - 0.052 - n * rh, top - 0.052 - n * rh], color="#1F3864", lw=1.4)
    out = OUT + "/qualified_list.png"
    fig.savefig(out, dpi=100, facecolor="white")
    plt.close(fig)
    print("[list-img] 已生成名单图：%s（%d 行 × %d 列）" % (out, n, len(cols)))
    print("[list-img] 行业/概念来源：明细表 %d 只，成分股兜底 %d 只%s" %
          (n - len(miss), len(miss), ("（无映射：%s）" % "、".join(miss)) if miss else ""))


if __name__ == "__main__":
    main()