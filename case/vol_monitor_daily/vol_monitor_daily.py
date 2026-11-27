#!/usr/bin/env python3
"""
股指波动率监控日报 - 纯Python零LLM版本
每个工作日午间运行，采集A股全市场波动率数据并输出分析报告。

数据来源：
  - akshare-futures-options skill CLI (期权IV/期货行情)
  - stock-data-skill CLI kline_v2 (历史K线 → HV计算)

输出：
  - output/vol_monitor_YYYYMMDD.json (结构化数据)
  - output/vol_monitor_YYYYMMDD.md  (人读报告)

用法：
  python3 vol_monitor_daily.py [--date YYYYMMDD] [--push]
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


import json
import math
import signal
import os
import re
import subprocess
import sys
import logging
from trading_day_check import check_trading_day
from daily_email import send_daily_report_email
check_trading_day()
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import (  # noqa: E402
    feishu_webhook as _feishu_webhook,
    smtp_password as _smtp_password,
    tushare_token as _tushare_token,
)
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()


# ──────────────────────────── 配置 ────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
SKILLS_DIR = Path(_cp("/Coze/Drive/股指波动率/.skills"))

AK_OPTIONS_CLI = SKILLS_DIR / "skill_akshare-futures-options/bin/_cli_wrapper.py"
STOCK_DATA_CLI = SKILLS_DIR / "skill_stock-data-skill/bin/_cli_wrapper.py"

# 历史波动率回溯天数（需要多拉一些以确保足够交易日）
KLINE_LOOKBACK_DAYS = 200  # 日历日，确保有120+交易日

# 标的定义：code用于kline_v2, display_name用于报告
UNDERLYINGS = {
    "hs300":  {"code": "000300.XSHG",  "name": "沪深300",    "etf": "510300"},
    "zz500":  {"code": "000905.XSHG",  "name": "中证500",    "etf": "510500"},
    "zz1000": {"code": "000852.XSHG",  "name": "中证1000",   "etf": None},
    "cyb":    {"code": "399006.XSHE",  "name": "创业板指",   "etf": "159915"},
    "kc50":   {"code": "000688.XSHG",  "name": "科创50",     "etf": "588000"},
    "zz2000": {"code": "563300.XSHG",  "name": "中证2000",   "etf": "563300"},  # 用ETF代替指数
}

# 期货定义
FUTURES = {
    "IF": {"name": "IF沪深300期货", "underlying": "hs300", "sina_symbol": "nf_IF0"},
    "IC": {"name": "IC中证500期货", "underlying": "zz500", "sina_symbol": "nf_IC0"},
    "IM": {"name": "IM中证1000期货", "underlying": "zz1000", "sina_symbol": "nf_IM0"},
}

# 期权品种定义
OPTIONS = {
    "510300": {"name": "沪深300ETF期权", "underlying": "hs300", "exchange": "sse", "has_iv": True, "iv_safe_range": (0.12, 0.25)},
    "510500": {"name": "中证500ETF期权", "underlying": "zz500", "exchange": "sse", "has_iv": True, "iv_safe_range": (0.14, 0.30)},
    "MO":     {"name": "中证1000股指期权", "underlying": "zz1000", "exchange": "cffex", "has_iv": True, "iv_safe_range": (0.14, 0.30)},
    "159915": {"name": "创业板ETF期权", "underlying": "cyb", "exchange": "szse", "has_iv": True, "iv_safe_range": (0.18, 0.35)},
    "588000": {"name": "科创50ETF期权", "underlying": "kc50", "exchange": "sse", "has_iv": True, "iv_safe_range": (0.20, 0.40)},
    "563300": {"name": "中证2000ETF期权", "underlying": "zz2000", "exchange": "sse", "has_iv": False, "iv_safe_range": (0.18, 0.40)},
}

# ──────────────────────────── 日志 ────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vol_monitor")


# ──────────────── data_hub 统一数据层（期权链 / 自算 VIX / 统一期权指标） ────────────────
# 单一真源：期权链取数、自算 VIX（CBOE 分钟到期法）、ATM IV 统一算法全部走 data_hub；
# 本脚本不再直连 akshare QVIX 现成值 / tushare opt_basic·opt_daily / 新浪期权链爬虫。
# data_hub 代码根目录（兼容 /Coze/Drive 与 fuse 别名两种挂载）
for _dh_cand in (
    _HUB_ROOT,
    _cp("/opt/coze/.fuse_drive/蓁宝/所有对话/主对话/codeact/scripts"),
):
    if os.path.isdir(os.path.join(_dh_cand, "data_hub")) and _dh_cand not in sys.path:
        sys.path.insert(0, _dh_cand)

_DATAHUB_READY = False
try:
    from data_hub.collectors.vix_history import (
        get_all_vix_percentiles as _dh_get_all_vix_percentiles,
    )
    from data_hub.collectors.option_chain_realtime import (
        get_realtime_option_chain as _dh_get_realtime_option_chain,
    )
    from data_hub.collectors.option_chain import (
        get_option_chain as _dh_get_option_chain,
    )
    from data_hub.option_metrics import (
        standardize_contracts as _dh_standardize_contracts,
        calc_iv_for_chain as _dh_calc_iv_for_chain,
        get_atm_iv as _dh_get_atm_iv,
    )
    from data_hub.tushare_config import get_tushare_pro as _dh_get_tushare_pro  # noqa: F401
    from data_hub.collectors.stocks.index_daily import (
        get_daily_with_realtime as _dh_get_daily_with_realtime,
    )
    from data_hub.collectors.stocks.minute_bar import (
        get_minute_bar as _dh_get_minute_bar,
    )
    from data_hub.collectors.misc.option_chain_realtime import (
        get_realtime_futures as _dh_get_realtime_futures,
    )
    _DATAHUB_READY = True
    log.info("data_hub 统一数据层已加载")
except Exception as _e:  # noqa: BLE001
    _DATAHUB_READY = False
    log.warning("data_hub 加载失败: %s（期权链路不可用）", _e)

# 期权品种键 → data_hub 自算 VIX 键（六宽基，MO=中证1000股指）
_VIX_KEY_MAP = {
    "510300": "300ETF",
    "510500": "500ETF",
    "MO":     "MO",
    "159915": "创业板ETF",
    "588000": "科创50ETF",
}
# 期权品种键 → data_hub 期权链键（与 VIX 键一致）
_CHAIN_KEY_MAP = dict(_VIX_KEY_MAP)

# ──────────────────────────── CLI调用 ────────────────────────────

def run_cli(cli_path: Path, *args: str, timeout: int = 30) -> Optional[str]:
    """执行CLI wrapper，返回stdout文本或None。自动处理GBK/UTF-8编码。"""
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path)] + list(args),
            capture_output=True, timeout=timeout,
        )
        # 先尝试UTF-8，失败则GBK（新浪期货数据是GBK编码）
        raw = result.stdout
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                output = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            output = raw.decode("utf-8", errors="replace")

        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace")[:200]
            log.warning("CLI %s 返回非零: %s | stderr: %s", args[0], result.returncode, stderr_text)
            # 即使非零返回，如果有输出也返回（有些CLI带warn但数据正常）
            if not output.strip():
                return None
        return output
    except subprocess.TimeoutExpired:
        log.warning("CLI %s 超时 (%ds)", args[0], timeout)
        return None
    except Exception as e:
        log.warning("CLI %s 异常: %s", args[0], e)
        return None


def _to_ts_code(code: str) -> str:
    """聚宽风格代码转 tushare 格式：000300.XSHG -> 000300.SH / 399006.XSHE -> 399006.SZ"""
    c = str(code).strip().upper()
    if c.endswith(".XSHG"):
        return c[:-5] + ".SH"
    if c.endswith(".XSHE"):
        return c[:-5] + ".SZ"
    return c


def _fnum(v):
    """宽松转 float，失败返回 None（不造数）。"""
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# data_hub.get_daily_with_realtime 回传的实时口径元信息，供上层汇总
_KLINE_RT_META: dict = {}


def fetch_kline(code: str, start_date: str, end_date: str) -> list[dict]:
    """获取日线K线，**含当日实时补齐**。

    统一走 data_hub.get_daily_with_realtime —— 内部=日线历史 + 当日实时快照拼 bar。
    本脚本不再直连新浪 / CLI / akshare，也不再自己拼当日 bar，
    保证全系统（期权、波动率、技术分析…）用同一份口径。
    """
    ts_code = _to_ts_code(code)
    if not _DATAHUB_READY:
        log.error("data_hub 不可用，K线获取失败（不降级直连，避免口径分叉）: %s", ts_code)
        return []
    try:
        df = _dh_get_daily_with_realtime(
            ts_code=ts_code, start_date=start_date, end_date=end_date,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("data_hub K线获取异常 %s: %s", ts_code, e)
        return []
    if df is None or len(df) == 0:
        log.warning("data_hub K线为空: %s", ts_code)
        return []

    _KLINE_RT_META[code] = (df.attrs or {}).get("realtime") or {}

    has_vol = "vol" in df.columns
    out = []
    for _, row in df.iterrows():
        d = row.get("trade_date") if "trade_date" in df.columns else row.get("date")
        d = str(d)
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        else:
            d = d[:10]
        out.append({
            "date": d,
            "open": _fnum(row.get("open")),
            "high": _fnum(row.get("high")),
            "low": _fnum(row.get("low")),
            "close": _fnum(row.get("close")),
            "volume": _fnum(row.get("vol")) if has_vol else _fnum(row.get("volume")),
        })
    _rt = _KLINE_RT_META.get(code) or {}
    log.info("  data_hub K线 %s → %d条%s", ts_code, len(out),
             "（含当日实时）" if _rt.get("is_realtime") else "（收盘口径）")
    return out



def fetch_futures_spot(symbols: list[str]) -> dict[str, dict]:
    """期货实时行情，统一走 data_hub.get_realtime_futures（option_chain_realtime）。

    该接口早已存在（OI/基差监控在用），本脚本不再自建取数。
    入参沿用新浪写法 nf_IF0；返回 key 保持原样，下游无需改动。
    """
    if not _DATAHUB_READY:
        log.error("data_hub 不可用，期货实时行情获取失败")
        return {}

    codes = [str(x).replace("nf_", "").upper() for x in symbols]
    try:
        raw = _dh_get_realtime_futures(codes)
    except Exception as e:  # noqa: BLE001
        log.warning("data_hub 期货实时行情异常: %s", e)
        return {}

    out: dict[str, dict] = {}
    for orig in symbols:
        key = str(orig).replace("nf_", "").upper().rstrip("0123456789")
        d = raw.get(key)
        if not d:
            continue
        out[orig] = {
            "last": d.get("last") or 0,
            "prev_settle": None,
            "low": None,
            "high": None,
            "volume": d.get("volume") or 0,
            "oi": d.get("open_interest") or 0,
            "date": d.get("quote_date") or "",
            "time": d.get("quote_time") or "",
        }

    if out:
        log.info("  data_hub 期货实时行情 %d/%d 命中（%s）",
                 len(out), len(symbols), next(iter(out.values())).get("time") or "")
    else:
        log.warning("data_hub 期货实时行情全部未命中: %s", symbols)
    return out
def _resolve_chain_key(underlying_code: str, opt_exchange: str,
                       etf_code: str = None) -> Optional[str]:
    """把本脚本的标的标识解析为 data_hub 期权链键（六宽基）。"""
    for cand in (etf_code, underlying_code):
        if not cand:
            continue
        c = str(cand).strip().upper().split(".")[0]
        if c in _CHAIN_KEY_MAP:
            return _CHAIN_KEY_MAP[c]
    return None


def _atm_iv_detail(contracts, spot):
    """从标准合约取最近到期月的平价 call/put，返回 (strike, dte, (call, put))。"""
    cand = [c for c in contracts if c.iv > 0 and c.dte > 3]
    if not cand:
        cand = [c for c in contracts if c.iv > 0 and c.dte >= 0]
    if not cand:
        return None, None, None
    near = sorted(set(c.expiry for c in cand))[0]
    pool = [c for c in cand if c.expiry == near]
    calls = [c for c in pool if c.opt_type == "C"]
    puts = [c for c in pool if c.opt_type == "P"]
    atm_call = min(calls, key=lambda c: abs(c.strike - spot)) if calls else None
    atm_put = min(puts, key=lambda c: abs(c.strike - spot)) if puts else None
    strike = None
    if atm_call and atm_put:
        strike = (atm_call.strike
                  if abs(atm_call.strike - spot) <= abs(atm_put.strike - spot)
                  else atm_put.strike)
    elif atm_call:
        strike = atm_call.strike
    elif atm_put:
        strike = atm_put.strike
    dte = pool[0].dte if pool else None
    return strike, dte, (atm_call, atm_put)


def calc_atm_iv(underlying_code: str, opt_exchange: str, etf_code: str = None,
                trade_date: str = None) -> Optional[dict]:
    """
    计算平值隐含波动率（统一走 data_hub 期权链 + option_metrics 统一算法）。
    - 盘中：data_hub 实时期权链（新浪，含 bid/ask）
    - 盘后/实时链不可用：data_hub 日频期权链（Tushare 为主，四级降级）
    - IV 由 data_hub.option_metrics.implied_vol（二分法）统一计算，不再本地重写 BS
    返回 {"iv": 百分比, "iv_source": "real_bs", "strike", "days", "spot",
           "call_iv", "put_iv", "price_source", "spot_source"}
    """
    if not _DATAHUB_READY:
        return None
    chain_key = _resolve_chain_key(underlying_code, opt_exchange, etf_code)
    if not chain_key:
        return None

    try:
        chain = None
        spot = None
        price_source = None

        # 1) 实时期权链优先（盘中实时）
        try:
            rt = _dh_get_realtime_option_chain(chain_key)
            if rt and rt.get("chain"):
                chain = rt["chain"]
                spot = rt.get("spot")
                price_source = f"realtime:{rt.get('source')}"
        except Exception as e:  # noqa: BLE001
            log.debug("实时链失败[%s]: %s", chain_key, e)

        # 2) 日频链兜底（data_hub 内部多级降级）
        if not chain:
            try:
                df, spot_d = _dh_get_option_chain(chain_key, trade_date,
                                                  with_spot=True)
                if df is not None and not df.empty:
                    chain = df
                    if not spot:
                        spot = spot_d
                    price_source = "daily_chain"
            except Exception as e:  # noqa: BLE001
                log.debug("日频链失败[%s]: %s", chain_key, e)

        if not chain or not spot or float(spot) <= 0:
            return None

        # 3) 标准化 + 统一算法算 IV
        contracts = _dh_standardize_contracts(
            chain, source="tushare", trade_date=trade_date or "")
        if not contracts:
            return None
        contracts = _dh_calc_iv_for_chain(contracts, float(spot), r=0.02)
        atm_iv = _dh_get_atm_iv(contracts, float(spot))
        if not atm_iv or atm_iv <= 0:
            return None

        strike, dte, pair = _atm_iv_detail(contracts, float(spot))
        call_iv = put_iv = None
        if pair:
            atm_call, atm_put = pair
            call_iv = round(atm_call.iv * 100, 2) if atm_call and atm_call.iv > 0 else None
            put_iv = round(atm_put.iv * 100, 2) if atm_put and atm_put.iv > 0 else None

        return {
            "iv": round(atm_iv * 100, 2),
            "iv_source": "real_bs",
            "strike": strike,
            "days": dte,
            "spot": round(float(spot), 4),
            "spot_source": "data_hub",
            "price_source": price_source,
            "call_iv": call_iv,
            "put_iv": put_iv,
        }
    except Exception as e:  # noqa: BLE001
        log.warning(f"计算真实IV失败[{underlying_code}]: {e}")
        return None


# ──────────────────────────── 波动率微笑数据收集 ────────────────────────────

def collect_smile_for_option(okey: str, oinfo: dict, trade_date: str = "") -> Optional[dict]:
    """
    收集单个期权品种的波动率微笑数据（近月+远月，call+put）。
    返回 {"near": {"calls": [(strike, iv%), ...], "puts": ...}, "far": {...}, "spot": float}
    """
    if not _DATAHUB_READY:
        return None

    chain_key = _resolve_chain_key(okey, oinfo["exchange"], okey)
    if not chain_key:
        return None

    try:
        chain = None
        spot = None

        # 1) 实时链
        try:
            rt = _dh_get_realtime_option_chain(chain_key)
            if rt and rt.get("chain"):
                chain = rt["chain"]
                spot = rt.get("spot")
        except Exception as e:
            log.debug("实时链失败[%s]: %s", chain_key, e)

        # 2) 日频链
        if chain is None:
            try:
                df, spot_d = _dh_get_option_chain(chain_key, trade_date, with_spot=True)
                if df is not None and not df.empty:
                    chain = df
                    if not spot:
                        spot = spot_d
            except Exception as e:
                log.debug("日频链失败[%s]: %s", chain_key, e)

        if chain is None or not spot or float(spot) <= 0:
            return None

        # 标准化 + IV
        contracts = _dh_standardize_contracts(chain, source="tushare", trade_date=trade_date or "")
        if not contracts:
            return None
        contracts = _dh_calc_iv_for_chain(contracts, float(spot), r=0.02)

        # 按到期月分组（过滤 IV > 100% 的噪声数据）
        expiries = {}
        for c in contracts:
            if c.iv is None or c.iv <= 0 or c.iv > 1.0:
                continue
            if c.dte < 3:
                continue
            exp = c.expiry
            if exp not in expiries:
                expiries[exp] = {"dte": c.dte, "calls": [], "puts": []}
            side = "calls" if c.opt_type == "C" else "puts"
            expiries[exp][side].append((c.strike, round(c.iv * 100, 2)))

        if len(expiries) < 1:
            return None

        sorted_exps = sorted(expiries.keys())
        near_exp = sorted_exps[0]
        far_exp = sorted_exps[-1] if len(sorted_exps) > 1 else None

        result = {
            "spot": float(spot),
            "near": {
                "expiry": near_exp,
                "dte": expiries[near_exp]["dte"],
                "calls": sorted(expiries[near_exp]["calls"], key=lambda x: x[0]),
                "puts": sorted(expiries[near_exp]["puts"], key=lambda x: x[0]),
            },
        }
        if far_exp and far_exp != near_exp:
            result["far"] = {
                "expiry": far_exp,
                "dte": expiries[far_exp]["dte"],
                "calls": sorted(expiries[far_exp]["calls"], key=lambda x: x[0]),
                "puts": sorted(expiries[far_exp]["puts"], key=lambda x: x[0]),
            }

        # 检测偏度方向（归一化斜率，按ATM归一化使不同标的可比）
        import numpy as np
        near_calls = result["near"]["calls"]
        if len(near_calls) >= 3:
            strikes = np.array([s for s, _ in near_calls])
            ivs = np.array([iv for _, iv in near_calls])
            slope = np.polyfit(strikes, ivs, 1)[0]
            atm = np.median(strikes)
            rel_slope = slope * atm
            result["skew_direction"] = "down" if rel_slope < -0.01 else ("up" if rel_slope > 0.01 else "flat")
            result["skew_slope"] = round(float(slope), 4)
        else:
            result["skew_direction"] = "unknown"
            result["skew_slope"] = 0

        log.info("  %s: smile收集完成, 近月%d call + %d put, 远月%s",
                 oinfo["name"],
                 len(result["near"]["calls"]),
                 len(result["near"]["puts"]),
                 far_exp or "无")
        return result

    except Exception as e:
        log.warning("smile数据收集失败[%s]: %s", okey, e)
        return None


# ──────────────────────────── HV计算 ────────────────────────────

def calc_hv(closes: list[float], window: int) -> Optional[float]:
    """
    计算年化历史波动率
    HV = std(log_returns) * sqrt(252)
    """
    if len(closes) < window + 1:
        return None
    # 取最近window+1个价格计算window个收益率
    prices = closes[-(window + 1):]
    log_returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0 and prices[i] > 0:
            log_returns.append(math.log(prices[i] / prices[i - 1]))

    if len(log_returns) < window:
        return None

    # 只取最后window个收益率
    log_returns = log_returns[-window:]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    std = math.sqrt(variance)
    return std * math.sqrt(252)


def calc_hv_percentile(all_closes: list[float], hv20_series: list[float]) -> Optional[float]:
    """
    计算HV20在过去250个交易日中的分位数 (0-100%)
    （约1年，用于分位数告警判断）
    """
    if len(hv20_series) < 2:
        return None
    # 取最近的一个hv20值
    current_hv20 = hv20_series[-1]
    # 在过去250个hv20值中排名（约1年）
    lookback = hv20_series[-250:] if len(hv20_series) >= 250 else hv20_series
    count_below = sum(1 for v in lookback[:-1] if v < current_hv20)
    total = len(lookback) - 1
    if total == 0:
        return None
    return (count_below / total) * 100


def calc_daily_vol_percentile(closes: list[float], lookback_days: int = 250) -> Optional[float]:
    """
    计算日波动率（日涨跌幅绝对值）在近N天中的分位数 (0-100%)。
    更灵敏——每天一个数据点，直接反映当日波动强度的历史位置。
    默认250天（约1年），远超120天。
    """
    if len(closes) < 3:
        return None
    # 计算每日涨跌幅绝对值（%）
    daily_returns = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            daily_returns.append(abs((closes[i] / closes[i-1] - 1) * 100))
    if len(daily_returns) < 2:
        return None
    # 取最近lookback_days天
    recent = daily_returns[-lookback_days:] if len(daily_returns) >= lookback_days else daily_returns
    current_vol = recent[-1]
    count_below = sum(1 for v in recent[:-1] if v < current_vol)
    total = len(recent) - 1
    if total == 0:
        return None
    return (count_below / total) * 100


def calc_returns(closes: list[float], days: int) -> Optional[float]:
    """计算近N日涨跌幅(%)"""
    if len(closes) < days + 1:
        return None
    return (closes[-1] / closes[-(days + 1)] - 1) * 100


def compute_hv_series(closes: list[float], window: int) -> list[float]:
    """计算HV时间序列（滑动窗口），用于分位数计算"""
    series = []
    for i in range(window + 1, len(closes) + 1):
        subset = closes[:i]
        hv = calc_hv(subset, window)
        if hv is not None:
            series.append(hv)
    return series


# ──────────────────────────── 数据聚合 ────────────────────────────


def fetch_intraday_rv(code: str, period: str = "5") -> Optional[dict]:
    """当日盘中已实现波动率（RV），统一走 data_hub.get_minute_bar。

    用当日 5 分钟收盘价算对数收益标准差并年化。
    返回: {"rv_annual_pct", "intraday_return_pct", "latest_time", "latest_price", "bars"}
    """
    if not _DATAHUB_READY:
        return None

    ts_code = _to_ts_code(code)
    try:
        df = _dh_get_minute_bar(ts_code=ts_code, freq=str(period), n=120)
    except Exception as e:  # noqa: BLE001
        log.debug("当日RV分钟线获取失败 %s: %s", ts_code, e)
        return None
    if df is None or len(df) == 0 or "close" not in df.columns:
        return None

    dcol = "datetime" if "datetime" in df.columns else "date"
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        df = df[df[dcol].astype(str).str.startswith(today_str)]
    except Exception:  # noqa: BLE001
        return None
    if len(df) < 3:
        return None

    closes = [c for c in (_fnum(x) for x in df["close"].tolist()) if c and c > 0]
    if len(closes) < 3:
        return None

    open_price = _fnum(df.iloc[0].get("open")) or closes[0]
    latest_price = closes[-1]
    latest_time = str(df.iloc[-1][dcol])[11:16]

    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes)) if closes[i - 1] > 0
    ]
    if len(log_returns) < 2:
        return None

    import statistics
    rv_std = statistics.stdev(log_returns)
    try:
        bars_per_day = max(1, int(240 / int(period)))
    except (TypeError, ValueError, ZeroDivisionError):
        bars_per_day = 48
    rv_annual = rv_std * math.sqrt(252 * bars_per_day)

    return {
        "rv_annual_pct": round(rv_annual * 100, 2),
        "intraday_return_pct": round((latest_price / open_price - 1) * 100, 2)
                                if open_price else 0.0,
        "latest_time": latest_time,
        "latest_price": round(latest_price, 2),
        "bars": len(closes),
    }
def get_end_date(target_date: datetime) -> str:
    """获取截止日期（target_date当天或之前最近的交易日）"""
    return target_date.strftime("%Y-%m-%d")


def get_start_date(target_date: datetime) -> str:
    """获取起始日期（回溯KLINE_LOOKBACK_DAYS天）"""
    start = target_date - timedelta(days=KLINE_LOOKBACK_DAYS)
    return start.strftime("%Y-%m-%d")


def collect_all_data(target_date: datetime) -> dict:
    """
    采集所有数据，返回结构化结果
    """
    end_date = get_end_date(target_date)
    start_date = get_start_date(target_date)
    date_str = target_date.strftime("%Y%m%d")

    import time as _time
    _t_total = _time.time()
    log.info("开始采集数据, 日期: %s, K线范围: %s ~ %s", date_str, start_date, end_date)

    result = {
        "date": date_str,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "underlyings": {},
        "futures": {},
        "options": {},
        "alerts": [],
        "intraday_rv": {},
    }

    # ── 1. 并发采集标的K线 ──
    log.info("并发采集 %d 个标的K线...", len(UNDERLYINGS))
    kline_results = {}

    def _fetch_one_kline(key: str, info: dict) -> tuple[str, list[dict]]:
        log.info("  采集: %s (%s)", info["name"], info["code"])
        kline = fetch_kline(info["code"], start_date, end_date)
        return key, kline

    # 串行采集：akshare 新浪接口并发会被限流，串行反而更快且可用 signal 超时
    for key, info in UNDERLYINGS.items():
        try:
            k, kline = _fetch_one_kline(key, info)
            kline_results[k] = kline
        except Exception as e:
            log.warning("K线采集失败 %s: %s", key, e)
            kline_results[key] = []

    _t1 = _time.time()
    log.info("[耗时] K线采集: %.1fs", _t1 - _t_total)

    # ── 1a2. 当日实时口径汇总 ──
    # bar 由 data_hub.get_daily_with_realtime 统一补齐，业务侧不做任何取数/拼接，
    # 只读取接口回传的 attrs（is_realtime / as_of / injected）。
    realtime_meta = {
        "source": "data_hub.get_daily_with_realtime",
        "as_of": None,
        "is_intraday": False,
        "injected": {},
        "missing": [],
    }
    _today_str = datetime.now().strftime("%Y-%m-%d")
    if target_date.strftime("%Y-%m-%d") == _today_str:
        for _key, _info in UNDERLYINGS.items():
            _m = _KLINE_RT_META.get(_info["code"]) or {}
            if _m.get("is_realtime"):
                realtime_meta["injected"][_key] = _m.get("injected") or {}
                realtime_meta["as_of"] = _m.get("as_of") or realtime_meta["as_of"]
            else:
                realtime_meta["missing"].append(_info["name"])
        realtime_meta["is_intraday"] = bool(realtime_meta["injected"])
        if realtime_meta["is_intraday"]:
            log.info(
                "当日实时价已由 data_hub 补齐 %d/%d 标的（截至 %s），HV/涨跌幅/分位均为盘中实时口径",
                len(realtime_meta["injected"]), len(UNDERLYINGS), realtime_meta["as_of"],
            )
        if realtime_meta["missing"]:
            log.warning("未取得当日实时的标的: %s", "、".join(realtime_meta["missing"]))
    else:
        log.info("历史日期回跑（%s），使用收盘口径", target_date.strftime("%Y-%m-%d"))
    result["realtime_meta"] = realtime_meta
    # ── 1b. 计算HV指标 ──
    for key, info in UNDERLYINGS.items():
        kline = kline_results.get(key, [])

        if not kline:
            log.warning("标的 %s K线数据为空", info["name"])
            result["alerts"].append(f"⚠️ {info['name']} K线数据获取失败")
            result["underlyings"][key] = {
                "name": info["name"], "code": info["code"],
                "error": "K线数据获取失败",
            }
            continue

        closes = [float(k["close"]) for k in kline if k.get("close")]
        if len(closes) < 21:
            log.warning("标的 %s 数据不足: 仅%d条", info["name"], len(closes))
            result["alerts"].append(f"⚠️ {info['name']} 数据不足({len(closes)}条)")
            result["underlyings"][key] = {
                "name": info["name"], "code": info["code"],
                "error": f"数据不足({len(closes)}条)",
            }
            continue

        hv10 = calc_hv(closes, 10)
        hv20 = calc_hv(closes, 20)
        hv60 = calc_hv(closes, 60) if len(closes) >= 61 else None

        # HV20分位数（用于方向判断参考）
        hv20_series = compute_hv_series(closes, 20)
        hv20_pct = calc_hv_percentile(closes, hv20_series)

        # 日波动分位数：近250个交易日每日涨跌幅绝对值的排名百分比
        # 注意：这是「单日涨跌幅大小」的历史分位，不是HV20那种滚动波动率
        daily_vol_pct = calc_daily_vol_percentile(closes)

        # 涨跌幅
        ret3 = calc_returns(closes, 3)
        ret5 = calc_returns(closes, 5)

        latest_close = closes[-1]
        latest_date = kline[-1].get("date", "")

        result["underlyings"][key] = {
            "name": info["name"],
            "code": info["code"],
            "latest_close": latest_close,
            "latest_date": latest_date,
            "hv10": round(hv10 * 100, 2) if hv10 else None,
            "hv20": round(hv20 * 100, 2) if hv20 else None,
            "hv60": round(hv60 * 100, 2) if hv60 else None,
            "hv20_percentile": round(hv20_pct, 1) if hv20_pct else None,
            "daily_vol_percentile": round(daily_vol_pct, 1) if daily_vol_pct else None,
            "ret3d": round(ret3, 2) if ret3 else None,
            "ret5d": round(ret5, 2) if ret5 else None,
        }
        log.info("  %s: HV10=%.1f%% HV20=%.1f%% HV60=%s 3日涨跌=%s%%",
                 info["name"],
                 hv10 * 100 if hv10 else 0,
                 hv20 * 100 if hv20 else 0,
                 f"{hv60*100:.1f}%" if hv60 else "N/A",
                 f"{ret3:.2f}" if ret3 else "N/A")

    # ── 1c. 采集当日实时已实现波动率（分钟线） ──
    _t2 = _time.time()
    log.info("[耗时] HV计算: %.1fs", _t2 - _t1)
    log.info("采集当日实时已实现波动率（5分钟线）...")
    for key, info in UNDERLYINGS.items():
        rv = fetch_intraday_rv(info["code"])
        if rv:
            result["intraday_rv"][key] = rv
            log.info("  %s: 当日RV=%s%% (截至%s, %d根K线)",
                     info["name"], rv["rv_annual_pct"], rv["latest_time"], rv["bars"])

    # ── 1d. 确定数据截止日期（K线最新一根日期） ──
    # 取有数据的标的中最新的日期作为数据截止日
    data_dates = []
    for key, udata in result["underlyings"].items():
        if udata.get("latest_date"):
            data_dates.append(udata["latest_date"])
    if data_dates:
        result["data_date"] = max(data_dates)
    else:
        result["data_date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # 判断是否当日（用于报告中提示）
    try:
        actual_data_date = result["data_date"]
        # 统一格式 YYYY-MM-DD
        if len(actual_data_date) == 8 and actual_data_date.isdigit():
            actual_data_date = f"{actual_data_date[:4]}-{actual_data_date[4:6]}-{actual_data_date[6:]}"
        today_str = target_date.strftime("%Y-%m-%d")
        result["is_today_data"] = (actual_data_date == today_str)
        result["data_date"] = actual_data_date
    except Exception:
        result["is_today_data"] = False
        result["data_date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    _t3 = _time.time()
    log.info("[耗时] 当日RV计算: %.1fs", _t3 - _t2)
    # ── 2. 采集期货行情 ──
    log.info("采集期货行情...")
    futures_data = fetch_futures_spot([f["sina_symbol"] for f in FUTURES.values()])

    for fkey, finfo in FUTURES.items():
        sina_sym = finfo["sina_symbol"]
        fdata = futures_data.get(sina_sym)

        if not fdata:
            log.warning("期货 %s 行情为空", finfo["name"])
            result["alerts"].append(f"⚠️ {finfo['name']} 行情获取失败（可能非交易时段）")
            result["futures"][fkey] = {
                "name": finfo["name"],
                "error": "行情获取失败",
            }
            continue

        futures_price = fdata["last"]
        # 基差 = (期货价 - 现货价) / 现货价 * 100
        underlying_key = finfo["underlying"]
        ul_data = result["underlyings"].get(underlying_key, {})
        spot_price = ul_data.get("latest_close")

        basis_pct = None
        if spot_price and futures_price > 0:
            basis_pct = round((futures_price - spot_price) / spot_price * 100, 2)

        result["futures"][fkey] = {
            "name": finfo["name"],
            "price": futures_price,
            "prev_settle": fdata["prev_settle"],
            "basis_pct": basis_pct,
            "date": fdata.get("date", ""),
            "time": fdata.get("time", ""),
        }
        log.info("  %s: 价格=%.1f 基差=%s%%", finfo["name"],
                 futures_price, f"{basis_pct}" if basis_pct else "N/A")

    _t4 = _time.time()
    log.info("[耗时] 期货行情: %.1fs", _t4 - _t3)
    # ── 3. 采集期权波动率（data_hub 自算VIX + 统一ATM IV） ──
    log.info("采集期权波动率（data_hub 自算VIX / 统一ATM IV）...")
    # 统一预取自算 VIX（六宽基，一次性并发，盘中预算保护）
    _vix_all = {}
    if _DATAHUB_READY:
        try:
            _vix_all = _dh_get_all_vix_percentiles() or {}
            log.info("[VIX] data_hub 自算VIX: %s",
                     {k: (v or {}).get("vix") for k, v in _vix_all.items()})
        except Exception as e:  # noqa: BLE001
            log.warning("[VIX] data_hub 自算VIX获取失败: %s", e)
    for okey, oinfo in OPTIONS.items():
        ul_key = oinfo["underlying"]
        ul_data = result["underlyings"].get(ul_key, {})
        hv20 = ul_data.get("hv20")  # 已乘100的百分比

        # 用BS公式算真实IV（兜底用）
        real_iv = None
        iv_source = "none"
        iv_detail = None

        # 先取自算 VIX（data_hub 单一真源，CBOE 分钟到期法，替换原 QVIX 现成值）
        vix_key = _VIX_KEY_MAP.get(okey)
        vix_entry = _vix_all.get(vix_key) if vix_key else None
        if vix_entry:
            vix_val = vix_entry.get("vix")
            if vix_val and 0 < vix_val < 200:
                real_iv = round(float(vix_val), 2)
                iv_source = "vix_selfcalc"
                iv_detail = {
                    "iv": real_iv,
                    "iv_source": "vix_selfcalc",
                    "strike": None,
                    "days": None,
                    "spot": vix_entry.get("spot"),
                    "price_source": "data_hub_vix",
                    "spot_source": "data_hub_vix",
                    "vix_date": vix_entry.get("trade_date"),
                    "vix_percentile": vix_entry.get("percentile"),
                    "vix_source": vix_entry.get("source"),
                }
            else:
                log.debug(f"[{oinfo['name']}] 自算VIX无效({vix_entry.get('error')})，降级ATM IV")
        elif vix_key:
            log.debug(f"[{oinfo['name']}] 自算VIX缺失，降级ATM IV")

        # 自算VIX缺失或无效，用 data_hub 统一ATM IV兜底
        if real_iv is None:
            try:
                if oinfo["exchange"] in ("sse", "szse"):
                    iv_detail = calc_atm_iv(
                        underlying_code=okey,
                        opt_exchange=oinfo["exchange"],
                        etf_code=okey
                    )
                elif oinfo["exchange"] == "cffex":
                    iv_detail = calc_atm_iv(
                        underlying_code=okey,
                        opt_exchange='cffex'
                    )

                if iv_detail and iv_detail.get('iv') and iv_detail['iv'] > 0:
                    real_iv = iv_detail['iv']
                    iv_source = 'real_bs'
            except Exception as e:
                log.warning(f"[{oinfo['name']}] ATM IV计算异常: {e}")

        # IV降级：拿不到真实IV就用 HV20 * 1.2 估算
        if real_iv is None and hv20 is not None:
            real_iv = round(hv20 * 1.2, 2)
            iv_source = "estimated"

        iv_hv_ratio = None
        if real_iv is not None and hv20 is not None and hv20 > 0:
            iv_hv_ratio = round(real_iv / hv20, 2)

        result["options"][okey] = {
            "name": oinfo["name"],
            "underlying": ul_key,
            "iv": real_iv,
            "iv_source": iv_source,
            "iv_hv20_ratio": iv_hv_ratio,
        }
        extra = ""
        if iv_detail and iv_detail.get('strike') is not None:
            extra = f" 平价K={iv_detail['strike']} 剩{iv_detail['days']}天"
        log.info("  %s: IV=%s%% (%s) IV/HV20=%s%s",
                 oinfo["name"],
                 f"{real_iv}" if real_iv else "N/A",
                 iv_source,
                 f"{iv_hv_ratio}" if iv_hv_ratio else "N/A",
                 extra)

    return result


# ──────────────────────────── 方向判断 ────────────────────────────

def judge_direction(ul_data: dict, futures_data: dict, option_data: dict) -> dict:
    """
    纯规则判断波动率方向和交易建议
    返回 {direction, confidence, strategy}
    """
    hv10 = ul_data.get("hv10")
    hv20 = ul_data.get("hv20")
    hv20_pct = ul_data.get("hv20_percentile")
    ret3 = ul_data.get("ret3d")
    basis = futures_data.get("basis_pct") if futures_data else None
    iv = option_data.get("iv") if option_data else None

    # 默认值
    direction = "横盘"
    confidence = "中低"
    strategy = "观望 / 轻仓铁秃鹰"

    if hv10 is None or hv20 is None:
        return {"direction": direction, "confidence": confidence, "strategy": strategy, "reason": "数据不足"}

    # 规则1: HV10 < HV20*0.8 且 期货贴水 > 2% → 上行（高置信度）
    if basis is not None and hv10 < hv20 * 0.8 and basis < -2.0:
        direction, confidence = "上行", "高"
        strategy = "买跨式 / 做多Vega"
        reason = f"HV10({hv10:.1f}%)远低于HV20({hv20:.1f}%)且期货贴水{basis:.1f}%"

    # 规则2: HV10 < HV20*0.85 且 期货贴水 > 1.5% → 上行（中高置信度）
    elif basis is not None and hv10 < hv20 * 0.85 and basis < -1.5:
        direction, confidence = "上行", "中高"
        strategy = "买跨式 / 做多Vega"
        reason = f"HV10({hv10:.1f}%)低于HV20({hv20:.1f}%)且期货贴水{basis:.1f}%"

    # 规则3: HV10 < HV20*0.9 且 近3日下跌>1% → 上行（中等置信度）
    elif ret3 is not None and hv10 < hv20 * 0.9 and ret3 < -1.0:
        direction, confidence = "上行", "中等"
        strategy = "买Put / 风险逆转"
        reason = f"HV10({hv10:.1f}%)低于HV20({hv20:.1f}%)且近3日跌{ret3:.1f}%"

    # 规则4: HV10 > HV20*1.1 且 IV分位>70% → 下行（中置信度）
    elif hv20_pct is not None and hv10 > hv20 * 1.1 and hv20_pct > 70:
        direction, confidence = "下行", "中"
        strategy = "卖宽跨 / 日历价差"
        reason = f"HV10({hv10:.1f}%)高于HV20({hv20:.1f}%)且HV20分位{hv20_pct:.0f}%"

    # 规则5: 近3日反弹>2% 且 IV分位>70% → 下行（中置信度）
    elif ret3 is not None and hv20_pct is not None and ret3 > 2.0 and hv20_pct > 70:
        direction, confidence = "下行", "中"
        strategy = "卖宽跨 / 日历价差"
        reason = f"近3日反弹{ret3:.1f}%且HV20分位{hv20_pct:.0f}%"

    # 规则6: 其他 → 横盘
    else:
        direction, confidence = "横盘", "中低"
        strategy = "观望 / 轻仓铁秃鹰"
        reason = f"HV10({hv10:.1f}%)≈HV20({hv20:.1f}%)"

    return {
        "direction": direction,
        "confidence": confidence,
        "strategy": strategy,
        "reason": reason,
    }


# ──────────────────────────── 报告生成 ────────────────────────────

def generate_json_report(data: dict, judgments: dict) -> dict:
    """生成结构化JSON报告"""
    report = {
        "date": data["date"],
        "generated_at": data["generated_at"],
        "summary": [],
        "details": [],
        "alerts": data.get("alerts", []),
    }

    # 按品种整理
    for okey, oinfo in OPTIONS.items():
        ul_key = oinfo["underlying"]
        ul = data["underlyings"].get(ul_key, {})
        opt = data["options"].get(okey, {})

        # 找对应期货
        fut = None
        for fkey, finfo in FUTURES.items():
            if finfo["underlying"] == ul_key:
                fut = data["futures"].get(fkey)
                break

        judgment = judgments.get(okey, {})

        detail = {
            "品种": oinfo["name"],
            "标的": ul.get("name", ul_key),
            "标的收盘": ul.get("latest_close"),
            "平值IV": opt.get("iv"),
            "IV来源": opt.get("iv_source", "N/A"),
            "IV近1年分位": opt.get("iv_percentile"),
            "IV_HV20比": opt.get("iv_hv20_ratio"),
            "HV20": ul.get("hv20"),
            "基差": fut.get("basis_pct") if fut else None,
            "近3日涨跌": ul.get("ret3d"),
            "近5日涨跌": ul.get("ret5d"),
            "波动率方向": judgment.get("direction", "N/A"),
            "置信度": judgment.get("confidence", "N/A"),
            "建议策略": judgment.get("strategy", "N/A"),
            "判断依据": judgment.get("reason", ""),
        }
        report["details"].append(detail)

    return report


def generate_markdown_report(data: dict, judgments: dict) -> str:
    """生成Markdown格式报告"""
    lines = []
    date_display = f"{data['date'][:4]}-{data['date'][4:6]}-{data['date'][6:]}"

    # 数据日期标注：如果不是当日数据，明确说明
    data_date = data.get("data_date", date_display)
    is_today = data.get("is_today_data", False)
    rt_meta = data.get("realtime_meta") or {}
    rt_ok = bool(rt_meta.get("is_intraday") and rt_meta.get("injected"))
    if rt_ok:
        date_note = (
            f"**数据口径**: 盘中实时（截至 {rt_meta.get('as_of', '')}），"
            f"K线末根为当日实时价，HV/涨跌幅/分位均为**盘中实时值**，非收盘值"
        )
    elif is_today:
        date_note = f"**K线数据截至**: {data_date}收盘"
    else:
        date_note = f"**⚠️ K线数据截至**: {data_date}收盘（非当日，请注意）"

    lines.append(f"# 📊 股指波动率监控日报")
    lines.append(f"**运行日期**: {date_display}  |  **生成时间**: {data['generated_at']}")
    lines.append(date_note)
    lines.append("")

    # ── 当日实时已实现波动率 ──
    intraday_rv = data.get("intraday_rv", {})
    if intraday_rv:
        lines.append("## ⚡ 当日盘中实时波动率")
        lines.append("")
        lines.append("| 品种 | 最新价 | 当日涨跌幅 | 5min已实现波动率(年化) | 截至时间 |")
        lines.append("|------|--------|------------|----------------------|----------|")
        for key, info in UNDERLYINGS.items():
            rv = intraday_rv.get(key)
            if rv:
                ret_str = f"{rv['intraday_return_pct']:+.2f}%"
                rv_str = f"{rv['rv_annual_pct']:.1f}%"
                lines.append(f"| {info['name']} | {rv['latest_price']} | {ret_str} | {rv_str} | {rv['latest_time']} |")
            else:
                lines.append(f"| {info['name']} | — | — | — | — |")
        lines.append("")
        lines.append("> 已实现波动率(RV)基于当日5分钟K线计算，盘中实时更新，仅反映当日波动幅度，不用于日波动分位告警判断。")
        lines.append("")

    # ── 波动率全景总表 ──
    lines.append("## 📈 波动率全景总表")
    lines.append("")
    lines.append("| 品种 | 平值IV | IV近1年分位 | Q25 | Q50 | Q80 | 基差% | 3日涨跌% | 5日涨跌% | 方向 | 置信度 | 建议策略 |")
    lines.append("|------|--------|-----------|-----|-----|-----|-------|----------|----------|------|--------|----------|")

    for okey, oinfo in OPTIONS.items():
        ul_key = oinfo["underlying"]
        ul = data["underlyings"].get(ul_key, {})
        opt = data["options"].get(okey, {})
        judgment = judgments.get(okey, {})

        # 找对应期货基差
        basis = None
        for fkey, finfo in FUTURES.items():
            if finfo["underlying"] == ul_key:
                fut = data["futures"].get(fkey, {})
                basis = fut.get("basis_pct")
                break

        iv_str = f"{opt['iv']:.1f}%" if opt.get("iv") else ("⚠️N/A" if opt.get("iv_source") == "none" else "—")
        iv_pct = opt.get("iv_percentile")
        if iv_pct is not None:
            if iv_pct >= 80:
                iv_pct_str = f"🔥{iv_pct:.0f}%"
            elif iv_pct <= 20:
                iv_pct_str = f"❄️{iv_pct:.0f}%"
            else:
                iv_pct_str = f"{iv_pct:.0f}%"
        else:
            iv_pct_str = "—"
        basis_str = f"{basis:+.2f}" if basis is not None else "—"
        ret3_str = f"{ul['ret3d']:+.2f}" if ul.get("ret3d") is not None else "—"
        ret5_str = f"{ul['ret5d']:+.2f}" if ul.get("ret5d") is not None else "—"
        direction = judgment.get("direction", "—")
        conf = judgment.get("confidence", "—")
        strategy = judgment.get("strategy", "—")

        # IV来源标注
        iv_note = ""
        if opt.get("iv_source") == "estimated":
            iv_note = " (估)"

        # 近1年关键分位值
        quantiles = opt.get("iv_quantiles", {})
        q25_str = f"{quantiles.get(25, '—')}" if quantiles.get(25) else "—"
        q50_str = f"{quantiles.get(50, '—')}" if quantiles.get(50) else "—"
        q80_str = f"{quantiles.get(80, '—')}" if quantiles.get(80) else "—"

        lines.append(f"| {oinfo['name']} | {iv_str}{iv_note} | {iv_pct_str} | {q25_str} | {q50_str} | {q80_str} | {basis_str} | {ret3_str} | {ret5_str} | {direction} | {conf} | {strategy} |")

    lines.append("")

    # ── 分品种简要分析 ──
    lines.append("## 📝 分品种分析")
    lines.append("")

    for okey, oinfo in OPTIONS.items():
        ul_key = oinfo["underlying"]
        ul = data["underlyings"].get(ul_key, {})
        judgment = judgments.get(okey, {})

        lines.append(f"### {oinfo['name']}")

        if ul.get("error"):
            lines.append(f"- ⚠️ 数据异常: {ul['error']}")
        else:
            hv10 = ul.get("hv10", 0) or 0
            hv20 = ul.get("hv20", 0) or 0
            ret3 = ul.get("ret3d")
            direction = judgment.get("direction", "横盘")
            strategy = judgment.get("strategy", "观望")
            reason = judgment.get("reason", "")

            # HV状态描述
            if hv20 > 0:
                ratio = hv10 / hv20
                if ratio < 0.85:
                    hv_desc = "HV10显著低于HV20，波动率压缩中"
                elif ratio > 1.15:
                    hv_desc = "HV10显著高于HV20，波动率扩张中"
                else:
                    hv_desc = "HV10与HV20接近，波动率平稳"
            else:
                hv_desc = "数据不足"

            lines.append(f"- {hv_desc}。{reason}")

            # 日波动分位星级提示（只展示不发邮件）
            daily_vol_pct = ul.get("daily_vol_percentile")
            if daily_vol_pct is not None:
                if daily_vol_pct >= 80:
                    lines.append(f"- ⭐⭐⭐ 当日波动极大（分位{daily_vol_pct:.1f}%），行情剧烈异动")
                elif daily_vol_pct >= 60:
                    lines.append(f"- ⭐⭐ 当日波动偏大（分位{daily_vol_pct:.1f}%），注意节奏")
                elif daily_vol_pct <= 20:
                    lines.append(f"- ⭐⭐⭐ 当日波动极小（分位{daily_vol_pct:.1f}%），变盘前夜")
                elif daily_vol_pct <= 40:
                    lines.append(f"- ⭐ 当日波动偏小（分位{daily_vol_pct:.1f}%）")

            # IV分位提示（隐含波动率近1年分位）
            opt = data["options"].get(okey, {})
            iv_pct = opt.get("iv_percentile")
            iv_val = opt.get("iv")
            if iv_pct is not None and iv_val is not None:
                iv_note = " (估算)" if opt.get("iv_source") == "estimated" else ""
                quantiles = opt.get("iv_quantiles", {})
                q25 = quantiles.get(25)
                q50 = quantiles.get(50)
                q80 = quantiles.get(80)
                q_info = ""
                if q25 and q50 and q80:
                    if iv_pct >= 80:
                        q_info = f"（Q25={q25}% Q50={q50}% Q80={q80}%）"
                    elif iv_pct <= 20:
                        gap_q25 = round(q25 - iv_val, 2)
                        q_info = f"（距Q25还差{gap_q25}%，Q50={q50}% Q80={q80}%）"
                    else:
                        gap_q80 = round(q80 - iv_val, 2)
                        q_info = f"（距Q80还差{gap_q80}%，Q25={q25}% Q50={q50}%）"
                if iv_pct >= 80:
                    lines.append(f"- 🔥 IV近1年分位{iv_pct:.1f}%（IV={iv_val:.1f}%{iv_note}）{q_info}，隐含波动率处于近1年极高水平，期权很贵")
                elif iv_pct <= 20:
                    lines.append(f"- ❄️ IV近1年分位{iv_pct:.1f}%（IV={iv_val:.1f}%{iv_note}）{q_info}，隐含波动率处于近1年极低水平，期权很便宜")
                else:
                    lines.append(f"- 📊 IV近1年分位{iv_pct:.1f}%（IV={iv_val:.1f}%{iv_note}）{q_info}，隐含波动率处于近1年中性区间")

            lines.append(f"- 方向判断: **{direction}** → {strategy}")

            if ret3 is not None:
                if ret3 > 2:
                    lines.append(f"- 标的近3日大幅反弹 {ret3:+.2f}%，注意追高风险")
                elif ret3 < -2:
                    lines.append(f"- 标的近3日大幅下跌 {ret3:+.2f}%，关注恐慌情绪蔓延")

        lines.append("")

    # ── 交易优先级排序 ──
    lines.append("## 🎯 今日交易优先级")
    lines.append("")

    # 按方向确定性排序：高>中高>中等>中>中低
    conf_order = {"高": 1, "中高": 2, "中等": 3, "中": 4, "中低": 5}
    direction_order = {"上行": 1, "下行": 2, "横盘": 3}

    sorted_items = sorted(
        judgments.items(),
        key=lambda x: (direction_order.get(x[1].get("direction", "横盘"), 9),
                       conf_order.get(x[1].get("confidence", "中低"), 9))
    )

    for rank, (okey, judgment) in enumerate(sorted_items, 1):
        oinfo = OPTIONS[okey]
        d = judgment["direction"]
        c = judgment["confidence"]
        s = judgment["strategy"]
        emoji = {"上行": "🔴", "下行": "🟢", "横盘": "⚪"}.get(d, "⚪")
        lines.append(f"{rank}. {emoji} **{oinfo['name']}** — {d}({c}) → {s}")

    lines.append("")

    # ── 告警 ──
    if data.get("alerts"):
        lines.append("## ⚠️ 数据告警")
        lines.append("")
        for alert in data["alerts"]:
            lines.append(f"- {alert}")
        lines.append("")

    # ── 期货基差专条 ──
    lines.append("## 📊 股指期货基差")
    lines.append("")
    lines.append("| 期货 | 最新价 | 基差% | 日期 |")
    lines.append("|------|--------|-------|------|")
    for fkey, finfo in FUTURES.items():
        fdata = data["futures"].get(fkey, {})
        if fdata.get("error"):
            lines.append(f"| {finfo['name']} | — | — | ⚠️{fdata['error']} |")
        else:
            price_str = f"{fdata.get('price', 0):.1f}" if fdata.get("price") else "—"
            basis_str = f"{fdata.get('basis_pct', 0):+.2f}" if fdata.get("basis_pct") is not None else "—"
            lines.append(f"| {finfo['name']} | {price_str} | {basis_str} | {fdata.get('date', '—')} |")

    lines.append("")
    lines.append("---")
    lines.append("*纯Python计算，零LLM调用，数据源: 新浪财经/StockData*")

    return "\n".join(lines)


LARK_CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"  # AI股票消息群
LARK_CLI = "lark-cli"
# lark-cli配置目录（项目目录下，确保cron沙箱也能找到）
def _pick_lark_config_dir() -> Path:
    """选一个真实存在且非空的 lark-cli 配置目录。

    云盘挂载点在不同会话下会变，硬编码单一路径容易失效；空目录比不设更糟
    （会让 lark-cli 认不到登录态），所以要求目录内至少有一个条目。
    """
    for _c in (
        _cp("/opt/coze/.fuse_drive/股指波动率/.feishu_cli"),
        _cp("/Coze/Drive/股指波动率/.feishu_cli"),
        _cp("/app/data/所有对话/主对话/.feishu_cli"),
        _cp("/Coze/Drive/蓁宝/所有对话/主对话/.feishu_cli"),
        _cp("/opt/coze/.fuse_drive/蓁宝/所有对话/主对话/.feishu_cli"),
    ):
        _p = Path(_c)
        try:
            if _p.is_dir() and any(_p.iterdir()):
                return _p
        except OSError:
            continue
    return Path(_cp("/app/data/所有对话/主对话/.feishu_cli"))


LARK_CONFIG_DIR = _pick_lark_config_dir()


def _setup_lark_env():
    """确保lark-cli能找到配置。优先用项目目录下的配置（跨沙箱持久）。"""
    if LARK_CONFIG_DIR.exists():
        os.environ["LARKSUITE_CLI_CONFIG_DIR"] = str(LARK_CONFIG_DIR)
        os.environ["LARKSUITE_CLI_DATA_DIR"] = str(LARK_CONFIG_DIR)


def _load_feishu_push():
    """加载统一推送模块 feishu_push（交互式卡片实现，全系统唯一出口）"""
    for _cand in (
        os.path.join(_HUB_ROOT, "options", "alert", "notifier"),
        _cp("/Coze/Drive/dataHub_三级体系/options/alert/notifier"),
    ):
        if os.path.isdir(_cand) and _cand not in sys.path:
            sys.path.insert(0, _cand)
    try:
        import feishu_push  # noqa: F401
        return feishu_push
    except Exception as e:  # noqa: BLE001
        log.warning("feishu_push 不可用: %s", e)
        return None


def push_to_lark(markdown_content: str, image_path: str = None) -> bool:
    """推送报告到飞书「AI股票消息」群。

    优先走 feishu_push 交互式卡片（表格/分栏/配色都保留）；卡片不可用时回退
    lark-cli markdown 纯文本。图片走 push_image_to_feishu（cwd 相对路径，
    规避 lark-cli 的 allowlist 拒绝）。
    """
    _setup_lark_env()
    ok = False
    fp = _load_feishu_push()
    if fp is not None:
        try:
            ok = bool(fp.push_to_feishu(text=markdown_content, chat_id=LARK_CHAT_ID, as_card=True))
            log.info("飞书卡片推送%s", "成功" if ok else "失败")
        except Exception as e:  # noqa: BLE001
            log.warning("卡片推送异常，回退纯文本: %s", e)
            ok = False

    if not ok:
        try:
            cmd = [
                LARK_CLI, "im", "+messages-send",
                "--chat-id", LARK_CHAT_ID,
                "--markdown", markdown_content,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                log.error("飞书文本推送失败: %s", result.stderr[:300])
                return False
            log.info("飞书文本推送成功（卡片回退）")
            ok = True
        except Exception as e:  # noqa: BLE001
            log.error("飞书推送异常: %s", e)
            return False

    if image_path and os.path.exists(image_path):
        pushed = False
        if fp is not None:
            try:
                pushed = bool(fp.push_image_to_feishu(image_path, chat_id=LARK_CHAT_ID))
            except Exception as e:  # noqa: BLE001
                log.warning("卡片模块图片推送异常: %s", e)
        if not pushed:
            try:
                import shutil
                _tmp_img = os.path.join("/tmp", os.path.basename(image_path))
                shutil.copy2(image_path, _tmp_img)
                r = subprocess.run(
                    [LARK_CLI, "im", "+messages-send", "--chat-id", LARK_CHAT_ID,
                     "--image", os.path.basename(_tmp_img)],
                    capture_output=True, text=True, timeout=60, cwd="/tmp",
                )
                pushed = (r.returncode == 0)
                if not pushed:
                    log.error("图片推送失败: %s", r.stderr[:300])
            except Exception as e:  # noqa: BLE001
                log.error("图片推送异常: %s", e)
        if pushed:
            log.info("飞书图片推送成功: %s", os.path.basename(image_path))

    return ok


# ──────────────────────────── IV分位告警邮件 ────────────────────────────

IV_ALERT_HIGH_PCT = 80   # 高于80%分位告警（隐含波动率极高）
IV_ALERT_LOW_PCT = 20    # 低于20%分位告警（隐含波动率极低）
IV_ALERT_EMAIL = os.environ.get("VOL_ALERT_EMAIL", "410119229@qq.com")
IV_HISTORY_FILE = OUTPUT_DIR / "iv_history.json"  # IV历史序列持久化


def _load_iv_history() -> dict:
    """加载IV历史序列。格式: {品种key: {日期: iv值}}"""
    if IV_HISTORY_FILE.exists():
        try:
            with open(IV_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.warning("IV历史文件读取失败，重新构建")
    return {}


def _save_iv_history(history: dict):
    """保存IV历史序列"""
    IV_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(IV_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


IV_HISTORY_DB = OUTPUT_DIR / "iv_history.db"


def _iv_db_connect():
    """连接 IV 历史库。

    云盘是 FUSE 挂载，SQLite 的 delete-journal 模式需要「创建后删除 journal 文件」，
    这一步在 FUSE 上会失败并抛 `disk I/O error`，事务卡住还会留下热日志把库锁死。
    统一用 MEMORY journal 规避：日志在内存，不落文件；库有备份、可按日重建。
    """
    import sqlite3
    conn = sqlite3.connect(str(IV_HISTORY_DB), timeout=20)
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
    except Exception as e:  # noqa: BLE001
        log.warning("IV库 journal_mode 设置失败: %s", e)
    return conn


def _load_iv_history_from_db() -> dict:
    """从SQLite加载IV历史序列（优先QVIX，其次BS）"""
    history = {}
    try:
        conn = _iv_db_connect()
        c = conn.cursor()
        # 取每个品种的全部历史数据
        c.execute('SELECT ts_code, trade_date, iv, iv_method FROM iv_daily ORDER BY ts_code, trade_date')
        rows = c.fetchall()
        conn.close()
        
        for ts_code, trade_date, iv, iv_method in rows:
            if ts_code not in history:
                history[ts_code] = {}
            history[ts_code][trade_date] = iv
    except Exception as e:
        log.warning(f"从数据库加载IV历史失败: {e}")
    
    return history

def _save_iv_to_db(ts_code: str, trade_date: str, iv: float, **kwargs):
    """保存当日IV到数据库"""
    try:
        conn = _iv_db_connect()
        c = conn.cursor()

        # 先检查是否存在
        c.execute('SELECT iv_method FROM iv_daily WHERE ts_code=? AND trade_date=?', (ts_code, trade_date))
        existing = c.fetchone()
        
        if existing:
            # 已是自算VIX（当前最高优先级真源）则不覆盖，避免同日重复写；
            # 其余来源（含历史 qvix / bs / estimated）允许升级为自算VIX
            if existing[0] == 'vix_selfcalc':
                conn.close()
                return
            # 否则更新
            c.execute('UPDATE iv_daily SET iv=?, iv_method=? WHERE ts_code=? AND trade_date=?',
                     (iv, kwargs.get('iv_method', 'bs_tushare_close'), ts_code, trade_date))
        else:
            c.execute('''INSERT INTO iv_daily (ts_code, trade_date, iv, iv_method, price_source, spot_source)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (ts_code, trade_date, iv, 
                      kwargs.get('iv_method', 'bs_tushare_close'),
                      kwargs.get('price_source', ''),
                      kwargs.get('spot_source', '')))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"保存IV到数据库失败: {e}")

def _update_iv_history(data: dict, target_date: datetime):
    """把当日IV写入历史（数据库+json双写），返回历史dict"""
    date_tag = target_date.strftime("%Y%m%d")
    history = _load_iv_history_from_db()
    
    for okey, oinfo in OPTIONS.items():
        opt = data["options"].get(okey, {})
        iv = opt.get("iv")
        iv_source = opt.get("iv_source", "none")
        if iv is None or iv <= 0:
            continue
        
        # 写入数据库
        method_map = {
            'real_bs': 'bs_tushare_close',
            'estimated': 'estimated_hv20_ratio',
        }
        _save_iv_to_db(okey, date_tag, iv, 
                       iv_method=method_map.get(iv_source, iv_source),
                       price_source=opt.get('price_source', ''),
                       spot_source=opt.get('spot_source', ''))
        
        # 更新内存中的历史
        if okey not in history:
            history[okey] = {}
        history[okey][date_tag] = iv
    
    # 也保存一份json（兼容旧代码）
    _save_iv_history(history)
    return history


def _calc_iv_percentile(iv_history: dict, okey: str, current_iv: float, lookback_days: int = 252) -> Optional[float]:
    """计算当前IV在近N天历史序列中的分位排名百分比（0-100）"""
    series = iv_history.get(okey, {})
    # 按日期排序，取最近lookback_days天
    sorted_items = sorted(series.items(), key=lambda x: x[0])
    recent = sorted_items[-lookback_days:] if len(sorted_items) > lookback_days else sorted_items
    values = [v for _, v in recent if v and v > 0]
    if not values:
        return None
    # 当前值也算入样本（保守估计）
    all_vals = values + [current_iv]
    # 分位 = 小于等于当前值的数量 / 总数 * 100
    count_le = sum(1 for v in all_vals if v <= current_iv)
    return round(count_le / len(all_vals) * 100, 1)


def _get_iv_quantiles(iv_history: dict, okey: str, lookback_days: int = 252, percentiles: list = None) -> dict:
    """获取近N天IV序列的指定分位数值（25/50/75/80等）"""
    if percentiles is None:
        percentiles = [25, 50, 75, 80]
    series = iv_history.get(okey, {})
    sorted_items = sorted(series.items(), key=lambda x: x[0])
    recent = sorted_items[-lookback_days:] if len(sorted_items) > lookback_days else sorted_items
    values = sorted([v for _, v in recent if v and v > 0])
    result = {}
    n = len(values)
    if n == 0:
        return result
    for p in percentiles:
        # 线性插值法计算分位数
        idx = (n - 1) * p / 100
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        result[p] = round(values[lo] + (values[hi] - values[lo]) * frac, 2)
    return result


def _check_iv_percentile_alert(data: dict, judgments: dict, target_date: datetime):
    """
    检查各品种隐含波动率(IV)分位：
    - IV分位 > 80% → 隐含波动率处于近1年极高水平
    - IV分位 < 20% → 隐含波动率处于近1年极低水平
    基于历史IV时间序列排名。
    触发时发送邮件，同一天同一品种只发一次（幂等）。

    注意：IV历史更新和分位计算在主流程中已提前完成，结果存于data["options"][okey]["iv_percentile"]
    """
    date_tag = target_date.strftime("%Y%m%d")
    alert_flag_dir = OUTPUT_DIR / ".iv_alerts"
    alert_flag_dir.mkdir(parents=True, exist_ok=True)

    triggered = []
    others = []  # 未触发告警的品种，在邮件里作为概览展示

    for okey, oinfo in OPTIONS.items():
        opt = data["options"].get(okey, {})
        iv = opt.get("iv")
        iv_pct = opt.get("iv_percentile")
        iv_source = opt.get("iv_source", "none")
        judgment = judgments.get(okey, {})

        if iv is None or iv <= 0 or iv_pct is None:
            continue

        quantiles = opt.get("iv_quantiles", {})
        q25 = quantiles.get(25, "—")
        q50 = quantiles.get(50, "—")
        q80 = quantiles.get(80, "—")

        # 判断方向
        if iv_pct >= IV_ALERT_HIGH_PCT:
            direction = "⚠️ IV极高（>80%分位）"
            direction_type = "high"
        elif iv_pct <= IV_ALERT_LOW_PCT:
            direction = "🔻 IV极低（<20%分位）"
            direction_type = "low"
        else:
            # 未触发告警，加入概览
            if iv_pct >= 50:
                status_text = "中性偏高"
                status_color = "#E6A23C"
            else:
                status_text = "中性偏低"
                status_color = "#909399"
            others.append({
                "name": oinfo['name'],
                "iv": iv,
                "iv_pct": iv_pct,
                "iv_source": iv_source,
                "q25": q25,
                "q50": q50,
                "q80": q80,
                "status_text": status_text,
                "status_color": status_color,
            })
            continue

        # 幂等检查：该品种今天已发过则跳过
        flag_file = alert_flag_dir / f"{okey}_{direction_type}_{date_tag}"
        if flag_file.exists():
            log.info(f"[{oinfo['name']}] 今日已发过IV_{direction_type}告警，跳过")
            continue

        triggered.append({
            "name": oinfo['name'],
            "direction": direction,
            "direction_type": direction_type,
            "iv_pct": iv_pct,
            "iv": iv,
            "iv_source": iv_source,
            "strategy": judgment.get("strategy", "—"),
            "judge_dir": judgment.get("direction", "—"),
            "confidence": judgment.get("confidence", "—"),
            "flag_file": str(flag_file),
            "q25": opt.get("iv_quantiles", {}).get(25, "—"),
            "q50": opt.get("iv_quantiles", {}).get(50, "—"),
            "q80": opt.get("iv_quantiles", {}).get(80, "—"),
        })

    if not triggered:
        log.info("IV分位告警：无品种触发阈值")
        return

    # 发邮件
    log.info(f"IV分位告警：{len(triggered)}个品种触发，准备发送邮件...")
    try:
        _send_iv_alert_email(triggered, target_date, data, others)
        # 写幂等标记
        for item in triggered:
            with open(item["flag_file"], "w") as f:
                f.write(f"sent at {datetime.now().isoformat()}\n")
        log.info(f"✅ IV分位告警邮件发送成功，{len(triggered)}个品种")
    except Exception as e:
        log.error(f"❌ IV分位告警邮件发送失败: {e}")


def _send_iv_alert_email(triggered: list, target_date: datetime, data: dict = None, others: list = None):
    """发送IV分位告警邮件 — 163邮箱SMTP发件 → QQ邮箱收件"""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.header import Header

    smtp_host = "smtp.163.com"
    smtp_port = 465
    smtp_user = "songshang951@163.com"
    smtp_pass = _smtp_password()
    to_addr = IV_ALERT_EMAIL

    date_str = target_date.strftime("%Y-%m-%d")
    high_count = sum(1 for t in triggered if t["direction_type"] == "high")
    low_count = sum(1 for t in triggered if t["direction_type"] == "low")

    # 数据日期标注
    data_date = ""
    is_today = True
    if data:
        data_date = data.get("data_date", date_str)
        is_today = data.get("is_today_data", True)

    if is_today:
        date_note = f"<p><b>K线数据截至：</b>{data_date}收盘</p>"
    else:
        date_note = f'<p style="color:#C00000;"><b>⚠️ K线数据截至：</b>{data_date}收盘（非当日，请注意）</p>'

    subject = f"【波动率告警】{len(triggered)}个品种IV进入近1年极端区间 - {data_date or date_str}"

    # 构建HTML正文
    html_parts = [f"""
