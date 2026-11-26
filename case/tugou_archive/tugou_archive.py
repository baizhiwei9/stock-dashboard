#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
沧海一土狗文章归档脚本
- 从飞书群「沧海一土狗 | 星知思源」拉取消息
- 筛选第三类文章（排除股票市场、债券市场）
- 用飞书 CLI docs +fetch API 抓取 wiki 正文（markdown格式）
- 上传到 IMA 知识库「沧海一土狗」
- 支持全量历史归档和每日增量两种模式

用法:
  python3 tugou_archive.py --mode full      # 全量历史归档
  python3 tugou_archive.py --mode daily     # 每日增量（只抓当天）
"""
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

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent.resolve()
ARTICLES_DIR = BASE_DIR / "articles"
STATE_FILE = BASE_DIR / "archive_state.json"
LOGIN_STATE = BASE_DIR / "playwright_state.json"  # 已废弃，保留兼容

CHAT_ID = "oc_0ca59fa0674e77c608e843d9d55a6757"
KB_NAME = "沧海一土狗"  # 按名字动态查询最新ID，避免IMA升级导致ID失效
KB_ID = "ima_kb_W4yAiAoY"  # 兜底，正常运行时由 resolve_kb_id() 动态覆盖

IMA_SKILL_DIR = Path(_cp("/Coze/Drive/飞书_cli/skill-drafts/ima-knowledge-base"))
IMA_API_SCRIPT = IMA_SKILL_DIR / "ima_api.cjs"

EXCLUDE_TYPES = {"股票市场", "债券市场"}

ARTICLES_DIR.mkdir(exist_ok=True)


# ============================================================
# 状态管理（去重）
# ============================================================
def wiki_id(url):
    """从URL提取wiki ID，用于跨域去重"""
    m = re.search(r'/wiki/([A-Za-z0-9]+)', url)
    return m.group(1) if m else url


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"uploaded": {}, "last_run": None}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_uploaded(article_url, state):
    # 用wiki ID去重，兼容域名变化
    wid = wiki_id(article_url)
    for url in state.get("uploaded", {}):
        if wiki_id(url) == wid:
            return True
    return False


def mark_uploaded(article_url, title, media_id, state):
    state.setdefault("uploaded", {})[article_url] = {
        "title": title,
        "media_id": media_id,
        "upload_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# 飞书 CLI 拉群消息
# ============================================================
def fetch_messages(page_size=50, start_time=None, end_time=None):
    """拉取群消息，返回第三类文章列表"""
    cmd = [
        "lark-cli", "im", "+chat-messages-list",
        "--chat-id", CHAT_ID,
        "--page-size", str(page_size),
        "--order", "asc",
        "--as", "user",
        "--page-all",
        "--page-limit", "100",  # 最多100页*50=5000条，足够覆盖全量
    ]
    if start_time:
        cmd.extend(["--start-time", start_time])
    if end_time:
        cmd.extend(["--end-time", end_time])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[ERROR] 拉取群消息失败: {result.stderr[:200]}")
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("[ERROR] 解析消息失败")
        return []

    if not data.get("ok"):
        print(f"[ERROR] 飞书API错误: {data.get('error', {})}")
        return []

    messages = data.get("data", {}).get("messages", [])
    articles = []

    for m in messages:
        if m.get("msg_type") != "interactive":
            continue
        if m.get("deleted"):
            continue

        content = m.get("content", "")

        title_match = re.search(r"内容：\[([^\]]+)\]", content)
        # 新卡片域名是 my.feishu.cn，旧卡片是 gz1u69sh97.feishu.cn
        url_match = re.search(r"\((https://(?:my|gz1u69sh97)\.feishu\.cn/wiki/[^\)]+)\)", content)
        date_match = re.search(r"日期：([^\n<]+)", content)
        type_match = re.search(r"类型：([^\n<\]]+)", content)

        if not url_match or not title_match:
            continue

        title = title_match.group(1).strip()
        url = url_match.group(1).strip()
        # 统一用 my.feishu.cn 域名（API 两个域名都支持，统一格式方便去重）
        url = url.replace("gz1u69sh97.feishu.cn/wiki/", "my.feishu.cn/wiki/")
        date_str = date_match.group(1).strip() if date_match else ""
        article_type = type_match.group(1).strip() if type_match else ""

        # 排除规则：类型或标题包含"股票市场"/"债券市场"的跳过
        is_excluded = False
        if article_type and article_type in EXCLUDE_TYPES:
            is_excluded = True
        if any(kw in title for kw in EXCLUDE_TYPES):
            is_excluded = True

        if is_excluded:
            continue

        articles.append({
            "date": date_str,
            "type": article_type,
            "title": title,
            "url": url,
            "msg_id": m.get("message_id", ""),
            "create_time": m.get("create_time", ""),
        })

    return articles


# ============================================================
# 飞书 CLI 抓取 wiki 正文（官方 API，markdown格式）
# ============================================================
def fetch_article_content(url, title):
    """用飞书 CLI 抓取 wiki 正文，返回 markdown 文本和文件路径
    流程：wiki URL -> wiki node-get（获取obj_token）-> docs +fetch（获取正文）
    """
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:80]
    md_path = ARTICLES_DIR / f"{safe_title}.md"

    # 如果已经抓过本地文件，直接读
    if md_path.exists() and md_path.stat().st_size > 100:
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read(), str(md_path)

    try:
        # 1. 先通过 wiki node-get 拿到 obj_token
        node_result = subprocess.run(
            ["lark-cli", "wiki", "+node-get",
             "--node-token", url,
             "--as", "user"],
            capture_output=True, text=True, timeout=15
        )
        if node_result.returncode != 0:
            print(f"  [WARN] wiki node-get失败 {title}: {node_result.stderr[:150]}")
            return None, None

        node_data = json.loads(node_result.stdout)
        if not node_data.get("ok"):
            print(f"  [WARN] wiki node-get错误 {title}: {node_data.get('error', {})}")
            return None, None

        obj_token = node_data.get("data", {}).get("obj_token", "")
        obj_type = node_data.get("data", {}).get("obj_type", "")
        if not obj_token:
            print(f"  [WARN] 无法获取obj_token {title}")
            return None, None

        # 2. 用 obj_token 拉正文
        doc_result = subprocess.run(
            ["lark-cli", "docs", "+fetch",
             "--doc", obj_token,
             "--doc-format", "markdown",
             "--as", "user"],
            capture_output=True, text=True, timeout=30
        )
        if doc_result.returncode != 0:
            print(f"  [WARN] docs fetch失败 {title}: {doc_result.stderr[:150]}")
            return None, None

        data = json.loads(doc_result.stdout)
        if not data.get("ok"):
            print(f"  [WARN] API返回错误 {title}: {data.get('error', {})}")
            return None, None

        content = data.get("data", {}).get("document", {}).get("content", "")
        if not content or len(content) < 100:
            print(f"  [WARN] 内容过短 {title}: {len(content)} 字")
            return None, None

        # 补个头部信息
        md_content = f"# {title}\n\n"
        md_content += f"> 作者：土狗和他的朋友们\n\n"
        md_content += f"原文链接：{url}\n\n"
        md_content += "---\n\n"
        md_content += content.strip()

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return md_content, str(md_path)

    except Exception as e:
        print(f"  [WARN] 抓取异常 {title}: {e}")
        return None, None


# ============================================================
# IMA 知识库上传
# ============================================================
def ima_api_call(api_path, body_json, retries=3):
    """调用 IMA API，返回解析后的 JSON。失败自动重试3次，指数退避。

    2026-11-26：ima_api.cjs 每天第一次调用会先做 skill 版本检查，发现有新版就把
    「更新提示」JSON 打到 stdout 并退出 —— 那个对象里**没有 code 字段**，早先版本
    会把它当成业务错误，日志里天天出现 `[RETRY] IMA API 第1次失败（code=None
    msg=None）`。这里识别出来：把提示里的 latest_version 塞进 IMA_SKILL_VERSION
    立刻重跑一次（不是失败，不 sleep、不记 RETRY）。
    """
    cmd = ["node", str(IMA_API_SCRIPT), api_path, json.dumps(body_json, ensure_ascii=False)]
    last_err = None
    for attempt in range(retries):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            try:
                resp = json.loads(result.stdout)
            except json.JSONDecodeError:
                last_err = f"响应解析失败: {result.stdout[:200]}"
                resp = None

            if resp is not None:
                # code==0 成功，直接返回
                if resp.get("code") == 0:
                    return resp
                # 版本检查提示（无 code 字段，带 latest_version）：带上版本号立刻重跑
                if "code" not in resp and resp.get("latest_version"):
                    latest = str(resp["latest_version"])
                    if latest != os.environ.get("IMA_SKILL_VERSION"):
                        os.environ["IMA_SKILL_VERSION"] = latest
                        print(f"  [INFO] IMA skill 有新版本 {latest}，按该版本重跑（已设 IMA_SKILL_VERSION）")
                        continue
                # 有明确业务错误码，重试（IMA偶发限流/抖动）
                last_err = f"code={resp.get('code')} msg={resp.get('msg')}"

        except subprocess.TimeoutExpired:
            last_err = "请求超时(60s)"
        except Exception as e:
            last_err = str(e)

        if attempt < retries - 1:
            wait = 3 * (attempt + 1)  # 3s, 6s
            print(f"  [RETRY] IMA API 第{attempt+1}次失败（{last_err}），{wait}s后重试...")
            time.sleep(wait)

    print(f"  [ERROR] IMA API 重试{retries}次均失败: {last_err}")
    return {"code": -1, "msg": last_err or "unknown error"}


def resolve_kb_id(name=KB_NAME):
    """按知识库名字查询最新的 kb_id。IMA 升级后旧 ID 会失效，必须动态获取。"""
    resp = ima_api_call("openapi/wiki/v1/search_knowledge_base", {
        "query": name, "cursor": "", "limit": 20,
    })
    if resp.get("code") != 0:
        print(f"  [WARN] 查询知识库ID失败，使用兜底ID: {resp.get('msg')}")
        return KB_ID
    for kb in resp.get("data", {}).get("info_list", []):
        if kb.get("kb_name") == name:
            return kb.get("kb_id")
    print(f"  [WARN] 未找到知识库「{name}」，使用兜底ID")
    return KB_ID


def upload_to_ima(title, file_path, kb_id=KB_ID):
    """上传 markdown 文件到 IMA 知识库，返回 media_id 或 None"""
    file_name = f"{title}.md"
    file_size = os.path.getsize(file_path)

    # 1. 检查重名
    check_resp = ima_api_call("openapi/wiki/v1/check_repeated_names", {
        "params": [{"name": file_name, "media_type": 7}],
        "knowledge_base_id": kb_id,
    })
    if check_resp.get("code") != 0:
        print(f"  [ERROR] 重名检查失败: {check_resp.get('msg')}")
        return None

    results = check_resp.get("data", {}).get("results", [])
    if results and results[0].get("is_repeated"):
        print(f"  [SKIP] 文件已存在: {file_name}")
        return "exists"

    # 2. 创建媒体
    create_resp = ima_api_call("openapi/wiki/v1/create_media", {
        "file_name": file_name,
        "file_size": file_size,
        "content_type": "text/markdown",
        "knowledge_base_id": kb_id,
        "file_ext": "md",
    })
    if create_resp.get("code") != 0:
        print(f"  [ERROR] 创建媒体失败: {create_resp.get('msg')}")
        return None

    media_id = create_resp["data"]["media_id"]
    cos = create_resp["data"]["cos_credential"]

    # 3. COS 上传
    cos_script = IMA_SKILL_DIR / "knowledge-base" / "scripts" / "cos-upload.cjs"
    cos_cmd = [
        "node", str(cos_script),
        "--file", file_path,
        "--secret-id", cos["secret_id"],
        "--secret-key", cos["secret_key"],
        "--token", cos["token"],
        "--bucket", cos["bucket_name"],
        "--region", cos["region"],
        "--cos-key", cos["cos_key"],
        "--content-type", "text/markdown",
        "--start-time", str(cos["start_time"]),
        "--expired-time", str(cos["expired_time"]),
        "--timeout", "120000",
    ]
    cos_result = subprocess.run(cos_cmd, capture_output=True, text=True, timeout=180)
    if cos_result.returncode != 0:
        print(f"  [ERROR] COS上传失败: {cos_result.stderr[:200] or cos_result.stdout[:200]}")
        return None

    # 4. 添加知识
    add_resp = ima_api_call("openapi/wiki/v1/add_knowledge", {
        "media_type": 7,
        "media_id": media_id,
        "title": file_name,
        "knowledge_base_id": kb_id,
        "file_info": {
            "cos_key": cos["cos_key"],
            "file_size": file_size,
            "file_name": file_name,
        },
    })
    if add_resp.get("code") != 0:
        print(f"  [ERROR] 添加知识失败: {add_resp.get('msg')}")
        return None

    return media_id


# ============================================================
# 主流程
# ============================================================
def run_archive(mode="daily"):
    state = load_state()

    # 动态解析最新知识库ID（IMA升级后ID会变，不能写死）
    kb_id = resolve_kb_id()
    print(f"[INFO] 目标知识库: {KB_NAME} ({kb_id})")

    if mode == "daily":
        import datetime
        today = datetime.date.today().strftime("%Y-%m-%d")
        start_time = f"{today} 00:00:00"
        end_time = f"{today} 23:59:59"
        print(f"[INFO] 每日增量模式: {today}")
        articles = fetch_messages(start_time=start_time, end_time=end_time)
    else:
        print("[INFO] 全量历史归档模式")
        articles = fetch_messages()

    print(f"[INFO] 找到 {len(articles)} 篇第三类文章")

    success = 0
    skipped = 0
    failed = 0

    for i, art in enumerate(articles, 1):
        url = art["url"]
        title = art["title"]
        date_str = art["date"]

        # 去重
        if is_uploaded(url, state):
            skipped += 1
            continue

        # 未命名页面跳过
        if "未命名页面" in title:
            skipped += 1
            continue

        file_title = f"{date_str.replace('/', '')}_{title}"
        print(f"[{i}/{len(articles)}] {date_str} | {art['type']} | {title}")

        # 抓取正文
        content, md_path = fetch_article_content(url, file_title)
        if not content or not md_path:
            failed += 1
            continue

        # 上传到 IMA
        media_id = upload_to_ima(file_title, md_path, kb_id=kb_id)
        if media_id:
            mark_uploaded(url, title, media_id, state)
            success += 1
            print(f"  [OK] 上传成功")
        else:
            failed += 1

        save_state(state)
        time.sleep(0.5)

    state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    print(f"\n[完成] 成功: {success} | 跳过: {skipped} | 失败: {failed}")
    return success, skipped, failed


def main():
    parser = argparse.ArgumentParser(description="沧海一土狗文章归档到IMA知识库")
    parser.add_argument("--mode", choices=["full", "daily"], default="daily",
                        help="归档模式：full=全量历史，daily=当日增量")
    args = parser.parse_args()
    run_archive(mode=args.mode)


if __name__ == "__main__":
    main()
