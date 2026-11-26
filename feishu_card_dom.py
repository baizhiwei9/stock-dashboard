# -*- coding: utf-8 -*-
"""feishu_card_dom — 飞书 interactive 卡片 JSON → 可嵌入的 DOM 片段。

用途：任务发给飞书的交互式卡片（`msg_type=interactive`）本身就是一份结构化的
「报告关键数据 + 版式」，不必再让展示层去猜 report.md 的格式。任务侧把卡片原样
落盘到 `out/card.json`，展示层用 `card_to_html()` 直接转成页面 DOM，两边同一份口径。

支持的元素（飞书卡片 1.x 常用子集）：
  header、div / markdown（lark_md → HTML）、hr、note、fields、table、action(按钮)、img(占位)
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

TEMPLATE_COLOR = {
    "blue": "#2f6feb", "wathet": "#5a8fd0", "turquoise": "#12a3a3", "green": "#2ea44f",
    "yellow": "#d4a72c", "orange": "#e07b39", "red": "#d64545", "carmine": "#c0392b",
    "violet": "#8a63d2", "purple": "#7d4bb3", "indigo": "#4b5bd6", "grey": "#6b7280",
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def md_to_html(text: str) -> str:
    """飞书 lark_md 的**最小**渲染：转义 → 行内语法 → 换行。

    只做可确定的部分：`**粗**` → <b>、`` `code` `` → <code>、`[文字](链接)` → <a>；
    去掉 `<at ...>` 与 `<font ...>` 标签（保留内部文字）。其它原样输出，不猜。
    """
    s = _esc(text)
    s = re.sub(r"<at[^>]*>(.*?)</at>", r"\1", s)
    s = re.sub(r"<at[^>]*/?>", "", s)
    s = re.sub(r"</?font[^>]*>", "", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noreferrer">\1</a>', s)
    s = s.replace("\r\n", "\n").replace("\n", "<br>")
    return s


def _text_of(node: Any) -> str:
    """从 {tag: lark_md|plain_text, content: ...} 取文本。"""
    if isinstance(node, dict):
        return str(node.get("content") or "")
    return str(node or "")


def _div(el: Dict) -> str:
    txt = el.get("text") or {}
    content = _text_of(txt)
    if not content:
        return ""
    body = md_to_html(content) if (txt.get("tag") or "lark_md") == "lark_md" else _esc(content)
    return '<div class="fc-div">%s</div>' % body


def _note(el: Dict) -> str:
    parts = [_text_of(x.get("text") if isinstance(x, dict) else x) for x in (el.get("elements") or [])]
    body = "<br>".join(md_to_html(p) for p in parts if p)
    return '<div class="fc-note">%s</div>' % body if body else ""


def _fields(el: Dict) -> str:
    cells = []
    for f in (el.get("fields") or []):
        txt = f.get("text") if isinstance(f, dict) else f
        val = md_to_html(_text_of(txt))
        if not val:
            continue
        cells.append('<div class="fc-field%s">%s</div>'
                     % (" short" if (f.get("is_short") if isinstance(f, dict) else False) else "", val))
    return '<div class="fc-fields">%s</div>' % "".join(cells) if cells else ""


def _table(el: Dict) -> str:
    cols = [c.get("display_name") or c.get("name") or "" for c in (el.get("columns") or [])]
    if not cols:
        return ""
    head = "".join("<th>%s</th>" % _esc(c) for c in cols)
    rows = []
    for r in (el.get("rows") or []):
        cells = "".join("<td>%s</td>" % _esc(r.get(c.get("name") if isinstance(c, dict) else "", ""))
                        for c in (el.get("columns") or []))
        rows.append("<tr>%s</tr>" % cells)
    return ('<div class="fc-table"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (head, "".join(rows)))


def _actions(el: Dict) -> str:
    btns = []
    for a in (el.get("actions") or []):
        if not isinstance(a, dict):
            continue
        label = _text_of(a.get("text") or a.get("content") or "")
        if not label:
            continue
        url = a.get("url")
        if isinstance(url, dict):                       # 飞书多端链接
            url = url.get("pc_url") or url.get("ios_url") or url.get("android_url")
        if url:
            btns.append('<a class="fc-btn" href="%s" target="_blank" rel="noreferrer">%s</a>'
                        % (_esc(url), _esc(label)))
        else:
            btns.append('<span class="fc-btn ghost">%s</span>' % _esc(label))
    return '<div class="fc-actions">%s</div>' % "".join(btns) if btns else ""


def _element(el: Any) -> str:
    if not isinstance(el, dict):
        return ""
    tag = el.get("tag")
    if tag == "div":
        return _div(el)
    if tag == "markdown":
        return '<div class="fc-div">%s</div>' % md_to_html(str(el.get("content") or ""))
    if tag == "hr":
        return "<hr class=\"fc-hr\">"
    if tag == "note":
        return _note(el)
    if tag == "fields":
        return _fields(el)
    if tag == "table":
        return _table(el)
    if tag == "action":
        return _actions(el)
    if tag == "img":
        return '<div class="fc-img">图片 %s（灯箱里看原图）</div>' % _esc(el.get("alt") or el.get("img_key") or "")
    # column_set / 交互组件等暂不渲染，避免输出不确定的版式
    return ""


def card_to_html(card: Any) -> str:
    """飞书卡片 → 可嵌入 DOM。接受 `{msg_type, card:{...}}` 或直接 `card:{...}`。"""
    if isinstance(card, dict) and "card" in card and isinstance(card["card"], dict):
        card = card["card"]
    if not isinstance(card, dict):
        return ""
    color = TEMPLATE_COLOR.get(str(((card.get("header") or {}).get("template")) or "blue"), "#2f6feb")
    title = _text_of(((card.get("header") or {}).get("title")) or {})
    body = "".join(_element(el) for el in (card.get("elements") or []))
    if not (title or body):
        return ""
    head = ('<div class="fc-head" style="--fc:%s"><b>%s</b></div>' % (color, _esc(title))) if title else ""
    return '<div class="fc-card">%s%s</div>' % (head, body)


def card_markdown(card: Any) -> str:
    """卡片 → 纯 Markdown 兜底（只要文字，不带版式）。"""
    if isinstance(card, dict) and "card" in card and isinstance(card["card"], dict):
        card = card["card"]
    if not isinstance(card, dict):
        return ""
    out: List[str] = []
    title = _text_of(((card.get("header") or {}).get("title")) or {})
    if title:
        out.append("# " + title)
    for el in (card.get("elements") or []):
        if not isinstance(el, dict):
            continue
        tag = el.get("tag")
        if tag in ("div", "markdown"):
            txt = _text_of(el.get("text") or {}) or str(el.get("content") or "")
            if txt:
                out.append(txt)
        elif tag == "note":
            txt = " ".join(_text_of(x.get("text") if isinstance(x, dict) else x)
                           for x in (el.get("elements") or []))
            if txt.strip():
                out.append("> " + txt.strip())
        elif tag == "hr":
            out.append("---")
    return "\n\n".join(out)


CSS = """
.fc-card{border:1px solid var(--line);border-radius:12px;background:var(--card);
  overflow:hidden;margin:12px 0 18px}
