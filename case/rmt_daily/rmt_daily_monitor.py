#!/usr/bin/env python3
"""
RMT 每日市场监测 + 个股推荐
拉取 18 只行业 ETF 行情，计算 RMT 指标，输出 Top5 动量行业 + 每个行业 Top3 个股
另附宽基指数观测块：科创50（000688）与创业板指（399006），以沪深300 为基准计算相对强弱。
⚠️ 两只宽基不并入 18 只行业 ETF 相关矩阵，RMT 的 Gap1/熵/ΔS/SNR 口径与红/绿灯阈值校准保持不变。
"""
import os, sys, json, time, requests, re, warnings, smtplib, subprocess
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

warnings.filterwarnings('ignore')

# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
# ETF/指数日线与快照、个股日线全部改由 data_hub 提供，本文件不再直连腾讯行情接口。
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''


WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)
from stock_picker import get_top_stocks_for_etf, ETF_STOCK_POOL

FEISHU_WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8'
REPORT_HEADER = '📊 RMT 随机矩阵每日监测'

ETF_LIST = {
    '512480': 'sh', '512760': 'sh', '515880': 'sh', '515050': 'sh',
    '510300': 'sh', '510500': 'sh', '512100': 'sh', '512890': 'sh',
    '512200': 'sh', '512010': 'sh', '512690': 'sh', '512800': 'sh',
    '512660': 'sh', '159915': 'sz', '510050': 'sh', '510880': 'sh',
    '512720': 'sh', '159928': 'sz',
}
ETF_NAMES = {
    '512480': '半导体', '512760': '芯片', '515880': '通信', '515050': '5G通信',
    '510300': '沪深300', '510500': '中证500', '512100': '中证1000', '512890': '红利低波',
    '512200': '房地产', '512010': '医药', '512690': '酒', '512800': '银行',
    '512660': '军工', '159915': '创业板', '510050': '上证50', '510880': '红利',
    '512720': '计算机', '159928': '消费',
}

# ================= 红/绿灯预警阈值 =================
# 来源：2024-11~2026-10 共469个交易日 RMT 指标重算 + 事件窗口校准（_hist_calib.py）
#   风险侧：ΔS≥+0.08 后 5 日 -0.48%（胜率44.4%），命中7月风险窗3天、10月末风险窗1天
#   机会侧：熵≤1.45 命中9月机会窗14天；ΔS≤-0.06 后 10 日 +2.73%（胜率88.0%）
#   熵≥1.55 不作风险信号（10月末漏报、前瞻收益反而为正）
ALERT_RED_DELTA_S = 0.08       # 风险红灯：ΔS ≥ +0.08 → 发邮件
ALERT_WARN_DELTA_S = 0.05      # 风险黄灯：ΔS ≥ +0.05（仅日志标记，不发邮件）
ALERT_GREEN_ENTROPY = 1.45     # 机会绿灯：熵 ≤ 1.45 → 发邮件
ALERT_GREEN_DELTA_S = -0.06    # 机会深绿灯：ΔS ≤ -0.06 → 发邮件

# ---- 邮件通道（复用既有工作区凭据，本文件不新增任何密钥）----
EMAIL_TO = '410119229@qq.com'
DWS_CLI = os.path.expanduser('~/.local/bin/dws')          # 主通道：钉钉企业邮箱
EMAIL_FROM = 'baizhiwei9492@dingtalk.com'
EMAIL_HOST = 'smtp.163.com'                               # 回退通道：163 SMTP
EMAIL_SSL_PORT = 465
EMAIL_TLS_PORT = 25
EMAIL_USER = 'songshang951@163.com'
_WS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace'
_AUTH_PATHS = [
    os.path.join(_WS, 'ses_gffe5f530aa190ffeRxnRiUPRdvAPh0', 'daily_wavelet_monitor.py'),
    os.path.join(_WS, 'ses_gffe5f530aa190ffeRxnRiUPRdvAPh0', 'test_email_only.py'),
]

# ---- 宽基指数观测（独立模块）----
# 说明：科创50与创业板指作为独立观测块纳入分析，不并入 18 只行业 ETF 相关矩阵，
#      因此 RMT 的 Gap1/熵/ΔS/SNR 口径与红/绿灯阈值校准全部保持不变。
BROAD_INDEX = {
    'sh000688': '科创50',       # 科创50指数（000688.SH）
    'sz399006': '创业板指',      # 创业板指（399006.SZ）
    'sh000905': '中证500',      # 中证500指数（000905.SH）
    'sh000852': '中证1000',     # 中证1000指数（000852.SH）
}
BROAD_BENCH = ('sh000300', '沪深300')   # 基准，仅用于计算相对强弱


