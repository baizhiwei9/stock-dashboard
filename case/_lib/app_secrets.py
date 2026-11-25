#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_secrets —— 敏感配置的唯一读取入口

原则
════════════════════════════════════════════════════════════════
· 敏感值只放一个文件（默认 ``~/.config/stock-dashboard/secrets.json``，权限 0600），
  代码里不写明文，仓库里只有 ``secrets.example.json`` 占位模板。
· 安装时必须先跑 ``python3 setup.py``（或 ``./install.sh``）把必填项补齐，
  否则 ``setup.py --check`` 返回非 0，安装不通过、调度器不启动。
· 读取顺序：环境变量 > 配置文件 > 默认值（仅非敏感项）。
· 报错只带 key 名和文件路径，**不回显值**。

配置文件查找顺序（命中即止）
════════════════════════════════════════════════════════════════
1. ``$COZE_SECRETS``       —— 显式指定（容器挂载 / CI）
2. ``$APP_SECRETS_FILE``   —— 显式指定（历史名，等价）
3. ``~/.config/stock-dashboard/secrets.json``   —— 默认写入目标，仓外最安全
4. ``<repo>/config/secrets.json``               —— 兼容把配置放在仓库目录下的部署
5. ``<repo>/case/_lib/secrets.json``            —— 更老的备选位置
6. 旧扁平 ``feishu_secret.json``（家目录 / case/_lib），只服务 feishu 段

显式指定时**不静默回落**：挂载失败会直接报错，而不是让你以为读的是 A 其实读的是 B。

两种调用形式都支持（历史原因，调用点不用改）
════════════════════════════════════════════════════════════════
    get("api.password")                  # 点号形式，缺失即抛 SecretMissing
    get("api.password", "")              # 点号 + 缺省值
    get("scheduler", "password")         # 分段形式（section 名不含点），缺省返回 ""
    section("scheduler")                 # 取一整段 dict
    require("api.password", "api.registry_key")
    set_value("scheduler", "password", v)    # 或 set_value("api.password", v)

键名别名
════════════════════════════════════════════════════════════════
历史上有两套命名，统一在这里归一，两个方向都能读：

    api.password      <-> scheduler.password
    api.registry_key  <-> scheduler.registry_key
    feishu.webhook    <-> feishu.webhook_url

环境变量
════════════════════════════════════════════════════════════════
每个 key 自动映射 ``COZE_SEC_<KEY>``（点号换下划线、大写），例如
``api.password`` → ``COZE_SEC_API_PASSWORD``；常用项另有传统别名
（``FEISHU_WEBHOOK`` / ``TUSHARE_TOKEN`` / ``SMTP_PASSWORD`` / ``FRED_API_KEY`` …），
见 ``ENV_ALIASES``。``export_env()`` 把这些导出给任务子进程，
但**不导出** ``api.password`` / ``api.registry_key`` —— 服务端口令只给调度器自己读。

命令行
════════════════════════════════════════════════════════════════
    python3 app_secrets.py --paths   # 打印查找路径与命中的文件
    python3 app_secrets.py --check   # 校验必填项（不打印值）
    python3 app_secrets.py --init    # 交互式补齐缺失项
    python3 app_secrets.py --env     # 输出 shell export（给只认环境变量的老脚本）
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile

__all__ = [
    "SecretMissing", "section", "get", "get_int", "has", "require", "source",
    "file_candidates", "config_path", "set_value", "write_target",
    "export_env", "check_permissions", "missing",
]

# 仓库根：本文件位于 <repo>/case/_lib/app_secrets.py
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_CASE_DIR = os.path.dirname(_LIB_DIR)
REPO_ROOT = os.path.dirname(_CASE_DIR)

# ── 键清单 ─────────────────────────────────────────────────────
# 必填 = 没有它就装不起来（安装门禁）；可选 = 用不到该通道时可以不填。
REQUIRED_KEYS = (
    "api.password",
    "api.registry_key",
)

OPTIONAL_KEYS = (
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.webhook",
    "feishu.chat_id",
    "tushare.token",
    "tushare.official_token",
    "email.smtp_host",
    "email.smtp_port",
    "email.username",
    "email.password",
    "email.sender",
    "email.recipients",
    "fred.api_key",
    "treasury.client_id",
    "treasury.client_secret",
    "alert.webhook",
)

