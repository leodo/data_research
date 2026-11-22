# 完整整理计划（结构重构）

> **执行结果（2026-11-22）**：P0–P6 已执行完成，实际包名采用 `workbench/`（下文已同步），
> 页面在 `web/`，旧 URL 302。P6 跑了完整 22 步 DAG（默认增量，避免 700MB 缓存重下）；
> 首轮第 7 步 `build_breadth` 因漏改 import 失败，修复后补跑并重算下游 10 个节点，全部通过。
> 当前结构以 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) 为准。

> 目标：把「文档 / 报告 / 代码 / 页面 / 数据 / 研究脚本」彻底分层，同时保持
> `http://localhost:8040/index.html` 等旧地址、`python start_site.py` 等旧命令可用。
> 本文是执行方案，不是已完成记录；已完成的部分见文末「当前进度」。

## 1. 原则

1. **URL 不变**：5 个栏目地址、报告链接、`/api/usdark/*` 全部保持可用；`web/` 只是物理位置。
2. **命令不变**：`启动.cmd`、`python start_site.py`、`python serve.py` 继续有效，根目录保留薄入口。
3. **数据不动**：`data/` 有 700MB、1.1 万个逐股缓存文件，物理搬动收益低、风险高；只新增说明，不重排。
4. **一次一层**：每阶段独立可回滚、可验收，不在同一提交里混代码搬家和逻辑改动。
5. **先测试后搬家**：先有 smoke 测试，再动文件；每阶段结束跑同一套测试。

## 2. 现状

已经完成（2026-11-22）：

- 9 份 HTML 报告 → `reports/`，8 处 `vendor/` 引用改为 `../vendor/`；
- 6 张预览图 → `docs/previews/`；
- 14 个 `_*.py` + `build_composite_v2.py` → `research/`，`BASE` 指回项目根；
- 5 个 `mood.html.bak_*` + `factor_audit_cards.js.bak` → `archive/backups/`；
- 根目录 80 → 51 个文件；报告/首页链接已修。

仍未解决：

- 5 个页面 + 9 个前端 JS/CSS 与 25 个 Python 生产文件混在根目录；
- 生产脚本各自用 `BASE = Path(__file__).parent` 推算 `data/`，一旦移动就全错；
- 静态服务根目录 = 项目根，页面用相对路径引用 `data/`、`vendor/`；
- 没有自动化 smoke 测试，重构只能靠手点。

## 3. 目标目录

```
data_research/
├─ README.md / requirements.txt / .gitignore / .gitattributes
├─ 启动.cmd / 启动.bat / 修复双击关联.reg      # 根入口，不改内容
├─ start_site.py / serve.py                    # 薄包装：import app.entry.* 后调用 main()
├─ workbench/                                        # Python 包（新增 __init__.py）
│  ├─ paths.py                                 # ROOT / DATA / WEB / REPORTS / DOCS / VENDOR
│  ├─ config.py
│  ├─ core.py
│  ├─ factor_utils.py
│  ├─ pipeline/                                # 22 个 DAG 脚本 + build_turning（由 build_mood 调用）
│  │  ├─ build_fear_greed.py … build_cb.py
│  │  ├─ audit_efficacy.py
│  │  └─ __init__.py
│  ├─ services/
│  │  ├─ us_dark.py
│  │  └─ __init__.py
│  ├─ entry/
│  │  ├─ start_site.py
│  │  ├─ serve.py
│  │  └─ __init__.py
│  ├─ web/
│  │  ├─ server.py                             # 旧路径 → web/ 的页面映射
│  │  └─ __init__.py
│  └─ audit/
│     ├─ transform.py                          # 原 audit_transform.py
│     └─ __init__.py
├─ web/                                        # 纯静态前端
│  ├─ index.html / indicators.html / mood.html / cb.html / us.html
│  ├─ app.css / app.js
│  ├─ cb.js / us.js
│  ├─ confirm_cards.js / factor_audit_cards.js / confidence_card.js
│  ├─ horizon_cards.js / rsi_breadth_card.js

├─ data/                                       # 不动：缓存 + JSON/JS 产物（700MB，gitignore）
├─ vendor/                                     # 不动：ECharts，页面与报告共用
├─ reports/                                    # 已完成
├─ docs/
│  ├─ PROJECT_STRUCTURE.md
│  ├─ REFACTOR_PLAN.md                         # 本文件
│  └─ previews/
├─ research/                                   # 已完成，导入语句随 Python 包化调整
├─ archive/
│  ├─ *.bak
│  └─ backups/
└─ tests/
   └─ smoke.py                                 # 无网络冒烟：导入 / 路由 / 链接 / 报告资源
```

