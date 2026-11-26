#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 v8.1 - data_hub四级降级版
============================================
筛选条件：
【基础条件】（所有档位必须满足）
1. 股价 > MA20 > MA40 > MA80 > MA160（完整多头排列）
2. MA80 和 MA160 必须向上（斜率>0）

【三档递减】
档位1（最严）：近60交易日未破MA40 + 近20交易日未破MA20
档位2（中等）：近60交易日未破MA40（去掉"未破MA20"）
档位3（最松）：近60交易日内允许跌破MA40 ≤ 2次

【降级规则】
从档位1开始，不足10只则降一档，直到凑够10只为止
最多选20只

排序：按当月涨幅降序

数据源：data_hub 四级降级框架
  Tier 1: AKShare (东财)
  Tier 2: Tushare (专业数据)
  Tier 3: Coze Gateway (技能CLI兜底)
  Tier 4: Web (爬取兜底)
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

import pandas as pd
import numpy as np
import os
import sys
import time
from datetime import datetime, timedelta
import warnings
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()

warnings.filterwarnings('ignore')

try:
    sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()
except:
    pass

# ============================================================
# data_hub 路径设置
# ============================================================
DATA_HUB_PATH = os.path.expanduser(_HUB_ROOT)
if DATA_HUB_PATH not in sys.path:
    sys.path.insert(0, DATA_HUB_PATH)

from data_hub.collectors.stock_daily import StockDailyCollector
from data_hub.collectors.stock_spot import StockSpotCollector
from data_hub.base import AllSourcesFailedError


def _get_stock_list_via_datahub():
    """通过 data_hub StockSpotCollector 获取全市场A股列表（四级降级）
    
    Returns:
        tuple: (stock_df, codes, stock_df_map, source_info)
            - stock_df: DataFrame with 'code', 'name'
            - codes: list of 6-digit codes
            - stock_df_map: dict {code: name}
            - source_info: str describing which source tier was used
    """
    print("[data_hub] 获取全市场A股列表 (StockSpotCollector, 四级降级)...")
    
    collector = StockSpotCollector()
    result = collector.collect()
    
    if not result.success or result.data is None or result.data.empty:
        print(f"[data_hub] ❌ StockSpotCollector 失败: {result.error}")
        return None, None, None, None
    
    stock_df = result.data.copy()
    source_info = f"stock_spot:{result.source}(tier{result.source_tier})"
    print(f"[data_hub] ✅ 获取到 {len(stock_df)} 只股票, 数据源: {source_info}")
    
    # 标准化列名
    # data_hub 返回 ts_code (如 '000001.SZ'), name
    if 'ts_code' in stock_df.columns:
        stock_df['code'] = stock_df['ts_code'].str[:6]
    elif 'symbol' in stock_df.columns:
        stock_df['code'] = stock_df['symbol'].astype(str).str[:6]
    else:
        print("[data_hub] ❌ 无法识别股票代码列")
        return None, None, None, None
    
    # 过滤ST/退市/科创板/北交所
    stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
    stock_df = stock_df[stock_df['code'].str.match(r'^\d{6}$')]
    stock_df = stock_df[~stock_df['code'].str.startswith('688')]  # 科创板
    stock_df = stock_df[~stock_df['code'].str.startswith('8')]    # 北交所
    stock_df = stock_df[~stock_df['code'].str.startswith('4')]    # 北交所
    stock_df = stock_df[~stock_df['code'].str.startswith('920')]  # 北交所(920xxx)
    
    codes = stock_df['code'].unique().tolist()
    stock_df_map = dict(zip(stock_df['code'], stock_df['name']))
    print(f"[data_hub] 排除ST/科创板/北交所后剩余 {len(codes)} 只")
    
    return stock_df, codes, stock_df_map, source_info


