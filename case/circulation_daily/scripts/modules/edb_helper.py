"""环流日报内部模块共用的 data_hub/EDB 统一取数封装。

设计原则
--------
1. **唯一取数入口**：所有子模块只通过本文件访问 data_hub/EDB，禁止各自 requests 直连外部源。
2. **失败即返回空**：取不到数据返回 None / []，由调用方决定降级展示，绝不抛异常打断日报。
3. **零 LLM**：纯 Python 计算，不消耗 token。

对外函数
--------
- ``rows(sid, limit=None)``        按日期升序的 ``[{'date','value'}, ...]``
- ``values(sid, limit=None)``      纯值列表
- ``point(sid)``                   最新值 ``(value, date)``
- ``prev(sid)``                    倒数第二个值
- ``chg(sid, n=1)``                绝对变动（最新 - n 个位置前）
- ``bp(sid, n=1)``                 变动，单位 BP（值本身是百分数时 ×100）
- ``pct(sid, n=1)``                变动，单位 %
- ``pct_rank(sid, window=252)``    近 window 个样本内的历史分位 0-100
- ``stat(sid, window=252)``        完整统计字典
- ``meta(sid)``                    序列元信息（名称/单位/来源）
- ``safe(fn, default=None)``       包裹任意取数，异常返回 default
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# data_hub 主根在不同会话里的挂载点不一样：有的会话挂在 /Coze/Drive/<卷名>，
# 有的只在 /opt/coze/.fuse_drive/<卷名> 下可见。逐个探测，取第一个真实存在的。
EDB_ROOT_CANDIDATES = [
    '/Coze/Drive/外汇掉期点',
    '/opt/coze/.fuse_drive/外汇掉期点',
]
EDB_ROOT = next((r for r in EDB_ROOT_CANDIDATES if os.path.isdir(os.path.join(r, 'data_hub'))),
                EDB_ROOT_CANDIDATES[0])

EDB_AVAILABLE = False
_EDB = None

try:
    if EDB_ROOT not in sys.path:
        sys.path.insert(0, EDB_ROOT)
    from data_hub import edb as _EDB  # noqa: F401
    EDB_AVAILABLE = True
except Exception as _err:  # pragma: no cover
    print(f"[WARN] edb_helper: data_hub EDB 导入失败: {_err} (root={EDB_ROOT})")


# ---------------------------------------------------------------- 进程内缓存
# data_hub 的 get_series_result 单次约 3-5 秒（内部走 SQLite 缓存 + 元数据装配）。
# 一个模块会重复问同一条序列十几次，必须做进程内 memo，否则单模块要跑几十秒。
# 缓存只在本次进程有效；跨进程持久化由 data_hub 自己的 SQLite 层负责。

_RESULT_MEMO: Dict[str, Any] = {}
_ROWS_MEMO: Dict[str, List[Dict[str, Any]]] = {}


def clear_cache(sid: Optional[str] = None) -> None:
    """清空进程内缓存；``sid`` 为空则全清。"""
    if sid is None:
        _RESULT_MEMO.clear()
        _ROWS_MEMO.clear()
    else:
        _RESULT_MEMO.pop(sid, None)
        _ROWS_MEMO.pop(sid, None)


def _result(sid: str) -> Optional[Dict[str, Any]]:
    """``get_series_result`` 的唯一入口，带进程内 memo。"""
    if not EDB_AVAILABLE:
        return None
    if sid in _RESULT_MEMO:
        return _RESULT_MEMO[sid]
    try:
        r = _EDB.get_series_result(sid)
    except Exception:
        r = None
    _RESULT_MEMO[sid] = r
    return r


# ---------------------------------------------------------------- 基础取数

def rows(sid: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """取序列，返回按日期升序的 ``[{'date': 'YYYY-MM-DD', 'value': float}, ...]``。"""
    if not EDB_AVAILABLE:
        return []
    full = _ROWS_MEMO.get(sid)
    if full is None:
        full = []
        r = _result(sid)
        if isinstance(r, dict) and r.get('ok'):
            df = r.get('data')
            if df is not None and len(df) > 0:
                try:
                    dates = list(df['date'])
                    vals = list(df['value'])
                except Exception:
                    dates, vals = [], []
                for d, v in zip(dates, vals):
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if fv != fv:  # NaN
                        continue
                    full.append({'date': str(d)[:10], 'value': fv})
        _ROWS_MEMO[sid] = full
    return full[-limit:] if limit else list(full)


def values(sid: str, limit: Optional[int] = None) -> List[float]:
    """取纯值列表（按日期升序，最新在最后）。"""
    return [x['value'] for x in rows(sid, limit=limit)]


def point(sid: str) -> Tuple[Optional[float], Optional[str]]:
    """取最新值，返回 ``(value, date)``；取不到返回 ``(None, None)``。"""
    if not EDB_AVAILABLE:
        return None, None
    r = _result(sid)
    if not isinstance(r, dict) or not r.get('ok'):
        return None, None
    val = r.get('latest_value')
    if val is None:
        return None, None
    try:
        val = float(val)
    except (TypeError, ValueError):
        return None, None
    return val, str(r.get('latest_date') or '')[:10]


def prev(sid: str, n: int = 1) -> Optional[float]:
    """取倒数第 ``n+1`` 个值（n=1 即上一期）。"""
    vals = values(sid, limit=n + 2)
    if len(vals) >= n + 1:
        return vals[-(n + 1)]
    return None


def chg(sid: str, n: int = 1) -> Optional[float]:
    """绝对变动 = 最新值 - n 期前值。"""
    vals = values(sid, limit=n + 2)
    if len(vals) >= n + 1:
        return vals[-1] - vals[-(n + 1)]
    return None


def bp(sid: str, n: int = 1) -> Optional[float]:
    """BP 变动 = 绝对变动 × 100（序列单位为百分数时成立）。"""
    c = chg(sid, n=n)
    return None if c is None else c * 100.0


def pct(sid: str, n: int = 1) -> Optional[float]:
    """百分比变动 = (最新 / n 期前 - 1) × 100。"""
    vals = values(sid, limit=n + 2)
    if len(vals) >= n + 1 and vals[-(n + 1)] not in (0, None):
        return (vals[-1] / vals[-(n + 1)] - 1.0) * 100.0
    return None


def pct_rank(sid: str, window: int = 252) -> Optional[float]:
    """近 ``window`` 个样本内的历史分位（0-100，越大越高）。"""
    vals = values(sid, limit=window)
    if len(vals) < 5:
        return None
    cur = vals[-1]
    below = sum(1 for v in vals if v < cur)
    equal = sum(1 for v in vals if v == cur)
    return (below + 0.5 * equal) / len(vals) * 100.0


def stat(sid: str, window: int = 252) -> Dict[str, Any]:
    """完整统计：最新值 / 各期变动 / 分位 / 均值 / 标准差 / z 值 / 区间。"""
    vals = values(sid, limit=window)
    out: Dict[str, Any] = {
        'sid': sid, 'ok': False, 'n': len(vals),
        'latest': None, 'date': None,
        'chg_1d': None, 'chg_5d': None, 'chg_20d': None,
        'bp_1d': None, 'bp_5d': None, 'bp_20d': None,
        'rank': None, 'mean': None, 'std': None, 'z': None,
        'hi': None, 'lo': None,
    }
    if not vals:
        return out
    out['ok'] = True
    out['latest'] = vals[-1]
    rs = rows(sid, limit=2)
    out['date'] = rs[-1]['date'] if rs else None
    out['chg_1d'] = chg(sid, 1)
    out['chg_5d'] = chg(sid, 5)
    out['chg_20d'] = chg(sid, 20)
    out['bp_1d'] = bp(sid, 1)
    out['bp_5d'] = bp(sid, 5)
    out['bp_20d'] = bp(sid, 20)
    out['rank'] = pct_rank(sid, window=window)
    out['mean'] = sum(vals) / len(vals)
    if len(vals) > 1:
        var = sum((v - out['mean']) ** 2 for v in vals) / (len(vals) - 1)
        out['std'] = var ** 0.5
        out['z'] = ((vals[-1] - out['mean']) / out['std']) if out['std'] else None
    out['hi'] = max(vals)
    out['lo'] = min(vals)
    return out


def meta(sid: str) -> Dict[str, Any]:
    """序列元信息：名称 / 分类 / 单位 / 来源链。"""
    out = {'sid': sid, 'name': sid, 'category': '', 'unit': '', 'sources': []}
    if not EDB_AVAILABLE:
        return out
    try:
        reg = getattr(_EDB, 'REGISTRY', {})
        d = reg.get(sid)
        if d is not None:
            out['name'] = getattr(d, 'name', sid) or sid
            out['category'] = getattr(d, 'category', '') or ''
            out['unit'] = getattr(d, 'unit', '') or ''
            srcs = getattr(d, 'sources', None) or []
            out['sources'] = list(srcs)
    except Exception:
        pass
    return out


def source_of(sid: str) -> str:
    """取该序列实际命中的数据源（如 ``fred:WTREGEN``），失败返回空串。"""
    if not EDB_AVAILABLE:
        return ''
    r = _result(sid)
    if isinstance(r, dict):
        return str(r.get('source') or '')
    return ''


def is_stale(sid: str) -> Optional[bool]:
    """该序列是否被 EDB 判为过期。"""
    if not EDB_AVAILABLE:
        return None
    r = _result(sid)
    if isinstance(r, dict):
        return bool(r.get('is_stale'))
    return None


def safe(fn, default=None):
    """包裹任意取数调用，异常一律返回 default。"""
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- 展示工具

def fmt(v: Optional[float], nd: int = 2, suffix: str = '') -> str:
    """数值格式化，None 显示 ``n/a``。"""
    if v is None:
        return 'n/a'
    try:
        return f"{v:.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return 'n/a'


def fmt_bp(v: Optional[float], nd: int = 1) -> str:
    """BP 变动带符号格式化，如 ``+3.2bp``。"""
    if v is None:
        return 'n/a'
    return f"{v:+.{nd}f}bp"


def fmt_pct(v: Optional[float], nd: int = 2) -> str:
    """百分比带符号格式化，如 ``+1.23%``。"""
    if v is None:
        return 'n/a'
    return f"{v:+.{nd}f}%"


def arrow(v: Optional[float], dead: float = 0.05) -> str:
    """方向箭头：↑ / ↓ / →。"""
    if v is None:
        return '·'
    if v > dead:
        return '↑'
    if v < -dead:
        return '↓'
    return '→'


def rank_word(r: Optional[float]) -> str:
    """分位的中文描述。"""
    if r is None:
        return '分位未知'
    if r >= 90:
        return '极高'
    if r >= 75:
        return '偏高'
    if r >= 50:
        return '中性偏高'
    if r >= 25:
        return '中性偏低'
    if r >= 10:
        return '偏低'
    return '极低'


def tail_date(sid: str) -> str:
    """序列最新日期。"""
    rs = rows(sid, limit=1)
    return rs[-1]['date'] if rs else ''


def build_series_note(sids: List[str]) -> str:
    """生成数据来源脚注，列出每个序列的命中源。"""
    parts = []
    for sid in sids:
        src = source_of(sid)
        if src:
            parts.append(f"{sid}={src}")
    if not parts:
        return ''
    return 'data_hub/EDB: ' + ', '.join(parts)
