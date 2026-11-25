# -*- coding: utf-8 -*-
"""PMI 分项取数（DataHub EDB 版）

分项一律从 EDB 读取（统计局发布稿口径由 EDB 抓取与落库），本脚本不再联网解析。
输出 data/pmi.json，字段契约与旧版一致，供 pmi_analysis.py 与 report_builder 使用。

EDB 已覆盖的分项：
    制造业：PMI、生产、新订单、新出口订单、购进价格、出厂价格
    服务业 / 建筑业：新订单（发布稿文字口径）
其余分项（商务活动、投入品价格、从业人员等）EDB 暂未登记，输出 None，由下游按
「数据缺失」处理，不使用上月值硬凑。
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, ensure_dirs
import edb_bridge as edb

MANU_MAP = {
    "PMI": "CN.PMI.MFG",
    "production": "CN.PMI.PROD",
    "new_orders": "CN.PMI.NEW.ORDER",
    "new_export": "CN.PMI.NEW.EXPORT",
    "input_price": "CN.PMI.INPUT.PRICE",
    "output_price": "CN.PMI.OUTPUT.PRICE",
}
SECTOR_MAP = {"services": "CN.PMI.SVC.NEW.ORDER", "construction": "CN.PMI.CONS.NEW.ORDER"}


def _pair(code):
    """返回 (本月值, 上月值, 月份 'YYYY-MM')。"""
    rows = edb.latest_n(code, 2)
    if not rows:
        return None, None, None
    cur_d, cur_v = rows[0]
    prev_v = rows[1][1] if len(rows) > 1 else None
    return cur_v, prev_v, cur_d[:7]


def fetch():
    manu, prev, month = {}, {}, None
    for key, code in MANU_MAP.items():
        cur, pr, mth = _pair(code)
        manu[key] = cur
        prev[key] = pr
        month = month or mth
    manu["prev"] = prev

    out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "datahub.edb",
        "month": month or "",
        "manu": manu,
        "services": {},
        "construction": {},
        "note": "分项来自 EDB（统计局发布稿口径）；EDB 未登记的分项输出 None，不用上月值硬凑",
    }
    for sector, code in SECTOR_MAP.items():
        cur, pr, _ = _pair(code)
        out[sector] = {
            "new_orders": cur,
            "new_orders_prev": pr,
            "new_orders_mom": round(cur - pr, 1) if (cur is not None and pr is not None) else None,
        }
        for key in ("business", "input_price", "output_price", "employment"):
            out[sector][key] = None
            out[sector][key + "_mom"] = None
            out[sector][key + "_prev"] = None

    doc = edb.doc("nbs_pmi")
    out["title"] = doc.get("title") or ""
    out["url"] = doc.get("url") or ""
    return out


def main():
    ensure_dirs()
    edb.update(list(MANU_MAP.values()) + list(SECTOR_MAP.values()))
    out = fetch()
    path = os.path.join(DATA_DIR, "pmi.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    m, s, c = out["manu"], out["services"], out["construction"]
    print("== PMI 取数摘要（DataHub EDB）==")
    print("月份:", out["month"], "|", out["title"])
    print("制造业 PMI:", m.get("PMI"), "| 新订单:", m.get("new_orders"),
          "| 新出口:", m.get("new_export"), "| 生产:", m.get("production"),
          "| 购进价格:", m.get("input_price"))
    print("服务业新订单:", s.get("new_orders"), "(Δ" + str(s.get("new_orders_mom")) + "pt)")
    print("建筑业新订单:", c.get("new_orders"), "(Δ" + str(c.get("new_orders_mom")) + "pt)")
    print("pmi.json 已保存")


if __name__ == "__main__":
    main()