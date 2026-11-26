# 迁机部署手册（去云盘化）

> 目标：把本机（Coze 云主机 + 云盘 FUSE）上跑着的这套东西，原样搬到一台**普通 Linux 服务器**。
> 本文只覆盖「怎么让它在没有云盘的机器上跑起来」，不改任何业务逻辑。

---

## 0. 一句话流程

```bash
# ① 三个仓库放到同一个父目录，目录名不能改
/opt/data/
├── datahub-multi-tier/     # git clone <DH_REPO>
├── stock-dashboard/        # git clone <SD_REPO>
└── options_utility/        # git clone <OPT_REPO>   期权 7 条日更的算法在这

# ② 装依赖
cd /opt/data/datahub-multi-tier && pip3 install -r requirements.txt
cd /opt/data/stock-dashboard   && pip3 install -r requirements.txt
cd /opt/data/options_utility   && pip3 install -r requirements.txt

# ③ 填凭据（见 §2，共四处，缺一处就有一类任务跑不了）
cp datahub-multi-tier/options/config/private.example.py \
   datahub-multi-tier/options/config/private.py
vi datahub-multi-tier/options/config/private.py

cp options_utility/config/private.example.py \
   options_utility/config/private.py
vi options_utility/config/private.py

# 第四处：IMA 凭据（土狗归档上传知识库用），从原机搬
mkdir -p ~/.config/ima
scp <原机>:~/.config/ima/{api_key,client_id} ~/.config/ima/ && chmod 600 ~/.config/ima/*

# ④ 调度配置（见 §3）
cd /opt/data/stock-dashboard
# modules.json 已入库，clone 下来就有，不需要复制/改任何路径
python3 datahub_path.py            # 确认 DataHub 解析到哪
python3 -c "import json;d=json.load(open('modules.json'));print('模块数',len(d['modules']))"

# ⑤ 自检
python3 case/_lib/paths.py
python3 setup.py --check          # 凭据门禁，退出 0 才算过

# ⑥ 起调度（幂等；不需要 crontab，日更/周更全在 case/ 里）
./start_all.sh up
./start_all.sh status
```

> 第三、四处（`options_utility/config/private.py`、`~/.config/ima/*`）和飞书 CLI 登录态
> 不在任何仓库里，只能从原机搬。少任何一处都是「任务跑起来但结果发不出去/传不上去」，
> 详见 §2 和 §5。

**目录名 `datahub-multi-tier` / `stock-dashboard` / `options_utility` 必须保持原样**，所有路径解析都靠这三个名字（`start_all.sh` 会自动把同级 `options_utility` 认成 `OPTIONS_UTILITY_ROOT`）。

---

## 1. 环境变量

全部可选 —— 不设也能跑，但迁机时建议显式设，省得靠探测猜。

| 变量 | 作用 | 不设时的行为 |
|---|---|---|
| `DATA_HUB_PATH` | DataHub 仓库根 | 向上找仓库根的 `datahub_path.py` → 同级 `../datahub-multi-tier`；都没有则报错 |
| `CASE_WORKSPACE` | 老「蓁宝/所有对话/主对话」目录 | 空（不再默认云盘旧目录） |
| `CASE_DRIVE_ROOT` | `/Coze/Drive` 的新位置 | 仍是 `/Coze/Drive` |
| `CASE_FUSE_ROOT` | `/opt/coze/.fuse_drive` 的新位置 | 仍是 `/opt/coze/.fuse_drive` |
| `CASE_APP_DATA` | `/app/data` 的新位置 | 仍是 `/app/data` |
| `CASE_FEISHU_LIB` | 飞书卡片/推送模块目录 | 仓库内 `case/sector_screening_v4` 优先 |
| `FEISHU_PUSH_CANON` | 飞书推送真身 `feishu_push.py` | 向上找同级 `datahub-multi-tier/options/alert/notifier/` |
| `GROWTH_SCREENER_DIR` | 成长股选股器目录（仓库外，见 §5） | `$CASE_WORKSPACE/成长股选股器` |
| `TUSHARE_TOKEN` | Tushare token | `options/config/private.py` |
| `CASE_PY_DEPS` / `CASE_FONTS` / `CASE_SHARED` | 手工依赖 / 字体 / 共享模块 | workspace 下同名目录 |

