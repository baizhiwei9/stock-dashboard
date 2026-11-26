# -*- coding: utf-8 -*-
"""
通用飞书群推送模块
所有脚本统一调用，避免LLM读报告再拼推送命令

用法:
    from feishu_push import push_to_feishu
    push_to_feishu("/path/to/report.md")            # 默认推卡片
    push_to_feishu(text="内容")                      # 默认推卡片
    push_to_feishu(text="内容", as_card=False)       # 回退纯文本
    # 或直接推文本:
    push_to_feishu(text="直接推送的文本")
"""

import subprocess
import os
import sys
import json
import re

CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"
MAX_LEN = 4000  # 飞书单条消息字符上限
CARD_MD_MAX = 2800  # 飞书卡片单个Markdown元素字符上限（留buffer）


# ============================================================
# 通用 Markdown 转卡片
# ============================================================

def markdown_to_card(title, content):
    """
    将标题+Markdown内容转为飞书v2交互式卡片JSON

    参数:
        title: 卡片标题字符串
        content: Markdown格式的内容文本

    返回:
        dict: 飞书交互式卡片JSON（可直接json.dumps传给push_card_to_feishu）
    """
    truncated = False
    if len(content) > CARD_MD_MAX:
        # 在限制长度附近找最后一个换行，避免截断到一半
        cut = content.rfind('\n', 0, CARD_MD_MAX)
        if cut <= 0:
            cut = CARD_MD_MAX
        content = content[:cut]
        truncated = True

    # 构建卡片元素
    elements = [
        {
            "tag": "markdown",
            "content": content
        }
    ]

    if truncated:
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "⚠️ 内容较长，已截断显示。完整内容请以文件形式查看。"
                }
            ]
        })

    # 飞书v2交互式卡片格式
    card = {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title
            },
            "template": "blue"
        },
        "elements": elements
    }

    return card


def _extract_title_from_content(content):
    """
    从内容中提取标题：
    - 搜索所有行，找到第一个 # 开头的行作为标题
    - 否则返回默认标题
    """
    if not content:
        return "📊 消息通知"

    for line in content.strip().split('\n'):
        line = line.strip()
        # 匹配 Markdown 标题: # xxx, ## xxx, ### xxx
        m = re.match(r'^#{1,6}\s+(.+)$', line)
        if m:
            return m.group(1).strip()

    return "📊 消息通知"


# ============================================================
# 核心推送函数
# ============================================================

def push_to_feishu(file_path=None, text=None, chat_id=None, as_file=False, as_card=True):
    """
    推送消息到飞书群

    参数:
        file_path: 读取文件内容推送，或 as_file=True 时作为附件上传发送
        text: 直接推送文本（优先于file_path，除非 as_file=True）
        chat_id: 可选指定群ID，默认AI股票消息群
        as_file: True 时将 file_path 作为文件附件发送（可预览），适合长报告
        as_card: True（默认）时将内容包装为交互式卡片推送；False 时走纯文本逻辑
    """
    cid = chat_id or CHAT_ID

    # 以文件附件方式发送（不受as_card影响）
    if as_file and file_path:
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return False
        return _send_file(file_path, cid)

    if text is None:
        if file_path is None:
            print("❌ 必须提供 file_path 或 text")
            return False
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return False
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()

    if not text.strip():
        print("❌ 推送内容为空")
        return False

    # 卡片模式
    if as_card:
        title = _extract_title_from_content(text)
        card = markdown_to_card(title, text)
        return push_card_to_feishu(card_dict=card, chat_id=cid)

    # 纯文本模式（原有逻辑）
    segments = _split_text(text, MAX_LEN)
    success = 0

    for i, seg in enumerate(segments):
        prefix = f"[{i+1}/{len(segments)}] " if len(segments) > 1 else ""
        content = prefix + seg
        ok = _send_one(content, cid)
        if ok:
            success += 1
        else:
            print(f"❌ 第{i+1}段推送失败")

    total = len(segments)
    if success == total:
        print(f"✅ 飞书推送完成 ({total}段)")
        return True
    else:
        print(f"⚠️ 飞书推送部分失败: {success}/{total}")
        return False


