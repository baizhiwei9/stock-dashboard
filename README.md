# stock-dashboard

A 股三级监控看板。**Tab（大类）→ 子类（model）→ task（可定时）**，一套页面把指数结构、期货基差、期权定价、流动性与宏观放在同一口径下看。

数据全部来自同级 [dataHub](https://github.com/baizhiwei9/datahub-multi-tier) 的 `data_hub.tech` 统一接口，本仓库只负责**编排、渲染、发布**，不直连数据源。

---

## 目录

| 文件 | 作用 |
|---|---|
| `build.py` | 生成 `index.html`（唯一渲染入口，纯 Python，无前端框架） |
| `scheduler.py` | 任务调度器：按 `modules.json` 里每个 task 的启停时间跑，**不用 crontab** |
| `manager.py` | 管理 API：任务启停、注册审批、密钥校验 |
| `modules.json` | 模块与调度配置，**含密码与注册密钥**（入库方便改；对外公开前必须先改） |
| `sections.json` | Tab 与子类结构 |
| `registry.json` | 已注册 Worker 清单 |
| `publish.sh` | 发布到 Coze Pages |
| `run_daily.sh` | 一次性全量刷新 |

## 快速开始

```bash
# 改 modules.json 里的 api.password / api.registry_key（初始都是 123456，务必改掉）

python3 scheduler.py fg     # 前台跑调度
python3 build.py            # 或单次生成 index.html
python3 manager.py          # 管理 API，默认 :8767
```

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

## 调度

弃用 crontab。`scheduler.py` 读 `modules.json`，每个 task 有自己的启停时间和 enabled 开关，管理页能看到、能点。默认排程：

| 模块 | 时间 |
|---|---|
| macro | 21:00 |
| liquidity | 21:20 |
| option | 18:10 |
| rmt | 22:10 |
| chanlun | 22:25 |
| dfa | 22:28 |
| build | 22:40 |

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
