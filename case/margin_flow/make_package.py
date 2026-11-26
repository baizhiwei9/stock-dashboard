#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包「市场融资风险扫描日报」自动化工程为【单个整体压缩包】。

工程由两部分组成，须同时交付：
  - margin_monitor/     主链路（取数 → 计算 → 报告 → 飞书/邮件推送），入口 run_daily.py
  - etf_macd_monitor/   栏目四引擎（ETF 存量资金 MACD），入口 run_chain.py
                       运行时由主链路以相对路径 ../etf_macd_monitor/run_chain.py 调用

包内两目录位于压缩包根同级，解压后相对路径即可正常工作。
排除运行期产物：data/、__pycache__/、*.log、*.pyc/*.pyo，以及一次性探针文件 _probe_markers.py/_probe_markers.png。
同时把本轮重新生成的交付物一并入包（按基准日精确取，避免纳入误跑产物）：
  - 市场融资风险扫描日报_两融专项_<基准日>.md    （报告A：两融统计汇总表 + 两融三项栏目）
  - 市场融资风险扫描日报_资金流与宽度_<基准日>.md（报告B：ETF资金流/市场宽度/存量资金MACD）
  - 市场融资风险扫描日报_合并图_<基准日>.png      （全部宽基全部子图合成的大图）
沿用原包名原地更新：市场融资风险扫描日报_自动化工程.zip（落在工作目录根，与两目录同级）。

用法：python3 make_package.py
"""
import os
import json
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))          # .../margin_monitor
WORK = os.path.dirname(HERE)                                # 工作目录根
PKG_NAME = "市场融资风险扫描日报_自动化工程.zip"
OUT = os.path.join(WORK, PKG_NAME)
DIRS = ["margin_monitor", "etf_macd_monitor"]
EXCLUDE_DIRS = {"data", "__pycache__"}
EXCLUDE_EXT = {".log", ".pyc", ".pyo"}
EXCLUDE_FILES = {"_probe_markers.py", "_probe_markers.png",   # 一次性探针，不入包
                 "build_report.py.bak"}                        # 改造前备份，不入包
REPORT_NAME = "市场融资风险扫描日报"
FILE_MARGIN = "两融专项"                                      # 报告A 文件名中段
FILE_OTHER = "资金流与宽度"                                    # 报告B 文件名中段


def _iter_files(root):
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in sorted(files):
            if fn in EXCLUDE_FILES:
                continue
            if os.path.splitext(fn)[1].lower() in EXCLUDE_EXT:
                continue
            yield os.path.join(cur, fn)


def _extra_files():
    """按基准日精确取两份报告正文与合并大图，避免把误跑产物打进包。"""
    out = []
    try:
        mm = json.load(open(os.path.join(HERE, "data", "margin_module.json"), encoding="utf-8"))
        base = str(mm.get("last_date"))
    except Exception as e:                                  # noqa: BLE001
        print(f"提示：未能确定基准日，跳过报告/合并图入包：{e}")
        return out
    wanted = [
        f"{REPORT_NAME}_{FILE_MARGIN}_{base}.md",
        f"{REPORT_NAME}_{FILE_OTHER}_{base}.md",
        f"{REPORT_NAME}_合并图_{base}.png",
    ]
    for fn in wanted:
        p = os.path.join(WORK, fn)
        if os.path.exists(p):
            out.append(p)
        else:
            print(f"提示：缺少交付物 {fn}，本次不入包")
    print(f"基准日 {base}，附加入包 {len(out)}/{len(wanted)} 个交付物")
    return out


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    n = 0
    seen = set()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for d in DIRS:
            base = os.path.join(WORK, d)
            if not os.path.isdir(base):
                raise SystemExit(f"缺少目录：{base}")
            for p in _iter_files(base):
                arc = os.path.relpath(p, WORK)      # margin_monitor/xxx.py / etf_macd_monitor/xxx.py
                z.write(p, arc)
                seen.add(arc)
                n += 1
        for p in _extra_files():                    # 报告 md + 合并图 png（根级）
            arc = os.path.relpath(p, WORK)
            if arc in seen:
                continue
            z.write(p, arc)
            seen.add(arc)
            n += 1
    print(f"已打包 {n} 个文件 -> {OUT}（{os.path.getsize(OUT)} B）")
    with zipfile.ZipFile(OUT) as z:
        for name in z.namelist():
            print("  ", name)


if __name__ == "__main__":
    main()