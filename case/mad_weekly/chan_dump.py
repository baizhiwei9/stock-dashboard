# -*- coding: utf-8 -*-
"""保存缠论全量结构输出，并为有效买点附上依赖中枢。"""
import os
import sys
import json
import glob

WORK = os.path.dirname(os.path.abspath(__file__))
SK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, SK)
import pandas as pd
import chan_fractal

OUT = os.path.join(WORK, "chan_out")
os.makedirs(OUT, exist_ok=True)
s = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
NAME = s.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()

for p in sorted(glob.glob(os.path.join(WORK, "chan_in", "*.csv"))):
    c = os.path.basename(p)[:-4]
    df = pd.read_csv(p, parse_dates=["date"])
    r = chan_fractal.analyze(df, level="day", base="struct", name=NAME.get(c, c))
    with open(os.path.join(OUT, "%s.json" % c), "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, default=str)
print("已保存 %d 只结构结果" % len(glob.glob(os.path.join(OUT, "*.json"))))

# 有效买点附中枢
live = pd.read_csv(os.path.join(WORK, "chan_latest_buypoint.csv"), dtype={"ts_code": str})
live = live[live.当前有效]
rows = []
for _, x in live.iterrows():
    c = x.ts_code
    r = json.load(open(os.path.join(OUT, "%s.json" % c), encoding="utf-8"))
    zs = r["结构"]["中枢"]
    bar = None
    df = pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % c), parse_dates=["date"])
    idx = int(df.index[df["date"].astype(str) == str(x.买点日期)].min()) if (df["date"].astype(str) == str(x.买点日期)).any() else None
    cand = [z for z in zs if z.get("起止时间") and str(z["起止时间"][1]) <= str(x.买点日期)]
    z = cand[-1] if cand else (zs[-1] if zs else None)
    rows.append({"ts_code": c, "name": x["name"], "类型": x.最新买点类型, "日期": x.买点日期,
                 "价格": x.买点价格, "末日收盘": x.末日收盘,
                 "中枢ZD": z.get("ZD") if z else None, "中枢ZG": z.get("ZG") if z else None,
                 "中枢区间": "%s~%s" % (z.get("起止时间")[0], z.get("起止时间")[1]) if z else "—",
                 "距末日K线": x.距末日K线})
z = pd.DataFrame(rows)
z.to_csv(os.path.join(WORK, "chan_live_with_zhongshu.csv"), index=False, encoding="utf-8-sig")
pd.set_option("display.width", 260)
print(z.round(3).to_string(index=False))
# 抽查襄阳轴承最近 15 根
d = pd.read_csv(os.path.join(WORK, "chan_in", "000678.SZ.csv"), parse_dates=["date"])
print("\n襄阳轴承最近 15 根（前复权）:")
print(d.tail(15)[["date", "open", "high", "low", "close"]].round(2).to_string(index=False))