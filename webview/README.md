# webview — 私有任务 web view（单独展示）

这个分支（`feat/web-view`）只做一件事：把私有仓 `precious-task` 的任务单独展示成一页，
和公开看板**互不混排**。

设计上刻意保持**零侵入**：不改 `build.py`、`manager.py`、`case_registry.py` 这些主线文件，
只新增本目录。所以它可以单独部署、单独交付，不依赖别的 PR 能不能合。

```
webview/
├── render.py   生成页面的脚本（自包含：读 task.json、取指标、扫图、出缩略图与灯箱）
├── index.html 构建产物（不入库）
├── assets/    图片落地目录（不入库）
└── README.md  本文件
```

## 怎么跑

```bash
# 1. 挂上私有仓（任务清单从这里读）
export CASE_EXTRA_DIRS=/srv/private-tasks/case
# 2. data_hub 在 PYTHONPATH 里（指标从 data_hub.tech 读，与公开页同一口径）
export PYTHONPATH=/srv/monitor/datahub-multi-tier:$PWD
# 3. 生成
python3 webview/render.py                       # → webview/index.html
python3 webview/render.py --out /srv/webview/index.html
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--out` | `webview/index.html` | 页面输出路径 |
| `--assets` | `<页面目录>/assets` | 图片落地目录（要和页面同级，web 根能访问即可） |
| `--max-images` | `6` | 每个任务最多展示几张产出图 |
| `--no-images` | — | 不扫图，只出指标 |
| `--public-url` | `index.html` | 「公开看板」入口地址；传空串 `""` 则不显示入口 |

## 页面上有什么

- **只列私有任务**：`task.json` 里 `visibility=private` 的任务（历史写法 `internal` 等价）；
  公有任务不在本页。
- **指标**：优先用 `task.json` 的 `metrics` 声明，没声明就从 `out/latest.json` 的 points 反推；
  库里没有的值显示「—」，不插值、不用旧值顶替。没有指标的任务出紧凑任务卡，等 `publish` 回填后自动出数值。
- **图**：扫 `<task>/out|charts|runs|output|figures|reports|assets` 里最近的图，
  原图拷到 `assets/<任务>/`、另生成最长边 900 的缩略图。
  缩略网格用 `contain`（长图不裁不压扁），点开是灯箱：
  滚轮缩放 0.4×~8×、按住拖动、双击切 1:1、Esc 或点背景关闭、可新标签打开原图。
- **板块**：按 `section`（没有就退 `section_domain`）分组；`credit` 映射到 `bond`。

## 部署

```nginx
# 示例：把 webview 单独挂到一个站点/子路径
location /webview/ {
    alias /srv/webview/;
    index index.html;
}
```

页面是纯静态文件（HTML + `assets/`），不需要后端。构建产物与图片都在 `.gitignore` 里，
不随仓库走；要更新就重跑一次 `python3 webview/render.py`。

## 与公开看板的关系

- 公开看板仍由主线 `build.py` 生成 `index.html`，只出 `visibility=public` 的任务；
- 本目录生成的页面只出 `visibility=private`；
- 两者读同一个 `data_hub.tech`、共用同一套视觉样式（`build.py` 的 `CSS` 与 `kv`/`num` 小工具），
  但页面文件、部署位置、访问入口都独立。