### 为什么 `web/` 不是新的服务根

页面引用 `data/*.js`、`vendor/echarts.min.js`、`reports/*.html`。如果直接把服务根改成 `web/`，
这些引用都会 404，必须把 700MB 数据和报告复制/软链进 `web/`，反而更乱。

本方案保持 **服务根 = 项目根**，页面放 `web/` 后：

- 页面内引用改成 `../data/...`、`../vendor/...`、`../reports/...`；
- 旧 URL `/index.html` 由 `workbench/web/server.py` 映射到 `/web/index.html`，`/web/index.html` 也可直达；
- `/data`、`/vendor`、`/reports`、`/docs` 仍由项目根自然提供，不需要拷贝。

## 4. 文件映射

| 当前 | 目标 | 备注 |
|---|---|---|
| `config.py` / `core.py` / `factor_utils.py` | `workbench/` | 全部改为 `from workbench...` |
| `build_*.py`（21 个，含 `build_turning`）、`rebuild_turnover.py`、`audit_efficacy.py` | `workbench/pipeline/` | 共 23 个文件；22 个 DAG 脚本 + `build_turning`（内部调用） |
| `us_dark.py` | `workbench/services/us_dark.py` | `DATA_DIR` 改用 `paths.DATA` |
| `start_site.py` / `serve.py` | `workbench/entry/` + 根目录薄包装 | 命令行不变 |
| `audit_transform.py` | `workbench/audit/transform.py` | 报告页脚命令改为 `python -m workbench.audit.transform` |
| 5 个 `*.html` | `web/` | 资源引用改 `../` |
| `app.css`、`app.js`、`cb.js`、`us.js`、6 个 `*_cards.js`/`confidence_card.js` | `web/` | 页面同目录引用不变 |
| `vendor/` | 不动 | 页面用 `../vendor/`，报告用 `../vendor/` |
| `data/` | 不动 | 目录用途写在 `docs/DATA_LAYOUT.md`（`data/` 被 gitignore，说明文件放这里不会入库） |
| `reports/`、`docs/`、`research/`、`archive/` | 已完成 | 只做链接与导入收尾 |

## 5. 必须改的代码点

