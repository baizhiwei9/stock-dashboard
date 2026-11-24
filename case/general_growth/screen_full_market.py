# -*- coding: utf-8 -*-
"""全市场（申万三级 + 同花顺概念）条件过滤：C1 体量 / C2 盈利 / C3 估值 / C6 容量

数据全部来自 data/ 下已落盘的全市场快照，不再联网。
输出：data/fm_screen.json（逐对象逐项判定）、data/fm_screen.csv
用法: python3 screen_full_market.py
"""
import csv
import json
import os
import statistics as stc

D = "data"

def load(f, default):
    p = os.path.join(D, f)
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding="utf-8"))


def fnum(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def np_ttm(basic, code):
    """净利润TTM(万元) = 总市值(万元) / PE(TTM)；pe_ttm 缺失/为0 → None"""
    b = basic.get(code)
    if not b:
        return None
    mv = fnum(b.get("total_mv"))
    pe = fnum(b.get("pe_ttm"))
    if mv is None or pe is None or pe == 0:
        return None
    return mv / pe


def main():
    sw = load("fm_sw_l3.json", [])
    ths = load("fm_ths_index.json", [])
    sw_mem = load("fm_sw_members.json", {})
    ths_mem = load("fm_ths_members.json", {})
    b26 = load("fm_basic_20261030.json", {})
    b25 = load("fm_basic_20251031.json", {})
    amt = load("fm_amount_last5.json", {})

    days = sorted([d for d, v in amt.items() if v])
    amt_sum = {}
    for dt in days:
        for c, r in amt[dt].items():
            a = fnum(r.get("amount"))
            if a is not None:
                amt_sum[c] = amt_sum.get(c, 0.0) + a * 0.1  # 千元 -> 万元

    objects = []
    for x in sw:
        objects.append({
            "type": "申万三级", "code": x["index_code"], "name": x["industry_name"],
            "members": (sw_mem.get(x["index_code"], {}) or {}).get("codes", []),
        })
    for x in ths:
        objects.append({
            "type": "概念", "code": x["ts_code"], "name": x["name"],
            "members": (ths_mem.get(x["ts_code"], {}) or {}).get("codes", []),
        })

    out = []
    for o in objects:
        codes = [c for c in o["members"]]
        rec = {"type": o["type"], "code": o["code"], "name": o["name"], "n_members": len(codes)}
        rec["n_valid_mv"] = sum(1 for c in codes if fnum((b26.get(c) or {}).get("total_mv")) is not None)
        rec["mv_yi"] = round(sum(fnum((b26.get(c) or {}).get("total_mv")) or 0 for c in codes) / 1e4, 2)

        # C2 盈利
        growths, n_pos, n_valid, n_neg_base, n_pe_miss = [], 0, 0, 0, 0
        for c in codes:
            n26 = np_ttm(b26, c)
            n25 = np_ttm(b25, c)
            if n26 is None or n25 is None:
                n_pe_miss += 1
                continue
            if n25 <= 0:
                n_neg_base += 1
                continue
            g = n26 / n25 - 1
            growths.append(g)
            n_valid += 1
            if g > 0:
                n_pos += 1
        rec["g_median_pct"] = round(100 * stc.median(growths), 1) if growths else None
        rec["pos_share_pct"] = round(100 * n_pos / n_valid, 1) if n_valid else None
        rec["n_profit_valid"] = n_valid
        rec["n_neg_base"] = n_neg_base
        rec["n_pe_miss"] = n_pe_miss

        # C3 / PE 中位
        pes = [fnum((b26.get(c) or {}).get("pe_ttm")) for c in codes]
        pes = [p for p in pes if p is not None and p > 0]
        rec["pe_median"] = round(stc.median(pes), 1) if pes else None

        # C6 容量
        rec["amt5_yi"] = round(sum(amt_sum.get(c, 0.0) for c in codes) / 1e4, 2)

        # C1 判定
        rec["C1"] = bool(rec["n_valid_mv"] >= 5 and rec["mv_yi"] >= 500)
        # C2 判定
        rec["C2"] = bool(rec["g_median_pct"] is not None and rec["g_median_pct"] > 0
                         and rec["pos_share_pct"] is not None and rec["pos_share_pct"] >= 50)
        # C3 判定
        rec["C3"] = bool(rec["pe_median"] is not None and rec["g_median_pct"] is not None
                         and rec["pe_median"] <= rec["g_median_pct"])
        # C6 判定
        rec["C6"] = bool(rec["amt5_yi"] >= 20)
        rec["pass_base"] = bool(rec["C1"] and rec["C2"] and rec["C3"] and rec["C6"])
        out.append(rec)

    out.sort(key=lambda r: (not r["pass_base"], -(r["mv_yi"] or 0)))
    json.dump(out, open(os.path.join(D, "fm_screen.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    with open(os.path.join(D, "fm_screen.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["类型", "代码", "名称", "成分数", "有效成分数", "合计总市值(亿)",
                    "净利润TTM同比中位(%)", "正增长占比(%)", "盈利有效样本", "基期为负",
                    "PE_TTM中位(倍)", "近5日成交额(亿)", "C1体量", "C2盈利", "C3估值", "C6容量", "基础层通过"])
        for r in out:
            w.writerow([r["type"], r["code"], r["name"], r["n_members"], r["n_valid_mv"], r["mv_yi"],
                        r["g_median_pct"], r["pos_share_pct"], r["n_profit_valid"], r["n_neg_base"],
                        r["pe_median"], r["amt5_yi"],
                        "√" if r["C1"] else "×", "√" if r["C2"] else "×",
                        "√" if r["C3"] else "×", "√" if r["C6"] else "×",
                        "√" if r["pass_base"] else "×"])

    n_sw = sum(1 for r in out if r["type"] == "申万三级")
    n_ths = sum(1 for r in out if r["type"] == "概念")
    n_pass = sum(1 for r in out if r["pass_base"])
    n_no_mem = sum(1 for r in out if r["n_members"] == 0)
    print("对象：申万三级 %d、概念 %d，共 %d；无成分 %d" % (n_sw, n_ths, len(out), n_no_mem))
    print("基础层通过（C1&C2&C3&C6）：%d 个" % n_pass)
    for lbl, key in [("C1体量", "C1"), ("C2盈利", "C2"), ("C3估值", "C3"), ("C6容量", "C6")]:
        print("  %s 通过 %d / %d" % (lbl, sum(1 for r in out if r[key]), len(out)))

    # 机器人链专项
    print("\n=== 机器人产业链专项 ===")
    targets = ["机器人", "电机Ⅲ", "工控设备", "其他自动化设备", "机床工具",
               "减速器", "人形机器人", "机器人概念", "机器视觉", "传感器"]
    for r in out:
        if r["name"] in targets:
            print(" [%s] %-12s 成分%4d 市值%9.1f亿 净利同比中位%8s%% 正增长占比%6s%% PE%7s "
                  "成交%8.2f亿  C1=%s C2=%s C3=%s C6=%s 基础通过=%s" % (
                      r["type"], r["name"], r["n_members"], r["mv_yi"], r["g_median_pct"],
                      r["pos_share_pct"], r["pe_median"], r["amt5_yi"],
                      r["C1"], r["C2"], r["C3"], r["C6"], r["pass_base"]))


if __name__ == "__main__":
    main()