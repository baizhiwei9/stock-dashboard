# -*- coding: utf-8 -*-
"""抓取申万三级行业全集合日线（含 close/high/low/amount/vol/pe/pb/total_mv），断点续传"""
import re, os, sys, json, time, datetime
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
src = open('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ffb350a15ffeoIqfEMXyfUcuT9/ts_config.py').read()
TOK = re.search(r"TS_TOKEN\s*=\s*[\"']([^\"']+)[\"']", src).group(1)
URL = re.search(r"TS_HTTP_URL\s*=\s*[\"']([^\"']+)[\"']", src).group(1)
START = "20230601"   # 覆盖~800交易日,可算750日Cstd及252动量+120轮动
END = os.environ.get("END_DATE", "20260930")
S = requests.Session()

def call(api_name, params, retry=1):
    last = None
    for i in range(retry + 1):
        try:
            r = S.post(URL, json={"api_name": api_name, "token": TOK, "params": params, "fields": ""}, timeout=30)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]["fields"], j["data"]["items"]
            last = f"code={j.get('code')} msg={j.get('msg')}"
        except Exception as e:
            last = repr(e)[:120]
        time.sleep(2 + i * 3)
    raise RuntimeError(f"{api_name} {params} failed: {last}")

def get_classify(level):
    f, it = call("index_classify", {"level": level, "src": "SW2021"})
    return [dict(zip(f, r)) for r in it]

PROG = os.path.join(DATA, "fetch_progress.json")
OUT_CSV = os.path.join(DATA, "sw_l3_full.csv")

l1 = get_classify("L1"); l2 = get_classify("L2"); l3 = get_classify("L3")
json.dump(l1, open(os.path.join(DATA, "L1_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(l2, open(os.path.join(DATA, "L2_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(l3, open(os.path.join(DATA, "L3_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
l2_by_code = {m["industry_code"]: m for m in l2}
l1_by_code = {m["industry_code"]: m for m in l1}

def fetch_daily(code):
    try:
        f, it = call("sw_daily", {"ts_code": code, "start_date": START, "end_date": END})
    except Exception:
        return None, None
    if not it:
        return None, None
    df = pd.DataFrame(it, columns=f)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df, f

def fetch_daily_from(code, used_code, start_yyyymmdd):
    try:
        f, it = call("sw_daily", {"ts_code": used_code, "start_date": start_yyyymmdd, "end_date": END})
    except Exception:
        return None, None
    if not it:
        return None, None
    df = pd.DataFrame(it, columns=f)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df, f

done = {}
if os.path.exists(PROG):
    done = json.load(open(PROG, encoding="utf-8"))
rows_all = []
# 基线：已有 sw_l3_full.csv（增量追加，避免重复运行清空）
old_rows = []
used_last = {}
if os.path.exists(OUT_CSV):
    old_df = pd.read_csv(OUT_CSV)
    old_rows = old_df.to_dict("records")
    get = lambda r, k: r.get(k)
    for r in old_rows:
        u = get(r, "code")
        d = get(r, "date")
        if u and d and (u not in used_last or d > used_last[u]):
            used_last[u] = d
print(f"[incr] baseline rows={len(old_rows)} used={len(used_last)}", flush=True)

def _fmt_end(date_str):
    return date_str.replace("-", "")

for m in l3:
    code = m["index_code"]
    rec = done.get(code)
    if rec and rec.get("status") == "ok":
        # 已完成：仅当 last < END 时增量拉取（统一 YYYY-MM-DD 比较）
        last = str(rec.get("last", ""))
        end_dash = f"{END[:4]}-{END[4:6]}-{END[6:8]}"
        if last >= end_dash:
            continue
        used_, level_ = rec.get("used"), rec.get("level")
        st = (datetime.date.fromisoformat(last) + datetime.timedelta(days=1)).strftime("%Y%m%d")
        df, f = fetch_daily_from(code, used_, st)
        if df is None or len(df) == 0:
            print(f"[incr] {m['industry_name']} no new rows after {last}", flush=True)
            continue
        level, used = level_, used_
        # 追加新行
        for _, r in df.iterrows():
            rows_all.append({"date": r["trade_date"].strftime("%Y-%m-%d"), "close": r.get("close"),
                             "high": r.get("high"), "low": r.get("low"), "amount": r.get("amount"),
                             "vol": r.get("vol"), "pe": r.get("pe"), "pb": r.get("pb"),
                             "total_mv": r.get("total_mv"), "float_mv": r.get("float_mv"),
                             "name": m["industry_name"], "code": used, "level": level, "orig_code": code})
        done[code] = {"status": "ok", "name": m["industry_name"], "used": used, "level": level,
                      "rows": rec.get("rows", 0) + len(df), "last": str(df["trade_date"].iloc[-1].date())}
        if len(done) % 50 == 0:
            print(f"[incr] ... {len(done)}/{len(l3)}", flush=True)
        time.sleep(0.5)
        continue
    df, f = fetch_daily(code)
    level, used = "L3", code
    if df is None or len(df) < 100:
        p2 = l2_by_code.get(m.get("parent_code"))
        if p2:
            df, f = fetch_daily(p2["index_code"]); level, used = "L2", p2["index_code"]
        if df is None or len(df) < 100:
            p1c = None
            for m2 in l2:
                if m2["industry_code"] == m.get("parent_code"):
                    p1c = l1_by_code.get(m2.get("parent_code")); break
            if p1c:
                df, f = fetch_daily(p1c["index_code"]); level, used = "L1", p1c["index_code"]
    if df is None or len(df) < 100:
        done[code] = {"status": "fail", "name": m["industry_name"]}
        json.dump(done, open(PROG, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[FAIL] {m['industry_name']} ({code})", flush=True)
        continue
    df = df.sort_values("trade_date").drop_duplicates("trade_date")
    for _, r in df.iterrows():
        rows_all.append({"date": r["trade_date"].strftime("%Y-%m-%d"), "close": r.get("close"),
                         "high": r.get("high"), "low": r.get("low"), "amount": r.get("amount"),
                         "vol": r.get("vol"), "pe": r.get("pe"), "pb": r.get("pb"),
                         "total_mv": r.get("total_mv"), "float_mv": r.get("float_mv"),
                         "name": m["industry_name"], "code": used, "level": level, "orig_code": code})
    done[code] = {"status": "ok", "name": m["industry_name"], "used": used, "level": level,
                  "rows": len(df), "last": str(df["trade_date"].iloc[-1].date())}
    json.dump(done, open(PROG, "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(0.5)
    if len(done) % 30 == 0:
        mt = pd.DataFrame(old_rows + rows_all).drop_duplicates(['date', 'code'], keep='last')
        mt.to_csv(OUT_CSV, index=False)
        print(f"... {len(done)}/{len(l3)}", flush=True)

# 合并基线 + 本次增量，按 (date, code) 去重后写回
merged = pd.DataFrame(old_rows + rows_all)
if len(merged):
    merged = merged.drop_duplicates(['date', 'code'], keep='last').sort_values(['date', 'code'])
merged.to_csv(OUT_CSV, index=False)
from collections import Counter
ok = [v for k,v in done.items() if v.get("status")=="ok"]
print("DONE 三级:", len(ok), "级别分布:", Counter(v['level'] for v in ok))
if ok:
    print("最新日期:", min(v['last'] for v in ok), "总行数:", len(merged))
