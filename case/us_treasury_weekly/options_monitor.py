#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美债 ETF 期权监控分析脚本（从统一数据层读取，不做联网）
============================================================
数据来源：options_data_output.json（options_data_fetch.py 抓取）
历史来源：options_oi_history.json（按日追加，供 2σ / 80 分位 / 20 日 95 分位）

监控对象：TLT（主）、TLH/IEF（10Y 档辅助）、XLU（利率敏感股验证）

报警规则：
  既有：
    R1  TLT 单日 call volume >= 30日均值 × 1.5 且 call/put vol >= 2.5
    R2  单合约 call OI 日增 >= 30% 且位于该合约20日95分位（需历史）
    R3  前10成交合约中 call 占比 >= 70%
    R4  同步校验：10Y/30Y 收益率是否高位回落、XLU 是否卖 put、TLT IV 百分位 > 90（需历史）
  新增：
    R5  持仓量（OI）变化超出近一年（取不到回退近一月）均值±2σ 或达 80 分位 → 邮件提醒
    R6  未超 1.5σ → 标红标星（弱异动标记）

输出：方向（赌收益率见顶/债价反弹/只是对冲）、结构（买虚值call/卖put/牛价差/双买）、
      强度（弱异动/中等/极端）、结论（偏多长债/偏空长债/波动率交易）+ 当前 OI 分布分析
      （按到期月/行权价分布、主力行权价）供每周报告使用。

