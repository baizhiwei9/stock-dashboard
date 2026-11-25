# -*- coding: utf-8 -*-
"""70 城房价指数技术分析与预警（一封邮件汇总所有触发项）。

设计要点
--------
1. **统一检查、统一发信**：一次跑完 70 城 × KDJ/MACD 的全部规则，把命中的城市汇总成
   **一封**邮件，绝不按信号逐条发。同一批里同城多条信号合并成一行。
2. **去重**：`out/state.json` 记 ``city|kind|month|code``；同一个月的同一个信号只发一次，
   否则月度数据不变时每周都会重复轰炸。
3. **带图**：只画有信号的城市（最多 6 个），一张 PNG 挂邮件附件。
4. **默认 dry-run**：不连 SMTP、不调用任何外发通道；`--send` 才真发。凭据只从环境变量读
   （``SMTP_HOST/PORT/FROM/TO/USER/PASSWORD``，可选 ``SMTP_SSL/TLS``），不写进仓库。

产物
----
- ``out/signals.json``  全量分析结果 + 本轮新信号
- ``out/signals.md``    人读摘要
- ``out/price_tech.png`` 技术分析图（有信号且成功出图时）

退出码：0 正常（有无信号都算正常）；1 取数/分析失败；3 有信号但邮件没发出去。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
STATE = os.path.join(OUT, "state.json")

try:
    from data_hub import house
except ImportError as exc:  # 部署机没装 datahub 时给明确提示
    print("[FATAL] 无法 import data_hub.house：%s" % exc, file=sys.stderr)
    print("        需先安装 datahub-multi-tier（pip install -e . 或设 PYTHONPATH）",
          file=sys.stderr)
    raise SystemExit(1)

LEVEL_CN = {"bull": "转多", "bear": "转空", "warn": "超买", "watch": "超卖"}
LEVEL_ORDER = {"bear": 0, "warn": 1, "bull": 2, "watch": 3}


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(OUT, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def collect(months=None, kind="old"):
    """跑全城分析，返回 (有信号的全部, 本轮新增)。

    ``months`` 默认 None = 用全部历史算指标（基期固定在该城第一个月，不同窗口才不会漂）。
    """
    all_hits = house.analyze_all(kind=kind, months=months, only_signals=True)
    state = load_state()
    fresh, seen_now = [], {}
    for it in all_hits:
        city, month = it["city"], it["month"]
        for s in it["signals"]:
            key = "%s|%s|%s|%s" % (city, kind, month, s["code"])
            seen_now[key] = s["month"]
            if key in state:
                continue
            fresh.append({"city": city, "month": month, "kind": kind,
                          "level": s["level"], "code": s["code"], "text": s["text"],
                          "detail": {k: v for k, v in s.items()
                                     if k not in ("code", "level", "text", "month")},
                          "kdj": it["kdj"], "macd": it["macd"], "price": it["price"]})
    # 只保留最近 3 个月的已发记录，避免 state 无限长
    keep = {k: v for k, v in state.items() if v >= (seen_now and min(seen_now.values()) or "")}
    keep.update(seen_now)
    return all_hits, fresh, keep


def group_rows(fresh):
    """同城同月多条信号合并成一行。"""
    rows = {}
    for f in fresh:
        k = (f["city"], f["month"])
        r = rows.setdefault(k, {"city": f["city"], "month": f["month"],
                                "level": f["level"], "items": [],
                                "kdj": f["kdj"], "macd": f["macd"], "price": f["price"]})
        if LEVEL_ORDER.get(f["level"], 9) < LEVEL_ORDER.get(r["level"], 9):
            r["level"] = f["level"]
        r["items"].append(f["text"])
    return sorted(rows.values(), key=lambda x: (LEVEL_ORDER.get(x["level"], 9), x["city"]))


def build_body(rows, all_hits, asof):
    bulls = [r for r in rows if r["level"] == "bull"]
    bears = [r for r in rows if r["level"] == "bear"]
    warn = [r for r in rows if r["level"] == "warn"]
    watch = [r for r in rows if r["level"] == "watch"]

    subject = "【房价指数技术预警】%s 新增 %d 城｜转多 %d 转空 %d 超买 %d 超卖 %d" % (
        asof, len(rows), len(bulls), len(bears), len(warn), len(watch))

    lines = ["# 房价指数技术分析与预警（%s）" % asof, "",
             "- 口径：70 城房价指数（月度，二手住宅环比连乘还原的合成价格，**不是元/㎡**）",
             "- 指标：KDJ(9,3,3)、MACD(12,26,9)、20 月均线（中轨）",
             "- 规则：MACD 零轴穿越 / KDJ 50 中轴穿越 / 价格穿 MA20 → 中枢突破；KDJ K>=80 或 J>=100 超买、K<=20 或 J<=0 超卖",
             "- 本轮全城扫描 %d 城有信号，其中**新增 %d 城**（同城同月同信号只报一次）" % (len(all_hits), len(rows)),
             ""]
    if not rows:
        lines += ["本轮没有新增信号（月度数据未更新，或信号已在之前报过）。"]
    else:
        lines += ["| 城市 | 月份 | 方向 | 触发 | K | D | J | DIF | DEA |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append("| %s | %s | %s | %s | %.1f | %.1f | %.1f | %.2f | %.2f |" % (
                r["city"], r["month"], LEVEL_CN.get(r["level"], r["level"]),
                "；".join(r["items"]), r["kdj"]["k"], r["kdj"]["d"], r["kdj"]["j"],
                r["macd"]["dif"], r["macd"]["dea"]))
        lines += ["", "图见附件（只画有信号的城市，最多 6 个）。"]
    lines += ["", "---", "由 data_hub.house 生成｜统一检查、统一发信"]
    return subject, "\n".join(lines)


def html_from_md(md):
    """极简 Markdown → HTML（只处理标题、列表、表格），够邮件用。"""
    import html as _html
    out, in_tbl = [], False
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if set("".join(cells)) <= {"-", " "}:
                continue
            if not in_tbl:
                out.append('<table border="1" cellspacing="0" cellpadding="5" '
                           'style="border-collapse:collapse;font-size:13px">')
                in_tbl = True
                out.append("<tr>" + "".join(
                    "<th>%s</th>" % _html.escape(c) for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(
                    "<td>%s</td>" % _html.escape(c) for c in cells) + "</tr>")
            continue
        if in_tbl:
            out.append("</table>")
            in_tbl = False
        if not s:
            continue
        if s.startswith("# "):
            out.append("<h3>%s</h3>" % _html.escape(s[2:]))
        elif s.startswith("- "):
            out.append("<li>%s</li>" % _html.escape(s[2:]))
        elif s == "---":
            out.append("<hr>")
        else:
            out.append("<p>%s</p>" % _html.escape(s))
    if in_tbl:
        out.append("</table>")
    return '<div style="font-family:Helvetica,Arial,sans-serif;font-size:14px">%s</div>' % "".join(out)


def send_mail(subject, text, html_body, attachments, dry_run=True):
    """发一封带附件的邮件。凭据只从环境变量读；缺配置就报清楚缺什么。"""
    conf = {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT") or 465),
        "from": os.environ.get("EMAIL_FROM", ""),
        "to": [x.strip() for x in (os.environ.get("EMAIL_TO", "") or "").split(",") if x.strip()],
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_ssl": (os.environ.get("SMTP_SSL", "1") not in ("0", "false", "False")),
        "use_tls": (os.environ.get("SMTP_TLS", "") in ("1", "true", "True")),
    }
    preview = {"sent": False, "dry_run": True, "subject": subject,
               "to": conf["to"], "attachments": [os.path.basename(a) for a in attachments],
               "bytes": len(text)}
    if dry_run:
        return preview

    missing = [k for k in ("host", "from") if not conf[k]] + ([] if conf["to"] else ["to"])
    if missing:
        raise RuntimeError("邮件通道未配置：缺 %s（设 SMTP_HOST/EMAIL_FROM/EMAIL_TO，"
                           "账号密码用 SMTP_USER/SMTP_PASSWORD）" % ", ".join(missing))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = conf["from"]
    msg["To"] = ", ".join(conf["to"])
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    for path in attachments or []:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype="image", subtype="png",
                           filename=os.path.basename(path))

    if conf["use_ssl"]:
        server = smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=30,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(conf["host"], conf["port"], timeout=30)
    try:
        if conf["use_tls"] and not conf["use_ssl"]:
            server.starttls(context=ssl.create_default_context())
        if conf["user"]:
            server.login(conf["user"], conf["password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return {"sent": True, "dry_run": False, "subject": subject, "to": conf["to"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="70 城房价指数技术分析与预警（一封邮件汇总）")
    ap.add_argument("--no-fetch", action="store_true", help="不抓新数据，直接用库里现有的")
    ap.add_argument("--months", type=int, default=0,
                    help="指标窗口（月），0=用全部历史（默认，基期才不漂）")
    ap.add_argument("--kind", default="old", choices=("old", "new"), help="old=二手住宅（默认）")
    ap.add_argument("--send", action="store_true", help="真发邮件（默认 dry-run）")
    ap.add_argument("--force", action="store_true", help="忽略去重状态，把所有信号都当新的")
    ap.add_argument("--max-panels", type=int, default=6, help="图上最多几个城市")
    args = ap.parse_args(argv)

    os.makedirs(OUT, exist_ok=True)
    asof = dt.datetime.now().strftime("%Y-%m-%d")

    fetch_info = {"skipped": True}
    if not args.no_fetch:
        try:
            fetch_info = house.fetch_price(verbose=True)
        except Exception as exc:
            print("[WARN] 抓取失败，改用库里现有数据：%s: %s" % (type(exc).__name__, exc),
                  file=sys.stderr)
            fetch_info = {"skipped": False, "error": str(exc)}

    all_hits, fresh, keep = collect(months=args.months, kind=args.kind)
    if args.force:
        fresh = [{"city": it["city"], "month": it["month"], "kind": args.kind,
                  "level": s["level"], "code": s["code"], "text": s["text"],
                  "detail": {k: v for k, v in s.items() if k not in ("code", "level", "text", "month")},
                  "kdj": it["kdj"], "macd": it["macd"], "price": it["price"]}
                 for it in all_hits for s in it["signals"]]

    rows = group_rows(fresh)
    subject, md = build_body(rows, all_hits, asof)

    png = ""
    if rows:
        try:
            pick = [it for it in all_hits
                    if it["city"] in {r["city"] for r in rows}]
            png = house.chart(pick, os.path.join(OUT, "price_tech.png"),
                              max_panels=args.max_panels,
                              title="房价指数技术分析 %s（%s）" % (asof, "二手住宅" if args.kind == "old" else "新建商品住宅"))
        except Exception as exc:
            print("[WARN] 出图失败（邮件仍发，但不带图）：%s: %s"
                  % (type(exc).__name__, exc), file=sys.stderr)

    with open(os.path.join(OUT, "signals.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": asof, "fetch": fetch_info, "total_with_signals": len(all_hits),
                   "fresh": fresh, "rows": rows,
                   "detail": [{"city": it["city"], "month": it["month"],
                               "signals": it["signals"], "kdj": it["kdj"],
                               "macd": it["macd"], "price": it["price"]} for it in all_hits]},
                  f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "signals.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")

    print(subject)
    print(md)

    mail = {"sent": False, "reason": "本轮无新增信号，不发空邮件"}
    if rows:
        try:
            mail = send_mail(subject, md, html_from_md(md),
                             [png] if png else [], dry_run=not args.send)
        except Exception as exc:
            print("[ERROR] 邮件发送失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            return 3
        save_state(keep)      # 只有确实发出去（或 dry-run 走完）才记已发，避免漏报
    print(json.dumps({"mail": mail, "png": png, "fresh": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())