<h2 style="color:#C00000;">🚨 隐含波动率(IV)分位告警</h2>
<p><b>告警时间：</b>{date_str} {datetime.now().strftime("%H:%M")}</p>
{date_note}
<p>共 <b style="color:#C00000;font-size:18px;">{len(triggered)}</b> 个品种<b>隐含波动率(IV)</b>进入近1年极端区间
（极高{high_count}个 / 极低{low_count}个）</p>
<p style="color:#666;">
<b>指标说明：</b>当日平值隐含波动率(IV)在近1年IV序列中的排名百分比。<br>
<b>含义：</b>期权市场定价的波动率水平到了近1年高位或低位，直接反映期权贵不贵。
</p>
<br>"""]

    # 当日实时波动率（如果有）
    if data and data.get("intraday_rv"):
        html_parts.append("""
<h3 style="color:#2E75B6;">⚡ 当日盘中实时波动率</h3>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px;">
  <tr style="background:#E8F0FE;">
    <th>品种</th>
    <th>最新价</th>
    <th>当日涨跌幅</th>
    <th>5min已实现波动率(年化)</th>
    <th>截至时间</th>
  </tr>
""")
        for key, info in UNDERLYINGS.items():
            rv = data["intraday_rv"].get(key)
            if rv:
                ret_color = "#C00000" if rv["intraday_return_pct"] < 0 else "#007000"
                html_parts.append(f"""  <tr>
    <td>{info['name']}</td>
    <td>{rv['latest_price']}</td>
    <td style="color:{ret_color};font-weight:bold;">{rv['intraday_return_pct']:+.2f}%</td>
    <td>{rv['rv_annual_pct']:.1f}%</td>
    <td>{rv['latest_time']}</td>
  </tr>