_INDEX_SUFFIX = {
    '000001': '.SH', '000016': '.SH', '000300': '.SH', '000688': '.SH',
    '000905': '.SH', '000852': '.SH', '000985': '.CSI',
    '399001': '.SZ', '399006': '.SZ', '399106': '.SZ',
}


def _split_symbol(code):
    """'sh512480' -> ('etf','512480')；'sh000300' -> ('index','000300.SH')。"""
    raw = str(code).strip().lower()
    prefix = raw[:2] if raw[:2] in ('sh', 'sz') else ''
    num = (raw[2:] if prefix else raw).split('.')[0]
    if num.startswith(('15', '16', '51', '52', '56', '58')):
        return 'etf', num
    if num in _INDEX_SUFFIX:
        return 'index', num + _INDEX_SUFFIX[num]
    return 'index', num + ('.SH' if (prefix == 'sh' or num.startswith('000')) else '.SZ')


def _norm_date(value):
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return f'{text[:4]}-{text[4:6]}-{text[6:8]}'
    return text[:10]


def fetch_kline(code, days=800):
    """DataHub 股票模块前复权日线：返回 {date: close}。"""
    kind, sym = _split_symbol(code)
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, start_date=start, end_date=end)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, start_date=start, end_date=end)
        if df is None or len(df) == 0:
            print(f'  dataHub {code}({sym}) 日线返回空')
            return {}
        dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
        bars = {}
        for _, row in df.iterrows():
            try:
                bars[_norm_date(row[dcol])] = float(row['close'])
            except Exception:
                continue
        return bars
    except Exception as e:
        print(f'  fetch_kline({code}) dataHub error: {e}')
        return {}


def fetch_realtime_close(code):
    """DataHub 快照取当日收盘价（盘后即当日收盘价）。code 形如 sh510300 / sz159915。"""
    kind, sym = _split_symbol(code)
    today = datetime.now().strftime('%Y%m%d')
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_etf_daily
            df = get_etf_daily(code=sym, trade_date=today)
        else:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=sym, trade_date=today)
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]['close'])
    except Exception:
        pass
    try:
        if kind == 'etf':
            from data_hub.collectors.misc import get_realtime_spot
            px, _src = get_realtime_spot(sym, verbose=False)
            return px
        from data_hub.collectors.stocks import get_index_spot
        spot = get_index_spot(index_codes=[code])
        if spot is not None and len(spot) > 0 and pd.notna(spot.iloc[0].get('price')):
            return float(spot.iloc[0]['price'])
    except Exception:
        pass
    return None


def is_trading_day():
    """检查今天是否为交易日（DataHub 交易日历 CN.MARKET.CAL.TRADE）。"""
    today = datetime.now().date()
    if today.weekday() >= 5:  # 周六、周日
        return False
    try:
        from data_hub import edb
        day = today.strftime('%Y%m%d')
        cal = edb.get_dataset('CN.MARKET.CAL.TRADE', start_date=day, end_date=day)
        return cal is not None and len(cal) > 0
    except Exception as e:
        print(f'  is_trading_day dataHub 交易日历不可用: {e}')
        return True


