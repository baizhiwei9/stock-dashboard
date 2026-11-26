#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""modules.json 可移植性守卫。

为什么需要这组用例
──────────────────
`modules.json` 现在**入库**了（2026-11-26）。它能入库的唯一前提是：文件里不含任何
「只在这一台机器上成立」的东西 —— 本机绝对路径、不认识的占位符、跟 `case/` 撞 key
的同名任务。这三样任意一条漏进来，新机器 clone 下来就是「能启动、但任务静默跑空」，
比启动失败难查得多。

所以这里把三条硬约束钉成测试，改 modules.json 时自动拦：
  1. cwd / cmd 里不许出现本机绝对路径（/opt /app /root /Coze /home ...）
  2. 占位符只许用 expand_root() 真正支持的那几个
  3. key 不许和本仓 case/<key>/task.json 重复（内置优先会把 case 那条屏蔽掉）
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "modules.json")

# expand_root() 支持的占位符白名单（加占位必须先加进 datahub_path.expand_root）
KNOWN_PLACEHOLDERS = ("{{DATAHUB_ROOT}}", "{{REPO_ROOT}}", "{{FEISHU_CLI_DIR}}")

# 机器相关的绝对路径前缀。系统路径（/usr/bin、/bin）不算。
MACHINE_PREFIXES = ("/opt/", "/app/", "/root/", "/Coze/", "/home/",
                    "/Users/", "/mnt/", "/srv/", "/data/", "/var/lib/")

# 本仓 case/ 下这些是框架目录，不是任务，不参与撞 key 检查
CASE_SKIP = {"_lib", "_shared", "_schema", "_seed", "_template", "_tools"}


def _cfg():
    assert os.path.isfile(CFG), "modules.json 不见了 —— 它已入库，缺了就 git checkout 恢复"
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


def test_cfg_parses_and_has_modules():
    d = _cfg()
    mods = d.get("modules")
    assert isinstance(mods, list) and mods, "modules.json 的 modules 为空"
    keys = [m.get("key") for m in mods]
    assert all(keys), "有模块缺 key：%r" % (keys,)
    assert len(keys) == len(set(keys)), "模块 key 重复：%r" % (keys,)


def test_no_machine_absolute_path():
    """cwd / cmd 里不许出现本机绝对路径 —— 这是 modules.json 能入库的前提。"""
    bad = []
    for m in _cfg()["modules"]:
        for fld in ("cwd", "cmd"):
            v = m.get(fld) or ""
            for pref in MACHINE_PREFIXES:
                for hit in re.findall(re.escape(pref) + r"[^\s\"';]*", v):
                    bad.append("%s.%s: %s" % (m["key"], fld, hit))
    assert not bad, "modules.json 里有本机绝对路径，换机器会跑空：\n  " + "\n  ".join(bad)


def test_only_known_placeholders():
    bad = []
    for m in _cfg()["modules"]:
        for fld in ("cwd", "cmd"):
            for ph in set(re.findall(r"\{\{[A-Z_]+\}\}", m.get(fld) or "")):
                if ph not in KNOWN_PLACEHOLDERS:
                    bad.append("%s.%s: %s" % (m["key"], fld, ph))
    assert not bad, ("出现了 expand_root() 不认识的占位符（会被原样传给子进程）：\n  "
                     + "\n  ".join(bad)
                     + "\n要么加进 datahub_path.expand_root()，要么删掉")


def test_placeholder_actually_expands():
    """白名单里的每个占位符都必须真能被替换掉，不能只是「名字对」。"""
    sys.path.insert(0, HERE)
    import datahub_path as dp
    for ph in KNOWN_PLACEHOLDERS:
        got = dp.expand_root("X=%s" % ph, root="/fake/hub", repo="/fake/repo")
        assert ph not in got, "%s 没被 expand_root 展开：%r" % (ph, got)
        assert got != "X=", "%s 展开成了空串（会让 cmd 静默变形）" % ph


def test_feishu_cli_dir_prefers_env(monkeypatch=None):
    """LARKSUITE_CLI_CONFIG_DIR 指到非空目录时，必须优先用它。"""
    import tempfile
    sys.path.insert(0, HERE)
    import datahub_path as dp
    d = tempfile.mkdtemp()
    open(os.path.join(d, "config.json"), "w").close()
    old = os.environ.get("LARKSUITE_CLI_CONFIG_DIR")
    os.environ["LARKSUITE_CLI_CONFIG_DIR"] = d
    try:
        assert dp.feishu_cli_dir() == d, dp.feishu_cli_dir()
    finally:
        if old is None:
            os.environ.pop("LARKSUITE_CLI_CONFIG_DIR", None)
        else:
            os.environ["LARKSUITE_CLI_CONFIG_DIR"] = old


def test_no_key_collision_with_case():
    """modules.json 的 key 不许和 case/<key>/ 重名。

    all_modules() 三源合并是「内置优先」：modules.json 里有同名 key 时，
    case/<key>/task.json 那条会被直接屏蔽 —— 现象是「任务改了没反应」，
    或者更糟：跑的是 modules.json 里那条指向旧位置的 cmd。
    2026-11-26 的 risk_v52 / vol_surface_vix 就是这个坑（已迁到 precious-task）。
    """
    case_dir = os.path.join(HERE, "case")
    if not os.path.isdir(case_dir):
        return
    have = set()
    for name in os.listdir(case_dir):
        if name in CASE_SKIP or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(case_dir, name, "task.json")):
            have.add(name)
    dup = sorted({m["key"] for m in _cfg()["modules"]} & have)
    assert not dup, ("modules.json 与 case/ 撞 key，case 那条会被静默屏蔽：%r\n"
                     "  二选一：要么从 modules.json 删掉，要么删掉 case/<key>/" % (dup,))


def test_enabled_modules_resolve_when_hub_present():
    """有 DataHub 时，enabled 模块展开后的 cwd 必须是真实目录。"""
    sys.path.insert(0, HERE)
    import datahub_path as dp
    hub = dp.datahub_root()
    if not hub:
        return          # 新机器还没 clone datahub-multi-tier：这条只在有 DataHub 时有意义
    bad = []
    for m in _cfg()["modules"]:
        if not m.get("enabled", True):
            continue
        exp = dp.expand_module(m)
        cwd = exp.get("cwd") or ""
        assert os.path.isabs(cwd), "%s 的 cwd 展开后不是绝对路径：%r" % (m["key"], cwd)
        if not os.path.isdir(cwd):
            bad.append("%s -> %s" % (m["key"], cwd))
    assert not bad, "enabled 模块的 cwd 在本机不存在：\n  " + "\n  ".join(bad)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            fails += 1
            print("  ❌ %s: %s" % (name, e))
    raise SystemExit(1 if fails else 0)
