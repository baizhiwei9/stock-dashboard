# -*- coding: utf-8 -*-
"""check 阶段（可选）：任务自己的校验

默认用 pipeline 里的 builtin=artifacts（框架通用校验）。
需要业务规则时把它换成 {"name":"check","cmd":"python3 -u check.py"}，
本脚本按契约写 out/check.json：{"ok": bool, "items":[{name,ok,detail,fatal}]}。
"""
import json
import os

OUT = os.environ.get("CASE_OUT_DIR") or "out"


def main():
    os.makedirs(OUT, exist_ok=True)
    res = {"ok": True, "items": [{"name": "业务规则", "ok": True,
                                  "detail": "模板默认通过", "fatal": True}]}
    with open(os.path.join(OUT, "check.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("自定义校验通过")


if __name__ == "__main__":
    main()
