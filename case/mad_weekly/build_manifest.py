# -*- coding: utf-8 -*-
"""生成并校验标注清单：SMC 区间（FVG/OB）与缠论中枢、买点触发价位。

校验方式：用与前次完全相同的规则从 OHLC 重算区间上下沿，与已保存结果逐条比对；
并复核"回踩触及区间 + 收盘未破下沿 + 阳线"的触发条件与区间不被破坏。
"""
import os
import json
import glob

import pandas as pd
import numpy as np

WORK = os.path.dirname(os.path.abspath(__file__))
WIN = 120


def zones_of(df, bos_bar):
    """按既定规则重算 OB 与 FVG 区间。"""
    hi, lo, cl, op = df.high.values, df.low.values, df.close.values, df.open.values
    seg = list(range(max(0, bos_bar - 20), bos_bar + 1))
    ob = None
    for i in reversed(seg):
        if cl[i] < op[i]:
            ob = (lo[i], hi[i], i)
            break
    fvg = None
    for i in reversed(list(range(max(1, bos_bar - 19), bos_bar + 1))):
        if lo[i] > hi[i - 2]:
            fvg = (hi[i - 2], lo[i], i)
            break
    return ob, fvg


merged = pd.read_csv(os.path.join(WORK, "buypoint_merged.csv"), dtype={"ts_code": str})
hits = merged[merged["命中来源"] != "未命中"]
smc = pd.read_csv(os.path.join(WORK, "smc_buypoints.csv"), dtype={"ts_code": str})
smc_live = smc[smc.距末日K线 <= 30]
chan_live = pd.read_csv(os.path.join(WORK, "chan_latest_buypoint.csv"), dtype={"ts_code": str})
chan_live = chan_live[chan_live.当前有效]

rows, errs = [], []
for c in hits.ts_code:
    d = pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % c), parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    name = hits[hits.ts_code == c]["name"].iloc[0]
    # --- SMC 区间 ---
    for _, r in smc_live[smc_live.ts_code == c].iterrows():
        ob, fvg = zones_of(d, int(r.BOS行号))
        z = ob if r.SMC类型 == "OB" else fvg
        # 已保存结果按 4 位小数四舍五入，故按 4 位小数比对
        ok_zone = z is not None and round(z[0], 4) == round(float(r.区间下沿ZD), 4) and round(z[1], 4) == round(float(r.区间上沿ZG), 4)
        u = int(r.买点行号)
        ok_trigger = (d.low[u] <= r.区间上沿ZG) and (d.close[u] > r.区间下沿ZD) and (d.close[u] > d.open[u])
        ok_intact = not (d.close[int(r.BOS行号) + 1:u] < r.区间下沿ZD).any()
        if not (ok_zone and ok_trigger and ok_intact):
            errs.append((c, r.SMC类型, ok_zone, ok_trigger, ok_intact))
        rows.append({"ts_code": c, "name": name, "标注类型": "SMC",
                     "子类": r.SMC类型, "下沿": float(r.区间下沿ZD), "上沿": float(r.区间上沿ZG),
                     "区间起点K线": int(z[2]) if z else None, "BOS行号": int(r.BOS行号),
                     "BOS日期": str(r.BOS日期), "买点行号": u, "买点日期": str(r.买点日期),
                     "买点价格": float(r.买点价格), "距末日K线": int(r.距末日K线),
                     "复核_区间一致": bool(ok_zone), "复核_触发条件": bool(ok_trigger), "复核_区间未破": bool(ok_intact)})
    # --- 缠论中枢 ---
    js = json.load(open(os.path.join(WORK, "chan_out", "%s.json" % c), encoding="utf-8"))
    cl = chan_live[chan_live.ts_code == c]
    if len(cl):
        cl = cl.iloc[0]
        buy_t = pd.Timestamp(cl.买点日期)
        zs = [z for z in js["结构"]["中枢"] if pd.Timestamp(z["起止时间"].split(" ~ ")[1]) <= buy_t]
        z = zs[-1] if zs else (js["结构"]["中枢"][-1] if js["结构"]["中枢"] else None)
        if z:
            s_t, e_t = z["起止时间"].split(" ~ ")
            def idx_of(t):
                m = d.index[d.date.astype(str) == str(pd.Timestamp(t))]
                return int(m[0]) if len(m) else None
            rows.append({"ts_code": c, "name": name, "标注类型": "缠论", "子类": cl.最新买点类型,
                         "下沿": float(z["下沿ZD"]), "上沿": float(z["上沿ZG"]),
                         "区间起点K线": idx_of(s_t), "BOS行号": None, "BOS日期": s_t,
                         "买点行号": int(d.index[d.date.astype(str).str[:10] == str(cl.买点日期)][0]),
                         "买点日期": str(cl.买点日期), "买点价格": float(cl.买点价格),
                         "距末日K线": int(cl.距末日K线), "中枢结束K线": idx_of(e_t),
                         "中枢结束日": e_t,
                         "复核_区间一致": bool(z["上沿ZG"] > z["下沿ZD"]), "复核_触发条件": None,
                         "复核_区间未破": None})
man = pd.DataFrame(rows)
man.to_csv(os.path.join(WORK, "annotate_manifest.csv"), index=False, encoding="utf-8-sig")
print("标注清单 %d 条（SMC %d / 缠论 %d）" % (len(man), (man.标注类型 == "SMC").sum(), (man.标注类型 == "缠论").sum()))
print("复核异常 %d 条：%s" % (len(errs), errs[:5]))
pd.set_option("display.width", 320)
show = man[["name", "标注类型", "子类", "下沿", "上沿", "买点日期", "买点价格", "距末日K线"]]
print(show.to_string(index=False))
# 买入价与区间关系统计
man["买点价相对区间"] = np.where(man.买点价格 > man.上沿, "买点价在区间上沿之上",
                            np.where(man.买点价格 >= man.下沿, "买点价落在区间内", "买点价在区间下沿之下"))
print("\n买点价位置分布:", man.买点价相对区间.value_counts().to_dict())