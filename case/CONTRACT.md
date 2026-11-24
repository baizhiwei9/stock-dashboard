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
   └── 私有任务        visibility=internal → 定时跑 + 写指标仓 + 不上牌面
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
| `visibility` | `public` | `public`：上牌面；`internal`：只落库不展示 |
| `section` | 空 | `visibility=public` 时必填，取值见下；`internal` 时必须为空或不写 |
| `depends_on` | `[]` | 上游任务 key 列表；`fetch` 前检查上游产物是否够新，不满足则报警退出 |

`section` 取值：`stock` / `futures` / `realestate` / `macro` / `liquidity` / `bond` / `option` / `risk` / `modules`。

### 运行参数

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否参与调度 |
| `timeout` | `1800` | 单次运行超时秒数 |
| `requires` | `[]` | 外部依赖名，当前仅 `pylibs`（靠 `PYLIBS_DIR` 找） |
| `deliver` | — | `{"report":bool,"email":bool}`，仅声明与牌面标注，框架不代发 |
| `desc` | `""` | 一句话说明 |
| `queue` | `default` | 串行队列名。调度器按 `modules.json` 的 `queue.max_parallel`（默认 `1`＝全局串行）限并发；名额满时同组任务排队等空档，**到点遇忙顺延，不跳过也不并发** |
| `not_before` | 空 | `"HH:MM"`，数据最早就绪时刻。队列空档**提前拉取**时的下限：现在早于它就不提前跑；到 `sched.at` 仍照常执行。不确定数据何时就绪就配上 |

> 排队口径：到点但队列占满时不写 `last_slot`，每 15 秒重试，最长等到 `GRACE_SEC`（6 小时）；
> 超过才放弃，并在 `state.modules.<key>.note` 记一句「超过宽限未执行」。
> 调度器被 kill 后遗留的子进程（孤儿）同样占名额，等它自己结束才放行。
> 接力粒度约等于 `TICK_SEC`（15 秒），短任务会有这点等待。
>
> 提前跑（pull-forward）：`modules.json` 的 `queue.pull_forward` 打开时（默认开，16:00 后生效、
> 最多提前 `pull_ahead_min` 分钟），队列空着就把后面的任务提前拉进来串着跑，**`last_slot` 仍记
> 当天计划点**，到点不重复；`state.modules.<key>.pulled_forward` 标记这次是否提前执行。

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
     "value": 3.42, "dec": 2, "quality": "ok"},
    {"key": "tda_spd.market_state", "sec": "CN.STRATEGY",
     "value_text": "防御", "obs_date": "2026-11-24", "quality": "ok",
     "risk_level": "risk", "risk_note": "脆弱性 78%，仓位降至 20%"}
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
| `risk_level` / `risk_note` | 可选 | 任务侧判断的标签，**原样透传，publish 不重算阈值**。`risk_level` 只允许 `none` / `opportunity` / `risk` / `alert`，兼容 `warning`→`risk`、`严重`→`alert`、`机会`→`opportunity`；其余取值直接校验失败，避免标签被静默丢掉。任务没有判断就不传 |

落库由 `case/_lib/publish.py` 完成：它把上面写法转成 DataHub 原生字段
（`metric_key` / `security_id` / `value_num` / `value_text` / `quality` /
`meta_json` / `risk_level` / `risk_note`）并调
`data_hub.tech.write_points(rows, producer="case/<key>")`。指标键未在 DataHub registry 登记时会
告警但照写，便于先跑起来再补登记。

---

## 六、依赖与新鲜度

`depends_on: ["option_skew"]` 表示本任务要用 `option_skew` 的产出。`fetch` 之前框架检查：

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
| `internal` | ✓ | ✓ | ✗ | 由 `dispatch` 决定 |

- `internal` 任务的指标仍可被其他任务通过 `depends_on` 使用，只是不渲染。
- 面板只渲染 `visibility=public` 且 `section` 合法的任务；`internal` 任务在模块管理页可见（带 internal 标记），便于排障。
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