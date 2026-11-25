# nowcast_daily —— 美国 Nowcast 日更

每天 **22:00（Asia/Shanghai）** 跑一次。这个时点对应美东 **9–10 点**：
克利夫兰联储 9 点后更新当日 nowcast，22:00 正好在它之后、又在看板 22:40 构建之前。

## 做什么

1. 从 DataHub EDB 取 10 条 `US.NOWCAST.*` 序列（官方端点直采，不读别的任务产物）；
2. 算「较一周前」（GDP 类）或「较上一目标月」（通胀类）的变动；
3. 变动超过阈值就发邮件（dws 主通道 + 163 SMTP 回退），同一数据日幂等；
4. 写 `out/points.json` / `out/report.md`，pipeline 的 `emit` 阶段把指标落到
   DataHub 指标仓 `US.NOWCAST`。

看板「宏观」与「流动性 → 利率」两处都读同一批 `nowcast.*` 指标
（登记见 DataHub `data_hub/tech/registry.py` 的 nowcast 域），不各自落一份。

## 邮件阈值（pp）

| 序列 | 比较窗口 | 触发线 |
|---|---|---|
| GDPNow | 较一周前 | 0.5 |
| 纽约联储 Nowcast | 较一周前 | 0.5 |
| WEI | 较一周前 | 1.0 |
| 核心 PCE 同比 nowcast | 较上一目标月 | 0.2 |
| CPI 同比 nowcast | 较上一目标月 | 0.3 |

不达阈值不发邮件；`--force-email` 可强制发一份日报。数据断档超过两倍窗口时
不计算变动（避免把跨季度水平差误报成单次下修）。

## 用法

```bash
python3 run.py                 # 正常跑（联网刷新 + 按阈值发邮件）
python3 run.py --no-refresh    # 只读 EDB 缓存（离线排查）
python3 run.py --dry-run       # 不发邮件、不写幂等状态
python3 run.py --force-email   # 忽略幂等，强制发日报
python3 -m pytest test_run.py -q
```

## 口径

- 观测日 = 官方模型更新日；GDPNow / NY Fed 只保留**当前季度**轨迹，季度切换会重置；
- WEI 是四季度同比口径，不能和 GDPNow（环比折年率）直接比大小；
- 克利夫兰是目标月最后一版 nowcast，月中会随发布修订；
- 核心 PCE 季折年的观测日是目标季末（可能在未来），落库时夹到今天，避免
  freshness 检查把未来日期当成最新。
