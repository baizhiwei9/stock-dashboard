# -*- coding: utf-8 -*-
"""render 阶段：计算周期与情绪，生成两份 HTML 报告 + out/report.md 摘要
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")
OUT = os.environ.get("CASE_OUT_DIR") or os.path.join(HERE, "out")
SRC_DATA = os.path.join(HERE, "output", "data")
SRC_REPORT = os.path.join(HERE, "output", "report")

STEPS = [
    ("计算出清周期", "calc_cycle.py"),
    ("计算情绪周期", "calc_emotion.py"),
    ("生成两份报告", "build_reports.py"),
    ("双分位告警判定", "calc_alert.py"),
]


def run(name, script):
    print(f"--- [{name}] {script}", flush=True)
    r = subprocess.run([sys.executable, "-u", script], cwd=SCRIPTS)
    if r.returncode != 0:
        raise SystemExit(f"{name} 失败，退出码 {r.returncode}")


def _src_data():
    """取数产物优先读契约目录 data/，回退脚本原生目录 output/data/。"""
    for p in (os.path.join(HERE, "data"), SRC_DATA):
        if os.path.exists(os.path.join(p, "cycle.json")):
            return p
    return SRC_DATA


def load(name, default=None):
    p = os.path.join(_src_data(), name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build_markdown(c, e, a=None):
    from datetime import datetime as _dt

    ma, p, b = c["ma"], c["passive"], c["basis"]
    bi = b["IF"]
    m = e["metrics"]
    mg = m.get("margin") or {}
    qv = m.get("qvix") or {}

    # 14:50 跑：日线未收，盘中的 MA / 分位与收盘价会有差异，报告头部标注
    _now = _dt.now()
    intra = "　|　⚠️ 盘中数据，非收盘价" if _now.hour * 60 + _now.minute < 15 * 60 else ""

    if ma["in_clearing"]:
        head = f"出清中，第 {ma['days']} 个交易日（{ma['since']} 下穿起）"
        action = "继续发送"
    else:
        head = f"出清已完成（{ma['since']} 上穿）"
        action = "停止发送"

    lines = [
        f"# A股出清周期监控 · {c['as_of']}",
        "",
        f"**周期状态**：{head}{intra}　|　报告动作：{action}",
        "",
        "## 一、周期边界（沪深300 MA40 / MA80）",
        "",
        "| 项目 | 数值 |",
        "| --- | --- |",
        f"| 沪深300 收盘 | {ma['close']:,.2f} |",
        f"| MA40 | {ma['ma_fast']:,.2f} |",
        f"| MA80 | {ma['ma_slow']:,.2f} |",
        f"| MA40 − MA80 | {ma['dif']:,.2f}（{ma['gap_pct']:.2f}%） |",
        f"| 横盘收敛速度 | {ma['converge_speed']:.2f} 点/日 |",
        f"| 修复测算 | 约 {ma['flat_days']} 个交易日 |" if ma.get("flat_days") else "| 修复测算 | — |",
        "",
        "## 二、进度指标",
        "",
        "| 指标 | 读数 | 状态 |",
        "| --- | --- | --- |",
        f"| 被动化率（Wind 分母） | {p['value_pct']:.2f}% | {'已过 7% 提醒线（并入双分位邮件，不单独发信）' if p['value_pct'] >= 7 else '计时中'} |",
        f"| 被动化率（土狗刻度） | {p['value_pct_dog']:.2f}% | — |",
        f"| {bi['contract']} 年化基差 | {bi['annual_pct']:.2f}% | {'正常带内' if bi['in_normal'] else '偏离正常带'} |",
        f"| 距 5 日高点回落 | {bi['drop_pp']:.2f}pp（高 {bi['high5']:.2f}% @ {bi['high5_date']}） | {'修复到位' if bi['triggered'] else '未到位'} |",
        "",
        "## 三、情绪温度（独立模块）",
        "",
        f"**{e['heat']} / 100 → {e['stage']}**：{e['meaning']}",
        "",
        "| 维度 | 得分 |",
        "| --- | --- |",
    ]
    for k, v in e["parts"].items():
        lines.append(f"| {k} | {v:+d} |")
    lines += [
        "",
        f"涨跌 {m['up']} / {m['down']}，涨停 {m['limit_up']} / 跌停 {m['limit_down']}，"
        f"中位数 {m['median_chg']:+.2f}%，全A 成交 {(m['amount_yi_all'] or 0) / 1e4:.2f} 万亿；"
        f"融资余额 {mg.get('fin_bal_yi', 0):,.0f} 亿（20日 {mg.get('chg20_pct', 0):+.2f}%）；"
        f"QVIX {qv.get('value', 0):.2f}（{qv.get('rank_250')}% 分位）。",
        "",
    ]

    if a:
        qvp, bpp = a.get("qvix_pct"), a.get("basis_pct")
        lines += [
            "## 四、波动率 × 基差 双分位（邮件告警口径）",
            "",
            "| 指标 | 读数 | 250日分位 | 门槛 |",
            "| --- | --- | --- | --- |",
            f"| QVIX（300ETF 隐波） | {a.get('qvix_value')} | {qvp}% | 70% |",
            f"| {a.get('basis_contract')} 年化基差 | {a.get('basis_annual')}% | {bpp}%（{a.get('basis_hist_days')} 日样本） | 70% |",
            "",
            f"**当前判定**：{a.get('state_label')}（自 {a.get('since')}）",
            "",
            "| 状态 | 条件 | 动作 |",
            "| --- | --- | --- |",
            "| 双高 | QVIX 分位 ≥70 且 基差分位 ≥70 | 发邮件提醒 |",
            "| 双低 | 两者同时 <70 | 发邮件提醒（出清） |",
            "| 分化 | 一高一低 | 不动，维持上一状态 |",
            "",
            "基差用「现货−期货」年化率（正=贴水），分位越高＝贴水越深＝基差越大，与 QVIX 同向。",
            "只在状态切换时发信，同状态不重复。被动化率 7% 提醒线已并入本邮件正文，不单独发信。",
            "",
        ]

    lines += [
        "## 五、触发规则",
        "",
        "| 事件 | 判定 | 动作 |",
        "| --- | --- | --- |",
        "| 出清开始 | MA40 下穿 MA80 | 开始发送 |",
        "| 出清完成 | MA40 上穿 MA80 | 停止发送 |",
        "| 被动化率 | 4.9% / 7.0% / 8.5% | 起计时 / 提醒（并入双分位邮件）/ 上沿 |",
        "| 基差 | 距 5 日高回落 ≥5pp | 修复到位 |",
        "| 双分位双高 | QVIX ≥70% 且 基差 ≥70% | 发风险邮件 |",
        "| 双分位双低 | 两者同时 <70% | 发出清邮件 |",
        "",
        "情绪温度是位置指标，不是择时信号；出清完成与否只看均线。",
        "",
        f"数据来源：公开行情接口（akshare / 交易所与中证指数公开接口），口径见上表；"
        f"数据截止 {c['as_of']}。本报告为统计描述，不构成投资建议。",
    ]
    return "\n".join(lines) + "\n"


def main():
    for name, script in STEPS:
        run(name, script)

    os.makedirs(OUT, exist_ok=True)
    n = 0
    if os.path.isdir(SRC_REPORT):
        for f in os.listdir(SRC_REPORT):
            if f.endswith(".html"):
                shutil.copy2(os.path.join(SRC_REPORT, f), os.path.join(OUT, f))
                n += 1

    c = load("cycle.json")
    e = load("emotion.json")
    a = load("alert.json")
    md = build_markdown(c, e, a)
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(f"报告已生成：out/report.md + {n} 份 HTML")


if __name__ == "__main__":
    main()
