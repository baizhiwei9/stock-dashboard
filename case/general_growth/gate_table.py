# -*- coding: utf-8 -*-
"""报告“全市场收敛对照表 + 重点行业核查”硬校验：每次推送前必须通过，不满足即中止推送

检查项：
1) config.table 行数必须等于 config.universe.条件全过；每行含 名称/口径/拓扑/邻居数/净利TTM/PE_TTM/成交额/指数级缠论/全链结论
2) config.focus_check 必须覆盖机器人/AI硬件/存储/光模块，且每行有“剔除层级 + 剔除原因”
3) 合成图底部必须检出结构色标表格（聚集绿底＋扩散红底像素）

用法: python3 gate_table.py [--task-dir DIR] [--png PNG]   # 不通过时退出码 1
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

REQ = ["name", "kind", "judge", "neighbors", "g_ttm", "pe_ttm", "amt5",
       "chan_verdict", "chan_pos", "chain_pass"]
FOCUS = ["机器人", "AI硬件", "存储", "光模块"]
GREEN_FILL = (228, 245, 233)
RED_FILL = (253, 231, 234)


def check(cfg, png, task_dir):
    problems = []
    tbl = cfg.get("table") or []
    un = cfg.get("universe") or {}
    if un.get("条件全过") and len(tbl) != un["条件全过"]:
        problems.append("对照表行数=%d（应为 %s）" % (len(tbl), un["条件全过"]))
    if len(tbl) < 5:
        problems.append("对照表行数过少：%d" % len(tbl))
    for r in tbl:
        for f in REQ:
            if f not in r:
                problems.append("行 %s 缺字段 %s" % (r.get("name", "?"), f))
        if r.get("judge") not in ("聚集", "扩散", "—"):
            problems.append("行 %s 结构判定非法：%s" % (r.get("name"), r.get("judge")))
    if not any(r.get("chain_pass") for r in tbl):
        problems.append("对照表无任何“全链通过”对象")
    for d in cfg.get("directions", []):
        for s in d.get("stocks", []):
            lb = (s.get("chan") or {}).get("最近买点") or ""
            pos = (s.get("chan") or {}).get("位置") or ""
            if not lb:
                problems.append("个股 %s 缺缠论标注" % s.get("name"))
            if "三买" in lb and pos != "突破中枢":
                problems.append("个股 %s 标注不自洽：三买但状态为 %s" % (s.get("name"), pos))
            if "二买" in lb and pos == "跌破中枢":
                problems.append("个股 %s 标注不自洽：二买但已跌破中枢" % s.get("name"))
    fc = cfg.get("focus_check") or []
    for f in FOCUS:
        rows = [x for x in fc if f in (x.get("focus") or "")]
        if not rows:
            problems.append("重点行业核查缺 %s" % f)
        for x in rows:
            if not x.get("layer"):
                problems.append("重点行业 %s 缺剔除层级" % x.get("name"))
            if not x.get("reason"):
                problems.append("重点行业 %s 缺剔除原因" % x.get("name"))
    charts = cfg.get("_charts") or ([png] if png else [])
    if not charts:
        problems.append("未找到合成图清单（data/charts.json 为空）")
    nstock = sum(len(d.get("stocks") or []) for d in cfg.get("directions", []))
    want = max(1, -(-nstock // int(cfg.get("max_stocks_per_chart", 20) or 20)))
    if len(charts) != want:
        problems.append("分图数=%d，应为 %d（%d 只个股 ÷ 每图 %s 只）"
                        % (len(charts), want, nstock, cfg.get("max_stocks_per_chart", 20)))
    for pth in charts:
        if not pth or not os.path.exists(pth):
            problems.append("合成图不存在：%s" % pth)
            continue
        a = np.asarray(Image.open(pth).convert("RGB")).astype(int)
        h = a.shape[0]
        band = a[int(0.50 * h):int(0.995 * h)]

        def cnt(t):
            return int((np.abs(band - np.array(t)).sum(axis=2) < 30).sum())

        g, rd = cnt(GREEN_FILL), cnt(RED_FILL)
        if g < 5000 or rd < 5000:
            problems.append("%s 未检出结构色标对照表（浅色底 1=%d、底 2=%d 像素）"
                            % (os.path.basename(pth), g, rd))
        else:
            print("%s 结构表底纹像素：%d / %d" % (os.path.basename(pth), g, rd))
    n_st = sum(len(d.get("stocks") or []) for d in cfg.get("directions", []))
    pp = os.path.join(task_dir, "data", "chart_plan.json")
    if not os.path.exists(pp):
        problems.append("缺 data/chart_plan.json：分图顺序未由链路生成")
    else:
        pl = json.load(open(pp, encoding="utf-8"))
        ranks = [x.get("rank") for x in (pl.get("order") or [])]
        if ranks != sorted(ranks, reverse=True):
            problems.append("分图计划未按买点等级降序排列")
        if int(pl.get("n_images") or 0) != len(charts):
            problems.append("计划分图数=%s 与实际图数=%d 不一致" % (pl.get("n_images"), len(charts)))
        if any((c or 0) > int(pl.get("cap") or 0) for c in (pl.get("per_image") or [])):
            problems.append("单图只数超过上限 %s" % pl.get("cap"))
        if sum(pl.get("per_image") or []) != n_st:
            problems.append("分图计划只数合计 %s 与入选个股数 %d 不一致"
                            % (sum(pl.get("per_image") or []), n_st))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--png", default=None)
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(a.task_dir, "config.json"), encoding="utf-8"))
    cj = os.path.join(a.task_dir, "data", "charts.json")
    if os.path.exists(cj):
        cfg["_charts"] = (json.load(open(cj, encoding="utf-8")) or {}).get("charts") or []
    png = a.png or (cfg["_charts"][0] if cfg.get("_charts") else None)
    if a.png:
        cfg["_charts"] = [a.png]
    problems = check(cfg, png, a.task_dir)
    if problems:
        print("[FAIL] 报告校验未通过，共 %d 项：" % len(problems))
        for p in problems:
            print("  · " + p)
        sys.exit(1)
    pl = json.load(open(os.path.join(a.task_dir, "data", "chart_plan.json"), encoding="utf-8"))
    print("[PASS] 报告校验通过：对照表 %d 行、重点行业核查 %d 行、结构色标齐备；"
          "分图计划 %s 张（每图 %s 只、单图上限 %s，按买点等级降序）"
          % (len(cfg["table"]), len(cfg.get("focus_check") or []),
             pl.get("n_images"), pl.get("per_image"), pl.get("cap")))


if __name__ == "__main__":
    main()