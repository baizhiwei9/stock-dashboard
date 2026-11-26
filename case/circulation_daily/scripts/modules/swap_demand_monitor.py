"""1Y 外汇掉期点 × 需求传导监控（纯 Python，零 LLM）

核心框架（用户最早需求，2026-11-13 提出）：

    rrr = R + e

    R   = 联邦基金利率（取 EFFR 日频有效利率）
    e   = 1Y USD/CNY 外汇掉期点年化贴水幅度（%，正值 = 贴水）
    rrr = 风险资产贴现率
    中性利率 = 5.5%

    rrr > 5.5%  → 限制性区间（消费、风险偏好承压）
    rrr < 5.5%  → 宽松区间
    系数约 2：e 动 1bp ≈ rrr 有效变动 2bp
    重点看 e 的边际变化，而非单日绝对值

历史水位参考（R≈3.5%-3.6% 环境）：
    e ≥ 2.9%          → 高压极值（逼近 3%，内需最大压制）
    e ≥ 2.4%          → 内需警报（rrr 重越 5.5% 向 6% 靠拢）
    1.6% ≤ e < 2.4%   → 中性偏紧
    e < 1.6%          → 宽松刺激（rrr 破 5.5%）

配套监控：
    · 五变量协调：CNH / 1Y存单 / 30Y国债 / 2Y美债 / e（不可孤立看 e）
    · 央行三响应变量：即期汇率 E / 政策利率 r / 掉期点 e（降 e = 最鸽、扩表最强）
    · 驱动变量：布油（外部冲击）/ 中国 PPI（内部升值压力）

本模块只输出「数据 + 判定规则算出的方向」，不做预测、不调用模型。
"""

from typing import Any, Dict, List, Optional, Tuple

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    import edb_helper as E

# ------------------------------------------------------------------ 常量

NEUTRAL_RATE = 5.5          # 中性利率（%）
COEF = 2.0                  # e 对 rrr 的传导系数

# 水位分档（e 的绝对值，%）
LEVEL_HIGH = 2.9            # 高压极值
LEVEL_ALERT = 2.4           # 内需警报
LEVEL_LOOSE = 1.6           # 宽松刺激

# 边际变化阈值（e 的变动，bp）
MARGIN_SIGNIFICANT_BP = 5.0

SID_SWAP = 'CN.FX.SWAP.1Y'
SID_SPOT = 'CN.FX.SPOT.USDCNY'
SID_EFFR = 'LIQ.US.EFFR'
SID_CD = 'LIQ.CN.CD.1Y'
SID_CN30 = 'RATE.CN.30Y'
SID_US2 = 'RATE.US.2Y'
SID_CNH = 'CN.FX.CNH'
SID_BRENT = 'GLOBAL.BRENT'
SID_PPI = 'CN.PPI.YOY'
SID_LPR = 'LIQ.CN.LPR.1Y'
SID_TGT_UP = 'US.FED.TARGET.UPPER'
SID_TGT_LOW = 'US.FED.TARGET.LOWER'


# ------------------------------------------------------------------ 取数

# 日报自身累积的掉期历史（circulation_daily 每天写入，250 个观测）
CACHE_FILE = '/Coze/Drive/蓁宝/所有对话/主对话/环流日报/scripts/cache.json'
DB_PATH = '/Coze/Drive/蓁宝/所有对话/主对话/环流日报/data/market.db'


