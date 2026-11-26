# -*- coding: utf-8 -*-
"""DataHub / EDB 取数入口（本工程唯一的取数通道）
==================================================

原则
----
本工程**不再直连 TuShare 网关、不再自建 requests 调用**：所有数据（两融汇总、
个股两融明细、每日指标、ETF 份额与价格、指数日线、涨跌停家数、股票基础信息、
申万行业映射、交易日历）一律经 DataHub 的 EDB 数据集接口取得。

- 取数实现：``data_hub/edb/fetchers/market_flow.py``（数据集口径）
- 取数通道：``data_hub/core/sources/tushare_source.py`` 的 ``TushareSource.query``
  （data_hub 既有统一数据源：统一凭据、代理与降级、全局串行限流；本工程不自建 HTTP）
- 接口定义：``data_hub/edb/datasets.py`` → ``edb.get_dataset`` / ``get_dataset_result``

仓库定位
--------
按 ``EDB_REPO`` 环境变量 → 工作区内 ``projects/datahub-three-tier`` →
工作区内任一含 ``data_hub`` 的部署副本（如 ``ses_*/_dh``）依次探测；
同时把工作区 ``py_deps`` 加入 ``sys.path``（DataHub 部分数据源依赖其内置库）。

取数失败的语义
--------------
``get_dataset_result`` 返回 ``ok=False`` 时本模块**抛 RuntimeError**，
由 run_daily 统一记录并中止该子步骤——不返回空 DataFrame 冒充「当日无数据」，
避免把断供写成「融资余额为 0」这种假结论。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(HERE))   # margin_monitor 位于 <workspace>/<session>/ 下
PY_DEPS = os.path.join(WORKSPACE, "py_deps")

_REPO_ENV = os.environ.get("EDB_REPO", "").strip()
_CANDIDATES = [
    _REPO_ENV,
    os.path.join(WORKSPACE, "projects", "datahub-three-tier"),
]
if not _REPO_ENV:  # 兜底：工作区内任意含 data_hub 的部署副本
    try:
        for name in sorted(os.listdir(WORKSPACE)):
            if name.startswith("ses_"):
                p = os.path.join(WORKSPACE, name, "_dh")
                if os.path.isdir(os.path.join(p, "data_hub")):
                    _CANDIDATES.append(p)
    except OSError:
        pass

EDB_REPO = next((p for p in _CANDIDATES if p and os.path.isdir(os.path.join(p, "data_hub"))), "")
_READY = False
_edb = None


class DataUnavailable(RuntimeError):
    """数据取数失败（上游断供 / 接口无该日数据 / 参数错误）。"""


def _ensure() -> Any:
    """把 DataHub 仓库与 py_deps 加入 sys.path，并导入 ``data_hub.edb``。"""
    global _READY, _edb
    if _READY:
        return _edb
    if not EDB_REPO:
        raise DataUnavailable(
            "未找到 DataHub 仓库：请用 EDB_REPO 指定含 data_hub 的目录，"
            f"已尝试 {[p for p in _CANDIDATES if p]}"
        )
    for p in (EDB_REPO, PY_DEPS):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    from data_hub import edb  # noqa: E402  （必须在路径注入之后导入）

    _edb = edb
    _READY = True
    return edb


def dataset(dataset_id: str, **params) -> pd.DataFrame:
    """取一个 EDB 数据集；失败抛 :class:`DataUnavailable`（带接口原始错误）。

    Args:
        dataset_id: EDB 数据集编号，如 ``CN.MARKET.MARGIN.DETAIL``。
        **params: 数据集参数（``trade_date`` / ``codes`` / ``start_date`` / ``end_date``）。

    Returns:
        pandas.DataFrame: 数据集结果（可能为空表，空表语义见各数据集说明）。
    """
    edb = _ensure()
    r = edb.get_dataset_result(dataset_id, **params)
    if not r["ok"]:
        raise DataUnavailable(f"{dataset_id} 取数失败：{r['error']}")
    return r["data"]


def dataset_result(dataset_id: str, **params) -> Dict[str, Any]:
    """取数据集并返回完整结果字典（不抛异常），供需要记录来源/失败的场景。"""
    edb = _ensure()
    return edb.get_dataset_result(dataset_id, **params)


# ---------------- 业务封装（列名与既有工程保持一致） ----------------
def trade_calendar(start_date: str, end_date: str) -> List[int]:
    """交易日历（开盘日），返回升序 ``int`` 列表。"""
    df = dataset("CN.MARKET.CAL.TRADE", start_date=str(start_date), end_date=str(end_date))
    if df.empty:
        raise DataUnavailable("交易日历为空")
    return [int(x) for x in df["cal_date"]]


def margin_summary(start_date: str, end_date: str) -> pd.DataFrame:
    """两融汇总（分交易所原始行，含 exchange_id）。"""
    return dataset("CN.MARKET.MARGIN.SUMMARY", start_date=str(start_date), end_date=str(end_date))


def margin_detail_day(trade_date: str) -> pd.DataFrame:
    """单日个股两融明细（列：ts_code/trade_date/rzye/rqye/rzmre/rzche/rzrqye/rqyl/rqchl）。"""
    return dataset("CN.MARKET.MARGIN.DETAIL", trade_date=str(trade_date))


def daily_basic_day(trade_date: str) -> pd.DataFrame:
    """单日全市场每日指标（列含 close/circ_mv/total_mv/float_share/turnover_rate）。"""
    return dataset("CN.MARKET.BASIC.DAILY", trade_date=str(trade_date))


def limit_counts_day(trade_date: str) -> Dict[str, Any]:
    """单日涨跌停家数：``{trade_date, up_limit, down_limit, broken, empty}``。"""
    df = dataset("CN.MARKET.LIMIT.COUNTS", trade_date=str(trade_date))
    if df.empty:
        raise DataUnavailable(f"涨跌停家数为空：{trade_date}")
    return df.iloc[0].to_dict()


def stock_basic() -> pd.DataFrame:
    """全市场股票基础信息。"""
    return dataset("CN.MARKET.STOCK.BASIC")


def industry_map() -> pd.DataFrame:
    """申万一级行业映射（ts_code / l1_name / name）。"""
    return dataset("CN.MARKET.INDUSTRY.SW")


def etf_share(codes: Sequence[str], start_date: str, end_date: str) -> pd.DataFrame:
    """多只 ETF 份额（fund_share 口径）。"""
    return dataset("CN.MARKET.ETF.SHARE", codes=list(codes),
                   start_date=str(start_date), end_date=str(end_date))


def etf_price(codes: Sequence[str], start_date: str, end_date: str) -> pd.DataFrame:
    """多只 ETF 行情（fund_daily 优先，缺失退 fund_nav）。"""
    return dataset("CN.MARKET.ETF.PRICE", codes=list(codes),
                   start_date=str(start_date), end_date=str(end_date))


def index_daily(codes: Sequence[str], start_date: str, end_date: str) -> pd.DataFrame:
    """多只指数日线（含 amount 千元）。"""
    return dataset("CN.MARKET.INDEX.DAILY", codes=list(codes),
                   start_date=str(start_date), end_date=str(end_date))


# ---------------- 兼容层：老脚本的 (fields, items) 调用形式 ----------------
_API_TO_DATASET = {
    "trade_cal": "CN.MARKET.CAL.TRADE",
    "margin": "CN.MARKET.MARGIN.SUMMARY",
    "margin_detail": "CN.MARKET.MARGIN.DETAIL",
    "daily_basic": "CN.MARKET.BASIC.DAILY",
    "fund_share": "CN.MARKET.ETF.SHARE",
    "fund_daily": "CN.MARKET.ETF.PRICE",
    "fund_nav": "CN.MARKET.ETF.PRICE",
    "index_daily": "CN.MARKET.INDEX.DAILY",
    "limit_list_d": "CN.MARKET.LIMIT.COUNTS",
    "stock_basic": "CN.MARKET.STOCK.BASIC",
    "index_classify": "CN.MARKET.INDUSTRY.SW",
    "index_member_all": "CN.MARKET.INDUSTRY.SW",
}


def call(api: str, params: Optional[Dict[str, Any]] = None) -> Tuple[List[str], List[list]]:
    """兼容老调用形式：返回 ``(fields, items)``，数据仍来自 DataHub/EDB。

    仅供历史诊断脚本使用，正式流水线请直接调用上面的业务函数。
    接口未映射时报错而不是静默返回空。
    """
    params = dict(params or {})
    did = _API_TO_DATASET.get(api)
    if did is None:
        raise DataUnavailable(f"未映射的取数接口: {api}（请先在 EDB 数据集层登记）")
    # 老参数名差异：fund_share/fund_daily 用 ts_code 单只，数据集用 codes 列表
    if "ts_code" in params and "codes" not in params:
        params["codes"] = [params.pop("ts_code")]
    # trade_cal 多一个 exchange 参数，数据集固定 SSE
    params.pop("exchange", None)
    df = dataset(did, **params)
    return list(df.columns), df.values.tolist()


def probe() -> Dict[str, Any]:
    """连通性自检：返回仓库路径、数据集数量与交易日历最小可用性。"""
    edb = _ensure()
    out = {"repo": EDB_REPO, "datasets": len(edb.list_datasets())}
    try:
        out["calendar_days"] = len(trade_calendar("20261101", "20261114"))
        out["ok"] = True
    except Exception as exc:
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(probe(), ensure_ascii=False, indent=1))