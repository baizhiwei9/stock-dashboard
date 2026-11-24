#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ξ+D结构风控 x 扩散+RRG 每日报告
================================
每日自动运行 (收盘后):
  1. 数据增量: 行业指数 / day截面 / diffusion 补到最新交易日
  2. ξ+D 结构风控: 最新交易日60日窗口 关联长度ξ(相关矩阵特征值间隙) + 分形维D(Higuchi)
     -> 各自历史滚动分位(防前视) -> 三档仓位(100/75/40%) -> 等权合并 = 目标总仓位
  3. 扩散+RRG选行业: 最新交易日扩散TOP6 + RRG象限过滤 (当前持仓 + 最新信号预览)
  4. 宽基板块分析: 10个主要宽基最新收盘/涨跌幅/趋势状态
  5. 飞书推送: 交互卡片(内容在前、署名「来自 DoMate」挂标题末尾) + 图表 + HTML附件

用法: python daily_tda_rrg_report.py [--skip-update] [--target YYYYMMDD] [--dry-run]
注: 风控信号依据 v2/v3 回测结论 (ξ+D 等权组合, 沪深300 2022-2026 +14.08%), 窗口适配本任务 60 日 x 30 行业。
选行业保持原 扩散+RRG 不变。原 TDA 脆弱性控仓已移除。
"""
import socket, os, sys, json, time, argparse


# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))
for p in ['/tmp/.pip-global/lib/python3.12/site-packages']:
    if p not in sys.path: sys.path.insert(0, p)
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(30)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.family'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data_rrg')
REPO_TDA = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0'
sys.path.insert(0, REPO_TDA)

FEISHU_HEADER = '来自 DoMate'

# ================= 数据增量 =================

def update_data(target):
    import fetch_rrg_incremental as fi
    fi.update_industry(target)
    fi.update_day(target)
    fi.rebuild_diffusion(target)

# ================= ξ+D 结构风控 =================

# 扩散+RRG 选行业 =================

def diff_rrg_signal(prices, diffusion, rrg, target_date, week_end=False):
    """周度调仓点(每周最后交易日)或最新交易日的扩散+RRG信号"""
    t = pd.Timestamp(target_date)
    q = {c: rrg[c].loc[t] for c in prices.columns}
    diff_t = diffusion.loc[t] if t in diffusion.index else pd.Series(dtype=float)
    topd = diff_t.dropna().sort_values(ascending=False)
    top6d = topd.index[:6].tolist()
    keep = [c for c in top6d if pd.notna(q.get(c, {}).get('quadrant', np.nan)) and int(q.get(c, {}).get('quadrant', -1)) in (1, 2)]
    rows = []
    for c in top6d:
        qd = q.get(c, {})
        _quad = qd.get('quadrant', -1)
        _dist = qd.get('dist', 0)
        rows.append({'industry': c, 'diffusion': round(float(diff_t[c]), 3),
                     'quadrant': int(_quad) if pd.notna(_quad) else -1,
                     'dist': round(float(_dist), 2) if pd.notna(_dist) else 0.0,
                     'selected': c in keep})
    return {'date': t, 'top6': rows, 'holdings': keep, 'n_hold': len(keep)}

def latest_holding(prices, diffusion, rrg, target_date):
    """当前实际持仓: 最近一个已过周度调仓点(<= target)的 keep 行业"""
    import rrg_engine
    p = prices[prices.index <= pd.Timestamp(target_date)]
    we_idx = rrg_engine.week_end_indices(p)
    if len(we_idx) == 0:
        return None
    last_i = we_idx[-1]
    t = p.index[last_i]
    return diff_rrg_signal(prices, diffusion, rrg, t)

# ================= 持仓行业龙头股 =================

def load_stock_basic(pro=None):
    """股票代码->名称映射 (tushare stock_basic 缓存到 state/stock_basic.csv)"""
    fp = os.path.join(BASE, 'state', 'stock_basic.csv')
    if os.path.exists(fp) and (time.time() - os.path.getmtime(fp)) < 30*86400:
        try:
            df = pd.read_csv(fp)
            if len(df) > 3000:
                return dict(zip(df['ts_code'], df['name']))
        except Exception:
            pass
    try:
        if pro is None:
            from ts_fast import init_tushare
            pro = init_tushare()
        cd = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        if cd is None or len(cd) == 0:
            return {}
        if len(cd) > 3000:
            cd.to_csv(fp, index=False)
        return dict(zip(cd['ts_code'], cd['name']))
    except Exception as e:
        print(f'  warn: stock_basic fetch failed: {e}')
        return {}

def leader_stocks(target, holdings, lookback=10, topn=3):
    """持仓行业近 lookback 交易日涨幅靠前龙头股
    返回 {industry: [(code, name, ret, circ_mv_wany), ...]}
    口径: t日收盘算作可用(报告于收盘后推送), 涨幅=最新adj_close/窗口首日adj_close-1
    """
    import rrg_data
    pidx = rrg_data.load_industry_prices().index
    dates = list(pidx[pidx <= pd.Timestamp(target)])[-(lookback+1):]
    if len(dates) < lookback + 1:
        return {}
    frames = []
    for d in dates:
        fp = os.path.join(DATA, 'day', d.strftime('%Y%m%d') + '.csv')
        if not os.path.exists(fp):
            continue
        df = pd.read_csv(fp, usecols=['ts_code', 'adj_close', 'circ_mv'])
        df['date'] = d
        frames.append(df)
    if not frames:
        return {}
    panel = pd.concat(frames, ignore_index=True)
    cons = pd.read_csv(os.path.join(DATA, 'constituents.csv'))
    nmap = load_stock_basic()
    first_d, last_d = dates[0], dates[-1]
    out = {}
    for ind in holdings:
        codes = cons[cons['industry'] == ind]['con_code'].tolist()
        sub = panel[panel['ts_code'].isin(codes)]
        if not len(sub):
            continue
        sf = sub[sub['date'] == first_d].set_index('ts_code')['adj_close']
        sl = sub[sub['date'] == last_d].set_index('ts_code')
        common = sf.index.intersection(sl.index)
        if len(common) == 0:
            continue
        ret = sl.loc[common, 'adj_close'] / sf.loc[common] - 1
        mv = sl.loc[common, 'circ_mv']
        tab = pd.DataFrame({'ret': ret, 'mv': mv}).dropna().sort_values('ret', ascending=False)
        rows = [(c, nmap.get(c, ''), round(float(r['ret']), 4), float(r['mv']))
                for c, r in tab.head(topn).iterrows()]
        out[ind] = rows
    return out

# ================= 宽基板块分析 =================

def _hub_index_daily(ts_code, start, end):
    """宽基指数日线降级: DataHub CN.MARKET.INDEX.DAILY"""
    try:
        import sys as _s
        for _p in ('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps',
                   '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-multi-tier'):
            if _p not in _s.path:
                _s.path.insert(0, _p)
        from data_hub import edb
        df = edb.get_dataset('CN.MARKET.INDEX.DAILY', codes=[ts_code], start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        df = df.copy()
        df['trade_date'] = df['trade_date'].astype(str)
        return df
    except Exception as e:
        print(f'  warn: hub index {ts_code} failed {str(e)[:60]}', flush=True)
        return None


def broad_indices(pro, target_date, lookback=90):
    """10个主要宽基指数的最新收盘/多周期涨跌/趋势状态"""
    idx_map = {
        '000300.SH': '沪深300', '000905.SH': '中证500', '000852.SH': '中证1000',
        '399006.SZ': '创业板指', '000016.SH': '上证50', '000688.SH': '科创50',
        '000001.SH': '上证指数', '399001.SZ': '深证成指', '000906.SH': '中证800',
        '932000.CSI': '中证2000',
    }
    t = pd.Timestamp(target_date)
    start = (t - pd.Timedelta(days=lookback * 2)).strftime('%Y%m%d')
    end = t.strftime('%Y%m%d')
    rows = []
    for ts_code, name in idx_map.items():
        df = None
        try:
            df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            df = _hub_index_daily(ts_code, start, end)
        if df is None or len(df) == 0:
            rows.append({'name': name, 'code': ts_code, 'close': None,
                         'pct_d': None, 'pct_5d': None, 'pct_20d': None, 'pct_60d': None,
                         'trend': 'N/A'})
            continue
        try:
            df = df.sort_values('trade_date').reset_index(drop=True)
            c = df['close'].values.astype(float)
            p = df['pct_chg'].values if 'pct_chg' in df.columns else None
            close = float(c[-1])
            pct_d = float(p[-1]) if p is not None and len(p) > 0 else None
            pct_5d = float((c[-1] / c[-6] - 1) * 100) if len(c) >= 6 else None
            pct_20d = float((c[-1] / c[-21] - 1) * 100) if len(c) >= 21 else None
            pct_60d = float((c[-1] / c[-61] - 1) * 100) if len(c) >= 61 else None
            ma20 = df['close'].rolling(20).mean().values
            ma60 = df['close'].rolling(60).mean().values
            if len(ma20) > 0 and len(ma60) > 0 and not np.isnan(ma20[-1]) and not np.isnan(ma60[-1]):
                if close > ma20[-1] and ma20[-1] > ma60[-1]:
                    trend = '上升'
                elif close < ma20[-1] and ma20[-1] < ma60[-1]:
                    trend = '下降'
                else:
                    trend = '震荡'
            else:
                trend = '数据不足'
            rows.append({'name': name, 'code': ts_code, 'close': close,
                         'pct_d': pct_d, 'pct_5d': pct_5d, 'pct_20d': pct_20d,
                         'pct_60d': pct_60d, 'trend': trend})
        except Exception:
            rows.append({'name': name, 'code': ts_code, 'close': None,
                         'pct_d': None, 'pct_5d': None, 'pct_20d': None, 'pct_60d': None,
                         'trend': '获取失败'})
    return rows

# ================= 图表 ===============

def draw_charts(xid_df, pos_hist, xid_res, sig_cur, leaders, outdir):
    os.makedirs(outdir, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(17, 10))

    # 图1: ξ 关联长度历史
    ax = axes[0, 0]
    if len(xid_df) > 1:
        ax.plot(xid_df.index, xid_df['xi'].values, '-o', ms=2.5, color='#2c3e50', label='ξ 关联长度(历史)')
    if xid_res.get('xi') is not None:
        ax.axhline(xid_res['xi'], color='#e74c3c', ls='--', lw=1.5,
                   label=f"今日 ξ={xid_res['xi']} (分位 {xid_res['q_xi']:.0%}, 仓位 {xid_res['xi_pos']:.0%})")
    ax.set_title('ξ 关联长度 | 相关矩阵特征值间隙 (高=市场强同步->低仓)')
    ax.set_ylabel('ξ'); ax.legend(fontsize=8.5); ax.grid(alpha=0.3)

    # 图2: D 分形维历史
    ax = axes[0, 1]
    if len(xid_df) > 1:
        ax.plot(xid_df.index, xid_df['D'].values, '-o', ms=2.5, color='#8e44ad', label='D 分形维(历史)')
    if xid_res.get('D') is not None:
        ax.axhline(xid_res['D'], color='#e74c3c', ls='--', lw=1.5,
                   label=f"今日 D={xid_res['D']} (分位 {xid_res['q_D']:.0%}, 仓位 {xid_res['d_pos']:.0%})")
    ax.set_title('D 分形维 | Higuchi (高=市场乱度高->低仓)')
    ax.set_ylabel('D'); ax.legend(fontsize=8.5); ax.grid(alpha=0.3)

    # 图3: 组合仓位历史
    ax = axes[0, 2]
    if len(pos_hist) > 1:
        ax.fill_between(pos_hist.index, pos_hist.values, 0, color='#2ca02c', alpha=0.35)
        ax.plot(pos_hist.index, pos_hist.values, color='#2ca02c', lw=1.2)
        ax.set_ylim(0, 1.15)
    ax.axhline(xid_res['position'], color='#e74c3c', ls='--', lw=1.5,
               label=f"今日组合仓位 {xid_res['position']:.0%}")
    ax.set_title('ξ+D 组合仓位历史 (100/75/40% 三档等权)')
    ax.set_ylabel('仓位'); ax.legend(fontsize=8.5); ax.grid(alpha=0.3)

    # 图4: 扩散TOP6得分
    ax = axes[1, 0]
    r = sig_cur['top6']
    names = [x['industry'] for x in r]
    vals = [x['diffusion'] for x in r]
    colors = ['#e74c3c' if x['selected'] else '#95a5a6' for x in r]
    bars = ax.bar(range(len(names)), vals, color=colors, alpha=0.85)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('扩散指标'); ax.set_ylim(0, 1.1)
    ax.set_title(f"扩散TOP6行业 (红={sig_cur['date'].date()} 选中持仓, 灰=RRG过滤)")
    for b, x in zip(bars, r):
        ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.02, f"Q{x['quadrant']}",
                ha='center', va='bottom', fontsize=9)
    ax.grid(alpha=0.3, axis='y')

    # 图5: 持仓行业龙头股(文字表)
    ax = axes[1, 1]
    ax.axis('off')
    heads = ['持仓行业龙头股 (近10交易日涨幅, 周频调仓口径)']
    if leaders:
        for ind, rows in leaders.items():
            heads.append(f"【{ind}】")
            for code, name, ret, mv in rows:
                nm = name if name else code
                heads.append(f"  {nm} ({code})  近10日 {ret*100:+.1f}%  流通市值 {mv/1e4:.0f}亿")
    else:
        heads.append('  当前无持仓 / 龙头股数据缺失')
    ax.text(0.02, 0.98, '\n'.join(heads), va='top', ha='left', fontsize=9.5, linespacing=1.6,
            family=['DejaVu Sans', 'Droid Sans Fallback'])
    ax.set_title('持仓行业龙头股')

    # 图6: 龙头股近10日涨幅条形
    ax = axes[1, 2]
    flat = []
    for ind, rows in leaders.items():
        for code, name, ret, mv in rows:
            flat.append((ind, name or code, ret))
    if flat:
        flat = flat[:12]
        labels = [f"{n}\n({i})" for i, n, _ in flat]
        vals = [r * 100 for _, _, r in flat]
        colors = ['#e74c3c' if v > 0 else '#2980b9' for v in vals]
        bars = ax.barh(range(len(flat))[::-1], vals, color=colors, alpha=0.85)
        ax.set_yticks(range(len(flat)))
        ax.set_yticklabels(labels, fontsize=8.5)
        ax.axvline(0, color='#7f8c8d', lw=0.8)
        ax.set_xlabel('近10日涨跌%')
        for b, v in zip(bars, vals):
            ax.text(v + (0.5 if v >= 0 else -0.5), b.get_y() + b.get_height()/2,
                    f'{v:+.1f}%', va='center', ha='left' if v >= 0 else 'right', fontsize=8)
    else:
        ax.text(0.5, 0.5, '暂无龙头股数据', ha='center', va='center')
    ax.set_title('龙头股近10日涨幅 (前12)')
    ax.grid(alpha=0.3, axis='x')

    fig.suptitle(f'ξ+D 结构风控 x 扩散+RRG 每日组合报告 | {xid_res["date"].date()}', fontsize=14, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    p = os.path.join(outdir, 'daily_charts.png')
    fig.savefig(p, dpi=105)
    plt.close(fig)
    return p

# ================= HTML 报告 =================

def build_html(xid_res, sig_cur, sig_latest, leaders, broad_data, outdir):
    def pct(x):
        return f'{x:.2f}%' if pd.notna(x) else '-'
    def _broad_cell(v):
        if v is not None and pd.notna(v):
            cls = 'good' if v > 0 else 'bad'
            return f'<td class="{cls}">{v:+.2f}%</td>'
        return '<td>-</td>'
    broad_rows = ''
    if broad_data:
        for r in broad_data:
            cs = f'{r["close"]:.2f}' if r.get('close') is not None else '-'
            broad_rows += (f'<tr><td>{r["name"]}</td><td>{cs}</td>'
                          f'{_broad_cell(r.get("pct_d"))}{_broad_cell(r.get("pct_5d"))}'
                          f'{_broad_cell(r.get("pct_20d"))}{_broad_cell(r.get("pct_60d"))}'
                          f'<td>{r["trend"]}</td></tr>\n')
    else:
        broad_rows = '<tr><td colspan="7" class="mute">宽基数据获取失败</td></tr>'
    hold = ', '.join(sig_latest['holdings']) if sig_latest and sig_latest['holdings'] else '(空仓)'
    prev = ', '.join(sig_cur['holdings']) if sig_cur['holdings'] else '(空仓)'
    sig_rows = '\n'.join(
        f'<tr><td>{x["industry"]}</td><td>{x["diffusion"]:.3f}</td><td>Q{x["quadrant"]}</td>'
        f'<td>{x["dist"]:.2f}</td><td>{"选中" if x["selected"] else "过滤"}</td></tr>'
        for x in sig_cur['top6'])
    ld_rows = '\n'.join(
        f'<tr><td>{ind}</td><td>{name or code}</td><td>{code}</td>'
        f'<td class="{'good' if ret > 0 else 'bad'}">{ret*100:+.1f}%</td>'
        f'<td>{mv/1e4:.0f}亿</td></tr>'
        for ind, rows in leaders.items() for code, name, ret, mv in rows) if leaders else \
        '<tr><td colspan="5" class="mute">当前无持仓 / 龙头股数据缺失</td></tr>'
    xi_s = f"{xid_res['xi']}" if xid_res.get('xi') is not None else '—'
    d_s = f"{xid_res['D']}" if xid_res.get('D') is not None else '—'
    qxi_s = f"{xid_res['q_xi']:.0%}" if xid_res.get('q_xi') is not None else '—'
    qd_s = f"{xid_res['q_D']:.0%}" if xid_res.get('q_D') is not None else '—'
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>ξ+D 结构风控 x 扩散+RRG 每日报告 {xid_res['date'].date()}</title>
<style>
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;margin:24px;color:#222;background:#fafafa}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-left:4px solid #2ca02c;padding-left:8px}}
.card{{background:#fff;border-radius:8px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #ddd;padding:6px 10px;text-align:center}}
th{{background:#f5f5f5}} .pos{{color:#fff;background:#2ca02c;padding:2px 10px;border-radius:12px;font-weight:bold}}
.good{{color:#e74c3c}} .bad{{color:#2980b9}} .mute{{color:#95a5a6}}
.footer{{color:#999;font-size:12px;margin-top:24px;text-align:center}}
</style></head><body>
<h1>ξ+D 结构风控 x 扩散+RRG 每日组合报告</h1>
<p>报告日期 {xid_res['date'].date()} | 数据截至最新交易日 | 来自 DoMate</p>
<div class="card"><h2>一、ξ+D 结构风控（选行业仓控）</h2>
<b>关联长度 ξ:</b> {xi_s}（历史分位 {qxi_s} → 仓位 <b>{xid_res['xi_pos']:.0%}</b>）
&nbsp;|&nbsp; <b>分形维 D:</b> {d_s}（历史分位 {qd_s} → 仓位 <b>{xid_res['d_pos']:.0%}</b>）
&nbsp;|&nbsp; <b>组合目标仓位:</b> <span class="pos">{xid_res['position']:.0%}</span>
<p class="mute">信号口径: 60日窗口 x 30行业。ξ=相关矩阵特征值间隙（低关联=市场自发分散→满仓100%，高关联=系统性共振→40%）；D=等权市场收益 Higuchi 分形维（低乱度→满仓，高乱度→40%）；各自相对此前全部历史的分位三档(100/75/40%)，等权合并为总仓位。防前视: 只用当日之前历史算分位。依据: 沪深300 2022-2026 回测 ξ+D 等权组合 +14.08%。</p></div>
<div class="card"><h2>二、扩散+RRG 行业轮动</h2>
<b>当前持仓 (最近周末调仓):</b> {hold}<br>
<b>最新信号预览 (若今日调仓):</b> {prev}
<table><tr><th>行业</th><th>扩散指标</th><th>RRG象限</th><th>距原点</th><th>入选</th></tr>{sig_rows}</table>
<p class="mute">策略为周频调仓: 每周最后交易日选扩散TOP6中处RRG 1/2象限的行业等权持有。今日信号仅供持仓监控参考。</p></div>
<div class="card"><h2>三、持仓行业龙头股 (近10交易日涨幅)</h2>
<table><tr><th>行业</th><th>股票</th><th>代码</th><th>近10日涨幅</th><th>流通市值</th></tr>{ld_rows}</table>
<p class="mute">龙头股口径: 持仓行业内近10个交易日 adj_close 涨幅排名前3 (报告于收盘后推送, 仅供选股参考, 不构成投资建议)。</p></div>
<div class="card"><h2>四、宽基板块分析</h2>
<table><tr><th>宽基指数</th><th>最新收盘</th><th>当日%</th><th>5日%</th><th>20日%</th><th>60日%</th><th>趋势</th></tr>
{broad_rows}</table>
<p class="mute">趋势判定: close>MA20>MA60=上升, close&lt;MA20&lt;MA60=下降, 其余=震荡。数据来源: Tushare index_daily。</p></div>
<div class="footer">Generated by DuMate · 策略框架: 西部证券RRG行业轮动 + ξ关联长度×D分形维结构风控</div>
</body></html>"""
    html = html.replace('</body></html>', _bs('rrg', 'html') + '</body></html>', 1)
    p = os.path.join(outdir, f'daily_report_{xid_res["date"].strftime("%Y%m%d")}.html')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(html)
    return p

