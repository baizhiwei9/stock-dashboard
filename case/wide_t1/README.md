# wide_t1 —— 宽基 T+1 双轨预测

五只宽基（沪深300 / 中证500 / 中证1000 / 科创50 / 创业板指）的**缠论中枢状态 +
AC 闸门 + 低波口径**，给出下一交易日的多空方向和历史校准信心度。

| 项 | 值 |
|---|---|
| 原入口 | `bash bin/wide_ens/t1_daily.sh` |
| 原 crontab | `30 8 * * 1-5`（工作日 08:30） |
| 现 sched | `{"mode":"daily","at":"08:30","weekdays":[1,2,3,4,5]}` |
| 阶段 | fetch → check → render → emit → dispatch |
| 板块 | `stock` |

## 三轨口径（原样保留）

| 轨 | 逻辑 |
|---|---|
| ① 原算法 | walk-forward 预测分（750 日训练窗、5 种子平均）；正=看多、负=看空，绝对值=强度 |
| ② 低波口径 | 只在中枢内且低波时触发：贴下沿看多 / 贴上沿看空；中枢外不适用 |
| AC 闸门 | 只作用于中枢外：AC1_40（近 40 日收益率 lag-1 自相关）≥ +0.10 放行，否则观望 |

`wd.t1.conviction` 取「该中枢状态的历史命中率」（`cal_state.hit`），不是模型自报概率。

## 指标

| key | sec | 含义 |
|---|---|---|
| `wd.t1.signal` | 五宽基 | +1 看多 / -1 看空 / 0 观望 |
| `wd.t1.ac_gate` | 五宽基 | 1 放行 / 0 未放行 |
| `wd.t1.conviction` | 五宽基 | 该状态历史命中率（%） |

## 跨天保留状态（**必需**，删了就退化成「只算中枢、算不出预测」）

这些文件在 `WIDE_WS`（默认 `case/_shared/wide_ens_lib/cache`）下面，**不在** `data/`：

| 路径 | 作用 | 丢了会怎样 |
|---|---|---|
| `<WS>/.cache/chan_zs_t1/panel_<指数>.csv` | walk-forward 历史预测面板 | today_dual 的 `pred` 为空 → 报告里方向全是 NA |
| `<WS>/.cache/chan_zs_t1/dual_ledger.db` | 双轨台账（累年结算） | 台账准确率一栏留空；`--settle/--record` 只影响新记录 |
| `<WS>/.cache/weekly_forecast/raw/idx_*.csv` | 五宽基日线 | 每天 fetch 会用 DataHub 重建（但只有 ~2013 起） |
| `<WS>/.cache/weekly_forecast/raw/cross_market_factors.csv` | 跨市场因子 | **fetch 会重建**（DataHub GLOBAL.*），不必手备 |
| `<WS>/.cache/wide_ens/` | 选模候选曲线 | 本任务不写，由 `wide_ens_weekly` 生成 |

`data/` 下只有本次运行产物（`gate.json` / `stage_*.json` / `today_dual.json`），
可以随时删。

## 为什么 `cross_market_factors.csv` 必须在 fetch 里造出来

`research_wf/xm_incr_test.build_X()` 开头**无条件** `pd.read_csv(cross_market_factors.csv)`。
文件不存在 → 抛异常 → `today_dual` 的 `pred_today` 落空 → 静默回退到 panel
最后一行的旧预测（实测会退回 5 周前）。所以：

* fetch 用 DataHub `GLOBAL.SP500 / NASDAQ100 / VIX / DXY / BRENT / GOLD / LME.CU / LME.AL`
  现造该文件（48 列 / 8 个市场）；
* render 之后**自检**：若 5 个指数的 `pred_std` 全为空 → 报警且不写指标。

## 数据来源

全部走 DataHub：

* `CN.MARKET.INDEX.DAILY` → 五宽基日线
* `RATE.CN.*` / `RATE.US.*` / `RATE.SPREAD.CN10Y_US10Y` → 利差因子
* `GLOBAL.*` → 跨市场因子

DataHub **没有**的利差列（`cn_cs_3y` 中债中短票 AAA 3Y、`qvix300/qvix500`、
`iv_atm300`、`skew300`、`us_cs_baa10y`）**留空不编造**，报告末尾会列出。

## 手动跑

```bash
# 一个阶段（调试用）
WIDE_WS=/path/to/cache python3 case/_lib/runner.py --task wide_t1 --only render

# 全流程（不推送）
python3 case/_lib/runner.py --task wide_t1
```

推送默认关：只有 `WT1_PUSH=1` 才会走 `dispatch` 的飞书发送。
