#!/usr/bin/env python3
"""
市场综合监控日报 — 数据采集脚本
=================================
采集: 流动性 + 风险偏好 + 期权SKEW + GEX + IF升贴水
输出: 结构化Markdown表格，LLM仅需写最终综合研判

用法: python market_daily_report.py [YYYYMMDD]
"""

import sys
import warnings; warnings.filterwarnings('ignore')
import os, sys, json, time, socket
import numpy as np, pandas as pd
from datetime import datetime, timedelta

socket.setdefaulttimeout(15)

import tushare as ts
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
from hub_root import tushare_token as _tushare_token, smtp_password as _smtp_password  # noqa: E402

ts.set_token(_tushare_token())
pro = ts.pro_api()

OUTPUT_DIR = '市场情绪监控/outputs'
SCRIPT_DIR = '市场情绪监控/scripts'
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ═══════════════════════════════════
# Part 1: 流动性 + 风险偏好
# ═══════════════════════════════════

def get_shibor_7d():
    try:
        for i in range(5):
            d = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
            df = pro.shibor(date=d)
            if df is not None and len(df) > 0 and '1w' in df.columns:
                return round(float(df['1w'].iloc[0]), 4), d
        return None, None
    except:
        return None, None

def get_trading_volume(td):
    try:
        df = pro.daily(trade_date=td)
        if df is not None and len(df) > 0 and 'amount' in df.columns:
            total = df['amount'].sum()
            wy = round(total / 1e9, 2)
            df_s = df.sort_values('amount', ascending=False)
            top5n = max(1, int(len(df) * 0.05))
            conc = round(df_s.head(top5n)['amount'].sum() / total * 100, 1) if total > 0 else None
            return (wy, conc) if wy > 0.1 else (None, None)
        return None, None
    except:
        return None, None

def get_limit_list(td):
    try:
        df_u = pro.limit_list(trade_date=td, limit_type='U')
        up = len(df_u) if df_u is not None else None
        df_d = pro.limit_list(trade_date=td, limit_type='D')
        down = len(df_d) if df_d is not None else None
        if up is not None and down is not None:
            return up, down
        df = pro.daily(trade_date=td)
        if df is not None and len(df) > 0 and 'pct_chg' in df.columns:
            return len(df[df['pct_chg'] >= 9.5]), len(df[df['pct_chg'] <= -9.5])
        return None, None
    except:
        return None, None

def get_north_money(td):
    try:
        df = pro.moneyflow_hsgt(trade_date=td)
        if df is not None and len(df) > 0:
            for col in ['north_net_mf', 'net_mf', 'north_net']:
                if col in df.columns:
                    v = df[col].sum()
                    return round(v / 1e8, 2) if abs(v) > 1 else None
        return None
    except:
        return None

def calc_liquidity_score(r7, vol):
    if r7 is None or vol is None: return None
    s = 0
    s += 30 if r7 < 1.5 else 24 if r7 < 1.8 else 18 if r7 < 2.0 else 12 if r7 < 2.5 else 6
    s += 30 if vol > 1.5 else 24 if vol >= 1.2 else 18 if vol >= 0.8 else 12 if vol >= 0.5 else 6
    return s

def calc_risk_score(conc, up, down, north):
    if conc is None: return None
    s = 0
    s += 18 if conc < 20 else 15 if conc < 25 else 12 if conc < 30 else 9 if conc < 35 else 6 if conc < 40 else 3
    if up is not None:
        s += 8 if up > 100 else 6 if up >= 60 else 4 if up >= 30 else 2
    if down is not None:
        s += 7 if down < 5 else 5 if down < 15 else 3 if down < 30 else 1
    if north is not None:
        s += 7 if north > 80 else 5 if north >= 0 else 3 if north >= -50 else 1
    return s


# ═══════════════════════════════════
# Part 2: 期权SKEW + GEX (调用现有脚本)
# ═══════════════════════════════════

