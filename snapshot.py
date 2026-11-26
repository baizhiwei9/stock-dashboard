#!/usr/bin/env python3
"""snapshot — 把看板数据打成机器可读 JSON，给 Agent / 外部程序读。

页面是给人看的 HTML；Agent 要的是结构化的数。这里复用 build.py **同一套**
`data_hub.tech` 接口，口径与页面完全一致——不是另算一份，也不是爬自己的页面。

    python3 snapshot.py                          # 打到 stdout
    python3 snapshot.py --out snap.json          # 落文件
    python3 snapshot.py --codes 000300.SH        # 只看部分指数
    python3 snapshot.py --futures                # 额外带期货实时快照（联网，慢）
    python3 snapshot.py --no-option              # 跳过期权缓存

输出结构
--------
{
  "as_of": "20261124",
  "indices": {
    "000300.SH": {
      "name": "沪深300",
      "metrics": {"chanlun.xxx": {"value": 1, "name": "...", "unit": "",
                                   "direction": "higher_bullish", "as_of": "20261124",
                                   "quality": "ok"}},
      "option": {"atm_iv": 18.3, "atm_iv_pk": 68.0, "iv_skew": 2.1, ...}
    }
  },
  "futures": {"as_of": "...", "groups": [...]},     # 仅 --futures
  "coverage": {"n_indices": 5, "n_option": 5, "missing": ["000016.SH"]}
}

约定
----
· key 一律是**指数代码**，不是 ETF（指数与 ETF/期权在 registry 里配 pair）
· 期权 IV / Skew 已 ×100 转成 %；分位样本不足 60 个时为 null，不给假精确
· 库里没有的指标**不出现**在结果里，不补零、不拿旧值顶替
· 缺什么在 `coverage.missing` 里明说
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from datahub_path import datahub_root

DATAHUB_ROOT = datahub_root()
if DATAHUB_ROOT and DATAHUB_ROOT not in sys.path:
    sys.path.insert(0, DATAHUB_ROOT)

from data_hub.tech import (                                    # noqa: E402
    INDEX_ORDER, get_def, list_metrics, opt_asof, opt_snapshot,
    read_latest, security_name,
)


def _f(v):
    """安全转 float：None / NaN / 非数字一律 None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def index_metrics(codes):
    """{code: {name, metrics}}。只回有定稿点的指标。"""
    keys = list_metrics()
    if not keys:
        return {}
    defs = {}
    for k in keys:
        try:
            defs[k] = get_def(k)
        except Exception:                                       # noqa: BLE001
            defs[k] = None
    got = read_latest(keys, codes)
    out = {}
    for (metric_key, sid), row in got.items():
        if sid not in codes:
            continue
        d = defs.get(metric_key)
        val = _f(row.get("value_num"))
        out.setdefault(sid, {"name": security_name(sid), "metrics": {}})
        out[sid]["metrics"][metric_key] = {
            "value": val if val is not None else row.get("value_text"),
            "name": getattr(d, "name", metric_key),
            "unit": getattr(d, "unit", "") or "",
            "direction": getattr(d, "direction", "") or "",
            "as_of": row.get("obs_date"),
            "quality": row.get("quality"),
        }
    return out


def build_snapshot(codes=None, with_option=True, with_futures=False):
    codes = [str(c).strip() for c in (codes or INDEX_ORDER) if str(c).strip()]
    snap = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hub": DATAHUB_ROOT, "indices": {}, "coverage": {}}

    snap["indices"] = index_metrics(codes)

    if with_option:
        try:
            opts = opt_snapshot()
            snap["option_as_of"] = opt_asof(opts)
            for code in codes:
                one = opts.get(code)
                if one:
                    snap["indices"].setdefault(
                        code, {"name": security_name(code), "metrics": {}})["option"] = one
            snap["coverage"]["n_option"] = sum(1 for c in codes if c in opts)
        except Exception as e:                                  # noqa: BLE001
            snap["coverage"]["option_error"] = f"{type(e).__name__}: {e}"

    if with_futures:
        try:
            from data_hub.tech import fut_groups, fut_snapshot
            snap["futures"] = {"groups": fut_groups(), "rows": fut_snapshot()}
        except Exception as e:                                  # noqa: BLE001
            snap["coverage"]["futures_error"] = f"{type(e).__name__}: {e}"

    stamps = []
    for one in snap["indices"].values():
        for m in (one.get("metrics") or {}).values():
            if m.get("as_of"):
                stamps.append(str(m["as_of"]))
    snap["as_of"] = max(stamps) if stamps else None

    snap["coverage"]["n_indices"] = len(snap["indices"])
    snap["coverage"]["missing"] = [c for c in codes if c not in snap["indices"]]
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="看板数据的机器可读快照")
    ap.add_argument("--out", default="", help="输出文件路径，默认 stdout")
    ap.add_argument("--codes", default="", help="逗号分隔的指数代码，默认全部")
    ap.add_argument("--no-option", action="store_true", help="跳过期权缓存")
    ap.add_argument("--futures", action="store_true", help="额外带期货实时快照（联网，慢）")
    ap.add_argument("--indent", type=int, default=2)
    args = ap.parse_args(argv)

    codes = [c for c in args.codes.replace("|", ",").split(",") if c.strip()] or None
    snap = build_snapshot(codes, with_option=not args.no_option,
                          with_futures=args.futures)
    text = json.dumps(snap, ensure_ascii=False, indent=args.indent, default=str)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        cov = snap["coverage"]
        print("wrote %s | as_of=%s indices=%s option=%s missing=%s"
              % (args.out, snap.get("as_of"), cov.get("n_indices"),
                 cov.get("n_option", "-"), cov.get("missing") or "-"))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
