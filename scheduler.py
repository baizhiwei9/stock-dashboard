#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scheduler.py — stock-dashboard 自带的任务调度器（不依赖 crontab）

一个 Python 进程管住所有模块：每个模块按 modules.json 里的 sched 自己算
下次该跑的时间，到点起子进程，跑完把状态写进 state.json。同时起一个轻量
HTTP 服务，让管理页面能查状态、启停模块、立刻跑一次、改板块显隐。

纯标准库，零 token。

命令：
  python3 scheduler.py start          后台常驻
  python3 scheduler.py stop           停止
  python3 scheduler.py restart        重启
  python3 scheduler.py status         打印状态表
  python3 scheduler.py fg             前台跑（调试，Ctrl-C 退出）
  python3 scheduler.py run <key>      立刻跑一次
  python3 scheduler.py on <key>       启用模块
  python3 scheduler.py off <key>      停用模块
  python3 scheduler.py serve          只起 HTTP 控制台，不跑调度

模块字段（modules.json）：
  key/title/desc/domains/cwd/cmd/timeout/enabled  必填
  sched: {"mode":"daily","at":"21:00","weekdays":[1,2,3,4,5]}   ISO：1=周一 … 7=周日
  depends: ["macro", ...]  这些模块在跑时先等它们跑完
