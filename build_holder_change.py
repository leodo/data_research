# -*- coding: utf-8 -*-
"""
重要股东增减持规模（东方财富，公开数据自算）
=============================================
数据源：东方财富数据中心「股东增减持」RPT_SHARE_HOLDER_INCREASE
        https://data.eastmoney.com/executive/gdzjc.html
        （akshare 的 stock_ggcg_em 指向同一接口，但它默认按 pageSize=500
          翻完 4 万余页全量分页；这里直连底层 API 按公告日增量抓取。）

口径：
  - 归属日 = NOTICE_DATE（公告日）。PIT 回放必须用公告日：变动截止日
    END_DATE 常早于披露日，按 END_DATE 归档会把当时尚未公开的信息算进历史。
  - 金额估算 = |CHANGE_NUM|(万股) × CLOSE_PRICE(元/股) / 10000 = 亿元。
    实测 TRADE_AVERAGE_PRICE / REAL_PRICE 缺失率 63%，CLOSE_PRICE 仅缺 2.4%，
    故用收盘价估算，且「估算额」必须在卡片文案中明示，不是公告精确金额。
  - 单位校验：1994-08-05 飞乐音响样本 CHANGE_NUM=146.4064、
    CHANGE_FREE_RATIO=2.8155% -> 反推流通股本 5200 万股，确认 CHANGE_NUM 为万股。
  - 去重键 (代码, 股东, 方向, 股数, 起止日, 公告日)，覆盖接口偶发重复录入。
  - 主口径 net_amt_roll12 = 滚动 12 月（增持估算额 − 减持估算额）。

输出：
  data/mood_holder_change_raw.csv   增量明细缓存（首次 2015 起全量，之后回看 15 天）
  data/mood_holder_change.csv       月频聚合：date, inc_amt, dec_amt, net_amt,
                                    *_roll12, n_records

局限：① 金额为收盘价估算，未用成交均价；② 公告日归档，公告本身有滞后；
③ 当月为未完结月，滚动值会随新公告继续抬升；④ 2015 前样本稀疏，作图从此开始；
⑤ 金额未经市值归一化，长期有规模膨胀趋势，以历史分位与相对变化为主，
   不宜跨时代比绝对值。
"""
from __future__ import annotations

import json
import sys
import time

import pandas as pd
import requests

from config import HOLDER_CHANGE_LOOKBACK, HOLDER_CHANGE_ROLL, HOLDER_CHANGE_START
from core import DATA, TODAY, log

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
RAW = DATA / "mood_holder_change_raw.csv"
OUT = DATA / "mood_holder_change.csv"
PROGRESS = DATA / "mood_holder_change_progress.json"  # 已完成的历史年份，支持断点续传

USE_COLS = [
    "SECURITY_CODE", "SECURITY_NAME_ABBR", "HOLDER_NAME", "DIRECTION",
    "CHANGE_NUM", "CLOSE_PRICE", "MARKET", "START_DATE", "END_DATE",
    "NOTICE_DATE", "EITIME",
]
DEDUP_KEYS = ["SECURITY_CODE", "HOLDER_NAME", "DIRECTION", "CHANGE_NUM",
              "START_DATE", "END_DATE", "NOTICE_DATE"]


def _fetch_range(since: str, until: str) -> pd.DataFrame:
    """抓取一个公告日区间内的全部分页。

    东财强制 pageSize<=500；只取必要字段以缩小响应体。单页带 3 次重试，
    避免偶发读超时让整批历史作废（首次全量曾因一页超时失败）。
    """
    params = {
        "sortColumns": "NOTICE_DATE,SECURITY_CODE,EITIME",
        "sortTypes": "-1,-1,-1",
        "pageSize": "500",
        "pageNumber": "1",
        "reportName": "RPT_SHARE_HOLDER_INCREASE",
        "columns": ",".join(USE_COLS),
        "source": "WEB",
        "client": "WEB",
        "filter": f"(NOTICE_DATE>='{since}')(NOTICE_DATE<='{until}')",
    }
    rows, pages, page = [], 0, 1
    while page <= 500:
        params["pageNumber"] = str(page)
        res = None
        for attempt in range(3):
            try:
                r = requests.get(URL, params=params, timeout=20)
                r.raise_for_status()
                res = (r.json().get("result") or {})
                break
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"第 {page} 页重试 3 次仍失败：{e}")
                time.sleep(0.8 * (attempt + 1))
        data = res.get("data") or []
        rows.extend(data)
        pages = int(res.get("pages") or 0)
        if not data or page >= pages:
            break
        page += 1
    log(f"    [holder_change] {since}~{until}: {len(rows)} 条 / {pages} 页")
    return pd.DataFrame(rows)


