"""降准降息预期利差监控（1Y−3M 国开 / 存单，纯 Python 零 LLM，全部走 data_hub/EDB）。

为什么用 1Y−3M 期限利差判断降准降息预期
----------------------------------------
不看 3M OMO。判断市场降准降息预期，看「1 年国开/存单 − 3 个月国开/存单（或 1Y−6M）」
利差。原理：预期宽松时发行人多发 3M 少发 1Y、投资者减配 3M 增配 1Y，使利差收窄；
利差低位 = 降准降息预期强（2020-10-23 该利差压到 20bp 附近即判断预期很强）。

三个维度
--------
1. **位置**：1Y−3M 国开利差在近 252 个交易日中的分位 → 五档（S1 极低 ~ S5 极高）。
2. **场景**：存单（1Y AAA）与长债（10Y 国债）20 日变动方向的组合，落到提示词的
   A/B/C/D 四个场景，决定「存单上行是好是坏」。
3. **极值**：10Y 国债 − 1Y 存单压到负值极值 = 央行从短端收紧流动性。

对外接口
--------
- ``run_monitor()``       → 分析结果 dict
- ``render_report(data)`` → 带标记行的 md 字符串（供 circulation_daily 解析）

``render_report`` 输出的标记行：
``【告警】`` ``【日期】`` ``【快照】`` ``【所处阶段】`` ``【相对上日变化】``
``【利差与曲线】`` ``【场景判断】`` ``【下一关键观测位】`` ``【今日操作含义】`` ``【数据说明】``

数据来源
--------
``BOND.SPREAD.CN.CDB.1Y_3M``（主）/ ``BOND.SPREAD.CN.NCD.1Y_3M`` /
``BOND.SPREAD.CN.CDB.1Y_6M`` / ``BOND.SPREAD.CN.CGB10Y_NCD1Y`` /
``BOND.CN.NCD.AAA.1Y`` / ``BOND.CN.GOVT.10Y``

⚠️ 单位坑：四条派生利差序列的存储单位已经是 **bp**，不能再用 ``edb_helper.bp()``
（那个函数假定原单位是 %，会再乘 100）。本模块的差分一律自己算。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

# ------------------------------------------------------------------ 配置

SID = {
    'cdb_1y3m': 'BOND.SPREAD.CN.CDB.1Y_3M',    # 主指标，bp
    'cdb_1y6m': 'BOND.SPREAD.CN.CDB.1Y_6M',    # 交叉验证，bp
    'ncd_1y3m': 'BOND.SPREAD.CN.NCD.1Y_3M',    # 存单同口径，bp
    'cgb_ncd': 'BOND.SPREAD.CN.CGB10Y_NCD1Y',  # 10Y国债 − 1Y存单，bp
    'ncd_1y': 'BOND.CN.NCD.AAA.1Y',            # %
    'cgb_10y': 'BOND.CN.GOVT.10Y',             # %
}

TH = {
    # 分位档位边界（近 252 交易日）
    'tier_s1': 10.0,    # ≤10%  极低位
    'tier_s2': 30.0,    # ≤30%  低位
    'tier_s3': 70.0,    # ≤70%  中位
    'tier_s4': 90.0,    # ≤90%  高位，>90% 极高位
    # 主指标单日变动关注阈值（bp，绝对利差单位就是 bp）
    'sp_daily_watch_bp': 3.0,
    'sp_daily_alert_bp': 6.0,
    # 场景判定的方向阈值（20 日变动，bp）
    'dir_bp': 2.0,
    # 10Y−1Y存单 周压缩关注阈值（bp）
    'curve_flatten_watch_bp': 10.0,
    # 存单 20 日上行告警（bp）
    'ncd_20d_alert_bp': 15.0,
}

# 五档：分位上限 → (代号, 名称, 含义)
TIERS: List[Tuple[float, str, str, str]] = [
    (10.0, 'S1', '极低位', '降准降息预期极端强，宽松已被充分定价'),
    (30.0, 'S2', '低位', '降准降息预期强'),
    (70.0, 'S3', '中位', '预期中性'),
    (90.0, 'S4', '高位', '预期偏弱'),
    (101.0, 'S5', '极高位', '预期消退或转向'),
]

# 绝对 bp 参考（历史可比，来自《利率与存单分析提示词》）
ABS_HINTS: List[Tuple[float, str]] = [
    (5.0, '≤5bp，属极端压缩区'),
    (20.0, '≤20bp，属「预期很强」区（2020-10-23 判例 20bp）'),
    (40.0, '20~40bp，中性偏强'),
    (70.0, '40~70bp，中性'),
    (1e9, '>70bp，预期消退或转向区'),
]

SCENES = {
    'A': ('需求收缩背离',
          '10Y国债下行 + 1Y存单上行/横盘 = 需求收缩、活期存款减少，对股市偏空（沪深300承压）'),
    'B': ('宽信用同向',
          '存单上行 + 长债上行 = 实体融资需求起来（宽信用前兆），短期压估值，若真宽信用则中期利好股市'),
    'C': ('流动性宽松双降',
          '存单下行 + 长债下行 = 流动性宽松推动，贴现率分母改善，中性偏多股市'),
    'D': ('短端收紧',
          '10Y国债 − 1Y存单压至负值极值 = 央行从短端收紧流动性，债市资金流入被打断，股市承压'),
    'N': ('方向不明', '存单与长债未形成明确同向/背离组合，跟随政策利率与资金面'),
}


# ------------------------------------------------------------------ 工具

def _diff(rows: List[Dict[str, Any]], n: int) -> Optional[float]:
    """n 期差值。派生利差序列存储单位已是 bp，直接相减，不再 ×100。"""
    if len(rows) <= n:
        return None
    try:
        return float(rows[-1]['value']) - float(rows[-1 - n]['value'])
    except (KeyError, TypeError, ValueError):
        return None


def _pctl(rows: List[Dict[str, Any]], p: float) -> Optional[float]:
    """近端序列的 p 分位值（p 取 0-100）。"""
    vals = [float(r['value']) for r in rows if r.get('value') is not None]
    if len(vals) < 20:
        return None
    vals.sort()
    k = max(0, min(len(vals) - 1, int(round(p / 100.0 * (len(vals) - 1)))))
    return vals[k]


def _tier(rank: Optional[float]) -> Tuple[str, str, str]:
    if rank is None:
        return '--', '数据不足', '分位不可得'
    for bound, code, name, mean in TIERS:
        if rank <= bound:
            return code, name, mean
    return 'S5', '极高位', '预期消退或转向'


def _abs_hint(v: Optional[float]) -> str:
    if v is None:
        return ''
    for bound, txt in ABS_HINTS:
        if v <= bound:
            return txt
    return ''


def _trend(rows: List[Dict[str, Any]], n: int = 5) -> str:
    """用 n 日变动定方向（bp 阈值）。"""
    d = _diff(rows, n)
    if d is None:
        return '走平'
    if d >= TH['dir_bp']:
        return '上行'
    if d <= -TH['dir_bp']:
        return '下行'
    return '走平'


def _scene(d_ncd20: Optional[float], d_cgb20: Optional[float],
           cgb_ncd: Optional[float]) -> Tuple[str, str, str]:
    """场景判定。D（短端收紧）优先，其次看 20 日方向组合。"""
    if cgb_ncd is not None and cgb_ncd < 0:
        return 'D', *SCENES['D']
    if d_ncd20 is not None and d_cgb20 is not None:
        b = TH['dir_bp']
        if d_ncd20 >= b and d_cgb20 <= -b:
            return 'A', *SCENES['A']
        if d_ncd20 >= b and d_cgb20 >= b:
            return 'B', *SCENES['B']
        if d_ncd20 <= -b and d_cgb20 <= -b:
            return 'C', *SCENES['C']
    return 'N', *SCENES['N']


def _next_level(rows: List[Dict[str, Any]], cur: Optional[float]) -> str:
    """下一关键观测位：分位档位边界对应的 bp 值。"""
    if cur is None or not rows:
        return '数据不足'
    bits = []
    for p, label in [(10, '升破 S1→S2'), (30, '升破 S2→S3'),
                     (70, '升破 S3→S4'), (90, '升破 S4→S5')]:
        v = _pctl(rows, p)
        if v is None:
            continue
        if v > cur:
            bits.append(f"{v:.1f}bp（{label}）")
            break
    lo = _pctl(rows, 10)
    if lo is not None and lo < cur:
        bits.append(f"跌回 {lo:.1f}bp 则重返 S1 极低区")
    return ' | '.join(bits) if bits else '近1年区间边界不可得'


def _action(tier: str, trend: str, scene: str, cgb_ncd: Optional[float],
            ncd_20d: Optional[float]) -> str:
    """今日操作含义。"""
    out: List[str] = []
    if tier in ('S1', 'S2'):
        if trend == '上行':
            out.append(f"利差已从极端低位回升（{trend}），宽松定价开始修正，降准降息交易边际退潮")
        else:
            out.append("利差处极低位，降准降息预期打满——继续追多债市性价比下降，"
                       "若无降准落地，反手调整风险上升")
    elif tier == 'S3':
        out.append("利差处中位，预期中性，跟随 MLF/OMO 政策利率与资金面，不做方向性下注")
    else:
        out.append("利差处高位，宽松预期消退；若同时存单上行，是宽松交易反转信号")

    if scene == 'A':
        out.append("存单与长债背离（需求收缩），权益端偏空，降低高估值成长暴露")
    elif scene == 'B':
        out.append("宽信用前兆，短期贴现率抬升压制估值，中期若社融确认则转多")
    elif scene == 'C':
        out.append("双降格局，流动性宽松推动，股债同向受益")
    elif scene == 'D':
        out.append("短端收紧信号触发，债市资金流入被打断，股市承压，控制久期")

    if cgb_ncd is not None and cgb_ncd < 0:
        out.append(f"10Y−1Y存单已转负（{cgb_ncd:+.1f}bp），历史极值区，警惕资金面实际收紧")
    if ncd_20d is not None and ncd_20d >= TH['ncd_20d_alert_bp']:
        out.append(f"1Y存单 20 日累计上行 {ncd_20d:+.1f}bp，负债端成本抬升压制信贷投放")
    return '；'.join(out)


# ------------------------------------------------------------------ 分析

def run_monitor() -> Dict[str, Any]:
    """执行降准降息预期利差分析。"""
    data: Dict[str, Any] = {
        'ok': False, 'date': None,
        'cdb_1y3m': None, 'cdb_1y3m_rank': None, 'cdb_1y3m_chg1': None,
        'cdb_1y3m_chg20': None, 'cdb_1y3m_trend': None,
        'cdb_1y6m': None, 'ncd_1y3m': None, 'cgb_ncd': None,
        'ncd_1y': None, 'cgb_10y': None,
        'ncd_20d': None, 'cgb_20d': None,
        'tier': None, 'tier_name': None, 'tier_meaning': None,
        'abs_hint': '', 'scene': None, 'scene_name': None, 'scene_meaning': None,
        'stage': '', 'next_level': '', 'action': '',
        'alerts': [], 'warns': [], 'note': '',
    }

    # ---- 主指标 ----
    rows_main = E.rows(SID['cdb_1y3m'], limit=252)
    if not rows_main:
        data['note'] = f"{SID['cdb_1y3m']} 取数失败（data_hub/EDB）"
        data['alerts'].append('1Y−3M 国开利差数据缺失，降准降息预期无法判定')
        return data

    data['ok'] = True
    data['cdb_1y3m'] = float(rows_main[-1]['value'])
    data['date'] = rows_main[-1]['date']
    data['cdb_1y3m_rank'] = E.pct_rank(SID['cdb_1y3m'], window=252)
    data['cdb_1y3m_chg1'] = _diff(rows_main, 1)
    data['cdb_1y3m_chg20'] = _diff(rows_main, 20)
    data['cdb_1y3m_trend'] = _trend(rows_main, 5)

    # ---- 辅助序列（bp） ----
    for key in ('cdb_1y6m', 'ncd_1y3m', 'cgb_ncd'):
        v, _ = E.point(SID[key])
        data[key] = v

    # ---- 水平值（%） ----
    for key in ('ncd_1y', 'cgb_10y'):
        v, _ = E.point(SID[key])
        data[key] = v

    # ---- 20 日变动（%→bp，用 E.bp 才对） ----
    data['ncd_20d'] = E.bp(SID['ncd_1y'], 20)
    data['cgb_20d'] = E.bp(SID['cgb_10y'], 20)

    # ---- 档位 ----
    tier, tname, tmean = _tier(data['cdb_1y3m_rank'])
    data['tier'], data['tier_name'], data['tier_meaning'] = tier, tname, tmean
    data['abs_hint'] = _abs_hint(data['cdb_1y3m'])

    # ---- 场景 ----
    sc, sname, smean = _scene(data['ncd_20d'], data['cgb_20d'], data['cgb_ncd'])
    data['scene'], data['scene_name'], data['scene_meaning'] = sc, sname, smean

    # ---- 阶段 / 观测位 / 操作含义 ----
    rank_txt = f"分位 {data['cdb_1y3m_rank']:.0f}%" if data['cdb_1y3m_rank'] is not None else '分位不可得'
    data['stage'] = f"{tier} {tname}（{rank_txt}）· 方向{data['cdb_1y3m_trend']}"
    data['next_level'] = _next_level(rows_main, data['cdb_1y3m'])
    data['action'] = _action(tier, data['cdb_1y3m_trend'], sc,
                             data['cgb_ncd'], data['ncd_20d'])

    # ---- 告警 ----
    alerts: List[str] = []
    warns: List[str] = []

    if data['cdb_1y3m_rank'] is not None and data['cdb_1y3m_rank'] <= TH['tier_s1']:
        warns.append(f"1Y−3M国开处近1年 {data['cdb_1y3m_rank']:.0f}% 分位（S1 极低位），"
                     f"降准降息预期已充分定价")
    if data['cdb_1y3m_chg1'] is not None:
        if abs(data['cdb_1y3m_chg1']) >= TH['sp_daily_alert_bp']:
            alerts.append(f"1Y−3M国开单日变动 {data['cdb_1y3m_chg1']:+.1f}bp"
                          f"（≥{TH['sp_daily_alert_bp']:.0f}bp）")
        elif abs(data['cdb_1y3m_chg1']) >= TH['sp_daily_watch_bp']:
            warns.append(f"1Y−3M国开单日变动 {data['cdb_1y3m_chg1']:+.1f}bp")

    if data['cgb_ncd'] is not None and data['cgb_ncd'] < 0:
        alerts.append(f"10Y国债−1Y存单转负 {data['cgb_ncd']:+.1f}bp，"
                      f"央行短端收紧信号（2025-02 曾至 5 年新低 −14bp）")
    else:
        cgb_ncd_chg5 = _diff(E.rows(SID['cgb_ncd'], limit=30), 5)
        if cgb_ncd_chg5 is not None and cgb_ncd_chg5 <= -TH['curve_flatten_watch_bp']:
            warns.append(f"10Y−1Y存单一周压缩 {cgb_ncd_chg5:+.1f}bp，期限利差快速走平")

    if sc == 'A':
        alerts.append(f"存单与长债背离（存单20日 {data['ncd_20d']:+.1f}bp / "
                      f"长债20日 {data['cgb_20d']:+.1f}bp），需求收缩，股市偏空")
    elif sc == 'D':
        alerts.append('期限利差压至负值，短端收紧，股市承压')

    if data['ncd_20d'] is not None and data['ncd_20d'] >= TH['ncd_20d_alert_bp']:
        warns.append(f"1Y存单20日上行 {data['ncd_20d']:+.1f}bp，负债成本抬升")

    # 国开 vs 存单同口径背离
    if data['ncd_1y3m'] is not None and data['cdb_1y3m'] is not None:
        rows_ncd = E.rows(SID['ncd_1y3m'], limit=30)
        d_ncd5 = _diff(rows_ncd, 5)
        d_cdb5 = _diff(E.rows(SID['cdb_1y3m'], limit=30), 5)
        if d_ncd5 is not None and d_cdb5 is not None:
            if d_ncd5 * d_cdb5 < 0 and min(abs(d_ncd5), abs(d_cdb5)) >= TH['dir_bp']:
                warns.append(f"国开与存单同口径利差方向背离"
                             f"（国开5日 {d_cdb5:+.1f}bp / 存单5日 {d_ncd5:+.1f}bp）")

    data['alerts'] = alerts
    data['warns'] = warns

    data['note'] = ('1Y−3M/1Y−6M 国开、1Y−3M 存单、10Y国债−1Y存单利差走 data_hub/EDB 派生序列'
                    '（中债估值中心曲线直采）；分位窗口=近252个交易日；'
                    '判定框架见《利率与存单分析提示词》')
    return data


# ------------------------------------------------------------------ 渲染

def render_report(data: Dict[str, Any]) -> str:
    """生成带标记行的报告字符串，由 circulation_daily 逐行解析。"""
    d = data or {}
    lines: List[str] = []

    if not d.get('ok'):
        lines.append(f"【数据说明】{d.get('note') or '数据获取失败'}")
        return '\n'.join(lines)

    alert_txt = '；'.join(d.get('alerts') or [])
    if alert_txt:
        lines.append(f"【告警】{alert_txt}")

    lines.append(f"【日期】{d.get('date')}")

    # 快照
    snap = f"1Y−3M国开 {d['cdb_1y3m']:.2f}bp"
    if d.get('cdb_1y3m_rank') is not None:
        snap += f"，近1年分位 {d['cdb_1y3m_rank']:.0f}%（{d.get('tier')} {d.get('tier_name')}）"
    if d.get('cdb_1y6m') is not None:
        snap += f"，1Y−6M {d['cdb_1y6m']:.2f}bp"
    if d.get('ncd_1y3m') is not None:
        snap += f"，存单1Y−3M {d['ncd_1y3m']:.2f}bp"
    if d.get('abs_hint'):
        snap += f"｜绝对水平：{d['abs_hint']}"
    lines.append(f"【快照】{snap}")

    lines.append(f"【所处阶段】{d.get('stage')}｜{d.get('tier_meaning')}")

    chg_bits = []
    if d.get('cdb_1y3m_chg1') is not None:
        chg_bits.append(f"日 {d['cdb_1y3m_chg1']:+.1f}bp")
    if d.get('cdb_1y3m_chg20') is not None:
        chg_bits.append(f"月 {d['cdb_1y3m_chg20']:+.1f}bp")
    chg_bits.append(f"方向{d.get('cdb_1y3m_trend')}")
    if d.get('ncd_20d') is not None:
        chg_bits.append(f"1Y存单20日 {d['ncd_20d']:+.1f}bp")
    if d.get('cgb_20d') is not None:
        chg_bits.append(f"10Y国债20日 {d['cgb_20d']:+.1f}bp")
    lines.append(f"【相对上日变化】{' | '.join(chg_bits)}")

    # 利差与曲线
    sp_bits = []
    if d.get('ncd_1y') is not None:
        sp_bits.append(f"1Y存单AAA {d['ncd_1y']:.4f}%")
    if d.get('cgb_10y') is not None:
        sp_bits.append(f"10Y国债 {d['cgb_10y']:.4f}%")
    if d.get('cgb_ncd') is not None:
        tag = '（转负⚠️）' if d['cgb_ncd'] < 0 else ''
        sp_bits.append(f"10Y国债−1Y存单 {d['cgb_ncd']:+.2f}bp{tag}")
    if sp_bits:
        lines.append(f"【利差与曲线】{' | '.join(sp_bits)}")

    lines.append(f"【场景判断】{d.get('scene')} {d.get('scene_name')}：{d.get('scene_meaning')}")
    lines.append(f"【下一关键观测位】{d.get('next_level')}")
    lines.append(f"【今日操作含义】{d.get('action')}")

    note = d.get('note') or ''
    warns = d.get('warns') or []
    if warns:
        note += f"；关注项: {'；'.join(warns)}"
    lines.append(f"【数据说明】{note}")
    return '\n'.join(lines)


if __name__ == '__main__':
    import json
    _d = run_monitor()
    print(render_report(_d))
    print('\n--- raw ---')
    print(json.dumps({k: v for k, v in _d.items()}, ensure_ascii=False, indent=2, default=str))
