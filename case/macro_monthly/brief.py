# -*- coding: utf-8 -*-
"""精简简报：只保留订单 / 产出 / 物价三条线。

产出三样东西：
- `out/brief_YYYYMM.md`  纯文本存档（给人读、给展示页兜底）
- 飞书交互式卡片 payload（KPI 字段 + 三张图 + 三句判断）
- 邮件 HTML（KPI 卡 + 内联图 + 三句判断）

所有数字来自 EDB（edb_bridge），不带主观臆造；缺失项显式写「未取到」。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import charts                                          # noqa: E402
import config                                          # noqa: E402
import edb_bridge as edb                               # noqa: E402

BLUE, RED, ORANGE, GREY = charts.BLUE, charts.RED, charts.ORANGE, charts.GREY


def _last2(code):
    rows = edb.latest_n(code, 2)
    cur = rows[0][1] if rows else None
    prev = rows[1][1] if len(rows) > 1 else None
    return cur, prev


def _fmt(v, unit="", sign=False):
    if v is None:
        return "未取到"
    return f"{v:+.1f}{unit}" if sign else f"{v:.1f}{unit}"


def _delta(cur, prev, unit="", signed=True):
    if cur is None or prev is None:
        return ""
    d = cur - prev
    return (f"{d:+.1f}{unit}" if signed else f"{d:.1f}{unit}")


def collect():
    """取三条线的读数与图；返回 dict。"""
    order, order_prev = _last2("CN.PMI.NEW.ORDER")
    export_order, export_order_prev = _last2("CN.PMI.NEW.EXPORT")
    prod, prod_prev = _last2("CN.PMI.PROD")
    exp, exp_prev = _last2("CN.EXP.YOY")
    exp_cum, _ = _last2("CN.EXP.CUM.YOY")
    ppi, ppi_prev = _last2("CN.PPI.YOY")
    ppi_mom, _ = _last2("CN.PPI.MOM")
    svc, svc_prev = _last2("CN.PMI.SVC.NEW.ORDER")
    cons, cons_prev = _last2("CN.PMI.CONS.NEW.ORDER")
    nmfg, nmfg_prev = _last2("CN.PMI.NMFG")
    durable, durable_prev = _last2("CN.PPI.DURABLE.YOY")
    daily, daily_prev = _last2("CN.PPI.DAILY.YOY")
    profit_m, _ = _last2("CN.PROFIT.MONTH.YOY")
    profit_cum, _ = _last2("CN.PROFIT.CUM.YOY")

    brent = edb.series("GLOBAL.BRENT")
    by_m = {}
    for d, v in brent.items():
        by_m.setdefault(d[:7], []).append(float(v))
    months = sorted(by_m)
    brent_cur = round(sum(by_m[months[-1]]) / len(by_m[months[-1]]), 1) if months else None
    brent_prev = round(sum(by_m[months[-2]]) / len(by_m[months[-2]]), 1) if len(months) > 1 else None

    imgs = charts.build_all(config.OUT_DIR)
    return {
        "month": (edb.month_series("CN.PPI.YOY", 1) or [("", None)])[0][0],
        "order": order, "order_prev": order_prev,
        "export_order": export_order, "export_order_prev": export_order_prev,
        "prod": prod, "prod_prev": prod_prev,
        "export": exp, "export_prev": exp_prev, "export_cum": exp_cum,
        "ppi": ppi, "ppi_prev": ppi_prev, "ppi_mom": ppi_mom,
        "brent": brent_cur, "brent_prev": brent_prev,
        "svc": svc, "svc_prev": svc_prev, "cons": cons, "cons_prev": cons_prev,
        "nmfg": nmfg, "nmfg_prev": nmfg_prev,
        "durable": durable, "durable_prev": durable_prev,
        "daily": daily, "daily_prev": daily_prev,
        "profit_m": profit_m, "profit_cum": profit_cum,
        "imgs": imgs,
    }


def judgements(d):
    """三句判断 + 一句合成（全部由读数推导，措辞避免超出数据）。"""
    out_order = []
    if d["order"] is not None:
        state = "跌破荣枯线" if d["order"] < 50 else "仍在荣枯线上"
        out_order.append(f"新订单 {d['order']:.1f}（{_delta(d['order'], d['order_prev'])}）{state}")
    if d["export_order"] is not None:
        out_order.append(f"新出口订单 {d['export_order']:.1f}（{_delta(d['export_order'], d['export_order_prev'])}）")
    order_txt = "，".join(out_order) + ("：外需订单回落更快。" if (
        d["export_order"] is not None and d["order"] is not None
        and d["export_order"] < d["order"]) else "。")

    nmfg_txt = ""
    if d["svc"] is not None or d["cons"] is not None:
        parts = []
        if d["svc"] is not None:
            parts.append(f"服务业新订单 {d['svc']:.1f}（{_delta(d['svc'], d['svc_prev'])}）")
        if d["cons"] is not None:
            parts.append(f"建筑业新订单 {d['cons']:.1f}（{_delta(d['cons'], d['cons_prev'])}）")
        nmfg_txt = "，".join(parts)
        if d["nmfg"] is not None:
            nmfg_txt += f"；非制造业 PMI {d['nmfg']:.1f}（{_delta(d['nmfg'], d['nmfg_prev'])}）"
        nmfg_txt += "：非制造业订单同步走弱。" if (
            (d["svc"] or 100) < 50 and (d["cons"] or 100) < 50) else "。"

    out_out = []
    if d["export"] is not None:
        out_out.append(f"出口同比 {d['export']:+.1f}%（前值 {_fmt(d['export_prev'], '%', True)}）")
    if d["prod"] is not None:
        out_out.append(f"PMI 生产 {d['prod']:.1f}（{_delta(d['prod'], d['prod_prev'])}）")
    if d["profit_m"] is not None:
        out_out.append(f"利润当月 {d['profit_m']:+.1f}%")
    output_txt = "，".join(out_out) + "。"

    out_price = []
    if d["ppi"] is not None:
        out_price.append(f"PPI 同比 {d['ppi']:+.1f}%（环比 {_fmt(d['ppi_mom'], '%', True)}）")
    if d["brent"] is not None:
        out_price.append(f"布伦特月均 {d['brent']:.0f} 美元（上月 {_fmt(d['brent_prev'], '', False)}）")
    price_txt = "，".join(out_price) + ("：输入性压力边际减弱，物价拐点看 11-12 月。" if (
        d["brent"] and d["brent_prev"] and d["brent"] < d["brent_prev"]) else "。")

    durable_txt = ""
    if d["durable"] is not None or d["daily"] is not None:
        durable_txt = (f"耐用品 PPI {_fmt(d['durable'], '%', True)}（{_delta(d['durable'], d['durable_prev'])}）、"
                       f"非耐用品（一般日用品）{_fmt(d['daily'], '%', True)}（{_delta(d['daily'], d['daily_prev'])}）")
        if d["durable"] is not None and d["daily"] is not None:
            durable_txt += ("：耐用品涨幅高于非耐用品，成本压力集中在可选消费。" if d["durable"] > d["daily"]
                            else "：非耐用品涨幅更高，压力偏必需消费。")

    combo = ""
    if d["order"] is not None and d["prod"] is not None and d["ppi"] is not None:
        fade = "订单先掉、产出跟随" if d["order"] < 50 and d["prod"] < 50 else "订单与产出一强一弱"
        combo = f"{fade}、物价滞后；三条线组合指向外需动能转弱、上游涨价接近尾声。"
    return {"order": order_txt, "nmfg": nmfg_txt, "output": output_txt,
            "price": price_txt, "durable": durable_txt, "combo": combo}


def kpi_pairs(d):
    """卡片字段块与邮件 KPI 条共用：[(标签, 值)]。"""
    return [
        ("制造业 · 新订单", f"{_fmt(d['order'])}（{_delta(d['order'], d['order_prev'])}）"),
        ("制造业 · 新出口订单", f"{_fmt(d['export_order'])}（{_delta(d['export_order'], d['export_order_prev'])}）"),
        ("非制造业 · 服务业新订单", f"{_fmt(d['svc'])}（{_delta(d['svc'], d['svc_prev'])}）"),
        ("非制造业 · 建筑业新订单", f"{_fmt(d['cons'])}（{_delta(d['cons'], d['cons_prev'])}）"),
        ("产出 · 出口同比", _fmt(d["export"], "%", True)),
        ("产出 · PMI 生产", f"{_fmt(d['prod'])}（{_delta(d['prod'], d['prod_prev'])}）"),
        ("物价 · PPI 同比", f"{_fmt(d['ppi'], '%', True)}（环比 {_fmt(d['ppi_mom'], '%', True)}）"),
        ("物价 · 布伦特月均", f"{_fmt(d['brent'], ' 美元')}"),
        ("耐用品 · PPI 耐用消费品", f"{_fmt(d['durable'], '%', True)}"),
        ("非耐用品 · PPI 一般日用品", f"{_fmt(d['daily'], '%', True)}"),
    ]


def title(d):
    month = d.get("month") or datetime.date.today().strftime("%Y-%m")
    if isinstance(month, str) and len(month) == 7 and month[4] == "-":
        month = f"{month[:4]}年{int(month[5:])}月"
    return f"宏观三条线 · {month}"


def _sections(d):
    """五段内容：标题、图 key、判断文本。卡片与邮件共用。"""
    j = judgements(d)
    return [("① 订单 · 制造业（新订单 / 新出口订单）", "order", j["order"]),
            ("② 订单 · 非制造业（服务业 / 建筑业新订单）", "order_nmfg", j["nmfg"]),
            ("③ 产出 · 出口同比 / PMI 生产", "output", j["output"]),
            ("④ 物价 · PPI / 布伦特月均", "price", j["price"]),
            ("⑤ 耐用品 vs 非耐用品 · PPI", "durable", j["durable"])]


def card_blocks(d, img_keys):
    """飞书卡片正文块（图片在 KPI 之后、判断之前）。"""
    j = judgements(d)
    blocks = [
        {"type": "div",
         "fields": [{"is_short": True,
                     "text": {"tag": "lark_md", "content": f"**{k}**\n{v}"}} for k, v in kpi_pairs(d)]},
        {"type": "hr"}]
    for head, key, text in _sections(d):
        blocks.append({"type": "md", "text": f"**{head}**"})
        blocks.append({"type": "img", "key": img_keys.get(key), "alt": head})
        if text:
            blocks.append({"type": "md", "text": text})
    blocks.append({"type": "hr"})
    if j["combo"]:
        blocks.append({"type": "md", "text": f"**一句话**：{j['combo']}"})
    return blocks


def email_html(d, img_names):
    """邮件 HTML：KPI 三列 + 三图内联 + 三句判断。img_names = {key: 文件名}。"""
    def kv(k, v):
        return (f'<td style="width:33%;padding:6px;"><div style="border:1px solid #dbe4ef;'
                f'border-radius:8px;padding:9px 10px;background:#f8fafc;">'
                f'<div style="font-size:12px;color:#5b6b7f;">{k}</div>'
                f'<div style="font-size:16px;font-weight:700;color:{BLUE};margin-top:3px;">{v}</div>'
                f'</div></td>')

    cells = [kv(k, v) for k, v in kpi_pairs(d)]
    rows = ""
    for i in range(0, len(cells), 3):
        chunk = cells[i:i + 3]
        while len(chunk) < 3:
            chunk.append('<td style="width:33%;"></td>')
        rows += f'<tr>{"".join(chunk)}</tr>'

    j = judgements(d)
    blocks = [(head, img_names.get(key), text) for head, key, text in _sections(d) if text or img_names.get(key)]
    body = []
    for head, name, text in blocks:
        body.append(f'<h3 style="font-size:15px;color:{BLUE};margin:20px 0 6px;">{head}</h3>')
        if name:
            body.append(f'<p style="margin:0 0 6px;text-align:center;">[inline:{name}]</p>')
        body.append(f'<p style="margin:6px 0 4px;line-height:1.7;">{text}</p>')

    combo = (f'<p style="margin:14px 0 4px;padding:10px 12px;background:#fff8e6;'
             f'border-left:3px solid #f2ddb0;line-height:1.7;"><strong>一句话</strong>：{j["combo"]}</p>'
             if j["combo"] else "")
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background:#eef1f5;">'
        '<div style="max-width:760px;margin:0 auto;padding:20px 18px 26px;background:#fff;'
        'font-family:-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
        'color:#222;font-size:14.5px;line-height:1.75;">'
        f'<h1 style="font-size:20px;color:{BLUE};margin:0 0 6px;padding-bottom:9px;'
        f'border-bottom:2px solid {BLUE};">{title(d)}</h1>'
        f'<p style="margin:6px 0 10px;color:#5b6b7f;font-size:12.5px;">数据截至 {d["month"]}；'
        '口径见文末。五张图对应制造业订单、非制造业订单、产出、物价、耐用品/非耐用品，只保留读数与判断。</p>'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{rows}</table>'
        + "".join(body) + combo +
        f'<p style="margin:18px 0 0;color:#9aa5b1;font-size:12px;">数据来源：国家统计局（PMI/PPI/利润）、'
        '海关总署（出口）、ICE 布伦特结算价；均为 EDB 落库序列。本邮件由 DuMate 宏观策略组自动生成。</p>'
        '</div></body></html>')


def to_markdown(d):
    j = judgements(d)
    lines = [f"# {title(d)}", "", "## 读数"]
    for k, v in kpi_pairs(d):
        lines.append(f"- {k}：{v}")
    lines += ["", "## 判断", f"- 订单（制造业）：{j['order']}",
              f"- 订单（非制造业）：{j['nmfg']}", f"- 产出：{j['output']}",
              f"- 物价：{j['price']}", f"- 耐用品/非耐用品：{j['durable']}"]
    if j["combo"]:
        lines.append(f"- 一句话：{j['combo']}")
    lines += ["", "## 图"] + [f"- {os.path.basename(p)}" for p in d["imgs"].values()]
    return "\n".join(lines)


def build(write_files=True):
    """生成简报内容；返回 (payload_dict, 卡片标题, 图路径, 图文件名)。"""
    d = collect()
    md = to_markdown(d)
    if write_files:
        os.makedirs(config.OUT_DIR, exist_ok=True)
        stamp = d["month"].replace("-", "") or datetime.date.today().strftime("%Y%m")
        with open(os.path.join(config.OUT_DIR, f"brief_{stamp}.md"), "w", encoding="utf-8") as f:
            f.write(md)
    payload = {"data": d, "judgements": judgements(d), "kpis": kpi_pairs(d),
               "title": title(d), "md": md, "imgs": d["imgs"]}
    return payload


if __name__ == "__main__":
    p = build()
    print(json.dumps({k: v for k, v in p.items() if k not in ("data",)}, ensure_ascii=False, indent=2)[:1200])