# 段 -> 该段包含的点号 key（section() 用它把跨段别名补进来）
_SECTION_KEYS = {
    "feishu": ("feishu.app_id", "feishu.app_secret", "feishu.webhook", "feishu.chat_id"),
    "scheduler": ("api.password", "api.registry_key"),
    "api": ("api.password", "api.registry_key"),
    "treasury": ("treasury.client_id", "treasury.client_secret"),
    "tushare": ("tushare.token", "tushare.official_token"),
    "email": ("email.smtp_host", "email.smtp_port", "email.username",
              "email.password", "email.sender", "email.recipients"),
    "fred": ("fred.api_key",),
    "alert": ("alert.webhook",),
}

# 键名别名：读不到就去对侧再找一次
_KEY_ALIASES = {
    "api.password": ("scheduler.password",),
    "api.registry_key": ("scheduler.registry_key",),
    "scheduler.password": ("api.password",),
    "scheduler.registry_key": ("api.registry_key",),
    "feishu.webhook": ("feishu.webhook_url",),
    "feishu.webhook_url": ("feishu.webhook",),
}

# 写入时的规范位置：历史别名统一落到规范 key，避免同一语义存两份
_CANONICAL = {
    "scheduler.password": "api.password",
    "scheduler.registry_key": "api.registry_key",
    "feishu.webhook_url": "feishu.webhook",
}

# 这些 key 不导出到任务子进程环境（只给调度器自己用）
# 非敏感默认值：这些是通道参数，不是凭据。配置文件里没写就用这个 ——
# 免得落到明文 SMTP 的 25 端口（163 只认 465 SSL / 587 STARTTLS）。
_BUILTIN_DEFAULTS = {
    "email.smtp_host": "smtp.163.com",
    "email.smtp_port": "465",
}

_SERVER_ONLY = ("api.password", "api.registry_key",
                "scheduler.password", "scheduler.registry_key")

# 点号 key -> 传统环境变量名（老脚本已经在用）
ENV_ALIASES = {
    "feishu.app_id": ("FEISHU_APP_ID",),
    "feishu.app_secret": ("FEISHU_APP_SECRET",),
    "feishu.webhook": ("FEISHU_WEBHOOK", "FEISHU_WEBHOOK_URL"),
    "feishu.chat_id": ("FEISHU_CHAT_ID",),
    "api.password": ("SCHEDULER_PASSWORD",),
    "api.registry_key": ("SCHEDULER_REGISTRY_KEY",),
    "tushare.token": ("TUSHARE_TOKEN",),
    "tushare.official_token": ("TUSHARE_OFFICIAL_TOKEN",),
    "email.smtp_host": ("SMTP_HOST", "EMAIL_HOST"),
    "email.smtp_port": ("SMTP_PORT", "EMAIL_PORT"),
    "email.username": ("SMTP_USER", "EMAIL_USER"),
    "email.password": ("SMTP_PASSWORD", "EMAIL_AUTH_CODE", "EMAIL_PASSWORD"),
    "email.sender": ("SMTP_FROM", "EMAIL_FROM"),
    "email.recipients": ("SMTP_TO", "EMAIL_TO"),
    "fred.api_key": ("FRED_API_KEY",),
    "treasury.client_id": ("TREASURY_CLIENT_ID",),
    "treasury.client_secret": ("TREASURY_CLIENT_SECRET",),
    "alert.webhook": ("CASE_ALERT_WEBHOOK",),
}

# 占位值：出现在配置里视为「没填」
_PLACEHOLDERS = {
    "", "REPLACE_ME", "CHANGE_ME", "CHANGEME", "TODO", "TBD", "NONE", "NULL",
    "YOUR_APP_ID", "YOUR_APP_SECRET", "YOUR_WEBHOOK", "YOUR_TOKEN",
    "YOUR_PASSWORD", "YOUR_REGISTRY_KEY",
}
_PLACEHOLDER_PREFIXES = ("REPLACE", "CHANGE_ME", "YOUR_", "示例", "请填", "填我",
                          "换成", "改为", "修改", "同上", "你的", "这里")
# 纯 x/星号占位：xxxxxxxxxxxx / ****
_PLACEHOLDER_MASKS = ("x", "X", "*")

_MISSING = object()
_cache = {"mtime": None, "path": None, "data": None}


class SecretMissing(RuntimeError):
    """必填敏感配置缺失。消息里只有 key 名与路径，不含值。"""


