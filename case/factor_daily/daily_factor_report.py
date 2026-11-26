"""
每日收盘后自动计算 A 股大盘因子，生成精简报告
用法: python3 daily_factor_report.py [--date YYYY-MM-DD]
"""
import sys, os, json, argparse, requests, smtplib, ssl, subprocess
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── DataHub 统一取数入口（禁止直连 akshare / 新浪）──
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
            '/opt/coze/.fuse_drive/dataHub_三级体系']
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq
_HUB_CANDS = _hub_candidates(os.path.dirname(os.path.abspath(__file__)))
for _hp in _HUB_CANDS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
try:
    import data_hub as dh
except Exception as _e:  # noqa: BLE001
    dh = None
    print(f"[DataHub] 导入失败: {_e}")

import pandas as pd
import numpy as np
from chanlun_backtest.index_factors import (
    calc_index_capital_factor,
    evaluate_capital_quality,
)
from chanlun_backtest.run_index_factor import fetch_index_data

# 监控的指数列表
INDICES = [
    ('000001', '上证指数'),
    ('000300', '沪深300'),
    ('399006', '创业板指'),
    ('000905', '中证500'),
    ('000852', '中证1000'),
    ('000688', '科创50'),
]

LOOKBACK = 120
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'daily_reports')

for _sp in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'case', '_lib'),
            '/Coze/Drive/股市监控画布/stock-dashboard/case/_lib'):
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
FEISHU_CHAT_ID = _secret('feishu.chat_id', 'FEISHU_CHAT_ID')  # AI股票消息群

# === 邮件配置（凭据一律走 app_secrets，代码里不留明文）===
SMTP_HOST = _secret('email.smtp_host', 'SMTP_HOST', default='smtp.163.com')
SMTP_PORT = int(_secret('email.smtp_port', 'SMTP_PORT', default='465') or 465)
SMTP_USER = _secret('email.username', 'SMTP_USER', 'EMAIL_USER')
SMTP_PASS = _secret('email.password', 'SMTP_PASSWORD', 'EMAIL_AUTH_CODE')
EMAIL_TO = _secret('email.recipients', 'EMAIL_TO', 'SMTP_TO')


def run_one_index(index_code: str, index_name: str, end_date: str) -> dict:
    """对单个指数计算因子，返回最新一日结果"""
    start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')
    df = fetch_index_data(index_code, start_date, end_date)
    if df.empty or len(df) < LOOKBACK + 5:
        return {'name': index_name, 'code': index_code, 'error': '数据不足'}

    from chanlun_backtest.indicators import calculate_all_indicators
    df = calculate_all_indicators(df)

    factor = calc_index_capital_factor(df, len(df) - 1, LOOKBACK)
    latest = df.iloc[-1]

    # 计算5日趋势
    factors_5d = []
    for i in range(max(0, len(df) - 5), len(df)):
        f = calc_index_capital_factor(df, i, LOOKBACK)
        factors_5d.append(f['composite'])

    if len(factors_5d) >= 2:
        trend = factors_5d[-1] - factors_5d[0]
        trend_str = '↑' if trend > 0.02 else ('↓' if trend < -0.02 else '→')
    else:
        trend_str = '→'

    return {
        'name': index_name,
        'code': index_code,
        'date': latest['timestamp'].strftime('%Y-%m-%d'),
        'close': round(float(latest['close']), 2),
        'outside_capital': factor['outside_capital'],
        'trapped_chips': factor['trapped_chips'],
        'chip_dispersion': factor['chip_dispersion'],
        'composite': factor['composite'],
        'passed': factor['passed'],
        'quality': evaluate_capital_quality(factor),
        'trend': trend_str,
    }




