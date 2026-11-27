#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""start_all.sh 的回归测试：一条命令起「公有 + 私有 + 看门狗」，不碰真调度器。

全部用临时目录里的假 scheduler.py / setup.py 跑，不联网、不触发任何任务。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
START = os.path.join(HERE, "start_all.sh")
WD = os.path.join(HERE, "sd_watchdog.sh")


def _write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)


def _fake_deploy(tmp):
    """最小可运行部署：假 scheduler + 假 setup + 假 DataHub + 一个私有任务。"""
    root = tmp / "stock-dashboard"
    root.mkdir()
    for name in ("start_all.sh", "sd_watchdog.sh", "datahub_path.py"):
        shutil.copy2(os.path.join(HERE, name), root / name)
    (root / "start_all.sh").chmod(0o755)
    (root / "sd_watchdog.sh").chmod(0o755)

    _write(root / "setup.py", "import sys\nsys.exit(0)\n")

    _write(root / "scheduler.py", '''#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
PIDF = os.path.join(HERE, "scheduler.pid")
ENVF = os.path.join(HERE, "start_env.json")
def pid():
    try:
        return int(open(PIDF).read().strip())
    except Exception:
        return 0
def alive(p):
    return bool(p) and os.path.exists("/proc/%d" % p)
c = sys.argv[1] if len(sys.argv) > 1 else ""
if c == "start":
    p = pid()
    if alive(p):
        print("已在运行 pid=%d" % p); sys.exit(0)
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    with open(ENVF, "w") as f:
        json.dump(dict(os.environ), f)
    # 与真 scheduler 一致：把本次运行期过滤写进 state.json（start_all 靠它判断
    # 「已在运行的过滤条件」要不要先停再起）
    only = [k for k in os.environ.get("SD_ONLY", "").split(",") if k]
    with open(os.path.join(HERE, "state.json"), "w") as f:
        json.dump({"scheduler": {"only": only}, "modules": {}}, f)
    log = open(os.path.join(HERE, "logs", "scheduler.log"), "ab")
    q = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(3600)"],
                         stdout=log, stderr=log, start_new_session=True)
    open(PIDF, "w").write(str(q.pid))
    time.sleep(0.2)
    print("调度器已启动 pid=%d" % q.pid); sys.exit(0)
if c == "stop":
    p = pid()
    if alive(p):
        os.kill(p, signal.SIGTERM)
    try: os.unlink(PIDF)
    except OSError: pass
    print("已停止"); sys.exit(0)
if c == "health":
    p = pid()
    print("健康 pid=%d" % p if alive(p) else "未运行")
    sys.exit(0 if alive(p) else 1)
if c == "status":
    print("fake status pid=%d" % pid()); sys.exit(0)
sys.exit(1)
''', 0o755)

    priv_case = tmp / "precious-task" / "case" / "dummy_priv"
    priv_case.mkdir(parents=True)
    _write(priv_case / "task.json", json.dumps({
        "key": "dummy_priv", "title": "私有测试", "visibility": "private",
        "sched": {"mode": "daily", "at": "22:00"}, "cmd": "true", "enabled": False,
    }, ensure_ascii=False))

    hub = tmp / "datahub-multi-tier" / "data_hub"
    hub.mkdir(parents=True)

    # doctor 要真扫 task.json：把纯标准库的注册表复制进 fixture 的空 case/
    (root / "case" / "_lib").mkdir(parents=True)
    shutil.copy2(os.path.join(HERE, "case", "_lib", "case_registry.py"),
                 root / "case" / "_lib" / "case_registry.py")
    return root


def _run(root, *args, **env_extra):
    env = dict(os.environ)
    env.pop("CASE_EXTRA_DIRS", None)
    env.pop("DATA_HUB_PATH", None)
    env.pop("EDB_REPO", None)
    env.pop("SD_ONLY", None)
    env.pop("SD_NO_API", None)
    env["SD_PYTHON"] = sys.executable
    env.update(env_extra)
    return subprocess.run(["bash", str(root / "start_all.sh"), *args],
                          cwd=str(root), env=env, capture_output=True,
                          text=True, timeout=60)


def _read_pid(root):
    try:
        return int((root / "scheduler.pid").read_text().strip())
    except (OSError, ValueError):
        return 0


def _alive(pid):
    return bool(pid) and os.path.exists("/proc/%d" % pid)


