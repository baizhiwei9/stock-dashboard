#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全系统统一邮件发送接口（唯一出口）
================================================
设计目标
--------
1. 所有定时任务/脚本的发邮件行为统一走本模块，不再各自 smtplib 拼装。
2. 每封邮件标题自动追加来源后缀，形如：
       📊 期权多指标共振预警 | 2026-11-09 | ... ｜扣子·收盘期权监控
   发件人显示名同步追加「·扣子」，正文页脚自动带一行来源标识。
   这样收到邮件就能立刻知道是哪个任务发的。
3. 发送结果统一落日志 logs/email_sent.log，便于事后追查"这封邮件是谁发的"。

用法
----
    import sys
    sys.path.insert(0, '/Coze/Drive/蓁宝/所有对话/主对话')
    from email_sender import send_email

    ok = send_email(
        subject='期权多指标共振预警 | 2026-11-09 | 中证500看空',
        html=html_body,
        source='收盘期权监控',            # ← 来源标识，必填（缺省取调用模块名）
    )

带内嵌图（cid）与附件：

    ok = send_email(
        subject='...', html=html, source='期权偏度预警',
        inline_images=[('chart1', '/path/a.png')],
        attachments=['/path/b.pdf'],
    )

纯文本：

    ok = send_email(subject='...', text='正文', source='xx任务')

注意
----
- 本模块不吞异常信息但也不向上抛，统一返回 bool，保持旧脚本 `if not ok: ...` 的容错习惯。
- 幂等（同一天同一告警只发一次）仍由各业务脚本自己控制，本模块只负责"发"。
- 如需更换邮箱账号，只改本文件 DEFAULT_CONFIG 或设置同名环境变量即可，全局生效。
"""
import os
import sys
import json
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication
from email.utils import formataddr, formatdate

# ============================================================
# 统一账号配置（全系统唯一真源）
# ============================================================
DEFAULT_CONFIG = {
    'smtp_host': os.environ.get('EMAIL_SMTP_HOST', 'smtp.163.com'),
    'smtp_port': int(os.environ.get('EMAIL_SMTP_PORT', '465')),
    'from_addr': os.environ.get('EMAIL_FROM_ADDR', 'songshang951@163.com'),
    'from_pass': os.environ.get('EMAIL_FROM_PASS', 'TBmy7DAsj9aNJdxb'),
    'to_addr': os.environ.get('EMAIL_TO_ADDR', '410119229@qq.com'),
    'use_ssl': True,
}

# 来源后缀标记：所有邮件标题/发件人统一带这个前缀，一眼看出是扣子发的
SOURCE_TAG = '扣子'

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
LOG_PATH = os.path.join(LOG_DIR, 'email_sent.log')


def _caller_source(depth=2):
    """从调用栈推断来源任务名（文件名去扩展名），作为 source 缺省值。"""
    try:
        import inspect
        frame = inspect.stack()[depth]
        fn = os.path.basename(frame.filename)
        return os.path.splitext(fn)[0]
    except Exception:
        return 'unknown'


def build_subject(subject, source):
    """标题追加来源后缀；已带后缀则不重复追加。"""
    suffix = f'｜{SOURCE_TAG}·{source}'
    subject = (subject or '').strip()
    if subject.endswith(suffix) or f'{SOURCE_TAG}·{source}' in subject:
        return subject
    return f'{subject}{suffix}'


def build_from_name(from_name, source):
    """发件人显示名追加「·扣子」，便于在邮箱列表直接辨认。"""
    base = (from_name or source or '通知').strip()
    if SOURCE_TAG in base:
        return base
    return f'{base}·{SOURCE_TAG}'


def _inject_footer(html, source, subject):
    """在 HTML 正文尾部注入来源标识块（找不到 body 就追加到末尾）。"""
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    footer = (
        f'<hr style="margin:18px 0;border:none;border-top:1px solid #eee;">'
        f'<p style="color:#999;font-size:12px;margin:0;">'
        f'来源：<b>{SOURCE_TAG}·{source}</b> ｜ 发送时间：{stamp} ｜ 自动推送，请勿直接回复'
        f'</p>'
    )
    if '</body>' in html.lower():
        idx = html.lower().rfind('</body>')
        return html[:idx] + footer + html[idx:]
    return html + footer


def _log(record):
    """发送记录落盘（JSONL），失败不影响主流程。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


