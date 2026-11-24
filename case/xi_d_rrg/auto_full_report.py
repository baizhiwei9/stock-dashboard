# -*- coding: utf-8 -*-
"""成长价值轮动复现 —— 全流程自动执行脚本 (幂等)
数据未齐: 打印进度并退出(退出码 3)
数据已齐: 跑全9信号回测 -> 生成HTML报告 -> 飞书推送(图片+交互卡片+HTML文件) -> 写 done 标记
用法: python auto_full_report.py
"""
import os, sys, re, json, time, socket
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig_gai(*a, **kw) if r[0] == socket.AF_INET]

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'pylib'))  # 工作区持久依赖

import numpy as np
import pandas as pd

# ---------- 0. 检查数据是否齐全 ----------
REQUIRED_KINDS = ['moneyflow', 'margin', 'income', 'daily_basic']


def data_progress():
    codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()
    n = len(codes)
    prog = {}
    for k in REQUIRED_KINDS:
        d = os.path.join(DATA, k)
        files = [f for f in os.listdir(d) if f.endswith('.csv') and not re.match(r'^\d{6}\.csv$', f)] if os.path.isdir(d) else []
        prog[k] = len(files)
    return n, prog


def data_ready():
    n, prog = data_progress()
    return all(v >= n for v in prog.values()), n, prog


def wait_and_report():
    n, prog = data_progress()
    print(f'DATA_NOT_READY total={n} ' + ' '.join(f'{k}={v}' for k, v in prog.items()))
    sys.exit(3)


# ---------- 1. 跑完整回测 ----------
def run_backtest():
    import repro_engine as re
    m, results = re.run_repro(start='20220101', end='20260822', with_stock=True, W=120)
    return re, m, results


# ---------- 2. 绘图 (matplotlib -> PNG) ----------
def draw_charts(re, m, results, outdir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = ['DejaVu Sans', 'Droid Sans Fallback', 'Noto Sans CJK SC']
    plt.rcParams['axes.unicode_minus'] = False

    nav_all = pd.DataFrame({
        '等权综合': results['等权综合']['nav'],
        '动态赋权': results['动态赋权']['nav'],
        '基准(50/50)': (1 + (0.5 * m['g_ret'] + 0.5 * m['v_ret']).fillna(0)).cumprod(),
    }).dropna()
    nav_all = nav_all / nav_all.iloc[0]

    # 图1: 净值曲线
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=110)
    colors = {'等权综合': '#1f77b4', '动态赋权': '#d62728', '基准(50/50)': '#9e9e9e'}
    for c in nav_all.columns:
        ax.plot(nav_all.index.astype(str), nav_all[c].values, label=c, color=colors[c], lw=1.8 if c != '基准(50/50)' else 1.3, ls='-' if c != '基准(50/50)' else '--')
    ax.set_title('成长价值轮动复现 — 累计净值 (2022-01 ~ 2026-08, 周度调仓)', fontsize=13)
    ax.set_ylabel('净值 (起点=1)')
    ax.legend(loc='upper left', fontsize=10)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    p1 = os.path.join(outdir, 'nav.png')
    fig.savefig(p1)
    plt.close(fig)

    # 图2: 超额净值
    fig, ax = plt.subplots(figsize=(11, 4.6), dpi=110)
    for name, key in [('等权综合超额', '等权综合'), ('动态赋权超额', '动态赋权')]:
        ex = results[key]['ret']['ret'] - results[key]['ret']['bench']
        ex_nav = (1 + ex).cumprod()
        ax.plot(ex_nav.index.astype(str), ex_nav.values, label=name, lw=1.8)
    ax.axhline(1.0, color='#9e9e9e', lw=1, ls='--')
    ax.set_title('相对基准(50%成长+50%价值)的超额净值', fontsize=13)
    ax.set_ylabel('超额净值')
    ax.legend(loc='upper left', fontsize=10)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    p2 = os.path.join(outdir, 'excess_nav.png')
    fig.savefig(p2)
    plt.close(fig)
    return p1, p2


# ---------- 3. 生成 HTML 报告 ----------
def fmt_pct(x, nd=2):
    return f'{x * 100:.{nd}f}%' if x is not None and not (isinstance(x, float) and np.isnan(x)) else '—'


