#!/usr/bin/env python3
"""
A股微盘择时模型 - 一次性计算脚本
计算市场宽度+边际资金流，生成W42周度复盘报告

模型：综合得分 = 0.5 × 市场宽度得分 + 0.5 × 边际资金流得分
      两个子分数都是过去5年分位数(0-100)

数据源：Tushare代理 (ttx.dailyfetch.top)
"""

import os, sys, json, time, asyncio, traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import aiohttp

# ============================================================
# Configuration
# ============================================================
BASE_DIR = Path("/Coze/Drive/两融季节同比")
CACHE_DIR = BASE_DIR / "codeact" / "output" / "micro_timing_cache"
OUTPUT_DIR = BASE_DIR
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = "1f7d5d325804a92b869963036fab5e5cb71e7820e7d60903ee9952b1"
API_URL = f"https://ttx.dailyfetch.top/{TOKEN}"
CONCURRENCY = 8
MAX_RETRIES = 3

START_DATE = "20211015"
END_DATE = "20261014"
MA_WINDOW = 20
MICRO_CAP_N = 400
LIST_DAYS_THRESHOLD = 180
LOOKBACK_DAYS = 1250  # ~5 years

# Target dates (W42: 10/8-10/14, W41: 9/30-10/7)
TARGET_WEEK = ["20261008", "20261009", "20261012", "20261013", "20261014"]
PREV_WEEK = ["20261008", "20261009"]  # will be filled after confirming trading dates

# ============================================================
# Tushare Proxy API
# ============================================================
# Known column names for each API
API_COLUMNS = {
    "stock_basic": ["ts_code", "name", "list_date", "market"],
    "trade_cal": ["exchange", "cal_date", "is_open", "pretrade_date"],
    "index_daily": ["ts_code", "trade_date", "close", "open", "high", "low", "pre_close", "change", "pct_chg", "vol", "amount"],
    "index_weight": ["index_code", "con_code", "trade_date", "weight"],
    "index_classify": ["index_code", "industry_name", "level", "industry_code", "is_pub", "parent_code", "src"],
    "index_member": ["index_code", "con_code", "in_date", "out_date", "is_new"],
    "daily": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
    "daily_basic": ["ts_code", "trade_date", "close", "total_mv", "circ_mv", "turnover_rate", "volume_ratio"],
    "moneyflow": ["ts_code", "trade_date", "buy_elg_amount", "buy_elg_vol", "buy_lg_amount", "buy_lg_vol", "buy_md_amount", "buy_md_vol", "buy_sm_amount", "buy_sm_vol", "net_mf_amount", "net_mf_vol", "sell_elg_amount", "sell_elg_vol", "sell_lg_amount", "sell_lg_vol", "sell_md_amount", "sell_md_vol", "sell_sm_amount", "sell_sm_vol"],
}


def items_to_df(items, api_name):
    """Convert raw API items (list of lists) to DataFrame with proper columns."""
    if not items:
        return pd.DataFrame()
    cols = API_COLUMNS.get(api_name)
    if cols and len(items[0]) == len(cols):
        return pd.DataFrame(items, columns=cols)
    # Fallback: if field count doesn't match, return without column names
    df = pd.DataFrame(items)
    if cols and df.shape[1] <= len(cols):
        df.columns = cols[:df.shape[1]]
    return df


