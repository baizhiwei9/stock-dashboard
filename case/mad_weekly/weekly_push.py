# -*- coding: utf-8 -*-
"""周度报告飞书投递：--mode grid（格式化网格报告，column_set 原生表格）｜--mode card（交互式卡片）。"""
import argparse
import json
import os
import sys

import pandas as pd
import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
FEISHU_CFG = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/momentum-risk-control/feishu_config.py"
PUSH_MOD = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f28eb4508ffeXvGpuDQNknTjQn/tech_consensus_daily"
OUT = WS + "/weekly_out"
MENTION = "小宇头"
DISCLAIMER = "本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，不构成任何投资建议。市场有风险，投资需谨慎。"


def conf():
    ns = {}
    exec(open(FEISHU_CFG, encoding="utf-8").read(), ns)
    return ns


def row_cells(cells, bold=False):
    return [{"tag": "column", "width": "weighted", "weight": w, "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": ("**%s**" % t) if bold else t}}]} for t, w in cells]


def table(headers, rows, weights):
    els = [{"tag": "column_set", "flex_mode": "stretch", "background_style": "grey",
            "columns": row_cells(list(zip(headers, weights)), bold=True)}]
    for r in rows:
        els.append({"tag": "column_set", "flex_mode": "stretch", "columns": row_cells(list(zip(r, weights)))})
    return els


def send(card, cfg):
    r = requests.post(cfg["WEBHOOK_URL"], json={"msg_type": "interactive", "card": card}, timeout=30)
    js = r.json() if r.text.strip().startswith("{") else {"raw": r.text[:200]}
    return js


def load():
    q = pd.read_csv(OUT + "/qualified.csv", dtype={"ts_code": str}).sort_values("PEG")
    e = pd.read_csv(OUT + "/entities.csv")
    data = json.load(open(OUT + "/report_data.json", encoding="utf-8"))
    return q, e, data


def name_block(q, per_line=4):
    """纯文本名单：只留股票名称，全角空格对齐分列，供直接读取/抓取。"""
    names = [str(x) for x in q["名称"]]
    def pad(nm):
        return nm + "\u3000" * (4 - len(nm) + 1)
    lines = []
    for i in range(0, len(names), per_line):
        lines.append("".join(pad(n) for n in names[i:i + per_line]).rstrip("\u3000"))
    return "**合格名单·纯文本（共 %d 只，按 PEG 升序）**\n%s" % (len(names), "\n".join(lines))


def grid_card(q, data, list_key=None):
    as_of = data.get("as_of", "")
    L = data.get("ladder", {})
    els = [{"tag": "div", "text": {"tag": "lark_md", "content":
            "**%s，本周筛选结果请查收**\n合格股票 **%d** 只（其中 PEG≤1 **%d** 只）｜资金聚集实体 **%d** 个｜数据时点 **%s**" % (
                MENTION, len(q), int((q.PEG <= 1).sum()), L.get("分子≥3且聚集", 0), as_of)}},
           {"tag": "hr"},
           {"tag": "div", "text": {"tag": "lark_md", "content": "**合格名单（按 PEG 升序，见下方表格图）**"}}]
    if list_key:
        els.append({"tag": "img", "img_key": list_key, "mode": "fit_horizontal", "preview": True,
                    "alt": {"tag": "plain_text", "content": "合格名单（含 PEG、增速、买点、行业/概念）"}})
    else:
        rows = [[r["名称"], "%.3f" % r["PEG"], "%.1f%%" % r["增速2027"], r["命中"]] for _, r in q.head(12).iterrows()]
        els += table(["名称", "PEG", "增速", "买点"], rows, [40, 20, 20, 20])
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": name_block(q)}})
    els += [{"tag": "hr"}, {"tag": "div", "text": {"tag": "lark_md", "content": "**筛选链各层只数**"}}]
    stage = [["三条件（MAD>0、MA40 向上、MAD20>0）", L.get("三条件")],
             ["行业前 50 池（只）", L.get("行业前50池")], ["实体本周等权>0（个）", L.get("实体>0")],
             ["分子≥3 且资金聚集（个）", L.get("分子≥3且聚集")], ["池内合格股（只）", L.get("池内合格股")],
             ["有买点（只）", L.get("有买点")], ["剔除宽基后实体（个）", L.get("剔除宽基后实体")],
             ["PEG≤1.5（只）", L.get("PEG≤1.5")]]
    els += table(["阶段", "数量"], [[a, ("%s" % b) if b is not None else "—"] for a, b in stage], [72, 28])
    els += [{"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": DISCLAIMER}]},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "MAD 均线指选股·网格报告 ｜ %s ｜ 来自 DoMate" % as_of}]}]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "orange", "title": {"tag": "plain_text",
                       "content": "⭐ MAD 均线指选股·网格报告 ｜ %s ｜ %s ｜ 来自 DoMate" % (as_of, MENTION)}},
            "elements": els}


