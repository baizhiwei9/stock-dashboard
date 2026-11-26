# 给 Agent / 外部程序读的数据契约

看板页面是给人看的 HTML。程序（多 Agent 底座、日报脚本、外部监控）要读，走
`snapshot.py`——它复用 `build.py` **同一套** `data_hub.tech` 接口，口径与页面完全一致，
不是另算一份，也不是爬自己的页面。

```bash
python3 snapshot.py --out snap.json          # 落文件
python3 snapshot.py --codes 000300.SH        # 只看部分指数
python3 snapshot.py --futures                # 额外带期货实时快照（联网，慢）
```

## 输出结构

```json
{
  "generated_at": "2026-11-24 19:30:00",
  "as_of": "2026-11-23",
  "option_as_of": "20261124",
  "indices": {
    "000300.SH": {
      "name": "沪深300",
      "metrics": {
        "chanlun.bull_score": {"value": 34.0, "name": "多头评分", "unit": "分",
                               "direction": "higher_bullish",
                               "as_of": "2026-11-23", "quality": "ok"}
      },
      "option": {"atm_iv": 18.27, "atm_iv_pk": 67.6, "iv_skew": 9.31,
                 "pcr_oi": 0.68, "pcr_vol": 0.71, "vix": 19.22, "n_hist": 500}
    }
  },
  "coverage": {"n_indices": 5, "n_option": 5, "missing": ["000016.SH"]}
}
```

## 四条约定

**1. key 一律是指数代码，不是 ETF。**
`000300.SH` / `000905.SH` / `000852.SH` / `000688.SH` / `000016.SH` / `399006.SZ`。
指数与它的 ETF、期权标的在 `data_hub.tech.registry` 里配成 pair，取值时哪边有数据用哪边。
这样"看风险"时结构分解和期权定价能落在**同一个 key** 上。

**2. 库里没有的指标不出现。**
不补零、不拿旧值顶替、不返回 `0` 假装有数据。缺什么在 `coverage.missing` 里明说。

**3. 期权 IV / Skew 已转成 %。**
库内是小数，快照里 ×100。分位（`atm_iv_pk` 等）样本不足 60 个返回 `null`——
宁可显示没有，也不给"1 个样本 = 100% 分位"的假精确。

**4. `direction` 只表达方向，不表达结论。**
`higher_bullish` / `higher_bearish` / `neutral` 说明"这个数往上走偏多还是偏空"。
多空判断归调用方，取数层不下结论。

## 与 multi-agent 底座的关系

[multi-agent](https://github.com/baizhiwei9/multi-agent) 的
`integrations/stock_dashboard.py` 把同一批指标注册成 `data` capability 的证据条
（`tech_structure` / `tech_option` / `tech_evidence`）。

它直接 import `data_hub.tech` 读库，比走 HTTP 快；本快照面向的是**不方便装 data_hub
的调用方**（别的机器、别的语言、只想要一份 JSON 的场景）。

两条路径读的是同一个库、同一套契约，不会对不上。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATA_HUB_PATH` | 优先 `DATA_HUB_PATH` → `DATAHUB_DIR` → `EDB_REPO`，再同级 `../datahub-multi-tier`，最后 `/opt/coze/.fuse_drive/dataHub_三级体系` | `data_hub` 包所在仓库根 |