后三个变量由 `case/_lib/cloud_paths.py` 统一消费：`case/` 下的旧根字面量都写成
`_cp('/Coze/Drive/...')`，没设变量时原样返回（原机行为零变化），设了就只换根、其余原样拼回。
`CASE_WORKSPACE` 是**最长前缀优先**：它命中「蓁宝/所有对话/主对话」时优先于 `CASE_DRIVE_ROOT`。

自检：`python3 case/_lib/cloud_paths.py`（打印映射表），加 `--check` 有根缺失就返回 1。
回归用例：`python3 -m pytest test_case_no_cloud_paths.py -q`（45 例，扫 21 个切换 key）。

写进 `/etc/environment` 或 systemd unit 的 `Environment=` 都行。crontab 里则直接写在任务行首：

```cron
30 19 * * 1-5 cd /opt/data/stock-dashboard/case/circulation_daily && \
  CASE_WORKSPACE=/opt/data/main-conversation \
  DATA_HUB_PATH=/opt/data/datahub-multi-tier \
  python3 -u scripts/circulation_daily.py
```

---

## 2. 凭据放哪（四处，全部在仓库外）

这四类凭据分属四套代码，**没有一处能代替另一处**。迁机时缺哪一处，就有一类任务直接跑不了。

| # | 位置 | 内容 | 谁在用 | 缺失后果 |
|---|---|---|---|---|
| 1 | `~/.config/stock-dashboard/secrets.json`（0600） | 飞书 app_id/app_secret/webhook、调度器密码、Tushare、SMTP | `case/` 下 21 条任务、`scheduler.py`、`setup.py` | 安装门禁 `setup.py --check` 直接不过，任务推不出去 |
| 2 | `datahub-multi-tier/options/config/private.py` | Tushare token、飞书 chat_id、邮箱授权码 | `data_hub`、期权算法、`modules.json` 里的老模块 | 对应通道降级/报错 |
| 3 | `options_utility/config/private.py` | 同上（期权仓自己的一份） | 7 条期权日更（`case/options_*`） | 期权任务取数/推送失败 |
| 4 | `~/.config/ima/{api_key,client_id}` | IMA 开放平台凭据 | `case/tugou_archive`（土狗归档上传知识库） | **土狗归档全挂**（见 §2.2） |

另外 `~/.config/stock-dashboard/secrets.json` 之外还有一个飞书 CLI 的登录态目录
（`LARKSUITE_CLI_CONFIG_DIR`，含 `master.key` 与 `*.enc`），土狗归档的预检会用到，见 §2.3。

### 2.1 两份 `private.py`

字段完全一样，都是空模板入库、真值仓外：

```python
TS_TOKEN = ''
TS_PROXY_TOKEN = ''
FEISHU_CHAT_ID = ''            # oc_ 开头
FEISHU_PRIVATE_USER_ID = ''    # ou_ 开头
EMAIL_FROM_ADDR = ''
EMAIL_FROM_PASS = ''           # 163 SMTP 授权码，不是登录密码
EMAIL_TO_ADDR = ''
AGENT_EMAIL = ''
QQ_EMAIL_AUTH_CODE = ''
```

优先级：**环境变量 > `private.py` > `credentials.py` 里的空默认值**。
所以容器化部署可以完全不建 `private.py`，只用环境变量注入。

`credentials.py` 本身不含明文，已入库，不用改。

### 2.2 IMA 凭据（土狗归档专用，最容易漏）

`case/tugou_archive` 不读 `secrets.json`，它调的是云盘上的 IMA skill 脚本
（`ima_api.cjs`），那个脚本只认两个来源：

```
~/.config/ima/client_id     ← 32 位
~/.config/ima/api_key       ← 76 字节
```

或环境变量 `IMA_CLIENT_ID` / `IMA_API_KEY`。**两样都没有时脚本报「未找到 IMA 凭证」**，
土狗归档当天所有文章都上传不了。

搬运（原机 → 新机）：

```bash
mkdir -p ~/.config/ima
scp <原机>:~/.config/ima/client_id  ~/.config/ima/
scp <原机>:~/.config/ima/api_key    ~/.config/ima/
chmod 600 ~/.config/ima/api_key ~/.config/ima/client_id
```

自检（`code: 0` 才算通，不打印任何密钥）：

```bash
node <IMA_SKILL_DIR>/ima_api.cjs openapi/wiki/v1/search_knowledge_base \
  '{"query":"沧海一土狗","cursor":"","limit":20}' | head -c 120
# 期望：{"code":0,"msg":"success",...}
```