def load_data():
    """拉取 18 只 ETF 日线。

    数据新鲜度铁律：
    1. 最新交易日日期必须 >= 今天（非交易日时可能是最近交易日，仅告警不拒发）；
    2. K线接口未返回最新数据的代码逐只重试一次；
    3. 仍缺失的用实时行情接口(qt.gtimg.cn)补齐当日收盘价；
    4. 依然缺失则拒绝继续（绝不静默 ffill 旧值）。
    """
    print('Fetching ETF data...')
    all_bars = {}
    for code, prefix in ETF_LIST.items():
        bars = fetch_kline(f'{prefix}{code}', days=800)
        for d, c in bars.items():
            if d not in all_bars:
                all_bars[d] = {}
            all_bars[d][code] = c
        time.sleep(0.15)

    dates_sorted = sorted(all_bars.keys())
    max_d = str(dates_sorted[-1]) if dates_sorted else None

    # 第一轮重试：K线接口盘后分批更新，缺最新日的代码逐只再拉一次
    if max_d:
        for code, prefix in ETF_LIST.items():
            if code not in all_bars.get(max_d, {}):
                time.sleep(1.0)
                bars = fetch_kline(f'{prefix}{code}', days=800)
                for d, c in bars.items():
                    if d not in all_bars:
                        all_bars[d] = {}
                    all_bars[d][code] = c
                time.sleep(0.3)

    # 第二轮兜底：仍缺最新日的代码用实时接口补当日收盘价
    if max_d:
        still_missing = [c for c in ETF_LIST
                         if c not in all_bars.get(max_d, {})]
        for code in still_missing:
            rp = ETF_LIST[code]
            rv = fetch_realtime_close(f'{rp}{code}')
            if rv is not None:
                all_bars.setdefault(max_d, {})[code] = rv
                print(f'  实时兜底 {code}({ETF_NAMES.get(code, code)}) {max_d}: {rv}')
            else:
                print(f'  !! {code}({ETF_NAMES.get(code, code)}) 实时接口也无数据')

    dates_sorted = sorted(all_bars.keys())
    etf_codes = list(ETF_LIST.keys())
    df = pd.DataFrame({'date': pd.to_datetime(dates_sorted)})
    for code in etf_codes:
        df[code] = [all_bars[d].get(code, np.nan) for d in dates_sorted]
    etf_codes = [c for c in etf_codes if df[c].notna().sum() > 10]

    # 数据闸门：最新交易日每列必须真实存在，缺失绝不静默 ffill
    latest_d = df['date'].max().date()
    today = datetime.now().date()
    last_row = df.iloc[-1]
    missing = [c for c in etf_codes if pd.isna(last_row[c])]
    completeness = len(etf_codes) - len(missing)
    print(f'  ETF: {len(df)} bars, {len(etf_codes)} codes, latest {latest_d}, '
          f'完整度 {completeness}/{len(etf_codes)}')
    if latest_d < today:
        if is_trading_day():
            print(f'[DATA-GATE] ETF数据日期({latest_d})落后于今天({today})，拒绝使用旧数据')
            sys.exit(3)
        else:
            print(f'[DATA-GATE] 今日非交易日，使用最近交易日({latest_d})数据（仅告警不拒发）')
    if missing:
        print(f'[DATA-GATE] 以下ETF缺少最新交易日({latest_d})真实数据且无法补齐: '
              f'{",".join(missing)}，拒绝使用旧数据')
        sys.exit(3)

    # 历史内部缺口用 ffill/bfill 补齐（最新交易日行已确认真实，不受影响）
    df[etf_codes] = df[etf_codes].ffill().bfill()
    print(f'  ETF final: latest {df["date"].max().date()}')
    return df, etf_codes


def load_broad_index():
    """拉取宽基指数日线并计算形态指标（科创50、创业板指 + 沪深300 基准）。

    与 18 只 ETF 相关矩阵完全隔离：某只指数取不到最新交易日真实数据时，
    只在观测块内标注「数据缺失」，不阻断主流程、不影响 RMT 与红/绿灯判定。
    沿用与 ETF 相同的数据新鲜度规则：K线缺最新日 → 实时接口兜底 → 仍缺则标注缺失。
    """
    print('\nFetching broad-based indices...')
    today = datetime.now().date()
    want = dict(BROAD_INDEX)
    want[BROAD_BENCH[0]] = BROAD_BENCH[1]

    raw, last_dates = {}, {}
    for code in want:
        bars = fetch_kline(code, days=800)
        raw[code] = bars
        if bars:
            last_dates[code] = sorted(bars)[-1]
        time.sleep(0.15)

    if not last_dates:
        print('  [宽基] 未取到任何指数数据，本次跳过宽基观测块')
        return {}
    latest = max(last_dates.values())
    if latest < str(today):
        note = '（今日非交易日，使用最近交易日）' if not is_trading_day() else '（落后于今日，如实标注数据日）'
        print(f'  [宽基] 最新交易日 {latest}{note}')

    out = {}
    for code, name in want.items():
        bars = dict(raw.get(code) or {})
        if latest not in bars:
            rv = fetch_realtime_close(code)
            if rv is not None:
                bars[latest] = rv
                print(f'  宽基实时兜底 {code}({name}) {latest}: {rv}')
        ds = sorted(bars)
        if latest not in bars or len(ds) < 61:
            reason = '最新交易日无真实数据（K线与实时接口均无）' if latest not in bars else '历史样本不足60日'
            print(f'  [宽基] {name}({code}) {reason}，本次标记为数据缺失')
            out[code] = {'name': name, 'code': code, 'missing': True, 'reason': reason}
            continue
        c = [bars[d] for d in ds]
        close = float(c[-1])
        hi60, lo60 = max(c[-60:]), min(c[-60:])
        ma20 = sum(c[-20:]) / 20.0
        ma60 = sum(c[-60:]) / 60.0
        out[code] = {
            'name': name, 'code': code, 'date': str(latest), 'close': close,
            'chg_1': float(close / c[-2] - 1),
            'mom_20': float(close / c[-21] - 1),
            'mom_60': float(close / c[-61] - 1),
            'drawdown_60': float(close / hi60 - 1),
            'range_pos_60': float((close - lo60) / (hi60 - lo60)) if hi60 > lo60 else None,
            'high_60': float(hi60), 'low_60': float(lo60),
            'ma20': float(ma20), 'ma60': float(ma60),
            'ma_state': ('多头排列' if close > ma20 > ma60
                         else ('空头排列' if close < ma20 < ma60 else '震荡/转换')),
            'missing': False,
        }

    b = out.get(BROAD_BENCH[0]) or {}
    b_mom = None if b.get('missing', True) else b.get('mom_20')
    for code, v in out.items():
        if v.get('missing'):
            continue
        v['excess_20'] = (float(v['mom_20'] - b_mom)
                          if (b_mom is not None and code != BROAD_BENCH[0]) else None)
    got = [f"{v['name']} {v['close']:.2f}" for v in out.values() if not v.get('missing')]
    print(f'  宽基: {"、".join(got) if got else "全部数据缺失"} | 数据日 {latest}')
    return out


