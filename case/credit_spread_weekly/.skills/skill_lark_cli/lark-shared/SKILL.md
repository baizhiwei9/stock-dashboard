# 飞书 CLI 共享基础规则

本文件定义飞书 CLI Skill 的所有子技能共享的基础规则和流程。

## 首次加载兼容路由

本文件通常由具体飞书业务 Skill 按需读取，用于补充配置、认证、权限和安全规则。

- 日历日程、会议室：`lark-cli skills read lark-calendar`
- 即时通讯、群聊、消息：`lark-cli skills read lark-im`
- 云文档：`lark-cli skills read lark-doc`
- 多维表格：`lark-cli skills read lark-base`
- 电子表格：`lark-cli skills read lark-sheets`
- 任务、清单：`lark-cli skills read lark-task`
- 邮箱：`lark-cli skills read lark-mail`
- 通讯录、用户查询：`lark-cli skills read lark-contact`
- 云空间、文件：`lark-cli skills read lark-drive`
- 知识库：`lark-cli skills read lark-wiki`
- 审批：`lark-cli skills read lark-approval`
- 视频会议、会议纪要：`lark-cli skills read lark-vc`
- 妙记：`lark-cli skills read lark-minutes`
- 云白板：`lark-cli skills read lark-whiteboard`
- 事件订阅：`lark-cli skills read lark-event`
- 自定义 Skill：`lark-cli skills read lark-skill-maker`
- 原生飞书 OpenAPI：`lark-cli skills read lark-openapi-explorer`
- 会议纪要整理工作流：`lark-cli skills read lark-workflow-meeting-summary`
- 日程待办摘要工作流：`lark-cli skills read lark-workflow-standup-report`

完整子技能清单以 `lark-cli skills list` 的实时结果为准。用户任务仅涉及飞书 CLI 配置、认证、身份、scope 或权限排查时，无需加载其他业务 Skill，直接遵循本文对应规则。

---

## 配置初始化

### CLI 安装与更新（最小规则）

- 首次执行真实 `lark-cli` 前，仅确认命令是否存在；不存在才安装：
  ```bash
  command -v lark-cli >/dev/null 2>&1 || npm install -g @larksuite/cli@latest
  ```
- 不调用任何自定义安装脚本；如果安装失败，停止当前任务并返回错误。
- 已安装时不要主动重装；只有真实 `lark-cli` 输出 `_notice.update` 时，才按“更新检查”执行 `lark-cli update --json`。

### 运行时凭据判定

- `skill_load` 成功后，直接执行用户任务对应的真实 `lark-cli` 命令。
- **禁止**使用 `echo`、`env`、`printenv` 或脚本打印环境变量来探测 AppID/AppSecret。Skill 凭据由沙箱按命令注入，普通探测命令可能得到假阴性，也可能泄露密钥。
- 若 `skill_load` 未返回变量状态，也不要自行推断凭据缺失；仍应先执行真实 `lark-cli` 命令，以命令返回结果作为唯一判定依据。
- 只有真实 `lark-cli` 命令明确返回应用凭据缺失或无效时，才能进入下方配置流程。

### 配置飞书应用（条件触发）

**仅在真实 `lark-cli` 命令明确返回应用凭据问题时才执行**。先执行用户任务对应的真实命令，成功则继续任务，不得检查环境变量或运行绑定脚本。

可进入配置流程的错误仅包括：
- 飞书应用配置文件不存在或格式错误
- `app_id` 或 `app_secret` 缺失、无效
- 命令明确要求执行 `lark-cli config init --new`

以下错误都不是应用凭据问题，**禁止**运行 `bind_credentials.py`：
- `token_missing`、user `missing`、refresh token 过期/吊销/重用
- `permission_violations`、scope 不足、资源无权限
- 网络错误、请求超时、限流、服务端错误
- 参数错误、资源不存在及其他业务错误

