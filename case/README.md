# case/ —— Dumate 任务目录

本目录是 **dumate 分支**的唯一任务来源。看板本体（`build.py` / `scheduler.py`）不再内置计算，
所有取数与计算都落在 `case/<task>/` 里，由 `scheduler.py`（TaskManager）按各自 `task.json`
的 `sched` 定时执行，产出写入 DataHub 指标仓，再由 `build.py` 按 `section` 标签渲染。

一句话：**踢掉原 producers，有代码的任务搬进 case，跑完写指标仓，面板按标签展示。**

---

## 只搬程序文件

- **只放 `.py`**。数据、报告、图片、中间 json、缓存、日志一概不入库——它们是产物，不是程序。
- 收集方式是**入口脚本的 import 闭包**（`_tools/collect_code.py`），不是整目录拷贝：
  会话目录里常压着上千个历史实验脚本（`bt_*`、旧版本备份），闭包不会把它们带进来。
- **不截断**：一个任务要几十个文件就放几十个。确实多的时候按批提交（见「分批提交」）。
- 第三方依赖不打包：`requires` 里记的是包名（目前只有 `pylibs`）。它其实是本机的
  site-packages 副本（akshare、baostock、PIL、PyEMD、cffi…… 7000 多个 .py 加 .so），
  不是自研代码，所以不入库。运行时用 `PYLIBS_DIR` 指向它，**必须显式设置**
  （没设时命令直接报错，不去猜相对路径）。完整依赖清单见仓库根的
  [`requirements.txt`](../requirements.txt)：要么 `pip install -r`，要么指 `PYLIBS_DIR`。

## 目录约定

```
case/
├── _schema/task.schema.json    task.json 的字段契约
├── _seed/tasks.json            任务清单：key/name/cron/section/root/entry/skip/needs_llm
├── _tools/
│   ├── collect_code.py         从入口脚本做 import 闭包，只挑 .py
│   └── import_py.py            按清单把 .py 搬进来并生成 task.json（唯一导入入口）
├── _lib/
│   ├── case_registry.py        读 case/*/task.json（调度器与看板共用）
│   └── publish.py              把产出写入 DataHub tech 指标仓，并回填 metrics
└── <task-key>/
    ├── task.json               任务元数据（必需）
    ├── out/latest.json         本次产出的标准指标（运行后生成，不入库）
    └── *.py                    程序文件
```

## task.json 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✓ | 唯一任务键，`[a-z0-9_]`，与目录名一致 |
| `title` | ✓ | 面板上的中文标题 |
| `section` | ✓ | 面板标签：`stock` / `futures` / `realestate` / `macro` / `liquidity` / `bond` / `modules` |
| `metrics` | ✓ | 面板展示的指标；首跑后由 `publish.py` 从产出回填，不手工编造 |
| `sched` | ✓ | `{"mode":"daily","at":"HH:MM","weekdays":[1..7]}`；月度用 `{"mode":"monthly","at":"HH:MM","days":[28]}` |
| `cmd` | ✓ | 执行命令（相对 `cwd`）；有 `requires` 时前置 `PYTHONPATH="${PYLIBS_DIR:?...}:."` |
| `cwd` | | 运行目录，默认就是 `case/<key>` |
| `timeout` | | 超时秒数，默认 1800 |
| `enabled` | | 是否参与调度 |
| `deliver` | | `{"report": true, "email": false}`，只做声明与看板标注，发送由任务脚本自己完成 |
| `requires` | | root 外的依赖包名（如 `pylibs`），不复制文件，靠 `PYLIBS_DIR` 找 |
| `needs_llm` | | `true` 表示该任务有一段必须由大模型完成（如联网取报价），无人值守时会卡在那里 |
| `code_files` | | 本任务带进来的 `.py` 数量 |
| `entry` / `root` | | 入口脚本与迁移前的源目录，仅溯源 |

## 数据落点：DataHub 指标仓（按 CONTRACT v1.0）

任务跑完写 `out/latest.json`（简写即可，`publish.py` 会转成 DataHub 原生字段）：

```json
{"as_of": "2026-11-23",
 "points": [{"key": "chanlun.bull_score", "sec": "000300.SH", "value": 62.0,
             "obs_date": "2026-11-23", "quality": "ok", "name": "缠论评分", "dec": 0},
            {"key": "chanlun.signal", "sec": "000300.SH", "value_text": "B2",
             "obs_date": "2026-11-23", "quality": "ok", "name": "最近买卖点"},
            {"key": "rmt.ar5", "sec": "000300.SH", "obs_date": "2026-11-23",
             "quality": "missing", "meta_json": {"reason": "样本不足"}}]}
```

- `quality` 只允许 **`ok` / `missing` / `stale` / `estimated`**（DataHub 口径），不传则按值自动推断。
- 只有 `ok` 进图；算不出来写 `missing`，**不补零、不用旧值顶替**。
- 写入走 `from data_hub.tech import write_points, start_run, finish_run`：
  `write_points(rows, producer="case/<key>") -> (n_ok, n_missing)`，行字段是
  `metric_key` / `security_id` / `obs_date` / `value_num` / `value_text` / `quality` / `meta_json`。

```bash
python3 case/_lib/publish.py --task <key>        # 写单个任务并回填 metrics
python3 case/_lib/publish.py --all               # 写全部 out/latest.json
python3 case/_lib/publish.py --all --dry-run     # 只校验、不落库
python3 case/_lib/publish.py --all --snapshot    # 写完推云盘快照
```

找不到 `data_hub.tech` 的写入口就报错退出（码 2），不会静默成功。

## 导入

```bash
python3 case/_tools/import_py.py --dry-run        # 看每个任务会带多少个 .py
python3 case/_tools/import_py.py                  # 全量导入（先清空旧任务目录）
python3 case/_tools/import_py.py --only tda_spd   # 只做一个
python3 case/_tools/import_py.py --with-shared    # 额外带一份 pylibs 到 _shared/
```

### 分批提交

单个任务通常 1–107 个 `.py`，总量 700 出头（约 8MB）。要分多次推时：

```bash
python3 case/_tools/import_py.py --batch 1/3
git add case/<本批的 key...>  && git commit -m "..." && git push
```

## 运行

```bash
export PYLIBS_DIR=/path/to/pylibs      # 任务里 import pylibs 时用
python3 scheduler.py fg                # 前台跑调度（内置 + case 一体）
python3 scheduler.py status            # 每个 case 任务的下次时间与状态
python3 scheduler.py run <key>         # 立刻跑一个
python3 manager.py list                # 清单里 case 任务标 case
```

## 标签与投递

`section` 决定看板归到哪个 Tab：`stock` / `futures` / `realestate` / `macro` / `liquidity` / `bond` / `modules`。
报告与邮件由任务脚本自带（`deliver` 只做声明），看板负责按标签集中展示结果。

## 未搬的任务

`crude_weekly`（中国原油周度变化量雷达周报）：定时消息里只有自然语言要求，没有可执行命令，
目录里的两个脚本是「参考结构」。无固定程序、无法无人值守复跑，因此不搬；
需要时把生成逻辑写成脚本再补一个 `task.json` 即可。

`needs_llm` 标记的三个任务（`cds_weekly`、`repo_collateral`、`macro_four_dim`）脚本已搬，
但其中有一段必须由大模型完成（检索 CDS 报价、判定报告期、联网补财政数据），纯定时会卡住。