def _e_series(limit: int = 300) -> List[Dict[str, Any]]:
    """1Y 掉期点年化贴水幅度序列（%），按日期升序。

    历史三级回退：
      1. ``cache.json`` 的 ``swap_history``——日报每天写入的 250 个观测，最完整，
         日报正文的走势图用的也是这份，边际变化必须与它对齐；
      2. ``market.db`` 的 ``swap_1y_history``——早期导入的散点，10/11 月有断档；
      3. EDB ``CN.FX.SWAP.1Y``——只有当日一个点，拿不到边际变化。
    各源的值都是年化率（负值=贴水），统一取绝对值作为 e。
    """
    out: List[Dict[str, Any]] = []

    # ---- 1) cache.json 的 swap_history ----
    try:
        import json
        import os
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                hist = (json.load(f) or {}).get('swap_history') or {}
            keys = sorted(hist.keys())[-limit:]
            for d in keys:
                try:
                    e_val = abs(float(hist[d]))
                except (TypeError, ValueError):
                    continue
                out.append({'date': d, 'e': e_val, 'pips': None, 'spot': None})
    except Exception:
        out = []
    if len(out) >= 2:
        return out

    # ---- 2) market.db 的 swap_1y_history ----
    try:
        import os
        import sqlite3
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect('file:%s?mode=ro' % DB_PATH, uri=True)
            try:
                rows = list(conn.execute(
                    "SELECT date, annualized_pct, pips, spot FROM swap_1y_history "
                    "WHERE annualized_pct IS NOT NULL ORDER BY date DESC LIMIT ?",
                    (limit,)))
            finally:
                conn.close()
            for d, pct, pips, spot in reversed(rows):
                try:
                    e_val = abs(float(pct))
                except (TypeError, ValueError):
                    continue
                out.append({'date': d, 'e': e_val, 'pips': pips, 'spot': spot})
    except Exception:
        out = []
    if len(out) >= 2:
        return out

    # ---- 3) EDB 单点 + 即期折算（只能拿到当日，边际变化会缺失）----
    swaps = E.rows(SID_SWAP, limit=limit)
    if not swaps:
        return []
    spot_rows = E.rows(SID_SPOT, limit=limit)
    spot_map = {r['date']: r['value'] for r in spot_rows if r.get('value')}
    fallback_spot, _ = E.point(SID_SPOT)
    last_known = None
    out = []
    for r in swaps:
        d = r.get('date')
        pips = r.get('value')
        if d is None or pips is None:
            continue
        s = spot_map.get(d)
        if s:
            last_known = s
        else:
            s = last_known or fallback_spot
        if not s:
            continue
        e_pct = abs(pips) * 0.0001 / s * 100.0
        out.append({'date': d, 'e': e_pct, 'pips': pips, 'spot': s})
    return out


def _diff_bp(series: List[Dict[str, Any]], back: int) -> Optional[float]:
    """e 相对 back 个观测前的变化（bp）。"""
    if len(series) <= back:
        return None
    return (series[-1]['e'] - series[-1 - back]['e']) * 100.0


# ------------------------------------------------------------------ 判定

def _level_tag(e_pct: Optional[float]) -> Tuple[str, str]:
    """按 e 的绝对水位分档。返回 (标签, 说明)。"""
    if e_pct is None:
        return '数据缺失', ''
    if e_pct >= LEVEL_HIGH:
        return '高压极值', f'e {e_pct:.2f}% ≥ {LEVEL_HIGH}%，逼近 3%，内需最大压制区'
    if e_pct >= LEVEL_ALERT:
        return '内需警报', f'e {e_pct:.2f}% ≥ {LEVEL_ALERT}%，rrr 重越中性向 6% 靠拢'
    if e_pct >= LEVEL_LOOSE:
        return '中性偏紧', f'e {e_pct:.2f}% 在 {LEVEL_LOOSE}%~{LEVEL_ALERT}% 区间'
    return '宽松刺激', f'e {e_pct:.2f}% < {LEVEL_LOOSE}%，rrr 破中性，刺激信号'


