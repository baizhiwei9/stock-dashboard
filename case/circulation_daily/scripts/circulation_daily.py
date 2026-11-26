#!/usr/bin/env python3.13
"""
环流日报 - 纯Python脚本版 v2.0
数据源: tushare + akshare + web scraping, 零search_web, 零LLM token消耗

输出: YYYY-MM-DD_环流日报.md
"""

import os
import sys
import re
import json
import time
import sqlite3
import subprocess
import pkgutil
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ══════════════════════════════════════════════════════════════════
# DataHub 统一数据入口（禁止直连 tushare / akshare 取数）
# 业务侧 pro.xxx() / ak.xxx() 调用签名保持原样，内部全部转 DataHub
# ══════════════════════════════════════════════════════════════════
import os as _os, sys as _sys  # noqa: E402
import pandas as _pd  # noqa: E402
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
    return '/opt/coze/.fuse_drive/dataHub_三级体系'

_HUB_ROOT = _hub_root_path()


_HUB_PATHS = [
    _HUB_ROOT,
    '/root/.coze/agents/7649998561689780486/workspace/codeact/scripts',
]
for _hp in _HUB_PATHS:
    if _os.path.isdir(_os.path.join(_hp, 'data_hub')) and _hp not in _sys.path:
        _sys.path.insert(0, _hp)
        break
# 清理可能抢先插入的旧 data_hub 副本（外汇掉期点等），确保全局唯一副本
for _bad in ('/Coze/Drive/外汇掉期点', '/opt/coze/.fuse_drive/外汇掉期点'):
    while _bad in _sys.path:
        _sys.path.remove(_bad)
_old = _sys.modules.get('data_hub')
if _old is not None and 'dataHub_三级体系' not in str(getattr(_old, '__file__', '')):
    for _k in [k for k in list(_sys.modules) if k == 'data_hub' or k.startswith('data_hub.')]:
        del _sys.modules[_k]
try:
    import data_hub as dh
    if 'dataHub_三级体系' not in str(getattr(dh, '__file__', '')):
        print(f"[DataHub] ⚠️ 加载到非统一副本: {dh.__file__}")
except Exception as _e:
    dh = None
    print(f"[DataHub] 导入失败，回退直连: {_e}")


def _dh_pick(df, trade_date):
    """DataHub 日线可能返回多日，按 trade_date 精确取行。"""
    if df is None or len(df) == 0 or not trade_date:
        return df
    _td = str(trade_date).replace('-', '')
    for _c in ('trade_date', 'date'):
        if _c in df.columns:
            _m = df[df[_c].astype(str).str.replace('-', '') == _td]
            if len(_m):
                return _m.reset_index(drop=True)
    return df.tail(1).reset_index(drop=True)


def _dh_asc(df, col='日期'):
    """DataHub 部分接口返回降序，统一转升序（业务侧按 iloc[-1] 取最新）。"""
    if df is None or len(df) == 0:
        return df
    for _c in (col, 'date', 'trade_date', 'TRADE_DATE'):
        if _c in df.columns:
            try:
                return df.sort_values(_c).reset_index(drop=True)
            except Exception:
                return df
    return df


class _ProProxy:
    """tushare pro 兼容层（全部转 DataHub）。"""

    def trade_cal(self, exchange='SSE', start_date=None, end_date=None, is_open='1', **kw):
        _d = dh.get_trade_dates(start_date, end_date, exchange) if dh else None
        return _pd.DataFrame({'cal_date': sorted(_d or [])})

    def us_tycr(self, start_date=None, end_date=None, **kw):
        _d = dh.get_us_treasury_yield(start_date, end_date) if dh else None
        if _d is None or len(_d) == 0:
            return None
        return _d.rename(columns={'date': 'trade_date'})

    def yc_cb(self, start_date=None, end_date=None, **kw):
        _d = dh.get_bond_yield() if dh else None
        if _d is None or len(_d) == 0:
            return None
        _d = _d[_d['曲线名称'].astype(str).str.contains('国债')].copy()
        _rows = []
        for _, _r in _d.iterrows():
            _dt = str(_r['日期']).replace('-', '')
            for _term, _col in ((1, '1年'), (2, '2年'), (5, '5年'), (10, '10年'), (30, '30年')):
                if _col in _d.columns:
                    _v = _r[_col]
                    if _v is not None and str(_v) != 'nan':
                        _rows.append({'trade_date': _dt, 'curve_term': _term, 'yield': float(_v)})
        if not _rows:
            return None
        _out = _pd.DataFrame(_rows)
        if start_date:
            _out = _out[_out['trade_date'] >= str(start_date)]
        if end_date:
            _out = _out[_out['trade_date'] <= str(end_date)]
        return _out.sort_values(['trade_date', 'curve_term']).reset_index(drop=True)

    def shibor(self, start_date=None, end_date=None, **kw):
        _d = dh.get_shibor_all() if dh else None
        if _d is None or len(_d) == 0:
            return None
        _d = _dh_asc(_d, '日期').copy()
        _d['trade_date'] = _d['日期'].astype(str).str.replace('-', '')
        _map = {'O/N-定价': 'on', '1W-定价': '1w', '2W-定价': '2w', '1M-定价': '1m',
                '3M-定价': '3m', '6M-定价': '6m', '9M-定价': '9m', '1Y-定价': '1y'}
        _d = _d.rename(columns={k: v for k, v in _map.items() if k in _d.columns})
        if start_date:
            _d = _d[_d['trade_date'] >= str(start_date)]
        if end_date:
            _d = _d[_d['trade_date'] <= str(end_date)]
        return _d.sort_values('trade_date').reset_index(drop=True)

    def fx_daily(self, ts_code=None, start_date=None, end_date=None, **kw):
        return dh.get_fx_daily(ts_code or 'USDCNH.FXCM', start_date, end_date) if dh else None

    def index_daily(self, ts_code=None, start_date=None, end_date=None, fields=None, **kw):
        _d = dh.get_index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date) if dh else None
        if _d is None or len(_d) == 0:
            return None
        if 'trade_date' not in _d.columns:
            for _c in ('date', '日期'):
                if _c in _d.columns:
                    _d = _d.rename(columns={_c: 'trade_date'})
                    break
        _d['trade_date'] = _d['trade_date'].astype(str).str.replace('-', '')
        _d = _d.sort_values('trade_date').reset_index(drop=True)
        if fields:
            _keep = [c.strip() for c in str(fields).split(',')]
            _keep = [c for c in _keep if c in _d.columns]
            if _keep:
                _d = _d[_keep]
        return _d

    def daily(self, trade_date=None, ts_code=None, start_date=None, end_date=None, fields=None, **kw):
        if trade_date:
            _d = dh.get_market_daily(trade_date=trade_date) if dh else None
        else:
            _d = dh.get_stock_daily(ts_code=ts_code, start_date=start_date,
                                    end_date=end_date) if dh else None
        if _d is None or len(_d) == 0:
            return None
        if fields:
            _keep = [c.strip() for c in str(fields).split(',')]
            _keep = [c for c in _keep if c in _d.columns]
            if _keep:
                _d = _d[_keep]
        return _d

    def daily_basic(self, trade_date=None, ts_code=None, fields=None, **kw):
        _d = dh.get_daily_basic(trade_date=trade_date) if dh else None
        if _d is None or len(_d) == 0:
            return None
        if fields:
            _keep = [c.strip() for c in str(fields).split(',')]
            _keep = [c for c in _keep if c in _d.columns]
            if _keep:
                _d = _d[_keep]
        return _d


class _AKProxy:
    """akshare 兼容层（全部转 DataHub）。"""

    def bond_zh_us_rate(self, start_date=None, **kw):
        _d = dh.get_bond_zh_us_rate() if dh else None
        if _d is None or len(_d) == 0:
            return None
        _d = _d.copy()
        _d['日期'] = _pd.to_datetime(_d['date'], errors='coerce')
        _rev = {'y2': '美国国债收益率2年', 'y5': '美国国债收益率5年',
                'y10': '美国国债收益率10年', 'y30': '美国国债收益率30年',
                'cn2y': '中国国债收益率2年', 'cn5y': '中国国债收益率5年',
                'cn10y': '中国国债收益率10年', 'cn30y': '中国国债收益率30年'}
        _d = _d.rename(columns={k: v for k, v in _rev.items() if k in _d.columns})
        return _dh_asc(_d, '日期')

    def macro_china_shibor_all(self, **kw):
        _d = dh.get_shibor_all() if dh else None
        return _dh_asc(_d, '日期') if _d is not None else None

    def fx_spot_quote(self, **kw):
        return dh.get_fx_spot_quote() if dh else None

    def stock_zh_index_daily_em(self, symbol='sh000001', **kw):
        _sym = str(symbol)
        _num = _sym[2:] if _sym[:2].lower() in ('sh', 'sz') else _sym
        _ts = f"{_num}.{'SH' if _sym[:2].lower() == 'sh' else 'SZ'}"
        _d = dh.get_index_daily(ts_code=_ts) if dh else None
        if _d is None or len(_d) == 0:
            return None
        _d = _d.copy()
        for _c in ('trade_date', 'date'):
            if _c in _d.columns:
                _d['日期'] = _pd.to_datetime(_d[_c].astype(str), errors='coerce')
                break
        if 'amount' in _d.columns:
            _d['成交额'] = _d['amount']
        if 'vol' in _d.columns:
            _d['成交量'] = _d['vol']
        return _dh_asc(_d, '日期')

    def stock_zh_a_spot_em(self, **kw):
        _d = dh.get_stock_spot_em() if dh else None
        if _d is None or len(_d) == 0:
            return None
        _d = _d.copy()
        if 'amount' in _d.columns and '成交额' not in _d.columns:
            _d['成交额'] = _d['amount']
        return _d

    def macro_china_lpr(self, **kw):
        _d = dh.get_lpr_history() if dh else None
        if _d is None or len(_d) == 0:
            return None
        return _dh_asc(_d, 'TRADE_DATE')


# ══════════════════════════════════════════════════════════════════


# 1Y AAA同业存单流动性监控模块
_LOCAL_MODULES = (os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts/modules'
if _LOCAL_MODULES not in sys.path:
    sys.path.insert(0, _LOCAL_MODULES)
try:
    from cd_liquidity_monitor import run_monitor, render_report as render_cd_report
    CD_LIQUIDITY_AVAILABLE = True
except Exception as _cd_import_err:
    CD_LIQUIDITY_AVAILABLE = False
    print(f"[WARN] 同业存单流动性监控模块导入失败: {_cd_import_err}")


# Python 3.12+ compatibility: pkgutil.ImpImporter removed
if not hasattr(pkgutil, 'ImpImporter'):
    pkgutil.ImpImporter = type('ImpImporter', (), {})

# akshare 重型依赖 mock (py_mini_racer/curl_cffi 安装超时，不影响我们用的函数)
import sys as _sys, types as _types
sys = _sys  # 确保全局 sys 可用

# 降准降息预期利差监控模块（1Y−3M 国开/存单，五档判定 + 场景判断）
try:
    from cd_gk_spread_monitor import run_monitor as run_gk_monitor, render_report as render_gk_report
    GK_SPREAD_AVAILABLE = True
except Exception as _gk_import_err:
    GK_SPREAD_AVAILABLE = False
    print(f"[WARN] 降准降息预期利差监控模块导入失败: {_gk_import_err}")

# 1Y外汇掉期点 × 需求传导监控模块（rrr = R + e 框架）
try:
    from swap_demand_monitor import run_swap_monitor, render_report as render_swap_report
    SWAP_DEMAND_AVAILABLE = True
except Exception as _sw_import_err:
    SWAP_DEMAND_AVAILABLE = False
    print(f"[WARN] 掉期点需求传导模块导入失败: {_sw_import_err}")

# 美债利率-流动性传导监控模块
if _LOCAL_MODULES not in sys.path:
    sys.path.insert(0, _LOCAL_MODULES)
try:
    from ust_liquidity_monitor import run_ust_monitor
    UST_MONITOR_AVAILABLE = True
except Exception as _ust_import_err:
    UST_MONITOR_AVAILABLE = False
    print(f"[WARN] 美债传导监控模块导入失败: {_ust_import_err}")

# 美元流动性水位仪模块
try:
    from usd_liquidity_gauge import run as run_usd_gauge, render_md as render_usd_gauge_md
    USD_GAUGE_AVAILABLE = True
except Exception as _gauge_import_err:
    USD_GAUGE_AVAILABLE = False
    print(f"[WARN] 美元流动性水位仪导入失败: {_gauge_import_err}")

try:
    from usd_fingerprint import run as run_usd_fp, render_md as render_usd_fp_md
    USD_FP_AVAILABLE = True
except Exception as _fp_import_err:
    USD_FP_AVAILABLE = False
    print(f"[WARN] 美元流动性指纹侦查导入失败: {_fp_import_err}")

def _make_mock_module(name):
    m = _types.ModuleType(name)
    m.__path__ = []
    m.__package__ = name
    return m
for _mn in ['py_mini_racer']:
    if _mn not in _sys.modules:
        _m = _make_mock_module(_mn)
        class _DummyMiniRacer:
            def __init__(self, *a, **kw): pass
            def call(self, *a, **kw): return None
            def eval(self, *a, **kw): return None
        _m.MiniRacer = _DummyMiniRacer
        _sys.modules[_mn] = _m
# 2026-09-17修复：curl_cffi已安装(0.15.0)，不再mock，避免覆盖真实Session
# 仅在curl_cffi确实无法导入时才降级mock
try:
    import curl_cffi
    import curl_cffi.requests
except ImportError:
    for _mn in ['curl_cffi', 'curl_cffi.requests']:
        if _mn not in _sys.modules:
            _m = _make_mock_module(_mn)
            _sys.modules[_mn] = _m

# ============ 配置 ============
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
from hub_root import tushare_token as _tushare_token, smtp_password as _smtp_password  # noqa: E402

TUSHARE_TOKEN = _tushare_token()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "outputs")
CACHE_FILE = os.path.join(SCRIPT_DIR, "cache.json")

# 是否启用 Tushare 直接调用（默认 False，避免 IP 超限）
# 2026-08-14：沙箱出口IP不固定，Tushare 个人版无白名单，默认禁用
# 如需恢复，改为 True 即可
USE_TUSHARE: bool = True  # DataHub 统一入口（proxy）

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/json'
}

# 警戒线
ALERT_RESERVE = 3000000    # 百万美元 (3万亿)
ALERT_RRP = 375000         # 百万美元 → 已废弃, 改用ON RRP阈值
ALERT_RRP_ON = 20000       # 百万美元 (ON RRP $20B = 枯竭警戒线)
ALERT_RRP_ON_RED = 5000    # 百万美元 (ON RRP $5B = 归零警戒线)
ALERT_SRF_YELLOW = 5000    # 百万美元 (50亿)
ALERT_SRF_RED = 10000      # 百万美元 (100亿)
ALERT_HIBOR = 3.0          # %
ALERT_SWAP_YELLOW = 2.5    # % (年化掉期率绝对值)
ALERT_SWAP_RED = 3.0       # % (年化掉期率绝对值)
ALERT_JGB_YELLOW = 1.0     # %
ALERT_JGB_RED = 1.5        # %
ALERT_JGB_DAILY_YELLOW = 5  # BP
ALERT_JGB_DAILY_RED = 10    # BP


# ============ 工具函数 ============

def safe_float(val, default=None):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def calc_bp(current, previous):
    """计算BP变化 (百分比→BP)"""
    if current is None or previous is None:
        return None
    return round((current - previous) * 100, 1)


def fmt(val, suffix="", default="N/A"):
    """智能格式化数值"""
    if val is None:
        return default
    if isinstance(val, float):
        if abs(val) < 0.001:
            return f"0{suffix}"
        elif abs(val) < 1:
            return f"{val:.4f}{suffix}"
        elif abs(val) < 100:
            return f"{val:.2f}{suffix}"
        else:
            return f"{val:.1f}{suffix}"
    return f"{val}{suffix}"


def fmt_bp(val, default="-"):
    """格式化BP变化，1位小数"""
    if val is None:
        return default
    if abs(val) < 0.05:
        return "0"
    return f"{val:.1f}"


def alert_tag(val, yellow, red, reverse=False):
    if val is None:
        return ""
    if reverse:
        if val < yellow: return "🔴"
        elif val < red: return "🟡"
    else:
        if val > red: return "🔴"
        elif val > yellow: return "🟡"
    return "✅"


# ============ data_hub EDB 统一取数层（2026-11-13 接入）============
# 设计原则：**EDB 优先，原有多源爬虫降为兜底**。
# EDB（/Coze/Drive/外汇掉期点/data_hub/edb）内部自带三级降级链 + SQLite 缓存 +
# 新鲜度判定，比本脚本各自 requests/爬虫更稳，且全平台口径统一。
# 全部 EDB 取数失败时，各 fetch_* 仍会走自己原有的降级逻辑，日报不会空。
# ===== 四子图（存单与降准降息预期利差）=====
# 环流日报出报告时同步渲染四子图，图片落 outputs/images/，
# 以相对路径嵌入报告，打开 md 就能直接看图。
NCD_PLOT_SCRIPT = '/Coze/Drive/存单和_降准预期/scripts/plot_ncd_spread.py'
NCD_PLOT_OUT = os.path.join(OUTPUT_DIR, 'images')


def render_ncd_plot():
    """调用四子图脚本，返回图片绝对路径；失败返回 None（不拖垮日报主体）。"""
    if not os.path.exists(NCD_PLOT_SCRIPT):
        print(f"  [WARN] 四子图脚本不存在: {NCD_PLOT_SCRIPT}")
        return None
    try:
        os.makedirs(NCD_PLOT_OUT, exist_ok=True)
        r = subprocess.run(
            [sys.executable, NCD_PLOT_SCRIPT, '--out-dir', NCD_PLOT_OUT],
            capture_output=True, text=True, timeout=420,
            cwd=os.path.dirname(NCD_PLOT_SCRIPT),
        )
    except Exception as e:
        print(f"  [WARN] 四子图脚本调用异常: {e}")
        return None
    if r.returncode != 0:
        print(f"  [WARN] 四子图脚本失败({r.returncode}): {(r.stderr or '')[-300:]}")
        return None
    for line in (r.stdout or '').splitlines():
        if line.startswith('[plot] saved'):
            return line.split('saved', 1)[1].strip()
    print(f"  [WARN] 四子图脚本无输出路径: {(r.stdout or '')[-200:]}")
    return None


EDB_ROOT = _HUB_ROOT  # 统一 DataHub 根（原指向外汇掉期点旧副本）
EDB_AVAILABLE = False
try:
    if EDB_ROOT not in sys.path:
        sys.path.insert(0, EDB_ROOT)
    from data_hub import edb as _EDB
    EDB_AVAILABLE = True
except Exception as _edb_err:
    print(f"[WARN] data_hub EDB 导入失败，全部回退原生取数: {_edb_err}")


def edb_point(series_id):
    """取 EDB 单指标最新值。

    Args:
        series_id: EDB 指标编号，如 ``RATE.US.10Y``。

    Returns:
        tuple: ``(value, date_str)``；取不到返回 ``(None, None)``。
    """
    if not EDB_AVAILABLE:
        return None, None
    try:
        r = _EDB.get_series_result(series_id)
    except Exception:
        return None, None
    if not isinstance(r, dict) or not r.get('ok'):
        return None, None
    val = r.get('latest_value')
    if val is None:
        return None, None
    return val, str(r.get('latest_date') or '')


def edb_rows(series_id, limit=None):
    """取 EDB 序列，转成按日期升序的 dict 列表。

    Args:
        series_id: EDB 指标编号。
        limit: 只取末尾 N 条（最新在最后），None 表示全取。

    Returns:
        list[dict]: ``[{'date': 'YYYY-MM-DD', 'value': float}, ...]``，失败返回空列表。
    """
    if not EDB_AVAILABLE:
        return []
    try:
        r = _EDB.get_series_result(series_id)
    except Exception:
        return []
    df = r.get('data') if isinstance(r, dict) else None
    if df is None or len(df) == 0:
        return []
    rows = []
    for d, v in zip(df['date'], df['value']):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv != fv:  # NaN
            continue
        rows.append({'date': str(d)[:10], 'value': fv})
    if limit:
        rows = rows[-limit:]
    return rows


def edb_values(series_id, limit=None):
    """取 EDB 序列的值列表（按日期升序，最新在最后）。"""
    return [x['value'] for x in edb_rows(series_id, limit)]


def edb_prev(series_id):
    """取 EDB 序列倒数第二个值，用于算日变动/BP 变化。"""
    vals = edb_values(series_id, limit=3)
    return vals[-2] if len(vals) >= 2 else None


def edb_bp_change(series_id, n=1):
    """取 EDB 序列末尾回溯 n 个位置的变动（BP）。"""
    vals = edb_values(series_id, limit=n + 1)
    if len(vals) < n + 1:
        return None
    return round((vals[-1] - vals[-1 - n]) * 100, 2)


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_cache(data):
    with open(CACHE_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ 数据获取 ============



def get_tushare():
    if not USE_TUSHARE:
        return None
    # DataHub 统一入口（原 ts.pro_api 已收敛）
    return _ProProxy()


def fetch_us_treasury(pro, date_str):
    """美债收益率 (data_hub EDB → tushare回退5天 → akshare fallback)"""
    # ---- 一级：data_hub EDB（统一口径）----
    if EDB_AVAILABLE:
        r = {}
        for k, sid in (('y2', 'RATE.US.2Y'), ('y5', 'RATE.US.5Y'),
                       ('y10', 'RATE.US.10Y'), ('y30', 'RATE.US.30Y')):
            v, _d = edb_point(sid)
            if v is not None:
                r[k] = float(v)
        if len(r) >= 3:
            return r

    # tushare: 回退最多5天（覆盖周末+假日）    """美债收益率 (tushare回退5天 → akshare fallback)"""
    # tushare: 回退最多5天（覆盖周末+假日）
    for i in range(6):
        d = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=i)).strftime('%Y%m%d')
        try:
            df = pro.us_tycr(start_date=d, end_date=d)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                result = {k: safe_float(row.get(k)) for k in ['y2','y5','y10','y30']}
                if any(v is not None for v in result.values()):
                    if i > 0:
                        print(f"  [INFO] us_treasury用tushare回退{d}")
                    return result
        except:
            pass
    
    # akshare fallback
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            row = df.iloc[-1]
            col_map = {'y2': '美国国债收益率2年', 'y5': '美国国债收益率5年',
                       'y10': '美国国债收益率10年', 'y30': '美国国债收益率30年'}
            result = {k: safe_float(row.get(col)) for k, col in col_map.items()}
            if any(v is not None for v in result.values()):
                print(f"  [INFO] us_treasury用akshare bond_zh_us_rate备选")
                return result
    except Exception as e:
        print(f"  [WARN] akshare美债收益率(bond_zh_us_rate)获取失败: {e}")
    
    return None


def fetch_us_treasury_5d(pro):
    """近5日美债 (tushare → akshare fallback)"""
    try:
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
        df = pro.us_tycr(start_date=start, end_date=end)
        if df is not None and not df.empty:
            return df.tail(5)
    except:
        pass
    
    # akshare fallback: 取近5日
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            df = df.tail(5).copy()
            # 转换列名为tushare兼容格式
            rename_map = {
                '美国国债收益率2年': 'y2', '美国国债收益率5年': 'y5',
                '美国国债收益率10年': 'y10', '美国国债收益率30年': 'y30',
                '日期': 'trade_date'
            }
            df = df.rename(columns=rename_map)
            return df
    except:
        pass
    return None


def fetch_cn_bond_yield(pro, date_str):
    """中国国债收益率 (data_hub EDB → tushare yc_cb → akshare fallback)"""
    # ---- 一级：data_hub EDB（统一口径）----
    if EDB_AVAILABLE:
        r = {}
        for term, sid in (('2Y', 'RATE.CN.2Y'), ('5Y', 'RATE.CN.5Y'),
                          ('10Y', 'RATE.CN.10Y'), ('30Y', 'RATE.CN.30Y')):
            v, _d = edb_point(sid)
            if v is not None:
                r[term] = float(v)
        if r.get('10Y'):
            return r

    # Method 1: tushare yc_cb    """中国国债收益率 (tushare yc_cb → akshare bond_china_yield fallback)"""
    # Method 1: tushare yc_cb
    try:
        df = pro.yc_cb(start_date=date_str, end_date=date_str)
        if df is not None and not df.empty:
            result = {}
            for term in [1, 2, 5, 10, 30]:
                row = df[df['curve_term'] == term]
                if not row.empty:
                    result[f'{term}Y'] = safe_float(row['yield'].values[0])
            if result:
                return result
    except:
        pass
    
    # Method 2: akshare bond_zh_us_rate (更稳定的备选)
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            row = df.iloc[-1]
            result = {}
            col_map = {'2Y': '中国国债收益率2年', '5Y': '中国国债收益率5年',
                       '10Y': '中国国债收益率10年', '30Y': '中国国债收益率30年'}
            for key, col in col_map.items():
                val = safe_float(row.get(col))
                if val:
                    result[key] = val
            if result:
                print(f"  [INFO] cn_bond_yield用akshare bond_zh_us_rate备选")
                return result
    except Exception as e:
        print(f"  [WARN] akshare国债收益率(bond_zh_us_rate)获取失败: {e}")
    return None


def fetch_shibor(pro, date_str):
    """Shibor (data_hub EDB 主, akshare 次, tushare 备), 当天无数据时回退到上一交易日"""
    # ============== 一级：data_hub EDB（统一口径）==============
    if EDB_AVAILABLE:
        _map = {'ON': 'LIQ.CN.SHIBOR.ON', '1W': 'LIQ.CN.SHIBOR.1W',
                '2W': 'LIQ.CN.SHIBOR.2W', '1M': 'LIQ.CN.SHIBOR.1M',
                '3M': 'LIQ.CN.SHIBOR.3M', '6M': 'LIQ.CN.SHIBOR.6M',
                '1Y': 'LIQ.CN.SHIBOR.1Y'}
        r = {}
        dates = []
        for k, sid in _map.items():
            v, d = edb_point(sid)
            if v is not None:
                r[k] = float(v)
                if d:
                    dates.append(d)
        if r.get('ON') is not None and r.get('1W') is not None:
            d0 = max(dates) if dates else datetime.now().strftime('%Y-%m-%d')
            r['_date'] = d0.replace('-', '')
            r['_is_fallback'] = (r['_date'] != date_str)
            r['_source'] = 'data_hub/EDB'
            return r

    # ============== 二级：akshare macro_china_shibor_all ==============    """Shibor (akshare macro_china_shibor_all 主, tushare 备), 当天无数据时回退到上一交易日"""
    # ============== 一级：akshare macro_china_shibor_all ==============
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.macro_china_shibor_all()
        if df is not None and not df.empty:
            df['日期'] = pd.to_datetime(df['日期'])
            target = datetime.strptime(date_str, '%Y%m%d')
            # 找 <= target 的最近一行
            df_past = df[df['日期'] <= pd.Timestamp(target)]
            if not df_past.empty:
                row = df_past.iloc[-1]
                row_date = row['日期'].strftime('%Y%m%d')
                result = {
                    'ON': safe_float(row.get('O/N-定价')),
                    '1W': safe_float(row.get('1W-定价')),
                    '2W': safe_float(row.get('2W-定价')),
                    '1M': safe_float(row.get('1M-定价')),
                    '3M': safe_float(row.get('3M-定价')),
                    '6M': safe_float(row.get('6M-定价')),
                    '1Y': safe_float(row.get('1Y-定价')),
                    '_date': row_date,
                    '_is_fallback': (row_date != date_str),
                    '_source': 'akshare',
                }
                return result
    except:
        pass

    # ============== 二级：tushare ==============
    for i in range(6):
        d = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=i)).strftime('%Y%m%d')
        try:
            df = pro.shibor(start_date=d, end_date=d)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                result = {
                    'ON': safe_float(row.get('on')),
                    '1W': safe_float(row.get('1w')),
                    '2W': safe_float(row.get('2w')),
                    '1M': safe_float(row.get('1m')),
                    '3M': safe_float(row.get('3m')),
                    '6M': safe_float(row.get('6m')),
                    '1Y': safe_float(row.get('1y')),
                    '_date': d,
                    '_is_fallback': (i > 0),
                    '_source': 'tushare',
                }
                return result
        except:
            pass
    return None


def fetch_shibor_5d(pro):
    """近10日Shibor (akshare 主, tushare 备)"""
    # 一级：akshare
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.macro_china_shibor_all()
        if df is not None and not df.empty and len(df) >= 2:
            df = df.tail(10).copy()
            df = df.rename(columns={
                '日期': 'date',
                'O/N-定价': 'on',
                '1W-定价': '1w',
                '2W-定价': '2w',
                '1M-定价': '1m',
                '3M-定价': '3m',
                '6M-定价': '6m',
                '1Y-定价': '1y',
            })
            return df
    except:
        pass

    # 二级：tushare
    try:
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=18)).strftime('%Y%m%d')
        df = pro.shibor(start_date=start, end_date=end)
        if df is not None and not df.empty:
            return df.tail(10)
    except:
        pass
    return None


