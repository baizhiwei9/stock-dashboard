# -*- coding: utf-8 -*-
"""报告交付前自洽校验：从原始数据独立复算，核对**两份**报告的正文数字与源 JSON/CSV 是否一致。

报告已拆为两份（同一任务、分别生成与推送）：
    A 两融专项    市场融资风险扫描日报_两融专项_<基准日>.md
    B 资金流与宽度 市场融资风险扫描日报_资金流与宽度_<基准日>.md
两套卡片 / 红标 / 邮件文件各自校验，并交叉检查两份报告内容不串。
"""
import json, os, re, sys
import pandas as pd
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REPORT_NAME = "市场融资风险扫描日报"
_m = json.load(open(os.path.join(D, "margin_module.json"), encoding="utf-8"))
WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = _m["last_date"]                                            # 基准日由 margin_module 决定


def _resolve(tag):
    p = os.path.join(WORK, f"{REPORT_NAME}_{tag}_{BASE}.md")
    if os.path.exists(p):
        return p
    cand = [f for f in os.listdir(WORK) if f.startswith(f"{REPORT_NAME}_{tag}_") and f.endswith(".md")]
    if cand:
        return os.path.join(WORK, sorted(cand)[-1])
    return p


REPORT_A, REPORT_B = _resolve("两融专项"), _resolve("资金流与宽度")
B = json.load(open(os.path.join(D, "breadth_result.json"), encoding="utf-8"))
M = json.load(open(os.path.join(D, "margin_module.json"), encoding="utf-8"))
BASE = int(BASE)
chk_a, chk_b = [], []
fails, notes = [], []


def chk(cond, msg, rep=None):
    """rep='A'/'B' 时把该项计入对应报告的通过/失败统计；rep=None 只做全局核对。"""
    if rep == "A":
        chk_a.append(bool(cond))
    elif rep == "B":
        chk_b.append(bool(cond))
    (notes if cond else fails).append(("OK " if cond else "FAIL ") + msg)


def chkA(cond, msg):
    chk(cond, msg, rep="A")


def chkB(cond, msg):
    chk(cond, msg, rep="B")


def approx(a, b, tol=0.05):
    return abs(a - b) <= tol


if not (os.path.exists(REPORT_A) and os.path.exists(REPORT_B)):
    print(f"FAIL 报告文件缺失：{REPORT_A} / {REPORT_B}")
    sys.exit(1)
repA = open(REPORT_A, encoding="utf-8").read()
repB = open(REPORT_B, encoding="utf-8").read()
repall = repA + "\n" + repB

# ---------- 0. 两份报告互相隔离 ----------
chkA(f"# {REPORT_NAME}（两融专项）" in repA, "报告A 标题含「（两融专项）」")
chkB(f"# {REPORT_NAME}（资金流与宽度）" in repB, "报告B 标题含「（资金流与宽度）」")
chkA(("两融专项" in os.path.basename(REPORT_A)), f"报告A 文件名含「两融专项」：{os.path.basename(REPORT_A)}")
chkB(("资金流与宽度" in os.path.basename(REPORT_B)), f"报告B 文件名含「资金流与宽度」：{os.path.basename(REPORT_B)}")
chkA("市场融资风险扫描日报（资金流与宽度）" in repA, "报告A 交叉指引报告B 名称")
chkB("市场融资风险扫描日报（两融专项）" in repB, "报告B 交叉指引报告A 名称")
chkA("两融统计汇总表" in repA and "两融杠杆监控" in repA, "报告A 含两融统计汇总表与两融杠杆监控")
chkB("栏目二 · ETF 资金流向监控" in repB and "栏目三 · 市场宽度监控" in repB
     and "栏目四 · ETF存量资金MACD突破监控" in repB, "报告B 含栏目二/三/四")
chkB("## 两融统计汇总表" not in repB and "## 栏目一 · 两融杠杆监控" not in repB,
     "报告B 不含两融统计汇总表/两融杠杆监控章节（已迁至报告A）")
chkA("## 栏目二 · ETF 资金流向监控" not in repA and "## 栏目四 · ETF存量资金MACD突破监控" not in repA,
     "报告A 不复用报告B 的栏目二/栏目四原标题（MACD 以「附栏目」并入，不串用栏目编号）")

# ---------- 1. 原始数据复算（独立于 JSON） ----------
ms = pd.read_csv(os.path.join(D, "margin_summary.csv"))
ms = ms[ms["trade_date"] == BASE]
main = ms[ms["exchange_id"].isin(["SSE", "SZSE"])]
rzye = main["rzye"].sum() / 1e8
rqye = main["rqye"].sum() / 1e8
rzrqye = main["rzrqye"].sum() / 1e8
rzmre = main["rzmre"].sum()
turn_amt = rzmre / 1e11  # 元 -> 百亿，便于比对
chk(approx(rzye, M["market"]["rzye_yi"], 0.1), f"融资余额复算 {rzye:.1f} vs JSON {M['market']['rzye_yi']}")
chk(approx(rqye, M["market"]["rqye_yi"], 0.1), f"融券余额复算 {rqye:.1f} vs JSON {M['market']['rqye_yi']}")
chk(approx(rzrqye, M["market"]["rzrqye_yi"], 0.1), f"两融余额复算 {rzrqye:.1f} vs JSON {M['market']['rzrqye_yi']}")

