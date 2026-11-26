"""波动率日报邮件发送模块"""
import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.header import Header
from pathlib import Path
import logging

log = logging.getLogger("vol_monitor")

SMTP_HOST = "smtp.163.com"
SMTP_PORT = 465
SMTP_USER = "songshang951@163.com"
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

SMTP_PASS = _smtp_password()


def send_daily_report_email(data, judgments, target_date, md_report, wavelet_image=None,
                            options_cfg=None, futures_cfg=None, underlyings_cfg=None,
                            to_addr="410119229@qq.com"):
    """发送完整波动率日报邮件（HTML格式）"""
    date_str = target_date.strftime("%Y-%m-%d")
    data_date = data.get("data_date", date_str)

    opts = data.get("options", {})
    high_count = sum(1 for o in opts.values() if o.get("iv_percentile", 0) >= 80)
    low_count = sum(1 for o in opts.values()
                    if o.get("iv_percentile", 0) is not None and o.get("iv_percentile", 100) <= 20)
    total_count = len(opts)

    subject = f"【波动率日报】{data_date} 共{total_count}个品种（极高{high_count}/极低{low_count}）"

    html_parts = [f"""
<h2 style="color:#2B2B2B;">📊 股指波动率监控日报</h2>
<p><b>日期：</b>{data_date}</p>
<p>
共 <b>{total_count}</b> 个品种 |
<b style="color:#C00000;">极高 {high_count}</b> |
<b style="color:#2E75B6;">极低 {low_count}</b> |
中性 {total_count - high_count - low_count}
</p>
<br>
"""]

    # IV表格（全部品种，按分位从高到低）
    html_parts.append("""
<h3 style="color:#555;">📈 隐含波动率(IV)一览</h3>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px;">
  <tr style="background:#2B2B2B;color:white;">
    <th>品种</th>
    <th>IV</th>
    <th>近1年分位</th>
    <th>Q25</th>
    <th>Q50</th>
    <th>Q80</th>
    <th>基差%</th>
    <th>方向</th>
    <th>建议策略</th>
  </tr>
""")

    sorted_opts = sorted(
        opts.items(),
        key=lambda x: x[1].get("iv_percentile", 0) or 0,
        reverse=True
    )

    for okey, opt in sorted_opts:
        oinfo = (options_cfg or {}).get(okey, {})
        iv = opt.get("iv")
        iv_pct = opt.get("iv_percentile")
        quantiles = opt.get("iv_quantiles", {})
        judgment = judgments.get(okey, {})

        if iv is None or iv <= 0:
            iv_str = "—"
            iv_pct_str = "—"
        else:
            iv_note = " <span style='color:#999;font-size:11px;'>(估)</span>" if opt.get("iv_source") == "estimated" else ""
            iv_str = f"{iv:.2f}%{iv_note}"
            if iv_pct is not None:
                if iv_pct >= 80:
                    iv_pct_str = f"<b style='color:#C00000;'>🔥{iv_pct:.0f}%</b>"
                elif iv_pct <= 20:
                    iv_pct_str = f"<b style='color:#2E75B6;'>❄️{iv_pct:.0f}%</b>"
                else:
                    iv_pct_str = f"{iv_pct:.0f}%"
            else:
                iv_pct_str = "—"

        q25 = quantiles.get(25, "—")
        q50 = quantiles.get(50, "—")
        q80 = quantiles.get(80, "—")

        # 基差
        ul_key = oinfo.get("underlying", "")
        basis = None
        for fkey, finfo in (futures_cfg or {}).items():
            if finfo.get("underlying") == ul_key:
                fut = data.get("futures", {}).get(fkey, {})
                basis = fut.get("basis_pct")
                break
        basis_str = f"{basis:+.2f}" if basis is not None else "—"

        direction = judgment.get("direction", "—")
        strategy = judgment.get("strategy", "—")

        if iv_pct is not None and iv_pct >= 80:
            row_bg = "#FFF5F5"
        elif iv_pct is not None and iv_pct <= 20:
            row_bg = "#F0F7FF"
        else:
            row_bg = "#FFFFFF"

        html_parts.append(f"""
  <tr style="background:{row_bg};">
    <td style="font-weight:bold;">{oinfo.get('name', okey)}</td>
    <td>{iv_str}</td>
    <td>{iv_pct_str}</td>
    <td>{q25}%</td>
    <td>{q50}%</td>
    <td>{q80}%</td>
    <td>{basis_str}</td>
    <td>{direction}</td>
    <td>{strategy}</td>
  </tr>
""")

    html_parts.append("""</table>
<p style="color:#888;font-size:11px;">Q25/Q50/Q80 = 近1年IV的25%/50%/80%分位值 | 单位：%</p>
<br>""")

    # 已实现波动率
    if data.get("intraday_rv"):
        html_parts.append("""
<h3 style="color:#555;">⏱️ 当日已实现波动率</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:80%;font-size:13px;">
  <tr style="background:#F5F5F5;">
    <th>品种</th>
    <th>当日涨跌</th>
    <th>已实现波动率(年化)</th>
    <th>数据截至</th>
  </tr>
""")
        for key, rv in data["intraday_rv"].items():
            name = (underlyings_cfg or {}).get(key, {}).get("name", key)
            ret_color = "#C00000" if rv.get("intraday_return_pct", 0) > 0 else "#2E75B6"
            html_parts.append(f"""
  <tr>
    <td>{name}</td>
    <td style="color:{ret_color};font-weight:bold;">{rv['intraday_return_pct']:+.2f}%</td>
    <td>{rv['rv_annual_pct']:.1f}%</td>
    <td>{rv['latest_time']}</td>
  </tr>
""")
        html_parts.append("""</table>
<p style="color:#888;font-size:11px;">已实现波动率(RV)基于当日5分钟K线计算</p>
<br>""")

    # 成交量小波图
    if wavelet_image and Path(wavelet_image).exists():
        html_parts.append("""
<h3 style="color:#555;">🌊 成交量小波分析</h3>
<img src="cid:wavelet_img" style="width:100%;max-width:800px;">
<br><br>
""")

    html_parts.append("""
<p style="color:#888;font-size:12px;margin-top:20px;">
本邮件由股指波动率监控系统自动发送，每个交易日发送。<br>
数据来源：AKShare / TuShare / 新浪财经 | IV分位基于近1年历史数据
</p>
""")

    body = "".join(html_parts)

    msg = MIMEMultipart("related")
    msg["From"] = Header(SMTP_USER)
    msg["To"] = Header(to_addr)
    msg["Subject"] = Header(subject, "utf-8")

    msg_html = MIMEMultipart("alternative")
    msg_html.attach(MIMEText(body, "html", "utf-8"))
    msg.attach(msg_html)

    if wavelet_image and Path(wavelet_image).exists():
        with open(wavelet_image, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<wavelet_img>")
            msg.attach(img)

    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    server.login(SMTP_USER, SMTP_PASS)
    server.sendmail(SMTP_USER, [to_addr], msg.as_string())
    server.quit()
    log.info(f"日报邮件发送成功 → {to_addr}")
