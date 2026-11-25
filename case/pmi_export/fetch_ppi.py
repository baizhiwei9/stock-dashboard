# -*- coding: utf-8 -*-
"""PPI 深度取数（DataHub EDB 版）

九步分析所需的 PPI 分项、PPIRM、行业归因、官方拉动点数与高频价格序列，
全部从 EDB 读取；取数、解析、来源元信息由 EDB 负责，本脚本只做拼装。

输出：data/ppi.json（字段契约与旧版一致）
降级原则：EDB 中缺失的序列写 None，报告层统一标注「该期数据未公布/未取到，跳过」，
禁止用上月值硬凑。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import DATA_DIR, ensure_dirs
import edb_bridge as edb

# 分项行 -> (EDB 序列前缀, 是否有环比)
ROWS = {
    "生产资料": ("CN.PPI.PROD", True),
    "生活资料": ("CN.PPI.LIVE", True),
    "采掘": ("CN.PPI.MINING", True),
    "原材料": ("CN.PPI.RAW", True),
    "加工": ("CN.PPI.PROC", True),
    "食品": ("CN.PPI.FOOD", True),
    "衣着": ("CN.PPI.CLOTH", True),
    "一般日用品": ("CN.PPI.DAILY", True),
    "耐用消费品": ("CN.PPI.DURABLE", True),
    "燃料、动力类": ("CN.PPIRM.FUEL", True),
    "黑色金属材料类": ("CN.PPIRM.FERROUS", True),
    "有色金属材料及电线类": ("CN.PPIRM.NONFERROUS", True),
    "化工原料类": ("CN.PPIRM.CHEM", True),
    "煤炭开采和洗选业": ("CN.PPI.IND.COAL", True),
    "石油和天然气开采业": ("CN.PPI.IND.OILGAS", True),
    "石油、煤炭及其他燃料加工业": ("CN.PPI.IND.FUELPROC", True),
    "黑色金属冶炼和压延加工业": ("CN.PPI.IND.FERROUS", True),
    "有色金属冶炼和压延加工业": ("CN.PPI.IND.NONFERROUS", True),
    "黑色金属矿采选业": ("CN.PPI.IND.FERROUS.MINING", True),
    "化学原料和化学制品制造业": ("CN.PPI.IND.CHEM", True),
    "非金属矿物制品业": ("CN.PPI.IND.NONMETAL", True),
    "有色金属矿采选业": ("CN.PPI.IND.NONFERROUS.MINING", True),
}


def _v(code):
    return edb.latest(code)[1]


def build_article():
    """月度总量、分项表、拉动点数（均来自 EDB 最新一期）。"""
    doc = edb.doc("nbs_ppi")
    extra = doc.get("extra") or {}
    month = extra.get("month") or ""
    art = {
        "error": None,
        "ppi_yoy": _v("CN.PPI.YOY"),
        "ppi_mom": _v("CN.PPI.MOM"),
        "ppi_cum_yoy": (round(_v("CN.PPI.CUM") - 100, 1)
                        if _v("CN.PPI.CUM") is not None else None),
        "ppirm_yoy": _v("CN.PPIRM.YOY"),
        "ppirm_mom": _v("CN.PPIRM.MOM"),
        "ppirm_cum_yoy": _v("CN.PPIRM.CUM"),
        "cum_months": extra.get("cum_months"),
        "table": {},
        "pull": {},
    }
    for name, (base, has_mom) in ROWS.items():
        art["table"][name] = {
            "mom": _v(base + ".MOM") if has_mom else None,
            "yoy": _v(base + ".YOY"),
            "cum": None,
        }
    for key, base, period in (("生产资料_同比", "PROD", "YOY"), ("生活资料_同比", "LIVE", "YOY"),
                              ("生产资料_环比", "PROD", "MOM"), ("生活资料_环比", "LIVE", "MOM")):
        chg = _v(f"CN.PPI.{base}.{period}")
        pts = _v(f"CN.PPI.PULL.{base}.{period}")
        if chg is not None or pts is not None:
            art["pull"][key] = {"chg": chg, "pts": pts}
    return month, art


def build_interp():
    return {
        "error": None,
        "prev_mom": _v("CN.PPI.PREV.MOM"),
        "prev_yoy": _v("CN.PPI.PREV.YOY"),
        "mom_up_pts": _v("CN.PPI.MOM.UP6.PTS"),
        "yoy_up_pts": _v("CN.PPI.UPSIDE9.PTS"),
        "yoy_down_pts": _v("CN.PPI.DOWNSIDE5.PTS"),
    }


def build_series():
    rows = edb.month_series("CN.PPI.YOY", limit=36)
    cum = dict(edb.month_series("CN.PPI.CUM", limit=36))
    return [{"date": f"{m}-01", "ppi_yoy": v, "ppi_cum_yoy": cum.get(m)} for m, v in rows]


def build_hf():
    """高频价格：月均同比由 EDB 日序列聚合计算。"""
    def stat(code, label, unit):
        st = edb.month_stats(code)
        if not st:
            return {"label": label, "error": "EDB 无数据"}
        out = {"label": label, "unit": unit, "month": st.get("month"),
               "avg": st.get("avg"), "base_avg": st.get("base_avg"), "yoy": st.get("yoy")}
        if st.get("cur_avg") is not None:
            out.update({"cur_month": st.get("cur_month"), "cur_avg": st.get("cur_avg"),
                        "cur_yoy": st.get("cur_yoy")})
        return out

    series = {
        "brent": stat("GLOBAL.BRENT", "布伦特原油", "美元/桶"),
        "lme_cu": stat("GLOBAL.LME.CU", "LME铜", "美元/吨"),
        "lme_al": stat("GLOBAL.LME.AL", "LME铝", "美元/吨"),
        "rebar_fut": stat("CN.REBAR.FUT", "螺纹钢期货主力连续(RB0)", "元/吨"),
        "rebar_spot": stat("CN.REBAR.SPOT", "螺纹钢现货(HRB400, 99qh口径)", "元/吨"),
    }
    series["rebar_spot"]["note"] = "本月为月初至今均值，基期为去年同月全月均值，含天数错配"
    series["coal_fut"] = {"label": "动力煤期货主力连续(ZC0)", "available": False,
                          "note": "ZC0 无有效成交，合约流动性衰竭；EDB 未登记该序列"}

    st = edb.month_stats("CN.COMMODITY.CCPI")
    if st:
        series["ccpi"] = {"label": "中国大宗商品价格指数(CCPI)", "unit": "点",
                          "month": st.get("month"), "avg": st.get("avg"),
                          "yoy": st.get("yoy"), "cur_month": st.get("cur_month"),
                          "cur_avg": st.get("cur_avg")}
        mom_prev, _, _ = edb.monthly_mom("CN.COMMODITY.CCPI")
    else:
        series["ccpi"] = {"label": "中国大宗商品价格指数", "error": "EDB 无数据"}

    # CCPI 环比：先用日序列按月均值算，月均值不足两期时退化为最近两月比
    import pandas as pd
    s = edb.series("CN.COMMODITY.CCPI")
    if not s.empty:
        g = s.groupby(pd.to_datetime(s.index).strftime("%Y-%m")).mean()
        cur_m, prev_m = st.get("cur_month"), st.get("month")
        if prev_m and prev_m in g.index:
            prevprev = (pd.Timestamp(prev_m + "-01") - pd.Timedelta(days=1)).strftime("%Y-%m")
            if prevprev in g.index and g[prevprev]:
                series["ccpi"]["mom"] = round((g[prev_m] / g[prevprev] - 1) * 100, 2)
            series["ccpi"]["prev_avg"] = round(float(g[prevprev]), 1) if prevprev in g.index else None
        if cur_m in g.index and prev_m in g.index and g[prev_m]:
            series["ccpi"]["cur_mom"] = round((g[cur_m] / g[prev_m] - 1) * 100, 2)
    return {"series": series}


def main():
    ensure_dirs()
    edb.update(edb.REPORT_CODES)
    month_iso, article = build_article()
    doc = edb.doc("nbs_ppi")
    res = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "datahub.edb",
        "title": doc.get("title") or "",
        "url": doc.get("url") or "",
        "interp_url": (doc.get("extra") or {}).get("interp_url") or "",
        "month": (f"{month_iso[:4]}年{int(month_iso[5:])}月" if month_iso else ""),
        "article": article,
        "interp": build_interp(),
        "series": build_series(),
        "hf": build_hf(),
    }
    path = os.path.join(DATA_DIR, "ppi.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    a = res["article"]
    print("== PPI 取数摘要（DataHub EDB）==")
    print("月份:", res["month"], "|", res["title"])
    print("同比:", a.get("ppi_yoy"), "% | 环比:", a.get("ppi_mom"),
          "% | 购进同比:", a.get("ppirm_yoy"), "%")
    print("分项表行数:", len(a.get("table") or {}), "| 拉动点数:", a.get("pull"))
    print("官方解读:", {k: v for k, v in res["interp"].items() if k != "error"})
    print("高频:", {k: {kk: vv for kk, vv in (v or {}).items() if kk in ("yoy", "cur_yoy", "mom")}
                  for k, v in res["hf"]["series"].items()})
    print("ppi.json 已保存:", path)


if __name__ == "__main__":
    main()