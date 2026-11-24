#!/usr/bin/env python3
"""DoMate 每日总结 - 汇总所有日报推送到飞书 (动态日期版)"""
import json, os, glob, re, urllib.request, sys, importlib
from datetime import datetime, timedelta

# ===== Feishu Config =====
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# ===== Data Paths =====
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
HODGE_DIR = f"{BASE}/ses_00495a421ffeZp6sKsQZoGR6e9"
RMT_DIR   = f"{BASE}/ses_gffe5fcc907173ffeEGsG0e1KvWP05b"
PHYGEO    = f"{BASE}/ses_gffe5fde225ad5ffePQAojl8RuNMiuQ/results.json"
OTSIGNAL  = f"{BASE}/skills/user/optimal-transport-ot/ot_signal.json"
TECHREPORT = f"{BASE}/ses_gffe5f28eb4508ffeXvGpuDQNknTjQn/tech_consensus_daily/output/text_report.md"
# 市场临界监测 C(t)&Gap（由同目录另一任务脚本 rmt_cgap_daily.py 产出，规则单一来源 cgap_rules.py）
CGAP_TODAY = f"{RMT_DIR}/rmt_cgap_today.json"
CGAP_HIST  = f"{RMT_DIR}/rmt_cgap_history.json"

def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def find_latest_hodge():
    """查找最新的 Hodge 报告文件"""
    today = datetime.now()
    for days_back in range(0, 5):
        d = today - timedelta(days=days_back)
        fname = os.path.join(HODGE_DIR, f"hlm_report_{d.strftime('%Y-%m-%d')}.json")
        if os.path.exists(fname):
            return fname
    # fallback: glob 最新
    files = sorted(glob.glob(os.path.join(HODGE_DIR, "hlm_report_*.json")))
    return files[-1] if files else None

def find_latest_rmt():
    """查找最新的 RMT 报告文件"""
    today = datetime.now()
    for days_back in range(0, 5):
        d = today - timedelta(days=days_back)
        fname = os.path.join(RMT_DIR, f"rmt_report_{d.strftime('%Y%m%d')}.json")
        if os.path.exists(fname):
            return fname
    # fallback: glob 最新
    files = sorted(glob.glob(os.path.join(RMT_DIR, "rmt_report_*.json")))
    return files[-1] if files else None

def safe_load(path, label):
    if path and os.path.exists(path):
        try:
            return load(path)
        except Exception as e:
            print(f"WARN: {label} load failed: {e}")
            return None
    print(f"WARN: {label} file not found: {path}")
    return None

# ===== 动态查找数据 =====
today_str = datetime.now().strftime("%Y-%m-%d")
today_short = datetime.now().strftime("%-m月%-d日")

hodge_path = find_latest_hodge()
rmt_path   = find_latest_rmt()

print(f"Hodge report: {hodge_path}")
print(f"RMT report: {rmt_path}")

hodge = safe_load(hodge_path, "Hodge")
rmt   = safe_load(rmt_path, "RMT")
phygeo= safe_load(PHYGEO, "Physics_Geo")
otsig = safe_load(OTSIGNAL, "OT")

tech_text = None
if os.path.exists(TECHREPORT):
    try:
        with open(TECHREPORT, "r", encoding="utf-8") as tf:
            tech_text = tf.read()
    except Exception as e:
        print(f"WARN: Tech_Consensus load failed: {e}")
else:
    print(f"WARN: Tech_Consensus file not found: {TECHREPORT}")

# ===== 数据新鲜度闸门：任何信号文件落后超过 5 天即拒绝汇总 =====
def _fresheness_ok(path, label):
    """校验数据文件日期新鲜度：fallback 到旧文件时禁止静默汇总"""
    if not path or not os.path.exists(path):
        return True, None  # 缺文件交由核心缺失逻辑处理
    try:
        d = load(path)
        dstr = d.get("date") or d.get("date_str") if isinstance(d, dict) else None
        if not dstr:
            return True, None
        fdate = datetime.strptime(str(dstr)[:10], "%Y-%m-%d")
        lag = (datetime.now() - fdate).days
        if lag > 5:
            return False, f"{label} 数据日期 {dstr} 距今 {lag} 天 (>5), 拒绝纳入汇总"
        return True, None
    except Exception:
        return True, None

stale = []
for path, label in [(hodge_path, "Hodge"), (rmt_path, "RMT"),
                    (PHYGEO, "Physics_Geo"),
                    (OTSIGNAL, "OT")]:
    ok, reason = _fresheness_ok(path, label)
    if not ok:
        stale.append(reason)

