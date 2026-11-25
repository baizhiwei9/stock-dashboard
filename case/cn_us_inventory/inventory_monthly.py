# -*- coding: utf-8 -*-
"""库存周期月度任务：取数 -> 重算阶段/预测 -> 出报告并推飞书 -> 切换检测并邮件提醒
用法：
  python inventory_monthly.py            # 全流程（取数+报告+飞书+切换检测+邮件）
  python inventory_monthly.py --no-feishu --no-email   # 只取数并重算，不推送
  python inventory_monthly.py --test-email             # 仅自检邮件通道
"""
import argparse
import json
import os
import re
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.utils import formataddr

import pandas as pd
import requests

# 原指向 ses_gffe5e7d65...（空目录，无任何数据）。改为本 case 目录，
# data/ 下的 csv/json 才是实际在用的那一份。
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = BASE + "/data"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 邮件凭据统一从 config/secrets.json 读取，不写明文
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

EMAIL_FROM = app_secrets.get("email.sender", "")
EMAIL_TO = app_secrets.get("email.recipients", "")
DWS_CLI = os.path.expanduser("~/.local/bin/dws")
EMAIL_HOST = app_secrets.get("email.smtp_host", "smtp.163.com")
EMAIL_PORT = app_secrets.get_int("email.smtp_port", 465)
EMAIL_USER = app_secrets.get("email.username", "")

PHASE_COLOR = {"主动补库存": "#1F7A4D", "被动补库存": "#A5561F", "主动去库存": "#B2472F", "被动去库存": "#2C6CA0"}
NEXT = {"主动补库存": "被动补库存", "被动补库存": "主动去库存",
        "主动去库存": "被动去库存", "被动去库存": "主动补库存"}


def phase_of(inv_dir, dem_dir, g):
    """库存动量方向 x 库销增速差：补库侧按库存/需求增速比较区分主动被动。"""
    if inv_dir >= 0:
        return "被动补库存" if g > 0 else "主动补库存"
    return "主动去库存" if dem_dir < 0 else "被动去库存"


def nextm(ym, k):
    y, m = int(ym[:4]), int(ym[4:])
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}{m:02d}"


# ---------------- 取数 ----------------
def fetch_china_hard(pages=8):
    """抓统计局 zxfb 列表最近数页，解析各期「产成品存货同比 / 营业收入累计同比」。"""
    base = "https://www.stats.gov.cn/sj/zxfb/"
    links = {}
    for pg in ["index.html"] + [f"index_{i}.html" for i in range(1, pages)]:
        try:
            r = requests.get(base + pg, headers=H, timeout=25)
            r.encoding = "utf-8"
        except Exception:
            continue
        for href, title in re.findall(r'href="(\./\d+/t\d+_\d+\.html)"[^>]*>([^<]*工业企业利润[^<]*)', r.text):
            links[base + href[2:]] = " ".join(title.split())
    rows = []
    for url, title in links.items():
        m = re.search(r"(\d{4})年1—(\d+)月份", title)
        if not m:
            continue
        try:
            rr = requests.get(url, headers=H, timeout=25)
            rr.encoding = "utf-8"
            t = re.sub(r"[\u3000\s]+", "", re.sub(r"<[^>]+>", "", rr.text))
        except Exception:
            continue
        inv = re.search(r"产成品存货[\d.]+万亿元，(增长|下降)([\d.]+)%", t)
        rev = re.search(r"实现营业收入[\d.]+万亿元，(同比增长|同比下降)([\d.]+)%", t)
        if inv and rev:
            rows.append({"month": f"{m.group(1)}{int(m.group(2)):02d}",
                         "inventory_yoy": float(inv.group(2)) * (1 if inv.group(1) == "增长" else -1),
                         "revenue_cum_yoy": float(rev.group(2)) * (1 if rev.group(1) == "同比增长" else -1)})
    return pd.DataFrame(rows)


def fetch_us():
    def fred(fid):
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fid}", headers=H, timeout=30)
        from io import StringIO
        return pd.read_csv(StringIO(r.text))
    inv = fred("BUSINV").rename(columns={"observation_date": "date", "BUSINV": "inventories"})
    rat = fred("ISRATIO").rename(columns={"observation_date": "date", "ISRATIO": "isratio"})
    us = inv.merge(rat, on="date")
    us["date"] = pd.to_datetime(us["date"])
    us["sales"] = us["inventories"] / us["isratio"]
    us = us.sort_values("date").reset_index(drop=True)
    for c in ["inventories", "sales"]:
        us[c + "_yoy"] = us[c].pct_change(12) * 100
    us = us.dropna(subset=["inventories_yoy", "sales_yoy"])
    us["month"] = us["date"].dt.strftime("%Y%m")
    return us[["month", "inventories", "sales", "inventories_yoy", "sales_yoy"]]


