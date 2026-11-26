# -*- coding: utf-8 -*-
"""五指数信号 · 交互式卡片（带置信度 = 历史同档胜率 → 直接给仓位）

大佬 2026-10-19 需求：
  「报告变成可交互式卡片的，然后带上置信度。我看不到置信度，就是有多少置信度
    让我有上多大仓位。另外你要告诉我它的历史的胜率是多少。」

数据来源（全部本地，纯脚本 0 token）：
  · 当日信号 + SHAP      → .cache/wide_ens/shap/<code>.json（signal_shap.py 产出）
  · 同档历史胜率校准表    → .cache/wide_ens/ms/<code>_lgb_tri_d<depth>_T<h>.json

仓位规则（写死在 CARD 里，可调）：
  胜率 < 55%          → 0（不参与，噪声区）
  55% ≤ 胜率 < 60%    → cap × 0.3
  60% ≤ 胜率 < 65%    → cap × 0.5
  65% ≤ 胜率 < 70%    → cap × 0.7
  胜率 ≥ 70%          → cap × 1.0
  再乘方向系数：看多=1，观望=0，看空=0（做空不做，A股无券）

用法：
  python3 bin/wide_ens/signal_card.py            # 打印卡片 JSON
  python3 bin/wide_ens/signal_card.py --push     # 推飞书私聊
  python3 bin/wide_ens/signal_card.py --push --chat oc_xxx
"""
import os, sys, json, argparse, subprocess

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHAP_DIR = os.path.join(WS, '.cache/wide_ens/shap')
MS_DIR = os.path.join(WS, '.cache/wide_ens/ms')
CARD_FILE = os.path.join(WS, '.cache/wide_ens/signal_card.json')

# (code, name, h, depth, thr, cap, note)
CFG = [
    ('000852_SH', '中证1000', 5, 3, 0.60, 1.00, '最稳'),
    ('399006_SZ', '创业板',   1, 3, 0.55, 0.80, '日频'),
    ('000300_SH', '沪深300',  5, 5, 0.55, 0.70, ''),
    ('000905_SH', '中证500',  5, 5, 0.70, 0.40, '样本少'),
    ('000688_SH', '科创50',   5, 3, 0.30, 0.50, '上限低'),
]


def jload(p):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None


def load_calib(code, h, depth, thr):
    """取同档历史胜率：thr 对应 bands 里的档位键（向上取最近档）"""
    p = os.path.join(MS_DIR, '%s_lgb_tri_d%d_T%d.json' % (code, depth, h))
    d = jload(p)
    if not d:
        return None
    bands = d.get('bands') or {}
    keys = sorted((float(k.replace('th', '')), k) for k in bands)
    pick = None
    for val, k in keys:
        if abs(val - thr) < 1e-9:
            pick = k
            break
    if pick is None:
        for val, k in keys:
            if val >= thr:
                pick = k
                break
    if pick is None:
        pick = keys[-1][1]
    b = bands[pick]
    return dict(
        band=pick,
        all_n=b['all']['n'], all_hit=b['all']['hit'], all_net=b['all'].get('net'),
        oos_n=b['oos']['n'], oos_hit=b['oos']['hit'], oos_net=b['oos'].get('net'),
        is_n=b['is_']['n'], is_hit=b['is_']['hit'],
        n_eval=d.get('n_eval'), base_up=d.get('base_up'),
    )


def pos_from_hit(hit, cap):
    """置信度 → 仓位（胜率越高，仓位越大；上限 = 该指数的 cap）"""
    if hit is None:
        return 0.0, '—'
    if hit < 0.55:
        return 0.0, '噪声区，不做'
    if hit < 0.60:
        return cap * 0.3, '低置信'
    if hit < 0.65:
        return cap * 0.5, '中置信'
    if hit < 0.70:
        return cap * 0.7, '较高置信'
    return cap * 1.0, '高置信'


def short_reasons(txt, k=3):
    if not txt:
        return '—'
    parts = [p.strip() for p in txt.split('、') if p.strip()]
    return '、'.join(parts[:k])