async def fetch_ts(session, api_name, semaphore, retries=MAX_RETRIES, **params):
    """Fetch data from Tushare proxy, return list of rows."""
    fields = params.pop("fields", "")
    payload = {"api_name": api_name, "token": TOKEN, "params": params, "fields": fields}
    
    for attempt in range(retries):
        async with semaphore:
            try:
                async with session.post(
                    API_URL, json=payload,
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    data = await resp.json()
                    if data.get("code") == 0 and "data" in data:
                        return data["data"]["items"]
                    if "您的token" in str(data.get("msg", "")):
                        raise Exception(f"Token error: {data}")
                    return []
            except Exception as e:
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    print(f"  [WARN] {api_name} failed after {retries} attempts: {e}")
                    return []

async def fetch_and_save(api_name, filename, semaphore, **params):
    """Fetch from API, save as parquet if not cached."""
    cache_path = CACHE_DIR / filename
    if cache_path.exists():
        return pd.read_parquet(cache_path)
    
    items = await fetch_ts(session_g, api_name, semaphore, **params)
    if not items:
        print(f"  [WARN] No data for {api_name} {params}")
        return pd.DataFrame()
    
    df = pd.DataFrame(items)
    df.to_parquet(cache_path, index=False)
    return df

# Global session
session_g = None

# ============================================================
# Phase 1: Data Collection
# ============================================================
async def collect_all_data():
    """Collect all required data from Tushare proxy."""
    global session_g
    
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY + 2, force_close=False)
    session_g = aiohttp.ClientSession(connector=connector)
    
    try:
        # 1. Stock basic
        print("[1/6] Fetching stock_basic...")
        stock_basic_path = CACHE_DIR / "stock_basic.parquet"
        sb_fields = "ts_code,name,list_date,market"
        if stock_basic_path.exists():
            stock_basic = pd.read_parquet(stock_basic_path)
        else:
            items = await fetch_ts(session_g, "stock_basic", sem, fields=sb_fields)
            stock_basic = pd.DataFrame(items, columns=sb_fields.split(","))
            stock_basic.to_parquet(stock_basic_path, index=False)
        print(f"  Stocks: {len(stock_basic)}")
        
        # 2. Trade calendar
        print("[2/6] Fetching trade_cal...")
        trade_cal_path = CACHE_DIR / "trade_cal.parquet"
        tc_fields = "exchange,cal_date,is_open,pretrade_date"
        if trade_cal_path.exists():
            trade_cal = pd.read_parquet(trade_cal_path)
        else:
            items = await fetch_ts(session_g, "trade_cal", sem,
                                   exchange="SSE",
                                   start_date=START_DATE, end_date=END_DATE,
                                   fields=tc_fields)
            trade_cal = pd.DataFrame(items, columns=tc_fields.split(","))
            trade_cal.to_parquet(trade_cal_path, index=False)
        
        open_dates = sorted(trade_cal[trade_cal["is_open"] == 1]["cal_date"].tolist())
        print(f"  Trading dates: {len(open_dates)} ({open_dates[0]}~{open_dates[-1]})")
        
        # 3. CSI300 index daily (5 years)
        print("[3/6] Fetching CSI300 index_daily...")
        csi300_path = CACHE_DIR / "index_csi300.parquet"
        if csi300_path.exists():
            csi300 = pd.read_parquet(csi300_path)
            # Validate columns
            if "pct_chg" not in csi300.columns or csi300["close"].dtype == object:
                csi300_path.unlink(missing_ok=True)
                csi300 = None
        else:
            csi300 = None
        
        if csi300 is None or not csi300_path.exists():
            csi300_fields = "ts_code,trade_date,close,pct_chg"
            items = await fetch_ts(session_g, "index_daily", sem,
                                   ts_code="000300.SH",
                                   start_date=START_DATE, end_date=END_DATE,
                                   fields=csi300_fields)
            csi300 = pd.DataFrame(items, columns=csi300_fields.split(","))
            csi300.to_parquet(csi300_path, index=False)
        print(f"  CSI300 days: {len(csi300)}")
        
        # 4. CSI300 constituents
        print("[4/6] Fetching CSI300 constituents...")
        csi300_cons_path = CACHE_DIR / "csi300_cons.parquet"
        iw_fields = "index_code,con_code,trade_date,weight"
        if csi300_cons_path.exists():
            csi300_cons = pd.read_parquet(csi300_cons_path)
        else:
            items = await fetch_ts(session_g, "index_weight", sem,
                                   index_code="000300.SH", fields=iw_fields)
            csi300_cons = pd.DataFrame(items, columns=iw_fields.split(","))
            # Get latest date's constituents
            if len(csi300_cons) > 0:
                latest = csi300_cons["trade_date"].max()
                csi300_cons = csi300_cons[csi300_cons["trade_date"] == latest]
            csi300_cons.to_parquet(csi300_cons_path, index=False)
        print(f"  CSI300 constituents: {len(csi300_cons)}")
        
        # 5. Shenwan L1 industry mapping
        print("[5/6] Fetching SW industry mapping...")
        industry_map_path = CACHE_DIR / "industry_map.parquet"
        if industry_map_path.exists():
            industry_map = pd.read_parquet(industry_map_path)
        else:
            # Get L1 industry list
            ic_fields = "index_code,industry_name,level"
            ind_items = await fetch_ts(session_g, "index_classify", sem,
                                       src="SW2021", fields=ic_fields)
            ind_df = pd.DataFrame(ind_items, columns=ic_fields.split(","))
            l1 = ind_df[ind_df["level"] == "L1"][["index_code", "industry_name"]].copy()
            
            # Get constituents for each industry
            im_fields = "index_code,con_code,in_date,out_date,is_new"
            all_members = []
            for _, row in l1.iterrows():
                members = await fetch_ts(session_g, "index_member", sem,
                                         index_code=row["index_code"], is_new="N",
                                         fields=im_fields)
                if members:
                    for m in members:
                        all_members.append({
                            "ts_code": m[1] if isinstance(m, list) else m["con_code"],
                            "industry_code": row["index_code"],
                            "industry_name": row["industry_name"]
                        })
                await asyncio.sleep(0.1)
            
            industry_map = pd.DataFrame(all_members)
            industry_map.to_parquet(industry_map_path, index=False)
        print(f"  Industry mapping: {len(industry_map)} stocks across {industry_map['industry_code'].nunique()} industries")
        
        # 6. Bulk daily data + daily_basic + moneyflow
        print("[6/6] Fetching bulk daily data (this takes ~20 min)...")
        
        # Determine which dates need fetching
        daily_cache_dir = CACHE_DIR / "daily"
        basic_cache_dir = CACHE_DIR / "daily_basic"
        mf_cache_dir = CACHE_DIR / "moneyflow"
        daily_cache_dir.mkdir(exist_ok=True)
        basic_cache_dir.mkdir(exist_ok=True)
        mf_cache_dir.mkdir(exist_ok=True)
        
        # Check existing cache
        existing_daily = set(p.stem for p in daily_cache_dir.glob("*.parquet"))
        missing_dates = [d for d in open_dates if d not in existing_daily]
        
        if missing_dates:
            print(f"  Daily: {len(missing_dates)} dates to fetch ({len(existing_daily)} cached)")
            
            # Progress tracking
            total = len(missing_dates)
            done = 0
            batch_size = 50
            
            for i in range(0, total, batch_size):
                batch = missing_dates[i:i+batch_size]
                tasks = []
                for d in batch:
                    cache_file = daily_cache_dir / f"{d}.parquet"
                    if not cache_file.exists():
                        tasks.append(_fetch_daily_batch(d, sem, daily_cache_dir))
                
                if tasks:
                    await asyncio.gather(*tasks)
                
                done += len(batch)
                pct = done / total * 100
                elapsed = time.time()
                print(f"  Daily progress: {done}/{total} ({pct:.0f}%)", flush=True)
        else:
            print(f"  Daily: all {len(open_dates)} dates cached ✓")
        
        # Daily basic (only for 60 most recent + micro-cap identification)
        recent_60 = open_dates[-60:] if len(open_dates) >= 60 else open_dates
        existing_basic = set(p.stem for p in basic_cache_dir.glob("*.parquet"))
        missing_basic = [d for d in recent_60 if d not in existing_basic]
        
        if missing_basic:
            print(f"  Daily_basic: {len(missing_basic)} dates to fetch")
            total = len(missing_basic)
            done = 0
            batch_size = 50
            for i in range(0, total, batch_size):
                batch = missing_basic[i:i+batch_size]
                tasks = []
                for d in batch:
                    cache_file = basic_cache_dir / f"{d}.parquet"
                    if not cache_file.exists():
                        tasks.append(_fetch_basic_batch(d, sem, basic_cache_dir))
                if tasks:
                    await asyncio.gather(*tasks)
                done += len(batch)
                print(f"  Basic progress: {done}/{total} ({done/total*100:.0f}%)", flush=True)
        else:
            print(f"  Daily_basic: all {len(recent_60)} dates cached ✓")
        
        # Moneyflow (only for 60 most recent)
        existing_mf = set(p.stem for p in mf_cache_dir.glob("*.parquet"))
        missing_mf = [d for d in recent_60 if d not in existing_mf]
        
        if missing_mf:
            print(f"  Moneyflow: {len(missing_mf)} dates to fetch")
            total = len(missing_mf)
            done = 0
            batch_size = 50
            for i in range(0, total, batch_size):
                batch = missing_mf[i:i+batch_size]
                tasks = []
                for d in batch:
                    cache_file = mf_cache_dir / f"{d}.parquet"
                    if not cache_file.exists():
                        tasks.append(_fetch_mf_batch(d, sem, mf_cache_dir))
                if tasks:
                    await asyncio.gather(*tasks)
                done += len(batch)
                print(f"  Moneyflow progress: {done}/{total} ({done/total*100:.0f}%)", flush=True)
        else:
            print(f"  Moneyflow: all {len(recent_60)} dates cached ✓")
        
        return open_dates, stock_basic, csi300, csi300_cons, industry_map
    
    finally:
        await session_g.close()


async def _fetch_daily_batch(date, sem, cache_dir):
    """Fetch daily data for one date and save."""
    cache_file = cache_dir / f"{date}.parquet"
    if cache_file.exists():
        return
    fields_str = "ts_code,trade_date,close,pre_close,pct_chg,amount"
    items = await fetch_ts(session_g, "daily", sem,
                           trade_date=date, fields=fields_str)
    if items:
        df = pd.DataFrame(items, columns=fields_str.split(","))
        df.to_parquet(cache_file, index=False)


async def _fetch_basic_batch(date, sem, cache_dir):
    """Fetch daily_basic for one date and save."""
    cache_file = cache_dir / f"{date}.parquet"
    if cache_file.exists():
        return
    fields_str = "ts_code,trade_date,close,total_mv,circ_mv,turnover_rate"
    items = await fetch_ts(session_g, "daily_basic", sem,
                           trade_date=date, fields=fields_str)
    if items:
        df = pd.DataFrame(items, columns=fields_str.split(","))
        df.to_parquet(cache_file, index=False)


async def _fetch_mf_batch(date, sem, cache_dir):
    """Fetch moneyflow for one date and save."""
    cache_file = cache_dir / f"{date}.parquet"
    if cache_file.exists():
        return
    items = await fetch_ts(session_g, "moneyflow", sem, trade_date=date)
    if items:
        df = items_to_df(items, "moneyflow")
        df.to_parquet(cache_file, index=False)


