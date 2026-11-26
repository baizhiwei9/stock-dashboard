# -*- coding: utf-8 -*-
"""第三方库接入层（用户口径 2026-11-13）：取数走 DataHub，期权计算走 options_utility。

边界
----
· 本模块只做「取数」与「期权计算原语」的转发，不含任何策略判断。
· 各业务脚本保持原有落盘文件名与字段结构，策略层（judge / analyze / forecast /
  卡片 / 邮件）不感知本次改造。
· DataHub 取不到时返回空对象（不抛异常），由调用方按原逻辑回落到旧通道，
  避免单源故障把整条日更链路打挂。

口径
----
· 回补历史期权链仍以本地 data/chains/<date>.json 为准（DataHub 历史链依赖的
  TuShare 通道在本环境已失效，且其降级链会返回「当日看板」，等于用实时价冒充
  历史，DataHub 自身文档亦禁止）；当日盘中链、标的实时价、交易日历、ETF/指数
  日线、合约基础信息一律经 DataHub。
· 计算原语与本地自研实现同源：BS 定价/IV/Gamma 取 calc.option_monitor，
  25Δ 取 calc.cn.skew_dual，30/90 加权与分位取 calc.cn.gex_weighted，
  期限结构取 calc.cn.iv_history，美股三轨取 calc.us.index_options。
"""
from __future__ import annotations

import os
import sys
import pandas as pd

# DoMate 原运行机的 workspace 根。换机/云盘环境下该路径不存在，
# 旧写法会把 _LIB_PATHS 全部落空，导致 `import calc` / `import data_hub` 直接
# ModuleNotFoundError（2026-11-25 option_risk_cn_us fetch 阶段整块失败即此因）。
# 改为多路径探测：先试原机路径，再试云盘 data_hub 仓库。
# 统一口径在最前：paths.workspace_root() / paths.datahub_root() 是当前机器
# 真实可用的位置；下面那些是原机路径，只作兜底，别让它们抢在前面。
# ---- 路径统一口径：case/_lib/paths.py（换机器只改一处）----------------------
try:
    from paths import workspace_root as _p_ws_fn, datahub_root as _p_hub_fn
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    import os as _o, sys as _s
    _d = _o.path.dirname(_o.path.abspath(__file__))
    while not _o.path.isfile(_o.path.join(_d, "_lib", "paths.py")):
        _n = _o.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    _s.path.insert(0, _o.path.join(_d, "_lib"))
    from paths import workspace_root as _p_ws_fn, datahub_root as _p_hub_fn

_P_WS = _p_ws_fn()
_P_HUB = _p_hub_fn()

_BASE_CANDS = [
    os.environ.get("DUMATE_WORKSPACE") or "",
    _P_WS or "",
    _P_HUB or "",
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace",
    (os.environ.get("DATA_HUB_PATH") or os.environ.get("DATAHUB_DIR")
     or os.environ.get("EDB_REPO") or ""),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "datahub-multi-tier"),
    "/opt/coze/.fuse_drive/dataHub_三级体系",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "dataHub_三级体系"),
    "/opt/coze/.fuse_drive/股市监控画布",
]
_LIB_PATHS = []
for _b in _BASE_CANDS:
    if not _b or not os.path.isdir(_b) or _b in _LIB_PATHS:
        continue
    # 原机上期权计算库在 projects/options_utility；云盘版统一放在 <repo>/options
    # 同时把 <repo> 本身加入，`import data_hub` 才能解析。
    for _sub in ("projects/options_utility", "options", ""):
        _p = os.path.join(_b, _sub) if _sub else _b
        if os.path.isdir(_p) and _p not in _LIB_PATHS:
            _LIB_PATHS.append(_p)
    for _sub in ("projects/datahub-three-tier", "pylibs", ".pip_libs"):
        _p = os.path.join(_b, _sub)
        if os.path.isdir(_p) and _p not in _LIB_PATHS:
            _LIB_PATHS.append(_p)
