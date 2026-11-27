# 迁移留档（仅历史参考，不参与运行）

- `_seed_tasks_legacy.json`：旧式 crontab 形态的任务清单（`cron/py/cwd` 字段）。
  里面很多 key 指向历史会话目录、早已迁到 precious-task 的 `case/`，且没有任何代码消费它。
  唯一真源是 `case/*/task.json`（`case/_lib/case_registry.py` 按目录扫描）。
  保留这份只为排障/回看迁移过程，不要按它去找任务、也不要试图同步它。
