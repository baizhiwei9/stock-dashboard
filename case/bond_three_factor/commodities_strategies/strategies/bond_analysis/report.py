# -*- coding: utf-8 -*-
"""债市分析 · 报告渲染（纯文本 / Markdown / 飞书卡片 / 图）。

与 `cftc_cot/report.py` 的分工一致：engine 只算数，report 只排版。
不引入外部模板依赖：飞书卡片用 dict 拼，图用 matplotlib 直接画。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Tuple

from .engine import BondState, TH, summary_line

SIGN = "📎 来自钉钉OpenClaw"

LEVEL_EMOJI = {"red": "🔴", "orange": "🟠", "green": "🟢", "info": "·"}
LEVEL_FCOLOR = {"red": "red", "orange": "orange", "green": "green", "info": "grey"}


# ---------------------------------------------------------------- 纯文本

def build_text(S: BondState) -> str:
    L = [f"🇨🇳 中国债市 · 四层判定 · {S.asof or '-'}",
         f"总判定：{summary_line(S)}",
         f"数据源：{S.source}", ""]
    for v in S.verdicts:
        L.append(f"{LEVEL_EMOJI.get(v.level, '·')} {v.text}")
    L.append("")
    L.append("— 明细 —")
    L.append("  ⑥ 对政策利率利差（政策利率 = 隔夜逆回购操作利率）")
    if S.policy_rate is not None:
        L.append(f"    政策利率 {S.policy_rate:.2f}%"
                 + (f"（{S.policy_rate_date}）" if S.policy_rate_date else ""))
    else:
        L.append("    政策利率 —（公告未解析到利率）")
    for x in (S.policy_spreads or []):
        chg = x.get("chg_1w_bp")
        L.append(f"    {x['name']:<12} {x['value_bp']:>7.1f}bp  分位 {x.get('pct')}%"
                 + (f"  周Δ {chg:+.1f}bp" if chg is not None else ""))
    for x in S.govt_curve:
        L.append(f"  国债 {x['tenor']:<4} {x['value']:.4f}%  分位 {x.get('pct')}%  "
                 f"周Δ {x.get('chg_1w_bp')}bp")
    for x in S.govt_spreads:
        L.append(f"  利差 {x['name']:<9} {x['value_bp']:.1f}bp  分位 {x.get('pct')}%")
    for x in S.credit:
        L.append(f"  3Y {x['grade']:<4} 利差 {x['spread_bp']:.1f}bp  分位 {x.get('pct')}%")
    for x in S.ncd:
        L.append(f"  NCD {x['tenor']:<4} {x['value']:.4f}%  对国债 {x.get('vs_govt_bp')}bp")
    for k, v in (S.funding or {}).items():
        L.append(f"  {k} {v['value']}{v['unit']}（分位 {v.get('pct')}%）")
    if S.cbond_index:
        c = S.cbond_index
        L.append(f"  中债新综合 {c['value']}（MA20 {c['ma20']}，偏离 {c['gap_to_ma20_pct']}%）")
    if S.notes:
        L.append("")
        L.append("— 口径 —")
        for n in S.notes:
            L.append(f"  {n}")
    L.append("")
    L.append("⚠️ 结构性研判，不构成投资建议。")
    return "\n".join(L)


# ---------------------------------------------------------------- Markdown

def build_markdown(S: BondState) -> str:
    L = [f"# 🇨🇳 中国债市 · 四层判定 · {S.asof or '-'}", ""]
    L.append(f"**总判定**：{summary_line(S)}　｜　数据源：`{S.source}`")
    L.append("")
    for v in S.verdicts:
        L.append(f"- {LEVEL_EMOJI.get(v.level, '·')} {v.text}")
        if v.detail:
            L.append(f"  - <sub>{v.detail}</sub>")
    L.append("")
    if S.govt_curve:
        L += ["## 第①层 国债曲线", "",
              "| 期限 | 收益率 | 分位 | 周Δ(bp) |", "|---|---|---|---|"]
        for x in S.govt_curve:
            L.append(f"| {x['tenor']} | {x['value']:.4f}% | {x.get('pct')}% | "
                     f"{x.get('chg_1w_bp') if x.get('chg_1w_bp') is not None else '-'} |")
        L.append("")
    if S.credit:
        L += ["## 第②层 信用利差（3Y 中短票 − 国债）", "",
              "| 档位 | 利差(bp) | 分位 | 周Δ(bp) |", "|---|---|---|---|"]
        for x in S.credit:
            L.append(f"| {x['grade']} | {x['spread_bp']:.1f} | {x.get('pct')}% | "
                     f"{x.get('chg_1w_bp') if x.get('chg_1w_bp') is not None else '-'} |")
        L.append("")
    if S.credit_matrix:
        L += ["### 全期限信用利差矩阵（bp）", "",
              "| 期限 | 国债 | AAA | AA+ | AA | AA- |", "|---|---|---|---|---|---|"]
        for r in S.credit_matrix:
            cells = [f"{r[k]}" if r.get(k) is not None else "—"
                     for k in ("MTN.AAA", "MTN.AAP", "MTN.AA", "MTN.AAL")]
            L.append(f"| {r['tenor']} | {r['govt']} | " + " | ".join(cells) + " |")
        L.append("")
    if S.ncd:
        L += ["## 第③层 同业存单 AAA", "", "| 期限 | 收益率 | 对国债(bp) |", "|---|---|---|"]
        for x in S.ncd:
            L.append(f"| {x['tenor']} | {x['value']:.4f}% | {x.get('vs_govt_bp')} |")
        L.append("")
    if S.funding:
        L += ["## 第④层 资金结构", ""]
        for k, v in S.funding.items():
            L.append(f"- {k}：**{v['value']}{v['unit']}**（分位 {v.get('pct')}%）")
        L.append("")
    # 第⑥段始终渲染：数据缺失时也露出「—」，不静默隐藏（宁空勿隐）
    L += ["## ⑥ 对政策利率利差", ""]
    L.append("政策利率锚 = **隔夜逆回购操作利率**（2026-11-02 新框架，"
             "每工作日固定利率、数量招标；7 天期已改利率招标无单一利率）。")
    L.append("")
    L.append("**政策利率当前值**："
             + (f"{S.policy_rate:.2f}%" if S.policy_rate is not None else "—")
             + (f"（{S.policy_rate_date}）" if S.policy_rate_date else ""))
    L.append("")
    if S.policy_spreads:
        L += ["| 利差 | 现值(bp) | 分位 | 周Δ(bp) |", "|---|---|---|---|"]
        for x in S.policy_spreads:
            chg = x.get("chg_1w_bp")
            L.append(f"| {x['name']} | {x['value_bp']:.1f} | {x.get('pct')}% | "
                     f"{(f'{chg:+.1f}' if chg is not None else '-')} |")
        L.append("")
    if S.notes:
        L += ["## 口径说明", ""]
        for n in S.notes:
            L.append(f"- {n}")
        L.append("")
    L.append("> ⚠️ 结构性研判，不构成投资建议。")
    return "\n".join(L)


# ---------------------------------------------------------------- 飞书卡片

def _weights(n: int) -> List[int]:
    """表格列宽配比：首列稍宽（期限/档位/利差名都是短标签，够用即可）。"""
    preset = {2: (3, 4), 3: (4, 3, 3), 4: (2, 3, 3, 3), 5: (2, 2, 2, 2, 2)}
    return list(preset.get(n, tuple([1] * n)))

def build_card(S: BondState, title: Optional[str] = None) -> dict:
    E: List[dict] = []

    def md(t: str) -> None:
        E.append({"tag": "markdown", "content": t})

    md(f"{SIGN}\n**🇨🇳 中国债市 · 四层判定**\n"
       f"<font color='grey'>中债估值 {S.asof}｜{S.source}</font>")
    md(f"**总判定：{summary_line(S)}**")
    for v in S.verdicts:
        col = LEVEL_FCOLOR.get(v.level, "grey")
        md(f"<font color='{col}'>{LEVEL_EMOJI.get(v.level, '·')} {v.text}</font>")

    def row(cells: List[Tuple[str, int]]) -> None:
        """飞书卡片真表格：一行 = 一个 column_set。

        ⚠️ 飞书卡片 markdown 元素**不支持管道表格**（`| a | b |` + `|---|`），
        直接塞进去会原样渲染成乱码，必须走 column_set + lark_md。
        """
        E.append({
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "small",
            "columns": [
                {"tag": "column", "width": "weighted", "weight": w,
                 "vertical_align": "center",
                 "elements": [{"tag": "div",
                               "text": {"tag": "lark_md", "content": c}}]}
                for c, w in cells
            ],
        })

    def table(headers: List[str], rows: List[List[str]]) -> None:
        weights = _weights(len(headers))
        row([(f"**{h}**", w) for h, w in zip(headers, weights)])
        for r in rows:
            row([(str(c), w) for c, w in zip(r, weights)])

    if S.govt_curve:
        E.append({"tag": "hr"})
        md("**第①层 国债曲线**")
        table(["期限", "收益率", "分位", "周Δ"],
              [[x["tenor"], f"{x['value']:.4f}%", f"{x.get('pct')}%",
                f"{x.get('chg_1w_bp')}bp" if x.get("chg_1w_bp") is not None else "—"]
               for x in S.govt_curve])
    if S.credit:
        md("**第②层 信用利差（3Y）**")
        table(["档位", "利差", "分位"],
              [[x["grade"], f"{x['spread_bp']:.1f}bp", f"{x.get('pct')}%"] for x in S.credit])
    if S.ncd:
        md("**第③层 同业存单 AAA**")
        table(["期限", "NCD", "对国债"],
              [[x["tenor"], f"{x['value']:.4f}%", f"{x.get('vs_govt_bp')}bp"] for x in S.ncd])
    if S.funding:
        md("**第④层 资金结构**")
        for k, v in S.funding.items():
            md(f"- {k}：**{v['value']}{v['unit']}**（分位 {v.get('pct')}%）")
    if S.cbond_index:
        c = S.cbond_index
        md(f"**债基基准**：中债新综合 {c['value']}（MA20 {c['ma20']}，偏离 {c['gap_to_ma20_pct']:+.3f}%）")
    # 同 markdown：数据缺失也露出，避免「没这一节」被误读成「没这项监测」
    E.append({"tag": "hr"})
    md("**⑥ 对政策利率利差**（政策利率 = 隔夜逆回购操作利率）")
    md("政策利率当前值：**"
       + (f"{S.policy_rate:.2f}%" if S.policy_rate is not None else "—")
       + (f"**（{S.policy_rate_date}）" if S.policy_rate_date else "**"))
    if S.policy_spreads:
        table(["利差", "现值", "分位"],
              [[x["name"], f"{x['value_bp']:.1f}bp", f"{x.get('pct')}%"]
               for x in S.policy_spreads])
    if S.notes:
        E.append({"tag": "hr"})
        md("<font color='grey'>" + "　".join(S.notes) + "</font>")

    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange" if any(v.level == "orange" for v in S.verdicts) else
                   ("red" if any(v.level == "red" for v in S.verdicts) else "green"),
                   "title": {"tag": "plain_text",
                             "content": title or f"🇨🇳 中国债市 · 四层判定 · {S.asof}"}},
        "elements": E,
    }


# ---------------------------------------------------------------- 出图

def build_chart(S: BondState, out_path: str | Path) -> Optional[str]:
    """2 张图：①国债曲线（现值/1周前/1月前）②信用利差矩阵热力。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception as exc:  # pragma: no cover
        print(f"[chart] matplotlib 不可用: {exc}")
        return None

    zh = None
    for name in ("Noto Sans CJK SC", "WenQuanYi Zen Hei", "Source Han Sans CN",
                 "SimHei", "Microsoft YaHei"):
        try:
            if font_manager.findfont(name, fallback_to_default=False):
                zh = name
                break
        except Exception:
            continue
    if zh:
        plt.rcParams["font.sans-serif"] = [zh]
    plt.rcParams["axes.unicode_minus"] = False

    def lab(cn: str, en: str) -> str:
        return cn if zh else en

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.0))

    ax = axes[0]
    if S.govt_curve:
        tens = [x["tenor"] for x in S.govt_curve]
        cur = [x["value"] for x in S.govt_curve]
        w1 = [x.get("chg_1w_bp") for x in S.govt_curve]
        w1v = [c - (d / 100.0) if d is not None else float("nan") for c, d in zip(cur, w1)]
        xr = range(len(tens))
        ax.plot(xr, cur, "o-", lw=2, label=lab("当前", "now"), color="#c0392b")
        ax.plot(xr, w1v, "s--", lw=1.2, label=lab("1周前", "1w ago"),
                color="#e67e22", alpha=.75)
        ax.set_xticks(list(xr))
        ax.set_xticklabels(tens, fontsize=9)
        ax.set_ylabel(lab("收益率 %", "yield %"))
        ax.set_title(lab("中国国债收益率曲线（中债估值）", "CGB yield curve"), fontsize=12)
        ax.grid(alpha=.25)
        ax.legend(fontsize=9)

    ax = axes[1]
    if S.credit_matrix:
        import numpy as np
        keys = ["MTN.AAA", "MTN.AAP", "MTN.AA", "MTN.AAL"]
        arr = np.array([[r.get(k) if r.get(k) is not None else np.nan for k in keys]
                        for r in S.credit_matrix], dtype=float)
        im = ax.imshow(arr, cmap="RdYlGn_r", aspect="auto")
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels(["AAA", "AA+", "AA", "AA-"])
        ax.set_yticks(range(len(S.credit_matrix)))
        ax.set_yticklabels([r["tenor"] for r in S.credit_matrix])
        for i in range(arr.shape[0]):
            for j in range(arr.shape[1]):
                if arr[i, j] == arr[i, j]:
                    ax.text(j, i, f"{arr[i, j]:.0f}", ha="center", va="center", fontsize=9)
        ax.set_title(lab("中短票对国债利差矩阵（bp）", "MTN - CGB spread (bp)"), fontsize=12)
        fig.colorbar(im, ax=ax, shrink=.85, label="bp")

    fig.tight_layout()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return str(out)


