#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五策略日报（XGBoost/TCN/LSTM+XGB/TxDIF/缠论）→ 结论 + 风险标签 落盘

分工
────
  任务侧（本模块）：拿各策略自己的 signal 原样汇总 → 判断偏多/偏空 → 打四档标签
  看板侧：只读 metric_points.risk_level，不重算
  通知侧：只读标签转发

判断口径（唯一来源，改这里就全改）
────────────────────────────────
  五个策略各自给 -1/0/1，缠论取 consensus_score 的符号（|score|>=1 才计入）。
  有效票数 n（不含 0/None）：
    偏空票 >= 4 且 多于偏多票  → risk
    偏多票 >= 4 且 多于偏空票  → opportunity
    其余（含分歧、票数不足）    → none
  即：只有「≥4 个策略同向」才算值得关注的结论，三票及以下一律 none。
  这是任务自己的判断，不是看板二次推断。

用法
────
    python3 strategies/five_strategy_tags.py --from-log        # 从已有日报日志回填
    python3 strategies/five_strategy_tags.py --print           # 只看结果不写

产物
────
    strategies/daily_reports/five_strategy_YYYY-MM-DD.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(HERE, "daily_reports")
LOG_PATH = "/var/log/xgboost_daily.log"

# 名称 → 板块代码（与 daily_report.py 的 INDEX_MAP/FUTURES_MAP/HK_INDEX_MAP 一致）
NAME_CODE = {
    "沪深300": "000300", "中证500": "000905", "中证1000": "000852",
    "创业板指": "399006", "科创50": "000688",
    "5年国债": "TF", "30年国债": "TL",
    "恒生指数": "HSI", "恒生科技": "HSTECH",
}


def _dir_of(cell: str) -> Optional[int]:
    """表格单元格 → -1/0/1；N/A 返回 None。"""
    s = (cell or "").strip()
    if not s or s.upper() == "N/A":
        return None
    if "多" in s:
        return 1
    if "空" in s:
        return -1
    if "中" in s or "分歧" in s:
        return 0
    return None


def _stars_of(cell: str) -> int:
    return (cell or "").count("⭐")


def judge(name: str, consensus: str, cells: List[str], cl_score: int = 0) -> Dict[str, Any]:
    """按五个策略的方向票数给出标签。cells = [xgb, tcn, lstm, txdif, chanlun]。"""
    votes: List[int] = []
    for c in cells[:4]:
        v = _dir_of(c)
        if v is not None:
            votes.append(v)
    if cl_score != 0:
        votes.append(1 if cl_score > 0 else -1)

    bull = sum(1 for v in votes if v > 0)
    bear = sum(1 for v in votes if v < 0)
    n = len(votes)
    score = bull - bear

    if bear >= 4 and bear > bull:
        level = "risk"
    elif bull >= 4 and bull > bear:
        level = "opportunity"
    else:
        level = "none"

    note = "%s 五策略共识「%s」：偏多 %d / 偏空 %d（有效票 %d）" % (
        name, consensus or "NA", bull, bear, n)
    if level == "risk":
        note += "｜≥4 票偏空，按任务自身口径记风险"
    elif level == "opportunity":
        note += "｜≥4 票偏多，按任务自身口径记机会"
    else:
        note += "｜同向票不足 4，不做方向标签"

    return {
        "name": name,
        "code": NAME_CODE.get(name, ""),
        "consensus": consensus or "NA",
        "bull": bull,
        "bear": bear,
        "n": n,
        "score": score,
        "stars": _stars_of(consensus),
        "risk_level": level,
        "risk_note": note,
    }


