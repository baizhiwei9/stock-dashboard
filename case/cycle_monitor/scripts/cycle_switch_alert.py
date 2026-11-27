#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周期切换提醒模块（提前预警 + 事后确认 + 切换后跟踪）

设计要点
--------
1. 通道：邮件（QQ邮箱 SMTP_SSL）。飞书推送可能因 lark-cli 配置丢失而失败，
   邮件是独立通道，保证「周期切换」这种关键事件一定送达。
2. 三封邮件：
   - 预警（切换前）：当前阶段剩余月数 <= 1 时发，告诉你「下个月大概率切到 X」
   - 确认（切换后）：检测到阶段实际变化时发，告诉你「已切到 X，从 YYYY-MM 起」
   - 跟踪（切换后次月）：确认邮件的下一个月再发一次，验证新阶段是否站稳
3. 幂等：data/cycle_alert_state.json 记录已发送事件，重复运行不会重复发。

用户原始要求：
「7月份这种切换周期，那你就在7月前和8月份都得提醒一下发邮件」
→ 7月前 = 预警邮件，8月 = 确认后的跟踪邮件。
"""
import os

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
import sys
import json
import sqlite3
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'cycle.db')
STATE_PATH = os.path.join(PROJECT_DIR, 'data', 'cycle_alert_state.json')

sys.path.insert(0, SCRIPT_DIR)

logger = logging.getLogger(__name__)

# ============ 邮件配置（与 market_bottom_monitor 保持一致）============
EMAIL_CONFIG = {
    'smtp_server': 'smtp.qq.com',
    'smtp_port': 465,                      # SSL
    'sender': _dh_secret('MAIL_USER'),
    'auth_code': _dh_secret('QQMAIL_AUTH_CODE'),       # QQ邮箱授权码
    'receiver': _dh_secret('COZE_EMAIL_TO'),      # 主人邮箱
}

# ============ 周期知识表 ============
INV_AVG = {'主动补库存': 14, '被动补库存': 8, '主动去库存': 12, '被动去库存': 10}
CREDIT_AVG = {'信贷扩张期': 16, '政策收紧期': 10, '信贷收缩期': 12, '政策宽松期': 8}
INV_NEXT = {'主动补库存': '被动补库存', '被动补库存': '主动去库存',
            '主动去库存': '被动去库存', '被动去库存': '主动补库存'}
CREDIT_NEXT = {'信贷扩张期': '政策收紧期', '政策收紧期': '信贷收缩期',
               '信贷收缩期': '政策宽松期', '政策宽松期': '信贷扩张期'}

PHASE_COLOR = {
    '主动补库存': '#3ddc97', '被动补库存': '#ffd166',
    '主动去库存': '#ff8c42', '被动去库存': '#4ea8ff',
    '信贷扩张期': '#3ddc97', '政策宽松期': '#4ea8ff',
    '信贷收缩期': '#ff5c7a', '政策收紧期': '#ffd166',
}

PHASE_MEANING = {
    '主动补库存': '需求↑ 库存↑ —— 企业主动加库存，经济最景气的一段',
    '被动补库存': '需求↓ 库存↑ —— 卖不动了还在产，景气见顶信号',
    '主动去库存': '需求↓ 库存↓ —— 企业主动减产清库，最难受但也是底部前夜',
    '被动去库存': '需求↑ 库存↓ —— 需求先回暖、库存还没跟上，复苏起点',
    '信贷扩张期': '贷款余额↑ M2↑ —— 宽信用+宽货币，最强组合',
    '政策收紧期': '贷款余额↑ M2↓ —— 信用还在扩但货币已收，拐点风险',
    '信贷收缩期': '贷款余额↓ M2↓ —— 双紧，最差组合',
    '政策宽松期': '贷款余额↓ M2↑ —— 货币先放水、信用还没起来，政策底',
}

KIND_CN = {'inventory': '库存周期', 'credit': '信贷周期'}


# ============ 工具函数 ============
def _shift_month(ym: str, n: int) -> str:
    """'2026-11' 偏移 n 个月"""
    y, m = map(int, ym.split('-'))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f'{y:04d}-{m:02d}'


def _month_diff(a: str, b: str) -> int:
    """b - a 的月份差"""
    ya, ma = map(int, a.split('-'))
    yb, mb = map(int, b.split('-'))
    return (yb - ya) * 12 + (mb - ma)


def load_state() -> Dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f'state 文件读取失败，按空处理: {e}')
    return {'inventory': {'warned': [], 'confirmed': [], 'followup': []},
            'credit': {'warned': [], 'confirmed': [], 'followup': []}}


def save_state(state: Dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============ 邮件 ============
def send_email(subject: str, html_body: str, plain_fallback: str = '') -> bool:
    """发送 HTML 邮件，返回是否成功。失败时打印完整错误，不静默。"""
    cfg = EMAIL_CONFIG
    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('coze.DeepSeek·周期监控', 'utf-8')), cfg['sender']))
    msg['To'] = cfg['receiver']

    if plain_fallback:
        msg.attach(MIMEText(plain_fallback, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        with smtplib.SMTP_SSL(cfg['smtp_server'], cfg['smtp_port'], timeout=20) as server:
            server.login(cfg['sender'], cfg['auth_code'])
            server.sendmail(cfg['sender'], [cfg['receiver']], msg.as_string())
        logger.info(f'✅ 邮件已发送: {subject}')
        print(f'✅ 邮件已发送: {subject}')
        return True
    except smtplib.SMTPAuthenticationError as e:
        err = f'❌ 邮件发送失败（SMTP 认证失败，授权码可能过期）: {e}'
    except smtplib.SMTPException as e:
        err = f'❌ 邮件发送失败（SMTP 异常）: {type(e).__name__}: {e}'
    except OSError as e:
        err = f'❌ 邮件发送失败（网络不可达 {cfg["smtp_server"]}:{cfg["smtp_port"]}）: {e}'
    except Exception as e:
        err = f'❌ 邮件发送失败（未知错误）: {type(e).__name__}: {e}'
    logger.error(err)
    print(err)
    return False


# ============ HTML 模板 ============
CSS = """
<style>
body{background:#0e1117;color:#d8dee9;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
     padding:24px;margin:0;line-height:1.7}
