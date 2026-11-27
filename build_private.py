#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_private.py — 私有任务展示页（precious-task）

公开页（`build.py` → `index.html`）与私有页（本脚本 → `private.html`）是**两套页面**：
各有各的入口，互不混排。样式、卡片渲染、取数口径全部复用 `build.py`，不复制框架、
不另算一份数据。

除了指标，私有页还会把任务产出的**图**展示出来：扫 `out/ charts/ runs/ ...` 里最近的图，
原图复制到页面同目录的 `assets/private/<key>/`、另生成缩略图，页面上先给缩略网格，
点图开灯箱看原图（可滚轮缩放、拖动、1:1；长图不会被压扁）。

图库（`#gallery`）是**跨任务**的：顶部先按今天汇总全部任务当天产出的图（大缩略图，
直接看得到内容，不显示图标位），下面按日期分组给近 30 天的时间轴，每天一个可折叠分组，
最新的过去一天默认展开。每个任务卡片里也各带一份「近 30 天产出图」（超过 6 张自动折叠）。
保留窗口由 `GALLERY_DAYS`（默认 30）/ `--gallery-days` 控制：更早的图不进页面、也不镜像。
镜像的清理由 `case/_lib/cleanup.py --rule 私有页图片镜像` 负责（scheduler 每天 00:20 跑），
只删 `assets/private/` 下的副本，不动任何任务原图；缩略图 mtime 跟随原图，所以会一起过期。

私有仓通过 `CASE_EXTRA_DIRS` 挂进来，任务在 `task.json` 里写 `visibility=private`
（历史写法 `internal` 等价）。私有页只读这些任务；公开页不认识它们。

用法：
    export CASE_EXTRA_DIRS=/srv/private-tasks/case
    export PYTHONPATH=/srv/monitor/datahub-multi-tier:$PWD
    python3 build_private.py                        # → private.html + assets/private/
    python3 build_private.py --out /srv/monitor/private.html
    python3 build_private.py --no-images            # 只要指标，不扫图
    python3 build_private.py --max-images 12        # 每个任务卡片最多几张（默认 0=全部）
    python3 build_private.py --gallery-days 30      # 图库/镜像保留窗口（默认 30 天）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build as B                                            # noqa: E402  复用 CSS / 卡片渲染 / 取数
import feishu_card_dom as FCD                                # noqa: E402  飞书卡片 → DOM

# 各算法选股结果的统一契约（case/_shared/pick_pool.py）
_SHARED = os.path.join(HERE, "case", "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

# task.json 没写 section / section_domain 的任务：不丢，单独归到「未分板块」
UNSORTED = "unsorted"
# 2026-11-26 大佬要求：把「选股」并进「自选股」，一个 tab 里按算法分块。
# 各算法统一走 case/_shared/pick_pool.py 的 out/picks.json 契约，
# 显示层只认那一份，不再逐个适配各家的杂 json。
PICK_SEC = "watch"
SECTION_ORDER = ("stock", "watch", "futures", "realestate", "macro",
                 "liquidity", "bond", "option", "risk", "modules", UNSORTED)
# 板块别名只维护一份，在 case/_lib/case_registry.py 的 SECTION_ALIAS，
# 这里不再平行维护（credit/credit_spread→bond、macro_watch→macro 都在那边）。
# sections.json 里没有的板块（未归类）在本页的标题
EXTRA_SECTIONS = {
    # watch / pick 只在本页出现，sections.json（公开看板导航）里没有这两个 key
    "watch": {"num": "—", "title": "自选股",
              "sub": "实盘持仓 + 各算法的自选股：一个算法一块，统一口径（代码·行业·理由·分数）"},
    # 兼容老 task.json 里还写着 section_domain=pick 的任务：一律并入自选股
    "pick": {"num": "—", "title": "自选股",
             "sub": "（已并入自选股）"},
    UNSORTED: {"num": "—", "title": "未分板块",
               "sub": "私有仓里没写 section / section_domain 的任务，先按原样列出"},
}
# 任务目录下会出图的子目录：约定俗成的几个，逐层扫但跳过缩略图与缓存
IMG_DIRS = ("out", "charts", "runs", "output", "figures", "reports", "assets", "data")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")
SKIP_DIRS = {"thumb", "__pycache__", ".git", "venv", "node_modules"}
# 图库保留窗口：只展示/镜像最近这些天的图，更早的不再进页面（镜像清理由 cleanup.py 负责）
GALLERY_DAYS = 30

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

/* ── 图库：今日图片流 + 近 30 天时间轴 ── */
.gal-wrap{margin:22px 0 30px;padding:16px 18px 20px;border:1px solid var(--line);
  border-radius:14px;background:var(--card)}
.gal-head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;
  justify-content:space-between;margin-bottom:4px}