for _p in _LIB_PATHS:
    if _p not in sys.path:
        # 追加到末尾而不是插到最前：链路自身的模块（如 macro_monthly_report/config.py）
        # 必须优先被解析，第三方库只做补充。
        sys.path.append(_p)

# ---------------- DataHub（取数） ----------------
import data_hub as dh  # noqa: E402

# ---------------- options_utility（期权计算） ----------------
from calc.option_monitor import (  # noqa: E402
    bs_call, bs_put, bs_iv, bs_gamma, bs_d1,
)
from calc.cn.gex_weighted import (  # noqa: E402
    interp_at, weighted_gex as lib_weighted_gex, percentile_rank as lib_percentile_rank,
)
from calc.cn.skew_dual import bs_delta as lib_bs_delta, pick_delta as lib_pick_delta  # noqa: E402
from calc.cn.iv_history import atm_term_from_day as lib_atm_term_from_day  # noqa: E402
import calc.us.index_options as calc_us  # noqa: E402

# 标的键 → 链路内部的 opt_code / 乘数（与 compute_ashare.TARGETS 一一对应）
TARGET_OPT = {
    "300ETF": ("OP510300.SH", 10000),
    "500ETF": ("OP510500.SH", 10000),
    "科创50ETF": ("OP588000.SH", 10000),
    "创业板ETF": ("OP159915.SZ", 10000),
    "MO": ("OP000852.SH", 100),
}
SINA_CODE = {
    "510300.SH": "sh510300", "510500.SH": "sh510500", "588000.SH": "sh588000",
    "159915.SZ": "sz159915", "159919.SZ": "sz159919", "159922.SZ": "sz159922",
    "000300.SH": "sh000300", "000905.SH": "sh000905", "000852.SH": "sh000852",
    "000688.SH": "sh000688", "399006.SZ": "sz399006",
}


def _rows(chain):
    """实时链接口有的返回 list、有的返回 DataFrame，统一成 list[dict]。"""
    if chain is None:
        return []
    if isinstance(chain, pd.DataFrame):
        return chain.to_dict("records")
    return list(chain)


# ============================================================ 取数：交易日历
def trade_dates(start_date: str, end_date: str):
    """A股交易日（升序）。失败返回 []。"""
    try:
        cal = dh.get_trade_calendar(start_date, end_date)
    except Exception as e:  # 单源故障不打断链路
        print("[lib] trade_calendar 失败:", repr(e)[:120])
        return []
    if cal is None or not len(cal):
        return []
    d = cal.copy()
    d["cal_date"] = d["cal_date"].astype(str)
    if "is_open" in d.columns:
        d = d[d["is_open"].astype(str).isin(["1", "1.0", "True", "true"])]
    return sorted(set(d["cal_date"].tolist()))


# ============================================================ 取数：期权链
def realtime_chains(write_date: str | None = None):
    """当日盘中 A股五轨期权链（DataHub 实时链），返回 (chain_df, basics_df)。

    chain_df  : ts_code / trade_date / exchange / close / settle / vol / oi
    basics_df : ts_code / opt_code / call_put / exercise_price / maturity_date / per_unit
    无数据返回 (None, None)。
    """
    rows, basics = [], []
    for key, (opt_code, mult) in TARGET_OPT.items():
        try:
            r = dh.get_realtime_option_chain(key)
        except Exception as e:
            print("[lib] realtime chain %s 失败: %s" % (key, repr(e)[:100]))
            continue
        ch = _rows((r or {}).get("chain"))
        if not ch:
            continue
        ex = "CFFEX" if key == "MO" else ("SZSE" if key == "创业板ETF" else "SSE")
        for x in ch:
            ts = str(x.get("ts_code") or "")
            if not ts:
                continue
            close = x.get("close")
            # 盘中快照不写结算价：DataHub 链里的 prev_settle 是**上一交易日**结算价，
            # 若当作当日结算价落盘，下游 px = settle 会用昨日价配今日现价，IV 被系统性抬高
            # （实测 500ETF 近月 ATM IV 会从 ~26% 虚增到 44%）。与原 fetch_rt 口径一致：settle=None。
            settle = None
            rows.append(dict(ts_code=ts, trade_date=write_date, exchange=ex,
                             close=close, settle=settle,
                             vol=x.get("vol"), oi=x.get("oi")))
            basics.append(dict(ts_code=ts, opt_code=opt_code,
                               call_put=x.get("call_put"),
                               exercise_price=x.get("exercise_price"),
                               maturity_date=str(x.get("maturity_date") or ""),
                               per_unit=mult))
    if not rows:
        return None, None
    return pd.DataFrame(rows), pd.DataFrame(basics).drop_duplicates("ts_code")


