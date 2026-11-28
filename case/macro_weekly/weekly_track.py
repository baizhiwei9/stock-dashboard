# -*- coding: utf-8 -*-
"""周频跟踪入口：《库存周期 × 上下游利润摆动》周报（每周日 09:00）。

周频能跟到什么程度
------------------
PMI（月末）、PPI（次月 9 号）、规上工企利润（27 号）都是月频，周里没有新数据，所以周频只做两件事：
1. **代理动量**：期货主力（煤焦钢、有色、化工、建材）、交易所库存、CCPI、LME —— 看上下游价差走阔/收窄；
2. **月度落地校准**：PMI/PPI/利润一发布，立刻重算四象限、三个宽度与利润份额。

推送规范（用户口径，2026-11-28）
------------------------------
- 飞书只发 **交互式卡片**（msg_type=interactive），标题「主题 | 报告期 | 来自 DoMate」，署名置末；
- 正文 lark_md，小数据表用 column_set（column.width 写百分比字符串，写数字飞书会报错）；
- **配图必须进卡片**：image_key 靠 feishu.app_id/app_secret 上传；缺凭据时卡片里明示缺图原因，不静默省略；
- **库存周期阶段切换、或利润分布第一名变化（上游→中游/下游）→ 发邮件**（dws 主通道 → 163 SMTP 回退）。

用法
----
  python3 weekly_track.py                        # 全流程：取数 → 出报 → 出图 → 切卡推送 + 切换发邮件
  python3 weekly_track.py --no-feishu --no-email  # 只取数出报出图（流水 fetch 阶段）
  python3 weekly_track.py --push-only             # 只推送卡片 + 检测切换（流水 dispatch 阶段）
"""
import argparse
import json
import os
import re
import subprocess
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(BASE, "..", "_lib"))
for _p in (BASE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "out")
KEYS = os.path.join(DATA, "key_numbers.json")
MONTHLY = os.path.join(DATA, "edb_monthly.csv")
STATE = os.path.join(DATA, "weekly_state.json")
REPORT = os.path.join(OUT, "库存周期与利润转移周报.html")
PNG = os.path.join(OUT, "库存周期与利润转移图表.png")

DWS_CLI = os.path.expanduser("~/.local/bin/dws")
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

GROUPS = [("上游", "share_up", "CN.PROFIT.AMT.UP"), ("中游", "share_mid", "CN.PROFIT.AMT.MID"),
          ("下游", "share_down", "CN.PROFIT.AMT.DOWN"), ("公用", "share_util", None)]
TPL = {"主动补库": "orange", "被动补库": "yellow", "主动去库": "red", "被动去库": "green"}


