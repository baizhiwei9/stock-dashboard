# -*- coding: utf-8 -*-
"""美股期权风险监控数据抓取: CBOE 指数历史 + CBOE 延迟期权链 + ETF 日线"""
import os, json, csv, io, subprocess, datetime
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
os.makedirs(D, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"
TODATE = os.environ.get("TODATE") or datetime.date.today().strftime("%Y-%m-%d")


def curl(url, timeout=60):
    r = subprocess.run(["curl", "-sL", "-A", UA, "-m", str(timeout), url],
                       capture_output=True)
    return r.stdout


def fetch_hist(name):
    raw = curl(CBOE.format(name))
    if not raw or raw[:15].lower().startswith(b"<?xml"):
        print("FAIL", name); return
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    df["DATE"] = df["DATE"].astype(str)
    df.to_json(os.path.join(D, f"us_{name}.json"), orient="records", force_ascii=False)
    print(name, df.shape, df.tail(2).to_dict("records"))


def fetch_chain(sym):
    raw = curl(f"https://cdn.cboe.com/api/global/delayed_quotes/options/_{sym}.json", timeout=90)
    if not raw or raw[:15].lower().startswith(b"<?xml"):
        print("FAIL chain", sym); return
    j = json.loads(raw)
    d = j["data"]
    keep = ["option", "bid", "ask", "iv", "delta", "gamma", "vega", "theta",
            "open_interest", "volume", "last_trade_price"]
    rows = [{k: o.get(k) for k in keep} for o in d["options"]]
    out = {"symbol": sym, "spot": d.get("current_price") or d.get("close"),
           "close": d.get("close"), "iv30": d.get("iv30"),
           "iv30_change": d.get("iv30_change"), "timestamp": j.get("timestamp"),
           "options": rows}
    with open(os.path.join(D, f"us_chain_{sym}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("chain", sym, "n=", len(rows), "spot=", out["spot"], "iv30=", out["iv30"])


def fetch_etf(sym):
    url = (f"https://api.nasdaq.com/api/quote/{sym}/historical?assetclass=etf"
           f"&fromdate=2024-09-01&todate={TODATE}&limit=600")
    raw = curl(url)
    try:
        j = json.loads(raw)
        rows = j["data"]["tradesTable"]["rows"]
    except Exception as e:
        print("FAIL etf", sym, str(e)[:60]); return
    df = pd.DataFrame(rows)
    df = df.rename(columns={"date": "Date", "close": "Close"})
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    df["Close"] = df["Close"].str.replace(",", "").astype(float)
    df = df.sort_values("Date")
    df.to_json(os.path.join(D, f"us_etf_{sym}.json"), orient="records", force_ascii=False)
    print("etf", sym, df.shape, df.tail(1).to_dict("records"))


if __name__ == "__main__":
    for n in ["VIX", "VIX9D", "VIX3M", "VXN", "RVX", "SKEW", "VVIX"]:
        fetch_hist(n)
    for s in ["SPX", "NDX", "RUT"]:
        fetch_chain(s)
    for s in ["SPY", "QQQ", "IWM"]:
        fetch_etf(s)