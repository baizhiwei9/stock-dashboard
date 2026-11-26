#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_snapshot — 把 tech 指标仓推成云盘 gzip 快照（调度任务 tech_snapshot 的入口）

为什么要单独一个脚本
────────────────────
``python3 -m data_hub.tech.store --snapshot`` 在快照失败时只打印
``快照: None`` 就 exit 0，调度器看到的永远是成功 —— 这次查出来的真问题就是
「快照从来没生成过，但没人发现」。这里把「有没有拿到路径」变成退出码：

    0  快照已落盘（打印绝对路径 + 大小）
    1  快照失败（store.snapshot() 返回 None，通常是活库不存在或云盘不可写）

落盘位置由 ``data_hub.tech.store`` 自己决定（``cloud_dir()`` +
``localdb.snapshot_path()``），本脚本**不写死任何文件名/目录**。

用法
────
    PYTHONPATH=<DataHub> python3 case/_tools/push_snapshot.py
    python3 case/_tools/push_snapshot.py --print   # 只打印目标路径，不推
"""
import argparse
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="推 tech 指标仓云盘快照")
    ap.add_argument("--print", dest="only_print", action="store_true",
                    help="只打印快照目标路径，不实际推送")
    a = ap.parse_args(argv)

    try:
        from data_hub.tech import store
    except Exception as e:                                     # noqa: BLE001
        print("无法 import data_hub.tech.store：%s: %s" % (type(e).__name__, e),
              file=sys.stderr)
        print("提示：PYTHONPATH 要指到 DataHub 根（含 data_hub/ 的那一层）", file=sys.stderr)
        return 1

    try:
        cloud = store.cloud_dir()
        name = store.DB_NAME
        from data_hub.core.localdb import snapshot_path
        target = snapshot_path(name, cloud)
    except Exception as e:                                     # noqa: BLE001
        print("解析快照路径失败：%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1

    if a.only_print:
        print(target)
        return 0

    try:
        got = store.snapshot()
    except Exception as e:                                     # noqa: BLE001
        print("快照异常：%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1

    if not got:
        print("快照失败：store.snapshot() 返回空（目标 %s）。"
              "常见原因：活库不存在、云盘目录不可写。" % target, file=sys.stderr)
        return 1
    if os.path.abspath(got) != os.path.abspath(target):
        print("快照落点与预期不一致：实际 %s，预期 %s" % (got, target), file=sys.stderr)
        return 1
    try:
        size = os.path.getsize(got)
    except OSError:
        size = 0
    print("快照已落盘：%s（%.1f MB）" % (got, size / 1048576.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