def fetch_usdcnh(pro, date_str):
    """USDCNH (data_hub EDB 离岸CNH 主, akshare 在岸CNY兜底, tushare离岸备)"""
    # ============== 一级：data_hub EDB 离岸 CNH ==============
    if EDB_AVAILABLE:
        v, _d = edb_point('CN.FX.CNH')
        if v is not None:
            return float(v), 'data_hub/EDB(CNH)'

    # ============== 二级：akshare fx_spot_quote (在岸CNY即时价) ==============    """USDCNH (akshare 在岸CNY兜底, tushare离岸备)"""
    # ============== 一级：akshare fx_spot_quote (在岸CNY即时价) ==============
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.fx_spot_quote()
        if df is not None and not df.empty:
            row = df[df['货币对'] == 'USD/CNY']
            if not row.empty:
                return safe_float(row.iloc[0]['买报价']), 'cn_y_spot'
    except:
        pass

    # ============== 二级：tushare USDCNH ==============
    for d in [date_str, (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')]:
        try:
            df = pro.fx_daily(ts_code='USDCNH.FXCM', start_date=d, end_date=d)
            if df is not None and not df.empty:
                return safe_float(df.iloc[-1]['bid_close']), 'tushare_cnh'
        except:
            pass
    return None, None


def fetch_bond_zh_us():
    """中美利差 (akshare)"""
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            return df.tail(5)
    except:
        pass
    return None


def fetch_bond_zh_us_history(days=252):
    """中美国债收益率历史（用于分位/环比回填）"""
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            return df.tail(days).reset_index(drop=True)
    except:
        pass
    return None


def fetch_volume_percentile(pro, date_str, lookback=252):
    """
    全A成交额年度分位数
    方法: 上证指数(000001.SH) + 深证综指(399106.SZ) 合并 ≈ 全A
    备用: 逐日daily汇总(慢) → 市值前2000(无历史分位)
    返回 dict: {today_amount(万亿), percentile, high_1y, low_1y, tag, top2000_fallback}
    """
    try:
        end_dt = datetime.strptime(date_str, '%Y%m%d')
        start_dt = end_dt - timedelta(days=int(lookback * 1.6))
        start_str = start_dt.strftime('%Y%m%d')
        
        # 1) 上证 + 深证综指 合并（高效，2次API调用）
        amounts = None
        if pro is not None:
            try:
                df_sh = pro.index_daily(ts_code='000001.SH', start_date=start_str,
                                         end_date=date_str, fields='trade_date,amount')
                df_sz = pro.index_daily(ts_code='399106.SZ', start_date=start_str,
                                         end_date=date_str, fields='trade_date,amount')
                if (df_sh is not None and len(df_sh) >= 60 and 'amount' in df_sh.columns and
                    df_sz is not None and len(df_sz) >= 60 and 'amount' in df_sz.columns):
                    # 按日期merge
                    df_sh = df_sh.rename(columns={'amount': 'amt_sh'})
                    df_sz = df_sz.rename(columns={'amount': 'amt_sz'})
                    df_merged = df_sh.merge(df_sz, on='trade_date', how='inner')
                    df_merged = df_merged.sort_values('trade_date').tail(lookback)
                    df_merged['total'] = df_merged['amt_sh'] + df_merged['amt_sz']
                    amounts = df_merged['total'].dropna().values  # 千元
            except Exception as e:
                print(f"  [警告] tushare双指数合并失败: {e}")
        
        # 1.5) AKShare 备用：上证指数 + 深证成指历史行情
        if amounts is None or len(amounts) < 60:
            try:
                ak = _AKProxy()  # DataHub 中转
                # 上证指数
                df_sh_ak = ak.stock_zh_index_daily_em(symbol="sh000001")
                # 深证成指
                df_sz_ak = ak.stock_zh_index_daily_em(symbol="sz399001")
                if (df_sh_ak is not None and not df_sh_ak.empty and
                    df_sz_ak is not None and not df_sz_ak.empty and
                    '成交额' in df_sh_ak.columns and '成交额' in df_sz_ak.columns):
                    df_sh_ak = df_sh_ak.copy()
                    df_sz_ak = df_sz_ak.copy()
                    df_sh_ak['trade_date'] = pd.to_datetime(df_sh_ak['日期']).dt.strftime('%Y%m%d')
                    df_sz_ak['trade_date'] = pd.to_datetime(df_sz_ak['日期']).dt.strftime('%Y%m%d')
                    df_sh_ak = df_sh_ak.rename(columns={'成交额': 'amt_sh'})
                    df_sz_ak = df_sz_ak.rename(columns={'成交额': 'amt_sz'})
                    df_merged = df_sh_ak[['trade_date', 'amt_sh']].merge(
                        df_sz_ak[['trade_date', 'amt_sz']], on='trade_date', how='inner')
                    df_merged = df_merged.sort_values('trade_date')
                    df_merged = df_merged[df_merged['trade_date'] <= date_str]
                    df_merged = df_merged.tail(lookback)
                    df_merged['total'] = df_merged['amt_sh'] + df_merged['amt_sz']
                    amounts = df_merged['total'].dropna().values  # 元 (AKShare单位是元)
                    # 转换为千元以匹配tushare口径（tushare index_daily amount 单位是千元）
                    amounts = amounts / 1000.0
            except Exception as e:
                print(f"  [警告] AKShare指数日线合并失败: {e}")
        
        # 2) 备用：逐日取全市场成交额（慢，约252次API，限流风险）
        if (amounts is None or len(amounts) < 60) and pro is not None:
            try:
                trade_dates_df = pro.trade_cal(exchange='SSE', start_date=start_str,
                                                end_date=date_str, is_open='1')
                if trade_dates_df is not None and len(trade_dates_df) > 0:
                    dates = trade_dates_df['cal_date'].tolist()[-lookback:]
                    daily_amounts = []
                    for d in dates:
                        try:
                            df_day = pro.daily(trade_date=d, fields='amount')
                            if df_day is not None and len(df_day) > 0:
                                daily_amounts.append({'date': d, 'amount': df_day['amount'].sum()})
                            time.sleep(0.15)
                        except:
                            pass
                    if len(daily_amounts) >= 60:
                        daily_amounts.sort(key=lambda x: x['date'])
                        amounts = np.array([x['amount'] for x in daily_amounts])
            except:
                pass
        
        # 3) 最终备用：取市值前2000当日成交额（无历史分位）
        if amounts is None or len(amounts) < 60:
            if pro is not None:
                try:
                    df_basics = pro.daily_basic(trade_date=date_str, fields='ts_code,total_mv,amount')
                    if df_basics is not None and len(df_basics) > 0:
                        df_basics = df_basics.dropna(subset=['total_mv', 'amount'])
                        df_basics = df_basics.sort_values('total_mv', ascending=False).head(2000)
                        today_amount = df_basics['amount'].sum() / 1e9
                        return {
                            'today_amount': round(today_amount, 2),
                            'percentile': None,
                            'high_1y': None,
                            'low_1y': None,
                            'tag': '⚠️',
                            'top2000_fallback': True,
                        }
                except:
                    pass
            # AKShare 兜底：A股实时行情近似当日总成交额
            try:
                ak = _AKProxy()  # DataHub 中转
                df_spot = ak.stock_zh_a_spot_em()
                if df_spot is not None and not df_spot.empty and '成交额' in df_spot.columns:
                    df_spot = df_spot.dropna(subset=['成交额'])
                    total_amount = df_spot['成交额'].sum() / 1e8  # 元→亿元
                    total_wanyi = total_amount / 10000  # 亿元→万亿
                    return {
                        'today_amount': round(total_wanyi, 2),
                        'percentile': None,
                        'high_1y': None,
                        'low_1y': None,
                        'tag': '⚠️',
                        'top2000_fallback': True,
                    }
            except:
                pass
            return None
        
        # 计算分位数
        today_amt = amounts[-1] if len(amounts) > 0 else None
        if today_amt is None:
            return None
        
        today_wanyi = today_amt / 1e9  # 千元→万亿
        pct = round(np.sum(amounts <= today_amt) / len(amounts) * 100, 1)
        high_1y = round(np.max(amounts) / 1e9, 2)
        low_1y = round(np.min(amounts) / 1e9, 2)
        
        # 分位数标签: ≥80%🔴放量过热, ≤20%🔴缩量危险
        if pct >= 95:
            tag = "🔴🔴 极度放量"
        elif pct >= 80:
            tag = "🔴 放量预警"
        elif pct >= 60:
            tag = "🟠 偏高"
        elif pct >= 40:
            tag = "🟢 正常"
        elif pct >= 20:
            tag = "🟠 偏低"
        else:
            tag = "🔴 缩量预警"
        
        return {
            'today_amount': round(today_wanyi, 2),
            'percentile': pct,
            'high_1y': high_1y,
            'low_1y': low_1y,
            'tag': tag,
            'top2000_fallback': False,
        }
    except Exception as e:
        print(f"  [警告] 成交量分位数获取失败: {e}")
        return None


def fetch_lpr():
    """LPR (akshare)"""
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.macro_china_lpr()
        if df is not None and not df.empty:
            return df.tail(1)
    except:
        pass
    return None


def fetch_cn_bond_yield_change(pro, date_str, lookback=12):
    """中国国债收益率环比 (日环比BP + 周环比BP)
    优先tushare yc_cb, 失败则fallback到akshare bond_china_yield
    """
    # Method 1: tushare yc_cb
    try:
        start = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=lookback)).strftime('%Y%m%d')
        df = pro.yc_cb(start_date=start, end_date=date_str)
        if df is not None and not df.empty:
            dates = sorted(df['trade_date'].unique())
            if len(dates) < 2:
                raise ValueError("not enough dates")
            curr_date = dates[-1]
            prev_date = dates[-2]
            week_idx = max(0, len(dates) - 6)
            week_prev_date = dates[week_idx] if len(dates) >= 6 else None

            result = {}
            for term in [1, 2, 5, 10, 30]:
                key = f'{term}Y'
                curr_row = df[(df['trade_date'] == curr_date) & (df['curve_term'] == term)]
                prev_row = df[(df['trade_date'] == prev_date) & (df['curve_term'] == term)]
                curr_val = safe_float(curr_row['yield'].values[0]) if not curr_row.empty else None
                prev_val = safe_float(prev_row['yield'].values[0]) if not prev_row.empty else None
                day_chg = calc_bp(curr_val, prev_val)

                week_chg = None
                if week_prev_date and week_prev_date != prev_date:
                    wp_row = df[(df['trade_date'] == week_prev_date) & (df['curve_term'] == term)]
                    week_prev_val = safe_float(wp_row['yield'].values[0]) if not wp_row.empty else None
                    week_chg = calc_bp(curr_val, week_prev_val)

                result[key] = {'curr': curr_val, 'day_chg': day_chg, 'week_chg': week_chg}
            result['_curr_date'] = curr_date
            result['_prev_date'] = prev_date
            result['_week_prev_date'] = week_prev_date
            return result
    except Exception as e:
        print(f"  [INFO] tushare yc_cb失败({e}), 尝试akshare...")

    # Method 2: akshare bond_zh_us_rate (更稳定的备选)
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            df = df.tail(10).copy()
            df['日期'] = pd.to_datetime(df['日期'])
            df = df.sort_values('日期').drop_duplicates(subset='日期', keep='last')
            dates = sorted(df['日期'].unique())
            if len(dates) < 2:
                return None
            
            curr_date = dates[-1]
            prev_date = dates[-2]
            week_idx = max(0, len(dates) - 6)
            week_prev_date = dates[week_idx] if len(dates) >= 6 else None
            
            curr_row = df[df['日期'] == curr_date].iloc[0]
            prev_row = df[df['日期'] == prev_date].iloc[0]
            week_prev_row = df[df['日期'] == week_prev_date].iloc[0] if week_prev_date else None
            
            col_map = {'2Y': '中国国债收益率2年', '5Y': '中国国债收益率5年',
                       '10Y': '中国国债收益率10年', '30Y': '中国国债收益率30年'}
            result = {}
            for key, col in col_map.items():
                if col in curr_row.index and col in prev_row.index:
                    curr_val = safe_float(curr_row[col])
                    prev_val = safe_float(prev_row[col])
                    day_chg = calc_bp(curr_val, prev_val)
                    week_chg = None
                    if week_prev_row is not None and col in week_prev_row.index:
                        week_prev_val = safe_float(week_prev_row[col])
                        week_chg = calc_bp(curr_val, week_prev_val)
                    result[key] = {'curr': curr_val, 'day_chg': day_chg, 'week_chg': week_chg}
            
            result['_curr_date'] = curr_date.strftime('%Y%m%d')
            result['_prev_date'] = prev_date.strftime('%Y%m%d')
            result['_week_prev_date'] = week_prev_date.strftime('%Y%m%d') if week_prev_date else None
            print(f"  [INFO] cn_bond_yield_change用akshare bond_zh_us_rate备选")
            return result
    except Exception as e:
        print(f"  [WARN] akshare国债收益率环比(bond_zh_us_rate)获取失败: {e}")
    return None


def fetch_omo_balance():
    """
    央行OMO余额 (逆回购未到期余额 + 今日净投放)
    数据源1: akshare macro_china_gksccz (中国货币网)
    数据源2: PBOC公告页解析
    返回: {reverse_repo_balance, today_injection, today_maturity, today_net, date, operations}
    """
    result = {
        'reverse_repo_balance': None,
        'today_injection': None,
        'today_maturity': None,
        'today_net': None,
        'date': None,
        'operations': [],
    }

    # ---- 零级：data_hub EDB 的 PBOC 解析器 ----
    # 与 EDB 的 pbc:omo 指标同一份数据（直采央行「公开市场业务交易公告」），
    # 但这里额外拿到逐笔操作明细（期限/金额），供隔夜逆回购判定使用。
    try:
        if EDB_AVAILABLE:
            from data_hub.edb.fetchers import pbc_omo as _pbc
            _recs = _pbc._load_ops(pages=3)
            _rr = [r for r in _recs if r.get('kind') == 'RR']
            if _rr:
                def _last_val(_field):
                    _df = _pbc._daily_frame(_recs, {'RR'}, _field)
                    if _df is None or len(_df) == 0:
                        return None, None
                    _row = _df.iloc[-1]
                    return int(_row['value']), str(_row['date'])[:10]

                _inj, _d1 = _last_val('inject')
                _mat, _d2 = _last_val('maturity')
                _net, _d3 = _last_val('net')
                _bal, _d4 = _last_val('balance')
                result['today_injection'] = _inj
                result['today_maturity'] = _mat
                result['today_net'] = _net
                result['reverse_repo_balance'] = _bal
                result['date'] = _d1 or _d2 or _d3 or _d4

                # 逐笔明细：pbc_omo 里 term=1 表示隔夜，本脚本约定 term=0 表示隔夜
                _all_ops = []
                for _r in _rr:
                    _all_ops.append({
                        'date': _r['date'].strftime('%Y-%m-%d'),
                        'term': 0 if int(_r['term']) <= 1 else int(_r['term']),
                        'amount': int(_r['amount']),
                        'rate': None,
                    })
                result['all_operations'] = _all_ops
                _latest = max(r['date'] for r in _rr)
                for _op in _all_ops:
                    if _op['date'] == _latest.strftime('%Y-%m-%d'):
                        result['operations'].append({
                            'term': _op['term'],
                            'amount': _op['amount'],
                            'rate': None,
                        })
                if result['today_net'] is not None:
                    print(f"  [INFO] OMO 走 data_hub/EDB(pbc_omo)，"
                          f"{len(_rr)}笔操作记录")
                    return result
    except Exception as _e:
        print(f"  [INFO] EDB OMO 取数失败({_e})，回退原生抓取")

    # Method 1: akshare macro_china_gksccz
    try:
        ak = _AKProxy()  # DataHub 中转
        func_name = 'macro_china_gksccz'
        if hasattr(ak, func_name):
            df = getattr(ak, func_name)()
            if df is not None and not df.empty:
                return _calc_omo_from_df(df, result)
    except Exception as e:
        print(f"  [INFO] akshare OMO失败({e}), 尝试PBOC公告...")
    
    # Method 2: PBOC公告列表页抓取 + 解析
    try:
        return _fetch_omo_from_pboc(result)
    except Exception as e:
        print(f"  [WARN] PBOC公告解析失败: {e}")
        import traceback
        traceback.print_exc()
    
    return None


def _calc_omo_from_df(df, result):
    """从akshare DataFrame计算OMO余额"""
    df['操作日期'] = pd.to_datetime(df['操作日期'])
    df['期限'] = pd.to_numeric(df['期限'], errors='coerce')
    df['交易量'] = pd.to_numeric(df['交易量'], errors='coerce')
    
    today = pd.Timestamp.now().normalize()
    cutoff = today - pd.Timedelta(days=30)
    df_recent = df[df['操作日期'] >= cutoff].copy()
    
    df_rr = df_recent[df_recent['正/逆回购'].str.contains('逆回购', na=False)].copy()
    if df_rr.empty:
        return None
    
    df_rr['到期日期'] = df_rr['操作日期'] + pd.to_timedelta(df_rr['期限'], unit='D')
    
    outstanding = df_rr[df_rr['到期日期'] > today]
    result['reverse_repo_balance'] = int(outstanding['交易量'].sum()) if not outstanding.empty else 0
    
    latest_date = df_rr['操作日期'].max()
    today_ops = df_rr[df_rr['操作日期'] == latest_date]
    result['date'] = latest_date.strftime('%Y-%m-%d')
    result['today_injection'] = int(today_ops['交易量'].sum()) if not today_ops.empty else 0
    
    maturing = df_rr[df_rr['到期日期'].dt.date == today.date()]
    result['today_maturity'] = int(maturing['交易量'].sum()) if not maturing.empty else 0
    
    result['today_net'] = result['today_injection'] - result['today_maturity']
    
    for _, row in today_ops.iterrows():
        result['operations'].append({
            'term': int(row['期限']),
            'amount': int(row['交易量']),
            'rate': safe_float(row['中标利率']),
        })
    
    return result


def _fetch_omo_from_pboc(result):
    """从PBOC公告页抓取近期OMO操作数据并计算余额"""
    import requests
    from bs4 import BeautifulSoup
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html',
    }
    
    # 1. 获取公告列表页
    list_url = "http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"
    r = requests.get(list_url, headers=headers, timeout=15)
    r.encoding = 'utf-8'
    if r.status_code != 200:
        print(f"  [WARN] PBOC列表页获取失败: {r.status_code}")
        return None
    
    soup = BeautifulSoup(r.text, 'html.parser')
    
    # 2. 提取近期公告链接 (约15个交易日=3周)
    links = []
    base_url = "http://www.pbc.gov.cn"
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        if '交易公告' in text and '/125475/' in href:
            full_url = base_url + href if href.startswith('/') else href
            links.append(full_url)
    
    if not links:
        print("  [WARN] 未找到PBOC公告链接")
        return None
    
    # 3. 逐个解析公告内容，提取逆回购操作
    operations = []  # [{date, term, amount, rate}]
    today = datetime.now().date()
    
    for link in links[:20]:  # 最多解析20条
        try:
            r = requests.get(link, headers=headers, timeout=10)
            r.encoding = 'utf-8'
            if r.status_code != 200:
                continue
            
            # 先解析HTML提取纯文本，避免HTML标签干扰regex
            soup2 = BeautifulSoup(r.text, 'html.parser')
            content_text = re.sub(r'\s+', ' ', soup2.get_text())
            
            # 提取日期
            date_match = re.search(r'20\d{2}年\d{1,2}月\d{1,2}日', content_text)
            if not date_match:
                continue
            op_date_str = date_match.group()
            op_date = datetime.strptime(op_date_str, '%Y年%m月%d日').date()
            
            # 提取逆回购操作: "开展了XXXX亿元7天期逆回购"
            main_pattern = r'开展了\s*([\d,]+)\s*亿元\s*(\d+)\s*天期?\s*逆回购'
            main_matches = list(re.finditer(main_pattern, content_text))
            
            # 提取隔夜逆回购: "开展了XXXX亿元隔夜逆回购操作"
            overnight_pattern = r'开展了?\s*([\d,]+)\s*亿元\s*隔夜\s*逆回购'
            overnight_matches = list(re.finditer(overnight_pattern, content_text))
            
            # 提取"操作量为零"的期限（如"7天期逆回购操作量为零"）
            zero_pattern = r'(\d+)天期?\s*逆回购\s*操作量为零'
            zero_matches = list(re.finditer(zero_pattern, content_text))
            
            # 提取利率: 表格格式 "7天 1.40% 1590亿元 1590亿元"
            rate_map = {}
            rate_pattern = r'(\d+)\s*天\s+([\d.]+)%\s*[\d,]*亿元'
            for rm in re.finditer(rate_pattern, content_text):
                term = int(rm.group(1))
                rate = float(rm.group(2))
                rate_map[term] = rate
            
            def _add_op(op_date, term, amount, rate=None):
                dup = any(op['date'] == op_date and op['term'] == term and op['amount'] == amount 
                         for op in operations)
                if not dup:
                    operations.append({
                        'date': op_date,
                        'term': term,
                        'amount': amount,
                        'rate': rate,
                    })
            
            for m in main_matches:
                amount_str = m.group(1).replace(',', '')
                term = int(m.group(2))
                amount = int(amount_str)
                _add_op(op_date, term, amount, rate_map.get(term))
            
            # 隔夜逆回购 (term=0 表示隔夜)
            for m in overnight_matches:
                amount_str = m.group(1).replace(',', '')
                amount = int(amount_str)
                _add_op(op_date, 0, amount, None)  # term=0 代表隔夜
            
            # 零操作的期限
            for m in zero_matches:
                term = int(m.group(1))
                _add_op(op_date, term, 0, rate_map.get(term))
        except:
            continue
    
    if not operations:
        print("  [WARN] 未能解析PBOC公告中的逆回购操作")
        return None
    
    # 4. 计算未到期余额
    balance = 0
    today_injection = 0
    today_maturity = 0
    
    def _calc_maturity_date(op_date, term):
        """计算逆回购到期日。
        隔夜逆回购(term=0)：当天投放，下一交易日到期（简化为+1天）。
        其他期限：op_date + term 天。
        """
        if term == 0:
            # 隔夜：+1天（遇到周末粗略顺延，这里不做精确交易日计算）
            return op_date + timedelta(days=1)
        else:
            return op_date + timedelta(days=term)
    
    for op in operations:
        maturity_date = _calc_maturity_date(op['date'], op['term'])
        if maturity_date > today:
            balance += op['amount']
        if maturity_date == today:
            today_maturity += op['amount']
        if op['date'] == today:
            today_injection += op['amount']
    
    result['reverse_repo_balance'] = balance
    result['today_injection'] = today_injection
    result['today_maturity'] = today_maturity
    result['today_net'] = today_injection - today_maturity
    result['date'] = today.strftime('%Y-%m-%d')
    
    # Today's operations detail
    for op in operations:
        if op['date'] == today:
            result['operations'].append({
                'term': op['term'],
                'amount': op['amount'],
                'rate': op['rate'],
            })
    
    # If no today ops found, use latest date
    if not result['operations']:
        latest_date = max(op['date'] for op in operations)
        result['date'] = latest_date.strftime('%Y-%m-%d')
        for op in operations:
            if op['date'] == latest_date:
                result['operations'].append({
                    'term': op['term'],
                    'amount': op['amount'],
                    'rate': op['rate'],
                })
    
    # 返回所有操作明细（用于查找昨日隔夜等跨日操作）
    result['all_operations'] = [
        {
            'date': op['date'].strftime('%Y-%m-%d'),
            'term': op['term'],
            'amount': op['amount'],
            'rate': op['rate'],
        }
        for op in operations
    ]
    
    return result


def fetch_omo_history_from_pboc(max_pages=1):
    """从PBOC公告列表页抓取近期OMO操作历史
    返回: list of dict [{date: 'YYYY-MM-DD', net: 亿元(正=净投放,负=净回笼), injection, maturity}]
    """
    import requests
    from bs4 import BeautifulSoup
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html',
    }
    
    # 获取列表页的公告链接
    list_url = "http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"
    try:
        r = requests.get(list_url, headers=headers, timeout=15)
        r.encoding = 'utf-8'
    except Exception as e:
        print(f"  [WARN] PBOC列表页获取失败: {e}")
        return []
    
    soup = BeautifulSoup(r.text, 'html.parser')
    base_url = "http://www.pbc.gov.cn"
    links = []
    url_pat = re.compile(r'/\d+/index\.html')
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        if '交易公告' in text and url_pat.search(href):
            full_url = base_url + href if href.startswith('/') else href
            links.append(full_url)
    
    if not links:
        return []
    
    # 解析每条公告，提取操作记录
    operations = []  # [{date(date), term(days), amount(亿)}]
    for link in links:
        try:
            r = requests.get(link, headers=headers, timeout=10)
            r.encoding = 'utf-8'
            if r.status_code != 200:
                continue
            soup2 = BeautifulSoup(r.text, 'html.parser')
            content_text = re.sub(r'\s+', ' ', soup2.get_text())
            
            # 提取日期
            date_match = re.search(r'20\d{2}年\d{1,2}月\d{1,2}日', content_text)
            if not date_match:
                continue
            op_date = datetime.strptime(date_match.group(), '%Y年%m月%d日').date()
            
            # 提取逆回购操作
            main_pattern = r'开展了?\s*([\d,]+)\s*亿元\s*(\d+)\s*天期?\s*逆回购'
            for m in re.finditer(main_pattern, content_text):
                amount = int(m.group(1).replace(',', ''))
                term = int(m.group(2))
                operations.append({'date': op_date, 'term': term, 'amount': amount})
            
            # 隔夜逆回购
            overnight_pattern = r'开展了?\s*([\d,]+)\s*亿元\s*隔夜\s*逆回购'
            for m in re.finditer(overnight_pattern, content_text):
                amount = int(m.group(1).replace(',', ''))
                operations.append({'date': op_date, 'term': 0, 'amount': amount})
        except:
            continue
    
    if not operations:
        return []
    
    # 计算每日净投放（投放 - 到期）
    daily_injection = {}
    daily_maturity = {}
    
    def _maturity_date(op_date, term):
        if term == 0:  # 隔夜
            return op_date + timedelta(days=1)
        else:
            return op_date + timedelta(days=term)
    
    for op in operations:
        d = op['date'].strftime('%Y-%m-%d')
        mat_d = _maturity_date(op['date'], op['term']).strftime('%Y-%m-%d')
        daily_injection[d] = daily_injection.get(d, 0) + op['amount']
        daily_maturity[mat_d] = daily_maturity.get(mat_d, 0) + op['amount']
    
    # 合并成日度净投放
    all_dates = set(daily_injection.keys()) | set(daily_maturity.keys())
    history = []
    for d in sorted(all_dates):
        inj = daily_injection.get(d, 0)
        mat = daily_maturity.get(d, 0)
        history.append({
            'date': d,
            'injection': inj,
            'maturity': mat,
            'net': inj - mat,
        })
    
    return history


def get_omo_net_history(cache, current_omo=None):
    """整合OMO净投放历史（cache + PBOC公告 + 当日数据）
    返回: sorted list of (date_str, net_value) for last ~252 trading days
    """
    # 1. 从cache读历史
    cached = cache.get('omo_net_history', [])
    
    # 2. 从PBOC公告抓近期历史（更新最近的真实数据）
    recent = fetch_omo_history_from_pboc()
    recent_dict = {h['date']: h['net'] for h in recent}
    
    # 3. 合并：以PBOC最新公告为准，更新/追加
    merged = {h['date']: h['net'] for h in cached if isinstance(h, dict)}
    
    for d, net in recent_dict.items():
        merged[d] = net  # PBOC公告数据覆盖旧缓存
    
    # 4. 加入当日数据
    if current_omo and current_omo.get('date') and current_omo.get('today_net') is not None:
        merged[current_omo['date']] = current_omo['today_net']
    
    # 转为按日期排序的列表，保留最近252个数据点
    items = sorted(merged.items(), key=lambda x: x[0])
    # 限制到约252个交易日（1年）
    if len(items) > 252:
        items = items[-252:]
    
    return items


def calc_omo_percentile(history_net, value):
    """计算净投放/回笼绝对值的分位数
    history_net: list of (date, net_value)
    value: 当前日净投放值（亿）
    返回: percentile (0-100), 表示当前值在历史绝对值中的位置
    """
    if not history_net or value is None:
        return None
    abs_values = [abs(v) for _, v in history_net if v is not None and v != 0]
    if not abs_values:
        return None
    abs_val = abs(value)
    below = sum(1 for v in abs_values if v < abs_val)
    return round(below / len(abs_values) * 100)


def check_omo_75pct_alert(cache, current_omo):
    """判断当日OMO净投放/回笼是否超过近1年75分位数
    返回: (should_alert, reason_str, percentile)
    """
    if not current_omo or current_omo.get('today_net') is None:
        return False, "OMO数据不可用", None
    
    history = get_omo_net_history(cache, current_omo)
    if len(history) < 20:  # 至少20个数据点才判断
        return False, f"历史数据不足（仅{len(history)}天），暂不告警", None
    
    today_net = current_omo['today_net']
    pct = calc_omo_percentile(history, today_net)
    
    if pct is None:
        return False, "分位数计算失败", None
    
    if pct >= 75:
        direction = "净投放" if today_net > 0 else "净回笼"
        return True, f"{direction}{abs(today_net)}亿，位于近1年{pct}分位（≥75%阈值）", pct
    
    return False, f"净量{today_net:+}亿，位于近1年{pct}分位（<75%阈值）", pct


