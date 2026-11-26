"""央行资金面判定：技术性扰动 vs 真收紧（纯 Python，零 LLM）。

核心思想
--------
DR001 单日上行 **不等于** 收紧。必须区分三种性质完全不同的情形：

- **技术性扰动**：跨月/跨季/缴税/缴准等时点因素，DR001 偏离政策利率但**分层（R001-DR001）不明显走阔**，
  且央行通过大额隔夜逆回购主动呵护 → 属短期扰动，不构成趋势。
- **结构性收紧**：DR001 本身不紧，但**非银融资溢价（分层）持续走阔** → 银行体系不缺钱，
  但流动性传导到非银的环节出了问题（大行净融出收缩）。
- **真收紧**：DR001 偏离 + 分层走阔 + 净投放转负 **三共振**，且持续 2-3 日以上。

对外接口
--------
- ``run(omo_data=None)``            → 判定结果 dict
- ``render_md_block(result)``       → list[str]，插入日报 markdown
- ``render_card_lines(result)``     → list[str]，插入飞书卡片

数据来源
--------
全部走 ``data_hub/EDB``：
``LIQ.CN.DR001`` / ``LIQ.CN.R001`` / ``LIQ.CN.DR007`` / ``LIQ.CN.OMO.NET`` /
``LIQ.CN.OMO.INJECT`` / ``LIQ.CN.OMO.MATURITY`` / ``LIQ.CN.OMO.BALANCE``

政策利率（7 天逆回购中标利率）优先从 ``omo_data['operations']`` 的 term=7 取，
取不到回落到 ``TH['policy_rate']``（1.40%，2025-05 起）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

# ------------------------------------------------------------------ 阈值

TH = {
    # 政策利率（7 天逆回购中标利率）兜底值；优先从 OMO 公告的 term=7 取
    'policy_rate': 1.40,
    # DR001 相对政策利率的偏离（bp）
    'dev_warn_bp': 15.0,
    'dev_alert_bp': 25.0,
    # 分层（R001 - DR001，bp）
    'tier_warn_bp': 15.0,
    'tier_alert_bp': 30.0,
    # 隔夜逆回购「大额呵护」阈值（亿元）
    'overnight_heavy': 5000.0,
    # 隔夜逆回购「缩量」判定比例
    'overnight_shrink': 0.60,
    # 连续上行天数（真收紧需 ≥ 该值）
    'consec_days': 2,
    # 净投放「约等于 0」的容差（亿元）
    'net_flat': 500.0,
}

# 隔夜逆回购工具 2024-07 才启用，在此之前 term=0 不代表隔夜工具
OVERNIGHT_TOOL_START = '2024-07-01'


# ------------------------------------------------------------------ 取数

def _pick_policy_rate(omo_data: Optional[Dict[str, Any]]) -> tuple:
    """从 OMO 公告里取 7 天逆回购中标利率，返回 ``(rate, source)``。"""
    if omo_data and isinstance(omo_data.get('operations'), list):
        for op in omo_data['operations']:
            try:
                if int(op.get('term') or 0) == 7 and op.get('rate'):
                    return float(op['rate']), 'OMO公告(7天逆回购中标利率)'
            except (TypeError, ValueError):
                continue
    return TH['policy_rate'], '配置兜底(1.40%)'


def _overnight_amount(omo_data: Optional[Dict[str, Any]], omo_date: Optional[str]) -> Optional[float]:
    """从 OMO 公告里取当日隔夜逆回购投放量（亿元）。"""
    if not omo_data or not isinstance(omo_data.get('operations'), list):
        return None
    total = 0.0
    hit = False
    for op in omo_data['operations']:
        try:
            if int(op.get('term') or -1) == 0:
                d = str(op.get('date') or '')[:10]
                # 只累计当日（公告里的 operations 可能跨日）
                if omo_date and d and d != omo_date:
                    continue
                total += float(op.get('amount') or 0)
                hit = True
        except (TypeError, ValueError):
            continue
    return total if hit else None


def _consec_up(sid: str, max_days: int = 6) -> int:
    """统计末端连续上行天数。"""
    vals = E.values(sid, limit=max_days + 1)
    if len(vals) < 2:
        return 0
    n = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1]:
            n += 1
        else:
            break
    return n


def _tier_bp(dr_sid: str, r_sid: str) -> Optional[float]:
    """分层利差（R - DR，bp）。必须同日同源对齐。"""
    dr_rows = E.rows(dr_sid, limit=5)
    r_rows = E.rows(r_sid, limit=5)
    if not dr_rows or not r_rows:
        return None
    dr_map = {x['date']: x['value'] for x in dr_rows}
    for x in reversed(r_rows):
        if x['date'] in dr_map:
            return (x['value'] - dr_map[x['date']]) * 100.0
    return None


def _tier_bp_prev(dr_sid: str, r_sid: str) -> Optional[float]:
    """上一交易日的分层利差（bp），用于判断是否走阔。"""
    dr_rows = E.rows(dr_sid, limit=8)
    r_rows = E.rows(r_sid, limit=8)
    if len(dr_rows) < 2 or len(r_rows) < 2:
        return None
    dr_map = {x['date']: x['value'] for x in dr_rows}
    pairs = []
    for x in r_rows:
        if x['date'] in dr_map:
            pairs.append((x['date'], (x['value'] - dr_map[x['date']]) * 100.0))
    if len(pairs) >= 2:
        return pairs[-2][1]
    return None


# ------------------------------------------------------------------ 主流程

def run(omo_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """执行资金面判定。

    Args:
        omo_data: ``circulation_daily.fetch_pbo_lending_deep()['omo']``，
            含 ``operations``（每项 ``date``/``term``/``amount``/``rate``）与
            ``today_net``/``today_injection``/``today_maturity``。

    Returns:
        dict，字段见模块 docstring。
    """
    res: Dict[str, Any] = {
        'ok': False,
        'date': None,
        'policy_rate': None, 'policy_rate_src': '',
        'dr001': None, 'dr001_date': None, 'dev_bp': None,
        'r001': None, 'tier_bp': None, 'tier_bp_prev': None, 'tier_widen_bp': None,
        'dr007': None, 'dr007_dev_bp': None,
        'omo': {'net': None, 'inject': None, 'maturity': None, 'balance': None,
                'overnight_amt': None, 'overnight_share': None},
        'consec_up_days': 0,
        'cb_stance': '未知',
        'label': '数据不足',
        'score': 50.0,
        'reasons': [], 'alerts': [], 'warns': [],
        'data_freshness': '',
        'note': '',
    }

    # ---- 1. 利率 ----
    pol, pol_src = _pick_policy_rate(omo_data)
    res['policy_rate'] = pol
    res['policy_rate_src'] = pol_src

    dr001, dr001_date = E.point('LIQ.CN.DR001')
    r001, _ = E.point('LIQ.CN.R001')
    dr007, _ = E.point('LIQ.CN.DR007')
    if dr001 is None:
        res['note'] = 'DR001 取数失败（data_hub/EDB）'
        res['warns'].append('DR001 数据缺失，资金面判定跳过')
        return res

    res['ok'] = True
    res['dr001'] = dr001
    res['dr001_date'] = dr001_date
    res['date'] = dr001_date
    res['r001'] = r001
    res['dr007'] = dr007
    res['dev_bp'] = (dr001 - pol) * 100.0
    if dr007 is not None:
        res['dr007_dev_bp'] = (dr007 - pol) * 100.0

    # ---- 2. 分层 ----
    tier = _tier_bp('LIQ.CN.DR001', 'LIQ.CN.R001')
    tier_prev = _tier_bp_prev('LIQ.CN.DR001', 'LIQ.CN.R001')
    res['tier_bp'] = tier
    res['tier_bp_prev'] = tier_prev
    if tier is not None and tier_prev is not None:
        res['tier_widen_bp'] = tier - tier_prev

    # ---- 3. 央行操作 ----
    omo_net = None
    omo_inject = None
    omo_mat = None
    omo_bal = None
    if omo_data:
        try:
            omo_net = omo_data.get('today_net')
            omo_inject = omo_data.get('today_injection')
            omo_mat = omo_data.get('today_maturity')
        except Exception:
            pass
    if omo_net is None:
        omo_net, _ = E.point('LIQ.CN.OMO.NET')
    if omo_inject is None:
        omo_inject, _ = E.point('LIQ.CN.OMO.INJECT')
    if omo_mat is None:
        omo_mat, _ = E.point('LIQ.CN.OMO.MATURITY')
    omo_bal, _ = E.point('LIQ.CN.OMO.BALANCE')

    on_amt = _overnight_amount(omo_data, dr001_date)
    on_share = None
    if on_amt is not None and omo_inject:
        try:
            on_share = on_amt / float(omo_inject) if float(omo_inject) > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            on_share = None

    res['omo'].update({
        'net': omo_net, 'inject': omo_inject, 'maturity': omo_mat,
        'balance': omo_bal, 'overnight_amt': on_amt, 'overnight_share': on_share,
    })

    # ---- 4. 连续上行 ----
    consec = _consec_up('LIQ.CN.DR001')
    res['consec_up_days'] = consec

    # ---- 5. 央行定性 ----
    stance = '中性'
    if omo_net is not None:
        net = float(omo_net)
        if on_amt is not None and on_amt >= TH['overnight_heavy'] and net >= 0:
            stance = '呵护'
        elif abs(net) <= TH['net_flat']:
            stance = '被动对冲'
        elif net > TH['net_flat']:
            stance = '净投放'
        else:  # 净回笼
            if on_amt is not None and on_share is not None and on_share < TH['overnight_shrink']:
                stance = '主动收敛'
            else:
                stance = '净回笼'
    res['cb_stance'] = stance

    # ---- 6. 五档标签 ----
    dev = res['dev_bp']
    tier_v = tier
    label, reasons = _classify(dev, tier_v, res['tier_widen_bp'], consec, stance, omo_net)
    res['label'] = label
    res['reasons'] = reasons

    # ---- 7. 紧张度评分（0=极宽松 100=极紧）----
    res['score'] = _score(dev, tier_v, consec, stance, omo_net)

    # ---- 8. 告警 ----
    alerts, warns = [], []
    if dev is not None:
        if dev >= TH['dev_alert_bp']:
            alerts.append(f"DR001 偏离政策利率 {dev:+.1f}bp（≥{TH['dev_alert_bp']:.0f}bp 阈值）")
        elif dev >= TH['dev_warn_bp']:
            warns.append(f"DR001 偏离政策利率 {dev:+.1f}bp")
    if tier_v is not None:
        if tier_v >= TH['tier_alert_bp']:
            alerts.append(f"非银分层 {tier_v:.1f}bp（≥{TH['tier_alert_bp']:.0f}bp，非银融资压力大）")
        elif tier_v >= TH['tier_warn_bp']:
            warns.append(f"非银分层 {tier_v:.1f}bp")
    if label == '真收紧':
        alerts.append(f"资金面三共振确认「真收紧」（连续{consec}日上行 + 净投放{omo_net:+.0f}亿 + 分层走阔）"
                      if omo_net is not None else f"资金面确认「真收紧」（连续{consec}日上行）")
    if label == '结构性收紧':
        warns.append('流动性传导阻塞：银行不缺钱但非银溢价走阔')
    if stance == '主动收敛':
        warns.append('央行主动收敛（隔夜缩量 + 净回笼）')
    res['alerts'] = alerts
    res['warns'] = warns

    # ---- 9. 数据新鲜度 ----
    res['data_freshness'] = _freshness(dr001_date)
    res['note'] = '偏离=DR001-政策利率；分层=R001-DR001（同日同源对齐）'
    return res


def _classify(dev, tier, tier_widen, consec, stance, net) -> tuple:
    """五档标签 + 判定理由。"""
    reasons: List[str] = []
    if dev is None:
        return '数据不足', reasons

    dev_txt = f"DR001 偏离政策利率 {dev:+.1f}bp"
    tier_txt = f"分层 {tier:.1f}bp" if tier is not None else '分层不可得'
    reasons.append(dev_txt)
    reasons.append(tier_txt)
    reasons.append(f"央行{stance}" + (f"（净投放{net:+.0f}亿）" if net is not None else ''))

    tier_wide = tier is not None and tier >= TH['tier_warn_bp']
    tier_widening = tier_widen is not None and tier_widen > 3.0
    dev_high = dev >= TH['dev_warn_bp']
    dev_neg = dev <= -TH['dev_warn_bp']

    # 真收紧：三共振
    if dev_high and tier_wide and consec >= TH['consec_days'] and (net is not None and net < -TH['net_flat']):
        reasons.append(f"三共振成立：偏离{dev:+.1f}bp + 分层{tier:.1f}bp + 连续{consec}日上行 + 净回笼")
        return '真收紧', reasons

    # 结构性收紧：DR 不紧但分层走阔
    if tier_wide and not dev_high:
        reasons.append('DR001 未明显偏离，但非银溢价走阔 → 传导阻塞')
        return '结构性收紧', reasons

    # 技术性扰动：DR 偏离 + 分层正常 + 央行呵护
    if dev_high and not tier_wide and stance in ('呵护', '净投放'):
        reasons.append('偏离由时点因素驱动，分层未走阔且央行呵护 → 技术性扰动')
        return '技术性扰动', reasons

    # 宽松余裕
    if dev_neg and (net is None or net >= -TH['net_flat']):
        reasons.append('DR001 显著低于政策利率 → 资金面宽松余裕')
        return '宽松余裕', reasons

    # 均衡
    if abs(dev) < TH['dev_warn_bp'] and not tier_wide:
        reasons.append('偏离与分层均在正常区间')
        return '均衡', reasons

    if dev_high:
        reasons.append('偏离偏高但共振条件不足 → 观察，暂不定性收紧')
        return '技术性扰动', reasons
    return '均衡', reasons


def _score(dev, tier, consec, stance, net) -> float:
    """紧张度评分 0-100（越高越紧）。"""
    s = 50.0
    if dev is not None:
        s += dev * 1.2
    if tier is not None:
        s += max(0.0, tier - 10.0) * 0.8
    s += min(consec, 4) * 3.0
    if stance == '呵护':
        s -= 8.0
    elif stance == '净投放':
        s -= 4.0
    elif stance == '主动收敛':
        s += 8.0
    elif stance == '净回笼':
        s += 3.0
    if net is not None and net < 0:
        s += min(10.0, abs(net) / 1000.0)
    return max(0.0, min(100.0, round(s, 1)))


def _freshness(date_str: Optional[str]) -> str:
    """数据新鲜度提示：中午 DR001 为盘中加权未定稿。"""
    if not date_str:
        return ''
    try:
        from datetime import datetime
        d = datetime.strptime(date_str, '%Y-%m-%d').date()
        today = datetime.now().date()
        gap = (today - d).days
        if gap == 0:
            return '当日盘中加权（未定稿）'
        if gap == 1:
            return '上一交易日定稿'
        return f'滞后 {gap} 天'
    except Exception:
        return ''


# ------------------------------------------------------------------ 渲染

def render_md_block(result: Dict[str, Any]) -> List[str]:
    """日报 markdown 段（list[str]，可直接 add）。"""
    r = result or {}
    lines: List[str] = ["\n#### 🏦 资金面判定：技术性扰动 vs 真收紧"]

    if not r.get('ok'):
        lines.append(f"\n⚠️ {r.get('note') or '数据获取失败'}")
        return lines

    label = r.get('label') or '未知'
    icon = {
        '真收紧': '🔴', '结构性收紧': '🟠', '技术性扰动': '🟡',
        '宽松余裕': '🟢', '均衡': '⚪', '数据不足': '⚠️',
    }.get(label, '⚪')
    lines.append(f"\n**判定**: {icon} **{label}** | 紧张度 {r.get('score')}/100 | 央行{r.get('cb_stance')}")

    dev = r.get('dev_bp')
    tier = r.get('tier_bp')
    pol = r.get('policy_rate')
    dr = r.get('dr001')
    r001 = r.get('r001')
    dr007 = r.get('dr007')
    tier_prev = r.get('tier_bp_prev')
    widen = r.get('tier_widen_bp')

    lines.append(f"\n| 指标 | 数值 | 相对政策利率 |")
    lines.append(f"|---|---|---|")
    lines.append(f"| DR001（存款类机构隔夜） | {E.fmt(dr, 4)}% | {E.fmt_bp(dev)} |")
    if r001 is not None:
        lines.append(f"| R001（全市场隔夜） | {E.fmt(r001, 4)}% | {E.fmt_bp((r001 - pol) * 100 if pol else None)} |")
    if dr007 is not None:
        lines.append(f"| DR007（存款类机构7天） | {E.fmt(dr007, 4)}% | {E.fmt_bp(r.get('dr007_dev_bp'))} |")
    if tier is not None:
        tw = f"，较上日{'走阔' if (widen or 0) > 0 else '收窄'}{abs(widen):.1f}bp" if widen is not None else ''
        lines.append(f"| 非银分层 R001-DR001 | {tier:.1f}bp | {tw.lstrip('，')} |")
    lines.append(f"\n*政策利率 {E.fmt(pol, 2)}%（{r.get('policy_rate_src')}）| 数据日期 {r.get('dr001_date')}（{r.get('data_freshness')}）*")

    omo = r.get('omo') or {}
    if any(omo.get(k) is not None for k in ('net', 'inject', 'overnight_amt')):
        parts = []
        if omo.get('inject') is not None:
            parts.append(f"投放 {omo['inject']:.0f}亿")
        if omo.get('maturity') is not None:
            parts.append(f"到期 {omo['maturity']:.0f}亿")
        if omo.get('net') is not None:
            parts.append(f"净{'投放' if omo['net'] >= 0 else '回笼'} {abs(omo['net']):.0f}亿")
        if omo.get('overnight_amt') is not None:
            sh = f"（占投放{omo['overnight_share']*100:.0f}%）" if omo.get('overnight_share') else ''
            parts.append(f"其中隔夜逆回购 {omo['overnight_amt']:.0f}亿{sh}")
        if omo.get('balance') is not None:
            parts.append(f"OMO余额 {omo['balance']:.0f}亿")
        lines.append(f"\n**央行操作**: {' | '.join(parts)}")

    reasons = r.get('reasons') or []
    if reasons:
        lines.append(f"\n**判定依据**: " + "；".join(reasons))

    lines.append(f"\n**铁律**: 单日 DR001 上行 ≠ 收紧，需「连续2-3日上行 + 净投放转负 + 分层走阔」三共振方可定性真收紧。")
    if r.get('note'):
        lines.append(f"\n*{r['note']}*")
    return lines


def render_card_lines(result: Dict[str, Any]) -> List[str]:
    """飞书卡片行（紧凑版）。"""
    r = result or {}
    if not r.get('ok'):
        return [f"⚠️ **资金面判定**: {r.get('note') or '数据获取失败'}"]

    label = r.get('label') or '未知'
    icon = {
        '真收紧': '🔴', '结构性收紧': '🟠', '技术性扰动': '🟡',
        '宽松余裕': '🟢', '均衡': '⚪',
    }.get(label, '⚪')
    out = [f"🏦 **资金面判定**: {icon} **{label}** | 紧张度 {r.get('score')}/100 | 央行{r.get('cb_stance')}"]

    dr = r.get('dr001')
    dev = r.get('dev_bp')
    tier = r.get('tier_bp')
    bits = []
    if dr is not None:
        bits.append(f"DR001 {dr:.4f}%（{E.fmt_bp(dev)}）")
    if tier is not None:
        w = r.get('tier_widen_bp')
        bits.append(f"分层 {tier:.1f}bp" + (f"（{'走阔' if w > 0 else '收窄'}{abs(w):.1f}）" if w is not None else ''))
    omo = r.get('omo') or {}
    if omo.get('net') is not None:
        bits.append(f"净{'投放' if omo['net'] >= 0 else '回笼'}{abs(omo['net']):.0f}亿")
    if omo.get('overnight_amt') is not None:
        bits.append(f"隔夜{omo['overnight_amt']:.0f}亿")
    if bits:
        out.append("- " + " | ".join(bits))
    if r.get('data_freshness'):
        out.append(f"- *{r['data_freshness']}*")
    return out