def _get_daily_batch_via_datahub(codes, start_date, end_date):
    """通过 data_hub StockDailyCollector 批量获取日线数据（四级降级）
    
    策略：使用 collect_batch 的"按天全市场"模式。
    - akshare 全市场: stock_zh_a_spot_em() → 与股票列表同一个API，稳定可用
    - tushare 全市场: daily(trade_date=day) → ~1s/天
    - 四级降级由 data_hub 内部自动处理
    
    Args:
        codes: list of 6-digit codes
        start_date: 'YYYY-MM-DD' 格式
        end_date: 'YYYY-MM-DD' 格式
    
    Returns:
        tuple: (daily_df, source_info)
            - daily_df: DataFrame with 'code', 'trade_date', 'close'
            - source_info: str describing which source tier was used
    """
    print(f"\n[data_hub] 批量获取日线数据 (StockDailyCollector, 四级降级)...")
    print(f"[data_hub] 股票数: {len(codes)}, 日期范围: {start_date} ~ {end_date}")
    print(f"[data_hub] 策略: collect_batch 按天全市场 (akshare全市场API + tushare全市场API)")
    
    # 6-digit code -> tushare format
    def _to_ts_code(code):
        if code.startswith(('6', '5')):
            return f"{code}.SH"
        else:
            return f"{code}.SZ"
    
    ts_code_list = [_to_ts_code(c) for c in codes]
    
    # 转换日期格式 YYYY-MM-DD -> YYYYMMDD
    start_date_compact = start_date.replace('-', '')
    end_date_compact = end_date.replace('-', '')
    
    # 全市场批量模式：tushare 最快（daily接口 ~2s/天），akshare 仅支持当日
    # 降级链仍保留四级，但 tushare 优先以减少无效等待
    collector = StockDailyCollector(
        source_priority=["tushare", "akshare", "coze_gateway", "web"]
    )
    
    try:
        result = collector.collect_batch(
            ts_code_list=ts_code_list,
            start_date=start_date_compact,
            end_date=end_date_compact,
        )
    except AllSourcesFailedError as e:
        print(f"[data_hub] ❌ 所有数据源均失败: {e}")
        return None, None
    except Exception as e:
        print(f"[data_hub] ❌ collect_batch 异常: {type(e).__name__}: {e}")
        return None, None
    
    if not result.success or result.data is None or result.data.empty:
        print(f"[data_hub] ❌ 未获取到日线数据: {result.error}")
        return None, None
    
    source_info = f"stock_daily:batch | 降级链[akshare→tushare→coze_gateway→web] | 实际:{result.source}(tier{result.source_tier})"
    daily_df = result.data.copy()
    
    # 打印元数据，确认降级情况
    meta = result.meta
    if meta:
        batch_strategy = meta.get('batch_strategy', 'unknown')
        success_days = meta.get('success_days', 0)
        failed_days = meta.get('failed_days', 0)
        matched_stocks = meta.get('matched_stocks', 0)
        print(f"[data_hub] ✅ 批量策略: {batch_strategy}")
        print(f"[data_hub] ✅ 成功天数: {success_days}, 失败天数: {failed_days}")
        print(f"[data_hub] ✅ 覆盖股票: {matched_stocks}/{len(codes)}")
        print(f"[data_hub] ✅ 数据源: {source_info}")
    
    # 标准化列名: ts_code -> code (6-digit), trade_date -> datetime, close -> float
    if 'ts_code' in daily_df.columns:
        daily_df['code'] = daily_df['ts_code'].str[:6]
    else:
        print("[data_hub] ❌ 日线数据缺少 ts_code 列")
        return None, None
    
    daily_df['trade_date'] = pd.to_datetime(daily_df['trade_date'], errors='coerce')
    daily_df['close'] = daily_df['close'].astype(float)
    daily_df = daily_df[['code', 'trade_date', 'close']].copy()
    
    # 去除 NaT
    daily_df = daily_df.dropna(subset=['trade_date'])
    
    print(f"[data_hub] ✅ 共获取 {len(daily_df)} 条日线记录")
    
    return daily_df, source_info