db = pd.read_csv(os.path.join(D, "daily_basic.csv"), usecols=["ts_code", "trade_date", "circ_mv"], dtype={"trade_date": int})
db["mk"] = db["ts_code"].str[-2:]
cur = db[(db["trade_date"] == BASE) & (db["mk"].isin(["SH", "SZ"]))]
circ = cur["circ_mv"].sum() / 1e4  # 万元 -> 亿元（沪深两市口径，与两融 SSE+SZSE 对齐）
chk(approx(circ, M["market"]["circ_mv_yi"], 1.0), f"流通市值复算(沪深) {circ:.0f}亿 vs JSON {M['market']['circ_mv_yi']:.0f}亿")
lev = rzye / circ * 100
chk(approx(lev, M["market"]["lev_pct"], 0.005), f"杠杆率复算 {lev:.3f}% vs JSON {M['market']['lev_pct']}%")

ia = pd.read_csv(os.path.join(D, "index_amount.csv"))
ia = ia[ia["trade_date"] == BASE]
amt = ia["amount"].sum() * 1000  # 千元 -> 元
turnpct = rzmre / amt * 100
chk(approx(turnpct, M["market"]["turn_pct"], 0.05), f"成交占比复算 {turnpct:.2f}% vs JSON {M['market']['turn_pct']}%")

# 宽度：从 breadth_series.csv 原始序列复核
bs = pd.read_csv(os.path.join(D, "breadth_series.csv"))
last = bs[bs["date"] == BASE].iloc[0]
chk(int(last["up"]) == B["up"] and int(last["down"]) == B["down"], f"涨/跌家数 {last['up']}/{last['down']} vs JSON {B['up']}/{B['down']}")
chk(approx(last["ad_ratio"], B["ad_ratio"], 0.001), f"涨跌比 {last['ad_ratio']:.3f} vs JSON {B['ad_ratio']}")
chk(approx(last["r20"], B["r20"], 0.01) and approx(last["r60"], B["r60"], 0.01), f"站上20/60MA {last['r20']:.2f}/{last['r60']:.2f} vs JSON {B['r20']}/{B['r60']}")
chk(int(last["nl20"]) == B["nl20"] and int(last["nhnl_diff"]) == B["nh_nl_diff"], f"新低/新高差 {last['nl20']}/{last['nhnl_diff']} vs JSON {B['nl20']}/{B['nh_nl_diff']}")

# ETF：从份额+价格复算净流入与前3名（只用 <= 基准日 的数据，与 margin_module 同口径）
fs = pd.read_csv(os.path.join(D, "etf_share.csv"))
pr = pd.read_csv(os.path.join(D, "etf_price.csv"))
fcol = "fd_share" if "fd_share" in fs.columns else fs.columns[-1]
fs = fs[fs["trade_date"] <= BASE]
pr = pr[pr["trade_date"] <= BASE]
dts = sorted(fs["trade_date"].unique())
d0, d1 = dts[-21], dts[-1]
chk(d1 <= BASE, f"ETF复算截止日 {d1} 未超出报告基准日 {BASE}")
piv_f = fs.pivot_table(index="ts_code", columns="trade_date", values=fcol)
piv_p = pr.pivot_table(index="ts_code", columns="trade_date", values="close")
raw = {}
for c in piv_f.index:
    if c in piv_p.index:
        raw[c] = (piv_f.loc[c, d1] - piv_f.loc[c, d0]) * 1e4 * piv_p.loc[c, d1] / 1e8
flow = {r["code"]: r["est_flow_yi"] for r in M["etf_flow"]}
groups = {r["code"]: r["group"] for r in M["etf_flow"]}
chk(len(raw) == len(flow), f"ETF复算覆盖 {len(raw)} 只 == JSON {len(flow)} 只")
top3 = sorted(raw.items(), key=lambda x: -x[1])[:3]
for c, v in top3:
    chk(approx(v, flow.get(c, 1e9), 0.5), f"ETF净流入复算 {c} {v:.2f}亿 vs JSON {flow.get(c)}亿")