.gal-head h2{font-size:16px;margin:0}
.gal-head p{margin:2px 0 0;font-size:12px;color:var(--tx3)}
.gal-head .gal-stat{font-size:12px;color:var(--tx3)}
.gal-head .gal-stat b{color:var(--tx);font-size:15px;margin:0 3px}
.gal-today{margin:14px 0 8px;padding:12px 14px;border:1px solid var(--line);
  border-radius:12px;background:#0e131b}
.gal-today h3{margin:0 0 10px;font-size:13.5px;color:var(--tx)}
.gal-today h3 span{font-weight:400;font-size:11.5px;color:var(--tx3);margin-left:8px}
.gal-day{margin:10px 0;border:1px solid var(--line);border-radius:12px;
  background:#0e131b;overflow:hidden}
.gal-day>summary{cursor:pointer;list-style:none;padding:10px 14px;display:flex;
  gap:10px;align-items:baseline;font-size:13px;color:var(--tx2);user-select:none}
.gal-day>summary::-webkit-details-marker{display:none}
.gal-day>summary::before{content:"▸";color:var(--tx3);font-size:11px}
.gal-day[open]>summary::before{content:"▾"}
.gal-day>summary b{color:var(--tx);font-weight:600}
.gal-day>summary span{font-size:11.5px;color:var(--tx3)}
.gal-day .gal-inner{padding:0 12px 12px}
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));
  gap:12px}
