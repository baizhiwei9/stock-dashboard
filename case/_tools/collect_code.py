#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_code.py — 只收集任务真正需要的程序文件（.py）

为什么不是整目录拷贝：会话目录里通常有上千个历史实验脚本（bt_*、*_v2.py、
旧版本备份），它们和当前任务无关。整目录会把仓库撑到几百 MB，也没意义。

做法：从入口脚本出发做 import 闭包
  1. 入口脚本所在目录的 .py（任务模块集，通常几十个）
  2. 解析 import / from ... import，逐层找到本地模块文件，递归
  3. 相对导入按当前包解析；`pylibs.*` / `data_hub.*` 这类走候选根查找
  4. 动态 import（importlib / __import__）无法静态解析，额外带上入口目录的
     同层模块作为兜底，并在结果里标注 dyn=True

只读不写。给 import_task.py 用，也可单独跑：
    python3 case/_tools/collect_code.py --root <目录> --entry run_daily.py
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

WS_TOKEN = "{WS}"
SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv", "site-packages",
    ".pytest_cache", ".mypy_cache", "tests", "test", "experiments", "exp",
    "archive", "backup", "bak", "old", "legacy", "dist", "build",
}
# 明显是历史实验/一次性脚本，闭包里不会被引用，这里只用于给"目录兜底"降噪
NOISE = re.compile(r"^(bt_|backtest_|tmp_|scratch_|old_|copy_|bak_|test_)", re.I)
# 历史会话目录（ses_xxx）：里面是别的任务的实验脚本，扫描时跳过
SES_RE = re.compile(r"^ses_[0-9a-f]{6,}", re.I)


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _mod_names(tree: ast.AST) -> List[Tuple[str, int]]:
    """抽出 (模块名, 相对层级)。相对导入 level>0。"""
    out: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, 0))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            lvl = node.level or 0
            if base:
                out.append((base, lvl))
            for a in node.names:
                if a.name != "*":
                    out.append(((base + "." + a.name) if base else a.name, lvl))
    return out


def has_dynamic_import(src: str) -> bool:
    return bool(re.search(r"\b(importlib|__import__|imp\.load)\b", src))


class Index:
    """候选根下的 模块名 → 文件 索引。"""

    def __init__(self, roots: List[str]):
        self.roots = [r for r in roots if r and os.path.isdir(r)]
        self.map: Dict[str, str] = {}
        for r in self.roots:
            self._scan(r, "", 0)

    def _scan(self, base: str, prefix: str, depth: int) -> None:
        if depth > 4:
            return
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return
        for n in names:
            p = os.path.join(base, n)
            if os.path.isdir(p):
                if n in SKIP_DIRS or n.startswith("."):
                    continue
                init = os.path.join(p, "__init__.py")
                if os.path.isfile(init):
                    self.map.setdefault((prefix + n).lstrip("."), init)
                self._scan(p, (prefix + n + ".") if prefix else n + ".", depth + 1)
            elif n.endswith(".py") and n != "__init__.py":
                self.map.setdefault((prefix + n[:-3]).lstrip("."), p)

    def find(self, mod: str) -> Optional[str]:
        m = mod.strip(".")
        if m in self.map:
            return self.map[m]
        # 尝试从长到短截断（from a.b.c import d 里 a.b.c.d 可能不存在）
        parts = m.split(".")
        for i in range(len(parts) - 1, 0, -1):
            k = ".".join(parts[:i])
            if k in self.map:
                return self.map[k]
        return None


def _pkg_of(path: str, root: str) -> str:
    """文件相对 root 的包前缀，如 a/b/c.py → a.b"""
    rel = os.path.relpath(path, root)
    d = os.path.dirname(rel)
    return d.replace(os.sep, ".") if d else ""


def collect(root: str, entry: str, extra_roots: Optional[List[str]] = None,
            same_dir_all: bool = True) -> Dict:
    """返回 {files:[相对路径...], dyn:bool, n:int, bytes:int}（相对 root）。

    root 为任务根；entry 为 root 下的入口脚本相对路径。
    same_dir_all=True 时把入口同目录的 .py 全部带上（模块集兜底）。
    """
    entry_abs = entry if os.path.isabs(entry) else os.path.join(root, entry)
    if not os.path.isfile(entry_abs):
        return {"files": [], "dyn": False, "n": 0, "bytes": 0, "err": "入口不存在"}

    roots = [root] + [r for r in (extra_roots or []) if os.path.isdir(r)]
    idx = Index(roots)

    files: Set[str] = set()
    dyn = False
    queue: List[str] = [entry_abs]

    if same_dir_all:
        d = os.path.dirname(entry_abs)
        for n in sorted(os.listdir(d)):
            if n.endswith(".py") and os.path.isfile(os.path.join(d, n)):
                files.add(os.path.join(d, n))

    seen: Set[str] = set()
    while queue:
        p = queue.pop()
        if p in seen:
            continue
        seen.add(p)
        files.add(p)
        src = _read(p) or ""
        if has_dynamic_import(src):
            dyn = True
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for mod, lvl in _mod_names(tree):
            cand: Optional[str] = None
            if lvl > 0:
                pkg = _pkg_of(p, root)
                parts = pkg.split(".") if pkg else []
                if lvl - 1 <= len(parts):
                    base = ".".join(parts[:len(parts) - (lvl - 1)]) if lvl > 1 else pkg
                    cand = idx.find((base + "." + mod) if base else mod)
                    if cand is None:
                        cand = idx.find(mod)
            else:
                cand = idx.find(mod)
            if cand and cand not in seen:
                queue.append(cand)

    # 只保留 root 内的文件；root 外的（公共库）记在 external
    out: List[str] = []
    ext: List[str] = []
    total = 0
    for f in sorted(files):
        try:
            total += os.path.getsize(f)
        except OSError:
            continue
        if os.path.commonpath([os.path.abspath(f), os.path.abspath(root)]) == os.path.abspath(root):
            out.append(os.path.relpath(f, root))
        else:
            ext.append(f)
    return {"files": out, "external": ext, "dyn": dyn, "n": len(out),
            "bytes": total}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--entry", required=True)
    ap.add_argument("--extra", default="", help="逗号分隔的额外候选根")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    extra = [x for x in a.extra.split(",") if x.strip()]
    r = collect(a.root, a.entry, extra)
    if r.get("err"):
        print("ERR", r["err"])
        return 1
    print("root=%s entry=%s" % (a.root, a.entry))
    print("程序文件 %d 个，%.1f KB，动态 import=%s，root 外依赖 %d 个"
          % (r["n"], r["bytes"] / 1024.0, r["dyn"], len(r.get("external") or [])))
    if a.list:
        for f in r["files"]:
            print("  ", f)
        for f in r.get("external") or []:
            print("  ext:", f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))