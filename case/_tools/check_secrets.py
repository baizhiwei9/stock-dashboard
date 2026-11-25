#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_secrets — 安装/启动前的敏感配置门禁

用法
════════════════════════════════════════════════════════════════
    python3 case/_tools/check_secrets.py           # 校验（不打印任何值）
    python3 case/_tools/check_secrets.py --init    # 交互式补齐必填项
    python3 case/_tools/check_secrets.py --json    # 机器可读结果
    python3 case/_tools/check_secrets.py --env     # 输出 shell export

退出码：0 = 通过；1 = 缺必填项或权限过宽；2 = 用法错误。

约定
════════════════════════════════════════════════════════════════
· 敏感值只在 config/secrets.json；仓库存 config/secrets.example.json。
· 本工具**永远不回显值**，只报 key 名、文件路径和权限。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import app_secrets  # noqa: E402


def main(argv):
    argv = list(argv)
    if not argv:
        return app_secrets._cmd_check()
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "--init":
        app_secrets._cmd_init()
        return 0 if not app_secrets.missing(app_secrets.REQUIRED_KEYS) else 1
    if cmd == "--json":
        return app_secrets._cmd_check(as_json=True)
    if cmd == "--env":
        app_secrets._cmd_env()
        return 0
    if cmd == "--paths":
        app_secrets._cmd_paths()
        return 0
    print("未知参数：%s（支持 --init / --json / --env / --paths）" % cmd,
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