def run_sub_script(script_name, trade_date, json_pattern=None, md_pattern=None):
    """运行子脚本，优先读JSON，其次读MD"""
    # 尝试JSON
    if json_pattern:
        json_path = os.path.join(OUTPUT_DIR, json_pattern.format(trade_date))
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f: return json.load(f), 'json'
            except: pass
    # 尝试MD
    if md_pattern:
        md_path = os.path.join(OUTPUT_DIR, md_pattern.format(trade_date))
        if os.path.exists(md_path):
            try:
                with open(md_path, 'r') as f: return f.read(), 'md'
            except: pass
    # 运行脚本
    try:
        import subprocess
        subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, script_name), trade_date],
            capture_output=True, text=True, timeout=120, cwd='.'
        )
        if json_pattern:
            json_path = os.path.join(OUTPUT_DIR, json_pattern.format(trade_date))
            if os.path.exists(json_path):
                with open(json_path, 'r') as f: return json.load(f), 'json'
        if md_pattern:
            md_path = os.path.join(OUTPUT_DIR, md_pattern.format(trade_date))
            if os.path.exists(md_path):
                with open(md_path, 'r') as f: return f.read(), 'md'
    except Exception as e:
        print(f'  [{script_name}] 执行失败: {e}')
    return None, None

def get_skew_data(td):
    data = run_sub_script('skew_calculator.py', td, '{}_skew_data.json')
    if data: return data, td
    for off in range(1, 4):
        prev = (datetime.strptime(td, '%Y%m%d') - timedelta(days=off)).strftime('%Y%m%d')
        data = run_sub_script('skew_calculator.py', prev, '{}_skew_data.json')
        if data: return data, prev
    return None, None

def get_gex_data(td):
    data = run_sub_script('gex_calculator.py', td, '{}_gex_data.json')
    if data: return data, td
    for off in range(1, 4):
        prev = (datetime.strptime(td, '%Y%m%d') - timedelta(days=off)).strftime('%Y%m%d')
        data = run_sub_script('gex_calculator.py', prev, '{}_gex_data.json')
        if data: return data, prev
    return None, None


# ═══════════════════════════════════
# Part 3: IF升贴水
# ═══════════════════════════════════

QM = [3,6,9,12]

def third_friday(y, m):
    d = datetime(y, m, 1)
    f = d + timedelta(days=(4 - d.weekday()) % 7)
    return (f + timedelta(weeks=2)).strftime('%Y%m%d')

def find_cq_nq(ds):
    td = datetime.strptime(ds, '%Y%m%d')
    cy, cm = td.year, td.month
    qs = []
    for off in range(0, 30):
        y = cy + (cm - 1 + off) // 12; m = (cm - 1 + off) % 12 + 1
        if m in QM:
            if third_friday(y, m) >= ds: qs.append(f'{y}{m:02d}')
            if len(qs) >= 2: break
    return (qs[0], qs[1]) if len(qs) >= 2 else (qs[0] if qs else None, None)

