# -*- coding: utf-8 -*-
"""realestate_daily 取数：更新 weekly_input.json 里可自动获取的字段。

基线是 weekly_input.seed.json（2026-10-10 人工快照）。三条硬规则：

1. 只写**有可靠来源**的字段；抓不到的字段保留原值与原 as_of，绝不改写日期；
2. 每个数据源独立 try/except，单源失败不影响其余，也不让整条任务链失败；
3. 明细落 data/fetch_meta.json，人可以复核哪项是新取的、哪项是沿用的。

自动覆盖
  土狗框架：EFFR、即期 USDCNY（含上周/上月对照）、CPI 同比、中美 10Y 利差
  付鹏框架：5Y LPR、按揭贷款利率
  北京/上海：70 城二手住宅环比、同比

不覆盖（目前无公开可自动源，保持原值）
  京沪网签、挂牌量与挂牌均价、租金与租金收益率、居民杠杆率、
  房贷余额、百城价格、AAA 存单、掉期点，以及付鹏/地方债/外部融资里的文字判断项。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = "weekly_input.json"
SEED_FILE = "weekly_input.seed.json"

# DataHub 序列层的全限定 code（短名一律返 NaN，别用）
HUB_CODES = ("LIQ.US.EFFR", "CN.FX.SPOT.USDCNY", "CN.CPI.YOY",
             "RATE.CN.10Y", "RATE.US.10Y")

# 自动字段的 note 只写来源与口径，不带具体月份数字。
# 数值归 value、时点归 as_of，note 写死数字下次更新就会自相矛盾（旧快照的教训）。
NOTES = {
    "fftr_effective": "DataHub LIQ.US.EFFR（联邦基金有效利率），自动取数",
    "spot_usdcny": "DataHub CN.FX.SPOT.USDCNY（在岸即期），自动取数",
    "spot_usdcny_上周": "7 天前在岸即期，用于周环比年化",
    "spot_usdcny_上月": "30 天前在岸即期，用于月环比年化",
    "headline_cpi": "DataHub CN.CPI.YOY（当月同比），自动取数",
    "中美10年利差倒挂": "中债 10Y − 美债 10Y（bp），DataHub RATE.CN.10Y / RATE.US.10Y",
    "lpr_5y": "5Y LPR 报价（全国银行间同业拆借中心），akshare 自动取数",
    "按揭贷款利率": "按 5Y LPR 锚定的首套按揭利率，随 LPR 同步",
    "70城二手_环比": "国家统计局 70 城二手住宅价格指数（环比 = 指数 − 100）",
    "70城二手_同比": "国家统计局 70 城二手住宅价格指数（同比 = 指数 − 100）",
}


# ---------------------------------------------------------------- 小工具
def _r(v, n=4):
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def _day(x):
    """规整成 YYYY-MM-DD；认不出返回 None（避免写进脏日期）。"""
    s = str(x)[:10]
    return s if len(s) == 10 and s[:2] == "20" and s[4] == "-" else None


def _month(x):
    d = _day(x)
    return d[:7] if d else None


def _hub_api():
    """部署环境已装 data_hub 直接用；否则按 CASE_DATAHUB_PATH 或相邻目录挂载。"""
    try:
        from data_hub.edb import api
        return api
    except Exception:
        pass
    cands = [(os.environ.get("CASE_DATAHUB_PATH") or "").strip()]
    cands += [os.path.join(HERE, "..", "..", "..", "datahub-multi-tier"),
              os.path.join(HERE, "..", "..", "datahub-multi-tier")]
    for p in cands:
        if p and os.path.isdir(os.path.join(p, "data_hub")):
            p = os.path.abspath(p)
            if p not in sys.path:
                sys.path.insert(0, p)
            try:
                from data_hub.edb import api
                return api
            except Exception:
                continue
    return None


# ---------------------------------------------------------------- 数据源
def _patch_hub():
    """DataHub 序列层：EFFR / USDCNY 及周月对照 / CPI / 中美利差。"""
    api = _hub_api()
    if api is None:
        raise RuntimeError("data_hub 不可用（未安装且未找到 CASE_DATAHUB_PATH）")

    patch = {"土狗框架": {}}
    got = {}

    df = api.latest(list(HUB_CODES))
    for _, row in df.iterrows():
        v, d = _r(row.get("value")), _day(row.get("date"))
        if v is None or d is None:
            continue
        got[str(row.get("code"))] = (v, d)

    if "LIQ.US.EFFR" in got:
        v, d = got["LIQ.US.EFFR"]
        patch["土狗框架"]["fftr_effective"] = {"value": v, "as_of": d,
                                               "note": NOTES["fftr_effective"]}

    if "CN.CPI.YOY" in got:
        v, d = got["CN.CPI.YOY"]
        patch["土狗框架"]["headline_cpi"] = {"value": v, "as_of": _month(d),
                                             "note": NOTES["headline_cpi"]}

    # 即期汇率：最新值 + 上周（7 天前）/上月（30 天前）对照，供周环比年化
    try:
        import pandas as pd
        s = api.get_series("CN.FX.SPOT.USDCNY").dropna()
        if len(s):
            ts = pd.to_datetime(s.index)
            last_d, last_v = ts[-1], float(s.iloc[-1])
            patch["土狗框架"]["spot_usdcny"] = {
                "value": round(last_v, 4), "as_of": last_d.strftime("%Y-%m-%d"),
                "note": NOTES["spot_usdcny"]}
            for key, days in (("spot_usdcny_上周", 7), ("spot_usdcny_上月", 30)):
                sub = s[ts <= last_d - pd.Timedelta(days=days)]
                if len(sub):
                    patch["土狗框架"][key] = {
                        "value": round(float(sub.iloc[-1]), 4),
                        "as_of": pd.to_datetime(sub.index)[-1].strftime("%Y-%m-%d"),
                        "note": NOTES[key]}
    except Exception as exc:                      # 对照取不到不影响最新值
        sys.stderr.write("[fetch] USDCNY 周/月对照跳过：%s\n" % exc)

    # 中美 10Y 利差（bp）：各自最新一天，as_of 取较保守的那个
    if "RATE.CN.10Y" in got and "RATE.US.10Y" in got:
        cn, cnd = got["RATE.CN.10Y"]
        us, usd = got["RATE.US.10Y"]
        patch["外部融资"] = {"中美10年利差倒挂": {
            "value": round((cn - us) * 100, 1), "as_of": min(cnd, usd),
            "note": NOTES["中美10年利差倒挂"]}}

    return patch, got


def _patch_akshare():
    """akshare：5Y LPR（含按揭锚）+ 北京/上海 70 城二手环比同比。"""
    import akshare as ak

    patch = {}

    # LPR：TRADE_DATE 是生效日，取最新一行
    lpr = ak.macro_china_lpr()
    lpr = lpr.dropna(subset=["LPR5Y"])
    if len(lpr):
        last = lpr.iloc[-1]
        d = _day(last["TRADE_DATE"])
        v = _r(last["LPR5Y"], 3)
        if d and v is not None:
            patch["付鹏框架"] = {
                "lpr_5y": {"value": v, "as_of": d, "note": NOTES["lpr_5y"]},
                # 一线首套按揭锚定 5Y LPR，口径随 LPR 同步
                "按揭贷款利率": {"value": v, "as_of": _month(d),
                                "note": NOTES["按揭贷款利率"]},
            }

    # 70 城：指数口径，100 为基准；环比/同比 = 指数 - 100
    hp = ak.macro_china_new_house_price()
    cols = {"环比": "二手住宅价格指数-环比", "同比": "二手住宅价格指数-同比"}
    if not set(cols.values()) <= set(hp.columns):
        raise RuntimeError("70 城表缺列：%s" % list(hp.columns))
    for city in ("北京", "上海"):
        sub = hp[hp["城市"] == city].dropna(subset=list(cols.values()))
        if not len(sub):
            continue
        row = sub.iloc[-1]
        ym = _month(row["日期"])
        if not ym:
            continue
        mom = _r(row[cols["环比"]])
        yoy = _r(row[cols["同比"]])
        if mom is None or yoy is None:
            continue
        patch.setdefault(city, {})
        patch[city]["70城二手_环比"] = {"value": round(mom - 100, 2), "as_of": ym,
                                     "note": NOTES["70城二手_环比"]}
        patch[city]["70城二手_同比"] = {"value": round(yoy - 100, 2), "as_of": ym,
                                     "note": NOTES["70城二手_同比"]}

    return patch, patch


# ---------------------------------------------------------------- 合并落盘
def _apply(base, patch):
    """把 patch 覆盖进 base；只覆盖 patch 里出现的字段。返回 (更新项, 新增项)。"""
    updated = added = 0
    for sec, items in patch.items():
        if not isinstance(items, dict):
            continue
        tgt = base.setdefault(sec, {})
        for key, fields in items.items():
            if key.startswith("_"):
                continue
            existed = key in tgt and isinstance(tgt[key], dict)
            cur = tgt[key] if existed else {}
            moved = False
            for f, val in (fields or {}).items():
                if val is None:
                    continue
                # 换值就得换口径说明：旧 note 存进 note_orig，别让描述与新数打架
                if f == "note" and existed and cur.get("note") and cur["note"] != val:
                    cur.setdefault("note_orig", cur["note"])
                cur[f] = val
                moved = True
            if not moved:
                continue
            tgt[key] = cur
            if existed:
                updated += 1
            else:
                added += 1
    return updated, added


def _load_base(out_path):
    for p in (out_path, os.path.join(HERE, SEED_FILE)):
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data, p
    raise RuntimeError("既无 %s 也无基线 %s" % (out_path, SEED_FILE))


def build(out_path=None):
    """取数主入口：读基线 → 各源更新 → 写回。返回统计字典。"""
    out_path = out_path or os.path.join(HERE, DATA_FILE)
    base, src = _load_base(out_path)

    meta = {"fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "base": os.path.basename(src), "sources": {}}
    total_updated = total_added = 0

    for name, fn in (("datahub", _patch_hub), ("akshare", _patch_akshare)):
        try:
            patch, detail = fn()
            u, a = _apply(base, patch)
            total_updated += u
            total_added += a
            meta["sources"][name] = {"ok": True, "updated": u, "added": a,
                                     "got": sorted(str(k) for k in (detail or {}))}
        except Exception as exc:
            meta["sources"][name] = {"ok": False,
                                     "error": "%s: %s" % (type(exc).__name__, exc)}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)

    data_dir = os.path.join(HERE, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "fetch_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    meta["updated"], meta["added"] = total_updated, total_added
    return meta


if __name__ == "__main__":
    st = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(st, ensure_ascii=False, indent=2))