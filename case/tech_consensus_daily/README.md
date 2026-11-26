# 宽基指数技术分析多空平日报（tech_consensus_daily）

对 7 只宽基 ETF（含恒生科技）每日运行 6 类历史技术分析方法，汇总为**买 / 卖 / 平**三态观点，
先出**多空平汇总图**、再配**文字说明**，推送飞书（先图后文）。

## 1. 覆盖标的（7 只）

| 代码 | 名称 |
| --- | --- |
| sh510300 | 沪深300 |
| sh510500 | 中证500 |
| sh510050 | 上证50 |
| sz159915 | 创业板 |
| sh588000 | 科创50 |
| sh512100 | 中证1000 |
| sh513180 | 恒生科技（2026-09 用户确认纳入） |

## 2. 数据获取（多级代理回退 · 单一统一数据入口）

- 所有方法（6 类）**只通过统一入口 `data_fetch.load_all()` 取数**，不再各自直连数据源；全链路数据统一落 `data/*.csv` 缓存。
- 回退顺序固定（AK 筛在前、TS 筛兜底，一层缺数据自动落下一层）：
  **新浪(sina, L1) → 东方财富(em, L2) → 同花顺(ths, L3) → Tushare(ts, L4 兜底)**
- 缓存回填：缓存缺失或落后于上一交易日（T-1）时，自动沿多级链重拉并回填 `data/*.csv`；拉取全部失败时沿用旧缓存，避免丢数据。
- 数据日期口径：每天取**上一交易日（T-1）**完整日线（交易日历判定），不在盘中用当日不完整数据。
- Tushare token 不写入工程：优先读环境变量 `TUSHARE_TOKEN`，其次引用既有资产 `ts_config.py`（15k 积分代理 token，必须配代理地址 `https://ttx.dailyfetch.top/`，`_DataApi__http_url` 已由 data_fetch 自动设置），最后兜底工作区 `tk.csv`（`TK_CSV_CANDIDATES` 可扩展）；token 缺失/失效时该层自动跳过，不阻塞 AK 主链。
- `--no-cache` 强制重新拉取。

## 3. 技术方法清单（每日并行计算，每个方法对每只指数输出：多 / 空 / 震荡）

| 方法 | 信号口径（忠实于历史工作区） | 模块 |
| --- | --- | --- |
| 动力学MACD | 收>EMA50 且 DIF/ATR14>0 且 MACD柱/ATR14>0 → 多；镜像 → 空 | `signals/dxke.py` |
| 缠论分型 | 日线级别；最近 B1/B2/B3 买点 → 多；S1/S2/S3 卖点 → 空；机会距当前>15 根K线视为失效 → 震荡 | `signals/chan.py` |
| 均线双通路 | 收>MA40>MA160 且双线上行 → 多；收<双线且下行且 AC1<0 风险确认 → 空 | `signals/madc.py` |
| AC1-VMD | regime=good(AC1>0.05 且波动率<70分位) 且 VMD(K4) 低频斜率>0 且收>MA40 → 多；反向 → 空 | `signals/ac1vmd.py` |
| STMOM张量 | **13 标的面板权重（2021-06~2024-12 重训，5 种子平均，D_in=520）** numpy 前向推理；sig>+0.10 → 多，<-0.10 → 空，其余震荡；覆盖科创50/中证1000/恒生科技 | `signals/stmom.py` |
| 小波DWT | 收盘价 sym4 三层分解低频趋势；收>低频且斜率>0 → 多；镜像 → 空 | `signals/dwt.py` |

汇总规则：多=+1、空=-1、震荡=0、N/A 不计票。总分>0 → **买**（看多）；<0 → **卖**（看空）；=0 → **平**（震荡观望）。

> 说明：21 日横截面动量已按用户要求移除（2026-09），不含流形类（三通路/CSHT）方法。

## 4. 运行

```bash
# 仅生成汇总图与文字（本地）
python run_daily.py

# 生成并推送飞书（先发汇总图卡片，再发文字说明卡片）
python run_daily.py --send

# 生成并推送飞书+发送邮件
python run_daily.py --send --mail

# 强制重新拉取行情（不用本地缓存）
python run_daily.py --send --mail --no-cache
```

产物输出到 `output/`：
- `tech_consensus_summary.png` — 多空平观点汇总图（1500×990：行=6方法+汇总+近5日涨跌，列=7指数）
- `text_report.md` — 文字说明

## 5. 依赖与凭据

运行环境已具备（Python 3.12 沙箱）：`numpy pandas requests Pillow PyWavelets vmdpy akshare`（Tushare 兜底层需 `tushare`，可选）。
若换机运行，先执行 `python -m pip install numpy pandas requests pillow PyWavelets vmdpy akshare tushare`。

- 飞书凭据**不复制密钥进工程**：`feishu_push.py` 运行时从
  `workspace/skills/user/momentum-risk-control/feishu_config.py` 读取 APP_ID / APP_SECRET / WEBHOOK_URL。
- 邮件通道：钉钉企业邮箱 `dws` CLI（`~/.local/bin/dws`），发件 `baizhiwei9492@dingtalk.com` → 收件 `410119229@qq.com`，
  正文 LF 换行，汇总图以内联附件置于文首（先图后文）。`--mail` 触发。

## 6. 每日定时任务

使用调度器配置（**每天早晨 8:30 运行，基于上一交易日 T-1 数据给出当日观点**）：

```text
cron: 30 8 * * *
命令: python run_daily.py --send
工作目录: <本工程目录>
时区: Asia/Shanghai
```

- 飞书消息头：图卡片与文字卡片（长文按段拆多条时每条）均带统一消息头 `宽基指数技术分析多空平日报 · {T-1日期}`。
- 自动化：每天 8:30 基于 T-1 数据运行，飞书先发汇总图卡片、再发文字说明卡片；`--mail` 可选追加邮件。

## 7. STMOM 重训说明

- 脚本：`train_stmom_ext.py`（13 标的面板：原 STOM_POOL 10 标的 + 科创50 + 中证1000 + 恒生科技）
- 训练区间：2021-06-01 ~ 2024-12-31；5 种子平均；D_in=520；验证 Sharpe 1.536
- 权重产物：`signals/stmom_weights/model_weights.npz` + `hp.json`

## 8. 说明

- 仅技术分析信号合计，不含资金流/基本面；流形类方法（三通路/CSHT）按要求排除；21日动量已移除。
- 不构成投资建议。