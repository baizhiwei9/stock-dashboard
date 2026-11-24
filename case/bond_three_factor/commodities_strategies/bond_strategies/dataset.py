"""债券三因子数据集：全部经 DataHub EDB 取数并组装周频面板。"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from ..config import Settings
from . import edb

TERMS = ["2年", "5年", "7年", "10年", "30年"]
YIELD_HISTORY_START = "2010-01-01"   # 收益率回补起点（中债官方曲线 2010 起可一次取全）
FOCUS = ["2年", "5年", "10年", "30年"]
SLOPE_TERMS = ["1年", "2年", "5年", "10年", "30年"]

# 曲期限 → DataHub 序列号。六条期限统一走 bond 子包的中债官方曲线
# （data_hub.collectors.bond.treasury_curve，含 AK 宽表缺失的 2 年 / 7 年），
# 同源同口径，不做跨源拼接，避免曲线水平跳变污染 PCA。
YIELD_CODES = {"1年": "RATE.CN.1Y", "2年": "RATE.CN.2Y", "5年": "RATE.CN.5Y",
               "7年": "RATE.CN.7Y", "10年": "RATE.CN.10Y", "30年": "RATE.CN.30Y"}

# 外部变量：美债由 bond 子包承接（FRED 官方口径），汇率/资金面走 EDB 对应序列。
EXT_CODES = {"US10Y": "RATE.US.10Y", "US30Y": "RATE.US.30Y", "US2Y": "RATE.US.2Y",
             "USDCNY": "CN.FX.SPOT.USDCNY", "SHIBOR_1W": "LIQ.CN.SHIBOR.1W"}

# 日频宏观 / 流动性：原油用布伦特（DataHub 无 SC 连续合约口径），资金面用隔夜 SHIBOR。
DAILY_CODES = {"INTERBANK_RATE": "LIQ.CN.SHIBOR.ON", "OIL_SC": "GLOBAL.BRENT"}

# 贵金属外部约束：上金所现货 Au99.99（DataHub 2026-11-17 新增序列，元/克）。
# 以前贵金属由策略侧直连 AKShare 取，现统一由 DataHub 提供；是否进入模型特征需重训验证，
# 因此只作为可取的序列暴露，不默认拼进周频面板。
GOLD_CODES = {"AU9999": "GLOBAL.GOLD.AU9999"}

# 月频宏观：DataHub 序列号 → 面板列名。
MONTH_CODES = {"CN.M2.YOY": "M2_YoY", "CN.CPI.YOY": "CPI_YoY",
               "CN.PPI.YOY": "PPI_YoY", "CN.PMI.MFG": "PMI"}

# 模型候选特征；实际训练时按面板里真实存在的列收窄（见 build_weekly），
# 缺失的特征不静默补零，避免把「没有数据」当成「数值为 0」。
MAC_COLS = ["US10Y", "US30Y", "US2Y", "USDCNY", "SHIBOR_1W",
            "M2_YoY", "CPI_YoY", "PPI_YoY", "INTERBANK_RATE", "OIL_SC"]


def load_yields(settings: Optional[Settings] = None, filled: bool = False) -> pd.DataFrame:
    """国债收益率（EDB）。filled=True 时按「工作日 ∪ 报价日」网格前向填充，
    供日频 Slope 计算使用；周频聚合应保持原始报价日（resample.last 自动取周内最后有效值）。"""
    data: Dict[str, pd.Series] = {}
    for cn, code in YIELD_CODES.items():
        # 首次运行按 2010 起点回补：bond 子包的中债官方曲线一次可取全历史，
        # 之后由 EDB 增量维护；没有这段历史，250 周滚动 PCA 无法起算。
        s = edb.series(settings, code, start=YIELD_HISTORY_START)
        if len(s) == 0 or s.index.min() > pd.Timestamp(YIELD_HISTORY_START) + pd.Timedelta(days=30):
            # 缓存只覆盖近期（新登记序列常见）时强制回补一次，避免长期缺 7 年等期限
            s = edb.series(settings, code, start=YIELD_HISTORY_START, force=True)
        if len(s):
            data[cn] = s
    df = pd.DataFrame(data).sort_index()
    if len(df) and filled:
        grid = pd.bdate_range(df.index.min(), df.index.max()).union(df.index)
        df = df.reindex(grid).ffill()
    return df[[c for c in YIELD_CODES if c in df.columns]]


def load_external(settings: Optional[Settings] = None) -> pd.DataFrame:
    data = {}
    for name, code in EXT_CODES.items():
        s = edb.series(settings, code)
        if len(s):
            data[name] = s
    return pd.DataFrame(data).sort_index()


def load_daily_macro(settings: Optional[Settings] = None) -> pd.DataFrame:
    data = {}
    for name, code in DAILY_CODES.items():
        s = edb.series(settings, code)
        if len(s):
            data[name] = s
    return pd.DataFrame(data).sort_index()


def load_monthly_macro(settings: Optional[Settings] = None) -> pd.DataFrame:
    data = {}
    for code, name in MONTH_CODES.items():
        s = edb.series(settings, code)
        if len(s):
            s.index = pd.to_datetime(s.index)
            data[name] = s
    return pd.DataFrame(data).sort_index()


def load_dr007(settings: Optional[Settings] = None) -> pd.Series:
    """DR007 代理：优先 DataHub 的 DR007（FDR007 代理口径），缺失时回退隔夜 SHIBOR。"""
    s = edb.series(settings, "LIQ.CN.DR007")
    return s if len(s) else edb.series(settings, "LIQ.CN.SHIBOR.ON")


def build_weekly(settings: Optional[Settings] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """组装周频面板：国债周频锚 + 外部变量 + 日频宏观 + 月频宏观（前向填充）。"""
    y = load_yields(settings)
    if y.empty:
        raise RuntimeError("EDB 无国债收益率序列，请先运行 bond 通道或迁移脚本")
    yw = y[TERMS].resample("W-FRI").last()
    ext_w = load_external(settings).resample("W-FRI").last()
    daily_w = load_daily_macro(settings).resample("W-FRI").last()
    monthly = load_monthly_macro(settings)
    if len(monthly):
        # 月频宏观：按发布月（月初标记）前向填充到周频
        m_w = monthly.sort_index().reindex(yw.index, method="ffill")
    else:
        m_w = pd.DataFrame(index=yw.index)
    out = yw.join(ext_w, how="left").join(daily_w, how="left").join(m_w, how="left")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out, yw


def load_gold(settings: Optional[Settings] = None) -> pd.DataFrame:
    """贵金属外部约束序列（上金所现货 Au99.99，元/克），全部经 DataHub EDB。

    DataHub 未注册该序列或取数失败时返回空表，由调用方如实标注缺数据，
    不回退到 AKShare 直取，也不补零。
    """
    data = {}
    for name, code in GOLD_CODES.items():
        s = edb.series(settings, code)
        if len(s):
            data[name] = s
    return pd.DataFrame(data).sort_index()
