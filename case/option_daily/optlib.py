# -*- coding: utf-8 -*-
"""optlib.py —— 在「框架进程内」安全地借 options_utility 的期权原语

问题
----
options_utility 的 ``calc.option_monitor`` 在**模块级**执行 ``from alert.trigger
import trigger``。而框架 ``case/_lib`` 自己有一个 ``alert.py``（同名但不同物，
是报警出口，不是包）。runner 把 ``_lib`` 放在 sys.path[0]，于是:

    import calc.option_monitor  →  from alert.trigger import trigger
                                →  'alert' 已经是 _lib/alert.py（模块非包）
                                →  ModuleNotFoundError: 'alert' is not a package

单跑 ``run_daily.py`` 时不会触发（那时没人先 import 框架的 alert），
但由 runner 拉起时必现 —— 表现是「IV/PCR 全 NA、GEX 算不出」，
而日志里只有一句 import 失败，很容易被当成数据问题。

做法
----
按需**临时**把 ``alert`` / ``alert.*`` 从 sys.modules 里摘出来，让 options_utility
的 ``alert/`` 包能正常解析，import 完再原样放回去。calC 模块已经把自己的
``trigger`` 绑定在模块属性上，之后摘掉 sys.modules 里的条目不影响它。
单线程顺序执行，没有并发窗口。

只借**计算原语**（bs_gamma / bs_iv），取数一律仍走 DataHub。
"""
from __future__ import annotations

import os
import sys

_CACHE = {}
_ALERT_KEYS = ("alert",)
_STATE = {"paths": None, "err": ""}


def optstack_paths():
    """options_utility 候选目录（存在才返回）。"""
    if _STATE["paths"] is not None:
        return _STATE["paths"]
    cands = []
    for name in ("OPTSTACK_DIR", "OPTIONS_UTILITY_DIR"):
        v = (os.environ.get(name) or "").strip()
        if v:
            cands.append(v)
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(os.path.dirname(here))          # …/stock-dashboard
    cands.append(os.path.join(proj, "options"))
    try:
        import paths as _p
        ws = _p.workspace_root()
        if ws:
            cands += [os.path.join(ws, "options"),
                      os.path.join(ws, "optstack", "options_utility"),
                      os.path.join(ws, "projects", "options_utility")]
        hub = _p.datahub_root()
        if hub:
            sib = os.path.dirname(hub)
            cands += [os.path.join(sib, "options"),
                      os.path.join(sib, "optstack", "options_utility")]
    except Exception:                                            # noqa: BLE001
        pass
    # 从 data_hub 实际位置往上找 <root>/options_utility（本机开发态布局）
    try:
        import data_hub as _dh
        cur = os.path.dirname(os.path.dirname(os.path.abspath(_dh.__file__)))
        for _ in range(4):
            cands += [os.path.join(cur, "options_utility"),
                      os.path.join(cur, "options")]
            nxt = os.path.dirname(cur)
            if nxt == cur:
                break
            cur = nxt
    except Exception:                                            # noqa: BLE001
        pass
    out, seen = [], set()
    for c in cands:
        if c and c not in seen and os.path.isdir(c):
            out.append(c)
            seen.add(c)
    _STATE["paths"] = out
    return out


def prepare_env():
    """把 options_utility 的位置写进 DataHub 认的两个环境变量。

    DataHub 的 IV 反解（``core.option_metrics._load_ou_bs_iv``）只认
    ``OPTIONS_UTILITY_ROOT`` / ``OPTSTACK_DIR``，并且**按 ``<root>/options_utility``
    与 ``<root>/options`` 拼**（要求两个里有一个含 ``calc/ev.py``）。它自己还会
    永久缓存结果（失败也缓存），所以必须**在它第一次调用之前**把变量设好。

    本函数不设 ``OPTSTACK_DIR``：那是「optstack 根」的语义，本项目里
    options_utility 就在它下面，混用会把 DataHub 指向
    ``<optstack>/options_utility/options_utility`` 这种不存在的路径。
    """
    if _STATE.get("env_done"):
        return _STATE.get("env_root") or ""
    roots = [d for d in optstack_paths() if os.path.isfile(os.path.join(d, "calc", "ev.py"))]
    if not roots:
        _STATE["env_done"] = True
        _STATE["env_root"] = ""
        return ""
    base = roots[0]
    parent = os.path.dirname(base)
    # 若目录本身不叫 options/options_utility，DataHub 拼不出路径，就造一个别名父级
    if os.path.basename(base) not in ("options", "options_utility"):
        _STATE["env_done"] = True
        _STATE["env_root"] = ""
        return ""
    os.environ["OPTIONS_UTILITY_ROOT"] = parent
    _STATE["env_done"] = True
    _STATE["env_root"] = parent
    return parent


