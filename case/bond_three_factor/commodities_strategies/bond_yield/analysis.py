"""债券涨跌归因：把 DataHub 的债券事实数据整理成「可直接交给债券分析技能」的面板。

定位
====
本模块不做因果断言，只产出**可核对的事实层分解**：

- 各期限收益率近 1/5/20 日变动（bp）→ 判断是牛/熊、平/陡；
- 关键利差（10Y-2Y、30Y-10Y、10Y-5Y）当前值与近 5 年分位；
- 资金面（DR007、FR007、SHIBOR 隔夜）与央行 OMO 净投放；
- 海外（美债 10Y）与「国债-存单」「国开 1Y-3M」等政策预期代理利差。

输出既可由 :func:`reason_pack` 直接调用，也可作为
``skills/fupeng-macro-analysis``（债券/利率分析技能）的输入面板：skill 负责按
「分子分母—长短利差—资金面—中美利差」框架解释成因，本模块负责保证每个数字
都来自 DataHub 且带截止日期。

用法::

    python -m commodities_strategies.bond_yield.analysis            # 打印事实面板
    python -m commodities_strategies.bond_yield.analysis --days 20 --write
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..bond_strategies import edb as hub_edb
from . import data as Bdata

#: 归因面板用到的 DataHub 序列（全部经 EDB 桥接层，不直连外部源）
CODE_LABELS: Dict[str, str] = {
    "RATE.CN.2Y": "国债2Y(%)",
    "RATE.CN.5Y": "国债5Y(%)",
    "RATE.CN.10Y": "国债10Y(%)",
    "RATE.CN.30Y": "国债30Y(%)",
    "LIQ.CN.DR007": "DR007(%)",
    "LIQ.CN.REPO.FR007": "FR007定盘(%)",
    "LIQ.CN.SHIBOR.ON": "SHIBOR隔夜(%)",
    "LIQ.CN.OMO.NET": "OMO当日净投放(亿元)",
    "LIQ.CN.OMO.POLICY": "隔夜逆回购政策利率(%)",
    "RATE.US.10Y": "美债10Y(%)",
    "BOND.SPREAD.CN.CGB10Y_NCD1Y": "国债10Y-存单1Y(bp)",
    "BOND.SPREAD.CN.CDB.1Y_3M": "国开1Y-3M(bp)",
}

SKILL_NAME = "fupeng-macro-analysis"
SKILL_PATH = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME / "SKILL.md"


def _series(code: str) -> pd.Series:
    try:
        s = hub_edb.series(None, code)
    except Exception:
        return pd.Series(dtype=float)
    return s if s is not None else pd.Series(dtype=float)


def _chg_bp(s: pd.Series, days: int, scale: float = 100.0) -> Optional[float]:
    """最近 days 个交易日变动（收益率/利差按 bp，其余按原单位×scale）。"""
    s = s.dropna()
    if len(s) <= days:
        return None
    return float((s.iloc[-1] - s.iloc[-1 - days]) * scale)


def _pct_5y(s: pd.Series) -> Optional[float]:
    s = s.dropna()
    if len(s) < 250:
        return None
    hist = s.iloc[:-1].tail(1250)
    return float((hist < s.iloc[-1]).mean())


def curve_panel() -> dict:
    """收益率曲线 + 关键利差（全部 DataHub）。"""
    y = {}
    for code in ("RATE.CN.2Y", "RATE.CN.5Y", "RATE.CN.10Y", "RATE.CN.30Y"):
        s = _series(code)
        y[code] = s
    out = {"yields": {}, "spreads": {}}
    for code, s in y.items():
        if not len(s):
            continue
        out["yields"][code] = {
            "label": CODE_LABELS[code],
            "last_date": s.index[-1].strftime("%Y-%m-%d"),
            "last": round(float(s.iloc[-1]), 4),
            "chg_1d_bp": _chg_bp(s, 1),
            "chg_5d_bp": _chg_bp(s, 5),
            "chg_20d_bp": _chg_bp(s, 20),
            "pct_5y": _pct_5y(s),
        }
    y2, y5, y10, y30 = (y.get("RATE.CN.2Y"), y.get("RATE.CN.5Y"),
                        y.get("RATE.CN.10Y"), y.get("RATE.CN.30Y"))
    for name, a, b in (("10Y-2Y", y10, y2), ("30Y-10Y", y30, y10), ("10Y-5Y", y10, y5)):
        if a is None or b is None or not len(a) or not len(b):
            continue
        sp = ((a - b) * 100).dropna()
        if not len(sp):
            continue
        out["spreads"][name] = {
            "label": f"{name}(bp)",
            "last_date": sp.index[-1].strftime("%Y-%m-%d"),
            "last": round(float(sp.iloc[-1]), 2),
            "chg_5d": _chg_bp(sp, 5, scale=1.0),
            "chg_20d": _chg_bp(sp, 20, scale=1.0),
            "pct_5y": _pct_5y(sp),
        }
    return out


def funding_panel() -> dict:
    """资金面与政策面（DataHub LIQ.* / bond 利差）。"""
    out = {}
    for code in ("LIQ.CN.DR007", "LIQ.CN.REPO.FR007", "LIQ.CN.SHIBOR.ON",
                 "LIQ.CN.OMO.NET", "LIQ.CN.OMO.POLICY"):
        s = _series(code)
        if not len(s):
            continue
        item = {"label": CODE_LABELS[code],
                "last_date": s.index[-1].strftime("%Y-%m-%d"),
                "last": round(float(s.iloc[-1]), 4)}
        if code != "LIQ.CN.OMO.NET":
            item["chg_5d"] = _chg_bp(s, 5, scale=1.0)
        else:
            item["sum_5d"] = float(s.tail(5).sum())
        out[code] = item
    return out


def external_panel() -> dict:
    """海外与政策预期代理（DataHub）。"""
    out = {}
    for code in ("RATE.US.10Y", "BOND.SPREAD.CN.CGB10Y_NCD1Y", "BOND.SPREAD.CN.CDB.1Y_3M"):
        s = _series(code)
        if not len(s):
            continue
        out[code] = {"label": CODE_LABELS[code],
                     "last_date": s.index[-1].strftime("%Y-%m-%d"),
                     "last": round(float(s.iloc[-1]), 4),
                     "chg_5d": _chg_bp(s, 5, scale=100.0 if "RATE" in code else 1.0)}
    return out


def _bullets(pack: dict) -> List[str]:
    """把事实面板压成机器可读的分行结论（只描述数据，不解释因果）。"""
    lines: List[str] = []
    y = pack["curve"]["yields"]
    if "RATE.CN.10Y" in y:
        i = y["RATE.CN.10Y"]
        d5 = i.get("chg_5d_bp")
        if d5 is not None:
            word = "上行(债价跌)" if d5 > 0 else ("下行(债价涨)" if d5 < 0 else "持平")
            lines.append(f"10Y {i['last']:.3f}%（{i['last_date']}），近5日 {word} {d5:+.1f}bp")
    sp = pack["curve"]["spreads"]
    if "10Y-2Y" in sp:
        item = sp["10Y-2Y"]
        shape = "走陡" if (item.get("chg_5d") or 0) > 0 else "走平"
        lines.append(f"10Y-2Y 利差 {item['last']:.1f}bp（近5年 {_pct_txt(item.get('pct_5y'))} 分位），近5日{shape}")
    if "30Y-10Y" in sp:
        item = sp["30Y-10Y"]
        lines.append(f"30Y-10Y 利差 {item['last']:.1f}bp（近5年 {_pct_txt(item.get('pct_5y'))} 分位）")
    f = pack["funding"]
    if "LIQ.CN.DR007" in f:
        line = f"DR007 {f['LIQ.CN.DR007']['last']:.3f}%"
        if f["LIQ.CN.DR007"].get("chg_5d") is not None:
            line += f"（近5日变动 {f['LIQ.CN.DR007']['chg_5d']:+.3f}%）"
        lines.append(line)
    if "LIQ.CN.OMO.NET" in f:
        lines.append(f"央行 OMO 近5日净投放合计 {f['LIQ.CN.OMO.NET']['sum_5d']:+.0f} 亿元")
    e = pack["external"]
    if "RATE.US.10Y" in e:
        lines.append(f"美债10Y {e['RATE.US.10Y']['last']:.3f}%（近5日 {e['RATE.US.10Y'].get('chg_5d', 0):+.1f}bp）")
    if "BOND.SPREAD.CN.CGB10Y_NCD1Y" in e:
        lines.append(f"国债10Y-存单1Y 利差 {e['BOND.SPREAD.CN.CGB10Y_NCD1Y']['last']:.1f}bp（负值加深=短端更紧）")
    return lines


def reason_pack(days: int = 20) -> dict:
    """构建「债券涨跌归因」事实面板（供分析技能/对话直接使用）。"""
    pack = {
        "as_of": None,
        "window_days": days,
        "curve": curve_panel(),
        "funding": funding_panel(),
        "external": external_panel(),
        "skill": {"name": SKILL_NAME, "path": str(SKILL_PATH),
                  "exists": SKILL_PATH.is_file()},
        "source": "DataHub EDB（RATE.CN.* / LIQ.CN.* / RATE.US.* / BOND.SPREAD.*）",
    }
    dates = [v["last_date"] for sec in ("curve", "funding", "external")
             for v in (pack[sec].get("yields", {}) or {}).values()] + \
            [v["last_date"] for v in pack["funding"].values()]
    pack["as_of"] = max(dates) if dates else None
    pack["bullets"] = _bullets(pack)
    return pack


def to_markdown(pack: dict) -> str:
    lines = [f"# 债券涨跌归因事实面板（截至 {pack.get('as_of') or 'n/a'}）", ""]
    lines += [f"- {b}" for b in pack["bullets"]]
    lines += ["", "## 期限收益率", "", "| 指标 | 日期 | 最新 | 近5日 | 近20日 | 近5年分位 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for v in pack["curve"]["yields"].values():
        lines.append(
            f"| {v['label']} | {v['last_date']} | {v['last']:.3f} | "
            f"{_fmt(v.get('chg_5d_bp'), 'bp')} | {_fmt(v.get('chg_20d_bp'), 'bp')} | "
            f"{_fmt_pct(v.get('pct_5y'))} |")
    lines += ["", "## 关键利差", "", "| 利差 | 日期 | 最新(bp) | 近5日 | 近20日 | 近5年分位 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for v in pack["curve"]["spreads"].values():
        lines.append(f"| {v['label']} | {v['last_date']} | {v['last']:.1f} | "
                     f"{_fmt(v.get('chg_5d'), 'bp')} | {_fmt(v.get('chg_20d'), 'bp')} | "
                     f"{_fmt_pct(v.get('pct_5y'))} |")
    lines += ["", "## 资金面与政策面", ""]
    for v in pack["funding"].values():
        extra = (f"近5日 {v['chg_5d']:+.3f}" if v.get("chg_5d") is not None
                 else (f"近5日合计 {v.get('sum_5d', 0):+.0f}" if "sum_5d" in v else ""))
        lines.append(f"- {v['label']}：{v['last']:.4g}（{v['last_date']}）{extra}")
    lines += ["", "## 海外与政策预期代理", ""]
    for v in pack["external"].values():
        lines.append(f"- {v['label']}：{v['last']:.4g}（{v['last_date']}）近5日 {v.get('chg_5d', 0):+.2f}")
    lines += ["", f"数据源：{pack['source']}；归因框架技能：{SKILL_NAME}（{SKILL_PATH}）"]
    return "\n".join(lines)


def _pct_txt(v: Optional[float]) -> str:
    return "样本不足" if v is None else f"{v * 100:.0f}%"


def _fmt(v: Optional[float], unit: str) -> str:
    return "n/a" if v is None else f"{v:+.1f}{unit}"


def _fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="债券涨跌归因事实面板（DataHub 取数）")
    ap.add_argument("--days", type=int, default=20, help="滚动窗口（交易日）")
    ap.add_argument("--write", action="store_true", help="写出 data/bond_yield/out/reason_pack.md")
    args = ap.parse_args()
    pack = reason_pack(args.days)
    print(json.dumps({k: v for k, v in pack.items() if k != "bullets"},
                     ensure_ascii=False, indent=2), flush=True)
    print("\n".join(pack["bullets"]), flush=True)
    if args.write:
        Bdata.ensure_out_dir()
        path = Bdata.OUT_DIR / "reason_pack.md"
        path.write_text(to_markdown(pack), encoding="utf-8")
        print(f"[write] {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())