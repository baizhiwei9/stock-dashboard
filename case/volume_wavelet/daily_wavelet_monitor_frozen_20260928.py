"""
Daily Wavelet Volume Monitor - 4 ETFs + 2 Treasury Bond Futures
510300 (CSI 300) with chart + KE/CH/HS text reports
T/TL bond futures open-interest wavelet decomposition (main-continuous by max OI per day)
Pushes to Feishu daily; email alerts when |z| > threshold
"""
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0')
from tushare_config import pro, amount_to_yuan

import pandas as pd
import numpy as np
import pywt
import requests
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

# ========== CONFIGURATION ==========
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
Z_THRESHOLD = 1.5
# 长周期 z-score 单日变化量超过该西格玛值时，在报告中附带近一周块字符走势图
Z_CHANGE_THRESHOLD = 2.0
# 长周期 z-score 绝对值超过该西格玛值时，在标的名后加注五星标记
Z_STAR_THRESHOLD = 2.0
WEEK_BARS = 5
ROLLING_WINDOW = 20
BLOCK_LEVELS = "▁▂▃▄▅▆▇█"


def build_week_block_chart(c_long, n=WEEK_BARS):
    """将最近 n 个交易日长周期分量归一化为 8 档块字符走势，返回 (块字符串, 数值列表)"""
    week = np.asarray(c_long, dtype=float)[-n:]
    if len(week) < 2:
        return "", []
    vmin, vmax = float(week.min()), float(week.max())
    rng = (vmax - vmin) or 1.0
    chars = []
    for v in week:
        norm = (float(v) - vmin) / rng
        idx = min(7, max(0, int(round(norm * 7))))
        chars.append(BLOCK_LEVELS[idx])
    return "".join(chars), week.tolist()

# Email configuration
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 25
EMAIL_USER = "songshang951@163.com"
EMAIL_PASS = "TBmy7DAsj9aNJdxb"  # Replace with actual 163 email authorization code
EMAIL_TO = "410119229@qq.com"

ETFS = {
    '510300.SH': {'name': '沪深300ETF', 'type': 'chart'},
    '588000.SH': {'name': '科创板50ETF', 'type': 'text'},
    '159915.SZ': {'name': '创业板ETF', 'type': 'text'},
    '513130.SH': {'name': '恒生科技ETF', 'type': 'text'},
}

# 国债期货：代理的 T.CFX / TL.CFX 主连代码不可信（TL.CFX 实际返回 T 合约数据），
# 采用按日取同品种各合约中持仓量(oi)最大的合约拼接主力连续序列。
BOND_FUTURES = {
    'T':  {'name': '10年期国债期货', 'contracts': ['T2512.CFX', 'T2603.CFX', 'T2606.CFX', 'T2609.CFX', 'T2612.CFX']},
    'TL': {'name': '30年期国债期货', 'contracts': ['TL2512.CFX', 'TL2603.CFX', 'TL2606.CFX', 'TL2609.CFX', 'TL2612.CFX']},
}


def wavelet_decompose(volume, wavelet='sym4', level=3):
    pad_len = (2**level) - (len(volume) % (2**level))
    if pad_len == 2**level: pad_len = 0
    if pad_len > 0:
        volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
    else:
        volume_padded = volume
    coeffs = pywt.wavedec(volume_padded, wavelet, level=level)
    c_long = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], wavelet)[:len(volume)]
    c_medium = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], wavelet)[:len(volume)]
    c_short = pywt.waverec([np.zeros_like(coeffs[0]), np.zeros_like(coeffs[1]), np.zeros_like(coeffs[2]), coeffs[3]], wavelet)[:len(volume)]
    return c_long, c_medium, c_short


