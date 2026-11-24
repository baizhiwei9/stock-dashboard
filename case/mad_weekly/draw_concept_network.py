# -*- coding: utf-8 -*-
"""概念-个股关系网络图：前20概念 × Top50样本股，归属连线 + 概念间重合弧线"""
import json, os, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import PathPatch
from matplotlib.lines import Line2D
from matplotlib.font_manager import FontProperties

D = json.load(open('concept_relation_data.json', encoding='utf-8'))
FP = None
for cand in ('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf',
             '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'):
    if os.path.exists(cand):
        FP = cand; break
print('font:', FP)
F  = lambda sz: FontProperties(fname=FP, size=sz)

RED, GREEN, ORANGE, GRAY = '#E14B3B', '#3E9B62', '#E8842C', '#9AA0A6'

def cluster(cc, concepts, thr=0.20):
    names=[c['标签'] for c in concepts]; parent={n:n for n in names}
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
        return x
    for r in cc:
        if r['Jaccard']>=thr:
            ra,rb=find(r['概念A']),find(r['概念B'])
            if ra!=rb: parent[rb]=ra
    import collections
    g=collections.defaultdict(list)
    for i,c in enumerate(concepts): g[find(c['标签'])].append(c)
    return sorted(g.values(), key=lambda grp: min(int(c['rank']) for c in grp))