wide_sum = sum(v for c, v in flow.items() if groups[c] == "宽基锚")
ind_sum = sum(v for c, v in flow.items() if groups[c] != "宽基锚")
wide_raw = sum(v for c, v in raw.items() if groups.get(c) == "宽基锚")
ind_raw = sum(v for c, v in raw.items() if groups.get(c) not in (None, "宽基锚"))
chk(approx(wide_sum, wide_raw, 1.0), f"宽基锚合计 JSON {wide_sum:.2f}亿 vs 复算 {wide_raw:.2f}亿")
chk(approx(ind_sum, ind_raw, 1.0), f"行业/主题合计 JSON {ind_sum:.2f}亿 vs 复算 {ind_raw:.2f}亿")
chkB(f"宽基锚合计 {wide_sum:+.0f}亿" in repB, f"报告B 正文宽基锚合计 {wide_sum:+.0f}亿 与 JSON 一致")
chkB(f"行业/主题合计 {ind_sum:+.0f}亿" in repB, f"报告B 正文行业/主题合计 {ind_sum:+.0f}亿 与 JSON 一致")
neg = sum(1 for c, v in flow.items() if groups[c] != "宽基锚" and v < 0)
sect_all = sum(1 for c in flow if groups[c] != "宽基锚")
chkB(f"（{neg}/{sect_all} 只净流出）" in repB, f"报告B 正文净流出只数 {neg}/{sect_all} 与 JSON 一致")

# ---------- 2. 报告正文数字 vs 源 JSON（基准日由 margin_module.last_date 动态解析，逐项取源断言，不硬编码） ----------
_mk, _rt1 = M["market"], M["risk_track1"]
_ra = json.load(open(os.path.join(D, "ratio_alert.json"), encoding="utf-8"))
_drop = next(r for r in _ra["summary_rows"] if r["indicator"] == "融资余额自区间高点回落")
cands = {
    "杠杆率": f"{_mk['lev_pct']}%",
    "融资余额": f"{_mk['rzye_yi']:.0f} 亿",
    "两融余额": f"{_mk['rzrqye_yi']:.0f} 亿",
    "流通市值": f"{_mk['circ_mv_yi']:.0f} 亿",
    "20日累计净流出": f"{_mk['chg20_pct']:+.2f}%",
    "成交占比": f"{_mk['turn_pct']}%",
    "涨跌比": f"{B['ad_ratio']:.3f}",
    "创新低家数": f"{B['nl20']}",
    "宽度综合分位": f"P{B['composite_pct']:.1f}",
    "创新低家数分位": f"P{B['percentiles']['nl20']:.1f}",
    "融资余额自区间高点回落": f"{_drop['value']:.2f}%",
    "融资余额区间高点": f"{_mk['rzye_peak_yi']:.0f} 亿",
    "成交占比区间高点": f"{_mk['turn_peak_pct']:.2f}%",
    "强平连锁个股数": f"{_rt1['grp_lev_gt8_count']} 只",
}
for k, tok in cands.items():
    chk(tok in repall, f"正文含 {k} {tok}")

# 站上20MA / 60MA 比例：报告正文按原始数值渲染（如 20.2%，而非固定两位小数 20.20%），
# 故按数值比较，容许省略末位 0，避免把格式差异误判为数字不一致。
for _k, _v in (("站上20MA比例", B["r20"]), ("站上60MA比例", B["r60"])):
    _mm = re.search(rf"{_k}：([0-9.]+)%", repall)
    chk(bool(_mm) and abs(float(_mm.group(1)) - float(_v)) < 1e-9,
        f"正文含 {_k} {_v}%（数值比较，容许末位 0 省略）")

# ---------- 3. 结构完整性（两融六段 → A；宽度六段 / MACD → B） ----------
for sec in ["一、风险等级", "二、宏观杠杆体温（双轨）", "三、227% 担保比例等效信号",
            "四、行业融资 × ETF 背离表", "五、强平脆弱前 10", "六、操作建议（两融）"]:
    chkA(sec in repA, f"两融六段（报告A）：{sec}")
for sec in ["一、宽度等级", "二、轨一触发项", "三、轨二分位", "四、背离检测", "五、联合信号", "六、操作建议"]:
    chkB(sec in repB, f"宽度六段（报告B）：{sec}")
chkA("报告总览（两融专项）" in repA, "报告A 含「报告总览（两融专项）」")
chkB("报告总览（资金流与宽度）" in repB, "报告B 含「报告总览（资金流与宽度）」")
chkB("四栏目联动综合结论" in repB, "报告B 含四栏目联动综合结论")
for sec in ["一、栏目读数", "二、五宽基信号明细", "三、机会 / 风险判定依据", "四、合并大图", "五、栏目解读"]:
    chkB(sec in repB, f"MACD五小节（报告B）：{sec}")
# 栏目四：全部子图合并为单张（含全部宽基 × 2 面板）
_mm = json.load(open(os.path.join(D, "macd_module.json"), encoding="utf-8")) if os.path.exists(
    os.path.join(D, "macd_module.json")) else {}