.gal-grid.big{grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.gal-card{margin:0;min-width:0}
.gal-card a{display:block;border:1px solid var(--line);border-radius:10px;
  background:#0b0e14;overflow:hidden}
.gal-card img{display:block;width:100%;height:180px;object-fit:contain;
  object-position:center;background:#0b0e14;cursor:zoom-in;transition:transform .18s ease}
.gal-grid.big .gal-card img{height:250px}
.gal-card a:hover img{transform:scale(1.03)}
.gal-card figcaption{margin-top:6px;font-size:11.5px;color:var(--tx3);line-height:1.5}
.gal-card figcaption b{display:block;color:var(--tx2);font-weight:500;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gal-card figcaption em{display:block;font-style:normal;color:var(--tx3)}
.gal-empty{margin:10px 0 0;font-size:12px;color:var(--tx3)}
.task-gal{margin:14px 0 4px;border:1px solid var(--line);border-radius:10px;
  background:#0e131b;overflow:hidden}
.task-gal>summary{cursor:pointer;list-style:none;padding:8px 12px;font-size:12px;
  color:var(--tx2);user-select:none}
.task-gal>summary::-webkit-details-marker{display:none}
.task-gal>summary::before{content:"▸";color:var(--tx3);font-size:10px;margin-right:6px}
.task-gal[open]>summary::before{content:"▾"}
.task-gal>summary span{color:var(--tx3);margin-left:6px}
.task-gal .shots{padding:0 12px 12px}

/* 图库入口：头部按钮 + 跳转条高亮 */
.gal-cta{display:inline-flex;flex-direction:column;gap:2px;align-self:center;
  padding:9px 16px;border:1px solid #2a6df5;border-radius:12px;
  background:linear-gradient(135deg,#14306b,#0e1a33);color:#dbe7ff;
  text-decoration:none;white-space:nowrap}
.gal-cta span{font-size:14px;font-weight:600;letter-spacing:.3px}
.gal-cta small{font-size:11px;color:#9db7e8}
.gal-cta:hover{border-color:#4b8bff;background:linear-gradient(135deg,#1a3d85,#10203f);
  color:#fff}
nav.jump a.gal-link{font-weight:600;border-color:#2a6df5;color:#dbe7ff}

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


def _find_images(task_dir: str, limit: Optional[int] = None,
                 since: Optional[float] = None) -> List[Tuple[float, int, str]]:
    """按修改时间倒序取图。

    - ``since``：epoch 秒，早于它的图不返回（图库只看近 30 天）；
    - ``limit``：最多取几张，None/0 表示不限（图库要「全部」）。

    扫两处：① 约定的产物子目录（IMG_DIRS，递归）；② **任务根目录下的图**（不递归）——
    不少任务（structural_factor 的太极图、mad_weekly/tda_spd/sector_rotation 的配图）
    把图直接写在任务目录，只扫 out/ 这类子目录会一张都看不到。
    不进 __pycache__/thumb。
    """
    if not task_dir or not os.path.isdir(task_dir):
        return []
    found: List[Tuple[float, int, str]] = []

    def _add(fp: str) -> None:
        try:
            stt = os.stat(fp)
        except OSError:
            return
        if since is not None and stt.st_mtime < since:
            return
        found.append((stt.st_mtime, stt.st_size, fp))

    try:
        for fn in os.listdir(task_dir):                      # ② 根目录这一层
            fp = os.path.join(task_dir, fn)
            if os.path.isfile(fp) and fn.lower().endswith(IMG_EXT):
                _add(fp)
    except OSError:
        pass
    for sub in IMG_DIRS:
        root = os.path.join(task_dir, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.lower().endswith(IMG_EXT):
                    _add(os.path.join(dirpath, fn))
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    seen, out = set(), []
    for mt, sz, fp in found:
        sig = (os.path.basename(fp), sz)
        if sig in seen:
            continue
        seen.add(sig)
        out.append((mt, sz, fp))
        if limit and limit > 0 and len(out) >= limit:
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
        # 缩略图名跟「去重后的文件名」走：不同子目录里同名 stem 的图不会互相覆盖
        th = os.path.join(thdir, os.path.splitext(name)[0] + ".jpg")
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
        try:                                         # 缩略图跟随原图年龄，过期清理才一致
            mt = os.path.getmtime(dst)
            if abs(os.path.getmtime(th) - mt) > 60:  # 老缩略图/刚生成的都对齐
                os.utime(th, (mt, mt))
        except OSError:
            pass
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


def _worker_title(w: Dict) -> str:
    return str(w.get("title") or w.get("key") or "—")


def _fmt_size(n: int) -> str:
    if not n:
        return ""
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1024.0 / 1024.0)
    return "%d KB" % max(1, int(round(n / 1024.0)))


def _collect_gallery(ws: List[Dict], days: int = GALLERY_DAYS) -> List[Dict]:
    """跨任务收集近 ``days`` 天的产出图，按时间倒序。

    与单任务图区共用同一套扫描口径（`_find_images` + `_task_root`），不另写规则。
    """
    since = time.time() - max(1, days) * 86400
    items: List[Dict] = []
    for w in ws:
        title = _worker_title(w)
        for mt, sz, src in _find_images(_task_root(w), None, since):
            items.append({"key": w.get("key") or "", "title": title,
                          "mtime": mt, "size": sz, "src": src})
    items.sort(key=lambda x: (x["mtime"], x["size"]), reverse=True)
    return items


def _gallery_card(it: Dict, assets_root: str, page_dir: str, big: bool = False) -> Tuple[str, Dict]:
    """一张图卡：缩略图展示完整画面（contain），点开灯箱看原图。"""
    full, thumb, dim, size = _mirror(it["key"], it["src"], assets_root, page_dir)
    dt = datetime.fromtimestamp(it["mtime"])
    fname = os.path.basename(it["src"])
    info = " · ".join(x for x in (dim, _fmt_size(size)) if x)
    label = "%s · %s" % (it["title"], fname)
    cell = (
        '<figure class="gal-card"><a href="%s" data-lb="%s" data-name="%s" '
        'title="点开看原图">'
        '<img loading="lazy" src="%s" alt="%s"></a>'
        '<figcaption><b>%s</b>'
        '<em>%s · %s</em><em>%s</em></figcaption></figure>'
        % (html.escape(full), html.escape(full), html.escape(label),
           html.escape(thumb), html.escape(label), html.escape(it["title"]),
           html.escape(dt.strftime("%H:%M")), html.escape(fname),
           html.escape(info))
    )
    entry = {"task": it["title"], "key": it["key"], "file": fname,
             "time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
             "mtime": int(it["mtime"]), "dim": dim, "size": size,
             "full": full, "thumb": thumb}
    return cell, entry


def _gallery_html(ws: List[Dict], assets_root: str, page_dir: str,
                  days: int = GALLERY_DAYS) -> Tuple[str, List[Dict]]:
    """页面顶部的「图库」：今天的全部图 + 近 30 天按日时间轴。

    返回 (HTML, 索引条目)。索引由调用方写成 ``assets/private/gallery.json``，
    供其它页面/任务读取，不用再扫盘。
    """
    items = _collect_gallery(ws, days)
    today = datetime.now().strftime("%Y-%m-%d")
    groups: Dict[str, List[Dict]] = {}
    for it in items:
        d = datetime.fromtimestamp(it["mtime"]).strftime("%Y-%m-%d")
        groups.setdefault(d, []).append(it)

    entries: List[Dict] = []
    days_sorted = sorted(groups, reverse=True)
    today_items = groups.get(today, [])
    n_today = len(today_items)
    n_tasks_today = len({x["key"] for x in today_items})

    def grid(bucket: List[Dict], cls: str) -> str:
        cells = []
        for it in bucket:
            cell, entry = _gallery_card(it, assets_root, page_dir, big=(cls == "gal-grid big"))
            cells.append(cell)
            entries.append(entry)
        return '<div class="%s">%s</div>' % (cls, "".join(cells))

    parts = [
        '<div class="gal-head">'
        '<div><h2>图库 · 图片流</h2>'
        '<p>近 %d 天所有私有任务的产出图，按时间倒序；今日全部展开，历史按日折叠。'
        '缩略图保留完整画面，点图看原图。</p></div>'
        '<div class="gal-stat">今日 <b>%d</b> 张 / <b>%d</b> 个任务 · '
        '近 %d 天 <b>%d</b> 张</div></div>'
        % (days, n_today, n_tasks_today, days, len(items))
    ]

    if today_items:
        parts.append('<div class="gal-today"><h3>今天 · %s<span>%d 张 · %d 个任务</span></h3>%s</div>'
                     % (today, n_today, n_tasks_today, grid(today_items, "gal-grid big")))
    else:
        parts.append('<div class="gal-today"><h3>今天 · %s<span>0 张</span></h3>'
                     '<p class="gal-empty">今天还没有任务产出图片。任务跑完后重新构建本页即可看到；'
                     '历史图片在下面的时间轴里。</p></div>' % today)

    wk = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    past = [d for d in days_sorted if d != today]
    for i, d in enumerate(past):
        bucket = groups[d]
        try:
            wd = wk[datetime.strptime(d, "%Y-%m-%d").weekday()]
        except ValueError:
            wd = ""
        opened = " open" if i == 0 else ""          # 最近一天默认展开，其余折叠
        parts.append(
            '<details class="gal-day"%s><summary><b>%s</b>'
            '<span>%s · %d 张</span></summary><div class="gal-inner">%s</div></details>'
            % (opened, html.escape(d), wd, len(bucket), grid(bucket, "gal-grid")))
    return "".join(parts), entries


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


def _shots_html(w: Dict, assets_root: str, page_dir: str, limit: int = 0,
                days: int = GALLERY_DAYS) -> str:
    """任务卡里的图区：近 30 天全部产出图，按时间倒序。

    ``limit`` >0 时只取最新的 N 张（旧命令行参数仍可用）；默认 0=全部。
    张数多时折叠成一个可展开块，避免把整页撑得过长；≤6 张保持直接展示。
    """
    since = time.time() - max(1, days) * 86400
    imgs = _find_images(_task_root(w), limit or None, since)
    if not imgs:
        return ""
    cells = []
    for mt, _sz, src in imgs:
        full, thumb, dim, size = _mirror(w["key"], src, assets_root, page_dir)
        cap = os.path.basename(src)
        info = " · ".join(x for x in (dim, _fmt_size(size)) if x)
        stamp = datetime.fromtimestamp(mt).strftime("%m-%d %H:%M")
        cells.append(
            '<figure class="shot"><a href="%s" data-lb="%s" data-name="%s" '
            'title="点开看原图">'
            '<img loading="lazy" src="%s" alt="%s"></a>'
            '<figcaption><b>%s</b><span>%s%s</span></figcaption></figure>'
            % (html.escape(full), html.escape(full), html.escape(cap),
               html.escape(thumb), html.escape(cap),
               html.escape(cap), html.escape(stamp),
               (" · " + html.escape(info)) if info else ""))
    grid = '<div class="shots">%s</div>' % "".join(cells)
    if len(imgs) <= 6:
        return grid
    return ('<details class="task-gal"><summary>近 %d 天产出图<span>%d 张 · 按时间倒序</span>'
            '</summary>%s</details>' % (days, len(imgs), grid))


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


PICK_CSS = """
/* ── 自选股：一个算法一块，统一结构 ── */
.pk-algo{margin:0 0 26px}
.pk-hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
  padding:0 0 8px;border-bottom:1px solid var(--line);margin-bottom:12px}
.pk-hd .nm{font-size:14.5px;font-weight:600;color:var(--tx)}
.pk-hd .meta{font-size:11.5px;color:var(--tx3)}
.pk-hd .meta b{font-weight:500;color:var(--tx2)}
.pk-hd .note{margin-left:auto;font-size:11.5px;color:var(--tx3);max-width:52%;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pk-tb{width:100%;border-collapse:collapse;font-size:12px}
.pk-tb th{text-align:left;font-weight:500;color:var(--tx3);font-size:11px;
  padding:4px 8px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
.pk-tb td{padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.04);
  color:var(--tx2);vertical-align:top}
.pk-tb tr:last-child td{border-bottom:0}
.pk-tb .cd{font-family:var(--mono,monospace);color:var(--tx3);font-size:11.5px}
.pk-tb .nmc{color:var(--tx);font-weight:500}
.pk-tb .rk{color:var(--tx3);font-size:11px;width:28px}
.pk-tb .sc{font-family:var(--mono,monospace);color:var(--tx2);white-space:nowrap}
.pk-tb .rs{color:var(--tx3);font-size:11.5px;line-height:1.55}
.pk-tag{display:inline-block;padding:1px 6px;margin:1px 3px 1px 0;border-radius:5px;
  background:rgba(90,140,255,.13);color:#8fb2ff;font-size:10.5px;white-space:nowrap}
.pk-tag.s{background:rgba(255,90,110,.13);color:#ff8b9a}
.pk-tag.w{background:rgba(255,190,80,.13);color:#ffc866}
.pk-empty{font-size:12px;color:var(--tx3);padding:6px 0 2px}
.pk-miss{font-size:11.5px;color:var(--tx3);padding:4px 0 0}
.pk-sum{font-size:12px;color:var(--tx2);line-height:1.7;margin:2px 0 8px}
.pk-link{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.pk-go{display:inline-block;padding:5px 13px;border:1px solid var(--blue);
  border-radius:8px;color:var(--blue);font-size:12px;text-decoration:none;
  transition:background .16s ease}
.pk-go:hover{background:rgba(74,158,255,.12)}
.pk-link .sm{font-size:11px;color:var(--tx3)}
.pk-chip{display:inline-block;padding:2px 9px;margin:2px 4px 2px 0;border-radius:6px;
  background:rgba(255,255,255,.05);color:var(--tx2);font-size:11px;text-decoration:none}
.pk-chip:hover{background:rgba(74,158,255,.16);color:var(--blue)}
/* ── 子页面 ── */
.pk-crumb{margin:0 0 14px;font-size:12px}
.pk-crumb a{color:var(--tx3);text-decoration:none}
.pk-crumb a:hover{color:var(--blue)}
.pk-h1{font-size:20px;font-weight:600;color:var(--tx);margin:0 0 6px}
.pk-h1 .cd{font-family:var(--mono,monospace);font-size:13px;color:var(--tx3);font-weight:400}
.pk-sub{font-size:12px;color:var(--tx3);margin:0 0 14px}
.pk-why{font-size:12.5px;color:var(--tx2);line-height:1.7;padding:10px 14px;
  border-left:2px solid var(--blue);background:rgba(74,158,255,.06);
  border-radius:0 8px 8px 0;margin:0 0 16px}
.pk-sect{font-size:13px;font-weight:600;color:var(--tx2);margin:22px 0 10px}
.pk-kv{width:100%;border-collapse:collapse;font-size:12.5px}
.pk-kv th{text-align:left;font-weight:400;color:var(--tx3);width:110px;
  padding:5px 10px 5px 0;border-bottom:1px solid rgba(255,255,255,.05);vertical-align:top}
.pk-kv td{color:var(--tx);padding:5px 0;border-bottom:1px solid rgba(255,255,255,.05)}
"""


def _dir_cls(d: str) -> str:
    return {"short": "s", "watch": "w"}.get(d or "long", "")


def _pick_table(picks: List[Dict], limit: int = 30,
                links: Optional[Dict[int, str]] = None) -> str:
    """统一的 picks 表：名次 · 名称 · 代码 · 行业 · 分数 · 理由/标签。

    links: {行号(1-based): 子页面相对路径}，给了就把名称做成超链（点进该股子页面）。
    """
    rows = []
    for i, p in enumerate(picks[:limit], 1):
        name = p.get("name") or p.get("code") or "—"
        code = p.get("code") or ""
        href = (links or {}).get(i)
        name_html = ('<a href="%s" target="_blank" rel="noopener">%s</a>'
                     % (html.escape(href), html.escape(name))) if href else html.escape(name)
        if p.get("sector_l2") and p.get("sector_l3"):
            ind = "%s › %s" % (p["sector_l2"], p["sector_l3"])
        else:
            ind = p.get("sector_l3") or p.get("sector_l2") or p.get("industry") or ""
        sc = p.get("score")
        sc_txt = ("%.2f" % sc).rstrip("0").rstrip(".") if isinstance(sc, (int, float)) else ""
        tags = "".join('<span class="pk-tag %s">%s</span>'
                       % (_dir_cls(p.get("direction")), html.escape(str(t)))
                       for t in (p.get("tags") or [])[:4])
        why = html.escape(p.get("reason") or "")
        rcell = (why + ("<br>" if why and tags else "") + tags) if (why or tags) else ""
        rows.append(
            '<tr><td class="rk">%d</td><td class="nmc">%s</td><td class="cd">%s</td>'
            '<td>%s</td><td class="sc">%s</td><td class="rs">%s</td></tr>'
            % (i, name_html, html.escape(code), html.escape(ind), sc_txt, rcell))
    return ('<table class="pk-tb"><thead><tr><th></th><th>名称</th><th>代码</th>'
            '<th>行业</th><th>分数</th><th>理由 / 标签</th></tr></thead>'
            '<tbody>%s</tbody></table>' % "".join(rows))


PICK_PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head><body><div class="wrap">
<div class="pk-crumb"><a href="__HOME__">← 返回自选股</a></div>
__BODY__
</div></body></html>
"""


def _pick_pages_dir(page_dir: str) -> str:
    """子页面落地目录：<页面目录>/pick-pages/。"""
    d = os.path.join(page_dir, "pick-pages")
    os.makedirs(d, exist_ok=True)
    return d


def _pick_page_css() -> str:
    """子页面用同一套样式（公开页 CSS + 自选股 CSS），保证和主页面一致。"""
    try:
        import feishu_card_dom as FCD
        return B.CSS + PICK_CSS + FCD.CSS
    except Exception:                                   # noqa: BLE001
        return B.CSS + PICK_CSS


def _write_page(path: str, title: str, body: str, home_rel: str) -> str:
    """写一个子页面，返回相对主页面的路径。失败返回空串（不挡主页面）。"""
    try:
        html_doc = (PICK_PAGE_TMPL
                    .replace("__TITLE__", html.escape(title))
                    .replace("__CSS__", _pick_page_css())
                    .replace("__HOME__", html.escape(home_rel))
                    .replace("__BODY__", body))
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        return os.path.basename(path)
    except Exception as e:                              # noqa: BLE001
        print("[pick-page] 写 %s 失败：%s" % (path, str(e)[:120]), file=sys.stderr)
        return ""


def _card_section(root: Optional[str], key: str = "") -> str:
    """任务 dump 的飞书交互卡片 → HTML（飞书看到什么，子页面就看到什么）。

    查找顺序：
      1. `<root>/out/card.json`（框架版任务：dump_card 写的，契约位置）
      2. `<root>/card.json`
      3. `<WS>/.cache/picks/cards/<key>.json`（宿主机脚本还没迁 case 时的落点）
    """
    cands: List[str] = []
    if root:
        for sub in ("out", ""):
            base = os.path.join(root, sub) if sub else root
            if not os.path.isdir(base):
                continue
            cands.extend(os.path.join(base, n) for n in CARD_NAMES)
    if key:
        try:
            import pick_pool
            cands.append(os.path.join(pick_pool.fallback_dir(), "cards", "%s.json" % key))
        except Exception:                                  # noqa: BLE001
            pass
    for fp in cands:
        if not os.path.isfile(fp):
            continue
        try:
            card = json.load(open(fp, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        body = FCD.card_to_html(card)
        if body:
            return ('<h3 class="pk-sect">飞书交互卡片（同一份内容）</h3>' + body)
    return ""


def _report_section(root: Optional[str]) -> str:
    """任务报告 md → 子页面（有就带，没有不占位）。"""
    md = _report_md({"key": "", "dir": root}) if root else ""
    if not md:
        return ""
    return ('<h3 class="pk-sect">当日报告</h3>'
            '<div class="fc-card"><div class="fc-div">%s</div></div>' % FCD.md_to_html(md))


def _stock_page_body(alg_title: str, as_of: str, p: Dict) -> str:
    """单只股票的子页面：把 picks 里这条能给的字段全摊开。"""
    name = p.get("name") or p.get("code") or "—"
    ind = (("%s › %s" % (p["sector_l2"], p["sector_l3"]))
           if p.get("sector_l2") and p.get("sector_l3")
           else (p.get("sector_l3") or p.get("sector_l2") or p.get("industry") or "—"))
    kv_rows = []
    for label, key, fmt in (
            ("代码", "code", None), ("行业", None, None), ("方向", "direction", None),
            ("分数", "score", "%.2f"), ("参考价", "price", "%.2f"),
            ("目标价", "target", "%.2f"), ("止损", "stop", "%.2f"),
            ("名次", "rank", "%.0f")):
        if key is None:
            val = ind
        else:
            v = p.get(key)
            if isinstance(v, (int, float)) and fmt:
                val = fmt % v
            else:
                val = "" if v in (None, "") else str(v)
        if val not in ("", "—") or label == "行业":
            kv_rows.append('<tr><th>%s</th><td>%s</td></tr>'
                           % (html.escape(label), html.escape(str(val))))
    tags = "".join('<span class="pk-tag %s">%s</span>'
                   % (_dir_cls(p.get("direction")), html.escape(str(t)))
                   for t in (p.get("tags") or []))
    extra = {k: v for k, v in p.items()
             if k not in ("code", "name", "industry", "sector_l1", "sector_l2", "sector_l3",
                          "reason", "direction", "tags", "score", "rank", "price",
                          "target", "stop")}
    extra_rows = "".join('<tr><th>%s</th><td>%s</td></tr>'
                         % (html.escape(str(k)), html.escape(str(v)))
                         for k, v in extra.items() if v not in (None, "", [], {}))

    return ('<h1 class="pk-h1">%s <span class="cd">%s</span></h1>'
            '<div class="pk-sub">%s · %s</div>'
            '%s'
            '<h3 class="pk-sect">指标</h3>'
            '<table class="pk-kv">%s%s</table>'
            '%s'
            % (html.escape(name), html.escape(p.get("code") or ""),
               html.escape(alg_title), html.escape(as_of or ""),
               ('<div class="pk-why">%s</div>' % html.escape(p["reason"])) if p.get("reason") else "",
               "".join(kv_rows), extra_rows,
               ('<h3 class="pk-sect">标签</h3><div>%s</div>' % tags) if tags else ""))


def _pick_block(w: Dict, root: Optional[str], page_dir: str = "") -> str:
    """一个算法一块 —— 两头都做（大佬 2026-11-26 定）：

    1. **主页面全量展示**：完整清单表，每只股票名做成超链；
    2. **子页面**：每只股票一个独立页（点名字打开），算法页还有完整清单 +
       飞书交互卡片 + 当日报告。

    原话：「你全量展示还是全量展示啊？但是用那个不更好吗？就是你两头都做」。
    """
    import pick_pool
    data = pick_pool.load_picks(root) if root else None
    if not data:
        # 脚本还没迁进 case 时，结果写在统一落盘目录 <WS>/.cache/picks/<key>.json
        data = pick_pool.load_picks_fallback(w.get("key") or "")
    title_raw = w.get("title") or w.get("key") or ""
    title = html.escape(title_raw)
    key = w.get("key") or ""
    if not data:
        return ('<div class="pk-algo"><div class="pk-hd"><span class="nm">%s</span>'
                '<span class="meta">%s</span></div>'
                '<div class="pk-miss">该算法尚未按统一契约输出 picks（显示层不猜数据）</div></div>'
                % (title, html.escape(key)))

    picks = data.get("picks") or []
    as_of = data.get("as_of") or ""
    cad = data.get("cadence") or ""
    cad_txt = {"daily": "日更", "weekly": "周更", "monthly": "月更"}.get(cad, cad)
    note = data.get("note") or ""
    meta_bits = [b for b in (html.escape(as_of), cad_txt,
                             ("<b>%d</b> 只" % len(picks)) if picks else "今日无票") if b]

    # ── 先生成子页面（拿到每只股票页的链接，主页表格要用）──
    stock_links: Dict[int, str] = {}
    algo_link = ""
    has_card = bool(_card_section(root, key))
    if not page_dir:
        page_dir = os.path.dirname(os.path.abspath("."))
    try:
        pdir = _pick_pages_dir(page_dir)
        # 每只股票一个独立子页面
        for i, p in enumerate(picks[:60], 1):
            sub_body = _stock_page_body(title_raw, as_of, p)
            sub_body += _card_section(root, key)
            nm = p.get("name") or p.get("code") or ("%d" % i)
            sfn = _write_page(
                os.path.join(pdir, "stk-%s-%02d-%s.html"
                             % (key, i, "".join(c for c in str(p.get("code") or nm)
                                                if c.isalnum())[:12])),
                "%s · %s" % (nm, title_raw), sub_body, "../private.html")
            if sfn:
                stock_links[i] = "pick-pages/%s" % sfn
        # 算法总页：完整清单（同样带股票链接）+ 飞书卡片 + 报告
        abody = ['<h1 class="pk-h1">%s</h1>' % title,
                 '<div class="pk-sub">%s%s</div>'
                 % (" · ".join([as_of, cad_txt] + (["%d 只" % len(picks)] if picks else [])),
                    ("　" + html.escape(note)) if note else "")]
        if note:
            abody.append('<div class="pk-why">%s</div>' % html.escape(note))
        abody.append('<h3 class="pk-sect">入选清单</h3>')
        abody.append(_pick_table(picks, links={k: os.path.basename(v) for k, v in stock_links.items()})
                     if picks else '<div class="pk-empty">今日无票</div>')
        abody.append(_card_section(root, key))
        abody.append(_report_section(root))
        fn = _write_page(os.path.join(pdir, "algo-%s.html" % key),
                         "%s · %s" % (title_raw, as_of), "".join(abody), "../private.html")
        if fn:
            algo_link = "pick-pages/%s" % fn
    except Exception as e:                               # noqa: BLE001
        print("[pick-page] %s 子页面生成失败：%s" % (key, str(e)[:140]), file=sys.stderr)

    # ── 主页面：全量展示 + 每只股票挂超链 + 算法子页入口 ──
    entry = ""
    if algo_link:
        entry = ('<a class="pk-go" href="%s" target="_blank" rel="noopener">'
                 '打开子页面 →</a>' % html.escape(algo_link))
    body_tbl = (_pick_table(picks, links=stock_links) if picks
                else '<div class="pk-empty">今日无票</div>')
    return ('<div class="pk-algo"><div class="pk-hd">'
            '<span class="nm">%s</span><span class="meta">%s</span>%s%s</div>'
            '%s</div>'
            % (title, " · ".join(meta_bits),
               ('<span class="note" title="%s">%s</span>'
                % (html.escape(note), html.escape(note))) if note else "",
               entry, body_tbl))


def _pick_summary(picks: List[Dict], n: int = 6) -> str:
    """一行摘要：前几只的名字 + 行业，让主页面不点开也能看个大概。"""
    if not picks:
        return ""
    bits = []
    for p in picks[:n]:
        nm = p.get("name") or p.get("code") or ""
        ind = p.get("sector_l3") or p.get("industry") or ""
        bits.append("%s%s" % (html.escape(nm), ("（%s）" % html.escape(ind)) if ind else ""))
    more = ("　等 %d 只" % len(picks)) if len(picks) > n else ""
    return "、".join(bits) + more


def build_private(out_path: str, with_images: bool = True,
                  max_images: int = 0,
                  assets: Optional[str] = None,
                  with_report: bool = True,
                  with_card: bool = True,
                  gallery_days: int = GALLERY_DAYS) -> str:
    ws = _workers()
    if not ws:
        print("[build_private] 没有私有任务：确认 CASE_EXTRA_DIRS 指到私有仓的 case/ 这一层",
              file=sys.stderr)
    latest = _latest_for(ws)
    series = _series_for(ws)

    page_dir = os.path.dirname(os.path.abspath(out_path))
    assets_root = assets or os.path.join(page_dir, "assets", "private")
    gallery_days = max(1, int(gallery_days))

    by_sec: Dict[str, List[Dict]] = {}
    for w in ws:
        sec = w.get("tab") or w.get("sec") or "modules"
        if sec == "pick":                 # 选股并入自选股（2026-11-26）
            sec = PICK_SEC
        by_sec.setdefault(sec, []).append(w)

    cfg = B.load_sections(os.path.join(HERE, "sections.json"))
    meta = {x["key"]: x for x in (cfg.get("sections") or [])}
    meta.update(EXTRA_SECTIONS)          # 未分板块等只在本页出现的板块

    gallery_html, gallery_entries = ("", [])
    if with_images:
        gallery_html, gallery_entries = _gallery_html(ws, assets_root, page_dir, gallery_days)

    nav, blocks, n_img, n_rep, n_card, n_page = [], [], len(gallery_entries), 0, 0, 0
    n_chart, scripts_js = 0, []
    if gallery_html:
        nav.append('<a class="gal-link" href="#gallery">图库 · %d 张</a>'
                   % len(gallery_entries))
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
            if sec == PICK_SEC:
                # 自选股：一个算法一块，统一表（代码·行业·理由·分数）
                cards.append(_pick_block(w, _task_root(w), page_dir))
                if with_report:
                    ex = _excerpt_html(w)
                    if ex:
                        n_rep += 1
                        cards.append(ex)
                continue
            # 大佬要求配图在前：先出图、再出指标卡（图在上、卡片在下）
            if with_images:
                shots = _shots_html(w, assets_root, page_dir, max_images, gallery_days)
                if shots:
                    if not gallery_entries:                  # 图库没启用时退回计数
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
    gal_btn = ""
    if with_images:
        _today = datetime.now().strftime("%Y-%m-%d")
        _n_today = sum(1 for e in gallery_entries
                       if str(e.get("time") or "").startswith(_today))
        gal_btn = ('  <a class="gal-cta" href="#gallery"><span>图库 · 图片流</span>'
                   '<small>今日 %d 张 / 近 %d 天 %d 张 →</small></a>\n'
                   % (_n_today, gallery_days, len(gallery_entries)))

    head = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Private Tasks · 私有任务展示区</title>\n<style>'
        + B.CSS + PRIVATE_CSS + PICK_CSS + FCD.CSS +
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
        '  </div>\n' + gal_btn + '</header>\n\n'
        '<nav class="jump">' +
        ("".join(nav) or '<span class="sm">（没有可显示的板块）</span>') +
        '</nav>\n\n'
        + (('<section class="gal-wrap" id="gallery">%s</section>\n\n' % gallery_html)
           if gallery_html else "")
        + ("".join(blocks) or
           '<div class="sec-wrap"><div class="sec-head"><span class="sec-num">—</span>'
           '<div><h2>暂无私有任务</h2><p>确认 CASE_EXTRA_DIRS 是否挂上私有仓</p></div></div></div>') +
        '\n\n<footer>\n'
        '  <p>只列 <code>visibility=private</code> 的任务（含旧别名 <code>internal</code>）。'
        '公有任务在另一套页面 <a href="index.html">index.html</a>，两页互不混排。</p>\n'
        '  <p>指标来自 <code>data_hub.tech</code>，库里没有的不显示；图是任务自己的产出，'
        '近 ' + str(gallery_days) + ' 天的图拷到 <code>assets/private/&lt;任务&gt;/</code>，'
        '点缩略图可放大看原图。图库索引 <code>assets/private/gallery.json</code>，'
        '超过 ' + str(gallery_days) + ' 天的镜像由半夜清理任务删除（不动任务原图）。'
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

    # 图库索引：页面之外的消费方（清理任务、其它看板）不用再扫盘
    if with_images:
        try:
            os.makedirs(assets_root, exist_ok=True)
            idx = {"built_at": now, "days": gallery_days,
                   "count": len(gallery_entries), "images": gallery_entries}
            with open(os.path.join(assets_root, "gallery.json"), "w",
                      encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False, indent=1)
        except OSError as e:                                 # noqa: BLE001
            print("[build_private] 图库索引写入失败（不影响页面）: %s" % e, file=sys.stderr)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "private.html"))
    ap.add_argument("--assets", default=None,
                    help="图片落地目录，默认 <页面目录>/assets/private")
    ap.add_argument("--max-images", type=int, default=0,
                    help="每个任务最多展示几张（0=近 gallery-days 天全部，默认）")
    ap.add_argument("--gallery-days", type=int, default=GALLERY_DAYS,
                    help="图库保留/展示天数（默认 %d）" % GALLERY_DAYS)
    ap.add_argument("--no-images", action="store_true", help="不扫图，只出指标")
    a = ap.parse_args()
    p = build_private(a.out, with_images=not a.no_images,
                      max_images=max(0, a.max_images), assets=a.assets,
                      gallery_days=max(1, a.gallery_days))
    print("已生成 %s  %.1f KB" % (p, os.path.getsize(p) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())