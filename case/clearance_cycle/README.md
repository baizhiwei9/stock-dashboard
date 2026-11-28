# clearance_cycle —— A股出清周期监控

归入 stock-dashboard 任务体系，按 `case/CONTRACT.md` v1 实现五阶段。

## 周期边界（用户设定）

| 事件 | 判定 | 动作 |
| --- | --- | --- |
| 出清开始 | 沪深300 MA40 **下穿** MA80 | 开始发送报告 |
| 出清完成 | MA40 **上穿** MA80 | 停止发送（dispatch 自动静默） |

被动化率与年化基差是出清期间的**进度条**，不是完成判定。情绪温度是独立模块，也不参与完成判定。

## 进度指标

- **被动化率** = 宽基ETF总规模 ÷ 沪深300自由流通市值；4.9% 起计时、7.0% 提醒、8.5% 上沿
- **年化基差率** =（现货−期货）/现货 × 365/剩余天数；取剩余 60–150 天远季合约；正常 4%–5%；距 5 日高回落 ≥5pp = 修复到位

## 情绪温度

五维合成 0–100：宽度（涨停/跌停）、涨幅（中位数）、量能（成交额250日分位）、杠杆（融资20日变化）、波动（QVIX分位反向）。
阶段：冰点<25 → 修复初期25–40 → 中性偏暖40–55 → 亢奋55–70 → 过热≥70。

## 目录

```
case/clearance_cycle/
  task.json       调度与五阶段声明
  fetch.py        取数：ETF规模 / 期货基差 / 情绪 / 被动化率回溯
  render.py       计算 + 出两份 HTML + out/report.md
  emit.py         写 out/latest.json（10 条指标）
  dispatch.py     推送（出清完成时静默）
  scripts/        取数与计算实现（与独立仓库 clearance-cycle-monitor 同源）
  data/           取数产物（框架 artifacts 默认位置，运行时同步）
  output/data/    脚本原生产物目录
  output/report/  脚本原生报告目录
  out/            框架产物：report.md / latest.json / *.html
```

## 指标

`clearance.ma_state` / `clearance.days` / `clearance.ma_gap_pct` / `clearance.flat_days` /
`clearance.passive_pct` / `clearance.passive_pct_dog` / `clearance.basis_annual_pct` /
`clearance.basis_drop_pp` / `emotion.heat` / `emotion.stage`

## 调度

工作日 16:10（日频）。出清完成后 `dispatch` 自动静默，指标照常落库。