# ---------------- 数字格式化 ----------------
def _num(v, d=1, sign=False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        return (("{:+." if sign else "{:.") + str(d) + "f}").format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _fmt(k, key, d=1, sign=False, suf=""):
    return _num(k.get(key), d, sign) + suf


def _leader(k):
    """利润份额第一名（存量口径）。"""
    cand = [(k.get(sk), g) for g, sk, _ in GROUPS if k.get(sk) is not None]
    return max(cand)[1] if cand else "—"


def _contrib(m=None):
    """增量贡献：今年同月累计额 − 去年同月累计额，占全部增量的比重。"""
    out = {}
    if m is None or "CN.PROFIT.AMT" not in m.columns:
        return out
    tot_s = m["CN.PROFIT.AMT"].dropna() if "CN.PROFIT.AMT" in m.columns else None
    if tot_s is None or tot_s.empty:
        return out
    cur = tot_s.index[-1]
    prev = [i for i in m.index if i.year == cur.year - 1 and i.month == cur.month]
    if not prev:
        return out
    b = prev[0]
    tot = float(m.loc[cur, "CN.PROFIT.AMT"]) - float(m.loc[b, "CN.PROFIT.AMT"])
    if abs(tot) < 1e-9:
        return out
    for g, _sk, col in GROUPS:
        if col and col in m.columns and pd.notna(m.loc[cur, col]) and pd.notna(m.loc[b, col]):
            out[g] = (float(m.loc[cur, col]) - float(m.loc[b, col])) / tot * 100.0
    return out


DAILY = os.path.join(DATA, "edb_daily.csv")
UP_C = ["CN.REBAR.FUT", "CN.FUT.JM", "CN.FUT.SC", "CN.FUT.CU", "CN.FUT.AL"]
MID_C = ["CN.FUT.FG", "CN.FUT.SA", "CN.FUT.TA", "CN.FUT.MA", "CN.FUT.EG"]


def _price_proxy():
    """上下游期货代理指数（2026-06-01=100）最新值、spread 与连续同向周数。"""
    try:
        if not os.path.exists(DAILY):
            return None, None, None, None
        d = pd.read_csv(DAILY, index_col=0, parse_dates=True).sort_index()
        base = d.loc[d.index >= "2026-06-01"]
        if len(base) < 20:
            return None, None, None, None

        def idx(cols):
            parts = [base[c].dropna() / base[c].dropna().iloc[0] * 100
                     for c in cols if c in base.columns and base[c].notna().any()]
            return pd.concat(parts, axis=1).mean(axis=1) if parts else None

        up, mid = idx(UP_C), idx(MID_C)
        if up is None or mid is None:
            return None, None, None, None
        w = pd.DataFrame({"up": up, "mid": mid}).resample("W-FRI").last()
        w["spread"] = w["mid"] - w["up"]
        sp = w["spread"].dropna()
        if sp.empty:
            return None, None, None, None
        dsig = sp.diff().dropna()
        run = 0
        if len(dsig):
            cur = dsig.iloc[-1]
            for v in dsig.iloc[::-1]:
                if (v > 0) == (cur > 0):
                    run += 1
                else:
                    break
        return (float(w["up"].dropna().iloc[-1]), float(w["mid"].dropna().iloc[-1]),
                float(sp.iloc[-1]), run)
    except Exception as e:  # noqa: BLE001
        print("[提示] 价格代理计算失败:", str(e)[:120])
        return None, None, None, None


def _load_monthly():
    try:
        if os.path.exists(MONTHLY):
            return pd.read_csv(MONTHLY, index_col=0, parse_dates=True).sort_index()
    except Exception as e:  # noqa: BLE001
        print("[提示] 月频宽表读取失败:", str(e)[:120])
    return None


# ---------------- 卡片 ----------------
def _cell(content):
    """column 里只能放块，lark_md 必须挂在 div.text 上（直接放会报 11310 unsupported type of block）。"""
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _row2(label, value, wl="34%", wr="66%"):
    return {"tag": "column_set", "flex_mode": "none", "columns": [
        {"tag": "column", "width": wl, "elements": [_cell("**%s**" % label)]},
        {"tag": "column", "width": wr, "elements": [_cell(value)]}]}


def _row3(a, b, c):
    return {"tag": "column_set", "flex_mode": "none", "columns": [
        {"tag": "column", "width": "34%", "elements": [_cell(a)]},
        {"tag": "column", "width": "33%", "elements": [_cell(b)]},
        {"tag": "column", "width": "33%", "elements": [_cell(c)]}]}


def build_card(k, m=None, img_key="", changes=None):
    """信息求全：结论 / 库存看板 / 三个宽度 / 利润分布（存量+增量）/ 价格代理 / 切换提示。"""
    contrib = _contrib(m)
    leader = _leader(k)
    quad = str(k.get("quad") or "—")
    head = "库存周期 与 上下游摆动 周报 | %s | 来自 DoMate" % (k.get("last") or "—")

    els = []
    if img_key:
        els.append({"tag": "img", "img_key": img_key,
                    "alt": {"tag": "plain_text", "content": "库存周期与利润转移四联图"}})

    # ① 结论
    trans = ""
    if contrib:
        pos = {g: v for g, v in contrib.items() if v > 0}
        neg = {g: v for g, v in contrib.items() if v < 0}
        if pos:
            order = sorted(pos, key=lambda g: -pos[g])
            trans = "新增利润 " + "、".join("%+.0f%% 来自%s" % (pos[g], g) for g in order) + "；"
        if neg:
            trans += "、".join("%s拖累 %+.0f%%" % (g, v) for g, v in sorted(neg.items(), key=lambda x: x[1])) + "。"
    lead_share = {"上游": k.get("share_up"), "中游": k.get("share_mid"),
                  "下游": k.get("share_down"), "公用": k.get("share_util")}.get(leader)
    els.append({"tag": "div", "text": {"tag": "lark_md", "content":
        "**结论**：库存 %s（自 %s 连续 %s 个月）；利润存量盘子最大的是 **%s**（%s%%），%s"
        % (quad, k.get("quad_since", "—"), _num(k.get("quad_months"), 0), leader,
           _num(lead_share), trans)}})
    els.append({"tag": "hr"})

    # ② 库存看板
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "**库存看板**（%s，统计局）" % (k.get("last") or "—")}})
    els.append(_row2("名义库存同比", _fmt(k, "inv_yoy", 1, suf="%") + "（连升 %s 个月）" % _num(k.get("inv_run"), 0)))
    els.append(_row2("实际库存同比", _fmt(k, "real_inv", 1, suf="%") + "（剔 PPI，连降 %s 个月）" % _num(k.get("real_run"), 0)))
    els.append(_row2("库销比 / 营收", _fmt(k, "ratio", 2, suf=" 月") + " ／ 营收 " + _fmt(k, "rev_yoy", 1, suf="%")))
    els.append(_row2("周转 / 回收期", _fmt(k, "inv_days", 1, suf=" 天") + "（同比 %s）／" % _fmt(k, "inv_days_chg", 1, sign=True)
                     + _fmt(k, "ar_days", 1, suf=" 天") + "（同比 %s）" % _fmt(k, "ar_days_chg", 1, sign=True)))
    els.append(_row2("PMI（制造业 / 原料 / 成品）", "%s / %s / %s" % (_fmt(k, "pmi"), _fmt(k, "pmi_raw"), _fmt(k, "pmi_fin"))))
    els.append({"tag": "hr"})

    # ③ 三个宽度
    els.append({"tag": "div", "text": {"tag": "lark_md", "content":
        "**三个宽度**（走阔=上游相对更强，挤压中下游）"}})
    els.append(_row3("PPI−CPI", "**%spp**" % _fmt(k, "w_ppi_cpi", 1, sign=True),
                     "环比 %spp" % _fmt(k, "w_ppi_cpi_d", 1, sign=True)))
    els.append(_row3("采掘−加工", "**%spp**" % _fmt(k, "w_mp", 1, sign=True),
                     "环比 %spp" % _fmt(k, "w_mp_d", 1, sign=True)))
    els.append(_row3("出厂−购进", "**%spp**" % _fmt(k, "w_out_in", 2, sign=True),
                     "环比 %spp" % _fmt(k, "w_out_in_d", 2, sign=True)))
    els.append({"tag": "hr"})

    # ④ 利润分布（存量份额 + 增量贡献）
    els.append({"tag": "div", "text": {"tag": "lark_md", "content":
        "**利润分布**（存量份额 ／ 增量贡献：谁拿走新增利润）"}})
    for g, sk, col in GROUPS:
        share = k.get(sk)
        if share is None:
            continue
        seg = "**%s**：%.1f%%" % (g, float(share))
        if g in contrib:
            seg += "　增量贡献 %+.0f%%" % contrib[g]
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": seg}})
    els.append({"tag": "hr"})

    # ⑤ 价格代理与切换预警
    up, mid, sp, run = _price_proxy()
    if up is not None:
        els.append({"tag": "div", "text": {"tag": "lark_md", "content":
            "**上下游价格代理**（期货，2026-06-01=100）：上游 **%.1f**｜中游 **%.1f**｜中游−上游 **%+.1f**"
            "（连续 %s 周同向）" % (up, mid, sp, ("%d" % run) if run is not None else "—")}})
    if changes:
        txt = "**本期发生切换**\n" + "\n".join("- " + c for c in changes) + "\n（已同步发邮件）"
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": txt}})

    # ⑥ 页脚
    els.append({"tag": "note", "elements": [{"tag": "plain_text", "content":
        "周中读数只作方向预判，正式结论以月度工企效益数据为准｜报告：out/库存周期与利润转移周报.html"
        + ("｜配图：out/库存周期与利润转移图表.png" if not img_key else "")}]})

    return {"config": {"wide_screen_mode": True},
            "header": {"template": TPL.get(quad.split("(")[0], "blue"),
                       "title": {"tag": "plain_text", "content": head}},
            "elements": els}


