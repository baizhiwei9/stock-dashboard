# case/CONTRACT.md —— 任务接口契约 v1

这份文件是**框架对任务的唯一约定**。写任务的人（包括私有仓库的作者）只需要看这一份，
不需要读 `runner.py` / `case_registry.py` 的实现，也能让自己的任务被 TaskManager 管起来。

契约只约束四件事：**任务怎么声明、代码放在哪、每个阶段产出什么、写进指标仓的数据长什么样**。
语言不限（Python / shell 都行），只要按这里说的命令行和文件路径工作。

---

## 一、三层结构与可见性

```
框架（本仓库）        TaskManager、runner、registry、publish、schema、看板渲染
   ├── 公开任务        visibility=public  → 定时跑 + 写指标仓 + 上牌面（按 section 分 Tab）
   └── 私有任务        visibility=private → 定时跑 + 写指标仓 + 不上牌面
```

- **框架是公开的**，任务可以是私有的。私有任务不是"不运行"，而是"运行、落库、不展示"。
- 公开/私有由 `task.json` 的 `visibility` 决定，**不是靠两个仓库的目录结构决定**。
- 私有任务放在**独立私有仓库**，通过 `CASE_EXTRA_DIRS` 挂进来；私有任务的代码不进公开仓库。
- 依赖是单向的：私有任务可以读公开任务的产物，公开任务不知道私有任务存在。

---

## 二、目录与挂载

一个任务 = 一个目录：

```
<task-dir>/                 # 目录名必须等于 task.json 的 key
├── task.json               # 唯一必需文件，见第三节
├── pipeline.py             # 可选：五阶段钩子（与 stages.cmd 二选一）
├── data/                   # 取数产物（运行时生成，不入库）
├── out/                    # 报告、latest.json、check.json、status.json、alerts.json（不入库）
└── logs/                   # 运行日志（不入库）
```

框架默认扫描 `case/*/task.json`。额外目录用环境变量挂载（冒号分隔，可多个）：

```bash
export CASE_EXTRA_DIRS=/srv/private-tasks/case:/srv/another/case
```

扫描规则：跳过以 `_` 或 `.` 开头的目录；目录名与 `key` 不一致视为配置错误。
**同一个 key 在多个目录里出现时，先扫描到的生效**（`case/` 优先），并记一条冲突告警。

---

## 三、task.json 字段

### 必填

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | `^[a-z0-9_]{2,40}$`，必须等于目录名 |
| `title` | string | 牌面标题 |
| `sched` | object | `{"mode":"daily\|weekly\|monthly","at":"HH:MM","weekdays":[1..7],"days":[1..31]}` |
| `metrics` | array | 要展示的指标声明，可为空数组；首跑后由 publish 回填 |

### 必填（二选一）

| 字段 | 说明 |
|---|---|
| `pipeline` | 推荐。声明五阶段，由 `runner.py` 驱动 |
| `cmd` | 兼容旧任务。没有 pipeline 时直接跑这条命令，框架不介入阶段与检查 |

### 展示与依赖

| 字段 | 默认 | 说明 |
|---|---|---|
| `visibility` | `public` | `public`：上牌面；`private`：只落库不展示（旧值 `internal` 是 `private` 的别名，过渡期内照常认，但新任务不要再写） |
| `section` | 空 | `visibility=public` 时必填，取值见下；`private` 时必须为空或不写 |
| `depends_on` | `[]` | 上游任务 key 列表；`fetch` 前检查上游产物是否够新，不满足则报警退出 |

`section` 取值：`stock` / `futures` / `realestate` / `macro` / `liquidity` / `bond` / `option` / `risk` / `modules`。

### 运行参数

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否参与调度 |
| `timeout` | `1800` | 单次运行超时秒数 |
| `requires` | `[]` | 外部依赖名，当前仅 `pylibs`（靠 `PYLIBS_DIR` 找；没设则走系统 site-packages，不报错） |
| `deliver` | — | `{"report":bool,"email":bool}`，仅声明与牌面标注，框架不代发 |
| `desc` | `""` | 一句话说明 |