def fetch_bond_main_oi(ts_code, contracts):
    """按日取同一品种各合约中持仓量(oi)最大的合约，拼接主力连续序列。返回含 settle/close/oi 的 DataFrame。"""
    frames = []
    start = (datetime.now() - timedelta(days=370)).strftime('%Y%m%d')
    end = datetime.now().strftime('%Y%m%d')
    for cont in contracts:
        df = None
        for attempt in range(3):
            try:
                df = pro.fut_daily(ts_code=cont, start_date=start, end_date=end)
                if df is not None and len(df) > 0:
                    break
            except Exception as e:
                print(f"  retry {cont} ({attempt+1}/3): {str(e)[:40]}")
                time.sleep(5 * (attempt + 1))
        if df is not None and len(df) > 0:
            frames.append(df[['trade_date', 'settle', 'close', 'oi']].copy())
    if not frames:
        raise RuntimeError(f"fut_daily fetch failed for {ts_code}")
    all_df = pd.concat(frames, ignore_index=True)
    all_df['oi'] = pd.to_numeric(all_df['oi'], errors='coerce').fillna(0)
    all_df['settle'] = pd.to_numeric(all_df['settle'], errors='coerce').fillna(0)
    # 每日期取持仓量最大的合约作为主力
    idx = all_df.groupby('trade_date')['oi'].idxmax()
    main_df = all_df.loc[idx].sort_values('trade_date').reset_index(drop=True)
    return main_df


def analyze_etf(code, name):
    """Analyze single ETF and return metrics dict"""
    # 拉取失败（如 tushare 代理限频）时重试，最多 3 次
    df = None
    for attempt in range(3):
        try:
            df = pro.fund_daily(ts_code=code, start_date=(datetime.now() - timedelta(days=370)).strftime('%Y%m%d'), end_date=datetime.now().strftime('%Y%m%d'))
            if df is not None and len(df) > 0:
                break
        except Exception as e:
            print(f"  retry {code} ({attempt+1}/3): {str(e)[:40]}")
            time.sleep(5 * (attempt + 1))
    if df is None or len(df) == 0:
        raise RuntimeError(f"fund_daily fetch failed for {code}")
    df = df.sort_values('trade_date').reset_index(drop=True)

    df['amount_yuan'] = amount_to_yuan(df['amount'])
    df['close'] = df['close'].astype(float)

    volume = df['amount_yuan'].values
    close = df['close'].values
    dates = df['trade_date'].values

    c_long, c_medium, c_short = wavelet_decompose(volume)

    # Z-scores (full series for change detection)
    roll_mean_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
    roll_std_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
    z_long_series = np.divide(c_long - roll_mean_long, roll_std_long,
                              out=np.zeros_like(c_long, dtype=float), where=roll_std_long > 0)
    z_long = z_long_series[-1] if len(z_long_series) > 0 else 0
    z_long_prev = z_long_series[-2] if len(z_long_series) > 1 else z_long
    z_long_change = z_long - z_long_prev

    roll_mean_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
    roll_std_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
    z_med_series = np.divide(c_medium - roll_mean_med, roll_std_med,
                             out=np.zeros_like(c_medium, dtype=float), where=roll_std_med > 0)
    z_med = z_med_series[-1] if len(z_med_series) > 0 else 0

    # Weekly MoM
    long_mom = ((np.mean(c_long[-5:]) - np.mean(c_long[-10:-5])) / abs(np.mean(c_long[-10:-5])) * 100) if len(c_long) >= 10 else None
    med_mom = ((np.mean(c_medium[-5:]) - np.mean(c_medium[-10:-5])) / abs(np.mean(c_medium[-10:-5])) * 100) if len(c_medium) >= 10 else None

    # Daily changes
    long_day = ((c_long[-1] - c_long[-2]) / abs(c_long[-2]) * 100) if len(c_long) > 1 and c_long[-2] != 0 else 0
    med_day = ((c_medium[-1] - c_medium[-2]) / abs(c_medium[-2]) * 100) if len(c_medium) > 1 and c_medium[-2] != 0 else 0
    close_chg = ((close[-1] - close[-2]) / close[-2] * 100) if len(close) > 1 else 0

    # Percentiles
    long_pct = np.sum(c_long <= c_long[-1]) / len(c_long) * 100
    med_pct = np.sum(c_medium <= c_medium[-1]) / len(c_medium) * 100

    return {
        'kind': 'etf',
        'name': name,
        'over2sigma': abs(z_long) > Z_STAR_THRESHOLD,
        'close': close[-1],
        'close_chg': close_chg,
        'amount': volume[-1] / 1e8,
        'long_value': c_long[-1] / 1e8,
        'long_mom': long_mom,
        'long_day': long_day,
        'long_z': z_long,
        'long_z_change': z_long_change,
        'long_pct': long_pct,
        'week_long': c_long[-WEEK_BARS:] / 1e8,
        'med_value': c_medium[-1] / 1e8,
        'med_mom': med_mom,
        'med_day': med_day,
        'med_z': z_med,
        'med_pct': med_pct,
    }