> **踩过的坑（2026-11-26，已在代码里修）**：`ima_api.cjs` 是 1.1.9、服务端已到 1.1.10。
> 它每天第一次调用会先做版本检查，发现有新版就把更新提示 JSON 打到 stdout 并退出 ——
> 那个对象**没有 `code` 字段**，早先 `tugou_archive.py` 把它当业务错误，
> 日志里天天出现 `[RETRY] IMA API 第1次失败（code=None msg=None）`，重试一次又好。
>
> 现在 `ima_api_call()` 会识别这种「无 code + 带 latest_version」的响应，
> 自动把 `IMA_SKILL_VERSION` 设成提示里的版本号并**立刻重跑**（不 sleep、不记 RETRY）：
>
> ```
> [INFO] IMA skill 有新版本 1.1.10，按该版本重跑（已设 IMA_SKILL_VERSION）
> ```
>
> 想彻底消除这次额外往返，就把 skill 升到 1.1.10，或预先 `export IMA_SKILL_VERSION=1.1.10`。

### 2.3 飞书 CLI 登录态（土狗归档的预检）

`case/tugou_archive/run_daily.sh` 第一步就跑 `lark-cli im +chat-messages-list --as user`，
用的是 `LARKSUITE_CLI_CONFIG_DIR` 指向的登录态：

```
$LARKSUITE_CLI_CONFIG_DIR/
├── config.json
├── lark-cli/master.key                       # 加解密主密钥
├── lark-cli/cli_<appid>_<openid>.enc         # 用户登录态
└── lark-cli/appsecret_<appid>.enc
```

原机是 `/app/data/所有对话/主对话/.feishu_cli`（`CASE_APP_DATA` 可重定向）。
不搬 → 预检直接失败并告警「飞书 CLI 用户授权失效」，一篇都不会归档。
搬完用 `lark-cli auth status` 或手工跑一次预检命令确认。

---

## 3. 调度配置 `modules.json`

**`modules.json` 现在入库了。** clone 下来就有，新机器不需要复制、不需要改路径。

为什么能入库：文件里的 `cwd` / `cmd` 已全部改成占位符，**一个本机绝对路径都没有**。
`datahub_path.expand_root()` 在 scheduler 拉起任务时才展开成本机真实路径。

| 占位符 | 展开成什么 | 谁提供 |
|---|---|---|
| `{{DATAHUB_ROOT}}` | `datahub-multi-tier` 绝对路径 | `DATA_HUB_PATH` / `DATAHUB_DIR` / `EDB_REPO`，或同级 clone |
| `{{REPO_ROOT}}` | `stock-dashboard` 绝对路径 | 本文件所在目录，自动 |
| `{{FEISHU_CLI_DIR}}` | `lark-cli` 配置目录 | `LARKSUITE_CLI_CONFIG_DIR` → `<repo>/.feishu_cli` → `~/.feishu_cli` → Coze 平台默认位置，取第一个非空目录 |

也就是说：**新机器只要 `datahub-multi-tier` 跟 `stock-dashboard` 是同级目录**，
12 个 producer 一条命令就能跑，`modules.json` 一个字都不用改。

12 个模块（生产实况，全部 `enabled: true`）：

| key | 时间 | 干什么 |
|---|---|---|
| `macro` | 21:00 | 宏观与美元环流 |
| `liquidity` | 21:20 | 流动性与两融 |
| `rmt` | 22:10 | RMT 随机矩阵 |
| `chanlun` | 22:25 | 缠论结构 + 趋势动量 |
| `dfa` | 22:28 | DFA / Hurst |
| `option` | 18:10 | 期权定价 IV/偏度/PCR/VIX |
| `bond` | 18:40 | 债券与理财 |
| `risk` | 21:50 | 风险与机会标签 |
| `task_tags` | 22:20 | 任务结论转录 |
| `task_tags_notify` | 22:35 | 任务标签汇总推送（飞书 + 邮件） |
| `tech_snapshot` | 22:37 | 指标仓云盘快照 |
| `build` | 22:40 | 看板构建发布 |

> `tech_snapshot` / `build` 带 `not_before == at`：它们要等前面 10 个 producer 落完数
> 才能跑，早跑会构建出半截数据的看板。

### 3.1 私有模块不在这个文件里