# 技术分析汇总报告为文本文件，单独校验其报告日期新鲜度
if tech_text:
    m = re.search(r"日报 · (\d{4}-\d{2}-\d{2})", tech_text)
    if m:
        try:
            fdate = datetime.strptime(m.group(1), "%Y-%m-%d")
            lag = (datetime.now() - fdate).days
            if lag > 5:
                stale.append(f"Tech_Consensus 数据日期 {m.group(1)} 距今 {lag} 天 (>5), 拒绝纳入汇总")
        except Exception:
            stale.append("Tech_Consensus 报告日期解析失败")
    else:
        stale.append("Tech_Consensus 报告未包含日期行, 拒绝纳入汇总")

if stale:
    print("ERROR: " + " | ".join(stale))
    sys.exit(1)

# 如果关键数据缺失, 直接退出
if not hodge or not rmt or not phygeo:
    print("ERROR: 核心数据缺失, 无法生成汇总")
    sys.exit(1)

otsig = otsig or {"signals": [], "summary": {"bull": 0, "bear": 0, "wait": 0}}

# ===== 市场临界监测（cgap）：采用另一任务脚本的判定，由等级驱动仓位 =====
# 规则单一来源：RMT_DIR/cgap_rules.py（与 build_cgap_html.py / send_cgap_feishu.py 共用同一模块）。
# 仓位映射（由临界监测等级驱动）：红警→减仓避险、橙警→减仓观望、黄警→控制新增仓位、无预警→持有；
# 达最低点（恐慌底）时转抄底加仓，达最高点（抱团拥挤顶）时卖出减仓，具体口径见卡片「状态→操作对照」。
# 说明：来源 cgap_rules 只定义 level/tag/advice，未定义仓位百分比；故这里的等级→仓位百分比为汇总层展示策略，
# 等级判定、触发原因与建议文本均引用来源（cgap_rules.evaluate_rules）。
CGAP_POS = {"red": 0.30, "orange": 0.60, "yellow": 0.85, "none": 1.00}
CGAP_ACT = {"red": "减仓避险", "orange": "减仓观望", "yellow": "控制新增仓位", "none": "持有"}

cgap_t = safe_load(CGAP_TODAY, "CgapToday")
cgap_h = safe_load(CGAP_HIST, "CgapHist")
cgap_R = None
cgap_note = ""
if cgap_t and cgap_h:
    try:
        _cd = datetime.strptime(str(cgap_t.get("date", ""))[:10], "%Y-%m-%d")
        if (datetime.now() - _cd).days > 5:
            cgap_note = f"临界监测(cgap)数据日期 {cgap_t.get('date')} 滞后 >5 天，本次不驱动仓位"
            print("WARN: " + cgap_note)
        else:
            if RMT_DIR not in sys.path:
                sys.path.insert(0, RMT_DIR)
            from cgap_rules import evaluate_rules as _cgap_eval
            cgap_R = _cgap_eval(cgap_t, cgap_h)
    except Exception as e:
        print(f"WARN: cgap rules eval failed: {e}")
        cgap_note = f"临界监测(cgap)判定失败：{e}"
else:
    cgap_note = "临界监测(cgap)数据缺失"

# ===== ETF 代码->名称映射（引用来源任务脚本的 ETF_NAMES，不在本地复制一份） =====
def _source_etf_names():
    """引用来源任务的 ETF 名称表：Hodge(hlm_daily_monitor) 与 RMT(rmt_daily_monitor) 的 ETF_NAMES 并集。"""
    names = {}
    for d, mod in ((HODGE_DIR, "hlm_daily_monitor"), (RMT_DIR, "rmt_daily_monitor")):
        try:
            if d and d not in sys.path:
                sys.path.insert(0, d)
            names.update(getattr(importlib.import_module(mod), "ETF_NAMES", {}) or {})
        except Exception as e:  # noqa: BLE001
            print(f"WARN: 引用 {mod}.ETF_NAMES 失败: {e}")
    return names

etf_names = _source_etf_names()