""")
        html_parts.append("""</table>
<p style="color:#888;font-size:12px;">已实现波动率(RV)基于当日5分钟K线计算，仅反映当日波动幅度</p>
<br>
""")

    html_parts.append("""
<table border="1" cellpadding="10" cellspacing="0" style="border-collapse:collapse;width:100%;">
  <tr style="background:#2B2B2B;color:white;">
    <th>品种</th>
    <th>IV</th>
    <th>IV近1年分位</th>
    <th>Q25</th>
    <th>Q50</th>
    <th>Q80</th>
    <th>状态</th>
    <th>方向判断</th>
    <th>建议策略</th>
  </tr>
""")

    for item in triggered:
        color = "#C00000" if item["direction_type"] == "high" else "#2E75B6"
        emoji = "🔴" if item["direction_type"] == "high" else "🟢"
        iv_note = " (估)" if item.get("iv_source") == "estimated" else ""
        q25 = item.get("q25", "—")
        q50 = item.get("q50", "—")
        q80 = item.get("q80", "—")
        html_parts.append(f"""
  <tr style="background:{'#FFF5F5' if item['direction_type'] == 'high' else '#F0F7FF'};">
    <td style="font-size:16px;font-weight:bold;">{emoji} ⭐ {item['name']} ⭐</td>
    <td>{item['iv']:.2f}%{iv_note}</td>
    <td style="font-size:16px;font-weight:bold;color:{color};">{item['iv_pct']:.1f}%</td>
    <td>{q25}%</td>
    <td>{q50}%</td>
    <td>{q80}%</td>
    <td style="font-weight:bold;color:{color};">{item['direction']}</td>
    <td>{item['judge_dir']}({item['confidence']})</td>
    <td>{item['strategy']}</td>
  </tr>
