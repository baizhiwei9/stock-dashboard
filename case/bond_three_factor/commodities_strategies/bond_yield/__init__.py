"""债券模块：国债收益率四维预测 + 曲线监控（日更）+ 债券涨跌归因面板。

数据口径：债券相关数据统一经 DataHub 取数
----------------------------------------
- 国债收益率（2/5/10/30 年）→ EDB 序列 ``RATE.CN.*``（中债官方口径）
- 中债国债全期限曲线（3月/6月/1年/3年/5年/7年/10年/30年）→ bond 子包
  ``data_hub.collectors.bond.chinabond_curve`` 的 ``GOVT`` 曲线
- 资金面 / OMO / 美债 / 政策预期代理利差 → EDB ``LIQ.CN.*`` / ``RATE.US.*`` /
  ``BOND.SPREAD.*``

DataHub 不可达时回退 ``data/bond_yield/snapshot/`` 本地快照，并在卡片与日志
标注「接口不可达回退本地快照」，不编造当日数值。

对外入口
--------
- 日更日报：``python -m commodities_strategies.bond_yield.run [--no-send]``
- 涨跌归因面板：``python -m commodities_strategies.bond_yield.analysis [--days N]``

说明：``run`` / ``reason_pack`` 均为惰性包装，导入本包不会连带加载 numpy/xgboost，
也不会在 ``python -m ...bond_yield.run`` 时重复导入子模块。
"""
from typing import Any, Dict


def run(*args: Any, **kwargs: Any):
    """执行日更日报主流程（等价于 ``run.main()``）。"""
    from .run import main
    return main(*args, **kwargs)


def reason_pack(days: int = 20) -> Dict[str, Any]:
    """构建债券涨跌归因事实面板。"""
    from .analysis import reason_pack as _pack
    return _pack(days)


def to_markdown(pack: Dict[str, Any]) -> str:
    """事实面板 → Markdown。"""
    from .analysis import to_markdown as _md
    return _md(pack)


__all__ = ["run", "reason_pack", "to_markdown"]
__version__ = "1.0.0"