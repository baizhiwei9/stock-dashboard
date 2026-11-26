# 部署手册

本文档面向**从零部署**本仓库的人。按顺序做完即可跑起来。

> 飞书凭据是**必填项**，也是安装部署的**第一步**。不配它，所有推送类任务都会在最后一步失败。

---

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | **3.11 及以上** | 运行机是 3.11，本地开发可能更高。注意 f-string 内嵌同类型引号在 3.12+ 才合法，**不要在 3.11 上跑 3.12 语法**（会在 import 阶段直接 SyntaxError） |
| 内存 | ≥ 4GB 可用 | 7GB 机器上**必须串行跑任务**，并发有 OOM 风险（历史事故：pmi_export 并发时静默失败，串行即通过） |
| 磁盘 | ≥ 2GB | EDB 缓存 + 报告产物 |
| 网络 | 可访问 `data_hub` 数据源、`open.feishu.cn` | FRED 域名在部分沙箱不通，见 §7 |

---

## 2. 依赖安装

```bash
git clone <本仓库>
cd stock-dashboard
pip install -r requirements.txt
```

**同级还需要 dataHub**（数据统一入口，本仓库不直连数据源）：

```bash
git clone https://github.com/baizhiwei9/datahub-multi-tier
# 目录结构：
#   <父目录>/stock-dashboard/
#   <父目录>/datahub-multi-tier/
```

`data_hub` 的定位顺序所有入口共用（见 `datahub_path.py`）：

1. `DATA_HUB_PATH` → `DATAHUB_DIR` → `EDB_REPO`，取第一个存在且含 `data_hub/` 的；
2. 同级 checkout `<父目录>/datahub-multi-tier`；
3. `/opt/coze/.fuse_drive/dataHub_三级体系` 兜底。

同级 checkout 最省事；放在别处就用环境变量指定（绝对路径）：

```bash
export DATA_HUB_PATH=/path/to/datahub-multi-tier   # 或 EDB_REPO=/path/to/datahub-multi-tier
```

`modules.json` 的 `cwd` / `cmd` 请用 `{{DATAHUB_ROOT}}` 占位（见 `modules.example.json`），
scheduler 启动任务时会换成实际解析结果；写死本机路径会让任务子进程的 `PYTHONPATH`
跟看门狗探测结果脱节。

---

## 3. 敏感配置（安装强制门禁 —— 不配装不上）

**这一步不能跳过。** 仓库里不含任何明文凭据，所有敏感值统一落在
`~/.config/stock-dashboard/secrets.json`（权限 600，不入任何仓库、不进项目共享目录），
由 `case/_lib/app_secrets.py` 统一读取。

安装门禁脚本 `setup.py` 负责录入与校验：**缺项、配错、或配了连不通，一律返回非 0 退出码，
安装流程应在此中断**。运行期还有第二道兜底——`case/_lib/runner.py` 在任务启动时预检凭据，
避免跑到全流程最后一步的推送阶段才失败（最贵的一种失败）。

### 3.0 三段配置一览

| 段 | 字段 | 环境变量 | 必填 | 缺失后果 |
|---|---|---|---|---|
| `feishu` | `app_id` / `app_secret` / `webhook_url` | `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_WEBHOOK_URL` | **必填** | 所有报告推送失败；runner 预检直接拦下任务 |
| `scheduler` | `password` / `registry_key` | `SCHEDULER_PASSWORD` / `SCHEDULER_REGISTRY_KEY` | **必填** | HTTP 控制台 fail-closed 全拒；worker 无法注册 |
| `treasury` | `client_id` / `client_secret` | `TREASURY_CLIENT_ID` / `TREASURY_CLIENT_SECRET` | 选填 | `us_treasury_weekly` 的 buybacks 段降级跳过，其余照跑 |

> 历史坑：管理密码曾硬编码 `"123456"` 写在入库的 `modules.json` 里随公开仓库外泄。
> 现在**没有任何硬编码兜底**，缺项即失败。

### 3.1 一条命令完成（推荐）

```bash
python3 setup.py
```

依次做四件事：

1. 检查环境变量 / 凭据文件是否已有配置，已有则展示并复验
2. 缺失时**交互式要求输入**（输入不回显，不落任何仓库文件）
   - 飞书三项必填
   - 管理密码必填：**至少 12 位、含两类字符**，弱口令（`123456` / `password` / `admin` 等）**直接拒收**；
     直接回车可自动生成 24 位强随机
   - 注册密钥自动生成 32 位随机串
   - TreasuryDirect 选填，回车跳过
