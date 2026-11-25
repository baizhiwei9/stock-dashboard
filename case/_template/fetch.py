# -*- coding: utf-8 -*-
"""fetch 阶段：取数，产物写 data/

契约：data/ 下至少一个非空文件，退出码 0。
框架注入 CASE_DATA_DIR / CASE_OUT_DIR，也可用相对路径 data/ out/。
"""
import json
import os

DATA = os.environ.get("CASE_DATA_DIR") or "data"


def main():
    os.makedirs(DATA, exist_ok=True)
    rows = [{"date": "2026-11-24", "value": 1.0}]          # ← 换成真实取数
    with open(os.path.join(DATA, "sample.json"), "w", encoding="utf-8") as f:
        json.dump({"as_of": "2026-11-24", "rows": rows}, f, ensure_ascii=False, indent=2)
    print("取数完成：%d 行" % len(rows))


if __name__ == "__main__":
    main()