def get_market_overview(report_date: str) -> dict:
    """
    获取市场概览数据：两市成交额、涨跌家数、涨停/跌停/炸板
    多源回退确保数据完整性
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
        'errors': [],
    }

    # === 两市成交额（DataHub 指数快照，amount 单位：元） ===
    try:
        spot = dh.get_index_spot() if dh else None
        if spot is None or len(spot) == 0:
            raise RuntimeError('DataHub 指数快照为空')
        amt = {}
        for _, row in spot.iterrows():
            _ts = str(row.get('ts_code') or '')
            if _ts in ('000001.SH', '399001.SZ'):
                amt[_ts] = float(row.get('amount') or 0) / 1e8
        if amt.get('000001.SH'):
            overview['sh_amount'] = round(amt['000001.SH'], 1)
        if amt.get('399001.SZ'):
            overview['sz_amount'] = round(amt['399001.SZ'], 1)
        if overview['sh_amount'] and overview['sz_amount']:
            overview['total_amount'] = round(overview['sh_amount'] + overview['sz_amount'], 1)
    except Exception as e:
        overview['errors'].append(f'成交额获取失败(DataHub): {e}')

    # === 涨跌家数 + 涨停/跌停/炸板（乐股网） ===
    try:
        df = dh.get_market_activity_legu() if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError('DataHub 涨跌统计为空')
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
        overview['errors'].append(f'涨跌统计获取失败: {e}')

    # === 炸板数（AKShare 涨停池补充） ===
    try:
        date_compact = report_date.replace('-', '')
        # DataHub 股池白名单是 zt/zb/dt/strong/sub_new/previous，炸板用 'zb'
        zb_df = dh.get_limit_pool(kind='zb', trade_date=date_compact) if dh else None
        if zb_df is None:
            raise RuntimeError('DataHub 炸板池为空')
        overview['zb_count'] = len(zb_df)
    except Exception as e:
        overview['errors'].append(f'炸板数据获取失败: {e}')

    return overview


def format_market_overview(overview: dict, report_date: str) -> str:
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



def _indicator_tag(value: float, indicator: str) -> str:
    """根据指标值返回状态标签"""
    if indicator == 'outside_capital':
        if value >= 0.6:
            return '🟢充裕'
        elif value >= 0.4:
            return '🟡适中'
        else:
            return '🔴不足'
    elif indicator == 'trapped_chips':
        if value >= 0.6:
            return '🟢稀少'
        elif value >= 0.4:
            return '🟡适中'
        else:
            return '🔴较多'
    elif indicator == 'chip_dispersion':
        if value >= 0.6:
            return '🟢分散'
        elif value >= 0.4:
            return '🟡适中'
        else:
            return '🔴集中'
    return ''


def generate_report(results: list, report_date: str) -> str:
    """生成精简文本报告"""
    lines = []
    lines.append(f"# A股大盘资金面日报 {report_date}")
    lines.append("")

    # === 总结（放在最前面）===
    bullish = [r for r in results if r.get('passed') and 'error' not in r]
    bearish = [r for r in results if not r.get('passed') and 'error' not in r]

    lines.append("## 📋 总结")
    lines.append("")
    if bullish:
        lines.append(f"- **📈 看多** ({len(bullish)}个): {', '.join(r['name'] for r in bullish)}")
    else:
        lines.append("- **📈 看多**: 无")
    if bearish:
        lines.append(f"- **📉 看空** ({len(bearish)}个): {', '.join(r['name'] for r in bearish)}")
    else:
        lines.append("- **📉 看空**: 无")
    lines.append("")

    # 市场概览
    overview = get_market_overview(report_date)
    lines.append(format_market_overview(overview, report_date))

    for r in results:
        if 'error' in r:
            lines.append(f"## {r['name']} — {r['error']}")
            lines.append("")
            continue

        status = '📈 看多' if r['passed'] else '📉 看空'
        lines.append(f"## {r['name']} ({r['code']})  收盘 {r['close']}  {r['trend']}")
        lines.append(f"- 综合评分: **{r['composite']:.3f}** | 质量: {r['quality']} | {status}")

        oc_tag = _indicator_tag(r['outside_capital'], 'outside_capital')
        tc_tag = _indicator_tag(r['trapped_chips'], 'trapped_chips')
        cd_tag = _indicator_tag(r['chip_dispersion'], 'chip_dispersion')

        lines.append(f"- 场外资金: **{r['outside_capital']:.3f}** {oc_tag} | 套牢盘稀少: **{r['trapped_chips']:.3f}** {tc_tag} | 筹码分散: **{r['chip_dispersion']:.3f}** {cd_tag}")
        lines.append("")

    # 总体判断
    passed_count = sum(1 for r in results if r.get('passed'))
    lines.append("---")
    if passed_count == len(results):
        lines.append(f"**总体判断**: 全部 {len(results)} 个指数看多，市场环境良好。")
    elif passed_count >= 1:
        lines.append(f"**总体判断**: {passed_count}看多 / {len(results)-passed_count}看空，市场分化，注意仓位控制。")
    else:
        lines.append(f"**总体判断**: 全部 {len(results)} 个指数看空，市场环境偏弱，建议观望或轻仓。")

    # 指标说明卡片
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 📖 指标说明（怎么看、怎么用）")
    lines.append("")
    lines.append("**场外资金** (≥0.4 通过)")
    lines.append("> 回调时成交量缩得越厉害，说明卖的人少了，场外还有钱等着进场。")
    lines.append("> 🟢≥0.6 充裕 → 🟡0.4~0.6 适中 → 🔴<0.4 不足")
    lines.append("> **怎么用**: 数值越高，说明回调缩量充分，后续拉升阻力小。低于0.4说明回调时还在放量砸，短期不宜追高。")
    lines.append("")
    lines.append("**套牢盘稀少** (≥0.4 通过)")
    lines.append("> 当前价格上方（比现价贵的）成交占比越少，套牢的人就越少，抛压就越轻。")
    lines.append("> 🟢≥0.6 稀少 → 🟡0.4~0.6 适中 → 🔴<0.4 较多")
    lines.append("> **怎么用**: 数值越高，上方套牢盘越少，涨起来阻力越小。低于0.4说明头顶一堆人等着解套卖出，上涨压力大。")
    lines.append("")
    lines.append("**筹码分散** (≥0.4 通过)")
    lines.append("> 成交量在各个价位的分布越均匀，说明没有大量筹码集中在某一个价位，不容易形成一致性抛压。")
    lines.append("> 🟢≥0.6 分散 → 🟡0.4~0.6 适中 → 🔴<0.4 集中")
    lines.append("> **怎么用**: 数值越高，筹码分布越均匀，行情运行更健康。低于0.4说明筹码扎堆在某个价位，一旦跌破容易引发恐慌踩踏。")
    lines.append("")
    lines.append("**综合评分** = 0.35×场外资金 + 0.35×套牢盘稀少 + 0.30×筹码分散")
    lines.append("> 三项都 ≥0.4 为看多，否则看空。综合评分越高，大盘资金面越健康。")

    return '\n'.join(lines)

def send_consolidated_email(results: list, report_date: str):
    """
    当 2 个以上指数看多或看空时，发送邮件提醒。
    """
    bullish = [r for r in results if r.get('passed') and 'error' not in r]
    bearish = [r for r in results if not r.get('passed') and 'error' not in r]

    # 至少 2 个指数同方向才发
    if len(bullish) < 2 and len(bearish) < 2:
        print("未触发邮件提醒（不足2个指数同方向）")
        return

    # 判断主要方向
    if len(bullish) >= len(bearish):
        direction = '📈 看多'
        color = '#52c41a'
        indices = bullish
    else:
        direction = '📉 看空'
        color = '#f5222d'
        indices = bearish

    subject = f"{direction}提醒 | {len(indices)}个指数资金面一致 | {report_date} | 来自于 coze.claude"

    # 构建 HTML
    rows = []
    for r in results:
        if 'error' in r:
            continue
        tag = '📈 看多' if r.get('passed') else '📉 看空'
        rows.append(f"""
        <tr>
          <td style='padding:8px 14px;border-bottom:1px solid #f0f0f0;font-weight:bold;'>{r['name']}</td>
          <td style='padding:8px 14px;border-bottom:1px solid #f0f0f0;color:{"#52c41a" if r.get("passed") else "#f5222d"};font-weight:bold;'>{tag}</td>
          <td style='padding:8px 14px;border-bottom:1px solid #f0f0f0;'>{r['composite']:.3f}</td>
          <td style='padding:8px 14px;border-bottom:1px solid #f0f0f0;'>{r['close']}</td>
          <td style='padding:8px 14px;border-bottom:1px solid #f0f0f0;'>场外{r['outside_capital']:.2f} / 套牢{r['trapped_chips']:.2f} / 分散{r['chip_dispersion']:.2f}</td>
        </tr>""")

    html = f"""
    <div style='max-width:600px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0 auto;'>
      <div style='background:{color};color:#fff;padding:18px 22px;border-radius:10px 10px 0 0;'>
        <h2 style='margin:0;font-size:20px;'>A股大盘资金面 — {direction}提醒</h2>
        <p style='margin:6px 0 0;opacity:0.85;font-size:13px;'>{report_date} | 来自于 coze.claude</p>
      </div>
      <div style='background:#fff;border:1px solid #e8e8e8;border-radius:0 0 10px 10px;padding:0;'>
        <div style='padding:14px 18px;background:#fafafa;border-bottom:1px solid #f0f0f0;'>
          <strong>📈 看多 ({len(bullish)}个):</strong> {', '.join(r['name'] for r in bullish) if bullish else '无'}
          <br>
          <strong>📉 看空 ({len(bearish)}个):</strong> {', '.join(r['name'] for r in bearish) if bearish else '无'}
        </div>
        <table style='width:100%;border-collapse:collapse;'>
          <tr style='background:#fafafa;'>
            <th style='padding:8px 14px;text-align:left;font-size:13px;color:#888;border-bottom:1px solid #e8e8e8;'>指数</th>
            <th style='padding:8px 14px;text-align:left;font-size:13px;color:#888;border-bottom:1px solid #e8e8e8;'>判断</th>
            <th style='padding:8px 14px;text-align:left;font-size:13px;color:#888;border-bottom:1px solid #e8e8e8;'>评分</th>
            <th style='padding:8px 14px;text-align:left;font-size:13px;color:#888;border-bottom:1px solid #e8e8e8;'>收盘</th>
            <th style='padding:8px 14px;text-align:left;font-size:13px;color:#888;border-bottom:1px solid #e8e8e8;'>三项因子</th>
          </tr>
          {''.join(rows)}
        </table>
        <div style='padding:14px 18px;background:#fafafa;border-radius:0 0 10px 10px;font-size:12px;color:#888;'>
          综合评分 = 0.35×场外资金 + 0.35×套牢盘稀少 + 0.30×筹码分散，三项均≥0.4 为看多
          <br><em style='color:#999;'>来自于 coze.claude</em>
        </div>
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((str(Header("资金面日报", "utf-8")), SMTP_USER))
    msg["To"] = EMAIL_TO
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate()
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())
        server.quit()
        print(f"✓ 邮件提醒已发送: {direction} ({len(indices)}个指数)")
    except Exception as e:
        print(f"✗ 邮件发送失败: {e}")


