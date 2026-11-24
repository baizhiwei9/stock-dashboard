# -*- coding: utf-8 -*-
"""「将军成长股拓扑选股」飞书交互式卡片（全市场口径）

结构：消息头 → 全市场链路概览 → 重点行业全链路核查（含剔除层级与原因）
      → 全市场收敛对照表（45 个条件通过对象，结构与缠论着色）→ 筛选链条与验证
      → 最终名单（行业级与个股级缠论均已通过）→ 取舍说明/证伪线 → 一张合成图 → 口径与免责

用法: python3 push_card.py [--task-dir DIR] [--img PNG] [--dry-run]
"""
import argparse
import importlib.util
import json
import os
import sys

import requests

CONFIG_CANDIDATES = [
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/momentum-risk-control/feishu_config.py",
]
IMG_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
TAG = {"聚集": "<font color='green'>🟢 资金聚集</font>",
       "扩散": "<font color='red'>🔴 资金扩散</font>",
       "—": "<font color='grey'>⚪ 无独立指数</font>"}
FOCUS_ORDER = ["机器人", "AI硬件", "存储", "光模块"]


def vcol(verdict):
    if verdict == "通过":
        return "<font color='green'>%s</font>" % verdict
    if verdict == "未取到":
        return "<font color='grey'>待核</font>"
    return "<font color='red'>%s</font>" % verdict