_comb = _mm.get("combined_report_png")
_nb = len(_mm.get("bases", {}))
chkB(bool(_comb) and os.path.exists(_comb), f"栏目四合并大图存在：{_comb}")
chkB(bool(_comb) and os.path.basename(_comb) in repB, "报告B 正文引用合并大图文件")
chkB(f"{_nb} 个宽基 × 2 面板 = {_nb * 2} 个子图" in repB, f"报告B 声明子图数量 {_nb}×2={_nb * 2}")
chkA("需人工确认" in repA or "矛盾信号" in repA, "冲突口径标注规则存在（报告A）")
chkB("需人工确认" in repB or "矛盾信号" in repB, "冲突口径标注规则存在（报告B）")
chkA("不做点位预测" in repA, "报告A 声明不做点位预测")
chkB("不做点位预测" in repB, "报告B 声明不做点位预测")
chkA("120 个交易日" in repA, "报告A 声明 120 日分位窗口")
chkB("120 个交易日" in repB, "报告B 声明 120 日分位窗口（市场宽度）")
chkA("固定阈值" in repA and "分位" in repA, "报告A 双轨并列声明")
chkB("固定阈值" in repB and "分位" in repB, "报告B 双轨并列声明")

# ---------- 3.5 统一「机会 / 风险」重点突出标记（风险红＋星、机会绿＋月） ----------
chkA("🔴⭐" in repA and "🟢🌙" in repA, "报告A 含风险标记 🔴⭐ 与机会标记 🟢🌙")
chkB("🔴⭐" in repB and "🟢🌙" in repB, "报告B 含风险标记 🔴⭐ 与机会标记 🟢🌙")
chkB("末列标记：" in repB, "报告B 声明末列标记口径（机会/风险）")
chkA(repA.count("| 标记 |") >= 1, f"报告A 汇总表含「标记」列（命中 {repA.count('| 标记 |')} 处）")
chkB(repB.count("| 标记 |") >= 1, f"报告B ETF 明细表含「标记」列（命中 {repB.count('| 标记 |')} 处）")
chkB("机会/风险标记" in repB, "报告B MACD 五宽基明细表含「机会/风险标记」列")
chkA(repA.count("🔴⭐") >= 3 and repA.count("🟢🌙") >= 3,
     f"报告A 标记覆盖：风险 {repA.count('🔴⭐')} 处 / 机会 {repA.count('🟢🌙')} 处")
chkB(repB.count("🔴⭐") >= 5 and repB.count("🟢🌙") >= 5,
     f"报告B 标记覆盖：风险 {repB.count('🔴⭐')} 处 / 机会 {repB.count('🟢🌙')} 处")

# 飞书卡片：两份各一套（彩色 header + 图文卡）
_fc_m = json.load(open(os.path.join(D, "feishu_cards_margin.json"), encoding="utf-8"))
_fc_o = json.load(open(os.path.join(D, "feishu_cards_other.json"), encoding="utf-8"))
_FC = _fc_m + _fc_o
chk(all(c.get("color") in ("red", "green", "orange") for c in _FC),
    f"全部飞书卡片带 header 颜色（red/green/orange）：{[c.get('color') for c in _FC]}")
chkA(any("两融专项" in str(c.get("title", "")) for c in _fc_m), "报告A 卡片标题带「两融专项」")
chkB(any("资金流与宽度" in str(c.get("title", "")) for c in _fc_o), "报告B 卡片标题带「资金流与宽度」")
_mc = [c for c in _fc_o if "存量资金MACD" in str(c.get("title", ""))]
_mc_want = "red" if len(_mm.get("risk", []) or []) > 0 else "green"
chk(bool(_mc) and all(c.get("color") == _mc_want for c in _mc),
    f"MACD 卡片颜色＝{_mc_want}（风险{len(_mm.get('risk', []) or [])}项）：{[c.get('color') for c in _mc]}")
chk(bool(_mc) and bool(_mc[0].get("image")), "MACD 卡片内嵌合并大图（图文卡）")
chk(any("🔴⭐" in str(c.get("content", "")) and "🟢🌙" in str(c.get("content", "")) for c in _FC),
    "飞书卡片正文含风险/机会标记")

