# 迁机部署手册（去云盘化）

> 目标：把本机（Coze 云主机 + 云盘 FUSE）上跑着的这套东西，原样搬到一台**普通 Linux 服务器**。
> 本文只覆盖「怎么让它在没有云盘的机器上跑起来」，不改任何业务逻辑。

---

## 0. 一句话流程

```bash
# ① 两个仓库放到同一个父目录，目录名不能改
/opt/data/
├── datahub-multi-tier/     # git clone <DH_REPO>
└── stock-dashboard/        # git clone <SD_REPO>

# ② 装依赖
cd /opt/data/datahub-multi-tier && pip3 install -r requirements.txt
cd /opt/data/stock-dashboard   && pip3 install -r requirements.txt

# ③ 填凭据（见 §3）
cp datahub-multi-tier/options/config/private.example.py \
   datahub-multi-tier/options/config/private.py
vi datahub-multi-tier/options/config/private.py

# ④ 生成调度配置（见 §4）
cd /opt/data/stock-dashboard
cp modules.example.json modules.json
# 把里面的 {{DATAHUB_ROOT}} {{REPO_ROOT}} {{WORKSPACE_A}} 等替换成本机真实路径

# ⑤ 自检
python3 case/_lib/paths.py
```

**目录名 `datahub-multi-tier` 和 `stock-dashboard` 必须保持原样**，所有路径解析都靠这两个名字。

---

## 1. 环境变量

全部可选 —— 不设也能跑，但迁机时建议显式设，省得靠探测猜。

| 变量 | 作用 | 不设时的行为 |
|---|---|---|
| `DATA_HUB_PATH` | DataHub 仓库根 | 向上找仓库根的 `datahub_path.py` → 同级 `../datahub-multi-tier` → 云盘兜底 |
| `CASE_WORKSPACE` | 老「蓁宝/所有对话/主对话」目录 | 云盘 `/Coze/Drive/蓁宝/所有对话/主对话` |
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

## 2. 凭据放哪

**只有一处**：`datahub-multi-tier/options/config/private.py`（已 `.gitignore`，不会入库）。

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

---

## 3. 调度配置 `modules.json`

`modules.json` **故意不入库**（`.gitignore:36-40`）—— 它描述的是「这台机器怎么跑」，含本机绝对路径。
仓库里只有 `modules.example.json` 模板。

16 个模块，`cwd` 全部是占位符：

| 占位符 | 含义 |
|---|---|
| `{{DATAHUB_ROOT}}` | datahub-multi-tier 绝对路径 |
| `{{REPO_ROOT}}` | stock-dashboard 绝对路径 |
| `{{WORKSPACE_A}}` | 跑 `risk_v52` / `vol_surface_vix` 的目录（见 §5） |
| `{{WORKSPACE_B}}` | 跑 `five_strategy` / `factor_daily` / `limit_up_*` 的目录 |
| `{{FEISHU_CLI_DIR}}` | lark-cli 配置目录 |
| `{{FEISHU_CHAT_ID}}` | 推送目标群 chat_id |
| `{{ALERT_EMAIL}}` | alert 级通知收件邮箱 |

```bash
cp modules.example.json modules.json
python3 - <<'PY'
import json, os
p = "modules.json"
s = open(p, encoding="utf-8").read()
for k, v in {
    "{{DATAHUB_ROOT}}":  "/opt/data/datahub-multi-tier",
    "{{REPO_ROOT}}":     "/opt/data/stock-dashboard",
    "{{WORKSPACE_A}}":   "/opt/data/workspace-a",
    "{{WORKSPACE_B}}":   "/opt/data/workspace-b",
    "{{FEISHU_CLI_DIR}}":"/root/.config/lark-cli",
    "{{FEISHU_CHAT_ID}}":"",
    "{{ALERT_EMAIL}}":   "",
}.items():
    s = s.replace(k, v)
open(p, "w", encoding="utf-8").write(s)
json.load(open(p))
print("modules.json 生成完成")
PY
```

然后按需删掉本机不跑的模块条目，`enabled: false` 也可以。

---

## 4. 高频任务（crontab）

**crontab 完全不受本次改造影响**，照原样搬过去即可。唯一要改的是路径前缀（如果新机器目录不同）。

