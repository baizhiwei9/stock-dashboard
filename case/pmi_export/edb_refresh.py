# -*- coding: utf-8 -*-
"""EDB 统一刷新：报告链路的唯一联网入口

按 REPORT_CODES 刷新 data_hub EDB（EDB 内部按序列频率做 TTL 与增量，
过期才真正联网），刷新失败不阻断报告——后续脚本只读库内已有数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edb_bridge as edb


def main():
    rep = edb.update(edb.REPORT_CODES, force=False)
    ok = len(rep.get("refreshed") or [])
    fail = rep.get("failed") or {}
    print(f"[edb_refresh] 刷新成功 {ok} 项，失败 {len(fail)} 项")
    for k, v in list(fail.items())[:5]:
        print(f"  - {k}: {v}")
    # 刷新失败不算致命：库内有旧值时报告仍可生成
    return 0


if __name__ == "__main__":
    sys.exit(main())
