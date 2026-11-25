> 唯一真源：`case/_lib/app_secrets.py`（读取）、`case/_lib/secrets.example.json`（模板）、
> `setup.py` / `install.sh`（安装门禁）。本文件是字段说明，供人工查阅。

# 敏感配置（secrets）

## 规则

1. **敏感值只放 `~/.config/stock-dashboard/secrets.json`**，代码、`modules.json`、`sections.json`、
   `task.json`、HTML 里都不许写明文。
2. `~/.config/stock-dashboard/secrets.json` 已 `.gitignore`，权限必须是 `600`。
3. 仓库只提交 `case/_lib/secrets.example.json`（占位值，字段清单）。
4. 安装/启动门禁：``python3 setup.py`（或 `./install.sh`）强制补齐必填项；缺项时安装不通过，
   调度器启动前也会再校验一次。

## 字段

| key | 必填 | 说明 |
|---|---|---|
| `api.password` | 是 | 看板管理密码 / 调度器 HTTP 鉴权（`X-SD-PW`）。≥10 位，禁止 `123456`。 |
| `api.registry_key` | 是 | 外部模块注册密钥（`X-SD-KEY`）。≥16 位，不要与管理密码相同。 |
| `feishu.app_id` | 否 | 飞书自建应用 App ID（发应用消息/上传图片时需要）。 |
| `feishu.app_secret` | 否 | 飞书应用 App Secret。 |
| `feishu.webhook` | 否 | 群机器人 Webhook。 |
| `feishu.chat_id` | 否 | 发送目标会话（`oc_...`）。 |
| `tushare.token` | 否 | Tushare Pro token。 |
| `tushare.official_token` | 否 | Tushare 官方站 token（备用通道）。 |
| `email.smtp_host` | 否 | SMTP 主机，默认 `smtp.163.com`。 |
| `email.smtp_port` | 否 | SMTP 端口，默认 `25`。 |
| `email.username` | 否 | SMTP 账号。 |
| `email.password` | 否 | SMTP 授权码（**不是**登录密码）。 |
| `email.sender` | 否 | 发件人地址。 |
| `email.recipients` | 否 | 收件人，多个用逗号分隔。 |
| `fred.api_key` | 否 | FRED API key（美债信用利差；缺省走无 key CSV）。 |
| `treasury.client_id` | 否 | 美财政部回购结果 API 的 client_id（缺省跳过回购明细）。 |
| `treasury.client_secret` | 否 | 同上，client_secret。 |
| `alert.webhook` | 否 | 任务失败/告警 webhook。 |

## 环境变量覆盖

临时覆盖不用改文件，环境变量优先于配置文件：

```bash
# 通用写法
export COZE_SEC_FEISHU_WEBHOOK='https://...'
export COZE_SEC_TUSHARE_TOKEN='...'
# 常用项的传统别名
export FEISHU_WEBHOOK='...'
export TUSHARE_TOKEN='...'
export SMTP_PASSWORD='...'
```

`$COZE_SECRETS` 可以显式指定另一个配置文件路径（设置后就只用它，不静默回退）。

安装时用环境变量喂必填项，`check_secrets.py --init` 会把它们落盘到配置文件，
后续启动不用再带同样的环境变量。

## 只认环境变量的老脚本

对读取 `FEISHU_WEBHOOK` / `SMTP_*` / `TUSHARE_TOKEN` 等环境变量的脚本：

```bash
eval "$(python3 case/_lib/app_secrets.py --env)"
```

调度器启动时会自动做同样的导出（api.password / registry_key 除外，不下发给任务子进程）。

## 历史泄露处置

这些凭据曾经以明文进过 git 历史（即使现在删掉，历史仍可检出）：

- 飞书 App Secret / 群机器人 Webhook
- Tushare token（含官方站备用 token）
- 163 邮箱 SMTP 授权码
- 看板管理密码 / 注册密钥

**必须全部轮换**（飞书后台重置 App Secret、群机器人重建、Tushare 重置 token、
邮箱重置授权码、安装时重新输入新的 api.password / registry_key）。
轮换后旧值作废，历史里的明文即失效。
