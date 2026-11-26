"""美债利率–流动性传导监控（纯 Python，零 LLM，全部走 data_hub/EDB）。

传导链条
--------
政策利率 → 短端(2Y) → 长端(10Y/30Y) → 信用溢价(IG/HY OAS) → 美元(DXY) / 风险资产(标普/VIX)

每一环都可能**阻塞**：

- 短端动、长端不动 → 期限溢价被压制（避险买盘 / QT 与避险对冲）；
- 长端动、信用不动 → 信用市场对利率钝化（风险偏好强 or 流动性充裕）；
- 信用动、美元股市不动 → 传导末端未到（尚未形成风险事件）；
- 货币市场利差（SOFR-EFFR / TGCR-SOFR）走阔 → **传导在货币市场这一层就打结了**，
  此时长端利率再怎么动都不代表流动性真正收紧，而是抵押品/准备金分布问题。

对外接口
--------
- ``run_ust_monitor()`` → ``(data, md)``
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

CURVE = [
    ('2Y', 'RATE.US.2Y'),
    ('5Y', 'RATE.US.5Y'),
    ('10Y', 'RATE.US.10Y'),
    ('30Y', 'RATE.US.30Y'),
]

TH = {
    'mm_spread_warn_bp': 5.0,     # 货币市场利差走阔警戒
    'oas_widen_bp': 20.0,         # 信用利差月度走阔警戒
    'srf_watch_mn': 1.0,
}


def run_ust_monitor() -> Tuple[Dict[str, Any], str]:
    """执行美债利率–流动性传导监控。"""
    data: Dict[str, Any] = {
        'ok': False, 'date': None,
        'curve': {}, 'curve_spread_bp': None, 'curve_shape': '',
        'real': {}, 'mm': {}, 'credit': {}, 'supply': {},
        'chain': [], 'blocked_at': None, 'verdict': '',
        'alerts': [], 'warns': [], 'note': '',
    }

    # ---------------- 1. 收益率曲线 ----------------
    curve: Dict[str, Any] = {}
    for tag, sid in CURVE:
        st = E.stat(sid, window=260)
        if not st.get('ok'):
            continue
        curve[tag] = {
            'value': st['latest'], 'date': st['date'],
            'bp_1d': st['bp_1d'], 'bp_5d': st['bp_5d'], 'bp_20d': st['bp_20d'],
            'rank': st['rank'],
        }
    if not curve:
        data['note'] = '美债收益率曲线取数全部失败（data_hub/EDB）'
        data['alerts'].append('美债收益率曲线数据缺失')
        return data, _render_md(data)

    data['ok'] = True
    data['curve'] = curve
    data['date'] = (curve.get('10Y') or {}).get('date') or (curve.get('2Y') or {}).get('date')

    # RATE.SPREAD.US.10Y2Y 的 registry 单位就是 bp，直接取用，不可再 ×100
    sp = E.stat('RATE.SPREAD.US.10Y2Y', window=260)
    if sp.get('ok'):
        data['curve_spread_bp'] = sp['latest']
        data['curve_spread_chg_bp'] = sp.get('chg_5d')
    elif curve.get('10Y') and curve.get('2Y'):
        # 兜底自算：两条收益率序列单位都是 %，差值 ×100 才是 bp
        data['curve_spread_bp'] = (curve['10Y']['value'] - curve['2Y']['value']) * 100.0

    c10 = (curve.get('10Y') or {}).get('value')
    c2 = (curve.get('2Y') or {}).get('value')
    c30 = (curve.get('30Y') or {}).get('value')
    shape_bits = []
    if data['curve_spread_bp'] is not None:
        cs = data['curve_spread_bp']
        if cs < 0:
            shape_bits.append(f"10Y-2Y 倒挂 {cs:.1f}bp")
        elif cs < 30:
            shape_bits.append(f"10Y-2Y 平坦 {cs:.1f}bp")
        elif cs < 120:
            shape_bits.append(f"10Y-2Y 正常陡峭 {cs:.1f}bp")
        else:
            shape_bits.append(f"10Y-2Y 异常陡峭 {cs:.1f}bp")
    if c30 is not None and c10 is not None:
        s = (c30 - c10) * 100.0
        shape_bits.append(f"30Y-10Y {s:+.1f}bp")
    data['curve_shape'] = '；'.join(shape_bits) or 'n/a'

    # ---------------- 2. 实际利率分解 ----------------
    real: Dict[str, Any] = {}
    nom, _ = E.point('RATE.US.10Y')
    rl, _ = E.point('RATE.US.REAL.10Y')
    be, _ = E.point('RATE.US.BREAKEVEN.10Y')
    if nom is not None:
        real['nominal_10y'] = nom
    if rl is not None:
        real['real_10y'] = rl
        real['real_10y_bp_20d'] = E.bp('RATE.US.REAL.10Y', 20)
    if be is not None:
        real['breakeven_10y'] = be
        real['breakeven_bp_20d'] = E.bp('RATE.US.BREAKEVEN.10Y', 20)
    rl5, _ = E.point('RATE.US.REAL.5Y')
    be5, _ = E.point('RATE.US.BREAKEVEN.5Y')
    if rl5 is not None:
        real['real_5y'] = rl5
    if be5 is not None:
        real['breakeven_5y'] = be5
    data['real'] = real

    # ---------------- 3. 货币市场利差 ----------------
    mm: Dict[str, Any] = {}
    sofr, _ = E.point('LIQ.US.SOFR')
    effr, _ = E.point('LIQ.US.EFFR')
    tgcr, _ = E.point('LIQ.US.TGCR')
    bgcr, _ = E.point('LIQ.US.BGCR')
    rrp_rate, _ = E.point('LIQ.US.RRP.RATE')
    if sofr is not None:
        mm['sofr'] = sofr
    if effr is not None:
        mm['effr'] = effr
    if tgcr is not None:
        mm['tgcr'] = tgcr
    if bgcr is not None:
        mm['bgcr'] = bgcr
    if sofr is not None and effr is not None:
        mm['sofr_effr_bp'] = (sofr - effr) * 100.0
    if tgcr is not None and sofr is not None:
        mm['tgcr_sofr_bp'] = (tgcr - sofr) * 100.0
    if bgcr is not None and sofr is not None:
        mm['bgcr_sofr_bp'] = (bgcr - sofr) * 100.0
    if sofr is not None and rrp_rate is not None:
        mm['sofr_floor_bp'] = (sofr - rrp_rate) * 100.0
    srf_rows = E.rows('LIQ.US.SRF', limit=5)
    if srf_rows:
        # registry 标注 LIQ.US.SRF 单位为「美元」，这里统一折算成百万美元
        mm['srf_max_5d_mn'] = max(x['value'] for x in srf_rows) / 1e6
        mm['srf_sum_5d_mn'] = sum(x['value'] for x in srf_rows) / 1e6
    data['mm'] = mm

    # ---------------- 4. 信用溢价 ----------------
    credit: Dict[str, Any] = {}
    for tag, sid in [('IG', 'CREDIT.US.OAS.IG'), ('HY', 'CREDIT.US.OAS.HY'),
                     ('BB', 'CREDIT.US.OAS.BB'), ('CCC', 'CREDIT.US.OAS.CCC'),
                     ('AA', 'CREDIT.US.OAS.AA')]:
        st = E.stat(sid, window=260)
        if not st.get('ok'):
            continue
        # CREDIT.US.OAS.* 的 registry 单位是 %（如 HY 3.2%），统一折算成 bp 存储，
        # 展示层一律按 bp 输出，避免把 3.2% 显示成「3bp」。
        credit[tag] = {
            'value': st['latest'] * 100.0, 'bp_5d': st['bp_5d'], 'bp_20d': st['bp_20d'],
            'rank': st['rank'],
        }
    data['credit'] = credit

    # ---------------- 5. 美债供给 ----------------
    supply: Dict[str, Any] = {}
    for tag, sid in [('短债Bills', 'US.UST.MSPD.BILLS'), ('中债Notes', 'US.UST.MSPD.NOTES'),
                     ('长债Bonds', 'US.UST.MSPD.BONDS'), ('TIPS', 'US.UST.MSPD.TIPS'),
                     ('合计', 'US.UST.MSPD.MARKETABLE')]:
        st = E.stat(sid, window=60)
        if not st.get('ok'):
            continue
        # MSPD 可流通余额是**月频**序列，chg_20d 等于 20 个月前的差，必须用 chg_1d（上月末）
        supply[tag] = {
            'value_bn': st['latest'] * 1e-3,
            'chg_1m_bn': (st.get('chg_1d') or 0) * 1e-3,
        }
    data['supply'] = supply

    # ---------------- 6. 传导链条诊断 ----------------
    chain, blocked = _diagnose(curve, mm, credit, real)
    data['chain'] = chain
    data['blocked_at'] = blocked
    data['verdict'] = _verdict(data)

    # ---------------- 7. 告警 ----------------
    alerts, warns = [], []
    for k, v in [('sofr_effr_bp', 'SOFR-EFFR'), ('tgcr_sofr_bp', 'TGCR-SOFR'),
                 ('bgcr_sofr_bp', 'BGCR-SOFR')]:
        val = mm.get(k)
        if val is not None and val >= TH['mm_spread_warn_bp']:
            alerts.append(f"{v} 利差 {val:.1f}bp（≥{TH['mm_spread_warn_bp']:.0f}bp，货币市场分层加剧）")
        elif val is not None and val >= TH['mm_spread_warn_bp'] / 2:
            warns.append(f"{v} {val:.1f}bp")
    if mm.get('srf_max_5d_mn') is not None and mm['srf_max_5d_mn'] > TH['srf_watch_mn']:
        alerts.append(f"SRF 被使用（近5日最大 ${mm['srf_max_5d_mn']:.0f}M）")
    hy = credit.get('HY') or {}
    if hy.get('bp_20d') is not None and hy['bp_20d'] >= TH['oas_widen_bp']:
        alerts.append(f"高收益债 OAS 月内走阔 {hy['bp_20d']:+.0f}bp（信用风险升温）")
    if blocked:
        warns.append(f"传导在「{blocked}」环节阻塞")
    data['alerts'] = alerts
    data['warns'] = warns
    data['note'] = ('收益率/实际利率/信用走 data_hub/EDB；货币市场利差=SOFR-EFFR、TGCR-SOFR；'
                    'SRF 单位为百万美元；美债供给=MSPD可流通余额月变')
    return data, _render_md(data)


def _diagnose(curve: Dict[str, Any], mm: Dict[str, Any],
              credit: Dict[str, Any], real: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """逐环诊断传导链条，返回 (链条明细, 阻塞环节名)。"""
    chain: List[Dict[str, Any]] = []
    blocked = None

    d2 = (curve.get('2Y') or {}).get('bp_20d')
    d10 = (curve.get('10Y') or {}).get('bp_20d')
    hy_chg = (credit.get('HY') or {}).get('bp_20d')
    dxy_chg = E.chg('GLOBAL.DXY', 20)
    spx_chg = E.chg('GLOBAL.SP500', 20)
    mm_sp = mm.get('sofr_effr_bp')

    # 环节 1：短端
    if d2 is not None:
        chain.append({
            'link': '短端 2Y', 'value': f"{d2:+.1f}bp/月",
            'state': '上行' if d2 > 5 else ('下行' if d2 < -5 else '走平'),
            'pass': True,
        })
    # 环节 2：短端→长端
    if d2 is not None and d10 is not None:
        passed = abs(d10) >= 3
        chain.append({
            'link': '长端 10Y', 'value': f"{d10:+.1f}bp/月",
            'state': '跟随短端' if passed else '未跟随（期限溢价被压制）',
            'pass': passed,
        })
        if not passed:
            blocked = '长端（期限溢价被压制）'
    # 环节 3：长端→信用
    if d10 is not None and hy_chg is not None:
        passed = abs(hy_chg) >= 5
        chain.append({
            'link': '信用 HY OAS', 'value': f"{hy_chg:+.1f}bp/月",
            'state': '跟随长端' if passed else '钝化（风险偏好强）',
            'pass': passed,
        })
        if not passed and blocked is None:
            blocked = '信用（对利率钝化）'
    # 环节 4：信用→美元/股市
    if hy_chg is not None and dxy_chg is not None:
        passed = (hy_chg > 0 and dxy_chg > 0) or (hy_chg < 0 and dxy_chg < 0)
        chain.append({
            'link': '美元 DXY', 'value': f"{dxy_chg:+.2f}/月",
            'state': '与信用同向' if passed else '背离',
            'pass': passed,
        })
        if not passed and blocked is None:
            blocked = '美元（与信用背离）'
    if hy_chg is not None and spx_chg is not None:
        passed = (hy_chg > 0 and spx_chg < 0) or (hy_chg < 0 and spx_chg > 0)
        chain.append({
            'link': '标普500', 'value': f"{spx_chg:+.0f}/月",
            'state': '与信用反向（正常）' if passed else '同向（异常）',
            'pass': passed,
        })

    # 货币市场先验：若货币市场自身分层，传导在源头就阻塞
    if mm_sp is not None and mm_sp >= TH['mm_spread_warn_bp']:
        blocked = '货币市场（SOFR-EFFR 分层）'
        chain.insert(0, {
            'link': '货币市场', 'value': f"SOFR-EFFR {mm_sp:+.1f}bp",
            'state': '分层加剧，源头阻塞', 'pass': False,
        })
    return chain, blocked


def _verdict(d: Dict[str, Any]) -> str:
    """一句话判词。"""
    bits = []
    cs = d.get('curve_spread_bp')
    if cs is not None:
        bits.append(f"10Y-2Y {cs:+.0f}bp")
    c10 = (d.get('curve') or {}).get('10Y') or {}
    if c10.get('value') is not None:
        bits.append(f"10Y {c10['value']:.2f}%")
    mm = d.get('mm') or {}
    if mm.get('sofr_effr_bp') is not None:
        bits.append(f"SOFR-EFFR {mm['sofr_effr_bp']:+.1f}bp")
    hy = (d.get('credit') or {}).get('HY') or {}
    if hy.get('value') is not None:
        bits.append(f"HY OAS {hy['value']:.0f}bp")
    if d.get('blocked_at'):
        bits.append(f"⚠️ 阻塞于{d['blocked_at']}")
    else:
        bits.append('✅ 传导顺畅')
    return ' | '.join(bits)


def _render_md(d: Dict[str, Any]) -> str:
    """生成 markdown 段。"""
    lines: List[str] = ["### 🔬 美债利率–流动性传导监控"]
    if not d.get('ok'):
        lines.append(f"\n⚠️ {d.get('note') or '数据获取失败'}")
        return '\n'.join(lines)

    lines.append(f"\n**判词**: {d.get('verdict')}")

    curve = d.get('curve') or {}
    if curve:
        lines.append(f"\n| 期限 | 收益率 | 日变动 | 周变动 | 月变动 | 近1年分位 |")
        lines.append("|---|---|---|---|---|---|")
        for tag, _ in CURVE:
            c = curve.get(tag)
            if not c:
                continue
            lines.append(f"| {tag} | {c['value']:.3f}% | {E.fmt_bp(c.get('bp_1d'))} | "
                         f"{E.fmt_bp(c.get('bp_5d'))} | {E.fmt_bp(c.get('bp_20d'))} | "
                         f"{c['rank']:.0f}% |" if c.get('rank') is not None else
                         f"| {tag} | {c['value']:.3f}% | {E.fmt_bp(c.get('bp_1d'))} | "
                         f"{E.fmt_bp(c.get('bp_5d'))} | {E.fmt_bp(c.get('bp_20d'))} | n/a |")
        lines.append(f"\n**曲线形态**: {d.get('curve_shape')}")
        lines.append(f"\n*数据日期: {d.get('date')}*")

    real = d.get('real') or {}
    if real:
        bits = []
        if real.get('nominal_10y') is not None:
            bits.append(f"名义10Y {real['nominal_10y']:.3f}%")
        if real.get('real_10y') is not None:
            bits.append(f"实际10Y(TIPS) {real['real_10y']:.3f}%（月变 {E.fmt_bp(real.get('real_10y_bp_20d'))}）")
        if real.get('breakeven_10y') is not None:
            bits.append(f"盈亏平衡通胀 {real['breakeven_10y']:.3f}%（月变 {E.fmt_bp(real.get('breakeven_bp_20d'))}）")
        lines.append(f"\n**实际利率分解**: {' | '.join(bits)}")

    mm = d.get('mm') or {}
    if mm:
        bits = []
        for k, label, nd in [('sofr', 'SOFR', 3), ('effr', 'EFFR', 3),
                             ('tgcr', 'TGCR', 3), ('bgcr', 'BGCR', 3)]:
            if mm.get(k) is not None:
                bits.append(f"{label} {mm[k]:.{nd}f}%")
        if bits:
            lines.append(f"\n**货币市场**: {' | '.join(bits)}")
        sp_bits = []
        for k, label in [('sofr_effr_bp', 'SOFR-EFFR'), ('tgcr_sofr_bp', 'TGCR-SOFR'),
                         ('bgcr_sofr_bp', 'BGCR-SOFR'), ('sofr_floor_bp', 'SOFR-央行下沿')]:
            if mm.get(k) is not None:
                sp_bits.append(f"{label} {mm[k]:+.1f}bp")
        if mm.get('srf_max_5d_mn') is not None:
            sp_bits.append(f"SRF近5日最大 ${mm['srf_max_5d_mn']:.0f}M")
        if sp_bits:
            lines.append(f"\n**利差与工具使用**: {' | '.join(sp_bits)}")

    credit = d.get('credit') or {}
    if credit:
        lines.append(f"\n| 信用品种 | OAS | 周变动 | 月变动 | 近1年分位 |")
        lines.append("|---|---|---|---|---|")
        for tag in ('IG', 'HY', 'BB', 'CCC'):
            c = credit.get(tag)
            if not c:
                continue
            rk = f"{c['rank']:.0f}%" if c.get('rank') is not None else 'n/a'
            lines.append(f"| {tag} | {c['value']:.0f}bp | {E.fmt_bp(c.get('bp_5d'))} | "
                         f"{E.fmt_bp(c.get('bp_20d'))} | {rk} |")

    supply = d.get('supply') or {}
    if supply:
        bits = []
        for tag, s in supply.items():
            bits.append(f"{tag} ${s['value_bn']/1e3:.2f}T（月变 {s['chg_1m_bn']:+.0f}B）")
        lines.append(f"\n**美债供给**: {' | '.join(bits)}")

    chain = d.get('chain') or []
    if chain:
        lines.append(f"\n**传导链条**:")
        for c in chain:
            icon = '✅' if c.get('pass') else '⚠️'
            lines.append(f"- {icon} {c['link']}: {c['value']} → {c['state']}")
        if d.get('blocked_at'):
            lines.append(f"\n**🚧 阻塞环节**: {d['blocked_at']}")

    alerts = d.get('alerts') or []
    warns = d.get('warns') or []
    if alerts:
        lines.append("\n**🚨 传导告警**:")
        for a in alerts:
            lines.append(f"- {a}")
    if warns:
        lines.append("\n**⚠️ 关注项**:")
        for w in warns:
            lines.append(f"- {w}")
    if d.get('note'):
        lines.append(f"\n*{d['note']}*")
    return '\n'.join(lines)
