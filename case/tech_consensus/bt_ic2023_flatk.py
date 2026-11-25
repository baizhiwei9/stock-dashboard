# -*- coding: utf-8 -*-
"""2023 中证500股指期货(IC) 动力学MACD 震荡降仓变体对比
原版: 多=全仓+1 / 空=全仓-1 / 震荡=全平0
变体: 震荡时保留原方向 K 成仓位 (K=0.33/0.5/0.67)，多/空回到全仓
回测引擎与 bt_ic_2023.py backtest_futures 严格同构:
  - T+1 执行(pos[i]=w[i-1]); 开仓时手数=floor(权益/单手保证金)*|权重|(至少1手), 持仓期手数不变
  - 全仓<->K成切换只在权重等级变化时按基准手数差值调仓并收手续费
  - 方向变化/归零->平旧仓; 盯市 pnl=手数*200*方向*(px[i]-px[i-1]); 强平=权益<占用保证金*0.5
输出: backtest_out/ic2023_dxke_flat.csv + ic2023_dxke_flat.png
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

import daily_views as DV  # noqa: E402

INIT_CAP = 500_000.0
MULT = 200
MARGIN = 0.12
FEE_RATE = 0.000023
MAINT = 0.5
BT_START = "2023-01-01"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "backtest_out")
os.makedirs(OUT, exist_ok=True)


def load_ic():
    return pd.read_csv(os.path.join(HERE, "data_ic", "ic_ohlcv_adj.csv"),
                       parse_dates=["date"]).set_index("date").sort_index()


def design_weights(v, flat_k=0.0):
    """三态信号(多=1/空=-1/震荡=0) -> 连续权重序列(设计层):
    多=+1, 空=-1; 震荡且此前有持仓方向 -> 保留该方向*K(连续震荡延续±K), 否则 0
    """
    w = np.zeros(len(v), dtype=float)
    if len(v) > 0:
        w[0] = float(v.iloc[0])  # 首日直接沿用信号, 与原引擎 pos[1:]=v[:-1] 对齐
    for i in range(1, len(v)):
        cur = int(v.iloc[i])
        if cur != 0:
            w[i] = float(cur)
        elif flat_k > 0 and w[i - 1] != 0:
            w[i] = float(np.sign(w[i - 1])) * flat_k
        else:
            w[i] = 0.0
    return pd.Series(w, index=v.index)


def backtest_weight(w, close):
    """与 bt_ic_2023.backtest_futures 严格同构, 支持 ±1/±K/0 连续权重."""
    pos = np.zeros(len(w))
    pos[1:] = w.values[:-1]  # t 日设计 -> t+1 持仓

    eq = INIT_CAP
    equity = np.full(len(w), np.nan)
    segs, seg_pnl = [], []
    n_force = 0
    cur_side, cur_n, cur_n_full, cur_entry = 0, 0, 0, None
    n_pos_days = 0
    daily_pnl = np.zeros(len(w))

    px = close.values
    for i in range(len(w)):
        side = float(pos[i])   # 目标权重(带方向)
        p_now = px[i]
        t_sign = 1 if side > 0 else (-1 if side < 0 else 0)
        t_level = abs(side)    # 目标权重等级: 1(全仓) 或 K

        # ---- 平旧仓: 目标归零或反向 ----
        if cur_side != 0 and (t_sign == 0 or t_sign != cur_side):
            seg_pnl.append(daily_pnl[cur_entry:i].sum())
            segs.append((cur_entry, i, cur_side))
            eq -= cur_n * p_now * MULT * FEE_RATE
            cur_side, cur_n, cur_n_full, cur_entry = 0, 0, 0, None
            t_level_target = 0
        else:
            t_level_target = t_level
        # ---- 开新仓: 目标非0且空仓 ----
        if t_sign != 0 and cur_side == 0:
            margin_per = p_now * MULT * MARGIN
            n_full = int(eq // margin_per) if eq > margin_per * MAINT else 0
            if n_full > 0:
                n = max(1, int(round(n_full * t_level)))
                cur_side, cur_n, cur_n_full, cur_entry = t_sign, n, n_full, i
                eq -= n * p_now * MULT * FEE_RATE
        # ---- 持仓中全仓<->K成等级切换: 按基准全仓手数差值调仓 ----
        elif t_sign != 0 and cur_side != 0 and cur_n_full > 0:
            target_n = max(1, int(round(cur_n_full * t_level)))
            if target_n != cur_n:
                diff = abs(target_n - cur_n)
                eq -= diff * p_now * MULT * FEE_RATE
                cur_n = target_n
        # ---- 盯市 ----
        if cur_side != 0 and i > 0:
            pnl = cur_n * MULT * cur_side * (px[i] - px[i - 1])
            eq += pnl
            daily_pnl[i] = pnl
            n_pos_days += 1
            margin_used = cur_n * p_now * MULT * MARGIN
            if eq < margin_used * MAINT:
                eq = max(eq, 0.0)
                eq -= cur_n * p_now * MULT * FEE_RATE
                eq = max(eq, 0.0)
                seg_pnl.append(daily_pnl[cur_entry:i + 1].sum())
                segs.append((cur_entry, i + 1, cur_side))
                n_force += 1
                cur_side, cur_n, cur_n_full, cur_entry = 0, 0, 0, None
        equity[i] = eq

    # 期末持仓结算
    if cur_side != 0:
        p_end = px[-1]
        eq -= cur_n * p_end * MULT * FEE_RATE
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:len(w)].sum())
        segs.append((cur_entry, len(w), cur_side))

    nav = pd.Series(equity, index=close.index)
    dd = nav / nav.cummax() - 1.0
    total_ret = float(nav.iloc[-1] / INIT_CAP - 1)
    n_days = len(nav)
    ann = (nav.iloc[-1] / INIT_CAP) ** (252 / n_days) - 1 if n_days > 0 else np.nan
    wins = sum(1 for s in seg_pnl if s > 0)
    seg_win = wins / len(seg_pnl) if seg_pnl else np.nan
    exposed = daily_pnl != 0
    day_win = float((daily_pnl[exposed] > 0).mean()) if exposed.sum() else np.nan

    return {
        "总收益%": total_ret * 100,
        "年化%": ann * 100,
        "最大回撤%": float(dd.min()) * 100,
        "日胜率%": (day_win * 100 if not pd.isna(day_win) else np.nan),
        "段胜率%": (seg_win * 100 if not pd.isna(seg_win) else np.nan),
        "交易段数": len(seg_pnl),
        "持仓天数": n_pos_days,
        "强平次数": n_force,
        "期末权益": nav.iloc[-1],
    }, nav, segs


def main():
    df = load_ic()
    d = df.copy()
    d.insert(0, "date", d.index)
    s = DV.dxke_daily(d)
    close23 = df["close"]["2023"]
    s23 = s.reindex(close23.index).fillna(0).astype(int)

    variants = {
        "原版(震荡=全平)": 0.0,
        "震荡=1/3仓(K=0.33)": 0.33,
        "震荡=1/2仓(K=0.5)": 0.5,
        "震荡=2/3仓(K=0.67)": 0.67,
    }
    rows, navs = {}, {}
    for name, k in variants.items():
        w = design_weights(s23, flat_k=k)
        m, nav, segs = backtest_weight(w, close23)
        m["变体"] = name
        rows[name] = m
        navs[name] = nav
    dfm = pd.DataFrame(rows).T
    dfm = dfm[["总收益%", "年化%", "最大回撤%", "日胜率%", "段胜率%",
               "交易段数", "持仓天数", "强平次数", "期末权益"]]
    print("===== 2023 IC 动力学MACD 震荡降仓对比 (50万本金) =====")
    print(dfm.round(2).to_string())
    dfm.to_csv(os.path.join(OUT, "ic2023_dxke_flat.csv"), encoding="utf-8-sig")
    draw_nav(navs, dfm)


def draw_nav(navs, dfm):
    from PIL import Image, ImageDraw, ImageFont
    font_p = os.path.join(HERE, "fonts", "SimHei.ttf")
    f_title = ImageFont.truetype(font_p, 22)
    f_lab = ImageFont.truetype(font_p, 14)
    f_leg = ImageFont.truetype(font_p, 13)
    W, H = 1400, 780
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    colors = {"原版(震荡=全平)": "#1f77b4", "震荡=1/3仓(K=0.33)": "#d62728",
              "震荡=1/2仓(K=0.5)": "#2ca02c", "震荡=2/3仓(K=0.67)": "#9467bd"}
    dr.text((30, 22), "2023 IC 动力学MACD 震荡降仓变体净值 · 50万本金保证金杠杆", fill="black", font=f_title)
    x0, y0, x1, y1 = 70, 90, 1340, 620
    all_nav = pd.concat(navs.values())
    lo, hi = all_nav.min() * 0.95, all_nav.max() * 1.05
    ref_y = y1 - (INIT_CAP - lo) / (hi - lo) * (y1 - y0)
    dr.line([(x0, ref_y), (x1, ref_y)], fill="#999999", width=1)
    dr.text((x0 + 4, ref_y - 16), "本金线", fill="#666666", font=f_lab)
    nav0 = list(navs.values())[0]
    for name, nav in navs.items():
        n = len(nav)
        pts = []
        for i, (dt, val) in enumerate(nav.items()):
            x = x0 + (x1 - x0) * i / max(n - 1, 1)
            y = y1 - (val - lo) / (hi - lo) * (y1 - y0)
            pts.append((x, y))
        if len(pts) > 1:
            dr.line(pts, fill=colors.get(name, "black"), width=2)
    for i in range(0, 7):
        x = x0 + (x1 - x0) * i / 6
        dr.line([(x, y1), (x, y1 + 6)], fill="black")
        idx = int(i * (len(nav0) - 1) / 6)
        dr.text((x - 20, y1 + 8), str(nav0.index[idx].date()), fill="black", font=f_lab)
    for i in range(0, 5):
        val = lo + (hi - lo) * i / 4
        y = y1 - (val - lo) / (hi - lo) * (y1 - y0)
        dr.line([(x0 - 6, y), (x0, y)], fill="black")
        dr.text((x0 - 80, y - 8), f"{val:,.0f}", fill="black", font=f_lab)
    ly = 648
    for name, nav in navs.items():
        dr.line([(70, ly), (96, ly)], fill=colors.get(name, "black"), width=4)
        r = dfm.loc[name]
        dr.text((102, ly - 9), f"{name}  收益{r['总收益%']:.1f}%  回撤{r['最大回撤%']:.1f}%",
                fill="black", font=f_leg)
        ly += 26
    img.save(os.path.join(OUT, "ic2023_dxke_flat.png"))
    print("已保存:", os.path.join(OUT, "ic2023_dxke_flat.png"))


if __name__ == "__main__":
    main()