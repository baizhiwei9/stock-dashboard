# 任务可见性清单

`public` 上牌面（按 section 归 Tab），`internal` 只跑与落库、不上牌面。
改可见性只改 `task.json` 的 `visibility`（internal 时 `section` 必须为空）。

## public（17）

| 任务 | 标签 | 说明 |
|---|---|---|
| `bond_three_factor` | bond | 利率三因子与 Slope 信号 |
| `bond_yield_daily` | bond | 国债收益率曲线监控 |
| `cn_us_inventory` | macro | 中美库存周期 |
| `futures_xsec_hedge` | futures | 期货横截面多空对冲模拟盘 |
| `m1_pmi_monthly` | macro | M1+PMI 月度监测 |
| `manning_flow` | liquidity | 曼宁资金流 |
| `margin_detail` | liquidity | 两融专项明细 |
| `margin_flow` | liquidity | 两融资金流与市场宽度 |
| `option_risk_cn_us` | stock | 中美期权偏度与风险释放 |
| `pmi_export` | macro | PMI 出口展望 |
| `realestate_daily` | realestate | 地产基础数据：北京/上海成交、土狗状态机、付鹏周期 |
| `rmt_cgap` | stock | 市场临界 C(t) 与 Gap |
| `rmt_daily` | stock | RMT 市场结构（噪音/共振） |
| `structural_factor` | stock | 结构因子（MFDFA 等） |
| `tech_consensus` | stock | 宽基技术面汇总（6 方法 多空平） |
| `us_treasury_weekly` | bond | 美债监控周报 |
| `volume_wavelet` | stock | 成交量小波信号 |

## internal（12）

| 任务 | 原标签 | 说明 |
|---|---|---|
| `analytical_mechanics` | - | 分析力学日报（中间算法，内部） |
| `dumate_daily_summary` | - | DoMate 每日总结（推送汇总，内部） |
| `factor_rotation` | - | 因子旋转周报（内部研究） |
| `five_system_concept` | - | 五系统概念轮动（选概念，内部） |
| `general_growth` | - | 将军成长股拓扑选股（选股，内部） |
| `hodge_laplacian` | - | Hodge/曲率结构（中间算法，内部） |
| `mad_weekly` | - | MAD 均线选股（选股，内部） |
| `ot_unified` | - | 最优传输 OT 统一日报（靠岸/多空，内部） |
| `sector_rotation` | - | 行业轮动机会风险（选行业，内部） |
| `tda_spd` | - | TDA-SPD 黎曼流形策略（选行业，内部信号） |
| `twosigma_rotation` | - | Two Sigma 行业动量（选行业，内部） |
| `xi_d_rrg` | - | ξ+D 结构风控 × 扩散 + RRG（行业轮动，内部） |
