# -*- coding: utf-8 -*-
"""
公募基金股票仓位（乐咕乐股，公开数据自算）
==========================================
数据源：akshare.fund_stock_position_lg() —— 一次返回全历史，属于「B 类」，
每次运行整抓即自动吸收最新一期，无需增量。周频（实测间隔中位数 7 天），
2017-12 起。position 为仓位测算值(%)，close 为同期基准指数点位。

输出：data/mood_fund_position.csv
  date, position, close, chg_4w, ma_12w

口径与局限：仓位为第三方用净值回归测算的估计值，不是基金季报披露的真实
持仓比例；不同机构测算结果可能有 1~3 个百分点差异。高仓位=机构已充分
加仓、增量资金有限（对应情绪偏热/顶部区域），低仓位=弹药充足（底部区域）。
"""
import pandas as pd

from workbench.core import DATA, TODAY, log

OUT = DATA / "mood_fund_position.csv"


def fetch():
    import akshare as ak
    d = ak.fund_stock_position_lg()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").set_index("date")
    pos = pd.to_numeric(d["position"], errors="coerce")
    close = pd.to_numeric(d["close"], errors="coerce")
    out = pd.DataFrame({
        "position": pos,
        "close": close,
        "chg_4w": pos.diff(4),
        "ma_12w": pos.rolling(12, min_periods=12).mean(),
    })
    return out[out.index <= TODAY].dropna(subset=["position"])


def main():
    try:
        df = fetch()
        if df.empty:
            raise ValueError("接口返回空")
    except Exception as e:
        log(f"[fund_position] 抓取失败({e})，沿用已有缓存")
        if OUT.exists():
            return
        raise
    df.to_csv(OUT, date_format="%Y-%m-%d")
    log(f"输出 {OUT.name}：{len(df)} 期，{df.index.min().date()} ~ {df.index.max().date()}")
    log(f"当前仓位 {df['position'].iloc[-1]:.2f}%（历史 min={df['position'].min():.2f} "
        f"max={df['position'].max():.2f}），近4周变化 {df['chg_4w'].iloc[-1]:+.2f}pp")


if __name__ == "__main__":
    main()
