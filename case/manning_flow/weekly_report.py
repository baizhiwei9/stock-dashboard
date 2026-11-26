#!/usr/bin/env python3
"""
曼宁资金流日报 (交易日19:25定时执行)
=====================================
1. 拉取最新行业资金流数据
2. 计算ANB因子排名
3. 输出 Top1 和 Top3 推荐
4. 输出 29个行业 ETF 流入/流出表 (彩色星=风险/净流出, 彩色月=机会/净流入)
5. 发飞书交互卡片
"""

import pandas as pd
import numpy as np
import json
import os
import requests
import sys
import time
from datetime import datetime, timedelta

# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = shared_dir()
        if _d and _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 路径统一口径：case/_lib/paths.py（换机器只改一处）----------------------
try:
    from paths import datahub_root, py_deps, shared_dir
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    import os as _o, sys as _s
    _d = _o.path.dirname(_o.path.abspath(__file__))
    while not _o.path.isfile(_o.path.join(_d, "_lib", "paths.py")):
        _n = _o.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    _s.path.insert(0, _o.path.join(_d, "_lib"))
    from paths import datahub_root, py_deps, shared_dir

# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
# 行业资金流明细与指数日线全部改由 data_hub 提供，本文件不再直连 tushare/同花顺/腾讯。
# 路径不再写死：HUB_ROOT 走 case/_lib/paths.py → stock-dashboard/datahub_path.py，
# 与 build.py / scheduler.py 同一口径（换机器改环境变量或 checkout 位置即可）。
HUB_ROOT = datahub_root()
HUB_DEPS = py_deps()
for _p in (HUB_DEPS, HUB_ROOT):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据缓存统一落在本 case 的 data/（.gitignore 已忽略 case/*/data/），
# 避免脚本目录散落 CSV，也避免迁移后与会话目录各存一套面板、各算一份 ANB。
CACHE_DIR = os.path.join(OUTPUT_DIR, 'data')
os.makedirs(CACHE_DIR, exist_ok=True)
# 凭据统一走 app_secrets（见 DEPLOY.md「敏感配置」）。
# ⚠️ 这里必须**惰性导入**：本机没配 feishu.webhook 时，顶层 import 会让整个脚本在
# 读取 app_secrets 阶段就抛 SecretMissing，连 --no-send 的取数与校验都跑不了，
# 迁移/回归验证因此失去意义。改为只在真正发送时取 webhook，缺配置就只在推送阶段报错。
def _get_webhook() -> str:
    from feishu_config import WEBHOOK_URL  # noqa: E402
    return WEBHOOK_URL
# 沙箱依赖持久化: pip 安装到工作区内 .pydeps, 避免 /tmp 被清理后模块丢失
LOCAL_DEPS = os.path.join(OUTPUT_DIR, '.pydeps')
if os.path.isdir(LOCAL_DEPS):
    sys.path.insert(0, LOCAL_DEPS)

INDUSTRY_ETF_MAP = {
    "半导体": ("512480", "半导体ETF"),
    "银行": ("512800", "银行ETF"),
    "证券": ("512000", "券商ETF"),
    "光伏设备": ("515790", "光伏ETF"),
    "军工装备": ("512660", "军工ETF"),
    "通信设备": ("515880", "通信ETF"),
    "计算机设备": ("512720", "计算机ETF"),
    "软件开发": ("515230", "软件ETF"),
    "化学制药": ("512010", "医药ETF"),
    "煤炭开采加工": ("515220", "煤炭ETF"),
    "钢铁": ("515210", "钢铁ETF"),
    "工业金属": ("512400", "有色ETF"),
    "房地产": ("512200", "房地产ETF"),
    "汽车整车": ("516110", "汽车ETF"),
    "白酒": ("512690", "酒ETF"),
    "食品加工制造": ("515170", "食品饮料ETF"),
    "生物制品": ("159992", "创新药ETF"),
    "医疗器械": ("159883", "医疗器械ETF"),
    "化学制品": ("159870", "化工ETF"),
    "风电设备": ("516950", "风电ETF"),
    "电力": ("159611", "电力ETF"),
    "消费电子": ("159732", "消费电子ETF"),
    "电池": ("159755", "电池ETF"),
    "贵金属": ("159933", "金ETF"),
    "建筑材料": ("159745", "建材ETF"),
    "物流": ("516910", "物流ETF"),
    "养殖业": ("159865", "养殖ETF"),
    "通信服务": ("515050", "5GETF"),
    "IT服务": ("515080", "AI智能ETF"),
}

W = 5  # ANB平滑窗口（回放验证最优，W3/5/7 接近但 W5 段内命中率最高）

# 宽基指数配置：行业成分映射（基于各指数实际权重分布近似）
INDEX_CONFIG = {
    "全A": {
        "code": "000985.SH",
        "industries": list(INDUSTRY_ETF_MAP.keys()),
    },
    "沪深300": {
        "code": "000300.SH",
        "industries": ["银行", "证券", "白酒", "食品加工制造", "化学制药", "生物制品",
                       "医疗器械", "房地产", "汽车整车", "煤炭开采加工", "钢铁",
                       "工业金属", "贵金属", "电力", "物流", "通信服务"],
    },
    "中证500": {
        "code": "000905.SH",
        "industries": ["半导体", "光伏设备", "通信设备", "计算机设备", "软件开发",
                       "军工装备", "化学制品", "风电设备", "消费电子", "电池",
                       "建筑材料", "养殖业", "IT服务"],
    },
    "中证1000": {
        "code": "000852.SH",
        "industries": ["半导体", "光伏设备", "电池", "消费电子", "软件开发", "IT服务",
                       "化学制品", "风电设备", "建筑材料", "养殖业", "计算机设备", "通信设备"],
    },
    "创业板": {
        "code": "399006.SZ",
        "industries": ["电池", "光伏设备", "医疗器械", "软件开发", "IT服务",
                       "消费电子", "通信设备", "半导体"],
    },
    "科创50": {
        "code": "000688.SH",
        "industries": ["半导体", "软件开发", "IT服务", "计算机设备", "消费电子"],
    },
}


STALE_LIMIT = 3  # 数据日期相对最近交易日允许的最大滞后天数(自然日), 超过则拒绝发送
CACHE_PATH = os.path.join(CACHE_DIR, 'industry_moneyflow_real.csv')


def get_recent_trade_dates(n=12, end=None):
    """获取最近 n 个交易日(截至 end, 默认今天)。失败时用工作日近似兜底"""
    try:
        from data_hub import edb
        end = end or datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=45)).strftime('%Y%m%d')
        cal = edb.get_dataset('CN.MARKET.CAL.TRADE', start_date=start, end_date=end)
        dates = sorted(cal['cal_date'].astype(str)) if cal is not None and len(cal) else []
        return dates[-n:]
    except Exception as e:
        print(f"  交易日历获取失败({e}), 用工作日近似")
        dates, d = [], datetime.now()
        while len(dates) < n:
            if d.weekday() < 5:
                dates.append(d.strftime('%Y%m%d'))
            d -= timedelta(days=1)
        return sorted(dates)


def load_cache():
    """读取本地缓存(仅作历史补充, 绝不作最新数据冒充)"""
    if os.path.exists(CACHE_PATH):
        return pd.read_csv(CACHE_PATH, dtype={'trade_date': str})
    return pd.DataFrame()


def merge_with_cache(new_df, existing=None):
    """合并新拉取数据与旧缓存: 新数据优先, 去重后全量返回"""
    if existing is None:
        existing = load_cache()
    if existing.empty:
        combined = new_df
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=['trade_date', 'industry'], keep='last')
    if 'trade_date' not in combined.columns or 'industry' not in combined.columns:
        # 面板缺主键列（历史上游结构变化时会出现），此时无法排序去重，
        # 原样返回让上层新鲜度校验判为不新鲜，静默不发，而不是抛 KeyError 炸掉整条链路。
        return combined
    combined = combined.sort_values(['trade_date', 'industry']).reset_index(drop=True)
    return combined


#: DataHub sector_flow 输出 schema → 本文件 ANB 因子所需 schema 的字段映射。
#: DataHub 侧列名是 sector_name / inflow_amount / outflow_amount / main_net_inflow / change_pct，
#: 本文件 compute_anb 按 industry / net_buy_amount / net_sell_amount / net_amount / pct_change 取数。
#: 两套命名统一在这里归一，避免下游到处 KeyError。
_SF_COLMAP = {
    'sector_name': 'industry',
    'inflow_amount': 'net_buy_amount',
    'outflow_amount': 'net_sell_amount',
    'main_net_inflow': 'net_amount',
    'change_pct': 'pct_change',
}


def _normalize_sector_flow(df, trade_date):
    """把 DataHub sector_flow 的返回归一成本文件 ANB 面板所需的列名。"""
    df = df.copy()
    for src, dst in _SF_COLMAP.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]
    if 'trade_date' not in df.columns:
        df['trade_date'] = str(trade_date)
    return df


def fetch_from_tushare(recent_dates):
    """
    DataHub（股票模块）逐日拉取行业资金流明细 (唯一取数通道).

    数据由 data_hub.collectors.stocks.get_sector_flow_detail 提供，
    底层复用 DataHub 的 Tushare 统一凭据/代理/全局串行限流，本文件不再自建 HTTP 客户端。
    倒序拉取(最新日期优先), 每个失败日期最多3轮重试(间隔递增).
    返回 (df_ok, missing_dates)
    """
    from data_hub.collectors.stocks.sector_flow import get_sector_flow_detail
    all_data = []
    missing = []

    # 倒序: 先保证最新交易日
    for dt in reversed(recent_dates):
        ok = False
        for attempt in range(3):  # 每日期最多3次尝试
            try:
                df = get_sector_flow_detail(dt)
                if df is not None and len(df) > 0:
                    # DataHub 各 tier 源返回的是「当日快照」，列名与本文件 ANB 面板不同，
                    # 且不带 trade_date。统一在这里归一（含补 trade_date），
                    # 否则下游 compute_anb 会 KeyError。
                    df = _normalize_sector_flow(df, dt)
                    if 'industry' not in df.columns:
                        # 归一后仍无行业主键，这条数据无法进面板，按该日缺失处理
                        print(f"  {dt} 归一后仍缺 industry 列，视为该日缺失")
                        time.sleep(3)
                        continue
                    all_data.append(df)
                    ok = True
                    break
                else:
                    time.sleep(3)
            except Exception as e:
                wait = 4 + attempt * 4
                print(f"  {dt} 第{attempt+1}次失败({str(e)[:50]}), {wait}s后重试")
                time.sleep(wait)
        if not ok:
            missing.append(dt)
            print(f"  {dt} 重试3次仍失败, 记录缺失")
            time.sleep(3)

    if not all_data:
        return pd.DataFrame(), missing
    return pd.concat(all_data, ignore_index=True), missing


def fetch_from_akshare(recent_dates):
    """已并入 DataHub 统一通道（get_sector_flow_detail 内置同花顺口径），不再单独走 akshare。"""
    return pd.DataFrame()


def fetch_latest_moneyflow():
    """
    三级数据源拉取最新行业资金流 (AKShare -> Tushare -> 网页兜底).
    强制数据新鲜度: 合并后最新日期必须 >= 最近交易日-STALE_LIMIT, 否则拒绝使用.
    返回 (df, is_fresh, meta)
      - df: 合并后的全量面板 (含旧缓存历史, 但最新日期取决于本次拉取)
      - is_fresh: 数据是否新鲜 (决定能否发送)
      - meta: 诊断信息
    """
    meta = {'sources': [], 'latest_pulled': None, 'missing': [], 'expected_latest': None}

    # 0. 确定目标交易日窗口 (多拉几天保证W=5窗口有足够历史)
    recent_dates = get_recent_trade_dates(n=W + 8)
    meta['expected_latest'] = recent_dates[-1] if recent_dates else None
    print(f"目标交易日: {recent_dates[0]} ~ {recent_dates[-1]} (共{len(recent_dates)}天)")

    # 1. AKShare 优先
    df_ak = fetch_from_akshare(recent_dates)
    if df_ak is not None and len(df_ak) > 0:
        meta['sources'].append('akshare')
        print(f"  AKShare 成功, {df_ak['trade_date'].nunique()} 天")
    else:
        meta['sources'].append('akshare(fail)')

    # 2. Tushare 主源 (AKShare口径一致时以AKShare为准, 否则用Tushare逐日)
    df_new, missing = fetch_from_tushare(recent_dates)
    meta['sources'].append('tushare')
    meta['missing'] = missing
    if not df_new.empty:
        meta['latest_pulled'] = str(df_new['trade_date'].max())
        print(f"  Tushare 成功拉取 {df_new['trade_date'].nunique()} 天, 最新={meta['latest_pulled']}")

    # 2.5 网页兜底已取消：取数统一由 DataHub 股票模块承担，不再直连同花顺网页
    if df_new.empty:
        meta['sources'].append('web-ths(removed)')
        print('  DataHub 未取到新数据，跳过高风险网页兜底（不直连同花顺）')

    # 3. 新鲜度校验: 合并后最新日期必须接近最近交易日
    combined = merge_with_cache(df_new) if not df_new.empty else load_cache()

    if combined.empty:
        return pd.DataFrame(), False, meta

    latest_in_data = str(combined['trade_date'].max())
    expected = meta['expected_latest']
    is_fresh = False
    if latest_in_data and expected:
        try:
            ld = datetime.strptime(latest_in_data, '%Y%m%d')
            ed = datetime.strptime(expected, '%Y%m%d')
            lag = (ed - ld).days
            is_fresh = lag <= STALE_LIMIT
            meta['lag_days'] = lag
            print(f"  最新数据日期: {latest_in_data} vs 最近交易日: {expected} (滞后{lag}天, 阈值{STALE_LIMIT})")
        except Exception:
            is_fresh = False
    if not is_fresh:
        print(f"  !! 数据不新鲜: 最新={latest_in_data}, 最近交易日={expected}. 按用户要求将拒绝发送报告")

    # 4. 落盘 (不覆盖已有, 追加合并)
    try:
        combined.to_csv(CACHE_PATH, index=False)
    except Exception as e:
        print(f"  缓存保存失败: {e}")

    return combined, is_fresh, meta


def compute_anb(df):
    """计算ANB因子"""
    df = df[df['industry'].isin(INDUSTRY_ETF_MAP.keys())].copy()
    df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df = df.sort_values(['date', 'industry']).reset_index(drop=True)
    
    for col in ['net_buy_amount', 'net_sell_amount', 'net_amount', 'pct_change']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    daily = df.groupby('date').agg(total_buy=('net_buy_amount', 'sum'), total_sell=('net_sell_amount', 'sum'))
    df = df.merge(daily, on='date')
    df['buy_ratio'] = df['net_buy_amount'] / df['total_buy']
    df['sell_ratio'] = df['net_sell_amount'] / df['total_sell']
    df['amount'] = df['net_buy_amount'] + df['net_sell_amount']
    daily_amt = df.groupby('date')['amount'].sum().rename('total_amount')
    df = df.merge(daily_amt, on='date')
    df['amount_ratio'] = df['amount'] / df['total_amount']
    df['anb'] = (df['buy_ratio'] - df['sell_ratio']) / df['amount_ratio'].replace(0, np.nan)
    df['net_ratio'] = df['net_amount'] / df['amount'].replace(0, np.nan)
    
    anb_panel = df.set_index(['date', 'industry'])['anb'].unstack('industry')
    net_panel = df.set_index(['date', 'industry'])['net_amount'].unstack('industry')
    net_ratio_panel = df.set_index(['date', 'industry'])['net_ratio'].unstack('industry')
    pct_panel = df.set_index(['date', 'industry'])['pct_change'].unstack('industry')
    
    anb_ma = anb_panel.rolling(W, min_periods=3).mean()
    
    return anb_ma, net_panel, net_ratio_panel, pct_panel


def fetch_index_prices(report_date):
    """DataHub 股票模块宽基指数日线，返回 {name: DataFrame[trade_date, open, close, high, low]}"""
    ts_codes = {
        "全A": "000985.CSI",
        "沪深300": "000300.SH",
        "中证500": "000905.SH",
        "中证1000": "000852.SH",
        "创业板": "399006.SZ",
        "科创50": "000688.SH",
    }
    result = {}
    for name, cfg in INDEX_CONFIG.items():
        ts_code = ts_codes.get(name)
        if not ts_code:
            continue
        try:
            from data_hub.collectors.stocks import get_index_daily
            df = get_index_daily(ts_code=ts_code, lookback=90)
            if df is None or len(df) == 0:
                print(f"  指数 {name}({ts_code}) DataHub 返回空")
                continue
            df = df.copy()
            if 'trade_date' not in df.columns and 'date' in df.columns:
                df['trade_date'] = df['date']
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            for col in ('open', 'close', 'high', 'low'):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.sort_values('trade_date').reset_index(drop=True)
            result[name] = df
        except Exception as e:
            print(f"  指数 {name}({ts_code}) 拉取失败: {e}")
    return result


def compute_index_views(anb_ma, net_panel, index_prices):
    """
    计算宽基指数多空观点 (价格趋势优先, 修正微正ANB覆盖明确趋势的缺陷):
    - 聚合ANB: 成分行业ANB等权平均 (W日均值, 滞后参考, 不再单独决定方向)
    - 当日净流入: 成分行业当日 net_amount 加总 (即时资金确认)
    - 价格趋势: close vs MA20 + 5日动量 (趋势窗口保持不动)
    - 判定规则:
      1) 5日动量显著为负(<=-3%) -> 直接看空; 仅当成分当日净流入>=+30亿(强抄底)才转中性
      2) 5日动量显著为正(>=+3%) -> 直接看多; 仅当成分当日净流出<=-30亿(强出逃)才转中性
      3) |动量|<3% 且趋势明确: 跟随趋势, 聚合ANB强反向(|ANB|>=EPS且反向)才升级为中性
      4) 趋势中性: 聚合ANB显著(|ANB|>=EPS)且当日净流入同向才多空, 否则中性
    这样"本周暴跌却聚合ANB微正"的板块(如创业板/半导体)不再被误判为看多/中性.
    返回 [(name, code, agg_anb, trend_signal, view, detail_str), ...]
    """
    if anb_ma.empty:
        return []

    latest_date = anb_ma.index[-1]
    anb_row = anb_ma.loc[latest_date]
    net_row = net_panel.loc[latest_date]
    views = []

    # 显著阈值: 聚合ANB |值|<EPS 视为资金流无信号; 5日动量|值|<MOM_FLOOR 视为趋势不显著
    EPS = 0.03
    MOM_FLOOR = 3.0
    # 当日成分净流入缓冲(亿): 只有超强反向资金才可压过显著价格趋势
    NET_BUFFER = 30.0

    for name, cfg in INDEX_CONFIG.items():
        industries = cfg['industries']
        code = cfg['code']

        # 1. 聚合ANB (W日均值, 滞后参考) + 当日成分净流入 (即时确认)
        constituent_anbs = [anb_row.get(ind) for ind in industries if pd.notna(anb_row.get(ind))]
        agg_anb = np.mean(constituent_anbs) if constituent_anbs else 0.0
        day_net = sum(float(net_row.get(ind, 0)) for ind in industries)

        # 2. 价格趋势
        trend_signal = 0  # -1=空, 0=中性, 1=多
        trend_detail = "无数据"
        mom5 = None
        if name in index_prices and len(index_prices[name]) >= 20:
            px = index_prices[name]
            close = px['close'].iloc[-1]
            ma20 = px['close'].rolling(20).mean().iloc[-1]
            close_5d = px['close'].iloc[-6] if len(px) >= 6 else px['close'].iloc[0]
            mom5 = (close - close_5d) / close_5d * 100

            above_ma = close > ma20
            momentum_up = mom5 > 0

            if above_ma and momentum_up:
                trend_signal = 1
            elif not above_ma and not momentum_up:
                trend_signal = -1
            else:
                trend_signal = 0

            trend_detail = (f"收盘={close:.1f} MA20={ma20:.1f} {'(上)' if above_ma else '(下)'} "
                            f"5日动量={mom5:+.2f}% 当日成分净流入={day_net:+.1f}亿")

        # 3. 综合判定: 价格趋势优先
        if mom5 is not None and mom5 <= -MOM_FLOOR:
            # 显著下跌, 直接看空; 除非当日成分出现超强抄底
            view = "中性" if day_net >= NET_BUFFER else "看空"
        elif mom5 is not None and mom5 >= MOM_FLOOR:
            # 显著上涨, 直接看多; 除非当日成分出现超强出逃
            view = "中性" if day_net <= -NET_BUFFER else "看多"
        elif trend_signal != 0:
            # 趋势明确但不显著: 跟随趋势, 聚合ANB强反向才升级为中性
            if (trend_signal > 0 and agg_anb >= -EPS) or (trend_signal < 0 and agg_anb <= EPS):
                view = "看多" if trend_signal > 0 else "看空"
            else:
                view = "中性"
        else:
            # 趋势中性: 聚合ANB显著且当日资金同向才给信号
            if agg_anb >= EPS and day_net > 0:
                view = "看多"
            elif agg_anb <= -EPS and day_net < 0:
                view = "看空"
            else:
                view = "中性"

        views.append((name, code, agg_anb, trend_signal, view, trend_detail))

    return views


def send_feishu(top1_ind, top3_list, anb_row, net_row, net_ratio_row, report_date, index_views=None, mom5_row=None):
    """发送飞书报告"""
    
    elements = []
    
    # 1. 标题区
    date_str = report_date.strftime('%Y-%m-%d')
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**曼宁资金流日报 | {date_str}**\n因子: ANB=(买占比-卖占比)/成交额占比, W={W}日MA | 数据源: AKShare→Tushare 最新行业资金流\n近期确认: 当日净流入>0 或 5日动量>-3%" }
    })
    elements.append({"tag": "hr"})
    
    # 2. Top1 推荐
    top1_code, top1_etf = INDUSTRY_ETF_MAP[top1_ind]
    top1_anb = anb_row.get(top1_ind, 0)
    top1_net = net_row.get(top1_ind, 0)
    top1_nr = net_ratio_row.get(top1_ind, 0)
    top1_mom5 = mom5_row.get(top1_ind, np.nan) if mom5_row is not None else np.nan
    top1_mom5_s = f"{top1_mom5:+.2f}%" if not pd.isna(top1_mom5) else "N/A"
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": 
            f"**Top1 推荐 (W7K1 集中策略)**\n"
            f"行业: {top1_ind} | 买入: {top1_code} {top1_etf}\n"
            f"ANB = {top1_anb:.4f} | 净流入 = {top1_net:+.1f}亿 | 净流入占比 = {top1_nr*100:+.2f}% | 5日动量 = {top1_mom5_s}"
        }
    })
    elements.append({"tag": "hr"})
    
    # 3. Top3 推荐
    top3_lines = []
    for rank, (ind, anb_val) in enumerate(top3_list, 1):
        code, etf = INDUSTRY_ETF_MAP[ind]
        net = net_row.get(ind, 0)
        nr = net_ratio_row.get(ind, 0)
        mom5 = mom5_row.get(ind, np.nan) if mom5_row is not None else np.nan
        mom5_s = f"{mom5:+.2f}%" if not pd.isna(mom5) else "N/A"
        top3_lines.append(f"{rank}. {ind} -> {code} {etf} | ANB={anb_val:.4f} | 净流入={net:+.1f}亿 | 5日动={mom5_s}")
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**Top3 推荐 (W10K3 平衡策略)**\n{chr(10).join(top3_lines)}"}
    })
    elements.append({"tag": "hr"})
    
    # 4. 29行业 ETF 流入/流出表
    # 星标=流入, 闪电=流出
    all_industries = sorted(INDUSTRY_ETF_MAP.keys())
    
    # 按净流入排序
    ind_net = [(ind, net_row.get(ind, 0), anb_row.get(ind, 0), net_ratio_row.get(ind, 0)) for ind in all_industries]
    ind_net.sort(key=lambda x: x[1], reverse=True)
    
    # 构建表格文本
    table_lines = []
    table_lines.append(f"{'行业':<10} {'ETF':<8} {'名称':<12} {'净额(亿)':>8} {'ANB':>8} {'标记':>4}")
    table_lines.append("─" * 58)
    
    for ind, net, anb, nr in ind_net:
        code, etf = INDUSTRY_ETF_MAP[ind]
        symbol = "🌙" if net > 0 else "🌟"  # 彩色月=机会(净流入), 彩色星=风险(净流出)
        table_lines.append(f"{ind:<10} {code:<8} {etf:<12} {net:>+7.1f} {anb:>8.4f} {symbol:>4}")
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**29行业资金流入/流出表**\n🌟 = 风险(资金净流出) | 🌙 = 机会(资金净流入)\n```\n{chr(10).join(table_lines)}\n```"}
    })
    elements.append({"tag": "hr"})
    
    # 4.5 宽基指数多空观点
    n_long = n_short = n_neutral = 0
    if index_views:
        view_emoji = {"看多": "🟢", "看空": "🔴", "中性": "⚪"}
        idx_lines = []
        idx_header = f"{'指数':<8} {'代码':<10} {'聚合ANB':>8} {'趋势':>4} {'观点':>4} {'趋势详情'}"
        idx_lines.append(idx_header)
        idx_lines.append("─" * 70)
        for name, code, agg_anb, trend_sig, view, detail in index_views:
            trend_str = "多" if trend_sig > 0 else "空" if trend_sig < 0 else "中"
            idx_lines.append(f"{name:<8} {code:<10} {agg_anb:>+8.4f} {trend_str:>4} {view_emoji.get(view, '')} {view:<4} {detail}")

        # 汇总
        n_long = sum(1 for _, _, _, _, v, _ in index_views if v == "看多")
        n_short = sum(1 for _, _, _, _, v, _ in index_views if v == "看空")
        n_neutral = sum(1 for _, _, _, _, v, _ in index_views if v == "中性")
        summary = f"看多 {n_long} | 看空 {n_short} | 中性 {n_neutral}"

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**宽基指数多空观点**\n聚合ANB=成分行业ANB等权均值 | 趋势=收盘vs MA20 + 5日动量 | 综合信号=趋势优先, ANB/资金反向校验\n```\n{chr(10).join(idx_lines)}\n```\n{summary}"}
        })
        elements.append({"tag": "hr"})
    
    # 5. 市场总览
    total_inflow = sum(net for _, net, _, _ in ind_net if net > 0)
    total_outflow = sum(abs(net) for _, net, _, _ in ind_net if net < 0)
    n_inflow = sum(1 for _, net, _, _ in ind_net if net > 0)
    n_outflow = sum(1 for _, net, _, _ in ind_net if net < 0)
    net_total = total_inflow - total_outflow
    
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": 
            f"**市场总览**\n"
            f"流入行业: {n_inflow}个 (合计+{total_inflow:.1f}亿) | 流出行业: {n_outflow}个 (合计{total_outflow:.1f}亿)\n"
            f"全市场净额: {net_total:+.1f}亿 | 情绪: {'偏多' if n_inflow > n_outflow else '偏空' if n_outflow > n_inflow else '均衡'}\n"
            f"\n**操作建议**: 周一开盘买入推荐ETF, 等权配置, 每周五调仓"
        }
    })
    
    _has_risk = n_short >= 3
    _has_opp = n_long >= 3
    _mark = ('注意风险 | ' if _has_risk else '') + ('注意机会 | ' if _has_opp else '')
    elements += _bs('manning', 'card')
    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": f"曼宁资金流日报 | {_mark}来自 DoMate"}, "template": "blue"},
            "elements": elements
        }
    }
    
    try:
        webhook = _get_webhook()
        resp = requests.post(webhook, json=msg, timeout=15)
        result = resp.json()
        print(f"飞书发送: {resp.status_code} - {result.get('StatusMessage', resp.text[:80])}")
        # 飞书对无效/已轮换 webhook 返回的是 HTTP 200 + {"code":19001,...}，
        # 不是异常。只看 requests 抛不抛异常会把这种失败判成成功。
        # code/StatusCode 非 0、或 HTTP 非 200，都算失败（issue #53 验收第 2 条）。
        rcode = result.get("StatusCode", result.get("code", 0))
        if resp.status_code != 200 or rcode != 0:
            print(f"飞书返回失败: HTTP {resp.status_code}, code={rcode}")
            return False
        return True
    except Exception as e:
        print(f"飞书发送失败: {e}")
        return False


def main():
    print("=" * 60)
    print(f"曼宁资金流日报 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 1. 拉取最新数据 (AKShare -> Tushare -> 网页兜底 + 新鲜度校验)
    print("\n[1] 拉取最新资金流数据...")
    df, is_fresh, meta = fetch_latest_moneyflow()
    if df.empty:
        print("  数据为空, 退出")
        return

    # 新鲜度门控: 数据不新鲜一律拒绝发送 (用户硬性要求, 不用旧数据骗人)
    if not is_fresh:
        print("\n  ⛔ 数据新鲜度校验未通过: 最新数据日期 = %s, 最近交易日 = %s" % (
            meta.get('latest_pulled') or df['trade_date'].max(),
            meta.get('expected_latest')))
        print("     按用户要求(必须全用最新数据), 本次不发送飞书报告, 不覆盖推荐结果.")
        print("     缺数交易日: %s | 已用数据源: %s" % (meta.get('missing', []), meta.get('sources', [])))
        return

    # 2. 计算ANB
    print("\n[2] 计算ANB因子...")
    anb_ma, net_panel, net_ratio_panel, pct_panel = compute_anb(df)
    
    latest_date = anb_ma.index[-1]
    anb_row = anb_ma.loc[latest_date]
    net_row = net_panel.loc[latest_date]
    net_ratio_row = net_ratio_panel.loc[latest_date]
    
    print(f"  最新日期: {latest_date.strftime('%Y-%m-%d')}")
    
    # 行业5日动量(近5个交易日累计涨跌%, 用于Top推荐的近期确认)
    def _prod_ret(x):
        x = x[~np.isnan(x)]
        if len(x) == 0:
            return np.nan
        return x.prod() - 1
    mom5_panel = (1 + pct_panel / 100.0).rolling(5, min_periods=3).apply(_prod_ret, raw=True) * 100.0
    mom5_row = mom5_panel.loc[latest_date]
    
    # 3. Top1 和 Top3 (近期确认过滤)
    # 近期确认: 当日净流入>0 或 5日动量不显著为负(>-3%), 二选一即通过;
    # 避免"7日均值仍高、但近期已转跌/资金流出"的行业被推荐(如白酒10/21-10/22连续下跌).
    MOM5_FLOOR = -3.0  # 5日动量显著为负阈值(%)
    anb_sorted = anb_row.dropna().sort_values(ascending=False)
    top_candidates = []
    for ind in anb_sorted.index:
        net_v = net_row.get(ind, 0)
        mom_v = mom5_row.get(ind, np.nan)
        mom_v = -999.0 if pd.isna(mom_v) else mom_v
        if net_v > 0 or mom_v > MOM5_FLOOR:
            top_candidates.append(ind)
        if len(top_candidates) >= 3:
            break
    # fallback1: 不足3个时按"当日净流入>0"补充
    if len(top_candidates) < 3:
        for ind in anb_sorted.index:
            if ind in top_candidates:
                continue
            if net_row.get(ind, 0) > 0:
                top_candidates.append(ind)
            if len(top_candidates) >= 3:
                break
    # fallback2: 仍不足则按原ANB排名补充(标注由飞书文本提示)
    if len(top_candidates) < 3:
        for ind in anb_sorted.index:
            if ind not in top_candidates:
                top_candidates.append(ind)
            if len(top_candidates) >= 3:
                break
    top1_ind = top_candidates[0]
    top3_list = [(ind, anb_sorted[ind]) for ind in top_candidates[:3]]
    
    print(f"\n  Top1: {top1_ind} (ANB={anb_sorted[top1_ind]:.4f})")
    print(f"  Top3: {', '.join([f'{ind}({v:.4f})' for ind, v in top3_list])}")
    
    # 3.5 宽基指数多空观点
    print("\n[2.5] 计算宽基指数多空观点...")
    index_prices = fetch_index_prices(latest_date)
    index_views = compute_index_views(anb_ma, net_panel, index_prices)
    if index_views:
        for name, code, agg_anb, trend_sig, view, detail in index_views:
            print(f"  {name}({code}): ANB={agg_anb:+.4f} 趋势={trend_sig} 观点={view} | {detail}")
    else:
        print("  无指数数据, 跳过")
    
    # 4. 发飞书（--no-send 供迁移/回归验证，避免同一天重复推送卡片）
    if '--no-send' in sys.argv:
        print("\n[3] --no-send 模式：跳过飞书推送（仅验证取数和计算）")
    else:
        print("\n[3] 发送飞书报告...")
        send_ok = send_feishu(top1_ind, top3_list, anb_row, net_row, net_ratio_row, latest_date, index_views, mom5_row)
        if not send_ok:
            # 发送失败必须反映到退出码：runner 的 fetch 靠 rc 判断成败，
            # 调度器再靠 health 连败感知。只打日志、进程退出 0，等于把
            # 「没发出去的日报」当正常结束（issue #53）。
            print("\n[!] 飞书发送失败，进程以非零退出（供调度器 / runner 感知）")
            raise SystemExit(2)
    
    # 5. 保存JSON
    result = {
        'date': latest_date.strftime('%Y-%m-%d'),
        'top1': {'industry': top1_ind, 'etf_code': INDUSTRY_ETF_MAP[top1_ind][0], 'anb': float(anb_sorted.iloc[0])},
        'top3': [{'industry': ind, 'etf_code': INDUSTRY_ETF_MAP[ind][0], 'anb': float(v)} for ind, v in top3_list],
        'all_industries': {ind: {'net': float(net_row.get(ind, 0)), 'anb': float(anb_row.get(ind, 0))} for ind in INDUSTRY_ETF_MAP},
        'index_views': [
            {'name': name, 'code': code, 'agg_anb': float(agg_anb), 'trend': trend_sig, 'view': view, 'detail': detail}
            for name, code, agg_anb, trend_sig, view, detail in index_views
        ] if index_views else [],
    }
    json_path = os.path.join(OUTPUT_DIR, f'weekly_report_{latest_date.strftime("%Y%m%d")}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n  保存: {json_path}")
    print("\n完成!")


if __name__ == '__main__':
    main()
