#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《中美期权风险释放汇总》—— 每交易日 14:30 无人值守链路（纯程序化）

设计目标
--------
1. 单一入口：把原先散落的 18 个脚本串成一条可复跑的链路，工程内只保留这一个入口。
2. 零 Token：全链路只依赖本程序包 + 本地数据文件；运行期不调用大模型、不做自然语言推理。
3. 双市场：上篇 A 股五轨（沪深300/中证500/科创50/创业板ETF期权 + 中证1000 MO），
   下篇 美股三轨（标普500/纳指100/罗素2000）。
4. 红黄灯优先置前：波动率预警（VIX / ATM-IV / 隐历差 / R 值）在最前，风险释放灯随后。
5. 偏度分位 / VVIX 分位报警（2026-10-25 并入）：A股 5 标的近月（剩余 > 20 天）25Δ 认沽 IV −
   25Δ 认购 IV，与美股 CBOE SKEW 指数、美股 CBOE VVIX（波动率的波动率，标普500 口径），
   各取自身近一年 252 交易日分位；分位 ≥ 90% 即报警——卡片标红 + 邮件报警（正文含预警明细，
   内嵌近半年走势图，逐监控标的各附一张对应图）。
   注：A股 5 标的 VVIX（回望口径）已按用户口径停用，不再参与计算、展示与报警；美股 CBOE VVIX
   预警保留。
6. A股五轨 期权 GEX / Gamma Flip 报警（2026-10-25 并入，当日扩为五轨逐标的）：净GEX=Σ sign·Γ·OI·MULT·S²·0.01
   （Call+1/Put−1；ETF 期权乘数 10000，中证1000 股指期权 MO 乘数 100；DTE 取 30~180；
   30/90 恒定期限线性插值加权）；Gamma Flip 为 [0.90S,1.10S] 网格上净 GEX 过零点，
   dist=(S−flip)/S。逐标的独立判定报警条件：净GEX<0 **且** dist 一年分位 ≤ 5%（两者同时满足）
   —— 该标的卡片标红 + 邮件报警（逐标的明细，内嵌近半年走势图；同一数据日多标的越阈时合并为一张图）。
7. 预警出口：邮件。A股口径出现任一红灯、或任一中美标的偏度/VVIX 分位越阈、或 GEX/Flip 越阈时发邮件
   （区别于常态的飞书群卡片；不再有飞书私聊推送）。邮件正文为每个监控标的附对应走势图；邮件发送
   失败时，在飞书消息侧补一条失败通知（含数据日、报警构成、主通道与回退通道的失败原因），
   避免报警静默丢失。

用法
----
    python 中美期权风险释放汇总.py --send                 # 全链路 + 推卡片（A股红灯/偏度/GEX·Flip 报警时加发邮件）
    python 中美期权风险释放汇总.py --send --skip-non-trading  # 定时任务用：非A股交易日直接跳过
    python 中美期权风险释放汇总.py --send --no-fetch       # 离线自测：复用本地已落盘数据
    python 中美期权风险释放汇总.py --force                 # 忽略非交易日强制跑一次（手工补跑用）

链路
----
    交易日历 -> 合约基础信息 -> A股实时快照(标的实时价/期权实时链/IM期指实时) -> QVIX自算
    -> 美股(CBOE指数 + CBOE延迟链 + ETF) -> 指标计算 -> 判灯 -> 波动率5日预测
    -> 偏度/VVIX 分位监控 + 逐标的近半年走势图（附加、容错：失败不阻断主链路）
    -> A股五轨 GEX/Flip 监控 + 逐标的/合并走势图（附加、容错：失败不阻断主链路）
    -> 渲染飞书交互式卡片
    -> 邮件报警（A股红灯 或 偏度越阈 或 GEX/Flip 越阈；正文含明细 + 内嵌走势图） -> 渲染本地 HTML 日报

数据日（2026-11-05 盘中实时化口径）
----------------------------------
数据日固定为「当日」：每交易日 14:30 运行时 A 股尚未收盘，链路不再回落到上一收盘日，
而是从行情网页/交易所实时报价取**当日盘中快照**并自行计算全部 A 股指标：
  · 标的实时价  -> fetch_rt.py spot（新浪）
  · 期权实时链  -> fetch_rt.py chains（东财，沪/深/中金所）
  · IM 期指实时 -> fetch_rt.py futures（东财）
  · QVIX 等价隐波 -> fetch_qvix.py 自算 30 日恒定期限 ATM IV
显式指定可用 TRADE_DATE=YYYYMMDD 覆盖。美股仍取最近可得收盘/延迟快照（14:30 对应美东凌晨，
美股未开盘，属正常）。

数据日统一校验（用户 2026-11-05 口径）
--------------------------------------
A股所有口径必须落在「当日（盘中）」；美股允许为最近收盘。发布前对 A股（标的行情
underlying_hist 5 标的 / 期权链 / 4 条 QVIX / 5 标的链指标）与美股（链快照 / 5 条指数序列）
逐一核对，A股任一来源 != 当日即判为过期/不一致：此时**不推送飞书卡片、不推送本地日报、
不发预警邮件**，只在飞书侧发一条告警通知说明不符来源与期望数据日。校验函数见
data_freshness_check()。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
SES = os.path.join(BASE, "ses_gffe5ea0b23728ffegpx6GMQLqwA5ek")
PYLIBS = os.path.join(BASE, "pylibs")
TSCFG = os.path.join(BASE, "ses_gffe5fde225ad5ffePQAojl8RuNMiuQ")

