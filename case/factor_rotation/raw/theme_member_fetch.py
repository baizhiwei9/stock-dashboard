# -*- coding: utf-8 -*-
"""抓取概念板块成分股（近 60 日热门概念 + 用户关注主题）→ raw/theme_member_all.csv"""
import sys, os, time
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts_session import api  # noqa

RAW = os.path.dirname(os.path.abspath(__file__))
hot = pd.read_csv(os.path.join(RAW, "theme_hot_daily.csv"))
codes = set(hot.loc[hot["data_type"] == "概念板块", "ts_code"].astype(str))
names = dict(zip(hot["ts_code"].astype(str), hot["ts_name"]))
# 追加用户关注主题
EXTRA = {"885467.TI": "特斯拉概念", "886002.TI": "机器视觉", "886000.TI": "一体化压铸",
         "885736.TI": "无人驾驶", "881277.TI": "电机", "861022.TI": "汽车零部件"}
for k, v in EXTRA.items():
    codes.add(k); names.setdefault(k, v)
codes = sorted(codes)
print("universe:", len(codes), flush=True)

rows, fails = [], []
for i, c in enumerate(codes):
    try:
        df = api("ths_member", ts_code=c)
    except Exception as e:
        fails.append((c, str(e)[:60])); print(f"[FAIL] {c} {names.get(c)}", flush=True); continue
    if df is None or not len(df):
        fails.append((c, "empty")); continue
    df = df.copy(); df["board_code"] = c; df["board_name"] = names.get(c, c)
    rows.append(df[["board_code", "board_name", "con_code", "con_name"]])
    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{len(codes)} ok, rows={sum(len(r) for r in rows)}", flush=True)
    time.sleep(0.5)
out = pd.concat(rows, ignore_index=True)
out.to_csv(os.path.join(RAW, "theme_member_all.csv"), index=False)
print(f"[saved] theme_member_all.csv rows={len(out)} boards={out['board_code'].nunique()}", flush=True)
print("fails:", fails, flush=True)
