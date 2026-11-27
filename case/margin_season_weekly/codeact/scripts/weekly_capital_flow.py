#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国金周度资金流监控 - 完整版
严格按国金策略《资金跟踪系列》算法框架，纯Python + AKShare实现

Usage (positional args):
    python3 weekly_capital_flow.py <result_mode> [end_date] [push_feishu] [output_dir]

Params order: result_mode end_date push_feishu output_dir
    result_mode: display_only | no_reply
    end_date: YYYY-MM-DD or "last_friday" (default)
    push_feishu: true | false (default)
    output_dir: path (default: ../reports)

数据源: AKShare (沪深交易所/东方财富/中证指数)
输出: Markdown格式报告 + 可选飞书推送
"""

import datetime
import json
import os
import sys
import warnings
import traceback
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

# 飞书群目标：统一配置口径（case/_lib/notify_init.py → DataHub 通知模块 / 本应用 secrets），
# 仓库里不写死。本文件在 case/margin_season_weekly/codeact/scripts/，往上四级即 case/_lib。
_LIB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))), "_lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from notify_init import chat_id as _chat_id, chat_hint as _chat_hint  # noqa: E402

warnings.filterwarnings('ignore')

# ============================================================
# 参数解析 (位置参数协议)
# ============================================================
def parse_args():
    """位置参数: result_mode end_date push_feishu output_dir"""
    args = sys.argv[1:]
    result_mode = args[0] if len(args) > 0 else "display_only"
    end_date = args[1] if len(args) > 1 else "last_friday"
    push_feishu = args[2] if len(args) > 2 else "false"
    output_dir = args[3] if len(args) > 3 else None
    return result_mode, end_date, push_feishu.lower() == "true", output_dir


# ============================================================
# 全局配置
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # /Coze/Drive/两融季节同比
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_DIR, "reports")
FEISHU_PUSH_SCRIPT = os.path.join(SCRIPT_DIR, "feishu_push.py")

TECH_INDUSTRIES = ["电子", "通信", "计算机", "国防军工"]

SW_L1_INDUSTRIES = [
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器",
    "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输",
    "房地产", "商贸零售", "社会服务", "银行", "非银金融", "建筑材料",
    "建筑装饰", "电力设备", "国防军工", "计算机", "传媒", "通信", "煤炭",
    "石油石化", "环保", "美容护理", "机械设备", "综合"
]

# 申万一级行业指数代码映射
SW_L1_INDEX_CODES = {
    "801010": "农林牧渔", "801020": "基础化工", "801030": "钢铁",
    "801040": "有色金属", "801050": "电子", "801880": "汽车",
    "801110": "家用电器", "801120": "食品饮料", "801140": "纺织服饰",
    "801150": "轻工制造", "801160": "医药生物", "801170": "公用事业",
    "801180": "交通运输", "801200": "房地产", "801990": "商贸零售",
    "801980": "社会服务", "801780": "银行", "801790": "非银金融",
    "801710": "建筑材料", "801720": "建筑装饰", "801730": "电力设备",
    "801740": "国防军工", "801750": "计算机", "801760": "传媒",
    "801770": "通信", "801950": "煤炭", "801960": "石油石化",
    "801970": "环保", "801890": "美容护理", "801230": "综合",
    # 机械设备用801890可能冲突，这里用实际代码
}
# 补充机械设备(801890被美容护理占用，实际机械设备是801890或独立代码)
# 申万2021版: 机械设备=801890(旧),新版需确认
SW_L1_INDEX_CODES_REV = {v: k for k, v in SW_L1_INDEX_CODES.items()}

# 宽基ETF → 跟踪指数代码(中证)
BROAD_INDEX_MAP = {
    "沪深300": "000300",
    "中证500": "000905",
    "科创50": "000688",
    "创业板指": "399006",
    "上证50": "000016",
    "中证1000": "000852",
}

BROAD_ETFS = {
    "510050": {"name": "上证50ETF华夏", "index": "上证50", "type": "宽基"},
    "510300": {"name": "沪深300ETF华泰柏瑞", "index": "沪深300", "type": "宽基"},
    "510330": {"name": "沪深300ETF华夏", "index": "沪深300", "type": "宽基"},
    "510500": {"name": "中证500ETF南方", "index": "中证500", "type": "宽基"},
    "588000": {"name": "科创50ETF华夏", "index": "科创50", "type": "宽基"},
    "588080": {"name": "科创50ETF易方达", "index": "科创50", "type": "宽基"},
    "159915": {"name": "创业板ETF易方达", "index": "创业板指", "type": "宽基"},
    "512100": {"name": "中证1000ETF南方", "index": "中证1000", "type": "宽基"},
    "159845": {"name": "中证1000ETF华夏", "index": "中证1000", "type": "宽基"},
    "560610": {"name": "中证2000ETF", "index": "中证2000", "type": "宽基"},
}

SECTOR_ETFS = {
    "512480": {"name": "半导体ETF", "industry": "电子", "type": "行业"},
    "512760": {"name": "半导体ETF国泰", "industry": "电子", "type": "行业"},
    "159995": {"name": "芯片ETF", "industry": "电子", "type": "行业"},
    "515050": {"name": "5GETF", "industry": "通信", "type": "行业"},
    "515880": {"name": "通信ETF", "industry": "通信", "type": "行业"},
    "512980": {"name": "传媒ETF", "industry": "传媒", "type": "行业"},
    "515030": {"name": "新能源车ETF", "industry": "汽车", "type": "行业"},
    "516160": {"name": "新能源ETF", "industry": "电力设备", "type": "行业"},
    "512660": {"name": "军工ETF", "industry": "国防军工", "type": "行业"},
    "512800": {"name": "银行ETF", "industry": "银行", "type": "行业"},
    "512880": {"name": "证券ETF", "industry": "非银金融", "type": "行业"},
    "512010": {"name": "医药ETF", "industry": "医药生物", "type": "行业"},
    "159928": {"name": "消费ETF", "industry": "食品饮料", "type": "行业"},
    "512200": {"name": "房地产ETF", "industry": "房地产", "type": "行业"},
    "512400": {"name": "有色金属ETF", "industry": "有色金属", "type": "行业"},
    "515220": {"name": "煤炭ETF", "industry": "煤炭", "type": "行业"},
    "515790": {"name": "光伏ETF", "industry": "电力设备", "type": "行业"},
    "516950": {"name": "基建ETF", "industry": "建筑装饰", "type": "行业"},
    "159869": {"name": "游戏ETF", "industry": "传媒", "type": "行业"},
    "562800": {"name": "中证传媒ETF", "industry": "传媒", "type": "行业"},
}

# 季节性节点定义 (月-日 范围 → 标签)
SEASONAL_NODES = [
    # (month_start, day_start, month_end, day_end, label_template)
    (1, 1, 1, 31, "1月{}日"),
    (2, 1, 2, 15, "春节前{}"),
    (2, 15, 2, 28, "春节后{}"),
    (3, 1, 3, 31, "3月{}"),
    (4, 1, 4, 10, "4月上旬"),
    (4, 10, 4, 30, "4月中下旬"),
    (5, 1, 5, 10, "五一节前"),
    (5, 10, 5, 31, "5月中下旬"),
    (6, 1, 6, 30, "6月{}"),
    (7, 1, 7, 15, "7月上半月"),
    (7, 15, 7, 31, "7月下半月"),
    (8, 1, 8, 31, "8月{}"),
    (9, 1, 9, 25, "9月{}"),
    (9, 25, 9, 30, "国庆前最后几天"),
    (10, 1, 10, 7, "国庆假期"),
    (10, 8, 10, 20, "国庆后第1周"),
    (10, 20, 10, 31, "10月下旬"),
    (11, 1, 11, 30, "11月{}"),
    (12, 1, 12, 15, "12月上半月"),
    (12, 15, 12, 31, "年末"),
]

print("=" * 60)
print("国金周度资金流监控 - 完整版")
print("=" * 60)


def safe_call(func, *args, retries=2, default=None, timeout_sec=None, **kwargs):
    """安全调用AKShare接口"""
    import signal
    
    class TimeoutError(Exception):
        pass
    
    def handler(signum, frame):
        raise TimeoutError("timeout")
    
    for i in range(retries + 1):
        try:
            if timeout_sec:
                old = signal.signal(signal.SIGALRM, handler)
                signal.alarm(timeout_sec)
            try:
                result = func(*args, **kwargs)
            finally:
                if timeout_sec:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old)
            return result
        except Exception as e:
            if i < retries:
                print(f"  ⚠️ {getattr(func, '__name__', str(func))} 第{i+1}次失败: {e}, 重试...")
                time.sleep(1)
            else:
                print(f"  ❌ {getattr(func, '__name__', str(func))} 最终失败: {type(e).__name__}")
                return default


def fmt_yi(val):
    """格式化亿元"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    return f"{val:.2f}"


def zscore_label(z):
    """Z-score分级标签"""
    if z > 1.0:
        return "显著强"
    elif z > 0.3:
        return "略强"
    elif z >= -0.3:
        return "正常"
    elif z >= -1.0:
        return "略弱"
    else:
        return "显著弱"