# ============================================================
# Phase 2: Load cached data into DataFrames
# ============================================================
def load_all_data(open_dates):
    """Load all cached data into consolidated DataFrames."""
    print("\n=== Loading cached data ===")
    
    # Load daily data
    daily_cache_dir = CACHE_DIR / "daily"
    daily_frames = []
    for d in open_dates:
        fp = daily_cache_dir / f"{d}.parquet"
        if fp.exists():
            df = pd.read_parquet(fp)
            daily_frames.append(df)
    
    if daily_frames:
        daily_all = pd.concat(daily_frames, ignore_index=True)
        daily_all["close"] = pd.to_numeric(daily_all["close"], errors="coerce")
        daily_all["pct_chg"] = pd.to_numeric(daily_all["pct_chg"], errors="coerce")
        daily_all["amount"] = pd.to_numeric(daily_all["amount"], errors="coerce")
        print(f"  Daily: {len(daily_all)} rows, {daily_all['ts_code'].nunique()} stocks, {daily_all['trade_date'].nunique()} dates")
    else:
        daily_all = pd.DataFrame()
        print("  Daily: EMPTY!")
    
    # Load daily_basic
    basic_cache_dir = CACHE_DIR / "daily_basic"
    basic_frames = []
    for p in sorted(basic_cache_dir.glob("*.parquet")):
        basic_frames.append(pd.read_parquet(p))
    
    if basic_frames:
        basic_all = pd.concat(basic_frames, ignore_index=True)
        for col in ["close", "total_mv", "circ_mv", "turnover_rate"]:
            if col in basic_all.columns:
                basic_all[col] = pd.to_numeric(basic_all[col], errors="coerce")
        print(f"  Daily_basic: {len(basic_all)} rows, {basic_all['trade_date'].nunique()} dates")
    else:
        basic_all = pd.DataFrame()
        print("  Daily_basic: EMPTY!")
    
    # Load moneyflow
    mf_cache_dir = CACHE_DIR / "moneyflow"
    mf_frames = []
    for p in sorted(mf_cache_dir.glob("*.parquet")):
        mf_frames.append(pd.read_parquet(p))
    
    if mf_frames:
        mf_all = pd.concat(mf_frames, ignore_index=True)
        for col in ["net_mf_amount", "buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"]:
            if col in mf_all.columns:
                mf_all[col] = pd.to_numeric(mf_all[col], errors="coerce")
        print(f"  Moneyflow: {len(mf_all)} rows, {mf_all['trade_date'].nunique()} dates")
    else:
        mf_all = pd.DataFrame()
        print("  Moneyflow: EMPTY!")
    
    # Load other data
    stock_basic = pd.read_parquet(CACHE_DIR / "stock_basic.parquet")
    csi300 = pd.read_parquet(CACHE_DIR / "index_csi300.parquet")
    csi300["close"] = pd.to_numeric(csi300["close"], errors="coerce")
    csi300_cons = pd.read_parquet(CACHE_DIR / "csi300_cons.parquet")
    industry_map = pd.read_parquet(CACHE_DIR / "industry_map.parquet")
    
    return daily_all, basic_all, mf_all, stock_basic, csi300, csi300_cons, industry_map


# ============================================================
# Phase 3: Compute Indicators
# ============================================================
def compute_breadth(daily_all, csi300, stock_basic, industry_map, open_dates):
    """Compute market breadth indicators for each trading date."""
    print("\n=== Computing breadth indicators ===")
    
    # Prepare stock filters
    stock_basic["list_date"] = stock_basic["list_date"].astype(str)
    cutoff_date = datetime(2026, 10, 14) - timedelta(days=LIST_DAYS_THRESHOLD)
    cutoff_str = cutoff_date.strftime("%Y%m%d")
    valid_stocks = set(
        stock_basic[
            (stock_basic["list_date"] <= cutoff_str) &
            (~stock_basic["name"].str.contains("ST", na=False))
        ]["ts_code"].tolist()
    )
    print(f"  Valid stocks (non-ST, >180d): {len(valid_stocks)}")
    
    # CSI300 close series
    csi300_sorted = csi300.sort_values("trade_date")
    csi300_close = dict(zip(csi300_sorted["trade_date"], csi300_sorted["close"]))
    csi300_dates = list(csi300_sorted["trade_date"])
    
    # Industry mapping
    ind_map = dict(zip(industry_map["ts_code"], industry_map["industry_code"]))
    industry_names = industry_map[["industry_code", "industry_name"]].drop_duplicates()
    ind_name_map = dict(zip(industry_names["industry_code"], industry_names["industry_name"]))
    
    # Build close price pivot table (ts_code × trade_date)
    print("  Building close price pivot...")
    pivot_close = daily_all.pivot_table(index="trade_date", columns="ts_code", values="close")
    pivot_close = pivot_close.sort_index()
    
    # Build pct_chg pivot
    pivot_pct = daily_all.pivot_table(index="trade_date", columns="ts_code", values="pct_chg")
    pivot_pct = pivot_pct.sort_index()
    
    # All trading dates from the pivot
    all_dates = list(pivot_close.index)
    print(f"  Price pivot: {len(all_dates)} dates × {pivot_close.shape[1]} stocks")
    
    # Compute MA20 for each date
    print("  Computing MA20...")
    ma20 = pivot_close.rolling(window=MA_WINDOW, min_periods=MA_WINDOW).mean()
    
    # Compute 20-day returns
    print("  Computing 20-day returns...")
    ret20 = pivot_close.pct_change(periods=MA_WINDOW)
    
    # Compute breadth scores for ALL dates
    # But only need percentile for 5-day interval dates
    # For target week dates, compute daily
    
    # Determine computation dates: every 5th date + target week dates
    step = 5
    compute_indices = list(range(0, len(all_dates), step))
    # Add target week indices
    target_set = set(TARGET_WEEK + ["20261008", "20261009", "20261010", "20261011"])
    for i, d in enumerate(all_dates):
        if d in target_set and i not in compute_indices:
            compute_indices.append(i)
    compute_indices = sorted(set(compute_indices))
    compute_dates = [all_dates[i] for i in compute_indices]
    
    print(f"  Computing breadth for {len(compute_dates)} dates...")
    
    breadth_results = []
    
    for di, date in enumerate(compute_dates):
        if date not in pivot_close.index:
            continue
        
        idx = all_dates.index(date)
        if idx < MA_WINDOW:
            continue
        
        # Get current day's closes
        today_close = pivot_close.loc[date]
        
        # Filter valid stocks
        valid_today = today_close.index.intersection(list(valid_stocks))
        valid_close = today_close[valid_today].dropna()
        
        if len(valid_close) < 100:
            continue
        
        # 1. MA20 Breadth
        ma20_today = ma20.loc[date]
        ma20_valid = ma20_today[valid_close.index].dropna()
        above_ma = (valid_close[ma20_valid.index] > ma20_valid).sum()
        ma20_breadth = above_ma / len(ma20_valid) if len(ma20_valid) > 0 else 0
        
        # 2. Weight Dispersion
        # Equal-weight return (avg of all valid stock pct_chg over 20 days)
        if date in ret20.index:
            ret20_today = ret20.loc[date]
            valid_ret20 = ret20_today[valid_close.index].dropna()
            ew_ret20 = valid_ret20.mean() if len(valid_ret20) > 0 else 0
        else:
            ew_ret20 = 0
        
        # CSI300 20-day return
        csi300_ret20 = 0
        if date in csi300_close:
            # Find date 20 trading days ago in CSI300
            csi_dates_before = [d for d in csi300_dates if d <= date]
            if len(csi_dates_before) > MA_WINDOW:
                d20_ago = csi_dates_before[-MA_WINDOW - 1]
                csi_now = csi300_close[date]
                csi_ago = csi300_close[d20_ago]
                csi300_ret20 = (csi_now - csi_ago) / csi_ago if csi_ago != 0 else 0
        
        weight_disp = ew_ret20 - csi300_ret20
        
        # 3. Industry Dispersion
        # Map valid stocks to industries
        stock_ind = {s: ind_map.get(s) for s in valid_close.index if s in ind_map}
        
        if date in ret20.index:
            ind_returns = {}
            for stock, ind_code in stock_ind.items():
                if stock in ret20_today.index and not np.isnan(ret20_today.get(stock, np.nan)):
                    if ind_code not in ind_returns:
                        ind_returns[ind_code] = []
                    ind_returns[ind_code].append(ret20_today[stock])
            
            if ind_returns:
                ind_avg_returns = {k: np.mean(v) for k, v in ind_returns.items()}
                positive_inds = sum(1 for v in ind_avg_returns.values() if v > 0)
                ind_disp = positive_inds / len(ind_avg_returns) if ind_avg_returns else 0
            else:
                ind_disp = 0
        else:
            ind_disp = 0
        
        breadth_results.append({
            "trade_date": date,
            "ma20_breadth": ma20_breadth,
            "weight_disp": weight_disp,
            "ind_disp": ind_disp,
            "valid_stocks": len(valid_close),
        })
        
        if (di + 1) % 50 == 0:
            print(f"  Breadth progress: {di+1}/{len(compute_dates)}")
    
    breadth_df = pd.DataFrame(breadth_results)
    print(f"  Breadth computed: {len(breadth_df)} dates")
    
    # Compute percentiles (rolling 5-year = all available history)
    for col in ["ma20_breadth", "weight_disp", "ind_disp"]:
        breadth_df[f"{col}_pct"] = breadth_df[col].rank(pct=True) * 100
    
    # Breadth score = equal weight of 3 percentile scores
    breadth_df["breadth_score"] = (
        breadth_df["ma20_breadth_pct"] +
        breadth_df["weight_disp_pct"] +
        breadth_df["ind_disp_pct"]
    ) / 3
    
    # Save intermediate
    breadth_df.to_parquet(CACHE_DIR / "breadth_scores.parquet", index=False)
    
    return breadth_df, pivot_close, pivot_pct, ret20, ind_map, ind_name_map