"""

import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import hmac
from datetime import datetime, timedelta, time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from datahub_path import datahub_root, expand_module

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "modules.json")
SEC_PATH = os.path.join(HERE, "sections.json")
REG_PATH = os.path.join(HERE, "registry.json")
STATE_PATH = os.path.join(HERE, "state.json")
LOG_DIR = os.path.join(HERE, "logs")
PID_PATH = os.path.join(HERE, "scheduler.pid")
SELF_LOG = os.path.join(LOG_DIR, "scheduler.log")
INDEX_PATH = os.path.join(HERE, "index.html")
CASE_DIR = os.path.join(HERE, "case")
CASE_LIB = os.path.join(CASE_DIR, "_lib")
if CASE_LIB not in sys.path:
    sys.path.insert(0, CASE_LIB)

import app_secrets  # noqa: E402  （敏感配置统一入口，见 config/README.md）

TICK_SEC = 15          # 调度轮询间隔
GRACE_SEC = 6 * 3600   # 错过超过 6 小时就不补跑
DEFAULT_PORT = 8767    # 与 modules.json / README 保持一致，避免文档里三个端口打架

HB_PATH = os.path.join(LOG_DIR, "scheduler.hb")   # 心跳：每次 tick 写 epoch 秒
HB_STALE_SEC = 180     # 心跳超过 3 分钟未更新 → 视为僵死（看门狗据此重启，而非只看 pid）


def write_heartbeat():
    """写心跳文件（epoch 秒）。

    看门狗只读这一个文件判断「调度循环还在转」—— 进程活着但循环卡死（僵死）时，
    只看 pid 会漏判，心跳比 pid 更能代表真的在调度。
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(HB_PATH, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def heartbeat_age():
    """返回心跳年龄（秒）；无心跳文件（老版本或刚启动）返回 None。"""
    try:
        with open(HB_PATH, encoding="utf-8") as f:
            t = int((f.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return None
    if t <= 0:
        return None
    return max(0, int(time.time()) - t)

QUEUE_MAX_DEFAULT = 1   # 全局并发上限，默认 1＝一次只跑一个任务（modules.json 的 queue.max_parallel 可覆盖）
QUEUE_NAME_DEFAULT = "default"
ORPHANS = {}            # key -> pid：调度器被杀后仍存活的子进程，仍占队列名额，等它自己结束

LOCK = threading.RLock()
PROCS = {}             # key -> {proc, started, slot, logf}
_STOP = threading.Event()

# case 任务配置错误只在「首次发现」或「错误内容变了」时告警一次：
# _case_modules() 每个 tick（15s）和每次 /api/state 都会调，逐次告警会把
# task_health 刷成连败 5 次、几分钟内发一堆邮件，正好违反「单次失败静默」。
# 落盘一份：调度器重启后不会把同一个老错误再告一遍。
CFG_ALERT_PATH = os.path.join(HERE, "state_config_alerts.json")
_CONFIG_ALERTED = None   # key -> 已经告过警的错误文本（懒加载）
_CONFIG_CLEARED = set()  # 已经报过「配置恢复正常」的 key，避免反复清零


def _queue_conf():
    """读 modules.json 的 queue 配置。

    {"max_parallel": int, "groups": {组名: int},
     "pull_forward": bool, "pull_forward_from": "HH:MM", "pull_ahead_min": int}

    缺省即「全局串行」（max_parallel=1）+「16:00 后允许提前拉取」（最多提前 240 分钟）：
    队列空着且离下一个任务计划点还有很长的空档时，把那个任务提前拉进来串着跑，
    不硬等它自己的计划点；报告比计划点早交付也接受，只要当天跑掉。
    """
    q = (load_cfg() or {}).get("queue") or {}

    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    mx = _int(q.get("max_parallel"), QUEUE_MAX_DEFAULT)
    groups = {}
    for k, v in (q.get("groups") or {}).items():
        try:
            groups[str(k)] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    pf = q.get("pull_forward")
    pull_forward = True if pf is None else bool(pf)
    try:
        _hh, _mm = [int(x) for x in str(q.get("pull_forward_from") or "16:00").split(":")]
        pull_from = dtime(_hh, _mm)
    except (ValueError, TypeError):
        pull_from = dtime(16, 0)
    ahead = max(0, _int(q.get("pull_ahead_min"), 240))
    return {"max_parallel": max(0, mx), "groups": groups,
            "pull_forward": pull_forward, "pull_from": pull_from,
            "pull_ahead_sec": ahead * 60}


def _not_before_time(m):
    """任务声明的「数据最早几点就绪」（HH:MM）；没配返回 None。提前拉取时用作下限。"""
    s = str((m or {}).get("not_before") or "").strip()
    if not s:
        return None
    try:
        hh, mm = [int(x) for x in s.split(":")]
        return dtime(hh, mm)
    except (ValueError, TypeError):
        return None


def _next_run_after(sched, now, last_slot):
    """下一次该跑的时间点，跳过已经跑过的计划点（提前跑完后不能还显示今天那个点）。"""
    probe = now
    for _ in range(64):
        nx = compute_next(sched, probe)
        if nx is None:
            return None
        if nx.strftime("%Y-%m-%d %H:%M") != (last_slot or ""):
            return nx
        probe = nx + timedelta(minutes=1)
    return None


def _queue_of(m):
    """任务所属队列（默认 default）。"""
    return str((m or {}).get("queue") or QUEUE_NAME_DEFAULT)


def _queue_used(mods):
    """当前占用：运行中的子进程 + 仍存活的孤儿（调度器被杀前起的那批）。返回 (总数, {组: 数})。"""
    used, total = {}, 0
    for key in list(ORPHANS.keys()):
        if not _pid_alive(ORPHANS[key]):
            ORPHANS.pop(key, None)
            continue
        g = _queue_of(mods.get(key))
        used[g] = used.get(g, 0) + 1
        total += 1
    for key in list(PROCS.keys()):
        g = _queue_of(mods.get(key))
        used[g] = used.get(g, 0) + 1
        total += 1
    return total, used


def _queue_room(g, mods, conf):
    """该组还有没有名额：全局上限与组上限都得有空位。"""
    total, used = _queue_used(mods)
    if total >= conf["max_parallel"]:
        return False
    return used.get(g, 0) < conf["groups"].get(g, conf["max_parallel"])


def _queue_blocker(g, mods):
    """排队时说明在等谁：先同组，再全局；孤儿进程也点名（有任务没销毁就等它）。"""
    same = [k for k in PROCS if _queue_of(mods.get(k)) == g]
    if same:
        return "/".join(same)
    alive = [k for k, pid in ORPHANS.items() if _pid_alive(pid) and _queue_of(mods.get(k)) == g]
    if alive:
        return "/".join(alive) + "（孤儿进程）"
    rest = list(PROCS.keys()) or [k for k, pid in ORPHANS.items() if _pid_alive(pid)]
    return "/".join(rest) if rest else "前一个任务"


def _queue_ahead(g, key, cands):
    """排队序号：本组里排在自己前面的候选数 + 1（同组串行时的位置）。"""
    n = 1
    for c in cands:
        if c["key"] == key:
            break
        if _queue_of(c["mod"]) == g:
            n += 1
    return n


# ────────────────────────────── 基础读写 ──────────────────────────────
def log(msg):
    """只写文件 —— stdout 本身可能就重定向到同一个文件，避免重复。"""
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(SELF_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    """原子写；云盘偶尔不让建 .tmp，退化成直接写。"""
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)


def load_cfg():
    return _read_json(CFG_PATH, {"modules": []})


def save_cfg(cfg):
    _write_json(CFG_PATH, cfg)


def load_state():
    st = _read_json(STATE_PATH, {})
    st.setdefault("scheduler", {})
    st.setdefault("modules", {})
    return st


def save_state(st):
    _write_json(STATE_PATH, st)


def api_conf():
    """调度器 HTTP 配置。敏感项（password / registry_key）只从 config/secrets.json
    或环境变量取，modules.json 里不再保存明文。"""
    conf = dict(load_cfg().get("api") or {})
    for key, dotted in (("password", "api.password"),
                        ("registry_key", "api.registry_key")):
        try:
            conf[key] = app_secrets.get(dotted)
        except app_secrets.SecretMissing:
            conf[key] = ""
    return conf


def _secrets_conf():
    """统一凭据（~/.config/stock-dashboard/secrets.json 的 scheduler 段）。

    敏感值不再写进 modules.json —— 那个文件是入库的，历史上管理密码就是
    明文 "123456"。modules.json 里若仍写了 password/registry_key，作为兼容
    覆盖保留（不推荐），但不再是唯一来源。
    """
    try:
        import app_secrets
    except ImportError:
        _lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "case", "_lib")
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        import app_secrets
    return app_secrets.section("scheduler")


def load_registry():
    r = _read_json(REG_PATH, {})
    r.setdefault("workers", [])
    return r


def save_registry(r):
    _write_json(REG_PATH, r)


def _case_modules():
    """case/*/task.json 的任务（dumate 分支的任务壳）。

    坏配置**不丢弃**：改成 enabled=False 留在清单里，并在调度台/看板标「配置错误」。
    任务失败可以静默（连败 5 天才说话），但任务从调度里悄悄消失不行 —— 那是
    「任务没了」不是「任务失败」，必须响。
    """
    try:
        if CASE_LIB not in sys.path:
            sys.path.insert(0, CASE_LIB)
        import case_registry
        mods = []
        for m in case_registry.as_modules(CASE_DIR):
            errs = m.get("_errors") or []
            if errs:
                m = dict(m)
                m["enabled"] = False
                m["_config_error"] = "；".join(errs)
            _config_alert(m.get("key"), errs)
            mods.append(m)
        return mods
    except Exception as e:                                  # noqa: BLE001
        log("装载 case 任务失败：%s" % e)
        return []


def _config_alert(key, errs):
    """case 任务配置错误的告警：首次发现就响，改好后把计数清零。

    走 data_hub.tech.health，跟其他任务共用一张 task_health 表、同一套
    提醒通道（不另建 crontab 检查）。这里用 threshold=1：配置错误的任务
    根本不会跑，等不到「连败 5 次」，而且任务从调度里消失是「任务没了」，
    不是「任务失败」，必须立刻说话。

    去重：同一个 key 同一段错误只告一次；错误文本变了（改了但还没改对）
    再告一次；恢复正常时记一次 ok=True 把计数清零。去重表落盘一份，
    调度器重启不会把老错误再告一遍。
    """
    global _CONFIG_ALERTED
    if not key:
        return
    msg = "；".join(errs or [])
    # tick 线程和 HTTP 线程都会走到这里，去重表必须在锁里改，否则同一个错误
    # 可能被两个线程各记一次（连败 +2）。
    with LOCK:
        if _CONFIG_ALERTED is None:
            loaded = _read_json(CFG_ALERT_PATH, {})
            _CONFIG_ALERTED = loaded if isinstance(loaded, dict) else {}
        if msg:
            if _CONFIG_ALERTED.get(key) == msg:
                return
            _CONFIG_ALERTED[key] = msg
            _CONFIG_CLEARED.discard(key)
            log("case 任务 %s 配置错误，已停用（不是静默跳过）：%s" % (key, msg))
            ok, threshold, err = False, 1, "配置错误: %s" % msg
        else:
            if key not in _CONFIG_ALERTED:
                return
            _CONFIG_ALERTED.pop(key, None)
            if key in _CONFIG_CLEARED:
                _save_cfg_alerts()
                return
            _CONFIG_CLEARED.add(key)
            log("case 任务 %s 配置恢复正常，健康计数清零" % key)
            ok, threshold, err = True, 1, None
        _save_cfg_alerts()

    def _run():
        # record() 在触发阈值时会同步发 SMTP（最坏 60s），不能卡住 tick。
        try:
            dh = datahub_root()
            if dh not in sys.path:
                sys.path.insert(0, dh)
            from data_hub.tech.health import record
            r = record(key, ok=ok, error=err, threshold=threshold)
            if not ok:
                log("case 任务 %s 配置错误已记入 task_health（连败 %s，%s）"
                    % (key, r.get("fails"),
                       "已发提醒" if r.get("alerted") else "未发提醒"))
        except Exception as e:                              # noqa: BLE001
            log("case 任务 %s 配置错误告警记录失败: %s: %s"
                % (key, type(e).__name__, e))
    threading.Thread(target=_run, daemon=True).start()


def _save_cfg_alerts():
    """把去重表落盘。写不进去只打日志 —— 最多下次重启多告一次，不能影响调度。"""
    try:
        _write_json(CFG_ALERT_PATH, _CONFIG_ALERTED or {})
    except Exception as e:                                  # noqa: BLE001
        log("配置错误告警去重表写入失败: %s: %s" % (type(e).__name__, e))


def all_modules():
    """内置 modules.json + 外部注册 registry.json + case 任务。key 唯一，内置优先。

    cwd/cmd 里的 {{DATAHUB_ROOT}} 在这里展开：modules.example.json 用占位而不是
    本机绝对路径，任务 cmd 里的 PYTHONPATH 与 sd_watchdog.sh / 各入口用的是同一个
    DataHub 根，不会出现「看门狗认为 datahub 在、子进程 import 失败」。
    """
    mods = []
    seen = set()
    root = datahub_root()
    for m in (load_cfg().get("modules") or []):
        if m.get("key") and m["key"] not in seen:
            m = expand_module(m, root)
            m["source"] = "builtin"
            mods.append(m)
            seen.add(m["key"])
    for w in (load_registry().get("workers") or []):
        k = w.get("key")
        if w.get("status") != "approved":
            continue
        if k and k not in seen:
            w = expand_module(w, root)
            w["source"] = "registered"
            mods.append(w)
            seen.add(k)
    for m in _case_modules():
        k = m.get("key")
        if k and k not in seen:
            m = expand_module(m, root)
            mods.append(m)
            seen.add(k)
    return mods


def _now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rand(n=8):
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def reg_key():
    """注册密钥：统一凭据的 api.registry_key（与 scheduler.registry_key 等价）。

    缺失时返回空串 —— 调用方按 fail-closed 处理，不再用 "123456" 或管理密码兜底。
    """
    return str(api_conf().get("registry_key") or "")


def migrate_legacy_api_secrets(verbose=True):
    """把 modules.json 里遗留的 api.password / api.registry_key 迁到统一凭据文件。

    历史包袱：这两个值曾以明文 "123456" 写在入库的 modules.json 里，随公开仓库外泄。
    现在源头是 ~/.config/stock-dashboard/secrets.json 的 scheduler 段；本函数在
    scheduler 启动时自动跑一次，把旧值搬过去再从 modules.json 删掉（先备份），
    这样正在跑的旧进程不断服、重启后仓库就干净了。

    返回 (迁走的字段列表, 说明)。没有可迁的就返回 ([], "")。
    """
    # 必须直接读 modules.json 原始值 —— api_conf() 只从凭据文件取，看不到旧字段
    raw_api = load_cfg().get("api") or {}
    legacy = {k: raw_api[k] for k in ("password", "registry_key")
              if isinstance(raw_api.get(k), str) and raw_api.get(k)}
    if not legacy:
        return [], ""

    try:
        import app_secrets
    except ImportError:
        _lib = os.path.join(HERE, "case", "_lib")
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        import app_secrets

    sec = app_secrets.section("scheduler")
    moved = []
    for k, v in legacy.items():
        # 统一凭据里已有就以其为准（那是安装时录入的），否则把旧值搬过去
        if not sec.get(k):
            app_secrets.set_value("scheduler", k, v)
            moved.append(k + "(已搬入凭据文件)")
        else:
            moved.append(k + "(凭据文件已有，丢弃旧值)")

    # 备份后从 modules.json 删掉
    try:
        import shutil
        import time as _t
        shutil.copy2(CFG_PATH, CFG_PATH + ".bak_" + _t.strftime("%Y%m%d_%H%M%S"))
    except OSError:
        pass
    cfg = load_cfg()
    api = cfg.get("api") or {}
    for k in legacy:
        api.pop(k, None)
    api["_note"] = ("password / registry_key 已移至 ~/.config/stock-dashboard/secrets.json 的 "
                    "scheduler 段（见 DEPLOY.md「敏感配置」）")
    cfg["api"] = api
    save_cfg(cfg)

    msg = "modules.json 的 %s 已迁移：%s" % (", ".join(legacy), "; ".join(moved))
    if verbose:
        print("[scheduler] " + msg)
    return list(legacy), msg




def find_worker(app_id=None, key=None):
    reg = load_registry()
    for w in (reg.get("workers") or []):
        if app_id and w.get("app_id") == app_id:
            return w, reg
        if key and w.get("key") == key:
            return w, reg
    return None, reg


def register_worker(body):
    """外部注册一个 Worker，落到 pending 等后台批准。返回 (worker, err)。"""
    key = str(body.get("key") or "").strip()
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]{1,31}$", key):
        return None, "key 必须字母开头，只能字母/数字/下划线，2-32 位"
    if not str(body.get("cmd") or "").strip():
        return None, "cmd 必填（这个 Worker 到点要执行的命令）"
    if any(m.get("key") == key for m in (load_cfg().get("modules") or [])):
        return None, "key 与内置模块重名，换一个"

    old, reg = find_worker(key=key)
    if old and old.get("status") == "approved":
        return None, "这个 key 已经批准过了，要改请先在后台注销"

    w = {
        "app_id": (old or {}).get("app_id") or ("wk_" + _rand(10)),
        "secret": (old or {}).get("secret") or ("sk_" + _rand(24)),
        "key": key,
        "title": str(body.get("title") or key).strip()[:64],
        "desc": str(body.get("desc") or "").strip()[:300],
        "tab": str(body.get("tab") or "").strip()[:32],
        "domains": [str(x)[:32] for x in (body.get("domains") or [])][:12],
        "sec": str(body.get("sec") or "CN.MACRO")[:32],
        "metrics": (body.get("metrics") or [])[:24],
        "sched": body.get("sched") or {"mode": "daily", "at": "21:30",
                                       "weekdays": [1, 2, 3, 4, 5]},
        "cwd": str(body.get("cwd") or HERE),
        "cmd": str(body["cmd"]),
        "timeout": 1800,
        "enabled": True,
        "status": "pending",
        "section": body.get("section") or None,
        "registered_at": _now_s(),
        "reviewed_at": None,
    }
    try:
        w["timeout"] = int(body.get("timeout") or 1800)
    except (TypeError, ValueError):
        return None, "timeout 要写秒数（整数），例如 1800"
    # 校验 sched
    if not isinstance(w["sched"], dict):
        return None, "sched 要写成对象，例如 {\"at\":\"21:30\",\"weekdays\":[1,2,3,4,5]}"
    try:
        hh, mm = [int(x) for x in str(w["sched"].get("at", "")).split(":")]
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except (ValueError, TypeError):
        return None, "sched.at 要写成 HH:MM，例如 21:30"
    wds = w["sched"].get("weekdays")
    if wds is not None:
        try:
            wds = [int(x) for x in wds]
        except (TypeError, ValueError):
            return None, "sched.weekdays 要是 1-7 的整数数组"
        if not wds or any(not (1 <= x <= 7) for x in wds):
            return None, "sched.weekdays 要是 1-7 的整数数组"
        w["sched"]["weekdays"] = sorted(set(wds))

    reg["workers"] = [x for x in (reg.get("workers") or []) if x.get("key") != key]
    reg["workers"].append(w)
    save_registry(reg)
    log("收到注册申请：%s（%s）→ 待批准" % (key, w["app_id"]))
    return w, None


def review_worker(app_id, action):
    """批准 / 拒绝 / 删除。返回 (worker, err)。"""
    w, reg = find_worker(app_id=app_id)
    if not w:
        return None, "没有这个 app_id"
    if action == "delete":
        reg["workers"] = [x for x in reg["workers"] if x.get("app_id") != app_id]
        save_registry(reg)
        log("后台删除 Worker：%s（%s）" % (w.get("key"), app_id))
        return w, None
    if action == "approve":
        w["status"] = "approved"
        w["enabled"] = True
        w["reviewed_at"] = _now_s()
        # 批准时才把板块写进 sections.json
        _ensure_section(w)
    elif action == "reject":
        w["status"] = "rejected"
        w["enabled"] = False
        w["reviewed_at"] = _now_s()
    else:
        return None, "action 只能是 approve / reject / delete"
    save_registry(reg)
    log("后台%s Worker：%s（%s）" % ({"approve": "批准", "reject": "拒绝"}[action],
                                    w.get("key"), app_id))
    return w, None


def _ensure_section(w):
    """把注册的 model 挂到它所属的大类 Tab 下；大类不存在就新建一个大类。"""
    sec = load_sections()
    sec.setdefault("sections", [])
    tab = w.get("tab") or ""
    model = {"key": w["key"], "title": w.get("title") or w["key"],
             "enabled": True, "worker": w.get("app_id"), "source": "registered"}
    tgt = next((x for x in sec["sections"] if x.get("key") == tab), None)
    if tgt is not None:
        tgt.setdefault("subs", [])
        tgt["subs"] = [x for x in tgt["subs"] if x.get("key") != w["key"]]
        tgt["subs"].append(model)
    else:
        keys = {x.get("key") for x in sec["sections"]}
        if w["key"] in keys:
            return
        new = {"key": w["key"], "num": "%02d" % (len(sec["sections"]) + 1),
               "title": w.get("title") or w["key"],
               "sub": w.get("desc") or "", "enabled": True, "subs": [],
               "source": "registered", "worker": w.get("app_id")}
        # 插在 modules 之前
        pos = next((i for i, x in enumerate(sec["sections"])
                    if x.get("key") == "modules"), len(sec["sections"]))
        sec["sections"].insert(pos, new)
    _write_json(SEC_PATH, sec)


def set_enabled(key, on):
    """改启用状态，写回它所在的那个文件。"""
    cfg = load_cfg()
    for m in (cfg.get("modules") or []):
        if m.get("key") == key:
            m["enabled"] = bool(on)
            save_cfg(cfg)
            return True
    reg = load_registry()
    for w in (reg.get("workers") or []):
        if w.get("key") == key:
            w["enabled"] = bool(on)
            save_registry(reg)
            return True
    return False


# ────────────────────────────── 时间计算 ──────────────────────────────
def _at_time(sched, day):
    try:
        hh, mm = [int(x) for x in str(sched.get("at", "")).split(":")]
    except (ValueError, AttributeError):
        return None
    return datetime.combine(day, dtime(hh, mm))


def _weekdays(sched):
    w = sched.get("weekdays")
    return set(w) if w else {1, 2, 3, 4, 5}


def _day_ok(sched, day):
    """这一天是否该跑：monthly 看每月日期，其余看 ISO 星期。"""
    if (sched or {}).get("mode") == "monthly":
        ds = sched.get("days") or []
        try:
            return day.day in {int(x) for x in ds}
        except (TypeError, ValueError):
            return False
    return day.isoweekday() in _weekdays(sched or {})


def _span(sched, back=False):
    """回看 / 前瞻的天数。

    monthly 的前瞻不能按 31 天算：例如 days=[31]，4 月 1 日往后找，
    下一个 31 号是 5 月 31 日（60 天后）。40 天会让 compute_next 直接返回
    None，管理页的「下次」永远是 —。回看 40 天足够覆盖上一个月的 31 号。
    """
    if (sched or {}).get("mode") == "monthly":
        return 40 if back else 62
    return 9 if back else 10


def compute_slot(sched, now):
    """now 之前（含）最近一个应跑时间点；没有就 None。"""
    if not sched.get("at"):
        return None
    for d in range(0, _span(sched, back=True)):
        day = (now - timedelta(days=d)).date()
        if not _day_ok(sched, day):
            continue
        cand = _at_time(sched, day)
        if cand and cand <= now:
            return cand
    return None


def compute_next(sched, now):
    """严格晚于 now 的下一个应跑时间点。"""
    if not sched.get("at"):
        return None
    for d in range(0, _span(sched)):
        day = (now + timedelta(days=d)).date()
        if not _day_ok(sched, day):
            continue
        cand = _at_time(sched, day)
        if cand and cand > now:
            return cand
    return None


# ────────────────────────────── 进程管理 ──────────────────────────────
def _kill_tree(p):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.terminate()
        except OSError:
            pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _finish(key, rc, status, st):
    with LOCK:
        rec = PROCS.pop(key, None)
    if not rec:
        return
    dur = (datetime.now() - rec["started"]).total_seconds()
    try:
        rec["logf"].write(("[%s] 结束 %s exit=%s 耗时 %.1fs\n" %
                           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            key, rc, dur)).encode())
        rec["logf"].close()
    except OSError:
        pass
    with LOCK:
        ms = st["modules"].setdefault(key, {})
        ms.pop("queued", None)
        ms.pop("orphan_pid", None)
        ms.update({
            "last_status": status, "exit": rc,
            "duration": round(dur, 1), "running": False, "pid": None,
            "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_state(st)
    log("模块结束 %s status=%s exit=%s 耗时 %.1fs" % (key, status, rc, dur))
    # 主动停用不是失败：管理页面 toggle 关掉正在跑的任务会走 _finish(-9,
    # "stopped")，照记就会算成连败 +1，手动停 5 次就收到"连续失败 5 次"的
    # 邮件，还污染真实连败计数。timeout 是任务自己没跑完，仍算失败。
    if status != "stopped":
        _health_async(key, rc, rec.get("mod") or {})


def _datahub_dir():
    """DataHub 根目录 —— 统一走 datahub_path.datahub_root()，不再自己解析。

    原来这里单独认 DATAHUB_DIR / DATA_HUB_PATH 再兜底 /opt/coze/...，与
    datahub_path 的两套口径在两条合法部署路径上分叉：
      · DEPLOY.md 推荐的「同级 checkout、不设环境变量」→ datahub_root() 能解析到
        ../datahub-multi-tier，这里却只认环境变量，落到兜底目录 → 同级部署下
        import data_hub.tech.health 必然 ModuleNotFoundError，连败计数静默丢失；
      · 用 EDB_REPO 指定 DataHub 时这里根本不认这个变量。
    scheduler.py 顶层已经 from datahub_path import datahub_root（all_modules() 就在用），
    直接复用同一结果，两边永远指向同一个目录。
    """
    return datahub_root()


def _health_preload():
    """启动时预热 data_hub.tech.health 的 import。

    这个 import 会一路拉进 data_hub/__init__.py → collectors.misc →
    option_metrics_history，后者在模块顶层执行 _migrate_legacy_cache_once()，
    在 FUSE 云盘上扫 os.path 实测要 20 秒上下；第二次命中 sys.modules 是 0 秒。
    放在启动阶段预热，第一次记账就不用干等；预热自己也丢后台，不拖慢启动。
    """
    def _warm():
        dh = _datahub_dir()
        try:
            if dh not in sys.path:
                sys.path.insert(0, dh)
            t0 = time.time()
            from data_hub.tech import health             # noqa: F401
            log("健康度模块预热完成（DataHub 根 %s，%.1fs）" % (dh, time.time() - t0))
        except Exception as e:                           # noqa: BLE001
            # 预热失败要能看出「是哪个根没找到 data_hub」，否则下次只是记账继续静默失败
            log("健康度模块预热失败（DataHub 根 %s）：%s: %s"
                % (dh, type(e).__name__, e))
    threading.Thread(target=_warm, daemon=True).start()


def _health_async(key, rc, m, err=None, force=False):
    """health 记账丢到后台线程 —— record() 在第 5 次失败会同步发 SMTP。

    send_email 的 SMTP_SSL timeout=30s、subprocess 兜底 timeout=60s，同步跑
    最坏能把 tick 卡一分多钟，后面的模块全部顺延。tick 本身串行，并发量很小，
    一个 daemon 线程足够；进程退出时最坏丢一次记账，下次成功即清零，可接受。
    """
    def _run():
        try:
            _health_record(key, rc, m, err, force)
        except Exception:                                   # noqa: BLE001
            pass
    threading.Thread(target=_run, daemon=True).start()


def _health_record(key, rc, m, err=None, force=False):
    """把模块结果记进 task_health，与 crontab 侧的 health_run 共用同一张表。

    scheduler 是 modules.json + registry.json + case/ 三类任务的统一执行者，
    在这里收口比给每条 cmd 套 health_run 外壳更彻底：一处覆盖全部模块，
    手动 run / HTTP 触发也不会漏，case/ 的 task.json 契约也不用动。

    三条边界：
      ① cmd 里已经带 health_run 的跳过 —— 否则同一次执行会记两遍，
         fails 双倍计数，连败 5 天的阈值等于被砍半。用子串判断够用：
         约定 health_run 只作为命令里的包装器出现，没有别的模块名带它。
      ② 主动停用（status=stopped）由 _finish 侧跳过，不进这里。
      ③ 记不了健康度绝不能影响调度本身：异常只打日志并返回原因（不静默吞掉），
         调用方/排障时能看出「是 DataHub 根没解析对，还是记账本身失败」。
    """
    # force=True 用在 spawn 失败：那时 health_run 包装器根本没启动，不可能
    # 自己记过账，跳过就等于这类模块永远静默。
    if not force and "health_run" in (m.get("cmd") or ""):
        return
    dh = _datahub_dir()
    try:
        if dh not in sys.path:
            sys.path.insert(0, dh)
        from data_hub.tech.health import record
        r = record(key, ok=(rc == 0),
                   error=err or (None if rc == 0 else "退出码 %s" % rc))
        if rc == 0:
            log("健康度 %s 成功，失败计数清零" % key)
        else:
            log("健康度 %s 失败，连败 %s/%s%s" % (
                key, r.get("fails"), r.get("threshold"),
                "，已发提醒邮件" if r.get("alerted") else "，未到提醒阈值"))
        return r
    except Exception as e:
        # 返回原因而不是 None：早些时候这里只 log 一行就 return None，日志里也看不出
        # 是根目录解析错（ModuleNotFoundError）还是库侧真出错，连败计数丢了没人发现。
        reason = "%s: %s" % (type(e).__name__, e)
        log("健康度记录失败 %s（DataHub 根 %s）：%s"
            % (key, dh or "<空>", reason))
        return {"ok": False, "recorded": False, "reason": reason, "datahub_root": dh}


def spawn(key, m, slot, st, pulled=False):
    """起一个模块子进程。已在跑就返回 False（防 tick 与 HTTP 线程双起）。

    pulled=True 表示「提前执行」：slot 仍是该任务当天的计划点（`last_slot` 记它，当天不重复跑），
    只是实际启动时间早于计划点 —— 报告早于计划点交付。
    """
    _spawn_err = None
    with LOCK:
        if key in PROCS:
            return False
        # 配置里的通知/数据凭据导出成环境变量，兼容只认 FEISHU_* / SMTP_* /
        # TUSHARE_TOKEN 的老脚本；api.password / registry_key 不导出。
        try:
            app_secrets.export_env()
        except Exception as e:                                # noqa: BLE001
            log("敏感配置导出失败（任务照跑，通道可能取不到凭据）：%s: %s"
                % (type(e).__name__, e))
        os.makedirs(LOG_DIR, exist_ok=True)
        logp = os.path.join(LOG_DIR, "%s.log" % key)
        f = open(logp, "ab")
        now = datetime.now()
        head = ("\n" + "=" * 72 +
                "\n[%s] 启动 %s（计划点 %s%s）\n[cmd] %s\n[cwd] %s\n" %
                (now.strftime("%Y-%m-%d %H:%M:%S"), key,
                 slot.strftime("%Y-%m-%d %H:%M"),
                 "，提前执行" if pulled else "", m.get("cmd"),
                 m.get("cwd") or HERE))
        f.write(head.encode())
        f.flush()
        try:
            p = subprocess.Popen(
                m["cmd"], shell=True, executable="/bin/bash",
                cwd=m.get("cwd") or HERE,
                stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True)
        except OSError as e:
            f.write(("启动失败: %s\n" % e).encode())
            f.close()
            st["modules"].setdefault(key, {}).update({
                # last_slot 必须写：tick 的 slot 去重就靠它。不写的话每个
                # 轮询（15s）都会重新 spawn 一次、每次失败都记一次 health，
                # 6h 宽限内最坏 1440 次，threshold=5 时等于 288 封提醒邮件，
                # 第一次 75 秒就到 —— 直接违反"单次失败静默"。
                "last_slot": slot.strftime("%Y-%m-%d %H:%M"),
                "last_status": "fail", "running": False,
                "note": "启动失败: %s" % e})
            save_state(st)
            log("启动失败 %s: %s" % (key, e))
            _spawn_err = e
        else:
            PROCS[key] = {"proc": p, "started": now, "slot": slot, "logf": f,
                          "mod": m}             # mod 供 _finish 记账时判断模块定义
            ORPHANS.pop(key, None)          # 该任务已由本进程接管，孤儿登记作废
            ms = st["modules"].setdefault(key, {})
            ms.pop("queued", None)          # 起跑即退出排队
            ms.pop("orphan_pid", None)
            ms.update({
                "last_slot": slot.strftime("%Y-%m-%d %H:%M"),
                "last_run": now.strftime("%Y-%m-%d %H:%M:%S"),
                "last_status": "running", "running": True, "note": "",
                "pid": p.pid, "pulled_forward": bool(pulled),
            })
            save_state(st)
    if _spawn_err is not None:
        # 起不来 = 每次都失败，必须记进 task_health。cwd 不存在 / 权限错这类
        # "永远起不来"的任务根本进不了 _finish，不在这里补就是永久静默 ——
        # 正好是"失败静默但连败 5 次要说"的反面。放在 LOCK 外：发邮件不能卡 tick。
        # force=True：cmd 里带 health_run 的模块，包装器压根没跑起来，
        # 不可能自己记过账，这里必须记，否则这批模块永久静默。
        _health_async(key, -1, m, err="启动失败: %s" % _spawn_err, force=True)
        return False
    log("启动 %s pid=%d（计划点 %s）" % (key, p.pid, slot.strftime("%Y-%m-%d %H:%M")))
    return True


def tick():
    now = datetime.now()
    st = load_state()
    mods = {m["key"]: m for m in all_modules() if m.get("key")}

    # ① 回收已结束 / 超时的进程
    for key in list(PROCS.keys()):
        rec = PROCS.get(key)          # HTTP 线程可能刚 _finish 掉
        if not rec:
            continue
        p = rec["proc"]
        rc = p.poll()
        if rc is None:
            to = float((mods.get(key) or {}).get("timeout") or 3600)
            if (now - rec["started"]).total_seconds() > to:
                _kill_tree(p)
                _finish(key, -1, "timeout", st)
            continue
        _finish(key, rc, "ok" if rc == 0 else "fail", st)

    # ② 到点的起新任务：串行队列 —— 名额没空就排队等；队列空着时把后面的任务提前拉进来串着跑
    st.setdefault("modules", {})
    for _ms in st["modules"].values():
        _ms.pop("queued", None)              # 每 tick 重算排队状态，避免残留
    conf = _queue_conf()
    pull_on = conf["pull_forward"] and now.time() >= conf["pull_from"]
    cands = []
    for idx, (key, m) in enumerate(mods.items()):
        if not m.get("enabled", True) or key in PROCS:
            continue
        sched = m.get("sched") or {}
        slot, pulled = compute_slot(sched, now), False
        ms = st["modules"].setdefault(key, {})
        # compute_slot 会回看若干天：今天的点还没到时会返回「昨天」那个点。
        # 昨天的点既不该补跑、也不该天天刷「超宽限」提醒 —— 直接当作没有待跑点。
        if slot is not None and (now - slot).total_seconds() > GRACE_SEC:
            if slot.date() == now.date():
                slotkey = slot.strftime("%Y-%m-%d %H:%M")
                if ms.get("missed_slot") != slotkey:   # 今天到点却超宽限：只记一次
                    ms["missed_slot"] = slotkey
                    ms["note"] = "超过宽限未执行（计划 %s，一直没轮到空档）" % slotkey
                    log("跳过 %s：计划点 %s 超出 %.0f 小时宽限" % (key, slotkey, GRACE_SEC / 3600))
                continue
            slot = None
        if slot is None:
            # 还没到点：空档够长就把后面的任务提前拉进来（不卡点，只求当天跑掉）
            if not pull_on:
                continue
            nx = compute_next(sched, now)
            if nx is None or (nx - now).total_seconds() > conf["pull_ahead_sec"]:
                continue
            nb = _not_before_time(m)
            if nb is not None and now.time() < nb:
                continue                     # 数据还没就绪，不许提前
            slot, pulled = nx, True
        slotkey = slot.strftime("%Y-%m-%d %H:%M")
        if ms.get("last_slot") == slotkey:
            continue
        deps = m.get("depends") or []
        waiting = [d for d in deps if d in PROCS]
        if waiting:
            ms["note"] = "等依赖 %s 跑完" % "/".join(waiting)
            continue
        cands.append({"slot": slot, "idx": idx, "key": key, "mod": m, "pulled": pulled})

    # 先到先跑：按计划点，再按配置顺序；名额未满才起，其余标排队等下一个 tick 接着试
    cands.sort(key=lambda c: (c["slot"], c["idx"]))
    for c in cands:
        key, m = c["key"], c["mod"]
        g = _queue_of(m)
        if not _queue_room(g, mods, conf):
            ms = st["modules"].setdefault(key, {})
            ms["queued"] = True
            ms["queue"] = g
            ms["note"] = "排队第 %d（等 %s 跑完）" % (
                _queue_ahead(g, key, cands), _queue_blocker(g, mods))
            continue
        spawn(key, m, c["slot"], st, pulled=c["pulled"])

    # ③ 刷新状态
    for key, m in mods.items():
        ms = st["modules"].setdefault(key, {})
        ms["enabled"] = bool(m.get("enabled", True))
        nx = _next_run_after(m.get("sched") or {}, now, ms.get("last_slot"))
        ms["next_run"] = nx.strftime("%Y-%m-%d %H:%M") if nx else "—"
        rec = PROCS.get(key)
        ms["running"] = rec is not None
        if rec:
            ms["elapsed"] = round((now - rec["started"]).total_seconds())
    _total, _used = _queue_used(mods)
    st["scheduler"].update({
        "pid": os.getpid(), "tick": now.strftime("%Y-%m-%d %H:%M:%S"),
        "running": len(PROCS), "mode": st["scheduler"].get("mode", "daemon"),
        "queue": {"max_parallel": conf["max_parallel"], "groups": conf["groups"],
                  "total_used": _total, "used": _used,
                  "orphans": {k: p for k, p in ORPHANS.items() if _pid_alive(p)}},
    })
    save_state(st)


def _recover_interrupted():
    """启动时清理上一个进程遗留的 running 状态。

    子进程句柄只存在于内存（PROCS），调度器被 kill 后新进程无法回收它们：
    state.json 会留下 running=true 且 last_slot 已写，于是该任务当天既不重跑、
    也不报错 —— 典型的静默失败。这里统一标记 interrupted，让管理页/日报看得见。

    自动补跑默认**关闭**：报告类任务重跑会重复推送飞书/邮件，需人工判断；
    确认任务幂等后可把 state.json 的 scheduler.auto_retry_interrupted 置 true，
    下次启动会清空对应 last_slot，在 6 小时宽限内重新命中。
    """
    st = load_state()
    mods = st.get("modules") or {}
    bad = [k for k, ms in mods.items() if (ms or {}).get("running")]
    if not bad:
        return
    orphans = []
    for k in bad:
        try:
            pid = int(mods[k].get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        alive = bool(pid) and _pid_alive(pid)
        if alive:
            orphans.append(k)
            ORPHANS[k] = pid        # 未销毁的子进程仍占队列名额，等它自己结束再放行
        mods[k].update({
            "running": False,
            "last_status": "interrupted",
            "orphan_pid": pid if alive else None,
            "note": ("上次调度器退出时该任务仍在运行；子进程 pid=%d 仍存活，继续占队列名额，等它结束后再放行" % pid)
                    if alive else "上次调度器退出时该任务仍在运行（结果未知，已标记中断）",
        })
    sch = st.setdefault("scheduler", {})
    sch["recovered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sch["recovered_tasks"] = bad
    if sch.get("auto_retry_interrupted"):
        for k in bad:
            mods[k]["last_slot"] = ""
        log("恢复：%d 个任务上次被中断（%s），已标记 interrupted 并清空 last_slot 以便补跑"
            % (len(bad), ",".join(bad)))
    else:
        log("恢复：%d 个任务上次被中断（%s），已标记 interrupted（auto_retry_interrupted=false，不自动补跑）"
            % (len(bad), ",".join(bad)))
    save_state(st)


def loop():
    write_heartbeat()          # 先落心跳：看门狗据此判断「循环在转」，与 pid 无关
    try:
        _recover_interrupted()
    except Exception:                                     # noqa: BLE001
        log("恢复中断任务失败：\n" + traceback.format_exc())
    log("调度器启动 pid=%d，轮询 %ds" % (os.getpid(), TICK_SEC))
    while not _STOP.is_set():
        try:
            tick()
        except Exception:                                    # noqa: BLE001
            log("tick 异常：\n" + traceback.format_exc())
        write_heartbeat()      # tick 抛异常也要续心跳：僵死判定的是「循环卡住」，不是「任务失败」
        _STOP.wait(TICK_SEC)
    log("调度器退出")


# ────────────────────────────── HTTP 控制台 ──────────────────────────────
def _json_resp(h, obj, code=200):
    """写 JSON 响应；浏览器提前断开（BrokenPipe）不算错误。"""
    body = json.dumps(obj, ensure_ascii=False).encode()
    try:
        _json_write(h, body, code)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _json_write(h, body, code):
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Access-Control-Allow-Headers", "Content-Type, X-SD-PW")
    h.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    h.end_headers()
    h.wfile.write(body)


def _kick_build():
    """Worker 批准后触发一次重建发布，让新板块上线。"""
    try:
        b = next((m for m in all_modules() if m.get("key") == "build"), None)
        if not b or not b.get("enabled", True):
            return False
        if "build" in PROCS:
            return False
        spawn("build", b, datetime.now(), load_state())
        log("已触发重建发布（Worker 变更）")
        return True
    except Exception:                                        # noqa: BLE001
        log("触发重建失败：" + traceback.format_exc())
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "sd-scheduler/1.0"

    def log_message(self, fmt, *args):      # 静音访问日志
        pass

    # ── 工具 ──
    def _pw_ok(self):
        """管理密码校验。没配密码时一律拒绝（fail-closed），避免裸奔。"""
        want = str(api_conf().get("password") or "")
        if not want:
            return False
        got = str(self.headers.get("X-SD-PW") or "")
        return hmac.compare_digest(got, want)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (ValueError, OSError):
            return {}

    def _snapshot(self):
        st = load_state()
        mods = []
        for m in all_modules():
            k = m.get("key")
            ms = st["modules"].get(k) or {}
            mods.append({
                "key": k, "title": m.get("title"), "desc": m.get("desc"),
                "source": m.get("source") or "builtin",
                "app_id": m.get("app_id"),
                "status": m.get("status") or "builtin",
                "domains": m.get("domains") or [],
                "sched": m.get("sched") or {},
                "enabled": bool(m.get("enabled", True)),
                "timeout": m.get("timeout"),
                "depends": m.get("depends") or [],
                "config_error": m.get("_config_error") or "",
                "running": k in PROCS,
                "next_run": ms.get("next_run"),
                "last_run": ms.get("last_run"),
                "last_status": ms.get("last_status"),
                "duration": ms.get("duration"),
                "exit": ms.get("exit"),
                "elapsed": ms.get("elapsed"),
                "note": ms.get("note") or "",
            })
        return {"now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scheduler": st.get("scheduler") or {},
                "modules": mods,
                "sections": (load_sections().get("sections") or [])}

    # ── GET ──
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                return _json_resp(self, self._snapshot())
            if path == "/api/sections":
                sec = load_sections()
                # 这个接口不鉴权，绝不能把 sections.json 里的管理密码带出去
                sec.pop("password", None)
                return _json_resp(self, sec)
            if path == "/api/registry":
                if not self._pw_ok():
                    return _json_resp(self, {"error": "密码不对"}, 401)
                r = load_registry()
                pend = [w for w in r.get("workers", []) if w.get("status") == "pending"]
                return _json_resp(self, {
                    "ok": True,
                    "registry_key": reg_key(),
                    "endpoint": "/api/register",
                    "workers": r.get("workers", []),
                    "pending": len(pend),
                })
            m = re.match(r"^/api/log/([A-Za-z0-9_]+)$", path)
            if m:
                if not self._pw_ok():
                    return _json_resp(self, {"error": "密码不对"}, 401)
                key = m.group(1)
                fp = os.path.join(LOG_DIR, "%s.log" % key)
                try:
                    with open(fp, "rb") as f:
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        f.seek(max(0, size - 8000))
                        txt = f.read().decode("utf-8", "replace")
                except OSError:
                    txt = "（还没有日志）"
                return _json_resp(self, {"key": key, "log": txt})
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path == "/health":
                return _json_resp(self, {"ok": True, "pid": os.getpid()})
            _json_resp(self, {"error": "not found"}, 404)
        except Exception:                                    # noqa: BLE001
            _json_resp(self, {"error": traceback.format_exc()}, 500)

    def _serve_index(self):
        try:
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
        except OSError:
            return _json_resp(self, {"error": "index.html 还没生成，先跑一次 build"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _key_ok(self):
        """外部注册用 X-SD-KEY（或 ?key=）对注册密钥，不要求管理密码。

        没配密钥时一律拒绝（fail-closed），避免空密钥裸奔。
        """
        want = reg_key()
        if not want:
            return False
        got = (self.headers.get("X-SD-KEY") or "").strip()
        if not got:
            q = parse_qs(urlparse(self.path).query)
            got = (q.get("key") or [""])[0].strip()
        return hmac.compare_digest(got, want)

    # ── POST ──
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        # 外部注册只认注册密钥
        if path == "/api/register":
            if not self._key_ok():
                return _json_resp(self, {"error": "注册密钥不对"}, 401)
            w, err = register_worker(body)
            if err:
                return _json_resp(self, {"error": err}, 400)
            return _json_resp(self, {
                "ok": True, "status": "pending",
                "app_id": w["app_id"], "secret": w["secret"], "key": w["key"],
                "message": "已收到申请，等后台批准后才会显示和调度",
            })
        if not self._pw_ok():
            return _json_resp(self, {"error": "密码不对"}, 401)
        try:
            m = re.match(r"^/api/worker/([A-Za-z0-9_]+)/(approve|reject|delete)$", path)
            if m:
                w, err = review_worker(m.group(1), m.group(2))
                if err:
                    return _json_resp(self, {"error": err}, 404)
                if m.group(2) == "approve":
                    # 异步触发，别让云盘读写把 HTTP 响应拖到 2-3 秒
                    threading.Thread(target=_kick_build, daemon=True).start()
                return _json_resp(self, {"ok": True, "app_id": w.get("app_id"),
                                         "key": w.get("key"), "status": w.get("status")})
            m = re.match(r"^/api/module/([A-Za-z0-9_]+)/(toggle|run)$", path)
            if m:
                return self._module_action(m.group(1), m.group(2), body)
            m = re.match(r"^/api/section/([A-Za-z0-9_]+)/toggle$", path)
            if m:
                return self._section_toggle(m.group(1), body)
            if path == "/api/shutdown":
                log("管理页面：请求停止调度器")
                _json_resp(self, {"ok": True, "bye": True})
                threading.Timer(1.0, _STOP.set).start()
                return
            if path == "/api/run_all":
                st = load_state()
                n = 0
                for mod in all_modules():
                    k = mod.get("key")
                    if k in PROCS or not mod.get("enabled", True):
                        continue
                    mod["_manual"] = True
                    spawn(k, mod, datetime.now(), st)
                    n += 1
                return _json_resp(self, {"ok": True, "started": n})
            _json_resp(self, {"error": "not found"}, 404)
        except Exception:                                    # noqa: BLE001
            _json_resp(self, {"error": traceback.format_exc()}, 500)

    def _module_action(self, key, act, body):
        st = load_state()
        mod = next((m for m in all_modules() if m.get("key") == key), None)
        if not mod:
            return _json_resp(self, {"error": "模块不存在: %s" % key}, 404)
        if act == "toggle":
            on = bool(body.get("enabled", not mod.get("enabled", True)))
            set_enabled(key, on)
            mod["enabled"] = on
            log("管理页面：%s → %s" % (key, "启用" if on else "停用"))
            if not mod["enabled"] and key in PROCS:
                _kill_tree(PROCS[key]["proc"])
                _finish(key, -9, "stopped", st)
            return _json_resp(self, {"ok": True, "key": key, "enabled": mod["enabled"]})
        # run
        if key in PROCS:
            return _json_resp(self, {"error": "%s 正在跑，先等它结束" % key}, 409)
        spawn(key, mod, datetime.now(), st)
        log("管理页面：手动跑 %s" % key)
        return _json_resp(self, {"ok": True, "key": key, "started": True})

    def _section_toggle(self, key, body):
        sec = load_sections()
        for s in sec.get("sections", []):
            if s.get("key") == key:
                s["enabled"] = bool(body.get("enabled", not s.get("enabled", True)))
                _write_json(SEC_PATH, sec)
                log("管理页面：板块 %s → %s" % (key, "显示" if s["enabled"] else "隐藏"))
                return _json_resp(self, {"ok": True, "key": key, "enabled": s["enabled"]})
        _json_resp(self, {"error": "板块不存在: %s" % key}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-SD-PW")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


def load_sections():
    return _read_json(SEC_PATH, {"sections": []})


def serve_thread():
    conf = api_conf()
    host = conf.get("host") or "0.0.0.0"
    port = int(conf.get("port") or DEFAULT_PORT)
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        log("HTTP 控制台起不来 %s:%s → %s" % (host, port, e))
        return
    log("HTTP 控制台 http://%s:%s（管理页面同源，无跨域问题）" % (host, port))
    srv.serve_forever()


# ────────────────────────────── 命令行 ──────────────────────────────
def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _is_scheduler(pid: int) -> bool:
    """确认这个 pid 真的是本调度器进程。

    容器里 PID 空间很小，残留的 pid 文件很容易被别的进程（比如 shell）复用，
    只判断「进程存活」会把无关进程当成调度器 —— 之后 stop/restart 就会
    SIGTERM 掉不相干的进程。所以这里额外校验 cmdline。
    """
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            raw = f.read()
    except OSError:
        return False
    parts = [a for a in raw.decode("utf-8", "ignore").split("\0") if a]
    if not parts:
        return False
    # 第一个参数必须是 python 解释器 —— 排除「某条 shell 命令里恰好提到 scheduler.py」
    if "python" not in os.path.basename(parts[0]).lower():
        return False
    # 必须有一个参数就是 scheduler.py 本体
    return any(os.path.basename(a) == "scheduler.py" for a in parts[1:])


def _http_alive(timeout=2.0) -> bool:
    """端口探活。

    cron 拉起的调度器跑在宿主命名空间，沙箱里 /proc/<pid> 看不见，单靠
    /proc 判断会误报「未运行」，进而重复起实例。端口是通的，用它兜底。

    4xx/5xx 也算活着：`urlopen` 对非 2xx **抛 HTTPError**，不会进 with 分支。
    老写法只认 `r.status`，于是 `/` 返回 404 时被当成「端口不通」——
    而 404 恰恰是「服务活着、只是没这个路径」。首装（modules 为空）和
    模板渲染异常时都会走到这条，届时 read_pid() 会误判「未运行」，
    看门狗转头去 start，起重复实例。
    """
    import urllib.error
    import urllib.request
    port = api_conf().get("port") or DEFAULT_PORT
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % int(port), timeout=timeout) as r:
            return 200 <= r.status < 500
    except urllib.error.HTTPError as e:
        return 400 <= e.code < 600
    except Exception:
        return False


def read_pid():
    """返回调度器 pid；进程不在当前命名空间但端口有响应时返回 -1。"""
    pid = 0
    try:
        with open(PID_PATH, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        pid = 0
    if pid > 0 and _pid_alive(pid) and _is_scheduler(pid):
        return pid
    if _http_alive():
        return -1
    return None


def _preflight_secrets():
    """启动门禁：必填敏感配置缺失就不启动（见 config/README.md）。"""
    try:
        app_secrets.require("api.password", "api.registry_key")
    except app_secrets.SecretMissing as e:
        print(str(e), file=sys.stderr)
        try:
            log("启动中止：%s" % str(e).replace("\n", " | "))
        except OSError:
            pass
        return False
    app_secrets.export_env()
    return True


def cmd_start():
    migrate_legacy_api_secrets()   # 先把 modules.json 里的旧明文搬进凭据文件
    if not _preflight_secrets():   # 再校验必填项，缺了就拒绝启动（fail-closed）
        return 1
    pid = read_pid()
    if pid:
        if pid > 0:
            print("已在运行 pid=%d" % pid)
        else:
            print("已在运行（端口 %s 有响应，进程不在当前命名空间）"
                  % (api_conf().get("port") or DEFAULT_PORT))
        return 0
    os.makedirs(LOG_DIR, exist_ok=True)
    out = open(SELF_LOG, "ab")
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "_daemon"],
                         cwd=HERE, stdin=subprocess.DEVNULL,
                         stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    time.sleep(1.8)
    if p.poll() is None:
        print("调度器已启动 pid=%d" % p.pid)
        print("  日志   %s" % SELF_LOG)
        print("  控制台 http://127.0.0.1:%s" % (api_conf().get("port") or DEFAULT_PORT))
        return 0
    print("启动失败，看 %s" % SELF_LOG)
    return 1


def _shutdown_via_api(timeout=10.0):
    """走 HTTP 让调度循环自然退出。返回 (ok, msg)。

    容器里调度器是 cron 在宿主命名空间拉起的，沙箱内 /proc/<pid> 看不见、
    os.kill 也发不到 —— 但它的 HTTP 控制台还通着。/api/shutdown 会置 _STOP，
    loop 里的 _STOP.wait(TICK_SEC) 立刻返回，进程自然退出。

    没有这条路，看门狗遇到「进程僵死」就只能干瞪眼：cmd_stop 拒绝、restart
    又不检查返回值，于是 start 看到端口有响应就报「已在运行」返回 0，整套
    「僵死 → 重启」在容器里是空转的。

    认证跟管理页同一套（X-SD-PW，fail-closed：没配密码一律拒绝），所以沙箱
    读到的凭据必须和进程内存里的一致 —— 不一致会拿到 401，这时如实报错，
    别猜密码。
    """
    import urllib.error
    import urllib.request
    conf = api_conf()
    port = int(conf.get("port") or DEFAULT_PORT)
    pw = str(conf.get("password") or "")
    if not pw:
        return False, "管理密码未配置（HTTP 控制台 fail-closed），无法远程停止"
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/shutdown" % port, data=b"{}", method="POST",
        headers={"X-SD-PW": pw, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, ("认证被拒（HTTP %d）：当前环境读到的管理密码与进程内存里的"
                           "不一致，检查凭据文件是否就是它启动时用的那份" % e.code)
        return False, "停止请求被拒：HTTP %d" % e.code
    except Exception as e:                                   # noqa: BLE001
        return False, "停止请求失败：%s: %s" % (type(e).__name__, e)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _http_alive(timeout=1.0):
            return True, "已通过 /api/shutdown 停止"
        time.sleep(0.3)
    return False, "已发出停止请求，但 %ss 内端口仍在响应" % int(timeout)


def cmd_stop():
    pid = read_pid()
    if not pid:
        print("没在跑")
        return 0
    if pid < 0:
        print("调度器在宿主命名空间（/proc 不可见、kill 发不到），改走 HTTP 优雅停…")
        ok, msg = _shutdown_via_api()
        print(("  ✓ " if ok else "  ✗ ") + msg)
        if ok:
            try:
                os.unlink(PID_PATH)
            except OSError:
                pass
            return 0
        print("  兜底：可在管理页点「停止」，或重启容器让它自然退出。")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print("停止失败：%s" % e)
        return 1
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.3)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.unlink(PID_PATH)
    except OSError:
        pass
    print("已停止 pid=%d" % pid)
    return 0


def cmd_status():
    cfg = load_cfg()
    st = load_state()
    pid = read_pid()
    if pid and pid > 0:
        _desc = "运行中 pid=%d" % pid
    elif pid:
        _desc = "运行中（端口 %s 有响应）" % (api_conf().get("port") or DEFAULT_PORT)
    else:
        _desc = "未运行"
    print("调度器 %s" % _desc)
    print("  控制台 http://127.0.0.1:%s   tick=%s" %
          (api_conf().get("port") or DEFAULT_PORT, (st.get("scheduler") or {}).get("tick") or "—"))
    _q = (st.get("scheduler") or {}).get("queue") or {}
    if _q:
        print("  队列 max_parallel=%s 在用=%s 孤儿=%s%s" % (
            _q.get("max_parallel"), _q.get("total_used", 0),
            len(_q.get("orphans") or {}),
            ("（%s）" % ",".join(_q.get("orphans") or {})) if _q.get("orphans") else ""))
    print()
    print("  %-11s %-22s %-16s %-9s %-8s %s" %
          ("KEY", "模块", "下次", "状态", "上次耗时", "数据域"))
    print("  " + "-" * 96)
    for m in all_modules():
        k = m.get("key")
        ms = st["modules"].get(k) or {}
        on = m.get("enabled", True)
        state = ms.get("last_status") or "未跑"
        if m.get("_config_error"):
            state = "配置错误"
        if k in PROCS:
            state = "运行中 %ss" % ms.get("elapsed", 0)
        elif ms.get("queued"):
            state = "排队"
        elif ms.get("pulled_forward") and ms.get("last_status") == "ok":
            state = "成功(提前)"
        print("  %-11s %-22s %-16s %-9s %-8s %s" %
              (k, (m.get("title") or "")[:20],
               ms.get("next_run") or "—",
               ("✓" if on else "✗") + " " + state[:7],
               ("%ss" % ms["duration"]) if ms.get("duration") else "—",
               ",".join(m.get("domains") or []) or "—"))
    return 0


def cmd_run(key):
    st = load_state()
    mod = next((m for m in all_modules() if m.get("key") == key), None)
    if not mod:
        print("没有这个模块：%s" % key)
        print("可选：" + " ".join(m.get("key", "") for m in all_modules()))
        return 1
    if key in PROCS:
        print("%s 正在跑" % key)
        return 1
    spawn(key, mod, datetime.now(), st)
    print("已启动 %s，日志 logs/%s.log" % (key, key))
    return 0


def cmd_toggle(key, on):
    if not set_enabled(key, on):
        print("没有这个模块：%s" % key)
        return 1
    print("%s → %s" % (key, "启用" if on else "停用"))
    return 0


def _daemon():
    if not _preflight_secrets():
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    def _bye(signum, frame):                                 # noqa: ARG001
        _STOP.set()

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    _health_preload()
    t = threading.Thread(target=serve_thread, daemon=True)
    t.start()
    try:
        loop()
    finally:
        for k in list(PROCS.keys()):
            try:
                _kill_tree(PROCS[k]["proc"])
            except OSError:
                pass
        try:
            os.unlink(PID_PATH)
        except OSError:
            pass


def cmd_fg():
    migrate_legacy_api_secrets()   # 先把 modules.json 里的旧明文搬进凭据文件
    if not _preflight_secrets():   # 再校验必填项，缺了就拒绝启动（fail-closed）
        return 1
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    threading.Thread(target=serve_thread, daemon=True).start()
    try:
        loop()
    except KeyboardInterrupt:
        _STOP.set()
    finally:
        try:
            os.unlink(PID_PATH)
        except OSError:
            pass
    return 0


def cmd_health():
    """供看门狗调用的健康检查。退出码：0=健康，1=未运行，2=僵死（进程在但心跳过期）。

    与 cmd_status 的区别：这里只回答「要不要拉起」，不打印表格，适合每分钟被调用。
    """
    pid = read_pid()
    if pid is None:
        print("未运行")
        return 1
    age = heartbeat_age()
    if age is None:
        # 老版本/刚启动还没有心跳文件：有 pid 或端口响应就不判僵死，避免误重启
        print("运行中 pid=%s（暂无心跳文件）" % pid)
        return 0
    if age > HB_STALE_SEC:
        print("僵死 pid=%s 心跳 %ds 未更新（阈值 %ds）" % (pid, age, HB_STALE_SEC))
        return 2
    print("健康 pid=%s 心跳 %ds 前" % (pid, age))
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd == "_daemon":
        _daemon()
        return 0
    if cmd == "start":
        return cmd_start()
    if cmd == "stop":
        return cmd_stop()
    if cmd == "restart":
        rc = cmd_stop()
        if rc != 0:
            # 停不掉就别再 start：start 看到端口有响应会报「已在运行」返回 0，
            # 整个 restart 于是静默成功、实际什么都没发生（旧进程还僵着）。
            print("重启中止：停止失败（原因见上），未启动新实例。")
            return rc
        time.sleep(0.6)
        return cmd_start()
    if cmd == "status":
        return cmd_status()
    if cmd == "health":
        # 看门狗专用：0=健康 / 1=未运行 / 2=僵死，不打印表格
        return cmd_health()
    if cmd == "fg":
        return cmd_fg()
    if cmd == "serve":
        # 网页服务即任务管理器：serve 起来就把调度循环一起带上。
        # 不再依赖 cron / 独立守护进程 —— 页面在跑，任务就在跑。
        migrate_legacy_api_secrets()   # 先把 modules.json 里的旧明文搬进凭据文件
        if not _preflight_secrets():   # 再校验必填项，缺了就拒绝启动（fail-closed）
            return 1
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(PID_PATH, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

        def _bye(signum, frame):                             # noqa: ARG001
            _STOP.set()

        signal.signal(signal.SIGTERM, _bye)
        signal.signal(signal.SIGINT, _bye)

        _health_preload()
        threading.Thread(target=loop, daemon=True).start()
        try:
            serve_thread()
        finally:
            _STOP.set()
            for k in list(PROCS.keys()):
                try:
                    _kill_tree(PROCS[k]["proc"])
                except OSError:
                    pass
            try:
                os.unlink(PID_PATH)
            except OSError:
                pass
        return 0
    if cmd == "run":
        if len(argv) < 3:
            print("用法：python3 scheduler.py run <key>")
            return 1
        return cmd_run(argv[2])
    if cmd in ("on", "off"):
        if len(argv) < 3:
            print("用法：python3 scheduler.py %s <key>" % cmd)
            return 1
        return cmd_toggle(argv[2], cmd == "on")
    print("未知命令：%s\n" % cmd)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