def compute_zscore(values, current):
    """计算Z-score"""
    if len(values) < 2:
        return 0.0, 0.0, 0.0
    mean = np.mean(values)
    std = np.std(values, ddof=1) if len(values) > 1 else 1.0
    if std == 0:
        std = 1.0
    z = (current - mean) / std
    pctile = (np.array(values) < current).sum() / len(values) * 100
    return z, mean, pctile


# ============================================================
# 基础数据构建：股票→申万一级行业映射
# ============================================================
def build_stock_to_sw_mapping():
    """从申万行业指数成分股构建 stock_code → SW_L1_industry 映射"""
    print("\n🔧 构建股票→申万一级行业映射...")
    import akshare as ak
    
    mapping = {}
    success_count = 0
    
    for code, name in SW_L1_INDEX_CODES.items():
        try:
            df = safe_call(ak.index_component_sw, symbol=code, retries=0)
            if df is not None and len(df) > 0:
                # 兼容不同列名
                code_col = '证券代码' if '证券代码' in df.columns else df.columns[1]
                for _, row in df.iterrows():
                    stock_code = str(row[code_col]).zfill(6)
                    mapping[stock_code] = name
                success_count += 1
                print(f"  ✅ {name}({code}): {len(df)}只")
            else:
                print(f"  ⚠️ {name}({code}): 无数据")
        except Exception as e:
            print(f"  ❌ {name}({code}): {e}")
    
    print(f"  ✅ 映射完成: {len(mapping)}只股票, {success_count}/{len(SW_L1_INDEX_CODES)}个行业")
    return mapping


# ============================================================
# 基础数据构建：宽基指数→成分股行业权重
# ============================================================
def build_index_industry_weights(stock_to_sw):
    """计算各宽基指数的申万一级行业权重分布"""
    print("\n🔧 构建宽基指数行业权重...")
    import akshare as ak
    
    index_weights = {}  # {index_name: {sw_industry: weight}}
    
    for idx_name, idx_code in BROAD_INDEX_MAP.items():
        try:
            print(f"  获取 {idx_name}({idx_code}) 成分股权重...", flush=True)
            df = safe_call(ak.index_stock_cons_weight_csindex, symbol=idx_code, retries=0, timeout_sec=60)
            if df is None or len(df) == 0:
                print(f"  ⚠️ {idx_name}({idx_code}): 无权重数据")
                continue
            
            # 映射每只成分股到SW行业
            industry_weight = {}
            mapped_weight = 0
            unmapped_weight = 0
            
            for _, row in df.iterrows():
                stock_code = str(row['成分券代码']).zfill(6)
                weight = float(row['权重'])
                sw_ind = stock_to_sw.get(stock_code)
                
                if sw_ind:
                    industry_weight[sw_ind] = industry_weight.get(sw_ind, 0) + weight
                    mapped_weight += weight
                else:
                    unmapped_weight += weight
            
            # 归一化（只保留已映射部分）
            total_mapped = sum(industry_weight.values())
            if total_mapped > 0:
                for ind in industry_weight:
                    industry_weight[ind] /= total_mapped
            
            index_weights[idx_name] = industry_weight
            print(f"  ✅ {idx_name}: {len(industry_weight)}个行业, "
                  f"映射率{mapped_weight/(mapped_weight+unmapped_weight)*100:.1f}%")
        except Exception as e:
            print(f"  ❌ {idx_name}: {e}")
    
    return index_weights


# ============================================================
# 模块1: ETF资金流向 + 行业分摊
# ============================================================
class ETFAnalyzer:
    def __init__(self, week_start, week_end, index_industry_weights):
        self.week_start = week_start
        self.week_end = week_end
        self.index_industry_weights = index_industry_weights
        self.flow_data = {}
        self.industry_flow_absolute = {}  # 绝对净流入(亿元)
        self.industry_flow_relative = {}  # 相对流入强度

    def collect(self):
        print("\n📊 [模块1] ETF资金流向 + 行业分摊...")
        import akshare as ak

        # 获取ETF快照（含最新份额和价格）
        print("  采集ETF快照...")
        spot = safe_call(ak.fund_etf_spot_em)
        if spot is None or len(spot) == 0:
            print("  ❌ ETF快照失败")
            return

        # 获取SSE ETF历史份额（周初 vs 周末）
        ws_str = self.week_start.strftime("%Y%m%d")
        we_str = self.week_end.strftime("%Y%m%d")
        
        # 尝试获取周初和周末的SSE份额
        start_shares = {}
        end_shares = {}
        
        # 周初SSE份额
        for offset in range(0, 5):
            d = (self.week_start + datetime.timedelta(days=offset)).strftime("%Y%m%d")
            scale = safe_call(ak.fund_etf_scale_sse, date=d)
            if scale is not None and len(scale) > 0:
                for _, row in scale.iterrows():
                    start_shares[row['基金代码']] = float(row['基金份额'])
                print(f"  ✅ 周初SSE份额({d}): {len(start_shares)}只")
                break
        
        # 周末SSE份额
        for offset in range(0, 5):
            d = (self.week_end - datetime.timedelta(days=offset)).strftime("%Y%m%d")
            scale = safe_call(ak.fund_etf_scale_sse, date=d)
            if scale is not None and len(scale) > 0:
                for _, row in scale.iterrows():
                    end_shares[row['基金代码']] = float(row['基金份额'])
                print(f"  ✅ 周末SSE份额({d}): {len(end_shares)}只")
                break

        # 计算每只ETF的净申购额
        all_etfs = {**BROAD_ETFS, **SECTOR_ETFS}
        for code, info in all_etfs.items():
            try:
                cur = spot[spot['代码'] == code]
                if len(cur) == 0:
                    continue
                
                cur_price = float(cur.iloc[0].get('最新价', 0))
                cur_shares = float(cur.iloc[0].get('最新份额', 0))
                if pd.isna(cur_price) or pd.isna(cur_shares) or cur_price <= 0:
                    continue

                # 份额变化
                shares_end = end_shares.get(code, cur_shares)
                shares_start = start_shares.get(code, shares_end)
                shares_change = shares_end - shares_start

                # 用周度均价估算金额
                avg_price = cur_price
                try:
                    ws_fmt = self.week_start.strftime("%Y%m%d")
                    we_fmt = self.week_end.strftime("%Y%m%d")
                    hist = safe_call(ak.fund_etf_hist_em, symbol=code, period="daily",
                                     start_date=ws_fmt, end_date=we_fmt, adjust="")
                    if hist is not None and len(hist) > 0:
                        avg_price = float(hist['收盘'].mean())
                except:
                    pass

                value_change_yi = shares_change * avg_price / 1e8

                self.flow_data[code] = {
                    "name": info["name"],
                    "index": info.get("index", ""),
                    "industry": info.get("industry", ""),
                    "type": info["type"],
                    "shares_change": shares_change,
                    "avg_price": avg_price,
                    "value_change_yi": round(value_change_yi, 4),
                    "current_aum_yi": round(cur_shares * cur_price / 1e8, 2),
                }
            except Exception:
                continue

        # 计算行业分摊
        self._compute_industry_allocation()
        
        total = sum(v['value_change_yi'] for v in self.flow_data.values())
        print(f"  ✅ 跟踪 {len(self.flow_data)} 只ETF, 估算净申购: {total:.1f}亿元")

    def _compute_industry_allocation(self):
        """将ETF净申购额按行业分摊"""
        # 1. 行业ETF/主题ETF：直接归类
        for code, data in self.flow_data.items():
            if data['type'] == '行业' and data.get('industry'):
                ind = data['industry']
                self.industry_flow_absolute[ind] = self.industry_flow_absolute.get(ind, 0) + data['value_change_yi']

        # 2. 宽基ETF：按成分股行业权重分摊
        for code, data in self.flow_data.items():
            if data['type'] != '宽基':
                continue
            idx_name = data.get('index', '')
            if idx_name not in self.index_industry_weights:
                continue
            
            weights = self.index_industry_weights[idx_name]
            net_sub = data['value_change_yi']
            
            for sw_ind, weight in weights.items():
                allocated = net_sub * weight
                self.industry_flow_absolute[sw_ind] = self.industry_flow_absolute.get(sw_ind, 0) + allocated

    def get_summary(self) -> dict:
        if not self.flow_data:
            return {"status": "数据不可用"}

        broad_total = sum(v['value_change_yi'] for v in self.flow_data.values() if v['type'] == '宽基')
        sector_total = sum(v['value_change_yi'] for v in self.flow_data.values() if v['type'] == '行业')

        broad_details = sorted(
            [(v['name'], v['value_change_yi'], v['current_aum_yi']) 
             for v in self.flow_data.values() if v['type'] == '宽基'],
            key=lambda x: x[1], reverse=True
        )
        sector_details = sorted(
            [(v['name'], v['industry'], v['value_change_yi']) 
             for v in self.flow_data.values() if v['type'] == '行业'],
            key=lambda x: x[2], reverse=True
        )

        # 行业分摊结果排序
        sorted_ind = sorted(self.industry_flow_absolute.items(), key=lambda x: x[1], reverse=True)

        return {
            "status": "ok",
            "total_net_subscription": round(broad_total + sector_total, 2),
            "broad_net_subscription": round(broad_total, 2),
            "sector_net_subscription": round(sector_total, 2),
            "tracked_etf_count": len(self.flow_data),
            "broad_details": broad_details,
            "sector_details": sector_details,
            "industry_flow": dict(sorted_ind),
            "industry_flow_absolute": self.industry_flow_absolute.copy(),
        }