class _Scoped(object):
    """临时把 options_utility 加进 sys.path 并摘掉同名 alert 模块。"""

    def __enter__(self):
        self._saved = {}
        self._added = []
        # ① 摘掉同名 alert（框架 _lib/alert.py）
        for k in list(sys.modules):
            if k in _ALERT_KEYS or k.startswith("alert."):
                self._saved[k] = sys.modules.pop(k)
        # ② options_utility 根**插到最前**：它的 alert/ 是包，框架的 alert.py 是模块。
        #    只 append 到末尾不够 —— 框架 case/_lib 排在前面，`import alert` 仍会
        #    命中 _lib/alert.py。作用域结束时会把这些路径摘掉并恢复 sys.modules，
        #    所以不会污染框架后续的 import。
        for root in optstack_paths():
            if root not in sys.path:
                sys.path.insert(0, root)
                self._added.append(root)
            for sub in ("calc", "calc/cn", "config"):
                p = os.path.join(root, sub)
                if os.path.isdir(p) and p not in sys.path:
                    sys.path.append(p)
                    self._added.append(p)
        return self

    def __exit__(self, *exc):
        # 收回临时路径：只在**本作用域加的**那几条，别人的顺序不动
        for p in self._added:
            try:
                sys.path.remove(p)
            except ValueError:
                pass
        # options_utility 自己的 alert.* 留在 sys.modules 里也无害（框架从不 import
        # alert.trigger），但为了让框架继续保持原状，先清掉再恢复被摘的那批。
        for k in list(sys.modules):
            if k in _ALERT_KEYS or k.startswith("alert."):
                del sys.modules[k]
        sys.modules.update(self._saved)
        return False


def _load(modname, attr):
    key = (modname, attr)
    if key in _CACHE:
        return _CACHE[key]
    mod = __import__(modname, fromlist=["__name__"])
    fn = getattr(mod, attr, None)
    if fn is None:
        raise AttributeError("%s 没有 %s" % (modname, attr))
    _CACHE[key] = fn
    return fn


def bs_gamma():
    """返回 options_utility 的 bs_gamma(S,K,T,r,sigma)。失败抛 RuntimeError。"""
    try:
        with _Scoped():
            return _load("calc.option_monitor", "bs_gamma")
    except Exception as e:                                       # noqa: BLE001
        raise RuntimeError("借 options_utility.bs_gamma 失败（%s）：%s: %s"
                           % (", ".join(optstack_paths()) or "无候选目录",
                              type(e).__name__, str(e)[:180]))


def ensure_option_iv():
    """让 DataHub 的 IV 反解可用。

    ``data_hub.core.option_metrics.calc_iv_for_chain`` 内部委托 options_utility 的
    ``bs_iv``；它在模块导入期就试着绑定，绑不上会把整条链的 IV 留成 0。
    所以在调用反解**之前**先按同样的作用域借一次 bs_iv，触发 DataHub 侧的
    惰性绑定。返回 True/False（能不能用），不抛。
    """
    try:
        with _Scoped():
            _load("calc.option_monitor", "bs_iv")
            # ⚠️ 关键：DataHub 的加载器在**首次调用**时把结果（含失败）永久缓存。
            # 上面的 _load 只解决了「本进程能 import」，DataHub 自己还会再走一遍
            # 它自己的 spec 加载（模块级 `from alert.trigger import trigger`
            # 会再次撞上框架的 alert.py）。所以必须在这个作用域里**主动调用**
            # 它的加载器，趁 alert 还在被摘掉的状态把正确实现钉进缓存。
            try:
                import data_hub.core.option_metrics as _om
                return _om._ou_bs_iv() is not None
            except Exception:                                    # noqa: BLE001
                return True
    except Exception:                                            # noqa: BLE001
        return False
