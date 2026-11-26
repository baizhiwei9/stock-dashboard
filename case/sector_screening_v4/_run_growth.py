# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()
import sys, os
sys.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器/scripts'))
os.chdir(_cp('/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器'))
import importlib.util
spec = importlib.util.spec_from_file_location('gs', _cp('/Coze/Drive/蓁宝/所有对话/主对话/成长股选股器/scripts/growth_screener_dual.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