def load_cfg():
    for p in CONFIG_CANDIDATES:
        if not os.path.exists(p):
            continue
        spec = importlib.util.spec_from_file_location("feishu_cfg", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return {"webhook": getattr(m, "WEBHOOK_URL", None),
                "app_id": getattr(m, "APP_ID", None),
                "app_secret": getattr(m, "APP_SECRET", None)}
    raise RuntimeError("未找到 feishu_config.py")


def tenant_token(app_id, app_secret):
    r = requests.post(TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError("tenant_access_token 获取失败: %s" % d.get("msg"))
    return d["tenant_access_token"]


def upload_image(token, path):
    with open(path, "rb") as f:
        r = requests.post(IMG_UPLOAD_URL,
                          headers={"Authorization": "Bearer " + token},
                          files={"image": (os.path.basename(path), f, "image/png")},
                          data={"image_type": "message"}, timeout=90)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError("图片上传失败: %s" % d.get("msg"))
    return d["data"]["image_key"]


def chan_text(s):
    ch = s.get("chan") or {}
    return ch.get("说明") or ch.get("位置") or "未判定"


SHORT = [("C1 体量不足（合计总市值<500 亿或有效成分<5）", "体量(C1)不足"),
         ("C2 盈利不达标（净利润TTM同比中位≤0 或正增长占比<50%）", "盈利(C2)不达标"),
         ("C3 估值偏高（PE_TTM中位 > 净利润增速中位，PEG>1）", "估值(C3)偏高"),
         ("C3 估值偏高（PE_TTM中位>净利润增速中位）", "估值(C3)偏高"),
         ("C6 容量不足（近5日成交额<20 亿）", "容量(C6)不足")]


def short_reason(reason):
    out = reason
    for a, b in SHORT:
        out = out.replace(a, b)
    if out.startswith("缠论不通过"):
        out = "缠论不通过"
    if "指数行情取不到" in out:
        out = "缠论待核"
    return out


def build_sections(cfg):
    un = cfg.get("universe", {})
    chain = cfg.get("chain", {})
    table = cfg.get("table", [])
    focus = cfg.get("focus_check", [])

    p1 = ["**全市场链路概览** ｜ 起点：申万 2021 三级行业 **%d** 个 ＋ 同花顺概念 **%d** 个 ＝ **%d** 个对象" % (
        un.get("申万三级", 0), un.get("同花顺概念", 0), un.get("全市场", 0))]
    p1.append("· 条件过滤（体量／盈利／估值／容量）后余 **%d** 个；拓扑结构按 60 日邻居数标注；"
              "行业与概念指数过缠论后余 <font color='green'>**%d**</font> 个，另有 <font color='grey'>%d</font> 个待核；"
              "单项通过数 体量 %d／盈利 %d／增速-估值匹配(PEG≤1) %d／容量 %d（估值匹配是最紧的一刀）。" % (
                  un.get("条件全过", 0), un.get("全链通过", 0), un.get("缠论待核", 0),
                  un.get("C1体量通过", 0), un.get("C2盈利通过", 0), un.get("C3估值通过", 0),
                  un.get("C6容量通过", 0)))
    p1.append("· 个股硬校验：增速与估值必须匹配（PEG≤1）——2027E PE ≤ 2027E 净利增速且增速>0；"
              "若净利TTM同比>0，再加 PE_TTM ≤ 净利TTM同比；不匹配者剔除并按候选顺位替换。")

    p1.append("")
    p1.append("**重点行业全链路核查（机器人 / AI 硬件 / 存储 / 光模块）**")
    p1.append("链路：L1 全市场 → L2 全产业链展开 → L3 条件过滤 → L4 拓扑标注 → L5 缠论过滤；逐项给出剔在哪一层、为什么。")
    for f in FOCUS_ORDER:
        rows = [x for x in focus if f in (x.get("focus") or "")]
        if not rows:
            continue
        n_l3 = sum(1 for x in rows if x.get("layer") == "L3 条件过滤")
        n_l5 = sum(1 for x in rows if x.get("layer") == "L5 缠论")
        n_ok = sum(1 for x in rows if x.get("layer") == "全链通过")
        p1.append("**%s**（%d 个对象：条件层剔除 %d、缠论层剔除 %d、全链通过 %d）" % (f, len(rows), n_l3, n_l5, n_ok))
        for x in rows:
            if x.get("layer") == "全链通过":
                p1.append("· %s（%s）｜ 净利TTM同比中位 %s%% ｜ PE_TTM中位 %s ｜ %s ｜ 邻居数 %s ｜ 缠论 %s（%s）｜ <font color='green'>全链通过</font>" % (
                    x["name"], x.get("kind", "—"), x.get("g_ttm"), x.get("pe_ttm"),
                    TAG.get(x.get("judge"), ""), x.get("neighbors", "—"),
                    vcol(x.get("chan_verdict", "-")), x.get("chan_pos") or "-"))
            else:
                p1.append("· %s（%s）｜ 净利TTM同比中位 %s%% ｜ PE_TTM中位 %s ｜ <font color='red'>剔除于 %s：%s</font>" % (
                    x["name"], x.get("kind", "—"), x.get("g_ttm"), x.get("pe_ttm"),
                    x["layer"], short_reason(x.get("reason") or "")))

    p1.append("")
    p1.append("**筛选链条：从全市场收敛到最终对象**")
    p1 += chain.get("steps", [])
    p1.append("")
    p1.append("**验证方式**")
    p1 += ["· " + v for v in chain.get("verify", [])]

    ok_rows = [r for r in table if r["chain_pass"]]
    no_rows = [r for r in table if not r["chain_pass"]]

    def peg_of(r):
        if r.get("pe_ttm") and r.get("g_ttm") and r["g_ttm"] > 0:
            return "%.2f" % (r["pe_ttm"] / r["g_ttm"])
        return "—"

    p2 = ["**全市场收敛对照表（完整 %d 行见附图）**：★全链通过 %d 个、○未通过 %d 个" % (
        len(table), len(ok_rows), len(no_rows))]
    p2.append("· <font color='green'>★ 全链通过</font>（按增速降序）：" + "、".join(
        "%s（%s，PEG %s）" % (r["name"], "申万" if r["kind"] == "申万三级" else "概念", peg_of(r))
        for r in ok_rows))
    p2.append("· <font color='red'>○ 未通过（%d 个，仍列入对照表不删行）</font>：" % len(no_rows)
              + "、".join(r["name"] for r in no_rows)
              + "（各自被剔层级与原因见附图对照表与明细表）")
    p2.append("")
    p2.append("**最终名单：全部 %d 个全链通过对象及其所属个股（%d 只）**" % (
        len(cfg["directions"]), sum(len(d.get("stocks") or []) for d in cfg["directions"])))
    p2.append("展示口径：%s" % cfg.get("featured_rule", ""))
    def _rk(s0):
        ch = s0.get("chan") or {}
        lb = str(ch.get("最近买点") or "")
        st = str(ch.get("状态") or ch.get("位置") or "")
        for k, v in (("三买", 5), ("二买", 4), ("一买", 3)):
            if k in lb:
                return v
        return 2 if st == "突破中枢" else (1 if st == "中枢内部" else 0)

    alls = [s0 for d0 in cfg["directions"] for s0 in (d0.get("stocks") or [])]
    alls.sort(key=lambda s0: -_rk(s0))
    cap = int(cfg.get("max_stocks_per_chart", 20) or 20)
    if len(alls) > cap:
        g1, g2 = alls[:cap], alls[cap:]
        from collections import Counter as _C
        c2 = _C(_rk(s0) for s0 in g2)
        parts = [x for x in ("三买 %d" % c2.get(5, 0), "二买 %d" % c2.get(4, 0), "一买 %d" % c2.get(3, 0),
                             "突破中枢 %d" % c2.get(2, 0), "中枢内部 %d" % c2.get(1, 0), "其他 %d" % c2.get(0, 0))
                 if not x.endswith(" 0")]
        p2.append("分图顺序：按买点等级降序（三买>二买>一买>突破中枢>中枢内部>其他），每图最多 %d 只；"
                  "第 1 张为买点等级最高的 %d 只（全部有买点），第 2 张起为其余 %d 只（买点较少：%s）。"
                  % (cap, cap, len(g2), "、".join(parts)))
    else:
        p2.append("分图顺序：按个股买点等级降序——三买 > 二买 > 一买 > 突破中枢 > 中枢内部 > 其他；全部 %d 只同处一张图。" % len(alls))
    for d in cfg["directions"]:
        p2.append("**%s**（%s｜%s｜邻居数 %s｜净利TTM同比中位 %s%%｜PE_TTM中位 %s｜指数级缠论 %s）" % (
            d["direction"], d.get("kind", "—"), TAG.get(d.get("judge"), ""), d.get("neighbors", "—"),
            d.get("g_ttm"), d.get("pe_ttm"), vcol(d.get("chan_verdict", "-"))))
        st = d.get("stocks") or []
        if not st:
            p2.append("　· 本轮无个股同时满足“研报覆盖≥6 家、增速-估值匹配 PEG≤1、个股缠论通过”，暂缺（不补造）")
        for s in st:
            c0 = s.get("chan") or {}
            lb = c0.get("最近买点") or "-"
            ctxt = ("%s · 现处%s" % (lb, c0.get("位置"))) if str(lb)[:1] in "一二三" else str(lb)
            at = s.get("归属") or {}
            p2.append("　· %s（%s）现价 %.2f 元｜2027E PE %s 倍／增速 %s%%（研报 %s 家）｜PEG %s／%s｜多归属 %s 个／匹配 %s 个｜缠论：%s" % (
                s["name"], s["code"], s["close"],
                s.get("pe27E") if s.get("pe27E") is not None else "—",
                s.get("g27") if s.get("g27") is not None else "—", s.get("orgs", 0),
                s.get("peg27E") if s.get("peg27E") is not None else "—",
                s.get("pegTTM") if s.get("pegTTM") is not None else "—",
                at.get("归属数", "—"), at.get("归属匹配数", "—"), ctxt))
        rjs = d.get("rejected") or []
        if rjs and len(st) < 2:
            p2.append("　· 剔除候补 %d 个，示例：<font color='red'>%s</font>" % (len(rjs), rjs[0]))
    p2.append("")
    p2.append("**证伪线（什么条件下本轮选择失效）**")
    p2.append("· 重点行业：机器人／AI 硬件／存储／光模块各对象的被剔层级与原因见上方核查（多数为 C3 增速-估值不匹配，个别为 C2 盈利不达标或缠论不通过）。")
    p2.append("· 指数层：任一全链通过对象的指数收盘跌破其最近中枢下沿且不出现一买，则该对象结构破坏、本轮入选失效。")
    p2.append("· 个股层：跌破最近中枢下沿且无新的有效买点，或 2027E 一致预期增速下修使 PEG>1，则个股入选失效。")
    return "\n".join(p1), "\n".join(p2)


STRUCTURE_NOTE = ("本次附图为 **SMC（聪明钱）／威科夫结构版**：在既有缠论分图之上叠加两套结构标注——"
                  "SMC＝摆动点(半径2)＋BOS/CHoCH 结构破坏＋OB 订单块＋流动性扫取＋FVG 不平衡（蓝色系）；"
                  "威科夫＝交易区间＋Spring／Upthrust／Test／SOS／SOW（橙紫色系）。"
                  "标注一律用真实颜色与符号，信息框位于价格区下方不遮挡曲线；分图顺序与上限沿用按买点排序规则。")


def build_card(cfg, img_keys=None, variant_label=None):
    title = "%s ｜ %s ｜ %s ｜ 来自 DoMate" % (
        cfg["report_name"], cfg["asof"], variant_label) if variant_label else \
        "%s ｜ %s ｜ 来自 DoMate" % (cfg["report_name"], cfg["asof"])
    s1, s2 = build_sections(cfg)
    if variant_label:
        s2 = STRUCTURE_NOTE + "\n\n" + s2
    els = [{"tag": "div", "text": {"tag": "lark_md", "content": s1}},
           {"tag": "div", "text": {"tag": "lark_md", "content": s2}}]
    for k in (img_keys or []):
        els.append({"tag": "img", "img_key": k,
                    "alt": {"tag": "plain_text", "content": "个股走势与全市场收敛对照表"}})
    els.append({"tag": "note", "elements": [{"tag": "plain_text",
                 "content": "起点为全市场行业与概念清单（申万 2021 三级 ＋ 同花顺概念）；条件＝体量/盈利/估值/容量；"
                            "拓扑＝60 日收益相关邻居数（≥4 资金聚集、≤3 资金扩散）；缠论＝分型版结构（MACD 20/40/5，一买即底背驰 B1）；"
                            "一致预期取卖方研报中位数且覆盖≥6 家。数据经 TuShare 代理获取。"
                            "本内容由人工智能基于公开信息与公开数据生成，仅供研究与交流，不构成任何投资建议"}]})
    return {"msg_type": "interactive",
            "card": {"config": {"wide_screen_mode": True},
                     "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
                     "elements": els}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--img", default=None)
    ap.add_argument("--charts-json", default=None,
                    help="图片清单（默认 data/charts.json；可指向 data/smc_wyckoff_charts.json）")
    ap.add_argument("--variant-label", default=None, help="标题前缀标签，如 SMC威科夫结构版")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(a.task_dir, "config.json"), encoding="utf-8"))
    cj = os.path.join(a.task_dir, a.charts_json or os.path.join("data", "charts.json"))
    charts = (json.load(open(cj, encoding="utf-8")) or {}).get("charts") if os.path.exists(cj) else None
    if a.img:
        charts = [a.img]
    charts = charts or []
    if a.dry_run:
        s1, s2 = build_sections(cfg)
        print(s1)
        print("\n===== 第二段 =====\n")
        print(s2)
        print("---- 字数:", len(s1) + len(s2))
        return
    fc = load_cfg()
    keys = []
    existing = [c for c in charts if os.path.exists(c)]
    if existing:
        try:
            tok = tenant_token(fc["app_id"], fc["app_secret"])
            for c in existing:
                keys.append(upload_image(tok, c))
            print("已上传 %d 张图，image_key 前缀:" % len(keys), [k[:8] for k in keys])
        except Exception as e:  # noqa
            print("图片上传失败，降级为纯文字卡:", e)
            keys = []
    else:
        print("[warn] 无可用图片，走纯文字卡")
    card = build_card(cfg, keys)
    r = requests.post(fc["webhook"], json=card, timeout=60)
    d = r.json()
    ok = (d.get("code") == 0) or (d.get("StatusCode") == 0)
    print("飞书卡片推送:", "成功" if ok else "失败", json.dumps(d, ensure_ascii=False)[:200])
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()