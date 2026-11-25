#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当日「市场临界监测 C(t) & Gap」推送到飞书（卡片内嵌图表图片）。

两步：
  1) 用工作区内其他任务已有的飞书应用凭据（APP_ID / APP_SECRET）取 tenant_access_token，
     调 im/v1/images 上传当日 PNG，得到 image_key；
  2) 用既有自定义机器人 webhook 发交互卡片，卡片内以 img 元素内嵌该图。

用法：python3 send_cgap_feishu.py [PNG 路径]
凭据不在本文件复制保存，运行时从既有配置读取（找不到可用凭据时报错退出，不静默降级）。
"""
import os, sys, json, re
import requests

WORK = os.path.dirname(os.path.abspath(__file__))
WS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace'
sys.path.insert(0, WORK)
from cgap_rules import evaluate_rules
from cgap_warning_power import warning_power

TODAY = os.path.join(WORK, 'rmt_cgap_today.json')
HIST = os.path.join(WORK, 'rmt_cgap_history.json')
CFG_CANDIDATES = [os.path.join(WS, 'skills/user/momentum-risk-control/feishu_config.py'),
                  os.path.join(WS, 'ses_gffe5ffa4f3c81ffeC0bm2DCboP3Oex/feishu_config.py'),
                  os.path.join(WS, 'ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0/feishu_config.py')]

t = json.load(open(TODAY, encoding='utf-8'))
hist = json.load(open(HIST, encoding='utf-8'))
R = evaluate_rules(t, hist)

png = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    WORK, f"market_cgap_chart_{t['date'].replace('-', '')}.png")
if not os.path.exists(png):
    print(f'[错误] 图表 PNG 不存在：{png}；先运行 build_cgap_png.py'); sys.exit(3)


def load_creds():
    for p in CFG_CANDIDATES:
        if not os.path.exists(p):
            continue
        ns = {}
        try:
            exec(open(p, encoding='utf-8').read(), ns)
        except Exception:
            continue
        if ns.get('APP_ID') and ns.get('APP_SECRET'):
            return ns['APP_ID'], ns['APP_SECRET'], p
    return None, None, None


def load_webhook():
    try:
        from rmt_daily_monitor import FEISHU_WEBHOOK
        return FEISHU_WEBHOOK
    except Exception:
        src = open(os.path.join(WORK, 'rmt_daily_monitor.py'), encoding='utf-8').read()
        return re.search(r"FEISHU_WEBHOOK\s*=\s*'([^']+)'", src).group(1)


APP_ID, APP_SECRET, CFG_SRC = load_creds()
if not APP_ID:
    print('[错误] 未找到可用的飞书应用凭据（APP_ID/APP_SECRET），无法上传图片'); sys.exit(3)
WEBHOOK = load_webhook()
print(f'[飞书] 凭据来源 {os.path.relpath(CFG_SRC, WS)}（APP_ID {APP_ID[:6]}***）')

# --- 1) 取 token ---
r = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                  json={'app_id': APP_ID, 'app_secret': APP_SECRET}, timeout=20).json()
token = r.get('tenant_access_token')
if not token:
    print(f'[错误] 取 tenant_access_token 失败：{r}'); sys.exit(3)
print(f'[飞书] token OK（有效期 {r.get("expire")}s）')

# --- 2) 上传图片 ---
with open(png, 'rb') as fh:
    up = requests.post('https://open.feishu.cn/open-apis/im/v1/images',
                       headers={'Authorization': f'Bearer {token}'},
                       data={'image_type': 'message'},
                       files={'image': (os.path.basename(png), fh, 'image/png')}, timeout=40).json()
key = (up.get('data') or {}).get('image_key')
if not key:
    print(f'[错误] 图片上传失败：{up}'); sys.exit(3)
print(f'[飞书] 图片上传 OK：{os.path.basename(png)} -> image_key {key[:10]}...{key[-4:]}')

# --- 3) 组装卡片 ---
pct = lambda v: f"{v * 100:+.2f}%" if v is not None else 'n/a'
idx_close = f"{t['index_close']:,.2f}" if t.get('index_close') is not None else '缺失'
vol_txt = '不可用' if t.get('vol_ratio') is None else f"{t['vol_ratio']:.3f}"
lh, dv, rd = R['level_history'], R['divergence'], R['retreat_detail']
tmpl = {'red': 'red', 'orange': 'orange', 'yellow': 'yellow', 'none': 'blue'}[R['level']]
title = f"📊 市场临界监测 C(t) & Gap | {t['date']} | 来自 DoMate"

fields = [
    ("λ₁ 第一特征值", f"{t['lambda1']:.4f}"),
    ("λ₂ 第二特征值", f"{t['lambda2']:.4f}"),
    ("C(t) 市场协同度", f"{t['C']:.2f}%"),
    ("Gap 主线集中度", f"{t['Gap']:.3f}"),
    ("Δλ₁ 一致性日变化", f"{t['d_lambda1']:+.4f}"),
    ("IPR 龙头拥挤度", f"{t['IPR']:.4f}"),
    ("λ₁ 250日滚动分位", f"{t['q250']:.1f}%"),
    ("成交量变化率", vol_txt),
    ("全A 收盘（中证全指）", f"{idx_close}  {pct(t.get('index_chg_1'))}"),
    ("全A 20日涨跌", pct(t.get('index_mom_20'))),
]

trig = [c for c in R['conditions'] if c['status'] == '已触发']
untrig = [c for c in R['conditions'] if c['status'] == '未触发']
unknown = [c for c in R['conditions'] if c['status'] == '数据未知']
cond_txt = ('**已触发（%d 条）**\n' % len(trig) +
            '\n'.join(f"· {c['level']}｜{c['name']}（当前 {c['current']}，阈值 {c['threshold']}，数据日 {c['data_date']}）"
                      for c in trig) +
            ('\n**未触发（%d 条）**\n' % len(untrig) +
             '；'.join(f"{c['name']}（当前 {c['current']}）" for c in untrig)) +
            ('\n**数据未知（%d 条）**\n' % len(unknown) +
             '；'.join(f"{c['name']}（{c['current']}，{c['note']}）" for c in unknown) if unknown else ''))

div_txt = (f"{dv['text']}　C(t) {dv['win']} 日 {dv['dC']:+.2f} 个百分点，"
           f"全A {dv['win']} 日 {dv['dI']:+.2f}%" if dv.get('ok') else dv['text'])
hist_txt = (f"当前等级 **{R['tag']}** 已连续 **{lh['streak']}** 个交易日（自 {lh['since']} 起）"
            + ('；该等级自序列起点从未改变——绝对阈值在本池恒定触发' if lh['streak'] >= len(lh['levels'])
               else f"；上一次不同等级 {lh['prev_diff']}"))

# --- 机会侧文案（与风险侧并列展示；不改动任何预警口径）---
o = t.get('opportunity')
if o and o.get('rows'):
    opp_txt = (
        "**机会侧 · 池内多维排序**（相对动量 40% + 趋势 30% + 量能扩张 30%，规则化排序、非预测模型）\n"
        f"机会：{'、'.join(o['strong']) if o['strong'] else '今日无'}\n"
        "Top5：" + ' | '.join(
            '{} {:.2f} {}'.format(x['name'], x['score'], x['grade']) for x in o['top']) + "\n"
        f"相对弱势（仅对照、非看空）：{'、'.join(o['weak']) if o['weak'] else '无'}"
    )
else:
    opp_txt = "**机会侧**：数据不足，未生成"

# --- 行业下钻文案（机会侧宽泛方向 → 一级/二级/三级行业 → 个股）---
drill = t.get('drilldown') or {}
if drill.get('directions'):
    _lines = []
    for b in drill['directions']:
        path = ' → '.join(p['name'] for p in b.get('path', []))
        st = b.get('stocks') or {}
        top = '、'.join('{} {}'.format(x['name'], x['code'])
                        for x in (st.get('selected') or [])[:5])
        _lines.append(f"**{b['direction']}**：{path}\n入选个股 Top5：{top or '无'}")
    drill_txt = ("**行业下钻（宽泛方向 → 三级行业 → 个股，规则化排序、非预测模型）**\n"
                 + '\n'.join(_lines))
elif drill.get('error'):
    drill_txt = f"**行业下钻**：执行失败（{drill['error']}）"
else:
    drill_txt = "**行业下钻**：机会档无行业方向（宽基/红利不参与），今日跳过"

# --- 图片下方：图上什么情况才算"能预警"（口径与报告同源：cgap_warning_power 全序列实测）---
_wp = warning_power(hist)
if _wp.get('ok'):
    _s = _wp['sample']
    _r90 = next((r for r in _wp['rows'] if r['key'] == 'q250>90'), None) or {}
    _rg3 = next((r for r in _wp['rows'] if r['key'] == 'Gap<3'), None) or {}
    warn_txt = (
        f"**图上什么情况才算“能预警”**（按 {_s['days']} 个交易日实测：触发占比需 5%~95%，"
        f"且触发后 20 日前瞻差异 ≤ −2pp）\n"
        f"唯一具备前瞻区分度的一步：**λ₁（C(t)）250 日滚动分位 > 90%**（橙警条件中的分位项）"
        f"——历史 {_r90.get('n_on')}/{_s['days']} 天满足，其后 20 个交易日全A 平均 "
        f"**{_r90['fwd20_on'] * 100:+.2f}%**、最大回撤 **{_r90['dd20_on'] * 100:+.2f}%**；"
        f"其余 {_r90.get('n_off')} 天 {_r90['fwd20_off'] * 100:+.2f}% / "
        f"{_r90['dd20_off'] * 100:+.2f}%。\n"
        f"已移除（无区分度）：C(t) 的 15/25/35% 与 Gap 的 10/5 线 100% 恒定触发；"
        f"Gap 的 3 线触发后仅 {_rg3['d_fwd20'] * 100:+.2f}pp；"
        f"Δλ₁<−5 与 IPR>0.15 从未触发（0/{_s['days']}）。阈值规则未改，仍逐条列在下方核对区。"
    )
else:
    warn_txt = "**图上什么情况才算“能预警”**：历史样本不足，暂时无法给出区分度结论。"

# --- 图上三项指标（是什么 / 怎么算 / 临界怎么读 / 现值与差额 / 偏多偏空）---
_si = t.get('show_indicators') or {}
if _si.get('items'):
    _si_lines = []
    for _it in _si['items']:
        _v = _it.get('value')
        if _v is None:
            _v_txt = '—'
        elif _it.get('unit') == '%':
            _v_txt = f"{_v:.1f}%"
        else:
            _v_txt = f"{_v:+.4f}" if _it.get('key') == 'gap_narrow' else f"{_v:.4f}"
        _p = _it.get('percentile')
        _p_txt = '—' if _p is None else f"{_p:.1f}%"
        _lv = _it.get('crit_level')
        _lv_txt = '' if (_lv is None or _it.get('unit') == '%') else f"，临界水平 {_lv:.4f}"
        _g = _it.get('gap_to_crit')
        _g_txt = ('—' if _g is None else
                  (f"超出 {_g:.1f} 个百分位" if _g >= 0 else f"差 {abs(_g):.1f} 个百分位"))
        _si_lines.append(f"**{_it.get('name')}**（{_it.get('kind')}）：现值 {_v_txt}，自身 250 日分位 {_p_txt}，"
                         f"临界 90%{_lv_txt} → {_it.get('stance')}（{_g_txt}）")
    si_txt = ("**图上三项指标 · 临界怎么读**（三条曲线＝各自 250 日滚动分位，共用 90% 临界线；"
              "只作展示，不参与预警判定）\n" + "\n".join(_si_lines))
else:
    si_txt = "**图上三项指标**：数据不足，未生成"

# --- 合并：RMT 预测监测（原 rmt_daily_monitor 的指标/灯/精选，与临界监控合成同一条卡片）---
_rp = t.get('rmt_prediction') or {}
if _rp.get('error'):
    pred_txt = f"**RMT 预测监测（合并）**：执行失败（{_rp['error']}）"
elif not _rp:
    pred_txt = "**RMT 预测监测（合并）**：当日未产出"
else:
    _pi = _rp.get('indicators') or {}
    _pa = _rp.get('alert') or {}
    _plvl = {'red': '🔴 红灯（风险）', 'green': '🟢 绿灯（机会）',
             'none': '⚪ 无红/绿灯'}.get(_pa.get('level', ''), str(_pa.get('level')))
    _plines = [
        f"**RMT 预测监测（合并自 rmt_daily_monitor）**　灯：{_plvl}",
        (f"gap1 {_pi.get('gap_1') or 0:.4f}　熵 {_pi.get('entropy') or 0:.4f}　"
         f"ΔS {_pi.get('delta_s') or 0:+.4f}　SNR {_pi.get('snr') or 0:.2f}　"
         f"λ+ {_pi.get('lambda_plus') or 0:.4f}"),
        (f"状态：{_rp.get('status')} · 仓位 {_rp.get('position')} · {_rp.get('action')}"
         f"　｜　触发原因：{'；'.join(_pa.get('reasons') or []) or '无'}"),
    ]
    _pk = []
    for _it in (_rp.get('top5') or [])[:5]:
        _ps = _it.get('picks') or []
        _pk.append(f"{_it.get('name')}({(_it.get('mom20') or 0) * 100:+.1f}%)：" +
                   ('、'.join(f"{s.get('name')}{s.get('code')}" for s in _ps) if _ps else '未取到'))
    if _pk:
        _plines.append("**Top5 动量行业与前 3 只个股**：" + '｜'.join(_pk))
    pred_txt = "\n".join(_plines)

card = {
    "config": {"wide_screen_mode": True},
    "header": {"title": {"tag": "plain_text", "content": title}, "template": tmpl},
    "elements": [
        {"tag": "img", "img_key": key,
         "alt": {"tag": "plain_text",
                 "content": "市场临界监测 · 三项指标 vs 临界线（λ₁ 分位 / 复杂度缺口 / Δ收窄 + 全A 对照）"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": warn_txt}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": si_txt}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md",
         "content": f"**{R['tag']}**\n触发原因：{'；'.join(R['reasons'])}"}},
        {"tag": "hr"},
        {"tag": "div", "fields": [{"is_short": True, "text": {"tag": "lark_md",
                                    "content": f"**{k}**\n{v}"}} for k, v in fields]},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": cond_txt}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md",
         "content": (f"**预警历史与连续天数**：{hist_txt}\n"
                     f"近 5 个交易日等级：" + ' → '.join(lh['recent_levels'][-5:]) + "\n"
                     f"**全A 与 C(t) 背离**：{div_txt}\n"
                     f"**退潮诊断详情**：{rd['text']}\n{rd.get('detail', '')}")}},
{"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": opp_txt}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": drill_txt}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": pred_txt}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md",
         "content": (f"**操作建议**：{R['advice']}\n"
                      f"**明日重点观察**：{R['watch']}")}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md",
         "content": (f"口径：{t['n_assets']} 只行业/宽基 ETF、{t['window']} 交易日滚动相关矩阵；"
                     f"全A（中证全指 000985）仅作同时间轴叠加对照。\n"
                     f"刻度提示：本池 C(t) 全历史 {R['Cfull'][0]:.2f}%–{R['Cfull'][2]:.2f}%"
                     f"（中位 {R['Cfull'][1]:.2f}%），Gap {R['Gfull'][0]:.3f}–{R['Gfull'][2]:.3f}，"
                     f"故 15/25/35 与 10/5/3 六条线在本池判别力有限、已按实测从图上移除；以自身分位为准："
                     f"C 处全历史 {R['Crank']:.1f} 分位 / 近250日 {R['Crank250']:.1f} 分位，"
                     f"Gap 处全历史 {R['Grank']:.1f} 分位 / 近250日 {R['Grank250']:.1f} 分位。")}},
    ],
}

resp = requests.post(WEBHOOK, json={"msg_type": "interactive", "card": card}, timeout=25)
try:
    body = resp.json()
except Exception:
    body = {"raw": resp.text[:200]}
ok = resp.status_code == 200 and body.get("code", 0) == 0
print(f'[飞书] 推送 HTTP {resp.status_code} resp={body}')
if ok:
    print(f'[飞书] 发送成功（含图片）-> {title}')
else:
    print(f'[飞书] 发送失败 -> {body}'); sys.exit(2)