# ============================================================
# 模块2: 两融 + 季节性同比
# ============================================================
class MarginAnalyzer:
    def __init__(self, week_start, week_end):
        self.week_start = week_start
        self.week_end = week_end
        self.total_data = None

    def collect(self):
        print("\n📊 [模块2] 两融数据 + 季节性分析...")
        import akshare as ak

        print("  采集沪市两融...")
        sse = safe_call(ak.macro_china_market_margin_sh)
        if sse is not None and len(sse) > 0:
            sse['日期'] = pd.to_datetime(sse['日期'])
            print(f"  ✅ 沪市: {len(sse)}条")
        else:
            print("  ❌ 沪市失败")
            return

        print("  采集深市两融...")
        szse = safe_call(ak.macro_china_market_margin_sz)
        if szse is not None and len(szse) > 0:
            szse['日期'] = pd.to_datetime(szse['日期'])
            print(f"  ✅ 深市: {len(szse)}条")
        else:
            print("  ❌ 深市失败")
            return

        # 合并
        cols = ['日期', '融资余额', '融资买入额', '融券余量', '融券余额', '融资融券余额']
        try:
            merged = pd.merge(
                sse[cols], szse[cols], on='日期', suffixes=('_沪', '_深'), how='inner'
            )
            for col in ['融资余额', '融资买入额', '融券余额', '融资融券余额']:
                merged[f'{col}_合计'] = merged[f'{col}_沪'] + merged[f'{col}_深']
            
            self.total_data = merged.sort_values('日期').reset_index(drop=True)
            print(f"  ✅ 合并: {len(self.total_data)}个交易日")
        except Exception as e:
            print(f"  ❌ 合并失败: {e}")

    def get_weekly_summary(self) -> dict:
        if self.total_data is None or len(self.total_data) == 0:
            return {"status": "数据不可用"}

        ws = pd.Timestamp(self.week_start)
        we = pd.Timestamp(self.week_end)
        week = self.total_data[(self.total_data['日期'] >= ws) & (self.total_data['日期'] <= we)]

        if len(week) == 0:
            week = self.total_data[self.total_data['日期'] <= we].tail(5)
        if len(week) == 0:
            return {"status": "本周无数据"}

        first, last = week.iloc[0], week.iloc[-1]
        prev = self.total_data[self.total_data['日期'] < ws].tail(5)

        result = {
            "status": "ok",
            "trading_days": len(week),
            "week_start_date": first['日期'].strftime('%Y-%m-%d'),
            "week_end_date": last['日期'].strftime('%Y-%m-%d'),
            "融资余额_周末": last.get('融资余额_合计', 0) / 1e8,
            "融资余额_周初": first.get('融资余额_合计', 0) / 1e8,
            "融资余额_变化": (last.get('融资余额_合计', 0) - first.get('融资余额_合计', 0)) / 1e8,
            "两融余额_周末": last.get('融资融券余额_合计', 0) / 1e8,
            "两融余额_变化": (last.get('融资融券余额_合计', 0) - first.get('融资融券余额_合计', 0)) / 1e8,
            "融券余额_周末": last.get('融券余额_合计', 0) / 1e8,
            "沪市融资余额": last.get('融资余额_沪', 0) / 1e8,
            "深市融资余额": last.get('融资余额_深', 0) / 1e8,
        }

        if len(prev) >= 2:
            prev_change = (prev.iloc[-1].get('融资余额_合计', 0) - prev.iloc[0].get('融资余额_合计', 0)) / 1e8
            result["融资余额_上周变化"] = prev_change
            result["融资余额_变化_环比"] = result["融资余额_变化"] - prev_change

        # 两融活跃度 = 融资买入额 / 融资余额 (proxy for sentiment)
        if '融资买入额_合计' in week.columns:
            avg_buy = week['融资买入额_合计'].mean() / 1e8
            avg_bal = week['融资余额_合计'].mean() / 1e8
            # 活跃度 = 融资买入额 / 融资余额 × 100%
            ratio = avg_buy / avg_bal * 100 if avg_bal > 0 else 0
            # 全历史活跃度
            all_ratio = self.total_data['融资买入额_合计'] / self.total_data['融资余额_合计'].replace(0, np.nan) * 100
            all_ratio = all_ratio.replace([np.inf, -np.inf], np.nan).dropna()
            pct = (all_ratio < ratio).sum() / len(all_ratio) * 100 if len(all_ratio) > 0 else 50
            result['activity'] = {
                "turnover_ratio": round(ratio, 2),
                "historical_percentile": round(pct, 1),
                "avg_daily_margin_buy": round(avg_buy, 2),
                "avg_margin_balance": round(avg_bal, 2),
            }

        return result

    def get_seasonality(self) -> dict:
        """两融季节性同比分析"""
        if self.total_data is None or len(self.total_data) < 250:
            return {"status": "历史数据不足"}

        df = self.total_data.sort_values('日期').copy()
        
        # 计算两融活跃度: 融资买入额/融资余额
        df['活跃度'] = df['融资买入额_合计'] / df['融资余额_合计'].replace(0, np.nan) * 100
        df['活跃度'] = df['活跃度'].replace([np.inf, -np.inf], np.nan)
        df = df.dropna(subset=['活跃度'])
        
        # 按周汇总
        df['年'] = df['日期'].dt.year
        df['周'] = df['日期'].dt.isocalendar().week.astype(int)
        df['月'] = df['日期'].dt.month
        df['日'] = df['日期'].dt.day
        
        weekly = df.groupby(['年', '周']).agg({
            '活跃度': 'mean',
            '融资余额_合计': 'last',
        }).reset_index()
        weekly['融资余额周变化'] = weekly.groupby('年')['融资余额_合计'].diff()
        
        # 当前周
        current_year = pd.Timestamp(self.week_start).year
        current_week = pd.Timestamp(self.week_start).isocalendar()[1]
        current_month = pd.Timestamp(self.week_start).month
        
        # 确定季节性标签
        season_label = self._get_season_label(current_month, pd.Timestamp(self.week_start).day)
        
        # 方法1：同行业周次对比（融资余额变化）
        same_week = weekly[weekly['周'] == current_week].dropna(subset=['融资余额周变化'])
        
        if len(same_week) < 3:
            nearby = weekly[(weekly['周'] >= current_week - 1) & (weekly['周'] <= current_week + 1)]
            same_week = nearby.dropna(subset=['融资余额周变化'])
        
        # 当前值
        ws = pd.Timestamp(self.week_start)
        we = pd.Timestamp(self.week_end)
        wk = self.total_data[(self.total_data['日期'] >= ws) & (self.total_data['日期'] <= we)]
        
        current_change = 0
        if len(wk) >= 2:
            current_change = (wk.iloc[-1]['融资余额_合计'] - wk.iloc[0]['融资余额_合计']) / 1e8
        
        result = {"status": "ok", "season_label": season_label}
        
        if len(same_week) >= 3:
            changes = same_week['融资余额周变化'].values / 1e8
            z, mean, pctile = compute_zscore(changes, current_change)
            
            result.update({
                "current_week_change": round(current_change, 2),
                "historical_mean": round(mean, 2),
                "historical_median": round(np.median(changes), 2),
                "historical_std": round(np.std(changes, ddof=1) if len(changes) > 1 else 1.0, 2),
                "z_score": round(z, 2),
                "z_label": zscore_label(z),
                "percentile": round(pctile, 1),
                "sample_count": len(changes),
                "sample_years": sorted(same_week['年'].tolist()),
                "historical_changes": {int(r['年']): round(r['融资余额周变化'] / 1e8, 2) 
                                       for _, r in same_week.iterrows()},
            })
        
        # 方法2：活跃度历史分位
        if 'activity' in self.get_weekly_summary():
            act = self.get_weekly_summary()['activity']
            result['activity_percentile'] = act['historical_percentile']
            result['activity_ratio'] = act['turnover_ratio']
        
        # 全历史活跃度分位（更精确）
        all_activity = weekly['活跃度'].dropna()
        current_wk = weekly[(weekly['年'] == current_year) & (weekly['周'] == current_week)]
        if len(current_wk) > 0:
            cur_act = current_wk.iloc[0]['活跃度']
            if not np.isnan(cur_act):
                act_pctile = (all_activity < cur_act).sum() / len(all_activity) * 100
                result['activity_percentile_full'] = round(act_pctile, 1)
                result['activity_current'] = round(cur_act, 2)
        
        return result

    def _get_season_label(self, month, day):
        """根据月日确定季节性标签"""
        if month == 1:
            return f"1月{day}日前"
        elif month == 2 and day <= 15:
            return "春节前后"
        elif month == 2 and day > 15:
            return "春节后回补期"
        elif month in [3, 4]:
            return "春季躁动期"
        elif month == 5 and day <= 10:
            return "五一后开盘"
        elif month in [5, 6]:
            return "5-6月调整期"
        elif month in [7, 8]:
            return "中报窗口期"
        elif month == 9 and day <= 25:
            return "9月布局期"
        elif month == 9 and day > 25:
            return "国庆前避险"
        elif month == 10 and day <= 15:
            return "国庆后第1周"
        elif month == 10:
            return "10月下旬"
        elif month == 11:
            return "11月政策窗口"
        elif month == 12 and day <= 15:
            return "12月中央经济工作会议"
        else:
            return "年末收官"


