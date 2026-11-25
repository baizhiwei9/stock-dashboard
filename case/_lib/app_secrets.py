#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_secrets — 敏感配置的唯一读取入口

原则
════════════════════════════════════════════════════════════════
· 敏感值只放 `config/secrets.json`（.gitignore，权限 0600），代码里不写明文。
· 仓库只提交 `config/secrets.example.json`（占位值，给人看字段清单）。
· 安装时必须先跑 `./install.sh`（内部调 `check_secrets.py --init`）把必填项补齐，
  否则 `check_secrets.py` 返回非 0，安装不通过、调度器不启动。
· 读取顺序：单项环境变量 > config/secrets.json > 默认值（仅非敏感项）。
· 报错只带 key 名和文件路径，**不回显值**。

配置文件查找顺序
════════════════════════════════════════════════════════════════
1. $COZE_SECRETS（显式指定文件）
2. <本仓>/config/secrets.json
3. <本仓同级 stock-dashboard>/config/secrets.json（precious-task 挂载场景）
4. ~/.config/coze-dashboard/secrets.json

环境变量覆盖
════════════════════════════════════════════════════════════════
每个 key 都会自动映射 `COZE_SEC_<KEY>`（点号换下划线、大写），
例如 `api.password` → `COZE_SEC_API_PASSWORD`；常用项另有传统别名
（FEISHU_WEBHOOK / TUSHARE_TOKEN / SMTP_PASSWORD …），见 ENV_ALIASES。

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

# 仓库根：本文件位于 <repo>/case/_lib/app_secrets.py
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_CASE_DIR = os.path.dirname(_LIB_DIR)
REPO_ROOT = os.path.dirname(_CASE_DIR)

# 数据结构：点号 key → 必填？
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

# 这些 key 不导出到任务子进程环境（只给调度器自己用）。
_SERVER_ONLY = ("api.password", "api.registry_key")