def compute_if_basis(td):
    end = td
    start = (datetime.strptime(td, '%Y%m%d') - timedelta(days=700)).strftime('%Y%m%d')

    sdf = pro.index_daily(ts_code='000300.SH', start_date=start, end_date=end)
    if sdf is None or len(sdf) == 0: return None
    sdf = sdf.sort_values('trade_date').reset_index(drop=True)
    sdf['trade_date'] = sdf['trade_date'].astype(str)
    spot = dict(zip(sdf['trade_date'], sdf['close']))
    dates = sorted(spot.keys())
    if not dates: return None

    needed = set()
    for d in dates:
        cq, nq = find_cq_nq(d)
        if cq: needed.add(cq)
        if nq: needed.add(nq)

    fut = {}
    for ym in sorted(needed):
        try:
            df = pro.fut_daily(ts_code=f'IF{ym[2:]}.CFX', start_date=start, end_date=end)
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                df['trade_date'] = df['trade_date'].astype(str)
                fut[ym] = df
            time.sleep(0.12)
        except: pass

    result = {}
    for which in ['cq', 'nq']:
        label = '当季' if which == 'cq' else '下季'
        recs = []
        for d in dates:
            cq_ym, nq_ym = find_cq_nq(d)
            ym = cq_ym if which == 'cq' else nq_ym
            if ym is None or ym not in fut: continue
            day = fut[ym][fut[ym]['trade_date'] == d]
            if len(day) == 0 or d not in spot: continue
            exp = third_friday(int(ym[:4]), int(ym[4:]))
            dte = (datetime.strptime(exp, '%Y%m%d') - datetime.strptime(d, '%Y%m%d')).days
            recs.append({'date': d, 'contract': f'IF{ym[2:]}', 'F_close': day['close'].values[0],
                         'S_close': spot[d], 'volume': day['vol'].values[0], 'oi': day['oi'].values[0],
                         'DTE': max(dte, 0)})
        if not recs: result[label] = None; continue

        df = pd.DataFrame(recs)
        chg = df['contract'] != df['contract'].shift(1)
        cum = 0; adjs = []
        for i in range(len(df)):
            if i > 0 and chg.iloc[i]: cum += df.iloc[i-1]['F_close'] - df.iloc[i]['F_close']
            adjs.append(cum)
        df['adj_F'] = df['F_close'] + pd.Series(adjs)

        df['basis_rate'] = (df['F_close'] / df['S_close'] - 1) * (365.0 / df['DTE'].clip(lower=1))
        df['basis_pts'] = df['F_close'] - df['S_close']
        df.loc[(df['volume'] == 0) | (df['oi'] == 0), 'basis_rate'] = np.nan
        df['br_clean'] = df['basis_rate'].where(df['DTE'] >= 5, np.nan)

        df['rank_252'] = df['br_clean'].rolling(252, min_periods=60).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x.dropna()) >= 30 else np.nan, raw=False)
        df['med90'] = df['br_clean'].rolling(90, min_periods=30).median()
        df['resid'] = df['br_clean'] - df['med90']
        df['std90'] = df['resid'].rolling(90, min_periods=30).std()
        df['z90'] = df['resid'] / df['std90']

        L = df.iloc[-1]
        r, z = L['rank_252'], L['z90']
        if pd.isna(r) or pd.isna(z): sig = '数据不足'
        elif r < 0.05 and z < -2.0: sig = '🔴极端恐慌(做多)'
        elif r < 0.10 and z < -1.5: sig = '🟠深度贴水(观察)'
        elif r > 0.95 and z > 2.0: sig = '🟣异常收敛(做空)'
        elif r < 0.25 and z < -0.5: sig = '🟡温和贴水'
        elif r > 0.75 and z > 0.5: sig = '🟢温和升水'
        else: sig = '⚪中性'

        recent5 = []
        for _, row in df.tail(5).iterrows():
            recent5.append({
                'date': row['date'], 'contract': row['contract'], 'DTE': row['DTE'],
                'basis_rate': round(row['basis_rate']*100, 4) if not pd.isna(row['basis_rate']) else None,
                'rank_252': round(row['rank_252'], 3) if not pd.isna(row['rank_252']) else None,
                'z_score': round(row['z90'], 2) if not pd.isna(row['z90']) else None,
            })

        pctls = {}
        valid = df['br_clean'].dropna()
        if len(valid) > 30:
            for p in [1,5,10,25,50,75,90,95,99]:
                pctls[p] = round(np.percentile(valid, p)*100, 4)

        result[label] = {
            'date': L['date'], 'contract': L['contract'], 'DTE': L['DTE'],
            'F_close': round(L['F_close'], 2), 'S_close': round(L['S_close'], 2),
            'basis_pts': round(L['basis_pts'], 1),
            'basis_rate': round(L['basis_rate']*100, 4) if not pd.isna(L['basis_rate']) else None,
            'rank_252': round(r, 4) if not pd.isna(r) else None,
            'z_score': round(z, 2) if not pd.isna(z) else None,
            'signal': sig, 'recent5': recent5, 'percentiles': pctls,
        }
    return result


