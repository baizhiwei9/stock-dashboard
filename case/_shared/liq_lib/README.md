# case/_shared/liq_lib —— 流动性 · 信用周报的行为库

`case/liq_credit_weekly` 的实现全在这里（原来是宿主机 `bin/liq_credit_weekly.py`
单文件 1600 行 + `bin/l5_micro.py` + `bin/ascii_chart.py` + `bin/marketdb.py`）。

| 文件 | 作用 |
|---|---|
| `liq_credit_weekly.py` | 报告本体：分层传导链、卡片构建、风险评估、卡片推送 |
| `liqdata.py` | **取数层**：DataHub EDB 映射 + 60 天保留期的滚动归档 |
| `liqeq.py` | 五宽基日线（DataHub `CN.MARKET.INDEX.DAILY`） |
| `l5_micro.py` | L5 股市微观池（两融/ETF/北向七指标） |
| `ascii_chart.py` | 卡片里的小折线图（纯字符） |
| `marketdb.py` | **兼容垫片**（见下） |

## 为什么需要 `marketdb.py` 垫片

原代码全篇通过 `marketdb.get_series(sid)` + 一条固定的 SQL 读 SQLite 长表
`data/market.db`。改 40 处调用点风险大、收益低，所以放一个**同名同签名**的垫片：

* `get_series(...)` → 转给 `liqdata`（DataHub + 归档）
* `connect()` / `shim_connect()` → 返回一个只认 `select date,value from obs
  where series_id=? order by date` 的最小连接；其它 SQL 直接抛错
* 写库函数（`save_series` 等）→ 抛错，说明「本垫片只读」

**它不是第二数据源**，不建表、不抓外网。

## ⚠️ 60 天保留期 → 滚动归档

DataHub EDB 各模块保留 **60 天**（大佬 2026-11-19 口径）。这套报告要 **252 日**
分位窗口，直接读会塌成 60 日。处理：

* `data/liq_archive/<series>.jsonl`：每次 fetch 把 DataHub 当天给的点 append 进去
* 同日期覆盖、整份原子重写
* 归档只存 DataHub 给过的值，不插值、不补零

另外 `marketdb._all()` 优先读 fetch 落的 `data/liq_series.json` 快照 —— 因为
fetch / render / dispatch 是三个进程，进程内缓存跨不过去，重新抓一次要 4~8 分钟
（含全市场 ETF 份额）。
