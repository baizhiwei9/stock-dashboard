#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成学习选股模型 - 每日运行器
============================
功能：
- 早上 9:00: 使用昨日收盘数据，输出 TOP20 推荐
- 中午 12:00: 使用实时快照 + 盘中数据，更新推荐

模型：XGBoost + LightGBM + CatBoost + LR (Stacking集成)

数据源四级回退：AKShare → TuShare → Coze Skill → 腾讯行情爬虫
所有数据缺失/异常均在报告中明确标注。

作者: Codex AI
日期: 2026-07-14
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

import warnings
warnings.filterwarnings('ignore')

from trading_day_check import check_trading_day
check_trading_day()

import sys
import os
import argparse
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ==================== DataHub 统一取数（禁止直连）====================
def _hub_candidates(_start):
    """DataHub 根候选（唯一口径优先）。

    先用仓库根的 ``datahub_path.py``（build.py / scheduler.py 用的同一份代码），
    它不在时退回「同级 checkout」再退老云盘路径。刻意**不**从任意上层目录找
    ``datahub-multi-tier`` —— 机器上可能有别人留下的同名旧副本（/tmp 下就有），
    按目录存在性猜会静默连错库。
    """
    out = []
    cur = os.path.abspath(_start)
    for _ in range(8):
        p = os.path.join(cur, 'datahub_path.py')
        if os.path.isfile(p):
            ns = {'__file__': p, '__name__': 'datahub_path'}
            try:
                with open(p, encoding='utf-8') as f:
                    exec(compile(f.read(), p, 'exec'), ns)
                out.append(ns['datahub_root']())
            except Exception:                              # noqa: BLE001
                pass
            out.append(os.path.join(cur, 'datahub-multi-tier'))
            out.append(os.path.join(os.path.dirname(cur), 'datahub-multi-tier'))
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    out += [os.environ.get('DATA_HUB_PATH') or os.environ.get('DATAHUB_DIR')
            or os.environ.get('EDB_REPO') or '',
            _cp('/opt/coze/.fuse_drive/dataHub_三级体系')]
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq
_HUB_PATHS = _hub_candidates(os.path.dirname(os.path.abspath(__file__)))
for _hp in _HUB_PATHS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
        break
try:
    import data_hub as dh
except Exception as _e:
    dh = None
    print(f"  ⚠ DataHub 导入失败，将回退直连: {_e}")

from data_fetcher_unified import UnifiedDataFetcher
from review_prediction import review_predictions, format_review_brief
from data_fetcher_tencent import DataFetcherTencent
from main import FeatureEngineer, PreFilter, LabelGenerator, StackingModel
from portfolio_tracker import PortfolioTracker
from watchlist_tracker import WatchlistTracker


# ── 敏感配置统一入口（app_secrets，代码里不留明文）──
for _sp in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'case', '_lib'),
            _cp('/Coze/Drive/股市监控画布/stock-dashboard/case/_lib')):
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
try:
    import app_secrets as _app_secrets
except Exception:  # noqa: BLE001
    _app_secrets = None


def _secret(dotted, *env_names, default=''):
    """敏感配置读取：环境变量 > ~/.config/stock-dashboard/secrets.json，缺失返回 default。"""
    for _n in env_names:
        _v = os.environ.get(_n)
        if _v:
            return _v
    if _app_secrets is None:
        return default
    try:
        return _app_secrets.get(dotted) or default
    except Exception:  # noqa: BLE001
        return default


FEISHU_WEBHOOK_URL = _secret('feishu.webhook', 'FEISHU_WEBHOOK_URL', 'FEISHU_WEBHOOK')


