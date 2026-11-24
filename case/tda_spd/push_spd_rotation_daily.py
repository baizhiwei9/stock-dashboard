#!/usr/bin/env python3
"""每日推送 SPD 行业旋转轨迹图（近5日 SPD Top5 因子权重折线）到飞书

流程: 读 state/signal_history_22f.json（主流程已保存当日信号）
  -> 提取近5日 -> state/spd_rotation_data.json
  -> 生成旋转日报 HTML（gen_rotation_report.generate, 输出按最新交易日命名）
  -> playwright 本地渲染, 截图旋转轨迹图 div.card[2] -> PNG
  -> 上传飞书 im/v1/images 取得 image_key
  -> webhook 发送单条交互式卡片(msg_type=interactive): 标题「来自 DoMate」+ 最新信号 + 旋转轨迹图内嵌 + 逐日权重 + 关键流转

供 run_daily_22f.py 每日推送日报卡片后调用；本脚本自身失败由调用方捕获, 不阻断主报告。

用法:
  python push_spd_rotation_daily.py           # 生成 + 推送飞书
  python push_spd_rotation_daily.py --no-feishu   # 仅生成 HTML 与 PNG, 不推送
"""
import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# playwright 浏览器二进制位置（与既有环境保持一致）
os.environ.setdefault(
    'PLAYWRIGHT_BROWSERS_PATH',
    '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.dumate-cache/ms-playwright'
)

BASE = HERE
STATE_DIR = os.path.join(BASE, 'state')
SIGNAL_FILE = os.path.join(STATE_DIR, 'signal_history_22f.json')
ROT_DATA_FILE = os.path.join(STATE_DIR, 'spd_rotation_data.json')
N_DAYS = 5

from feishu_config import APP_ID, APP_SECRET, WEBHOOK_URL, TOKEN_URL, UPLOAD_URL
import requests


# ---------- 1. 提取近5日 SPD 序列（与 state/spd_rotation_extract.py 同逻辑, 日内联） ----------
def extract_rotation():
    with open(SIGNAL_FILE, encoding='utf-8') as f:
        data = json.load(f)
    signals = data.get('signals', [])
    by_date = {}
    for s in signals:
        by_date[s['trade_date']] = s
    dates = sorted(by_date.keys())
    if not dates:
        raise RuntimeError('signal_history_22f.json 中无信号记录')
    recent_dates = dates[-N_DAYS:]
    days = []
    for d in recent_dates:
        s = by_date[d]
        w = {k: round(v, 4) for k, v in s.get('weights', {}).items() if v > 0}
        days.append({
            'date': f"{d[:4]}-{d[4:6]}-{d[6:]}",
            'trade_date': d,
            'weights': w,
            'position_pct': round(s.get('position_ratio', 0) * 100, 1),
            'state': s.get('market_state', ''),
        })
    transitions = []
    for i in range(len(days) - 1):
        w0, w1 = days[i]['weights'], days[i + 1]['weights']
        keep = sorted(set(w0) & set(w1))
        rin = sorted(set(w1) - set(w0))
        rout = sorted(set(w0) - set(w1))
        transitions.append({
            'from': days[i]['date'],
            'to': days[i + 1]['date'],
            'keep': [{'factor': k, 'from_w': w0[k], 'to_w': w1[k]} for k in keep],
            'in': [{'factor': k, 'w': w1[k]} for k in rin],
            'out': [{'factor': k, 'w': w0[k]} for k in rout],
        })
    out = {'days': days, 'transitions': transitions}
    with open(ROT_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'[1/4] 提取近{N_DAYS}日 SPD 序列 -> {ROT_DATA_FILE}')
    for day in days:
        ws = ', '.join(f"{k}={v*100:.1f}%" for k, v in day['weights'].items())
        print(f"      {day['date']} [{day['state']} 仓位{day['position_pct']}%] {ws}")
    return out


# ---------- 2. 生成旋转日报 HTML ----------
def build_html():
    from gen_rotation_report import generate
    out_html = generate(base_dir=BASE)
    print(f'[2/4] 生成旋转日报 HTML -> {out_html}')
    return out_html


# ---------- 3. playwright 截图旋转轨迹图（div.card 第3块 = 旋转轨迹图） ----------
def render_trajectory(html_path, trade_date, out_dir=None):
    if out_dir is None:
        out_dir = BASE
    else:
        os.makedirs(out_dir, exist_ok=True)
    png_path = os.path.join(out_dir, f'SPD行业旋转轨迹_截至{trade_date}.png')

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=BASE)
    with socketserver.TCPServer(('127.0.0.1', 0), handler) as httpd:
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            from playwright.sync_api import sync_playwright
            url = f'http://127.0.0.1:{port}/{os.path.basename(html_path)}'
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={'width': 1180, 'height': 960}, device_scale_factor=2)
                page.goto(url, wait_until='networkidle', timeout=60000)
                page.wait_for_selector('#trajectory canvas', timeout=30000)
                time.sleep(0.8)
                page.locator('div.card').nth(2).screenshot(path=png_path)
                browser.close()
        finally:
            httpd.shutdown()
    size_kb = round(os.path.getsize(png_path) / 1024, 1)
    print(f'[3/4] 渲染旋转轨迹图 PNG -> {png_path} ({size_kb} KB)')
    return png_path


