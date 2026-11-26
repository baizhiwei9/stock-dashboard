# -*- coding: utf-8 -*-
"""报告末尾『基线组合统计』栏的统一注入助手 (fail-safe)。

各报告只需在文件顶部引入轻量 shim `_bs(key, kind)`，再在最终文本/卡片/HTML
组装点调用即可；本模块把 portfolio_stats.stats_block 的文本结果包装成不同载体：
    kind='text'  -> "" 或 "\\n\\n" + block           (markdown/纯文本报告)
    kind='lines' -> [] 或 ["", block]               (逐行 lines 列表)
    kind='card'  -> [] 或 [hr, div(lark_md)]         (飞书交互卡片 elements)
    kind='html'  -> "" 或 一段带样式的 <div>          (本地 HTML 报告)

任何异常均返回空值，绝不向调用方抛出，避免破坏生产报告推送。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_HTML = (
    '<div class="card" style="background:#fff;border:1px solid #e6e8eb;border-radius:10px;'
    'padding:14px 18px;margin:16px 0;font-size:13.5px;line-height:1.8;color:#374151">'
    '<b>基线组合统计</b><br>{body}</div>'
)


def _block(key):
    try:
        import portfolio_stats as ps

        return ps.stats_block(key) or ""
    except Exception:
        return ""


def segment(key, kind="text"):
    """按载体类型返回统计栏片段；无数据或异常时返回对应空值。"""
    b = _block(key)
    if not b:
        return [] if kind in ("card", "lines") else ""
    if kind == "lines":
        return ["", b]
    if kind == "card":
        return [
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": b}},
        ]
    if kind == "html":
        return _HTML.format(body=b.replace("\n", "<br>").replace("　", "&emsp;"))
    return "\n\n" + b