# -*- coding: utf-8 -*-
"""
重建全市场换手率历史（基于交易所权威成交额/市值）
==================================================
换手率定义 = 两市成交额 / 两市总市值 * 100（总市值口径）

数据可用性（2026-11 实测）：
  深交所 stock_summary_szse：2015-01 ~ 2021-07 可用，之后返回损坏文件
  上交所 stock_deal_daily_sse：2022-01 起可用

口径统一（关键）：
  深市换手率系统性高于沪市（深市流通盘小、交投活跃），不能直接拼接。
  用市值权重把单市场换手率换算到"全市场"口径：
    全市场换手率 ≈ 沪换手率 * wsh + 深换手率 * wsz
  wsh/wsz 取各时段两市总市值占比（相对稳定的经验值）：
    早期(2015-2021)：沪深市值约 45:55  -> 全市场 ≈ 深 * 0.55 + 沪 * 0.45
    缺沪市时，用全市场/深市 ≈ 0.72 近似
  2022 起沪市有数据，深市无：全市场 ≈ 沪 * 1.45（按市值占比反推）

  说明：这是用单市场权威数据 + 市值权重还原的"全市场换手率"近似，
  量级与真实全市场一致，分位走势可靠；页面来源中会明确标注。

输出：data/mood_turnover_rate.csv (index=date, 列=turnover_rate)
"""
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
import akshare as ak

from config import (START_TURNOVER as START, K_SZ_TO_ALL, K_SH_TO_ALL)
from core import DATA, TODAY_TAG as TODAY, log, trade_dates

OUT = DATA / "mood_turnover_rate.csv"
RAW = DATA / "mood_turnover_raw.csv"
DONE = DATA / "mood_turnover_done.csv"


def fetch_sz_rate(ds):
    sz = ak.stock_summary_szse(date=ds)
    row = sz[sz["证券类别"] == "股票"].iloc[0]
    amt, tot = float(row["成交金额"]), float(row["总市值"])
    return amt / tot * 100.0 if amt > 0 and tot > 0 else np.nan


def fetch_sh_rate(ds):
    sh = ak.stock_deal_daily_sse(date=ds)
    amt = float(sh[sh["单日情况"] == "成交金额"]["股票"].iloc[0])
    tot = float(sh[sh["单日情况"] == "市价总值"]["股票"].iloc[0])
    return amt / tot * 100.0 if amt > 0 and tot > 0 else np.nan


def main():
    # 用官方完整交易日历（含当日），而非新浪指数日历——后者常滞后1个交易日
    cal = trade_dates(START, TODAY)
    log(f"交易日 {len(cal)}  {cal[0]} ~ {cal[-1]}")

    # 断点续抓：优先读已算好的结果
    done = {}
    if DONE.exists():
        old = pd.read_csv(DONE)
        done = {str(r["date"]).split(".")[0]: float(r["rate"]) for _, r in old.iterrows() if np.isfinite(r["rate"])}
        log(f"续抓：已有结果 {len(done)} 日")
    if not done and RAW.exists():
        old = pd.read_csv(RAW)
        for _, r in old.iterrows():
            ds = str(r["date"])
            if np.isfinite(r.get("sz_amt", np.nan)) and r["sz_amt"] > 0 and np.isfinite(r.get("sz_tot", np.nan)):
                done[ds] = r["sz_amt"] / r["sz_tot"] * 100.0 * K_SZ_TO_ALL
        log(f"复用深市缓存 {len(done)} 日")

    t0 = time.time()
    for i, ds in enumerate(cal):
        if ds in done:
            continue
        if ds >= "20220104":
            for att in range(2):
                try:
                    v = fetch_sh_rate(ds)
                    if np.isfinite(v) and v > 0:
                        done[ds] = v * K_SH_TO_ALL
                    break
                except Exception:
                    if att < 1: time.sleep(0.4)
        if i % 100 == 0 and i > 0:
            # 增量落盘，防中断丢失
            pd.DataFrame({"date": list(done.keys()), "rate": list(done.values())}).to_csv(DONE, index=False)
            log(f"进度 {i}/{len(cal)} 有效{len(done)} 用时{time.time()-t0:.0f}s")
    pd.DataFrame({"date": list(done.keys()), "rate": list(done.values())}).to_csv(DONE, index=False)

    s = pd.Series(done)
    s.index = pd.to_datetime([str(x).split(".")[0] for x in s.index], format="%Y%m%d")
    s = s.sort_index()
    full = pd.Series(index=pd.to_datetime(cal), dtype=float)
    full.loc[s.index] = s.values
    full = full.interpolate(method="linear", limit=7).dropna()
    full.name = "turnover_rate"
    full.to_csv(OUT)

    log(f"完成 {len(full)} 日 {full.index.min().date()} ~ {full.index.max().date()}")
    log(f"最新 {full.iloc[-1]:.4f}% 近5日 {[round(x,3) for x in full.tail(5).tolist()]}")
    log(f"min/max = {full.min():.3f}% / {full.max():.3f}%")
    log(f"输出 {OUT}")


if __name__ == "__main__":
    main()
