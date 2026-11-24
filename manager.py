#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manager.py — 看板模块管理器

每个模块 = 一个独立脚本 + 一条自己的 crontab，互不牵连。
一个模块炸了不影响别的模块出数。

用法
════════════════════════════════════════════════════════════════
    python3 manager.py list                 # 模块清单 + 数据状态
    python3 manager.py status               # 详细体检
    python3 manager.py run <key> [--bg]     # 手动跑一个模块
    python3 manager.py enable  <key>        # 启用（改 modules.json）
    python3 manager.py disable <key>        # 停用
    python3 manager.py install              # 写 crontab 标记块（只装启用的）
    python3 manager.py uninstall            # 移除 crontab 标记块
    python3 manager.py sections [on|off|pw] # 板块显隐 / 改管理密码
    python3 manager.py log <key>            # 看最近日志

设计
════════════════════════════════════════════════════════════════
· 配置在 modules.json，crontab 只由 install 生成，标记块内不要手改
· 停用 = 从 crontab 摘掉，不删脚本，随时可恢复
· 数据状态直接读 tech 指标仓，不看日志猜
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "modules.json")
DH_ROOT = "/opt/coze/.fuse_drive/dataHub_三级体系"
LOG_DIR = os.path.join(HERE, "logs")

if DH_ROOT not in sys.path:
    sys.path.insert(0, DH_ROOT)

NA = "\033[2m—\033[0m"
GRN = "\033[32m"
RED = "\033[31m"
YEL = "\033[33m"
DIM = "\033[2m"
RST = "\033[0m"


# ── 配置读写 ─────────────────────────────────────────────────────────
def load_cfg() -> Dict:
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


def save_cfg(cfg: Dict) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_mod(cfg: Dict, key: str) -> Dict:
    for m in cfg["modules"]:
        if m["key"] == key:
            return m
    raise SystemExit("未知模块: %s（可选：%s）"
                     % (key, ", ".join(m["key"] for m in cfg["modules"])))


# ── crontab ─────────────────────────────────────────────────────────

def sched_text(m) -> str:
    """把 modules.json 的 sched 结构格式化成可读文本。

    调度已统一由 scheduler.py 管理，不再有 crontab 表达式字段。
    """
    s = m.get("sched") or {}
    mode = s.get("mode")
    if mode == "daily":
        wd = s.get("weekdays") or list(range(1, 7))
        if sorted(wd) == [1, 2, 3, 4, 5]:
            wd_txt = "工作日"
        elif len(wd) == 7:
            wd_txt = "每天"
        else:
            wd_txt = "周" + ",".join(str(x) for x in sorted(wd))
        return "每日 %s %s" % (s.get("at", "?"), wd_txt)
    if mode == "interval":
        return "每 %s 分钟" % s.get("every", "?")
    if mode == "cron":
        return s.get("expr", "?")
    return "未配置" if not s else str(s)


def live_keys() -> set:
    """被调度器纳入的模块 key（enabled 即视为在跑，scheduler 热加载 modules.json）。"""
    cfg = load_cfg()
    return {m["key"] for m in cfg["modules"] if m.get("enabled", True)}

def crontab_read() -> List[str]:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return r.stdout.splitlines()


