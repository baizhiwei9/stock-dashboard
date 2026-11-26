# -*- coding: utf-8 -*-
"""因子拥挤度判断（红绿灯）——横截面多空对冲
============================================
对横截面对冲实际使用的每一个因子，逐个给出「是否拥挤 / 是否仍有效」的可复现结论。

判断口径（全部只用工程内既有数据，向前看，不引入新数据源）：
1) 因子多空日收益 r_t：板块内按该因子横截面 z 排序，取前 1/3 为多头腿、后 1/3 为空头腿，
   等权多空收益（因子用 T 日值，收益用 T+1 日 ret_hold，避免同期前视）；两板块等权平均。
2) 三个拥挤度指标（各自取自身过去 LOOKBACK=756 个交易日的历史分位，分位越高越拥挤）：
   - 透支度   : 多空组合过去 252 日累计收益分位
   - 趋同度   : 全体品种日收益 60 日均值两两相关性的分位（资金做同一件事时齐涨齐跌）
   - 波动比   : 多空组合 20 日波动 / 250 日波动 的分位
3) 有效性指标：因子日度横截面 Spearman IC 的 60 日均值，取历史分位（分位越低说明因子越失效）。
4) 拥挤度得分 = 三个拥挤指标分位的等权平均；有效性得分 = IC 分位。
5) 红绿灯：
   - 红灯：拥挤度 ≥ RED(0.90) 或 有效性 ≤ EFF_RED(0.10)
   - 黄灯：拥挤度 ≥ YELLOW(0.75) 或 有效性 ≤ EFF_YELLOW(0.25)
   - 绿灯：其余
6) 是否允许开新仓：绿灯数 ≥ ALLOW_MIN(2) 且无红灯 → 允许；否则判为「拥挤/失效期，不建议开新仓」。

阈值均为模块常量，可按用户口径调整；本模块不改动原有交易逻辑。
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOTT = Path(__file__).resolve().parent.parent
if str(_ROOTT) not in sys.path:
    sys.path.insert(0, str(_ROOTT))

from sim_trade.config import FACTORS_FILE, SECTOR_OF, STATE_DIR  # noqa: E402

# 横截面对冲实际使用的因子（与 factors_build_2022 的 score 权重一致）
FACTORS = {"z_inv": "库存", "z_roll": "展期收益", "z_basis": "基差", "z_droll": "结构变化"}
PENALTY = {"momz_20": "动量惩罚项"}          # score_adj 实际由该项主导，一并监控
WIN_IC = 60
WIN_PERF = 252
LOOKBACK = 756                                # 分位窗口 ≈ 3 年
MINP = 250
RED, YELLOW = 0.90, 0.75
EFF_RED, EFF_YELLOW = 0.10, 0.25
ALLOW_MIN = 2
LIGHT = {1: "绿", 0: "黄", -1: "红"}


def load_factors():
    f = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    f = f[f["variety"].isin(set(SECTOR_OF))].copy()
    f = f.sort_values(["variety", "date"]).reset_index(drop=True)
    f["ret_next"] = f.groupby("variety")["ret_hold"].shift(-1)
    return f


def ls_series(f, col):
    """板块内前后 1/3 等权多空日收益（两板块等权）"""
    rows = []
    for (d, _sec), g in f.groupby(["date", "sector"]):
        g = g.dropna(subset=[col, "ret_next"])
        if len(g) < 6:
            continue
        k = max(2, len(g) // 3)
        g = g.sort_values(col)
        rows.append({"date": d, "r": g["ret_next"].tail(k).mean() - g["ret_next"].head(k).mean()})
    if not rows:
        return pd.Series(dtype=float)
    return pd.DataFrame(rows).groupby("date")["r"].mean().sort_index()


def ic_series(f, col):
    rows = []
    for d, g in f.groupby("date"):
        g = g.dropna(subset=[col, "ret_next"])
        if len(g) < 6:
            continue
        rows.append({"date": d, "ic": g[col].rank().corr(g["ret_next"].rank())})
    return pd.DataFrame(rows).set_index("date")["ic"].sort_index() if rows else pd.Series(dtype=float)


def co_movement(f, win=60):
    """趋同度：全体品种日收益的两两平均相关（滚动 win 日）"""
    wide = f.pivot_table(index="date", columns="variety", values="ret_hold").sort_index()
    arr = wide.values
    out = np.full(len(wide), np.nan)
    for i in range(win - 1, len(wide)):
        w = arr[i - win + 1:i + 1]
        ok = ~np.isnan(w).any(axis=0)
        if ok.sum() < 6:
            continue
        c = np.corrcoef(w[:, ok].T)
        out[i] = c[np.triu_indices(c.shape[0], 1)].mean()
    return pd.Series(out, index=wide.index)


def pct_rank(s, lookback=LOOKBACK, minp=MINP):
    v = s.reindex(sorted(s.index)).values
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        if np.isnan(v[i]):
            continue
        w = v[max(0, i - lookback + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) >= minp:
            out[i] = float((w <= v[i]).mean())
    return pd.Series(out, index=sorted(s.index))


def evaluate(asof=None, factors=None, save=True):
    """返回 {date, factors:{name:{...}}, red:[], yellow:[], green:[], allow, title_flag}"""
    if factors is None:
        f = load_factors()
    else:                                  # 传入的 factors 可能未含 ret_next，先补齐并对齐品种池
        f = factors.copy()
        f = f[f["variety"].isin(set(SECTOR_OF))].sort_values(["variety", "date"])
        if "ret_next" not in f.columns:
            f["ret_next"] = f.groupby("variety")["ret_hold"].shift(-1)
    if asof is None:
        asof = f["date"].max()
    asof = pd.Timestamp(asof)
    f = f[f["date"] <= asof]
    res = {"date": str(asof.date()), "factors": {}}
    for col, name in {**FACTORS, **PENALTY}.items():
        if col not in f.columns:
            continue
        ls = ls_series(f, col)
        ic = ic_series(f, col)
        if len(ls) < MINP:
            res["factors"][name] = {"code": col, "light": "黄", "note": "历史样本不足，暂按黄灯处理"}
            continue
        cum = (1 + ls).rolling(WIN_PERF, min_periods=120).apply(np.prod, raw=True) - 1
        volr = ls.rolling(20, min_periods=15).std() / ls.rolling(250, min_periods=120).std()
        cm = co_movement(f)
        p_cum = pct_rank(cum)
        p_cm = pct_rank(cm)
        p_vol = pct_rank(volr)
        ic60 = ic.rolling(WIN_IC, min_periods=30).mean()
        p_ic = pct_rank(ic60)
        idx = None
        for _s in (p_cum, p_cm, p_vol, p_ic):
            cand = _s.index[_s.index <= asof]
            if len(cand):
                idx = cand[-1] if idx is None else min(idx, cand[-1])
        if idx is None:
            res["factors"][name] = {"code": col, "light": "黄", "note": "无分位样本"}
            continue
        res["eval_date"] = str(idx.date())      # 多空收益需次日收益，评估基准日常为 T-1 交易日

        def g(s):
            v = s.get(idx, np.nan)
            return float("nan") if (v is None or pd.isna(v)) else float(v)
        _vals = [g(p_cum), g(p_cm), g(p_vol)]
        _vals = [v for v in _vals if not np.isnan(v)]
        if not _vals:                     # 三项拥挤分位都还没有样本（历史不足）
            res["factors"][name] = {"code": col, "light": "黄", "note": "拥挤度分位样本不足",
                                    "ic60": None if np.isnan(g(ic60)) else round(g(ic60), 4)}
            continue
        crowd = float(np.mean(_vals))
        eff = g(p_ic)
        eff = None if np.isnan(eff) else eff
        heavy = (crowd >= RED) or (eff is not None and eff <= EFF_RED)
        mid = (crowd >= YELLOW) or (eff is not None and eff <= EFF_YELLOW)
        light = "红" if heavy else ("黄" if mid else "绿")
        res["factors"][name] = {
            "code": col, "light": light, "crowd": round(crowd, 3),
            "eff_ic_pct": None if eff is None else round(eff, 3),
            "ic60": None if np.isnan(g(ic60)) else round(g(ic60), 4),
            "cum252": None if np.isnan(g(cum)) else round(g(cum), 4),
            "co_move": None if np.isnan(g(cm)) else round(g(cm), 3),
            "vol_ratio": None if np.isnan(g(volr)) else round(g(volr), 3),
            "pct_cum": None if np.isnan(g(p_cum)) else round(g(p_cum), 3),
            "pct_cm": None if np.isnan(g(p_cm)) else round(g(p_cm), 3),
            "pct_vol": None if np.isnan(g(p_vol)) else round(g(p_vol), 3),
        }
    lights = {k: v["light"] for k, v in res["factors"].items()}
    res["red"] = [k for k, v in lights.items() if v == "红"]
    res["yellow"] = [k for k, v in lights.items() if v == "黄"]
    res["green"] = [k for k, v in lights.items() if v == "绿"]
    res["allow"] = (len(res["green"]) >= ALLOW_MIN and not res["red"])
    res["title_flag"] = "拥挤" if res["red"] else ""
    if save:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"crowding_{asof.date()}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def format_lines(res):
    out = [f"【因子拥挤度红绿灯】报告期 {res['date']}｜评估基准日 {res.get('eval_date', res['date'])}"
           + (f"　⚠️{'／'.join(res['red'])} 拥挤" if res["red"] else "　无红灯")]
    for name, d in res["factors"].items():
        icon = {"绿": "🟢", "黄": "🟡", "红": "🔴"}[d["light"]]
        bits = []
        if d.get("crowd") is not None:
            bits.append(f"拥挤度 {d['crowd']:.2f}")
        if d.get("eff_ic_pct") is not None:
            bits.append(f"有效性分位 {d['eff_ic_pct']:.2f}")
        if d.get("ic60") is not None:
            bits.append(f"近60日IC {d['ic60']:+.3f}")
        out.append(f"{icon} {name}：{d['light']}灯　" + "，".join(bits)
                   + (f"（{d['note']}）" if d.get("note") else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    res = evaluate(a.date)
    for ln in format_lines(res):
        print(ln)
    print(f"绿灯 {len(res['green'])} 个 / 黄灯 {len(res['yellow'])} 个 / 红灯 {len(res['red'])} 个"
          f"｜是否允许开新仓：{'允许' if res['allow'] else '不建议（拥挤/失效期）'}"
          f"｜标题标记：{res['title_flag'] or '无'}")
    print("落盘:", STATE_DIR / f"crowding_{res['date']}.json")


if __name__ == "__main__":
    main()