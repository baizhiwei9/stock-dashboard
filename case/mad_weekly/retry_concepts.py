# -*- coding: utf-8 -*-
import os, sys, csv, time
WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch
idx = {r["ts_code"]: r["name"] for r in csv.DictReader(open(os.path.join(WORK,"ths_index_all.csv"), encoding="utf-8-sig"))
       if r["type"]=="N" and r["ts_code"][:3] in ("885","886")}
have = set()
rows = list(csv.DictReader(open(os.path.join(WORK,"concept_members.csv"), encoding="utf-8-sig")))
for r in rows: have.add(r["概念代码"])
missing = [c for c in idx if c not in have]
print("缺失概念:", missing)
add = []
for c in missing:
    for attempt in range(5):
        try:
            f, items = fetch("ths_member", {"ts_code": c})
            print(c, idx[c], "rows", len(items))
            for x in items:
                add.append({"概念代码": c, "概念名称": idx[c], "股票代码": x[1], "股票名称": x[2] if len(x)>2 else ""})
            time.sleep(1.5)
            break
        except Exception as e:
            print(c, "retry", attempt, type(e).__name__, e)
            time.sleep(8 + 5*attempt)
with open(os.path.join(WORK,"concept_members.csv"), "a", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["概念代码","概念名称","股票代码","股票名称"])
    w.writerows(add)
print("补入 rows=%d 总概念数=%d" % (len(add), len(have)+len(missing)))