用法：python3 options_monitor.py [--json 输出文件]
"""
import json

# ---- 统一配置（DataHub config / notify；真实值在本机 config.local.json，不入库）----
def _dh_secret(_name, _default=""):
    try:
        from data_hub import get_secret as _gs
        return _gs(_name, _default)
    except Exception:
        return _default


def _dh_cfg(_key, _default=None):
    try:
        from data_hub import cfg as _cfg
        return _cfg(_key, _default)
    except Exception:
        return _default


def _dh_tushare(_default=""):
    try:
        from data_hub import get_tushare_token as _gt
        return _gt() or _default
    except Exception:
        return _default
# ---------------------------------------------------------------------------
import os
import smtplib
import statistics
import sys
from datetime import date
from email.header import Header
from email.mime.text import MIMEText

DATA_FILE = "options_data_output.json"
HIST_FILE = "options_oi_history.json"


def _load(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _pct_rules(sym, d, hist):
    """R1: TLT 单日 call vol 异动。
    30日均值 = 期权 call_vol 历史快照的 30 日均值（与 call_vol 同口径）；
    历史不足 30 日时用全部可用历史，仍不足则标注待积累，不触发误报。"""
    so = d["symbols"][sym]
    t = so["totals"]
    res = {"rule": "R1", "hit": False, "detail": ""}
    snaps = [s for s in hist.get("snapshots", []) if sym in s.get("symbols", {})]
    vols = [s["symbols"][sym]["call_vol"] for s in snaps]
    if len(vols) >= 2:
        base = vols[-30:] if len(vols) >= 30 else vols
        avg30 = sum(base) / len(base)
        ratio = t["call_vol"] / avg30 if avg30 else 0
    else:
        ratio, avg30 = 0, None
    cp = t["pcr_vol"]
    res["ratio"] = round(ratio, 2)
    res["pcr_vol"] = cp
    if avg30 is None:
        res["detail"] = "期权成交量历史不足（%d 日），待积累后启用" % len(vols)
        return res
    res["hit"] = ratio >= 1.5 and cp >= 2.5
    res["detail"] = "call_vol/期权30日均量=%.2f (>=1.5: %s) | call/put vol=%.2f (>=2.5: %s)" % (
        ratio, ratio >= 1.5, cp, cp >= 2.5)
    return res


def _top10_call_share(sym, d):
    """R3: 前10成交合约中 call 占比（按 vol 排序取前10）。"""
    so = d["symbols"][sym]
    all_ = []
    for c in so["chain"]:
        all_.append((c["c_vol"], "C", c["strike"], c["expiry"]))
        all_.append((c["p_vol"], "P", c["strike"], c["expiry"]))
    all_.sort(key=lambda x: -x[0])
    top10 = all_[:10]
    c_cnt = sum(1 for x in top10 if x[1] == "C")
    share = c_cnt / len(top10) if top10 else 0
    return {"rule": "R3", "hit": share >= 0.7, "call_share": round(share, 2),
            "detail": "前10成交合约 call 占比 %d/10 = %.0f%%" % (c_cnt, share * 100)}


def _oi_change_rules(sym, d, hist):
    """R5/R6: OI 变化 vs 近一年（回退近一月）均值±2σ 与 80 分位；未超 1.5σ 标红标星。

    注意：历史快照中 OCC 回填的成交量快照（source=OCC_backfill）不含 OI 字段，
    只统计含 call_oi 的快照序列，避免把回填日当作 0 污染统计。

    降级策略（用户确认「无历史就用当天做标准」）：当含 OI 快照不足 2 日时，
    不再报「待积累」等待，改为按「当天标准」判定——以当天 OI 结构与已有
    成交量历史（OCC 回填）为证据输出判定，明确标注 history=none，不编造
    OI 历史 μ/σ/分位数值。"""
    snaps = hist.get("snapshots", [])
    oi_snaps = [s for s in snaps
                if sym in s.get("symbols", {})
                and s["symbols"][sym].get("call_oi") is not None]
    cur = d["symbols"][sym]
    res = {"rule": "R5", "hit": False, "score": None, "z": None,
           "p80": None, "window": None, "detail": "", "star": False}
    if len(oi_snaps) < 2:
        return _oi_day_standard(sym, d, hist, len(oi_snaps))
    # 当期 OI 变化
    prev = None
    for s in reversed(oi_snaps):
        if s["date"] != cur["as_of"]:
            prev = s["symbols"][sym]
            break
    if prev is None:
        res["detail"] = "无上一交易历史快照"
        return res
    t = cur["totals"]
    change = (t["call_oi"] + t["put_oi"]) - (prev.get("call_oi", 0) + prev.get("put_oi", 0))
    # 历史 OI 日增量序列（近一年优先，不足回退近一月）
    seq = []
    for i in range(1, len(oi_snaps)):
        a, b = oi_snaps[i - 1]["symbols"][sym], oi_snaps[i]["symbols"][sym]
        seq.append((b["call_oi"] + b["put_oi"]) - (a["call_oi"] + a["put_oi"]))
    window = seq[-252:] if len(seq) > 21 else seq  # 近一年≈252 交易日；不足取全量（近一月）
    res["window"] = "近一年" if len(seq) > 21 else "近一月(不足一年)"
    if not window:
        res["detail"] = "历史序列为空"
        return res
    mu, sd = statistics.mean(window), (statistics.pstdev(window) if len(window) > 1 else 0)
    z = (change - mu) / sd if sd else 0
    p80 = sorted(window)[int(len(window) * 0.8) - 1]
    hit = abs(z) >= 2 or change >= p80
    star = sd > 0 and abs(z) < 1.5  # 未超 1.5σ → 标红标星
    res.update({"hit": hit, "score": int(change), "z": round(z, 2),
                "mu": round(mu, 0), "sd": round(sd, 0), "p80": int(p80), "star": star,
                "detail": "OI变化 %d (μ=%.0f σ=%.0f, z=%.2f) | 80分位=%d | %s | 标星=%s" % (
                    change, mu, sd, z, p80, "触发" if hit else "未触发", star)})
    return res


def _oi_day_standard(sym, d, hist, n_oi_days):
    """R5/R6 降级：无 OI 历史时按「当天标准」判定（用户确认口径）。

    证据（均为真实可得，不编造历史）：
      1. 当天 OI 结构：call_oi 占比（call_oi/(call+put)）与 PCR_oi
      2. 当天成交量：call_vol/put_vol 与 PCR_vol
      3. 成交量历史（OCC 回填 234 日，若该标的有回填）：call_vol 相对历史的
         分位数/比值，作为当天放量程度的辅助证据
    R5 hit（极端信号 → 邮件）：call_oi 占比 >= 0.72（PCR_oi<=0.39，call 单边
      堆积偏乐观）或 <= 0.28（PCR_oi>=2.57，put 单边堆积偏悲观），或 call_vol
      达成交量历史 95 分位（仅当有成交量历史）。
    R6 标星（弱异动）：未达极端但有方向信号（call_oi 占比 >= 0.60 或 <= 0.40）。
    """
    cur = d["symbols"][sym]
    t = cur["totals"]
    total_oi = t.get("call_oi", 0) + t.get("put_oi", 0)
    call_share = t.get("call_oi", 0) / total_oi if total_oi else None
    pcr_oi = t.get("pcr_oi")
    pcr_vol = t.get("pcr_vol")
    # 成交量历史辅助（OCC 回填，可能为0）
    v_snaps = [s for s in hist.get("snapshots", [])
               if sym in s.get("symbols", {})
               and s["symbols"][sym].get("call_vol") is not None]
    vols = [s["symbols"][sym]["call_vol"] for s in v_snaps]
    vol_hist_ok = len(vols) >= 30
    vol_ratio = None
    vol_p95 = None
    if vol_hist_ok:
        vol_ratio = t["call_vol"] / (sum(vols[-30:]) / 30) if vols else None
        vol_p95 = sorted(vols)[int(len(vols) * 0.95) - 1]
        vol_p95_hit = t["call_vol"] >= vol_p95 and len(vols) >= 21
    else:
        vol_p95_hit = False

    hit = False
    flags = []
    if call_share is not None:
        if call_share >= 0.72:
            hit, flags = True, flags + ["call OI 占比 %.0f%%（极端单边堆积）" % (call_share * 100)]
        elif call_share <= 0.28:
            hit, flags = True, flags + ["put OI 占比 %.0f%%（极端单边堆积）" % ((1 - call_share) * 100)]
        elif call_share >= 0.60 or call_share <= 0.40:
            flags.append("call OI 占比 %.0f%%（方向性偏置）" % (call_share * 100))
    if vol_p95_hit:
        hit, flags = True, flags + ["call_vol=%.0f 达成交量历史 95 分位" % t["call_vol"]]
    # R6 弱异动标星：未达极端但有方向信号
    star = (not hit) and bool(flags)
    base = "OI 历史不足（%d 日，无免费历史源）；按【当天标准】判定（history=none，未编造历史）" % n_oi_days
    extra = " | ".join(flags) if flags else "未见单边堆积或放量信号"
    det = "%s | 当天: call_oi=%.0f put_oi=%.0f call占比=%.1f%% PCR_oi=%.2f PCR_vol=%.2f%s | %s | 标星=%s" % (
        base, t.get("call_oi", 0), t.get("put_oi", 0),
        (call_share or 0) * 100, pcr_oi or 0, pcr_vol or 0,
        (" 成交量30日均值比值=%.2f" % vol_ratio) if vol_ratio is not None else "",
        "触发" if hit else "未触发", star)
    res = {"rule": "R5", "hit": hit, "score": int(total_oi), "z": None,
           "p80": None, "window": "当天(无OI历史)", "detail": det, "star": star,
           "call_oi_share": round(call_share, 3) if call_share is not None else None,
           "history_days": len(vols), "vol_ratio_30d": round(vol_ratio, 2) if vol_ratio is not None else None}
    return res


def _sync_checks(d, hist):
    """R4 同步校验：10Y/30Y 高位回落、XLU 卖 put、TLT IV 百分位。"""
    y = d.get("yields", {})
    checks = []
    if y.get("y10") is not None and y.get("y30") is not None:
        checks.append("10Y=%.2f%% 30Y=%.2f%%（高位回落看周内，本次为单点快照）" % (y["y10"], y["y30"]))
    xlu = d["symbols"].get("XLU", {})
    if xlu:
        t = xlu.get("totals", {})
        # 卖 put 代理信号：put vol 相对放大或 put OI 高位 + put 侧虚值挡密集
        checks.append("XLU put_vol=%d PCR_v=%.2f（卖 put 信号需结合挡位分布周内确认）" % (
            t.get("put_vol", 0), t.get("pcr_vol") or 0))
    tlt_iv = d["symbols"]["TLT"].get("iv_stats", {})
    # IV 百分位需历史 IV（免费源缺失），周报中如实标注为单点数据
    checks.append("TLT ATM_IV=%.1f%% 中位=%.1f%%（IV 为单点，历史百分位无免费源未计算）" % (
        (tlt_iv.get("atm_iv") or 0) * 100, (tlt_iv.get("median_iv") or 0) * 100))
    return checks


def send_email_alert(subject, body):
    """R5 触发时通过 SMTP 发送提醒。
    凭据从环境变量读取（OPTIONS_SMTP_*）——不写入任何文件；
    未配置时打印提示并返回 False，不阻塞飞书推送。"""
    host = _dh_cfg("MAIL_SMTP_HOST")
    user = _dh_secret("MAIL_USER", "") or None
    pwd = _dh_secret("MAIL_PASS", "") or None
    to = _dh_secret("MAIL_DEFAULT_TO", "") or None
    port = int(_dh_cfg("MAIL_SMTP_PORT", "465"))
    if not (host and user and pwd and to):
        print("  ⚠ R5 触发但 SMTP 未配置（OPTIONS_SMTP_HOST/USER/PASS/TO），邮件未发送")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8").encode()
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, [to], msg.as_string())
        print("  邮件提醒已发送至 %s" % to)
        return True
    except Exception as exc:  # noqa: BLE001
        print("  ⚠ 邮件发送失败: %s" % exc)
        return False


def judge(d, hist):
    """汇总判定：四条既有规则 + 新增 OI 变化规则，输出四类标签。"""
    rules = [_pct_rules("TLT", d, hist), _top10_call_share("TLT", d),
             _oi_change_rules("TLT", d, hist)]

    tlt = d["symbols"]["TLT"]
    t = tlt["totals"]
    # ---- 方向 ----
    direction = "只是对冲"
    if t.get("pcr_vol", 0) >= 1.0 and t.get("pcr_oi", 0) >= 0.8:
        direction = "赌收益率见顶"     # put vol/PCR 双高 → 押注长债见顶回调
    elif t.get("pcr_vol", 0) <= 0.7 and t.get("pcr_oi", 0) <= 0.6:
        direction = "债价反弹"         # call 主导 → 押注长债反弹/收益率回落
    # ---- 结构 ----
    top = tlt["top"]
    structure = "牛价差"               # 默认：call 主导且主力在 ATM 上方
    if top.get("put_top_oi", 0) > top.get("call_top_oi", 0):
        structure = "卖put"
    elif t.get("pcr_vol", 0) >= 1.5:
        structure = "双买"             # put 成交异常放大 → 双买波动
    # ---- 强度（命中规则数 + call 异动幅度）----
    hits = [r for r in rules if r.get("hit")]
    ratio = next((r.get("ratio", 0) for r in rules if r.get("rule") == "R1"), 0)
    if len(hits) >= 2 or ratio >= 2.5:
        strength = "极端"
    elif len(hits) >= 1 or ratio >= 1.5:
        strength = "中等"
    else:
        strength = "弱异动"
    # ---- 结论 ----
    if direction == "债价反弹":
        verdict = "偏多长债"
    elif direction == "赌收益率见顶":
        verdict = "偏空长债"
    else:
        verdict = "波动率交易"

    stars = [r.get("star") for r in rules if r.get("rule") == "R5"]
    # R5 判定口径（历史充足 vs 当天标准降级）
    r5 = next((r for r in rules if r.get("rule") == "R5"), {})
    r5_mode = "历史2σ/80分位" if r5.get("window") not in ("当天(无OI历史)", None) else "当天标准(无OI历史)"
    return {
        "run_date": date.today().isoformat(),
        "level": "红灯（强预警）" if strength == "极端" else
                 "黄灯（关注）" if strength in ("中等",) else "绿灯（正常）",
        "labels": {"direction": direction, "structure": structure,
                   "strength": strength, "verdict": verdict},
        "rules": rules,
        "sync_checks": _sync_checks(d, hist),
        "r5_mode": r5_mode,
        "oi_distribution": build_oi_distribution(d),
        "notes": ["成交量历史 OCC 回填 %d 日（source=OCC_backfill）；R5 判定=%s：历史 OI 无免费源，"
                  "不足时按当天标准判定（不编造历史，记录于 R5.detail）" % (
                      len(hist.get("snapshots", [])), r5_mode)],
    }


def build_oi_distribution(d):
    """当前持仓量分布（供每周报告）：主要标的的到期月/行权价分布 + 主力行权价。"""
    out = {}
    for sym in ("TLT", "TLH", "IEF", "XLU"):
        so = d["symbols"].get(sym)
        if not so:
            continue
        top = so["top"]
        total = so["totals"]
        out[sym] = {
            "expiry_dist": so["distrib_expiry"],
            "strike_top": so["distrib_strike"][:6],
            "main_call_strike": top.get("call_top_strike"),
            "main_call_oi": top.get("call_top_oi"),
            "main_put_strike": top.get("put_top_strike"),
            "main_put_oi": top.get("put_top_oi"),
            "call_oi": total["call_oi"], "put_oi": total["put_oi"],
            "pcr_oi": total["pcr_oi"],
        }
    return out


def main():
    d = _load(DATA_FILE)
    try:
        hist = _load(HIST_FILE)
    except FileNotFoundError:
        hist = {"snapshots": []}
    out = judge(d, hist)
    out_json = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--json" else "options_output.json"
    with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print("期权监控分析完成 | %s | 方向=%s 结构=%s 强度=%s 结论=%s" % (
        out["level"], out["labels"]["direction"], out["labels"]["structure"],
        out["labels"]["strength"], out["labels"]["verdict"]))
    for r in out["rules"]:
        print("  %s %s" % (r["rule"], r["detail"]))
    for c in out["sync_checks"]:
        print("  同步 %s" % c)
    for n in out["notes"]:
        print("  ⚠ %s" % n)
    # R5 命中 → 邮件提醒；R6 标红标星
    r5 = next((r for r in out["rules"] if r.get("rule") == "R5"), {})
    if r5.get("hit"):
        lab = out["labels"]
        body = "【美债ETF期权 OI 异动提醒】%s\n方向=%s｜结构=%s｜强度=%s｜结论=%s\n%s\n%s" % (
            out["run_date"], lab["direction"], lab["structure"],
            lab["strength"], lab["verdict"], r5.get("detail", ""),
            "\n".join(out["sync_checks"]))
        send_email_alert("美债期权 OI 异动（2σ/80分位）", body)
    for r in out["rules"]:
        if r.get("star"):
            print("  ★ %s 标红标星：%s" % (r["rule"], r["detail"]))
            break
    print("JSON 已落盘: %s" % out_json)


if __name__ == "__main__":
    main()