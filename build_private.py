#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_private.py — 私有任务展示页（precious-task）

公开页（`build.py` → `index.html`）与私有页（本脚本 → `private.html`）是**两套页面**：
各有各的入口，互不混排。样式、卡片渲染、取数口径全部复用 `build.py`，不复制框架、
不另算一份数据。

除了指标，私有页还会把任务产出的**图**展示出来：扫 `out/ charts/ runs/ ...` 里最近的图，
原图复制到页面同目录的 `assets/private/<key>/`、另生成缩略图，页面上先给缩略网格，
点图开灯箱看原图（可滚轮缩放、拖动、1:1；长图不会被压扁）。

私有仓通过 `CASE_EXTRA_DIRS` 挂进来，任务在 `task.json` 里写 `visibility=private`
（历史写法 `internal` 等价）。私有页只读这些任务；公开页不认识它们。

用法：
    export CASE_EXTRA_DIRS=/srv/private-tasks/case
    export PYTHONPATH=/srv/monitor/datahub-multi-tier:$PWD
    python3 build_private.py                        # → private.html + assets/private/
    python3 build_private.py --out /srv/monitor/private.html
    python3 build_private.py --no-images            # 只要指标，不扫图
    python3 build_private.py --max-images 12        # 每个任务最多几张（默认 6）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build as B                                            # noqa: E402  复用 CSS / 卡片渲染 / 取数
import feishu_card_dom as FCD                                # noqa: E402  飞书卡片 → DOM

# task.json 没写 section / section_domain 的任务：不丢，单独归到「未分板块」
UNSORTED = "unsorted"
SECTION_ORDER = ("stock", "watch", "pick", "futures", "realestate", "macro",
                 "liquidity", "bond", "option", "risk", "modules", UNSORTED)
# 板块别名只维护一份，在 case/_lib/case_registry.py 的 SECTION_ALIAS，
# 这里不再平行维护（credit/credit_spread→bond、macro_watch→macro 都在那边）。
# sections.json 里没有的板块（未归类）在本页的标题
EXTRA_SECTIONS = {
    # watch / pick 只在本页出现，sections.json（公开看板导航）里没有这两个 key
    "watch": {"num": "—", "title": "自选股",
              "sub": "我的实盘 / 自选股（默尔根、亚丽）：持仓风险与机会"},
    "pick": {"num": "—", "title": "选股",
             "sub": "选股 / 选行业 / 选概念：策略给出的标的池"},
    UNSORTED: {"num": "—", "title": "未分板块",
               "sub": "私有仓里没写 section / section_domain 的任务，先按原样列出"},
}
# 任务目录下会出图的子目录：约定俗成的几个，逐层扫但跳过缩略图与缓存
IMG_DIRS = ("out", "charts", "runs", "output", "figures", "reports", "assets", "data")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")
SKIP_DIRS = {"thumb", "__pycache__", ".git", "venv", "node_modules"}

