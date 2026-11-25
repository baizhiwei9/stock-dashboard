# -*- coding: utf-8 -*-
"""render 阶段：出报告，产物写 out/

契约：out/ 下有本次生成的报告（参考 pipeline.artifacts.report），退出码 0。
"""
import json
import os

DATA = os.environ.get("CASE_DATA_DIR") or "data"
OUT = os.environ.get("CASE_OUT_DIR") or "out"


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(DATA, "sample.json"), encoding="utf-8") as f:
        d = json.load(f)
    lines = ["# 模板任务报告", "", "- as_of：%s" % d.get("as_of"), "- 行数：%d" % len(d["rows"])]
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("报告已写：out/report.md")


if __name__ == "__main__":
    main()
