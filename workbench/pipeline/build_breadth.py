# -*- coding: utf-8 -*-
"""
全A市场宽度：站上 200 日均线的个股占比（公开数据自算）
=====================================================
口径：逐日横截面，统计当日收盘价 > 自身 200 日均线的个股数 ÷ 当日有效股票数 × 100。
  - 高（>70%）= 普涨、趋势强、情绪偏热；低（<20%）= 普跌、超卖、常对应底部区域
  - 同时输出 60 日均线口径作为辅助，用于观察短中期宽度背离
  - 未复权价格：除权日会有跳空，但每日除权股票占比极小，横截面统计影响有限（见 source 说明）

数据：全A列表取 ak.stock_zh_a_spot_sina()；个股 close 取 ak.stock_zh_a_hist_sina()
      （与 build_crowding.py 同一接口，但拥挤度缓存只有 date,amount，无法复用，故单独建缓存）
      新浪底层 mini_racer 非线程安全 → 多进程 + 每只独立 CSV，天然断点续抓

输出：data/mood_breadth.csv (date, pct_above_ma200, pct_above_ma60, n_stocks, n_above)
用法：python build_breadth.py            # 增量抓取 + 计算
      python build_breadth.py compute   # 只用现有缓存重算
"""
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

from workbench.config import (NPROC, START_BREADTH, MA_LONG, MA_SHORT, BREADTH_MIN_STOCKS)
from workbench.core import DATA, TODAY_TAG, log, trade_dates

ADIR = DATA / "breadth_prices"          # 每只股票一个 CSV: date,close
OUT = DATA / "mood_breadth.csv"


def get_universe():
    from build_crowding import get_universe as _get
    return _get()


def fetch_one(code):
    """抓单只股票历史收盘价，存独立 CSV；已有文件则只增量补最新交易日。"""
    import akshare as ak
    fp = ADIR / f"{code}.csv"
    last_date, old = None, None
    if fp.exists():
        try:
            old = pd.read_csv(fp)
            if len(old):
                if "close" not in old.columns:
                    old = None                 # 老格式文件，丢弃重抓
                else:
                    last_date = str(old["date"].iloc[-1]).replace("-", "")[:8]
                    if last_date >= TODAY_TAG:
                        return code, "cached"
        except Exception:
            old = None
    for att in range(3):
        try:
            start = START_BREADTH
            if last_date:
                start = (pd.Timestamp(last_date) - pd.Timedelta(days=7)).strftime("%Y%m%d")
            d = ak.stock_zh_a_hist_sina(symbol=code, start_date=start, end_date=TODAY_TAG)
            if d is None or len(d) == 0:
                return code, "empty"
            dt = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            cl = pd.to_numeric(d["close"], errors="coerce").values.astype("float64")
            new = pd.DataFrame({"date": dt.values, "close": cl})
            if old is not None and len(old):
                new = pd.concat([old, new], ignore_index=True)
            new = (new.dropna(subset=["date", "close"])
                      .drop_duplicates("date", keep="last").sort_values("date"))
            new.to_csv(fp, index=False)
            return code, "ok" if old is None else "updated"
        except Exception:
            if att < 2:
                time.sleep(0.5 * (att + 1))
    return code, "err"


def fetch_all(codes):
    ADIR.mkdir(parents=True, exist_ok=True)
    existing = sum(1 for c in codes if (ADIR / f"{c}.csv").exists())
    log(f"全A股票 {len(codes)} 只，已有收盘价缓存 {existing} 只（逐只增量补齐至 {TODAY_TAG}）")
    t0, done, errs, n_upd = time.time(), 0, 0, 0
    with Pool(NPROC) as p:
        for code, st in p.imap_unordered(fetch_one, codes, chunksize=8):
            done += 1
            if st == "err":
                errs += 1
            if st == "updated":
                n_upd += 1
            if done % 500 == 0:
                log(f"进度 {done}/{len(codes)}  已更新{n_upd}  失败{errs}  用时{time.time()-t0:.0f}s")
    log(f"抓取完成：{done} 只，失败 {errs}，用时 {time.time()-t0:.0f}s")


def compute(out_path=None):
    """按交易日历建立日期桶，用 bincount 向量化累加，避免千万级 Python 循环。"""
    out_path = out_path or OUT
    log("计算每日横截面市场宽度 ...")
    cal = trade_dates(START_BREADTH, TODAY_TAG)
    cal_iso = [f"{x[:4]}-{x[4:6]}-{x[6:]}" for x in cal]
    dmap = {d: i for i, d in enumerate(cal_iso)}
    n_days = len(cal_iso)
    total = np.zeros(n_days, dtype="float64")
    above_l = np.zeros(n_days, dtype="float64")
    above_s = np.zeros(n_days, dtype="float64")

    files = sorted(ADIR.glob("*.csv"))
    log(f"读取 {len(files)} 只股票的收盘价，日期桶 {n_days} 个（{cal_iso[0]} ~ {cal_iso[-1]}）")
    used = 0
    for fp in files:
        try:
            d = pd.read_csv(fp)
            if "close" not in d.columns or len(d) < MA_LONG + 1:
                continue
            s = pd.to_numeric(d["close"], errors="coerce")
            ma_l = s.rolling(MA_LONG, min_periods=MA_LONG).mean()
            ma_s = s.rolling(MA_SHORT, min_periods=MA_SHORT).mean()
            ok = ma_l.notna() & s.notna()
            d = d.loc[ok]
            if not len(d):
                continue
            s, ma_l, ma_s = s[ok], ma_l[ok], ma_s[ok]
            dates = d["date"].astype(str)
            idx = dates.map(dmap)
            valid = idx.notna()
            if not valid.any():
                continue
            codes = idx[valid].to_numpy(dtype="int64")
            total += np.bincount(codes, minlength=n_days)
            above_l += np.bincount(codes, weights=(s[valid] > ma_l[valid]).to_numpy(dtype="float64"),
                                   minlength=n_days)
            above_s += np.bincount(codes, weights=(s[valid] > ma_s[valid]).to_numpy(dtype="float64"),
                                   minlength=n_days)
            used += 1
        except Exception:
            continue

    rows = []
    for i, dt in enumerate(cal_iso):
        n = int(total[i])
        if n < BREADTH_MIN_STOCKS:
            continue
        rows.append((dt, round(above_l[i] / n * 100, 4),
                     round(above_s[i] / n * 100, 4), n, int(above_l[i])))
    out = pd.DataFrame(rows, columns=["date", "pct_above_ma200", "pct_above_ma60",
                                      "n_stocks", "n_above"])
    out.to_csv(out_path, index=False)
    log(f"有效股票 {used} 只，输出 {len(out)} 行：{out['date'].iloc[0]} ~ {out['date'].iloc[-1]}")
    v = out["pct_above_ma200"].values
    log(f"200日宽度 min={v.min():.2f}% max={v.max():.2f}% 当前={v[-1]:.2f}%")
    log(f"当前辅助：60日宽度={out['pct_above_ma60'].iloc[-1]:.2f}%  有效股票={out['n_stocks'].iloc[-1]}")


def main():
    if "compute" in sys.argv[1:]:
        compute()
        return
    codes = get_universe()
    fetch_all(codes)
    compute()


if __name__ == "__main__":
    main()
