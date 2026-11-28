# -*- coding: utf-8 -*-
"""宏观月度工程配置：路径与凭据全部「环境变量优先 → 仓内 _lib → 明确报错」。

换服务器可执行的前提（本文件负责）：
- 源码不出现宿主机绝对路径、不出现明文凭据；
- 数据/输出目录默认在 case 内（data/、out/），可用 MACRO_DATA_DIR / MACRO_OUT_DIR 覆盖；
- DataHub（含 data_hub 包）根目录由 case/_lib/libroots 统一解析（DATAHUB_ROOT 可覆盖）；
- 飞书凭据走 case/_lib/feishu_creds（环境变量 → ~/.config/stock-dashboard/secrets.json）；
- 邮件凭据走 case/_lib/app_secrets 的 email 段，键名兼容 auth_code / password。
"""
import os
import shutil
import sys

CASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_ROOT = os.path.dirname(CASE_DIR)          # case/
REPO_ROOT = os.path.dirname(CASE_ROOT)         # 仓库根

_LIB = os.path.join(CASE_ROOT, "_lib")
if os.path.isdir(_LIB) and _LIB not in sys.path:
    sys.path.insert(0, _LIB)

DATA_DIR = os.environ.get("MACRO_DATA_DIR") or os.path.join(CASE_DIR, "data")
OUT_DIR = os.environ.get("MACRO_OUT_DIR") or os.path.join(CASE_DIR, "out")


# ---------- DataHub / EDB ----------
def edb_repo():
    """返回含 data_hub 包的工程目录；解析失败抛带操作指引的异常。"""
    env = os.environ.get("EDB_REPO")
    if env:
        return env
    try:
        from hub_root import hub_root                   # stock-dashboard/case/_lib
        root = hub_root(os.path.dirname(os.path.abspath(__file__)))
        if root:
            return str(root)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from hub_paths import resolve_hub_root          # precious-task/case/_lib
        return str(resolve_hub_root())
    except Exception as exc:                            # noqa: BLE001
        raise RuntimeError(
            "找不到 data_hub 工程目录：请设置 DATA_HUB_PATH / DATAHUB_DIR / EDB_REPO "
            "（指向含 data_hub/ 的目录）。原始错误：%s" % str(exc)[:160]
        ) from exc


def py_deps():
    """可选的共享依赖目录（历史沙箱 py_deps / pylibs），不存在返回空串。"""
    for k in ("PY_DEPS_ROOT", "PYLIBS_DIR"):
        v = os.environ.get(k)
        if v and os.path.isdir(v):
            return v
    for name in ("py_deps", "pylibs"):
        p = os.path.join(os.path.dirname(REPO_ROOT), name)
        if os.path.isdir(p):
            return p
    return ""


# ---------- 飞书 ----------
def _feishu():
    try:
        import feishu_creds                              # noqa: WPS433
        return (os.environ.get("FEISHU_APP_ID") or feishu_creds.APP_ID,
                os.environ.get("FEISHU_APP_SECRET") or feishu_creds.APP_SECRET,
                os.environ.get("FEISHU_WEBHOOK_URL") or feishu_creds.WEBHOOK_URL)
    except Exception:                                    # noqa: BLE001
        return (os.environ.get("FEISHU_APP_ID", ""),
                os.environ.get("FEISHU_APP_SECRET", ""),
                os.environ.get("FEISHU_WEBHOOK_URL", ""))


FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_WEBHOOK = _feishu()


# ---------- 邮件 ----------
def _email_section():
    try:
        import app_secrets                              # noqa: WPS433
        return app_secrets.section("email") or {}
    except Exception:                                    # noqa: BLE001
        return {}


_EMAIL = _email_section()

# 钉钉企业邮箱 CLI：环境变量 → PATH → 家目录常见位置（缺失时自动回退 163 SMTP）
DWS_CLI = (os.environ.get("DWS_CLI")
           or shutil.which("dws")
           or os.path.expanduser("~/.local/bin/dws"))

EMAIL_FROM = os.environ.get("EMAIL_FROM") or _EMAIL.get("from") or _EMAIL.get("sender") or ""
EMAIL_TO = os.environ.get("EMAIL_TO") or _EMAIL.get("to") or _EMAIL.get("recipients") or ""
EMAIL_HOST = os.environ.get("EMAIL_HOST") or _EMAIL.get("smtp_host") or "smtp.163.com"
EMAIL_PORT = int(os.environ.get("EMAIL_PORT") or _EMAIL.get("smtp_port") or 25)
EMAIL_USER = os.environ.get("EMAIL_USER") or _EMAIL.get("username") or _EMAIL.get("sender") or ""


def get_auth_code():
    """SMTP 授权码：环境变量优先，其次统一凭据库（不落盘、不打印）。"""
    return (os.environ.get("EMAIL_AUTH_CODE")
            or _EMAIL.get("auth_code") or _EMAIL.get("password") or "")


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)