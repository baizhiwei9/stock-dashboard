# -*- coding: utf-8 -*-
"""集成演练（宽基版）：执行改造后的 main() 全流程，拦截所有对外发送动作，
验证 取数 → compute_rmt（口径不变）→ 红/绿灯判定 → 飞书卡片 → 邮件正文 → JSON 落盘 的完整链路。

拦截方式：替换 rm.requests.post（飞书）与 rm.send_via_dws / rm.send_via_smtp（邮件），
不产生任何真实外部推送，但拿到的是真实构造的卡片与邮件正文。
"""
import importlib.util, os, json, datetime

WORK = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.datetime.now().strftime('%Y%m%d')
RMT_JSON = os.path.join(WORK, f'rmt_report_{TODAY}.json')

# 运行前先把当日已有报告读进内存，作为「口径未变」的对照基准
baseline = json.load(open(RMT_JSON)) if os.path.exists(RMT_JSON) else None

spec = importlib.util.spec_from_file_location('rm', os.path.join(WORK, 'rmt_daily_monitor.py'))
rm = importlib.util.module_from_spec(spec); spec.loader.exec_module(rm)

sent = {}


class _FakeResp:
    def json(self):
        return {'code': 0, 'msg': 'success'}


def fake_post(url, json=None, timeout=None):
    sent['feishu_card'] = json
    return _FakeResp()


def fake_dws(subject, body):
    sent['email'] = {'channel': 'dws', 'subject': subject, 'body': body}
    return True


def fake_smtp(subject, html):
    sent['email_smtp'] = {'channel': 'smtp', 'subject': subject, 'html': html}
    return True


rm.requests.post = fake_post
rm.send_via_dws = fake_dws
rm.send_via_smtp = fake_smtp

rm.main()

report = json.load(open(RMT_JSON))
card_str = json.dumps(sent.get('feishu_card', {}), ensure_ascii=False)

# 邮件正文：今日为黄灯不发信，故用同一份真实指标按红灯演练一次正文渲染
sim = rm.evaluate_alert(dict(report, delta_s=0.098, entropy=1.5467))
body_sim = rm.build_alert_body(report, sim, report.get('stock_picks', {}), report.get('broad_index'))

checks = [
    ('飞书卡片已构造', bool(sent.get('feishu_card'))),
    ('卡片含宽基观测区块标题', '宽基指数观测' in card_str),
    ('卡片含科创50', '科创50' in card_str),
    ('卡片含创业板指', '创业板指' in card_str),
    ('卡片含科创50收盘价', f"{report['broad_index']['sh000688']['close']:.2f}" in card_str),
    ('卡片数据日为最新交易日', report.get('broad_index', {}).get('sh000688', {}).get('date') == report.get('broad_index', {}).get('sh000300', {}).get('date')),
    ('卡片仍含 Top5 行业与个股', 'Top5 动量行业' in card_str and '个股推荐' in card_str),
    ('邮件正文（红灯演练）含宽基区块', '宽基指数观测' in body_sim and '科创50' in body_sim and '创业板指' in body_sim),
    ('邮件正文含宽基数值', f"{report['broad_index']['sz399006']['close']:.2f}" in body_sim),
    ('邮件正文标注独立口径', '不并入 18 只行业 ETF 相关矩阵' in body_sim),
    ('今日未触发红/绿灯（黄灯不发信）', 'email' not in sent),
]

if baseline:
    same = all(abs(baseline[k] - report[k]) < 1e-12 for k in ('gap_1', 'entropy', 'delta_s', 'snr'))
    checks.append((f'RMT 指标与改造前一致（{baseline["gap_1"]:.4f}/{baseline["entropy"]:.4f}/'
                   f'{baseline["delta_s"]:+.4f}/{baseline["snr"]:.2f}）', same))
else:
    print('[WARN] 未找到改造前的当日报告，跳过口径一致性对照')

ok = all(v for _, v in checks)
print('\n===== 演练校验 =====')
for n, v in checks:
    print(f"[{'OK ' if v else 'FAIL'}] {n}")
print('\n宽基观测块:', json.dumps(report.get('broad_index'), ensure_ascii=False, indent=2))
print('今日指标:', {k: round(report[k], 4) for k in ('gap_1', 'entropy', 'delta_s', 'snr')},
      report['status'], report['position'])
print('红/绿灯:', report['alert'])
print('结论:', 'PASS' if ok else 'FAIL')