# -*- coding: utf-8 -*-
"""emit 阶段：写指标模板 out/latest.json

默认用 builtin=emit + case/_lib/extract.py 自动抽 json 标量；
要精确控制指标时，把 stage 换成 {"name":"emit","cmd":"python3 -u emit.py"}，
本脚本按契约写 out/latest.json。落库由 publish.py 负责，这里只管写文件。
"""
import json
import os

OUT = os.environ.get("CASE_OUT_DIR") or "out"


def main():
    os.makedirs(OUT, exist_ok=True)
    payload = {
        "as_of": "2026-11-24",
        "points": [
            {"key": "demo.value", "sec": "CN.GENERIC", "obs_date": "2026-11-24",
             "value": 1.0, "dec": 2, "quality": "ok", "name": "模板指标"}
        ],
    }
    with open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("指标模板已写：out/latest.json（1 条）")


if __name__ == "__main__":
    main()