3. **校验**：
   - 飞书：真调接口——`app_id + app_secret` 能否换到 `tenant_access_token`、webhook 是否可达
   - 调度器：本地强度校验
4. 全部通过才写入 `~/.config/stock-dashboard/secrets.json`（权限 600）；
   任一项不过 → 打印原因并退出 1，**不允许继续安装**

非交互环境（CI / 容器 / 流水线）用校验模式，不合格即失败：

```bash
python3 setup.py --check
# 退出 0 = 三段全部就绪；退出 1 = 缺项或不合格，流水线应在此中断
```

离线环境加 `--skip-live`，只校验齐全性与密码强度、不联网：

```bash
python3 setup.py --check --skip-live
```

已有配置要重录（secret 轮换、密码更换后）：

```bash
python3 setup.py --force
```

### 3.2 飞书凭据（必填）

| 字段 | 从哪拿 | 用途 |
|---|---|---|
| `app_id` | 飞书开放平台 → 开发者后台 → 自建应用 → 凭证与基础信息 | 换 `tenant_access_token`，用于**上传图片**、以应用身份发消息 |
| `app_secret` | 同上（点「查看」） | 同上，与 `app_id` 配对 |
| `webhook_url` | 飞书群 → 群设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook | 纯文本 / 卡片消息推送 |

只发文本或卡片（webhook 模式）→ 只要 `webhook_url`。
要发**图片**（大部分图文报告都发）→ 三个都要。

### 3.3 调度器鉴权（必填）

`scheduler.py` 的 HTTP 控制台用 `X-SD-PW` 头鉴权，密码读 `scheduler.password`。

- **没有任何默认值**：没配密码时 `_pw_ok()` 一律返回 False（fail-closed），控制台全拒，不会裸奔。
- **改密码**：
  ```bash
  python3 manager.py sections pw '<新密码>'   # 写入统一凭据文件，权限 600
  python3 scheduler.py restart                # 重启生效
  ```
  ⚠️ 若同时存在环境变量 `SCHEDULER_PASSWORD`，运行时仍以环境变量为准，需一并修改或清除。
- `registry_key` 用于 worker 注册（`/api/register`），由 `setup.py` 自动生成，一般不用手动管。

### 3.4 TreasuryDirect buybacks（选填）

`us_treasury_weekly` 的 buybacks 段要一对 `client_id` / `client_secret`（取自 treasurydirect
官网 buy-backs 页面公开脚本）。**不配也能装**，只是这一段降级跳过、不出 buybacks 数据。

### 3.5 手工配置（等价于 setup.py，不想用脚本时）

**方式 A：环境变量（推荐用于容器 / CI / 多环境）**

```bash
export FEISHU_APP_ID="cli_xxxxxxxxxxxxxxxx"
export FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

export SCHEDULER_PASSWORD="换成 12 位以上强密码"
export SCHEDULER_REGISTRY_KEY="换成 32 位随机串"

export TREASURY_CLIENT_ID="..."        # 选填
export TREASURY_CLIENT_SECRET="..."    # 选填
```

写进 `~/.bashrc` 或部署编排的 env 配置。**不要**写进仓库里的任何文件。

**方式 B：凭据文件（推荐用于单机长期运行）**

```bash
mkdir -p ~/.config/stock-dashboard
cp case/_lib/secrets.example.json ~/.config/stock-dashboard/secrets.json
vi ~/.config/stock-dashboard/secrets.json
chmod 600 ~/.config/stock-dashboard/secrets.json   # 只给运行账号读
```

文件格式（各段独立，缺段不算错）：

```json
{
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  },
  "scheduler": {
    "password": "换成 12 位以上强密码",
    "registry_key": "换成 32 位随机串"
  },
  "treasury": {
    "client_id": "...",
    "client_secret": "..."
  }
}
```

凭据文件的搜索顺序（命中即止）：

1. `$APP_SECRETS_FILE` 指向的路径 —— **显式指定就只认它**，读不到不回落（避免「以为读 A 其实读 B」）
2. `~/.config/stock-dashboard/secrets.json` ← **推荐放这里**，不在任何仓库内
3. `<repo>/case/_lib/secrets.json` ← 备选，已被 `.gitignore` 排除
4. 旧的扁平 `feishu_secret.json`（兼容历史部署，只服务 `feishu` 段）

