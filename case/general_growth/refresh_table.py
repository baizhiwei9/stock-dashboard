# -*- coding: utf-8 -*-
"""对照表与依据数据刷新：6 方向的拓扑快照（冻结）+ PE/增速（刷新）+ 主力净流入（刷新）

- 首次运行：从 merged_verdict.json / industry_moneyflow.json 播种 config.json 的
  candidates / table / rationale
- 之后每次运行：刷新各方向候选池的一致预期（2027E PE、增速中位）与近 5 个交易日主力净流入
用法: python3 refresh_table.py [--task-dir DIR]
"""
import argparse
import json
import os
import statistics as stc
import sys
import time

import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs")
sys.path.insert(0, os.path.join("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace",
                                "ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG", "pylibs"))
import ts_config  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()
MIN_ORGS = 6

CANDIDATES = {
    "覆铜板/电子布/CCL": ["600183.SH", "688519.SH", "603186.SH", "603002.SH", "002636.SZ"],
    "电网设备与变压器": ["002028.SZ", "600089.SH", "600312.SH", "601179.SH"],
    "燃气轮机": ["600875.SH", "603308.SH", "002353.SZ"],
    "铝/电解铝": ["601600.SH", "000807.SZ", "000933.SZ", "002532.SZ"],
    "创新药": ["600276.SH", "688235.SH", "300765.SZ"],
    "化学原料": ["600309.SH", "600426.SH", "002648.SZ"],
}

RATIONALE = {
    "电网设备与变压器": [
        "增长与订单：1—9 月变压器累计出口 92.20 亿美元、同比 +28.0%（海关口径）；"
        "思源电气前三季营收 177.62 亿元（+28.46%）、新增订单增速超全年 25% 的收入目标；"
        "特变电工上半年国际市场产品签约 16.18 亿美元、同比 +44%。",
        "估值匹配：行业中位 2027E PE 约 {pe} 倍，对应 2027E 净利增速中位约 {g}%，估值倍数低于增速。",
        "拓扑与资金：输变电／配电／电网自动化设备 60 日邻居数均为 0（≤3 判扩散），走势与大盘脱钩；"
        "近 {days} 个交易日主力净流入 {mf} 亿元（净占比 {rate}%）。"],
    "铝/电解铝": [
        "增长：中国铝业前三季归母 179.16 亿元（+64.83%）；云铝股份 116.12 亿元（+164.01%）；"
        "国内产能天花板 4500 万吨、运行率 98.68%。",
        "估值匹配：行业中位 2027E PE 约 {pe} 倍，但卖方对 2027E 增速中位仅约 {g}%（铝价假设偏保守），"
        "上行主要来自估值修复而非增速。",
        "拓扑与资金：铝指数 60 日邻居数 0（扩散）；近 {days} 个交易日主力净流入 {mf} 亿元（净占比 {rate}%），"
        "为六个方向中唯一净流入。"],
    "化学原料": [
        "增长：万华化学前三季营收 1858.67 亿元（+28.87%）、归母 156.58 亿元（+70.99%）；"
        "基础化工板块前三季净利同比 +56.2%。",
        "估值匹配：行业中位 2027E PE 约 {pe} 倍、2027E 增速中位约 {g}%，增长与估值大体匹配。",
        "拓扑与资金：其他化学原料／聚氨酯／氟化工／无机盐邻居数 3／0／1／0（扩散）；"
        "近 {days} 个交易日主力净流入 {mf} 亿元（净占比 {rate}%）。"],
}


def call(api, params, retry=4):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.8)
            r = S.post(URL, json={"api_name": api, "token": TOKEN, "params": params,
                                  "fields": ""}, timeout=45)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]["fields"], j["data"]["items"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:60])
            if "过快" in (j.get("msg") or ""):
                time.sleep(10 + 8 * i)
                continue
        except Exception as e:  # noqa
            last = repr(e)[:120]
        time.sleep(2 + 2 * i)
    print("[warn] 接口失败", api, params, last)
    return [], []


def recent_trading_days(n=5):
    f, it = call("index_daily", {"ts_code": "000001.SH", "start_date": "20260901",
                                 "end_date": "20261231"})
    rows = [dict(zip(f, r)) for r in it] if f else []
    rows.sort(key=lambda x: x["trade_date"])
    return [r["trade_date"] for r in rows[-n:]]