### pipeline 结构

```json
"pipeline": {
  "on_fail": "stop",            // stop：失败即中断；continue：失败也把后面阶段跑完
  "freshness_days": 5,          // 指标 obs_date 允许落后的天数
  "min_ok_ratio": 0.5,          // quality=ok 的最低占比
  "require_data": true,         // 是否要求 data/ 有非空产物
  "require_report": true,       // 是否要求 out/ 有本次生成的报告
  "require_metrics": true,      // 是否要求 out/latest.json 合法
  "artifacts": {
    "data":   ["data/*.json"],
    "report": ["out/report.md", "out/*.png"]
  },
  "stages": [
    {"name": "fetch",    "cmd": "python3 -u fetch.py", "timeout": 1200},
    {"name": "check",    "builtin": "artifacts"},
    {"name": "render",   "cmd": "python3 -u render.py"},
    {"name": "emit",     "builtin": "emit"},
    {"name": "dispatch", "cmd": "python3 -u dispatch.py", "optional": true}
  ]
}
```

`stages[]` 每项：

| 字段 | 说明 |
|---|---|
| `name` | 五阶段之一：`fetch` / `check` / `render` / `emit` / `dispatch`，顺序即执行顺序 |
| `cmd` | 该阶段的命令，工作目录是任务目录 |
| `builtin` | 用框架内置实现：`artifacts`（通用校验）、`emit`（写 latest.json 并落库） |
| `hook` | `true` 表示用任务目录 `pipeline.py` 里的同名函数 |
| `timeout` | 该阶段超时秒数 |
| `optional` | `true` 时失败只报警、不中断（分发阶段常用） |
| `as_of` | 仅 emit 用，固定 `as_of`；不填取各指标 `obs_date` 最大值 |

优先级：`pipeline.py` 的同名函数 > `stages[].cmd` > `builtin`。

---

## 四、五阶段契约

| 阶段 | 必须做的事 | 产物 | 失败后果 |
|---|---|---|---|
| `fetch` | 取数（可顺带算完） | `data/` 下至少一个非空文件 | 中断，报警 |
| `check` | 校验产物 | `out/check.json` | 报警；`on_fail=stop` 时中断 |
| `render` | 出报告 | `out/report.*` | 中断，报警 |
| `emit` | 写指标模板并落库 | `out/latest.json` | 中断，报警 |
| `dispatch` | 调任务自己的推送 | 无 | 一般设 `optional`，只报警 |

**退出码**：`0` 成功；非 0 视为该阶段失败。框架整体退出码：`0` 全通过、`1` 阶段失败、`3` 检查不通过。

**注入给任务的环境变量**：`CASE_TASK`（任务 key）、`CASE_DATA_DIR`、`CASE_OUT_DIR`。

**框架保证**：阶段按声明顺序执行；每阶段耗时与退出码写进 `out/status.json`；
失败写进 `out/alerts.json`；日志写 `logs/run-<时间>.log`。

---

## 五、out/latest.json 格式（emit 的产物）

`emit` 必须写出这个文件，它是"任务 → 指标仓"的唯一接口：

```json
{
  "as_of": "2026-11-24",
  "points": [
    {"key": "tech.consensus", "sec": "000300.SH", "obs_date": "2026-11-24",
     "value_text": "平", "quality": "ok", "name": "沪深300 技术共识"},
    {"key": "re.discount_rate", "sec": "CN.REALESTATE", "obs_date": "2026-11-24",
     "value": 3.42, "dec": 2, "quality": "ok"}
  ]
}
```

`points[]` 字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✓ | 指标键，`点分层.名称`，如 `chanlun.bull_score` |
| `sec` | ✓ | 证券/对象 id：指数用 `000300.SH`，期货主连 `AU.SHF`，非证券用 `CN.MACRO` 这类 |
| `obs_date` | ✓ | 数据观测日 `YYYY-MM-DD`（不填时用 `as_of`） |
| `value` / `value_text` | 二者其一 | 数值指标用 `value`，枚举/文本用 `value_text` |
| `quality` | 建议 | `ok` / `missing` / `stale` / `estimated`；算不出来写 `missing`，**不补零、不填旧值** |
| `name` / `unit` / `dec` | 可选 | 展示名、单位、小数位 |