# ---------- 3.6 两融统计汇总表（近一年分位）与 ratio_alert.json 对齐（报告A） ----------
_rap = os.path.join(D, "ratio_alert.json")
RA = json.load(open(_rap, encoding="utf-8")) if os.path.exists(_rap) else None
chk(RA is not None, "存在 data/ratio_alert.json（近一年分位数据源）")
if RA:
    rows = RA["summary_rows"]
    st = RA["summary_stats"]
    w = RA["window"]
    chkA("两融统计汇总表" in repA, "报告A 含「两融统计汇总表」栏目")
    chk(len(rows) == 14, f"汇总表行数 {len(rows)} == 14")
    chk(st["n_rows"] == 14, f"summary_stats.n_rows {st['n_rows']} == 14")
    chk(w["n_trading_days"] == 242 and int(w["last_date"]) == BASE,
        f"分位窗口 {w['n_trading_days']} 个交易日、截止 {w['last_date']} == 基准日 {BASE}")
    _ws = f"{str(w['first_date'])[:4]}-{str(w['first_date'])[4:6]}-{str(w['first_date'])[6:]}"
    chkA(_ws in repA and f"{w['n_trading_days']} 个完整交易日" in repA,
         f"报告A 声明近一年窗口（{w['n_trading_days']} 交易日）与起点日 {_ws}")
    chkA("| 指标 |" in repA and "近一年分位" in repA and "判定口径" in repA and "正常范围" in repA,
         "报告A 汇总表含 指标/当前值/近一年分位/标记/正常范围/起点日/判定口径 七列")
    # 正常范围列：表头 + 每栏数值齐备 + 口径声明
    chkA("正常范围（P10~P90｜中位 P50）" in repA, "报告A 汇总表含「正常范围（P10~P90｜中位 P50）」列头")
    chkA("正常范围 = 该指标近一年自身分布的 P10~P90 分位带" in repA,
         "报告A 声明正常范围口径（近一年 P10~P90 分位带，中位为 P50 中枢）")
    chkA("触发式指标（227% 等效信号）正常状态＝未触发" in repA,
         "报告A 声明 227% 等效信号为触发式指标（正常＝未触发）")
    chk(RULE_NR := RA.get("normal_range_rule", ""), "ratio_alert.json 顶层含 normal_range_rule")
    _loop_seen = {}
    for line in repA.splitlines():
        if line.startswith("| ") and line.count("|") >= 7:
            for r in rows:
                if line.startswith("| " + r["indicator"] + " |"):
                    _loop_seen[r["indicator"]] = line
    for r in rows:
        _nd = str(r.get("normal_display", ""))
        chk(bool(_nd), f"正常范围字段存在：{r['indicator']}")
        ln = _loop_seen.get(r["indicator"], "")
        chk(_nd in ln, f"汇总表正常范围 {r['indicator']} = {_nd}")
        for _k in ("normal_low", "normal_med", "normal_high"):
            chk(_k in r, f"正常范围分位数落盘：{r['indicator']}.{_k}")
        chk(bool(r.get("normal_note")), f"正常范围判定依据存在：{r['indicator']}")
    # 227% 行：正常状态＝未触发，不适用分位带
    _sig = [r for r in rows if r["indicator"] == "227% 担保比例等效信号"]
    chk(len(_sig) == 1 and "未触发" in str(_sig[0].get("normal_display", "")),
        "227% 等效信号正常范围显示为「未触发（触发即为风险）」")
    # 用户点名栏（融资余额÷两市成交额）正常范围必须可见且给出判定依据
    _tgt = [r for r in rows if "融资余额" in r["indicator"] and "两市成交额" in r["indicator"]]
    chk(len(_tgt) == 1 and _tgt[0]["normal_display"] in _loop_seen.get(_tgt[0]["indicator"], ""),
        f"用户点名栏「融资余额 ÷ 两市成交额」正常范围可见：{_tgt[0]['normal_display'] if _tgt else 'N/A'}")
    # 对齐：报告A 汇总表分隔行含对齐冒号（数值右对齐、标记居中）
    _sepA = [ln for ln in repA.splitlines()
             if ln.startswith("|") and ln.strip("|") and set(ln) <= set("|:- ")]
    chkA(any(":" in ln for ln in _sepA), "报告A 汇总表分隔行含 l/c/r 对齐冒号（显式对齐生效）")
    # 对齐不得外溢到报告B：报告B Markdown 表格分隔行无对齐冒号（维持既有无冒号渲染）
    _sepB = [ln for ln in repB.splitlines()
             if ln.startswith("|") and ln.strip("|") and set(ln) <= set("|:- ")]
    chkB(_sepB and not any(":" in ln for ln in _sepB),
         "报告B 表格分隔行无对齐冒号（未受报告A 对齐改动影响）")
    # 逐行：当前值 + 近一年分位 + 起点日当日值 三要素齐备
    for r in rows:
        chkA(str(r["value_display"]) in repA, f"汇总表当前值 {r['indicator']} = {r['value_display']}")
        chkA(str(r["percentile_display"]) in repA, f"汇总表近一年分位 {r['indicator']} = {r['percentile_display']}")
        chkA(str(r["start_display"]) in repA, f"汇总表起点日值 {r['indicator']} = {r['start_display']}")
    # 标记自洽：risk → 🔴⭐、opp → 🟢🌙、neu → ➖，按行核对
    _rows_seen = {}
    for line in repA.splitlines():
        if line.startswith("| ") and line.count("|") >= 7:
            for r in rows:
                if line.startswith("| " + r["indicator"] + " |"):
                    _rows_seen[r["indicator"]] = line
    chk(len(_rows_seen) == 14, f"汇总表 14 行均可在正文定位（实得 {len(_rows_seen)}）")
    _want = {"risk": "🔴⭐", "opp": "🟢🌙", "neu": "➖"}
    for r in rows:
        ln = _rows_seen.get(r["indicator"], "")
        chk(_want[r["kind"]] in ln, f"标记自洽 {r['indicator']} → {_want[r['kind']]}")
    n_r = sum(1 for r in rows if r["kind"] == "risk")
    n_o = sum(1 for r in rows if r["kind"] == "opp")
    n_n = sum(1 for r in rows if r["kind"] == "neu")
    chk((n_r, n_o, n_n) == (st["n_risk"], st["n_opp"], st["n_neu"]),
        f"标记计数 风险{n_r}/机会{n_o}/中性{n_n} == summary_stats "
        f"{st['n_risk']}/{st['n_opp']}/{st['n_neu']}")
    _repA_plain = repA.replace("**", "")   # 正文可能用 Markdown 粗体包裹数值，比对前先剥离
    chkA(f"共 {st['n_rows']} 栏" in _repA_plain, "报告A 汇总栏数与 JSON 一致")
    chkA(f"窗口 {w['first_date'] // 10000}-{w['first_date'] // 100 % 100:02d}-{w['first_date'] % 100:02d}" in repA,
         "报告A 标题含分位窗口起止日")
    # 红标清单：近一年分位 ≥90 的风险项必须进入**报告A**邮件触发清单
    rf = json.load(open(os.path.join(D, "red_flags_margin.json"), encoding="utf-8"))
    for r in rows:
        if r["kind"] == "risk":
            chk(any(r["indicator"] in it for it in rf["items"]),
                f"风险项进入报告A红标清单：{r['indicator']}")
            chk(any(r["indicator"] in it and "两融汇总·" in it for it in rf["items"]),
                f"报告A红标清单中两融汇总项带统一前缀：{r['indicator']}")
    chkA(f"近一年分位 {rows[4]['percentile_display']}" in repA or rows[4]["percentile_display"] in repA,
         "风险行（融资余额÷两市成交额）分位在报告A正文可见")
    # 飞书卡片：报告A 含「两融统计汇总表」卡，且卡片含风险/机会标记
    _rc = [c for c in _fc_m if "两融统计汇总表" in str(c.get("title", ""))]
    chk(bool(_rc), "报告A 飞书卡片含「两融统计汇总表」卡")
    chk(bool(_rc) and _rc[0].get("color") in ("red", "green", "orange"),
        f"两融汇总卡片带 header 颜色：{[c.get('color') for c in _rc]}")
    chk(bool(_rc) and "🔴⭐" in str(_rc[0].get("content", "")) and "🟢🌙" in str(_rc[0].get("content", "")),
        "两融汇总卡片正文含风险/机会标记")
    chk(bool(_rc) and "正常范围" in str(_rc[0].get("content", "")),
        "两融汇总卡片含「正常范围」列")
    chk(bool(_rc) and "未触发" in str(_rc[0].get("content", "")),
        "两融汇总卡片 227% 等效信号行显示「未触发」")
    # 邮件正文：报告A 邮件含两融统计汇总表段落
    _eb = open(os.path.join(D, "email_body_margin.txt"), encoding="utf-8").read()
    chk("两融统计汇总表" in _eb, "报告A 邮件正文含「两融统计汇总表」段落")
    chk("正常范围" in _eb, "报告A 邮件正文含「正常范围」信息")
    chk(f"共 {st['n_rows']} 栏：风险 {st['n_risk']} · 机会 {st['n_opp']} · 中性 {st['n_neu']}" in _eb,
        "报告A 邮件正文两融汇总栏计数与 JSON 一致")

    # ---- 3.6.5 风险与机会前置速览（报告A 专用，全部由脚本确定性生成） ----
    _MKR, _MKO, _MKN = "🔴⭐", "🟢🌙", "➖"
    _SEC = "## 风险与机会速览（程序自动生成）"
    chkA(_SEC in repA, "报告A 含「风险与机会速览（程序自动生成）」章节")
    chkB(_SEC not in repB, "报告B 不含风险与机会速览章节（保持逐字节不变）")
    if _SEC in repA:
        _ps, _pn = repA.index(_SEC), repA.index("> 说明：")
        _po = repA.index("## 报告总览（两融专项）")
        chkA(_pn < _ps < _po, "速览位于「> 说明：」段之后、`## 报告总览（两融专项）` 之前（前置落点正确）")
        _body = repA[_ps:_po]
        _pr = _body.find(f"{_MKR} 风险（")
        _poo = _body.find(f"{_MKO} 机会（")
        _pnu = _body.find(f"{_MKN} 中性（")
        _pw = _body.find("到底是怎么回事（按分子 / 分母分解，程序自动推导）")
        chkA(-1 not in (_pr, _poo, _pnu, _pw) and _pr < _poo < _pnu < _pw,
             "速览内部顺序＝风险 → 机会 → 中性 → 成因说明")
        chkA(f"{_MKR} 风险（汇总表 {st['n_risk']} 项 ＋ 栏目一侧 " in _body,
             f"速览风险项数与 JSON 一致（汇总表 {st['n_risk']} 项 ＋ 栏目一侧项）")
        chkA(f"{_MKO} 机会（{st['n_opp']} 项）" in _body, f"速览机会项数＝{st['n_opp']} 项")
        chkA(f"共 {st['n_rows']} 栏 —— {_MKR} 风险 {st['n_risk']} · {_MKO} 机会 {st['n_opp']} · {_MKN} 中性 {st['n_neu']}" in _body,
             "速览口径 note 的栏数与 JSON 一致")
        for r in rows:
            if r["kind"] in ("risk", "opp"):
                chkA(r["indicator"] in _body and r["value_display"] in _body
                     and str(r["percentile_display"]) in _body,
                     f"速览含 {'风险' if r['kind'] == 'risk' else '机会'}项明细：{r['indicator']}（值+分位）")
        _c, _sp2 = RA.get("current", {}) or {}, RA.get("start_point", {}) or {}
        try:
            _bzo = (float(_c["融资余额_亿"]) - float(_sp2["融资余额_亿"])) / float(_sp2["融资余额_亿"]) * 100
            _ato = (float(_c["两市成交额_亿"]) - float(_sp2["两市成交额_亿"])) / float(_sp2["两市成交额_亿"]) * 100
            _rzo = float(_c["融资余额÷两市成交额_%"])
            _rso = float(_sp2["融资余额÷两市成交额_%"])
            chkA(f"{_bzo:+.2f}%" in _body and f"{_ato:+.2f}%" in _body
                 and f"{_rso:.4f}%" in _body and f"{_rzo:.4f}%" in _body,
                 f"速览成因说明可由 current/start_point 复算（融资余额 {_bzo:+.2f}%／成交额 {_ato:+.2f}%／"
                 f"比值 {_rso:.4f}%→{_rzo:.4f}%）")
        except Exception as _e:
            fails.append(f"FAIL 速览成因说明快照字段缺失，无法复算：{_e}")
    # 报告A 总览卡：前置速览块（风险/机会先说，且含程序推导的成因说明）
    _ovc = [c for c in _fc_m if "总览" in str(c.get("title", ""))]
    chk(bool(_ovc) and "【先说结论】" in str(_ovc[0].get("content", "")),
        "报告A 总览卡开头先呈现风险与机会速览（先说结论）")
    chk(bool(_ovc) and "到底是怎么回事" in str(_ovc[0].get("content", "")),
        "报告A 总览卡含程序推导的成因说明")
    chk(bool(_ovc) and _MKR in str(_ovc[0].get("content", "")) and _MKO in str(_ovc[0].get("content", "")),
        "报告A 总览卡速览块含风险 🔴⭐ 与机会 🟢🌙 标记")