os.chdir(SES)
for _p in (SES, TSCFG, PYLIBS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["PYTHONPATH"] = ":".join(
    [SES, TSCFG, PYLIBS, os.environ.get("PYTHONPATH", "")])

DISCLAIMER = (
    "本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，"
    "不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。"
    "数据可能存在延迟、遗漏或误差，请独立判断并根据自身风险承受能力审慎决策。"
)

# 预警邮件内嵌图（存在即带上），逐监控标的各一张，顺序即正文出现顺序
A_LABELS = {
    "300ETF": ("沪深300ETF期权(510300)", "510300.SH"),
    "500ETF": ("中证500ETF期权(510500)", "510500.SH"),
    "KC50": ("科创50ETF期权(588000)", "588000.SH"),
    "CYB": ("创业板ETF期权(159915)", "159915.SZ"),
    "ZZ1000": ("中证1000股指期权(MO 000852)", "000852.SH"),
}
ALERT_CHART_LABEL = {
    "alert_skew_us_spx.png": "标普500 CBOE SKEW 指数（近半年，含一年 P90 阈值线）",
    "alert_vol_us_spx.png": "标普500 VIX（近半年走势）",
    "alert_vol_us_ndx.png": "纳指100 VXN（近半年走势）",
    "alert_vol_us_rut.png": "罗素2000 RVX（近半年走势）",
    "alert_vvix_spx.png": "标普500 CBOE VVIX 波动率的波动率（近半年，含一年 P90 阈值线）",
    "gex_chart_alarm.png": "A股五轨 期权 GEX / Gamma Flip 越阈标的合并报警图（近半年走势）",
}
for _k, (_nm, _sym) in A_LABELS.items():
    ALERT_CHART_LABEL["alert_skew_%s.png" % _k] = \
        "%s 期权偏度分位（近半年，含一年 P90 阈值线）" % _nm

# A股 5 标的 VVIX（回望口径，见 vvix_ashare.py）已按用户口径停用：
# 不再计算、不出图、不报警、不进邮件附图（文件本体保留，便于后续按需恢复）。


# ------------------------------------------------------------------ 环境/工具
def _now():
    return datetime.datetime.now()


def calendar_trade_date():
    """上交所日历口径：<= 今天的最近一个交易日。"""
    import ts_pin
    pro = ts_pin.init()
    today = _now().strftime("%Y%m%d")
    cal = ts_pin.call(pro.trade_cal, exchange="SSE", start_date="20250101",
                      end_date=today, is_open="1")
    return sorted({str(x) for x in cal["cal_date"].tolist()})[-1]


def available_trade_date():
    """已实际落盘的最后一个交易日（以 510300.SH ETF 收盘为准）。"""
    import pandas as pd
    uh = pd.read_json(os.path.join(SES, "data", "underlying_hist.json"),
                      orient="records")
    d = uh[uh["code"] == "510300.SH"]["trade_date"].astype(str)
    return max(d) if len(d) else None


def is_trading_today():
    """今天是否为 A 股（上交所）交易日。查询失败时保守按「交易日」继续。"""
    import ts_pin
    today = _now().strftime("%Y%m%d")
    try:
        pro = ts_pin.init()
        cal = ts_pin.call(pro.trade_cal, exchange="SSE", start_date=today,
                          end_date=today, is_open="1")
        return len(cal) > 0
    except Exception as e:  # 网络/权限异常 -> 不阻断链路
        print("[warn] 交易日历查询失败，按交易日继续: %s" % str(e)[:120])
        return True


def _env():
    return os.environ.copy()


def run(step, args, env=None):
    cmd = [sys.executable, step] + list(args)
    print("\n=== %s %s ===" % (step, " ".join(args)), flush=True)
    r = subprocess.run(cmd, env=env or _env(), cwd=SES)
    if r.returncode != 0:
        raise SystemExit("STEP FAILED: %s %s (rc=%d)" % (step, args, r.returncode))


def run_soft(step, args, env=None):
    """附加步骤：失败只告警、不中断主链路（偏度分位/出图属附加项，坏掉不应影响主日报）"""
    try:
        run(step, args, env)
        return True
    except SystemExit as e:
        print("[warn] 附加步骤失败，主链路继续: %s" % e)
        return False


# ------------------------------------------------------------------ A股红灯
def ashare_reds(path=None):
    """从 data/final.json 汇总 A 股全部红灯。

    返回 (asof, [(名称, 维度, 原因), ...])
      · 波动率预警红灯：vol_summary.ashare.detail（judge.py 已只收录红灯）
      · 风险释放红灯：ashare[k].light 含 🔴
    """
    path = path or os.path.join(SES, "data", "final.json")
    if not os.path.exists(path):
        return "", []
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    asof = str(d.get("asof", "") or "")

    reds = []
    vs = (d.get("vol_summary") or {}).get("ashare") or {}
    for it in vs.get("detail", []) or []:
        reds.append((it.get("label", ""), "波动率预警", it.get("why", "")))
    for _k, v in (d.get("ashare") or {}).items():
        if "🔴" in str(v.get("light", "")):
            why = v.get("light_why") or v.get("light")
            reds.append((v.get("label", ""), "风险释放", why))
    return asof, reds


def _disp_date(yyyymmdd):
    s = str(yyyymmdd or "")
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 else s


# ------------------------------------------------------------------ 偏度分位报警
def load_skew_summary():
    p = os.path.join(SES, "data", "skew_pct_summary.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def skew_alarms(summary):
    """分位越阈列表：[(市场, 名称, 指标值, 一年分位, P90, 阈值, 口径), ...]
    口径为 "偏度"（A股近月25Δ偏度 / 美股 CBOE SKEW）或 "VVIX"（美股 CBOE VVIX）。
    A股 5 标的 VVIX 已按用户口径停用，不再参与报警判定。"""
    if not summary:
        return []
    thr = summary.get("thr", 90)
    out = []
    for _k, v in (summary.get("cn") or {}).items():
        if v.get("alarm"):
            out.append(("A股", v.get("label") or _k, v.get("skew"), v.get("pct"),
                        v.get("p90"), thr, "偏度"))
    u = summary.get("us")
    if u and u.get("alarm"):
        out.append(("美股", u.get("label") or "标普500", u.get("skew"), u.get("pct"),
                    u.get("p90"), thr, "偏度"))
    vv = summary.get("vvix")
    if vv and vv.get("alarm"):
        out.append(("美股", vv.get("label") or "标普500 VVIX", vv.get("vvix"), vv.get("pct"),
                    vv.get("p90"), thr, "VVIX"))
    return out


def alert_chart_paths():
    """预警邮件附图：逐监控标的各一张对应走势图（存在即带上）。
    返回 [(路径, 说明), ...]，顺序 = A股五轨偏度分位 → 标普500 SKEW →
    美股三轨波动率指数 → 标普500 VVIX。
    （GEX/Flip 越阈合并报警图由 gex_chart_paths() 另行追加；
      A股 5 标的 VVIX 已按用户口径停用，不再附图）"""
    names = ["alert_skew_%s.png" % k for k in ("300ETF", "500ETF", "KC50", "CYB", "ZZ1000")]
    names += ["alert_skew_us_spx.png", "alert_vol_us_spx.png", "alert_vol_us_ndx.png",
              "alert_vol_us_rut.png", "alert_vvix_spx.png"]
    out = []
    for f in names:
        p = os.path.join(SES, "data", f)
        if os.path.exists(p):
            out.append((p, ALERT_CHART_LABEL.get(f, f)))
    return out


# ------------------------------------------------------------------ GEX / Gamma Flip 报警（A股五轨逐标的）
def load_gex_summary():
    p = os.path.join(SES, "data", "gex_flip_summary.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def gex_alarms(summary):
    """逐标的越阈列表：[(key, label, level, 净GEX, flip, dist, pct, thr), ...]"""
    if not summary:
        return []
    thr = summary.get("thr", 5)
    out = []
    for a in (summary.get("alarms") or []):
        out.append((a.get("key"), a.get("label") or a.get("key"), a.get("level"),
                    a.get("gex_weighted"), a.get("flip_1"), a.get("dist"),
                    a.get("pct"), thr))
    return out


def gex_chart_paths():
    """预警邮件要内嵌的 GEX/Flip 合并报警图（仅越阈时生成/存在）。返回 [(路径, 说明), ...]。"""
    p = os.path.join(SES, "data", "gex_chart_alarm.png")
    return [(p, ALERT_CHART_LABEL["gex_chart_alarm.png"])] if os.path.exists(p) else []


def send_ashare_alert(asof, reds, always=False, skew=None, sk_alarms=None, gx=None):
    """红灯 / 偏度·VVIX 分位报警 / GEX·Flip 报警邮件（正文含预警明细 + 逐监控标的近半年走势图）。三者皆无且 always=False 时静默跳过。"""
    sk_alarms = sk_alarms or []
    gx_alarms = gex_alarms(gx)
    gx_alert = bool(gx_alarms)
    thr_skew = (skew or {}).get("thr", 90)
    # 分位报警按口径拆两类：偏度（A股25Δ / 美股 SKEW）与 VVIX（美股 CBOE VVIX；A股 VVIX 已停用）
    skew_only = [a for a in sk_alarms if not str(a[6] or "").startswith("VVIX")]
    vv_alarms = [a for a in sk_alarms if str(a[6] or "").startswith("VVIX")]
    if not reds and not sk_alarms and not gx_alert and not always:
        print("[邮件] A股无红灯、无偏度/VVIX报警、无GEX/Flip报警，按约定不发邮件")
        return "skipped"

    disp = _disp_date(asof)
    vol = [r for r in reds if r[1] == "波动率预警"]
    rel = [r for r in reds if r[1] != "波动率预警"]
    parts = []
    if reds:
        parts.append("A股红灯 %d 项" % len(reds))
    if skew_only:
        parts.append("偏度报警 %d 项" % len(skew_only))
    if vv_alarms:
        parts.append("VVIX 分位报警 %d 项" % len(vv_alarms))
    if gx_alert:
        parts.append("GEX/Flip 报警 %d 标的" % len(gx_alarms))
    subject = "【中美期权风险释放汇总】%s | %s" % (" + ".join(parts), disp)

    _st = (_jload(os.path.join(SES, "data", "data_status.json")) or {})
    _st_line = str(_st.get("line") or ("数据日：%s（A股口径，当日盘中）" % disp)).replace("**", "")
    lines = [
        "中美期权风险释放汇总 · 报警提醒",
        _st_line,
        "数据日：%s ｜ 报警构成：%s" % (disp, "；".join(parts)),
        "",
    ]
    if vol:
        lines.append("一、A股波动率预警（红）")
        for name, _dim, why in vol:
            lines.append("  · %s ｜ %s" % (name, why))
        lines.append("")
    if rel:
        lines.append("二、A股风险释放（红）")
        for name, _dim, why in rel:
            lines.append("  · %s ｜ %s" % (name, why))
        lines.append("")
    if sk_alarms:
        lines.append("三、期权偏度 / VVIX 分位报警（一年分位 ≥ %g%%，含美股）" % thr_skew)
        for a in sk_alarms:
            lines.append("  · %s %s ｜ %s=%.2f ｜ 一年分位 %.1f%% ｜ P90 %.2f"
                         % (a[0], a[1], a[6] or "偏度", a[2] or 0, a[3] or 0, a[4] or 0))
        lines.append("    口径：A股偏度=近月(剩余>20天) 25Δ认沽IV − 25Δ认购IV（单位 pt，delta 按各合约自身IV插值）；")
        lines.append("          美股偏度=标普500 CBOE SKEW 指数；")
        lines.append("          美股 VVIX=标普500 CBOE VVIX（VIX 期权隐含波动率，前瞻口径）；")
        lines.append("          美股 VVIX 与偏度同量纲但不同源，绝对水平不可直接混用；纳指/罗素2000 无 VVIX 等价口径。")
        lines.append("          分位取各标的自身近一年 252 交易日，分位 ≥ %g%% 即报警。" % thr_skew)
        lines.append("")
    if gx_alarms:
        lines.append("四、A股五轨 期权 GEX / Gamma Flip 报警（逐标的，共 %d 项）" % len(gx_alarms))
        for _k, label, level, gw, fl, dist, pct, thr in gx_alarms:
            lv = {"red": "●红", "yellow": "○黄(降级)"}.get(level, level)
            lines.append("  · %s ｜ %s ｜ 净GEX=%.4f 亿元(<0) ｜ Gamma Flip=%s ｜ dist=%s%% ｜ 一年分位=%s%% (≤%g%%)"
                         % (label, lv, gw or 0,
                            ("%.4f" % fl) if fl is not None else "—",
                            ("%.2f" % dist) if dist is not None else "—",
                            ("%.1f" % pct) if pct is not None else "—", thr))
        lines.append("    口径：净GEX=Σ sign·Γ·OI·MULT·S²·0.01（Call+1/Put−1；ETF 期权乘数 10000，")
        lines.append("          中证1000 股指期权(MO) 乘数 100）；DTE 取 30~180，30/90 恒定期限线性插值加权；")
        lines.append("          Gamma Flip=[0.90S,1.10S] 网格上净 GEX 过零点；dist=(S−flip)/S；")
        lines.append("          报警=净GEX<0 且 dist 一年分位≤阈值（两者同时满足），逐标的独立判定。")
        lines.append("")
    chart_items = alert_chart_paths() + gex_chart_paths()
    chart_paths = [p for p, _lbl in chart_items]
    if chart_items:
        lines.append("附图（近半年走势，逐监控标的各一张）")
        for cp, lbl in chart_items:
            lines.append("  · %s" % lbl)
            lines.append("[inline:%s]" % os.path.basename(cp))
        lines.append("")
    lines += [
        "口径说明",
        "  · 波动率红灯：252日分位≥90%，或绝对水位（科创50 VIX>42 / 创业板 VIX>34 /",
        "    中证1000 ATM-IV>28），或隐历差 IV−HV20 ≥ +8pct。",
        "  · 风险释放红灯：Lv1 命中≥2 项且红灯条件触发（R 值近端倒挂、SKEW 破位、",
        "    Gamma Flip 收破等）。",
        "  · VVIX 分位报警：美股 CBOE VVIX（标普500，VIX 期权隐含波动率，前瞻口径），",
        "    取自身近一年 252 交易日分位，分位 ≥ %g%% 即报警（与偏度分位共用阈值）。" % thr_skew,
        "  · 数据日与美股不一致属正常：美股以美东最近一个已收盘交易日为准。",
        "",
        DISCLAIMER,
    ]
    text_body = "\n".join(lines)

    rows = [(r[1], "", r[0], r[2]) for r in reds]
    rows += [("%s分位" % ("VVIX" if str(a[6] or "").startswith("VVIX") else "偏度"),
              a[0], a[1],
              "%s %.2f ｜ 一年分位 %.1f%% ≥ %g%%"
              % (a[6] or "偏度", a[2] or 0, a[3] or 0, thr_skew))
             for a in sk_alarms]
    for _k, label, level, gw, fl, dist, pct, thr in gx_alarms:
        rows.append(("GEX/Flip", "A股", label,
                     "净GEX %.4f 亿元<0 ｜ dist %s%% ｜ 一年分位 %s%% ≤ %g%%"
                     % (gw or 0,
                        ("%.2f" % dist) if dist is not None else "—",
                        ("%.1f" % pct) if pct is not None else "—", thr)))
    html_rows = "".join(
        "<tr><td style='padding:4px 10px'>%s</td><td style='padding:4px 10px'>%s</td>"
        "<td style='padding:4px 10px'>%s</td><td style='padding:4px 10px'>%s</td></tr>"
        % (dim, mk, nm_, detail) for dim, mk, nm_, detail in rows) \
        or "<tr><td colspan='4' style='padding:4px 10px'>—</td></tr>"
    html_imgs = "".join(
        "<div style='margin:8px 0'><img src='cid:%s' alt='%s' style='max-width:100%%;'></div>"
        "<div style='color:#666;font-size:13px;margin:-4px 0 12px 4px'>%s</div>"
        % (os.path.basename(cp), lbl, lbl)
        for cp, lbl in chart_items)
    html_body = (
        "<div style='font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif'>"
        "<h3 style='color:#c0392b'>中美期权风险释放汇总 · 报警提醒</h3>"
        "<p style='background:#fffbe6;border:1px solid #ffe58f;padding:6px 8px'><b>%s</b></p>"
        "<p>数据日：<b>%s</b>（A股口径，当日盘中）<br/>"
        "报警构成：<b style='color:#c0392b'>%s</b></p>"
        "<table border='0' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;border:1px solid #e5e5e5'>"
        "<tr style='background:#fafafa'><th style='padding:6px 10px;text-align:left'>维度</th>"
        "<th style='padding:6px 10px;text-align:left'>市场</th>"
        "<th style='padding:6px 10px;text-align:left'>名称</th>"
        "<th style='padding:6px 10px;text-align:left'>详情</th></tr>%s</table>"
        "%s"
        "<p style='color:#888;font-size:12px'>%s</p></div>"
        % (_st_line, disp, "；".join(parts), html_rows, html_imgs, DISCLAIMER)
    )

    print("[邮件] 触发报警提醒（%s；内嵌图 %d 张）-> %s"
          % ("；".join(parts), len(chart_paths), subject))
    import email_alert
    try:
        ok = email_alert.send_email(subject, text_body, html_body, images=chart_paths)
        fail_reason = "" if ok else (getattr(email_alert, "LAST_ERROR", "") or "原因未采集")
    except Exception as e:
        ok = False
        fail_reason = "发送模块异常: %s" % str(e)[:160]
        print("[邮件] 发送模块异常: %s" % str(e)[:200])
    if ok:
        return "sent"
    # 邮件发送失败：在飞书消息侧补通知（唯一出口以外的第二触达，避免报警静默丢失）
    mailbox = getattr(email_alert, "EMAIL_TO", "") or "（未配置）"
    note = ("数据日：%s\n"
            "报警构成：%s\n"
            "邮件失败双通道原因：%s\n"
            "本次预警明细未送达 %s（飞书卡片已推送，可作为临时备份入口）"
            % (disp, "；".join(parts), fail_reason, mailbox))
    print("[通知] 邮件发送失败，改在飞书消息侧通知")
    try:
        email_alert.notify_feishu(note, title="中美期权风险释放汇总｜预警邮件发送失败")
    except Exception as e:
        print("[通知] 飞书失败通知异常: %s" % str(e)[:200])
    return "failed"


# ------------------------------------------------------------------ 数据日统一校验
# 口径（用户 2026-10-30）：所有被监控标的的期权数据必须落在同一「最新可得交易日」。
# 任一来源落后（过期）或各来源互相不一致 -> 不推送日报/卡片，只发一条告警通知。
def _jload(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _d8(x):
    """归一化为 YYYYMMDD 字符串；无法解析返回 None。"""
    import pandas as pd
    if x in (None, ""):
        return None
    try:
        t = pd.to_datetime(x)
    except Exception:
        return None
    if pd.isna(t):
        return None
    return t.strftime("%Y%m%d")


def _qvix_last_date(fn):
    import pandas as pd
    try:
        q = pd.read_json(os.path.join(SES, "data", fn), orient="records")
        d = pd.to_datetime(q["date"], unit="ms", errors="coerce")
        if d.isna().all():
            d = pd.to_datetime(q["date"], errors="coerce")
        d = d.dropna()
        return d.max().strftime("%Y%m%d") if len(d) else None
    except Exception:
        return None


def _series_last_date(rows, datefield="trade_date"):
    vals = [str(r.get(datefield)) for r in rows
            if isinstance(r, dict) and r.get(datefield) not in (None, "")]
    return max(vals) if vals else None


def data_freshness_check(expected_a=None):
    """统一数据日校验。

    返回 (ok, exp_a, exp_u, rows)，rows=[(口径, 来源, 实际最新日, 期望日, 达标)]。
    A股期望日 = 当日（盘中口径，由主链路传入 expected_a）；任一 A股来源 != 当日即判过期。
    美股期望日 = 该市场内各来源实际最新日的最大值（美股为最近收盘，属正常）。"""

    import glob as _glob
    D = os.path.join(SES, "data")
    rows = []

    def add(mkt, src, got):
        rows.append([mkt, src, got or "缺", None, False])

    # ---- A股来源 ----
    uh = _jload(os.path.join(D, "underlying_hist.json")) or []
    add("A股", "标的行情 underlying_hist(510300)", _series_last_date(uh))
    chs = sorted(os.path.basename(p)[:-5] for p in _glob.glob(os.path.join(D, "chains", "*.json")))
    add("A股", "期权链 chains/ 最新", (chs[-1] if chs else None))
    for fn, nm in (("qvix_300etf.json", "QVIX 沪深300ETF"),
                   ("qvix_500etf.json", "QVIX 中证500ETF"),
                   ("qvix_kcb.json", "QVIX 科创50"),
                   ("qvix_cyb.json", "QVIX 创业板")):
        add("A股", nm, _qvix_last_date(fn))
    cm = _jload(os.path.join(D, "ashare_chain_metrics.json")) or []
    for tgt in ("300ETF", "500ETF", "KC50", "CYB", "ZZ1000"):
        add("A股", "链指标 %s" % tgt,
            _series_last_date([r for r in cm if r.get("target") == tgt], "date"))
    # 标的收盘价按 underlying_hist（每日 fetch 刷新）核对 5 标的；
    # 原 price_ext.json 为会话用派生文件、链路不再刷新，已随 A股 VVIX 出图一并停用，不再纳入数据日闸门。
    for code in ("510500.SH", "588000.SH", "159915.SZ", "000852.SH"):
        add("A股", "标的行情 underlying_hist(%s)" % code,
            _series_last_date([r for r in uh if r.get("code") == code]))

    # ---- 美股来源 ----
    uf = _jload(os.path.join(D, "us_final.json")) or {}
    add("美股", "us_final 链快照日", _d8(uf.get("snapshot")))
    import compute_us
    add("美股", "us_chain 快照日", (_d8(compute_us.resolve_snap()) if compute_us.resolve_snap() else None))
    for fn, col in (("us_VIX.json", "VIX"), ("us_VVIX.json", "VVIX"),
                    ("us_VXN.json", "VXN"), ("us_RVX.json", "RVX"),
                    ("us_SKEW.json", "SKEW")):
        rws = _jload(os.path.join(D, fn)) or []
        ds = [_d8(r.get("DATE")) for r in rws if isinstance(r, dict)]
        ds = [x for x in ds if x]
        add("美股", "指数 %s" % fn, max(ds) if ds else None)

    exp_a = str(expected_a) if expected_a else \
        max([r[2] for r in rows if r[0] == "A股" and r[2] != "缺"], default=None)
    exp_u = max([r[2] for r in rows if r[0] == "美股" and r[2] != "缺"], default=None)
    ok = True
    for r in rows:
        r[3] = exp_a if r[0] == "A股" else exp_u
        r[4] = (r[2] == r[3])
        if not r[4]:
            ok = False
    return ok, exp_a, exp_u, rows


def _notify_stale(exp_a, exp_u, bad):
    """数据日不统一/过期时的唯一输出：飞书告警通知（明确标注「过期数据」）。"""
    st = data_time_status(exp_a or "", rt_ok=False, include_metrics=True)
    lines = ["中美期权风险释放汇总｜【过期数据】，本次日报与卡片未推送。",
             "数据时点：A股数据日 %s（落后当日 %s 个交易日）｜ 美股数据日 %s" %
             (_disp_date(st.get("a_date")) or st.get("a_date") or "缺",
              st.get("a_lag_days") or 0,
              _disp_date(st.get("us_date")) or st.get("us_date") or "缺"),
             "期望数据日：A股 %s ｜ 美股 %s" % (_disp_date(exp_a) or exp_a, _disp_date(exp_u) or exp_u),
             "不符来源："]
    for mkt, src, got, exp, _ok in bad:
        lines.append("  · [%s] %s ｜ 实际 %s ｜ 期望 %s" % (mkt, src, got, exp))
    lines.append("按约定：A股各口径须为当日盘中；过期/不一致时不发布，仅告警。")
    note = "\n".join(lines)
    print(note)
    try:
        import email_alert
        email_alert.notify_feishu(note, title="中美期权风险释放汇总｜过期数据，已暂停发布")
    except Exception as e:
        print("[通知] 飞书告警发送异常: %s" % str(e)[:200])


# ------------------------------------------------------------------ 数据时点声明
# 口径（用户 2026-11-05）：每次运行都必须先声明本次拿到的是「盘中（自算）」还是「过期数据」，
# 三态标识「盘中（自算）/ 最近收盘 / 过期」同步到卡片首行、邮件正文与本地日报。
def data_time_status(D, rt_ok=False, include_metrics=True):
    """判定本次数据时点状态（三态），落盘 data/data_status.json，供卡片/邮件/日报引用。

    · 盘中（自算）：A股各口径均为当日，且当日数据由盘中实时快照取数并自算得到；
    · 最近收盘  ：A股各口径为当日，但非实时取数（离线复用等）；
    · 过期      ：A股存在来源落后于当日（滞后 N 个交易日）。
    include_metrics=False 时只核对取数层来源（标的行情/期权链/QVIX），用于计算前的提前声明。
    """
    import glob as _glob
    Dd = os.path.join(SES, "data")
    uh = _jload(os.path.join(Dd, "underlying_hist.json")) or []
    chs = sorted(os.path.basename(p)[:-5]
                 for p in _glob.glob(os.path.join(Dd, "chains", "*.json")))
    a_dates = [_series_last_date(uh), (chs[-1] if chs else None)]
    for fn in ("qvix_300etf.json", "qvix_500etf.json", "qvix_kcb.json", "qvix_cyb.json"):
        a_dates.append(_qvix_last_date(fn))
    if include_metrics:
        cm = _jload(os.path.join(Dd, "ashare_chain_metrics.json")) or []
        for tgt in ("300ETF", "500ETF", "KC50", "CYB", "ZZ1000"):
            a_dates.append(_series_last_date([r for r in cm if r.get("target") == tgt], "date"))
    a_dates = [x for x in a_dates if x]
    a_min = min(a_dates) if a_dates else None
    a_max = max(a_dates) if a_dates else None
    us_date = _d8((_jload(os.path.join(Dd, "us_final.json")) or {}).get("snapshot"))
    lag = 0
    if a_min and a_min != D:
        try:
            with open(os.path.join(Dd, "trade_cal.json"), encoding="utf-8") as f:
                cal = [str(x) for x in json.load(f)["all"]]
        except Exception:
            cal = []
        if D in cal:
            lag = len([x for x in cal if a_min < x <= D])
        else:   # 数据日晚于交易日历覆盖范围（补跑/指定未来日）时按工作日近似
            try:
                import datetime as _dt
                d0 = _dt.date(int(a_min[:4]), int(a_min[4:6]), int(a_min[6:8]))
                d1 = _dt.date(int(D[:4]), int(D[4:6]), int(D[6:8]))
                lag = sum(1 for i in range(1, (d1 - d0).days + 1)
                          if (d0 + _dt.timedelta(days=i)).weekday() < 5)
            except Exception:
                lag = 0
    if a_min == D and rt_ok:
        status = "盘中（自算）"
    elif a_min == D:
        status = "最近收盘"
    else:
        status = "过期"
    a_disp = _disp_date(a_min) or "缺"
    us_disp = _disp_date(us_date) or "缺"
    if status == "盘中（自算）":
        line = ("本次数据时点：**盘中（自算）** ｜ A股数据日 %s（当日盘中实时快照，"
                "隐波/偏度/GEX 均由期权链自算）｜ 美股数据日 %s（美东最近收盘）" % (a_disp, us_disp))
    elif status == "最近收盘":
        line = ("本次数据时点：**最近收盘** ｜ A股数据日 %s（非实时取数，当日/最近收盘口径）"
                "｜ 美股数据日 %s（美东最近收盘）" % (a_disp, us_disp))
    else:
        line = ("本次数据时点：**过期数据** ｜ A股数据日 %s（落后当日 %d 个交易日）"
                "｜ 美股数据日 %s ｜ 按约定：过期数据不推送" % (a_disp, lag, us_disp))
    out = dict(status=status, a_date=a_min, a_date_max=a_max, a_lag_days=lag,
               us_date=us_date, today=D, rt_ok=bool(rt_ok), line=line)
    try:
        with open(os.path.join(Dd, "data_status.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[warn] data_status.json 写入失败: %s" % str(e)[:120])
    return out


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="中美期权风险释放汇总 每日链路（纯程序化）")
    ap.add_argument("--send", action="store_true",
                    help="推送飞书交互式卡片；A股红灯、偏度/VVIX 越阈或 GEX/Flip 越阈时额外发邮件（内嵌逐标的走势图）")
    ap.add_argument("--no-fetch", action="store_true",
                    help="跳过全部取数步骤，复用本地已落盘数据（离线自测）")
    ap.add_argument("--skip-non-trading", action="store_true",
                    help="今天不是 A股交易日时直接跳过（定时任务用）")
    ap.add_argument("--force", action="store_true",
                    help="忽略非交易日判断，强制执行（手工补跑）")
    ap.add_argument("--email-always", action="store_true",
                    help="无论有无红灯/偏度/GEX·Flip 报警都发邮件（默认仅三者任一触发时发）")
    args = ap.parse_args()

    if args.skip_non_trading and not args.force and not is_trading_today():
        print("[skip] 今天不是 A股交易日，跳过本次执行")
        return

    env = _env()
    if args.no_fetch:
        D = env.get("TRADE_DATE") or available_trade_date() \
            or _now().strftime("%Y%m%d")
    else:
        D = env.get("TRADE_DATE") or calendar_trade_date()
    env["TRADE_DATE"] = D
    print("[calendar date]", D)

    rt_ok = False
    if not args.no_fetch:
        # 盘中实时化（用户 2026-11-05 口径）：数据日固定为「当日」，直接取当日盘中
        # 实时快照（行情网页/交易所报价）并自算指标，不再回落到最近收盘日。
        run("fetch_ashare2.py", ["cal"], env)      # 交易日历（TuShare）
        run("fetch_basics.py", [], env)            # 合约基础信息（TuShare opt_basic）
        rt_ok = True
        for _m in ("spot", "chains", "futures"):   # A股实时快照（新浪标的价 + 东财期权链/IM期指）
            rt_ok = run_soft("fetch_rt.py", [_m], env) and rt_ok
        run("fetch_qvix.py", [], env)              # 自算 30 日恒定期限 ATM IV（QVIX 等价）
        run("fetch_us.py", [], env)                # 美股（CBOE，最近收盘/延迟快照）
        print("[data date]", D)
    else:
        print("[no-fetch] 跳过取数，直接复用本地数据")

    # ---- 计算前的「数据时点」提前声明：本次到底是盘中（自算）还是过期数据 ----
    _st_pre = data_time_status(D, rt_ok=rt_ok, include_metrics=False)
    print("[数据时点] " + _st_pre["line"].replace("**", ""))

    run("compute_ashare.py", [], env)
    run("compute_us.py", [], env)
    run("analyze_ashare.py", [], env)
    run("judge.py", [], env)
    run("forecast_vol.py", [], env)

    # ---- A股 5 标的 VVIX（波动率的波动率，回望口径，见 vvix_ashare.py）已按用户口径停用 ----
    # 每日链路不再调用 vvix_ashare.py / make_vvix_charts.py：不计算、不出图、不报警、不进邮件附图；
    # vvix_ashare.py 与 data/vvix_ashare.json 文件本体保留（可逆，便于后续按需恢复）。

    # ---- 附加：期权偏度/ VVIX 分位监控（A股近月25Δ ｜ 美股 CBOE SKEW / VVIX）+ 近半年走势图 ----
    if not run_soft("skew_pct_monitor.py", [], env):
        # 失败即清理其产物，避免卡片/邮件读到过期截面或过期图
        for _f in ("skew_pct_summary.json", "skew_pct_cn_raw.json",
                   "skew_chart_all.png", "skew_iv_all.png"):
            try:
                os.remove(os.path.join(SES, "data", _f))
            except OSError:
                pass
        import glob as _glob
        for _p in _glob.glob(os.path.join(SES, "data", "alert_*.png")):
            try:
                os.remove(_p)
            except OSError:
                pass
        print("[warn] 偏度/VVIX 分位步骤失败，已清理其产物，卡片显示「待更新」")
    else:
        run_soft("make_skew_charts.py", [], env)
        run_soft("make_alert_charts.py", [], env)

    # ---- 附加：A股五轨 期权 GEX / Gamma Flip 监控（净GEX<0 且 Flip距离一年分位≤5%）+ 走势图 ----
    if not run_soft("gex_flip_monitor.py", [], env):
        for _f in ("gex_flip_summary.json", "gex_flip_series.json"):
            try:
                os.remove(os.path.join(SES, "data", _f))
            except OSError:
                pass
        print("[warn] GEX/Flip 步骤失败，已清理其产物，卡片显示「待更新」")
    else:
        run_soft("make_gex_charts.py", [], env)

    # ---- 数据日统一校验：A股各口径须为「当日盘中」；美股为最近收盘；过期/不一致则不发布，仅告警 ----
    gate_ok, exp_a, exp_u, gate_rows = data_freshness_check(D)
    print("[gate] 数据日统一校验：A股期望 %s ｜ 美股期望 %s ｜ 通过=%s" % (exp_a, exp_u, gate_ok))
    for _mkt, _src, _got, _exp, _ok in gate_rows:
        if not _ok:
            print("   [%s] %s ｜ 实际 %s ｜ 期望 %s  <-- 不符" % (_mkt, _src, _got, _exp))
    if not gate_ok:
        bad = [r for r in gate_rows if not r[4]]
        print("[gate] 存在过期/不一致数据（%d 项），按约定不推送日报与卡片" % len(bad))
        _notify_stale(exp_a, exp_u, bad)
        print("\n[OK] daily chain aborted (stale/inconsistent data) | expected = %s" % exp_a)
        return

    # ---- 计算完成后的「数据时点」终判（含链指标），供卡片首行/邮件正文/本地日报引用 ----
    st = data_time_status(D, rt_ok=rt_ok, include_metrics=True)
    print("[数据时点] " + st["line"].replace("**", ""))

    run("build_card.py", ["--send"] if args.send else [], env)
    run("build_report.py", [], env)

    skew_sum = load_skew_summary()
    sk_alarms = skew_alarms(skew_sum)
    if sk_alarms:
        print("[偏度/VVIX报警] 越阈标的: %s" % [("%s %s %s=%.2f %.1f%%" % (m, n, c or "偏度", s or 0, p or 0))
                                              for m, n, s, p, _p9, _t, c in sk_alarms])
    else:
        print("[偏度/VVIX报警] 无标的越阈")

    gex_sum = load_gex_summary()
    gx_alarms = gex_alarms(gex_sum)
    if gx_alarms:
        print("[GEX/Flip报警] 越阈标的 %d/%d: %s"
              % (len(gx_alarms), len(gex_sum.get("targets") or {}),
                 [("%s %s 净GEX%.4f dist%.2f%%" % (a[1], a[2], a[3] or 0, a[5] or 0))
                  for a in gx_alarms]))
    else:
        print("[GEX/Flip报警] 未触发（A股五轨）")

    if args.send:
        asof, reds = ashare_reds()
        # 预警唯一出口：邮件（A股红灯 / 任一偏度越阈 / GEX·Flip 越阈时触发，正文含明细 + 内嵌走势图）
        # 邮件发送失败时，额外在飞书消息侧补一条失败通知（避免报警静默丢失）
        mail_status = send_ashare_alert(asof, reds, always=args.email_always,
                                        skew=skew_sum, sk_alarms=sk_alarms, gx=gex_sum)
        print("[邮件] 邮件状态: %s" % mail_status)

    print("\n[OK] daily chain done | data date = %s | sent = %s" % (D, args.send))


if __name__ == "__main__":
    main()