# -*- coding: utf-8 -*-
"""定稿「行业清单 + 绿红标注 + 行业级缠论过滤」口径，写入 config.json

- industry_filter：行业级缠论过滤的判据与指数映射
- chain.steps：把 ④~⑦ 更新为「扩散+估值匹配 → 行业级缠论过滤 → 行业内选股 → 个股级缠论过滤」
用法: python3 build_criteria.py [--task-dir DIR]
"""
import argparse
import json
import os

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    p = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(p, encoding="utf-8"))
    res = json.load(open(os.path.join(WS, "results.json"), encoding="utf-8"))
    nm2code = {x["name"]: x["code"] for x in res["industries"]}
    res2 = json.load(open(os.path.join(WS, "results.json"), encoding="utf-8"))
    nb = {x["name"]: x["neighbors"] for x in res2["industries"]}

    idx_map = {}
    for r in cfg["table"]:
        idx_map[r["direction"]] = [{"name": n, "code": nm2code.get(n), "neighbors": nb.get(n)}
                                   for n in r["indices"]]

    cfg["industry_filter"] = {
        "scope": "六个候选方向对应的申万三级行业指数，共 %d 个" % sum(len(v) for v in idx_map.values()),
        "indices": idx_map,
        "data": "TuShare 代理 sw_daily 日线 OHLCV，窗口 2025-01-01 起至最新交易日",
        "method": "缠论分型版（严格分型 → 笔 → 中枢；MACD 20/40/5）",
        "single_index_pass": "收盘不在最近中枢下方，或位于中枢下方但出现一买（B1 底背驰）",
        "direction_pass": "该方向下通过指数个数占比 ≥ 50%（多数决；单指数方向按该指数）",
        "usage": "绿＝资金聚集、红＝资金扩散；缠论过滤用于判断该行业当前是否具备买卖交易价值；"
                 "最终入选行业＝扩散（红）且行业级缠论通过。",
    }

    steps = cfg.get("chain", {}).get("steps", [])
    head = steps[:4]
    tail = [
        "④ 行业级缠论过滤（新增）：对六个方向各自对应的申万三级行业指数取日线 OHLCV，"
        "跑缠论分型版，要求指数“收盘不在最近中枢下方，或位于中枢下方但出现一买（B1）”；"
        "方向内有多个指数时按多数决（通过占比 ≥ 50%）判定该行业是否有买卖交易价值。",
        "⑤ 行业内选股：在经过行业级缠论过滤且属于扩散的方向内，按卖方一致预期 2027E 净利增速"
        "排序取前二，并列时取 2027E PE 更低者。",
        "⑥ 个股级缠论过滤：对这 6 只逐只做同样的缠论判定，不满足者用同行业下一顺位候选替换并复检。",
    ]
    cfg["chain"]["steps"] = head + tail
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("行业级缠论过滤口径已写入 config.industry_filter")
    for d, lst in idx_map.items():
        print("  %-14s %s" % (d, "、".join("%s(%s)" % (x["name"], x["code"]) for x in lst)))
    print("chain.steps 现有 %d 步" % len(cfg["chain"]["steps"]))
    for s in cfg["chain"]["steps"]:
        print("   " + s[:64] + ("…" if len(s) > 64 else ""))


if __name__ == "__main__":
    main()