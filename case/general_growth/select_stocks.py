# -*- coding: utf-8 -*-
"""全市场链路第⑥⑦步：在缠论通过的对象内选个股（每对象至多 2 只）并跑个股缠论

新增硬校验（本次改动）：增速与估值必须匹配 —— PEG≤1
  · 主口径：2027E PE ≤ 2027E 净利增速（且 2027E 增速 > 0）
  · 并列口径：若净利润TTM同比 > 0，则 PE_TTM ≤ 净利润TTM同比
不满足者不做入选，按候选顺位替换。同时保存缠论结构（中枢/笔/买卖点）供出图标注。
写入 config.directions + data/px_<code>.csv + data/ohlcv/<code>.csv
用法: python3 select_stocks.py [--task-dir DIR] [--top 6] [--cand 22]
"""
import argparse
import json
import os
import statistics as stc
import sys
import time

import pandas as pd
import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
sys.path.insert(0, os.path.join(WS, "pylibs"))
sys.path.insert(0, os.path.join(WS, "ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG", "pylibs"))
sys.path.insert(0, os.path.join(WS, "skills", "user", "chan-fenxing-ban"))
import ts_config  # noqa
from chan_fractal import analyze  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()
MIN_ORGS = 6
_CACHE = None
_CACHE_PATH = None


def call(api, params, retry=4):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.75)
            r = S.post(URL, json={"api_name": api, "token": TOKEN, "params": params,
                                  "fields": ""}, timeout=60)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]["fields"], j["data"]["items"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:50])
            if "过快" in (j.get("msg") or "") or "超限" in (j.get("msg") or ""):
                time.sleep(12 + 8 * i)
                continue
        except Exception as e:  # noqa
            last = repr(e)[:100]
        time.sleep(2 + 2 * i)
    print("[warn]", api, params, last)
    return [], []