def send_email(
    subject,
    html=None,
    text=None,
    source=None,
    to_addr=None,
    from_name=None,
    inline_images=None,
    attachments=None,
    cc_addr=None,
    timeout=20,
    dry_run=False,
    config=None,
):
    """统一发邮件入口。

    Args:
        subject: 邮件标题（不需要自己加来源，本函数会自动加「｜扣子·来源」）
        html: HTML 正文（与 text 二选一，同时给则两者都挂，客户端择优）
        text: 纯文本正文
        source: 来源任务名，如 "收盘期权监控"；缺省自动取调用脚本文件名
        to_addr: 收件人（str 或 list），缺省用统一配置里的默认收件人
        from_name: 发件人显示名（会自动追加「·扣子」）
        inline_images: [(cid, filepath), ...] HTML 内嵌图
        attachments: [filepath, ...] 普通附件
        cc_addr: 抄送
        timeout: SMTP 超时秒数
        dry_run: True 只打印不真发
        config: 覆盖默认配置的 dict

    Returns:
        bool: 是否发送成功
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    source = source or _caller_source()
    final_subject = build_subject(subject, source)
    final_from_name = build_from_name(from_name, source)

    # 收件人归一化
    if isinstance(to_addr, str):
        to_addrs = [to_addr]
    elif isinstance(to_addr, (list, tuple)):
        to_addrs = [str(a) for a in to_addr if a]
    else:
        to_addrs = [cfg['to_addr']]
    if not to_addrs:
        to_addrs = [cfg['to_addr']]

    # 正文
    body_html = html
    if body_html:
        body_html = _inject_footer(body_html, source, final_subject)
    if not body_html and not text:
        text = final_subject

    # 组装
    has_inline = bool(inline_images)
    msg = MIMEMultipart('related' if has_inline else 'alternative')
    msg['Subject'] = final_subject
    msg['From'] = formataddr((final_from_name, cfg['from_addr']))
    msg['To'] = ', '.join(to_addrs)
    if cc_addr:
        msg['Cc'] = ', '.join(cc_addr if isinstance(cc_addr, (list, tuple)) else [cc_addr])
    msg['Date'] = formatdate(localtime=True)

    alt = MIMEMultipart('alternative') if has_inline else msg
    if text:
        alt.attach(MIMEText(text, 'plain', 'utf-8'))
    if body_html:
        alt.attach(MIMEText(body_html, 'html', 'utf-8'))
    if has_inline:
        msg.attach(alt)
        for cid, path in inline_images:
            try:
                with open(path, 'rb') as f:
                    img = MIMEImage(f.read())
                img.add_header('Content-ID', f'<{cid}>')
                img.add_header('Content-Disposition', 'inline',
                               filename=os.path.basename(path))
                msg.attach(img)
            except Exception as e:
                print(f'  [WARN] 内嵌图挂载失败 {path}: {e}')

    # 普通附件
    for path in (attachments or []):
        try:
            with open(path, 'rb') as f:
                part = MIMEApplication(f.read())
            part.add_header('Content-Disposition', 'attachment',
                            filename=os.path.basename(path))
            msg.attach(part)
        except Exception as e:
            print(f'  [WARN] 附件挂载失败 {path}: {e}')

    record = {
        'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': source,
        'subject': final_subject,
        'to': to_addrs,
        'ok': False,
        'error': None,
    }

    if dry_run:
        print(f'  [DRY-RUN] {final_subject} -> {to_addrs}')
        record['ok'] = True
        record['error'] = 'dry_run'
        _log(record)
        return True

    try:
        if cfg.get('use_ssl', True):
            server = smtplib.SMTP_SSL(cfg['smtp_host'], cfg['smtp_port'], timeout=timeout)
        else:
            server = smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=timeout)
        with server as s:
            if cfg.get('from_pass'):
                s.login(cfg['from_addr'], cfg['from_pass'])
            s.sendmail(cfg['from_addr'], to_addrs, msg.as_string())
        record['ok'] = True
        _log(record)
        print(f'  ✅ 邮件已发送 [{SOURCE_TAG}·{source}] -> {", ".join(to_addrs)}')
        return True
    except Exception as e:
        record['error'] = str(e)
        _log(record)
        print(f'  ❌ 邮件发送失败 [{SOURCE_TAG}·{source}]: {e}')
        return False


def recent_sent(limit=20, source=None):
    """读取最近的发送记录（排查"这封邮件是谁发的"用）。"""
    if not os.path.exists(LOG_PATH):
        return []
    rows = []
    with open(LOG_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if source and rec.get('source') != source:
                continue
            rows.append(rec)
    return rows[-limit:]


if __name__ == '__main__':
    # 自检：python3 email_sender.py [--send]
    demo_html = ('<html><body><h3>统一邮件接口自检</h3>'
                 '<p>若你收到这封邮件，说明接口可用，标题末尾应带来源后缀。</p>'
                 '</body></html>')
    do_send = '--send' in sys.argv
    send_email('统一邮件接口自检', html=demo_html, source='email_sender自检',
               dry_run=not do_send)
    if not do_send:
        print('（dry-run，加 --send 才真正发送）')