# ===== 1. 宽基指数汇总 =====
idx_data = phygeo.get("indices", {})
idx_lines = []
for key in ["HS300","CSI500","CSI1000","SCI50","GEM"]:
    if key not in idx_data:
        continue
    d = idx_data[key]
    f = d.get("fusion", {})
    g = d.get("geometry", {})
    wr = d.get("weekly_ret", 0)
    wr_str = f"+{wr}%" if wr >= 0 else f"{wr}%"
    pe = d.get("pe", 0)
    pe_pct = d.get("pe_pct", 0)
    idx_lines.append(
        f"**{d['name']}** | {f.get('state','')} | {f.get('judgment','')}\n"
        f"周涨跌: {wr_str} | PE: {pe} ({pe_pct}%分位) | "
        f"Morse: {g.get('morse','')} | 流形: {g.get('manifold','')} | "
        f"机会分: {f.get('opp_score','')} | 风险分: {f.get('risk_score','')}\n"
        f"触发条件: {f.get('critical_condition','')}"
    )

# 技术分析汇总（宽基多空平）补充
# 引用来源（技术分析汇总）脚本的投票口径 consensus.VIEW_SCORE / to_score，不本地另写一套
try:
    _TECH_DIR = os.path.dirname(os.path.dirname(TECHREPORT))
    if _TECH_DIR not in sys.path:
        sys.path.insert(0, _TECH_DIR)
    from consensus import VIEW_SCORE as _TECH_VIEW, to_score as _tech_to_score
except Exception as e:  # noqa: BLE001
    print(f"WARN: 引用 tech consensus 失败: {e}")
    _TECH_VIEW, _tech_to_score = {}, None
_VOTE_KEYS = [v for v in (_TECH_VIEW or {}) if v != "N/A"] or ["多", "空", "震荡"]

tech_lines = []
tech_date = "未知"
if tech_text:
    m = re.search(r"日报 · (\d{4}-\d{2}-\d{2})", tech_text)
    if m:
        tech_date = m.group(1)
    # 每只宽基: ### 名称（code）→ **观点**（score N） + 方法视图 + 行情参考
    idx_sec = re.split(r"\n### ", tech_text)[1:]
    for sec in idx_sec:
        hm = re.match(r"(.+?)（([\w]+)）→ \*\*(.+?)\*\*（score ([+-]?\d+)）", sec)
        if not hm:
            continue
        name, code, view, score = hm.group(1), hm.group(2), hm.group(3), hm.group(4)
        vcount = {v: 0 for v in _VOTE_KEYS}
        for mv in re.findall(r"：([多空震荡]+)\n", sec):
            if mv in vcount:
                vcount[mv] += 1
        mr = re.search(r"行情参考：当日 ([+-][\d.]+%) · 近5日 ([+-][\d.]+%)", sec)
        ret_s = f"当日{mr.group(1)} 近5日{mr.group(2)}" if mr else ""
        tech_lines.append(
            f"**{name}**({code}): {view}(score {score}) | "
            f"多{vcount.get('多',0)}空{vcount.get('空',0)}震{vcount.get('震荡',0)} | {ret_s}"
        )
tech_block = ""
if tech_lines:
    tech_block = f"\n\n**技术分析汇总·宽基多空平({tech_date}数据):**\n" + "\n".join(tech_lines)
else:
    tech_block = "\n\n**技术分析汇总·宽基多空平:（今日无数据）**"

# ===== 2. 多系统信号 =====
sys_lines = []
sys_lines.append(
    f"**RMT量子能级（谱结构口径）**: 状态={rmt.get('status','')} | "
    f"Gap_1={rmt.get('gap_1',0):.4f} | 熵={rmt.get('entropy',0):.4f} | "
    f"ΔS={rmt.get('delta_s',0):+.4f} | 信噪比={rmt.get('snr',0):.2f}\n"
    f"（谱结构口径：ΔS≥+0.03 为危机预警、ΔS≤−0.10 为恐慌底；仅作顶部/底部判定的辅证，"
    f"建议仓位以上方「市场临界监测等级」为准）"
)
sys_lines.append(
    f"**Hodge系统**: {hodge.get('reason','')}, 仓位={hodge.get('position','')}"
)

# OT signal
ot_summ = otsig.get("summary", {"bull_triggered": 0, "bear_triggered": 0, "rotation_pairs": 0})
ot_date = otsig.get("date", "")
ot_sigs = otsig.get("signals", [])
# 引用来源 OT 脚本给出的 signal 结论（看多/看空，含“看空(高漂移)”等子标签），不在本地按 direction/触发 重判
ot_bull = [s for s in ot_sigs if str(s.get("signal", "")).startswith("看多")]
ot_bear = [s for s in ot_sigs if str(s.get("signal", "")).startswith("看空")]
bull_names = ", ".join([f"{s['name']}(W1={s.get('w1',0):.4f})" for s in ot_bull[:5]])
bear_names = ", ".join([f"{s['name']}(W1={s.get('w1',0):.4f})" for s in ot_bear[:5]])
sys_lines.append(
    f"**最优传输OT系统**({ot_date}数据): 看多{ot_summ.get('bull_triggered',0)} | 看空{ot_summ.get('bear_triggered',0)} | 轮动对{ot_summ.get('rotation_pairs',0)}\n"
    f"看多: {bull_names or '无'}\n"
    f"看空: {bear_names or '无'}"
)

