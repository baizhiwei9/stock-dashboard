# -*- coding: utf-8 -*-
"""期权波动率预测 —— 未来 5 个交易日（确定性程序，不依赖模型逐次推理）

方法（可解释、可复现）：
  主模型：对「隐含波动率指数序列」做对数空间的均值回复 AR(1)/OU 拟合
          x_t = ln(v_t);  x_{t+1} = c + phi*x_t + eps
          => 长期均值 mu = c/(1-phi)
          => E[x_{t+h}] = mu + phi^h (x_t - mu)
          => 点预测 v_{t+h} = exp(E[x_{t+h}])  (对数正态中位数)
          => 80% 预测区间 = exp(E ± z0.9 * sigma_h),
             sigma_h = sigma_eps * sqrt((1 - phi^{2h})/(1 - phi^2))
  参考锚：标的日收益的 RiskMetrics EWMA 实现波动率(lambda=0.94, 年化%)，作为独立参照
  分期：t+1..t+5 按各自市场下一交易日历（A股取上证日历窗口外的工作日；美股取工作日）

数据来源：
  A股隐含波动率: 券商 QVIX 序列(300ETF/500ETF/KC50/CYB)；中证1000 用 MO 链 ATM 自算(data/ashare_chain_metrics.json 的 iv_near)
  美股隐含波动率: CBOE 指数序列(VIX/VXN/RVX)
  标的行情(用于实现波动率): data/underlying_hist.json(ETF/指数收盘) 与 data/us_etf_*.json(SPY/QQQ/IWM)
缺数据一律写「待更新」，不插值。
"""
import os
import json
import math
import datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
H = 5                      # 预测 5 个交易日
Z90 = 1.2815515655446004   # 80% 双侧区间
LAMBDA = 0.94              # RiskMetrics EWMA
WIN = 252                  # 拟合窗口(与基线窗口一致)


def _d(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y%m%d")


def _biz_next(start_yyyymmdd, n):
    """start 之后的 n 个工作日(跳过周六周日)。用于两市场下一交易日历。"""
    y, m, d = int(start_yyyymmdd[:4]), int(start_yyyymmdd[4:6]), int(start_yyyymmdd[6:8])
    cur = datetime.date(y, m, d)
    out = []
    while len(out) < n:
        cur += datetime.timedelta(days=1)
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
    return out


def _fmt(s):
    return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8]) if len(str(s)) == 8 else str(s)


def _qvix(fn):
    p = os.path.join(D, fn)
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    ser = []
    for r in d:
        c = r.get("close")
        if c is None:
            continue
        try:
            c = float(c)
        except Exception:
            continue
        if not math.isfinite(c) or c <= 0:   # 剔除 0/None/异常
            continue
        ser.append((_d(r["date"]), c))
    return ser


def _chain_iv(key):
    p = os.path.join(D, "ashare_chain_metrics.json")
    if not os.path.exists(p):
        return []
    import pandas as pd
    cm = pd.read_json(p, orient="records")
    cm["date"] = cm["date"].astype(str)
    g = cm[cm["target"] == key].sort_values("date")
    ser = []
    for _, r in g.iterrows():
        v = r["iv_near"]
        if v is None or not np.isfinite(v) or v <= 0:
            continue
        ser.append((str(r["date"]), float(v) * 100.0))
    return ser


def _cboe(fn):
    p = os.path.join(D, fn)
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    ser = []
    for r in d:
        try:
            dt = datetime.datetime.strptime(r["DATE"], "%m/%d/%Y").strftime("%Y%m%d")
            c = float(r["CLOSE"])
        except Exception:
            continue
        if math.isfinite(c) and c > 0:
            ser.append((dt, c))
    return ser


def _ewma_hv(closes):
    """风险因子 EWMA 年化实现波动率(%)"""
    c = np.asarray([x for x in closes if x is not None and np.isfinite(x)], dtype=float)
    if len(c) < 40:
        return None
    r = np.diff(np.log(c[-260:]))
    var = float(np.var(r[:20], ddof=1))
    for x in r[20:]:
        var = LAMBDA * var + (1 - LAMBDA) * x * x
    return math.sqrt(var * 252.0) * 100.0


