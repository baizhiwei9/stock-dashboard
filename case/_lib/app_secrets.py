#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用敏感配置统一入口 —— 所有密钥/口令集中一个文件，源码里不留明文。

凭据文件（权限 600，不入任何仓库，不放项目共享目录）::

    ~/.config/stock-dashboard/secrets.json

搜索顺序（命中即止）：

1. ``$APP_SECRETS_FILE``
2. ``~/.config/stock-dashboard/secrets.json``
3. ``<repo>/case/_lib/secrets.json``   （已被 .gitignore 排除，备选）

文件结构（各段独立，缺段不算错，取到空串）::

    {
      "feishu":    {"app_id": "...", "app_secret": "...", "webhook_url": "..."},
      "scheduler": {"password": "...", "registry_key": "..."},
      "treasury":  {"client_id": "...", "client_secret": "..."}
    }

对应的环境变量（优先级最高，容器/流水线用）::

    FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_WEBHOOK_URL
    SCHEDULER_PASSWORD / SCHEDULER_REGISTRY_KEY
    TREASURY_CLIENT_ID / TREASURY_CLIENT_SECRET

兼容：旧的扁平 ``feishu_secret.json`` 仍然能读，只影响 feishu 段。

安装时必须先跑 ``python3 setup.py`` 录入这些值；缺项时运行期会 fail-closed，
不会用硬编码兜底（历史坑：scheduler 管理密码曾硬编码 "123456"）。
"""
import json
import os

__all__ = [
    "SecretMissing", "section", "get", "require", "source", "file_candidates",
    "set_value", "write_target",
]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 段 -> {字段: 环境变量名}
_ENV_MAP = {
    "feishu": {
        "app_id": "FEISHU_APP_ID",
        "app_secret": "FEISHU_APP_SECRET",
        "webhook_url": "FEISHU_WEBHOOK_URL",
    },
    "scheduler": {
        "password": "SCHEDULER_PASSWORD",
        "registry_key": "SCHEDULER_REGISTRY_KEY",
    },
    "treasury": {
        "client_id": "TREASURY_CLIENT_ID",
        "client_secret": "TREASURY_CLIENT_SECRET",
    },
}


class SecretMissing(RuntimeError):
    """敏感配置缺失。"""


def file_candidates():
    env = (os.environ.get("APP_SECRETS_FILE") or "").strip()
    if env:
        # 显式指定就只认它：容器里挂载失败时静默回落到别的文件，
        # 会出现「以为用的是 A，其实读的是 B」的排查地狱。
        yield env
        return
    yield os.path.join(os.path.expanduser("~"), ".config", "stock-dashboard", "secrets.json")
    yield os.path.join(_REPO_ROOT, "case", "_lib", "secrets.json")
    # 兼容旧文件名（扁平结构，只服务 feishu 段）
    yield os.path.join(os.path.expanduser("~"), ".config", "stock-dashboard", "feishu_secret.json")
    yield os.path.join(_REPO_ROOT, "case", "_lib", "feishu_secret.json")


def _load():
    """返回 (整个 dict, 命中的路径)。"""
    for path in file_candidates():
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            return d, path
    return {}, None


_DATA, _PATH = _load()


def source():
    return _PATH or "none"


def section(name):
    """取一整段，返回 dict（缺段返回 {}）。兼容旧扁平 feishu 文件。"""
    raw = _DATA.get(name)
    if isinstance(raw, dict):
        out = dict(raw)
    elif name == "feishu" and any(k in _DATA for k in ("app_id", "app_secret", "webhook_url")):
        # 旧扁平格式 {"app_id": ..., "app_secret": ..., "webhook_url": ...}
        out = {k: _DATA.get(k, "") for k in ("app_id", "app_secret", "webhook_url")}
    else:
        out = {}
    for key, env in (_ENV_MAP.get(name) or {}).items():
        v = (os.environ.get(env) or "").strip()
        if v:
            out[key] = v
    return out


def get(name, key, default=""):
    v = section(name).get(key)
    return v if isinstance(v, str) and v.strip() else default


def require(name, *keys):
    """校验某段的若干字段。缺失抛 SecretMissing，报错里带配置指引。"""
    sec = section(name)
    missing = [k for k in keys if not (isinstance(sec.get(k), str) and sec.get(k).strip())]
    if not missing:
        return sec
    envs = [(_ENV_MAP.get(name) or {}).get(k, k.upper()) for k in missing]
    raise SecretMissing(
        "%s 段缺少 %s。请任选一种方式配置——\n"
        "  1) 环境变量：%s\n"
        "  2) 凭据文件：%s\n"
        "     结构见 case/_lib/secrets.example.json\n"
        "  3) 交互式录入：python3 setup.py\n"
        "详见 DEPLOY.md「敏感配置」一节。当前来源：%s"
        % (name, ", ".join(missing), " / ".join(envs),
           os.path.join(os.path.expanduser("~"), ".config", "stock-dashboard", "secrets.json"),
           source())
    )


def write_target():
    """写入目标：$APP_SECRETS_FILE，否则 ~/.config/stock-dashboard/secrets.json。"""
    env = (os.environ.get("APP_SECRETS_FILE") or "").strip()
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".config", "stock-dashboard", "secrets.json")


def set_value(name, key, value, path=None):
    """写入一段的一个字段，返回落盘路径。

    只给运维命令（如 manager.py sections pw）用；正常部署走 setup.py。
    写文件不改环境变量优先级 —— 若同名环境变量存在，运行时仍以环境变量为准，
    返回值里由调用方自行提示。
    """
    global _DATA, _PATH
    p = path or write_target()
    d = {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f) or {}
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    # 旧扁平 feishu 文件顺手升级成分段结构
    if name == "feishu" and not isinstance(d.get("feishu"), dict):
        flat = {k: d.pop(k) for k in ("app_id", "app_secret", "webhook_url") if k in d}
        if flat:
            d["feishu"] = flat
    seg = d.get(name)
    if not isinstance(seg, dict):
        seg = {}
    seg[key] = value
    d[name] = seg

    d_dir = os.path.dirname(p)
    if d_dir:
        os.makedirs(d_dir, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass

    _DATA, _PATH = d, p
    return p
