# case/_shared/wide_ens_lib —— 宽基三任务的共用库

`case/wide_t1`（T+1 日报）、`case/wide_t5`（T+5 周报）、`case/wide_ens_weekly`
（集成周报）原来都长在宿主机的 `bin/wide_ens/` + `research/chan_zs_t1/` +
`bin/wide_daily/` 三处。搬进 `case/` 后放在这里共用一份，避免复制三份互相漂移。

目录结构就是**模块根**：`wsenv.MODULE_DIRS` 把这些目录全塞进 `sys.path`，
所以模块之间的 `import hunt70` / `import today_dual` 照原样可用。

| 目录 | 内容 |
|---|---|
| `wsenv.py` | **入口**。决定缓存根 `WS`、挂 DataHub、暴露 `raw_dir()` / `state_dir()` |
| `datahub_fetch.py` | 取数层：五宽基日线、利差因子、跨市场因子（**全部走 DataHub**） |
| `wide_ens/` | 候选家族训练/选模/卡片（`hunt70` `multi_scale` `enhanced` `train_one` `unified_*` `wd_refresh` …） |
| `chan_zs_t1/` | 缠论中枢 v3 + AC + 波动档 + 双轨台账（`today_dual` `dual_ledger` `zhongshu_v3` `span_sens` `vol_layer`） |
| `wide_daily/` | 日频面板与横向比较（`core` `compare_horizon`） |
| `research_wf/` | walk-forward 特征构建（`xm_incr_test`） |
| `bin/` | `trade_day.py`（DataHub 交易日历）、`marketdb.py`（只读垫片，实际未被宽基任务用） |

## 缓存根（`WS`）怎么定

```
WIDE_WS / WIDE_CACHE 环境变量      ← 优先级最高（换机器/CI）
<本库目录>/cache                    ← 默认（gitignore）
```

三个任务**必须共用同一个 `WS`**：`unified_selected.json`、`panel_*.csv`、
`dual_ledger.db` 是跨任务读写的。详见 `case/wide_t1/README.md`「跨天保留状态」。

## 搬迁时做的改动（口径不变，只改路径/取数）

| 原来 | 现在 |
|---|---|
| `WS = '/root/.openclaw/workspace'` | `from wsenv import WS` |
| `update_data.py` 直连 tushare `index_daily` | `datahub_fetch.fetch_index_csv()`（DataHub EDB） |
| `build_spread_factors.py` 直连 akshare/中债 | `datahub_fetch.fetch_spread_factors()`（DataHub，缺列留空） |
| `build_cross_market.py` 直连 yfinance | `datahub_fetch.fetch_cross_market()`（DataHub `GLOBAL.*`） |
| `bin/trade_day.py` 自拉 tushare 日历 + 自存 json | 薄包装 `data_hub.core.trade_calendar` |
| 推送内嵌在 sh 里 | 移到各 case 的 `dispatch` 阶段，默认关 |

## 已知缺口（如实记录）

`unified_live` / `unified_select` 的训练面板需要两个**宿主机一次性生成的 csv**：

| 文件 | 内容 | DataHub 有无 |
|---|---|---|
| `raw/orth_feats.csv` | 行业宽度 / 离散度 / shibor 等正交因子 | ❌ 无构建器 |
| `raw/opt_feat.csv` | 期权 PCR（成交量/成交额/持仓量） | ❌ 无构建器 |
| `.cache/quark_dl/科创50日频预测包_20261018/` | 111 维包内特征（139MB） | ❌ 无 |

缺这三个时 `unified_live` 会全部失败（pipeline 会打 warn 说明缺哪几个文件），
候选曲线仍能读旧 csv 出报告，但**尾部不会更新**。补法：从原宿主机拷过来。
这是本次迁移**未解决**的点，已列在 PR body 的「未做到」里。