def interactive_card(q, data, img_key=None, list_key=None):
    as_of = data.get("as_of", "")
    els = [{"tag": "div", "text": {"tag": "lark_md", "content": "**%s，本周筛选结果如下**" % MENTION}},
           {"tag": "div", "fields": [
               {"is_short": True, "text": {"tag": "lark_md", "content": "**合格股票**\n%d 只" % len(q)}},
               {"is_short": True, "text": {"tag": "lark_md", "content": "**PEG≤1**\n%d 只" % int((q.PEG <= 1).sum())}},
               {"is_short": True, "text": {"tag": "lark_md", "content": "**平均 PEG**\n%.2f" % q.PEG.mean()}},
               {"is_short": True, "text": {"tag": "lark_md", "content": "**数据时点**\n%s" % as_of}}]}]
    if img_key:
        els.append({"tag": "img", "img_key": img_key, "mode": "fit_horizontal", "preview": True,
                    "alt": {"tag": "plain_text", "content": "MAD 均线指选股图"}})
    els.append({"tag": "hr"})
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "**合格名单（按 PEG 升序，含 PEG≤1 全部标的）**"}})
    if list_key:
        els.append({"tag": "img", "img_key": list_key, "mode": "fit_horizontal", "preview": True,
                    "alt": {"tag": "plain_text", "content": "合格名单（含 PEG、增速、买点、行业/概念）"}})
    else:
        top = q[q.PEG <= 1]
        top = top if len(top) else q.head(5)
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(
            "%d. **%s**　PEG %.3f　增速 +%.1f%%　%s" % (i, r["名称"], r["PEG"], r["增速2027"], r["命中"])
            for i, (_, r) in enumerate(top.iterrows(), 1))}})
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": name_block(q)}})
    els += [{"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "**筛选窗口（可切换口径后查看明细）**"}},
            {"tag": "action", "actions": [
                {"tag": "select_static", "placeholder": {"tag": "plain_text", "content": "选择口径"},
                 "options": [{"text": {"tag": "plain_text", "content": "PEG≤1"}, "value": "p1"},
                             {"text": {"tag": "plain_text", "content": "PEG≤1.5"}, "value": "p15"}]},
                {"tag": "button", "text": {"tag": "plain_text", "content": "查看名单"}, "type": "primary", "value": {"act": "list"}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "重跑筛选"}, "type": "default", "value": {"act": "rerun"}}]},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": DISCLAIMER}]},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "MAD 均线指选股·交互卡片 ｜ %s ｜ 来自 DoMate" % as_of}]}]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "red", "title": {"tag": "plain_text",
                       "content": "⭐ MAD 均线指选股·交互卡片 ｜ %s ｜ %s ｜ 来自 DoMate" % (as_of, MENTION)}},
            "elements": els}


def upload(path):
    try:
        sys.path.insert(0, PUSH_MOD)
        import feishu_push
        cfg = conf()
        token = feishu_push._tenant_token(cfg["APP_ID"], cfg["APP_SECRET"])
        return feishu_push.upload_image(token, path)
    except Exception as e:
        print("[push] 图片上传失败（%s），改用纯文字卡" % type(e).__name__)
        return None


def ensure_list_image():
    """推送前保证名单图与当前名单一致：图缺失或早于 qualified.csv 时重生成，否则复用。"""
    p = OUT + "/qualified_list.png"
    src = OUT + "/qualified.csv"
    if os.path.exists(p) and os.path.exists(src) and os.path.getmtime(p) > os.path.getmtime(src):
        return p
    try:
        if WS not in sys.path:
            sys.path.insert(0, WS)
        import weekly_list_image
        weekly_list_image.main()
    except Exception as e:
        print("[push] 名单图生成失败（%s），本次不附名单图" % type(e).__name__)
    return p if os.path.exists(p) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["grid", "card", "both"], default="both")
    ap.add_argument("--png", default=OUT + "/weekly_chart.png")
    a = ap.parse_args()
    cfg = conf()
    q, e, data = load()
    if q.empty:
        print("[push] 合格名单为空，已中止推送（不发送空报告）"); sys.exit(2)
    lpng = ensure_list_image()
    lkey = upload(lpng) if lpng else None
    if a.mode in ("grid", "both"):
        js = send(grid_card(q, data, lkey), cfg)
        print("[push] 网格报告 code=%s msg=%s 名单图=%s" % (js.get("code", js.get("StatusCode")), str(js)[:120], bool(lkey)))
    if a.mode in ("card", "both"):
        key = upload(a.png) if os.path.exists(a.png) else None
        js = send(interactive_card(q, data, key, lkey), cfg)
        print("[push] 交互卡片 code=%s msg=%s img=%s" % (js.get("code", js.get("StatusCode")), str(js)[:120], bool(key)))


if __name__ == "__main__":
    main()