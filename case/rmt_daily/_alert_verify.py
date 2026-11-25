# -*- coding: utf-8 -*-
"""校验：正文/HTML 关键内容（含宽基观测块）+ 发信分支计数。"""
import importlib.util, os
WORK = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('rm', os.path.join(WORK, 'rmt_daily_monitor.py'))
rm = importlib.util.module_from_spec(spec); spec.loader.exec_module(rm)

r = {'delta_s': 0.0980, 'entropy': 1.5467, 'gap_1': 0.5987, 'snr': 19.30,
     'lambda_plus': 2.7434, 'n_assets': 18, 'q': 0.3, 'status': '危机预警',
     'position': 0.7, 'action': '减仓30%', 'template': 'red',
     'top5': {'512800': 0.0312, '512890': 0.0264}, 'bottom5': {'512480': -0.055},
     'eigs_top5': [9.0, 3.2, 2.6, 2.2, 1.9]}
broad = {
    'sh000688': {'name': '科创50', 'code': 'sh000688', 'date': '2026-10-22', 'close': 1305.87,
                 'chg_1': -0.0279, 'mom_20': -0.0512, 'mom_60': 0.0143, 'drawdown_60': -0.1180,
                 'range_pos_60': 0.3521, 'high_60': 1480.0, 'low_60': 1150.0,
                 'ma20': 1350.0, 'ma60': 1290.0, 'ma_state': '震荡/转换',
                 'excess_20': -0.0321, 'missing': False},
    'sz399006': {'name': '创业板指', 'code': 'sz399006', 'date': '2026-10-22', 'close': 2576.90,
                 'chg_1': -0.0437, 'mom_20': -0.0765, 'mom_60': -0.0021, 'drawdown_60': -0.1520,
                 'range_pos_60': 0.2103, 'high_60': 3040.0, 'low_60': 2400.0,
                 'ma20': 2700.0, 'ma60': 2650.0, 'ma_state': '空头排列',
                 'excess_20': -0.0574, 'missing': False},
    'sh000300': {'name': '沪深300', 'code': 'sh000300', 'date': '2026-10-22', 'close': 4036.30,
                 'chg_1': -0.0184, 'mom_20': -0.0191, 'mom_60': 0.0088, 'drawdown_60': -0.0700,
                 'range_pos_60': 0.4010, 'high_60': 4340.0, 'low_60': 3860.0,
                 'ma20': 4100.0, 'ma60': 4050.0, 'ma_state': '震荡/转换',
                 'excess_20': None, 'missing': False},
}
broad_missing = dict(broad)
broad_missing['sz399006'] = {'name': '创业板指', 'code': 'sz399006', 'missing': True,
                             'reason': '最新交易日无真实数据（K线与实时接口均无）'}

alert = rm.evaluate_alert(r)
picks = {'512800': [{'name': '招商银行', 'code': '600036', 'mom_20': 0.05}]}
body = rm.build_alert_body(r, alert, picks, broad)
body_nobroad = rm.build_alert_body(r, alert, picks)
body_missing = rm.build_alert_body(r, alert, picks, broad_missing)
html = rm._md_to_html(body)
checks = [
    ('正文含红灯标题', '🔴 RMT 风险红灯' in body),
    ('正文含ΔS数值', '| ΔS | +0.0980' in body),
    ('正文含Gap1/熵/SNR', all(x in body for x in ('| Gap₁ | 0.5987', '| 熵 | 1.5467', '| SNR | 19.30'))),
    ('正文含个股推荐', '招商银行（600036）+5.0%' in body),
    ('正文含阈值口径', '红灯 ΔS≥+0.08' in body and '绿灯 熵≤1.45 或 ΔS≤-0.06' in body),
    ('正文含免责声明与署名', '投资需谨慎' in body and '来自 DoMate' in body),
    ('正文含宽基区块标题', '📈 宽基指数观测（科创50 / 创业板指，独立于 RMT 相关矩阵）' in body),
    ('正文含科创50与创业板指数值', '科创50 1305.87' in body and '创业板指 2576.90' in body),
    ('正文含沪深300基准标记', '沪深300（基准） 4036.30' in body),
    ('正文含相对强弱超额', '较沪深300 -5.74%' in body),
    ('正文含独立口径说明', '不并入 18 只行业 ETF 相关矩阵' in body),
    ('无宽基数据时不报错且不出现区块', '宽基指数观测' not in body_nobroad),
    ('宽基缺失时如实标注', '创业板指（sz399006）：数据缺失' in body_missing and '科创50 1305.87' in body_missing),
    ('HTML 有表格', '<table' in html and '</table>' in html and '<tr>' in html),
    ('HTML 加粗已转换', '<strong>触发原因</strong>' in html and '**' not in html),
    ('HTML 缩进推荐项', 'padding-left:18px' in html),
    ('HTML 无原始 markdown 表格符', '| --- |' not in html),
]
ok = all(v for _, v in checks)
for n, v in checks:
    print(f"[{'OK ' if v else 'FAIL'}] {n}")

# 分支计数：红/绿灯才调用发信
calls = []
rm.send_alert_email = lambda *a, **k: calls.append(1) or True
Cases = [(0.098, 1.5467), (0.065, 1.5288), (0.079, 1.53), (0.01, 1.45),
         (0.01, 1.52), (-0.06, 1.51), (0.09, 1.40), (-0.082, 1.52)]
for ds, ent in Cases:
    a = rm.evaluate_alert(dict(r, delta_s=ds, entropy=ent))
    if a['level'] != 'none':
        rm.send_alert_email(dict(r, delta_s=ds, entropy=ent), a, {}, broad)
print(f"\n[{'OK ' if len(calls) == 5 else 'FAIL'}] 8 例中发信 {len(calls)} 次（期望 5：red 3 + green 2）")
ok = ok and len(calls) == 5
print('\n结论:', 'PASS' if ok else 'FAIL')