def get_realtime_snapshot() -> Optional[pd.DataFrame]:
    """获取实时行情快照（用于中午模式），多源回退"""
    
    # 尝试 1: AKShare
    try:
        print("  获取实时行情快照 (DataHub)...")
        df = None
        if dh is not None:
            for _fn in ('get_stock_spot_em', 'get_stock_spot'):
                try:
                    df = getattr(dh, _fn)()
                    if df is not None and len(df) > 0:
                        break
                except Exception as _e:  # noqa: BLE001
                    print(f"  ⚠ DataHub 快照 {_fn} 失败: {_e}")
                    df = None
        if df is None or len(df) == 0:
            raise RuntimeError("DataHub 快照为空")
        df = df.rename(columns={
            '代码': 'pure_code', '名称': 'name', '最新价': 'close',
            '涨跌幅': 'change_pct', '涨跌额': 'change',
            '成交量': 'volume', '成交额': 'amount', '振幅': 'amplitude',
            '最高': 'high', '最低': 'low', '今开': 'open',
            '昨收': 'pre_close', '量比': 'volume_ratio', '换手率': 'turnover',
            '市盈率-动态': 'pe', '市净率': 'pb',
        })
        df['code'] = df['pure_code'].apply(
            lambda x: f"sh{x}" if str(x).startswith('6') else f"sz{x}")
        print(f"  ✓ 获取到 {len(df)} 只股票实时数据")
        return df
    except Exception as e:
        print(f"  ✗ AKShare 实时快照失败: {e}")
    
    # 尝试 2: 腾讯行情接口
    try:
        print("  获取实时行情快照 (腾讯行情)...")
        url = 'https://qt.gtimg.cn/q=sh000001'
        r = requests.get(url, timeout=10)
        print(f"  ✓ 腾讯行情接口可用")
        return None
    except Exception as e:
        print(f"  ✗ 腾讯行情快照也失败: {e}")
    
    return None


def load_latest_model() -> Optional[StackingModel]:
    """加载最新的训练模型"""
    model_path = './output/stacking_model_latest.pkl'
    
    if not os.path.exists(model_path):
        print(f"  ✗ 模型文件不存在: {model_path}")
        return None
    
    try:
        import joblib
        print(f"  加载模型: {model_path}")
        model_data = joblib.load(model_path)
        print(f"  ✓ 模型加载成功")
        return model_data
    except Exception as e:
        print(f"  ✗ 模型加载失败: {e}")
        return None


def predict_today(model: StackingModel, stock_data: Dict[str, pd.DataFrame],
                  feature_cols: List[str], mode: str = 'morning') -> pd.DataFrame:
    """对今日数据进行预测"""
    feature_engineer = FeatureEngineer()
    results = []
    
    print(f"\n  对 {len(stock_data)} 只股票进行预测...")
    
    for code, df in stock_data.items():
        try:
            if len(df) < 100:
                continue
            
            df_feat = feature_engineer.compute_all_features(df)
            if df_feat is None or len(df_feat) == 0:
                continue
            
            latest = df_feat.iloc[-1:]
            missing_cols = [c for c in feature_cols if c not in latest.columns]
            if missing_cols:
                continue
            
            X_pred = latest[feature_cols].fillna(0)
            pred_prob = model.predict_proba(X_pred)[0]
            
            latest_row = df.iloc[-1]
            latest_date = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df['date'].iloc[-1]
            
            results.append({
                'code': code,
                'date': latest_date,
                'close': latest_row['close'],
                'pred_prob': pred_prob,
                'volume': latest_row.get('volume', 0),
                'amount': latest_row.get('amount', 0),
            })
        except Exception:
            continue
    
    results_df = pd.DataFrame(results)
    if len(results_df) > 0:
        results_df = results_df.sort_values('pred_prob', ascending=False)
        results_df['rank'] = range(1, len(results_df) + 1)
        total = len(results_df)
        # 分位数：该概率超过了全市场百分之多少的股票
        results_df['percentile'] = ((total - results_df['rank'] + 1) / total * 100).round(1)
    
    return results_df


# 实时快照里「有就用、没有就留空」的列。DataHub 的 akshare stock_spot 只给
# 代码/名称/最新价/涨跌幅/涨跌额/成交量/成交额/振幅/最高/最低/今开/昨收，
# 没有「换手率」「量比」（见 2026-11-25 午盘 12:25 KeyError）。这两个字段在报告里
# 只做参考，缺了不该让整条链路炸，所以按实际存在的列取，缺的补默认值。
_RT_OPTIONAL_COLS = ('turnover', 'volume_ratio')
_RT_FILL = {'turnover': 0, 'volume_ratio': 1}


