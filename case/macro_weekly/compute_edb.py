# -*- coding: utf-8 -*-
"""从 datahub EDB 取数并计算库存周期与利润转移指标。"""
import sys, json, os
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))


def _ensure_datahub() -> None:
    """定位 datahub：PYTHONPATH -> DATAHUB_PATH -> CASE_WS 约定 -> 本机默认。"""
    try:
        import data_hub.edb  # noqa: F401
        return
    except ImportError:
        pass
    ws = os.environ.get("CASE_WS") or ""
    for c in (os.environ.get("DATAHUB_PATH"),
              os.path.join(ws, "projects", "datahub-multi-tier") if ws else None,
              os.path.join(BASE, "..", "..", "..", "datahub-multi-tier"),
              "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-multi-tier"):
        if c and os.path.isdir(os.path.join(c, "data_hub")):
            sys.path.insert(0, os.path.abspath(c))
            import data_hub.edb  # noqa: F401
            return
    raise SystemExit("未找到 datahub：请设 DATAHUB_PATH 指向 datahub 仓库根目录")


_ensure_datahub()
from data_hub.edb import api  # noqa: E402

DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)

MONTH = ["CN.PPI.YOY", "CN.PPI.MOM", "CN.PPI.PROD.YOY", "CN.PPI.LIVE.YOY",
         "CN.PPI.MINING.YOY", "CN.PPI.RAW.YOY", "CN.PPI.PROC.YOY", "CN.PPI.FOOD.YOY",
         "CN.PPI.DURABLE.YOY", "CN.PPIRM.YOY", "CN.PPIRM.FUEL.YOY", "CN.PPIRM.FERROUS.YOY",
         "CN.PPIRM.NONFERROUS.YOY", "CN.PPIRM.CHEM.YOY", "CN.CPI.YOY", "CN.M1.YOY",
         "CN.PMI.MFG", "CN.PMI.PROD", "CN.PMI.NEW.ORDER", "CN.PMI.RAW.INV", "CN.PMI.FIN.INV",
         "CN.PMI.INPUT.PRICE", "CN.PMI.OUTPUT.PRICE",
         "CN.PROFIT.CUM.YOY", "CN.PROFIT.MONTH.YOY", "CN.PROFIT.INV.YOY", "CN.PROFIT.REVENUE.YOY",
         "CN.PROFIT.INV.AMT", "CN.PROFIT.AR.AMT", "CN.PROFIT.REVENUE.AMT",
         "CN.PROFIT.INV.DAYS", "CN.PROFIT.INV.DAYS.CHG", "CN.PROFIT.AR.DAYS", "CN.PROFIT.AR.DAYS.CHG",
         "CN.PROFIT.OPM", "CN.PROFIT.COST.P100",
         "CN.PROFIT.SHARE.UP", "CN.PROFIT.SHARE.MID", "CN.PROFIT.SHARE.DOWN", "CN.PROFIT.SHARE.UTIL",
         "CN.PROFIT.AMT", "CN.PROFIT.AMT.UP", "CN.PROFIT.AMT.MID", "CN.PROFIT.AMT.DOWN",
         "CN.PPI.IND.COAL.YOY", "CN.PPI.IND.FERROUS.YOY", "CN.PPI.IND.NONFERROUS.YOY",
         "CN.PPI.IND.NONMETAL.YOY", "CN.PPI.IND.CHEM.YOY"]

DAY = ["CN.REBAR.FUT", "CN.FUT.JM", "CN.FUT.SC", "CN.FUT.CU", "CN.FUT.AL",
       "CN.FUT.FG", "CN.FUT.SA", "CN.FUT.TA", "CN.FUT.MA", "CN.FUT.EG",
       "CN.COMMODITY.CCPI", "GLOBAL.BRENT", "GLOBAL.LME.CU", "GLOBAL.LME.AL",
       "CN.EXINV.RB", "CN.EXINV.CU", "CN.EXINV.AL", "CN.EXINV.TA", "CN.EXINV.MA", "CN.EXINV.EG"]

m = api.get_wide(MONTH, start="2024-01-01", force_refresh=True)
d = api.get_wide(DAY, start="2025-06-01", force_refresh=True)
d.index = pd.to_datetime(d.index)
m.index = pd.to_datetime(m.index)
m.to_csv(os.path.join(DATA, "edb_monthly.csv"), encoding="utf-8-sig")
d.to_csv(os.path.join(DATA, "edb_daily.csv"), encoding="utf-8-sig")
print("monthly shape", m.shape, "last", m.dropna(how="all").index[-1].date())
print("daily   shape", d.shape, "last", d.dropna(how="all").index[-1].date())

