#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataHub 仓库根路径解析（唯一口径）。

build.py / snapshot.py / scheduler.py / manager.py 都从这里取 DataHub 根；
run_daily.sh / sd_watchdog.sh 直接 `python3 datahub_path.py` 取同一结果。
换机器时只改环境变量或 checkout 位置，所有入口一起跟着变。

优先级
──────
1. 显式环境变量 DATA_HUB_PATH → DATAHUB_DIR → EDB_REPO，
   取第一个「目录存在且含 data_hub/」的。只存在、但没有 data_hub/ 的路径
   会跳过 —— 选了它后面 import 也必然失败，跳过才有机会落到可用路径。
2. 同级 checkout：<本仓库>/../datahub-multi-tier；
   再兼容 <本仓库>/datahub-multi-tier 的旧布局。
3. 都没有时返回空串 —— 调用方必须显式报错。代码里不再保留云盘旧副本
   （/opt/coze/.fuse_drive/...）兜底：那条路径已废弃，退过去只会静默连错库。

只解析路径，不改 sys.path、不改环境变量。环境变量请给绝对路径。

    python3 datahub_path.py      # 打印解析结果，给 shell 脚本用
"""
import os
import sys

ENV_NAMES = ("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO")
SIBLING_DIRS = ("../datahub-multi-tier", "datahub-multi-tier")


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _is_hub_root(path):
    return bool(path) and os.path.isdir(os.path.join(path, "data_hub"))


def candidates(project=None):
    """按优先级返回 [(来源, 路径), ...]，排障时可打印出来看为什么选了某条。"""
    project = project or _project_dir()
    out = []
    for name in ENV_NAMES:
        val = os.environ.get(name)
        if val:
            out.append(("env:" + name, val))
    for rel in SIBLING_DIRS:
        out.append(("sibling", os.path.normpath(os.path.join(project, rel))))
    return out


def datahub_root(project=None):
    """返回实际可用的 DataHub 根；都没命中时返回空串（由调用方显式报错）。"""
    for _source, path in candidates(project):
        if _is_hub_root(path):
            return path
    return ""


def repo_root(project=None):
    """本仓库（stock-dashboard）根：{{REPO_ROOT}} 的展开值。"""
    return _project_dir() if project is None else os.path.abspath(project)


def feishu_cli_dir(repo=None):
    """lark-cli 配置目录（{{FEISHU_CLI_DIR}} 的展开值）。

    为什么需要占位：这个目录**只跟机器/平台有关**，跟业务无关。老写法在
    modules.json 里写死 `/app/data/所有对话/主对话/.feishu_cli`，换台机器就是
    一个不存在的路径 —— 而给错目录比不给更糟，lark-cli 会认不到登录态并
    静默推送失败。

    按优先级取第一个「真实存在且非空」的目录（空目录等于没登录）：
      1. 环境变量 LARKSUITE_CLI_CONFIG_DIR（显式指定最优先）
      2. <本仓库>/.feishu_cli（仓库内自带，便于整仓搬迁）
      3. ~/.feishu_cli（新机器 lark-cli 登录后的默认位置）
      4. /app/data/所有对话/主对话/.feishu_cli（Coze 平台的标准位置）
    都不命中时返回 ~/.feishu_cli：让 lark-cli 直接报「未登录」，
    比塞一个别的机器的空目录好排查。
    """
    def _ok(p):
        try:
            return bool(p) and os.path.isdir(p) and bool(os.listdir(p))
        except OSError:
            return False

    repo = repo if repo is not None else repo_root()
    for cand in (os.environ.get("LARKSUITE_CLI_CONFIG_DIR"),
                 os.path.join(repo, ".feishu_cli"),
                 os.path.join(os.path.expanduser("~"), ".feishu_cli"),
                 "/app/data/所有对话/主对话/.feishu_cli"):
        if _ok(cand):
            return cand
    return os.path.join(os.path.expanduser("~"), ".feishu_cli")


def expand_root(value, root=None, repo=None):
    """把 modules.json 的 cwd/cmd 里的占位换成解析结果。

    支持三个占位：
      {{DATAHUB_ROOT}}   → datahub_root()（DataHub 仓库根）
      {{REPO_ROOT}}      → 本仓库根（cleanup / tech_snapshot 这类框架自带模块用）
      {{FEISHU_CLI_DIR}} → feishu_cli_dir()（lark-cli 配置目录，只跟机器有关）

    为什么 modules.json 现在能入库：这三个占位把「本机差异」全部收口了，
    文件里不再出现任何 /opt、/app、/root 开头的绝对路径，换机器 clone 下来
    就能跑（见 DEPLOY_PORTABLE.md §6）。
    """
    if not isinstance(value, str):
        return value
    if "{{DATAHUB_ROOT}}" in value:
        value = value.replace("{{DATAHUB_ROOT}}",
                              root if root is not None else datahub_root())
    if "{{REPO_ROOT}}" in value:
        value = value.replace("{{REPO_ROOT}}",
                              repo if repo is not None else repo_root())
    if "{{FEISHU_CLI_DIR}}" in value:
        value = value.replace("{{FEISHU_CLI_DIR}}", feishu_cli_dir(repo))
    return value


def expand_module(module, root=None, repo=None):
    """返回副本：把模块 cwd/cmd 里的占位展开，其余字段原样保留。"""
    root = root if root is not None else datahub_root()
    repo = repo if repo is not None else repo_root()
    out = dict(module)
    for field in ("cwd", "cmd"):
        value = out.get(field)
        if isinstance(value, str):
            out[field] = expand_root(value, root, repo)
    return out


if __name__ == "__main__":
    _root = datahub_root()
    if not _root:
        sys.stderr.write(
            "找不到 DataHub（需要含 data_hub/ 的仓库）：设 DATA_HUB_PATH，"
            "或把 datahub-multi-tier clone 到本仓库同级\n")
        raise SystemExit(1)
    print(_root)
