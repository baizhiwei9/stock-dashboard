# -*- coding: utf-8 -*-
"""MAD 均线指选股链路（可重复运行）：数据刷新 → 三条件 → 行业前50/概念全量 → 实体本周等权>0 →
分子≥3且资金聚集（拓扑）→ 缠论+SMC 买点 → 剔除宽基 → 一致预期口径 PEG≤1.5 → 出图与报告数据。

用法：
    python3 weekly_screen.py            # 完整链路（刷新数据需联网）
    python3 weekly_screen.py --no-fetch # 只用本地已有数据（离线复跑）
每个阶段的只数会打印，便于与基线（327/216/75/34/114/61/27/79/18）核对。
"""
import argparse
import collections
import csv
import glob
import json
import os
import re
import statistics as st
import subprocess
import sys

import numpy as np
import pandas as pd

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
TS_DIR = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu"
PY_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps"
CHAN_SKILL = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
IFIND_CLI = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/plugins/official/financial-investment-plugin/skills/commons/ifind-data-service/finance_api.py"
PANEL = WS + "/panel"
OHLC = WS + "/panel_ohlc"
OUT = WS + "/weekly_out"
FONT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"

BROAD_EXACT = {"同花顺漂亮100", "同花顺中特估100", "证金持股", "高股息精选", "央企国企改革", "中字头股票", "乡村振兴"}
BROAD_PAT = re.compile(r"融资融券|沪股通|深股通|国企改革|专精特新|中特估|漂亮100|高股息|中字头")
TOP_IND = 50          # 行业取前 50
MIN_NUM = 3           # 分子 ≥3
PEG_MAX = 1.5         # PEG 上限
CHAN_START = "20240601"

sys.path.insert(0, TS_DIR)
sys.path.insert(0, CHAN_SKILL)


def _ssh():
    import ts_http
    return ts_http