def analyze_bond_oi(ts_code, name, contracts):
    """Analyze treasury bond futures main-continuous open interest series"""
    df = fetch_bond_main_oi(ts_code, contracts)
    oi = df['oi'].values.astype(float)
    settle = df['settle'].values.astype(float)
    dates = df['trade_date'].values

    c_long, c_medium, c_short = wavelet_decompose(oi)

    # Z-scores (full series for change detection)
    roll_mean_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
    roll_std_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
    z_long_series = np.divide(c_long - roll_mean_long, roll_std_long,
                              out=np.zeros_like(c_long, dtype=float), where=roll_std_long > 0)
    z_long = z_long_series[-1] if len(z_long_series) > 0 else 0
    z_long_prev = z_long_series[-2] if len(z_long_series) > 1 else z_long
    z_long_change = z_long - z_long_prev

    roll_mean_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
    roll_std_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
    z_med_series = np.divide(c_medium - roll_mean_med, roll_std_med,
                             out=np.zeros_like(c_medium, dtype=float), where=roll_std_med > 0)
    z_med = z_med_series[-1] if len(z_med_series) > 0 else 0

    # Weekly MoM
    long_mom = ((np.mean(c_long[-5:]) - np.mean(c_long[-10:-5])) / abs(np.mean(c_long[-10:-5])) * 100) if len(c_long) >= 10 else None
    med_mom = ((np.mean(c_medium[-5:]) - np.mean(c_medium[-10:-5])) / abs(np.mean(c_medium[-10:-5])) * 100) if len(c_medium) >= 10 else None

    # Daily changes
    long_day = ((c_long[-1] - c_long[-2]) / abs(c_long[-2]) * 100) if len(c_long) > 1 and c_long[-2] != 0 else 0
    med_day = ((c_medium[-1] - c_medium[-2]) / abs(c_medium[-2]) * 100) if len(c_medium) > 1 and c_medium[-2] != 0 else 0
    close_chg = ((settle[-1] - settle[-2]) / settle[-2] * 100) if len(settle) > 1 and settle[-2] != 0 else 0

    # Percentiles
    long_pct = np.sum(c_long <= c_long[-1]) / len(c_long) * 100
    med_pct = np.sum(c_medium <= c_medium[-1]) / len(c_medium) * 100

    return {
        'kind': 'bond',
        'name': name,
        'over2sigma': abs(z_long) > Z_STAR_THRESHOLD,
        'close': settle[-1],
        'close_chg': close_chg,
        'amount': oi[-1] / 1e4,
        'long_value': c_long[-1] / 1e4,
        'long_mom': long_mom,
        'long_day': long_day,
        'long_z': z_long,
        'long_z_change': z_long_change,
        'long_pct': long_pct,
        'week_long': c_long[-WEEK_BARS:] / 1e4,
        'med_value': c_medium[-1] / 1e4,
        'med_mom': med_mom,
        'med_day': med_day,
        'med_z': z_med,
        'med_pct': med_pct,
    }