# ---------------- 推送与邮件 ----------------
def _feishu_creds():
    try:
        from feishu_creds import load_credentials
    except Exception as e:  # noqa: BLE001
        print("[飞书] 凭据模块不可用:", str(e)[:120])
        return "", "", ""
    c = load_credentials()
    if isinstance(c, (tuple, list)):
        c = c[0] or {}
    c = c or {}
    return (c.get("webhook_url") or "", c.get("app_id") or "", c.get("app_secret") or "")


def _upload_image(png, app_id, app_secret):
    import requests
    try:
        tok = requests.post(TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret}, timeout=20).json()
        token = tok.get("tenant_access_token") or ""
        if not token:
            print("[飞书] 取 tenant_access_token 失败:", str(tok)[:160])
            return ""
        up = requests.post(UPLOAD_URL, headers={"Authorization": "Bearer " + token},
                           files={"image": (os.path.basename(png), open(png, "rb"), "image/png")},
                           data={"image_type": "message"}, timeout=60).json()
        key = (up.get("data") or {}).get("image_key") or ""
        print("[飞书] image_key:", key or str(up)[:160])
        return key
    except Exception as e:  # noqa: BLE001
        print("[飞书] 上传图片异常:", str(e)[:160])
        return ""


def push_card(card):
    import requests
    hook, app_id, app_secret = _feishu_creds()
    if not hook:
        print("[飞书] 未配置 feishu.webhook，跳过推送")
        return False
    r = requests.post(hook, json={"msg_type": "interactive", "card": card}, timeout=30).json()
    ok = r.get("StatusCode") == 0 or r.get("code") == 0
    print("[飞书] 卡片:", "成功" if ok else "失败", str(r)[:200])
    return ok


