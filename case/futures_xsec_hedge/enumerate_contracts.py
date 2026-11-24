#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""枚举各品种在2025-01至2026-12期间存续的合约（通过新浪批量实时行情探测存在性）。"""
import urllib.request, ssl, time, json, os
import pandas as pd

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
      "Referer":"https://finance.sina.com.cn"}

# 品种: (品种代码前缀, 板块, 交易所)
UNIVERSE = {
    # 化工
    "MA": ("MA", "chem", "CZCE"), "TA": ("TA", "chem", "CZCE"), "SA": ("SA", "chem", "CZCE"),
    "UR": ("UR", "chem", "CZCE"), "V": ("V", "chem", "DCE"), "PP": ("PP", "chem", "DCE"),
    "L": ("L", "chem", "DCE"), "EG": ("EG", "chem", "DCE"), "EB": ("EB", "chem", "DCE"),
    # 农产品
    "M": ("M", "agri", "DCE"), "RM": ("RM", "agri", "CZCE"), "Y": ("Y", "agri", "DCE"),
    "P": ("P", "agri", "DCE"), "OI": ("OI", "agri", "CZCE"), "SR": ("SR", "agri", "CZCE"),
    "CF": ("CF", "agri", "CZCE"), "C": ("C", "agri", "DCE"), "AP": ("AP", "agri", "CZCE"),
    "JD": ("JD", "agri", "DCE"),
}

def probe(contracts):
    """批量查询合约存在性，返回 {contract: exists_bool}"""
    out = {}
    chunk = contracts
    url = "https://hq.sinajs.cn/list=" + ",".join("nf_" + c for c in chunk)
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=20, context=CTX).read()
        text = raw.decode("gbk", "ignore")
        for line in text.strip().split("\n"):
            m = line.split("=", 1)
            if len(m) == 2:
                name = m[0].strip().replace("var hq_str_nf_", "").strip()
                val = m[1].strip().strip(';').strip('"')
                out[name] = len(val) > 0
    except Exception as e:
        print("probe err", str(e)[:80])
        for c in chunk:
            out[c] = None
    return out

def main():
    # 生成候选合约: YYMM 从 2501 到 2612（回测区间存续合约；远月合约到2712可能在2026年9月仍存续，也纳入）
    months = []
    for yy in (25, 26, 27):
        for mm in range(1, 13):
            months.append(f"{yy}{mm:02d}")
    all_probe = []
    for code, (pre, board, ex) in UNIVERSE.items():
        for ym in months:
            all_probe.append(f"{pre}{ym}")
    # 分批探测，每批40个
    exists = {}
    batch = 40
    for i in range(0, len(all_probe), batch):
        chunk = all_probe[i:i+batch]
        r = probe(chunk)
        exists.update(r)
        time.sleep(0.25)
    # 汇总
    rows = []
    for code, (pre, board, ex) in UNIVERSE.items():
        cts = sorted([c for c in exists if c.startswith(pre) and exists.get(c)])
        rows.append({"code": code, "board": board, "exchange": ex, "contracts": ",".join(cts), "n": len(cts)})
        print(code, board, ex, len(cts), cts[:5], "...", cts[-3:] if len(cts) > 3 else "", flush=True)
    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "contract_universe.csv")
    df.to_csv(out, index=False)
    print("saved", out)

if __name__ == "__main__":
    main()