def test_scripts_syntax():
    for f in (START, WD):
        r = subprocess.run(["bash", "-n", f], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_doctor_flags_public_task_in_private_repo(tmp_path):
    """公私归属检查：私有仓里出现 public 任务要报出来。"""
    root = _fake_deploy(tmp_path)
    case = tmp_path / "precious-task" / "case"
    _write(case / "dummy_pub" / "task.json", json.dumps({
        "key": "dummy_pub", "title": "公开测试", "visibility": "public",
        "sched": {"mode": "daily", "at": "22:01"}, "cmd": "true", "enabled": False,
    }, ensure_ascii=False))

    r = _run(root, "doctor", "--json", SD_PRECIOUS_DIR=str(tmp_path / "precious-task"))
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)

    assert d["paths_ok"]["precious"] is True
    assert d["tasks"]["private"] == 1
    assert d["enabled_private"] == []
    assert any("dummy_pub" in e and "应放 stock-dashboard" in e
               for e in d["split_errors"])


def test_up_status_down_and_env(tmp_path):
    """一条命令起全部：私有目录/DataHub 注入，看门狗拉起挂掉的调度器，down 收干净。"""
    root = _fake_deploy(tmp_path)
    env = {"SD_WATCHDOG_INTERVAL": "1",
           "SD_PRECIOUS_DIR": str(tmp_path / "precious-task")}

    up = _run(root, "up", **env)
    assert up.returncode == 0, up.stdout + up.stderr
    assert "调度器已启动" in up.stdout
    assert "看门狗已启动" in up.stdout
    # 空 modules 要明说：否则新机器上公开看板没人构建，看起来却一切正常
    assert "没有旧生产模块" in up.stderr

    # 子进程环境：私有仓挂进来了，DataHub 指向同级 fake 仓
    start_env = json.loads((root / "start_env.json").read_text())
    assert "precious-task/case" in start_env.get("CASE_EXTRA_DIRS", "")
    assert start_env.get("DATA_HUB_PATH", "").endswith("datahub-multi-tier")

    # modules.json 由脚本生成，且不含旧模块
    mods = json.loads((root / "modules.json").read_text())
    assert mods["modules"] == []

    st = _run(root, "status")
    assert st.returncode == 0
    assert "看门狗：运行中" in st.stdout

    # 调度器被 kill 后，看门狗应在 1s 间隔内拉起一个新进程
    old = _read_pid(root)
    assert _alive(old)
    os.kill(old, signal.SIGKILL)
    time.sleep(0.2)
    try:
        os.unlink(root / "scheduler.pid")
    except OSError:
        pass
    new = 0
    for _ in range(20):
        time.sleep(0.5)
        new = _read_pid(root)
        if new and new != old and _alive(new):
            break
    assert new and new != old and _alive(new), "看门狗没有拉起挂掉的调度器"

    down = _run(root, "down", **env)
    assert down.returncode == 0, down.stdout + down.stderr
    assert "看门狗已停止" in down.stdout
    assert not (root / "logs" / "watchdog.pid").exists()
    assert not _read_pid(root)


def test_up_refuses_without_precious(tmp_path):
    """私有仓缺失默认拒绝启动：私有任务不能静默消失。"""
    root = _fake_deploy(tmp_path)
    shutil.rmtree(tmp_path / "precious-task")
    r = _run(root, "up", SD_WATCHDOG_INTERVAL="1")
    assert r.returncode != 0
    assert "找不到私有仓" in r.stderr


def test_modules_from_import_and_path_check(tmp_path):
    """换机迁移：SD_MODULES_FROM 导入旧机 modules.json，doctor 挑出坏路径/坏占位符。"""
    root = _fake_deploy(tmp_path)
    good_cwd = tmp_path / "work"
    good_cwd.mkdir()
    src = tmp_path / "old_modules.json"
    src.write_text(json.dumps({"version": 2, "modules": [
        {"key": "legacy_bad", "enabled": True, "cwd": "/no/such/dir",
         "cmd": "python3 x.py", "sched": {"mode": "daily", "at": "22:00"}},
        {"key": "legacy_ok", "enabled": True, "cwd": str(good_cwd),
         "cmd": "python3 build.py", "sched": {"mode": "daily", "at": "22:40"}},
        {"key": "legacy_off", "enabled": False, "cwd": "/nope",
         "cmd": "bash -c '{{WORKSPACE_A}}/x.py'"},
    ]}, ensure_ascii=False), encoding="utf-8")

    up = _run(root, "up", SD_MODULES_FROM=str(src), SD_DRY_RUN="1",
              SD_PRECIOUS_DIR=str(tmp_path / "precious-task"))
    assert up.returncode == 0, up.stdout + up.stderr
    assert "已从 SD_MODULES_FROM 导入" in up.stdout
    mods = json.loads((root / "modules.json").read_text())
    assert [m["key"] for m in mods["modules"]] == ["legacy_bad", "legacy_ok", "legacy_off"]

    d = json.loads(_run(root, "doctor", "--json",
                        SD_PRECIOUS_DIR=str(tmp_path / "precious-task")).stdout)
    # 只查启用的：坏的绝对 cwd 要报，禁用的不报
    assert d["modules_cfg"]["count"] == 3
    assert d["modules_cfg"]["bad_cwd"] == ["legacy_bad"]
    assert d["modules_cfg"]["unresolved"] == []

    # 已存在的 modules.json 不被覆盖
    up2 = _run(root, "up", SD_MODULES_FROM=str(src), SD_DRY_RUN="1",
               SD_PRECIOUS_DIR=str(tmp_path / "precious-task"))
    assert "忽略 SD_MODULES_FROM" in up2.stdout