def build_rows():
    rows = []
    for code, name, h, depth, thr, cap, note in CFG:
        sg = jload(os.path.join(SHAP_DIR, '%s.json' % code.split('_')[0]))
        if not sg:
            rows.append(dict(code=code, name=name, tf='T+%d' % h, thr=thr, cap=cap, note=note,
                             missing='信号缺失'))
            continue
        r = sg['rows'][0] if sg.get('rows') else None
        if not r:
            rows.append(dict(code=code, name=name, tf='T+%d' % h, thr=thr, cap=cap, note=note,
                             missing='信号为空'))
            continue
        score = r['score']
        if score >= thr:
            act, d = '看多', 1
        elif score <= -thr:
            act, d = '看空', -1
        else:
            act, d = '观望', 0
        cal = load_calib(code, h, depth, thr) or {}
        hit = cal.get('oos_hit') if cal else None
        pos, level = pos_from_hit(hit, cap)
        if d <= 0:
            pos = 0.0
        rows.append(dict(
            code=code, name=name, tf='T+%d' % h, thr=thr, cap=cap, note=note,
            date=r.get('date'), score=score, act=act, dirn=d,
            p_up=r.get('p_up'), p_down=r.get('p_down'),
            hit=hit, hit_all=cal.get('all_hit'), oos_n=cal.get('oos_n'),
            all_n=cal.get('all_n'), net_oos=cal.get('oos_net'),
            band=cal.get('band'), pos=pos, level=level,
            up=short_reasons(r.get('up')), down=short_reasons(r.get('down')),
            base_up=cal.get('base_up'),
        ))
    return rows


def _md(text):
    return {'tag': 'markdown', 'content': text}


def conf_from_hit(hit):
    """历史胜率 → 置信档位（文字），用于在表格里直白地表达“有多大把握”。"""
    if not hit:
        return '—'
    if hit < 0.55:
        return '低（噪声）'
    if hit < 0.60:
        return '偏低'
    if hit < 0.65:
        return '中等'
    if hit < 0.70:
        return '较高'
    return '高'


def _hr():
    return {'tag': 'hr'}


def _row(cells, weights=None, head=False):
    cols = []
    n = len(cells)
    w = weights or [1] * n
    for i, c in enumerate(cells):
        txt = ('**%s**' % c) if head else str(c)
        cols.append({'tag': 'column', 'width': 'weighted', 'weight': w[i],
                     'elements': [{'tag': 'markdown', 'content': txt}]})
    return {'tag': 'column_set', 'flex_mode': 'none', 'background_style': 'default',
            'columns': cols}


