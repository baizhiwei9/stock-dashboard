# -*- coding: utf-8 -*-
"""期权风险释放监控 · R 值口径与数值核对说明 —— 飞书交互式卡片（精简版）

与交付 Word 文档《期权风险释放监控_R值口径与数值核对说明_20261021.docx》内容对应：
- R 定义与公式、三种平值取法
- A股五轨 R（单腿 / 平值）对照
- 美股三轨 R（单腿 / 队列）对照
- 中证500（OP510500.SH + IC 基差）、中证1000（MO + IM 基差）完整数据
- 差异来源与判定、数据边界与免责

推送凭据：复用 skills/user/momentum-risk-control/feishu_config.py（只引用路径，不复制密钥）
"""
import json
import os
import sys

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
WORK = os.path.join(BASE, "ses_gffe5ea0b23728ffegpx6GMQLqwA5ek")
D = os.path.join(WORK, "data")
CFG_DIR = os.path.join(BASE, "skills", "user", "momentum-risk-control")
sys.path.insert(0, CFG_DIR)

ASOF = "2026-10-21"
HEADER = "期权风险释放监控 · R 值口径与数值核对 | %s | 来自 DoMate" % ASOF

RED = "<font color='red'>%s</font>"
ORANGE = "<font color='orange'>%s</font>"
GREY = "<font color='grey'>%s</font>"


def b(s):
    return "**%s**" % s


def red(s):
    return RED % s


def org(s):
    return ORANGE % s


def grey(s):
    return GREY % s


def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def col(width, content):
    return {"tag": "column", "width": width, "vertical_align": "top",
            "elements": [div(content)]}


def row(items):
    return {"tag": "column_set", "flex_mode": "stretch", "horizontal_spacing": "8px",
            "columns": [col(w, c) for w, c in items]}


