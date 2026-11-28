# case/macro_monthly — 宏观三条线月报（订单 / 产出 / 物价）

月度宏观简报任务。**只关心三条线**：订单、产出、物价；每个月的推送是一张飞书交互式卡片（含 3 张图）
加一封内联图邮件，不再发五章长文。

| 线 | 指标 | EDB 序列 |
|---|---|---|
| 订单（制造业） | PMI 新订单、PMI 新出口订单 | `CN.PMI.NEW.ORDER`、`CN.PMI.NEW.EXPORT` |
| 订单（非制造业） | 服务业新订单、建筑业新订单、非制造业 PMI | `CN.PMI.SVC.NEW.ORDER`、`CN.PMI.CONS.NEW.ORDER`、`CN.PMI.NMFG` |
| 产出 | 出口当月/累计同比、PMI 生产、利润当月同比 | `CN.EXP.YOY`、`CN.EXP.CUM.YOY`、`CN.PMI.PROD`、`CN.PROFIT.MONTH.YOY` |
| 物价 | PPI 同比/环比、布伦特月均 | `CN.PPI.YOY`、`CN.PPI.MOM`、`GLOBAL.BRENT` |
| 耐用品 / 非耐用品 | PPI 耐用消费品、一般日用品 | `CN.PPI.DURABLE.YOY`、`CN.PPI.DAILY.YOY` |

## 入口

```bash
python run_monthly.py              # 全链路：取数 → 长版报告存档 → 简报 → 飞书+邮件
python run_monthly.py --dry-run    # 只取数 + 生成（含三张图），不推送
python run_monthly.py --no-email   # 只发飞书卡片
python push_brief.py               # 单独重发简报（读库不重新取数）
```

产物全部落在 `out/`：`brief_YYYYMM.md`（简报文本）、`brief_order|output|price.png`（三张图）、
`brief_email.html`（邮件正文存档）、`card.json` + `card.md`（飞书卡片原文，供展示页复用）；
长版报告 `report_YYYYMM.md|html` 也在 `out/`，**只存档、不推送**。

## 数据来源

全部经 `data_hub` 的 EDB（SQLite）读取，本 case 不直连统计局/海关/东财。取数统一在
`edb_refresh.py`（`EDB_UPDATE=1`）里联网刷新一次，其余脚本只读库。

- 统计局：规上工业企业利润、PPI、PMI 分项
- 海关总署：出口当月/累计同比
- ICE：布伦特结算价（`GLOBAL.BRENT`，日频，做月均）

## 环境变量（换服务器只需这几项）

| 变量 | 作用 | 缺省 |
|---|---|---|
| `DATA_HUB_PATH` / `DATAHUB_DIR` / `EDB_REPO` | 含 `data_hub/` 的工程目录 | `case/_lib/hub_root` 自动探测（优先环境变量） |
| `MACRO_OUT_DIR` / `MACRO_DATA_DIR` | 产物 / 中间数据目录 | `case/macro_monthly/out`、`data` |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 上传卡片图片（`im/v1/images`） | `case/_lib/feishu_creds` → secrets.json |
| `FEISHU_WEBHOOK_URL` | 卡片推送目标群 | 同上 |
| `DWS_CLI` | 钉钉邮箱 CLI（主通道） | `PATH` → `~/.local/bin/dws` |
| `EMAIL_FROM` / `EMAIL_TO` | 发件人 / 收件人 | `app_secrets` 的 `email` 段 |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USER` / `EMAIL_AUTH_CODE` | SMTP 回退通道 | `app_secrets` 的 `email` 段 |

仓库内**不含任何明文凭据**；凭据统一从 `~/.config/stock-dashboard/secrets.json`（或环境变量）读。

## 渲染要点（踩过的坑）

- 自定义机器人 webhook 只接受**卡片 1.0**：文本块用 `{"tag":"div","text":{"tag":"lark_md"}}`；
  用 `{"tag":"markdown"}` 会报 `11310 unsupported type of block`。
- 卡片图片需先以应用身份上传拿 `img_key`（`case/_lib/feishu_card.upload_image`），再放进 `img` 元素。
- 邮件用 dws 发送时，`--content` 传 HTML（实测支持 `h1/h3/strong/ul/br`），图片用
  `--inline-attachment` + 正文占位符 `[inline:文件名]`；dws 返回的 `success` 是**布尔 true**，
  判定要按 JSON 解析，不能只匹配 `"success": "true"` 字符串。
- 图表依赖中文字体（Noto Sans SC / Droid Sans Fallback）；缺字体时 `charts._pick_font()` 会退化到
  DejaVu 并出现方块，换机后先 `fc-list :lang=zh` 确认。

## 调度

`cron: 0 21 28 * *`（Asia/Shanghai，每月 28 日 21:00）。`task.json` 只做描述，实际调度由
DuMate 定时任务（job_94ab3e93-e839-4623-9c7b-7e388d115399）执行本目录的 `run_monthly.py`。

## 与迁移前（workspace/macro_monthly_report）的差异

1. 路径不再写死：`config.py` 改为「环境变量 → `case/_lib/hub_root`（本仓）/ `hub_paths`（precious 仓）→ 明确报错」，删掉了
   `WS = "/home/work/dumate/..."` 与 `ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0/feishu_config.py` 的硬引用。
2. 凭据不再从别的会话目录 `exec` 读取，改走 `case/_lib/feishu_creds` / `app_secrets`。
3. 输出目录 `output/` → `out/`（仓内 .gitignore 已覆盖 `case/*/out/`）。
4. **推送形态**：从「五章长文（利润/出口/情景/PMI剔除法/PPI 九步）」改为订单/产出/物价三条线简报 +
   三张图；长版报告仍生成，仅供存档。
5. 旧目录里的 `pusher.py`、`rich_push.py`、`pmi_push.py` 等已被 `push_brief.py` 取代，未迁移。

## 已知边界

- PMI 分项（新订单/新出口订单/生产）在 EDB 里只有 13 期，图只能画近一年；更早数据待 DataHub 回补。
- `CN.PPIRM.YOY` 在库内仅 1 期，暂不能画购进价格趋势，故物价图用 PPI + 布伦特。
- `macro_watch/_common`、`structural_factor/email_push.py` 仍指向旧目录 `workspace/macro_monthly_report`
  复用凭据；本 case 不依赖它们，可另行迁移（不在本次范围）。