def generate_text_block(r):
    """Generate text block for single target (ETF or bond future)"""
    lines = []
    star_mark = " ★★★★★" if r.get('over2sigma') else ""
    lines.append(f"**【{r['name']}】**{star_mark}")
    if r['kind'] == 'bond':
        lines.append(f"结算价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 持仓量: {r['amount']:.2f}万手")
    else:
        lines.append(f"收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 成交额: {r['amount']:.2f}亿")

    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"

    signal_long = " 🚨" if r['long_z'] > Z_THRESHOLD else " ⚠️" if r['long_z'] < -Z_THRESHOLD else ""
    signal_med = " 🚨" if r['med_z'] > Z_THRESHOLD else " ⚠️" if r['med_z'] < -Z_THRESHOLD else ""

    if r['kind'] == 'bond':
        lines.append(f"🔵 长周期{signal_long}: {r['long_value']:.2f}万手 | z={r['long_z']:+.2f} | 日环比{r['long_day']:+.2f}% | 周环比{long_mom_str} | 分位{r['long_pct']:.1f}%")
        lines.append(f"🟢 中周期{signal_med}: {r['med_value']:.2f}万手 | z={r['med_z']:+.2f} | 日环比{r['med_day']:+.2f}% | 周环比{med_mom_str} | 分位{r['med_pct']:.1f}%")
    else:
        lines.append(f"🔵 长周期{signal_long}: {r['long_value']:.2f}亿 | z={r['long_z']:+.2f} | 日环比{r['long_day']:+.2f}% | 周环比{long_mom_str} | 分位{r['long_pct']:.1f}%")
        lines.append(f"🟢 中周期{signal_med}: {r['med_value']:.2f}亿 | z={r['med_z']:+.2f} | 日环比{r['med_day']:+.2f}% | 周环比{med_mom_str} | 分位{r['med_pct']:.1f}%")

    # 长周期单日变化超过两倍西格玛时，附带近一周长周期成交量/持仓量块字符走势
    if abs(r.get('long_z_change', 0)) > Z_CHANGE_THRESHOLD and len(r.get('week_long', [])) >= 2:
        blocks, week_vals = build_week_block_chart(r['week_long'])
        unit = '万手' if r['kind'] == 'bond' else '亿'
        vals_str = " → ".join(f"{v:.1f}" for v in week_vals)
        zc = r['long_z_change']
        zc_arrow = "📈" if zc > 0 else "📉"
        lines.append(f"📊 近一周长周期走势: {blocks} (Δz={zc:+.2f}σ {zc_arrow})")
        lines.append(f"   {vals_str} {unit}")

    # Signal interpretation
    if r['long_z'] > Z_THRESHOLD and r['med_z'] > Z_THRESHOLD:
        if r['kind'] == 'bond':
            lines.append("⚡ **双频段共振增仓** — 长中周期同步突破+1.5σ，持仓快速增加，买入力量显著放大")
        else:
            lines.append("⚡ **双频段共振上涨** — 长中周期同步突破+1.5σ，资金大幅涌入，趋势确立")
    elif r['long_z'] < -Z_THRESHOLD and r['med_z'] < -Z_THRESHOLD:
        if r['kind'] == 'bond':
            lines.append("⚡ **双频段共振减仓** — 长中周期同步跌破-1.5σ，持仓快速萎缩，多头撤离警惕逆转")
        else:
            lines.append("⚡ **双频段共振下跌** — 长中周期同步跌破-1.5σ，资金撤离，警惕逆转")
    elif r['long_z'] > Z_THRESHOLD:
        lines.append("⚡ **长周期单频段突破** — 长期资金/持仓改善，中期动能未跟上，关注持续性")
    elif r['long_z'] < -Z_THRESHOLD:
        lines.append("⚡ **长周期单频段跌破** — 长期资金/持仓走弱，中期未共振，谨慎观望")
    else:
        lines.append(f"⚡ 暂无共振信号 (长z={r['long_z']:+.2f}, 中z={r['med_z']:+.2f})")

    lines.append("")
    return "\n".join(lines)