# ============================================================
# 模块3: 北上资金双口径
# ============================================================
class NorthboundAnalyzer:
    def __init__(self, week_start, week_end):
        self.week_start = week_start
        self.week_end = week_end
        self.hist_data = None
        self.flow_summary = None

    def collect(self):
        print("\n📊 [模块3] 北上资金双口径...")
        import akshare as ak

        # 口径基础：北上资金历史数据
        self.hist_data = safe_call(ak.stock_hsgt_hist_em, symbol="北向资金")
        if self.hist_data is not None and len(self.hist_data) > 0:
            self.hist_data['日期'] = pd.to_datetime(self.hist_data['日期'])
            print(f"  ✅ 北上历史: {len(self.hist_data)}条")
        else:
            print("  ❌ 北上历史失败")

        # 尝试获取资金流向摘要
        try:
            self.flow_summary = safe_call(ak.stock_hsgt_fund_flow_summary_em)
            if self.flow_summary is not None and len(self.flow_summary) > 0:
                print(f"  ✅ 资金流向摘要: {len(self.flow_summary)}条")
        except:
            pass

    def get_summary(self) -> dict:
        if self.hist_data is None:
            return {"status": "数据不可用"}

        ws, we = pd.Timestamp(self.week_start), pd.Timestamp(self.week_end)
        week = self.hist_data[(self.hist_data['日期'] >= ws) & (self.hist_data['日期'] <= we)]
        if len(week) == 0:
            return {"status": "本周无数据"}

        result = {"status": "ok", "trading_days": len(week)}

        # 检查是否有真实净买额数据（2024.6后为NaN）
        net_buy = week['当日成交净买额'].dropna()
        if len(net_buy) > 0 and net_buy.abs().sum() > 0:
            result['net_buy_total'] = round(float(net_buy.sum()), 2)
            result['has_real_data'] = True
        else:
            result['has_real_data'] = False
            result['note'] = "2024年6月起不披露净买额，使用代理变量"

        # 代理变量1：持股市值变化
        mv = week['持股市值'].dropna()
        if len(mv) >= 2 and mv.iloc[-1] > 0:
            result['market_value_start'] = round(float(mv.iloc[0]), 2)
            result['market_value_end'] = round(float(mv.iloc[-1]), 2)
            result['market_value_change'] = round(float(mv.iloc[-1] - mv.iloc[0]), 2)

        # 代理变量2：成交总额
        buy = week['买入成交额'].dropna()
        sell = week['卖出成交额'].dropna()
        if len(buy) > 0 and buy.sum() > 0:
            result['weekly_buy'] = round(float(buy.sum()), 2)
            result['weekly_sell'] = round(float(sell.sum()) if len(sell) > 0 else 0, 2)

        # 沪深300参考
        hs300 = week['沪深300-涨跌幅'].dropna()
        if len(hs300) > 0:
            result['hs300_weekly_pct'] = round(float(hs300.sum()), 2)

        # 口径A/B说明
        result['dual_calibration'] = {
            "口径A_前十大活跃股": "2024.6后无精确行业分布数据",
            "口径B_持股变化": "持股明细接口暂不可用，待数据恢复后启用",
            "当前可用": "仅汇总层面代理变量（持股市值变化）",
        }

        return result


# ============================================================
# 模块4: 主动偏股基金仓位（降级处理）
# ============================================================
class FundPositionAnalyzer:
    """主动偏股基金仓位变化 - 二次规划法框架预留"""
    
    def __init__(self, week_start, week_end):
        self.week_start = week_start
        self.week_end = week_end
    
    def collect(self):
        print("\n📊 [模块4] 主动偏股基金仓位（降级）...")
        print("  ⚠️ 需基金季报数据，当前为框架预留")
        print("  📋 二次规划法: β_i通过QP求解，剔除涨跌幅后的主动加减仓")
    
    def get_summary(self) -> dict:
        return {
            "status": "降级-数据暂不可得",
            "note": "需等待最新基金季报披露后启用二次规划法",
            "method": "Quadratic Programming: min Σε², s.t. Σβ≈1, β≥0",
            "data_timeliness": "估算值-基于最近一期已披露季报",
        }


# ============================================================
# 模块5: 龙虎榜
# ============================================================
class LHBAnalyzer:
    def __init__(self, week_start, week_end, stock_to_sw):
        self.week_start = week_start
        self.week_end = week_end
        self.stock_to_sw = stock_to_sw
        self.detail = None
        self.inst = None

    def collect(self):
        print("\n📊 [模块5] 龙虎榜...")
        import akshare as ak
        ws_s = self.week_start.strftime("%Y%m%d")
        we_s = self.week_end.strftime("%Y%m%d")

        self.detail = safe_call(ak.stock_lhb_detail_em, start_date=ws_s, end_date=we_s)
        if self.detail is not None and len(self.detail) > 0:
            print(f"  ✅ 龙虎榜: {len(self.detail)}条")
        else:
            print("  ⚠️ 龙虎榜为空")

        self.inst = safe_call(ak.stock_lhb_jgmmtj_em, start_date=ws_s, end_date=we_s)
        if self.inst is not None and len(self.inst) > 0:
            print(f"  ✅ 机构席位: {len(self.inst)}条")

    def get_summary(self) -> dict:
        r = {"status": "ok" if self.detail is not None and len(self.detail) > 0 else "数据不可用"}
        if self.detail is None or len(self.detail) == 0:
            return r

        df = self.detail
        total_amount = df['龙虎榜成交额'].sum() if '龙虎榜成交额' in df.columns else 0
        market_amount = df['市场总成交额'].sum() if '市场总成交额' in df.columns else 0

        r.update({
            "record_count": len(df),
            "total_net_buy": round(df['龙虎榜净买额'].sum() / 1e8, 2) if '龙虎榜净买额' in df.columns else 0,
            "total_buy": round(df['龙虎榜买入额'].sum() / 1e8, 2) if '龙虎榜买入额' in df.columns else 0,
            "total_sell": round(df['龙虎榜卖出额'].sum() / 1e8, 2) if '龙虎榜卖出额' in df.columns else 0,
            "total_amount": round(total_amount / 1e8, 2),
            "market_amount": round(market_amount / 1e8, 2),
            "lhb_ratio": round(total_amount / market_amount * 100, 4) if market_amount > 0 else 0,
        })

        # TOP10净买入
        if '龙虎榜净买额' in df.columns:
            top_buy = df.nlargest(10, '龙虎榜净买额')[['代码', '名称', '龙虎榜净买额', '涨跌幅']].copy()
            top_buy['net_wan'] = (top_buy['龙虎榜净买额'] / 1e4).round(0)
            # 映射行业
            top_buy['行业'] = top_buy['代码'].apply(
                lambda x: self.stock_to_sw.get(str(x).zfill(6), '未知'))
            r['top_buy'] = top_buy.to_dict('records')

            top_sell = df.nsmallest(10, '龙虎榜净买额')[['代码', '名称', '龙虎榜净买额', '涨跌幅']].copy()
            top_sell['net_wan'] = (top_sell['龙虎榜净买额'] / 1e4).round(0)
            top_sell['行业'] = top_sell['代码'].apply(
                lambda x: self.stock_to_sw.get(str(x).zfill(6), '未知'))
            r['top_sell'] = top_sell.to_dict('records')

            # 行业汇总
            industry_lhb = {}
            for _, row in df.iterrows():
                code = str(row.get('代码', '')).zfill(6)
                sw = self.stock_to_sw.get(code)
                if sw:
                    net = row.get('龙虎榜净买额', 0)
                    industry_lhb[sw] = industry_lhb.get(sw, 0) + net
            r['industry_flow'] = {k: round(v / 1e8, 4) for k, v in 
                                   sorted(industry_lhb.items(), key=lambda x: x[1], reverse=True)}

        # 机构席位
        if self.inst is not None and len(self.inst) > 0:
            idf = self.inst
            r['inst_count'] = len(idf)
            r['inst_net_buy'] = round(idf['机构买入净额'].sum() / 1e8, 2) if '机构买入净额' in idf.columns else 0
            
            if '机构买入净额' in idf.columns:
                inst_top = idf.nlargest(5, '机构买入净额')[['代码', '名称', '机构买入净额', '买方机构数', '卖方机构数']].copy()
                inst_top['net_wan'] = (inst_top['机构买入净额'] / 1e4).round(0)
                # 映射行业
                inst_top['行业'] = inst_top['代码'].apply(
                    lambda x: self.stock_to_sw.get(str(x).zfill(6), '未知'))
                r['inst_top'] = inst_top.to_dict('records')

                # 机构行业汇总
                inst_industry = {}
                for _, row in idf.iterrows():
                    code = str(row.get('代码', '')).zfill(6)
                    sw = self.stock_to_sw.get(code)
                    if sw:
                        net = row.get('机构买入净额', 0)
                        inst_industry[sw] = inst_industry.get(sw, 0) + net
                r['inst_industry_flow'] = {k: round(v / 1e8, 4) for k, v in
                                           sorted(inst_industry.items(), key=lambda x: x[1], reverse=True)}

        return r


