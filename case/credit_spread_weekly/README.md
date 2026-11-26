# 宏观信用风险周度监控

基于公开市场数据，对三层风险传导链路进行周度监控，指标触发阈值时即时输出告警。

## 三层传导链路

```
L2 货币信用链（传导起点） → L3 银行层（传导中继） → L4 外汇层（传导放大器）
```

### L2 货币信用链
| 指标 | 阈值 | 说明 |
|------|------|------|
| SOFR - IORB 利差 | > 15bp | 流动性危机前兆（来自宏观流动性项目信用层） |
| 投资级(IG) OAS | 4周变动 > 50bp 或 单周跳升 > 20bp | ICE BofA US Corporate Index |
| 高收益(HY) OAS | 4周变动 > 100bp | ICE BofA US High Yield Index |
| CoreWeave 5Y CDS | > 1000bp | 重点信用标的 |
| Oracle 5Y CDS | > 300bp | 重点信用标的 |
| SoftBank 5Y CDS | > 400bp（观察项） | 信用风向标 |
| 回购压力 SOFR-GC | 走阔 > 10bp | 抵押品链预警信号 |

### L3 银行层
| 指标 | 阈值 | 说明 |
|------|------|------|
| 银行股ETF(KBE) | 单周跌幅 > 7% | 银行体系信用风险传导 |
| HY-银行股联动 | HY 4周>100bp 且 银行股下跌 | 传导确认信号 |

### L4 外汇层
| 指标 | 阈值 | 说明 |
|------|------|------|
| 美元指数(DXY) | 单周涨幅 > 2% | 全球流动性收紧信号 |
| USDCNH | 单周波动 > 1.5% 或 破7.0/7.2 | 汇率压力与资本外流 |

## 传导判定规则

| 触发层级 | 状态 | 含义 |
|----------|------|------|
| 0层 | 正常 | 信用环境稳定 |
| 1层 | 独立风险 | 单层触发，尚未跨层传导 |
| 2层 | 传导中 | 风险沿传导链蔓延 |
| 3层 | 系统性风险 | 完整传导链形成，建议避险 |

## 数据源

- **FRED（主源）**：SOFR, IORB, IG/HY OAS, 贸易加权美元指数, USDCNY
- **搜索兜底**：CDS（CoreWeave/Oracle/SoftBank）、KBE银行ETF、GC回购利率
- 数据缺失时标注"无数据"，严禁估算

## 目录结构

```
信用利差预警/
├── scripts/
│   └── credit_spread_monitor.py    # 主监控脚本
├── outputs/                        # 周报输出
│   └── YYYY-MM-DD_信用风险周度监控.md
├── data/
│   └── credit_monitor.db           # SQLite状态库（历史数据+告警去重）
└── config/
```

## 运行方式

```bash
# 周报模式（生成监控表 + 告警检查）
python3 scripts/credit_spread_monitor.py full

# 仅告警检查
python3 scripts/credit_spread_monitor.py alert

# 仅生成周报
python3 scripts/credit_spread_monitor.py weekly
```

## 告警规则

- 任一指标触发阈值，立即输出告警（同一天同一指标去重）
- 告警内容：触发指标、当前值、阈值、数据日期、数据来源、风险等级
- 传导升级（传导中/系统性风险）单独告警
