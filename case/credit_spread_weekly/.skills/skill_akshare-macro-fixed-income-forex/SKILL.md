---
name: akshare-macro-fixed-income-forex-skill
description: |
  封装 AKShare 宏观经济、利率/银行、债券固收与外汇数据入口。覆盖 GDP/CPI/PPI/PMI/M2、
  社融/信贷/财政/工业/消费、LPR/SHIBOR/HIBOR、沪深债券、可转债、债券发行、
  外汇储备与指定货币对即期报价。
  当用户需要查询宏观经济指标、利率、银行间市场、债券收益率、固收市场、外汇或人民币汇率时使用。
  注意：实时行情类 op（如债券实时、外汇即期）仅在对应市场交易/报价时段有有效数据，
  非交易时段返回空字符串、空数组或上一报价为正常现象；宏观/历史/发行类数据不受交易时段限制。
---

# 宏观固收与外汇数据 Data Skill

通过 data-provider 网关访问 AKShare 覆盖的宏观经济、利率/银行、债券固收与外汇数据。
所有调用走 `python3 bin/_cli_wrapper.py`（内部转发到 `bin/akshare-macro-fixed-income-forex-cli`）。

> 本 skill 使用独立的 `tools/akshare-macro-fixed-income-forex-cli` tool；发布包里应带自己的 `bin/akshare-macro-fixed-income-forex-cli`，仅暴露宏观、固收与外汇领域入口。网关 provider name 仍为 `akshare`。
> **重要**：实时行情类 op 仅在对应市场交易/报价时段返回有效数据，非交易时段返回空字符串、空数组或上一报价为正常现象；宏观、历史收益率、债券发行等接口通常不受交易时段影响。

## 快速开始

```bash
python3 ./bin/_cli_wrapper.py list
python3 ./bin/_cli_wrapper.py schema forex-pair-quote

# 中国季度 GDP
python3 ./bin/_cli_wrapper.py call macro-china-gdp

# 美元/离岸人民币即期汇率
python3 ./bin/_cli_wrapper.py call forex-pair-quote --param symbol=USDCNH
```

## 失败降级策略（重要）

CLI 任意 op 在以下情况下视为“取数失败”：

- exit code 为 3/4/5（上游业务/服务/网络错误）。
- exit code 为 0 但返回数据为空、字段缺失或明显异常（如利率/汇率为 0、时间戳过期、实时行情返回空字符串且无法满足用户问题）。

取数失败时 agent 应主动降级，优先满足用户原始诉求，不要直接回复“查不到”：

1. **重试一次**：仅对 exit code 5 重试 1 次，避免偶发网络抖动。
2. **联网搜索兜底**：调用可用的联网搜索/网页浏览能力，从官方统计、市场基础设施或权威财经站点补齐数据。不要用 shell/curl 直连数据源。
   - 宏观指标：搜索“`{指标名} 最新数据`”“`{指标名} 历史数据`”，优先参考国家统计局、中国人民银行、财政部、海关总署、外汇局等官方来源。
   - 利率/银行间：搜索“`LPR 最新`”“`SHIBOR {日期}`”“`HIBOR {日期}`”，优先参考中国人民银行、全国银行间同业拆借中心、SHIBOR 官网、香港银行公会等来源。
   - 债券/固收：搜索“`中国国债收益率曲线`”“`{债券代码} 行情`”“`可转债 行情`”“`质押式回购 利率`”，优先参考中国债券信息网、中国货币网、新浪财经。
   - 外汇/人民币汇率：搜索“`USDCNH quote`”“`美元人民币 即期汇率`”“`人民币汇率中间价`”，优先参考中国外汇交易中心、中国人民银行、外汇局、Yahoo Finance、Google Finance。
3. **明确标注数据来源**：兜底数据必须在回复中说明“数据来自联网搜索（非本 Skill 实时接口）”，并附引用链接。
4. **分析类需求特例**：若用户要求宏观、利率、债券或汇率趋势分析但历史序列/实时行情失败，先用搜索补齐公开时间序列，再由 agent 自行计算环比/同比、均值、利差、MA/RSI 等基础指标；若无法补齐足够序列，则给出基于官方发布/公开资讯的定性判断并说明限制。

不要做：

