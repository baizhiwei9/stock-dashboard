# stock-dashboard

A 股三级监控看板。**Tab（大类）→ 子类（model）→ task（可定时）**，一套页面把指数结构、期货基差、期权定价、流动性与宏观放在同一口径下看。

数据全部来自同级 [dataHub](https://github.com/baizhiwei9/datahub-multi-tier) 的 `data_hub.tech` 统一接口，本仓库只负责**编排、渲染、发布**，不直连数据源。

---

## 目录

| 文件 | 作用 |
|---|---|
| `build.py` | 生成 `index.html`（唯一渲染入口，纯 Python，无前端框架） |
| `scheduler.py` | 任务调度器 + HTTP 控制台：按 `modules.json` 里每个 task 的启停时间跑；**业务任务不写 crontab**，只用一条 cron 巡检本进程存活（见「调度与守护」） |
| `sd_watchdog.sh` | scheduler 看门狗：cron 每分钟调一次，按心跳判定存活，未运行/僵死则拉起、重启，连续失败才告警 |
| `manager.py` | 命令行运维：模块清单 / 体检 / 手动跑 / 启停 / 板块显隐 |
| `modules.json` | 模块与调度配置，**含密码与注册密钥**（入库方便改；对外公开前必须先改） |
| `sections.json` | Tab 与子类结构 |
| `registry.json` | 已注册 Worker 清单 |
| `publish.sh` | 发布到 Coze Pages |
| `run_daily.sh` | 一次性全量刷新 |

## 快速开始

```bash
# 改 modules.json 里的 api.password / api.registry_key（初始都是 123456，务必改掉）

python3 scheduler.py fg     # 前台跑调度（同时起 HTTP 控制台，端口见 modules.json）
python3 build.py            # 或单次生成 index.html
python3 manager.py list     # 命令行运维：清单 / status / run / enable / disable
```

`manager.py` 是**命令行工具**，不监听端口；HTTP 控制台和注册接口都由 `scheduler.py`
提供（默认端口 `modules.json` 的 `api.port`，现为 `8767`）。

`build.py` 依赖 `data_hub`，需要同级 checkout `datahub-multi-tier` 并把它的根目录加进 `PYTHONPATH`（或把 `data_hub/` 放进同目录）。

## 核心约定

### key 一律用指数，不用 ETF

看板的每个格子用**指数代码**当 key（`000300.SH`、`000905.SH`、`399006.SZ`、`000688.SH`、`000016.SH`、`000852.SH`），不用对应的 ETF。

指数与它的 ETF / 期权标的在 `data_hub.tech.registry` 里配成 pair，取值时哪边有数据用哪边。这样"看股市风险"时，缠论的结构分解和 ETF 期权的情绪定价能落在**同一个 key** 上，不会因为代码不同而拼不起来。

### 期货用品种主连当 key

`AU.SHF`（沪金主连）、`RB.SHF`（螺纹主连）…… 品种是 key，合约是属性。

`AU2412.SH` / `AU0` / `au` / `黄金` / `沪金` 全部归到 `AU.SHF`。看板显示的是**品种视角**：主连价、涨跌、当前主力合约、期限结构（主力−次主力）、基差（仅股指有现货指数）、持仓。

Tushare 主连后缀有坑，注意：SHFE→`.SHF`、DCE→`.DCE`、INE→`.INE`、**CZCE→`.ZCE`**、**CFFEX→`.CFX`**。

### 期权挂到指数上

`option.atm_iv` / `option.iv_skew` / `option.pcr_oi` / `option.pcr_vol` / `option.vix` 五个指标挂在指数 key 上，不单列期权标的。一个指数可能有多套期权（沪深300 既有 IO 股指期权也有 510300 ETF 期权），只取「现货与指数同一只」的那套，口径最干净。

IV / Skew 库内是小数，展示时 ×100 转 %。分位需要 ≥30 个样本才算，不够就 NA——**不编造、不拿旧值顶替**。

## dumate 分支：只展示，任务全部搬进 `case/`

`dumate` 分支把「计算」和「展示」彻底分开：**本仓库不再跑原来的 8 个 producer**
（`modules.json` 里 macro / liquidity / rmt / chanlun / dfa / option / bond / risk 全部 `enabled=false`，
保留配置只为对照与回滚），所有任务以独立目录搬进 `case/`，由 `scheduler.py` 按各自 `task.json`
的 `sched` 定时执行，产出写 DataHub 指标仓，`build.py` 再按 `section` 标签渲染。

```
case/<task-key>/
├── task.json           任务元数据：section / metrics / sched / cmd / deliver
├── out/latest.json     本次产出的标准指标（运行后生成，不入库）
└── *.py                任务代码（白名单复制，不含数据与产物）
```

数据链路：

```
case 任务跑完 → out/latest.json → case/_lib/publish.py → data_hub.tech 指标仓 → build.py
```

任务按标签归类，`section` 同时决定面板 Tab 与投递行为：
股票 `stock` 17 个、期货 `futures` 2 个、地产 `realestate` 1 个，
其余落在 `macro` 4 / `liquidity` 4 / `bond` 4 / `modules` 1。

常用命令：

```bash
python3 case/_lib/case_registry.py          # 列 case 任务与校验结果
python3 case/_tools/import_task.py --dry-run  # 预览从 _seed/tasks.json 导入
python3 case/_lib/publish.py --all -n        # 校验产出（--dry-run 写法见 case/README.md）
python3 scheduler.py fg                      # 调度：内置 + case 一体
python3 manager.py list                      # 清单里 case 任务标 case
```

打包到其他服务器：整目录拷过去，`PYTHONPATH` 指到 DataHub 根，`python3 scheduler.py fg` 即可。
`cwd` 全部相对，不绑死本机绝对路径。细节见 [case/README.md](case/README.md)。

要脱离终端常驻，别用 `nohup`（工具调用结束会连带清掉派生的子进程），改由 `sd_watchdog.sh`
加一条 cron 拉起，见「调度与守护」。

新机器从零部署——前置条件、目录布局、环境变量、首次配置、cron 守护、故障排查、升级回滚——
看 **[docs/DEPLOY.md](docs/DEPLOY.md)**。

## 调度

业务任务不再逐条写 crontab：`scheduler.py` 读 `modules.json` + `case/*/task.json`，每个 task 有自己的启停时间和 enabled 开关，管理页能看到、能点。默认排程：

| 模块 | 时间 | 说明 |
|---|---|---|
| option | 18:10 | 期权日频落库 |
| bond | 18:40 | 债券 · 理财 |
| macro | 21:00 | 宏观与美元环流 |
| liquidity | 21:20 | 流动性与两融 |
| risk | 21:50 | 风险 · 结构 |
| rmt | 22:10 | RMT 随机矩阵 |
| task_tags | 22:20 | 任务结论转录成带标签指标 |
| chanlun | 22:25 | 缠论 + 趋势 |
| dfa | 22:28 | DFA / Hurst |
| task_tags_notify | 22:35 | 读标签发飞书（alert 另发邮件） |
| build | 22:40 | 生成 index.html + 发布 |
| five_strategy | 08:30 | 五策略综合日报（产出由 task_tags 转录） |
| factor_daily | 08:30 | 资金面因子日报 |
| limit_up_morning | 09:00 | 涨停预测 · 早盘 |
| limit_up_noon | 12:00 | 涨停预测 · 午盘（每日一次） |

后 4 个原来是 crontab 里的独立行，现在归 scheduler 管，统一包一层
`data_hub.tech.health_run`：单次失败静默、连败 5 次才发一封邮件、成功一次清零。

### 调度与守护（唯一需要的一条 cron）

业务任务不需要巡检 crontab，但 **scheduler 进程本身需要**：它是单点常驻进程，被 kill 后
所有任务都不会再被触发。所以留一条 cron 专门守护它，每分钟一次：

```bash
* * * * * /path/to/stock-dashboard/sd_watchdog.sh >/dev/null 2>&1
```

看门狗不碰业务，只做三件事：

1. 用 `scheduler.py health` 判定：`0`=健康 / `1`=未运行 / `2`=僵死（进程还在，但心跳
   `logs/scheduler.hb` 超过 3 分钟没更新）。心跳由调度循环每 tick 写，**比 pid 更能代表
   "真的在调度"**；僵死走 `restart`（`start` 会因「已在运行」而空转）。
2. 拉起后再查一次；连续 `SD_ALERT_AT` 次（默认 3）拉不起来才告警，避免每分钟刷屏。配了
   `SD_ALERT_WEBHOOK`（钉钉/飞书自定义机器人）才推送，否则只写 `logs/watchdog.log`。
3. 健康时只更新 `logs/watchdog.hb`（最后巡检时间），日志保持安静 —— 想确认 cron 这行还在
   跑，看这个文件的时间即可。

几件容易踩的事：

- **漏跑窗口 = `GRACE_SEC` 6 小时**：scheduler 一分钟内被拉起时任务照常补跑；停摆超过 6 小时
  才会静默跳过（日志能看到该 slot 未执行）。
- **半途被杀**：子进程句柄只在内存里，新进程收不回来，`state.json` 会留下 `running=true`。
  启动时统一标记 `interrupted` 并写 `scheduler.recovered_tasks`，不再让它假装「跑完了」。
  默认**不自动重跑**（报告类任务重跑会重复推送飞书/邮件）；确认任务幂等后可把 `state.json`
  里 `scheduler.auto_retry_interrupted` 置 `true`，启动时清空该任务 `last_slot`，在宽限内补跑一次。
- **看门狗要能拉起自己**：脚本用 `BASH_SOURCE` 定位自身目录，不写死绝对路径；cron 那行的路径
  换成你的实际部署路径即可，换机器不用改脚本。

### 排队与串行（编排）

原来到点的任务各自起进程，同一分钟到点的几个会并发去抢行情接口。现在默认 **全局串行**：
一次只跑一个，其余进队列，等前一个结束再跑（`modules.json` 的 `queue.max_parallel`，默认 `1`）。

- **到点遇忙不跳过、不并发**：排队任务不写 `last_slot`，每 15 秒的 tick 重新试，直到轮到自己；
  最长等到 `GRACE_SEC`（6 小时）才放弃，并在 `state.modules.<key>.note` 留一句
  「超过宽限未执行（计划 …）」。
- **接力粒度 ≈ `TICK_SEC`（15 秒）**：前一个任务结束后，下一个最多 15 秒才起；
  盘后几分钟级以上的任务可忽略，纯秒级短任务会有这点等待。
- **排队可见**：`scheduler.py status` 的状态列显示「排队」，`state.modules.<key>.note` 写明
  「排队第 N（等 xxx 跑完）」，`state.scheduler.queue` 汇总 `max_parallel / used / orphans`。
- **分组**：任务可写 `queue`（`task.json` 或 `modules.json`），要给某组单独放开并发就配
  `queue.groups.<组名>`；不写则全在 `default` 组（即全局串行）。
- **孤儿进程也算占用**：调度器被 kill 后子进程还在跑，重启时会登记成孤儿（`orphans`），
  继续占名额，等它自己结束才放行 —— 也就是「有任务没销毁就等它」。

代价说清楚：串行会让盘后那一串任务排成一条线，整体跑完比并发晚，换来的是不会同时打接口、
不会两个任务同时写同一份数据。排不下时先调 `max_parallel` 或拆组，而不是关掉队列。

#### 空档提前跑（pull-forward）

不必卡点：队列空着、而下一个任务离自己的计划点还有大段空档时，直接把它提前拉进来跑，
后面的依次顶上，盘后那一串就串起来了——只要求「当天跑掉、报告拿得到」。

- **生效范围**：`queue.pull_forward`（默认 `true`）+ `queue.pull_forward_from`（默认 `"16:00"`）
  + `queue.pull_ahead_min`（默认 `240`）。只在 16:00 之后、最多提前 4 小时；早晨/午间任务照旧
  按点跑，深夜任务不会被拉到下午。
- **数据就绪兜底**：任务可写 `not_before`（如 `"18:30"`）。早于这个点不提前跑，到自己的
  `at` 仍照常执行。不确定某任务的数据几点就绪，就给它配上。
- **去重不变**：提前跑仍以**当天计划点**写 `last_slot`，到点不会再跑第二遍；
  `state.modules.<key>.pulled_forward=true` 记它这次是提前的，`scheduler.py status` 显示「成功(提前)」。
- **关掉**：`queue.pull_forward: false` 即回到「严格到点才跑」。

## 注册机制

外部 Worker 想往看板加自己的数据：

1. 拿注册密钥调 `POST /api/register`，提交 Worker 名称、接口地址、指标声明
2. 进后台待审批列表
3. 管理页用管理密码批准 → 出现在看板

双密钥分开：`X-SD-PW` 管管理动作，`X-SD-KEY` 只管注册。两者都在 `modules.json` 的 `api` 段。

初始值都是 `123456`，跟仓库一起提交是为了改起来方便——**这个仓库一旦要转公开，先改掉这两个值**。

## 给 Agent 读：JSON 快照

页面给人看，程序读 `snapshot.py`：

```bash
python3 snapshot.py --out snap.json      # 指数四模块 + 期权，一次拿全
python3 snapshot.py --futures            # 额外带期货实时快照（联网，慢）
```

复用 `build.py` 同一套 `data_hub.tech` 接口，口径与页面一致。数据契约（key 用指数、
缺的不补零、IV 已转 %、`direction` 只表达方向）见 [docs/agent_api.md](docs/agent_api.md)。

多 Agent 底座那边有对应的取数角色实现，见
[multi-agent#27](https://github.com/baizhiwei9/multi-agent/pull/27)。

## 发布

```bash
./publish.sh
```

先 `cp index.html` 到干净的 `pub/` 再打包（避开 `state.json.tmp` 的打包竞态），必须带 `--app-id`，否则 CLI 会新建一个错误的应用。