# ---------------------------------------------------------------- 1. 数据刷新
def seed_panel():
    """首次运行：把既有全 A 日线缓存复制为面板。"""
    os.makedirs(PANEL, exist_ok=True)
    have = {f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz")}
    src = {f.split("D_")[1][:8] for f in glob.glob(WS + "/cache_ma/D_*.npz")}
    for d in sorted(src - have):
        z = np.load(WS + "/cache_ma/D_%s.npz" % d, allow_pickle=True)
        if "close" not in z.files:            # 少数日期是占位空文件，跳过
            continue
        np.savez_compressed(PANEL + "/D_%s.npz" % d, codes=z["codes"], close=z["close"], adj=z["adj"])
    return have | src


def refresh_panel():
    """按交易日增量刷新全 A 日线面板（close/adj），存为 panel/D_YYYYMMDD.npz。"""
    have = seed_panel()
    src = have
    cal = _ssh()
    last = max(have | src) if (have or src) else None
    try:
        today = pd.Timestamp.today().strftime("%Y%m%d")
        rows = cal.fetch_df("trade_cal", {"exchange": "SSE", "start_date": (last or "20240101"), "end_date": today})
        dates = [str(d) for d, o in zip(rows["cal_date"], rows["is_open"]) if int(o) == 1 and str(d) > (last or "")]
    except Exception as e:
        print("[refresh] 交易日历不可用（%s），仅用本地面板" % type(e).__name__); dates = []
    if True:
        # 重取最近一个已缓存交易日（无论是否有新日期），避免本地缓存里的停牌陈旧价混入
        latest = max(have | src)
        try:
            daily = cal.fetch_df("daily", {"trade_date": latest})
            adj = cal.fetch_df("adj_factor", {"trade_date": latest})
            if len(daily) == 0:
                raise RuntimeError("source empty")
            m = daily.merge(adj[["ts_code", "adj_factor"]], on="ts_code", how="left")
            np.savez_compressed(PANEL + "/D_%s.npz" % latest, codes=m["ts_code"].values,
                                close=m["close"].fillna(0).values, adj=m["adj_factor"].fillna(1).values)
            print("[refresh] 已按数据源重取最近交易日 %s" % latest)
        except Exception as e:
            print("[refresh] 重取最近交易日失败（%s），沿用本地缓存" % type(e).__name__)
        if not dates:
            return sorted({f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz")})
    res = cal.fetch_many("daily", [{"trade_date": d} for d in dates], workers=3)
    adj = cal.fetch_many("adj_factor", [{"trade_date": d} for d in dates], workers=3)
    for d, a, b in zip(dates, res, adj):
        if isinstance(a, Exception) or a is None or len(a) == 0:
            print("[refresh] 跳过 %s（无行情）" % d); continue
        m = a.merge(b[["ts_code", "adj_factor"]], on="ts_code", how="left")
        np.savez_compressed(PANEL + "/D_%s.npz" % d, codes=m["ts_code"].values,
                            close=m["close"].fillna(0).values, adj=m["adj_factor"].fillna(1).values)
    try:                                  # 新日期取完后再重取最新"已缓存"交易日，确保口径干净
        latest = max(f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz"))
        daily = cal.fetch_df("daily", {"trade_date": latest})
        adjf = cal.fetch_df("adj_factor", {"trade_date": latest})
        m = daily.merge(adjf[["ts_code", "adj_factor"]], on="ts_code", how="left")
        np.savez_compressed(PANEL + "/D_%s.npz" % latest, codes=m["ts_code"].values,
                            close=m["close"].fillna(0).values, adj=m["adj_factor"].fillna(1).values)
    except Exception as e:
        print("[refresh] 最新日重取失败（%s）" % type(e).__name__)
    print("[refresh] 新增 %d 个交易日，面板共 %d 天" % (len(dates), len(glob.glob(PANEL + "/D_*.npz"))))
    return sorted({f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz")})


def load_matrix(dates, n=10**6):
    """返回 (dates, codes, 前复权收盘矩阵)；codes 取窗口内所有日期的并集（与既有口径一致）。"""
    ds = sorted(dates)[-n:]
    snaps, allcodes = [], set()
    for d in ds:
        z = np.load(PANEL + "/D_%s.npz" % d, allow_pickle=True)
        if "close" not in z.files: continue
        snaps.append(dict(zip(z["codes"], np.asarray(z["close"], dtype=float) * np.asarray(z["adj"], dtype=float))))
        allcodes |= set(z["codes"])
    codes = sorted(allcodes)
    mat = np.array([[s.get(c, np.nan) for c in codes] for s in snaps], dtype=float)
    mat[~(mat > 0)] = np.nan          # 停牌等零值/负值按缺失处理（与既有口径一致）
    return ds[:len(snaps)], codes, mat


# ---------------------------------------------------------------- 2. 指标与三条件
def build_metrics(dates, codes, M):
    """按个股有效序列（跳过停牌缺口）计算均线与斜率，口径与既有 universe_all 一致。"""
    base = pd.read_csv(WS + "/universe_all.csv", dtype={"ts_code": str}).set_index("ts_code")
    out = []
    for j, c in enumerate(codes):
        s = M[:, j]
        s = s[~np.isnan(s)]
        n = len(s)
        rec = {"ts_code": c, "obs": n}
        if n < 200:
            rec.update({"status": "insufficient_history"})
            out.append(rec); continue
        if np.isnan(M[-1, j]):
            rec.update({"status": "no_trade_on_last_day"})
            out.append(rec); continue
        ma20 = s[-20:].mean(); ma40 = s[-40:].mean(); ma200 = s[-200:].mean()
        lag5 = s[-(40 + 5):-5].mean() if n >= 45 else np.nan
        rec.update({"status": "ok", "close": s[-1], "ma20": ma20, "ma40": ma40, "ma200": ma200,
                    "MAD": ma20 / ma200 - 1, "MAD20": ma20 / ma40 - 1,
                    "MA40斜率5": (ma40 / lag5 - 1) if lag5 and lag5 > 0 else np.nan,
                    "本周": (s[-1] / s[-6] - 1) if n >= 6 and s[-6] > 0 else np.nan})
        if "industry" in base.columns:
            pass
        out.append(rec)
    df = pd.DataFrame(out)
    df = df.merge(base[["name", "industry"]], left_on="ts_code", right_index=True, how="left")
    df["as_of"] = dates[-1]
    return df


def screen(df):
    q = df[(df.status == "ok") & df.MAD.notna() & (df.MAD > 0) & (df["MA40斜率5"] > 0) & (df.MAD20 > 0)].copy()
    return q.reset_index(drop=True)


# ---------------------------------------------------------------- 3-4. 实体与二次过滤
def entities(q, df):
    cons = collections.defaultdict(set)
    for r in pd.read_csv(WS + "/concept_members.csv", dtype={"股票代码": str}).to_dict("records"):
        cons[r["概念名称"]].add(r["股票代码"])
    ind = {}
    for name, g in q.groupby("industry"):
        ind[name] = set(g.ts_code)
    ind_rank = sorted(ind.items(), key=lambda kv: -st.median(q[q.ts_code.isin(kv[1])].MAD20))
    keep_ind = {k for k, _ in ind_rank[:TOP_IND]}
    pool = q[q.industry.isin(keep_ind)]
    cons_use = {k: v & set(pool.ts_code) for k, v in cons.items() if v & set(pool.ts_code)}
    meta = {}
    valid = set(df[(df.status == "ok") & df.ma200.notna()].ts_code)
    for k in keep_ind:
        m = pool[pool.industry == k]
        den = int(((df.industry == k) & df.ts_code.isin(valid)).sum())
        meta[k] = {"类型": "行业", "合格股数": len(m), "全部成分股数": den}
    allcons = collections.defaultdict(set)
    for r in pd.read_csv(WS + "/concept_members.csv", dtype={"股票代码": str}).to_dict("records"):
        allcons[r["概念名称"]].add(r["股票代码"])
    for k, v in cons_use.items():
        meta[k] = {"类型": "概念", "合格股数": len(v), "全部成分股数": len(allcons[k] & valid)}
    ret = pool.set_index("ts_code")["本周"].to_dict()
    rows = []
    for k, v in meta.items():
        codes = set(pool[pool.industry == k].ts_code) if v["类型"] == "行业" else cons_use[k]
        rr = [ret[c] for c in codes if pd.notna(ret.get(c))]
        rows.append({"名称": k, "类型": v["类型"], "合格股数": len(codes), "本周等权": float(np.mean(rr)) if rr else np.nan,
                     "MAD20中位": float(np.median(q[q.ts_code.isin(codes)].MAD20))})
    E = pd.DataFrame(rows)
    return pool, cons_use, E


def topology(pool, cons_use, E):
    """共现网络 + 资金聚集判定（加权度≥10且最大共享≥3，或 加权度≥5且上涨占比≥2/3）。"""
    byk = {}
    for _, r in E.iterrows():
        k = r["名称"]
        byk[k] = set(pool[pool.industry == k].ts_code) if r["类型"] == "行业" else cons_use[k]
    ret = pool.set_index("ts_code")["本周"].to_dict()
    keys = list(byk)
    G = {k: {} for k in keys}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            w = len(byk[a] & byk[b])
            if w: G[a][b] = w; G[b][a] = w
    rows = []
    for k in keys:
        wdeg = sum(G[k].values()); mx = max(G[k].values(), default=0)
        rr = [ret[c] for c in byk[k] if pd.notna(ret.get(c))]
        up = (sum(1 for x in rr if x > 0) / len(rr)) if rr else 0.0
        agg = "资金聚集" if ((wdeg >= 10 and mx >= 3) or (wdeg >= 5 and up >= 2/3)) else "资金扩散"
        rows.append({"名称": k, "度": len(G[k]), "加权度": wdeg, "最大共享": mx, "上涨占比": up, "判定": agg})
    T = pd.DataFrame(rows)
    E = E.merge(T, on="名称", how="left")
    return E


# ---------------------------------------------------------------- 5. 缠论 + SMC
def seed_ohlc(codes):
    os.makedirs(OHLC, exist_ok=True)
    n = 0
    for c in codes:                                   # 复用既有 chan_in_pool 的 OHLC（同口径）
        src = WS + "/chan_in_pool/%s.csv" % c
        if os.path.exists(src) and not os.path.exists(OHLC + "/%s.csv" % c):
            open(OHLC + "/%s.csv" % c, "w", encoding="utf-8").write(open(src, encoding="utf-8").read()); n += 1
    return n


def fetch_ohlc(codes):
    seed_ohlc(codes)
    need = [c for c in codes if not os.path.exists(OHLC + "/%s.csv" % c)]
    if need:
        cal = _ssh()
        end = max(f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz"))
        d = cal.fetch_many("daily", [{"ts_code": c, "start_date": CHAN_START, "end_date": end} for c in need], workers=4)
        a = cal.fetch_many("adj_factor", [{"ts_code": c, "start_date": CHAN_START, "end_date": end} for c in need], workers=4)
        ok = 0
        for c, dd, aa in zip(need, d, a):
            if isinstance(dd, Exception) or dd is None or len(dd) == 0 or isinstance(aa, Exception):
                continue
            m = dd.merge(aa[["trade_date", "adj_factor"]], on="trade_date", how="left")
            m["adj_factor"] = m["adj_factor"].ffill().bfill()
            m = m.sort_values("trade_date")
            la = m.adj_factor.iloc[-1]
            for col in ("open", "high", "low", "close"):
                m[col] = m[col] * m.adj_factor / la
            m["date"] = pd.to_datetime(m.trade_date, format="%Y%m%d")
            m[["date", "open", "high", "low", "close", "vol", "amount"]].rename(columns={"vol": "volume"}) \
                .to_csv(OHLC + "/%s.csv" % c, index=False); ok += 1
        print("[ohlc] 新增 %d 只（申请 %d）" % (ok, len(need)))


def chan_smc(codes):
    import chan_fractal
    out = {}
    for c in sorted(codes):
        p = OHLC + "/%s.csv" % c
        if not os.path.exists(p): continue
        d = pd.read_csv(p, parse_dates=["date"]).reset_index(drop=True)
        n = len(d); h, l, cl, op = d.high.values, d.low.values, d.close.values, d.open.values
        # ---- 缠论 ----
        try:
            r = chan_fractal.analyze(d, level="day", base="struct", name=c)
        except Exception:
            r = {"交易机会": [], "结构": {"笔端点": []}}
        pts = r["结构"]["笔端点"]
        bots = {}
        for i, pt in enumerate(pts):
            if pt.get("类型") != "底": continue
            nx = pts[i + 1] if i + 1 < len(pts) else None
            bots[pt["行号"]] = (str(pt["时间"])[:10], bool(nx is not None and nx.get("类型") == "顶"))
        buys = [(o["行号"], o["类型"], str(o["时间"])[:10]) for o in r["交易机会"] if o.get("方向") == "买入"]
        sells = {o["行号"] for o in r["交易机会"] if o.get("方向") == "卖出"}
        ch = None
        for ln, typ, dt in sorted(buys, reverse=True):
            if n - 1 - ln <= 30 and not any(s > ln for s in sells) and ln in bots:
                ch = {"类型": typ, "日期": dt, "距末日K线": n - 1 - ln, "笔底": bots[ln][0], "向上笔已确认": bots[ln][1]}
                break
        # ---- SMC（修正口径） ----
        bos = []
        for t in range(21, n):
            if cl[t] > h[t-20:t].max() and cl[t-1] <= h[t-21:t-1].max() and (not bos or t - bos[-1] > 5):
                bos.append(t)
        smc = None
        for t in bos:
            zones = []
            for i in range(t, max(0, t-5) - 1, -1):
                if cl[i] < op[i]: zones.append(("OB", l[i], h[i], i)); break
            for i in range(t, max(1, t-5) - 1, -1):
                if l[i] > h[i-2]: zones.append(("FVG", h[i-2], l[i], i)); break
            for kind, zd, zg, zb in zones:
                ft = None; dead = False
                for u in range(zb + 1, n):
                    if cl[u] < zd or l[u] <= zd: dead = True; break
                    if l[u] <= zg: ft = u; break
                if ft is not None and not dead and cl[ft] > zd and cl[ft] > op[ft] and (n - 1 - ft) <= 30:
                    cand = {"类型": kind, "区间下沿": float(zd), "区间上沿": float(zg), "买点日": str(d.date[ft].date()),
                            "买点价": float(cl[ft]), "距末日K线": n - 1 - ft}
                    if smc is None or cand["距末日K线"] < smc["距末日K线"]: smc = cand
        out[c] = {"缠论": ch, "SMC": smc}
    return out


# ---------------------------------------------------------------- 6. PE 与一致预期
def ifind_pe(codes):
    b = 20; res = {}
    for i in range(0, len(codes), b):
        prm = {"symbols": list(codes[i:i+b]), "start": "2026-10-30", "end": "2026-10-30", "fields": ["close", "pe_ttm"]}
        p = subprocess.run([sys.executable, IFIND_CLI, "--function", "get_price_history", "--params", json.dumps(prm)],
                           capture_output=True, text=True, timeout=180)
        try:
            js = json.loads(p.stdout)
        except Exception:
            continue
        for r in js.get("rows", []):
            res[r.get("thscode")] = r.get("pe_ttm")
    return res


def consensus(codes):
    cal = _ssh()
    end = max(f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz"))
    res = cal.fetch_many("report_rc", [{"ts_code": c, "start_date": "20260101", "end_date": end} for c in codes], workers=4)
    big = pd.concat([d for d in res if not isinstance(d, Exception) and len(d)], ignore_index=True) if any(
        (not isinstance(d, Exception) and len(d)) for d in res) else pd.DataFrame()
    out = {}
    if not big.empty:
        big = big.copy()
        big["year"] = big["quarter"].astype(str).str.extract(r"^(\d{4})Q4$")[0]
        big = big[big.year.notna() & big.np.notna() & (big.np > 0)]
        big["report_date"] = big.report_date.astype(str)
        for c, g in big.groupby("ts_code"):
            latest = g.sort_values("report_date").groupby(["year", "org_name"]).tail(1)
            med = latest.groupby("year").np.median()
            n26, n27 = med.get("2026"), med.get("2027")
            g27 = (n27 / n26 - 1) * 100 if (n26 and n26 > 0 and pd.notna(n27)) else None
            out[c] = {"FY1增速": None, "2027增速": g27, "机构数": int(latest.org_name.nunique()) if hasattr(latest, "org_name") else None,
                      "报告数": int(len(g))}
            if latest.empty is False:
                out[c]["机构数"] = int(latest.org_name.nunique())
    return out


def cache_file(name):
    return OUT + "/cache_%s.json" % name


def cache_load(name, as_of):
    """只认与当次数据时点一致的缓存，避免把上一期估值当本期用。"""
    p = cache_file(name)
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    return d.get("data", {}) if d.get("as_of") == as_of else {}


def cache_save(name, as_of, data):
    json.dump({"as_of": as_of, "data": data}, open(cache_file(name), "w", encoding="utf-8"),
              ensure_ascii=False, default=str)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    if args.no_fetch:
        seed_panel()
        dates = sorted(f.split("D_")[1][:8] for f in glob.glob(PANEL + "/D_*.npz"))
    else:
        dates = refresh_panel()
    ds, codes, M = load_matrix(dates, 10**6)
    print("[1] 面板 %s 共 %d 天 / %d 只" % (ds[-1], len(ds), len(codes)))
    df = build_metrics(ds, codes, M)
    q = screen(df)
    print("[2] 三条件合格股：%d 只" % len(q))

    pool, cons_use, E = entities(q, df)
    print("[3] 行业前%d + 概念全量：实体 %d 个，保留池 %d 只" % (TOP_IND, len(E), len(pool)))
    E = E[E["本周等权"] > 0].reset_index(drop=True)
    print("[4] 实体本周等权>0：%d 个" % len(E))

    E = topology(pool, cons_use, E)
    sel = E[(E["合格股数"] >= MIN_NUM) & (E["判定"] == "资金聚集")]
    print("[5] 分子≥%d 且资金聚集：实体 %d 个" % (MIN_NUM, len(sel)))
    sel_codes = set()
    for _, r in sel.iterrows():
        sel_codes |= set(pool[pool.industry == r["名称"]].ts_code) if r["类型"] == "行业" else cons_use[r["名称"]]
    print("    合格股并集：%d 只" % len(sel_codes))

    if not args.no_fetch:
        fetch_ohlc(sorted(sel_codes))
    else:
        seed_ohlc(sorted(sel_codes))
    smc_map = chan_smc(sel_codes)
    hit = {}
    for c in sorted(sel_codes):
        s = smc_map.get(c, {})
        a, b = s.get("缠论") is not None, s.get("SMC") is not None
        hit[c] = "缠论+SMC" if (a and b) else ("仅缠论" if a else ("仅SMC" if b else "无"))
    n_hit = sum(1 for v in hit.values() if v != "无")
    print("[6] 缠论/SMC 买点：池 %d 只中有买点 %d 只（双命中 %d）" % (
        len(sel_codes), n_hit, sum(1 for v in hit.values() if v == "缠论+SMC")))

    keep = [e for e in sel["名称"] if e not in BROAD_EXACT]
    drop = [e for e in sel["名称"] if e in BROAD_EXACT]
    narrow_codes = set()
    for _, r in sel[sel["名称"].isin(keep)].iterrows():
        narrow_codes |= set(pool[pool.industry == r["名称"]].ts_code) if r["类型"] == "行业" else cons_use[r["名称"]]
    print("[7] 剔除宽基 %d 个实体后：保留 %d 个实体 / %d 只合格股" % (len(drop), len(keep), len(narrow_codes)))

    as_of = ds[-1]
    pe_cache, cg_cache = cache_load("pe", as_of), cache_load("consensus", as_of)
    if args.no_fetch:
        pe, cg = pe_cache, cg_cache
        print("[8a] 离线复跑：PE %d 只 / 一致预期 %d 只 取自本地缓存（时点 %s）" % (len(pe), len(cg), as_of))
        if not pe or not cg:
            print("[8a] 同口径缓存缺失，无法离线计算 PEG；请先跑一次联网完整链路"); sys.exit(3)
    else:
        pe_f = ifind_pe(sorted(narrow_codes)) or {}
        cg_f = consensus(sorted(narrow_codes)) or {}
        pe = {c: (pe_f.get(c) if pe_f.get(c) is not None else pe_cache.get(c)) for c in narrow_codes}
        cg = {c: ((cg_f.get(c) if (cg_f.get(c) or {}).get("2027增速") is not None else cg_cache.get(c)) or {})
              for c in narrow_codes}
        cache_save("pe", as_of, pe); cache_save("consensus", as_of, cg)
        print("[8a] PE/一致预期：本次取到 %d / %d 只，其余按同口径缓存补齐并留存（时点 %s）"
              % (len(pe_f), len(cg_f), as_of))
    rows = []
    for c in sorted(narrow_codes):
        g = cg.get(c) or {}
        g27 = g.get("2027增速")
        pev = pe.get(c)
        peg = (pev / g27) if (pev and pev > 0 and g27 and g27 > 0) else None
        rows.append({"ts_code": c, "名称": df.set_index("ts_code").loc[c, "name"], "命中": hit.get(c, "无"),
                     "PE_TTM": pev, "增速2027": g27, "机构数": g.get("机构数"), "PEG": peg})
    P = pd.DataFrame(rows)
    ok = P[(P["命中"] != "无") & (P.PEG.notna()) & (P.PEG <= PEG_MAX)].sort_values("PEG")
    print("[8] PEG≤%.1f：%d 只（PEG≤1 的 %d 只）" % (PEG_MAX, len(ok), int((ok.PEG <= 1).sum())))
    if ok.empty:
        print("[8] 合格名单为空：中止落盘与推送，保留上一期产物（不发空报告）"); sys.exit(4)

    # ---- 落盘 ----
    df.to_csv(OUT + "/metrics.csv", index=False, encoding="utf-8-sig")
    E.to_csv(OUT + "/entities.csv", index=False, encoding="utf-8-sig")
    P.to_csv(OUT + "/peg_all.csv", index=False, encoding="utf-8-sig")
    ok.to_csv(OUT + "/qualified.csv", index=False, encoding="utf-8-sig")
    card = {
        "as_of": ds[-1], "ladder": {"三条件": len(q), "行业前50池": len(pool), "实体>0": int(len(E)),
                                    "分子≥3且聚集": len(sel), "池内合格股": len(sel_codes),
                                    "有买点": n_hit, "剔除宽基后实体": len(keep), "PEG≤1.5": len(ok)},
        "qualified": ok.to_dict("records"),
    }
    json.dump(card, open(OUT + "/report_data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[9] 落盘 weekly_out/（metrics/entities/peg_all/qualified/report_data）")
    return card


if __name__ == "__main__":
    main()