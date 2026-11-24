# -*- coding: utf-8 -*-
"""「因子合成 × 缠论买卖点 × 均线过滤」周度生产驱动（供「因子旋转周报」定时任务调用）。

把 7 个环节串成一条可重复执行的链路，产出周报的缠论选股合并图与文字卡数据。

数据流：
    build_cross_section_fullA.py -> raw/latest_cross_section_fullA.csv          全 A 因子截面
    factor_layer_final.py        -> raw/factor_layer_stock_pct.csv              个股因子分位矩阵
    nonneg_layer_pick.py         -> output/nonneg_layer_rank.csv                三层板块名次
    chan_final_rank.py           -> output/chan_final_rank.csv                  缠论双口径榜单
    chan_final_v4.py             -> output/chan_final_v4.csv                    定稿（用户右侧确认口径）
    chan_panels.py               -> raw/_panels.pkl / raw/_panels_index.json    面板数据 + 索引
    chan_panels_plot_v6.py       -> output/chan_select_weekly{SUFFIX}.png       合并图
    本脚本                        -> raw/chan_select_summary.json                文字卡数据

纳入规则（用户定稿，格数不写死，随数据自动变化）：
    ① 有缠论买点的板块全部纳入 —— **不对其施加均线过滤**（A 档买点有效且均线过关、
       B 档买点有效但均线未过关，均保留）；
    ② 无买点的「待买点 / 待突破」板块（C 档）中，现价均线过关者纳入；
    ③ 无买点且均线未过关的 C 档排除。
均线过关 = 最新收盘 > MA40，或 最新 MA10 > MA20。
三买 B3 出图时必画其所依据的锚定中枢（可在图上肉眼验「回抽不回中枢」）。

日期动态化：链路各脚本统一经 raw/_daily_snap.read_daily 读取「区间表 daily_260d.csv +
最新单日快照 daily_YYYYMMDD.csv」，不再硬编码交易日；缺最新快照时必须先补齐快照。

用法：
    python raw/chan_weekly.py                 # 全链路
    python raw/chan_weekly.py --skip-build    # 跳过第 1 步（截面日未变时复用已有截面文件）
    python raw/chan_weekly.py --plot-only     # 只重绘（复用已有 raw/_panels.pkl）
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
PYLIBS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs"
PY = sys.executable or "python3"
SUMMARY_JSON = f"{RAW}/chan_select_summary.json"

SUFFIX = os.environ.get("WK_SUFFIX", "")
IMG = f"{OUT}/chan_select_weekly{SUFFIX}.png"

# (阶段名, 脚本, 是否受 --skip-build / --plot-only 影响)
STEPS = [
    ("1/7 重建全 A 因子截面", f"{RAW}/build_cross_section_fullA.py", "build"),
    ("2/7 因子层打分（个股分位矩阵）", f"{RAW}/factor_layer_final.py", "full"),
    ("3/7 非负·剔反转三层榜单", f"{RAW}/nonneg_layer_pick.py", "full"),
    ("4/7 缠论榜单（close / 极值双口径）", f"{RAW}/chan_final_rank.py", "full"),
    ("5/7 缠论定稿（用户右侧确认口径）", f"{RAW}/chan_final_v4.py", "full"),
    ("6/7 面板数据（K线/笔/中枢/买点/均线）", f"{RAW}/chan_panels.py", "full"),
    ("7/7 合并出图（买点全部保留 + 均线过关待买点）", f"{RAW}/chan_panels_plot_v6.py", "plot"),
]


def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = os.pathsep.join([RAW, PYLIBS, e.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def run_step(label, script):
    print(f"\n=========== {label} ===========", flush=True)
    t0 = dt.datetime.now()
    r = subprocess.run([PY, script], cwd=BASE, env=_env(), capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()
    for ln in tail[-24:]:
        print("   ", ln, flush=True)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        print(f"[FAIL] {label} rc={r.returncode}", flush=True)
        for ln in err[-25:]:
            print("   !", ln, flush=True)
        raise SystemExit(f"阶段失败：{label}")
    print(f"[OK] {label} 用时 {(dt.datetime.now() - t0).total_seconds():.0f}s", flush=True)


def build_summary():
    """由 raw/_panels_index.json 生成文字卡数据（与图上 12 格完全同源同规则）。"""
    idx = json.load(open(f"{RAW}/_panels_index.json", encoding="utf-8"))
    buy = sorted([p for p in idx if p.get("buy_kind")], key=lambda p: p["因子名次"])
    ma = sorted([p for p in idx if p.get("ma_pass") and not p.get("buy_kind")],
                key=lambda p: p["因子名次"])
    cn = {"B1": "一买", "B2": "二买", "B3": "三买", "S1": "一卖", "S2": "二卖", "S3": "三卖"}
    panels = []
    for p in buy + ma:
        panels.append({
            "层级": p["层级"], "名称": p["名称"], "因子名次": int(p["因子名次"]),
            "组别": p["grp"],
            "买点": (f"{cn.get(p['buy_kind'], p['buy_kind'])} {p['buy_kind']} @{p['buy_date'][5:]}"
                     if p.get("buy_kind") else "待买点/待突破"),
            "买点类型": p.get("buy_kind") or "",
            "买点日期": p.get("buy_date") or "",
            "均线过关": bool(p.get("ma_pass")),
            "说明": p.get("tag", ""),
        })
    as_of = ""
    try:
        sys.path.insert(0, RAW)
        from _daily_snap import latest_snapshot
        as_of, _ = latest_snapshot(RAW)
    except Exception:  # noqa: BLE001
        pass
    summary = {
        "as_of": f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}" if len(as_of) == 8 else as_of,
        "n_buy_panel": len(buy), "n_ma_panel": len(ma), "n_panel": len(panels),
        "img": IMG, "suffix": SUFFIX, "rule": "有买点全部保留（不做均线过滤）+ 均线过关的待买点",
        "panels": panels,
    }
    with open(SUMMARY_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true", help="跳过第 1 步截面重建")
    ap.add_argument("--plot-only", action="store_true", help="只重绘（复用已有 _panels.pkl）")
    a = ap.parse_args()

    for label, script, kind in STEPS:
        if a.plot_only and kind != "plot":
            continue
        if a.skip_build and kind == "build":
            print(f"[skip] {label}（--skip-build）", flush=True)
            continue
        run_step(label, script)

    s = build_summary()
    print(f"\n===== 缠论选股图产出 =====")
    print(f"图文件：{s['img']}  存在={os.path.exists(s['img'])}")
    print(f"数据截至：{s['as_of']}  格数={s['n_panel']}"
          f"（买点 {s['n_buy_panel']} + 均线过关待买点 {s['n_ma_panel']}）")
    for i, p in enumerate(s["panels"], 1):
        print(f"  {i:2d} [{p['组别']}]【{p['层级']}】{p['名称']} #{p['因子名次']:<4d} {p['买点']:<22s} 均线{'√' if p['均线过关'] else '×'}")
    print(f"\n[out] {SUMMARY_JSON}")
    print("CHAN_WEEKLY_OK")


if __name__ == "__main__":
    main()