def send_to_feishu(report_content):
    """通过 lark-cli 发送报告到 AI股票消息群（富文本卡片格式）"""
    title = "A股大盘资金面日报"
    lines = report_content.strip().split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# '):
            title = stripped[2:].strip()
            break

    card_body = {
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "*报告来自于 coze.claude*"}},
            {"tag": "hr"},
            {"tag": "markdown", "content": report_content}
        ]
    }

    # 优先 lark-cli
    try:
        card_json = json.dumps(card_body, ensure_ascii=False)
        cmd = [
            'lark-cli', 'im', '+messages-send',
            '--as', 'bot',
            '--chat-id', FEISHU_CHAT_ID,
            '--content', card_json,
            '--msg-type', 'interactive',
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            resp_data = json.loads(result.stdout) if result.stdout else {}
            if resp_data.get('ok'):
                print(f"✓ 报告已发送到飞书群 (lark-cli)")
                return True
            else:
                print(f"✗ lark-cli 返回异常: {result.stdout[:300]}")
        else:
            print(f"✗ lark-cli 失败: {result.stderr[:300]}")
    except Exception as e:
        print(f"✗ lark-cli 异常: {e}")

    # fallback: webhook
    if not FEISHU_WEBHOOK_URL:
        return False
    payload = {"msg_type": "interactive", "card": card_body}
    try:
        response = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                print("✓ 报告已发送到飞书（webhook fallback）")
                return True
            else:
                print(f"✗ 飞书 webhook 失败: {result}")
    except Exception as e:
        print(f"✗ 飞书 webhook 异常: {e}")
    return False


def main():
    parser = argparse.ArgumentParser(description='A股大盘每日因子报告')
    parser.add_argument('--date', type=str, default=None,
                        help='报告日期 YYYY-MM-DD，默认今天')
    args = parser.parse_args()

    report_date = args.date or datetime.now().strftime('%Y-%m-%d')
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始计算 {report_date} 大盘因子...")

    results = []
    for code, name in INDICES:
        print(f"  正在处理 {name}...")
        r = run_one_index(code, name, report_date)
        results.append(r)

    report = generate_report(results, report_date)

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f'factor_{report_date}.md')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)

    # 同时保存 JSON 方便程序读取
    json_file = os.path.join(REPORT_DIR, f'factor_{report_date}.json')
    overview = get_market_overview(report_date)
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': report_date,
            'market_overview': {k: v for k, v in overview.items() if k != 'errors'},
            'indices': results
        }, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存:")
    print(f"  Markdown: {report_file}")
    print(f"  JSON:     {json_file}")
    print()
    print(report)

    send_to_feishu(report)

    # 2个以上指数看多或看空时发邮件提醒
    send_consolidated_email(results, report_date)


if __name__ == '__main__':
    main()