def check_overnight_repo_info(current_omo):
    """检查隔夜逆回购相关信息：
    1. 昨日隔夜逆回购操作（今日到期）
    2. 今日隔夜逆回购操作
    返回: list of dict [{type, amount, rate, date, term}]
    """
    result = []
    if not current_omo:
        return result
    
    today_str = current_omo.get('date', '')
    all_ops = current_omo.get('all_operations', [])
    
    if not all_ops or not today_str:
        return result
    
    today = datetime.strptime(today_str, '%Y-%m-%d').date()
    yesterday = today - timedelta(days=1)
    yesterday_str = yesterday.strftime('%Y-%m-%d')
    
    for op in all_ops:
        op_date = op.get('date', '')
        term = op.get('term')
        if term != 0:  # 只看隔夜
            continue
        
        amount = op.get('amount', 0)
        rate = op.get('rate')
        
        if op_date == today_str and amount > 0:
            result.append({
                'type': '今日投放',
                'term': '隔夜',
                'amount': amount,
                'rate': rate,
                'date': today_str,
                'maturity_date': (today + timedelta(days=1)).strftime('%Y-%m-%d'),
            })
        elif op_date == yesterday_str and amount > 0:
            result.append({
                'type': '昨日投放(今日到期)',
                'term': '隔夜',
                'amount': amount,
                'rate': rate,
                'date': yesterday_str,
                'maturity_date': today_str,
            })
    
    return result


