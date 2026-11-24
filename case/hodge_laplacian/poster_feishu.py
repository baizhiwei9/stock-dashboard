#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三合一海报：Pillow 生成 + 飞书上传 + webhook 发送
"""
import sys, os, json, requests
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fb973b4beffe8IdHR8Rmy21klf')
from feishu_config import APP_ID, APP_SECRET, WEBHOOK_URL, TOKEN_URL, UPLOAD_URL

# ---- Fonts ----
_ZH = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
_EN = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

def F(size, lang='zh'):
    return ImageFont.truetype(_ZH if lang == 'zh' else _EN, size)

def TS(draw, text, font):
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def DT(draw, x, y, text, font, fill='black', anchor='la'):
    w, h = TS(draw, text, font)
    if anchor == 'ma': x -= w//2
    elif anchor == 'ra': x -= w
    elif anchor == 'lm': y -= h//2
    elif anchor == 'mm': x -= w//2; y -= h//2
    elif anchor == 'rm': x -= w; y -= h//2
    draw.text((x, y), text, fill=fill, font=font)
    return w, h

def DM(draw, x, y, text, zh_f, en_f, fill='black', anchor='la'):
    """混合中英文渲染"""
    runs = []
    cl = None
    cr = ''
    for ch in text:
        is_ascii = '\x00' <= ch <= '\x7f'
        lang = 'en' if is_ascii else 'zh'
        if lang == cl:
            cr += ch
        else:
            if cr: runs.append((cl, cr))
            cl = lang; cr = ch
    if cr: runs.append((cl, cr))
    tw = 0; th = 0
    for lang, run in runs:
        w, h = TS(draw, run, zh_f if lang == 'zh' else en_f)
        tw += w; th = max(th, h)
    if anchor == 'ma': x -= tw//2
    elif anchor == 'ra': x -= tw
    cx = x
    for lang, run in runs:
        font = zh_f if lang == 'zh' else en_f
        w, h = TS(draw, run, font)
        dy = (th - h)//2
        draw.text((cx, y+dy), run, fill=fill, font=font)
        cx += w
    return tw, th

# ---- Data ----
WAIXU = {
    'state': '偏强', 'score': 0.59,
    'items': [
        ('中国出口同比', '+25.0%'),
        ('韩国出口同比', '+70.7%'),
        ('美国进口同比', '+16.9%'),
        ('SCFI综合指数', '2032.0'),
        ('日本出口同比', '+5.8%'),
        ('耐用品订单', '+12.0%'),
    ],
    'mom': 'SCFI周涨+9.2% | BDI周均+5.2%',
    'yoy': '中国出口+25% | 美国进口+16.9%',
    'near': '日韩CLI上行，外需边际改善',
}
NEIXU = {
    'state': '中性', 'view': '看平',
    'items': [
        ('制造业PMI', '49.8↓'),
        ('社零同比', '+0.6%'),
        ('固投累计', '-6.7%'),
        ('六大电日耗', '91万吨'),
        ('30城商品房', '+15.3%'),
        ('乘用车日均', '8.4万辆'),
    ],
    'mom': '日耗-5.2%(季节) | 商品房+15.3%(冲量)',
    'yoy': '社零+0.6% | 固投-6.7% | 汽车-19%',
    'near': 'PMI 49.8收缩区，缺乏趋势扩张信号',
}
FISCAL = {
    'state': '传导期前段', 'phase': '蓄水→传导过渡',
    'items': [
        ('专项债进度', '68%'),
        ('政府债进度', '66%'),
        ('实物工作量', '零星启动'),
        ('9月计划发行', '7500亿'),
    ],
    'cli': [
        ('美国', 100.80, +0.04),
        ('韩国', 102.87, +0.21),
        ('日本', 100.30, +0.06),
        ('德国', 100.72, -0.04),
        ('中国', 98.57, -0.03),
    ],
    'mom': '专项债8月发行5188亿(年内第二高)',
    'yoy': '发行节奏后置(vs 2025同期76%)',
    'near': 'OECD CLI美日韩上行，中国低于趋势',
}

# ---- Draw Poster ----
def draw_poster():
    W, H = 2400, 1600
    img = Image.new('RGB', (W, H), '#f8f9fa')
    d = ImageDraw.Draw(img)

    f_title = F(48, 'zh')
    f_sub = F(24, 'zh')
    f_head = F(36, 'zh')
    f_body = F(22, 'zh')
    f_num = F(26, 'en')
    f_small = F(18, 'zh')
    f_tiny = F(16, 'zh')

    # Title
    DM(d, W//2, 30, '中国宏观经济三维度监测全景图', f_title, f_title, fill='#1a1a2e', anchor='ma')
    DM(d, W//2, 90, f'数据日期：{datetime.now().strftime("%Y年%m月%d日")} | 报告来自于 DoMate', f_sub, f_sub, fill='#666666', anchor='ma')

    # Three panels
    pw = 740
    ph = 1050
    px = [40, 830, 1620]
    py = 140

    colors = [
        ('#e8f5e9', '#4caf50', '#2e7d32', '#1b5e20'),
        ('#fff3e0', '#ff9800', '#e65100', '#bf360c'),
        ('#e3f2fd', '#2196f3', '#0d47a1', '#1565c0'),
    ]
    titles = ['外需', '内需', '政府财政']
    datas = [WAIXU, NEIXU, FISCAL]

    for i in range(3):
        bg, border, title_c, sub_c = colors[i]
        x, data = px[i], datas[i]
        # Panel bg
        d.rounded_rectangle([x, py, x+pw, py+ph], radius=20, fill=bg, outline=border, width=4)
        # Title
        DM(d, x+pw//2, py+25, titles[i], f_head, f_head, fill=title_c, anchor='ma')
        DM(d, x+pw//2, py+75, f"状态：{data['state']}", f_body, f_body, fill=sub_c, anchor='ma')
        if 'score' in data:
            DM(d, x+pw//2, py+110, f"信号打分：{data['score']:+.2f}", f_body, f_body, fill='#388e3c', anchor='ma')
        if 'view' in data:
            DM(d, x+pw//2, py+110, f"景气研判：{data['view']}", f_body, f_body, fill='#ef6c00', anchor='ma')
        if 'phase' in data:
            DM(d, x+pw//2, py+110, data['phase'], f_body, f_body, fill='#1976d2', anchor='ma')

        # Items
        iy = py + 160
        for name, val in data['items']:
            DM(d, x+30, iy, f"• {name}", f_body, f_body, fill='#333333', anchor='la')
            DM(d, x+pw-30, iy, val, f_num, f_num, fill=border, anchor='ra')
            iy += 55

        # CLI table (only for fiscal)
        if 'cli' in data:
            iy += 10
            DM(d, x+pw//2, iy, 'OECD CLI 周边对比', f_body, f_body, fill=title_c, anchor='ma')
            iy += 45
            for country, val, change in data['cli']:
                arrow = '↑' if change > 0 else '↓'
                ccolor = '#4caf50' if val > 100 else '#f44336'
                DM(d, x+50, iy, country, f_small, f_small, fill='#333', anchor='la')
                DM(d, x+pw//2-20, iy, f"{val:.2f}", f_num, f_num, fill='#333', anchor='ma')
                DM(d, x+pw-50, iy, f"{arrow} {change:+.2f}", f_small, f_small, fill=ccolor, anchor='ra')
                iy += 38

        # Bottom labels
        iy = py + ph - 130
        DM(d, x+pw//2, iy, f"环比：{data['mom']}", f_tiny, f_tiny, fill='#666666', anchor='ma')
        iy += 32
        DM(d, x+pw//2, iy, f"同比：{data['yoy']}", f_tiny, f_tiny, fill='#666666', anchor='ma')
        if 'near' in data:
            iy += 32
            DM(d, x+pw//2, iy, f"周边：{data['near']}", f_tiny, f_tiny, fill='#666666', anchor='ma')

        # Progress bar
        bar_y = py + ph - 40
        if i == 0:
            pct = int(data['score'] / 1.0 * 100)
        elif i == 1:
            pct = 50
        else:
            pct = 68
        bar_w = int((pw - 80) * pct / 100)
        d.rounded_rectangle([x+40, bar_y, x+pw-40, bar_y+22], radius=11, fill='#e0e0e0')
        d.rounded_rectangle([x+40, bar_y, x+40+bar_w, bar_y+22], radius=11, fill=border)
        DM(d, x+pw//2, bar_y+2, f"强度进度: {pct}%", f_tiny, f_tiny, fill='white', anchor='ma')

    # Bottom summary bar
    by = py + ph + 30
    bh = 180
    d.rounded_rectangle([40, by, W-40, by+bh], radius=20, fill='#fce4ec', outline='#e91e63', width=3)
    DM(d, W//2, by+25, '📊 综合诊断', F(32, 'zh'), F(32, 'en'), fill='#c62828', anchor='ma')
    DM(d, W//2, by+75, '外需偏强(+0.59) + 内需中性(看平) + 财政传导期前段(68%) = 温和修复',
       F(22, 'zh'), F(22, 'en'), fill='#880e4f', anchor='ma')
    DM(d, W//2, by+115, '结构特征：外需 > 财政 > 内需 | 风险点：中国CLI低于趋势(98.57)、内需缺乏主动扩张信号',
       F(20, 'zh'), F(20, 'en'), fill='#ad1457', anchor='ma')
    DM(d, W//2, by+150, '关注窗口：9-10月财政广义赤字高峰 + 「金九」旺季内需验证',
       F(20, 'zh'), F(20, 'en'), fill='#c2185b', anchor='ma')

    out_path = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_00495a421ffeZp6sKsQZoGR6e9/macro_poster_v2.png"
    img.save(out_path, 'PNG')
    print(f"海报已保存: {out_path}")
    return out_path

# ---- Feishu Upload ----
def get_token():
    r = requests.post(TOKEN_URL, json={'app_id': APP_ID, 'app_secret': APP_SECRET}, timeout=15)
    return r.json().get('tenant_access_token', '')

def upload_image(path, token):
    with open(path, 'rb') as f:
        r = requests.post(UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                          data={'image_type': 'message'}, files={'image': f}, timeout=30)
    return r.json().get('data', {}).get('image_key', '')

def send_feishu_image(image_key):
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "报告来自于 DoMate | 宏观经济三维度全景图"},
            "template": "red"
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**📊 外需 + 内需 + 政府财政 三维监测全景图**\n数据日期：2026-09-11"}
            },
            {"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "三维监测海报"}},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**综合诊断**：外需偏强(+0.59) + 内需中性(看平) + 财政传导期前段(68%) = 温和修复\n结构特征：外需 > 财政 > 内需 | 关注9-10月验证窗口"}
            }
        ]
    }
    payload = {"msg_type": "interactive", "card": card}
    r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    return r.json()

# ---- Main ----
if __name__ == '__main__':
    print("[1/3] 生成海报...")
    poster = draw_poster()

    print("[2/3] 上传图片到飞书...")
    token = get_token()
    if not token:
        print("ERROR: 无法获取 tenant_access_token")
        sys.exit(1)
    image_key = upload_image(poster, token)
    if not image_key:
        print("ERROR: 无法上传图片")
        sys.exit(1)
    print(f"image_key: {image_key}")

    print("[3/3] 发送飞书卡片...")
    result = send_feishu_image(image_key)
    print(f"飞书响应: {result}")

    if result.get('StatusCode') == 0 or result.get('code') == 0:
        print("✓ 海报卡片发送成功!")
    else:
        print(f"✗ 发送失败: {result}")
