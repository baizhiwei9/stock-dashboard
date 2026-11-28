# 库存周期 × 上下游利润转移（周频跟踪）

`case/cn_us_inventory` 的周频子模块。月度 `inventory_monthly.py` 管“中美库存周期与切换预测”，
这里管两件月度数据看不到的事：**库存的真实/名义背离**和**上下游利润转移的宽度与方向**，
用来给每月 27 号前后的规上工业企业效益数据做预习。

## 数据来源

全部经 `datahub` 的 EDB 取数（不直连统计局/交易所），依赖以下 series 分组：

| 组 | series | 用途 |
| --- | --- | --- |
| PPI 明细 | `CN.PPI.MINING/RAW/PROC/LIVE/PROD.YOY`、`CN.PPIRM.*` | 三层梯度、出厂−购进宽度 |
| 价格总量 | `CN.PPI.YOY`、`CN.CPI.YOY`、`CN.M1.YOY` | 剪刀差、领先链 |
| PMI | `CN.PMI.RAW.INV`、`CN.PMI.FIN.INV`、`CN.PMI.MFG` | 库存分项交叉验证 |
| 工企 | `CN.PROFIT.INV.YOY/INV.AMT/INV.DAYS/AR.DAYS/AR.YOY/OPM` | 名义/实际库存、周转、回收期 |
| 利润占比 | `CN.PROFIT.SHARE.UP/MID/DOWN/UTIL` | 利润重心在哪个环节 |
| 高频价格 | `CN.FUT.*`、`CN.REBAR.FUT`、`CN.COMMODITY.CCPI`、`GLOBAL.BRENT/LME.*` | 纵向梯度代理、三黑一色动量 |
| 高频库存 | `CN.EXINV.*` | 库存周频代理（交易所仓单口径） |

其中 `nbs:ppi_hist` / `nbs:pmi_hist` / `nbs:profit_hist` / `nbs:profit_share` 与
`akshare:cn_fut_main` / `akshare:cn_exch_inv` 是配套 datahub PR 新增的接口；缺它们时
`compute_edb.py` 会在月度列上大面积留空，不会编数。

## 运行

```bash
export PYTHONPATH=/path/to/datahub-multi-tier:$PYTHONPATH
python3 compute_edb.py     # 取数 + 指标计算 -> data/edb_monthly.csv, data/edb_daily.csv, data/key_numbers.json
python3 build_report.py    # 出 HTML（数据全部读上面两个 CSV，不再联网）
```

产物：`库存周期与利润转移周报_YYYYMMDD.html`，固定七节（总览 / 库存看板 / 剪刀差宽度表 /
纵向梯度 / 利润转移结论 / 趋势切换预警 / 下月验证项）。

## 口径要点

- **名义与实际分开看**：实际库存 = (1+存货同比)/(1+PPI 同比)−1，符号法象限与 3 月均动量口径要一起给，
  两套口径打架时以“谁快谁慢”解释，不取舍。
- **宽度只比方向与相对变化**：PPI 自 2026 年起以 2025 为基期，跨基期不连比。
- **周频只作方向**：交易所库存是仓单口径、交割月集中注销会假摔，不当真实去库；
  4 周均线判近月、13 周判季度，连续 3–4 周同向才算切换。
- 已知缺口：螺纹钢社会库存、水泥库容比无接口；LME 库存接口滞后；动力煤无连续主力（用焦煤代理）。

## 数据归档（独立 SQLite，保留 ≥ 半年）

`python3 archive_macro.py` 把本模块用到的 70 条月度/日频序列 upsert 进 `archive/macro_archive.db`：

- 表 `series_data(series_id, date, value, freq, unit, source, updated_at)`，主键 `(series_id, date)`，增量幂等，可反复跑；
- **保留期 365 天（一年）**，硬上限 366 天（容闰年）—— 口径是「最少 60 天、最多一年」。
  单独建库的原因：datahub 统一库的 60 天窗口会把月度序列裁到只剩 1 个点，持续月数根本算不了；
- 库文件随仓库提交（异地副本）；工作区本身挂在 CFS 网络盘上，换台机器 clone 下来直接可查；
- 查询：`sqlite3 archive/macro_archive.db "select date,value from series_data where series_id='CN.PPI.YOY' order by date desc limit 12;"`
- 体检：`python3 archive_macro.py --check`（打印每条序列的起止、行数、跨度与保留策略）。

当前覆盖：70 条序列 / 4,428 行 / 1.0 MB，最早 2025-11-28，跨度 365 天。
日频 242~258 个交易日；月频 7~11 期（缺月是发布稿解析缺口，非保留期裁掉的）。
`--rebuild` 重建，日常用默认增量模式。