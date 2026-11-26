# 日更 / 周更任务收拢清单

> 生成时间：2026-11-26
> 目标：把散落在仓库外的**日更 / 周更**任务代码统一收进 `case/` 框架，代码就位但**不切 crontab**，
> 保留「一步替换」能力。用户确认后再执行 `scripts/switch_crontab.sh --apply`。

## 一、边界（谁进、谁不进）

| 类型 | 处理 | 原因 |
|---|---|---|
| 日更（工作日一次 / 每日一次） | ✅ 收进 `case/` | 本次范围 |
| 周更 / 月更 | ✅ 收进 `case/` | 本次范围 |
| 15 分钟、1 小时、盘中多点采样（期权 OI / 期货基差 / 小波 / 缠论模拟盘 / 杨康平盘中 / 策略监控启停） | ❌ 不动 | 用户明确：继续走原数据库、原路径 |
| 数据采集本身（data_hub 各 collector） | ❌ 不动 | 已统一在 `datahub-multi-tier` |

## 二、清点结果：29 条日更/周更任务

- **仓库内已有 8 条**（全在 `datahub-multi-tier`）：PCR-MACD、两融入库、偏度预警skew10、期权OI日报、期权PCR收盘、期货基差日报、VIX日频入库、收盘期权监控
- **散落在仓库外 21 条**：全部找到源码，缺失 0 条 → **本次已全部收进 `stock-dashboard/case/`**

## 三、21 条迁移明细

| # | case key | 任务名 | crontab 原名 | 频率 | 入口 | 文件数 | 体积 | enabled |
|---|---|---|---|---|---|---|---|---|
| 1 | `midline_preference` | 中线偏好监控 | 中线偏好监控 | 工作日 19:30 | `midline_preference_monitor.py` | 2 | 130 KB | `False` |
| 2 | `circulation_daily` | 环流日报 | 环流日报 | 工作日 19:00 | `circulation_daily.py` | 35 | 530 KB | `False` |
| 3 | `unified_screener` | 多头排列选股 | 多头排列选股 | 工作日 15:30 | `unified_screener.py` | 28 | 301 KB | `False` |
| 4 | `sector_screening_v4` | 板块筛选v4 | 板块筛选v4 | 工作日 16:00 | `板块筛选v4_fast.py` | 43 | 453 KB | `False` |
| 5 | `risk_release_monitor` | 风险释放监控 | 风险释放监控 | 工作日 17:00 | `risk_release_monitor.py` | 2 | 41 KB | `False` |
| 6 | `risk_forecast_daily` | 风险预测·日终 | 风险预测·日终 / **风险预测·午间** | 工作日 20:50 + 12:30 | `risk_forecast_daily.py` | 46 | 1624 KB | `False` ⚠️ 一个脚本两条 cron，**必须同切** |
| 7 | `liquidity_risk_monitor` | 流动性风险监控 | 流动性风险监控 | 工作日 19:00 | `liquidity_risk_monitor.py` | 46 | 1624 KB | `False` |
| 8 | `merged_analysis` | 合并分析 | 合并分析 | 工作日 19:10 | `merged_analysis.py` | 2 | 10 KB | `False` |
| 9 | `top200_concept` | 涨幅Top200概念 | 涨幅Top200概念 | 周二/周四 20:00 | `top200_concept_report.py` | 72 | 2751 KB | `False` |
| 10 | `concept_tracker_top20` | 热点概念Top20 | 热点概念Top20 | 周一 08:00 | `concept_tracker_top20.py` | 5 | 36 KB | `False` |
| 11 | `bond_four_factor` | 国债期货四因子 | 国债期货四因子 | 周六 07:00 | `main.py` | 5 | 62 KB | `False` ⚠️ 调 LLM，**不纳入切换** |
| 12 | `cycle_monitor` | 库存信贷周期 | 库存信贷周期 | 每月 28 日 20:00 | `cycle_monitor.py` | 10 | 366 KB | `False` |
| 13 | `oil_monitor` | 油价月报 | 油价月报 | 每月 1 日 20:00 | `oil_monitor.py` | 2 | 35 KB | `False` |
| 14 | `style_drift_monitor` | 风格漂移监控 | 风格漂移监控 | 工作日 19:20 | `style_drift_monitor.py` | 46 | 1624 KB | `False` |
| 15 | `vol_monitor_daily` | 股指波动率监控 | 股指波动率监控 | 工作日 12:00 | `vol_monitor_daily.py` | 5 | 138 KB | `False` |
| 16 | `credit_spread_weekly` | 信用利差周报 | 信用利差周报 | 周一 08:00 | `panoramic_monitor.py` | 34 | 384 KB | `False` |
| 17 | `margin_season_weekly` | 两融季节同比(周五) | 两融季节同比(周五) | 周五 18:30 | `weekly_combined_report.py` | 60 | 574 KB | `False` |
| 18 | `hurst_daily` | Hurst指数 | Hurst指数 | 工作日 12:30 | `run_hurst_cron.sh` | 8 | 130 KB | `False` |
| 19 | `tugou_archive` | 土狗归档 | 土狗归档 | 每天 23:30 | `run_daily.sh` | 3 | 21 KB | `False` |
| 20 | `weekly_stock_monitor` | 自选股周报 | 自选股周报 | 周日 10:00 | `weekly_stock_monitor.py` | 24 | 182 KB | `False` |
| 21 | `tech_consensus_daily` | 科技共识日报 | 科技共识日报 | 工作日 19:00 | `run_daily.py` | 51 | 250 KB | `False` |

