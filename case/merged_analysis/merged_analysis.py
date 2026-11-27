#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并分析脚本 —— 土狗 + 将军 合并为一张飞书卡片
=================================================
策略：
1. 优先检查 SQLite 缓存是否已有当天数据
2. 如果有，直接合并推送（秒级完成）
3. 如果缺少，用 subprocess 运行对应脚本采集数据，再合并推送

用法:
    python merged_analysis.py [trade_date]
    python merged_analysis.py --force 20260803
    python merged_analysis.py --skip-push 20260803
"""

from __future__ import annotations
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
import os
import argparse
import asyncio
import subprocess
import sqlite3
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional

# ===== 路径 =====
BASE_DIR = Path(_cp("/Coze/Drive/蓁宝/所有对话/主对话"))
TUGOU_SCRIPT = BASE_DIR / "土狗分析" / "tugou_analysis.py"
GENERAL_SCRIPT = BASE_DIR / "将军分析" / "general_analysis.py"
TUGOU_DB = BASE_DIR / "codeact" / "output" / "tugou_state.db"
GENERAL_DB = BASE_DIR / "codeact" / "output" / "general_state.db"

CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"

# 导入飞书卡片工具
sys.path.insert(0, str(BASE_DIR))
from feishu_card_utils import build_card_header, build_hr, build_note, build_markdown, wrap_card
from feishu_push import push_card_to_feishu


def _resolve_trade_date(trade_date: Optional[str] = None) -> str:
    """确定交易日期，默认最近工作日"""
    if trade_date:
        return trade_date
    td = datetime.now()
    for i in range(7):
        check_date = (td - timedelta(days=i)).strftime("%Y%m%d")
        check_td = datetime.strptime(check_date, "%Y%m%d")
        if check_td.weekday() < 5:
            return check_date
    return datetime.now().strftime("%Y%m%d")


def _has_cached_data(db_path: Path, table_name: str, trade_date: str) -> bool:
    """检查缓存中是否有当天数据"""
    try:
        if not db_path.exists():
            return False
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            f"SELECT 1 FROM {table_name} WHERE trade_date=?", (trade_date,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _read_card_json(db_path: Path, table_name: str, trade_date: str) -> Optional[str]:
    """从 SQLite 读取 card_json"""
    try:
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            f"SELECT card_json FROM {table_name} WHERE trade_date=?", (trade_date,)
        ).fetchone()
        conn.close()
        if row:
            return row[0]
        return None
    except Exception as e:
        print(f"  ❌ 读取 {db_path} 失败: {e}")
        return None


def _run_script(script_path: Path, trade_date: str, force: bool = False) -> bool:
    """用 subprocess 运行子分析脚本（--skip-push + --no-submit 模式，避免重复推送和重复 submit_result）"""
    cmd = [sys.executable, str(script_path), trade_date, "--skip-push", "--no-submit"]
    if force:
        cmd.append("--force")

    name = script_path.stem
    print(f"  ▶ {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=540,
            cwd=str(BASE_DIR),
        )
        for line in proc.stdout.split('\n'):
            stripped = line.strip()
            if any(m in stripped for m in ['✅', '❌', '⚠️', '📊', '📤']):
                print(f"    {stripped}")

        if proc.returncode != 0:
            print(f"  ❌ {name} 退出码={proc.returncode}")
            if proc.stderr:
                print(f"    stderr: {proc.stderr[-500:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"  ❌ {name} 超时(540s)")
        return False
    except Exception as e:
        print(f"  ❌ {name} 异常: {e}")
        return False


def _mark_pushed(db_path: Path, table_name: str, trade_date: str):
    """标记已推送"""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            f"UPDATE {table_name} SET pushed=1, updated_at=? WHERE trade_date=?",
            (datetime.now().isoformat(), trade_date)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠️ 标记推送失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="合并分析：土狗+将军 → 一张飞书卡片")
    parser.add_argument("trade_date", nargs="?", default=None,
                        help="交易日期 YYYYMMDD，默认最近交易日")
    parser.add_argument("--force", action="store_true",
                        help="强制重新采集数据（忽略缓存）")
    parser.add_argument("--skip-push", action="store_true",
                        help="跳过飞书推送")
    args = parser.parse_args()

    trade_date = _resolve_trade_date(args.trade_date)
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

    print(f"\n{'='*60}")
    print(f"📊 合并分析日报 | 土狗框架 + 将军框架 | {date_str}")
    print(f"{'='*60}\n")

    # ===== 检查缓存 & 按需运行子脚本 =====
    tugou_cached = not args.force and _has_cached_data(TUGOU_DB, "tugou_daily_cache", trade_date)
    general_cached = not args.force and _has_cached_data(GENERAL_DB, "general_daily_cache", trade_date)

    if not tugou_cached:
        print(f"🐕 [1/4] 运行土狗分析（无缓存或--force）")
        tugou_ok = _run_script(TUGOU_SCRIPT, trade_date, force=True)
        print()
    else:
        print(f"🐕 [1/4] 土狗分析: 缓存命中，跳过采集 ✅\n")
        tugou_ok = True

    if not general_cached:
        print(f"⚔️ [2/4] 运行将军分析（无缓存或--force）")
        general_ok = _run_script(GENERAL_SCRIPT, trade_date, force=True)
        print()
    else:
        print(f"⚔️ [2/4] 将军分析: 缓存命中，跳过采集 ✅\n")
        general_ok = True

    if not tugou_ok and not general_ok:
        print(f"❌ 两个分析都失败，中止")
        _submit_result("notify", f"合并分析 {date_str}: 土狗和将军分析均失败", status="error")
        return

    # ===== 读取 card_json 并合并 =====
    print(f"📋 [3/4] 合并卡片")

    tugou_card_json = _read_card_json(TUGOU_DB, "tugou_daily_cache", trade_date)
    general_card_json = _read_card_json(GENERAL_DB, "general_daily_cache", trade_date)

    tugou_elements = []
    general_elements = []

    if tugou_card_json:
        tugou_card = json.loads(tugou_card_json)
        tugou_elements = tugou_card.get("elements", [])
        print(f"  ✅ 土狗: {len(tugou_elements)} elements")
    else:
        print(f"  ⚠️ 土狗 card_json 缺失" + (" (采集失败)" if not tugou_ok else ""))

    if general_card_json:
        general_card = json.loads(general_card_json)
        general_elements = general_card.get("elements", [])
        print(f"  ✅ 将军: {len(general_elements)} elements")
    else:
        print(f"  ⚠️ 将军 card_json 缺失" + (" (采集失败)" if not general_ok else ""))

    # 合并 elements，加上框架标签
    merged = []
    if tugou_elements:
        merged.append(build_markdown("**━━━ 🐕 沧海一土狗框架 ━━━**"))
        merged.extend(tugou_elements)
    if tugou_elements and general_elements:
        merged.append(build_hr())
    if general_elements:
        merged.append(build_markdown("**━━━ ⚔️ 将军投顾框架 ━━━**"))
        merged.extend(general_elements)

    # 底部 note
    merged.append(build_hr())
    merged.append(build_note("数据来源: AKShare/Tushare/央行官网 | 仅供参考"))

    # 构建 header
    subtitle = f"沧海一土狗 × 将军投顾 | {date_str}"
    header = build_card_header("📊 每日市场分析（土狗+将军）", subtitle, "blue")
    merged_card = wrap_card(header, merged)

    total = len(merged)
    print(f"  ✅ 合并完成: {total} elements")

    # ===== 推送 =====
    if not args.skip_push:
        print(f"\n📤 [4/4] 推送合并卡片到飞书群...")
        ok = push_card_to_feishu(card_dict=merged_card, chat_id=CHAT_ID)
        if ok:
            print("✅ 推送成功")
            _mark_pushed(TUGOU_DB, "tugou_daily_cache", trade_date)
            _mark_pushed(GENERAL_DB, "general_daily_cache", trade_date)
            _submit_result("display_only", f"📊 合并分析 {date_str}: 土狗+将军合并卡片已推送飞书群")
        else:
            print("❌ 推送失败")
            _submit_result("notify", f"合并分析 {date_str}: 推送飞书失败", status="error")
    else:
        print(f"\n⏭️ [4/4] 跳过推送（--skip-push）")
        _submit_result("display_only", f"合并分析 {date_str}: 合并完成（未推送）")


def _submit_result(result_mode: str, message: str, status: str = "success"):
    """通过 CodeAct SDK 提交结果"""
    try:
        sys.path.insert(0, str(BASE_DIR / "codeact" / "scripts"))
        from codeact_sdk import CodeActSDK
        sdk = CodeActSDK()
        asyncio.run(sdk.submit_result(
            status=status,
            result_mode=result_mode,
            message=message,
            data={},
        ))
    except Exception as e:
        print(f"(SDK: {e})")
        print(message)


if __name__ == '__main__':
    main()