# ═══════════════════════════════════
# Part 4: 拼接报告 (纯表格, LLM只写最后综合研判)
# ═══════════════════════════════════

def fmt_val(v, suffix='', digits=2):
    if v is None: return 'N/A'
    try: return f'{float(v):.{digits}f}{suffix}'
    except: return str(v)

def build_report(td, liq, risk, skew_data, skew_date, gex_data, gex_date, basis):
    td_fmt = f'{td[:4]}-{td[4:6]}-{td[6:]}'
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    L = []  # lines

    L.append(f'# 市场综合监控日报 | {td_fmt}')
    L.append(f'\n> 生成时间: {now} | 数据脚本自动采集，综合研判由LLM生成')
    L.append('')

    # ── 一、流动性 ──
    L.append('## 一、流动性评分（60分）')
    L.append('')
    L.append('| 指标 | 数值 | 评分 |')
    L.append('|:---|:---:|:---:|')
    r7 = liq.get('rate_7d'); vol = liq.get('volume'); lscore = liq.get('liquidity_score')
    r7s = 0
    if r7 is not None:
        r7s = 30 if r7<1.5 else 24 if r7<1.8 else 18 if r7<2.0 else 12 if r7<2.5 else 6
    vs = 0
    if vol is not None:
        vs = 30 if vol>1.5 else 24 if vol>=1.2 else 18 if vol>=0.8 else 12 if vol>=0.5 else 6
    L.append(f'| SHIBOR 7天 | {fmt_val(r7, "%", 3)} | {r7s}/30 |')
    L.append(f'| 全A成交额 | {fmt_val(vol, "万亿")} | {vs}/30 |')
    L.append(f'| **流动性总分** | | **{lscore or "N/A"}/60** |')
    L.append('')

    # ── 二、风险偏好 ──
    L.append('## 二、风险偏好评分（40分）')
    L.append('')
    L.append('| 指标 | 数值 | 评分 |')
    L.append('|:---|:---:|:---:|')
    conc = risk.get('concentration'); up = risk.get('up_limit'); down = risk.get('down_limit'); north = risk.get('north_money')
    rscore = risk.get('risk_score')
    cs = 0
    if conc is not None:
        cs = 18 if conc<20 else 15 if conc<25 else 12 if conc<30 else 9 if conc<35 else 6 if conc<40 else 3
    us = 0
    if up is not None: us = 8 if up>100 else 6 if up>=60 else 4 if up>=30 else 2
    ds = 0
    if down is not None: ds = 7 if down<5 else 5 if down<15 else 3 if down<30 else 1
    ns = 0
    if north is not None: ns = 7 if north>80 else 5 if north>=0 else 3 if north>=-50 else 1
    L.append(f'| 成交额前5%占比 | {fmt_val(conc, "%", 1)} | {cs}/18 |')
    L.append(f'| 涨停家数 | {up if up is not None else "N/A"} | {us}/8 |')
    L.append(f'| 跌停家数 | {down if down is not None else "N/A"} | {ds}/7 |')
    L.append(f'| 北向资金 | {fmt_val(north, "亿")} | {ns}/7 |')
    L.append(f'| **风险偏好总分** | | **{rscore or "N/A"}/40** |')
    L.append('')

    # ── 三、期权SKEW ──
    L.append('## 三、期权情绪监控')
    L.append('')
    if skew_data and isinstance(skew_data, dict):
        if skew_date != td:
            L.append(f'> 数据日期: {skew_date}（当日数据尚未入库）')
            L.append('')
        # skew_data 可能是 {results: [...]} 或以标的为key的dict
        skew_list = skew_data.get('results', []) if 'results' in skew_data else []
        if not skew_list:
            # 尝试直接遍历dict
            for k, v in skew_data.items():
                if isinstance(v, dict) and 'atm_iv' in v:
                    v['etf_name'] = v.get('etf_name', k)
                    skew_list.append(v)
        if skew_list:
            L.append('| 标的 | ATM IV | IV Skew | SKEW | Vol PCR | OI PCR | 支撑 | 压力 | 方向 |')
            L.append('|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|')
            def fval(v, s=''):
                if v is None or v == 'N/A': return 'N/A'
                try: return f'{float(v):.2f}{s}'
                except: return str(v)
            for d in skew_list:
                name = d.get('etf_name', d.get('etf_code', ''))
                L.append(f'| {name} | {fval(d.get("atm_iv"),"%")} | {fval(d.get("iv_skew"),"%")} | {fval(d.get("cboe_skew"))} | {fval(d.get("vol_pcr"))} | {fval(d.get("oi_pcr"))} | {d.get("support") or "N/A"} | {d.get("resistance") or "N/A"} | {d.get("direction","N/A")} |')
        else:
            L.append('> ⚠️ SKEW结果为空')
        L.append('')
    else:
        L.append('> ⚠️ 期权SKEW数据获取失败')
        L.append('')

    # ── 四、GEX ──
    L.append('## 四、GEX Gamma挤压分析')
    L.append('')
    if gex_data and isinstance(gex_data, dict):
        if gex_date != td:
            L.append(f'> 数据日期: {gex_date}（当日数据尚未入库）')
            L.append('')
        L.append('| 标的 | 现价 | Net GEX(亿) | Flip Point | 风险 |')
        L.append('|:---|:---:|:---:|:---:|:---|')
        for name, d in gex_data.items():
            if not isinstance(d, dict): continue
            L.append(f'| {name} | {d.get("spot_price","N/A")} | {d.get("net_gex","N/A")} | {d.get("flip_point","N/A")} | {d.get("risk_label","N/A")} |')
        L.append('')
    else:
        L.append('> ⚠️ GEX数据获取失败')
        L.append('')

    # ── 五、IF升贴水 ──
    L.append('## 五、IF股指期货升贴水监控')
    L.append('')
    L.append('> 年化基差率 = (F/S - 1) × (365/DTE) | 双轨: 252日分位数(rank) + 90日中位数残差Z-Score')
    L.append('')
    if basis:
        L.append('### 当前状态')
        L.append('')
        L.append('| 合约 | DTE | 期货 | 现货 | 价差 | 年化基差率 | rank_252 | z_score | 信号 |')
        L.append('|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|')
        for label in ['当季', '下季']:
            bd = basis.get(label)
            if bd is None:
                L.append(f'| {label} | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 无数据 |')
                continue
            rk = f'{bd["rank_252"]:.4f}' if bd['rank_252'] is not None else 'N/A'
            zs = f'{bd["z_score"]:+.2f}' if bd['z_score'] is not None else 'N/A'
            br = f'{bd["basis_rate"]:+.4f}%' if bd['basis_rate'] is not None else 'N/A'
            pts = f'{bd["basis_pts"]:+.1f}' if bd['basis_pts'] is not None else 'N/A'
            L.append(f'| {bd["contract"]}({label}) | {bd["DTE"]}d | {bd["F_close"]} | {bd["S_close"]} | {pts} | {br} | {rk} | {zs} | {bd["signal"]} |')
        L.append('')

        # 近5日
        for label in ['当季', '下季']:
            bd = basis.get(label)
            if bd and bd.get('recent5'):
                L.append(f'#### {label}近5日')
                L.append('')
                L.append('| 日期 | 合约 | DTE | 年化基差率 | rank | Z |')
                L.append('|:---:|:---:|:---:|:---:|:---:|:---:|')
                for r5 in bd['recent5']:
                    rk5 = f'{r5["rank_252"]:.3f}' if r5['rank_252'] is not None else 'N/A'
                    z5 = f'{r5["z_score"]:+.2f}' if r5['z_score'] is not None else 'N/A'
                    br5 = f'{r5["basis_rate"]:+.4f}%' if r5['basis_rate'] is not None else 'N/A'
                    L.append(f'| {r5["date"]} | {r5["contract"]} | {r5["DTE"]}d | {br5} | {rk5} | {z5} |')
                L.append('')

        # 信号定义
        L.append('> 信号: 🔴极端恐慌(rank<0.05且Z<-2.0) | 🟠深度贴水(rank<0.10且Z<-1.5) | 🟡温和贴水(rank<0.25且Z<-0.5) | ⚪中性 | 🟢温和升水(rank>0.75且Z>0.5) | 🟣异常收敛(rank>0.95且Z>2.0)')
        L.append('')
    else:
        L.append('> ⚠️ IF基差数据获取失败')
        L.append('')

    # ── 六、综合评分 ──
    L.append('## 六、综合评分')
    L.append('')
    total = (lscore + rscore) if (lscore is not None and rscore is not None) else None
    L.append(f'| 维度 | 评分 |')
    L.append(f'|:---|:---:|')
    L.append(f'| 流动性 | {lscore or "N/A"}/60 |')
    L.append(f'| 风险偏好 | {rscore or "N/A"}/40 |')
    L.append(f'| **综合** | **{total or "N/A"}/100** |')
    L.append('')

    # ── 七、LLM综合研判占位 ──
    L.append('## 七、综合研判')
    L.append('')
    L.append('<!-- LLM_WRITE_HERE -->')
    L.append('')
    L.append('> 以下由LLM基于上方数据统一撰写')
    L.append('')
    L.append('___COMPREHENSIVE_JUDGMENT___')
    L.append('')

    # ── 底部 ──
    L.append('---')
    L.append('*数据来源: tushare | 案例分析学习材料，不构成投资建议*')

    return '\n'.join(L)


