# wide_ens_weekly —— 宽基集成周报（统一选模）

四家族候选统一按滚动 500 日分位选模，输出 **T+1/T+5 × 普适/高精度 四张表**
和每个指数的覆盖率。

| 项 | 值 |
|---|---|
| 原入口 | `bash bin/wide_ens/unified_weekly.sh` |
| 原 crontab | `30 15 * * 0`（周日 15:30） |
| 现 sched | `{"mode":"weekly","at":"15:30","weekdays":[7]}` |
| 阶段 | fetch → check → render → emit → dispatch |
| 板块 | `stock` |

## 指标

| key | sec | 含义 |
|---|---|---|
| `wd.ens.t1_best` | 五宽基 | T+1 最优槽位的**模型名**（如 `enh:lgb_tri_d3_T1`），文本型 |
| `wd.ens.t5_best` | 五宽基 | T+5 最优槽位的模型名 |
| `wd.ens.acc` | 五宽基 | 该指数 T+1/T+5 两个周期里 `status=ok` 的比例（%） |

模型名用文本而不是数字：`enh:lgb_tri_d3_T5` 这类标识本身有信息量，压成数字就失去意义
（DataHub 的 `value_text` 正是为此设计）。

## ⚠️ 本任务是上游

写出的 `unified_selected.json` / `unified_signal.json` 被 `wide_t5` 和 `wide_t1` 读。
原 crontab 顺序：**本任务 15:30 → wide_t5 20:00 → wide_t1 次日 08:30**。
手工验证按这个顺序串行，别并行（会互相覆盖 json）。

## `--retrain` 未搬（如实说明）

原 sh 有 `--retrain` 分支：重算 ms/enh/train 全家族（40+ 分钟，会覆盖候选曲线历史）。
那是**调参动作**，不是周日该跑的活。本 case 只做原 sh 的**默认行为**（RETRAIN=0）：

1. `wd_refresh.py --h 1,5 -p 4` —— 重算 wd 家族（这是必须的，见 wide_t5 README）
2. `unified_live.py` × 18 —— 补 live 尾部
3. `unified_select.py` → `unified_signal.py`

需要重训时在 deployment 侧手工跑原 sh，不要在调度里开。

## 数据来源与共用缓存

同 `wide_t1` / `wide_t5`：DataHub 取数，缓存根 `WIDE_WS`，
`.cache/wide_ens/` 里的 `ms/` `enh/` `train/` 候选曲线由本任务写、另两个任务读。

## 推送

默认关。`WENS_PUSH=1` 才发。