def historical_chain(target: str, trade_date: str):
    """历史日单标的期权链（DataHub 历史链，TuShare 通道可用时有效）。"""
    try:
        df = dh.get_option_chain(target, trade_date)
    except Exception as e:
        print("[lib] hist chain %s %s 失败: %s" % (target, trade_date, repr(e)[:100]))
        return None
    return df if (df is not None and len(df)) else None


# ============================================================ 取数：标的与期货
def underlying_hist(codes, start_date: str, end_date: str):
    """ETF / 指数日线，返回 DataFrame[code, kind, trade_date, close]。失败返回 None。"""
    etfs = [c for c in codes if c.endswith(".SH") or c.endswith(".SZ")]
    etf_codes = ["510300.SH", "510500.SH", "588000.SH", "159915.SZ", "159919.SZ", "159922.SZ"]
    idx_codes = ["000300.SH", "000905.SH", "000852.SH", "000688.SH", "399006.SZ"]
    rows = []
    for code in etfs:
        try:
            if code in etf_codes:
                d = dh.get_etf_daily(ts_code=code, start_date=start_date, end_date=end_date)
                kind = "etf"
            elif code in idx_codes:
                d = dh.get_index_daily(ts_code=code, start_date=start_date, end_date=end_date)
                kind = "index"
            else:
                continue
        except Exception as e:
            print("[lib] daily %s 失败: %s" % (code, repr(e)[:100]))
            continue
        if d is None or not len(d) or "close" not in d.columns:
            continue
        d = d.copy()
        d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
        d["code"], d["kind"] = code, kind
        rows.append(d[["code", "kind", "trade_date", "close"]])
    if not rows:
        return None
    return pd.concat(rows, ignore_index=True)


def spot_prices(codes):
    """标的实时价 {code: price}；逐只取，失败跳过。"""
    out = {}
    for code in codes:
        sym = SINA_CODE.get(code, code)
        try:
            px, _src = dh.get_realtime_spot(sym, label=code, verbose=False)
        except Exception:
            px = None
        if px:
            out[code] = float(px)
    return out


def futures_contract_daily(contract: str, start_date: str, end_date: str):
    """单个期货合约日线（DataHub）。失败返回 None。"""
    try:
        d = dh.get_futures_contract_daily(contract=contract, start_date=start_date,
                                          end_date=end_date)
    except Exception as e:
        print("[lib] futures %s 失败: %s" % (contract, repr(e)[:100]))
        return None
    return d if (d is not None and len(d)) else None


def futures_basic():
    """期货合约基础信息（DataHub）。失败返回 None。"""
    try:
        d = dh.get_futures_basic()
    except Exception as e:
        print("[lib] futures_basic 失败:", repr(e)[:100])
        return None
    return d if (d is not None and len(d)) else None


# ---------------- 命名隔离 ----------------
# options_utility 自带顶层 `config` 包，会与链路里 email_alert 依赖的
# macro_monthly_report/config.py（DWS_CLI / EMAIL_* / get_auth_code）重名。
# 库导入完成后把该模块从 sys.modules 摘掉，避免后续通知/邮件模块解析到错误实现。
_cfg = sys.modules.get("config")
if _cfg is not None and "/projects/options_utility/" in str(getattr(_cfg, "__file__", "")):
    del sys.modules["config"]