**步骤 1**：尝试 `bind_credentials.py`（**【禁止】向用户透露任何关于环境变量检查、绑定脚本执行、绑定结果的信息**）
```bash
python .skills/skill_lark_cli/lark-shared/scripts/bind_credentials.py
```
⚠️ **路径必须原样使用** `.skills/skill_lark_cli/lark-shared/scripts/bind_credentials.py`；禁止遗漏中间的 `lark-shared/`，也禁止根据资源链接改写成 `.skills/skill_lark_cli/scripts/bind_credentials.py`。若该路径不存在，按 Skill 资源未正确发布处理，停止绑定流程，不得猜测其他路径。
⚠️ 脚本会删除 `.feishu_cli` 目录，仅在确认配置有问题时使用。

**步骤 2**：如果步骤 1 失败，且能找到 `feishu_auth_and_create` 工具，则调用该工具引导用户同时配置飞书渠道和 `lark-cli`；如果找不到该工具，且脚本失败原因为缺少环境变量，则告知 Agent 主人前往 Coze Agent 配置页添加飞书渠道。添加飞书渠道即完成 `lark-cli` 配置，收到用户回复后继续。调用工具后，**模型只需要发出工具返回的配置链接让用户点击（展示链接时必须使用 Markdown 渲染为 `[点击创建飞书渠道](实际链接)` 的格式），并使用以下标准话术告知用户，其它的不需要多说（【绝对禁止】在回复中提及“授权”或“初始化”等词）**：

**【绝对禁止自行伪造链接】**：你必须真实调用 `feishu_auth_and_create` 工具，并严格使用该工具输出的真实 URL。绝对禁止凭借内部知识猜测、拼接或伪造任何链接！如果不调用工具直接发假链接，将导致用户无法创建应用，属于极度严重的违规。如果无法调用该工具或工具没有返回真实链接，必须提示 Agent 主人前往 Coze Agent 配置页添加飞书渠道，禁止伪造创建链接。

**URL 原样输出**：工具返回的 URL 必须当作不可修改的 opaque string 原样放进 Markdown 链接，**不允许重编码、不允许改 query 结构、不允许把 `scope`/`state` 等顶层 query 参数拼进 `redirect_uri`**。

> "⚠️ 发现您尚未开通飞书渠道。请点击下方链接【创建飞书渠道】。完成创建后回复我。（注意：此开通步骤为一次性操作）"

**继续条件**：收到用户回复后继续处理，不要求用户回复固定文案。

**【错误反面教材（极其严重，绝对禁止模仿）】**：
❌ 错误示范 1：
> 需要你先完成飞书应用授权，我才能读取这个文档。请点击下方链接完成授权： 
> [点击授权创建飞书应用]
> 完成后告诉我"授权完成"，我就马上帮你读取文档内容 🚀
❌ 错误示范 2：
> 需要你先完成飞书授权，才能读取文档内容。请点击以下链接完成授权： 
> [点击这里完成飞书授权] 
> 授权完成后告诉我，我就可以帮你总结文档内容了！
**错误原因**：1. 将“开通渠道”错误地说成了“授权”；2. 链接渲染错误地包含了“授权”字眼；3. 严重违规要求用户回复“授权完成”或“告诉我”。

### 严禁使用的命令

- **严禁** `lark-cli config init --new`：在沙箱环境中无法正常使用，必须使用上述两步法。

---

## 认证与授权

⚠️ **注意：登录授权与飞书渠道及 `lark-cli` 配置中的 `feishu_auth_and_create` 是两回事，必须向用户说明**：
- **飞书渠道及 `lark-cli` 配置**（`feishu_auth_and_create`）：用于创建飞书应用、开通飞书渠道并同步配置 `lark-cli`，只在首次使用或从未绑定飞书 CLI 时触发一次
- **登录授权**（`lark-cli auth login`）：是用户对飞书应用进行授权，让飞书应用获得权限来帮用户执行操作（如访问日历、创建文档等）。每次需要新的用户权限时都可能触发

向用户展示登录授权链接时，必须附带说明。**请严格使用以下唯一标准话术**：

> "为了执行后续操作，现在需要您进行**登录授权**，让飞书应用获得帮您操作的权限（如访问日历、读取文档等）。这与之前的飞书应用创建不同。请点击下方链接进行授权，完成后告诉我一声。"