def _demand_call(rrr: Optional[float], e_pct: Optional[float],
                 de_bp: Optional[float]) -> Dict[str, Any]:
    """需求方向判定：水位定基调，边际定方向。

    返回 {'base','margin','verdict','icon','note'}
    """
    if rrr is None:
        return {'base': '未知', 'margin': '未知', 'verdict': '数据缺失',
                'icon': '❓', 'note': 'rrr 无法计算（缺 R 或 e）'}

    gap = rrr - NEUTRAL_RATE
    if gap > 0:
        base = '抑制'
        base_note = (f'rrr {rrr:.2f}% 高于中性 {NEUTRAL_RATE:.1f}% 达 {gap:+.2f}pp，'
                     f'风险资产贴现率处限制性区间')
    else:
        base = '提升'
        base_note = (f'rrr {rrr:.2f}% 低于中性 {NEUTRAL_RATE:.1f}% 达 {gap:+.2f}pp，'
                     f'贴现率处宽松区间')

    eff_bp = None if de_bp is None else de_bp * COEF
    if eff_bp is None:
        margin = '数据不足'
        marg_note = 'e 的边际变化无法计算'
    elif eff_bp >= MARGIN_SIGNIFICANT_BP * COEF:
        margin = '恶化'
        marg_note = f'e 变动 {de_bp:+.1f}bp → rrr 有效 {eff_bp:+.1f}bp，抑制加强'
    elif eff_bp <= -MARGIN_SIGNIFICANT_BP * COEF:
        margin = '改善'
        marg_note = f'e 变动 {de_bp:+.1f}bp → rrr 有效 {eff_bp:+.1f}bp，抑制缓解'
    else:
        margin = '持平'
        marg_note = f'e 变动 {de_bp:+.1f}bp → rrr 有效 {eff_bp:+.1f}bp，边际无实质变化'

    # 综合结论
    if base == '抑制' and margin == '恶化':
        verdict, icon = '抑制（边际加强）', '⛔'
    elif base == '抑制' and margin == '改善':
        verdict, icon = '抑制（边际缓解）', '⚠️'
    elif base == '抑制':
        verdict, icon = '抑制（边际持平）', '⚠️'
    elif base == '提升' and margin == '恶化':
        verdict, icon = '提升（边际减弱）', '🟡'
    elif base == '提升':
        verdict, icon = '提升（边际增强）', '✅'
    else:
        verdict, icon = base, '🟡'

    return {'base': base, 'margin': margin, 'verdict': verdict, 'icon': icon,
            'note': f'{base_note}；{marg_note}', 'gap': gap, 'eff_bp': eff_bp}


def _five_vars() -> Dict[str, Any]:
    """五变量协调：CNH / 1Y存单 / 30Y国债 / 2Y美债 / e。"""
    specs = [
        ('CNH', SID_CNH, 'CNY/USD', 5, '升=人民币贬值'),
        ('1Y存单', SID_CD, '%', 5, '升=人民币利率上行'),
        ('30Y国债', SID_CN30, '%', 5, '升=长端上行'),
        ('2Y美债', SID_US2, '%', 5, '升=美元利率上行'),
    ]
    rows = []
    for name, sid, unit, back, note in specs:
        st = E.stat(sid, window=260)
        if not st.get('ok'):
            rows.append({'name': name, 'value': None, 'chg': None, 'unit': unit,
                         'dir': '缺失', 'note': note})
            continue
        chg = st.get(f'chg_{back}d')
        # 汇率类变动是「元」，换算成「点」(×10000)；利率类变动是「%」，换算成 bp(×100)
        chg_std = None
        if chg is not None:
            chg_std = chg * 10000.0 if unit == 'CNY/USD' else chg * 100.0
        if chg_std is None:
            d = '—'
        elif chg_std > 0:
            d = '↑'
        elif chg_std < 0:
            d = '↓'
        else:
            d = '→'
        rows.append({'name': name, 'value': st['latest'], 'chg': chg_std,
                     'unit': unit, 'dir': d, 'note': note})
    return {'rows': rows}


def _three_tools() -> Dict[str, Any]:
    """央行三响应变量：即期汇率 E / 政策利率 r / 掉期点 e。

    降 e = 最鸽、扩表最强。
    """
    spot, spot_date = E.point(SID_SPOT)
    spot_chg = E.chg(SID_SPOT, 5)
    lpr, lpr_date = E.point(SID_LPR)
    tgt_up, _ = E.point(SID_TGT_UP)
    tgt_low, _ = E.point(SID_TGT_LOW)
    return {
        'spot': spot, 'spot_date': spot_date, 'spot_chg': spot_chg,
        'lpr': lpr, 'lpr_date': lpr_date,
        'target': (tgt_low, tgt_up) if (tgt_low is not None and tgt_up is not None) else None,
    }