def broad_data_date(broad):
    """宽基观测块的数据日（取其中最新且非缺失者）。"""
    ds = [v['date'] for v in (broad or {}).values()
          if not v.get('missing') and v.get('date')]
    return max(ds) if ds else None


def format_broad_block(broad):
    """宽基指数观测块文本行（Markdown 与飞书 lark_md 通用）。

    每只指数一行：名称 收盘价（当日涨跌）｜20日涨幅｜60日区间位置｜均线状态｜较沪深300超额。
    沪深300 仅作基准，用于计算科创50/创业板指的相对强弱。
    """
    if not broad:
        return []
    order = [c for c in BROAD_INDEX if c in broad] + [c for c in broad if c not in BROAD_INDEX]
    lines = []
    for code in order:
        v = broad[code]
        if v.get('missing'):
            lines.append(f"{v['name']}（{v['code']}）：数据缺失（{v.get('reason', '')}）")
            continue
        tag = '（基准）' if code == BROAD_BENCH[0] else ''
        chg = v.get('chg_1')
        chg_s = f"（当日 {chg*100:+.2f}%）" if chg is not None else ''
        pos = v.get('range_pos_60')
        pos_s = f"{pos*100:.0f}%" if pos is not None else 'n/a'
        ex = v.get('excess_20')
        ex_s = f"｜较沪深300 {ex*100:+.2f}%" if ex is not None else ''
        lines.append(f"{v['name']}{tag} {v['close']:.2f}{chg_s}｜20日 {v['mom_20']*100:+.2f}%｜"
                     f"60日区间位置 {pos_s}｜{v['ma_state']}{ex_s}")
    return lines


def compute_rmt(ret_df):
    WINDOW = 60
    ret_window = ret_df.iloc[-WINDOW:]
    C = ret_window.corr().values
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    C = (C + C.T) / 2
    eigs = np.sort(np.linalg.eigvalsh(C))[::-1]
    eigs = np.maximum(eigs, 1e-12)

    N = len(ret_window.columns)
    q = N / len(ret_window)
    lambda_plus = (1 + np.sqrt(q)) ** 2
    gap_1 = float((eigs[0] - eigs[1]) / eigs[0])
    p = eigs / eigs.sum()
    mask = p > 1e-12
    entropy = float(-np.sum(p[mask] * np.log(p[mask])))
    snr = float(eigs[0] / (np.mean(eigs[1:]) + 1e-12))

    if len(ret_df) >= WINDOW + 5:
        prev_window = ret_df.iloc[-WINDOW - 5:-5]
        prev_C = prev_window.corr().values
        prev_C = np.nan_to_num(prev_C, nan=0.0)
        np.fill_diagonal(prev_C, 1.0)
        prev_C = (prev_C + prev_C.T) / 2
        prev_eigs = np.sort(np.linalg.eigvalsh(prev_C))[::-1]
        prev_eigs = np.maximum(prev_eigs, 1e-12)
        prev_p = prev_eigs / prev_eigs.sum()
        prev_mask = prev_p > 1e-12
        prev_entropy = float(-np.sum(prev_p[prev_mask] * np.log(prev_p[prev_mask])))
        delta_s = entropy - prev_entropy
    else:
        delta_s = 0.0

    if gap_1 < 0.10:
        status, position, action, template = '简并共振警报', 0.90, '减仓10%', 'red'
    elif delta_s > 0.03:
        status, position, action, template = '危机预警', 0.70, '减仓30%', 'red'
    elif delta_s < -0.10:
        status, position, action, template = '恐慌底', 1.00, '满仓抄底', 'green'
    else:
        status, position, action, template = '正常', 1.00, '满仓', 'blue'

    mom_20 = ret_df.iloc[-20:].sum()
    top5 = pick_industries(mom_20, top=True)
    bottom5 = pick_industries(mom_20, top=False)

    return {
        'gap_1': gap_1, 'entropy': entropy, 'delta_s': float(delta_s),
        'snr': snr, 'lambda_plus': float(lambda_plus),
        'status': status, 'position': position, 'action': action, 'template': template,
        'top5': top5.to_dict(), 'bottom5': bottom5.to_dict(),
        'eigs_top5': eigs[:5].tolist(),
        'n_assets': N, 'q': float(q),
    }