# ============================================================
# 模块6: 资金共识矩阵
# ============================================================
def build_consensus_matrix(etf_industry, margin_industry, northbound_data, 
                            fund_data, lhb_industry, stock_to_sw):
    """
    构建资金共识矩阵
    每类资金对各行业做Z-score，然后方向打分
    方向打分：显著净买入+2, 小幅+1, 持平0, 小幅-1, 显著-2
    """
    print("\n📊 [模块6] 构建资金共识矩阵...")
    
    # 收集各资金的行业分布
    fund_flows = {
        'ETF': etf_industry or {},
        '两融': margin_industry or {},
        '北上': {},  # 北上行业数据不可得
        '主动基金': {},  # 降级
        '龙虎榜': lhb_industry or {},
    }
    
    # 对每类资金计算行业Z-score
    zscores = {}
    for fund_type, industry_data in fund_flows.items():
        if not industry_data:
            zscores[fund_type] = {}
            continue
        
        values = list(industry_data.values())
        if len(values) < 2:
            zscores[fund_type] = {k: 0.0 for k in industry_data}
            continue
        
        mean = np.mean(values)
        std = np.std(values, ddof=1) if len(values) > 1 else 1.0
        if std == 0:
            std = 1.0
        
        zscores[fund_type] = {k: (v - mean) / std for k, v in industry_data.items()}
    
    # 构建共识矩阵
    matrix = {}
    for ind in SW_L1_INDUSTRIES:
        scores = {}
        z_vals = {}
        
        for fund_type in ['ETF', '两融', '北上', '主动基金', '龙虎榜']:
            z = zscores.get(fund_type, {}).get(ind, 0.0)
            z_vals[fund_type] = z
            
            # 方向打分
            if z > 1.0:
                scores[fund_type] = 2
            elif z > 0.3:
                scores[fund_type] = 1
            elif z >= -0.3:
                scores[fund_type] = 0
            elif z >= -1.0:
                scores[fund_type] = -1
            else:
                scores[fund_type] = -2
        
        # 共识度 = 五类资金打分之和
        consensus = sum(scores.values())
        
        # 共识度分级
        if consensus >= 6:
            grade = "极高共识买入"
        elif consensus >= 3:
            grade = "较高共识买入"
        elif consensus >= -2:
            grade = "分歧/中性"
        elif consensus >= -5:
            grade = "较高共识卖出"
        else:
            grade = "极高共识卖出"
        
        # 多空对决检测
        institutional_buy = (scores.get('ETF', 0) > 0) and (scores.get('北上', 0) > 0)
        retail_sell = (scores.get('两融', 0) < 0) and (scores.get('龙虎榜', 0) < 0)
        institutional_sell = (scores.get('ETF', 0) < 0) and (scores.get('北上', 0) < 0)
        retail_buy = (scores.get('两融', 0) > 0) and (scores.get('龙虎榜', 0) > 0)
        
        conflict = ""
        if institutional_buy and retail_sell:
            conflict = "⚔️机构买-散户卖"
        elif institutional_sell and retail_buy:
            conflict = "⚔️机构卖-散户买"
        
        matrix[ind] = {
            "scores": scores,
            "z_scores": z_vals,
            "consensus": consensus,
            "grade": grade,
            "conflict": conflict,
        }
    
    return matrix


# ============================================================
# 模块7: 行业综合净流入排序
# ============================================================
def compute_industry_composite_score(consensus_matrix):
    """
    加权Z-score汇总
    ETF 25% + 两融 25% + 北上 20% + 主动基金 20% + 龙虎榜 10%
    """
    print("\n📊 [模块7] 行业综合净流入排序...")
    
    weights = {'ETF': 0.25, '两融': 0.25, '北上': 0.20, '主动基金': 0.20, '龙虎榜': 0.10}
    
    composite = {}
    for ind in SW_L1_INDUSTRIES:
        if ind not in consensus_matrix:
            continue
        z_vals = consensus_matrix[ind]['z_scores']
        score = sum(weights.get(ft, 0) * z_vals.get(ft, 0) for ft in weights)
        composite[ind] = {
            "composite_score": round(score, 3),
            "z_ETF": round(z_vals.get('ETF', 0), 3),
            "z_两融": round(z_vals.get('两融', 0), 3),
            "z_北上": round(z_vals.get('北上', 0), 3),
            "z_主动基金": round(z_vals.get('主动基金', 0), 3),
            "z_龙虎榜": round(z_vals.get('龙虎榜', 0), 3),
            "consensus": consensus_matrix[ind]['consensus'],
            "grade": consensus_matrix[ind]['grade'],
        }
    
    sorted_inds = sorted(composite.items(), key=lambda x: x[1]['composite_score'], reverse=True)
    return dict(sorted_inds)


# ============================================================
# 模块8: 科技板块专项判断
# ============================================================
def judge_tech_composite(composite_scores, consensus_matrix, etf_industry, lhb_industry):
    """
    科技板块综合判断
    范围：电子+通信+计算机+国防军工
    """
    print("\n📊 [模块8] 科技板块专项判断...")
    
    tech_data = {}
    for ind in TECH_INDUSTRIES:
        if ind in composite_scores:
            tech_data[ind] = composite_scores[ind]
    
    if not tech_data:
        return "观望", 0.0, "科技板块数据不可用", {}
    
    # 1. 规模加权综合分（简化用等权）
    scores = [d['composite_score'] for d in tech_data.values()]
    tech_composite = np.mean(scores) if scores else 0
    
    # 2. 方向一致性
    positive_count = sum(1 for s in scores if s > 0)
    consistency_bonus = 0
    if positive_count >= 3:
        consistency_bonus = 0.5
    elif positive_count <= 1:
        consistency_bonus = -0.5
    
    # 3. 机构共识加成（ETF和北上同向）
    institutional_bonus = 0
    etf_z = np.mean([consensus_matrix.get(ind, {}).get('z_scores', {}).get('ETF', 0) 
                     for ind in TECH_INDUSTRIES])
    nb_z = np.mean([consensus_matrix.get(ind, {}).get('z_scores', {}).get('北上', 0) 
                    for ind in TECH_INDUSTRIES])
    
    if etf_z > 0 and nb_z > 0:
        institutional_bonus = 0.3
    elif etf_z < 0 and nb_z < 0:
        institutional_bonus = -0.3
    
    # 最终得分
    final_score = tech_composite + consistency_bonus + institutional_bonus
    
    # 4. 五级判定
    if final_score > 1.0:
        judgment = "强烈买入"
    elif final_score > 0.3:
        judgment = "谨慎买入"
    elif final_score >= -0.3:
        judgment = "观望"
    elif final_score >= -1.0:
        judgment = "谨慎卖出"
    else:
        judgment = "强烈卖出"
    
    # 构建详情
    details = {}
    for ind in TECH_INDUSTRIES:
        if ind in tech_data:
            d = tech_data[ind]
            details[ind] = {
                "composite": d['composite_score'],
                "z_ETF": d['z_ETF'],
                "z_两融": d['z_两融'],
                "z_北上": d['z_北上'],
                "z_龙虎榜": d['z_龙虎榜'],
                "consensus": d['consensus'],
                "grade": d['grade'],
            }
        else:
            details[ind] = {"composite": 0, "note": "数据不可得"}
    
    logic = (f"综合分{final_score:.2f}("
             f"基础{tech_composite:.2f}"
             f"+一致性{consistency_bonus:+.1f}"
             f"+机构{institutional_bonus:+.1f})")
    
    return judgment, final_score, logic, details