def _drivers() -> Dict[str, Any]:
    """驱动变量：布油（外部冲击）/ 中国 PPI（内部升值压力）。"""
    out = {}
    for key, sid, label in (('brent', SID_BRENT, '布伦特原油'),
                            ('ppi', SID_PPI, '中国PPI同比')):
        st = E.stat(sid, window=60)
        if st.get('ok'):
            out[key] = {'label': label, 'value': st['latest'], 'date': st.get('date'),
                        'chg_20d': st.get('chg_20d'), 'rank': st.get('rank')}
        else:
            out[key] = {'label': label, 'value': None, 'date': None,
                        'chg_20d': None, 'rank': None}
    return out


# ------------------------------------------------------------------ 主入口

def run_swap_monitor() -> Dict[str, Any]:
    """计算掉期点 × 需求传导全量指标。"""
    data: Dict[str, Any] = {'ok': False}

    es = _e_series()
    if not es:
        data['error'] = 'CN.FX.SWAP.1Y 无数据'
        return data

    cur = es[-1]
    e_pct = cur['e']

    # 当日的 pips / 即期单独取（cache.json 的历史只有年化率，没有原始点）
    pips_now, _ = E.point(SID_SWAP)
    spot_now, _ = E.point(SID_SPOT)
    if pips_now is None:
        pips_now = cur.get('pips')
    if spot_now is None:
        spot_now = cur.get('spot')

    effr, effr_date = E.point(SID_EFFR)
    rrr = (effr + e_pct) if effr is not None else None

    d1 = _diff_bp(es, 1)
    d5 = _diff_bp(es, 5)
    d20 = _diff_bp(es, 20)

    tag, tag_note = _level_tag(e_pct)
    demand = _demand_call(rrr, e_pct, d5)

    data.update({
        'ok': True,
        'date': cur['date'],
        'e': e_pct,
        'pips': pips_now,
        'spot': spot_now,
        'R': effr,
        'R_date': effr_date,
        'rrr': rrr,
        'neutral': NEUTRAL_RATE,
        'gap': demand.get('gap'),
        'level_tag': tag,
        'level_note': tag_note,
        'chg': {'d1': d1, 'd5': d5, 'd20': d20},
        'demand': demand,
        'five': _five_vars(),
        'three': _three_tools(),
        'drivers': _drivers(),
        'e_rank': _e_percentile(es),
    })
    return data


def _e_percentile(es: List[Dict[str, Any]]) -> Optional[float]:
    """e 在近 260 个观测中的分位（越高=贴水越深）。"""
    if len(es) < 20:
        return None
    window = [x['e'] for x in es[-260:]]
    cur = window[-1]
    below = sum(1 for v in window if v < cur)
    return round(below / len(window) * 100.0, 1)


# ------------------------------------------------------------------ 渲染

def _fmt(v: Optional[float], nd: int = 2, suffix: str = '') -> str:
    if v is None:
        return '—'
    return f'{v:.{nd}f}{suffix}'


def _fmt_chg(v: Optional[float], nd: int = 1, suffix: str = 'bp') -> str:
    if v is None:
        return '—'
    return f'{v:+.{nd}f}{suffix}'


