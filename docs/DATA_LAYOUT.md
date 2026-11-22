# data/ 目录说明

`data/` 是**派生产物**，已被 `.gitignore` 排除，不入库；删掉后可由构建链重新生成，
但部分逐股缓存重建很慢（全 A 成交额/收盘价各约 20 分钟、成分股强度约 13 分钟、ETF 份额约 8 分钟）。
因此日常不要清空 `data/`，只清理明确可再生成的坏缓存。

现状（2026-11-22）：**11295 个文件，约 700.7 MB**。

## 目录结构

```
data/
├─ *.csv                     源端缓存（mood_*.csv / strength_*.csv / cb_history.csv 等）
├─ *.json / *.js             页面数据产物（mood_data、index_data、cb_data、confirm_data…）
├─ last_update.json          启动器「今日是否已尝试更新」标记
├─ breadth_prices/           5579 只个股收盘价缓存（市场宽度/破净率/RSI 用）
├─ crowding_amounts/         5579 只个股成交额缓存（换手率/拥挤度/TRIN 用）
├─ sw_industries/            31 个申万行业缓存（低估行业指标）
├─ cb_cache/                 可转债逐券估值与长历史缓存
├─ us_dark/                  暗盘行情快照（latest.json + snapshots/YYYYMMDD.jsonl）
└─ _yahoo/                   跨市场对照用的少量外部数据
```

## 哪些能删、哪些别删

| 类别 | 例子 | 处理 |
|---|---|---|
| 页面数据产物 | `mood_data.json/js`、`index_data.*`、`confirm_data.*`、`factor_matrix.*` | 可重生成；删前确认构建链能跑 |
| 源端缓存（便宜） | `mood_*.csv` 里的日频汇总、`mood_qvix.csv` | 可删，重抓几分钟 |
| 源端缓存（昂贵） | `breadth_prices/`、`crowding_amounts/`（各 5579 只） | **不要删**，重建约 20 分钟/类 |
| 中间研究产物 | `_conditional_probe.json`、`rolling_origin.json`、`trial_log.json` | 可保留，供报告复现 |
| 暗盘快照 | `us_dark/snapshots/`（每日 jsonl） | 保留 7 天，脚本会自动清理 |
| 坏缓存 | `mood_all_pe.csv`、`mood_total_mv.csv`、`pcr_recent_probe.csv` | 已知历史遗留，无消费者，可删 |

## 命名约定

- **源缓存**：`mood_<指标>.csv`、`strength_<指数>_cache.csv`、`<源>_cache.csv`；
- **页面产物**：`<栏目>_data.json` + 同名 `.js`（`window.XXX_DATA=…`），两者内容必须一致；
- **逐股缓存**：`data/breadth_prices/<code>.csv`、`data/crowding_amounts/<code>.csv`，按代码分文件便于断点续抓；
- **快照/日志**：`data/us_dark/snapshots/YYYYMMDD.jsonl`，一行一次 5 秒快照；
- **临时探针输出**：以 `_` 开头（如 `_conditional_probe.json`），便于和正式产物区分。

## 与代码的对应关系

- `core.DATA` / `app.paths.DATA` 指向本目录；构建脚本只通过它读写，不写死相对路径；
- 页面通过 `<script src="data/xxx.js">` 读取 `.js` 产物（`web/` 页面对应 `../data/xxx.js`）；
- `start_site.py` 的新鲜度判断读取 `data/index_data.json`、`mood_data.json`、`cb_data.json` 与 `last_update.json`；
- `data/` 不入库，因此目录说明放在 `docs/`，不要试图在 `data/` 里放需要版本控制的文件。

## 备份建议

- 只需要备份 **源缓存中昂贵的那几类**（`breadth_prices/`、`crowding_amounts/`、`cb_cache/history.csv`）与
  `last_update.json`；页面产物可由构建链重建。
- 结构重构（见 `REFACTOR_PLAN.md`）**不移动 `data/`**；若未来要分层，先做兼容读取再迁移。