# ================= 飞书推送 =================

def push_feishu(png_path, html_path, xid_res, sig_cur, sig_latest, leaders, broad_data):
    sys.path.insert(0, BASE)
    try:
        from feishu_config import (APP_ID, APP_SECRET, WEBHOOK_URL, TOKEN_URL,
                                   UPLOAD_URL, CHAT_ID, FILE_UPLOAD_URL, MSG_SEND_URL)
    except Exception as e:
        print(f'feishu config import failed: {e}')
        return False
    import requests
    try:
        resp = requests.post(TOKEN_URL, json={'app_id': APP_ID, 'app_secret': APP_SECRET}, timeout=15)
        token = resp.json()['tenant_access_token']
        image_keys = []
        for p in (png_path,):
            if not os.path.exists(p):
                continue
            with open(p, 'rb') as f:
                r = requests.post(UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                                  data={'image_type': 'message'}, files={'image': f}, timeout=30)
            ik = r.json().get('data', {}).get('image_key')
            image_keys.append(ik)
        for ik in image_keys:
            if ik:
                requests.post(WEBHOOK_URL, json={'msg_type': 'image', 'content': {'image_key': ik}}, timeout=20)

        # 交互卡片
        hold_str = ', '.join(sig_latest['holdings']) if sig_latest and sig_latest['holdings'] else '(空仓)'
        ld_txt = '\n'.join(
            f"**{ind}** " + '、'.join(f"{name or code}({ret*100:+.1f}%)" for code, name, ret, _ in rows[:3])
            for ind, rows in leaders.items()) if leaders else '当前无持仓'
        def _bc(r):
            cs = f'{r["close"]:.0f}' if r.get('close') is not None else '-'
            def _p(k):
                v = r.get(k)
                return f'{v:+.1f}%' if v is not None and pd.notna(v) else '-'
            return f"{r['name']} {cs} (当日{_p('pct_d')} 5日{_p('pct_5d')} 20日{_p('pct_20d')} 60日{_p('pct_60d')}) {r['trend']}"
        broad_txt = '\n'.join(_bc(r) for r in broad_data) if broad_data else '宽基数据获取失败'
        xi_txt = f"{xid_res['xi']}" if xid_res.get('xi') is not None else '—'
        d_txt = f"{xid_res['D']}" if xid_res.get('D') is not None else '—'
        qxi_txt = f"{xid_res['q_xi']:.0%}" if xid_res.get('q_xi') is not None else '—'
        qd_txt = f"{xid_res['q_D']:.0%}" if xid_res.get('q_D') is not None else '—'
        # 检测到风险：建议总仓位<60%（红色档）→ 风险提示置顶 + 标题标记
        has_risk = xid_res['position'] < 0.6
        has_opp = xid_res['position'] >= 0.8
        _risk_broad = [r['name'] for r in (broad_data or []) if r.get('trend') == '下降']
        _els = [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**ξ+D结构风控 × 扩散+RRG行业轮动 | 数据截至 {xid_res['date'].date()}**"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**一、ξ+D 结构风控**\n关联长度 ξ: {xi_txt} (分位 {qxi_txt} → 仓位 {xid_res['xi_pos']:.0%}) | 分形维 D: {d_txt} (分位 {qd_txt} → 仓位 {xid_res['d_pos']:.0%})\n**建议总仓位: {xid_res['position']:.0%}**"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**二、行业持仓**\n当前持仓: **{hold_str}**"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**三、持仓行业龙头股** (近10交易日涨幅)\n{ld_txt}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**四、宽基板块趋势**\n{broad_txt}"}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text",
             "content": "周频调仓信号, 每日监控 | ξ+D仓位为组合总仓位(等权行业) | 完整指标见HTML附件"}]},
        ]
        if has_risk:
            _risk_txt = f"**⚠️ 检测到风险：建议总仓位降至 {xid_res['position']:.0%}**"
            if _risk_broad:
                _risk_txt += f"｜宽基走弱：{'、'.join(_risk_broad)}"
            _els.insert(1, {"tag": "div", "text": {"tag": "lark_md", "content": _risk_txt}})
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text",
                       "content": f"ξ+D × 扩散+RRG 每日报告 | {xid_res['date'].date()} | {'注意风险 | ' if has_risk else ''}{'注意机会 | ' if has_opp else ''}来自 DoMate"},
                       "template": "red" if xid_res['position'] < 0.6 else ("orange" if xid_res['position'] < 0.8 else "green")},
            "elements": _els,
        }
        requests.post(WEBHOOK_URL, json={'msg_type': 'interactive', 'card': card}, timeout=20)

        # HTML 附件
        if os.path.exists(html_path):
            with open(html_path, 'rb') as f:
                r = requests.post(FILE_UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                                  data={'file_type': 'stream', 'file_name': os.path.basename(html_path)},
                                  files={'file': f}, timeout=30)
            fk = r.json().get('data', {}).get('file_key')
            if fk:
                requests.post(MSG_SEND_URL, params={'receive_id_type': 'chat_id'}, timeout=20,
                              headers={'Authorization': f'Bearer {token}'},
                              json={'receive_id': CHAT_ID,
                                    'msg_type': 'file',
                                    'content': json.dumps({'file_key': fk})})
        print('feishu pushed OK')
        return True
    except Exception as e:
        print(f'feishu push failed: {e}')
        return False

