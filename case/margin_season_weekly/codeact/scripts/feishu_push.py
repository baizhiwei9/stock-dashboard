# -*- coding: utf-8 -*-
"""
飞书推送薄壳（2026-11-20 改造）

飞书发送实现统一到 DataHub 仓库：
    /opt/coze/.fuse_drive/dataHub_三级体系/options/alert/notifier/feishu_push.py
其中已挂元宝群镜像钩子（_mirror_to_yuanbao），改一处全局生效。
本文件只做转发，老脚本 `from feishu_push import ...` 不受影响。
"""

import importlib.util
import os
import sys

_CANON = '/opt/coze/.fuse_drive/dataHub_三级体系/options/alert/notifier/feishu_push.py'


def _load_canonical(path=_CANON):
    if not os.path.exists(path):
        raise ImportError(
            f'统一推送实现缺失: {path}\n'
            '飞书推送/元宝镜像的唯一实现放在 DataHub 仓库，请检查 .fuse_drive 挂载。'
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
