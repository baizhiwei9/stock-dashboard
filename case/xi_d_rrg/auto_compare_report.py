# -*- coding: utf-8 -*-
"""成长价值轮动 vs RRG行业轮动 —— 同期对比报告 (幂等)
流程: 检查数据齐(gy+rrg) -> 跑成长价值回测 -> 跑RRG三类策略回测
      -> 对比图(净值/超额) -> HTML报告 -> 飞书推送(图片+卡片+HTML)
用法: python auto_compare_report.py
"""
import os, sys, re, json, time, socket, glob, base64
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig_gai(*a, **kw) if r[0] == socket.AF_INET]

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
DATA_RRG = os.path.join(BASE, 'data_rrg')
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'pylib'))
import numpy as np
import pandas as pd
import rrg_engine  # perf_stats 等

START, END = '20220101', '20260822'


# ---------- 0. 数据检查 ----------
def gy_ready():
    codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()
    n = len(codes)
    nd_path = os.path.join(DATA, 'no_data.json')
    no_data = json.load(open(nd_path)) if os.path.exists(nd_path) else {}
    for k in ['moneyflow', 'margin', 'income', 'daily_basic']:
        d = os.path.join(DATA, k)
        files = [f for f in os.listdir(d) if f.endswith('.csv') and not re.match(r'^\d{6}\.csv$', f)] if os.path.isdir(d) else []
        nd = no_data.get(k, [])
        need = n - len(nd)
        if len(files) < need:
            return False, k, len(files), need
    return True, 'ok', 0, n


def rrg_ready():
    ind = glob.glob(os.path.join(DATA_RRG, 'industry', '*.csv'))
    day = glob.glob(os.path.join(DATA_RRG, 'day', '*.csv'))
    if len(ind) < 30 or len(day) < 200:
        return False, len(ind), len(day)
    last = os.path.basename(sorted(day)[-1])[:8]
    if last < '20220101':
        return False, len(ind), len(day)
    return True, len(ind), len(day)


# ---------- 1. 成长价值回测 ----------
def run_gy(with_stock=True):
    import repro_engine as re
    m, results = re.run_repro(start=START, end=END, with_stock=with_stock, W=120)
    return re, m, results


# ---------- 2. RRG 回测 ----------
def run_rrg():
    import rrg_data, rrg_engine
    # 扩散指标(未落盘则计算)
    diff_path = os.path.join(DATA_RRG, 'diffusion.csv')
    if not os.path.exists(diff_path):
        diffusion = rrg_data.build_diffusion()
    else:
        diffusion = pd.read_csv(diff_path, index_col=0, parse_dates=True)
    prices = rrg_data.load_industry_prices()
    rrg = rrg_engine.compute_rrg(prices)
    results = rrg_engine.backtest(prices, diffusion, rrg, start=START, end=END)
    return rrg_engine, results


