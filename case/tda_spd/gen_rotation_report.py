#!/usr/bin/env python3
"""生成 TDA-SPD 行业旋转日报 HTML（内嵌 ECharts 单文件）
含: 近5日持仓总览 / SPD行业流转桑基图 / 行业旋转轨迹图 / 流转明细表
数据源: state/spd_rotation_data.json (由 push_spd_rotation_daily.py / spd_rotation_extract.py 生成)
用法:
  python gen_rotation_report.py [--out 文件名.html]   # 直接生成
  from gen_rotation_report import generate           # 供每日推送脚本调用
"""
import json
import os
import argparse


# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''

BASE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0'
ECHARTS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/open-source-industry-rotation/assets/echarts.min.js'

# 因子配色（同一因子全图一致）
COLORS = {
    '金融地产': '#1E6FB8',
    '流动性因子': '#17A2A2',
    '红利高息': '#D69E2E',
    '医药健康': '#2F9E6E',
    '新能源': '#58A55C',
    '小盘成长': '#E07B39',
    '科技成长': '#7A5CD6',
    '高端制造': '#C24E4E',
}
POOL_COLOR = '#9AA0A6'
KEEP_COLOR_L = '#B7CCE3'   # 维持流边界色(灰蓝)
IN_COLOR = '#D9534F'       # 轮入/加仓(红)
OUT_COLOR = '#B0B2B8'      # 轮出/减仓(灰)


def factor_color(name):
    return COLORS.get(name, '#5B6770')


