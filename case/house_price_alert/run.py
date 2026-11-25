# -*- coding: utf-8 -*-
"""京沪房价指数技术分析与预警（一封邮件汇总）。

只管**北京、上海**两城（用户口径）。70 城数据仍在库（取数不设限），但预警不扫全城——
70 城一起报会变成噪音，两城每月一封才是能看的量。

设计要点
--------
1. **统一检查、统一发信**：一次跑完两城 × KDJ/MACD 全部规则，命中的合并成**一封**邮件。
2. **邮件永远带当前状态**：即使本轮没事件，正文也列两城的「价格 / K / D / J / DIF / DEA / 区间」，
   这样「已经在超买区」这件事不会被漏掉。
3. **区间也算信号**：处于超买/超卖区本身按 ``city|kind|month|zone`` 去重上报——月度数据更新后
   每月最多提醒一次，不会天天刷。
4. **去重**：``out/state.json``；同一月同一信号只发一次。
5. **带图**：两城一张 PNG（三栏：价格+MA20 / KDJ / MACD）挂附件。
6. **默认 dry-run**：凭据只从环境变量读（``SMTP_HOST/PORT/USER/PASSWORD``、``EMAIL_FROM/TO``），
   ``--send`` 才真发。

产物：``out/signals.json`` / ``out/signals.md`` / ``out/price_tech.png`` / ``out/state.json``

退出码：0 正常；1 取数/分析失败；3 有信号但邮件没发出去。
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

# 只盯这两城；要改关注范围改这里或 --cities
DEFAULT_CITIES = tuple(getattr(house, "WATCH_CITIES", ("北京", "上海")))

LEVEL_CN = {"bull": "转多", "bear": "转空", "warn": "超买", "watch": "超卖"}
LEVEL_ORDER = {"bear": 0, "warn": 1, "bull": 2, "watch": 3}
ZONE_SIGNAL = {"超买区": ("zone_overbought", "warn", "处于超买区"),
               "超卖区": ("zone_oversold", "watch", "处于超卖区")}


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


def collect(months=None, kind="old", cities=None):
    """跑关注城市的分析，返回 ``(全部分析, 本轮新增信号, 去重状态)``。

    ``months`` 默认 None = 用全部历史算指标（基期固定在该城第一个月，不同窗口才不会漂）。
    """
    cities = tuple(cities or DEFAULT_CITIES)
    all_items = house.analyze_all(kind=kind, months=months, cities=cities,
                                  only_signals=False)
    state = load_state()
    fresh, seen_now = [], {}

    def push(city, month, level, code, text, extra, it):
        key = "%s|%s|%s|%s" % (city, kind, month, code)
        seen_now[key] = month
        if key in state:
            return
        fresh.append({"city": city, "month": month, "kind": kind, "level": level,
                      "code": code, "text": text, "detail": extra,
                      "kdj": it["kdj"], "macd": it["macd"], "price": it["price"]})

    for it in all_items:
        if not it.get("ok"):
            continue
        city, month = it["city"], it["month"]
        for s in it.get("signals") or []:
            push(city, month, s["level"], s["code"], s["text"],
                 {k: v for k, v in s.items()
                  if k not in ("code", "level", "text", "month")}, it)
        z = ZONE_SIGNAL.get(it.get("zone") or "")
        if z:
            code, level, text = z
            push(city, month, level, code,
                 "%s（K=%.1f J=%.1f）" % (text, it["kdj"]["k"], it["kdj"]["j"]),
                 {"k": it["kdj"]["k"], "j": it["kdj"]["j"]}, it)

    # 只保留近 12 个月的已发记录，state 不无限长
    months_seen = sorted(seen_now.values())
    floor = ""
    if months_seen:
        yy, mm = int(months_seen[0][:4]), int(months_seen[0][5:7])
        mm -= 12
        while mm <= 0:
            mm += 12
            yy -= 1
        floor = "%04d-%02d" % (yy, mm)
    keep = {k: v for k, v in state.items() if v >= floor} if floor else dict(state)
    keep.update(seen_now)
    return all_items, fresh, keep


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


def _status_table(all_items):
    lines = ["| 城市 | 月份 | 合成价格 | K | D | J | DIF | DEA | 区间 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for it in all_items:
        if not it.get("ok"):
            lines.append("| %s | — | 数据不足 | | | | | | |" % it.get("city", "?"))
            continue
        lines.append("| %s | %s | %.2f | %.1f | %.1f | %.1f | %.2f | %.2f | %s |" % (
            it["city"], it["month"], it["price"], it["kdj"]["k"], it["kdj"]["d"],
            it["kdj"]["j"], it["macd"]["dif"], it["macd"]["dea"], it.get("zone") or "—"))
    return lines


def build_body(rows, all_items, asof):
    cnt = {k: len([r for r in rows if r["level"] == k]) for k in ("bull", "bear", "warn", "watch")}
    zones = "／".join("%s %s" % (it["city"], it.get("zone")) for it in all_items
                     if it.get("ok"))
    subject = "【京沪房价指数技术预警】%s｜%s｜新增 %d 条" % (
        asof, zones or "无数据", len(rows))

    lines = ["# 京沪房价指数技术分析与预警（%s）" % asof, "",
             "- 关注城市：%s（70 城数据仍在库，预警只看这两城）" % "、".join(
                 it["city"] for it in all_items if it.get("ok")) or "—",
             "- 口径：房价指数（月度，二手住宅环比连乘还原的合成价格，**不是元/㎡**）",
             "- 指标：KDJ(9,3,3)、MACD(12,26,9)、20 月均线（中轨）",
             "- 规则：MACD 零轴穿越 / KDJ 50 中轴穿越 / 价格穿 MA20 → 中枢突破；"
             "K>=80 或 J>=100 超买、K<=20 或 J<=0 超卖；**处于超买/超卖区按月提醒一次**",
             "", "## 当前状态", ""]
    lines += _status_table(all_items)
    lines += ["", "## 本轮新增信号", ""]
    if not rows:
        lines += ["- 本轮无新增（同月同信号此前已报过，或数据未更新）"]
    else:
        lines += ["- 共 %d 条：转多 %d、转空 %d、超买 %d、超卖 %d" % (
            len(rows), cnt["bull"], cnt["bear"], cnt["warn"], cnt["watch"]), "",
            "| 城市 | 月份 | 方向 | 触发 |", "|---|---|---|---|"]
        for r in rows:
            lines.append("| %s | %s | %s | %s |" % (
                r["city"], r["month"], LEVEL_CN.get(r["level"], r["level"]),
                "；".join(r["items"])))
        lines += ["", "图见附件。"]
    lines += ["", "---", "由 data_hub.house 生成｜统一检查、统一发信"]
    return subject, "\n".join(lines)


def html_from_md(md):
    """极简 Markdown → HTML（标题、列表、表格），够邮件用。"""
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
        if s.startswith("## "):
            out.append("<h4>%s</h4>" % _html.escape(s[3:]))
        elif s.startswith("# "):
            out.append("<h3>%s</h3>" % _html.escape(s[2:]))
        elif s.startswith("- "):
            out.append("<li>%s</li>" % _html.escape(s[2:]))
        elif s == "---":
            out.append("<hr>")
        else:
            out.append("<p>%s</p>" % _html.escape(s))
    if in_tbl:
        out.append("</table>")
    return ('<div style="font-family:Helvetica,Arial,sans-serif;font-size:14px">%s</div>'
            % "".join(out))


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
    ap = argparse.ArgumentParser(description="京沪房价指数技术分析与预警（一封邮件汇总）")
    ap.add_argument("--cities", default=",".join(DEFAULT_CITIES),
                    help="关注城市，逗号分隔（默认 %s）" % ",".join(DEFAULT_CITIES))
    ap.add_argument("--no-fetch", action="store_true", help="不抓新数据，直接用库里现有的")
    ap.add_argument("--months", type=int, default=0,
                    help="指标窗口（月），0=用全部历史（默认，基期才不漂）")
    ap.add_argument("--kind", default="old", choices=("old", "new"), help="old=二手住宅（默认）")
    ap.add_argument("--send", action="store_true", help="真发邮件（默认 dry-run）")
    ap.add_argument("--force", action="store_true", help="忽略去重状态，把所有信号都当新的")
    args = ap.parse_args(argv)

    cities = tuple(c.strip() for c in args.cities.split(",") if c.strip())
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

    all_items, fresh, keep = collect(months=(args.months or None), kind=args.kind,
                                     cities=cities)
    if args.force:
        state_backup = load_state()
        save_state({})
        all_items, fresh, keep = collect(months=(args.months or None), kind=args.kind,
                                         cities=cities)
        save_state(state_backup)

    rows = group_rows(fresh)
    subject, md = build_body(rows, all_items, asof)

    png = ""
    if all_items:
        try:
            png = house.chart(all_items, os.path.join(OUT, "price_tech.png"),
                              max_panels=len(cities),
                              title="%s 房价指数技术分析 %s（%s）" % (
                                  "/".join(cities), asof,
                                  "二手住宅" if args.kind == "old" else "新建商品住宅"))
        except Exception as exc:
            print("[WARN] 出图失败（邮件仍发，但不带图）：%s: %s"
                  % (type(exc).__name__, exc), file=sys.stderr)

    with open(os.path.join(OUT, "signals.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": asof, "cities": list(cities), "fetch": fetch_info,
                   "fresh": fresh, "rows": rows,
                   "detail": [{"city": it["city"], "month": it.get("month"),
                               "zone": it.get("zone"), "signals": it.get("signals"),
                               "kdj": it.get("kdj"), "macd": it.get("macd"),
                               "price": it.get("price")} for it in all_items]},
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
        save_state(keep)
    print(json.dumps({"mail": mail, "png": png, "fresh": len(rows),
                      "cities": list(cities)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())