# 传统环境变量名（老脚本已经在用）。
ENV_ALIASES = {
    "feishu.webhook": ("FEISHU_WEBHOOK",),
    "feishu.app_id": ("FEISHU_APP_ID",),
    "feishu.app_secret": ("FEISHU_APP_SECRET",),
    "feishu.chat_id": ("FEISHU_CHAT_ID",),
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

# 占位值：出现在配置里视为「没填」。
_PLACEHOLDERS = {
    "", "REPLACE_ME", "CHANGE_ME", "CHANGEME", "TODO", "TBD", "NONE", "NULL",
    "YOUR_APP_ID", "YOUR_APP_SECRET", "YOUR_WEBHOOK", "YOUR_TOKEN",
    "YOUR_PASSWORD", "YOUR_REGISTRY_KEY",
}
_PLACEHOLDER_PREFIXES = ("REPLACE", "CHANGE_ME", "YOUR_", "示例", "请填", "填我")
_PLACEHOLDER_WRAPS = ("<", "{{", "${", "YOUR")

_MISSING = object()
_cache = {"mtime": None, "path": None, "data": None}


class SecretMissing(RuntimeError):
    """必填敏感配置缺失。消息里只有 key 名与路径，不含值。"""


def _is_placeholder(value) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if s in _PLACEHOLDERS:
        return True
    if len(s) >= 2 and s[0] in "<{[" and s[-1] in ">}]":
        return True
    up = s.upper()
    return any(up.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def candidate_paths():
    """按优先级返回配置文件的候选路径。

    显式设置了 $COZE_SECRETS 就只用它（即使文件还不存在），
    不静默回退到仓库内配置 —— 避免写入/读取到非预期的文件。
    """
    env_path = os.environ.get("COZE_SECRETS")
    if env_path:
        yield os.path.abspath(os.path.expanduser(env_path))
        return
    yield os.path.join(REPO_ROOT, "config", "secrets.json")
    # precious-task 与 stock-dashboard 部署在同一父目录时可以直接复用
    if os.path.basename(REPO_ROOT) != "stock-dashboard":
        yield os.path.join(os.path.dirname(REPO_ROOT), "stock-dashboard",
                           "config", "secrets.json")
    yield os.path.join(os.path.expanduser("~"), ".config", "coze-dashboard",
                       "secrets.json")


def config_path():
    """返回实际使用的配置文件路径（显式指定优先，否则取第一个存在的）。"""
    env_path = os.environ.get("COZE_SECRETS")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    for p in candidate_paths():
        if os.path.isfile(p):
            return p
    return os.path.join(REPO_ROOT, "config", "secrets.json")


def example_path():
    return os.path.join(REPO_ROOT, "config", "secrets.example.json")


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


def _walk(data, dotted):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _env_names(dotted):
    yield "COZE_SEC_" + dotted.upper().replace(".", "_")
    for name in ENV_ALIASES.get(dotted, ()):  # 传统别名
        yield name


def get(dotted, default=_MISSING):
    """读一个配置项。缺失时：给了 default 返回 default，否则抛 SecretMissing。"""
    for name in _env_names(dotted):
        val = os.environ.get(name)
        if val is not None and not _is_placeholder(val):
            return val
    val = _walk(_read_config(), dotted)
    if not _is_placeholder(val):
        return val
    if default is not _MISSING:
        return default
    raise SecretMissing(_missing_message([dotted]))


def get_int(dotted, default=_MISSING):
    raw = get(dotted, default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def has(dotted):
    try:
        get(dotted)
    except SecretMissing:
        return False
    return True


def missing(keys=None):
    """返回缺失（或仍是占位值）的 key 列表。"""
    out = []
    for k in (keys or (REQUIRED_KEYS + OPTIONAL_KEYS)):
        try:
            get(k)
        except SecretMissing:
            out.append(k)
    return out


def _missing_message(keys):
    path = config_path()
    state = "存在" if os.path.isfile(path) else "不存在"
    return (
        "缺少敏感配置：%s\n"
        "配置文件：%s（%s）\n"
        "处理：运行 ./install.sh 补齐（或参照 config/secrets.example.json 手工填写）。"
        "安装门禁不允许在缺配置时启动。"
        % (", ".join(keys), path, state))


def require(*keys):
    """校验一批 key，缺任一即抛 SecretMissing。"""
    lack = [k for k in keys if not has(k)]
    if lack:
        raise SecretMissing(_missing_message(lack))
    return {k: get(k) for k in keys}


def set_value(dotted, value, path=None):
    """写单个 key 到配置文件（原子写 + 0600）。返回写入路径。"""
    path = path or config_path()
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
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
                data = loaded
        except (OSError, ValueError):
            data = {}
    cur = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".secrets-", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)   # 0600
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _cache["mtime"] = None
    return path


def check_permissions(path=None):
    """返回配置文件的权限告警列表（空 = 正常）。"""
    path = path or config_path()
    if not os.path.isfile(path):
        return []
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        return ["配置文件权限过宽：%s 当前 %o，建议 chmod 600" % (path, mode)]
    return []


def export_env():
    """把任务侧要用的敏感项导出为环境变量（不覆盖已有值）。

    只导出通知/数据类，不导出 api.password / registry_key ——
    服务端口令只给调度器自己读，不下发给每个任务子进程。
    """
    for key, names in ENV_ALIASES.items():
        if key in _SERVER_ONLY:
            continue
        try:
            val = get(key)
        except SecretMissing:
            continue
        for name in names:
            os.environ.setdefault(name, str(val))
    # 兼容只认 FEISHU_* 命名的模块
    try:
        os.environ.setdefault("FEISHU_WEBHOOK", str(get("feishu.webhook")))
    except SecretMissing:
        pass


# ── 命令行 ──────────────────────────────────────────────────────

def _cmd_paths():
    print("仓库根：%s" % REPO_ROOT)
    print("当前生效：%s" % config_path())
    for p in candidate_paths():
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
        if lack:
            print("✗ 缺少必填项：%s" % ", ".join(lack))
        else:
            print("✓ 必填项齐全")
        if opt:
            print("· 未填（不影响启动，用到对应通道时才会报错）：%s"
                  % ", ".join(opt))
        for w in warns:
            print("! %s" % w)
    return 0 if ok else 1


def _cmd_env():
    """给 shell 用：eval "$(python3 app_secrets.py --env)" """
    for key, names in ENV_ALIASES.items():
        try:
            val = str(get(key))
        except SecretMissing:
            continue
        for name in names:
            print("export %s=%s" % (name, _shquote(val)))


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


def _persist_env_required(path):
    """把环境变量里给到的必填项落盘，避免只活在当前 shell。"""
    wrote = []
    for key in REQUIRED_KEYS:
        if not _is_placeholder(_walk(_read_config(), key)):
            continue
        for name in _env_names(key):
            val = os.environ.get(name)
            if val is not None and not _is_placeholder(val):
                set_value(key, val.strip(), path)
                wrote.append(key)
                break
    if wrote:
        print("已把环境变量中的必填项写入 %s：%s" % (path, ", ".join(wrote)))


def _cmd_init():
    path = config_path()
    if not os.path.isfile(path) and os.path.isfile(example_path()):
        with open(example_path(), encoding="utf-8") as f:
            data = f.read()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        print("已从 example 创建 %s（0600）" % path)
    _persist_env_required(path)
    lack = missing(REQUIRED_KEYS)
    if not lack:
        print("必填项已齐全。")
    for key in lack:
        while True:
            val = _prompt("请输入 %s%s：" % (key, "（不回显）" if key in (
                "api.password", "feishu.app_secret", "email.password",
                "tushare.token", "tushare.official_token") else ""),
                secret=key in ("api.password", "feishu.app_secret",
                               "email.password", "tushare.token",
                               "tushare.official_token"))
            if val is None:
                print("  输入已结束，跳过 %s（安装门禁会因此不通过）。" % key)
                break
            val = val.strip()
            if key == "api.password":
                if len(val) < 10:
                    print("  api.password 至少 10 位，且不要用 123456。")
                    continue
                confirm = _prompt("再输入一次 %s（不回显）：" % key, secret=True)
                if confirm is None:
                    print("  输入已结束，跳过 %s。" % key)
                    break
                if confirm.strip() != val:
                    print("  两次输入不一致，重来。")
                    continue
            if key == "api.registry_key":
                if len(val) < 16:
                    print("  api.registry_key 至少 16 位。")
                    continue
            if _is_placeholder(val):
                print("  不能为空或占位值。")
                continue
            set_value(key, val, path)
            print("  ✓ 已写入 %s" % key)
            break
    # 可选通道：逐项问，回车跳过
    opt = missing(OPTIONAL_KEYS)
    if opt:
        print("\n可选通道（用不到直接回车跳过）：")
    for key in opt:
        if key in ("email.smtp_host", "email.smtp_port"):
            continue      # 有默认值，不追问
        val = _prompt("  %s（回车跳过）：" % key,
                      secret=key in ("feishu.app_secret", "email.password",
                                     "tushare.official_token"))
        if val is None:
            break
        val = val.strip()
        if val and not _is_placeholder(val):
            set_value(key, val, path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    print("\n完成。运行 python3 case/_tools/check_secrets.py 复核。")


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