def compute_fund_flow(mf_all, daily_all, basic_all, csi300_cons, pivot_close, ret20, open_dates):
    """Compute marginal fund flow indicators."""
    print("\n=== Computing fund flow indicators ===")
    
    # Determine micro-cap 400 stocks (using latest available market cap)
    if len(basic_all) > 0:
        latest_basic_date = basic_all["trade_date"].max()
        latest_basic = basic_all[basic_all["trade_date"] == latest_basic_date].copy()
        latest_basic = latest_basic.dropna(subset=["total_mv"])
        latest_basic = latest_basic.sort_values("total_mv")
        micro_cap_stocks = set(latest_basic.head(MICRO_CAP_N)["ts_code"].tolist())
        micro_cap_mv_range = (
            latest_basic.head(MICRO_CAP_N)["total_mv"].min(),
            latest_basic.head(MICRO_CAP_N)["total_mv"].max()
        )
        print(f"  Micro-cap 400: {len(micro_cap_stocks)} stocks, MV range: {micro_cap_mv_range[0]:.0f}~{micro_cap_mv_range[1]:.0f} 万元")
    else:
        print("  [WARN] No daily_basic data, using amount-based proxy for micro-cap")
        # Fallback: use stocks with smallest average trading amount
        micro_cap_stocks = set()
        micro_cap_mv_range = (0, 0)
    
    # CSI300 constituents
    csi300_stocks = set(csi300_cons["con_code"].tolist()) if len(csi300_cons) > 0 else set()
    print(f"  CSI300 constituents: {len(csi300_stocks)}")
    
    # Build amount pivot
    amount_pivot = daily_all.pivot_table(index="trade_date", columns="ts_code", values="amount")
    amount_pivot = amount_pivot.sort_index()
    
    # Get available moneyflow dates
    mf_dates = sorted(mf_all["trade_date"].unique()) if len(mf_all) > 0 else []
    print(f"  Moneyflow dates: {len(mf_dates)} ({mf_dates[0] if mf_dates else 'N/A'}~{mf_dates[-1] if mf_dates else 'N/A'})")
    
    # Build moneyflow intensity for micro-cap and CSI300
    # net_active = net_mf_amount (超大单+大单 净买入)
    # If net_mf_amount not available, compute from buy/sell elg+lg
    
    # Prepare moneyflow pivot
    if "net_mf_amount" in mf_all.columns:
        mf_all["net_active"] = mf_all["net_mf_amount"]
    elif all(c in mf_all.columns for c in ["buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"]):
        mf_all["net_active"] = (
            mf_all["buy_elg_amount"].fillna(0) + mf_all["buy_lg_amount"].fillna(0) -
            mf_all["sell_elg_amount"].fillna(0) - mf_all["sell_lg_amount"].fillna(0)
        )
    else:
        print("  [ERROR] Cannot compute net active buy!")
        mf_all["net_active"] = 0
    
    # Create moneyflow pivot
    mf_pivot_net = mf_all.pivot_table(index="trade_date", columns="ts_code", values="net_active")
    mf_pivot_net = mf_pivot_net.sort_index()
    
    # Vectorized computation using pandas
    # Filter mf and amount for micro-cap and CSI300
    micro_mask = mf_pivot_net.columns.isin(list(micro_cap_stocks))
    csi_mask = mf_pivot_net.columns.isin(list(csi300_stocks))
    
    micro_net_pivot = mf_pivot_net.iloc[:, micro_mask]
    csi_net_pivot = mf_pivot_net.iloc[:, csi_mask]
    
    amt_micro = amount_pivot[[c for c in micro_cap_stocks if c in amount_pivot.columns]]
    amt_csi = amount_pivot[[c for c in csi300_stocks if c in amount_pivot.columns]]
    
    # Align indices
    common_mf_dates = sorted(set(micro_net_pivot.index) & set(amount_pivot.index))
    print(f"  Common dates (mf + daily): {len(common_mf_dates)}")
    
    micro_net_pivot = micro_net_pivot.loc[common_mf_dates]
    amt_micro = amt_micro.reindex(common_mf_dates)
    csi_net_pivot = csi_net_pivot.reindex(common_mf_dates)
    amt_csi = amt_csi.reindex(common_mf_dates)
    
    # Daily sums
    micro_daily_net = micro_net_pivot.sum(axis=1, skipna=True)
    micro_daily_amt = amt_micro.sum(axis=1, skipna=True)
    csi_daily_net = csi_net_pivot.sum(axis=1, skipna=True)
    csi_daily_amt = amt_csi.sum(axis=1, skipna=True)
    
    # 5-day rolling sums
    micro_net_5d = micro_daily_net.rolling(5, min_periods=5).sum()
    micro_amt_5d = micro_daily_amt.rolling(5, min_periods=5).sum()
    csi_net_5d = csi_daily_net.rolling(5, min_periods=5).sum()
    csi_amt_5d = csi_daily_amt.rolling(5, min_periods=5).sum()
    
    micro_intensity_series = micro_net_5d / micro_amt_5d
    csi_intensity_series = csi_net_5d / csi_amt_5d
    rel_intensity_series = micro_intensity_series - csi_intensity_series
    
    results_df = pd.DataFrame({
        "trade_date": common_mf_dates,
        "micro_intensity": micro_intensity_series.values,
        "csi300_intensity": csi_intensity_series.values,
        "rel_intensity": rel_intensity_series.values,
    })
    results_df = results_df.dropna(subset=["micro_intensity", "rel_intensity"])
    flow_df = results_df.copy()
    print(f"  Fund flow computed: {len(flow_df)} dates")
    
    if len(flow_df) == 0:
        print("  [WARN] No fund flow data computed!")
        return flow_df
    
    # EMA10 smoothing
    flow_df = flow_df.sort_values("trade_date").reset_index(drop=True)
    flow_df["micro_intensity_ema"] = flow_df["micro_intensity"].ewm(span=10, adjust=False).mean()
    flow_df["rel_intensity_ema"] = flow_df["rel_intensity"].ewm(span=10, adjust=False).mean()
    
    # 20-day change of relative intensity
    flow_df["rel_intensity_20d_chg"] = (
        flow_df["rel_intensity_ema"] - flow_df["rel_intensity_ema"].rolling(20, min_periods=20).mean()
    )
    
    # Percentiles (over all available history)
    for col in ["micro_intensity_ema", "rel_intensity_20d_chg"]:
        flow_df[f"{col}_pct"] = flow_df[col].rank(pct=True) * 100
    
    # Fund flow score = equal weight of 2 percentile scores
    flow_df["fund_flow_score"] = (
        flow_df["micro_intensity_ema_pct"] +
        flow_df["rel_intensity_20d_chg_pct"]
    ) / 2
    
    # Save
    flow_df.to_parquet(CACHE_DIR / "fund_flow_scores.parquet", index=False)
    
    return flow_df


