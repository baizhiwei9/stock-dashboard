# stock-dashboard

A 股三级监控看板。**Tab（大类）→ 子类（model）→ task（可定时）**，一套页面把指数结构、期货基差、期权定价、流动性与宏观放在同一口径下看。

数据全部来自同级 [dataHub](https://github.com/baizhiwei9/datahub-multi-tier) 的 `data_hub.tech` 统一接口，本仓库只负责**编排、渲染、发布**，不直连数据源。

---

## 目录

| 文件 | 作用 |
|---|---|
| `build.py` | 生成 `index.html`（唯一渲染入口，纯 Python，无前端框架） |
| `scheduler.py` | 任务调度器 + HTTP 控制台：按 `modules.json` + `case/*/task.json` 里每个 task 的启停时间跑；**业务任务不写 crontab**，只用一条 cron 巡检本进程存活（见「调度与守护」） |
| `sd_watchdog.sh` | scheduler 看门狗：cron 每分钟调一次，按心跳判定存活，未运行/僵死则拉起、重启，连续失败才告警 |
| `manager.py` | 命令行运维：模块清单 / 体检 / 手动跑 / 启停 / 板块显隐 |
| `modules.json` | 模块与调度配置（**不含任何口令**；口令/密钥见 `config/secrets.json`） |
| `sections.json` | Tab 与子类结构 |
| `registry.json` | 已注册 Worker 清单 |
| `publish.sh` | 发布到 Coze Pages |
| `run_daily.sh` | 旧版一次性全量刷新（仍跑 3 个旧 producer，不覆盖 case 任务；dumate 分支建议用 scheduler） |

## 快速开始

> **部署到新机器？先看 [DEPLOY.md](DEPLOY.md)，第一步必须跑 `python3 setup.py`。**
> 飞书凭据（APP ID / APP Secret / Webhook）已从代码中移除，改为安装门禁：凭据没配、
> 配错、或配了连不通飞书，`setup.py` 返回非 0，安装不应继续。运行期 `runner.py` 也会在
> 任务启动时预检凭据，避免跑到最后一步的推送阶段才失败。


```bash
./install.sh                # 首次安装：强制输入 api.password / api.registry_key 等敏感项，缺项不能继续

python3 scheduler.py fg     # 前台跑调度（同时起 HTTP 控制台，端口见 modules.json）
python3 build.py            # 或单次生成 index.html
python3 manager.py list     # 命令行运维：清单 / status / run / enable / disable
```

敏感配置**只放 `config/secrets.json`**（已 `.gitignore`，权限 0600），字段清单见
[`config/README.md`](config/README.md)。仓库里只有占位模板 `config/secrets.example.json`；
`./install.sh` 与 `scheduler.py` 启动前都会校验，缺必填项直接拒绝启动。

`manager.py` 是**命令行工具**，不监听端口；HTTP 控制台和注册接口都由 `scheduler.py`
提供（默认端口 `modules.json` 的 `api.port`，现为 `8767`）。

`build.py` 依赖 `data_hub`：用 `DATA_HUB_PATH` / `DATAHUB_DIR` / `EDB_REPO` 指定根目录，未指定时按同级 `../datahub-multi-tier` 查找（见 `datahub_path.py`）。

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
股票 `stock` 2 个、期货 `futures` 1 个、`macro` 3 个、`liquidity` 3 个、`bond` 3 个，
`modules` 1 个（模板，不调度）。共 12 个 `public` 任务；另外 17 个 `private` 任务在私有仓库
`precious-task`，通过 `CASE_EXTRA_DIRS` 挂进来照常跑、照常落库，只是不上牌面。

常用命令：

```bash
python3 case/_lib/case_registry.py           # 列 case 任务与校验结果
python3 case/_tools/import_py.py --dry-run   # 预览从 _seed/tasks.json 导入
python3 case/_lib/publish.py --all -n        # 校验产出（缺 out/latest.json 时退出码非零）
python3 scheduler.py fg                      # 调度：内置 + case 一体
python3 manager.py list                      # 清单里 case 任务标 case
```

打包到其他服务器：整目录拷过去，`PYTHONPATH` 指到 DataHub 根，`python3 scheduler.py fg` 即可。
任务入口的 `cwd` 已相对化；但部分历史脚本仍引用迁移前 workspace 的绝对路径
（`/home/work/dumate/...`，含少量数据/字体/配置文件），换机首跑前需按实际路径改造或用
`BOND_STRATEGIES_DIR` 之类的环境变量覆盖。细节见 [case/README.md](case/README.md)。

## 调度

弃用 crontab。`scheduler.py` 读 `modules.json` + `case/*/task.json`，每个 task 有自己的启停时间和 enabled 开关，管理页能看到、能点。默认排程：

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
| build | 23:40 | 生成 index.html + 发布（等 23:10 nowcast 落完美东 10:00 版） |
| five_strategy | 08:30 | 五策略综合日报（产出由 task_tags 转录） |
| factor_daily | 08:30 | 资金面因子日报 |
| limit_up_morning | 09:00 | 涨停预测 · 早盘 |
| limit_up_noon | 12:00 | 涨停预测 · 午盘（每日一次） |

后 4 个原来是 crontab 里的独立行，现在归 scheduler 管，统一包一层
`data_hub.tech.health_run`：单次失败静默、连败 5 次才发一封邮件、成功一次清零，
不需要额外的巡检 crontab。

## 注册机制

外部 Worker 想往看板加自己的数据：

1. 拿注册密钥调 `POST /api/register`，提交 Worker 名称、接口地址、指标声明
2. 进后台待审批列表
3. 管理页用管理密码批准 → 出现在看板

双密钥分开：`X-SD-PW` 管管理动作，`X-SD-KEY` 只管注册。两者都不在 `modules.json` 里，
也不写进生成的 `index.html`——统一放在部署机的
`~/.config/stock-dashboard/secrets.json` 的 `api` 段（`password` / `registry_key`，
别名 `scheduler` 段同样可读），由安装脚本 `python3 setup.py` 或 `./install.sh`
强制录入，弱口令直接拒收。审批面板要求现场输入管理密码，由同源调度器校验。
详见 [DEPLOY.md](DEPLOY.md)「敏感配置」与 [docs/SECRETS.md](docs/SECRETS.md)。

改密码：`python3 manager.py sections pw '<新密码>'`（调度器每次请求重读凭据，无需重启）。

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
