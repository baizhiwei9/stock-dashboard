# case/_shared —— 跨任务共用的小工具与产物目录

`case_registry` 只扫「有 task.json 的目录」，`_` 开头的目录整层跳过。
本目录里有两类东西：

| 内容 | 是什么 | 谁在用 |
|---|---|---|
| `report_stats.py` / `broad5_risk.py` / `portfolio_stats.py` | 多任务复用的报告统计小工具 | `manning_flow` / `margin_detail` / `margin_flow` / `tech_consensus` 等 |
| `limit_up_predictor/` | 涨停预测的模型与取数代码（2205 只 × 3 年，模型 `output/stacking_model_latest.pkl` 1.3MB） | `case/limit_up_morning`、`case/limit_up_noon` |
| `reports/` | 五策略 / 资金面因子日报的历史落盘（`five_strategy_*.json`、`factor_*.json`） | `case/five_strategy`、`case/factor_daily`、DataHub `task_tags_daily` |

## 为什么不在各自任务目录里各放一份

早盘/午盘共用同一个 `output/`：午盘要读早盘刚写的 `daily_top20_<date>_morning.json`；
五策略与因子日报也共用同一个 `daily_reports/`（DataHub 的 `task_tags_daily` 按文件名扫目录
取最新，拆成两个目录反而会让它只看到一半）。

所以任务目录里这两个子目录都是**符号链接**，指向这里：

```
case/five_strategy/daily_reports  -> ../_shared/reports
case/factor_daily/daily_reports   -> ../_shared/reports
```

`output/`、`reports/` 里的内容都是运行产物，**不入库**；入库的只有代码和 `.gitkeep`。

## 取数

两个任务都只走 DataHub（`data_hub`），腾讯 / akshare / tushare 只在 DataHub 整体不可用时兜底。
DataHub 根由 `stock-dashboard/datahub_path.py` 统一解析。