res = {}
mm = m.dropna(how="all").copy()
mm["nm"] = mm.index.month
mm["real_inv"] = ((1 + mm["CN.PROFIT.INV.YOY"] / 100) / (1 + mm["CN.PPI.YOY"] / 100) - 1) * 100
mm["ratio"] = (mm["CN.PROFIT.INV.AMT"] * 10000) / (mm["CN.PROFIT.REVENUE.AMT"] / mm["nm"])
def runlen(sig: pd.Series) -> int:
    sig = sig.dropna()
    if len(sig) == 0:
        return 0
    cur, n = sig.iloc[-1], 0
    for v in sig.iloc[::-1]:
        if v == cur:
            n += 1
        else:
            break
    return int(n)


def quad(inv, rev):
    if pd.isna(inv) or pd.isna(rev):
        return None
    return "主动补库(过热)" if (inv >= 0 and rev >= 0) else ("被动去库(复苏)" if (rev >= 0) else ("被动补库(滞胀)" if inv >= 0 else "主动去库(衰退)"))
mm["quad"] = [quad(a, b) for a, b in zip(mm["CN.PROFIT.INV.YOY"], mm["CN.PROFIT.REVENUE.YOY"])]
# 宽度（pp）
mm["w_out_in"] = mm["CN.PPI.PROD.YOY"] - mm["CN.PPIRM.YOY"]      # 出厂(生产资料)-购进
mm["w_ppi_cpi"] = mm["CN.PPI.YOY"] - mm["CN.CPI.YOY"]            # PPI-CPI
mm["w_mining_proc"] = mm["CN.PPI.MINING.YOY"] - mm["CN.PPI.PROC.YOY"]   # 采掘-加工
mm["w_raw_live"] = mm["CN.PPI.RAW.YOY"] - mm["CN.PPI.LIVE.YOY"]        # 原材料-生活资料
mm["w_total"] = mm["CN.PPI.YOY"] - mm["CN.PPIRM.YOY"]
mm["trans_coef"] = mm["CN.PPI.YOY"] / mm["CN.PPIRM.YOY"]
mm["trans_coef_prod"] = mm["CN.PPI.PROD.YOY"] / mm["CN.PPIRM.YOY"]

cols = ["CN.PROFIT.INV.YOY", "CN.PROFIT.REVENUE.YOY", "CN.PPI.YOY", "real_inv", "quad", "ratio",
        "CN.PROFIT.INV.DAYS", "CN.PROFIT.INV.DAYS.CHG", "CN.PROFIT.AR.DAYS", "CN.PROFIT.AR.DAYS.CHG",
        "CN.PROFIT.OPM", "CN.PROFIT.COST.P100", "w_out_in", "w_ppi_cpi", "w_mining_proc", "w_raw_live",
        "CN.PMI.RAW.INV", "CN.PMI.FIN.INV", "CN.PMI.NEW.ORDER", "CN.PMI.MFG", "CN.M1.YOY"]
print("\n=== 月度核心（近16期）===")
print(mm[cols].tail(16).round(2).to_string())

# 四象限持续月数
q = mm["quad"].dropna()
cur = q.iloc[-1]
n = 0
for v in reversed(q.tolist()):
    if v == cur:
        n += 1
    else:
        break
res["quad"] = {"current": cur, "months": int(n), "since": str(q.index[-n].date()), "last_month_quad": q.iloc[-2] if len(q) > 1 else None}
# 名义/实际库存连续同向月数 + 背离
inv = mm["CN.PROFIT.INV.YOY"].dropna()
real = mm["real_inv"].dropna()
print(f"\n名义库存同比 {inv.iloc[-1]:.1f}% ({inv.index[-1].strftime('%Y-%m')})  实际库存同比 {real.iloc[-1]:.2f}%")
print("近6月 名义/实际:", [(i.strftime('%y-%m'), round(a, 1), round(b, 1)) for i, a, b in list(zip(inv.index, inv, real))[-6:]])

# 宽度历史（月度）
print("\n=== 宽度月度（近8期）===")
wc = ["CN.PPI.YOY", "CN.PPIRM.YOY", "CN.PPI.PROD.YOY", "CN.PPI.MINING.YOY", "CN.PPI.RAW.YOY",
      "CN.PPI.PROC.YOY", "CN.PPI.LIVE.YOY", "CN.CPI.YOY", "w_out_in", "w_ppi_cpi", "w_mining_proc", "w_raw_live"]
print(mm[wc].dropna(how="all").tail(8).round(2).to_string())