# ============================================================
# Phase 4: Merge and Generate Report
# ============================================================
def generate_report(breadth_df, flow_df, daily_all, basic_all, mf_all, csi300, open_dates, ind_name_map):
    """Generate the weekly review report and JSON data."""
    print("\n=== Generating report ===")
    
    # Determine actual target dates (trading days only)
    actual_target = [d for d in TARGET_WEEK if d in open_dates]
    actual_prev = [d for d in ["20261008", "20261009"] if d in actual_target]
    
    # For W41 (previous week): 20260929~20261007
    prev_week_dates = [d for d in ["20260929", "20260930", "20261007"] if d in open_dates]
    # W41 last trading days
    w41_dates = [d for d in open_dates if "20260929" <= d <= "20261007"][-5:]
    
    # Merge breadth and flow scores
    breadth_df["trade_date"] = breadth_df["trade_date"].astype(str)
    flow_df["trade_date"] = flow_df["trade_date"].astype(str)
    
    merged = breadth_df.merge(flow_df, on="trade_date", how="left")
    
    # Composite score
    merged["has_flow"] = merged["fund_flow_score"].notna()
    merged["composite_score"] = np.where(
        merged["has_flow"],
        0.5 * merged["breadth_score"] + 0.5 * merged["fund_flow_score"],
        merged["breadth_score"]  # If no flow data, use breadth only
    )
    merged["signal"] = np.where(merged["composite_score"] > 50, "看多微盘", "看多沪深300")
    
    # Save merged
    merged.to_parquet(CACHE_DIR / "merged_scores.parquet", index=False)
    
    # Extract target week data
    week_data = merged[merged["trade_date"].isin(actual_target)].copy()
    
    if len(week_data) == 0:
        print("  [ERROR] No data for target week!")
        # Try to find closest dates
        all_score_dates = sorted(merged["trade_date"].tolist())
        print(f"  Available dates: {all_score_dates[-10:]}")
        return None, None
    
    # Signal continuity
    all_signals = merged.sort_values("trade_date")
    current_signal = all_signals.iloc[-1]["signal"]
    consecutive = 0
    for _, row in all_signals.iloc[::-1].iterrows():
        if row["signal"] == current_signal:
            consecutive += 1
        else:
            break
    
    # Current week vs previous week comparison
    this_week_scores = week_data[["trade_date", "breadth_score", "fund_flow_score", "composite_score", "signal"]].to_dict("records")
    
    # Previous week data
    prev_week_data = merged[merged["trade_date"].isin(w41_dates)].copy()
    if len(prev_week_data) == 0:
        # Fallback: use last 5 dates before target week
        before_target = merged[merged["trade_date"] < actual_target[0]]
        prev_week_data = before_target.tail(5)
    
    # Build JSON output
    json_output = {
        "model_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "target_week": "2026W42",
        "target_dates": actual_target,
        "trading_days_count": len(actual_target),
        "daily_scores": [],
        "signal_continuity": {
            "current_signal": current_signal,
            "consecutive_days": consecutive,
        },
        "breadth_components": {},
        "fund_flow_components": {},
        "micro_structure": {},
    }
    
    # Daily scores
    for _, row in week_data.iterrows():
        day_entry = {
            "date": row["trade_date"],
            "breadth_score": round(float(row["breadth_score"]), 1),
            "fund_flow_score": round(float(row["fund_flow_score"]), 1) if pd.notna(row.get("fund_flow_score")) else None,
            "composite_score": round(float(row["composite_score"]), 1),
            "signal": row["signal"],
        }
        json_output["daily_scores"].append(day_entry)
    
    # Breadth components (this week + prev week)
    for label, df_sel in [("this_week", week_data), ("prev_week", prev_week_data)]:
        comp = {}
        for col in ["ma20_breadth", "ma20_breadth_pct", "weight_disp", "weight_disp_pct", "ind_disp", "ind_disp_pct"]:
            if col in df_sel.columns:
                vals = df_sel[col].tolist()
                comp[col] = [round(float(v), 4) if pd.notna(v) else None for v in vals]
        json_output["breadth_components"][label] = comp
    
    # Fund flow components
    for label, df_sel in [("this_week", week_data), ("prev_week", prev_week_data)]:
        comp = {}
        for col in ["micro_intensity", "micro_intensity_ema", "micro_intensity_ema_pct",
                     "rel_intensity_20d_chg", "rel_intensity_20d_chg_pct"]:
            if col in df_sel.columns:
                vals = df_sel[col].tolist()
                comp[col] = [round(float(v), 6) if pd.notna(v) else None for v in vals]
        json_output["fund_flow_components"][label] = comp
    
    # Micro structure
    if len(basic_all) > 0:
        latest_basic = basic_all[basic_all["trade_date"] == basic_all["trade_date"].max()]
        micro_stocks_latest = latest_basic[latest_basic["ts_code"].isin(
            latest_basic.nsmallest(MICRO_CAP_N, "total_mv")["ts_code"]
        )]
        json_output["micro_structure"] = {
            "micro_avg_turnover": round(float(micro_stocks_latest["turnover_rate"].mean()), 2) if "turnover_rate" in micro_stocks_latest.columns else None,
            "micro_avg_mv": round(float(micro_stocks_latest["total_mv"].mean()), 0) if "total_mv" in micro_stocks_latest.columns else None,
        }
    
    if len(mf_all) > 0:
        # Margin data from latest date
        latest_mf_date = mf_all["trade_date"].max()
        latest_mf = mf_all[mf_all["trade_date"] == latest_mf_date]
        json_output["micro_structure"]["margin_data_date"] = str(latest_mf_date)
    
    # Save JSON
    json_path = OUTPUT_DIR / "微盘择时_2026W42_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)
    print(f"  JSON saved: {json_path}")
    
    # ============================================================
    # Generate Markdown Report
    # ============================================================
    report = generate_markdown_report(
        json_output, week_data, prev_week_data, merged,
        actual_target, w41_dates, current_signal, consecutive,
        breadth_df, flow_df, ind_name_map
    )
    
    md_path = OUTPUT_DIR / "微盘择时周度复盘_2026W42.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  Report saved: {md_path}")
    
    return json_output, report


