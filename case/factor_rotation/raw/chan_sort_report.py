# -*- coding: utf-8 -*-
"""把 output/chan_layer_scan.csv 按"机会强度"分层排序，输出三层名单表。
机会排序规则（由强到弱）：
  组1 近40个交易日内出现买点：右侧确认 B2/B3 优先于左侧 B1，同类按日期越新越前
  组2 无近40日买点但最新收盘有效突破最新中枢上沿 ZG（且突破发生在该中枢成立之后）
  组3 更早（>40日）的买点，仅作历史结构参考
  组4 无买点且无有效突破
"""
import json
import pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
SRC = f"{BASE}/output/chan_layer_scan.csv"
OUT_CSV = f"{BASE}/output/chan_opportunity_rank.csv"
OUT_JSON = f"{BASE}/output/chan_opportunity_summary.json"

TYPE_W = {"B2": 3, "B3": 3, "B1": 2}  # 右侧确认优先


def tier_of(r):
    recent = pd.notna(r["近40日买点类型"])
    brk = bool(r["突破中枢"]) and bool(r["突破在中枢后"])
    has_bp = pd.notna(r["最新买点类型"])
    if recent:
        return 1
    if brk:
        return 2
    if has_bp:
        return 3
    return 4


def sig_type(r):
    if pd.notna(r["近40日买点类型"]):
        return r["近40日买点类型"]
    if pd.notna(r["最新买点类型"]):
        return r["最新买点类型"]
    return None


def sig_ago(r):
    if pd.notna(r["近40日买点距今天"]):
        return float(r["近40日买点距今天"])
    if pd.notna(r["最新买点距今天"]):
        return float(r["最新买点距今天"])
    return 9999.0


def sort_key(r):
    t = r["_tier"]
    ty = r["_sig"]
    tw = TYPE_W.get(ty, 0)
    ago = r["_ago"]
    # 组1 内：右侧(3)优先，再按日期新；组2 内按突破距今天；
    return (t, -tw, ago, r["_brk_ago"])


def prep(df):
    df = df.copy()
    df["_tier"] = df.apply(tier_of, axis=1)
    df["_sig"] = df.apply(sig_type, axis=1)
    df["_ago"] = df.apply(sig_ago, axis=1)
    df["_brk_ago"] = df["突破距今天"].fillna(9999).astype(float)
    df["_brk"] = df.apply(lambda r: bool(r["突破中枢"]) and bool(r["突破在中枢后"]), axis=1)
    df["_sig_txt"] = df.apply(
        lambda r: ("突破中枢" if (r["_brk"] and pd.isna(r["_sig"])) else (r["_sig"] if pd.notna(r["_sig"]) else "无")),
        axis=1,
    )
    df["_sig_date"] = df.apply(
        lambda r: (r["突破日期"] if (r["_brk"] and pd.isna(r["近40日买点类型"]) and pd.isna(r["最新买点类型"]))
                   else (r["近40日买点日期"] if pd.notna(r["近40日买点日期"]) else r["最新买点日期"])),
        axis=1,
    )
    df["_sig_ago_txt"] = df.apply(
        lambda r: (r["突破距今天"] if (r["_brk"] and pd.isna(r["近40日买点类型"]) and pd.isna(r["最新买点类型"]))
                   else (r["近40日买点距今天"] if pd.notna(r["近40日买点距今天"]) else r["最新买点距今天"])),
        axis=1,
    )
    df["_fresh"] = df.apply(
        lambda r: ("近40日" if pd.notna(r["近40日买点类型"]) else ("突破中枢" if r["_brk"] else ("历史" if pd.notna(r["最新买点类型"]) else "-"))),
        axis=1,
    )
    return df.sort_values(by=["_tier", "_sig", "_ago"], key=lambda s: s.map(lambda v: v) if s.name == "_sig" else s,
                          ascending=True) if False else df


if __name__ == "__main__":
    d = pd.read_csv(SRC)
    d = prep(d)
    rows = []
    summary = {}
    for L in ["行业", "主题", "概念"]:
        s = d[d["层级"] == L].copy()
        # 组内按"新鲜度"（距今天升序）排；同一天右侧确认(B2/B3)排在左侧(B1)之前
        s["_k"] = s.apply(
            lambda r: (r["_tier"], r["_ago"] if r["_tier"] != 2 else r["_brk_ago"],
                       -TYPE_W.get(r["_sig"] or "", 0)), axis=1)
        s = s.sort_values("_k", kind="mergesort")
        # 每层计数
        n = len(s)
        n_recent = int((s["_tier"] == 1).sum())
        n_brkonly = int((s["_tier"] == 2).sum())
        n_hist = int((s["_tier"] == 3).sum())
        n_none = int((s["_tier"] == 4).sum())
        type_cnt = s["_sig"].dropna().value_counts().to_dict()
        summary[L] = {
            "n_total": n,
            "n_chance": n_recent + n_brkonly,
            "n_recent_bp": n_recent,
            "n_brk_only": n_brkonly,
            "n_hist_bp": n_hist,
            "n_none": n_none,
            "recent_type": s[s["_tier"] == 1]["_sig"].value_counts().to_dict(),
            "all_bp_type": type_cnt,
        }
        for _, r in s.iterrows():
            rows.append({
                "层级": L,
                "机会组": {1: "①近40日买点", 2: "②突破中枢", 3: "③较早买点", 4: "④无信号"}[r["_tier"]],
                "名称": r["名称"],
                "因子名次": int(r["因子名次"]),
                "信号": r["_sig_txt"],
                "信号日期": r["_sig_date"],
                "距今天": r["_sig_ago_txt"],
                "新鲜度": r["_fresh"],
                "近40日买点": r["近40日买点类型"] if pd.notna(r["近40日买点类型"]) else "",
                "最新买点": r["最新买点类型"] if pd.notna(r["最新买点类型"]) else "",
                "最新买点日期": r["最新买点日期"] if pd.notna(r["最新买点日期"]) else "",
                "中枢上沿ZG": round(float(r["中枢上沿ZG"]), 2),
                "最新收盘": round(float(r["最新收盘"]), 2),
                "突破中枢": bool(r["突破中枢"]),
                "突破在中枢后": bool(r["突破在中枢后"]),
                "均线版最新买点": r["均线版最新买点"] if pd.notna(r["均线版最新买点"]) else "",
                "均线版买点日期": r["均线版最新买点日期"] if pd.notna(r["均线版最新买点日期"]) else "",
            })
    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n[out]", OUT_CSV, len(out))