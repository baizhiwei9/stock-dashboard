#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kiley_analysis · 取数 + 计算 + 出报告

产出：
  data/termprem.csv       原始（按天落盘，便于回溯）
  data/fcig.json          原始
  data/mps_payload.json   政策预期页的 data-payload（已抽出的 JSON）
  data/kiley_raw.json     本次用到的关键原始值（审计用）
  out/kiley_points.json   指标点（emit 阶段读它）
  out/report.md           三块说明（含方法/口径/局限）

设计原则（照 Kiley 站点的做法）：
  1. 同一量给多个口径并列，不合成一个「官方答案」；
  2. 每个数带 obs_date 与来源，算不出来写 missing，不用旧值顶替；
  3. 口径与局限写进报告，不藏在代码里。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")

BASE = "https://michaeltkiley.github.io"
SRC_TERMPREM = BASE + "/termprem/termprem.csv"
SRC_FCIG = BASE + "/fcig/data.json"
SRC_MPS = BASE + "/monetary_policy_surprises/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; stock-dashboard kiley_analysis)"}


def log(msg: str) -> None:
    print("[kiley] %s" % msg, flush=True)


def get(url: str, timeout: int = 40, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                                    # noqa: BLE001
            last = e
            log("取数失败(%d/%d) %s: %s" % (i + 1, tries, url, e))
    raise RuntimeError("取数失败: %s (%s)" % (url, last))


def month_add(d: dt.date, n: int) -> dt.date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.date(y, m, day)


def point(key, name, value, obs, unit="", dec=2, quality="ok", note=""):
    p = {"key": key, "sec": "KILEY", "name": name, "obs_date": obs,
         "quality": quality}
    if value is None:
        p["quality"] = "missing"
        p["value_text"] = "NA"
    elif isinstance(value, (int, float)):
        p["value"] = round(float(value), 6)
        p["unit"] = unit
        p["dec"] = dec
    else:
        p["value_text"] = str(value)
    if note:
        p["note"] = note
    return p


# ── 块一：期限溢价与长期锚 ───────────────────────────────────────
def block_termprem(points, raw):
    b = get(SRC_TERMPREM).decode("utf-8", "replace")
    with open(os.path.join(DATA, "termprem.csv"), "w", encoding="utf-8") as f:
        f.write(b)
    rows = list(csv.DictReader(io.StringIO(b)))
    rows = [r for r in rows if r.get("date")]
    last = rows[-1]

    def f(k):
        v = (last.get(k) or "").strip()
        try:
            return float(v)
        except ValueError:
            return None

    obs = last["date"]
    y10 = f("yield_10y")
    tp_d = f("tp10_discounted_var")
    tp_a = f("tp10_acm")
    tp_k = f("tp10_kim_wright")
    tp_r = f("tp10_recursive_var")
    lr_d = f("longrun_level_discounted_var")
    lr_r = f("longrun_level_recursive_var")
    sep = f("sep_longrun_fed_funds")
    exp_short = (y10 - tp_a) if (y10 is not None and tp_a is not None) else None
    gap = (exp_short - sep) if (exp_short is not None and sep is not None) else None

    points += [
        point("kiley.y10", "美债10Y", y10, obs, "%", 2),
        point("kiley.tp.discounted", "期限溢价·贴现VAR(Kiley2024)", tp_d, obs, "%", 2,
              note="ρ=0.99722，N_eff≈360 个月"),
        point("kiley.tp.acm", "期限溢价·ACM", tp_a, obs, "%", 2),
        point("kiley.tp.kim_wright", "期限溢价·Kim-Wright", tp_k, obs, "%", 2),
        point("kiley.tp.recursive", "期限溢价·递归VAR", tp_r, obs, "%", 2),
        point("kiley.exp_short", "10Y隐含预期短端均值", exp_short, obs, "%", 2,
              note="10Y − ACM 期限溢价"),
        point("kiley.sep_longrun", "SEP长期联邦基金利率", sep, obs, "%", 1),
        point("kiley.longrun.discounted", "长期利率锚·贴现VAR", lr_d, obs, "%", 2),
        point("kiley.longrun.recursive", "长期利率锚·递归VAR", lr_r, obs, "%", 2),
        point("kiley.gap_vs_sep", "预期短端均值−SEP", gap, obs, "pp", 2,
              note="正值=市场定价的短端均值高于联储长期中枢(higher-for-longer)"),
    ]
    raw["termprem"] = {"obs": obs, "n_rows": len(rows), "y10": y10, "tp_acm": tp_a,
                       "tp_discounted": tp_d, "tp_kim_wright": tp_k,
                       "tp_recursive": tp_r, "longrun_discounted": lr_d,
                       "longrun_recursive": lr_r, "sep_longrun": sep,
                       "expected_short": exp_short, "gap_vs_sep": gap}
    return obs


# ── 块二：政策预期路径 ──────────────────────────────────────────
def block_policy(points, raw):
    html = get(SRC_MPS).decode("utf-8", "replace")
    m = re.search(r'<script[^>]*id=["\']data-payload["\'][^>]*>(.*?)</script>', html, re.S)
    if not m:
        log("政策预期页没找到 data-payload，这块记 missing")
        points += [point("kiley.policy.mode_3m", "3M合约最可能落点", None, "", "%", 3)]
        raw["policy"] = {"error": "no data-payload"}
        return None
    D = json.loads(m.group(1))
    with open(os.path.join(DATA, "mps_payload.json"), "w", encoding="utf-8") as f:
        json.dump(D, f, ensure_ascii=False)
    te = D.get("tab_expectations") or {}
    obs = str(te.get("current_date") or D.get("as_of") or "")
    cur = te.get("ff_path_current") or []
    prv = te.get("ff_path_previous") or []
    prev_date = str(te.get("previous_date") or "")

    def mode_of(pdf):
        pdf = pdf or {}
        rs, ps = pdf.get("rates") or [], pdf.get("probs") or []
        if not rs or len(rs) != len(ps):
            return None, None
        i = max(range(len(ps)), key=lambda j: ps[j])
        return rs[i], ps[i]

    r3, p3 = mode_of(te.get("pdf_3mo_current"))
    rd, pd_ = mode_of(te.get("pdf_dec_ny_current"))

    def at_12m(path, base_date):
        if not path:
            return None
        try:
            base = dt.date.fromisoformat(base_date)
        except ValueError:
            return None
        tgt = month_add(base, 12)
        hit = None
        for item in path:
            s = str(item.get("start") or "")[:10]
            try:
                sd = dt.date.fromisoformat(s[:10])
            except ValueError:
                continue
            if sd.toordinal() <= tgt.toordinal():
                hit = item
            else:
                break
        if hit is None:
            hit = path[0]
        return hit.get("rate")

    p_cur = at_12m(cur, obs) if obs else None
    p_prv = at_12m(prv, prev_date) if prev_date else None
    chg = None
    if p_cur is not None and p_prv is not None:
        chg = (p_cur - p_prv) * 100.0                              # bp

    points += [
        point("kiley.policy.contract", "近月期货合约", te.get("contract_3mo") or "NA", obs),
        point("kiley.policy.mode_3m", "3M合约最可能落点", r3, obs, "%", 3,
              note="SOFR 期权风险中性密度峰值（Carlson-Craig-Melick）"),
        point("kiley.policy.mode_3m_prob", "3M合约峰值概率", p3, obs, "%", 1),
        point("kiley.policy.mode_dec", "DEC27最可能落点", rd, obs, "%", 3),
        point("kiley.policy.mode_dec_prob", "DEC27峰值概率", pd_, obs, "%", 1),
        point("kiley.policy.path_12m", "期货隐含12个月后利率", p_cur, obs, "%", 3,
              note="联邦基金期货曲线，距当前 12 个月"),
        point("kiley.policy.path_12m_chg", "较上次定价变化", chg, obs, "bp", 1,
              note="对比定价日 %s" % (prev_date or "NA")),
    ]
    raw["policy"] = {"obs": obs, "previous_date": prev_date,
                     "contract_3mo": te.get("contract_3mo"),
                     "contract_dec_ny": te.get("contract_dec_ny"),
                     "mode_3m": r3, "mode_3m_prob": p3,
                     "mode_dec": rd, "mode_dec_prob": pd_,
                     "path_12m_current": p_cur, "path_12m_previous": p_prv,
                     "path_12m_chg_bp": chg, "n_path_current": len(cur)}
    return obs


# ── 块三：FCI-G 金融条件 ────────────────────────────────────────
def block_fcig(points, raw):
    d = json.loads(get(SRC_FCIG).decode("utf-8", "replace"))
    with open(os.path.join(DATA, "fcig.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    daily = d.get("daily") or []
    if not daily:
        points += [point("kiley.fcig", "FCI-G金融条件", None, "", "pp", 2)]
        raw["fcig"] = {"error": "empty daily"}
        return None
    # 列序（页面 NAMES/KEYS）：0=date，1=FCI-G total，其后为分项；这里只取总指数
    obs = str(daily[-1][0])
    series = [(str(r[0]), float(r[1])) for r in daily
              if len(r) > 1 and isinstance(r[1], (int, float))]
    cur = series[-1][1]

    def chg(days):
        tgt = dt.date.fromisoformat(obs) - dt.timedelta(days=days)
        hit = None
        for dstr, v in series:
            if dt.date.fromisoformat(dstr) <= tgt:
                hit = v
            else:
                break
        return None if hit is None else (cur - hit)

    c1m, c3m = chg(30), chg(91)
    points += [
        point("kiley.fcig", "FCI-G金融条件", cur, obs, "pp", 2,
              note="正值=对增长构成拖累，负值=助推"),
        point("kiley.fcig.chg1m", "FCI-G近1月变化", c1m, obs, "pp", 2),
        point("kiley.fcig.chg3m", "FCI-G近3月变化", c3m, obs, "pp", 2),
    ]
    raw["fcig"] = {"obs": obs, "level": cur, "chg_1m": c1m, "chg_3m": c3m,
                   "asof": d.get("asof"), "generated": d.get("generated"),
                   "n_daily": len(series)}
    return obs


def fmt(v, dec=2, unit=""):
    if v is None:
        return "NA"
    return ("%." + str(dec) + "f") % v + unit


def write_report(raw) -> None:
    tp = raw.get("termprem") or {}
    po = raw.get("policy") or {}
    fc = raw.get("fcig") or {}
    y10, tp_a, tp_d, sep = tp.get("y10"), tp.get("tp_acm"), tp.get("tp_discounted"), tp.get("sep_longrun")
    exp_s, gap = tp.get("expected_short"), tp.get("gap_vs_sep")
    lines = []
    lines.append("# Kiley数据分析\n")
    lines.append("数据源：michaeltkiley.github.io（Michael T. Kiley，美联储经济学家）公开发布物。"
                 "本页只搬数值、并列口径，不给方向判断。\n")

    lines.append("## 一、10Y 拆解：预期短端 + 期限溢价\n")
    lines.append("| 项 | 值 | 口径 |\n|---|---|---|")
    lines.append("| 美债 10Y | %s | 月度均价 |" % fmt(y10, 2, "%"))
    lines.append("| 期限溢价·贴现VAR | %s | Kiley(2024) 主口径，ρ=0.99722 |" % fmt(tp_d, 2, "%"))
    lines.append("| 期限溢价·ACM | %s | 纽约联储 ACM |" % fmt(tp_a, 2, "%"))
    lines.append("| 期限溢价·Kim-Wright | %s | 联储 FEDS 模型 |" % fmt(tp.get("tp_kim_wright"), 2, "%"))
    lines.append("| 期限溢价·递归VAR | %s | 论文原口径 |" % fmt(tp.get("tp_recursive"), 2, "%"))
    lines.append("| 隐含预期短端均值 | %s | 10Y − ACM 溢价 |" % fmt(exp_s, 2, "%"))
    lines.append("| 长期利率锚（贴现VAR / 递归VAR） | %s / %s | 两个口径并列 |"
                 % (fmt(tp.get("longrun_discounted"), 2, "%"), fmt(tp.get("longrun_recursive"), 2, "%")))
    lines.append("| SEP 长期联邦基金利率 | %s | FOMC 长期中枢 |" % fmt(sep, 1, "%"))
    lines.append("")
    lines.append("读法：10Y 的久期补偿约 %s，剩下约 %s 是市场对未来十年短端均值的预期；"
                 "该预期比 SEP 长期中枢%s %s，即「higher-for-longer」在这个位置上是%s的。\n"
                 % (fmt(tp_a, 2, "pp"), fmt(exp_s, 2, "%"),
                    "高" if (gap or 0) > 0 else "低", fmt(abs(gap), 2, "pp") if gap is not None else "NA",
                    "成立" if (gap or 0) > 0 else "不成立"))

    lines.append("## 二、政策预期路径（期货曲线 + 期权密度）\n")
    lines.append("| 项 | 值 |\n|---|---|")
    lines.append("| 定价日 / 对比日 | %s / %s |" % (po.get("obs") or "NA", po.get("previous_date") or "NA"))
    lines.append("| 近月合约 | %s |" % (po.get("contract_3m") or "NA"))
    lines.append("| 3M 合约最可能落点（峰值概率） | %s（%s） |"
                 % (fmt(po.get("mode_3m"), 3, "%"), fmt(po.get("mode_3m_prob"), 1, "%")))
    lines.append("| DEC27 最可能落点（峰值概率） | %s（%s） |"
                 % (fmt(po.get("mode_dec"), 3, "%"), fmt(po.get("mode_dec_prob"), 1, "%")))
    lines.append("| 期货隐含 12 个月后利率 | %s |" % fmt(po.get("path_12m_current"), 3, "%"))
    lines.append("| 较上次定价 | %s |" % fmt(po.get("path_12m_chg_bp"), 1, "bp"))
    lines.append("")
    lines.append("口径：路径来自联邦基金期货收盘定价；概率密度由 SOFR 期权按风险中性"
                 "（Carlson-Craig-Melick）反解，是「市场愿意付钱的方向」，不是模型预测。\n")

    lines.append("## 三、FCI-G 金融条件\n")
    lines.append("| 项 | 值 |\n|---|---|")
    lines.append("| FCI-G（当期） | %s |" % fmt(fc.get("level"), 2, "pp"))
    lines.append("| 近 1 月变化 | %s |" % fmt(fc.get("chg_1m"), 2, "pp"))
    lines.append("| 近 3 月变化 | %s |" % fmt(fc.get("chg_3m"), 2, "pp"))
    if isinstance(fc.get("asof"), dict):
        lines.append("| 分项数据日期 | %s |" % "、".join(
            "%s %s" % (k, v) for k, v in fc["asof"].items()))
    lines.append("")
    lines.append("读法：正值是对未来一年 GDP 的拖累（条件偏紧），负值是助推；"
                 "FCI-G 由联储按月发布，这里用公开数据每工作日重建，与官方序列存在小幅复制误差。\n")

    lines.append("## 口径与局限（照站点自陈的三段注）\n")
    lines.append("- 期限溢价四个口径互相独立，**分歧本身是信息**，不取平均当作「真值」。")
    lines.append("- 长期锚是一边估计（one-sided），不给误差带；用平滑带做实时比较是错的。")
    lines.append("- 政策路径是期货定价，遇到流动性差的远月合约会有噪声；期权密度只到近月与明年末两个合约。")
    lines.append("- FCI-G 的分项贡献未在此拆解（站点列序未公开标注，避免误标），只给总指数与区间变化。")
    lines.append("- 所有 obs_date 取各自原始序列的最新日期，三个块日期可能不一致，已在点位里分别标注。\n")
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    raw, points = {}, []
    obs_all = []
    for name, fn in (("termprem", block_termprem), ("policy", block_policy), ("fcig", block_fcig)):
        try:
            o = fn(points, raw)
            if o:
                obs_all.append(o)
            log("块 %s 完成，obs=%s" % (name, o))
        except Exception as e:                                    # noqa: BLE001
            log("块 %s 失败：%s" % (name, e))
            raw[name] = {"error": str(e)}
    as_of = max(obs_all) if obs_all else dt.date.today().isoformat()

    with open(os.path.join(DATA, "kiley_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "kiley_points.json"), "w", encoding="utf-8") as f:
        json.dump({"as_of": as_of, "points": points}, f, ensure_ascii=False, indent=2)
    # 框架口径：publish.py 只认 out/latest.json（emit 阶段由 runner 写，这里直接给一份，
    # 保证「单独跑脚本 + publish」也能落库）
    with open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"as_of": as_of, "task": "kiley_analysis", "points": points},
                  f, ensure_ascii=False, indent=2)
    write_report(raw)
    log("点位 %d 条，as_of=%s" % (len(points), as_of))
    return 0


if __name__ == "__main__":
    sys.exit(main())