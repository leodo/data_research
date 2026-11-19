# -*- coding: utf-8 -*-
"""
市场宽度扩展包：新高新低差 / 腾落线 / 动量与均线广度（零网络请求）
==================================================================
完全复用 build_breadth.py 已落盘的 data/breadth_prices/*.csv（5547 只个股
未复权收盘价），按同一交易日历做横截面 bincount 向量化统计，不发起任何抓取。

输出指标（均为逐日横截面占比，%）：
  nh_nl        (创60日新高家数 − 创60日新低家数) ÷ 有效股票数 ×100
  new_high / new_low   创新高/新低家数占比
  adv_ratio / dec_ratio 上涨/下跌家数占比（与前一日收盘比较）
  ad_diff      (上涨家数 − 下跌家数) ÷ 有效股票数 ×100（当日净广度）
  ad_line      ad_diff 的累计和（腾落线，绝对值无意义，看趋势与背离）
  mom20_ratio  近20日收益为正的个股占比
  ma_bull      MA5>MA20>MA60 多头排列个股占比

关键用法：顶底常伴随「背离」——指数创新低而 nh_nl / ad_line 不再创新低，
说明下跌动能由少数权重股贡献、内部抛压衰竭；指数创新高而宽度走低则
提示涨势收窄。本脚本只提供序列，背离判定在 build_turning.py 的共振板中完成。

股票池为当前在市A股（含北交所），已退市个股缺失 → 早期轻微幸存者偏差；
未复权价格除权跳空对横截面统计影响有限（每日除权占比极小）。

输出：data/mood_breadth_extra.csv
用法：python build_breadth_extra.py
"""
import time

import numpy as np
import pandas as pd

from config import BREADTH_MIN_STOCKS, START_BREADTH
from core import DATA, TODAY_TAG, log, trade_dates

ADIR = DATA / "breadth_prices"
OUT = DATA / "mood_breadth_extra.csv"
NHNL_WIN = 60          # 新高新低回看窗口（交易日）
MOM_WIN = 20           # 动量窗口


def compute():
    t0 = time.time()
    cal = trade_dates(START_BREADTH, TODAY_TAG)
    cal_iso = [f"{x[:4]}-{x[4:6]}-{x[6:]}" for x in cal]
    dmap = {d: i for i, d in enumerate(cal_iso)}
    n_days = len(cal_iso)
    z = lambda: np.zeros(n_days, dtype="float64")
    total, adv, dec, nh, nl, mom, bull = z(), z(), z(), z(), z(), z(), z()

    files = sorted(ADIR.glob("*.csv"))
    log(f"读取 {len(files)} 只个股收盘价，日期桶 {n_days} 个（{cal_iso[0]} ~ {cal_iso[-1]}）")
    used = 0
    for fp in files:
        try:
            d = pd.read_csv(fp)
            if "close" not in d.columns or len(d) < NHNL_WIN + 1:
                continue
            s = pd.to_numeric(d["close"], errors="coerce")
            prev = s.shift(1)
            hi = prev.rolling(NHNL_WIN, min_periods=NHNL_WIN).max()
            lo = prev.rolling(NHNL_WIN, min_periods=NHNL_WIN).min()
            m20 = s / s.shift(MOM_WIN) - 1.0
            ma5 = s.rolling(5, min_periods=5).mean()
            ma20 = s.rolling(20, min_periods=20).mean()
            ma60 = s.rolling(60, min_periods=60).mean()
            valid = hi.notna() & lo.notna() & m20.notna() & ma60.notna() & s.notna() & prev.notna()
            d = d.loc[valid]
            if not len(d):
                continue
            idx = d["date"].astype(str).map(dmap)
            ok = idx.notna()
            if not ok.any():
                continue
            codes = idx[ok].to_numpy(dtype="int64")
            s, prev, hi, lo, m20 = s[valid][ok], prev[valid][ok], hi[valid][ok], lo[valid][ok], m20[valid][ok]
            ma5, ma20, ma60 = ma5[valid][ok], ma20[valid][ok], ma60[valid][ok]
            w = lambda x: x.to_numpy(dtype="float64")
            total += np.bincount(codes, minlength=n_days)
            adv += np.bincount(codes, weights=w(s > prev), minlength=n_days)
            dec += np.bincount(codes, weights=w(s < prev), minlength=n_days)
            nh += np.bincount(codes, weights=w(s > hi), minlength=n_days)
            nl += np.bincount(codes, weights=w(s < lo), minlength=n_days)
            mom += np.bincount(codes, weights=w(m20 > 0), minlength=n_days)
            bull += np.bincount(codes, weights=w((ma5 > ma20) & (ma20 > ma60)), minlength=n_days)
            used += 1
        except Exception:
            continue

    rows, ad_line = [], 0.0
    for i, dt in enumerate(cal_iso):
        n = total[i]
        if n < BREADTH_MIN_STOCKS:
            continue
        ad_diff = (adv[i] - dec[i]) / n * 100
        ad_line += ad_diff
        rows.append((
            dt,
            round((nh[i] - nl[i]) / n * 100, 4),
            round(nh[i] / n * 100, 4), round(nl[i] / n * 100, 4),
            round(adv[i] / n * 100, 4), round(dec[i] / n * 100, 4),
            round(ad_diff, 4), round(ad_line, 4),
            round(mom[i] / n * 100, 4), round(bull[i] / n * 100, 4),
            int(n),
        ))
    out = pd.DataFrame(rows, columns=["date", "nh_nl", "new_high", "new_low",
                                      "adv_ratio", "dec_ratio", "ad_diff", "ad_line",
                                      "mom20_ratio", "ma_bull", "n_stocks"])
    out.to_csv(OUT, index=False)
    log(f"有效股票 {used} 只，输出 {len(out)} 行：{out['date'].iloc[0]} ~ {out['date'].iloc[-1]}，"
        f"用时 {time.time()-t0:.0f}s")
    log(f"当前：NH-NL={out['nh_nl'].iloc[-1]:.2f}%  新高={out['new_high'].iloc[-1]:.2f}%  "
        f"新低={out['new_low'].iloc[-1]:.2f}%  上涨占比={out['adv_ratio'].iloc[-1]:.2f}%  "
        f"20日动量广度={out['mom20_ratio'].iloc[-1]:.2f}%  多头排列={out['ma_bull'].iloc[-1]:.2f}%")


if __name__ == "__main__":
    compute()