落库由 `case/_lib/publish.py` 完成：它把上面写法转成 DataHub 原生字段
（`metric_key` / `security_id` / `value_num` / `value_text` / `meta_json`）并调
`data_hub.tech.write_points(rows, producer="case/<key>")`。指标键未在 DataHub registry 登记时会
告警但照写，便于先跑起来再补登记。

---

## 六、任务独立性与依赖

**默认：任务之间没有任何运行时依赖。** 每个任务自己取数、自己算、自己出报告，不读别的任务的
`data/` 或 `out/`，也不要求别的任务先跑完。看板上的先后只由各自 `sched` 的时点决定，不是依赖关系。

- **共享的是代码，不是任务**：`report_stats` / `broad5_risk` / `portfolio_stats` 这类多任务复用的模块
  放 `case/_shared/`，框架启动时统一注入 `sys.path`，子进程也能拿到 `CASE_SHARED_DIR`。
- **共享的是数据源，不是产物**：需要同一份外部数据时，各自从数据源取，或由 `CASE_DATA_SRC` 注入，
  不通过"上游任务写、下游任务读"来传递。
- **禁止**：`../<别的任务>/out/` 这类跨任务读取。发现即视为契约违规。

确实存在必须读上游产物的例外，才写 `depends_on`，并在 issue 里说明理由。写法：`depends_on: ["option_skew"]`，
`fetch` 之前框架检查：

1. 上游任务目录存在；
2. 上游 `out/latest.json` 存在且 `points` 非空；
3. 上游 `as_of` 距今不超过本任务的 `freshness_days`；
4. 上游 `quality=ok` 的占比不低于 `min_ok_ratio`。

任一条不满足：写一条 `error` 级报警（说明是哪个上游、差在哪），然后**不进入 fetch**。
这样下游不会拿过期或缺失的数据硬算，报告也不会发出去。

跨仓库依赖同样适用——检查的是文件路径，不是代码位置。

---

## 七、可见性规则

| visibility | 定时跑 | 写指标仓 | 上牌面 | 发报告/邮件 |
|---|---|---|---|---|
| `public` + 有 `section` | ✓ | ✓ | ✓（按 section 归 Tab） | 由 `dispatch` 决定 |
| `private` | ✓ | ✓ | ✗ | 由 `dispatch` 决定 |

- `private` 任务的指标仍可被其他任务通过 `depends_on` 使用，只是不渲染。
- 面板只渲染 `visibility=public` 且 `section` 合法的任务；`private` 任务在模块管理页可见（带 private 标记），便于排障。
- **未知 visibility 不会被当成 public**：`validate_task` 记一条错误，调度器把该任务以「停用 + 配置错误」留在清单里、在调度台标出来，并在**首次发现时**发一封告警邮件（同一个错误只告一次，改好后自动清零）—— 任务可以跑失败，但不能从调度里悄悄消失。
- `internal` 是 `private` 的**旧别名**（过渡期兼容），不产生错误；等 `precious-task` 的 17 个 `task.json` 全部改成 `private` 后删掉别名。
- 想让私有任务的**部分数据**公开：把那个指标单独拆成一个小 `public` 任务，或在私有任务里再挂一个只 `emit` 该指标的 `public` 任务。不要整包公开。

---

## 八、最小示例

`case/_template/` 是一份可直接复制改名的最小任务：`task.json` + 五个几行的脚本，
跑 `python3 case/_lib/runner.py --task demo_task --dry-run` 就能看到阶段链路。

---

## 九、版本与兼容

- 本契约 v1，对应 `case/_schema/task.schema.json`。
- 新增字段一律可选、有默认值；**删改字段会升版本**并在 `CHANGELOG` 记一条。
- 私有仓库按本契约写的任务，框架侧字段不变就不用跟着改。
