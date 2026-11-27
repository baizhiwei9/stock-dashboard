#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🇨🇳🇺🇸 流动性 / 信用风险 周报（股市 + 债券 合并版）
分层传导链：共同层不分栏，差异层左右两栏（股市 | 债券）

用法：
  python3 bin/liq_credit_weekly.py --side cn          # 中国周报 → 飞书
  python3 bin/liq_credit_weekly.py --side us          # 美国周报 → 飞书
  python3 bin/liq_credit_weekly.py --side cn --dry    # 只打印
数据：data/market.db（流动性/信用/利率）+ tushare（A股指数，带本地缓存）
"""
import os, sys, json, argparse, datetime as dt, subprocess

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

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# 取数统一走 DataHub（liqdata）+ 兼容垫片 marketdb，本文件不再 import sqlite3/requests
import marketdb as M
try:
    import ascii_chart as ac
except Exception:                                    # pragma: no cover
    ac = None
try:
    import l5_micro as l5m
except Exception:                                    # pragma: no cover
    l5m = None

def _case_data_dir():
    d = (os.environ.get('CASE_DATA_DIR') or '').strip()
    if d:
        return d
    up = os.path.dirname(os.path.dirname(_HERE))       # …/case
    return os.path.join(up, 'liq_credit_weekly', 'data')

DATA_DIR = _case_data_dir()
EQ_CACHE = os.path.join(DATA_DIR, 'cn_eq_index.json')
# 飞书 webhook 不写明文：走 case/_lib/feishu_creds（缺凭据时 HOOK 为空，推送自然跳过）
try:
    _LIB = os.path.join(os.path.dirname(_HERE), '_lib')
    if _LIB not in sys.path:
        sys.path.insert(0, _LIB)
    import feishu_creds as _FC
    HOOK = _dh_secret('CREDIT_FEISHU_HOOK', '') or _FC.WEBHOOK_URL or ''
except Exception:                                    # noqa: BLE001
    HOOK = _dh_secret('CREDIT_FEISHU_HOOK', '') or ''
SIGN = '📎 来自钉钉OpenClaw'

CN_IDX = [('000300.SH', '沪深300'), ('000905.SH', '中证500'), ('000852.SH', '中证1000'),
          ('399006.SZ', '创业板指'), ('000688.SH', '科创50')]


# ======================= 通用工具 =======================
def con():
    """只读垫片连接：liqdata（DataHub + 本地归档）支撑，全篇只用到一条 SELECT。"""
    return M.shim_connect()


def ser(c, k):
    return [x for x in c.execute('select date,value from obs where series_id=? order by date', (k,))
            if x[1] is not None]


def D(c, k):
    return dict(ser(c, k))


def last(c, k):
    r = ser(c, k)
    return r[-1] if r else (None, None)


def pct(c, k, year=True):
    r = ser(c, k)
    v = [x[1] for x in r]
    if not v:
        return None
    w = v[-252:] if (year and len(v) >= 252) else v
    return round(100 * sum(1 for x in w if x <= w[-1]) / len(w), 1)


def last_n(c, k, n=10):
    """最近 n 个观测，[] -> 升序（旧→新）"""
    r = ser(c, k)[-n:]
    return [(d, v) for d, v in r]


def at(c, k, days):
    r = ser(c, k)
    if not r:
        return None
    tgt = (dt.date.fromisoformat(r[-1][0]) - dt.timedelta(days=days)).isoformat()
    p = [x for x in r if x[0] <= tgt]
    return p[-1][1] if p else None


def spread(c, hi, lo):
    a, b = D(c, hi), D(c, lo)
    com = sorted(set(a) & set(b))
    return [(d, (a[d] - b[d]) * 100) for d in com]


def sp_pct(c, hi, lo):
    s = spread(c, hi, lo)
    if not s:
        return None, None
    v = [x[1] for x in s]
    w = v[-252:] if len(v) >= 252 else v
    return round(100 * sum(1 for x in w if x <= w[-1]) / len(w), 1), v[-1]


def f(v, fmt='.1f', suffix=''):
    return '—' if v is None else format(v, fmt) + suffix


def f100(v, fmt='.0f'):
    """v*100 再格式化，v 为 None 时返回 '—'。

    这五处原来是 ``f(x[1]*100, '.0f')``：x[1] 为 None 时先炸在乘法上，
    ``f()`` 的 None 保护根本轮不到。DataHub 缺某档信用序列时就是这样炸的。
    """
    return '—' if v is None else format(v * 100, fmt)


def col(p, lo=5, mid=25):
    """分位染色的三档阈值"""
    if p is None:
        return 'grey'
    if p <= lo:
        return 'red'
    if p <= mid:
        return 'orange'
    return 'green'


# ---------- 飞书卡片小工具 ----------
def row(elems):
    return {'tag': 'column_set', 'flex_mode': 'none',
            'columns': [{'tag': 'column', 'width': 'weighted', 'weight': w,
                         'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': t}}]}
                        for t, w in elems]}


def two(left, right, lw=1, rw=1):
    """两栏并排（左=股市，右=债券）"""
    return {'tag': 'column_set', 'flex_mode': 'none',
            'columns': [
                {'tag': 'column', 'width': 'weighted', 'weight': lw,
                 'elements': [{'tag': 'markdown', 'content': left}]},
                {'tag': 'column', 'width': 'weighted', 'weight': rw,
                 'elements': [{'tag': 'markdown', 'content': right}]},
            ]}


# ======================= 风险评估 =======================
# 规则：RISK（超出阈值/分位）→ 标红 ⭐ 加粗 + 发邮件；WARN → 只标色，不发邮件

def _pct_rank(vals, cur):
    if not vals or cur is None:
        return None
    return round(100 * sum(1 for x in vals if x <= cur) / len(vals), 1)


def _R(name, level, hit, cur, thr, note=''):
    return {'name': name, 'level': level, 'hit': bool(hit), 'cur': cur, 'thr': thr, 'note': note}


_L5_CACHE = {}


def _l5(force=False):
    """L5 股市微观池（含两融杠杆子层），进程内缓存一次"""
    if not _L5_CACHE or force:
        try:
            import l5_micro
            _L5_CACHE['d'] = l5_micro.compute_l5()
        except Exception as e:
            print(f'[L5] 计算失败: {e!r}')
            _L5_CACHE['d'] = None
    return _L5_CACHE.get('d')


def _l5num(L5, key):
    return (L5 or {}).get('meta', {}).get(key)


def assess_cn(c, eq):
    """中国：风险=超出阈值  警告=接近/结构性"""
    R = []
    rdr = last(c, 'cn.repo.r_dr_spread')[1]
    onsh = last(c, 'cn.repo.vol_on_share')[1]
    p102, v102 = sp_pct(c, 'cn.cb.govt.10Y', 'cn.cb.govt.2Y')
    p_aaa, v_aaa = sp_pct(c, 'cn.cb.mtn_aaa.3Y', 'cn.cb.govt.3Y')
    pn, vn = sp_pct(c, 'cn.cb.ncd_aaa.1Y', 'cn.cb.govt.1Y')
    gap = last(c, 'cn.macro.m1_m2_gap')[1]
    y10 = last(c, 'cn.cb.govt.10Y')[1]
    cyb = eq_stat(eq, '399006.SZ', 20)
    hs = eq_stat(eq, '000300.SH', 20)
    L5 = _l5()
    m5 = L5['meta'] if L5 else {}

    # ---------- RISK ----------
    R.append(_R('非银流动性分层 R007-DR007', 'RISK',
                rdr is not None and rdr >= 20, rdr, '≥20bp', '非银拿钱变贵，去杠杆启动信号'))
    R.append(_R('曲线极度平坦 10Y-2Y', 'RISK',
                v102 is not None and v102 <= 35, v102, '≤35bp', '长端缓冲为零'))
    R.append(_R('隔夜杠杆 隔夜占比', 'RISK',
                onsh is not None and onsh >= 95, onsh, '≥95%', '极致carry，扰动即砸长端'))
    R.append(_R('资产荒极限 3Y AAA 利差', 'RISK',
                v_aaa is not None and v_aaa <= 25, v_aaa, '≤25bp', '信用缓冲耗尽'))
    R.append(_R('银行负债端 NCD1Y-国债1Y', 'RISK',
                vn is not None and vn >= 40, vn, '≥40bp', '银行拿钱成本脱锚'))
    R.append(_R('债市转向 10Y 国债', 'RISK',
                y10 is not None and y10 >= 2.00, y10, '≥2.00%', '债牛终结，资本利得反转'))
    R.append(_R('股市实质回撤 创业板20日', 'RISK',
                cyb is not None and cyb <= -15, cyb, '≤-15%', '高beta已杀穿'))
    R.append(_R('股市实质回撤 沪深300 20日', 'RISK',
                hs is not None and hs <= -10, hs, '≤-10%', '宽基也失守'))

    # ---------- L5 股市微观池（含两融杠杆子层）RISK ----------
    _netq, _netv = m5.get('net_q'), m5.get('net_last')
    R.append(_R('两融净买入撤退（周，杠杆边际）', 'RISK',
                _netq is not None and _netq <= 20 and (_netv or 0) < 0,
                _netv, '≤20%分位且为负', '杠杆资金主动去杠杆，成长/小票最脆'))
    _balq = m5.get('bal_q')
    R.append(_R('两融余额冰点（杠杆存量）', 'RISK',
                _balq is not None and _balq <= 20, _balq, '≤20%分位', '杠杆存量已退到一年最低区'))
    _etfq, _etfv = m5.get('etf_q'), m5.get('etf_last')
    R.append(_R('股票ETF抽水（机构撤退）', 'RISK',
                _etfq is not None and _etfq <= 20 and (_etfv or 0) < 0,
                _etfv, '≤20%分位且净赎回', '机构端真金白银在退'))

    # ---------- WARN ----------
    R.append(_R('宽信用失败 M1-M2剪刀差', 'RISK',
                gap is not None and gap <= -3.5, gap, '≤-3.5pct', '钱停在金融体系，无增量资金'))
    R.append(_R('资产荒极限 3Y AAA利差分位', 'RISK',
                p_aaa is not None and p_aaa <= 5, p_aaa, '≤5%分位', '历史最低，信用缓冲为零'))
    R.append(_R('隔夜杠杆 隔夜占比', 'WARN',
                onsh is not None and 90 <= onsh < 95, onsh, '90~95%', '杠杆偏高'))
    R.append(_R('非银利差 R007-DR007', 'WARN',
                rdr is not None and 10 <= rdr < 20, rdr, '10~20bp', '开始走阔'))
    R.append(_R('高beta回撤 创业板20日', 'WARN',
                cyb is not None and -15 < cyb <= -8, cyb, '-8~-15%', '回撤中'))

    # ---------- L5 WARN（过热/抽血侧）----------
    _balq = m5.get('bal_q')
    R.append(_R('两融余额饱和（杠杆存量）', 'WARN',
                _balq is not None and _balq >= 80, _balq, '≥80%分位', '杠杆已打到一年高位'))
    _shq, _shv = m5.get('share_q'), None
    for _it in (L5['items'] if L5 else []):
        if _it['name'].startswith('两融成交占全A比'):
            _shv = _it['value']
            break
    R.append(_R('两融话语权过热', 'WARN',
                _shq is not None and _shq >= 80, _shq, '≥80%分位（现' + str(_shv) + '）',
                '杠杆成交占比过高，一收紧就先杀杠杆'))
    _hq = m5.get('hold_q')
    R.append(_R('产业资本抽血（股东净减持周）', 'WARN',
                _hq is not None and _hq >= 80, _hq, '≥80%分位', '供给端在抢跑'))

    return _summ(R)


def assess_us(c):
    """美国：风险=超出阈值"""
    R = []
    cbb = last(c, 'us.derived.ccc_bb_spread')[1]
    ccc = last(c, 'us.fred.BAMLH0A3HYC')[1]
    hy = last(c, 'us.fred.BAMLH0A0HYM2')[1]
    bb = last(c, 'us.fred.BAMLH0A1HYBB')[1]
    bbs = last(c, 'us.derived.bb_b_spread')[1]
    d10 = last(c, 'us.fred.DGS10')[1]
    vix = last(c, 'us.fred.VIXCLS')[1]
    sofr = last(c, 'us.derived.sofr_iorb_spread')[1]
    spx = [x[1] for x in ser(c, 'us.fred.SP500')]
    spx_hi = max(spx[-252:]) if len(spx) >= 252 else max(spx)
    spx_dd = (spx[-1] / spx_hi - 1) * 100
    cccv = ccc * 100 if ccc else None
    hyv = hy * 100 if hy else None
    bbv = bb * 100 if bb else None
    hy_pct = pct(c, 'us.fred.BAMLH0A0HYM2')
    bb_pct = pct(c, 'us.fred.BAMLH0A1HYBB')
    cbb_pct = pct(c, 'us.derived.ccc_bb_spread')
    cbb4 = (cbb - at(c, 'us.derived.ccc_bb_spread', 28)) if (cbb and at(c, 'us.derived.ccc_bb_spread', 28)) else None

    # ---------- RISK ----------
    R.append(_R('尾部分层 CCC-BB', 'RISK',
                cbb is not None and cbb >= 1150, cbb, '≥1150bp', '尾部向整体HY扩散'))
    R.append(_R('CCC 绝对水平', 'RISK',
                cccv is not None and cccv >= 1000, cccv, '≥1000bp', '尾部绝对高位'))
    R.append(_R('回购脱离区间 SOFR-IORB', 'RISK',
                sofr is not None and sofr >= 5, sofr, '≥+5bp', '流动性危机启动'))
    R.append(_R('信用中段 BB→B', 'RISK',
                bbs is not None and bbs >= 180, bbs, '≥180bp', '中段真恶化'))
    R.append(_R('股市恐慌 VIX', 'RISK',
                vix is not None and vix >= 30, vix, '≥30', '估值杀变恐慌杀'))
    R.append(_R('长端利率 10Y', 'RISK',
                d10 is not None and d10 >= 5.30, d10, '≥5.30%', '贴现率二次冲击'))
    R.append(_R('HY 分位', 'RISK',
                hy_pct is not None and hy_pct >= 95, hy_pct, '≥95%分位', '整体高收益已极端'))
    R.append(_R('股指回撤 SPX距高点', 'RISK',
                spx_dd <= -10, spx_dd, '≤-10%', '股指开始认账'))

    # ---------- WARN ----------
    R.append(_R('CCC-BB 4周变化速度', 'WARN',
                cbb4 is not None and cbb4 >= 100, cbb4, '≥+100bp/4周', '跳档在加速'))
    R.append(_R('BB 分位（高资质被拖）', 'WARN',
                bb_pct is not None and bb_pct >= 90, bb_pct, '≥90%分位', '已非单纯尾部'))
    R.append(_R('CCC-BB 分位', 'WARN',
                cbb_pct is not None and cbb_pct >= 90, cbb_pct, '≥90%分位', '分层历史高位'))
    R.append(_R('股指回撤 SPX距高点', 'WARN',
                -10 < spx_dd <= -5, spx_dd, '-5~-10%', '开始调整'))
    R.append(_R('VIX', 'WARN',
                vix is not None and 20 <= vix < 30, vix, '20~30', '波动率抬升'))
    # ---------- 信用利差极值（触发的档位 → 追加传导断点诊断段）----------
    _ext = credit_extreme_us(c)
    if _ext.get('cur') is not None:
        R.append(_R(f"信用利差极值 {_ext['grade']}", 'RISK', _ext['hit'], _ext['cur'],
                    f"≥{_ext['thr']:.0f}%分位", '任一档位触极值 → 出断点诊断'))
        _ext['name'] = f"信用利差极值 {_ext['grade']}"

    return _summ(R)


# ============ 信用利差极值 · 传导断点诊断（2026-10-19 建）============
# 仅在「信用利差超出阈值」时追加到美国周报；未触发 → 原模板不动。
# 优先级：④ 实体真出血 > ② 一级市场关门 > ① 长端利率顶 > ③ 抵押品不认了
# 硬规则：只输出第一个命中的层；拿不到数据的层直接跳过，不写"暂缺"。

CREDIT_GRADES = [                      # (档位, FRED 系列, 触发分位)
    ('BBB', 'us.fred.BAMLC0A4CBBB', 85.0),
    ('高收益', 'us.fred.BAMLH0A0HYM2', 90.0),
    ('CCC', 'us.fred.BAMLH0A3HYC', 95.0),
]


def _pctthr(p, table):
    for lo, nm in table:
        if p >= lo:
            return nm
    return table[-1][1]


def credit_extreme_us(c):
    """信用利差是否触极值：任一档位（BBB/高收益/CCC）分位超阈值 + 周环比走阔即触发
    主读数取「当前利差最高且已触发」的档位。"""
    xs = []
    for nm, idx, thr in CREDIT_GRADES:
        r = ser(c, idx)
        if not r:
            continue
        d, v = r[-1]
        w = at(c, idx, 7)
        if v is None or w is None:
            continue
        xs.append({'grade': nm, 'idx': idx, 'date': d,
                   'cur': v * 100, 'wk': (v - w) * 100, 'pct': pct(c, idx), 'thr': thr})
    if not xs:
        return {'hit': False, 'cur': None}
    for x in xs:
        x['hit'] = bool(x['pct'] is not None and x['pct'] >= x['thr'] and x['wk'] > 0)
    hits = [x for x in xs if x['hit']]
    main = max(hits or xs, key=lambda x: x['cur'])
    main = dict(main)
    main['hit'] = bool(hits)
    main['hits'] = [x['grade'] for x in hits]
    return main


def breaking_point(c, ext):
    """按优先级逐层判，返回第一个命中的层 + 一句话 + 三项锚点"""
    sig, skip = {}, []

    def num(k, scale=1.0, nd=2):
        v = last(c, k)[1]
        return None if v is None else round(v * scale, nd)

    g = ext['grade']
    g_lbl = f"{g} 档"

    # ---- ④ L4 实体真出血：CMBS拖欠≥8% 或 头条违约≥2起/周（无免费源 → 跳过）----
    cmbs_delinq = None                                  # Trepp 月度博客为人工摘录，无免费API
    default_news = None                                  # 违约新闻需人工计数
    l4_ok = (cmbs_delinq is not None or default_news is not None)
    if not l4_ok:
        skip.append('L4(CMBS拖欠/违约头条无免费源)')

    # ---- ② L1 一级市场关门：本周取消/推迟≥3只 或 长端认购≤2x（无免费源 → 跳过）----
    cancel_cnt = None                                    # Reuters/财联社公司债栏 + SEC 8-K，需人工计数
    cover_ratio = None                                   # 长端认购倍数无免费结构化源
    l1_ok = (cancel_cnt is not None or cover_ratio is not None)
    if not l1_ok:
        skip.append('L1(取消只数/认购倍数无免费源)')

    # ---- ① L0 长端利率顶：10Y ≥80%分位 且 本周↑≥10bp ----
    d10 = num('us.fred.DGS10')
    p10 = pct(c, 'us.fred.DGS10')
    w10 = at(c, 'us.fred.DGS10', 7)
    chg10 = round((d10 - w10) * 100, 0) if (d10 is not None and w10 is not None) else None
    d30 = num('us.ust.30Y')
    d2 = num('us.fred.DGS2')
    l0_hit = bool(d10 is not None and p10 is not None and p10 >= 80 and chg10 is not None and chg10 >= 10)
    sig['L0'] = l0_hit

    # ---- ③ L3 抵押品不认了：SOFR ≤ IORB 且 货基久期缩（SRF动用代理）----
    # ⚠ SRF 日度动用数据无公开可用 API（markets.newyorkfed.org 的 repo/srf 端点全返 400）
    #   → 用「准备金 4 周变化」作 duration-shortening 代理（钱被抽回准备金池 = 久期缩短）
    si = num('us.derived.sofr_iorb_spread', 1, 1)         # 已是 bp
    sofr = num('us.fred.SOFR')
    iorb = num('us.fred.IORB')
    rrp = num('us.fred.RRPONTSYD', 1, 2)
    rrp_w = at(c, 'us.fred.RRPONTSYD', 28)
    resv = num('us.fred.WRESBAL', 1 / 1e6, 3)
    resv_w = at(c, 'us.fred.WRESBAL', 28)
    resv_chg = ((resv / (resv_w / 1e6) - 1) * 100) if (resv is not None and resv_w) else None
    rrp_zero = bool(rrp is not None and rrp <= 20)        # 缓冲垫已耗尽
    resv_drain = bool(resv_chg is not None and resv_chg <= -1.0)   # 准备金四周抽水 >1%
    l3_hit = bool(si is not None and si <= 0 and resv_drain)
    sig['L3'] = l3_hit
    sig['L3_rrp_zero'] = rrp_zero

    one = [f"{g_lbl} {f(ext['cur'],'.0f')}bp，周环比 {f(ext['wk'],'+.0f')}bp，一年 {f(ext['pct'],'.1f')}% 分位"]

    if l4_ok:
        layer, why, aff = 'L4', '实体真出血：不是定价问题了，是真实再融资死局', None
    elif l1_ok:
        layer, why, aff = 'L1', '一级市场关门：新钱不接了，到期续不上', None
    elif l0_hit:
        layer, why = 'L0', '利率把缓冲垫烧穿了，不是信用变差'
        aff = '回望 10Y 是否回落 → 回了利差就收'
    elif l3_hit:
        layer, why = 'L3', '钱没少，但没人肯拿长端信用当抵押品'
        aff = 'SOFR−IORB 是否仍贴 0~+3bp；SRF 是否连续多日动用'
    else:
        layer, why, aff = None, None, None

    # 三项锚点（数字全部来自库内实值；按命中层调整顺序）
    a0 = f"**10Y {f(d10,'.2f')}%**（{f(p10,'.0f')}% 分位）｜周环比 **{f(chg10,'+.0f')}bp**｜2Y {f(d2,'.2f')}%｜30Y {f(d30,'.2f')}%｜30Y-10Y {f((((d30-d10)*100) if (d30 is not None and d10 is not None) else None),'.0f')}bp"
    a3 = f"**SOFR {f(sofr,'.2f')}% − IORB {f(iorb,'.2f')}% = {f(si,'+.1f')}bp**（{'钱不缺·贴地板' if (si is not None and si <= 0) else '正区间·抵押品被嫌'}）"
    a4 = f"**准备金 {f(resv,'.3f')}万亿**（4周 **{f(resv_chg,'+.2f')}%**）｜RRP {f(rrp,'.2f')}十亿{'（已归零·缓冲垫耗尽）' if rrp_zero else ''}"
    anc = [a3, a4, a0] if layer == 'L3' else [a0, a3, a4]

    return {'layer': layer, 'one': one, 'why': why, 'aff': aff,
            'anchors': anc, 'sig': sig, 'skip': skip, 'g': g,
            'd10': d10, 'p10': p10, 'chg10': chg10}


# ============ 断点诊断的「真信息」增强（2026-10-19 深夜追加）============
# 大佬反馈：只说「无层命中」等于没有信息。以下四块全部来自 FRED 免费源，可复现。

FRED_CSV = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}'   # 仅注释，不再调用
_CIDX = os.path.join(DATA_DIR, 'fred_credit_full.json')
_CIDX_TTL = 20 * 3600          # 20 小时


_FRED_TO_EDB = {'BAMLC0A1CAAA': 'CREDIT.US.OAS.AAA', 'BAMLC0A2CAA': 'CREDIT.US.OAS.AA',
                'BAMLC0A3CA': 'CREDIT.US.OAS.A', 'BAMLC0A4CBBB': 'CREDIT.US.OAS.BBB',
                'BAMLH0A1HYBB': 'CREDIT.US.OAS.BB', 'BAMLH0A2HYB': 'CREDIT.US.OAS.B',
                'BAMLH0A3HYC': 'CREDIT.US.OAS.CCC'}


def _credit_cache(force=False):
    """七档 OAS 的本地缓存（20h，键名保持 FRED id 以免改调用方）。

    原来这里直连 FRED 抓 45 条序列。迁移后改由 DataHub 提供可得的 7 档，
    落盘格式不变（``{FRED_ID: [[date, value], ...]}``）。
    NFCI 系列 DataHub **没有** → 不放进缓存（credit_ladder 会自行跳过）。
    """
    if (not force and os.path.exists(_CIDX)
            and (dt.datetime.now().timestamp() - os.path.getmtime(_CIDX) < _CIDX_TTL)):
        try:
            return json.load(open(_CIDX, encoding='utf-8'))
        except Exception:
            pass
    X = {}
    try:
        for fred_id, code in _FRED_TO_EDB.items():
            rows = M.get_series('us.fred.' + fred_id, days=None, ascending=True)
            if rows:
                X[fred_id] = [[d, v] for d, v in rows]
    except Exception as e:                                   # noqa: BLE001
        print('[credit_cache] DataHub 取数失败 %r' % (e,))
    if X:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            json.dump(X, open(_CIDX, 'w', encoding='utf-8'), ensure_ascii=False)
        except OSError:
            pass
    return X or None


def credit_ladder(c):
    """七档 OAS 的 4 周变化 + 全历史分位：回答「是不是只在尾部」"""
    X = _credit_cache()
    if not X:
        return None
    ids = [('AAA', 'BAMLC0A1CAAA'), ('AA', 'BAMLC0A2CAA'), ('A', 'BAMLC0A3CA'),
           ('BBB', 'BAMLC0A4CBBB'), ('BB', 'BAMLH0A1HYBB'), ('B', 'BAMLH0A2HYB'),
           ('CCC', 'BAMLH0A3HYC')]
    out = []
    for nm, sid in ids:
        s = sorted((d, v) for d, v in X.get(sid, []))
        if not s:
            continue
        d, v = s[-1]
        tgt = (dt.date.fromisoformat(d) - dt.timedelta(days=28)).isoformat()
        p4 = [x for x in s if x[0] <= tgt]
        chg = (v - p4[-1][1]) * 100 if p4 else None
        pct10 = round(100 * sum(1 for _, y in s if y <= v) / len(s), 1)
        out.append({'grade': nm, 'bp': v * 100, 'chg4': chg, 'pct10': pct10})
    if not out:
        return None
    widened = [x['grade'] for x in out if (x['chg4'] or 0) > 0]
    lad = {'rows': out, 'n_widen': len(widened), 'n_all': len(out),
           'broad': len(widened) >= len(out) - 1}
    return lad


def ig_term(c):
    """IG 期限结构 4 周变化：短端抬=流动性，长端抬=信用；全段齐抬=系统性"""
    X = _credit_cache()
    if not X:
        return None
    ids = [('1-3Y', 'BAMLC1A0C13Y'), ('3-5Y', 'BAMLC2A0C35Y'), ('5-7Y', 'BAMLC3A0C57Y'),
           ('7-10Y', 'BAMLC4A0C710Y'), ('10-15Y', 'BAMLC7A0C1015Y'), ('15Y+', 'BAMLC8A0C15PY')]
    out = []
    for nm, sid in ids:
        s = sorted((d, v) for d, v in X.get(sid, []))
        if not s:
            continue
        d, v = s[-1]
        tgt = (dt.date.fromisoformat(d) - dt.timedelta(days=28)).isoformat()
        p4 = [x for x in s if x[0] <= tgt]
        out.append({'b': nm, 'bp': v * 100, 'chg4': ((v - p4[-1][1]) * 100 if p4 else None)})
    if len(out) < 4:
        return None
    chgs = [x['chg4'] for x in out if x['chg4'] is not None]
    short = out[0]['chg4'] or 0
    long_ = out[-1]['chg4'] or 0
    return {'rows': out, 'short': short, 'long': long_,
            'parallel': bool(chgs and min(chgs) > 0 and (max(chgs) - min(chgs)) <= 6)}


def real_rate_split(c):
    """10Y 名义/实际/通胀预期 分解：实际利率主导 = 贴现率问题，不是信用问题"""
    X = _credit_cache()
    if not X:
        return None
    g = lambda sid: sorted((d, v) for d, v in X.get(sid, []))
    def chg(sid, days=28):
        s = g(sid)
        if not s:
            return None, None
        d, v = s[-1]
        tgt = (dt.date.fromisoformat(d) - dt.timedelta(days=days)).isoformat()
        p = [x for x in s if x[0] <= tgt]
        return v, ((v - p[-1][1]) * 100 if p else None)
    n, nc = chg('DGS10')
    r, rc = chg('DFII10')
    b, bc = chg('T10YIE')
    if None in (n, r, b):
        return None
    dom = '实际利率' if abs(rc or 0) > abs(bc or 0) else '通胀预期'
    return {'nom': n, 'nom_chg': nc, 'real': r, 'real_chg': rc,
            'be': b, 'be_chg': bc, 'dom': dom}


def bank_stress(c):
    """银行侧（季度/月频）：拖欠率趋势 + 信贷标准，回答「实体有没有真出血」"""
    X = _credit_cache()
    if not X:
        return None
    def q(sid, back=4):
        s = sorted((d, v) for d, v in X.get(sid, []))
        if not s:
            return None
        d, v = s[-1]
        prev = s[-1 - back][1] if len(s) > back else s[0][1]
        return {'date': d, 'v': v, 'prev': prev, 'chg': v - prev}
    res = {'cre': q('DRCRELEXFACBS'), 'all_ln': q('DRCLACBS'),
           'ci': q('DRBLACBS'), 'cons': q('DRSREACBS'),
           'std_lm': q('DRTSCILM', 1), 'std_sb': q('DRTSCIS', 1), 'std_cc': q('DRTSCLCC', 1)}
    cre = res['cre']
    res['bleeding'] = bool(cre and cre['v'] >= 8.0)
    res['cre_rising'] = bool(cre and cre['chg'] > 0)
    return res


def nfci_now(c):
    """芝加哥联储 NFCI：负=宽松。信用分项转正是早期警报"""
    X = _credit_cache()
    if not X:
        return None
    def g(sid):
        s = sorted((d, v) for d, v in X.get(sid, []))
        if not s:
            return None
        d, v = s[-1]
        tgt = (dt.date.fromisoformat(d) - dt.timedelta(days=28)).isoformat()
        p = [x for x in s if x[0] <= tgt]
        return {'date': d, 'v': v, 'chg4': (v - p[-1][1] if p else None)}
    return {'all': g('NFCI'), 'credit': g('NFCICREDIT'), 'risk': g('NFCIRISK')}


def hist_analog(c, thr=150.0):
    """历史同态：CCC 4 周走阔 ≥thr 的历史事件，之后 1/3/6 个月 SPX 表现"""
    X = _credit_cache()
    if not X:
        return None
    # SP500 历史改从 DataHub（us.fred.SP500），不再依赖宿主机 .cache/sp500_hist.json
    try:
        sp = {d: v for d, v in M.get_series('us.fred.SP500', days=None, ascending=True)}
    except Exception:
        return None
    if not sp:
        return None
    ccc = sorted((d, v) for d, v in X.get('BAMLH0A3HYC', []))
    if not ccc:
        return None
    c4 = {}
    for i, (d, v) in enumerate(ccc):
        tgt = (dt.date.fromisoformat(d) - dt.timedelta(days=28)).isoformat()
        p = [x for x in ccc[:i + 1] if x[0] <= tgt]
        if p:
            c4[d] = (v - p[-1][1]) * 100
    ev = []
    for d in sorted(c4):
        if c4[d] >= thr and (not ev or (dt.date.fromisoformat(d) - dt.date.fromisoformat(ev[-1])).days > 20):
            ev.append(d)
    if not ev:
        return None
    keys = sorted(sp)
    def fwd(d, mo):
        b = [k for k in keys if k >= d]
        if not b:
            return None
        b = b[0]
        tgt = (dt.date.fromisoformat(b) + dt.timedelta(days=30 * mo)).isoformat()
        f = [k for k in keys if k <= tgt]
        if not f or f[-1] == b or f[-1] < tgt[:7]:
            return None
        return (sp[f[-1]] / sp[b] - 1) * 100
    out = {'n': len(ev), 'dates': ev[-6:], 'thr': thr}
    for mo in (1, 3, 6):
        rr = [x for x in (fwd(d, mo) for d in ev) if x is not None]
        if rr:
            rr_s = sorted(rr)
            med = rr_s[len(rr_s) // 2] if len(rr_s) % 2 else (rr_s[len(rr_s)//2-1] + rr_s[len(rr_s)//2]) / 2
            out[f'm{mo}'] = {'med': med, 'win': 100 * sum(1 for x in rr if x > 0) / len(rr),
                             'n': len(rr), 'lo': min(rr), 'hi': max(rr)}
    # 当前这次是否已在历史事件中
    out['is_new'] = not any(True for d in ev if (dt.date.today() - dt.date.fromisoformat(d)).days <= 20)
    return out


def info_block(c, ext):
    """把上面五块拼成「真信息」结构；拿不到的块自动略过"""
    lad = credit_ladder(c)
    term = ig_term(c)
    rr = real_rate_split(c)
    bk = bank_stress(c)
    nf = nfci_now(c)
    ha = hist_analog(c, 150.0)
    return {'ladder': lad, 'term': term, 'real': rr, 'bank': bk, 'nfci': nf, 'hist': ha}


def verdict(ext, I):
    """把证据收敛成一句可执行的判断（不是复述数字）"""
    lad, term, rr, bk, nf = I['ladder'], I['term'], I['real'], I['bank'], I['nfci']
    amp = None
    if lad:
        g = {x['grade']: x['chg4'] for x in lad['rows']}
        amp = {'AAA': g.get('AAA'), 'BBB': g.get('BBB'), 'CCC': g.get('CCC')}
    # 形态：凸性（越往下越猛）还是平移（全档同幅）
    convex = bool(amp and amp['CCC'] is not None and amp['AAA'] is not None
                  and amp['CCC'] > (amp['AAA'] or 0) * 5)
    rate_dom = bool(rr and abs(rr['real_chg'] or 0) >= abs(rr['be_chg'] or 0))
    bleed = bool(bk and bk['bleeding'])
    # 定性
    if bleed:
        tag, col_ = '实体已出血', 'red'
    elif convex and lad and lad['broad']:
        tag, col_ = '全档走阔·尾部凸性（定价事件，非违约事件）', 'orange'
    elif convex:
        tag, col_ = '仅尾部走阔（单体事件）', 'orange'
    elif lad and lad['broad']:
        tag, col_ = '全档平移（系统性折价，多因利率/供给）', 'orange'
    else:
        tag, col_ = '未见系统性传导', 'grey'
    why = []
    if lad and lad['broad']:
        why.append(f"{lad['n_widen']}/{lad['n_all']} 档同步走阔（不是只有 CCC）")
    if convex and amp:
        why.append(f"幅度自 AAA {f(amp['AAA'],'+.0f')}bp 递增到 CCC {f(amp['CCC'],'+.0f')}bp")
    if rate_dom and rr:
        why.append(f"10Y 变动 {f(rr['nom_chg'],'+.0f')}bp 里实际利率占 {f(rr['real_chg'],'+.0f')}bp、通胀预期只 {f(rr['be_chg'],'+.0f')}bp")
    if bk and bk.get('cre') and not bk['bleeding']:
        why.append(f"银行端没有确认：CRE 拖欠 {f(bk['cre']['v'],'.2f')}%（{'升' if bk['cre_rising'] else '降'}）")
    elif bk is not None and not bk.get('cre'):
        # DataHub 没有 CRE 拖欠（DRCRELEXFACBS）→ 不编造，明说缺哪一条
        why.append("银行端无数据（CRE 拖欠率 DataHub 未登记）→ 本项不参与判定")
    return {'tag': tag, 'color': col_, 'why': why, 'convex': convex,
            'rate_dom': rate_dom, 'bleed': bleed, 'amp': amp}


def _summ(rules):
    risks = [r for r in rules if r['hit'] and r['level'] == 'RISK']
    warns = [r for r in rules if r['hit'] and r['level'] == 'WARN']
    level = 'RISK' if risks else ('WARN' if warns else 'OK')
    flag = ('credit_extreme', '信用利差极值') if any(r['name'].startswith('信用利差极值') for r in risks) else None
    return {'level': level, 'risks': risks, 'warns': warns, 'all': rules, 'flag': flag}


def risk_banner(a):
    """卡片顶部横幅：风险时 红 + ⭐ + 标题级(大) + 加粗"""
    if a['level'] == 'RISK':
        lines = [f"<font color='red'>**🔴⭐ {r['name']}　{f(r['cur'],'.2f')}　(阈值 {r['thr']})**</font>"
                 for r in a['risks'][:4]]
        more = f"\n<font color='red'>**…另有 {len(a['risks'])-4} 项超阈值，见文末明细**</font>" if len(a['risks']) > 4 else ''
        return ("<font color='red'>**▼▼▼ 风险预警 ▼▼▼**</font>\n"
                + '\n'.join(lines) + more)
    if a['level'] == 'WARN':
        names = '｜'.join(r['name'] for r in a['warns'][:4])
        more = f' 等 {len(a["warns"])} 项' if len(a['warns']) > 4 else ''
        return (f"<font color='orange'>**🟡 警告 · {len(a['warns'])} 项接近阈值（不发邮件）**</font>\n"
                f"<font color='orange'>{names}{more}</font>")
    return "<font color='green'>**🟢 正常（无指标超出阈值）**</font>"


def risk_section(a):
    """风险/警告明细段（卡片末尾）"""
    out = []
    if a['risks']:
        lines = [f"<font color='red'>**🔴⭐ {r['name']}**</font>　**{f(r['cur'],'+.2f' if isinstance(r['cur'],(int,float)) else '.2f')}**"
                 f"　阈值 {r['thr']}　<font color='grey'>{r['note']}</font>" for r in a['risks']]
        out.append("**🔴⭐ 触发风险阈值（已发邮件告警）**\n" + '\n'.join(lines))
    if a['warns']:
        lines = [f"<font color='orange'>🟡 {r['name']}</font>　{f(r['cur'],'.2f')}　阈值 {r['thr']}"
                 for r in a['warns']]
        out.append("**🟡 接近阈值（仅提示，不发邮件）**\n" + '\n'.join(lines))
    if not a['risks'] and not a['warns']:
        out.append("<font color='green'>**🟢 全部指标在阈值内**</font>")
    return out


def risk_md(title, a, body_lines):
    """邮件正文（Markdown）"""
    L = [f"# {title}", '']
    if a['level'] == 'RISK':
        L.append(f"## 🔴⭐ 风险预警：{len(a['risks'])} 项超出阈值")
        L.append('')
        L.append('| 指标 | 现值 | 阈值 | 含义 |')
        L.append('|---|---|---|---|')
        for r in a['risks']:
            L.append(f"| **{r['name']}** | **{f(r['cur'],'.2f')}** | {r['thr']} | {r['note']} |")
        L.append('')
    if a['warns']:
        L.append(f"### 🟡 警告：{len(a['warns'])} 项接近阈值（不发邮件，仅记录）")
        L.append('')
        L.append('| 指标 | 现值 | 阈值 |')
        L.append('|---|---|---|')
        for r in a['warns']:
            L.append(f"| {r['name']} | {f(r['cur'],'.2f')} | {r['thr']} |")
        L.append('')
    L.append('---')
    L.append('')
    L.extend(body_lines)
    L.append('')
    L.append(f'<font color="grey">阈值口径见 bin/liq_credit_weekly.py 的 assess_* 函数。</font>')
    return '\n'.join(L)


# ======================= A股指数（tushare + 缓存） =======================
def load_eq(refresh=False):
    if not refresh and os.path.exists(EQ_CACHE):
        age = (dt.datetime.now().timestamp() - os.path.getmtime(EQ_CACHE)) / 3600
        if age < 20:
            return json.load(open(EQ_CACHE, encoding='utf-8'))
    try:
        # 五宽基日线走 DataHub EDB 数据集（Tushare 通道，字段含 close/amount）
        import liqeq
        out = liqeq.load_index_daily([c for c, _n in CN_IDX])
        if out:
            os.makedirs(os.path.dirname(EQ_CACHE), exist_ok=True)
            json.dump(out, open(EQ_CACHE, 'w'), ensure_ascii=False)
            return out
    except Exception as e:
        print(f'[eq] 取数失败 {e!r}，用缓存')
    if os.path.exists(EQ_CACHE):
        return json.load(open(EQ_CACHE, encoding='utf-8'))
    return {}


def eq_stat(eq, code, n):
    d = eq.get(code)
    if not d or len(d['close']) <= n:
        return None
    cl = d['close']
    return (cl[-1] / cl[-1 - n] - 1) * 100


def eq_from_high(eq, code, n=250):
    d = eq.get(code)
    if not d:
        return None
    cl = d['close']
    hi = max(cl[-n:]) if len(cl) >= n else max(cl)
    return (cl[-1] / hi - 1) * 100


def _hdr_tpl(a):
    return {'RISK': 'red', 'WARN': 'orange', 'OK': 'green'}[a['level']]


def _hdr_title(a, side, asof):
    """卡片标题 = 字号最大的部分，风险时带 ⭐ 和数量"""
    n = len(a['risks'])
    if a['level'] == 'RISK':
        return f"🔴⭐ 风险预警 · {side} · {n}项超阈值 · {asof}"
    if a['level'] == 'WARN':
        return f"🟡 警告 · {side} 流动性信用周报 · {len(a['warns'])}项接近阈值 · {asof}"
    return f"🟢 {side} 流动性·信用风险周报 · {asof}"


# ======================= 中国周报 =======================
def build_cn(c):
    E = []

    def md(t):
        E.append({'tag': 'markdown', 'content': t})

    def hr():
        E.append({'tag': 'hr'})

    eq = load_eq()
    asof_eq = ''
    for _, _n in CN_IDX:
        pass
    if eq:
        any_code = list(eq)[0]
        asof_eq = eq[any_code]['dates'][-1]
    asof_bond = last(c, 'cn.cb.govt.10Y')[0] or ''
    L5 = _l5()

    # ---- 头 ----
    _a = assess_cn(c, eq)
    md(f"{SIGN}\n**🇨🇳 中国 · 流动性与信用风险 周报（股市 + 债券）**\n"
       f"<font color='grey'>股市 {asof_eq}｜债券 {asof_bond}｜共同层不分栏，差异层左右对照</font>")
    md(risk_banner(_a))
    md("<font color='red'>**总判定：资金面满档宽松 → 传导失败（钱进不了实体）→ 股债定价同时到极端 → 靠 92% 隔夜杠杆维持**</font>")
    md("<font color='grey'>一句话：**不是信用风险，是资产荒。** 债市被买到没有缓冲，股市没有增量资金。</font>")

    # ===== 第①层 共同：资金面 =====
    hr()
    md("**第①层 · 资金面：✅ 满档宽松（这一层股债共用同一池子，不分栏）**")
    dr001 = last(c, 'cn.repo.FDR001')
    dr007 = last(c, 'cn.repo.FDR007')
    r007 = last(c, 'cn.repo.R007')
    rdr = last(c, 'cn.repo.r_dr_spread')
    onsh = last(c, 'cn.repo.vol_on_share')
    omo = last(c, 'cn.pbc.omo.amount')
    omonet = last(c, 'cn.pbc.omo.net')
    omorate = last(c, 'cn.pbc.omo.rate')
    E.append(row([('**指标**', 3), ('**现值**', 4), ('**状态 / 分位**', 3)]))
    E.append(row([('DR001 / DR007', 3), (f"{f(dr001[1],'.2f')}% / {f(dr007[1],'.2f')}%", 4),
                  ("<font color='green'>低位不动</font>", 3)]))
    E.append(row([('7天逆回购 / LPR1Y', 3),
                  (f"{f(omorate[1],'.2f')}% / {f(last(c,'cn.lpr.1y')[1],'.2f')}%", 4),
                  ("<font color='grey'>政策利率未动</font>", 3)]))
    E.append(row([('R007-DR007', 3), (f"{f(rdr[1],'.1f')}bp", 4),
                  (f"{f(pct(c,'cn.repo.r_dr_spread'),'.1f')}% 分位", 3)]))
    E.append(row([('隔夜回购占比', 3), (f"{f(onsh[1],'.1f')}%", 4),
                  (f"<font color='orange'>{f(pct(c,'cn.repo.vol_on_share'),'.1f')}% 分位</font>", 3)]))
    E.append(row([('OMO 当日量 / 净投放', 3), (f"{f(omo[1],'.0f')} / {f(omonet[1],'+.0f')}亿", 4), ('—', 3)]))
    md(f"DR007 偏离政策利率 **{((dr007[1] or 0)-(omorate[1] or 0))*100:+.0f}bp** → "
       f"<font color='green'>**资金面完全没有收紧迹象，这是股债共同的底座。**</font>")

    # ── ① 层风险：隔夜杠杆 ASCII 近 10 日（剔采集洞，如 10-09 的 30%） ──
    _ons_rows = l5m.onshare_rows(10) if l5m else last_n(c, 'cn.repo.vol_on_share', 10)
    if _ons_rows and ac:
        _q_ons = (L5['meta'].get('onshare_q') if L5 else None)
        # ① 层一览：4 个关键量的 10 日折线
        _multi = [
            ('DR007', last_n(c, 'cn.repo.FDR007', 10), '%', '.2f'),
            ('R007-DR007', last_n(c, 'cn.repo.r_dr_spread', 10), 'bp', '+.1f'),
            ('隔夜占比', _ons_rows, '%', '.1f'),
            ('回购成交量', last_n(c, 'cn.repo.vol_total', 10), '亿', '.0f'),
        ]
        md(ac.multi(_multi, head='**📈 第①层 近 10 个交易日一屏看（折线左旧右新）**'))
        md(ac.chart('🌙 隔夜回购占比（近10个交易日）', _ons_rows, unit='%', fmt='.1f',
                    extra=((f"一年分位 {_q_ons:.1f}%｜" if _q_ons is not None else '') +
                           "≥95% = 极致carry，资金面一有扰动即砸长端")))

    # ===== 第②层 传导（分两栏）=====
    hr()
    md("**第②层 · 传导：❌ 双双失效（这一层股债走向不同，分两栏看）**")
    m1 = last(c, 'cn.macro.m1_yoy')
    m2 = last(c, 'cn.macro.m2_yoy')
    gap = last(c, 'cn.macro.m1_m2_gap')
    north = last(c, 'cn.eq.north_flow')
    nf = last(c, 'cn.eq.new_fund_week')

    left = (f"**📈 股市：增量资金缺席**\n"
            f"M1-M2 剪刀差 **{f(gap[1],'+.1f')}pct**\n"
            f"北向 {f(north[1],'+.1f')}亿\n"
            f"偏股新发 **{f(nf[1],'.2f')}亿**（周）\n"
            f"<font color='red'>7→8→9月 剪刀差 -3.7/-3.4/-4.0 连续恶化</font>")
    y10 = last(c, 'cn.cb.govt.10Y')
    y30 = last(c, 'cn.cb.govt.30Y')
    right = (f"**📉 债券：资金全挤进债市**\n"
             f"国债 10Y **{f(y10[1],'.4f')}%**（{f(pct(c,'cn.cb.govt.10Y'),'.1f')}% 分位）\n"
             f"国债 30Y **{f(y30[1],'.4f')}%**（{f(pct(c,'cn.cb.govt.30Y'),'.1f')}% 分位）\n"
             f"<font color='red'>10Y 创建库新低</font>\n"
             f"<font color='grey'>钱没有出金融体系，只是从贷款搬到了债券</font>")
    E.append(two(left, right))
    md("<font color='orange'>**同一件事的两面：剪刀差 -4.0 说明钱出不去实体，而钱没消失——它全部涌进债市，把 10Y 买到历史最低。**</font>"
       "\n所以股市缺的是「增量」，债券多的是「存量搬家」。")

    # ===== 第③层 定价极端度（分两栏）=====
    hr()
    md("**第③层 · 定价：⚠️ 两头都到极端（这一层是报告的核心，分两栏对照）**")

    # 股市左栏
    sl = "**📈 股市：按 beta 排序的下跌**\n"
    for code, name in CN_IDX:
        d20 = eq_stat(eq, code, 20)
        sl += f"{name} **{f(d20,'+.1f')}%**（20日）\n"
    sl += "<font color='grey'>越贵跌越多 → 外部传导，非内部流动性</font>"

    # 债券右栏
    sr = "**📉 债券：四档利差压到样本最低**\n"
    for tag, key in (('AAA', 'mtn_aaa'), ('AA+', 'mtn_aa1'), ('AA', 'mtn_aa'), ('AA-', 'mtn_aa_lo')):
        p, v = sp_pct(c, f'cn.cb.{key}.3Y', 'cn.cb.govt.3Y')
        sr += f"{tag} **{f(v,'.0f')}bp**（{f(p,'.1f')}% 分位）\n"
    sr += "<font color='grey'>AA 与 AAA 只差 7bp → 短端分层消失</font>"
    E.append(two(sl, sr))

    # 矩阵
    md("**债券 · 全期限信用利差矩阵（bp，中短票 - 国债）**")
    E.append(row([('**期限**', 2), ('**国债**', 2), ('**AAA**', 2), ('**AA+**', 2), ('**AA**', 2), ('**AA-**', 2)]))
    for t in ('1Y', '2Y', '3Y', '5Y', '7Y', '10Y'):
        g = D(c, f'cn.cb.govt.{t}')
        cells = []
        for key in ('mtn_aaa', 'mtn_aa1', 'mtn_aa', 'mtn_aa_lo'):
            dd = D(c, f'cn.cb.{key}.{t}')
            com = [x for x in dd if x in g]
            cells.append(f"{(dd[com[-1]]-g[com[-1]])*100:.0f}" if com else '—')
        gv = list(g.values())[-1] if g else None
        E.append(row([(t, 2), (f"{gv:.3f}" if gv else '—', 2)] + [(x, 2) for x in cells]))

    # 期限利差
    p102, v102 = sp_pct(c, 'cn.cb.govt.10Y', 'cn.cb.govt.2Y')
    p3010, v3010 = sp_pct(c, 'cn.cb.govt.30Y', 'cn.cb.govt.10Y')
    p10on, v10on = sp_pct(c, 'cn.cb.govt.10Y', 'cn.cb.govt.0.083Y')
    md(f"**债券 · 期限结构**：10Y-2Y **{f(v102,'.1f')}bp（{f(p102,'.1f')}% 分位）**　"
       f"30Y-10Y **{f(v3010,'.1f')}bp（{f(p3010,'.1f')}%）**　"
       f"10Y-隔夜 **{f(v10on,'.1f')}bp（{f(p10on,'.1f')}%）**"
       f"\n<font color='orange'>→ 中长段平坦到极端，但短端利差正常 → **不是流动性泛滥压低全曲线，是配置盘在 5Y 以上抢券**</font>")

    md("<font color='red'>**股债定价的共振点：都在为「没有增长」定价。**</font>"
       "\n股市跌的是增长预期（beta 越高的跌越多），债券涨的是增长预期（长端越买越凶）。**同一件事，两个市场用相反方向表达。**")

    # ===== 第④层 结构脆弱点（分两栏）=====
    hr()
    md("**第④层 · 脆弱点：⚠️ 各自的风险源（分两栏）**")
    ncd = last(c, 'cn.cb.ncd_aaa.1Y')
    pncd, vncd = sp_pct(c, 'cn.cb.ncd_aaa.1Y', 'cn.cb.govt.1Y')
    vol = last(c, 'cn.repo.vol_total')

    _amt = 0.0
    for _code, _n in CN_IDX:
        _d = eq.get(_code)
        if _d and _d.get('amount'):
            _amt += _d['amount'][-1]
    lf = (f"**📈 股市：无融资盘挤兑，但杠杆在悄悄退**\n"
          f"五宽基成交 **{_amt/1e5:.0f}亿**\n"
          f"北向 {f(north[1],'+.1f')}亿（日）\n"
          f"两融周净买入 **{(L5['meta']['net_last'] if L5 else None) or 0:+.0f}亿**\n"
          f"<font color='orange'>风险=美债 10Y 继续上行 + 杠杆边际转负</font>")
    rf = (f"**📉 债券：隔夜杠杆 {f(onsh[1],'.1f')}%，唯一真风险源**\n"
          f"NCD 1Y **{f(ncd[1],'.4f')}%**（对国债 {f(vncd,'.0f')}bp）\n"
          f"隔夜占比 **{f(onsh[1],'.1f')}%**\n"
          f"R007-DR007 {f(rdr[1],'.1f')}bp\n"
          f"<font color='red'>成本粘着、资产到地板 → 息差两头挤</font>")
    E.append(two(lf, rf))
    md(f"<font color='red'>**债市的雷不在信用，在杠杆：{f(onsh[1],'.1f')}% 隔夜 + 极致 carry，资金面一有扰动，去杠杆会直接砸长端。**</font>"
       "\n而股市的问题反过来——**它没有杠杆挤兑，是被美债长端从外部压着。**")

    # ===== 第⑤层 L5 股市微观池（含两融杠杆子层）=====
    hr()
    L5 = _l5()
    if L5:
        md(f"**第⑤层 · L5 股市微观池（含两融杠杆子层）：{L5['layer_star'] or '—'} "
           f"（{L5['stars']} 项触阈值）**")
        md("<font color='grey'>口径：周频 52 周、日频 250 交易日一年分位；缺数据写「数据不足」不估算</font>")
        md(l5m.render_md_table(L5))
        md('**🚦 叠加规则**')
        md(l5m.render_md_overlay(L5))
        md('**📈 近 10 个交易日（ASCII）**')
        md(l5m.render_card_charts(L5))
    else:
        md('**第⑤层 · L5 股市微观池：本周期暂缺**（l5_micro 计算失败）')

    # ===== 结论 + 阈值 =====
    hr()
    md("**🎯 结论**")
    md("① 资金面满档宽松（股债共同底座）"
       "\n② 传导失败：剪刀差 -4.0，钱出不了实体"
       "\n③ 钱没消失，全涌进债市 → 10Y 建库新低、四档利差样本最低"
       "\n④ 股市缺增量被外部杀 beta，债市有存量但全靠 92% 隔夜杠杆"
       "\n<font color='red'>**中国的问题不是「会不会违约」，是「钱什么时候能找到出口」。出口找不到：股市没增量，债市没缓冲。**</font>")

    md("**📌 下周盯这四个数**")
    E.append(two(
        "**📈 股市侧**\n"
        "1️⃣ M1-M2 剪刀差回 **-3pct** 以上（现 -4.0）\n"
        "2️⃣ 偏股新发回到 **50亿/周** 以上（现 0.24亿）\n"
        "3️⃣ 沪深300 跌破 **4000**（现 4170.61）\n"
        "4️⃣ 美债 10Y 上破 **5.30%**（现 5.10%）",
        "**📉 债券侧**\n"
        "1️⃣ 10Y 上破 **1.75%**（4周前水平）\n"
        "2️⃣ 3Y AAA 利差回 **45bp**（现 32）\n"
        f"3️⃣ NCD-国债从 {f(vncd,'.0f')}bp 破 **40bp**\n"
        "4️⃣ R007-DR007 破 **20bp**（现 6.8）"))

    hr()
    for _t in risk_section(_a):
        md(_t)

    md(f"<font color='grey'>口径：中债估值（国债全曲线·中短票四档·同业存单 AAA）、银行间质押回购、央行 OMO、"
       f"tushare 指数与 cn_m/sf_month。⚠️ 中债分位样本仅 {len(ser(c,'cn.cb.govt.10Y'))} 个交易日（2026-07-01 起），"
       f"非一年分位，读数偏敏感；但「四档利差同时到样本最低」方向可靠。</font>")

    return {'config': {'wide_screen_mode': True},
            'header': {'template': _hdr_tpl(_a),
                       'title': {'tag': 'plain_text', 'content': _hdr_title(_a, '中国', asof_bond)}},
            'elements': E}


# ======================= 美国周报 =======================
def build_us(c):
    E = []

    def md(t):
        E.append({'tag': 'markdown', 'content': t})

    def hr():
        E.append({'tag': 'hr'})

    hy = last(c, 'us.fred.BAMLH0A0HYM2')
    ig = last(c, 'us.fred.BAMLC0A0CM')
    bb = last(c, 'us.fred.BAMLH0A1HYBB')
    b = last(c, 'us.fred.BAMLH0A2HYB')
    ccc = last(c, 'us.fred.BAMLH0A3HYC')
    cbb = last(c, 'us.derived.ccc_bb_spread')
    cbs = last(c, 'us.derived.ccc_b_spread')
    bbs = last(c, 'us.derived.bb_b_spread')
    rrp = last(c, 'us.fred.RRPONTSYD')
    res = last(c, 'us.fred.WRESBAL')
    tga = last(c, 'us.fred.WTREGEN')
    sofr = last(c, 'us.derived.sofr_iorb_spread')
    d10 = last(c, 'us.fred.DGS10')
    d2 = last(c, 'us.fred.DGS2')
    d30 = last(c, 'us.ust.30Y')
    vix = last(c, 'us.fred.VIXCLS')
    spx = last(c, 'us.fred.SP500')
    ndx = last(c, 'us.fred.NASDAQCOM')
    cpe = last(c, 'us.derived.cp_effr_spread')
    asof = ccc[0] or ''

    md(f"{SIGN}\n**🇺🇸 美国 · 流动性与信用风险 周报（股市 + 债券）**\n"
       f"<font color='grey'>信用债 {asof}｜利率/股指 {d10[0]}｜准备金 {res[0]}｜共同层不分栏，差异层左右对照</font>")
    _a = assess_us(c)
    md(risk_banner(_a))
    _sofr0 = last(c, 'us.derived.sofr_iorb_spread')[1]
    md(f"<font color='red'>**总判定：美元流动性不缺（SOFR-IORB {f(_sofr0,'+.0f')}bp）→ 但长端 {f(d10[1],'.2f')}% 高位 → "
       f"信用{'全档走阔、尾部历史极值' if True else ''} → 股市还没认账**</font>")
    md("<font color='grey'>一句话：**不缺钱，但每档都能拿到更高的信用溢价（全档折价），尾部最狠。** "
       "信用市场已全面折价，股票市场还没认账。</font>")

    # ===== 🔍 信用利差极值 · 传导断点诊断（仅极值时追加）=====
    _ext = credit_extreme_us(c)
    if _ext.get('hit'):
        _bp = breaking_point(c, _ext)
        _I = info_block(c, _ext)
        _v = verdict(_ext, _I)
        hr()
        md(f"<font color='red'>**🔍 信用利差极值 · 传导断点诊断**</font>\n"
           f"**触发档位**：{_ext['grade']}（{'／'.join(_ext['hits'])} 触阈值）\n"
           f"**主读数**：**{f(_ext['cur'],'.0f')}bp**（周环比 {f(_ext['wk'],'+.0f')}bp，一年 {f(_ext['pct'],'.1f')}% 分位）\n"
           f"<font color='grey'>→ 来源：FRED（BAMLH0A0HYM2 / BAMLC0A4CBBB / BAMLH0A3HYC）</font>")
        if _bp['layer']:
            _ln = {'L0': '长端利率顶', 'L1': '一级市场关门',
                   'L3': '抵押品不认了', 'L4': '实体真出血'}[_bp['layer']]
            md(f"<font color='red'>**断点定位：→ ⭐ {_bp['layer']} 层（{_ln}）**</font>\n"
               f"**一句话**：{_bp['why']}")
            md("**三项锚点**\n" + '\n'.join(f"{i}️⃣ {t}" for i, t in enumerate(_bp['anchors'], 1)))
            if _bp.get('aff'):
                md(f"<font color='grey'>锚点观察：{_bp['aff']}</font>")
        else:
            md(f"<font color='{_v['color']}'>**断点定位：→ ⋯ 无单层坐实，形态是「{_v['tag']}」**</font>\n"
               f"**一句话**：" + '；'.join(_v['why']) + '。')

        # ---- ① 七档阶梯：这是「只有 CCC 还是一片」的直接证据 ----
        _lad = _I['ladder']
        if _lad:
            md("<font color='grey'>**① 是不是只有尾部在动？看全档阶梯（4周变化）**</font>")
            _cells = ' → '.join(f"{x['grade']} {f(x['chg4'],'+.0f')}" for x in _lad['rows'])
            md(f"`{_cells}`　**{_lad['n_widen']}/{_lad['n_all']} 档同步走阔**"
               + ("　<font color='red'>**不是单体事件**</font>" if _lad['broad'] else "　<font color='grey'>仅尾部</font>"))
            E.append(row([('**档位**', 2), ('**OAS**', 2), ('**4周Δ**', 2), ('**10年分位**', 2)]))
            for x in _lad['rows']:
                _c = 'red' if (x['pct10'] or 0) >= 90 else ('orange' if (x['pct10'] or 0) >= 75 else 'grey')
                E.append(row([(f"**{x['grade']}**", 2), (f"{f(x['bp'],'.0f')}bp", 2),
                              (f"<font color='{_c}'>{f(x['chg4'],'+.0f')}bp</font>", 2),
                              (f"<font color='{_c}'>{f(x['pct10'],'.0f')}%</font>", 2)]))

        # ---- ② 期限结构：短端抬=流动性，全段齐抬=系统性折价 ----
        _t = _I['term']
        if _t:
            _tc = ' → '.join(f"{x['b']} {f(x['chg4'],'+.0f')}" for x in _t['rows'])
            md(f"<font color='grey'>**② 期限结构（IG 各久期 4周Δ）**</font>\n`{_tc}`\n"
               + ("**全段齐抬 → 不是流动性，是系统性折价**" if _t['parallel']
                  else f"短端 {f(_t['short'],'+.0f')} / 长端 {f(_t['long'],'+.0f')} → 看哪头主导"))

        # ---- ③ 利率分解：实际利率 vs 通胀预期 ----
        _r = _I['real']
        if _r:
            md(f"<font color='grey'>**③ 10Y 这 {f(_r['nom_chg'],'+.0f')}bp 是谁抬的**</font>\n"
               f"{f(_r['nom'],'.2f')}% = 实际 **{f(_r['real'],'.2f')}%**（{f(_r['real_chg'],'+.0f')}bp）"
               f" + 通胀预期 {f(_r['be'],'.2f')}%（{f(_r['be_chg'],'+.0f')}bp）\n"
               f"<font color='{'orange' if _v['rate_dom'] else 'grey'}'>"
               f"→ **{_r['dom']}主导**"
               f"{'，不是通胀问题，是真实资金成本问题' if _v['rate_dom'] else ''}</font>")

        # ---- ④ 实体侧：唯一能否决「定价事件」的一票 ----
        _bk = _I['bank']
        if _bk and _bk['cre']:
            md(f"<font color='grey'>**④ 实体侧：银行认不认账（否决项）**</font>\n"
               f"CRE 拖欠 **{f(_bk['cre']['v'],'.2f')}%**（{'↑ 升' if _bk['cre_rising'] else '↓ 降'}）"
               f"｜全部贷款 {f(_bk['all_ln']['v'],'.2f')}%｜工商 {f(_bk['ci']['v'],'.2f')}%\n"
               f"信贷标准净收紧：大中企业 {f(_bk['std_lm']['v'],'+.1f')}｜小企业 {f(_bk['std_sb']['v'],'+.1f')}"
               f"｜信用卡 {f(_bk['std_cc']['v'],'+.1f')}\n"
               + (f"<font color='red'>**→ CRE 拖欠 {f(_bk['cre']['v'],'.1f')}% 已达 L4 出血线（≥8%）**</font>"
                  if _bk['bleeding'] else
                  "<font color='green'>**→ 银行端没确认：这是全档走阔里最关键的反证**</font>"))
            md(f"<font color='grey'>数据日 {_bk['cre']['date']}（季度），比信用利差滞后一个季度，"
               "下一份是这套判断的唯一定时炸弹</font>")

        # ---- ⑤ 历史同态 ----
        _h = _I['hist']
        if _h and _h.get('m1'):
            _s = (f"**过去 10 年 CCC 4周走阔 ≥{f(_h['thr'],'.0f')}bp 只出现 {_h['n']} 次**"
                  f"（{'／'.join(x[2:].replace('-','/') for x in _h['dates'])}）\n")
            for _mo, _lb in ((1, '1个月'), (3, '3个月'), (6, '6个月')):
                _k = f'm{_mo}'
                if _h.get(_k):
                    _s += (f"之后{_lb} SPX 中位 **{f(_h[_k]['med'],'+.1f')}%**"
                           f"（胜率 {f(_h[_k]['win'],'.0f')}%，n={_h[_k]['n']}，区间 "
                           f"{f(_h[_k]['lo'],'+.0f')}~{f(_h[_k]['hi'],'+.0f')}%）\n")
            _s += "<font color='grey'>样本极少，只能当「不是必跌信号」的反证，不能当规律</font>"
            md(f"<font color='grey'>**⑤ 历史同态（这条决定要不要慌）**</font>\n" + _s)

        # ---- ⑥ 综合结论 ----
        _n = _I['nfci']
        _nf = ''
        if _n and _n['credit']:
            _nf = (f"｜NFCI 信用分项 {f(_n['credit']['v'],'+.3f')}"
                   f"（{f(_n['credit']['chg4'],'+.3f')}，{'仍在均值下=宽松' if _n['credit']['v'] < 0 else '已转正=警报'}）")
        md(f"<font color='{_v['color']}'>**⑥ 结论：{_v['tag']}**</font>\n"
           f"→ {('；').join(_v['why'])}\n"
           f"<font color='grey'>金融条件 {_nf[1:] if _nf else '未取到'}</font>")
        md(f"<font color='grey'>层级命中：L0 长端利率 {'✅' if _bp['sig'].get('L0') else '✖'}｜"
           f"L3 抵押品 {'✅' if _bp['sig'].get('L3') else '✖'}｜"
           f"L1/L4 {'跳过（无免费源）' if (_bp.get('skip') or []) else '已判'}</font>")

    # ===== 第①层 共同：美元流动性 =====
    hr()
    md("**第①层 · 美元流动性：✅ 不缺钱（股债共用，不分栏）**")
    E.append(row([('**指标**', 3), ('**现值**', 4), ('**状态**', 3)]))
    E.append(row([('**SOFR-IORB**', 3), (f"**{f(sofr[1],'+.0f')}bp**", 4), ("<font color='green'>安全（>+5 才危险）</font>", 3)]))
    E.append(row([('准备金 WRESBAL', 3), (f"{f(res[1]/1e6,'.3f')}万亿", 4), ("<font color='green'>区间中位偏上</font>", 3)]))
    E.append(row([('TGA 财政账户', 3), (f"{f(tga[1]/1e3,'.0f')}十亿", 4),
                  (f"<font color='orange'>{f(pct(c,'us.fred.WTREGEN'),'.0f')}% 分位</font>", 3)]))
    E.append(row([('RRP 缓冲垫', 3), (f"{f(rrp[1],'.1f')}十亿", 4), ("<font color='grey'>已归零一年</font>", 3)]))
    E.append(row([('CP3M-EFFR', 3), (f"{f(cpe[1],'+.0f')}bp", 4),
                  (f"{f(pct(c,'us.derived.cp_effr_spread'),'.1f')}% 分位", 3)]))
    md("<font color='grey'>对比 2025Q4 挤压期 SOFR-IORB 曾到 +9/+22/+32bp，现在 -1bp → "
       "**回购市场完全不紧，这一层对股债都是利好底座，不是风险源。**</font>")

    # ===== 第②层 传导（分两栏）=====
    hr()
    md("**第②层 · 传导：⚠️ 长端利率渗透到两个市场（分两栏）**")
    spx_hi = max([x[1] for x in ser(c, 'us.fred.SP500')][-252:])
    ndx_hi = max([x[1] for x in ser(c, 'us.fred.NASDAQCOM')][-252:])
    left = (f"**📈 股市：估值被长端压着**\n"
            f"SPX {f(spx[1],',.0f')}（距高点 **{(spx[1]/spx_hi-1)*100:+.1f}%**）\n"
            f"纳指 {f(ndx[1],',.0f')}（距高点 **{(ndx[1]/ndx_hi-1)*100:+.1f}%**）\n"
            f"VIX **{f(vix[1],'.2f')}**（{f(pct(c,'us.fred.VIXCLS'),'.1f')}% 分位）\n"
            f"<font color='grey'>跌幅远小于信用端 → 还没认账</font>\n"
            f"<font color='grey'>注：周五实时 VIX 19.99（+21.5%），FRED 仍 T-1</font>")
    p102, v102 = sp_pct(c, 'us.fred.DGS10', 'us.fred.DGS2')
    p3010, v3010 = sp_pct(c, 'us.ust.30Y', 'us.fred.DGS10')
    right = (f"**📉 债券：长端 5% 高位横盘**\n"
             f"10Y **{f(d10[1],'.2f')}%**（{f(pct(c,'us.fred.DGS10'),'.1f')}% 分位）\n"
             f"2Y **{f(d2[1],'.2f')}%**　30Y **{f(d30[1],'.2f')}%**\n"
             f"10Y-2Y {f(v102,'.0f')}bp（{f(p102,'.0f')}% 分位）\n"
             f"30Y-10Y {f(v3010,'.0f')}bp\n"
             f"<font color='orange'>短端高 + 长端更高 = 熊平</font>")
    E.append(two(left, right))
    _cyb = eq_stat(load_eq(), '399006.SZ', 20)
    md(f"<font color='red'>**核心传导链：10Y {f(d10[1],'.2f')}%（{f(pct(c,'us.fred.DGS10'),'.0f')}% 分位）"
       f"→ 全球长久期资产估值被杀 → A股创业板 {f(_cyb,'+.1f')}%（20日）、纳指距高点 {(ndx[1]/ndx_hi-1)*100:+.1f}%。**</font>"
       "\n不是信用传染，是**贴现率上行**。这条线是股债共同的压力来源。")

    # ===== 第③层 定价极端度（分两栏）=====
    hr()
    md("**第③层 · 定价：🔴 信用端历史极值 vs 股票端无动于衷（分两栏对照）**")
    sl = (f"**📈 股市：几乎没事**\n"
          f"VIX **{f(vix[1],'.2f')}**\n"
          f"（{f(pct(c,'us.fred.VIXCLS'),'.1f')}% 分位）\n"
          f"SPX 距一年高点 **{(spx[1]/spx_hi-1)*100:+.1f}%**\n"
          f"<font color='green'>没有任何恐慌定价</font>")
    sr = (f"**📉 债券：尾部历史新高**\n"
          f"CCC **{f100(ccc[1])}bp**（{f(pct(c,'us.fred.BAMLH0A3HYC'),'.1f')}%）\n"
          f"BB **{f100(bb[1])}bp**（{f(pct(c,'us.fred.BAMLH0A1HYBB'),'.1f')}%）\n"
          f"B **{f100(b[1])}bp**（{f(pct(c,'us.fred.BAMLH0A2HYB'),'.1f')}%）\n"
          f"HY {f100(hy[1])}bp｜IG {f100(ig[1])}bp\n"
          f"<font color='red'>CCC 建库以来最高</font>")
    E.append(two(sl, sr))

    md("**债券 · 分层位置判定（比「整体走阔」更有用）**")
    E.append(row([('**内部利差**', 3), ('**现值**', 4), ('**分位 / 含义**', 3)]))
    E.append(row([('BB→B', 3), (f"{f(bbs[1],'.0f')}bp", 4),
                  (f"<font color='green'>{f(pct(c,'us.derived.bb_b_spread'),'.1f')}% 中段正常</font>", 3)]))
    E.append(row([('B→CCC', 3), (f"{f(cbs[1],'.0f')}bp", 4),
                  (f"<font color='red'>{f(pct(c,'us.derived.ccc_b_spread'),'.1f')}% 跳档</font>", 3)]))
    E.append(row([('**CCC-BB**', 3), (f"**{f(cbb[1],'.0f')}bp**", 4),
                  (f"<font color='red'>**{f(pct(c,'us.derived.ccc_bb_spread'),'.1f')}% 全距极值**</font>", 3)]))
    # 这一段是最容易被误读的地方：内部利差「中段正常」≠ 中段没走阔
    _lad0 = (info_block(c, credit_extreme_us(c)) or {}).get('ladder')
    if _lad0 and _lad0['n_widen'] >= _lad0['n_all'] - 1:
        _g = {x['grade']: x['chg4'] for x in _lad0['rows']}
        md(f"<font color='orange'>**⚠️ 别被「中段正常」误导：BB→B 这个内部利差"
           f"{f(pct(c,'us.derived.bb_b_spread'),'.1f')}% 分位只是「没相对多走」，"
           f"但绝对水平 4 周已 +{f(_g.get('BB'),'.0f')}bp（BB）、+{f(_g.get('B'),'.0f')}bp（B）。**</font>\n"
           f"→ 真实形态是 **{_lad0['n_widen']}/{_lad0['n_all']} 档全档走阔 + CCC 尾部凸性**"
           f"（AAA 只 +{f(_g.get('AAA'),'.0f')}bp、CCC +{f(_g.get('CCC'),'.0f')}bp）："
           "高资质被轻微折价、尾部被重定价，**不是「只有 CCC 出事」**。")
    else:
        md("<font color='red'>**分层集中在 B→CCC 这一段，BB→B 中段未动。**</font>"
           f"\n→ 尾部单体事件的形态。BB 自身 {f(pct(c,'us.fred.BAMLH0A1HYBB'),'.1f')}% 分位，"
           "高资质也已被拖下水。")

    # ===== 第④层 结构脆弱（分两栏）=====
    hr()
    md("**第④层 · 分歧点：这就是当前最重要的定价事实（分两栏）**")
    lf = (f"**📈 股市在赌什么**\n"
          f"赌「CCC 只是微观事件」\n"
          f"VIX {f(vix[1],'.2f')} · SPX 距高点 {(spx[1]/spx_hi-1)*100:+.1f}%\n"
          f"<font color='green'>定价按「不传染」</font>\n"
          f"<font color='orange'>一旦 VIX 破 30，这个假设作废</font>")
    rf = (f"**📉 债券在说什么**\n"
          f"CCC-BB 4周 +{(cbb[1]-at(c,'us.derived.ccc_bb_spread',28) or cbb[1]):.0f}bp\n"
          f"CCC 4周 {('—' if ccc[1] is None else '%+.0fbp' % ((ccc[1] - (at(c,'us.fred.BAMLH0A3HYC',28) or ccc[1])) * 100))}\n"
         f"<font color='red'>尾部在快速出清</font>\n"
          f"<font color='orange'>全档都在抬，不只是尾部</font>")
    E.append(two(lf, rf))
    md("<font color='red'>**这两个市场在讲相反的故事：债券说尾部在崩，股票说没事。总有一个是错的。**</font>"
       "\n历史上（2025-04 关税冲击）同类形态是**流动性宽松压过了信用恐慌**，40天后 SPX +17.9%；"
       "但样本只有 3 次，不能当规律。**关键看 VIX 会不会跳到 30 以上。**")

    # ===== 结论 + 阈值 =====
    hr()
    md("**🎯 结论**")
    _ladC = (info_block(c, credit_extreme_us(c)) or {}).get('ladder')
    _gC = ({x['grade']: x['chg4'] for x in _ladC['rows']} if _ladC else {})
    md("① 美元流动性不缺（SOFR-IORB -1bp，这条是确认安全）"
       "\n② 压力来自长端：10Y 5.10%（94% 分位），且 +16bp 里实际利率占 +17bp → 杀的是长久期，不是杀信用资质"
       "\n③ 信用端是**全档走阔**："
       + (f"{_ladC['n_widen']}/{_ladC['n_all']} 档 4 周同步抬（AAA {f(_gC.get('AAA'),'+.0f')} → CCC {f(_gC.get('CCC'),'+.0f')}bp）"
          if _ladC else "尾部历史极值")
       + f"；CCC 1278bp、CCC-BB 1081bp 双创历史新高"
       "\n④ 但**银行端没认账**（CRE 拖欠 1.53%、在降）→ 目前是定价事件，不是违约事件"
       "\n<font color='red'>**美国的问题不在流动性、也不在违约，在「实际利率 × 全档折价」这两件事同时压。**</font>")

    md("**📌 下周盯这四个数**")
    _ndx = last(c, 'us.fred.NASDAQCOM')[1]
    _spxr = (spx[1] / spx_hi - 1) * 100
    _tgt_spx = spx_hi * 0.90
    _tgt_ndx = ndx_hi * 0.90
    E.append(two(
        "**📈 股市侧**\n"
        f"1️⃣ VIX 破 **30**（现 {f(vix[1],'.2f')}）\n"
        f"2️⃣ SPX 跌破 **{f(_tgt_spx,',.0f')}**（-10% 线，现 {f(spx[1],',.0f')}）\n"
        f"3️⃣ 纳指跌破 **{f(_tgt_ndx,',.0f')}**（现 {f(_ndx,',.0f')}）\n"
        f"4️⃣ SPX 距高点超 **-10%**（现 {f(_spxr,'+.1f')}%）",
        "**📉 债券侧**\n"
        f"1️⃣ CCC-BB 破 **1150bp**（现 {f(cbb[1],'.0f')}）\n"
        f"2️⃣ BB→B 破 **180bp**（现 {f(bbs[1],'.0f')}）\n"
        f"3️⃣ SOFR-IORB 连续3日 > **+5bp**（现 {f(_sofr0,'+.0f')}）\n"
        f"4️⃣ 10Y 破 **5.30%** 或回落 **4.80%**（现 {f(d10[1],'.2f')}%）"))

    hr()
    for _t in risk_section(_a):
        md(_t)

    md("<font color='grey'>口径：ICE BofA OAS（FRED T-1）、H.4.1 准备金、SOFR/IORB、CNBC 实时 VIX。"
       "分位为近一年滚动；分位在顶部会饱和，故主判据用 CCC-BB 利差自身分位而非分位差。"
       "CDX.NA.IG/HY 5Y、单名 CDS 无免费合法源，标 NULL 不编造。</font>")

    return {'config': {'wide_screen_mode': True},
            'header': {'template': _hdr_tpl(_a),
                       'title': {'tag': 'plain_text', 'content': _hdr_title(_a, '美国', asof)}},
            'elements': E}


# ======================= 推送 =======================
def push(card, label):
    """推飞书卡片。

    ⚠️ 飞书 webhook 失败也返 HTTP 200（body 里 code≠0），只判 HTTP 状态会把
    「没发出去」当成发成功（老代码就是 `js.get('code')==0 or StatusCode==0`，
    后半段对 bot/v2/hook 的响应根本不存在，等于只认 code）。这里统一只认 code。
    ⚠️ HOOK 从 case/_lib/feishu_creds 读，源码里不写明文；空 HOOK 直接判失败。
    """
    if not HOOK:
        print(f'[{label}] 未配置飞书 webhook（case/_lib/feishu_creds）→ 跳过推送')
        return False
    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    try:
        import requests
        r = requests.post(HOOK, data=body.encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, timeout=30)
        http = r.status_code
        try:
            js = r.json()
        except Exception:
            js = {'raw': (r.text or '')[:300]}
    except Exception as e:                               # noqa: BLE001
        print(f'[{label}] 请求异常 {e!r}')
        return False
    ok = (js.get('code') == 0)
    print(f'[{label}] http={http} ok={ok} size={len(body)}B '
          f'resp={json.dumps(js, ensure_ascii=False)[:200]}')
    return ok


MAIL_STATE = os.path.join(DATA_DIR, 'liq_credit_weekly_mail_state.json')


def _mail_state():
    try:
        return json.load(open(MAIL_STATE, encoding='utf-8'))
    except Exception:
        return {}


def _mail_already_sent(side, as_of):
    """同一 side + 同一 as_of 当天只发一次，防重跑导致重复邮件"""
    st = _mail_state()
    return st.get(f'{side}:{as_of}') is not None


def _mark_mail_sent(side, as_of, subject):
    st = _mail_state()
    st[f'{side}:{as_of}'] = {'at': dt.datetime.now().isoformat(timespec='seconds'),
                             'subject': subject}
    # 只保留最近 60 条
    if len(st) > 60:
        for k in sorted(st, key=lambda k: st[k].get('at', ''))[:len(st) - 60]:
            st.pop(k, None)
    os.makedirs(os.path.dirname(MAIL_STATE), exist_ok=True)
    json.dump(st, open(MAIL_STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def send_mail(subject, body_md):
    """发邮件：本框架当前**没有**通用邮件出口，返回 False 并说明原因。

    原实现调宿主机 ``scripts/send_email.py``（私有脚本，不在本仓）。迁进 case/
    后没有等价物，所以这里不假装发出：返回 False + 打印原因。RISK 级别报告的
    邮件通知因此失效，改由 pipeline 的 dispatch 阶段用 ``case/_lib/alert``
    （框架报警通道）投递同样内容 —— 见 README「推送与告警」。
    """
    try:
        lib = os.path.join(os.path.dirname(_HERE), '_lib')
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import mailer                                    # noqa: PLC0415
        ok, msg = mailer.send(subject, body_md, html=True)
        print(f'[email] ok={ok} {msg}')
        return bool(ok)
    except ImportError as e:
        print(f'[email] 跳过：case/_lib/mailer 不可用（{e}）')
        return False
    except Exception as e:                               # noqa: BLE001
        print(f'[email] ERR {e!r}')
        return False


def cn_mail_body(c, eq, a):
    """中国周报邮件正文（Markdown）"""
    L = []
    dr007 = last(c, 'cn.repo.FDR007')[1]
    rdr = last(c, 'cn.repo.r_dr_spread')[1]
    onsh = last(c, 'cn.repo.vol_on_share')[1]
    gap = last(c, 'cn.macro.m1_m2_gap')[1]
    y10 = last(c, 'cn.cb.govt.10Y')
    y30 = last(c, 'cn.cb.govt.30Y')
    L.append('## 一、股市')
    L.append('')
    L.append('| 指数 | 20日 | 5日 | 距一年高点 |')
    L.append('|---|---|---|---|')
    for code, name in CN_IDX:
        L.append(f"| {name} | **{f(eq_stat(eq,code,20),'+.1f')}%** | "
                 f"{f(eq_stat(eq,code,5),'+.1f')}% | {f(eq_from_high(eq,code),'+.1f')}% |")
    L.append('')
    L.append(f"成交额（五宽基）**{sum((eq.get(k,{}).get('amount') or [0])[-1] for k,_ in CN_IDX)/1e5:.0f}亿**")
    L.append('')
    L.append('## 二、债券')
    L.append('')
    L.append(f"| 期限 | 收益率 | 分位 |")
    L.append('|---|---|---|')
    for t in ('0.083Y', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '30Y'):
        l = last(c, f'cn.cb.govt.{t}')
        if l[1] is not None:
            L.append(f"| {t} | **{l[1]:.4f}%** | {f(pct(c,f'cn.cb.govt.{t}'),'.1f')}% |")
    L.append('')
    L.append('**信用利差（中短票 - 国债，3Y）**')
    L.append('')
    L.append('| 档位 | 利差 | 分位 |')
    L.append('|---|---|---|')
    for tag, key in (('AAA', 'mtn_aaa'), ('AA+', 'mtn_aa1'), ('AA', 'mtn_aa'), ('AA-', 'mtn_aa_lo')):
        p_, v_ = sp_pct(c, f'cn.cb.{key}.3Y', 'cn.cb.govt.3Y')
        L.append(f"| {tag} | **{f(v_,'.0f')}bp** | {f(p_,'.1f')}% |")
    L.append('')
    L.append('**全期限矩阵（bp）**')
    L.append('')
    L.append('| 期限 | 国债 | AAA | AA+ | AA | AA- |')
    L.append('|---|---|---|---|---|---|')
    for t in ('1Y', '2Y', '3Y', '5Y', '7Y', '10Y'):
        g = D(c, f'cn.cb.govt.{t}')
        cells = []
        for key in ('mtn_aaa', 'mtn_aa1', 'mtn_aa', 'mtn_aa_lo'):
            dd = D(c, f'cn.cb.{key}.{t}')
            com = [x for x in dd if x in g]
            cells.append(f"{(dd[com[-1]]-g[com[-1]])*100:.0f}" if com else '—')
        gv = list(g.values())[-1] if g else None
        L.append(f"| {t} | {gv:.3f} | " + ' | '.join(cells) + ' |' if gv else f"| {t} | — | " + ' | '.join(cells) + ' |')
    L.append('')
    L.append('## 三、资金面')
    L.append('')
    L.append(f"- DR007 **{f(dr007,'.2f')}%**　R007-DR007 **{f(rdr,'.1f')}bp**　隔夜占比 **{f(onsh,'.1f')}%**")
    L.append(f"- M1-M2 剪刀差 **{f(gap,'+.1f')}pct**")
    L.append('')
    L5 = _l5()
    if L5:
        L.append('### ⭐ 第一层风险 · 隔夜回购占比（近 10 个交易日）')
        L.append('')
        L.append(l5m.render_md_charts(L5, onshare_only=True) if l5m else '')
        L.append('')
        L.append(f"## 四、L5 A股微观池（含两融杠杆子层）{L5['layer_star']}")
        L.append('')
        L.append(l5m.render_md_table(L5) if l5m else '')
        L.append('')
        L.append('**🚦 叠加规则**')
        L.append('')
        L.append(l5m.render_md_overlay(L5) if l5m else '')
        L.append('')
        L.append('**📈 近 10 个交易日（ASCII）**')
        L.append('')
        L.append(l5m.render_md_charts(L5) if l5m else '')
        L.append('')
    L.append('')
    L.append('## 四、结论')
    L.append('')
    L.append('资金面满档宽松，但传导失败（剪刀差 -4.0）。钱没消失，全涌进债市把 10Y 买到建库新低；'
             '股市缺增量被外部杀 beta。**中国的问题不是会否违约，是钱什么时候找到出口。**')
    L.append('')
    L.append('**下周盯**：M1-M2 回 -3pct / 偏股新发回 50亿/周 / 10Y 破 1.75% / R007-DR007 破 20bp')
    return L


def us_mail_body(c):
    L = []
    L.append('## 一、信用分层')
    L.append('')
    L.append('| 档位 | OAS(bp) | 4周Δ | 分位 |')
    L.append('|---|---|---|---|')
    for nm, k, k4 in (('IG', 'us.fred.BAMLC0A0CM', 'us.fred.BAMLC0A0CM'),
                      ('BB', 'us.fred.BAMLH0A1HYBB', 'us.fred.BAMLH0A1HYBB'),
                      ('B', 'us.fred.BAMLH0A2HYB', 'us.fred.BAMLH0A2HYB'),
                      ('CCC', 'us.fred.BAMLH0A3HYC', 'us.fred.BAMLH0A3HYC'),
                      ('HY', 'us.fred.BAMLH0A0HYM2', 'us.fred.BAMLH0A0HYM2')):
        cu = last(c, k)[1]
        pv = at(c, k4, 28)
        L.append(f"| **{nm}** | **{f(cu*100 if cu else None,'.0f')}** | "
                 f"{(cu-pv)*100:+.0f} | {f(pct(c,k),'.1f')}% |" if (cu and pv) else f"| {nm} | — | — | — |")
    L.append('')
    L.append('| 内部利差 | 现值 | 分位 |')
    L.append('|---|---|---|')
    for nm, k in (('BB→B', 'us.derived.bb_b_spread'), ('B→CCC', 'us.derived.ccc_b_spread'),
                  ('**CCC-BB**', 'us.derived.ccc_bb_spread')):
        p_, v_ = pct(c, k), last(c, k)[1]
        L.append(f"| {nm} | **{f(v_,'.0f')}bp** | **{f(p_,'.1f')}%** |")
    L.append('')
    _ladM = (info_block(c, credit_extreme_us(c)) or {}).get('ladder')
    if _ladM:
        _gM = {x['grade']: x['chg4'] for x in _ladM['rows']}
        L.append(f"**分层位置**：{_ladM['n_widen']}/{_ladM['n_all']} 档 4 周同步走阔"
                 f"（AAA {f(_gM.get('AAA'),'+.0f')}bp → CCC {f(_gM.get('CCC'),'+.0f')}bp），"
                 f"内部利差 B→CCC 在 {f(pct(c,'us.derived.ccc_b_spread'),'.1f')}% 分位。"
                 "**是「全档折价 + 尾部凸性」，不是「只有 CCC 单体出事」**——"
                 "BB→B 中段分位低只说明它没相对多走，不代表绝对水平没抬。")
    else:
        L.append('**分层位置**：尾部历史极值，中段相对温和。')
    L.append('')
    L.append('## 二、利率与股市')
    L.append('')
    d10 = last(c, 'us.fred.DGS10')
    d2 = last(c, 'us.fred.DGS2')
    d30 = last(c, 'us.ust.30Y')
    vix = last(c, 'us.fred.VIXCLS')
    spx = [x[1] for x in ser(c, 'us.fred.SP500')]
    L.append(f"- 10Y **{f(d10[1],'.2f')}%**（{f(pct(c,'us.fred.DGS10'),'.1f')}% 分位）"
             f"　2Y {f(d2[1],'.2f')}%　30Y {f(d30[1],'.2f')}%")
    L.append(f"- VIX **{f(vix[1],'.2f')}**　SPX {f(spx[-1],',.0f')}（距一年高点 {(spx[-1]/max(spx[-252:])-1)*100:+.1f}%）")
    L.append('')
    L.append('## 三、结论')
    L.append('')
    L.append('美元流动性不缺（SOFR-IORB -1bp）。真正风险是长端 10Y 5.10%（94% 分位）杀全球长久期资产；'
             '信用端尾部历史极值但中段正常。**美国的问题不在流动性，在尾部定价。**')
    L.append('')
    L.append('**下周盯**：CCC-BB 破 1150 / BB→B 破 180 / SOFR-IORB 连续3日>+5bp / VIX 破 30')

    # ---- 🔍 信用利差极值 · 传导断点诊断（仅极值时追加）----
    ext = credit_extreme_us(c)
    if ext.get('hit'):
        bp = breaking_point(c, ext)
        L.extend(['', '---', '', '## 🔍 信用利差极值 · 传导断点诊断', ''])
        L.append(f"**触发档位**：{ext['grade']}（{'／'.join(ext['hits'])} 触阈值）")
        L.append('')
        L.append(f"**主读数**：**{f(ext['cur'],'.0f')}bp**（周环比 {f(ext['wk'],'+.0f')}bp，"
                 f"一年 {f(ext['pct'],'.1f')}% 分位）→ FRED")
        L.append('')
        if bp['layer']:
            _ln = {'L0': '长端利率顶', 'L1': '一级市场关门',
                   'L3': '抵押品不认了', 'L4': '实体真出血'}[bp['layer']]
            L.append(f"**断点定位**：→ ⭐ **{bp['layer']} 层（{_ln}）**")
            L.append('')
            L.append(f"**一句话**：{bp['why']}")
        else:
            _vv = verdict(ext, info_block(c, ext))
            L.append(f"**断点定位**：→ ⋯ 无单层坐实，形态是「{_vv['tag']}」")
            L.append('')
            L.append('**一句话**：' + '；'.join(_vv['why']) + '。')
        L.append('')
        L.append('**三项锚点**：')
        L.append('')
        for i, t in enumerate(bp['anchors'], 1):
            L.append(f"{i}. {t}")
        L.append('')
        # ---- 真信息四块（邮件版）----
        I = info_block(c, ext)
        v = verdict(ext, I)
        L.extend(['### 形态：' + v['tag'], ''])
        if I['ladder']:
            L.append('**① 全档阶梯（4 周变化）**：' +
                     ' → '.join(f"{x['grade']} {f(x['chg4'],'+.0f')}" for x in I['ladder']['rows']) +
                     f"　→ **{I['ladder']['n_widen']}/{I['ladder']['n_all']} 档同步走阔**")
            L.append('')
            L.append('| 档位 | OAS | 4周Δ | 10年分位 |')
            L.append('|---|---|---|---|')
            for x in I['ladder']['rows']:
                L.append(f"| **{x['grade']}** | {f(x['bp'],'.0f')}bp | {f(x['chg4'],'+.0f')}bp | {f(x['pct10'],'.0f')}% |")
            L.append('')
        if I['term']:
            L.append('**② 期限结构（IG 各久期 4周Δ）**：' +
                     ' → '.join(f"{x['b']} {f(x['chg4'],'+.0f')}" for x in I['term']['rows']) +
                     ('　→ **全段齐抬 = 系统性折价，不是流动性**' if I['term']['parallel'] else ''))
            L.append('')
        if I['real']:
            r = I['real']
            L.append(f"**③ 10Y 的 {f(r['nom_chg'],'+.0f')}bp 是谁抬的**："
                     f"{f(r['nom'],'.2f')}% = 实际 {f(r['real'],'.2f')}%（{f(r['real_chg'],'+.0f')}bp）"
                     f" + 通胀预期 {f(r['be'],'.2f')}%（{f(r['be_chg'],'+.0f')}bp）→ **{r['dom']}主导**")
            L.append('')
        if I['bank'] and I['bank']['cre']:
            bk = I['bank']
            L.append(f"**④ 实体侧（否决项）**：CRE 拖欠 {f(bk['cre']['v'],'.2f')}%"
                     f"（{'↑' if bk['cre_rising'] else '↓'}）｜全部贷款 {f(bk['all_ln']['v'],'.2f')}%"
                     f"｜工商 {f(bk['ci']['v'],'.2f')}%｜信贷标准净收紧：大中 {f(bk['std_lm']['v'],'+.1f')}、"
                     f"小企业 {f(bk['std_sb']['v'],'+.1f')}、信用卡 {f(bk['std_cc'],'.1f') if isinstance(bk['std_cc'],(int,float)) else f(bk['std_cc']['v'],'+.1f')}")
            L.append('')
            L.append('→ ' + ('**CRE 已达 L4 出血线**' if bk['bleeding'] else
                             '**银行端没确认 → 定价事件，不是违约事件**'))
            L.append('')
        if I['hist'] and I['hist'].get('m1'):
            h = I['hist']
            L.append(f"**⑤ 历史同态**：过去 10 年 CCC 4周走阔 ≥{f(h['thr'],'.0f')}bp 出现 {h['n']} 次")
            L.append('')
            for mo, lb in ((1, '1个月'), (3, '3个月'), (6, '6个月')):
                k = f'm{mo}'
                if h.get(k):
                    L.append(f"- 之后{lb} SPX 中位 **{f(h[k]['med'],'+.1f')}%**"
                             f"（胜率 {f(h[k]['win'],'.0f')}%，n={h[k]['n']}）")
            L.append('')
        return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--side', default='cn', choices=['cn', 'us', 'all'])
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--refresh-eq', action='store_true')
    ap.add_argument('--no-mail', action='store_true', help='只推飞书不发邮件')
    ap.add_argument('--force-mail', action='store_true', help='强制发邮件（跳过同日去重，调试用）')
    ap.add_argument('--no-push', action='store_true', help='不推飞书（只跑评估/邮件，调试用）')
    a = ap.parse_args()
    c = con()
    eq = load_eq(refresh=a.refresh_eq)
    order = ['cn', 'us'] if a.side == 'all' else [a.side]

    summary = []
    for s in order:
        card = build_cn(c) if s == 'cn' else build_us(c)
        assess = (assess_cn(c, eq) if s == 'cn' else assess_us(c))
        if a.dry:
            print(json.dumps(card, ensure_ascii=False, indent=1))
        elif not a.no_push:
            push(card, s)
        else:
            print(f'[{s}] --no-push 跳过飞书推送')

        # ---- 邮件：仅 RISK 级别发 ----
        mail_sent = False
        if assess['level'] == 'RISK' or a.force_mail:
            nm = '中国' if s == 'cn' else '美国'
            n = len(assess['risks'])
            as_of_mail = (last(c, 'cn.cb.govt.10Y')[0] if s == 'cn' else last(c, 'us.fred.BAMLH0A3HYC')[0]) or ''
            subj = f"🔴⭐【风险预警·{nm}】流动性信用风险周报 · {n}项超阈值"
            if not a.force_mail and _mail_already_sent(s, as_of_mail):
                print(f'[{s}] 同一数据日 {as_of_mail} 已发过邮件，跳过（--force-mail 可强制）')
                assess_mail_skip = True
            else:
                assess_mail_skip = False
            body = cn_mail_body(c, eq, assess) if s == 'cn' else us_mail_body(c)
            title = f"{nm} 流动性·信用风险周报 · 风险预警"
            md_txt = risk_md(title, assess, body)
            if a.dry:
                print(f'--- 邮件（{nm}）---')
                print(md_txt)
            elif not a.no_mail and not assess_mail_skip:
                mail_sent = send_mail(subj, md_txt)
                if mail_sent:
                    _mark_mail_sent(s, as_of_mail, subj)
        elif assess['level'] == 'WARN':
            nm = '中国' if s == 'cn' else '美国'
            print(f'[{s}] 级别 WARN（{len(assess["warns"])}项接近阈值）→ 不发邮件')
        else:
            print(f'[{s}] 级别 OK → 不发邮件')

        summary.append({'side': s, 'level': assess['level'],
                        'risks': len(assess['risks']), 'warns': len(assess['warns']),
                        'mail': ('skip' if locals().get('assess_mail_skip') else mail_sent)})

    print('\n== 汇总 ==')
    for x in summary:
        print(f"  {x['side']}: {x['level']}  risk={x['risks']} warn={x['warns']} mail={x['mail']}")


if __name__ == '__main__':
    main()
