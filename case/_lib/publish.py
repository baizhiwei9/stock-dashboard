#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish.py — 把 case 任务的产出写入 DataHub tech 指标仓

数据链路：
    case/<key>/out/latest.json  →  publish.py  →  data_hub.tech 指标仓  →  build.py 渲染

写入契约以 DataHub `data_hub/tech/CONTRACT.md` 为准（v1.0）：

    from data_hub.tech import write_points, start_run, finish_run
    rows = [{"metric_key": "chanlun.bull_score", "security_id": "000300.SH",
             "obs_date": "2026-11-23", "value_num": 62.0,
             "quality": "ok", "meta_json": {...}}]
    n_ok, n_missing = write_points(rows, producer="case/<key>")

quality 只允许 ok / missing / stale / estimated；不传时 DataHub 按值自动推断。
算不出来写 missing，**不补零、不用旧值顶替**。

risk_level / risk_note 原样透传给 DataHub（标签由任务自己判断，publish 不重算）：
    risk_level 只允许 none / opportunity / risk / alert，兼容 warning→risk、严重→alert、
    机会→opportunity 等旧写法；未知档位直接报错，避免标签被静默丢掉。

任务侧 out/latest.json 允许简写（publish 负责转成上面的原生字段）：

    {"as_of": "2026-11-23",
     "points": [{"key": "chanlun.bull_score", "sec": "000300.SH",
                 "value": 62.0, "obs_date": "2026-11-23",
                 "quality": "ok", "name": "缠论评分", "dec": 0}]}

也接受原生写法（metric_key / security_id / value_num / value_text）。

用法：
    python3 case/_lib/publish.py --task tech_consensus
    python3 case/_lib/publish.py --all
    python3 case/_lib/publish.py --all --dry-run
    python3 case/_lib/publish.py --all --snapshot     # 写完后推云盘快照
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)

QUALITIES = ("ok", "missing", "stale", "estimated")
# DataHub metric_points.risk_level 口径；标签由任务侧判断，这里只做档位翻译
RISK_LEVELS = ("none", "opportunity", "risk", "alert")
RISK_ALIASES = {
    "warn": "risk", "warning": "risk", "预警": "risk", "风险": "risk",
    "severe": "alert", "严重": "alert", "告警": "alert",
    "机会": "opportunity", "中性": "none", "": "none",
}
# DataHub 里的写入口（顶层导出优先，其次是 store 模块）
WRITER_NAMES = (
    "write_points", "upsert_points", "write_point", "put_points",
    "write_metrics", "upsert_metrics", "append_points",
)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def task_dir(key: str) -> str:
    return os.path.join(CASE_DIR, key)


def normalize(points: List[Dict], default_obs: str = "") -> Tuple[List[Dict], List[str]]:
    """把关卡的简写字段转成 DataHub 原生字段，并做校验。"""
    rows: List[Dict] = []
    errs: List[str] = []
    for i, it in enumerate(points):
        if not isinstance(it, dict):
            errs.append("points[%d] 不是对象" % i)
            continue
        mk = it.get("metric_key") or it.get("key")
        sid = it.get("security_id")
        if sid is None:
            sid = it.get("sec") or ""
        obs = str(it.get("obs_date") or default_obs or "")
        if not mk:
            errs.append("points[%d] 缺 metric_key/key" % i)
            continue
        if not obs:
            errs.append("points[%d] 缺 obs_date（且 as_of 也没给）" % i)
            continue
        q = it.get("quality") or ""
        if q and q not in QUALITIES:
            errs.append("points[%d] quality=%r 不在 %s" % (i, q, "/".join(QUALITIES)))
            continue
        vnum = it.get("value_num", it.get("value"))
        vtext = it.get("value_text")
        if vnum is not None and not isinstance(vnum, (int, float)):
            errs.append("points[%d] value_num 不是数字" % i)
            continue
        if isinstance(vnum, float) and not math.isfinite(vnum):
            errs.append("points[%d] value_num 不是有限数（NaN/Inf）" % i)
            continue
        if vnum is None and vtext is None and q == "ok":
            errs.append("points[%d] quality=ok 但没有 value_num/value_text" % i)
            continue
        if vnum is None and vtext is None and not q:
            q = "missing"
        raw_lv = it.get("risk_level")
        lv = ""
        if raw_lv is not None:
            lv = str(raw_lv).strip().lower()
            lv = RISK_ALIASES.get(lv, lv)
            if lv not in RISK_LEVELS:
                errs.append("points[%d] risk_level=%r 不在 %s"
                            % (i, raw_lv, "/".join(RISK_LEVELS)))
                continue
        row: Dict = {"metric_key": mk, "security_id": sid, "obs_date": obs}
        if vnum is not None:
            row["value_num"] = float(vnum)
        if vtext is not None:
            row["value_text"] = str(vtext)
        if q:
            row["quality"] = q
        if lv:
            row["risk_level"] = lv
        note = it.get("risk_note")
        if note not in (None, ""):
            row["risk_note"] = str(note)
        if isinstance(it.get("meta_json"), dict):
            row["meta_json"] = it["meta_json"]
        rows.append(row)
    return rows, errs


