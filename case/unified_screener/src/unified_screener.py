#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股四选股器综合选股器 v2.0
============================
合并四大选股策略为统一报告，按策略交集打星标注，并输出行业/概念加权分布。

策略列表：
  S1 - 缠论主升浪（v8 三档降级，动量）
  S2 - 成长股选股器（基本面成长）
  S3 - A股多时段强势股筛选器（动量多因子）
  S4 - MAD选股器（MA20>MA200 且 MA20>MA40 + MA40斜率>0 + 周涨>0 + PEG双口径任一≤1.5）

打星规则：
  ★★★★ = 4个选股器共同选出（最强共振，飞书置顶）
  ★★★ = 3个选股器共同选出
  ★★  = 2个选股器共同选出
  ★   = 仅1个选股器选出

全流程纯 Python，零 LLM、零 Token。

输出：
  - 统一报告 .md（保存到缠论主升浪/outputs/股票筛选/）
  - 飞书群推送

使用：python3.13 unified_screener.py
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


import os
import sys
import subprocess
import glob
import re
from datetime import datetime

def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import (  # noqa: E402
    feishu_webhook as _feishu_webhook,
    smtp_password as _smtp_password,
    tushare_token as _tushare_token,
)
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


# ===================== 路径配置 =====================
BASE = _cp('/Coze/Drive/蓁宝/所有对话/主对话')
def _latest_trade_date():
    """最近交易日（YYYY-MM-DD）。优先 tushare trade_cal，失败则退化为跳过周末。"""
    from datetime import timedelta
    now = datetime.now()
    try:
        _dates = dh.get_trade_dates(
            (now - timedelta(days=20)).strftime('%Y%m%d'),
            now.strftime('%Y%m%d')) if dh else None
        if _dates:
            _d = sorted(_dates)[-1]
            return '%s-%s-%s' % (_d[:4], _d[4:6], _d[6:])
    except Exception:
        pass
    d = now
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


_args = [a for a in sys.argv[1:] if not a.startswith('--')]
TODAY = _args[0] if _args else _latest_trade_date()

CHANLUN_DIR   = os.path.join(BASE, '长期计划/缠论主升浪')
GROWTH_DIR    = os.path.join(BASE, '成长股选股器')
ASTOCK_DIR    = os.path.join(BASE, '长期计划/A股选股器/src/选股器')
MAD_DIR       = os.path.join(BASE, 'MAD选股器')
OUTPUT_DIR    = os.path.join(CHANLUN_DIR, 'outputs/股票筛选')

# 各脚本路径
CHANLUN_SCRIPT = os.path.join(CHANLUN_DIR, 'src/screening_akshare_v8.py')
GROWTH_SCRIPT  = os.path.join(GROWTH_DIR, 'scripts/growth_screener_dual.py')
ASTOCK_SCRIPT  = os.path.join(ASTOCK_DIR, 'run_screener_20260605.py')
MAD_SCRIPT     = os.path.join(MAD_DIR, 'src/mad_screener.py')
PEG_SCRIPT     = os.path.join(MAD_DIR, 'src/peg_filter.py')

TUSHARE_TOKEN = os.environ.get(
    'COZE_TUSHARE_TOKEN_7649020808588689460',
    _tushare_token()
)

# ── DataHub 统一数据入口（禁止直连 tushare/akshare 取数） ──
_HUB_PATHS = [
    _HUB_ROOT,
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), 'codeact', 'scripts'),
]
for _hp in _HUB_PATHS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
        break
try:
    import data_hub as dh
except Exception as _e:  # noqa: BLE001
    dh = None
    print(f"[DataHub] 导入失败，回退直连: {_e}")

# Tushare 开关：因沙箱多出口IP触发"IP数量超限"，默认禁用，改用AKShare
# 如需恢复tushare，将 USE_TUSHARE 改为 True 即可
USE_TUSHARE = False

# Python3.13 akshare兼容
try:
    import sys as _sys
    _sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()
except:
    pass

# ===================== 辅助函数 =====================
def run_script(cmd, cwd=BASE, env_extra=None):
    """执行脚本并实时打印输出"""
    env = os.environ.copy()
    env['COZE_TUSHARE_TOKEN_7649020808588689460'] = TUSHARE_TOKEN
    # 设置S1输出目录的绝对路径，避免相对路径因cwd不同而错位
    env['CHANLUN_OUTPUT_DIR'] = os.path.join(CHANLUN_DIR, 'outputs', '股票筛选')
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True,
        text=True, timeout=1200, env=env
    )
    print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
    if result.returncode != 0:
        print(f"  ⚠️ 脚本返回非零: {result.returncode}")
        if result.stderr:
            print(f"  STDERR: {result.stderr[-500:]}")
    return result.returncode == 0

def normalize_code(raw):
    """提取纯6位股票代码"""
    raw = str(raw).strip().replace('\u200b', '').replace('\ufeff', '')
    m = re.search(r'\d{6}', raw)
    return m.group(0) if m else None

# ===================== 运行三个策略 =====================
def run_all_strategies():
    """依次运行三个选股脚本，返回各自的执行状态"""
    status = {}

    # --- S1: 缠论主升浪 v8 ---
    print("=" * 60)
    print("[S1] 缠论主升浪 v8 (三档降级)")
    print("=" * 60)
    ok = run_script(f'python3.13 "{CHANLUN_SCRIPT}"', cwd=CHANLUN_DIR)
    # 双重校验：即使脚本返回0，也要确认输出文件存在（脚本失败时可能只print不exit）
    chanlun_output = os.path.join(OUTPUT_DIR, f'{TODAY}_多头排列Top20.md')
    if ok and not os.path.exists(chanlun_output):
        print("  ⚠️ S1 输出文件不存在，判定为失败")
        ok = False
    status['S1'] = ok
    print()

    # --- S2: 成长股选股器 ---
    print("=" * 60)
    print("[S2] 成长股选股器")
    print("=" * 60)
    # 写临时wrapper脚本避免引号问题
    wrapper2 = os.path.join(BASE, '_run_growth.py')
    with open(wrapper2, 'w') as f:
        f.write(f"import sys, os\nsys.path.insert(0, '{os.path.join(GROWTH_DIR, 'scripts')}')\n")
        f.write(f"os.chdir('{GROWTH_DIR}')\n")
        f.write("import importlib.util\n")
        f.write(f"spec = importlib.util.spec_from_file_location('gs', '{GROWTH_SCRIPT}')\n")
        f.write("mod = importlib.util.module_from_spec(spec)\n")
        f.write("spec.loader.exec_module(mod)\n")
    ok = run_script(f'python3.13 "{wrapper2}"')
    status['S2'] = ok
    print()

    # --- S3: A股选股器（动态修正日期） ---
    print("=" * 60)
    print("[S3] A股多时段强势股筛选器")
    print("=" * 60)
    # 写临时wrapper脚本避免引号问题
    wrapper3 = os.path.join(BASE, '_run_astock.py')
    with open(ASTOCK_SCRIPT, 'r', encoding='utf-8') as f:
        src = f.read()
    # 把硬编码日期替换为今天
    src = re.sub(r"today_str\s*=\s*'[^']*'", f"today_str = '{TODAY}'", src)
    # 注入 __file__ 修正：使 os.path.abspath(__file__) 指向原始脚本，
    # 这样 S3 的 output_dir 计算结果正确（指向 A股选股器/outputs/选股器/）
    with open(wrapper3, 'w') as f:
        f.write(f"import sys, os\nsys.path.insert(0, '{ASTOCK_DIR}')\n")
        f.write(f"os.chdir('{ASTOCK_DIR}')\n")
        f.write(f"__file__ = '{ASTOCK_SCRIPT}'\n")
        f.write(src + "\n")
    ok = run_script(f'python3.13 "{wrapper3}"', cwd=ASTOCK_DIR)
    status['S3'] = ok
    print()

    return status