def send_omo_alert_email(omo_data, alert_reason, percentile, overnight_ops, history_count):
    """发送OMO异常告警邮件
    发件人: songshang951@163.com (163邮箱)
    收件人: 410119229@qq.com
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr
    
    # 163邮箱配置
    smtp_server = 'smtp.163.com'
    smtp_port = 465
    sender = 'songshang951@163.com'
    password = _smtp_password()
    sender_name = '环流日报告警'
    receiver = '410119229@qq.com'
    
    today_net = omo_data.get('today_net', 0)
    today_inj = omo_data.get('today_injection', 0)
    today_mat = omo_data.get('today_maturity', 0)
    op_date = omo_data.get('date', '')
    
    direction = "净投放" if today_net > 0 else "净回笼"
    
    # 构造邮件内容
    subject = f"【OMO告警】央行{direction}{abs(today_net)}亿，达近1年{percentile}分位"
    
    body_lines = [
        f"日期: {op_date}",
        f"",
        f"=== 今日OMO操作 ===",
        f"投放: {today_inj} 亿元",
        f"到期: {today_mat} 亿元",
        f"净{direction}: {abs(today_net)} 亿元",
        f"分位: 近1年 {percentile}% 分位（阈值: 75%）",
        f"",
    ]
    
    # 操作明细
    ops = omo_data.get('operations', [])
    if ops:
        body_lines.append("=== 操作明细 ===")
        for op in ops:
            term = op.get('term')
            term_str = '隔夜' if term == 0 else f'{term}天'
            rate = op.get('rate')
            rate_str = f', 利率{rate}%' if rate else ''
            body_lines.append(f"  {term_str}逆回购: {op['amount']}亿{rate_str}")
        body_lines.append("")
    
    # 隔夜逆回购信息
    if overnight_ops:
        body_lines.append("=== 隔夜逆回购 ===")
        for op in overnight_ops:
            body_lines.append(f"  {op['type']}: {op['amount']}亿 ({op['date']})")
        body_lines.append("")
    
    body_lines.append(f"历史数据: {history_count}天（约{round(history_count/252*12,1)}个月）")
    body_lines.append("")
    body_lines.append("-- 环流日报 OMO自动告警 --")
    
    body = '\n'.join(body_lines)
    
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = formataddr((str(Header(sender_name, 'utf-8')), sender))
        msg['To'] = receiver
        msg['Subject'] = Header(subject, 'utf-8')
        
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            server.starttls()
        
        server.login(sender, password)
        server.sendmail(sender, [receiver], msg.as_string())
        server.quit()
        print(f"  ✅ OMO告警邮件已发送至 {receiver}")
        return True
    except Exception as e:
        print(f"  [WARN] OMO告警邮件发送失败: {e}")
        return False


def update_omo_history_cache(cache, current_omo):
    """更新cache中的OMO历史数据"""
    if not current_omo or not current_omo.get('date') or current_omo.get('today_net') is None:
        return
    
    history = cache.get('omo_net_history', [])
    if not isinstance(history, list):
        history = []
    
    today = current_omo['date']
    today_net = current_omo['today_net']
    
    # 查找是否已有当日记录
    found = False
    for item in history:
        if isinstance(item, dict) and item.get('date') == today:
            item['net'] = today_net
            item['injection'] = current_omo.get('today_injection', 0)
            item['maturity'] = current_omo.get('today_maturity', 0)
            found = True
            break
    
    if not found:
        history.append({
            'date': today,
            'net': today_net,
            'injection': current_omo.get('today_injection', 0),
            'maturity': current_omo.get('today_maturity', 0),
        })
    
    # 按日期排序，保留最多252条
    history = [h for h in history if isinstance(h, dict) and h.get('date')]
    history.sort(key=lambda x: x['date'])
    if len(history) > 252:
        history = history[-252:]
    
    cache['omo_net_history'] = history


def _mlf_finalize(history, result):
    """把 MLF 月度操作历史算成报告用的投放/到期/净投放/余额（锚点滚动）"""
    result['history'] = [{'month': k, **v} for k, v in sorted(history.items(), reverse=True)]

    # ===== 3. 填充最新月份数据 =====
    sorted_yms = sorted(history.keys(), reverse=True)
    latest_ym = sorted_yms[0]
    latest_data = history[latest_ym]

    result['date'] = latest_data['date'] or latest_ym + '-15'
    result['operation_amount'] = latest_data['amount']
    result['term'] = latest_data['term']
    result['rate'] = latest_data['rate']

    # 如果最新月没利率，往前找最近一个有利率的
    if result['rate'] is None:
        for ym in sorted_yms:
            if history[ym].get('rate'):
                result['rate'] = history[ym]['rate']
                break

    # ===== 4. 推算到期量和余额 =====
    # MLF 期限1年，当月到期量 ≈ 12个月前的操作量
    # 余额 = 上月余额 + 本月投放 - 本月到期
    # 我们从最早的已知月份开始滚，用操作表已知余额做锚点（如果有的话）

    all_yms = sorted(history.keys())  # 升序

    # 先算每个月的到期量（向前推12个月的操作量）
    monthly_data = {}
    for ym in all_yms:
        # 计算12个月前的月份
        y, m = ym.split('-')
        y_int, m_int = int(y), int(m)
        m_int -= 12
        while m_int <= 0:
            m_int += 12
            y_int -= 1
        prev12_ym = f"{y_int}-{m_int:02d}"

        maturity = history.get(prev12_ym, {}).get('amount', None)
        operation = history[ym]['amount']
        net = operation - maturity if maturity is not None else None

        monthly_data[ym] = {
            'operation': operation,
            'maturity': maturity,
            'net': net,
        }

    # 用2025年5月PBOC操作表余额作为锚点 (50320亿)
    # 然后往前/往后滚
    anchor_ym = '2025-05'
    anchor_balance = 50320  # 亿，来自PBOC 2025年5月操作表

    if anchor_ym in monthly_data:
        # 从锚点往后滚，计算每个月的余额
        balance = anchor_balance
        # 先确保锚点月余额正确
        balances = {anchor_ym: balance}

        # 往后算
        start_idx = all_yms.index(anchor_ym)
        for i in range(start_idx + 1, len(all_yms)):
            ym = all_yms[i]
            md = monthly_data[ym]
            if md['maturity'] is not None:
                balance = balance + md['operation'] - md['maturity']
                balances[ym] = balance

        # 往前算（锚点之前的月份也补一下）
        balance = anchor_balance
        for i in range(start_idx - 1, -1, -1):
            ym = all_yms[i]
            next_ym = all_yms[i + 1]
            nd = monthly_data[next_ym]
            if nd['maturity'] is not None:
                # 下月余额 = 本月余额 + 下月投放 - 下月到期
                # 本月余额 = 下月余额 - 下月投放 + 下月到期
                balance = balances[next_ym] - nd['operation'] + nd['maturity']
                balances[ym] = balance

        # 填充最新月的到期/净投放/余额
        if latest_ym in monthly_data:
            result['maturity_amount'] = monthly_data[latest_ym]['maturity']
            result['net_injection'] = monthly_data[latest_ym]['net']
        if latest_ym in balances:
            result['balance'] = balances[latest_ym]

    result['source'] = 'PBOC官网'

    # 利率：2026 年起 MLF 招标方式为「固定数量、利率招标、多重价位中标」，
    # 央行公告本身不再公布单一中标利率（实测 2026-10 公告全文无「中标利率」字样），
    # 因此取不到就留空，不硬编码旧政策利率 2.30%（会误导为当前 MLF 成本）
    result['rate_note'] = ('多重价位中标，无统一中标利率' if result['rate'] is None else '')

    return result


def fetch_mlf_data():
    """
    中期借贷便利(MLF)操作数据
    数据源: PBOC官网 MLF招标公告
    - 抓取最近18个月的操作记录
    - 推算每月到期量和当前余额（基于1年期到期滚动）
    返回: {date, operation_amount, maturity_amount, net_injection, balance, rate, term, source, history}
    """
    result = {
        'date': None,               # 最新操作日期 YYYY-MM-DD
        'operation_amount': None,   # 当月投放(亿元)
        'maturity_amount': None,    # 当月到期(亿元)
        'net_injection': None,      # 当月净投放(亿元)
        'balance': None,            # 当前余额(亿元，最新月末推算)
        'rate': None,               # 利率(%)
        'term': '1年',
        'source': '',
        'history': [],              # 历史操作记录 [{month, amount, date}]
    }

    # 零级：data_hub EDB（pbc:mlf 直采央行 MLF 招标公告，逐月操作量与操作日）
    if EDB_AVAILABLE:
        _rows = edb_rows('LIQ.CN.MLF')
        if len(_rows) >= 6:
            _hist = {}
            for _x in _rows:
                _ym = str(_x['date'])[:7]
                _hist[_ym] = {'amount': int(round(_x['value'])), 'date': str(_x['date']),
                              'rate': None, 'term': '1年'}
            _res = _mlf_finalize(_hist, result)
            if _res:
                _res['source'] = 'data_hub/EDB(pbc:mlf)'
                print(f"  [INFO] MLF 走 data_hub/EDB，{len(_hist)} 个月操作记录")
                return _res

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html',
    }
    base_url = "http://www.pbc.gov.cn"

    try:
        # ===== 1. 获取 MLF 招标公告列表 =====
        list_url = base_url + "/zhengcehuobisi/125207/125213/125437/125446/125873/index.html"
        r = requests.get(list_url, headers=headers, timeout=15)
        r.encoding = 'utf-8'
        if r.status_code != 200:
            print(f"  [WARN] MLF列表页获取失败: {r.status_code}")
            return None

        soup = BeautifulSoup(r.text, 'html.parser')

        # 提取所有招标公告链接
        announcements = []  # [(ym, url, title)]
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            href = a.get('href', '')
            if '中期借贷便利招标公告' in text and '/125873/' in href and '.html' in href:
                full_url = base_url + href if href.startswith('/') else href
                m = re.search(r'(\d{4})年(\d{1,2})月', text)
                if m:
                    ym = f"{m.group(1)}-{int(m.group(2)):02d}"
                    announcements.append((ym, full_url, text))

        if not announcements:
            print("  [WARN] 未找到MLF招标公告")
            return None

        # 按时间倒序
        announcements.sort(key=lambda x: x[0], reverse=True)

        # ===== 2. 解析每条公告，获取操作金额和日期 =====
        history = {}  # {ym: {'amount': int, 'date': str, 'rate': float}}
        for ym, link, title in announcements[:18]:  # 取最近18个月
            try:
                r2 = requests.get(link, headers=headers, timeout=10)
                r2.encoding = 'utf-8'
                if r2.status_code != 200:
                    continue
                soup2 = BeautifulSoup(r2.text, 'html.parser')
                ctext = re.sub(r'\s+', ' ', soup2.get_text())

                # 操作金额
                amt_m = re.search(r'开展\s*([\d,]+)\s*亿元\s*MLF', ctext)
                if not amt_m:
                    continue
                amount = int(amt_m.group(1).replace(',', ''))

                # 操作日期
                date_m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', ctext)
                op_date = None
                if date_m:
                    op_date = f"{date_m.group(1)}-{int(date_m.group(2)):02d}-{int(date_m.group(3)):02d}"

                # 利率
                rate_m = re.search(r'中标利率\s*([\d.]+)\s*%', ctext)
                rate = float(rate_m.group(1)) if rate_m else None

                # 期限
                term_m = re.search(r'期限为?\s*(\d+)\s*年期', ctext)
                term = f"{term_m.group(1)}年" if term_m else '1年'

                history[ym] = {
                    'amount': amount,
                    'date': op_date,
                    'rate': rate,
                    'term': term,
                }
            except:
                continue

        if not history:
            print("  [WARN] 未能解析任何MLF操作数据")
            return None

        return _mlf_finalize(history, result)

    except Exception as e:
        print(f"  [WARN] MLF数据获取失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def fetch_repo_rates_tushare(lookback_days=540):
    """从tushare获取银行间回购利率历史数据
    返回DataFrame: date, DR001, DR007, DR014, R001, R007, R014, GC001, GC007, GC014
    优先使用加权利率(weight)，缺失用收盘价(close)
    """
    try:
        pro = _ProProxy()  # DataHub 统一入口
        
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        # 目标品种映射
        target_codes = {
            'DR001.IB': 'DR001',
            'DR007.IB': 'DR007', 
            'DR014.IB': 'DR014',
            'R001.IB': 'R001',
            'R007.IB': 'R007',
            'R014.IB': 'R014',
            'GC001.SH': 'GC001',
            'GC007.SH': 'GC007',
            'GC014.SH': 'GC014',
        }
        
        all_data = []
        for ts_code in target_codes:
            try:
                df = pro.repo_daily(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is not None and not df.empty:
                    col_name = target_codes[ts_code]
                    # 优先用加权利率weight，其次用收盘价close
                    df[col_name] = df['weight_r'].fillna(df['close'])
                    df = df[['trade_date', col_name]].copy()
                    df['date'] = pd.to_datetime(df['trade_date'])
                    df = df.drop(columns=['trade_date'])
                    all_data.append(df)
                time.sleep(0.6)  # tushare限流
            except Exception as e:
                print(f"  [WARN] tushare {ts_code}获取失败: {e}")
        
        if not all_data:
            return None
        
        # 合并所有品种
        result = all_data[0]
        for df in all_data[1:]:
            result = result.merge(df, on='date', how='outer')
        
        result = result.sort_values('date').reset_index(drop=True)
        return result
    except Exception as e:
        print(f"  [WARN] tushare回购利率获取失败: {e}")
        return None


def fetch_repo_rates_cnmoney(lookback_days=540):
    """从中国货币网获取银行间回购定盘利率历史数据
    返回DataFrame: date, FR001, FR007, FR014, FDR001, FDR007, FDR014
    """
    all_data = []
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=lookback_days)
    
    # 按月分批获取
    current = start_date.replace(day=1)
    while current <= end_date:
        month_end = (current + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if month_end > end_date:
            month_end = end_date
        
        url = 'https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/FrrHis'
        params = {
            'startDate': current.strftime('%Y-%m-%d'),
            'endDate': month_end.strftime('%Y-%m-%d'),
            'pageSize': 500,
            'pageNo': 1,
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.chinamoney.com.cn/chinese/bkfrr/',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        
        try:
            r = requests.post(url, data=params, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                records = data.get('records', [])
                for rec in records:
                    fr_map = rec.get('frValueMap', {})
                    if fr_map and fr_map.get('date'):
                        row = {
                            'date': fr_map.get('date'),
                            'FR001': safe_float(fr_map.get('FR001')),
                            'FR007': safe_float(fr_map.get('FR007')),
                            'FR014': safe_float(fr_map.get('FR014')),
                            'FDR001': safe_float(fr_map.get('FDR001')),
                            'FDR007': safe_float(fr_map.get('FDR007')),
                            'FDR014': safe_float(fr_map.get('FDR014')),
                        }
                        all_data.append(row)
        except Exception as e:
            print(f"  [WARN] 中国货币网{current.strftime('%Y%m')}数据获取失败: {e}")
        
        current = (current + timedelta(days=32)).replace(day=1)
        time.sleep(0.3)  # 礼貌限流
    
    if not all_data:
        return None
    
    df = pd.DataFrame(all_data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').drop_duplicates('date').reset_index(drop=True)
    return df


def fetch_pbo_lending_deep():
    """央行流动性操作 + 市场利率分位数 深度追踪
    整合：央行OMO操作 + 银行间回购利率分位数 + 政策利率对比
    数据源：中国货币网（回购利率历史） + PBOC公告（OMO操作）
    """
    result = {
        'omo': None,
        'mlf': None,
        'repo_rates': None,
        'repo_analysis': None,
        'repo_volume': None,
    }
    
    # ===== 1. 央行OMO操作（PBOC公告） =====
    print("  [1/2] 获取央行OMO操作...")
    omo_result = {
        'reverse_repo_balance': None,
        'today_injection': None,
        'today_maturity': None,
        'today_net': None,
        'date': None,
        'operations': [],
    }
    try:
        omo_raw = _fetch_omo_from_pboc(omo_result)
        if omo_raw:
            result['omo'] = omo_raw
    except Exception as e:
        print(f"  [WARN] PBOC OMO获取失败: {e}")
    
    # ===== 2. 银行间回购利率（data_hub EDB 优先 → 中国货币网 → tushare） =====
    print("  [2/2] 获取回购利率历史...")
    repo_df = None
    data_source = 'chinamoney'
    col_map_tushare = {}  # 列名映射 (chinamoney/akshare已经是FDR/FR格式)

    # 零级：data_hub EDB（统一口径；DR/R 为 tushare repo_daily 加权均价真值，
    # GC 为新浪交易所收盘，全部由 EDB 内部做降级与缓存，脚本不再各自爬）
    if EDB_AVAILABLE:
        _edb_cols = {'DR001': 'LIQ.CN.DR001', 'DR007': 'LIQ.CN.DR007', 'DR014': 'LIQ.CN.DR014',
                     'R001': 'LIQ.CN.R001', 'R007': 'LIQ.CN.R007', 'R014': 'LIQ.CN.R014',
                     'GC001': 'LIQ.CN.REPO.GC001', 'GC007': 'LIQ.CN.REPO.GC007',
                     'GC014': 'LIQ.CN.REPO.GC014'}
        _series_map = {}
        for _name, _sid in _edb_cols.items():
            _rows = edb_rows(_sid, limit=540)
            if len(_rows) >= 20:
                _series_map[_name] = pd.Series({pd.Timestamp(x['date']): x['value'] for x in _rows})
        if len(_series_map) >= 4:
            repo_df = pd.DataFrame(_series_map).sort_index()
            repo_df.index.name = 'date'
            repo_df = repo_df.reset_index()
            data_source = 'data_hub/EDB'
            col_map_tushare = {'DR001': 'FDR001', 'DR007': 'FDR007', 'DR014': 'FDR014',
                               'R001': 'FR001', 'R007': 'FR007', 'R014': 'FR014'}
            print(f"  [INFO] 回购利率走 data_hub/EDB，{len(repo_df)}行")

    # 一级：中国货币网（EDB 无数据时兜底，最稳定，无需IP授权）
    if repo_df is None or len(repo_df) < 20:
        repo_df = fetch_repo_rates_cnmoney(lookback_days=540)
        if repo_df is not None and len(repo_df) >= 20:
            data_source = 'chinamoney'
        else:
            print("  [INFO] 中国货币网数据不足，尝试tushare...")
            # 二级：tushare 兜底（该函数自持 token，独立于全局 USE_TUSHARE 开关）
            try:
                _tdf = fetch_repo_rates_tushare(lookback_days=540)
                if _tdf is not None and len(_tdf) >= 20:
                    repo_df = _tdf
                    data_source = 'tushare'
                    col_map_tushare = {
                        'DR001': 'FDR001', 'DR007': 'FDR007', 'DR014': 'FDR014',
                        'R001': 'FR001', 'R007': 'FR007', 'R014': 'FR014',
                    }
                else:
                    print("  [WARN] tushare回购利率兜底数据不足")
            except Exception as _te:
                print(f"  [WARN] tushare回购利率兜底失败: {_te}")
    
    if repo_df is None or repo_df.empty or len(repo_df) < 20:
        print("  [WARN] 回购利率数据不足")
        return result
    
    # 统一列名
    if col_map_tushare:
        rename_map = {k: v for k, v in col_map_tushare.items() if k in repo_df.columns}
        repo_df = repo_df.rename(columns=rename_map)
    
    result['repo_rates'] = repo_df
    result['repo_data_source'] = data_source
    
    # ===== 3. 回购利率分位数分析 =====
    analysis = {}
    latest = repo_df.iloc[-1]
    latest_date = latest['date'].strftime('%Y-%m-%d')
    analysis['date'] = latest_date
    
    for rate_key in ['FDR007', 'FDR001', 'FR007', 'FR001', 'FDR014', 'FR014']:
        series = repo_df[rate_key].dropna().values
        if len(series) < 20:
            continue
        
        # 各品种更新节奏不同（DR 系列 T 日出数，R 系列 T-1），整行取最新会拿到 NaN，
        # 因此每个品种都用自己那一列的最后有效值
        _col_valid = repo_df[rate_key].dropna()
        if len(_col_valid) == 0:
            continue
        current = float(_col_valid.iloc[-1])
        current_pct = round(float(pd.Series(series).rank(pct=True).iloc[-1]) * 100, 1)
        
        # 变动
        def chg_back(n_days):
            target = pd.Timestamp(latest_date) - pd.Timedelta(days=n_days)
            past = repo_df[repo_df['date'] <= target]
            if len(past) > 0:
                _pv = past[rate_key].dropna()
                past_val = safe_float(_pv.iloc[-1]) if len(_pv) > 0 else None
                if past_val:
                    return round((current - past_val) * 100, 1)  # BP
            return None
        
        chg_1d = chg_back(1)
        chg_5d = chg_back(7)
        chg_20d = chg_back(30)
        chg_250d = chg_back(365)
        
        r_high = float(max(series))
        r_low = float(min(series))
        r_median = float(pd.Series(series).median())
        
        # 预警等级
        if current_pct >= 90:
            alert = "🔴 极高"
            alert_desc = "近两年90%分位以上，资金面极度紧张"
        elif current_pct >= 80:
            alert = "🟠 偏高"
            alert_desc = "近两年80%分位以上，资金面偏紧"
        elif current_pct >= 60:
            alert = "🟡 中性偏高"
            alert_desc = "近两年60%-80%分位"
        elif current_pct >= 40:
            alert = "🟢 中性"
            alert_desc = "近两年40%-60%分位"
        elif current_pct >= 20:
            alert = "🟢 中性偏低"
            alert_desc = "近两年20%-40%分位"
        else:
            alert = "🔵 低位"
            alert_desc = "近两年20%分位以下，资金面宽松"
        
        analysis[rate_key] = {
            'current': current,
            'percentile': current_pct,
            'chg_1d_bp': chg_1d,
            'chg_5d_bp': chg_5d,
            'chg_20d_bp': chg_20d,
            'chg_250d_bp': chg_250d,
            'high': r_high,
            'low': r_low,
            'median': r_median,
            'alert': alert,
            'alert_desc': alert_desc,
        }
    
    # 非银融资压力（FR007 - FDR007利差）
    if 'FR007' in analysis and 'FDR007' in analysis:
        # 计算利差历史分位（必须按日期对齐后相减，避免两列缺失日不同导致长度不一致）
        if 'date' in repo_df.columns:
            _sp = repo_df[['date', 'FR007', 'FDR007']].dropna()
        else:
            _sp = repo_df[['FR007', 'FDR007']].dropna()
        # 当前利差取对齐后的最后一行，避免单列当日缺失导致 nan
        if len(_sp) > 0:
            spread = float(_sp.iloc[-1]['FR007']) - float(_sp.iloc[-1]['FDR007'])
        else:
            spread = analysis['FR007']['current'] - analysis['FDR007']['current']
        if len(_sp) > 20:
            spread_series = _sp['FR007'].values - _sp['FDR007'].values
            spread_pct = round(float(pd.Series(spread_series).rank(pct=True).iloc[-1]) * 100, 1)
        else:
            spread_pct = None
        
        analysis['nonbank_spread'] = {
            'current_bp': round(spread * 100, 1),
            'percentile': spread_pct,
            'alert': '🔴 非银融资压力大' if spread_pct and spread_pct >= 80 else '🟢 正常',
        }
    
    # 与政策利率对比（假设OMO 7天为政策利率，约1.4%）
    if 'FDR007' in analysis and result.get('omo') and result['omo'].get('operations'):
        for op in result['omo']['operations']:
            if op.get('term') == 7 and op.get('rate'):
                pol_rate = op['rate']
                curr = analysis['FDR007']['current']
                spread = round((curr - pol_rate) * 100, 1)  # BP
                analysis['policy_spread_bp'] = spread
                analysis['policy_rate'] = pol_rate
                break
    
    result['repo_analysis'] = analysis

    # ===== 4. 回购成交量 =====
    print("  额外：获取回购成交量...")
    try:
        repo_vol = fetch_repo_volume()
        if repo_vol:
            result['repo_volume'] = repo_vol
            print(f"  ✅ 回购成交量获取成功，共{len(repo_vol.get('volumes', {}))}个品种")
    except Exception as e:
        print(f"  [WARN] 回购成交量获取失败: {e}")

    # ===== 5. MLF操作 =====
    print("  额外：获取MLF操作数据...")
    try:
        mlf_data = fetch_mlf_data()
        if mlf_data:
            result['mlf'] = mlf_data
            print(f"  ✅ MLF数据获取成功: {mlf_data.get('operation_amount', 'N/A')}亿 投放")
    except Exception as e:
        print(f"  [WARN] MLF数据获取失败: {e}")

    return result


def fetch_repo_volume():
    """
    银行间质押式回购成交量（亿元）
    一级：中国货币网质押式回购日报 (Playwright 无头浏览器，JS渲染页面，最稳定)
    二级：中国货币网纯 HTML 解析 (BeautifulSoup，兜底)
    三级：AKShare 货币市场数据 (兜底)
    返回: {'date': 'YYYY-MM-DD', 'volumes': {'DR001': float, 'DR007': float, ...},
            'rates': {'DR001': {'open': float, 'close': float, 'weighted': float}, ...}} 或 None
    """
    # ===== 零级：data_hub EDB（统一口径，tushare repo_daily 成交量真值）=====
    if EDB_AVAILABLE:
        _vol_map = {'DR001': 'LIQ.CN.REPO.VOL.DR001', 'DR007': 'LIQ.CN.REPO.VOL.DR007',
                    'DR014': 'LIQ.CN.REPO.VOL.DR014', 'R001': 'LIQ.CN.REPO.VOL.R001',
                    'R007': 'LIQ.CN.REPO.VOL.R007', 'R014': 'LIQ.CN.REPO.VOL.R014'}
        _vols = {}
        _dates = []
        for _name, _sid in _vol_map.items():
            _v, _d = edb_point(_sid)
            if _v is not None:
                _vols[_name] = round(float(_v), 2)
                if _d:
                    _dates.append(_d)
        if len(_vols) >= 3:
            return {
                'date': max(_dates) if _dates else datetime.now().strftime('%Y-%m-%d'),
                'volumes': _vols,
                'rates': {},
                'source': 'data_hub/EDB',
            }

    # ===== 一级：中国货币网 Playwright 无头浏览器 =====
    # 页面是JS渲染的，纯requests拿不到数据，Playwright启动Chromium渲染后提取文本
    try:
        from playwright.sync_api import sync_playwright
        url = "https://www.chinamoney.com.cn/chinese/mtdexdaily/?tab=2"

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            page = browser.new_page()
            page.goto(url, timeout=60000, wait_until='domcontentloaded')
            try:
                page.wait_for_selector('table', timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(3000)  # 额外等待JS数据填充
            text = page.inner_text('body')
            browser.close()

        result = _parse_chinamoney_repo_text(text)
        if result and result.get('volumes') and len(result['volumes']) >= 4:
            print(f"  ✅ 中国货币网(Playwright)回购成交量获取成功: {result['date']}, {len(result['volumes'])}个品种")
            return result
    except ImportError:
        print("  [INFO] playwright未安装，跳过一级数据源")
    except Exception as e:
        print(f"  [WARN] 中国货币网(Playwright)回购成交量获取失败: {e}")

    # ===== 二级：中国货币网纯 HTML 解析 (BeautifulSoup，兜底) =====
    try:
        url = "https://www.chinamoney.com.cn/chinese/mtdexdaily/?tab=2"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            volumes = {}
            data_date = None

            # 尝试提取日期
            date_elem = soup.select_one('.date-title, .title-date, [class*="date"]')
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                date_match = re.search(r'(\d{4}[-年]\d{1,2}[-月]\d{1,2})', date_text)
                if date_match:
                    data_date = date_match.group(1).replace('年', '-').replace('月', '-').replace('日', '')

            # 解析表格数据
            table = soup.select_one('table')
            if table:
                rows = table.select('tr')
                for row in rows:
                    cells = row.select('td, th')
                    if len(cells) >= 3:
                        name = cells[0].get_text(strip=True)
                        target_names = ['DR001', 'DR007', 'DR014', 'R001', 'R007', 'R014']
                        matched = None
                        for tn in target_names:
                            if tn == name or tn in name:
                                matched = tn
                                break
                        if matched:
                            for ci in range(1, len(cells)):
                                val_text = cells[ci].get_text(strip=True)
                                vol_match = re.search(r'([\d,]+\.?\d*)', val_text)
                                if vol_match:
                                    try:
                                        vol = float(vol_match.group(1).replace(',', ''))
                                        volumes[matched] = vol
                                        break
                                    except ValueError:
                                        pass

            if volumes and len(volumes) >= 2:
                print(f"  ✅ 中国货币网(HTML)回购成交量获取成功: {len(volumes)}个品种")
                return {'date': data_date, 'volumes': volumes}
    except Exception as e:
        print(f"  [WARN] 中国货币网(HTML)回购成交量获取失败: {e}")

    # ===== 三级兜底：AKShare 货币市场数据 =====
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.macro_china_money_market()
        if df is not None and not df.empty:
            row = df.iloc[-1]
            vol_map = {}
            col_map = {
                'DR001': ['DR001成交量', 'DR001成交量(亿元)', '隔夜DR成交量'],
                'DR007': ['DR007成交量', 'DR007成交量(亿元)', '7天DR成交量'],
                'R001': ['R001成交量', 'R001成交量(亿元)', '隔夜回购成交量'],
                'R007': ['R007成交量', 'R007成交量(亿元)', '7天回购成交量'],
            }
            for key, candidates in col_map.items():
                for col in candidates:
                    if col in df.columns:
                        val = safe_float(row.get(col))
                        if val is not None:
                            vol_map[key] = val
                            break
            if vol_map:
                print(f"  ✅ AKShare货币市场成交量获取成功: {len(vol_map)}个品种")
                return {'date': str(row.get('日期', '')), 'volumes': vol_map}
    except Exception as e:
        print(f"  [WARN] AKShare货币市场成交量获取失败: {e}")

    return None


def _parse_chinamoney_repo_text(text):
    """
    解析中国货币网质押式回购日报的页面文本（Playwright inner_text 提取）
    页面结构：两张表（R系列 + DR系列），制表符分隔，8列
    返回: {'date': 'YYYY-MM-DD', 'volumes': {...}, 'rates': {...}} 或 None
    """
    if not text:
        return None

    # 1. 提取日期："质押式回购日报  2026-08-19"
    date_match = re.search(r'质押式回购日报\s+(\d{4}-\d{2}-\d{2})', text)
    if not date_match:
        return None
    date_str = date_match.group(1)

    # 2. 定位所有表格的表头位置
    table_header = '品种\t开盘利率(%)\t收盘利率(%)\t加权利率(%)\t加权平均利率(利率债)(%)\t平均回购期限(天)\t成交笔数(笔)\t成交量(亿元)'
    header_positions = [m.start() for m in re.finditer(re.escape(table_header), text)]
    if len(header_positions) < 2:
        return None

    volumes = {}
    rates = {}

    for idx, pos in enumerate(header_positions):
        start = pos + len(table_header)
        if idx + 1 < len(header_positions):
            end = header_positions[idx + 1]
        else:
            note_match = re.search(r'注：', text[start:])
            end = start + note_match.start() if note_match else start + 2000

        table_text = text[start:end].strip()
        lines = [l.strip() for l in table_text.split('\n') if l.strip() and '\t' in l]

        for line in lines:
            cols = line.split('\t')
            if len(cols) < 8:
                continue
            name = cols[0].strip()
            try:
                open_rate = float(cols[1])
                close_rate = float(cols[2])
                weighted_rate = float(cols[3])
                volume = float(cols[7])
            except (ValueError, IndexError):
                continue

            volumes[name] = volume
            rates[name] = {
                'open': open_rate,
                'close': close_rate,
                'weighted': weighted_rate,
            }

    if not volumes:
        return None

    return {'date': date_str, 'volumes': volumes, 'rates': rates}


def fetch_bond_us_sina():
    """美债10Y日线 (akshare bond_gb_us_sina)"""
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_gb_us_sina()
        if df is not None and not df.empty:
            return df.tail(5)
    except:
        pass
    return None


def fetch_bond_zh_sina():
    """中债10Y日线 (akshare bond_gb_zh_sina)"""
    try:
        ak = _AKProxy()  # DataHub 中转
        df = ak.bond_gb_zh_sina()
        if df is not None and not df.empty:
            return df.tail(5)
    except:
        pass
    return None


def fetch_fed_h41():
    """美联储H.4.1报告 - 准备金/ON RRP/Foreign Pool/TGA (web scraping)
    关键修正(2026-06-06): H.4.1的"Reverse repurchase agreements"总计包含:
    1. Foreign official and international accounts (外国央行回购池, ~$319B)
    2. Others (国内ON RRP等, ~$2-6B)
    必须区分! ON RRP才是流动性缓冲, Foreign Pool不是.
    """
    result = {}
    # 零级：data_hub EDB（准备金 / ON RRP / TGA，单位百万美元，与 H.4.1 一致）
    if EDB_AVAILABLE:
        _v_res, _d_res = edb_point('LIQ.US.RESERVES')
        _v_rrp, _d_rrp = edb_point('LIQ.US.RRP')
        _v_tga, _d_tga = edb_point('LIQ.US.TGA')
        if _v_res is not None and _v_tga is not None:
            result['reserve'] = int(round(_v_res))
            result['tga'] = int(round(_v_tga))
            if _v_rrp is not None:
                result['rrp_on'] = int(round(_v_rrp))
                result['rrp'] = result['rrp_on']
            result['source'] = 'data_hub/EDB'
            result['date'] = _d_res or _d_tga
            print(f"  [INFO] H.4.1 关键项走 data_hub/EDB：准备金 ${_v_res/1e6:.3f}T, "
                  f"TGA ${_v_tga/1e3:.0f}B, ON RRP ${(_v_rrp or 0)/1e3:.2f}B")
            return result
    try:
        url = "https://www.federalreserve.gov/releases/h41/current/default.htm"
        r = requests.get(url, timeout=20, headers=HEADERS, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            all_text = soup.get_text()
            lines = all_text.split('\n')
            
            # Reserve balances with Federal Reserve Banks
            for i, line in enumerate(lines):
                if 'Reserve balances with Federal Reserve Banks' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) > 5:
                            result['reserve'] = int(nums[0].replace(',', ''))
                            break
                    break
            
            # Reverse repurchase agreements (总计)
            for i, line in enumerate(lines):
                if 'Reverse repurchase agreements' in line and 'Depository' not in line and 'Less' not in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) > 4:
                            result['rrp_total'] = int(nums[0].replace(',', ''))
                            break
                    break
            
            # Foreign official RRP sub-item
            for i, line in enumerate(lines):
                if 'Foreign official and international accounts' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) > 4:
                            result['rrp_foreign'] = int(nums[0].replace(',', ''))
                            break
                    break
            
            # ON RRP = Others = Total RRP - Foreign RRP
            rrp_total = result.get('rrp_total', 0)
            rrp_foreign = result.get('rrp_foreign', 0)
            if rrp_total and rrp_foreign:
                result['rrp_on'] = rrp_total - rrp_foreign
            elif rrp_total:
                result['rrp_on'] = rrp_total  # fallback
            # 兼容旧字段名 (rrp现在指向ON RRP而非总RRP)
            result['rrp'] = result.get('rrp_on', result.get('rrp_total', 0))
            
            # TGA - U.S. Treasury, general account
            for i, line in enumerate(lines):
                if 'U.S. Treasury, general account' in line:
                    for j in range(i+1, min(i+10, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) > 4:
                            result['tga'] = int(nums[0].replace(',', ''))
                            break
                    break
    except Exception as e:
        print(f"  [WARN] Fed H.4.1获取失败: {e}")
    
    return result if result else None



def fetch_tga_dts():
    """TGA 日度余额：data_hub EDB 优先兜底，DTS 明细补充
    - EDB(LIQ.US.TGA) 提供日度收盘余额，单位百万美元
    - DTS API 提供当日存取明细（opening/deposits/withdrawals/top 分项），EDB 不覆盖
    """
    _edb_tga = None
    if EDB_AVAILABLE:
        _v, _d = edb_point('LIQ.US.TGA')
        if _v is not None:
            _edb_tga = {
                'date': _d, 'closing': int(round(_v)),
                'opening': None, 'deposits': None, 'withdrawals': None, 'net_change': None,
                'top_deposits': [], 'top_withdrawals': [],
                'debt_issues': None, 'debt_redemptions': None, 'debt_net': None,
                'source': 'data_hub/EDB',
            }
    _dts = _fetch_tga_dts_raw()
    if _dts and _dts.get('closing') is not None:
        if _edb_tga:
            _dts['edb_closing'] = _edb_tga['closing']
            _dts['edb_date'] = _edb_tga['date']
            if _dts['closing'] != _edb_tga['closing']:
                print(f"  [WARN] TGA 口径差异：DTS {_dts['closing']}M vs EDB {_edb_tga['closing']}M")
        return _dts
    if _edb_tga:
        print(f"  [INFO] TGA 走 data_hub/EDB：{_edb_tga['date']} 收盘 ${_edb_tga['closing']/1e3:.0f}B")
        return _edb_tga
    return _dts


def _fetch_tga_dts_raw():
    """美国财政部 DTS (Daily Treasury Statement) - TGA 日度数据 + 存取明细
    数据源: fiscaldata.treasury.gov API (免key, JSON)
    """
    result = {}
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts"
    
    try:
        # ---- Table I: Operating Cash Balance ----
        url1 = f"{base}/operating_cash_balance?sort=-record_date&page%5Bnumber%5D=1&page%5Bsize%5D=20"
        r1 = requests.get(url1, timeout=20, headers=HEADERS)
        if r1.status_code != 200:
            print(f"  [WARN] DTS Table I HTTP {r1.status_code}")
            return None
        
        data1 = r1.json().get('data', [])
        if not data1:
            print("  [WARN] DTS Table I 无数据")
            return None
        
        latest_date = data1[0]['record_date']
        rows_today = [d for d in data1 if d['record_date'] == latest_date]
        
        opening = deposits = withdrawals = closing = None
        for row in rows_today:
            atype = row['account_type']
            val = int(row['open_today_bal']) if row['open_today_bal'] != 'null' else None
            if 'Opening Balance' in atype:
                opening = val
            elif 'Total TGA Deposits' in atype:
                deposits = val
            elif 'Total TGA Withdrawals' in atype:
                withdrawals = val
            elif 'Closing Balance' in atype:
                ct = row.get('close_today_bal', 'null')
                if ct != 'null':
                    closing = int(ct)
                else:
                    closing = val
        
        if closing is None:
            print("  [WARN] DTS 无TGA收盘余额")
            return None
        
        result['date'] = latest_date
        result['opening'] = opening
        result['deposits'] = deposits
        result['withdrawals'] = withdrawals
        result['closing'] = closing
        result['net_change'] = (deposits or 0) - (withdrawals or 0) if (deposits and withdrawals) else None
        
        # ---- Table II: Deposits & Withdrawals 明细 ----
        url2 = (f"{base}/deposits_withdrawals_operating_cash"
                f"?filter=record_date:eq:{latest_date}"
                f"&sort=src_line_nbr&page%5Bsize%5D=200")
        r2 = requests.get(url2, timeout=20, headers=HEADERS)
        if r2.status_code == 200:
            data2 = r2.json().get('data', [])
            
            deposits_list = []
            withdrawals_list = []
            debt_issues = 0
            debt_redemptions = 0
            
            for row in data2:
                if row['account_type'] != 'Treasury General Account (TGA)':
                    continue
                catg = row['transaction_catg']
                if catg == 'null' or not catg:
                    continue
                amt = int(row['transaction_today_amt']) if row['transaction_today_amt'] != 'null' else 0
                if amt == 0:
                    continue
                
                if row['transaction_type'] == 'Deposits':
                    deposits_list.append((catg, amt))
                    if 'Public Debt Cash Issues' in catg:
                        debt_issues += amt
                elif row['transaction_type'] == 'Withdrawals':
                    withdrawals_list.append((catg, amt))
                    if 'Public Debt Cash Redemp' in catg:
                        debt_redemptions += amt
            
            deposits_list.sort(key=lambda x: x[1], reverse=True)
            withdrawals_list.sort(key=lambda x: x[1], reverse=True)
            
            result['top_deposits'] = deposits_list[:8]
            result['top_withdrawals'] = withdrawals_list[:8]
            result['debt_issues'] = debt_issues
            result['debt_redemptions'] = debt_redemptions
            result['debt_net'] = debt_issues - debt_redemptions
        else:
            print(f"  [WARN] DTS Table II HTTP {r2.status_code}")
            result['top_deposits'] = []
            result['top_withdrawals'] = []
            result['debt_issues'] = 0
            result['debt_redemptions'] = 0
            result['debt_net'] = 0
    
    except Exception as e:
        print(f"  [WARN] DTS TGA获取失败: {e}")
        return None
    
    return result if result.get('closing') else None


def fetch_on_rrp_fred():
    """ON RRP facility余额 - 优先FRED RRPONTSYD，备选H.4.1 Others
    FRED RRPONTSYD是真正的ON RRP facility (~$0.5-2B),
    H.4.1 Others=Total-Foreign约$6B，包含了其他国内RRP操作.
    真实ON RRP远小于H.4.1 Others.
    """
    result = {}
    # ---- 一级：data_hub EDB（LIQ.US.RRP，百万美元 → 十亿美元）----
    if EDB_AVAILABLE:
        _v, _d = edb_point('LIQ.US.RRP')
        if _v is not None:
            return {'rrp_on_fred': round(float(_v) / 1000.0, 3),
                    'source': 'data_hub/EDB', 'date': _d}
    # Method 1: FRED RRPONTSYD页面
    try:
        url = "https://fred.stlouisfed.org/series/RRPONTSYD"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            # 尝试多种模式
            for pattern in [
                r'latestObservation.*?value["\s:]+(\d+\.?\d*)',
                r'observation_value["\s:]+(\d+\.?\d*)',
                r'"value"\s*:\s*"(\d+\.?\d*)"',
            ]:
                m = re.search(pattern, r.text)
                if m:
                    val = float(m.group(1))
                    result['rrp_on_fred'] = val
                    result['source'] = 'FRED'
                    return result
    except:
        pass
    # Method 2: NY Fed API
    try:
        url = "https://markets.newyorkfed.org/api/rp/reverse/results/search.json"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            ops = data.get('rpReverseResults', [])
            if ops:
                latest = ops[-1]
                amt = float(latest.get('totalAmountAccepted', 0))
                if amt > 0:
                    result['rrp_on_fred'] = amt / 1e9  # convert to B
                    result['source'] = 'NYFed'
                    return result
    except:
        pass
    return None


def _fetch_fred_csv(series_id, lookback_days=30):
    """从FRED CSV端点获取时序数据（无需API Key）"""
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}&coed={end}"
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200 and r.text.strip():
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            df = df.dropna(subset=[series_id])
            df[series_id] = pd.to_numeric(df[series_id], errors='coerce')
            df = df.dropna(subset=[series_id])
            if not df.empty:
                return df
    except Exception as e:
        print(f"  [WARN] FRED CSV {series_id}获取失败: {e}")
    return None


def fetch_us2y_deep(lookback_days=730):
    """美债2Y收益率深度追踪 - FRED CSV (DGS2)
    返回当前值、日/周/月/年变动、近两年分位数、预警等级
    """
    # ---- 一级：data_hub EDB（RATE.US.2Y 全历史）----
    df = None
    if EDB_AVAILABLE:
        _cut = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        _rows = [x for x in edb_rows('RATE.US.2Y') if x['date'] >= _cut]
        if len(_rows) >= 30:
            df = pd.DataFrame({'observation_date': [x['date'] for x in _rows],
                               'DGS2': [x['value'] for x in _rows]})
    if df is None:
        df = _fetch_fred_csv('DGS2', lookback_days=lookback_days)
    if df is None or df.empty or len(df) < 30:
        return None
    
    series = df['DGS2'].values
    current = float(series[-1])
    current_date = str(df.iloc[-1]['observation_date'])
    
    # 计算各周期变动 (BP)
    def get_bp_back(series_data, n):
        """回溯n个交易日的变动，单位BP"""
        if len(series_data) > n:
            return round((float(series_data[-1]) - float(series_data[-n-1])) * 100)
        return None
    
    bp_1d = get_bp_back(series, 1)   # 日环比
    bp_5d = get_bp_back(series, 5)   # 周环比 (~5交易日)
    bp_20d = get_bp_back(series, 20) # 月环比 (~20交易日)
    bp_250d = get_bp_back(series, 250) # 年同比 (~250交易日)
    
    # 近两年分位数
    percentile_2y = round(float(pd.Series(series).rank(pct=True).iloc[-1]) * 100, 1)
    
    # 预警等级 (分位数越高越警戒)
    if percentile_2y >= 90:
        alert_level = "🔴 极高"
        alert_desc = "近两年90%分位以上，加息周期极端高位"
    elif percentile_2y >= 80:
        alert_level = "🟠 偏高"
        alert_desc = "近两年80%分位以上，高利率环境持续"
    elif percentile_2y >= 60:
        alert_level = "🟡 中性偏高"
        alert_desc = "近两年60%-80%分位"
    elif percentile_2y >= 40:
        alert_level = "🟢 中性"
        alert_desc = "近两年40%-60%分位"
    elif percentile_2y >= 20:
        alert_level = "🟢 中性偏低"
        alert_desc = "近两年20%-40%分位"
    else:
        alert_level = "🔵 低位"
        alert_desc = "近两年20%分位以下"
    
    # 关键位置
    high_2y = float(max(series))
    low_2y = float(min(series))
    median_2y = float(pd.Series(series).median())
    
    return {
        'current': current,
        'date': current_date,
        'bp_1d': bp_1d,
        'bp_5d': bp_5d,
        'bp_20d': bp_20d,
        'bp_250d': bp_250d,
        'percentile_2y': percentile_2y,
        'alert_level': alert_level,
        'alert_desc': alert_desc,
        'high_2y': high_2y,
        'low_2y': low_2y,
        'median_2y': median_2y,
        'data_points': len(series),
    }


def fetch_us10y_deep(lookback_days=730):
    """美债10Y收益率深度追踪 - FRED CSV (DGS10)
    返回当前值、日/周/月/年变动、近两年分位数、预警等级
    """
    # ---- 一级：data_hub EDB（RATE.US.10Y 全历史）----
    df = None
    if EDB_AVAILABLE:
        _cut = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        _rows = [x for x in edb_rows('RATE.US.10Y') if x['date'] >= _cut]
        if len(_rows) >= 30:
            df = pd.DataFrame({'observation_date': [x['date'] for x in _rows],
                               'DGS10': [x['value'] for x in _rows]})
    if df is None:
        df = _fetch_fred_csv('DGS10', lookback_days=lookback_days)
    if df is None or df.empty or len(df) < 30:
        return None
    
    series = df['DGS10'].values
    current = float(series[-1])
    current_date = str(df.iloc[-1]['observation_date'])
    
    def get_bp_back(series_data, n):
        if len(series_data) > n:
            return round((float(series_data[-1]) - float(series_data[-n-1])) * 100)
        return None
    
    bp_1d = get_bp_back(series, 1)
    bp_5d = get_bp_back(series, 5)
    bp_20d = get_bp_back(series, 20)
    bp_250d = get_bp_back(series, 250)
    
    # 近两年分位数
    percentile_2y = round(float(pd.Series(series).rank(pct=True).iloc[-1]) * 100, 1)
    
    if percentile_2y >= 90:
        alert_level = "🔴 极高"
        alert_desc = "近两年90%分位以上，长端利率极端高位"
    elif percentile_2y >= 80:
        alert_level = "🟠 偏高"
        alert_desc = "近两年80%分位以上，高利率环境持续"
    elif percentile_2y >= 60:
        alert_level = "🟡 中性偏高"
        alert_desc = "近两年60%-80%分位"
    elif percentile_2y >= 40:
        alert_level = "🟢 中性"
        alert_desc = "近两年40%-60%分位"
    elif percentile_2y >= 20:
        alert_level = "🟢 中性偏低"
        alert_desc = "近两年20%-40%分位"
    else:
        alert_level = "🔵 低位"
        alert_desc = "近两年20%分位以下"
    
    high_2y = float(max(series))
    low_2y = float(min(series))
    median_2y = float(pd.Series(series).median())
    
    return {
        'current': current,
        'date': current_date,
        'bp_1d': bp_1d,
        'bp_5d': bp_5d,
        'bp_20d': bp_20d,
        'bp_250d': bp_250d,
        'percentile_2y': percentile_2y,
        'alert_level': alert_level,
        'alert_desc': alert_desc,
        'high_2y': high_2y,
        'low_2y': low_2y,
        'median_2y': median_2y,
        'data_points': len(series),
    }


def fetch_sofr():
    """SOFR利率 (data_hub EDB → FRED CSV 兜底)"""
    # ---- 一级：data_hub EDB ----
    if EDB_AVAILABLE:
        v, d = edb_point('LIQ.US.SOFR')
        if v is not None:
            return {'rate': float(v), 'date': d, 'volume': None}
    df = _fetch_fred_csv('SOFR', lookback_days=10)
    if df is not None and not df.empty:
        last = df.iloc[-1]
        return {
            'rate': float(last['SOFR']),
            'date': str(last['observation_date']),
            'volume': None,
        }
    # fallback: 旧NY Fed API
    try:
        url = "https://markets.newyorkfed.org/api/rates/secured/search.json?startDate=&endDate=&type=SOFR"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            if 'refRates' in data and len(data['refRates']) > 0:
                latest = data['refRates'][-1]
                return {
                    'rate': float(latest.get('percentRate', 0)),
                    'date': latest.get('effectiveDate', ''),
                    'volume': int(latest.get('volume', 0)) if latest.get('volume') else None,
                }
    except Exception as e:
        print(f"  [WARN] NY Fed SOFR获取失败(fallback): {e}")
    return None


def fetch_iorb():
    """IORB利率 (data_hub EDB → FRED CSV → HTML scraping 兜底)"""
    # ---- 一级：data_hub EDB ----
    if EDB_AVAILABLE:
        v, _d = edb_point('US.IORB')
        if v is not None:
            return {'rate': float(v)}
    df = _fetch_fred_csv('IORB', lookback_days=10)
    if df is not None and not df.empty:
        last = df.iloc[-1]
        return {'rate': float(last['IORB'])}
    # fallback: 旧HTML scraping
    try:
        url = "https://fred.stlouisfed.org/series/IORB"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            match = re.search(r'value"\s*:\s*([\d.]+)', r.text)
            if match:
                return {'rate': float(match.group(1))}
    except:
        pass
    return None


def fetch_fed_balance_sheet():
    """美联储资产负债表详情 (Total Assets/Securities/Treasuries/MBS)"""
    result = {}
    # 零级：data_hub EDB（总资产 / 持有国债合计 / 短债 / 长债，单位百万美元）
    if EDB_AVAILABLE:
        _v_bs, _d_bs = edb_point('LIQ.US.FED.BS')
        _v_ust, _ = edb_point('US.FED.UST.TOTAL')
        _v_long, _ = edb_point('US.FED.UST.LONG')
        _v_short, _ = edb_point('US.FED.UST.SHORT')
        if _v_bs is not None:
            result['total_assets'] = int(round(_v_bs))
            result['date'] = _d_bs
            if _v_ust is not None: result['treasuries'] = int(round(_v_ust))
            if _v_long is not None: result['treasury_long_bonds'] = int(round(_v_long))
            if _v_short is not None: result['treasury_bills'] = int(round(_v_short))
            result['source'] = 'data_hub/EDB'
            print(f"  [INFO] 美联储资产负债表走 data_hub/EDB：总资产 ${_v_bs/1e6:.3f}T, "
                  f"持有国债 ${(_v_ust or 0)/1e3:.0f}B（长 ${(_v_long or 0)/1e3:.0f}B / 短 ${(_v_short or 0)/1e3:.0f}B）")
            return result
    try:
        url = "https://www.federalreserve.gov/releases/h41/current/default.htm"
        r = requests.get(url, timeout=20, headers=HEADERS, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            all_text = soup.get_text()
            lines = all_text.split('\n')

            for i, line in enumerate(lines):
                if 'Total assets' in line and 'Total factors' not in line:
                    for j in range(i+1, min(i+20, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) >= 7:
                            result['total_assets'] = int(nums[0].replace(',', ''))
                            break
                    if 'total_assets' in result:
                        break

            for i, line in enumerate(lines):
                if 'Securities held outright' in line:
                    for j in range(i+1, min(i+20, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) >= 7:
                            result['securities_outright'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'U.S. Treasury securities' in line and 'held outright' not in line:
                    for j in range(i+1, min(i+15, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) >= 7:
                            result['treasuries'] = int(nums[0].replace(',', ''))
                            break
                    break

            for i, line in enumerate(lines):
                if 'Mortgage-backed securities' in line:
                    for j in range(i+1, min(i+20, len(lines))):
                        nums = re.findall(r'([\d,]+)', lines[j].strip())
                        if nums and len(nums[0].replace(',','')) >= 7:
                            result['mbs'] = int(nums[0].replace(',', ''))
                            break
                    break
            
            # ---- 国债期限结构 (短债/长债) ----
            # 在 "U.S. Treasury securities" 下面找 Bills / Notes and bonds nominal / TIPS
            _treasury_idx = None
            for i, line in enumerate(lines):
                if 'U.S. Treasury securities' in line and 'held outright' not in line:
                    _treasury_idx = i
                    break
            
            def _find_first_money(lines_list, start_idx, max_rows=20):
                """从start_idx开始往下找，返回第一个大于10000的数字(M单位)和该行所有数字列表"""
                for k in range(start_idx, min(start_idx + max_rows, len(lines_list))):
                    nums_all = re.findall(r'([+\-]?[\d,]+)', lines_list[k].strip())
                    for n_str in nums_all:
                        val_str = n_str.replace(',', '').replace('+', '')
                        try:
                            val = int(val_str)
                            if abs(val) > 10000:  # 金额都是几万M以上，过滤掉脚注数字
                                return val, nums_all
                        except:
                            continue
                return None, []
            
            if _treasury_idx is not None:
                _search_end = min(_treasury_idx + 120, len(lines))
                
                # 遍历国债区块，找各个分项
                for i in range(_treasury_idx, _search_end):
                    line_text = lines[i].strip()
                    
                    # Bills (短期国库券，1年以内) - 匹配 "Bills" 开头(可能带脚注数字)
                    if re.match(r'^Bills\d*$', line_text):
                        val, all_nums = _find_first_money(lines, i + 1, 20)
                        if val is not None:
                            result['treasury_bills'] = val
                            # 第2个大数字是周变动
                            big_nums = []
                            for n_str in all_nums:
                                try:
                                    v = int(n_str.replace(',', '').replace('+', ''))
                                    if abs(v) > 100:
                                        big_nums.append(v)
                                except:
                                    pass
                            # 或者往下找第2行的第一个大数字
                            if len(big_nums) < 2:
                                val2, _ = _find_first_money(lines, i + 2, 3)
                                if val2 is not None:
                                    result['treasury_bills_wk_chg'] = val2 - val if val else None
                    
                    # Notes and bonds, nominal (名义中长期，>1年)
                    if 'Notes and bonds, nominal' in line_text:
                        val, all_nums = _find_first_money(lines, i + 1)
                        if val is not None:
                            result['treasury_notes_bonds_nominal'] = val
                    
                    # Notes and bonds, inflation-indexed (TIPS)
                    if 'Notes and bonds, inflation-indexed' in line_text:
                        val, all_nums = _find_first_money(lines, i + 1)
                        if val is not None:
                            result['treasury_tips'] = val
                
                # 长债合计 (名义 + TIPS)
                nb_nom = result.get('treasury_notes_bonds_nominal')
                tips = result.get('treasury_tips')
                if nb_nom is not None and tips is not None:
                    result['treasury_long_bonds'] = nb_nom + tips
                
                # 短长比例
                bills = result.get('treasury_bills')
                long_bonds = result.get('treasury_long_bonds')
                if bills is not None and long_bonds is not None and long_bonds > 0:
                    result['treasury_short_long_ratio'] = round(bills / long_bonds * 100, 2)  # 百分比
            
            # ---- 周环比变动 ----
            # H.4.1表格数据列: 当前值 | 周变动 | 年初变动 | ...
            if _treasury_idx is not None and result.get('treasury_bills') is not None:
                _search_end = min(_treasury_idx + 120, len(lines))
                
                def _get_data_row_vals(line_idx, max_rows=25):
                    """获取指定分项行后面的所有数据值，按出现顺序返回
                    返回: [当前值, 周变动值, ...]  其中周变动值带符号
                    """
                    big_vals = []
                    for k in range(line_idx + 1, min(line_idx + max_rows, len(lines))):
                        nums = re.findall(r'([+\-]?[\d,]+)', lines[k].strip())
                        for n_str in nums:
                            try:
                                sign = 1
                                if n_str.startswith('-'):
                                    sign = -1
                                v = int(n_str.replace(',', '').replace('+', '').replace('-', ''))
                                if abs(v) > 100:  # 过滤脚注小数字
                                    big_vals.append(v * sign)
                            except:
                                pass
                    return big_vals
                
                # 找 Bills 周变动
                for i in range(_treasury_idx, _search_end):
                    if re.match(r'^Bills\d*$', lines[i].strip()):
                        vals = _get_data_row_vals(i)
                        if len(vals) >= 2:
                            result['treasury_bills_wk_chg'] = vals[1]  # 第2个=周变动
                        break
                
                # 找 Notes and bonds nominal 周变动
                for i in range(_treasury_idx, _search_end):
                    if 'Notes and bonds, nominal' in lines[i].strip():
                        vals = _get_data_row_vals(i)
                        if len(vals) >= 2:
                            result['treasury_nb_nominal_wk_chg'] = vals[1]
                        break
                
                # 找 TIPS 周变动
                for i in range(_treasury_idx, _search_end):
                    if 'inflation-indexed' in lines[i].strip():
                        vals = _get_data_row_vals(i)
                        if len(vals) >= 2:
                            result['treasury_tips_wk_chg'] = vals[1]
                        break
                
                # 计算长债周变动 + 短长比例周环比
                bills_wk = result.get('treasury_bills_wk_chg')
                nb_wk = result.get('treasury_nb_nominal_wk_chg')
                tips_wk = result.get('treasury_tips_wk_chg')
                if bills_wk is not None and nb_wk is not None and tips_wk is not None:
                    long_wk = nb_wk + tips_wk
                    result['treasury_long_bonds_wk_chg'] = long_wk
                    
                    bills_cur = result.get('treasury_bills')
                    long_cur = result.get('treasury_long_bonds')
                    if bills_cur is not None and long_cur is not None and long_cur > 0:
                        prev_bills = bills_cur - bills_wk
                        prev_long = long_cur - long_wk
                        if prev_long > 0:
                            cur_ratio = bills_cur / long_cur * 100
                            prev_ratio = prev_bills / prev_long * 100
                            result['treasury_slr_wk_chg'] = round(cur_ratio - prev_ratio, 2)  # 百分点
    except Exception as e:
        print(f"  [WARN] 美联储资产负债表获取失败: {e}")
    return result if result else None


def fetch_dxy():
    """美元指数DXY (data_hub EDB → tradingeconomics 兜底)"""
    # ---- 一级：data_hub EDB ----
    if EDB_AVAILABLE:
        v, _d = edb_point('GLOBAL.DXY')
        if v is not None:
            return float(v)
    try:
        url = "https://tradingeconomics.com/united-states/currency"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    text = row.get_text()
                    if 'DXY' in text:
                        nums = re.findall(r'([\d.]+)', text)
                        for n in nums:
                            if 90 < float(n) < 120:  # DXY合理范围
                                return float(n)
    except:
        pass
    return None


def fetch_jgb():
    """日本国债收益率全期限。

    2026-11-13 改造：主源改 data_hub EDB（``RATE.JP.*``，日本财务省官方 CSV，
    全历史 + 当月合并）。原 tradingeconomics 爬虫与官方有偏差（2.87% vs 2.841%）
    且易被反爬，降为兜底。

    Returns:
        dict | None: ``{'2Y': 1.857, '10Y': 2.841, ...}``，取不到返回 None。
    """
    terms = {'2Y': 'RATE.JP.2Y', '3Y': 'RATE.JP.3Y', '5Y': 'RATE.JP.5Y',
             '7Y': 'RATE.JP.7Y', '10Y': 'RATE.JP.10Y', '20Y': 'RATE.JP.20Y',
             '30Y': 'RATE.JP.30Y', '40Y': 'RATE.JP.40Y'}
    result = {}
    for term, sid in terms.items():
        val, _dt = edb_point(sid)
        if val is not None:
            result[term] = round(float(val), 3)
    if result:
        return result

    # ---- 兜底：tradingeconomics 爬虫 ----
    try:
        url = "https://tradingeconomics.com/japan/government-bond-yield"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            all_text = BeautifulSoup(r.text, 'html.parser').get_text()
            for term in terms:
                m = re.findall(rf'Japan\s+{term}[^0-9]*?([\d.]+)', all_text)
                if m:
                    result[term] = float(m[0])
    except Exception:
        pass
    return result if result else None


def get_jgb10y_history(cache, current_jgb10y=None):
    """JGB 10Y历史序列（从缓存累积），返回list[float]（按时间顺序，最新在最后）
    缓存 key: jgb10y_history = [{'date': 'YYYYMMDD', 'value': 3.10}, ...]
    """
    # 零级：data_hub EDB 全历史（日本财务省官方 CSV，实测 1800+ 交易日）
    # 原实现靠 cache 逐日累积，新部署或清缓存后周/月环比会缺数据（曾只有 26 天样本）
    if EDB_AVAILABLE:
        _rows = edb_rows('RATE.JP.10Y')
        if len(_rows) >= 60:
            _vals = [x['value'] for x in _rows if x.get('value') is not None]
            if current_jgb10y is not None and _vals:
                _vals[-1] = float(current_jgb10y)   # 用最新官方值覆盖同日
            return _vals

    history = cache.get('jgb10y_history', [])
    if current_jgb10y is not None:
        today = datetime.now().strftime('%Y%m%d')
        # 去重：同一天只保留最新值
        filtered = [h for h in history if h.get('date') != today]
        filtered.append({'date': today, 'value': current_jgb10y})
        # 最多保留400天（约一年半+）
        if len(filtered) > 400:
            filtered = filtered[-400:]
        cache['jgb10y_history'] = filtered
        history = filtered
    return [h['value'] for h in history if h.get('value') is not None]


def calc_bp_change(series, n):
    """从序列末尾回溯n个位置的变动(BP)。不足则返回None"""
    if series and len(series) > n:
        return round((float(series[-1]) - float(series[-n-1])) * 100)
    return None


def fetch_usdcny_spot_sina():
    """获取USD/CNY即期汇率 (data_hub EDB → 新浪财经兜底)
    Returns: float or None
    """
    if EDB_AVAILABLE:
        _v, _d = edb_point('CN.FX.SPOT.USDCNY')
        if _v is not None:
            return float(_v)
    try:
        url = 'https://hq.sinajs.cn/list=fx_susdcny'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}
        r = requests.get(url, timeout=10, headers=headers)
        r.encoding = 'gbk'
        # 格式: var hq_str_fx_susdcny="13:26:05,6.7098,..."
        m = re.search(r'"([^"]+)"', r.text)
        if m:
            parts = m.group(1).split(',')
            if len(parts) >= 3:
                return float(parts[1])  # 最新价
    except Exception as e:
        print(f"  [WARN] 新浪即期汇率获取失败: {e}")
    return None

def fetch_usdcny_spot():
    """获取USD/CNY即期汇率 (data_hub EDB 优先 → CFETS → 新浪 → 默认值)
    """
    if EDB_AVAILABLE:
        _v, _d = edb_point('CN.FX.SPOT.USDCNY')
        if _v is not None:
            return float(_v)
    try:
        ak = _AKProxy()  # DataHub 中转
        # 重试3次
        for attempt in range(3):
            try:
                df = ak.fx_spot_quote()
                if df is not None and not df.empty and '货币对' in df.columns:
                    usd = df[df['货币对'].astype(str).str.contains('USD/CNY', case=False, na=False)]
                    if not usd.empty:
                        row = usd.iloc[0]
                        bid = safe_float(row.get('买报价'))
                        ask = safe_float(row.get('卖报价'))
                        if bid and ask:
                            return round((bid + ask) / 2, 4)
                        if bid:
                            return bid
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2)
    except Exception:
        pass
    
    # 兜底: 新浪
    spot = fetch_usdcny_spot_sina()
    if spot:
        return spot
    
    return None

def calc_percentile(series_list, value):
    """计算value在序列中的分位数(%) - 当前值在历史中的位置百分比"""
    if not series_list or value is None:
        return None
    below = sum(1 for v in series_list if v < value)
    return round(below / len(series_list) * 100)

def calc_annualized_swap_rate(pips, spot):
    """计算年化掉期率 (%)
    公式: 掉期点(Pips) × 0.0001 ÷ 即期汇率 × 100%
    说明: 掉期点报价本身已是年化点数，无需额外乘(360÷天数)因子
    负值=贴水(人民币利率<美元利率), 正值=升水
    """
    if spot is None or spot == 0 or pips is None:
        return None
    return round(pips * 0.0001 / spot * 100, 2)

def fetch_swap_cfets():
    """1Y USD/CNY掉期点 (CFETS中国货币网, 多级降级)
    主源: akshare fx_swap_quote (人民币外汇远掉报价, 实时掉期点)
    备用: akshare fx_c_swap_cm (C-Swap定盘曲线)
    Returns: dict with pips, 年化掉期率(%), 即期汇率, 各期限pips
    """
    result = {}

    # ===== 零级: data_hub EDB（CN.FX.SWAP.* 全期限掉期点，pips）=====
    if EDB_AVAILABLE:
        _tenors = {'1w_pips': 'CN.FX.SWAP.1W', '1m_pips': 'CN.FX.SWAP.1M',
                   '3m_pips': 'CN.FX.SWAP.3M', '6m_pips': 'CN.FX.SWAP.6M',
                   '9m_pips': 'CN.FX.SWAP.9M', '1y_pips': 'CN.FX.SWAP.1Y'}
        for _k, _sid in _tenors.items():
            _v, _d = edb_point(_sid)
            if _v is not None:
                result[_k] = float(_v)
        if result.get('1y_pips') is not None:
            _spot = fetch_usdcny_spot()
            if _spot:
                result['spot'] = _spot
                result['1y_annualized_pct'] = calc_annualized_swap_rate(
                    result['1y_pips'], _spot)
            result['source'] = 'data_hub/EDB(CFETS掉期)'
            return result

    ak = _AKProxy()  # DataHub 中转
    
    # ===== 主源: fx_swap_quote (CFETS人民币外汇远掉报价) =====
    try:
        # 带重试: chinamoney接口偶发返回空JSON
        df = None
        for attempt in range(3):
            try:
                df = ak.fx_swap_quote()
                if df is not None and not df.empty:
                    break
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2)
        
        if df is not None and not df.empty:
            # 找USD/CNY
            usd_row = df[df['货币对'].astype(str).str.contains('USD/CNY', case=False, na=False)]
            if not usd_row.empty:
                row = usd_row.iloc[0]
                
                # 各期限掉期点 (格式: "bid/ask", 取中间价)
                tenor_col_map = {
                    '1周': '1w_pips',
                    '1月': '1m_pips',
                    '3月': '3m_pips',
                    '6月': '6m_pips',
                    '9月': '9m_pips',
                    '1年': '1y_pips',
                }
                for col, key in tenor_col_map.items():
                    if col in df.columns:
                        val = str(row[col]).strip()
                        if '/' in val:
                            try:
                                bid, ask = val.split('/')
                                result[key] = (float(bid) + float(ask)) / 2
                            except ValueError:
                                pass
                
                # 即期汇率: 用同一个接口的fx_spot_quote，或新浪，或默认值
                spot = fetch_usdcny_spot()
                if spot:
                    result['spot'] = spot
                
                pips_1y = result.get('1y_pips')
                spot_rate = result.get('spot')
                if pips_1y is not None and spot_rate:
                    result['1y_annualized_pct'] = calc_annualized_swap_rate(pips_1y, spot_rate)
                
                result['source'] = 'CFETS远掉报价'
                return result
    except Exception as e:
        print(f"  [WARN] fx_swap_quote获取失败: {e}")
    
    # ===== 备用: C-Swap定盘曲线 =====
    try:
        df = ak.fx_c_swap_cm()
        if df is not None and not df.empty:
            tenor_map = {
                'ON': 'on_pips',
                '1W': '1w_pips', 
                '1M': '1m_pips',
                '3M': '3m_pips',
                '6M': '6m_pips',
                '1Y': '1y_pips',
            }
            for _, row in df.iterrows():
                tenor = str(row.get('期限品种', '')).strip()
                pips = safe_float(row.get('掉期点(Pips)'))
                full_price = safe_float(row.get('全价汇率'))
                if tenor in tenor_map and pips is not None:
                    result[tenor_map[tenor]] = pips
                if tenor == 'ON' and full_price is not None:
                    result['spot'] = full_price
            
            if result.get('spot') is None:
                spot = fetch_usdcny_spot()
                if spot:
                    result['spot'] = spot
            
            pips_1y = result.get('1y_pips')
            spot_rate = result.get('spot')
            if pips_1y is not None and spot_rate:
                result['1y_annualized_pct'] = calc_annualized_swap_rate(pips_1y, spot_rate)
            
            result['source'] = '中国货币网C-Swap'
            return result
    except Exception as e:
        print(f"  [WARN] C-Swap获取失败: {e}")
    
    return None


def fetch_hibor():
    """HIBOR 多期限。

    2026-11-13 改造：主源改 data_hub EDB（``LIQ.HK.HIBOR.*``，akshare 口径，T+1 出数）。
    原 HKMA 官方 API 实测最新只到 2026-06-09（滞后 5 个月），拿陈旧值算「日变动」
    会得出 2.84% 这种假信号（真实 1.65%），差 119bp —— 因此 HKMA 只作末级兜底。

    Returns:
        dict | None: current=3M、1m/6m/12m/overnight、date、previous、source。
    """
    result = {}

    # ---- Source 1: data_hub EDB（统一口径）----
    terms = {'current': 'LIQ.HK.HIBOR.3M', '1m': 'LIQ.HK.HIBOR.1M',
             '6m': 'LIQ.HK.HIBOR.6M', '12m': 'LIQ.HK.HIBOR.1Y',
             'overnight': 'LIQ.HK.HIBOR.ON'}
    got = False
    dates = []
    for field, sid in terms.items():
        val, dt = edb_point(sid)
        if val is not None:
            result[field] = round(float(val), 4)
            got = True
            if dt:
                dates.append(dt)
    if got:
        result['date'] = max(dates) if dates else ''
        prev = edb_prev('LIQ.HK.HIBOR.3M')
        if prev is not None:
            result['previous'] = round(float(prev), 4)
        result['source'] = 'data_hub/EDB(akshare)'

    # ---- Source 2: HKMA 官方 API（兜底，注意可能严重滞后）----
    if not result.get('current'):
        try:
            url = ("https://api.hkma.gov.hk/public/market-data-and-statistics/"
                   "monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily"
                   "?segment=hibor.fixing&offset=0")
            r = requests.get(url, timeout=20, headers=HEADERS)
            if r.status_code == 200:
                records = r.json().get('result', {}).get('records', [])
                if records:
                    latest = records[-1]
                    result['current'] = latest.get('ir_3m')
                    result['1m'] = latest.get('ir_1m')
                    result['6m'] = latest.get('ir_6m')
                    result['12m'] = latest.get('ir_12m')
                    result['overnight'] = latest.get('ir_overnight')
                    result['date'] = latest.get('end_of_day', '')
                    if len(records) >= 2:
                        result['previous'] = records[-2].get('ir_3m')
                    result['source'] = 'HKMA官方API(兜底,可能滞后)'
        except Exception:
            pass

    if result.get('current'):
        return result
    return None


# ============ 报告生成 ============

def generate_report():
    ust_monitor_data = None
    now = datetime.now()
    date_str = now.strftime('%Y%m%d')
    date_display = now.strftime('%Y-%m-%d')
    weekday_cn = ['周一','周二','周三','周四','周五','周六','周日'][now.weekday()]
    
    print(f"=== 环流日报生成 {date_display} {weekday_cn} ===\n")
    
    pro = get_tushare()
    lines = []
    add = lines.append
    
    # 缓存
    cache = load_cache()
    
    # ---- 标题 ----
    add(f"# 环流日报 {date_display} {weekday_cn}")
    add(f"\n**数据时效**: {date_display} | 纯脚本生成，零LLM token")
    add("\n---")
    
    # ========== Part 1: 美元环流 (Q/g/P 三维度) ==========
    add("\n## 一、美元环流 (Q/g/P 三维度)")
    
    # Q: 水池总水量
    print("[1/10] 美联储H.4.1...")
    fed = fetch_fed_h41()
    print("[1.5/10] FRED ON RRP...")
    on_rrp_fred = fetch_on_rrp_fred()
    on_rrp_source = "H.4.1 Others"
    if on_rrp_fred and on_rrp_fred.get('rrp_on_fred') is not None:
        # 优先用FRED RRPONTSYD覆盖H.4.1的Others值
        fed['rrp_on'] = int(on_rrp_fred['rrp_on_fred'] * 1e3)  # B→M
        fed['rrp'] = fed['rrp_on']
        on_rrp_source = on_rrp_fred.get('source', 'FRED')
        print(f"  ON RRP from {on_rrp_source}: ${on_rrp_fred['rrp_on_fred']:.2f}B (覆盖H.4.1 Others)")
    else:
        print(f"  FRED/NYFed获取失败, 使用H.4.1 Others=${fed.get('rrp_on', 0)/1e3:.1f}B (含额外国内RRP)")
    print("[2/10] 美联储资产负债表...")
    bs = fetch_fed_balance_sheet()
    
    print("[2.5/10] 财政部DTS (TGA日度)...")
    tga_dts = fetch_tga_dts()
    if tga_dts:
        print(f"  TGA {tga_dts['date']}: 收盘 ${tga_dts['closing']/1e3:.0f}B, 净变动 {tga_dts['net_change']/1e3:+.0f}B")
    else:
        print("  [WARN] TGA DTS获取失败")
    
    add("\n### 💧 Q: 水池总水量")
    if fed:
        reserve = fed.get('reserve')
        rrp_on = fed.get('rrp_on')
        rrp_foreign = fed.get('rrp_foreign')
        rrp_total = fed.get('rrp_total')
        tga = fed.get('tga')
        
        # 储备充裕度
        add(f"\n**储备充裕度 & QE→QT阶段**")
        add(f"\n| 指标 | 数值 | 折算 | 周变化 | 状态 |")
        add(f"|:---|:---:|:---:|:---:|:---:|")
        
        # 读取上周缓存数据
        prev_week = cache.get('prev_week_fed', {})
        
        if reserve:
            reserve_tn = reserve / 1e6
            reserve_bn = reserve / 1e3
            if reserve_bn > 3500: reserve_tag = "🟢 充裕"
            elif reserve_bn > 3000: reserve_tag = "🟡 适中"
            elif reserve_bn > 2500: reserve_tag = "🟠 偏紧"
            else: reserve_tag = "🔴 紧张"
            pw_reserve = prev_week.get('reserve')
            wk_chg = f"{(reserve - pw_reserve) / pw_reserve * 100:+.1f}%" if pw_reserve else "-"
            add(f"| 准备金余额 | {reserve:,}M | ${reserve_tn:.2f}T | {wk_chg} | {reserve_tag} |")
        if rrp_on is not None:
            rrp_on_bn = rrp_on / 1e3
            if rrp_on_bn > 500: rrp_tag = "🟢 充足"
            elif rrp_on_bn > 100: rrp_tag = "🟡 下降"
            elif rrp_on_bn > 20: rrp_tag = "🟠 低位"
            elif rrp_on_bn > 5: rrp_tag = "🔴 枯竭"
            elif rrp_on_bn > 0.5: rrp_tag = "🔴💀 逼近归零"
            else: rrp_tag = "🔴💀 归零"
            pw_rrp_on = prev_week.get('rrp_on')
            wk_chg_rrp = f"${(rrp_on_bn - pw_rrp_on/1e3):+.1f}B" if pw_rrp_on else "-"
            add(f"| ON RRP (流动性缓冲) | {rrp_on:,}M | ${rrp_on_bn:.1f}B | {wk_chg_rrp} | {rrp_tag} |")
            add(f"> ON RRP数据来源: {on_rrp_source} | H.4.1 Others=${fed.get('rrp_total',0)/1e3 - fed.get('rrp_foreign',0)/1e3:.2f}B(含额外国内RRP) | RRP总计=${fed.get('rrp_total',0)/1e3:.2f}B")
        if rrp_foreign is not None:
            pw_rrp_f = prev_week.get('rrp_foreign')
            wk_chg_f = f"${(rrp_foreign/1e3 - pw_rrp_f/1e3):+.0f}B" if pw_rrp_f else "-"
            add(f"| Foreign Pool (外国央行) | {rrp_foreign:,}M | ${rrp_foreign/1e3:.0f}B | {wk_chg_f} | 非缓冲 |")
        if rrp_total is not None:
            pw_rrp_t = prev_week.get('rrp_total')
            wk_chg_t = f"${(rrp_total/1e3 - pw_rrp_t/1e3):+.0f}B" if pw_rrp_t else "-"
            add(f"| RRP总计(H.4.1) | {rrp_total:,}M | ${rrp_total/1e3:.0f}B | {wk_chg_t} | - |")
        if tga:
            pw_tga = prev_week.get('tga')
            wk_chg_tga = f"{(tga - pw_tga) / pw_tga * 100:+.1f}%" if pw_tga else "-"
            add(f"| TGA | {tga:,}M | ${tga/1e6:.2f}T | {wk_chg_tga} | - |")
        
        # RMP阶段
        add(f"\n**RMP阶段判断**")
        if reserve:
            if reserve_bn > 3500:
                rmp = "🟢 充足储备 — QT可继续"
            elif reserve_bn > 3000:
                rmp = "🟡 接近底线 — QT放缓/暂停"
            elif reserve_bn > 2500:
                rmp = "🟠 低于充足 — RMP启动, SRF可能使用"
            else:
                rmp = "🔴 储备不足 — 需停止QT或扩表"
            add(f"- {rmp}")
            add(f"- 准备金 ${reserve_tn:.2f}T vs 充足线 ~$3.0T")
            if rrp_on is not None:
                rrp_note = "(仍有缓冲)" if rrp_on_bn > 20 else "(接近耗尽⚠️)" if rrp_on_bn > 5 else "(逼近归零💀!)" if rrp_on_bn > 0.5 else "(已归零💀!)"
                add(f"- ON RRP ${rrp_on_bn:.1f}B {rrp_note}")
            if rrp_foreign is not None:
                add(f"- Foreign Pool ${rrp_foreign/1e3:.0f}B (非流动性缓冲, 外国央行存款)")
        
        if reserve and reserve < ALERT_RESERVE:
            add(f"\n🔴 准备金 < 3万亿！流动性紧张信号")
        if rrp_on is not None and rrp_on < ALERT_RRP_ON_RED:
            add(f"\n🔴💀 ON RRP仅 ${rrp_on_bn:.2f}B，逼近归零！QT直接冲击准备金，流动性极度脆弱")
        elif rrp_on is not None and rrp_on < ALERT_RRP_ON:
            add(f"\n🔴 ON RRP接近枯竭！QT将直接消耗准备金")
    else:
        add("\n⚠️ 美联储H.4.1数据获取失败")
    
    # ---- TGA 日度变动 (财政部DTS) ----
    if tga_dts:
        add("\n### 🏛️ TGA 日度变动 (财政部DTS)")
        tga_date = tga_dts.get('date', '')
        closing = tga_dts.get('closing')
        net_chg = tga_dts.get('net_change')
        deposits = tga_dts.get('deposits')
        withdrawals = tga_dts.get('withdrawals')
        debt_issues = tga_dts.get('debt_issues', 0)
        debt_redemptions = tga_dts.get('debt_redemptions', 0)
        debt_net = tga_dts.get('debt_net', 0)
        
        add(f"\n**数据日期**: {tga_date}")
        add(f"\n| 项目 | 金额 (M) | 金额 (B) | 说明 |")
        add(f"|:---|---:|---:|:---|")
        if closing is not None:
            add(f"| TGA 收盘余额 | {closing:,} | ${closing/1e3:.0f}B | 财政部在美联储的账户余额 |")
        if net_chg is not None:
            if net_chg > 0:
                net_tag = "🟢 净增加"
            elif net_chg < 0:
                net_tag = "🔴 净减少"
            else:
                net_tag = "⚪ 持平"
            add(f"| 当日净变动 | {net_chg:+,} | ${net_chg/1e3:+.0f}B | {net_tag} |")
        if deposits is not None:
            add(f"| 总存入 | {deposits:,} | ${deposits/1e3:.0f}B | 当日所有进账 |")
        if withdrawals is not None:
            add(f"| 总取出 | {withdrawals:,} | ${withdrawals/1e3:.0f}B | 当日所有支出 |")
        
        # 发债 vs 赎回
        if debt_issues or debt_redemptions:
            add(f"\n**💰 发债融资 vs 债务到期**")
            add(f"\n| 项目 | 金额 (M) | 金额 (B) |")
            add(f"|:---|---:|---:|")
            add(f"| 新发债融资 | {debt_issues:,} | ${debt_issues/1e3:.0f}B |")
            add(f"| 债务到期赎回 | {debt_redemptions:,} | ${debt_redemptions/1e3:.0f}B |")
            if debt_net > 0:
                add(f"| **净发债** | **{debt_net:+,}** | **${debt_net/1e3:+.0f}B** 🟢 |")
            elif debt_net < 0:
                add(f"| **净赎回** | **{debt_net:,}** | **${debt_net/1e3:+.0f}B** 🔴 |")
            else:
                add(f"| 净变动 | 0 | $0B |")
        
        # 存入来源明细 (Top 5, 排除发债)
        top_dep = tga_dts.get('top_deposits', [])
        dep_non_debt = [(c,a) for c,a in top_dep if 'Public Debt' not in c][:5]
        if dep_non_debt:
            add(f"\n**📥 主要存入来源 (不含发债)**")
            add(f"\n| 来源 | 当日金额 |")
            add(f"|:---|---:|")
            for catg, amt in dep_non_debt:
                short = catg.replace('Dept of ', '').replace('Department of ', '')
                add(f"| {short} | ${amt/1e3:.1f}B |")
        
        # 取出去向明细 (Top 5, 排除债务赎回)
        top_wd = tga_dts.get('top_withdrawals', [])
        wd_non_debt = [(c,a) for c,a in top_wd if 'Public Debt' not in c][:5]
        if wd_non_debt:
            add(f"\n**📤 主要支出去向 (不含债务赎回)**")
            add(f"\n| 去向 | 当日金额 |")
            add(f"|:---|---:|")
            for catg, amt in wd_non_debt:
                short = catg.replace('Dept of ', '').replace('Department of ', '')
                add(f"| {short} | ${amt/1e3:.1f}B |")
    else:
        add("\n### 🏛️ TGA 日度变动 (财政部DTS)")
        add("\n⚠️ 财政部DTS数据获取失败")
    
        # 资产负债表 (QT进程)
    if bs:
        total_assets = bs.get('total_assets')
        securities = bs.get('securities_outright')
        treasuries_bs = bs.get('treasuries')
        mbs = bs.get('mbs')
        add(f"\n**QT进程**")
        add(f"\n| 项目 | 数值 | 折算 |")
        add(f"|:---|:---:|:---:|")
        if total_assets:
            add(f"| 总资产 | {total_assets:,}M | ${total_assets/1e6:.2f}T |")
        if securities:
            add(f"| 持有证券 | {securities:,}M | ${securities/1e6:.2f}T |")
        if treasuries_bs:
            add(f"| 美国国债 | {treasuries_bs:,}M | ${treasuries_bs/1e6:.2f}T |")
        if mbs:
            add(f"| MBS | {mbs:,}M | ${mbs/1e6:.2f}T |")
        if total_assets:
            peak = 8.96
            total_assets_t = total_assets / 1e6
            qt_pct = (1 - total_assets_t / peak) * 100
            add(f"| QT缩减 | 峰值${peak:.2f}T → 当前 | -{peak-total_assets_t:.2f}T ({qt_pct:.1f}%) |")
        
        # ---- 国债期限结构 (短债/长债比例) ----
        bills = bs.get('treasury_bills')
        long_bonds = bs.get('treasury_long_bonds')
        sl_ratio = bs.get('treasury_short_long_ratio')
        slr_wk_chg = bs.get('treasury_slr_wk_chg')
        bills_wk = bs.get('treasury_bills_wk_chg')
        long_wk = bs.get('treasury_long_bonds_wk_chg')
        
        if bills is not None and long_bonds is not None:
            add(f"\n**国债期限结构 (美联储持有)**")
            add(f"\n| 项目 | 余额 | 周变动 |")
            add(f"|:---|:---:|:---:|")
            
            def _fmt_wk(v):
                if v is None:
                    return "-"
                sign = "+" if v > 0 else ""
                return f"{sign}{v:,}M"
            
            add(f"| Bills短债 (≤1年) | ${bills/1e3:.0f}B | {_fmt_wk(bills_wk)} |")
            add(f"| Notes+Bonds+TIPS长债 (>1年) | ${long_bonds/1e3:.0f}B | {_fmt_wk(long_wk)} |")
            
            if sl_ratio is not None:
                chg_str = ""
                if slr_wk_chg is not None:
                    sign = "+" if slr_wk_chg > 0 else ""
                    chg_str = f" (周{sign}{slr_wk_chg:.2f}pp)"
                add(f"| **短/长债比例** | **{sl_ratio:.2f}%**{chg_str} | - |")
            add(f"\n> 💡 H.4.1 周度更新；短长比例↑= 美联储持有更多短债（流动性储备/久期管理），↓= 持有更多长债（压低长端收益率/QT久期结构变化）")
    
    # g: 含氧量
    print("[3/10] SOFR/IORB...")
    sofr_data = fetch_sofr()
    iorb_data = fetch_iorb()
    
    # 缓存回退
    if not sofr_data and cache.get('sofr'):
        sofr_data = cache['sofr']
        print("  ⚡ SOFR(缓存)")
    if not iorb_data and cache.get('iorb'):
        iorb_data = cache['iorb']
        print("  ⚡ IORB(缓存)")
    
    # 互相估算
    if (not sofr_data or sofr_data.get('rate') is None) and iorb_data and iorb_data.get('rate'):
        sofr_data = {'rate': iorb_data['rate'] - 0.07, 'date': '', 'volume': None}
        print("  ⚡ SOFR估算")
    if (not iorb_data or iorb_data.get('rate') is None) and sofr_data and sofr_data.get('rate'):
        iorb_data = {'rate': sofr_data['rate'] + 0.07}
        print("  ⚡ IORB估算")
    
    add("\n### 🫧 g: 含氧量结构分布")
    if sofr_data or iorb_data:
        sofr_rate = sofr_data.get('rate') if sofr_data else None
        iorb_rate = iorb_data.get('rate') if iorb_data else None
        spread_g_bp = round((sofr_rate - iorb_rate) * 100, 1) if (sofr_rate is not None and iorb_rate is not None) else None
        
        add(f"\n| 指标 | 数值 | 状态 | 说明 |")
        add(f"|:---|:---:|:---:|:---|")
        if sofr_rate is not None:
            add(f"| SOFR | {sofr_rate:.2f}% | - | {sofr_data.get('date', '') if sofr_data else ''} |")
        if iorb_rate is not None:
            add(f"| IORB | {iorb_rate:.2f}% | - | Fed政策利率下限 |")
        if spread_g_bp is not None:
            if spread_g_bp < -5: g_tag = "🟢 宽松"
            elif spread_g_bp < 5: g_tag = "🟡 正常"
            elif spread_g_bp < 20: g_tag = "🟠 偏紧"
            else: g_tag = "🔴 紧张"
            add(f"| SOFR-IORB | {spread_g_bp:+.1f}bp | {g_tag} | {'融资充裕' if spread_g_bp < -5 else '融资平稳' if spread_g_bp < 5 else '融资成本上升' if spread_g_bp < 20 else '融资压力显著'} |")
        
        add(f"\n> SOFR-IORB<0=融资充裕, ≈0=正常, >0=融资紧张")
        
        # 缓存
        if sofr_data and sofr_data.get('rate') is not None:
            cache['sofr'] = sofr_data
        if iorb_data and iorb_data.get('rate') is not None:
            cache['iorb'] = iorb_data
    else:
        add("\n⚠️ SOFR/IORB数据获取失败")
        spread_g_bp = None
    
    # P: 水温
    print("[4/10] 美债收益率...")
    us_yield = fetch_us_treasury(pro, date_str)
    us_5d = fetch_us_treasury_5d(pro)
    us_sina = fetch_bond_us_sina()
    
    add("\n### 🌡️ P: 水温 (利率曲线)")
    if us_yield:
        us10y = us_yield.get('y10')
        us2y = us_yield.get('y2')
        us5y = us_yield.get('y5')
        us30y = us_yield.get('y30')
        
        bp_2y = None
        bp_5y = None
        bp_10y = None
        bp_30y = None
        if us_5d is not None and len(us_5d) >= 2:
            prev = us_5d.iloc[-2]
            bp_2y = calc_bp(us2y, safe_float(prev.get('y2')))
            bp_5y = calc_bp(us5y, safe_float(prev.get('y5')))
            bp_10y = calc_bp(us10y, safe_float(prev.get('y10')))
            bp_30y = calc_bp(us30y, safe_float(prev.get('y30')))
        
        add(f"\n| 期限 | 收益率(%) | 日变动(BP) |")
        add(f"|:---|:---:|:---:|")
        add(f"| 2Y | {fmt(us2y)} | {fmt_bp(bp_2y)} |")
        add(f"| 5Y | {fmt(us5y)} | {fmt_bp(bp_5y)} |")
        add(f"| **10Y** | **{fmt(us10y)}** | **{fmt_bp(bp_10y)}** |")
        add(f"| 30Y | {fmt(us30y)} | {fmt_bp(bp_30y)} |")
        
        if us10y and us2y:
            spread = round(us10y - us2y, 2)
            curve_tag = "🟢 正常" if spread > 0.01 else "🟡 平坦" if spread > -0.005 else "🔴 倒挂"
            add(f"\n💡 2Y-10Y利差: {spread*100:+.0f}bp {curve_tag}")
    else:
        add("\n⚠️ 美债收益率获取失败")
    
    # 美债2Y深度追踪
    print("[4.5/10] 美债2Y深度追踪...")
    us2y_deep = fetch_us2y_deep()
    add("\n### 📊 美债2Y深度追踪")
    if us2y_deep:
        add(f"\n**当前值**: {us2y_deep['current']:.2f}% ({us2y_deep['date']})")
        add(f"**近两年分位**: {us2y_deep['percentile_2y']}% → {us2y_deep['alert_level']}")
        
        add(f"\n| 周期 | 变动(BP) |")
        add(f"|:---|:---:|")
        add(f"| 日环比 | {fmt_bp(us2y_deep.get('bp_1d'))} |")
        add(f"| 周环比 | {fmt_bp(us2y_deep.get('bp_5d'))} |")
        add(f"| 月环比 | {fmt_bp(us2y_deep.get('bp_20d'))} |")
        add(f"| 年同比 | {fmt_bp(us2y_deep.get('bp_250d'))} |")
        
        add(f"\n**近两年区间**: {us2y_deep['low_2y']:.2f}% ~ {us2y_deep['high_2y']:.2f}% | 中位数 {us2y_deep['median_2y']:.2f}%")
        
        # 进度条可视化 (当前值在近两年区间的位置)
        range_val = us2y_deep['high_2y'] - us2y_deep['low_2y']
        if range_val > 0:
            pos_pct = int((us2y_deep['current'] - us2y_deep['low_2y']) / range_val * 20)
            pos_pct = max(0, min(20, pos_pct))
            bar = '█' * pos_pct + '░' * (20 - pos_pct)
            add(f"\n`{bar}` {us2y_deep['current']:.2f}%")
        
        # 80%分位以上预警标星
        if us2y_deep['percentile_2y'] >= 80:
            add(f"\n⭐ **预警**: 美债2Y收益率处于近两年{us2y_deep['percentile_2y']}%分位，{us2y_deep['alert_desc']}")
            add(f"> 高利率环境持续，对成长股估值压制、美元流动性收紧压力需关注")
        else:
            add(f"\n{us2y_deep['alert_desc']}")
        
        add(f"\n*数据来源: FRED DGS2，共{us2y_deep['data_points']}个交易日样本*")
    else:
        add("\n⚠️ 美债2Y深度数据获取失败")
    
    # 美债10Y深度追踪
    print("[4.6/10] 美债10Y深度追踪...")
    us10y_deep = fetch_us10y_deep()
    add("\n### 📊 美债10Y深度追踪")
    if us10y_deep:
        add(f"\n**当前值**: {us10y_deep['current']:.2f}% ({us10y_deep['date']})")
        add(f"**近两年分位**: {us10y_deep['percentile_2y']}% → {us10y_deep['alert_level']}")
        
        add(f"\n| 周期 | 变动(BP) |")
        add(f"|:---|:---:|")
        add(f"| 日环比 | {fmt_bp(us10y_deep.get('bp_1d'))} |")
        add(f"| 周环比 | {fmt_bp(us10y_deep.get('bp_5d'))} |")
        add(f"| 月环比 | {fmt_bp(us10y_deep.get('bp_20d'))} |")
        add(f"| 年同比 | {fmt_bp(us10y_deep.get('bp_250d'))} |")
        
        add(f"\n**近两年区间**: {us10y_deep['low_2y']:.2f}% ~ {us10y_deep['high_2y']:.2f}% | 中位数 {us10y_deep['median_2y']:.2f}%")
        
        # 进度条可视化
        range_val = us10y_deep['high_2y'] - us10y_deep['low_2y']
        if range_val > 0:
            pos_pct = int((us10y_deep['current'] - us10y_deep['low_2y']) / range_val * 20)
            pos_pct = max(0, min(20, pos_pct))
            bar = '█' * pos_pct + '░' * (20 - pos_pct)
            add(f"\n`{bar}` {us10y_deep['current']:.2f}%")
        
        if us10y_deep['percentile_2y'] >= 80:
            add(f"\n⭐ **预警**: 美债10Y收益率处于近两年{us10y_deep['percentile_2y']}%分位，{us10y_deep['alert_desc']}")
            add(f"> 长端利率持续高位，对全球风险资产估值压制加大，港股成长股敏感度更高")
        else:
            add(f"\n{us10y_deep['alert_desc']}")
        
        add(f"\n*数据来源: FRED DGS10，共{us10y_deep['data_points']}个交易日样本*")
    else:
        add("\n⚠️ 美债10Y深度数据获取失败")
    
    # DXY
    print("[5/10] DXY...")
    dxy = fetch_dxy()
    add("\n### 💵 美元指数 (DXY)")
    if dxy:
        prev_dxy = cache.get('dxy')
        chg = round((dxy - prev_dxy) / prev_dxy * 100, 2) if prev_dxy else None
        add(f"\n**DXY**: {dxy:.2f} ({fmt(chg, '%', '-')})")
        cache['dxy'] = dxy
    else:
        add("\n⚠️ DXY获取失败")
    
    # 中美利差
    print("[6/10] 中美利差...")
    cn_yield = fetch_cn_bond_yield(pro, date_str)
    
    spread_bp = None
    cn10y_val = None
    us10y_val = None
    if us_yield and us_yield.get('y10') and cn_yield and cn_yield.get('10Y'):
        cn10y_val = cn_yield['10Y']
        us10y_val = us_yield['y10']
        spread_bp = round((us10y_val - cn10y_val) * 100)
    
    prev_spread_bp = cache.get('spread_bp')
    spread_chg = spread_bp - prev_spread_bp if spread_bp and prev_spread_bp else None
    spread_week_chg = None
    
    # 计算周变动：从spread_history取5天前
    spread_history = cache.get('spread_history', {})
    if spread_bp is not None:
        # 历史数据太少时，用bond_zh_us历史回填
        if len(spread_history) < 6:
            try:
                _hist = fetch_bond_zh_us_history(252)
                if _hist is not None and not _hist.empty:
                    for _idx in range(len(_hist)):
                        _r = _hist.iloc[_idx]
                        _d = str(_r.get('日期', ''))[:10]
                        _u = safe_float(_r.get('美国国债收益率10年'))
                        _c = safe_float(_r.get('中国国债收益率10年'))
                        if _d and _u and _c and len(_d) == 10:
                            spread_history[_d] = round((_u - _c) * 100)
                    if len(spread_history) > 250:
                        _sd2 = sorted(spread_history.keys())
                        for _d3 in _sd2[:-250]:
                            del spread_history[_d3]
            except Exception:
                pass
        
        from datetime import datetime as _dt
        _today = _dt.now().strftime('%Y-%m-%d')
        spread_history[_today] = spread_bp
        if len(spread_history) > 250:
            _sd = sorted(spread_history.keys())
            for _d in _sd[:-250]:
                del spread_history[_d]
        cache['spread_history'] = spread_history
        # 周环比
        _sd = sorted(spread_history.keys())
        if len(_sd) >= 6:
            spread_week_chg = spread_bp - spread_history[_sd[-6]]
    
    add("\n### 🔗 中美利差")
    if spread_bp is not None:
        add(f"\n| 指标 | 数值 | 日变动 | 周变动 |")
        add(f"|:---|:---:|:---:|:---:|")
        _wk = fmt_bp(spread_week_chg) + 'BP' if spread_week_chg is not None else '-'
        add(f"| 中美10Y利差 | -{abs(spread_bp)}BP | {fmt_bp(spread_chg) + 'BP'} | {_wk} |")
        add(f"| 中国10Y | {cn10y_val}% | - | - |")
        add(f"| 美国10Y | {us10y_val}% | - | - |")
        
        if spread_bp > 250:
            add(f"\n⚠️ 中美利差倒挂{spread_bp}BP，资本外流压力大")
        
        cache['spread_bp'] = spread_bp
    else:
        bond_zh_us = fetch_bond_zh_us()
        if bond_zh_us is not None and not bond_zh_us.empty:
            for idx in range(len(bond_zh_us)-1, -1, -1):
                row = bond_zh_us.iloc[idx]
                cn10 = safe_float(row.get('中国国债收益率10年'))
                us10 = safe_float(row.get('美国国债收益率10年'))
                if cn10 and us10:
                    spread_bp = round((us10 - cn10) * 100)
                    cn10y_val = cn10
                    us10y_val = us10
                    prev_spread_bp = cache.get('spread_bp')
                    spread_chg = spread_bp - prev_spread_bp if prev_spread_bp else None
                    
                    # 用bond_zh_us回填spread_history
                    if len(spread_history) < 6:
                        try:
                            for _idx in range(len(bond_zh_us)):
                                _r = bond_zh_us.iloc[_idx]
                                _d = str(_r.get('日期', ''))[:10]
                                _u = safe_float(_r.get('美国国债收益率10年'))
                                _c = safe_float(_r.get('中国国债收益率10年'))
                                if _d and _u and _c and len(_d) == 10:
                                    spread_history[_d] = round((_u - _c) * 100)
                            if len(spread_history) > 250:
                                _sd = sorted(spread_history.keys())
                                for _d2 in _sd[:-250]:
                                    del spread_history[_d2]
                        except Exception:
                            pass
                    
                    from datetime import datetime as _dt2
                    _today2 = _dt2.now().strftime('%Y-%m-%d')
                    spread_history[_today2] = spread_bp
                    cache['spread_history'] = spread_history
                    _sd2 = sorted(spread_history.keys())
                    if len(_sd2) >= 6:
                        spread_week_chg = spread_bp - spread_history[_sd2[-6]]
                    
                    _wk2 = fmt_bp(spread_week_chg) + 'BP' if spread_week_chg is not None else '-'
                    add(f"\n| 指标 | 数值 | 日变动 | 周变动 |")
                    add(f"|:---|:---:|:---:|:---:|")
                    add(f"| 中美10Y利差 | -{abs(spread_bp)}BP | {fmt_bp(spread_chg) + 'BP'} | {_wk2} | (akshare备选)")
                    add(f"| 中国10Y | {cn10y_val}% | - | - |")
                    add(f"| 美国10Y | {us10y_val}% | - | - |")
                    if spread_bp > 250:
                        add(f"\n⚠️ 中美利差倒挂{spread_bp}BP，资本外流压力大")
                    cache['spread_bp'] = spread_bp
                    break
        else:
            add("\n⚠️ 中美利差数据获取失败")
    
    # USDCNH
    print("[7/10] USDCNH...")
    usdcnh_val, usdcnh_src = fetch_usdcnh(pro, date_str)
    add("\n### 💱 离岸/在岸人民币汇率")
    if usdcnh_val:
        prev_cnh = cache.get('usdcnh')
        chg = round((usdcnh_val - prev_cnh) / prev_cnh * 100, 3) if prev_cnh else None
        src_label = '在岸即期(akshare)' if usdcnh_src == 'cn_y_spot' else ('离岸(tushare)' if usdcnh_src == 'tushare_cnh' else usdcnh_src)
        add(f"\n**USD/CNY**: {usdcnh_val} ({fmt(chg, '%', '-')})")
        add(f"\n数据源: {src_label}")
        cache['usdcnh'] = usdcnh_val
    else:
        add("\n⚠️ USDCNY获取失败")
    
    # ===== 美债利率-流动性传导监控 =====
    print("[7.5/10] 美债利率-流动性传导监控...")
    if UST_MONITOR_AVAILABLE:
        try:
            _ust_data, _ust_md = run_ust_monitor()
            add(_ust_md)
            ust_monitor_data = _ust_data
        except Exception as _ust_err:
            add("\n### 🔬 美债利率-流动性传导监控")
            add(f"\n⚠️ 监控模块运行失败: {_ust_err}")
            print(f"  [WARN] 美债传导监控运行失败: {_ust_err}")
    else:
        add("\n### 🔬 美债利率-流动性传导监控")
        add("\n⚠️ 模块不可用，跳过")

    # ===== 美元流动性水位仪（纯代码，零 LLM） =====
    print("[7.6/10] 美元流动性水位仪...")
    usd_gauge_data = None
    if USD_GAUGE_AVAILABLE:
        try:
            usd_gauge_data = run_usd_gauge(verbose=False)
            add("\n### 🌊 美元流动性水位仪")
            add("")
            add(render_usd_gauge_md(usd_gauge_data))
            print(f"  水位判级: {usd_gauge_data.get('level')} | {usd_gauge_data.get('verdict','')[:60]}")
        except Exception as _gauge_err:
            add("\n### 🌊 美元流动性水位仪")
            add(f"\n⚠️ 运行失败: {_gauge_err}")
            print(f"  [WARN] 美元流动性水位仪运行失败: {_gauge_err}")
    else:
        add("\n### 🌊 美元流动性水位仪")
        add("\n⚠️ 模块不可用，跳过")

    # ===== 美元流动性指纹侦查（纯代码，零 LLM）=====
    print("[7.7/10] 美元流动性指纹侦查...")
    usd_fp_data = None
    if USD_FP_AVAILABLE:
        try:
            usd_fp_data = run_usd_fp(verbose=False)
            add("")
            add(render_usd_fp_md(usd_fp_data))
            _top = usd_fp_data.get('top')
            _sc = (usd_fp_data.get('step4') or {}).get('scores', {})
            print(f"  首位嫌疑: {_top} ({_sc.get(_top)}分) | {usd_fp_data.get('concl','')[:70]}")
        except Exception as _fp_err:
            add("\n### 🕵️ 美元流动性指纹侦查")
            add(f"\n⚠️ 运行失败: {_fp_err}")
            print(f"  [WARN] 美元流动性指纹侦查运行失败: {_fp_err}")
    else:
        add("\n### 🕵️ 美元流动性指纹侦查")
        add("\n⚠️ 模块不可用，跳过")

    # ========== Part 2: 中国流动性 ==========
    add("\n---\n")
    add("## 二、中国流动性")
    
    print("[8/10] Shibor/LPR...")
    shibor = fetch_shibor(pro, date_str)
    shibor_5d = fetch_shibor_5d(pro)
    lpr = fetch_lpr()
    
    print("[8.5/10] 国债收益率环比...")
    cn_yield_change = fetch_cn_bond_yield_change(pro, date_str)
    
    add("\n### 📈 Shibor利率")
    if shibor:
        is_fallback = shibor.get('_is_fallback', False)
        shibor_date = shibor.get('_date', date_str)
        if is_fallback:
            add(f"\n⏳ 取{shibor_date}数据（当日未入库）")
        prev_row = shibor_5d.iloc[-2] if shibor_5d is not None and len(shibor_5d) >= 2 else None
        week_prev_row = shibor_5d.iloc[0] if shibor_5d is not None and len(shibor_5d) >= 6 else None
        
        add(f"\n| 期限 | 利率(%) | 日变动(BP) | 周变动(BP) |")
        add(f"|:---|:---:|:---:|:---:|")
        for key, label in [('ON','O/N'), ('1W','1W'), ('2W','2W'), ('1M','1M'), ('3M','3M'), ('6M','6M'), ('1Y','1Y')]:
            val = shibor.get(key)
            prev_val = safe_float(prev_row.get(key.lower())) if prev_row is not None else None
            week_prev_val = safe_float(week_prev_row.get(key.lower())) if week_prev_row is not None else None
            bp = calc_bp(val, prev_val) if val and prev_val else None
            week_bp = calc_bp(val, week_prev_val) if val and week_prev_val else None
            add(f"| {label} | {fmt(val)} | {fmt_bp(bp)} | {fmt_bp(week_bp)} |")
        
        shibor_1w = shibor.get('1W')
        if shibor_1w:
            tag = alert_tag(shibor_1w, 1.8, 2.5)
            add(f"\n💡 Shibor 1W {shibor_1w}% {tag}")
    else:
        add("\n⚠️ Shibor获取失败")
    
    # LPR
    if lpr is not None and not lpr.empty:
        row = lpr.iloc[-1]
        add(f"\n### 📋 LPR利率")
        add(f"\n| 期限 | 利率(%) |")
        add(f"|:---|:---:|")
        add(f"| 1Y | {fmt(safe_float(row.get('LPR1Y')))} |")
        add(f"| 5Y | {fmt(safe_float(row.get('LPR5Y')))} |")
    
    # 中国国债
    add("\n### 🏛️ 中国国债收益率")
    if cn_yield or cn_yield_change:
        add(f"\n| 期限 | 收益率(%) | 日变动(BP) | 周变动(BP) |")
        add(f"|:---|:---:|:---:|:---:|")
        for term in ['1Y', '2Y', '5Y', '10Y', '30Y']:
            val = safe_float(cn_yield.get(term)) if cn_yield else None
            day_chg = None
            week_chg = None
            if cn_yield_change and term in cn_yield_change:
                # fallback: if cn_yield is None, use cn_yield_change's curr value
                if val is None:
                    val = cn_yield_change[term].get('curr')
                day_chg = cn_yield_change[term].get('day_chg')
                week_chg = cn_yield_change[term].get('week_chg')
            add(f"| {term} | {fmt(val)} | {fmt_bp(day_chg)} | {fmt_bp(week_chg)} |")
    
    # 央行流动性操作深度追踪
    print("[8.7/10] 央行流动性深度追踪...")
    pbo = fetch_pbo_lending_deep()
    add("\n### 🏦 央行流动性操作")
    
    # ===== OMO操作部分 =====
    omo = pbo.get('omo') if pbo else None
    if omo:
        rr_balance = omo.get('reverse_repo_balance')
        today_net = omo.get('today_net')
        today_injection = omo.get('today_injection')
        today_maturity = omo.get('today_maturity')
        omo_date = omo.get('date', '')
        
        add(f"\n**OMO操作** ({omo_date}):")
        add(f"\n| 指标 | 数值(亿) |")
        add(f"|:---|:---:|")
        if rr_balance is not None:
            add(f"| 逆回购余额 | {rr_balance:,} |")
        if today_injection is not None:
            add(f"| 今日操作/到期 | {today_injection:,} / {today_maturity:,} |")
        if today_net is not None:
            net_tag = "🟢净投放" if today_net > 0 else "🔴净回笼" if today_net < 0 else "⚪零投放"
            add(f"| 今日净投放 | {today_net:+,} | {net_tag} |")
        
        if omo.get('operations'):
            add(f"\n操作明细:")
            for op in omo['operations']:
                term_label = "隔夜" if op['term'] == 0 else f"{op['term']}天"
                rate_str = f" @ {op['rate']}%" if op['rate'] else ""
                add(f"- {term_label}逆回购 {op['amount']:,}亿{rate_str}")
    else:
        add("\n⚠️ OMO操作数据获取失败")
    
    # ===== 资金面判定（扰动 vs 真紧）—— 纯Python零LLM =====
    liq_result = None
    try:
        import pboc_liquidity_reader as _plr
        liq_result = _plr.run(omo_data=pbo.get('omo') if pbo else None)
        for _ln in _plr.render_md_block(liq_result):
            add(_ln)
    except Exception as _liq_err:
        print(f"  [WARN] 资金面判定模块失败: {_liq_err}")
        add("\n⚠️ 资金面判定模块数据获取失败")
    
    # ===== OMO异常告警（75分位数阈值）=====
    omo_alert_triggered = False
    omo_alert_reason = ""
    if omo and cache:
        # 更新OMO历史缓存
        update_omo_history_cache(cache, omo)
        
        # 判断是否触发告警
        should_alert, alert_reason, pct = check_omo_75pct_alert(cache, omo)
        omo_alert_pct = pct
        
        if should_alert:
            omo_alert_triggered = True
            omo_alert_reason = alert_reason
            # 找隔夜逆回购信息
            overnight_ops = check_overnight_repo_info(omo)
            
            # 历史数据量
            history = get_omo_net_history(cache, omo)
            history_count = len(history)
            
            # 告警只登记到统一告警簿，邮件由「报警数≥3」统一触发
            print(f"  🚨 OMO报警触发: {alert_reason}")
            
            # 在Markdown报告中也标注
            add(f"\n> 🚨 **OMO异常报警**: {alert_reason}")
            if overnight_ops:
                op_descs = ', '.join(f"{op['amount']}亿" for op in overnight_ops)
                add(f"> 隔夜逆回购操作: {op_descs}")
        else:
            print(f"  ℹ️ OMO未触发告警: {alert_reason}")
    
    # ===== 回购利率分位数部分 =====
    analysis = pbo.get('repo_analysis') if pbo else None
    if analysis and 'FDR007' in analysis:
        add("\n### 📊 银行间回购利率分位数")
        
        # 核心利率表
        add(f"\n| 品种 | 利率(%) | 日变动 | 周变动 | 月变动 | 年变动 | 近两年分位 | 状态 |")
        add(f"|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
        
        rate_info = [
            ('FDR001', 'DR001 (隔夜)'),
            ('FDR007', 'DR007 (7天) ⭐'),
            ('FDR014', 'DR014 (14天)'),
            ('FR001', 'FR001 (全市场隔夜)'),
            ('FR007', 'FR007 (全市场7天)'),
            ('FR014', 'FR014 (全市场14天)'),
        ]
        for key, label in rate_info:
            if key in analysis:
                d = analysis[key]
                def _fmt_bp(v):
                    return f"{v:+.1f}BP" if v is not None else "-"
                add(f"| {label} | {d['current']:.3f} | "
                    f"{_fmt_bp(d['chg_1d_bp'])} | "
                    f"{_fmt_bp(d['chg_5d_bp'])} | "
                    f"{_fmt_bp(d['chg_20d_bp'])} | "
                    f"{_fmt_bp(d['chg_250d_bp'])} | "
                    f"{d['percentile']}% | "
                    f"{d['alert']} |")
        
        # DR007 深度展示
        dr007 = analysis.get('FDR007')
        if dr007:
            add(f"\n**DR007深度追踪**:")
            
            # 进度条
            range_val = dr007['high'] - dr007['low']
            if range_val > 0:
                pos_pct = int((dr007['current'] - dr007['low']) / range_val * 20)
                pos_pct = max(0, min(20, pos_pct))
                bar = '█' * pos_pct + '░' * (20 - pos_pct)
                add(f"\n`{bar}` {dr007['current']:.3f}%")
                add(f"\n  区间: {dr007['low']:.3f}% ~ {dr007['high']:.3f}% | 中位 {dr007['median']:.3f}%")
            
            # 与政策利率对比
            if 'policy_spread_bp' in analysis:
                spread = analysis['policy_spread_bp']
                pol_rate = analysis.get('policy_rate', 'N/A')
                add(f"\n**与政策利率(7天逆回购 {pol_rate}%)偏离**: {spread:+.1f}BP")
            
            # 80%分位预警
            if dr007['percentile'] >= 80:
                add(f"\n⭐ **预警**: DR007处于近两年{dr007['percentile']}%分位，{dr007['alert_desc']}")
            else:
                add(f"\n{dr007['alert_desc']}")
        
        # 非银融资压力
        if 'nonbank_spread' in analysis:
            nb = analysis['nonbank_spread']
            add(f"\n**非银融资压力(FR007-DR007利差)**: {nb['current_bp']:.1f}BP {nb['alert']}")
            if nb.get('percentile'):
                add(f"  近两年分位: {nb['percentile']}%")
        
        repo_rates = pbo.get('repo_rates')
        data_src = pbo.get('repo_data_source', 'tushare')
        src_name = {'tushare': 'tushare', 'data_hub/EDB': 'data_hub/EDB'}.get(data_src, '中国货币网')
        if repo_rates is not None:
            add(f"\n*数据来源: {src_name}，共{len(repo_rates)}个交易日样本*")
    else:
        add("\n⚠️ 回购利率数据获取失败")

    # ===== 回购成交量部分 =====
    if pbo and pbo.get('repo_volume'):
        rv = pbo['repo_volume']
        vols = rv.get('volumes', {})
        vol_date = rv.get('date', 'T-1')
        if vols:
            add("\n### 📈 银行间回购成交量")
            add(f"\n**数据日期**: {vol_date} *(中国货币网每日22:00更新，显示T-1数据)*")
            add(f"\n| 品种 | 成交量(亿元) |")
            add(f"|:---|---:|")
            vol_order = ['DR001', 'DR007', 'DR014', 'R001', 'R007', 'R014']
            vol_labels = {
                'DR001': 'DR001 (存款类隔夜)',
                'DR007': 'DR007 (存款类7天)',
                'DR014': 'DR014 (存款类14天)',
                'R001': 'R001 (全市场隔夜)',
                'R007': 'R007 (全市场7天)',
                'R014': 'R014 (全市场14天)',
            }
            for key in vol_order:
                if key in vols:
                    add(f"| {vol_labels.get(key, key)} | {vols[key]:,.0f} |")
            # 计算总成交量估算（DR001 + DR007 + R001 + R007）
            total_est = sum(vols.get(k, 0) for k in ['DR001', 'DR007', 'R001', 'R007'])
            if total_est > 0:
                add(f"\n**主要品种合计估算**: {total_est:,.0f} 亿元")
    else:
        add("\nℹ️ 回购成交量数据暂不可用")
    
    # MLF操作
    mlf = pbo.get('mlf') if pbo else None
    if mlf:
        mlf_op = mlf.get('operation_amount')
        mlf_mat = mlf.get('maturity_amount')
        mlf_net = mlf.get('net_injection')
        mlf_bal = mlf.get('balance')
        mlf_rate = mlf.get('rate')
        mlf_date = mlf.get('date', '')
        mlf_term = mlf.get('term', '1年')

        add(f"\n**MLF操作** ({mlf_date}):")
        add(f"\n| 指标 | 数值(亿) |")
        add(f"|:---|:---:|")
        if mlf_bal is not None:
            add(f"| MLF余额 | {mlf_bal:,} |")
        if mlf_op is not None and mlf_mat is not None:
            add(f"| 当月操作/到期 | {mlf_op:,} / {mlf_mat:,} |")
        elif mlf_op is not None:
            add(f"| 当月操作 | {mlf_op:,} |")
        if mlf_net is not None:
            net_tag = "🟢净投放" if mlf_net > 0 else "🔴净回笼" if mlf_net < 0 else "⚪零投放"
            add(f"| 当月净投放 | {mlf_net:+,} | {net_tag} |")
        if mlf_rate is not None:
            add(f"| 利率 | {mlf_rate}% |")
        elif mlf.get('rate_note'):
            add(f"| 利率 | {mlf['rate_note']} |")
        add(f"| 期限 | {mlf_term} |")
        if mlf_bal is not None:
            add(f"\n*余额为按 1 年期滚动推算（锚点 2025-05 = 50,320 亿，来自央行操作表），非官方直报*")
    else:
        add("\n⚠️ MLF操作数据获取失败")
    
    # 全A成交额年度分位数（已移至市场综合监控17:00）

    # ===== 1Y AAA同业存单流动性监控 =====
    print("[8.9/10] 同业存单流动性监控...")
    if CD_LIQUIDITY_AVAILABLE:
        try:
            _cd_data = run_monitor()
            _cd_md_raw = render_cd_report(_cd_data)
            _cd_md_lines = ["\n### 🧾 1Y AAA同业存单流动性"]
            _alert_line = None
            for _l in _cd_md_raw.strip().split('\n'):
                _l = _l.strip()
                if not _l:
                    continue
                if _l.startswith('【告警】'):
                    _alert_line = _l.replace('【告警】', '').strip()
                elif _l.startswith('【日期】'):
                    _cd_date = _l.replace('【日期】', '').strip()
                    _cd_md_lines.append(f"\n**数据日期**: {_cd_date}")
                elif _l.startswith('【快照】'):
                    _snap = _l.replace('【快照】', '').strip()
                    _cd_md_lines.append(f"\n**快照**: {_snap}")
                elif _l.startswith('【所处阶段】'):
                    _phase = _l.replace('【所处阶段】', '').strip()
                    _cd_md_lines.append(f"\n**所处阶段**: {_phase}")
                elif _l.startswith('【相对上日变化】'):
                    _chg = _l.replace('【相对上日变化】', '').strip()
                    _cd_md_lines.append(f"**变化**: {_chg}")
                elif _l.startswith('【利差与曲线】'):
                    _spread = _l.replace('【利差与曲线】', '').strip()
                    _cd_md_lines.append(f"**利差与曲线**: {_spread}")
                elif _l.startswith('【下一关键观测位】'):
                    _next = _l.replace('【下一关键观测位】', '').strip()
                    _cd_md_lines.append(f"**下一观测位**: {_next}")
                elif _l.startswith('【今日操作含义】'):
                    _act = _l.replace('【今日操作含义】', '').strip()
                    _cd_md_lines.append(f"**操作含义**: {_act}")
                elif _l.startswith('【数据说明】'):
                    _note = _l.replace('【数据说明】', '').strip()
                    _cd_md_lines.append(f"\n*数据说明: {_note}*")
            if _alert_line:
                _cd_md_lines.insert(1, f"\n⚠️ **告警**: {_alert_line}")
            add('\n'.join(_cd_md_lines))
        except Exception as _cd_err:
            add("\n### 🧾 1Y AAA同业存单流动性")
            add(f"\n⚠️ 监控模块运行失败: {_cd_err}")
            print(f"  [WARN] 同业存单流动性监控运行失败: {_cd_err}")
    else:
        add("\n### 🧾 1Y AAA同业存单流动性")
        add("\n⚠️ 模块不可用，跳过")

    # ===== 降准降息预期利差（1Y−3M 国开/存单） =====
    print("[8.95/10] 降准降息预期利差...")
    _GK_TITLE = "\n### 🎯 降准降息预期利差（1Y−3M 国开）"
    if GK_SPREAD_AVAILABLE:
        try:
            _gk_data = run_gk_monitor()
            _gk_md_raw = render_gk_report(_gk_data)
            _gk_md_lines = [_GK_TITLE]
            _gk_alert = None
            _gk_map = {
                '【日期】': ('\n**数据日期**: ', ''),
                '【快照】': ('\n**快照**: ', ''),
                '【所处阶段】': ('\n**所处阶段**: ', ''),
                '【相对上日变化】': ('**变化**: ', ''),
                '【利差与曲线】': ('**利差与曲线**: ', ''),
                '【场景判断】': ('**场景判断**: ', ''),
                '【下一关键观测位】': ('**下一观测位**: ', ''),
                '【今日操作含义】': ('**操作含义**: ', ''),
                '【数据说明】': ('\n*数据说明: ', '*'),
            }
            for _l in _gk_md_raw.strip().split('\n'):
                _l = _l.strip()
                if not _l:
                    continue
                if _l.startswith('【告警】'):
                    _gk_alert = _l.replace('【告警】', '').strip()
                    continue
                for _tag, (_pre, _suf) in _gk_map.items():
                    if _l.startswith(_tag):
                        _gk_md_lines.append(_pre + _l.replace(_tag, '').strip() + _suf)
                        break
            if _gk_alert:
                _gk_md_lines.insert(1, f"\n⚠️ **告警**: {_gk_alert}")
            add('\n'.join(_gk_md_lines))
        except Exception as _gk_err:
            add(_GK_TITLE)
            add(f"\n⚠️ 监控模块运行失败: {_gk_err}")
            print(f"  [WARN] 降准降息预期利差监控运行失败: {_gk_err}")
    else:
        add(_GK_TITLE)
        add("\n⚠️ 模块不可用，跳过")

    # ========== Part 3: 1Y掉期点 ==========
    add("\n---\n")
    add("## 三、1Y掉期点（年化掉期率）")
    
    # 主数据源: CFETS实盘 (akshare)
    print("[7.5/10] CFETS 1Y掉期点...")
    cfets_swap = fetch_swap_cfets()
    
    swap_annualized = None  # 年化掉期率(%)，负值=贴水
    swap_pips = None
    swap_spot = None
    swap_source = ''
    swap_details = []
    
    if cfets_swap and cfets_swap.get('1y_annualized_pct') is not None:
        swap_annualized = cfets_swap['1y_annualized_pct']
        swap_pips = cfets_swap.get('1y_pips')
        swap_spot = cfets_swap.get('spot')
        swap_source = cfets_swap['source']
        pips_1m = cfets_swap.get('1m_pips')
        pips_3m = cfets_swap.get('3m_pips')
        pips_6m = cfets_swap.get('6m_pips')
        swap_details = []
        if pips_1m is not None:
            swap_details.append(f"1M: {pips_1m:.0f}pips")
        if pips_3m is not None:
            swap_details.append(f"3M: {pips_3m:.0f}pips")
        if pips_6m is not None:
            swap_details.append(f"6M: {pips_6m:.0f}pips")
        if swap_pips is not None:
            swap_details.append(f"1Y: {swap_pips:.0f}pips")
    else:
        # 备用: 2Y中美利差估算（2Y利差最接近1Y掉期率的期限特征，CIP理论近似）
        us2y_val = us_yield.get('y2') if us_yield else None
        cn2y_val = cn_yield.get('2Y') if cn_yield else None
        if us2y_val is None or cn2y_val is None:
            # 再fallback到bond_zh_us
            try:
                _bzu = fetch_bond_zh_us()
                if _bzu is not None and not _bzu.empty:
                    for idx in range(len(_bzu)-1, -1, -1):
                        row = _bzu.iloc[idx]
                        _u2 = safe_float(row.get('美国国债收益率2年'))
                        _c2 = safe_float(row.get('中国国债收益率2年'))
                        if _u2 and _c2:
                            us2y_val = _u2
                            cn2y_val = _c2
                            break
            except Exception:
                pass
        
        if us2y_val is not None and cn2y_val is not None:
            # 注意符号: 掉期率=人民币利率-美元利率，贴水(美元利率更高)时为负
            swap_annualized = round(cn2y_val - us2y_val, 2)
            swap_source = '2Y利差估算(备用)'
            swap_details = [f"中债2Y({cn2y_val}%) - 美债2Y({us2y_val}%)"]
    
    if swap_annualized is not None:
        # 周环比/月环比 - 从历史缓存取；首次运行时用2Y利差历史回填
        swap_history = cache.get('swap_history', {})
        
        # 历史数据太少时，用2Y中美利差历史回填（保证周/月环比和分位可用）
        if len(swap_history) < 22:
            try:
                _bzu2 = fetch_bond_zh_us_history(252)
                if _bzu2 is not None and not _bzu2.empty:
                    for idx in range(len(_bzu2)):
                        row = _bzu2.iloc[idx]
                        _date = str(row.get('日期', ''))[:10]
                        _u2 = safe_float(row.get('美国国债收益率2年'))
                        _c2 = safe_float(row.get('中国国债收益率2年'))
                        if _date and _u2 and _c2 and len(_date) == 10:
                            # 掉期率 = 中债 - 美债 = 负值（贴水）
                            _spread = round(_c2 - _u2, 2)
                            if _date not in swap_history:
                                swap_history[_date] = _spread
                    # 裁剪到250条
                    if len(swap_history) > 250:
                        _sd = sorted(swap_history.keys())
                        for _d in _sd[:-250]:
                            del swap_history[_d]
            except Exception:
                pass
        
        # 保存今天数据到历史
        from datetime import datetime as _dt
        today_str = _dt.now().strftime('%Y-%m-%d')
        swap_history[today_str] = swap_annualized
        # 只保留最近250个交易日
        if len(swap_history) > 250:
            sorted_dates = sorted(swap_history.keys())
            for old_date in sorted_dates[:-250]:
                del swap_history[old_date]
        cache['swap_history'] = swap_history
        
        # 同时写入SQLite（项目共享目录）
        try:
            _db_path = (os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/data/market.db'
            _conn = sqlite3.connect(_db_path)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute(
                'INSERT OR REPLACE INTO swap_1y_history (date, annualized_pct, pips, spot, source) VALUES (?, ?, ?, ?, ?)',
                (today_str, swap_annualized, swap_pips, float(swap_spot) if swap_spot else None, swap_source)
            )
            _conn.commit()
            _conn.close()
        except Exception as _e:
            print(f"  [WARN] 掉期点SQLite写入失败: {_e}")
        
        # 算周环比(5个交易日前)
        sorted_dates = sorted(swap_history.keys())
        wk_chg = None
        mo_chg = None
        pct_1y = None
        
        if len(sorted_dates) >= 6:
            week_ago_date = sorted_dates[-6]  # 约1周前
            wk_chg = round(swap_annualized - swap_history[week_ago_date], 2)
        
        if len(sorted_dates) >= 22:
            month_ago_date = sorted_dates[-22]  # 约1个月前
            mo_chg = round(swap_annualized - swap_history[month_ago_date], 2)
        
        # 近一年分位数
        if len(sorted_dates) >= 20:
            vals = [swap_history[d] for d in sorted_dates]
            pct_1y = calc_percentile(vals, swap_annualized)
        
        # 状态标签：用绝对值判断，越大越紧
        abs_val = abs(swap_annualized)
        if abs_val >= 3.0:
            tag = '🔴'
        elif abs_val >= 2.5:
            tag = '🟡'
        else:
            tag = '🟢'
        
        sign_str = "+" if swap_annualized > 0 else ""
        add(f"\n| 指标 | 年化掉期率 | 周环比 | 月环比 | 近1年分位 | 状态 | 数据源 |")
        add(f"|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
        wk_str = f"{wk_chg:+.2f}pp" if wk_chg is not None else "-"
        mo_str = f"{mo_chg:+.2f}pp" if mo_chg is not None else "-"
        pct_str = f"{pct_1y:.0f}%" if pct_1y is not None else "-"
        add(f"| 1Y USD/CNY | {sign_str}{swap_annualized:.2f}% | {wk_str} | {mo_str} | {pct_str} | {tag} | {swap_source} |")
        
        # ===== 近一周逐日掉期点走势 =====
        add("\n### 📈 近一周1Y掉期点走势")
        sorted_dates_all = sorted(swap_history.keys())
        n_week = min(7, len(sorted_dates_all))
        recent_dates = sorted_dates_all[-n_week:]
        week_rows = []
        prev_val = None
        daily_chg_bp_list = []
        for d in recent_dates:
            v = swap_history[d]
            chg_bp = round((v - prev_val) * 100, 1) if prev_val is not None else None
            daily_chg_bp_list.append(chg_bp)
            chg_str = ""
            if chg_bp is not None:
                arrow = "↑" if chg_bp > 0 else "↓" if chg_bp < 0 else "→"
                chg_str = f"{arrow}{abs(chg_bp):.0f}BP"
                if abs(chg_bp) >= 20:
                    chg_str += " ⚠️"
            v_sign = "+" if v > 0 else ""
            week_rows.append((d, f"{v_sign}{v:.2f}%", chg_str))
            prev_val = v
        
        # 表格展示
        add(f"\n| 日期 | 年化掉期率 | 日变动 |")
        add(f"|:---|:---:|:---|")
        for d, v, c in week_rows:
            add(f"| {d} | {v} | {c} |")
        
        # ===== ASCII字符图：近一周 + 近一月 =====
        def generate_swap_ascii_chart(hist_dict, days, title, bar_width=30):
            """生成掉期率ASCII横向条形图（贴水=条形长度，向右越长贴水越大）"""
            sorted_d = sorted(hist_dict.keys())[-days:]
            vals = [hist_dict[d] for d in sorted_d]
            if not vals:
                return ""
            
            # 刻度范围：0 ~ -4%（覆盖正常贴水区间）
            top = 0.0
            bottom = -4.0
            min_v = min(vals)
            if min_v < bottom:
                bottom = min_v - 0.2
            
            lines = [f"**{title}**", "```"]
            lines.append(f"  日期 │  {'贴水幅度 →':<{bar_width}} 掉期率")
            lines.append("  ────┼" + "─" * (bar_width + 12))
            
            for d in sorted_d:
                v = hist_dict[d]
                # 贴水幅度 = 0 - v（v是负值，贴水幅度为正）
                premium = 0 - v
                ratio = premium / (0 - bottom)
                bar_len = max(0, int(ratio * bar_width))
                # 用渐变色字符模拟层次
                bar = '█' * bar_len
                label = d[5:]  # MM-DD
                v_sign = "+" if v > 0 else ""
                lines.append(f"  {label} │ {bar:<{bar_width}} {v_sign}{v:.2f}%")
            
            lines.append("  ────┴" + "─" * (bar_width + 12))
            lines.append(f"       0%{' ' * (bar_width - 5)}{bottom:.0f}%")
            lines.append("```")
            return "\n".join(lines)
        
        if len(sorted_dates_all) >= 3:
            # 近一周
            add(f"\n{generate_swap_ascii_chart(swap_history, 7, '近一周 1Y掉期率', bar_width=30)}")
        
        if len(sorted_dates_all) >= 10:
            # 近一月
            add(f"\n{generate_swap_ascii_chart(swap_history, 30, '近一月 1Y掉期率', bar_width=40)}")
        
        # 画近一周走势折线图
        swap_chart_path = None
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from datetime import datetime as _dtm
            
            fig, ax = plt.subplots(figsize=(8, 4))
            dates_list = [_dtm.strptime(d, '%Y-%m-%d') for d in recent_dates]
            vals_list = [swap_history[d] for d in recent_dates]
            
            ax.plot(dates_list, vals_list, 'o-', linewidth=2, markersize=6, color='#e74c3c')
            ax.fill_between(dates_list, vals_list, alpha=0.15, color='#e74c3c')
            
            # 标注最新值
            ax.annotate(f'{vals_list[-1]:.2f}%', 
                       xy=(dates_list[-1], vals_list[-1]),
                       xytext=(-50, 15), textcoords='offset points',
                       fontsize=11, fontweight='bold', color='#e74c3c')
            
            ax.set_title('1Y USD/CNY 掉期率走势（近一周）', fontsize=13, fontweight='bold')
            ax.set_ylabel('年化掉期率 (%)')
            ax.grid(True, alpha=0.3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
            ax.tick_params(axis='x', rotation=0)
            
            # 背景深色主题
            fig.patch.set_facecolor('#1a1a2e')
            ax.set_facecolor('#16213e')
            ax.tick_params(colors='white')
            ax.title.set_color('white')
            ax.yaxis.label.set_color('white')
            for spine in ax.spines.values():
                spine.set_color('#0f3460')
            ax.grid(color='#0f3460', alpha=0.5)
            
            # 输出目录
            out_dir = f(os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/outputs/charts'
            os.makedirs(out_dir, exist_ok=True)
            chart_fname = f'{today_str}_1y_swap_weekly.png'
            swap_chart_path = os.path.join(out_dir, chart_fname)
            plt.tight_layout()
            plt.savefig(swap_chart_path, dpi=120, facecolor=fig.get_facecolor())
            plt.close()
        except Exception as e:
            print(f"  [WARN] 掉期点走势图绘制失败: {e}")
        
        # ===== 掉期点异动告警 =====
        swap_alert_msg = None
        last_chg_bp = daily_chg_bp_list[-1] if daily_chg_bp_list and daily_chg_bp_list[-1] is not None else None
        
        if last_chg_bp is not None and abs(last_chg_bp) >= 20:
            direction = "贴水扩大（美元流动性收紧）" if last_chg_bp < 0 else "贴水收窄（美元流动性缓解）"
            pct_label = ""
            if pct_1y is not None:
                pct_label = f"，位于近1年{pct_1y:.0f}%分位"
            swap_alert_msg = (
                f"1Y掉期率单日变动 {last_chg_bp:+.0f}BP（{direction}）{pct_label}\n"
                f"当前值: {sign_str}{swap_annualized:.2f}%\n"
                f"前日值: {sign_str}{swap_history[sorted_dates_all[-2]]:.2f}%"
            )
        
        if swap_alert_msg and cfets_swap:
            # 只对CFETS真实数据告警，不对估算数据告警
            print(f"  ⚠️ 掉期点异动: {last_chg_bp:+.0f}BP")
            try:
                import sys
                sys.path.insert(0, (os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts')
                from send_email import send_email_default
                subject = f"⚠️ 1Y掉期点异动 {last_chg_bp:+.0f}BP - {today_str}"
                body = f"""
