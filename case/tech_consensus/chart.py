# -*- coding: utf-8 -*-
"""Pillow 绘制多空平观点汇总图：行=技术方法，列=宽基指数，底部=汇总观点"""
import os

from PIL import Image, ImageDraw, ImageFont

from config import FONT_PATH, OUT_DIR, ETF_POOL

# 颜色：多=红（A股红涨），空=绿，震荡=灰，N/A=白（空心）
COLOR_LONG = (198, 44, 44)
COLOR_SHORT = (22, 138, 74)
COLOR_FLAT = (150, 150, 150)
COLOR_NA = (245, 245, 245)
COLOR_TEXT = (30, 30, 30)
COLOR_HEAD = (245, 246, 248)
COLOR_GRID = (180, 185, 192)
COLOR_HEADER_BG = (230, 233, 238)

VIEW_COLOR = {
    "多": COLOR_LONG,
    "空": COLOR_SHORT,
    "震荡": COLOR_FLAT,
    "N/A": COLOR_NA,
}
VIEW_TEXT = {"多": "看多", "空": "看空", "震荡": "震荡", "N/A": "N/A"}


def _font(size):
    return ImageFont.truetype(FONT_PATH, size)


def draw_summary_chart(method_names, view_matrix, consensus_row, asof_date,
                       ret5_row=None, out_name="summary.png"):
    """view_matrix: {方法名: {code: view}}；consensus_row: {code: (结论, score)}
    返回图片绝对路径
    """
    codes = [e["code"] for e in ETF_POOL]
    names = [e["name"] for e in ETF_POOL]
    m_names = list(method_names)

    W, H = 1500, 990
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # 标题
    d.text((40, 22), f"宽基指数 · 技术分析多空平观点汇总（{asof_date}）",
           fill=COLOR_TEXT, font=_font(34))

    # 布局参数
    x0, y0 = 40, 96
    row_h = 74
    col_w = 168
    first_w = 300
    header_h = 62
    n_cols = len(codes)

    # 表头
    d.rectangle([x0, y0, x0 + first_w + n_cols * col_w, y0 + header_h], fill=COLOR_HEADER_BG)
    d.rectangle([x0, y0, x0 + first_w, y0 + header_h], outline=COLOR_GRID)
    d.text((x0 + 14, y0 + 14), "技术方法", fill=COLOR_TEXT, font=_font(22))
    for j, nm in enumerate(names):
        cx = x0 + first_w + j * col_w
        d.rectangle([cx, y0, cx + col_w, y0 + header_h], outline=COLOR_GRID)
        d.text((cx + 18, y0 + 12), nm, fill=COLOR_TEXT, font=_font(24))

    # 方法行
    y = y0 + header_h
    for i, m in enumerate(m_names):
        d.rectangle([x0, y, x0 + first_w, y + row_h], fill=COLOR_HEAD)
        d.rectangle([x0, y, x0 + first_w, y + row_h], outline=COLOR_GRID)
        d.text((x0 + 14, y + 22), m, fill=COLOR_TEXT, font=_font(24))
        for j, c in enumerate(codes):
            cx = x0 + first_w + j * col_w
            view = view_matrix.get(m, {}).get(c, "N/A")
            color = VIEW_COLOR.get(view, COLOR_NA)
            d.rectangle([cx, y, cx + col_w, y + row_h], fill=color, outline=COLOR_GRID)
            # SMC 日/周线级别机会：多/空格子标星 + 大字 + 加粗感（描边）突出
            if m == "SMC日周线" and view in ("多", "空"):
                label = "★ 看多" if view == "多" else "★ 看空"
                d.text((cx + 16, y + 10), label, fill="white", font=_font(34))
            else:
                d.text((cx + 42, y + 20), VIEW_TEXT.get(view, "?"),
                       fill="white" if view != "N/A" else COLOR_TEXT, font=_font(26))
        y += row_h

    # 汇总行
    d.rectangle([x0, y, x0 + first_w, y + row_h], fill=COLOR_HEADER_BG)
    d.rectangle([x0, y, x0 + first_w, y + row_h], outline=COLOR_GRID)
    d.text((x0 + 14, y + 22), "汇总观点（买/卖/平）", fill=COLOR_TEXT, font=_font(24))
    for j, c in enumerate(codes):
        cx = x0 + first_w + j * col_w
        view, score = consensus_row.get(c, ("平", 0))[:2]
        color = VIEW_COLOR.get(view, COLOR_FLAT)
        d.rectangle([cx, y, cx + col_w, y + row_h], fill=color, outline=COLOR_GRID)
        label = {"买": "买", "卖": "卖", "平": "平"}.get(view, view)
        d.text((cx + 58, y + 12), label, fill="white", font=_font(34))
        d.text((cx + 40, y + 48), f"score {score:+d}", fill="white", font=_font(16))
    y += row_h

    # 最近5日涨跌幅（若提供）
    if ret5_row:
        y += 12
        d.text((x0, y + 6), "近5日涨跌：", fill=COLOR_TEXT, font=_font(20))
        for j, c in enumerate(codes):
            cx = x0 + first_w + j * col_w
            v = ret5_row.get(c)
            txt = f"{v*100:+.2f}%" if v is not None else "--"
            col = COLOR_LONG if (v or 0) >= 0 else COLOR_SHORT
            d.text((cx + 30, y + 6), txt, fill=col, font=_font(20))

    # 图例
    ly = y + 60
    d.text((x0, ly), "图例：", fill=COLOR_TEXT, font=_font(20))
    lx = x0 + 90
    for label, color in [("看多(买)", COLOR_LONG), ("看空(卖)", COLOR_SHORT),
                          ("震荡(平)", COLOR_FLAT), ("N/A(模型池外)", COLOR_NA)]:
        d.rectangle([lx, ly, lx + 34, ly + 26], fill=color, outline=COLOR_GRID)
        d.text((lx + 44, ly + 2), label, fill=COLOR_TEXT, font=_font(20))
        lx += 44 + len(label) * 22 + 30

    # 脚注
    d.text((x0, H - 56),
           "仅技术分析信号合计（多=+1 空=-1 震荡=0，N/A不计票）；不构成投资建议。",
           fill=(120, 120, 120), font=_font(18))

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, out_name)
    img.save(path)
    return path