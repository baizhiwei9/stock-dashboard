#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装引导 / 部署门禁 —— 敏感配置必须在部署阶段录入，不配就装不上。

用法::

    python3 setup.py              # 安装引导：缺项就交互式录入并校验，通过才返回 0
    python3 setup.py --check      # 只校验不录入（CI / 部署流水线用），不合格退出 1
    python3 setup.py --force      # 已有配置也重新录入
    python3 setup.py --skip-live  # 跳过联网校验（离线环境）

为什么要有这个脚本：
    凭据原本明文硬编码在代码里（2026-11 脱敏）。脱敏后如果只写「请自行配置」，
    部署的人很容易漏配，然后跑到全流程最后一步的推送阶段才失败——最贵的一种失败。
    所以把凭据录入做成**安装前置门禁**：不配、配错、配了连不通，都装不上。

管三段配置（全部落 ``~/.config/stock-dashboard/secrets.json``，权限 600，不入库）::

    feishu     APP ID / APP SECRET / Webhook     必填，联网校验
    scheduler  管理密码 / 注册密钥                必填，本地强度校验（弱口令直接拒）
    treasury   client_id / client_secret         选填，缺了 us_treasury_weekly 降级跳过

历史坑：管理密码曾硬编码 "123456" 写在 modules.json 里随公开仓库外泄。
现在没有任何硬编码兜底，缺项一律 fail-closed。
"""
import argparse
import getpass
import json
import os
import random
import re
import string
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402
import feishu_creds as creds  # noqa: E402

TARGET = app_secrets.write_target()

# 常见弱口令：见到直接拒，不做「只是提示」
# ---------------------------------------------------------------- 交互
def _ask(label, hint, secret=False, default=None):
    """录入一项。default 不为 None 时，直接回车 = 采用 default。"""
    print("\n%s" % label)
    if hint:
        print("  %s" % hint)
    while True:
        if default is not None:
            print("  （直接回车 = %s）" % ("自动生成" if default == "" else default))
        v = (getpass.getpass("  > ") if secret else input("  > ")).strip()
        if v:
            return v
        if default is not None:
            return default
        print("  ✗ 不能为空，必须填写")


def _gen(n=24):
    pool = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    return "".join(random.SystemRandom().choice(pool) for _ in range(n))


def _gen_hex(n=32):
    return "".join(random.SystemRandom().choice("0123456789abcdef") for _ in range(n))


# ---------------------------------------------------------------- 校验
def _verify_token(app_id, app_secret):
    req = urllib.request.Request(
        creds.TOKEN_URL,
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        d = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception as e:  # noqa: BLE001
        return False, "调不通飞书 token 接口：%s" % repr(e)[:160]
    if d.get("code") == 0 and d.get("tenant_access_token"):
        return True, "app_id / app_secret 有效（已换到 tenant_access_token）"
    return False, "飞书返回 code=%s msg=%s" % (d.get("code"), d.get("msg"))


def _verify_webhook(url):
    if not url.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
        return False, "webhook 地址格式不对，应以 https://open.feishu.cn/open-apis/bot/v2/hook/ 开头"
    try:
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        # 空 body 必然报参数错误，能拿到业务 code 就说明地址真实可达
        return True, "webhook 可达（返回 code=%s，符合空 body 的预期）" % d.get("code")
    except urllib.error.HTTPError as e:
        e.read()
        return (True, "webhook 可达（HTTP %s）" % e.code) if e.code < 500 \
            else (False, "webhook 服务端错误 %s" % e.code)
    except Exception as e:  # noqa: BLE001
        return False, "webhook 连不通：%s" % repr(e)[:160]


def _pw_problem(pw):
    """管理密码强度校验。返回问题描述，合格返回 None。

    规则本体在 app_secrets.pw_problem —— 交互录入（app_secrets._cmd_init）
    也调那一个，两边不会再各写一套。
    """
    return app_secrets.pw_problem(pw)


# 值注入用的环境变量裸名（FEISHU_APP_ID 这类），file_only 校验时临时摘掉。
# 路径类（COZE_SECRETS / APP_SECRETS_FILE）不在其中 —— 它们指定「读哪个文件」，要保留。
_VALUE_ENV_NAMES = {n for _names in app_secrets.ENV_ALIASES.values() for n in _names}


def _state(file_only=False):
    """读取当前三段配置。

    file_only=True 只认凭据文件，忽略值注入的环境变量。安装门禁用这个：
    `--init` 写盘失败（比如 COZE_SECRETS 指到不可写路径）但 COZE_SEC_* 还在环境里时，
    普通校验读到的是 env，会全绿 —— 于是「打印安装成功、磁盘空空如也」，
    等第一次启动才 fail-closed。校验磁盘才算校验安装。
    """
    names = ("feishu", "scheduler", "treasury")
    if not file_only:
        return {name: app_secrets.section(name) for name in names}
    saved = {}
    for k in list(os.environ):
        if k.startswith("COZE_SEC_") or k in _VALUE_ENV_NAMES:
            saved[k] = os.environ.pop(k)
    try:
        return {name: app_secrets.section(name) for name in names}
    finally:
        os.environ.update(saved)


def _feishu_ready(f, skip_live):
    """返回 (是否合格, 明细行列表, 失败原因列表)。"""
    lines, fails = [], []
    missing = [k for k in ("app_id", "app_secret", "webhook_url") if not f.get(k)]
    if missing:
        fails.append("飞书凭据缺：%s" % ", ".join(missing))
        lines.append("✗ 飞书凭据缺：%s" % ", ".join(missing))
        return False, lines, fails
    if skip_live:
        lines.append("✅ 飞书三项齐全（已跳过联网校验）")
        return True, lines, fails
    ok1, m1 = _verify_token(f["app_id"], f["app_secret"])
    ok2, m2 = _verify_webhook(f["webhook_url"])
    lines.append(("✅ " if ok1 else "✗ ") + m1)
    lines.append(("✅ " if ok2 else "✗ ") + m2)
    if not ok1:
        fails.append(m1)
    if not ok2:
        fails.append(m2)
    return (ok1 and ok2), lines, fails


def _scheduler_ready(s):
    lines, fails = [], []
    pw = s.get("password") or ""
    if not pw:
        fails.append("管理密码未配置")
        lines.append("✗ 管理密码未配置（HTTP 控制台会 fail-closed 全拒）")
    else:
        bad = _pw_problem(pw)
        if bad:
            fails.append("管理密码不合规：%s" % bad)
            lines.append("✗ 管理密码不合规：%s" % bad)
        else:
            lines.append("✅ 管理密码合规（%d 位）" % len(pw))
    if not s.get("registry_key"):
        fails.append("worker 注册密钥未配置")
        lines.append("✗ worker 注册密钥未配置")
    else:
        lines.append("✅ worker 注册密钥已配置（%d 位）" % len(s["registry_key"]))
    return (not fails), lines, fails


def _treasury_ready(t):
    """treasury 选填：缺了只是 us_treasury_weekly 的 buybacks 段降级跳过。"""
    if t.get("client_id") and t.get("client_secret"):
        return True, ["✅ treasury 凭据已配置（buybacks 段可正常取数）"], []
    return True, ["· treasury 凭据未配置 —— 不影响安装，us_treasury_weekly 的 buybacks 段会降级跳过"], []


def _save(sec):
    """把三段写进 secrets.json（分段结构，权限 600）。"""
    for name, seg in sec.items():
        for k, v in (seg or {}).items():
            if isinstance(v, str) and v.strip():
                app_secrets.set_value(name, k, v.strip())
    os.chmod(TARGET, 0o600)
    print("\n✅ 已写入 %s（权限 600，不在任何仓库里）" % TARGET)




# ---------------------------------------------------------------- 仓库审计
# 判断「这个仓库能不能开源」的硬门禁：扫全仓有没有真凭据漏在文件里。
# 只看会被提交的文件；.git / __pycache__ / 备份 / 凭据文件本身都跳过。
_AUDIT_SKIP_DIR = {".git", "__pycache__", "node_modules", "pub", "logs", ".runtime", ".coze"}
_AUDIT_SKIP_SUFFIX = (".pyc", ".log", ".pid", ".bak")
# modules.json 是本地安装配置（本机路径 / 端口 / 启用哪些模块），已列入 .gitignore，
# 不随仓库发布，所以不算「入库泄露」；它的弱口令问题由 _upgrade_local_secrets() 单独管。
_AUDIT_SKIP_NAME = {"secrets.json", "feishu_secret.json", "modules.json"}

# (名称, 正则, 是否致命)
_AUDIT_RULES = [
    ("飞书群机器人 webhook",
     re.compile(r"open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-fA-F]{8}-"), True),
    ("飞书 APP ID",
     re.compile(r"\bcli_[a-z0-9]{16,}\b"), True),
    ("疑似密钥（app_secret / client_secret 相邻 32 位）",
     re.compile(r"(?i)(app_secret|client_secret|appsecret)[\"'\s:=]{1,8}[0-9a-zA-Z]{32}"), True),
    ("JSON 里的明文 password",
     re.compile(r'"password"\s*:\s*"(?!\s*")([^"]{1,64})"'), True),
    ("JSON 里的明文 registry_key",
     re.compile(r'"registry_key"\s*:\s*"(?!\s*")([^"]{1,64})"'), True),
    ("代码里的硬编码兜底口令",
     re.compile(r'or\s+"(123456|password|admin)"'), True),
]

# 占位符白名单：命中这些就当作模板，不算泄露
_PLACEHOLDER = re.compile(
    r"^(<.*>|\*{3,}|[xX*]{4,}|[xX]{3,}.*|换.*|填.*|你的.*|.*example.*|.*placeholder.*|\.\.\.)$"
)
# 环境变量名不是值：SCHEDULER_PASSWORD / APP_SECRET 这类全大写常量
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{4,}$")
# 飞书 APP ID 的占位写法：cli_xxxxxxxx / cli_<你的app_id>
_CLI_PLACEHOLDER = re.compile(r"^cli_([xX*]{4,}.*|<.*>|xxx.*)$", re.I)
# 字段名本身不是值
_FIELD_NAMES = {
    "password", "registry_key", "app_secret", "appsecret", "client_secret",
    "app_id", "client_id", "webhook_url", "secret", "token",
}


def _is_placeholder(got):
    """判断这个命中是不是模板占位，不是真凭据。"""
    g = (got or "").strip().strip("\"'")
    if not g:
        return True
    if _PLACEHOLDER.match(g):
        return True
    if _ENV_NAME.match(g):
        return True
    if _CLI_PLACEHOLDER.match(g):
        return True
    if g.lower() in _FIELD_NAMES:
        return True
    return False


def _iter_audit_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _AUDIT_SKIP_DIR]
        for fn in filenames:
            if fn in _AUDIT_SKIP_NAME or fn.endswith(_AUDIT_SKIP_SUFFIX):
                continue
            if ".bak_" in fn or ".bak." in fn:
                continue
            # *.example.json / *.sample.* 是给人看的模板，本来就只有占位符
            if ".example." in fn or ".sample." in fn or fn.endswith(".example"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > 3 * 1024 * 1024:
                    continue
            except OSError:
                continue
            yield path


def audit_repo(root=None, fix=False):
    """扫全仓敏感值。返回 (命中列表, 是否通过)。

    命中项 = (相对路径, 行号, 规则名, 片段)。
    """
    root = root or _HERE
    hits = []
    for path in _iter_audit_files(root):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        for i, line in enumerate(lines, 1):
            for name, rx, fatal in _AUDIT_RULES:
                for m in rx.finditer(line):
                    got = (m.group(1) if m.groups() else m.group(0)).strip()
                    if _is_placeholder(got):
                        continue
                    hits.append((rel, i, name, got[:60]))
    return hits, (not hits)


def _upgrade_local_secrets():
    """把本机 modules.json 的弱口令升级成强随机，并与凭据文件对齐。

    历史包袱：api.password / api.registry_key 曾长期是明文 "123456"，且 modules.json
    早期是入库的，所以这两个值随公开仓库外泄过。现在 modules.json 已列入 .gitignore
    （本地安装配置），但老机器上那个 "123456" 还在生效，必须换掉。

    这里不删值 —— 正在运行的 scheduler 是旧代码、只读 modules.json，删了会 fail-closed
    断服。做法是：生成强随机 → 写进凭据文件 → 同步写回 modules.json，旧进程立刻可用。
    """
    cfg_path = os.path.join(_HERE, "modules.json")
    if not os.path.exists(cfg_path):
        print("\n没有 modules.json，跳过（新机器先复制 modules.example.json）")
        return
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        print("\n读 modules.json 失败：%s" % e)
        return

    api = cfg.get("api") or {}
    sec = app_secrets.section("scheduler")
    changed, todo = [], {}

    def _pw_ok_(v):
        return bool(v) and not _pw_problem(v)

    def _key_ok_(v):
        return bool(v) and len(v) >= 24

    for k, gen, ok in (("password", lambda: _gen(24), _pw_ok_),
                       ("registry_key", lambda: _gen_hex(32), _key_ok_)):
        old = str(api.get(k) or "")
        new = str(sec.get(k) or "")
        if not ok(new):
            new = gen()
            app_secrets.set_value("scheduler", k, new)
            changed.append("%s（凭据文件新生成，%d 位）" % (k, len(new)))
        if old != new:
            todo[k] = new
            if old:
                changed.append("%s（modules.json 旧值 %s → 已换）"
                               % (k, "弱口令" if app_secrets.is_weak_pw(old)
                                  else "长度 %d" % len(old)))

    if not changed:
        print("\nmodules.json 与凭据文件已对齐，无需改动")
        return

    import shutil
    import time as _t
    shutil.copy2(cfg_path, cfg_path + ".bak_" + _t.strftime("%Y%m%d_%H%M%S"))
    for k, v in todo.items():
        api[k] = v
    api["_note"] = ("password / registry_key 的源头是 ~/.config/stock-dashboard/secrets.json 的 "
                    "scheduler 段；此处保留一份是给旧版 scheduler 进程读的。"
                    "modules.json 属本地安装配置，不入库（见 .gitignore）。")
    cfg["api"] = api
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("\n已处理：")
    for c in changed:
        print("  · " + c)
    print("\n正在运行的 scheduler 是旧代码（只读 modules.json），已同步因此不断服；")
    print("重启一次后它会从凭据文件读，modules.json 那份就可以不管了：")
    print("  python3 scheduler.py restart")


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不录入")
    ap.add_argument("--init", action="store_true",
                    help="非交互：把环境变量里的配置落盘，然后按 --check 校验"
                         "（批量装机 / CI 用，不需要 TTY）")
    ap.add_argument("--force", action="store_true", help="已有配置也重新录入")
    ap.add_argument("--skip-live", action="store_true", help="跳过联网校验（离线环境）")
    ap.add_argument("--file-only", action="store_true",
                    help="配合 --check：只校验凭据文件里真实落了什么，忽略 COZE_SEC_* 环境变量。"
                         "安装门禁（install.sh）用这个 —— 否则 --init 写盘失败但 env 还在时会假通过")
    ap.add_argument("--audit", action="store_true",
                    help="扫全仓有没有真凭据漏在文件里（开源 / push 前的硬门禁）")
    ap.add_argument("--fix", action="store_true",
                    help="配合 --audit：把 modules.json 的弱口令升级成强随机，并与凭据文件对齐")
    a = ap.parse_args()

    print("=" * 68)
    print(" stock-dashboard 安装引导 —— 敏感配置")
    print("=" * 68)
    print("凭据文件：%s" % TARGET)
    print("当前来源：%s" % app_secrets.source())

    cur = _state(a.file_only)
    f, s, t = cur["feishu"], cur["scheduler"], cur["treasury"]

    # ---------- 仓库审计（开源 / push 门禁） ----------
    if a.audit:
        if a.fix:
            _upgrade_local_secrets()

        hits, ok = audit_repo()
        print("\n--- 仓库审计 ---")
        if ok:
            print("  ✅ 全仓未发现明文凭据，可以开源 / push")
            return 0
        by_file = {}
        for rel, ln, name, frag in hits:
            by_file.setdefault(rel, []).append((ln, name, frag))
        for rel in sorted(by_file):
            print("  ✗ %s" % rel)
            for ln, name, frag in by_file[rel][:6]:
                print("      L%-5d %-34s %s" % (ln, name, frag))
            if len(by_file[rel]) > 6:
                print("      …… 另有 %d 处" % (len(by_file[rel]) - 6))
        print("\n✗ 共 %d 处命中，%d 个文件。开源前必须清干净。" % (len(hits), len(by_file)))
        print("  修 modules.json 弱口令：python3 setup.py --audit --fix")
        return 1

    # ---------- 非交互落盘（批量装机 / CI） ----------
    if a.init:
        print("\n--- 从环境变量落盘 ---")
        try:
            wrote = app_secrets.persist_env(TARGET)
        except OSError as e:
            # exit 2：写盘本身失败（目录不存在 / 不可写 / 权限不足）。
            # 跟「没东西可落盘」分开，否则装机脚本会把两种完全不同的故障
            # 报成同一句话，排错方向直接跑偏。
            print("  ✗ 写盘失败：%s: %s" % (type(e).__name__, e))
            print("  检查 COZE_SECRETS 指向的目录是否存在且可写；"
                  "不设它就默认写 ~/.config/stock-dashboard/。")
            return 2
        if not wrote:
            # 没东西可落盘有两种：幂等重跑（文件里已有值），或压根没给凭据。
            # 前者继续走校验就行；后者必须 exit 3 —— 否则空环境 + 空盘会一路
            # 走到 --check 报「校验未通过 N 项」，看不出根因是「你什么都没给」。
            snap = _state(True)
            have = any(snap[k] for k in ("feishu", "scheduler", "treasury"))
            if not have:
                print("  ✗ 环境变量里没有可落盘的配置，凭据文件里飞书 / 管理密码 / 注册密钥也都没值。")
                print("  先 export 下面这些再跑（COZE_SEC_ 前缀）：")
                for k in ("api.password", "api.registry_key",
                          "feishu.app_id", "feishu.app_secret", "feishu.webhook"):
                    print("    %s" % ", ".join(app_secrets._env_names(k)))
                return 3
            print("  环境变量里没有新配置可落盘（已有的不覆盖），继续校验磁盘。")
        a.check = True          # 落盘后照常走完整校验
        a.file_only = True      # 但校验的是「磁盘上真的写了什么」，不是 env 里还剩下什么
        # 上面那行必须在重新取状态之前生效。cur/f/s/t 是最开头取的（那时
        # a.file_only 还是命令行传进来的 False），不重取就等于拿 env 当磁盘内容，
        # --init 自带的那次校验会假通过 —— 文件里是 10 位弱口令、env 里是强口令，
        # 于是先打「✅ 全部就绪」，install.sh 紧接着 --check --file-only 又 exit 1。
        cur = _state(True)
        f, s, t = cur["feishu"], cur["scheduler"], cur["treasury"]

    # ---------- 只校验 ----------
    if a.check:
        print("\n--- 校验 ---")
        all_fails = []
        for ready, lines, fails in (
            _feishu_ready(f, a.skip_live),
            _scheduler_ready(s),
            _treasury_ready(t),
        ):
            for ln in lines:
                print("  " + ln)
            all_fails += fails
        if all_fails:
            print("\n✗ 校验未通过，共 %d 项。修正后重跑：python3 setup.py" % len(all_fails))
            return 1
        print("\n✅ 全部就绪，安装可继续。")
        return 0

    # ---------- 安装引导 ----------
    complete = (
        all(f.get(k) for k in ("app_id", "app_secret", "webhook_url"))
        and not _pw_problem(s.get("password") or "")
        and s.get("registry_key")
    )

    if complete and not a.force:
        print("\n检测到已有配置：")
        print("  飞书 app_id    : %s..." % f["app_id"][:10])
        print("  飞书 app_secret: %d 字符" % len(f["app_secret"]))
        print("  飞书 webhook   : %s..." % f["webhook_url"][:56])
        print("  管理密码       : %d 位" % len(s["password"]))
        print("  注册密钥       : %d 位" % len(s["registry_key"]))
        print("  treasury       : %s" % ("已配置" if t.get("client_id") else "未配置（可降级）"))
        ok, lines, _ = _feishu_ready(f, a.skip_live)
        print()
        for ln in lines:
            print("  " + ln)
        if not ok:
            print("\n凭据存在但不可用。请用 --force 重新录入：python3 setup.py --force")
            return 1
        print("\n✅ 配置就绪，安装可继续。")
        return 0

    if not sys.stdin.isatty():
        print("\n✗ 当前不是交互终端，无法录入。请改用环境变量：")
        print("    export FEISHU_APP_ID=... FEISHU_APP_SECRET=... FEISHU_WEBHOOK_URL=...")
        print("    export SCHEDULER_PASSWORD=... SCHEDULER_REGISTRY_KEY=...")
        print("    export TREASURY_CLIENT_ID=... TREASURY_CLIENT_SECRET=...   # 选填")
        print("  或预先写好：%s" % TARGET)
        print("  格式见 case/_lib/secrets.example.json")
        return 1

    # ---- 1) 飞书（必填）----
    print("\n" + "-" * 68)
    print(" [1/3] 飞书凭据（必填，缺失则所有报告推送都会失败）")
    print("-" * 68)
    f_new = {
        "app_id": f.get("app_id") or _ask(
            "飞书 APP ID",
            "开放平台 → 开发者后台 → 自建应用 → 凭证与基础信息，形如 cli_xxxxxxxx"),
        "app_secret": f.get("app_secret") or _ask(
            "飞书 APP SECRET", "同上页面点「查看」，32 位字符串", secret=True),
        "webhook_url": f.get("webhook_url") or _ask(
            "群机器人 Webhook 地址",
            "飞书群 → 设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook"),
    }

    # ---- 2) 调度器（必填，本地校验）----
    print("\n" + "-" * 68)
    print(" [2/3] 调度器鉴权（必填，HTTP 控制台与 worker 注册用）")
    print("-" * 68)
    print("  管理密码不接受弱口令，至少 12 位且含两类字符。")
    print("  历史坑：这个密码曾硬编码 \"123456\" 写在 modules.json 里随公开仓库外泄。")
    while True:
        pw = s.get("password") or ""
        if pw and not _pw_problem(pw):
            print("\n管理密码\n  已配置（%d 位），回车沿用；要换直接输入新密码" % len(pw))
            got = getpass.getpass("  > ").strip()
            pw = got or pw
        else:
            got = _ask("管理密码", "输入时不回显；直接回车 = 自动生成 24 位强随机",
                       secret=True, default="")
            pw = got or _gen(24)
            if not got:
                print("  → 已自动生成 24 位强随机密码")
        bad = _pw_problem(pw)
        if bad:
            print("  ✗ %s，请重新输入" % bad)
            continue
        break
    rk = s.get("registry_key") or _gen_hex(32)
    if not s.get("registry_key"):
        print("\nworker 注册密钥\n  → 已自动生成 32 位随机串")

    # ---- 3) treasury（选填）----
    print("\n" + "-" * 68)
    print(" [3/3] TreasuryDirect buybacks 凭据（选填）")
    print("-" * 68)
    print("  取自 treasurydirect 官网 buy-backs 页面公开脚本。不填也能装，")
    print("  只是 us_treasury_weekly 的 buybacks 段会降级跳过。")
    print("  直接回车跳过。")
    tid = t.get("client_id") or ""
    tsec = t.get("client_secret") or ""
    if not tid:
        got = input("  client_id > ").strip()
        if got:
            tid = got
            tsec = _ask("client_secret", "", secret=True)

    sec = {
        "feishu": f_new,
        "scheduler": {"password": pw, "registry_key": rk},
        "treasury": {"client_id": tid, "client_secret": tsec} if tid else {},
    }

    # ---------- 逐项校验，不过就不让装 ----------
    print("\n" + "-" * 68)
    print(" 校验")
    print("-" * 68)
    all_fails = []
    for ready, lines, fails in (
        _feishu_ready(sec["feishu"], a.skip_live),
        _scheduler_ready(sec["scheduler"]),
        _treasury_ready(sec["treasury"]),
    ):
        for ln in lines:
            print("  " + ln)
        all_fails += fails

    if all_fails:
        print("\n✗ 校验未通过，安装中止（共 %d 项）。请修正后重跑：python3 setup.py" % len(all_fails))
        return 1

    _save(sec)
    print("""
下一步：
  1. 自检：python3 setup.py --check         （退出码 0 才算装好）
  2. 冒烟：python3 case/_lib/runner.py --task pmi_export
  3. 确认飞书群收到消息后，再把各 case/<task>/task.json 的 enabled 改成 true
  4. 完整部署流程见 DEPLOY.md「敏感配置」一节
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
