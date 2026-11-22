# -*- coding: utf-8 -*-
"""
阿姆氏指标(TRIN) + 市场宽广度体系（纯本地派生，零个股抓取）
================================================================
复用两份已落盘的逐股缓存，按同一交易日历对齐，不发起任何个股请求：
  data/breadth_prices/*.csv    date,close     5547 只未复权收盘价
  data/crowding_amounts/*.csv  date,amount    5579 只成交额(元)

一、阿姆氏指标 Arms Index / TRIN（Richard Arms, 1967）
  TRIN = (上涨家数 / 下跌家数) ÷ (上涨股成交量 / 下跌股成交量)
  > 1 = 下跌股放量、抛压主导；< 1 = 上涨股放量、买盘主导
  经验区间 0.5~2.0 为常态；>2 超卖（常对应恐慌底部）、<0.5 超买/资金抱团

  输出两套口径：
    trin      成交额口径(Dollar TRIN)：分母用上涨/下跌股成交额，高价股权重大
    trin_vol  近似成交量口径(经典)：用 amount/close 反推股数再分组，去掉价格权重
  新浪 stock_zh_a_hist_sina 原始字段同时含 volume 与 amount，实测 sh600000
  近 3 日 amount/close 与真实 volume 误差 0.05%~0.4%，故该近似可信。
  日频噪声大，另给 trin5 / trin10 平滑（10 日平滑为常用判极值口径）。

  **A股口径注意**：极端普跌日 n_adv/n_dec 极小，若少数上涨股吸走大量成交，
  TRIN 会系统性偏低——此时并非「买盘强劲」，而是资金抱团叠加全面失血，
  不能机械套用美股教科书的超买结论，须结合上涨家数占比一起读。

二、市场宽广度体系
  breadth_score  6 个宽度序列的 expanding 分位等权合成(0~100)：
                 站上MA200/MA60占比、NH-NL、上涨家数占比、20日动量广度、多头排列
  mcc_osc        McClellan Oscillator = EMA19(净广度%) − EMA39(净广度%)
                 经典用家数差，但 A 股上市公司数由 2015 年约 2800 增至 2026 年
                 约 5500，绝对家数量纲随时代膨胀，故改用占比(百分点)使阈值跨时代可比
  mcc_sum        McClellan Summation Index = mcc_osc 累计和
  bt_ema         Zweig Breadth Thrust = EMA10(上涨家数/(上涨+下跌) ×100)
  bt_signal      10 日内由 ≤40% 升至 ≥61.5% 的经典「宽度推力」底部反转触发

股票池为当前在市 A 股（含北交所），存在幸存者偏差；收盘价为未复权口径，
除权跳空对横截面统计影响有限；两份缓存由不同批次抓取，按 date 内连接取交集。

输出：data/mood_arms.csv / data/mood_breadth_advance.csv
用法：python build_arms.py
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from workbench.config import (ARMS_MIN_STOCKS, ARMS_TRIN_SMOOTH, BT_HIGH, BT_LOOKBACK,
                    BT_LOW, BT_SPAN, MCC_FAST, MCC_SLOW, START_BREADTH)
from workbench.core import DATA, TODAY, TODAY_TAG, log, pct_rank, trade_dates

PDIR = DATA / "breadth_prices"
ADIR = DATA / "crowding_amounts"
OUT_ARMS = DATA / "mood_arms.csv"
OUT_ADV = DATA / "mood_breadth_advance.csv"

# 宽度综合分的成员：各维度 expanding 分位(0~100)等权平均
SCORE_SPEC = [
    ("mood_breadth.csv", "pct_above_ma200", "站上MA200占比"),
    ("mood_breadth.csv", "pct_above_ma60", "站上MA60占比"),
    ("mood_breadth_extra.csv", "nh_nl", "新高新低差"),
    ("mood_breadth_extra.csv", "adv_ratio", "上涨家数占比"),
    ("mood_breadth_extra.csv", "mom20_ratio", "20日动量广度"),
    ("mood_breadth_extra.csv", "ma_bull", "多头排列占比"),
]


def _scan_cross_section(cal_iso):
    """逐股对齐价格与成交额，按交易日桶做 bincount 向量化累加。"""
    dmap = {d: i for i, d in enumerate(cal_iso)}
    n = len(cal_iso)
    z = lambda: np.zeros(n, dtype="float64")
    n_adv, n_dec, a_adv, a_dec, v_adv, v_dec, total = z(), z(), z(), z(), z(), z(), z()

    pcodes = {f.stem for f in PDIR.glob("*.csv")}
    acodes = {f.stem for f in ADIR.glob("*.csv")}
    codes = sorted(pcodes & acodes)
    log(f"价格缓存 {len(pcodes)} 只 / 成交额缓存 {len(acodes)} 只，交集 {len(codes)} 只；日期桶 {n} 个")
    used, samples = 0, 0
    for code in codes:
        try:
            p = pd.read_csv(PDIR / f"{code}.csv", usecols=["date", "close"])
            a = pd.read_csv(ADIR / f"{code}.csv", usecols=["date", "amount"])
            d = p.merge(a, on="date", how="inner")
            if len(d) < 3:
                continue
            close = pd.to_numeric(d["close"], errors="coerce")
            amt = pd.to_numeric(d["amount"], errors="coerce")
            prev = close.shift(1)
            ok = (close.notna() & (close > 0) & prev.notna() & (prev > 0)
                  & amt.notna() & (amt > 0))
            d = d.loc[ok]
            if not len(d):
                continue
            close, prev, amt = close[ok], prev[ok], amt[ok]
            vol = amt / close                      # 近似成交量(股)：实测误差 <0.5%
            ix = d["date"].astype(str).map(dmap)
            m = ix.notna()
            if not m.any():
                continue
            c = ix[m].to_numpy(dtype="int64")
            up = (close[m] > prev[m]).to_numpy(dtype="float64")
            dn = (close[m] < prev[m]).to_numpy(dtype="float64")
            av = amt[m].to_numpy(dtype="float64")
            vv = vol[m].to_numpy(dtype="float64")
            w = lambda x: x
            n_adv += np.bincount(c, weights=w(up), minlength=n)
            n_dec += np.bincount(c, weights=w(dn), minlength=n)
            a_adv += np.bincount(c, weights=w(av * up), minlength=n)
            a_dec += np.bincount(c, weights=w(av * dn), minlength=n)
            v_adv += np.bincount(c, weights=w(vv * up), minlength=n)
            v_dec += np.bincount(c, weights=w(vv * dn), minlength=n)
            total += np.bincount(c, minlength=n)
            used += 1
            samples += int(m.sum())
        except Exception:
            continue
    log(f"有效股票 {used} 只，(股票,日)样本 {samples:,} 条")
    return pd.DataFrame(
        {"n_adv": n_adv, "n_dec": n_dec, "a_adv": a_adv, "a_dec": a_dec,
         "v_adv": v_adv, "v_dec": v_dec, "n_stocks": total},
        index=pd.to_datetime(cal_iso),
    )


def _compute_arms(df):
    d = df.copy()
    valid = ((d["n_stocks"] >= ARMS_MIN_STOCKS) & (d["n_adv"] > 0) & (d["n_dec"] > 0)
             & (d["a_adv"] > 0) & (d["a_dec"] > 0) & (d["v_adv"] > 0) & (d["v_dec"] > 0))
    d.loc[~valid, ["n_adv", "n_dec", "a_adv", "a_dec", "v_adv", "v_dec"]] = np.nan
    ad = d["n_adv"] / d["n_dec"]
    d["ad_ratio"] = ad
    d["adv_amt_ratio"] = d["a_adv"] / d["a_dec"]
    d["adv_vol_ratio"] = d["v_adv"] / d["v_dec"]
    d["trin"] = ad / d["adv_amt_ratio"]
    d["trin_vol"] = ad / d["adv_vol_ratio"]
    d["trin5"] = d["trin_vol"].rolling(5, min_periods=5).mean()
    d["trin10"] = d["trin_vol"].rolling(ARMS_TRIN_SMOOTH, min_periods=ARMS_TRIN_SMOOTH).mean()
    d["up_vol_share"] = d["v_adv"] / (d["v_adv"] + d["v_dec"]) * 100
    # 上涨家数占比：判断 TRIN 是否落在「极端单边日」失真区间的必备诊断列
    d["adv_share"] = d["n_adv"] / (d["n_adv"] + d["n_dec"]) * 100
    cols = ["trin", "trin_vol", "trin5", "trin10", "ad_ratio", "adv_amt_ratio",
            "adv_vol_ratio", "up_vol_share", "adv_share", "n_adv", "n_dec", "n_stocks"]
    out = d[cols].replace([np.inf, -np.inf], np.nan).dropna(subset=["trin", "trin_vol"])
    out = out[out.index <= TODAY].round(4)
    out.to_csv(OUT_ARMS, index_label="date")
    return out


def _breadth_score(index):
    """6 个宽度维度的 expanding 分位等权合成，0~100（越高=内部越健康）。"""
    parts = {}
    for f, col, _ in SCORE_SPEC:
        p = DATA / f
        if not p.exists():
            log(f"    [breadth_score] 缺少 {f}，该维度跳过")
            continue
        try:
            d = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
            d = d[~d.index.duplicated(keep="last")]
            s = pd.to_numeric(d[col], errors="coerce").dropna()
            s = s[s.index <= TODAY]
            parts[col] = pct_rank(s).reindex(index).ffill(limit=10)
        except Exception as e:
            log(f"    [breadth_score] {col} 失败: {e}")
    if len(parts) < 4:
        log("    [breadth_score] 有效维度不足 4 个，跳过")
        return None
    m = pd.DataFrame(parts)
    cnt = m.notna().sum(axis=1)
    # mean(axis=1) 不支持 min_count，手动要求至少 4 个有效维度才输出
    return m.sum(axis=1) / cnt.where(cnt >= 4)


def _compute_advance(df):
    # 交易日历含「今天」，但盘后个股数据尚未落盘时该日 n_stocks=0；
    # 若不剔除，ewm 会沿用前值而 cumsum/mcc_sum 又被多累加一次，产生幽灵日。
    d = df[df["n_stocks"] >= ARMS_MIN_STOCKS].copy()
    valid = ((d["n_adv"] + d["n_dec"]) > 0)
    d.loc[~valid, ["n_adv", "n_dec"]] = np.nan
    net_pct = (d["n_adv"] - d["n_dec"]) / d["n_stocks"] * 100
    fast = net_pct.ewm(span=MCC_FAST, adjust=False).mean()
    slow = net_pct.ewm(span=MCC_SLOW, adjust=False).mean()
    mcc_osc = fast - slow
    ratio = d["n_adv"] / (d["n_adv"] + d["n_dec"]) * 100
    bt = ratio.ewm(span=BT_SPAN, adjust=False).mean()
    low = bt.rolling(BT_LOOKBACK, min_periods=1).min()
    signal = ((low <= BT_LOW) & (bt >= BT_HIGH) & (bt.shift(1) < BT_HIGH)).astype(int)
    score = _breadth_score(d.index)
    out = pd.DataFrame({
        "breadth_score": score,
        "mcc_osc": mcc_osc,
        "mcc_sum": mcc_osc.cumsum(),
        "bt_ema": bt,
        "bt_signal": signal,
        "net_pct": net_pct,
        "n_adv": d["n_adv"], "n_dec": d["n_dec"],
    }, index=d.index)
    out = out[out.index <= TODAY].round(4)
    out.to_csv(OUT_ADV, index_label="date")
    return out


def build():
    t0 = time.time()
    log("=" * 60)
    log("阿姆氏指标与市场宽广度体系（纯本地派生）")
    log("=" * 60)
    cal = trade_dates(START_BREADTH, TODAY_TAG)
    iso = [f"{x[:4]}-{x[4:6]}-{x[6:]}" for x in cal]
    df = _scan_cross_section(iso)
    arms = _compute_arms(df)
    adv = _compute_advance(df)

    log(f"TRIN(成交额口径) {len(arms)} 日：{arms.index[0].date()} ~ {arms.index[-1].date()}")
    v = arms["trin_vol"]
    log(f"  经典成交量口径 中位={v.median():.3f} 1%={v.quantile(.01):.3f} "
        f"99%={v.quantile(.99):.3f} 当前={v.iloc[-1]:.3f}  TRIN10={arms['trin10'].iloc[-1]:.3f}")
    log(f"  成交额口径 中位={arms['trin'].median():.3f} 当前={arms['trin'].iloc[-1]:.3f}  "
        f"上涨/下跌量比={arms['adv_vol_ratio'].iloc[-1]:.3f}")
    bs = adv["breadth_score"].dropna()
    if len(bs):
        log(f"宽度综合分 {len(bs)} 日：{bs.index[0].date()} ~ {bs.index[-1].date()}，当前={bs.iloc[-1]:.2f}")
    log(f"McClellan OSC 当前={adv['mcc_osc'].iloc[-1]:.2f}  Summation={adv['mcc_sum'].iloc[-1]:.1f}  "
        f"宽度推力EMA10={adv['bt_ema'].iloc[-1]:.2f}%")
    hits = adv.index[adv["bt_signal"] == 1]
    log(f"Zweig 宽度推力历史触发 {len(hits)} 次" +
        (f"，最近 {hits[-1].date()}" if len(hits) else ""))
    log(f"输出：{OUT_ARMS.name} / {OUT_ADV.name}，用时 {time.time()-t0:.0f}s")
    return arms, adv


if __name__ == "__main__":
    build()
