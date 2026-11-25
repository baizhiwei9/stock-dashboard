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
| Python | 3.9+（实测 3.12） | `scheduler.py`、`case/_lib/*` **纯标准库**；`build.py` 只额外依赖 `data_hub` 仓库（不是 pip 包） |
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
│   ├── config/               # 敏感配置（secrets.json 不入库，example 入库）
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
| `COZE_SECRETS` | 可选 | 显式指定敏感配置文件路径（默认 `<仓库>/config/secrets.json`） |
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
#   等价入口：./install.sh   —— 先 --init 补齐，再校验，不合格非 0 退出
#     api.password      ≥10 位，禁止 123456（HTTP 控制台 / X-SD-PW）
#     api.registry_key  ≥16 位，且不要与管理密码相同（Worker 注册 / X-SD-KEY）

#    非交互部署（CI / 自动化装机）：用环境变量喂值，--init 会落盘
export COZE_SEC_API_PASSWORD='<至少10位>'
export COZE_SEC_API_REGISTRY_KEY='<至少16位>'
python3 setup.py

# ③ 校验必填项与文件权限（退出码 0 才算过，不打印任何值）
python3 setup.py --check
#   等价：python3 case/_tools/check_secrets.py

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

```bash
# 前台跑，先确认没报错（Ctrl-C 退出）
python3 scheduler.py fg

# 确认无误后转后台常驻
python3 scheduler.py start
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

## 7. 守护（唯一需要的一条 cron）

`scheduler.py` 是**单点常驻进程**：它被 kill 掉，所有任务都不会再被触发。所以留一条 cron
专门守着它——cron 不跑任何业务任务，业务仍全部由 `scheduler.py` 决定。

```cron
# 每分钟巡检一次；路径换成你的实际部署目录
* * * * * /srv/monitor/stock-dashboard/sd_watchdog.sh >/dev/null 2>&1
```

安装：

```bash
chmod +x /srv/monitor/stock-dashboard/sd_watchdog.sh
crontab -e        # 粘上面那一行
crontab -l | grep sd_watchdog    # 确认写入
```

看门狗按**心跳**判定，不看 pid：

| `scheduler.py health` | 含义 | 看门狗动作 |
|---|---|---|
| `0` | 心跳新鲜（180 秒内更新过） | 只更新 `logs/watchdog.hb`，静默 |
| `1` | 未运行 | `scheduler.py start` |
| `2` | 僵死：进程在，但心跳超 180 秒没更新 | `scheduler.py restart`（`start` 会因「已在运行」空转） |

拉起/重启后会再查一次；连续 `SD_ALERT_AT`（默认 3）次失败才发 `SD_ALERT_WEBHOOK`，
告警后清零计数，避免每分钟刷屏。

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

私有任务放独立私有仓库，只按 `case/CONTRACT.md` 写 `task.json` + 脚本，不复制框架：

```bash
git clone git@github.com:<you>/precious-task.git /srv/private-tasks
export CASE_EXTRA_DIRS=/srv/private-tasks/case

python3 scheduler.py status                       # 私有任务与 case/ 一起排期
python3 case/_lib/runner.py --task <key> --dry-run
```

- 扫描顺序：先 `case/`，再 `CASE_EXTRA_DIRS`；同一个 key 重复时**先命中的生效**，后出现的记一条冲突（stderr）。
- 私有任务的 `visibility` 应设 `internal`：照常定时跑、照常写指标仓，但不上牌面。

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
| `watchdog.hb` 时间不动 | cron 那行没生效 | `crontab -l`；确认脚本可执行；确认路径正确 |
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
- [ ] `./install.sh` 通过；`config/secrets.json` 存在且权限为 `600`
- [ ] `python3 case/_tools/check_secrets.py` 返回 0
- [ ] `index.html` 里搜不到明文管理密码 / 注册密钥
- [ ] `python3 scheduler.py status` 能列出全部 key（含私有任务）
- [ ] `python3 scheduler.py health` 返回 `0`
- [ ] `curl -s http://127.0.0.1:8767 | head` 有响应
- [ ] `logs/watchdog.hb` 在 1 分钟内更新
- [ ] 手动跑通一个任务：`scheduler.py run <key>`，`out/status.json` 全绿
- [ ] `python3 build.py` 能生成 `index.html`
- [ ] `index.html` 里目标 Tab 有数据