def push_card_to_feishu(card_dict=None, card_json=None, card_file=None, chat_id=None):
    """
    推送交互式卡片到飞书群
    - card_dict: 卡片字典（Python dict，会被json.dumps）
    - card_json: 卡片JSON字符串
    - card_file: 卡片JSON文件路径
    - chat_id: 可选指定群ID，默认AI股票消息群
    """
    cid = chat_id or CHAT_ID

    # 优先使用dict
    if card_dict is not None:
        content = json.dumps(card_dict, ensure_ascii=False)
    elif card_json is not None:
        content = card_json
    elif card_file is not None:
        if not os.path.exists(card_file):
            print(f"❌ 卡片文件不存在: {card_file}")
            return False
        with open(card_file, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        print("❌ 必须提供 card_dict / card_json / card_file 之一")
        return False

    # lark-cli --msg-type interactive --content 直接传card对象JSON即可
    cmd = [
        'lark-cli', 'im', '+messages-send',
        '--chat-id', cid,
        '--msg-type', 'interactive',
        '--content', content,
        '--as', 'bot'
    ]
    env = os.environ.copy()
    env['LARK_CLI_NO_PROXY'] = '1'

    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and ('"ok": true' in result.stdout or '"ok":true' in result.stdout):
            print("✅ 飞书卡片推送完成")
            return True
        else:
            print(f"❌ 卡片推送失败: {result.stderr[:300] or result.stdout[:300]}")
            return False
    except Exception as e:
        print(f"❌ 卡片推送异常: {e}")
        return False


# ============================================================
# 内部工具函数
# ============================================================

def _send_file(file_path, chat_id):
    """以文件附件方式发送到飞书群（可预览）"""
    file_dir = os.path.dirname(os.path.abspath(file_path))
    file_name = os.path.basename(file_path)
    cmd = [
        'lark-cli', 'im', '+messages-send',
        '--chat-id', chat_id,
        '--file', f'./{file_name}',
        '--as', 'bot'
    ]
    env = os.environ.copy()
    env['LARK_CLI_NO_PROXY'] = '1'

    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=60,
            cwd=file_dir
        )
        if result.returncode == 0 and ('"ok": true' in result.stdout or '"ok":true' in result.stdout):
            print(f"✅ 飞书文件推送完成: {file_name}")
            return True
        else:
            print(f"❌ 文件推送失败: {result.stderr or result.stdout[:300]}")
            return False
    except Exception as e:
        print(f"❌ 文件推送异常: {e}")
        return False


def _split_text(text, max_len):
    """按段落分割长文本，尽量在换行处切分"""
    if len(text) <= max_len:
        return [text]

    segments = []
    while text:
        if len(text) <= max_len:
            segments.append(text)
            break
        # 在max_len附近找换行符
        cut = text.rfind('\n', 0, max_len)
        if cut <= 0:
            cut = max_len
        segments.append(text[:cut])
        text = text[cut:].lstrip('\n')

    return segments


def _send_one(content, chat_id):
    """执行lark-cli发送一条消息"""
    cmd = [
        'lark-cli', 'im', '+messages-send',
        '--chat-id', chat_id,
        '--text', content,
        '--as', 'bot'
    ]
    env = os.environ.copy()
    env['LARK_CLI_NO_PROXY'] = '1'

    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and '"ok": true' in result.stdout:
            return True
        else:
            print(f"❌ lark-cli返回: {result.stderr or result.stdout[:200]}")
            return False
    except Exception as e:
        print(f"❌ 推送异常: {e}")
        return False


if __name__ == '__main__':
    # 测试: python feishu_push.py <file_path>
    if len(sys.argv) > 1:
        push_to_feishu(file_path=sys.argv[1])
    else:
        push_to_feishu(text="🔧 feishu_push模块测试 - 可忽略")