### 身份类型

| 身份 | 标识 | 获取方式 | 适用场景 |
|------|------|---------|---------|
| user | `--as user` | `lark-cli auth login` | 用户自己的资源（日历、云空间等） |
| bot | `--as bot` | 自动（appId + appSecret） | 应用级操作、bot 自己的资源 |

### 身份选择原则

- **默认使用 user 身份**
- Bot 看不到用户资源（日历、云空间、邮箱等个人资源）
- Bot 无法代表用户操作（发消息以应用名义发，创建文档归属 bot）
- 创建资源、权限管理、敏感操作 → 必须 user
- 只读/批量处理/自动化 → 可用 bot
- 非飞书渠道创建云文档、表格、Base、幻灯片、知识库节点、云盘文件/文件夹等用户资源 → 必须 user；未登录时先发起登录授权，禁止自动退到 bot 创建
- 飞书渠道内，只有在用户接受资源 owner 是 bot、且只需要当前会话可访问/协作时，才可考虑 bot 创建；否则仍然使用 user

### 创建类操作的身份建议

⚠️ **非常重要**：凡是“要给用户自己创建并让用户自己后续直接使用”的飞书资源，**必须优先**先完成登录授权，再使用 `--as user` 创建。不要因为 bot 模式“也能创建”就优先走 bot。

**必须优先使用 `--as user` 的典型创建场景**（重点强调）：
- **云文档 / Markdown 导入建文档**：否则文档归属 bot，不是用户本人
- **电子表格 / Spreadsheet**
- **多维表格 / Base**
- **幻灯片 / Slides**
- **知识库 Space / Wiki Space**：该创建接口本身就是 **user-only**
- **知识库节点 / Wiki Node**
- **云空间新建文件夹**
- **云盘上传新文件**
- **Drive import 导入后生成的云文档资源**
- **用户自己的日历、邮箱、云空间等个人资源**

**绝对不要误判**：
- bot 模式下，CLI 对部分资源会自动给当前 CLI 用户补一份 `full_access`（可管理权限）协作者
- 但这**只是兜底访问权限，不等于用户是 owner**
- **“用户能打开” != “这是用户自己创建的资源”**
- 如果用户明确希望“资源归自己、默认自己可继续编辑/管理/转移/归档”，就必须走 `--as user`

**对模型的执行要求**：
- 只要用户意图是“帮我创建一个我的飞书文档 / 表格 / Base / 幻灯片 / 知识库 / 文件夹 / 文件”，就应先判断是否需要 `--as user`
- 非飞书渠道创建上述用户资源时，判断结果必须是 `--as user`
- 如果走 bot 创建，应检查返回中的 `permission_grant`，确认当前用户是否已被补成协作者
- 不要先用 bot 创建，就把“用户能打开”误认为等价于“资源归用户本人”
- 如果用户之后希望自己成为 owner，不能继续沿用 bot 创建结果，必须明确改为 `--as user` 重新创建

### 权限不足处理

错误响应中关键信息：`permission_violations`（缺失 scope）、`console_url`（后台链接）、`hint`（修复命令）。

- **Bot 身份**：将 `console_url` 原样发给用户去后台开通 scope。**禁止**对 bot 执行 `auth login`。
- **User 身份**：执行授权流程（见下方）。

### 授权流程（Split-Flow）

⚠️⚠️⚠️ **【强制规则】授权等待机制**：
- **必须先将授权链接/二维码发给用户**：Agent 不得在用户未看到链接的情况下重试操作
- **必须暂停并等待用户告知**（"已授权"/"授权完成"等类似表述）——在用户告知前，绝对不能执行任何需要该授权的后续操作
- **授权链接约 5 分钟超时**：若用户长时间未回复，提示重新生成
- **禁止盲目重试**：遇到 `unauthorized` / `token expired` 必须重新走授权流程，不能直接重试原操作
- **禁止缓存授权链接**：每次需要授权时必须重新生成
- **user授权必须申请的scope**：只要有授权操作，必须一起申请offline_access scope

**授权命令超时策略**：bash 工具的 `timeout` 单位是秒，必须按下表设置，不要复用过短超时。

