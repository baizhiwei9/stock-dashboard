# 股市监控画布 · 每日看板

所有历史报告 → 一个 SQLite 事实库 → 一个单文件 HTML 看板。每天自动更新，纯 Python，零 token。

## 链路

```
$DASH_BASE 下的 7 类历史报告（md）   ← 默认指向本地数据根，可用环境变量覆盖
                │
                ├── 市场情绪监控/outputs/           风险预测日报      36 份
                ├── 市场情绪监控/outputs/           流动性风险偏好    36 份
                ├── 环流日报/outputs/               环流日报          37 份
                ├── 结构因子分析/outputs/           结构因子分析      31 份
                ├── 国债期货四因子/                 国债四因子周报    23 份
                ├── 库存信贷周期/outputs/           库存信贷周期月报   4 份
                ├── 热点概念追踪/outputs/           热点概念周报       8 份
                │
                └── 市场情绪监控/data/options_cache/style_drift.db  daily_metrics（451 天 × 4 标的）
                            │
                     ingest.py（8 个解析器）
                            ▼
                     facts.db  26237 条 / 15 域
                            │
                    build_html.py（只读）
                            ▼
                     index.html  约 106 KB / 8 栏目 / 25 张图
                            │
                    run_daily.sh ← crontab 工作日 22:10
```

## 文件

| 文件 | 作用 |
|---|---|
| `ingest.py` | 解析全部报告 + 期权库，增量写入 `facts.db`。改解析逻辑必须升 `PARSER_VER` |
| `facts.db` | 事实库，单表 `facts(date,domain,entity,metric,value_num,value_text,source,ingested_at)` |
| `build_html.py` | 从 `facts.db` 只读直读，生成 `index.html`。不复采行情、不插值 |
| `tmpl_css.txt` | 深色金融主题 CSS（构建时注入） |
| `tmpl_js_core.txt` | `drawChart()` + `legend()` 工具函数（构建时注入） |
| `run_daily.sh` | cron 入口：先 ingest 再 build，结果写 `daily.log` |
| `index.html` | 产物，每天覆盖 |
| `daily.log` | 运行日志，超 2000 行自动裁剪到 1000 行 |

## 设计要点

**单一长表，加栏目不动 schema。** 新数据源 = 写一个新 `parse_xxx()`，把结果 `F.add(date, domain, entity, metric, value, text)` 进去，然后在 `build_html.py` 加一个 `build_xxx()` 返回 `(kpis, charts, tables)` 三元组，注册进 `PANELS`。表结构永远不变。

**每条数据可回溯。** `source` 字段存原始文件名，出问题能直接定位到哪份报告哪一行。

**解析器版本机制。** `ingest_log.ver` 记录入库时的 `PARSER_VER`，`done` 集合只认当前版本的记录。改了正则不升版号，增量模式会跳过已入库文件，改了等于没改。

**前后端职责切分。** Python 端算好 KPI 文本、表格行、图表配置（labels + series 的 JSON），JS 端只做 `drawChart()` 渲染和 tab 切换。不在浏览器里做数据处理，避免大 payload 和前端逻辑膨胀。

**格式化函数不能 JSON 序列化。** 图表配置里用字符串枚举 `vfmtKey` / `yfmtKey`（`sign2`/`f1`/`f2`/`f3`/`f4`），JS 端用 `VF` / `YF` 映射表还原成函数。

**跨 metric 单位归一。** 同一个指标在报告里换过单位（如 TGA 有 `余额_万亿` 和 `余额_十亿` 两套），用 `pick()` 按日期取最新一条 + 倍率换算，`ser_multi()` 按日期合并成一条连续序列。

**Y 轴刻度自适应精度。** `tickText(v, range)` 按值域跨度决定小数位（≥20 取整、≥2 一位、≥0.4 两位、更小三位），避免相邻刻度取整后撞车。

## 已踩过的坑

