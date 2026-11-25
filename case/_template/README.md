# _template —— 最小任务模板

以 `_` 开头，不会被当成任务扫描。用的时候：

```bash
cp -r case/_template case/my_task
# 改 task.json 的 key（必须和目录名一致）、title、section、sched、visibility
python3 case/_lib/runner.py --task my_task --dry-run     # 看阶段链路
python3 case/_lib/runner.py --task my_task --no-publish  # 真跑，不落库
```

五个阶段各一个脚本，接口见 `case/CONTRACT.md`：

| 阶段 | 脚本 | 要产出 |
|---|---|---|
| fetch | fetch.py | `data/` 下非空文件 |
| check | 内置 artifacts（可换 check.py） | `out/check.json` |
| render | render.py | `out/report.md` 等 |
| emit | 内置 emit（可换 emit.py） | `out/latest.json` |
| dispatch | dispatch.py | 无（自己发消息） |

默认不要提交 `data/`、`out/`、`logs/`，它们已在 `.gitignore`。

例外：任务带模型（权重、元数据、特征面板）时，这些是**要入库的资产**，不是运行产物 ——
在 `.gitignore` 里显式放行该任务的 `data/`，并补一个 `train_model.py` 做训练兜底，
让权重缺失时任务能自己训出来。口径见 `case/CONTRACT.md` 的「模型资产与训练兜底」。