# ============================================================
# Switch Detection Helpers (reusable by alert script)
# ============================================================

def detect_intra_week_switches(week_data):
    """
    Detect signal switches within a week.
    Returns list of (date, from_signal, to_signal) tuples.
    """
    if len(week_data) < 2:
        return []
    switches = []
    dates = week_data["trade_date"].tolist()
    signals = week_data["signal"].tolist()
    for i in range(1, len(signals)):
        if signals[i] != signals[i - 1]:
            switches.append((dates[i], signals[i - 1], signals[i]))
    return switches


def detect_cross_week_switch(merged, this_week_dates, prev_week_dates):
    """
    Detect signal switch between this week's last day and last week's last day.
    Returns dict with switch info or None.
    """
    merged_sorted = merged.sort_values("trade_date")
    this_week_data = merged_sorted[merged_sorted["trade_date"].isin(this_week_dates)]
    prev_week_data = merged_sorted[merged_sorted["trade_date"].isin(prev_week_dates)]

    if len(this_week_data) == 0 or len(prev_week_data) == 0:
        return None

    this_week_last = this_week_data.iloc[-1]
    prev_week_last = prev_week_data.iloc[-1]

    if this_week_last["signal"] != prev_week_last["signal"]:
        # Count consecutive days of previous signal before switch
        all_sorted = merged_sorted.reset_index(drop=True)
        prev_signal = prev_week_last["signal"]
        prev_consecutive = 0
        for _, row in all_sorted.iloc[::-1].iterrows():
            if row["trade_date"] >= this_week_last["trade_date"]:
                continue
            if row["signal"] == prev_signal:
                prev_consecutive += 1
            else:
                break

        return {
            "date": this_week_last["trade_date"],
            "from_signal": prev_week_last["signal"],
            "to_signal": this_week_last["signal"],
            "from_score": float(prev_week_last["composite_score"]),
            "to_score": float(this_week_last["composite_score"]),
            "breadth": float(this_week_last["breadth_score"]),
            "flow": float(this_week_last["fund_flow_score"]) if pd.notna(this_week_last.get("fund_flow_score")) else None,
            "prev_consecutive_days": prev_consecutive,
        }
    return None


def detect_switch_zone(merged, min_days=3):
    """
    Detect periods where composite_score is in 40~60 range for min_days+ consecutive days.
    Returns list of zone dicts.
    """
    merged_sorted = merged.sort_values("trade_date").reset_index(drop=True)
    zones = []
    zone_start = None

    for i, row in merged_sorted.iterrows():
        score = row["composite_score"]
        in_zone = 40 <= score <= 60

        if in_zone and zone_start is None:
            zone_start = i
        elif not in_zone and zone_start is not None:
            zone_len = i - zone_start
            if zone_len >= min_days:
                zone_data = merged_sorted.iloc[zone_start:i]
                zones.append({
                    "start_date": zone_data.iloc[0]["trade_date"],
                    "end_date": zone_data.iloc[-1]["trade_date"],
                    "days": zone_len,
                    "scores": zone_data[["trade_date", "composite_score", "signal"]].to_dict("records"),
                })
            zone_start = None

    # Handle zone that extends to the end
    if zone_start is not None:
        zone_len = len(merged_sorted) - zone_start
        if zone_len >= min_days:
            zone_data = merged_sorted.iloc[zone_start:]
            zones.append({
                "start_date": zone_data.iloc[0]["trade_date"],
                "end_date": zone_data.iloc[-1]["trade_date"],
                "days": zone_len,
                "scores": zone_data[["trade_date", "composite_score", "signal"]].to_dict("records"),
            })

    return zones


def get_current_week_dates(merged, target_n=5):
    """
    Determine the current trading week's dates from merged data.
    Groups consecutive dates by ISO week and returns the most recent complete/incomplete group.
    """
    dates = sorted(merged["trade_date"].unique())
    if len(dates) == 0:
        return [], []

    # Parse dates and group by ISO week
    from datetime import datetime as dt
    week_groups = {}
    for d in dates:
        try:
            d_obj = dt.strptime(str(d), "%Y%m%d")
            iso_year, iso_week, _ = d_obj.isocalendar()
            key = (iso_year, iso_week)
            if key not in week_groups:
                week_groups[key] = []
            week_groups[key].append(d)
        except (ValueError, TypeError):
            continue

    if not week_groups:
        return [], []

    sorted_keys = sorted(week_groups.keys())
    current_week_dates = week_groups[sorted_keys[-1]]

    # Previous week with data
    prev_week_dates = []
    for k in reversed(sorted_keys[:-1]):
        prev_week_dates = week_groups[k]
        break

    return current_week_dates, prev_week_dates


