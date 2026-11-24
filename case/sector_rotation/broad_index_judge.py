#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基指数判断模块 —— 五指数（沪深300/中证500/中证1000/创业板指/科创50）
涨跌判断 + 风险/机会判断 + 横截面轮动判断（指数间强弱迁移）。

- 数据源：akshare stock_zh_index_daily（新浪接口，已验证可更新至当日收盘）。
- 算法与主日报「开源时序轮动」同源：20/60 日动量 + 14 日趋势强度（上涨日占比）；
  叠加均线排列、RSI、波动率状态，形成可解释的信号，不做黑箱模型。
- 输出：report_run/broad_index_judge.json，含每个指数的方向/置信/机会风险标签、
  横截面轮动排序与排名迁移（"从该指数轮到另一个指数"）。

失败即 raise（由主流程统一推送飞书失败备注并退出非0）。
"""
import json
import os
import sys
import time

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
REPORT = os.path.join(BASE, "report_run")
_pd = os.path.join(BASE, "ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2", "py_deps")
if _pd not in sys.path and os.path.isdir(_pd):
    sys.path.insert(0, _pd)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# 五宽基指数（akshare 新浪 symbol -> 展示名 / 风格标签）
IDX = [
    ("sh000300", "沪深300", "大盘核心资产"),
    ("sh000905", "中证500", "中盘"),
    ("sh000852", "中证1000", "小盘"),
    ("sz399006", "创业板指", "成长"),
    ("sh000688", "科创50", "科创成长"),
]
OUT = os.path.join(REPORT, "broad_index_judge.json")
_CACHE = os.path.join(REPORT, "broad_index_cache.json")


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}]   宽基 {msg}", flush=True)


def load_cache(asof):
    """当日已拉取则复用，避免重复请求新浪接口。"""
    if os.path.exists(_CACHE):
        try:
            d = json.load(open(_CACHE, encoding="utf-8"))
            if d.get("asof") == asof and len(d.get("series", {})) == len(IDX):
                return d["series"]
        except Exception:  # noqa: BLE001
            pass
    return None


def save_cache(asof, series):
    with open(_CACHE, "w", encoding="utf-8") as f:
        json.dump({"asof": asof, "series": series}, f, ensure_ascii=False)


def fetch_series(symbol, retry=1):
    """拉取单指数日线（新浪），返回 DataFrame(date index, close)。"""
    import akshare as ak
    last = None
    for i in range(retry + 1):
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            if df is None or not len(df):
                raise RuntimeError("空数据")
            s = df.set_index("date")["close"].astype(float).sort_index()
            return s
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:120]
            if i < retry:
                time.sleep(2)
    raise RuntimeError(f"新浪宽基指数 {symbol} 拉取失败: {last}")


def rsi14(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def judge_one(close, name, style):
    """单指数算法判断：方向（涨/跌/震荡）+ 置信 + 机会/风险标签 + 关键指标。"""
    ret = close.pct_change()
    m5 = close / close.shift(5) - 1
    m20 = close / close.shift(20) - 1
    m60 = close / close.shift(60) - 1
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    rsi = rsi14(close)
    vol21 = ret.rolling(21).std() * np.sqrt(252)
    strength = ret.clip(lower=0).abs().rolling(14).sum() / ret.abs().rolling(14).sum()

    last = close.iloc[-1]
    s_m5 = 1 if m5.iloc[-1] > 0 else -1
    s_m20 = 1 if m20.iloc[-1] > 0 else -1
    s_m60 = 1 if m60.iloc[-1] > 0 else -1
    s_ma20 = 1 if last > ma20.iloc[-1] else -1
    s_ma60 = 1 if last > ma60.iloc[-1] else -1
    score = s_m5 + s_m20 + s_m60 + s_ma20 + s_ma60  # [-5, 5]

    if score >= 3:
        direction = "涨"
        conf = "强" if score >= 4 else "中"
    elif score <= -3:
        direction = "跌"
        conf = "强" if score <= -4 else "中"
    else:
        direction = "震荡"
        conf = "弱"

    # 波动率状态：近一年波动分位（风险放大信号）
    vq = (vol21.rank(pct=True)).iloc[-1]
    rsi_now = rsi.iloc[-1]
    # 风险/机会标签：方向得分 + 波动 + 超买超卖修正
    if score >= 2:
        label = "机会"
        if vq > 0.8:
            label = "机会·波动放大"
        if rsi_now >= 75:
            label = "机会·超买警示"
    elif score <= -2:
        label = "风险"
        if vq > 0.8:
            label = "风险·波动放大"
        if rsi_now <= 25:
            label = "风险·超卖"
    else:
        label = "中性"

    return {
        "code": name, "style": style, "close": round(float(last), 2),
        "m5": round(float(m5.iloc[-1]) * 100, 2),
        "m20": round(float(m20.iloc[-1]) * 100, 2),
        "m60": round(float(m60.iloc[-1]) * 100, 2),
        "ma20_pos": round(float(((last / ma20.iloc[-1]) - 1) * 100), 2),
        "rsi": round(float(rsi_now), 1),
        "vol21_ann": round(float(vol21.iloc[-1]) * 100, 1),
        "vol_q": round(float(vq), 2),
        "strength14": round(float(strength.iloc[-1]), 3),
        "score": int(score),
        "direction": direction,
        "conf": conf,
        "label": label,
        "_series": close,
    }


def zscore_s(s):
    return (s - s.mean()) / s.std()


def run_broad_index_judge(asof):
    """主入口：五指数判断 + 横截面轮动。返回 dict（可写 JSON）。"""
    series = load_cache(asof)
    if series is None:
        series = {}
        for sym, name, style in IDX:
            s = fetch_series(sym)
            series[name] = {"symbol": sym, "close": s}
            _log(f"{name} {sym}: {len(s)} 行 -> 最新 {s.index[-1]}")
        save_cache(asof, {k: {"symbol": v["symbol"],
                              "close": [str(x) for x in v["close"].index],  # noqa: E501
                              "vals": [float(x) for x in v["close"].values]}
                          for k, v in series.items()})

    # 重建 pandas Series（读缓存时转换）
    rebuilt = {}
    for name, d in series.items():
        if "close" in d and hasattr(d["close"], "iloc"):  # 内存路径
            rebuilt[name] = d["close"]
        else:
            rebuilt[name] = pd.Series(d["vals"], index=pd.to_datetime(d["close"])).sort_index()
    series = rebuilt

    judges = []
    for sym, name, style in IDX:
        close = series[name]
        judges.append(judge_one(close, name, style))

    # 横截面轮动：20/60 日动量 + 14 日趋势强度 截面 z-score 合成强度
    rows = []
    for jd in judges:
        close = jd.pop("_series")
        ret = close.pct_change()
        m20 = round(float((close / close.shift(20) - 1).iloc[-1]), 6)
        m60 = round(float((close / close.shift(60) - 1).iloc[-1]), 6)
        strength = round(float((ret.clip(lower=0).abs().rolling(14).sum()
                                / ret.abs().rolling(14).sum()).iloc[-1]), 6)
        rows.append({"name": jd["code"], "m20": m20, "m60": m60, "strength": strength})
    rf = pd.DataFrame(rows)
    # 当前截面强度
    cur = zscore_s(rf["m20"]) + zscore_s(rf["m60"]) + zscore_s(rf["strength"])
    rf["cur"] = cur
    rf["rank"] = cur.rank(ascending=False, method="min").astype(int)
    # 上期强度（前 1 交易日）：用各指数前一日 m20/m60/strength 重建截面
    prev_rows = []
    for jd, (sym, name, style) in zip(judges, IDX):
        close = series[name]
        ret = close.pct_change()
        p_m20 = round(float((close.shift(1) / close.shift(21) - 1).iloc[-1]), 6)
        p_m60 = round(float((close.shift(1) / close.shift(61) - 1).iloc[-1]), 6)
        p_strength = round(float((ret.shift(1).clip(lower=0).abs().rolling(14).sum()
                                  / ret.shift(1).abs().rolling(14).sum()).iloc[-1]), 6)
        prev_rows.append({"name": name, "m20": p_m20, "m60": p_m60, "strength": p_strength})
    pf = pd.DataFrame(prev_rows)
    pf["prev"] = zscore_s(pf["m20"]) + zscore_s(pf["m60"]) + zscore_s(pf["strength"])
    pf["rank_prev"] = pf["prev"].rank(ascending=False, method="min").astype(int)

    rf = rf.set_index("name").join(pf.set_index("name")[["rank_prev"]]).reset_index()
    rf["rank_chg"] = rf["rank_prev"] - rf["rank"]  # 负=排名上升(相对走强)
    for _, r in rf.iterrows():
        for jd in judges:
            if jd["code"] == r["name"]:
                jd["rank"] = int(r["rank"])
                jd["rank_chg"] = int(r["rank_chg"])
                jd["rel_str"] = round(float(r["cur"]), 3)
                break

    # 轮动方向：当前最强2 vs 最弱2 的风格标签
    strong = rf.sort_values("rank").head(2)
    weak = rf.sort_values("rank", ascending=False).head(2)
    s_names = [next(x["code"] for x in judges if x["code"] == n) for n in strong["name"]]
    w_names = [next(x["code"] for x in judges if x["code"] == n) for n in weak["name"]]
    up_names = [next(x["code"] for x in judges if x["code"] == n)
                for n in rf[rf["rank_chg"] < 0].sort_values("rank_chg")["name"]]
    dn_names = [next(x["code"] for x in judges if x["code"] == n)
                for n in rf[rf["rank_chg"] > 0].sort_values("rank_chg", ascending=False)["name"]]
    rotation_desc = (f"资金强度流向：**{s_names[0]} > {s_names[1]}**，"
                     f"弱势端 **{w_names[0]} / {w_names[1]}**；"
                     f"排名上升（流入）：{'、'.join(up_names) if up_names else '无'}；"
                     f"排名下降（流出）：{'、'.join(dn_names) if dn_names else '无'}。"
                     f"风格上由「{weak.iloc[0]['name']}」向「{strong.iloc[0]['name']}」轮动的信号"
                     f"{'明确' if int(rf['rank_chg'].abs().max()) >= 2 else '尚未明确'}。")

    out = {
        "asof": asof,
        "indices": [{k: v for k, v in jd.items() if k != "_series"} for jd in judges],
        "rotation": {
            "strong": s_names, "weak": w_names,
            "up": up_names, "down": dn_names,
            "desc": rotation_desc,
        },
        "method": "新浪日线；20/60日动量+14日趋势强度(上涨日占比)截面z-score轮动；"
                  "方向=5信号投票(动量5/20/60+价格vs MA20/60)；风险机会=方向得分+波动分位+RSI修正",
        "disclaimer": "算法判断仅供参考，不构成投资建议。",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_opp = sum(1 for x in out["indices"] if x["label"].startswith("机会"))
    n_risk = sum(1 for x in out["indices"] if x["label"].startswith("风险"))
    _log(f"判断完成: 机会 {n_opp} | 风险 {n_risk} | 轮动 {s_names[0]} -> 强 -> {s_names[1]}")
    return out


if __name__ == "__main__":
    asof = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y%m%d")
    res = run_broad_index_judge(asof)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])