def _is_placeholder(value) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    if s in _PLACEHOLDERS:
        return True
    if len(s) >= 2 and s[0] in "<{[" and s[-1] in ">}]":
        return True
    # 纯掩码占位（xxxxxxxx / ****）。只在 4~20 位时判定 ——
    # 真实口令/密钥通常更长，避免把 24 位全 x 的强密码误判成占位。
    if 4 <= len(s) <= 20 and all(c in _PLACEHOLDER_MASKS for c in s):
        return True
    up = s.upper()
    return any(up.startswith(p) for p in _PLACEHOLDER_PREFIXES)


# ── 路径 ───────────────────────────────────────────────────────

def file_candidates():
    """按优先级返回配置文件的候选路径。

    显式指定（$COZE_SECRETS / $APP_SECRETS_FILE）就只用它，即使文件还不存在，
    不静默回退 —— 避免读写到非预期的文件。
    """
    for var in ("COZE_SECRETS", "APP_SECRETS_FILE"):
        env_path = (os.environ.get(var) or "").strip()
        if env_path:
            yield os.path.abspath(os.path.expanduser(env_path))
            return
    home = os.path.expanduser("~")
    yield os.path.join(home, ".config", "stock-dashboard", "secrets.json")
    yield os.path.join(REPO_ROOT, "config", "secrets.json")
    yield os.path.join(_LIB_DIR, "secrets.json")
    # 兼容旧扁平文件名（只服务 feishu 段）
    yield os.path.join(home, ".config", "stock-dashboard", "feishu_secret.json")
    yield os.path.join(_LIB_DIR, "feishu_secret.json")


def config_path():
    """实际使用的配置文件路径（显式指定优先，否则取第一个存在的）。"""
    for var in ("COZE_SECRETS", "APP_SECRETS_FILE"):
        env_path = (os.environ.get(var) or "").strip()
        if env_path:
            return os.path.abspath(os.path.expanduser(env_path))
    for p in file_candidates():
        if os.path.isfile(p):
            return p
    return write_target()


def write_target():
    """写入目标：显式指定 > ~/.config/stock-dashboard/secrets.json（仓外）。"""
    for var in ("COZE_SECRETS", "APP_SECRETS_FILE"):
        env_path = (os.environ.get(var) or "").strip()
        if env_path:
            return os.path.abspath(os.path.expanduser(env_path))
    return os.path.join(os.path.expanduser("~"), ".config", "stock-dashboard",
                        "secrets.json")


def example_path():
    return os.path.join(_LIB_DIR, "secrets.example.json")


def _blank_structure(data, _path=""):
    """只保留结构：字符串值置空，但非敏感默认值（SMTP 主机/端口）保留。

    example 里的字段说明（"换成强密码…" / "同上 client_secret"）是给人看的，
    不能被当成配置值继承下来 —— 否则别名兜底会被一句中文说明挡住。
    """
    if isinstance(data, dict):
        return {k: _blank_structure(v, (_path + "." + k).lstrip("."))
                for k, v in data.items() if not k.startswith("_")}
    if isinstance(data, list):
        return []
    if isinstance(data, str):
        return _BUILTIN_DEFAULTS.get(_path, "")
    return data


def _read_config():
    path = config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["data"] or {}
    data = {}
    if mtime is not None:
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError) as e:
            raise SecretMissing(
                "配置文件读不了：%s（%s）" % (path, type(e).__name__))
    _cache.update(path=path, mtime=mtime, data=data)
    return data


def source():
    return config_path() if os.path.isfile(config_path()) else "none"


# ── 取值 ───────────────────────────────────────────────────────

def _walk(data, dotted):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _env_names(dotted):
    yield "COZE_SEC_" + dotted.upper().replace(".", "_")
    for name in ENV_ALIASES.get(dotted, ()):
        yield name


def _raw_get(dotted):
    """不抛异常、不套别名，只查这一层（环境变量 + 文件）。"""
    for name in _env_names(dotted):
        val = os.environ.get(name)
        if val is not None and not _is_placeholder(val):
            return val
    val = _walk(_read_config(), dotted)
    return _MISSING if _is_placeholder(val) else val


