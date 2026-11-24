# -*- coding: utf-8 -*-
"""周度执行入口（全市场口径）

链路：刷新全市场数据 → 全链路过滤（起点＝全市场行业+概念清单）→ 选股 → 合成图 → 硬门禁 → 推送飞书
顺序：fetch_full_market → screen_full_market → fetch_ths_topology → chan_full
      → universe_chain → select_stocks → gen_chart → gate_table → push_card
用法: python3 run_report.py [--task-dir DIR] [--no-push] [--skip-fetch]
"""
import argparse
import json
import os
import subprocess
import sys
import time

import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
PL = os.path.join(WS, "pylibs")
CFG = os.path.join(WS, "ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG", "pylibs")
sys.path.insert(0, PL)
sys.path.insert(0, CFG)
import ts_config  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()
HERE = os.path.dirname(os.path.abspath(__file__))


def call(api, params, retry=3):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.8)
            r = S.post(URL, json={"api_name": api, "token": TOKEN, "params": params,
                                  "fields": ""}, timeout=45)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]["fields"], j["data"]["items"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:60])
            if "过快" in (j.get("msg") or ""):
                time.sleep(10 + 8 * i)
                continue
        except Exception as e:  # noqa
            last = repr(e)[:120]
        time.sleep(2 + 2 * i)
    print("[warn] 接口失败", api, params, last)
    return [], []


def latest_trade_date():
    f, it = call("index_daily", {"ts_code": "000001.SH", "start_date": "20260901",
                                 "end_date": "20261231"})
    rows = [dict(zip(f, r)) for r in it] if f else []
    rows.sort(key=lambda x: x["trade_date"])
    return rows[-1]["trade_date"] if rows else None


def run_script(task_dir, name, extra=None, label=None, fatal=False):
    cmd = [sys.executable, os.path.join(task_dir, name)]
    if name in ("universe_chain.py", "select_stocks.py", "gen_chart.py", "gate_table.py",
                "push_card.py"):
        cmd += ["--task-dir", task_dir]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=task_dir)
    tag = label or name
    print("--- %s ---" % tag)
    print((r.stdout or "").strip()[-1500:] or (r.stderr or "").strip()[-500:])
    if r.returncode != 0:
        if fatal:
            print("[fail] %s 失败，中止" % tag)
            sys.exit(1)
        print("[warn] %s 失败，继续（沿用上次结果）" % tag)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=HERE)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    a = ap.parse_args()
    T = a.task_dir
    cfg_path = os.path.join(T, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    cfg["report_name"] = "将军成长股拓扑选股"
    tdate = latest_trade_date()
    if not tdate:
        print("[warn] 未取到最近交易日，沿用配置时点", cfg["asof"])
        tdate = cfg["asof"].replace("-", "")
    cfg["asof"] = "%s-%s-%s" % (tdate[:4], tdate[4:6], tdate[6:8])
    cfg["table_asof"] = cfg["asof"]
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("数据时点:", cfg["asof"])

    if not a.skip_fetch:
        run_script(T, "fetch_full_market.py", label="① 取全市场行情/成分/估值/成交额")
        run_script(T, "screen_full_market.py", label="①b 全市场条件过滤（C1/C2/C3/C6）")
        run_script(T, "fetch_ths_topology.py", extra=["--n", "80"], label="② 概念指数拓扑")
        run_script(T, "chan_full.py", label="③ 行业/概念指数缠论")

    run_script(T, "universe_chain.py", label="④ 全市场链路汇总（对照表+重点行业核查）", fatal=True)
    run_script(T, "select_stocks.py", extra=["--top", "0", "--cand", "22"],
               label="⑤ 选股与个股缠论（全部全链通过对象）", fatal=True)
    run_script(T, "gen_chart.py", label="⑥ 合成图", fatal=True)
    run_script(T, "gate_table.py", label="⑦ 硬门禁", fatal=True)

    if a.no_push:
        print("[skip] 按要求不推送")
        return
    r = run_script(T, "push_card.py", label="⑧ 推送飞书", fatal=True)
    sys.exit(r)


if __name__ == "__main__":
    main()