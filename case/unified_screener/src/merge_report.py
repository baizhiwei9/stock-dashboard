#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四选股器合并 + 行业/概念加权统计（v4，纯程序零 Token）
=========================================================
S1 缠论主升浪（多头排列）   S2 成长股选股器
S3 A股强势股（多时段动量）  S4 MAD 双偏离度 × PEG 双口径

加权规则：
  每只股票权重 w = 它入选的选股器数量（1~4）
  某行业/概念的「加权次数」= 该维度下所有股票 w 之和
  排名按加权次数降序，同分按股票数降序

用法：
  python merge_report.py                # 默认今天
  python merge_report.py 2026-10-30     # 指定日期
  python merge_report.py 2026-10-30 --no-push   # 不推飞书
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()
import os
import re
import csv
import sys
import pickle
from datetime import datetime

import pandas as pd
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()


DATA_HUB_PATH = _HUB_ROOT
sys.path.insert(0, DATA_HUB_PATH)

BASE = _cp('/Coze/Drive/蓁宝/所有对话/主对话')
sys.path.insert(0, BASE)
OUTPUT_DIR = os.path.join(BASE, '长期计划/缠论主升浪/outputs/股票筛选')
PROJ_DIR = _cp('/Coze/Drive/选股/四策略综合选股_20261102')
CACHE_DIR = os.path.join(BASE, 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# 命令行
_args = [a for a in sys.argv[1:] if not a.startswith('--')]
FLAGS = set(a for a in sys.argv[1:] if a.startswith('--'))
TODAY = _args[0] if _args else datetime.now().strftime('%Y-%m-%d')
YMD = TODAY.replace('-', '')
NO_PUSH = '--no-push' in FLAGS

STRATEGY_META = [
    ('S1', '缠论主升浪', '多头排列（股价>MA20>MA40>MA80>MA160）+ MA80/160↑ + 回撤三档降级'),
    ('S2', '成长股', '营收↑>20% + 净利↑>15% + PE>0 + 年内涨幅前200 + 行业前50'),
    ('S3', 'A股强势股', '市值>50亿 + 多时段动量百分位四重筛选'),
    ('S4', 'MAD选股', 'MA20>MA200 且 MA20>MA40 + MA40斜率>0 + 周涨幅>0 + PEG双口径任一≤1.5'),
]
SRC_LABEL = {k: v for k, (k, v, _) in [(m[0], (m[0], m[1], m[2])) for m in STRATEGY_META]}


def log(m):
    print(m, flush=True)


def normalize_code(raw):
    raw = str(raw).strip().replace('\u200b', '').replace('\ufeff', '')
    m = re.search(r'\d{6}', raw)
    return m.group(0) if m else None


def to_ts(code6):
    c = str(code6).zfill(6)
    if c[0] == '6':
        return c + '.SH'
    if c[0] in ('0', '3'):
        return c + '.SZ'
    if c[0] in ('4', '8'):
        return c + '.BJ'
    return c + '.SZ'


# ---------------- 各选股器加载 ----------------
def load_s1():
    p = os.path.join(OUTPUT_DIR, '%s_多头排列Top20.md' % TODAY)
    if not os.path.exists(p):
        log('⚠️ S1 缺失: %s' % p)
        return {}
    d = {}
    with open(p, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('|'):
                continue
            cells = [c.strip() for c in line.split('|')]
            if len(cells) < 12:
                continue
            if '档位' in cells[1] or '---' in cells[1]:
                continue
            try:
                int(cells[1])
            except Exception:
                continue
            code = normalize_code(cells[2])
            if not code:
                continue
            try:
                price = float(cells[4])
            except Exception:
                price = None
            try:
                mtd = float(cells[11])
            except Exception:
                mtd = None
            d[code] = dict(name=cells[3], price=price, month_return=mtd,
                           tier=cells[1])
    return d


def load_s2():
    p = os.path.join(BASE, '成长股选股器/outputs/%s_成长股筛选结果.csv' % TODAY)
    if not os.path.exists(p):
        log('⚠️ S2 缺失: %s' % p)
        return {}
    d = {}
    with open(p, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            code = normalize_code(row.get('股票代码', ''))
            if not code:
                continue
            try:
                rev = float(row.get('营收增速%') or 0)
                prof = float(row.get('净利润增速%') or 0)
            except Exception:
                continue
            if rev <= 0 and prof <= 0:
                continue
            d[code] = dict(name=row.get('股票名称', ''), industry=row.get('行业', ''),
                           rev_growth=rev, prof_growth=prof,
                           pe=float(row.get('PE') or 0),
                           year_return=float(row.get('年内涨幅%') or 0))
    return d


def load_s3():
    p = os.path.join(BASE, '长期计划/A股选股器/outputs/选股器/%s_筛选结果.csv' % TODAY)
    if not os.path.exists(p):
        log('⚠️ S3 缺失: %s' % p)
        return {}
    d = {}
    with open(p, 'r', encoding='utf-8-sig') as f:
        rows = [ln for ln in f if ln.strip() and not ln.lstrip().startswith('#')]
    for row in csv.DictReader(rows):
        code = normalize_code(row.get('股票代码', ''))
        if not code:
            continue
        try:
            d[code] = dict(name=row.get('名称', ''),
                           price=float(row.get('最新价') or 0),
                           year_return=float(row.get('近1年涨幅_%') or 0),
                           month_return=float(row.get('近1月涨幅_%') or 0))
        except Exception:
            continue
    return d


def load_s4():
    cands = [
        os.path.join(BASE, 'MAD选股器/outputs/%s_MAD选股结果.csv' % TODAY),
        _cp('/Coze/Drive/选股/MAD选股_20261102/最终选股_PEG过滤_%s.csv') % YMD,
    ]
    p = next((x for x in cands if os.path.exists(x)), None)
    if p is None:
        log('⚠️ S4 缺失，尝试过: %s' % cands)
        return {}
    d = {}
    with open(p, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            raw = row.get('symbol') or row.get('股票代码') or row.get('ts_code') or ''
            code = normalize_code(raw)
            if not code:
                continue
            d[code] = dict(name=row.get('name') or row.get('股票名称', ''),
                           industry=row.get('industry') or row.get('行业', ''),
                           price=float(row.get('close_y') or 0),
                           pe_ttm=float(row.get('pe_ttm') or 0),
                           peg=float(row.get('peg') or 0),
                           week_ret=float(row.get('week_ret') or 0))
    log('S4 来源: %s' % p)
    return d


# ---------------- 行业 / 概念 ----------------
def get_industry_map():
    fp = os.path.join(CACHE_DIR, 'stock_basic.pkl')
    df = None
    if os.path.exists(fp):
        try:
            df = pd.read_pickle(fp)
        except Exception:
            df = None
    if df is None:
        from data_hub.tushare_config import get_tushare_pro
        df = get_tushare_pro().stock_basic(
            exchange='', list_status='L', fields='ts_code,symbol,name,industry,market')
        df.to_pickle(fp)
    return dict(zip(df['symbol'], df['industry'].fillna('未分类')))


# ---------------- 主流程 ----------------
# ===================== 加权统计（可被外部 import） =====================
def build_industry_concept_stats(merged, cmap, dim):
    """按行业/概念做加权聚合。

    merged: [{'code','name','industry','n','month_return',...}, ...]
    cmap:   {code6: [(concept_name, count), ...]}
    dim:    'industry' | 'concept'

    返回 [(名称, 加权次数, 股票数, [股票...]), ...]
    加权次数 = Σ(该维度下每只股票的入选选股器数)
    """
    bucket = {}
    for m in merged:
        w = m.get('n') if m.get('n') is not None else m.get('n_strategies', 1)
        if dim == 'industry':
            keys = [m.get('industry') or '未分类']
        else:
            keys = [c[0] for c in cmap.get(m['code'], [])]
            if not keys:
                keys = ['（无匹配概念）']
        for k in keys:
            b = bucket.setdefault(k, dict(w=0, stocks=[]))
            b['w'] += w
            b['stocks'].append(m)
    rows = []
    for k, b in bucket.items():
        st = sorted(b['stocks'], key=lambda x: (-(x.get('n') or x.get('n_strategies', 1)),
                                               -(x['month_return'] or -999)))
        rows.append((k, b['w'], len(st), st))
    rows.sort(key=lambda x: (-x[1], -x[2], x[0]))
    return rows


def main():
    log('=' * 76)
    log('四选股器合并 | 基准日 %s' % TODAY)
    log('=' * 76)

    s1, s2, s3, s4 = load_s1(), load_s2(), load_s3(), load_s4()
    log('S1 缠论主升浪: %d 只' % len(s1))
    log('S2 成长股    : %d 只' % len(s2))
    log('S3 A股强势股 : %d 只' % len(s3))
    log('S4 MAD选股   : %d 只' % len(s4))

    srcs = {'S1': s1, 'S2': s2, 'S3': s3, 'S4': s4}
    all_codes = set()
    for d in srcs.values():
        all_codes |= set(d)

    ind_map = get_industry_map()

    merged = []
    for code in all_codes:
        hit = [k for k in ('S1', 'S2', 'S3', 'S4') if code in srcs[k]]
        info = dict(code=code, name='', industry=ind_map.get(code, '未分类'),
                    price=None, month_return=None, year_return=None,
                    n=len(hit), hit=hit,
                    hit_label='、'.join(SRC_LABEL[k] for k in hit))
        for k in ('S1', 'S2', 'S3', 'S4'):
            if code not in srcs[k]:
                continue
            d = srcs[k][code]
            if not info['name']:
                info['name'] = d.get('name', '')
            if not info['industry'] or info['industry'] == '未分类':
                info['industry'] = d.get('industry') or info['industry']
            for f in ('price', 'month_return', 'year_return'):
                if info[f] is None and d.get(f) is not None:
                    info[f] = d[f]
        merged.append(info)

    merged.sort(key=lambda x: (-x['n'], -(x['month_return'] or -999)))

    # ---- 概念映射 ----
    import concept_map
    cmap = concept_map.get_stock_concepts([m['code'] for m in merged])

    # ---- 加权统计 ----
    ind_rows = build_industry_concept_stats(merged, cmap, 'industry')
    con_rows = build_industry_concept_stats(merged, cmap, 'concept')

    # ---- 输出 ----
    def stock_cell(st, with_ind=False):
        out = []
        for s in st:
            star = '★' * s['n']
            out.append('%s%s%s' % (s['name'], star, '（%s）' % s['industry'] if with_ind else ''))
        return '、'.join(out)

    L = []
    A = L.append
    A('# %s A股四选股器综合选股报告\n' % TODAY)
    A('**基准日**：%s  ' % TODAY)
    A('**生成方式**：纯 Python 脚本合并（`merge_report.py`），零 LLM 参与  ')
    A('**数据源**：TuShare / data_hub 四级降级体系\n')
    A('## 一、四选股器说明\n')
    A('| 编号 | 选股器 | 核心逻辑 | 入选数 |')
    A('|------|--------|----------|--------|')
    for k, nm, desc in STRATEGY_META:
        A('| %s | %s | %s | %d |' % (k, nm, desc, len(srcs[k])))
    A('| — | **去重合计** | — | **%d** |\n' % len(merged))

    cnt = {i: sum(1 for m in merged if m['n'] == i) for i in range(1, 5)}
    A('## 二、共振分布\n')
    A('| 评级 | 含义 | 只数 |')
    A('|------|------|------|')
    A('| ★★★★ | 四选股器全部命中 | **%d** |' % cnt[4])
    A('| ★★★ | 三选股器命中 | **%d** |' % cnt[3])
    A('| ★★ | 双选股器命中 | **%d** |' % cnt[2])
    A('| ★ | 单选股器命中 | **%d** |' % cnt[1])
    A('| — | **总计** | **%d** |\n' % len(merged))

    A('## 三、交叉共振明细\n')
    A('| 评级 | 代码 | 名称 | 行业 | 现价 | 月涨% | 命中选股器 |')
    A('|------|------|------|------|------|-------|-----------|')
    for m in merged:
        star = '★' * m['n']
        price = '%.2f' % m['price'] if m['price'] else '-'
        mtd = '%+.1f' % m['month_return'] if m['month_return'] is not None else '-'
        A('| %s | %s | %s | %s | %s | %s | %s |'
          % (star, m['code'], m['name'], m['industry'], price, mtd, m['hit_label']))
    A('')

    A('## 四、行业加权分布（按加权次数排名）\n')
    A('> **加权规则**：股票权重 = 它入选的选股器数量。例如一只股票同时被 S1 和 S4 选中，'
      '权重为 2。行业「加权次数」= 该行业全部股票权重之和，体现该行业在多策略下的集中度。\n')
    A('| 排名 | 行业 | 加权次数 | 股票数 | 选中股票（★=入选选股器数） |')
    A('|------|------|----------|--------|---------------------------|')
    for i, (k, w, ns, st) in enumerate(ind_rows, 1):
        A('| %d | **%s** | **%d** | %d | %s |' % (i, k, w, ns, stock_cell(st)))
    A('')

    A('## 五、概念加权分布（按加权次数排名）\n')
    A('> 概念来自同花顺概念指数，已剔除宽泛指数（成分股 >300 只）与指数成分股类概念。'
      '一只股票可同时属于多个概念，故本节加权总数会大于行业节。\n')
    A('| 排名 | 概念 | 加权次数 | 股票数 | 选中股票 |')
    A('|------|------|----------|--------|----------|')
    for i, (k, w, ns, st) in enumerate(con_rows[:40], 1):
        A('| %d | **%s** | **%d** | %d | %s |' % (i, k, w, ns, stock_cell(st, with_ind=True)))
    A('')
    if len(con_rows) > 40:
        A('> 概念共 %d 个，此处展示前 40 个，完整明细见同名 CSV。\n' % len(con_rows))

    A('---')
    A('*纯程序生成，零 Token 消耗 ｜ 案例分析学习材料，不构成投资建议*')

    report = '\n'.join(L)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(PROJ_DIR, exist_ok=True)
    for d in (OUTPUT_DIR, PROJ_DIR):
        fp = os.path.join(d, '%s_四策略综合选股报告.md' % TODAY)
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(report)
        log('📁 %s' % fp)

    # CSV
    for d in (OUTPUT_DIR, PROJ_DIR):
        with open(os.path.join(d, '%s_四策略明细.csv' % TODAY), 'w',
                  encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['评级', '代码', '名称', '行业', '现价', '月涨%', '年涨%',
                        '入选数', '命中选股器', '概念'])
            for m in merged:
                w.writerow(['★' * m['n'], m['code'], m['name'], m['industry'],
                            m['price'], m['month_return'], m['year_return'],
                            m['n'], m['hit_label'],
                            '、'.join(c[0] for c in cmap.get(m['code'], []))])
        with open(os.path.join(d, '%s_行业加权统计.csv' % TODAY), 'w',
                  encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['排名', '行业', '加权次数', '股票数', '选中股票'])
            for i, (k, wt, ns, st) in enumerate(ind_rows, 1):
                w.writerow([i, k, wt, ns, '、'.join('%s%s' % (s['name'], '★' * s['n']) for s in st)])
        with open(os.path.join(d, '%s_概念加权统计.csv' % TODAY), 'w',
                  encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['排名', '概念', '加权次数', '股票数', '选中股票'])
            for i, (k, wt, ns, st) in enumerate(con_rows, 1):
                w.writerow([i, k, wt, ns, '、'.join('%s%s' % (s['name'], '★' * s['n']) for s in st)])

    with open(os.path.join(PROJ_DIR, 'cache_%s.pkl' % YMD), 'wb') as f:
        pickle.dump(dict(merged=merged, ind_rows=ind_rows, con_rows=con_rows,
                         cnt=cnt, src_counts={k: len(v) for k, v in srcs.items()}), f)

    # 控制台摘要
    log('\n共振分布：★★★★ %d ｜ ★★★ %d ｜ ★★ %d ｜ ★ %d ｜ 合计 %d'
        % (cnt[4], cnt[3], cnt[2], cnt[1], len(merged)))
    log('\n行业加权 TOP10：')
    for i, (k, w, ns, st) in enumerate(ind_rows[:10], 1):
        log('  %2d. %-10s 加权%2d  股票%d只  %s' % (
            i, k, w, ns, '、'.join(s['name'] for s in st[:6]) + ('…' if ns > 6 else '')))
    log('\n概念加权 TOP10：')
    for i, (k, w, ns, st) in enumerate(con_rows[:10], 1):
        log('  %2d. %-14s 加权%2d  股票%d只  %s' % (
            i, k, w, ns, '、'.join(s['name'] for s in st[:6]) + ('…' if ns > 6 else '')))

    # 飞书推送
    if not NO_PUSH:
        try:
            sys.path.insert(0, BASE)
            from feishu_push import push_to_feishu
            txt = '📊 四选股器综合选股 | %s\n\n' % TODAY
            txt += '★★★★ %d只 ｜ ★★★ %d只 ｜ ★★ %d只 ｜ ★ %d只 ｜ 合计 %d只\n\n' % (
                cnt[4], cnt[3], cnt[2], cnt[1], len(merged))
            if cnt[4] or cnt[3]:
                txt += '🔥 三共振以上：\n'
                for m in merged:
                    if m['n'] >= 3:
                        txt += '  %s %s（%s）%s\n' % ('★' * m['n'], m['name'], m['industry'], m['hit_label'])
                txt += '\n'
            txt += '行业加权 TOP5：\n'
            for k, w, ns, st in ind_rows[:5]:
                txt += '  %s 加权%d ｜ %s\n' % (k, w, '、'.join(s['name'] for s in st[:4]))
            txt += '\n---\n⚠️ 案例分析学习材料，不构成投资建议'
            push_to_feishu(text=txt)
            log('\n✅ 飞书已推送')
        except Exception as e:
            log('\n⚠️ 飞书推送失败: %s' % e)


if __name__ == '__main__':
    main()
