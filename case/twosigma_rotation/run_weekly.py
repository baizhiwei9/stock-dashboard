# -*- coding: utf-8 -*-
"""Two Sigma 行业动量 - 每周日一键链路
数据增量更新 → 因子/预算/仓位 → 组装 → 生成报告 → 推送飞书（带消息头）
用法: python run_weekly.py [--no-push] [--data-date YYYY-MM-DD] [--full]
  --no-push   只生成报告不推送飞书
  --data-date 显式指定数据截至日（默认取 sw_l3 最新日期）
  --full      全量重拉行业日线（默认增量）
"""
import os, sys, subprocess, argparse, json

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PY = sys.executable


def sh(cmd, env=None, timeout=None):
    print(f"\n>>> {cmd}", flush=True)
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, shell=True, env=e, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败({r.returncode}): {cmd}")
    return r


def latest_l3_date():
    import pandas as pd
    df = pd.read_csv(os.path.join(DATA, "sw_l3_full.csv"), usecols=["date"])
    return df["date"].max()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--data-date", default=None)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    d = args.data_date or latest_l3_date()
    END = d.replace("-", "")
    END_YM = f"{END[:4]}-{END[4:6]}"
    env = {"END_DATE": END, "END_YM": END_YM, "END_DATE_T": d}
    print(f"data_date = {d}  END_DATE={END} END_YM={END_YM}", flush=True)

    # ---------- 1. 数据增量更新 ----------
    if args.full:
        sh(f"{PY} fetch_l3_full.py 2>&1 || true", env=env, timeout=3600)  # 仍走增量(若存在旧进度)
    sh(f"{PY} fetch_l3.py", env=env, timeout=3600)          # 申万三级日线(增量)
    sh(f"{PY} scripts/fetch_cdb_ts.py", env=env, timeout=600)  # 国开收益率(按L3日期增量)
    sh(f"{PY} scripts/fetch_gz.py", env=env, timeout=300)      # 国债曲线(全量重拉,~1s)
    sh(f"{PY} scripts/fetch_ncd_fix.py", env=env, timeout=600) # 中票AAA(增量月份)
    sh(f"{PY} scripts/fetch_dr007.py", env=env, timeout=300)   # DR007定盘(合并去重)
    sh(f"{PY} scripts/build_rates.py", env=env, timeout=120)   # 合并利率 → rates_merged
    sh(f"{PY} scripts/fetch_hs300.py", env=env, timeout=900)   # 沪深300 + 资金流(25日) + 估值
    sh(f"{PY} scripts/fetch_cny_pmi.py", env=env, timeout=600) # CNY中间价 + PMI(全量重拉)
    sh(f"{PY} scripts/fetch_research2.py", env=env, timeout=600)  # 东财研报近200日

    # ---------- 2. 因子/预算/仓位 ----------
    sh(f"{PY} scripts/compute_factors.py")
    sh(f"{PY} scripts/compute_budget.py")
    sh(f"{PY} scripts/compute_budget2.py")

    # ---------- 3. 组装 + 生成报告 ----------
    sh(f"{PY} scripts/assemble_pkg.py")
    sh(f"{PY} scripts/build_report.py")

    # ---------- 4. 推送飞书（带消息头） ----------
    if args.no_push:
        print("\n[skip] --no-push 已跳过飞书推送", flush=True)
        return
    sh(f"{PY} scripts/push_feishu.py", env=env, timeout=300)


if __name__ == "__main__":
    main()