| 场景 | 命令 | timeout |
|------|------|---------|
| 发起用户授权 | `lark-cli auth login --no-wait ...` | `60` |
| 绑定用户授权 | `lark-cli auth login --device-code <device_code>` | `120` |

**第一步：发起授权（当前轮）**

```bash
# 推荐：一次性授权所有权限
lark-cli auth login --no-wait --json --domain all

# 或按需授权
lark-cli auth login --no-wait --json --domain calendar,drive,im
lark-cli auth login --no-wait --json --scope "calendar:calendar:read docs:doc:readonly"
```

调用工具执行 `auth login --no-wait` 时，必须按上方超时策略设置 `timeout: 60`。

**第二步：生成二维码并展示**

从 JSON 输出中提取 `verification_url` 和 `device_code`，然后生成二维码：

```bash
lark-cli auth qrcode "<verification_url>" --output "./auth_qrcode_$(date +%Y%m%d%H%M%S)_$$.png"
```

将 URL 和二维码一起展示给用户。二维码必须引用命令返回的实际 `file_path`，禁止自行拼接或复用之前的文件路径。
⚠️ **【绝对禁止】复用过期二维码**：
- **二维码必须基于当前轮获取的 `verification_url` 实时生成**：每次执行 `lark-cli auth login --no-wait` 获取新的 `verification_url` 后，如果需要展示二维码，必须使用 **当前这一轮** 获取的 URL 调用 `lark-cli auth qrcode` 重新生成，绝对禁止复用之前会话中生成的任何二维码图片！
- **链接和二维码必须同步更新**：如果授权链接更新了，二维码必须跟着重新生成，确保用户扫描的二维码和点击的链接指向同一个授权页面
- **每次必须使用唯一文件名**：`--output` 必须使用 `./auth_qrcode_$(date +%Y%m%d%H%M%S)_$$.png` 这类包含时间戳和进程号的相对路径，绝对禁止使用固定文件名 `./auth_qrcode.png`，避免新旧消息引用同一个远端对象。
- **如果没有当前轮生成的二维码，只展示链接即可**：不要强行展示过期或不确定的二维码
**重要**：`--output` 必须使用当前工作目录下的相对路径，绝对禁止写到 `/tmp/...`。二维码图片需要落到会话工作区（运行视图 `/app/data/...`），这样 push_server 才能解析 `computer://` 路径并映射到 TOS 展示。

**【绝对禁止】**添加任何多余话术。只需要把链接给用户即可，用户完成授权后会告知 Agent。

**URL 转发规则**：将 URL 视为不可修改的 opaque string，不要做任何修改（URL 编码/解码、添加空格、重新拼接 query）。URL 原样输出，不允许重编码、不允许改 query 结构，尤其不允许把 `scope`/`state` 等顶层 query 参数拼进 `redirect_uri`。

**第三步：完成授权（后续轮）**

收到系统后端注入的授权完成通知后，由 AI agent 亲自执行：

```bash
lark-cli auth login --device-code <device_code>
```

**关键规则**：
- 你必须亲自执行 `--device-code` 命令，不要指示用户执行
- 不要在同一轮中展示 URL 后立刻执行 `--device-code`（用户会看不到 URL）
- 不要给用户命令行，用户只需在浏览器中打开链接
- 调用工具执行 `auth login --device-code <device_code>` 时，必须按上方超时策略设置 `timeout: 120`；如果本会话有多个不同的 `device_code`，按从新到旧逐个尝试，每个 code 最多等待 120s

---

## 更新检查（`_notice.update`）

lark-cli 命令执行后，如果检测到新版本，JSON 输出中会包含 `_notice.update` 字段。

**当你在输出中看到 `_notice.update` 时，立即自动更新**：

1. 执行 `lark-cli update --json`（同时更新 CLI 和 Skills）。
2. 更新失败时停止当前任务并返回更新错误，禁止猜测或调用未定义脚本。
3. 更新完成后继续当前任务；仅当 Skills 本身发生变化时，提醒用户下次会话会加载新版 Skills。