def test_modules_from_rejects_bad_json(tmp_path):
    root = _fake_deploy(tmp_path)
    src = tmp_path / "bad.json"
    src.write_text("{not json", encoding="utf-8")
    r = _run(root, "up", SD_MODULES_FROM=str(src), SD_DRY_RUN="1",
             SD_PRECIOUS_DIR=str(tmp_path / "precious-task"))
    assert r.returncode != 0
    assert "SD_MODULES_FROM" in r.stderr
    assert not (root / "modules.json").exists()


def test_up_only_sets_runtime_filter(tmp_path):
    """up --only：只传运行期白名单，写进子进程环境与 state，不碰任何配置文件。"""
    root = _fake_deploy(tmp_path)
    env = {"SD_WATCHDOG_INTERVAL": "1",
           "SD_PRECIOUS_DIR": str(tmp_path / "precious-task")}
    mods_before = None

    up = _run(root, "up", "--only", " dummy_priv , dummy_priv,", **env)
    assert up.returncode == 0, up.stdout + up.stderr
    assert "本次只调度 dummy_priv" in up.stdout
    assert "运行期过滤，未改配置文件" in up.stdout

    start_env = json.loads((root / "start_env.json").read_text())
    assert start_env.get("SD_ONLY") == "dummy_priv"       # 去重、去空格、去空项
    st = json.loads((root / "state.json").read_text())
    assert st["scheduler"]["only"] == ["dummy_priv"]

    # 只影响调度，不动配置：modules.json 内容不变
    if mods_before is not None:
        assert json.loads((root / "modules.json").read_text()) == mods_before
    _run(root, "down", **env)


def test_up_only_restart_when_filter_changes(tmp_path):
    """已在跑但过滤条件不同 → up 先停再起；不带 --only 再 up 恢复全部。"""
    root = _fake_deploy(tmp_path)
    env = {"SD_WATCHDOG_INTERVAL": "1",
           "SD_PRECIOUS_DIR": str(tmp_path / "precious-task")}

    a = _run(root, "up", **env)
    assert a.returncode == 0, a.stdout + a.stderr
    pid_all = _read_pid(root)
    assert _alive(pid_all)

    b = _run(root, "up", "--only", "dummy_priv", **env)
    assert b.returncode == 0, b.stdout + b.stderr
    assert "过滤条件变化" in b.stdout
    pid_one = _read_pid(root)
    assert _alive(pid_one) and pid_one != pid_all
    assert json.loads((root / "start_env.json").read_text()).get("SD_ONLY") == "dummy_priv"

    c = _run(root, "up", **env)
    assert c.returncode == 0, c.stdout + c.stderr
    assert "过滤条件变化" in c.stdout
    pid_restored = _read_pid(root)
    assert _alive(pid_restored) and pid_restored != pid_one
    assert not json.loads((root / "start_env.json").read_text()).get("SD_ONLY")
    assert json.loads((root / "state.json").read_text())["scheduler"]["only"] == []

    _run(root, "down", **env)


def test_up_only_equals_form_and_no_api(tmp_path):
    root = _fake_deploy(tmp_path)
    env = {"SD_WATCHDOG_INTERVAL": "1",
           "SD_PRECIOUS_DIR": str(tmp_path / "precious-task")}
    up = _run(root, "up", "--only=dummy_priv", "--no-api", **env)
    assert up.returncode == 0, up.stdout + up.stderr
    assert "HTTP 控制台     = 关闭" in up.stdout
    assert "控制台  已关闭（纯后台调度" in up.stdout
    e = json.loads((root / "start_env.json").read_text())
    assert e.get("SD_ONLY") == "dummy_priv"
    assert e.get("SD_NO_API") == "1"
    _run(root, "down", **env)


def test_up_rejects_unknown_flag(tmp_path):
    root = _fake_deploy(tmp_path)
    r = _run(root, "up", "--bogus",
             SD_PRECIOUS_DIR=str(tmp_path / "precious-task"))
    assert r.returncode != 0
    assert "未知参数" in r.stderr
