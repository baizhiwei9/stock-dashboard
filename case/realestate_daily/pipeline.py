# -*- coding: utf-8 -*-
"""realestate_daily 的框架适配：数据准备 → 日报 → 指标 → 推送/预警邮件

    fetch     准备周度输入 weekly_input.json（从 CASE_DATA_SRC 取，或确认本地已有）
    check     框架通用校验
    render    土狗状态机 + 付鹏周期 → out/report.md
    emit      贴现率/中性利率/双杀利差/周期定位/北京预警 → out/latest.json
    dispatch  飞书推送日报 + 北京看涨预警邮件（任务自己的推送）

数据口径：weekly_input.json 是上游产出（周度高频数据），本任务不编造、不补数；
拿不到就报警并失败，让上游先跑。原 daily_canghai_fupeng.py 保留为手动入口。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA_FILE = "weekly_input.json"
SLOW_FILE = "fupeng_frame_annual_slow_vars.json"


def _src_dir() -> str:
    return (os.environ.get("CASE_DATA_SRC") or "").strip()


def fetch(ctx):
    """数据准备：本地有就用，没有就从 CASE_DATA_SRC 拷进来；两处都没有就报警。"""
    os.makedirs(ctx.data_dir, exist_ok=True)
    local = os.path.join(HERE, DATA_FILE)
    src = _src_dir()

    if not os.path.isfile(local) and src:
        for name in (DATA_FILE, SLOW_FILE):
            s = os.path.join(src, name)
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(HERE, name))
                ctx.log("从 %s 复制 %s" % (src, name))
    if not os.path.isfile(local):
        ctx.warn("缺少周度输入数据", [
            "未找到 %s" % local,
            "也没有可用的 CASE_DATA_SRC（当前：%s）" % (src or "未设置"),
            "这个文件由上游周度任务产出，本任务不编造数据"], "error")
        raise RuntimeError("weekly_input.json 缺失，无法生成地产日报")

    with open(local, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data:
        raise RuntimeError("weekly_input.json 内容为空或不是对象")
    ctx.data = data

    # 各板块的截止日期汇总成一份摘要，供检查阶段判断新鲜度
    asof = {}
    for sec, items in data.items():
        if not isinstance(items, dict):
            continue
        dates = [str(v.get("as_of") or "") for v in items.values()
                 if isinstance(v, dict) and v.get("as_of")]
        if dates:
            asof[sec] = max(dates)
    with open(os.path.join(ctx.data_dir, "asof.json"), "w", encoding="utf-8") as f:
        json.dump({"sections": asof}, f, ensure_ascii=False, indent=2)

    # 指标 obs_date 只认日频截止日期（YYYY-MM-DD）；"2026Q2" 这类季度标记不进指标
    import re
    daily = [v for v in asof.values() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(v))]
    ctx.asof = max(daily) if daily else ""
    ctx.log("输入板块 %d 个，截止日期：%s（日频最新 %s）"
            % (len(data), asof, ctx.asof or "无"))


def render(ctx):
    """土狗状态机 + 付鹏四维 → 日报正文，落 out/report.md。"""
    import daily_canghai_fupeng as D
    data = getattr(ctx, "data", None)
    if not data:
        raise RuntimeError("render 阶段拿不到输入数据（fetch 未执行或失败）")

    tc = D.tugou_calc(data)
    fc = D.fupeng_calc(data)
    stage = D.fupeng_cycle_stage(fc, tc)
    triggered, reasons, signals = D.check_beijing_alert(data)
    body = D.build_daily_report(data, fc, tc, (triggered, reasons, signals))

    os.makedirs(ctx.out_dir, exist_ok=True)
    with open(os.path.join(ctx.out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(body + "\n")

    ctx.tc, ctx.fc, ctx.stage = tc, fc, stage
    ctx.alert = {"triggered": bool(triggered), "reasons": reasons or []}
    ctx.body = body
    ctx.log("日报已生成：out/report.md｜状态 %s｜周期 %s｜预警 %s"
            % (tc.get("state"), stage, "触发" if triggered else "未触发"))


def emit(ctx):
    """指标：贴现率/中性利率/双杀利差/周期定位/北京预警。"""
    tc = getattr(ctx, "tc", None)
    if not tc:
        raise RuntimeError("emit 阶段拿不到计算结果（render 未执行或失败）")
    fc, stage = ctx.fc, ctx.stage
    obs = str(getattr(ctx, "asof", "") or "")
    if not obs:
        # 兜底：只从能解析成 YYYY-MM-DD 的截止日期里取最新
        import re
        dates = []
        for items in (ctx.data or {}).values():
            if isinstance(items, dict):
                dates += [str(v.get("as_of")) for v in items.values()
                          if isinstance(v, dict) and v.get("as_of")]
        dates = [d for d in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
        obs = max(dates, default="")
    sec = "CN.REALESTATE"

    def num(key, val, name, unit="", dec=2):
        return {"key": key, "sec": sec, "obs_date": obs, "name": name,
                "value": val, "unit": unit, "dec": dec,
                "quality": "ok" if val is not None else "missing"}

    def text(key, val, name):
        return {"key": key, "sec": sec, "obs_date": obs, "name": name,
                "value_text": val or "", "quality": "ok" if val else "missing"}

    points = [
        text("re.tugou_state", tc.get("state"), "土狗状态机"),
        num("re.discount_rate", tc.get("disc"), "风险资产贴现率", "%"),
        num("re.neutral_rate", tc.get("neutral_tugou"), "土狗中性利率", "%"),
        num("re.kill_spread", fc.get("kill_spread"), "按揭-租金收益率利差", "%"),
        text("re.double_kill", "是" if fc.get("double_kill") else
             ("否" if fc.get("double_kill") is False else ""), "付鹏双杀判定"),
        text("re.cycle_stage", stage, "周期定位"),
        num("re.bj_alert", 1.0 if ctx.alert.get("triggered") else 0.0, "北京看涨预警", "", 0),
        text("re.consumption", tc.get("consumption"), "消费区间判定"),
    ]
    ctx.log("产出 %d 条指标" % len(points))
    return points


def dispatch(ctx):
    """任务自己的推送：飞书日报 + 预警邮件（触发才发）。"""
    import daily_canghai_fupeng as D
    body = getattr(ctx, "body", "")
    if not body:
        raise RuntimeError("dispatch 阶段没有日报正文（render 未执行或失败）")

    ctx.log("推送飞书日报 ...")
    rc = ctx.sh("%s %s %s %s" % (_q(sys.executable), _q(D.PUSH_SCRIPT),
                                 _q(D.REPORT_TITLE), _q(body)), check=False)
    if rc != 0:
        raise RuntimeError("飞书推送退出码 %s" % rc)

    alert = getattr(ctx, "alert", {}) or {}
    if alert.get("triggered"):
        ctx.log("北京看涨信号触发，发预警邮件 ...")
        subject = "【土狗付鹏预警】北京房价看涨（%s）" % __import__("datetime").date.today().isoformat()
        mail_body = "北京房价看涨趋势触发预警：\n" + "\n".join(
            "- %s" % r for r in alert.get("reasons") or [])
        ok, msg = D.send_alert_email(subject, mail_body)
        if not ok:
            ctx.warn("预警邮件未发出", [str(msg)], "warn")
    else:
        ctx.log("北京房价无看涨信号，不发预警邮件")


def _q(s):
    return '"%s"' % str(s).replace('"', '\\"')