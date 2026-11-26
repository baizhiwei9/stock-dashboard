# -*- coding: utf-8 -*-
"""
飞书推送薄壳（2026-11-20 改造）

飞书发送实现统一到 DataHub 仓库：
    /opt/coze/.fuse_drive/dataHub_三级体系/options/alert/notifier/feishu_push.py
其中已挂元宝群镜像钩子（_mirror_to_yuanbao），改一处全局生效。
本文件只做转发，老脚本 `from feishu_push import ...` 不受影响。
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

_CANON_REL = ("options", "alert", "notifier", "feishu_push.py")


def _find_canon():
    """找统一推送实现。迁机后不再依赖云盘。

    顺序：FEISHU_PUSH_CANON 环境变量 → 向上找同级 datahub-multi-tier 仓库
    （2026-11-26 起 options/ 已入库）→ 原机云盘两个挂载视图兜底。
    """
    env = (os.environ.get("FEISHU_PUSH_CANON") or "").strip()
    if env and os.path.exists(env):
        return env
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        for base in (os.path.join(d, ".."), d):
            cand = os.path.normpath(os.path.join(base, "datahub-multi-tier", *_CANON_REL))
            if os.path.exists(cand):
                return cand
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    for p in (_cp("/opt/coze/.fuse_drive/dataHub_三级体系/options/alert/notifier/feishu_push.py"),
              _cp("/Coze/Drive/dataHub_三级体系/options/alert/notifier/feishu_push.py")):
        if os.path.exists(p):
            return p
    return ""


_CANON = _find_canon()
def _load_canonical(path=_CANON):
    if not os.path.exists(path):
        raise ImportError(
            f'统一推送实现缺失: {path}\n'
            '飞书推送/元宝镜像的唯一实现放在 DataHub 仓库 options/alert/notifier/。\n'
            '迁机后请确认 datahub-multi-tier 与 stock-dashboard 是同级目录，\n'
            '或用环境变量 FEISHU_PUSH_CANON 直接指定该文件。'
        )
    spec = importlib.util.spec_from_file_location('_canon_feishu_push_lr', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['_canon_feishu_push_lr'] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_canonical()
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith('__')})


def __getattr__(name):
    try:
        return getattr(_mod, name)
    except AttributeError:
        raise AttributeError(f'module feishu_push has no attribute {name!r}') from None
