# -*- coding: utf-8 -*-
"""月度宏观报告 · 取数（DataHub EDB 版）

所有宏观数据统一从 data_hub 的 EDB 读取，本脚本不再直连统计局 / 东方财富 / akshare。
刷新动作交给 EDB（`edb.update`，按频率 TTL 决定是否真正联网），这里只负责把
EDB 的序列拼装成报告需要的 latest.json（字段契约与旧版保持一致）。

输出：data/latest.json
  customs[]  出口/进口同比、累计同比、出口金额
  ppi[]      PPI 同比、累计指数
  m1m2[]     M1/M2 同比（月度序列）
  m1_summary M1 最新值/上期/环比/历史分位
  profit     规上工业企业利润（累计/当月同比、营收、分行业）
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, ensure_dirs
import edb_bridge as edb

CUSTOMS_CODES = ["CN.EXP.YOY", "CN.EXP.CUM.YOY", "CN.IMP.YOY", "CN.IMP.CUM.YOY", "CN.EXP.AMT"]
PROFIT_INDUSTRIES = ["计算机、通信和其他电子设备制造业", "有色金属冶炼和压延加工业",
                     "煤炭开采和洗选业", "汽车制造业", "黑色金属冶炼和压延加工业"]


def build_customs():
    """海关进出口：按月份合并 EDB 五条序列。"""
    data = {c: dict(edb.month_series(c, limit=24)) for c in CUSTOMS_CODES}
    months = sorted({m for d in data.values() for m in d}, reverse=True)
    rows = []
    for m in months:
        rows.append({
            "date": f"{m}-01",
            "export_base": data["CN.EXP.AMT"].get(m),
            "export_yoy": data["CN.EXP.YOY"].get(m),
            "export_cum_yoy": data["CN.EXP.CUM.YOY"].get(m),
            "import_yoy": data["CN.IMP.YOY"].get(m),
            "import_cum_yoy": data["CN.IMP.CUM.YOY"].get(m),
        })
    return rows


def build_ppi():
    """PPI 同比序列（累计指数来自 CN.PPI.CUM）。"""
    yoy = dict(edb.month_series("CN.PPI.YOY", limit=36))
    cum = dict(edb.month_series("CN.PPI.CUM", limit=36))
    rows = []
    for m in sorted(yoy, reverse=True):
        v = yoy[m]
        rows.append({"date": f"{m}-01",
                     "ppi_base": round(100 + v, 1) if v is not None else None,
                     "ppi_yoy": v,
                     "ppi_cum_yoy": cum.get(m)})
    return rows


def build_m1():
    """M1/M2 同比序列，月份标签沿用旧格式 'YYYY年MM'。"""
    m1 = dict(edb.month_series("CN.M1.YOY", limit=240))
    m2 = dict(edb.month_series("CN.M2.YOY", limit=240))
    rows = []
    for m in sorted(set(m1) | set(m2), reverse=True):
        y, mo = m.split("-")
        rows.append({"month": f"{y}年{mo}", "m1_yoy": m1.get(m), "m2_yoy": m2.get(m)})
    return rows


def m1_summary(m1_rows):
    """从 M1 序列(降序)计算汇总：最新值/上期/环比/历史分位。"""
    rows = [r for r in m1_rows if r.get("m1_yoy") is not None]
    if not rows:
        return {}
    latest, prev = rows[0], (rows[1] if len(rows) > 1 else None)
    vals = [r["m1_yoy"] for r in rows]
    below = sum(1 for v in vals if v <= latest["m1_yoy"])
    return {
        "month": latest["month"],
        "m1_yoy": latest["m1_yoy"],
        "prev_yoy": prev["m1_yoy"] if prev else None,
        "mom": round(latest["m1_yoy"] - prev["m1_yoy"], 2) if prev else None,
        "pctile": round(below * 100.0 / len(vals), 1),
        "n": len(vals),
    }


def build_profit():
    """规上工业企业利润：总量 + 报告引用的 5 个分行业。"""
    d = edb.doc("nbs_profit")
    extra = d.get("extra") or {}
    month = extra.get("month") or ""
    label = f"{int(month[5:])}月" if month else ""
    ib = {}
    for ind in PROFIT_INDUSTRIES:
        _, v = edb.latest(f"CN.PROFIT.IND.{ind}")
        ib[ind] = v
    return {
        "title": d.get("title") or "",
        "url": d.get("url") or "",
        "month_yoy": edb.latest("CN.PROFIT.MONTH.YOY")[1],
        "month_label": label,
        "revenue_amount": edb.latest("CN.PROFIT.REVENUE.AMT")[1],
        "revenue_yoy": edb.latest("CN.PROFIT.REVENUE.YOY")[1],
        "profit_amount": edb.latest("CN.PROFIT.AMT")[1],
        "cum_yoy": edb.latest("CN.PROFIT.CUM.YOY")[1],
        "industry_breakdown": ib,
        "industry_sents": extra.get("industry_sents") or [],
    }


def main():
    ensure_dirs()
    rep = edb.update(edb.REPORT_CODES + ["CN.PPI.CUM"])
    out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "datahub.edb",
        "edb_update": {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in rep.items()},
        "customs": build_customs(),
        "ppi": build_ppi(),
        "m1m2": build_m1(),
        "m1_summary": {},
        "profit": build_profit(),
    }
    out["m1_summary"] = m1_summary(out["m1m2"])
    path = os.path.join(DATA_DIR, "latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    p = out["profit"]
    c = out["customs"][0] if out["customs"] else {}
    pp = out["ppi"][0] if out["ppi"] else {}
    m = out["m1m2"][0] if out["m1m2"] else {}
    print("== 取数摘要（DataHub EDB）==")
    print("EDB 刷新:", {k: rep.get(k) for k in ("refreshed", "failed") if k in rep})
    print("利润:", p.get("title"), "| 累计同比:", p.get("cum_yoy"), "% | 当月:", p.get("month_yoy"),
          "% | 营收同比:", p.get("revenue_yoy"), "%")
    print("出口:", c.get("date"), "当月同比:", c.get("export_yoy"), "% 累计:", c.get("export_cum_yoy"), "%")
    print("PPI:", pp.get("date"), "当月同比:", pp.get("ppi_yoy"), "%")
    print("M1:", m.get("month"), "同比:", m.get("m1_yoy"), "% | M2:", m.get("m2_yoy"), "%")
    print("latest.json 已保存")


if __name__ == "__main__":
    main()