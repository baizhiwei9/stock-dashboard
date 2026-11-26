"""环流日报内部功能模块（全部走 data_hub/EDB，纯 Python 零 LLM）。

2026-11-13 重建：原先这 6 个模块依赖 ``/Coze/Drive/环流报告`` 目录，
该目录在本会话挂载范围内不可达，因此按原调用契约用 data_hub/EDB 重新实现，
调度仍由系统 crontab 负责，不依赖 CodeAct / Calendar。

模块清单
--------
- ``edb_helper``             data_hub/EDB 统一取数封装（其他模块唯一取数入口）
- ``pboc_liquidity_reader``  央行资金面判定（技术性扰动 / 结构性收紧 / 真收紧）
- ``cd_liquidity_monitor``   1Y AAA 同业存单流动性监控
- ``cd_gk_spread_monitor``   降准降息预期利差（1Y−3M 国开/存单，五档 + 场景）
- ``swap_demand_monitor``    1Y 掉期点 × 需求传导（rrr = R + e 框架）
- ``ust_liquidity_monitor``  美债利率–流动性传导监控
- ``usd_liquidity_gauge``    美元流动性水位仪
- ``usd_fingerprint``        美元流动性指纹侦查（A–F 归因）
- ``alert_book``             统一告警体系（警告 / 报警分级 + 汇总 + 邮件）

使用方式
--------
把本目录加入 ``sys.path`` 后即可按扁平模块名导入，与原调用代码一致：

    sys.path.insert(0, (__import__('os').environ.get('CASE_WORKSPACE') or _cp('/Coze/Drive/蓁宝/所有对话/主对话')) + '/环流日报/scripts/modules')
    from cd_liquidity_monitor import run_monitor, render_report
"""
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

__all__ = [
    'edb_helper',
    'pboc_liquidity_reader',
    'cd_liquidity_monitor',
    'cd_gk_spread_monitor',
    'swap_demand_monitor',
    'ust_liquidity_monitor',
    'usd_liquidity_gauge',
    'usd_fingerprint',
    'alert_book',
]