def _send_smtp163(subject, body, image=None):
    """163 SMTP 主通道（用户 2026-11-28 指定）：需 email.username / email.password(授权码) / email.sender / email.recipients。"""
    import app_secrets
    frm = app_secrets.get("email.sender", "") or app_secrets.get("email.username", "")
    to = app_secrets.get("email.recipients", "")
    code = app_secrets.get("email.password", "")
    user = app_secrets.get("email.username", "") or frm
    if not (frm and to and code):
        print("[邮件] 163 通道缺配置（需 email.username/password/sender/recipients），跳过")
        return False
    try:
        from email.mime.image import MIMEImage
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import smtplib
        host = app_secrets.get("email.smtp_host", "smtp.163.com")
        port = app_secrets.get_int("email.smtp_port", 465)
        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = frm
        msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if image and os.path.exists(image):
            with open(image, "rb") as f:
                img = MIMEImage(f.read())
            img.add_header("Content-ID", "<chart>")
            img.add_header("Content-Disposition", "inline", filename=os.path.basename(image))
            msg.attach(img)
        s = smtplib.SMTP_SSL(host, port, timeout=60)
        s.login(user, code)
        s.sendmail(user, [x.strip() for x in to.replace(";", ",").split(",") if x.strip()], msg.as_string())
        s.quit()
        print("[邮件] 163 通道发送成功 ->", to)
        return True
    except Exception as e:  # noqa: BLE001
        print("[邮件] 163 通道失败:", str(e)[:150])
        return False


def _send_dws(subject, body, image=None):
    """dws 兜底通道（只需 email.sender/recipients）。"""
    import app_secrets
    frm = app_secrets.get("email.sender", "")
    to = app_secrets.get("email.recipients", "")
    if not (frm and to):
        print("[邮件] dws 通道缺 email.sender/recipients，跳过")
        return False
    cmd = [DWS_CLI, "mail", "message", "send", "--from", frm, "--to", to,
           "--subject", subject, "--content", body, "--format", "json"]
    if image and os.path.exists(image):
        cmd += ["--inline-attachment", image]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if re.search(r'"success"\s*:\s*(true|"true")', p.stdout, re.I):
            print("[邮件] dws 兜底通道发送成功 ->", to)
            return True
        print("[邮件] dws 返回异常:", (p.stderr or p.stdout)[:200])
    except Exception as e:  # noqa: BLE001
        print("[邮件] dws 调用失败:", str(e)[:150])
    return False


def send_email(subject, body, image=None):
    """163 SMTP 为主、dws 兜底（用户 2026-11-28 选定 163）。"""
    if _send_smtp163(subject, body, image):
        return True
    return _send_dws(subject, body, image)


