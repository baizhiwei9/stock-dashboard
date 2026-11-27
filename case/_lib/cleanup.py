#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cleanup.py — 产物保留期清理（issue #80）

背景
----
case 任务会往 `data/`（取数缓存）、`out/`、`output/`、`reports/`、`charts*/` 里写文件，
其中一部分按日期命名、只增不减：

    case/margin_flow/data/md/20261120.csv          每交易日一个
    case/option_risk_cn_us/data/chains/20261120.json 每交易日一个（全市场期权链）
    case/option_risk_cn_us/xxx_20261120.html       每数据日一份
    case/margin_flow/市场融资风险扫描日报_合并图_20261120.png
    case/<key>/out/alerts/<stamp>-warn.md          每条告警一个
    assets/private/<key>/<img>.png                 每次 build 复制一份原图

`.gitignore` 只能让它们不进 git，**不会让它们消失**。这个脚本就是给它们定保留期。

安全约束（改之前先读这几条）
----------------------------
1. **只删白名单后缀**（CSV/JSON/PNG/HTML/MD/TXT/日志/序列化缓存），代码与配置
   （`.py/.sh/.yaml/.toml/...`）永远不碰；
2. **git 跟踪的文件一个都不删**（`git ls-files` 求交集，模型资产、历史 JSON 都在里面）；
3. 关键文件名永不删：`latest.json / check.json / status.json / task.json / metrics.json`；
4. 只能删**根目录内部**的路径（`realpath` 必须落在 root 下，挡符号链接穿越）；
5. 默认 `--dry-run`，只打印清单；`--apply` 才真删；
6. 不管删不删，都先把「清单 + 字节数」写进 `logs/cleanup-<时间戳>.txt`，便于回查；
7. 规则可带 `exclude` glob（如 `case/*/data/chains/**/*`）：命中即跳过，给「不能重建的
   历史数据」留一条更高保留期的规则，不被短保留期规则先删掉。

用法
----
    python3 case/_lib/cleanup.py                      # 干跑：打印将删清单
    python3 case/_lib/cleanup.py --apply              # 真删
    python3 case/_lib/cleanup.py --apply --json       # 机器可读
    python3 case/_lib/cleanup.py --task margin_flow   # 只清一个任务
    python3 case/_lib/cleanup.py --rule 私有页图片镜像   # 只清图库镜像（半夜任务用）
    python3 case/_lib/cleanup.py --root ../precious-task   # 清私有仓（默认也吃 CASE_EXTRA_DIRS）

调度：`modules.json` 里 `cleanup` 模块，默认每天 04:30（错开 datahub 05:10 的 prune）。
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
RULES_PATH = os.path.join(HERE, "cleanup_rules.json")
LOG_DIR = os.path.join(REPO, "logs")

#: 允许删除的后缀（规则里可以用 ``ext`` 再收窄，但不能超出这个集合）
ALLOWED_EXT = {
    ".csv", ".json", ".pkl", ".parquet", ".npy", ".npz",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".html", ".md", ".txt", ".log", ".zip",
}

#: 文件名命中这些就永不删（模板/契约/落库产物）
PROTECTED_NAMES = {
    "latest.json", "check.json", "status.json", "task.json",
    "metrics.json", "cleanup_rules.json", "cleanup_rules.example.json",
}

#: 目录名命中这些就整棵跳过
PROTECTED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}

MAX_AGE_DAYS_DEFAULT = 90


def _load_rules(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _git_tracked(root: str) -> set:
    """root 下被 git 跟踪的文件（绝对路径）。git 不可用时返回空集（不是报错）。"""
    try:
        p = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                           capture_output=True, timeout=30)
    except Exception:                                       # noqa: BLE001
        return set()
    if p.returncode != 0:
        return set()
    out = p.stdout.decode("utf-8", "replace")
    return {os.path.normpath(os.path.join(root, x)) for x in out.split("\0") if x}


def _html_refs(root: str, html_rel: str) -> set:
    """从页面 HTML 里抠出 ``assets/...`` 相对引用（引用感知：被引用的图不删）。"""
    p = os.path.join(root, html_rel)
    if not os.path.isfile(p):
        return set()
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return set()
    refs = set()
    for token in text.replace('"', " ").replace("'", " ").split():
        t = token.strip()
        if t.startswith("assets/") or t.startswith("./assets/"):
            refs.add(os.path.normpath(t.lstrip("./")))
    return refs


