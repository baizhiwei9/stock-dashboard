# -*- coding: utf-8 -*-
"""推送「非负·剔反转 因子选层（行业 / 主题 / 概念）」飞书卡片（请求 32）。

口径唯一真源：output/nonneg_layer_top.json（由 raw/nonneg_layer_pick.py 产出）。
本卡只含 行业 / 主题 / 概念 三层名单，**不含选股段**；
不推送反转口径原始版、不推送原「行业主题」选层结果。

用法：
    python raw/push_nonneg_layer.py --dry      # 仅预览卡片文本，不发送
    python raw/push_nonneg_layer.py            # 实际推送到「AI股票消息」群
"""
import os
import sys
import json

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
sys.path.insert(0, os.path.join(BASE, "raw"))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f28eb4508ffeXvGpuDQNknTjQn/tech_consensus_daily")
import feishu_push  # noqa: E402

OUT = os.path.join(BASE, "output")
TOP_JSON = os.path.join(OUT, "nonneg_layer_top.json")
RANK_CSV = os.path.join(OUT, "nonneg_layer_rank.csv")
CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"  # 「AI股票消息」群
PERIOD = "2026-10-28"                            # 个股截面收盘日（数据截至）
TITLE = f"非负·剔反转 因子选层（行业/主题/概念） | {PERIOD} | 来自 DoMate"

G, R, B, E = "<font color='green'>", "<font color='red'>", "**", "</font>"


def load_top():
    import pandas as pd
    with open(TOP_JSON, encoding="utf-8") as f:
        top = json.load(f)
    rk = pd.read_csv(RANK_CSV)
    rkmap = {(r["层级"], r["名称"]): int(r["名次"]) for _, r in rk.iterrows()}
    return top, rkmap


def build_card(top, rkmap):
    facs = top["因子"]
    w = top["权重"]
    wtxt = " + ".join(f"{f} {w[f]}" for f in facs)
    ind, the, con = top["行业"], top["主题"], top["概念"]
    tgt = top.get("目标名次", {}).get("概念", {})

    mark = {"电机", "AI视频"}
    L = []
    L.append(f"**一、因子口径（非负 · 剔反转）**")
    L.append(f"因子与权重：{wtxt}")
    L.append("约束：" + "；".join(top["约束"]) + "。")
    L.append("说明：板块得分＝成分股因子分位（缺失跳过）等权平均；个股分位基于全 A 可交易池 5292 只；"
             "本卡只含 行业 / 主题 / 概念 三层，不含选股。")
    L.append("")

    L.append(f"**二、行业 Top5（{G}机会{ E}）**")
    for i, (r, nm, v) in enumerate(ind, 1):
        L.append(f"{G}{i}. {nm} {v:.4f}{E}")
    L.append("")

    L.append(f"**三、主题 Top5（{G}机会{E}）**")
    for i, (r, nm, v) in enumerate(the, 1):
        L.append(f"{G}{i}. {nm} {v:.4f}{E}")
    L.append("")

    L.append(f"**四、概念 Top10（{G}机会{E}）**")
    for r, nm, v in con:
        txt = f"{nm}" if nm not in mark else f"**{nm}**"
        L.append(f"{G}{int(r)}. {txt} {v:.4f}{E}")
    L.append("")

    L.append("**五、关键核对（目标板块 · 概念层）**")
    for k in ("电机", "AI视频"):
        if k in tgt:
            rk, tot, sc = tgt[k]
            tag = "✅ 进前 10" if rk <= 10 else "❌ 未进前 10"
            col = G if rk <= 10 else R
            L.append(f"{col}{B}{k}{B} #{rk}/{tot}（{sc:.4f}）{tag}{E}")
    for k in ("减速器", "短剧游戏", "人形机器人", "特斯拉概念"):
        if k in tgt:
            rk, tot, sc = tgt[k]
            col = G if rk <= 10 else R
            L.append(f"{col}{k} #{rk}/{tot}（{sc:.4f}）{'进前 10' if rk <= 10 else '未进前 10'}{E}")
    L.append("")

    L.append("**六、热门方向提及**")
    th_ai = rkmap.get(("主题", "AI软件与应用"), "—")
    th_ro = rkmap.get(("主题", "机器人与智能制造"), "—")
    L.append(f"机器人（{G}电机 概念层 #1{E}、减速器 #11）与 {G}AI视频 概念层 #4{E} 均入选概念层；"
             f"主题层对应「AI软件与应用 #{th_ai}」「机器人与智能制造 #{th_ro}」。")
    L.append("")

    L.append("**七、口径与证据边界**")
    L.append("① 行业层为 Tushare 110 行业口径，其中不存在「电机」「AI视频」名称，两者仅在概念层存在；")
    L.append("② 前瞻证据弱：能同时命中两目标的组合，其概念层 44 周前瞻多空 t 值均 < 0.5，不显著；")
    L.append(f"③ {R}PEG 方向提示{E}：引擎按 direction=+1（高 PEG 视为更优）处理，与字段注释「越低越好」存在口径冲突，"
             "选层结果对该符号敏感，须留意；")
    L.append(f"④ 数据截至 {PERIOD}（个股截面收盘日），资金流因子取最新可得交易日窗口。")
    return "\n".join(L)


def main():
    DRY = "--dry" in sys.argv
    top, rkmap = load_top()
    card = build_card(top, rkmap)
    chunks = feishu_push._chunk_markdown(card)
    print("converted chars:", len(card))
    print("chunks:", len(chunks))
    if DRY:
        print("\n---- 卡片文本预览 ----")
        print(card)
        print("---- 预览结束（未发送）----")
        print("DRY_OK")
        return
    app_id, app_secret, webhook = feishu_push._config()
    tk = feishu_push._tenant_token(app_id, app_secret)
    for i, seg in enumerate(chunks, 1):
        feishu_push.push_text_card(webhook, tk, TITLE, seg)
        print(f"text card sent {i}/{len(chunks)}: {len(seg)} chars")
    print("NONNEG_LAYER_SEND_OK")


if __name__ == "__main__":
    main()