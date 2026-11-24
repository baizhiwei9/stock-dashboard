#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
沧海一土狗地产监控周报 - 飞书交互卡片推送脚本
用法: python canghai_property_weekly_report.py '<报告标题>' '<报告正文markdown>'

卡片结构：
- 白色简洁标题栏（无彩色template）
- 一级分区：灰色背景（第一性原理、状态判定、趋势判断等）
- 折叠面板："展开"按钮 → 展开后显示"指标详情"标题 + 二级分区内容
"""

import requests
import json
import sys
import re

APP_ID = "cli_a97b60337b7bdbd5"
APP_SECRET = "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"
CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"


def get_tenant_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET})
    data = resp.json()
    if data.get("code") != 0:
        print(f"获取 token 失败: {data}")
        sys.exit(1)
    return data["tenant_access_token"]


def parse_sections(body_md):
    """将markdown正文按二级标题分割成多个section"""
    # 按 ## 标题分割，保留标题
    pattern = r'(?=\n## )'
    parts = re.split(pattern, '\n' + body_md)
    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 提取标题
        title_match = re.match(r'##\s+(.+?)(?:\n|$)', part)
        if title_match:
            title = title_match.group(1).strip()
            content = part[title_match.end():].strip()
            sections.append({"title": title, "content": content})
        else:
            # 没有标题的前置内容
            sections.append({"title": "", "content": part})
    return sections


def normalize_text(s):
    """把字面的 \n / \r\n / \n 等转义序列统一成真实换行符，
    避免飞书卡片把字面的 '\n' 原样显示出来。"""
    if not s:
        return s
    # 先处理双重转义（\\n → 换行），再处理单层转义（\n → 换行）
    s = s.replace('\\\\n', '\n')
    s = s.replace('\r\n', '\n')
    s = s.replace('\r', '\n')
    s = s.replace('\n', '\n')
    return s
    # 先处理双重转义（\\n → 换行），再处理单层转义（\n → 换行）
    s = s.replace('\\\\n', '\n')
    s = s.replace('\\r\\n', '\n')
    s = s.replace('\\r', '\n')
    s = s.replace('\\n', '\n')
    return s
    s = s.replace('\\r\\n', '\n')
    s = s.replace('\\r', '\n')
    s = s.replace('\\n', '\n')
    return s


def is_indicator_section(title):
    """判断是否为指标详情类section，应放入折叠面板"""
    indicator_keywords = ['指标', '监控', '数据', '详情', '排除', '门槛']
    return any(kw in title for kw in indicator_keywords)


def build_section_element(title, content, level=1):
    """构建一个分区元素
    level=1: 一级分区（灰色背景）
    level=2: 二级分区（浅灰/白色交替）
    """
    elements = []
    
    # 分区标题
    if title:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{title}**"
            },
            "background_style": "grey" if level == 1 else "light_grey"
        })
    
    # 分区内容
    if content:
        elements.append({
            "tag": "markdown",
            "content": normalize_text(content)
        })
    
    return elements


def build_interactive_card(title, body_md):
    """构建飞书交互式卡片"""
    # 先把字面换行符转回真实换行，否则 parse_sections 按真实 \n 分割会失效
    body_md = normalize_text(body_md)
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title
            }
            # 不设置 template，保持白色简洁风格
        },
        "elements": []
    }
    
    sections = parse_sections(body_md)
    
    # 所有section按顺序添加，用hr分隔
    for i, sec in enumerate(sections):
        card["elements"].extend(build_section_element(sec["title"], sec["content"], level=1))
        card["elements"].append({"tag": "hr"})
    
    # 去掉最后一个 hr（如果有的话）
    if card["elements"] and card["elements"][-1]["tag"] == "hr":
        card["elements"].pop()
    
    return card


def send_interactive_card(chat_id, title, body_md):
    token = get_tenant_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    card = build_interactive_card(title, body_md)
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card)
    }

    resp = requests.post(
        url,
        headers=headers,
        params={"receive_id_type": "chat_id"},
        json=payload
    )
    print(f"发送结果: {resp.status_code} {resp.text[:800]}")
    return resp.json()


def main():
    if len(sys.argv) < 3:
        print("Usage: python canghai_property_weekly_report.py '<标题>' '<正文markdown>'")
        sys.exit(1)

    title = sys.argv[1]
    body = sys.argv[2]
    result = send_interactive_card(CHAT_ID, title, body)
    if result.get("code") == 0:
        print("发送成功")
    else:
        print(f"发送失败: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