# ============================================================
# 报告生成
# ============================================================
def generate_report(ws, we, margin_s, etf_s, nb_s, fund_s, lhb_s, 
                    consensus_matrix, composite_scores, tech_result):
    """生成完整Markdown报告"""
    from datetime import date
    
    week_label = f"{ws.year}年第{ws.isocalendar()[1]}周"
    today_str = date.today().strftime('%Y年%m月%d日')
    ws_s = ws.strftime('%m月%d日')
    we_s = we.strftime('%m月%d日')
    
    tech_judgment, tech_score, tech_logic, tech_details = tech_result
    
    L = []
    L.append(f"# 国金周度资金流监控 | {week_label}（{ws_s}-{we_s}）")
    L.append("")
    L.append(f"> **报告日期**: {today_str}")
    L.append(f"> **观察窗口**: {ws_s}-{we_s}（{margin_s.get('trading_days', 5)}个交易日）")
    L.append(f"> **框架**: 国金策略《资金跟踪系列》五大资金主体交叉验证")
    L.append(f"> **数据源**: AKShare (沪深交易所/东方财富/中证指数)")
    L.append("")
    L.append("---")
    L.append("")
    
    # ====== 1. 核心结论 ======
    L.append("## 🎯 一、核心结论")
    L.append("")
    
    mc = margin_s.get('融资余额_变化', 0)
    en = etf_s.get('total_net_subscription', 0)
    lhb_net = lhb_s.get('total_net_buy', 0)
    
    # 构建一句话结论
    etf_desc = f"ETF净{'购' if en > 0 else '赎回'}{abs(en):.0f}亿"
    margin_desc = f"两融{'扩' if mc > 0 else '缩'}{abs(mc):.0f}亿"
    season = margin_s.get('seasonal', {})
    season_desc = ""
    if season.get('status') == 'ok':
        season_desc = f"({season.get('z_label', '')})"
    
    core_conclusion = f"{etf_desc}托底、{margin_desc}{season_desc}，科技{tech_judgment}"
    
    L.append(f"**{core_conclusion}**")
    L.append("")
    L.append(f"**关键数据**:")
    L.append(f"- ① ETF净申购 **{en:.1f}亿**（宽基{etf_s.get('broad_net_subscription', 0):.1f}亿 + 行业{etf_s.get('sector_net_subscription', 0):.1f}亿）")
    L.append(f"- ② 融资余额变化 **{mc:+.1f}亿**，活跃度{margin_s.get('activity', {}).get('turnover_ratio', 0):.1f}%（{margin_s.get('activity', {}).get('historical_percentile', 0):.0f}%分位）")
    if season.get('status') == 'ok':
        L.append(f"- ③ 季节性Z-score **{season.get('z_score', 0):.2f}** → {season.get('z_label', '正常')}")
    L.append(f"- 🔬 科技板块判断: **{tech_judgment}**（综合分{tech_score:.2f}）")
    L.append("")
    L.append("---")
    L.append("")
    
    # ====== 2. 资金主体全景表 ======
    L.append("## 📊 二、资金主体全景表")
    L.append("")
    L.append("| 资金类型 | 周度净流向 | 方向 | 备注 |")
    L.append("|---------|----------|------|------|")
    
    # ETF
    L.append(f"| 股票ETF | {en:+.1f}亿 | {'净申购' if en > 0 else '净赎回'} | 跟踪{etf_s.get('tracked_etf_count', 0)}只 |")
    
    # 两融
    margin_dir = "融资余额增加" if mc > 0 else "融资余额减少"
    L.append(f"| 两融 | {mc:+.1f}亿 | {margin_dir} | 活跃度{margin_s.get('activity', {}).get('turnover_ratio', 0):.1f}% |")
    
    # 北上
    if nb_s.get('status') == 'ok':
        if nb_s.get('has_real_data'):
            nb_val = nb_s.get('net_buy_total', 0)
            L.append(f"| 北上资金 | {nb_val:+.1f}亿 | {'净买入' if nb_val > 0 else '净卖出'} | 精确数据 |")
        else:
            mv_chg = nb_s.get('market_value_change', 'N/A')
            L.append(f"| 北上资金 | 持股市值变化{mv_chg}亿 | 代理变量 | 2024.6后不披露净买额 |")
    else:
        L.append("| 北上资金 | 数据暂不可得 | - | - |")
    
    # 主动基金
    L.append(f"| 主动偏股基金 | 数据暂不可得 | - | {fund_s.get('note', '待季报披露')} |")
    
    # 龙虎榜
    if lhb_s.get('status') == 'ok':
        L.append(f"| 龙虎榜 | {lhb_net:+.1f}亿 | {'净买入' if lhb_net > 0 else '净卖出'} | {lhb_s.get('record_count', 0)}条记录 |")
    else:
        L.append("| 龙虎榜 | 数据暂不可得 | - | - |")
    
    L.append("")
    L.append("---")
    L.append("")
    
    # ====== 3. 资金共识矩阵 ======
    L.append("## 📊 三、资金共识矩阵")
    L.append("")
    
    def score_icon(v):
        if v >= 2: return "++"
        elif v == 1: return "+"
        elif v == 0: return "0"
        elif v == -1: return "-"
        else: return "--"
    
    # 按共识度绝对值排序
    sorted_matrix = sorted(consensus_matrix.items(), key=lambda x: abs(x[1]['consensus']), reverse=True)
    
    L.append("| 行业 | ETF | 两融 | 北上 | 主动基金 | 龙虎榜 | 共识度 | 分级 | 特殊 |")
    L.append("|------|-----|------|------|---------|--------|-------|------|------|")
    
    for ind, d in sorted_matrix:
        if d['consensus'] == 0 and all(v == 0 for v in d['scores'].values()):
            continue  # 跳过完全无数据的行业
        sc = d['scores']
        conflict = d.get('conflict', '')
        L.append(f"| **{ind}** | {score_icon(sc.get('ETF', 0))} | {score_icon(sc.get('两融', 0))} | "
                 f"{score_icon(sc.get('北上', 0))} | {score_icon(sc.get('主动基金', 0))} | "
                 f"{score_icon(sc.get('龙虎榜', 0))} | {d['consensus']:+d} | "
                 f"{d['grade']} | {conflict} |")
    
    L.append("")
    L.append("> ++=显著买入(2) +=小幅买入(1) 0=中性(0) -=小幅卖出(-1) --=显著卖出(-2)")
    L.append("> 共识度范围 -10~+10: ≥+6极高共识买, +3~+5较高买, -2~+2分歧, -5~-3较高卖, ≤-6极高卖")
    L.append("")
    
    # 高共识行业
    high_buy = [(ind, d) for ind, d in sorted_matrix if d['consensus'] >= 3]
    high_sell = [(ind, d) for ind, d in sorted_matrix if d['consensus'] <= -3]
    conflicts = [(ind, d) for ind, d in sorted_matrix if d.get('conflict')]
    
    if high_buy:
        parts = [f"{ind}({d['consensus']:+d})" for ind, d in high_buy[:5]]
        L.append(f"**🟢 高共识买入行业**: {', '.join(parts)}")
        L.append("")
    if high_sell:
        parts = [f"{ind}({d['consensus']:+d})" for ind, d in high_sell[:5]]
        L.append(f"**🔴 高共识卖出行业**: {', '.join(parts)}")
        L.append("")
    if conflicts:
        parts = [f"{ind}({d['conflict']})" for ind, d in conflicts]
        L.append(f"**⚔️ 多空对决行业**: {', '.join(parts)}")
        L.append("")
    
    L.append("---")
    L.append("")
    
    # ====== 4. 行业净流入/流出TOP10 ======
    L.append("## 📈 四、行业综合净流入排序")
    L.append("")
    
    sorted_composite = sorted(composite_scores.items(), key=lambda x: x[1]['composite_score'], reverse=True)
    
    L.append("### 净流入TOP10")
    L.append("")
    L.append("| 排名 | 行业 | 综合得分 | ETF-Z | 两融-Z | 北上-Z | 龙虎榜-Z | 共识 |")
    L.append("|------|------|---------|-------|--------|--------|---------|------|")
    for i, (ind, d) in enumerate(sorted_composite[:10], 1):
        L.append(f"| {i} | **{ind}** | {d['composite_score']:+.3f} | "
                 f"{d['z_ETF']:+.2f} | {d['z_两融']:+.2f} | {d['z_北上']:+.2f} | "
                 f"{d['z_龙虎榜']:+.2f} | {d['consensus']:+d} |")
    L.append("")
    
    L.append("### 净流出TOP10")
    L.append("")
    L.append("| 排名 | 行业 | 综合得分 | ETF-Z | 两融-Z | 北上-Z | 龙虎榜-Z | 共识 |")
    L.append("|------|------|---------|-------|--------|--------|---------|------|")
    for i, (ind, d) in enumerate(reversed(sorted_composite[-10:]), 1):
        L.append(f"| {i} | **{ind}** | {d['composite_score']:+.3f} | "
                 f"{d['z_ETF']:+.2f} | {d['z_两融']:+.2f} | {d['z_北上']:+.2f} | "
                 f"{d['z_龙虎榜']:+.2f} | {d['consensus']:+d} |")
    L.append("")
    
    L.append("---")
    L.append("")
    
    # ====== 5. 科技板块专项 ======
    L.append("## 🔬 五、科技板块专项判断")
    L.append("")
    L.append(f"### 综合判断: **{tech_judgment}**")
    L.append(f"**综合分: {tech_score:.2f}** — {tech_logic}")
    L.append("")
    
    L.append("| 子行业 | 综合Z | ETF-Z | 两融-Z | 北上-Z | 龙虎榜-Z | 共识度 | 分级 |")
    L.append("|-------|-------|-------|--------|--------|---------|--------|------|")
    for ind in TECH_INDUSTRIES:
        if ind in tech_details:
            d = tech_details[ind]
            if 'note' in d:
                L.append(f"| {ind} | - | - | - | - | - | - | {d['note']} |")
            else:
                L.append(f"| **{ind}** | {d['composite']:+.3f} | {d['z_ETF']:+.2f} | "
                         f"{d['z_两融']:+.2f} | {d['z_北上']:+.2f} | {d['z_龙虎榜']:+.2f} | "
                         f"{d['consensus']:+d} | {d['grade']} |")
        else:
            L.append(f"| {ind} | 数据不可得 | - | - | - | - | - | - |")
    L.append("")
    
    # 判定阈值说明
    L.append("> 判定阈值: >1.0强烈买入, 0.3~1.0谨慎买入, -0.3~0.3观望, -1.0~-0.3谨慎卖出, <-1.0强烈卖出")
    L.append("> 加分项: 四行业方向一致(+0.5)/分歧(-0.5), ETF+北上同向买(+0.3)/同向卖(-0.3)")
    L.append("")
    L.append("---")
    L.append("")
    
    # ====== 6. 各资金分项详解 ======
    L.append("## 📋 六、各资金分项详解")
    L.append("")
    
    # --- 6.1 ETF ---
    L.append("### 6.1 ETF资金流向 + 行业分摊")
    L.append("")
    if etf_s.get("status") == "ok":
        L.append(f"跟踪 **{etf_s.get('tracked_etf_count', 0)}** 只主要ETF，"
                 f"估算净申购 **{etf_s.get('total_net_subscription', 0):.1f}亿**。")
        L.append(f"- 宽基ETF: **{etf_s.get('broad_net_subscription', 0):.1f}亿**（按成分股行业权重分摊到申万一级）")
        L.append(f"- 行业ETF: **{etf_s.get('sector_net_subscription', 0):.1f}亿**（直接归类）")
        L.append("")
        
        if etf_s.get('broad_details'):
            L.append("**宽基ETF明细**:")
            L.append("")
            L.append("| ETF | 净申购(亿) | 当前规模(亿) |")
            L.append("|-----|----------|------------|")
            for n, c, a in etf_s['broad_details']:
                L.append(f"| {n} | {c:+.2f} | {a:.0f} |")
            L.append("")
        
        if etf_s.get('sector_details'):
            L.append("**行业ETF明细**:")
            L.append("")
            L.append("| ETF | 行业 | 净申购(亿) |")
            L.append("|-----|------|----------|")
            for n, ind, c in etf_s['sector_details']:
                L.append(f"| {n} | {ind} | {c:+.2f} |")
            L.append("")
        
        if etf_s.get('industry_flow'):
            sorted_flow = sorted(etf_s['industry_flow'].items(), key=lambda x: x[1], reverse=True)
            L.append("**ETF行业分摊汇总（绝对净流入 亿元）**:")
            L.append("")
            L.append("| 行业 | 净流入(亿) | 方向 |")
            L.append("|------|----------|------|")
            for ind, val in sorted_flow[:15]:
                L.append(f"| {ind} | {val:+.2f} | {'净申购' if val > 0 else '净赎回'} |")
            L.append("")
    else:
        L.append("*ETF数据不可用*")
    L.append("")
    
    # --- 6.2 两融 ---
    L.append("### 6.2 两融资金 + 季节性分析")
    L.append("")
    if margin_s.get("status") == "ok":
        L.append("| 指标 | 数值(亿元) |")
        L.append("|------|-----------|")
        L.append(f"| 融资余额(周末) | {margin_s.get('融资余额_周末', 0):.2f} |")
        L.append(f"| 融资余额(周初) | {margin_s.get('融资余额_周初', 0):.2f} |")
        L.append(f"| **融资余额变化** | **{margin_s.get('融资余额_变化', 0):+.2f}** |")
        L.append(f"| 两融余额(周末) | {margin_s.get('两融余额_周末', 0):.2f} |")
        L.append(f"| 两融余额变化 | {margin_s.get('两融余额_变化', 0):+.2f} |")
        if '融资余额_上周变化' in margin_s:
            L.append(f"| 上周变化 | {margin_s['融资余额_上周变化']:+.2f} |")
            L.append(f"| 环比变化 | {margin_s.get('融资余额_变化_环比', 0):+.2f} |")
        L.append(f"| 沪市融资余额 | {margin_s.get('沪市融资余额', 0):.2f} |")
        L.append(f"| 深市融资余额 | {margin_s.get('深市融资余额', 0):.2f} |")
        L.append("")
        
        # 活跃度
        act = margin_s.get('activity', {})
        if act:
            L.append("**两融活跃度**:")
            L.append(f"- 融资买入/融资余额: **{act.get('turnover_ratio', 0):.2f}%**")
            L.append(f"- 历史分位: **{act.get('historical_percentile', 0):.0f}%**")
            L.append(f"- 日均融资买入: {act.get('avg_daily_margin_buy', 0):.2f}亿")
            L.append("")
        
        # 季节性分析
        season = margin_s.get('seasonal', {})
        if season.get('status') == 'ok':
            L.append("**季节性同比分析**:")
            L.append(f"- 季节性标签: **{season.get('season_label', '未知')}**")
            L.append(f"- 本周变化: **{season.get('current_week_change', 0):+.1f}亿** vs 历史均值{season.get('historical_mean', 0):.1f}亿")
            L.append(f"- Z-score: **{season.get('z_score', 0):.2f}** → **{season.get('z_label', '正常')}**")
            L.append(f"- 历史分位: {season.get('percentile', 0):.0f}% | 样本{season.get('sample_count', 0)}年")
            L.append("")
            
            L.append("| 年份 | 变化(亿) |")
            L.append("|------|---------|")
            for y, c in sorted(season.get('historical_changes', {}).items()):
                L.append(f"| {y} | {c:+.2f} |")
            L.append("")
    else:
        L.append("*两融数据不可用*")
    L.append("")
    
    # --- 6.3 北上 ---
    L.append("### 6.3 北上资金（双口径）")
    L.append("")
    if nb_s.get("status") == "ok":
        L.append("> ⚠️ 2024年6月起港交所不再披露北上净买额，使用代理变量")
        L.append("")
        
        if nb_s.get('has_real_data'):
            L.append(f"- 净买额: **{nb_s.get('net_buy_total', 0):+.2f}亿**")
        else:
            L.append(f"- {nb_s.get('note', '')}")
        
        if 'market_value_change' in nb_s:
            L.append(f"- 持股市值变化: {nb_s['market_value_change']:+.2f}亿（代理变量1）")
        if 'weekly_buy' in nb_s:
            L.append(f"- 周买入: {nb_s['weekly_buy']:.2f}亿 | 卖出: {nb_s.get('weekly_sell', 0):.2f}亿")
        if 'hs300_weekly_pct' in nb_s:
            L.append(f"- 沪深300周涨跌: {nb_s['hs300_weekly_pct']:+.2f}%（参考基准）")
        
        # 双口径说明
        dc = nb_s.get('dual_calibration', {})
        L.append("")
        L.append("**双口径说明**:")
        L.append(f"- 口径A（前十大活跃股）: {dc.get('口径A_前十大活跃股', 'N/A')}")
        L.append(f"- 口径B（持股变化代理）: {dc.get('口径B_持股变化', 'N/A')}")
        L.append(f"- 当前可用: {dc.get('当前可用', 'N/A')}")
    else:
        L.append("*北上数据不可用*")
    L.append("")
    
    # --- 6.4 主动基金 ---
    L.append("### 6.4 主动偏股基金仓位")
    L.append("")
    L.append(f"*{fund_s.get('note', '数据暂不可得')}*")
    L.append(f"- 方法: {fund_s.get('method', '二次规划法')}")
    L.append(f"- 时效性: {fund_s.get('data_timeliness', 'N/A')}")
    L.append("")
    
    # --- 6.5 龙虎榜 ---
    L.append("### 6.5 龙虎榜")
    L.append("")
    if lhb_s.get("status") == "ok":
        L.append(f"共 **{lhb_s.get('record_count', 0)}** 条记录")
        L.append("")
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        L.append(f"| 龙虎榜成交额 | {lhb_s.get('total_amount', 0):.2f}亿 |")
        L.append(f"| 市场成交额 | {lhb_s.get('market_amount', 0):.2f}亿 |")
        L.append(f"| 占比 | {lhb_s.get('lhb_ratio', 0):.4f}% |")
        L.append(f"| **净买入** | **{lhb_s.get('total_net_buy', 0):+.2f}亿** |")
        L.append("")
        
        if lhb_s.get('inst_count'):
            L.append("**机构席位**:")
            L.append(f"- 记录: {lhb_s['inst_count']}条 | 净买入: {lhb_s.get('inst_net_buy', 0):+.2f}亿")
            if lhb_s.get('inst_top'):
                L.append("")
                L.append("| 股票 | 行业 | 净额(万) | 买方 | 卖方 |")
                L.append("|------|------|---------|------|------|")
                for it in lhb_s['inst_top']:
                    L.append(f"| {it['名称']} | {it.get('行业', 'N/A')} | {it['net_wan']:.0f} | {it['买方机构数']} | {it['卖方机构数']} |")
            L.append("")
        
        if lhb_s.get('top_buy'):
            L.append("**净买入TOP10**:")
            L.append("")
            L.append("| # | 股票 | 行业 | 净买(万) | 涨跌% |")
            L.append("|---|------|------|---------|-------|")
            for i, it in enumerate(lhb_s['top_buy'][:10], 1):
                L.append(f"| {i} | {it['名称']} | {it.get('行业', 'N/A')} | {it['net_wan']:.0f} | {it.get('涨跌幅', 0):.2f} |")
            L.append("")
        
        if lhb_s.get('industry_flow'):
            sorted_lhb_ind = sorted(lhb_s['industry_flow'].items(), key=lambda x: x[1], reverse=True)
            L.append("**龙虎榜行业分布**:")
            L.append("")
            L.append("| 行业 | 净买入(亿) |")
            L.append("|------|----------|")
            for ind, val in sorted_lhb_ind[:10]:
                L.append(f"| {ind} | {val:+.2f} |")
            L.append("")
    else:
        L.append("*龙虎榜不可用*")
    L.append("")
    
    L.append("---")
    L.append("")
    
    # ====== 7. 测算误差与风险提示 ======
    L.append("## ⚠️ 七、测算误差与风险提示")
    L.append("")
    L.append("| 维度 | 数据来源 | 局限性 |")
    L.append("|------|---------|--------|")
    L.append("| ETF行业分摊 | 中证指数成分权重 | 依赖成分股映射，部分小众ETF映射可能不精确 |")
    L.append("| 两融季节性 | 沪深交易所 | 无行业级拆分，历史样本约10年，极端年份影响均值 |")
    L.append("| 北上资金 | AKShare | 2024.6起无精确净买额，代理变量有偏差；双口径明细暂不可得 |")
    L.append("| 主动偏股基金 | 基金季报 | 二次规划法为估算值，与实际季报有误差；当前降级为框架预留 |")
    L.append("| 龙虎榜 | 东方财富 | 仅覆盖异动股，非全市场；营业部vs机构拆分可能有遗漏 |")
    L.append("| 行业映射 | 申万一级行业指数 | 部分股票同时属于多个概念板块，映射存在模糊性 |")
    L.append("")
    
    L.append(f"*报告生成: {today_str} | 框架: 国金策略《资金跟踪系列》 | 数据: AKShare*")
    
    return "\n".join(L)