def merge_with_realtime(predictions: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """将预测结果与实时快照合并（快照缺列不报错）"""
    if snapshot is None or len(snapshot) == 0:
        return predictions
    if 'code' not in snapshot.columns:
        print("  ⚠ 实时快照缺 code 列，跳过合并")
        return predictions

    base_cols = ['code', 'close', 'change_pct', 'volume', 'amount']
    use_cols = [c for c in base_cols + list(_RT_OPTIONAL_COLS) if c in snapshot.columns]
    missing = [c for c in base_cols + list(_RT_OPTIONAL_COLS) if c not in snapshot.columns]
    if missing:
        print(f"  ⚠ 实时快照缺列 {missing}，缺失字段按默认值填充")

    snap = snapshot[use_cols].copy()
    for c in missing:
        snap[c] = np.nan

    merged = predictions.merge(snap, on='code', how='left', suffixes=('_hist', '_rt'))

    # 快照里的值是当日的，优先用；缺了回退历史值
    for c in ('close', 'volume', 'amount', 'change_pct'):
        rt, hist = f'{c}_rt', f'{c}_hist'
        if rt in merged.columns and hist in merged.columns:
            merged[c] = merged[rt].fillna(merged[hist])
        elif rt in merged.columns:
            merged[c] = merged[rt]
    merged['change_pct'] = merged['change_pct'].fillna(0)
    for c, default in _RT_FILL.items():
        merged[c] = merged[c].fillna(default) if c in merged.columns else default

    drop_cols = [c for c in merged.columns if c.endswith('_rt') or c.endswith('_hist')]
    merged = merged.drop(columns=drop_cols, errors='ignore')
    return merged



def load_industry_map() -> Dict[str, str]:
    """
    加载申万三级行业映射，返回 {ts_code: industry_name}
    ts_code 格式如 '000612.SZ'
    """
    # v2：改用 DataHub 构建（5922 只，含北交所），与旧 tushare 缓存隔离
    map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'output', 'sw_l3_industry_map_v2.json')
    
    # 优先从缓存文件加载
    if os.path.exists(map_path):
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"  ✓ 行业映射已加载 ({len(data)} 只股票)")
            return data
        except Exception as e:
            print(f"  ⚠ 行业映射加载失败: {e}")
    
    # 缓存不存在时，优先走 DataHub（禁止直连 tushare）
    print("  构建申万三级行业映射 (DataHub)...")
    stock_industry = {}

    if dh is not None:
        try:
            m = dh.get_sw_industry_map('L3')
            if m:
                stock_industry = m
                print(f"  ✓ 行业映射构建完成 ({len(stock_industry)} 只股票) [DataHub]")
        except Exception as e:
            print(f"  ⚠ DataHub 行业映射失败: {e}")

    # 兜底：DataHub 完全不可用时才直连 tushare
    if not stock_industry:
        print("  ↻ DataHub 无数据，回退 tushare...")
        try:
            import tushare as ts
            token = _secret('tushare.token', 'TUSHARE_TOKEN')
            if not token:
                raise RuntimeError("TuShare token 未配置（TUSHARE_TOKEN / tushare.token）")
            ts.set_token(token)
            pro = ts.pro_api()

            time.sleep(0.5)
            df_l3 = pro.index_classify(level='L3', src='SW2021')

            for _, row in df_l3.iterrows():
                idx_code = row['index_code']
                idx_name = row['industry_name']
                try:
                    time.sleep(0.3)
                    members = pro.index_member(index_code=idx_code)
                    active = members[members['is_new'] == 'Y']
                    for _, m in active.iterrows():
                        stock_industry[m['con_code']] = idx_name
                except Exception:
                    continue
            print(f"  ✓ 行业映射构建完成 ({len(stock_industry)} 只股票) [tushare]")
        except Exception as e:
            print(f"  ✗ 行业映射构建失败: {e}")

    # 保存缓存
    if stock_industry:
        try:
            os.makedirs(os.path.dirname(map_path), exist_ok=True)
            with open(map_path, 'w', encoding='utf-8') as f:
                json.dump(stock_industry, f, ensure_ascii=False)
        except Exception as e:
            print(f"  ⚠ 缓存写入失败: {e}")

    return stock_industry


def code_to_ts(code: str) -> str:
    """将 sh600000/sz000612 格式转换为 600000.SH/000612.SZ 格式"""
    if len(code) <= 2:
        return code
    pure = code[2:]
    market = code[:2]
    if market == 'sh':
        return f"{pure}.SH"
    elif market == 'sz':
        return f"{pure}.SZ"
    return code


def get_stock_industry(code: str, industry_map: Dict[str, str]) -> str:
    """获取个股所属行业（申万三级），如果找不到则返回空字符串"""
    ts_code = code_to_ts(code)
    return industry_map.get(ts_code, '')