# ---------- 3. 渲染对比图 ----------
def draw_compare(gy_results, rrg_results, outdir='.'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = ['DejaVu Sans', 'Droid Sans Fallback']
    plt.rcParams['axes.unicode_minus'] = False

    # 统一净值: 各策略独立(起点=1)对齐到周
    series = {}
    for name in ['等权综合', '动态赋权']:
        v = gy_results[name]
        nav = v['nav']
        # PeriodIndex -> DatetimeIndex 以便统一 resample
        if hasattr(nav.index, 'to_timestamp'):
            nav = nav.copy()
            nav.index = nav.index.to_timestamp('W-FRI')
        series[f'成长-{name}'] = nav
    rrg_names = {'RRG_12': 'RRG 1+2象限', '扩散': '扩散指标', '扩散+RRG': '扩散+RRG'}
    for key, label in rrg_names.items():
        r, nav, sig = rrg_results[key]
        series[f'RRG-{label}'] = nav

    # 重采样到周末尾对齐 (确保 DatetimeIndex)
    df = pd.DataFrame(series)
    df.index = pd.to_datetime(df.index)
    df = df.dropna(how='all')
    df_w = df.resample('W-FRI').last().dropna(how='all').ffill()
    df_w = df_w / df_w.iloc[0]

    colors = {'成长-等权综合': '#1f77b4', '成长-动态赋权': '#d62728',
              'RRG-RRG 1+2象限': '#2ca02c', 'RRG-扩散指标': '#ff7f0e', 'RRG-扩散+RRG': '#9467bd'}
    fig, ax = plt.subplots(figsize=(12, 6), dpi=110)
    for c in df_w.columns:
        ax.plot(df_w.index.astype(str), df_w[c].values, label=c, color=colors.get(c),
                lw=1.8 if '成长' in c else 1.5)
    ax.set_title(f'成长价值轮动 vs RRG行业轮动 — 同期累计净值 ({START[:4]}-01 ~ {END[:4]}-{END[4:6]})', fontsize=13)
    ax.set_ylabel('净值 (起点=1)')
    ax.legend(loc='upper left', fontsize=9, ncol=2)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    p1 = os.path.join(outdir, 'compare_nav.png')
    fig.savefig(p1)
    plt.close(fig)

    # 图2: 年化/回撤/夏普 横向对比条形
    stats = {}
    for name in ['等权综合', '动态赋权']:
        v = gy_results[name]
        stats[f'成长-{name}'] = {'ann': v['ann'], 'mdd': v['mdd'], 'sharpe': v['sharpe']}
    for key, label in rrg_names.items():
        r, nav, sig = rrg_results[key]
        st = rrg_engine.perf_stats(r, nav, start=START, end=END)
        stats[f'RRG-{label}'] = {'ann': st.get('ann_ret', 0), 'mdd': st.get('maxdd', 0), 'sharpe': st.get('sharpe', 0)}

    names_l = list(stats.keys())
    anns = [stats[n]['ann'] * 100 for n in names_l]
    mdds = [stats[n]['mdd'] * 100 for n in names_l]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), dpi=110)
    ax = axes[0]
    bars = ax.barh(names_l, anns, color=['#1f77b4', '#d62728', '#2ca02c', '#ff7f0e', '#9467bd'])
    for b, v in zip(bars, anns):
        ax.text(v + (0.4 if v >= 0 else -0.4), b.get_y() + b.get_height() / 2, f'{v:.1f}%',
                va='center', ha='left' if v >= 0 else 'right', fontsize=9)
    ax.axvline(0, color='#999', lw=0.8)
    ax.set_title('年化收益 (%)', fontsize=12)
    ax = axes[1]
    bars = ax.barh(names_l, mdds, color=['#1f77b4', '#d62728', '#2ca02c', '#ff7f0e', '#9467bd'])
    for b, v in zip(bars, mdds):
        ax.text(v - 0.2, b.get_y() + b.get_height() / 2, f'{v:.1f}%', va='center', ha='right', fontsize=9)
    ax.axvline(0, color='#999', lw=0.8)
    ax.set_title('最大回撤 (%)', fontsize=12)
    fig.tight_layout()
    p2 = os.path.join(outdir, 'compare_stats.png')
    fig.savefig(p2)
    plt.close(fig)
    return p1, p2, stats


# ---------- 4. HTML ----------
def fmt_pct(x, nd=2):
    return f'{x * 100:.{nd}f}%' if x is not None and not (isinstance(x, float) and np.isnan(x)) else '—'