""")

    html_parts.append("""
</table>
<br>""")

    # 其他未触发品种的IV概览
    if others:
        html_parts.append(f"""
<h3 style="color:#666;">📊 其他品种IV概览</h3>
<p style="color:#888;font-size:13px;">共 {len(others)} 个品种处于中性区间，供参考</p>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px;">
  <tr style="background:#F5F5F5;color:#333;">
    <th>品种</th>
    <th>IV</th>
    <th>IV近1年分位</th>
    <th>Q25</th>
    <th>Q50</th>
    <th>Q80</th>
    <th>状态</th>
  </tr>
""")
        for item in others:
            iv_note = " (估)" if item.get("iv_source") == "estimated" else ""
            q25 = item.get("q25", "—")
            q50 = item.get("q50", "—")
            q80 = item.get("q80", "—")
            sc = item.get("status_color", "#999")
            html_parts.append(f"""
  <tr>
    <td style="font-weight:bold;">{item['name']}</td>
    <td>{item['iv']:.2f}%{iv_note}</td>
    <td style="color:{sc};font-weight:bold;">{item['iv_pct']:.1f}%</td>
    <td>{q25}%</td>
    <td>{q50}%</td>
    <td>{q80}%</td>
    <td style="color:{sc};">{item['status_text']}</td>
  </tr>