def evaluate_alert(rmt_r):
    """红/绿灯判定：红灯（风险）优先于绿灯（机会）。

    Returns: {'level': 'red'/'green'/'none', 'reasons': [...], 'watch': [...]}
    """
    risk, opp, watch = [], [], []
    if rmt_r['delta_s'] >= ALERT_RED_DELTA_S:
        risk.append(f"ΔS={rmt_r['delta_s']:+.4f} ≥ +{ALERT_RED_DELTA_S:.2f}（维度扩张，风险）")
    elif rmt_r['delta_s'] >= ALERT_WARN_DELTA_S:
        watch.append(f"ΔS={rmt_r['delta_s']:+.4f} ≥ +{ALERT_WARN_DELTA_S:.2f}（黄灯，未达红灯）")
    if rmt_r['entropy'] <= ALERT_GREEN_ENTROPY:
        opp.append(f"熵={rmt_r['entropy']:.4f} ≤ {ALERT_GREEN_ENTROPY:.2f}（低维共振，历史对应机会窗）")
    if rmt_r['delta_s'] <= ALERT_GREEN_DELTA_S:
        opp.append(f"ΔS={rmt_r['delta_s']:+.4f} ≤ {ALERT_GREEN_DELTA_S:.2f}（维度坍缩／恐慌底）")

    reasons = risk or opp
    if risk and opp:
        reasons = risk + [f"（同时满足机会条件：{'；'.join(opp)}，按风险优先处理）"]
    return {'level': 'red' if risk else ('green' if opp else 'none'),
            'reasons': reasons, 'watch': watch}


def get_email_auth_code():
    """163 授权码（仅回退通道用）：环境变量优先，缺失时读既有配置，不在本文件落盘。"""
    code = os.environ.get('EMAIL_AUTH_CODE', '')
    if code:
        return code
    for p in _AUTH_PATHS:
        try:
            with open(p, encoding='utf-8') as f:
                src = f.read()
            m = re.search(r"EMAIL_PASS\s*=\s*[\"']([^\"']+)[\"']", src)
            if m and len(m.group(1)) >= 6:
                return m.group(1)
        except Exception:
            continue
    return ''


def _md_to_html(md):
    """极简 Markdown → HTML（仅覆盖本邮件构造：标题/加粗/列表/表格/段落）。"""
    out, in_tbl, first_row = [], False, True

    def bold(t):
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)

    for raw in md.split('\n'):
        s = raw.rstrip()
        if s.startswith('|'):
            cells = [c.strip() for c in s.strip('|').split('|')]
            if cells and all(c and set(c) <= set('-: ') for c in cells):
                continue
            if not in_tbl:
                out.append('<table border="1" cellpadding="4" cellspacing="0" '
                           'style="border-collapse:collapse;font-size:13px">')
                in_tbl, first_row = True, True
            tag = 'th' if first_row else 'td'
            first_row = False
            out.append('<tr>' + ''.join(f'<{tag}>{bold(c)}</{tag}>' for c in cells) + '</tr>')
            continue
        if in_tbl:
            out.append('</table>')
            in_tbl = False
        if s.startswith('## '):
            out.append(f'<h2>{bold(s[3:])}</h2>')
        elif s.startswith('# '):
            out.append(f'<h1>{bold(s[2:])}</h1>')
        elif s.startswith('- '):
            out.append(f'<div>• {bold(s[2:])}</div>')
        elif s.startswith('  · '):
            out.append(f'<div style="padding-left:18px">{bold(s[4:])}</div>')
        elif s == '':
            out.append('<br>')
        else:
            out.append(f'<div>{bold(s)}</div>')
    if in_tbl:
        out.append('</table>')
    return ('<div style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;'
            'font-size:14px;line-height:1.6">' + '\n'.join(out) + '</div>')


def send_via_dws(subject, markdown_body):
    """主通道：钉钉企业邮箱（dws CLI）。"""
    try:
        cmd = [DWS_CLI, 'mail', 'message', 'send',
               '--from', EMAIL_FROM, '--to', EMAIL_TO,
               '--subject', subject, '--content', markdown_body,
               '--format', 'json']
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = proc.stdout.strip()
        if '"success": "true"' in out or '"success":"true"' in out:
            print(f'[邮件] dws 主通道发送成功 -> {EMAIL_TO}')
            return True
        print(f'[邮件] dws 返回异常: {(proc.stderr or out)[:200]}')
        return False
    except FileNotFoundError:
        print(f'[邮件] dws CLI 不存在: {DWS_CLI}')
        return False
    except Exception as e:
        print(f'[邮件] dws 调用失败: {str(e)[:200]}')
        return False