`risk_v52`（风险日报 v5.2）和 `vol_surface_vix`（波动率曲面）**已经迁进 `precious-task` 仓**
的 `case/risk_v52/` 和 `case/vol_surface_vix/`，各自带 `task.json` / `pipeline.py` /
`trading_day_check.py`，代码完整入库。它们**不写在本文件的 `modules.json` 里**：

- 写在这里会和 `precious-task/case/` 的同名任务撞 key，而 `all_modules()` 是
  **内置优先**——内置那条赢，`case/` 那条被屏蔽，结果就是跑了一个没人维护的旧路径
- 私有任务统一靠 `SD_CASE_EXTRA_DIRS=../precious-task/case` 挂载（`start_all.sh` 自动认同级目录）

这两条在 `task.json` 里默认 `enabled: false`，需要时单独打开：

```bash
cd ../precious-task
python3 - <<'PY'
import json
for k in ("risk_v52", "vol_surface_vix"):
    p = "case/%s/task.json" % k
    d = json.load(open(p, encoding="utf-8"))
    d["enabled"] = True
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
```

打开前先按各自 `task.json` 里的 `migrate_note` 验一次（都是 `--no-send` 干跑，不推送）：

```bash
cd case/risk_v52        && python3 daily_risk_report.py --no-send
cd case/vol_surface_vix && python3 vol_surface_runner.py --no-send
```

### 3.2 要改时间/加任务怎么办

直接改 `modules.json` 并提交。`scheduler.py` 每 30s 重扫一次，改完自动生效，不用重启。
`enabled: false` 的任务既不会被定时拉起，也不会被 `/api/run_all` 手动跑起来。

两处**不是占位符、需要按人改**的值在 `task_tags_notify` 的 `cmd` 里：
飞书群 `--chat oc_...` 和告警邮箱 `--to ...@qq.com`。它们是「通知对象」，不是凭据
（没有 `app_secret` 也用不了），所以直接写在文件里；换人用就改这两处。

### 3.3 老机器的 `modules.json` 怎么处理

老机器那份里全是 `/opt/coze/.fuse_drive/...` 之类的绝对路径，**不要搬**。
真要参考，用 `SD_MODULES_FROM=/旧机/modules.json ./start_all.sh up` 导进来，
然后 `./start_all.sh doctor` 会把「绝对 cwd 在本机不存在」的模块列出来。

---

## 4. 调度：日更/周更走 scheduler，crontab 只留高频

新机器上**不需要为日更/周更配 crontab**。两类任务分开管：

| 类别 | 载体 | 怎么起 |
|---|---|---|
| 日更 / 周更（21 条，含期权 7 条） | `case/<key>/task.json` | `./start_all.sh up`（scheduler 一个常驻进程） |
| 一天多次的高频采样（期权 OI/VIX 盘中、小波、缠论模拟盘、策略监控启停） | crontab | 照原机搬，只改路径前缀 |

原机 crontab 里的日更行（如 `中线偏好监控`、`Hurst指数`、`土狗归档`）**在新机器上不要搬** ——
这些任务已由 `case/` 接管，两条都留就是双跑。切换脚本 `scripts/switch_crontab.sh --apply`
做的是同一件事：先校验 21 个 `task.json` 齐全、时间点/入口文件/星期都对得上，
再一次性删掉 crontab 里那 22 行、同时把 `enabled` 打开，装失败自动回滚。

留在 crontab 的高频行仍走 `health_run` 包装器：

```
30 19 * * 1-5  cd <主对话> && PYTHONPATH=<DATAHUB> python3 -m data_hub.tech.health_run \
                 --name "中线偏好监控" --timeout 3600 -- python3 codeact/scripts/midline_preference_monitor.py
```

`health_run` 是统一包装器：超时控制 + 失败落库 + 告警抑制，**不要去掉**。

---

## 5. 仓库外依赖（必须单独提供）

这些代码/技能**不在三个仓库里**，迁机时需要从原机拷贝或重新实现：