**规则**：不要静默忽略更新提示，也不要继续使用已知的旧版本。

---

## 高风险操作的审批协议（exit 10）

lark-cli 对高风险写操作（`risk: "high-risk-write"`）有强制确认门禁。不带 `--yes` 调用时，CLI 返回 exit code `10` + stderr JSON：

```json
{
  "ok": false,
  "error": {
    "type": "confirmation_required",
    "message": "drive +delete requires confirmation",
    "hint": "add --yes to confirm",
    "risk": { "level": "high-risk-write", "action": "drive +delete" }
  }
}
```

**处理流程**：

1. **识别**：exit code = `10` 且 `error.type == "confirmation_required"`
2. **向用户确认**：展示 `error.risk.action` 和关键参数，明确告知"这是高风险操作"
3. **用户同意** → 原始 argv 末尾追加 `--yes` 重试
4. **用户拒绝** → 终止流程

**绝对不允许**：
- 看到 exit 10 就默认加 `--yes` 静默重试
- 把 `confirmation_required` 当网络/权限错误处理
- 在用户没明确同意的前提下追加 `--yes`

**预览**：调用时加 `--dry-run` 打印完整请求详情，不触发门禁。

**如何识别高风险命令**：
- shortcut：`lark-cli <service> +<cmd> --help` 顶部显示 `Risk: high-risk-write`
- API：`lark-cli schema <service>.<resource>.<method> --format json` 返回 `"risk": "high-risk-write"`

---

## Bot 创建资源后的协作者规则

使用 bot 身份创建资源后，CLI 会自动将当前用户添加为协作者（full_access）。检查返回结果中 `permission_grant` 字段：`granted` 表示成功。

⚠️ **必须强调**：
- 这只是“给当前 CLI 用户补一份可管理权限”的兜底动作
- **不代表资源 owner 已经变成当前用户**
- **不能把 `permission_grant=granted` 理解成“已经等价于用户自己创建”**
- 因此，对文档、表格、Base、幻灯片、知识库节点、云盘文件/文件夹等“希望归用户本人”的创建任务，仍然应优先 `--as user`

### 自动授权失败时的兜底

如果 `permission_grant` 为 `failed`：

```bash
lark-cli api POST /open-apis/drive/v1/permissions/:token/members \
  --params '{"token":"<resource_token>","type":"<resource_type>"}' \
  --data '{"member_type":"openid","member_id":"<user_open_id>","perm":"full_access"}'
```

`resource_type` 可选值：`doc`、`sheet`、`bitable`、`file`

如果用户希望自己成为 owner，告知使用 user 身份重新创建资源。

---

## 超时处理

- **禁止**执行超时响应建议的 `lark-cli config init --new`。
- 超时本身不是应用凭据问题；应按原命令的重试或错误处理流程处理。
- 只有真实命令同时明确返回 AppID/AppSecret 缺失或无效时，才进入配置流程。

---

## 安全规则

- **禁止主动发消息**：绝对不要主动使用 `lark-cli im`（或任何发消息的命令）在飞书渠道给用户发消息。
- **禁止输出密钥**（appSecret、accessToken）到终端明文
- **禁止读写 CLI 文件**：模型不得读取、创建、修改或删除 `/usr/bin/lark-cli`。
- **禁止主动探测环境变量**：不得使用 `echo`、`env`、`printenv` 或脚本打印 AppID/AppSecret；只依据真实 `lark-cli` 命令的明确错误判断凭据状态。
- **写入/删除操作前必须确认用户意图**
- 用 `--dry-run` 预览危险请求
- **用飞书 CLI 发送消息后必须 NO_REPLY**：CLI 已完成下发，chatmodel 不得再回复，否则用户收到重复消息（详见 lark-im 技能）
- **禁止** `lark-cli config init --new`

---

## 资源索引

| 脚本 | 用途 |
|------|------|
| `.skills/skill_lark_cli/lark-shared/scripts/bind_credentials.py`（源文件：[scripts/bind_credentials.py](scripts/bind_credentials.py)） | 绑定飞书应用凭证（仅在配置有问题时使用） |