def fit_ou(ser, window=WIN, minobs=60):
    """对数空间 AR(1)/OU 拟合 + h 步预测。返回 dict 或 None。"""
    ser = ser[-window:] if len(ser) > window else ser
    if len(ser) < minobs:
        return None
    dates = [s[0] for s in ser]
    v = np.asarray([s[1] for s in ser], dtype=float)
    x = np.log(v)
    x0 = x[:-1]
    x1 = x[1:]
    A = np.column_stack([np.ones_like(x0), x0])
    coef, *_ = np.linalg.lstsq(A, x1, rcond=None)
    c, phi = float(coef[0]), float(coef[1])
    if not (0.0 < phi < 1.0):          # 保证均值回复、平稳
        return None
    resid = x1 - (c + phi * x0)
    sig = float(np.std(resid, ddof=2))
    mu = c / (1.0 - phi)
    x_last = float(x[-1])
    last_date, last_vol = ser[-1][0], float(ser[-1][1])

    path = []
    for h in range(1, H + 1):
        ex = mu + (phi ** h) * (x_last - mu)
        var_h = sig * sig * (1.0 - phi ** (2 * h)) / (1.0 - phi * phi)
        sd_h = math.sqrt(max(var_h, 0.0))
        path.append(dict(h=h, point=math.exp(ex),
                         lo=math.exp(ex - Z90 * sd_h),
                         hi=math.exp(ex + Z90 * sd_h)))
    return dict(last_date=last_date, last_vol=last_vol, mu=math.exp(mu),
                phi=phi, sigma=sig, n=len(ser), path=path)


TARGETS = [
    # key, label, market, vol_name, series_builder, underlying_code / us_etf file
    ("300ETF", "沪深300ETF期权", "A", "QVIX", lambda: _qvix("qvix_300etf.json"), ("uh", "510300.SH")),
    ("500ETF", "中证500ETF期权", "A", "QVIX", lambda: _qvix("qvix_500etf.json"), ("uh", "510500.SH")),
    ("KC50", "科创50ETF期权", "A", "QVIX", lambda: _qvix("qvix_kcb.json"), ("uh", "588000.SH")),
    ("CYB", "创业板ETF期权", "A", "QVIX", lambda: _qvix("qvix_cyb.json"), ("uh", "159915.SZ")),
    ("ZZ1000", "中证1000(MO股指期权)", "A", "ATM-IV", lambda: _chain_iv("ZZ1000"), ("uh", "000852.SH")),
    ("SPX", "标普500", "U", "VIX", lambda: _cboe("us_VIX.json"), ("us", "SPY")),
    ("NDX", "纳斯达克100", "U", "VXN", lambda: _cboe("us_VXN.json"), ("us", "QQQ")),
    ("RUT", "罗素2000", "U", "RVX", lambda: _cboe("us_RVX.json"), ("us", "IWM")),
]


def _uh_closes(code):
    p = os.path.join(D, "underlying_hist.json")
    if not os.path.exists(p):
        return []
    import pandas as pd
    uh = pd.read_json(p, orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    g = uh[uh["code"] == code].sort_values("trade_date")
    return g["close"].tolist()


def _us_closes(code):
    p = os.path.join(D, "us_etf_%s.json" % code)
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    out = []
    for r in d:
        v = r.get("Close") or r.get("close") or r.get("adj_close") or r.get("CLOSE")
        try:
            v = float(v)
        except Exception:
            continue
        if math.isfinite(v) and v > 0:
            out.append(v)
    return out


def main():
    out = {"generated": datetime.date.today().strftime("%Y-%m-%d"),
           "horizon_days": H, "method": "OU(log-IV AR1) + EWMA实现波动率参照", "targets": {}}
    for key, label, market, vname, builder, und in TARGETS:
        ser = builder()
        r = fit_ou(ser)
        entry = dict(key=key, label=label, market=market, vol_name=vname,
                     ok=bool(r), method="OU/AR(1) on log(vol)")
        if not r:
            entry["note"] = "有效样本不足或序列不可用，待更新"
            out["targets"][key] = entry
            continue
        # 实现波动率参照
        if und[0] == "uh":
            cl = _uh_closes(und[1])
            entry["hv_code"] = und[1]
        else:
            cl = _us_closes(und[1])
            entry["hv_code"] = und[1]
        hv = _ewma_hv(cl)
        entry.update(last_date=r["last_date"], last_vol=r["last_vol"],
                     long_mean=r["mu"], phi=r["phi"], sigma_log=r["sigma"], n=r["n"],
                     hv_ewma=hv)
        # 预测日期：各市场下一交易日历
        base = r["last_date"]
        dts = _biz_next(base, H)
        path = []
        for i, p in enumerate(r["path"]):
            path.append(dict(date=dts[i], date_fmt=_fmt(dts[i]), h=p["h"],
                             point=p["point"], lo=p["lo"], hi=p["hi"]))
        entry["forecast"] = path
        out["targets"][key] = entry

    with open(os.path.join(D, "vol_forecast.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    # 控制台摘要
    for k, e in out["targets"].items():
        if not e["ok"]:
            print(k, "待更新")
            continue
        fs = " ".join("%s:%.2f[%.1f~%.1f]" % (p["date_fmt"], p["point"], p["lo"], p["hi"])
                      for p in e["forecast"])
        print("%-8s %s 最新%.2f(长期均值%.2f,phi=%.3f) HV(EWMA)=%s\n         %s"
              % (k, e["vol_name"], e["last_vol"], e["long_mean"], e["phi"],
                 ("%.2f" % e["hv_ewma"]) if e["hv_ewma"] else "待更新", fs))


if __name__ == "__main__":
    main()