<b>1年期掉期点异常变动提醒</b><br><br>
📅 日期: {today_str}<br>
📊 当前掉期率: <b>{sign_str}{swap_annualized:.2f}%</b><br>
📈 日变动: <b style="color:red">{last_chg_bp:+.0f}BP</b> ({direction})<br>
📉 前日值: {sign_str}{swap_history[sorted_dates_all[-2]]:.2f}%<br>
📍 近1年分位: {pct_1y:.0f}%<br>
🔍 数据源: CFETS远掉报价<br>
<br>
---<br>
<small>环流日报掉期点监控自动推送</small>
"""
                # 尝试附加走势图
                attachment = swap_chart_path if swap_chart_path and os.path.exists(swap_chart_path) else None
                if attachment:
                    send_email_default(subject=subject, body=body, html=True, attachment=attachment)
                else:
                    send_email_default(subject=subject, body=body, html=True)
                print(f"  ✅ 掉期点告警邮件已发送")
            except Exception as e:
                print(f"  [WARN] 掉期点告警邮件发送失败: {e}")
        
        # 近一周3日斜率判断（连续向下=贴水持续扩大趋势）
        if len(daily_chg_bp_list) >= 3 and all(x is not None and x < 0 for x in daily_chg_bp_list[-3:]):
            total_3d = sum(x for x in daily_chg_bp_list[-3:] if x is not None)
            if abs(total_3d) >= 30:
                print(f"  ⚠️ 掉期点连续3日下行趋势，累计{total_3d:.0f}BP")
                try:
                    import sys
                    sys.path.insert(0, (os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/环流日报/scripts')
                    from send_email import send_email_default
                    subject = f"📉 掉期点连续下行趋势 - {today_str}"
                    body = f"""
