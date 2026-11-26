"""1Y AAA 同业存单流动性监控（纯 Python，零 LLM，全部走 data_hub/EDB）。

为什么盯同业存单
----------------
同业存单是银行**主动负债**的核心工具。1Y 存单利率上行意味着：

1. 银行负债端成本抬升 → 净息差承压 → 信贷投放意愿下降；
2. 银行缺中长期负债 → 需要靠提价发行才能募集 → 往往伴随资金面结构性收紧；
3. 存单利率 vs 资金利率（DR007）的背离，能区分「资金面紧」还是「银行负债结构问题」。

对外接口
--------
- ``run_monitor()``      → 分析结果 dict
- ``render_report(data)``→ 带标记行的 md 字符串（供 circulation_daily 解析）

``render_report`` 输出的标记行（circulation_daily 逐行解析）：
``【告警】`` ``【日期】`` ``【快照】`` ``【所处阶段】`` ``【相对上日变化】``
``【下一关键观测位】`` ``【今日操作含义】`` ``【数据说明】``

数据来源
--------
``LIQ.CN.CD.1Y``（核心）/ ``LIQ.CN.SHIBOR.*`` 曲线 / ``LIQ.CN.DR001`` / ``LIQ.CN.DR007`` /
``LIQ.CN.LPR.1Y`` / ``LIQ.CN.OMO.NET``
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

TH = {
    # 单日变动告警阈值（bp）
    'daily_jump_bp': 10.0,
    'daily_drop_bp': -10.0,
    # 周变动关注阈值（bp）
    'week_watch_bp': 15.0,
    # 分位极值
    'rank_high': 85.0,
    'rank_low': 15.0,
    # 净息差倒挂（CD1Y - LPR1Y > 0 即倒挂）
    'nimp_invert': 0.0,
    # 存单-资金利差关注阈值（bp）
    'cd_dr_warn_bp': 80.0,
}

CD_SID = 'LIQ.CN.CD.1Y'


# ------------------------------------------------------------------ 分析

def _ma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    seg = vals[-n:]
    return sum(seg) / len(seg)


def _stage(rank: Optional[float], ma20: Optional[float], ma60: Optional[float], cur: Optional[float]) -> str:
    """所处阶段：位置（分位）+ 方向（均线）二维定位。"""
    if cur is None:
        return '数据不足'
    trend = '走平'
    if ma20 is not None and ma60 is not None:
        if ma20 > ma60 + 0.01:
            trend = '上行'
        elif ma20 < ma60 - 0.01:
            trend = '下行'
    else:
        v = E.values(CD_SID, limit=25)
        if len(v) >= 21:
            trend = '上行' if v[-1] > v[-21] else '下行'

    if rank is None:
        return f'方向{trend}（分位不可得）'
    if rank >= 70:
        return f'高位{trend}（负债端承压）' if trend == '上行' else f'高位回落'
    if rank <= 30:
        return f'低位筑底回升' if trend == '上行' else f'低位下行（负债成本改善）'
    return f'中枢震荡（{trend}）'


def _next_level(cur: Optional[float], hi: Optional[float], lo: Optional[float]) -> str:
    """下一关键观测位：整数关口 + 前高前低。"""
    if cur is None:
        return '数据不足'
    levels = []
    # 最近的 0.05% 整数关口（上下各一档）
    step = 0.05
    up = (int(cur / step) + 1) * step
    dn = int(cur / step) * step
    if abs(up - cur) < 0.001:
        up += step
    levels.append(f"上方 {up:.2f}%（+{(up - cur) * 100:.1f}bp）")
    levels.append(f"下方 {dn:.2f}%（{(dn - cur) * 100:+.1f}bp）")
    if hi is not None and hi > cur:
        levels.append(f"近1年前高 {hi:.2f}%")
    if lo is not None and lo < cur:
        levels.append(f"近1年低点 {lo:.2f}%")
    return ' | '.join(levels)


def _action(rank: Optional[float], chg_bp: Optional[float], nimp_bp: Optional[float],
            cd_dr_bp: Optional[float], stage: str) -> str:
    """今日操作含义。"""
    out = []
    if chg_bp is not None and chg_bp >= TH['daily_jump_bp']:
        out.append(f"存单单日跳升 {chg_bp:+.1f}bp，银行负债端骤然收紧，警惕资金面传导")
    elif chg_bp is not None and chg_bp <= TH['daily_drop_bp']:
        out.append(f"存单单日大幅下行 {chg_bp:+.1f}bp，负债压力明显缓解")
    if nimp_bp is not None and nimp_bp > TH['nimp_invert']:
        out.append(f"存单成本高于 1Y LPR（{nimp_bp:+.1f}bp），银行净息差倒挂，压制信贷投放")
    if cd_dr_bp is not None and cd_dr_bp >= TH['cd_dr_warn_bp']:
        out.append(f"存单-资金利差 {cd_dr_bp:.0f}bp 偏阔，中长期负债贵于短期资金，曲线陡")
    if rank is not None and rank >= TH['rank_high']:
        out.append("存单利率处近1年高位，负债成本是当前银行体系主要矛盾")
    elif rank is not None and rank <= TH['rank_low']:
        out.append("存单利率处近1年低位，银行负债成本对债市友好")
    if not out:
        out.append(f"{stage}，无异常信号，维持既有判断")
    return '；'.join(out)


def run_monitor() -> Dict[str, Any]:
    """执行 1Y 同业存单流动性分析。"""
    data: Dict[str, Any] = {
        'ok': False, 'date': None,
        'cd_1y': None, 'chg_1d_bp': None, 'chg_5d_bp': None, 'chg_20d_bp': None,
        'rank_1y': None, 'hi_1y': None, 'lo_1y': None,
        'ma20': None, 'ma60': None,
        'shibor_curve': {},
        'spreads': {},
        'stage': '', 'next_level': '', 'action': '',
        'alerts': [], 'warns': [], 'note': '',
    }

    st = E.stat(CD_SID, window=252)
    if not st.get('ok'):
        data['note'] = f'{CD_SID} 取数失败（data_hub/EDB）'
        data['alerts'].append('1Y 同业存单利率数据缺失')
        return data

    data['ok'] = True
    data['cd_1y'] = st['latest']
    data['date'] = st['date']
    data['chg_1d_bp'] = st['bp_1d']
    data['chg_5d_bp'] = st['bp_5d']
    data['chg_20d_bp'] = st['bp_20d']
    data['rank_1y'] = st['rank']
    data['hi_1y'] = st['hi']
    data['lo_1y'] = st['lo']

    vals = E.values(CD_SID, limit=120)
    data['ma20'] = _ma(vals, 20)
    data['ma60'] = _ma(vals, 60)

    # ---- SHIBOR 期限结构 ----
    curve = {}
    for tag, sid in [('ON', 'LIQ.CN.SHIBOR.ON'), ('1W', 'LIQ.CN.SHIBOR.1W'),
                     ('1M', 'LIQ.CN.SHIBOR.1M'), ('3M', 'LIQ.CN.SHIBOR.3M'),
                     ('6M', 'LIQ.CN.SHIBOR.6M'), ('9M', 'LIQ.CN.SHIBOR.9M'),
                     ('1Y', 'LIQ.CN.SHIBOR.1Y')]:
        v, _ = E.point(sid)
        if v is not None:
            curve[tag] = v
    data['shibor_curve'] = curve

    # ---- 利差 ----
    sp: Dict[str, Any] = {}
    sh1y = curve.get('1Y')
    if sh1y is not None and data['cd_1y'] is not None:
        sp['cd_shibor1y_bp'] = (data['cd_1y'] - sh1y) * 100.0
    dr007, _ = E.point('LIQ.CN.DR007')
    if dr007 is not None and data['cd_1y'] is not None:
        sp['cd_dr007_bp'] = (data['cd_1y'] - dr007) * 100.0
    lpr1y, _ = E.point('LIQ.CN.LPR.1Y')
    if lpr1y is not None and data['cd_1y'] is not None:
        sp['cd_lpr1y_bp'] = (data['cd_1y'] - lpr1y) * 100.0
    omo_net, _ = E.point('LIQ.CN.OMO.NET')
    if omo_net is not None:
        sp['omo_net'] = omo_net
    dr001, _ = E.point('LIQ.CN.DR001')
    if dr001 is not None:
        sp['dr001'] = dr001
    if dr007 is not None:
        sp['dr007'] = dr007
    if lpr1y is not None:
        sp['lpr1y'] = lpr1y
    data['spreads'] = sp

    # ---- 阶段 / 观测位 / 操作含义 ----
    stage = _stage(data['rank_1y'], data['ma20'], data['ma60'], data['cd_1y'])
    data['stage'] = stage
    data['next_level'] = _next_level(data['cd_1y'], data['hi_1y'], data['lo_1y'])
    data['action'] = _action(data['rank_1y'], data['chg_1d_bp'],
                             sp.get('cd_lpr1y_bp'), sp.get('cd_dr007_bp'), stage)

    # ---- 告警 ----
    alerts, warns = [], []
    if data['chg_1d_bp'] is not None:
        if data['chg_1d_bp'] >= TH['daily_jump_bp']:
            alerts.append(f"1Y存单单日跳升 {data['chg_1d_bp']:+.1f}bp（≥{TH['daily_jump_bp']:.0f}bp）")
        elif data['chg_1d_bp'] <= TH['daily_drop_bp']:
            alerts.append(f"1Y存单单日大幅下行 {data['chg_1d_bp']:+.1f}bp")
        elif abs(data['chg_1d_bp']) >= TH['week_watch_bp'] / 3:
            warns.append(f"1Y存单日变动 {data['chg_1d_bp']:+.1f}bp")
    if data['chg_5d_bp'] is not None and abs(data['chg_5d_bp']) >= TH['week_watch_bp']:
        warns.append(f"1Y存单周变动 {data['chg_5d_bp']:+.1f}bp")
    if data['rank_1y'] is not None:
        if data['rank_1y'] >= TH['rank_high']:
            warns.append(f"1Y存单处近1年 {data['rank_1y']:.0f}% 分位（高位）")
        elif data['rank_1y'] <= TH['rank_low']:
            warns.append(f"1Y存单处近1年 {data['rank_1y']:.0f}% 分位（低位）")
    nimp = sp.get('cd_lpr1y_bp')
    if nimp is not None and nimp > TH['nimp_invert']:
        alerts.append(f"存单成本高于 1Y LPR {nimp:+.1f}bp，银行净息差倒挂")
    # 曲线倒挂
    if curve.get('ON') is not None and curve.get('1Y') is not None and curve['ON'] > curve['1Y']:
        warns.append(f"SHIBOR 曲线倒挂（ON {curve['ON']:.3f}% > 1Y {curve['1Y']:.3f}%）")
    data['alerts'] = alerts
    data['warns'] = warns

    data['note'] = ('1Y AAA 同业存单到期收益率走 data_hub/EDB(LIQ.CN.CD.1Y)；'
                    '利差=存单利率与对应基准之差（bp）；分位窗口=近252个交易日')
    return data


# ------------------------------------------------------------------ 渲染

def render_report(data: Dict[str, Any]) -> str:
    """生成带标记行的报告字符串，由 circulation_daily 逐行解析。"""
    d = data or {}
    lines: List[str] = []

    if not d.get('ok'):
        lines.append(f"【数据说明】{d.get('note') or '数据获取失败'}")
        return '\n'.join(lines)

    # 告警行（只输出一行，多条用「；」合并）
    alert_txt = '；'.join(d.get('alerts') or [])
    if alert_txt:
        lines.append(f"【告警】{alert_txt}")

    lines.append(f"【日期】{d.get('date')}")

    snap = f"{d.get('cd_1y'):.4f}%"
    if d.get('chg_1d_bp') is not None:
        snap += f"（日 {E.fmt_bp(d['chg_1d_bp'])}）"
    if d.get('rank_1y') is not None:
        snap += f"，近1年分位 {d['rank_1y']:.0f}%（{E.rank_word(d['rank_1y'])}）"
    snap += f"，区间 {d.get('lo_1y'):.2f}%~{d.get('hi_1y'):.2f}%"
    lines.append(f"【快照】{snap}")

    lines.append(f"【所处阶段】{d.get('stage')}")

    chg_bits = []
    if d.get('chg_1d_bp') is not None:
        chg_bits.append(f"日 {E.fmt_bp(d['chg_1d_bp'])}")
    if d.get('chg_5d_bp') is not None:
        chg_bits.append(f"周 {E.fmt_bp(d['chg_5d_bp'])}")
    if d.get('chg_20d_bp') is not None:
        chg_bits.append(f"月 {E.fmt_bp(d['chg_20d_bp'])}")
    if d.get('ma20') is not None and d.get('ma60') is not None:
        chg_bits.append(f"MA20 {d['ma20']:.3f}% / MA60 {d['ma60']:.3f}%")
    if chg_bits:
        lines.append(f"【相对上日变化】{' | '.join(chg_bits)}")

    # 利差表
    sp = d.get('spreads') or {}
    sp_bits = []
    if sp.get('cd_shibor1y_bp') is not None:
        sp_bits.append(f"存单-1Y拆借 {sp['cd_shibor1y_bp']:+.1f}bp")
    if sp.get('cd_dr007_bp') is not None:
        sp_bits.append(f"存单-DR007 {sp['cd_dr007_bp']:+.1f}bp")
    if sp.get('cd_lpr1y_bp') is not None:
        tag = '（倒挂⚠️）' if sp['cd_lpr1y_bp'] > TH['nimp_invert'] else ''
        sp_bits.append(f"存单-1Y LPR {sp['cd_lpr1y_bp']:+.1f}bp{tag}")
    curve = d.get('shibor_curve') or {}
    if curve:
        sp_bits.append('SHIBOR ' + ' / '.join(f"{k} {v:.3f}%" for k, v in curve.items()))
    if sp_bits:
        lines.append(f"【利差与曲线】{' | '.join(sp_bits)}")

    lines.append(f"【下一关键观测位】{d.get('next_level')}")
    lines.append(f"【今日操作含义】{d.get('action')}")

    warns = d.get('warns') or []
    note = d.get('note') or ''
    if warns:
        note += f"；关注项: {'；'.join(warns)}"
    lines.append(f"【数据说明】{note}")
    return '\n'.join(lines)
