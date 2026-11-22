# -*- coding: utf-8 -*-
"""
A股交易拥挤度（公开数据自算）
================================
主指标（业界标准口径：中金/广发/乐咕一致）：
  每日把全部A股按成交额降序排列，取前5%个股的成交额之和 ÷ 全部A股成交额 × 100
  - 低(<40%)=交易分散、结构健康；中(40~50%)=集中度上升；高(>50%)=抱团/结构风险
  - 反向情绪指标：越高越拥挤、越需警惕（对应情绪分越高）

辅助指标：
  - HHI = Σ(个股成交额占比²)，有效交易个股数 = 1/HHI（全分布集中度，比单一阈值稳健）
  - Top10% 成交额占比（多切点看集中度曲线形状）

数据（全程公开，不抓乐咕成品）：
  - 全A列表：akshare.stock_zh_a_spot_sina()
  - 个股历史成交额：akshare.stock_zh_a_hist_sina(symbol, start_date)，取 date+amount
  - 多进程并发（新浪接口底层 mini_racer 非线程安全，故用进程），每只存独立CSV，天然断点续抓

输出：data/mood_crowding.csv (date, top5_ratio, top10_ratio, hhi, effective_n, n_stocks, total_amt_yi)
"""
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

from workbench.config import NPROC, START_CROWDING as START
from workbench.core import DATA, TODAY_TAG as TODAY, log

ADIR = DATA / "crowding_amounts"      # 每只股票一个CSV: date,amount
OUT = DATA / "mood_crowding.csv"


def get_universe():
    import akshare as ak
    d = ak.stock_zh_a_spot_sina()
    codes = sorted(d["代码"].astype(str).tolist())
    return codes


def fetch_one(code):
    """抓单只股票历史成交额，存独立CSV。已有文件则只增量补最新交易日。返回(code, ok/err)"""
    import akshare as ak
    fp = ADIR / f"{code}.csv"
    last_date = None
    old = None
    if fp.exists():   # 断点续抓：已有则只读出最后日期，改为增量
        try:
            old = pd.read_csv(fp)
            if len(old):
                last_date = str(old["date"].iloc[-1]).replace("-", "")[:8]
                if last_date >= TODAY:
                    return code, "cached"
        except Exception:
            old = None
    for att in range(3):
        try:
            # 有旧文件时只抓最后日期之后的数据（多取几天重叠以防漏），再合并去重
            start = START
            if last_date:
                start = (pd.Timestamp(last_date) - pd.Timedelta(days=7)).strftime("%Y%m%d")
            d = ak.stock_zh_a_hist_sina(symbol=code, start_date=start, end_date=TODAY)
            if d is None or len(d) == 0:
                return code, "empty"
            dt = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            amt = pd.to_numeric(d["amount"], errors="coerce").values.astype("float64")
            new = pd.DataFrame({"date": dt.values, "amount": amt})
            if old is not None and len(old):
                new = pd.concat([old, new], ignore_index=True)
            new = new.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date")
            new.to_csv(fp, index=False)
            return code, "ok" if old is None else "updated"
        except Exception:
            if att < 2:
                time.sleep(0.5 * (att + 1))
    return code, "err"


def fetch_all(codes):
    ADIR.mkdir(parents=True, exist_ok=True)
    existing = sum(1 for c in codes if (ADIR / f"{c}.csv").exists())
    # 全部交给 fetch_one：它内部会判断"已到最新则跳过 / 否则增量补最新交易日"
    log(f"全A股票 {len(codes)} 只，已有缓存 {existing} 只（逐只检查并增量补齐至 {TODAY}）")
    todo = list(codes)
    t0 = time.time()
    done = 0
    errs = 0
    n_upd = 0
    with Pool(NPROC) as p:
        for code, st in p.imap_unordered(fetch_one, todo, chunksize=8):
            done += 1
            if st == "err":
                errs += 1
            if st == "updated":
                n_upd += 1
            if done % 500 == 0:
                el = time.time() - t0
                log(f"进度 {done}/{len(todo)}  已更新{n_upd}  失败{errs}  用时{el:.0f}s")
    log(f"抓取完成：{done} 只，失败 {errs}，用时 {time.time()-t0:.0f}s")


def compute(min_stocks=100, out_path=None):
    """按日横截面计算拥挤度指标"""
    if out_path is None:
        out_path = OUT
    log("计算每日横截面拥挤度 ...")
    files = sorted(ADIR.glob("*.csv"))
    log(f"读取 {len(files)} 只股票的成交额文件")
    rows_by_date = {}
    for fp in files:
        try:
            d = pd.read_csv(fp)
            d = d[d["amount"] > 0]
            for dt, a in zip(d["date"], d["amount"]):
                rows_by_date.setdefault(str(dt), []).append(float(a))
        except Exception:
            pass

    rows = []
    for dt in sorted(rows_by_date):
        amt = np.array(rows_by_date[dt], dtype="float64")
        n = len(amt)
        if n < min_stocks:
            continue
        total = amt.sum()
        if total <= 0:
            continue
        s = np.sort(amt)[::-1]
        k5 = max(1, int(np.ceil(n * 0.05)))
        k10 = max(1, int(np.ceil(n * 0.10)))
        top5 = s[:k5].sum() / total * 100
        top10 = s[:k10].sum() / total * 100
        share = amt / total
        hhi = float(np.sum(share ** 2))
        eff = 1.0 / hhi if hhi > 0 else n
        rows.append((dt, round(top5, 4), round(top10, 4),
                     round(hhi, 6), round(eff, 2), n, round(total / 1e8, 2)))

    out = pd.DataFrame(rows, columns=["date", "top5_ratio", "top10_ratio", "hhi",
                                      "effective_n", "n_stocks", "total_amt_yi"])
    out = out[out["date"] <= TODAY].reset_index(drop=True)
    out.to_csv(out_path, index=False)
    log(f"输出 {out_path}：{len(out)} 行，{out['date'].iloc[0]} ~ {out['date'].iloc[-1]}")
    v = out["top5_ratio"].values
    log(f"top5占比 min={v.min():.2f}% max={v.max():.2f}% 当前={v[-1]:.2f}%")
    for y in [2015, 2018, 2020, 2021, 2024, 2025, 2026]:
        x = out[out["date"].str.startswith(str(y))]["top5_ratio"]
        if len(x):
            log(f"  {y}: n={len(x)} med={x.median():.2f}% min={x.min():.2f}% max={x.max():.2f}%")
    cur = v[-1]
    sig = "结构风险(>50%)" if cur > 50 else ("集中上升(40-50%)" if cur > 40 else "分散健康(<40%)")
    log(f"当前信号：{sig}")
    log(f"当前辅助：top10={out['top10_ratio'].iloc[-1]:.2f}% HHI={out['hhi'].iloc[-1]:.5f} 有效个股数={out['effective_n'].iloc[-1]:.0f}")


def main():
    if "compute" in sys.argv[1:]:
        compute()
        return
    codes = get_universe()
    fetch_all(codes)
    compute()


if __name__ == "__main__":
    main()
