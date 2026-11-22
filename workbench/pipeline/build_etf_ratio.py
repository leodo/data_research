# -*- coding: utf-8 -*-
"""
头部300ETF占沪深300流通市值比例（公开数据自算）
================================================
指标定义（参考 legulegu.com/stockdata/hs300-etf-ratio）：
  分子 = 头部沪深300ETF资产净值之和 = Σ(份额 × 单位净值)
  分母 = 沪深300流通市值

本实现（全程公开数据，不抓取乐咕成品）：
  分子：三只上交所300ETF 510300/510310/510330
        - 份额：上交所每日ETF规模表 akshare.fund_etf_scale_sse(date) 逐交易日抓
        - 单位净值：天天基金 fund_open_fund_info_em(indicator='单位净值走势') 一次取全历史
        159919(嘉实,深交所)因深交所接口在本环境不可用，未纳入（透明标注）。
  分母：沪深300流通市值 ≈ 沪深300指数收盘点位 × 基准系数（流通市值与点位高度线性相关）
        - 点位：新浪 index_zh_daily_sina('sh000300')
        - 基准系数：用当前沪深300流通市值锚校准，使量级与真实占比一致

输出：data/mood_etf_ratio.csv (date, ratio)
"""
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
import akshare as ak

from workbench.config import (START_ETF_RATIO as START, ETF_CODES as ETFS, MV_ANCHOR_YI)
from workbench.core import DATA, TODAY_TAG as TODAY, log, trade_dates as cal_dates_base

OUT = DATA / "mood_etf_ratio.csv"
SHARES = DATA / "mood_etf_shares_raw.csv"


def cal_dates():
    # 官方完整交易日历（含当日），新浪指数日历常滞后1个交易日
    return cal_dates_base(START, TODAY)


def fetch_nav():
    """四只ETF单位净值历史（一次取全）"""
    out = {}
    for code in ETFS:
        try:
            d = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            d["净值日期"] = pd.to_datetime(d["净值日期"])
            s = d.set_index("净值日期")["单位净值"].dropna()
            out[code] = s
            log(f"  净值 {code}: {len(s)} 日 {s.index.min().date()}~{s.index.max().date()}")
        except Exception as e:
            log(f"  净值 {code} 失败: {type(e).__name__} {str(e)[:60]}")
    return out


def fetch_share_once(ds):
    """某交易日三只上交所ETF份额"""
    s = ak.fund_etf_scale_sse(date=ds)
    r = {}
    for code in ETFS:
        row = s[s["基金代码"].astype(str) == code]
        if len(row):
            v = float(row["基金份额"].iloc[0])
            if np.isfinite(v) and v > 0:
                r[code] = v  # 单位:份
    return r


def main():
    t0 = time.time()
    log("=" * 56)
    log("头部300ETF占沪深300流通市值比例（公开数据自算）")
    log("=" * 56)
    cal = cal_dates()
    log(f"交易日 {len(cal)} {cal[0]}~{cal[-1]}")

    # 份额：断点续抓
    done = {}
    if SHARES.exists():
        old = pd.read_csv(SHARES)
        for _, r in old.iterrows():
            ds = str(r["date"]).split(".")[0]
            for code in ETFS:
                v = r.get(code, np.nan)
                if np.isfinite(v) and v > 0:
                    done.setdefault(ds, {})[code] = float(v)
        log(f"续抓：份额缓存 {len(done)} 日")

    for i, ds in enumerate(cal):
        if ds in done and all(c in done[ds] for c in ETFS):
            continue
        for att in range(2):
            try:
                r = fetch_share_once(ds)
                if r:
                    done.setdefault(ds, {}).update(r)
                break
            except Exception:
                if att < 1:
                    time.sleep(0.5)
        if i % 200 == 0 and i > 0:
            rows = []
            for d, m in done.items():
                row = {"date": d}
                row.update(m)
                rows.append(row)
            pd.DataFrame(rows).to_csv(SHARES, index=False)
            log(f"  份额进度 {i}/{len(cal)} 有效{len(done)} 用时{time.time()-t0:.0f}s")
    rows = []
    for d, m in done.items():
        row = {"date": d}
        row.update(m)
        rows.append(row)
    pd.DataFrame(rows).to_csv(SHARES, index=False)
    log(f"份额抓取完成 {len(done)} 日 用时{time.time()-t0:.0f}s")

    # 净值
    log("抓取单位净值 ...")
    nav = fetch_nav()

    # 份额 DataFrame
    sh = pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()
    if len(sh):
        sh.index = pd.to_datetime([str(x).split(".")[0] for x in sh.index], format="%Y%m%d")
        sh = sh.sort_index()
        for c in ETFS:
            if c not in sh.columns:
                sh[c] = np.nan
            sh[c] = pd.to_numeric(sh[c], errors="coerce")

    # 沪深300点位
    idx = ak.index_zh_daily_sina(symbol="sh000300")
    idx["date"] = pd.to_datetime(idx["date"])
    idx = idx.set_index("date")["close"].sort_index()
    idx = idx[(idx.index >= pd.Timestamp(cal[0])) & (idx.index <= pd.Timestamp(TODAY))]

    # 基准系数: 最新点位 -> 流通市值锚
    latest_close = float(idx.dropna().iloc[-1])
    coef = MV_ANCHOR_YI / latest_close  # 亿元/点
    log(f"沪深300最新点位 {latest_close:.2f}, 流通市值锚 {MV_ANCHOR_YI:.0f}亿, 系数 {coef:.2f}亿/点")

    # 逐日: 分子(亿元) = Σ 份额(份) × 净值(元) / 1e8
    nav_df = pd.DataFrame(nav)
    nav_df.index = pd.to_datetime(nav_df.index)
    nav_df = nav_df.sort_index().ffill()
    etf_asset_yi = pd.DataFrame(index=sh.index)
    for c in ETFS:
        nv = nav_df[c].reindex(sh.index).ffill() if c in nav_df.columns else np.nan
        etf_asset_yi[c] = sh[c] * nv / 1e8
    numerator = etf_asset_yi.sum(axis=1, min_count=1)

    # 分母: 流通市值代理(亿元) = 点位 × 系数
    mv_proxy = idx.reindex(sh.index).ffill() * coef

    ratio = (numerator / mv_proxy * 100).dropna()
    ratio = ratio[(ratio > 0) & (ratio < 20)]
    ratio.name = "ratio"
    ratio.to_csv(OUT)
    log(f"完成 {len(ratio)} 日 {ratio.index.min().date()}~{ratio.index.max().date()}")
    log(f"  min/max = {ratio.min():.3f}% / {ratio.max():.3f}%  最新 {ratio.iloc[-1]:.3f}%")
    log(f"  近5 = {[round(x,3) for x in ratio.tail(5).tolist()]}")
    log(f"输出 {OUT}")


if __name__ == "__main__":
    main()