PRIVATE_CSS = """
/* ── 图片区：缩略网格 + 灯箱 ── */
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
  gap:12px;margin:14px 0 2px}
.shot{margin:0}
.shot a{display:block;overflow:hidden;border:1px solid var(--line);border-radius:10px;
  background:#0b0e14}
.shot img{display:block;width:100%;height:190px;object-fit:contain;object-position:center;
  background:#0b0e14;cursor:zoom-in;transition:transform .18s ease}
.shot a:hover img{transform:scale(1.03)}
.shot figcaption{display:flex;gap:8px;justify-content:space-between;align-items:baseline;
  margin-top:6px;font-size:11.5px;color:var(--tx3)}
.shot figcaption b{font-weight:500;color:var(--tx2);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}

.excerpt{margin:12px 0 20px;padding:12px 14px;border:1px solid var(--line);
  border-radius:10px;background:var(--card)}
.excerpt .ex-h{display:flex;justify-content:space-between;gap:8px;align-items:baseline;
  font-size:12px;font-weight:600;color:var(--tx2);margin-bottom:8px}
.excerpt .ex-h span{font-weight:400;font-size:11px;color:var(--tx3)}
.excerpt ul{margin:0;padding-left:16px}
.excerpt li{font-size:12px;line-height:1.65;color:var(--tx2)}

/* ── 任务整页入口 ── */
.pg-link{margin:12px 0 2px}
.pg-link a{display:flex;align-items:center;gap:10px;padding:10px 14px;
  border:1px solid var(--line);border-radius:10px;background:var(--card);
  text-decoration:none;transition:border-color .16s ease,transform .16s ease}
.pg-link a:hover{border-color:var(--blue);transform:translateY(-1px)}
.pg-link .pg-ico{font-size:17px;line-height:1}
.pg-link .pg-tx{display:flex;flex-direction:column;gap:2px}
.pg-link .pg-tx b{font-size:13px;color:var(--blue);font-weight:600}
.pg-link .pg-tx i{font-style:normal;font-size:11px;color:var(--tx3)}

.lb{position:fixed;inset:0;z-index:99;background:rgba(4,7,11,.95);
  display:flex;flex-direction:column}
.lb[hidden]{display:none}
.lb-bar{display:flex;align-items:center;gap:8px;padding:10px 14px;
  border-bottom:1px solid var(--line);font-size:12px;color:var(--tx3);flex-wrap:wrap}
.lb-bar b{color:var(--tx);font-weight:600;font-size:12.5px}
.lb-bar .sp{margin-left:auto}
.lb-bar button,.lb-bar a{padding:4px 11px;border:1px solid var(--line);border-radius:8px;
  background:var(--card);color:var(--tx2);font-size:12px;cursor:pointer;text-decoration:none}
.lb-bar button:hover,.lb-bar a:hover{border-color:var(--blue);color:var(--blue)}
.lb-body{flex:1;overflow:hidden;position:relative;cursor:grab;touch-action:none}
.lb-body.drag{cursor:grabbing}
.lb-body img{position:absolute;left:0;top:0;transform-origin:0 0;user-select:none;
  -webkit-user-drag:none;max-width:none}
.lb-tip{position:absolute;left:14px;bottom:12px;font-size:11.5px;color:var(--tx3);
  background:rgba(10,14,20,.7);padding:5px 10px;border-radius:8px;pointer-events:none}
"""

PRIVATE_JS = """
(function(){
  var lb=document.getElementById('lb'), body=lb.querySelector('.lb-body'),
      img=document.getElementById('lbImg'), nm=document.getElementById('lbName'),
      openA=document.getElementById('lbOpen'), zoomTxt=document.getElementById('lbZoom');
  var st={scale:1,x:0,y:0,w:0,h:0,fit:1,drag:false,px:0,py:0};
  function apply(){
    img.style.transform='translate('+st.x+'px,'+st.y+'px) scale('+st.scale+')';
    if(zoomTxt){ zoomTxt.textContent=Math.round(st.scale*100)+'%'; }
  }
  function fit(){
    var bw=body.clientWidth, bh=body.clientHeight;
    st.w=img.naturalWidth||bw; st.h=img.naturalHeight||bh;
    if(!bw||!bh){ return; }
    st.fit=Math.min(bw/st.w, bh/st.h);
    st.scale=st.fit;
    st.x=(bw-st.w*st.scale)/2; st.y=(bh-st.h*st.scale)/2;
    apply();
  }
  function zoom(f, cx, cy){
    var r=body.getBoundingClientRect();
    if(cx==null){ cx=r.width/2; cy=r.height/2; }
    var ns=Math.min(8, Math.max(st.fit*0.4, st.scale*f));
    var k=ns/st.scale;
    st.x=cx-(cx-st.x)*k; st.y=cy-(cy-st.y)*k; st.scale=ns; apply();
  }
  function open(src, t){
    img.src=src; nm.textContent=t||src.split('/').pop(); openA.href=src;
    lb.hidden=false; document.body.style.overflow='hidden';
    if(img.complete){ fit(); } else { img.onload=fit; }
  }
  function close(){ lb.hidden=true; img.removeAttribute('src'); document.body.style.overflow=''; }
  document.addEventListener('click', function(e){
    var a=e.target.closest? e.target.closest('[data-lb]') : null;
    if(a){ e.preventDefault(); open(a.getAttribute('data-lb'), a.getAttribute('data-name')); }
  });
  document.getElementById('lbClose').onclick=close;
  document.getElementById('lbFit').onclick=fit;
  document.getElementById('lbOne').onclick=function(){ zoom(1/st.scale); };
  document.getElementById('lbIn').onclick=function(){ zoom(1.25); };
  document.getElementById('lbOut').onclick=function(){ zoom(1/1.25); };
  lb.addEventListener('click', function(e){ if(e.target===lb||e.target===body) close(); });
  document.addEventListener('keydown', function(e){ if(!lb.hidden && e.key==='Escape'){ close(); } });
  body.addEventListener('wheel', function(e){
    e.preventDefault();
    var r=body.getBoundingClientRect();
    zoom(e.deltaY<0?1.15:1/1.15, e.clientX-r.left, e.clientY-r.top);
  }, {passive:false});
  body.addEventListener('mousedown', function(e){
    st.drag=true; st.px=e.clientX; st.py=e.clientY; body.classList.add('drag');
  });
  window.addEventListener('mousemove', function(e){
    if(!st.drag){ return; }
    st.x+=e.clientX-st.px; st.y+=e.clientY-st.py; st.px=e.clientX; st.py=e.clientY; apply();
  });
  window.addEventListener('mouseup', function(){ st.drag=false; body.classList.remove('drag'); });
  img.addEventListener('dblclick', function(){
    if(st.scale>st.fit*1.01){ st.scale=st.fit; st.x=(body.clientWidth-st.w*st.scale)/2;
      st.y=(body.clientHeight-st.h*st.scale)/2; apply(); }
    else { zoom(1/st.scale); }
  });
  window.addEventListener('resize', function(){ if(!lb.hidden){ fit(); } });
})();
"""


