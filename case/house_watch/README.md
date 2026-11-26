# house_watch —— 京沪楼市监控（取数与判定）

只盯**北京、上海**：成交面积/套数（网签）、挂牌均价、挂牌数量。判定三件事：
任一指标变化 **>10%**、KDJ/MACD 超买超卖与中枢穿越、价格对 **VWAP ±1σ/±2σ** 的新穿越。

**本任务不画图、不发信**：图与邮件在私有任务 `house_watch_chart`（单向依赖，读本任务的
`data/market.json`）。这样公开仓只有数据与判定，图片数据留在私有仓。

## 数据来源

| 指标 | 来源 | 口径 | 频率 |
|---|---|---|---|
| 成交面积 / 套数 | 北京住建委主题服务 + 上海开源归档（`data_hub.house.fetch_all`） | 网签，无成交金额 | 日 |
| 挂牌均价 | 中国房价行情桌面城市页 | 挂牌（要价），元/㎡ | 月 |
| 挂牌数量 | 中国房价行情**移动端**城市页 | 月度挂牌套数 | 月 |
| 价格曲线形状 | 70 城房价指数（`data_hub.house.fetch_price`） | 环比连乘还原，无量纲 | 月 |

**口径红线**：网签只有套数与面积，没有成交金额，所以**成交单价推不出来**；价格面板用挂牌均价
替代，实测月单独标出，更早那段是指数形态回溯，不是逐月实测。

## 产物

| 文件 | 内容 | 是否入库 |
|---|---|---|
| `data/market.json` | 图表任务消费的快照（价格/通道/KDJ/MACD/周度/挂牌/告警） | 否（运行时生成） |
| `data/creprice.json` | 挂牌价与挂牌量按月累积（每跑一次追加当期） | 否 |
| `out/latest.json` | 本期摘要（城市、告警数、取数步骤状态） | 否 |
| `out/alerts.json` | 告警与信号明细 | 否 |
| `out/report.md` | 人读版 | 否 |

## 快照契约（`data/market.json`）

```jsonc
{
  "asof": "2026-11-25 18:35", "threshold": 10.0, "window": 12,
  "cities": {
    "BJ": {
      "label": "北京", "basis": "北京存量房网签（不含商业）",
      "price": {"months": [...], "values": [...],            // 元/㎡（锚定后的近似单价）
                "anchor": {"month": "2026-10", "price_yuan": 53849},
                "real_months": ["2026-10"],
                "weighted": false,
                "bands": {"vwap": [...], "upper1": [...], "lower1": [...],
                          "upper2": [...], "lower2": [...], "sigma": [...]}},
      "kdj": {"k": [...], "d": [...], "j": [...], "last": {"k": 85.4, "d": 64.9, "j": 126.4}},
      "macd": {"dif": [...], "dea": [...], "hist": [...]},
      "weekly": [{"label": "11-16~11-22", "start": "...", "end": "...", "days": 7,
                  "deals": 6310, "area": 514887.84, "partial": false}],
      "listing": [{"month": "2026-10", "listings": 5869, "price_yuan": 53849}]
    },
    "SH": { ... }
  },
  "alerts": [{"kind": "change", "code": "area", "city": "SH", "metric": "成交面积",
              "latest": 514887.84, "prev": 467428.52, "change_pct": 10.15, "period": "周 11-16~11-22"}],
  "signals": [{"kind": "tech"|"vwap", "code": "...", "text": "...", "period": "月 2026-10"}],
  "notes": ["..."], "steps": {"index": {"ok": true}}
}
```

## 跑法

```bash
# 全量（网签 + 70 城指数 + 挂牌价/量）
python3 -u run.py

# 只重算判定、不联网（调阈值/改文案时用）
python3 -u run.py --skip-net --skip-index --skip-creprice --threshold 10

# 阈值调紧
python3 -u run.py --threshold 8
```

退出码：0 = 至少一城有可用快照；1 = 两城都缺 70 城指数（先补 `fetch-price`）。

## 依赖

- `data_hub.house`：`fetch_all` / `fetch_price` / `price` / `weekly` / `monthly`
- `requests`（抓中国房价行情）
- 不依赖浏览器：中国房价行情桌面与移动端都是纯 HTTP，带会话预热 + ≥2.5s 间隔即可