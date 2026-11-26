#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assets.py — 运行时资产的准备（主要是中文字体）

仓库只放程序文件（.py），字体这类二进制资源不入库；但出图需要能打开的中文字体，
所以由框架在任务取数阶段准备一次：

    from assets import ensure_cjk_font
    font = ensure_cjk_font(os.path.join(HERE, "fonts", "SimHei.ttf"))

查找顺序（第一个存在的就用）：
    1. 环境变量 CASE_CJK_FONT 指定的字体文件
    2. 目标路径已存在（之前准备过）
    3. 本机常见中文字体（Noto Sans SC / 思源 / 文泉驿 / Droid Sans Fallback / SimHei）
    4. fc-match 解析到的任意中文字体

复制到目标路径后返回该路径；一个都找不到就抛 RuntimeError，让任务明确失败，
而不是画出一堆豆腐块还当成功。
"""
from __future__ import annotations

import os
import shutil
import subprocess

CANDIDATES = (
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


def _from_fc_match() -> str:
    for pat in ("Noto Sans SC", "Droid Sans Fallback", "sans-serif:lang=zh"):
        try:
            r = subprocess.run(["fc-match", "-f", "%{file}", pat],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        p = (r.stdout or "").strip()
        if p and os.path.isfile(p):
            return p
    return ""


def find_cjk_font() -> str:
    env = (os.environ.get("CASE_CJK_FONT") or "").strip()
    if env and os.path.isfile(env):
        return env
    for p in CANDIDATES:
        if os.path.isfile(p):
            return p
    # 用户级字体目录（XDG_DATA_HOME/fonts、~/.fonts）
    for base in (os.environ.get("XDG_DATA_HOME", ""), os.path.expanduser("~")):
        if not base:
            continue
        for sub in ("fonts", ".fonts"):
            d = os.path.join(base, sub)
            if not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                for f in files:
                    if f.lower().endswith((".ttf", ".otf", ".ttc")):
                        return os.path.join(root, f)
    return _from_fc_match()


def ensure_cjk_font(dst: str, force: bool = False) -> str:
    """确保 dst 处有一份可用中文字体，返回可用路径（可能就是 dst）。"""
    if os.path.isfile(dst) and not force:
        return dst
    src = find_cjk_font()
    if not src:
        raise RuntimeError(
            "找不到可用的中文字体：请设置 CASE_CJK_FONT 指向一个 .ttf/.otf/.ttc，"
            "或把字体放到 %s" % dst)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        os.link(src, dst)                    # 同盘硬链，省一份 9MB 拷贝
    except OSError:
        shutil.copy2(src, dst)
    return dst


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", required=True)
    a = ap.parse_args(argv)
    print(ensure_cjk_font(a.dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())