def _load_progress() -> dict:
    try:
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_progress(d: dict):
    PROGRESS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_raw() -> pd.DataFrame:
    if not RAW.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(RAW, dtype={"SECURITY_CODE": str})
    except Exception as e:
        log(f"    [holder_change] 明细缓存读取失败({e})，重建")
        return pd.DataFrame()


def _merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """合并并去重，立即落盘，保证任何时点中断都不丢已抓年份。"""
    if new.empty:
        return old
    for c in USE_COLS:
        if c not in new.columns:
            new[c] = None
        if c not in old.columns and not old.empty:
            old[c] = None
    df = pd.concat([old, new[USE_COLS]], ignore_index=True)[USE_COLS]
    df = df.drop_duplicates(subset=DEDUP_KEYS, keep="last").reset_index(drop=True)
    df.to_csv(RAW, index=False, encoding="utf-8")
    return df


def _detail_frame(years: list[int] | None = None) -> pd.DataFrame:
    """按年分批补齐历史 + 当年按公告日回看增量。

    历史年份抓完即记进度，中断后续跑跳过；当年每次都重抓近 N 天，
    覆盖盘后补录/修订的公告。可用 --year=YYYY 只跑指定年份做分批执行。
    """
    old = _read_raw()
    done = _load_progress()
    if old.empty:
        log("    [holder_change] 首次构建，按年分批抓取历史明细")
    else:
        log(f"    [holder_change] 已有明细 {len(old)} 条")

    start_year = int(HOLDER_CHANGE_START[:4])
    cur_year = TODAY.year
    todo = years if years is not None else list(range(start_year, cur_year))
    for y in todo:
        if y >= cur_year:
            continue
        key = str(y)
        if done.get(key, 0) > 0:
            continue
        new = _fetch_range(f"{y}-01-01", f"{y}-12-31")
        old = _merge(old, new)
        done[key] = len(new)
        _save_progress(done)
        log(f"    [holder_change] {y} 年完成，累计 {len(old)} 条")

    # 当年增量：回看窗口但不早于当年 1 月 1 日（历史年份已单独抓完）
    if years is not None and cur_year not in years:
        return old
    cur_start = pd.Timestamp(f"{cur_year}-01-01")
    if old.empty:
        since = cur_start
    else:
        mx = pd.to_datetime(old["NOTICE_DATE"], errors="coerce").max()
        since = max(cur_start, mx - pd.Timedelta(days=HOLDER_CHANGE_LOOKBACK))
    new = _fetch_range(since.strftime("%Y-%m-%d"), TODAY.strftime("%Y-%m-%d"))
    old = _merge(old, new)
    return old


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """按公告日月份聚合为增持/减持/净额，并计算滚动窗口。"""
    d = df.copy()
    d["notice_dt"] = pd.to_datetime(d["NOTICE_DATE"], errors="coerce")
    # PIT：剔除晚于今天的公告（东财常在盘后预挂次日公告）
    d = d[d["notice_dt"] <= TODAY]
    d["change_wan"] = pd.to_numeric(d["CHANGE_NUM"], errors="coerce").abs()
    d["close"] = pd.to_numeric(d["CLOSE_PRICE"], errors="coerce")
    d["amt_yi"] = d["change_wan"] * d["close"] / 10000.0
    d = d.dropna(subset=["notice_dt", "amt_yi"])
    d["ym"] = d["notice_dt"].dt.to_period("M").dt.to_timestamp()

    inc = d[d["DIRECTION"] == "增持"].groupby("ym")["amt_yi"].sum().rename("inc_amt")
    dec = d[d["DIRECTION"] == "减持"].groupby("ym")["amt_yi"].sum().rename("dec_amt")
    # 东财 MARKET 区分「二级市场 / 大宗交易 / 二级市场以及大宗交易 / 其他(协议划转)」。
    # 内部划转、协议转让会同时记一笔增持和一笔减持，净额抵消但虚增总额；
    # 这里单列「仅二级市场」金额供卡片说明口径，主口径仍保留广义全量。
    sec = d[d["MARKET"].astype(str).str.strip() == "二级市场"].groupby("ym")["amt_yi"].sum().rename("sec_amt")
    cnt = d.groupby("ym").size().rename("n_records")
    g = pd.concat([inc, dec, sec, cnt], axis=1).fillna(0.0)

    start = pd.Timestamp(HOLDER_CHANGE_START)
    end = TODAY.to_period("M").to_timestamp()
    full = pd.date_range(start, end, freq="MS")
    g = g.reindex(full, fill_value=0.0)
    g.index.name = "date"
    g["net_amt"] = g["inc_amt"] - g["dec_amt"]

    for c in ("inc_amt", "dec_amt", "net_amt", "sec_amt"):
        g[f"{c}_roll{HOLDER_CHANGE_ROLL}"] = (
            g[c].rolling(HOLDER_CHANGE_ROLL, min_periods=HOLDER_CHANGE_ROLL).sum()
        )
    return g.dropna(subset=[f"net_amt_roll{HOLDER_CHANGE_ROLL}"])