# ---------- 4. 推送飞书（文本说明 + 旋转轨迹图） ----------
def get_tenant_access_token():
    resp = requests.post(TOKEN_URL, json={
        'app_id': APP_ID,
        'app_secret': APP_SECRET,
    }, timeout=30)
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'获取 tenant_access_token 失败: {data}')
    return data['tenant_access_token']


def upload_image(token, path):
    with open(path, 'rb') as f:
        resp = requests.post(
            UPLOAD_URL,
            headers={'Authorization': f'Bearer {token}'},
            data={'image_type': 'message'},
            files={'image': (os.path.basename(path), f, 'image/png')},
            timeout=60,
        )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'上传图片失败 {os.path.basename(path)}: {data}')
    return data['data']['image_key']


def send_webhook(payload, label):
    for attempt in range(3):
        try:
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)
            result = resp.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                print(f'[OK] {label} 发送成功')
                return True
            print(f'[WARN] {label} 返回: {result}')
        except Exception as e:
            print(f'[WARN] {label} 异常(第{attempt+1}次): {e}')
        time.sleep(2)
    return False


def _md_div(content):
    return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}}


def _fmt_pct(v):
    return f"{v * 100:.1f}%"


def build_rotation_card(rot, image_key, trade_date=None):
    """构建 SPD 行业旋转轨迹图交互式卡片（interactive）, 图片内嵌卡片

    结构:
      header  : SPD 行业旋转轨迹图 | 截至 YYYY-MM-DD 收盘 | 来自 DoMate
      div     : 最新信号 T+1 生效 Top5 因子权重
      img     : 旋转轨迹图（image_key）
      hr
      div     : 近5日逐日状态/仓位/Top5 权重
      div     : 关键流转(轮入/轮出)
      note    : 口径与免责声明
    """
    days = rot['days']
    last_day = days[-1]
    if trade_date is None:
        trade_date = last_day['trade_date']
    date_display = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

    elements = []

    tops = ' / '.join(f"{k} {_fmt_pct(v)}" for k, v in last_day['weights'].items())
    elements.append(_md_div(f"**最新信号 T+1 生效**（仓位 {last_day['position_pct']}% · {last_day['state']}）：{tops}"))

    elements.append({
        'tag': 'img',
        'img_key': image_key,
        'alt': {'tag': 'plain_text', 'content': f'SPD 行业旋转轨迹图 截至{date_display}'},
    })

    elements.append({'tag': 'hr'})

    lines = []
    for d in days:
        ws = ' / '.join(f"{k} {_fmt_pct(v)}" for k, v in d['weights'].items())
        lines.append(f"· {d['date']} [{d['state']}·{d['position_pct']}%]：{ws}")
    elements.append(_md_div(
        f"**近{len(days)}个交易日 SPD Top5 因子权重旋转**（多因子折线，断点=当日轮出组合）\n" + "\n".join(lines)
    ))

    trans_lines = []
    for t in rot.get('transitions', []):
        seg = f"· {t['from'][5:]}→{t['to'][5:]}"
        if t.get('in'):
            seg += " 轮入 " + "、".join(f"{x['factor']}({_fmt_pct(x['w'])})" for x in t['in'])
        if t.get('out'):
            seg += "；轮出 " + "、".join(f"{x['factor']}({_fmt_pct(x['w'])})" for x in t['out'])
        if not t.get('in') and not t.get('out'):
            seg += " 组合无进出"
        trans_lines.append(seg)
    if trans_lines:
        elements.append(_md_div("**关键流转**\n" + "\n".join(trans_lines)))

    elements.append({
        'tag': 'note',
        'elements': [{'tag': 'plain_text', 'content':
                      'SPD Fisher-z 测地漂移选行业 | 申万行业指数 | 信号T日收盘生成、T+1生效 | 仅供参考,不构成投资建议'}],
    })

    card = {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content':
                      f'SPD 行业旋转轨迹图 | 截至 {date_display} 收盘 | 来自 DoMate'},
            'template': 'turquoise',
        },
        'elements': elements,
    }
    return card


def push_feishu(png_path, rot):
    token = get_tenant_access_token()
    print('[4/4] 已获取 tenant_access_token')

    try:
        image_key = upload_image(token, png_path)
    except Exception as e:
        print(f'[FATAL] 旋转轨迹图上传失败: {e}')
        return False

    card = build_rotation_card(rot, image_key)
    ok = send_webhook({'msg_type': 'interactive', 'card': card}, '交互式卡片-旋转轨迹图')
    if not ok:
        print('[FATAL] 旋转轨迹图交互式卡片发送失败')
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description='生成并推送 SPD 行业旋转轨迹图到飞书')
    parser.add_argument('--no-feishu', action='store_true', help='仅生成 HTML/PNG, 不推送飞书')
    parser.add_argument('--out-dir', type=str, default=None, help='PNG 输出目录(默认与脚本同目录)')
    args = parser.parse_args()

    rot = extract_rotation()
    html_path = build_html()
    trade_date = rot['days'][-1]['trade_date']
    png_path = render_trajectory(html_path, trade_date, out_dir=args.out_dir)

    if args.no_feishu:
        print('跳过飞书推送 (--no-feishu)')
        return 0

    ok = push_feishu(png_path, rot)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())