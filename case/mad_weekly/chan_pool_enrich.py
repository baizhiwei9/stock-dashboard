# -*- coding: utf-8 -*-
"""干净重生成：缠论机会记录（含笔底与向上笔确认状态）"""
import os, sys
import pandas as pd
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban")
import chan_fractal

WORK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
pool = pd.read_csv(WORK + "/chan_smc_pool.csv", dtype={"ts_code": str})
NAME = pool.drop_duplicates("ts_code").set_index("ts_code")["名称"].to_dict()

sig, bot, meta = [], [], []
for c in sorted(pool.ts_code):
    p = WORK + "/chan_in_pool/%s.csv" % c
    if not os.path.exists(p):
        print("缺文件", c); continue
    df = pd.read_csv(p, parse_dates=["date"]); n = len(df)
    r = chan_fractal.analyze(df, level="day", base="struct", name=NAME.get(c, c))
    pts = r["结构"]["笔端点"]
    bottoms = {}
    for i, pt in enumerate(pts):
        if pt.get("类型") != "底":
            continue
        nxt = pts[i + 1] if i + 1 < len(pts) else None
        ok = bool(nxt is not None and nxt.get("类型") == "顶")
        bottoms[pt["行号"]] = (str(pt["时间"])[:10], pt["价格"], ok, str(nxt["时间"])[:10] if ok else "", n - 1 - pt["行号"])
        bot.append({"ts_code": c, "名称": NAME.get(c, c), "笔底行号": pt["行号"], "笔底日期": str(pt["时间"])[:10],
                    "笔底价": pt["价格"], "向上笔已确认": ok, "向上笔确认日": (str(nxt["时间"])[:10] if ok else ""),
                    "距末日K线": n - 1 - pt["行号"]})
    for op in r["交易机会"]:
        ln = op.get("行号")
        b = bottoms.get(ln)
        sig.append({"ts_code": c, "名称": NAME.get(c, c), "行号": ln, "日期": str(op.get("时间"))[:10],
                    "价格": op.get("价格"), "方向": op.get("方向"), "类型": op.get("类型"), "说明": op.get("说明"),
                    "距末日K线": n - 1 - (ln or 0),
                    "落在笔底": bool(b is not None),
                    "笔底日期": b[0] if b else "", "笔底价": b[1] if b else "",
                    "向上笔已确认": bool(b[2]) if b else None, "向上笔确认日": b[3] if b else ""})
    meta.append({"ts_code": c, "名称": NAME.get(c, c), "bars": n, "笔数": len(pts),
                 "中枢数": len(r["结构"]["中枢"]), "机会数": len(r["交易机会"])})

pd.DataFrame(sig).to_csv(WORK + "/chan_pool_signals.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(bot).to_csv(WORK + "/chan_pool_bottoms.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(meta).to_csv(WORK + "/chan_pool_meta.csv", index=False, encoding="utf-8-sig")
b = pd.DataFrame(sig); buy = b[b["方向"] == "买入"]
print("标的 %d 只，机会 %d 条（买 %d / 卖 %d）" % (len(meta), len(b), len(buy), len(b) - len(buy)))
print("买点落在笔底: %d/%d；向上笔已确认 %d 条、待确认 %d 条" % (
    int(buy["落在笔底"].sum()), len(buy), int(buy["向上笔已确认"].sum()), int((buy["向上笔已确认"] == False).sum())))
print("笔底合计 %d 个；列名 %s" % (len(bot), list(b.columns)))