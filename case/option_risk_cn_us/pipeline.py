# -*- coding: utf-8 -*-
"""option_risk_cn_us 的框架适配（自动生成，可直接改）

由 case/_tools/make_pipeline.py 按入口参数生成：
  · 取数阶段跑原命令（默认不发），dispatch 加 --send 发送
  · 指标用 case/_lib/extract.py 扫 out/*.json 自动抽取，需要更细就改下面的 emit()

阶段：fetch → check → emit → dispatch（顺序见 task.json 的 pipeline）
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

def _hub_root():
    """DataHub 根：环境变量 → 仓库根 datahub_path.py（与 build/scheduler 同口径）→ 同级 checkout。"""
    env = (os.environ.get('DATA_HUB_PATH') or os.environ.get('DATAHUB_DIR')
           or os.environ.get('EDB_REPO') or '')
    if env:
        return env
    cur = HERE
    for _ in range(6):
        p = os.path.join(cur, 'datahub_path.py')
        if os.path.isfile(p):
            ns = {'__file__': p, '__name__': 'datahub_path'}
            try:
                with open(p, encoding='utf-8') as f:
                    exec(compile(f.read(), p, 'exec'), ns)
                return ns['datahub_root']()
            except Exception:                      # noqa: BLE001
                break
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return os.path.normpath(os.path.join(HERE, '..', '..', '..', 'datahub-multi-tier'))


_HUB = _hub_root()
CMD_FETCH = ('PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}%s/options:." '
             'PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." '
             'python3 -u 中美期权风险释放汇总.py' % _HUB)
CMD_SEND = 'PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." python3 -u 中美期权风险释放汇总.py --send'
SEC = 'CN.STOCK'
TIMEOUT = 1800


def fetch(ctx):
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)


def emit(ctx):
    from extract import auto_extract
    points = auto_extract(ctx, sec=SEC)
    if not points:
        ctx.warn("没有抽到可展示的指标", [
            "out/ 下没有可解析的 json 产物",
            "任务还在跑的话，面板这一格会是空的"], "warn")
    return points


def dispatch(ctx):
    # 利率概率变化提醒：挂在本任务里每日执行（阈值 5 个百分点、按数据日幂等）
    ctx.sh("python3 -u rate_prob_alert.py --send", timeout=300, check=False)
    ctx.sh(CMD_SEND, timeout=TIMEOUT, check=False)
