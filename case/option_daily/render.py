# -*- coding: utf-8 -*-
"""option_daily render 层 —— 文字报告 + 偏度/期限结构小图

图只画**真实存在的链上数据**（各档 IV vs 行权价），不画「示意曲线」。
IV 全 0（DataHub 反解失败）时不出图，报告里写明原因。
"""
from __future__ import annotations

import os


def _f(v, dec=4, unit="", sign=False):
    if v is None:
        return "NA"
    fmt = ("%+" if sign else "%") + ".%df" % dec
    return (fmt % v) + unit


def _pct(v, dec=2):
    """小数 → 百分数显示（IV / 偏度都用它）。"""
    if v is None:
        return "NA"
    return "%.*f%%" % (dec, v * 100)


def build_text(m, trade_date, spot, diag, mode="full", prev=None):
    as_of = "%s-%s-%s" % (trade_date[:4], trade_date[4:6], trade_date[6:])
    L = ["## 沪深300ETF 期权日报 · %s" % as_of]
    if mode == "smile":
        L.append("> 本档只算波动率微笑/偏度（--smile-only 口径），不算 GEX。")
    L.append("")
    L.append("标的 510300（%s）现价 **%.4f**｜有效合约 %s 条（有 IV %s 条）"
             % ("沪深300ETF", spot or 0.0,
                diag.get("n_contracts", 0), diag.get("n_with_iv", 0)))
    L.append("")

    L.append("**① 核心指标**")
    L.append("")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("|---|---|---|")
    L.append("| 平值 IV | %s | 最近月 ATM 购/沽均值 |" % _pct(m.get("atm_iv")))
    L.append("| 25Δ 偏度 | %s | put25Δ IV − call25Δ IV |" % _pct(m.get("iv_skew"), 3))
    L.append("| 持仓 PCR | %s | 认沽持仓 / 认购持仓 |" % _f(m.get("pcr_oi"), 3))
    if mode == "smile":
        L.append("| Gamma Flip | （本档不算） | 22:20 全量档出 |")
    else:
        L.append("| Gamma Flip | %s | 现价±10%% 内净 GEX 变号点 |"
                 % _f(m.get("gex_flip"), 4))
    L.append("")

    gx = (diag.get("gex") or {})
    if mode != "smile":
        if gx.get("flip"):
            d = gx.get("dist_pct")
            side = "上方" if (d or 0) > 0 else "下方"
            L.append("Flip 在现价%s %.2f%%（净 GEX %s 亿元/1%%）；"
                     "现价在 Flip %s → %s。"
                     % (side, abs(d or 0), _f(gx.get("net_gex_yi"), 3),
                        "之上" if (d or 0) < 0 else "之下",
                        "正 gamma 区（波动被抑制）" if (d or 0) < 0 else "负 gamma 区（放波动）"))
        else:
            L.append("Flip 未算出：%s" % (gx.get("reason") or "原因未知"))
    if mode != "smile" and gx.get("n_legs"):
        L.append("参与 GEX 合约 %d 条（DTE 7~180 天，含持仓量）。" % gx["n_legs"])
    while L and not L[-1].strip():
        L.pop()
    L.append("")
    L.append("**② 偏度明细**")
    L.append("")
    if diag.get("put25_iv") is not None and diag.get("call25_iv") is not None:
        L.append("- 25Δ 认沽 IV：%s（行权月 %s，剩余 %s 天）"
                 % (_pct(diag.get("put25_iv"), 2), diag.get("expiry") or "—",
                    diag.get("dte") or "—"))
        L.append("- 25Δ 认购 IV：%s" % _pct(diag.get("call25_iv"), 2))
        rr = (diag["put25_iv"] or 0) - (diag["call25_iv"] or 0)
        L.append("- 差值：%s → %s" % (_pct(rr, 3),
                                     "看跌期权更贵（尾部避险溢价）" if rr > 0
                                     else "看涨期权更贵（追涨需求）"))
    else:
        L.append("- 未算出：%s" % (diag.get("skew_err") or "合约不足"))
    rows_cmp = []
    for k, nm, f in (("atm_iv", "平值 IV", _pct), ("iv_skew", "25Δ 偏度",
                                                   lambda x: _pct(x, 3)),
                     ("pcr_oi", "持仓 PCR", lambda x: _f(x, 3))):
        a, b = (prev or {}).get(k), m.get(k)
        if a is not None and b is not None and a != b:
            rows_cmp.append("- %s：%s → %s" % (nm, f(a), f(b)))
    if rows_cmp:
        L.append("")
        L.append("**③ 与上一档对比**")
        L.append("")
        L.extend(rows_cmp)
    L.append("")
    L.append("_数据：DataHub 期权链（TuShare opt_daily + opt_basic）；"
             "IV/PCR/偏度走 data_hub.core.option_metrics，Gamma 走 options_utility。"
             "不构成投资建议。_")
    return "\n".join(L) + "\n"


def build_smile_chart(contracts, spot, trade_date, out_path):
    """各档 IV vs 行权价（按到期月分色）。contracts 是补好 IV 的合约对象列表。"""
    pts = []
    for c in contracts or []:
        try:
            k = float(c.strike)
            iv = float(c.iv or 0)
            dte = int(c.dte or 0)
            cp = str(c.opt_type or "").upper()
        except (AttributeError, TypeError, ValueError):
            continue
        if k <= 0 or iv <= 0.02 or iv > 4.5:
            continue
        pts.append((dte, k, iv * 100, cp))
    if len(pts) < 8:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    import sys

    _lib = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "_lib"))
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    try:
        from assets import ensure_cjk_font
        f = ensure_cjk_font(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "fonts", "SimHei.ttf"))
        font_manager.fontManager.addfont(f)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
    except Exception:                                            # noqa: BLE001
        pass
    plt.rcParams["axes.unicode_minus"] = False

    by_dte = {}
    for dte, k, iv, cp in pts:
        by_dte.setdefault(dte, []).append((k, iv, cp))
    dtes = sorted(by_dte)[:3]        # 只画最近 3 个到期月，避免糊成一团

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    for dte in dtes:
        calls = sorted([(k, iv) for k, iv, cp in by_dte[dte] if cp == "C"])
        puts = sorted([(k, iv) for k, iv, cp in by_dte[dte] if cp == "P"])
        if calls:
            ax.plot([x[0] for x in calls], [x[1] for x in calls], "o-", ms=3,
                    lw=1.2, label="%d天 认购" % dte)
        if puts:
            ax.plot([x[0] for x in puts], [x[1] for x in puts], "s--", ms=3,
                    lw=1.2, label="%d天 认沽" % dte)
    if spot:
        ax.axvline(spot, color="#888", ls=":", lw=1)
        ax.annotate("现价 %.4f" % spot, (spot, ax.get_ylim()[1]),
                    xytext=(3, -12), textcoords="offset points", fontsize=8, color="#666")
    ax.set_xlabel("行权价", fontsize=10)
    ax.set_ylabel("隐含波动率 (%)", fontsize=10)
    ax.set_title("沪深300ETF 期权波动率微笑 · %s-%s-%s"
                 % (trade_date[:4], trade_date[4:6], trade_date[6:]), fontsize=11, pad=10)
    ax.grid(ls=":", lw=0.6, color="#ddd")
    ax.legend(fontsize=8, ncol=max(1, len(dtes)))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
