# -*- coding: utf-8 -*-
"""tech_consensus 的框架适配：把原 run_daily.py 的一体式流程拆成五个阶段

    fetch     拉 7 只宽基的日线（缓存到 data/），落 data/market.json
    check     框架通用校验（data/ 有数据、out/latest.json 有指标）
    render    6 方法信号 → 汇总 → 出图 + 文字，落 out/
    emit      每只宽基的共识/看多占比/近 5 日与当日涨跌 → out/latest.json
    dispatch  飞书图文 + 邮件 + 多空转换提醒邮件（任务自己的推送）

原 run_daily.py 保留不动，仍是手动入口；定时调度改走 runner，阶段顺序由 task.json 的
pipeline 决定。改流程时优先改这里，不要在两处各写一套。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ETF 代码 → DataHub 证券 id（面板按指数代码分栏）
SEC_OF = {
    "sh510300": "000300.SH",
    "sh510500": "000905.SH",
    "sh510050": "000016.SH",
    "sz159915": "399006.SZ",
    "sh588000": "000688.SH",
    "sh512100": "000852.SH",
    "sh513180": "HSTECH.HI",
}
VALID_VIEWS = ("多", "空", "震荡")


def _asof(data):
    dates = [df["date"].iloc[-1] for df in data.values() if len(df)]
    return max(dates).strftime("%Y-%m-%d") if dates else ""


def fetch(ctx):
    """取数：先确保中文字体可用（出图依赖），再把行情缓存进 data/ 并落一份摘要。"""
    from assets import ensure_cjk_font
    font = ensure_cjk_font(os.path.join(HERE, "fonts", "SimHei.ttf"))
    ctx.log("中文字体：%s" % font)
    import data_fetch
    ctx.log("拉取行情数据（不使用旧缓存）")
    data = data_fetch.load_all(target="both", use_cache=False)
    if not data:
        raise RuntimeError("行情数据为空（所有数据源都没有返回）")
    asof = _asof(data)
    bars = {code: int(len(df)) for code, df in data.items()}
    short = [c for c, n in bars.items() if n < 60]
    if short:
        ctx.warn("部分标的样本偏短", ["%s 只有 %d 根" % (c, bars[c]) for c in short], "warn")
    os.makedirs(ctx.data_dir, exist_ok=True)
    with open(os.path.join(ctx.data_dir, "market.json"), "w", encoding="utf-8") as f:
        json.dump({"as_of": asof, "bars": bars}, f, ensure_ascii=False, indent=2)
    ctx.market = data
    ctx.asof = asof
    ctx.log("标的 %d 个，最新交易日 %s" % (len(data), asof))


def render(ctx):
    """用取数结果算信号、出图、写文字报告，产物全部落 out/。"""
    import run_daily
    from chart import draw_summary_chart
    from config import ETF_POOL
    from report import build_text, build_one_liner

    data = getattr(ctx, "market", None)
    if not data:
        raise RuntimeError("render 阶段拿不到取数结果（fetch 未执行或失败）")
    ctx.log("计算 6 方法信号 ...")
    views, consensus_row, hist, smc_detail = run_daily.compute_all(data)
    asof = ctx.asof or _asof(data)

    ret5, ret1 = {}, {}
    for e in ETF_POOL:
        c = hist[e["code"]]["close"]
        ret5[e["code"]] = float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else None
        ret1[e["code"]] = float(c.iloc[-1] / c.iloc[-2] - 1) if len(c) > 2 else None

    ctx.log("绘制汇总图 ...")
    img = draw_summary_chart(list(run_daily.METHODS.keys()), views, consensus_row, asof,
                             ret5_row=ret5, out_name="tech_consensus_summary.png")
    text = build_text(views, consensus_row, asof, ret5_row=ret5, ret1_row=ret1,
                      smc_detail=smc_detail)
    one = build_one_liner(consensus_row, asof)
    ctx.log(one)

    os.makedirs(ctx.out_dir, exist_ok=True)
    with open(os.path.join(ctx.out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(os.path.join(ctx.out_dir, "report_one_liner.txt"), "w", encoding="utf-8") as f:
        f.write(one + "\n")

    ctx.views, ctx.consensus_row = views, consensus_row
    ctx.ret5, ctx.ret1 = ret5, ret1
    ctx.img_path, ctx.text, ctx.one = img, text, one
    ctx.log("产物：%s / out/report.md" % os.path.basename(img))


def emit(ctx):
    """指标：每只宽基一条共识 + 看多方法占比 + 5 日/当日涨跌。"""
    if not getattr(ctx, "consensus_row", None):
        raise RuntimeError("emit 阶段拿不到汇总结果（render 未执行或失败）")
    from config import ETF_POOL

    points = []
    for e in ETF_POOL:
        code = e["code"]
        sec = SEC_OF.get(code, code)
        view = ctx.consensus_row[code][0]
        # 看多方法占比：只看有效观点（N/A 不计入分母）
        methods = list(ctx.views.keys())
        valid = [m for m in methods if ctx.views[m].get(code) in VALID_VIEWS]
        bull = [m for m in valid if ctx.views[m].get(code) == "多"]
        ratio = (len(bull) / float(len(valid))) if valid else None
        points.append({"key": "tech.consensus", "sec": sec, "obs_date": ctx.asof,
                       "name": "%s 技术共识" % e["name"], "value_text": view,
                       "quality": "ok" if valid else "missing"})
        points.append({"key": "tech.bull_ratio", "sec": sec, "obs_date": ctx.asof,
                       "name": "%s 看多方法占比" % e["name"],
                       "value": ratio, "dec": 2,
                       "quality": "ok" if ratio is not None else "missing"})
        for key, row, nm in (("tech.ret_5d", ctx.ret5, "近5日涨跌"),
                             ("tech.ret_1d", ctx.ret1, "当日涨跌")):
            v = row.get(code)
            points.append({"key": key, "sec": sec, "obs_date": ctx.asof,
                           "name": "%s %s" % (e["name"], nm),
                           "value": round(v * 100, 2) if v is not None else None,
                           "unit": "%", "dec": 2,
                           "quality": "ok" if v is not None else "missing"})
    ctx.log("产出 %d 条指标" % len(points))
    return points


def dispatch(ctx):
    """任务自己的推送：飞书图文 → 邮件 → 多空转换提醒邮件。"""
    from config import ETF_POOL

    img, text = getattr(ctx, "img_path", ""), getattr(ctx, "text", "")
    if not text:
        raise RuntimeError("dispatch 阶段没有报告正文（render 未执行或失败）")

    risk = [e for e in ETF_POOL if ctx.consensus_row[e["code"]][0] == "卖"]
    opp = [e for e in ETF_POOL if ctx.consensus_row[e["code"]][0] == "买"]
    header = "宽基指数技术分析多空平日报 · %s" % ctx.asof
    if risk:
        header += " | 注意风险"
    if opp:
        header += " | 注意机会"
    header += " | 来自 DoMate"
    body = text
    if risk:
        body = "**⚠️ 检测到风险：%s（6方法汇总建议卖出）**\n\n" % \
               "、".join(e["name"] for e in risk) + body

    import convert_alert
    cur = {e["code"]: {m: ctx.views[m].get(e["code"], "N/A") for m in ctx.views}
           for e in ETF_POOL}
    _flips, alert_text = convert_alert.check_and_build(cur, ctx.asof, ETF_POOL)

    ctx.log("推送飞书 ...")
    import feishu_push
    feishu_push.send_report(img, header, body)

    ctx.log("发送邮件 ...")
    import mail_push
    brief = ctx.one.split("：", 1)[1] if "：" in ctx.one else ctx.one
    ok = mail_push.send_mail("宽基多空平日报 %s：%s" % (ctx.asof, brief), body,
                             img_path=img, inline_img=True)
    if not ok:
        ctx.warn("日报邮件发送失败", ["mail_push.send_mail 返回 False"], "warn")

    if alert_text:
        ctx.log("发送多空转换提醒邮件 ...")
        ok = mail_push.send_mail("⚠️ 宽基多空转换提醒 %s" % ctx.asof, alert_text,
                                 img_path=img, inline_img=True)
        if not ok:
            ctx.warn("多空转换提醒邮件发送失败", ["mail_push.send_mail 返回 False"], "warn")