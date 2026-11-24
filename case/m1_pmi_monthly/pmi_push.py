# -*- coding: utf-8 -*-
"""PMI 推送模块：飞书交互式卡片（分析报告）+ 邮件提醒（黄色及以上仓位）

输入：
  - data/pmi_analysis.json      （pmi_report.py 产出，结构化判定结果）
  - output/pmi_report_{YYYYMM}.txt（分析报告文本）
行为：
  - 飞书：每次运行推送交互式卡片（分析报告），复用 feishu_push.py 模板结构
    （tenant_access_token + webhook，凭据来自 momentum-risk-control/feishu_config.py）
  - 邮件：仓位为黄/红时发送提醒至 410119229@qq.com（dws 主通道 + 163 SMTP 回退，
    复用 pusher.send_email）；绿色仓位不发邮件
  - 幂等：同月（data_month）已推送过飞书卡片且已处理邮件 → 跳过，--force 可强制重发
用法：
  python pmi_push.py            # 正常推送
  python pmi_push.py --dry-run  # 只打印将发送内容，不实际发送
  python pmi_push.py --force    # 忽略同月幂等
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ecfae4e5bffelKf2SjHn8w8zAu")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/momentum-risk-control")

import requests

from config import DATA_DIR, OUT_DIR
from pusher import send_email

ANALYSIS_JSON = os.path.join(DATA_DIR, "pmi_analysis.json")
STATE_FILE = os.path.join(DATA_DIR, "pmi_push_state.json")

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"


def feishu_config():
    import feishu_config as fc
    return fc.APP_ID, fc.APP_SECRET, fc.WEBHOOK_URL


def tenant_token(app_id, app_secret):
    r = requests.post(TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"飞书token失败: {d.get('msg')}")
    return d["tenant_access_token"]


# ---------------- 卡片构建（对齐 feishu_push.py 模板） ----------------
def make_header(title, template="blue"):
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def make_text_element(content, tag="lark_md"):
    return {"tag": "div", "text": {"tag": tag, "content": content}}


def make_divider():
    return {"tag": "hr"}


def make_note(content):
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def build_card(r, report_text):
    month = r["data_month"]
    pos = r["position"]
    pos_label = r["position_label"]
    template = {"绿": "green", "黄": "orange", "红": "red"}[pos]

    elements = []
    # 顶部：报告来源 + 日期
    elements.append(make_text_element(
        f"**报告来自于 DoMate**  |  PMI 月度分析  |  **{month}**"))
    elements.append(make_divider())

    # ① 仓位结论置顶
    alert_txt = "⚠️ 触发邮件提醒" if r["alert_email"] else "✅ 未触发邮件（绿色仓位）"
    elements.append(make_text_element(
        f"**当前仓位：{pos}（{pos_label}）**，加权分 {r['position_score']}（核心动作 A/B/E 权重2，次要 C/D 权重1）\n{alert_txt}"))

    # ② 阈值判定清单
    seg = [f"**一、阈值判定清单（7 项）**（制造业PMI {fmt(r['pmi'])}｜非制造业商务活动 {fmt(r['business_activity'])}）\n"]
    for c in r["checks"]:
        mark = "✓" if c["hit"] is True else ("✗" if c["hit"] is False else "▲")
        note = f"（{c['note']}）" if c.get("note") else ""
        seg.append(f"· {mark} {c['name']}：{c['formula']}{note}")
    elements.append(make_text_element("\n".join(seg)))
    elements.append(make_divider())

    # ③ 触发动作
    seg = ["**二、触发动作**\n"]
    if r["hit_actions"]:
        for k, desc in r["hit_actions"].items():
            seg.append(f"· ▶ **{k}.** {desc}")
    else:
        seg.append("· 无触发动作")
    for k in r["actions"]:
        if r["actions"][k]["hit"] is None:
            seg.append(f"· ▲ **{k}.** {r['actions'][k]['desc']}【原文缺失，无法判定】")
    elements.append(make_text_element("\n".join(seg)))
    elements.append(make_divider())

    # ④ 数据口径与风险
    gaps = []
    for c in r["checks"]:
        if "原文缺失" in c.get("formula", ""):
            gaps.append(c["name"])
    gap_txt = ("；".join(gaps) + "【原文缺失】") if gaps else "无"
    elements.append(make_note(
        f"数据：国家统计局官方 PMI 运行情况发布页（制造业+非制造业）；"
        f"数据缺口：{gap_txt}；仓位分级为规则化辅助信号，非投资建议。"))

    return {
        "config": {"wide_screen_mode": True},
        "header": make_header(f"PMI 月度分析 · {month}", template),
        "elements": elements,
    }


def fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def build_email(r, report_text):
    month = r["data_month"]
    pos = r["position"]
    subject = f"【PMI仓位】{month} 判定为 {pos}（{r['position_label']}）触发提醒"
    body = (
        "【PMI 仓位提醒】\n\n"
        f"数据月份：{month}\n"
        f"当前仓位：**{pos}（{r['position_label']}）**，加权分 {r['position_score']}\n"
        "阈值判定：\n"
        + "\n".join(
            f"- {'✓' if c['hit'] is True else ('✗' if c['hit'] is False else '▲')} "
            f"{c['name']}：{c['formula']}{('（' + c['note'] + '）') if c.get('note') else ''}"
            for c in r["checks"])
        + "\n\n触发动作：\n"
        + ("\n".join(f"- {k}. {d}" for k, d in r["hit_actions"].items()) if r["hit_actions"] else "- 无")
        + f"\n\n完整分析报告：见 output/pmi_report_{month.replace('-', '')}.txt\n"
        "数据来源：国家统计局官方 PMI 发布页。本提醒为规则化辅助信号，非投资建议。"
    )
    return subject, body


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    ap.add_argument("--force", action="store_true", help="忽略同月幂等")
    args = ap.parse_args()

    if not os.path.exists(ANALYSIS_JSON):
        print("[PMI推送] 缺少 pmi_analysis.json，请先运行 pmi_report.py")
        sys.exit(1)
    with open(ANALYSIS_JSON, encoding="utf-8") as f:
        r = json.load(f)
    month = r["data_month"]
    report_path = os.path.join(OUT_DIR, f"pmi_report_{month.replace('-', '')}.txt")
    report_text = ""
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as f:
            report_text = f.read()

    state = load_state()
    if not args.force and state.get("last_pushed_month") == month:
        print(f"[PMI推送] 当月({month})已推送过，跳过(幂等)；--force 可强制重发")
        return

    # 1) 飞书交互式卡片（每月推送分析报告）
    card = build_card(r, report_text)
    payload = {"msg_type": "interactive", "card": card}
    if args.dry_run:
        print("[PMI推送] dry-run 飞书卡片内容：")
        print(json.dumps(card, ensure_ascii=False, indent=2)[:2000])
    else:
        app_id, app_secret, webhook = feishu_config()
        tk = tenant_token(app_id, app_secret)
        headers = {"Authorization": f"Bearer {tk}",
                   "Content-Type": "application/json"}
        resp = requests.post(webhook, json=payload, headers=headers, timeout=30)
        d = resp.json()
        if d.get("code") != 0 and d.get("StatusCode") != 0:
            # token 过期重试一次
            tk = tenant_token(app_id, app_secret)
            headers["Authorization"] = f"Bearer {tk}"
            resp = requests.post(webhook, json=payload, headers=headers, timeout=30)
            d = resp.json()
        if d.get("code") != 0 and d.get("StatusCode") != 0:
            raise RuntimeError(f"飞书推送失败: {d.get('msg') or d.get('StatusMessage')}")
        print(f"[PMI推送] 飞书交互式卡片已推送（{month}）")

    # 2) 邮件提醒（黄/红才发，绿色不发）
    email_done = False
    if r["alert_email"]:
        subject, body = build_email(r, report_text)
        if args.dry_run:
            print(f"[PMI推送] dry-run 将发送邮件：{subject}")
        else:
            ok = send_email(subject, body)
            if ok:
                email_done = True
                print(f"[PMI推送] 邮件已发送 -> {subject}")
            else:
                print("[PMI推送] 邮件发送失败")
                sys.exit(1)
    else:
        print(f"[PMI推送] 当前仓位绿色，无需发送邮件提醒")

    if not args.dry_run:
        save_state({"last_pushed_month": month,
                    "last_email_month": month if (r["alert_email"] and email_done) else state.get("last_email_month")})
        print("[PMI推送] 状态已保存（同月幂等）")


if __name__ == "__main__":
    main()