def _inside(root: str, path: str) -> bool:
    try:
        real = os.path.realpath(path)
        base = os.path.realpath(root)
    except OSError:
        return False
    return real == base or real.startswith(base + os.sep)


def _skip_dir(path: str) -> bool:
    return any(part in PROTECTED_DIRS for part in path.split(os.sep))


def _ext_ok(name: str, allow: set) -> bool:
    """后缀判定按「点分段」做，不能用 ``splitext``。

    ``margin_flow.log.20261101-043000`` 这种轮转日志，``splitext`` 取到的后缀是
    时间戳本身（``.20261101-043000``），永远不在白名单里 —— 按点分段则能命中 ``.log``。
    """
    return bool({"." + seg.lower() for seg in name.split(".")[1:]} & allow)


def _excluded(posix: str, excludes: list) -> bool:
    """规则级 exclude 判定。

    ``fnmatch`` 里 ``*`` 会吃掉斜杠但 ``**/*`` 仍要求「再有一层目录」，所以
    ``case/*/data/chains/**/*`` 匹配不到 ``chains/20260101.json``。这里对
    ``/**/*`` 与 ``/**`` 结尾的模式改成「逐级祖先目录逐个比对前缀」，前缀里的
    通配符才不会被当成字面量。
    """
    for ex in excludes:
        if fnmatch.fnmatch(posix, ex):
            return True
        pat = None
        if ex.endswith("/**/*"):
            pat = ex[:-5]
        elif ex.endswith("/**"):
            pat = ex[:-3]
        if pat:
            d = os.path.dirname(posix)
            while d and d not in ("/", ""):
                if fnmatch.fnmatch(d, pat):
                    return True
                d = os.path.dirname(d)
    return False


