# -*- coding: utf-8 -*-
"""文字说明生成（每只宽基：各方法观点 + 汇总买/卖/平建议）"""
from config import ETF_POOL


# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''


def build_text(views, consensus_row, asof_date, ret5_row=None, ret1_row=None, smc_detail=None):
    """views: {方法名: {code: view}}；consensus_row: {code: (结论, score, detail, n_na)}
    smc_detail: {code: SMC 判定依据文本}
    返回 markdown 文本
    """
    lines = []
    lines.append(f"**宽基指数技术分析多空平日报 · {asof_date}**")
    lines.append("")
    lines.append("对 7 只宽基指数（含恒生科技）分别运行 6 类技术分析方法（动力学MACD/缠论分型/均线双通路/"
                 "AC1-VMD/小波DWT/SMC日周线），多=+1 空=−1 震荡=0 计票汇总："
                 "总分>0 建议买（看多）、<0 建议卖（看空）、=0 平（震荡观望）。")
    lines.append("")
    for e in ETF_POOL:
        code, name = e["code"], e["name"]
        view, score, detail, n_na = consensus_row[code]
        lines.append(f"### {name}（{code}）→ **{view}**（score {score:+d}）")
        lines.append("")
        # SMC 日/周线级别机会：出现看多/看空时重点提示（标红、标星、标大、标粗）
        smc_v = views.get("SMC日周线", {}).get(code, "N/A")
        if smc_v in ("多", "空"):
            mark = "🔴" if smc_v == "多" else "🟢"
            kind = "看多" if smc_v == "多" else "看空"
            basis = (smc_detail or {}).get(code, "")
            lines.append(f"> ⭐ **{mark} SMC日周线{kind}机会**：{basis}")
            lines.append("")
        for m in views:
            v = views[m].get(code, "N/A")
            sym = "🔴" if v == "多" else ("🟢" if v == "空" else ("⚪" if v == "震荡" else "➖"))
            star = "⭐ " if (m == "SMC日周线" and v in ("多", "空")) else ""
            lines.append(f"- {sym} {star}{m}：{v}")
        r1 = ret1_row.get(code) if ret1_row else None
        r5 = ret5_row.get(code) if ret5_row else None
        if r1 is not None or r5 is not None:
            r1s = f"{r1*100:+.2f}%" if r1 is not None else "--"
            r5s = f"{r5*100:+.2f}%" if r5 is not None else "--"
            lines.append(f"- 行情参考：当日 {r1s} · 近5日 {r5s}")
        lines.append("")
    lines.append("---")
    lines.append("说明：6 类方法均为纯技术分析（不含资金流/基本面，不含流形类）；"
                 "数据经统一入口按多级代理依次回退获取（AK 筛：新浪→东方财富→同花顺；TS 筛：Tushare 兜底），"
                 "缺口自动经缓存回填，各方法同一份数据源。")
    lines.append("以上为由历史技术分析框架自动计算的观点汇总，仅供研究参考，不构成投资建议。")
    lines += _bs("tech_consensus_daily", "lines")
    return "\n".join(lines)


def build_one_liner(consensus_row, asof_date):
    """一句摘要（飞书标题用）"""
    parts = []
    for e in ETF_POOL:
        view, score, _, _ = consensus_row[e["code"]]
        mark = {"买": "🟢", "卖": "🔴", "平": "⚪"}.get(view, "⚪")
        parts.append(f"{mark}{e['name']}={view}")
    return f"{asof_date} 宽基多空平：{'  '.join(parts)}"