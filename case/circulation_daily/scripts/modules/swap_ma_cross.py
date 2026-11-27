"""1Y 外汇掉期年化率的 MA5/MA10 金叉死叉监控 + 邮件提醒（纯 Python 零 LLM）。

口径
----
- **序列**：环流日报 ``data/market.db`` 的 ``swap_1y_history.annualized_pct``
  （CFETS 在岸 1Y 掉期年化率，负值=贴水）。
- **信号口径**：与 ``plot_ncd_spread.py`` 第④图一致，统一用**贴水幅度**（绝对值）。
  图上画的就是贴水幅度，均线和交叉必须同口径，否则看图和收信对不上。
- **金叉** = MA5 上穿 MA10 → 贴水加速加深（人民币贬值压力加大 / 降息预期强化）。
- **死叉** = MA5 下穿 MA10 → 贴水收窄（人民币压力缓解 / 美元利率回落）。

去噪
----
裸交叉在日频掉期数据上噪声很大（226 个交易日里 28 次），因此加两道过滤：

1. ``CONFIRM``：交叉后需连续 N 个交易日维持同侧才算确认（滤掉单日假穿）；
2. ``MIN_GAP``：距上一次确认交叉至少 N 个交易日（滤掉震荡区来回抽）。

邮件幂等：``swap_ma_signal_log`` 表以 (cross_date, kind) 为唯一键，同一交叉只发一次。

对外接口
--------
- ``load_swap()`` / ``with_ma()`` / ``detect_crosses()``  → 供 plot 脚本复用
- ``latest_signal()``  → 最近一次确认交叉
- ``run_check(send=True)`` → 检测 + 发邮件，返回 dict
"""

from __future__ import annotations

# ---- 统一配置（DataHub config / notify；真实值在本机 config.local.json，不入库）----
def _dh_secret(_name, _default=""):
    try:
        from data_hub import get_secret as _gs
        return _gs(_name, _default)
    except Exception:
        return _default


def _dh_cfg(_key, _default=None):
    try:
        from data_hub import cfg as _cfg
        return _cfg(_key, _default)
    except Exception:
        return _default


def _dh_tushare(_default=""):
    try:
        from data_hub import get_tushare_token as _gt
        return _gt() or _default
    except Exception:
        return _default
# ---------------------------------------------------------------------------
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


import os
import sqlite3
from typing import Any, Dict, List, Optional

import pandas as pd

SWAP_DB = _cp('/Coze/Drive/蓁宝/所有对话/主对话/环流日报/data/market.db')
SWAP_DB_ALT = _cp('/opt/coze/.fuse_drive/蓁宝/所有对话/主对话/环流日报/data/market.db')

FAST, SLOW = 5, 10
CONFIRM = 2      # 交叉后需连续维持同侧的交易日数
MIN_GAP = 3      # 两次确认交叉的最小间隔（交易日）

# 邮件配置（沿用环流日报 OMO 告警的 163 通道）
SMTP_SERVER = 'smtp.163.com'
SMTP_PORT = 465
MAIL_SENDER = _dh_secret('MAIL_USER')
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

MAIL_PASSWORD = _smtp_password()
MAIL_RECEIVER = _dh_secret('MAIL_DEFAULT_TO')
MAIL_SENDER_NAME = '环流日报·掉期均线'


def _db_path() -> str:
    for p in (SWAP_DB, SWAP_DB_ALT):
        if os.path.exists(p):
            return p
    return SWAP_DB


# ------------------------------------------------------------------ 数据

def load_swap(db_path: Optional[str] = None) -> pd.DataFrame:
    """读 1Y 掉期年化率（%），返回 date / value（带符号，负=贴水）。"""
    empty = pd.DataFrame(columns=['date', 'value'])
    path = db_path or _db_path()
    if not os.path.exists(path):
        return empty
    try:
        conn = sqlite3.connect(path)
        raw = pd.read_sql_query(
            'SELECT date, annualized_pct, pips, spot FROM swap_1y_history ORDER BY date',
            conn)
        conn.close()
    except Exception:
        return empty
    if raw.empty:
        return empty
    raw['date'] = pd.to_datetime(raw['date'], errors='coerce')
    val = pd.to_numeric(raw['annualized_pct'], errors='coerce')
    pips = pd.to_numeric(raw['pips'], errors='coerce')
    spot = pd.to_numeric(raw['spot'], errors='coerce')
    back = (pips / spot) * 100.0          # 老记录只有 pips/spot，按年化率反算
    val = val.where(val.notna(), back)
    df = pd.DataFrame({'date': raw['date'], 'value': val}).dropna()
    return df.sort_values('date').reset_index(drop=True)


def with_ma(df: pd.DataFrame, fast: int = FAST, slow: int = SLOW,
            absolute: bool = True) -> pd.DataFrame:
    """算均线。默认先把序列转成贴水幅度（绝对值），与图上口径一致。"""
    out = df.copy().sort_values('date').reset_index(drop=True)
    if absolute:
        out['value'] = out['value'].abs()
    out['ma_fast'] = out['value'].rolling(fast, min_periods=fast).mean()
    out['ma_slow'] = out['value'].rolling(slow, min_periods=slow).mean()
    return out