| 依赖 | 原机位置 | 谁在用 | 缺失后果 |
|---|---|---|---|
| **成长股选股器** | `<主对话>/成长股选股器/scripts/growth_screener_dual.py` | `case/sector_screening_v4/_run_growth.py` | 板块筛选 v4 的成长股部分跳过；已改为**明确报错**而非静默崩 |
| **IMA skill（`ima-knowledge-base/`）** | `/Coze/Drive/飞书_cli/skill-drafts/ima-knowledge-base/` | `case/tugou_archive`（上传知识库） | 土狗归档抓完正文也传不上去，日志报 `IMA_API_SCRIPT` 不存在 |
| **lark-cli 登录态** | `/app/data/所有对话/主对话/.feishu_cli/` | `case/tugou_archive/run_daily.sh` 预检 | 预检失败，直接告警退出 |

> `DailyPredict`（risk_v52）和 `vol_surface_runner.py`（vol_surface_vix）**已不在这个表里** ——
> 它们 2026-11-25 已整体迁进 `precious-task` 仓的 `case/`，代码、`pipeline.py`、
> `trading_day_check.py` 全部入库，不再依赖 `<workspace_a>`。详见 §3.1。

解决方式：
- 优先：把这几块也迁进仓库（后续单独提 PR）
- 临时：拷到新机器，用 `GROWTH_SCREENER_DIR` / `CASE_DRIVE_ROOT` / `CASE_APP_DATA` 指过去

IMA skill 与飞书 CLI 登录态的搬法（原机 → 新机）：

```bash
# IMA skill（116 KB，含 ima_api.cjs / cos-upload.cjs / meta.json）
mkdir -p /opt/legacy/drive/飞书_cli/skill-drafts
scp -r <原机>:"/Coze/Drive/飞书_cli/skill-drafts/ima-knowledge-base" \
       /opt/legacy/drive/飞书_cli/skill-drafts/

# 飞书 CLI 登录态
mkdir -p /opt/legacy/appdata/所有对话/主对话
scp -r <原机>:"/app/data/所有对话/主对话/.feishu_cli" \
       /opt/legacy/appdata/所有对话/主对话/

# 然后在启动环境里指过去（写进 /etc/environment 或 start_all 的 env）
export CASE_DRIVE_ROOT=/opt/legacy/drive
export CASE_APP_DATA=/opt/legacy/appdata
export LARKSUITE_CLI_CONFIG_DIR=/opt/legacy/appdata/所有对话/主对话/.feishu_cli
export LARKSUITE_CLI_DATA_DIR="$LARKSUITE_CLI_CONFIG_DIR"
```

`case/tugou_archive/tugou_archive.py:55` 就是
`Path(_cp("/Coze/Drive/飞书_cli/skill-drafts/ima-knowledge-base"))` ——
设了 `CASE_DRIVE_ROOT` 就自动指到新位置，不用改代码。

### 5.1 期权 7 条日更依赖 `options_utility` 同级仓

`case/options_*` 这 7 个目录里**只有薄包装**（`run.py` 定位 + 拼环境 + 防双跑），
策略代码一行都不复制。定位顺序：

```
$OPTIONS_UTILITY_ROOT  →  ../options_utility  →  ../options-utility  →  <老云盘>/options（迁移期兜底）
```

特征文件是 `alert/engine.py`，找不到就打印：

```
[error] 找不到 options_utility 仓（特征文件 <options_utility>/alert/engine.py）
        请与 stock-dashboard 同级 clone，或设 OPTIONS_UTILITY_ROOT。
```

所以新机器**必须** clone 第三个仓，且放在 `stock-dashboard` 的同级
（`start_all.sh up` 会自动找到并 export `OPTIONS_UTILITY_ROOT`）。

这 7 条的入口文件在 `options_utility` 里分别是：

| case | options_utility 入口 |
|---|---|
| `options_midday` | `alert/reports/midday.py` |
| `options_pcr_close` | `alert/tasks/oi_pcr.py close` |
| `options_oi_daily` | `alert/reports/oi_daily.py` |
| `options_pcr_macd` | `alert/tasks/pcr_macd_task.py batch` |
| `options_basis_daily` | `calc/basis_daily.py` |
| `options_close_monitor` | `alert/reports/close.py` |
| `options_skew10` | `alert/tasks/skew10.py` |

自检（应打印出绝对路径，不是空串）：

```bash
cd /opt/data/stock-dashboard
python3 -c "
import sys; sys.path.insert(0,'case/_shared')
import options_daily_runner as r
print(r.options_root())"
```

**注意防双跑守卫**：`options_daily_runner.run()` 会先查 `crontab -l`，
发现同名 `--name "期权OI日报"` 之类的行还在，就打印 `[skip] ... 本任务跳过以免双跑`
并 **exit 0**。所以老机器上手工跑这 7 条会「不报错也不干活」，属预期；
新机器没有 crontab 才真正执行。

