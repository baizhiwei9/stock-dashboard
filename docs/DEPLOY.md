# 部署手册（旧版 · 架构与队列说明仍有效）

> ⚠️ **敏感配置的录入方式已变，以根目录 [DEPLOY.md](../DEPLOY.md) §3 为准。**
> 本文第 5 节「首次配置」里让手改 `modules.json` 的做法已废弃：管理密码与注册密钥
> 现在统一走 `~/.config/stock-dashboard/secrets.json`，由 `python3 setup.py` 强制录入
> （弱口令直接拒收），`modules.json` 本身也已改为本地安装配置、不再入库。
> 本文保留的价值在第 0 节的架构说明、第 6–9 节的启动 / 排障 / 队列规则。

面向把 stock-dashboard 部署到一台新机器的人。按顺序做完第 1–6 节即可跑起来，
第 7 节是让它在没人管的时候也不会静默停摆，第 9 节是出事时先看哪里。

---

## 0. 一句话架构

- **看板本体**（`build.py`）只读 DataHub 的 `data_hub.tech` 指标仓生成 `index.html`，不直连数据源。
- **所有任务**在 `case/<task>/` 里（五阶段：取数 → 检查 → 出报告 → 出指标 → 分发），由 `scheduler.py` 按 `task.json` 定时驱动。
- **`scheduler.py` 是唯一的调度器**，也是唯一需要常驻的进程；它同时提供 HTTP 控制台（默认 `:8767`）。

---

## 1. 前置条件

| 项 | 要求 | 说明 |
|---|---|---|
| Python | **3.11+**（实测 3.12 / 3.13） | `scheduler.py`、`case/_lib/*` **纯标准库**；`build.py` 只额外依赖 `data_hub` 仓库（不是 pip 包）。**按 3.11 基线写，别在 3.11 上跑 3.12 语法**：f-string 内嵌同类型引号在 3.12+ 才合法，降级会 import 阶段直接 SyntaxError |
| 第三方包 | 见 `requirements.txt` | 只有任务脚本要用（pandas / matplotlib / akshare / lightgbm / POT 等） |
| DataHub 仓库 | `datahub-multi-tier` | 需与本站同级 checkout；`PYTHONPATH` 指到含 `data_hub/` 的仓库根 |
| 时区 | `Asia/Shanghai` | 调度按本地时间算，机器时区错了所有任务都会错点 |
| 出网 | 视任务而定 | 取数类任务要访问公开行情接口；纯离线环境只能跑不取数的任务 |

```bash
# 时区自检（必须输出 CST +0800）
date
```

---

## 2. 目录布局

推荐同级放置，`PYTHONPATH` 一次指好：

```
/srv/monitor/
├── stock-dashboard/          # 本仓库
│   ├── scheduler.py          # 调度器 + HTTP 控制台（唯一常驻进程）
│   ├── sd_watchdog.sh        # 看门狗（由 cron 每分钟调用，见第 7 节）
│   ├── build.py              # 渲染 index.html
│   ├── install.sh            # 安装门禁：补齐并校验敏感配置
│   ├── case/_lib/secrets.example.json  # 敏感配置模板（唯一真源，值不入库）
│   │                         # 真实值在 ~/.config/stock-dashboard/secrets.json（仓外，600）
│   ├── case/                 # 任务目录（public 任务）
│   ├── docs/DEPLOY.md        # 本文
│   ├── logs/                 # 运行时生成，不入库
│   ├── state.json            # 运行时生成，不入库
│   └── scheduler.pid         # 运行时生成，不入库
└── datahub-multi-tier/       # DataHub，提供 data_hub.tech
    └── data_hub/
```

私有任务放在**另一个私有仓库**里（如 `precious-task`），通过 `CASE_EXTRA_DIRS` 挂载，
不要复制框架代码进私有仓库。

---

## 3. 装依赖

```bash
cd /srv/monitor/stock-dashboard
python3 -m venv .venv && . .venv/bin/activate      # 或者在既有环境里装
python3 -m pip install -r requirements.txt
```

`requirements.txt` 是全量快照（75 个包），只为「干净机器一次装齐」；实际每个任务只用到子集。
如果目标机已有 Python 依赖目录而**不想装**，把 `PYLIBS_DIR` 指向它即可，但任务命令里写的是
`${PYLIBS_DIR:?}`，**没设会直接报错**，不会去猜路径。

装完依赖先别启动，第 5 节会把敏感配置补齐；**没补齐不允许安装/启动**。

---

## 4. 环境变量

写进 systemd unit、`/etc/environment` 或启动脚本的 `export` 里，一次配好。

```bash
export TZ=Asia/Shanghai
export DATA_HUB_PATH=/srv/monitor/datahub-multi-tier     # 可选：显式指定 DataHub 根（所有入口共用）
export PYTHONPATH=/srv/monitor/datahub-multi-tier:$PWD   # 含 data_hub/ 的仓库根
export PYLIBS_DIR=/srv/monitor/pylibs                    # 任务脚本的第三方包目录（可选）
export CASE_EXTRA_DIRS=/srv/private-tasks/case           # 私有任务根目录（可选，冒号分隔）
export CASE_DATA_SRC=/srv/data/weekly                    # 上游输入数据（部分任务需要）
export CASE_CJK_FONT=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc   # 可选
export CASE_ALERT_WEBHOOK=                               # 可选：任务报警外发（飞书/钉钉机器人）
export SD_ALERT_WEBHOOK=                                 # 可选：调度器拉不起来时的告警
```

