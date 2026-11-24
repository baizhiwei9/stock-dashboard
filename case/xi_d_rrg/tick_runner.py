# -*- coding: utf-8 -*-
"""scheduler 统一入口: 限时拉数据 -> 数据齐则自动出报告并推飞书 (完全幂等)
阶段1: fetch_final.py    (成长价值个股数据, 预算 priority)
阶段2: rrg_fetch.py      (RRG 行业数据, 预算 priority)
阶段3: 两者数据齐 -> auto_full_report.py (成长+RRG 对比报告) 并推飞书
用法: python tick_runner.py [抓取预算秒数=1500]
退出码: 0=完成或无需动作; 2=数据未齐(本次只抓取); 3=推送待重试/fail
"""
import os, sys, subprocess, time, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 1500.0
T_START = time.time()

# 避开其他tushare任务的时段(上海16-20点: 结构因子16:00/分析力学18:00/TDA+Hodge19:00/物理几何20:00)
# 仅工作日有这些任务; 周末只有20:00物理几何(每天)+曼宁资金流(周日20:00) -> 周末只避20点整时时
_now_utc = datetime.datetime.utcnow()
_SH = (_now_utc + datetime.timedelta(hours=8)).hour
_WD = _now_utc.weekday()  # 0=周一
if (16 <= _SH < 21 and _WD < 5) or (_SH == 20 and _WD >= 5):
    print(f'SKIP_WINDOW hour={_SH} weekday={_WD} (避开其他tushare任务)', flush=True)
    sys.exit(0)


def budget_remain():
    return max(60, BUDGET - (time.time() - T_START))


def run_script(name, script, timeout):
    r = subprocess.run([sys.executable, os.path.join(BASE, script), str(timeout)],
                       cwd=BASE, capture_output=True, text=True, timeout=timeout + 300)
    return r


def needs_rrg_data():
    """RRG 数据是否齐全: 行业>=30 且 day 已覆盖 2022 起回测(>=200天且最后日期>=20220101)"""
    import glob
    ind = glob.glob(os.path.join(BASE, 'data_rrg', 'industry', '*.csv'))
    days = sorted(glob.glob(os.path.join(BASE, 'data_rrg', 'day', '*.csv')))
    if len(ind) < 30 or len(days) < 200:
        return False
    last = os.path.basename(days[-1])[:8]
    return last >= '20220101'


def needs_gy_data():
    """成长价值数据是否齐全: fetch_done 计数 == 1177*4"""
    import json, os as _os
    fp = _os.path.join(BASE, 'data', 'fetch_done.json')
    if not _os.path.exists(fp):
        return False
    try:
        d = json.load(open(fp))
        return len(d) >= 1177 * 4
    except Exception:
        return False


def rrg_ready_check():
    import glob
    ind = glob.glob(os.path.join(BASE, 'data_rrg', 'industry', '*.csv'))
    days = sorted(glob.glob(os.path.join(BASE, 'data_rrg', 'day', '*.csv')))
    return len(ind), len(days)


print(f'=== tick_runner start {time.strftime("%Y-%m-%d %H:%M:%S")} budget={BUDGET:.0f}s ===', flush=True)

# ---- 阶段1: 成长价值数据 (预算 65%; rrg主要靠夜间轮次推进) ----
b1 = max(60, budget_remain() * 0.65)
r = run_script('成长价值抓取', 'fetch_final.py', b1)
progress_lines = [l for l in r.stdout.strip().splitlines() if 'total_done' in l or 'pending' in l or 'BUDGET' in l or 'ALL DONE' in l or 'ERROR' in l]
if progress_lines:
    print('\n'.join(progress_lines[-5:]), flush=True)
gy_ready = needs_gy_data()

# ---- 阶段2: RRG 数据 (剩余预算, 成长价值齐则全额) ----
print(f'budget remain for rrg: {budget_remain():.0f}s', flush=True)
r2 = run_script('RRG抓取', 'rrg_fetch.py', budget_remain())
pr2 = [l for l in r2.stdout.strip().splitlines() if 'industry' in l.lower() or 'const' in l.lower() or 'daily' in l.lower() or 'BUDGET' in l or 'DONE' in l or 'ERR' in l]
if pr2:
    print('\n'.join(pr2[-5:]), flush=True)
rrg_ready = needs_rrg_data()

# ---- 阶段3: RRG数据齐(含2022起) -> 对比报告 (gy未齐自动6信号版, 齐则9信号版) ----
if rrg_ready:
    print(f'RRG_DATA_READY gy_ready={gy_ready}, running compare report...', flush=True)
    r3 = subprocess.run([sys.executable, os.path.join(BASE, 'auto_compare_report.py')],
                        cwd=BASE, capture_output=True, text=True, timeout=3600)
    print(r3.stdout[-2000:], flush=True)
    if r3.returncode not in (0,):  # 3=RRG未齐(重检),4=推送失败
        print(f'compare_report exit={r3.returncode}', flush=True)
        sys.exit(r3.returncode)
    print('TICK_OK', flush=True)
else:
    print(f'DATA_NOT_READY gy={gy_ready} rrg={rrg_ready}', flush=True)
    sys.exit(2)