def main():
    t0 = time.time()
    years = None
    for a in sys.argv[1:]:
        if a.startswith("--year="):
            years = [int(a.split("=", 1)[1])]

    # 分批模式：只补历史明细、不产出聚合，避免历史年份没抓齐时写出不完整序列。
    if years is not None:
        detail = _detail_frame(years)
        log(f"[holder_change] 分批模式完成，明细累计 {len(detail)} 条，耗时 {time.time()-t0:.1f}s")
        return

    try:
        detail = _detail_frame()
        if detail.empty or len(detail) < 100:
            raise ValueError(f"明细过少({len(detail)})，疑似接口异常")
        g = _aggregate(detail)
        if g.empty:
            raise ValueError("聚合结果为空")
    except Exception as e:
        log(f"[holder_change] 抓取/聚合失败({e})，沿用已有缓存")
        if OUT.exists():
            return
        raise

    g.to_csv(OUT, date_format="%Y-%m-%d")
    col = f"net_amt_roll{HOLDER_CHANGE_ROLL}"
    r = g.iloc[-1]
    log(f"输出 {OUT.name}：{len(g)} 个月点，{g.index.min().date()} ~ {g.index.max().date()}，耗时 {time.time()-t0:.1f}s")
    inc_col = f"inc_amt_roll{HOLDER_CHANGE_ROLL}"
    dec_col = f"dec_amt_roll{HOLDER_CHANGE_ROLL}"
    sec_col = f"sec_amt_roll{HOLDER_CHANGE_ROLL}"
    total = float(r[inc_col] + r[dec_col])
    sec_pct = (float(r[sec_col]) / total * 100) if total else 0.0
    log(f"当前滚动 {HOLDER_CHANGE_ROLL} 月净增持 {r[col]:.1f} 亿 "
        f"（增持 {r[inc_col]:.1f} / 减持 {r[dec_col]:.1f}，其中仅二级市场占 {sec_pct:.0f}%），"
        f"当月公告 {int(r['n_records'])} 笔，历史区间 {g[col].min():.1f} ~ {g[col].max():.1f} 亿")


if __name__ == "__main__":
    main()