| 变量 | 必需性 | 用途 |
|---|---|---|
| `PYTHONPATH` | 依赖 DataHub 的任务必需 | 取数、`publish.py` 落库 |
| `PYLIBS_DIR` | 用 `pylibs` 的任务必需 | 命令里 `${PYLIBS_DIR:?}` 强校验 |
| `EDB_REPO` | `margin_flow` / `margin_detail` 等 | 指到含 `data_hub` 的 EDB 仓库目录 |
| `CASE_DATA_SRC` | 周/月频任务 | `fetch` 阶段把上游数据复制进来 |
| `CASE_EXTRA_DIRS` | 私有任务 | 额外任务根，`case/` 优先，key 冲突记 stderr |
| `CASE_CJK_FONT` | 可选 | 覆盖字体自动查找，避免图里豆腐块 |
| `CASE_ALERT_WEBHOOK` | 可选 | 任务级报警（写 `out/alerts.json` 之外再外发） |
| `CASE_NO_PUBLISH` | 可选 | 置 1 只写 `out/latest.json` 不落库，离线调试用 |
| `SD_PYTHON` | 可选 | 看门狗用哪个解释器（默认 `python3`，venv 里要指过去） |
| `SD_ALERT_WEBHOOK` | 可选 | 调度器连续拉不起来时的告警地址 |
| `SD_ALERT_AT` | 可选 | 连续失败几次才告警（默认 3） |
| `COZE_SECRETS` | 可选 | 显式指定敏感配置文件路径（默认 `~/.config/stock-dashboard/secrets.json`） |
| `COZE_SEC_<KEY>` | 可选 | 单项覆盖配置，如 `COZE_SEC_TUSHARE_TOKEN`；优先级高于配置文件 |

---

## 5. 首次配置

> 本节已随敏感配置收口改写。旧的「手改 modules.json 里的 password」做法作废——
> 现在密码不在任何仓库文件里。

**所有敏感数据只放一个文件**：默认 `~/.config/stock-dashboard/secrets.json`
（权限 0600，仓外，不随仓库外泄；想放别处就 `export COZE_SECRETS=/path/to/secrets.json`）。
仓库里只有占位模板 `case/_lib/secrets.example.json`。字段清单见
[docs/SECRETS.md](SECRETS.md)。

```bash
# ① 复制本机模块清单（modules.json 是本机安装数据，不入库）
cp modules.example.json modules.json
#    然后把里面的 {{DATAHUB_ROOT}} / {{WORKSPACE_A}} / {{WORKSPACE_B}} /
#    {{FEISHU_CLI_DIR}} / {{FEISHU_CHAT_ID}} / {{ALERT_EMAIL}} 换成这台机器的真实值
#    （文件末尾 _placeholders 有对照表）

# ② 录入敏感配置（飞书凭据 + 管理密码 + 注册密钥，缺失或弱口令会中断安装）
python3 setup.py
#   有终端时这是交互引导，缺项逐条问、弱口令直接拒收。
#   门禁口径（缺一不装，与根 DEPLOY.md §3.0 一致）：
#     feishu.app_id / app_secret / webhook  必填，默认联网校验（--skip-live 可跳）
#     api.password                          必填，≥12 位、含两类字符、非弱口令
#                                           （HTTP 控制台 / X-SD-PW）
#     api.registry_key                      必填，且不要与管理密码相同（Worker 注册 / X-SD-KEY）
#     treasury.* / tushare.* / email.* / fred.api_key / alert.webhook
#                                           选填，缺了对应通道降级，不影响启动

#    非交互部署（CI / 自动化装机，没有 TTY）：用环境变量喂值，--init 会落盘
export COZE_SEC_API_PASSWORD='<至少12位，含两类字符>'
export COZE_SEC_API_REGISTRY_KEY='<至少16位>'
export COZE_SEC_FEISHU_APP_ID='cli_xxx'
export COZE_SEC_FEISHU_APP_SECRET='xxx'
export COZE_SEC_FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/xxx'
python3 setup.py --init            # 只落盘 + 校验，不交互；离线加 --skip-live

# ③ 校验（退出码 0 才算过，不打印任何值）
python3 setup.py --check
#   ./install.sh 内部走的就是 ② 的 --init + ③ 的 --check，两个都过才放行。
#   不要用 case/_tools/check_secrets.py 当安装门禁：它只判 api.password /
#   api.registry_key（REQUIRED_KEYS），飞书在 OPTIONAL_KEYS 里，只过它会出现
#   「安装通过但所有推送必失败」——最贵的一种失败。check_secrets 的定位是
#   日常排查工具（--paths 看 6 级查找链、不带参看当前生效值）。

# ④ 确认端口没被占用（默认 8767）
ss -lntp | grep 8767 || echo "8767 空闲"

# ⑤ 任务清单核对：能列出所有 key 就说明 modules.json + case/*/task.json 都读到了
python3 scheduler.py status
```

- 飞书 / Tushare / 邮件 / FRED 等通道按需填写；不填不会阻塞启动，只会在该通道
  实际使用时明确报错。字段说明见 [`config/README.md`](../config/README.md)。
- `scheduler.py` 的 `start / fg / serve` 启动前会再做一次同样的校验；缺必填项
  直接拒绝启动，不会以默认口令裸奔。
- 管理口令与注册密钥**不再写进 `modules.json`，也不再内嵌到 `index.html`**；
  看板管理面板审批外部模块时现场输入密码，由同源调度器校验。

`status` 会打印每个模块的「下次时间 / 状态 / 上次耗时 / 数据域」。私有任务挂在
`CASE_EXTRA_DIRS` 下时也会一起列出来。

---

## 6. 启动

**一条命令起全部**（公有 `case/` + 私有 `CASE_EXTRA_DIRS` 同一个进程，按各自
`task.json` 的 `sched` 时间跑，不需要 crontab）：