def build_card(rows):
    ok = [r for r in rows if not r.get('missing')]
    date = ok[0]['date'] if ok else ''
    longs = [r for r in ok if r['act'] == '看多']
    # 组合建议仓位 = 各「已触发」指数仓位之和（上限 100%）
    # 未触发的一律 0 仓位，所以直接相加就是组合层面的总仓
    combo = min(1.0, sum(r['pos'] for r in ok)) if ok else 0.0

    if len(longs) >= 2:
        flag, tpl = '🚨', 'red'
    elif len(longs) == 1:
        flag, tpl = '⚠️', 'orange'
    else:
        flag, tpl = '💤', 'blue'

    el = []
    el.append(_md('📎 来自钉钉OpenClaw'))

    # ── 一、结论：今天的观点 + 仓位 ──
    el.append(_md('**① 今天的结论（观点 / 置信度 / 仓位）**'))
    el.append(_row(['建议总仓位', '看多的指数', '置信度'], [2, 3, 2], head=True))
    trig = [r for r in ok if r['dirn'] != 0]
    best = max((r['hit'] or 0) for r in trig) if trig else None
    el.append(_row(['**%.0f%%**' % (combo * 100),
                    ('、'.join(r['name'] for r in longs) if longs else '无（全部观望）'),
                    (conf_from_hit(best) + '（%.1f%%）' % (best * 100)) if best else '—'],
                   [2, 3, 2]))
    # 逐个指数一句话观点
    for r in ok:
        if r['dirn'] > 0:
            line = '<font color=\'green\'>🔴★ **%s：看多**</font>，置信度 %s（历史同档 %.1f%%），建议仓位 **%.0f%%**' \
                   % (r['name'], conf_from_hit(r['hit']), (r['hit'] or 0) * 100, r['pos'] * 100)
        elif r['dirn'] < 0:
            line = '<font color=\'red\'>🟢★ **%s：看空**</font>（做空/回避），置信度 %s' \
                   % (r['name'], conf_from_hit(r['hit']))
        else:
            line = '<font color=\'grey\'>⚪ **%s：观望**（不进场；已持仓则平掉）</font>' % r['name']
        el.append(_md(line))
    el.append(_md('<font color=\'grey\'>总仓位 = 各「已触发」指数的建议仓位相加（未触发的按 0）；'
                  '胜率低于 55% 的即使触发也按 0 处理（噪声区不做）</font>'))

    el.append(_hr())

    # ── 二、五指数总表：观点 / 置信度 / 仓位 ──
    el.append(_md('**② 五指数：观点 × 置信度 × 建议仓位**'))
    el.append(_row(['指数', '观点', 'score', '置信度', '仓位', '周期'], [2, 1, 1, 2, 1, 1], head=True))
    for r in rows:
        if r.get('missing'):
            el.append(_row([r['name'], '—', '—', r['missing'], '—', r['tf']]))
            continue
        cl = 'green' if r['act'] == '看多' else ('red' if r['act'] == '看空' else 'grey')
        nm = r['name']
        if r['code'] == '000688_SH':
            nm = "%s<br/><font color='grey'>方法一</font>" % nm
        # 置信度：只在“已触发”时表意；观望时直白写“未触发”
        if r['dirn'] == 0:
            cf = '<font color=\'grey\'>未触发</font>'
        else:
            cf = '**%s**' % conf_from_hit(r['hit'])
        el.append(_row([
            nm,
            '<font color=\'%s\'>**%s**</font>' % (cl, r['act']),
            '%+.2f<br/>阈%.2f' % (r['score'], r['thr']),
            cf,
            ('**%.0f%%**' % (r['pos'] * 100)) if r['pos'] else '<font color=\'grey\'>0%</font>',
            r['tf'],
        ]))
    el.append(_md('<font color=\'grey\'>**score 是什么**：三分类模型（跌/平/涨）的概率差 = **P(涨) − P(跌)**，'
                  '取值 −1 ~ +1。它是「模型内部分数」，**不是胜率、不是置信度**，'
                  '只用来和阈值比大小决定「触没触发」。</font>'))
    el.append(_md('<font color=\'grey\'>**置信度怎么来的**：根据该指数在「|score| ≥ 当前阈值」这一档位上的'
                  '历史样本外真实命中率分档：<55% 低（噪声）｜55~60% 偏低｜60~65% 中等｜65~70% 较高｜≥70% 高。</font>'))

    # ── 二·附、历史胜率详表（放表格下方，不占主表列）──
    el.append(_md('**②·附 历史胜率（上面置信度的依据）**'))
    el.append(_row(['指数', '同档胜率', '样本数 n', '基础上涨率'], [2, 2, 2, 2], head=True))
    for r in rows:
        if r.get('missing'):
            continue
        hit_s = ('**%.1f%%**' % (r['hit'] * 100)) if r['hit'] else '—'
        base = ('%.1f%%' % (r['base_up'] * 100)) if r.get('base_up') else '—'
        el.append(_row([r['name'], hit_s, str(r['oos_n'] or '—'), base]))
    el.append(_md('<font color=\'grey\'>上表胜率 = 该指数在「|score| ≥ 当前阈值」档位上、样本外期的真实方向命中率'
                  '（不是模型自报概率）；基础上涨率 = 同期无条件看涨的天数占比，用来对照是否有超额。</font>'))
    el.append(_md('<font color=\'grey\'>注：观望行的胜率是该档位「一旦触发」的历史水平，此时并未触发，所以仓位仍为 0。</font>'))

    el.append(_hr())

    # ── 二·补、科创50 方法二 / 方法三 ──
    kc = jload(os.path.join(WS, '.cache/wide_ens/kc50_tristate.json'))
    if kc and kc.get('methods'):
        el.append(_md('**②·补 科创50 专项：方法二 / 方法三（同一批预测、两套门槛）**'))
        el.append(_md('<font color=\'grey\'>方法一 = 上面表中那行（多尺度 T+5，阈0.30，OOS 58.3%）。'
                      '下面两个方法用夸克包日频模型重训结果（2026-10-19 关闭低置信冻结的干净口径），'
                      '预测标的是“今日收盘→下一交易日收盘”。</font>'))
        el.append(_row(['方法', '今日观点', '置信度', '强度/触发线', '仓位建议', '方向'],
                       [2, 2, 1, 2, 1, 1], head=True))
        for key in ('m2', 'm3'):
            st = kc['methods'].get(key)
            if not st:
                continue
            lg, sh = st.get('long') or {}, st.get('short') or {}
            state = st.get('state')
            # 观点 + 方向（今天的结论，不是历史）
            if state == 'LONG':
                view = '<font color=\'green\'>**看多**</font>'
                dirn = '★ 做多'
                hit_use = lg.get('hit')
                pos = 1.0
            elif state == 'SHORT':
                view = '<font color=\'red\'>**看空**</font>'
                dirn = '★ 做空'
                hit_use = sh.get('hit')
                pos = 1.0
            else:
                view = '<font color=\'grey\'>观望</font>'
                dirn = '平仓/空仓'
                pos = 0.0
            cf = conf_from_hit(hit_use) if pos else '<font color=\'grey\'>未触发</font>'
            ratio = st.get('amp_ratio')
            rats = ('%.0f%%' % (ratio * 100)) if ratio == ratio else '—'
            el.append(_row([st['label'].split(' · ')[0] + '<br/><font color=\'grey\'>' +
                            st['label'].split(' · ')[1] + '</font>',
                            view,
                            ('**%s**' % cf) if pos else cf,
                            '%s<br/><font color=\'grey\'>线%.3f</font>' % (rats, st.get('need') or 0),
                            ('**100%%**' if pos else '<font color=\'grey\'>0%</font>'),
                            dirn],
                           [2, 2, 1, 2, 1, 1]))
        # 逐方法一句话观点（今日结论优先）
        for key in ('m2', 'm3'):
            st = kc['methods'].get(key)
            if not st:
                continue
            nm = st['label'].split(' · ')[0]
            ratio = (st.get('amp_ratio') or 0) * 100
            if st.get('state') == 'LONG':
                el.append(_md('<font color=\'green\'>🔴★ **%s：看多**</font>（买入/加仓）　'
                              '置信度 %s　今日强度 %.0f%% 已过线'
                              % (nm, conf_from_hit((st.get('long') or {}).get('hit')), ratio)))
            elif st.get('state') == 'SHORT':
                el.append(_md('<font color=\'red\'>🟢★ **%s：看空**</font>（做空/清仓）　'
                              '置信度 %s　今日强度 %.0f%% 已过线'
                              % (nm, conf_from_hit((st.get('short') or {}).get('hit')), ratio)))
            else:
                el.append(_md('<font color=\'grey\'>⚪ **%s：观望**</font>（不进场；已持仓则平掉）　'
                              '今日强度只有触发线的 %.0f%%'
                              % (nm, ratio)))

        # 历史胜率放下方段落（不占列）
        el.append(_md('**②·补·附 方法二/三 历史胜率（上面置信度的依据）**'))
        el.append(_row(['方法', '做多胜率', '做空胜率', '出手频率', '全期收益/回撤'],
                       [2, 2, 2, 1, 2], head=True))
        for key in ('m2', 'm3'):
            st = kc['methods'].get(key)
            if not st:
                continue
            lg, sh = st.get('long') or {}, st.get('short') or {}
            lg_s = ('**%.1f%%** n=%d p=%.3f' % (lg['hit'] * 100, lg['n'], lg['p'])) if lg.get('n') else '—'
            sh_s = ('**%.1f%%** n=%d p=%.2f' % (sh['hit'] * 100, sh['n'], sh['p'])) if sh.get('n') else '—'
            cover = st.get('all', {}).get('cover')
            backtest = ('%+.1f%% / %.1f%%' % (kc['strat_cum'] * 100, kc['mdd'] * 100)
                        if key == 'm2' else '（同策略口径，未单独回测）')
            el.append(_row([st['label'].split(' · ')[0], lg_s, sh_s,
                            ('%.0f%%' % (cover * 100)) if cover else '—', backtest],
                           [2, 2, 2, 1, 2]))
        el.append(_md('<font color=\'grey\'>**两个方法怎么用**：方法二门槛低、出手多（17.7% 的交易日），'
                      '做多 66.7%(p<0.001) / 做空 64.2%(p=0.027)；'
                      '方法三门槛高、出手少（6.3%），做多 65.7%、做空 72.7%，'
                      '**但做空 n 只有 11，p=0.11 不显著，只能当提示不能下注**。</font>'))
        el.append(_md('<font color=\'grey\'>回测（方法二，2021-03 起）：策略累计 %+.1f%%、最大回撤 %.1f%%，'
                      '同期持有 %+.1f%%。今日 amp=%.4f，只到做多线的 %.0f%% → 两法均未触发。</font>'
                      % (kc['strat_cum'] * 100, kc['mdd'] * 100, kc['bh_cum'] * 100,
                         kc['amp'], (kc['methods']['m2'].get('amp_ratio') or 0) * 100)))

    el.append(_hr())

    # ── 三、逐指数：为什么看涨/看跌（SHAP） ──
    el.append(_md('**③ 为什么（SHAP 归因，各取前 3）**'))
    for r in rows:
        if r.get('missing'):
            continue
        cl = 'green' if r['act'] == '看多' else ('red' if r['act'] == '看空' else 'grey')
        if r['dirn'] == 0:
            cf = '未触发（该档历史 %s）' % (('%.1f%%' % (r['hit'] * 100)) if r['hit'] else '—')
        else:
            cf = '%s（该档历史 %s）' % (conf_from_hit(r['hit']),
                                      ('%.1f%%' % (r['hit'] * 100)) if r['hit'] else '—')
        el.append(_md('**%s** %s　<font color=\'%s\'>%s</font>　置信度 %s　仓位 %s'
                      % (r['name'], r['tf'], cl, r['act'], cf,
                         ('%.0f%%' % (r['pos'] * 100)) if r['pos'] else '0%')))
        el.append(_md('　⬆ 推涨：%s' % r['up']))
    el.append(_md('　⬇ 压跌：%s' % r['down']))

    el.append(_hr())

    # ── 四、口径 ──
    el.append(_md('**④ 怎么用这张卡定仓位**'))
    el.append(_md('· **胜率 ≥70%** → 打满该指数上限仓位；65~70% → 七成；60~65% → 五成；'
                  '55~60% → 三成；**<55% 不做**（噪声区）'))
    el.append(_md('· 各指数上限不同：中证1000 100%、创业板 80%、沪深300 70%、'
                  '科创50 50%、中证500 40%（样本少）'))
    el.append(_md('· 胜率来源：多尺度日度特征 + ETF量能/份额，LightGBM 三分类，'
                  '滚动 750 日训练、每 20 日重训，样本外从 2023-01-01 起算'))
    el.append(_md('· 观望也照发，因为**空仓也是一种仓位**：胜率不够时不做，比做错便宜'))

    el.append(_hr())

    # ── 五、今年预测准确度（日频台账，历史已结算回填，滚动统计） ──
    try:
        import daily_ledger as DL
        for kind, payload in DL.build_md():
            if kind == 'md':
                el.append(_md(payload))
            elif kind == 'row':
                rows_, w = payload
                el.append(_row(rows_[0], w, head=True))
                for x in rows_[1:]:
                    el.append(_row(x))
    except Exception as e:
        el.append(_md('<font color=\'grey\'>⑤ 今年准确度台账读取失败：%s</font>' % e))

    el.append(_md('<font color=\'grey\'>以上为量化信号，不构成投资建议</font>'))

    card = {
        'config': {'wide_screen_mode': True},
        'header': {'template': tpl,
                   'title': {'tag': 'plain_text',
                             'content': '%s 五指数信号 · 置信度与仓位 · %s' % (flag, date)}},
        'elements': el,
    }
    return card, dict(date=date, combo=combo, longs=[r['name'] for r in longs], rows=rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--chat', default=None)
    ap.add_argument('--print-json', action='store_true')
    a = ap.parse_args()

    rows = build_rows()
    card, meta = build_card(rows)
    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    os.makedirs(os.path.dirname(CARD_FILE), exist_ok=True)
    json.dump(card, open(CARD_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('[card] elements=%d bytes=%d date=%s combo=%.0f%% longs=%s'
          % (len(card['elements']), len(body.encode()), meta['date'],
             meta['combo'] * 100, ','.join(meta['longs']) or '-'))
    for r in meta['rows']:
        if r.get('missing'):
            print('  %-8s %s' % (r['name'], r['missing']))
            continue
        print('  %-8s %-3s %-4s score=%+.3f 胜率=%s(%.1f%%,n=%d) 仓位=%.0f%%'
              % (r['name'], r['tf'], r['act'], r['score'], r['band'],
                 (r['hit'] or 0) * 100, r['oos_n'] or 0, r['pos'] * 100))

    if a.print_json:
        print(body[:2000])

    if a.push:
        cmd = ['python3', os.path.join(WS, 'bin/feishu_push_card.py'),
               '--card-file', CARD_FILE]
        if a.chat:
            cmd += ['--chat', a.chat]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print('[push]', (r.stdout or r.stderr).strip()[:300])
        sys.exit(0 if r.returncode == 0 else 1)


if __name__ == '__main__':
    main()