def _file_value(dotted):
    """只查配置文件，**不看环境变量**。

    给 _persist_env_required 用：那里要判断「文件里到底有没有」，
    不能用 has()/get()——它们会先查环境变量，env 给了值就误以为文件里也有。

    注意 _walk 找不到时返回 _MISSING，而 _is_placeholder 只认 None 和 str，
    对 _MISSING 会返回 False（当成「不是占位、是个真值」）。所以这里必须先
    把 _MISSING 归一成 None，否则「文件里没有」会被误判成「文件里有」。
    """
    def _pick(k):
        v = _walk(_read_config(), k)
        return None if v is _MISSING or _is_placeholder(v) else v

    v = _pick(dotted)
    if v is not None:
        return v
    for alt in _KEY_ALIASES.get(dotted, ()):
        v = _pick(alt)
        if v is not None:
            return v
    return None


def _resolve(dotted):
    """查一个点号 key，带别名兜底。返回 _MISSING 表示没有。"""
    val = _raw_get(dotted)
    if val is not _MISSING:
        return val
    for alt in _KEY_ALIASES.get(dotted, ()):
        val = _raw_get(alt)
        if val is not _MISSING:
            return val
    return _BUILTIN_DEFAULTS.get(dotted, _MISSING)


def get(name, key=None, default=_MISSING):
    """读一个配置项。支持点号与分段两种调用。

    get("api.password")           缺失抛 SecretMissing
    get("api.password", "")       缺失返回 ""
    get("scheduler", "password")  缺失返回 ""
    get("scheduler", "password", default=None)  缺失返回 None
    """
    if "." in name:
        dotted = name
        dflt = key if key is not None else default
    else:
        dotted = "%s.%s" % (name, key if key is not None else "")
        dflt = default if default is not _MISSING else ""
    val = _resolve(dotted)
    if val is not _MISSING:
        return val
    if dflt is not _MISSING:
        return dflt
    raise SecretMissing(_missing_message([dotted]))


def get_int(dotted, default=_MISSING):
    raw = get(dotted, None if default is _MISSING else default)
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def has(dotted):
    return _resolve(dotted) is not _MISSING


def section(name):
    """取一整段，返回 dict（缺段返回 {}）。

    会把跨段别名（api.password <-> scheduler.password）和环境变量覆盖补进来，
    并同时给出 webhook / webhook_url 两种键名，兼容不同年代的调用点。
    """
    cfg = _read_config()
    out = {}
    raw = cfg.get(name)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if isinstance(v, str)})
    elif name == "feishu" and any(
            k in cfg for k in ("app_id", "app_secret", "webhook_url")):
        # 旧扁平格式 {"app_id": ..., "app_secret": ..., "webhook_url": ...}
        out.update({k: cfg.get(k, "") for k in ("app_id", "app_secret", "webhook_url")})

    for dotted in _SECTION_KEYS.get(name, ()):
        val = _resolve(dotted)
        if val is not _MISSING:
            out[dotted.split(".", 1)[1]] = str(val)

    # 键名互通
    if "webhook" in out and "webhook_url" not in out:
        out["webhook_url"] = out["webhook"]
    if "webhook_url" in out and "webhook" not in out:
        out["webhook"] = out["webhook_url"]
    return out


def missing(keys=None):
    """返回缺失（或仍是占位值）的 key 列表。"""
    return [k for k in (keys or (REQUIRED_KEYS + OPTIONAL_KEYS)) if not has(k)]


def _missing_message(keys):
    path = config_path()
    state = "存在" if os.path.isfile(path) else "不存在"
    return (
        "缺少敏感配置：%s\n"
        "配置文件：%s（%s）\n"
        "处理：运行 python3 setup.py（或 ./install.sh）补齐，"
        "或参照 case/_lib/secrets.example.json 手工填写。\n"
        "安装门禁不允许在缺配置时启动。详见 DEPLOY.md「敏感配置」一节。"
        % (", ".join(keys), path, state))


def require(*keys):
    """校验一批 key，缺任一即抛 SecretMissing。返回 {key: value}。"""
    lack = [k for k in keys if not has(k)]
    if lack:
        raise SecretMissing(_missing_message(lack))
    return {k: _resolve(k) for k in keys}


# ── 写入 ───────────────────────────────────────────────────────

