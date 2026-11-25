# -*- coding: utf-8 -*-
"""期权合约基础信息刷新（SSE / SZSE / CFFEX），供 compute_ashare.py 映射链上的 opt_code。

改口（用户 2026-11-13）：合约基础信息经 DataHub 实时链自带字段生成；DataHub
取不到时才回落到 TuShare opt_basic（原通道）。

重要：写入采用「并集合并」而不是覆盖。DataHub 实时链只含当前挂牌合约，若直接覆盖，
历史 252 个交易日的链会因为映射缺失而整段丢失，偏度/GEX 的分位失去可比性。合并后
新增合约即时可用，历史合约映射（原 TuShare opt_basic 口径）继续保留。
"""
import os

import pandas as pd

import lib_adapter as LIB

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

FILES = [("SSE", "basic_sse"), ("SZSE", "basic_szse"), ("CFFEX", "basic_cffex")]
KEYS = ["ts_code", "opt_code", "call_put", "exercise_price", "maturity_date", "per_unit"]


def _merge_and_save(fn, sub, tag):
    p = os.path.join(DATA, fn + ".json")
    if os.path.exists(p):
        try:
            old = pd.read_json(p, orient="records", dtype={"ts_code": str})
        except Exception:
            old = pd.DataFrame(columns=KEYS)
    else:
        old = pd.DataFrame(columns=KEYS)
    for c in KEYS:
        if c not in old.columns:
            old[c] = None
        if c not in sub.columns:
            sub[c] = None
    keep = list(KEYS)
    merged = pd.concat([old[keep], sub[keep]], ignore_index=True)
    merged = merged.drop_duplicates("ts_code", keep="last")
    merged.to_json(p, orient="records", force_ascii=False)
    print("saved", fn, merged.shape, "(新增 %d 条, %s)" % (len(sub), tag))


def from_datahub():
    _ch, ba = LIB.realtime_chains(None)
    if ba is None or not len(ba):
        return False

    def _ex(r):
        if r["opt_code"] == "OP000852.SH":
            return "CFFEX"
        return "SZSE" if str(r["opt_code"]).endswith(".SZ") else "SSE"

    ba = ba.copy()
    ba["exchange"] = [_ex(r) for r in ba.to_dict("records")]
    for ex, fn in FILES:
        sub = ba[ba["exchange"] == ex].drop(columns=["exchange"])
        if not len(sub):
            print("skip", fn, "(DataHub 无 %s 合约)" % ex)
            continue
        _merge_and_save(fn, sub[KEYS], "DataHub")
    return True


def from_tushare():
    import ts_pin
    pro = ts_pin.init()
    for ex, fn in FILES:
        b = ts_pin.call(pro.opt_basic, exchange=ex)
        _merge_and_save(fn, b[[c for c in KEYS if c in b.columns]], "TuShare 回退")


if not from_datahub():
    from_tushare()

print("DONE fetch_basics")