def build_html(gy_results, rrg_results, stats, p1, p2):
    rows = []
    # 成长价值
    for name in ['等权综合', '动态赋权']:
        v = gy_results[name]
        rows.append(f'''<tr><td><b>成长价值-{name}</b></td><td>{fmt_pct(v['ann'])}</td><td>{fmt_pct(v['excess'])}</td>
<td>{v['sharpe']:.2f}</td><td>{v['ir']:.2f}</td><td>{fmt_pct(v['mdd'])}</td><td>{v['n_sig']}</td><td>{fmt_pct(v['win_rate'])}</td><td>{v['payoff']:.2f}</td></tr>''')
    # RRG
    rrg_names = {'RRG_12': 'RRG 1+2象限', '扩散': '扩散指标', '扩散+RRG': '扩散+RRG'}
    for key, label in rrg_names.items():
        r, nav, sig = rrg_results[key]
        st = rrg_engine.perf_stats(r, nav, start=START, end=END)
        # 超额 vs 行业等权
        rets = rrg_results['bench_ret'] if 'bench_ret' in rrg_results else None
        total = st.get('total_ret', np.nan)
        n_sig = len(sig)
        rows.append(f'''<tr><td><b>RRG-{label}</b></td><td>{fmt_pct(st.get('ann_ret'))}</td><td>—</td>
<td>{st.get('sharpe', 0):.2f}</td><td>—</td><td>{fmt_pct(st.get('maxdd'))}</td><td>{n_sig}</td><td>—</td><td>—</td></tr>''')
    table = ''.join(rows)

    # 文章披露对照
    article_rows = '''
<tr><td>RRG 1+2象限</td><td class="tag">18.74%</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>
<tr><td>扩散指标</td><td class="tag">16.87%</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>
<tr><td>扩散+RRG</td><td class="tag">20.60%</td><td class="tag">9.49%(vs行业等权)</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>'''

    img_b64 = []
    for p in (p1, p2):
        if os.path.exists(p):
            with open(p, 'rb') as f:
                img_b64.append(base64.b64encode(f.read()).decode())
    img_tag1 = f'<img src="data:image/png;base64,{img_b64[0]}">' if len(img_b64) > 0 else ''
    img_tag2 = f'<img src="data:image/png;base64,{img_b64[1]}">' if len(img_b64) > 1 else ''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>轮动策略对比报告</title>