def generate(base_dir=None, out_name=None):
    """读取 state/spd_rotation_data.json 生成旋转日报 HTML, 返回输出文件绝对路径"""
    if base_dir is None:
        base_dir = BASE
    data_path = os.path.join(base_dir, 'state', 'spd_rotation_data.json')
    with open(data_path, encoding='utf-8') as f:
        rot = json.load(f)
    days = rot['days']
    transitions = rot['transitions']

    # 输出文件按最新交易日动态命名
    trade_date = days[-1]['trade_date']
    if out_name is None:
        out_name = f'SPD行业旋转_截至{trade_date}.html'
    OUT = os.path.join(base_dir, out_name)

    # ---------- 构建桑基图 nodes/links (守恒模型) ----------
    nodes = []      # {name, itemStyle, label}
    links = []      # {source, target, value, itemStyle}
    node_names = set()
    pool_names = []
    date_label = lambda d: d[5:].replace('-', '/')  # 09/28

    def add_node(name, display, color, extra=None):
        if name in node_names:
            return
        node_names.add(name)
        d = {'name': name, 'itemStyle': {'color': color}, 'label': {'show': True, 'formatter': display}}
        if extra:
            d.update(extra)
        nodes.append(d)

    # 每层因子节点
    for i, day in enumerate(days):
        for fac, w in day['weights'].items():
            add_node(f"D{i}|{fac}", f"{date_label(day['date'])}\n{fac}", factor_color(fac))

    def w_at(i, fac):
        return days[i]['weights'].get(fac, 0.0)

    pool_used = set()
    for i in range(len(days) - 1):
        w0, w1 = days[i]['weights'], days[i + 1]['weights']
        common = set(w0) & set(w1)
        p = f"P{i}"
        for fac in sorted(common, key=lambda x: -w1[x]):
            m = min(w0[fac], w1[fac])
            if m > 1e-6:
                links.append({'source': f"D{i}|{fac}", 'target': f"D{i+1}|{fac}", 'value': round(m * 100, 2),
                              'itemStyle': {'color': factor_color(fac), 'opacity': 0.55}})
            if w0[fac] > w1[fac] + 1e-6:  # 减仓差额 -> 池
                pool_used.add(p)
                links.append({'source': f"D{i}|{fac}", 'target': p, 'value': round((w0[fac] - w1[fac]) * 100, 2),
                              'itemStyle': {'color': OUT_COLOR, 'opacity': 0.45}})
            elif w1[fac] > w0[fac] + 1e-6:  # 加仓差额 <- 池
                pool_used.add(p)
                links.append({'source': p, 'target': f"D{i+1}|{fac}", 'value': round((w1[fac] - w0[fac]) * 100, 2),
                              'itemStyle': {'color': IN_COLOR, 'opacity': 0.55}})
        for fac in sorted(set(w0) - set(w1)):  # 轮出 -> 池
            pool_used.add(p)
            links.append({'source': f"D{i}|{fac}", 'target': p, 'value': round(w0[fac] * 100, 2),
                          'itemStyle': {'color': OUT_COLOR, 'opacity': 0.45}})
        for fac in sorted(set(w1) - set(w0)):  # 轮入 <- 池
            pool_used.add(p)
            links.append({'source': p, 'target': f"D{i+1}|{fac}", 'value': round(w1[fac] * 100, 2),
                          'itemStyle': {'color': IN_COLOR, 'opacity': 0.6}})

    # 仅存在实际调仓流时才创建换仓池节点
    for i in range(len(days) - 1):
        if f"P{i}" in pool_used:
            add_node(f"P{i}", f"换仓池\n{date_label(days[i]['date'])}→{date_label(days[i+1]['date'])}", POOL_COLOR)

    # ---------- 旋转轨迹图数据 ----------
    series = []
    all_factors = []
    for day in days:
        for fac in day['weights']:
            if fac not in all_factors:
                all_factors.append(fac)
    x_dates = [date_label(d['date']) for d in days]
    for fac in all_factors:
        values = []
        for day in days:
            w = day['weights'].get(fac)
            values.append(round(w * 100, 1) if w else None)
        series.append({'name': fac, 'type': 'line', 'smooth': 0.3, 'connectNulls': False,
                       'symbolSize': 7, 'lineStyle': {'width': 3, 'color': factor_color(fac)},
                       'itemStyle': {'color': factor_color(fac)},
                       'emphasis': {'focus': 'series'},
                       'data': values})

    # ---------- 概览表 ----------
    overview_rows = []
    for day in days:
        tops = ' '.join(f"{k} {v*100:.1f}%" for k, v in day['weights'].items())
        overview_rows.append(
            f"<tr><td>{day['date']}</td><td>{day['state']}</td><td>{day['position_pct']:.0f}%</td><td class='l'>{tops}</td></tr>")

    # ---------- 流转明细表 ----------
    def trans_row(t):
        cells = []
        for kind, cls, mark in (('keep', 'c-keep', '维持'), ('in', 'c-in', '轮入'), ('out', 'c-out', '轮出')):
            items = t[kind]
            if not items:
                cells.append(f"<td class='c-mut {cls}'>{mark}: —</td>")
                continue
            if kind == 'keep':
                txt = '; '.join(f"{x['factor']} {x['from_w']*100:.1f}→{x['to_w']*100:.1f}%" for x in items)
            elif kind == 'in':
                txt = '; '.join(f"{x['factor']} +{x['w']*100:.1f}%" for x in items)
            else:
                txt = '; '.join(f"{x['factor']} -{x['w']*100:.1f}%" for x in items)
            cells.append(f"<td class='l {cls}'>{mark}: {txt}</td>")
        return (f"<tr><th class='pair'>{t['from']} → {t['to']}</th>" + ''.join(cells) + "</tr>")

    trans_rows = '\n'.join(trans_row(t) for t in transitions)

    sankey_data = json.dumps({'nodes': nodes, 'links': links}, ensure_ascii=False)
    traj_data = json.dumps({'x': x_dates, 'series': series}, ensure_ascii=False)
    overview_html = '\n'.join(overview_rows)
    meta_end = days[-1]['date']

    # 日期范围与最新信号说明(动态生成, 每日复用)
    range_str = f"近 {len(days)} 个交易日（{date_label(days[0]['date'])}–{date_label(days[-1]['date'])}）"
    last_display = meta_end
    top5_now = ' / '.join(days[-1]['weights'].keys()) or '—'
    note_html = (
        f"⚠️ <b>口径与滞后提示</b>：SPD 漂移基于 60 日参考窗口的测地距离变化，属于动量型滞后指标；"
        f"{range_str}仅含交易日序列，节假日休市期间无信号变化。<br>"
        f"最新信号（{last_display} 收盘）按 T+1 规则于下一交易日生效：持有 {top5_now}。"
    )

    TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDA-SPD 行业旋转日报 · 截至 {meta_end}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #F5F7FA; color: #222; padding: 20px 16px 40px; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; color: #123A66; }}
  .meta {{ color: #6B7280; font-size: 13px; margin-bottom: 18px; line-height: 1.6; }}
  .card {{ background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(16,42,67,.08);
           padding: 18px 20px; margin-bottom: 18px; }}
  .card h2 {{ font-size: 16px; color: #123A66; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ border: 1px solid #E3E8EF; padding: 7px 9px; text-align: center; }}
  th {{ background: #EEF3F9; color: #33475B; }}
  td.l {{ text-align: left; }}
  .c-keep {{ color: #1E6FB8; }}
  .c-in {{ color: #C0392B; }}
  .c-out {{ color: #7A828B; }}
  .c-mut {{ color: #6B7280; }}
  th.pair {{ width: 150px; background: #F7FAFD; }}
  .legend {{ font-size: 12px; color: #6B7280; margin-top: 8px; line-height: 1.8; }}
  .legend b {{ font-weight: 600; }}
  .tag {{ display: inline-block; border-radius: 4px; padding: 0 7px; margin-right: 6px; font-size: 12px; }}
  .tag.keep {{ background: #E8F1F9; color: #1E6FB8; }}
  .tag.in {{ background: #FDECEB; color: #C0392B; }}
  .tag.out {{ background: #F1F2F4; color: #7A828B; }}
  .tag.pool {{ background: #F1F2F4; color: #4B5563; }}
  .note {{ font-size: 12px; color: #8A6D3B; background: #FDF6E7; border: 1px solid #F0E3C0;
          border-radius: 8px; padding: 10px 14px; margin-top: 14px; line-height: 1.7; }}
  .sankey-wrap {{ position: relative; }}
  .chart {{ width: 100%; }}
  #sankey {{ height: 470px; }}
  #trajectory {{ height: 420px; }}
  footer {{ text-align: center; color: #9AA3AD; font-size: 12px; margin-top: 16px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>TDA-SPD 黎曼流形策略 · 行业旋转日报（桑基图 + 旋转轨迹）</h1>
  <div class="meta">数据截至 <b>{meta_end} 收盘</b> ｜ SPD Fisher-z 测地漂移 × 动量折扣 ｜ 22 因子选 Top5 集中持仓 ｜ T 日信号、T+1 生效 ｜ {range_str}</div>

  <div class="card">
    <h2>一、近 {ndays} 个交易日持仓总览（SPD Top5 因子）</h2>
    <table>
      <tr><th>日期</th><th>TDA状态</th><th>建议仓位</th><th>Top5 因子权重</th></tr>
      {overview_html}
    </table>
  </div>

  <div class="card sankey-wrap">
    <h2>二、SPD 行业流转桑基图（从这个 → 到这个）</h2>
    <div id="sankey" class="chart"></div>
    <div class="legend">
      <span class="tag keep">实线=维持（因子延续，权重取两日较小值）</span>
      <span class="tag in">红色流入=轮入/加仓（新进入或增配）</span>
      <span class="tag out">灰色流出=轮出/减仓（退出或减配）</span>
      <span class="tag pool">灰色节点=换仓资金池（两日间再平衡中转）</span><br>
      每一列 = 一个交易日；矩形宽度 ∝ SPD 权重；列间资金守恒（轮出+减仓 = 轮入+加仓）。
    </div>
  </div>

  <div class="card">
    <h2>三、SPD 行业旋转轨迹图（权重随时间旋转变化）</h2>
    <div id="trajectory" class="chart"></div>
    <div class="legend">仅在入选 Top5 的日期有数据点；折线断开处表示该因子当日轮出组合。点击图例可单独查看。</div>
  </div>

  <div class="card">
    <h2>四、相邻交易日流转明细</h2>
    <table>
      <tr><th>区间</th><th>维持（前→后权重）</th><th>轮入（新增）</th><th>轮出（退出）</th></tr>
      {trans_rows}
    </table>
  </div>

  <div class="note">
    {note_html}
  </div>
  <footer>TDA-SPD 黎曼流形策略 v5.0（22因子）｜ TDA 拓扑状态控仓 + SPD Fisher-z 测地漂移选行业｜ 仅供参考，不构成投资建议</footer>
</div>

{echarts_script}
<script>
var SANKEY_DATA = {sankey_data};
var TRAJ_DATA = {traj_data};

var sankeyChart = echarts.init(document.getElementById('sankey'));
sankeyChart.setOption({{
  tooltip: {{ trigger: 'item', triggerOn: 'mousemove',
    formatter: function(p) {{
      if (p.dataType === 'edge') {{
        var v = p.data.value;
        return '<b>' + p.data.source + '</b> → <b>' + p.data.target + '</b><br/>权重/资金: ' + v.toFixed(1) + '%';
      }}
      return '<b>' + p.name + '</b>';
    }}
  }},
  series: [{{
    type: 'sankey',
    left: 30, right: 120, top: 20, bottom: 30,
    nodeWidth: 16, nodeGap: 16,
    nodeAlign: 'justify',
    draggable: true,
    lineStyle: {{ color: 'gradient', curveness: 0.5, opacity: 0.5 }},
    emphasis: {{ focus: 'adjacency' }},
    data: SANKEY_DATA.nodes,
    links: SANKEY_DATA.links
  }}]
}});
window.addEventListener('resize', function() {{ sankeyChart.resize(); }});

var trajChart = echarts.init(document.getElementById('trajectory'));
trajChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ top: 0, left: 'center', textStyle: {{ fontSize: 12 }} }},
  grid: {{ left: 55, right: 30, top: 40, bottom: 40 }},
  xAxis: {{ type: 'category', data: TRAJ_DATA.x, axisLine: {{ lineStyle: {{ color: '#9AA3AD' }} }} }},
  yAxis: {{ type: 'value', name: 'SPD权重 %', max: 70, min: 0,
           splitLine: {{ lineStyle: {{ type: 'dashed', color: '#E8EDF3' }} }} }},
  series: TRAJ_DATA.series
}});
window.addEventListener('resize', function() {{ trajChart.resize(); }});
</script>
</body>
</html>
"""

    with open(ECHARTS, encoding='utf-8') as f:
        echarts_js = f.read()

    # TEMPLATE 按 f-string 双写转义花括号: 用 format 渲染, 值内部的花括号不会被二次解析
    html = TEMPLATE.format(
        meta_end=meta_end,
        ndays=len(days),
        range_str=range_str,
        note_html=note_html,
        overview_html=overview_html,
        trans_rows=trans_rows,
        echarts_script=f'<script>{echarts_js}</script>',
        sankey_data=sankey_data,
        traj_data=traj_data,
    )
    html = html.replace('</body>', _bs('tda_spd', 'html') + '</body>', 1)

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)

    print('written:', OUT)
    print('size KB:', round(os.path.getsize(OUT) / 1024, 1))
    print('sankey nodes:', len(nodes), 'links:', len(links))
    print('traj series:', len(series))
    return OUT


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成 TDA-SPD 行业旋转日报 HTML')
    parser.add_argument('--out', type=str, default=None, help='输出文件名（默认 SPD行业旋转_截至{最新交易日}.html）')
    args = parser.parse_args()
    generate(out_name=args.out)