.fc-head{display:flex;align-items:center;gap:8px;padding:10px 14px;
  background:linear-gradient(90deg,color-mix(in srgb,var(--fc) 26%,transparent),transparent);
  border-bottom:1px solid var(--line);font-size:12.5px;color:var(--tx)}
.fc-head b{font-weight:600}
.fc-div{padding:8px 14px;font-size:12.5px;line-height:1.7;color:var(--tx2)}
.fc-div b{color:var(--tx);font-weight:600}
.fc-div code{font-size:11.5px;padding:1px 5px;border-radius:5px;background:#0f141c;
  border:1px solid var(--line)}
.fc-hr{border:0;border-top:1px solid var(--line);margin:2px 0}
.fc-note{padding:8px 14px;font-size:11.5px;color:var(--tx3);background:#0c1117}
.fc-fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
  gap:8px;padding:10px 14px}
.fc-field.short{grid-column:span 1}
.fc-table{padding:8px 14px;overflow-x:auto}
.fc-table table{border-collapse:collapse;font-size:12px;width:100%}
.fc-table th,.fc-table td{border:1px solid var(--line);padding:5px 8px;text-align:left;
  color:var(--tx2);white-space:nowrap}
.fc-table th{color:var(--tx);background:#0f141c}
.fc-actions{display:flex;gap:8px;padding:10px 14px}
.fc-btn{display:inline-block;padding:5px 12px;border:1px solid var(--line);border-radius:8px;
  font-size:12px;color:var(--blue);text-decoration:none}
.fc-btn.ghost{color:var(--tx3)}
.fc-img{padding:8px 14px;font-size:11.5px;color:var(--tx3)}
"""
