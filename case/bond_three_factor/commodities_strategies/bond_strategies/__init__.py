"""债券策略模块：利率三因子周度预测 + Slope(10Y-2Y) 交易机会。

数据统一经 DataHub EDB 取数（bond_cgb / bond_funding / bond_macro / bond_ext），
策略层不再直连中债爬虫、AKShare 或 TuShare。

推送（``--push``）复用仓库外的私有路由与邮件模块，仓库内不含任何 webhook/凭据。
"""
from .run import main as run

__all__ = ["run"]
__version__ = "1.1.0"