def _collect(root: str, rule: dict, tracked: set, refs: set, task: str | None):
    """按一条规则收集候选（返回 (rel, size, age_days) 列表）。"""
    pattern = rule["glob"]
    if task and pattern.startswith("case/"):
        # 任务过滤只对 case/<task>/... 生效，其它规则（assets/logs）照常。
        # 剥掉 "case/*" 后只留尾巴：case/*/*.png → *.png，case/*/data/**/* → data/**/*
        tail = pattern.split("/", 2)[2] if pattern.count("/") > 1 else "**/*"
        pattern = "case/%s/%s" % (task, tail)

    ext_allow = {e.lower() for e in rule.get("ext", [])} or ALLOWED_EXT
    excludes = [os.path.join(root, g).replace(os.sep, "/") for g in rule.get("exclude", [])]
    keep_days = float(rule.get("keep_days", MAX_AGE_DAYS_DEFAULT))
    now = time.time()
    out = []
    for path in glob.glob(os.path.join(root, pattern), recursive=True):
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        if _skip_dir(path):
            continue
        name = os.path.basename(path)
        if name in PROTECTED_NAMES:
            continue
        if not _ext_ok(name, ext_allow):
            continue
        if not _inside(root, path):
            continue
        norm = os.path.normpath(path)
        posix = norm.replace(os.sep, "/")
        if _excluded(posix, excludes):                       # 规则显式排除（历史数据）
            continue
        if norm in tracked:                                  # git 跟踪的一律不删
            continue
        rel = os.path.relpath(norm, root).replace(os.sep, "/")
        if rule.get("respect_html") and rel in refs:
            continue
        try:
            st = os.stat(norm)
        except OSError:
            continue
        age = (now - st.st_mtime) / 86400.0
        if age < keep_days:
            continue
        out.append((rel, st.st_size, age))
    return out


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f%s" % (n, unit) if unit != "B" else "%dB" % n
        n /= 1024.0
    return "%dB" % n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="case 产物保留期清理（默认干跑）")
    ap.add_argument("--apply", action="store_true", help="真删；不加就是 dry-run")
    ap.add_argument("--root", action="append", default=None,
                    help="要清理的根（可多次给）。默认本仓 + CASE_EXTRA_DIRS")
    ap.add_argument("--task", default=None, help="只清这个 case 任务")
    ap.add_argument("--rule", action="append", default=None,
                    help="只跑指定规则名（可多次给，精确匹配 rules[].name）；"
                         "用于「图库月清」只清镜像、不动其它产物")
    ap.add_argument("--rules", default=RULES_PATH, help="规则文件路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-manifest", action="store_true", help="不写 logs/cleanup-*.txt")
    ap.add_argument("--max-delete", type=int, default=0,
                    help="整轮最多删多少个文件（0=不限，跨规则/跨根累计），防止规则写错一次删光")
    a = ap.parse_args(argv)

    try:
        cfg = _load_rules(a.rules)
    except (OSError, ValueError) as e:
        print("[cleanup] 读不到规则 %s: %s" % (a.rules, e), file=sys.stderr)
        return 2

    roots = list(a.root or [])
    if not roots:
        roots = [REPO]
        extra = os.environ.get("CASE_EXTRA_DIRS") or ""
        roots += [p for p in extra.split(os.pathsep) if p]
        roots = [os.path.normpath(os.path.join(r, "..")) if r.rstrip("/").endswith("/case") else r
                 for r in roots]
    roots = [r for r in dict.fromkeys(os.path.abspath(r) for r in roots) if os.path.isdir(r)]
    if not roots:
        print("[cleanup] 没有可清理的根目录", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = ["# cleanup %s  mode=%s" % (stamp, "APPLY" if a.apply else "DRY-RUN")]
    report = {"mode": "apply" if a.apply else "dry-run", "roots": roots,
              "rules": [], "deleted": 0, "bytes": 0, "skipped_tracked": 0}
    budget = a.max_delete if a.max_delete > 0 else None
    total_del = 0            # 跨规则、跨根累计的删除数（--max-delete 的真实口径）

    exhausted = False
    for root in roots:
        if exhausted:
            break
        tracked = _git_tracked(root)
        for rule in cfg.get("rules", []):
            if a.rule and rule.get("name") not in a.rule:
                continue
            refs = _html_refs(root, rule["respect_html"]) if rule.get("respect_html") else set()
            cand = _collect(root, rule, tracked, refs, a.task)
            if not cand:
                continue
            rule_del, rule_bytes = 0, 0
            lines.append("\n## %s  [%s]  keep=%sd  root=%s"
                         % (rule.get("name") or rule["glob"], rule["glob"],
                            rule.get("keep_days", MAX_AGE_DAYS_DEFAULT),
                            os.path.relpath(root, REPO) if root != REPO else "."))
            for rel, size, age in sorted(cand, key=lambda x: -x[2]):
                if budget is not None and total_del >= budget:
                    lines.append("  ... 达到 --max-delete=%d，本轮停止" % budget)
                    exhausted = True
                    break
                lines.append("  %-8s %8s  %5.0fd  %s"
                             % ("DELETE" if a.apply else "WOULD", _fmt_bytes(size), age, rel))
                if a.apply:
                    try:
                        os.remove(os.path.join(root, rel))
                    except OSError as e:                     # noqa: BLE001
                        lines.append("  ! 删除失败 %s: %s" % (rel, e))
                        continue
                rule_del += 1
                rule_bytes += size
                total_del += 1
            report["rules"].append({"rule": rule.get("name") or rule["glob"],
                                    "root": root, "count": rule_del, "bytes": rule_bytes})
            report["deleted"] += rule_del
            report["bytes"] += rule_bytes
            if not a.quiet:
                print("[cleanup] %-22s %3d 个 %8s  %s"
                      % (rule.get("name") or rule["glob"], rule_del,
                         _fmt_bytes(rule_bytes), os.path.basename(root)))
            if exhausted:
                break

    if not a.no_manifest:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            mp = os.path.join(LOG_DIR, "cleanup-%s.txt" % stamp)
            lines.append("\n# total: %d files, %s" % (report["deleted"], _fmt_bytes(report["bytes"])))
            with open(mp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            report["manifest"] = mp
        except OSError as e:                                 # noqa: BLE001
            print("[cleanup] 清单写入失败（不影响结果）: %s" % e, file=sys.stderr)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif not a.quiet:
        print("[cleanup] %s：%d 个文件，%s%s"
              % ("已删除" if a.apply else "将删除", report["deleted"],
                 _fmt_bytes(report["bytes"]),
                 "" if a.apply else "（加 --apply 才真删）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())