<b>掉期点趋势告警：连续3日贴水扩大</b><br><br>
📅 日期: {today_str}<br>
📊 当前掉期率: <b>{sign_str}{swap_annualized:.2f}%</b><br>
📉 近3日累计变动: <b style="color:red">{total_3d:+.0f}BP</b><br>
💡 解读: 掉期点持续下行 = 人民币远期贴水扩大，美元离岸流动性收紧信号<br>
<br>
---<br>
<small>环流日报掉期点监控自动推送</small>
"""
                    attachment = swap_chart_path if swap_chart_path and os.path.exists(swap_chart_path) else None
                    if attachment:
                        send_email_default(subject=subject, body=body, html=True, attachment=attachment)
                    else:
                        send_email_default(subject=subject, body=body, html=True)
                    print(f"  ✅ 趋势告警邮件已发送")
                except Exception as e:
                    print(f"  [WARN] 趋势告警邮件发送失败: {e}")
        
        if cfets_swap and swap_details:
            add(f"\n💡 CFETS实盘各期限: {', '.join(swap_details)}")
            if swap_spot:
                add(f"> 即期汇率: {swap_spot} | 掉期点: {swap_pips:.0f} pips")
                add(f"> 公式: {swap_pips:.0f} × 0.0001 ÷ {swap_spot} × 100% = {sign_str}{swap_annualized:.2f}%")
        else:
            add(f"\n💡 ⚠️估算: {'; '.join(swap_details)} ≈ {sign_str}{swap_annualized:.2f}%")
            add(f"> CFETS数据获取失败，使用中美2Y国债利差近似（CIP理论：掉期率≈两国利差，2Y期限最接近1Y掉期）")
        
        if swap_annualized < 0:
            add(f"> 负值=贴水（人民币利率低于美元利率，Carry成本）；绝对值越大→美元流动性越紧")
        else:
            add(f"> 正值=升水（人民币利率高于美元利率）")
        
        cache['swap_annualized'] = swap_annualized
        
        # ===== 利率平价验证：1Y存单 + 掉期 ≈ 2Y美债 =====
        add("\n### 📊 利率平价验证")
        cip_valid = False
        cn_1y_cd = None
        us_2y = None
        synthetic_rate = None
        spread = None
        cd_date = ''
        try:
            ak = _AKProxy()  # DataHub 中转
            # 1Y存单代理: 中债商业银行普通债收益率曲线(AAA) 1年期
            try:
                bond_df = ak.bond_china_yield(
                    start_date=(datetime.now() - timedelta(days=10)).strftime('%Y%m%d'),
                    end_date=datetime.now().strftime('%Y%m%d')
                )
                if bond_df is not None and not bond_df.empty:
                    cd_df = bond_df[bond_df['曲线名称'] == '中债商业银行普通债收益率曲线(AAA)']
                    if not cd_df.empty:
                        latest_cd = cd_df.iloc[-1]
                        cn_1y_cd = safe_float(latest_cd.get('1年'))
                        cd_date = str(latest_cd.get('日期', ''))
            except Exception as e:
                print(f"  [WARN] 1Y存单收益率获取失败: {e}")
            
            # 2Y美债：从主流程已获取的 us_yield 中取
            if us_yield:
                us_2y = us_yield.get('y2')
            
            # 合成利率 = 1Y存单利率 + 掉期点年化贴水率
            # 掉期率为负(贴水)时：合成利率 = 存单利率 - 掉期率 = 存单利率 + 贴水绝对值
            if cn_1y_cd is not None and swap_annualized is not None and us_2y is not None:
                synthetic_rate = round(cn_1y_cd - swap_annualized, 2)  # 减负数 = 加贴水
                spread = round(synthetic_rate - us_2y, 2)
                
                if abs(spread) > 0.3:
                    spread_tag = "⚠️偏离较大"
                elif abs(spread) > 0.15:
                    spread_tag = "小幅偏离"
                else:
                    spread_tag = "基本吻合"
                
                add(f"\n| 指标 | 利率(%) |")
                add(f"|:---|:---:|")
                add(f"| 1Y商业银行普通债(AAA) | {cn_1y_cd:.2f} |")
                add(f"| 掉期点年化贴水 | {abs(swap_annualized):.2f} |")
                add(f"| **合成美元利率** | **{synthetic_rate:.2f}** |")
                add(f"| 2Y美债收益率 | {us_2y:.2f} |")
                add(f"| 利差(合成-美债) | {spread:+.2f} {spread_tag} |")
                
                add(f"\n> 利率平价(CIP): 合成美元利率 = 人民币利率 + 掉期贴水，理论上应≈同期限美元利率")
                add(f"> 用1Y存单+1Y掉期合成美元成本，对标2Y美债（存单是信用债，美债是无风险，基差约20-50BP为正常）")
                if cd_date:
                    add(f"> 存单数据日期: {cd_date}")
                cip_valid = True
        except Exception as e:
            print(f"  [WARN] 利率平价验证失败: {e}")
        
        if not cip_valid:
            add("\n> 利率平价数据暂不可用")
        
        # 构造飞书卡片用的swap_data
        swap_data = {
            'annualized_pct': swap_annualized,
            'pips_1y': swap_pips,
            'spot': swap_spot,
            'wk_chg': wk_chg,
            'mo_chg': mo_chg,
            'pct_1y': pct_1y,
            'source': swap_source,
            'cip_valid': cip_valid,
        }
        if cip_valid:
            swap_data['cip_cn_1y_cd'] = cn_1y_cd
            swap_data['cip_us_2y'] = us_2y
            swap_data['cip_synthetic'] = synthetic_rate
            swap_data['cip_spread'] = spread
            swap_data['cip_cd_date'] = cd_date
    else:
        add("\n⚠️ 1Y掉期点数据获取失败 (CFETS与利差估算均不可用)")
        swap_data = None
    
    add(f"\n> 警戒线: 🟡绝对值≥2.5% | 🔴绝对值≥3.0%")

    # ===== 掉期点 × 需求传导（rrr = R + e，纯程序判定）=====
    print("[7.6/10] 掉期点×需求传导...")
    _swap_demand_data = None
    if SWAP_DEMAND_AVAILABLE:
        try:
            _swap_demand_data = run_swap_monitor()
            _sd_raw = render_swap_report(_swap_demand_data)
            _sd_lines = ["\n### 🔁 1Y掉期点 × 需求传导（rrr = R + e）"]
            _sd_alerts = []
            for _l in _sd_raw.strip().split('\n'):
                _l = _l.strip()
                if not _l:
                    continue
                if _l.startswith('【核心读数】'):
                    _sd_lines.append(f"\n**核心读数**: {_l.replace('【核心读数】', '').strip()}")
                elif _l.startswith('【需求判定】'):
                    _sd_lines.append(f"**需求判定**: {_l.replace('【需求判定】', '').strip()}")
                elif _l.startswith('【水位分档】'):
                    _sd_lines.append(f"**水位分档**: {_l.replace('【水位分档】', '').strip()}")
                elif _l.startswith('【判定依据】'):
                    _sd_lines.append(f"**判定依据**: {_l.replace('【判定依据】', '').strip()}")
                elif _l.startswith('【边际方向】'):
                    _sd_lines.append(f"**边际方向**: {_l.replace('【边际方向】', '').strip()}")
                elif _l.startswith('【五变量协调】'):
                    _sd_lines.append(f"**五变量协调**: {_l.replace('【五变量协调】', '').strip()}")
                elif _l.startswith('【三响应变量】'):
                    _sd_lines.append(f"**三响应变量**: {_l.replace('【三响应变量】', '').strip()}")
                elif _l.startswith('【驱动变量】'):
                    _sd_lines.append(f"**驱动变量**: {_l.replace('【驱动变量】', '').strip()}")
                elif _l.startswith('【原始数据】'):
                    _sd_lines.append(f"**原始数据**: {_l.replace('【原始数据】', '').strip()}")
                elif _l.startswith('【数据说明】'):
                    _sd_lines.append(f"\n*数据说明: {_l.replace('【数据说明】', '').strip()}*")
            _dm = (_swap_demand_data or {}).get('demand') or {}
            if _dm.get('verdict'):
                _sd_alerts.append(_dm['verdict'])
            add('\n'.join(_sd_lines))
            print(f"  需求判定: {_dm.get('verdict', '—')} | "
                  f"rrr {_swap_demand_data.get('rrr', 0):.2f}% "
                  f"(中性 5.5%) | e {_swap_demand_data.get('e', 0):.2f}%")
        except Exception as _sw_err:
            add("\n### 🔁 1Y掉期点 × 需求传导（rrr = R + e）")
            add(f"\n⚠️ 需求传导模块运行失败: {_sw_err}")
            print(f"  [WARN] 掉期点需求传导模块运行失败: {_sw_err}")
    else:
        add("\n### 🔁 1Y掉期点 × 需求传导（rrr = R + e）")
        add("\n⚠️ 模块不可用，跳过")

    # ========== Part 4: HIBOR ==========
    add("\n---\n")
    add("## 四、港股流动性 (HIBOR)")
    
    print("[9/10] HIBOR...")
    hibor = fetch_hibor()
    if hibor:
        cur = hibor.get('current')
        prev = hibor.get('previous')
        bp_chg = calc_bp(cur, prev) if cur and prev else None
        tag = alert_tag(cur, ALERT_HIBOR, ALERT_HIBOR + 1)
        source = hibor.get('source', '未知')
        
        add(f"\n| 指标 | 利率(%) | 变动(BP) | 状态 | 数据源 |")
        add(f"|:---|:---:|:---:|:---:|:---:|")
        add(f"| HIBOR 3M | {fmt(cur)} | {fmt_bp(bp_chg)} | {tag} | {source} |")
        
        # HKMA API提供多期限数据
        if hibor.get('overnight') or hibor.get('1m') or hibor.get('6m') or hibor.get('12m'):
            add(f"\n💡 HIBOR期限结构: ON {fmt(hibor.get('overnight'))}% | 1M {fmt(hibor.get('1m'))}% | 3M {fmt(cur)}% | 6M {fmt(hibor.get('6m'))}% | 12M {fmt(hibor.get('12m'))}%")
            if hibor.get('date'):
                add(f"> 数据日期: {hibor.get('date')} | 源: {source}")
    else:
        add("\n⚠️ HIBOR获取失败 (data_hub EDB / HKMA API / TradingEconomics 均不可用)")
    add(f"\n> 警戒线: 🟡>{ALERT_HIBOR}%")
    
    # ========== Part 5: JGB ==========
    add("\n---\n")
    add("## 五、日本国债收益率 (JGB)")
    
    print("[10/10] JGB...")
    jgb = fetch_jgb()
    if jgb:
        jgb10y = jgb.get('10Y')
        jgb30y = jgb.get('30Y')
        jgb20y = jgb.get('20Y')
        jgb40y = jgb.get('40Y')
        jgb2y = jgb.get('2Y')
        jgb5y = jgb.get('5Y')
        
        # 从缓存获取历史序列（用于周/月/年变动）
        jgb10y_history = get_jgb10y_history(cache, jgb10y) if jgb10y else []
        prev_jgb10 = cache.get('jgb10y')
        jgb10y_chg = calc_bp(jgb10y, prev_jgb10) if jgb10y and prev_jgb10 else None
        
        # 计算多周期变动
        jgb_bp_1d = calc_bp_change(jgb10y_history, 1)
        jgb_bp_5d = calc_bp_change(jgb10y_history, 5)
        jgb_bp_20d = calc_bp_change(jgb10y_history, 20)
        jgb_bp_250d = calc_bp_change(jgb10y_history, 250)
        
        # JGB 10Y 警戒
        jgb_tag = alert_tag(jgb10y, ALERT_JGB_YELLOW, ALERT_JGB_RED) if jgb10y else ""
        jgb_chg_tag = ""
        if jgb10y_chg is not None:
            if abs(jgb10y_chg) > ALERT_JGB_DAILY_RED:
                jgb_chg_tag = "🔴"
            elif abs(jgb10y_chg) > ALERT_JGB_DAILY_YELLOW:
                jgb_chg_tag = "🟡"
        
        add(f"\n| 期限 | 收益率(%) |")
        add(f"|:---|:---:|")
        add(f"| 2Y | {fmt(jgb2y)} |")
        add(f"| 5Y | {fmt(jgb5y)} |")
        add(f"| **10Y** | **{fmt(jgb10y)}** |")
        add(f"| 20Y | {fmt(jgb20y)} |")
        add(f"| 30Y | {fmt(jgb30y)} |")
        add(f"| 40Y | {fmt(jgb40y)} |")
        
        add(f"\n**JGB 10Y**: {fmt(jgb10y)}% {jgb_tag} | 日变动: {fmt_bp(jgb10y_chg) + 'BP'} {jgb_chg_tag}")
        
        # 多周期变动表
        add(f"\n| 周期 | 变动(BP) |")
        add(f"|:---|:---:|")
        add(f"| 日环比 | {fmt_bp(jgb_bp_1d)} |")
        add(f"| 周环比 | {fmt_bp(jgb_bp_5d)} |")
        add(f"| 月环比 | {fmt_bp(jgb_bp_20d)} |")
        add(f"| 年同比 | {fmt_bp(jgb_bp_250d)} |")
        if jgb10y_history:
            add(f"\n*历史数据累计 {len(jgb10y_history)} 天，数据越长变动越准确*")
        
        if jgb10y and jgb10y > ALERT_JGB_RED:
            add(f"\n🔴 JGB 10Y > {ALERT_JGB_RED}%！Carry Trade平仓风险高悬")
        elif jgb10y and jgb10y > ALERT_JGB_YELLOW:
            add(f"\n🟡 JGB 10Y > {ALERT_JGB_YELLOW}%，YCC政策承压")
        
        # 美日利差
        if us_yield and us_yield.get('y10') and jgb10y:
            us_jp_spread = round(us_yield['y10'] - jgb10y, 2)
            add(f"\n💡 美日10Y利差: {us_jp_spread}% ({'宽' if us_jp_spread > 3 else '窄'})")
            if us_jp_spread < 2:
                add("  ⚠️ 利差收窄，日元升值/套利平仓风险上升")
        
        # 缓存
        if jgb10y:
            cache['jgb10y'] = jgb10y
    else:
        add("\n⚠️ JGB数据获取失败")
    
    add(f"\n**警戒线**:")
    add(f"- JGB 10Y > {ALERT_JGB_YELLOW}% = 🟡 | > {ALERT_JGB_RED}% = 🔴")
    add(f"- JGB 10Y 单日变动 > {ALERT_JGB_DAILY_YELLOW}BP = 🟡 | > {ALERT_JGB_DAILY_RED}BP = 🔴")
    add(f"\n💡 JGB飙升→日元升值→Carry Trade平仓→全球风险资产承压（2024年8月先例）")
    
    # ========== Part 6: 综合评级 ==========
    add("\n---\n")
    add("## 六、综合评级")
    
    score = 5.0
    risks = []
    positives = []
    
    # 准备金
    if fed and fed.get('reserve'):
        if fed['reserve'] < ALERT_RESERVE:
            score -= 1.5
            risks.append(f"准备金 ${fed['reserve']/1e6:.2f}T < $3.0T🔴")
        elif fed['reserve'] < 3.2e6:
            score -= 0.5
            risks.append(f"准备金 ${fed['reserve']/1e6:.2f}T 偏低")
        else:
            positives.append(f"准备金 ${fed['reserve']/1e6:.2f}T 充裕")
    
    # ON RRP
    if fed and fed.get('rrp_on') is not None:
        if fed['rrp_on'] < ALERT_RRP_ON_RED:
            score -= 1.0
            risks.append(f"ON RRP {fed['rrp_on']/1e3:.2f}B 逼近归零💀")
        elif fed['rrp_on'] < ALERT_RRP_ON:
            score -= 0.5
            risks.append(f"ON RRP {fed['rrp_on']/1e3:.0f}B 枯竭")
    
    # SOFR-IORB
    if spread_g_bp is not None:
        if spread_g_bp > 20:
            score -= 1.0
            risks.append(f"SOFR-IORB {spread_g_bp:+.0f}bp 融资紧张")
        elif spread_g_bp > 5:
            score -= 0.5
            risks.append(f"SOFR-IORB {spread_g_bp:+.0f}bp 融资偏紧")
        elif spread_g_bp < -5:
            score += 0.5
            positives.append(f"SOFR-IORB {spread_g_bp:+.0f}bp 融资充裕")
    
    # QT进程
    if bs and bs.get('total_assets'):
        total_assets_t = bs['total_assets'] / 1e6
        if total_assets_t < 6.5:
            score -= 0.5
            risks.append(f"美联储总资产 ${total_assets_t:.2f}T 大幅缩表")
        elif total_assets_t < 7.0:
            risks.append(f"美联储总资产 ${total_assets_t:.2f}T 持续QT中")
    
    # 中美利差
    if spread_bp is not None:
        if spread_bp > 300:
            score -= 1.0
            risks.append(f"中美利差倒挂{spread_bp}BP")
        elif spread_bp > 250:
            score -= 0.5
            risks.append(f"中美利差倒挂{spread_bp}BP")
    
    # Shibor
    if shibor:
        s1w = shibor.get('1W')
        if s1w:
            if s1w < 1.5:
                score += 0.5
                positives.append(f"Shibor 1W {s1w}% 宽松")
            elif s1w > 2.0:
                score -= 0.5
                risks.append(f"Shibor 1W {s1w}% 偏紧")
    
    # JGB
    if jgb and jgb.get('10Y'):
        if jgb['10Y'] > ALERT_JGB_RED:
            score -= 1.0
            risks.append(f"JGB 10Y {jgb['10Y']}% 远超警戒线")
        elif jgb['10Y'] > ALERT_JGB_YELLOW:
            score -= 0.5
            risks.append(f"JGB 10Y {jgb['10Y']}% 超警戒线")
    
    # 掉期点 (年化掉期率)
    if swap_annualized is not None:
        abs_swap = abs(swap_annualized)
        if abs_swap >= ALERT_SWAP_RED:
            score -= 0.5
            risks.append(f"1Y掉期年化率{swap_annualized:+.2f}% 超红线")
        elif abs_swap >= ALERT_SWAP_YELLOW:
            risks.append(f"1Y掉期年化率{swap_annualized:+.2f}% 触及警戒线")
    

    # ===== 边际变动检测（日级大幅变动自动预警） =====
    # 阈值定义
    ALERT_REPO_VOL_CHG = 0.20      # 回购成交量日变动 ±20%
    ALERT_US10Y_BP = 10.0           # 美债10Y日变动 ±10BP
    ALERT_CNY_PIPS = 300             # USD/CNY日变动 ±300点
    ALERT_SHIBOR1W_BP = 10.0         # Shibor 1W日变动 ±10BP

    # 1. 美债10Y日变动
    try:
        if us_yield and us_5d is not None and len(us_5d) >= 2:
            us10y_curr = safe_float(us_yield.get('10Y'))
            us10y_prev = safe_float(us_5d.iloc[-2].get('10Y'))
            if us10y_curr is not None and us10y_prev is not None and us10y_prev > 0:
                us10y_chg_bp = round((us10y_curr - us10y_prev) * 100, 1)
                if abs(us10y_chg_bp) >= ALERT_US10Y_BP:
                    direction = "飙升" if us10y_chg_bp > 0 else "大幅下行"
                    impact = "融资成本上行，债市承压" if us10y_chg_bp > 0 else "融资环境改善，利好成长"
                    severity = 0.5 if abs(us10y_chg_bp) < 20 else 1.0
                    if us10y_chg_bp > 0:
                        score -= severity
                        risks.append(f"美债10Y {direction} {us10y_chg_bp:+.1f}BP → {impact}")
                    else:
                        score += severity * 0.5
                        positives.append(f"美债10Y {direction} {us10y_chg_bp:+.1f}BP → {impact}")
    except Exception as e:
        print(f"  [INFO] 美债10Y变动检测跳过: {e}")

    # 2. Shibor 1W日变动
    try:
        if shibor and shibor_5d is not None and len(shibor_5d) >= 2:
            shib_curr = safe_float(shibor.get('1W'))
            prev_row_shib = shibor_5d.iloc[-2]
            shib_prev = safe_float(prev_row_shib.get('1w')) if prev_row_shib is not None else None
            if shib_curr is not None and shib_prev is not None and shib_prev > 0:
                shib_chg_bp = round((shib_curr - shib_prev) * 100, 1)
                if abs(shib_chg_bp) >= ALERT_SHIBOR1W_BP:
                    direction = "上行" if shib_chg_bp > 0 else "下行"
                    impact = "资金面收紧" if shib_chg_bp > 0 else "资金面宽松"
                    severity = 0.3 if abs(shib_chg_bp) < 20 else 0.5
                    if shib_chg_bp > 0:
                        score -= severity
                        risks.append(f"Shibor 1W {direction} {shib_chg_bp:+.1f}BP → {impact}")
                    else:
                        score += severity
                        positives.append(f"Shibor 1W {direction} {shib_chg_bp:+.1f}BP → {impact}")
    except Exception as e:
        print(f"  [INFO] Shibor变动检测跳过: {e}")

    # 3. USD/CNY 日变动
    try:
        if usdcnh_val is not None and cache.get('usdcnh_prev'):
            prev_cnh_val = cache['usdcnh_prev']
            if prev_cnh_val and prev_cnh_val > 0:
                cny_chg_pips = round((usdcnh_val - prev_cnh_val) * 10000)
                if abs(cny_chg_pips) >= ALERT_CNY_PIPS:
                    direction = "贬值" if cny_chg_pips > 0 else "升值"
                    impact = "资本外流压力" if cny_chg_pips > 0 else "外资流入动力"
                    severity = 0.5 if abs(cny_chg_pips) < 500 else 1.0
                    if cny_chg_pips > 0:
                        score -= severity
                        risks.append(f"人民币{cny_chg_pips:+d}点 → 大幅{direction}，{impact}")
                    else:
                        score += severity
                        positives.append(f"人民币{cny_chg_pips:+d}点 → 大幅{direction}，{impact}")
    except Exception as e:
        print(f"  [INFO] CNY变动检测跳过: {e}")

    # 4. 回购成交量日变动（DR001为主）
    try:
        if pbo and pbo.get('repo_volume') and cache.get('repo_volume_prev'):
            rv_curr = pbo['repo_volume'].get('volumes', {})
            rv_prev = cache['repo_volume_prev'].get('volumes', {})
            if rv_curr.get('DR001') and rv_prev.get('DR001') and rv_prev['DR001'] > 0:
                dr001_chg = (rv_curr['DR001'] - rv_prev['DR001']) / rv_prev['DR001']
                if abs(dr001_chg) >= ALERT_REPO_VOL_CHG:
                    direction = "放量" if dr001_chg > 0 else "缩量"
                    impact = "交易活跃，资金需求上升" if dr001_chg > 0 else "交投清淡，资金需求弱"
                    severity = 0.3 if abs(dr001_chg) < 0.4 else 0.5
                    if dr001_chg > 0:
                        score -= severity
                        risks.append(f"DR001成交量{direction} {dr001_chg*100:+.0f}% → {impact}")
                    else:
                        score += severity
                        positives.append(f"DR001成交量{direction} {dr001_chg*100:+.0f}% → {impact}")
    except Exception as e:
        print(f"  [INFO] 回购成交量变动检测跳过: {e}")
    score = max(1, min(10, round(score, 1)))

    # ===== 统一告警体系：警告(普通) / 报警(高级) 分级 =====
    # 规则：报警=阈值击穿/方向性恶化，计入计数；报警数≥2 触发邮件提醒
    alert_book = None
    try:
        import alert_book as _ab_mod
        _g = dir()
        alert_book = _ab_mod.AlertBook(date_display)
        alert_book.collect(
            omo=omo if 'omo' in _g else None,
            omo_pct=omo_alert_pct if 'omo_alert_pct' in _g else None,
            omo_alert_reason=omo_alert_reason if 'omo_alert_reason' in _g else '',
            liq=liq_result if 'liq_result' in _g else None,
            analysis=analysis if 'analysis' in _g else None,
            fed=fed if 'fed' in _g else None,
            bs=bs if 'bs' in _g else None,
            jgb=jgb if 'jgb' in _g else None,
            hibor_1m=(hibor.get('current') if ('hibor' in _g and hibor) else None),
            spread_bp=spread_bp if 'spread_bp' in _g else None,
            spread_g_bp=spread_g_bp if 'spread_g_bp' in _g else None,
            swap_annualized=swap_annualized if 'swap_annualized' in _g else None,
            us10y_chg_bp=us10y_chg_bp if 'us10y_chg_bp' in _g else None,
            shib_chg_bp=shib_chg_bp if 'shib_chg_bp' in _g else None,
            cny_chg_pips=cny_chg_pips if 'cny_chg_pips' in _g else None,
            repo_vol_chg=dr001_chg if 'dr001_chg' in _g else None,
            cd_alert=_cd_alert_line if '_cd_alert_line' in _g else None,
            tga=bool(tga_dts) if 'tga_dts' in _g else None,
            mlf=bool(mlf) if 'mlf' in _g else None,
            repo_volume=bool(pbo and pbo.get('repo_volume')) if 'pbo' in _g else None,
            ust=bool(ust_monitor_data) if 'ust_monitor_data' in _g else None,
            srf_amt_5d=(usd_gauge_data.get('raw', {}).get('srf', {}).get('amt_5d')
                        if ('usd_gauge_data' in _g and usd_gauge_data) else None),
            srf_max_1d=(usd_gauge_data.get('raw', {}).get('srf', {}).get('max_1d_5d')
                        if ('usd_gauge_data' in _g and usd_gauge_data) else None),
            srf_sofr_bp=(usd_gauge_data.get('raw', {}).get('ceil_bp')
                         if ('usd_gauge_data' in _g and usd_gauge_data) else None),
            effr_iorb_bp=(usd_gauge_data.get('raw', {}).get('effr_iorb', {}).get('bp')
                          if ('usd_gauge_data' in _g and usd_gauge_data) else None),
            sofr_effr_bp=((usd_fp_data.get('inputs') or {}).get('spreads', {}).get('sofr_effr')
                          if ('usd_fp_data' in _g and usd_fp_data) else None),
            sofr_effr_pct=(((usd_fp_data.get('inputs') or {}).get('spread_trend') or {}).get('sofr_effr_pct_hist')
                           if ('usd_fp_data' in _g and usd_fp_data) else None),
            fp_top=((usd_fp_data.get('top')) if ('usd_fp_data' in _g and usd_fp_data) else None),
            fp_top_score=((((usd_fp_data.get('step4') or {}).get('scores') or {}).get(usd_fp_data.get('top')))
                          if ('usd_fp_data' in _g and usd_fp_data) else None),
            fp_price_confirm=(((usd_fp_data.get('step2') or {}).get('price_confirm'))
                              if ('usd_fp_data' in _g and usd_fp_data) else None),
            swap_demand=_swap_demand_data if '_swap_demand_data' in _g else None,
        )
        # 告警段插到报告标题正下方（第一章节之前）
        if alert_book.items:
            lines[3:3] = alert_book.render_md()
        print(f"  告警汇总: {alert_book.headline()}")
    except Exception as _ab_err:
        print(f"  [WARN] 告警汇总模块失败: {_ab_err}")

    if score >= 7: rating = "🟢 宽松"
    elif score >= 5: rating = "🟡 中性偏紧"
    elif score >= 3: rating = "🟠 偏紧"
    else: rating = "🔴 紧张"
    
    add(f"\n**综合评级**: {rating} | 评分 {score}/10")
    
    if risks:
        add(f"\n**⚠️ 风险点**:")
        for r in risks:
            add(f"- {r}")
    
    if positives:
        add(f"\n**✅ 积极因素**:")
        for p in positives:
            add(f"- {p}")
    
    # ---- 底部 ----
    add("\n---\n")
    add("*数据来源: data_hub/EDB（主）+ tushare + akshare + Fed H.4.1/DTS + 央行公告 + 日本财务省*")
    add("*案例分析学习材料，不构成投资建议*")
    add(f"\n*生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')} | 零LLM token*")
    
    # ---- 四子图（存单与降准降息预期利差）----
    # 放在掉期数据落库之后调用，④图能带上当天最新值
    _img_path = render_ncd_plot()
    if _img_path:
        _rel = os.path.relpath(_img_path, OUTPUT_DIR)
        _img_block = (
            f"\n![存单与降准降息预期利差四子图]({_rel})\n"
            f"\n*图：① 1Y存单AAA　② 10Y国债　③ 1Y−3M国开（降准降息预期）"
            f"　④ 1Y外汇掉期贴水（含 MA5/MA10）｜数据源 data_hub/EDB + swap_1y_history*\n"
        )
        _pos = next((i for i, _l in enumerate(lines)
                     if _l.startswith('## 三、1Y掉期点')), None)
        if _pos is None:
            add(_img_block)
        else:
            # 往前退过空行与分节符，让图落在降准板块内部末尾，而不是跑到下一节开头
            while _pos > 0 and lines[_pos - 1].strip() in ('', '---'):
                _pos -= 1
            lines.insert(_pos, _img_block)
        print(f"  [INFO] 四子图已嵌入报告: {_rel}")

    # ---- 保存 ----
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_file = os.path.join(OUTPUT_DIR, f"{date_display}_环流日报.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    # ---- 告警汇总结构化落盘 ----
    # 供 data_hub 的 a_circulation adapter 转录成带 risk_level 的指标点。
    # 不落盘的话，告警结论只存在于 md 文本里，指标仓读不到，看板也没法只看库。
    if alert_book is not None:
        try:
            _ab_json = os.path.join(OUTPUT_DIR, f"{date_display}_alerts.json")
            with open(_ab_json, 'w', encoding='utf-8') as _f:
                json.dump({
                    "date": date_display,
                    "counts": alert_book.counts(),
                    "headline": alert_book.headline(),
                    "score": score,
                    "rating": rating,
                    "items": [
                        {"level": it.level,
                         "category": it.category,
                         "title": it.title,
                         "detail": it.detail,
                         "value": it.value}
                        for it in alert_book.items
                    ],
                }, _f, ensure_ascii=False, indent=1)
            print(f"  告警汇总已落盘: {os.path.basename(_ab_json)}"
                  f"（报警 {alert_book.counts()['alert']} / 警告 {alert_book.counts()['warn']}）")
        except Exception as _ab_dump_err:
            print(f"  [WARN] 告警汇总落盘失败: {_ab_dump_err}")

    # 保存缓存
    # 每周更新prev_week_fed（取最新fed数据存入，下周对比用）
    if fed:
        last_pw_date = cache.get('prev_week_date', '')
        today_str = now.strftime('%Y%m%d')
        # 如果上次保存距今超过5天，或没有历史，更新prev_week
        if not last_pw_date or (int(today_str) - int(last_pw_date)) > 5:
            # 先把当前prev_week_fed保存为prev_week（如果有的话）
            if cache.get('prev_week_fed'):
                cache['prev_prev_week_fed'] = cache['prev_week_fed']
            cache['prev_week_fed'] = {k: v for k, v in fed.items() if k in ['reserve', 'rrp_on', 'rrp_foreign', 'rrp_total', 'tga', 'rrp']}
            cache['prev_week_date'] = today_str
    # ===== 日级 prev 值滚动（用于明天的变动检测） =====
    # 只有日期变化时才滚动prev，避免同日多次运行覆盖
    today_mark = now.strftime('%Y-%m-%d')
    if cache.get('prev_roll_date') != today_mark:
        # 滚动 USD/CNY
        if cache.get('usdcnh'):
            cache['usdcnh_prev'] = cache['usdcnh']
        # 滚动回购成交量
        if pbo and pbo.get('repo_volume'):
            cache['repo_volume_prev'] = pbo['repo_volume']
        cache['prev_roll_date'] = today_mark
    save_cache(cache)

    # ===== 告警邮件：报警数 ≥ 2 才发（当日幂等）=====
    if alert_book is not None and alert_book.should_email():
        mail_marker = f"alert_mailed_{date_display}"
        if cache.get(mail_marker):
            print(f"  ℹ️ 今日({date_display})告警邮件已发过，跳过")
        else:
            if alert_book.send_email():
                cache[mail_marker] = True
                save_cache(cache)
    elif alert_book is not None:
        _th = getattr(_ab_mod, "EMAIL_ALERT_THRESHOLD", 3)
        print(f"  ℹ️ 报警数 {len(alert_book.alerts)} < {_th}，不发邮件（警告 {len(alert_book.warns)} 项仅展示）")

    # 飞书推送（交互式卡片）—— 当日幂等，避免重复推送
    push_marker = f"feishu_pushed_{date_display}"
    if cache.get(push_marker):
        print(f"  ℹ️  今日({date_display})已推送过，跳过重复推送")
    else:
        import sys as _sys
        _sys.path.insert(0, os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话')
        from feishu_card_utils import build_circulation_daily_card
        from feishu_push import push_card_to_feishu
        
        # 收集关键指标（带周变动）
        key_metrics = {}
        if fed:
            if fed.get('reserve'):
                key_metrics['美联储准备金'] = f"${fed['reserve']/1e6:.2f}T"
            if fed.get('rrp_on') is not None:
                key_metrics['ON RRP'] = f"{fed['rrp_on']/1e3:.1f}B"
            if fed.get('tga'):
                key_metrics['TGA'] = f"${fed['tga']/1e3:.1f}B"
        if shibor:
            # Shibor 1W + 周变动
            if shibor.get('1W'):
                s1w_val = shibor['1W']
                s1w_wk = ''
                if shibor_5d is not None and len(shibor_5d) >= 6:
                    _wkv = safe_float(shibor_5d.iloc[0].get('1w'.lower()))
                    if _wkv is not None:
                        _chg = calc_bp(s1w_val, _wkv)
                        if _chg is not None:
                            s1w_wk = f" ({fmt_bp(_chg)}BP/w)"
                key_metrics['Shibor 1W'] = f"{s1w_val}%{s1w_wk}"
            # Shibor 1M + 周变动
            if shibor.get('1M'):
                s1m_val = shibor['1M']
                s1m_wk = ''
                if shibor_5d is not None and len(shibor_5d) >= 6:
                    _wkv = safe_float(shibor_5d.iloc[0].get('1m'.lower()))
                    if _wkv is not None:
                        _chg = calc_bp(s1m_val, _wkv)
                        if _chg is not None:
                            s1m_wk = f" ({fmt_bp(_chg)}BP/w)"
                key_metrics['Shibor 1M'] = f"{s1m_val}%{s1m_wk}"
        if jgb:
            if jgb.get('10Y'):
                key_metrics['JGB 10Y'] = f"{jgb['10Y']}%"
        if spread_bp is not None:
            sp_wk = ''
            if spread_week_chg is not None:
                sp_wk = f" ({fmt_bp(spread_week_chg)}BP/w)"
            key_metrics['中美利差'] = f"{spread_bp/100:.2f}%{sp_wk}"

        # 掉期点 × 需求传导（rrr = R + e）——用户重点关注项
        if '_swap_demand_data' in _g and _swap_demand_data:
            _sd = _swap_demand_data
            _dm = (_sd.get('demand') or {})
            if _sd.get('rrr') is not None:
                key_metrics['掉期点 rrr'] = f"{_sd['rrr']:.2f}% (中性5.5%)"
            if _sd.get('e') is not None:
                key_metrics['掉期点 e'] = f"{_sd['e']:.2f}% [{_sd.get('level_tag', '')}]"
            if _dm.get('verdict'):
                key_metrics['需求判定'] = _dm['verdict']
        
        # OMO数据
        omo_data = None
        if pbo and pbo.get('omo'):
            omo = pbo['omo']
            omo_data = {
                'date': omo.get('date', ''),
                'operations': omo.get('operations', []),
                'today_injection': omo.get('today_injection'),
                'today_maturity': omo.get('today_maturity'),
                'today_net': omo.get('today_net'),
                'reverse_repo_balance': omo.get('reverse_repo_balance'),
            }
        
        card = build_circulation_daily_card(
            date_display,
            score,
            rating,
            risks,
            positives,
            key_metrics=key_metrics if key_metrics else None,
            omo_data=omo_data,
            tga_data=tga_dts if tga_dts else None,
            ust_data=ust_monitor_data if 'ust_monitor_data' in dir() else None,
            bs_data=bs if bs else None,
            swap_data=swap_data if 'swap_data' in dir() else None,
            liq_data=liq_result if 'liq_result' in dir() else None,
            fp_data=usd_fp_data if 'usd_fp_data' in dir() else None,
            alerts_data=alert_book if 'alert_book' in dir() else None,
        )
        push_ok = push_card_to_feishu(card_dict=card)
        if push_ok:
            cache[push_marker] = True
            save_cache(cache)
            print(f"  ✅ 推送成功，已标记今日({date_display})已推送")
            # 卡片之后补一张四子图，聊天流里直接看，不用点开附件
            if '_img_path' in dir() and _img_path:
                from feishu_push import push_image_to_feishu
                push_image_to_feishu(_img_path)
        else:
            print(f"  ⚠️  推送失败，不写入幂等标记（下次可重试）")
    
    print(f"\n✅ 报告已保存: {output_file}")
    return output_file


if __name__ == '__main__':
    output_file = generate_report()
    if not output_file:
        print("❌ 报告生成失败")