def fnum(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def np_ttm(basic, code):
    b = basic.get(code)
    if not b:
        return None
    mv, pe = fnum(b.get("total_mv")), fnum(b.get("pe_ttm"))
    return mv / pe if (mv is not None and pe not in (None, 0)) else None


def dstr(t):
    d = "".join(ch for ch in str(t) if ch.isdigit())[-8:]
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else str(t)


def span(b):
    """'1970-01-01 00:00:00.020250117 ~ ...' -> ('2025-01-17','2025-09-17')"""
    parts = str(b).split("~")
    return (dstr(parts[0]), dstr(parts[-1])) if len(parts) >= 2 else (dstr(b), dstr(b))


def _cache_get(code, tdate):
    global _CACHE
    if _CACHE is None:
        return None
    return _CACHE.get("%s|%s" % (code, tdate))


def _cache_put(code, tdate, val):
    global _CACHE
    if _CACHE is None:
        return
    _CACHE["%s|%s" % (code, tdate)] = val


def consensus(code, tdate):
    hit = _cache_get(code, tdate)
    if hit is not None:
        return hit
    out = _consensus_raw(code, tdate)
    _cache_put(code, tdate, out)
    return out


def _consensus_raw(code, tdate):
    f, it = call("report_rc", {"ts_code": code, "start_date": "20250101", "end_date": tdate})
    recs = [dict(zip(f, r)) for r in it] if f else []
    best = {}
    for r in recs:
        org, q = r.get("org_name"), str(r.get("quarter") or "")
        if not org or not q:
            continue
        k = (org, q)
        if k not in best or str(best[k].get("report_date", "")) < str(r.get("report_date", "")):
            best[k] = r
    n27, n26, o27 = [], [], set()
    for r in best.values():
        q = str(r.get("quarter"))
        v = fnum(r.get("np"))
        if v is None or abs(v) >= 1e12:
            continue
        if q == "2027Q4":
            n27.append(v); o27.add(r.get("org_name"))
        elif q == "2026Q4":
            n26.append(v)
    fb, itb = call("daily_basic", {"ts_code": code, "start_date": "20260901", "end_date": tdate})
    db = [dict(zip(fb, r)) for r in itb] if fb else []
    db.sort(key=lambda x: x["trade_date"])
    d = db[-1] if db else {}
    mv, close, pe = fnum(d.get("total_mv")), fnum(d.get("close")), fnum(d.get("pe_ttm"))
    out = {"orgs": len(o27), "close": close, "pe_ttm": pe,
           "mv_yi": round(mv / 1e4, 2) if mv else None, "pe27E": None, "g27": None}
    if n27 and mv and len(o27) >= MIN_ORGS:
        out["pe27E"] = round(mv / stc.median(n27), 2)
    if n27 and n26 and len(o27) >= MIN_ORGS:
        out["g27"] = round((stc.median(n27) / stc.median(n26) - 1) * 100, 1)
    return out


def ohlcv(code, tdate, out_csv):
    f, it = call("daily", {"ts_code": code, "start_date": "20250101", "end_date": tdate})
    recs = [dict(zip(f, r)) for r in it] if f else []
    recs.sort(key=lambda x: x["trade_date"])
    with open(out_csv, "w", encoding="utf-8") as fh:
        fh.write("date,open,high,low,close,vol\n")
        for r in recs:
            fh.write("%s,%s,%s,%s,%s,%s\n" % (r["trade_date"], r["open"], r["high"], r["low"],
                                              r["close"], r.get("vol", 0)))
    return recs


def chan_struct(df):
    r = analyze(df, level="day", backtest=False, name="")
    zs = (r.get("结构") or {}).get("中枢") or []
    bi = (r.get("结构") or {}).get("笔端点") or []
    sig = r.get("交易机会") or []
    st = {"中枢": [], "笔": [], "买卖点": []}
    for z in zs:
        s, e = span(z.get("起止时间"))
        st["中枢"].append({"ZD": float(z["下沿ZD"]), "ZG": float(z["上沿ZG"]), "起": s, "止": e})
    for x in bi:
        st["笔"].append({"类型": x.get("类型"), "价格": float(x.get("价格")), "时间": dstr(x.get("时间"))})
    for x in sig:
        st["买卖点"].append({"类型": x.get("类型"), "价格": float(x.get("价格")),
                           "时间": dstr(x.get("时间")), "说明": x.get("说明")})
    return st, sig, zs


BUY_NAME = {"B1": "一买", "B2": "二买", "B3": "三买"}
# 非主题型归属（指数样本/两融/互联互通等），不参与个股多归属匹配核验
PLACE = ["样本股", "成份股", "成分股", "融资融券", "沪股通", "深股通", "证金持股", "漂亮100"]


def build_rev(sw_mem, ths_mem, nm_sw, nm_ths):
    """股票 -> [(归属类型, 对象代码, 对象名)] 反向索引（申万三级 + 同花顺概念）"""
    rev = {}
    for code, v in sw_mem.items():
        for c in (v or {}).get("codes", []):
            rev.setdefault(c, []).append(("申万三级", code, nm_sw.get(code, code)))
    for code, v in ths_mem.items():
        for c in (v or {}).get("codes", []):
            rev.setdefault(c, []).append(("概念", code, nm_ths.get(code, code)))
    return rev


def attributions(code, rev, screen, chain_map=None):
    """个股全部所属行业/概念的逐项增速-估值匹配核验"""
    rows = []
    for kind, ocode, name in rev.get(code, []):
        if any(x in name for x in PLACE):
            continue
        r = screen.get(ocode)
        if not r:
            continue
        pe, g = r.get("pe_median"), r.get("g_median_pct")
        pg = round(pe / g, 2) if (pe and g and g > 0) else None
        rows.append({"归属类型": kind, "归属对象": name, "对象代码": ocode,
                     "PE_TTM中位": pe, "净利TTM同比中位": g, "PEG": pg,
                     "匹配": "匹配" if (pg is not None and pg <= 1) else "不匹配",
                     "全链通过": bool((chain_map or {}).get(ocode, False))})
    rows.sort(key=lambda x: (x["匹配"] != "匹配", x["PEG"] if x["PEG"] is not None else 9))
    return rows


def _zd(z):
    return float(z.get("下沿ZD", z.get("ZD")))


def _zg(z):
    return float(z.get("上沿ZG", z.get("ZG")))


def chan_state(close, lz):
    """相对最近中枢的当前状态"""
    if not lz:
        return "无中枢"
    zd, zg = _zd(lz), _zg(lz)
    if close > zg:
        return "突破中枢"
    return "中枢内部" if close >= zd else "跌破中枢"


def consistent_buys(close, zs, sig):
    """按状态口径筛出结构自洽的买点：三买需突破上沿、二买需守住下沿、一买用于跌破/无中枢"""
    state = chan_state(close, zs[-1] if zs else None)
    buys = [x for x in sig if x.get("类型") in BUY_NAME]
    if zs:
        lz = zs[-1]
        zd, zg = _zd(lz), _zg(lz)
        if state == "突破中枢":
            c = [x for x in buys if x["类型"] == "B3" and float(x["价格"]) > zg]
        elif state == "中枢内部":
            c = [x for x in buys if x["类型"] == "B2" and float(x["价格"]) >= zd]
        elif state == "跌破中枢":
            c = [x for x in buys if x["类型"] == "B1"]
        else:
            c = [x for x in buys if x["类型"] in ("B1", "B2")]
    else:
        c = [x for x in buys if x["类型"] in ("B1", "B2")]
    return c


def chan_label(close, zs, sig):
    """标注必须与结构自洽：三买需突破中枢上沿、二买需守住下沿、一买用于跌破/无中枢"""
    state = chan_state(close, zs[-1] if zs else None)
    ztxt = ""
    if zs:
        lz = zs[-1]
        ztxt = "（中枢 %.2f–%.2f）" % (_zd(lz), _zg(lz))
    c = consistent_buys(close, zs, sig)
    valid = c[-1] if c else None
    if valid:
        return state, state + ztxt + "｜%s（%s）" % (BUY_NAME[valid["类型"]], dstr(valid["时间"]))
    return state, state + ztxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--top", type=int, default=0, help="0 表示展示全部全链通过对象")
    ap.add_argument("--cand", type=int, default=22)
    a = ap.parse_args()
    T = a.task_dir
    dd = os.path.join(T, "data")
    cfg = json.load(open(os.path.join(T, "config.json"), encoding="utf-8"))
    global _CACHE, _CACHE_PATH
    _CACHE_PATH = os.path.join(dd, "cons_cache.json")
    _CACHE = json.load(open(_CACHE_PATH, encoding="utf-8")) if os.path.exists(_CACHE_PATH) else {}
    tdate = cfg.get("asof", "2026-10-30").replace("-", "")
    sw_mem = json.load(open(os.path.join(dd, "fm_sw_members.json"), encoding="utf-8"))
    ths_mem = json.load(open(os.path.join(dd, "fm_ths_members.json"), encoding="utf-8"))
    b26 = json.load(open(os.path.join(dd, "fm_basic_20261030.json"), encoding="utf-8"))
    b25 = json.load(open(os.path.join(dd, "fm_basic_20251031.json"), encoding="utf-8"))
    nm = {}
    fs, its = call("stock_basic", {"exchange": "", "list_status": "L"})
    for r in (dict(zip(fs, x)) for x in its) if fs else []:
        nm[r["ts_code"]] = r["name"]
    print("个股名称表", len(nm))

    screen = {r["code"]: r for r in json.load(open(os.path.join(dd, "fm_screen.json"), encoding="utf-8"))}
    nm_sw = {x["index_code"]: x["industry_name"] for x in
             json.load(open(os.path.join(dd, "fm_sw_l3.json"), encoding="utf-8"))}
    nm_ths = {x["ts_code"]: x["name"] for x in
              json.load(open(os.path.join(dd, "fm_ths_index.json"), encoding="utf-8"))}
    rev = build_rev(sw_mem, ths_mem, nm_sw, nm_ths)
    chain_map = {r["code"]: r["chain_pass"] for r in cfg["table"]}

    passed = [r for r in cfg["table"] if r["chain_pass"]]
    featured = sorted(passed, key=lambda r: -(r["g_ttm"] or 0))
    if a.top and a.top > 0:
        featured = featured[:a.top]
    print("全链通过对象 %d 个，展示 %d 个" % (len(passed), len(featured)))
    print("展示对象:", [x["name"] for x in featured])

    os.makedirs(os.path.join(dd, "ohlcv"), exist_ok=True)
    directions = []
    for obj in featured:
        code, kind = obj["code"], obj["kind"]
        mem = ((sw_mem if kind == "申万三级" else ths_mem).get(code) or {}).get("codes", [])
        scored, gmap = [], {}
        for c in mem:
            n26, n25 = np_ttm(b26, c), np_ttm(b25, c)
            if n26 is None or n25 is None or n25 <= 0:
                continue
            g = (n26 / n25 - 1) * 100
            gmap[c] = g
            scored.append((c, g))
        scored.sort(key=lambda x: -x[1])
        cands = [c for c, _ in scored[:a.cand]]
        print(" · %s：成分 %d，候选 %d" % (obj["name"], len(mem), len(cands)))

        chosen, rejected = [], []
        for c in cands:
            if len(chosen) >= 2:
                break
            cons = consensus(c, tdate)
            if cons["orgs"] < MIN_ORGS or cons["g27"] is None:
                rejected.append("%s(%s)：研报覆盖 %d 家（<6）或缺 2027E 增速，不计入"
                                % (nm.get(c, c), c, cons["orgs"]))
                continue
            g_ttm = gmap.get(c)
            peg27 = round(cons["pe27E"] / cons["g27"], 2) if (cons["pe27E"] and cons["g27"] > 0) else None
            peg_ttm = round(cons["pe_ttm"] / g_ttm, 2) if (cons["pe_ttm"] and g_ttm and g_ttm > 0) else None
            if cons["g27"] <= 0 or peg27 is None or peg27 > 1:
                rejected.append("%s(%s)：2027E PE %s 倍 / 增速 %.1f%%（PEG %s）不匹配"
                                % (nm.get(c, c), c, cons["pe27E"] or "—", cons["g27"], peg27))
                continue
            if g_ttm and g_ttm > 0 and peg_ttm is not None and peg_ttm > 1:
                rejected.append("%s(%s)：PE_TTM %.2f 倍 / 净利TTM同比 %.1f%%（PEG %s）不匹配"
                                % (nm.get(c, c), c, cons["pe_ttm"], g_ttm, peg_ttm))
                continue
            csv_ohlc = os.path.join(dd, "ohlcv", "%s.csv" % c.replace(".", "_"))
            recs = ohlcv(c, tdate, csv_ohlc)
            if len(recs) < 120:
                continue
            df = pd.read_csv(csv_ohlc)
            st, sig, zs = chan_struct(df)
            close = float(df["close"].iloc[-1])
            lz = zs[-1] if zs else None
            zd = float(lz["下沿ZD"]) if lz else None
            zg = float(lz["上沿ZG"]) if lz else None
            b1 = [x for x in sig if x.get("类型") == "B1"]
            pos, label = chan_label(close, zs, sig)
            if not ((pos != "跌破中枢") or bool(b1)):
                rejected.append("%s(%s)：个股缠论不通过（%s 且无一买）" % (nm.get(c, c), c, pos))
                continue
            fpx, itpx = call("daily", {"ts_code": c, "start_date": "20260401", "end_date": tdate})
            px = [dict(zip(fpx, r)) for r in itpx] if fpx else []
            px.sort(key=lambda x: x["trade_date"])
            with open(os.path.join(dd, "px_%s.csv" % c), "w", encoding="utf-8") as fh:
                fh.write("trade_date,close\n")
                for r in px:
                    fh.write("%s,%s\n" % (r["trade_date"], r["close"]))
            att = attributions(c, rev, screen, chain_map)
            natt = len(att)
            okn = sum(1 for x in att if x["匹配"] == "匹配")
            chosen.append({
                "归属": {"归属数": natt, "归属匹配数": okn, "归属不匹配数": natt - okn,
                         "归属匹配率": round(okn / natt * 100, 1) if natt else None,
                         "明细": att},
                "code": c, "name": nm.get(c, c), "close": round(close, 2),
                "pe27E": cons["pe27E"], "g27": cons["g27"], "orgs": cons["orgs"],
                "pe_ttm": round(cons["pe_ttm"], 2) if cons["pe_ttm"] else None,
                "g_ttm": round(g_ttm, 1) if g_ttm is not None else None,
                "peg27E": peg27, "pegTTM": peg_ttm,
                "match": "匹配（2027E PEG %.2f）" % peg27,
                "chan": {"位置": pos, "状态": pos, "ZD": zd, "ZG": zg, "最近买点": label,
                         "说明": ("结构自洽标注：%s" % label)},
                "chan_struct": st})
        directions.append({
            "direction": obj["name"], "kind": kind, "code": code, "judge": obj["judge"],
            "neighbors": obj["neighbors"], "g_ttm": obj["g_ttm"], "pe_ttm": obj["pe_ttm"],
            "amt5": obj["amt5"], "chan_verdict": obj["chan_verdict"], "chan_pos": obj["chan_pos"],
            "industry_chan_pass": "行业级缠论 %s（%s）" % (obj["chan_verdict"], obj["chan_pos"]),
            "stocks": chosen, "rejected": rejected})
        for rj in rejected:
            print("     × 剔除", rj)

    cfg["directions"] = directions
    cfg["match_rule"] = ("个股硬校验：2027E PE ≤ 2027E 净利增速（PEG≤1，且增速>0）；若净利润TTM同比>0，"
                         "再加 PE_TTM ≤ 净利润TTM同比（PEG≤1）。不满足者剔除，按候选顺位替换。")
    cfg["featured_rule"] = ("展示范围＝全部全链通过对象（本轮 %d 个，按净利润TTM同比中位降序排列）；"
                            "每个对象入选至多 2 只个股（研报覆盖≥6 家、增速与估值匹配 PEG≤1、逐只过个股缠论）" % len(featured))
    import chart_order
    cfg["max_stocks_per_chart"] = chart_order.DEFAULT_CAP
    cfg["chart_order_rule"] = chart_order.ORDER_RULE
    cfg["chart_variant"] = "按买点排序"
    json.dump(cfg, open(os.path.join(T, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if _CACHE is not None:
        json.dump(_CACHE, open(_CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    nstock = sum(len(d["stocks"]) for d in directions)
    print("入选个股合计 %d 只 → 分图 %d 张（每张最多 %d 只）"
          % (nstock, max(1, -(-nstock // 20)), 20))
    for d in directions:
        print("  %-14s %s | 个股 %s" % (d["direction"], d["judge"],
              ", ".join("%s(%s)" % (s["name"], s["code"]) for s in d["stocks"])))
    print("入选个股合计", sum(len(d["stocks"]) for d in directions))


if __name__ == "__main__":
    main()