```bash
./start_all.sh up          # TaskManager + 常驻看门狗
./start_all.sh status      # 全部任务（公有+私有）+ 看门狗/心跳
./start_all.sh down        # 停
./start_all.sh restart     # 重启
./start_all.sh doctor      # 只体检：路径 / 凭据 / 任务分布 / 公私归属失配
```

`up` 是幂等的：已在跑就只补齐没起的部分。第一次会按需生成 `modules.json`
（空 `modules`，任务全部来自 `case/` + `CASE_EXTRA_DIRS`），并做凭据门禁
（缺项直接拒绝启动，先跑 `./install.sh`）。

只想手工看调度器输出时：

```bash
./start_all.sh fg                 # 前台跑，Ctrl-C 退出
# 等价于
python3 scheduler.py fg

python3 scheduler.py status        # 看状态表
python3 scheduler.py health        # 0=健康 / 1=未运行 / 2=僵死
```

- 控制台：`http://<host>:8767`（端口读 `modules.json` 的 `api.port`）。
- `start` 是幂等的：已经在跑就直接返回，不会起第二个实例（先查 `scheduler.pid`，再校验进程命令行，再用端口探活兜底）。
- 首次启动会创建 `state.json` 与 `logs/`，两者都在 `.gitignore` 里。

先手动跑一个任务验证链路，再等它自己定时跑：

```bash
python3 scheduler.py run tech_consensus              # 立刻跑一次（走 runner 五阶段）
python3 case/_lib/runner.py --task tech_consensus --dry-run   # 只看阶段链路，不执行
```

---

## 7. 守护（常驻看门狗，不需要 cron）

`scheduler.py` 是**单点常驻进程**：它被 kill 掉，所有任务都不会再被触发。
`start_all.sh up` 会同时拉起 `sd_watchdog.sh --loop`，按心跳巡检并在进程
未运行/僵死时拉起或重启——**业务任务与守护都不写 crontab**。

```bash
./start_all.sh up            # 同时起 TaskManager 与看门狗
./start_all.sh status        # 看门狗 pid + 调度心跳
tail -20 logs/watchdog.log   # 看门狗自己的记录（健康时不写）
```

退出/重启用 `./start_all.sh down|restart`，不要只 kill 其中一个：看门狗会把
它拉回来。

> 老部署如果已经有一条 cron 看着（`* * * * * .../sd_watchdog.sh`），它仍然可用，
> 与 `--loop` 同时存在也只会有一个真正去拉起 scheduler（`start` 幂等）。
> 迁移到新机器时**不要**再装这条 cron，统一交给 `start_all.sh`。

看门狗按**心跳**判定，不看 pid：

| `scheduler.py health` | 含义 | 看门狗动作 |
|---|---|---|
| `0` | 心跳新鲜（180 秒内更新过） | 只更新 `logs/watchdog.hb`，静默 |
| `1` | 未运行 | `scheduler.py start` |
| `2` | 僵死：进程在，但心跳超 180 秒没更新 | `scheduler.py restart`（`start` 会因「已在运行」空转） |

拉起/重启后会再查一次；连续 `SD_ALERT_AT`（默认 3）次失败才发 `SD_ALERT_WEBHOOK`，
告警后清零计数，避免每分钟刷屏。

告警通道按这个顺序找，前一个为空才看后一个：

1. 环境变量 `SD_ALERT_WEBHOOK`（写在 cron 行里最省事）
2. 凭据文件的 `alert.webhook`（`~/.config/stock-dashboard/secrets.json`）
3. 都没有 → 只写 `logs/watchdog.log`，不外发（日志里会留一行「未配置…」）

飞书和钉钉的自定义机器人都能用：URL 含 `feishu` / `larksuite` / `/open-apis/bot`
按飞书格式（`msg_type`）发，其余按钉钉格式（`msgtype`）发。

```cron
# 直接带 webhook；SD_ALERT_AT 默认 3，可以调
* * * * * SD_ALERT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx /srv/monitor/stock-dashboard/sd_watchdog.sh >/dev/null 2>&1
```

手动验判定链路（不用等真坏）：

```bash
python3 scheduler.py health     # 0 健康 / 1 未运行 / 2 僵死
```

想确认 `2` 这条路真的会走，把 `logs/scheduler.hb` 的时间戳往前改 10 分钟，
`health` 应立刻返回 2；改回来即可（下一轮 tick 会自己覆盖）。

看门狗还会做一次**环境自举**：cron 的 env 极简，`PYTHONPATH` 一般空着，而 scheduler
进程内的 `task_health` 上报要 `import data_hub.tech.health`。脚本会按部署约定
（datahub 与 stock-dashboard 同级 checkout）自己补上；已经用 systemd / 第 4 节配过
环境变量的话完全不覆盖。

看门狗自己的落盘都在 `logs/`：

| 文件 | 含义 |
|---|---|
| `watchdog.log` | 拉起 / 重启 / 失败记录（健康时不写，保持安静） |
| `watchdog.hb` | 最后一次成功巡检的 epoch 秒 —— **cron 这行还在不在，看这个文件的时间** |
| `watchdog.fail` | 连续拉起失败计数，成功即清零 |

```bash
# 确认看门狗真的在跑（时间应该是 1 分钟内）
ls -l --time-style=full-iso logs/watchdog.hb
```

**两个容易踩的口径：**

- **漏跑窗口 = 6 小时**（`GRACE_SEC`）。被 kill 后一分钟内被拉起，任务照常补跑；停摆超过 6 小时
  才会静默跳过（日志里能看到该 slot 未执行）。
