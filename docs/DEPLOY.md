# 部署手册

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
| Python | 3.9+ | `scheduler.py`、`build.py`、`case/_lib/*` **纯标准库**，不装包也能起调度与看板 |
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

---

## 5. 首次配置（三件必改）

```bash
# ① modules.json 里的 api.password / api.registry_key 初始是 123456，必须改掉
#    （HTTP 控制台与 Worker 注册都用它做校验）
python3 - <<'PY'
import json, secrets
p = "modules.json"
d = json.load(open(p, encoding="utf-8"))
d["api"]["password"] = secrets.token_urlsafe(12)
d["api"]["registry_key"] = secrets.token_urlsafe(16)
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已写入新口令，请自行记录")
PY

# ② 确认端口没被占用（默认 8767）
ss -lntp | grep 8767 || echo "8767 空闲"

# ③ 任务清单核对：能列出所有 key 就说明 modules.json + case/*/task.json 都读到了
python3 scheduler.py status
```

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
- [ ] `python3 scheduler.py status` 能列出全部 key（含私有任务）
- [ ] `python3 scheduler.py health` 返回 `0`
- [ ] `curl -s http://127.0.0.1:8767 | head` 有响应
- [ ] `logs/watchdog.hb` 在 1 分钟内更新
- [ ] 手动跑通一个任务：`scheduler.py run <key>`，`out/status.json` 全绿
- [ ] `python3 build.py` 能生成 `index.html`
- [ ] `index.html` 里目标 Tab 有数据