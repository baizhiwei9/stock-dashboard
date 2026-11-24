# case/ —— Dumate 任务目录（框架适配版）

本目录是 **dumate 分支**的唯一任务来源。看板本体（`build.py`）只读指标仓做展示，
取数、计算、检查、报警、出报告、发送全部落在 `case/<task>/` 里，由 `scheduler.py`
（TaskManager）按各自 `task.json` 的 `sched` 定时驱动。

一句话：**任务不再"自己跑完自己发"，而是按框架的五个阶段走：取数 → 检查 → 出报告 → 出指标 → 分发。**

---

## 一、框架怎么驱动任务

`task.json` 里有 `pipeline` 时，调度命令由 `case/_lib/case_registry.py` 自动换成：

```bash
python3 case/_lib/runner.py --task <key>
```

`runner.py` 按 `pipeline.stages` 的顺序执行，阶段名固定五个：

| 阶段 | 干什么 | 产物落点 |
|---|---|---|
| `fetch` | 取数（多数任务顺带算完） | `data/` |
| `check` | 数据/产物的通用校验，不通过就报警 | `out/check.json` |
| `render` | 出报告（一体式任务此阶段为空） | `out/report.*` 等 |
| `emit` | 写指标模板 `out/latest.json`，再落 DataHub 指标仓 | `out/latest.json` |
| `dispatch` | 调任务自己的推送（飞书 / 邮件） | 无 |

跑完写 `out/status.json`（每阶段耗时、退出码、报警），日志在 `logs/run-<时间>.log`。

退出码：`0` 全通过 / `1` 阶段失败 / `3` 检查不通过。`pipeline.on_fail` 可设 `stop`（默认）或
`continue`（失败也把后面阶段跑完，便于一次看清全部问题）。

### 两种接法

- **A 类：出稿与发送分离**（原入口支持 `--no-push` / `--no-feishu` / `--no-email` / `--push` / `--send`）
  `fetch` 跑"不发"的那条命令 → 检查 → 出指标 → `dispatch` 跑"发"的那条。
  检查不过就不会发出去，这是推荐形态。目前 10 个任务属于这类。
- **B 类：一体式**（原入口没有发送开关）
  `fetch` 一次跑完取数 + 出报告 + 自带发送，`check`/`emit` 在后面复盘。
  `dispatch` 明确记录"发送已内嵌"，不重复发。16 个任务属于这类；后续想收紧，就给原入口补
  `--no-push` 再切成 A 类（改完重跑 `case/_tools/make_pipeline.py --only <key>`）。

### 任务目录里的 pipeline.py

每个任务有一个 `pipeline.py`，暴露与阶段同名的函数（钩子优先于 `task.json` 里的 `cmd`）：

```python
def fetch(ctx):    ctx.sh("python3 -u run_daily.py --no-feishu")   # 取数
def render(ctx):   ...                                             # 出报告
def emit(ctx):     return auto_extract(ctx, sec="CN.MACRO")         # 出指标
def dispatch(ctx): ctx.sh("python3 -u run_daily.py")                # 发送
```

`ctx` 提供：`ctx.dir / data_dir / out_dir`、`ctx.sh(cmd, timeout, check)`、`ctx.emit(points)`、
`ctx.save_json(name, obj)`、`ctx.log()`、`ctx.warn(title, lines, level)`、`ctx.dry`。

需要精细控制的任务（`tech_consensus` / `tda_spd`）直接复用原模块的函数，
把"取数结果 → 报告 → 指标 → 发送"拆开，指标按业务语义命名（如 `tech.consensus`、
`tda_spd.position_pct`、`re.cycle_stage`）。其余任务先用 `_lib/extract.py` 的
`auto_extract` 从 `out/*.json` 自动抽取标量字段，先有指标上面板，再逐步精细化。

---

## 二、目录与数据约定

```
case/
├── _schema/task.schema.json    task.json 契约（含 pipeline）
├── _seed/tasks.json            迁移清单：key/name/cron/section/root/entry/skip/needs_llm
├── _lib/
│   ├── runner.py               五阶段驱动（唯一执行入口）
│   ├── checks.py               通用校验：产物 / 新鲜度 / quality 占比
│   ├── alert.py                报警：out/alerts.json + 可选 webhook
│   ├── assets.py               中文字体运行时准备（字体不入库）
│   ├── extract.py              从 out/*.json 自动抽指标
│   ├── case_registry.py        任务注册表（调度器与看板共用）
│   └── publish.py              写 DataHub tech 指标仓并回填 metrics
├── _tools/
│   ├── collect_code.py         入口 import 闭包，只挑 .py
│   ├── import_py.py            按清单导入并生成 task.json
│   ├── make_pipeline.py        批量生成 pipeline.py 与 pipeline 声明
│   ├── check_imports.py        静态检查入口闭包的 import 是否能解析
│   ├── audit_assets.py         盘点源目录里有、case 里缺的子包与运行资产
│   └── fix_imports.py          把漏掉的本地 .py 模块补回任务目录
└── <task-key>/
    ├── task.json               元数据 + 调度 + pipeline
    ├── pipeline.py             五阶段钩子
    ├── data/                   取数产物（运行时生成，不入库）
    ├── out/                    报告、latest.json、check.json、status.json、alerts.json（不入库）
    └── logs/                   运行日志（不入库）
```