def _screen_core(daily_df, codes, stock_df_map, today_str, month_start_str):
    """三档降级筛选核心逻辑（与数据源无关，data_hub 统一调用）
    
    Args:
        daily_df: 统一列名的日线 DataFrame，含 code / trade_date / close
        codes: 股票代码列表
        stock_df_map: dict {code: name}
        today_str: 今天日期字符串 'YYYY-MM-DD'
        month_start_str: 当月起始 'YYYY-MM-01'
    
    Returns:
        (tier1_results, tier2_results, tier3_results, scan_count)
    """
    tier1_results = []
    tier2_results = []
    tier3_results = []
    scan_count = 0

    for idx, code in enumerate(codes):
        stock_daily = daily_df[daily_df['code'] == code].copy()
        if len(stock_daily) < 200:
            continue

        stock_daily = stock_daily.sort_values('trade_date').reset_index(drop=True)

        stock_daily['ma20'] = stock_daily['close'].rolling(window=20, min_periods=20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(window=40, min_periods=40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(window=80, min_periods=80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(window=160, min_periods=160).mean()

        latest = stock_daily.iloc[-1]
        prev = stock_daily.iloc[-2]

        # 基础条件1：多头排列
        if not (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and
                pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            continue
        if not (latest['close'] > latest['ma20'] > latest['ma40'] >
                latest['ma80'] > latest['ma160']):
            continue

        # 基础条件2：MA80和MA160向上
        if not (pd.notna(prev['ma80']) and pd.notna(prev['ma160'])):
            continue
        if latest['ma80'] <= prev['ma80']:
            continue
        if latest['ma160'] <= prev['ma160']:
            continue

        scan_count += 1

        # === 档位判定 ===
        recent_60 = stock_daily.tail(60)
        ma40_broken_60 = int((recent_60['close'] < recent_60['ma40']).sum())
        ma40_clean_60 = (ma40_broken_60 == 0)

        recent_20 = stock_daily.tail(20)
        ma20_broken_20 = int((recent_20['close'] < recent_20['ma20']).sum())
        ma20_clean_20 = (ma20_broken_20 == 0)

        ma40_le2_breaks = (ma40_broken_60 <= 2)

        if ma40_clean_60 and ma20_clean_20:
            passed_tier = 1
        elif ma40_clean_60:
            passed_tier = 2
        elif ma40_le2_breaks:
            passed_tier = 3
        else:
            continue

        # 计算当月涨幅
        month_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(month_start_str)]
        if len(month_df) >= 2:
            month_return = (latest['close'] / month_df.iloc[0]['close'] - 1) * 100
        else:
            month_return = None

        # 计算年初至今涨幅（YTD）
        year_start_date = pd.Timestamp(today_str[:4] + '-01-02')
        year_df = stock_daily[stock_daily['trade_date'] >= year_start_date]
        if len(year_df) >= 2:
            ytd_return = (latest['close'] / year_df.iloc[0]['close'] - 1) * 100
        else:
            ytd_return = None

        exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'
        name = stock_df_map.get(code, code)

        stock_info = {
            '股票代码': code,
            '交易所': exchange,
            '股票简称': name,
            '当前价': round(float(latest['close']), 2),
            'MA20': round(float(latest['ma20']), 2),
            'MA40': round(float(latest['ma40']), 2),
            'MA80': round(float(latest['ma80']), 2),
            'MA160': round(float(latest['ma160']), 2),
            '当月涨幅': round(month_return, 2) if month_return is not None else None,
            '年初至今涨幅': round(ytd_return, 2) if ytd_return is not None else None,
            '通过档位': passed_tier,
            '60日破MA40次数': ma40_broken_60,
            '20日破MA20次数': ma20_broken_20,
        }

        if passed_tier == 1:
            tier1_results.append(stock_info.copy())
        if passed_tier <= 2:
            tier2_results.append(stock_info.copy())
        if passed_tier <= 3:
            tier3_results.append(stock_info.copy())

        if scan_count % 50 == 0:
            print(f"  扫描{idx+1}/{len(codes)}, 基础通过{scan_count}只, "
                  f"档位1:{len(tier1_results)} 档位2:{len(tier2_results)} 档位3:{len(tier3_results)}")

    return tier1_results, tier2_results, tier3_results, scan_count


def _generate_report(tier1_results, tier2_results, tier3_results, today_str, data_source):
    """生成 Markdown 报告（公共逻辑）"""
    # 降级逻辑
    if len(tier1_results) >= 10:
        selected = tier1_results
        active_tier = 1
        removed_conditions = []
        print(f"\n✅ 档位1结果 {len(tier1_results)} 只 >= 10，使用档位1")
    elif len(tier2_results) >= 10:
        selected = tier2_results
        active_tier = 2
        removed_conditions = ["近20交易日未破MA20"]
        print(f"\n⚠️ 档位1仅 {len(tier1_results)} 只 < 10，降级到档位2（去掉'近20日未破MA20'）")
    else:
        selected = tier3_results
        active_tier = 3
        removed_conditions = ["近20交易日未破MA20", "近3个月未破MA40（改为允许跌破≤2次）"]
        print(f"\n⚠️⚠️ 档位1仅{len(tier1_results)}只，档位2仅{len(tier2_results)}只 < 10，降级到档位3（允许近3月破MA40≤2次）")

    if selected:
        results_df = pd.DataFrame(selected)
        results_df = results_df.sort_values('当月涨幅', ascending=False)
    else:
        results_df = pd.DataFrame()

    top_n = results_df.head(20) if len(results_df) > 0 else results_df

    if len(results_df) > 0:
        print(f"\n📊 选出 {len(results_df)} 只，展示前{min(20, len(results_df))}只：")
        for _, row in top_n.iterrows():
            mtd = f"{row['当月涨幅']:.2f}%" if pd.notna(row['当月涨幅']) else "N/A"
            tier_mark = f"[档{int(row['通过档位'])}]"
            print(f"  {tier_mark} {row['交易所']}.{row['股票代码']} {row['股票简称']} 价:{row['当前价']} 月涨:{mtd}")

    # 生成报告
    tier_labels = {
        1: "档位1（最严）",
        2: "档位2（中等）",
        3: "档位3（最松）"
    }

    condition_text = f"**筛选条件**（当前激活: {tier_labels[active_tier]}）\n\n"
    condition_text += "**基础条件**（所有档位必须满足）：\n"
    condition_text += "1. 股价 > MA20 > MA40 > MA80 > MA160（完整多头排列）\n"
    condition_text += "2. MA80 和 MA160 必须向上（斜率>0）\n\n"
    condition_text += "**三档递减条件**：\n"

    tier1_mark = "✅ 已激活" if active_tier == 1 else ("✅ 满足" if active_tier == 2 else "❌ 未满足")
    tier2_mark = "✅ 已激活" if active_tier == 2 else ("✅ 满足" if active_tier == 1 else "❌ 未满足")
    tier3_mark = "✅ 已激活" if active_tier == 3 else "— 未使用"

    condition_text += f"- 档位1：近3个月(60交易日)未破MA40 + 近1月(20交易日)未破MA20 → {tier1_mark}\n"
    condition_text += f"- 档位2：近3个月(60交易日)未破MA40 → {tier2_mark}\n"
    condition_text += f"- 档位3：近3个月内允许跌破MA40 ≤ 2次 → {tier3_mark}\n"

    if removed_conditions:
        condition_text += f"\n⚠️ **降级说明**: 因上一档结果不足10只，已去掉以下条件：\n"
        for rc in removed_conditions:
            condition_text += f"  - ~~{rc}~~\n"

    md_content = f"# {today_str} 全市场A股多头排列筛选\n\n"
    md_content += f"**筛选基准日**: {today_str}\n\n"
    md_content += condition_text + "\n\n"
    md_content += "**排序方式**: 按当月涨幅降序 | **最多20只**\n\n"
    md_content += f"**档位1**: {len(tier1_results)} 只 | **档位2**: {len(tier2_results)} 只 | **档位3**: {len(tier3_results)} 只\n\n"
    md_content += f"**最终选用**: {tier_labels[active_tier]}，共 {len(results_df)} 只\n\n"

    if len(results_df) > 0:
        md_content += "| 档位 | 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 60日破MA40 | 20日破MA20 | 当月涨幅(%) | 年初至今(%) |\n"
        md_content += "|------|----------|----------|--------|------|------|-------|-------|-----------|-----------|-------------|-------------|\n"
        for _, row in top_n.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            mtd_display = f"{row['当月涨幅']:.2f}" if pd.notna(row['当月涨幅']) else "N/A"
            ytd_display = f"{row['年初至今涨幅']:.2f}" if pd.notna(row['年初至今涨幅']) else "N/A"
            name = row['股票简称']
            if pd.notna(row.get('年初至今涨幅')) and row['年初至今涨幅'] < -50:
                name = '⚠️ ' + name
            md_content += f"| {int(row['通过档位'])} | {code_full} | {name} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {int(row['60日破MA40次数'])} | {int(row['20日破MA20次数'])} | {mtd_display} | {ytd_display} |\n"
    else:
        md_content += "⚠️ **今日无股票满足全部条件**\n"

    md_content += f"\n*数据源: {data_source}*\n*案例分析学习材料，不构成投资建议*\n"

    # 优先用环境变量（wrapper传入的绝对路径），否则从脚本位置推算
    # __file__ = .../缠论主升浪/src/screening_akshare_v8.py → dirname×1 = 缠论主升浪
    default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'outputs', '股票筛选')
    output_dir = os.environ.get('CHANLUN_OUTPUT_DIR', default_dir)
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{today_str}_多头排列Top20.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"\n📁 已保存: {output_file}")
    return output_file, results_df, active_tier


def _run_datahub(today_str):
    """data_hub 四级降级路径主流程
    
    通过 data_hub 的 StockSpotCollector 和 StockDailyCollector 获取数据，
    自动走四级降级: akshare → tushare → coze_gateway → web
    """
    print("[数据源] data_hub 四级降级框架")
    print("  降级链: akshare → tushare → coze_gateway → web")
    
    # 1. 获取股票列表 (StockSpotCollector, 四级降级)
    stock_df, codes, stock_df_map, spot_source_info = _get_stock_list_via_datahub()
    if codes is None or len(codes) == 0:
        print("❌ data_hub 获取股票列表失败，退出")
        return None
    
    if not codes:
        print("没有符合条件的股票")
        return None
    
    # 2. 获取日线数据 (StockDailyCollector.collect_batch, 四级降级)
    # 回溯约370天(250+交易日)为生产配置；验证时可改为300天(210交易日)以控制在15min内
    # ⚠️ 300天数据不足MA160+60窗口(需220交易日)，筛选结果为0属正常
    end_date = today_str
    start_dt = datetime.strptime(today_str, '%Y-%m-%d') - timedelta(days=370)
    start_date = start_dt.strftime('%Y-%m-%d')
    
    daily_df, daily_source_info = _get_daily_batch_via_datahub(codes, start_date, end_date)
    
    if daily_df is None or daily_df.empty:
        print("❌ data_hub 未获取到日线数据，退出")
        return None
    
    # 当月第一个交易日
    month_start = today_str[:8] + '01'
    
    # 3. 三档筛选
    print("\n【三档降级筛选】计算均线...")
    tier1, tier2, tier3, scan_count = _screen_core(
        daily_df, codes, stock_df_map, today_str, month_start
    )
    
    print(f"\n基础条件通过: {scan_count} 只")
    print(f"档位1（60日未破MA40+20日未破MA20）: {len(tier1)} 只")
    print(f"档位2（60日未破MA40）: {len(tier2)} 只")
    print(f"档位3（60日破MA40≤2次）: {len(tier3)} 只")
    
    # 4. 生成报告
    data_source_desc = f"data_hub 四级降级 | {spot_source_info} + {daily_source_info}"
    output_file, results_df, active_tier = _generate_report(
        tier1, tier2, tier3, today_str, data_source=data_source_desc
    )
    
    return output_file


def main():
    # 解析 --no-push 参数
    no_push = '--no-push' in sys.argv
    
    print("=" * 60)
    print("A股多头排列筛选器 v8.1 (data_hub 四级降级)")
    print("=" * 60)
    
    TODAY = datetime.now().strftime('%Y-%m-%d')
    print(f"筛选基准日: {TODAY}")
    print(f"数据源: data_hub 四级降级框架 (akshare → tushare → coze_gateway → web)")
    if no_push:
        print(f"模式: --no-push (不推送飞书)")
    print()
    
    start_time = time.time()
    
    try:
        result = _run_datahub(TODAY)
        elapsed = time.time() - start_time
        
        if result is None:
            print(f"\n❌ 筛选失败，耗时 {elapsed:.1f}s")
            sys.exit(1)
        else:
            print(f"\n✅ 筛选完成，耗时 {elapsed:.1f}s")
            
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ 运行异常 ({type(e).__name__}: {e})，耗时 {elapsed:.1f}s")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