def build():
    els = []

    # ---- 0. 一图看懂：R 定义 ----
    els.append(div(b("R 定义：") + red(b("R = IV(近月) / IV(次月)")) +
                   grey("（近月、次月取数据日之后最近的两个到期月）")))
    els.append(div("R>1 表示近月隐波高于次月，近端风险定价更贵；R<1 相反。" +
                   b("定价：") + "欧式 Black-Scholes 反解，r=1.8%，T=(到期日−数据日)/365，"
                   "二分法 80 次、区间 [0.5%, 300%]，无解/价格为零者剔除。"))
    els.append(div(b("三种平值取法：") +
                   "① " + b("单腿") + "（A股日报实际采用：取最接近现价、能解出 IV 的 1 张合约）｜"
                   "② " + b("平值双边") + "（同一行权价认购+认沽隐波均值，更常见的标准平值）｜"
                   "③ " + b("队列") + "（美股：近 4 张有效合约均值）"))
    els.append(div(grey("本系统 R 并非全链加权口径；若对方用“全链加权/OTM 虚值加权”，构造上就不同。")))

    els.append({"tag": "hr"})

    # ---- 1. A 股五轨 ----
    els.append(div(b("A股五轨 R 值对照")))
    els.append(row([("34%", grey("标的")), ("22%", grey("单腿 R")),
                    ("22%", grey("平值 R")), ("22%", grey("两口径之差"))]))
    a_rows = [
        ("沪深300ETF", "0.8324", "0.9388", "+0.1064"),
        ("中证500ETF", "1.1795", "1.0233", "−0.1562"),
        ("科创50ETF", "0.9334", "0.9709", "+0.0375"),
        ("创业板ETF", "1.0056", "0.9879", "−0.0178"),
        ("中证1000（MO）", "1.0000", "1.0406", "+0.0406"),
    ]
    for nm, r1, r2, d in a_rows:
        c2 = red(b(r1)) if nm == "中证500ETF" else b(r1)
        els.append(row([("34%", b(nm)), ("22%", c2), ("22%", b(r2)), ("22%", b(d))]))

    els.append({"tag": "hr"})

    # ---- 2. 美股三轨 ----
    els.append(div(b("美股三轨 R 值对照")))
    els.append(row([("46%", grey("标的")), ("27%", grey("单腿 R")), ("27%", grey("队列 R"))]))
    us_rows = [
        ("标普500（VIX / SPX 链）", "0.9609", "0.9618"),
        ("纳斯达克100（VXN / NDX 链）", "1.0104", "1.0247"),
        ("罗素2000（RVX / RUT 链）", "0.9769", "0.9778"),
    ]
    for nm, r1, r2 in us_rows:
        els.append(row([("46%", b(nm)), ("27%", b(r1)), ("27%", b(r2))]))

    els.append({"tag": "hr"})

    # ---- 3. 中证500 完整数据 ----
    els.append(div(b("中证500 完整数据") + "：" + b("OP510500.SH") +
                   "，标的 510500ETF 现价 " + b("6.8640")))
    els.append(div("单腿：" + "近月 10012354.SH（K6.75 认沽）IV " + red(b("27.51%")) +
                   " / 次月 10012473.SH（K6.75 认购）IV " + red(b("23.32%")) +
                   " → 单腿 R = " + red(b("1.1795"))))
    els.append(div("平值双边：近月 " + b("27.24%") + " / 次月 " + b("26.62%") +
                   " → 平值 R = " + b("1.0233")))
    els.append(div("IC 股指期货年化基差：当月 " + b("−10.49%") + " / 次月 " + b("−9.71%") +
                   " / 当季 " + b("−9.41%") + "；当季 Z = " + b("−0.27") +
                   grey("（近一年均值 −8.64%、标准差 2.84%、样本 252）")))

    els.append({"tag": "hr"})

    # ---- 4. 中证1000 完整数据 ----
    els.append(div(b("中证1000 完整数据") + "：" + b("MO 股指期权") +
                   "，标的 000852.SH 收盘 " + b("6780.2598") +
                   grey("（无官方 QVIX，隐波 ATM 自算，标注“期货期权替代”）")))
    els.append(div("单腿：" + "近月 MO2611-P-6800（认沽）IV " + b("32.24%") +
                   " / 次月 MO2612-P-6800（认沽）IV " + b("32.24%") +
                   " → 单腿 R = " + b("1.0000")))
    els.append(div("平值双边：近月 " + b("27.72%") + " / 次月 " + b("26.64%") +
                   " → 平值 R = " + b("1.0406") +
                   grey("（认沽隐波显著高于认购约 9pt，下行保护定价偏贵）")))
    els.append(div("IM 股指期货年化基差：当月 " + b("−11.32%") + " / 次月 " + b("−11.24%") +
                   " / 当季 " + b("−10.37%") + "；当季 Z = " + b("+0.16") +
                   grey("（近一年均值 −10.80%、标准差 2.77%、样本 252）")))

    els.append({"tag": "hr"})

    # ---- 5. 差异来源与判定 ----
    els.append(div(red(b("差异来源与判定：")) +
                   "本系统 R 非加权口径；与外部数值差异主因是“口径不对齐”，可归两点："))
    els.append(div("① " + b("单腿 vs 双边") +
                   "：A股按行权价距离只取一张合约，认购/认沽不对称会明显改变 R。"
                   "以中证500为例，近月恰好取到认沽 " + b("27.51%") + "、次月恰好取到认购 " + b("23.32%") +
                   "，得 " + red(b("1.1795")) + "；改用平值双边均值仅 " + b("1.0233") + "，相差 " + b("0.156") + "。"))
    els.append(div("② " + b("近月合约窗口") +
                   "：本系统近月采用最近到期月（A 股 2026-10-28，仅剩 " + red(b("7 天")) + "），"
                   "短到期隐波系统性偏高会推高 R；若采用次月/当季或剔除一周内到期，R 明显不同" +
                   grey("（中证500 平值口径 M1/M2=1.0233、M2/M3=1.0116、M1/M3=1.0352）")))
    els.append(div(grey("比较两个 R 之前必须对齐三项：取哪两个月、单腿还是认购认沽双边、是否含一周内短到期月。任一项不同即不具可比性。")))

    # ---- 6. 脚注 ----
    note = ("数据日 %s（沪深交易所/中金所当日收盘，当前最新可得交易日）；无风险利率固定 r=1.8%%，"
            "未做期限结构处理；中证1000 无官方波动率指数，隐波由 MO 当月平价期权 ATM 自算并标注“期货期权替代”，"
            "不与其他标的做绝对值横比；美股一列为 CBOE 延迟期权链盘中快照（链时间戳 2026-10-22 00:04–00:05，对应美东 10-21），"
            "官方法定波动率指数取最近收盘；缺失值一律标“待更新”，不插值不估算。"
            "本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。"
            "市场有风险，投资需谨慎。数据可能存在延迟、遗漏或误差，请独立判断并根据自身风险承受能力审慎决策。"
            "完整口径与全部数值见 Word 文档《期权风险释放监控_R值口径与数值核对说明_20261021.docx》") % ASOF

    els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "red",
                       "title": {"tag": "plain_text", "content": HEADER}},
            "elements": els,
        },
    }
    return card


def send(card):
    import feishu_config as fc
    import requests
    r = requests.post(fc.WEBHOOK_URL, json=card,
                      headers={"Content-Type": "application/json"}, timeout=30)
    d = r.json()
    print("[webhook] http=%s resp=%s" % (r.status_code, json.dumps(d, ensure_ascii=False)))
    ok = (d.get("code") == 0) or (d.get("StatusCode") == 0)
    if not ok:
        raise RuntimeError("飞书推送失败: %s" % (d.get("msg") or d.get("StatusMessage")))
    return d


if __name__ == "__main__":
    card = build()
    out = os.path.join(D, "card_r_payload.json")
    json.dump(card, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[card] written", out, "elements=%d" % len(card["card"]["elements"]))
    if "--send" in sys.argv:
        resp = send(card)
        print("[sent] OK, receipt=%s" % json.dumps(resp, ensure_ascii=False))