#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取新浪期货各品种候选合约全生命周期日线（OHLC+量+持仓+结算），逐品种落盘csv。"""
import urllib.request, ssl, re, json, time, os, sys
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"}

# ---- 路径统一口径：case/_lib/paths.py（换机器只改一处）----------------------
try:
    from paths import datahub_root, py_deps
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    import os as _o, sys as _s
    _d = _o.path.dirname(_o.path.abspath(__file__))
    while not _o.path.isfile(_o.path.join(_d, "_lib", "paths.py")):
        _n = _o.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    _s.path.insert(0, _o.path.join(_d, "_lib"))
    from paths import datahub_root, py_deps


# ---- DataHub 期货模块（取数统一入口）----
HUB_ROOT = datahub_root()      # 不再写死：统一走 case/_lib/paths.py
HUB_DEPS = py_deps()
for _p in (HUB_DEPS, HUB_ROOT):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

UNIVERSE = {
    "MA": ("MA","chem","CZCE"), "TA": ("TA","chem","CZCE"), "SA": ("SA","chem","CZCE"),
    "UR": ("UR","chem","CZCE"), "V": ("V","chem","DCE"), "PP": ("PP","chem","DCE"),
    "L": ("L","chem","DCE"), "EG": ("EG","chem","DCE"), "EB": ("EB","chem","DCE"),
    "M": ("M","agri","DCE"), "RM": ("RM","agri","CZCE"), "Y": ("Y","agri","DCE"),
    "P": ("P","agri","DCE"), "OI": ("OI","agri","CZCE"), "SR": ("SR","agri","CZCE"),
    "CF": ("CF","agri","CZCE"), "C": ("C","agri","DCE"), "AP": ("AP","agri","CZCE"),
    "JD": ("JD","agri","DCE"),
}

def candidates(code):
    """候选合约：2024-01~2027-06 的所有月份（覆盖回测期主力+远月切换）"""
    out = []
    for yy in (24, 25, 26, 27):
        for mm in range(1, 13):
            if (yy == 24 and mm < 1) or (yy == 27 and mm > 6):
                continue
            out.append(f"{code}{yy}{mm:02d}")
    return out

def fetch_kline(sym, retry=2):
    """DataHub 期货模块合约全生命周期日线（替代新浪 jsonp）。

    返回与旧新浪口径同构的行列表：[date, open, high, low, close, volume, oi, settle]。
    DataHub 合约日线不返回 settle，末位固定为 None（下游未使用该列）。
    """
    from data_hub.collectors.futures import get_futures_contract_daily
    from datetime import date as _date, timedelta as _td
    # 起始日按合约自身年月推导（前推 400 天覆盖上市初期），避免早期合约取空
    _m = re.match(r"^([A-Za-z]+)(\d{2})(\d{2})$", sym)
    if _m:
        _y, _mo = 2000 + int(_m.group(2)), int(_m.group(3))
        start_d = (_date(_y, _mo, 1) - _td(days=400)).strftime("%Y%m%d")
    else:
        start_d = "20210101"
    end_d = _date.today().strftime("%Y%m%d")
    for i in range(max(1, retry)):
        try:
            df = get_futures_contract_daily(contract=sym, start_date=start_d, end_date=end_d)
            if df is None or len(df) == 0:
                raise RuntimeError("DataHub 返回空")
            dcol = "trade_date" if "trade_date" in df.columns else "date"
            vcol = "vol" if "vol" in df.columns else "volume"
            rows = []
            for _, r in df.sort_values(dcol).iterrows():
                rows.append([
                    str(r[dcol]).replace("-", ""),
                    float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r[vcol]) if pd.notna(r.get(vcol)) else None,
                    float(r["oi"]) if pd.notna(r.get("oi")) else None,
                    None,
                ])
            return rows or None
        except Exception:
            time.sleep(1.0 + i)
    return None

