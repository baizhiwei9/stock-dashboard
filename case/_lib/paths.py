#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case 层路径统一入口（唯一口径）。

为什么有这个文件
────────────────
case/*/ 下的脚本是从另一个 agent 的沙箱整体迁过来的，里面写死了一堆
``/home/work/dumate/<长id>/workspace/...`` 绝对路径。换机器后这些路径全部
失效，但多数被 try/except 包着，表现不是报错而是「某块数据一直空着」——
比直接崩更难发现。

本模块把 case 层用到的路径收敛成 6 个函数，统一走「环境变量 → 实际探测 →
兜底常量」三段式。调用方只拿路径字符串，存不存在自己判断（isdir/isfile），
本模块不抛异常、不改 sys.path（唯一例外是显式调 ensure_hub_on_path()）。

六个口径
────────
    datahub_root()     DataHub 仓库根，转调 stock-dashboard/datahub_path.py
                       —— 与 build.py / scheduler.py 完全同一口径，不另立规则
    workspace_root()   workspace 根（DataHub 的父目录那种布局）
    shared_dir()       workspace/shared          跨任务共用的小工具模块
    py_deps()          workspace/py_deps         第三方依赖目录
    fonts_dir()        workspace/.fonts          出图字体
    feishu_router()    workspace/feishu_router   飞书卡片路由
    pylibs()           workspace/pylibs          少量手装库（vmdpy/pywt 等）
    legacy_workspace() 老「蓁宝/所有对话/主对话」目录（飞书模块原家）
    feishu_dirs()      飞书卡片/推送模块候选目录（仓库内优先，云盘兜底）

环境变量覆盖
────────────
    DATA_HUB_PATH / DATAHUB_DIR / EDB_REPO   → datahub_root()（交给 datahub_path.py）
    CASE_WORKSPACE                           → workspace_root() 及其所有下游
    CASE_PY_DEPS / CASE_FONTS / CASE_SHARED / CASE_FEISHU_ROUTER  → 单项覆盖
    CASE_FEISHU_LIB                          → feishu_dirs() 最高优先级

排障
────
    python3 case/_lib/paths.py     # 打印六个口径的解析结果和是否存在
"""
import os

# 旧沙箱 workspace：只在探测时当**候选**用，不再作为默认值。
# 迁机后它通常整个不存在，留着是因为 py_deps / .fonts 这类东西
# 有时还在原处，能命中就不用重装。
_LEGACY_WORKSPACE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
_DEFAULT_HUB = "/opt/coze/.fuse_drive/dataHub_三级体系"

_HERE = os.path.dirname(os.path.abspath(__file__))          # …/case/_lib
_CASE_DIR = os.path.dirname(_HERE)                          # …/case
_PROJECT_DIR = os.path.dirname(_CASE_DIR)                   # …/stock-dashboard


# ── 小工具 ────────────────────────────────────────────────────────────────
def _env(*names):
    """取第一个非空环境变量，去空白。"""
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def _is_hub(path):
    """DataHub 根的特征：目录下有 data_hub/。"""
    return bool(path) and os.path.isdir(os.path.join(path, "data_hub"))


def _first_dir(*cands):
    for p in cands:
        if p and os.path.isdir(p):
            return p
    return ""


def _upfind(filename, start, depth=6):
    """从 start 往上最多 depth 层找 filename，返回其绝对路径或空串。"""
    cur = os.path.abspath(start)
    for _ in range(depth):
        cand = os.path.join(cur, filename)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


# ── 1. DataHub 根 ─────────────────────────────────────────────────────────
def _load_datahub_path_module():
    """把 stock-dashboard/datahub_path.py 当脚本 exec 进来，返回它的命名空间。

    不能直接 ``import datahub_path``：case 任务由 runner.py 拉起时 sys.path 里
    只有 case/_lib，没有仓库根；独立跑 case 脚本时更是什么都没有。所以向上找
    文件再 exec —— 保证与 build.py / scheduler.py 读的是同一份代码，改口径时
    只改一处。
    """
    p = _upfind("datahub_path.py", _HERE)
    if not p:
        return None
    ns = {"__file__": p, "__name__": "datahub_path"}
    try:
        with open(p, encoding="utf-8") as f:
            exec(compile(f.read(), p, "exec"), ns)
    except Exception:
        return None
    return ns if callable(ns.get("datahub_root")) else None


_DH_NS = None
_DH_TRIED = False


def datahub_root():
    """DataHub 仓库根。命中顺序与 stock-dashboard/datahub_path.py 完全一致。"""
    global _DH_NS, _DH_TRIED
    if not _DH_TRIED:
        _DH_NS = _load_datahub_path_module()
        _DH_TRIED = True
    if _DH_NS is not None:
        try:
            return _DH_NS["datahub_root"]()
        except Exception:
            pass
    # datahub_path.py 不在（case 目录被单独拷出去跑）时的降级：
    # 环境变量 → 仓库同级 → 兜底常量，规则与它保持一致。
    env = _env("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO")
    if _is_hub(env):
        return env
    for rel in ("../datahub-multi-tier", "datahub-multi-tier"):
        p = os.path.normpath(os.path.join(_PROJECT_DIR, rel))
        if _is_hub(p):
            return p
    return _DEFAULT_HUB


def ensure_hub_on_path(root=None):
    """把 DataHub 根塞进 sys.path 并返回该路径。

    这是本模块唯一会改 sys.path 的函数，且必须显式调用 —— 让「谁把 hub 挂进来」
    在调用点一眼可见，而不是 import 时偷偷发生。
    """
    import sys
    root = root or datahub_root()
    if root and root not in sys.path:
        sys.path.insert(0, root)
    return root


# ── 2. workspace 根 ──────────────────────────────────────────────────────
def workspace_root():
    """workspace 根。

    优先级：CASE_WORKSPACE → 仓库父目录（云盘布局：<ws>/stock-dashboard 与
    <ws>/datahub-multi-tier 并列）→ DataHub 的父目录 → 旧沙箱路径。
    """
    env = _env("CASE_WORKSPACE")
    if env:
        return env
    parent = os.path.dirname(_PROJECT_DIR)
    if parent and parent != _PROJECT_DIR:
        # 父目录里同时能看到 datahub 或其它同级仓库，才算 workspace
        if _is_hub(os.path.join(parent, "datahub-multi-tier")) or \
                os.path.isdir(os.path.join(parent, "precious-task")):
            return parent
    hub_parent = os.path.dirname(datahub_root())
    if hub_parent and os.path.isdir(hub_parent):
        return hub_parent
    return _LEGACY_WORKSPACE


# ── 3~6. workspace 下的共享资源 ───────────────────────────────────────────
def _under_ws(*rel, **kw):
    """先看环境变量，再在 workspace_root() 下按 rel 拼，最后试 extra 候选。"""
    env = _env(*kw.get("env_names", ()))
    if env:
        return env
    ws = workspace_root()
    cands = [os.path.join(ws, *rel)] if ws else []
    cands.extend(kw.get("extra", ()))
    hit = _first_dir(*cands)
    return hit or (cands[0] if cands else "")


def shared_dir():
    """workspace/shared —— 跨任务共用的小工具模块（如 report_stats）。"""
    return _under_ws("shared", env_names=("CASE_SHARED",),
                     extra=(os.path.join(_LEGACY_WORKSPACE, "shared"),))


def py_deps():
    """workspace/py_deps —— 第三方依赖目录。"""
    return _under_ws("py_deps", env_names=("CASE_PY_DEPS",),
                     extra=(os.path.join(_LEGACY_WORKSPACE, "py_deps"),))


def fonts_dir():
    """workspace/.fonts —— 出图中文字体。"""
    return _under_ws(".fonts", env_names=("CASE_FONTS",),
                     extra=(os.path.join(_LEGACY_WORKSPACE, ".fonts"),))


def feishu_router():
    """workspace/feishu_router —— 飞书卡片路由。"""
    return _under_ws("feishu_router", env_names=("CASE_FEISHU_ROUTER",),
                     extra=(os.path.join(_LEGACY_WORKSPACE, "feishu_router"),))


def pylibs():
    """workspace/pylibs —— 少量手装库（vmdpy/pywt 之类）。

    多数已在 py_deps 或 site-packages 里，所以本函数常返回空串；
    调用方按「有就加进 sys.path、没有就跳过」处理即可。
    """
    return _under_ws("pylibs", env_names=("CASE_PYLIBS",),
                     extra=(os.path.join(_LEGACY_WORKSPACE, "pylibs"),))


# 系统自带中文字体：workspace/.fonts 通常只是指向它们的软链，
# 迁机后 .fonts 整个丢掉也不会让出图失败。
_SYS_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def font_path(name="NotoSansSC-Regular.ttf"):
    """中文字体完整路径。workspace/.fonts → 系统 Noto CJK → 其它兜底。

    保证返回**存在**的文件，因为 PIL 的 ImageFont.truetype("") 会直接抛异常，
    调用方不该为了「字体没装」再写一层 try。真的一个都没有才返回空串。
    """
    d = fonts_dir()
    cands = [os.path.join(d, name)] if d else []
    cands.extend(_SYS_FONTS)
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return ""


# ── 7. 老「主对话」目录 + 飞书模块目录 ───────────────────────────────────
# case/*/ 里 20 余处脚本仍在 sys.path.insert 云盘上的
# 「蓁宝/所有对话/主对话」，目的几乎只有一个：import 飞书卡片/推送模块。
# 这些模块仓库里已有完全相同的副本（feishu_card_utils.py 207955B 与云盘 md5 一致、
# feishu_push.py 是薄壳、pboc_liquidity_reader / usd_fingerprint 在
# case/circulation_daily/scripts/modules 下），所以迁机后不该再依赖云盘。
_LEGACY_MAIN = "/Coze/Drive/蓁宝/所有对话/主对话"
_LEGACY_MAIN_ALT = "/opt/coze/.fuse_drive/蓁宝/所有对话/主对话"