def push_to_feishu(title, content_elements, image_base64=None):
    """Push report to Feishu"""
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n**标的**: 沪深300ETF | 科创板50ETF | 创业板ETF | 恒生科技ETF | 10年期国债期货 | 30年期国债期货\n**方法**: sym4小波3层分解 | z=±{Z_THRESHOLD}"
            }
        },
        {"tag": "hr"},
    ]

    # Add image if provided (510300 chart)
    if image_base64:
        elements.append({
            "tag": "img",
            "title": {"tag": "plain_text", "content": "沪深300ETF 成交量小波分解图"},
            "img_key": image_base64,
            "alt": {"tag": "plain_text", "content": "沪深300ETF Wavelet Chart"}
        })
        elements.append({"tag": "hr"})

    # Add text content for each target
    for elem in content_elements:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": elem}
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "⚠️ 仅供研究参考，不构成投资建议"}]
    })

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": elements
        }
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
        print(f"Feishu response: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Result: {data}")
            return data.get('code') == 0
        return False
    except Exception as e:
        print(f"Push failed: {e}")
        return False


def get_tenant_token():
    """Get Feishu tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, headers={"Content-Type": "application/json"},
                         json={"app_id": "cli_a97b60337b7bdbd5", "app_secret": "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"}, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        return data["tenant_access_token"]
    print(f"Token error: {data}")
    return None


def upload_image(token, img_path):
    """Upload image to Feishu and get image_key"""
    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    with open(img_path, "rb") as f:
        files = {"image": f}
        data = {"image_type": "message"}
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        return data["data"]["image_key"]
    print(f"Upload error: {data}")
    return None


def send_image_card(image_key, date_str):
    """Send image-only card"""
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"成交量小波分析 | {date_str} | 沪深300ETF图表"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "img",
                    "img_key": image_key,
                    "alt": {"tag": "plain_text", "content": "沪深300ETF Wavelet Chart"}
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**沪深300ETF 成交量小波分解图**\nsym4小波3层分解 | z=±1.5阈值"
                    }
                }
            ]
        }
    }
    return send_webhook_card(payload)


def send_text_card(text_blocks, date_str):
    """Send text report card for 4 ETFs + 2 bond futures"""
    text_content = "\n".join(text_blocks)
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"成交量小波分析 | {date_str} | 六标对比报告"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n**方法**: sym4小波3层分解 | z=±{Z_THRESHOLD}σ | 长=cA3 中=cD3+cD2 短=cD1\n📊 ETF: 成交额分解 | 📈 国债期货: 持仓量分解(主力连续)\n\n{text_content}"
                    }
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": "⚠️ 仅供研究参考，不构成投资建议"}]
                }
            ]
        }
    }
    return send_webhook_card(payload)


def send_webhook_card(payload):
    resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = resp.json()
    return data.get("code") == 0, data


def send_email_alert(subject, body):
    """Send email alert when z-threshold is breached"""
    if not EMAIL_PASS or EMAIL_PASS == "YOUR_163_AUTH_CODE":
        print("⚠️ Email not configured: EMAIL_PASS not set")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html', 'utf-8'))

        server = smtplib.SMTP_SSL(EMAIL_HOST, 465, timeout=30)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print(f"✅ Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False


def generate_email_body(triggered):
    """Generate HTML email body for triggered targets (aligned with Feishu text blocks)"""
    body = f"""<h2>成交量小波分析 - 阈值突破告警</h2>