top5_str = ", ".join([f"{etf_names.get(k,k)}({v:+.1%})" for k,v in rmt.get("top5",{}).items()])
bot5_str = ", ".join([f"{etf_names.get(k,k)}({v:+.1%})" for k,v in rmt.get("bottom5",{}).items()])
sys_lines.append(f"RMT看多Top5: {top5_str}")
sys_lines.append(f"RMT看空Bottom5: {bot5_str}")

lead_str = ", ".join(hodge.get("lead_names", [])[:10])
lag_str = ", ".join(hodge.get("lag_names", [])[:10])
sys_lines.append(f"Hodge领涨: {lead_str}...")
sys_lines.append(f"Hodge领跌: {lag_str}...")

# ===== 3. 行业ETF多空对比 =====
etf_bull = []
etf_bear = []
for key, d in idx_data.items():
    if not d.get("is_etf"):
        continue
    f = d.get("fusion", {})
    wr = d.get("weekly_ret", 0)
    wr_str = f"+{wr}%" if wr >= 0 else f"{wr}%"
    entry = f"{d['name']} {wr_str} 看涨{f.get('bull_prob',0)}% 看跌{f.get('bear_prob',0)}%"
    # 多空归组引用来源（物理几何）给出的研判结论 judgment，不再本地另写 bull/bear 比较规则
    _jg = str(f.get("judgment", ""))
    if _jg.startswith("看涨"):
        etf_bull.append(entry)
    elif _jg.startswith("看跌"):
        etf_bear.append(entry)
    elif "偏空" in _jg:
        etf_bear.append(entry)
    else:
        etf_bull.append(entry)

# ===== 4. 看多前10个股（跨任务汇总层：各来源任务均无“合并Top10”算法，
#          此处仅按来源产物字段聚合排序，个股选择算法本身来自 stock_picker/来源报告） =====
all_stocks = []
# From Hodge stock_recommendations
for code, info in hodge.get("stock_recommendations", {}).items():
    for s in info.get("stocks", []):
        all_stocks.append({
            "code": s["code"], "name": s["name"], "industry": info.get("name", etf_names.get(code, code)),
            "mom20": s.get("mom_20", 0), "mom5": s.get("mom_5", 0),
            "close": s.get("close", 0), "source": "Hodge"
        })
# From RMT stock_picks
for code, stocks in rmt.get("stock_picks", {}).items():
    ind_name = etf_names.get(code, code)
    for s in stocks:
        all_stocks.append({
            "code": s["code"], "name": s["name"], "industry": ind_name,
            "mom20": s.get("mom_20", 0), "mom5": s.get("mom_5", 0),
            "close": s.get("close", 0), "source": "RMT"
        })

# Deduplicate by code, keep highest mom20
seen = {}
for s in all_stocks:
    c = s["code"]
    if c not in seen or s["mom20"] > seen[c]["mom20"]:
        seen[c] = s
unique_stocks = list(seen.values())
unique_stocks.sort(key=lambda x: x["mom20"], reverse=True)
top10_bull = unique_stocks[:10]

# ===== 5. 看空前10 (ETF/指数)（跨任务汇总层：同上，仅聚合排序来源产物，
#          看空判定引用物理几何 judgment / RMT bottom5 / Hodge 领跌结果） =====
bearish = []
# From Physics Geo ETFs (bear_prob > bull_prob)
for key, d in idx_data.items():
    f = d.get("fusion", {})
    if f.get("bear_prob", 0) > f.get("bull_prob", 0):
        wr = d.get("weekly_ret", 0)
        wr_str = f"+{wr}%" if wr >= 0 else f"{wr}%"
        bearish.append({
            "name": d["name"], "bear_prob": f.get("bear_prob", 0),
            "bull_prob": f.get("bull_prob", 0), "weekly_ret": wr_str,
            "risk_score": f.get("risk_score", 0), "source": "物理几何"
        })
