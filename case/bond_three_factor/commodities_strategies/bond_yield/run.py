"""国债收益率四维预测 + 曲线监控 · 日更入口（DataHub 取数版）。

模块归属：``commodities_strategies.bond_yield``（独立债券模块，与商品横截面/
价差策略互不影响）。

与原脚本（``bond_yield_daily/run_daily.py``）的唯一实质差异是**取数口径**：
所有债券数据经 DataHub 取得（EDB ``RATE.CN.*`` + bond 子包中债全期限曲线），
DataHub 不可达时才回退本地快照并在卡片上标注；模型权重、四维框架、卡片版式、
自证逻辑与之前完全一致，保证日更输出不变。

用法::

    python -m commodities_strategies.bond_yield.run            # 推送飞书卡片
    python -m commodities_strategies.bond_yield.run --no-send   # 只生成卡片
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WS = os.path.dirname(os.path.dirname(_ROOT))
for _p in (_ROOT, os.path.join(_WS, "pylibs"), os.path.join(_WS, "py_deps"),
           os.path.join(_WS, ".venv_pkgs")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from . import data as Bdata
from .notify import (make_header, make_text_element, make_divider,
                     make_note, send_feishu_card)

BASE = os.path.dirname(os.path.abspath(__file__))
ASSET = str(Bdata.ASSET_DIR)
OUT = str(Bdata.OUT_DIR)
ROOT = _ROOT


# ===================== 数据加载与增量刷新 =====================
def load_base_data():
    """读取快照基线（DataHub 未覆盖的历史窗口 + 模型特征面板）。

    债券行情部分在 main() 里立刻被 DataHub 增量覆盖；快照在此只作基线。
    """
    return Bdata.load_snapshot()


def refresh_latest(cn, p87):
    """DataHub EDB 增量刷新 2/5/10/30 年国债收益率；失败回退快照。

    返回 ``(cn, updated, info)``：``info['channel']`` 以 ``datahub`` 开头表示确实
    经 DataHub 取到数（当日无新增行也算），``snapshot`` 才是真的回退快照——
    卡片来源标注必须按 channel 判断，不能用「有没有新增行」代替。
    """
    cn, updated, info = Bdata.refresh_yields(cn)
    if info.get("note"):
        print(f"[refresh] {info['note']}", flush=True)
    return cn, updated, info


# ===================== LSTM 前向 =====================
def load_lstm(name):
    z = np.load(os.path.join(ASSET, f"lstm_{name}.npz"))
    return {"W": z["W"], "U": z["U"], "b": z["b"], "Wy": z["Wy"], "by": z["by"]}


def lstm_forward(p, Xb):
    b, S, D_ = Xb.shape; H_ = p["Wy"].shape[1]
    hs = np.zeros((b, S + 1, H_)); cs = np.zeros((b, S + 1, H_))
    for t in range(S):
        xt = Xb[:, t, :]
        g = np.stack([xt @ p["W"][k].T + hs[:, t, :] @ p["U"][k].T + p["b"][k] for k in range(4)])
        gi, gf, go, gc = g[0], g[1], g[2], g[3]
        i = 1 / (1 + np.exp(-gi)); f = 1 / (1 + np.exp(-gf)); o = 1 / (1 + np.exp(-go)); cc = np.tanh(gc)
        c = f * cs[:, t, :] + i * cc; h = o * np.tanh(c)
        cs[:, t + 1] = c; hs[:, t + 1] = h
    logit = hs[:, S, :] @ p["Wy"].T + p["by"]
    return 1 / (1 + np.exp(-np.clip(logit, -30, 30))).ravel()


# ===================== NSS 拟合 + AR(1) =====================
NSS_MATS = np.array([0.25, 0.5, 1, 3, 5, 7, 10, 30])
LAM1, LAM2 = 1.0, 4.0


def nss_design(m, t1=LAM1, t2=LAM2):
    m = np.asarray(m, float)
    x1 = (1 - np.exp(-m / t1)) / (m / t1)
    return np.column_stack([np.ones_like(m), x1, x1 - np.exp(-m / t1),
                            (1 - np.exp(-m / t2)) / (m / t2) - np.exp(-m / t2)])


def nss_ar1_predict(cb, win=500):
    """中债国债曲线(8期限,bp) → β0-β3(OLS) → 各列AR(1)滚动预测明日 → 反推10Y/30Y dR(bp)"""
    cols = ["3月", "6月", "1年", "3年", "5年", "7年", "10年", "30年"]
    g = cb[cols].astype(float).ffill() * 100
    g = g[g.notna().all(axis=1)]
    Xd = nss_design(NSS_MATS)
    B = g.values @ np.linalg.pinv(Xd).T
    X10 = nss_design([10.0])[0]; X30 = nss_design([30.0])[0]
    out10 = np.full(len(g), np.nan); out30 = np.full(len(g), np.nan)
    R10t = g["10年"].values; R30t = g["30年"].values
    for t in range(win, len(g)):
        wB = B[t - win:t]
        pred = np.zeros(4)
        for j in range(4):
            b = wB[:, j]; x = b[:-1]; y = b[1:]
            xm = x.mean(); ym = y.mean(); den = np.sum((x - xm) ** 2)
            if den <= 1e-9:
                pred[j] = y.mean(); continue
            phi = np.sum((x - xm) * (y - ym)) / den
            phi = max(-0.999, min(0.999, phi))
            c = ym - phi * xm
            pred[j] = c + phi * b[-1]
        out10[t] = pred @ X10 - R10t[t]
        out30[t] = pred @ X30 - R30t[t]
    return pd.Series(out10, index=g.index), pd.Series(out30, index=g.index)


# ===================== 技术指标动量（最新一天） =====================
def tech_indicators(y: pd.Series) -> pd.DataFrame:
    d = pd.DataFrame(index=y.index)
    ma5, ma6, ma10, ma12, ma20, ma24, ma26, ma60, ma120 = [y.rolling(w, min_periods=w).mean()
                                                           for w in [5, 6, 10, 12, 20, 24, 26, 60, 120]]
    std20 = y.rolling(20).std(); std60 = y.rolling(60).std()
    d["MA5_dev"] = y - ma5; d["MA10_dev"] = y - ma10; d["MA20_dev"] = y - ma20
    d["MA60_dev"] = y - ma60; d["MA120_dev"] = y - ma120
    d["EMA12_dev"] = y - y.ewm(span=12, adjust=False).mean(); d["EMA26_dev"] = y - y.ewm(span=26, adjust=False).mean()
    ema12 = y.ewm(span=12, adjust=False).mean(); ema26 = y.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26; dea = dif.ewm(span=9, adjust=False).mean()
    d["DIF"] = dif; d["DEA"] = dea; d["MACD_HIST"] = (dif - dea) * 2
    chg = y.diff()
    for n, pre in [(6, "RSI6"), (12, "RSI12"), (24, "RSI24")]:
        up = chg.clip(lower=0).rolling(n).mean(); dn = (-chg.clip(upper=0)).rolling(n).mean()
        d[pre] = 100 - 100 / (1 + up / (dn + 1e-12))
    low9 = y.rolling(9).min(); high9 = y.rolling(9).max()
    rsv = (y - low9) / (high9 - low9 + 1e-12) * 100
    k = rsv.ewm(com=2, adjust=False).mean(); dd = k.ewm(com=2, adjust=False).mean(); j = 3 * k - 2 * dd
    d["K"] = k; d["D"] = dd; d["J"] = j
    for n, pre in [(14, "CCI14"), (20, "CCI20")]:
        tp = y.rolling(n).mean(); md = (y - tp).abs().rolling(n).mean()
        d[pre] = (y - tp) / (0.015 * md + 1e-12)
    mid = ma20; upb = mid + 2 * std20; lob = mid - 2 * std20
    d["BOLL_UP_dev"] = y - upb; d["BOLL_LOW_dev"] = y - lob
    d["BOLL_width"] = (upb - lob) / mid; d["PctB"] = (y - lob) / (upb - lob + 1e-12)
    d["ZScore20"] = (y - mid) / std20; d["ZScore60"] = (y - ma60) / std60
    d["Bias6"] = (y - ma6) / ma6 * 100; d["Bias12"] = (y - ma12) / ma12 * 100; d["Bias24"] = (y - ma24) / ma24 * 100
    d["ROC5"] = y / y.shift(5) - 1; d["ROC10"] = y / y.shift(10) - 1; d["ROC20"] = y / y.shift(20) - 1
    hh14 = y.rolling(14).max(); ll14 = y.rolling(14).min()
    d["WR14"] = (hh14 - y) / (hh14 - ll14 + 1e-12) * 100
    d["ATR14"] = chg.abs().rolling(14).mean(); d["ATR20"] = chg.abs().rolling(20).mean()
    d["Mom10"] = y - y.shift(10)
    d["Slope20"] = y.rolling(20).apply(lambda a: np.polyfit(np.arange(20), a, 1)[0], raw=True)
    d["Slope60"] = y.rolling(60).apply(lambda a: np.polyfit(np.arange(60), a, 1)[0], raw=True)
    return d


def tech_signal_latest(y: pd.Series, win=250, topk=10):
    """只计算最新一天的 s_tech（滚动250日选top10 → Z加权，口径与训练一致）"""
    ind = tech_indicators(y)
    names = list(ind.columns)
    arr = ind.values.astype(float)
    dy = y.shift(-1) - y
    dya = dy.values.astype(float)
    t = len(y) - 1
    w0 = t - win
    X = arr[w0:t + 1]; Yv = dya[w0:t + 1]
    valid = ~(np.isnan(X).any(axis=1) | np.isnan(Yv))
    if valid.sum() < 40:
        return 0.0, {}
    Xv = X[valid]; Yvv = Yv[valid]
    rkY = rankdata(Yvv)
    corrs = np.array([np.corrcoef(rankdata(Xv[:, j]), rkY)[0, 1] for j in range(len(names))])
    idx = np.argsort(-np.abs(corrs))[:topk]
    zsum = 0.0; wsum = 0.0; info = {}
    for j in idx:
        wj = corrs[j]; mu = Xv[:, j].mean(); sd = Xv[:, j].std()
        if sd <= 1e-12:
            continue
        z = (arr[t, j] - mu) / sd
        zsum += wj * z; wsum += abs(wj); info[names[j]] = round(float(corrs[j]), 3)
    return (zsum / (wsum + 1e-12)) if wsum > 0 else 0.0, info


# ===================== 宏观极端信号（最新一天） =====================
def extreme_signal_latest(p87, rate, win=500, top_pct=0.20, n_factors=20):
    """只计算最新一天 s_extreme（滚动win日前20%极端日 Spearman → 前20因子 → 全历史百分位加权）"""
    Fv = p87.values.astype(np.float64)
    cols = p87.columns
    dyv = (rate.shift(-1) - rate).values.astype(np.float64)
    n = len(p87)
    t = n - 1
    w0 = t - win
    wdy = dyv[w0:t]
    okw = ~np.isnan(wdy)
    abs_dy = np.abs(wdy[okw])
    if len(abs_dy) < 50:
        return 0.0, []
    thr = np.quantile(abs_dy, 1 - top_pct)
    ext = np.where(okw)[0][np.where(abs_dy >= thr)[0]]
    if len(ext) < 20:
        return 0.0, []
    Xw = Fv[w0 + ext]; Yw = wdy[ext]
    rkX = np.array([rankdata(Xw[:, j]) for j in range(len(cols))])
    rkY = rankdata(Yw)
    corrs = np.array([np.corrcoef(rkX[j], rkY)[0, 1] for j in range(len(cols))])
    top_idx = np.argsort(-np.abs(corrs))[:n_factors]
    # 全历史百分位（含当日，同训练口径）
    wsum = 0.0; s = 0.0; info = []
    for j in top_idx:
        rho = corrs[j]
        hist = p87.iloc[:t + 1, j].dropna()
        if len(hist) < 60:
            continue
        pct = float((hist < Fv[t, j]).mean())
        s += rho * (pct - 0.5) * 2
        wsum += abs(rho)
        info.append((cols[j], round(float(rho), 3), round(pct, 3)))
    return (np.clip(s / wsum, -1, 1) if wsum > 0 else 0.0), info


# ===================== 曲线监控：利差近5年分位（来源：DataHub 维护的利差历史） =====================
SPREAD_LABELS = [("sp1", "10Y-5Y"), ("sp2", "30Y-10Y"), ("sp3", "30Y-5Y")]
SPREAD_SRC = str(Bdata.spread_path())  # 展示用路径；读写均由 DataHub 负责


def load_spread_history():
    """读取利差历史（10Y-5Y / 30Y-10Y / 30Y-5Y，bp）；读不到返回 None（如实标注，不自行编造）。

    读取、格式兼容与缺失处理统一在 ``data_hub.collectors.bond.market``，
    策略侧不再自行解析 CSV，避免两套口径。
    """
    return Bdata.load_spread_history()


def extend_spread_history(sp_hist: pd.DataFrame, cn, base_date):
    """用刷新后的收益率补齐利差历史（若文件日期早于 base_date），保持追踪最新交易日。
    按期限独立补齐：某期限缺失只跳过依赖它的利差，其余照常补；当日无可算利差才整日跳过。"""
    if sp_hist is None or len(sp_hist) == 0:
        return sp_hist
    hist_last = sp_hist.index[-1]
    if hist_last >= base_date:
        return sp_hist
    new_rows = []
    for d in cn.index[(cn.index > hist_last) & (cn.index <= base_date)]:
        r = cn.loc[d]
        y5 = float(r["中国国债收益率5年"]) * 100 if not pd.isna(r.get("中国国债收益率5年")) else np.nan
        y10 = float(r["中国国债收益率10年"]) * 100 if not pd.isna(r.get("中国国债收益率10年")) else np.nan
        y30 = float(r["中国国债收益率30年"]) * 100 if not pd.isna(r.get("中国国债收益率30年")) else np.nan
        row = {"日期": d}
        ok = False
        if not (np.isnan(y5) or np.isnan(y10)):
            row["sp1"] = y10 - y5
            ok = True
        if not (np.isnan(y30) or np.isnan(y10)):
            row["sp2"] = y30 - y10
            ok = True
        if not (np.isnan(y30) or np.isnan(y5)):
            row["sp3"] = y30 - y5
            ok = True
        if ok:
            new_rows.append(row)
    if new_rows:
        add = pd.DataFrame(new_rows).set_index("日期").sort_index()
        sp_hist = pd.concat([sp_hist, add])
        sp_hist = sp_hist[~sp_hist.index.duplicated(keep="last")].sort_index()
        Bdata.save_spread_history(sp_hist)  # 写盘交给 DataHub，策略侧不直接落盘
    return sp_hist


def curve_monitor(cn, sp_hist, base_date):
    """10Y-5Y/30Y-10Y/30Y-5Y 利差在近5年历史(上一任务文件)中的分位数，≥80% 触发报警"""
    warn = None
    if sp_hist is None:
        return {"today": str(base_date.date()), "rows": [], "alerts": [],
                "unavailable": True, "warn": "上一个任务利差数据不可读，未计算分位（不编造）"}
    sp_hist = sp_hist.loc[:base_date]
    rows, alerts = [], []
    for col, lab in SPREAD_LABELS:
        col_s = sp_hist[col]
        if col_s.dropna().empty:
            continue
        cur_raw = col_s.iloc[-1]  # 当日该列值（按期限独立补行后可能缺失）
        if pd.isna(cur_raw):
            # 当日该利差不可得（依赖期限缺失）：如实标注，不使用前一交易日值冒充当日，也不触发报警
            hist = col_s.iloc[:-1].dropna()
            rows.append({"label": lab, "val": None, "pct": None, "alert": False, "n": len(hist)})
            continue
        cur = float(cur_raw)
        hist = col_s.iloc[:-1].dropna()  # 历史不含当日
        if len(hist) < 60:
            rows.append({"label": lab, "val": cur, "pct": None, "alert": False, "n": len(hist)})
            continue
        pct = float((hist < cur).mean())
        alert = pct >= 0.80
        rows.append({"label": lab, "val": cur, "pct": pct, "alert": alert, "n": len(hist)})
        if alert:
            alerts.append(f"{lab}利差 {cur:.1f}bp 处于近5年 {pct*100:.0f}% 分位（≥80%）")
    return {"today": str(base_date.date()), "rows": rows, "alerts": alerts,
            "unavailable": False, "warn": warn,
            "hist_start": str(sp_hist.index[0].date()), "hist_end": str(sp_hist.index[-1].date())}


# ===================== 元模型 =====================
def load_meta(name):
    import xgboost as xgb
    m = xgb.XGBClassifier()
    m.load_model(os.path.join(ASSET, f"meta_{name}.json"))
    cls_mean = json.load(open(os.path.join(ASSET, f"meta_{name}_mean.json")))
    meta = json.load(open(os.path.join(ASSET, f"meta_{name}_meta.json")))
    return m, cls_mean, meta


# ===================== SHAP 特征归因 =====================
META_FEAT_NAMES = ["p_lstm", "s_extreme", "d_nss_bp", "s_tech"]
META_FEAT_CN = {"p_lstm": "宏观LSTM", "s_extreme": "极端信号",
                "d_nss_bp": "NSS预测", "s_tech": "技术动量"}
CLS_NAMES = ["下行", "震荡", "上行"]


def compute_shap_attribution(m10, m30, f10, f30, cls10, cls30):
    """对 10Y/30Y 元模型做 SHAP 归因：各特征对预测方向的贡献。
    返回 dict；shap 不可用时返回 available=False，不影响主流程与推送。
    """
    try:
        import shap
    except Exception as e:
        print(f"[shap] SHAP 不可用: {e}", flush=True)
        return {"available": False, "warn": str(e)}
    try:
        def attr_block(model, feat, cls):
            exp = shap.TreeExplainer(model)
            sv = np.asarray(exp.shap_values(feat.reshape(1, -1)))
            # sv 形状 (1, n_feat, n_class) 或 (n_class, 1, n_feat)，统一成 (n_feat, n_class)
            if sv.ndim == 3 and sv.shape[0] == 1:
                sv = sv[0]
            elif sv.ndim == 3 and sv.shape[1] == 1:
                sv = sv[:, 0, :].T
            elif sv.ndim == 2 and sv.shape[1] == 1:
                sv = sv.T
            c = CLS_NAMES.index(cls)
            ev = np.asarray(exp.expected_value)
            if ev.ndim == 0:
                ev = np.array([ev])
            contribs = []
            for i, name in enumerate(META_FEAT_NAMES):
                contribs.append({
                    "name": name, "cn": META_FEAT_CN.get(name, name),
                    "value": round(float(feat[i]), 3),
                    "shap": round(float(sv[i, c]), 3),
                })
            contribs.sort(key=lambda x: -abs(x["shap"]))
            return {"direction": cls, "base": [round(float(x), 4) for x in ev],
                    "contribs": contribs}
        return {
            "available": True,
            "features": META_FEAT_NAMES,
            "10Y": attr_block(m10, f10, cls10),
            "30Y": attr_block(m30, f30, cls30),
        }
    except Exception as e:
        print(f"[shap] 归因计算失败(不影响推送): {e}", flush=True)
        return {"available": False, "warn": str(e)}


# ===================== 主流程 =====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true", help="只生成卡片不推送")
    ap.add_argument("--date", default=None, help="报告日 YYYY-MM-DD (默认今天)；数据基准=报告日上一交易日")
    ap.add_argument("--asof", default=None, help="强制数据基准日 YYYY-MM-DD (默认自动取上一交易日)")
    ap.add_argument("--force", action="store_true", help="数据基准无变化时也强制推送")
    args = ap.parse_args()

    cn, cb, p87, p50 = load_base_data()
    sp_hist, sp_warn = load_spread_history()
    cn, updated, y_info = refresh_latest(cn, p87)
    # DataHub bond 子包（中债国债全期限曲线）补齐 NSS 用的 8 期限曲线
    cb, curve_info = Bdata.refresh_curve(cb, cn)
    if curve_info.get("note"):
        print(f"[curve] {curve_info['note']}", flush=True)
    if curve_info.get("added_rows"):
        # 增量落回快照，使次日只补新交易日（历史不重抓）
        print(f"[curve] 已并入 {curve_info['added_rows']} 个交易日 → "
              f"{Bdata.save_curve_snapshot(cb)}", flush=True)

    # ---- 数据基准：报告日（默认今天）的上一交易日，如周一报告取上周五 ----
    if args.asof:
        base_date = pd.Timestamp(args.asof)
        if base_date not in cn.index:
            prev = cn.index[cn.index < base_date]
            base_date = cn.index[-1] if len(prev) == 0 else prev[-1]
    else:
        report_day = pd.Timestamp(args.date) if args.date else pd.Timestamp.now().normalize()
        prev = cn.index[cn.index < report_day]
        if len(prev) == 0:
            base_date = cn.index[-1]
        else:
            base_date = prev[-1]  # 严格小于报告日的最后交易日
    cn = cn.loc[:base_date]

    # ---- 防重复：数据基准无变化说明非交易日/无新数据，跳过推送（可 --force 强制） ----
    basis_file = os.path.join(OUT, "last_basis.txt")
    os.makedirs(OUT, exist_ok=True)
    if not args.force:
        if os.path.exists(basis_file):
            with open(basis_file) as fh:
                last_basis = fh.read().strip()
            if last_basis == base_date.strftime("%Y-%m-%d"):
                print(f"[skip] 数据基准 {base_date.date()} 与前次相同（非交易日或无新数据），跳过推送", flush=True)
                return
    with open(basis_file, "w") as fh:
        fh.write(base_date.strftime("%Y-%m-%d"))

    # 对齐日期口径：收益率与面板（截断到基准日）
    y10 = cn["中国国债收益率10年"].ffill() * 100
    y30 = cn["中国国债收益率30年"].ffill() * 100
    td = base_date
    p50u = p50.loc[p50.index <= base_date]
    p87u = p87.loc[p87.index <= base_date]
    print(f"[data] 数据基准日(上一交易日): {td.date()} 增量刷新={'是' if updated else '否(回退快照)'}", flush=True)

    # ---- LSTM p ----
    p10m = load_lstm("10y"); p30m = load_lstm("30y")
    SEQ = 20
    Pv = p50u.values.astype(np.float64)
    if len(Pv) < SEQ:
        print("面板数据不足20日", flush=True); sys.exit(1)
    X_last = Pv[-SEQ:][None, :, :]
    p10 = float(lstm_forward(p10m, X_last)[0])
    p30 = float(lstm_forward(p30m, X_last)[0])

    # ---- 极端信号 ----
    s10e, info10e = extreme_signal_latest(p87u, y10)
    s30e, info30e = extreme_signal_latest(p87u, y30)

    # ---- NSS AR(1)（周频曲线重索引到收益率日频后前向填充，与训练口径一致） ----
    cb_aligned = cb.reindex(cn.index).ffill()
    d10n, d30n = nss_ar1_predict(cb_aligned, win=500)
    d10 = float(d10n.iloc[-1]); d30 = float(d30n.iloc[-1])

    # ---- 技术动量 ----
    s10t, _ = tech_signal_latest(y10)
    s30t, _ = tech_signal_latest(y30)

    # ---- 元模型 ----
    m10, mean10, meta10 = load_meta("10y")
    m30, mean30, meta30 = load_meta("30y")
    f10 = np.array([p10, s10e, d10, s10t], dtype=float)
    f30 = np.array([p30, s30e, d30, s30t], dtype=float)
    pr10 = m10.predict_proba(f10.reshape(1, -1))[0]
    pr30 = m30.predict_proba(f30.reshape(1, -1))[0]
    esum10 = float(sum(pr10[c] * mean10[str(c)] for c in range(3)))
    esum30 = float(sum(pr30[c] * mean30[str(c)] for c in range(3)))
    lbl = {0: "下行", 1: "震荡", 2: "上行"}
    cls10 = lbl[int(pr10.argmax())]; cls30 = lbl[int(pr30.argmax())]

    # ---- SHAP 特征归因（10Y/30Y 各特征对预测方向的贡献） ----
    shap_attr = compute_shap_attribution(m10, m30, f10, f30, cls10, cls30)

    # ---- 曲线监控（利差近5年分位，来源上一个任务产物） ----
    sp_hist = extend_spread_history(sp_hist, cn, base_date)
    mon = curve_monitor(cn, sp_hist, base_date)
    r10now = y10.iloc[-1]; r30now = y30.iloc[-1]
    spr_now = r30now - r10now

    # ---- 组装报告 ----
    result = {
        "trade_date": td.strftime("%Y-%m-%d"),
        "data_updated": updated,
        "yields_channel": y_info.get("channel", "snapshot"),
        "yields_last": str(cn.index[-1].date()) if len(cn) else None,
        "signal10Y": {"p_lstm": round(p10, 3), "s_extreme": round(s10e, 3),
                      "d_nss_bp": round(d10, 2), "s_tech": round(s10t, 3),
                      "softprob": {"下行": round(float(pr10[0]), 3), "震荡": round(float(pr10[1]), 3),
                                   "上行": round(float(pr10[2]), 3)},
                      "direction": cls10, "expected_bp": round(esum10, 2)},
        "signal30Y": {"p_lstm": round(p30, 3), "s_extreme": round(s30e, 3),
                      "d_nss_bp": round(d30, 2), "s_tech": round(s30t, 3),
                      "softprob": {"下行": round(float(pr30[0]), 3), "震荡": round(float(pr30[1]), 3),
                                   "上行": round(float(pr30[2]), 3)},
                      "direction": cls30, "expected_bp": round(esum30, 2)},
        "curve": {"today10Y": round(r10now, 3), "today30Y": round(r30now, 3),
                  "spread": round(spr_now, 2), "monitor": mon},
        "shap": shap_attr,
        "spr_change": "走阔" if (esum30 - esum10) > 0.15 else ("收窄" if (esum30 - esum10) < -0.15 else "持平"),
        "data_basis": str(base_date.date()),  # 数据基准 = 上一交易日
        "data_source": {
            "yields": ("DataHub EDB RATE.CN.*（中债官方口径）"
                       + ("，当日无新增行" if not updated else "，已增量刷新"))
                      if str(y_info.get("channel", "")).startswith("datahub")
                      else "本地快照（DataHub 不可达）",
            "curve": f"DataHub bond 子包（中债全期限曲线，{curve_info.get('added_rows', 0)} 个交易日增量）"
                     if curve_info.get("channel", "").startswith("datahub") else "本地快照（DataHub 不可达）",
            "curve_last": curve_info.get("last_date", ""),
            "curve_note": curve_info.get("note", ""),
            "panels": "模型特征面板快照（87/50 维，非债券行情）",
        },
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"daily_{td.strftime('%Y%m%d')}.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    build_and_send_card(result, send=(not args.no_send))


def build_and_send_card(r, send=True):
    td = r["trade_date"]
    s10, s30 = r["signal10Y"], r["signal30Y"]
    spr_txt = r["spr_change"]
    curve = r["curve"]
    mon = curve["monitor"]
    alerts = mon["alerts"]
    alarm = len(alerts) > 0
    tpl = "red" if alarm else "blue"

    elements = []
    # ===== 消息头（沿用既有偏好：报告来自 DoMate | 主题 | 日期） =====
    elements.append(make_text_element(
        f"**报告来自于 DoMate**  |  国债收益率四维预测+曲线监控  |  **{td}**"
    ))
    elements.append(make_divider())

    # ===== 一、明日预测结论 =====
    t10, t30 = s10["direction"], s30["direction"]
    sign10 = "+" if s10["expected_bp"] > 0 else ""
    sign30 = "+" if s30["expected_bp"] > 0 else ""
    elements.append(make_text_element(
        f"**一、明日国债收益率预测**（收益率上行=债价跌=空）\n"
        f"🔵 10Y：**{t10}**，预计 **{sign10}{s10['expected_bp']:.2f}bp**\n"
        f"🔵 30Y：**{t30}**，预计 **{sign30}{s30['expected_bp']:.2f}bp**\n"
        f"📐 10Y-30Y 利差（当前 {curve['spread']:.2f}bp）：**{spr_txt}**"
    ))
    elements.append(make_divider())

    # ===== 二、四子模型信号 =====
    def fmt_sig(s):
        return (f"宏观LSTM {s['p_lstm']:.2f} · 极端 {s['s_extreme']:+.2f} · "
                f"NSS {s['d_nss_bp']:+.1f}bp · 技术 {s['s_tech']:+.2f}")
    elements.append(make_text_element(
        f"**二、四子模型信号**（正=上行压力）\n"
        f"10Y：{fmt_sig(s10)}\n"
        f"30Y：{fmt_sig(s30)}"
    ))
    elements.append(make_divider())

    # ===== 三、XGBoost 元模型 =====
    def fmt_prob(s):
        return f"{s['softprob']['下行']:.2f}/{s['softprob']['震荡']:.2f}/{s['softprob']['上行']:.2f}"
    elements.append(make_text_element(
        f"**三、XGBoost 元模型判断**（下行/震荡/上行）\n"
        f"10Y：{fmt_prob(s10)} → **{s10['direction']}**\n"
        f"30Y：{fmt_prob(s30)} → **{s30['direction']}**"
    ))
    elements.append(make_divider())

    # ===== 四、预测原因（SHAP 特征归因） =====
    shap_attr = r.get("shap") or {}
    if shap_attr.get("available"):
        def fmt_shap(sig_key):
            blk = shap_attr.get(sig_key) or {}
            cs = blk.get("contribs") or []
            if not cs:
                return None
            parts = []
            for c in cs[:3]:
                mark = "⬆" if c["shap"] >= 0 else "⬇"
                parts.append(f"{c['cn']}({c['value']:+.2f}){mark}{abs(c['shap']):.2f}")
            return f"{blk.get('direction','')}：" + "   ".join(parts)
        l10 = fmt_shap("10Y")
        l30 = fmt_shap("30Y")
        if l10 or l30:
            elems = ["**四、预测原因（SHAP 归因·正⬆=助推该方向·负⬇=压制）**",
                     "10Y " + (l10 or "n/a"),
                     "30Y " + (l30 or "n/a")]
            elements.append(make_text_element("\n".join(elems)))
            elements.append(make_divider())

    # ===== 五、曲线监控（利差近5年分位，≥80%报警） =====
    mon_rows = []
    for row in mon["rows"]:
        mark = "🔴" if row.get("alert") else "🟢"
        pct_txt = f"{row['pct']*100:.0f}%" if row.get("pct") is not None else "样本不足"
        if row.get("val") is None:
            mon_rows.append(f"{mark} {row['label']} 利差：当日缺失（依赖期限无数据，不编造）")
        else:
            mon_rows.append(f"{mark} {row['label']} 利差：{row['val']:.2f}bp（近5年 {pct_txt} 分位）")
    hdr = "**五、收益率曲线监控（利差近5年分位，≥80%报警）**"
    if mon.get("unavailable"):
        hdr += "  ⚠️ **数据不可读**"
        mon_rows = [f"🔴 上一个任务利差分位数据不可读，本项未计算（不编造）：{mon.get('warn','')}"]
    elif alarm:
        hdr += "  ⚠️ **触发报警**"
    elements.append(make_text_element(hdr + "\n" + "\n".join(mon_rows)))
    if not mon.get("unavailable") and len(mon["rows"]) > 0:
        _n = mon["rows"][0].get("n")
        elements.append(make_note(
            f"利差来源：上一个任务产物 yield_spread_5y.csv（{mon.get('hist_start','')} ~ {mon.get('hist_end','')}，{_n if _n else '≥5年'}个交易日窗口）；≥80%分位报警"
        ))
    if alarm:
        elements.append(make_note(" ⚠️ ".join(alerts)))
    elements.append(make_divider())

    elements.append(make_note(
        "基准数据：上一交易日 " + td + " 收盘" + (
            "（经 DataHub 增量刷新）" if str(r.get("yields_channel", "")).startswith("datahub")
            else "（接口不可达回退本地快照）") + "。"
        "数据来源：国债收益率与全期限曲线均经 DataHub（EDB RATE.CN.* + bond 子包中债曲线）取数；"
        "曲线覆盖至 " + str((r.get("data_source") or {}).get("curve_last", "")) + "。"
        "四维框架：宏观LSTM(50特征20日序列) / 极端信号(前20%极端日Spearman) / NSS-AR(1) / 技术动量(36指标250日)。"
        "利差分位数：10Y-5Y/30Y-10Y/30Y-5Y 相对近5年历史，≥80%报警。"
        "预测仅基于模型统计规律，不构成投资建议。"
    ))

    card = {
        "config": {"wide_screen_mode": True},
        "header": make_header(f"国债收益率四维预测+曲线监控 · {td}", tpl),
        "elements": elements,
    }
    if send:
        ok = send_feishu_card(card)
        print(f"[push] 飞书推送={'成功' if ok else '失败'}", flush=True)
    else:
        with open(os.path.join(OUT, f"card_{td}.json"), "w", encoding="utf-8") as fh:
            json.dump({"msg_type": "interactive", "card": card}, fh, ensure_ascii=False, indent=2)
        print(f"[card] 卡片已生成(未发送): out/card_{td}.json", flush=True)


if __name__ == "__main__":
    main()