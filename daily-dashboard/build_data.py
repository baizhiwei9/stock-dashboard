#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把历史报告(md)与报告引用的数据库 解析成结构化时间序列 -> data.json
数据来源（全部来自已推送的历史报告 / 报告引用的库，不自行采集行情）：
  1) 风险预测日报 md      -> 宽基指数结构/量能风险分、CFC分位、up50
  2) 环流日报 md          -> 美国流动性：TGA / 准备金 / ON RRP / 中美利差 / JGB / EFFR
  3) style_drift.db       -> 期权：ATM IV / IV Skew / CBOE SKEW / 标的VIX / PCR
"""
import os, re, json, glob, sqlite3
from datetime import datetime

# 数据源根目录（各任务产出的 md/报告都在这下面）。可用 DASH_BASE 覆盖，便于换机部署。
import os
BASE = os.environ.get("DASH_BASE", "/Coze/Drive/蓁宝/所有对话/主对话")
RISK_DIR = os.path.join(BASE, "市场情绪监控/outputs")
MACRO_DIR = os.path.join(BASE, "环流日报/outputs")
OPT_DB = os.path.join(BASE, "市场情绪监控/data/options_cache/style_drift.db")
OUT = os.environ.get("DASH_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json"))

IDX_NAMES = ["沪深300", "中证500", "中证1000", "创业板指", "科创50", "万得全A"]
CIRCLED = "[①②③④⑤⑥⑦⑧⑨⑩]"


def num(s):
    """从字符串里抠第一个数字（支持负号、千分位）"""
    if s is None:
        return None
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None


def _rows_from(seg_text, max_lines=30):
    rows, started = [], False
    for ln in seg_text.split("\n")[:max_lines]:
        ln = ln.strip()
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                started = True
                continue
            rows.append(cells)
            started = True
        elif started:
            break
    return rows


def table_after(text, anchor, max_lines=30):
    """取 anchor 之后的第一张 markdown 表格，返回 [(cells...), ...]（含表头）"""
    i = text.find(anchor)
    if i < 0:
        return []
    return _rows_from(text[i + len(anchor):], max_lines)


def table_after_re(text, pattern, max_lines=30):
    """按正则定位小节标题（编号会漂移），取其后的第一张 markdown 表"""
    m = re.search(pattern, text)
    if not m:
        return []
    return _rows_from(text[m.end():], max_lines)


def _detail(t, name_kw, max_lines=18):
    """解析某张风险明细表 -> {指数: {列名: 单元格文本}}"""
    rows = table_after_re(t, r"###\s*" + CIRCLED + r"\s*" + name_kw, max_lines)
    out = {}
    if not rows:
        return out
    head = rows[0]
    col = {h: i for i, h in enumerate(head)}
    for r in rows[1:]:
        if not r or r[0] not in IDX_NAMES:
            continue
        out[r[0]] = {h: (r[i] if i < len(r) else None) for h, i in col.items()}
    return out


def _pick(tbl, name, keys):
    """从明细/总览表里按列名优先级取值，跳过 N/A 与占位符"""
    row = tbl.get(name) or {}
    for k in keys:
        v = row.get(k)
        if v and "N/A" not in v and v.strip() not in ("-", "—", ""):
            return v
    return None


# ---------------- 1. 宽基指数（风险预测日报） ----------------
def parse_risk_reports():
    files = sorted(glob.glob(os.path.join(RISK_DIR, "2026-*_风险预测日报.md")))
    dates = []
    idx = {n: {"struct": [], "volume": [], "extreme": [], "rating": [], "cfc_pct": [], "up50": []} for n in IDX_NAMES}
    wind_score = []  # 万得全A 综合得分
    for fp in files:
        d = os.path.basename(fp)[:10]
        try:
            t = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        dates.append(d)

        # --- 各指数风险对比（总览：综合评级 + 兜底分数） ---
        ov = {}
        rows = table_after(t, "各指数风险对比")
        if rows:
            head = rows[0]
            col = {h: i for i, h in enumerate(head)}
            for r in rows[1:]:
                if not r or r[0] not in IDX_NAMES:
                    continue
                ov[r[0]] = {h: (r[i] if i < len(r) else None) for h, i in col.items()}

        # --- 三张明细表（标题编号会漂移，用正则锚定） ---
        ds = _detail(t, "结构风险")
        dv = _detail(t, "量能情绪风险")
        de = _detail(t, "极端风险")

        for name in IDX_NAMES:
            s = _pick(ds, name, ["综合"]) or _pick(ov, name, ["结构"])
            v = _pick(dv, name, ["综合"]) or _pick(ov, name, ["量能情绪"])
            e = _pick(de, name, ["综合"]) or _pick(ov, name, ["极端"])
            idx[name]["struct"].append(num(s))
            idx[name]["volume"].append(num(v))
            idx[name]["extreme"].append(num(e))
            idx[name]["rating"].append((ov.get(name) or {}).get("综合评级"))

            cfc = _pick(ds, name, ["CFC"]) or ""
            m = re.search(r"(\d+)%分位", cfc)
            idx[name]["cfc_pct"].append(int(m.group(1)) if m else None)
            up = _pick(ds, name, ["up50"]) or ""
            idx[name]["up50"].append(re.sub(r"^[^\u4e00-\u9fa5A-Za-z]+", "", up) or None)

        # --- 万得全A 综合得分 ---
        m = re.search(r"综合得分[:：]\s*([-+]?\d+\.?\d*)", t)
        wind_score.append(float(m.group(1)) if m else None)

    return {"dates": dates, "indices": idx, "wind_score": wind_score}


# ---------------- 2. 宏观（环流日报） ----------------
def parse_macro_reports():
    files = sorted(glob.glob(os.path.join(MACRO_DIR, "2026-*_环流日报.md")))
    dates = []
    S = {"tga": [], "reserve": [], "onrrp": [], "cn_us": [], "jgb10": [], "effr": [], "rrr": []}
    for fp in files:
        d = os.path.basename(fp)[:10]
        try:
            t = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        dates.append(d)

        # TGA（两种格式）
        tga = None
        m = re.search(r"\|\s*TGA 收盘余额\s*\|\s*([\d,]+)", t)
        if m:
            tga = float(m.group(1).replace(",", ""))
        else:
            m = re.search(r"\|\s*TGA\s*\|\s*([\d,]+)M", t)
            if m:
                tga = float(m.group(1).replace(",", ""))
        S["tga"].append(tga)

        # 准备金余额 (M)
        m = re.search(r"准备金余额\s*\|\s*([\d,]+)M", t)
        S["reserve"].append(float(m.group(1).replace(",", "")) if m else None)

        # ON RRP (M)
        m = re.search(r"ON RRP[^|]*\|\s*([\d,]+)(M|B)\s*\|", t)
        if m:
            v = float(m.group(1).replace(",", ""))
            S["onrrp"].append(v * 1000 if m.group(2) == "B" else v)
        else:
            S["onrrp"].append(None)

        # 中美利差（倒挂XXXBP / +XXXbp）
        m = re.search(r"中美利差[^\d\n]{0,6}([-+]?\d+(?:\.\d+)?)\s*(?:BP|bp)", t)
        if not m:
            m = re.search(r"中美利差倒挂\s*(\d+(?:\.\d+)?)\s*(?:BP|bp)", t)
        S["cn_us"].append(float(m.group(1)) if m else None)

        # JGB 10Y
        m = re.search(r"JGB 10Y[^\d]{0,4}(\d+\.\d+)%", t)
        S["jgb10"].append(float(m.group(1)) if m else None)

        # EFFR
        m = re.search(r"EFFR 联邦基金有效利率\s*\|\s*(\d+\.\d+)%", t)
        if not m:
            m = re.search(r"EFFR\s*(\d+\.\d+)%", t)
        if m:
            S["effr"].append(float(m.group(1)))
            S.setdefault("effr_dev", []).append(None)
        else:
            m2 = re.search(r"EFFR 高于央行利率下沿\s*([-+]\d+\.?\d*)bp", t)
            S["effr"].append(None)
            S.setdefault("effr_dev", []).append(float(m2.group(1)) if m2 else None)

        # rrr 掉期点
        m = re.search(r"rrr\s*(\d+\.\d+)%", t)
        S["rrr"].append(float(m.group(1)) if m else None)

    # 长度对齐兜底：所有序列必须与 dates 等长，否则图表会整体错位
    for k, arr in S.items():
        if len(arr) < len(dates):
            S[k] = [None] * (len(dates) - len(arr)) + arr
        elif len(arr) > len(dates):
            S[k] = arr[-len(dates):]
    return {"dates": dates, "series": S}


# ---------------- 3. 期权（style_drift.db） ----------------
OPT_NAMES = {"510300": "沪深300ETF", "510500": "中证500ETF", "159915": "创业板ETF", "588000": "科创50ETF"}


def parse_option_db():
    c = sqlite3.connect("file:%s?mode=ro" % OPT_DB, uri=True)
    rows = c.execute(
        "select trade_date,underlying,spot_price,atm_iv,iv_skew,cboe_skew,vol_pcr,oi_pcr,vix "
        "from daily_metrics order by trade_date"
    ).fetchall()
    c.close()
    dates = sorted({r[0] for r in rows})
    pos = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    data = {}
    for code, name in OPT_NAMES.items():
        data[code] = {
            "name": name,
            "spot": [None] * n, "atm_iv": [None] * n, "iv_skew": [None] * n,
            "cboe_skew": [None] * n, "vol_pcr": [None] * n, "oi_pcr": [None] * n,
            "vix": [None] * n,
        }
    for d, u, spot, atm, skew, cskew, vpcr, opcr, vix in rows:
        if u not in data:
            continue
        i = pos[d]
        o = data[u]
        o["spot"][i] = spot
        o["atm_iv"][i] = round(atm * 100, 2) if atm is not None else None
        o["iv_skew"][i] = round(skew * 100, 2) if skew is not None else None
        o["cboe_skew"][i] = cskew
        o["vol_pcr"][i] = vpcr
        o["oi_pcr"][i] = opcr
        o["vix"][i] = vix
    return {"dates": dates, "underlyings": data}


def pct_rank(series, window=None):
    """当前值在历史序列中的分位（%），window 限定回看长度"""
    vals = [v for v in series if v is not None]
    if len(vals) < 20:
        return None
    cur = series[-1] if series[-1] is not None else vals[-1]
    if window:
        vals = vals[-window:]
    less = sum(1 for v in vals if v <= cur)
    return round(less / len(vals) * 100, 1)


def main():
    risk = parse_risk_reports()
    macro = parse_macro_reports()
    opt = parse_option_db()

    # 期权最新值与分位
    opt_latest = {}
    for code, o in opt["underlyings"].items():
        opt_latest[code] = {
            "name": o["name"],
            "date": opt["dates"][-1],
            "spot": o["spot"][-1],
            "atm_iv": o["atm_iv"][-1],
            "iv_skew": o["iv_skew"][-1],
            "cboe_skew": o["cboe_skew"][-1],
            "vix": o["vix"][-1],
            "vol_pcr": o["vol_pcr"][-1],
            "pct_iv_252": pct_rank(o["atm_iv"], 252),
            "pct_skew_252": pct_rank(o["iv_skew"], 252),
            "pct_cboe_252": pct_rank(o["cboe_skew"], 252),
            "pct_vix_252": pct_rank(o["vix"], 252),
        }

    # 指数最新值
    idx_latest = {}
    for n in IDX_NAMES:
        o = risk["indices"][n]
        idx_latest[n] = {
            "date": risk["dates"][-1],
            "struct": o["struct"][-1] if o["struct"] else None,
            "volume": o["volume"][-1] if o["volume"] else None,
            "extreme": o["extreme"][-1] if o["extreme"] else None,
            "rating": o["rating"][-1] if o["rating"] else None,
            "cfc_pct": o["cfc_pct"][-1] if o["cfc_pct"] else None,
            "up50": o["up50"][-1] if o["up50"] else None,
        }

    macro_latest = {"date": macro["dates"][-1]}
    for k, v in macro["series"].items():
        macro_latest[k] = v[-1] if v else None

    out = {
        "meta": {
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "risk_range": [risk["dates"][0], risk["dates"][-1], len(risk["dates"])],
            "macro_range": [macro["dates"][0], macro["dates"][-1], len(macro["dates"])],
            "opt_range": [opt["dates"][0], opt["dates"][-1], len(opt["dates"])],
            "sources": [
                "风险预测日报（市场情绪监控/outputs，%d份）" % len(risk["dates"]),
                "环流日报（环流日报/outputs，%d份）" % len(macro["dates"]),
                "期权日频库 style_drift.db daily_metrics（%d交易日）" % len(opt["dates"]),
            ],
        },
        "index": {"dates": risk["dates"], "indices": risk["indices"],
                  "wind_score": risk["wind_score"], "latest": idx_latest},
        "option": {"dates": opt["dates"], "underlyings": opt["underlyings"], "latest": opt_latest},
        "macro": {"dates": macro["dates"], "series": macro["series"], "latest": macro_latest},
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("written", OUT, os.path.getsize(OUT), "bytes")
    print("risk dates", risk["dates"][0], "~", risk["dates"][-1], len(risk["dates"]))
    print("macro dates", macro["dates"][0], "~", macro["dates"][-1], len(macro["dates"]))
    print("opt dates", opt["dates"][0], "~", opt["dates"][-1], len(opt["dates"]))
    print("\n--- 指数最新 ---")
    for k, v in idx_latest.items():
        print(k, v)
    print("\n--- 期权最新 ---")
    for k, v in opt_latest.items():
        print(k, v)
    print("\n--- 宏观最新 ---", macro_latest)
    print("\n--- 宏观序列样本(近5) ---")
    for k, v in macro["series"].items():
        print(k, v[-5:])


if __name__ == "__main__":
    main()