# ------------------------------------------------------------------ 交叉

def detect_crosses(df: pd.DataFrame, confirm: int = CONFIRM,
                   min_gap: int = MIN_GAP) -> pd.DataFrame:
    """检测 MA5/MA10 确认交叉。

    Args:
        df: ``with_ma()`` 的输出（需含 ma_fast / ma_slow 列）。
        confirm: 交叉后需连续维持同侧的交易日数。
        min_gap: 两次确认交叉的最小间隔（交易日）。

    Returns:
        DataFrame[date, kind, value, ma_fast, ma_slow, confirm_date]
    """
    cols = ['date', 'kind', 'value', 'ma_fast', 'ma_slow', 'confirm_date']
    d = df.dropna(subset=['ma_fast', 'ma_slow']).reset_index(drop=True)
    if len(d) < confirm + 2:
        return pd.DataFrame(columns=cols)

    diff = (d['ma_fast'] - d['ma_slow']).tolist()
    side = [1 if x > 0 else (-1 if x < 0 else 0) for x in diff]

    out: List[Dict[str, Any]] = []
    last_idx = -10 ** 9
    for i in range(1, len(d)):
        prev, cur = side[i - 1], side[i]
        if prev == 0 or cur == 0 or prev == cur:
            continue
        # 需后续 confirm 日维持同侧
        tail = side[i:i + confirm]
        if len(tail) < confirm or any(s != cur for s in tail):
            continue
        if i - last_idx < min_gap:
            continue
        out.append({
            'date': d['date'].iloc[i],
            'kind': '金叉' if cur > 0 else '死叉',
            'value': float(d['value'].iloc[i]),
            'ma_fast': float(d['ma_fast'].iloc[i]),
            'ma_slow': float(d['ma_slow'].iloc[i]),
            'confirm_date': d['date'].iloc[i + confirm - 1],
        })
        last_idx = i
    return pd.DataFrame(out, columns=cols)


def latest_signal(df: pd.DataFrame, max_age_days: int = 10) -> Optional[Dict[str, Any]]:
    """最近一次确认交叉；若距今超过 ``max_age_days`` 个交易日则视为无新信号。"""
    cr = detect_crosses(df)
    if cr.empty:
        return None
    row = cr.iloc[-1].to_dict()
    # 用「距末尾的交易日数」判断新旧
    tail = df.dropna(subset=['ma_fast', 'ma_slow'])
    dates = list(tail['date'])
    try:
        pos = dates.index(row['confirm_date'])
    except ValueError:
        return None
    if len(dates) - 1 - pos > max_age_days:
        return None
    return row


# ------------------------------------------------------------------ 幂等

