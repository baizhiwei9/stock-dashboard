# -*- coding: utf-8 -*-
"""宽基合并信号 · 每日报告（纯脚本 0 token）
用法：
  python3 bin/wide_ens/daily.py            # 打印
  python3 bin/wide_ens/daily.py --push     # 发飞书私聊
  python3 bin/wide_ens/daily.py --email    # 发邮件
"""
import os, sys, argparse, datetime, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from scipy.stats import binomtest
import core as C

IS = ('2019-01-01', '2024-06-30')
OOS = ('2024-07-01', '2026-12-31')

# 滚动选档实测（2022-2026，每年初只用历史挑方案+门槛）
ROLL = dict(n=52, hit=0.673, p=0.0088, net=0.67)


def star(hit, net, p, n):
    if n < 15: return '☆'
    if hit >= 0.62 and net > 0.40 and p < 0.10: return '★★★'
    if hit >= 0.58 and net > 0.20: return '★★'
    if hit >= 0.54 and net > 0: return '★'
    return '·'


def build():
    frames = C.load_all()
    L = []; A = L.append
    today = datetime.date.today().isoformat()
    sig_date = max(df.index[-1] for df in frames.values())

    A('# 📡 宽基合并信号 · %s' % today)
    A('')
    A('**源A 包内模型 ＋ 源B 我的46维｜两路 300日 z-score 等权合并｜T+4｜%s 收盘口径｜纯脚本 0 token**'
      % sig_date.date().isoformat())
    A('')
    A('---')
    A('')
    A('## 一、今日信号（预测未来 %d 个交易日）' % C.H)
    A('')
    A('| 指数 | 方向 | 合并强度 | 源A | 源B | 门槛 | 该档历史胜率 | 星级 |')
    A('|---|---|---|---|---|---|---|---|')
    rows = []
    for code in C.INDICES:
        if code not in frames: continue
        df = frames[code]; r = df.iloc[-1]
        cfg = C.calib(df)
        hit = cfg['hit'] if cfg else float('nan'); net = cfg['net'] if cfg else float('nan')
        pv = cfg['p'] if cfg else 1.0; nn = cfg['n'] if cfg else 0
        frozen = bool(r['frozen']) or not np.isfinite(r['pkg'])
        fired = bool(r['fired'])
        rows.append(dict(name=C.INDICES[code][0], code=code, dir='多 ▲' if r['ens'] > 0 else '空 ▼',
                         ens=r['ens'], fired=fired, hit=hit, net=net, p=pv, n=nn, frozen=frozen))
        A('| %s | %s | %+.2f | %s | %s | %s | %s | %s |' % (
            C.INDICES[code][0], '多 ▲' if r['ens'] > 0 else '空 ▼', r['ens'],
            '冻结' if frozen else '%+.2f' % C.zscore(df['pkg']).iloc[-1],
            '%+.2f' % C.zscore(df['mine']).iloc[-1],
            '✅ 达标' if fired else '⏸ 未达标',
            ('%.1f%% (n=%d)' % (hit * 100, nn)) if cfg else '样本不足',
            star(hit, net, pv, nn) if cfg else '☆'))
    A('')
    A('> 强度 = 各自 300 日 z-score 后等权。门槛 = 合并强度 ≥ 自身 300 日 %d%% 分位（shift1，无未来函数）。'
      % int(C.GATE_Q * 100))
    A('')

    fired = [r for r in rows if r['fired']]
    A('## 二、共振判定')
    A('')
    if len(fired) >= 2:
        dirs = set(r['dir'][0] for r in fired)
        A('**%d 个达标：%s**' % (len(fired), '、'.join('%s(%s)' % (r['name'], r['dir']) for r in fired)))
        A('')
        A('- 同向 → **成立，按 %d 个等权**' % len(fired) if len(dirs) == 1
          else '- ⚠️ 方向不一致 → 无信号，不做')
    else:
        A('今日 **%d** 个达标，未到 2 个共振门槛 → 观望' % len(fired))
    A('')

    A('## 三、这套方案的真实成绩')
    A('')
    A('| 区间 | 事件数 | 命中率 | p | 平均收益 | 净（扣%.1f%%） |' % C.COST)
    A('|---|---|---|---|---|---|')
    for lab, lo, hi in (('全期 2019-2026', '2019-01-01', '2026-12-31'),
                        ('IS 2019-01~2024-06', *IS),
                        ('OOS 2024-07~', *OOS)):
        s = C.pooled(frames, lo, hi)
        A('| %s | %d | **%.1f%%** | %.3f | %+.2f%% | %+.2f%% |' % (
            lab, s['n'], s['hit'] * 100, s['p'], s['ret'], s['net']) if s
          else '| %s | — | 样本不足 | — | — | — |' % lab)
    A('')
    A('**逐指数（全期，严格非重叠）**')
    A('')
    A('| 指数 | n | 命中 | p | 净 |')
    A('|---|---|---|---|---|')
    for code in C.INDICES:
        if code not in frames: continue
        cfg = C.calib(frames[code])
        if not cfg: continue
        A('| %s | %d | %.1f%% | %.3f | %+.2f%% |' % (
            C.INDICES[code][0], cfg['n'], cfg['hit'] * 100, cfg['p'], cfg['net']))
    A('')
    A('**滚动选档（真实可交易口径：每年初只用历史挑方案+门槛，再应用该年）**')
    A('')
    A('- %d~2026 合计 **n=%d / 命中 %.1f%% / p=%.4f / 平均净 +%.2f%%**' % (
        2022, ROLL['n'], ROLL['hit'] * 100, ROLL['p'], ROLL['net']))
    A('- 这是唯一没有「用未来数据挑参数」的检验，数字比全样本挑档低，但可信')
    A('')

    A('---')
    A('')
    A('## 四、口径与边界（重要）')
    A('')
    A('- **源A 的训练目标是次日**（`y_next_ret = close.shift(-1)/close-1`，已核验 corr=1.000）')
    A('- **源B 的训练目标是未来 4 日**（`close.shift(-4)`，已核验 corr=1.000）')
    A('- **包内"70%命中"不可用**：那是次日口径 + 全样本挑档；同一批预测换真 T+4 只剩 47~54%，扣成本为负')
    A('- **包内 alarm_freeze**：约 45% 交易日预测被强制清零（=当日不出信号），已剔除，不当作中性观点')
    A('- **合并后 T+4 优于单一源**：池化 OOS 合并 56.7%(p=0.050) vs 包内 54.4%(p=0.250) vs 我的 58.2%(p=0.016)')
    A('- **T+1 合并反而变差**（OOS 53.6% vs 我的单用 57.1%）→ 主用 T+4，不做次日')
    A('- ⚠️ **不给目标价**：幅度 IC 近零，只输出方向 + 历史胜率')
    A('')
    A('## 五、今年预测准确度（日频台账，滚动累计）')
    A('')
    try:
        import daily_ledger as DL
        s = DL.stats()
        if s['settled_n'] == 0:
            A('台账已建（%d 条待结算，起点 %s），结算后本节自动出现数字。'
              % (s['pending'], s['first_sig'] or '—'))
        else:
            A('| 周期 | 已结算 | 命中率 | 同期基准上涨率 | 方向收益 |')
            A('|---|---|---|---|---|')
            for k in [x for x in s['periods'] if x != '合计'] + ['合计']:
                a = s['periods'].get(k) or {}
                if not a.get('n'):
                    continue
                A('| %s | %d | **%.1f%%** | %.1f%% | %+.2f%% |' % (
                    k, a['n'], a['hit'] * 100, a['base'] * 100, a['dir_avg']))
            A('')
            A('台账 %s 起（历史按同一套门槛回补，无后视），实盘自 2026-10-16 起每交易日记录；%d 条待结算。'
              % (s['first_sig'] or '—', s['pending']))
    except Exception as e:
        A('台账读取失败：%s' % e)
    A('')
    A('*数据源 tushare 5 宽基日线 + 中美信用利差/QVIX · 合并系统 2026-10-18 建*')
    return '\n'.join(L), frames, rows


