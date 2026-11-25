# -*- coding: utf-8 -*-
"""美股三标的(标普500/纳斯达克100/罗素2000)期权风险指标:
   官方波动率指数(截至最新收盘) + CBOE 延迟链(盘中快照) 的 R / 偏度 / PCR / GEX / Flip"""
import os, json, re, math
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
# 美股三轨的计算原语（atm_iv / rr25 / GEX / Flip / 分位 / 假日历）自 2026-11-13 起
# 统一走 options_utility.calc.us.index_options；数据目录用本链路 data/ 覆盖。
os.environ.setdefault("OPTIONS_US_DATA_DIR", D)
import lib_adapter as LIB  # noqa: E402

_CU = LIB.calc_us
# 链快照日：由 main() 从 CBOE 延迟链文件的 timestamp（北京时间）折算为美东交易日。
# 禁止硬编码，避免链快照日落后于实际抓取的链数据（曾出现 10-21 vs 实际 10-28）。
SNAP = None


def resolve_snap(fn="us_chain_SPX.json"):
    """链文件 timestamp（北京时间）-> 快照所在美东交易日（委托库函数）。"""
    return _CU.resolve_snap(fn)

TARGETS = [
    dict(key="SPX", name="标普500(S&P 500)", idx="VIX", idx_file="us_VIX.json",
         etf="SPY", chain="us_chain_SPX.json", skew_file="us_SKEW.json", vvix_file="us_VVIX.json"),
    dict(key="NDX", name="纳斯达克100(Nasdaq 100)", idx="VXN", idx_file="us_VXN.json",
         etf="QQQ", chain="us_chain_NDX.json", skew_file=None, vvix_file=None),
    dict(key="RUT", name="罗素2000(小盘股 Russell 2000)", idx="RVX", idx_file="us_RVX.json",
         etf="IWM", chain="us_chain_RUT.json", skew_file=None, vvix_file=None),
]

PAT = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def pct_rank(arr, val):
    """分位（委托库函数）。"""
    return _CU.pct_rank(arr, val)


def hv(closes, n=20):
    """历史波动率（委托库函数）。"""
    return _CU.hv(closes, n)


def third_friday(y, m):
    """当月第三个周五（委托库函数）。"""
    return _CU.third_friday(y, m)


def load_chain(fn):
    """读 CBOE 延迟链（委托库函数，数据目录已由 OPTIONS_US_DATA_DIR 指到本链路）。"""
    return _CU.load_chain(fn)


def monthly_expiries(df, after):
    """月度到期日（委托库函数）。"""
    return _CU.monthly_expiries(df, after)


def atm_iv(df, exp, S):
    """ATM IV（委托库函数）。"""
    return _CU.atm_iv(df, exp, S)


def rr25(df, exp, S, atm):
    """25Δ 风险逆转（委托库函数）。"""
    return _CU.rr25(df, exp, S, atm)