- **只放程序文件（.py）**：数据、报告、图片、缓存、日志一律不入库。子包（如 `signals/`）
  也是 .py，必须带全——`check_imports.py` 会按入口闭包检查漏没漏。
- **字体不入库**：`assets.ensure_cjk_font()` 在 `fetch` 阶段从系统字体（或 `CASE_CJK_FONT`）
  落到任务目录，找不到就明确报错，不画豆腐块。
- **输入数据靠注入**：任务不编造数据。需要上游周/月数据的任务（如私有仓库里的 `realestate_daily`），用
  `CASE_DATA_SRC` 指向含数据文件的目录，`fetch` 阶段复制进来；没有就报警并失败。

---

## 三、环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `PYLIBS_DIR` | 是（任务 `requires` 含 `pylibs` 时） | 第三方包目录；命令里用 `${PYLIBS_DIR:?}`，**没设直接报错**，不猜路径 |
| `PYTHONPATH` | 是（依赖 DataHub 的任务） | 指到含 `data_hub/` 的仓库根，`publish.py` 与取数都要用 |
| `EDB_REPO` | 部分任务 | 指到含 `data_hub` 的 EDB 仓库目录（`margin_flow` / `margin_detail` 等） |
| `CASE_DATA_SRC` | 部分任务 | 上游输入数据目录（周/月度任务） |
| `CASE_CJK_FONT` | 可选 | 指定中文字体文件，覆盖自动查找 |
| `CASE_EXTRA_DIRS` | 私有任务 | 额外任务根目录（冒号分隔），用于挂载私有仓库的 case 目录 |
| `CASE_ALERT_WEBHOOK` | 可选 | 报警外发地址（飞书/钉钉机器人），不配则只落盘 |
| `CASE_NO_PUBLISH` | 可选 | 置 1 只写 `out/latest.json`、不落库（离线调试） |

外部依赖（不复制进仓库，由目标机提供）：`data_hub`、`data_hub.collectors.*`、
`feishu_config` / `feishu_pusher` / `feishu_router`、`ts_config` / `ts_session` / `ts_http`。
其中 `report_stats`、`broad5_risk` 等在入口闭包内均为 `try/except` 可选导入，缺了不阻塞。

---

## 四、跑起来

```bash
export PYLIBS_DIR=/path/to/pylibs
export PYTHONPATH=/path/to/datahub-repo:$PWD

python3 case/_lib/runner.py --task tech_consensus              # 完整五阶段
python3 case/_lib/runner.py --task tech_consensus --dry-run     # 只打印阶段，不执行
python3 case/_lib/runner.py --task tech_consensus --only fetch,check
python3 case/_lib/runner.py --task tech_consensus --no-publish  # 只写模板不落库
python3 case/_lib/runner.py --task tech_consensus --verify      # 检查不过只报警、不中断

python3 scheduler.py status        # 每个任务下次时间与状态
python3 scheduler.py run <key>     # 立刻跑一个（走 runner）
python3 manager.py list            # 清单里 case 任务标 case
```

排障顺序：

```bash
python3 case/_lib/runner.py --task <key> --dry-run   # 阶段链路对不对
python3 case/_lib/runner.py --task <key> --only fetch # 取数能不能跑通
python3 case/_tools/check_imports.py                 # 是不是漏了子包
python3 case/_tools/audit_assets.py                  # 源目录还有哪些资产没带
python3 case/_lib/checks.py --task <key>             # 单独跑一次产物校验
```

---

## 五、导入与批量生成

```bash
python3 case/_tools/import_py.py --dry-run         # 看每个任务会带多少 .py
python3 case/_tools/import_py.py                   # 全量导入（会先清空旧任务目录）
python3 case/_tools/import_py.py --only tda_spd
python3 case/_tools/check_imports.py               # 入口闭包 import 检查
python3 case/_tools/fix_imports.py                 # 补回漏掉的本地 .py（先 --dry-run）
python3 case/_tools/make_pipeline.py               # 生成 pipeline.py + pipeline 声明
python3 case/_tools/make_pipeline.py --only tech_consensus --force
```

`import_py.py` 会保留已手工改过入口的 `cmd`（标 `cmd_manual`），`make_pipeline.py` 默认
不覆盖已有 `pipeline.py`（要覆盖加 `--force`）。

---

