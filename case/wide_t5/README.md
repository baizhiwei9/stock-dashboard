# wide_t5 —— 宽基 T+5 周报

五只宽基**下一周方向**：四家族候选（ms / enh / train / wd）统一按滚动 500 日分位
选模，IS(2019~2022) 挑、OOS(2023+) 验收，给方向、胜率、稳定性。

| 项 | 值 |
|---|---|
| 原入口 | `bash bin/wide_ens/t5_weekly.sh` |
| 原 crontab | `0 20 * * 0`（周日 20:00） |
| 现 sched | `{"mode":"weekly","at":"20:00","weekdays":[7]}` |
| 阶段 | fetch → check → render → emit → dispatch |
| 板块 | `stock` |

> `weekdays` 是 **ISO 星期**（1=周一 … 7=周日），见 `scheduler.py::_day_ok` / `_weekdays`。

## 指标

| key | sec | 含义 |
|---|---|---|
| `wd.t5.signal` | 五宽基 | +1 看多 / -1 看空 / 0 观望（未触发或未达标） |
| `wd.t5.conviction` | 五宽基 | 该槽位 OOS 真实命中率（%） |

## render 里那三步为什么必须真跑

1. `wd_refresh.py --h 5` —— wd 家族的 `bagged` 列就是当日 score。不重算，曲线就
   永远停在旧日期（踩过：日频卡用了 3 天前的信号）。**CPU 密集，3~6 分钟/进程**。
2. `unified_live.py` × 20（5 指数 × 2 深度 × {ms,enh}）—— 补候选曲线尾部。
3. `unified_select.py` → `unified_signal.py` —— 挑模 + 出本期信号。

## ⚠️ 依赖与互斥（重要）

本任务**写** `<WS>/.cache/wide_ens/unified_selected.json` 和 `unified_signal.json`，
`case/wide_t1` 与 `case/wide_ens_weekly` **都读**这两份。

原 crontab 顺序（天然错开）：

| 时间 | 任务 |
|---|---|
| 周日 15:30 | `wide_ens_weekly`（先算四张表） |
| 周日 20:00 | **本任务**（T+5） |
| 次日 08:30 | `wide_t1`（T+1） |

手工验证时**按这个顺序串行跑**，不要并行：两个进程同时写同一份 json 会互相覆盖，
且 `unified_select` 期间读到半成品会得出「全部观望」。

## 数据来源

DataHub：`CN.MARKET.INDEX.DAILY`（五宽基日线）、`RATE.*`（利差）、`GLOBAL.*`（跨市场）。
缺列留空不编造（见 `case/_shared/wide_ens_lib/datahub_fetch.py` 的 `SPREAD_MISSING`）。

## 跨天保留状态

与 `wide_t1` 共用同一份缓存根（`WIDE_WS`）：`panel_*.csv`、`dual_ledger.db`、
`.cache/wide_ens/`（本任务写）、`.cache/wide_daily/`。详见
`case/wide_t1/README.md` 的表格。

## 推送

默认关。`WT5_PUSH=1` 才发。
