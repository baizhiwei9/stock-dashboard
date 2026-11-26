# kline_v2 — Stockdata 跨市场历史 K 线

## 用途

按明确日期区间获取证券历史 K 线。该 operation 调用 Stockdata 的历史 K 线 V2 接口，不改变原有 `kline` 的参数或实现。

## 数据源

Stockdata `POST /external/v1/quotation/history-daily-kline-v2`。

## 调用

```bash
python3 ./bin/_cli_wrapper.py call kline_v2 \
  --param code=000001.XSHE \
  --param startdate=2026-08-01 \
  --param enddate=2026-08-26 \
  --param period=day \
  --param fq=none
```

## 入参

| 参数 | 必填 | 类型 | 默认 | 说明 |
|------|------|------|------|------|
| code | ✅ | string | — | 单个标准 `symbol.MIC`，如 `600519.XSHG`、`00700.XHKG`、`AAPL.XNAS` |
| startdate | ✅ | string | — | 闭区间开始日期，严格 `YYYY-MM-DD` |
| enddate | ✅ | string | — | 闭区间结束日期，严格 `YYYY-MM-DD` |
| period | ❌ | string | `day` | `day/week/month/quarter/half_year/year` |
| fq | ❌ | string | `none` | `none`=不复权，`qfq`=前复权，`hfq`=后复权 |

`kline_v2` 不接收 `indicators`。接口固定返回 `pre_close/open/high/low/close/volume/amt`，源数据缺失时对应值为 `null`。

## 支持范围

- 沪深北股票：`XSHG`、`XSHE`、`BJSE`
- 港股：`XHKG`
- 美股：`XNAS`、`XNYS`、`XASE`
- 沪深 ETF/LOF 与沪深指数：`XSHG`、`XSHE`

不支持美股 ETF/指数、港股基金及普通开放式基金行情。代码必须使用标准 MIC，不接受 `.SH/.SZ/.BJ/.HK/.O/.N`、DataYes 的 `.XBEI/.AMXO`，也不接受 `sh600519` 这类供应商格式。

## 返回字段

响应顶层只有 `count + data`，不返回旧接口的顶层 `code/period/fq/indicators`。

| 字段 | 类型 | 说明 |
|------|------|------|
| count | int | 实际返回条数 |
| data[] | array | K 线数组，按日期升序 |
| data[].symbol | string | 交易所原始证券代码，不带 MIC 后缀 |
| data[].date | string | 日期 `YYYY-MM-DD` |
| data[].pre_close | string/null | 前收盘价 |
| data[].open | string/null | 开盘价 |
| data[].high | string/null | 最高价 |
| data[].low | string/null | 最低价 |
| data[].close | string/null | 收盘价 |
| data[].volume | string/null | 成交量 |
| data[].amt | string/null | 成交额 |

金融数值按十进制字符串返回，调用方不要先转成二进制浮点再做精确金额计算。美股源数据没有 `pre_close` 和 `amt`，这两个字段返回 `null`；美股 `qfq/hfq` 依赖复权因子，因子缺失时接口会返回业务错误。沪深指数选择任意 `fq` 都返回同一份原始指数行情。

## 示例

```bash
# A 股前复权日 K
python3 ./bin/_cli_wrapper.py call kline_v2 \
  --param code=600519.XSHG \
  --param startdate=2026-01-01 \
  --param enddate=2026-08-26 \
  --param fq=qfq

# 港股周 K
python3 ./bin/_cli_wrapper.py call kline_v2 \
  --param code=00700.XHKG \
  --param startdate=2025-01-01 \
  --param enddate=2026-08-26 \
  --param period=week

# 美股日 K
python3 ./bin/_cli_wrapper.py call kline_v2 \
  --param code=AAPL.XNAS \
  --param startdate=2026-01-01 \
  --param enddate=2026-08-26
```