- **半途被杀**：子进程句柄只在内存里，新进程收不回来，`state.json` 会留下 `running=true`。
  启动时会统一标记 `interrupted` 并写 `scheduler.recovered_tasks`，不再假装「跑完了」。
  默认**不自动重跑**（报告类任务重跑会重复推送飞书/邮件）；确认任务幂等后，把 `state.json`
  里的 `scheduler.auto_retry_interrupted` 置 `true`，启动时会清空该任务 `last_slot`，
  在 6 小时宽限内补跑一次。

### 7.1 排队与串行（编排）

一次只跑一个任务，其余排队等空档——这样盘后同一段时间到点的几个任务不会并发去抢行情接口。

```jsonc
// modules.json 顶层
"queue": {
  "max_parallel": 1,            // 默认 1＝全局串行
  "groups": { "fetch": 2 },     // 可选：给某组单独放开并发
  "pull_forward": true,         // 队列空着就把后面的任务提前拉进来串着跑
  "pull_forward_from": "16:00", // 只在这个时刻之后允许提前（早间任务不受影响）
  "pull_ahead_min": 240         // 最多提前 4 小时，防止把深夜任务拉到下午
}
```

| 行为 | 说明 |
|---|---|
| 到点遇忙 | 不跳过、不并发：排队任务不写 `last_slot`，每 15 秒重试，轮到自己才起 |
| 顺延上限 | `GRACE_SEC` 6 小时；超过后在 `state.modules.<key>.note` 记「超过宽限未执行」并只在日志写一次 |
| 接力粒度 | 约 `TICK_SEC`（15 秒）：前一个结束到下一个起跑最多隔 15 秒 |
| 排序 | 按计划点先后，同点按 `modules.json` / `case/` 的配置顺序 |
| 排队可见 | `scheduler.py status` 状态列「排队」；`state.scheduler.queue` = `{max_parallel, used, orphans}` |
| 分组 | 任务写 `queue`（`task.json` 或 `modules.json`）；组上限取 `queue.groups.<组名>`，缺省随全局 |
| 孤儿占位 | 调度器被 kill 后子进程仍存活 → 登记 `orphans` 占名额，等它结束才放行 |
| 空档提前跑 | 16:00 后队列空着时，把后面的任务提前拉进来（最多 `pull_ahead_min`）；任务可用 `not_before` 声明数据就绪下限 |
| 提前跑去重 | 仍以**当天计划点**写 `last_slot`，到点不重复；`state.modules.<key>.pulled_forward=true`，`status` 显示「成功(提前)」 |

```bash
# 看队列现状（有任务在跑/排队时）
python3 scheduler.py status | head -3
python3 -c "import json;s=json.load(open('state.json'));print(s['scheduler']['queue'])"
```

要提速就调 `max_parallel` 或按组拆开，别把队列关掉——关掉等于回到并发抢接口的老样子。

---

## 8. 私有任务挂载

私有任务放独立私有仓库，只按 `case/CONTRACT.md` 写 `task.json` + 脚本，不复制框架。
标准布局是**与 stock-dashboard 同级**的 `precious-task/`，`start_all.sh` 会自动挂载：

```bash
# 标准布局：../precious-task
./start_all.sh up                  # 自动 export CASE_EXTRA_DIRS=<同级>/precious-task/case

# 放在别处时显式指定
export SD_PRECIOUS_DIR=/srv/private-tasks
./start_all.sh up

# 也可以完全手工（不经过 start_all）
export CASE_EXTRA_DIRS=/srv/private-tasks/case
python3 scheduler.py status                       # 私有任务与 case/ 一起排期
python3 case/_lib/runner.py --task <key> --dry-run
```

`./start_all.sh doctor` 会打印「公有 N 条 / 私有 M 条」；私有仓找不到时 `up`
默认拒绝启动（宁可报错，也不让私有任务静默消失），只跑公有任务用
`SD_ALLOW_NO_PRECIOUS=1`。

- 扫描顺序：先 `case/`，再 `CASE_EXTRA_DIRS`；同一个 key 重复时**先命中的生效**，后出现的记一条冲突（stderr）。
- 私有任务 `task.json` 的 `visibility` 写 `private`（历史写法 `internal` 等价）：照常定时跑、照常写指标仓，
  出页时只上私有页 `private.html`，公开页 `index.html` 不认识它们；没有 `section` 时按 `section_domain` 归板块。
- 私有页单独构建一次即可（与公开页同一环境、同一 PYTHONPATH）：
  `python3 build_private.py --out /srv/monitor/private.html`。
- 私有页会把任务产出的**图**一起带上：扫 `<task>/out|charts|runs|output|figures|reports|assets` 里最近的图
  （默认每个任务 6 张，`--max-images` 可调），原图拷到**页面同目录** `assets/private/<key>/`，
  另生成最长边 900 的缩略图放 `assets/private/<key>/thumb/`；页面先给缩略网格，点开是灯箱原图
  （滚轮缩放、拖动、1:1、Esc 关闭）。所以 web 根目录要能访问到 `assets/`，与 `private.html` 同级即可。
  不想带图或只想出指标：`python3 build_private.py --no-images`；图片放到别处用 `--assets /path/to/assets`。
- 私有任务要出数值，需要它在本机跑过并由 `publish` 回填（`out/latest.json` 或指标仓有点）；
  还没跑过的先显示任务卡，属正常状态。

---

## 9. 故障排查

先看三件事，八成问题在这里：

```bash
python3 scheduler.py health                 # 进程与心跳
tail -50 logs/scheduler.log                 # 调度器自己在说什么
tail -30 logs/<key>.log                     # 具体任务在说什么
```

