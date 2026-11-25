# -*- coding: utf-8 -*-
"""多空转换提醒 · 判定与结论留存（单只基金 · 方法级）

对比相邻两次日报中**每一只宽基内部、逐个技术方法**的多空观点，
统计该基金当日发生「由多转空（买→卖）/ 由空转多（卖→买）」的方法数量；
**单只基金翻转方法数严格大于阈值（默认 3，即 ≥4 个）** 时生成提醒文本，
由主流程决定是否发邮件。

设计要点：
- 触发对象是**单只基金**：任一只宽基内部翻转方法数 > 阈值即触发，跨基金不累加；
- 只统计 买↔卖 的双向翻转，买→震荡 / 震荡→买 / 卖→震荡 / 震荡→卖 均不算「转向」；
- 首次运行（无可用基线）不触发；旧版仅存汇总结论的记录无法做方法级对比，同样视为无基线；
- 每次运行都会把当日**逐基金逐方法**观点写入历史文件，供下一交易日对比。
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state", "consensus_history.json")

THRESHOLD = 3       # 触发阈值：单只基金内翻转方法数 **严格大于** 该值（即 ≥4）时触发
MAX_HISTORY = 120   # 历史最多保留的交易天数

# (前一日方法观点, 当日方法观点) -> 转换方向
FLIP_LABEL = {("买", "卖"): "由多转空", ("卖", "买"): "由空转多"}


def load_history(path=STATE_PATH):
    """读取历史观点 {日期: {code: {method: view}}}；文件缺失或损坏时返回空字典。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_history(history, path=STATE_PATH):
    """原子写入历史观点，仅保留最近 MAX_HISTORY 个交易日。"""
    keys = sorted(history.keys())
    if len(keys) > MAX_HISTORY:
        for k in keys[:-MAX_HISTORY]:
            history.pop(k, None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def latest_before(history, cur_date):
    """取 cur_date 之前最近一次记录的 (日期, 观点)；无则返回 (None, None)。"""
    past = [d for d in history.keys() if str(d) < str(cur_date)]
    if not past:
        return None, None
    k = max(past)
    return k, history[k]


def detect_flips(prev, cur, pool):
    """按单只基金逐一对比各方法观点，统计方向翻转。

    prev: {code: {method: view}} 上一交易日观点；cur: {code: {method: view}} 当日观点；
    pool: [{"code":..., "name":...}, ...]
    返回 [{"code","name","count","n_long2short","n_short2long",
            "methods":[{"method","prev","cur","label"}, ...]}, ...]
    仅返回 count>0 的基金；旧版（值为字符串）记录视为无基线、跳过。
    """
    out = []
    if not prev:
        return out
    for e in pool:
        code = e["code"]
        pv_map, cv_map = prev.get(code), cur.get(code)
        if not isinstance(pv_map, dict) or not isinstance(cv_map, dict):
            continue  # 旧版汇总结论无方法级基线
        methods = []
        for m, cv in cv_map.items():
            pv = pv_map.get(m)
            if pv is None:
                continue
            label = FLIP_LABEL.get((pv, cv))
            if label:
                methods.append({"method": m, "prev": pv, "cur": cv, "label": label})
        if not methods:
            continue
        methods.sort(key=lambda x: (x["label"], x["method"]))
        n_l2s = sum(1 for x in methods if x["label"] == "由多转空")
        n_s2l = sum(1 for x in methods if x["label"] == "由空转多")
        out.append({"code": code, "name": e["name"], "count": len(methods),
                    "n_long2short": n_l2s, "n_short2long": n_s2l,
                    "methods": methods})
    return out


def _triggered(fund_flips, threshold):
    """筛出掉头方法数严格大于阈值的基金，按数量降序。"""
    hit = [f for f in fund_flips if f["count"] > threshold]
    hit.sort(key=lambda f: (-f["count"], f["code"]))
    return hit


def build_alert(asof, fund_flips, threshold=THRESHOLD):
    """生成多空转换提醒正文（Markdown）；无基金达到阈值时返回 None。"""
    hit = _triggered(fund_flips, threshold)
    if not hit:
        return None
    lines = [f"**⚠️ 宽基多空转换提醒 · {asof}**", ""]
    lines.append(f"检测到 **{len(hit)}** 只宽基当日内部技术方法出现大面积转向"
                 f"（单只基金翻转方法数 > {threshold}，即 ≥{threshold + 1} 个）：")
    lines.append("")
    for f in hit:
        lines.append(f"### {f['name']}（{f['code']}）· {f['count']} 个方法翻转"
                     f"（由多转空 {f['n_long2short']} / 由空转多 {f['n_short2long']}）")
        for x in f["methods"]:
            mark = "🔻" if x["label"] == "由多转空" else "🔺"
            lines.append(f"- {mark} {x['method']}：{x['prev']} → {x['cur']}（{x['label']}）")
        lines.append("")
    lines.append("说明：以上为**单只宽基内部**逐方法的多空观点转向（仅统计买↔卖，"
                 f"平/震荡方向不计），单只基金翻转方法数 > {threshold} 即触发、跨基金不累加；"
                 "由历史技术分析框架自动判定，仅供研究参考，不构成投资建议。")
    return "\n".join(lines)


def check_and_build(cur_views, asof, pool, path=STATE_PATH, threshold=THRESHOLD):
    """对比历史 -> 写回当日逐方法观点 -> 判定。

    cur_views: {code: {method: view}}
    返回 (fund_flips, alert_text)；无基金达阈值时 alert_text 为 None。
    """
    history = load_history(path)
    _, prev = latest_before(history, asof)
    fund_flips = detect_flips(prev, cur_views, pool)
    history[asof] = copy.deepcopy(cur_views)
    save_history(history, path)
    return fund_flips, build_alert(asof, fund_flips, threshold)