def load_out(key: str) -> Tuple[Optional[Dict], List[Dict], List[str]]:
    """读 out/latest.json，返回 (payload, rows, errors)。"""
    p = os.path.join(task_dir(key), "out", "latest.json")
    if not os.path.isfile(p):
        return None, [], ["缺少 %s（任务还没产出）" % os.path.relpath(p, ROOT)]
    d = _read_json(p, None)
    if not isinstance(d, dict):
        return None, [], ["out/latest.json 不是 JSON 对象"]
    pts = d.get("points")
    if not isinstance(pts, list) or not pts:
        return None, [], ["out/latest.json 的 points 为空或不是数组"]
    rows, errs = normalize(pts, str(d.get("as_of") or ""))
    return d, rows, errs


def resolve_writer(spec: Optional[str] = None):
    """返回 (writer, 说明, (start, finish, snapshot))。优先顶层导出，其次 store。"""
    try:
        mod = importlib.import_module("data_hub.tech")
    except Exception as e:                                      # noqa: BLE001
        return None, ("导入 data_hub.tech 失败：%s。把 DataHub 根目录加进 PYTHONPATH"
                      % e), None
    holders = [("data_hub.tech", mod)]
    try:
        store = importlib.import_module("data_hub.tech.store")
    except Exception:                                           # noqa: BLE001
        store = getattr(mod, "store", None)
    if store is not None:
        holders.append(("data_hub.tech.store", store))

    def _find(name: str):
        for label, holder in holders:
            fn = getattr(holder, name, None)
            if callable(fn):
                return fn, "%s.%s" % (label, name)
        return None, ""

    start = getattr(mod, "start_run", None)
    finish = getattr(mod, "finish_run", None)
    snap = getattr(mod, "snapshot", None) or (getattr(store, "snapshot", None)
                                              if store is not None else None)

    names = (spec,) if spec else WRITER_NAMES
    for name in names:
        fn, label = _find(name)
        if fn is not None:
            return fn, label, (start, finish, snap)
    if spec:
        return None, "data_hub.tech(.store) 没有 %s" % spec, None
    return None, ("data_hub.tech 没找到写入口（试过 %s）。按 CONTRACT.md 应为 "
                  "write_points(rows, producer=...)" % "/".join(WRITER_NAMES)), None