| 症状 | 先判断 | 处置 |
|---|---|---|
| 控制台 `:8767` 打不开 | 进程没起 / 端口被占 | `health` 看退出码；`ss -lntp \| grep 8767` |
| 到点没跑 | `enabled=false` / 超 6 小时宽限 / 上次被中断 | `status` 看 `next_run` 与 `last_status`；必要时 `on <key>` |
| `status` 里一直显示 `running` | 上一次调度器被杀留下的脏状态 | 重启一次会自动标 `interrupted`；然后 `run <key>` 手动补 |
| `watchdog.log` 每分钟一条失败 | 拉不起来 | `health` + `scheduler.log` 尾部；多半是 `PYTHONPATH`/依赖/权限 |
| `watchdog.hb` 时间不动 | 看门狗没在跑 | `./start_all.sh status`；`./start_all.sh up` 重起 |
| `status` 里看不到私有任务 | 没挂上私有仓 | `./start_all.sh doctor` 看 precious 路径；设 `SD_PRECIOUS_DIR` |
| 报告生成了但没发出去 | `dispatch` 失败或检查不过 | 看 `case/<key>/out/status.json` 与 `out/alerts.json` |
| 指标没上面板 | `visibility=internal` 或落库失败 | 看 `out/latest.json`；`CASE_NO_PUBLISH=1` 时本就不落库 |
| 图里中文变方块 | 字体缺失 | 设 `CASE_CJK_FONT`；`assets.ensure_cjk_font()` 会明确报错，不画豆腐块 |
| 任务 import 报错 | 子包漏带 | `python3 case/_tools/check_imports.py`，再 `fix_imports.py` 补回 |
| 任务拿不到上游数据 | 输入没注入 | 设 `CASE_DATA_SRC`，或先跑上游任务 |

---

## 10. 升级与回滚

```bash
cd /srv/monitor/stock-dashboard
git pull
python3 -m pip install -r requirements.txt      # 依赖有变动时
python3 scheduler.py restart                    # 重启让新代码生效
python3 scheduler.py status                     # 确认模块还在
```

- 升级只影响下一次 tick；正在跑的子进程不会被中断（`restart` 会 `stop` 现有调度器，
  子进程随进程组结束——**建议避开整点任务窗口再重启**）。
- 回滚：`git checkout <上一个 tag/commit>` + `scheduler.py restart`。
- `state.json` 是运行状态，不要跟着回滚；它只记录 `last_slot / last_status` 这类事实。

---

## 11. 一次部署的自检清单

- [ ] `date` 是 CST +0800
- [ ] `./install.sh` 通过；`~/.config/stock-dashboard/secrets.json` 存在且权限为 `600`
- [ ] `python3 case/_tools/check_secrets.py` 返回 0
- [ ] `index.html` 里搜不到明文管理密码 / 注册密钥
- [ ] `python3 scheduler.py status` 能列出全部 key（含私有任务）
- [ ] `python3 scheduler.py health` 返回 `0`
- [ ] `curl -s http://127.0.0.1:8767 | head` 有响应
- [ ] `logs/watchdog.hb` 在 1 分钟内更新
- [ ] 手动跑通一个任务：`scheduler.py run <key>`，`out/status.json` 全绿
- [ ] `python3 build.py` 能生成 `index.html`
- [ ] `index.html` 里目标 Tab 有数据
---

## 12. 日更 / 周更任务从 crontab 收拢进 case/（本次迁移）

### 12.1 边界：什么进 case/，什么留在原地

| 类别 | 判定 | 处置 |
|---|---|---|
| **日更 / 周更** | 每交易日一次、每周一次、每月一次 | ✅ 收进 `case/`，由 `scheduler.py` 排期 |
| **一天多次** | 15 分钟 / 1 小时 / 盘中多点采样（如 09:45+15:15、10:15/11:35/14:15/15:10） | ❌ 不动，继续走原 crontab、原数据库 |
| **管理类** | 策略监控启停这类开关脚本 | ❌ 不动 |
| **消耗大模型** | 实际调 LLM 接口（如 `bond_four_factor` 打 DeepSeek） | ❌ 不进本框架，代码留库但不切 |
| **已废弃** | crontab 里被 `#` 注释掉的 | ❌ 不迁 |

已收进 `case/` 的 21 条日更/周更任务明细（key、crontab 原名、频率、入口、文件数、体积）
见 [`docs/DAILY_TASK_MIGRATION.md`](DAILY_TASK_MIGRATION.md)。

切换清单是 **20 个 key / 21 条 cron**：

- `bond_four_factor`（国债期货四因子）**被剔出** —— 它实际调 LLM（`main.py:31` → `llm_utils.py:50`
  打 `api.deepseek.com`），按「消耗大模型的不用管」继续走原 crontab。
- `risk_forecast_daily` **一个 key 对两条 cron**：`风险预测·午间` 12:30 + `风险预测·日终` 20:50，
  **必须一起切** —— 只删一条会让同一脚本一条走 scheduler、一条走 crontab，两套入口并存。

> **重要：这 21 个 `task.json` 的 `enabled` 现在全部是 `false`。**
> 代码进库不等于接管。不切 crontab 就永远不会双跑，也不会停摆。
> `--apply` 只把切换清单里的 **20 个**置 `true`，`bond_four_factor` 全程保持 `false`。

### 12.2 为什么不能「开着 enabled 又留着 crontab」

`crontab` 和 `scheduler.py` 是两套独立的触发器。同一个任务两边都开，会在同一分钟各起一个进程：

- 同时写 `out/latest.json` 和指标仓 → 后写的覆盖先写的，指标可能抖动
- 同时发飞书/邮件 → 重复推送
- 同时抢上游数据文件 → 偶发读到半截文件

反过来只删 crontab 不启 `enabled`，任务会直接停摆。
所以切换必须**原子**：开 `enabled` 和删 crontab 行在同一次执行内完成。

### 12.3 切换步骤（默认 dry-run，不动生产）

