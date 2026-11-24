# -*- coding: utf-8 -*-
"""修正版 SMC：输出仍有效的买点清单 + 逐区间失效原因。"""
import os
import pandas as pd
import numpy as np

WORK = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(WORK, "smc_zones_v2.csv"), dtype={"ts_code": str})
old = pd.read_csv(os.path.join(WORK, "smc_buypoints.csv"), dtype={"ts_code": str})
old_live = old[old.距末日K线 <= 30]

# 失效原因归类
def reason(r):
    if r.修正后仍成立:
        return "有效"
    if r.收盘破下沿:
        return "形成后被收盘价跌破下沿（结构破坏）"
    if r.被完整填补:
        return "缺口／区间已被价格完整填补（失效）"
    return "首次触碰不满足触发条件（收阴或收盘未站上下沿）"

df["失效原因"] = df.apply(reason, axis=1)
valid = df[df.修正后仍成立].copy()
valid.to_csv(os.path.join(WORK, "smc_valid_all.csv"), index=False, encoding="utf-8-sig")

live = valid[valid.距末日K线 <= 30].copy()
live["is_old"] = live.apply(lambda r: bool(((old_live.ts_code == r.ts_code) & (old_live.SMC类型 == r.类型)
                                           & (old_live.买点日期 == r.买点日期)).any()), axis=1)
live.to_csv(os.path.join(WORK, "smc_valid_live.csv"), index=False, encoding="utf-8-sig")

print("区间候选 %d 个：有效买点 %d 个，其中最近 30 根内 %d 个（覆盖 %d 只）"
      % (len(df), len(valid), len(live), live.ts_code.nunique()))
print("\n失效原因分布（全部历史区间）:")
print(df[df.失效原因 != "有效"].失效原因.value_counts().to_string())

S = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
NAME = S.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()
old_by = old_live.groupby("ts_code").apply(
    lambda g: ";".join("%s %s" % (r.SMC类型, r.买点日期) for _, r in g.iterrows()), include_groups=False).to_dict()
new_by = live.groupby("ts_code").apply(
    lambda g: ";".join("%s %s%s" % (r.类型, r.买点日期, "" if r.is_old else "(新)") for _, r in g.iterrows()),
    include_groups=False).to_dict()
LATEST = live.sort_values("买点日期").groupby("ts_code").tail(1).set_index("ts_code")

rows = []
for c in sorted(set(old_by) | set(new_by)):
    tag = "保留" if c in old_by and c in new_by else ("仅修正后新增" if c in new_by else "被剔除")
    lr = LATEST.loc[c] if c in LATEST.index else None
    rows.append({"代码": c, "名称": NAME.get(c, c), "修正前SMC买点": old_by.get(c, "—"),
                 "修正后SMC买点": new_by.get(c, "—"), "对比": tag,
                 "最新有效买点": ("%s %s 区间%.2f~%.2f 跌到%.2f 买点价%.2f"
                              % (lr.类型, lr.买点日期, lr.下沿ZD, lr.上沿ZG, lr["跌到价(触发日最低)"], lr["买点价(收盘)"])
                              if lr is not None else "—")})
t = pd.DataFrame(rows)
t.to_csv(os.path.join(WORK, "smc_fix_compare.csv"), index=False, encoding="utf-8-sig")
pd.set_option("display.width", 320)
print("\n与修正前的逐只对比（最近 30 根内）:")
print(t.to_string(index=False))
print("\n被剔除的标的（原 22 只中）: %s" % "、".join(t[t.对比 == "被剔除"].名称))
print("案例复核 绿盟科技:", new_by.get("300369.SZ", "—"), "（原为 OB 2026-10-28;FVG 2026-10-28;OB 2026-09-16）")