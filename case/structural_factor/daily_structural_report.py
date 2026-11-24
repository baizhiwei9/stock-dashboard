#!/usr/bin/env python3
"""
结构因子每日报告 - 自动拉取数据、生成图表、发送飞书
独立运行，不依赖钉钉dws。
飞书发送：通过应用凭据上传图片，发送含图的交互卡片。
"""

import sys
import os
import json
import base64
import smtplib

# 确保公共依赖库路径（持久化共享目录 pylibs，兼容旧 py_deps / pip-global）
# 必须在 import akshare / matplotlib 之前执行，保证无 PYTHONPATH 时也能正常取数与绘图
_SHARED_DEPS = [
    '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs',
    '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps',
    '/tmp/.pip-global/lib/python3.12/site-packages',
]
for _p in reversed(_SHARED_DEPS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import requests
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import fetch_csi_all_index
from structural_factors import IntervalDetector, analyze_market
from visualization import plot_taiji, plot_indicators
from feishu_config import APP_ID, APP_SECRET, TOKEN_URL, UPLOAD_URL, WEBHOOK_URL
import mail_config

WORK_DIR = os.path.dirname(os.path.abspath(__file__))


# ==================== 飞书 API ====================

def get_tenant_token():
    """获取飞书 tenant_access_token"""
    resp = requests.post(TOKEN_URL, json={
        'app_id': APP_ID,
        'app_secret': APP_SECRET
    }, timeout=10)
    data = resp.json()
    if data.get('code') != 0:
        raise Exception(f"获取token失败: {data}")
    return data['tenant_access_token']


def upload_image(token, image_path):
    """上传图片到飞书，返回 image_key"""
    with open(image_path, 'rb') as f:
        resp = requests.post(UPLOAD_URL,
            headers={'Authorization': f'Bearer {token}'},
            data={'image_type': 'message'},
            files={'image': f},
            timeout=30
        )
    data = resp.json()
    if data.get('code') != 0:
        raise Exception(f"上传图片失败 {image_path}: {data}")
    return data['data']['image_key']


# ==================== 模型运行 ====================

def run_model():
    """运行结构因子模型，返回分析结果和图表路径"""
    print("[1/3] 拉取中证全A(000985)行情数据...")
    data = fetch_csi_all_index(days=120, source='tx')
    prices = data['prices']
    volumes = data['volumes']
    dates = data['dates']

    print("[2/3] 计算结构因子...")
    detector = IntervalDetector()
    result = detector.analyze(prices, volumes)
    market = analyze_market(prices, volumes)

    current_date = dates.iloc[-1].strftime('%Y-%m-%d')

    print("[3/3] 生成图表...")
    taiji_img = plot_taiji(result['absolute_value']['values'], result['taiji'],
                           current_date=current_date)
    taiji_path = os.path.join(WORK_DIR, 'taiji_chart.png')
    taiji_img.save(taiji_path)

    indicators_img = plot_indicators(
        prices,
        result['relative_value']['values'],
        result['absolute_value']['values'],
        result['relative_value']['peaks'],
        result['absolute_value']['crosses'],
        dates=dates
    )
    indicators_path = os.path.join(WORK_DIR, 'indicators_chart.png')
    indicators_img.save(indicators_path)

    html_path = generate_html_report(market, result, taiji_path, indicators_path, current_date)

    return market, result, current_date, html_path, taiji_path, indicators_path


# ==================== HTML 报告 ====================

def generate_html_report(market, result, taiji_path, indicators_path, current_date):
    """生成含内嵌图片的HTML报告"""
    with open(taiji_path, 'rb') as f:
        taiji_b64 = base64.b64encode(f.read()).decode()
    with open(indicators_path, 'rb') as f:
        indicators_b64 = base64.b64encode(f.read()).decode()

    interval = result['interval']
    odds = market['odds_signal']
    win = market['win_rate_signal']
    taiji = result['taiji']
    rv_peaks = result['relative_value']['peaks']
    av_ext = result['absolute_value']['extremes']

    odds_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'exit': '退出'}.get(odds['signal'], odds['signal'])
    win_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'no_signal': '未激活'}.get(win['signal'], win['signal'])
    dominant_cn = {'bull': '多头主导', 'bear': '空头主导', 'balanced': '平衡'}.get(taiji['dominant'], taiji['dominant'])

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>结构因子分析报告 - {current_date}</title>
<style>
body {{ font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
.header {{ background: linear-gradient(135deg, #1a1a2e, #16213e); color: white; padding: 20px 30px; border-radius: 10px; margin-bottom: 20px; }}
.header h1 {{ margin: 0; font-size: 22px; }}
.header .subtitle {{ color: #aaa; font-size: 13px; margin-top: 5px; }}
.section {{ background: white; border-radius: 10px; padding: 20px 25px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.section h2 {{ font-size: 16px; color: #333; border-left: 4px solid #e94560; padding-left: 10px; margin: 0 0 15px 0; }}
.metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.metric {{ background: #f8f9fa; padding: 10px 15px; border-radius: 6px; }}
.metric .label {{ color: #666; font-size: 12px; }}
.metric .value {{ font-size: 16px; font-weight: bold; color: #333; margin-top: 3px; }}
.signal-buy {{ color: #e74c3c; }}
.signal-wait {{ color: #f39c12; }}
.signal-na {{ color: #95a5a6; }}
.charts img {{ width: 100%; border-radius: 8px; margin-bottom: 10px; }}
.summary {{ background: #fff3cd; border: 1px solid #ffeaa7; padding: 15px 20px; border-radius: 8px; font-size: 14px; line-height: 1.6; }}
.footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 20px; }}
</style>
</head>
<body>
<div class="header">
  <h1>结构因子分析报告</h1>
  <div class="subtitle">数据来源: 中证全A(000985)，最近120个交易日 | 数据日期: {current_date} | 生成时间: {now_str} | 来自 DoMate</div>
</div>

<div class="section">
  <h2>一、区间判断</h2>
  <div class="metric-grid">
    <div class="metric"><div class="label">当前区间</div><div class="value">{interval['name']}</div></div>
    <div class="metric"><div class="label">置信度</div><div class="value">{interval['confidence']*100:.1f}%</div></div>
  </div>
  <p style="color:#666; font-size:13px; margin-top:10px;">{interval['description']}</p>
  <p style="font-size:13px;"><b>策略建议:</b> {interval['strategy']}</p>
</div>

<div class="section">
  <h2>二、交易信号</h2>
  <div class="metric-grid">
    <div class="metric">
      <div class="label">赔率模型 (左侧交易)</div>
      <div class="value {'signal-buy' if odds['signal']=='buy' else 'signal-wait' if odds['signal']=='prepare_buy' else 'signal-na'}">{odds_signal_cn} ({odds['confidence']*100:.0f}%)</div>
      <div style="font-size:12px; color:#666; margin-top:5px;">{odds['reason']}</div>
    </div>
    <div class="metric">
      <div class="label">胜率模型 (右侧交易)</div>
      <div class="value {'signal-buy' if win['signal']=='buy' else 'signal-wait' if win['signal']=='prepare_buy' else 'signal-na'}">{win_signal_cn} ({win['confidence']*100:.0f}%)</div>
      <div style="font-size:12px; color:#666; margin-top:5px;">{win['reason']}</div>
    </div>
  </div>
</div>

<div class="section charts">
  <h2>三、太极图 - 多空能量</h2>
  <img src="data:image/png;base64,{taiji_b64}" alt="太极图">
  <div class="metric-grid">
    <div class="metric"><div class="label">白鱼面积 (多头能量)</div><div class="value">{taiji['white_fish_area']:.2f}</div></div>
    <div class="metric"><div class="label">黑鱼面积 (空头能量)</div><div class="value">{taiji['black_fish_area']:.2f}</div></div>
    <div class="metric"><div class="label">力量平衡比</div><div class="value">{taiji['balance_ratio']*100:.1f}%</div></div>
    <div class="metric"><div class="label">主导方</div><div class="value">{dominant_cn}</div></div>
  </div>
</div>

<div class="section charts">
  <h2>四、指标序列图</h2>
  <img src="data:image/png;base64,{indicators_b64}" alt="指标序列图">
</div>

<div class="section">
  <h2>五、相对值分析</h2>
  <div class="metric-grid">
    <div class="metric"><div class="label">第一峰高度</div><div class="value">{rv_peaks.get('first_peak_val', 0):.4f}</div></div>
    <div class="metric"><div class="label">第二峰高度</div><div class="value">{rv_peaks.get('second_peak_val', 0):.4f}</div></div>
    <div class="metric"><div class="label">双顶结构</div><div class="value">{'是' if rv_peaks['is_double_top'] else '否'}</div></div>
    <div class="metric"><div class="label">资金池状态</div><div class="value">{result['relative_value']['money_pool']['status']}</div></div>
  </div>
</div>

<div class="section">
  <h2>六、绝对值分析</h2>
  <div class="metric-grid">
    <div class="metric"><div class="label">是否创新低</div><div class="value">{'是' if av_ext['is_new_low'] else '否'}</div></div>
    <div class="metric"><div class="label">不再创新低确认</div><div class="value">{'是' if av_ext['confirmation'] else '否'}</div></div>
    <div class="metric"><div class="label">近期最低值</div><div class="value">{av_ext['recent_low']:.4f}</div></div>
    <div class="metric"><div class="label">零轴穿越次数</div><div class="value">{len(result['absolute_value']['crosses'])}</div></div>
  </div>
</div>

<div class="summary">
  <b>综合建议:</b> {market['summary']}
</div>

<div class="footer">
  报告由结构因子模型自动生成 | 参考: 短期基线交易基本原理和框架流程(四)
</div>
</body>
</html>"""

    html_path = os.path.join(WORK_DIR, 'structural_factor_report.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML报告已保存: {html_path}")
    return html_path


# ==================== 飞书发送 ====================

def send_feishu(market, result, current_date, taiji_path, indicators_path, turn_info=None, alert=None, zone=None):
    """上传图片并发送飞书交互卡片（含图）"""
    interval = result['interval']
    odds = market['odds_signal']
    win = market['win_rate_signal']
    taiji = result['taiji']
    rv_peaks = result['relative_value']['peaks']
    av_ext = result['absolute_value']['extremes']

    # 检测到风险：绝对因子与相对因子同时转向 → 风险提示置顶 + 标题标记
    has_risk = bool(turn_info and turn_info.get('triggered'))
    has_opp = bool(alert and alert.get('opportunity'))
    zone_risk = bool(zone and zone.get('risk'))
    zone_opp = bool(zone and zone.get('opp'))
    _dry = os.environ.get('STRUCTURAL_CARD_DRY') == '1'

    odds_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'exit': '退出'}.get(odds['signal'], odds['signal'])
    win_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'no_signal': '未激活'}.get(win['signal'], win['signal'])
    dominant_cn = {'bull': '多头', 'bear': '空头', 'balanced': '平衡'}.get(taiji['dominant'], taiji['dominant'])

    if _dry:
        taiji_key, indicators_key = 'dry_taiji', 'dry_indicators'
        print("[飞书][dry-run] 跳过 token 获取与图片上传")
    else:
        # 1. 获取 token
        print("[飞书] 获取token...")
        token = get_tenant_token()

        # 2. 上传图片
        print("[飞书] 上传太极图...")
        taiji_key = upload_image(token, taiji_path)
        print(f"  image_key: {taiji_key}")

        print("[飞书] 上传指标图...")
        indicators_key = upload_image(token, indicators_path)
        print(f"  image_key: {indicators_key}")

    # 3. 构建卡片
    def make_row(label, value):
        return {"tag": "div", "text": {"tag": "lark_md", "content": f"{label}: **{value}**"}}

    elements = []
    # 将军四区间判定：固定置于消息最前，结构因子图与结论在后
    if zone and zone.get('elements'):
        elements += list(zone['elements'])
    # 五大宽基风险监控（2026-10-22 新增：统一给出宽基明确多空结论）
    broad5_text, broad5_bear = '', 0
    try:
        import sys as _sys
        _SHARED_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _SHARED_DIR not in _sys.path:
            _sys.path.insert(0, _SHARED_DIR)
        import broad5_risk
        broad5_text = broad5_risk.summary_text()
        broad5_bear = broad5_risk.counts().get('看空', 0)
    except Exception as _e:
        broad5_text = f"**【五大宽基风险监控】**\n数据获取失败，本次未纳入：{str(_e)[:80]}"
    if broad5_bear >= 3:
        broad5_text = (f"**⚠️ 风险提示：五大宽基中 {broad5_bear} 个已转看空，"
                       f"本报告持仓建议需据此从严执行**\n") + broad5_text

    if has_risk:
        _turn_cn = {'up_to_down': '由升转降', 'down_to_up': '由降转升'}
        _rv_txt = _turn_cn.get(turn_info.get('rv_turn'), turn_info.get('rv_turn', ''))
        _av_txt = _turn_cn.get(turn_info.get('av_turn'), turn_info.get('av_turn', ''))
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                         "content": f"**⚠️ 检测到风险：绝对因子与相对因子同日转向（相对值 {_rv_txt}，绝对值 {_av_txt}）**"}})
        elements.append({"tag": "hr"})
    elements += [
        # 区间判断
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**结构因子区间（筹码/资金口径）: {interval['name']}**  |  置信度: {interval['confidence']*100:.1f}%"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"策略: {interval['strategy']}"}},
        {"tag": "hr"},
        # 交易信号
        {"tag": "div", "text": {"tag": "lark_md", "content": "**交易信号**"}},
        make_row("赔率模型(左侧)", f"{odds_signal_cn} ({odds['confidence']*100:.0f}%)"),
        {"tag": "div", "text": {"tag": "lark_md", "content": f"  {odds['reason']}"}},
        make_row("胜率模型(右侧)", f"{win_signal_cn} ({win['confidence']*100:.0f}%)"),
        {"tag": "div", "text": {"tag": "lark_md", "content": f"  {win['reason']}"}},
        {"tag": "hr"},
        # 太极图图片
        {"tag": "div", "text": {"tag": "lark_md", "content": "**太极图 - 多空能量**"}},
        {"tag": "img", "img_key": taiji_key, "alt": {"tag": "plain_text", "content": "太极图"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"白鱼(多头): **{taiji['white_fish_area']:.2f}**　黑鱼(空头): **{taiji['black_fish_area']:.2f}**　力量平衡: **{taiji['balance_ratio']*100:.1f}% ({dominant_cn}主导)**"}},
        {"tag": "hr"},
        # 指标图图片
        {"tag": "div", "text": {"tag": "lark_md", "content": "**指标序列图**"}},
        {"tag": "img", "img_key": indicators_key, "alt": {"tag": "plain_text", "content": "指标序列图"}},
        {"tag": "hr"},
        # 相对值
        {"tag": "div", "text": {"tag": "lark_md", "content": "**相对值(筹码结构)**"}},
        make_row("双顶结构", "是" if rv_peaks['is_double_top'] else "否"),
        make_row("资金池状态", result['relative_value']['money_pool']['status']),
        {"tag": "hr"},
        # 绝对值
        {"tag": "div", "text": {"tag": "lark_md", "content": "**绝对值(资金流向)**"}},
        make_row("不再创新低", "是" if av_ext['confirmation'] else "否"),
        make_row("零轴穿越次数", str(len(result['absolute_value']['crosses']))),
        {"tag": "hr"},
        # 综合
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**综合建议**\n{market['summary']}"}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": broad5_text}},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据日期: {current_date}"}]}
    ]

    _flags = ('注意风险 | ' if (has_risk or zone_risk) else '') + ('注意机会 | ' if (has_opp or zone_opp) else '')
    _zone_title = (zone or {}).get('title', '结构因子分析报告')
    _zone_short = (zone or {}).get('short', '')
    _head_title = f"{_zone_title} | {_zone_short} | {_flags}来自 DoMate" if _zone_short else f"{_zone_title} | {_flags}来自 DoMate"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": _head_title},
            "template": "red" if (has_risk or zone_risk) else ("orange" if zone_opp else "blue")
        },
        "elements": elements
    }

    payload = {"msg_type": "interactive", "card": card}

    if _dry:
        dry_path = os.path.join(WORK_DIR, 'card_dry.json')
        with open(dry_path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print(f"[飞书][dry-run] 卡片已落盘 {dry_path}，未发送")
        return True

    print("[飞书] 发送卡片...")
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    print(f"[飞书] 响应: {resp.status_code} {resp.text}")
    return resp.status_code == 200


# ==================== 同时转向检测 + 邮件提醒 ====================

def detect_simultaneous_turn(rv_values, av_values):
    """
    检测绝对因子与相对因子是否同时转向（对比前一交易日）。
    转向定义：与前一交易日相比方向发生转换，即由升转降或由降转升。
    同时转向：两个因子在同一日同时发生方向转换。
    返回 dict: {'triggered': bool, 'rv_turn': str, 'av_turn': str, ...}
    """
    def check_turn(values):
        if len(values) < 3:
            return None
        d_prev = values[-2] - values[-3]   # 前一日变化
        d_curr = values[-1] - values[-2]   # 当日变化
        if abs(d_prev) < 1e-12 or abs(d_curr) < 1e-12:
            return None
        if d_prev > 0 and d_curr < 0:
            return 'up_to_down'
        if d_prev < 0 and d_curr > 0:
            return 'down_to_up'
        return 'same'

    rv_turn = check_turn(rv_values)
    av_turn = check_turn(av_values)
    triggered = rv_turn in ('up_to_down', 'down_to_up') and av_turn in ('up_to_down', 'down_to_up')

    return {
        'triggered': triggered,
        'rv_turn': rv_turn,
        'av_turn': av_turn,
        'rv_prev': float(rv_values[-2]) if len(rv_values) >= 2 else None,
        'rv_curr': float(rv_values[-1]) if len(rv_values) >= 1 else None,
        'av_prev': float(av_values[-2]) if len(av_values) >= 2 else None,
        'av_curr': float(av_values[-1]) if len(av_values) >= 1 else None,
    }


def _send_email(subject, markdown_body, body_html):
    """
    统一邮件发送通道。
    主通道：钉钉企业邮箱（dws，用户已授权）；回退：163 SMTP。
    邮件发送失败不抛异常（不中断主流程），打印错误后继续。
    """
    ok = mail_config.send_via_dws(subject, markdown_body)
    if ok:
        print(f"[邮件] 已通过钉钉邮箱发送至 {mail_config.EMAIL_TO}")
        return True

    auth_code = mail_config.get_auth_code()
    if not auth_code:
        print("[邮件] 回退通道未配置授权码（环境变量 EMAIL_AUTH_CODE 或既有配置缺失），跳过 SMTP 发送")
        return False

    msg = MIMEMultipart()
    msg['From'] = mail_config.EMAIL_USER
    msg['To'] = mail_config.EMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))

    try:
        print("[邮件] 回退通道: 通过 163 SMTP 发送...")
        server = smtplib.SMTP(mail_config.EMAIL_HOST, mail_config.EMAIL_PORT, timeout=15)
        server.starttls()
        server.login(mail_config.EMAIL_USER, auth_code)
        server.sendmail(mail_config.EMAIL_USER, mail_config.EMAIL_TO, msg.as_string())
        server.quit()
        print(f"[邮件] 已发送至 {mail_config.EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[邮件] SMTP 发送失败: {e}")
        return False


def send_turn_email(turn_info, market, result, current_date):
    """
    发送「同时转向」邮件提醒。
    主通道：钉钉企业邮箱（dws，用户已授权）；回退：163 SMTP。
    邮件发送失败不抛异常（不中断主流程），打印错误后继续。
    """
    turn_cn = {'up_to_down': '由升转降', 'down_to_up': '由降转升', 'same': '方向未变', None: '数据不足'}
    rv_arrow = '↓' if turn_info['rv_turn'] == 'up_to_down' else ('↑' if turn_info['rv_turn'] == 'down_to_up' else '→')
    av_arrow = '↓' if turn_info['av_turn'] == 'up_to_down' else ('↑' if turn_info['av_turn'] == 'down_to_up' else '→')

    interval = result['interval']
    odds = market['odds_signal']
    win = market['win_rate_signal']
    odds_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'exit': '退出'}.get(odds['signal'], odds['signal'])
    win_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'no_signal': '未激活'}.get(win['signal'], win['signal'])

    subject = f"[结构因子提醒] 绝对因子与相对因子同时转向 ({current_date}) 来自 DoMate"

    markdown_body = f"""## ⚠️ 绝对因子与相对因子同时转向

**结构因子模型提醒** | 数据日期: {current_date}

与前一交易日相比，**相对值因子**与**绝对值因子**同日发生方向转换，请关注市场结构变化。

| 因子 | 前一日值 | 当日值 | 方向变化 |
|------|---------|--------|---------|
| 相对值因子 (筹码结构) | {turn_info['rv_prev']:.4f} | {turn_info['rv_curr']:.4f} | {rv_arrow} {turn_cn.get(turn_info['rv_turn'], '')} |
| 绝对值因子 (资金流向) | {turn_info['av_prev']:.4f} | {turn_info['av_curr']:.4f} | {av_arrow} {turn_cn.get(turn_info['av_turn'], '')} |

**当前区间：** {interval['name']} (置信度 {interval['confidence']*100:.1f}%)
**策略建议：** {interval['strategy']}
**赔率模型(左侧)：** {odds_signal_cn} ({odds['confidence']*100:.0f}%)
**胜率模型(右侧)：** {win_signal_cn} ({win['confidence']*100:.0f}%)

综合建议：{market['summary']}

---
*仅供参考，不构成投资建议 | 来自 DoMate*
"""

    # 转 HTML 正文（供 _send_email 的回退通道使用）
    rows = "".join(
        f"<tr><td style='padding:8px;border:1px solid #ddd;'>{r[0]}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{r[1]}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{r[2]}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{r[3]}</td></tr>"
        for r in [
            ('相对值因子 (筹码结构)', f"{turn_info['rv_prev']:.4f}", f"{turn_info['rv_curr']:.4f}", f"{rv_arrow} {turn_cn.get(turn_info['rv_turn'], '')}"),
            ('绝对值因子 (资金流向)', f"{turn_info['av_prev']:.4f}", f"{turn_info['av_curr']:.4f}", f"{av_arrow} {turn_cn.get(turn_info['av_turn'], '')}"),
        ]
    )

    body_html = f"""<html><body style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.6;">
<div style="max-width:640px;margin:0 auto;border:1px solid #eee;border-radius:8px;overflow:hidden;">
<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:16px 24px;">
<h2 style="margin:0;font-size:18px;">⚠️ 绝对因子与相对因子同时转向</h2>
<p style="margin:4px 0 0;color:#aaa;font-size:12px;">结构因子模型提醒 | 数据日期: {current_date}</p>
</div>
<div style="padding:20px 24px;">
<p><strong>检测结果：</strong>与前一交易日相比，相对值因子与绝对值因子<strong>同日发生方向转换</strong>，请关注市场结构变化。</p>
<table style="border-collapse:collapse;width:100%;font-size:13px;">
<tr style="background:#f5f5f5;">
<th style="padding:8px;border:1px solid #ddd;text-align:left;">因子</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">前一日值</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">当日值</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">方向变化</th>
</tr>
{rows}
</table>
<div style="margin-top:16px;padding:12px;background:#f8f9fa;border-radius:6px;font-size:13px;">
<p style="margin:4px 0;"><strong>当前区间：</strong>{interval['name']} (置信度 {interval['confidence']*100:.1f}%)</p>
<p style="margin:4px 0;"><strong>策略建议：</strong>{interval['strategy']}</p>
<p style="margin:4px 0;"><strong>赔率模型(左侧)：</strong>{odds_signal_cn} ({odds['confidence']*100:.0f}%)</p>
<p style="margin:4px 0;"><strong>胜率模型(右侧)：</strong>{win_signal_cn} ({win['confidence']*100:.0f}%)</p>
</div>
<p style="margin-top:12px;color:#999;font-size:12px;">综合建议：{market['summary']}</p>
</div>
<div style="background:#fafafa;border-top:1px solid #eee;padding:10px 24px;color:#999;font-size:12px;">
仅供参考，不构成投资建议 | 来自 DoMate
</div>
</div></body></html>"""

    return _send_email(subject, markdown_body, body_html)


# ==================== 资金流零轴穿越 + 筹码结构 风险/机会提醒 ====================

def detect_risk_opportunity(rv_values, av_values):
    """
    检测「资金流零轴穿越 + 筹码结构」组合信号（针对最新交易日）。

    口径说明（与模型定义保持一致）：
      - 资金流 = 绝对值因子（av）：由零轴上方转至零轴及以下为「下穿」，
        由零轴下方转至零轴及以上为「上穿」。
      - 筹码斜率 = 相对值因子（rv）：模型对相对值的定义为「场内筹码结构所反馈的
        结构因子值变化的斜率」，故以相对值本身相对零轴的方向判断筹码结构强弱
        （rv < 0 视为筹码结构转弱，rv > 0 视为筹码结构转强）。

    风险：资金流由零轴上方下穿至零轴下方，且筹码斜率 < 0。
    机会：资金流由零轴下方上穿至零轴上方，且筹码斜率 > 0。

    Returns:
        dict: {'risk', 'opportunity', 'down_cross', 'up_cross',
               'av_prev', 'av_curr', 'rv_prev', 'rv_curr', 'rv_slope'}
    """
    empty = {
        'risk': False, 'opportunity': False, 'down_cross': False, 'up_cross': False,
        'av_prev': None, 'av_curr': None, 'rv_prev': None, 'rv_curr': None, 'rv_slope': None,
    }
    if rv_values is None or av_values is None or len(rv_values) < 2 or len(av_values) < 2:
        return empty

    av_prev, av_curr = float(av_values[-2]), float(av_values[-1])
    rv_prev, rv_curr = float(rv_values[-2]), float(rv_values[-1])

    down_cross = av_prev > 0 and av_curr <= 0   # 零轴上方 → 零轴下方
    up_cross = av_prev < 0 and av_curr >= 0     # 零轴下方 → 零轴上方

    return {
        'risk': down_cross and rv_curr < 0,          # 筹码斜率 < 0
        'opportunity': up_cross and rv_curr > 0,     # 筹码斜率 > 0
        'down_cross': down_cross,
        'up_cross': up_cross,
        'av_prev': av_prev,
        'av_curr': av_curr,
        'rv_prev': rv_prev,
        'rv_curr': rv_curr,
        'rv_slope': rv_curr - rv_prev,
    }


def send_risk_opportunity_email(alert, market, result, current_date):
    """
    发送「资金流零轴穿越 + 筹码结构」风险/机会邮件提醒。
    主通道：钉钉企业邮箱（dws）；回退：163 SMTP。发送失败不中断主流程。
    """
    is_risk = alert['risk']
    kind_cn = '风险' if is_risk else '机会'
    icon = '⚠️' if is_risk else '🌟'
    cross_cn = '下穿' if is_risk else '上穿'
    chip_sign = '<0' if is_risk else '>0'
    chip_word = '转弱' if is_risk else '转强'
    flow_move = '由零轴上方下穿至零轴下方' if is_risk else '由零轴下方上穿至零轴上方'

    interval = result['interval']
    odds = market['odds_signal']
    win = market['win_rate_signal']
    odds_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'exit': '退出'}.get(odds['signal'], odds['signal'])
    win_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'no_signal': '未激活'}.get(win['signal'], win['signal'])

    subject = f"[结构因子{kind_cn}提醒] 资金流{cross_cn}零轴 + 筹码斜率{chip_sign} ({current_date}) 来自 DoMate"

    if is_risk:
        lead = (f"资金流向（绝对值因子）{flow_move}，且筹码斜率（相对值因子 {chip_sign}）同步转弱，"
                f"提示市场结构性风险，请关注持仓风险与结构变化。")
    else:
        lead = (f"资金流向（绝对值因子）{flow_move}，且筹码斜率（相对值因子 {chip_sign}）同步转强，"
                f"提示市场结构性机会，请关注后续结构性变化。")

    markdown_body = f"""## {icon} 结构因子{kind_cn}提醒

**结构因子模型提醒** | 数据日期: {current_date}

{lead}

| 指标 | 前一日 | 当日 | 判定 |
|------|-------|------|------|
| 资金流（绝对值因子） | {alert['av_prev']:.4f} | {alert['av_curr']:.4f} | {cross_cn}零轴 |
| 筹码斜率（相对值因子） | {alert['rv_prev']:.4f} | {alert['rv_curr']:.4f} | {chip_sign}（{chip_word}） |

**当前区间：** {interval['name']} (置信度 {interval['confidence']*100:.1f}%)
**策略建议：** {interval['strategy']}
**赔率模型(左侧)：** {odds_signal_cn} ({odds['confidence']*100:.0f}%)
**胜率模型(右侧)：** {win_signal_cn} ({win['confidence']*100:.0f}%)

综合建议：{market['summary']}

---
*仅供参考，不构成投资建议 | 来自 DoMate*
"""

    rows = "".join(
        f"<tr><td style='padding:8px;border:1px solid #ddd;'>{r[0]}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{r[1]}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{r[2]}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{r[3]}</td></tr>"
        for r in [
            ('资金流（绝对值因子）', f"{alert['av_prev']:.4f}", f"{alert['av_curr']:.4f}", f"{cross_cn}零轴"),
            ('筹码斜率（相对值因子）', f"{alert['rv_prev']:.4f}", f"{alert['rv_curr']:.4f}", f"{chip_sign}（{chip_word}）"),
        ]
    )

    body_html = f"""<html><body style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.6;">
<div style="max-width:640px;margin:0 auto;border:1px solid #eee;border-radius:8px;overflow:hidden;">
<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:16px 24px;">
<h2 style="margin:0;font-size:18px;">{icon} 结构因子{kind_cn}提醒</h2>
<p style="margin:4px 0 0;color:#aaa;font-size:12px;">结构因子模型提醒 | 数据日期: {current_date}</p>
</div>
<div style="padding:20px 24px;">
<p><strong>检测结果：</strong>{lead}</p>
<table style="border-collapse:collapse;width:100%;font-size:13px;">
<tr style="background:#f5f5f5;">
<th style="padding:8px;border:1px solid #ddd;text-align:left;">指标</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">前一日</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">当日</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">判定</th>
</tr>
{rows}
</table>
<div style="margin-top:16px;padding:12px;background:#f8f9fa;border-radius:6px;font-size:13px;">
<p style="margin:4px 0;"><strong>当前区间：</strong>{interval['name']} (置信度 {interval['confidence']*100:.1f}%)</p>
<p style="margin:4px 0;"><strong>策略建议：</strong>{interval['strategy']}</p>
<p style="margin:4px 0;"><strong>赔率模型(左侧)：</strong>{odds_signal_cn} ({odds['confidence']*100:.0f}%)</p>
<p style="margin:4px 0;"><strong>胜率模型(右侧)：</strong>{win_signal_cn} ({win['confidence']*100:.0f}%)</p>
</div>
<p style="margin-top:12px;color:#999;font-size:12px;">综合建议：{market['summary']}</p>
</div>
<div style="background:#fafafa;border-top:1px solid #eee;padding:10px 24px;color:#999;font-size:12px;">
仅供参考，不构成投资建议 | 来自 DoMate
</div>
</div></body></html>"""

    return _send_email(subject, markdown_body, body_html)


# ==================== 双因子同向状态邮件（双双>0 / 双双<0，各自只发一次） ====================

MAIL_STATE_DEFAULT = os.path.join(WORK_DIR, 'mail_state.json')
REGIME_CN = {'positive': '双双>0', 'negative': '双双<0'}


def _mail_state_path():
    """标记文件路径；测试时可用环境变量 STRUCTURAL_MAIL_STATE 覆盖。"""
    return os.environ.get('STRUCTURAL_MAIL_STATE') or MAIL_STATE_DEFAULT


def load_mail_state():
    """读取上次已发送标记；文件缺失或损坏一律视为「无标记」。"""
    try:
        with open(_mail_state_path(), 'r', encoding='utf-8') as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_mail_state(state):
    """原子写入标记文件。"""
    path = _mail_state_path()
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[邮件] 标记写入失败（次日会重发）: {e}")
        return False


def detect_dual_regime(rv_values, av_values):
    """
    判读资金流（绝对值因子）与筹码斜率（相对值因子）的同向状态。

    positive：两者同时 > 0；negative：两者同时 < 0；
    mixed   ：一正一负或落在零轴，视为不同向，不发邮件、不改标记。
    """
    info = {'regime': 'unknown', 'av': None, 'rv': None, 'prev_av': None, 'prev_rv': None}
    if rv_values is None or av_values is None or len(av_values) < 1 or len(rv_values) < 1:
        return info
    av, rv = float(av_values[-1]), float(rv_values[-1])
    info['av'], info['rv'] = av, rv
    if len(av_values) >= 2:
        info['prev_av'] = float(av_values[-2])
    if len(rv_values) >= 2:
        info['prev_rv'] = float(rv_values[-2])
    if av > 0 and rv > 0:
        info['regime'] = 'positive'
    elif av < 0 and rv < 0:
        info['regime'] = 'negative'
    else:
        info['regime'] = 'mixed'
    return info


def _fmt(v):
    return '—' if v is None else f"{v:.4f}"


def send_regime_email(info, market, result, current_date, prev_regime=None):
    """发送「双因子同向」邮件，标题与正文明确标注本次是 >0 还是 <0。"""
    positive = info['regime'] == 'positive'
    icon = '🟢' if positive else '🔴'
    kind_cn = REGIME_CN[info['regime']]
    mark_cn = '已发大于0' if positive else '已发小于0'
    last_cn = REGIME_CN.get(prev_regime, '无标记')
    av, rv = info['av'], info['rv']
    sign = '>0' if positive else '<0'

    lead = ('资金流（绝对值因子）与筹码斜率（相对值因子）同时站上零轴，多头能量与筹码结构同步转强，'
            '关注右侧（胜率模型）入场节奏。') if positive else \
           ('资金流（绝对值因子）与筹码斜率（相对值因子）同时跌破零轴，资金持续流出且筹码结构转弱，'
            '注意区间切换与风险控制。')

    interval = result['interval']
    odds = market['odds_signal']
    win = market['win_rate_signal']
    odds_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'exit': '退出'}.get(odds['signal'], odds['signal'])
    win_signal_cn = {'buy': '买入', 'prepare_buy': '准备买入', 'hold': '持有/观望', 'no_signal': '未激活'}.get(win['signal'], win['signal'])

    subject = f"[结构因子{kind_cn}提醒] 资金流{av:+.4f}、筹码{rv:+.4f} ({current_date}) 来自 DoMate"

    markdown_body = f"""## {icon} 结构因子{kind_cn}提醒

**结构因子模型提醒** | 数据日期: {current_date}

{lead}

| 指标 | 前一日 | 当日 | 判定 |
|------|-------|------|------|
| 资金流（绝对值因子） | {_fmt(info['prev_av'])} | {av:.4f} | {sign} |
| 筹码斜率（相对值因子） | {_fmt(info['prev_rv'])} | {rv:.4f} | {sign} |

**本次邮件标记：** {kind_cn}（{mark_cn}）
**上次标记：** {last_cn}（本次已覆盖，另一侧标记自动清除）

**当前区间：** {interval['name']} (置信度 {interval['confidence']*100:.1f}%)
**策略建议：** {interval['strategy']}
**赔率模型(左侧)：** {odds_signal_cn} ({odds['confidence']*100:.0f}%)
**胜率模型(右侧)：** {win_signal_cn} ({win['confidence']*100:.0f}%)

综合建议：{market['summary']}

---
*仅供参考，不构成投资建议 | 来自 DoMate*
"""

    rows = "".join(
        f"<tr><td style='padding:8px;border:1px solid #ddd;'>{name}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{_fmt(prev)}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{curr:.4f}</td>"
        f"<td style='padding:8px;border:1px solid #ddd;'>{sign}</td></tr>"
        for name, prev, curr in [
            ('资金流（绝对值因子）', info['prev_av'], av),
            ('筹码斜率（相对值因子）', info['prev_rv'], rv),
        ]
    )

    body_html = f"""<html><body style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.6;">
<div style="max-width:640px;margin:0 auto;border:1px solid #eee;border-radius:8px;overflow:hidden;">
<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:16px 24px;">
<h2 style="margin:0;font-size:18px;">{icon} 结构因子{kind_cn}提醒</h2>
<p style="margin:4px 0 0;color:#aaa;font-size:12px;">结构因子模型提醒 | 数据日期: {current_date}</p>
</div>
<div style="padding:20px 24px;">
<p><strong>检测结果：</strong>{lead}</p>
<table style="border-collapse:collapse;width:100%;font-size:13px;">
<tr style="background:#f5f5f5;">
<th style="padding:8px;border:1px solid #ddd;text-align:left;">指标</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">前一日</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">当日</th>
<th style="padding:8px;border:1px solid #ddd;text-align:left;">判定</th>
</tr>
{rows}
</table>
<div style="margin-top:16px;padding:12px;background:#f8f9fa;border-radius:6px;font-size:13px;">
<p style="margin:4px 0;"><strong>本次邮件标记：</strong>{kind_cn}（{mark_cn}）</p>
<p style="margin:4px 0;"><strong>上次标记：</strong>{last_cn}（本次已覆盖，另一侧标记自动清除）</p>
</div>
<div style="margin-top:12px;padding:12px;background:#f8f9fa;border-radius:6px;font-size:13px;">
<p style="margin:4px 0;"><strong>当前区间：</strong>{interval['name']} (置信度 {interval['confidence']*100:.1f}%)</p>
<p style="margin:4px 0;"><strong>策略建议：</strong>{interval['strategy']}</p>
<p style="margin:4px 0;"><strong>赔率模型(左侧)：</strong>{odds_signal_cn} ({odds['confidence']*100:.0f}%)</p>
<p style="margin:4px 0;"><strong>胜率模型(右侧)：</strong>{win_signal_cn} ({win['confidence']*100:.0f}%)</p>
</div>
<p style="margin-top:12px;color:#999;font-size:12px;">综合建议：{market['summary']}</p>
</div>
<div style="background:#fafafa;border-top:1px solid #eee;padding:10px 24px;color:#999;font-size:12px;">
仅供参考，不构成投资建议 | 来自 DoMate
</div>
</div></body></html>"""

    return _send_email(subject, markdown_body, body_html)


def maybe_send_regime_email(info, market, result, current_date):
    """
    同向状态翻转才发信：
      - 双双>0 且上次标记不是大于0 → 发大于0邮件，写入「已发大于0」标记；
      - 双双<0 且上次标记不是小于0 → 发小于0邮件，写入「已发小于0」标记（大于0标记随之失效）；
      - 状态与上次一致或双因子不同向 → 不发信、不动标记。
    发送失败不写标记，次日重试。
    """
    regime = info.get('regime')
    state = load_mail_state()
    last = state.get('regime')
    last_cn = REGIME_CN.get(last, '无标记')

    if regime not in ('positive', 'negative'):
        print(f"[邮件] 双因子不同向（资金流 {_fmt(info['av'])}、筹码 {_fmt(info['rv'])}），不发邮件，保留标记：{last_cn}")
        return False

    if regime == last:
        print(f"[邮件] 双因子同向={REGIME_CN[regime]}，与上次已发标记一致，不重复发送（标记：{last_cn}）")
        return False

    kind_cn = REGIME_CN[regime]
    if os.environ.get('STRUCTURAL_MAIL_DRY') == '1':
        print(f"[邮件][dry-run] 命中状态翻转：{last_cn} → {kind_cn}，应发「{kind_cn}」邮件；本次未实际发送、未改标记")
        return True

    ok = send_regime_email(info, market, result, current_date, last)
    if not ok:
        print("[邮件] 发送失败，保留原标记，次日重试")
        return False

    save_mail_state({
        'regime': regime,
        'mark': '已发大于0' if regime == 'positive' else '已发小于0',
        'sent_at': current_date,
        'av': round(info['av'], 6),
        'rv': round(info['rv'], 6),
    })
    print(f"[邮件] 已发送「{kind_cn}」邮件并写入标记；"
          f"{'已清除小于0标记' if regime == 'positive' else '已清除大于0标记'}")
    return True


# ==================== 主入口 ====================

if __name__ == "__main__":
    print(f"=== 结构因子每日报告 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    try:
        market, result, current_date, html_path, taiji_path, indicators_path = run_model()

        # 检测绝对因子与相对因子是否同时转向（仅用于飞书卡片标注）
        turn_info = None
        try:
            turn_info = detect_simultaneous_turn(
                result['relative_value']['values'],
                result['absolute_value']['values']
            )
            if turn_info['triggered']:
                print(f"[转向] 相对值因子与绝对值因子同时转向（rv={turn_info['rv_turn']}, av={turn_info['av_turn']}），仅作卡片标注；邮件由双因子同向规则发送")
            else:
                print(f"[转向] 未触发同时转向（rv={turn_info['rv_turn']}, av={turn_info['av_turn']}）")
        except Exception as e:
            print(f"[转向] 检测异常（不中断主流程）: {e}")

        # 资金流零轴穿越 + 筹码结构：仅用于飞书卡片标注
        alert = None
        try:
            alert = detect_risk_opportunity(
                result['relative_value']['values'],
                result['absolute_value']['values']
            )
            if alert['risk'] or alert['opportunity']:
                kind_cn = '风险' if alert['risk'] else '机会'
                cross_cn = '下穿' if alert['risk'] else '上穿'
                print(f"[风险/机会] 触发{kind_cn}提醒：资金流 {alert['av_prev']:.4f}→{alert['av_curr']:.4f}（{cross_cn}零轴），"
                      f"筹码斜率={alert['rv_curr']:.4f}；仅作卡片标注，邮件由双因子同向规则发送")
            else:
                print(f"[风险/机会] 未触发（资金流 {alert['av_prev']:.4f}→{alert['av_curr']:.4f}，筹码斜率={alert['rv_curr']:.4f}）")
        except Exception as e:
            print(f"[风险/机会] 检测异常（不中断主流程）: {e}")

        # 双因子同向状态邮件：双双>0 / 双双<0 各发一次，标记互斥
        regime_info = None
        try:
            regime_info = detect_dual_regime(
                result['relative_value']['values'],
                result['absolute_value']['values']
            )
            maybe_send_regime_email(regime_info, market, result, current_date)
        except Exception as e:
            print(f"[邮件] 同向状态提醒异常（不中断主流程）: {e}")

        zone = None
        try:
            import zone_block
            zone = zone_block.build()
            print(f"[区间] {zone['short']}｜{zone['summary']}")
        except Exception as e:
            print(f"[区间] 判定异常（不中断结构因子主流程）: {e}")

        send_feishu(market, result, current_date, taiji_path, indicators_path, turn_info, alert, zone=zone)

        if zone and zone.get('ok'):
            if os.environ.get('STRUCTURAL_CARD_DRY') == '1':
                print("[区间邮件][dry-run] 跳过切换邮件，仅打印：", zone.get('verdict', {}).get('zone'))
            else:
                print(zone_block.notify_switch(zone.get('verdict')))

        print(f"\n完成! HTML报告: {html_path}")
    except Exception as e:
        print(f"\n执行失败: {e}")
        import traceback
        traceback.print_exc()
        try:
            requests.post(WEBHOOK_URL, json={
                "msg_type": "text",
                "content": {"text": f"结构因子报告执行失败: {e} | 来自 DoMate"}
            }, timeout=10)
        except:
            pass
        sys.exit(1)
