# liq_credit_weekly —— 流动性 · 信用风险周报

分层传导链：**资金面 → 传导 → 定价极端度**，中国侧与美国侧分别出报告，
并给每一侧一个可复核的压力分。

| 项 | 值 |
|---|---|
| 原入口 | `python3 bin/liq_credit_weekly.py --side cn|us` |
| 原 crontab | `30 8 * * 0 --side cn --refresh-eq`（周日 08:30）<br>`0 11 * * 0 --side us`（周日 11:00） |
| 现 sched | `{"mode":"weekly","at":["08:30","11:00"],"weekdays":[7]}` |
| 阶段 | fetch → check → render → emit → dispatch |
| 板块 | `liquidity` |

## 一条 pipeline，按时点分流 side

原 crontab 是两行，跑同一个脚本的两个 `--side`。迁进框架后是一个任务、一个
`sched.at` 数组（`["08:30","11:00"]`），**fetch 阶段按时点决定本轮跑哪一侧**：

| 触发时点 | side | 内容 |
|---|---|---|
| 08:30 | `cn` | 中国：资金面 / 传导 / 股债定价极端度 + L5 两融微观池 + ETF 净申赎 |
| 11:00 | `us` | 美国：信用分层（CCC/BB/HY/IG）、回购脱离、VIX、SPX 回撤 |

以 **09:45 为界**判断（`pipeline.py::_sides`）。也可显式覆盖：`LIQ_SIDE=cn|us|all`。

两侧各落 `out/report_cn.md` / `out/report_us.md`，互不覆盖。

`--refresh-eq` 那一步（原 sh 里由 `bin/l5_fetch.py` 做的取数）现在由 fetch 统一完成：
五宽基日线走 DataHub `CN.MARKET.INDEX.DAILY`，两融/ETF 走 DataHub 采集器。

## 指标

| key | sec | 含义 |
|---|---|---|
| `liq.credit.cn_score` | `CN.LIQ` | 中国侧 `-(2×RISK + 1×WARN)`，0 = 全绿，越负越紧张 |
| `liq.credit.us_score` | `GLOBAL.MACRO` | 美国侧同口径 |
| `liq.stress_level` | `CN.LIQ` | 两侧取最差：0 OK / 1 WARN / 2 RISK |

**关于 sec**：任务描述里写的 `LIQ.CN` / `LIQ.US` 在 DataHub tech catalog 里
**不存在**。改用实际登记的两个证券 ID（`CN.LIQ`、`GLOBAL.MACRO`）——
写不存在的 sec 会变成无归属指标，面板上找不到。

**关于「分数」**：原脚本没有 0~100 的校准分，只有「几条命中」。这里用
`RISK 计 -2 / WARN 计 -1` 累加，宁可口径粗也要可复核、能一眼验算。
`liq.stress_level` 与 DataHub 的 `risk_level`（none/warning/risk/opportunity）
是两套口径，publish 时再映射。

## ⚠️ 60 天保留期 vs 252 日分位窗口

这套报告的分位数要 **近 252 个交易日**。DataHub EDB 的保留策略是
**60 天**（大佬 2026-11-19 定口径）。所以：

* 第一次跑：能拿到全历史（如 US HY OAS 785 行 / 2010 年起的美债 16k 行）
* 60 天后：库里只剩 60 个点，`pct()` 的窗口会从 252 塌到 60

处理办法：**`data/liq_archive/<series>.jsonl` 滚动归档**。每次 fetch 把 DataHub
当天给的点 append 进归档（同日期覆盖），窗口随运行次数长回来。

归档**不是第二数据源**：它只存 DataHub 给过的值，不自己抓、不插值、不补零。

## 数据来源映射

核心映射写在 `case/_shared/liq_lib/liqdata.py::EDB_MAP`（`market.db` 的 series_id
→ DataHub EDB code）。要点：

| 类别 | 映射 |
|---|---|
| 中国债券曲线 | `cn.cb.govt.*` → `RATE.CN.*` |
| 中国回购 | `cn.repo.FDR00x` → `LIQ.CN.REPO.FR00x`（无 FDR 系列，用 FR 同期限替代） |
| 中国两融 | `cn.eq.margin_*` → `CN.EQ.MARGIN.*` |
| 美国信用 | `us.fred.BAMLH0A3HYC` 等 → `CREDIT.US.OAS.CCC/HY/BB/IG/BBB` |
| 美国利率 | `us.fred.DGS10` → `RATE.US.10Y`、`us.ust.30Y` → `RATE.US.30Y` |
| 风险资产 | `us.fred.VIXCLS` → `GLOBAL.VIX`、`us.fred.SP500` → `GLOBAL.SP500` |
| ETF 净申赎 | `data_hub.aggregate_etf_net_flow()`（份额×价格，亿元） |

DataHub **没有**的：`cn.cb.govt.3Y`（中债 3Y）、`cn.cb.mtn_aaa.3Y`（中短票 AAA 3Y，
信用利差矩阵的主体）、`cn.repo.vol_on_share`（隔夜回购占比）、
`cn.eq.north_flow`（北向）、`cn.eq.new_fund_week`（偏股新发）、
`cn.eq.holder_net_reduce`（股东减持）、`cn.eq.amt_total`（全A成交额）、
`cn.eq.margin_net` / `cn.eq.margin_amt_share`。这些序列在报告里显示「本周期暂缺」，
不会编造数值。**这意味着第③层「四档信用利差矩阵」当前是空的** —— 如实记录。

## 邮件

原实现调宿主机私有脚本 `scripts/send_email.py`，本仓没有等价物。
`send_mail()` 现在直接返回 False 并打印原因（不假装发出）。
RISK 级别的内容改由 `dispatch` 阶段走 `case/_lib/alert`（框架报警通道）投递。

## 飞书

默认关。`LIQ_PUSH=1` 且 `CREDIT_FEISHU_HOOK` 有值才发。
webhook 不写在源码里：走 `case/_lib/feishu_creds`（缺凭据时 HOOK 为空 → 直接跳过）。
返回值只认 `code == 0`（飞书失败也返 HTTP 200，老代码按 HTTP 判成功会静默漏发）。
