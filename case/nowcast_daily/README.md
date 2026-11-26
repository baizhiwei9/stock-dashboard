# nowcast_daily —— 美国 Nowcast 日更

每天 **23:10（Asia/Shanghai）** 跑一次 = 美东 **10:10 EST / 11:10 EDT**。

为什么不是 22:00：克利夫兰 Inflation Nowcasting 每个工作日**约 10:00 ET** 才更新
（官方 FAQ：around 10:00 a.m. Eastern time），GDPNow 模型日也是 10:00 ET 发布；
22:00 = 09:00 EST，冬令时只能拿到前一交易日的版本，看板会晚一天。23:10 留 10 分钟
余量，全年都在官方更新之后。

看板构建要跟着放到 23:40（`modules.json` 的 `build.at`）。如果部署机的
`modules.json` 仍是 22:40，也不会更差：那一班读到的是前一晚 23:10 落的数据，和
原来 22:00 那班拿到的是同一份美东更新；只是要等下一次构建（次日 22:40）才能看到
当晚 23:10 的新值。要“当天更新当天上板”，两处时间必须一起改。

## 做什么

1. 从 DataHub EDB 取 10 条 `US.NOWCAST.*` 序列（官方端点直采，不读别的任务产物）；
2. 算「较一周前」（GDP 类）或「较上一目标月」（通胀类）的变动，**只用于展示**；
3. 写 `out/points.json` / `out/report.md`，pipeline 的 `emit` 阶段把指标落到
   DataHub 指标仓 `US.NOWCAST`。

看板「宏观」与「流动性 → 利率」两处都读同一批 `nowcast.*` 指标
（登记见 DataHub `data_hub/tech/registry.py` 的 nowcast 域），不各自落一份。

## 预警不在这里

预警只有一条链路：DataHub 的

```bash
python3 -m data_hub.edb.nowcast_alert        # 命中才发；--dry 只打印
```

口径是 **最新值 − 上一次取数时的值**（上一天 / 上一交易日，即上次真正取数），
状态文件 `NOWCAST_ALERT_STATE`（默认 `~/.cache/data_hub/nowcast_alert.json`）
里存快照；首次运行没有快照才回退到上一观测值。阈值（pp / %）都在
`data_hub.edb.nowcast_alert.DEFAULT_THRESHOLDS`。

本任务不再按 7 天窗口另判一套阈值——两套口径会变成两封邮件、两个结论。
`--force-email` 只用于人工要一份日报，不做阈值判定。

## 用法

```bash
python3 run.py                 # 正常跑（联网刷新 + 写产物）
python3 run.py --no-refresh    # 只读 EDB 缓存（离线排查）
python3 run.py --dry-run       # 不发邮件（--force-email 时也不发）
python3 run.py --force-email   # 人工日报：把当前读数发一封邮件
python3 -m pytest test_run.py -q
```

`deliver.email` 在 `task.json` 里保持 `false`：邮件由 `mail.py` 自己发，
不走框架投递，避免将来两边都开时重复发送。

## 口径

- 观测日 = 官方模型更新日；GDPNow / NY Fed 只保留**当前季度**轨迹，季度切换会重置；
- WEI 是四季度同比口径，不能和 GDPNow（环比折年率）直接比大小；
- 克利夫兰是目标月最后一版 nowcast，月中会随发布修订；
- 核心 PCE 季折年的目标季末在未来（例如 2026-12-31），**不直接当观测日**，也不用
  「夹到今天」——那会每个工作日写一条同值新观测，把 Q 频序列灌成日频。现在取
  「这次修订实际落地日」（末尾同值连续段的第一天），同一版修订反复跑落到同一个
  `obs_date`，仓储里是覆盖不是新增。见 `run.py::_pin_obs`。