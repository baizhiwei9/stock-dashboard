# -*- coding: utf-8 -*-
import sys, collections
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch
import csv
f, items = fetch("ths_index", {"exchange": "A"})
rows = [dict(zip(f, r)) for r in items]
pref = collections.Counter(r["ts_code"][:3] for r in rows)
print("prefix分布:", dict(sorted(pref.items())))
for p in sorted(pref):
    sample = [r for r in rows if r["ts_code"].startswith(p)][:6]
    print(p, "n=%d" % pref[p], [(r["ts_code"], r["name"], r["type"]) for r in sample])
with open("ths_index_all.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
print("saved ths_index_all.csv rows=%d" % len(rows))
