#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美债信用利差预警（ICE BofA 三档 OAS 两两缺口）。

监控 ICE BofA 期权调整利差（OAS）的 AA / BB / CCC 及以下三档，
计算三对两两缺口，默认按"走阔方向"（低评级利差 - 高评级利差）：
    BB - AA      （AA 级 vs BB 级）
    CCC - BB     （BB 级 vs CCC 及以下）
    CCC - AA     （AA 级 vs CCC 及以下）
任一缺口的当前值达到过去一年（252 个交易日）80% 分位及以上即触发预警，
触发时发送邮件（主通道：钉钉企业邮箱 dws；回退：163 SMTP）。

数据源：FRED（主：官方 API api.stlouisfed.org；回退：无 key CSV fredgraph.csv）
    AA  : BAMLC0A2CAA
    BB  : BAMLH0A1HYBB
    CCC : BAMLH0A3HYC
API key 只从 config/secrets.json 的 fred.api_key（或 FRED_API_KEY 环境变量）读取。

用法：
    python3 credit_spread_alert.py                         # 计算并落盘 JSON（不发送）
    python3 credit_spread_alert.py --send                  # 触发时发送邮件
    python3 credit_spread_alert.py --json out.json --send  # 指定 JSON 路径
    python3 credit_spread_alert.py --direction signed      # 切换为带符号差值口径
    python3 credit_spread_alert.py --dry-run               # 仅计算，明确不发送