# ============================================================
# 飞书推送
# ============================================================
def build_feishu_header(week_label, core_conclusion, key_numbers, tech_judgment):
    """构建飞书消息头（200字以内）"""
    lines = [
        f"【国金周度资金流监控】{week_label}",
        f"核心结论：{core_conclusion}",
        f"关键数据：{key_numbers}",
        f"科技判断：{tech_judgment}",
        "详细报告见附件 ↓",
    ]
    return "\n".join(lines)


def push_to_feishu(report_path, report_text, header_text=""):
    """推送到飞书群"""
    print("\n📤 推送飞书...")

    # 优先使用 feishu_push.py
    if os.path.exists(FEISHU_PUSH_SCRIPT):
        sys.path.insert(0, os.path.dirname(FEISHU_PUSH_SCRIPT))
        try:
            from feishu_push import push_to_feishu as _push
            # 推送文件附件
            _push(file_path=report_path, as_file=True)
            # 推送消息头卡片
            push_text = header_text if header_text else report_text[:2500]
            _push(text=push_text, as_card=True)
            print("  ✅ 推送完成")
            return True
        except Exception as e:
            print(f"  ⚠️ feishu_push失败: {e}")

    # 降级：lark-cli（目标群走统一配置，不写死）
    try:
        chat_id = _chat_id("stocks")
        if not chat_id:
            print("  ❌ " + _chat_hint("stocks"))
            return False
        push_text = header_text if header_text else report_text[:3000]
        cmd = ['lark-cli', 'im', '+messages-send', '--chat-id', chat_id,
               '--text', push_text, '--as', 'bot']
        env = os.environ.copy()
        env['LARK_CLI_NO_PROXY'] = '1'
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            print("  ✅ 飞书推送完成")
            return True
        else:
            print(f"  ❌ 推送失败: {r.stderr[:200]}")
    except Exception as e:
        print(f"  ❌ 推送异常: {e}")
    return False


