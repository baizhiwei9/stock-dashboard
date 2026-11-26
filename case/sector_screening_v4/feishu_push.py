# -*- coding: utf-8 -*-
"""
飞书推送薄壳（2026-11-20 改造）

大佬要求：飞书发送只保留一份代码，元宝镜像钩子只挂一处。
真正实现已统一到 DataHub 仓库：
    /opt/coze/.fuse_drive/dataHub_三级体系/options/alert/notifier/feishu_push.py

本文件不再保存实现，只做转发，保持老脚本 `from feishu_push import xxx` 不变。
镜像钩子（_mirror_to_yuanbao）也只存在于那份实现里，改一处两边都生效。

如需临时关闭元宝镜像：环境变量 YUANBAO_MIRROR=0
"""

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
    for p in ("/opt/coze/.fuse_drive/dataHub_三级体系/options/alert/notifier/feishu_push.py",
              "/Coze/Drive/dataHub_三级体系/options/alert/notifier/feishu_push.py"):
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
    spec = importlib.util.spec_from_file_location('_canon_feishu_push', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['_canon_feishu_push'] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_canonical()

# 把实现里的公开符号（含下划线工具函数）全部暴露到本模块命名空间
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith('__')})


def __getattr__(name):
    """后续新增的函数也能自动转发，不用改这个壳"""
    try:
        return getattr(_mod, name)
    except AttributeError:
        raise AttributeError(f'module feishu_push has no attribute {name!r}') from None