### 5.2 建议手工搬的状态文件（不搬最多重推当天，不会全量回补）

有两个任务把「跑到哪了」记在**脚本同目录**（`Path(__file__).parent`）。代码迁过来、
状态文件没跟，程序会当成「从没跑过」。

> **实测口径（2026-11-26 更正）**：不搬**不会**把历史内容全量重推。
> - `hurst_daily.py` 一次只算一个交易日（默认最近交易日），没有回补循环 ——
>   最坏情况是当天那张卡再推一次。
> - `tugou_archive.py --mode daily` 只抓当天；`upload_to_ima()` 上传前先调
>   `check_repeated_names`，同名直接 `[SKIP] 文件已存在` —— 当天最多多抓一次正文，
>   IMA 那边大概率直接挡掉。
>
> 所以这一节是「建议搬」，不是「不搬就翻车」。真正必须搬的是 §2 的四类凭据，
> 缺了那是直接跑不起来。

| 状态文件 | 原机位置 | 大小 | 新机目标位置 | 谁在用 | 丢失后果 |
|---|---|---|---|---|---|
| `hurst_state.db` | `/opt/coze/.fuse_drive/hurst_指数/hurst_state.db` | 576 KB（`hurst_daily_cache` 66 行） | `case/hurst_daily/hurst_state.db` | `case/hurst_daily/hurst_daily.py:84` | 当天午报可能再推一次 |
| `archive_state.json` | `<主对话>/土狗归档/archive_state.json` | 103 KB | `case/tugou_archive/archive_state.json` | `case/tugou_archive/tugou_archive.py:48` | 丢本地「已传」记录；当天重抓，IMA 重名检查会挡掉 |
| `playwright_state.json` | `<主对话>/土狗归档/playwright_state.json` | 763 KB | `case/tugou_archive/playwright_state.json` | `tugou_archive.py:49`（**代码零引用**） | 无（历史遗留文件，搬不搬都行） |
| `articles/`（目录） | `<主对话>/土狗归档/articles/` | 351 条 / 1.6 MB | `case/tugou_archive/articles/` | `tugou_archive.py:47`（正文缓存） | 历史正文缓存丢，重抓要再走一遍 lark-cli |

搬运（新机上执行，从原机拉）：

```bash
cd /opt/data/stock-dashboard          # 新机仓库根

# Hurst（sqlite，直接拷）
scp <原机>:/opt/coze/.fuse_drive/hurst_指数/hurst_state.db  case/hurst_daily/

# 土狗（json + 历史文章目录）
scp <原机>:"<主对话>/土狗归档/archive_state.json"     case/tugou_archive/
scp <原机>:"<主对话>/土狗归档/playwright_state.json"  case/tugou_archive/
scp -r <原机>:"<主对话>/土狗归档/articles"            case/tugou_archive/
```

搬完验证（三个都必须非零，且 db 能查出行）：

```bash
ls -l case/hurst_daily/hurst_state.db \
      case/tugou_archive/archive_state.json \
      case/tugou_archive/playwright_state.json
ls case/tugou_archive/articles | wc -l                                  # 期望 351 上下
sqlite3 case/hurst_daily/hurst_state.db 'select count(*) from hurst_daily_cache'   # 期望 66 上下
python3 -c "import json;d=json.load(open('case/tugou_archive/archive_state.json'));print(len(d))"
```

> **这四个路径已写进 `.gitignore`** —— 它们是本机运行态数据，`git status` 里不该出现，
> 也**绝不能 commit**（含历史推送记录，体积不小）。换机器只能手工搬，没有别的路径。
>
> 换机器后第一次跑，看日志确认走的是增量：土狗日志里应出现「跳过: N」而不是
> 一大片「[OK] 上传成功」；Hurst 卡片只该有当天一个交易日。


---

## 6. 迁机自检清单