合计：**21 个任务，529 个文件，11.0 MB**（`case/` 全量 15 MB，含原有 15 个任务与 `_lib`/`_shared`）

### 源码原位置分布

| 源目录 | 条数 |
|---|---|
| `/Coze/Drive/蓁宝/所有对话/主对话`（另一 agent 工作目录） | 14 |
| `/opt/coze/.fuse_drive`（云盘老目录） | 4 |
| `/root/.coze/agents/7691719640636244234/workspace` | 1 |
| `/root/tech-consensus-daily` | 1 |
| `/Coze/Drive/蓁宝/所有对话/主对话/土狗归档` | 1 |

## 四、怎么切的

全部走仓库自带的 `case/_tools/import_task.py`，数据源是 `case/_seed/tasks.json`：

```bash
python3 case/_tools/import_task.py --dry-run                    # 只看不写
python3 case/_tools/import_task.py                              # 导入全部
python3 case/_tools/import_task.py --only tech_consensus_daily  # 只导一个
```

本次对 `import_task.py` 做了 3 处增强（原版只认 `.py` 入口）：

1. `pick_entry()` 的候选扩展名加入 `.sh`
2. 兜底目录扫描的正则匹配同样接受 `.sh`
3. `cmd` 按入口扩展名生成：`.sh` → `bash xxx.sh`，否则 `python3 -u xxx.py`

> 踩坑记录：`_seed/tasks.json` 里 `py` 字段**必须写绝对路径**。写相对路径时 `_abs_py()` 会按
> workspace 根去拼，解析失败后入口会退化成按文件名打分，容易选错（曾把 `daily_english_email_learner.py`
> 当成 4 个不同任务的入口）。

## 五、清理掉的噪音

| 任务 | 处理 | 说明 |
|---|---|---|
| `tugou_archive` | 删 `articles/`（351 篇 md）、`logs/`、`archive_state.json`、`playwright_state.json`、`crontab_bak_*.txt` | 仓库只存代码，数据/产物不进库。剩 3 文件 |
| `sector_screening_v4` | 从整目录 400 文件 → `collect_code.py` 精确 import 闭包 42 个程序文件 | 原目录混了大量历史垃圾（crontab 备份、旧 json、临时 md） |
| `midline_preference` / `risk_release_monitor` / `merged_analysis` | 各 62 文件 1.9 MB → 只留入口 py + `task.json` | `collect_code.py` 证明三个任务闭包都只有 1 个 py，原来重复打包了同一批公共模块（浪费 5.7 MB） |