def get_market_overview() -> dict:
    """
    获取市场概览数据：两市成交额、涨跌家数、涨停/跌停/炸板
    用于在报告头部展示市场整体情况
    """
    overview = {
        'sh_amount': None,      # 上证成交额(亿)
        'sz_amount': None,      # 深证成交额(亿)
        'total_amount': None,   # 两市合计成交额(亿)
        'up_count': None,       # 上涨家数
        'down_count': None,     # 下跌家数
        'flat_count': None,     # 平盘家数
        'zt_count': None,       # 涨停数
        'dt_count': None,       # 跌停数
        'zb_count': None,       # 炸板数
        'real_zt': None,        # 真实涨停
        'real_dt': None,        # 真实跌停
        'activity': None,       # 活跃度
        'data_time': None,      # 数据统计时间
    }

    # === 两市成交额（新浪行情接口） ===
    try:
        url = 'https://hq.sinajs.cn/list=sh000001,sz399001'
        headers = {'Referer': 'https://finance.sina.com.cn'}
        r = requests.get(url, headers=headers, timeout=10)
        lines = r.text.strip().split('\n')
        for line in lines:
            parts = line.split(',')
            if len(parts) > 9:
                name = parts[0].split('"')[-1]
                amount_yuan = float(parts[9])
                amount_yi = amount_yuan / 1e8
                if '上证指数' in name:
                    overview['sh_amount'] = round(amount_yi, 1)
                elif '深证成指' in name:
                    overview['sz_amount'] = round(amount_yi, 1)
        if overview['sh_amount'] and overview['sz_amount']:
            overview['total_amount'] = round(overview['sh_amount'] + overview['sz_amount'], 1)
    except Exception as e:
        print(f"  成交额获取失败: {e}")

    # === 涨跌家数 + 涨停/跌停（乐股网） ===
    try:
        df = dh.get_market_activity_legu() if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError("DataHub 市场活跃度为空")
        for _, row in df.iterrows():
            item = str(row['item']).strip()
            val = row['value']
            if item == '上涨':
                overview['up_count'] = int(val)
            elif item == '下跌':
                overview['down_count'] = int(val)
            elif item == '平盘':
                overview['flat_count'] = int(val)
            elif item == '涨停':
                overview['zt_count'] = int(val)
            elif item == '跌停':
                overview['dt_count'] = int(val)
            elif item == '真实涨停':
                overview['real_zt'] = int(val)
            elif item == '真实跌停':
                overview['real_dt'] = int(val)
            elif item == '活跃度':
                overview['activity'] = str(val)
            elif item == '统计日期':
                overview['data_time'] = str(val)
    except Exception as e:
        print(f"  涨跌统计获取失败: {e}")

    # === 炸板数 ===
    try:
        date_compact = datetime.now().strftime('%Y%m%d')
        zb_df = dh.get_limit_pool('zb', trade_date=date_compact) if dh else None
        if zb_df is None or len(zb_df) == 0:
            raise RuntimeError("DataHub 炸板股池为空")
        overview['zb_count'] = len(zb_df)
    except Exception as e:
        print(f"  炸板数据获取失败: {e}")

    return overview


def format_market_overview(overview: dict) -> str:
    """格式化市场概览为 Markdown 文本"""
    lines = []
    lines.append("## 市场概览")
    lines.append("")

    # 成交额
    if overview.get('total_amount'):
        sh_str = f"{overview['sh_amount']}亿" if overview.get('sh_amount') else "N/A"
        sz_str = f"{overview['sz_amount']}亿" if overview.get('sz_amount') else "N/A"
        lines.append(f"- **两市成交额**: {overview['total_amount']}亿 (沪市 {sh_str} / 深市 {sz_str})")
    else:
        lines.append("- **两市成交额**: 数据暂缺")

    # 涨跌家数
    if overview.get('up_count') is not None and overview.get('down_count') is not None:
        flat_str = f"{overview['flat_count']}" if overview.get('flat_count') is not None else "N/A"
        lines.append(f"- **涨跌家数**: 涨 {overview['up_count']} / 跌 {overview['down_count']} / 平 {flat_str}")
    else:
        lines.append("- **涨跌家数**: 数据暂缺")

    # 涨停/跌停
    zt_parts = []
    if overview.get('zt_count') is not None:
        real_str = f"(真实{overview['real_zt']})" if overview.get('real_zt') is not None else ""
        zt_parts.append(f"涨停 {overview['zt_count']} {real_str}")
    if overview.get('dt_count') is not None:
        real_str = f"(真实{overview['real_dt']})" if overview.get('real_dt') is not None else ""
        zt_parts.append(f"跌停 {overview['dt_count']} {real_str}")
    if overview.get('zb_count') is not None:
        zt_parts.append(f"炸板 {overview['zb_count']}")

    if zt_parts:
        lines.append(f"- **涨跌停**: {' / '.join(zt_parts)}")
    else:
        lines.append("- **涨跌停**: 数据暂缺")

    # 活跃度
    if overview.get('activity'):
        lines.append(f"- **活跃度**: {overview['activity']}")

    # 统计时间
    if overview.get('data_time'):
        lines.append(f"- **数据时间**: {overview['data_time']}")

    lines.append("")
    return '\n'.join(lines)