# ---------- 3.7 两份红标 / 邮件 / 卡片文件一致性 ----------
_rf_m = json.load(open(os.path.join(D, "red_flags_margin.json"), encoding="utf-8"))
_rf_o = json.load(open(os.path.join(D, "red_flags_other.json"), encoding="utf-8"))
sA = open(os.path.join(D, "email_subject_margin.txt"), encoding="utf-8").read().strip()
sB = open(os.path.join(D, "email_subject_other.txt"), encoding="utf-8").read().strip()
chkA(_rf_m.get("severity") in ("无", "提醒", "强提醒") and sA.startswith("【市场融资风险扫描日报（两融专项）"),
     f"报告A 红标分级/邮件主题自洽：{_rf_m.get('severity')}｜{sA}")
chkB(_rf_o.get("severity") in ("无", "提醒", "强提醒") and sB.startswith("【市场融资风险扫描日报（资金流与宽度）"),
     f"报告B 红标分级/邮件主题自洽：{_rf_o.get('severity')}｜{sB}")
chk(sA != sB, f"两份邮件主题可区分：{sA} / {sB}")
chkA(f"红标项数：{_rf_m.get('count')}" in open(os.path.join(D, "email_body_margin.txt"), encoding="utf-8").read(),
     "报告A 邮件正文红标数与 JSON 一致")