```bash
cd /srv/monitor/stock-dashboard

# ① 预演：只打印会删哪些 crontab 行、会把哪些任务设为 enabled=true
bash scripts/switch_crontab.sh

# ② 确认预演输出里是预期的 21 条（20 个 key，`风险预测·午间`+`风险预测·日终` 算两条），再真正执行
#    会自动备份 crontab 到 logs/crontab.<时间戳>.bak（仓库内 logs/，不是 /tmp）
bash scripts/switch_crontab.sh --apply

# ③ 验证：crontab 少 21 行、scheduler status 里切换清单的 20 个 key enabled=true
crontab -l | grep -c health_run
python3 scheduler.py status | grep -E 'midline_preference|sector_screening_v4'

# ④ 单个任务先手动跑一遍，确认输出正常再等它自己到点
python3 scheduler.py run sector_screening_v4

# ⑤ 出问题一键回滚：恢复 crontab 备份 + 把 enabled 改回 false
bash scripts/switch_crontab.sh --revert
```

**`--apply` 自带前置检查，不过就 `exit 3` 中止、一行 crontab 都不动**：

1. 切换清单的 20 个 key，在**当前目录**的 `case/<key>/task.json` 必须都存在。
   在错的目录跑（比如只有十几个旧 case 的运行目录）会在这里被拦下 ——
   否则 crontab 删了、scheduler 那边没任务，是**静默停摆**，不报错、不告警，第二天才发现没数据。
2. 一个 key 对多条 cron 的（`risk_forecast_daily` → 12:30 + 20:50），
   `task.json` 的 `sched.at` 必须覆盖全部时间点。少一个，切完那条就永远不跑。

dry-run 跑同一套检查，但只警告不中止，方便先看清缺什么。
所以正确姿势是：**先在目标目录 `bash scripts/switch_crontab.sh` 看 preflight 全绿，再 `--apply`**。

> 上面用 `/srv/monitor/stock-dashboard` 举例，那是部署规范位置。
> 实际执行前先确认自己就在 scheduler 的工作目录里：
> ```bash
> readlink /proc/$(pgrep -f 'scheduler.py' | head -1)/cwd
> ```
> 不在的话，preflight 会因为你这边缺 case 而中止 —— 这是保护，不是故障。

`--revert` 做三件事（顺序固定）：

1. 先把**当前** crontab 另存一份 `logs/crontab.<时间戳>.bak`（回滚动作本身也可再回滚）；
2. 把切换清单里的 **20 个** `task.json` 的 `enabled` 改回 `false`；
3. 自动 `crontab <上一次 --apply 留下的备份>` —— **不需要手工恢复**。

只有在 `logs/` 里找不到更早的备份（`--apply` 从没跑过、或 `logs/` 被清过）时，
脚本才打印「找不到更早的备份，请手工恢复」，这时才需要自己 `crontab <某个 .bak>`。

### 12.4 切换前的环境前提

这 21 个任务原先在别的目录、别的工作副本里跑，迁进来后有三处必须对上：

1. **`PYTHONPATH` 要能 import 到 `data_hub`**
   ```bash
   echo "$PYTHONPATH" | tr ':' '\n' | grep datahub     # 应命中本机 datahub-multi-tier
   python3 -c "import data_hub; print(data_hub.__file__)"   # 路径必须在本机，不是云盘副本
   ```
   云盘旧副本（`/opt/coze/.fuse_drive/dataHub_三级体系`）已废弃、不再作为兜底：
   解析不到就直接报错，不会再「静默连错库」。

2. **数据根：让 `python3 datahub_path.py` 落在本机 `datahub-multi-tier`**
   ```bash
   python3 datahub_path.py                    # 打印实际生效的 DataHub 根，换机器只看这一条
   ```
   解析顺序：`DATA_HUB_PATH` → `DATAHUB_DIR` → `EDB_REPO` → 同级 `../datahub-multi-tier`。
   前三个只有「目录存在且含 `data_hub/`」才会被采用，否则直接跳过（跳过是对的：
   选错了后面 import 也必然失败）。都没有时**报错退出**，不再退云盘旧副本。
   `DATAHUB_ROOT` **不是**主路径变量 —— 它只在 `modules.json` 的 `cwd` / `cmd` 里当
   `{{DATAHUB_ROOT}}` 占位符用，另外 `option_risk_cn_us` / `rate_board` 两个老脚本会直接
   `os.environ` 读它。别指望设了 `DATAHUB_ROOT` 就能改数据根。
   打印为空或直接报错，说明前几条一个都没命中 —— 这时**先别切**，
   把 `DATA_HUB_PATH` 指到本机副本再验一次。

3. **依赖闭包与凭据**
   ```bash
   python3 case/_tools/check_imports.py       # 依赖闭包是否完整
   python3 case/_tools/check_secrets.py       # 凭据是否齐全（飞书 / tushare / email）
   ```
   `check_imports.py` 报 `data_hub.*` / `commodities_strategies.*` / `calc.*` 未解析时，
   先确认是 `PYTHONPATH` 没带全，不是文件缺失；`report_stats` 这类共享模块在 `case/_shared/`。

### 12.5 切换后的自检清单

- [ ] `--apply` 的 preflight 输出是「20 个 task.json 齐全，多槽任务时间点对齐」（不是被 exit 3 拦下）
- [ ] `crontab -l | grep -c health_run` 比切换前少 21
- [ ] `python3 scheduler.py status` 里切换清单的 20 个 key 全部 `enabled=true`（`bond_four_factor` 仍是 `false`），`next_run` 是预期时间
- [ ] 抽 3 个任务 `scheduler.py run <key>`，`out/status.json` 全绿、指标有数
- [ ] 观察 1 个完整交易日：`logs/<key>.log` 无重复执行记录，飞书/邮件无重复推送
- [ ] 指标仓数值与切换前同一天的值一致（口径没跑偏）
- [ ] 第二天回头确认 **12:30 的风险预测·午间** 有跑（它是唯一一个双时间点任务，最容易漏）