- ❌ 不要试图绕过 CLI 直接 curl 数据源：所有请求必须通过网关转发，否则无法计费和鉴权。
- ❌ 不要在 CLI 成功且数据有效时擅自走联网搜索兜底；仅在确认取数失败后才降级。

## 环境变量

## Data-provider 配置

| 字段 | 值 | 说明 |
|------|----|------|
| Provider name | `akshare` | data-provider 网关路由名，四个 AKShare skill 共用 |
| Credential name | `DATA_PROVIDER_API_KEY` | Coze 托管凭证变量名，不是 provider name |
| SkillID | `7647829564114436106` | Coze skill 页面真实 ID |

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `COZE_DATA_GATEWAY_URL` | 否 | `https://data.coze.cn` | 网关域名 |
| `COZE_DATA_PROVIDER` | 否 | `akshare` | 网关路由 provider |
| `COZE_DATA_TIMEOUT_SEC` | 否 | `20` | 超时秒数 |

## Operations（25 个）

### 宏观经济

| Operation | 用途 |
|-----------|------|
| `macro-china-gdp` | 中国季度 GDP |
| `macro-china-cpi` | 中国月度 CPI |
| `macro-china-ppi` | 中国月度 PPI |
| `macro-china-pmi` | 中国官方 PMI，可用 `symbol=制造业/非制造业/综合` |
| `macro-china-m2` | 中国 M2 货币供应量 |
| `macro-china-trade-balance` | 中国进出口与贸易差额 |
| `macro-china-industrial-production` | 规模以上工业增加值 |
| `macro-china-consumer-retail` | 社会消费品零售总额 |
| `macro-china-new-credit` | 新增人民币信贷 |
| `macro-china-fixed-asset-investment` | 固定资产投资 |
| `macro-china-fiscal-revenue` | 财政收入 |
| `macro-china-social-financing` | 社会融资规模增量 |
| `macro-china-electricity` | 社会用电量 |

### 利率/银行

| Operation | 用途 |
|-----------|------|
| `macro-china-lpr` | LPR 贷款市场报价利率 |
| `macro-china-shibor` | SHIBOR，支持 `start_date/end_date` |
| `rate-hibor` | 香港银行同业拆息 HIBOR，支持 `start_date/end_date` |

### 债券/固收

| Operation | 用途 |
|-----------|------|
| `bond-zh-hs-spot` | 沪深债券实时行情 |
| `bond-zh-hs-daily` | 沪深债券历史日行情，必填 `symbol` |
| `bond-cov-spot` | 可转债实时行情 |
| `bond-cov-daily` | 可转债历史日行情，必填 `symbol` |
| `bond-treasure-issue` | 国债发行信息 |
| `bond-corporate-issue` | 公司债发行信息 |
| `bond-cov-issue` | 可转债发行信息 |

### 外汇

| Operation | 用途 |
|-----------|------|
| `macro-china-fx-reserves` | 中国外汇储备 |
| `forex-pair-quote` | 指定人民币货币对即期报价，必填 `symbol` |

## 常用查询

```bash
# 宏观总览
python3 ./bin/_cli_wrapper.py call macro-china-pmi --param indicator=制造业PMI
python3 ./bin/_cli_wrapper.py call macro-china-social-financing
python3 ./bin/_cli_wrapper.py call macro-china-new-credit

# 利率和银行间
python3 ./bin/_cli_wrapper.py call macro-china-shibor --param date=2024-01-02
python3 ./bin/_cli_wrapper.py call rate-hibor \
  --param start_date=2024-01-01 --param end_date=2024-01-31

# 固收市场
python3 ./bin/_cli_wrapper.py call bond-cov-spot
python3 ./bin/_cli_wrapper.py call bond-treasure-issue \
  --param start_date=2024-01-01 --param end_date=2024-12-31

# 外汇
python3 ./bin/_cli_wrapper.py call forex-pair-quote --param symbol=USDCNH
```

## 使用约定

- 先用 `schema <operation>` 看必填字段和日期格式，再 `call`。
- 日期参数一般使用 `YYYY-MM-DD`；少数商品/期货接口使用 `YYYYMMDD`，以 `schema` 输出为准。
- 本 skill 不负责股票、基金、期货期权实时行情；这些需求优先用对应 AkShare 领域 skill。
- 源码位于 `tools/akshare-macro-fixed-income-forex-cli/`；发布包里本 skill 目录应带自己的 `bin/akshare-macro-fixed-income-forex-cli`。
