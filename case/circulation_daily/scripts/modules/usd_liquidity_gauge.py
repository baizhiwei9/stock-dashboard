"""美元流动性水位仪（纯 Python，零 LLM，全部走 data_hub/EDB）。

水位模型
--------
美联储负债端 ≈ 银行准备金 + 隔夜逆回购(ON RRP) + 财政部TGA + 流通货币。
其中对市场「可用」的流动性池 = **准备金 + ON RRP**。

- **ON RRP 是缓冲垫**：QT 或 TGA 补库时先抽 RRP，准备金不受影响；RRP 见底后，
  抽水直接冲击准备金 → 隔夜利率跳升 → SRF 被触发。所以 RRP 归零是**质变信号**。
- **TGA 是抽水机**：TGA 上升（发债缴款）等于把准备金吸进财政账户，市场流动性下降。
- **Fed BS 是总闸门**：缩表 = 总水位下降。

综合水位分（0-100，越高越宽松）= 准备金分位×0.40 + (100-RRP分位)×0.25
                                    + (100-TGA分位)×0.20 + FedBS分位×0.15

对外接口
--------
- ``run(verbose=False)`` → dict（含 ``level`` / ``verdict`` / ``raw`` 子字典）
- ``render_md(data)``    → str

``raw`` 中被下游（alert_book）依赖的字段：
``raw.srf.amt_5d`` / ``raw.srf.max_1d_5d`` / ``raw.ceil_bp`` / ``raw.effr_iorb.bp``
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

TH = {
    'rrp_dry_bn': 5.0,       # ON RRP 低于该值（十亿美元）视为逼近枯竭
    'rrp_zero_bn': 0.5,      # 视为已归零
    'srf_watch_mn': 1.0,     # SRF 单日使用超过该值（百万美元）视为被触发
    'ceil_warn_bp': 10.0,    # SOFR 高于央行利率下沿的警戒 bp
    'effr_warn_bp': 10.0,
}

SID_BS = 'LIQ.US.FED.BS'
SID_RES = 'LIQ.US.RESERVES'
SID_RRP = 'LIQ.US.RRP'
SID_TGA = 'LIQ.US.TGA'
SID_SRF = 'LIQ.US.SRF'
SID_SOFR = 'LIQ.US.SOFR'
SID_EFFR = 'LIQ.US.EFFR'
SID_RRP_RATE = 'LIQ.US.RRP.RATE'
SID_TGCR = 'LIQ.US.TGCR'
SID_BGCR = 'LIQ.US.BGCR'

# 单位：Fed H.4.1 系列均为百万美元
MN_TO_BN = 1e-3


def _bn(v_mn: Optional[float]) -> Optional[float]:
    """百万美元 → 十亿美元。"""
    return None if v_mn is None else v_mn * MN_TO_BN


def run(verbose: bool = False) -> Dict[str, Any]:
    """执行美元流动性水位测量。"""
    data: Dict[str, Any] = {
        'ok': False, 'date': None,
        'level': '未知', 'score': 50.0, 'verdict': '',
        'items': {}, 'raw': {'srf': {}, 'effr_iorb': {}, 'ceil_bp': None},
        'alerts': [], 'warns': [], 'note': '',
    }

    # ---- 四项核心水位 ----
    st_res = E.stat(SID_RES, window=260)
    st_rrp = E.stat(SID_RRP, window=260)
    st_tga = E.stat(SID_TGA, window=260)
    st_bs = E.stat(SID_BS, window=260)

    if not st_res.get('ok') and not st_bs.get('ok'):
        data['note'] = 'Fed 资产负债表系列全部取数失败（data_hub/EDB）'
        data['alerts'].append('美元流动性水位：核心数据缺失')
        return data

    data['ok'] = True
    data['date'] = st_res.get('date') or st_bs.get('date')

    items: Dict[str, Any] = {}
    if st_res.get('ok'):
        items['reserves'] = {
            'name': '银行准备金', 'value_bn': _bn(st_res['latest']), 'date': st_res['date'],
            'chg_1w_bn': _bn(st_res['chg_5d']), 'chg_1m_bn': _bn(st_res['chg_20d']),
            'rank': st_res['rank'],
        }
    if st_rrp.get('ok'):
        items['rrp'] = {
            'name': 'ON RRP', 'value_bn': _bn(st_rrp['latest']), 'date': st_rrp['date'],
            'chg_1w_bn': _bn(st_rrp['chg_5d']), 'chg_1m_bn': _bn(st_rrp['chg_20d']),
            'rank': st_rrp['rank'],
        }
    if st_tga.get('ok'):
        items['tga'] = {
            'name': 'TGA', 'value_bn': _bn(st_tga['latest']), 'date': st_tga['date'],
            'chg_1w_bn': _bn(st_tga['chg_5d']), 'chg_1m_bn': _bn(st_tga['chg_20d']),
            'rank': st_tga['rank'],
        }
    if st_bs.get('ok'):
        items['fed_bs'] = {
            'name': 'Fed总资产', 'value_bn': _bn(st_bs['latest']), 'date': st_bs['date'],
            'chg_1w_bn': _bn(st_bs['chg_5d']), 'chg_1m_bn': _bn(st_bs['chg_20d']),
            'rank': st_bs['rank'],
        }
    data['items'] = items

    # ---- 综合水位分 ----
    def _r(key: str, default: float = 50.0) -> float:
        it = items.get(key)
        if not it or it.get('rank') is None:
            return default
        return float(it['rank'])

    score = (0.40 * _r('reserves')
             + 0.25 * (100.0 - _r('rrp'))
             + 0.20 * (100.0 - _r('tga'))
             + 0.15 * _r('fed_bs'))
    score = max(0.0, min(100.0, round(score, 1)))

    # RRP 枯竭强制降级：缓冲垫没了，QT 直接冲击准备金
    rrp_bn = (items.get('rrp') or {}).get('value_bn')
    forced = None
    if rrp_bn is not None:
        if rrp_bn <= TH['rrp_zero_bn']:
            forced = '枯竭'
        elif rrp_bn <= TH['rrp_dry_bn']:
            forced = '紧张'

    if forced == '枯竭':
        level = '枯竭'
        score = min(score, 18.0)
    elif forced == '紧张':
        level = '紧张'
        score = min(score, 30.0)
    elif score >= 75:
        level = '充裕'
    elif score >= 55:
        level = '中性偏松'
    elif score >= 40:
        level = '中性'
    elif score >= 25:
        level = '偏紧'
    else:
        level = '紧张'
    data['level'] = level
    data['score'] = score

    # ---- 货币市场压力（SRF / SOFR / EFFR）----
    srf_rows = E.rows(SID_SRF, limit=5)
    # registry 标注 LIQ.US.SRF 单位为「美元」，本模块内部统一换算成百万美元，
    # 与 TH['srf_watch_mn']、alert_book 的 srf_trigger_mn 保持同一口径。
    srf_vals = [x['value'] / 1e6 for x in srf_rows]
    srf_5d = sum(srf_vals) if srf_vals else None
    srf_max = max(srf_vals) if srf_vals else None
    data['raw']['srf'] = {'amt_5d': srf_5d, 'max_1d_5d': srf_max, 'n': len(srf_vals)}

    sofr, _ = E.point(SID_SOFR)
    effr, _ = E.point(SID_EFFR)
    rrp_rate, _ = E.point(SID_RRP_RATE)
    tgcr, _ = E.point(SID_TGCR)
    bgcr, _ = E.point(SID_BGCR)

    ceil_bp = None
    if sofr is not None and rrp_rate is not None:
        ceil_bp = (sofr - rrp_rate) * 100.0
    data['raw']['ceil_bp'] = ceil_bp

    effr_bp = None
    if effr is not None and rrp_rate is not None:
        # 无 IORB 序列，用 RRP 报价利率作央行利率下沿代理（IORB-5bp 量级）
        effr_bp = (effr - rrp_rate) * 100.0
    data['raw']['effr_iorb'] = {'bp': effr_bp, 'proxy': 'RRP.RATE'}

    mm = {
        'sofr': sofr, 'effr': effr, 'rrp_rate': rrp_rate,
        'tgcr': tgcr, 'bgcr': bgcr,
        'sofr_effr_bp': ((sofr - effr) * 100.0) if (sofr is not None and effr is not None) else None,
        'tgcr_sofr_bp': ((tgcr - sofr) * 100.0) if (tgcr is not None and sofr is not None) else None,
        'bgcr_sofr_bp': ((bgcr - sofr) * 100.0) if (bgcr is not None and sofr is not None) else None,
    }
    data['raw']['mm'] = mm

    # ---- 告警 ----
    alerts, warns = [], []
    if rrp_bn is not None:
        if rrp_bn <= TH['rrp_zero_bn']:
            alerts.append(f"ON RRP 已归零（${rrp_bn:.2f}B），QT 直接冲击准备金")
        elif rrp_bn <= TH['rrp_dry_bn']:
            alerts.append(f"ON RRP 仅 ${rrp_bn:.2f}B，逼近枯竭，缓冲垫失效")
    if srf_max is not None and srf_max > TH['srf_watch_mn']:
        alerts.append(f"SRF 被触发（近5日最大单日 ${srf_max:.0f}M，合计 ${srf_5d:.0f}M）")
    if ceil_bp is not None and ceil_bp >= TH['ceil_warn_bp']:
        alerts.append(f"SOFR 高于央行利率下沿 {ceil_bp:.1f}bp，货币市场脱离控制区间")
    elif ceil_bp is not None and ceil_bp >= TH['ceil_warn_bp'] / 2:
        warns.append(f"SOFR-央行下沿 {ceil_bp:.1f}bp，利差走阔")
    if effr_bp is not None and effr_bp >= TH['effr_warn_bp']:
        warns.append(f"EFFR 高于央行利率下沿 {effr_bp:.1f}bp")
    res_it = items.get('reserves') or {}
    if res_it.get('rank') is not None and res_it['rank'] <= 15:
        warns.append(f"准备金处近1年 {res_it['rank']:.0f}% 分位（低位）")
    tga_it = items.get('tga') or {}
    if tga_it.get('chg_1w_bn') is not None and tga_it['chg_1w_bn'] > 50:
        warns.append(f"TGA 一周补库 ${tga_it['chg_1w_bn']:.0f}B，抽走准备金")
    data['alerts'] = alerts
    data['warns'] = warns

    data['verdict'] = _verdict(level, score, items, alerts)
    data['note'] = ('水位=准备金+ON RRP 池；分位窗口近260个观测；'
                    'Fed H.4.1 数据单位为百万美元，展示已折算十亿美元')
    if verbose:
        print(f"  水位: {level} ({score}) | {data['verdict'][:80]}")
    return data


def _verdict(level: str, score: float, items: Dict[str, Any], alerts: List[str]) -> str:
    """一句话判词。"""
    res = items.get('reserves') or {}
    rrp = items.get('rrp') or {}
    tga = items.get('tga') or {}
    bits = [f"综合水位 {score}/100 → {level}"]
    if res.get('value_bn') is not None:
        bits.append(f"准备金 ${res['value_bn']/1e3:.2f}T")
    if rrp.get('value_bn') is not None:
        bits.append(f"ON RRP ${rrp['value_bn']:.2f}B")
    if tga.get('value_bn') is not None:
        bits.append(f"TGA ${tga['value_bn']/1e3:.2f}T")
    if alerts:
        bits.append("；".join(alerts[:2]))
    return ' | '.join(bits)


# ------------------------------------------------------------------ 渲染

def render_md(data: Dict[str, Any]) -> str:
    """生成 markdown 段。"""
    d = data or {}
    if not d.get('ok'):
        return f"⚠️ {d.get('note') or '美元流动性水位数据获取失败'}"

    lines: List[str] = []
    icon = {'充裕': '🟢', '中性偏松': '🟢', '中性': '🟡',
            '偏紧': '🟠', '紧张': '🔴', '枯竭': '🔴💀'}.get(d.get('level'), '⚪')
    lines.append(f"**水位判级**: {icon} **{d.get('level')}** | 综合水位 {d.get('score')}/100")
    lines.append(f"\n**判词**: {d.get('verdict')}")

    items = d.get('items') or {}
    if items:
        lines.append(f"\n| 水位项 | 最新 | 周变动 | 月变动 | 近1年分位 |")
        lines.append("|---|---|---|---|---|")
        for key in ('reserves', 'rrp', 'tga', 'fed_bs'):
            it = items.get(key)
            if not it or it.get('value_bn') is None:
                continue
            v = it['value_bn']
            vs = f"${v/1e3:.2f}T" if abs(v) >= 1000 else f"${v:.2f}B"
            w = it.get('chg_1w_bn')
            m = it.get('chg_1m_bn')
            ws = f"{w:+.1f}B" if w is not None else 'n/a'
            ms = f"{m:+.1f}B" if m is not None else 'n/a'
            rk = f"{it['rank']:.0f}%" if it.get('rank') is not None else 'n/a'
            lines.append(f"| {it['name']} | {vs} | {ws} | {ms} | {rk} |")
        lines.append(f"\n*数据日期: {d.get('date')}*")

    mm = (d.get('raw') or {}).get('mm') or {}
    mm_bits = []
    for label, key, nd in [('SOFR', 'sofr', 3), ('EFFR', 'effr', 3),
                           ('ON RRP报价利率', 'rrp_rate', 2), ('TGCR', 'tgcr', 3), ('BGCR', 'bgcr', 3)]:
        if mm.get(key) is not None:
            mm_bits.append(f"{label} {mm[key]:.{nd}f}%")
    if mm_bits:
        lines.append(f"\n**货币市场利率**: {' | '.join(mm_bits)}")
    spread_bits = []
    if mm.get('sofr_effr_bp') is not None:
        spread_bits.append(f"SOFR-EFFR {mm['sofr_effr_bp']:+.1f}bp")
    if mm.get('tgcr_sofr_bp') is not None:
        spread_bits.append(f"TGCR-SOFR {mm['tgcr_sofr_bp']:+.1f}bp")
    if mm.get('bgcr_sofr_bp') is not None:
        spread_bits.append(f"BGCR-SOFR {mm['bgcr_sofr_bp']:+.1f}bp")
    ceil_bp = (d.get('raw') or {}).get('ceil_bp')
    if ceil_bp is not None:
        spread_bits.append(f"SOFR-央行下沿 {ceil_bp:+.1f}bp")
    if spread_bits:
        lines.append(f"\n**利差**: {' | '.join(spread_bits)}")

    srf = (d.get('raw') or {}).get('srf') or {}
    if srf.get('max_1d_5d') is not None:
        srf_txt = f"近5日最大单日 ${srf['max_1d_5d']:.0f}M"
        if srf.get('amt_5d') is not None:
            srf_txt += f"，合计 ${srf['amt_5d']:.0f}M"
        srf_txt += "（>0 表示常备回购便利被使用）"
        lines.append(f"\n**SRF 使用**: {srf_txt}")

    alerts = d.get('alerts') or []
    warns = d.get('warns') or []
    if alerts:
        lines.append("\n**🚨 水位告警**:")
        for a in alerts:
            lines.append(f"- {a}")
    if warns:
        lines.append("\n**⚠️ 关注项**:")
        for w in warns:
            lines.append(f"- {w}")
    if d.get('note'):
        lines.append(f"\n*{d['note']}*")
    return '\n'.join(lines)