def build(cfg):
    metric   = cfg['metric']
    concepts = sorted(D[cfg['concepts']], key=lambda c: -float(c[cfg['metric']]))
    for i, c in enumerate(concepts, 1): c['rank'] = i
    cc_all   = D[cfg['cc']]
    groups   = cluster(cc_all, concepts)
    ordered  = [c for grp in groups for c in grp]              # 按簇排布，簇内按排名
    for c in ordered: c['v'] = float(c[metric])

    e_all   = D[cfg['edges']]
    n2rank  = {c['标签']: i for i, c in enumerate(ordered)}
    stocks  = {}
    for e in e_all:
        s = {x['ts_code']: x for x in D['top50']}[e['股票代码']]
        stocks.setdefault(e['股票代码'], {**s, 'cs': []})['cs'].append(e['概念'])
    # 个股按所连概念的重心排序（减少交叉）
    for k, s in stocks.items():
        s['bc'] = float(np.mean([n2rank[c] for c in s['cs']]))
    stock_list = sorted(stocks.values(), key=lambda s: (s['bc'], int(s['rank'])))

    fig = plt.figure(figsize=(32, 20), dpi=100)
    ax  = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
    fig.patch.set_facecolor('white')

    # ---- 标题区 ----
    ax.text(0.5, 0.968, cfg['title'], ha='center', va='center', fontproperties=F(27), color='#1A1A1A')
    ax.text(0.5, 0.939, cfg['sub'], ha='center', va='center', fontproperties=F(13.5), color='#444444')
    ax.text(0.5, 0.917, cfg['note'], ha='center', va='center', fontproperties=F(11.5), color='#6B6B6B')

    # ---- 布局 ----
    YT, YB = 0.878, 0.145
    NC, NS = len(ordered), len(stock_list)
    csp, ssp = (YT-YB)/NC, (YT-YB)/NS
    cy = {c['标签']: YT-(i+0.5)*csp for i, c in enumerate(ordered)}
    sy = {s['ts_code']: YT-(i+0.5)*ssp for i, s in enumerate(stock_list)}
    XC, XS = 0.300, 0.720

    # ---- 簇分隔线 ----
    k = 0
    for grp in groups[:-1]:
        k += len(grp); yb = YT - k*csp
        ax.plot([0.015, 0.288], [yb, yb], color='#D8D8D8', lw=0.9, ls=(0, (3, 3)), zorder=1)

    # ---- 归属连线（谁属于谁）----
    for e in e_all:
        ax.plot([XC, XS], [cy[e['概念']], sy[e['股票代码']]], color=GRAY, lw=0.85, alpha=0.30, zorder=2)

    # ---- 概念间重合弧线（谁关心谁）----
    cc_top = sorted(cc_all, key=lambda r: -r['Jaccard'])[:12]
    jmax = cc_top[0]['Jaccard']
    for r in cc_top:
        y1, y2 = cy[r['概念A']], cy[r['概念B']]
        bulge = min(0.115, 0.030 + 0.45*abs(y1-y2))
        verts = [(XC, y1), (XC+bulge, (y1+y2)/2), (XC, y2)]
        ax.add_patch(PathPatch(Path(verts, [Path.MOVETO, Path.CURVE3, Path.CURVE3]), fill=False,
                               edgecolor=ORANGE, lw=1.6+4.6*(r['Jaccard']/jmax), alpha=0.78, zorder=4))

    # ---- 节点 ----
    cv = [abs(c['v']) for c in ordered]
    for c in ordered:
        pos = (abs(c['v'])-min(cv))/(max(cv)-min(cv)) if max(cv) > min(cv) else 1.0
        col = RED if c['v'] > 0 else GREEN
        ax.scatter([XC], [cy[c['标签']]], s=110+230*pos, color=col, zorder=6, edgecolor='white', lw=1.0)
        ax.text(0.288, cy[c['标签']], cfg['clabel'](c), ha='right', va='center', fontproperties=F(11), color='#222222')
    sv = [abs(s['ytd']) for s in stock_list]
    for s in stock_list:
        pos = (abs(s['ytd'])/max(sv))**0.6
        col = RED if s['ytd'] > 0 else GREEN
        ax.scatter([XS], [sy[s['ts_code']]], s=70+240*pos, color=col, zorder=6, edgecolor='white', lw=1.0)
        ax.text(0.734, sy[s['ts_code']], f"#{s['rank']} {s['name']} {s['ytd']*100:+.1f}%",
                ha='left', va='center', fontproperties=F(10), color='#222222')

    # ---- 列标题 ----
    ax.text(XC, 0.893, f"概念（{NC} 个）", ha='center', va='center', fontproperties=F(12.5), color='#111111')
    ax.text(XS, 0.893, f"mad20 前50 样本股（有归属的 {NS} 只）", ha='center', va='center', fontproperties=F(12.5), color='#111111')

    # ---- 图例与脚注 ----
    ax.text(0.015, 0.093, cfg['foot1'], ha='left', va='center', fontproperties=F(11), color='#555555')
    ax.text(0.015, 0.069, cfg['foot2'], ha='left', va='center', fontproperties=F(11), color='#555555')
    ax.text(0.015, 0.045, cfg['foot3'], ha='left', va='center', fontproperties=F(11), color='#555555')
    lg = [Line2D([], [], color=GRAY, lw=1.6, alpha=0.6, marker='o', ms=9, mfc=RED, mec='white', ls='-'),
          Line2D([], [], color=GRAY, lw=0.9, ls='-'),
          Line2D([], [], color=ORANGE, lw=3.2),
          Line2D([], [], marker='o', ms=13, color='w', mfc=RED, mec='white', ls='none'),
          Line2D([], [], marker='o', ms=6, color='w', mfc=GREEN, mec='white', ls='none')]
    ax.legend(lg, ['概念节点（橙心=该口径为正的红点；大小∝|概念指数值|）',
                   '灰细线 = 概念—个股归属（谁属于谁）',
                   '橙弧 = 概念间成分重合（谁关心谁，越粗重合度越高，仅画最高的 12 对）',
                   '红 = 该股今年上涨', '绿 = 该股今年下跌'],
              loc='lower right', bbox_to_anchor=(0.995, 0.012), frameon=True, framealpha=0.95,
              edgecolor='#E0E0E0', prop=F(11.5))

    out = cfg['out']; fig.savefig(out, dpi=100, facecolor='white')
    fig.canvas.draw(); rend = fig.canvas.get_renderer()
    W, H = fig.canvas.get_width_height()
    tbs = [(t.get_text()[:14], t.get_window_extent(rend)) for t in ax.texts if t.get_text().strip()]
    ov = []
    for i in range(len(tbs)):
        for j in range(i+1, len(tbs)):
            if tbs[i][1].overlaps(tbs[j][1]): ov.append((tbs[i][0], tbs[j][0]))
    outside = [t[0] for t in tbs if t[1].x0 < 0 or t[1].y0 < 0 or t[1].x1 > W or t[1].y1 > H]
    print(out, os.path.getsize(out)//1024, 'KB', '| 概念', NC, '| 个股', NS, '| 归属边', len(e_all),
          '| 弧线', len(cc_top), '| 簇', len(groups), '| 文字块', len(tbs), '| 重叠对', len(ov), '| 出界', len(outside))
    if ov: print('   重叠明细:', ov[:8])
    if outside: print('   出界明细:', outside)
    plt.close(fig)
    return fig, ax, ordered, stock_list

