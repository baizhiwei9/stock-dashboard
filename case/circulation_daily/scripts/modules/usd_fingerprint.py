"""美元流动性指纹侦查：A–F 六维归因（纯 Python，零 LLM，全部走 data_hub/EDB）。

思路
----
美元流动性收紧从来不是单一原因。本模块把「谁在抽水」拆成六个嫌疑人，各自打分：

| 维度 | 嫌疑人 | 观测指标 |
|---|---|---|
| A | 央行（缩表） | Fed 总资产、银行准备金 |
| B | 财政（TGA 补库） | 财政部 TGA 余额 |
| C | 货币市场（结构性摩擦） | SOFR-EFFR、TGCR-SOFR、SRF 使用、ON RRP |
| D | 信用（风险溢价） | IG/HY/BB/CCC OAS |
| E | 美元与风险偏好 | DXY、VIX、标普500 |
| F | 海外（外部抽水） | 日本10Y、HIBOR 3M、离岸人民币 |

每维输出 0–100 的「紧张贡献分」，最高者为**首位嫌疑**。
再用价格（DXY / VIX / SP500）做**价格确认**：基本面显示紧、价格却没反应 → 结论降级。

对外接口
--------
- ``run(verbose=False)``     → dict（``top`` / ``step4.scores`` / ``concl`` / ``inputs`` / ``step2``）
- ``render_md(data)``        → str
- ``render_card_lines(data)``→ list[str]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

DIM_NAMES = {
    'A': '央行缩表',
    'B': '财政抽水',
    'C': '货币市场摩擦',
    'D': '信用风险溢价',
    'E': '美元与风险偏好',
    'F': '海外抽水',
}


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _rank_score(sid: str, window: int = 260, invert: bool = False) -> Optional[float]:
    """把分位直接转成紧张分：正常时低、紧张时高。invert=True 表示低分位=紧张。"""
    r = E.pct_rank(sid, window=window)
    if r is None:
        return None
    return _clip(100.0 - r if invert else r)


def _flow_score(chg: Optional[float], scale: float) -> Optional[float]:
    """按变动幅度给分：chg>0 表示抽水（紧张上升）。scale 为满分对应的变动量。"""
    if chg is None or scale == 0:
        return None
    return _clip(50.0 + chg / scale * 50.0)


def run(verbose: bool = False) -> Dict[str, Any]:
    """执行六维归因。"""
    data: Dict[str, Any] = {
        'ok': False, 'date': None,
        'step1': {}, 'step2': {'price_confirm': None, 'detail': ''},
        'step3': {}, 'step4': {'scores': {}, 'top': None, 'ranking': []},
        'top': None, 'concl': '',
        'inputs': {'spreads': {}, 'spread_trend': {}, 'levels': {}},
        'alerts': [], 'warns': [], 'note': '',
    }

    scores: Dict[str, float] = {}
    detail: Dict[str, Any] = {}

    # ---------------- A 央行缩表 ----------------
    a_parts = []
    bs = E.stat('LIQ.US.FED.BS', window=260)
    res = E.stat('LIQ.US.RESERVES', window=260)
    if bs.get('ok'):
        s = _flow_score(bs.get('chg_20d'), scale=-3.0e7)  # 缩表 300亿美元/月 = 满分偏紧
        if s is not None:
            a_parts.append(s)
        detail['fed_bs_bn'] = bs['latest'] * 1e-3
        detail['fed_bs_chg_1m_bn'] = (bs.get('chg_20d') or 0) * 1e-3
    if res.get('ok'):
        s = _rank_score('LIQ.US.RESERVES', invert=True)
        if s is not None:
            a_parts.append(s)
        detail['reserves_bn'] = res['latest'] * 1e-3
        detail['reserves_rank'] = res['rank']
    if a_parts:
        scores['A'] = round(sum(a_parts) / len(a_parts), 1)

    # ---------------- B 财政抽水（TGA） ----------------
    tga = E.stat('LIQ.US.TGA', window=260)
    if tga.get('ok'):
        b = []
        s = _flow_score(tga.get('chg_20d'), scale=2.0e5)  # TGA 一个月补 2000亿 = 满分
        if s is not None:
            b.append(s)
        s2 = _rank_score('LIQ.US.TGA')
        if s2 is not None:
            b.append(s2)
        if b:
            scores['B'] = round(sum(b) / len(b), 1)
        detail['tga_bn'] = tga['latest'] * 1e-3
        detail['tga_chg_1m_bn'] = (tga.get('chg_20d') or 0) * 1e-3
        detail['tga_rank'] = tga['rank']

    # ---------------- C 货币市场摩擦 ----------------
    c_parts = []
    sofr, _ = E.point('LIQ.US.SOFR')
    effr, _ = E.point('LIQ.US.EFFR')
    tgcr, _ = E.point('LIQ.US.TGCR')
    rrp_rate, _ = E.point('LIQ.US.RRP.RATE')
    sofr_effr = ((sofr - effr) * 100.0) if (sofr is not None and effr is not None) else None
    data['inputs']['spreads']['sofr_effr'] = sofr_effr
    if sofr_effr is not None:
        # 0bp=40分，5bp=80分，10bp=100分
        c_parts.append(_clip(40.0 + sofr_effr * 8.0))
    # SOFR-EFFR 历史分位（自算历史序列）
    s_rows = E.rows('LIQ.US.SOFR', limit=260)
    e_rows = E.rows('LIQ.US.EFFR', limit=260)
    if s_rows and e_rows:
        emap = {x['date']: x['value'] for x in e_rows}
        hist = [(x['value'] - emap[x['date']]) * 100.0 for x in s_rows if x['date'] in emap]
        if len(hist) >= 20:
            cur = hist[-1]
            below = sum(1 for v in hist if v < cur)
            pr = (below + 0.5 * sum(1 for v in hist if v == cur)) / len(hist) * 100.0
            data['inputs']['spread_trend']['sofr_effr_pct_hist'] = round(pr, 1)
            c_parts.append(_clip(pr))
            data['inputs']['spread_trend']['sofr_effr_mean'] = round(sum(hist) / len(hist), 2)
    if tgcr is not None and sofr is not None:
        tgcr_sofr = (tgcr - sofr) * 100.0
        data['inputs']['spreads']['tgcr_sofr'] = tgcr_sofr
        c_parts.append(_clip(40.0 + tgcr_sofr * 8.0))
    srf_rows = E.rows('LIQ.US.SRF', limit=5)
    # registry 标注 LIQ.US.SRF 单位为「美元」，阈值 1.0 是百万美元口径，必须先折算
    srf_max = max((x['value'] for x in srf_rows), default=None)
    if srf_max is not None:
        srf_max_mn = srf_max / 1e6
        data['inputs']['levels']['srf_max_5d_mn'] = srf_max_mn
        c_parts.append(100.0 if srf_max_mn > 1.0 else 30.0)
    rrp_bn = (E.point('LIQ.US.RRP')[0] or 0) * 1e-3
    if rrp_bn <= 0.5:
        c_parts.append(100.0)
    elif rrp_bn <= 5.0:
        c_parts.append(85.0)
    elif rrp_bn <= 50.0:
        c_parts.append(60.0)
    else:
        c_parts.append(30.0)
    data['inputs']['levels']['rrp_bn'] = round(rrp_bn, 2)
    if c_parts:
        scores['C'] = round(sum(c_parts) / len(c_parts), 1)

    # ---------------- D 信用风险溢价 ----------------
    d_parts = []
    for sid, scale in [('CREDIT.US.OAS.HY', 30.0), ('CREDIT.US.OAS.IG', 15.0),
                       ('CREDIT.US.OAS.BB', 30.0), ('CREDIT.US.OAS.CCC', 60.0)]:
        st = E.stat(sid, window=260)
        if not st.get('ok'):
            continue
        # CREDIT.US.OAS.* 的 registry 单位是 %，_flow_score 的 scale 与展示都按 bp，
        # 统一 ×100 折算，否则利差变动会被当成 0.1 量级、D 维度永远贴在中性 50 分。
        chg_bp = None if st.get('chg_20d') is None else st['chg_20d'] * 100.0
        s = _flow_score(chg_bp, scale=scale)
        if s is not None:
            d_parts.append(s)
        detail.setdefault('oas', {})[sid.split('.')[-1]] = {
            'value': st['latest'] * 100.0, 'chg_1m': chg_bp, 'rank': st['rank'],
        }
    if d_parts:
        scores['D'] = round(sum(d_parts) / len(d_parts), 1)

    # ---------------- E 美元与风险偏好 ----------------
    e_parts = []
    dxy = E.stat('GLOBAL.DXY', window=260)
    vix = E.stat('GLOBAL.VIX', window=260)
    spx = E.stat('GLOBAL.SP500', window=260)
    if dxy.get('ok'):
        s = _flow_score(dxy.get('chg_20d'), scale=3.0)  # 美元一个月涨3 = 满分紧
        if s is not None:
            e_parts.append(s)
        detail['dxy'] = dxy['latest']
        detail['dxy_chg_1m'] = dxy.get('chg_20d')
    if vix.get('ok'):
        s = _rank_score('GLOBAL.VIX')
        if s is not None:
            e_parts.append(s)
        detail['vix'] = vix['latest']
        detail['vix_rank'] = vix['rank']
    if spx.get('ok'):
        s = _flow_score(spx.get('chg_20d'), scale=-300.0)  # 标普一个月跌300 = 满分紧
        if s is not None:
            e_parts.append(s)
        detail['spx'] = spx['latest']
        detail['spx_chg_1m'] = spx.get('chg_20d')
    if e_parts:
        scores['E'] = round(sum(e_parts) / len(e_parts), 1)

    # ---------------- F 海外抽水 ----------------
    f_parts = []
    for sid, scale in [('RATE.JP.10Y', 0.3), ('LIQ.HK.HIBOR.3M', 0.5)]:
        st = E.stat(sid, window=260)
        if not st.get('ok'):
            continue
        s = _flow_score(st.get('chg_20d'), scale=scale)
        if s is not None:
            f_parts.append(s)
        detail.setdefault('overseas', {})[sid] = {
            'value': st['latest'], 'chg_1m': st.get('chg_20d'), 'rank': st['rank'],
        }
    cnh = E.stat('CN.FX.CNH', window=260)
    if cnh.get('ok'):
        s = _flow_score(cnh.get('chg_20d'), scale=0.05)  # USDCNH 一个月涨 5 分 = 满分
        if s is not None:
            f_parts.append(s)
        detail['cnh'] = cnh['latest']
        detail['cnh_chg_1m'] = cnh.get('chg_20d')
    if f_parts:
        scores['F'] = round(sum(f_parts) / len(f_parts), 1)

    if not scores:
        data['note'] = '六维归因全部取数失败（data_hub/EDB）'
        data['alerts'].append('美元流动性指纹：核心数据缺失')
        return data

    data['ok'] = True
    data['date'] = (dxy.get('date') or res.get('date') or tga.get('date') or '')
    data['step1'] = detail
    data['step4'] = {
        'scores': scores,
        'top': None,
        'ranking': sorted(scores.items(), key=lambda kv: -kv[1]),
    }
    top = data['step4']['ranking'][0][0]
    data['step4']['top'] = top
    data['top'] = top

    # ---------------- step2 价格确认 ----------------
    pc_bits = []
    pc_score = 0
    if dxy.get('chg_20d') is not None:
        if dxy['chg_20d'] > 0.5:
            pc_score += 1
            pc_bits.append(f"DXY 上行 {dxy['chg_20d']:+.2f}（美元强=流动性紧）")
        else:
            pc_bits.append(f"DXY {dxy['chg_20d']:+.2f}（未上行）")
    if vix.get('ok'):
        if vix.get('chg_5d') is not None and vix['chg_5d'] > 1.0:
            pc_score += 1
            pc_bits.append(f"VIX 上行 {vix['chg_5d']:+.2f}（风险偏好恶化）")
        else:
            pc_bits.append(f"VIX {vix['latest']:.1f}（未恶化）")
    if spx.get('chg_20d') is not None:
        if spx['chg_20d'] < -50:
            pc_score += 1
            pc_bits.append(f"标普下跌 {spx['chg_20d']:+.0f}（风险资产承压）")
        else:
            pc_bits.append(f"标普 {spx['chg_20d']:+.0f}（未承压）")

    confirm = 'confirmed' if pc_score >= 2 else ('partial' if pc_score == 1 else 'unconfirmed')
    data['step2'] = {
        'price_confirm': confirm,
        'score': pc_score,
        'detail': '；'.join(pc_bits),
    }

    # ---------------- 结论 ----------------
    top_name = DIM_NAMES.get(top, top)
    top_score = scores[top]
    sec = data['step4']['ranking'][1] if len(data['step4']['ranking']) > 1 else None
    sec_txt = f"，次位 {DIM_NAMES.get(sec[0], sec[0])}({sec[1]})" if sec else ''
    conf_txt = {
        'confirmed': '价格已确认，流动性收紧正在传导至风险资产',
        'partial': '价格部分确认，传导刚起步',
        'unconfirmed': '价格未确认，属结构性/技术性收紧，尚未传导至风险资产',
    }[confirm]
    data['concl'] = f"首位嫌疑 {top}={top_name}({top_score}分){sec_txt}；{conf_txt}"

    # ---------------- 告警 ----------------
    alerts, warns = [], []
    if top_score >= 80:
        alerts.append(f"美元流动性「{top_name}」贡献 {top_score} 分（≥80），主因高度集中")
    elif top_score >= 60:
        warns.append(f"美元流动性主因「{top_name}」{top_score} 分")
    if confirm == 'confirmed':
        alerts.append('流动性收紧已获价格确认（DXY/VIX/标普共振）')
    if sofr_effr is not None and sofr_effr >= 5.0:
        alerts.append(f"SOFR-EFFR 利差 {sofr_effr:.1f}bp（≥5bp，回购市场分层加剧）")
    data['alerts'] = alerts
    data['warns'] = warns
    data['note'] = '六维各0-100分（越高越紧）；价格确认需 DXY/VIX/标普中≥2项共振'

    if verbose:
        print(f"  首位嫌疑: {top} {top_name} ({top_score}) | {data['concl'][:80]}")
    return data


# ------------------------------------------------------------------ 渲染

def render_md(data: Dict[str, Any]) -> str:
    """日报 markdown 段。"""
    d = data or {}
    lines: List[str] = ["### 🕵️ 美元流动性指纹侦查（A–F 归因）"]
    if not d.get('ok'):
        lines.append(f"\n⚠️ {d.get('note') or '数据获取失败'}")
        return '\n'.join(lines)

    top = d.get('top')
    scores = (d.get('step4') or {}).get('scores') or {}
    lines.append(f"\n**首位嫌疑**: 🔍 **{top} {DIM_NAMES.get(top, top)}**（{scores.get(top)}分）")
    lines.append(f"\n**结论**: {d.get('concl')}")

    lines.append(f"\n| 维度 | 嫌疑人 | 紧张分 | 说明 |")
    lines.append("|---|---|---|---|")
    for dim, sc in ((d.get('step4') or {}).get('ranking') or []):
        mark = '**' if dim == top else ''
        lines.append(f"| {dim} | {mark}{DIM_NAMES.get(dim, dim)}{mark} | {sc} | {_dim_note(dim, d)} |")

    pc = d.get('step2') or {}
    conf_icon = {'confirmed': '✅', 'partial': '🟡', 'unconfirmed': '❌'}.get(pc.get('price_confirm'), '⚪')
    conf_word = {'confirmed': '已确认', 'partial': '部分确认', 'unconfirmed': '未确认'}.get(pc.get('price_confirm'), '未知')
    lines.append(f"\n**价格确认**: {conf_icon} {conf_word}（{pc.get('score', 0)}/3）")
    if pc.get('detail'):
        lines.append(f"- {pc['detail']}")

    alerts = d.get('alerts') or []
    warns = d.get('warns') or []
    if alerts:
        lines.append("\n**🚨 指纹告警**:")
        for a in alerts:
            lines.append(f"- {a}")
    if warns:
        lines.append("\n**⚠️ 关注项**:")
        for w in warns:
            lines.append(f"- {w}")
    if d.get('note'):
        lines.append(f"\n*{d['note']}*")
    return '\n'.join(lines)


def _dim_note(dim: str, d: Dict[str, Any]) -> str:
    """各维度的关键读数摘要。"""
    s1 = d.get('step1') or {}
    inp = d.get('inputs') or {}
    lv = inp.get('levels') or {}
    sp = inp.get('spreads') or {}
    if dim == 'A':
        bits = []
        if s1.get('reserves_bn') is not None:
            bits.append(f"准备金 ${s1['reserves_bn']/1e3:.2f}T")
        if s1.get('reserves_rank') is not None:
            bits.append(f"分位{s1['reserves_rank']:.0f}%")
        if s1.get('fed_bs_chg_1m_bn') is not None:
            bits.append(f"表月变{s1['fed_bs_chg_1m_bn']:+.0f}B")
        return '，'.join(bits) or 'n/a'
    if dim == 'B':
        bits = []
        if s1.get('tga_bn') is not None:
            bits.append(f"TGA ${s1['tga_bn']/1e3:.2f}T")
        if s1.get('tga_chg_1m_bn') is not None:
            bits.append(f"月变{s1['tga_chg_1m_bn']:+.0f}B")
        return '，'.join(bits) or 'n/a'
    if dim == 'C':
        bits = []
        if sp.get('sofr_effr') is not None:
            bits.append(f"SOFR-EFFR {sp['sofr_effr']:+.1f}bp")
        if lv.get('srf_max_5d_mn') is not None:
            bits.append(f"SRF {lv['srf_max_5d_mn']:.0f}M")
        if lv.get('rrp_bn') is not None:
            bits.append(f"ON RRP ${lv['rrp_bn']:.2f}B")
        return '，'.join(bits) or 'n/a'
    if dim == 'D':
        oas = s1.get('oas') or {}
        bits = []
        for k in ('HY', 'IG', 'BB', 'CCC'):
            if k in oas:
                bits.append(f"{k} {oas[k]['value']:.0f}bp({oas[k]['chg_1m']:+.0f})")
        return '，'.join(bits) or 'n/a'
    if dim == 'E':
        bits = []
        if s1.get('dxy') is not None:
            bits.append(f"DXY {s1['dxy']:.2f}({s1.get('dxy_chg_1m') or 0:+.2f})")
        if s1.get('vix') is not None:
            bits.append(f"VIX {s1['vix']:.1f}")
        if s1.get('spx') is not None:
            bits.append(f"标普 {s1['spx']:.0f}")
        return '，'.join(bits) or 'n/a'
    if dim == 'F':
        ov = s1.get('overseas') or {}
        bits = []
        if 'RATE.JP.10Y' in ov:
            bits.append(f"JGB10Y {ov['RATE.JP.10Y']['value']:.2f}%")
        if 'LIQ.HK.HIBOR.3M' in ov:
            bits.append(f"HIBOR3M {ov['LIQ.HK.HIBOR.3M']['value']:.2f}%")
        if s1.get('cnh') is not None:
            bits.append(f"USDCNH {s1['cnh']:.4f}")
        return '，'.join(bits) or 'n/a'
    return ''


def render_card_lines(data: Dict[str, Any]) -> List[str]:
    """飞书卡片行（紧凑版）。"""
    d = data or {}
    if not d.get('ok'):
        return [f"⚠️ **美元流动性指纹**: {d.get('note') or '数据获取失败'}"]
    top = d.get('top')
    scores = (d.get('step4') or {}).get('scores') or {}
    pc = d.get('step2') or {}
    conf_icon = {'confirmed': '✅', 'partial': '🟡', 'unconfirmed': '❌'}.get(pc.get('price_confirm'), '⚪')
    out = [f"🕵️ **美元流动性指纹**: 首位嫌疑 **{top} {DIM_NAMES.get(top, top)}**（{scores.get(top)}分）"]
    rank_txt = ' > '.join(f"{k}{v:.0f}" for k, v in ((d.get('step4') or {}).get('ranking') or [])[:4])
    if rank_txt:
        out.append(f"- 排序: {rank_txt}")
    out.append(f"- 价格确认: {conf_icon} {pc.get('score', 0)}/3")
    return out