""")
        html_parts.append("""</table>
<br>""")

    html_parts.append("""
<h3 style="color:#555;">📌 解读</h3>
<ul>
  <li><b style="color:#C00000;">IV极高(>80%分位)</b>：隐含波动率处于近1年前20%水平，期权很贵。若伴随暴跌可买Put对冲，若已在高位可考虑卖波动率收IV（卖方策略）</li>
  <li><b style="color:#2E75B6;">IV极低(<20%分位)</b>：隐含波动率处于近1年后20%水平，期权很便宜=变盘前夜。建议布局买方策略（买跨式/做多Vega），赌波动率向上突破</li>
</ul>
<p style="color:#888;font-size:12px;margin-top:20px;">
本邮件由股指波动率监控系统自动发送。同一天同一品种同一方向仅发送一次。<br>
指标：当日平值IV，在近1年IV序列中的分位排名 | 阈值：>80% / <20%
</p>
""")

    body = "".join(html_parts)

    msg = MIMEMultipart("related")
    msg["From"] = Header(smtp_user)
    msg["To"] = Header(to_addr)
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(body, "html", "utf-8"))

    server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
    server.login(smtp_user, smtp_pass)
    server.sendmail(smtp_user, [to_addr], msg.as_string())
    server.quit()
    log.info(f"IV告警邮件发送成功 → {to_addr}")


# ──────────────────────────── 主流程 ────────────────────────────

def main():
    # 解析参数
    target_date = datetime.now()
    push = True  # 默认推送飞书，用 --no-push 关闭
    send_email = True  # 默认发邮件，用 --no-email 关闭（调试/补跑时避免重复发信）

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--date" and i + 1 < len(args):
            try:
                target_date = datetime.strptime(args[i + 1], "%Y%m%d")
                i += 2
            except ValueError:
                log.error("日期格式错误，应为YYYYMMDD")
                sys.exit(1)
        elif args[i] == "--push":
            push = True
            i += 1
        elif args[i] == "--no-push":
            push = False
            i += 1
        elif args[i] == "--no-email":
            send_email = False
            i += 1
        else:
            i += 1

    log.info("=" * 60)
    log.info("股指波动率监控日报 - 开始运行")
    log.info("目标日期: %s", target_date.strftime("%Y-%m-%d"))
    log.info("=" * 60)

    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 采集数据
    data = collect_all_data(target_date)

    # 2. 波动率方向判断
    judgments = {}
    for okey, oinfo in OPTIONS.items():
        ul_key = oinfo["underlying"]
        ul = data["underlyings"].get(ul_key, {})

        # 找对应期货
        fut = None
        for fkey, finfo in FUTURES.items():
            if finfo["underlying"] == ul_key:
                fut = data["futures"].get(fkey)
                break

        opt = data["options"].get(okey, {})
        judgments[okey] = judge_direction(ul, fut, opt)

    # 2.5. 计算IV近1年分位（写回data供报告和告警使用）
    # 历史数据优先从SQLite（QVIX官方数据）加载，分位更准
    iv_history = _load_iv_history_from_db()
    # 如果数据库里数据太少，回退到json文件
    min_days = 20
    db_samples = max(len(iv_history.get(k, {})) for k in OPTIONS.keys()) if iv_history else 0
    if db_samples < min_days:
        iv_history = _load_iv_history()
    
    # 先更新历史（写入今天的IV），再用更新后的历史算分位
    iv_history = _update_iv_history(data, target_date)
    
    for okey, oinfo in OPTIONS.items():
        opt = data["options"].get(okey, {})
        iv = opt.get("iv")
        if iv and iv > 0:
            iv_pct = _calc_iv_percentile(iv_history, okey, iv)
            data["options"][okey]["iv_percentile"] = iv_pct
            # 近1年关键分位数值（25/50/75/80）
            quantiles = _get_iv_quantiles(iv_history, okey, percentiles=[10, 25, 50, 75, 80, 90])
            data["options"][okey]["iv_quantiles"] = quantiles
            sample_days = len(iv_history.get(okey, {}))
            log.info(f"  {oinfo['name']}: IV={iv:.1f}% IV近1年分位={iv_pct:.1f}%（样本{sample_days}天）")
            log.info(f"    近1年分位值: Q10={quantiles.get(10, 'N/A')}% Q25={quantiles.get(25, 'N/A')}% Q50={quantiles.get(50, 'N/A')}% Q75={quantiles.get(75, 'N/A')}% Q80={quantiles.get(80, 'N/A')}% Q90={quantiles.get(90, 'N/A')}%")

    # 3. 生成报告
    json_report = generate_json_report(data, judgments)
    md_report = generate_markdown_report(data, judgments)

    # 3.5 收集波动率微笑数据 + 生成图表
    smile_data = {}
    smile_image = None
    log.info("收集波动率微笑数据...")
    for okey, oinfo in OPTIONS.items():
        if not oinfo.get("has_iv", False):
            continue
        sd = collect_smile_for_option(okey, oinfo, target_date.strftime("%Y%m%d"))
        if sd:
            smile_data[okey] = sd
            log.info("  %s: smile OK, 近月expiry=%s, 偏度=%s",
                     oinfo["name"],
                     sd.get("near", {}).get("expiry", "?"),
                     sd.get("skew_direction", "?"))
        else:
            log.warning("  %s: smile 数据不足", oinfo["name"])

    smile_risks = []
    if smile_data:
        try:
            from vol_smile_chart import generate_smile_chart, detect_smile_risks, format_risk_alert
            smile_output = OUTPUT_DIR / f"vol_smile_{target_date.strftime('%Y%m%d')}.png"
            iv_safe_ranges = {k: v["iv_safe_range"] for k, v in OPTIONS.items() if "iv_safe_range" in v}
            underlying_names = {k: v["name"] for k, v in OPTIONS.items()}
            date_display = target_date.strftime("%Y-%m-%d")
            smile_image = generate_smile_chart(
                smile_data, iv_safe_ranges, underlying_names,
                str(smile_output), date_display
            )
            if smile_image:
                log.info("波动率微笑图已生成: %s", smile_image)

            # 风险检测
            smile_risks = detect_smile_risks(smile_data, iv_safe_ranges, underlying_names)
            if smile_risks:
                log.warning("波动率微笑检测到 %d 项风险", len(smile_risks))
                for r in smile_risks:
                    log.warning("  [%s] %s: %s", r["level"], r["name"], r["type"])
        except Exception as e:
            log.error("生成波动率微笑图失败: %s", e)
            import traceback
            traceback.print_exc()

    # 3.6 在报告末尾附加波动率微笑风险预警
    if smile_risks:
        md_report += "\n\n## ⚠️ 波动率微笑风险预警\n\n"
        high_risks = [r for r in smile_risks if r["level"] == "high"]
        medium_risks = [r for r in smile_risks if r["level"] == "medium"]
        if high_risks:
            md_report += f"### 高风险（{len(high_risks)}项）\n\n"
            for r in high_risks:
                md_report += f"- [!!] **{r['name']}** [{r['type']}]：{r['detail']}\n"
            md_report += "\n"
        if medium_risks:
            md_report += f"### 中等风险（{len(medium_risks)}项）\n\n"
            for r in medium_risks:
                md_report += f"- [!] **{r['name']}** [{r['type']}]：{r['detail']}\n"
            md_report += "\n"
    elif smile_data:
        md_report += "\n\n## ✅ 波动率微笑检测\n\n所有标的光滑率微笑结构正常，无异常信号。\n"

    # 4. 保存文件
    date_str = target_date.strftime("%Y%m%d")
    json_path = OUTPUT_DIR / f"vol_monitor_{date_str}.json"
    md_path = OUTPUT_DIR / f"vol_monitor_{date_str}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_report, f, ensure_ascii=False, indent=2)
    log.info("JSON报告已保存: %s", json_path)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    log.info("Markdown报告已保存: %s", md_path)

    # 5. 运行成交量小波分析（与波动率日报一起推送）
    wavelet_image = None
    wavelet_script = SCRIPT_DIR / "volume_wavelet_analysis.py"
    if wavelet_script.exists():
        log.info("运行成交量小波分析...")
        try:
            wavelet_result = subprocess.run(
                [sys.executable, str(wavelet_script),
                 "--date", target_date.strftime("%Y%m%d"),
                 "--lookback", "250"],
                capture_output=True, text=True, timeout=120,
                cwd=str(SCRIPT_DIR)
            )
            if wavelet_result.returncode == 0:
                img_path = OUTPUT_DIR / f"volume_wavelet_{target_date.strftime('%Y%m%d')}.png"
                if img_path.exists():
                    wavelet_image = str(img_path)
                    log.info("成交量小波分析完成: %s", img_path)
                else:
                    log.warning("小波脚本运行成功但未找到输出图片")
            else:
                log.error("成交量小波分析失败: %s", wavelet_result.stderr[-500:])
        except Exception as e:
            log.error("调用小波分析脚本异常: %s", e)
    else:
        log.warning("小波分析脚本不存在，跳过: %s", wavelet_script)

    # 6. IV隐含波动率分位告警邮件（IV >80% 或 <20% 近1年分位）
    _check_iv_percentile_alert(data, judgments, target_date)

    # 7. 飞书推送（可选）
    if push:
        push_to_lark(md_report, wavelet_image)
        # 波动率微笑图单独推送（附加风险预警）
        if smile_image and os.path.exists(smile_image):
            from vol_smile_chart import format_risk_alert
            risk_text = format_risk_alert(smile_risks, target_date.strftime("%Y-%m-%d"))
            smile_caption = (
                "📈 **波动率微笑图说明**\n"
                "- 实线 = 近月期权 Call/Put IV vs 行权价\n"
                "- 虚线 = 远月期权 Call/Put IV（对比期限结构）\n"
                "- 绿色区域 = IV安全区间（历史正常范围）\n"
                "- 标注 [!!] = 反向偏斜（行权价越高IV越低，异常）\n"
                "- ATM虚线 = 当前标的价格位置\n\n"
                f"**风险检测**：\n{risk_text}"
            )
            push_to_lark(smile_caption, smile_image)

    # 7.5 日报邮件（每次运行都发，不幂等）
    try:
        if send_email:
            send_daily_report_email(
            data, judgments, target_date, md_report, wavelet_image,
            options_cfg=OPTIONS, futures_cfg=FUTURES, underlyings_cfg=UNDERLYINGS,
            to_addr=IV_ALERT_EMAIL
        )
    except Exception as e:
        log.error(f"日报邮件发送失败: {e}")

    # 8. 输出摘要到stdout
    log.info("=" * 60)
    log.info("运行完成！")
    log.info("JSON: %s", json_path)
    log.info("MD:   %s", md_path)
    if wavelet_image:
        log.info("小波图: %s", wavelet_image)

    # 打印简要摘要
    print("\n" + "=" * 60)
    print("📊 波动率快照")
    print("=" * 60)
    for okey, oinfo in OPTIONS.items():
        ul_key = oinfo["underlying"]
        ul = data["underlyings"].get(ul_key, {})
        j = judgments.get(okey, {})
        opt = data["options"].get(okey, {})
        hv20_str = f"{ul['hv20']:.1f}%" if ul.get("hv20") is not None else "N/A"
        iv_str = f"{opt['iv']:.1f}%" if opt.get("iv") else "N/A"
        print(f"  {oinfo['name']:12s} | IV={iv_str:>6s} | HV20={hv20_str:>6s} | {j.get('direction','?')}({j.get('confidence','?')}) → {j.get('strategy','?')}")

    if data.get("alerts"):
        print("\n⚠️ 告警:")
        for a in data["alerts"]:
            print(f"  {a}")

    print()


if __name__ == "__main__":
    main()