### 3.6 校验

```bash
python3 setup.py --check
```

期望输出（每行一个 ✅，退出码 0）：

```
  ✅ app_id / app_secret 有效（已换到 tenant_access_token）
  ✅ webhook 可达（返回 code=19002，符合空 body 的预期）
  ✅ 管理密码合规（24 位）
  ✅ worker 注册密钥已配置（32 位）
  ✅ treasury 凭据已配置（buybacks 段可正常取数）
✅ 全部就绪，安装可继续。
```

只想看来源和取值（不打印明文）：

```bash
cd case/_lib
python3 -c "
import app_secrets as a
print('来源:', a.source())
for name in ('feishu', 'scheduler', 'treasury'):
    sec = a.section(name)
    print(name, {k: (('%d 位' % len(v)) if k.endswith(('secret', 'password', 'key')) else v[:16]) for k, v in sec.items()})
"
```

### 3.7 常见错误

| 报错 | 原因 | 处理 |
|---|---|---|
| `飞书凭据缺：webhook_url` | 没配 webhook | 按 §3.2 配 `FEISHU_WEBHOOK_URL` 或文件里的 `webhook_url` |
| `飞书凭据缺：app_id, app_secret` | 只配了 webhook，任务又要发图片 | 补 `app_id` / `app_secret` |
| `管理密码不合规：至少 12 位（当前 6 位）` | 用了 `123456` 这类弱口令 | 换强密码，或直接回车让 `setup.py` 自动生成 |
| `管理密码未配置（HTTP 控制台会 fail-closed 全拒）` | `scheduler` 段缺失 | 跑 `python3 setup.py` |
| 控制台一直返回 `密码不对` | 改了 `modules.json` 或 `sections.json` 里的密码（已废弃） | 密码只在统一凭据文件里，用 `manager.py sections pw` 改 |
| 推送返回 `code != 0`，提示 `sign match fail` | 群机器人开了「签名校验」，代码用的是无签名模式 | 群机器人设置里关掉签名校验，或改用应用身份发消息 |
| 推送返回 `code 19001` / `invalid webhook` | webhook 被重置或机器人被移出群 | 重新复制 webhook 地址 |
| `当前不是交互终端，无法录入` | 在 CI / 管道里跑了不带 `--check` 的 `setup.py` | 改用环境变量，或跑 `setup.py --check` |

### 3.8 安全约定

- **敏感值绝不入库**：`.gitignore` 已排除 `secrets.json` / `feishu_secret.json` / `*.secret.json` / `.env`。
- **不入项目共享空间**：统一放 `~/.config/`，不要放仓库目录（云盘目录可能被其他成员看到）。
- **代码里只允许出现占位符模板**：`case/_lib/secrets.example.json`、`case/_lib/feishu_secret.example.json`。
- **轮换**：凭据一旦进过公开 git 历史就视为已泄露，必须在飞书后台**重置 `app_secret` + 重置群机器人 webhook**、
  换掉管理密码，然后按 §3.1 或 §3.5 重新配置。

---

## 4. 任务清单与启用

每个任务一个目录 `case/<task_name>/`，配置在 `task.json`：

```json
{
  "key": "pmi_export",
  "title": "pmi 出口 月度展望报告推送",
  "section": "macro",
  "sched": { "mode": "monthly", "at": "21:00", "days": [28] },
  "cmd": "python3 -u run_monthly.py",
  "timeout": 1800,
  "enabled": false,
  "visibility": "public"
}
```

| 字段 | 说明 |
|---|---|
| `enabled` | `false` 时调度器跳过。**新部署默认全为 false**，先手动验证通过再改 true |
| `visibility` | `public`（进主仓看板，12 个）/ `private`（进 precious-task 仓，17 个） |
| `cmd` | 兜底命令。若 `pipeline.stages` 声明了阶段，`_lib/runner.py` 按阶段跑，`cmd` 只在无阶段声明时用 |
| `timeout` | 秒。取数类任务给足（月度全链路实测约 490s，建议 ≥1800） |

启用一个任务：

```bash
# 手动跑一次（先验证）
python3 case/_lib/runner.py --task pmi_export

# 只跑某个阶段（排错时用）
python3 case/_lib/runner.py --task pmi_export --only fetch

# 验证通过后改 enabled
python3 -c "
import json; p='case/pmi_export/task.json'
d=json.load(open(p)); d['enabled']=True
json.dump(d, open(p,'w'), ensure_ascii=False, indent=2)
print('enabled ->', d['enabled'])
"
```