def format_report(predictions: pd.DataFrame, mode: str, top_n: int = 20,
                  stock_names: Dict[str, str] = None,
                  data_issues: List[str] = None,
                  data_stats: Dict = None,
                  industry_map: Dict[str, str] = None) -> str:
    """
    格式化报告，包含股票名称和数据完整性说明
    """
    now = datetime.now()
    mode_cn = "早盘" if mode == "morning" else "午盘"
    
    lines = []
    lines.append(f"# 集成学习选股 - {mode_cn}推荐")
    lines.append("*消息来自于 coze.codex*")
    lines.append(f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    
    # 市场概览
    print("  获取市场概览数据...")
    overview = get_market_overview()
    lines.append(format_market_overview(overview))
    
    lines.append(f"**模型**: XGBoost + LightGBM + CatBoost + LR (Stacking)")
    lines.append(f"**候选股票**: {len(predictions)} 只")
    lines.append(f"**股票池**: 沪深300 + 中证500 + 中证1000 + 科创板")
    lines.append("")
    
    top_preds = predictions.head(top_n)
    
    # 概率解读说明
    if len(predictions) > 0 and 'percentile' in predictions.columns:
        top1_prob = predictions.iloc[0]['pred_prob']
        top1_pct = predictions.iloc[0].get('percentile', 99)
        top20_prob = top_preds.iloc[-1]['pred_prob']
        lines.append(f"> **概率怎么看**：概率 = 模型预测次日涨停的可能性，越高越好。")
        lines.append(f"> 每只股票后面标注的「超过X%」= 该概率在全市场{len(predictions)}只股票中的排名百分位。")
        lines.append(f"> 今日TOP1概率 {top1_prob*100:.1f}%（超过{top1_pct:.0f}%的股票），TOP{top_n}概率 {top20_prob*100:.1f}%。")
        lines.append("")
    
    lines.append(f"## TOP{top_n} 推荐股票")
    lines.append("")
    
    for i, row in top_preds.iterrows():
        code = row['code']
        pure_code = code[2:] if len(code) > 2 else code
        
        # 获取股票名称
        name = ""
        if stock_names and code in stock_names:
            name = stock_names[code]
        elif 'name' in row and pd.notna(row.get('name')):
            name = row['name']
        
        prob = row['pred_prob']
        close = row['close']
        prob_pct = f"{prob*100:.2f}%"
        
        # 获取行业
        industry = ""
        if industry_map:
            industry = get_stock_industry(code, industry_map)
        
        # 分位数信息
        pct = row.get('percentile', None)
        pct_str = f"，超过{pct:.0f}%" if pct is not None else ""
        
        if name:
            line = f"**{i+1}. {pure_code} {name}**"
            if industry:
                line += f" [{industry}]"
            line += f" - 涨停概率{prob_pct}{pct_str}，收盘¥{close:.2f}"
        else:
            line = f"**{i+1}. {pure_code}**"
            if industry:
                line += f" [{industry}]"
            line += f" - 涨停概率{prob_pct}{pct_str}，收盘¥{close:.2f}"
        
        if mode == 'noon' and 'change_pct' in row:
            change = row.get('change_pct', 0)
            change_str = f"+{change:.2f}%" if change > 0 else f"{change:.2f}%"
            line += f", 涨跌 {change_str}"
        
        lines.append(line)
    
    # 数据完整性说明
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 数据完整性说明")
    lines.append("")
    
    if data_stats:
        total_s = (data_stats.get('primary_success', 0) + 
                   data_stats.get('tushare_success', 0) + 
                   data_stats.get('crawler_success', 0))
        total_f = (data_stats.get('primary_failure', 0) + 
                   data_stats.get('tushare_failure', 0) + 
                   data_stats.get('crawler_failure', 0))
        total = total_s + total_f
        if total > 0:
            rate = total_s / total * 100
            lines.append(f"- **数据获取成功率**: {rate:.1f}% ({total_s}/{total})")
            lines.append(f"- **数据源使用**: 腾讯行情 {data_stats.get('primary_success',0)} 次, "
                        f"TuShare {data_stats.get('tushare_success',0)} 次, "
                        f"腾讯爬虫 {data_stats.get('crawler_success',0)} 次")
            lines.append(f"- **数据源切换**: {data_stats.get('switch_count',0)} 次")
    
    if data_issues and len(data_issues) > 0:
        lines.append(f"- **数据异常**: 共 {len(data_issues)} 条")
        for issue in data_issues[:5]:
            lines.append(f"  - {issue}")
        if len(data_issues) > 5:
            lines.append(f"  - ... 还有 {len(data_issues)-5} 条")
    else:
        lines.append("- **数据状态**: 所有数据源正常，无异常")
    
    lines.append("")
    lines.append("---")
    lines.append("*注: 概率为模型预测的涨停可能性，仅供参考，不构成投资建议。"
                 "数据来源：沪深300/中证500/中证1000/科创板指数成分股*")
    
    return "\n".join(lines)


def send_to_feishu(report_content: str, mode: str) -> bool:
    """发送报告到飞书"""
    env_webhook = os.environ.get('FEISHU_WEBHOOK_URL', '')
    webhook = env_webhook if env_webhook else FEISHU_WEBHOOK_URL
    
    if not webhook:
        print("未配置飞书 Webhook，跳过发送")
        return False
    
    mode_cn = "早盘" if mode == "morning" else "午盘"
    title = f"集成学习选股 - {mode_cn}推荐"
    
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": report_content}
            ]
        }
    }
    
    try:
        response = requests.post(webhook, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                print("✓ 报告已发送到飞书")
                return True
            else:
                print(f"✗ 飞书发送失败: {result}")
                return False
        else:
            print(f"✗ 飞书请求失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ 飞书发送异常: {e}")
        return False


def save_report(predictions: pd.DataFrame, mode: str,
                stock_names: Dict[str, str] = None,
                industry_map: Dict[str, str] = None):
    """保存报告到文件"""
    output_dir = './output'
    os.makedirs(output_dir, exist_ok=True)
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 保存 CSV（包含名称）
    csv_path = f"{output_dir}/daily_top20_{today}_{mode}.csv"
    save_df = predictions.head(20).copy()
    if stock_names:
        save_df.insert(1, 'name', save_df['code'].map(stock_names).fillna(''))
    if industry_map:
        save_df['industry'] = save_df['code'].apply(lambda c: get_stock_industry(c, industry_map))
    save_df.to_csv(csv_path, index=False)
    print(f"✓ 报告已保存: {csv_path}")
    
    # 保存 JSON
    json_path = f"{output_dir}/daily_top20_{today}_{mode}.json"
    top20 = save_df.head(20)
    json_data = {
        'date': today,
        'mode': mode,
        'generated_at': datetime.now().isoformat(),
        'total_stocks': len(predictions),
        'top20': json.loads(top20.to_json(orient='records', date_format='iso', force_ascii=False))
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"✓ 报告已保存: {json_path}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='集成学习选股模型 - 每日运行器')
    parser.add_argument('--mode', type=str, default='morning',
                        choices=['morning', 'noon'],
                        help='运行模式: morning(早盘,9:00) 或 noon(午盘,12:00)')
    parser.add_argument('--top-n', type=int, default=20,
                        help='推荐股票数量 (默认: 20)')
    parser.add_argument('--no-send', action='store_true',
                        help='不发送到飞书')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print(f"集成学习选股模型 - 每日运行器 ({args.mode})")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"模式: {'早盘 (基于昨日收盘)' if args.mode == 'morning' else '午盘 (基于实时快照)'}")
    print(f"数据源: 腾讯行情 → TuShare → Coze Skill → 腾讯爬虫 (四级回退)")
    print()
    
    # 1. 加载模型
    print("[1/5] 加载模型...")
    model = load_latest_model()
    if model is None:
        print("✗ 模型加载失败，退出")
        sys.exit(1)
    
    # 2. 获取数据（使用统一获取器，三级回退）
    print("\n[2/5] 获取数据...")
    fetcher = UnifiedDataFetcher(start_date='2023-01-01')
    
    stock_pool = fetcher.get_full_stock_pool()
    print(f"\n  股票池: {len(stock_pool)} 只")
    
    # 获取历史数据
    stock_data = fetcher.fetch_all_data(stock_pool)
    
    if not stock_data:
        print("✗ 数据获取失败，退出")
        sys.exit(1)
    
    # 3. 获取股票名称
    print("\n[3/5] 获取股票名称...")
    all_codes = list(stock_data.keys())
    stock_names = fetcher.get_stock_names_batch(all_codes)
    print(f"  ✓ 获取到 {len(stock_names)} 只股票名称")
    
    # 3.5 加载行业映射
    print("\n  加载行业映射...")
    industry_map = load_industry_map()
    
    # 4. 预测
    print("\n[4/5] 生成预测...")
    feature_cols = model.feature_cols if hasattr(model, 'feature_cols') else []
    
    if not feature_cols:
        first_code = list(stock_data.keys())[0]
        feature_cols = [c for c in stock_data[first_code].columns
                       if c not in ['open', 'high', 'low', 'close', 'volume', 'amount', 'date']]
    
    predictions = predict_today(model, stock_data, feature_cols, args.mode)
    
    if len(predictions) == 0:
        print("✗ 预测失败，没有产生结果")
        sys.exit(1)
    
    # 中午模式：合并实时数据
    if args.mode == 'noon':
        print("\n  合并实时行情数据...")
        snapshot = get_realtime_snapshot()
        if snapshot is not None:
            predictions = merge_with_realtime(predictions, snapshot)
    
    # 5. 输出报告
    print("\n[5/5] 生成报告...")
    data_issues = fetcher.get_data_issues()
    data_stats = fetcher.get_stats()
    
    report = format_report(predictions, args.mode, args.top_n,
                          stock_names=stock_names,
                          data_issues=data_issues,
                          data_stats=data_stats,
                          industry_map=industry_map)
    
    # 早盘模式：嵌入昨日预测复盘
    if args.mode == 'morning':
        print("\n  生成昨日预测复盘...")
        try:
            review_data = review_predictions()
            if review_data:
                review_brief = format_review_brief(review_data)
                report = review_brief + "\n" + report
                print(f"  ✓ 复盘已嵌入报告 (命中 {review_data.get('up_count', review_data.get('hit_count', 0))}/{review_data['total']})")
            else:
                print("  ⚠ 无历史预测数据可复盘")
        except Exception as e:
            print(f"  ⚠ 复盘生成失败: {e}")
    
    print("\n" + report)
    
    # 保存报告
    save_report(predictions, args.mode, stock_names=stock_names,
                 industry_map=industry_map)
    
    # 投资组合跟踪（合并到同一份报告）
    print("\n" + "=" * 60)
    print("更新投资组合...")
    print("=" * 60)
    try:
        tracker = PortfolioTracker()
        today = datetime.now().strftime('%Y-%m-%d')
        tracker.run_daily_task(date=today, mode=args.mode)
        portfolio_report = tracker.generate_report()
        report = report + "\n\n" + portfolio_report
    except Exception as e:
        print(f"⚠ 投资组合更新失败: {e}")
    
    # 持仓观察跟踪（星标/叉号标记）
    print("\n" + "=" * 60)
    print("持仓观察跟踪...")
    print("=" * 60)
    try:
        wl_tracker = WatchlistTracker()
        wl_data = wl_tracker.update_watchlist()
        if wl_data:
            wl_report = wl_tracker.generate_report(wl_data)
            report = report + "\n\n" + wl_report
    except Exception as e:
        print(f"⚠ 持仓观察更新失败: {e}")
    
    # 发送到飞书（一份报告）
    if not args.no_send:
        send_to_feishu(report, args.mode)
    
    print("\n" + "=" * 60)
    print("✓ 运行完成!")
    print("=" * 60)
    
    return report


if __name__ == '__main__':
    main()