def build_items(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 daily_report.run_single_symbol() 的 results 直接构建（正常路径）。"""
    items: List[Dict[str, Any]] = []
    for r in results or []:
        if not r:
            continue
        cells = [
            "多" if r.get("xgb_signal") == 1 else ("空" if r.get("xgb_signal") == 0 else "N/A"),
            {1: "多", -1: "空", 0: "中"}.get(r.get("tcn_signal"), "N/A"),
            {1: "多", -1: "空", 0: "中"}.get(r.get("lstm_xgb_signal"), "N/A"),
            {1: "多", -1: "空", 0: "中"}.get(r.get("txdif_signal"), "N/A"),
        ]
        cl_score = int(r.get("chanlun_consensus_score") or 0)
        cells.append("多" if cl_score > 0 else ("空" if cl_score < 0 else "分歧"))
        consensus = _consensus_text(cells, cl_score)
        item = judge(str(r.get("index_name") or ""), consensus, cells, cl_score)
        item.update({
            "code": str(r.get("index_code") or item["code"]),
            "category": r.get("category") or "",
            "close": r.get("latest_close"),
        })
        items.append(item)
    return items


def _consensus_text(cells: List[str], cl_score: int) -> str:
    """与 daily_report.generate_report 的汇总表同一套规则。"""
    votes: List[int] = []
    for c in cells[:4]:
        v = _dir_of(c)
        if v is not None:
            votes.append(v)
    if cl_score != 0:
        votes.append(1 if cl_score > 0 else -1)
    if len(votes) == 5 and len(set(votes)) == 1:
        return "⭐⭐⭐⭐⭐多" if votes[0] == 1 else "⭐⭐⭐⭐⭐空"
    if len(votes) == 4 and len(set(votes)) == 1:
        return "⭐⭐⭐⭐多" if votes[0] == 1 else "⭐⭐⭐⭐空"
    if len(votes) == 3 and len(set(votes)) == 1:
        return "⭐⭐⭐多" if votes[0] == 1 else "⭐⭐⭐空"
    if len(votes) >= 2:
        bull = sum(1 for v in votes if v == 1)
        bear = sum(1 for v in votes if v == -1)
        if bull > bear:
            return "偏多(%d/%d)" % (bull, len(votes))
        if bear > bull:
            return "偏空(%d/%d)" % (bear, len(votes))
        return "分歧"
    return "N/A"


def parse_log(path: str = LOG_PATH) -> Dict[str, Any]:
    """从日报日志里取最近一份「五策略汇总」表（回填用，不重算信号）。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    idx = txt.rfind("## 📋 五策略汇总")
    if idx < 0:
        raise RuntimeError("日志里没有五策略汇总表")
    head = txt[:idx]
    dates = re.findall(r"\*\*📅 生成日期\*\*: *(\d{4}-\d{2}-\d{2})", head)
    data_dates = re.findall(r"\*\*📊 数据截止\*\*: *(\d{4}-\d{2}-\d{2})", head)
    date = dates[-1] if dates else datetime.now().strftime("%Y-%m-%d")
    data_date = data_dates[-1] if data_dates else ""

    items: List[Dict[str, Any]] = []
    for line in txt[idx:].splitlines():
        if not line.lstrip().startswith("|"):
            continue
        parts = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(parts) < 7 or parts[0] in ("品种",) or set(parts[0]) <= {"-"}:
            continue
        name, cells, consensus = parts[0], parts[1:5], parts[6]
        cl_cell = parts[5]
        cl_score = 0
        if "共振多" in cl_cell or "偏多" in cl_cell:
            cl_score = 2 if "共振" in cl_cell else 1
        elif "共振空" in cl_cell or "偏空" in cl_cell:
            cl_score = -2 if "共振" in cl_cell else -1
        item = judge(name, consensus, cells, cl_score)
        item["chanlun"] = cl_cell
        items.append(item)
    return {"date": date, "data_date": data_date, "source": "strategies/daily_report.py",
            "items": items}


def write_json(payload: Dict[str, Any], out_dir: str = REPORT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "five_strategy_%s.json" % payload["date"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="五策略日报结论打标落盘")
    ap.add_argument("--from-log", action="store_true", help="从日志回填最近一份汇总表")
    ap.add_argument("--print", action="store_true", dest="show", help="只打印不写文件")
    ap.add_argument("--log", default=LOG_PATH)
    a = ap.parse_args(argv)

    if not a.from_log:
        ap.error("当前只支持 --from-log（正常路径由 daily_report.py 直接调用 build_items）")

    payload = parse_log(a.log)
    if a.show:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    path = write_json(payload)
    tagged = [i for i in payload["items"] if i["risk_level"] != "none"]
    print("写入 %s：%d 个品种，带标签 %d 个" % (path, len(payload["items"]), len(tagged)))
    for i in tagged:
        print("  %-6s %-10s %s" % (i["risk_level"], i["name"], i["consensus"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