```bash
cd /opt/data/stock-dashboard

# ① 路径口径全绿
python3 case/_lib/paths.py
#   datahub_root     存在  /opt/data/datahub-multi-tier
#   legacy_workspace 存在  ...（或缺失，缺失时确认 CASE_WORKSPACE 是否该设）
#   feishu_dirs[0]   存在  .../case/sector_screening_v4

# ② 飞书推送真身能找到
python3 -c "
import sys; sys.path.insert(0,'case/sector_screening_v4')
import feishu_push
print('真身:', feishu_push._CANON)
print('存在:', __import__('os').path.exists(feishu_push._CANON))
"

# ③ data_hub 能 import
python3 -c "import data_hub; print(data_hub.__file__)"

# ④ 凭据四处齐（退出码 0 才算过）
python3 setup.py --check                       # ① secrets.json
test -s ../datahub-multi-tier/options/config/private.py && echo "② private.py OK"
test -s ../options_utility/config/private.py    && echo "③ options private.py OK"
test -s ~/.config/ima/api_key && test -s ~/.config/ima/client_id && echo "④ IMA 凭据 OK"

# ⑤ 期权仓能被定位到（应打印绝对路径，不是空串）
python3 -c "
import sys; sys.path.insert(0,'case/_shared')
import options_daily_runner as r
print('options_root:', r.options_root() or '(空 —— 7 条期权任务会失败)')"

# ⑥ IMA 接口通（土狗归档用；只打前 120 字节）
node /Coze/Drive/飞书_cli/skill-drafts/ima-knowledge-base/ima_api.cjs \
  openapi/wiki/v1/search_knowledge_base \
  '{"query":"沧海一土狗","cursor":"","limit":20}' | head -c 120

# ⑦ 每个在跑的任务 dry-run（不会真的推送）
for t in case/*/task.json; do
  python3 -c "
import json,sys
j=json.load(open('$t'))
print(j.get('key','?'), '|', j.get('cmd','')[:70])
"
done
```

---

## 7. 与原机的行为差异

| 项 | 原机 | 新机器 |
|---|---|---|
| 数据来源 | 云盘 `/opt/coze/.fuse_drive/...` + 本机 `/root/work/...` 两份副本 | 只有仓库一份，不再有「读到另一套库」的风险 |
| 历史数据 | `datahub-multi-tier/output/` 47M（含 `margin_detail.db` 43M） | **不迁移**，按需重新算 |
| 期权算法 | 本机/云盘各一份 `datahub-multi-tier/options/`（未跟踪） | 用公开仓 **`options_utility`**（同级 clone）；`case/options_*` 只放薄包装，不复制算法 |
| IMA skill | 云盘 `/Coze/Drive/飞书_cli/skill-drafts/ima-knowledge-base/` | 不在任何仓库，需单独搬（§5）；可用 `CASE_DRIVE_ROOT` 重定向 |
| 飞书 CLI 登录态 | `/app/data/所有对话/主对话/.feishu_cli/` | 不在任何仓库，需单独搬（§2.3）；可用 `CASE_APP_DATA` 重定向 |
| `modules.json` | 本机手写、含绝对路径、不入库 | **入库**，`cwd`/`cmd` 全占位符，换机器不用改 |
| 云盘兜底 | 框架层曾按云盘兜底 | 框架层已删兜底：解析不到就报错；case 脚本的旧根字面量走 `_cp()` 重定向 |

**注意**：框架层（`datahub_path.py` / `hub_root.py` / 启动脚本）的云盘兜底已删除，
解析不到 DataHub 就显式报错；case 脚本里的旧根字面量统一走 `_cp()`，可用
`CASE_DRIVE_ROOT` / `CASE_FUSE_ROOT` 重定向，不设时原样返回。

---

## 8. 已知遗留

- `send_email_default`（`circulation_daily.py` 掉期告警邮件用）在原机云盘上**已经不存在**，
  仓库里也没有。这条 import 一直是死引用，被 `try/except` 吞掉。要恢复得先找到实现。
- `modules.json` 里 `risk_v52` / `vol_surface_vix` 的代码不在仓库（见 §5）。
- `case/style_drift_monitor/datahub_refactor/e2e_*/` 等 16 处历史快照仍写死云盘路径，
  它们是重构前后的对比快照，不参与运行，本次未改。
- 土狗归档的 `playwright_state.json` 代码里已标「已废弃，保留兼容」，全仓零引用；
  仍列在 §5.1 是因为旧部署可能还留着，搬不搬都不影响运行。
- IMA skill 版本（1.1.9 vs 服务端 1.1.10）的每日首调失败见 §2.2，属于上游脚本行为，
  本仓库不改它；要根治就升级 skill 或设 `IMA_SKILL_VERSION`。