chkB(f"红标项数：{_rf_o.get('count')}" in open(os.path.join(D, "email_body_other.txt"), encoding="utf-8").read(),
     "报告B 邮件正文红标数与 JSON 一致")
_sevA, _sevB = _rf_m.get("severity"), _rf_o.get("severity")
_cntA, _cntB = _rf_m.get("count", 0), _rf_o.get("count", 0)
chk(_sevA == ("强提醒" if _cntA >= 2 else ("提醒" if _cntA == 1 else "无")), "报告A 分级规则（≥2 强提醒/≥1 提醒）自洽")
chk(_sevB == ("强提醒" if _cntB >= 2 else ("提醒" if _cntB == 1 else "无")), "报告B 分级规则（≥2 强提醒/≥1 提醒）自洽")

# ---------- 3.8 报告A 附栏目：ETF存量资金MACD（与报告B 栏目四同源同图，供两融专项报告一并呈现） ----------
_MA_SEC = "## 附栏目 · ETF存量资金MACD突破监控（全部子图合并大图）"
chkA(_MA_SEC in repA, "报告A 含「附栏目 · ETF存量资金MACD突破监控（全部子图合并大图）」")
chkA("## 栏目四 · ETF存量资金MACD突破监控" not in repA,
     "报告A 未复用报告B 的「## 栏目四 · …」原标题（避免栏目编号串用）")
