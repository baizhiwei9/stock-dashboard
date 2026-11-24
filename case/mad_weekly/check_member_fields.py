# -*- coding: utf-8 -*-
import sys, csv, collections
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch
idx = {r["ts_code"]: r for r in csv.DictReader(open("ths_index_all.csv", encoding="utf-8-sig"))
       if r["type"]=="N" and r["ts_code"][:3] in ("885","886")}
for c in ("885887.TI", "885311.TI", "885300.TI"):
    f, items = fetch("ths_member", {"ts_code": c}, fields="ts_code,con_code,con_name,is_new,in_date,out_date,weight")
    f2, items2 = fetch("ths_member", {"ts_code": c})
    print(c, idx[c]["name"], "| count字段:", idx[c]["count"], "| 带字段行数:", len(items), "| 默认行数:", len(items2), "| fields:", f)
    print("   样本:", items[:3])
    if f and "is_new" in f:
        j = f.index("is_new")
        print("   is_new分布:", collections.Counter(x[j] for x in items))
# 逐个概念对比 count 字段与实际行数
rows = list(csv.DictReader(open("concept_members.csv", encoding="utf-8-sig")))
cnt = collections.Counter(r["概念代码"] for r in rows)
diffs = [(c, idx[c]["count"], cnt.get(c,0)) for c in idx]
bad = [(c, int(float(a)), b) for c,a,b in diffs if abs(float(a)-b) > 0.5]
print("count字段与实际行数不符的概念数: %d / %d" % (len(bad), len(idx)))
print("样例:", bad[:12])