.wrap{max-width:680px;margin:0 auto;background:#161b26;border:1px solid #2a3142;border-radius:12px;padding:26px}
h1{font-size:19px;margin:0 0 6px;color:#fff}
.sub{color:#8b95a7;font-size:13px;margin-bottom:18px}
.badge{display:inline-block;padding:5px 13px;border-radius:16px;font-size:14px;font-weight:700;margin:0 4px}
.card{background:#1d2433;border-radius:10px;padding:15px 18px;margin:14px 0}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td{padding:7px 4px;border-bottom:1px solid #2a3142}
td:first-child{color:#8b95a7;width:38%}
td:last-child{color:#d8dee9;font-weight:600}
.note{color:#8b95a7;font-size:12.5px;margin-top:16px;border-top:1px solid #2a3142;padding-top:12px}
.hl{color:#ffd166;font-weight:700}
</style>
"""


def _phase_badge(phase: str) -> str:
    c = PHASE_COLOR.get(phase, '#8b95a7')
    return f'<span class="badge" style="background:{c}22;color:{c};border:1px solid {c}66">{phase}</span>'


def build_warn_email(kind: str, cur_phase: str, next_phase: str, cur_months: int,
                     avg_months: int, est_month: str, evidence: str) -> Tuple[str, str, str]:
    cn = KIND_CN[kind]
    remain = max(0, avg_months - cur_months)
    overdue = max(0, cur_months - avg_months)
    if overdue > 0:
        subject = f'【周期预警】{cn}已超期 {overdue} 个月：{cur_phase} → {next_phase}（随时可能切换）'
    else:
        subject = f'【周期预警】{cn}即将切换：{cur_phase} → {next_phase}（预计 {est_month} 前后）'
    if overdue > 0:
        overdue_rows = (f'<tr><td>超期情况</td><td class="hl" style="color:#ff5c7a">'
                        f'已超出历史均值 {overdue} 个月，随时可能切换</td></tr>'
                        f'<tr><td>预估切换月份</td><td class="hl">已过预估月（{est_month}），尚未切换</td></tr>')
    else:
        overdue_rows = (f'<tr><td>按平均推算剩余</td><td class="hl">{remain} 个月</td></tr>'
                        f'<tr><td>预估切换月份</td><td class="hl">{est_month} 前后</td></tr>')
    html = f"""<html><head><meta charset="utf-8">{CSS}</head><body><div class="wrap">
<h1>⚠️ {cn}即将切换</h1>
<div class="sub">{'已超期预警' if overdue > 0 else '提前预警'} · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>

<div class="card" style="text-align:center;font-size:15px">
  {_phase_badge(cur_phase)} <span style="color:#8b95a7;margin:0 8px">→</span> {_phase_badge(next_phase)}
</div>

<div class="card">
<table>
<tr><td>当前阶段</td><td>{cur_phase}</td></tr>
<tr><td>已持续</td><td>{cur_months} 个月</td></tr>
<tr><td>历史平均时长</td><td>{avg_months} 个月</td></tr>
{overdue_rows}
<tr><td>下一阶段含义</td><td style="font-weight:400;color:#d8dee9">{PHASE_MEANING.get(next_phase, '')}</td></tr>
</table>
</div>

<div class="card">
<div style="color:#8b95a7;font-size:12.5px;margin-bottom:6px">判断依据</div>
<div style="font-size:13.5px">{evidence}</div>
</div>

<div class="note">
这是<b>提前预警</b>，不是切换确认。周期时长按历史平均推算，实际可能提前或延后 1-2 个月。<br>
切换确认后我会再发一封确认邮件，下个月再发一封跟踪邮件。<br>
数据源：国家统计局 / 中国人民银行 / 中国物流与采购联合会
</div>
</div></body></html>"""
    if overdue > 0:
        plain = (f'【周期预警】{cn}已超期\n{cur_phase} → {next_phase}\n'
                 f'当前阶段已持续 {cur_months} 个月，历史平均 {avg_months} 个月，'
                 f'已超出 {overdue} 个月，随时可能切换（原预估 {est_month} 前后）。\n依据：{evidence}')
    else:
        plain = (f'【周期预警】{cn}即将切换\n{cur_phase} → {next_phase}\n'
                 f'当前阶段已持续 {cur_months} 个月，历史平均 {avg_months} 个月，'
                 f'按平均推算剩余 {remain} 个月，预估 {est_month} 前后切换。\n依据：{evidence}')
    return subject, html, plain


def build_confirm_email(kind: str, prev_phase: str, new_phase: str, start_month: str,
                        prev_actual_months: int, new_months: int, next_phase: str,
                        next_avg: int, evidence: str) -> Tuple[str, str, str]:
    cn = KIND_CN[kind]
    subject = f'【周期确认】{cn}已切换：{prev_phase} → {new_phase}（{start_month} 起）'
    html = f"""<html><head><meta charset="utf-8">{CSS}</head><body><div class="wrap">
<h1>✅ {cn}已切换</h1>
<div class="sub">事后确认 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>

<div class="card" style="text-align:center;font-size:15px">
  {_phase_badge(prev_phase)} <span style="color:#8b95a7;margin:0 8px">→</span> {_phase_badge(new_phase)}
</div>

<div class="card">
<table>
<tr><td>新阶段</td><td>{new_phase}</td></tr>
<tr><td>切换生效月</td><td class="hl">{start_month}</td></tr>
<tr><td>新阶段已运行</td><td>{new_months} 个月</td></tr>
<tr><td>上一阶段实际持续</td><td>{prev_actual_months} 个月</td></tr>
<tr><td>新阶段含义</td><td style="font-weight:400;color:#d8dee9">{PHASE_MEANING.get(new_phase, '')}</td></tr>
<tr><td>再下一阶段预估</td><td>{next_phase}（历史平均 {next_avg} 个月后）</td></tr>
</table>
</div>

<div class="card">
<div style="color:#8b95a7;font-size:12.5px;margin-bottom:6px">判断依据</div>
<div style="font-size:13.5px">{evidence}</div>
</div>

<div class="note">
这是<b>切换确认</b>。下个月我会再发一封跟踪邮件，验证新阶段是否站稳。<br>
数据源：国家统计局 / 中国人民银行 / 中国物流与采购联合会
</div>
</div></body></html>"""
    plain = (f'【周期确认】{cn}已切换\n{prev_phase} → {new_phase}\n'
             f'切换生效月 {start_month}，新阶段已运行 {new_months} 个月。\n依据：{evidence}')
    return subject, html, plain


def build_followup_email(kind: str, new_phase: str, start_month: str, new_months: int,
                         next_phase: str, next_avg: int, evidence: str) -> Tuple[str, str, str]:
    cn = KIND_CN[kind]
    subject = f'【周期跟踪】{cn}：{new_phase} 已运行 {new_months} 个月'
    html = f"""<html><head><meta charset="utf-8">{CSS}</head><body><div class="wrap">
<h1>📌 {cn}切换后跟踪</h1>
<div class="sub">切换后次月跟踪 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>

<div class="card" style="text-align:center">
  {_phase_badge(new_phase)}
</div>

<div class="card">
<table>
<tr><td>当前阶段</td><td>{new_phase}</td></tr>
<tr><td>起始月</td><td>{start_month}</td></tr>
<tr><td>已运行</td><td class="hl">{new_months} 个月</td></tr>
<tr><td>阶段含义</td><td style="font-weight:400;color:#d8dee9">{PHASE_MEANING.get(new_phase, '')}</td></tr>
<tr><td>下一阶段预估</td><td>{next_phase}（历史平均 {next_avg} 个月后）</td></tr>
</table>
</div>

<div class="card">
<div style="color:#8b95a7;font-size:12.5px;margin-bottom:6px">当前数据</div>
<div style="font-size:13.5px">{evidence}</div>
</div>

<div class="note">
上个月已确认切换，本月跟踪新阶段是否站稳。若数据反复（新阶段只持续 1 个月就切回），
说明这轮信号不稳，我会在下次确认邮件里说明。<br>
数据源：国家统计局 / 中国人民银行 / 中国物流与采购联合会
</div>
</div></body></html>"""
    plain = (f'【周期跟踪】{cn}：{new_phase} 已运行 {new_months} 个月（起始 {start_month}）。\n依据：{evidence}')
    return subject, html, plain


# ============ 证据采集 ============
def _latest(conn, indicator_id: str, n: int = 2) -> List[Tuple[str, float]]:
    rows = conn.execute(
        "SELECT date, value FROM economic_indicators WHERE indicator_id=? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", (indicator_id, n)
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def build_evidence(kind: str) -> str:
    """组装当前数据依据（纯数据描述，不含推断）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        if kind == 'inventory':
            i006 = _latest(conn, 'I006', 2)   # PMI 新订单
            i007 = _latest(conn, 'I007', 2)   # PMI 产成品库存
            parts = []
            if len(i006) == 2:
                d, v = i006[0]
                parts.append(f'PMI 新订单（需求）{d} 为 <b>{v}</b>（前值 {i006[1][1]}，'
                             f'{"↑" if v > i006[1][1] else "↓" if v < i006[1][1] else "→"}）')
            if len(i007) == 2:
                d, v = i007[0]
                parts.append(f'PMI 产成品库存（存货）{d} 为 <b>{v}</b>（前值 {i007[1][1]}，'
                             f'{"↑" if v > i007[1][1] else "↓" if v < i007[1][1] else "→"}）')
            i009 = _latest(conn, 'I009', 1)
            i010 = _latest(conn, 'I010', 1)
            if i009 and i010:
                parts.append(f'统计局口径（已滞后）：产成品存货同比 {i009[0][1]}%（{i009[0][0]}）、'
                             f'营业收入同比 {i010[0][1]}%（{i010[0][0]}）')
            return '<br>'.join(parts) if parts else '数据不足'
        else:
            c003 = _latest(conn, 'C003', 2)   # 贷款余额同比
            c002 = _latest(conn, 'C002', 2)   # M2
            parts = []
            if len(c003) == 2:
                d, v = c003[0]
                parts.append(f'人民币贷款余额同比 {d} 为 <b>{v}%</b>（前值 {c003[1][1]}%）')
            if len(c002) == 2:
                d, v = c002[0]
                parts.append(f'M2 同比 {d} 为 <b>{v}%</b>（前值 {c002[1][1]}%）')
            return '<br>'.join(parts) if parts else '数据不足'
    finally:
        conn.close()


# ============ 主逻辑 ============
def run_alerts(dry_run: bool = False, force: bool = False) -> List[str]:
    """
    检查并发送周期切换提醒。
    返回实际发送（或 dry_run 下将要发送）的邮件主题列表。
    """
    try:
        from cycle_phase_analyzer import analyze_cycles
    except Exception as e:
        logger.error(f'❌ 无法加载 cycle_phase_analyzer，周期提醒跳过: {e}')
        return []

    credit_trans, inv_trans, _, _ = analyze_cycles(DB_PATH)
    state = load_state()
    now_month = datetime.now().strftime('%Y-%m')
    sent = []

    for kind, trans in (('inventory', inv_trans), ('credit', credit_trans)):
        if not trans or len(trans) < 2:
            logger.warning(f'{KIND_CN[kind]} 阶段序列不足，跳过提醒')
            continue
        avg_map = INV_AVG if kind == 'inventory' else CREDIT_AVG
        next_map = INV_NEXT if kind == 'inventory' else CREDIT_NEXT

        cur_start, cur_phase, cur_months = trans[-1]
        prev_start, prev_phase, prev_months = trans[-2]
        st = state.setdefault(kind, {'warned': [], 'confirmed': [], 'followup': []})

        # ---------- ① 事后确认 ----------
        ckey = f'{prev_phase}->{cur_phase}@{cur_start}'
        already = any(c.get('key') == ckey for c in st['confirmed'])
        # 只对「当前阶段」确认一次；若当前阶段起点距今超过 3 个月，视为老阶段不补发
        is_recent = _month_diff(cur_start, now_month) <= 3
        if not already and is_recent and prev_phase != cur_phase:
            avg_prev = avg_map.get(prev_phase, 12)
            next_phase = next_map.get(cur_phase, '未知')
            subject, html, plain = build_confirm_email(
                kind, prev_phase, cur_phase, cur_start,
                prev_actual_months=prev_months, new_months=cur_months,
                next_phase=next_phase, next_avg=avg_map.get(next_phase, 12),
                evidence=build_evidence(kind))
            if dry_run:
                sent.append(f'[dry] {subject}')
            elif send_email(subject, html, plain):
                st['confirmed'].append({
                    'key': ckey, 'from': prev_phase, 'to': cur_phase,
                    'start': cur_start, 'sent_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                })
                st['followup'].append({
                    'phase': cur_phase, 'start': cur_start,
                    'due': _shift_month(cur_start, 1), 'sent_at': None,
                    'created_at': now_month,   # 当轮不发，留到下一轮
                })
                sent.append(subject)
                save_state(state)

        # ---------- ② 切换后跟踪 ----------
        for fu in st['followup']:
            if fu.get('sent_at'):
                continue
            if fu.get('created_at') == now_month:
                continue
            if fu.get('due') and fu['due'] <= now_month and fu['phase'] == cur_phase:
                next_phase = next_map.get(cur_phase, '未知')
                subject, html, plain = build_followup_email(
                    kind, cur_phase, cur_start, cur_months,
                    next_phase=next_phase, next_avg=avg_map.get(next_phase, 12),
                    evidence=build_evidence(kind))
                if dry_run:
                    sent.append(f'[dry] {subject}')
                elif send_email(subject, html, plain):
                    fu['sent_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                    sent.append(subject)
                    save_state(state)

        # ---------- ③ 提前预警 ----------
        avg_cur = avg_map.get(cur_phase, 12)
        remain = avg_cur - cur_months
        next_phase = next_map.get(cur_phase)
        est = _shift_month(cur_start, avg_cur)
        # 预警窗口：预估切换月的前 1 个月内（含当月）。用「距预估月还有几个月」判断，
        # 这样每月 2 号/16 号两次运行都能落在窗口里，靠幂等保证只发一封。
        months_to_est = _month_diff(now_month, est)
        # 预警窗口覆盖「当月 + 下月」两种情形：
        #  a) 正常阶段：预估切换月落在本月或下月 → months_to_est 为 0 或 1
        #  b) 已超期阶段：est 已是过去月份（months_to_est<0），原逻辑永远进不来 → 超期阶段完全静默，
        #     这正是「库存已 11 个月、均值 8 个月、超期 3 个月却一封预警都没有」的原因。
        #     改为超期后按月预警一次，key 带月份做月内幂等。
        overdue = cur_months > avg_cur
        if next_phase and (0 <= months_to_est <= 1 or overdue):
            wkey = f'{cur_phase}->{next_phase}@{est}'
            if overdue:
                wkey += f'#overdue@{now_month}'
            already_warned = any(w.get('key') == wkey for w in st['warned'])
            if not already_warned:
                subject, html, plain = build_warn_email(
                    kind, cur_phase, next_phase, cur_months, avg_cur, est,
                    evidence=build_evidence(kind))
                if dry_run:
                    sent.append(f'[dry] {subject}')
                elif send_email(subject, html, plain):
                    st['warned'].append({
                        'key': wkey, 'from': cur_phase, 'to': next_phase,
                        'est': est, 'sent_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    })
                    sent.append(subject)
                    save_state(state)

    if not sent:
        logger.info('周期提醒：无新增事件（全部已发过或未到窗口）')
    return sent


def main():
    import argparse
    p = argparse.ArgumentParser(description='周期切换提醒（提前预警 + 事后确认 + 跟踪）')
    p.add_argument('--dry-run', action='store_true', help='只检查不发邮件')
    p.add_argument('--force', action='store_true', help='忽略状态强制重发')
    p.add_argument('--reset-state', action='store_true', help='清空提醒状态（测试用）')
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    if args.reset_state:
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
            print(f'已清空状态文件: {STATE_PATH}')
        else:
            print('状态文件不存在')

    out = run_alerts(dry_run=args.dry_run, force=args.force)
    if out:
        print('\n'.join(out))
    else:
        print('无提醒需要发送')


if __name__ == '__main__':
    main()