### 12.6 凭据：不要写死在脚本里

这批任务迁进来时踩过一次：`case/bond_four_factor/llm_utils.py` 曾写死明文 DeepSeek API key，
并随代码进了 git 历史。现在的口径是——

```bash
# 落盘统一走 app_secrets.set_value（原子写 + 0600），脚本里只写 get()，不写值
PYTHONPATH=case/_lib python3 -c "import app_secrets; print(app_secrets.set_value('deepseek.api_key', '<新key>'))"

# 确认读得到：只打印「已配置 / 未配置」，不回显值
PYTHONPATH=case/_lib python3 -c "import app_secrets; print('已配置' if app_secrets.get('deepseek.api_key','') else '未配置')"

# 配置文件在哪、权限对不对；门禁键（api.password / 飞书 / tushare …）齐不齐
python3 case/_tools/check_secrets.py --paths
python3 case/_lib/app_secrets.py --check
```

> 两个坑：`app_secrets.py` 的命令行只有 `--check` / `--paths` / `--init` / `--env`，
> **没有** `set` / `check` 这样的子命令（写错会直接 `未知参数` 退出 2）。
> `deepseek.api_key` 也不在门禁清单（`REQUIRED_KEYS` / `OPTIONAL_KEYS`）里，
> 所以 `--check` / `--init` 都不会提到它 —— 只能像上面那样用 `set_value()` 写、用 `get()` 验。
> 另外值若是 `<新key>` / `{xxx}` 这类尖括号大括号包着的占位写法，`get()` 会当成**未配置**，
> 写完记得跑第二条确认打印的是「已配置」。

脚本侧读法（`DEEPSEEK_API_KEY` 环境变量优先，回退 secrets.json）：

```python
import app_secrets
KEY = os.environ.get("DEEPSEEK_API_KEY") or app_secrets.get("deepseek.api_key", "")
```

**key 一旦进过 git 历史就必须轮换**，删 commit 不算修好——历史还在。
另外 `.gitignore` 已加 `case/*/.tmp/`：任务跑挂了留下的截断日志里常有完整环境变量。

### 12.7 不在切换清单、但代码也还没入库的 6 条（`options/` 从未被跟踪）

`datahub-multi-tier/options/` 下有 6 条日更脚本：`pcr_macd.sh`、`skew10.sh`、`oi_daily.sh`、
`basis_daily.sh`、`close.sh`、`midday.sh`。它们**不在 §12.1 的切换清单（20 key / 21 cron）里**，
所以本次 `--apply` 不会动它们。

但要知道一个事实：`git ls-files options/` = **0**，`git status` 是 `?? options/` ——
这个目录从来没被 `datahub-multi-tier` 跟踪过，`sync_github.sh` 也没带上它。
**所以别把这些任务当成「代码已经在库里了」。** 将来要把它们也纳入 scheduler 时，先做：

```bash
cd datahub-multi-tier
git add options/ && git commit -m "chore(options): 期权日更脚本入库" && git push
```

否则 `--apply` 一跑，crontab 删了、scheduler 那边没有代码，就是停摆。

### 12.8 三个仓库里都没有的 3 条

`杨康平顶底·盘中`（`run_topbottom.sh`）、`杨康平顶底 v2`（`run_topbottom_v2.sh`）、
`缠论模拟盘·日终`（`sim_engine/run_daily_summary.sh`）目前只在源目录，不在
stock-dashboard / datahub-multi-tier / precious-task 任一仓库。

三者都属盘中告警或一天多次（缠论日终一天 4 次），按 §12.1 边界未纳入本次迁移，需要单独决策。

## 13. 期权 7 条日更接入 case（2026-11-26）

> 更正 §12.7 的说法：期权日更的**算法代码其实在公开仓 `options_utility`**（`run/*.sh`、
> `alert/`、`calc/` 都在），只是 `datahub-multi-tier` 里的 `options/` 目录从未被跟踪。
> 本节把 7 条**日更、无大模型依赖**的任务接成 case，算法仍只在 `options_utility` 一份，
> 这里只放薄包装（`run.py` 调入口脚本），不复制策略代码。

### 13.1 接入的 7 条

| key | 时间 | options_utility 入口 |
|---|---|---|
| `options_midday` | 12:30 | `alert/reports/midday.py` |
| `options_pcr_close` | 14:55 | `alert/tasks/oi_pcr.py close` |
| `options_oi_daily` | 15:30 | `alert/reports/oi_daily.py` |
| `options_pcr_macd` | 15:50 | `alert/tasks/pcr_macd_task.py batch` |
| `options_basis_daily` | 16:30 | `calc/basis_daily.py` → `alert/tasks/basis_ma20.py` |
| `options_close_monitor` | 18:30 | `alert/reports/close.py` |
| `options_skew10` | 18:50 | `alert/tasks/skew10.py` |

盘中采样类（OI/基差/VIX 每 15–30 分钟、PCR 14:30 盘中）**不接**，仍留原调度。

### 13.2 新机器怎么跑

1. 三个仓 clone 成**同级目录**（顺序任意，名字要对）：
   ```bash
   git clone https://github.com/baizhiwei9/stock-dashboard.git
   git clone https://github.com/baizhiwei9/options_utility.git
   git clone https://github.com/baizhiwei9/datahub-multi-tier.git
   ```
