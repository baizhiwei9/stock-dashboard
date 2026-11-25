"""Test email alert for 588000 breach"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import json

# Config
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 25
EMAIL_USER = "songshang951@163.com"
EMAIL_PASS = "TBmy7DAsj9aNJdxb"
EMAIL_TO = "410119229@qq.com"
Z_THRESHOLD = 1.5

# Load results
with open('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_mom_results.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

# Find triggered ETFs
triggered = [r for r in results if abs(r['long_z']) > Z_THRESHOLD]
print(f"Triggered ETFs: {len(triggered)}")
for r in triggered:
    print(f"  {r['name']}: long_z={r['long_z']:+.2f}")

if not triggered:
    print("No threshold breaches, skipping email")
    exit(0)

# Build email body
body = f"""<h2>成交量小波分析 - 阈值突破告警</h2>
<p><strong>时间:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p><strong>阈值:</strong> z=±{Z_THRESHOLD}</p>
<hr>
"""

for r in triggered:
    direction = "上涨" if r['long_z'] > Z_THRESHOLD else "下跌"
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    
    body += f"""
<h3>【{r['name']} ({r['code']})】— 长周期{direction}突破</h3>
<ul>
<li>收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%)</li>
<li>成交额: {r['amount']:.2f} 亿元</li>
<li>长周期 z-score: <strong>{r['long_z']:+.2f}</strong> (突破±{Z_THRESHOLD}σ)</li>
<li>中周期 z-score: {r['med_z']:+.2f}</li>
<li>长周期周环比: {long_mom_str}</li>
<li>中周期周环比: {med_mom_str}</li>
<li>长周期历史分位: {r['long_pct']:.1f}%</li>
<li>中周期历史分位: {r['med_pct']:.1f}%</li>
</ul>
"""
    
    if r['long_z'] > Z_THRESHOLD and r['med_z'] > Z_THRESHOLD:
        body += "<p><strong>⚡ 双频段共振上涨 — 资金大幅涌入，趋势确立</strong></p>"
    elif r['long_z'] > Z_THRESHOLD:
        body += "<p><strong>⚡ 长周期单频段突破 — 长期资金改善，关注持续性</strong></p>"
    elif r['long_z'] < -Z_THRESHOLD and r['med_z'] < -Z_THRESHOLD:
        body += "<p><strong>⚡ 双频段共振下跌 — 资金撤离，警惕逆转</strong></p>"
    else:
        body += "<p><strong>⚡ 长周期单频段跌破 — 长期资金走弱，谨慎观望</strong></p>"

body += "<hr><p><em>⚠️ 仅供研究参考，不构成投资建议</em></p>"

# Send email
try:
    msg = MIMEMultipart()
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    msg['Subject'] = f"[成交量小波分析告警] {len(triggered)}只ETF突破±{Z_THRESHOLD}σ"
    msg.attach(MIMEText(body, 'html', 'utf-8'))
    
    server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
    server.starttls()
    server.login(EMAIL_USER, EMAIL_PASS)
    server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
    server.quit()
    print(f"✅ Email sent to {EMAIL_TO}")
except Exception as e:
    print(f"❌ Email failed: {e}")