def consensus(code, tdate):
    f, it = call("report_rc", {"ts_code": code, "start_date": "20250101", "end_date": tdate})
    recs = [dict(zip(f, r)) for r in it] if f else []
    best = {}
    for r in recs:
        org, q = r.get("org_name"), str(r.get("quarter") or "")
        if not org or not q:
            continue
        k = (org, q)
        if k not in best or str(best[k]["report_date"]) < str(r["report_date"]):
            best[k] = r
    n27, n26 = [], []
    for r in best.values():
        q = str(r.get("quarter"))
        try:
            v = float(r.get("np"))
        except (TypeError, ValueError):
            continue
        if v != v or abs(v) >= 1e12:
            continue
        if q == "2027Q4":
            n27.append(v)
        elif q == "2026Q4":
            n26.append(v)
    orgs27 = len({r.get("org_name") for r in best.values() if str(r.get("quarter")) == "2027Q4"})
    fb, itb = call("daily_basic", {"ts_code": code, "start_date": "20260901", "end_date": tdate})
    db = [dict(zip(fb, r)) for r in itb] if fb else []
    db.sort(key=lambda x: x["trade_date"])
    mv = float(db[-1]["total_mv"]) if db else None
    out = {"orgs": orgs27, "pe27E": None, "g27": None}
    if n27 and mv and orgs27 >= MIN_ORGS:
        out["pe27E"] = round(mv / stc.median(n27), 2)
    if n27 and n26 and orgs27 >= MIN_ORGS:
        out["g27"] = round((stc.median(n27) / stc.median(n26) - 1) * 100, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    cfg_path = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    data_dir = os.path.join(a.task_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    if "table" not in cfg or "candidates" not in cfg:
        merged = json.load(open(os.path.join(WS, "merged_verdict.json"), encoding="utf-8"))
        flow = json.load(open(os.path.join(WS, "industry_moneyflow.json"), encoding="utf-8"))
        mv = {m["direction"]: m for m in merged["merged"]}
        cfg["candidates"] = CANDIDATES
        cfg["rationale"] = RATIONALE
        cfg["table"] = []
        for d, m in mv.items():
            cfg["table"].append({
                "direction": d, "indices": m["indices"], "neighbors": m["median_neighbors"],
                "judge": m["judge"], "pe27_median": m["pe27_median"], "g27_median": m["g27_median"],
                "mf4d_yi": flow["directions"][d]["mf5_yi"], "rate4": flow["directions"][d]["rate5"],
            })
        cfg["table_snapshot"] = {"topology_asof": merged["asof"], "flow_window": flow["window"]}
        print("已播种 table / candidates / rationale")

    tdate = recent_trading_days(1)[0]
    days = recent_trading_days(5)
    print("最新交易日", tdate, "| 资金流窗口", days)

    # 1) PE / 增速刷新（6 方向候选池）
    for row in cfg["table"]:
        pes, gs = [], []
        for c in cfg["candidates"][row["direction"]]:
            r = consensus(c, tdate)
            if r["pe27E"]:
                pes.append(r["pe27E"])
            if r["g27"] is not None:
                gs.append(r["g27"])
            time.sleep(0.4)
        if pes:
            row["pe27_median"] = round(stc.median(pes), 1)
        if gs:
            row["g27_median"] = round(stc.median(gs), 1)
        print(" %-14s 候选 %d 只 → 2027E PE 中位 %s | 增速中位 %s" % (
            row["direction"], len(cfg["candidates"][row["direction"]]),
            row["pe27_median"], row["g27_median"]))

    # 2) 主力净流入刷新（申万三级成分聚合）
    mem_path = os.path.join(data_dir, "members.json")
    mem = json.load(open(mem_path, encoding="utf-8")) if os.path.exists(mem_path) else {}
    idx_names = sorted({n for row in cfg["table"] for n in row["indices"]})
    code_of = {}
    for row in cfg["table"]:
        for n in row["indices"]:
            code_of[n] = None
    res = json.load(open(os.path.join(WS, "results.json"), encoding="utf-8"))
    nm2code = {x["name"]: x["code"] for x in res["industries"]}
    for n in idx_names:
        if n in mem:
            continue
        code = nm2code.get(n)
        if not code:
            print("[warn] 无三级代码:", n)
            continue
        f, it = call("index_member_all", {"l3_code": code, "limit": 3000})
        rows = [dict(zip(f, r)) for r in it] if f else []
        mem[n] = sorted({r["ts_code"] for r in rows if str(r.get("is_new", "")) in ("Y", "1", "True", "")})
        time.sleep(0.5)
    json.dump(mem, open(mem_path, "w", encoding="utf-8"), ensure_ascii=False)

    mf, amt, valid = {}, {}, []
    for dt in days:
        f, it = call("moneyflow", {"trade_date": dt, "limit": 6000})
        mf[dt] = {r[0]: dict(zip(f, r)) for r in it} if f else {}
        if len(mf[dt]) < 3000:
            print("[warn] %s 资金流数据缺失（%d 行），剔除" % (dt, len(mf[dt])))
            mf[dt] = {}
            continue
        valid.append(dt)
        f2, it2 = call("daily", {"trade_date": dt, "limit": 6000})
        amt[dt] = {r[0]: dict(zip(f2, r)) for r in it2} if f2 else {}
        time.sleep(0.5)

    def n(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return v if v == v else 0.0

    for row in cfg["table"]:
        tot_mf = tot_amt = 0.0
        for name in row["indices"]:
            for c in mem.get(name, []):
                for dt in valid:
                    rr = mf[dt].get(c)
                    if rr:
                        tot_mf += (n(rr.get("buy_lg_amount")) + n(rr.get("buy_elg_amount"))
                                   - n(rr.get("sell_lg_amount")) - n(rr.get("sell_elg_amount")))
                    aa = amt[dt].get(c)
                    if aa:
                        tot_amt += n(aa.get("amount")) * 0.1  # 千元 -> 万元
        row["mf4d_yi"] = round(tot_mf / 1e4, 2)
        row["rate4"] = round(100 * tot_mf / tot_amt, 2) if tot_amt else None
        print(" %-14s %d 日主力净流入 %8.2f 亿元 | 净占比 %s%%" % (
            row["direction"], len(valid), row["mf4d_yi"], row["rate4"]))

    cfg["table_window_days"] = len(valid)
    cfg["table_asof"] = "%s-%s-%s" % (tdate[:4], tdate[4:6], tdate[6:8])
    cfg["table_missing_days"] = [d for d in days if d not in valid]
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("config.json 已更新（table/candidates/rationale）")


if __name__ == "__main__":
    main()