<style>
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;margin:24px;color:#222;background:#fff}}
h1{{font-size:22px;border-bottom:3px solid #d62728;padding-bottom:8px}}
h2{{font-size:17px;margin-top:28px;color:#1f77b4}}
table{{border-collapse:collapse;width:100%;margin:10px 0}}
th,td{{border:1px solid #d0d0d0;padding:6px 10px;font-size:13px;text-align:right}}
th{{background:#f5f5f5}}
td:first-child{{text-align:left}}
img{{max-width:100%;margin:8px 0}}
.note{{font-size:12px;color:#666;line-height:1.7}}
.tag{{color:#d62728;font-weight:bold}}
</style></head><body>
<h1>轮动策略同期对比报告：成长价值框架 vs RRG行业轮动</h1>
<p>回测区间 <b>{START[:4]}-01-01 ~ {END[:4]}-08-22</b>（统一周期）；高频信号策略周度调仓，RRG 系列月度调仓。当前为 <b>{globals().get('SIG_VER', 9)} 信号版</b>{'（个股L2/融资/盈利信号暂缺，待数据齐自动升级为9信号完整版）' if globals().get('SIG_VER', 9) == 6 else '（9信号完整版）'}。不作为投资建议。</p>

<h2>一、同期绩效对比 (2022-01 ~ 2026-08)</h2>
<table>
<tr><th>策略</th><th>年化收益</th><th>年化超额</th><th>夏普</th><th>IR</th><th>最大回撤</th><th>信号次数</th><th>胜率</th><th>赔率</th></tr>
{table}
</table>

<h2>二、西部证券原文披露 (2018-12 ~ 2026-03, 口径不同仅参考)</h2>
<table>
<tr><th>策略</th><th>年化收益</th><th>年化超额</th><th>夏普</th><th>IR</th><th>最大回撤</th><th>信号次数</th><th>胜率</th><th>赔率</th></tr>
{article_rows}
</table>
<p class="note">注：原文披露区间为 2018.12.28-2026.03.31（月频调仓，中信一级行业），本对比统一使用 2022 年起且以申万一级行业近似，区间/行业口径不同，数字不可直接对比。</p>

<h2>三、净值曲线</h2>
{img_tag1}
<h2>四、年化与回撤对比</h2>
{img_tag2}

<h2>五、方法与口径说明</h2>
<div class="note">
<p><b>成长价值框架</b>（开源金工）：9 个子信号（价格动量/成交额动量/超大单占比/融资买盘/净利润增速/股息比/PPI-CPI/固投/M1-M2），周五采样周度调仓，在国证成长/价值之间切换，基准 50/50。等权综合=信号等权；动态赋权=滚动120周优化。</p>
<p><b>RRG 框架</b>（西部证券）：以行业指数为标的按月调仓。RS=行业价格/等权基准；RS-Ratio(回看220日)与RS-Mom(回看60日)均MA20平滑；四象限以100为界。RRG_12=每月末等权配置1+2象限距原点最远6行业；扩散=自由流通市值加权成分股220日上涨占比(MA20)最高6行业；扩散+RRG=扩散最高6行业再剔除RRG三、四象限。</p>
<p><b>行业口径</b>：原文用中信一级(剔除综合/综合金融，28个)；tushare 无中信行业指数数据，本项目以<b>申万一级(剔除综合，30个)</b>近似复现，行业数量与成分与传统不同。</p>
<p><b>数据源</b>：行业指数/成分股/个股行情来自 tushare；成分股后复权价用于 220 日涨跌判断。</p>
</div>
<p class="note" style="margin-top:18px;border-top:1px solid #eee;padding-top:8px">本报告由 DuMate 生成，数据与实现仅供参考，不构成投资建议。</p>
</body></html>'''
    path = os.path.join(BASE, 'compare_report.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


# ---------- 5. 飞书推送 ----------
def push_feishu(html_path, p1, p2, gy_results, rrg_results):
    sys.path.insert(0, BASE)
    try:
        from feishu_config import APP_ID, APP_SECRET, WEBHOOK_URL, TOKEN_URL, UPLOAD_URL, CHAT_ID, FILE_UPLOAD_URL, MSG_SEND_URL
    except Exception as e:
        print(f'feishu config import failed: {e}')
        return False
    import requests
    try:
        resp = requests.post(TOKEN_URL, json={'app_id': APP_ID, 'app_secret': APP_SECRET}, timeout=15)
        token = resp.json()['tenant_access_token']
        print('token ok')
        image_keys = []
        for p in (p1, p2):
            if not os.path.exists(p):
                continue
            with open(p, 'rb') as f:
                r = requests.post(UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                                  data={'image_type': 'message'}, files={'image': f}, timeout=30)
            ik = r.json().get('data', {}).get('image_key')
            image_keys.append(ik)
            print('img ok' if ik else f'upload fail: {r.text[:100]}')
        for ik in image_keys:
            if ik:
                requests.post(WEBHOOK_URL, json={'msg_type': 'image', 'content': {'image_key': ik}}, timeout=20)

        def col(text):
            return {'tag': 'column', 'width': 'auto', 'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}}]}

        def cap(text):
            return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f'**{text}**'}}

        eq = gy_results['等权综合']
        dy = gy_results['动态赋权']
        stats = {}
        for key, label in [('RRG_12', 'RRG 1+2'), ('扩散', '扩散'), ('扩散+RRG', '扩散+RRG')]:
            r, nav, sig = rrg_results[key]
            st = rrg_engine.perf_stats(r, nav, start=START, end=END)
            stats[label] = st
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "轮动策略对比 | 成长价值 vs RRG"}, "template": "red"},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": "**报告来自于 DoMate**\n开源金工成长价值轮动 vs 西部证券RRG行业轮动 · 统一区间 2022-01 ~ 2026-08"}},
                    {"tag": "hr"},
                    cap('成长价值框架 (周度调仓)'),
                    {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
                        col(f'等权年化\n**{fmt_pct(eq["ann"])}**'),
                        col(f'动态年化\n**{fmt_pct(dy["ann"])}**'),
                        col(f'动态回撤\n**{fmt_pct(dy["mdd"])}**'),
                        col(f'动态IR\n**{dy["ir"]:.2f}**'),
                    ]},
                    cap('RRG框架 (月度调仓, 申万行业近似)'),
                    {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
                        col(f'RRG_12年化\n**{fmt_pct(stats["RRG 1+2"].get("ann_ret"))}**'),
                        col(f'扩散年化\n**{fmt_pct(stats["扩散"].get("ann_ret"))}**'),
                        col(f'扩散+RRG年化\n**{fmt_pct(stats["扩散+RRG"].get("ann_ret"))}**'),
                        col(f'扩散+RRG回撤\n**{fmt_pct(stats["扩散+RRG"].get("maxdd"))}**'),
                    ]},
                    {"tag": "div", "text": {"tag": "lark_md", "content": '口径提醒：西部原文2018.12-2026.3月频中信口径为年化18.74%/16.87%/20.60%；本对比为2022起申万口径月度调仓，数字不同属正常。详见HTML报告。'}},
                    {"tag": "note", "elements": [{"tag": "lark_md", "content": "完整报告(含净值曲线)见HTML附件 · 不构成投资建议"}]},
                ]
            }
        }
        r2 = requests.post(WEBHOOK_URL, json=card, timeout=20)
        print('card sent:', r2.json())
        with open(html_path, 'rb') as f:
            rf = requests.post(FILE_UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                               data={'file_type': 'stream', 'file_name': 'compare_report.html'},
                               files={'file': f}, timeout=30)
        fdata = rf.json()
        if fdata.get('code') == 0:
            file_key = fdata['data']['file_key']
            rm = requests.post(f'{MSG_SEND_URL}?receive_id_type=chat_id',
                               headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                               json={'receive_id': CHAT_ID, 'msg_type': 'file',
                                     'content': json.dumps({'file_key': file_key})}, timeout=20)
            print('html sent:', rm.json().get('code') == 0)
        else:
            print('file upload fail:', fdata.get('msg'))
        return True
    except Exception as e:
        print(f'feishu push error: {e}')
        import traceback; traceback.print_exc()
        return False


# ---------- MAIN ----------
if __name__ == '__main__':
    print(f'=== Compare Report === {time.strftime("%Y-%m-%d %H:%M")}')
    gy_ok, k, have, need = gy_ready()
    rrg_ok, n_ind, n_day = rrg_ready()
    if not rrg_ok:
        print(f'RRG_NOT_READY industry={n_ind} day={n_day}')
        sys.exit(3)
    if not gy_ok:
        print(f'GY_NOT_READY {k}={have}/{need} -> 先用6信号(宏观+相对强度)版')
    SIG_VER = 9 if gy_ok else 6
    globals()['SIG_VER'] = SIG_VER
    done6 = os.path.join(BASE, '.compare_report_6sig_done')
    done9 = os.path.join(BASE, '.compare_report_9sig_done')
    if SIG_VER == 9:
        if os.path.exists(done9):
            print('ALREADY_DONE_9SIG skip')
            sys.exit(0)
        if os.path.exists(done6):
            os.remove(done6)
    else:
        if os.path.exists(done6) or os.path.exists(done9):
            print('ALREADY_DONE_6SIG skip')
            sys.exit(0)

    re_mod, m, gy_results = run_gy(with_stock=(SIG_VER == 9))
    for name in ['等权综合', '动态赋权']:
        v = gy_results[name]
        print(f'GY {name}: ann={v["ann"]:.4%} excess={v["excess"]:.4%} mdd={v["mdd"]:.2%} n_sig={v["n_sig"]}')

    _rrg_mod, rrg_results = run_rrg()
    globals()['rrg_engine'] = _rrg_mod
    for key, label in [('RRG_12', 'RRG 1+2'), ('扩散', '扩散'), ('扩散+RRG', '扩散+RRG')]:
        r, nav, sig = rrg_results[key]
        st = rrg_engine.perf_stats(r, nav, start=START, end=END)
        print(f'RRG {label}: ann={st.get("ann_ret", 0):.4%} mdd={st.get("maxdd", 0):.2%} sig={len(sig)}')

    p1, p2, stats = draw_compare(gy_results, rrg_results, BASE)
    html_path = build_html(gy_results, rrg_results, stats, p1, p2)
    print('HTML at:', html_path)

    pushed = push_feishu(html_path, p1, p2, gy_results, rrg_results)
    if pushed:
        with open(done9 if SIG_VER == 9 else done6, 'w') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M'))
        print('FEISHU_PUSHED done')
    else:
        print('FEISHU_PUSH_FAILED (will retry next tick)')
        sys.exit(4)