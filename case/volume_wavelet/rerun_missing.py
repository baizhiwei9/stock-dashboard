# -*- coding: utf-8 -*-
"""补推脚本：重抓首轮因 tushare IP 限频失败的标的（六标或行业ETF），单独推送飞书补充卡片 + 邮件告警（2σ规则）。
行业ETF补推时：仅当长/中期资金流 |z|>1.5σ 才发飞书补充卡（含近一周块图）；邮件仅当 |long_z|>2σ。"""
import importlib.util
import sys
import time
from datetime import datetime

W = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0'
sys.path.insert(0, W)
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs')

spec = importlib.util.spec_from_file_location("dwm", W + '/daily_wavelet_monitor.py')
dwm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dwm)

# 首轮 fetch 失败的标的填入此处；industry=True 表示行业ETF
MISSING = {
    '510300.SH': {'name': '沪深300ETF', 'industry': False},
    '512880.SH': {'name': '证券ETF', 'industry': True},
}

def main():
    print(f"[{datetime.now()}] Rerun missing targets")
    results = []
    failed_targets = []  # 补推仍失败的标的，报告（飞书+邮件）中显式说明，不得静默缺失
    for code, info in MISSING.items():
        industry = info.get('industry', False)
        last_err = None
        for attempt in range(5):
            try:
                r = dwm.analyze_etf(code, info['name'], industry=industry)
                results.append(r)
                print(f"✓ {info['name']} analyzed. long_z={r['long_z']:+.2f} med_z={r['med_z']:+.2f} "
                      f"over2sigma={r['over2sigma']} Δz={r['long_z_change']:+.2f}")
                break
            except Exception as e:
                last_err = e
                print(f"  retry {info['name']} ({attempt+1}/5): {str(e)[:50]}")
                time.sleep(15 * (attempt + 1))
        else:
            reason = f"补推后仍 fetch failed（{str(last_err)[:60]}）"
            print(f"✗ {info['name']} fetch failed after 5 attempts")
            failed_targets.append({'name': info['name'], 'reason': reason})

    if not results:
        print("No results, aborting.")
        return

    date_str = datetime.now().strftime('%Y-%m-%d')
    token = dwm.get_tenant_token()
    if not token:
        print("Failed to get token, aborting push")
        return

    # 飞书补充卡：六标全部补推展示；行业ETF仅触发 ±1.5σ 时展示（含近一周块图）；仍失败的标的显式说明
    text_blocks = []
    for r in results:
        if r.get('industry'):
            if abs(r['long_z']) > dwm.Z_THRESHOLD or abs(r['med_z']) > dwm.Z_THRESHOLD:
                text_blocks.append(dwm.generate_text_block(r))
                print(f"✅ 行业ETF {r['name']} 触发 ±{dwm.Z_THRESHOLD}σ，纳入补充卡展示")
            else:
                print(f"ℹ️ 行业ETF {r['name']} 未触发 ±{dwm.Z_THRESHOLD}σ，不推送补充卡")
        else:
            text_blocks.append(dwm.generate_text_block(r))

    if failed_targets:
        fail_lines = "\n".join(f"⚠️ {f['name']}：数据获取失败，本次未纳入分析（{f['reason']}）" for f in failed_targets)
        text_blocks.append(f"**【数据获取失败】**\n{fail_lines}\n")
        print(f"⚠️ 补推后仍有 {len(failed_targets)} 个标的获取失败，已写入补充卡说明")

    if text_blocks:
        print("\n===== GENERATED TEXT BLOCK =====")
        print("\n".join(text_blocks))
        print("=================================")
        ok, resp = dwm.send_text_card(text_blocks, date_str,
                                      with_industry=any(r.get('industry') for r in results))
        print(f"Feishu supplement card: {'✅' if ok else '❌'} {resp}")
    else:
        print("无触发标的，跳过飞书补充卡")

    # 邮件告警：仅 |long_z| > EMAIL_THRESHOLD(2σ) 的标的；无触发但有失败标的时发失败提示邮件
    triggered = [r for r in results if abs(r['long_z']) > dwm.EMAIL_THRESHOLD]
    if triggered:
        print(f"🚨 {len(triggered)} target(s) breached z=±{dwm.EMAIL_THRESHOLD}σ email threshold")
        subject = f"[成交量小波分析告警-补充] {len(triggered)}个标的突破±{dwm.EMAIL_THRESHOLD}σ"
        dwm.send_email_alert(subject, dwm.generate_email_body(triggered, failed=failed_targets or None))
    elif failed_targets:
        print(f"⚠️ {len(failed_targets)} target(s) fetch failed, sending failure notice email")
        subject = f"[成交量小波分析-数据缺失-补充] {len(failed_targets)}个标的获取失败"
        dwm.send_email_alert(subject, dwm.generate_email_body([], failed=failed_targets))
    else:
        print(f"✅ No threshold breaches at z=±{dwm.EMAIL_THRESHOLD}σ among missing targets")

    print(f"[{datetime.now()}] Rerun complete")

if __name__ == '__main__':
    main()