原机 crontab 结构：

```
30 19 * * 1-5  cd <主对话> && PYTHONPATH=<DATAHUB> python3 -m data_hub.tech.health_run \
                 --name "中线偏好监控" --timeout 3600 -- python3 codeact/scripts/midline_preference_monitor.py
```

`health_run` 是统一包装器：超时控制 + 失败落库 + 告警抑制，**不要去掉**。

---

## 5. 仓库外依赖（必须单独提供）

这三块代码**不在任何仓库里**，迁机时需要从原机拷贝或重新实现：

| 依赖 | 原机位置 | 谁在用 | 缺失后果 |
|---|---|---|---|
| **成长股选股器** | `<主对话>/成长股选股器/scripts/growth_screener_dual.py` | `case/sector_screening_v4/_run_growth.py` | 板块筛选 v4 的成长股部分跳过；已改为**明确报错**而非静默崩 |
| **DailyPredict** | `<workspace_a>/DailyPredict/daily_risk_report.py` | `modules.json` 的 `risk_v52` | 风险日报 v52 不出 |
| **vol_surface_runner.py** | `<workspace_a>/vol_surface_runner.py` | `modules.json` 的 `vol_surface_vix` | 波动率曲面不出 |

解决方式：
- 优先：把这三块也迁进仓库（后续单独提 PR）
- 临时：拷到新机器，用 `GROWTH_SCREENER_DIR` / `{{WORKSPACE_A}}` 指过去

### 5.1 必须手工搬的状态文件（不搬 = 历史内容全量重推）

有两个任务把「跑到哪了」记在**脚本同目录**（`Path(__file__).parent`）。代码迁过来了、
状态文件没跟，程序会当成「从没跑过」——把历史内容**整个重推一遍**，比不迁机更糟。

| 状态文件 | 原机位置 | 大小 | 新机目标位置 | 谁在用 | 丢失后果 |
|---|---|---|---|---|---|
| `hurst_state.db` | `/opt/coze/.fuse_drive/hurst_指数/hurst_state.db` | 576 KB（`hurst_daily_cache` 66 行） | `case/hurst_daily/hurst_state.db` | `case/hurst_daily/hurst_daily.py:84` | Hurst 午报重复推送 |
| `archive_state.json` | `<主对话>/土狗归档/archive_state.json` | 103 KB | `case/tugou_archive/archive_state.json` | `case/tugou_archive/tugou_archive.py:48` | 土狗归档全量重跑 |
| `playwright_state.json` | `<主对话>/土狗归档/playwright_state.json` | 763 KB | `case/tugou_archive/playwright_state.json` | `tugou_archive.py:49`（已废弃，保留兼容） | 登录态丢失，需重新扫码 |
| `articles/`（目录） | `<主对话>/土狗归档/articles/` | 351 条 / 1.6 MB | `case/tugou_archive/articles/` | `tugou_archive.py:47` | 历史文章全丢 |

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
> 换机器后第一次跑，务必看日志确认是「增量」而不是「全量」：
> 土狗日志里出现大量「已归档，跳过」才对；Hurst 卡片应该只有当天数据。

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

# ④ 每个在跑的任务 dry-run（不会真的推送）
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
| 期权 `options/` | 只在云盘 + 本机，未入库 | **已入库**（本次 PR） |
| `modules.json` | 本机手写 | 从 `modules.example.json` 生成 |
| 云盘兜底 | 命中 | 探测不到，自动走仓库内副本 |

**注意**：代码里所有云盘路径都保留成了「最后兜底」，所以在原机上行为**完全不变**，
不会因为这次改造把正在跑的东西改坏。

---

## 8. 已知遗留

- `send_email_default`（`circulation_daily.py` 掉期告警邮件用）在原机云盘上**已经不存在**，
  仓库里也没有。这条 import 一直是死引用，被 `try/except` 吞掉。要恢复得先找到实现。
- `modules.json` 里 `risk_v52` / `vol_surface_vix` 的代码不在仓库（见 §5）。
- `case/style_drift_monitor/datahub_refactor/e2e_*/` 等 16 处历史快照仍写死云盘路径，
  它们是重构前后的对比快照，不参与运行，本次未改。
