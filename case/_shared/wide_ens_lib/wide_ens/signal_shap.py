# -*- coding: utf-8 -*-
"""五指数统一信号 + SHAP 归因（说清为什么看涨/看跌）

用法:
  python3 bin/wide_ens/signal_shap.py            # 打印全部+SHAP
  python3 bin/wide_ens/signal_shap.py --push     # 推飞书
  python3 bin/wide_ens/signal_shap.py --topk 6   # 每边列几个因子
"""
import os, sys, json, argparse, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hunt70 as H
import multi_scale as MS
from shap_explain import shap_explain, format_reasons, cn_name

CFG = [
    ('000852_SH', 5, 3, 0.60, 1.00, '70%', '最稳'),
    ('399006_SZ', 1, 3, 0.55, 0.80, '67-71%', '日频'),
    ('000300_SH', 5, 5, 0.55, 0.70, '67%', ''),
    ('000905_SH', 5, 5, 0.70, 0.40, '66-73%', '样本少'),
    ('000688_SH', 5, 3, 0.30, 0.50, '62.5%', '上限低'),
]


def predict_with_shap(code, h, depth, seed=0, topk=6):
    X, y_ret, lab = MS.build(code, h)
    q1, q2 = lab['ret'].quantile(0.30), lab['ret'].quantile(0.70)

    def label(v):
        if v <= q1: return 0
        if v >= q2: return 2
        return 1

    y_all = y_ret.apply(lambda v: label(v) if pd.notna(v) else -1)
    idx = X.index
    n = len(idx)
    lo = max(0, n - MS.TRAIN_WIN)
    seg = y_all.iloc[lo:n]
    ok = seg >= 0
    if ok.sum() < MS.MIN_TRAIN:
        return None
    Xtr = X.iloc[lo:n][ok.values]
    ytr = seg[ok].values.astype(int)
    m = MS.mk_lgb(seed, 400, depth, 0.05, 30)
    m.fit(Xtr.values, ytr)

    Xrow = X.iloc[[n - 1]].values
    pc = m.predict_proba(Xrow)[0]
    score = float(pc[2] - pc[0])
    # raw margin 差（SHAP 的加总目标）
    raw = m.predict(Xrow, raw_score=True)[0]
    margin = float(raw[2] - raw[0])
    base, contrib = shap_explain(m, Xrow, list(X.columns), topk=topk)
    up_txt, dn_txt = ('', '')
    shap_total = None
    if contrib is not None:
        up_txt, dn_txt = format_reasons(contrib, score, topk)
        shap_total = float(contrib['shap'].sum())
    return dict(date=str(idx[-1].date()), p_down=float(pc[0]), p_mid=float(pc[1]),
                p_up=float(pc[2]), score=score, margin=margin, base=base, shap_total=shap_total,
                up=up_txt, down=dn_txt,
                contrib=contrib.head(topk * 2).to_dict('records') if contrib is not None else [],
                n_feat=len(X.columns))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--topk', type=int, default=6)
    ap.add_argument('--only', default='', help='只跑某指数代码')
    ap.add_argument('--out', default=None, help='输出 json 路径（并行跑时必传，避免互相覆盖）')
    a = ap.parse_args()

    rows = []
    for code, h, depth, thr, cap, exp, note in CFG:
        if a.only and a.only not in code:
            continue
        name = H.IDX[code][0]
        print('>>> %s 训练中（T+%d d%d）...' % (name, h, depth), flush=True)
        try:
            r = predict_with_shap(code, h, depth, topk=a.topk)
        except Exception as e:
            print('   ERR %s' % str(e)[:100], flush=True)
            continue
        if not r:
            print('   数据不足', flush=True); continue
        if r['score'] >= thr:
            act, pos = '看多', cap
        elif r['score'] <= -thr:
            act, pos = '看空', 0.0
        else:
            act, pos = '观望', None
        r.update(name=name, code=code, tf='T+%d' % h, depth=depth, thr=thr,
                 act=act, pos=pos, cap=cap, expect=exp, note=note)
        rows.append(r)
        print('   %s %-4s score=%+.3f (阈%.2f) → %-3s 仓位%s'
              % (r['date'], r['tf'], r['score'], thr, act,
                 ('%.0f%%' % (pos * 100)) if pos is not None else '—'), flush=True)
        print('   ⬆ 推涨: %s' % r['up'], flush=True)
        print('   ⬇ 压跌: %s' % r['down'], flush=True)
        if r.get('shap_total') is not None:
            print('   SHAP 校验(margin空间): base %+.3f + Σshap %+.3f = %+.3f  vs margin %+.3f  (差 %.6f)'
                  % (r['base'], r['shap_total'], r['base'] + r['shap_total'],
                     r['margin'], abs(r['base'] + r['shap_total'] - r['margin'])), flush=True)
        print(flush=True)

    if not rows:
        print('无信号'); return

    longs = [x for x in rows if x['act'] == '看多']
    shorts = [x for x in rows if x['act'] == '看空']
    n = len(rows)
    if longs and not shorts:
        concl = ('全部 %d 个看多' % n) if len(longs) == n and n > 1 else \
                ('%d 个看多（%s）' % (len(longs), '、'.join(x['name'] for x in longs)))
    elif shorts and not longs:
        concl = ('全部 %d 个看空' % n) if len(shorts) == n else \
                ('%d 个看空（%s）' % (len(shorts), '、'.join(x['name'] for x in shorts)))
    elif longs and shorts:
        concl = '分歧：看多 %s；看空 %s' % ('、'.join(x['name'] for x in longs),
                                      '、'.join(x['name'] for x in shorts))
    else:
        concl = '全部观望'
    print('=' * 70)
    print('结论：' + concl)

    d = rows[0]['date']
    body = ['**%s · 五指数信号**' % d, '']
    for x in rows:
        pos_s = ('%.0f%%' % (x['pos'] * 100)) if x['pos'] is not None else '—'
        body.append('**%s**（%s）%s  score=%+.2f / 阈%.2f → **%s**，仓位 %s'
                    % (x['name'], x['tf'], x['date'], x['score'], x['thr'], x['act'], pos_s))
        body.append('  ⬆ 推涨：%s' % x['up'])
        body.append('  ⬇ 压跌：%s' % x['down'])
        body.append('')
    body += ['**结论**：' + concl, '',
             'SHAP 说明：数值是各因子对 margin(=logit涨−logit跌) 的净贡献，正=推涨、负=压跌；',
             'score=P涨−P跌 是 margin 经 softmax 后的非线性映射，所以两者不会直接相加相等。',
             '模型：LightGBM 三分类 + 多尺度日度 + ETF量能/份额，滚动1000日训练。']
    txt = '\n'.join(body)

    _SHAP = os.path.join(WS, '.cache', 'wide_ens')
    out_json = a.out or (os.path.join(_SHAP, 'signal_shap.json')
                         if not a.only else os.path.join(_SHAP, 'shap', '%s.json' % a.only))
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, 'w') as f:
        json.dump(dict(date=d, rows=rows, concl=concl), f, ensure_ascii=False, indent=1, default=str)
    print('已存 %s' % out_json)

    if a.push:
        import subprocess
        cmd = ['python3', os.path.join(WS, 'bin', 'feishu_push_card.py'),
               '--title', '📈 五指数信号 · %s' % d, '--body', txt]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            print('push:', (out.stdout or out.stderr)[:300])
        except Exception as e:
            print('push fail', e)


if __name__ == '__main__':
    main()
