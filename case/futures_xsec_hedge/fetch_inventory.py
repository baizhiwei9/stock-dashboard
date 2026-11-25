#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 99qh/fx168 期货库存日频全历史数据。"""
import urllib.request, json, ssl, time, os, sys, re
import pandas as pd

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Referer":"https://www.99qh.com/data/stockIn","Origin":"https://www.99qh.com",
      "Accept-Language":"zh-CN,zh;q=0.9"}
TOKEN_URL = "https://centerapi.fx168api.com/app/common/v.js"
DATA_URL = "https://centerapi.fx168api.com/app/qh/api/stock/trend"

# 品种: (productId, 名称, 板块)
PRODUCTS = {
    # 化工
    "MA": (49, "甲醇", "chem"), "TA": (48, "PTA", "chem"), "V": (72, "PVC", "chem"),
    "PP": (73, "聚丙烯", "chem"), "L": (71, "聚乙烯", "chem"), "EG": (77, "乙二醇", "chem"),
    "EB": (78, "苯乙烯", "chem"), "SA": (57, "纯碱", "chem"), "UR": (56, "尿素", "chem"),
    # 农产品
    "M": (63, "豆粕", "agri"), "RM": (39, "菜籽粕", "agri"), "Y": (64, "豆油", "agri"),
    "P": (65, "棕榈油", "agri"), "OI": (36, "菜籽油", "agri"), "SR": (35, "白糖", "agri"),
    "CF": (34, "棉花", "agri"), "C": (59, "玉米", "agri"), "AP": (43, "苹果", "agri"),
    "JD": (68, "鸡蛋", "agri"),
}

def get_token(retry=3):
    for i in range(retry):
        try:
            req = urllib.request.Request(TOKEN_URL, headers=UA)
            r = urllib.request.urlopen(req, timeout=20, context=CTX)
            pcc = dict(r.headers).get("_pcc","")
            if pcc:
                return pcc
        except Exception as e:
            pass
        time.sleep(1.5)
    return ""

def fetch_stock(pid, pcc):
    end_date = time.strftime("%Y-%m-%d")
    params = f"?productId={pid}&type=1&pageNo=1&pageSize=100000&startDate=&endDate={end_date}&appCategory=web"
    h = dict(UA); h["_pcc"] = pcc
    req = urllib.request.Request(DATA_URL+params, headers=h)
    r = urllib.request.urlopen(req, timeout=40, context=CTX)
    return json.loads(r.read().decode("utf-8"))

def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out_dir, exist_ok=True)
    results = {}
    for code, (pid, name, board) in PRODUCTS.items():
        ok = False
        for attempt in range(3):
            pcc = get_token()
            if not pcc:
                time.sleep(2); continue
            try:
                resp = fetch_stock(pid, pcc)
                d = resp.get("data")
                if isinstance(d, dict) and d.get("list"):
                    rows = d["list"]
                    df = pd.DataFrame(rows)
                    # 行: [date, close, stock(, stockChange?)]
                    df.to_csv(os.path.join(out_dir, f"inventory_{code}.csv"), index=False)
                    first, last = rows[0][0], rows[-1][0]
                    results[code] = (name, board, len(rows), first, last)
                    ok = True
                    break
                else:
                    time.sleep(2)
            except Exception as e:
                time.sleep(2)
        if not ok:
            results[code] = (name, board, -1, "NA", f"FAIL:{resp.get('message','')[:80]}" if 'resp' in dir() else "FAIL")
        print(results[code], flush=True)
    print("=== SUMMARY ===")
    for k, v in results.items():
        print(k, v)

if __name__ == "__main__":
    main()