def send_via_smtp(subject, html_body):
    """回退通道：163 SMTP（先 SSL 465，失败再试 starttls 25）。"""
    code = get_email_auth_code()
    if not code:
        print('[邮件] 未取到 163 授权码，回退通道不可用')
        return False
    msg = MIMEText(html_body, 'html', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('DuMate RMT 监测', 'utf-8')), EMAIL_USER))
    msg['To'] = EMAIL_TO
    for mode in ('ssl', 'starttls'):
        try:
            if mode == 'ssl':
                server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_SSL_PORT, timeout=30)
            else:
                server = smtplib.SMTP(EMAIL_HOST, EMAIL_TLS_PORT, timeout=30)
                server.starttls()
            server.login(EMAIL_USER, code)
            server.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
            server.quit()
            print(f'[邮件] 163 SMTP({mode}) 回退通道发送成功 -> {EMAIL_TO}')
            return True
        except Exception as e:
            print(f'[邮件] 163 SMTP({mode}) 发送失败: {str(e)[:160]}')
    return False


def send_email(subject, markdown_body, html_body=None):
    """邮件发送：主通道 dws，失败回退 163 SMTP。"""
    if send_via_dws(subject, markdown_body):
        return True
    print('[邮件] 主通道失败，尝试 163 SMTP 回退...')
    return send_via_smtp(subject, html_body or _md_to_html(markdown_body))


def build_alert_body(rmt_r, alert, stock_picks, broad=None):
    """红/绿灯提醒邮件正文（Markdown，主通道直发 / 回退通道转 HTML）。"""
    today = datetime.now().strftime('%Y-%m-%d')
    tag = '🔴 RMT 风险红灯' if alert['level'] == 'red' else '🟢 RMT 机会绿灯'
    L = [f'## {tag} | {today}', '', '**触发原因**']
    L += [f'- {r}' for r in alert['reasons']]
    if alert['watch']:
        L += [f'- （黄灯提示）{w}' for w in alert['watch']]
    L += ['', '**核心指标（60 日相关矩阵）**', '',
          '| 指标 | 数值 | 说明 |', '| --- | --- | --- |',
          f"| Gap₁ | {rmt_r['gap_1']:.4f} | 最大/次大特征值间隙比 |",
          f"| 熵 | {rmt_r['entropy']:.4f} | Von Neumann 熵，高=多因子独立 |",
          f"| ΔS | {rmt_r['delta_s']:+.4f} | 当前熵 − 5 日前熵 |",
          f"| SNR | {rmt_r['snr']:.2f} | 最大特征值/其余均值 |",
          f"| λ+ | {rmt_r['lambda_plus']:.4f} | MP 随机矩阵上限 |",
          f"| 资产数/q | {rmt_r['n_assets']} / {rmt_r['q']:.3f} | 行业 ETF 数 |",
          '',
          f"**状态**：{rmt_r['status']} | **建议仓位**：{rmt_r['position']*100:.0f}%（{rmt_r['action']}）", '']
    broad_lines = format_broad_block(broad)
    if broad_lines:
        L += [f'**📈 宽基指数观测（科创50 / 创业板指，独立于 RMT 相关矩阵）**'
              f'　数据日 {broad_data_date(broad) or today}', '']
        L += [f'- {x}' for x in broad_lines]
        L += ['- 说明：两只宽基不并入 18 只行业 ETF 相关矩阵，故 RMT 指标与红/绿灯阈值口径不变', '']
    L += ['', '**🔥 Top5 动量行业（20 日）+ 个股推荐**', '']
    for i, (code, mom) in enumerate(rmt_r['top5'].items(), 1):
        L.append(f"- {i}. {ETF_NAMES.get(code, code)}（{code}）{mom*100:+.2f}%")
        for s in stock_picks.get(code, []):
            L.append(f"  · {s['name']}（{s['code']}）{s['mom_20']*100:+.1f}%")
    L += ['', '**❄️ Bottom5 弱势行业（20 日）**', '']
    for i, (code, mom) in enumerate(rmt_r['bottom5'].items(), 1):
        L.append(f"- {i}. {ETF_NAMES.get(code, code)}（{code}）{mom*100:+.2f}%")
    L += ['', '---', '',
          f"数据日期：{today}（腾讯前复权日线，60 日窗口）｜触发阈值：红灯 ΔS≥+{ALERT_RED_DELTA_S:.2f}；"
          f"绿灯 熵≤{ALERT_GREEN_ENTROPY:.2f} 或 ΔS≤{ALERT_GREEN_DELTA_S:.2f}", '',
          '本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。',
          '', '来自 DoMate']
    return '\n'.join(L)


