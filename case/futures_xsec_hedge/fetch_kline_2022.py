#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补抓 2022-2023 存续合约（2109~2312）日线，与 all_kline 合并。"""
import urllib.request, ssl, re, json, time, os, sys
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Referer":"https://finance.sina.com.cn"}

# ---- DataHub 期货模块（取数统一入口）----
HUB_ROOT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier"
HUB_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps"
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

UNIVERSE = {
    "MA": "MA","TA": "TA","SA": "SA","UR": "UR","V": "V","PP": "PP","L": "L",
    "EG": "EG","EB": "EB","M": "M","RM": "RM","Y": "Y","P": "P","OI": "OI",
    "SR": "SR","CF": "CF","C": "C","AP": "AP","JD": "JD",
}

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