<p><strong>时间:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p><strong>阈值:</strong> z=±{Z_THRESHOLD}</p>
<hr>
"""
    for r in triggered:
        star_mark = " ★★★★★" if r.get('over2sigma') else ""
        signal_long = " 🚨" if r['long_z'] > Z_THRESHOLD else " ⚠️" if r['long_z'] < -Z_THRESHOLD else ""
        signal_med = " 🚨" if r['med_z'] > Z_THRESHOLD else " ⚠️" if r['med_z'] < -Z_THRESHOLD else ""
        if r['kind'] == 'bond':
            price_line = f"结算价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 持仓量: {r['amount']:.2f}万手"
            long_line = f"🔵 长周期{signal_long}: {r['long_value']:.2f}万手 | z={r['long_z']:+.2f} | 日环比{r['long_day']:+.2f}% | 周环比{r['long_mom']:+.1f}% | 分位{r['long_pct']:.1f}%"
            med_line = f"🟢 中周期{signal_med}: {r['med_value']:.2f}万手 | z={r['med_z']:+.2f} | 日环比{r['med_day']:+.2f}% | 周环比{r['med_mom']:+.1f}% | 分位{r['med_pct']:.1f}%"
        else:
            price_line = f"收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 成交额: {r['amount']:.2f}亿"
            long_line = f"🔵 长周期{signal_long}: {r['long_value']:.2f}亿 | z={r['long_z']:+.2f} | 日环比{r['long_day']:+.2f}% | 周环比{r['long_mom']:+.1f}% | 分位{r['long_pct']:.1f}%"
            med_line = f"🟢 中周期{signal_med}: {r['med_value']:.2f}亿 | z={r['med_z']:+.2f} | 日环比{r['med_day']:+.2f}% | 周环比{r['med_mom']:+.1f}% | 分位{r['med_pct']:.1f}%"

        week_block_html = ""
        if abs(r.get('long_z_change', 0)) > Z_CHANGE_THRESHOLD and len(r.get('week_long', [])) >= 2:
            blocks, week_vals = build_week_block_chart(r['week_long'])
            unit = '万手' if r['kind'] == 'bond' else '亿'
            vals_str = " → ".join(f"{v:.1f}" for v in week_vals)
            zc = r['long_z_change']
            zc_arrow = "📈" if zc > 0 else "📉"
            week_block_html = f'<li>📊 近一周长周期走势: <strong>{blocks}</strong> (Δz={zc:+.2f}σ {zc_arrow})</li>\n<li>近一周数值: {vals_str} {unit}</li>'

        if r['long_z'] > Z_THRESHOLD and r['med_z'] > Z_THRESHOLD:
            sig = "⚡ 双频段共振" + ("增仓" if r['kind'] == 'bond' else "上涨") + " — 长中周期同步突破+1.5σ"
        elif r['long_z'] < -Z_THRESHOLD and r['med_z'] < -Z_THRESHOLD:
            sig = "⚡ 双频段共振" + ("减仓" if r['kind'] == 'bond' else "下跌") + " — 长中周期同步跌破-1.5σ"
        elif r['long_z'] > Z_THRESHOLD:
            sig = "⚡ 长周期单频段突破 — 长期资金/持仓改善，中期动能未跟上，关注持续性"
        elif r['long_z'] < -Z_THRESHOLD:
            sig = "⚡ 长周期单频段跌破 — 长期资金/持仓走弱，中期未共振，谨慎观望"
        else:
            sig = f"⚡ 暂无共振信号 (长z={r['long_z']:+.2f}, 中z={r['med_z']:+.2f})"

        body += f"""
