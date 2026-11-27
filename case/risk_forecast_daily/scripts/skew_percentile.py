# -*- coding: utf-8 -*-
"""
期权 Skew / IV / PCR 量化分析（5大宽基，统一真源版）

口径（2026-10-23 主人定稿）：
  - 标的固定 5 大宽基：沪深300 / 中证500 / 创业板 / 中证1000 / 科创50（不含上证50）。
  - 取数统一走 data_hub.get_all_option_chains（tushare 全量链为主，看板兜底），
    本脚本不直连 akshare / tushare，不自行抓 T 型报价。
  - 指标统一走 data_hub.option_metrics（全系统唯一期权指标真源）：
      * Skew = 25Δ Risk Reversal 归一化 = (IV_Put25Δ - IV_Call25Δ) / ATM_IV
        —— 真 BS 反推 IV + BS delta 反解 25Δ 行权价；优先主力次月（dte>=10），
           自动跳过临到期失真合约；禁用任何 "100-10x" 简写或 ±25% 行权价近似。
      * ATM_IV 由 BS 反推。
      * PCR 走 calc_pcr（volume / oi / amount 三口径）。
  - 宁慢勿空、绝不造假：实时取不到 → 该标的全部标 N/A，不写历史、不补模拟、不拍 baseline。

历史：真实 Skew 每日落盘 scripts/data/skew_history.json，结构 {标的: {YYYYMMDD: skew}}，
      满 5 个真实点起算分位（近1年/近2年）。
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()


import os
import sys
import json
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'skew_history.json')
OUTPUT_FILE = os.path.join(DATA_DIR, 'skew_percentile_latest.json')

os.makedirs(DATA_DIR, exist_ok=True)

# ============== 5 大宽基定义（键名对齐 data_hub.OPTION_CHAIN_TARGETS） ==============
# skew>0 = 左偏(认沽更贵，对冲/恐慌需求强)；skew<0 = 右偏(认购更贵，偏看涨)
TARGETS = [
    {"key": "300ETF", "name": "沪深300"},
    {"key": "500ETF", "name": "中证500"},
    {"key": "创业板ETF", "name": "创业板"},
    {"key": "中证1000", "name": "中证1000"},
    {"key": "科创50ETF", "name": "科创50"},
]


# ============== data_hub 统一入口 ==============
def _ensure_data_hub_on_path():
    """把工作区 codeact/scripts 加入 sys.path，使 data_hub 可导入。"""
    candidates = [
        os.environ.get('DATA_HUB_SCRIPTS_DIR', ''),
        os.path.join(SCRIPT_DIR, '..', '..', 'codeact', 'scripts'),
        os.path.join(os.getcwd(), 'codeact', 'scripts'),
        _cp('/Coze/Drive/蓁宝/所有对话/主对话/codeact/scripts'),
    ]
    for _p in candidates:
        if not _p:
            continue
        _p = os.path.abspath(_p)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)


_ensure_data_hub_on_path()

try:
    from data_hub import get_all_option_chains
    from data_hub.option_metrics import (
        standardize_contracts,
        calc_25d_risk_reversal,
        calc_pcr,
    )
    HAS_DATA_HUB = True
except Exception as _dh_err:  # pragma: no cover
    HAS_DATA_HUB = False
    print(f"⚠️ data_hub 未加载，期权链路不可用: {_dh_err}")

# 静默 data_hub 日志，保持本脚本输出简洁
try:
    import logging as _logging
    _logging.getLogger('data_hub').setLevel(_logging.CRITICAL + 1)
except Exception:
    pass


# ============== 历史数据管理（仅真实值） ==============
def load_skew_history():
    """加载真实 Skew 历史，结构 {标的: {YYYYMMDD: skew}}。

    自动兼容旧版污染文件：跳过 '_comment' 等下划线元字段，以及非 dict 的标的块。
    """
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            clean = {}
            for name, block in raw.items():
                if name.startswith('_') or not isinstance(block, dict):
                    continue
                vals = {}
                for d, v in block.items():
                    try:
                        vals[str(d)] = float(v)
                    except (TypeError, ValueError):
                        continue
                if vals:
                    clean[name] = vals
            return clean
        except Exception:
            pass
    return {}


def save_skew_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_skew_history(etf_name, trade_date, skew_value):
    """仅写入真实 skew；调用方需先保证 skew_value 非 None。"""
    if skew_value is None:
        return
    history = load_skew_history()
    history.setdefault(etf_name, {})[trade_date] = skew_value
    save_skew_history(history)


# ============== 分位数 ==============
def calculate_percentile(value, history_values):
    """值在历史序列中的百分位（0-100）；少于5个真实点返回 None。"""
    if value is None or not history_values or len(history_values) < 5:
        return None
    sorted_hist = sorted(history_values)
    n = len(sorted_hist)
    rank = sum(1 for v in sorted_hist if v <= value)
    return round(rank / n * 100, 1)


def get_percentile_level(percentile):
    if percentile is None:
        return ("N/A", "")
    if percentile > 90:
        return ("🔴 极端", "极度偏离")
    elif percentile > 70:
        return ("🟡 偏高", "偏离较大")
    elif percentile < 30:
        return ("🟡 偏低", "偏离较大")
    else:
        return ("🟢 中性", "正常范围")


def get_skew_direction_label(skew):
    """归一化 25Δ RR skew 方向（基于该口径真实分布标定）。

    新口径量级：常态约 ±0.3，0.5+ 为明显对冲需求，负向对称看待。
    skew>0 左偏（认沽贵）；skew<0 右偏（认购贵）。
    """
    if skew is None:
        return "N/A"
    if skew > 0.5:
        return "左偏(强对冲)"
    elif skew > 0.15:
        return "左偏(偏谨慎)"
    elif skew >= -0.15:
        return "基本对称"
    elif skew >= -0.5:
        return "右偏(偏乐观)"
    else:
        return "右偏(强乐观)"


def _window_values(history_block, ref_date, years):
    """取 ref_date 往前 years 年窗口内的真实 skew 列表。"""
    start = ref_date - timedelta(days=int(365 * years))
    out = []
    for d, v in history_block.items():
        try:
            dt = datetime.strptime(str(d), "%Y%m%d")
        except (TypeError, ValueError):
            continue
        if start <= dt <= ref_date:
            out.append(float(v))
    return out


def calculate_skew_percentiles(etf_name, current_skew, ref_dt):
    """近1年/近2年分位（仅真实历史；不足5点返回 None）。"""
    history = load_skew_history()
    block = history.get(etf_name, {})
    v1 = _window_values(block, ref_dt, 1)
    v2 = _window_values(block, ref_dt, 2)
    return {
        'current': current_skew,
        '1year_pct': calculate_percentile(current_skew, v1),
        '2year_pct': calculate_percentile(current_skew, v2),
        'history_count_1y': len(v1),
        'history_count_2y': len(v2),
    }


# ============== 单日全标的取数 + 统一指标 ==============
def _collect_once(trade_date):
    """取一日 5 大宽基全量链并计算统一指标。返回 {key: result_dict}。失败返回 {}。"""
    if not HAS_DATA_HUB:
        return {}
    try:
        chains = get_all_option_chains(trade_date, with_spot=True)
    except Exception as e:
        print(f"   get_all_option_chains({trade_date}) 异常: {e}")
        return {}

    out = {}
    for t in TARGETS:
        key = t["key"]
        item = chains.get(key)
        if item is None:
            continue
        chain = item.get("chain")
        spot = item.get("spot")
        if chain is None or len(chain) == 0 or not spot or spot <= 0:
            continue
        try:
            contracts = standardize_contracts(chain, source="tushare", trade_date=trade_date)
            if not contracts:
                # 看板兜底链来源标记不同，按通用方式再标准化一次
                contracts = standardize_contracts(chain, source="akshare_sse", trade_date=trade_date)
            rr = calc_25d_risk_reversal(contracts, float(spot), trade_date=trade_date)
            if not rr:
                continue
            out[key] = {
                "skew": rr["skew"],
                "atm_iv": rr["atm_iv"],
                "rr": rr["rr"],
                "put25_iv": rr["put25_iv"],
                "call25_iv": rr["call25_iv"],
                "dte": rr["dte"],
                "expiry": rr["expiry"],
                "pcr_vol": calc_pcr(contracts, "volume"),
                "pcr_oi": calc_pcr(contracts, "oi"),
                "pcr_amount": calc_pcr(contracts, "amount"),
                "spot": float(spot),
                "source": item.get("source", ""),
                "contract_count": len(contracts),
            }
        except Exception as e:
            print(f"   {t['name']} 指标计算异常: {e}")
            continue
    return out


def analyze_skew_percentile(lookback_days=10):
    """执行 5 大宽基 Skew/IV/PCR 分析。

    非交易日/当日数据未入库时，自动向最近交易日回退（最多 lookback_days 天）。
    取不到的标的恒展示为 N/A，不造假、不写历史。
    """
    print("=" * 70)
    print("期权 Skew / IV / PCR 量化分析（5大宽基 · 统一真源）")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    if not HAS_DATA_HUB:
        print("\n⚠️ data_hub 不可用，全部标的 N/A。")
        metrics = {}
        used_date = datetime.now().strftime("%Y%m%d")
    else:
        today = datetime.now()
        metrics = {}
        used_date = today.strftime("%Y%m%d")
        for back in range(lookback_days + 1):
            d = (today - timedelta(days=back)).strftime("%Y%m%d")
            attempt = _collect_once(d)
            # 只要该日取到任一标的即采用（期权全市场同步入库，通常是有/无一致）
            if attempt:
                metrics = attempt
                used_date = d
                if back == 0:
                    print(f"\n数据交易日: {d}（当日）")
                else:
                    print(f"\n当日无入库数据，回退到最近交易日: {d}（回退{back}天）")
                break
            print(f"   {d} 无可用期权链，继续回退...")
        if not metrics:
            print(f"\n⚠️ 最近{lookback_days}天均无可用期权数据，全部标的 N/A。")

    results = []
    real_n = 0
    na_n = 0
    ref_dt = datetime.strptime(used_date, "%Y%m%d")

    for t in TARGETS:
        key, name = t["key"], t["name"]
        m = metrics.get(key)

        if m and m.get("skew") is not None:
            current_skew = m["skew"]
            # 仅真实值写历史、算分位
            update_skew_history(name, used_date, current_skew)
            pct = calculate_skew_percentiles(name, current_skew, ref_dt)
            results.append({
                "name": name,
                "key": key,
                "current_skew": current_skew,
                "atm_iv": m["atm_iv"],
                "rr": m["rr"],
                "pcr_vol": m["pcr_vol"],
                "pcr_oi": m["pcr_oi"],
                "spot": m["spot"],
                "expiry": m["expiry"],
                "dte": m["dte"],
                "1year_pct": pct["1year_pct"],
                "2year_pct": pct["2year_pct"],
                "history_count_1y": pct["history_count_1y"],
                "source": m["source"],
            })
            real_n += 1
            print(f"  ✓ {name:<6} skew={current_skew:+.3f} ATMIV={m['atm_iv']:.3f} "
                  f"PCRvol={m['pcr_vol']:.2f} (dte={m['dte']})")
        else:
            # N/A：不写历史、不补模拟
            results.append({
                "name": name,
                "key": key,
                "current_skew": None,
                "atm_iv": None,
                "rr": None,
                "pcr_vol": None,
                "pcr_oi": None,
                "spot": None,
                "expiry": None,
                "dte": None,
                "1year_pct": None,
                "2year_pct": None,
                "history_count_1y": 0,
                "source": "N/A",
            })
            na_n += 1
            print(f"  ⚠ {name:<6} N/A（无数据，不估算）")

    # 落盘（绝对路径，避免被 cwd 影响）
    payload = {
        "timestamp": datetime.now().isoformat(),
        "trade_date": used_date,
        "metric": "25delta_risk_reversal_normalized",
        "results": results,
        "summary": {"real": real_n, "na": na_n},
    }
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 结果已保存: {OUTPUT_FILE}")
    except Exception as e:
        print(f"\n⚠ 结果保存失败: {e}")

    print(f"数据源统计: 真实 {real_n} 个，N/A {na_n} 个（N/A 不造假不写历史）")
    return results


def _fmt_pct(p):
    if p is None:
        return "N/A"
    return f"{p:.0f}%"


def _fmt_num(v, nd=3):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "N/A"


def format_markdown_output(results):
    """Markdown 报告片段。"""
    md = []
    md.append("### 期权偏度 Skew / IV / PCR（5大宽基 · 25Δ风险逆转）")
    md.append("")
    md.append("| 标的 | Skew | 方向 | ATM IV | PCR(量) | 近1年分位 | 近2年分位 |")
    md.append("|------|------|------|--------:|--------:|---------:|---------:|")
    for r in results:
        skew_str = f"{r['current_skew']:+.3f}" if r['current_skew'] is not None else "N/A"
        pct1 = _fmt_pct(r['1year_pct'])
        pct2 = _fmt_pct(r['2year_pct'])
        if r['1year_pct'] is not None:
            lvl, _ = get_percentile_level(r['1year_pct'])
            pct1 = f"{pct1} {lvl}".strip()
        md.append(
            f"| {r['name']} | {skew_str} | {get_skew_direction_label(r['current_skew'])} "
            f"| {_fmt_num(r['atm_iv'])} | {_fmt_num(r['pcr_vol'], 2)} | {pct1} | {pct2} |"
        )
    md.append("")
    md.append("> Skew=(IV沽25Δ−IV购25Δ)/ATM IV，正值=左偏(认沽贵/对冲需求强)；分位基于真实历史，满5个交易日起算。")
    return "\n".join(md)


if __name__ == "__main__":
    print("开始执行期权 Skew/IV/PCR 量化分析...\n")
    results = analyze_skew_percentile()
    print("\n" + "=" * 70)
    print("Markdown报告片段")
    print("=" * 70)
    print(format_markdown_output(results))