# ===================== 解析输出 =====================
def load_chanlun():
    """解析缠论主升浪 v8 输出的 markdown 表格"""
    pattern = os.path.join(OUTPUT_DIR, f'{TODAY}_多头排列Top20.md')
    if not os.path.exists(pattern):
        print("  ⚠️ 缠论输出文件不存在")
        return {}
    results = {}
    with open(pattern, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('|'):
                continue
            cells = [c.strip() for c in line.split('|')]
            if len(cells) < 12:
                continue
            # 跳过表头/分隔线
            if '档位' in cells[1] or '---' in cells[1]:
                continue
            try:
                tier = int(cells[1])
            except (ValueError, IndexError):
                continue
            raw_code = cells[2]
            code = normalize_code(raw_code)
            if not code:
                continue
            name = cells[3]
            try:
                price = float(cells[4])
                mtd = float(cells[11])
            except (ValueError, IndexError):
                continue
            # 尝试解析年初至今涨幅（第13列，index 12）
            ytd = None
            if len(cells) >= 13 and cells[12] and cells[12] != 'N/A':
                try:
                    ytd = float(cells[12])
                except (ValueError, IndexError):
                    pass
            results[code] = {
                'name': name,
                'price': price,
                'month_return': mtd,
                'year_return': ytd,
                'half_return': None,
                'week_return': None,
                'revenue_growth': None,
                'profit_growth': None,
                'pe': None,
                'industry': None,
            }
    print(f"  缠论主升浪: {len(results)} 只（有YTD数据: {sum(1 for v in results.values() if v.get('year_return') is not None)}只）")
    return results

def calc_month_return_tushare(ts_code: str, trade_date: str) -> float:
    """使用tushare计算真实月涨幅（当月1日至今）
    
    注意：此函数保留以备恢复，默认通过 USE_TUSHARE 开关禁用。
    """
    try:
        import tushare as ts
        from datetime import datetime, timedelta
        
        # 解析日期
        dt = datetime.strptime(trade_date, '%Y-%m-%d')
        
        # 获取月初第一个交易日的收盘价
        month_start = dt.replace(day=1)
        # 如果今天是1号，就用上个月的数据
        if dt.day == 1:
            month_start = (dt - timedelta(days=1)).replace(day=1)
        
        # 查询月初附近的数据（最多往前找10天）
        start_search = (month_start - timedelta(days=5)).strftime('%Y%m%d')
        end_search = (month_start + timedelta(days=5)).strftime('%Y%m%d')
        
        df = dh.get_stock_daily(ts_code=ts_code, start_date=start_search,
                                end_date=end_search, adjust='qfq') if dh else None
        if df is None or df.empty:
            return None
        
        df = df.sort_values('trade_date')
        # 找到月初或之后的第一个交易日
        month_start_data = df[df['trade_date'] >= month_start.strftime('%Y%m%d')].head(1)
        if month_start_data.empty:
            # 如果月初没有数据，用查询范围内的第一个交易日
            month_start_close = df.iloc[0]['close']
        else:
            month_start_close = month_start_data.iloc[0]['close']
        
        # 获取今日收盘价
        today_df = dh.get_stock_daily(ts_code=ts_code, start_date=trade_date.replace('-',''),
                                      end_date=trade_date.replace('-',''), adjust='qfq') if dh else None
        if today_df is None or today_df.empty:
            return None
        
        today_close = today_df.iloc[0]['close']
        
        # 计算月涨幅
        month_return = (today_close - month_start_close) / month_start_close * 100
        return round(month_return, 2)
        
    except Exception as e:
        print(f"    ⚠️ 计算{ts_code}月涨幅失败: {e}")
        return None


def calc_month_return_akshare(code: str, trade_date: str) -> float:
    """使用AKShare计算真实月涨幅（当月1日至今）
    
    Args:
        code: 6位纯数字股票代码（如 '000001'）
        trade_date: 日期字符串，格式 'YYYY-MM-DD'
    
    Returns:
        float: 月涨幅百分比（保留2位小数），失败返回None
    """
    try:
        import akshare as ak
        import pandas as pd
        from datetime import datetime, timedelta
        
        dt = datetime.strptime(trade_date, '%Y-%m-%d')
        
        # 获取月初第一个交易日的收盘价
        month_start = dt.replace(day=1)
        # 如果今天是1号，就用上个月的数据
        if dt.day == 1:
            month_start = (dt - timedelta(days=1)).replace(day=1)
        
        # 查询月初附近的数据（往前找5天确保覆盖到上月末）
        start_search = (month_start - timedelta(days=5)).strftime('%Y%m%d')
        end_search = trade_date.replace('-', '')
        
        # DataHub 前复权日线，列名映射为 akshare 口径（日期/收盘）
        df = dh.get_stock_daily(symbol=code, start_date=start_search,
                                end_date=end_search, adjust='qfq') if dh else None
        if df is None or df.empty:
            return None
        df = df.rename(columns={'trade_date': '日期', 'close': '收盘',
                                'open': '开盘', 'high': '最高', 'low': '最低',
                                'vol': '成交量', 'amount': '成交额'})
        
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期')
        
        # 找到月初或之后的第一个交易日
        month_start_str = month_start.strftime('%Y-%m-%d')
        month_start_data = df[df['日期'] >= month_start_str].head(1)
        if month_start_data.empty:
            # 如果月初没有数据，用查询范围内的第一个交易日
            month_start_close = float(df.iloc[0]['收盘'])
        else:
            month_start_close = float(month_start_data.iloc[0]['收盘'])
        
        # 获取最新（今日或最近交易日）收盘价
        today_close = float(df.iloc[-1]['收盘'])
        
        if month_start_close <= 0:
            return None
        
        # 计算月涨幅
        month_return = (today_close - month_start_close) / month_start_close * 100
        return round(month_return, 2)
        
    except Exception as e:
        print(f"    ⚠️ 计算{code}月涨幅(akshare)失败: {e}")
        return None


def calc_month_return(code: str, trade_date: str) -> float:
    """计算月涨幅的统一入口，按 USE_TUSHARE 开关路由
    
    Args:
        code: 6位纯数字股票代码（如 '000001'）
        trade_date: 日期字符串，格式 'YYYY-MM-DD'
    """
    if USE_TUSHARE:
        # tushare 路径（保留以备恢复）
        if code.startswith('6'):
            ts_code = f"{code}.SH"
        else:
            ts_code = f"{code}.SZ"
        return calc_month_return_tushare(ts_code, trade_date)
    else:
        return calc_month_return_akshare(code, trade_date)

def load_growth():
    """解析成长股选股器 CSV"""
    csv_path = os.path.join(GROWTH_DIR, 'outputs', f'{TODAY}_成长股筛选结果.csv')
    if not os.path.exists(csv_path):
        print(f"  ⚠️ 成长股输出不存在: {csv_path}")
        return {}
    import csv
    results = {}
    
    # 批量获取所有股票的月涨幅（默认AKShare，USE_TUSHARE=True时回退tushare）
    print(f"  📊 计算成长股真实月涨幅（数据源: {'tushare' if USE_TUSHARE else 'akshare'}）...")
    month_returns_cache = {}
    
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = normalize_code(row.get('股票代码', ''))
            if not code:
                continue
            try:
                rev = float(row.get('营收增速%', 0) or 0)
                prof = float(row.get('净利润增速%', 0) or 0)
                pe = float(row.get('PE', 0) or 0)
            except (ValueError, TypeError):
                continue
            if rev <= 0 and prof <= 0:
                continue
            
            # 获取真实月涨幅（统一路由，6位代码）
            if code not in month_returns_cache:
                mtd = calc_month_return(code, TODAY)
                month_returns_cache[code] = mtd
            else:
                mtd = month_returns_cache[code]
            
            try:
                wr = float(row.get('近一周涨幅%', 0) or 0)
            except (ValueError, TypeError):
                wr = None
            
            results[code] = {
                'name': row.get('股票名称', ''),
                'price': None,
                'month_return': mtd,
                'year_return': float(row.get('年内涨幅%', 0) or 0),
                'half_return': None,
                'week_return': wr,
                'revenue_growth': rev,
                'profit_growth': prof,
                'pe': pe,
                'industry': row.get('行业', ''),
            }
    print(f"  成长股选股器: {len(results)} 只（月涨幅已用tushare真实数据）")
    return results

def load_astock():
    """解析A股选股器 CSV"""
    # S3脚本实际输出路径（按可能性排列）：
    # 1. ASTOCK_DIR 相对上两级 outputs/选股器/（wrapper修正__file__后的正确路径）
    # 2. 沙箱路径 /app/data/outputs/选股器/
    # 3. BASE/outputs/选股器/（wrapper未修正__file__时的旧路径兜底）
    possible_paths = [
        os.path.join(os.path.dirname(os.path.dirname(ASTOCK_DIR)), 'outputs', '选股器'),
        _cp('/app/data/outputs/选股器'),
        os.path.join(BASE, 'outputs', '选股器'),
    ]
    csv_path = None
    for p in possible_paths:
        candidate = os.path.join(p, f'{TODAY}_筛选结果.csv')
        if os.path.exists(candidate):
            csv_path = candidate
            break
    if not csv_path:
        print(f"  ⚠️ A股选股器输出不存在 (已检查: {possible_paths})")
        return {}
    import csv
    results = {}
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(
            (line for line in f if not line.startswith('#')),
        )
        for row in reader:
            code = normalize_code(row.get('股票代码', ''))
            if not code:
                continue
            try:
                price = float(row.get('最新价', 0) or 0)
                yr = float(row.get('近1年涨幅_%', 0) or 0)
                hr = float(row.get('近半年涨幅_%', 0) or 0)
                mr = float(row.get('近1月涨幅_%', 0) or 0)
                ytd = float(row.get('当年涨幅_%', 0) or 0)
            except (ValueError, TypeError):
                continue
            if price <= 0:
                continue
            results[code] = {
                'name': row.get('名称', ''),
                'price': price,
                'month_return': mr,
                'year_return': yr,
                'half_return': hr,
                'week_return': None,
                'revenue_growth': None,
                'profit_growth': None,
                'pe': None,
                'industry': None,
            }
    print(f"  A股选股器: {len(results)} 只")
    return results

# ===================== S4 MAD选股器 =====================
def run_s4():
    """运行 MAD 选股器（mad_screener 出初筛 → peg_filter 做 PEG 双口径过滤）"""
    print("\n" + "=" * 60)
    print("  S4 MAD选股器")
    print("=" * 60)
    ymd = TODAY.replace('-', '')
    ok = True
    for name, script in [('mad_screener', MAD_SCRIPT), ('peg_filter', PEG_SCRIPT)]:
        if not os.path.exists(script):
            print(f"  ❌ 缺失脚本: {script}")
            ok = False
            continue
        print(f"  ▶ {name} ...")
        try:
            r = subprocess.run([sys.executable, script, '--end', ymd],
                               cwd=os.path.join(MAD_DIR, 'src'),
                               capture_output=True, text=True, timeout=1800)
            tail = [l for l in (r.stdout or '').strip().split('\n') if l.strip()][-3:]
            for l in tail:
                print(f"    {l}")
            if r.returncode != 0:
                print(f"  ⚠️ {name} 退出码 {r.returncode}: {(r.stderr or '')[-300:]}")
                ok = False
        except Exception as e:
            print(f"  ⚠️ {name} 执行异常: {e}")
            ok = False
    return ok


def load_s4():
    """读取 MAD 选股器最终结果 CSV"""
    import csv as _csv
    p = os.path.join(MAD_DIR, 'outputs', f'{TODAY}_MAD选股结果.csv')
    if not os.path.exists(p):
        print(f"  ⚠️ S4 结果缺失: {p}")
        return {}
    d = {}
    with open(p, 'r', encoding='utf-8-sig') as f:
        for row in _csv.DictReader(f):
            code = normalize_code(row.get('symbol') or row.get('股票代码') or '')
            if not code:
                continue

            def _f(k):
                try:
                    v = float(row.get(k) or '')
                    return v if v == v else None
                except Exception:
                    return None

            d[code] = dict(name=row.get('name') or row.get('股票名称', ''),
                           industry=row.get('industry') or row.get('行业', ''),
                           price=_f('close_y'),
                           pe=_f('pe_ttm'),
                           peg=_f('peg'),
                           week_return=_f('week_ret'))
    print(f"  S4 MAD选股   : {len(d)} 只")
    return d


def fill_missing_ytd(s1_dict, s2_dict, s3_dict):
    """为S1中缺失year_return的股票补全年初至今涨幅
    
    补全策略：
    1. 优先从S2/S3已有数据补全（重叠股票）
    2. 剩余的用akshare批量查询年初至今涨跌幅（USE_TUSHARE=True时走tushare）
    """
    # 第一步：从S2/S3补全
    filled_from_other = 0
    for code in s1_dict:
        if s1_dict[code].get('year_return') is not None:
            continue
        # 尝试从S2/S3取
        for other in [s2_dict, s3_dict]:
            if code in other and other[code].get('year_return') is not None:
                s1_dict[code]['year_return'] = other[code]['year_return']
                filled_from_other += 1
                break
    
    # 第二步：剩余缺失的用akshare/tushare批量查询
    missing_codes = [code for code, v in s1_dict.items() if v.get('year_return') is None]
    if not missing_codes:
        print(f"  YTD补全: 从其他策略补{filled_from_other}只，全部完成")
        return
    
    data_source_name = 'tushare' if USE_TUSHARE else 'akshare'
    print(f"  YTD补全: 从其他策略补{filled_from_other}只，剩余{len(missing_codes)}只需要{data_source_name}查询...")
    
    if USE_TUSHARE:
        # ---- tushare 路径（保留以备恢复）----
        _fill_missing_ytd_tushare(s1_dict, missing_codes)
    else:
        # ---- akshare 路径（默认）----
        _fill_missing_ytd_akshare(s1_dict, missing_codes)


def _fill_missing_ytd_tushare(s1_dict, missing_codes):
    """tushare版YTD补全（保留以备恢复）"""
    try:
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y%m%d')
        year_start = f"{datetime.now().year}0105"
        
        # 获取最近交易日
        _ds = dh.get_trade_dates(
            (datetime.now() - timedelta(days=10)).strftime('%Y%m%d'), today) if dh else None
        cal = pd.DataFrame({'cal_date': [d for d in (_ds or []) if d < today]}).sort_values('cal_date')
        if len(cal) == 0:
            print("    ⚠️ 无法获取交易日，跳过补全")
            return
        trade_date = cal.iloc[-1]['cal_date']
        
        # 获取年初交易日
        _yd = dh.get_trade_dates(year_start, f'{datetime.now().year}0131') if dh else None
        year_cal = pd.DataFrame({'cal_date': sorted(_yd or [])})
        if len(year_cal) == 0:
            print("    ⚠️ 无法获取年初交易日，跳过tushare补全")
            return
        year_start_date = year_cal.iloc[0]['cal_date']
        
        # 批量获取当前价和年初价
        df_now = dh.get_market_daily(trade_date=trade_date) if dh else None
        df_start = dh.get_market_daily(trade_date=year_start_date) if dh else None
        if df_now is None or df_start is None:
            print("    ⚠️ DataHub 全市场日线为空，跳过补全")
            return
        
        now_prices = dict(zip(df_now['ts_code'], df_now['close']))
        start_prices = dict(zip(df_start['ts_code'], df_start['close']))
        
        def _to_ts_code(code):
            return f"{code}.SH" if code.startswith(('6', '5')) else f"{code}.SZ"
        
        filled_count = 0
        for code in missing_codes:
            ts_code = _to_ts_code(code)
            p_now = now_prices.get(ts_code)
            p_start = start_prices.get(ts_code)
            if p_now and p_start and p_start > 0:
                ytd = round((p_now - p_start) / p_start * 100, 2)
                s1_dict[code]['year_return'] = ytd
                filled_count += 1
        
        print(f"  YTD补全: tushare批量查询补充{filled_count}只，剩余{len(missing_codes) - filled_count}只无数据")
        
    except Exception as e:
        print(f"  ⚠️ tushare批量YTD补全失败: {e}")


def _fill_missing_ytd_akshare(s1_dict, missing_codes):
    """akshare版YTD补全（默认路径）
    
    逐只股票查询今年初至今的日线数据，计算YTD涨跌幅。
    数量通常较少（S1缺YTD的股票），串行+轻量重试即可。
    """
    import akshare as ak
    from datetime import datetime
    
    year_start = f"{datetime.now().year}0102"
    end_date = datetime.now().strftime('%Y%m%d')
    
    filled_count = 0
    failed_count = 0
    
    for i, code in enumerate(missing_codes):
        try:
            df = dh.get_stock_daily(symbol=code, start_date=year_start,
                                    end_date=end_date, adjust='qfq') if dh else None
            if df is None or df.empty:
                failed_count += 1
                continue
            df = df.rename(columns={'trade_date': '日期', 'close': '收盘'})
            df = df.sort_values('日期')
            start_close = float(df.iloc[0]['收盘'])
            latest_close = float(df.iloc[-1]['收盘'])
            
            if start_close > 0:
                ytd = round((latest_close - start_close) / start_close * 100, 2)
                s1_dict[code]['year_return'] = ytd
                filled_count += 1
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            if i < 3:  # 只打印前几个失败，避免刷屏
                print(f"    ⚠️ {code} YTD补全失败: {e}")
        
        # 每10只打印一次进度
        if (i + 1) % 10 == 0 or (i + 1) == len(missing_codes):
            print(f"    进度: {i+1}/{len(missing_codes)} (成功{filled_count}, 失败{failed_count})")
    
    print(f"  YTD补全: akshare查询补充{filled_count}只，剩余{len(missing_codes) - filled_count}只无数据")


# ===================== 合并 & 打星 =====================
def merge_results(s1, s2, s3, s4=None):
    """合并四个选股器结果，按星级排序"""
    s4 = s4 or {}
    all_codes = set(s1) | set(s2) | set(s3) | set(s4)
    merged = []

    for code in all_codes:
        sources = []
        if code in s1:
            sources.append('缠论主升浪')
        if code in s2:
            sources.append('成长股')
        if code in s3:
            sources.append('A股强势股')
        if code in s4:
            sources.append('MAD选股')

        n = len(sources)
        stars = n  # 4个=★★★★, 3个=★★★, 2个=★★, 1个=★

        # 聚合各来源的数据（取最优值）
        info = {'name': '', 'price': None}
        month_returns, year_returns = [], []
        for src_dict in [s1, s2, s3, s4]:
            if code not in src_dict:
                continue
            d = src_dict[code]
            if not info['name']:
                info['name'] = d.get('name', '')
            if info['price'] is None and d.get('price'):
                info['price'] = d['price']
            if d.get('month_return') is not None:
                month_returns.append(d['month_return'])
            if d.get('year_return') is not None:
                year_returns.append(d['year_return'])
            for key in ['revenue_growth', 'profit_growth', 'pe', 'industry', 'week_return', 'peg']:
                if info.get(key) is None and d.get(key) is not None:
                    info[key] = d[key]

        info['code'] = code
        info['strategies'] = sources
        info['n_strategies'] = n
        info['stars'] = stars
        info['month_return'] = round(sum(month_returns) / len(month_returns), 2) if month_returns else None
        info['year_return'] = round(sum(year_returns) / len(year_returns), 2) if year_returns else None

        merged.append(info)

    # 排序：星级降序 → 月涨幅降序
    merged.sort(key=lambda x: (-x['stars'], -(x['month_return'] or 0)))
    return merged

# ===================== 生成报告 =====================
def _resonance_industry_rows(merged):
    """共振行业加权：只统计 ★★ 及以上的股票"""
    bucket = {}
    for m in merged:
        if m['n_strategies'] < 2:
            continue
        k = m.get('industry') or '未分类'
        b = bucket.setdefault(k, dict(w=0, stocks=[]))
        b['w'] += m['n_strategies']
        b['stocks'].append(m)
    rows = []
    for k, b in bucket.items():
        st = sorted(b['stocks'], key=lambda x: (-x['n_strategies'], -(x['month_return'] or -999)))
        rows.append((k, b['w'], len(st), st))
    rows.sort(key=lambda x: (-x[1], -x[2], x[0]))
    return rows


def generate_report(merged, strategy_status, ind_rows=None, con_rows=None):
    """生成统一 Markdown 报告（共振置顶 → 共振行业加权 → 全样本加权 → 明细）"""
    star_label = {4: '★★★★', 3: '★★★', 2: '★★', 1: '★'}

    c4 = sum(1 for s in merged if s['n_strategies'] >= 4)
    c3 = sum(1 for s in merged if s['n_strategies'] == 3)
    c2 = sum(1 for s in merged if s['n_strategies'] == 2)
    c1 = sum(1 for s in merged if s['n_strategies'] == 1)
    multi = [s for s in merged if s['n_strategies'] >= 2]
    res_rows = _resonance_industry_rows(merged)

    L = []
    L.append(f"# {TODAY} A股四选股器综合选股报告\n")
    L.append(f"**报告日期**：{TODAY}  ")
    L.append(f"**生成方式**：纯 Python 脚本合并（unified_screener.py），零 LLM 参与  ")
    L.append(f"**数据源**：TuShare / AKShare / data_hub 四级降级体系\n")

    # ========== 一、共振速览（置顶） ==========
    L.append("## 一、共振速览\n")
    L.append(f"> **今日 {len(multi)} 只共振股（★★ 及以上），分布在 {len(res_rows)} 个行业** ｜ "
             f"全样本 {len(merged)} 只，来自 4 个选股器\n")
    L.append("| 评级 | 含义 | 只数 |")
    L.append("|------|------|------|")
    L.append(f"| ★★★★ | 四选股器全部命中 | **{c4}** |")
    L.append(f"| ★★★ | 三选股器命中 | **{c3}** |")
    L.append(f"| ★★ | 双选股器命中 | **{c2}** |")
    L.append(f"| ★ | 单选股器命中 | {c1} |")
    L.append(f"| — | **总计** | **{len(merged)}** |\n")

    if multi:
        L.append("### 共振个股\n")
        L.append("| 评级 | 代码 | 名称 | 行业 | 现价 | 月涨% | 年涨% | 命中选股器 |")
        L.append("|------|------|------|------|------|-------|-------|-----------|")
        for s in multi:
            sl = star_label.get(s['stars'], '★')
            mtd = f"{s['month_return']:.1f}" if s['month_return'] is not None else '-'
            yr = f"{s['year_return']:.1f}" if s['year_return'] is not None else '-'
            price = f"{s['price']:.2f}" if s.get('price') else '-'
            L.append(f"| {sl} | {s['code']} | {s['name']} | {s.get('industry') or '-'} | {price} | {mtd} | {yr} | {'、'.join(s['strategies'])} |")
    else:
        L.append("> ⚠️ 今日无 ★★ 及以上共振股票——四选股器风格互斥（S1/S3 追动量、S2 要业绩、S4 抓低估值），无交集属正常。\n")
    L.append("")

    # ========== 二、共振行业加权 ==========
    L.append("## 二、共振行业加权（仅 ★★ 及以上）\n")
    L.append("> 只统计被 **2 个以上选股器同时命中** 的股票，行业「加权次数」= Σ(该行业共振股的入选选股器数)。")
    L.append("> 这是最干净的行业信号——剔除了单策略行业扎堆的噪音。\n")
    if res_rows:
        L.append("| 排名 | 行业 | 加权次数 | 股票数 | 共振股票 |")
        L.append("|------|------|----------|--------|----------|")
        for i, (k, w, ns, st) in enumerate(res_rows, 1):
            cell = '、'.join('%s%s' % (x['name'], '★' * x['n_strategies']) for x in st)
            L.append(f"| {i} | **{k}** | **{w}** | {ns} | {cell} |")
    else:
        L.append("> 今日无共振行业。")
    L.append("")

    # ========== 三、全样本行业加权 ==========
    if ind_rows:
        L.append("## 三、全样本行业加权（按加权次数排名）\n")
        L.append("> **加权规则**：股票权重 = 它入选的选股器数量。行业「加权次数」= 该行业全部股票权重之和。")
        L.append("> ⚠️ 加权次数高 ≠ 多策略共振，也可能是单一选股器在某行业扎堆（如银行多为 S1 单命中）。要看真共振请回看第二节。\n")
        L.append("| 排名 | 行业 | 加权次数 | 股票数 | 选中股票（★=入选选股器数） |")
        L.append("|------|------|----------|--------|---------------------------|")
        for i, (k, w, ns, st) in enumerate(ind_rows, 1):
            cell = '、'.join('%s%s' % (x['name'], '★' * x['n_strategies']) for x in st)
            L.append(f"| {i} | **{k}** | **{w}** | {ns} | {cell} |")
        L.append("")

    # ========== 四、概念加权 ==========
    if con_rows:
        L.append("## 四、全样本概念加权（按加权次数排名）\n")
        L.append("> 概念来自同花顺概念指数，已剔除宽泛指数（成分股>300只）与指数成分股类概念。一只股票可属多个概念，故加权总数大于行业节。\n")
        L.append("| 排名 | 概念 | 加权次数 | 股票数 | 选中股票 |")
        L.append("|------|------|----------|--------|----------|")
        for i, (k, w, ns, st) in enumerate(con_rows[:40], 1):
            cell = '、'.join('%s%s' % (x['name'], '★' * x['n_strategies']) for x in st)
            L.append(f"| {i} | **{k}** | **{w}** | {ns} | {cell} |")
        if len(con_rows) > 40:
            L.append(f"\n> 概念共 {len(con_rows)} 个，此处展示前 40 个，完整明细见同名 CSV。")
        L.append("")

    # ========== 五、选股器说明 ==========
    L.append("## 五、四选股器说明与运行状态\n")
    L.append("| 编号 | 选股器 | 核心逻辑 | 状态 | 入选数 |")
    L.append("|------|--------|----------|------|--------|")
    L.append(f"| S1 | 缠论主升浪 | 多头排列（股价>MA20>MA40>MA80>MA160）+ MA80/160↑ + 回撤三档降级 | {'✅' if strategy_status.get('S1') else '❌'} | {len(strategy_status.get('_n1', []))} |")
    L.append(f"| S2 | 成长股 | 营收↑>20% + 净利↑>15% + PE>0 + 年内涨幅前200 + 行业前50 | {'✅' if strategy_status.get('S2') else '❌'} | {len(strategy_status.get('_n2', []))} |")
    L.append(f"| S3 | A股强势股 | 市值>50亿 + 多时段动量百分位四重筛选 | {'✅' if strategy_status.get('S3') else '❌'} | {len(strategy_status.get('_n3', []))} |")
    L.append(f"| S4 | MAD选股 | MA20>MA200 且 MA20>MA40 + MA40斜率>0 + 周涨>0 + PEG双口径任一≤1.5 | {'✅' if strategy_status.get('S4') else '❌'} | {len(strategy_status.get('_n4', []))} |")
    L.append(f"| — | **去重合计** | — | — | **{len(merged)}** |\n")

    # ========== 六、全量明细 ==========
    L.append("## 六、全量选股明细\n")
    L.append("| 评级 | 代码 | 名称 | 行业 | 现价 | 月涨% | 年涨% | 命中选股器 |")
    L.append("|------|------|------|------|------|-------|-------|-----------|")
    for s in merged:
        sl = star_label.get(s['stars'], '★')
        mtd = f"{s['month_return']:.1f}" if s['month_return'] is not None else '-'
        yr = f"{s['year_return']:.1f}" if s['year_return'] is not None else '-'
        price = f"{s['price']:.2f}" if s.get('price') else '-'
        name = f"⚠️ {s['name']}" if (s['year_return'] is not None and s['year_return'] < -50) else s['name']
        L.append(f"| {sl} | {s['code']} | {name} | {s.get('industry') or '-'} | {price} | {mtd} | {yr} | {'、'.join(s['strategies'])} |")

    L.append("")
    L.append("> 月涨% 为 `-` 表示该股仅被 S4 命中——S4 数据源只取周涨幅（周涨>0 是筛选条件），无月涨字段。")
    L.append("")
    L.append("---")
    L.append("*纯程序生成，零 Token 消耗 ｜ 案例分析学习材料，不构成投资建议*")
    return '\n'.join(L)


# ===================== 推送飞书 =====================
def push_feishu(file_path=None, text=None, as_file=False):
    """转发到 feishu_push 模块（纯文本/文件附件）"""
    sys.path.insert(0, BASE)
    from feishu_push import push_to_feishu as _push
    return _push(file_path=file_path, text=text, as_file=as_file)


def push_card_feishu(card_dict):
    """推送交互式卡片到飞书"""
    sys.path.insert(0, BASE)
    from feishu_push import push_card_to_feishu as _push_card
    return _push_card(card_dict=card_dict)


def _build_card_strategies_data(merged):
    """将 merged 结果转换为 build_multi_strategy_card 需要的 strategies_data 格式
    
    按共振星级分组：★★★三策略共振 / ★★双策略共振 / ★单策略
    返回: list of dict with strategy_name, stock_count, top_stocks (list of (name, month_pct, year_pct))
    """
    # 按星级分组
    s4_stocks = [s for s in merged if s['n_strategies'] >= 4]
    s3_stocks = [s for s in merged if s['n_strategies'] == 3]
    s2_stocks = [s for s in merged if s['n_strategies'] == 2]
    s1_stocks = [s for s in merged if s['n_strategies'] == 1]
    
    # 每组按月涨幅降序取 top_stocks
    def _top_pairs(stocks, top_n=10):
        sorted_stocks = sorted(stocks, key=lambda x: -(x.get('month_return') or 0))
        return [(s['name'], s.get('month_return') or 0, s.get('year_return')) for s in sorted_stocks[:top_n]]
    
    strategies_data = []
    # 共振优先：★★★★ → ★★★ → ★★ → ★（星级高的永远排前面）
    if s4_stocks:
        strategies_data.append({
            'strategy_name': "🏆 ★★★★ 四选股器共振",
            'stock_count': len(s4_stocks),
            'top_stocks': _top_pairs(s4_stocks, top_n=20),
        })
    if s3_stocks:
        strategies_data.append({
            'strategy_name': f"🔥 ★★★ 三选股器共振",
            'stock_count': len(s3_stocks),
            'top_stocks': _top_pairs(s3_stocks, top_n=15),
        })
    if s2_stocks:
        strategies_data.append({
            'strategy_name': f"⭐ ★★ 双选股器共振",
            'stock_count': len(s2_stocks),
            'top_stocks': _top_pairs(s2_stocks, top_n=15),
        })
    if s1_stocks:
        strategies_data.append({
            'strategy_name': f"📋 ★ 单选股器入选",
            'stock_count': len(s1_stocks),
            'top_stocks': _top_pairs(s1_stocks, top_n=10),
        })
    
    return strategies_data


def _build_feishu_summary(merged, degraded=False):
    """生成飞书摘要文本（简短版，完整内容见文件附件）—— 保留兼容，卡片推送主用
    
    Args:
        merged: 合并后的选股结果
        degraded: 是否为卡片推送失败降级而来，降级时会加标注
    """
    s3_count = sum(1 for s in merged if s['n_strategies'] >= 3)
    s2_count = sum(1 for s in merged if s['n_strategies'] == 2)
    s1_count = sum(1 for s in merged if s['n_strategies'] == 1)

    md = ""
    if degraded:
        md += "⚠️ 【卡片推送失败已降级】以下为文本摘要版：\n\n"
    md += f"📊 多策略综合选股 | {TODAY}\n\n"
    md += f"★★★(3策略): {s3_count}只  ★★(2策略): {s2_count}只  ★(单策略): {s1_count}只\n"
    md += f"总计: {len(merged)} 只\n\n"

    if s3_count > 0:
        md += "🔥 ★★★ 三策略共振:\n"
        for s in merged:
            if s['n_strategies'] >= 3:
                mtd = f"月涨{s['month_return']:.1f}%" if s['month_return'] else ''
                disp_name = f"⚠️ {s['name']}" if (s['year_return'] is not None and s['year_return'] < -50) else s['name']
                md += f"  {s['code']} {disp_name} {mtd}\n"
        md += "\n"

    if s2_count > 0:
        md += "⭐ ★★ 双策略共振:\n"
        for s in merged:
            if s['n_strategies'] == 2:
                mtd = f"月涨{s['month_return']:.1f}%" if s['month_return'] else ''
                disp_name = f"⚠️ {s['name']}" if (s['year_return'] is not None and s['year_return'] < -50) else s['name']
                md += f"  {s['code']} {disp_name} {mtd}\n"
        md += "\n"

    md += f"📎 完整{len(merged)}只股票明细见附件报告\n"
    md += "---\n⚠️ 案例分析学习材料，不构成投资建议"
    return md


def push_card_with_retry(card_dict, max_retries=2, retry_delay=2):
    """带重试的卡片推送，提高成功率
    
    Args:
        card_dict: 卡片字典
        max_retries: 最大重试次数（不含首次）
        retry_delay: 重试间隔（秒）
    
    Returns:
        bool: 是否推送成功
    """
    import time
    sys.path.insert(0, BASE)
    from feishu_push import push_card_to_feishu as _push_card
    
    for attempt in range(max_retries + 1):
        try:
            ok = _push_card(card_dict=card_dict)
            if ok:
                if attempt > 0:
                    print(f"  ✅ 卡片推送成功（第{attempt+1}次尝试）")
                return True
        except Exception as e:
            print(f"  ⚠️ 卡片推送第{attempt+1}次异常: {e}")
        
        if attempt < max_retries:
            print(f"  ⏳ 等待{retry_delay}秒后重试...")
            time.sleep(retry_delay)
    
    return False

# ===================== 邮件通知（★★★共振） =====================
EMAIL_STATE_FILE = os.path.join(OUTPUT_DIR, '.email_sent_dates.txt')

def _check_email_idempotent(today_str):
    """检查今日是否已发送过共振邮件"""
    try:
        if os.path.exists(EMAIL_STATE_FILE):
            with open(EMAIL_STATE_FILE, 'r') as f:
                dates = [line.strip() for line in f if line.strip()]
            return today_str in dates
    except Exception:
        pass
    return False

def _mark_email_sent(today_str):
    """标记今日已发送共振邮件"""
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(EMAIL_STATE_FILE, 'a') as f:
            f.write(today_str + '\n')
    except Exception as e:
        print(f"  ⚠️ 邮件幂等标记写入失败: {e}")

def send_resonance_email(merged, report_path):
    """当存在★★★股票时，发送共振邮件到主人邮箱
    
    邮件内容：
    - 正文：共振股票明细（代码、名称、当前价、月涨%、年涨%、入选策略）
    - 附件：当日完整多策略综合选股报告.md
    
    幂等：同一天只发一次。
    如果邮件发送失败（如QQ邮箱自投黑名单），降级为飞书特殊通知。
    """
    if _check_email_idempotent(TODAY):
        print("  📧 今日已发送过共振邮件，幂等跳过")
        return False
    
    three_stars = [s for s in merged if s['n_strategies'] >= 3]
    if not three_stars:
        return False
    
    # 构建邮件/通知正文
    lines = []
    lines.append(f"🔥 {TODAY} 三策略共振提醒（★★★）")
    lines.append(f"共 {len(three_stars)} 只股票被 S1/S2/S3 全部选中\n")
    lines.append("| 代码 | 名称 | 当前价 | 月涨% | 年涨% | 入选策略 |")
    lines.append("|------|------|--------|-------|-------|---------|")
    for s in three_stars:
        price = f"{s['price']:.2f}" if s.get('price') else '-'
        mtd = f"{s['month_return']:.1f}" if s['month_return'] is not None else '-'
        yr = f"{s['year_return']:.1f}" if s['year_return'] is not None else '-'
        strat = '、'.join(s['strategies'])
        lines.append(f"| {s['code']} | {s['name']} | {price} | {mtd} | {yr} | {strat} |")
    
    lines.append(f"\n完整 {len(merged)} 只股票明细见附件报告。")
    lines.append("\n⚠️ 案例分析学习材料，不构成投资建议")
    
    body = '\n'.join(lines)
    email_sent = False
    
    # 尝试发送邮件
    try:
        sys.path.insert(0, BASE)
        from qq_mail_tool import send_mail
        send_mail(
            to='410119229@qq.com',
            subject=f"🔥 {TODAY} 三策略共振 {len(three_stars)}只",
            content=body,
            html=False,
            attachment=report_path if os.path.exists(report_path) else None
        )
        email_sent = True
        print(f"  📧 共振邮件发送成功：{len(three_stars)}只★★★股票")
    except Exception as e:
        print(f"  ⚠️ 共振邮件发送失败: {e}")
        # 降级为飞书特殊通知
        try:
            from feishu_push import push_to_feishu
            fallback_text = f"🔥🔥🔥 ★★★ 三策略共振 {len(three_stars)}只 | {TODAY}\n\n"
            for s in three_stars:
                price = f"{s['price']:.2f}" if s.get('price') else '-'
                mtd = f"月涨{s['month_return']:.1f}%" if s['month_return'] is not None else ''
                fallback_text += f"  {s['code']} {s['name']} {price} {mtd}\n"
            fallback_text += f"\n⚠️ 邮件发送失败({type(e).__name__})，降级飞书通知"
            push_to_feishu(text=fallback_text)
            print("  📧 邮件失败→飞书降级通知已发送")
            email_sent = True  # 降级成功也算已通知
        except Exception as e2:
            print(f"  ⚠️ 飞书降级通知也失败: {e2}")
    
    if email_sent:
        _mark_email_sent(TODAY)
    return email_sent


# ===================== 主流程 =====================
def main():
    # 解析CLI参数
    no_push = '--no-push' in sys.argv
    
    print("=" * 60)
    print(f"  A股四选股器综合选股器 v2.0 | {TODAY}")
    if no_push:
        print("  ⚡ --no-push 模式：跳过飞书推送")
    print("=" * 60)

    if '--skip-run' in sys.argv:
        print("\n⚡ --skip-run 模式：跳过选股器执行，直接读取已有输出")
        status = {'S1': True, 'S2': True, 'S3': True, 'S4': True}
    else:
        # Step 1: 运行 S1/S2/S3
        status = run_all_strategies()
        # Step 1.5: 运行 S4 MAD选股器
        status['S4'] = run_s4()

    # Step 2: 解析各策略输出
    print("\n" + "=" * 60)
    print("解析各策略输出...")
    print("=" * 60)
    s1 = load_chanlun()
    s2 = load_growth()
    s3 = load_astock()
    s4 = load_s4()
    status['_n1'], status['_n2'], status['_n3'], status['_n4'] = s1, s2, s3, s4

    # Step 2.5: 为S1缺失YTD的股票补全
    print("\n补全S1缺失的年初至今涨幅...")
    fill_missing_ytd(s1, s2, s3)

    # Step 3: 合并打星
    print("\n合并打星...")
    merged = merge_results(s1, s2, s3, s4)

    c4 = sum(1 for s in merged if s['n_strategies'] >= 4)
    c3 = sum(1 for s in merged if s['n_strategies'] == 3)
    c2 = sum(1 for s in merged if s['n_strategies'] == 2)
    c1 = sum(1 for s in merged if s['n_strategies'] == 1)
    print(f"  ★★★★: {c4}  ★★★: {c3}  ★★: {c2}  ★: {c1}  总计: {len(merged)}")

    # Step 3.5: 行业/概念加权统计（纯程序）
    print("\n行业/概念加权统计...")
    ind_rows, con_rows = [], []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, BASE)
        import concept_map
        import merge_report
        # 补齐缺失行业（unified 的 merged 可能没有 industry）
        try:
            _ind = merge_report.get_industry_map()
            for m in merged:
                if not m.get('industry'):
                    m['industry'] = _ind.get(m['code'], '未分类')
            print(f"  行业字段补齐完成（映射表 {len(_ind)} 条）")
        except Exception as _e:
            print(f"  ⚠️ 行业映射不可用: {_e}")
        cmap = concept_map.get_stock_concepts([m['code'] for m in merged])
        ind_rows = merge_report.build_industry_concept_stats(merged, cmap, 'industry')
        con_rows = merge_report.build_industry_concept_stats(merged, cmap, 'concept')
        print(f"  行业 {len(ind_rows)} 个 ｜ 概念 {len(con_rows)} 个")
    except Exception as e:
        print(f"  ⚠️ 加权统计失败（报告将跳过该节）: {e}")

    # Step 4: 生成报告
    print("\n生成报告...")
    report = generate_report(merged, status, ind_rows, con_rows)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report_path = os.path.join(OUTPUT_DIR, f'{TODAY}_四选股器综合选股报告.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  📁 {report_path}")

    # Step 4.5: 加权统计 CSV
    if ind_rows or con_rows:
        import csv as _csv
        try:
            for fn, rows, head in [
                (f'{TODAY}_行业加权统计.csv', ind_rows, ['排名', '行业', '加权次数', '股票数', '选中股票']),
                (f'{TODAY}_概念加权统计.csv', con_rows, ['排名', '概念', '加权次数', '股票数', '选中股票']),
            ]:
                with open(os.path.join(OUTPUT_DIR, fn), 'w', encoding='utf-8-sig', newline='') as f:
                    w = _csv.writer(f)
                    w.writerow(head)
                    for i, (k, wt, ns, st) in enumerate(rows, 1):
                        w.writerow([i, k, wt, ns,
                                    '、'.join('%s%s' % (x['name'], '★' * x['n_strategies']) for x in st)])
                print(f"  📁 {os.path.join(OUTPUT_DIR, fn)}")
        except Exception as e:
            print(f"  ⚠️ CSV 写出失败: {e}")

    # Step 5: 推送飞书（先发交互式卡片，再发完整报告文件）
    if no_push:
        print("\n⚡ --no-push 模式：跳过飞书推送")
    else:
        print("\n推送飞书...")
        
        # 5.1 交互式卡片推送（卡片为主推送方式，带重试机制，极端情况才降级）
        card_ok = False
        try:
            sys.path.insert(0, BASE)
            from feishu_card_utils import build_multi_strategy_card
            
            strategies_data = _build_card_strategies_data(merged)
            # 数据校验：确保至少有一组数据
            if not strategies_data:
                print("  ⚠️ 无选股结果，跳过卡片推送")
            else:
                _res_ind = _resonance_industry_rows(merged)
                card = build_multi_strategy_card(
                    TODAY, strategies_data,
                    resonance_industries=[(k, w, ns) for k, w, ns, _ in _res_ind])
                # 带重试的卡片推送，最多重试2次，降低偶发失败导致的降级
                card_ok = push_card_with_retry(card, max_retries=2, retry_delay=2)
                if card_ok:
                    print("  ✅ 交互式卡片推送成功")
                else:
                    print("  ⚠️ 卡片推送多次失败，降级为文本摘要")
        except Exception as e:
            print(f"  ⚠️ 卡片构建/推送异常: {e}，降级为文本摘要")
            import traceback
            traceback.print_exc()
        
        # 5.1.1 降级兜底：卡片完全失败时才发文本，且明确标注降级
        if not card_ok:
            try:
                push_feishu(text=_build_feishu_summary(merged, degraded=True))
                print("  📝 文本降级推送完成（已标注降级）")
            except Exception as e2:
                print(f"  ⚠️ 文本摘要推送也失败: {e2}")
        
        # 5.2 完整报告文件附件推送
        try:
            push_feishu(file_path=report_path, as_file=True)
        except Exception as e:
            print(f"  ⚠️ 飞书文件推送失败: {e}")

    # Step 6: ★★★ 三策略共振邮件通知
    three_star_count = sum(1 for s in merged if s['n_strategies'] >= 3)
    if three_star_count > 0:
        print(f"\n🔥 检测到 {three_star_count} 只★★★共振股票，准备发送邮件...")
        send_resonance_email(merged, report_path)
    else:
        print("\n📋 无★★★共振股票，不发送邮件")

    # 打印摘要
    print("\n" + "=" * 60)
    print("最终结果摘要")
    print("=" * 60)
    for s in merged[:20]:
        star = '★' * s['stars']
        mtd = f"月涨{s['month_return']:.1f}%" if s['month_return'] else ''
        print(f"  {star:3s} {s['code']} {s['name']:8s} {mtd:12s} {'、'.join(s['strategies'])}")

    print(f"\n✅ 完成！报告: {report_path}")
    return report_path


if __name__ == '__main__':
    main()