def _workers() -> List[Dict]:
    """私有任务清单，公有任务一律不出。

    直接复用 case_registry.as_workers（include_unsorted=True），不另起一套
    扫描 / visibility / 板块 / metrics 口径 —— 那套逻辑只有一个源，改别名、
    改嵌套扫描都在 case_registry 里做，调度器、模块管理、私有页同步生效。
    私有仓里没写 visibility 的老任务按私有处理，没写板块的归「未分板块」。
    """
    lib = os.path.join(HERE, "case", "_lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import case_registry
    ws = case_registry.as_workers(os.path.join(HERE, "case"),
                                  visibility="private", include_unsorted=True)
    ws.sort(key=lambda w: (SECTION_ORDER.index(w["sec"])
                           if w["sec"] in SECTION_ORDER else len(SECTION_ORDER),
                           w["key"]))
    return ws


def _latest_for(ws: List[Dict]) -> Dict:
    """按 (metric_key, sec) 批量取最新值；库里没有就不显示，不补零、不拿旧值顶替。"""
    need: Dict[str, List[str]] = {}
    for w in ws:
        for it in (w.get("metrics") or []):
            if isinstance(it, str):
                it = {"key": it}
            k = it.get("key")
            s = it.get("sec") or w.get("sec") or ""
            if k and s:
                need.setdefault(s, []).append(k)
    latest: Dict = {}
    for s, ks in need.items():
        try:
            latest.update(B.read_latest(ks, [s]))
        except Exception:                                     # noqa: BLE001
            pass
    return latest


def _series_for(ws: List[Dict]) -> Dict:
    """按 (metric_key, sec) 批量取历史序列，供页面画趋势图。

    2026-11-26 修复（大佬：「私有页趋势图怎么都画不出来」）：
    原来这里直接给 ``render_registered_model(w, latest, {})`` 传空字典，
    图表 div 生成了但 series 永远为空 → 一条线都画不出来。公开页（build.py）
    一直传的是真序列。现在照 ``_latest_for`` 的口径把序列也取齐。
    """
    need: Dict[str, List[str]] = {}
    for w in ws:
        for it in (w.get("metrics") or []):
            if isinstance(it, str):
                it = {"key": it}
            k = it.get("key")
            s = it.get("sec") or w.get("sec") or ""
            if k and s:
                need.setdefault(s, []).append(k)
    series: Dict = {}
    for s, ks in need.items():
        for k in ks:
            if (k, s) in series:
                continue
            try:
                series[(k, s)] = B.read_series(k, s)
            except Exception:                                 # noqa: BLE001
                series[(k, s)] = []
    return series


def _task_root(w: Dict) -> str:
    """任务**真实产出目录**：task.json 的 `root`（`{WS}` 展开）优先，退回 case 目录。

    私有仓把 `case/*/out/`、`case/*/data/` 都写进 .gitignore 了 → 仓库里的 case 目录永远没有产物，
    真正的 out/、报告、配图都在任务运行目录（迁移过来的 `ses_*` 或原工程目录）。
    只按 case 目录扫，页面上就只剩「暂无指标」和空图区。
    """
    d = w.get("dir") or ""
    if not d or not os.path.isdir(d):
        return d
    try:
        meta = json.load(open(os.path.join(d, "task.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return d
    raw = str(meta.get("root") or "")
    if not raw:
        return d
    ws = os.environ.get("CASE_WS")
    if not ws:                                                   # <WS>/projects/<repo>/case/<key>
        repo = os.path.dirname(os.path.dirname(os.path.abspath(d)))
        parent = os.path.dirname(repo)
        ws = os.path.dirname(parent) if os.path.basename(parent) == "projects" else parent
    cand = raw.replace("{WS}", ws)
    return cand if os.path.isdir(cand) else d


def _find_images(task_dir: str, limit: int) -> List[Tuple[float, int, str]]:
    """按修改时间倒序取最近的图。

    扫两处：① 约定的产物子目录（IMG_DIRS，递归）；② **任务根目录下的图**（不递归）——
    不少任务（structural_factor 的太极图、mad_weekly/tda_spd/sector_rotation 的配图）
    把图直接写在任务目录，只扫 out/ 这类子目录会一张都看不到。
    不进 __pycache__/thumb。
    """
    if not task_dir or not os.path.isdir(task_dir):
        return []
    found: List[Tuple[float, int, str]] = []
    try:
        for fn in os.listdir(task_dir):                      # ② 根目录这一层
            fp = os.path.join(task_dir, fn)
            if os.path.isfile(fp) and fn.lower().endswith(IMG_EXT):
                stt = os.stat(fp)
                found.append((stt.st_mtime, stt.st_size, fp))
    except OSError:
        pass
    for sub in IMG_DIRS:
        root = os.path.join(task_dir, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if not fn.lower().endswith(IMG_EXT):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    stt = os.stat(fp)
                except OSError:
                    continue
                found.append((stt.st_mtime, stt.st_size, fp))
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    seen, out = set(), []
    for mt, sz, fp in found:
        sig = (os.path.basename(fp), sz)
        if sig in seen:
            continue
        seen.add(sig)
        out.append((mt, sz, fp))
        if len(out) >= limit:
            break
    return out


def _mirror(key: str, src: str, assets_root: str, page_dir: str) -> Tuple[str, str, str, int]:
    """原图复制到 assets/private/<key>/ + 生成缩略图。

    返回 (原图相对页面路径, 缩略图相对路径, "宽×高", 原图字节数)。失败时尽量降级：
    没有 Pillow 就直接用原图当缩略图。
    """
    dstdir = os.path.join(assets_root, key)
    thdir = os.path.join(dstdir, "thumb")
    os.makedirs(thdir, exist_ok=True)
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    name, i = base, 2
    while os.path.exists(os.path.join(dstdir, name)):
        cur = os.path.join(dstdir, name)
        if os.path.getsize(cur) == os.path.getsize(src) and os.path.abspath(cur) != os.path.abspath(src):
            break                                          # 同名同大小：当作同一张图，复用
        name = "%s-%d%s" % (stem, i, ext)
        i += 1
    dst = os.path.join(dstdir, name)
    try:
        if (not os.path.exists(dst)) or os.path.getmtime(dst) < os.path.getmtime(src):
            shutil.copy2(src, dst)
    except OSError:
        dst = src
    dim, thumb = "", dst
    try:
        from PIL import Image
        im = Image.open(dst)
        w, h = im.width, im.height
        dim = "%d×%d" % (w, h)
        th = os.path.join(thdir, stem + ".jpg")
        redo = (not os.path.exists(th)) or os.path.getmtime(th) < os.path.getmtime(dst)
        if not redo:
            try:
                with Image.open(th) as old:      # 老缩略图尺寸过大（历史遗留）→ 重建
                    redo = max(old.size) > 1000
            except Exception:                    # noqa: BLE001
                redo = True
        if redo:
            t = im.convert("RGB")
            t.thumbnail((900, 5400), Image.LANCZOS)   # 在转换后的对象上缩，别丢返回值
            t.save(th, "JPEG", quality=82, optimize=True)
        thumb = th
    except Exception:                                      # noqa: BLE001  Pillow 缺失或坏图都不挡页面
        dim = ""
    rel_full = os.path.relpath(dst, page_dir).replace(os.sep, "/")
    rel_thumb = os.path.relpath(thumb, page_dir).replace(os.sep, "/")
    try:
        size = os.path.getsize(dst)
    except OSError:
        size = 0
    return rel_full, rel_thumb, dim, size


PAGE_MIN_BYTES = 2000          # 太小的 html 多半是空壳/占位，不挂链接
#: 任务自产的**自包含页面**候选名：优先 `<key>.html`，其次通用名
PAGE_NAMES = ("report.html", "index.html", "page.html", "dashboard.html")
CARD_NAMES = ("card.json", "feishu_card.json", "card_payload.json")
REPORT_NAMES = ("report.md", "latest.md", "summary.md")


def _card_html(w: Dict) -> str:
    """任务发飞书的那张交互式卡片 → 页面 DOM。

    优先 `<root>/out/card.json`（任务侧 dump 的飞书卡片原样），其次 `out/card.md`
    （卡片转的纯 Markdown 兜底）。同一份卡片，飞书看到的和页面上看到的一致，
    展示层不用再去猜 report.md 的版式。
    """
    root = _task_root(w)
    if not root:
        return ""
    for sub_dir in ("out", ""):
        base = os.path.join(root, sub_dir) if sub_dir else root
        if not os.path.isdir(base):
            continue
        for name in CARD_NAMES:
            fp = os.path.join(base, name)
            if not os.path.isfile(fp):
                continue
            try:
                card = json.load(open(fp, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            html_card = FCD.card_to_html(card)
            if html_card:
                return html_card
        fp = os.path.join(base, "card.md")
        if os.path.isfile(fp):
            try:
                md = open(fp, encoding="utf-8").read().strip()
            except OSError:
                md = ""
            if md:
                return '<div class="fc-card"><div class="fc-div">%s</div></div>' % FCD.md_to_html(md)
    return 


SKIP_MD = ("readme.md", "changelog.md", "contract.md", "deploy.md", "install.md",
           "skill.md", "agents.md")
REPORT_HINTS = ("report", "summary", "daily", "weekly", "日报", "周报", "晚报", "复盘")


def _looks_like_report(low_name: str) -> bool:
    """根目录的 md 只认约定名（out/ 下的 md 不设限，那里本来就是产物目录）。

    用「名字命中」而不是「含 日报/周报」，否则 `RMT日报DataHub路径加固.md` 这类诊断记录
    也会被当成报告摘上去（实测踩过）。
    """
    return low_name in REPORT_NAMES or low_name.endswith("_report.md") \
        or low_name.endswith("_daily.md") or low_name.endswith("_weekly.md")


def _report_md(w: Dict) -> str:
    """找任务最近的一份 **报告** Markdown：优先 `out/`，其次任务根目录的约定名。

    只排除 README / CHANGELOG / CONTRACT 这类工程文档，其余 md（含 `*_report.md`）都当报告。
    """
    root = _task_root(w)
    if not root or not os.path.isdir(root):
        return ""
    cands: List[Tuple[float, str]] = []
    for base, allowed in ((os.path.join(root, "out"), None), (root, "report")):
        if not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for fn in names:
            low = fn.lower()
            if not low.endswith(".md") or low in SKIP_MD:
                continue
            if allowed == "report" and not _looks_like_report(low):
                continue
            fp = os.path.join(base, fn)
            if not os.path.isfile(fp):
                continue
            try:
                cands.append((os.path.getmtime(fp), fp))
            except OSError:
                continue
    if not cands:
        return ""
    # 约定名的报告优先，其次按修改时间
    cands.sort(key=lambda x: (os.path.basename(x[1]) in REPORT_NAMES, x[0]), reverse=True)
    return cands[0][1]


def _excerpt_items(md_path: str, limit: int = 8) -> List[str]:
    """从 Markdown 报告里挑「带数字的关键行」：只摘原文，不改写、不补数。

    跳过标题/引用/表格分隔/代码块/图片行；表格行取前几列；每条裁到 110 字，去重后最多 limit 条。
    """
    try:
        text = open(md_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    items, seen, in_code = [], set(), False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        if line.startswith(("#", ">", "![")) or set(line) <= set("-|: "):
            continue
        if line.startswith("|"):                            # 表格行：取前几列
            line = " · ".join(c.strip() for c in line.strip("|").split("|")[:4] if c.strip())
        line = line.lstrip("-*+ ").replace("**", "").replace("`", "")
        if not re.search(r"\d", line):                      # 只认带数字的行（关键数据）
            continue
        if len(line) > 110:
            line = line[:110].rstrip() + "…"
        if line in seen:
            continue
        seen.add(line)
        items.append(line)
        if len(items) >= limit:
            break
    return [i for i in items if len(i) >= 4]


def _excerpt_html(w: Dict, limit: int = 8) -> str:
    md = _report_md(w)
    if not md:
        return ""
    items = _excerpt_items(md, limit)
    if not items:
        return ""
    lis = "".join("<li>%s</li>" % html.escape(x) for x in items)
    return ('<div class="excerpt"><div class="ex-h">%s<span>%s</span></div>'
            '<ul>%s</ul></div>'
            % (html.escape("报告关键数据"), html.escape(os.path.basename(md)), lis))


def _page_html(w: Dict, assets_root: str, page_dir: str) -> str:
    """任务自己产出的**自包含 HTML 页面** → 「打开完整页面」链接。

    私有页原来只展示图（`_shots_html`）+ 报告摘要，看不到任务自己的整页
    （例如 `market_tech` 把四张图 base64 内嵌成一张长页）。这里补一个入口：

    * 只在 `out/` 里找（产物目录，不扫任务根目录，避免把 README 当页面）；
    * 候选：`out/<key>.html` → `out/report.html` → 其它 `out/*.html`（取最大最新一张）；
    * 单文件太小（< PAGE_MIN_BYTES）视为空壳，不挂；
    * 命中就抄到 `assets/private/<key>/`（照 `_mirror` 的做法），链接指向抄过去那份，
      否则相对路径在 `assets/` 下会失效。
    """
    root = _task_root(w)
    if not root:
        return ""
    out_dir = os.path.join(root, "out")
    if not os.path.isdir(out_dir):
        return ""
    key = w["key"]
    cands: List[str] = []
    for name in ["%s.html" % key] + list(PAGE_NAMES):
        fp = os.path.join(out_dir, name)
        if os.path.isfile(fp):
            cands.append(fp)
    if not cands:                                    # 兜底：out/ 下别的 html
        try:
            for fn in os.listdir(out_dir):
                if fn.lower().endswith((".html", ".htm")):
                    fp = os.path.join(out_dir, fn)
                    if os.path.isfile(fp):
                        cands.append(fp)
        except OSError:
            pass
    picked, picked_sz = "", 0
    for fp in cands:
        try:
            sz = os.path.getsize(fp)
        except OSError:
            continue
        if sz < PAGE_MIN_BYTES:                       # 空壳/占位，不挂
            continue
        if sz > picked_sz:                            # 取最大的那张（整页通常最大）
            picked, picked_sz = fp, sz
    if not picked:
        return ""
    dstdir = os.path.join(assets_root, key)
    os.makedirs(dstdir, exist_ok=True)
    dst = os.path.join(dstdir, os.path.basename(picked))
    try:
        if (not os.path.exists(dst)) or os.path.getmtime(dst) < os.path.getmtime(picked):
            shutil.copy2(picked, dst)
    except OSError:
        dst = picked
    rel = os.path.relpath(dst, page_dir).replace(os.sep, "/")
    title = (w.get("title") or key)
    return ('<div class="pg-link"><a href="%s" target="_blank" rel="noopener">'
            '<span class="pg-ico">📄</span>'
            '<span class="pg-tx"><b>打开完整页面 →</b>'
            '<i>%s · %s · %.0f KB</i></span></a></div>'
            % (html.escape(rel), html.escape(title),
               html.escape(os.path.basename(dst)), picked_sz / 1024.0))


def _shots_html(w: Dict, assets_root: str, page_dir: str, limit: int) -> str:
    imgs = _find_images(_task_root(w), limit)
    if not imgs:
        return ""
    cells = []
    for _mt, _sz, src in imgs:
        full, thumb, dim, size = _mirror(w["key"], src, assets_root, page_dir)
        cap = os.path.basename(src)
        info = " · ".join(x for x in (dim, ("%d KB" % (size / 1024)) if size else "") if x)
        cells.append(
            '<figure class="shot"><a href="%s" data-lb="%s" data-name="%s" '
            'title="点开看原图">'
            '<img loading="lazy" src="%s" alt="%s"></a>'
            '<figcaption><b>%s</b><span>%s</span></figcaption></figure>'
            % (html.escape(full), html.escape(full), html.escape(cap),
               html.escape(thumb), html.escape(cap),
               html.escape(cap), html.escape(info)))
    return '<div class="shots">%s</div>' % "".join(cells)


def _lightbox_html() -> str:
    return """
<div id="lb" class="lb" hidden>
  <div class="lb-bar">
    <b id="lbName">—</b>
    <span id="lbZoom">100%</span>
    <span class="sp"></span>
    <button id="lbOut" title="缩小">−</button>
    <button id="lbIn" title="放大">＋</button>
    <button id="lbOne" title="原始像素 1:1">1:1</button>
    <button id="lbFit" title="适应窗口">适应</button>
    <a id="lbOpen" href="#" target="_blank" rel="noopener">新标签打开原图</a>
    <button id="lbClose" title="关闭（Esc）">✕</button>
  </div>
  <div class="lb-body"><img id="lbImg" alt=""></div>
  <div class="lb-tip">滚轮缩放 · 按住拖动 · 双击切换 1:1 · Esc 关闭</div>
</div>
"""


def build_private(out_path: str, with_images: bool = True,
                  max_images: int = 6,
                  assets: Optional[str] = None,
                  with_report: bool = True,
                  with_card: bool = True) -> str:
    ws = _workers()
    if not ws:
        print("[build_private] 没有私有任务：确认 CASE_EXTRA_DIRS 指到私有仓的 case/ 这一层",
              file=sys.stderr)
    latest = _latest_for(ws)
    series = _series_for(ws)

    page_dir = os.path.dirname(os.path.abspath(out_path))
    assets_root = assets or os.path.join(page_dir, "assets", "private")

    by_sec: Dict[str, List[Dict]] = {}
    for w in ws:
        by_sec.setdefault(w.get("tab") or w.get("sec") or "modules", []).append(w)

    cfg = B.load_sections(os.path.join(HERE, "sections.json"))
    meta = {x["key"]: x for x in (cfg.get("sections") or [])}
    meta.update(EXTRA_SECTIONS)          # 未分板块等只在本页出现的板块

    nav, blocks, n_img, n_rep, n_card, n_page = [], [], 0, 0, 0, 0
    n_chart, scripts_js = 0, []
    for sec in SECTION_ORDER:
        items = by_sec.get(sec) or []
        if not items:
            continue
        m = meta.get(sec) or {}
        nav.append('<a href="#p_%s">%s</a>' % (sec, html.escape(m.get("title") or sec)))
        cards = []
        for w in items:
            # 任务自己的整页入口放最前（有就给，没有不占位；不受 --no-images 影响）
            pg = _page_html(w, assets_root, page_dir)
            if pg:
                n_page += 1
                cards.append(pg)
            # 大佬要求配图在前：先出图、再出指标卡（图在上、卡片在下）
            if with_images:
                shots = _shots_html(w, assets_root, page_dir, max_images)
                if shots:
                    n_img += shots.count('class="shot"')
                    cards.append(shots)
            _blk, _charts = B.render_registered_model(w, latest, series)
            cards.append(_blk)
            for _cid, _key, _nm, _color, _unit, _dec in (_charts or []):
                _js = B.chart_payload(_cid, series.get(_key) or [], _nm,
                                      _color, unit=_unit, dec=_dec)
                if _js:
                    n_chart += 1
                    scripts_js.append(_js)
            chtml = _card_html(w) if with_card else ""
            if chtml:
                n_card += 1
                cards.append(chtml)
            if with_report:
                ex = _excerpt_html(w)
                if ex:
                    n_rep += 1
                    cards.append(ex)
        blocks.append(
            '<div class="sec-wrap" data-sec="p_%s" id="p_%s">'
            '<div class="sec-head"><span class="sec-num">%s</span>'
            '<div><h2>%s</h2><p>%s</p></div>'
            '<span class="sec-obs">%d 个私有任务</span></div>'
            '<div class="sec-grid">%s</div></div>'
            % (sec, sec, html.escape(str(m.get("num") or "")),
               html.escape(m.get("title") or sec), html.escape(m.get("sub") or ""),
               len(items), "".join(cards)))

    n_with = sum(1 for w in ws if w.get("metrics"))
    dates = [r.get("obs_date") for r in latest.values() if r.get("obs_date")]
    asof = max(dates) if dates else "—"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    head = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Private Tasks · 私有任务展示区</title>\n<style>'
        + B.CSS + PRIVATE_CSS + FCD.CSS +
        '</style>\n</head>\n<body>\n<div class="wrap">\n\n'
        '<header class="top">\n'
        '  <h1>Private Tasks<small>私有任务展示区 · precious-task（与公开看板分开两套页面）</small></h1>\n'
        '  <div class="top-meta">\n'
        '    <div><b>' + html.escape(str(asof)) + '</b><span>最新数据日</span></div>\n'
        '    <div><b>' + str(len(ws)) + '</b><span>私有任务</span></div>\n'
        '    <div><b>' + str(n_with) + '</b><span>带指标</span></div>\n'
        '    <div><b>' + str(n_img) + '</b><span>产出图</span></div>\n'
        '    <div><b>' + str(n_chart) + '</b><span>趋势图</span></div>\n'
        '    <div><b>' + str(n_card) + '</b><span>卡片</span></div>\n'
        '    <div><b>' + str(n_rep) + '</b><span>报告摘录</span></div>\n'
        '    <div><b>' + str(n_page) + '</b><span>完整页面</span></div>\n'
        '    <div><b><a href="index.html">公开看板 →</a></b><span>另一套页面</span></div>\n'
        '  </div>\n</header>\n\n'
        '<nav class="jump">' +
        ("".join(nav) or '<span class="sm">（没有可显示的板块）</span>') +
        '</nav>\n\n'
        + ("".join(blocks) or
           '<div class="sec-wrap"><div class="sec-head"><span class="sec-num">—</span>'
           '<div><h2>暂无私有任务</h2><p>确认 CASE_EXTRA_DIRS 是否挂上私有仓</p></div></div></div>') +
        '\n\n<footer>\n'
        '  <p>只列 <code>visibility=private</code> 的任务（含旧别名 <code>internal</code>）。'
        '公有任务在另一套页面 <a href="index.html">index.html</a>，两页互不混排。</p>\n'
        '  <p>指标来自 <code>data_hub.tech</code>，库里没有的不显示；图是任务自己的产出，'
        '原图拷到 <code>assets/private/&lt;任务&gt;/</code>，点缩略图可放大看原图。'
        '构建时间 ' + now + '。</p>\n'
        '</footer>\n\n</div>\n'
        + (_lightbox_html() + "\n<script>" + PRIVATE_JS + "</script>\n" if with_images else "") +
        # 趋势图：公开页的 drawChart 在 B.JS_CORE 里，私有页原来没引入 →
        # 图表 div 有了也没人画。这里补上 JS_CORE 和各图表的 drawChart 调用。
        (("\n<script>" + B.JS_CORE + "</script>\n" +
          "\n".join(scripts_js)) if scripts_js else "") +
        '</body>\n</html>\n'
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(head)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "private.html"))
    ap.add_argument("--assets", default=None,
                    help="图片落地目录，默认 <页面目录>/assets/private")
    ap.add_argument("--max-images", type=int, default=6, help="每个任务最多展示几张（默认 6）")
    ap.add_argument("--no-images", action="store_true", help="不扫图，只出指标")
    a = ap.parse_args()
    p = build_private(a.out, with_images=not a.no_images,
                      max_images=max(1, a.max_images), assets=a.assets)
    print("已生成 %s  %.1f KB" % (p, os.path.getsize(p) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())