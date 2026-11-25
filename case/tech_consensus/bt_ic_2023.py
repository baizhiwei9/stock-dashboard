# -*- coding: utf-8 -*-
"""2023 中证500股指期货(IC) 五方法回测 — 只做中证500，50万本金，保证金杠杆
规则:
  信号: 多=+1 空=-1 震荡=0 (五方法逐日观点)
  T+1 执行: t 日收盘出信号 -> t+1 日持仓(以 t+1 日收益结算, 开仓价=信号日收盘)
  标的: IC 等比复权主力连续(乘数200元/点), 保证金率12%
  手数: 开仓时全仓 N = floor(权益/(开仓价*200*0.12)), 期间不变, 平仓后按新权益重开
  手续费: 开/平各 0.23%%(万分之0.23) 成交额
  强平: 权益 < 占用保证金*0.5 -> 当日晚间强制平仓(结算后剩余权益), 之后可再开
统计: 总收益/年化/最大回撤/日胜率/段胜率/交易次数/持仓天数/爆仓次数
输出: backtest_out/ic2023_{method}.csv + ic2023_compare.csv + 净值图
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

import daily_views as DV  # noqa: E402

INIT_CAP = 500_000.0
MULT = 200          # IC 合约乘数 200 元/点
MARGIN = 0.12       # 保证金率 12%
FEE_RATE = 0.000023 # 单边手续费 0.23%%
MAINT = 0.5         # 强平线: 权益 < 保证金占用*0.5
BT_START = "2023-01-01"
BT_END = "2023-12-31"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "backtest_out")
os.makedirs(OUT, exist_ok=True)


def load_ic():
    df = pd.read_csv(os.path.join(HERE, "data_ic", "ic_ohlcv_adj.csv"),
                     parse_dates=["date"]).set_index("date").sort_index()
    return df


def views_for_ic(df):
    """各方法逐日观点（基于 IC 复权主连的 OHLC）"""
    # 统一 df 格式: index=date(dxke/madc/ac1vmd/dwt 使用), 另加显式 date 列供 chan 归一
    d = df.copy()
    d.insert(0, "date", d.index)
    out = {}
    for mkey, (fn, _label) in DV.METHODS.items():
        s = fn(d)
        s = s[s.index >= pd.Timestamp(BT_START)]
        out[mkey] = s
    return out


def backtest_futures(views, close):
    """views: Series(index=date), close: 复权收盘价(2023 区间)
    返回指标 dict 与净值序列(nav), 持仓段记录(segments)
    """
    v = views.reindex(close.index).fillna(0).astype(int)
    # t 日信号 -> t+1 持仓
    pos = np.zeros(len(v), dtype=int)
    pos[1:] = v.values[:-1]

    eq = INIT_CAP
    equity = np.full(len(v), np.nan)
    segs, seg_pnl = [], []
    n_force = 0
    cur_side, cur_n, cur_entry = 0, 0, None
    n_pos_days = 0
    daily_pnl = np.zeros(len(v))

    px = close.values
    for i in range(len(v)):
        side = int(pos[i])
        p_now = px[i]
        # ---- 平旧仓: 目标0或反向，先结算当前段 ----
        if cur_side != 0 and side != cur_side:
            seg_pnl.append(daily_pnl[cur_entry:i].sum())
            segs.append((cur_entry, i, cur_side))
            eq -= cur_n * p_now * MULT * FEE_RATE  # 平仓手续费
            cur_side, cur_n, cur_entry = 0, 0, None
        # ---- 开新仓: 目标不为0且当前空仓 ----
        if side != 0 and cur_side == 0:
            margin_per = p_now * MULT * MARGIN
            n = max(1, int(eq // margin_per)) if eq > margin_per * MAINT else 0
            if n > 0:
                cur_side, cur_n, cur_entry = side, n, i
                eq -= n * p_now * MULT * FEE_RATE
        # ---- 盯市盈亏 ----
        if cur_side != 0 and i > 0:
            pnl = cur_n * MULT * cur_side * (px[i] - px[i - 1])
            eq += pnl
            daily_pnl[i] = pnl
            n_pos_days += 1
            # 强平检查
            margin_used = cur_n * p_now * MULT * MARGIN
            if eq < margin_used * MAINT:
                eq = max(eq, 0.0)
                eq -= cur_n * p_now * MULT * FEE_RATE  # 强平平仓手续费
                eq = max(eq, 0.0)
                seg_pnl.append(daily_pnl[cur_entry:i + 1].sum())
                segs.append((cur_entry, i + 1, cur_side))
                n_force += 1
                cur_side, cur_n, cur_entry = 0, 0, None
        equity[i] = eq

    # 期末持仓结算 + 平仓手续费
    if cur_side != 0:
        last = len(v)
        p_end = px[last - 1]
        eq -= cur_n * p_end * MULT * FEE_RATE
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:last].sum())
        segs.append((cur_entry, last, cur_side))

    nav = pd.Series(equity, index=close.index)
    dd = nav / nav.cummax() - 1.0
    total_ret = float(nav.iloc[-1] / INIT_CAP - 1)
    n_days = len(nav)
    ann = (nav.iloc[-1] / INIT_CAP) ** (252 / n_days) - 1 if n_days > 0 else np.nan

    wins = sum(1 for s in seg_pnl if s > 0)
    seg_win = wins / len(seg_pnl) if seg_pnl else np.nan
    exposed = daily_pnl != 0
    day_win = float((daily_pnl[exposed] > 0).mean()) if exposed.sum() else np.nan

    metrics = {
        "总收益%": total_ret * 100,
        "年化%": ann * 100,
        "最大回撤%": float(dd.min()) * 100,
        "日胜率%": (day_win if not pd.isna(day_win) else np.nan) * 100,
        "段胜率%": (seg_win if not pd.isna(seg_win) else np.nan) * 100,
        "交易段数": len(seg_pnl),
        "持仓天数": n_pos_days,
        "多头天数": int((pos == 1).sum()),
        "空头天数": int((pos == -1).sum()),
        "空仓天数": int((pos == 0).sum()),
        "强平次数": n_force,
        "期末权益": nav.iloc[-1],
    }
    return metrics, nav, segs


def main():
    df = load_ic()
    close23 = df["close"]["2023"]
    views = views_for_ic(df)

    rows, navs = [], {}
    for mkey, (_fn, label) in DV.METHODS.items():
        m, nav, segs = backtest_futures(views[mkey], close23)
        r = dict(m)
        r["方法"] = label
        rows.append(r)
        navs[label] = nav

    dfm = pd.DataFrame(rows).set_index("方法")
    dfm = dfm[["总收益%", "年化%", "最大回撤%", "日胜率%", "段胜率%", "交易段数",
               "多头天数", "空头天数", "空仓天数", "强平次数", "期末权益"]]
    print(f"===== 2023 中证500股指期货(IC) 五方法 · 初始本金 {INIT_CAP:,.0f} =====")
    print(dfm.round(2).to_string())

    dfm.to_csv(os.path.join(OUT, "ic2023_compare.csv"), encoding="utf-8-sig")

    # 净值曲线图（Pillow 中文字体）
    try:
        draw_nav(navs, dfm)
    except Exception as e:  # noqa: BLE001
        print("  [!] 出图失败:", e)
    print(f"\n已保存: {OUT}/ic2023_compare.csv")


def draw_nav(navs, dfm):
    from PIL import Image, ImageDraw, ImageFont
    font_p = os.path.join(HERE, "fonts", "SimHei.ttf")
    f_title = ImageFont.truetype(font_p, 22)
    f_lab = ImageFont.truetype(font_p, 14)
    f_leg = ImageFont.truetype(font_p, 13)
    W, H = 1400, 760
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    colors = {"动力学MACD": "#1f77b4", "缠论分型": "#ff7f0e", "均线双通路": "#2ca02c",
              "AC1-VMD": "#d62728", "小波DWT": "#17becf"}
    dr.text((30, 22), "2023 中证500股指期货(IC) 五方法净值 · 50万本金保证金杠杆", fill="black", font=f_title)
    # 绘图区
    x0, y0, x1, y1 = 70, 90, 1340, 640
    all_nav = pd.concat(navs.values())
    lo, hi = all_nav.min() * 0.95, all_nav.max() * 1.05
    # 参考线 100% 本金
    ref_y = y1 - (INIT_CAP - lo) / (hi - lo) * (y1 - y0)
    dr.line([(x0, ref_y), (x1, ref_y)], fill="#999999", width=1)
    dr.text((x0 + 4, ref_y - 16), "本金线", fill="#666666", font=f_lab)
    for k, (name, nav) in enumerate(navs.items()):
        n = len(nav)
        pts = []
        for i, (dt, val) in enumerate(nav.items()):
            x = x0 + (x1 - x0) * i / max(n - 1, 1)
            y = y1 - (val - lo) / (hi - lo) * (y1 - y0)
            pts.append((x, y))
        if len(pts) > 1:
            dr.line(pts, fill=colors.get(name, "black"), width=2)
    # 坐标刻度
    for i in range(0, 7):
        x = x0 + (x1 - x0) * i / 6
        dr.line([(x, y1), (x, y1 + 6)], fill="black")
        idx = int(i * (n - 1) / 6)
        dr.text((x - 20, y1 + 8), str(nav.index[idx].date()), fill="black", font=f_lab)
    for i in range(0, 5):
        val = lo + (hi - lo) * i / 4
        y = y1 - (val - lo) / (hi - lo) * (y1 - y0)
        dr.line([(x0 - 6, y), (x0, y)], fill="black")
        dr.text((x0 - 70, y - 8), f"{val:,.0f}", fill="black", font=f_lab)
    # 图例（两列三行，避免超出画布）
    names = list(navs.keys())
    lx0, ly0 = 70, 672
    col_w = 640
    for idx, name in enumerate(names):
        col = idx // 3
        row = idx % 3
        lx = lx0 + col * col_w
        ly = ly0 + row * 26
        dr.line([(lx, ly), (lx + 26, ly)], fill=colors.get(name, "black"), width=4)
        r = dfm.loc[name]
        dr.text((lx + 32, ly - 9), f"{name}  收益{r['总收益%']:.1f}%  回撤{r['最大回撤%']:.1f}%",
                fill="black", font=f_leg)
    img.save(os.path.join(OUT, "ic2023_nav.png"))
    print("已保存净值图:", os.path.join(OUT, "ic2023_nav.png"))


if __name__ == "__main__":
    main()