# ═══════════════════════════════════
# Main
# ═══════════════════════════════════

def main():
    td = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
    print(f'市场综合监控日报 | {td}')
    print('='*50)

    # 1. 流动性+风险偏好
    print('[1/4] 流动性+风险偏好...')
    r7, shibor_dt = get_shibor_7d()
    vol, conc = get_trading_volume(td)
    up, down = get_limit_list(td)
    north = get_north_money(td)
    lscore = calc_liquidity_score(r7, vol)
    rscore = calc_risk_score(conc, up, down, north)
    liq = {'rate_7d': r7, 'volume': vol, 'liquidity_score': lscore}
    risk = {'concentration': conc, 'up_limit': up, 'down_limit': down, 'north_money': north, 'risk_score': rscore}
    print(f'  SHIBOR={r7} 成交额={vol}万亿 流动性={lscore}/60 风险偏好={rscore}/40')

    # 2. SKEW
    print('[2/4] 期权SKEW...')
    skew_data, skew_date = get_skew_data(td)
    print(f'  SKEW: {"OK("+skew_date+")" if skew_data else "失败"}')

    # 3. GEX
    print('[3/4] GEX...')
    gex_data, gex_date = get_gex_data(td)
    print(f'  GEX: {"OK("+gex_date+")" if gex_data else "失败"}')

    # 4. IF基差
    print('[4/4] IF升贴水...')
    basis = compute_if_basis(td)
    if basis:
        for lb in ['当季','下季']:
            bd = basis.get(lb)
            if bd: print(f'  {lb}: {bd["contract"]} DTE={bd["DTE"]}d 基差={bd["basis_rate"]:+.4f}% rank={bd["rank_252"]} Z={bd["z_score"]} {bd["signal"]}')
    else:
        print('  IF基差: 失败')

    # 生成报告
    report = build_report(td, liq, risk, skew_data, skew_date, gex_data, gex_date, basis)
    td_fmt = f'{td[:4]}-{td[4:6]}-{td[6:]}'
    out_path = os.path.join(OUTPUT_DIR, f'{td_fmt}_市场综合监控.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'\n报告已保存: {out_path}')
    return out_path

if __name__ == '__main__':
    main()