def crontab_write(lines: List[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n"
    r = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("写 crontab 失败: %s" % r.stderr.strip())


def strip_block(lines: List[str], marker: str) -> List[str]:
    b, e = "# >>> %s >>>" % marker, "# <<< %s <<<" % marker
    out, skip = [], False
    for ln in lines:
        if ln.strip() == b:
            skip = True
            continue
        if ln.strip() == e:
            skip = False
            continue
        if not skip:
            out.append(ln)
    return out


def installed_keys(marker: str) -> List[str]:
    """从当前 crontab 标记块里读出已装的模块 key。"""
    lines = crontab_read()
    b, e = "# >>> %s >>>" % marker, "# <<< %s <<<" % marker
    keys, inside = [], False
    for ln in lines:
        if ln.strip() == b:
            inside = True
            continue
        if ln.strip() == e:
            inside = False
            continue
        if inside:
            m = re.search(r"#\s*sd:([a-z0-9_]+)\s*$", ln)
            if m:
                keys.append(m.group(1))
    return keys


# ── 数据状态 ─────────────────────────────────────────────────────────
def data_state(domains: List[str]) -> List[Dict]:
    """按域统计指标仓里的最新日期与覆盖。"""
    if not domains:
        return []
    try:
        from data_hub.tech import registry as reg
        from data_hub.tech import store
        conn = store.connect()
        out = []
        for d in domains:
            keys = [k for k, v in reg.REGISTRY.items() if v.domain == d]
            if not keys:
                continue
            q = ",".join("?" * len(keys))
            row = conn.execute(
                "SELECT COUNT(DISTINCT metric_key), MAX(obs_date), "
                "SUM(CASE WHEN quality='ok' THEN 1 ELSE 0 END), COUNT(*) "
                "FROM metric_points WHERE metric_key IN (%s)" % q, keys).fetchone()
            out.append({"domain": d, "n_metrics": row[0] or 0,
                        "latest": row[1], "n_ok": row[2] or 0, "n_all": row[3] or 0,
                        "n_def": len(keys)})
        conn.close()
        return out
    except Exception as e:                                    # noqa: BLE001
        return [{"domain": d, "error": str(e)[:60]} for d in domains]


def last_run(domains: List[str]) -> Optional[str]:
    if not domains:
        return None
    try:
        from data_hub.tech import store
        conn = store.connect()
        rows = conn.execute(
            "SELECT producer, finished_at, status, message FROM metric_runs "
            "ORDER BY run_id DESC LIMIT 40").fetchall()
        conn.close()
        for prod, fin, st, msg in rows:
            for d in domains:
                if d in (prod or "") or d.split(".")[0] in (prod or ""):
                    return "%s %s %s" % ((fin or "?"), st or "", (msg or "")[:40])
        return None
    except Exception:                                         # noqa: BLE001
        return None


# ── 命令 ─────────────────────────────────────────────────────────────
def cmd_list() -> int:
    cfg = load_cfg()
    live = live_keys()

    print("\n%s看板模块清单%s  （配置 %s）\n" % (YEL, RST, CFG))
    hdr = "  %-11s %-22s %-18s %-8s %-8s %s" % (
        "KEY", "模块", "调度", "状态", "已纳入", "数据")
    print(DIM + hdr + RST)
    print(DIM + "  " + "─" * 104 + RST)

    for m in cfg["modules"]:
        key = m["key"]
        on = m.get("enabled", True)
        st = (GRN + "启用" + RST) if on else (RED + "停用" + RST)
        inst = (GRN + "✓" + RST) if key in live else (NA + "✗" + RST)
        if not on:
            inst = NA + "—" + RST
        ds = data_state(m.get("domains") or [])
        if not ds:
            d_txt = DIM + "（无数据域）" + RST
        else:
            parts = []
            for x in ds:
                if x.get("error"):
                    parts.append(RED + "%s 读取失败" % x["domain"] + RST)
                elif not x.get("latest"):
                    parts.append(RED + "%s 空" % x["domain"] + RST)
                else:
                    age = ""
                    try:
                        dd = (date.today() - datetime.strptime(x["latest"], "%Y-%m-%d").date()).days
                        if dd <= 1:
                            age = GRN + "✓" + RST
                        elif dd <= 4:
                            age = YEL + "%dd" % dd + RST
                        else:
                            age = RED + "%dd" % dd + RST
                    except ValueError:
                        pass
                    parts.append("%s %s %s" % (x["domain"], x["latest"], age))
            d_txt = " | ".join(parts)
        print("  %-11s %-22s %-18s %-8s %-8s %s"
              % (key, m["title"][:20], sched_text(m), st, inst, d_txt))

    n_on = sum(1 for m in cfg["modules"] if m.get("enabled", True))
    print("\n  %d 个模块，启用 %d 个，调度器已纳入 %d 个\n"
          % (len(cfg["modules"]), n_on, len(live)))
    return 0


def cmd_status() -> int:
    cfg = load_cfg()
    live = live_keys()
    print("\n%s模块体检%s\n" % (YEL, RST))
    for m in cfg["modules"]:
        key = m["key"]
        print("%s[%s] %s%s" % (YEL, key, m["title"], RST))
        print("  说明   %s" % m["desc"])
        print("  调度   %s   %s" % (sched_text(m),
              (GRN + "启用" + RST) if key in live else (RED + "停用" + RST)))
        print("  命令   cd %s && %s" % (m["cwd"], m["cmd"]))
        for x in data_state(m.get("domains") or []):
            if x.get("error"):
                print("  数据   %s%s %s%s" % (RED, x["domain"], x["error"], RST))
            else:
                print("  数据   %-10s 最新 %-12s 已定义 %d / 已入库 %d，ok %d 条"
                      % (x["domain"], x.get("latest") or "—",
                         x.get("n_def", 0), x.get("n_metrics", 0), x.get("n_ok", 0)))
        lr = last_run(m.get("domains") or [])
        if lr:
            print("  上次   %s" % lr)
        print()
    return 0


def cmd_run(key: str, bg: bool = False) -> int:
    cfg = load_cfg()
    m = get_mod(cfg, key)
    cmd = m["cmd"]
    os.makedirs(LOG_DIR, exist_ok=True)
    log = os.path.join(LOG_DIR, "%s.log" % key)
    print("跑模块 %s：cd %s && %s" % (key, m["cwd"], cmd))
    print("日志 %s" % log)
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n════ %s manager run %s ════\n"
                % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key))
        f.flush()
        if bg:
            p = subprocess.Popen(["bash", "-lc", cmd], cwd=m["cwd"],
                                 stdout=f, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            print("已在后台启动 pid=%d" % p.pid)
            return 0
        r = subprocess.run(["bash", "-lc", cmd], cwd=m["cwd"],
                           stdout=f, stderr=subprocess.STDOUT)
    print("退出码 %d，看日志：tail -30 %s" % (r.returncode, log))
    return r.returncode


def cmd_toggle(key: str, on: bool) -> int:
    cfg = load_cfg()
    m = get_mod(cfg, key)
    m["enabled"] = on
    save_cfg(cfg)
    print("%s %s（%s）→ %s" % ("启用" if on else "停用", key, m["title"],
                              "enabled=true" if on else "enabled=false"))
    print("scheduler.py 会热加载 modules.json，无需 install")
    return 0


def cmd_install() -> int:
    print("%sinstall 已停用%s" % (RED, RST))
    print("  调度已统一由 scheduler.py 管理（modules.json 的 sched 字段）。")
    print("  再往 crontab 写模块会和 scheduler 双跑，所以这里直接拒绝。")
    print("  要启用/停用模块： python3 manager.py enable|disable <key>")
    return 1


def cmd_uninstall() -> int:
    print("%suninstall 已停用%s" % (RED, RST))
    print("  crontab 里已无本看板的模块块；如需清理历史残留：")
    print("  crontab -l | grep -v 'sd:' | crontab -")
    return 1


def cmd_sections(argv: List[str]) -> int:
    """sections            列出板块与显隐
       sections on  <key>  设为显示
       sections off <key>  设为隐藏
       sections pw  <新密码>
    """
    cfg = _sec_load()
    secs = cfg.get("sections", [])
    if not argv:
        print("\n%s看板板块%s（配置 %s，管理密码 %s）\n"
              % (YEL, RST, SEC_CFG, cfg.get("password")))
        for m in secs:
            st = (GRN + "显示" + RST) if m.get("enabled", True) else (RED + "隐藏" + RST)
            print("  %-12s %-14s %s" % (m["key"], m["title"][:12], st))
            for sm in (m.get("subs") or []):
                st2 = (GRN + "显示" + RST) if sm.get("enabled", True) else (RED + "隐藏" + RST)
                print("    └ %-16s %s" % (sm["key"], st2))
        print("\n  改完执行 python3 build.py 重新生成，再 ./publish.sh 发布\n")
        return 0

    a = argv[0]
    if a == "pw":
        if len(argv) < 2:
            raise SystemExit("用法: manager.py sections pw <新密码>")
        cfg["password"] = argv[1]
        with open(SEC_CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("管理密码已改为 %s" % argv[1])
        return 0
    if a in ("on", "off"):
        if len(argv) < 2:
            raise SystemExit("用法: manager.py sections %s <key>" % a)
        want = (a == "on")
        hit = False
        for m in secs:
            if m["key"] == argv[1]:
                m["enabled"] = want
                hit = True
            for sm in (m.get("subs") or []):
                if sm["key"] == argv[1]:
                    sm["enabled"] = want
                    hit = True
        if not hit:
            raise SystemExit("未知板块: %s" % argv[1])
        with open(SEC_CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("%s → %s（改完执行 python3 build.py && ./publish.sh 生效）"
              % (argv[1], "显示" if want else "隐藏"))
        return 0
    raise SystemExit("用法: manager.py sections [on|off|pw] ...")


def cmd_log(key: str) -> int:
    log = os.path.join(LOG_DIR, "%s.log" % key)
    if not os.path.exists(log):
        print("没有日志：%s" % log)
        return 1
    r = subprocess.run(["tail", "-40", log], text=True)
    return r.returncode


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    c = argv[0]
    if c == "list":
        return cmd_list()
    if c == "status":
        return cmd_status()
    if c == "run":
        if len(argv) < 2:
            raise SystemExit("用法: manager.py run <key> [--bg]")
        return cmd_run(argv[1], bg="--bg" in argv)
    if c == "enable":
        return cmd_toggle(argv[1], True)
    if c == "disable":
        return cmd_toggle(argv[1], False)
    if c == "install":
        return cmd_install()
    if c == "uninstall":
        return cmd_uninstall()
    if c == "sections":
        return cmd_sections(argv[1:])
    if c == "log":
        return cmd_log(argv[1])
    raise SystemExit("未知命令: %s（list/status/run/enable/disable/log）" % c)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