邮件凭据不写入本文件：统一从 config/secrets.json 读取。
"""
import argparse
import bisect
import json
import os
import sqlite3
import re
import smtplib
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

# ---------------- 监控对象 ----------------
SERIES = {"AA": "BAMLC0A2CAA", "BB": "BAMLH0A1HYBB", "CCC": "BAMLH0A3HYC"}

# 统一口径：信用利差一律优先取 data_hub EDB（与环流/中线日报同源），
# FRED 直连只作降级。2026-11 实测本机到 fred.stlouisfed.org 全面超时，
# 只走 FRED 会让信用利差预警整块失败。
EDB_OAS = {"BAMLC0A2CAA": "CREDIT.US.OAS.AA",
           "BAMLH0A1HYBB": "CREDIT.US.OAS.BB",
           "BAMLH0A3HYC": "CREDIT.US.OAS.CCC"}


def _edb_db_path():
    env = (os.environ.get("EDB_REPO") or "").strip()
    cands = []
    if env:
        cands.append(env)
    cands += [
        "/opt/coze/.fuse_drive/dataHub_三级体系",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "dataHub_三级体系"),
    ]
    for repo in cands:
        p = os.path.join(repo, "data_hub", "edb", "data", "edb.sqlite3")
        if os.path.exists(p):
            return p
    return None


def _edb_series(series_id, min_obs=30):
    """从 data_hub EDB 取整条序列 [(date, value)]，不足 min_obs 返回 None。"""
    db = _edb_db_path()
    if not db:
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
        try:
            rows = con.execute(
                "SELECT date, value FROM edb_points WHERE series_id=? "
                "AND value IS NOT NULL ORDER BY date", (series_id,)).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None
    out = [(str(r[0]), float(r[1])) for r in rows if r[1] is not None]
    return out if len(out) >= min_obs else None
RATING_LABEL = {"AA": "AA 级", "BB": "BB 级", "CCC": "CCC 及以下"}
RATING_RANK = {"AA": 0, "BB": 1, "CCC": 2}   # 越大 = 信用越低
# 三对两两利差，按用户命名顺序（前 - 后）
PAIRS = [("BB", "AA"), ("BB", "CCC"), ("AA", "CCC")]
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s"
FRED_API = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id=%s&api_key=%s&file_type=json&sort_order=desc&limit=400")

WINDOW = 252          # 一年窗口（交易日）
THRESHOLD = 0.80      # 80% 分位阈值
DEFAULT_OUT = os.path.join(HERE, "credit_spread_output.json")

# ---------------- 邮件通道（复用环境既有配置） ----------------
DWS_CLI = os.path.expanduser("~/.local/bin/dws")
EMAIL_FROM = app_secrets.get("email.sender", "")
EMAIL_TO = app_secrets.get("email.recipients", "")
SMTP_HOST = app_secrets.get("email.smtp_host", "smtp.163.com")
SMTP_USER = app_secrets.get("email.username", "")


# ---------------- 取数 ----------------
def get_fred_key():
    """FRED API key：只从 config/secrets.json 的 fred.api_key（或 FRED_API_KEY 环境变量）取。"""
    return app_secrets.get("fred.api_key", "")


def _http_text(url, tries=3, timeout=30):
    """curl 优先（与现有数据层一致），失败回退 urllib。返回文本。"""
    last = None
    for i in range(tries):
        try:
            out = subprocess.check_output(
                ["curl", "-s", "--max-time", str(timeout), "-A", "Mozilla/5.0", url],
                stderr=subprocess.DEVNULL, timeout=timeout + 10)
            if out.strip():
                return out.decode("utf-8", errors="replace")
            last = "空响应"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(2 + i * 2)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        if text.strip():
            return text
    except Exception as exc:  # noqa: BLE001
        last = "%s | urllib: %s" % (last, exc)
    raise RuntimeError("抓取失败 %s: %s" % (url.split("?")[0], last))


def fetch_series(series_id):
    """返回 (按日期升序的 [(date, value)], 通道名)。

    主通道：FRED 官方 API（需 key，稳定）；回退：fredgraph.csv（无 key）。
    """
    edb_id = EDB_OAS.get(series_id)
    if edb_id:
        edb = _edb_series(edb_id)
        if edb:
            return edb, "data_hub EDB %s" % edb_id
    key = get_fred_key()
    if key:
        try:
            data = json.loads(_http_text(FRED_API % (series_id, key)))
            obs = [(o["date"], float(o["value"]))
                   for o in data.get("observations", [])
                   if str(o.get("value")) not in (".", "", "None")]
            if len(obs) >= 60:
                return sorted(obs), "FRED API"
        except Exception:  # noqa: BLE001
            pass
    text = _http_text(FRED_CSV % series_id)
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) >= 2 and parts[1].strip() not in (".", ""):
            try:
                rows.append((parts[0].strip(), float(parts[1])))
            except ValueError:
                continue
    if not rows:
        raise RuntimeError("FRED %s 无有效观测" % series_id)
    return sorted(rows), "fredgraph.csv"


def pctile_rank(sorted_vals, value):
    """value 在 sorted_vals 中的分位（<= 计数比例），返回 [0,1]。"""
    return bisect.bisect_right(sorted_vals, value) / len(sorted_vals)


# ---------------- 计算 ----------------
def compute_alerts(direction="widening", window=WINDOW, threshold=THRESHOLD):
    raw, channels = {}, {}
    for k, sid in SERIES.items():
        rows, channel = fetch_series(sid)
        raw[k] = dict(rows)
        channels[k] = channel
    dates = sorted(set(raw["AA"]) & set(raw["BB"]) & set(raw["CCC"]))
    if len(dates) < 60:
        raise RuntimeError("三档 OAS 共同交易日不足（%d 天）" % len(dates))
    win = dates[-window:] if len(dates) > window else dates

    latest = {k: raw[k][win[-1]] for k in SERIES}
    items = []
    for a, b in PAIRS:
        weak, strong = (a, b) if RATING_RANK[a] > RATING_RANK[b] else (b, a)
        if direction == "signed":
            src, dst = a, b
        else:  # widening：低评级 - 高评级
            src, dst = weak, strong
        seq = [raw[src][d] - raw[dst][d] for d in win]
        cur = seq[-1]
        sg = sorted(seq)
        pr = pctile_rank(sg, cur)
        p80 = sg[int(threshold * (len(sg) - 1))]
        items.append({
            "pair": "%s-%s" % (a, b),
            "from": src, "to": dst,
            "from_label": RATING_LABEL[src], "to_label": RATING_LABEL[dst],
            "value": round(cur, 4),
            "value_bp": round(cur * 100, 1),
            "signed_value": round(raw[a][win[-1]] - raw[b][win[-1]], 4),
            "pctile": round(pr, 4),
            "p80": round(p80, 4),
            "p80_bp": round(p80 * 100, 1),
            "win_min": round(min(seq), 4),
            "win_max": round(max(seq), 4),
            "win_n": len(seq),
            "alert": pr >= threshold,
        })
    triggered = [it["pair"] for it in items if it["alert"]]
    return {
        "run_date": datetime.now().strftime("%Y-%m-%d"),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": win[-1],
        "direction": direction,
        "window": window,
        "threshold_pctile": threshold,
        "latest_oas": {k: latest[k] for k in SERIES},
        "items": items,
        "triggered": bool(triggered),
        "triggered_pairs": triggered,
        "fetch_channel": channels,
        "source": "FRED ICE BofA OAS: " + ", ".join("%s=%s" % (k, v) for k, v in SERIES.items()),
    }


# ---------------- 邮件 ----------------
def get_auth_code():
    """163 授权码：只从 config/secrets.json / 环境变量取。"""
    return app_secrets.get("email.password", "")


def send_via_dws(subject, body):
    """主通道：钉钉企业邮箱 dws CLI。返回 (ok, info)。"""
    try:
        cmd = [DWS_CLI, "mail", "message", "send",
               "--from", EMAIL_FROM, "--to", EMAIL_TO,
               "--subject", subject, "--content", body, "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "").strip()
        if re.search(r'"success"\s*:\s*(true|"true")', out):
            return True, "dws 主通道发送成功 -> %s" % EMAIL_TO
        return False, "dws 返回异常: %s" % ((proc.stderr or out).strip()[:200] or "空响应")
    except FileNotFoundError:
        return False, "dws CLI 不存在: %s" % DWS_CLI
    except Exception as exc:  # noqa: BLE001
        return False, "dws 调用失败: %s" % str(exc)[:200]


def send_via_smtp(subject, body):
    """回退通道：163 SMTP（465 SSL 优先，25 STARTTLS 兜底）。返回 (ok, info)。"""
    code = get_auth_code()
    if not code:
        return False, "未取到 163 授权码，回退通道不可用"
    last = ""
    for port, use_ssl in ((465, True), (25, False)):
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = str(Header(subject, "utf-8"))
            msg["From"] = formataddr((str(Header("DuMate 美债信用利差预警", "utf-8")), SMTP_USER))
            msg["To"] = EMAIL_TO
            if use_ssl:
                server = smtplib.SMTP_SSL(SMTP_HOST, port, timeout=30)
            else:
                server = smtplib.SMTP(SMTP_HOST, port, timeout=30)
                server.starttls()
            server.login(SMTP_USER, code)
            server.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())
            server.quit()
            return True, "163 SMTP(%d) 回退发送成功 -> %s" % (port, EMAIL_TO)
        except Exception as exc:  # noqa: BLE001
            last = "163 SMTP(%d) 失败: %s" % (port, str(exc)[:160])
    return False, last


def build_subject(result):
    n = len(result["triggered_pairs"])
    return "【信用利差预警】%d 项利差达一年 80%% 分位 | 数据截止 %s" % (n, result["asof"])


def build_body(result):
    L = []
    L.append("美债信用利差预警（ICE BofA OAS 三档两两缺口）")
    L.append("数据截止：%s" % result["asof"])
    L.append("判定口径：%s，窗口 %d 交易日，阈值 %.0f%% 分位"
             % ("走阔方向（低评级 - 高评级）" if result["direction"] == "widening" else "带符号差值",
                result["window"], result["threshold_pctile"] * 100))
    L.append("=" * 56)
    L.append("")
    L.append("【触发】以下 %d 项达到一年 80%% 分位及以上：" % len(result["triggered_pairs"]))
    for it in result["items"]:
        if it["alert"]:
            L.append("  ● %-7s 现值 %8.1f bp | 一年分位 %5.1f%% | 一年80%%线 %8.1f bp"
                     % (it["pair"], it["value_bp"], it["pctile"] * 100, it["p80_bp"]))
    L.append("")
    L.append("【全部监控项】")
    L.append("  利差对     现值(bp)   一年分位   一年80%线(bp)   状态")
    for it in result["items"]:
        L.append("  %-8s %9.1f  %8.1f%%  %12.1f   %s"
                 % (it["pair"], it["value_bp"], it["pctile"] * 100, it["p80_bp"],
                    "预警" if it["alert"] else "正常"))
    L.append("")
    L.append("【各档 OAS 现值】")
    for k in ("AA", "BB", "CCC"):
        L.append("  %-12s %6.2f%%" % (RATING_LABEL[k], result["latest_oas"][k]))
    L.append("")
    L.append("数据源：FRED ICE BofA 各评级 OAS（AA=BAMLC0A2CAA / BB=BAMLH0A1HYBB / CCC=BAMLH0A3HYC）")
    L.append("说明：缺口按『低评级 OAS - 高评级 OAS』衡量，数值越大代表对应档位间信用利差越阔、信用风险越高。")
    L.append("本邮件由美债监控周报链路自动生成，仅供参考，不构成投资建议。")
    return "\n".join(L)


def send_alert_email(result):
    if not (EMAIL_FROM and EMAIL_TO):
        print("[邮件] 未配置 email.sender/email.recipients（见 config/secrets.json），跳过发送")
        return (False, "未配置 email.sender/email.recipients")
    subject = build_subject(result)
    body = build_body(result)
    ok, info = send_via_dws(subject, body)
    if ok:
        return True, info
    print("  [邮件] 主通道失败：%s，尝试 163 SMTP 回退..." % info)
    ok2, info2 = send_via_smtp(subject, body)
    if ok2:
        return True, info2
    return False, "邮件发送失败（dws 主通道与 163 回退均不可用）：%s | %s" % (info, info2)


# ---------------- 入口 ----------------
def main():
    parser = argparse.ArgumentParser(description="美债信用利差预警（ICE BofA OAS 三档两两缺口）")
    parser.add_argument("--json", default=DEFAULT_OUT, help="输出 JSON 路径")
    parser.add_argument("--send", action="store_true", help="触发时发送邮件")
    parser.add_argument("--dry-run", action="store_true", help="仅计算，明确不发送（覆盖 --send）")
    parser.add_argument("--direction", choices=["widening", "signed"], default="widening",
                        help="缺口方向：widening=低评级-高评级（默认）；signed=按命名顺序带符号差值")
    parser.add_argument("--threshold", type=float, default=THRESHOLD,
                        help="触发分位阈值，默认 0.80（80%% 分位）")
    args = parser.parse_args()

    try:
        result = compute_alerts(direction=args.direction, threshold=args.threshold)
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] 信用利差数据获取/计算失败: %s" % exc, file=sys.stderr)
        sys.exit(2)

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("美债信用利差预警（OAS 三档两两缺口）  数据截止 %s" % result["asof"])
    print("=" * 60)
    for it in result["items"]:
        print("  %-7s 现值 %8.1f bp | 一年分位 %5.1f%% | 一年80%%线 %8.1f bp | %s"
              % (it["pair"], it["value_bp"], it["pctile"] * 100, it["p80_bp"],
                 "预警" if it["alert"] else "正常"))
    print("JSON 已落盘: %s" % args.json)

    if result["triggered"]:
        print("[预警] %d 项达一年 80%% 分位: %s" % (len(result["triggered_pairs"]),
                                                    ", ".join(result["triggered_pairs"])))
        if args.send and not args.dry_run:
            ok, info = send_alert_email(result)
            print("  [邮件] %s" % info)
            if not ok:
                sys.exit(3)
        else:
            print("  [邮件] 已触发但未发送（%s）" % ("dry-run" if args.dry_run else "未开启 --send"))
    else:
        print("[正常] 三对利差均未达一年 80% 分位，不发送邮件")


if __name__ == "__main__":
    main()