# ================= 主流程 =================

def main():
    import rrg_data, rrg_engine
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-update', action='store_true')
    ap.add_argument('--target', default=None, help='YYYYMMDD')
    ap.add_argument('--dry-run', action='store_true', help='只生成报告不推送')
    ap.add_argument('--force', action='store_true', help='强制重发(忽略当日已推送)')
    args = ap.parse_args()

    from ts_fast import init_tushare
    pro = init_tushare()

    # 目标日: 默认最新交易日(tushare交易日历)
    if args.target:
        target = args.target
    else:
        import fetch_rrg_incremental as fi
        target = fi.last_trade_date()

    if not args.skip_update:
        print('=== 1. 数据增量更新 ===', flush=True)
        update_data(target)

    print('=== 2. 计算信号 ===', flush=True)
    diffusion = pd.read_csv(os.path.join(DATA, 'diffusion.csv'), index_col=0, parse_dates=True)
    prices = rrg_data.load_industry_prices()
    rrg = rrg_engine.compute_rrg(prices)

    # 行业指数可能晚于个股数据可用, 回退到最新可用交易日
    # 注: 宽基指数(index_daily)独立可用最新交易日, 不跟随行业指数回退
    latest_td = target
    if pd.Timestamp(target) not in prices.index:
        old_target = target
        target = prices.index[-1].strftime('%Y%m%d')
        print(f'  行业指数 {old_target} 暂无数据, 回退到 {target}', flush=True)

    # 幂等保护: 当日已推送则跳过 (除非 --force)
    daily_cache = os.path.join(BASE, 'state', 'xid_daily.csv')
    if os.path.exists(daily_cache) and not args.force and not args.dry_run:
        done = pd.read_csv(daily_cache, index_col=0, parse_dates=True)
        if pd.Timestamp(target) in done.index:
            print(f'{target} 已推送过，跳过 (--force 可重发)', flush=True)
            return

    import xid_risk
    hist = xid_risk.ensure_history(prices)
    xid_res = xid_risk.compute_position(prices, target, hist)
    xid_risk.save_daily(target, xid_res)
    print(f'  今日信号: ξ={xid_res.get("xi")} q_xi={xid_res.get("q_xi")} xi_pos={xid_res.get("xi_pos")} '
          f'D={xid_res.get("D")} q_D={xid_res.get("q_D")} d_pos={xid_res.get("d_pos")} '
          f'position={xid_res.get("position")}', flush=True)

    sig_cur = diff_rrg_signal(prices, diffusion, rrg, target, week_end=False)
    sig_latest = latest_holding(prices, diffusion, rrg, target)
    print(f'  当前持仓(最近周末调仓): {sig_latest["holdings"] if sig_latest else "无"}')
    print(f'  最新信号: {sig_cur["holdings"]}')

    print('=== 3. 持仓行业龙头股 ===', flush=True)
    holdings_list = sig_latest['holdings'] if sig_latest and sig_latest['holdings'] else []
    leaders = leader_stocks(target, holdings_list) if holdings_list else {}
    for ind, rows in leaders.items():
        print(f'  {ind}: ' + ', '.join(f"{n or c}({r*100:+.1f}%)" for c, n, r, _ in rows))

    print('=== 3b. 宽基板块分析 ===', flush=True)
    broad_data = broad_indices(pro, latest_td)
    for r in broad_data:
        cs = f'{r["close"]:.2f}' if r.get('close') is not None else '-'
        print(f"  {r['name']} {cs} 趋势={r['trend']}")

    print('=== 4. 生成报告 ===', flush=True)
    outdir = os.path.join(BASE, 'reports')
    pos_hist = xid_risk.position_history(hist)
    png = draw_charts(hist, pos_hist, xid_res, sig_cur, leaders, outdir)
    html = build_html(xid_res, sig_cur, sig_latest, leaders, broad_data, outdir)
    print(f'  png: {png}')
    print(f'  html: {html}')

    if not args.dry_run:
        print('=== 5. 推送飞书 ===', flush=True)
        ok = push_feishu(png, html, xid_res, sig_cur, sig_latest, leaders, broad_data)
        print('pushed' if ok else 'push failed')
    print('DONE', flush=True)

if __name__ == '__main__':
    main()