# 周频
dd = d.dropna(how="all").copy()
w = dd.resample("W-FRI").last()
rows = []
for c in dd.columns:
    s = w[c].dropna()
    if len(s) < 20:
        continue
    last = s.iloc[-1]
    ma4 = s.rolling(4).mean(); ma13 = s.rolling(13).mean()
    rows.append({
        "series": c, "last": round(last, 2),
        "1w%": round((last / s.iloc[-2] - 1) * 100, 2),
        "4w%": round((last / s.iloc[-5] - 1) * 100, 2),
        "13w%": round((last / s.iloc[-14] - 1) * 100, 2),
        "ma4_dir": np.sign(round(ma4.iloc[-1] / ma4.iloc[-2] - 1, 4)),
        "ma13_dir": np.sign(round(ma13.iloc[-1] / ma13.iloc[-2] - 1, 4)),
        "same_weeks_ma4": runlen(np.sign(ma4.diff())),
    })
wdf = pd.DataFrame(rows)
print("\n=== 周频（截至 %s）===" % w.index[-1].date())
print(wdf.to_string(index=False))
wdf.to_csv(os.path.join(DATA, "weekly_metrics.csv"), index=False, encoding="utf-8-sig")

# 上下游指数（归一化，2026-06-01=100）
idx_start = "2026-06-01"
up_cols = ["CN.REBAR.FUT", "CN.FUT.JM", "CN.FUT.SC", "CN.FUT.CU", "CN.FUT.AL"]
mid_cols = ["CN.FUT.FG", "CN.FUT.SA", "CN.FUT.TA", "CN.FUT.MA", "CN.FUT.EG"]
base = dd.loc[dd.index >= idx_start]
def mkidx(cols):
    parts = []
    for c in cols:
        s = base[c].dropna()
        parts.append(s / s.iloc[0] * 100)
    return pd.concat(parts, axis=1).mean(axis=1)
up = mkidx(up_cols); mid = mkidx(mid_cols); ccpi = base["CN.COMMODITY.CCPI"].dropna() / base["CN.COMMODITY.CCPI"].dropna().iloc[0] * 100
wb = pd.DataFrame({"up": up, "mid": mid, "ccpi": ccpi}).resample("W-FRI").last()
wb["spread"] = wb["mid"] - wb["up"]
wb["d_spread"] = wb["spread"].diff()
print("\n=== 上下游指数(以2026-06-01=100) 近10周 ===")
print(wb.tail(10).round(1).to_string())

# 连续同向周数（spread 变化方向）
ds = wb["d_spread"].dropna()
sign = np.sign(ds)
cur_sign = sign.iloc[-1]
cnt = 0
for v in sign.iloc[::-1]:
    if v == cur_sign:
        cnt += 1
    else:
        break
res["spread_run"] = {"last_d": round(ds.iloc[-1], 2), "direction": "走阔(中游强于上游)" if cur_sign > 0 else "收窄(下游弱于上游)", "weeks": int(cnt)}

# 库存周变化（交易所库存口径）
print("\n=== 交易所库存周度（近6周）===")
invw = dd[[c for c in dd.columns if c.startswith("CN.EXINV")]].resample("W-FRI").last().dropna(how="all")
print(invw.tail(6).to_string())
res["inv_chg_4w"] = {c: [round(float(invw[c].iloc[-1]), 1), round(float(invw[c].iloc[-1] / invw[c].iloc[-5] - 1) * 100, 2)] for c in invw.columns if invw[c].notna().sum() >= 5}

# PPI 明细（梯度）+ 基期提示
print("\n=== PPI 三层梯度（月度，%）===")
pp = mm[["CN.PPI.MINING.YOY", "CN.PPI.RAW.YOY", "CN.PPI.PROC.YOY", "CN.PPI.LIVE.YOY", "CN.PPI.PROD.YOY", "CN.PPIRM.YOY"]].dropna(how="all")
print(pp.tail(8).round(2).to_string())

# 工企分行业利润（最新一期，占比与增速）
print("\n=== 工企最新（%s）===" % mm.index[-1].strftime("%Y-%m"))
print(mm.loc[mm.index[-1], ["CN.PROFIT.CUM.YOY", "CN.PROFIT.MONTH.YOY", "CN.PROFIT.OPM", "CN.PROFIT.COST.P100", "CN.PROFIT.INV.AMT", "CN.PROFIT.AR.AMT", "CN.PROFIT.REVENUE.AMT"]].to_string())

json.dump({k: v for k, v in res.items() if isinstance(v, (dict, list, str, int, float))},
          open(os.path.join(DATA, "metrics.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print("\nsaved metrics.json")