def build_html(re, m, results, nav_png, ex_png):
    article = [
        ('等权综合（披露）', '8.44%', '114', '60%', '2.65', '11.86%'),
        ('动态赋权（披露）', '10.23%', '115', '66.38%', '2.84', '10.00%'),
    ]
    rows = []
    for name in ['等权综合', '动态赋权']:
        v = results[name]
        rows.append(f'''<tr>
<td><b>{name}</b></td>
<td>{fmt_pct(v['ann'])}</td>
<td>{fmt_pct(v['excess'])}</td>
<td>{fmt_pct(v['excess'], 1)} (超额)</td>
<td>{v['sharpe']:.2f}</td>
<td>{v['ir']:.2f}</td>
<td>{fmt_pct(v['mdd'])}</td>
<td>{v['n_sig']}</td>
<td>{fmt_pct(v['win_rate'])}</td>
<td>{v['payoff']:.2f}</td>
</tr>''')
    perf_rows = ''.join(rows)

    # 信号覆盖统计
    sig_cols = [c for c in re.ALL_SIGS if c in m.columns]
    sig_rows = []
    names = {'s1': 'S1 价格动量', 's2': 'S2 成交额动量', 's3': 'S3 超大单占比', 's4': 'S4 融资买盘比',
             's5': 'S5 净利润增速', 's6': 'S6 股息比', 's7': 'S7 PPI-CPI', 's8': 'S8 固定资产投资', 's9': 'S9 M1-M2'}
    for c in sig_cols:
        s = m[c]
        n_up = int((s == 1).sum())
        n_dn = int((s == -1).sum())
        n_total = int(s.notna().sum())
        sig_rows.append(f'''<tr><td>{names.get(c, c)}</td><td>{n_total}</td><td>{n_up}</td><td>{n_dn}</td><td>{fmt_pct(n_up / n_total) if n_total else '—'}</td></tr>''')
    sig_table = ''.join(sig_rows)

    img_b64 = []
    for p in (nav_png, ex_png):
        with open(p, 'rb') as f:
            import base64
            img_b64.append(base64.b64encode(f.read()).decode())

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>成长价值轮动复现报告</title>
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
<h1>成长价值轮动全景式框架 — 复现核验报告</h1>
<p>策略来源：《成长价值轮动的全景式框架》（开源金工）· 复现区间 <b>2022-01-01 ~ 2026-08-22</b>（周度调仓，基准 50%国证成长+50%国证价值）</p>

<h2>一、复现绩效 vs 文章披露</h2>
<table>
<tr><th>组合</th><th>年化收益</th><th>年化超额</th><th>夏普(超额)</th><th>IR</th><th>最大回撤</th><th>信号次数</th><th>胜率</th><th>赔率</th></tr>
{perf_rows}
</table>
<table>
<tr><th>文章披露指标</th><th>年化超额</th><th>信号次数</th><th>胜率</th><th>赔率</th><th>最大回撤</th></tr>
<tr><td>等权综合</td><td class="tag">8.44%</td><td>114</td><td>60%</td><td>2.65</td><td>11.86%</td></tr>
<tr><td>动态赋权</td><td class="tag">10.23%</td><td>115</td><td>66.38%</td><td>2.84</td><td>10.00%</td></tr>
</table>
<p class="note">注：文章披露为全区间(2010-01~2026-07)口径；本表为 2022-01 起的复现结果，两者区间不同不可直接对比，仅作参考锚点。</p>

<h2>二、净值曲线</h2>
<img src="data:image/png;base64,{img_b64[0]}" alt="净值曲线">
<h2>三、超额净值</h2>
<img src="data:image/png;base64,{img_b64[1]}" alt="超额净值">

<h2>四、子信号周度覆盖统计</h2>
<table>
<tr><th>信号</th><th>有效周数</th><th>看多成长(次)</th><th>看空成长(次)</th><th>看多占比</th></tr>
{sig_table}
</table>

