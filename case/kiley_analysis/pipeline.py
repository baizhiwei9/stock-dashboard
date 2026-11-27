# -*- coding: utf-8 -*-
"""kiley_analysis 的框架适配

阶段：fetch（取数+算指标+出报告）→ check（产物检查）→ emit（落指标仓）

数据源全部是 Michael T. Kiley 公开站点的产物（公开可下载，无需授权）：
  · termprem.csv       10Y 收益率 + 四种期限溢价 + 两个长期锚 + SEP 长期利率
  · fcig/data.json     FCI-G 日频重建（含 asof 分项日期）
  · monetary_policy_surprises/ 页内 data-payload（期货隐含路径 + 期权概率密度）

口径提醒：只搬数值与分位，不做方向判断；每个数都带 obs_date 与来源。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CMD_FETCH = "python3 -u run_fetch.py"
SEC = "KILEY"
TIMEOUT = 600


def fetch(ctx):
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=False)


def emit(ctx):
    """把 out/kiley_points.json 里的 points 交给框架落指标仓。"""
    p = os.path.join(HERE, "out", "kiley_points.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        ctx.warn("读不到指标产物", [str(e), p], "warn")
        return []
    pts = d.get("points") or []
    if not pts:
        ctx.warn("没有抽到可展示的指标", ["out/kiley_points.json 的 points 为空"], "warn")
    return pts