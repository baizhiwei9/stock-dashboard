# -*- coding: utf-8 -*-
"""成长股选股器入口（薄封装）。

2026-11-26 去云盘化
──────────────────
真正实现在「蓁宝/所有对话/主对话/成长股选股器/scripts/growth_screener_dual.py」，
这份代码**没有**进 stock-dashboard 仓库，所以它仍是一处外部依赖。

原机靠云盘路径直连；迁机后：
  · 把该目录放到新位置，然后 export CASE_WORKSPACE=<蓁宝主对话目录>
  · 或 export GROWTH_SCREENER_DIR=<成长股选股器目录>

两者都没有时**明确报错**，而不是像以前那样抛一个 FileNotFoundError
让人以为是代码坏了。
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()
import importlib.util
import os
import sys

_WS = (os.environ.get("CASE_WORKSPACE") or "").strip()
_DIR = (os.environ.get("GROWTH_SCREENER_DIR") or "").strip()

_cands = []
if _DIR:
    _cands.append(_DIR)
if _WS:
    _cands.append(os.path.join(_WS, "成长股选股器"))
# 原机云盘兜底（迁机后通常不存在）
_cands.append(_cp("/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器"))
_cands.append(_cp("/opt/coze/.fuse_drive/蓁宝/所有对话/主对话/成长股选股器"))

GROWTH_DIR = next((p for p in _cands if p and os.path.isdir(p)), "")
TARGET = os.path.join(GROWTH_DIR, "scripts", "growth_screener_dual.py") if GROWTH_DIR else ""

if not TARGET or not os.path.isfile(TARGET):
    raise ImportError(
        "成长股选股器代码不在仓库内，需要单独提供。\n"
        "已尝试：\n  " + "\n  ".join(p or "(空)" for p in _cands) + "\n"
        "解决方式（二选一）：\n"
        "  export CASE_WORKSPACE=<蓁宝/所有对话/主对话 的新位置>\n"
        "  export GROWTH_SCREENER_DIR=<成长股选股器 目录>\n"
        "详见 DEPLOY_PORTABLE.md「仓库外依赖」一节。"
    )

sys.path.insert(0, os.path.join(GROWTH_DIR, "scripts"))
os.chdir(GROWTH_DIR)

spec = importlib.util.spec_from_file_location("gs", TARGET)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
