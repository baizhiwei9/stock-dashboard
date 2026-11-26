#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流动性指标计算与预警规则脚本。

输入（macroliq_fetcher/data/ 下已核验文件）：
  fred_data.json        FRED 8 序列（260 条历史）
  fx_bs_data.json       汇率 DEXUSEU/DEXJPUS/DEXCHUS + ECBASSETSW/JPNASSETS
  acm_full.json         NY Fed ACM Term Premium 全历史（月频）
  ice_move_record.json  ICE MOVE（首期基线）
  ecb_m2_eur_full.json  ECB M2
  boj_m2_jpy_full.json  BOJ M2

输出（美债工作目录）：
  liquidity_metrics.json

单位口径：
  WALCL/WTREGEN 百万美元；RRPONTSYD 十亿美元（×1000 转百万美元）；
  ECBASSETSW 百万欧元；JPNASSETS 亿日元；DEXUSEU=USD/EUR；DEXJPUS=JPY/USD。
  ACM 月频，20 期均线按 20 个月计；MOVE 无历史，仅首期基线。
  Global_Liq = US_Net_Liq + ECB_BS_USD + BOJ_BS_USD + GlobalM2_USD(ECB+BOJ,缺US/PBoC)；PBoC 项缺失。
"""
import datetime
import json
import os

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "ses_gffe5ee1d17e02ffeBBPuFJhbMDuVqs", "macroliq_fetcher", "data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "liquidity_metrics.json")


def load(name):
    try:
        with open(os.path.join(BASE, name), encoding="utf-8") as fp:
            return json.load(fp)
    except Exception as exc:  # noqa: BLE001
        return {"_err": str(exc)}


def as_obs(series):
    return [(o["date"], o["value"]) for o in series.get("observations", [])]


def iso_key(date_str):
    y, m, d = date_str.split("-")
    return datetime.date(int(y), int(m), int(d)).isocalendar()[:2]


def weekly_series(day_obs):
    """日频转周频：按 ISO 周取每周最后一个观测。"""
    out = {}
    for d, v in day_obs:
        out[iso_key(d)] = (d, v)
    return [out[k] for k in sorted(out.keys())]


def wma20(pairs):
    return round(sum(v for _, v in pairs[-20:]) / 20.0, 4) if len(pairs) >= 20 else None


def pct_wow(pairs):
    if len(pairs) < 2 or pairs[-2][1] in (None, 0):
        return None
    return round((pairs[-1][1] - pairs[-2][1]) / pairs[-2][1] * 100.0, 4)


def diff_wow(pairs):
    if len(pairs) < 2:
        return None
    return round(pairs[-1][1] - pairs[-2][1], 4)


def n_down(pairs, n):
    if len(pairs) < n:
        return False
    tail = pairs[-n:]
    return all(tail[i + 1][1] < tail[i][1] for i in range(n - 1))


def pair_weekly(a, b):
    """两个周序列按 ISO 周对齐，返回 [(date, a-b)]。"""
    ma, mb = {iso_key(d): (d, v) for d, v in a}, {iso_key(d): (d, v) for d, v in b}
    return [(ma[k][0], ma[k][1] - mb[k][1]) for k in sorted(ma.keys()) if k in mb]


def color_updown(delta, red=None, yellow=0.0):
    if delta is None:
        return "灰"
    if red is not None and delta >= red:
        return "红"
    return "绿" if delta >= yellow else "黄"


def main():
    fred = load("fred_data.json")
    fx = load("fx_bs_data.json")
    acm = load("acm_full.json")
    move = load("ice_move_record.json")

    s = fred.get("series", {})
    walcl = as_obs(s.get("WALCL", {}))
    tga = as_obs(s.get("WTREGEN", {}))
    rrp = as_obs(s.get("RRPONTSYD", {}))
    sofr = as_obs(s.get("SOFR", {}))
    iorb = as_obs(s.get("IORB", {}))

    fx_s = fx.get("series", {})
    usd_eur = as_obs(fx_s.get("DEXUSEU", {}))
    jpy_usd = as_obs(fx_s.get("DEXJPUS", {}))
    cny_usd = as_obs(fx_s.get("DEXCHUS", {}))
    ecb_bs = as_obs(fx_s.get("ECBASSETSW", {}))
    boj_bs = as_obs(fx_s.get("JPNASSETS", {}))

    # ---- 1. US_Net_Liquidity（周频） ----
    rrp_week_mn = [(d, v * 1000.0) for d, v in weekly_series(rrp)]
    rrp_map = {iso_key(d): v for d, v in rrp_week_mn}
    tga_map = {iso_key(d): v for d, v in tga}
    net = []
    for d, wv in walcl:
        k = iso_key(d)
        if k in rrp_map and k in tga_map:
            net.append((d, round(wv - tga_map[k] - rrp_map[k], 2)))
    net_wow = pct_wow(net)
    net_wma = wma20(net)
    net_3down = n_down(net, 3)
    net_sig = "黄" if net_3down else (
        "绿" if (net_wow is not None and net_wow > 0 and net_wma is not None and net[-1][1] > net_wma) else (
            "黄" if (net_wow is not None and net_wow < 0) else "绿"))

    # ---- 2. SOFR_IORB（bp，周频差值） ----
    si = pair_weekly(weekly_series(sofr), weekly_series(iorb))
    si_last_bp = round(si[-1][1] * 100.0, 2) if si else None
    si_wow_bp = round(diff_wow(si) * 100.0, 2) if si else None
    si_wma_bp = round(wma20(si) * 100.0, 2) if si and len(si) >= 20 else None
    si_sig = "红" if (si_last_bp is not None and si_last_bp > 15) else (
        "黄" if (si_last_bp is not None and si_last_bp > 0) else "绿")

    # ---- 3. MOVE ----
    move_h = move.get("history") or []
    move_last = move_h[-1] if move_h else {}
    move_val = move_last.get("value")
    move_sig = "红" if (move_val is not None and move_val > 100) else "黄"

    # ---- 4. ACM Term Premium（月频 acmtp10，日期含 %d-%b-%Y 需转可排序） ----
    acm_rows = acm if isinstance(acm, list) else []
    acm_pairs = []
    for r in acm_rows:
        d = r.get("DATE")
        tp = r.get("ACMTP10") or r.get("acmtp10")
        if d and tp is not None:
            try:
                parsed = datetime.datetime.strptime(d.strip(), "%d-%b-%Y")
            except (ValueError, AttributeError):
                try:
                    parsed = datetime.datetime.strptime(d.strip(), "%Y-%m-%d")
                except (ValueError, AttributeError):
                    continue
            try:
                acm_pairs.append((parsed.strftime("%Y-%m-%d"), float(tp)))
            except (TypeError, ValueError):
                pass
    acm_pairs.sort(key=lambda x: x[0])
    acm_last = acm_pairs[-1][1] if acm_pairs else None
    acm_wow = diff_wow(acm_pairs)
    acm_wma = wma20(acm_pairs)
    acm_sig = "红" if (acm_last is not None and acm_last > 1.0) else (
        "黄" if (acm_last is not None and acm_last > 0.5) else "绿")

    # ---- 5. ECB / BOJ 资产负债表 ----
    ecb_week = weekly_series(ecb_bs)
    boj_week = boj_bs  # JPNASSETS 月频
    ecb_val, ecb_date = (ecb_week[-1][1], ecb_week[-1][0]) if ecb_week else (None, None)
    ecb_wow, ecb_wma = pct_wow(ecb_week), wma20(ecb_week)
    ecb_sig = color_updown(ecb_wow)
    boj_val, boj_date = (boj_week[-1][1], boj_week[-1][0]) if boj_week else (None, None)
    boj_wow, boj_wma = pct_wow(boj_week), wma20(boj_week)
    boj_sig = color_updown(boj_wow)

    # ---- 6. Global_Liq（百万USD） ----
    eur = usd_eur[-1][1] if usd_eur else None
    jpy = jpy_usd[-1][1] if jpy_usd else None
    cny = cny_usd[-1][1] if cny_usd else None
    ecb_bs_usd = round(ecb_val * eur, 2) if (ecb_val is not None and eur) else None
    # JPNASSETS 亿日元 -> 美元 -> 百万USD
    boj_bs_usd = round(boj_val * 1e8 / jpy / 1e6, 2) if (boj_val is not None and jpy) else None

    ecb_m2 = load("ecb_m2_eur_full.json")
    boj_m2 = load("boj_m2_jpy_full.json")
    ecb_m2_last = ecb_m2[-1]["m2_eur_millions"] if isinstance(ecb_m2, list) and ecb_m2 else None
    boj_m2_last = boj_m2[-1]["m2_jpy_100m"] if isinstance(boj_m2, list) and boj_m2 else None
    ecb_m2_usd = round(ecb_m2_last * eur, 2) if (ecb_m2_last is not None and eur) else None
    # BOJ M2 单位 亿日元(100m JPY) -> 日元 -> USD -> 百万USD
    boj_m2_usd = round(boj_m2_last * 1e8 / jpy / 1e6, 2) if (boj_m2_last is not None and jpy) else None
    global_m2_usd = round(ecb_m2_usd + boj_m2_usd, 2) if (ecb_m2_usd is not None and boj_m2_usd is not None) else None

    net_val = net[-1][1] if net else None
    gl = None
    if NetOK := all(v is not None for v in (net_val, ecb_bs_usd, boj_bs_usd)):
        gl = round(net_val + ecb_bs_usd + boj_bs_usd + (global_m2_usd or 0), 2)

    # ---- 阶段标签 ----
    stage = "平静"
    if move_val is not None and (move_val > 100 or (si_last_bp is not None and si_last_bp > 15)):
        stage = "动荡"
    elif move_val is not None and move_val > 80 and net_wow is not None and net_wow > 0:
        stage = "投机"
    elif net_wow is not None and net_wow > 0:
        stage = "反弹"
    else:
        stage = "平静"

    # ---- 预警 ----
    alerts = []
    if move_val is not None and move_val > 100:
        alerts.append({"level": "红", "label": "抵押品链 预警", "why": "MOVE=%.2f > 100" % move_val})
    if si_last_bp is not None and si_last_bp > 15:
        alerts.append({"level": "红", "label": "抵押品链 预警", "why": "SOFR_IORB=%.1fbp > 15bp" % si_last_bp})
    if net_3down:
        alerts.append({"level": "黄", "label": "流动性流量拐点", "why": "US_Net_Liquidity 连续3周下降"})

    out = {
        "generated_at": datetime.date.today().isoformat(),
        "metrics": {
            "US_Net_Liquidity": {"value": net_val, "unit": "百万USD", "date": net[-1][0] if net else None,
                                 "wow_pct": net_wow, "wma20": net_wma, "signal": net_sig},
            "SOFR_IORB": {"value": si_last_bp, "unit": "bp", "date": si[-1][0] if si else None,
                          "wow_bp": si_wow_bp, "wma20_bp": si_wma_bp, "signal": si_sig},
            "MOVE": {"value": move_val, "unit": "index", "date": move_last.get("date"),
                     "wow_pct": None, "wma20": None, "signal": move_sig,
                     "note": "首期基线无历史，周环比/20周均线暂无"},
            "ACM_TermPremium_10Y": {"value": acm_last, "unit": "%", "date": acm_pairs[-1][0] if acm_pairs else None,
                                    "wow_pts": acm_wow, "wma20": acm_wma, "signal": acm_sig,
                                    "note": "NY Fed ACM 月频，均线按20个月计"},
            "ECB_BS": {"value": ecb_val, "unit": "百万EUR", "date": ecb_date,
                       "wow_pct": ecb_wow, "wma20": ecb_wma, "signal": ecb_sig},
            "BOJ_BS": {"value": boj_val, "unit": "亿JPY", "date": boj_date,
                       "wow_pct": boj_wow, "wma20": boj_wma, "signal": boj_sig},
            "Global_Liq": {"value": gl, "unit": "百万USD", "date": net[-1][0] if net else None,
                           "note": "=US_Net_Liq+ECB_BS_USD+BOJ_BS_USD+GlobalM2_USD(ECB+BOJ折算,缺US/PBoC)；PBoC_credit 缺失未计入"},
        },
        "fx": {"EURUSD": eur, "JPYUSD": jpy, "CNYUSD": cny},
        "stage": stage,
        "alerts": alerts,
        "rules": {
            "stage": "动荡: MOVE>100 或 SOFR_IORB>15bp；投机: MOVE>80 且 US_Net_Liq 环比>0；反弹: US_Net_Liq 环比>0；其余平静",
            "alert_red": "MOVE>100 或 SOFR_IORB>15bp -> 抵押品链 预警",
            "alert_yellow": "US_Net_Liquidity 连续3周下降 -> 流动性流量拐点",
            "signal": "环比>0 绿，<0 黄；MOVE>100 红；ACM 环比>+0.15pp 红",
        },
        "gaps": ["PBoC 对其他存款性公司债权/黄金储备无免费通道，Global_Liq 未含 PBoC 项",
                 "GlobalM2 仅含 ECB+BOJ（缺美国 M2 与 PBoC M2）"],
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()