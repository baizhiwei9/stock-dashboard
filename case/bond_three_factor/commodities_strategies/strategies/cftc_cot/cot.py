# -*- coding: utf-8 -*-
"""CFTC Disaggregated COT：年度 zip 包下载 + 解析 → 周度持仓面板。

数据源
------
  https://www.cftc.gov/files/dea/history/fut_disagg_txt_YYYY.zip

口径
----
  · 报告日 = 周二收盘持仓；官方发布 = 周五 15:30 ET（北京周六 03:30）
  · 因此周标签统一贴到该报告所属周的**周五**（W-FRI），做价格对齐时不会用到未来数据
  · `mm_net` = 管理基金多头 − 管理基金空头（Managed Money，投机盘）
  · `Q` = Δmm_net / OI（论文口径的投机资金流）
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from ...config import ROOT
from .universe import Commodity

CFTC_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
UA = {"User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120 Safari/537.36")}
MIN_ZIP_BYTES = 100_000

REPO_CACHE = ROOT / "data" / "cache" / "cftc_cot"
LEGACY_CACHE = Path.home() / ".openclaw" / "workspace" / ".cache" / "cftc_cot"

_KEEP = ["Report_Date_as_YYYY-MM-DD", "Market_and_Exchange_Names",
         "CFTC_Contract_Market_Code", "Open_Interest_All",
         "M_Money_Positions_Long_All", "M_Money_Positions_Short_All"]


def cache_dirs() -> List[Path]:
    """缓存目录候选：环境变量 > 仓库 data/cache/cftc_cot > 旧工作区缓存（只读复用）。"""
    out: List[Path] = []
    env = os.environ.get("CFTC_COT_CACHE")
    if env:
        out.append(Path(env).expanduser())
    out.extend([REPO_CACHE, LEGACY_CACHE])
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _zip_path(year: int, cache_dir: Path) -> Path:
    return Path(cache_dir) / f"fut_disagg_txt_{year}.zip"


def _find_zip(year: int) -> Optional[Path]:
    for d in cache_dirs():
        p = _zip_path(year, d)
        try:
            if p.exists() and p.stat().st_size > MIN_ZIP_BYTES:
                return p
        except OSError:
            continue
    return None


def fetch_year(year: int, cache_dir: Optional[Path] = None, force: bool = False,
               timeout: int = 90, verbose: bool = False, max_age_hours: float = 24.0,
               offline: bool = False) -> pd.DataFrame:
    """下载（或读缓存）并解析单年 COT 包，返回长表（含 date / name / oi / mm_long / mm_short）。

    当年包超过 `max_age_hours` 会重下（周五更新，否则拿不到新数据）；
    下载失败但有旧缓存时沿用旧缓存；`offline=True` 完全不联网。
    """
    import requests

    target = _zip_path(year, Path(cache_dir) if cache_dir else cache_dirs()[0])
    existing = _find_zip(year)
    fresh = False
    if existing is not None:
        age = (time.time() - existing.stat().st_mtime) / 3600.0
        fresh = (age < max_age_hours) and (existing == target or cache_dir is None)

    path = existing
    if offline:
        if path is None:
            raise RuntimeError(
                f"离线模式且无 {year} 年 COT 缓存（找过 {', '.join(str(d) for d in cache_dirs())}）")
    elif force or not fresh:
        url = CFTC_URL.format(year=year)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = requests.get(url, headers=UA, timeout=timeout)
                r.raise_for_status()
                data = r.content
                if len(data) < MIN_ZIP_BYTES:
                    raise RuntimeError(f"返回体积异常 {len(data)} 字节")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                path = target
                if verbose:
                    print(f"  [CFTC] {year} 下载 OK ({len(data)/1e6:.2f} MB)")
                break
            except Exception as exc:                     # noqa: BLE001
                last_err = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        else:
            if path is None:
                raise RuntimeError(f"CFTC {year} 数据不可用: {last_err}")
            if verbose:
                print(f"  [CFTC] {year} 下载失败({last_err})，沿用缓存 {path}")

    assert path is not None
    return parse_zip(path)


def parse_zip(path: Path) -> pd.DataFrame:
    """解析年度 zip → 长表（date/name/code/oi/mm_long/mm_short），纯本地、可离线。"""
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        raw = pd.read_csv(io.BytesIO(z.read(name)), low_memory=False)
    missing = [c for c in _KEEP if c not in raw.columns]
    if missing:
        raise RuntimeError(f"COT 文件缺列: {missing}")
    d = raw[_KEEP].copy()
    d["date"] = pd.to_datetime(d["Report_Date_as_YYYY-MM-DD"], errors="coerce")
    d["name"] = d["Market_and_Exchange_Names"].astype(str).str.strip()
    d["code"] = d["CFTC_Contract_Market_Code"].astype(str).str.strip().str.zfill(6)
    d["oi"] = pd.to_numeric(d["Open_Interest_All"], errors="coerce")
    d["mm_long"] = pd.to_numeric(d["M_Money_Positions_Long_All"], errors="coerce")
    d["mm_short"] = pd.to_numeric(d["M_Money_Positions_Short_All"], errors="coerce")
    return d[["date", "name", "code", "oi", "mm_long", "mm_short"]].dropna(subset=["date"])


def match_rows(d: pd.DataFrame, commodity: Commodity) -> pd.DataFrame:
    """按 CFTC 名称（含历史别名）过滤；品种配了 `cftc_code` 时再用合约代码兜底。

    只有改过名的品种（目前仅 WTI 原油 067651）会走代码兜底，
    其余品种行为与加兜底之前完全一致（单元测试 `test_crude_alias_merge_*` 卡死）。
    """
    names = set(commodity.cftc_names)
    m = d["name"].isin(names)
    code = (commodity.cftc_code or "").strip().zfill(6) if commodity.cftc_code else ""
    if code and "code" in d.columns:
        m = m | (d["code"].astype(str).str.strip().str.zfill(6) == code)
    return d[m]


def dedup_by_date(d: pd.DataFrame, commodity: Commodity) -> pd.DataFrame:
    """按报告日去重：同一报告日新旧两个名字都在时，优先保留「当前名」那一行。

    改名年份（WTI 原油 2022）年度包里新旧名会同时出现 52 行，这里必须收敛到唯一周一，
    否则 `diff()` / 分位窗口都会被重复周污染。
    """
    if d.empty:
        return d.reset_index(drop=True)
    t = d.copy()
    t["_pref"] = np.where(t["name"].eq(commodity.cftc_name), 0, 1)
    t = (t.sort_values(["date", "_pref"])
         .drop_duplicates(subset=["date"], keep="first")
         .sort_values("date")
         .drop(columns="_pref")
         .reset_index(drop=True))
    return t


def years_for(start_year: int, end_year: int) -> List[int]:
    """需要下载的年份（多取前一年：算 Δ 与 104 周回看都要前置数据）。"""
    this_year = pd.Timestamp.today().year
    hi = min(max(end_year, start_year), this_year)
    lo = max(2000, start_year - 1)
    return list(range(lo, hi + 1))


def weekly_panel(commodity: Commodity, years: Sequence[int], cache_dir: Optional[Path] = None,
                 force: bool = False, verbose: bool = False,
                 offline: bool = False) -> pd.DataFrame:
    """单品种 COT 周度面板，索引 = 报告周周五（W-FRI）。

    列：report_date / oi / mm_long / mm_short / mm_net / d_mm_net / d_oi / Q
    """
    frames: List[pd.DataFrame] = []
    for y in years:
        try:
            d = fetch_year(y, cache_dir=cache_dir, force=force, verbose=verbose, offline=offline)
        except Exception as exc:                          # noqa: BLE001
            if verbose:
                print(f"  [!] {commodity.name_cn} {y} COT 不可用: {exc}")
            continue
        d = match_rows(d, commodity)
        if len(d):
            frames.append(d)
    if not frames:
        raise RuntimeError(f"CFTC 未找到品种 {commodity.cftc_name} 的持仓数据")

    # 改名年份新旧两个名字会同时出现（WTI 原油 2022），必须先并起来再按报告日去重
    d = dedup_by_date(pd.concat(frames, ignore_index=True), commodity)

    d["mm_net"] = d["mm_long"] - d["mm_short"]
    d["d_mm_net"] = d["mm_net"].diff()
    d["d_oi"] = d["oi"].diff()
    d["Q"] = d["d_mm_net"] / d["oi"]
    d["week"] = d["date"] + pd.to_timedelta(4 - d["date"].dt.weekday, unit="D")
    d = (d.dropna(subset=["mm_net"]).set_index("week").sort_index())
    d = d[~d.index.duplicated(keep="last")]
    d.index.name = "week"
    return d[["date", "oi", "mm_long", "mm_short", "mm_net", "d_mm_net", "d_oi", "Q"]]


def latest_report_date(panel: pd.DataFrame) -> Optional[pd.Timestamp]:
    if panel is None or panel.empty or "date" not in panel:
        return None
    return pd.Timestamp(pd.to_datetime(panel["date"]).max())


def panel_to_records(commodity: Commodity, panel: pd.DataFrame) -> List[Dict[str, object]]:
    """转成 SQLite 行（供 engine 落库）。"""
    if panel is None or panel.empty:
        return []
    out: List[Dict[str, object]] = []
    for week, row in panel.iterrows():
        out.append({
            "commodity": commodity.key,
            "name_cn": commodity.name_cn,
            "week": pd.Timestamp(week).strftime("%Y-%m-%d"),
            "report_date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
            "oi": _num(row.get("oi")),
            "mm_long": _num(row.get("mm_long")),
            "mm_short": _num(row.get("mm_short")),
            "mm_net": _num(row.get("mm_net")),
            "d_mm_net": _num(row.get("d_mm_net")),
            "Q": _num(row.get("Q")),
        })
    return out


def _num(v) -> Optional[float]:
    try:
        if v is None or pd.isna(v) or not np.isfinite(float(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_cached_only(years: Iterable[int]) -> Dict[int, bool]:
    """调试用：报告各年份缓存是否就绪（不联网）。"""
    return {y: _find_zip(y) is not None for y in years}