def set_value(name, key=None, value=None, path=None):
    """写一个配置项（原子写 + 0600）。返回落盘路径。

    set_value("scheduler", "password", v)
    set_value("api.password", v)
    """
    if key is not None and value is None:
        # 两参数形式：set_value("api.password", v)
        dotted, val = name, key
    else:
        dotted = "%s.%s" % (name, key)
        val = value
    if "." not in dotted:
        raise ValueError("set_value 需要点号 key 或 (段, 字段)：%r" % (dotted,))
    dotted = _CANONICAL.get(dotted, dotted)      # 别名归一，不重复落盘

    p = path or write_target()
    data = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    elif os.path.isfile(example_path()):
        try:
            with open(example_path(), encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = _blank_structure(loaded)      # 只继承结构，不继承说明文字
        except (OSError, ValueError):
            data = {}

    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = val

    d_dir = os.path.dirname(p)
    if d_dir:
        os.makedirs(d_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".secrets-", suffix=".json", dir=d_dir or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)   # 0600
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _cache.update(mtime=None, path=None, data=None)
    return p


def check_permissions(path=None):
    """返回配置文件的权限告警列表（空 = 正常）。"""
    p = path or config_path()
    if not os.path.isfile(p):
        return []
    mode = stat.S_IMODE(os.stat(p).st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        return ["配置文件权限过宽：%s 当前 %o，建议 chmod 600" % (p, mode)]
    return []


def export_env():
    """把任务侧要用的敏感项导出为环境变量（不覆盖已有值）。

    只导出通知/数据类，不导出 api.password / registry_key ——
    服务端口令只给调度器自己读，不下发给每个任务子进程。
    """
    for key in ENV_ALIASES:
        if key in _SERVER_ONLY:
            continue
        val = _resolve(key)
        if val is _MISSING:
            continue
        for name in ENV_ALIASES[key]:
            os.environ.setdefault(name, str(val))


# ── 命令行 ─────────────────────────────────────────────────────

def _cmd_paths():
    print("仓库根：%s" % REPO_ROOT)
    print("当前生效：%s" % config_path())
    for p in file_candidates():
        print("  %s %s" % ("✓" if os.path.isfile(p) else "·", p))


def _cmd_check(as_json=False):
    lack = missing(REQUIRED_KEYS)
    opt = missing(OPTIONAL_KEYS)
    warns = check_permissions()
    ok = not lack and not warns
    if as_json:
        print(json.dumps({"ok": ok, "config": config_path(),
                          "missing_required": lack, "missing_optional": opt,
                          "warnings": warns}, ensure_ascii=False))
    else:
        print("配置文件：%s" % config_path())
        print("✓ 必填项齐全" if not lack
              else "✗ 缺少必填项：%s" % ", ".join(lack))
        if opt:
            print("· 未填（不影响启动，用到对应通道时才报错）：%s" % ", ".join(opt))
        for w in warns:
            print("! %s" % w)
    return 0 if ok else 1


def _cmd_env():
    """给 shell 用：eval "$(python3 app_secrets.py --env)" """
    for key in ENV_ALIASES:
        if key in _SERVER_ONLY:
            continue
        val = _resolve(key)
        if val is _MISSING:
            continue
        for name in ENV_ALIASES[key]:
            print("export %s=%s" % (name, _shquote(str(val))))


def _shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _prompt(prompt, secret=False):
    """读一行输入；EOF（非交互/管道结束）返回 None，由调用方决定跳过还是中止。"""
    import getpass
    try:
        return getpass.getpass(prompt) if secret else input(prompt)
    except EOFError:
        print()
        return None


_SECRET_KEYS = ("api.password", "feishu.app_secret", "email.password",
                "tushare.token", "tushare.official_token")


def _persist_env_required(path, keys=None):
    """把环境变量里给到的配置落盘，避免只活在当前 shell（批量装机 / CI 用）。

    这里**必须**用 _file_value 判断，不能用 has()：
    has() 走 _resolve → _raw_get，而 _raw_get 是「先查环境变量、再查文件」，
    所以 env 给了值时 has() 已经是 True。原来写成 `if has(key): continue`，
    等于「env 给了反而跳过落盘」——环境变量有值时提前 continue，没值时循环里
    也取不到值，两条路都写不进文件，整段是死代码。表现就是无 TTY 环境下
    `--init` 只落出一个空壳 secrets.json。
    """
    wrote = []
    for key in (keys or (REQUIRED_KEYS + OPTIONAL_KEYS)):
        if _file_value(key) is not None:
            continue
        for name in _env_names(key):
            val = os.environ.get(name)
            if val is not None and not _is_placeholder(val):
                set_value(key, val.strip(), path=path)
                wrote.append(key)
                break
    if wrote:
        print("已把环境变量中的配置写入 %s：%s" % (path, ", ".join(wrote)))
    return wrote


def persist_env(path=None, keys=None):
    """把环境变量里给到的配置落盘（批量装机 / CI 的公开入口）。"""
    return _persist_env_required(path or write_target(), keys)


# 常见弱口令（小写比较）。交互录入和门禁共用这一张表 ——
# 之前 setup.py 有一份、_cmd_init 自己写了个 len<10，两边口径分裂：
# 10~11 位能录进去，随后被 --check 拒掉。
_WEAK_PW = {
    "123456", "1234567", "12345678", "123456789", "1234567890",
    "password", "passwd", "admin", "admin123", "root", "test",
    "qwerty", "abc123", "111111", "000000", "888888", "666666",
    "stock-dashboard", "stockdashboard", "feishu",
}


def is_weak_pw(pw):
    """是不是表里的常见弱口令（不看长度）。提示文案要区分「太短」和「太常见」时用。"""
    return str(pw or "").strip().lower() in _WEAK_PW


def pw_problem(pw):
    """管理密码强度校验。返回问题描述，合格返回 None。

    这是唯一口径：门禁（setup.py --check）、交互录入（_cmd_init）、以及任何
    让用户设密码的入口都必须调它。分开写就会像以前那样 —— 录入放宽到 10 位、
    门禁卡 12 位，用户照着提示录完，下一步安装却中止。
    """
    pw = str(pw or "")
    if len(pw) < 12:
        return "至少 12 位（当前 %d 位）" % len(pw)
    if pw.strip().lower() in _WEAK_PW:
        return "这是常见弱口令，必须换掉"
    kinds = sum([
        any(c.islower() for c in pw),
        any(c.isupper() for c in pw),
        any(c.isdigit() for c in pw),
        any(not c.isalnum() for c in pw),
    ])
    if kinds < 2:
        return "至少包含小写字母 / 大写字母 / 数字 / 符号中的两类"
    return None


def _cmd_init():
    path = config_path()
    if not os.path.isfile(path) and os.path.isfile(example_path()):
        with open(example_path(), encoding="utf-8") as f:
            loaded = json.load(f)
        blank = _blank_structure(loaded) if isinstance(loaded, dict) else {}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blank, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        print("已从 example 创建 %s（0600）" % path)
    _persist_env_required(path)
    lack = missing(REQUIRED_KEYS)
    if not lack:
        print("必填项已齐全。")
    for key in lack:
        while True:
            val = _prompt("请输入 %s%s：" % (key, "（不回显）" if key in _SECRET_KEYS else ""),
                          secret=key in _SECRET_KEYS)
            if val is None:
                print("  输入已结束，跳过 %s（安装门禁会因此不通过）。" % key)
                break
            val = val.strip()
            if key == "api.password":
                prob = pw_problem(val)
                if prob:
                    print("  api.password %s。" % prob)
                    continue
                confirm = _prompt("再输入一次 %s（不回显）：" % key, secret=True)
                if confirm is None:
                    print("  输入已结束，跳过 %s。" % key)
                    break
                if confirm.strip() != val:
                    print("  两次输入不一致，重来。")
                    continue
            if key == "api.registry_key" and len(val) < 16:
                print("  api.registry_key 至少 16 位。")
                continue
            if _is_placeholder(val):
                print("  不能为空或占位值。")
                continue
            set_value(key, val, path)
            print("  ✓ 已写入 %s" % key)
            break
    opt = missing(OPTIONAL_KEYS)
    if opt:
        print("\n可选通道（用不到直接回车跳过）：")
    for key in opt:
        if key in ("email.smtp_host", "email.smtp_port"):
            continue      # 有默认值，不追问
        val = _prompt("  %s（回车跳过）：" % key, secret=key in _SECRET_KEYS)
        if val is None:
            break
        val = val.strip()
        if val and not _is_placeholder(val):
            set_value(key, val, path)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print("\n完成。运行 python3 setup.py --check 复核。")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _cmd_check()
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "--paths":
        _cmd_paths()
        return 0
    if cmd == "--check":
        return _cmd_check("--json" in argv)
    if cmd == "--init":
        _cmd_init()
        return 0
    if cmd == "--env":
        _cmd_env()
        return 0
    print("未知参数：%s（支持 --paths / --check / --init / --env）" % cmd,
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