def legacy_workspace():
    """老「主对话」目录。迁机后由 CASE_WORKSPACE 指到新位置。

    注意与 workspace_root() 的区别：workspace_root() 是「stock-dashboard 的
    父目录」（本机 /root/work），而这里指的是云盘上另一套 workspace
    （蓁宝/所有对话/主对话），两者不是一回事，不要合并。
    """
    env = _env("CASE_WORKSPACE")
    if env:
        return env
    return _first_dir(_LEGACY_MAIN, _LEGACY_MAIN_ALT) or _LEGACY_MAIN


def feishu_dirs():
    """飞书卡片/推送模块的候选目录（按优先级，只返回真实存在的）。

    1. CASE_FEISHU_LIB 环境变量
    2. 仓库内 case/sector_screening_v4 —— feishu_card_utils.py + feishu_push.py 壳
    3. 仓库内 case/circulation_daily/scripts/modules —— pboc_liquidity_reader 等
    4. 老「主对话」目录（原机兜底，迁机后通常不存在）
    """
    cands = []
    env = _env("CASE_FEISHU_LIB")
    if env:
        cands.append(env)
    cands.append(os.path.join(_CASE_DIR, "sector_screening_v4"))
    cands.append(os.path.join(_CASE_DIR, "circulation_daily", "scripts", "modules"))
    cands.append(legacy_workspace())
    out = []
    for p in cands:
        if p and os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def ensure_feishu_on_path():
    """把所有可用的飞书模块目录前置进 sys.path，返回优先级最高的那个。

    与 ensure_hub_on_path() 同性质：显式改 sys.path，调用点一眼可见。
    """
    import sys
    dirs = feishu_dirs()
    for p in reversed(dirs):          # reversed + insert(0) 保证 dirs[0] 在最前
        if p not in sys.path:
            sys.path.insert(0, p)
    return dirs[0] if dirs else ""


# ── 自检 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    rows = [
        ("datahub_root", datahub_root()),
        ("workspace_root", workspace_root()),
        ("shared_dir", shared_dir()),
        ("py_deps", py_deps()),
        ("fonts_dir", fonts_dir()),
        ("feishu_router", feishu_router()),
        ("pylibs", pylibs()),
        ("legacy_workspace", legacy_workspace()),
        ("feishu_dirs[0]", (feishu_dirs() or [""])[0]),
    ]
    for name, path in rows:
        mark = "存在" if path and os.path.isdir(path) else "缺失"
        print("%-16s %-4s %s" % (name, mark, path or "(空)"))
