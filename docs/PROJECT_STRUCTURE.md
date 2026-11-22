# 项目目录与维护约定

本文件说明「什么文件放在哪里、为什么」，避免根目录再次变成杂物堆。
完整重构过程见 [REFACTOR_PLAN.md](REFACTOR_PLAN.md)，`data/` 用途见 [DATA_LAYOUT.md](DATA_LAYOUT.md)。

## 目录总览

```
data_research/
├─ 启动.cmd / 启动.bat / 修复双击关联.reg   双击入口（CRLF）
├─ start_site.py / serve.py                薄包装：import workbench.entry.* 后调用 main()
├─ workbench/                              Python 包
│  ├─ paths.py                             ROOT / DATA / WEB / VENDOR / REPORTS / DOCS
│  ├─ config.py                            全局口径系数、抓取参数、端口、暗盘标的清单
│  ├─ core.py                              路径/日期/日志/缓存/分位/交易日历
│  ├─ factor_utils.py                      L0/L1 研究公共层
│  ├─ pipeline/                            22 个 DAG 脚本 + build_turning（23 个文件）
│  ├─ services/us_dark.py                  暗盘行情服务（WS + 5 秒快照 + /api/usdark/*）
│  ├─ entry/start_site.py                  新鲜度判定 + DAG 编排 + 站点
│  ├─ entry/serve.py                       只起静态服务（可带端口）
│  ├─ web/server.py                        SiteHandler：旧 URL 302 → /web/
│  └─ audit/transform.py                   可重复审计（原 audit_transform.py）
├─ web/                                    纯静态前端
│  ├─ index.html / indicators.html / mood.html / cb.html / us.html
│  ├─ app.css / app.js
│  └─ cb.js / us.js / *_cards.js / confidence_card.js / horizon_cards.js
├─ data/                                   全部派生产物（CSV/JSON/JS，.gitignore）
├─ vendor/                                 ECharts（页面与报告共用）
├─ reports/                                9 份评估/方案报告 HTML
├─ docs/                                   本文件 + REFACTOR_PLAN + DATA_LAYOUT + previews/
├─ research/                               一次性探针与报告生成器
├─ tests/smoke.py                          无网络冒烟测试
└─ archive/                                旧版脚本 + backups/
```

## 运行方式

```bash
python start_site.py            # 更新数据 + 启动站点（根目录薄包装）
python start_site.py --force    # 强制重建
python serve.py                 # 只起静态服务，可加端口：python serve.py 8041
python -m workbench.pipeline.build_mood         # 单个 DAG 节点
python -m workbench.audit.transform             # 因子预处理审计
python -m tests.smoke                           # 结构/路由冒烟
```

## 路径规则

- **禁止**再用 `Path(__file__).parent` 推算 `data/`；统一 `from workbench.paths import DATA, ROOT`。
- 构建脚本在 `workbench/pipeline/`，运行时用 `python -m workbench.pipeline.<name>`；
  `start_site` 的 `PIPELINES` 存模块名，子进程以 `-m` + `cwd=ROOT` 启动。
- `web/` 页面用相对路径：同级 `app.css`/`cb.js`，上级 `../data/...`、`../vendor/...`、`../reports/...`。
- `data/` 不物理搬动（700MB、11295 个逐股缓存），只通过 `paths.DATA` 访问。

## 旧 URL 如何处理

页面在 `web/`，但旧地址仍可用：

```
/index.html       302 → /web/index.html
/mood.html        302 → /web/mood.html
/us.html          302 → /web/us.html
…
```

`workbench/web/server.py` 的 `SiteHandler` 在 `do_GET` 里做 302；浏览器基地址变成 `/web/` 后，
页面里的相对资源才会正确解析。若改成内部 `translate_path` 改写，`/mood.html` 会去请求
`/app.css` 而 404——这是已经验证过的坑。

## 报告 / 预览 / 研究 / 备份

| 类型 | 位置 | 规则 |
|---|---|---|
| 报告 | `reports/` | 图表引用 `../vendor/echarts.min.js`；生成器写这里 |
| 预览图 | `docs/previews/` | `预览-*.png`，不要堆根目录 |
| 研究脚本 | `research/` | 从根目录运行，`BASE` 已指回根；`_gen_*.py` 输出 `reports/` |
| 测试 | `tests/` | `python -m tests.smoke`，标准库、无网络 |
| 备份 | `archive/backups/` | 改动前快照，不要用 `xxx.bak_*` 堆根目录 |

> 注意：`research/_gen_horizon_preview.py` 生成的是「草案 v3」，当前
> `reports/因子周期画像-20261121.html` 是后续增补版（比生成器产物多约 16KB）。
> 直接重跑会覆盖增补版，先备份再生成。

## 新增文件的落点

- 新指标/新数据链：`workbench/pipeline/build_xxx.py`，并在 `workbench/entry/start_site.py` 的 `PIPELINES` 登记；
- 新页面：`web/xxx.html` + `web/xxx.js`，在页面侧栏/子导航加入口；旧 URL 若要保留，加进 `PAGE_MAP`；
- 新报告：`reports/`；新预览：`docs/previews/`；
- 一次性分析：`research/`，文件名以 `_` 开头；
- 旧版本快照：`archive/backups/`。

## 已完成的整理（2026-11-22）

- 报告、预览、研究脚本、备份分目录（根目录 80 → 9 个文件）；
- 页面/前端资源迁入 `web/`，旧 URL 302；
- Python 迁入 `workbench/` 包：`pipeline/` 23 个文件、`services/`、`entry/`、`audit/`；
- 根目录 `start_site.py` / `serve.py` 改为薄包装，`启动.cmd` 不变；
- 路径统一走 `workbench/paths.py`；新增 `tests/smoke.py`；
- 报告与 README 的旧命令同步更新（`python -m workbench.pipeline.*`）。