def push_feishu(md, title):
    p = os.path.join(C.WS, 'bin/feishu_push_card.py')
    if not os.path.exists(p):
        print('[push] 缺少 feishu_push_card.py'); return False
    tmp = '/tmp/wide_ens_report.md'
    open(tmp, 'w', encoding='utf-8').write(md)
    r = subprocess.run([sys.executable, p, '--title', title, '--body-file', tmp],
                       capture_output=True, text=True, timeout=180)
    print('[push-feishu]', (r.stdout or '').strip()[:200], (r.stderr or '').strip()[:200])
    return r.returncode == 0


def send_email(md, title):
    p = os.path.join(C.WS, 'scripts/send_email.py')
    if not os.path.exists(p):
        print('[email] 缺少 send_email.py'); return False
    r = subprocess.run([sys.executable, p, '--subject', title, '--body', md, '--html'],
                       capture_output=True, text=True, timeout=180)
    print('[email]', (r.stdout or '').strip()[:200], (r.stderr or '').strip()[:200])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--email', action='store_true')
    ap.add_argument('--out', default='')
    ap.add_argument('--no-ledger', action='store_true', help='跳过台账结算/入表（仅调试用）')
    a = ap.parse_args()

    # 日频台账：只做结算（用当日已更新的收盘价）。
    # ⚠️ 入表统一由 signal_card_daily.sh 负责 —— 那份卡片用的是 SHAP 信号，
    #    与本报告的「合并信号」不是同一套模型，不能混在一条台账里记。
    if not a.no_ledger:
        try:
            import daily_ledger as DL
            DL.cmd_settle(verbose=False)
            print('[ledger] 日频台账已结算到期预测')
        except Exception as e:
            print('[ledger] 台账异常（不影响报告）：%s' % e)

    md, frames, rows = build()
    if a.out: open(a.out, 'w', encoding='utf-8').write(md)
    else: print(md)
    title = '📡 宽基合并信号 · %s · 来自 OpenClaw.Deepseek' % datetime.date.today().isoformat()
    if a.push: push_feishu(md, title)
    if a.email: send_email(md, title)


if __name__ == '__main__':
    main()