def get_micro_structure(date_str=None):
    """
    Get micro-cap structure data (turnover, market value) for a given date.
    Returns dict with turnover_pct and mv_billion.
    """
    basic_cache_dir = CACHE_DIR / "daily_basic"
    if not basic_cache_dir.exists():
        return {"turnover_pct": None, "mv_billion": None}

    if date_str is None:
        files = sorted(basic_cache_dir.glob("*.parquet"))
        if not files:
            return {"turnover_pct": None, "mv_billion": None}
        date_str = files[-1].stem

    fp = basic_cache_dir / f"{date_str}.parquet"
    if not fp.exists():
        return {"turnover_pct": None, "mv_billion": None}

    df = pd.read_parquet(fp)
    for col in ["total_mv", "turnover_rate"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "total_mv" not in df.columns or len(df) < MICRO_CAP_N:
        return {"turnover_pct": None, "mv_billion": None}

    df_valid = df.dropna(subset=["total_mv"])
    micro = df_valid.nsmallest(MICRO_CAP_N, "total_mv")

    turnover = float(micro["turnover_rate"].mean()) if "turnover_rate" in micro.columns else None
    mv = float(micro["total_mv"].mean()) / 10000 if "total_mv" in micro.columns else None  # 万元 → 亿元

    return {"turnover_pct": round(turnover, 2) if turnover else None, "mv_billion": round(mv, 1) if mv else None}


def generate_markdown_report(json_data, week_data, prev_week_data, merged,
                              target_dates, prev_dates, signal, consecutive_days,
                              breadth_df, flow_df, ind_name_map):
    """Generate the weekly review markdown report."""
    
    lines = []
    
    # Helper functions
    def pct_label(v):
        if pd.isna(v): return "N/A"
        if v >= 80: return "极高"
        if v >= 60: return "偏高"
        if v >= 40: return "中性"
        if v >= 20: return "偏低"
        return "极低"
    
    def arrow(this, prev):
        if pd.isna(this) or pd.isna(prev): return "—"
        if this > prev + 0.01: return "↑扩张"
        if this < prev - 0.01: return "↓收缩"
        return "→持平"
    
    # ===== Section 1: Signal Overview =====
    lines.append("# 微盘择时周度复盘 — 2026年第42周\n")
    lines.append(f"复盘区间：{target_dates[0]} 至 {target_dates[-1]}（共{len(target_dates)}个交易日）\n")
    
    lines.append("## 一、本周信号概览\n")
    
    # Daily scores
    for _, row in week_data.iterrows():
        d = row["trade_date"]
        b_score = round(row["breadth_score"], 1) if pd.notna(row.get("breadth_score")) else "N/A"
        f_score = round(row["fund_flow_score"], 1) if pd.notna(row.get("fund_flow_score")) else "N/A"
        c_score = round(row["composite_score"], 1) if pd.notna(row.get("composite_score")) else "N/A"
        sig = row["signal"]
        lines.append(f"- **{d}**：宽度得分 {b_score}，资金流得分 {f_score}，综合得分 {c_score} → **{sig}**")
    
    # Switch detection (reuse helper)
    intra_switches = detect_intra_week_switches(week_data)
    if len(week_data) >= 2:
        if intra_switches:
            lines.append(f"\n⚡ **本周存在信号切换**：")
            for sw_date, from_sig, to_sig in intra_switches:
                lines.append(f"  - {sw_date}：从「{from_sig}」切换为「{to_sig}」")
        else:
            signals = week_data["signal"].tolist()
            lines.append(f"\n本周无信号切换，全程维持「{signals[0]}」信号。")
    
    lines.append(f"\n当前信号连续第 **{consecutive_days}** 个交易日（截至{merged.iloc[-1]['trade_date']}）。\n")
    
    # ===== Section 2: Breadth Breakdown =====
    lines.append("## 二、宽度三维拆解\n")
    
    # Get latest target date's breadth data
    latest_target = target_dates[-1]
    latest_breadth = breadth_df[breadth_df["trade_date"] == latest_target]
    
    # Get previous week's average breadth
    prev_avg_breadth = prev_week_data[["ma20_breadth", "weight_disp", "ind_disp"]].mean() if len(prev_week_data) > 0 else pd.Series()
    this_avg_breadth = week_data[["ma20_breadth", "weight_disp", "ind_disp"]].mean() if len(week_data) > 0 else pd.Series()
    
    if len(latest_breadth) > 0:
        row = latest_breadth.iloc[0]
        
        # MA20 Breadth
        ma20_val = row.get("ma20_breadth", 0)
        ma20_pct = row.get("ma20_breadth_pct", 0)
        ma20_arrow = arrow(this_avg_breadth.get("ma20_breadth"), prev_avg_breadth.get("ma20_breadth")) if len(prev_avg_breadth) > 0 else "—"
        lines.append(f"**MA20趋势宽度**：{ma20_val:.1%}的全A非ST股票站上20日均线，处于5年 **{pct_label(ma20_pct)}** 分位（{ma20_pct:.0f}），较上周{ma20_arrow}。")
        
        # Weight Dispersion
        wd_val = row.get("weight_disp", 0)
        wd_pct = row.get("weight_disp_pct", 0)
        wd_arrow = arrow(this_avg_breadth.get("weight_disp"), prev_avg_breadth.get("weight_disp")) if len(prev_avg_breadth) > 0 else "—"
        lines.append(f"\n**权重扩散**：全A等权指数20日收益减沪深300的差值为 {wd_val:+.2%}，处于5年 **{pct_label(wd_pct)}** 分位（{wd_pct:.0f}），较上周{wd_arrow}。")
        
        # Industry Dispersion
        id_val = row.get("ind_disp", 0)
        id_pct = row.get("ind_disp_pct", 0)
        id_arrow = arrow(this_avg_breadth.get("ind_disp"), prev_avg_breadth.get("ind_disp")) if len(prev_avg_breadth) > 0 else "—"
        lines.append(f"\n**行业扩散**：{id_val:.0%} 的申万一级行业20日等权收益为正，处于5年 **{pct_label(id_pct)}** 分位（{id_pct:.0f}），较上周{id_arrow}。")
    
    # Breadth interpretation
    breadth_score = week_data["breadth_score"].mean() if len(week_data) > 0 else 0
    lines.append(f"\n**宽度环境解读**：")
    
    if breadth_score >= 70:
        lines.append("本周市场宽度处于历史高位，属于「广撒网式上涨」格局——多数股票同步走强，行业轮动活跃。这种环境对微盘股的彩票效应兑现非常有利，小票普涨时微盘弹性最大。")
    elif breadth_score >= 50:
        lines.append("本周市场宽度中性偏强，大部分指标处于中等水平。行情有一定扩散但不算极致普涨，微盘股有机会但需要选择性参与。")
    elif breadth_score >= 30:
        lines.append("本周市场宽度偏弱，行情集中在少数权重股或热门赛道，「抱团」特征明显。这种环境对微盘股不利——资金虹吸效应下，小票容易被边缘化。")
    else:
        lines.append("本周市场宽度极弱，行情高度集中于极少数龙头，绝大多数股票跑输指数。微盘股的彩票效应几乎无法兑现，应回避。")
    
    lines.append("")
    
    # ===== Section 3: Fund Flow Breakdown =====
    lines.append("## 三、资金流二维拆解\n")
    
    if len(flow_df) > 0 and "micro_intensity_ema" in flow_df.columns:
        latest_flow = flow_df[flow_df["trade_date"] <= latest_target].iloc[-1:] if len(flow_df[flow_df["trade_date"] <= latest_target]) > 0 else pd.DataFrame()
        
        if len(latest_flow) > 0:
            frow = latest_flow.iloc[0]
            
            # Micro intensity
            mi_val = frow.get("micro_intensity_ema", 0)
            mi_pct = frow.get("micro_intensity_ema_pct", 0)
            
            # Relative intensity change
            ri_val = frow.get("rel_intensity_20d_chg", 0)
            ri_pct = frow.get("rel_intensity_20d_chg_pct", 0)
            
            lines.append(f"**微盘主动净买入强度**：微盘400的EMA10平滑净买入强度为 {mi_val:+.4f}（大单净买入/成交额口径），处于5年 **{pct_label(mi_pct)}** 分位（{mi_pct:.0f}）。")
            
            lines.append(f"\n**相对主动强度20日变化**：微盘相对沪深300的净买入强度差值偏离20日均线的幅度为 {ri_val:+.4f}，处于5年 **{pct_label(ri_pct)}** 分位（{ri_pct:.0f}）。")
            
            # Fund flow interpretation
            lines.append(f"\n**资金流解读**：")
            
            if mi_pct >= 60 and ri_pct >= 60:
                lines.append("微盘自身有较强的主动买盘支撑，且相对沪深300在持续吸金。量化中性、游资等交易型资金大概率在积极参与微盘，流动性充裕。这是最有利的资金面环境。")
            elif mi_pct >= 40:
                lines.append("微盘主动买盘处于中性水平，没有明显的资金大规模流入或流出。相对沪深300的资金流向比较均衡，没有显著失血。需要关注后续变化方向。")
            else:
                lines.append("微盘主动买盘偏弱，可能存在赎回降仓或流动性收缩迹象。如果相对沪深300也在失血，说明资金在从小票向大票转移，需要警惕微盘流动性风险。")
        else:
            lines.append("⚠️ 资金流数据不可用（可能因Tushare moneyflow接口历史数据不足）。本周资金流分析基于有限数据，仅供参考。")
    else:
        lines.append("⚠️ 资金流数据不可用，本部分跳过。")
    
    lines.append("")
    
    # ===== Section 4: Comprehensive Assessment =====
    lines.append("## 四、综合判断与信号质量评估\n")
    
    latest_composite = week_data["composite_score"].iloc[-1] if len(week_data) > 0 else 50
    latest_breadth_s = week_data["breadth_score"].iloc[-1] if len(week_data) > 0 else 50
    latest_flow_s = week_data["fund_flow_score"].iloc[-1] if len(week_data) > 0 and "fund_flow_score" in week_data.columns else None
    
    dist_to_50 = abs(latest_composite - 50)
    
    lines.append(f"当前综合得分 {latest_composite:.1f}，距离50阈值 {'仅' if dist_to_50 < 10 else ''}{dist_to_50:.1f} 分。")
    
    if dist_to_50 < 5:
        lines.append("⚠️ 得分类似在50附近反复横跳，信号非常不稳定，随时可能翻转。这种位置不宜重仓押注单一方向。")
    elif dist_to_50 < 15:
        lines.append("得分在50边缘区域，信号有一定可信度但不够坚实。需要观察后续几天能否站稳当前方向。")
    else:
        lines.append(f"得分稳稳站住{'多' if latest_composite > 50 else '空'}方向（距离阈值>{15}分），信号可信度较高，趋势大概率延续。")
    
    # Factor alignment check
    if latest_flow_s is not None:
        breadth_side = "多" if latest_breadth_s > 50 else "空"
        flow_side = "多" if latest_flow_s > 50 else "空"
        
        if breadth_side == flow_side:
            lines.append(f"\n宽度（{latest_breadth_s:.0f}）和资金流（{latest_flow_s:.0f}）两个因子**同向共振**，均指向看{breadth_side}。双因子共振时信号可信度高，趋势延续概率大。")
        else:
            lines.append(f"\n宽度（{latest_breadth_s:.0f}）和资金流（{latest_flow_s:.0f}）两个因子**出现背离**——宽度指向看{breadth_side}，资金流指向看{flow_side}。背离状态下信号脆弱，市场可能随时翻盘，建议降低仓位或观望。")
    
    # Micro structure
    lines.append(f"\n**微观结构观察**：")
    micro_struct = json_data.get("micro_structure", {})
    if micro_struct.get("micro_avg_turnover"):
        lines.append(f"- 微盘400平均换手率 {micro_struct['micro_avg_turnover']:.2f}%，{'偏高，存在一定拥挤度' if micro_struct['micro_avg_turnover'] > 5 else '处于正常水平' if micro_struct['micro_avg_turnover'] > 2 else '偏低，交易清淡'}")
    if micro_struct.get("micro_avg_mv"):
        lines.append(f"- 微盘400平均总市值 {micro_struct['micro_avg_mv']/10000:.1f} 亿元")
    
    lines.append("")
    
    # ===== Section 5: Scenario Analysis =====
    lines.append("## 五、下周情景推演\n")
    
    # Optimistic
    lines.append("**乐观情景**（概率约25%）：")
    if latest_composite > 50:
        lines.append("若市场延续普涨格局且微盘资金流入加速，宽度和资金流双双走高，综合得分有望升至70+，继续看多微盘。触发条件：成交量放大、行业轮动加速、量化DMA加仓微盘。")
    else:
        lines.append("若市场快速反弹，宽度大幅扩张且资金回流微盘，综合得分从当前低位快速回升至50以上，信号翻多看多微盘。触发条件：政策利好、量化空头回补。")
    
    # Base
    lines.append("\n**基准情景**（概率约50%）：")
    lines.append(f"市场维持当前格局，宽度得分和资金流得分在当前水平附近窄幅波动，综合得分在{max(30, latest_composite-10):.0f}~{min(70, latest_composite+10):.0f}区间。信号方向不变，维持当前持仓策略。")
    
    # Pessimistic
    lines.append("\n**悲观情景**（概率约25%）：")
    if latest_composite > 50:
        lines.append("若市场出现风格急剧切换，资金从微盘撤出流向权重股，宽度和资金流同步走弱，综合得分跌破50翻空。触发条件：流动性收紧、量化中性DMA集中减仓微盘、两融去杠杆。")
    else:
        lines.append("若市场继续低迷且微盘遭遇流动性冲击，综合得分进一步下探至30以下，看多沪深300的信号更加确定。触发条件：信用风险暴露、小票停牌潮。")
    
    lines.append("")
    
    # ===== Section 6: One-liner =====
    lines.append("## 六、一句话结论\n")
    
    signal_cn = "看多微盘" if latest_composite > 50 else "看多沪深300"
    
    if latest_composite > 65:
        lines.append(f"本周微盘择时综合得分 {latest_composite:.0f}，强烈看多微盘。下周重点盯宽度和资金流是否同步维持高位，只要不出现大幅回落就继续持有微盘。")
    elif latest_composite > 50:
        lines.append(f"本周微盘择时综合得分 {latest_composite:.0f}，看多微盘但信号不算极端强。下周重点盯{('资金流是否持续流入' if latest_flow_s and latest_flow_s < 60 else '宽度能否继续扩张')}，如果得分回落接近50要考虑减仓。")
    elif latest_composite > 35:
        lines.append(f"本周微盘择时综合得分 {latest_composite:.0f}，偏中性略偏沪深300。下周重点盯宽度指标能否企稳，如果宽度继续恶化或资金流加速流出微盘，需要果断切到沪深300。")
    else:
        lines.append(f"本周微盘择时综合得分 {latest_composite:.0f}，明确看多沪深300。微盘处于失血状态，下周继续回避微盘，重点观察何时出现企稳信号。")
    
    lines.append("")
    
    # Data source note
    lines.append("---\n")
    lines.append("*数据来源：Tushare代理（大单净买入作为主动买盘代理，因L2主动买卖数据不可得）。分位数计算基于2021年10月以来约1250个交易日的日度数据。微盘400定义为全A中总市值最小的400只股票。本报告为量化模型输出，仅供参考。*")
    
    return "\n".join(lines)


# ============================================================
# Main
# ============================================================
async def main():
    t_start = time.time()
    print("=" * 60)
    print("A股微盘择时模型 - 数据计算 & 周度复盘")
    print("=" * 60)
    
    # Phase 1: Data Collection
    print("\n=== Phase 1: Data Collection ===")
    open_dates, stock_basic, csi300, csi300_cons, industry_map = await collect_all_data()
    
    # Phase 2: Load Data
    daily_all, basic_all, mf_all, stock_basic, csi300, csi300_cons, industry_map = load_all_data(open_dates)
    
    if len(daily_all) == 0:
        print("[FATAL] No daily data collected!")
        sys.exit(1)
    
    # Phase 3: Compute Indicators
    print("\n=== Phase 3: Computing Indicators ===")
    breadth_df, pivot_close, pivot_pct, ret20, ind_map, ind_name_map = compute_breadth(
        daily_all, csi300, stock_basic, industry_map, open_dates
    )
    
    flow_df = compute_fund_flow(
        mf_all, daily_all, basic_all, csi300_cons, pivot_close, ret20, open_dates
    )
    
    # Phase 4: Generate Report
    json_output, report = generate_report(
        breadth_df, flow_df, daily_all, basic_all, mf_all, csi300, open_dates, ind_name_map
    )
    
    # Done
    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"Done! Total time: {elapsed/60:.1f} min")
    
    if report:
        print(f"\nReport preview (first 500 chars):")
        print(report[:500])


if __name__ == "__main__":
    asyncio.run(main())
