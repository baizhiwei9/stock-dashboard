#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美债监控（长短结构 + 加息/降息路径 + 财政部回购 + 拍卖认购与买方结构）
- 飞书交互式卡片发送脚本

用法:
  python3 send_treasury_card.py             # 仅构造卡片 JSON（落盘 treasury_card.json）
  python3 send_treasury_card.py --send      # 构造并发送到飞书群
"""
import json
import sys

# ---- 路径统一口径：case/_lib/paths.py（换机器只改一处）----------------------
try:
    from paths import feishu_router
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    import os as _o, sys as _s
    _d = _o.path.dirname(_o.path.abspath(__file__))
    while not _o.path.isfile(_o.path.join(_d, "_lib", "paths.py")):
        _n = _o.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    _s.path.insert(0, _o.path.join(_d, "_lib"))
    from paths import feishu_router


ROUTER_DIR = feishu_router()          # 统一口径；缺失时为空串，下面的 try 会降级
if ROUTER_DIR and ROUTER_DIR not in sys.path:
    sys.path.insert(0, ROUTER_DIR)

# feishu_router 是 DoMate 侧的共享推送路由，路径只在原运行机存在。
# 换机/云盘环境下缺它是常态，不能因此让整条取数链路判失败——
# 卡片 JSON 照常落盘，推送降级为「跳过并告警」，等路由模块可用时自动恢复。
try:
    import feishu_router  # noqa: E402  统一推送路由：默认「债券宏观」群，含股票内容时加推「AI股票消息」群
except ImportError:  # pragma: no cover - 环境相关
    feishu_router = None

WEBHOOK = ""  # 旧自定义机器人 webhook 已停用
TITLE = "美债监控 | 来自 DoMate"

SEV = {"green": 0, "yellow": 1, "orange": 2, "red": 3}


def _load(json_path):
    with open(json_path, encoding="utf-8") as fp:
        return json.load(fp)


def _merge_level(maturity, rate, buyback, auction=None):
    """综合灯位：取四部分最重档位。"""
    colors = []
    lv = maturity.get("level", "")
    colors.append("orange" if "黄灯" in lv else "red" if "红灯" in lv else "green")
    if buyback is not None:
        if buyback.get("weakness_signal"):
            colors.append("orange")
        elif buyback.get("latest"):
            colors.append(buyback["latest"].get("color", "green"))
    if rate is not None:
        # 深度定价/多轮节奏路径视为偏鹰/偏紧的关注档
        colors.append("orange" if "深度定价" in rate.get("band", "") else "yellow")
    if auction is not None:
        colors.append(auction.get("color", "green"))
    colors.sort(key=lambda c: SEV.get(c, 0))
    top = colors[-1]
    return {"red": "红灯（强预警）", "orange": "黄灯（关注）", "yellow": "黄灯（关注）",
            "green": "绿灯（正常）"}[top], top


def _make_row(cells, weights=None):
    if weights is None:
        weights = (2, 2, 2, 2)
    return {
        "tag": "column_set",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": weights[i],
             "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": str(c)}}]}
            for i, c in enumerate(cells)
        ],
    }


SIG_MARK = {"红": "🔴", "黄": "🟡", "绿": "🟢", "灰": "⚪"}
STAGE_MARK = {"动荡": "🔴", "投机": "🟠", "反弹": "🟡", "平静": "🟢"}


def _fmt_metric_value(v, unit):
    """按单位口径把指标值格式化为展示字符串；None -> —。"""
    if v is None:
        return "—"
    if unit == "百万USD":
        return "$%.2fT" % (v / 1e6)
    if unit == "百万EUR":
        return "€%.2fT" % (v / 1e6)
    if unit == "亿JPY":
        return "¥%.0fT" % (v / 1e4)
    if unit == "bp":
        return "%.1fbp" % v
    if unit == "%":
        return "%.3f%%" % v
    if unit == "index":
        return "%.2f" % v
    return str(v)


def _metric_row(mk, name, met):
    """6 项核心指标卡片行：指标｜当前值｜周环比｜20周均线｜信号。"""
    sig = met.get("signal") or "灰"
    mark = SIG_MARK.get(sig, "⚪")
    wow = met.get("wow_pct")
    if wow is not None:
        wow_s = "%+.2f%%" % wow
    elif met.get("wow_bp") is not None:
        wow_s = "%+.1fbp" % met["wow_bp"]
    elif met.get("wow_pts") is not None:
        wow_s = "%+.3fpp(月)" % met["wow_pts"]
    else:
        wow_s = "—"
    wma = met.get("wma20")
    if wma is not None:
        wma_s = _fmt_metric_value(wma, met.get("unit", ""))
    elif met.get("wma20_bp") is not None:
        wma_s = "%.1fbp" % met["wma20_bp"]
    else:
        wma_s = "—"
    return _make_row([name, _fmt_metric_value(met.get("value"), met.get("unit", "")),
                      wow_s, wma_s, "%s %s" % (mark, sig)], weights=(5, 3, 3, 3, 2))


def _liquidity_front_block(d):
    """⓪ 全球流动性速览（置顶栏目）：阶段标签 + 红黄预警 + 6项核心指标卡片。

    d 来自 treasury_liquidity_front.json（由 gen_liquidity_front.py 从
    macroliq_fetcher 已核验数据汇总，并合并 liquidity_metrics.json 的
    指标计算结果）。不联网不编造；缺数据如实标注缺口。
    """
    m = d.get("metrics") or {}
    met = m.get("metrics") or {}
    stage = m.get("stage")
    alerts = m.get("alerts") or []

    # ---- 阶段标签 + 红黄预警（最醒目） ----
    heads = []
    if stage:
        heads.append("流动阶段：**%s** %s" % (stage, STAGE_MARK.get(stage, "")))
    for a in alerts:
        mark = "🔴" if a.get("level") == "红" else "🟡" if a.get("level") == "黄" else "⚠"
        heads.append("%s **%s**：%s" % (mark, a.get("label", ""), a.get("why", "")))
    if not heads:
        heads.append("流动阶段：数据不足")
    title_content = ("**⓪ 全球流动性速览（阶段 / 预警 / 6项核心指标卡片）**\n"
                     + "\n".join("• " + h for h in heads))

    # ---- 6 项核心指标卡片 ----
    rows = [_make_row(["核心指标", "当前值", "周环比", "20周均线", "信号"], weights=(5, 3, 3, 3, 2))]
    card_metric_defs = [
        ("US_Net_Liquidity", "US_Net_Liquidity"),
        ("SOFR_IORB", "SOFR_IORB"),
        ("MOVE", "MOVE 美债波动率"),
        ("ACM_TermPremium_10Y", "ACM 10Y期限溢价"),
        ("ECB_BS", "ECB 资产负债表"),
        ("BOJ_BS", "BOJ 资产负债表"),
    ]
    card_rows = 0
    for mk, name in card_metric_defs:
        if mk in met:
            rows.append(_metric_row(mk, name, met[mk]))
            card_rows += 1

    # ---- Global_Liq 总览 ----
    gl = met.get("Global_Liq") or {}
    gl_line = "Global_Liq：**%s**（%s）＝US_Net_Liq＋ECB_BS＋BOJ_BS＋GlobalM2(ECB+BOJ折算)" % (
        _fmt_metric_value(gl.get("value"), gl.get("unit", "")), gl.get("date") or "—")
    if "PBoC_credit 缺失未计入" in (gl.get("note") or ""):
        gl_line += "；PBoC 项缺失"
    if (m.get("gaps") or []):
        gl_line += "（参照缺口行）"

    # ---- 补充明细（压缩，不再是主栏目） ----
    fred = d.get("fred") or {}
    move = d.get("move") or {}

    def mv(key):
        v = fred.get(key) or {}
        return v.get("value"), v.get("date")

    extra_pts = []
    walcl_v, walcl_d = mv("WALCL")
    tga_v, tga_d = mv("WTREGEN")
    rrp_v, rrp_d = mv("RRPONTSYD")
    ffr_v, ffr_d = mv("FEDFUNDS")
    iorb_v, iorb_d = mv("IORB")
    sofr_v, sofr_d = mv("SOFR")
    d2_v, d2_d = mv("DGS2")
    d10_v, d10_d = mv("DGS10")

    if walcl_v is not None and tga_v is not None:
        extra_pts.append("美联储总资产 **$%.2fT**（%s）｜TGA **$%.2fT**（%s）｜隔夜逆回购 **%.2f%%**（%s）"
                         % (walcl_v / 1e6, walcl_d, tga_v / 1e6, tga_d, rrp_v or 0, rrp_d))
    rates = []
    if ffr_v is not None:
        rates.append("联邦基金 %.2f%%（%s）" % (ffr_v, ffr_d))
    if iorb_v is not None:
        rates.append("IORB %.2f%%（%s）" % (iorb_v, iorb_d))
    if sofr_v is not None:
        rates.append("SOFR %.2f%%（%s）" % (sofr_v, sofr_d))
    if rates:
        extra_pts.append("政策利率：" + "｜".join(rates))
    if d2_v is not None and d10_v is not None:
        extra_pts.append("收益率：2Y **%.2f%%**（%s）｜10Y **%.2f%%**（%s）｜2s10s **%+.0fbp**"
                         % (d2_v, d2_d, d10_v, d10_d, (d10_v - d2_v) * 100))

    ecb_bs = d.get("ecb_bs") or {}
    boj_bs = d.get("boj_bs") or {}
    bs_parts = []
    if ecb_bs.get("value") is not None:
        bs_parts.append("ECB 总资产 **€%.2fT**（%s）" % (ecb_bs["value"] / 1e6, ecb_bs.get("date", "?")))
    if boj_bs.get("value") is not None:
        bs_parts.append("BOJ 总资产 **¥%.0fT**（%s）" % (boj_bs["value"] / 1e4, boj_bs.get("date", "?")))
    if bs_parts:
        extra_pts.append("央行资产负债表：" + "｜".join(bs_parts))

    ecb_m2 = d.get("ecb_m2") or {}
    boj_m2 = d.get("boj_m2") or {}
    m2_parts = []
    if ecb_m2.get("value") is not None:
        m2_parts.append("ECB M2 **€%.2fT**（%s）" % (ecb_m2["value"] / 1e6, ecb_m2.get("date", "?")))
    if boj_m2.get("value") is not None:
        m2_parts.append("BOJ M2 **¥%.0fT**（%s）" % (boj_m2["value"] / 1e4, boj_m2.get("date", "?")))
    if m2_parts:
        extra_pts.append("M2：" + "｜".join(m2_parts))

    acm = d.get("acm") or {}
    if acm.get("acmtp10") is not None:
        extra_pts.append("NY Fed ACM 10Y 期限溢价 **%.3f%%**（%s）" % (acm["acmtp10"], acm.get("date", "?")))

    gap_pts = []
    for g in (m.get("gaps") or []):
        gap_pts.append("数据缺口：%s" % g)
    gap_pts.append("MOVE 首期基线无历史：周环比/20周均线暂缺，每周一 09:00 自动记录，下期起更新同比")

    blocks = [
        {"tag": "div", "text": {"tag": "lark_md", "content": title_content}},
    ]
    if card_rows:
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "**核心指标卡片（当前｜周环比｜20周均线｜信号）**"}})
        blocks += rows
    else:
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "核心指标卡片：数据缺失"}})
    blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": gl_line}})
    for p in extra_pts:
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "• " + p}})
    for g in gap_pts:
        blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "⚠ " + g}})
    return tuple(blocks)


def _maturity_block(d):
    """① 长短结构：核心要点 + 6 项指标表 + 阈值口径。"""
    level = d["level"]
    f, t, rv, au = d["fed"], d["treasury"], d["rollover"], d["auction_month"]
    fc, tc, rc, auc, dc = f["compare"], t["compare"], rv["compare"], au["compare"], rv["compare"]

    def _d(a, b):
        """差值：任一侧缺失返回 None，避免 None - float 抛 TypeError。

        拍卖/存量对比表在源站缺发布时（如 bills_share_pct 当期未出）会整块为 None，
        旧写法直接相减会让卡片构造整体崩溃，连带推送和取数都判失败。"""
        if a is None or b is None:
            return None
        try:
            return a - b
        except TypeError:
            return None

    def _s(v, spec, na="—"):
        """安全格式化：None 或类型不符输出占位符，不打断卡片构造。"""
        if v is None:
            return na
        try:
            return spec % v
        except (TypeError, ValueError):
            return na

    fed_qq = fc["qoq"]["change_pct"]
    fed_wk = fc["wow"]["change_pct"]
    fed_yoy = fc["yoy"]["change_pct"]
    tbill = t["tbill_share_pct"]
    tbill_mom = _d(tbill, tc["mom"]["tbill_share_pct"])
    tbill_yoy = _d(tbill, tc["yoy"]["tbill_share_pct"])
    roll = rv["rollover_12m_pct"]
    roll_mom = _d(roll, rc["mom"]["rollover_12m_pct"])
    roll_yoy = _d(roll, rc["yoy"]["rollover_12m_pct"])
    au_now = au["bills_share_pct"]
    au_mom = _d(au_now, auc["mom"]["bills_share_pct"])
    au_yoy = _d(au_now, auc["yoy"]["bills_share_pct"])
    dur = rv["avg_maturity_months"]
    dur_mom = _d(dur, dc["mom"]["avg_maturity_months"])
    dur_yoy = _d(dur, dc["yoy"]["avg_maturity_months"])

    pts = []
    gap = _d(23.0, tbill)
    pts.append("T-bill 占可交易 %s%%（20%% 为关注线，距 23%% 强警线 %s pct）"
               % (_s(tbill, "%.2f"), _s(gap, "%.2f")))
    pts.append("美联储持仓短债占比 %s%%，同比 **%s pct**（1年内升幅最大），季环比 %s pct"
               % (_s(f["short_share_pct"], "%.2f"), _s(fed_yoy, "%+.2f"), _s(fed_qq, "%+.2f")))
    pts.append("滚动偿债比 %s%%，同比 %s pct，距 35%% 告警线 %s pct"
               % (_s(roll, "%.1f"), _s(roll_yoy, "%+.2f"), _s(_d(35.0, roll), "%.1f")))
    pts.append("平均剩余期限 %s 个月，同比 %s 个月，未破 65 个月告警线"
               % (_s(dur, "%.1f"), _s(dur_yoy, "%+.1f")))

    rows = [_make_row(["指标", "当前", "环比", "同比"])]
    rows += [
        _make_row(["美联储短债占比", _s(f["short_share_pct"], "%.2f%%"),
                   "周 %s / 季 %s" % (_s(fed_wk, "%+.2f"), _s(fed_qq, "%+.2f")),
                   "%s pct" % _s(fed_yoy, "%+.2f")]),
        _make_row(["T-bill 占可交易", _s(tbill, "%.2f%%"), "月 %s" % _s(tbill_mom, "%+.2f"),
                   "%s pct" % _s(tbill_yoy, "%+.2f")]),
        _make_row(["滚动偿债比(12m)", _s(roll, "%.2f%%"), "月 %s" % _s(roll_mom, "%+.2f"),
                   "%s pct" % _s(roll_yoy, "%+.2f")]),
        _make_row(["发债结构比(Bills)", _s(au_now, "%.2f%%"), "月 %s" % _s(au_mom, "%+.2f"),
                   "%s pct" % _s(au_yoy, "%+.2f")]),
        _make_row(["平均剩余期限", _s(dur, "%.1f 月"), _s(dur_mom, "%+.1f"),
                   "%s 月" % _s(dur_yoy, "%+.1f")]),
        _make_row(["政策信号", "待确认", "—", "—"]),
    ]
    return (
        {"tag": "div", "text": {"tag": "lark_md", "content": "**⓵ 国债长短结构**\n" + "\n".join("• " + p for p in pts)}},
        {"tag": "div", "text": {"tag": "lark_md", "content": "**指标一览（环比 / 同比）**"}},
        *rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": "阈值：T-bill ≥20% 关注 / ≥23% 强警 ｜ 滚动偿债 ≥35% 告警 ｜ 平均期限 <65 个月告警 ｜ 美联储短债占比单季 +5pct 告警\n"
                                                                "口径：FRN 不并入短端；滚动比/久期按 Auction 发行量加权（绝对值略低估，看趋势）；发债结构比月内日历效应显著，看季内累计"}},
    )


def _rate_block(d):
    """② 加息/降息路径判断（每周）：方向、幅度、大约几月。"""
    pts = []
    pts.append("方向 **%s**，总空间 **%.1fbp**；时间加权反解 **约 %d 次 × 25bp**（残差 %.1fbp，简单除法 %.2f 次仅作对照）"
               % (d["direction"], d["total_bp"], d["weighted_n"], d["weighted_residual_bp"], d["simple_n"]))
    pts.append("%s" % d["first_step"])
    pts.append("%s；%s；置信度 %s" % (d["band"], d["position"], d["confidence"]))

    # 未来路径时间点（FOMC 日历映射）：大约几月、每步多少、累计到多少
    sc = d.get("schedule")
    if sc and sc.get("items"):
        arrow = "+" if d["direction"] == "加息" else "-"
        line = "路径时间点（%s）：" % sc["mode"]
        pts.append(line)
        for it in sc["items"]:
            target = d["r_mid"] + (it["cumulative_bp"] if d["direction"] == "加息" else -it["cumulative_bp"]) / 100.0
            pts.append("  %s（%s）：%s %+dbp → %s%%"
                       % (it["month_hint"], it["meeting"], d["direction"],
                          it["step_bp"], round(target, 3)))
        if sc.get("human"):
            pts.append("一句话：%s" % sc["human"])

    rows = [
        _make_row(["R 目标区间中值", "%.2f%%" % d["r_mid"], "上界 %.2f / 下界 %.2f" % (d["r_up"], d["r_lo"]), ""]),
        _make_row(["Y2 两年美债", "%.2f%%" % d["y2"], "数据日 %s" % d["data_date"]["y2"], ""]),
        _make_row(["触发变量", "非农 / CPI / 鲍威尔讲话（风险平衡措辞）", "", ""]),
    ]
    return (
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**⓶ 加息/降息路径判断（每周）**\n" + "\n".join("• " + p for p in pts)}},
        *rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": "纪律：读数是'大概率场景'非精确值 ｜ 落地后 Y2 不动 = 已 price in，不构成新信号 ｜ 时间点按 FOMC 日历映射（约 6-7 周一场），非预测承诺"}},
    )


def _buyback_block(d):
    """③ 财政部国债回购监控。"""
    elems = []
    latest = d.get("latest")
    warn = []
    if not latest:
        warn.append("暂无最新回购结果")
    else:
        warn.append("%s｜%s｜%s｜上限 %.1f 亿美元｜实际买入 %.1f 亿（utilization **%.1f%%**，%s）｜报价 %.1f 亿（bid_cover %.1fx）｜接纳率 %.1f%%"
                    % (latest["operation_date"], latest["operation_type"], latest["maturity_bucket"],
                       latest["max_100m"], latest["accepted_100m"], latest["utilization_pct"],
                       latest["level"], latest["submitted_100m"], latest["bid_cover"],
                       latest["acceptance_rate_pct"]))
    if d.get("weakness_signal"):
        warn.append("**%s**" % d["weakness_note"])
    if d.get("long_end_note"):
        warn.append(d["long_end_note"])
    warn.append(d.get("qe_note", "口径提示：回购低于上限 != QE——财政部回购多数用短债筹资，不算美联储扩表，勿直接等同量化宽松"))

    rows = []
    if latest:
        rows = [
            _make_row(["指标", "当前值", "", ""]),
            _make_row(["操作类型", latest["operation_type"], "", ""]),
            _make_row(["期限篮子", latest["maturity_bucket"], "", ""]),
            _make_row(["单次上限", "%.1f 亿美元" % latest["max_100m"], "", ""]),
            _make_row(["实际买入", "%.1f 亿（%d 只）" % (latest["accepted_100m"], latest["issues_accepted"]), "", ""]),
            _make_row(["总报价", "%.1f 亿（%d 只）" % (latest["submitted_100m"], latest["issues_eligible"]), "", ""]),
            _make_row(["接纳率", "%.1f%%" % latest["acceptance_rate_pct"], "", ""]),
            _make_row(["Utilization", "%.1f%%（%s）" % (latest["utilization_pct"], latest["level"]), "", ""]),
            _make_row(["Bid Cover", "%.2fx" % latest["bid_cover"], "", ""]),
        ]
    next_op = d.get("upcoming", [{}])[0] if d.get("upcoming") else None
    next_line = "无待公布操作"
    if next_op:
        next_line = ("下次：%s｜%s｜%s｜上限 %.1f 亿美元（公告 %s，结算 %s）"
                     % (next_op.get("operation_date", "?"), next_op.get("bucket", "?"),
                        next_op.get("operation_type", "?"), (next_op.get("max_size_usd") or 0) / 1e8,
                        next_op.get("announcement_date", "?"), next_op.get("settlement_date", "?")))
    return (
        {"tag": "div", "text": {"tag": "lark_md", "content": "**⓷ 财政部国债回购监控**\n" + "\n".join("• " + w for w in warn)}},
        *rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": next_line}},
    )


def _auction_block(d):
    """④ 美债拍卖认购热情与买方结构（置于最末尾）。

    d 来自 treasury_auction_output.json：
      btc           12 期限认购倍数（最新 vs 前11次均值 + 近3期 + 判断）
      high_yield    得标利率（最新 vs 上一场可比）
      buyer_structure 三类占比（PD/Direct/Indirect）近 3 期表
      buyer_summary 买方结构要点
      stock_demand  存量需求端（美联储/海外/基差/货币基金）
      china_factor  中国因素（持仓/黄金）
      conclusion    一段话综合结论
    """
    pts = []
    btc = d.get("btc", {})
    items = btc.get("items", {})

    # 术语定义（浓缩）
    terms = ("一级交易商PD=承销托底（承接越高越差） ｜ 直接=对冲基金/自营自投标 "
             "｜ 间接=海外央行/国际投资者（海外需求代理） ｜ 认购倍数=总投标/发行额 "
             "｜ 得标利率=最后成交利率 ｜ 尾差=得标−WI（负=bid through 强）")
    pts.append("术语：%s" % terms)

    # 综合判断
    if d.get("conclusion"):
        pts.append("综合判断：%s" % d["conclusion"])

    # 认购倍数弱期限
    weak_list = btc.get("weak_list") or []
    if weak_list:
        w_detail = []
        for t in weak_list:
            v = items.get(t) or {}
            r3 = v.get("recent3") or []
            w_detail.append("%s（近3期 %s，趋势%s）" % (
                t, "→".join(str(x) for x in r3) if r3 else "—", v.get("trend", "—")))
        pts.append("认购倍数弱于近 12 次均值的期限：**%s**" % "；".join(w_detail))
    else:
        pts.append("认购倍数全部期限处于近 12 次均值附近（无强/弱偏离）")

    # 得标利率显著变动（coupon 期限，变化 > 20bp 提示）
    hy = d.get("high_yield", {})
    big_chg = []
    for term in ("2-Year", "3-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year"):
        v = hy.get(term) or {}
        if v.get("latest") is not None and v.get("chg_bp") is not None and abs(v["chg_bp"]) >= 20:
            big_chg.append("%s %s%%（%s%+dbp）" % (
                term.replace("-Year", "Y"), v["latest"],
                v["latest_auction_date"][5:], v["chg_bp"]))
    if big_chg:
        pts.append("得标利率显著上行（单期 ≥20bp）：" + "；".join(big_chg))

    # 买方结构要点
    bsum = d.get("buyer_summary", {})
    for note in bsum.get("notes", []):
        pts.append("买方结构：%s" % note)

    # 存量需求端
    sd = d.get("stock_demand", {})
    sd_line = []
    if sd:
        for k, v in sd.items():
            tag = {"fed_soma": "美联储SOMA", "tic_foreign": "海外持仓TIC",
                   "hedge_fund_basis": "基差交易", "money_market": "货币基金"}.get(k, k)
            sd_line.append("%s=%s" % (tag, v.get("judge", "—")))
    if sd_line:
        pts.append("存量需求端：%s" % " ｜ ".join(sd_line))

    # 中国因素
    cf = d.get("china_factor", {})
    if cf:
        ut = cf.get("us_treasury") or {}
        g = cf.get("gold") or {}
        pts.append("中国因素：美债 %sB（%s）｜黄金 %s 吨，连续 %s 个月增持" % (
            ut.get("holdings_bn"), ut.get("period"), g.get("tonnes"),
            g.get("months_consecutive")))

    # 缺数据说明
    for note in (d.get("notes") or []):
        pts.append("⚠ %s" % note)

    # 认购倍数表格（12 期限：最新 / 均值 / 偏差 / 判断）
    rows = [_make_row(["期限", "最新", "近12均", "偏差|判断"])]
    for term in ("4-Week", "13-Week", "17-Week", "26-Week", "52-Week",
                 "2-Year", "3-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year"):
        v = items.get(term) or {}
        if v.get("latest") is None:
            continue
        rows.append(_make_row(
            [term.replace("-Week", "W").replace("-Year", "Y"),
             "%.2f" % v["latest"], "%.2f" % (v["avg_prev11"] or 0),
             "%+.2f｜%s" % (v["dev"] or 0, v["judge"])]))

    # 买方结构表格：最新一期三类占比（取近 3 期中的最新）
    bs_rows = [_make_row(["期限", "PD", "Direct", "Indirect"])]
    bs = d.get("buyer_structure", {})
    for term in ("2-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year", "4-Week"):
        lst = bs.get(term) or []
        if not lst:
            continue
        r = lst[-1]
        bs_rows.append(_make_row(
            [term.replace("-Week", "W").replace("-Year", "Y"),
             "%.1f%%" % (r.get("pd_pct") or 0),
             "%.1f%%" % (r.get("direct_pct") or 0),
             "%.1f%%" % (r.get("indirect_pct") or 0)]))

    return (
        {"tag": "div", "text": {"tag": "lark_md", "content": "**⓸ 美债拍卖认购与买方结构（每周）**\n" + "\n".join("• " + p for p in pts)}},
        {"tag": "div", "text": {"tag": "lark_md", "content": "**认购倍数（12 个重点期限，最新 vs 近 12 次均值）**"}},
        *rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": "**买方结构（最新场次，占竞争性成交）**"}},
        *bs_rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": "口径：间接=海外/央行代理需求 ｜ 直接=对冲基金/自营 ｜ PD=一级交易商托底（越高越差）｜ 尾差因 fiscaldata 无 WI 报价标注缺数据 ｜ 判断阈值：偏差 |≥0.2| 为强/弱"}},
    )


def _options_block(o):
    """⑤ 美债 ETF 期权持仓量分布（每周变化报告）。"""
    labels = o.get("labels", {})
    pts = ["方向=%s｜结构=%s｜强度=%s｜结论=%s" % (
        labels.get("direction", "—"), labels.get("structure", "—"),
        labels.get("strength", "—"), labels.get("verdict", "—"))]
    for r in o.get("rules", []):
        d = r.get("detail", "")
        if d:
            pts.append("%s：%s" % (r["rule"], d))
    for c in o.get("sync_checks", []):
        pts.append("同步：%s" % c)
    for n in o.get("notes", []):
        pts.append("⚠ %s" % n)

    # 当前持仓量分布：TLT 主表 + 辅助标的简要
    dist = o.get("oi_distribution", {})
    tlt = dist.get("TLT") or {}
    rows = [_make_row(["标的", "主力call行权价", "主力put行权价", "PCR_oi"])]
    for sym, label in (("TLT", "TLT"), ("TLH", "TLH"), ("IEF", "IEF"), ("XLU", "XLU")):
        so = dist.get(sym)
        if not so:
            continue
        rows.append(_make_row([
            label,
            "%s (OI %s)" % (so.get("main_call_strike", "—"),
                            _fmt_oi(so.get("main_call_oi"))),
            "%s (OI %s)" % (so.get("main_put_strike", "—"),
                            _fmt_oi(so.get("main_put_oi"))),
            so.get("pcr_oi") if so.get("pcr_oi") is not None else "—"]))

    # 到期月分布（TLT，前 4 个月）
    ym_rows = [_make_row(["到期月", "Call OI", "Put OI", "合计"])]
    for e in (tlt.get("expiry_dist") or [])[:4]:
        ym_rows.append(_make_row([
            e["month"], _fmt_oi(e["call_oi"]), _fmt_oi(e["put_oi"]),
            _fmt_oi(e["total_oi"])], weights=(3, 2, 2, 2)))

    return (
        {"tag": "div", "text": {"tag": "lark_md", "content": "**⓹ 美债 ETF 期权持仓量分布（每周，TLT/TLH/IEF/XLU）**\n" + "\n".join("• " + p for p in pts)}},
        {"tag": "div", "text": {"tag": "lark_md", "content": "**主力行权价与 PCR（OI）**"}},
        *rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": "**TLT 到期月 OI 分布（前4月）**"}},
        *ym_rows,
        {"tag": "div", "text": {"tag": "lark_md", "content": "口径：OI=未平仓合约数，PCR_oi=put_oi/call_oi｜IV 为 BSM 反推（免费源无 IV 字段）｜2σ/80分位/IV百分位需 OI 历史积累后启用"}},
    )


def _fmt_oi(v):
    if v is None:
        return "—"
    return "%.0f万" % (v / 1e4) if v >= 1e4 else str(int(v))


def build_card(d, rate=None, buyback=None, auction=None, options=None, front=None):
    level, color = _merge_level(d, rate, buyback, auction=auction)
    template = {"red": "red", "orange": "orange", "yellow": "orange"}.get(color, "green")

    run_date = d.get("run_date", "?")
    data_line = "数据日：美联储 %s / 财政部 %s" % (d["fed"]["release_date"], d["treasury"]["record_date"])
    if buyback and buyback.get("data_as_of"):
        data_line += " ｜ 回购结果 %s" % buyback["data_as_of"]
    if rate and rate.get("data_date"):
        data_line += " ｜ 路径 R %s / Y2 %s" % (rate["data_date"]["r"], rate["data_date"]["y2"])
    if auction is not None:
        data_line += " ｜ 拍卖认购弱信号 %d 项" % auction.get("weak_count", 0)
    if options is not None:
        lab = options.get("labels", {})
        data_line += " ｜ 期权 %s/%s" % (lab.get("direction", "—"), lab.get("strength", "—"))
    if front is not None:
        data_line += " ｜ 流动性/MOVE %s" % (front.get("generated_at") or "?")
        serr = [k.replace("_err", "") for k in ("fred_err", "move_err", "boj_m2_err",
                                                "ecb_m2_err", "avail_err") if front.get(k)]
        if serr:
            data_line += "（缺 %s）" % "/".join(serr)

    header_md = "**综合灯位：%s**｜运行日 %s\n%s" % (level, run_date, data_line)
    elems = [{"tag": "div", "text": {"tag": "lark_md", "content": header_md}}, {"tag": "hr"}]
    if front is not None:
        elems += list(_liquidity_front_block(front))
        elems += [{"tag": "hr"}]
    elems += list(_maturity_block(d))
    elems += [{"tag": "hr"}, ]
    if rate is not None:
        elems += list(_rate_block(rate))
        elems += [{"tag": "hr"}]
    if buyback is not None:
        elems += list(_buyback_block(buyback))
        elems += [{"tag": "hr"}]
    if auction is not None:
        elems += list(_auction_block(auction))
        elems += [{"tag": "hr"}]
    if options is not None:
        elems += list(_options_block(options))

    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": TITLE}},
        "elements": elems,
    }
    return card


def main():
    d = _load("treasury_maturity_output.json")
    try:
        rate = _load("rate_path_output.json")
    except FileNotFoundError:
        rate = None
    try:
        buyback = _load("treasury_buyback_output.json")
    except FileNotFoundError:
        buyback = None
    try:
        auction = _load("treasury_auction_output.json")
    except FileNotFoundError:
        auction = None
    try:
        options = _load("options_output.json")
    except FileNotFoundError:
        options = None
    try:
        front = _load("treasury_liquidity_front.json")
    except FileNotFoundError:
        front = None
    card = build_card(d, rate=rate, buyback=buyback, auction=auction, options=options, front=front)
    with open("treasury_card.json", "w", encoding="utf-8") as fp:
        json.dump(card, fp, ensure_ascii=False, indent=2)
    print("卡片 JSON 已落盘 treasury_card.json, elements=%d, header=%s, 含rate=%s, buyback=%s, auction=%s, options=%s"
          % (len(card["elements"]), card["header"]["template"], rate is not None,
             buyback is not None, auction is not None, options is not None))

    if "--send" in sys.argv:
        if feishu_router is None:
            print("[WARN] feishu_router 不可用（%s 不存在），卡片已落盘 treasury_card.json，本次跳过推送"
                  % ROUTER_DIR)
            return
        results = feishu_router.push_card(card, tag="美债监控周报")
        ok = feishu_router.all_ok(results)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()