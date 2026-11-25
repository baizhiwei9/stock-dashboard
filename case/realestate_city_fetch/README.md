# 京沪房产成交取数（realestate_city_fetch）

每天 07:30 跑一次，把**北京、上海两地的房产成交数据**收进 `data_hub.realestate`，
并写一份人读摘要到 `out/summary.md`。

## 为什么要有这个任务

`case/realestate_daily` 的 fetch 阶段要 `weekly_input.json`，而这个文件此前**没有
任何生成方**（全仓只有消费方），所以地产日报一次都没跑起来过。这个任务补的就是
那段上游：把两地成交数据真实地采回来并落库。

## 两地怎么取

| | 北京 | 上海 |
|---|---|---|
| 源 | 北京市住建委「房地产数据主题服务」 | 归档 `ahbencat/shanghai_fangdi_data` + 官网直采 |
| 方式 | HTTP 抓页面 → 解析 | `git pull` → 解析 Markdown（基线）；直采补当日 |
| 浏览器 | 不需要 | 归档不需要；直采需要（过瑞数） |
| 粒度 | **只有最新一日 + 当月累计** | 逐日，2025-11 起 |

**北京**：官方只发最新一日和当月累计，没有历史序列。所以这个任务的价值在**日积月累**
——跑满 7 天才第一次有周度数字。第一次部署时周度会是空的，这是数据源的限制，不是 bug。

**上海**：`fangdi.com.cn` 挂瑞数（trp）动态防护，requests 一律 412，必须真浏览器。所以
定时这条链走归档仓（作者每日本地采集后推 Markdown，我们只 pull 和解析），**归档只当
历史基线**——它会滞后会断档（实测 2026-11-25 07:05 那次没提交，二手停在 11-23），而
一手房「今日签约」是首页滚动值、隔天即丢。当日增量由我们自己的直采补：

```bash
DUMATE_BROWSER_CLI=/path/to/dumate-browser-use/scripts/playwright-cli \
  PYTHONPATH=/path/to/datahub-multi-tier \
  bash /path/to/datahub-multi-tier/scripts/fetch_sh_via_browser.sh
```

直采实现在 `data_hub/realestate/sh_direct.py`：取 `old_house` 的 `#z_sell_num_p`（值在
兄弟节点，标签节点只有文字）、首页 `#sign_num`、trade 页「预/出售各类商品房」。二手那个
点位**时段性可用**（10:09 有值、18:15 空），脚本带重试与正则回退，取不到就不写；17:00 后
trade 页常为「0 套」，已判为无效丢弃。建议 07:10 与 22:40 各跑一次。

## 部署依赖

`run.py` 里 `from data_hub import realestate`，所以运行环境必须能 import 到 datahub：

```bash
pip install -e /path/to/datahub-multi-tier
# 或
export PYTHONPATH=/path/to/datahub-multi-tier
```

`git pull` 需要能访问 GitHub。网络偶发 TLS 中断，`sh.sync()` 内置 3 次重试。

## 产物

- `out/fetch.json` —— 本次抓取结果，含每个城市 `added/updated/total`
- `out/summary.md` —— 周度/月度摘要，可直接给日报或看板消费

数据本体落在 `data_hub/realestate/data/*_daily.json`（那边已 gitignore），本任务目录
不存数据，只存本次跑的记录。

## 退出码

- `0` 两地都成功
- `2` 有一边成功（部分可用，框架会记一次失败但不至于整条链断）
- `1` 全失败

## 口径

北京「住宅」不含商业办公，上海「二手房」含商业与车位，**两地数字不能直接横比**。
上海另有纯住宅口径（约 2.2 万套/月），需要时单独取。