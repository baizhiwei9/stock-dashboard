#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case 层 DataHub 根 / 凭据的统一解析（唯一实现）。

为什么要有这个文件
──────────────────
2026-11-26 把 21 条日更/周更任务迁进 ``case/`` 后，这些脚本仍各自写死
``/opt/coze/.fuse_drive/dataHub_三级体系`` 或 ``<主对话>/codeact/scripts``。
迁到本机盘后它们**先命中云盘那份**，读出来的是另一套库 —— 不报错，只是数字
不对，属于「静默连错库」。同一天早些时候 ``_shared/limit_up_predictor`` 等 5 个
脚本已按同一口径改过，这里把口径固化成一份，新迁任务直接调，不再各写一套。

DataHub 根口径（与 ``stock-dashboard/datahub_path.py`` 完全一致）
─────────────────────────────────────────────────────────────
1. 向上找仓库根的 ``datahub_path.py``，exec 它拿 ``datahub_root()``
   （环境变量 → 同级 checkout → 云盘兜底，全在那一份里）；
2. 它不在时退回「同级 checkout ``<repo>/../datahub-multi-tier``」；
3. 最后才退老云盘路径 —— 原机部署还在用，不能直接删。

刻意**不**按「上层目录里存在 datahub-multi-tier 就认」来猜：机器上残留的
同名旧副本（``/tmp/datahub-multi-tier`` 这类）会让它静默连错库。

Tushare token 口径
──────────────────
``tushare_token()``：环境变量 ``TUSHARE_TOKEN`` → ``case/_lib/secrets.json``
（或 ``~/.config/stock-dashboard/secrets.json``）→ 空串。代码里不再写明文；
scheduler 拉起任务前会 ``app_secrets.export_env()``，所以走调度时一定有值。

用法
────
    from hub_root import hub_root, ensure_hub_on_path, tushare_token
    root = ensure_hub_on_path(__file__)     # 顺手塞进 sys.path

