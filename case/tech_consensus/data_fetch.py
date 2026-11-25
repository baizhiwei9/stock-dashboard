# -*- coding: utf-8 -*-
"""行情日线获取：多级代理数据源回退体系（单一统一数据入口）

顺序固定：新浪 → 东方财富 → 同花顺（AKShare 系） → Tushare（TS 兜底）
  L1 新浪：akshare.fund_etf_hist_sina  (全历史，无需起止日期)
  L2 东财：akshare.fund_etf_hist_em    (前复权 qfq；接口存在，网络不可达时自动回退)
  L3 同花顺：d.10jqka.com.cn/v6/line   (公开K线接口，JSONP，需 Referer)
  L4 Tushare：fund_daily 优先、daily 备用；token 失效/无权限时自动跳过，不阻塞主链

所有层级统一输出列：date/open/close/high/low/volume（腾讯源兼容列）

统一入口 load_all()：所有方法只从这里取数，全链路数据统一落 data/*.csv 缓存；
缓存缺失或落后于 T-1 交易日时自动沿多级链回填补全。
"""
import os
import sys
import re
import time
import ssl
import urllib.request

# 读取用户权限目录 py_deps 下的 akshare/tushare
_PD = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps"
if _PD not in sys.path and os.path.isdir(_PD):
    sys.path.insert(0, _PD)

import pandas as pd

# ---- dataHub 统一取数（首选）；不可用时逐级回退原通道 ----------------------
_HUB = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier"
_HUB_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps"
for _p in (_HUB, _HUB_DEPS):
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)


def _hub():
    try:
        from data_hub import edb
        return edb
    except Exception as exc:  # 缺依赖/路径异常时告警并回退
        print(f"  [!] dataHub 不可用，回退原源: {type(exc).__name__}: {exc}")
        return None


def _ts_code(code):
    """sh510300 -> 510300.SH。"""
    return f"{code[2:]}.{'SH' if code.startswith('sh') else 'SZ'}"

import akshare as ak

from config import DATA_START, DATA_END, DATA_DIR, SOURCE_ORDER, \
    TK_CSV_CANDIDATES, TS_CFG_DIRS, TS_DEFAULT_HTTP_URL

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


# ---------------- 各层级取数 ----------------

def _get(url, ref=None, timeout=25, retries=2):
    """通用 GET（同花顺需要 Referer）"""
    headers = {"User-Agent": "Mozilla/5.0"}
    if ref:
        headers["Referer"] = ref
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2)
    raise RuntimeError(f"请求失败 {url}: {last}")


