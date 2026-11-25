#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美债监控周报统一入口（已合并 ICE MOVE 逐周记录）。

每周日 09:00 由定时任务触发，固定执行：数据均为海外（FRED/美股/CNBC），
不依赖 A股交易日；A股周末休市不构成跳过条件。

链路（按序，任一步非零退出即中止；MOVE 记录失败仅告警不阻断）：
  [0] record_move_weekly.py        → 本周 MOVE 值落盘（幂等：当日已记录则 [SKIP]）
  [1] treasury_data_fetch.py       → 七板块统一数据层（H.4.1/MSPD/滚偿/发债/久期/FRED/FOMC）
  [2] treasury_maturity_monitor.py → 存量久期与滚偿结构
  [3] rate_path_judgment.py        → 利率路径（方向/时间点/幅度）
  [4] treasury_buyback_monitor.py  → 回购公告与结果
  [5] liquidity_metrics.py         → 6 核心指标 + 阶段 + 红黄预警（流动性速览）
  [6] gen_liquidity_front.py       → 生成置顶栏目数据（合并 metrics）
  [7] credit_spread_alert.py       → 信用利差预警（AA/BB/CCC 三档 OAS 两两缺口，
                                     任一达一年 80% 分位则发邮件；非致命步骤）
  [8] send_treasury_card.py --send → 构造并推送飞书交互卡片

用法：
  python3 run_treasury_weekly.py             # 完整链路，末尾推送飞书
  python3 run_treasury_weekly.py --no-send   # 完整链路但不推送（核验/试跑）
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MACROLIQ = os.path.normpath(os.path.join(HERE, "..", "ses_gffe5ee1d17e02ffeBBPuFJhbMDuVqs", "macroliq_fetcher"))
PY = sys.executable or "python3"


def run_step(name, cmd, cwd=None, fatal=True):
    print("\n[STEP] %s" % name, flush=True)
    print("       %s" % " ".join(cmd), flush=True)
    try:
        proc = subprocess.run(cmd, cwd=cwd or HERE, text=True, timeout=1500)
    except subprocess.TimeoutExpired:
        print("[FAIL] %s 超时(1500s)" % name, flush=True)
        if fatal:
            sys.exit(1)
        return False
    if proc.returncode != 0:
        print("[FAIL] %s 退出码=%d" % (name, proc.returncode), flush=True)
        if fatal:
            sys.exit(1)
        return False
    print("[OK] %s" % name, flush=True)
    return True


def main():
    no_send = "--no-send" in sys.argv

    # [0] MOVE 记录（非致命：CNBC 偶发失败不阻断整条周报，卡片会标记缺口）
    run_step("MOVE 逐周记录 (record_move_weekly.py)", [PY, os.path.join(MACROLIQ, "record_move_weekly.py")],
             cwd=MACROLIQ, fatal=False)

    run_step("数据层 treasury_data_fetch.py", [PY, "treasury_data_fetch.py", "--json", "treasury_data_output.json"])
    run_step("久期滚偿 treasury_maturity_monitor.py", [PY, "treasury_maturity_monitor.py", "--json", "treasury_maturity_output.json"])
    run_step("利率路径 rate_path_judgment.py", [PY, "rate_path_judgment.py", "--json", "rate_path_output.json"])
    run_step("回购监控 treasury_buyback_monitor.py", [PY, "treasury_buyback_monitor.py", "--json", "treasury_buyback_output.json"])
    run_step("流动性指标 liquidity_metrics.py", [PY, "liquidity_metrics.py"])
    run_step("置顶栏目 gen_liquidity_front.py", [PY, "gen_liquidity_front.py"])

    # [7] 信用利差预警（非致命：FRED 取数偶发失败不阻断整条周报；
    #     触发时自行发送邮件；--no-send 试跑时不发信）
    cs_cmd = [PY, "credit_spread_alert.py", "--json", "credit_spread_output.json"]
    if not no_send:
        cs_cmd.append("--send")
    run_step("信用利差预警 credit_spread_alert.py", cs_cmd, fatal=False)

    if no_send:
        run_step("构造卡片（不推送）send_treasury_card.py", [PY, "send_treasury_card.py"])
    else:
        run_step("推送飞书 send_treasury_card.py --send", [PY, "send_treasury_card.py", "--send"])

    print("\n[完成] 美债监控周报链路%s" % ("（未推送，仅构造）" if no_send else "全部执行完毕"), flush=True)


if __name__ == "__main__":
    main()