def refresh_data():
    ch = fetch_china_hard()
    os.makedirs(DATA, exist_ok=True)
    # 首跑时历史库可能不在（data/ 空目录不进仓库），用空表起步，本次抓到的直接落盘
    _old_p = DATA + "/china_hard.csv"
    if os.path.exists(_old_p):
        old = pd.read_csv(_old_p, dtype={"month": str}).rename(columns={"month_cum": "month"})
    else:
        old = pd.DataFrame(columns=["month", "inventory_yoy", "revenue_cum_yoy"])
    old["month"] = old["month"].str.replace("-", "")
    allc = pd.concat([old[["month", "inventory_yoy", "revenue_cum_yoy"]], ch]).drop_duplicates("month").sort_values("month")
    allc.to_csv(DATA + "/china_hard.csv", index=False)
    us = fetch_us()
    us.to_csv(DATA + "/us_business.csv", index=False)
    return allc, us


# ---------------- 计算 ----------------
def build_base(raw, dcol, icol):
    raw = raw.copy()
    raw["month"] = raw["month"].astype(str)
    months = pd.period_range(raw.month.iloc[0], raw.month.iloc[-1], freq="M").strftime("%Y%m").astype(str).tolist()
    m = pd.DataFrame({"month": months})
    raw = raw.drop_duplicates("month").set_index("month")
    m["demand"] = m.month.map(raw[dcol]).interpolate()
    m["inventory"] = m.month.map(raw[icol]).interpolate()
    m["dma"] = m.demand.rolling(3).mean()
    m["ima"] = m.inventory.rolling(3).mean()
    m["dsd"] = m.dma.diff(3)
    m["isd"] = m.ima.diff(3)
    m["g"] = (m.inventory - m.demand).round(2)
    m["phase"] = [phase_of(i, d, g) if pd.notna(i) and pd.notna(d) else None for i, d, g in zip(m.isd, m.dsd, m.g)]
    return m.dropna(subset=["dma", "ima"]).reset_index(drop=True)


def forecast(d, key):
    import numpy as np
    def sl(sr, fit=12):
        v = sr.dropna().tail(fit).values
        return 0.0 if len(v) < 3 else float(np.polyfit(np.arange(len(v)), v, 1)[0])
    last = d.iloc[-1]
    out = {}
    for sname, scale in [("base", 1.0), ("conservative", 0.5)]:
        sd, si = sl(d["dma"]) * scale, sl(d["ima"]) * scale
        sr = sl(d["demand"]) * scale
        sri = sl(d["inventory"]) * scale
        ed, ei = list(d["dma"]), list(d["ima"])
        rd, ri = list(d["demand"]), list(d["inventory"])
        for k in range(6):
            ed.append(ed[-1] + sd); ei.append(ei[-1] + si)
            rd.append(round(rd[-1] + sr, 2)); ri.append(round(ri[-1] + sri, 2))
        Sd, Si = pd.Series(ed), pd.Series(ei)
        rows = []
        for k in range(1, 7):
            x = len(ed) - 6 + k - 1
            dd, ii = Sd.diff(3).iloc[x], Si.diff(3).iloc[x]
            g = round(ri[x] - rd[x], 2)
            rows.append({"month": nextm(last["month"], k), "dma": round(ed[x], 2), "ima": round(ei[x], 2),
                         "dsd": round(dd, 2), "isd": round(ii, 2), "g": g, "phase": phase_of(ii, dd, g),
                         "gap_restock": round(max(0, g), 2), "gap_destock": round(max(0, ii) + max(0, dd), 2)})
        sw = next((r for r in rows if r["phase"] != last["phase"]), None)
        out[sname] = {"slope_demand": round(sd, 3), "slope_inv": round(si, 3), "current_month": last["month"],
                      "current_phase": last["phase"], "current_g": float(last["g"]),
                      "cur_gap_restock": round(max(0, last["g"]), 2),
                      "cur_gap_destock": round(max(0, last.isd) + max(0, last.dsd), 2),
                      "switch": ({"month": sw["month"], "to": sw["phase"]} if sw else None), "table": rows}
    return out


def recompute():
    ch = pd.read_csv(DATA + "/china_hard.csv", dtype={"month": str})
    us = pd.read_csv(DATA + "/us_business.csv", dtype={"month": str})
    chb = build_base(ch.rename(columns={"revenue_cum_yoy": "demand", "inventory_yoy": "inventory"}), "demand", "inventory")
    usb = build_base(us[["month", "sales_yoy", "inventories_yoy"]].rename(columns={"sales_yoy": "demand", "inventories_yoy": "inventory"}), "demand", "inventory")
    chb.to_csv(DATA + "/china_hard_base.csv", index=False)
    usb.to_csv(DATA + "/us_base.csv", index=False)
    fc = {"china": forecast(chb, "china"), "us": forecast(usb, "us")}
    json.dump(fc, open(DATA + "/forecast2.json", "w"), ensure_ascii=False, indent=2)
    return chb, usb, fc