# 复核数值（供 print 校验）
yts = {s['ts_code']: s['ytd'] for s in D['top50']}
print('样本股今年涨幅区间: %.2f%% ~ %.2f%%  正收益 %d/%d' % (
    min(yts.values())*100, max(yts.values())*100, sum(1 for v in yts.values() if v > 0), len(yts)))

cfgA = dict(concepts='top20_mad', cc='cc_mad', edges='e_mad', metric='m20',
            out='概念个股关系网络_MAD20前20概念_截至20261030.png',
            title='概念 ⇄ 个股关系网络 · MAD20 前 20 概念',
            sub='左列：按概念指数 MA20/MA40−1 降序取前 20 个细分题材（簇内按排名）；右列：样本股按所连概念重心排列',
            note='连线口径：灰线=个股属于哪些概念；橙弧=概念之间成分股重合度。数据日期 2026-10-30',
            clabel=lambda c: f"#{c['rank']} {c['标签']}  {c['v']*100:+.2f}%  ({c['Top50成员数']}只)",
            foot1='个股标注 = #mad20排名 名称 个股今年以来涨幅（2026-10-30 ÷ 2025-12-31）；圆大小 ∝ |今年涨幅|，红=正/绿=负。',
            foot2='概念值 = 该概念同花顺概念指数的 MA20/MA40−1；括号内为概念在 mad20 前 50 样本股中的成分股数。左列按成分股重合度分簇（簇间灰色虚线分隔）。',
            foot3='重合度最高 5 对：数据安全×网络安全 43.5%(共享114只)｜AI应用×DeepSeek概念 36.6%(355)｜数据安全×华为鲲鹏 34.3%(91)｜华为鲲鹏×智慧政务 33.7%(101)｜AI应用×AIGC概念 33.6%(214)。')

cfgB = dict(concepts='top20_ytd', cc='cc_ytd', edges='e_ytd', metric='ytd',
            out='概念个股关系网络_今年涨幅前20概念_截至20261030.png',
            title='概念 ⇄ 个股关系网络 · 今年以来涨幅前 20 概念',
            sub='左列：按概念指数今年以来涨幅降序取前 20 个细分题材（簇内按排名）；右列：样本股按所连概念重心排列',
            note='连线口径：灰线=个股属于哪些概念；橙弧=概念之间成分股重合度。数据日期 2026-10-30（年初基准 2025-12-31）',
            clabel=lambda c: f"#{c['rank']} {c['标签']}  {c['v']*100:+.2f}%  ({c['Top50成员数']}只)",
            foot1='个股标注 = #mad20排名 名称 个股今年以来涨幅（2026-10-30 ÷ 2025-12-31）；圆大小 ∝ |今年涨幅|，红=正/绿=负。',
            foot2='概念值 = 该概念同花顺概念指数今年以来涨幅；括号内为概念在 mad20 前 50 样本股中的成分股数。左列按成分股重合度分簇（簇间灰色虚线分隔）。',
            foot3='重合度最高 5 对：见随图交付的两张关联边明细表（概念-概念 Jaccard 与共享股数）。')

for cfg in (cfgA, cfgB):
    build(cfg)