def _ensure_log(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS swap_ma_signal_log (
            cross_date TEXT NOT NULL,
            kind       TEXT NOT NULL,
            sent_at    TEXT NOT NULL,
            value      REAL,
            PRIMARY KEY (cross_date, kind)
        )
    """)
    conn.commit()


def _already_sent(conn: sqlite3.Connection, cross_date: str, kind: str) -> bool:
    cur = conn.execute(
        'SELECT 1 FROM swap_ma_signal_log WHERE cross_date=? AND kind=?',
        (cross_date, kind))
    return cur.fetchone() is not None


def _mark_sent(conn: sqlite3.Connection, cross_date: str, kind: str,
               value: Optional[float], sent_at: str) -> None:
    conn.execute(
        'INSERT OR IGNORE INTO swap_ma_signal_log '
        '(cross_date, kind, sent_at, value) VALUES (?,?,?,?)',
        (cross_date, kind, sent_at, value))
    conn.commit()


# ------------------------------------------------------------------ 邮件

def _context_lines(df: pd.DataFrame, sig: Dict[str, Any]) -> List[str]:
    """邮件正文里附上关联指标，避免只有一根均线交叉看不出背景。"""
    lines: List[str] = []
    tail = df.tail(21)
    if len(tail) >= 21:
        lines.append(f"近20个交易日贴水变化: {tail['value'].iloc[-1] - tail['value'].iloc[0]:+.2f}pct")
    _abs_v = float(df['value'].iloc[-1])
    lines.append(f"最新贴水幅度: {_abs_v:.2f}%（原始年化掉期率 {-_abs_v:.2f}%，负=贴水）")

    # 关联：降准降息预期利差 / 期限利差
    try:
        import sys
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import edb_helper as E
        sp, _ = E.point('BOND.SPREAD.CN.CDB.1Y_3M')
        rank = E.pct_rank('BOND.SPREAD.CN.CDB.1Y_3M', window=252)
        cn, _ = E.point('BOND.SPREAD.CN.CGB10Y_NCD1Y')
        ncd, _ = E.point('BOND.CN.NCD.AAA.1Y')
        if sp is not None:
            r = f"，近1年 {rank:.0f}% 分位" if rank is not None else ''
            lines.append(f"1Y−3M 国开利差: {sp:.2f}bp{r}（低位=降准降息预期强）")
        if ncd is not None:
            lines.append(f"1Y 存单 AAA: {ncd:.4f}%")
        if cn is not None:
            tag = '（转负=央行短端收紧⚠️）' if cn < 0 else ''
            lines.append(f"10Y 国债 − 1Y 存单: {cn:+.2f}bp{tag}")
    except Exception as _e:
        lines.append(f"（关联指标取数失败: {_e}）")
    return lines


def _meaning(kind: str) -> str:
    if kind == '金叉':
        return ('MA5 上穿 MA10 = 贴水加速加深。含义：人民币远期贴水走阔，'
                '通常对应降息预期强化或美元利率高企，跨境资金流出压力上升。')
    return ('MA5 下穿 MA10 = 贴水收窄。含义：人民币远期压力缓解，'
            '通常对应降息预期退潮或美元利率回落，跨境资金流出压力下降。')


def send_email(subject: str, body: str) -> bool:
    import smtplib
    from email.header import Header
    from email.mime.text import MIMEText
    from email.utils import formataddr

    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = formataddr((str(Header(MAIL_SENDER_NAME, 'utf-8')), MAIL_SENDER))
        msg['To'] = MAIL_RECEIVER
        msg['Subject'] = Header(subject, 'utf-8')
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.login(MAIL_SENDER, MAIL_PASSWORD)
        server.sendmail(MAIL_SENDER, [MAIL_RECEIVER], msg.as_string())
        server.quit()
        print(f"  ✅ 邮件已发送至 {MAIL_RECEIVER}")
        return True
    except Exception as e:
        print(f"  [WARN] 邮件发送失败: {e}")
        return False


# ------------------------------------------------------------------ 主流程

def run_check(send: bool = True, force: bool = False) -> Dict[str, Any]:
    """检测最近一次确认交叉；未发过则发邮件。"""
    out: Dict[str, Any] = {'ok': False, 'signal': None, 'sent': False,
                           'reason': '', 'date': None, 'value': None}
    df = load_swap()
    if df.empty:
        out['reason'] = 'swap_1y_history 无数据'
        return out

    ma = with_ma(df)
    out['ok'] = True
    out['date'] = ma['date'].iloc[-1].strftime('%Y-%m-%d')
    out['value'] = float(ma['value'].iloc[-1])

    sig = latest_signal(ma)
    if sig is None:
        out['reason'] = f'最近 {CONFIRM} 日内无确认交叉'
        return out
    out['signal'] = {k: (v.strftime('%Y-%m-%d') if hasattr(v, 'strftime') else v)
                     for k, v in sig.items()}

    path = _db_path()
    conn = sqlite3.connect(path)
    try:
        _ensure_log(conn)
        cross_date = sig['date'].strftime('%Y-%m-%d')
        kind = sig['kind']
        if not force and _already_sent(conn, cross_date, kind):
            out['reason'] = f'{cross_date} {kind} 已发送过，跳过'
            return out

        subject = (f"【掉期均线】1Y 掉期率 {kind}｜{cross_date} "
                   f"MA5 {sig['ma_fast']:.2f}% / MA10 {sig['ma_slow']:.2f}%")
        body_lines = [
            f"信号: MA5 {kind} MA10（贴水幅度口径）",
            f"交叉日: {cross_date}（确认日 {sig['confirm_date']:%Y-%m-%d}）",
            f"交叉时贴水幅度: {sig['value']:.2f}%",
            f"MA5 = {sig['ma_fast']:.3f}% | MA10 = {sig['ma_slow']:.3f}%",
            f"",
            f"=== 信号含义 ===",
            _meaning(kind),
            f"",
            f"=== 关联指标 ===",
            *_context_lines(ma, sig),
            f"",
            f"=== 口径说明 ===",
            f"序列: market.db swap_1y_history（CFETS 在岸 1Y 掉期年化率，原始值负=贴水）",
            f"均线与交叉统一在「贴水幅度」（绝对值）上计算，与 ④ 图同口径",
            f"去噪: 交叉后需连续 {CONFIRM} 日维持同侧确认；距上次确认 ≥ {MIN_GAP} 个交易日",
            f"",
            f"-- 环流日报 · 掉期均线自动提醒 --",
        ]
        out['sent'] = send_email(subject, '\n'.join(body_lines)) if send else False
        if out['sent'] or not send:
            if send:
                from datetime import datetime as _dt
                _mark_sent(conn, cross_date, kind, sig['value'],
                           _dt.now().strftime('%Y-%m-%d %H:%M:%S'))
            out['reason'] = f'{cross_date} {kind} 已处理'
        else:
            out['reason'] = f'{cross_date} {kind} 邮件发送失败'
    finally:
        conn.close()
    return out


if __name__ == '__main__':
    import json
    import sys as _sys
    _send = '--no-send' not in _sys.argv
    print(json.dumps(run_check(send=_send, force='--force' in _sys.argv),
                     ensure_ascii=False, indent=2, default=str))
