# commodity_spread_scanner（价差回归扫描器）

商品期货跨品种价差的信号计算与报告模块，原为独立工程，2026-11-25 随
`case/futures_xsec_hedge` 入库，作为「价差套利单元」的策略代码存档。

## 来源与完整性（重要）

- 本包是另一 agent 私有 workspace（`/root/.coze/agents/<id>/workspace/commodity_spread_scanner`）
  的快照，那个 workspace 不在任何 GitHub 仓库里，随时可能被清理，因此入库保存。
- **这份快照不完整**：原工程里的 `data_update.py`、`load_data.py`、`data_fetcher.py`、
  `notifier.py` 等运行支撑模块没有随包迁出，仓库历史里也从未出现。
- 因此 `case/futures_xsec_hedge/run_merged_daily.py` 的套利单元（它按
  `from scanner.data_update / scanner.load_data` 导入，并在 case 根下找 `scanner/`）
  **仍然不可用**，会照旧降级为横截面对冲单单元。入库本包不会改变这一点。

## 能跑的部分

```bash
cd case/futures_xsec_hedge
python data_update.py                     # 先经 DataHub 生成 data/full/all_kline.csv
python -m commodity_spread_scanner.run_spread_monitor            # 打印报告
python -m commodity_spread_scanner.run_spread_monitor --send     # 发飞书群
python -m commodity_spread_scanner.run_spread_monitor --summary  # 仅持仓汇总
```

- 行情读取：`data/full/all_kline.csv`（可用 `SPREAD_KLINE_FILE` 指向同结构 CSV），
  同一交易日取持仓量最大的合约为该品种主力。
- 状态文件：`data/spread_regression_state.json`（可用 `SPREAD_STATE_FILE` 覆盖）。
- 飞书：webhook 与凭据来自 `case/_lib/feishu_creds`（环境变量 / secrets），
  chat_id 不再写死在代码里。

## 配对表口径

`config/pair_table.csv` 现有 15 组：农产品 8、贵金属 1、化工 6，**没有有色金属配对**；
而 `config/config.py` 的 `CONTRACT_MULTIPLIER` / `COST_MODEL` 覆盖 25 个品种（含有色）。
两者不对称说明这份配对表是旧版，原始有色配对定义未随快照迁出；补齐需要原工程数据，
本包内不做猜测性补录。