- 报告写 `-345BP`（大写），正则必须 `re.I`
- 期权库 `atm_iv=0.2029` 是比例要 ×100，`vix=19.69` 已是百分点不能乘
- `timing` 六维度 entity 是 `① ETF资金流` 这种带圈号+空格的，不能 `LIKE '%维度%'`
- `--rebuild` 必须同时删 `.db` / `-wal` / `-shm`，否则 WAL 残留
- 占位符不要用会污染 JS 标识符的名字（`__DATA__` 会连带改坏 `window.__DATA__`）
- 报告里的两融余额是**沪市口径**（1.139 万亿），与 `margin` 域 akshare 两市合计（21727 亿）差约 10 倍，页面只用后者
- CGB1Y 在多数报告里标 `N/A`（数据源缺失），国债收益率曲线从 2Y 起画
- 中证500 的 CFC 分位全部 `N/A`，已从 CFC 图略去

## 运维

```bash
# 手动跑一次
cd <本目录> && ./run_daily.sh && tail -20 daily.log

# 全量重建（改了 schema 或解析器大改时）
python3 ingest.py --rebuild

# 只重建 HTML
python3 build_html.py

# 看 crontab
crontab -l | grep 股市监控
```

## 已知缺口

第 8 栏「自选股」是占位。全盘搜过云盘（文件名 + 文件内容，三个挂载点，关键词 自选/自选股/股票池/watchlist/portfolio/默尔根/亚丽），没找到「默尔根和亚丽」的自选股清单。云盘里现有的是 `长期计划/股票早晚报/计划配置.md` 的 10 只港股**持仓**，和 `limit_up_predictor/` 下的选股模型跟踪器，都不是自选股。

拿到清单（哪怕一句话：「默尔根看 A B C，亚丽看 D E」）就能接上。

## 环境变量（换机部署用）

全部有默认值，不设也能跑；换机器/换路径时覆盖即可。

| 变量 | 默认 | 作用 |
|---|---|---|
| `DASH_BASE` | `/Coze/Drive/蓁宝/所有对话/主对话` | 数据源根目录，各任务产出的报告都在这下面 |
| `DASH_DIR` | 脚本所在目录 | 看板工作目录（`run_daily.sh` / `publish.sh` 自动推导） |
| `DASH_OUT` | `<脚本目录>/data.json` | `build_data.py` 的中间产物路径 |
| `DASH_APP_ID` | 内置应用 ID | 发布目标 Pages 应用 |
| `DASH_URL` | 内置域名 | 发布后访问地址，只用于日志 |
| `DASH_APP_NAME` | 内置名称 | 应用显示名 |

```bash
# 例：数据在别处
DASH_BASE=/data/reports ./run_daily.sh
```

## 页面上的数据可信度提示

看板第一眼就得知道「这些数字是哪天的」，所以每个域各带一份新鲜度判定：

- **顶部角标**：`数据截至 2026-11-26`，超 SLA 转黄/红。
- **数据源状态表**（footer 折叠）：15 个域各自的「最新日期 / 落后几个交易日 / 状态」。
- **告警红条**：只有核心域（指数风险、期权、美元环流、中国流动性、两融、三维择时、流动性风险偏好）
  过期才报，避免天天挂一串黄条把人练脱敏。

判定用**工作日间隔**而非自然日（`_bdays_between`），周末不误报；各域 SLA 按真实更新节奏设
（两融 T+1 给 2 天、国债四因子周更给 5 天、库存信贷周期月更给 20 天）。

**加载遮罩**：`build_html.py` 一次性渲染全部 25 张图（含隐藏 tab），首屏需要时间。
`#boot` 全屏遮罩在 `load` 后双帧释放，另带 **8 秒兜底** —— JS 出任何错都不会把页面永久锁死。

## 产物不入库

`facts.db` / `data.json` / `index.html` / `daily.log` / `site/` 都是运行产物，走 `.gitignore`。
仓库里只有代码和模板。