def render_report(data: Dict[str, Any]) -> str:
    """输出带标记行的纯文本，由 circulation_daily 解析成 markdown。"""
    if not data.get('ok'):
        return f"【数据说明】掉期点×需求传导不可用：{data.get('error', '未知错误')}"

    L: List[str] = []
    dm = data['demand']

    L.append(f"【核心读数】e {data['e']:.2f}% | R {_fmt(data['R'], 2, '%')} | "
             f"rrr {_fmt(data['rrr'], 2, '%')} | 中性 {data['neutral']:.1f}% | "
             f"偏离 {data['gap']:+.2f}pp" if data['rrr'] is not None else
             f"【核心读数】e {data['e']:.2f}% | R 数据缺失 | rrr 无法计算")

    L.append(f"【需求判定】{dm['icon']} {dm['verdict']}")
    L.append(f"【水位分档】{data['level_tag']}｜{data['level_note']}")
    L.append(f"【判定依据】{dm['note']}")

    d1, d5, d20 = data['chg']['d1'], data['chg']['d5'], data['chg']['d20']
    L.append(f"【边际方向】日 {_fmt_chg(d1)} | 周 {_fmt_chg(d5)} | 月 {_fmt_chg(d20)}"
             f"｜系数×{COEF:.0f}：周变动对应 rrr 有效 {_fmt_chg(None if d5 is None else d5 * COEF)}")

    # 五变量
    bits = []
    for r in data['five']['rows']:
        if r['value'] is None:
            bits.append(f"{r['name']} 缺失")
        else:
            _u = '点' if r['unit'] == 'CNY/USD' else 'bp'
            bits.append(f"{r['name']} {_fmt(r['value'], 4 if r['name'] == 'CNH' else 2)}"
                        f"({r['dir']}{_fmt_chg(r['chg'], 0 if _u == '点' else 1, _u)})")
    L.append("【五变量协调】" + " | ".join(bits))

    # 三响应变量
    t = data['three']
    spot_txt = (f"即期 {_fmt(t['spot'], 4)}({_fmt_chg(t['spot_chg'], 0, '点')})"
                if t['spot'] is not None else '即期 缺失')
    r_txt = f"政策利率 LPR1Y {_fmt(t['lpr'], 2, '%')}" if t['lpr'] is not None else '政策利率 缺失'
    e_txt = f"掉期点 e {data['e']:.2f}%"
    tool_state = '未启动宽松工具（e 高位、政策利率未动）' if data['e'] >= LEVEL_ALERT else '有宽松空间'
    L.append(f"【三响应变量】{spot_txt} | {r_txt} | {e_txt}｜{tool_state}")

    # 驱动变量
    brent = data['drivers']['brent']
    ppi = data['drivers']['ppi']
    brent_txt = (f"布油 ${_fmt(brent['value'])}（月变 {_fmt_chg(brent['chg_20d'], 2, '')}）"
                 if brent['value'] is not None else '布油 缺失')
    ppi_txt = (f"中国PPI {_fmt(ppi['value'], 1, '%')}（{ppi['date'] or '—'}）"
               if ppi['value'] is not None else '中国PPI 缺失')
    L.append(f"【驱动变量】{brent_txt} | {ppi_txt}")

    _pips_txt = f"{data['pips']:.0f}pips" if data.get('pips') is not None else '—'
    _spot_txt = f"{data['spot']:.4f}" if data.get('spot') is not None else '—'
    L.append(f"【原始数据】CFETS 1Y掉期 {_pips_txt} | 即期 {_spot_txt} | "
             f"e 近1年分位 {_fmt(data['e_rank'], 1, '%')} | 数据日期 {data['date']}")

    L.append("【数据说明】e=|掉期pips|×0.0001÷即期×100%；rrr=联邦基金有效利率(EFFR)+e；"
             f"中性利率{NEUTRAL_RATE}%；e 对 rrr 传导系数×{COEF:.0f}；"
             f"水位档：≥{LEVEL_HIGH}%高压极值 / ≥{LEVEL_ALERT}%内需警报 / <{LEVEL_LOOSE}%宽松刺激。"
             "全部走 data_hub/EDB，纯程序判定，无模型参与。")

    return '\n'.join(L)


if __name__ == '__main__':
    d = run_swap_monitor()
    print(render_report(d))
    print()
    import json
    print(json.dumps({k: v for k, v in d.items() if k not in ('five', 'three', 'drivers')},
                     ensure_ascii=False, indent=2, default=str))