## 六、task.json 字段（节选）

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` / `title` / `section` | ✓ | 任务键、面板标题、所属 Tab（stock/futures/realestate/macro/liquidity/bond/option/risk/modules） |
| `sched` | ✓ | `{"mode":"daily\|weekly\|monthly","at":"HH:MM","weekdays":[...],"days":[...]}` |
| `cmd` | 有 `pipeline` 时可省 | 直跑命令；有 pipeline 时由 registry 换成 runner 入口，原命令存 `legacy_cmd` |
| `pipeline` | 推荐 | `{on_fail, freshness_days, min_ok_ratio, require_data/report/metrics, artifacts, stages[]}` |
| `metrics` | ✓（可空） | 面板展示的指标；首跑后由 `publish.py` 回填 |
| `deliver` | | `{"report": true, "email": false}`，只做声明与看板标注 |
| `requires` | | 外部依赖名（如 `pylibs`），靠环境变量找 |
| `timeout` / `enabled` | | 超时秒数 / 是否参与调度 |

`pipeline.stages[]` 每项：`name`（五阶段之一）、`cmd` 或 `builtin`（`artifacts`/`emit`）
或 `hook: true`（用任务目录 `pipeline.py` 的同名函数）、`timeout`、`optional`（失败只报警）、`as_of`。

---

## 七、未搬的任务

- `crude_weekly`：定时消息只有自然语言要求，没有可执行命令。
- `cds_weekly` / `repo_collateral` / `macro_four_dim`：必须由大模型完成的环节（检索 CDS 报价、
  判定报告期、联网补财政数据），纯定时会卡住，按用户要求未上传。

## 八、已知待办（目标机首次运行需确认）

- 依赖 DataHub 的任务（`margin_flow`、`margin_detail` 等）
  需要 `PYTHONPATH` 指到 DataHub 仓库、`EDB_REPO` 指到 EDB 仓库，本机无此环境，未做真跑验证。
- `twosigma_rotation` 等少数任务依赖静态输入数据（如 `data/sw_l3_full.csv`），需用
  `CASE_DATA_SRC` 注入或用上游任务产出补到 `data/`。
- 自动抽取的指标键名（`<task>.<文件>.<字段>`）只是过渡命名，真正上海报前建议按业务语义
  改成 `chanlun.bull_score` 这类规范键，并在 DataHub registry 登记。
---

## 九、可见性与私有任务

接口契约单独一份：**`case/CONTRACT.md`**（v1）。写任务的人只看它，不用读 runner 实现。

### 一条规则

`task.json` 的 `visibility` 决定这个任务**上不上牌面**：

| visibility | 定时跑 | 写指标仓 | 上牌面 | 用途 |
|---|---|---|---|---|
| `public`（默认） | ✓ | ✓ | ✓（按 `section` 归 Tab） | 基础数据、结构图这类可以给人看的 |
| `private` | ✓ | ✓ | ✗ | 选行业/选股/中间算法这类只自己用的 |

`private` 任务必须把 `section` 留空（原标签挪到 `section_domain` 备查）。枚举只有 `public`/`private`，
旧的 `internal` 已废弃：写了会判非法，任务以「配置错误 + 停用」出现在调度台，不会静默消失。
看板通过 `case_registry.as_workers()` 拿数据，它只返回 `public` 且 `section` 非空的；
索引与调度走 `as_modules()`，两种可见性都在，只是 `private` 在模块管理页带标记。

### 私有仓库怎么接

私有任务放**独立私有仓库**，不复制框架代码，只按契约写 `task.json` + 脚本：

```bash
export CASE_EXTRA_DIRS=/srv/private-tasks/case
python3 scheduler.py status          # 私有任务和 case/ 下的任务一起排期
python3 case/_lib/runner.py --task tda_structure --dry-run
```

`case_registry.task_dirs()` 先扫 `case/` 再扫 `CASE_EXTRA_DIRS`；同一个 key 重复时先命中的生效，
后出现的记一条冲突。`runner.load_task()` 同样按这个顺序找任务，所以私有任务可以直接用
`runner --task <key>` 跑，报警与日志也写在它自己的 `out/` 与 `logs/` 下。

### 依赖上游

`depends_on: ["option_skew"]` 会在 `fetch` 之前检查上游：`out/latest.json` 存在且非空、
`as_of` 不超 `freshness_days`、`quality=ok` 占比不低于 `min_ok_ratio`。任一条不满足就写一条
`error` 报警并跳过全部阶段——下游不拿过期数据硬算，也不会把报告发出去。跨仓库依赖一样适用。

### 新建一个任务

```bash
cp -r case/_template case/my_task
# 改 task.json：key（=目录名）、title、section、visibility、sched、depends_on
python3 case/_lib/runner.py --task my_task --dry-run
```

分类清单维护在 **`case/_seed/visibility.md`**，改可见性只改 `task.json`，清单同步更新即可。

17 个 `private` 任务（原 12 个 + 2026-11-25 从 public 挪下来的 5 个：`realestate_daily` /
`volume_wavelet` / `rmt_daily` / `rmt_cgap` / `structural_factor`）都在独立私有仓库
**precious-task**（`case/<key>/`），本仓库只保留 12 个 `public` 任务。挂载命令见该仓库 README，或用 `CASE_EXTRA_DIRS` 指过去。