<h2>五、方法与说明</h2>
<div class="note">
<p><b>数据源</b>：国证成长(399370)/国证价值(399371)日线（tushare）；月度权重快照（2020-02~2026-07）；个股 moneyflow/margin/income/daily_basic 全历史（tushare，1177只）；宏观 CPI/PPI/M1-M2/固定资产投资（akshare+手工补全）。</p>
<p><b>信号定义</b>：S1 日超额15日累计符号；S2 周成交额占比8周差分；S3 个股超大单+大单主动净流入占比差；S4 融资买入额差；S5 净利润同比(整体法)差；S6 股息率比值偏离均线；S7 36月PPI-CPI双条件分位；S8 固投同比差分；S9 36月M1-M2分位。</p>
<p><b>执行口径</b>：信号于周末收盘确定，下一周持有对应指数，周度调仓；等权综合=信号等权加总取方向；动态赋权=滚动120周SLSQP最小化组合信号收益与最优解收益距离。</p>
<p><b>归因说明</b>：文章未披露子信号精确构造细节与样本口径，S3-S6 为本项目合理假设实现；复现绩效与披露数字的差异主要来自区间不同(本文2022起 vs 文章2010起)，2022-2026 为大熊+结构性行情，与全区间样本差异显著。</p>
</div>
<p class="note" style="margin-top:18px;border-top:1px solid #eee;padding-top:8px">本报告由 DuMate 生成，数据与实现仅供参考，不构成投资建议。</p>
</body></html>'''
    path = os.path.join(BASE, 'growth_value_repro_report.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


# ---------- 4. 飞书推送 ----------
def push_feishu(html_path, nav_png, ex_png, results):
    feishu_cfg_dir = BASE  # feishu_config.py 已本地化
    sys.path.insert(0, feishu_cfg_dir)
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

        # 上传主图(净值曲线)
        image_keys = []
        for p in (nav_png, ex_png):
            with open(p, 'rb') as f:
                r = requests.post(UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                                  data={'image_type': 'message'}, files={'image': f}, timeout=30)
            ik = r.json().get('data', {}).get('image_key')
            image_keys.append(ik)
            print('image_key ok' if ik else f'upload fail: {r.text[:120]}')

        # 发图片消息
        for ik in image_keys:
            if ik:
                requests.post(WEBHOOK_URL, json={'msg_type': 'image', 'content': {'image_key': ik}}, timeout=20)

        # 交互卡片 (header + 绩效摘要, column_set 表格)
        def col(text, w='auto'):
            return {'tag': 'column', 'width': w, 'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}}]}

        def cap(text):
            return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f'**{text}**'}}

        eq, dy = results['等权综合'], results['动态赋权']
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "成长价值轮动复现 | 2022-2026.8"}, "template": "red"},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": "**报告来自于 DoMate**\n开源金工《成长价值轮动的全景式框架》复现核验 · 周度调仓 · 基准50%成长+50%价值"}},
                    {"tag": "hr"},
                    cap('等权综合'),
                    {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
                        col(f'年化超额\n**{fmt_pct(eq["excess"])}**'),
                        col(f'信号\n**{eq["n_sig"]}**次'),
                        col(f'胜率\n**{fmt_pct(eq["win_rate"])}**'),
                        col(f'最大回撤\n**{fmt_pct(eq["mdd"])}**'),
                    ]},
                    cap('动态赋权'),
                    {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
                        col(f'年化超额\n**{fmt_pct(dy["excess"])}**'),
                        col(f'信号\n**{dy["n_sig"]}**次'),
                        col(f'胜率\n**{fmt_pct(dy["win_rate"])}**'),
                        col(f'IR\n**{dy["ir"]:.2f}**'),
                    ]},
                    {"tag": "div", "text": {"tag": "lark_md", "content": '文章披露(2010-2026全区间)参考：等权超额8.44%/114次/胜率60%；动态超额10.23%/115次/胜率66.38%。本复现区间为2022-01起，与披露口径不同，详见HTML报告。'}},
                    {"tag": "note", "elements": [{"tag": "lark_md", "content": "完整报告(含净值曲线)见HTML附件 · 不构成投资建议"}]},
                ]
            }
        }
        r2 = requests.post(WEBHOOK_URL, json=card, timeout=20)
        print('card sent:', r2.json())

        # 发送 HTML 文件
        with open(html_path, 'rb') as f:
            rf = requests.post(FILE_UPLOAD_URL, headers={'Authorization': f'Bearer {token}'},
                               data={'file_type': 'stream', 'file_name': 'growth_value_repro_report.html'},
                               files={'file': f}, timeout=30)
        fdata = rf.json()
        if fdata.get('code') == 0:
            file_key = fdata['data']['file_key']
            rm = requests.post(f'{MSG_SEND_URL}?receive_id_type=chat_id',
                               headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                               json={'receive_id': CHAT_ID, 'msg_type': 'file',
                                     'content': json.dumps({'file_key': file_key})}, timeout=20)
            print('html file sent:', rm.json().get('code') == 0)
        else:
            print('file upload fail:', fdata.get('msg'))
        return True
    except Exception as e:
        print(f'feishu push error: {e}')
        import traceback
        traceback.print_exc()
        return False


# ---------- MAIN ----------
if __name__ == '__main__':
    print(f'=== Growth-Value Repro Auto Report === {time.strftime("%Y-%m-%d %H:%M")}')
    ready, n, prog = data_ready()
    if not ready:
        wait_and_report()

    done_mark = os.path.join(BASE, '.full_report_done')
    if os.path.exists(done_mark):
        print('ALREADY_DONE skip')
        sys.exit(0)

    ok, n, prog = True, n, prog
    re_mod, m, results = run_backtest()
    for name in ['等权综合', '动态赋权']:
        v = results[name]
        print(f'{name}: excess={v["excess"]:.4%} n_sig={v["n_sig"]} win={v["win_rate"]:.2%} ir={v["ir"]:.2f} ex_mdd={v["ex_mdd"]:.2%}')

    nav_png, ex_png = draw_charts(re_mod, m, results, BASE)
    html_path = build_html(re_mod, m, results, nav_png, ex_png)
    print('HTML at:', html_path)

    pushed = push_feishu(html_path, nav_png, ex_png, results)
    if pushed:
        with open(done_mark, 'w') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M'))
        print('FEISHU_PUSHED done')
    else:
        print('FEISHU_PUSH_FAILED (will retry next tick)')
        sys.exit(4)