---

## 5. 运行方式

### 5.1 单任务手动跑

```bash
cd stock-dashboard
export EDB_REPO=/path/to/datahub-multi-tier      # 若 data_hub 不在同级
python3 case/_lib/runner.py --task <task_name>
```

产物落在 `case/<task_name>/out/`，状态写 `out/status.json`。

### 5.2 全量刷新

```bash
bash run_daily.sh
```

### 5.3 定时调度

两个可选入口：

- **`scheduler.py`**（仓库自带）：按 `modules.json` 里每个 task 的启停时间跑，HTTP 控制台端口见 `modules.json` 的 `api.port`。
  ```bash
  python3 scheduler.py fg     # 前台跑
  ```
- **系统 crontab**：纯 Python 任务用 crontab 更省资源（零 Token）。注意同一任务**不要同时挂在 scheduler 和 crontab 上**，会双跑。

> ⚠️ 本仓库的任务都是**纯 Python 脚本**，不需要 LLM，调度方式不影响 Token 成本。但**必须串行**（见 §1 内存要求）。

### 5.4 运维命令

```bash
python3 manager.py list          # 任务清单
python3 manager.py status        # 各任务最近状态
python3 manager.py run <task>    # 手动跑
python3 manager.py enable <task> # 启用
python3 manager.py disable <task>
```

---

## 6. 目录约定

```
stock-dashboard/
├── case/
│   ├── _lib/                  # 公共库
│   │   ├── runner.py          # 阶段化执行器
│   │   ├── case_registry.py   # 任务注册（主仓优先于 CASE_EXTRA_DIRS）
│   │   ├── feishu_creds.py    # 飞书凭据统一入口 ← 脱敏核心
│   │   ├── feishu_secret.example.json  # 凭据模板（占位符）
│   │   ├── alert.py           # 失败告警
│   │   ├── checks.py          # 数据新鲜度检查
│   │   ├── extract.py         # 产物提取
│   │   ├── publish.py         # 落库/发布
│   │   └── assets.py          # 图表资源
│   └── <task_name>/
│       ├── task.json          # 任务配置
│       ├── pipeline.py        # 阶段钩子（可选）
│       ├── feishu_config.py   # 薄封装，变量转发自 feishu_creds（不再含明文）
│       └── out/               # 产物 + status.json（不入库）
├── build.py                   # 生成 index.html
├── scheduler.py               # 调度器 + HTTP 控制台
├── manager.py                 # 命令行运维
└── DEPLOY.md                  # 本文档
```

**私仓扩展**：`precious-task` 仓的任务通过环境变量挂进来，主仓同名 key 优先：

```bash
export CASE_EXTRA_DIRS=/path/to/precious-task/case
```

---

## 7. 已知环境限制

| 限制 | 表现 | 规避 |
|---|---|---|
| FRED 域名在部分沙箱不通 | `curl ... fredgraph.csv` exit 28 / exit 92 超时 | 走 `data_hub.edb` 的对应序列；或改用 `markets.newyorkfed.org` / `api.fiscaldata.treasury.gov` 等可达源 |
| SQLite 单写锁 | 并发跑两个任务时，后启动的会卡在 EDB 库锁上直到超时（rc=124） | **严格串行**。等前一个任务 `status.json` 落盘后再起下一个 |
| 无 swap | 7GB 机器上并发 → OOM → 进程被静默 kill，日志只有一行空错误 | 串行 + 单任务 `timeout` 给足 |
| 云盘 `.git` 只读 | 在云盘目录里跑 `git` / `gh` 报 `not a git repository` | 在 `/tmp` 单独 clone 一份做 git 操作 |

---

## 7.1 跑在本机盘、云盘只做备份（推荐）

**不要直接在 FUSE 云盘目录里跑调度器。** 实测（2026-11-26，云盘 `/Coze/Drive/...`）：

| 操作 | 云盘 | 本机盘 |
|---|---|---|
| `listdir case/` | 0.6 ~ 1.3 s | ~0 ms |
| `_case_modules()`（`listdir` × 16） | 6 ~ 14 s | ~ ms |
| `GET /api/state` | 9 ~ 12 s | ~ ms |
| `tick()` 实测间隔（名义 15s） | **29 s** | 15 s |

