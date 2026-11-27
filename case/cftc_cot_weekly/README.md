# cftc_cot_weekly —— CFTC COT 极值持仓周报

四个境外商品（COMEX 铜 / 黄金 / 白银、CBOT 玉米）的管理基金净持仓
**104 周 min-max 区间位置**，判断极值方向、已持续周数与剩余持有窗口。

| 项 | 值 |
|---|---|
| 原入口 | `python3 bin/cftc_cot_weekly.py [--qq]` |
| 原 crontab | `0 9 * * 6`（周六 09:00） |
| 现 sched | `{"mode":"weekly","at":"09:00","weekdays":[6]}` |
| 阶段 | fetch → check → render → emit → dispatch |
| 板块 | `futures` |

> `weekdays` 是 ISO 星期：6 = 周六。

## 口径（回测验证过的部分，不动）

```
COT104 = mm_net 在过去 104 周（不含未来）的min-max 区间位置（非统计分位），0~1
        ≥ 0.90 → 做多
        ≤ 0.10 → 做空
        其余   → 观望
持有窗口 = 4~8 周（历史极值停留：中位 4 周、P75 6 周）
剩余周数 = max(0, 8 − 已持续周数)
```

实测（2020-2026，74 次事件去重）：4 周胜率 77%，均 +395bp。

## 指标

| key | sec | 含义 |
|---|---|---|
| `cot.index` | 见下 | COT104 区间位置（0~1，3 位小数） |
| `cot.weeks_left` | 见下 | 剩余持有周数（观望时 missing） |
| `cot.direction` | 见下 | 1 做多 / -1 做空 / 0 观望 |

**sec 归属**：金银铜用国内对应主力合约（`CU.SHF` / `AU.SHF` / `AG.SHF`，
DataHub tech catalog 里真实登记）；**玉米（C.DCE）在 catalog 里没有登记**，
所以玉米**只进报告、不写指标**（`emit` 阶段显式跳过并打日志）。
写一个未登记的 sec 会变成无归属证券、面板上根本看不到 —— 宁可少写三条，
也不制造看不见的指标。等 catalog 补了 C.DCE，把 `task.json` 里那三条补回去即可。

## 数据来源

**只走 DataHub**：`data_hub.collectors.futures.cftc_cot.get_cftc_cot()`。
采集器自己负责：年度 zip 下载/缓存/解包、品种筛选、与价格对齐（`align="mon"`）。
返回字段已经够用：`market, week, report_date, oi, mm_long, mm_short, mm_net,
d_mm_net, q_flow, close, ret`。

原脚本 `_refresh_cftc_zips()` 直连 cftc.gov 的逻辑**不再需要**。

## 品种

原脚本 `NAMES` 是 **copper / gold / silver / corn** 四个。
任务描述里提到的 `crude` 在 DataHub 有数据，fetch 阶段一并取回存在
`data/cot_panel.csv`（备用），但**不进报告、不进指标** —— 不凭空扩大面板。

需要加品种时改 `pipeline.py` 的 `MARKETS`，并在 `task.json` 的 `metrics` 里补三条。

## 出图

`out/cot.png`：4 个子图，各品种近 120 周 COT104 走势 + 极值带（≥0.90 红 / ≤0.10 绿）。
中文字体优先 Noto Sans CJK，退化到 DejaVu Sans。出图失败不影响报告与指标。

## 推送

默认关。`COTC_PUSH=1` 才发。
