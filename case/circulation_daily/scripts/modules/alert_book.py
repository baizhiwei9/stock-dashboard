"""环流日报统一告警体系：警告（普通） vs 报警（高级）。

分级原则
--------
- **警告 warn**：数据缺失、接口失败、技术性扰动、分位偏高但未击穿 → **仅展示，不发邮件**。
- **报警 alert**：阈值击穿、方向性恶化、结构性阻塞 → 计入报警数，
  报警数 ≥ ``EMAIL_ALERT_THRESHOLD``（默认 2）触发邮件。

邮件幂等由调用方（circulation_daily）用 ``alert_mailed_<日期>`` 缓存键控制，
本模块只负责判定与发送。

对外接口
--------
- ``AlertBook(date_display)``
- ``.collect(**kwargs)``        收集所有上游模块的观测值
- ``.items`` / ``.alerts`` / ``.warns``
- ``.counts()``                 ``{'alert': n, 'warn': m}``
- ``.headline()``               一句话摘要
- ``.render_md()``              list[str]，插入日报标题下方
- ``.render_card_lines()``      list[str]，插入飞书卡片置顶
- ``.should_email()``           bool
- ``.send_email()``             bool

阈值集中在 ``TH``，改阈值只改一处。
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


import sys
from typing import Any, Dict, List, Optional

try:
    from . import edb_helper as E
except ImportError:  # 兼容把 modules/ 直接挂到 sys.path 的用法
    try:
        import edb_helper as E
    except Exception:  # EDB 不可用时相关检查自动跳过
        E = None

# ------------------------------------------------------------------ 阈值

TH = {
    # ---- 资金面 ----
    'liq_dev_bp': 25.0,            # DR001 偏离政策利率
    'liq_tier_bp': 30.0,           # 非银分层
    # ---- OMO ----
    'omo_pct': 75.0,               # 净投放近1年分位
    # ---- 美联储 ----
    'reserve_low_tn': 2.8,         # 准备金低于该值（万亿美元）偏紧
    'rrp_dry_bn': 5.0,             # ON RRP 低于该值（十亿美元）逼近枯竭
    'rrp_zero_bn': 0.5,            # ON RRP 归零
    'tga_high_tn': 1.0,            # TGA 高于该值（万亿美元）抽水明显
    # ---- 货币市场 ----
    'mm_spread_bp': 5.0,           # SOFR-EFFR / TGCR-SOFR
    'effr_iorb_bp': 8.0,           # EFFR 高于央行利率下沿
    'srf_trigger_mn': 1.0,         # SRF 单日使用（百万美元）
    'sofr_effr_pct': 90.0,         # SOFR-EFFR 历史分位
    # ---- 利率 / 汇率 ----
    'us10y_chg_bp': 10.0,          # 美债10Y 单日变动
    'shib_chg_bp': 10.0,           # Shibor 单日变动
    'cny_chg_pips': 300.0,         # 人民币单日变动（pips）
    'spread_bp': 150.0,            # 中美利差绝对水平
    'spread_g_bp': 15.0,           # 中美利差单日变动
    'swap_abs_pct': 3.5,           # 1Y 掉期年化绝对水平
    'jgb_10y': 2.5,                # JGB 10Y 水平
    'hibor_1m': 3.0,               # HIBOR 1M 水平
    'repo_vol_chg_pct': 30.0,      # 回购成交量变动
    # ---- 指纹 ----
    'fp_top_score': 80.0,          # 首位嫌疑贡献分
    'swap_e_week_bp': 20.0,        # 1Y掉期贴水周变动阈值（bp）
}

EMAIL_ALERT_THRESHOLD = 2
EMAIL_SOURCE = '环流日报'
EMAIL_TO = _dh_secret('COZE_EMAIL_TO')


# ------------------------------------------------------------------ 数据结构

class AlertItem:
    """单条告警。"""

    __slots__ = ('level', 'category', 'title', 'detail', 'value')

    def __init__(self, level: str, category: str, title: str, detail: str = '', value: Any = None):
        self.level = level          # 'alert' | 'warn'
        self.category = category
        self.title = title
        self.detail = detail
        self.value = value

    @property
    def icon(self) -> str:
        return '🚨' if self.level == 'alert' else '⚠️'

    def to_md(self) -> str:
        if self.detail:
            return f"- {self.icon} **[{self.category}]** {self.title} — {self.detail}"
        return f"- {self.icon} **[{self.category}]** {self.title}"

    def to_card(self) -> str:
        return self.to_md()

    def __repr__(self) -> str:
        return f"<AlertItem {self.level} {self.category} {self.title}>"


# ------------------------------------------------------------------ 主体

class AlertBook:
    """告警簿：收集 → 分级 → 渲染 → 判定是否发信。"""

    def __init__(self, date_display: str):
        self.date = date_display
        self.items: List[AlertItem] = []
        self._data: Dict[str, Any] = {}
        self._mailed = False

    # ---------------- 内部登记 ----------------

    def _alert(self, category: str, title: str, detail: str = '', value: Any = None):
        self.items.append(AlertItem('alert', category, title, detail, value))

    def _warn(self, category: str, title: str, detail: str = '', value: Any = None):
        self.items.append(AlertItem('warn', category, title, detail, value))

    # ---------------- 收集 ----------------

    def collect(self, **kw) -> 'AlertBook':
        """收集全部上游观测值并逐项判级。

        所有参数均为可选，缺失即跳过该项检查（不报错）。
        """
        self._data = kw
        self.items = []
        for fn in (
            self._check_data_health,
            self._check_omo,
            self._check_liq,
            self._check_repo,
            self._check_fed,
            self._check_tga,
            self._check_money_market,
            self._check_swap_demand,
            self._check_rates,
            self._check_swap,
            self._check_overseas,
            self._check_cd,
            self._check_fingerprint,
        ):
            try:
                fn()
            except Exception as e:  # 单项失败不影响其他
                self._warn('模块', f'{fn.__name__} 检查异常', str(e)[:80])
        # 报警在前，警告在后
        self.items.sort(key=lambda x: 0 if x.level == 'alert' else 1)
        return self

    # ---- 1. 数据健康度 ----
    def _check_data_health(self):
        d = self._data
        missing = []
        if d.get('tga') is False:
            missing.append('TGA(DTS)')
        if d.get('mlf') is False:
            missing.append('MLF')
        if d.get('repo_volume') is False:
            missing.append('回购成交量')
        if d.get('ust') is False:
            missing.append('美债传导')
        if missing:
            self._warn('数据', f'数据源缺失: {", ".join(missing)}', '对应段落降级展示')

    # ---- 2. OMO ----
    def _check_omo(self):
        d = self._data
        omo = d.get('omo')
        pct = d.get('omo_pct')
        reason = d.get('omo_alert_reason') or ''
        if not omo:
            return
        net = omo.get('today_net')
        if pct is not None and pct >= TH['omo_pct']:
            if net is not None and net > 0:
                self._warn('OMO', f'净投放 {net:+.0f}亿 处近1年 {pct:.0f}% 分位',
                           reason or '投放规模偏高，属呵护而非收紧')
            elif net is not None and net < 0:
                self._alert('OMO', f'净回笼 {abs(net):.0f}亿 处近1年 {pct:.0f}% 分位',
                            reason or '回笼规模偏高，流动性边际收紧')

    # ---- 3. 资金面判定 ----
    def _check_liq(self):
        liq = self._data.get('liq')
        if not liq or not isinstance(liq, dict) or not liq.get('ok'):
            return
        label = liq.get('label')
        dev = liq.get('dev_bp')
        tier = liq.get('tier_bp')
        if label == '真收紧':
            self._alert('资金面', '三共振确认「真收紧」',
                        f"DR001偏离{dev:+.1f}bp，分层{tier:.1f}bp" if dev is not None and tier is not None
                        else liq.get('cb_stance') or '')
        elif label == '结构性收紧':
            self._alert('资金面', '结构性收紧（传导阻塞）',
                        f"非银分层 {tier:.1f}bp，银行不缺钱但非银溢价走阔" if tier is not None else '')
        elif label == '技术性扰动':
            self._warn('资金面', '技术性扰动', f"DR001偏离{dev:+.1f}bp，属时点因素" if dev is not None else '')
        for a in (liq.get('alerts') or []):
            if '真收紧' not in str(a):
                self._alert('资金面', str(a)[:60])

    # ---- 4. 回购市场 ----
    def _check_repo(self):
        d = self._data
        analysis = d.get('analysis') or {}
        # 调用方传的 repo_vol_chg 实际是 DR001 **利率**的日环比（(今-昨)/昨），
        # 不是回购成交量变动，直接拿来判「成交量放大/萎缩」会完全错位。
        # 这里用 data_hub 的回购成交量序列自算环比。
        vol_chg = None
        if E is not None:
            v_now, _ = E.point('LIQ.CN.REPO.VOL.DR001')
            v_prev = E.prev('LIQ.CN.REPO.VOL.DR001', 1)
            if v_now and v_prev:
                vol_chg = v_now / v_prev - 1.0
        if vol_chg is not None and abs(vol_chg) * 100 >= TH['repo_vol_chg_pct']:
            direction = '放大' if vol_chg > 0 else '萎缩'
            level_fn = self._alert if vol_chg < 0 else self._warn
            level_fn('回购', f'DR001成交量{direction} {abs(vol_chg)*100:.0f}%',
                     '成交萎缩伴随利率上行=真紧信号' if vol_chg < 0 else '成交放大，资金需求旺盛')
        fdr = analysis.get('FDR007') if isinstance(analysis, dict) else None
        if isinstance(fdr, dict):
            pct = fdr.get('percentile')
            if pct is not None and pct >= 80:
                self._alert('回购', f'FDR007 处近1年 {pct:.0f}% 分位', '银行间资金价格偏高')
        # circulation_daily 把非银分层放在 analysis['nonbank_spread']，
        # 字段为 current_bp / percentile / alert，没有顶层 spread_percentile。
        nonbank = analysis.get('nonbank_spread') if isinstance(analysis, dict) else None
        if isinstance(nonbank, dict):
            tier_pct = nonbank.get('percentile')
            tier_bp = nonbank.get('current_bp')
            if tier_pct is not None and tier_pct >= 80:
                self._alert('回购', f'非银分层 {tier_bp}bp 处近1年 {tier_pct:.0f}% 分位',
                            '非银融资压力大，分层走阔')

    # ---- 5. 美联储 ----
    def _check_fed(self):
        fed = self._data.get('fed')
        bs = self._data.get('bs')
        if isinstance(fed, dict):
            res = fed.get('reserve')
            rrp_on = fed.get('rrp_on')
            tga = fed.get('tga')
            if res is not None and res / 1e6 < TH['reserve_low_tn']:
                self._warn('美联储', f'准备金 ${res/1e6:.2f}T', f'低于 {TH["reserve_low_tn"]:.1f}T 阈值')
            if rrp_on is not None:
                bn = rrp_on / 1e3
                if bn <= TH['rrp_zero_bn']:
                    self._alert('美联储', f'ON RRP 已归零（${bn:.2f}B）', 'QT 直接冲击准备金，缓冲垫失效')
                elif bn <= TH['rrp_dry_bn']:
                    self._alert('美联储', f'ON RRP 仅 ${bn:.2f}B', '逼近枯竭，隔夜利率随时跳升')
            if tga is not None and tga / 1e6 > TH['tga_high_tn']:
                self._warn('美联储', f'TGA ${tga/1e6:.2f}T', '财政账户高位，持续抽走银行准备金')
        if isinstance(bs, dict):
            # fetch_fed_balance_sheet() 返回 {'treasuries','mbs','reserve','rrp_on','tga',...}，
            # 没有 weekly_change 字段，周变要自己按 5 个交易日算。
            chg = E.chg('LIQ.US.FED.BS', 5) if E is not None else None
            if chg is not None and abs(chg) > 30e3:
                direction = '扩张' if chg > 0 else '收缩'
                self._warn('美联储', f'资产负债表周{direction} ${abs(chg)/1e3:.0f}B', 'QT 进度')

    # ---- 6. TGA ----
    def _check_tga(self):
        tga = self._data.get('tga')
        if isinstance(tga, dict):
            net = tga.get('net_change')
            if net is not None and abs(net) > 30e3:
                direction = '补库' if net > 0 else '消耗'
                fn = self._warn if net > 0 else None
                if fn:
                    fn('财政', f'TGA 单日{direction} ${abs(net)/1e3:.0f}B', '抽走银行准备金')

    # ---- 7. 货币市场 ----
    def _check_money_market(self):
        d = self._data
        sofr_effr = d.get('sofr_effr_bp')
        sofr_pct = d.get('sofr_effr_pct')
        effr_iorb = d.get('effr_iorb_bp')
        srf_max = d.get('srf_max_1d')
        srf_sum = d.get('srf_amt_5d')
        srf_sofr = d.get('srf_sofr_bp')

        if sofr_effr is not None and sofr_effr >= TH['mm_spread_bp']:
            self._alert('货币市场', f'SOFR-EFFR {sofr_effr:+.1f}bp',
                        f'≥{TH["mm_spread_bp"]:.0f}bp，回购市场分层加剧')
        if sofr_pct is not None and sofr_pct >= TH['sofr_effr_pct']:
            self._alert('货币市场', f'SOFR-EFFR 处历史 {sofr_pct:.0f}% 分位', '利差处极端水平')
        if effr_iorb is not None and effr_iorb >= TH['effr_iorb_bp']:
            self._alert('货币市场', f'EFFR 高于央行利率下沿 {effr_iorb:+.1f}bp',
                        f'≥{TH["effr_iorb_bp"]:.0f}bp，联邦基金市场偏离目标区间')
        if srf_max is not None and srf_max > TH['srf_trigger_mn']:
            self._alert('货币市场', f'SRF 被触发（单日 ${srf_max:.0f}M）',
                        f'近5日合计 ${srf_sum:.0f}M' if srf_sum is not None else '常备回购便利被使用=准备金短缺')
        if srf_sofr is not None and srf_sofr >= TH['mm_spread_bp']:
            self._warn('货币市场', f'SRF-SOFR 利差 {srf_sofr:+.1f}bp', '上限工具被逼近')

    # ---- 8. 利率 ----
    def _check_rates(self):
        d = self._data
        us10 = d.get('us10y_chg_bp')
        shib = d.get('shib_chg_bp')
        spread = d.get('spread_bp')
        spread_g = d.get('spread_g_bp')
        cny = d.get('cny_chg_pips')

        if us10 is not None and abs(us10) >= TH['us10y_chg_bp']:
            self._warn('利率', f'美债10Y 单日 {us10:+.1f}bp', '关注对汇率与风险资产的传导')
        if shib is not None and abs(shib) >= TH['shib_chg_bp']:
            self._warn('利率', f'Shibor 单日 {shib:+.1f}bp', '国内资金价格波动')
        if spread is not None and abs(spread) >= TH['spread_bp']:
            self._warn('利率', f'中美利差 {spread:+.0f}bp', '绝对水平偏离，关注跨境资金')
        # circulation_daily 的 spread_g_bp = (SOFR - IOER) × 100，是美元货币市场利差，
        # 不是中美利差单日变动，标签必须写清楚。
        if spread_g is not None and abs(spread_g) >= TH['spread_g_bp']:
            self._warn('利率', f'SOFR-IOER 利差 {spread_g:+.1f}bp', '美元货币市场分层')
        if cny is not None and abs(cny) >= TH['cny_chg_pips']:
            self._alert('汇率', f'人民币单日 {cny:+.0f}pips', '汇率波动超阈值，关注掉期点与跨境套利')

    # ---- 9. 掉期点 ----
    def _check_swap(self):
        swap = self._data.get('swap_annualized')
        if swap is None:
            return
        if abs(swap) >= TH['swap_abs_pct']:
            direction = '贴水' if swap < 0 else '升水'
            self._warn('掉期', f'1Y 掉期年化 {swap:+.2f}%（{direction}）',
                       f'绝对水平 ≥{TH["swap_abs_pct"]:.1f}%，rrr=R+e 框架下 e 影响显著')

    # ---- 10. 海外 ----
    def _check_swap_demand(self):
        """1Y 外汇掉期点 × 需求传导（rrr = R + e，中性 5.5%）。

        e 是贴水幅度（%），rrr 越高代表风险资产贴现率越限制性、越抑制需求。
        """
        sd = self._data.get('swap_demand')
        if not isinstance(sd, dict) or not sd.get('ok'):
            return
        e = sd.get('e')
        rrr = sd.get('rrr')
        gap = sd.get('gap')
        tag = sd.get('level_tag')
        dm = sd.get('demand') or {}
        d5 = (sd.get('chg') or {}).get('d5')
        if e is None or rrr is None:
            return

        # 水位：高压极值 / 内需警报 / 宽松刺激
        if tag == '高压极值':
            self._alert('掉期点',
                        f'rrr {rrr:.2f}% 处高压极值（e {e:.2f}%，偏离中性 {gap:+.2f}pp）',
                        f'需求{dm.get("verdict", "抑制")}，风险资产贴现率深度限制性')
        elif tag == '内需警报':
            self._alert('掉期点',
                        f'rrr {rrr:.2f}% 超中性 {gap:+.2f}pp（e {e:.2f}%）',
                        '进入内需警报区间')
        elif tag == '宽松刺激':
            self._warn('掉期点', f'e {e:.2f}% < 1.6%，rrr {rrr:.2f}%',
                       '宽松刺激区间，需求端转暖信号')

        # 边际：贴水周内快速加深 / 收窄
        if d5 is not None:
            if d5 >= TH['swap_e_week_bp']:
                self._alert('掉期点',
                            f'e 周内 +{d5:.0f}bp（rrr 有效 +{d5 * 2:.0f}bp）',
                            '贴水快速加深，需求抑制加强')
            elif d5 <= -TH['swap_e_week_bp']:
                self._warn('掉期点',
                           f'e 周内 {d5:.0f}bp（rrr 有效 {d5 * 2:.0f}bp）',
                           '贴水收窄，需求边际改善')

    def _check_overseas(self):
        d = self._data
        jgb = d.get('jgb')
        # fetch_jgb() 返回的是扁平期限字典 {'2Y':..,'10Y':2.841,..}，没有 current/change_bp
        if isinstance(jgb, dict):
            cur = jgb.get('10Y')
            if cur is not None and cur >= TH['jgb_10y']:
                self._alert('日本', f'JGB 10Y {cur:.3f}%', f'≥{TH["jgb_10y"]:.1f}%，日元套息交易成本抬升')
            chg = E.bp('RATE.JP.10Y', 1) if E is not None else None
            if chg is not None and abs(chg) >= 5:
                self._warn('日本', f'JGB 10Y 单日 {chg:+.1f}bp', '日本长端异动，关注套息平仓')
        # 调用方传入的 hibor_1m 实际取的是 LIQ.HK.HIBOR.3M（fetch_hibor 的 current 口径），
        # 这里统一按 1M 重新取，避免标签与数据错配。
        hibor = E.point('LIQ.HK.HIBOR.1M')[0] if E is not None else d.get('hibor_1m')
        if hibor is not None and hibor >= TH['hibor_1m']:
            self._alert('香港', f'HIBOR 1M {hibor:.3f}%', f'≥{TH["hibor_1m"]:.1f}%，港元流动性收紧')

    # ---- 11. 同业存单 ----
    def _check_cd(self):
        cd = self._data.get('cd_alert')
        if cd:
            self._alert('同业存单', str(cd)[:120], '银行负债端压力')
            return
        # circulation_daily 侧没有稳定产出 cd_alert 变量，这里兜底自判：
        # 1Y AAA 存单单日变动超过 10bp 视为银行负债端异动。
        if E is None:
            return
        chg = E.bp('LIQ.CN.CD.1Y', 1)
        if chg is not None and abs(chg) >= 10.0:
            direction = '上行' if chg > 0 else '下行'
            self._warn('同业存单', f'1Y AAA 存单单日{direction} {chg:+.1f}bp',
                       '银行负债端成本异动，关注存单-资金面利差')

    # ---- 12. 指纹 ----
    def _check_fingerprint(self):
        d = self._data
        top = d.get('fp_top')
        score = d.get('fp_top_score')
        confirm = d.get('fp_price_confirm')
        if top and score is not None and score >= TH['fp_top_score']:
            self._alert('美元流动性', f'主因「{top}」贡献 {score:.0f} 分',
                        f'≥{TH["fp_top_score"]:.0f}，流动性收紧高度集中')
        if confirm == 'confirmed':
            self._alert('美元流动性', '收紧已获价格确认', 'DXY/VIX/标普共振')

    # ---------------- 查询 ----------------

    @property
    def alerts(self) -> List[AlertItem]:
        return [x for x in self.items if x.level == 'alert']

    @property
    def warns(self) -> List[AlertItem]:
        return [x for x in self.items if x.level == 'warn']

    def counts(self) -> Dict[str, int]:
        return {'alert': len(self.alerts), 'warn': len(self.warns)}

    def headline(self) -> str:
        c = self.counts()
        if not self.items:
            return '无告警'
        top = self.alerts[0].title if self.alerts else self.warns[0].title
        return f"报警 {c['alert']} 项 / 警告 {c['warn']} 项 | 首要: {top}"

    # ---------------- 渲染 ----------------

    def render_md(self) -> List[str]:
        """插入日报标题下方的告警段。"""
        if not self.items:
            return []
        c = self.counts()
        out: List[str] = []
        icon = '🚨' if c['alert'] else '⚠️'
        out.append(f"\n## {icon} 告警汇总（报警 {c['alert']} / 警告 {c['warn']}）")
        if self.alerts:
            out.append("\n**🔴 报警（阈值击穿/方向恶化）**:")
            for it in self.alerts:
                out.append(it.to_md())
        if self.warns:
            out.append("\n**🟡 警告（数据缺失/关注项，不发信）**:")
            for it in self.warns:
                out.append(it.to_md())
        out.append("")
        return out

    def render_card_lines(self) -> List[str]:
        """飞书卡片置顶告警段（更紧凑）。"""
        if not self.items:
            return ['✅ **告警汇总**: 无告警']
        c = self.counts()
        icon = '🚨' if c['alert'] else '⚠️'
        out = [f"{icon} **告警汇总**: 报警 {c['alert']} 项 / 警告 {c['warn']} 项"]
        for it in self.alerts[:5]:
            out.append(it.to_card())
        if c['alert'] > 5:
            out.append(f"- …… 另有 {c['alert'] - 5} 项报警")
        if self.warns:
            brief = '；'.join(x.title for x in self.warns[:3])
            more = f" 等{len(self.warns)}项" if len(self.warns) > 3 else ''
            out.append(f"- ⚠️ 警告: {brief}{more}")
        return out

    # ---------------- 邮件 ----------------

    def should_email(self) -> bool:
        return len(self.alerts) >= EMAIL_ALERT_THRESHOLD

    def _build_html(self) -> str:
        c = self.counts()
        rows = []
        for it in self.alerts:
            color = '#c0392b'
            rows.append(f"<tr><td style='color:{color};padding:6px 8px;border-bottom:1px solid #eee;'>"
                        f"🚨 {it.category}</td>"
                        f"<td style='padding:6px 8px;border-bottom:1px solid #eee;'>{it.title}"
                        f"{('<br><span style=\"color:#666;font-size:12px;\">' + it.detail + '</span>') if it.detail else ''}"
                        f"</td></tr>")
        warn_rows = []
        for it in self.warns:
            warn_rows.append(f"<tr><td style='color:#b8860b;padding:6px 8px;border-bottom:1px solid #eee;'>"
                             f"⚠️ {it.category}</td>"
                             f"<td style='padding:6px 8px;border-bottom:1px solid #eee;'>{it.title}</td></tr>")

        html = [
            "<div style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;max-width:720px;'>",
            f"<h2 style='margin:0 0 4px;'>🚨 环流日报告警 | {self.date}</h2>",
            f"<p style='color:#666;margin:0 0 16px;'>报警 <b style='color:#c0392b;'>{c['alert']}</b> 项"
            f" / 警告 {c['warn']} 项</p>",
        ]
        if self.alerts:
            html.append("<h3 style='margin:16px 0 6px;color:#c0392b;'>🔴 报警</h3>")
            html.append("<table style='border-collapse:collapse;width:100%;font-size:14px;'>")
            html.extend(rows)
            html.append("</table>")
        if warn_rows:
            html.append("<h3 style='margin:16px 0 6px;color:#b8860b;'>🟡 警告</h3>")
            html.append("<table style='border-collapse:collapse;width:100%;font-size:14px;'>")
            html.extend(warn_rows)
            html.append("</table>")
        html.append("<p style='color:#999;font-size:12px;margin-top:20px;'>"
                    "本邮件由环流日报告警体系自动发出，报警数达到阈值 "
                    f"{EMAIL_ALERT_THRESHOLD} 项即触发。案例分析学习材料，不构成投资建议。</p>")
        html.append("</div>")
        return ''.join(html)

    def send_email(self) -> bool:
        """发送报警邮件，返回是否成功。幂等由调用方控制。"""
        if self._mailed:
            return True
        try:
            sys.path.insert(0, _cp('/Coze/Drive/蓁宝/所有对话/主对话'))
            from email_sender import send_email as _send
        except Exception as e:
            print(f"  [WARN] 告警邮件模块导入失败: {e}")
            return False
        subject = f"🚨 环流日报告警 | {self.date} | 报警 {len(self.alerts)} 项"
        try:
            ok = _send(subject=subject, html=self._build_html(), source=EMAIL_SOURCE)
        except Exception as e:
            print(f"  [WARN] 告警邮件发送异常: {e}")
            return False
        if ok:
            self._mailed = True
            print(f"  ✅ 告警邮件已发送（报警 {len(self.alerts)} 项）")
        else:
            print("  [WARN] 告警邮件发送失败")
        return bool(ok)