`all_modules()` 每个 tick 都要跑一次，云盘上光扫目录就吃掉十几秒 —— 调度精度整体退化，
控制台也打不开。所以：**代码、配置、`state.json`、数据库全部放本机盘跑；云盘只当备份。**

```
/root/work/                     # 本机盘（ext4）：跑在这里
├── stock-dashboard/            #   git clone 到本机，不要 clone 到云盘
├── datahub-multi-tier/
└── data/

/Coze/Drive/股市监控画布/backup/  # 云盘：只放同步上来的副本
```

```bash
# ① 本机盘部署（云盘上那份原件先留着，不删）
rsync -a --exclude logs --exclude __pycache__ \
      /Coze/Drive/股市监控画布/stock-dashboard/ /root/work/stock-dashboard/
rsync -a --exclude __pycache__ \
      /Coze/Drive/股市监控画布/datahub-multi-tier/ /root/work/datahub-multi-tier/

# ② 按本文第 2~3 节重跑依赖与凭据（凭据在 ~/.config 下，不随目录搬）
cd /root/work/stock-dashboard && ./install.sh

# ③ 起调度器，确认 47 个模块都在
python3 scheduler.py fg          # 前台看几秒，没问题 Ctrl-C
bash sd_watchdog.sh              # 带 CASE_EXTRA_DIRS 拉起常驻实例
python3 scheduler.py health      # 0 = 健康
curl -s -o /dev/null -w '%{time_total}\n' http://127.0.0.1:8767/api/state   # 应 < 0.1s
```

> **重启必须带 `CASE_EXTRA_DIRS`**。裸 `python3 scheduler.py restart` 会把私有任务
> （`precious-task/case` 下那 18 个）整个漏掉 —— `sd_watchdog.sh` 会自动导出这个变量，
> 所以**统一走 `bash sd_watchdog.sh`**，别手敲 restart。

**定时把本机盘备份回云盘**（脚本：`scripts/sync_to_drive.sh`）：

```bash
bash scripts/sync_to_drive.sh --dry-run     # 先看要同步什么
bash scripts/sync_to_drive.sh               # 同步一次
# crontab：每小时一次
0 * * * * cd /root/work/stock-dashboard && bash scripts/sync_to_drive.sh >/dev/null 2>&1
```

脚本默认排除 `logs/`、`__pycache__/`、`state.json`、`scheduler.pid`、`*.bak*`，
不加 `--prune` 就不会删云盘上的东西。数据全是从公开源抓的，丢了重跑一遍就有，
**不需要做主从/双写/一致性那一套**。

---

## 8. 部署自检清单

按顺序打勾：

- [ ] Python ≥ 3.11，`pip install -r requirements.txt` 无报错
- [ ] **敏感配置门禁已过**：`python3 setup.py --check` 退出码为 0（缺项或弱口令此处必须中断安装）
- [ ] 三段齐备：`feishu` 联网校验通过、`scheduler.password` 非弱口令、`treasury` 已配或明确接受降级
- [ ] 同级 checkout 了 `datahub-multi-tier`（或设了 `EDB_REPO`）
- [ ] 凭据文件 `~/.config/stock-dashboard/secrets.json` 权限 `600`，且不在仓库目录内
- [ ] 仓库内无明文敏感值：`git grep -nE '123456|open\\.feishu\\.cn/open-apis/bot/v2/hook/[0-9a-f]{8}-' ` 无命中
- [ ] `modules.json` / `sections.json` 里**不含** `password` / `registry_key`（已移到统一凭据文件）
- [ ] `python3 case/_lib/runner.py --task pmi_export` 能跑通（作为冒烟用例）
- [ ] 飞书群里收到测试消息
- [ ] 逐个任务验证通过后，再把 `task.json` 的 `enabled` 改 `true`
- [ ] 定时入口只挂一处（scheduler 或 crontab，不要双挂）

---

## 9. 排错入口

| 现象 | 先看哪 |
|---|---|
| 任务失败 | `case/<task>/out/status.json` 的 `error` / `stages` |
| 取数失败 | `case/<task>/out/` 下的阶段日志；再单独跑 `--only fetch` |
| 推送失败 | 先跑 §3.3 校验凭据；再看返回的 `code` / `msg` |
| 卡住不退出 | `ps aux \| grep <task>` 看是否在等 SQLite 锁（是不是有别的任务在跑） |
| 产物没生成 | `case/<task>/out/` 是否为空 → `pipeline.stages` 的 `artifacts` 是否配错 |