# ---------------------------------------------------------------- 推送

def _tools_webhook() -> str:
    """从 workspace/TOOLS.md 读飞书 webhook（不入库的惯例做法）。"""
    import re
    for cand in (Path.home() / ".openclaw/workspace/TOOLS.md",
                 Path("/root/.openclaw/workspace/TOOLS.md")):
        try:
            txt = cand.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-zA-Z-]+", txt)
        if m:
            return m.group(0)
    return ""


def push_feishu(card: dict, webhook: Optional[str] = None) -> bool:
    hook = webhook or os.environ.get("BOND_FEISHU_WEBHOOK") or _tools_webhook()
    if not hook:
        print("[推送] 未配置飞书 webhook（设 BOND_FEISHU_WEBHOOK 或写进 TOOLS.md）")
        return False
    import requests
    body = json.dumps({"msg_type": "interactive", "card": card}, ensure_ascii=False)
    try:
        r = requests.post(hook, data=body.encode("utf-8"),
                          headers={"Content-Type": "application/json"}, timeout=30)
        js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = r.status_code == 200 and (js.get("code") == 0 or js.get("StatusCode") == 0)
        print(f"[推送] 飞书 http={r.status_code} ok={ok}")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 飞书异常: {exc}")
        return False


def push_text_feishu(text: str, webhook: Optional[str] = None) -> bool:
    hook = webhook or os.environ.get("BOND_FEISHU_WEBHOOK") or _tools_webhook()
    if not hook:
        return False
    import requests
    try:
        r = requests.post(hook, json={"msg_type": "text",
                                      "content": {"text": text + f"\n\n{SIGN}"}},
                          timeout=30)
        ok = r.status_code == 200 and r.json().get("code", 0) == 0
        print(f"[推送] 飞书文本 http={r.status_code} ok={ok}")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 飞书文本异常: {exc}")
        return False