def send_alert_email(rmt_r, alert, stock_picks, broad=None):
    """红/绿灯触发时发邮件。失败只告警，不影响飞书推送与报告保存。"""
    tag = '风险红灯' if alert['level'] == 'red' else '机会绿灯'
    key = '；'.join(re.sub(r'（.*', '', r) for r in alert['reasons'])
    subject = f"【RMT{tag}】{datetime.now().strftime('%Y-%m-%d')} {key} | 建议仓位{rmt_r['position']*100:.0f}%"
    body = build_alert_body(rmt_r, alert, stock_picks, broad)
    print(f'\n[ALERT] {tag}触发 -> 发送邮件 {EMAIL_TO}')
    if not send_email(subject, body):
        print('[邮件] 红/绿灯提醒发送失败（飞书推送与报告保存不受影响）')
        return False
    return True


def send_feishu(rmt_r, stock_picks, broad=None):
    today = datetime.now().strftime('%Y-%m-%d')

    # 宽基指数观测文本（科创50 / 创业板指，独立于 RMT 相关矩阵）
    broad_lines = format_broad_block(broad)
    broad_text = '\n'.join(f"- {x}" for x in broad_lines) if broad_lines else ''
    broad_date = broad_data_date(broad)

    # Top5 行业文本
    top5_lines = []
    for i, (code, mom) in enumerate(rmt_r['top5'].items(), 1):
        name = ETF_NAMES.get(code, code)
        stock_str = ''
        if code in stock_picks:
            stock_str = '\n  ' + '  '.join([f"{s['name']}({s['code']}) {s['mom_20']*100:+.1f}%" for s in stock_picks[code]])
        top5_lines.append(f"{i}. **{name}**（{code}）20日动量 {mom*100:+.2f}%{stock_str}")
    top5_text = '\n'.join(top5_lines)

    # Bottom5
    bottom5_lines = []
    for i, (code, mom) in enumerate(rmt_r['bottom5'].items(), 1):
        name = ETF_NAMES.get(code, code)
        bottom5_lines.append(f"{i}. {name}（{code}）{mom*100:+.2f}%")
    bottom5_text = '\n'.join(bottom5_lines)

    # 特征值
    eig_str = '  '.join([f'λ{i+1}={v:.3f}' for i, v in enumerate(rmt_r['eigs_top5'])])

    status_emoji = {'正常': '🟢', '危机预警': '🔴', '恐慌底': '🟡', '简并共振警报': '🟠'}.get(rmt_r['status'], '⚪')

    # 检测到风险：危机预警/简并共振警报 → 标题标记 + 弱势行业置顶
    has_risk = rmt_r['status'] in ('危机预警', '简并共振警报')
    has_opp = rmt_r['status'] == '恐慌底'
    title = f"{REPORT_HEADER} | {today} | 来自 DoMate"
    _mark = ('注意风险 | ' if has_risk else '') + ('注意机会 | ' if has_opp else '')
    if _mark:
        title = f"{REPORT_HEADER} | {today} | {_mark}来自 DoMate"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": rmt_r['template']
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md",
                         "content": f"**{status_emoji} 状态：{rmt_r['status']}** | 建议仓位：{rmt_r['position']*100:.0f}%（{rmt_r['action']}）"
                                   + (f"\n\n**⚠️ 检测到风险：弱势行业（20日动量最差）**\n{bottom5_text}" if has_risk else "")}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Gap₁（特征值间隙比）**\n{rmt_r['gap_1']:.4f}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Von Neumann 熵**\n{rmt_r['entropy']:.4f}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**ΔS（熵变化）**\n{rmt_r['delta_s']:+.4f}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**SNR（信噪比）**\n{rmt_r['snr']:.2f}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**λ+（MP上限）**\n{rmt_r['lambda_plus']:.4f}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**资产数 / q**\n{rmt_r['n_assets']} / {rmt_r['q']:.3f}"}},
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**特征值（Top5）**\n{eig_str}"}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md",
                         "content": f"**📈 宽基指数观测（科创50 / 创业板指）**　数据日 {broad_date or '—'}\n"
                                    + (broad_text or '（本次未取到宽基指数数据）')}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**🔥 Top5 动量行业 + 个股推荐（20日动量Top3）**"}
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": top5_text}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**❄️ Bottom5 弱势行业**"}
            },
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": bottom5_text}
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**📖 指标解释**\n"
                    "**Gap₁**：最大特征值与次大特征值之比。Gap₁<0.10=简并共振，多个因子共振驱动，市场脆弱。\n"
                    "**Von Neumann 熵**：特征值分布的香农熵。熵高=多因子独立行情（安全），熵低=单因子裹挟（危险）。\n"
                    "**ΔS**：当前熵 vs 5天前熵。ΔS>0.03=维度扩张（危机预警），ΔS<-0.10=维度坍缩（恐慌底）。\n"
                    "**SNR**：最大特征值 / 其余均值。SNR越高=市场被单一因子主导越严重。\n"
                    "**λ+**：Marchenko-Pastur 随机矩阵理论上限，超过 λ+ 的特征值才含真实信息。"
                }
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text",
                     "content": f"RMT Monitor v1.1 + 宽基观测（科创50/创业板指）+ 个股推荐 | 随机矩阵做熔断器，动量选行业，Top3选个股"}
                ]
            }
        ]
    }
    card["elements"] += _bs('rmt', 'card')

    payload = {"msg_type": "interactive", "card": card}
    print(f'\nSending Feishu RMT report...')
    print(f'Status: {rmt_r["status"]} | Position: {rmt_r["position"]*100:.0f}%')
    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        resp = r.json()
        if resp.get('StatusCode') == 0 or resp.get('code') == 0 or resp.get('msg') == 'success':
            print(f'Feishu RMT report sent successfully')
            return True
        else:
            print(f'Feishu API error: {resp}')
            return False
    except Exception as e:
        print(f'Send error: {e}')
        return False