# ============================================================
# 主函数
# ============================================================
def main():
    result_mode, end_date_arg, do_push, output_dir_arg = parse_args()
    
    # 确定end_date
    if end_date_arg and end_date_arg != "last_friday":
        end_date_str = end_date_arg
    else:
        today = datetime.date.today()
        offset = (today.weekday() - 4) % 7
        if offset == 0:
            offset = 7
        last_friday = today - datetime.timedelta(days=offset)
        end_date_str = last_friday.strftime("%Y-%m-%d")
    
    end_dt = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
    week_start = end_dt - datetime.timedelta(days=end_dt.weekday())
    output_dir = output_dir_arg or DEFAULT_OUTPUT_DIR

    print(f"\n📅 分析周期: {week_start} ~ {end_date_str}")
    print(f"📁 输出目录: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    # ====== Step 1: 构建基础映射 ======
    print("\n" + "=" * 40)
    print("🔧 构建基础数据映射")
    print("=" * 40)
    stock_to_sw = build_stock_to_sw_mapping()
    index_industry_weights = build_index_industry_weights(stock_to_sw)

    # ====== Step 2: 采集各模块数据 ======
    print("\n" + "=" * 40)
    print("📊 采集各资金模块数据")
    print("=" * 40)

    # 模块1: ETF
    etf = ETFAnalyzer(week_start, end_dt, index_industry_weights)
    etf.collect()
    etf_s = etf.get_summary()

    # 模块2: 两融
    margin = MarginAnalyzer(week_start, end_dt)
    margin.collect()
    margin_s = margin.get_weekly_summary()
    margin_s['seasonal'] = margin.get_seasonality()

    # 模块3: 北上
    nb = NorthboundAnalyzer(week_start, end_dt)
    nb.collect()
    nb_s = nb.get_summary()

    # 模块4: 主动基金（降级）
    fund = FundPositionAnalyzer(week_start, end_dt)
    fund.collect()
    fund_s = fund.get_summary()

    # 模块5: 龙虎榜
    lhb = LHBAnalyzer(week_start, end_dt, stock_to_sw)
    lhb.collect()
    lhb_s = lhb.get_summary()

    # ====== Step 3: 共识矩阵 + 综合排序 + 科技判断 ======
    print("\n" + "=" * 40)
    print("📊 构建共识矩阵与综合判断")
    print("=" * 40)

    # 准备各资金的行业分布数据
    etf_industry = etf_s.get('industry_flow_absolute', {}) if etf_s.get('status') == 'ok' else {}
    
    # 两融行业分布（当前无行业级数据，用全市场方向代替）
    margin_industry = {}  # 两融暂无行业拆分
    
    # 龙虎榜行业分布
    lhb_industry = lhb_s.get('industry_flow', {}) if lhb_s.get('status') == 'ok' else {}

    # 共识矩阵
    consensus_matrix = build_consensus_matrix(
        etf_industry, margin_industry, nb_s, fund_s, lhb_industry, stock_to_sw
    )

    # 综合排序
    composite_scores = compute_industry_composite_score(consensus_matrix)

    # 科技板块判断
    tech_result = judge_tech_composite(
        composite_scores, consensus_matrix, etf_industry, lhb_industry
    )

    # ====== Step 4: 生成报告 ======
    print("\n" + "=" * 40)
    print("📝 生成报告")
    print("=" * 40)

    week_label = f"{ws.year}年第{ws.isocalendar()[1]}周" if (ws := week_start) else ""
    report = generate_report(
        week_start, end_dt, margin_s, etf_s, nb_s, fund_s, lhb_s,
        consensus_matrix, composite_scores, tech_result
    )

    # 保存报告
    fn = f"国金资金流周报_{end_date_str.replace('-', '')}.md"
    fp = os.path.join(output_dir, fn)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  ✅ 报告保存: {fp}")

    # ====== Step 5: 飞书推送 ======
    if do_push:
        mc = margin_s.get('融资余额_变化', 0)
        en = etf_s.get('total_net_subscription', 0)
        tech_j = tech_result[0]
        
        core_conclusion = f"ETF净{'购' if en > 0 else '赎回'}{abs(en):.0f}亿、两融{'扩' if mc > 0 else '缩'}{abs(mc):.0f}亿，科技{tech_j}"
        key_numbers = f"① ETF {en:+.0f}亿 ② 融资{mc:+.0f}亿 ③ Z={margin_s.get('seasonal', {}).get('z_score', 0):.2f}"
        
        header = build_feishu_header(week_label, core_conclusion, key_numbers, tech_j)
        push_to_feishu(fp, report, header)

    # ====== 完成 ======
    tech_j = tech_result[0]
    en = etf_s.get('total_net_subscription', 0)
    mc = margin_s.get('融资余额_变化', 0)
    print(f"\n{'='*60}")
    print(f"📊 完成 | ETF:{en:+.1f}亿 两融:{mc:+.1f}亿 科技:{tech_j}")
    print(f"{'='*60}")

    return fp


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        traceback.print_exc()
        sys.exit(1)
