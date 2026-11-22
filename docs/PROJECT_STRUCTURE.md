# 项目目录与维护约定

本文件说明「什么文件放在哪里、为什么」，避免根目录再次变成杂物堆。

## 目录总览

```
data_research/
├─ 启动.cmd / 启动.bat / 修复双击关联.reg   双击入口（CRLF）
├─ start_site.py      更新数据 + 启动站点 + 挂载 /api/usdark/*
├─ serve.py           只起静态服务（预览/调试），可带端口参数
├─ config.py          全局口径系数、抓取参数、端口、暗盘标的清单
├─ core.py            路径/日期/日志/缓存/分位/交易日历
├─ factor_utils.py    L0/L1 研究公共层
├─ us_dark.py         暗盘行情服务（WebSocket + 5 秒快照 + /api/usdark/*）
├─ build_*.py         数据构建（DAG 节点）
├─ rebuild_turnover.py / audit_efficacy.py / audit_transform.py
├─ index.html / indicators.html / mood.html / cb.html / us.html
├─ app.js / app.css / cb.js / us.js / *_cards.js / confidence_card.js
├─ vendor/            ECharts（离线）
├─ data/              全部派生产物（CSV/JSON/JS，.gitignore）
├─ reports/           评估/方案报告 HTML（9 份）
├─ docs/              本文件 + previews/ 预览截图
├─ research/          一次性探针与报告生成器
└─ archive/           旧版脚本 + backups/（改动前的 HTML/JS 快照）
```

## 分类规则

| 类型 | 位置 | 说明 |
|---|---|---|
| 站点页面 | 根目录 `*.html` | 服务根目录就是项目根，`data/*.js`、`app.js`、`vendor/` 都是相对路径，不能随意搬 |
| 前端渲染器 | 根目录 `*.js` | 页面用 `<script src="cb.js">` 等直接引用 |
| 生产 Python | 根目录 | 入口、DAG 节点、公共层；互相 `from config import`、`from core import` |
| 网页数据 | `data/` | 构建脚本输出，派生产物，不入库；`data/us_dark/` 是行情快照 |
| 报告 | `reports/` | 9 份评估/方案 HTML；统一引用 `../vendor/echarts.min.js` |
| 预览截图 | `docs/previews/` | 页面/配色预览 PNG，与报告分开，避免根目录堆图 |
| 研究脚本 | `research/` | 一次性探针、报告生成器；不属于启动链 |
| 备份 | `archive/` | `archive/*.bak` 旧脚本；`archive/backups/` 页面改动前快照 |

## 为什么生产代码暂时不搬进 `scripts/`

不是“懒得搬”，而是搬动会同时牵动三层路径，收益小于风险：

1. **Python 导入**：所有构建脚本都 `from config import ...` / `from core import ...`，靠“从项目根运行”找到模块；搬进子目录需要改 `sys.path` 或改成包导入。
2. **数据路径**：`core.BASE = Path(__file__).parent`、`us_dark.BASE` 都以脚本位置推算 `data/`；搬进 `scripts/` 后 `DATA` 会变成 `scripts/data`，所有缓存/输出路径全错。
3. **启动与静态服务**：`start_site.py` 用 `subprocess.run([python, "build_x.py"], cwd=BASE)` 拉起 DAG；静态服务的根目录就是项目根，HTML 里 `data/*.js`、`app.js`、`vendor/` 全是相对路径。要么全站改绝对路径/改服务根，要么整体搬到 `web/` 并重做路由。

**结论**：在站点仍是“根目录静态站 + 根目录 Python”架构时，保持生产代码在根目录最稳。完整重构方案已单独写成 [REFACTOR_PLAN.md](REFACTOR_PLAN.md)：目标是 `app/`（Python 包）+ `web/`（页面与前端资源），服务根仍为项目根，用页面映射保留旧 URL；`data/` 不物理搬动。该方案分 P0–P6 阶段，每阶段有验收与回滚点，不宜和日常改版混在一起。

`data/` 的目录用途与“哪些能删、哪些别删”见 [DATA_LAYOUT.md](DATA_LAYOUT.md)。

## research/ 使用方式

- 一律**从项目根目录**运行，例如：
  ```bash
  python research/_composite_probe.py
  python research/_gen_horizon_preview.py     # 输出 reports/因子周期画像-…html
  ```
- 脚本里的 `BASE` 已指回项目根，`data/` 读写与生产一致；
- `_conditional_probe.py` 额外把项目根加入 `sys.path`，才能 `from factor_utils import`；
- 报告生成器输出到 `reports/`，不要再写回根目录；
- **注意**：`_gen_horizon_preview.py` 生成的是「草案 v3」，当前 `reports/因子周期画像-20261121.html` 是后续增补版（比生成器产物多约 16KB）。直接重跑会覆盖增补版，先备份再生成；
- `audit_transform.py` 留在根目录：报告页脚写的复跑命令是 `python audit_transform.py`，且它属于可重复审计而非一次性探针。

## 新增文件的落点

- 新指标/新数据链：根目录 `build_xxx.py`，并在 `start_site.PIPELINES` 登记；
- 新页面：根目录 `xxx.html` + `xxx.js`，并在四个页面的侧栏/子导航加入口；
- 新报告：`reports/`，图表引用 `../vendor/echarts.min.js`；
- 新预览图：`docs/previews/`；
- 一次性分析脚本：`research/`，文件名以 `_` 开头；
- 旧版本快照：`archive/backups/`，不要用 `xxx.html.bak_*` 堆在根目录。

## 当前整理记录（2026-11-22）

- 9 份根目录 HTML 报告 → `reports/`，并修正 8 处 `vendor/` 相对路径；
- 6 张 `预览-*.png` → `docs/previews/`；
- 15 个 `_*.py` / `build_composite_v2.py` → `research/`，`BASE` 统一指回项目根，生成器输出改到 `reports/`；
- 5 个 `mood.html.bak_*` + `factor_audit_cards.js.bak` → `archive/backups/`；
- 删除空的 `serve.log` 与 `__pycache__/`；
- 首页与 `horizon_cards.js` 的报告链接改到 `reports/`。