任务由 scheduler 用 ``python3 -u scripts/xxx.py`` 直拉，``sys.path`` 里没有
``case/_lib``，所以脚本里先用 ``_bootstrap_hub_lib(__file__)`` 找到本目录。
"""
import json
import os
import sys

CLOUD_DEFAULT = "/opt/coze/.fuse_drive/dataHub_三级体系"
ENV_NAMES = ("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO")

# AnchorStructor 大项目根的结构标记：只认这些文件，不按目录名猜。
_STRUCT_MARKERS = ("members.toml", "members.public.toml", "bootstrap.sh")


def _is_hub(path):
    """DataHub 根的特征：目录下有 data_hub/。"""
    return bool(path) and os.path.isdir(os.path.join(path, "data_hub"))


def _lib_dir(start):
    """从 start 往上找 case/_lib，返回其绝对路径或空串。"""
    cur = os.path.abspath(start)
    for _ in range(8):
        cand = os.path.join(cur, "_lib")
        if os.path.isfile(os.path.join(cand, "hub_root.py")):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def _bootstrap_hub_lib(start):
    """把 case/_lib 挂进 sys.path（重复调用无副作用），返回其路径或空串。"""
    lib = _lib_dir(start)
    if lib and lib not in sys.path:
        sys.path.insert(0, lib)
    return lib


def _installed_hub_root():
    """已安装的 data_hub 包反推出来的仓库根（`pip install -e` 时即源码目录）。

    装了成员库就不该再猜目录：`import data_hub` 能成立，说明包已在解释器里，
    把源码位置反推成根即可。与 `datahub_path.py` 的 `installed:data_hub` 同口径。
    """
    try:
        import importlib.util as _iu
        spec = _iu.find_spec("data_hub")
    except Exception:                                      # noqa: BLE001
        return ""
    if spec is None:
        return ""
    origin = getattr(spec, "origin", None)
    if origin and origin.endswith(".py"):
        return os.path.dirname(os.path.dirname(os.path.abspath(origin)))
    locs = list(getattr(spec, "submodule_search_locations", None) or [])
    return os.path.dirname(os.path.abspath(locs[0])) if locs else ""


def hub_candidates(start=None):
    """按优先级返回 DataHub 根候选（已去重），排障时可直接打印。

    顺序：显式环境变量 → 本仓 `datahub_path.py` 的解析（含同级 checkout）
    → 大项目根（带 `members.toml`/`bootstrap.sh` 的祖先）的同级 checkout
    → 已安装的 `data_hub` 包 → 云盘兜底。

    为什么还留「大项目根」这一条：私有任务在 `precious-task/case/<key>` 下，
    向上碰不到 `datahub_path.py`，不加这条就只能落到不存在的云盘常量。
    判据只认 AnchorStructor 的结构标记，**不按「上层有同名目录就认」**——
    机器上残留的 `/tmp/datahub-multi-tier` 这类旧副本会让人静默连错库。
    """
    out = [(os.environ.get(n) or "") for n in ENV_NAMES]
    cur = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    local_done = False
    for _ in range(8):
        p = os.path.join(cur, "datahub_path.py")
        if os.path.isfile(p) and not local_done:
            ns = {"__file__": p, "__name__": "datahub_path"}
            try:
                with open(p, encoding="utf-8") as f:
                    exec(compile(f.read(), p, "exec"), ns)
                if callable(ns.get("datahub_root")):
                    out.append(ns["datahub_root"]())
            except Exception:                              # noqa: BLE001
                pass
            out.append(os.path.join(os.path.dirname(cur), "datahub-multi-tier"))
            out.append(os.path.join(cur, "datahub-multi-tier"))
            local_done = True
        elif not local_done and any(os.path.isfile(os.path.join(cur, m))
                                    for m in _STRUCT_MARKERS):
            out.append(os.path.join(cur, "datahub-multi-tier"))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    out.append(_installed_hub_root())
    out.append(CLOUD_DEFAULT)
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def hub_root(start=None):
    """第一个真实可用的 DataHub 根；都没有时返回空串。

    不再直接返回不存在的云盘兜底常量：它只作为候选之一参与 `_is_hub` 检查，
    不存在就跳过 —— 返回一个不存在的根比返回空更容易掩盖问题。
    """
    for p in hub_candidates(start):
        if _is_hub(p):
            return p
    return ""


def ensure_hub_on_path(start=None):
    """把存在的 DataHub 根前置进 sys.path 并返回它（找不到返回空串）。"""
    root = hub_root(start)
    if root and root not in sys.path:
        sys.path.insert(0, root)
    return root


def hub_side_dir(*rel, **kw):
    """DataHub 仓库内的子目录，如 hub_side_dir('options','alert','notifier')。

    根找不到时返回空串 —— 别拼出一个相对路径让调用方误以为有货。
    """
    root = hub_root(kw.get("start"))
    return os.path.join(root, *rel) if root else ""


# ── 凭据 ─────────────────────────────────────────────────────────────────
def _secret_files():
    """凭据文件候选（每次现算 —— 改 HOME / COZE_SECRETS 后立刻生效）。"""
    return (
        os.environ.get("COZE_SECRETS", ""),
        os.environ.get("APP_SECRETS_FILE", ""),
        os.path.join(os.path.expanduser("~"), ".config", "stock-dashboard", "secrets.json"),
    )


def _read_secret(dotted):
    """从 secrets.json 读点号键；读不到返回空串（不抛异常）。"""
    for path in _secret_files():
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                node = json.load(f)
        except (OSError, ValueError):
            continue
        for part in dotted.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def tushare_token(default=""):
    """Tushare token：环境变量 → secrets.json → default。代码里不再写明文。"""
    for name in ("TUSHARE_TOKEN", "COZE_SEC_TUSHARE_TOKEN"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return _read_secret("tushare.token") or default


def smtp_password(default=""):
    """163/SMTP 授权码：环境变量 → secrets.json → default。代码里不写明文。"""
    for name in ("SMTP_PASSWORD", "EMAIL_AUTH_CODE", "EMAIL_PASSWORD",
                 "COZE_SEC_EMAIL_PASSWORD"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return _read_secret("email.password") or default


def feishu_webhook(default=""):
    """飞书群机器人 webhook：环境变量 → secrets.json → default。"""
    for name in ("FEISHU_WEBHOOK", "FEISHU_WEBHOOK_URL", "COZE_SEC_FEISHU_WEBHOOK"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return _read_secret("feishu.webhook") or default


if __name__ == "__main__":
    for _p in hub_candidates():
        print("%-6s %s" % ("存在" if _is_hub(_p) else "缺失", _p))
    for _k, _v in (("tushare.token", tushare_token()),
                   ("email.password", smtp_password()),
                   ("feishu.webhook", feishu_webhook())):
        print("%-6s %s" % ("有值" if _v else "缺失", _k))