# From RMT bottom5
for k, v in rmt.get("bottom5", {}).items():
    nm = etf_names.get(k, k)
    bearish.append({
        "name": nm, "bear_prob": 0, "bull_prob": 0,
        "weekly_ret": f"{v:+.1%}", "risk_score": 0,
        "source": "RMTBottom", "rmt_score": v
    })
# From Hodge lag with negative momentum
for p in hodge.get("predictions", []):
    if p.get("cluster") == "领跌" and p.get("mom_60", 0) < -0.05:
        bearish.append({
            "name": p["name"], "bear_prob": 0, "bull_prob": 0,
            "weekly_ret": f"60日{p['mom_60']:+.1%}", "risk_score": 0,
            "source": "Hodge领跌", "mom60": p["mom_60"]
        })

# Sort: prioritize high bear_prob, then most negative mom60
bearish.sort(key=lambda x: (x.get("bear_prob", 0), -x.get("mom60", 0)), reverse=True)
# Deduplicate by name
seen_bear = {}
for b in bearish:
    nm = b["name"]
    if nm not in seen_bear:
        seen_bear[nm] = b
    elif b.get("bear_prob", 0) > seen_bear[nm].get("bear_prob", 0):
        seen_bear[nm] = b
top10_bear = list(seen_bear.values())[:10]

# ===== Build Feishu Card =====
md_parts = []
md_parts.append(f"**DoMate 每日总结 | {today_str} 晚间汇总 | 来自 DoMate**\n"
                "数据来源: Hodge轮动 + RMT量子能级 + 技术分析汇总 + 物理几何融合 + 最优传输OT")

md_parts.append("---\n## 一、宽基指数汇总\n\n" + "\n\n".join(idx_lines) + tech_block)

# 仓位决策块（由临界监测等级驱动）+ 状态→操作对照
cgap_lines = []
if cgap_R:
    lvl = cgap_R["level"]
    q250 = float(cgap_t.get("q250") or 0)
    ds = float(rmt.get("delta_s", 0) or 0)
    dv = cgap_R.get("divergence", {}) or {}
    dv_mark = dv.get("mark") if dv.get("ok") else "mid"
    if lvl == "red" and dv_mark == "warn":
        loc = "顶部风险区（抱团拥挤：C(t) 升而全A 跌的背离）"
    elif lvl == "red":
        loc = "顶部风险区（协同度超阈，抱团拥挤）"
    elif lvl == "none" and q250 <= 10:
        loc = "底部区（结构充分扩散、λ₁ 处低分位）"
    elif ds <= -0.10:
        loc = "底部区（RMT 谱结构恐慌底：ΔS≤−0.10）"
    else:
        loc = "中性/过渡区（无顶部或底部极值特征）"
    pos = CGAP_POS.get(lvl, 1.00)
    act = CGAP_ACT.get(lvl, "持有")
    if lvl == "none" and loc.startswith("底部"):
        act = "抄底加仓（满仓）"
    lh = cgap_R.get("level_history", {}) or {}
    cgap_lines.append(
        f"**仓位决策（由市场临界监测等级驱动）**\n"
        f"临界等级: {cgap_R['tag']}（已连续 {lh.get('streak','')} 个交易日，自 {lh.get('since','')} 起）｜"
        f"C(t)={cgap_t.get('C',0):.2f}%｜Gap={cgap_t.get('Gap',0):.3f}｜"
        f"Δλ₁={cgap_t.get('d_lambda1',0):+.4f}｜IPR={cgap_t.get('IPR',0):.4f}｜λ₁ 250日分位={q250:.1f}%\n"
        f"触发原因: {'；'.join(cgap_R['reasons']) if cgap_R.get('reasons') else '无'}\n"
        f"来源建议（cgap_rules）: {cgap_R.get('advice','')}\n"
        f"**建议仓位: {pos*100:.0f}%｜动作: {act}**（仓位百分比为汇总层展示策略，等级与建议引用来源）\n"
        f"顶部/底部定位: {loc}\n"
        f"退潮诊断: {cgap_R.get('retreat','')}\n"
        f"全A×C(t): {dv.get('text','') if dv.get('ok') else '数据不足'}"
    )
    cgap_lines.append(
        "**状态→操作对照（口径说明）**："
        "报警/达临界值（红警：C(t)>35% 或 Gap<3且Δλ₁<−10 或 C>30%且IPR>0.15）→ 减仓避险；"
        "橙警（C>30% 且 λ₁ 250日分位>90%）→ 减仓观望；"
        "黄警（C>25% 或 Gap<5 或 Δλ₁<−5 连续2天）→ 控制新增仓位；"
        "达最低点（恐慌底：ΔS≤−0.10 且 λ₁ 处低分位）→ 抄底加仓；"
        "达最高点（抱团拥挤顶：红警且 C(t) 升、全A 跌的背离，或 RMT ΔS≥+0.03）→ 卖出减仓；"
        "无预警 → 持有。"
    )