# ================= 看好/看空名单：宽基/风格类过滤 + 动量补选 =================
# 宽基/风格类（非行业）：看好/看空名单中过滤，改按动量继续往下补足，维持名单数量。
WIDE_STYLE_ETFS = {'510300', '510500', '512100', '510050', '159915', '512890', '510880'}
PICK_N = 5


def pick_industries(mom_20, n=PICK_N, top=True):
    """按 20 日动量取 n 个「行业」：先剔除宽基/风格类，按动量继续往下补足；
    行业数不足 n 时用全池补齐（如实呈现，不缩减名单数量）。"""
    s = mom_20.sort_values(ascending=not top)
    picks = [c for c in s.index if c not in WIDE_STYLE_ETFS][:n]
    if len(picks) < n:
        for c in s.index:
            if c not in picks:
                picks.append(c)
            if len(picks) == n:
                break
    return mom_20[picks]


def main():
    print(f'=== RMT Daily Monitor + Stock Picks ===')
    print(f'Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    t0 = time.time()

    df, etf_codes = load_data()
    broad = load_broad_index()
    closes = df[etf_codes].values.astype(float)
    ret_arr = np.zeros_like(closes)
    ret_arr[1:] = closes[1:] / closes[:-1] - 1
    ret_arr = np.nan_to_num(ret_arr, nan=0.0)
    ret_df = pd.DataFrame(ret_arr, columns=etf_codes, index=df['date'])

    print('\n[1/2] Computing RMT...')
    rmt_r = compute_rmt(ret_df)
    print(f'  status={rmt_r["status"]} pos={rmt_r["position"]} gap_1={rmt_r["gap_1"]:.4f} delta_s={rmt_r["delta_s"]:+.4f}')

    # 红/绿灯判定（接在 compute_rmt 之后）
    alert = evaluate_alert(rmt_r)
    print(f'  [ALERT] level={alert["level"]} reasons={alert["reasons"] or "无"}'
          + (f' watch={alert["watch"]}' if alert['watch'] else ''))

    print('\n[2/2] Fetching stock picks...')
    stock_picks = {}
    for code in rmt_r['top5'].keys():
        print(f'  {code}...', end=' ')
        picks = get_top_stocks_for_etf(code, top_n=3)
        if picks:
            stock_picks[code] = picks
            print(', '.join(s['name'] for s in picks))
        else:
            print('no data')

    # 红/绿灯才发邮件；黄灯与无信号不发
    if alert['level'] != 'none':
        send_alert_email(rmt_r, alert, stock_picks, broad)
    else:
        print('\n[ALERT] 未触发红/绿灯，跳过邮件提醒'
              + (f"（黄灯提示：{'；'.join(alert['watch'])}）" if alert['watch'] else ''))

    send_feishu(rmt_r, stock_picks, broad)

    # 保存 JSON
    report = {**rmt_r, 'alert': alert, 'broad_index': broad,
              'stock_picks': {k: v for k, v in stock_picks.items()}}
    report_path = os.path.join(WORK, f'rmt_report_{datetime.now().strftime("%Y%m%d")}.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f'\nReport saved: {report_path}')
    print(f'Total: {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
