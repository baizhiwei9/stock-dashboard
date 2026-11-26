#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据获取模块 - DataHub 统一入口（主）+ 腾讯行情 API（兜底）
支持沪深300+中证500+中证1000+科创板+创业板
支持批量获取股票名称、数据缺失追踪

数据源约定（2026-11-26）
────────────────────────
业务代码一律先走 DataHub（``data_hub``），腾讯 / AKShare 只作为兜底。
理由：直连各家接口会各自限流、各自缺列、各自单位，同一个指标在不同源上
算出来不是一个东西；DataHub 内部已经做了多源并行 + 列补齐 + 单位统一。

DataHub 口径 → 本模块口径（训练模型按本模块口径产出，转换必须保持）
    ts_code  600000.SH   →  sh600000
    trade_date 20261125  →  DatetimeIndex 2026-11-25
    vol（手）             →  volume（手，同口径）
    amount（千元）        →  amount = 千元 × 10 = volume × close（旧口径：手·元/100）
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

import requests
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import time
import json
import os
import sys

# ── DataHub 统一取数入口（禁止直连 akshare 取数）──
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
except Exception as _e:  # noqa: BLE001
    dh = None
    print(f"[DataHub] 导入失败，将回退直连: {_e}")

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


class DataFetcherTencent:
    """使用腾讯行情API获取数据"""
    
    def __init__(self, start_date: str = '2023-01-01', end_date: str = None):
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        self.base_url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        self.name_cache = {}
        self.data_issues = []
    
    # ── 代码 / 单位换算（DataHub ↔ 本模块口径）──────────────────────────
    @staticmethod
    def _to_ts_code(code: str) -> str:
        """'sh600000' / '600000' → '600000.SH'（DataHub / tushare 口径）。"""
        c = str(code).strip().lower()
        if '.' in c:
            return c.upper()
        if c.startswith(('sh', 'sz', 'bj')):
            return f"{c[2:]}.{c[:2].upper()}"
        return f"{c}.SH" if c.startswith('6') else f"{c}.SZ"

    @staticmethod
    def _to_tencent_code(ts_code: str) -> str:
        """'600000.SH' → 'sh600000'（腾讯行情口径）。"""
        c = str(ts_code).strip().upper()
        if '.' in c:
            num, mkt = c.split('.', 1)
            return ('sh' if mkt == 'SH' else 'sz') + num
        return DataFetcherTencent._convert_code(c)

    @staticmethod
    def _from_datahub(df: pd.DataFrame, code: str) -> Optional[pd.DataFrame]:
        """DataHub 日线 → 本模块统一口径（index=date，列 open/high/low/close/volume/amount）。

        单位坑：DataHub 的 vol/amount 并不完全统一 ——
          · akshare / tushare：vol=手，amount=千元  → amount/(vol*close) ≈ 0.1
          · baostock：        vol=股，amount=元    → amount/(vol*close) ≈ 1.0
        训练模型吃的是腾讯口径（vol=手，amount=手·元=元/100）。这里用
        amount/(vol*close) 的中位比例识别来源口径再换算，识别不了就退回
        amount=vol*close（腾讯老代码的近似口径），并在 attrs 里留下记录，
        不静默把量纲不同的数据喂给模型。
        """
        if df is None or len(df) == 0:
            return None
        d = df.copy()
        if 'trade_date' in d.columns:
            d['date'] = pd.to_datetime(d['trade_date'].astype(str), format='%Y%m%d', errors='coerce')
        elif 'date' in d.columns:
            d['date'] = pd.to_datetime(d['date'], errors='coerce')
        else:
            return None
        ren = {'vol': 'volume'}
        d = d.rename(columns=ren)
        need = ['date', 'open', 'high', 'low', 'close', 'volume']
        if any(c not in d.columns for c in need):
            return None
        for col in ['open', 'high', 'low', 'close', 'volume']:
            d[col] = pd.to_numeric(d[col], errors='coerce')
        unit = 'unknown'
        if 'amount' in d.columns:
            d['amount'] = pd.to_numeric(d['amount'], errors='coerce')
            base = (d['volume'] * d['close']).replace(0, np.nan)
            ratio = (d['amount'] / base).replace([np.inf, -np.inf], np.nan).median()
            if ratio == ratio and 0.02 <= ratio <= 0.2:
                # 手 / 千元（akshare、tushare）：千元 → 手·元 需 ×10
                d['amount'] = d['amount'] * 10
                unit = 'hand_kcny'
            elif ratio == ratio and 0.5 <= ratio <= 2.0:
                # 股 / 元（baostock）：股 → 手、元 → 手·元，各除以 100
                d['volume'] = d['volume'] / 100
                d['amount'] = d['amount'] / 100
                unit = 'share_cny'
            else:
                d['amount'] = d['volume'] * d['close']
                unit = 'derived'
        else:
            d['amount'] = d['volume'] * d['close']
            unit = 'derived' 
        d = d.dropna(subset=['date'])
        d = d.set_index('date').sort_index()
        d = d[(d['volume'] > 0) & (d['close'] > 0)]
        d = d[~d.index.duplicated(keep='last')]
        d.attrs['source'] = 'datahub'
        d.attrs['code'] = code
        d.attrs['unit'] = unit
        return d[['open', 'high', 'low', 'close', 'volume', 'amount']] if len(d) else None

    def get_full_stock_pool(self) -> List[str]:
        """
        获取完整股票池：沪深300 + 中证500 + 中证1000 + 科创板（+ 成分里的创业板）
        多源尝试：DataHub → AKShare → 腾讯指数成分股接口
        """
        all_codes = set()
        pool_issues = []
        
        index_configs = [
            ('000300', '沪深300'),
            ('000905', '中证500'),
            ('000852', '中证1000'),
        ]
        
        # 第一优先：DataHub（内部已做多源回退与缓存）
        dh_ok = False
        if dh is not None:
            try:
                for symbol, name in index_configs:
                    codes = [self._to_tencent_code(c) for c in (dh.get_index_components(symbol) or [])]
                    if codes:
                        print(f"  {name}: {len(codes)} 只 [DataHub]")
                        all_codes.update(codes)
                    else:
                        pool_issues.append(f"{name}成分股获取失败")
                star_codes = self._get_star_market_stocks_datahub()
                if star_codes:
                    print(f"  科创板(688): {len(star_codes)} 只 [DataHub]")
                    all_codes.update(star_codes)
                dh_ok = len(all_codes) > 0
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ DataHub 股票池获取失败，回退 AKShare/腾讯: {e}")
                all_codes.clear()
                dh_ok = False
        
        if not dh_ok:
            for symbol, name in index_configs:
                codes = self._get_index_components(symbol, name)
                if codes:
                    print(f"  {name}: {len(codes)} 只")
                    all_codes.update(codes)
                else:
                    pool_issues.append(f"{name}成分股获取失败")
                    print(f"  {name}: 获取失败，尝试备用方案...")
                    codes = self._get_index_components_tencent(symbol, name)
                    if codes:
                        print(f"  {name}(腾讯备用): {len(codes)} 只")
                        all_codes.update(codes)
            
            # 科创板：通过代码前缀 688 从指数成分中筛选 + 腾讯接口补充
            star_codes = self._get_star_market_stocks()
            if star_codes:
                print(f"  科创板(688): {len(star_codes)} 只")
                all_codes.update(star_codes)
            else:
                pool_issues.append("科创板列表获取失败")
        
        result = sorted(all_codes)
        
        star_count = sum(1 for c in result if c.startswith('sh688'))
        chinext_count = sum(1 for c in result if c.startswith('sz300') or c.startswith('sz301'))
        print(f"  科创板(688): {star_count} 只")
        print(f"  创业板(300/301): {chinext_count} 只")
        print(f"  合计（去重）: {len(result)} 只")
        
        if pool_issues:
            self.data_issues.extend(pool_issues)
        
        return result
    
    def _get_star_market_stocks_datahub(self) -> List[str]:
        """科创板（688）列表：DataHub 全市场清单按代码段筛。"""
        if dh is None:
            return []
        try:
            lst = dh.get_stock_list()
            if lst is None or len(lst) == 0:
                return []
            codes = lst[lst['ts_code'].astype(str).str.startswith('688')]['ts_code'].tolist()
            return [self._to_tencent_code(c) for c in codes]
        except Exception as e:  # noqa: BLE001
            print(f"  DataHub 科创板列表失败: {e}")
            return []

    def _get_index_components(self, symbol: str, name: str) -> List[str]:
        """通过 AKShare 获取指数成分股"""
        if not AKSHARE_AVAILABLE:
            return []
        try:
            df = ak.index_stock_cons(symbol=symbol)
            codes = [self._convert_code(c) for c in df['品种代码'].astype(str)]
            return codes
        except Exception as e:
            print(f"  AKShare获取{name}失败: {e}")
            return []
    
    def _get_index_components_tencent(self, symbol: str, name: str) -> List[str]:
        """通过腾讯接口获取指数成分股（备用）"""
        try:
            tencent_symbol = symbol
            if symbol == '000300':
                tencent_symbol = '000300'
            elif symbol == '000905':
                tencent_symbol = '000905'
            elif symbol == '000852':
                tencent_symbol = '000852'
            
            url = f'https://web.sqt.gtimg.cn/q=sh{tencent_symbol}'
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                return []
            
            if AKSHARE_AVAILABLE:
                df = ak.index_stock_cons(symbol=symbol)
                return [self._convert_code(c) for c in df['品种代码'].astype(str)]
            return []
        except:
            return []
    
    def _get_star_market_stocks(self) -> List[str]:
        """获取科创板股票列表（三级回退：AKShare → 腾讯行情扫描 → 东方财富列表）"""
        # 第一级：AKShare
        if AKSHARE_AVAILABLE:
            try:
                df = ak.stock_kc_a_spot_em()
                codes = df['代码'].tolist()
                result = [self._convert_code(str(c).zfill(6)) for c in codes if str(c).startswith('688')]
                if result:
                    return result
            except Exception as e:
                print(f"  AKShare获取科创板列表失败: {e}")
        
        # 第二级：通过腾讯行情批量扫描 688 代码段
        try:
            print("  尝试腾讯行情扫描科创板(688)...")
            star_codes = []
            # 科创板代码范围: 688001 ~ 689999，按每批50个扫描
            for start in range(688001, 689000, 50):
                batch = [f"sh{i:06d}" for i in range(start, min(start + 50, 689000))]
                code_str = ','.join(batch)
                try:
                    url = f'https://qt.gtimg.cn/q={code_str}'
                    resp = requests.get(url, timeout=10)
                    resp.encoding = 'gbk'
                    for line in resp.text.strip().split('\n'):
                        if '~' not in line:
                            continue
                        parts = line.split('~')
                        if len(parts) > 5 and parts[3] and float(parts[3] or 0) > 0:
                            code_raw = line.split('=')[0].replace('v_', '').strip()
                            if code_raw.startswith('sh688'):
                                star_codes.append(code_raw)
                except Exception:
                    continue
                time.sleep(0.05)
            
            if star_codes:
                print(f"  腾讯行情扫描到科创板: {len(star_codes)} 只")
                return star_codes
        except Exception as e:
            print(f"  腾讯行情扫描科创板失败: {e}")
        
        # 第三级：通过东方财富指数成分获取科创板50成分股
        try:
            print("  尝试获取科创板50成分股...")
            if AKSHARE_AVAILABLE:
                df = ak.index_stock_cons(symbol='000688')
                codes = [self._convert_code(str(c).zfill(6)) for c in df['品种代码'].astype(str)]
                star_only = [c for c in codes if c.startswith('sh688')]
                if star_only:
                    print(f"  科创板50成分股: {len(star_only)} 只")
                    return star_only
        except Exception as e:
            print(f"  科创板50成分股获取失败: {e}")
        
        return []
    
    def get_stock_names_batch(self, codes: List[str], batch_size: int = 50) -> Dict[str, str]:
        """
        批量获取股票名称（通过腾讯实时行情接口）
        
        Args:
            codes: 股票代码列表，如 ['sh600000', 'sz000001']
            batch_size: 每批查询数量
            
        Returns:
            dict: {code: name}
        """
        names = {}
        
        # 第一优先：DataHub 全市场清单（一次拿全，不逐批打腾讯接口）
        if dh is not None:
            try:
                if not getattr(self, '_name_map', None):
                    lst = dh.get_stock_list()
                    if lst is not None and len(lst) > 0:
                        self._name_map = {
                            self._to_tencent_code(r['ts_code']): str(r['name'])
                            for _, r in lst.iterrows()
                        }
                for code in codes:
                    nm = (self._name_map or {}).get(code)
                    if nm:
                        names[code] = nm
                        self.name_cache[code] = nm
                if len(names) == len(codes):
                    return names
            except Exception as e:  # noqa: BLE001
                print(f"  DataHub 股票名称失败，回退腾讯: {e}")
        
        missing = [c for c in codes if c not in names]
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            query = ','.join(batch)
            
            try:
                url = f'https://qt.gtimg.cn/q={query}'
                r = requests.get(url, timeout=10)
                
                for line in r.text.strip().split(';'):
                    line = line.strip()
                    if not line or '~' not in line:
                        continue
                    parts = line.split('~')
                    if len(parts) > 5:
                        stock_name = parts[1]
                        stock_code_raw = parts[2]
                        
                        for code in batch:
                            if code[2:] == stock_code_raw:
                                names[code] = stock_name
                                self.name_cache[code] = stock_name
                                break
                
                time.sleep(0.1)
            except Exception as e:
                print(f"  批量获取名称失败(batch {i}): {e}")
        
        return names
    
    def get_stock_name(self, code: str) -> str:
        """获取单只股票名称"""
        if code in self.name_cache:
            return self.name_cache[code]
        
        names = self.get_stock_names_batch([code])
        return names.get(code, code[2:])
    
    def _convert_code(self, code: str) -> str:
        """转换股票代码: '600000' -> 'sh600000'"""
        code = str(code).zfill(6)
        if code.startswith('6'):
            return f'sh{code}'
        else:
            return f'sz{code}'
    
    @staticmethod
    def get_stock_board(code: str) -> str:
        """判断所属板块"""
        pure_code = code[2:] if len(code) > 2 else code
        if pure_code.startswith('688'):
            return 'star'
        elif pure_code.startswith('300') or pure_code.startswith('301'):
            return 'chinext'
        else:
            return 'main'
    
    def get_stock_data(self, code: str) -> Optional[pd.DataFrame]:
        """获取单只股票日线数据（前复权）：DataHub 优先，腾讯接口兜底。"""
        # 第一优先：DataHub（多源并行、列/单位已统一）
        if dh is not None:
            try:
                raw = dh.get_stock_daily(
                    ts_code=self._to_ts_code(code),
                    start_date=self.start_date, end_date=self.end_date, adjust='qfq')
                df = self._from_datahub(raw, code)
                if df is not None and len(df) > 0:
                    return df
            except Exception as e:  # noqa: BLE001
                print(f"  DataHub {code} 取数失败，回退腾讯: {e}")
        
        try:
            params = {
                'param': f'{code},day,{self.start_date},{self.end_date},1000,qfq'
            }
            
            r = requests.get(self.base_url, params=params, timeout=10)
            data = r.json()
            
            if 'data' not in data or code not in data['data']:
                return None
            
            stock_data = data['data'][code]
            
            if 'qfqday' in stock_data:
                days = stock_data['qfqday']
            elif 'day' in stock_data:
                days = stock_data['day']
            else:
                return None
            
            if not days or len(days) == 0:
                return None
            
            # 每行可能有6列或7列（7列含分红信息），只取前6列
            days_clean = [row[:6] for row in days]
            df = pd.DataFrame(days_clean, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
            
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df['amount'] = df['volume'] * df['close']
            
            df = df[df['volume'] > 0]
            df = df[df['close'] > 0]
            
            return df
            
        except Exception:
            return None
    
    def _fetch_batch_datahub(self, stock_pool: List[str]) -> Dict[str, pd.DataFrame]:
        """DataHub 批量取数（一次调用内部多源并行，比逐只请求快一个数量级）。"""
        if dh is None or not stock_pool:
            return {}
        out: Dict[str, pd.DataFrame] = {}
        try:
            raw = dh.get_stock_daily_batch(
                [self._to_ts_code(c) for c in stock_pool],
                self.start_date, self.end_date)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ DataHub 批量取数失败，逐只回退: {e}")
            return {}
        if raw is None or len(raw) == 0 or 'ts_code' not in raw.columns:
            return {}
        for ts_code, grp in raw.groupby('ts_code'):
            code = self._to_tencent_code(ts_code)
            df = self._from_datahub(grp, code)
            if df is not None and len(df) > 0:
                out[code] = df
        print(f"  DataHub 批量取数：{len(out)}/{len(stock_pool)} 只")
        return out

    def fetch_all_data(self, stock_pool: List[str]) -> Dict[str, pd.DataFrame]:
        """批量获取股票数据：DataHub 批量优先，缺的逐只回退，追踪失败"""
        from tqdm import tqdm
        
        print(f"\n开始获取 {len(stock_pool)} 只股票数据...")
        
        all_data = self._fetch_batch_datahub(stock_pool)
        pending = [c for c in stock_pool if c not in all_data]
        if pending:
            print(f"  DataHub 未覆盖 {len(pending)} 只，逐只回退腾讯…")
        failed_codes = []
        for code in tqdm(pending, desc="腾讯兜底"):
            df = self.get_stock_data(code)
            if df is not None and len(df) > 100:
                all_data[code] = df
            else:
                failed_codes.append(code)
        
        print(f"✓ 成功获取 {len(all_data)} 只股票数据")
        if failed_codes:
            print(f"  失败: {len(failed_codes)} 只")
            self.data_issues.append(f"日线数据获取失败 {len(failed_codes)} 只")
        
        return all_data
    
    def get_data_issues(self) -> List[str]:
        """获取数据问题列表"""
        return self.data_issues.copy()
    
    def clear_issues(self):
        """清除数据问题记录"""
        self.data_issues = []


if __name__ == '__main__':
    fetcher = DataFetcherTencent(start_date='2023-01-01')
    
    print("="*60)
    print("测试数据获取")
    print("="*60)
    
    stock_pool = fetcher.get_full_stock_pool()
    print(f"\n股票池大小: {len(stock_pool)}")
    
    test_codes = stock_pool[:5]
    print(f"\n测试获取名称...")
    names = fetcher.get_stock_names_batch(test_codes)
    for code, name in names.items():
        print(f"  {code}: {name}")
    
    print(f"\n测试获取日线数据...")
    for code in test_codes:
        df = fetcher.get_stock_data(code)
        name = names.get(code, '?')
        if df is not None:
            print(f"  {code}({name}): {len(df)} 天, 最新收盘 {df['close'].iloc[-1]:.2f}")
        else:
            print(f"  {code}({name}): 获取失败")
