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
- 两个指标都带 **250 日分位**（`pct_rank_250d`），供双分位告警使用；为此 `fetch_basis.py` 回看 15 个月合约（含已到期，新浪源按合约代码可回溯）

## 双分位邮件告警（2026-11-28 新增）

QVIX 与基差 250 日分位，两把尺子同时看：

| 状态 | 条件 | 动作 |
| --- | --- | --- |
| 双高 `alert` | QVIX 分位 ≥70 **且** 基差分位 ≥70 | 发**风险**邮件：波动率与对冲需求同步升温 |
| 双低 `clear` | 两者**同时** <70 | 发**出清**邮件：双双回落 |
| 分化 `mixed` | 一高一低 | 不动，保持上一状态 |
| 数据不足 `unknown` | 任一分位缺失 | 不动 |

口径：基差用「现货−期货」年化率（正=贴水），**分位越高＝贴水越深＝基差越大**，与 QVIX 同向，所以双高=风险、双低=出清。

**边沿触发**：只在状态切换时发信，同状态不重复；首跑只建基线不发信。状态存 `output/data/alert_state.json`。

**被动化率 7% 提醒线已并入本邮件**（2026-11-28 用户指令）：不再单独发信，只在邮件正文展示读数；≥7% 时行尾标注「已过 7% 提醒线」。

与均线口径**互不影响**：MA40 上穿后报告静默，但双分位告警继续盯下一轮。

邮件出口见 `scripts/mailer.py`：优先 `notify_init` + `data_hub.notify`（email 通道），签名不同逐档降级；发不出去落 `out/mail_draft-*.md` 草稿 + `out/mail_log.json`，不让任务失败（dispatch 是 optional）。

## 情绪温度

五维合成 0–100：宽度（涨停/跌停）、涨幅（中位数）、量能（成交额250日分位）、杠杆（融资20日变化）、波动（QVIX分位反向）。
阶段：冰点<25 → 修复初期25–40 → 中性偏暖40–55 → 亢奋55–70 → 过热≥70。

## 目录

```
case/clearance_cycle/
  task.json       调度与五阶段声明
  fetch.py        取数：ETF规模 / 期货基差 / 情绪 / 被动化率回溯
  render.py       计算 + 出两份 HTML + out/report.md（4 步，含 calc_alert）
  emit.py         写 out/latest.json（13 条指标；框架下由 pipeline.py 的 emit hook 交回，见下）
  pipeline.py     五阶段钩子：只实现 emit（框架 emit 分支不执行 stages[].cmd）
  dispatch.py     两条推送规则：双分位邮件告警 + 报告摘要（出清完成时静默）
  scripts/        取数与计算实现
    fetch_basis.py   IF/IC/IM 合约日线（回看 15 个月）+ 现货 → 年化基差
    realtime_fill.py 盘中实时补当日 bar（DataHub 快照，带交易日/报价日期校验）
    calc_clearing.py 被动化率 / 基差（含 250 日分位）/ 情绪低点
    calc_alert.py    双分位状态机 + 邮件文案
    mailer.py        邮件出口（data_hub.notify 三级降级 + 草稿兜底）
  data/           取数产物（框架 artifacts 默认位置，运行时同步）
  output/data/    脚本原生产物目录
  output/report/  脚本原生报告目录
  out/            框架产物：report.md / latest.json / *.html / mail_draft-*.md / mail_log.json
```

## 指标

`clearance.ma_state` / `clearance.days` / `clearance.ma_gap_pct` / `clearance.flat_days` /
`clearance.passive_pct` / `clearance.passive_pct_dog` / `clearance.basis_annual_pct` /
`clearance.basis_drop_pp` / `emotion.heat` / `emotion.stage` /
`clearance.alert_state` / `clearance.qvix_pct` / `clearance.basis_pct`

## 调度

工作日 **14:50**（日频）。

**盘中口径**：14:50 日线尚未收盘，MA40/MA80、成交额、QVIX、基差均取当时盘中价，与 15:00 收盘价会有差异。报告头部与邮件正文会自动标注「盘中数据，非收盘价」。若某天出现「盘中触发、收盘未触发」的抖动，把 `task.json` 的 `sched.at` 改到 `15:05` 即可，无需改代码。

**数据源兜底**：中证指数官网 / 新浪期货日线取不到时自动退 DataHub 日线；日线仍缺当日，就用 DataHub 实时快照补一根 bar（`scripts/realtime_fill.py`）。补数有三道闸：北京时间的今天必须是交易日、快照报价日期必须等于今天、接口硬超时 40s——任何一道不过就沿用 T-1，绝不造数。成交额补不到时报告显示「—」或「（T-1）」，不按 0 参与分位。深交所 ETF 份额改用 `fund.szse.cn` 单次 xlsx 导出（requests 被 TLS 指纹拦截时退 curl），避免分页接口断连导致深市 ETF 静默缺失、被动化率偏低约 2pp。

**ETF 份额降级口径**：深交所 / 上交所其中一边最终仍取不到时，任务**不中断**——当天照常出报告（均线、基差、情绪都要看），但 `etf_scale.json` 会写 `degraded: true` + `missing_exchanges`，被动化率在报告与邮件里标「数据不全，不做 7% 判定」，`done_flags` 也不把它算成「未过线」。理由是偏低读数会漏报 7% 提醒线，属于错信号，比缺一行数字更危险。两边都取不到才真失败（真没数据，不编）。

出清完成后 `dispatch` 的报告推送自动静默，指标与双分位告警照常运行。

## emit 为什么走 hook

`case/_lib/runner.py` 的 `run_stage` 对 `emit` 单独分支处理，只认 `hook` 与 `builtin`，
**不执行 `stages[].cmd`**。所以 `task.json` 里 emit 写 `{"hook": true}`，
由 `pipeline.py::emit(ctx)` 调 `emit.build_points()` 把 13 条 points 交回框架，
框架再写 `out/latest.json` 并落指标仓。`emit.py` 仍保留，供 `run_daily.sh` 之类无框架场景直跑。

取数产物一律优先读契约目录 `data/`，`output/data/` 只作回退（脚本原生输出位，不入库、会被清）。