2. 放好 `~/.config/stock-dashboard/secrets.json`（600），跑 `python3 -m data_hub.tech.check_secrets` 自检。
3. 直接跑 scheduler 即可，不需要 crontab。路径可用环境变量覆盖：
   - `OPTIONS_UTILITY_ROOT`：options_utility 仓根（默认找同级目录，最后兜底老云盘路径）
   - `DATA_HUB_PATH` / `DATAHUB_DIR`：datahub 仓根（默认同级 `datahub-multi-tier`）
4. 先手工验证一条（不会重复推送）：
   ```bash
   cd stock-dashboard/case/options_oi_daily && python3 -u run.py
   ```

### 13.3 防双跑护栏

`task.json` 里 `enabled: true`，任务跑之前会检查 `crontab -l`：如果老机器上还挂着
同名 `--name "期权OI日报"` 的行，任务打印 `[skip]` 并退出 0，**不会和 crontab 双推**。
新机器没有 crontab（或 `crontab` 命令不存在）时正常执行。确认迁完后把老 crontab 行删掉，
task 自动接管。

### 13.4 已知尾巴

- `options_utility/run/basis_daily.sh` 比老机器 live 版少了一行 `calc/basis_daily.py`；
  case 包装里已按 live 行为跑两步，但 crontab 直接调该脚本的人仍会丢日频基差，需单独修该仓。
- 期权数据目前仍是「算完直接推飞书 + 落 JSON 快照」，未入库；按老板口径后续改成
  `data_hub.core.persist.persist_result()` 落 `hub_points`，图片除外。不在本次范围。

---

## 14. 新机器迁移：一个 TaskManager + 一个启动脚本（2026-11-26）

目标：换一台机器后，**只有两处放任务**，一个进程按各自时间跑，不留 crontab。

| 任务归属 | 仓库 | 调度 |
|---|---|---|
| 产**公开**数据的任务 | `stock-dashboard` 的 `case/` | 同一个 `scheduler.py` |
| **私有**任务 | `precious-task` 的 `case/`（通过 `CASE_EXTRA_DIRS` 挂载） | 同上 |

算法在别的仓库时**只 import/调用，不复制**（例：期权任务的算法在 `options_utility`，
薄包装落在 `stock-dashboard/case/options_*`，见 §13）。

### 14.1 一次迁移步骤

```bash
# 1) 四个仓 clone 到同一个父目录（名字要对；私有仓先 gh auth login）
git clone https://github.com/baizhiwei9/stock-dashboard.git
gh repo clone baizhiwei9/precious-task
gh repo clone baizhiwei9/datahub-multi-tier
git clone https://github.com/baizhiwei9/options_utility.git

# 2) 装依赖 + 录入凭据（一次性；缺项拒绝安装）
cd stock-dashboard
pip install -r requirements.txt
./install.sh                  # 无终端：export COZE_SEC_* 后重跑

# 3) 一条命令起全部任务（公有 + 私有 + 看门狗，不需要 crontab）
./start_all.sh up
#    要接管旧机的 build/macro 等 modules.json 模块时，改用：
#    SD_MODULES_FROM=/备份/旧机/modules.json ./start_all.sh up

# 4) 确认
./start_all.sh status
./start_all.sh doctor
```

`start_all.sh` 自动完成：

- `CASE_EXTRA_DIRS=<同级>/precious-task/case`（已存在则只追加、不重复）
- `DATA_HUB_PATH` / `EDB_REPO` / `PYTHONPATH` 指向同级 `datahub-multi-tier`
- `OPTIONS_UTILITY_ROOT` 指向同级 `options_utility`（也可 `../options-utility`）
- 首次生成 `modules.json`（空 `modules`，任务全部来自 case 目录；可用 `SD_MODULES_FROM` 导入旧机清单，见 §14.2）
- 凭据门禁（`setup.py --check --file-only`），缺项 fail-closed
- `scheduler.py start` + `sd_watchdog.sh --loop`，日志/心跳都在 `logs/`

### 14.2 旧机器上的 `modules.json` 模块

老部署的 macro / liquidity / rmt / chanlun / dfa / build 等 producer 仍写在
`modules.json` 里，不走 `case/`；`modules.json` 不入库，新机器从 git 拿不到。
首次 `up` 时用 `SD_MODULES_FROM` 指向旧机那份，一条命令导入（文件已存在则不会覆盖）：

```bash
SD_MODULES_FROM=/备份/旧机/modules.json ./start_all.sh up
```

导入后 `doctor` 会核对三件事：旧模块条数/启用数、「启用模块的绝对 `cwd` 在本机
是否存在」、「cmd 里有没有 scheduler 不会展开的占位符」（只认 `{{DATAHUB_ROOT}}`
和 `{{REPO_ROOT}}`，`{{WORKSPACE_A}}` / `{{FEISHU_*}}` 等要手改成真路径）。
不动旧机也能用：不设 `SD_MODULES_FROM` 时只跑 `case/` 任务，`up`/`doctor` 会明确
警告「公开看板不会自动刷新」，不会假装一切正常。

### 14.3 端口与旧机器并存

旧机器如果还在跑，别让两台同时写同一份 `state.json`：先 `./start_all.sh down`
旧机，或改新机 `modules.json` 的 `api.port`。共享云盘上同名目录的旧 `datahub`
会被同级解析先命中——`doctor` 打印的 `datahub` 路径要人工确认是这一台机器的。

### 14.4 已发现的归属失配

`doctor` 会报：

- `realestate_daily` 是 **public** 但代码在 `precious-task` → 按本节口径应搬到
  `stock-dashboard/case/`（待单独 PR，迁移期间调度不受影响，只是归属不符）。

另有一批老的 `modules.json` 模块（macro / liquidity / rmt / chanlun / dfa 等）
仍是 Dashboard 自带、不走 `case/`；它们产公开数据，位置符合「公有在 Dashboard」，
但还没改成 `case` 任务。逐步迁，不在本次范围。
