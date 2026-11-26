#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手工入口：一次跑完整条链路（等价 runner 的 fetch→check→render→emit→dispatch）

option_daily 特有：``--mode full|smile`` 模拟 22:20 全量档 / 22:50·23:30 微笑档。

定时调度走 scheduler.py + task.json 的 pipeline，本文件只留作排障与手动补跑：

    python3 run_daily.py                # 今天（非交易日回退）
    python3 run_daily.py 2026-11-26     # 指定日
    python3 run_daily.py --no-publish   # 只写 out/latest.json，不落 DataHub
    python3 run_daily.py --only fetch   # 只跑某个阶段
    python3 run_daily.py --mode smile   # 只重算微笑/偏度（等价 --smile-only）

与 pipeline.py 是同一套钩子，不复制逻辑。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class _Ctx:
    """最小上下文：与 runner.Ctx 的字段名保持一致，钩子不用改就能手工跑。"""

    def __init__(self, key, task_dir, do_publish=True):
        self.key = key
        self.dir = task_dir
        self.data_dir = os.environ.get("CASE_DATA_DIR") or os.path.join(task_dir, "data")
        self.out_dir = os.environ.get("CASE_OUT_DIR") or os.path.join(task_dir, "out")
        self.do_publish = do_publish
        self.points = []
        self.warnings = []
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)

    def log(self, msg):
        print("[%s] %s" % (self.key, msg), flush=True)

    def warn(self, title, lines, level="warn"):
        self.warnings.append((title, list(lines or []), level))
        print("[WARN:%s] %s" % (level, title), flush=True)
        for ln in (lines or []):
            print("       " + str(ln), flush=True)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default="", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--only", default="", help="只跑这些阶段，逗号分隔")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--mode", default="", choices=("", "full", "smile"),
                    help="full=全量档 / smile=只重算微笑（等价原 --smile-only）")
    a = ap.parse_args(argv)

    if a.date:
        os.environ["MM_DATE"] = a.date.replace("-", "")
        os.environ["OD_DATE"] = a.date.replace("-", "")
    if a.mode:
        os.environ["OD_MODE"] = a.mode

    import pipeline
    ctx = _Ctx("option_daily", HERE, do_publish=not a.no_publish)
    stages = ["fetch", "render", "emit", "dispatch"]
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        stages = [s for s in stages if s in want]

    for st in stages:
        ctx.log("== %s ==" % st)
        fn = getattr(pipeline, st, None)
        if fn is None:
            ctx.log("跳过（pipeline 里没有 %s）" % st)
            continue
        try:
            ret = fn(ctx)
        except Exception as e:                                   # noqa: BLE001
            print("[FAIL] %s: %s: %s" % (st, type(e).__name__, e), file=sys.stderr)
            return 1
        if isinstance(ret, int) and ret != 0:
            print("[FAIL] %s 返回 %s" % (st, ret), file=sys.stderr)
            return ret
        if st == "emit":
            ctx.points = ret or []
        if st == "dispatch":
            continue
    ctx.log("完成。阶段=%s 指标=%d 告警=%d"
            % (",".join(stages), len(ctx.points), len(ctx.warnings)))

    # emit 的产物落 out/latest.json（与框架 _write_metrics 同一格式）
    if "emit" in stages and not a.no_publish:
        import json
        st = {}
        p = os.path.join(ctx.data_dir, "stage_render.json")
        if os.path.isfile(p):
            st = json.load(open(p, encoding="utf-8"))
        payload = {"as_of": st.get("as_of") or "", "task": ctx.key, "points": ctx.points}
        with open(os.path.join(ctx.out_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        ctx.log("已写 out/latest.json（%d 条）" % len(ctx.points))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