def fetch_sina(code):
    """L1 主源：优先 dataHub（CN.MARKET.ETF.PRICE，Tushare fund_daily 未复权），
    不可用时回退 akshare.fund_etf_hist_sina。

    口径核对：两者收盘/开高低完全一致；成交量新浪为「股」、Tushare 为「手」，
    这里统一 ×100 还原成股，保证下游指标口径不变。
    """
    e = _hub()
    if e is not None:
        try:
            hub = e.get_dataset("CN.MARKET.ETF.PRICE", codes=[_ts_code(code)],
                                start_date=DATA_START.replace("-", ""),
                                end_date=DATA_END.replace("-", ""))
            if hub is not None and len(hub):
                h = pd.DataFrame({
                    "date": pd.to_datetime(hub["trade_date"].astype(str)),
                    "open": pd.to_numeric(hub["open"], errors="coerce"),
                    "high": pd.to_numeric(hub["high"], errors="coerce"),
                    "low": pd.to_numeric(hub["low"], errors="coerce"),
                    "close": pd.to_numeric(hub["close"], errors="coerce"),
                    "volume": pd.to_numeric(hub["vol"], errors="coerce") * 100,
                })
                return h.dropna(subset=["close"]).reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] dataHub ETF {code} 失败，回退新浪: {type(exc).__name__}: {str(exc)[:80]}")
    df = ak.fund_etf_hist_sina(symbol=code)
    if df is None or df.empty:
        return None
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_em(code):
    """L2 东财源：akshare.fund_etf_hist_em，symbol=纯数字（如 510300 / 159915）"""
    num = code[2:]
    df = ak.fund_etf_hist_em(symbol=num, period="daily",
                             start_date=DATA_START.replace("-", ""),
                             end_date=DATA_END.replace("-", ""),
                             adjust="qfq")
    if df is None or df.empty:
        return None
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                            "最高": "high", "最低": "low", "成交量": "volume"})
    df = df[["date", "open", "close", "high", "low", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_ths(code):
    """L3 同花顺源：d.10jqka.com.cn/v6/line/hs_<num>/01/last1500.js
    JSONP 包裹，data 字段分号分隔每日K线，每行逗号分隔：
    日期,开,高,低,收,量,额,涨跌幅
    """
    num = code[2:]
    url = f"https://d.10jqka.com.cn/v6/line/hs_{num}/01/last1500.js"
    txt = _get(url, ref="https://stockpage.10jqka.com.cn/")
    m = re.search(r'"data":"([^"]+)"', txt)
    if not m:
        return None
    rows = [r.split(",") for r in m.group(1).split(";") if r.strip()]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.iloc[:, :7]
    df.columns = ["date", "open", "high", "low", "close", "volume", "amount"]
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["date", "open", "close", "high", "low", "volume"]]


# ---------------- L4 Tushare 兜底层 ----------------

def _ts_asset_creds():
    """从既有资产 ts_config.py 读取代理配置：
    返回 (token, http_url)，找不到返回 (None, None)。
    密钥不写入本工程文件，仅引用既有资产（与 feishu_push 引用 feishu_config 同模式）。"""
    for d in TS_CFG_DIRS:
        fp = os.path.join(d, "ts_config.py")
        if not os.path.exists(fp):
            continue
        try:
            ns = {}
            with open(fp, "r", encoding="utf-8") as f:
                code = f.read()
            exec(compile(code, fp, "exec"), ns)
            tok = str(ns.get("TS_TOKEN", "") or "").strip()
            url = str(ns.get("TS_HTTP_URL", "") or "").strip()
            return (tok or None, url or None)
        except Exception:  # noqa: BLE001
            continue
    return None, None


def _ts_url():
    """Tushare 代理地址：环境变量 TUSHARE_HTTP_URL 优先，其次既有资产 ts_config.py，最后默认代理"""
    env_url = os.environ.get("TUSHARE_HTTP_URL", "").strip()
    if env_url:
        return env_url
    _, url = _ts_asset_creds()
    if url:
        return url
    return TS_DEFAULT_HTTP_URL


def _ts_token():
    """读取 Tushare token：环境变量 TUSHARE_TOKEN 优先，其次既有资产 ts_config.py（15k 代理），
    最后 TK_CSV_CANDIDATES 候选文件（tk.csv，与代理 token 同源）。密钥不写入工程文件。"""
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    asset_tok, _ = _ts_asset_creds()
    if asset_tok:
        return asset_tok
    for fp in TK_CSV_CANDIDATES:
        if not fp or not os.path.exists(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if not parts or not parts[0]:
                        continue
                    if parts[0].lower() == "token":      # 兼容 "token,xxx" 表头格式
                        if len(parts) > 1 and parts[1]:
                            return parts[1]
                        continue
                    return parts[0]
        except Exception:  # noqa: BLE001
            continue
    return None


def _ts_pro():
    """初始化并缓存 tushare pro 实例（走代理地址）；token 缺失/初始化失败返回 None"""
    global _TS_PRO_CACHE
    tok = _ts_token()
    if not tok:
        print("  [!] ts 无可用 token（TUSHARE_TOKEN / ts_config.py / tk.csv），跳过 TS 兜底")
        return None
    url = _ts_url()
    key = f"{tok}@{url}"
    if _TS_PRO_CACHE.get("key") == key:
        return _TS_PRO_CACHE.get("pro")
    try:
        import tushare as ts
        pro = ts.pro_api(tok)
        pro._DataApi__http_url = url    # 代理 token 必须指定代理地址，否则报"token不对"
        _TS_PRO_CACHE = {"key": key, "pro": pro}
        return pro
    except Exception as e:  # noqa: BLE001
        print(f"  [!] ts 初始化失败: {e}")
        return None


_TS_PRO_CACHE = {"key": None, "pro": None}


def _ts_code(code):
    """sh510300 -> 510300.SH ; sz159915 -> 159915.SZ"""
    return f"{code[2:]}.{'SH' if code.startswith('sh') else 'SZ'}"


def fetch_ts(code):
    """L4 Tushare 兜底源：fund_daily 优先、daily 备用。
    统一输出 date/open/close/high/low/volume；
    无 token / token 失效 / 接口失败时返回 None（不阻塞 AKShare 主链）。"""
    pro = _ts_pro()
    if pro is None:
        return None
    ts_code = _ts_code(code)
    start = DATA_START.replace("-", "")
    end = DATA_END.replace("-", "")
    for api in ("fund_daily", "daily"):
        try:
            raw = getattr(pro, api)(ts_code=ts_code, start_date=start, end_date=end)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] ts {api} {ts_code} 失败: {type(e).__name__} {str(e)[:80]}")
            continue
        if raw is None or raw.empty:
            continue
        df = raw.rename(columns={"trade_date": "date", "vol": "volume"})
        need = ["date", "open", "close", "high", "low", "volume"]
        if not set(need).issubset(df.columns):
            continue
        df = df[need].copy()
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "close", "high", "low", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    return None


SOURCE_FETCHERS = {
    "sina": fetch_sina,
    "em": fetch_em,
    "ths": fetch_ths,
    "ts": fetch_ts,
}


# ---------------- 统一入口 ----------------

def fetch_kline(code, start=DATA_START, end=DATA_END):
    """按 SOURCE_ORDER 多级回退拉取单标的日线，返回 DataFrame 或 None。
    AK 筛在前、TS 筛兜底：上一层缺数据自动落到下一层。"""
    for src in SOURCE_ORDER:
        try:
            df = SOURCE_FETCHERS[src](code)
            if df is None or len(df) < 100:
                continue
            df = df[(df["date"] >= start) & (df["date"] <= end)]
            df = df.dropna(subset=["open", "close", "high", "low"])
            df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
            if len(df) >= 100:
                return df
        except Exception as e:  # noqa: BLE001
            print(f"  [!] {code} @{src} 失败: {type(e).__name__} {str(e)[:100]}")
    return None


def _last_expected_trade_date():
    """期望数据日期 = 今天之前最近的一个交易日（T-1）。
    交易日历获取失败时回退为自然日近似（today-7），保证缓存不会无限期不刷新。"""
    try:
        e = _hub()
        cal = None
        if e is not None:
            try:
                cdf = e.get_dataset("CN.MARKET.CAL.TRADE",
                                    start_date=(pd.Timestamp.now() - pd.Timedelta(days=30)).strftime("%Y%m%d"),
                                    end_date=pd.Timestamp.now().strftime("%Y%m%d"))
                if cdf is not None and len(cdf):
                    cal = pd.DataFrame({"trade_date": pd.to_datetime(cdf["cal_date"].astype(str))})
            except Exception as exc:  # noqa: BLE001
                print(f"  [!] dataHub 交易日历失败，回退新浪: {str(exc)[:70]}")
        if cal is None:
            cal = ak.tool_trade_date_hist_sina()
            cal["trade_date"] = pd.to_datetime(cal["trade_date"])
        today = pd.Timestamp.now().normalize()
        past = cal.loc[cal["trade_date"] < today, "trade_date"]
        if len(past) > 0:
            return past.max()
    except Exception as e:  # noqa: BLE001
        print(f"  [!] 交易日历获取失败，退回自然日近似: {str(e)[:80]}")
    return pd.Timestamp.now().normalize() - pd.Timedelta(days=7)


def load_all(target="both", use_cache=True, save=True):
    """统一数据入口：加载所需标的日线并存缓存。target: both / etf

    - 缓存存在且最后日期 >= 期望 T-1 交易日 → 直接使用，不再触网；
    - 缓存缺失/落后于 T-1 → 按 SOURCE_ORDER 多级回退拉取（AK 筛 → TS 筛兜底），成功后回填 data/*.csv；
    - 拉取全部失败 → 沿用旧缓存继续运行，避免丢数据。
    六个方法均只从这里拿数，不再各自直连数据源。
    """
    from config import ETF_POOL
    os.makedirs(DATA_DIR, exist_ok=True)
    codes = []
    if target in ("both", "etf"):
        codes += [e["code"] for e in ETF_POOL]
    expected = _last_expected_trade_date()
    print(f"  [i] 期望数据日期（T-1）: {expected.date()}")
    out = {}
    for code in codes:
        fp = os.path.join(DATA_DIR, f"{code}.csv")
        cached = None
        if use_cache and os.path.exists(fp):
            try:
                cached = pd.read_csv(fp, parse_dates=["date"])
            except Exception:  # noqa: BLE001
                cached = None
            if cached is not None and len(cached) > 0:
                last_date = cached["date"].max()
                if last_date >= expected:
                    out[code] = cached
                    continue
                print(f"  [~] {code} 缓存最后日期 {last_date.date()} < 期望 {expected.date()}，回填刷新")
        df = fetch_kline(code)
        if df is not None and len(df) >= 100:
            if save:
                df.to_csv(fp, index=False)
            out[code] = df
        elif cached is not None and len(cached) > 0:
            print(f"  [!] {code} 刷新失败，沿用旧缓存（最后日期 {cached['date'].max().date()}）")
            out[code] = cached
        else:
            print(f"  [!] {code} 数据不足: {None if df is None else len(df)}")
        time.sleep(0.3)  # 限速保护
    return out


def aligned_panel(dict_of_df, codes=None):
    """把多标的 close 对齐成面板 DataFrame[index=date, columns=code]"""
    codes = codes or list(dict_of_df.keys())
    sers = {}
    for c in codes:
        df = dict_of_df.get(c)
        if df is None or len(df) == 0:
            continue
        s = df.set_index("date")["close"].rename(c)
        sers[c] = s
    if not sers:
        return pd.DataFrame()
    panel = pd.concat(sers, axis=1).sort_index()
    return panel