### 5.1 新增 `workbench/paths.py`

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEB = ROOT / "web"
VENDOR = ROOT / "vendor"
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"
```

替换清单（当前各自算 `BASE` 的文件）：

- `core.py`：`BASE = ROOT`，`DATA = paths.DATA`
- `factor_utils.py`：同上
- `build_cb.py`、`build_daily_confidence.py`、`build_horizon_profile.py`、`build_rsi_breadth.py`：删本地 `BASE/DATA`，改用 `paths`
- `us_dark.py`：`BASE = ROOT`，`DATA_DIR = paths.DATA / "us_dark"`，`SNAP_DIR = ...`
- `start_site.py` / `serve.py`：`os.chdir(ROOT)`
- `audit_transform.py`：`BASE = ROOT`
- `research/*`：已用根目录 `BASE`，只把 `factor_utils` 导入改成 `app.factor_utils`

### 5.2 导入语句

全量机械替换（仅这些模式）：

```
from config import …        → from workbench.config import …
from core import …          → from workbench.core import …
from factor_utils import …  → from workbench.factor_utils import …
import us_dark              → from workbench.services import us_dark
```

`build_mood.py` 内部调用 `build_turning.build()`，改为 `from workbench.pipeline import build_turning`。

### 5.3 DAG 编排

`workbench/entry/start_site.py`：

```python
PIPELINES = {
  "mood": [
    ("全市场换手率", "app.pipeline.rebuild_turnover"),
    ("交易拥挤度",   "app.pipeline.build_crowding"),
    …
  ],
  "cb": [("可转债数据", "app.pipeline.build_cb")],
}
```

`rebuild()` 改为：

```python
cmd = [sys.executable, "-m", script]          # script 是模块名
subprocess.run(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})
```

`--force` 透传逻辑不变。

### 5.4 静态服务与旧 URL

页面搬到 `web/` 后，旧 URL **不能只做内部路径改写**：浏览器地址仍是 `/mood.html`，页面里的
`app.css` / `cb.js` 会按 `/app.css` 解析而 404。正确做法是 **302 跳到 `/web/`**，让浏览器基地址变成
`/web/`，相对资源自然生效。

`workbench/web/server.py`：

```python
PAGE_MAP = {
    "/": "/web/index.html",
    "/index.html": "/web/index.html",
    "/indicators.html": "/web/indicators.html",
    "/mood.html": "/web/mood.html",
    "/cb.html": "/web/cb.html",
    "/us.html": "/web/us.html",
}

class SiteHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        u = urlsplit(self.path)
        if u.path in PAGE_MAP:
            target = PAGE_MAP[u.path] + (("?" + u.query) if u.query else "")
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        super().do_GET()
```

`start_site` 与 `serve` 的 Handler 继承它，并在 `do_GET` 里先处理 `/api/usdark/*`（命中则不进静态分支），
其余交给 `SiteHandler`。`/web/...` 可直接访问；`/reports`、`/docs`、`/data`、`/vendor` 仍由项目根自然提供。

> 备选方案是把页面内所有资源改成绝对路径（`/web/app.css`、`/data/...`），这样旧 URL 也能直接出图；
> 但页面里有大量 `data/*.js`、`vendor/*`、`reports/*`，改起来点更多。首选 302。

### 5.5 页面资源引用

在 `web/` 的 5 个页面里：

| 原引用 | 新引用 |
|---|---|
| `app.css` / `app.js` / `cb.js` / `us.js` / `*_cards.js` | 不变（同目录） |
| `vendor/echarts.min.js` | `../vendor/echarts.min.js` |
| `data/xxx.js` | `../data/xxx.js` |
| `reports/xxx.html` | `../reports/xxx.html` |
| 页面互链 `index.html` / `mood.html` … | 不变（同目录） |

`horizon_cards.js` 里报告链接：`reports/...` → `../reports/...`。
`mountToc` 的 localStorage key 用 `location.pathname`，从 `/mood.html` 变成 `/web/mood.html`，
旧折叠偏好会丢一次；可在 `app.js` 里改成按文件名取 key（非必须）。

### 5.6 报告与文档链接

- `index.html` → `../reports/因子周期画像-…html`；
- 报告内部 `../vendor/echarts.min.js` 不变；
- README 命令：`python start_site.py` / `python serve.py` 不变；`python -m workbench.audit.transform` 改 `python -m workbench.audit.transform`；
- `docs/PROJECT_STRUCTURE.md` 的目录树同步为目标结构。

## 6. 分阶段执行

| 阶段 | 内容 | 预计 | 验收 | 回滚 |
|---|---|---|---|---|
| P0 | 基线：git 分支/标签、跑现有页面冒烟、记录 `last_update.json` | 0.5h | 5 页 + API + 报告均可打开 | 无需 |
| P1 | 新增 `workbench/paths.py`；`core/factor_utils/us_dark` 改用 paths（文件仍不动） | 1h | `python -m workbench.pipeline.build_factor_matrix`、`start_site.py`（跳过更新）正常 | `git checkout` |
| P2 | 页面/前端资源搬 `web/`；页面引用改 `../`；加页面映射 | 1.5h | 旧 URL 与新 `/web/` 均 200；数据/图表正常 | 移回根 + 删映射 |
| P3 | Python 包化：`workbench/pipeline`、`services`、`entry`；根目录薄包装；DAG 改 `-m` | 4h | `python -m workbench.pipeline.audit_efficacy`、`python start_site.py`、API 正常 | 分支回滚 |
| P4 | `audit_transform` 入 `workbench/audit`；研究脚本导入与文档收尾；smoke 测试 | 1h | `python -m tests.smoke` 全绿 | 分支回滚 |
| P5（可选） | 数据分层 `data/cache|derived|public` | 3h+ | 全链重建 | 不建议，见 §9 |
| P6 | 全量验收：`python start_site.py --force` 完整跑一次（约 40 分钟） | 1h | 所有页面日期/数值更新，快照/API 正常 | 保留旧 tag |

总计：**活跃约 8–9 小时 + 一次完整重建等待**。建议拆成两次提交/两个工作日：

- 第一次：P0–P2（页面与路径层，当天可验证）；
- 第二次：P3–P4（Python 包化，需完整重建验收）。

### P0 基线

```bash
git checkout -b refactor/structure
git add -A && git commit -m "chore: snapshot before structure refactor"
python serve.py 8041
# 另开窗口：逐页打开 /index.html /indicators.html /mood.html /cb.html /us.html
# 记录 /api/usdark/health、/reports/整体总结-20261120.html
```

不复制 `data/`（700MB，且 gitignore）；分支只跟踪代码/文档。

### P1 路径层

- 新增 `workbench/__init__.py`、`workbench/paths.py`；
- 只改 4 个公共文件，不动文件位置；
- 验收：`python -m workbench.pipeline.build_factor_matrix`、`python -m workbench.pipeline.build_fear_greed`（会走 QVIX 降级）正常，
  `python serve.py 8041` 页面正常。

### P2 页面搬家

```powershell
New-Item -ItemType Directory web
Move-Item index.html,indicators.html,mood.html,cb.html,us.html web\
Move-Item app.css,app.js,cb.js,us.js,confirm_cards.js,factor_audit_cards.js,confidence_card.js,horizon_cards.js,rsi_breadth_card.js web\
```

然后：

1. 页面里 `vendor/` → `../vendor/`、`data/` → `../data/`、`reports/` → `../reports/`；
2. `workbench/web/server.py` 加 `PAGE_MAP`（或先在 `serve.py/start_site.py` 内联）；
3. 验收矩阵：

```
GET /index.html          → 302 /web/index.html
GET /web/index.html      → 200
GET /mood.html           → 302 /web/mood.html
GET /data/mood_data.js   → 200
GET /vendor/echarts.min.js → 200
GET /reports/整体总结-20261120.html → 200
```

### P3 Python 包化

1. 建目录与 `__init__.py`；
2. 移动文件（用 `git mv` 保留历史）；
3. 机械替换导入与 BASE；
4. 改 `start_site.PIPELINES` 与 `subprocess -m`；
5. 根目录 `start_site.py`/`serve.py` 变成 5 行包装；
6. 验收：5 页 + `/api/usdark/health` + 一个快速 DAG 节点（`build_factor_matrix`）+ `python start_site.py`（跳过更新）。

### P4 收尾与测试

`tests/smoke.py`（无网络）：

```python
# 伪代码
assert import app.config, app.core, app.services.us_dark
assert DATA / "mood_data.json" exists
srv = ThreadedServer(("127.0.0.1", 0), SiteHandler); port = srv.server_address[1]
for path in ["/index.html","/web/index.html","/mood.html","/us.html",
             "/data/mood_data.js","/vendor/echarts.min.js",
             "/reports/整体总结-20261120.html"]:
    assert GET(port, path).status == 200
assert "reports/整体总结" in GET("/index.html").text or "../reports" ...
# 报告资源：每个 reports/*.html 不含 src="vendor/
```

运行：`python -m tests.smoke`，退出码 0 才进入 P6。

### P6 全量验收

```bash
python start_site.py --force
```

检查：

- 22 步 DAG 全绿（QVIX 源故障时允许恐贪降级）；
- 5 个页面数据日期更新、无 404；
- `/api/usdark/state` 返回 66 标的、快照文件增长；
- 报告页图表正常；
- 根目录无 `.bak`、`__pycache__`、`serve.log`。

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 导入路径断裂 | 高 | 启动失败 | P1/P3 分两次；`tests/smoke` 先导入再跑服务 |
| `data/` 写错目录 | 高 | 重建写空/覆盖 | `paths` 单点定义 + 启动时断言 `DATA == ROOT/"data"` |
| 页面 404/白屏 | 中 | 站点不可用 | P2 路由矩阵 + 5 页逐个截图/请求 |
| 报告图表空白 | 中 | 报告不可读 | 检查 `../vendor`；smoke 扫描 `src="vendor/` |
| DAG 子进程找不到包 | 中 | 数据不更新 | `python -m` + `cwd=ROOT` + `PYTHONPATH=ROOT` |
| localStorage TOC 偏好丢失 | 低 | 体验小 | 可改 `mountToc` key 为 basename |
| 中文路径/编码 | 中 | 命令失败 | 全程 UTF-8；PowerShell 引号包裹 |
| 完整重建耗时 | 中 | 验收拖长 | 先 `start_site.py`（跳过），最后单独 `--force` |

## 8. 验收清单（Definition of Done）

- [ ] `python start_site.py` 正常启动并自动打开首页
- [ ] `python serve.py 8041` 正常
- [ ] 旧 URL 302 到 `/web/`，新 URL 直接 200，跳转后页面资源不 404
- [ ] 5 个栏目页面数据/图表/主题切换正常
- [ ] `/api/usdark/state|sparklines|health` 正常
- [ ] 9 份报告可打开且图表加载
- [ ] `python -m workbench.pipeline.audit_efficacy` 可跑
- [ ] `python -m workbench.audit.transform` 可跑
- [ ] `python -m tests.smoke` 全绿
- [ ] 根目录只剩入口、README、requirements、配置 dotfiles
- [ ] `git status` 只含预期改动

## 9. 不做的事（明确边界）

1. **不物理重排 `data/`**：700MB、11295 个文件（逐股缓存 5579×2），移动不会提升可读性，
   却要改几十处 `DATA / "..."`，且缓存路径变化会让增量抓取失效。目录用途写在 `docs/DATA_LAYOUT.md`。
2. **不把 `vendor/` 搬进 `web/`**：报告在根目录的 `reports/` 下共用 `../vendor`，搬了要改两套路径。
3. **不改数据口径、指标算法、页面功能**：本次只做结构，任何逻辑改动单独提交。
4. **不引入 pytest/vite 等新工具链**：`tests/smoke.py` 用标准库，保持零新依赖。

## 10. 需要拍板的点

| # | 选项 | 建议 |
|---|---|---|
| 1 | 旧 URL 保留方式 | `PAGE_MAP` 映射（无重复文件） |
| 2 | 包名 | `workbench`（短、明确）；若想更具体可用 `workbench` |
| 3 | 根目录是否保留 `start_site.py/serve.py` 薄包装 | 保留，保护肌肉记忆与 `启动.cmd` |
| 4 | `audit_transform.py` 去向 | `workbench/audit/transform.py`，报告命令改 `python -m workbench.audit.transform` |
| 5 | 数据是否分层 | **暂不做**；等结构稳定后单独评估 |
| 6 | 是否先提交当前状态 | 建议先建分支提交，便于回滚 |

## 11. 执行记录（2026-11-22）

- ✅ P0 基线：分支 `refactor/structure`，快照已提交；
- ✅ P1 路径层：`workbench/paths.py`，公共层不再用 `__file__` 推算 `data/`；
- ✅ P2 页面：5 页 + 9 个前端资源迁入 `web/`，旧 URL 302，资源/报告链接已验证；
- ✅ P3 Python 包化：`workbench/{config,core,factor_utils,pipeline,services,entry,audit,web}`，根目录薄包装；
- ✅ P4 收尾：`tests/smoke.py` 全绿，README/结构文档/报告命令同步；
- ✅ P6 全量验收：22 步 DAG 完成；首轮第 7 步 `build_breadth` 的 `from build_crowding import` 漏改，修复后补跑，并重算 `below_net → breadth_extra → arms → mood → confirm → L0–L4` 全部通过；`last_update.json` 写回 `ok=true`；8040 重启并逐页验证；
- 数据分层（原 P5）按计划不做。