def _accepts_producer(fn) -> Optional[bool]:
    """True/False=签名明确；None=拿不到签名（C 函数等），交给 TypeError 判断。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    if "producer" in sig.parameters:
        return True
    return any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())


def call_writer(fn, rows: List[Dict], producer: str) -> Tuple[Optional[int], Optional[int]]:
    """按 CONTRACT 调用 write_points(rows, producer=...) -> (n_ok, n_missing)。"""
    accepts = _accepts_producer(fn)
    try:
        ret = fn(rows) if accepts is False else fn(rows, producer=producer)
    except TypeError as e:
        # 只有「不接受 producer 关键字」才回退；写函数内部的 TypeError 不能再调一次，
        # 否则会重复写库（而且第一次可能已经写进去了一部分）。
        if accepts is False or "producer" not in str(e):
            raise
        ret = fn(rows)
    if isinstance(ret, tuple) and len(ret) == 2:
        return ret[0], ret[1]
    return None, None


def backfill_metrics(key: str, rows: List[Dict], pts: List[Dict]) -> int:
    """把写进去的 (metric_key, security_id) 回填 task.json 的 metrics，面板据此渲染。"""
    tj = os.path.join(task_dir(key), "task.json")
    t = _read_json(tj, None)
    if not isinstance(t, dict):
        return 0
    name_of, meta_of = {}, {}
    for p in pts:
        mk = p.get("metric_key") or p.get("key")
        if not mk:
            continue
        if p.get("name"):
            name_of[mk] = p["name"]
        extra = {}
        if isinstance(p.get("unit"), str):
            extra["unit"] = p["unit"]
        if isinstance(p.get("dec"), int) and not isinstance(p.get("dec"), bool) \
                and 0 <= p["dec"] <= 6:
            extra["dec"] = p["dec"]
        if isinstance(p.get("color"), str) and p["color"]:
            extra["color"] = p["color"]
        if extra:
            meta_of[mk] = extra
    cur = t.get("metrics") or []
    seen = {(m.get("key"), m.get("sec")) for m in cur if isinstance(m, dict)}
    added = 0
    for r in rows:
        pair = (r["metric_key"], r.get("security_id") or "")
        if pair in seen:
            continue
        m = {"key": r["metric_key"], "sec": r.get("security_id") or "",
             "name": name_of.get(r["metric_key"]) or r["metric_key"]}
        m.update(meta_of.get(r["metric_key"]) or {})   # 保留 unit/dec/color，面板才能按精度展示
        cur.append(m)
        seen.add(pair)
        added += 1
    if added:
        t["metrics"] = cur
        _write_json(tj, t)
    return added


def publish_one(key: str, dry: bool, writer_spec: Optional[str],
                do_snapshot: bool = False) -> Dict:
    payload, rows, errs = load_out(key)
    if errs:
        return {"key": key, "ok": False, "msg": "；".join(errs[:3])}
    n_ok = sum(1 for r in rows if (r.get("quality") or "ok") == "ok")
    n_missing = len(rows) - n_ok
    if dry:
        return {"key": key, "ok": True,
                "msg": "dry-run：%d 行（ok %d / 非 ok %d），未落库"
                       % (len(rows), n_ok, n_missing)}
    fn, label, extras = resolve_writer(writer_spec)
    if fn is None:
        return {"key": key, "ok": False, "msg": label}
    start, finish, snap = extras or (None, None, None)
    producer = "case/%s" % key
    run_id = None
    if callable(start):
        try:
            run_id = start(producer)
        except Exception:                                       # noqa: BLE001
            run_id = None
    try:
        got_ok, got_missing = call_writer(fn, rows, producer)
    except Exception as e:                                      # noqa: BLE001
        return {"key": key, "ok": False, "msg": "写入失败（%s）：%s" % (label, e)}
    if callable(finish) and run_id is not None:
        try:
            finish(run_id, str(payload.get("as_of") or rows[0]["obs_date"]),
                   got_ok if got_ok is not None else n_ok,
                   got_missing if got_missing is not None else n_missing, "ok", "case publish")
        except Exception:                                       # noqa: BLE001
            pass
    if do_snapshot and callable(snap):
        try:
            snap()
        except Exception:                                       # noqa: BLE001
            pass
    added = backfill_metrics(key, rows, payload.get("points") or [])
    if not getattr(publish_one, "_synced", False):
        _sync_out_template(key)
        publish_one._synced = True
    # 真实落库行数：writer 返回 (n_ok, n_missing) 时以它为准，不能拿「准备写的行数」充数。
    # 一行都没写进去还报 ok=True，下游 runner/scheduler 会把 rc=0 记成成功，
    # health 连败清零、看板永远全绿 —— 失败静默的反面就是假成功。
    written = got_ok if got_ok is not None else n_ok
    if got_ok is not None and len(rows) > 0 and got_ok <= 0:
        return {"key": key, "ok": False,
                "msg": "写入 0 行（准备 %d 行，ok 0 / 非 ok %d）via %s，回填 metrics +%d"
                       % (len(rows), n_missing, label, added)}
    return {"key": key, "ok": True,
            "msg": "写入 %d 行（ok %s）via %s，回填 metrics +%d"
                   % (len(rows), written, label, added)}


def _sync_out_template(key: str) -> None:
    """把标准输出模板放到任务目录，供任务脚本照着写 out/latest.json。"""
    tdir = task_dir(key)
    if not os.path.isdir(tdir):
        return
    sample = {
        "as_of": "YYYY-MM-DD",
        "points": [
            {"key": "chanlun.bull_score", "sec": "000300.SH", "value": 62.0,
             "obs_date": "YYYY-MM-DD", "quality": "ok", "name": "缠论评分", "dec": 0},
            {"key": "chanlun.signal", "sec": "000300.SH", "value_text": "B2",
             "obs_date": "YYYY-MM-DD", "quality": "ok", "name": "最近买卖点"},
            {"key": "rmt.ar5", "sec": "000300.SH", "obs_date": "YYYY-MM-DD",
             "quality": "missing", "meta_json": {"reason": "样本不足"}},
        ],
    }
    p = os.path.join(tdir, "out", "latest.sample.json")
    if os.path.isfile(p):
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    _write_json(p, sample)


def all_keys() -> List[str]:
    out: List[str] = []
    try:
        names = sorted(os.listdir(CASE_DIR))
    except OSError:
        return out
    for n in names:
        if n.startswith("_") or n.startswith("."):
            continue
        if os.path.isfile(os.path.join(CASE_DIR, n, "task.json")):
            out.append(n)
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-n", "--dry-run", action="store_true")
    ap.add_argument("--snapshot", action="store_true", help="写完推云盘快照")
    ap.add_argument("--writer", default="", help="写入口函数名（默认 write_points）")
    args = ap.parse_args(argv)

    keys = all_keys() if args.all else ([args.task] if args.task else [])
    if not keys:
        print("用法：publish.py --task <key> | --all  [--dry-run] [--snapshot]")
        return 1
    if args.task and args.task not in all_keys():
        print("未知 case 任务：%s（可用：%s）" % (args.task, ", ".join(all_keys())))
        return 1

    res = [publish_one(k, args.dry_run, args.writer or None, args.snapshot) for k in keys]
    bad = [r for r in res if not r["ok"]]
    for r in res:
        print("  %-24s %s  %s" % (r["key"], "OK  " if r["ok"] else "FAIL", r["msg"]))
    print("共 %d 个，成功 %d，失败 %d" % (len(res), len(res) - len(bad), len(bad)))
    if bad:
        if args.dry_run:
            return 1          # 校验模式失败也要非零，否则 --dry-run 等于永远通过
        only_missing = all("缺少" in r["msg"] for r in bad)
        only_writer = all(("写入口" in r["msg"] or "导入 data_hub.tech" in r["msg"]) for r in bad)
        return 1 if only_missing else 2 if only_writer else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))