精确闭包工具：

```bash
python3 case/_tools/collect_code.py --root <源目录> --entry <入口.py> --list
```

## 六、一键切换（现在不要执行）

```bash
bash scripts/switch_crontab.sh              # dry-run，打印将删的 crontab 行 + 将开启的任务
bash scripts/switch_crontab.sh --apply      # 真切：21 个 task.json 置 enabled=true + 从 crontab 删 21 行
bash scripts/switch_crontab.sh --revert     # 回滚：置 enabled=false + 恢复最近一次 crontab 备份
```

脚本行为：

- 只按 `--name "任务名"` 精确匹配删除，**不碰**高频任务（期权采样、期货基差采样、小波信号、缠论模拟盘、杨康平盘中、策略监控启停）
- `--apply` / `--revert` 前自动备份 crontab 到 `logs/crontab.<时间戳>.bak`
- 切换是原子的：**开 scheduler 任务和删 crontab 行在同一次执行内完成**，不会出现双跑或停摆

当前状态：**21 个任务全部 `enabled: false`**，crontab 一行未动。dry-run 实测命中 21 行、移除后 crontab 由 189 行降至 168 行。

## 六点五、不纳入切换的 1 条：`bond_four_factor`

`case/bond_four_factor/` 代码留在库里，但**不进 `scripts/switch_crontab.sh` 的切换清单**。

原因：它确实调大模型。`main.py:31` 会 `from llm_utils import llm_analyze`，
`llm_utils.py:50` 打 `api.deepseek.com/v1/chat/completions`。
按「消耗大模型的不用管」的边界，这条继续走原 crontab（周六 07:00）。

因此切换清单是 **20 个 key / 21 条 cron**（`risk_forecast_daily` 一个 key 对两条 cron）。

**该目录原本还有一个安全问题已在本批修掉**：
`llm_utils.py:16` 曾写死明文 DeepSeek API key。现已改为读
`DEEPSEEK_API_KEY` 环境变量，回退 `~/.config/stock-dashboard/secrets.json` 的
`deepseek.api_key`（走 `case/_lib/app_secrets` 统一解析）；无 key 时打印一行提示并跳过
LLM，只出规则结果，不再带着空 Bearer 去打接口。

> ⚠️ 该 key 已经进过 git 历史，**必须去 DeepSeek 后台轮换**，然后写入本机：
>
> ```bash
> PYTHONPATH=case/_lib python3 -c "import app_secrets; print(app_secrets.set_value('deepseek.api_key', '<新key>'))"
> PYTHONPATH=case/_lib python3 -c "import app_secrets; print('已配置' if app_secrets.get('deepseek.api_key','') else '未配置')"
> ```
>
> `app_secrets.py` 命令行只有 `--check` / `--paths` / `--init` / `--env`，没有 `set` 子命令；
> `deepseek.api_key` 也不在门禁清单里，`--check` 不会提到它。

另外 `case/hurst_daily/.tmp/trunc/` 里曾误带一份含同一 key 的调试日志入库，
已删除并在 `.gitignore` 补 `case/*/.tmp/`。

---

## 七、还差什么

- [x] 21 条源码收进仓库、入口全部可执行
- [x] 噪音文件清理
- [x] 一键切换脚本（dry-run 验证通过）
- [ ] **等用户点头后再 `--apply`**（用户原话：crontab 先不用替，代码先放上）
- [ ] 切换后逐个跑一遍 `scheduler.py run <key>` 验证输出正常
- [ ] `case/_seed/tasks.json` 的 `root` 字段仍指向原绝对路径，切换后建议改为 `{WS}` 相对路径