else:
    cgap_lines.append(
        f"**仓位决策**: {cgap_note or '临界监测数据不可用'}；本次仓位暂以 RMT 谱结构口径呈现（见下方 RMT 行）。"
    )

md_parts.append("## 二、多系统信号交叉验证\n\n" + "\n\n".join(cgap_lines + sys_lines))

if etf_bull or etf_bear:
    md_parts.append("## 三、行业ETF多空对比\n\n"
        "**看多方向:**\n" + "\n".join([f"- {e}" for e in etf_bull])
        + "\n\n**看空方向:**\n" + "\n".join([f"- {e}" for e in etf_bear]))
else:
    md_parts.append("## 三、行业ETF多空对比\n\n（今日无ETF级别数据）")

# Top 10 bullish stocks
bull_lines = []
for i, s in enumerate(top10_bull, 1):
    bull_lines.append(f"{i}. **{s['name']}**({s['code']}) | {s['industry']} | "
        f"20日动量+{s['mom20']:.1%} | 5日+{s['mom5']:.1%} | 收盘{s['close']} | 来源:{s['source']}")
md_parts.append("## 四、看多前10个股\n\n" + ("\n".join(bull_lines) if bull_lines else "（今日无个股推荐数据）"))

# Top 10 bearish
bear_lines = []
for i, b in enumerate(top10_bear, 1):
    bp = f"看跌{b['bear_prob']}%" if b.get("bear_prob", 0) > 0 else b.get("weekly_ret", "")
    src = b.get("source", "")
    extra = f" | 风险分{b['risk_score']}" if b.get("risk_score", 0) > 0 else ""
    bear_lines.append(f"{i}. **{b['name']}** | {bp} | {src}{extra}")
md_parts.append("## 五、看空前10(ETF/指数)\n\n" + ("\n".join(bear_lines) if bear_lines else "（今日无看空信号）"))

# Footer
md_parts.append("---\n*以上数据由 Hodge Laplacian 2 + RMT + 技术分析汇总 + 物理几何融合 + 最优传输OT 系统自动生成*\n"
    "*个股推荐基于动量信号,未经独立回测验证胜率,仅供参考*")

# Build card
elements = []
for part in md_parts:
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": part}})
    elements.append({"tag": "hr"})

if elements and elements[-1]["tag"] == "hr":
    elements.pop()

# 标题标记：跨系统风险/机会共识
_hodge_pos = str(hodge.get('position', ''))
_rmt_status = str(rmt.get('status', ''))
_cgap_lvl = cgap_R['level'] if cgap_R else None
_has_risk = (('空仓' in _hodge_pos) or ('注意' in _hodge_pos)
             or (_rmt_status in ('危机预警', '简并共振警报'))
             or (_cgap_lvl in ('red', 'orange')))
_has_opp = (_rmt_status == '恐慌底') or bool(_hodge_pos and '空仓' not in _hodge_pos and '注意' not in _hodge_pos)
_mark = ('注意风险 | ' if _has_risk else '') + ('注意机会 | ' if _has_opp else '')
_card_tmpl = {'red': 'red', 'orange': 'orange', 'yellow': 'yellow', 'none': 'blue'}.get(_cgap_lvl, 'blue')
card = {
    "msg_type": "interactive",
    "card": {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"DoMate 每日总结 | {today_str} | {_mark}来自 DoMate"},
            "template": _card_tmpl
        },
        "elements": elements
    }
}

# Send
data = json.dumps(card, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(WEBHOOK_URL, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code", 0) == 0 or result.get("StatusCode", 0) == 0:
            print(f"OK: Feishu message sent for {today_str}")
            print(f"  Hodge date: {hodge.get('date','')}")
            print(f"  RMT report: {os.path.basename(rmt_path) if rmt_path else 'N/A'}")
            print(f"  Cgap level: {cgap_R['level'] if cgap_R else (cgap_note or 'N/A')}")
            print(f"  Top10 bull: {len(top10_bull)}, Top10 bear: {len(top10_bear)}")
        else:
            print(f"ERROR: {result}")
            sys.exit(1)
except Exception as e:
    print(f"SEND ERROR: {e}")
    sys.exit(1)