<h3>【{r['name']}】{star_mark}</h3>
<ul>
<li>{price_line}</li>
<li>{long_line}</li>
<li>{med_line}</li>
{week_block_html}
<li>{sig}</li>
</ul>
"""
    body += "<hr><p><em>⚠️ 仅供研究参考，不构成投资建议</em></p>"
    return body


def check_data_freshness():
    """数据新鲜度闸门：fund_daily / fut_daily 最新交易日必须为最近交易日（周末/节假日容忍4天）。
    返回 {'ok': bool, 'latest': str/None, 'reason': str}"""
    try:
        latest_dates = []
        for code in ETFS:
            tdf = pro.fund_daily(
                ts_code=code,
                start_date=(datetime.now() - timedelta(days=10)).strftime('%Y%m%d'),
                end_date=datetime.now().strftime('%Y%m%d'))
            if tdf is not None and len(tdf) > 0:
                latest_dates.append(str(tdf['trade_date'].max()))
        for code, info in BOND_FUTURES.items():
            for cont in info['contracts'][-2:]:
                try:
                    tdf = pro.fut_daily(
                        ts_code=cont,
                        start_date=(datetime.now() - timedelta(days=10)).strftime('%Y%m%d'),
                        end_date=datetime.now().strftime('%Y%m%d'))
                    if tdf is not None and len(tdf) > 0:
                        latest_dates.append(str(tdf['trade_date'].max()))
                        break
                except Exception:
                    continue
        if not latest_dates:
            return {'ok': False, 'latest': None,
                    'reason': 'fund_daily/fut_daily 无任何数据返回'}
        latest = max(latest_dates)
        lag = (datetime.now() - datetime.strptime(latest, '%Y%m%d')).days
        if lag > 4:
            return {'ok': False, 'latest': latest,
                    'reason': f'数据最新交易日 {latest} 距今 {lag} 天，超过4天容忍阈值'}
        return {'ok': True, 'latest': latest, 'reason': 'OK'}
    except Exception as e:
        return {'ok': False, 'latest': None, 'reason': f'新鲜度检查异常: {e}'}


def main():
    print(f"[{datetime.now()}] Starting Daily Wavelet Monitor - 4 ETFs + 2 Bond Futures")

    # 数据新鲜度闸门：最新交易日必须为最近交易日（周末/节假日容忍 4 天）
    gate = check_data_freshness()
    if not gate['ok']:
        print(f"[DATA-GATE] {gate['reason']}，拒绝生成/发送报告")
        return

    results = []
    for code, info in ETFS.items():
        try:
            r = analyze_etf(code, info['name'])
            results.append(r)
            print(f"✓ {info['name']} analyzed")
        except Exception as e:
            print(f"✗ {info['name']}: {e}")

    for code, info in BOND_FUTURES.items():
        try:
            r = analyze_bond_oi(code, info['name'], info['contracts'])
            results.append(r)
            print(f"✓ {info['name']} analyzed (main OI series)")
        except Exception as e:
            print(f"✗ {info['name']}: {e}")

    if not results:
        print("No results, aborting.")
        return

    # Generate text blocks
    text_blocks = []
    for r in results:
        text_blocks.append(generate_text_block(r))

    date_str = datetime.now().strftime('%Y-%m-%d')
    token = get_tenant_token()
    if not token:
        print("Failed to get token, aborting push")
        token_fail = True
    else:
        token_fail = False

    # Message 1: 510300 chart image
    image_key = None
    if not token_fail:
        chart_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v6_zscore_1.5_signals.png'
        image_key = upload_image(token, chart_path)
        if image_key:
            print(f"Image uploaded, key={image_key[:16]}...")
        else:
            print("Image upload failed")

    success1 = False
    if image_key:
        success1, resp1 = send_image_card(image_key, date_str)
        print(f"Message 1 (image): {'✅' if success1 else '❌'} {resp1}")

    # Message 2: text report for all targets
    success2 = False
    if not token_fail:
        success2, resp2 = send_text_card(text_blocks, date_str)
        print(f"Message 2 (text): {'✅' if success2 else '❌'} {resp2}")

    success = (success1 or image_key is None) and success2

    # Check for threshold breaches and send email alerts
    triggered = [r for r in results if abs(r['long_z']) > Z_THRESHOLD]
    if triggered:
        print(f"\n🚨 {len(triggered)} target(s) breached z=±{Z_THRESHOLD} threshold!")
        email_subject = f"[成交量小波分析告警] {len(triggered)}个标的突破±{Z_THRESHOLD}σ"
        email_body = generate_email_body(triggered)
        send_email_alert(email_subject, email_body)
    else:
        print(f"\n✅ No threshold breaches at z=±{Z_THRESHOLD}")

    if success:
        print("✅ Daily report pushed successfully")
    else:
        print("⚠️ Push failed or returned error")

    print(f"[{datetime.now()}] Monitor complete")


if __name__ == '__main__':
    main()