# ---------------- 邮件 ----------------
def send_email(subject, body, image=None):
    if not (EMAIL_FROM and EMAIL_TO):
        print("[邮件] 未配置 email.sender/email.recipients（见 config/secrets.json），跳过发送")
        return False
    cmd = [DWS_CLI, "mail", "message", "send", "--from", EMAIL_FROM, "--to", EMAIL_TO,
           "--subject", subject, "--content", body, "--format", "json"]
    if image and os.path.exists(image):
        cmd += ["--inline-attachment", image]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if re.search(r'"success"\s*:\s*(true|"true")', p.stdout, re.I):
            print("[邮件] dws 主通道发送成功（含图）->", EMAIL_TO)
            return True
        print("[邮件] dws 返回异常:", (p.stderr or p.stdout)[:200])
    except Exception as e:
        print("[邮件] dws 调用失败:", str(e)[:150])
    code = app_secrets.get("email.password", "")
    if not code:
        print("[邮件] 无 163 授权码，回退通道不可用")
        return False
    try:
        if image and os.path.exists(image):
            msg = MIMEMultipart("related")
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with open(image, "rb") as fh:
                part = MIMEImage(fh.read())
            part.add_header("Content-ID", "<chart>")
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(image))
            msg.attach(part)
        else:
            msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("DuMate 库存周期监测", "utf-8")), EMAIL_USER))
        msg["To"] = EMAIL_TO
        s = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=30)
        s.login(EMAIL_USER, code)
        s.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
        s.quit()
        print("[邮件] 163 回退发送成功")
        return True
    except Exception as e:
        print("[邮件] 163 回退失败:", str(e)[:150])
        return False


# ---------------- 切换检测 ----------------
def check_switch(chb, usb, fc, force_email=False, chart=None):
    state_path = DATA + "/inv_switch_state.json"
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    alerts = []
    lines = []
    for key, d in (("china", chb), ("us", usb)):
        cur = d["phase"].iloc[-1]
        mon = str(d["month"].iloc[-1])
        nm = "中国" if key == "china" else "美国"
        prev = state.get(key, {}).get("phase")
        state.setdefault(key, {})["phase"] = cur
        state[key]["month"] = mon
        lines.append(f"{nm}：当前周期 {cur}（截至 {mon[:4]}-{mon[4:]}）→ 下一周期 {NEXT.get(cur, '—')}")
        if prev is not None and prev != cur:
            alerts.append(f"{nm}库存周期已切换：{prev} → {cur}（{mon[:4]}-{mon[4:]}）")
    json.dump(state, open(state_path, "w"), ensure_ascii=False, indent=2)
    if force_email:
        alerts.append("（自检触发）库存周期邮件提醒通道测试")
    if alerts:
        img_txt = ""
        if chart and os.path.exists(chart):
            img_txt = "\n\n库存周期图表如下：\n\n[inline:" + os.path.basename(chart) + "]"
        body = ("库存周期监测提醒\n\n"
                + "\n\n".join("- " + a for a in alerts)
                + "\n\n**当前 / 下一周期**\n\n"
                + "\n\n".join(lines)
                + img_txt
                + "\n\n（阈值口径：库存同比动量方向 × 库销增速差；详见《库存周期切换预测报告》）"
                + "\n\n署名：DuMate 库存周期监测")
        send_email("[库存周期监测] 周期阶段已发生变化", body, image=chart)
    else:
        print("[切换检测] 未发生阶段切换，无需发信")
    return alerts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-feishu", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--test-email", action="store_true")
    args = ap.parse_args()
    if args.test_email:
        import render_chart
        render_chart.main(push=False)
        send_email("[库存周期监测] 邮件通道自检",
                   "库存周期邮件提醒通道自检（测试发送）。\n\n"
                   "触发条件：库存周期阶段发生切换的当月或次月自动发信；邮件将内嵌库存周期图表。\n\n"
                   "库存周期图表如下：\n\n[inline:中美库存周期图表.png]\n\n"
                   "署名：DuMate 库存周期监测",
                   image=BASE + "/中美库存周期图表.png")
        return
    refresh_data()
    chb, usb, fc = recompute()
    print("[计算] 中国", chb["month"].iloc[-1], chb["phase"].iloc[-1], "| 美国", usb["month"].iloc[-1], usb["phase"].iloc[-1])
    import render_chart
    render_chart.main(push=False)          # 先按最新数据出图，供邮件内嵌与管理端推送复用
    chart = BASE + "/中美库存周期图表.png"
    if not args.no_email:
        check_switch(chb, usb, fc, chart=chart)
    if not args.no_feishu:
        import build_report
        build_report.main()
    print("[完成] 库存周期月度任务")


if __name__ == "__main__":
    main()