if _MA_SEC in repA:
    _MAB = repA[repA.index(_MA_SEC):]
    chkA(bool(_comb) and os.path.exists(_comb), f"报告A 附栏目合并大图存在：{_comb}")
    chkA(bool(_comb) and os.path.basename(_comb) in repA, "报告A 正文引用合并大图文件（Markdown 图片）")
    chkA(f"![ETF存量资金MACD全部子图合并大图]({os.path.basename(_comb)})" in repA,
         "报告A 图片语法指向合并大图文件名")
    chkA(f"{_nb} 个宽基 × 2 面板 = {_nb * 2} 个子图" in repA,
         f"报告A 附栏目声明子图数量 {_nb}×2={_nb * 2}")
    chkA("同源同图" in _MAB and "不重复取数、不重复计算" in _MAB,
         "报告A 附栏目声明与报告B「同源同图」且不重复取数/计算")
    chkA(all(s in _MAB for s in ("一、五宽基信号明细", "二、机会 / 风险判定依据", "三、与两融栏目的联动阅读")),
         "报告A 附栏目含 五宽基明细 / 机会风险判定依据 / 联动阅读 三小节")
    chkA("| 宽基 | 当日信号 |" in repA, "报告A 附栏目含五宽基明细表")
    for _bname in (_mm.get("bases", {}) or {}):
        chkA(f"| {_bname} |" in repA, f"报告A 附栏目明细含宽基行：{_bname}")
    chkA(f"五宽基触发 {_mm.get('n_triggered')}/{_mm.get('n_bases')}" in repA,
         "报告A 附栏目/总览触发数与 macd_module.json 一致")
    chkB(bool(_comb) and os.path.basename(_comb) in repB, "报告B 与报告A 引用同一张合并大图（同源同图）")
    _ebA2 = open(os.path.join(D, "email_body_margin.txt"), encoding="utf-8").read()
    chkA("ETF存量资金MACD" in _ebA2, "报告A 邮件正文含 ETF存量资金MACD 读数（附栏目同步）")
chkA("| ④ 存量资金MACD（附） |" in repA, "报告A 总览表新增 ④ 存量资金MACD 行")
_mA = [c for c in _fc_m if "存量资金MACD" in str(c.get("title", ""))]
chkA(bool(_mA), "报告A 飞书卡片含「存量资金MACD」卡")
chkA(bool(_mA) and bool(_mA[0].get("image")), "报告A MACD 卡片内嵌合并大图（图文卡）")
chkA(bool(_mA) and _mA[0].get("color") == _mc_want,
     f"报告A MACD 卡片颜色＝{_mc_want}（风险{len(_mm.get('risk', []) or [])}项）：{[c.get('color') for c in _mA]}")
chkA(bool(_mA) and _mA[0].get("image") == _comb, "报告A MACD 卡片图片＝合并大图路径（与报告B 同源）")
chkA(bool(_mA) and "🔴⭐" in str(_mA[0].get("content", "")) and "🟢🌙" in str(_mA[0].get("content", "")),
     "报告A MACD 卡片正文含风险/机会标记")

# ---------- 4. 占位符 / 乱码检查（两份） ----------
for _rep in (repA, repB):
    for bad in ["TODO", "XXX", "待补充", "N/A", "�", "NaN", "None", "{", "}"]:
        if bad in _rep:
            fails.append(f"FAIL [{os.path.basename(REPORT_A) if _rep is repA else os.path.basename(REPORT_B)}] "
                         f"发现可疑占位/乱码字符: {bad}")

# ---------- 5. 强平脆弱前10 与源 JSON 对齐（报告A） ----------
tbl = re.findall(r"\|\s*(\d{6}\.[A-Z]{2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", repA)
risk = [(r["ts_code"], r["name"]) for r in M["risk_top10"]]
got = [(c, n.strip()) for c, n, _ in tbl if c in dict(risk)]
chkA([c for c, _ in risk] == [c for c, _ in got], "强平脆弱前10代码与JSON顺序一致（报告A）")
chkB("### 五、强平脆弱前 10" not in repB, "报告B 不含强平脆弱个股明细表（已归报告A）")

print("=" * 66)
for n in notes:
    print(n)
print("-" * 66)
print(f"[报告A 两融专项] 通过 {sum(chk_a)} / {len(chk_a)}")
print(f"[报告B 资金流与宽度] 通过 {sum(chk_b)} / {len(chk_b)}")
print(f"合计 通过 {len(notes)} 项，失败 {len(fails)} 项")
for f in fails:
    print(f)
sys.exit(1 if fails else 0)