def main():
    global SNAP
    if SNAP is None:
        SNAP = resolve_snap()
        if SNAP is None:
            raise SystemExit("compute_us: 无法解析美股链快照日（us_chain_SPX.json 缺 timestamp）")
        print("[snap]", SNAP.strftime("%Y-%m-%d"))
    out = dict(snapshot=SNAP.strftime("%Y-%m-%d"), targets={})
    for T in TARGETS:
        # ---------- 官方波动率指数(截至最新收盘) ----------
        idx = pd.read_json(os.path.join(D, T["idx_file"]), orient="records")
        idx["d"] = pd.to_datetime(idx["DATE"], format="%m/%d/%Y")
        idx = idx.sort_values("d")
        col = T["idx"] if T["idx"] in idx.columns else "CLOSE"
        s = idx.dropna(subset=[col]).tail(252)
        idx_last_date = idx["d"].iloc[-1].strftime("%Y-%m-%d")
        idx_last = float(idx[col].iloc[-1])
        idx_pct = pct_rank(s[col].tolist(), idx_last)
        idx_min252, idx_max252 = float(s[col].min()), float(s[col].max())

        # 期限结构(仅标普有 VIX9D/VIX3M)
        term = None
        if T["key"] == "SPX":
            v9 = pd.read_json(os.path.join(D, "us_VIX9D.json"), orient="records")
            v3 = pd.read_json(os.path.join(D, "us_VIX3M.json"), orient="records")
            v9["d"] = pd.to_datetime(v9["DATE"], format="%m/%d/%Y")
            v3["d"] = pd.to_datetime(v3["DATE"], format="%m/%d/%Y")
            term = dict(vix9d=float(v9["CLOSE"].iloc[-1]), vix3m=float(v3["CLOSE"].iloc[-1]),
                        d9=v9["d"].iloc[-1].strftime("%Y-%m-%d"),
                        d3=v3["d"].iloc[-1].strftime("%Y-%m-%d"))

        # SKEW(仅标普有官方指数)
        skew = None
        if T["skew_file"]:
            sk = pd.read_json(os.path.join(D, T["skew_file"]), orient="records")
            sk["d"] = pd.to_datetime(sk["DATE"], format="%m/%d/%Y")
            sk = sk.sort_values("d")
            sk252 = sk.dropna(subset=["SKEW"]).tail(252)
            skew = dict(val=float(sk["SKEW"].iloc[-1]), date=sk["d"].iloc[-1].strftime("%Y-%m-%d"),
                        pct=pct_rank(sk252["SKEW"].tolist(), float(sk["SKEW"].iloc[-1])),
                        med=float(np.median(sk252["SKEW"])),
                        sd=float(np.std(sk252["SKEW"], ddof=1)))

        # ---------- ETF 日线 -> HV20 ----------
        etf = pd.read_json(os.path.join(D, "us_etf_%s.json" % T["etf"]), orient="records")
        etf["d"] = pd.to_datetime(etf["Date"])
        etf = etf.sort_values("d")
        h20 = hv(etf["Close"].tolist())
        etf_last_date = etf["d"].iloc[-1].strftime("%Y-%m-%d")
        etf_last = float(etf["Close"].iloc[-1])

        # ---------- CBOE 延迟链 ----------
        raw, df = load_chain(T["chain"])
        S = float(raw["spot"])
        df = df[df.exp > pd.Timestamp(SNAP)]
        mons = monthly_expiries(df, pd.Timestamp(SNAP))
        near, nxt = mons[0], mons[1]
        iv_near, _ = atm_iv(df, near, S)
        iv_next, _ = atm_iv(df, nxt, S)
        R = (iv_near / iv_next) if (iv_near and iv_next) else None
        rr_n = rr25(df, near, S, iv_near)

        # PCR(全部在场合约持仓)
        po = df[df.cp == "P"].oi.fillna(0).sum()
        co = df[df.cp == "C"].oi.fillna(0).sum()
        pcr = float(po / co) if co else None

        # GEX 代理: OI x gamma x sign(认购+/认沽-), 归一化
        g = df[df.gamma.notna() & df.oi.notna()].copy()
        g["sg"] = g.gamma * g.oi * np.where(g.cp == "C", 1.0, -1.0)
        byk = g.groupby("K", as_index=False)["sg"].sum().sort_values("K")
        tot_oi = float(g.oi.sum()) or 1.0
        gex_norm = float(byk.sg.sum()) / tot_oi
        ks, gs = byk.K.tolist(), byk.sg.tolist()
        cands = [(ks[i - 1] + ks[i]) / 2.0 for i in range(1, len(ks)) if gs[i - 1] < 0 <= gs[i]]
        flip = min(cands, key=lambda x: abs(x - S)) if cands else None
        flip_dist = abs(S - flip) / S * 100 if flip else None

        out["targets"][T["key"]] = dict(
            name=T["name"], idx=T["idx"], idx_name=T["idx"],
            idx_last=idx_last, idx_last_date=idx_last_date, idx_pct=idx_pct,
            idx_min252=idx_min252, idx_max252=idx_max252,
            etf=T["etf"], etf_last=etf_last, etf_last_date=etf_last_date, hv20=h20,
            ivhv=(idx_last - h20) if (idx_last and h20) else None,
            term=term, skew=skew,
            chain_spot=S, chain_ts=raw["timestamp"], chain_iv30=float(raw["iv30"]),
            near=str(near.date()), next=str(nxt.date()),
            iv_near=iv_near, iv_next=iv_next, R=R, rr=rr_n,
            pcr=pcr, gex_norm=gex_norm, flip=flip, flip_dist=flip_dist,
            n_chain=len(df),
        )

    with open(os.path.join(D, "us_final.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1,
                  default=lambda o: bool(o) if hasattr(o, "item") else float(o))

    for k, v in out["targets"].items():
        print("=====", k, v["name"])
        print("  官方指数 %s=%.2f (%s) 分位=%.1f%% [252d %.2f~%.2f]" %
              (v["idx"], v["idx_last"], v["idx_last_date"], v["idx_pct"], v["idx_min252"], v["idx_max252"]))
        print("  ETF %s=%.2f (%s) HV20=%.2f 隐历差=%.2f" %
              (v["etf"], v["etf_last"], v["etf_last_date"], v["hv20"], v["ivhv"]))
        print("  chain spot=%.2f ts=%s iv30=%.2f | near %s iv=%.2f / next %s iv=%.2f R=%.3f" %
              (v["chain_spot"], v["chain_ts"], v["chain_iv30"], v["near"], v["iv_near"],
               v["next"], v["iv_next"], v["R"]))
        print("  rr25=%.2f pcr=%.3f gex_norm=%.6f flip=%s flip_dist=%s" %
              (v["rr"], v["pcr"], v["gex_norm"], v["flip"],
               ("%.2f%%" % v["flip_dist"]) if v["flip_dist"] else None))
        if v["skew"]:
            print("  SKEW=%.2f (%s) 分位=%.1f%% med=%.2f sd=%.2f" %
                  (v["skew"]["val"], v["skew"]["date"], v["skew"]["pct"], v["skew"]["med"], v["skew"]["sd"]))
        if v["term"]:
            print("  VIX9D=%.2f VIX3M=%.2f" % (v["term"]["vix9d"], v["term"]["vix3m"]))


if __name__ == "__main__":
    main()