# ---------------- 切换检测 ----------------
def detect_switch(k):
    """库存周期阶段、或利润分布第一名变化 → 返回变化说明列表（并落 state）。"""
    cur = {"quad": k.get("quad"), "leader": _leader(k), "month": k.get("last")}
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = {}
    changes = []
    if prev.get("quad") and cur["quad"] and prev["quad"] != cur["quad"]:
        changes.append("库存周期阶段切换：%s → %s（%s）" % (prev["quad"], cur["quad"], cur["month"]))
    if prev.get("leader") and cur["leader"] and prev["leader"] != cur["leader"]:
        changes.append("利润分布第一名变化：%s → %s（%s）" % (prev["leader"], cur["leader"], cur["month"]))
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump({**cur, "checked_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")},
                  open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:  # noqa: BLE001
        print("[切换检测] state 写入失败:", str(e)[:120])
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-feishu", action="store_true", help="不推飞书卡片")
    ap.add_argument("--no-email", action="store_true", help="不发切换邮件")
    ap.add_argument("--push-only", action="store_true", help="只推送/检测，不重新取数")
    ap.add_argument("--test-email", action="store_true", help="只发一封邮件通道自检（不发卡片、不取数）")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    if args.test_email:
        ok = send_email("[库存周期监测] 邮件通道自检",
                        "库存周期周频跟踪的邮件通道自检（163 SMTP 主通道、dws 兜底）。\n\n"
                        "触发规则：库存周期阶段切换、或利润分布第一名变化（如上游第一→下游第一）时自动发信，"
                        "邮件内嵌四联配图。\n\n署名：DuMate 库存周期周频跟踪",
                        PNG if os.path.exists(PNG) else None)
        print("[自检] 邮件通道:", "成功" if ok else "失败")
        return

    if not args.push_only:
        import compute_edb  # noqa: F401  顶层执行：取数 + 算指标 + 落 CSV/key_numbers
        import build_report  # noqa: F401  顶层执行：出 HTML
        print("[出报]", REPORT, os.path.exists(REPORT))
        try:
            import render_weekly_chart
            render_weekly_chart.main()
        except Exception as e:  # noqa: BLE001
            print("[出图] 失败（不阻断推送）:", str(e)[:200])

    k = json.load(open(KEYS, encoding="utf-8")) if os.path.exists(KEYS) else {}
    if not k:
        print("[提示] 没有 key_numbers.json，退出")
        return
    # 供框架 emit 抽指标：带 as_of 的副本（extract 不拿运行日兜底）
    try:
        asof = str(k.get("last") or "")
        if len(asof) == 7:
            asof += "-01"
        json.dump({**k, "as_of": asof}, open(os.path.join(OUT, "key_numbers.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, default=str)
    except Exception as e:  # noqa: BLE001
        print("[提示] out/key_numbers.json 写入失败:", str(e)[:120])

    m = _load_monthly()

    changes = [] if args.no_email else detect_switch(k)
    if changes:
        body = ("库存周期周频跟踪：本期发生切换\n\n"
                + "\n".join("- " + c for c in changes)
                + "\n\n**当前读数**\n"
                + "- 阶段：%s（自 %s 连续 %s 个月）\n" % (k.get("quad"), k.get("quad_since"), _num(k.get("quad_months"), 0))
                + "- 名义库存同比 %s%% / 实际 %s%%\n" % (_num(k.get("inv_yoy")), _num(k.get("real_inv")))
                + "- 利润份额：上游 %s%% / 中游 %s%% / 下游 %s%% / 公用 %s%%\n"
                  % (_num(k.get("share_up")), _num(k.get("share_mid")), _num(k.get("share_down")), _num(k.get("share_util")))
                + "- 三个宽度：PPI−CPI %spp、采掘−加工 %spp、出厂−购进 %spp\n"
                  % (_num(k.get("w_ppi_cpi")), _num(k.get("w_mp")), _num(k.get("w_out_in")))
                + "\n配图见附件；报告：out/库存周期与利润转移周报.html\n\n"
                + "署名：DuMate 库存周期周频跟踪")
        send_email("[库存周期监测] 阶段/利润分布发生变化（%s）" % (k.get("last") or ""), body, PNG)

    img_key = ""
    if not args.no_feishu:
        hook, app_id, app_secret = _feishu_creds()
        if PNG and os.path.exists(PNG):
            if app_id and app_secret:
                img_key = _upload_image(PNG, app_id, app_secret)
            else:
                print("[飞书] 缺 feishu.app_id/app_secret → 卡片里没图（image_key 需应用身份上传）")
        push_card(build_card(k, m, img_key, changes))
    else:
        print("[飞书] --no-feishu，跳过推送")
    print("[完成] macro_weekly")


if __name__ == "__main__":
    main()