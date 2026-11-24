# -*- coding: utf-8 -*-
"""T2：取候选股的 PE(TTM) 与一致预期（FY1/FY2 预测 PE、FY1 净利润同比、预测 ROE）。

- 输入：narrow_pool.csv（79 只）
- 输出：raw/estimates_b*.json、raw/pe_b*.json（保留原始响应）与 valuation_growth.csv
"""
import json, os, subprocess, sys
import pandas as pd

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
CD = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/plugins/official/financial-investment-plugin/skills/commons/ifind-data-service"
CLI = CD + "/finance_api.py"
RAW = WS + "/raw"
os.makedirs(RAW, exist_ok=True)

pool = pd.read_csv(WS + "/narrow_pool.csv", dtype=str)
codes = list(pool.ts_code)
B = 20
batches = [codes[i:i+B] for i in range(0, len(codes), B)]

def call(fn, params, out):
    p = subprocess.run([sys.executable, CLI, "--function", fn, "--params", json.dumps(params, ensure_ascii=False)],
                       capture_output=True, text=True, timeout=180)
    txt = p.stdout.strip()
    if not txt:
        print("空响应", fn, out, p.stderr[-200:]); return None
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    return json.loads(txt)

est_rows, pe_rows = [], []
for i, b in enumerate(batches, 1):
    r = call("get_analyst_estimates",
             {"symbols": b, "fields": ["fore_pe_fy1", "fore_pe_fy2", "fore_np_yoy", "fore_roe_fy1", "fore_roe_fy2"],
              "as_of": "2026-10-30"}, "%s/estimates_b%d.json" % (RAW, i))
    if r:
        print("estimates b%d: status=%s rows=%d" % (i, r.get("status"), len(r.get("rows", []))))
        est_rows += r.get("rows", [])
    q = call("get_price_history", {"symbols": b, "start": "2026-10-30", "end": "2026-10-30",
                                   "fields": ["close", "pe_ttm"]}, "%s/pe_b%d.json" % (RAW, i))
    if q:
        print("quotes   b%d: status=%s rows=%d" % (i, q.get("status"), len(q.get("rows", []))))
        pe_rows += q.get("rows", [])

E = pd.DataFrame(est_rows)
P = pd.DataFrame(pe_rows)
if not E.empty:
    E = E[[c for c in E.columns if c != "_provenance"]]
if not P.empty:
    P = P[[c for c in P.columns if c != "_provenance"]]
m = pool.merge(P[["thscode", "close", "pe_ttm"]], left_on="ts_code", right_on="thscode", how="left").drop(columns=["thscode"])
m = m.merge(E, left_on="ts_code", right_on="thscode", how="left").drop(columns=["thscode"])
m.to_csv(WS + "/valuation_growth.csv", index=False, encoding="utf-8-sig")
print("\n落盘 valuation_growth.csv：%d 行" % len(m))
print("PE(TTM) 覆盖 %d/%d；fore_pe_fy1 覆盖 %d；fore_pe_fy2 覆盖 %d；fore_np_yoy 覆盖 %d" % (
    int(m.pe_ttm.notna().sum()), len(m), int(m.get("fore_pe_fy1", pd.Series(dtype=float)).notna().sum()),
    int(m.get("fore_pe_fy2", pd.Series(dtype=float)).notna().sum()), int(m.get("fore_np_yoy", pd.Series(dtype=float)).notna().sum())))
print(m[["ts_code", "名称", "命中", "pe_ttm", "fore_pe_fy1", "fore_pe_fy2", "fore_np_yoy", "fore_roe_fy1"]].head(8).to_string(index=False))