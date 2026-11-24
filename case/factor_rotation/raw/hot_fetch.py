# -*- coding: utf-8 -*-
"""补取近期行情与 AI/传媒 相关板块成分：
1) raw/daily_20261029.csv   当日全市场日线（用于把 board 近期收益延到最新交易日）
2) raw/theme_member_ai.csv  AI视频/多模态/数字人/IP经济等 7 个板块成分（补齐 theme_member_all 缺口）
"""
import os, sys, time
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAW)
from ts_session import api  # noqa

# 1) 最新交易日行情
fp = os.path.join(RAW, "daily_20261029.csv")
if not os.path.exists(fp):
    df = api("daily", trade_date="20261029")
    df.to_csv(fp, index=False)
    print(f"[daily 20261029] rows={len(df)}")
else:
    print("[daily 20261029] exists")

# 2) 缺失板块成分
TARGETS = {"886062.TI": "多模态AI", "885950.TI": "虚拟数字人", "886095.TI": "IP经济(谷子经济)",
           "885874.TI": "云游戏", "885934.TI": "元宇宙", "885603.TI": "网络游戏",
           "885457.TI": "手机游戏", "884178.TI": "广告营销"}
rows, fails = [], []
for c, nm in TARGETS.items():
    try:
        d = api("ths_member", ts_code=c)
    except Exception as e:
        fails.append((c, str(e)[:60])); print(f"[FAIL] {c} {nm}", flush=True); continue
    if d is None or not len(d):
        fails.append((c, "empty")); print(f"[FAIL] {c} {nm} empty", flush=True); continue
    d = d.copy(); d["board_code"] = c; d["board_name"] = nm
    rows.append(d[["board_code", "board_name", "con_code", "con_name"]])
    print(f"[OK] {nm} {c} members={len(d)}", flush=True)
    time.sleep(0.4)
out = pd.concat(rows, ignore_index=True)
out.to_csv(os.path.join(RAW, "theme_member_ai.csv"), index=False)
print(f"[saved] theme_member_ai.csv rows={len(out)} boards={out['board_code'].nunique()} fails={fails}")