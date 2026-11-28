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
  emit.py         写 out/latest.json（10 条指标；框架下由 pipeline.py 的 emit hook 交回，见下）
  pipeline.py     五阶段钩子：只实现 emit（框架 emit 分支不执行 stages[].cmd）
  dispatch.py     推送摘要（出清完成时静默；**当前通道未接入，只回显**）
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

## emit 为什么走 hook

`case/_lib/runner.py` 的 `run_stage` 对 `emit` 单独分支处理，只认 `hook` 与 `builtin`，
**不执行 `stages[].cmd`**。所以 `task.json` 里 emit 写 `{"hook": true}`，
由 `pipeline.py::emit(ctx)` 调 `emit.build_points()` 把 10 条 points 交回框架，
框架再写 `out/latest.json` 并落指标仓。`emit.py` 仍保留，供 `run_daily.sh` 之类无框架场景直跑。

取数产物一律优先读契约目录 `data/`，`output/data/` 只作回退（脚本原生输出位，不入库、会被清）。
